#!/usr/bin/env python3
"""Render the demo images and videos from the recorded session (no model calls).

    python docs/media/source/render_media.py images      # docs/media/images/*.png, *.webp
    python docs/media/source/render_media.py video       # docs/media/video/*.webm, *.vtt
    python docs/media/source/render_media.py storyboard  # docs/media/source/storyboard.md

Everything shown as terminal output comes from session/session.json, line for line.
Lines may be left out (an omission marker says how many); none are changed or added.
Colours are applied to the text, not to its content. Needs Playwright's Python package and
a Chromium build (any Playwright installation), Pillow, and Playwright's ffmpeg build for
the video (VP9 WebM, no audio).
"""
from __future__ import annotations

import glob
import html
import io
import json
import os
import re
import subprocess
import sys

from PIL import Image
from playwright.sync_api import sync_playwright

HERE = os.path.dirname(os.path.abspath(__file__))
MEDIA = os.path.dirname(HERE)
IMAGES = os.path.join(MEDIA, "images")
VIDEO = os.path.join(MEDIA, "video")
SESSION = json.load(io.open(os.path.join(HERE, "session", "session.json"), encoding="utf-8"))
sys.path.insert(0, HERE)
from svg_to_png import chromium  # noqa: E402

PROMPT = "notes-app $ "
FONT_MONO = '"Cascadia Mono", Consolas, "DejaVu Sans Mono", monospace'
FONT_UI = '"Segoe UI", "Helvetica Neue", Arial, sans-serif'
FONT_AR = 'Dubai, "Segoe UI", Tahoma, "Noto Sans Arabic", sans-serif'
RECORDED = "Real output · recorded %s · Claude Code %s · synthetic project" % (
    SESSION["recorded"], SESSION["claude"].split()[0])


# --------------------------------------------------------------------------- the session

def step(command, nth=0):
    """The nth recorded step whose command starts with `command` (shown form)."""
    found = [s for s in SESSION["steps"] if s["command"].startswith(command)]
    if len(found) <= nth:
        raise SystemExit("no recorded step %r (#%d)" % (command, nth))
    return found[nth]


def select(item, keep=None):
    """[(text, kind)] for a step: the prompt line, then its output. `keep` is None (all),
    a list of (start, end) line ranges, or a list of regular expressions; left-out lines
    become one marker line each run."""
    lines = [text for _, text in item["lines"]]
    if keep is None:
        chosen = list(range(len(lines)))
    elif keep and isinstance(keep[0], tuple):
        chosen = sorted({i for start, end in keep for i in range(start, min(end, len(lines)))})
    else:
        chosen = [i for i, text in enumerate(lines) if any(re.search(p, text) for p in keep)]
    out = [(item["command"], "prompt")]
    last = -1
    for i in chosen:
        if i > last + 1:
            out.append(("⋯ %d line%s not shown" % (i - last - 1, "s" if i - last - 1 > 1 else ""), "omit"))
        out.append((lines[i], "out"))
        last = i
    if last < len(lines) - 1:
        gap = len(lines) - 1 - last
        out.append(("⋯ %d line%s not shown" % (gap, "s" if gap > 1 else ""), "omit"))
    return out


COLOURS = [(r"\bDONE\b|\bdone\b|\bOK\b|\breported\b|Approved", "ok"),
           (r"\bFAILED\b|STALE|interrupted|killed", "bad"),
           (r"\bwaiting\b|approval|\bwaits\b|manual override|sonnet\*", "warn"),
           (r"PARALLEL|\bpid \d+|snapshot of HEAD", "info")]


def line_html(text, kind):
    if kind == "prompt":
        return '<span class="p">%s</span><span class="c">%s</span>' % (html.escape(PROMPT), html.escape(text))
    if kind == "omit":
        return '<span class="o">%s</span>' % html.escape(text)
    escaped = html.escape(text)
    for pattern, css in COLOURS:
        escaped = re.sub(pattern, lambda m: '<span class="%s">%s</span>' % (css, m.group(0)), escaped)
    return escaped


