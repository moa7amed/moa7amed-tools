#!/usr/bin/env python3
"""Capture one page with Playwright (Chromium) for moa7amed-queue Replica.

Started by `moa7amed-queue replica capture` only, for a URL the user gave, with the
Python interpreter that has Playwright installed. It is the one part of the plugin that
opens a network connection, and only to that URL (and what the page itself loads).

    python replica_browser.py '<json request>'

Request: {"url", "out_png", "out_html", "width", "height", "full_page", "timeout_ms",
          "wait_ms", "color_scheme", "locale"}
Prints one JSON object: {"ok", "status", "final_url", "title", "login_required", "links",
                         "styles", "direction", "error"}

What it does not do: log in, fill or submit forms, accept cookies banners, solve
challenges, follow links, keep cookies or storage (a fresh browser context each time), or
record request headers. A page that asks for a login is reported as such.
"""
import json
import sys

STYLE_SCRIPT = """
() => {
  const props = ['color', 'background-color', 'border-color', 'font-family', 'font-size',
                 'font-weight', 'line-height', 'border-radius', 'box-shadow', 'margin-top',
                 'margin-bottom', 'padding-top', 'padding-bottom', 'padding-left',
                 'padding-right', 'gap'];
  const out = {};
  props.forEach(p => out[p] = {});
  const nodes = Array.from(document.querySelectorAll('body, body *')).slice(0, 4000);
  for (const node of nodes) {
    const rect = node.getBoundingClientRect();
    if (rect.width === 0 || rect.height === 0) continue;
    const style = getComputedStyle(node);
    if (style.visibility === 'hidden' || style.display === 'none') continue;
    for (const p of props) {
      const v = style.getPropertyValue(p);
      if (!v) continue;
      out[p][v] = (out[p][v] || 0) + 1;
    }
  }
  const links = Array.from(document.querySelectorAll('a[href]'))
    .map(a => a.href).filter(h => h.startsWith(location.origin)).slice(0, 200);
  return {styles: out, links: Array.from(new Set(links)),
          login: !!document.querySelector('input[type=password]'),
          direction: getComputedStyle(document.documentElement).direction,
          title: document.title};
}
"""


def _installed_chromium():
    """Chromium builds other Playwright installations left on this machine, newest first."""
    import glob
    import os
    roots = [os.environ.get("PLAYWRIGHT_BROWSERS_PATH") or "",
             os.path.join(os.environ.get("LOCALAPPDATA") or "", "ms-playwright"),
             os.path.expanduser("~/.cache/ms-playwright"),
             os.path.expanduser("~/Library/Caches/ms-playwright")]
    found = []
    for root in roots:
        if not root or not os.path.isdir(root):
            continue
        for pattern in ("chromium-*/chrome-win*/chrome.exe", "chromium-*/chrome-linux*/chrome",
                        "chromium-*/chrome-mac*/Chromium.app/Contents/MacOS/Chromium"):
            found += glob.glob(os.path.join(root, pattern))
    return sorted(found, reverse=True)


def launch(playwright):
    """Playwright's own Chromium; else an installed Chrome or Edge; else another Chromium
    build a Playwright installation left. Nothing is downloaded."""
    errors = []
    try:
        return playwright.chromium.launch(), "playwright chromium"
    except Exception as exc:  # noqa: BLE001
        errors.append(str(exc).splitlines()[0])
    for channel in ("chrome", "msedge"):
        try:
            return playwright.chromium.launch(channel=channel), channel
        except Exception as exc:  # noqa: BLE001
            errors.append(str(exc).splitlines()[0])
    for path in _installed_chromium():
        try:
            return playwright.chromium.launch(executable_path=path), path
        except Exception as exc:  # noqa: BLE001
            errors.append(str(exc).splitlines()[0])
    raise RuntimeError("no browser could be started: " + " | ".join(errors[:3]))


def main():
    request = json.loads(sys.argv[1])
    if request.get("pdf"):
        # print a local HTML file to PDF (BidStudio documents); nothing is fetched remotely
        job = request["pdf"]
        try:
            import pathlib
            from playwright.sync_api import sync_playwright
            with sync_playwright() as playwright:
                browser, which = launch(playwright)
                page = browser.new_page()
                page.route("**/*", lambda route: route.continue_()
                           if route.request.url.startswith("file:") else route.abort())
                page.goto(pathlib.Path(job["html"]).resolve().as_uri())
                page.pdf(path=job["out"], format="A4", print_background=True,
                         margin={"top": "18mm", "bottom": "18mm", "left": "16mm", "right": "16mm"},
                         display_header_footer=True, header_template="<span></span>",
                         footer_template="<div style='font-size:8px;width:100%;text-align:center'>"
                                         "<span class='pageNumber'></span> / "
                                         "<span class='totalPages'></span></div>")
                version = browser.version
                browser.close()
            print(json.dumps({"ok": True, "browser": "%s %s" % (which if len(which) < 40
                                                                 else "chromium", version)}))
            return 0
        except Exception as exc:  # noqa: BLE001
            print(json.dumps({"ok": False, "error": str(exc)[:300]}))
            return 1
    if request.get("probe"):
        try:
            from playwright.sync_api import sync_playwright
            with sync_playwright() as playwright:
                browser, which = launch(playwright)
                version = browser.version
                browser.close()
            print(json.dumps({"ok": True, "browser": "%s %s" % (which, version)}))
            return 0
        except Exception as exc:  # noqa: BLE001
            print(json.dumps({"ok": False, "error": str(exc)[:300]}))
            return 1
    result = {"ok": False, "status": None, "final_url": None, "title": None,
              "login_required": False, "links": [], "styles": {}, "direction": None,
              "error": None}
    try:
        from playwright.sync_api import sync_playwright
    except Exception as exc:  # noqa: BLE001
        result["error"] = "Playwright is not installed for this Python: %s" % exc
        print(json.dumps(result))
        return 3
    try:
        with sync_playwright() as playwright:
            browser, which = launch(playwright)
            try:
                context = browser.new_context(
                    viewport={"width": int(request.get("width", 1280)),
                              "height": int(request.get("height", 800))},
                    color_scheme=request.get("color_scheme") or "light",
                    locale=request.get("locale") or "en-US", accept_downloads=False,
                    java_script_enabled=True)
                page = context.new_page()
                response = page.goto(request["url"], wait_until="networkidle",
                                     timeout=int(request.get("timeout_ms", 30000)))
                if request.get("wait_ms"):
                    page.wait_for_timeout(int(request["wait_ms"]))
                result["status"] = response.status if response is not None else None
                result["final_url"] = page.url
                summary = page.evaluate(STYLE_SCRIPT)
                result.update(styles=summary["styles"], links=summary["links"],
                              login_required=summary["login"], direction=summary["direction"],
                              title=summary["title"])
                page.screenshot(path=request["out_png"], full_page=bool(request.get("full_page")))
                if request.get("out_html"):
                    with open(request["out_html"], "w", encoding="utf-8") as handle:
                        handle.write(page.content())
                result["browser"] = "%s %s" % (which if len(which) < 40 else "chromium",
                                               browser.version)
                context.close()
            finally:
                browser.close()
        result["ok"] = result["status"] is None or result["status"] < 400
        if not result["ok"]:
            result["error"] = "the page answered HTTP %s" % result["status"]
    except Exception as exc:  # noqa: BLE001 - reported to the caller
        result["error"] = str(exc).splitlines()[0][:300] if str(exc) else exc.__class__.__name__
    print(json.dumps(result))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
