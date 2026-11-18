"""Sidekick Plus: a create_agent worker and an evaluator from another model family, as a LangGraph graph.

    START -> worker -> evaluator -> route --retry--> feedback -> worker
                                          \\--accept / ask_user / out of attempts--> END

The worker is one create_agent with MCP tools and middleware. The evaluator is a single model
call with structured output. The graph's SQLite checkpointer keeps every conversation as a
thread, and holds a run in place while it waits for the user to approve a tool call.

This is the notebook sidekick_plus.ipynb as a module. The app talks to it through the Sidekick
class, which turns a run into a stream of small event dicts for the screen to draw.
"""

import asyncio
import operator
import os
import time
from datetime import datetime
from typing import Annotated, Literal

import aiosqlite
from dotenv import load_dotenv
from pydantic import BaseModel, Field
from typing_extensions import TypedDict

from deepagents.backends import FilesystemBackend
from deepagents.middleware.skills import SkillsMiddleware
from langchain.agents import create_agent
from langchain.agents.middleware import (
    AgentMiddleware,
    HumanInTheLoopMiddleware,
    ModelCallLimitMiddleware,
    ModelFallbackMiddleware,
    PIIMiddleware,
    TodoListMiddleware,
    ToolCallLimitMiddleware,
    wrap_tool_call,
)
from langchain_core.messages import HumanMessage, ToolMessage
from langchain_openai import ChatOpenAI
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
from langgraph.graph import END, START, StateGraph
from langgraph.graph.message import add_messages
from langgraph.types import Command

from sidekick_tools import DATA, ON_SPACE, SKILLS, get_all_tools

load_dotenv(override=True)

# The default worker is the strongest model at home and the cheapest on a public Space
WORKER_MODEL = os.getenv("SIDEKICK_WORKER_MODEL", "anthropic:claude-haiku-5-5" if ON_SPACE else "anthropic:claude-opus-5-5")
EVALUATOR_MODEL = os.getenv("SIDEKICK_EVALUATOR_MODEL", "gpt-5.5")
MAX_ATTEMPTS = 2 if ON_SPACE else 3
DATABASE = os.path.join(DATA, "sidekick_memory.db")
DEFAULT_CRITERIA = "The answer should be clear, correct and complete"
CRITERIA_MARK = "\n\nThe success criteria for this task are: "


# --- State ---------------------------------------------------------------------------------


def add_or_reset(current: int, update: int | None) -> int:
    """Our own reducer: add to the count, or go back to zero when the update is None."""
    return 0 if update is None else current + update


class State(TypedDict):
    messages: Annotated[list, add_messages]
    task: str
    success_criteria: str
    todos: list
    verdict: dict
    attempts: Annotated[int, add_or_reset]
    feedback_history: Annotated[list[str], operator.add]


# --- Evaluator -----------------------------------------------------------------------------


class EvaluatorOutput(BaseModel):
    feedback: str = Field(description="Brief, concrete feedback on the assistant's response")
    score: int = Field(description="How well the success criteria are met, from 1 (not at all) to 10 (fully)")
    missing: list[str] = Field(description="Each success criterion that is not yet met; empty if all are met")
    next_action: Literal["accept", "retry", "ask_user"] = Field(
        description="accept if the criteria are met; retry if the assistant should keep working; "
        "ask_user if the assistant asked a question, needs clarification, or is stuck"
    )


evaluator_llm = ChatOpenAI(model=EVALUATOR_MODEL).with_structured_output(EvaluatorOutput)

EVIDENCE_CHARS = 30_000  # how much tool output the evaluator may read in one turn