TERM_CSS = """
:root { --bg:#0d1117; --fg:#c9d1d9; }
* { box-sizing: border-box; }
body { margin:0; background:#f1f5f9; font-family:%(ui)s; }
.frame { padding:28px; width:%(width)dpx; }
.head { display:flex; justify-content:space-between; align-items:baseline; margin:0 4px 12px; }
.head h1 { font-size:24px; margin:0; color:#0f172a; }
.head span { font-size:14px; color:#64748b; }
.win { background:var(--bg); border-radius:12px; overflow:hidden; border:1px solid #1f2937; }
.bar { background:#161b22; color:#8b949e; font-size:13px; padding:8px 14px; border-bottom:1px solid #21262d; }
pre { margin:0; padding:16px 18px; font-family:%(mono)s; font-size:%(fs)spx; line-height:1.5;
      color:var(--fg); white-space:pre-wrap; word-break:break-word; }
.p { color:#7ee787; } .c { color:#f0f6fc; font-weight:600; }
.o { color:#8b949e; font-style:italic; }
.ok { color:#3fb950; } .bad { color:#f85149; } .warn { color:#d29922; } .info { color:#79c0ff; }
.foot { display:flex; justify-content:space-between; margin:10px 4px 0; font-size:13px; color:#64748b; }
"""


def terminal_page(title, blocks, width=1280, font_size=15):
    body = []
    for index, block in enumerate(blocks):
        if index:
            body.append("")
        body.extend(line_html(text, kind) for text, kind in block)
    return ("<html><head><meta charset='utf-8'><style>%s</style></head><body><div class='frame'>"
            "<div class='head'><h1>%s</h1><span>moa7amed-queue</span></div>"
            "<div class='win'><div class='bar'>notes-app — terminal</div><pre>%s</pre></div>"
            "<div class='foot'><span>%s</span><span>moa7amed.com</span></div></div></body></html>"
            % (TERM_CSS % {"ui": FONT_UI, "mono": FONT_MONO, "fs": font_size, "width": width},
               html.escape(title), "\n".join(body), html.escape(RECORDED)))


# --------------------------------------------------------------------------- images

def images_spec():
    return [
        {"name": "queue-and-run", "title": "Queue tasks, then run them one at a time",
         "blocks": lambda: [select(step("moa7amed-queue add", 0)), select(step("moa7amed-queue add", 1)),
                    select(step("moa7amed-queue add", 2)), select(step("moa7amed-queue depend")),
                    select(step("moa7amed-queue status", 0)), select(step("moa7amed-queue run", 0))]},
        {"name": "routing", "title": "Automatic model routing, with a manual override",
         "blocks": lambda: [select(step("moa7amed-queue model sonnet 3")),
                    select(step("moa7amed-queue route"), [r"^Task #", r"Model:", r"Recommended:",
                                                          r"Reason:", r"Complexity:", r"Override:"])]},
        {"name": "workflow", "title": "QUICK, STANDARD or DEEP, decided per task",
         "blocks": lambda: [select(step("moa7amed-queue workflow", 0))]},
        {"name": "agents", "title": "Read-only analysis agents, measured",
         "blocks": lambda: [select(step("moa7amed-queue agents evaluate")), select(step("moa7amed-queue agents show"))]},
        {"name": "approval", "title": "A material DEEP plan waits for your approval",
         "blocks": approval_blocks},
        {"name": "verification", "title": "Verified, committed, accounted for",
         "blocks": lambda: [select(step("moa7amed-queue show 3", 1), [r"^Task #3", r"state", r"result",
                                                              r"Model:", r"Actual:", r"Agents:",
                                                              r"Usage:"]),
                    select(step("git log")), select(step("python -m unittest")),
                    select(step("moa7amed-queue usage"))]},
        {"name": "recovery", "title": "A killed runner, recovered",
         "blocks": lambda: [select(step("moa7amed-queue add", 3)), select(killed_step()),
                    select(step("moa7amed-queue status", status_after_kill()), [(0, 4)]),
                    select(step("moa7amed-queue run", run_after_kill())),
                    select(step("moa7amed-queue history 4"))]},
        {"name": "bid-and-replica", "title": "BidStudio and Replica start from your documents",
         "blocks": lambda: [select(step("moa7amed-queue bid new")), select(step("moa7amed-queue bid doc")),
                    select(step("moa7amed-queue bid status")), select(step("moa7amed-queue replica new")),
                    select(step("moa7amed-queue replica ref")), select(step("moa7amed-queue replica status"))]},
    ]


