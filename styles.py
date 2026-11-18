"""The look of Sidekick Plus: a terminal. Black, one box, one monospace face, six colours.

Each colour has one job:
    off-white  answers and what you type
    grey       secondary text: tool results, criteria, the status line
    cyan       what the Sidekick is doing: tool calls, the step in progress, links
    yellow     anything waiting for you: the prompt mark, the approval prompt
    green      success
    red        work sent back, errors

In Gradio 6, theme, css and head are passed to launch() rather than to gr.Blocks().
"""

import gradio as gr

BLACK = "#000000"
TEXT = "#E8E8E8"
GREY = "#8C8C8C"
RULE = "#2A2A2A"
CYAN = "#4FD6E8"
YELLOW = "#F5D547"
GREEN = "#6BD968"
RED = "#FF6B6B"

MONO = [gr.themes.GoogleFont("JetBrains Mono"), "Cascadia Code", "Consolas", "ui-monospace", "monospace"]

THEME = gr.themes.Base(font=MONO, font_mono=MONO)

CSS = """
:root, .dark, .gradio-container {
    --black: #000000; --text: #E8E8E8; --grey: #8C8C8C; --rule: #2A2A2A; --lift: #101010;
    --cyan: #4FD6E8; --yellow: #F5D547; --green: #6BD968; --red: #FF6B6B;
    --size: 13px; --lh: 1.5; --pad: 12px;

    /* Gradio's own variables, so its components start from the same black */
    --body-background-fill: var(--black); --background-fill-primary: var(--black);
    --background-fill-secondary: var(--black); --block-background-fill: var(--black);
    --input-background-fill: var(--black); --input-background-fill-focus: var(--black);
    --input-background-fill-hover: var(--black);
    --body-text-color: var(--text); --body-text-color-subdued: var(--grey);
    --block-label-text-color: var(--grey); --input-placeholder-color: #6F6F6F;
    --border-color-primary: var(--rule); --border-color-accent: var(--rule);
    --block-border-width: 0px; --block-shadow: none; --block-radius: 0px; --block-padding: 0px;
    --container-radius: 0px; --layout-gap: 0px; --form-gap-width: 0px;
    --input-border-width: 0px; --input-radius: 0px; --input-padding: 0px;
    --input-shadow: none; --input-shadow-focus: none; --input-border-color-focus: transparent;
    --input-text-size: var(--size); --body-text-size: var(--size);
    --button-border-width: 0px; --button-large-radius: 0px; --button-small-radius: 0px;
    --button-shadow: none; --button-shadow-hover: none; --button-shadow-active: none;
    --color-accent: var(--yellow); --color-accent-soft: var(--lift);
    --link-text-color: var(--cyan); --link-text-color-hover: var(--cyan);
    --link-text-color-visited: var(--cyan); --link-text-color-active: var(--cyan);
    color-scheme: dark;
}

html, body { background: #000000; margin: 0; }
.gradio-container {
    max-width: none !important; padding: 6px !important; margin: 0 !important;
    background: var(--black) !important; font-size: var(--size); line-height: var(--lh);
}
.gradio-container .main, .gradio-container .wrap, .gradio-container .contain { padding: 0 !important; gap: 0 !important; }
footer { display: none !important; }

::selection { background: var(--cyan); color: var(--black); }
* { scrollbar-width: thin; scrollbar-color: var(--rule) transparent; }

/* The box */
#term {
    height: calc(100dvh - 12px); min-height: 320px; gap: 0; flex-wrap: nowrap;
    border: 1px solid var(--rule); background: var(--black); color: var(--text);
    font-size: var(--size); line-height: var(--lh); overflow: hidden;
}
#term > * { flex: none !important; min-width: 0 !important; }
#term .block, #term .form, #term .html-container, #term .prose {
    background: transparent; border: 0; border-radius: 0; box-shadow: none; padding: 0; margin: 0;
    font-size: var(--size); line-height: var(--lh); color: inherit; overflow: visible; max-width: none;
}
#term :focus-visible { outline: 1px solid var(--yellow); outline-offset: 1px; }

/* Title bar: the name, then the thread picker */
#term #bar {
    align-items: center; gap: 2ch; padding: 5px var(--pad); border-bottom: 1px solid var(--rule);
    flex-wrap: nowrap;
}
#bar > * { flex: none; min-width: 0; }
#bar #title { flex: 1 1 auto; overflow: hidden; }
#title .title { white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
#title .title b { font-weight: 700; color: var(--text); }
#title .title span { color: var(--grey); }
#term #threads { flex: 0 1 auto !important; width: min(46ch, 42vw); min-width: 0 !important; overflow: visible !important; }
#threads .wrap, #threads .wrap-inner, #threads .secondary-wrap {
    background: transparent; border: 0; box-shadow: none; padding: 0; margin: 0; gap: 4px; min-height: 0;
}
#threads input {
    color: var(--cyan); font-size: var(--size); line-height: var(--lh); padding: 0; margin: 0;
    height: auto; text-overflow: ellipsis; cursor: pointer; text-align: right;
}
#threads .icon-wrap { display: none; }
#threads .secondary-wrap { display: flex; align-items: baseline; }
#threads .secondary-wrap::after { content: '▾'; flex: none; color: var(--grey); }
#threads ul.options, .gradio-container ul.options {
    background: var(--black); border: 1px solid var(--rule); border-radius: 0; box-shadow: none;
    font-size: var(--size); padding: 2px 0; max-height: 50vh !important;
    width: min(64ch, calc(100vw - 14px)) !important; min-width: 0 !important; max-width: none !important;
    left: auto !important; right: 7px !important; overflow-x: hidden;
}
ul.options li.item {
    display: block; width: 100% !important; max-width: none !important; box-sizing: border-box;
    color: var(--text); padding: 2px var(--pad); border-radius: 0; white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
}
ul.options li.item * { display: inline; }
ul.options li.item.selected, ul.options li.item.active, ul.options li.item:hover { background: var(--lift); color: var(--cyan); }
#term button.plain {
    background: transparent; color: var(--cyan); border: 0; padding: 0 2px; min-width: 0; width: auto;
    font: inherit; font-weight: 400; line-height: var(--lh); cursor: pointer;
}
#term button.plain:hover { text-decoration: underline; text-underline-offset: 3px; }
#term button.plain:active { color: var(--text); }

/* The log: the one part that grows and scrolls */
#term > #log {
    flex: 1 1 0 !important; min-height: calc(var(--size) * var(--lh) * 5);
    overflow-y: auto !important; overflow-x: hidden !important; overscroll-behavior: contain;
}
#term #log .html-container { overflow: visible; }
.log { padding: 8px var(--pad) 10px; overflow-wrap: anywhere; }
.log .gap { height: calc(var(--size) * var(--lh)); }
.log .line, .log .req, .log .crit, .log .note, .log .tool, .log .res, .log .verdict { white-space: pre-wrap; }
.log .mark, #prompt::before { color: var(--yellow); }
.log .req { display: flex; gap: 1ch; color: var(--text); }
.log .req .mark { flex: none; }
.log .crit { color: var(--grey); padding-left: 2ch; }
.log .note { color: var(--grey); padding-left: 2ch; }
.log .dim { color: var(--grey); }
.log .error { color: var(--red); }
.log .notice { color: var(--yellow); }

.log .tool { display: flex; gap: 1ch; }
.log .tool .pip { flex: none; color: var(--cyan); }
.log .tool .name { color: var(--cyan); }
.log .tool .args { color: var(--text); }
.log .tool.failed .pip, .log .tool.failed .name { color: var(--red); }
.log .tool.waiting .pip, .log .tool.waiting .name { color: var(--yellow); }
.log .tool.idle .pip, .log .tool.declined .pip, .log .tool.declined .name, .log .tool.declined .args { color: var(--grey); }

.log .res { color: var(--grey); padding-left: 2ch; }
.log div.res, .log details.res > summary { display: flex; gap: 1ch; }
.log .res .elbow { flex: none; color: #5A5A5A; }
.log .res.failed { color: var(--red); }
.log .res.declined, .log .res.waiting { color: var(--yellow); }
.log .res.running { color: var(--cyan); }
.log details.res .preview { flex: 0 1 auto; min-width: 0; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.log .res .size { flex: none; color: #6F6F6F; white-space: nowrap; }
.log .res a.file { flex: none; white-space: nowrap; }
.log details.res > summary { list-style: none; cursor: pointer; }
.log details.res > summary::-webkit-details-marker { display: none; }
.log details.res > summary:hover .preview { color: var(--text); }
.log details.res[open] > summary { color: var(--text); }
.log details.res pre {
    margin: 2px 0 6px 2ch; padding: 6px 10px; border-left: 1px solid var(--rule); background: transparent;
    color: var(--grey); font: inherit; white-space: pre-wrap; overflow-wrap: anywhere;
    max-height: 40vh; overflow: auto;
}
.log a, .log a:visited { margin: 0; padding: 0; color: var(--cyan); text-decoration: underline; text-underline-offset: 3px; text-decoration-thickness: 1px; }
.log a:hover { color: var(--text); }

/* The answer: markdown, kept in the same face and size */
.log .answer { color: var(--text); padding: calc(var(--size) * var(--lh) / 2) 0 0 2ch; max-width: 100ch; }
.log .answer > :first-child { margin-top: 0; }
.log .answer > :last-child { margin-bottom: 0; }
.log .answer p, .log .answer ul, .log .answer ol, .log .answer pre, .log .answer table, .log .answer blockquote {
    margin: 0 0 calc(var(--size) * var(--lh) / 2); font-size: var(--size); line-height: var(--lh); color: inherit;
}
.log .answer h1, .log .answer h2, .log .answer h3, .log .answer h4 {
    font-size: var(--size); line-height: var(--lh); font-weight: 700; color: var(--text);
    margin: calc(var(--size) * var(--lh)) 0 calc(var(--size) * var(--lh) / 2);
}
.log .answer strong { font-weight: 700; color: #FFFFFF; }
.log .answer ul, .log .answer ol { padding-left: 3ch; }
.log .answer ul { list-style: "- "; }
.log .answer ol { list-style: decimal; }
.log .answer li { margin: 0; }
.log .answer code { font: inherit; color: var(--cyan); background: transparent; padding: 0; border: 0; }
.log .answer pre { padding: 6px 10px; border-left: 1px solid var(--rule); background: transparent; overflow-x: auto; white-space: pre; }
.log .answer pre code { color: var(--text); }
.log .answer blockquote { padding-left: 2ch; border-left: 1px solid var(--rule); color: var(--grey); }
.log .answer .table { overflow-x: auto; margin: 0 0 calc(var(--size) * var(--lh) / 2); }
.log .answer table { border-collapse: collapse; width: max-content; max-width: 96ch; margin: 0; }
.log .answer th, .log .answer td {
    border: 1px solid var(--rule); padding: 1px 1ch; text-align: left; vertical-align: top;
    font-variant-numeric: tabular-nums; overflow-wrap: normal; word-break: normal; min-width: 8ch;
}
.log .answer th { font-weight: 700; color: var(--text); }
.log .answer hr { border: 0; border-top: 1px solid var(--rule); margin: calc(var(--size) * var(--lh) / 2) 0; }

.log .verdict { padding: calc(var(--size) * var(--lh) / 2) 0 0 2ch; display: flex; flex-direction: column; }
.log .verdict .why { color: var(--grey); padding-left: 2ch; }
.log .verdict.accept .head { color: var(--green); }
.log .verdict.retry .head { color: var(--red); }
.log .verdict.ask .head { color: var(--yellow); }

.log .working { color: var(--cyan); padding-top: 2px; }
@keyframes pulse { 50% { opacity: 0.3; } }
.log .working .pip, .log .tool.running .pip, .status .state.working .pip, .status .state.starting .pip {
    animation: pulse 1.1s ease-in-out infinite;
}
@media (prefers-reduced-motion: reduce) {
    .log .working .pip, .log .tool.running .pip, .status .state .pip { animation: none; }
}

/* The plan: one wrapping line above the prompt */
#term > #plan:not(:has(.plan)) { display: none; }
.plan {
    display: flex; flex-wrap: wrap; gap: 0 3ch; padding: 4px var(--pad); border-top: 1px solid var(--rule);
    max-height: calc(var(--size) * var(--lh) * 4 + 8px); overflow-y: auto;
}
.plan .todo { color: var(--text); overflow-wrap: anywhere; }
.plan .todo .box { margin-right: 1ch; color: var(--grey); }
.plan .todo.in_progress, .plan .todo.in_progress .box { color: var(--cyan); }
.plan .todo.completed { color: var(--grey); }
.plan .todo.completed .box { color: var(--green); }

/* The approval prompt */
#term #approval { border-top: 1px solid var(--yellow); padding: 6px var(--pad); gap: 0; }
#approval > * { flex: none; }
.approval { overflow-wrap: anywhere; max-height: 36vh; overflow-y: auto; }
.approval .action { margin-bottom: 4px; }
.approval .action .name { color: var(--yellow); }
.approval .arg { display: flex; gap: 2ch; padding-left: 2ch; }
.approval .arg .key { flex: none; color: var(--grey); min-width: 8ch; }
.approval .arg .value { color: var(--text); white-space: pre-wrap; }
.approval .question { color: var(--text); }
#term button.choice {
    display: block; width: 100%; text-align: left; justify-content: flex-start; white-space: normal;
    background: transparent; color: var(--text); border: 0; padding: 1px 2ch !important; min-height: 0; margin: 0;
    font: inherit; font-weight: 400; line-height: var(--lh); cursor: pointer; overflow-wrap: anywhere;
}
#term button.choice:hover, #term button.choice:focus-visible { background: var(--lift); color: var(--yellow); }
#term button.choice:active { background: var(--rule); }
#approval .hint { color: var(--grey); padding: 1px 2ch; }

/* The prompt and the criteria line */
#term #prompt, #term #criteria {
    display: flex; flex-direction: row; align-items: flex-start; gap: 1ch; padding: 0 var(--pad);
    overflow: visible !important;
}
#term #prompt { border-top: 1px solid var(--rule); padding-top: 5px; }
#term #criteria { padding-bottom: 5px; }
#prompt::before { content: ">"; flex: none; opacity: 0.55; }
#prompt:focus-within::before { opacity: 1; }
#criteria::before { content: "  criteria:"; white-space: pre; flex: none; color: #6F6F6F; }
#criteria:focus-within::before { color: var(--text); }
#prompt > *, #criteria > * { flex: 1 1 auto; min-width: 0; }
#prompt label, #criteria label, #prompt .input-container, #criteria .input-container { display: block; padding: 0; margin: 0; }
#prompt textarea, #criteria textarea, #criteria input {
    display: block; width: 100%; background: transparent !important; border: 0 !important; box-shadow: none !important;
    outline: none !important; padding: 0 !important; margin: 0; resize: none; border-radius: 0;
    font: inherit; font-size: var(--size); line-height: var(--lh); color: var(--text); caret-color: var(--yellow);
}
#criteria textarea, #criteria input { color: var(--grey); }
#criteria:focus-within textarea, #criteria:focus-within input { color: var(--text); }
#prompt textarea::placeholder, #criteria textarea::placeholder, #criteria input::placeholder { color: #6F6F6F; opacity: 1; }
#prompt textarea:disabled { cursor: progress; opacity: 1; }

/* The status line */
.status {
    padding: 4px var(--pad); border-top: 1px solid var(--rule); color: var(--grey);
    white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
}
.status .sep { color: #5A5A5A; }
.status .state.ready { color: var(--green); }
.status .state.working, .status .state.starting { color: var(--cyan); }
.status .state.waiting { color: var(--yellow); }

/* Gradio's "processing" overlays would cover the log while a run streams in */
#term .wrap.default, #term .generating, #term .progress-text, #term .eta-bar { display: none !important; }
#term .pending, #term .translucent { opacity: 1 !important; }

@media (max-width: 640px) {
    :root, .dark, .gradio-container { --pad: 8px; }
    .gradio-container { padding: 0 !important; }
    #term { height: 100dvh; border-left: 0; border-right: 0; }
    #title .title span { display: none; }
    .status .extra { display: none; }
    .approval .arg { flex-direction: column; gap: 0; }
}
"""

