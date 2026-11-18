"""The Sidekick Plus app: a terminal-style screen for the Sidekick. Run with: uv run app.py"""

import asyncio
import os
import shutil
import uuid
from datetime import datetime, timezone

import gradio as gr

import render
import styles
from sidekick import EVALUATOR_MODEL, MAX_ATTEMPTS, WORKER_MODEL, Sidekick
from sidekick_tools import ON_SPACE, SANDBOX, visitor_folder

# Limits for a public Space, where strangers spend your API credit. The request caps apply only there.
SESSION_REQUESTS = 8      # requests from one browser tab
ADDRESS_REQUESTS = 24     # requests a day from one network address (a reload gets a new tab, not a new address)
DAILY_REQUESTS = 150      # requests a day from everyone together: the one that really caps the bill
MAX_REQUEST_CHARS = 4000  # the request and its criteria together
RUNS_AT_ONCE = 2          # runs in flight, here and at home; more wait in the queue
QUEUE_SIZE = 200          # waiting events are cheap; this only stops a flood

SIDEKICK = None  # one Sidekick for the whole app: the tools, the graph and the database
_starting = None


async def get_sidekick() -> Sidekick:
    """Start the Sidekick the first time a browser connects, and share it after that."""
    global SIDEKICK, _starting
    if SIDEKICK is None:
        _starting = _starting or asyncio.Lock()
        async with _starting:
            if SIDEKICK is None:
                sidekick = Sidekick()
                await sidekick.setup()
                SIDEKICK = sidekick
    return SIDEKICK


# --- Sessions: what one browser tab is, and what belongs to it ------------------------------


def new_thread_id(session_id: str, taken: list) -> str:
    """A name for a new conversation. On a Space it starts with the visitor's random id, so it cannot be guessed."""
    stamp = f"{datetime.now():%m%d-%H%M%S}"
    thread_id = f"{session_id}.{stamp}" if ON_SPACE else stamp
    while thread_id in taken:
        thread_id += "x"
    return thread_id


def thread_label(thread_id: str) -> str:
    return thread_id.split(".", 1)[-1]  # leave the visitor's id out of what is shown


def new_session() -> dict:
    """What one browser tab remembers: who it is, its threads, its folder, the tools it has said
    "always" to, how many requests it has made, and the screen's events.
    On a Space each visitor gets a folder of their own; locally there is one sandbox."""
    session_id = uuid.uuid4().hex
    thread = new_thread_id(session_id, [])
    return {
        "id": session_id,
        "thread": thread,
        "threads": [thread],
        "sandbox": visitor_folder(session_id),
        "allow": set(),
        "requests": 0,
        "busy": False,  # a run is in flight: one at a time per tab, so two clicks cannot double the work
        "events": [],
    }


def visitor(session: dict) -> str | None:
    """The id the file tools use to find this visitor's folder. Not needed locally."""
    return session["id"] if ON_SPACE else None


def end_session(session: dict | None):
    """When a visitor leaves a Space, remove their folder. Never touches the shared sandbox itself."""
    folder = (session or {}).get("sandbox")
    if ON_SPACE and folder and os.path.dirname(os.path.realpath(folder)) == os.path.realpath(SANDBOX):
        shutil.rmtree(folder, ignore_errors=True)
    if ON_SPACE and session and SIDEKICK and SIDEKICK.loop:  # and their saved conversations
        asyncio.run_coroutine_threadsafe(SIDEKICK.forget(list(session["threads"])), SIDEKICK.loop)


def revive(session: dict | None) -> dict:
    """Gradio forgets a session about an hour after its connection drops. If that happened, start a
    new one and say so, rather than leave the visitor typing into a screen that no longer answers."""
    if session is not None:
        return session
    session = new_session()
    session["events"] = [{"type": "notice", "text": "Your earlier session ended, so this is a new one. Earlier threads and files are gone."}]
    return session


# --- Limits on a Space ---------------------------------------------------------------------

_usage = {"day": "", "total": 0, "addresses": {}}  # kept in memory: a restart of the Space sets it back to zero


_header_logged = False


