#!/usr/bin/env python3
"""Check the demo media: every video plays to its end in Chromium with its captions
loaded, every image opens, and the documentation's relative links resolve.

    python docs/media/source/verify_media.py

Serves docs/media on 127.0.0.1 (a random free port) for the browser; nothing leaves the
machine."""
from __future__ import annotations

import functools
import glob
import http.server
import json
import os
import re
import sys
import threading

from PIL import Image
from playwright.sync_api import sync_playwright

HERE = os.path.dirname(os.path.abspath(__file__))
MEDIA = os.path.dirname(HERE)
REPO = os.path.dirname(os.path.dirname(MEDIA))
sys.path.insert(0, HERE)
from svg_to_png import chromium  # noqa: E402


class Quiet(http.server.SimpleHTTPRequestHandler):
    def log_message(self, *args):
        pass


def serve():
    handler = functools.partial(Quiet, directory=MEDIA)
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


PLAY = """async ([src, track, lang]) => {
  const v = document.createElement('video');
  v.muted = true; v.preload = 'auto'; v.src = src;
  const t = document.createElement('track');
  t.kind = 'captions'; t.srclang = lang; t.src = track; t.default = true;
  v.appendChild(t); document.body.appendChild(v);
  await new Promise((ok, bad) => { v.onloadedmetadata = ok; v.onerror = () => bad(String(v.error && v.error.message)); });
  await new Promise(ok => { if (t.readyState === 2) ok(); else t.onload = ok; setTimeout(ok, 5000); });
  const cues = Array.from(v.textTracks[0].cues || []);
  const info = {duration: v.duration, width: v.videoWidth, height: v.videoHeight, cues: cues.length,
                lastCueEnd: cues.length ? cues[cues.length - 1].endTime : null,
                firstCue: cues.length ? cues[0].text : null};
  v.playbackRate = 16;
  const started = performance.now();
  await v.play();
  await new Promise(ok => { v.onended = ok; setTimeout(ok, 60000); });
  info.ended = v.ended; info.currentTime = v.currentTime; info.playSeconds = (performance.now() - started) / 1000;
  return info;
}"""


def check_links(path):
    text = open(path, encoding="utf-8").read()
    problems = []
    for target in re.findall(r"\]\(([^)#\s]+)(?:#[^)]*)?\)", text):
        if re.match(r"^[a-z]+:", target):
            continue
        full = os.path.normpath(os.path.join(os.path.dirname(path), target))
        if not os.path.exists(full):
            problems.append(target)
    return problems


def main():
    results, failed = {"videos": {}, "images": {}, "links": {}}, False
    server = serve()
    base = "http://127.0.0.1:%d/" % server.server_address[1]
    with sync_playwright() as p:
        browser = p.chromium.launch(executable_path=chromium())
        page = browser.new_page()
        page.goto(base)
        for video in sorted(glob.glob(os.path.join(MEDIA, "video", "*.webm"))):
            name = os.path.basename(video)[:-5]
            lang = "ar" if name.endswith("-ar") else "en"
            track = "video/%s.%s.vtt" % (name, lang)
            info = page.evaluate(PLAY, [base + "video/" + name + ".webm", base + track, lang])
            info["bytes"] = os.path.getsize(video)
            ok = (info["ended"] and info["width"] == 1920 and info["height"] == 1080 and info["cues"] > 0
                  and abs(info["lastCueEnd"] - info["duration"]) < 0.5)
            failed |= not ok
            results["videos"][name] = dict(info, ok=ok)
            print("%-16s %s  %.1f s  %dx%d  %d cues (last ends %.1f s)  played to the end: %s  %.1f MB" % (
                name, "ok  " if ok else "FAIL", info["duration"], info["width"], info["height"], info["cues"],
                info["lastCueEnd"] or 0, info["ended"], info["bytes"] / 1e6))
        browser.close()
    server.shutdown()
    for image in sorted(glob.glob(os.path.join(MEDIA, "images", "*.png"))):
        with Image.open(image) as handle:
            handle.load()
            results["images"][os.path.basename(image)] = {"size": handle.size, "bytes": os.path.getsize(image)}
    for doc in ("README.md", "docs/demo.md", "docs/media/README.md"):
        full = os.path.join(REPO, doc)
        if os.path.exists(full):
            missing = check_links(full)
            failed |= bool(missing)
            results["links"][doc] = missing
            print("links %-22s %s" % (doc, "ok" if not missing else "MISSING %s" % missing))
    print("images: %d opened" % len(results["images"]))
    with open(os.path.join(HERE, "session", "verification.json"), "w", encoding="utf-8") as handle:
        json.dump(results, handle, indent=1)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
