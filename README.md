---
title: Sidekick Plus
emoji: ⌨️
colorFrom: gray
colorTo: yellow
sdk: gradio
sdk_version: 6.14.0
python_version: 3.12
app_file: app.py
pinned: false
short_description: A personal co-worker agent with an evaluator, in a terminal
---

# Sidekick Plus

> A personal co-worker agent with a terminal-style screen. You give it a task and, if you like, the criteria you will judge it by. A worker agent plans, uses its tools and answers; a second model from a different family checks the answer against your criteria and either accepts it or sends it back. Anything that writes a file or reaches outside stops and asks you first.

**Live demo:** https://huggingface.co/spaces/VKM47/sidekick-plus

Built as the Week 4 project (LangChain and LangGraph) of Ed Donner's *AI Engineer Agentic Track: The Complete Agent & MCP Course*, then extended with a second-family evaluator, Claude-Code-style approvals, saved threads, MCP servers, agent skills and a public deployment with per-visitor isolation and request caps.

---

## Contents

- [What it does](#what-it-does)
- [How it works](#how-it-works)
- [Approvals](#approvals)
- [Tools](#tools)
- [Project structure](#project-structure)
- [Tech stack](#tech-stack)
- [Running locally](#running-locally)
- [Settings](#settings)
- [Using it](#using-it)
- [On this Space](#on-this-space)
- [Deploying to a Space of your own](#deploying-to-a-space-of-your-own)
- [Design decisions](#design-decisions)
- [Acknowledgements](#acknowledgements)
- [Licence](#licence)
- [Contact](#contact)

---

## What it does

- **Works, then gets checked.** The worker answers; an evaluator from another model family scores the answer from 1 to 10 against your criteria, lists what is missing, and decides: accept, retry, or ask you.
- **Retries with feedback.** A rejected answer goes back to the worker with the evaluator's feedback, up to 3 attempts (2 on the public Space).
- **Asks before it acts.** Writing files, sending email, running Python and driving the browser all pause for approval: yes, yes and don't ask again, or no with a note saying what to do instead.
- **Remembers.** Every conversation is a saved thread you can return to. A run waiting for approval survives a page reload.
- **Uses skills.** Folders of instructions (for example, how to write a research brief) that the worker reads when a task calls for them.
- **Shows its work.** Each tool call, the plan, and the verdict appear as lines in a terminal-style log.

---

## How it works

```
START -> worker -> evaluator -> route --retry--> feedback -> worker
                                      \--accept / needs you / out of attempts--> END
```

The outer loop is a LangGraph `StateGraph` with three nodes and one router.

- **Worker.** One LangChain `create_agent` with the tools below and a stack of middleware. Default model: `anthropic:claude-opus-5-5` locally, `anthropic:claude-haiku-5-5` on a Space.
- **Evaluator.** One structured-output call to an OpenAI model (`gpt-5.5` by default). It sees the conversation, the task and criteria, every tool call with its arguments and the start of each result, and the worker's last reply. It returns `feedback`, `score`, `missing` and `next_action`.
- **Feedback.** Turns a `retry` verdict into the worker's next instruction.
- **Memory.** A SQLite checkpointer (`AsyncSqliteSaver`). Each conversation is a thread; the same store holds a run in place while it waits for approval.

### Worker middleware, in order

| Middleware | What it does |
|---|---|
| `TolerateToolErrors` | Turns a tool exception into an error message the model can react to. |
| `block_list` | Refuses calls that mention blocked sites. |
| `clip_long_results` | Cuts a long tool result (20,000 characters locally, 6,000 on a Space) and says so. |
| `SkillsMiddleware` | Lists each skill's name and description in the system prompt. |
| `TodoListMiddleware` | Gives the worker a to-do list, shown on screen as the plan. |
| `PIIMiddleware` | Redacts email addresses and card numbers. |
| `ModelCallLimitMiddleware` | Limits model calls per stretch of work and per attempt. |
| `ToolCallLimitMiddleware` (Space) / `ModelFallbackMiddleware` (local) | A cap of 25 tool calls on a Space; a fallback to `anthropic:claude-sonnet-5-5` locally. |
| `HumanInTheLoopMiddleware` | Pauses before any tool on the approval list. |

### From a run to the screen

`sidekick.py` streams a run as plain event dicts (`tool_call`, `tool_result`, `todos`, `answer`, `verdict`, `approval`, …). `render.py` turns events into HTML with pure functions, and `app.py` wires them to Gradio. The renderer has no state and no Gradio imports, so the screen can be tested with canned events and no model calls.

---

## Approvals

When the worker wants to use a tool that changes something, the run pauses and the screen shows what it is about to do.

| Choice | Key | Effect |
|---|---|---|
| Yes | `1` | Allow this call once. |
| Yes, and don't ask again | `2` | Allow this tool for the rest of the session. |
| No, and say what to do instead | type, then Enter | Decline; your note goes to the worker as its next instruction. |

Tools that ask: file writes and edits, `create_slide`, `run_python`, `send_email`, `send_push_notification`, `request_human_help`, browser actions (navigate, click, type, fill, press, select, dialogs) and deletions from the knowledge graph. Reading tools never ask.

---

## Tools

| Tool | What it does | Local | Space |
|---|---|---|---|
| `search` | Web search through Serper | yes | yes |
| `wikipedia_lookup` | Wikipedia summaries | yes | yes |
| `read_skill` | Reads a skill's full instructions | yes | yes |
| `create_slide` | Builds a one-slide `.pptx` | yes | yes |
| `list_files`, `read_file`, `write_file` | Plain-text files in the visitor's own folder | through MCP | yes |
| `firecrawl_scrape` | Reads a web page (Firecrawl MCP) | with key | with key |
| `resolve-library-id`, `query-docs` | Current library documentation (Context7 MCP) | with key | with key |
| Filesystem tools | Read, write, edit, move within the sandbox (filesystem MCP) | yes | no |
| `browser_*` | A real browser (Playwright MCP) | yes | no |
| Knowledge-graph tools | A memory of facts about you (memory MCP) | yes | no |
| `fetch` | Free page reader (fetch MCP), used when there is no Firecrawl key | yes | no |
| GitHub read tools | Repos, issues and pull requests, read-only (GitHub MCP) | with token | no |
| `read_pdf` | Text of a PDF in the sandbox | yes | no |
| `run_python` | Runs Python in the sandbox, 60-second limit | yes | no |
| `send_email` | Email through Resend | yes | no |
| `send_push_notification` | Push through Pushover | yes | no |
| `request_human_help` | Asks you to do something in the browser window | yes | no |

Every file path the worker gives is checked to be inside the sandbox; `..`, symlinks and look-alike folder names are refused.

### Skills

A skill is a folder under `skills/` with a `SKILL.md`. Add a folder and the worker can use it; no code change is needed.

| Skill | Purpose |
|---|---|
| `research-brief` | A sourced markdown briefing readable in two minutes: bottom line, a section per option, comparison table, recommendation, numbered sources. |
| `fleet-slide` | House style for a one-slide deck built with `create_slide`. |

---

## Project structure

```
.
├── app.py              # Gradio app: sessions, request caps on a Space, event handlers, launch
├── sidekick.py         # The graph, the worker's middleware, the evaluator, and the Sidekick class that streams events
├── sidekick_tools.py   # Mode detection, sandbox rules, own tools, MCP connections, tool filtering
├── render.py           # Events to terminal-style HTML (pure functions)
├── styles.py           # Theme, CSS and the small page script (auto-scroll, 1 and 2 keys)
├── slide_kit.py        # One-slide .pptx builder (python-pptx)
├── skills/
│   ├── fleet-slide/    # SKILL.md and logo.png
│   └── research-brief/ # SKILL.md
├── requirements.txt    # Pinned dependencies
├── .env.example        # The keys the app reads
├── LICENSE
└── README.md
```

Created at run time and not in the repo: `sandbox/` (the agent's working folder), `sidekick_memory.db` (saved threads) and `sidekick_knowledge.json` (the memory server's store).

---

## Tech stack

| Layer | Choice |
|---|---|
| Agent framework | LangChain 1.3.4 (`create_agent`, middleware), LangGraph 1.2.4 (`StateGraph`) |
| Worker model | Anthropic Claude through `langchain-anthropic` 1.4.4 |
| Evaluator model | OpenAI through `langchain-openai` 1.2.2 |
| Memory | `langgraph-checkpoint-sqlite` 3.1.0 with `aiosqlite` |
| Skills | `deepagents` 0.6.8 (`SkillsMiddleware`) |
| MCP | `langchain-mcp-adapters` 0.2.2, `mcp` 1.27.0 |
| Screen | Gradio 6.14.0, `markdown-it-py` |
| Other | `python-pptx`, `pypdf`, `wikipedia`, `requests` |
| Python | 3.12 |

`mcp` is pinned to 1.27.0 because a newer version breaks `langchain-mcp-adapters` 0.2.2.

---

## Running locally

You need Python 3.12 and, for the browser, filesystem and memory tools, Node.js (they start through `npx`).

```bash
git clone https://github.com/Vishnu-K-Menon/Sidekick-Plus.git
cd Sidekick-Plus

python -m venv .venv
.venv\Scripts\activate        # Windows; on macOS or Linux: source .venv/bin/activate
pip install -r requirements.txt

copy .env.example .env        # macOS or Linux: cp .env.example .env
# fill in the three required keys in .env

python app.py
```

The app opens in your browser. Locally there are no request caps, the worker is Opus with a Sonnet fallback, and every tool in the table above is available if its key is set. Without a `FIRECRAWL_API_KEY` the free page reader is started through `uvx`, which needs [uv](https://docs.astral.sh/uv/).

To try the public-Space behaviour on your own machine, set `SPACE_ID` to any value before starting.

---

## Settings

Keys:

| Name | Needed | Used for |
|---|---|---|
| `ANTHROPIC_API_KEY` | yes | the worker |
| `OPENAI_API_KEY` | yes | the evaluator |
| `SERPER_API_KEY` | yes | web search |
| `FIRECRAWL_API_KEY` | no | reading web pages. Without it, a Space has no page reader; locally the free fetch server is used |
| `CONTEXT7_API_KEY` | no | current library documentation |
| `GITHUB_PERSONAL_ACCESS_TOKEN` | no, local only | read-only GitHub tools |
| `PUSHOVER_TOKEN`, `PUSHOVER_USER` | no, local only | push notifications |
| `RESEND_API_KEY`, `LEAD_EMAIL_TO` | no, local only | email |

Variables:

| Name | Default | Notes |
|---|---|---|
| `SIDEKICK_WORKER_MODEL` | `anthropic:claude-opus-5-5` locally, `anthropic:claude-haiku-5-5` on a Space | the worker model |
| `SIDEKICK_EVALUATOR_MODEL` | `gpt-5.5` | any OpenAI chat model |

---

## Using it

- Type what you want done at the `>` prompt and press Enter.
- The `criteria:` line is optional. Leave it empty and the answer is judged on being clear, correct and complete.
- Each tool call appears as a `●` line with its arguments. Click the result under it to unfold it.
- When the Sidekick wants to write a file or do anything that reaches outside, it stops and asks. Press `1` to allow it once, `2` to allow that tool for the rest of the session, or type what it should do instead and press Enter.
- The verdict line shows the outcome: `✓ accept`, `✗ retry`, or `? needs you`.
- The picker at the top right switches between saved threads; `new` starts a fresh one.

---

## On this Space

This is a public demo, so it runs with limits:

- The worker here is Claude Sonnet 5.5, set through `SIDEKICK_WORKER_MODEL`.
- The browser, email, push notification, Python and GitHub tools are switched off.
- Each visit gets its own private folder and thread list. Other visitors cannot see them.
- The Sidekick can write `.md`, `.txt`, `.csv` and `.json` files (up to 200 KB each, 20 per visit) and one-slide `.pptx` files. Each gets a download link in the log.
- 8 requests per visit, 24 a day from one network, and 150 a day across all visitors. A request is refused over 4,000 characters.
- Each request gets at most 2 attempts of 15 model calls each, and is stopped after 4 minutes.
- Two requests run at a time; others wait in a queue.
- Reading web pages needs a `FIRECRAWL_API_KEY`. Without one the Sidekick has search and Wikipedia only.

Saved threads and written files live on the Space's temporary disk and are cleared whenever it restarts. The daily counts are kept in memory, so a restart also sets them back to zero; the dependable ceiling on cost is a spend limit on the API keys themselves.

---

## Deploying to a Space of your own

1. Create a Gradio Space on Hugging Face.
2. Upload this folder **with exclusions**. Plain `gradio deploy` uploads everything in the folder, including your local `sidekick_memory.db` (your own conversations) and `sandbox/`.

   ```python
   from huggingface_hub import HfApi

   HfApi().upload_folder(
       folder_path=".",
       repo_id="<your-name>/<your-space>",
       repo_type="space",
       ignore_patterns=[
           ".git/**", "*.db", "*.db-*", "sandbox/*", "sandbox/**", ".gradio/**",
           "**/__pycache__/**", "sidekick_knowledge.json", ".env", ".venv/**",
       ],
   )
   ```

3. In the Space's **Settings → Variables and secrets**, add `ANTHROPIC_API_KEY`, `OPENAI_API_KEY` and `SERPER_API_KEY` as secrets. Use keys with a monthly spend limit.
4. The Space restarts by itself. Hugging Face sets `SPACE_ID`, which switches the app to its public mode.

---

## Design decisions

- **A judge from another model family.** A model is a weak critic of its own family's work, so the worker is Anthropic and the evaluator is OpenAI. The evaluator sees tool arguments and result excerpts, not only tool names, so it can tell a sourced answer from an invented one.
- **The retry loop is a graph, not a Python loop.** Worker, evaluator and feedback are nodes, so a checkpoint can hold the run at any point, including in the middle of an approval.
- **Two model-call limits.** The per-stretch limit resets at every approval pause, so with "don't ask again" switched on it would never stop a run. A second limit counts the whole attempt, pauses included, and is what bounds cost.
- **Private data stays out of the served folder.** On a Space, Gradio serves its cache folder to anyone. Only the per-visitor sandbox lives there; the conversations database sits in a private temporary folder.
- **Plain-text files only on a Space.** A file in a served folder is delivered with its real type, so an `.html` file would run in a visitor's browser. The writable types are `.md`, `.txt`, `.csv` and `.json`, plus `.pptx` from the slide tool, and links are downloads.
- **Limits enforced on the server.** Hiding an endpoint from Gradio's API page does not stop a hand-written request, so every cap is checked inside the handler.
- **A pure renderer.** `render.py` escapes everything that comes from a model, a tool or a user, and renders Markdown with raw HTML off.
- **One colour, one job.** Cyan for agent activity, yellow for waiting on you, green for success, red for retry or error, grey for secondary text.


---

## Licence

MIT. See [LICENSE](LICENSE).

---

## Contact

- Email: vishnumenon@myworkgmail.com
- LinkedIn: https://www.linkedin.com/in/vkm47/
- GitHub: https://github.com/Vishnu-K-Menon