def address_of(request: gr.Request | None) -> str:
    """The visitor's network address. A Space sits behind a proxy, which adds the address it saw to
    the end of a header. Earlier entries in that header can be typed in by the visitor, so only the
    last one is trusted. If the proxy turns out to add more than one entry, everyone shares a bucket
    and this cap becomes a second daily cap: the log line below shows which, once, after a deploy."""
    global _header_logged
    if request is None:
        return "unknown"
    entries = [entry.strip() for entry in request.headers.get("x-forwarded-for", "").split(",") if entry.strip()]
    if ON_SPACE and not _header_logged:
        _header_logged = True
        print(f"Address header: {len(entries)} entries on the first request", flush=True)
    return entries[-1] if entries else (request.client.host if request.client else "unknown")


def refusal(text: str, criteria: str, session: dict, request: gr.Request | None) -> str:
    """Decide whether a request may run. Returns the reason if not, and counts it if it may.

    There is no await in here, so two requests arriving together cannot both take the last place.
    """
    if not ON_SPACE:
        return ""
    if len(text) + len(criteria) > MAX_REQUEST_CHARS:
        return f"That is too long for this demo. Keep the request and criteria under {MAX_REQUEST_CHARS:,} characters together."
    today = f"{datetime.now(timezone.utc):%Y-%m-%d}"
    if _usage["day"] != today:
        _usage.update(day=today, total=0, addresses={})
    address = address_of(request)
    if session["requests"] >= SESSION_REQUESTS:
        return f"This demo allows {SESSION_REQUESTS} requests per visit, and you have used them. You can still read and download what is here."
    if _usage["addresses"].get(address, 0) >= ADDRESS_REQUESTS:
        return f"This demo allows {ADDRESS_REQUESTS} requests a day from one network, and that limit is reached. It resets at midnight UTC."
    if _usage["total"] >= DAILY_REQUESTS:
        return "This demo has reached its limit for today across all visitors. It resets at midnight UTC."
    session["requests"] += 1
    _usage["total"] += 1
    _usage["addresses"][address] = _usage["addresses"].get(address, 0) + 1
    return ""


SPACE_NOTE = (
    f"Public demo: {SESSION_REQUESTS} requests per visit. Files and threads are yours alone and are cleared when the demo restarts."
    if ON_SPACE else ""
)


# --- Drawing the screen --------------------------------------------------------------------


async def thread_choices(session: dict) -> list:
    """The thread picker's list. On a Space it holds only this visitor's own threads."""
    threads = await SIDEKICK.list_threads(session["threads"] if ON_SPACE else None) if SIDEKICK else []
    choices = [(f"{thread_label(thread_id)} · {render.clip(render.one_line(task), 44)}", thread_id) for thread_id, task in threads]
    if session["thread"] not in [thread_id for thread_id, _ in threads]:
        choices.insert(0, (f"{thread_label(session['thread'])} · new thread", session["thread"]))
    return choices


def screen(session: dict, state: str, clear: bool = False, threads: list | None = None):
    """Every part of the screen, drawn from the session. state is 'starting', 'ready', 'working' or 'waiting'."""
    events = session["events"]
    actions = render.pending_approval(events)
    empty = render.render_empty(
        len(SIDEKICK.tool_names) if SIDEKICK else 0, WORKER_MODEL, EVALUATOR_MODEL,
        SIDEKICK.notices if SIDEKICK else [], SPACE_NOTE,
    )
    placeholder = {
        "starting": "starting the tools, one moment",
        "ready": "what do you want done?",
        "working": "working",
        "waiting": "to say no, type what to do instead and press Enter",
    }[state]
    prompt = gr.update(interactive=state in ("ready", "waiting"), placeholder=placeholder)
    if clear:
        prompt["value"] = ""
    return (
        render.render_log(events, live=state == "working", sandbox=session["sandbox"], max_attempts=MAX_ATTEMPTS, empty=empty),
        render.render_plan(events),
        gr.update(visible=bool(actions)),
        render.render_approval(actions),
        gr.update(value=f"2 Yes, and don't ask again for {render.approval_names(actions)} this session") if actions else gr.update(),
        render.render_status(events, state, WORKER_MODEL, EVALUATOR_MODEL, MAX_ATTEMPTS),
        prompt,
        gr.update(value="") if clear else gr.update(),
        gr.update(choices=threads, value=session["thread"]) if threads is not None else gr.update(),
        session,
    )