# Small behaviours the terminal needs: follow the log as it grows, give focus back to the
# prompt when a run ends, and let 1 and 2 answer an approval prompt from the keyboard.
HEAD = """
<script>
(function () {
    let stuck = true;
    const logBox = () => document.querySelector('#log');
    const prompt = () => document.querySelector('#prompt textarea');

    function follow() {
        const box = logBox();
        if (box && stuck) box.scrollTop = box.scrollHeight;
    }
    function watch() {
        const box = logBox();
        if (!box) return setTimeout(watch, 200);
        box.addEventListener('scroll', () => {
            stuck = box.scrollHeight - box.scrollTop - box.clientHeight < 48;
        });
        new MutationObserver(follow).observe(box, { childList: true, subtree: true, characterData: true });
        new MutationObserver(() => {
            const input = prompt();
            const active = document.activeElement;
            const typing = active && active !== input && /^(TEXTAREA|INPUT)$/.test(active.tagName);
            if (input && !input.disabled && !typing) input.focus({ preventScroll: true });
            follow();
        }).observe(document.querySelector('#term'), { attributes: true, subtree: true, attributeFilter: ['disabled'] });
        follow();
    }
    document.addEventListener('keydown', (event) => {
        if (event.ctrlKey || event.metaKey || event.altKey) return;
        const input = prompt();
        if (event.target !== input || input.value !== '') return;
        const button = document.querySelector(event.key === '1' ? '#yes' : event.key === '2' ? '#always' : null);
        if (button && button.offsetParent !== null) {
            event.preventDefault();
            button.click();
        }
    });
    if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', watch);
    else watch();
})();
</script>
"""
