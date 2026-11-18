"""Turn the Sidekick's events into the HTML of the terminal screen.

Everything here is a plain function from data to a string, so it can be tested with made-up
events and no model. Anything that came from a model, a tool or the user is escaped.
"""

import json
import os
from html import escape
from urllib.parse import quote

from markdown_it import MarkdownIt

ARG_CHARS = 72        # one argument value on a tool line
CALL_CHARS = 200      # the whole argument list on a tool line
PREVIEW_CHARS = 150   # the one-line preview of a tool result
RESULT_CHARS = 4000   # the unfolded tool result
FILE_TOOLS = {"write_file": "path", "edit_file": "path", "create_slide": "filename"}

_markdown = MarkdownIt("js-default")  # this preset leaves raw HTML switched off, so model text cannot inject any


def clip(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[: limit - 1] + "…"


def one_line(text: str) -> str:
    return " ".join(str(text).split())


def model_label(model: str) -> str:
    """'anthropic:claude-opus-5-5' -> 'opus-5-5'."""
    return model.split(":")[-1].removeprefix("claude-")


def markdown(text: str) -> str:
    html = _markdown.render(text)
    html = html.replace("<table>", '<div class="table"><table>').replace("</table>", "</table></div>")  # so a wide table scrolls
    return html.replace("<a href=", '<a target="_blank" rel="noopener noreferrer" href=')


# --- reading the event list ----------------------------------------------------------------


def current_turn(events: list) -> list:
    """The events from the latest request onwards."""
    for index in range(len(events) - 1, -1, -1):
        if events[index]["type"] == "request":
            return events[index:]
    return events


def pending_approval(events: list) -> list:
    """The tool calls waiting for the user, if the run is paused."""
    return events[-1]["actions"] if events and events[-1]["type"] == "approval" else []


def add_event(events: list, event: dict) -> list:
    """Append an event. A tool call is reported again when a paused run resumes, so skip repeats."""
    if event["type"] == "tool_call" and any(e["type"] == "tool_call" and e["id"] == event["id"] for e in events):
        return events
    if event["type"] == "note":  # the worker's remark before a tool call is reported again on resume, too
        for earlier in reversed(events):
            if earlier["type"] in ("tool_result", "answer", "verdict", "request"):
                break  # work has moved on since, so the same words are a new remark
            if earlier["type"] == "note" and earlier["text"] == event["text"]:
                return events
    if events and events[-1]["type"] == "approval":
        events = events[:-1]  # the pause is over once anything else happens
    return events + [event]


# --- the log -------------------------------------------------------------------------------


def format_args(args: dict) -> str:
    parts = []
    for key, value in (args or {}).items():
        if isinstance(value, str):
            shown = json.dumps(clip(one_line(value), ARG_CHARS), ensure_ascii=False)
        else:
            shown = clip(one_line(json.dumps(value, ensure_ascii=False, default=str)), ARG_CHARS)
        parts.append(f"{key}={shown}")
    return clip(", ".join(parts), CALL_CHARS)


def file_link(call: dict, sandbox: str) -> str:
    """A download link to the file a tool call wrote, if it is a file in the sandbox."""
    name = (call["args"] or {}).get(FILE_TOOLS.get(call["name"], ""), "")
    if not name and call["name"] == "create_slide":
        name = "slide.pptx"  # the tool's own default, for when the model leaves the name out
    if not name or not sandbox:
        return ""
    root = os.path.realpath(sandbox)
    name = str(name).replace("\\", "/")
    if name.lower().startswith(root.replace("\\", "/").lower() + "/"):  # the filesystem server may be given a full path
        name = name[len(root) + 1:]
    name = name.lstrip("/")
    if call["name"] == "create_slide" and not name.lower().endswith(".pptx"):
        name += ".pptx"
    path = os.path.realpath(os.path.join(root, name))
    try:
        inside = os.path.commonpath([root, path]) == root
    except ValueError:
        inside = False
    if not inside or not os.path.isfile(path):
        return ""
    url = "/gradio_api/file=" + quote(path.replace("\\", "/"), safe="/:")
    label = escape(name)
    return f' <a class="file" href="{url}" download="{escape(os.path.basename(name), quote=True)}" target="_blank" rel="noopener">open {label}</a>'


def render_tool(call: dict, result: dict | None, state: str, sandbox: str) -> str:
    """One tool call and what came back. state is 'done', 'running', 'waiting' or 'idle'."""
    declined = bool(result) and (result["text"] or "").startswith("The user declined this tool call.")
    kind = "declined" if declined else "failed" if result and result["failed"] else state if not result else "done"
    line = (
        f'<div class="tool {kind}"><span class="pip" aria-hidden="true">●</span>'
        f'<span class="call"><span class="name">{escape(call["name"])}</span>'
        f'<span class="args">({escape(format_args(call["args"]))})</span></span></div>'
    )
    if not result:
        note = {"waiting": "waiting for you", "running": "running"}.get(kind)
        return line + (f'<div class="res {kind}"><span class="elbow">└</span><span>{note}</span></div>' if note else "")

    text = result["text"] or "(no output)"
    if declined:
        text = "declined: " + text.partition("The user says: ")[2]
    preview = escape(clip(one_line(text), PREVIEW_CHARS))
    link = "" if result["failed"] or declined else file_link(call, sandbox)
    css = "res declined" if declined else "res failed" if result["failed"] else "res"
    if len(one_line(text)) <= PREVIEW_CHARS:
        return line + f'<div class="{css}"><span class="elbow">└</span><span>{preview}{link}</span></div>'
    more = len(text) - RESULT_CHARS
    full = escape(text[:RESULT_CHARS]) + (f"\n… {more:,} more characters" if more > 0 else "")
    return line + (
        f'<details class="{css}"><summary><span class="elbow">└</span><span class="preview">{preview}</span>'
        f'<span class="size">{len(text):,} chars</span>{link}</summary><pre>{full}</pre></details>'
    )


def render_verdict(event: dict, max_attempts: int) -> str:
    action, score = event["next_action"], event.get("score")
    marks = f" · {score}/10" if score is not None else ""
    if action == "accept":
        kind, head = "accept", f"✓ accept{marks}"
    elif action == "ask_user":
        kind, head = "ask", f"? needs you{marks}"
    elif event.get("attempt") is None:
        kind, head = "retry", "✗ sent back"
    elif event.get("final"):
        kind, head = "retry", f"✗ not met after {event['attempt']} of {max_attempts} attempts{marks}"
    else:
        kind, head = "retry", f"✗ retry · attempt {event['attempt']} of {max_attempts}{marks}"
    missing = "; ".join(event.get("missing") or [])
    return (
        f'<div class="verdict {kind}"><span class="head">{head}</span>'
        f'<span class="why">{escape(event.get("feedback") or "")}</span>'
        + (f'<span class="why missing">missing: {escape(missing)}</span>' if missing else "")
        + "</div>"
    )


def render_empty(tool_count: int, worker: str, evaluator: str, notices: list, limits: str = "") -> str:
    notes = "".join(f'<div class="line error">{escape(n)}</div>' for n in notices)
    if limits:
        notes = f'<div class="line dim">{escape(limits)}</div>' + notes
    ready = f"{tool_count} tools ready" if tool_count else "starting the tools"
    return f"""<div class="log empty">
<div class="line">Give the Sidekick a task. It plans, uses its tools, and a second model checks the result.</div>
<div class="line"><span class="mark">&gt;</span> what you want done</div>
<div class="line dim">&nbsp;&nbsp;criteria: how you will judge it (optional)</div>
<div class="line dim">It asks before it writes a file, runs code, drives the browser or sends anything.</div>
<div class="line dim">{ready} · worker {escape(model_label(worker))} · evaluator {escape(evaluator)}</div>
{notes}</div>"""


def render_log(events: list, live: bool = False, sandbox: str = "", max_attempts: int = 3, empty: str = "") -> str:
    """The whole transcript. live is True while a run is in flight."""
    if not events:
        return empty or '<div class="log"></div>'
    results = {e["id"]: e for e in events if e["type"] == "tool_result"}
    waiting = {(a["name"], json.dumps(a["args"], sort_keys=True, default=str)) for a in pending_approval(events)}
    out = []
    for event in events:
        kind = event["type"]
        if kind == "request":
            if out:
                out.append('<div class="gap"></div>')
            out.append(f'<div class="req"><span class="mark">&gt;</span><span>{escape(event["text"])}</span></div>')
            if event.get("criteria"):
                out.append(f'<div class="crit">criteria: {escape(event["criteria"])}</div>')
        elif kind == "note":
            out.append(f'<div class="note">{escape(event["text"])}</div>')
        elif kind == "tool_call":
            result = results.get(event["id"])
            key = (event["name"], json.dumps(event["args"], sort_keys=True, default=str))
            state = "waiting" if key in waiting else "running" if live else "idle"
            out.append(render_tool(event, result, state, sandbox))
        elif kind == "answer":
            out.append(f'<div class="answer">{markdown(event["text"])}</div>')
        elif kind == "verdict":
            out.append(render_verdict(event, max_attempts))
        elif kind == "notice":
            out.append(f'<div class="line notice">{escape(event["text"])}</div>')
        elif kind == "error":
            out.append(
                f'<div class="line error">✗ the run stopped: {escape(clip(event["text"], 600))}</div>'
                '<div class="line dim">&nbsp;&nbsp;Nothing was lost. Send the request again, or start a new thread.</div>'
            )
    if live and not pending_approval(events) and events[-1]["type"] not in ("tool_call",):
        out.append('<div class="line working"><span class="pip" aria-hidden="true">●</span> working</div>')
    return f'<div class="log">{"".join(out)}</div>'


# --- the plan, the approval prompt, the status line ----------------------------------------


def render_plan(events: list) -> str:
    """The worker's to-do list for the current turn, as one wrapping line."""
    todos = next((e["items"] for e in reversed(current_turn(events)) if e["type"] == "todos"), [])
    if not todos:
        return ""
    marks = {"completed": "☑", "in_progress": "◐", "pending": "☐"}
    items = "".join(
        f'<span class="todo {escape(str(t.get("status", "pending")))}">'
        f'<span class="box" aria-hidden="true">{marks.get(t.get("status"), "☐")}</span>'
        f'{escape(clip(one_line(t.get("content", "")), 90))}</span>'
        for t in todos
    )
    done = sum(t.get("status") == "completed" for t in todos)
    return f'<div class="plan" aria-label="Plan, {done} of {len(todos)} done">{items}</div>'


def approval_names(actions: list) -> str:
    return ", ".join(dict.fromkeys(a["name"] for a in actions))


def render_approval(actions: list) -> str:
    if not actions:
        return ""
    blocks = []
    for action in actions:
        rows = "".join(
            f'<div class="arg"><span class="key">{escape(str(key))}</span>'
            f'<span class="value">{escape(clip(value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, default=str), 1200))}</span></div>'
            for key, value in (action["args"] or {}).items()
        )
        blocks.append(f'<div class="action"><div class="name">{escape(action["name"])}</div>{rows}</div>')
    several = len(actions) > 1
    question = f"The Sidekick wants to run {'these ' + str(len(actions)) + ' tools' if several else 'this tool'}. Do you want to proceed?"
    return f'<div class="approval">{"".join(blocks)}<div class="question">{question}</div></div>'


def render_status(events: list, state: str, worker: str, evaluator: str, max_attempts: int) -> str:
    """The bottom line. state is 'starting', 'ready', 'working' or 'waiting'."""
    turn = current_turn(events)
    calls = sum(e["type"] == "model_call" for e in turn)
    verdicts = sum(e["type"] == "verdict" for e in turn)
    label = {"starting": "starting the tools", "ready": "ready", "working": "working", "waiting": "waiting for you"}[state]
    parts = [f'<span class="state {state}"><span class="pip" aria-hidden="true">●</span> {label}</span>']
    if calls:
        attempt = min(verdicts + (state != "ready"), max_attempts) or 1
        parts.append(f"attempt {attempt} of {max_attempts}")
        parts.append(f"{calls} model call{'s' if calls != 1 else ''}")
    parts.append(f"worker {escape(model_label(worker))}")
    parts.append(f"evaluator {escape(evaluator)}")
    return f'<div class="status" role="status">{"<span class=sep> · </span>".join(parts)}</div>'
