#!/usr/bin/env python3
"""The Auto Queue image: real output of an interactive Claude Code session and of the CLI.

    python docs/media/source/autoqueue_media.py record <fixture folder> <session result.json>
    python docs/media/source/autoqueue_media.py render

`record` takes a finished run of the interactive Auto Queue check (a throwaway fixture
project and queue home, and the text of the Claude Code screen) and stores, in
session/autoqueue.json: the replies the capture hook showed in the session, and the output
of `moa7amed-queue auto`, `status` and `history` in that fixture afterwards. Local paths
and the host name are replaced (the replacement is listed in the file and on the image);
nothing else is changed. `render` draws docs/media/images/auto-queue.png from that file.
"""
from __future__ import annotations

import io
import json
import os
import re
import socket
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
OUT_JSON = os.path.join(HERE, "session", "autoqueue.json")
MQ = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(HERE))), "plugins",
                  "moa7amed-queue", "scripts", "mq.py")


def record(fixture, result_path):
    fixture = os.path.abspath(fixture)
    project, home = os.path.join(fixture, "project"), os.path.join(fixture, "queue-home")
    env = dict(os.environ, MOA7AMED_QUEUE_HOME=home, PYTHONIOENCODING="utf-8")
    replacements = [(project, "C:\\demo\\project"), (fixture, "C:\\demo"),
                    (socket.gethostname(), "demo-pc")]

    def clean(text):
        for old, new in replacements:
            text = text.replace(old, new)
        return text

    def run(*argv):
        out = subprocess.run([sys.executable, MQ, "--project", project] + list(argv), env=env,
                             stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        return [clean(line) for line in out.stdout.decode("utf-8", "replace").splitlines()]

    screen = json.load(io.open(result_path, encoding="utf-8"))["screen"]
    replies = []
    for match in re.finditer(r"(Task #\d saved.*?)\rOriginal prompt: (.*?)\r", screen, re.S):
        replies.append({"reply": clean(" ".join(match.group(1).replace("\r", " ").split())),
                        "original_prompt": clean(match.group(2).strip())})
    data = {"recorded": "2026-11-21", "claude": "2.1.329", "os": "Windows 11",
            "replaced": ["local fixture folder -> C:\\demo", "host name -> demo-pc"],
            "hook_replies": replies,
            "steps": [{"command": "moa7amed-queue auto", "lines": run("auto")},
                      {"command": "moa7amed-queue status", "lines": run("status")},
                      {"command": "moa7amed-queue history", "lines": run("history")}]}
    os.makedirs(os.path.dirname(OUT_JSON), exist_ok=True)
    with io.open(OUT_JSON, "w", encoding="utf-8", newline="\n") as handle:
        json.dump(data, handle, indent=1, ensure_ascii=False)
        handle.write("\n")
    print(OUT_JSON)


def render():
    import html
    from playwright.sync_api import sync_playwright
    sys.path.insert(0, HERE)
    from svg_to_png import chromium
    data = json.load(io.open(OUT_JSON, encoding="utf-8"))
    rows = ['<div class="who">Claude Code, interactive session: what the capture hook answered</div>']
    for item in data["hook_replies"]:
        shown = item["original_prompt"]
        shown = shown[len("queue: "):] if shown.startswith("queue: ") else shown
        cut = "" if shown.endswith(".") else ' <span class="o">⋯ (wrapped on screen; the rest '              'is not shown)</span>'
        rows.append('<div class="you">&gt; queue: %s%s</div><div class="hook">%s</div>'
                    % (html.escape(shown), cut, html.escape(item["reply"])))
    term = []
    for step in data["steps"]:
        if step["command"] == "moa7amed-queue history":
            chosen = [i for i, l in enumerate(step["lines"])
                      if re.search(r"#\d+ +(added|auto-launch|done)|runner-start|runner-stop", l)]
        elif step["command"] == "moa7amed-queue status":
            chosen = list(range(5))
        else:
            chosen = list(range(4))
        term.append('<span class="p">$ </span><span class="c">%s</span>' % html.escape(step["command"]))
        last = -1
        for i in chosen + [len(step["lines"])]:      # omissions are marked where they are
            if i > last + 1:
                term.append('<span class="o">⋯ %d line(s) not shown</span>' % (i - last - 1))
            if i < len(step["lines"]):
                term.append(html.escape(step["lines"][i]))
            last = i
        term.append("")
    page = """<html><head><meta charset='utf-8'><style>
body{margin:0;background:#f1f5f9;font-family:"Segoe UI",Arial,sans-serif}
.frame{padding:28px;width:1280px;box-sizing:border-box}
h1{font-size:24px;margin:0 4px 12px;color:#0f172a}
.box{background:#0d1117;border-radius:12px;border:1px solid #1f2937;padding:16px 18px;margin-bottom:14px}
.who{color:#8b949e;font-size:13px;margin-bottom:8px}
.you{font-family:Consolas,monospace;color:#f0f6fc;font-size:14px;margin-top:8px}
.hook{font-family:Consolas,monospace;color:#d29922;font-size:14px;margin:2px 0 4px 16px}
pre{margin:0;font-family:"Cascadia Mono",Consolas,monospace;font-size:14px;line-height:1.45;color:#c9d1d9;white-space:pre-wrap}
.p{color:#7ee787}.c{color:#f0f6fc;font-weight:600}.o{color:#8b949e;font-style:italic}
.foot{display:flex;justify-content:space-between;margin:4px;font-size:13px;color:#64748b}
</style></head><body><div class='frame'><h1>Auto Queue: submitted in the session, executed in the background</h1>
<div class='box'>%s</div><div class='box'><pre>%s</pre></div>
<div class='foot'><span>Real output · recorded %s · Claude Code %s · %s · synthetic fixture · %s</span><span>moa7amed.com</span></div>
</div></body></html>""" % ("".join(rows), "\n".join(term), data["recorded"], data["claude"], data["os"],
                           "; ".join(data["replaced"]))
    out = os.path.join(os.path.dirname(HERE), "images", "auto-queue.png")
    with sync_playwright() as p:
        browser = p.chromium.launch(executable_path=chromium())
        tab = browser.new_page(viewport={"width": 1336, "height": 900}, device_scale_factor=1.5)
        tab.set_content(page)
        tab.locator(".frame").screenshot(path=out)
        browser.close()
    from PIL import Image
    Image.open(out).convert("RGB").quantize(colors=256, method=Image.MEDIANCUT,
                                            dither=Image.NONE).save(out, optimize=True)
    print(out, os.path.getsize(out))


if __name__ == "__main__":
    if sys.argv[1:2] == ["record"]:
        record(sys.argv[2], sys.argv[3])
    elif sys.argv[1:2] == ["render"]:
        render()
    else:
        raise SystemExit(__doc__)