def tools_used_this_turn(messages: list) -> str:
    """The tool calls made since the user's request, each with its arguments and the start of its result.

    This is the evaluator's evidence: it shows which page was opened and what came back, not just a tool name.
    """
    results = {message.tool_call_id: message.text for message in messages if message.type == "tool"}
    calls = []
    for message in reversed(messages):
        if isinstance(message, HumanMessage) and message.name == "user":
            break
        calls = list(getattr(message, "tool_calls", None) or []) + calls
    share = max(500, min(4000, EVIDENCE_CHARS // max(len(calls), 1)))  # fewer calls, longer excerpts
    lines = []
    for call in calls:
        lines.append(f"- {call['name']}({str(call['args'])[:300]})")
        result = results.get(call["id"])
        if result:
            lines.append(f"  returned: {' '.join(result.split())[:share]}")
    return "\n".join(lines)


def conversation_so_far(messages: list) -> str:
    """The recent conversation as plain text, so the evaluator can judge a reply in context."""
    lines = []
    for message in messages:
        if message.type in ("human", "ai") and message.text:
            speaker = "Assistant" if message.type == "ai" else "Evaluator" if message.name == "evaluator" else "User"
            lines.append(f"{speaker}: {message.text[:600]}")
    if len(lines) > 30:  # keep the opening and the latest part of a long conversation
        lines = lines[:4] + [f"[{len(lines) - 30} messages left out here]"] + lines[-26:]
    return "\n".join(lines)


async def evaluator_node(state: State) -> dict:
    prompt = f"""You decide whether an assistant has met the success criteria for a task.

The conversation so far, oldest first. A long conversation is shortened here, but the assistant sees all of it,
so do not mark a reply wrong only because it refers to something missing from this copy:
{conversation_so_far(state["messages"][:-1])}

The user's latest request was:
{state["task"]}

The success criteria are:
{state["success_criteria"]}

The tool calls the assistant made while working, in order, with their arguments and the start of each result.
Results are cut short here, so a detail missing from an excerpt was not necessarily missing from the page:
{tools_used_this_turn(state["messages"]) or "none"}

The assistant's most recent reply was:
{state["messages"][-1].text}

Decide whether the success criteria are met, using the tool calls as evidence of what was actually done.
If the reply adds supporting detail because earlier evaluator feedback asked for proof, judge any length
request against the answer itself, not against that supporting detail.
Give brief, concrete feedback, a score, the criteria still missing, and the next action."""
    verdict = await evaluator_llm.ainvoke(prompt)
    marked = {**verdict.model_dump(), "attempt": state["attempts"] + 1}  # which attempt this verdict is for
    return {"verdict": marked, "attempts": 1, "feedback_history": [verdict.feedback]}


# --- Worker middleware ---------------------------------------------------------------------


class TolerateToolErrors(AgentMiddleware):
    """Hand tool failures back to the model as a message so it can recover, rather than
    crashing the run. Tools that touch the outside world, like a browser, fail now and then."""

    async def awrap_tool_call(self, request, handler):
        try:
            return await handler(request)
        except Exception as error:
            return ToolMessage(
                content=f"That tool call failed: {error}. Try another approach.",
                tool_call_id=request.tool_call["id"],
                name=request.tool_call["name"],
                status="error",  # so the screen shows it as a failure, with no download link
            )


BLOCKED_SITES = ["facebook.com", "instagram.com", "tiktok.com"]


@wrap_tool_call
async def block_list(request, handler):
    """Refuse any tool call whose arguments point at a blocked site."""
    arguments = str(request.tool_call["args"]).lower()
    for site in BLOCKED_SITES:
        if site in arguments:
            return ToolMessage(
                content=f"Blocked: {site} is on the block list. Do not try another route to it.",
                tool_call_id=request.tool_call["id"],
                name=request.tool_call["name"],
                status="error",
            )
    return await handler(request)


MAX_RESULT_CHARS = 6_000 if ON_SPACE else 20_000  # every result is sent again on each later model call


@wrap_tool_call
async def clip_long_results(request, handler):
    """Cut a very long tool result short, so one big page cannot fill the worker's context."""
    result = await handler(request)
    if isinstance(result, ToolMessage):
        note = " [The rest of this result was cut because it was too long]"
        if isinstance(result.content, str) and len(result.content) > MAX_RESULT_CHARS:
            result.content = result.content[:MAX_RESULT_CHARS] + note
        elif isinstance(result.content, list):
            for block in result.content:
                if isinstance(block, dict) and len(block.get("text", "")) > MAX_RESULT_CHARS:
                    block["text"] = block["text"][:MAX_RESULT_CHARS] + note
    return result


NEEDS_APPROVAL = {
    "write_file", "edit_file", "create_directory", "move_file", "create_slide",
    "send_push_notification", "send_email", "run_python", "request_human_help",
    "browser_navigate", "browser_click", "browser_type", "browser_fill_form", "browser_press_key",
    "browser_select_option", "browser_handle_dialog",
    "delete_entities", "delete_observations", "delete_relations",
}

WORKER_PROMPT = """You are Sidekick, a capable personal assistant who completes tasks for the user.
You have web search, Wikipedia, a page reader, a sandbox filesystem, and, when running on the user's own machine,
a real web browser, a memory of facts about the user, Python, email and push notifications.
Prefer search and the page reader for finding things out; use the browser only when a page needs interaction.
Some of your tools pause for the user's approval. If the user rejects one, read their note and change course.
Save facts about the user that will matter in future conversations to your memory, and check it when relevant.
Keep working until the success criteria are met, or until you genuinely need to ask the user a question.
If you have a question, ask it plainly. When you are finished, give your final answer clearly,
saying what you did, what you produced, and what you found."""


# Limits on the worker. A public Space gets tighter ones, and no fallback to a dearer model.
RUN_CALLS = 12 if ON_SPACE else 30
ATTEMPT_CALLS = 15 if ON_SPACE else 90
RUN_SECONDS = 240 if ON_SPACE else None  # checked between steps; it cannot interrupt a single slow step
# On a Space, save a run's state once, when it finishes or pauses, instead of after every small
# step. Saving every step multiplies the database's size many times over for no benefit there.
STREAM_OPTIONS = {"durability": "exit"} if ON_SPACE else {}
if ON_SPACE:
    SPACE_ONLY_LIMITS = [ToolCallLimitMiddleware(run_limit=25)]
else:
    SPACE_ONLY_LIMITS = [ModelFallbackMiddleware("anthropic:claude-sonnet-5-5")]


# --- Graph ---------------------------------------------------------------------------------


def feedback_node(state: State) -> dict:
    note = (
        "Your last response did not meet the success criteria. "
        f"Here is the feedback: {state['verdict']['feedback']} Please keep working and address it."
    )
    return {"messages": [HumanMessage(content=note, name="evaluator")]}


def route(state: State) -> str:
    """Retry while the evaluator asks for it and there are attempts left; otherwise finish."""
    if state["verdict"]["next_action"] == "retry" and state["attempts"] < MAX_ATTEMPTS:
        return "feedback"
    return END


def build_graph(tools: list, checkpointer):
    gated = {t.name: {"allowed_decisions": ["approve", "reject"]} for t in tools if t.name in NEEDS_APPROVAL}
    worker = create_agent(
        model=WORKER_MODEL,
        tools=tools,
        system_prompt=f"{WORKER_PROMPT}\nToday is {datetime.now():%A %d %B %Y}.",
        middleware=[
            TolerateToolErrors(),
            block_list,
            clip_long_results,
            SkillsMiddleware(backend=FilesystemBackend(root_dir=SKILLS, virtual_mode=True), sources=["/"]),
            TodoListMiddleware(),
            PIIMiddleware("email"),
            PIIMiddleware("credit_card", apply_to_tool_results=True),
            # run_limit counts one stretch of work; an approval pause starts a new stretch, so on
            # its own it would let "don't ask again" run forever. thread_limit counts the whole
            # attempt, pauses included, and is what actually bounds the cost of a request.
            ModelCallLimitMiddleware(run_limit=RUN_CALLS, thread_limit=ATTEMPT_CALLS),
            *SPACE_ONLY_LIMITS,
            HumanInTheLoopMiddleware(interrupt_on=gated),
        ],
    )
    builder = StateGraph(State)
    builder.add_node("worker", worker)  # no checkpointer of its own: the outer graph's covers it
    builder.add_node("evaluator", evaluator_node)
    builder.add_node("feedback", feedback_node)
    builder.add_edge(START, "worker")
    builder.add_edge("worker", "evaluator")
    builder.add_conditional_edges("evaluator", route, {"feedback": "feedback", END: END})
    builder.add_edge("feedback", "worker")
    return builder.compile(checkpointer=checkpointer)


# --- Events for the screen -----------------------------------------------------------------
#
# A run is shown as a list of small dicts, each with a "type":
#   request      text, criteria          what the user asked
#   note         text                    something the worker said while still working
#   tool_call    id, name, args          the worker called a tool
#   tool_result  id, name, text, failed  what came back
#   todos        items                   the worker's plan changed
#   answer       text                    the worker's reply for this attempt
#   verdict      next_action, score, feedback, missing, attempt, final
#   approval     actions                 the run is paused, waiting for the user
#   error        text                    the run stopped with an error
#   model_call                           one call to the worker model, for the counter


def message_events(message) -> list[dict]:
    """The events one saved message stands for."""
    if message.type == "ai":
        calls = message.tool_calls or []
        events = [{"type": "note" if calls else "answer", "text": message.text}] if message.text.strip() else []
        return events + [{"type": "tool_call", "id": c["id"], "name": c["name"], "args": c["args"]} for c in calls]
    if message.type == "tool":
        return [{
            "type": "tool_result", "id": message.tool_call_id, "name": message.name or "",
            "text": message.text, "failed": getattr(message, "status", "") == "error",
        }]
    if message.type == "human" and message.name == "user":
        text, _, criteria = message.text.partition(CRITERIA_MARK)
        return [{"type": "request", "text": text, "criteria": "" if criteria == DEFAULT_CRITERIA else criteria}]
    if message.type == "human" and message.name == "evaluator":  # an earlier attempt was sent back
        feedback = message.text.partition("Here is the feedback: ")[2].removesuffix(" Please keep working and address it.")
        return [{"type": "verdict", "next_action": "retry", "score": None, "feedback": feedback, "missing": [],
                 "attempt": None, "final": False}]
    return []


def verdict_event(verdict: dict, attempt: int | None = None) -> dict:
    attempt = verdict.get("attempt") or attempt or 1
    final = verdict["next_action"] != "retry" or attempt >= MAX_ATTEMPTS
    return {"type": "verdict", **verdict, "attempt": attempt, "final": final}


class Sidekick:
    """One running Sidekick: the tools, the graph and its database. Shared by every conversation."""

    def __init__(self):
        self.graph = None
        self.sessions = None
        self.connection = None
        self.memory = None
        self.tool_names = []
        self.notices = []
        self.loop = None

    async def setup(self):
        tools, self.sessions, self.notices = await get_all_tools()
        self.tool_names = [t.name for t in tools]
        self.loop = asyncio.get_running_loop()
        self.connection = await aiosqlite.connect(DATABASE)
        self.memory = AsyncSqliteSaver(self.connection)
        self.graph = build_graph(tools, self.memory)

    async def cleanup(self):
        if self.sessions:
            self.sessions.stop()
        if self.connection:
            await self.connection.close()

    # --- running ---

    @staticmethod
    def config(thread_id: str, visitor: str | None = None) -> dict:
        """What a run is told about itself: its thread and, on a Space, which visitor it belongs to.
        The file tools turn the visitor's id into that visitor's own folder."""
        configurable = {"thread_id": thread_id}
        if visitor:
            configurable["visitor"] = visitor
        return {"configurable": configurable}

    async def stream_turn(self, thread_id: str, message: str, success_criteria: str, allow: set, visitor: str | None = None):
        """Give the Sidekick a new request on a thread, and yield events as it works."""
        criteria = success_criteria.strip() or DEFAULT_CRITERIA
        payload = {
            "messages": [HumanMessage(content=f"{message}{CRITERIA_MARK}{criteria}", name="user")],
            "task": message,
            "success_criteria": criteria,
            "attempts": None,  # our reducer reads None as "back to zero"
        }
        async for event in self._stream(payload, thread_id, allow, visitor):
            yield event

    async def stream_answer(self, thread_id: str, choice: str, note: str, allow: set, visitor: str | None = None):
        """Answer an approval request with 'yes', 'always' or 'no' (plus a note), and carry on."""
        actions = await self.pending_actions(thread_id)
        if not actions:
            return
        if choice == "always":
            allow.update(action["name"] for action in actions)
        if choice == "no":
            decision = {"type": "reject", "message": f"The user declined this tool call. The user says: {note or 'do not do this'}"}
        else:
            decision = {"type": "approve"}
        payload = Command(resume={"decisions": [decision] * len(actions)})
        async for event in self._stream(payload, thread_id, allow, visitor):
            yield event

    async def _stream(self, payload, thread_id: str, allow: set, visitor: str | None = None):
        config = self.config(thread_id, visitor)
        started = time.monotonic()
        try:
            while True:
                # subgraphs=True also reports the steps inside the worker, each tagged with where it came from
                async for namespace, update in self.graph.astream(
                    payload, config, stream_mode="updates", subgraphs=True, **STREAM_OPTIONS
                ):
                    if RUN_SECONDS and time.monotonic() - started > RUN_SECONDS:
                        yield {"type": "error", "text": f"This request took longer than {RUN_SECONDS // 60} minutes and was stopped."}
                        return
                    for node, change in update.items():
                        if not isinstance(change, dict):
                            continue  # an interrupt marker; the pause is read from the saved state below
                        if namespace:  # a step inside the worker
                            if node == "model":
                                yield {"type": "model_call"}
                            for message in change.get("messages") or []:
                                for event in message_events(message):
                                    yield event
                            if change.get("todos") is not None:
                                yield {"type": "todos", "items": change["todos"]}
                        elif node == "evaluator":
                            yield verdict_event(change["verdict"])
                actions = await self.pending_actions(thread_id)
                if actions and all(action["name"] in allow for action in actions):
                    payload = Command(resume={"decisions": [{"type": "approve"}] * len(actions)})
                    continue
                if actions:
                    yield {"type": "approval", "actions": actions}
                return
        except Exception as error:
            if ON_SPACE:  # the details can hold key fragments or paths: they go to the Space's log, not the visitor
                print(f"Run failed on thread {thread_id[-11:]}: {type(error).__name__}: {error}", flush=True)
                yield {"type": "error", "text": f"Something went wrong while working on this ({type(error).__name__})."}
            else:
                yield {"type": "error", "text": f"{type(error).__name__}: {error}"}

    async def forget(self, thread_ids: list[str]):
        """Delete saved conversations, for when a visitor to a Space has left."""
        for thread_id in thread_ids:
            await self.memory.adelete_thread(thread_id)

    async def pending_actions(self, thread_id: str) -> list:
        """The tool calls waiting for approval on a thread, or an empty list."""
        state = await self.graph.aget_state({"configurable": {"thread_id": thread_id}})
        return state.interrupts[0].value["action_requests"] if state.interrupts else []

    # --- threads ---

    async def list_threads(self, only: list[str] | None = None) -> list[tuple[str, str]]:
        """Saved conversations, newest first, as (thread_id, its latest request).

        With only, look up just those threads: on a Space a visitor is shown their own and no one else's.
        """
        if only is not None:
            thread_ids = list(reversed(only))
        else:
            thread_ids = []
            async for checkpoint in self.memory.alist(None):
                thread_id = checkpoint.config["configurable"]["thread_id"]
                if thread_id not in thread_ids:
                    thread_ids.append(thread_id)
        threads = []
        for thread_id in thread_ids:
            state = await self.graph.aget_state({"configurable": {"thread_id": thread_id}})
            if state.values:  # a thread with nothing saved yet is not listed
                threads.append((thread_id, state.values.get("task", "")))
        return threads

    async def load_thread(self, thread_id: str) -> list[dict]:
        """Rebuild the events of a saved conversation from its messages."""
        state = await self.graph.aget_state({"configurable": {"thread_id": thread_id}})
        values = state.values
        events = [event for message in values.get("messages", []) for event in message_events(message)]
        if values.get("todos"):
            events.append({"type": "todos", "items": values["todos"]})
        if state.interrupts:
            events.append({"type": "approval", "actions": state.interrupts[0].value["action_requests"]})
        elif values.get("verdict") and events and events[-1]["type"] == "answer":
            events.append(verdict_event(values["verdict"], values.get("attempts", 0)))
        return events
