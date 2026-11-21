#!/usr/bin/env python3
"""Render the SVG diagrams in docs/media/images to PNG with Chromium (Playwright).

    python docs/media/source/svg_to_png.py [--scale 1.5]
"""
from __future__ import annotations

import argparse
import glob
import os
import re

from playwright.sync_api import sync_playwright

def chromium():
    """The newest Chromium that a Playwright installation left on this machine."""
    roots = [os.environ.get("PLAYWRIGHT_BROWSERS_PATH") or "",
             os.path.join(os.environ.get("LOCALAPPDATA") or "", "ms-playwright"),
             os.path.expanduser("~/.cache/ms-playwright")]
    found = []
    for root in roots:
        for pattern in ("chromium-*/chrome-win*/chrome.exe", "chromium-*/chrome-linux*/chrome"):
            found += glob.glob(os.path.join(root, pattern))
    found.sort(key=lambda path: int(re.search(r"chromium-(\d+)", path).group(1)), reverse=True)
    return found[0] if found else None


IMAGES = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "images")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--scale", type=float, default=1.5)
    parser.add_argument("names", nargs="*")
    args = parser.parse_args()
    paths = [os.path.join(IMAGES, n) for n in args.names] or sorted(glob.glob(os.path.join(IMAGES, "*.svg")))
    with sync_playwright() as p:
        browser = p.chromium.launch(executable_path=chromium())
        for path in paths:
            with open(path, encoding="utf-8") as handle:
                svg = handle.read()
            width, height = (int(v) for v in re.search(r'width="(\d+)" height="(\d+)"', svg).groups())
            page = browser.new_page(viewport={"width": width, "height": height}, device_scale_factor=args.scale)
            page.set_content("<html><body style='margin:0'>%s</body></html>" % svg)
            out = path[:-4] + ".png"
            page.locator("svg").screenshot(path=out)
            page.close()
            print("%s  %dx%d  %d KB" % (os.path.basename(out), width * args.scale, height * args.scale,
                                        os.path.getsize(out) // 1024))
        browser.close()


if __name__ == "__main__":
    main()