def approval_blocks():
    return [select(step("moa7amed-queue run", 0), [r"task #3", r"approval", r"finished"]),
            select(step("moa7amed-queue status", 1), [r"^Tasks", r"^ +ID", r"^ +3 "]),
            select(step("moa7amed-queue workflow 3"), [r"^Task #3", r"Workflow:", r"Model:", r"Planning:"]),
            select(step("moa7amed-queue workflow approve")),
            select(step("moa7amed-queue run", 1), [r"task #3", r"finished"])]


def killed_step():
    return [s for s in SESSION["steps"] if s["killed"]][0]


def _index_after(command, after):
    later = [s for s in SESSION["steps"][SESSION["steps"].index(after) + 1:] if s["command"].startswith(command)]
    return [s for s in SESSION["steps"] if s["command"].startswith(command)].index(later[0])


def status_after_kill():
    return _index_after("moa7amed-queue status", killed_step())


def run_after_kill():
    return _index_after("moa7amed-queue run", killed_step())


ALT = {
    "banner": "moa7amed-queue by moa7amed: Queue. Route. Execute. Verify. A persistent task queue for "
              "Claude Code.",
    "lifecycle": "Diagram of how a queued task is executed: queue, route, claim, optional read-only "
                 "analysis, execute, verify, commit; deep material plans wait for approval; a crash is "
                 "recovered by the next runner.",
    "workflows": "Diagram comparing the QUICK, STANDARD and DEEP workflows.",
    "multi-agent": "Diagram of stage A multi-agent analysis: concurrent read-only agents on a snapshot, "
                   "integration by the queue, and the coordinator as the only writer.",
    "queue-and-run": "Terminal: three tasks are added and routed, task 3 is made to wait for task 2, "
                     "the status table lists them, and the runner executes them one after another.",
    "routing": "Terminal: task 3 is set to Sonnet by hand; the route report shows the automatic choice "
               "for tasks 1 and 2, and for task 3 the recommended Opus, the manual Sonnet and the DEEP "
               "workflow.",
    "workflow": "Terminal: the workflow report shows each task's profile, capabilities, planning and "
                "required verification.",
    "agents": "Terminal: the agent evaluation chooses two read-only agents for task 3; agents show "
              "lists their process ids, overlapping start and end times, snapshot isolation, input "
              "revision and reported cost.",
    "approval": "Terminal: task 3's plan waits for approval, the owner approves it, and the next run "
                "implements it.",
    "verification": "Terminal: task 3's record with its result, model and agents; the git log with one "
                    "commit per task; the project's tests passing; the usage report.",
    "recovery": "Terminal: a runner is killed while a task runs; status reports the stale lock; the next "
                "run recovers the interrupted task and finishes it; the history shows both attempts.",
    "bid-and-replica": "Terminal: a BidStudio project is created and a synthetic tender document "
                       "recorded; a Replica project is created from the team's own HTML prototype; "
                       "both report their status.",
}


def optimise(path):
    image = Image.open(path).convert("RGB")
    image.quantize(colors=256, method=Image.MEDIANCUT, dither=Image.NONE).save(path, optimize=True)


def render_images():
    with sync_playwright() as p:
        browser = p.chromium.launch(executable_path=chromium())
        for spec in images_spec():
            try:
                blocks = spec["blocks"]()
            except (SystemExit, IndexError, ValueError) as exc:
                print("%-22s skipped: %s" % (spec["name"], exc))
                continue
            page = browser.new_page(viewport={"width": 1336, "height": 800}, device_scale_factor=1.5)
            page.set_content(terminal_page(spec["title"], blocks))
            out = os.path.join(IMAGES, spec["name"] + ".png")
            page.locator(".frame").screenshot(path=out)
            page.close()
            optimise(out)
            size = Image.open(out).size
            print("%-22s %dx%d  png %d KB" % (spec["name"], size[0], size[1], os.path.getsize(out) // 1024))
        browser.close()
    with io.open(os.path.join(IMAGES, "ALT.json"), "w", encoding="utf-8") as handle:
        json.dump(ALT, handle, indent=1, ensure_ascii=False)


if __name__ == "__main__":
    action = sys.argv[1] if len(sys.argv) > 1 else "images"
    if action == "images":
        render_images()
    elif action in ("video", "storyboard", "thumbnail"):
        import render_video
        render_video.main(action)
    else:
        raise SystemExit(__doc__)
