"""Tools for Sidekick Plus: our own tools, ready-made LangChain tools, and MCP servers."""

import asyncio
import importlib.util
import os
import subprocess
import re
import sys
import tempfile
import threading
import unicodedata
from contextlib import AsyncExitStack

import requests
import wikipedia
from dotenv import load_dotenv
from langchain_community.tools import GoogleSerperRun, WikipediaQueryRun
from langchain_community.utilities import GoogleSerperAPIWrapper, WikipediaAPIWrapper
from langchain_core.runnables import RunnableConfig
from langchain_core.tools import tool
from langchain_mcp_adapters.client import MultiServerMCPClient
from langchain_mcp_adapters.tools import load_mcp_tools
from pypdf import PdfReader

from slide_kit import build_slide

load_dotenv(override=True)

if sys.platform == "win32":
    # On Windows the stdio MCP servers cannot write their logs to a notebook or app console
    from functools import partial
    import langchain_mcp_adapters.sessions as mcp_sessions

    mcp_sessions.stdio_client = partial(mcp_sessions.stdio_client, errlog=subprocess.DEVNULL)

HERE = os.path.dirname(os.path.abspath(__file__))
SKILLS = os.path.join(HERE, "skills")

ON_SPACE = bool(os.getenv("SPACE_ID"))  # Hugging Face sets SPACE_ID inside a Space

if ON_SPACE:
    # Two separate places on a Space, on purpose.
    # The sandbox sits inside Gradio's own cache folder: Gradio serves files from there as
    # downloads, never as a page the browser would open and run.
    # Everything private (the database of conversations) goes in a temporary folder that Gradio
    # does not serve at all. Neither place is the app folder, so nothing uploaded with the code
    # (an old sandbox or database) can end up being used by mistake.
    SANDBOX = os.path.join(os.path.abspath(os.environ.get("GRADIO_EXAMPLES_CACHE", ".gradio/cached_examples")), "sidekick-sandbox")
    DATA = tempfile.mkdtemp(prefix="sidekick-private-")
else:
    DATA = HERE
    SANDBOX = os.path.join(HERE, "sandbox")
os.makedirs(SANDBOX, exist_ok=True)

FILE_LOCK = threading.Lock()  # tools can run side by side; this keeps "is there room?" and "write" together

WRITABLE_TYPES = {".md", ".txt", ".csv", ".json"}  # plain text only: nothing a browser would run as a page
MAX_FILE_BYTES = 200_000
MAX_FILES = 20
MAX_NAME_CHARS = 100
MAX_READ_CHARS = 15_000  # below the worker's own limit on a tool result, so our "cut" note survives
BIDI_CONTROLS = set("\u061c\u200e\u200f\u202a\u202b\u202c\u202d\u202e\u2066\u2067\u2068\u2069")  # these can disguise a name

# Wikimedia rejects the wikipedia library's default user agent, so identify ourselves properly
wikipedia.set_user_agent("agentic-track-course (https://edwarddonner.com)")

search = GoogleSerperRun(api_wrapper=GoogleSerperAPIWrapper())

wikipedia_lookup = WikipediaQueryRun(api_wrapper=WikipediaAPIWrapper())


def visitor_folder(visitor: str | None) -> str:
    """The sandbox folder for one visitor. Locally there are no visitors, just the one sandbox.

    On a Space every browser session has a random id and a folder named after it. A run with no
    id is refused there, rather than quietly sharing one folder between strangers.
    """
    if not ON_SPACE:
        return SANDBOX
    if not visitor or not re.fullmatch(r"[0-9a-f]{32}", visitor):
        raise ValueError("This run has no sandbox of its own")
    return os.path.join(SANDBOX, visitor)


def sandbox_root(config: RunnableConfig | None) -> str:
    """The folder this run may use, read from the run's config, which the app sets and the model cannot."""
    root = visitor_folder(((config or {}).get("configurable") or {}).get("visitor"))
    os.makedirs(root, exist_ok=True)
    return root