def resting_state(session: dict) -> str:
    if SIDEKICK is None:
        return "starting"
    return "waiting" if render.pending_approval(session["events"]) else "ready"


async def drive(stream, session: dict):
    """Draw the screen again as each event arrives from a run. The caller has already marked the
    session busy; it is freed here however the run ends, including the visitor closing the page."""
    try:
        yield screen(session, "working", clear=True)
        async for event in stream:
            session["events"] = render.add_event(session["events"], event)
            yield screen(session, "working")
    finally:
        session["busy"] = False
    yield screen(session, resting_state(session), threads=await thread_choices(session))


def say(session: dict, text: str):
    """Put a notice in the log without calling a model, keeping any approval prompt in place."""
    events = session["events"]
    pause = events[-1:] if render.pending_approval(events) else []
    session["events"] = events[: len(events) - len(pause)] + [{"type": "notice", "text": text}] + pause
    return screen(session, resting_state(session))


# --- What the page can ask for -------------------------------------------------------------


async def start():
    session = new_session()
    yield screen(session, "starting")
    try:
        await get_sidekick()
    except Exception as error:
        session["events"] = [{"type": "error", "text": f"The tools did not start. {type(error).__name__}: {error}"}]
        yield screen(session, "starting")
        return
    yield screen(session, resting_state(session), threads=await thread_choices(session))


async def submit(text: str, criteria: str, session: dict | None, request: gr.Request):
    """Enter in the prompt: a new request, or, while a tool waits for approval, "No" with this note."""
    text, criteria = (text or "").strip(), (criteria or "").strip()
    if not text or SIDEKICK is None:
        return
    session = revive(session)
    if session["busy"]:
        return
    paused = bool(render.pending_approval(session["events"]))
    reason = refusal(text, "" if paused else criteria, session, request)
    if reason:
        if paused:
            reason += " Press 1 to let the waiting tool run, or start a new thread."
        yield say(session, reason)
        return
    session["busy"] = True  # no await between the check above and here, so two events cannot both pass
    if paused:
        stream = SIDEKICK.stream_answer(session["thread"], "no", text, session["allow"], visitor(session))
    else:
        session["events"] = render.add_event(session["events"], {"type": "request", "text": text, "criteria": criteria})
        stream = SIDEKICK.stream_turn(session["thread"], text, criteria, session["allow"], visitor(session))
    async for update in drive(stream, session):
        yield update


async def decide(choice: str, session: dict | None):
    """The Yes and "don't ask again" buttons. These are not counted as requests: they continue one
    that was, and the worker's own limit on model calls per attempt bounds how far it can go."""
    if SIDEKICK is None:
        return
    if session is None:
        session = revive(session)
        yield screen(session, resting_state(session), threads=await thread_choices(session))
        return
    if session["busy"] or not render.pending_approval(session["events"]):
        return
    session["busy"] = True
    stream = SIDEKICK.stream_answer(session["thread"], choice, "", session["allow"], visitor(session))
    async for update in drive(stream, session):
        yield update


async def approve_once(session: dict | None):
    async for update in decide("yes", session):
        yield update


async def approve_always(session: dict | None):
    async for update in decide("always", session):
        yield update


async def switch_thread(thread_id: str, session: dict | None):
    session = revive(session)
    if session["busy"]:  # the run in flight belongs to the current thread; let it finish first
        return tuple(gr.update() for _ in outputs)
    if SIDEKICK is None or not thread_id:
        return screen(session, resting_state(session))
    if ON_SPACE and thread_id not in session["threads"]:  # not this visitor's thread, whatever the browser sent
        return screen(session, resting_state(session), threads=await thread_choices(session))
    session["thread"] = thread_id
    session["events"] = await SIDEKICK.load_thread(thread_id)
    return screen(session, resting_state(session), threads=await thread_choices(session))