def in_sandbox(path: str, root: str | None = None) -> str:
    """Turn a path the model gives us into a real path, and refuse anything outside the sandbox.

    The check compares real folders, not text, so neither ".." nor a link nor a sibling folder
    whose name merely starts with the sandbox's name can get past it.
    """
    root = os.path.realpath(root or SANDBOX)
    path = str(path).replace("\\", "/")
    if any(unicodedata.category(c) in ("Cc", "Cs", "Co") or c in BIDI_CONTROLS for c in path):
        raise ValueError("A file name cannot contain control characters")
    shown_root = root.replace("\\", "/")
    if path.lower().startswith(shown_root.lower() + "/"):  # the model repeated the sandbox's full path
        path = path[len(shown_root) + 1:]
    full = os.path.realpath(os.path.join(root, path.lstrip("/")))
    try:
        inside = os.path.commonpath([root, full]) == root
    except ValueError:  # on Windows, a path on another drive
        inside = False
    if not inside:
        raise ValueError("That path is outside the sandbox")
    name = os.path.basename(full)
    if full != root and (not name.strip(". ") or len(name) > MAX_NAME_CHARS):
        raise ValueError(f"A file needs a name of at most {MAX_NAME_CHARS} characters")
    return full


def sandbox_files(root: str | None = None) -> list[str]:
    """Every file in the sandbox, as paths relative to it."""
    root = root or SANDBOX
    found = []
    for folder, _, names in os.walk(root):
        found += [os.path.relpath(os.path.join(folder, name), root).replace("\\", "/") for name in names]
    return sorted(found)


def room_for(full: str, root: str) -> None:
    """Refuse a new file once the sandbox holds MAX_FILES. Replacing an existing file is always allowed."""
    if not os.path.exists(full) and len(sandbox_files(root)) >= MAX_FILES:
        raise ValueError(f"The sandbox is full ({MAX_FILES} files). Overwrite an existing file instead")


def text_type(full: str) -> None:
    if os.path.splitext(full)[1].lower() not in WRITABLE_TYPES:
        raise ValueError(f"Only these file types can be used: {', '.join(sorted(WRITABLE_TYPES))}")


@tool
def send_push_notification(text: str) -> str:
    """Send a short push notification to the user's phone."""
    response = requests.post(
        "https://api.pushover.net/1/messages.json",
        data={"token": os.getenv("PUSHOVER_TOKEN"), "user": os.getenv("PUSHOVER_USER"), "message": text},
    )
    response.raise_for_status()
    return "Notification sent"


@tool
def request_human_help(instructions: str) -> str:
    """Ask the user to do something in the browser window that you cannot do yourself,
    such as logging in to a site, passing a captcha, or approving two-factor authentication.
    Explain exactly what you need them to do. The run pauses until they have done it."""
    return "The user says it is done. Continue with the task."


@tool
def read_pdf(path: str, config: RunnableConfig, max_pages: int = 20) -> str:
    """Read the text of a PDF file in the sandbox. Give the path relative to the sandbox, like report.pdf."""
    try:
        reader = PdfReader(in_sandbox(path, sandbox_root(config)))
    except OSError:
        raise ValueError(f"Could not open {path}. Check the file name") from None
    pages = [page.extract_text() or "" for page in reader.pages[:max_pages]]
    return "\n\n".join(pages)[:MAX_READ_CHARS] or "No text found in that PDF"


@tool
def create_slide(
    title: str, key_points: list[str], recommendation: str, config: RunnableConfig, filename: str = "slide.pptx"
) -> str:
    """Create a one-slide PowerPoint in the Voltway Research house style and save it in the sandbox.
    Give a short title, exactly three key points, and a one sentence recommendation."""
    filename = filename.strip() or "slide.pptx"
    if not filename.lower().endswith(".pptx"):
        filename += ".pptx"
    if not os.path.basename(filename.replace("\\", "/"))[:-5].strip(". "):
        raise ValueError("Give the slide a file name, like summary.pptx")
    key_points = [point for point in key_points if point.strip()]
    if not title.strip() or not recommendation.strip() or not key_points:
        raise ValueError("A slide needs a title, at least one key point and a recommendation")
    if len(title) > 120 or len(recommendation) > 500 or len(key_points) > 5 or any(len(p) > 300 for p in key_points):
        raise ValueError("That is too much text for one slide. Shorten the title, the points or the recommendation")
    root = sandbox_root(config)
    full = in_sandbox(filename, root)
    with FILE_LOCK:
        room_for(full, root)
        try:
            os.makedirs(os.path.dirname(full), exist_ok=True)
            build_slide(title, key_points, recommendation, full)
        except OSError:
            raise ValueError(f"Could not save {filename}. Try a different file name") from None
    return f"Saved the slide to {filename}"


@tool
def send_email(subject: str, body: str) -> str:
    """Send an email to the user. Use it for results the user asked to receive by email."""
    response = requests.post(
        "https://api.resend.com/emails",
        headers={"Authorization": f"Bearer {os.getenv('RESEND_API_KEY')}"},
        json={
            "from": "Sidekick <onboarding@resend.dev>",
            "to": [os.getenv("LEAD_EMAIL_TO")],
            "subject": subject,
            "text": body,
        },
        timeout=15,
    )
    response.raise_for_status()
    return "Email sent"


@tool
def run_python(code: str) -> str:
    """Run a short Python script and return what it prints. The script runs in the sandbox folder.
    Use print() to see results. Good for calculations and for working with files in the sandbox."""
    done = subprocess.run(
        [sys.executable, "-c", code], cwd=SANDBOX, capture_output=True, text=True, timeout=60
    )
    return (done.stdout + done.stderr)[-6000:] or "The script printed nothing"


@tool
def read_skill(name: str) -> str:
    """Read the full instructions of one of your skills, by its name."""
    try:
        with open(os.path.join(SKILLS, os.path.basename(name), "SKILL.md"), encoding="utf-8") as f:
            return f.read()
    except OSError:
        raise ValueError(f"There is no skill called {name}") from None


# --- File tools for a Space ----------------------------------------------------------------
#
# On your own machine the filesystem MCP server reads and writes the sandbox. That server needs
# Node, which a Space does not have, so there these three small tools do the job instead.
# write_file keeps the MCP tool's name on purpose: the approval list and the download link
# both go by that name. The config argument is filled in by LangChain; the model never sees it.


@tool
def write_file(path: str, content: str, config: RunnableConfig) -> str:
    """Write a text file in the sandbox, replacing it if it already exists.
    Give the path relative to the sandbox, like notes.md. Allowed types: .md, .txt, .csv, .json."""
    root = sandbox_root(config)
    full = in_sandbox(path, root)
    text_type(full)
    if len(content.encode("utf-8")) > MAX_FILE_BYTES:
        raise ValueError(f"That is too large. A file can be at most {MAX_FILE_BYTES // 1000} KB")
    with FILE_LOCK:
        room_for(full, root)
        try:
            os.makedirs(os.path.dirname(full), exist_ok=True)
            with open(full, "w", encoding="utf-8", newline="\n") as f:
                f.write(content)
        except OSError:
            raise ValueError(f"Could not write {path}. Try a shorter, simpler file name") from None
    return f"Successfully wrote to {path}"


@tool
def read_file(path: str, config: RunnableConfig) -> str:
    """Read a text file in the sandbox. Give the path relative to the sandbox, like notes.md."""
    full = in_sandbox(path, sandbox_root(config))
    text_type(full)
    try:
        with open(full, encoding="utf-8", errors="replace") as f:
            text = f.read(MAX_READ_CHARS + 1)
    except OSError:
        raise ValueError(f"There is no file called {path}. Use list_files to see what is in the sandbox") from None
    if len(text) > MAX_READ_CHARS:
        return text[:MAX_READ_CHARS] + "\n[The rest of this file was cut because it is too long]"
    return text or "That file is empty"


@tool
def list_files(config: RunnableConfig) -> str:
    """List the files in the sandbox."""
    return "\n".join(sandbox_files(sandbox_root(config))) or "The sandbox is empty"


def local_connections() -> dict:
    """The MCP servers that run as a program on this machine."""
    connections = {}
    if not ON_SPACE:  # these need Node, and the browser needs a screen
        connections["playwright"] = {
            "transport": "stdio", "command": "npx", "args": ["-y", "@playwright/mcp@latest", "--isolated"],
        }
        connections["filesystem"] = {
            "transport": "stdio", "command": "npx",
            "args": ["-y", "@modelcontextprotocol/server-filesystem", SANDBOX],
        }
        connections["memory"] = {
            "transport": "stdio", "command": "npx", "args": ["-y", "@modelcontextprotocol/server-memory"],
            "env": {"MEMORY_FILE_PATH": os.path.join(DATA, "sidekick_knowledge.json")},
        }
    # The free page reader runs on this machine and will open any address it is given, including
    # ones only this machine can reach. That is fine at home and not on a public Space, so there
    # pages are read through Firecrawl (a remote service) or not at all.
    if not os.getenv("FIRECRAWL_API_KEY") and not ON_SPACE:
        if importlib.util.find_spec("mcp_server_fetch"):
            command, args = sys.executable, ["-m", "mcp_server_fetch"]
        else:
            command, args = "uvx", ["mcp-server-fetch"]
        connections["fetch"] = {"transport": "stdio", "command": command, "args": args}
    return connections