async def new_thread(session: dict | None):
    """Start a fresh conversation. The visitor, their folder, their "always" list and their count stay the same."""
    fresh = session is None
    session = revive(session)
    if session["busy"]:
        return tuple(gr.update() for _ in outputs)
    if not fresh:  # a session Gradio had dropped already starts on a new, empty thread, with a notice
        session["thread"] = new_thread_id(session["id"], session["threads"])
        session["threads"].append(session["thread"])
        session["events"] = []
    return screen(session, resting_state(session), clear=True, threads=await thread_choices(session))


TITLE = (
    f'<div class="title"><b>sidekick plus</b>'
    f"<span> · {render.model_label(WORKER_MODEL)}, checked by {EVALUATOR_MODEL}</span></div>"
)

with gr.Blocks(title="Sidekick Plus", fill_height=True) as ui:
    session = gr.State(None, delete_callback=end_session)

    with gr.Column(elem_id="term"):
        with gr.Row(elem_id="bar"):
            gr.HTML(TITLE, elem_id="title", padding=False, apply_default_css=False)
            threads = gr.Dropdown(
                choices=[], show_label=False, container=False, filterable=False, interactive=True,
                elem_id="threads", scale=0, min_width=120,
            )
            new_button = gr.Button("new", elem_id="new", elem_classes="plain", scale=0, min_width=0)
        log = gr.HTML(elem_id="log", padding=False, apply_default_css=False)
        plan = gr.HTML(elem_id="plan", padding=False, apply_default_css=False)
        with gr.Column(visible=False, elem_id="approval") as approval:
            approval_details = gr.HTML(padding=False, apply_default_css=False)
            yes_button = gr.Button("1 Yes", elem_id="yes", elem_classes="choice")
            always_button = gr.Button("2 Yes, and don't ask again this session", elem_id="always", elem_classes="choice")
            gr.HTML('<div class="hint">3 No, and tell the Sidekick what to do instead</div>', padding=False, apply_default_css=False)
        prompt = gr.Textbox(
            show_label=False, container=False, lines=1, max_lines=8, autofocus=True, interactive=False,
            placeholder="starting the tools, one moment", elem_id="prompt",
        )
        criteria = gr.Textbox(
            show_label=False, container=False, lines=1, max_lines=3, elem_id="criteria",
            placeholder="how you will judge the result (optional)",
        )
        status = gr.HTML(elem_id="status", padding=False, apply_default_css=False)

    outputs = [log, plan, approval, approval_details, always_button, status, prompt, criteria, threads, session]
    # "private" keeps these out of Gradio's API listing and client libraries. It does not stop a
    # hand-written web request, which is why the limits above are checked on the server.
    quiet = {"show_progress": "hidden", "api_visibility": "private"}
    # Everything that runs the Sidekick shares one allowance of runs in flight; the rest stays quick.
    run = {**quiet, "concurrency_limit": RUNS_AT_ONCE, "concurrency_id": "runs"}
    light = {**quiet, "concurrency_limit": None}

    ui.load(start, None, outputs, **light)
    prompt.submit(submit, [prompt, criteria, session], outputs, **run)
    criteria.submit(submit, [prompt, criteria, session], outputs, **run)
    yes_button.click(approve_once, [session], outputs, **run)
    always_button.click(approve_always, [session], outputs, **run)
    threads.input(switch_thread, [threads, session], outputs, **light)
    new_button.click(new_thread, [session], outputs, **light)

ui.queue(max_size=QUEUE_SIZE if ON_SPACE else None)


if __name__ == "__main__":
    if ON_SPACE:  # start clean: files left by earlier visitors do not carry over a restart
        shutil.rmtree(SANDBOX, ignore_errors=True)
        os.makedirs(SANDBOX, exist_ok=True)
    ui.launch(
        inbrowser=not ON_SPACE, ssr_mode=False,
        # Locally the sandbox is an ordinary folder, so Gradio must be told it may serve files from it.
        # On a Space the sandbox sits in Gradio's cache folder, which it already serves: as downloads,
        # or for .txt and .json as plain text. Never as a page the browser would run.
        allowed_paths=None if ON_SPACE else [SANDBOX],
        max_file_size="100kb" if ON_SPACE else None,  # the app takes no uploads; keep Gradio's upload route small
        theme=styles.THEME, css=styles.CSS, head=styles.HEAD,
    )