def remote_connections() -> dict:
    """The MCP servers that are a URL and a key. Each one is added only if its key is set."""
    connections = {}
    if os.getenv("FIRECRAWL_API_KEY"):
        connections["firecrawl"] = {
            "transport": "streamable_http", "url": "https://mcp.firecrawl.dev/v2/mcp",
            "headers": {"Authorization": f"Bearer {os.getenv('FIRECRAWL_API_KEY')}"},
        }
    if os.getenv("GITHUB_PERSONAL_ACCESS_TOKEN") and not ON_SPACE:
        connections["github"] = {
            "transport": "streamable_http", "url": "https://api.githubcopilot.com/mcp/",
            "headers": {
                "Authorization": f"Bearer {os.getenv('GITHUB_PERSONAL_ACCESS_TOKEN')}",
                "X-MCP-Readonly": "true",
                "X-MCP-Toolsets": "repos,issues,pull_requests",
            },
        }
    if os.getenv("CONTEXT7_API_KEY"):
        connections["context7"] = {
            "transport": "streamable_http", "url": "https://mcp.context7.com/mcp",
            "headers": {"CONTEXT7_API_KEY": os.getenv("CONTEXT7_API_KEY")},
        }
    return connections


# The big servers offer far more tools than the Sidekick needs. Every tool costs tokens on
# every model call, so keep a short list from the remote servers and drop the odd local ones.
REMOTE_KEEP = {
    "firecrawl_scrape",
    "resolve-library-id", "query-docs",
    "get_file_contents", "search_repositories", "search_code", "list_commits",
    "list_issues", "issue_read", "list_pull_requests", "pull_request_read",
}
LOCAL_DROP = {
    "read_file", "read_media_file", "list_directory_with_sizes", "get_file_info", "list_allowed_directories",
    "copy_file", "append_file",
    "browser_resize", "browser_emulate_media", "browser_drop", "browser_drag", "browser_network_requests",
    "browser_network_request", "browser_console_messages", "browser_run_code_unsafe", "browser_file_upload",
    "browser_evaluate",
}


class McpSessions:
    """Holds persistent MCP sessions open so the browser keeps its state between tool calls.

    The stdio transport must be opened and closed from the same asyncio task, so one
    background task owns the sessions: it opens them, waits, and unwinds them when stop()
    is called. Stopping shuts down the servers, and you will see the browser close.
    """

    def __init__(self, connections: dict):
        self.connections = connections
        self.tools = []
        self._ready = asyncio.Event()
        self._stop = asyncio.Event()
        self._task = None

    async def _run(self):
        client = MultiServerMCPClient(self.connections)
        async with AsyncExitStack() as stack:
            for name in self.connections:
                session = await stack.enter_async_context(client.session(name))
                self.tools += await load_mcp_tools(session, server_name=name)
            self._ready.set()
            await self._stop.wait()

    async def start(self) -> list:
        self._task = asyncio.create_task(self._run())
        ready = asyncio.create_task(self._ready.wait())
        await asyncio.wait([ready, self._task], return_when=asyncio.FIRST_COMPLETED)
        ready.cancel()
        if self._task.done():
            self._task.result()  # the servers failed to start; raise the real error
        return self.tools

    def stop(self):
        self._stop.set()


async def get_all_tools():
    """Return the full tool list, the session holder, and a note for each remote server that failed."""
    sessions = McpSessions(local_connections())
    local_tools = await sessions.start()

    remote_tools, notices = [], []
    for name, connection in remote_connections().items():
        try:
            remote_tools += await MultiServerMCPClient({name: connection}).get_tools()
        except Exception as error:  # one server being down should not stop the app
            notices.append(f"{name} is unavailable: {str(error)[:120]}")

    mcp_tools = [t for t in local_tools if t.name not in LOCAL_DROP] + [t for t in remote_tools if t.name in REMOTE_KEEP]
    our_tools = [search, wikipedia_lookup, create_slide, read_skill]
    if ON_SPACE:  # no filesystem MCP server here, so use our own file tools
        our_tools += [list_files, read_file, write_file]
    else:  # a public Space must not message you or run code for strangers, and has no PDFs to read
        our_tools += [read_pdf, send_push_notification, send_email, run_python, request_human_help]
    return our_tools + mcp_tools, sessions, notices
