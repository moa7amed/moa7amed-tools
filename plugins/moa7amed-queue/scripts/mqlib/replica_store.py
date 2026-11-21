"""Replica projects: storage and operations.

A replica project lives next to the queue it feeds, in additive tables of the same
database, and is linked to a project plan of the same id: architecture, infrastructure,
decisions, approval, task import, model routing, workflow and compliance are the planning
engine's and the queue's, not a second system. Reference files (screenshots, captures,
recordings, documents) are copied into the project's data directory, outside the
application being built and outside the plugin, and referred to by path; their content
is never put into a prompt wholesale.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import sys

from . import plan_store, replica_css, replica_image, util
from . import replica_model as model
from .util import QueueError

SOURCE_PREFIX = "replica:"
SCHEMA = (
    """CREATE TABLE IF NOT EXISTS replica_projects (
      id TEXT PRIMARY KEY, plan_id TEXT NOT NULL, title TEXT, request TEXT NOT NULL,
      intent TEXT, requested_mode TEXT, mode TEXT NOT NULL, quality TEXT NOT NULL,
      ownership TEXT NOT NULL, target_dir TEXT, document TEXT NOT NULL, revision INTEGER NOT NULL,
      created_at TEXT NOT NULL, updated_at TEXT NOT NULL)""",
    """CREATE TABLE IF NOT EXISTS replica_refs (
      project TEXT NOT NULL, id TEXT NOT NULL, kind TEXT NOT NULL, uri TEXT, path TEXT,
      sha256 TEXT, bytes INTEGER, status TEXT NOT NULL, limitation TEXT, provenance TEXT,
      permission TEXT, platform TEXT, meta TEXT NOT NULL DEFAULT '{}', captured_at TEXT NOT NULL,
      PRIMARY KEY (project, id))""",
    """CREATE TABLE IF NOT EXISTS replica_comparisons (
      id INTEGER PRIMARY KEY AUTOINCREMENT, project TEXT NOT NULL, screen TEXT NOT NULL,
      reference TEXT, candidate TEXT, diff TEXT, score REAL, method TEXT, metrics TEXT,
      iteration INTEGER, git_rev TEXT, created_at TEXT NOT NULL)""",
    """CREATE TABLE IF NOT EXISTS replica_tests (
      id INTEGER PRIMARY KEY AUTOINCREMENT, project TEXT NOT NULL, feature TEXT, flow TEXT,
      name TEXT, result TEXT NOT NULL, kind TEXT NOT NULL, evidence TEXT, source TEXT,
      git_rev TEXT, created_at TEXT NOT NULL)""",
    """CREATE TABLE IF NOT EXISTS replica_iterations (
      id INTEGER PRIMARY KEY AUTOINCREMENT, project TEXT NOT NULL, number INTEGER NOT NULL,
      status TEXT NOT NULL, started_at TEXT NOT NULL, finished_at TEXT, git_before TEXT,
      scores_before TEXT, scores_after TEXT, tasks TEXT NOT NULL DEFAULT '[]', stop_reason TEXT)""",
    """CREATE TABLE IF NOT EXISTS replica_log (
      id INTEGER PRIMARY KEY AUTOINCREMENT, project TEXT NOT NULL, kind TEXT NOT NULL,
      detail TEXT, at TEXT NOT NULL)""",
)
IMAGE_TYPES = {".png": "screenshot", ".jpg": "screenshot", ".jpeg": "screenshot",
               ".webp": "screenshot", ".gif": "screenshot"}
VIDEO_TYPES = (".mp4", ".webm", ".mov", ".mkv", ".avi", ".m4v")
DOC_TYPES = {".html": "html", ".htm": "html", ".css": "html", ".js": "html", ".md": "doc",
             ".txt": "doc", ".pdf": "doc", ".docx": "doc", ".sql": "schema", ".yaml": "api",
             ".yml": "api", ".json": "api", ".graphql": "api", ".fig": "design",
             ".sketch": "design", ".xd": "design"}
SECRET = re.compile(r"(?i)(password|passwd|token|secret|session|api[_-]?key|authorization)"
                    r"\s*[=:]\s*\S+")


def ensure_schema(q):
    for statement in SCHEMA:
        q.conn.execute(statement)


def _log(q, project, kind, detail):
    q.conn.execute("INSERT INTO replica_log(project, kind, detail, at) VALUES(?,?,?,?)",
                   (project, kind, detail, util.now_iso()))


def task_folder(q, project_id):
    """The replica folder of a project, when `project_id` is a replica project with files."""
    ensure_schema(q)
    if not q.conn.execute("SELECT 1 FROM replica_projects WHERE id=?", (project_id,)).fetchone():
        return None
    path = os.path.join(q.dir, "replica", project_id)
    return path if os.path.isdir(path) else None


def check_integrity(q, project_id):
    """References whose stored file no longer matches the SHA-256 recorded at intake."""
    changed = []
    for item in refs(q, project_id):
        path = item.get("path")
        if item.get("sha256") and path and os.path.isfile(path) and path.startswith(q.dir):
            if _sha256(path) != item["sha256"]:
                changed.append(item["id"])
    return changed


def data_dir(q, project, *parts):
    path = os.path.join(q.dir, "replica", project, *parts)
    os.makedirs(path, exist_ok=True)
    return path


def _sha256(path):
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for block in iter(lambda: handle.read(1 << 16), b""):
            digest.update(block)
    return digest.hexdigest()


def _redact(text):
    return SECRET.sub(lambda match: match.group(1) + "=<redacted>", text or "")


# --------------------------------------------------------------------------- projects

def create(q, project_id, request, title=None, mode=None, quality="prototype",
           ownership="unknown", target_dir=None):
    """A new replica project and its linked plan. The request is kept verbatim."""
    ensure_schema(q)
    if not plan_store.PLAN_ID.match(project_id or ""):
        raise QueueError("a replica id is lowercase letters, digits, '.', '_' or '-' (up to 64)")
    if not (request or "").strip():
        raise QueueError("a replica project needs the request it answers")
    if quality not in model.QUALITY:
        raise QueueError("quality is one of %s" % ", ".join(model.QUALITY))
    if ownership not in model.OWNERSHIP:
        raise QueueError("ownership is one of %s" % ", ".join(model.OWNERSHIP))
    detected = model.intent(request)
    requested = mode or detected["mode"] or model.choose_mode(request)
    if requested not in model.MODES:
        raise QueueError("mode is one of %s" % ", ".join(model.MODES))
    if q.conn.execute("SELECT 1 FROM replica_projects WHERE id=?", (project_id,)).fetchone():
        raise QueueError("replica project '%s' already exists" % project_id)
    plan_exists = False
    try:
        plan_store.load(q, project_id)
        plan_exists = True
    except QueueError:
        pass
    if not plan_exists:
        plan_store.create(q, project_id, title or "Replica %s" % project_id, "new",
                          summary="Reconstruction requested: %s" % request.strip()[:300])
    stamp = util.now_iso()
    with q.tx():
        q.conn.execute(
            "INSERT INTO replica_projects(id, plan_id, title, request, intent, requested_mode, mode, "
            "quality, ownership, target_dir, document, revision, created_at, updated_at) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?,1,?,?)",
            (project_id, project_id, title or "Replica %s" % project_id, request, detected["intent"],
             requested, requested, quality, ownership, target_dir or q.root,
             json.dumps(model.new_document()), stamp, stamp))
        _log(q, project_id, "created", "%s, %s, ownership %s" % (requested, quality, ownership))
    for url in detected["urls"]:
        add_ref(q, project_id, "url", url, provenance="named in the request")
    return get(q, project_id)


def next_id(q, prefix="r"):
    ensure_schema(q)
    number = 1
    while q.conn.execute("SELECT 1 FROM replica_projects WHERE id=?",
                         ("%s%d" % (prefix, number),)).fetchone():
        number += 1
    return "%s%d" % (prefix, number)


def get(q, project_id):
    ensure_schema(q)
    row = q.conn.execute("SELECT * FROM replica_projects WHERE id=?", (project_id,)).fetchone()
    if row is None:
        raise QueueError("no replica project '%s' (moa7amed-queue replica list)" % project_id)
    project = dict(row)
    project["document"] = json.loads(row["document"])
    return project


def projects(q):
    ensure_schema(q)
    return [dict(row) for row in q.conn.execute(
        "SELECT id, title, mode, quality, ownership, created_at, updated_at FROM replica_projects "
        "ORDER BY created_at")]


def save_document(q, project_id, document, note):
    with q.tx():
        q.conn.execute("UPDATE replica_projects SET document=?, revision=revision+1, updated_at=? "
                       "WHERE id=?", (json.dumps(document, ensure_ascii=False), util.now_iso(),
                                      project_id))
        _log(q, project_id, "inventory", note)


def configure(q, project_id, **values):
    """mode, quality, ownership, target_dir; candidate settings go into the document."""
    project = get(q, project_id)
    updates = {}
    for key in ("quality", "ownership", "target_dir", "title"):
        if values.get(key) is not None:
            updates[key] = values[key]
    if values.get("mode"):
        if values["mode"] not in model.MODES:
            raise QueueError("mode is one of %s" % ", ".join(model.MODES))
        updates["requested_mode"] = values["mode"]
    if updates.get("quality") and updates["quality"] not in model.QUALITY:
        raise QueueError("quality is one of %s" % ", ".join(model.QUALITY))
    if updates.get("ownership") and updates["ownership"] not in model.OWNERSHIP:
        raise QueueError("ownership is one of %s" % ", ".join(model.OWNERSHIP))
    candidate = {key: values[key] for key in ("url", "serve", "pages", "viewport", "masks")
                 if values.get(key) is not None}
    with q.tx():
        for key, value in updates.items():
            q.conn.execute("UPDATE replica_projects SET %s=?, updated_at=? WHERE id=?" % key,
                           (value, util.now_iso(), project_id))
        if candidate:
            document = project["document"]
            document.setdefault("candidate", {}).update(candidate)
            q.conn.execute("UPDATE replica_projects SET document=? WHERE id=?",
                           (json.dumps(document, ensure_ascii=False), project_id))
        _log(q, project_id, "configured", json.dumps(dict(updates, **candidate))[:300])
    return refresh_mode(q, project_id)


def refresh_mode(q, project_id):
    """The effective mode follows the evidence: full without the evidence runs functional."""
    project = get(q, project_id)
    mode, note = model.effective_mode(project["requested_mode"], refs(q, project_id))
    if mode != project["mode"]:
        with q.tx():
            q.conn.execute("UPDATE replica_projects SET mode=? WHERE id=?", (mode, project_id))
            _log(q, project_id, "mode", note or "mode %s" % mode)
    project = get(q, project_id)
    project["mode_note"] = note
    return project


# --------------------------------------------------------------------------- references

def refs(q, project_id):
    ensure_schema(q)
    out = []
    for row in q.conn.execute("SELECT * FROM replica_refs WHERE project=? ORDER BY "
                              "CAST(substr(id, 2) AS INTEGER)", (project_id,)):
        item = dict(row)
        item["meta"] = json.loads(row["meta"] or "{}")
        out.append(item)
    return out


def _next_ref_id(q, project_id):
    numbers = [int(row["id"][1:]) for row in q.conn.execute(
        "SELECT id FROM replica_refs WHERE project=?", (project_id,)) if row["id"][1:].isdigit()]
    return "E%d" % (max(numbers) + 1 if numbers else 1)


def _image_size(path):
    try:
        image, scale = replica_image.load(path, max_side=10 ** 6)
        return [image.width, image.height]
    except Exception:  # noqa: BLE001 - not every format is readable without Pillow
        with open(path, "rb") as handle:
            head = handle.read(32)
        if head[:8] == b"\x89PNG\r\n\x1a\n":
            import struct
            return list(struct.unpack(">II", head[16:24]))
        return None


def add_ref(q, project_id, kind=None, source=None, provenance=None, permission=None,
            platform=None, note=None):
    """Record a reference. Files are copied into the project's data directory; folders
    (source code) are fingerprinted where they are; URLs are recorded and captured only on
    request. A reference that cannot be read is recorded as unavailable, with the reason."""
    get(q, project_id)
    source = (source or "").strip()
    if not source:
        raise QueueError("a reference needs a URL, file or folder")
    meta, path, sha, size, status, limitation = {}, None, None, None, "available", None
    if re.match(r"^https?://", source, re.I):
        kind = kind or "url"
        status = "not captured"
        if re.search(r"(?i)[?&](token|session|key|password|auth)[^=]*=", source):
            raise QueueError("the URL carries what looks like a credential; give the page "
                             "address without it")
    elif os.path.isdir(source):
        kind = kind or "source"
        from . import plan_discover
        meta["fingerprint"] = plan_discover.fingerprint(source)
        try:
            found = plan_discover.discover(source)
            meta["discovered"] = {"components": found.get("existing_components") or [],
                                  "summary": found.get("summary") or {},
                                  "facts": [item.get("statement") for item in
                                            (found.get("facts") or [])[:20]]}
        except Exception as exc:  # noqa: BLE001 - discovery is a help, not a requirement
            meta["discovered_error"] = str(exc)[:200]
        meta["files"] = sum(len(files) for _, _, files in os.walk(source))
        path = os.path.abspath(source)
    elif os.path.isfile(source):
        extension = os.path.splitext(source)[1].lower()
        if kind is None:
            kind = IMAGE_TYPES.get(extension) or ("video" if extension in VIDEO_TYPES
                                                  else DOC_TYPES.get(extension, "doc"))
        sha = _sha256(source)
        size = os.path.getsize(source)
        target = os.path.join(data_dir(q, project_id, "refs"), sha[:16] + extension)
        if not os.path.exists(target):
            shutil.copyfile(source, target)
        path = target
        meta["original_name"] = os.path.basename(source)
        if kind == "screenshot":
            meta["size"] = _image_size(target)
        if kind == "video":
            meta.update(video_metadata(target))
            limitation = meta.get("limitation")
    else:
        kind = kind or "doc"
        status, limitation = "unavailable", "not found on this machine: %s" % source
    if kind not in model.REF_KINDS:
        raise QueueError("kind is one of %s" % ", ".join(model.REF_KINDS))
    if platform:
        meta["platform"] = platform
        if platform in ("android", "ios"):
            limitation = (limitation + "; " if limitation else "") + (
                "mobile reference: screens are evidence of layout and visible behaviour only; "
                "device automation is %s" % ("available" if tools().get(
                    "adb" if platform == "android" else "xcrun") else "not installed"))
    if note:
        meta["note"] = _redact(note)
    with q.tx():
        if sha and q.conn.execute("SELECT 1 FROM replica_refs WHERE project=? AND sha256=?",
                                  (project_id, sha)).fetchone():
            row = q.conn.execute("SELECT id FROM replica_refs WHERE project=? AND sha256=?",
                                 (project_id, sha)).fetchone()
            return dict(ref(q, project_id, row["id"]), duplicate=True)
        if kind == "url" and q.conn.execute("SELECT 1 FROM replica_refs WHERE project=? AND "
                                            "uri=? AND kind='url'", (project_id, source)).fetchone():
            row = q.conn.execute("SELECT id FROM replica_refs WHERE project=? AND uri=? AND "
                                 "kind='url'", (project_id, source)).fetchone()
            return dict(ref(q, project_id, row["id"]), duplicate=True)
        ref_id = _next_ref_id(q, project_id)
        q.conn.execute(
            "INSERT INTO replica_refs(project, id, kind, uri, path, sha256, bytes, status, "
            "limitation, provenance, permission, platform, meta, captured_at) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (project_id, ref_id, kind, source if kind == "url" else None, path, sha, size, status,
             limitation, provenance or "supplied by the user", permission, platform,
             json.dumps(meta, ensure_ascii=False), util.now_iso()))
        _log(q, project_id, "reference", "%s %s %s" % (ref_id, kind, status))
    if kind in model.DOCUMENT_KINDS:
        refresh_mode(q, project_id)
    return ref(q, project_id, ref_id)


def ref(q, project_id, ref_id):
    for item in refs(q, project_id):
        if item["id"] == ref_id:
            return item
    raise QueueError("no reference %s in %s" % (ref_id, project_id))


def video_metadata(path):
    """Duration and frame size with ffprobe when it is installed; otherwise only what the
    file itself says (its size) and the limitation."""
    probe = shutil.which("ffprobe")
    if not probe:
        return {"limitation": "ffprobe is not installed: duration, size and frames of the "
                              "recording are unknown; describe the screens it shows, or add "
                              "screenshots of them"}
    try:
        result = subprocess.run([probe, "-v", "error", "-show_entries",
                                 "format=duration:stream=width,height", "-of", "json", path],
                                stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=60,
                                **util.no_window_kwargs())
        data = json.loads(result.stdout.decode("utf-8", "replace") or "{}")
        streams = [item for item in data.get("streams") or [] if item.get("width")]
        return {"duration_s": float((data.get("format") or {}).get("duration") or 0) or None,
                "size": [streams[0]["width"], streams[0]["height"]] if streams else None}
    except Exception as exc:  # noqa: BLE001
        return {"limitation": "ffprobe could not read the recording: %s" % str(exc)[:120]}


def mark_unavailable(q, project_id, ref_id, reason):
    with q.tx():
        q.conn.execute("UPDATE replica_refs SET status='unavailable', limitation=? WHERE "
                       "project=? AND id=?", (_redact(reason)[:500], project_id, ref_id))
        _log(q, project_id, "reference", "%s unavailable: %s" % (ref_id, reason[:200]))


# --------------------------------------------------------------------------- tools

def tools(config=None, probe=False):
    """What this machine can do for Replica. Nothing is installed or configured here."""
    config = config or {}
    python = config.get("replica_python") or sys.executable
    found = {"python": python}
    try:
        check = subprocess.run([python, "-c", "import playwright.sync_api"],
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=60,
                               **util.no_window_kwargs())
        found["playwright"] = check.returncode == 0
    except Exception:  # noqa: BLE001
        found["playwright"] = False
    found["pillow"] = replica_image._pillow() is not None
    for name in ("ffprobe", "adb", "xcrun", "node"):
        found[name] = bool(shutil.which(name))
    found["capture"] = found["playwright"]
    if probe and found["playwright"]:
        script = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                              "replica_browser.py")
        try:
            result = subprocess.run([python, "-B", script, json.dumps({"probe": True})],
                                    stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                    timeout=120, **util.no_window_kwargs())
            data = json.loads(result.stdout.decode("utf-8", "replace").strip().splitlines()[-1])
        except Exception as exc:  # noqa: BLE001
            data = {"ok": False, "error": str(exc)[:200]}
        found["capture"] = bool(data.get("ok"))
        found["browser"] = data.get("browser") or data.get("error")
    return found


# --------------------------------------------------------------------------- capture

def capture(q, project_id, url=None, ref_id=None, width=1280, height=800, full_page=False,
            screen=None, config=None, candidate=False):
    """Capture one page with Playwright: a screenshot, the page source and a summary of its
    computed styles. Reference captures become references; candidate captures (the
    rebuild) are stored for comparison only. Login pages, errors and missing tools are
    recorded as limitations, never worked around."""
    config = config or util.effective_config(q.dir)
    get(q, project_id)
    if ref_id and not url:
        url = ref(q, project_id, ref_id)["uri"]
    if not url or not re.match(r"^(https?|file)://", url, re.I):
        raise QueueError("capture needs an http(s) or file URL")
    if not candidate and ref_id is None:
        known = [item for item in refs(q, project_id) if item["kind"] == "url" and item["uri"] == url]
        ref_id = known[0]["id"] if known else add_ref(q, project_id, "url", url,
                                                      provenance="captured on request")["id"]
    available = tools(config)
    if not available["playwright"]:
        reason = ("no browser automation: Playwright is not installed for %s. Install it as "
                  "described at https://playwright.dev/python/docs/intro, set replica_python to "
                  "an interpreter that has it, or add screenshots by hand" % available["python"])
        if not candidate:
            with q.tx():
                q.conn.execute("UPDATE replica_refs SET limitation=? WHERE project=? AND id=?",
                               (reason, project_id, ref_id))
        return {"ok": False, "error": reason, "ref": ref_id}
    folder = data_dir(q, project_id, "candidates" if candidate else "refs")
    stamp = util.now_iso().replace(":", "").replace("-", "")[:15]
    base = os.path.join(folder, "%s-%s-%dx%d" % (screen or "page", stamp, width, height))
    request = {"url": url, "out_png": base + ".png", "out_html": base + ".html", "width": width,
               "height": height, "full_page": bool(full_page),
               "timeout_ms": int(config.get("replica_capture_timeout_s", 45)) * 1000,
               "wait_ms": int(config.get("replica_capture_wait_ms", 500))}
    script = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                          "replica_browser.py")
    try:
        result = subprocess.run([available["python"], "-B", script, json.dumps(request)],
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                timeout=int(config.get("replica_capture_timeout_s", 45)) + 60,
                                **util.no_window_kwargs())
        data = json.loads(result.stdout.decode("utf-8", "replace").strip().splitlines()[-1])
    except Exception as exc:  # noqa: BLE001
        data = {"ok": False, "error": "the capture did not complete: %s" % str(exc)[:200]}
    out = {"ok": bool(data.get("ok")), "url": url, "status": data.get("status"),
           "title": data.get("title"), "login_required": bool(data.get("login_required")),
           "error": data.get("error"), "screenshot": None, "html": None, "ref": ref_id}
    if candidate:
        out.update(screenshot=request["out_png"] if os.path.exists(request["out_png"]) else None,
                   html=request["out_html"] if os.path.exists(request["out_html"]) else None)
        return out
    limitation = None
    if not data.get("ok"):
        limitation = data.get("error") or "the page could not be captured"
    elif data.get("login_required"):
        limitation = ("the page asks for a login: only what is visible without signing in was "
                      "captured. Add authorised screenshots of the screens behind it; the queue "
                      "does not sign in")
    with q.tx():
        q.conn.execute("UPDATE replica_refs SET status=?, limitation=?, captured_at=?, meta=? "
                       "WHERE project=? AND id=?",
                       ("unavailable" if not data.get("ok") else "captured", limitation,
                        util.now_iso(), json.dumps({"final_url": data.get("final_url"),
                                                    "status": data.get("status"),
                                                    "title": data.get("title"),
                                                    "links": (data.get("links") or [])[:50],
                                                    "direction": data.get("direction")}),
                        project_id, ref_id))
    if not data.get("ok") or not os.path.exists(request["out_png"]):
        return out
    previous = [item for item in refs(q, project_id) if item["kind"] == "html"
                and item["meta"].get("parent") == ref_id
                and item["meta"].get("viewport") == [width, height]]
    shot = _store_capture(q, project_id, request["out_png"], "screenshot", url, ref_id,
                          [width, height], data, screen)
    page = _store_capture(q, project_id, request["out_html"], "html", url, ref_id,
                          [width, height], data, screen) if os.path.exists(request["out_html"]) else None
    out.update(screenshot=shot["id"], html=page["id"] if page else None)
    if previous and page and previous[-1]["sha256"] != page["sha256"]:
        out["changed_since"] = previous[-1]["captured_at"]
        with q.tx():
            _log(q, project_id, "changed", "%s changed since %s" % (url, previous[-1]["captured_at"]))
    return out


def _store_capture(q, project_id, path, kind, url, parent, viewport, data, screen):
    sha = _sha256(path)
    meta = {"parent": parent, "viewport": viewport, "method": "playwright %s" % (
        data.get("browser") or "chromium"), "title": data.get("title"),
            "direction": data.get("direction")}
    if kind == "screenshot":
        meta["size"] = _image_size(path)
    if kind == "html":
        meta["styles"] = data.get("styles") or {}
    if screen:
        meta["screen"] = screen
    with q.tx():
        ref_id = _next_ref_id(q, project_id)
        q.conn.execute(
            "INSERT INTO replica_refs(project, id, kind, uri, path, sha256, bytes, status, "
            "limitation, provenance, permission, platform, meta, captured_at) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (project_id, ref_id, kind, url, path, sha, os.path.getsize(path), "available", None,
             "browser capture of %s" % url, None, None, json.dumps(meta), util.now_iso()))
    return ref(q, project_id, ref_id)


# --------------------------------------------------------------------------- inventory

SECTIONS = ("screens", "features", "flows", "unknowns", "notes")


def apply_inventory(q, project_id, fragment):
    """Merge screens, features, flows, unknowns (by id) and design tokens into the
    document; return the validation findings. A `remove` key lists ids to drop."""
    if not isinstance(fragment, dict):
        raise QueueError("the inventory must be a JSON object")
    project = get(q, project_id)
    document = project["document"]
    for key, value in fragment.items():
        if key in SECTIONS and isinstance(value, list):
            current = document.get(key) or []
            index = {item.get("id"): position for position, item in enumerate(current)
                     if isinstance(item, dict) and item.get("id")}
            for item in value:
                if isinstance(item, dict) and item.get("id") in index:
                    merged = dict(current[index[item["id"]]])
                    merged.update(item)
                    current[index[item["id"]]] = merged
                elif isinstance(item, dict) and item.get("id"):
                    index[item["id"]] = len(current)
                    current.append(item)
                elif item not in current:
                    current.append(item)
            document[key] = current
        elif key == "design" and isinstance(value, dict):
            design = document.setdefault("design", {"tokens": []})
            for sub, subvalue in value.items():
                if sub == "tokens" and isinstance(subvalue, list):
                    named = {item.get("name"): item for item in design.get("tokens") or []}
                    for token in subvalue:
                        named[token.get("name")] = dict(token, source=token.get("source", "manual"))
                    design["tokens"] = list(named.values())
                else:
                    design[sub] = subvalue
        elif key == "sample_data" and isinstance(value, dict):
            document["sample_data"] = value
        elif key == "acceptance" and isinstance(value, dict):
            document.setdefault("acceptance", {}).update(value)
        elif key not in ("remove", "id"):
            raise QueueError("unknown inventory section '%s' (screens, features, flows, unknowns, "
                             "notes, design, acceptance, sample_data)" % key)
    for section, ids in (fragment.get("remove") or {}).items():
        if section in SECTIONS:
            document[section] = [item for item in document.get(section) or []
                                 if not (isinstance(item, dict) and item.get("id") in ids)]
    findings = model.validate(document, refs(q, project_id), project["mode"])
    save_document(q, project_id, document, "inventory updated: %d screens, %d features, %d flows"
                  % (len(document["screens"]), len(document["features"]), len(document["flows"])))
    return findings


def extract_tokens(q, project_id):
    """Design tokens from the evidence: page sources and computed styles of captures,
    supplied CSS/HTML files, and the colours of screenshots. Replaces earlier automatic
    tokens; tokens written by hand are kept."""
    project = get(q, project_id)
    references = refs(q, project_id)
    classes = {item["id"]: ("OBSERVED" if item["kind"] in model.VISUAL_KINDS
                            or item["meta"].get("parent") else "DOCUMENTED")
               for item in references}
    counter = {}
    for item in references:
        if item["status"] == "unavailable" or not item.get("path"):
            continue
        if item["kind"] == "html" and os.path.isfile(item["path"]):
            with open(item["path"], encoding="utf-8", errors="replace") as handle:
                replica_css.from_css(handle.read(), item["id"], counter)
            replica_css.from_computed(item["meta"].get("styles"), item["id"], counter)
    found = replica_css.tokens(counter, lambda source: classes.get(source, "INFERRED"))
    palettes = []
    for item in references:
        if item["kind"] == "screenshot" and item.get("path") and os.path.isfile(item["path"]):
            try:
                palettes.append({"ref": item["id"], "colors": replica_image.palette(item["path"])})
            except Exception as exc:  # noqa: BLE001 - an unreadable image is a limitation
                palettes.append({"ref": item["id"], "error": str(exc)[:150]})
    document = project["document"]
    design = document.setdefault("design", {"tokens": []})
    manual = [token for token in design.get("tokens") or [] if token.get("source") != "auto"]
    design["tokens"] = manual + [dict(token, source="auto") for token in found]
    design["palettes"] = palettes
    save_document(q, project_id, document, "design tokens extracted: %d" % len(found))
    return {"tokens": design["tokens"], "palettes": palettes}


# --------------------------------------------------------------------------- planning

def make_plan(q, project_id):
    """Write the reconstruction tasks into the linked plan (a draft: the plan's own checks,
    architecture decisions and approval apply)."""
    project = refresh_mode(q, project_id)
    document = project["document"]
    references = refs(q, project_id)
    errors = [item for item in model.validate(document, references, project["mode"])
              if item["level"] == "error"]
    if errors:
        raise QueueError("the inventory has errors; fix them first:\n" + "\n".join(
            "- %s: %s" % (item["where"], item["message"]) for item in errors[:12]))
    if not document.get("screens"):
        raise QueueError("no screens in the inventory yet (replica-recon)")
    fragment = model.plan_fragment(project, document, references, project["mode"],
                                   project["quality"])
    plan = plan_store.load(q, project["plan_id"])
    old = [task["id"] for task in plan.get("tasks") or [] if "replica" in task]
    keep = {task["id"] for task in fragment["tasks"]}
    plan_store.apply(q, project["plan_id"], {
        "milestones": fragment["milestones"], "requirements": fragment["requirements"],
        "tasks": fragment["tasks"],
        "remove": {"tasks": [task_id for task_id in old if task_id not in keep]}},
        note="replica %s: %d tasks from the inventory" % (project_id, len(fragment["tasks"])))
    document["screen_tasks"] = fragment["screen_tasks"]
    save_document(q, project_id, document, "plan generated")
    return {"plan": project["plan_id"], "tasks": fragment["tasks"],
            "milestones": fragment["milestones"], "requirements": fragment["requirements"]}


def queue_tasks(q, project_id):
    """{feature or screen id: [queue task rows]} for the tasks imported from the plan."""
    project = get(q, project_id)
    number = plan_store.execution_revision(q, project["plan_id"])
    if number is None:
        return {}, {}
    plan = plan_store.revision(q, project["plan_id"], number)
    links = plan_store.links(q, project["plan_id"])
    by_feature, by_screen = {}, {}
    for task in plan.get("tasks") or []:
        link = links.get(task["id"])
        if not link:
            continue
        try:
            row = q.get(link["queue_task_id"])
        except QueueError:
            continue
        info = {"id": row["id"], "status": row["status"], "plan_task": task["id"]}
        for feature in (task.get("replica") or {}).get("features") or []:
            by_feature.setdefault(feature, []).append(info)
        for screen in (task.get("replica") or {}).get("screens") or []:
            by_screen.setdefault(screen, []).append(info)
    return by_feature, by_screen


# --------------------------------------------------------------------------- comparison

def _git_rev(path):
    from . import gitops
    try:
        return gitops.head(path) if path and gitops.is_repo(path) else None
    except Exception:  # noqa: BLE001
        return None


def _apply_masks(path, masks, out_path):
    """Paint masked regions grey so moving content does not count."""
    image, _ = replica_image.load(path, max_side=10 ** 6)
    for box in masks or []:
        x, y, w, h = [int(value) for value in box]
        for row in range(max(0, y), min(image.height, y + h)):
            for column in range(max(0, x), min(image.width, x + w)):
                image.pixels[row][column] = (128, 128, 128)
    replica_image.write_png(out_path, image)
    return out_path


def compare(q, project_id, screen, candidate, reference=None, config=None, masks=None):
    """Compare the rebuild's screenshot (a file, or a URL to capture) with the reference
    screenshot of a screen, and record the measurement."""
    config = config or util.effective_config(q.dir)
    project = get(q, project_id)
    document = project["document"]
    screens = model.by_id(document.get("screens"))
    if screen not in screens:
        raise QueueError("no screen %s in the inventory" % screen)
    references = {item["id"]: item for item in refs(q, project_id)}
    if reference is None:
        shots = [ref_id for ref_id in screens[screen].get("refs") or []
                 if ref_id in references and references[ref_id]["kind"] == "screenshot"
                 and references[ref_id]["status"] != "unavailable"]
        if not shots:
            raise QueueError("screen %s has no reference screenshot (refs); add one or name it "
                             "with --reference" % screen)
        reference = shots[-1]
    if reference not in references or not references[reference].get("path"):
        raise QueueError("reference %s is not a screenshot of this project" % reference)
    reference_path = references[reference]["path"]
    size = references[reference]["meta"].get("size") or [1280, 800]
    if re.match(r"^(https?|file)://", candidate or "", re.I):
        shot = capture(q, project_id, url=candidate, width=size[0], height=min(size[1], 4000),
                       full_page=size[1] > 1000, screen=screen, config=config, candidate=True)
        if not shot["ok"] or not shot["screenshot"]:
            raise QueueError("the rebuild could not be captured: %s" % shot.get("error"))
        candidate = shot["screenshot"]
    if not candidate or not os.path.isfile(candidate):
        raise QueueError("candidate screenshot not found: %s" % candidate)
    folder = data_dir(q, project_id, "comparisons")
    stamp = util.now_iso().replace(":", "").replace("-", "")[:15]
    if masks is None:
        configured = (document.get("candidate") or {}).get("masks")
        masks = configured.get(screen) if isinstance(configured, dict) else None
    left, right = reference_path, candidate
    if masks:
        left = _apply_masks(reference_path, masks, os.path.join(folder, "%s-%s-ref-masked.png"
                                                                % (screen, stamp)))
        right = _apply_masks(candidate, masks, os.path.join(folder, "%s-%s-cand-masked.png"
                                                            % (screen, stamp)))
    diff = os.path.join(folder, "%s-%s-diff.png" % (screen, stamp))
    result = replica_image.compare(left, right, diff,
                                   tolerance=int(config.get("replica_pixel_tolerance", 24)),
                                   shift=min(1, max(0, int(config.get("replica_pixel_shift", 1)))))
    if masks:
        result["notes"].append("%d masked region(s) excluded" % len(masks))
    stored = os.path.join(folder, "%s-%s-candidate%s" % (screen, stamp,
                                                         os.path.splitext(candidate)[1] or ".png"))
    if os.path.abspath(candidate) != os.path.abspath(stored):
        shutil.copyfile(candidate, stored)
    iteration = q.conn.execute("SELECT MAX(number) AS n FROM replica_iterations WHERE project=? "
                               "AND status='open'", (project_id,)).fetchone()["n"]
    with q.tx():
        cursor = q.conn.execute(
            "INSERT INTO replica_comparisons(project, screen, reference, candidate, diff, score, "
            "method, metrics, iteration, git_rev, created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
            (project_id, screen, reference, stored, diff, result["score"], result["method"],
             json.dumps({key: result.get(key) for key in (
                 "whole_image_score", "differing_pixels", "content_pixels", "compared_pixels",
                 "union_pixels", "background", "reference_size", "candidate_size", "regions",
                 "notes", "reader")}),
             iteration, _git_rev(project["target_dir"]), util.now_iso()))
        _log(q, project_id, "compared", "%s: %.4f" % (screen, result["score"]))
    result.update(id=cursor.lastrowid, screen=screen, reference=reference, candidate=stored,
                  findings=replica_image.describe_regions(result))
    return result


def comparisons(q, project_id, latest_only=True):
    ensure_schema(q)
    rows = [dict(row) for row in q.conn.execute(
        "SELECT * FROM replica_comparisons WHERE project=? ORDER BY id", (project_id,))]
    for row in rows:
        row["metrics"] = json.loads(row["metrics"] or "{}")
    if not latest_only:
        return rows
    latest = {}
    for row in rows:
        latest[row["screen"]] = row
    return [latest[key] for key in sorted(latest)]


# --------------------------------------------------------------------------- functional tests

def record_test(q, project_id, result, kind, feature=None, flow=None, name=None, evidence=None,
                source="manual"):
    project = get(q, project_id)
    if result not in ("pass", "fail", "skip"):
        raise QueueError("result is pass, fail or skip")
    if kind not in ("real", "mock"):
        raise QueueError("kind is real (against the working application) or mock")
    document = project["document"]
    if feature and feature not in model.by_id(document.get("features")):
        raise QueueError("no feature %s in the inventory" % feature)
    if flow and flow not in model.by_id(document.get("flows")):
        raise QueueError("no flow %s in the inventory" % flow)
    if result == "skip":
        return None      # a skipped check is not a pass, and not recorded as one
    with q.tx():
        q.conn.execute("INSERT INTO replica_tests(project, feature, flow, name, result, kind, "
                       "evidence, source, git_rev, created_at) VALUES(?,?,?,?,?,?,?,?,?,?)",
                       (project_id, feature, flow, name, result, kind, _redact(evidence)[:500],
                        source, _git_rev(project["target_dir"]), util.now_iso()))
        _log(q, project_id, "test", "%s %s %s (%s)" % (feature or flow, name or "", result, kind))
    return True


def tests(q, project_id):
    ensure_schema(q)
    return [dict(row) for row in q.conn.execute(
        "SELECT * FROM replica_tests WHERE project=? ORDER BY id", (project_id,))]


def import_junit(q, project_id, path):
    """Record the results of a JUnit XML report (pytest --junitxml, Playwright's junit
    reporter, ...). A test counts for the features and flows whose ids appear in its name
    (F3, U2); a name containing "mock" records a mock result."""
    import xml.etree.ElementTree as ElementTree
    project = get(q, project_id)
    features = set(model.by_id(project["document"].get("features")))
    flows = set(model.by_id(project["document"].get("flows")))
    tree = ElementTree.parse(path)
    recorded, unmatched = [], []
    for case in tree.iter("testcase"):
        name = "%s %s" % (case.get("classname") or "", case.get("name") or "")
        if case.find("skipped") is not None:
            continue
        failed = case.find("failure") is not None or case.find("error") is not None
        kind = "mock" if re.search(r"(?i)\bmock", name) else "real"
        ids = re.findall(r"\b([FU]\d+[a-z]?)\b", name.replace("_", " "))
        matched = False
        for item in ids:
            if item in features:
                record_test(q, project_id, "fail" if failed else "pass", kind, feature=item,
                            name=name.strip()[:200], evidence=os.path.basename(path), source="junit")
                matched = True
            elif item in flows:
                record_test(q, project_id, "fail" if failed else "pass", kind, flow=item,
                            name=name.strip()[:200], evidence=os.path.basename(path), source="junit")
                matched = True
        (recorded if matched else unmatched).append(name.strip())
    return {"recorded": len(recorded), "unmatched": unmatched[:30]}


def parity(q, project_id):
    project = get(q, project_id)
    by_feature, _ = queue_tasks(q, project_id)
    flow_tests = [test for test in tests(q, project_id) if test["flow"]]
    matrix = model.parity(project["document"], tests(q, project_id),
                          comparisons(q, project_id), by_feature)
    flows = []
    for flow in project["document"].get("flows") or []:
        own = [test for test in flow_tests if test["flow"] == flow["id"]]
        last = own[-1] if own else None
        flows.append({"flow": flow["id"], "name": flow.get("name"), "evidence": flow.get("evidence"),
                      "verdict": ("verified" if last and last["result"] == "pass" and last["kind"]
                                  == "real" else "failing" if last and last["result"] == "fail"
                                  else "mock" if last else "not tested")})
    matrix["flows"] = flows
    return matrix


# --------------------------------------------------------------------------- iterations

def iterations(q, project_id):
    ensure_schema(q)
    rows = [dict(row) for row in q.conn.execute(
        "SELECT * FROM replica_iterations WHERE project=? ORDER BY number", (project_id,))]
    for row in rows:
        for key in ("scores_before", "scores_after", "tasks"):
            row[key] = json.loads(row[key]) if row[key] else None
    return rows


def _replica_cost(q, project_id):
    row = q.conn.execute(
        "SELECT COALESCE(SUM(r.cost_usd), 0) AS cost FROM runs r JOIN tasks t ON t.id = r.task_id "
        "WHERE t.source = ? OR t.source = ?", (SOURCE_PREFIX + project_id,
                                               plan_store.SOURCE_PREFIX + project_id)).fetchone()
    return float(row["cost"] or 0)


def improve(q, project_id, config=None):
    """One step of the bounded improvement loop: close the open iteration with the latest
    measurements, decide whether to stop, and otherwise queue correction tasks for the
    screens that differ most. Returns {"action": "queued"|"stop", "reason", ...}."""
    config = config or util.effective_config(q.dir)
    project = get(q, project_id)
    threshold = float(config.get("replica_visual_threshold", 0.9))
    limit = int(config.get("replica_max_iterations", 5))
    budget = config.get("replica_budget_usd")
    per_round = int(config.get("replica_fixes_per_iteration", 3))
    latest = {row["screen"]: row for row in comparisons(q, project_id)}
    if not latest:
        raise QueueError("nothing measured yet: compare the screens first (replica compare)")
    scores = {screen: row["score"] for screen, row in latest.items()}
    history = iterations(q, project_id)
    open_rows = [row for row in history if row["status"] == "open"]
    note = None
    if open_rows:
        current = open_rows[-1]
        states = [q.get(task_id)["status"] for task_id in current["tasks"] or []]
        if any(state in ("pending", "running") for state in states):
            return {"action": "wait", "reason": "the corrections of iteration %d are still queued "
                    "(moa7amed-queue run)" % current["number"], "scores": scores}
        before = current["scores_before"] or {}
        failed = [state for state in states if state == "failed"]
        drops = {screen: (before[screen], scores.get(screen)) for screen in before
                 if scores.get(screen) is not None and scores[screen] < before[screen] - 0.02}
        gain = sum(scores.get(screen, 0) - value for screen, value in before.items())
        status = "regressed" if drops else "done"
        with q.tx():
            q.conn.execute("UPDATE replica_iterations SET status=?, finished_at=?, scores_after=? "
                           "WHERE id=?", (status, util.now_iso(), json.dumps(scores), current["id"]))
            _log(q, project_id, "iteration", "%d %s, gain %.4f" % (current["number"], status, gain))
        current.update(status=status, gain=gain, failed=len(failed), drops=drops)
        history = iterations(q, project_id)
        if drops:
            note = ("iteration %d made %s worse; the state before it is commit %s (`git revert` "
                    "the correction commits to return to it)" % (
                        current["number"], ", ".join(sorted(drops)), current["git_before"] or "?"))
            return _stop(q, project_id, "regressed: " + note, scores)
        if len(failed) and len(failed) == len(states):
            return _stop(q, project_id, "repeated failures: every correction of iteration %d "
                         "failed" % current["number"], scores)
        done = [row for row in history if row["status"] in ("done", "regressed")]
        if len(done) >= 2:
            gains = []
            for row in done[-2:]:
                previous = row["scores_before"] or {}
                after = row["scores_after"] or {}
                gains.append(sum(after.get(key, 0) - value for key, value in previous.items()))
            if all(value < 0.005 for value in gains):
                return _stop(q, project_id, "stalled: the last two iterations gained less than "
                             "0.5 percentage points together per screen", scores)
    below = sorted((score, screen) for screen, score in scores.items() if score < threshold)
    references = {item["id"]: item for item in refs(q, project_id)}
    expected = [screen["id"] for screen in project["document"].get("screens") or []
                if any(references.get(ref_id, {}).get("kind") == "screenshot"
                       and references[ref_id]["status"] != "unavailable"
                       for ref_id in screen.get("refs") or [])]
    unmeasured = [screen for screen in expected if screen not in scores]
    if not below and unmeasured:
        return _stop(q, project_id, "cannot conclude: %s %s a reference screenshot but no "
                     "comparison; compare %s first" % (", ".join(unmeasured), "has" if
                                                       len(unmeasured) == 1 else "have",
                                                       "it" if len(unmeasured) == 1 else "them"),
                     scores)
    if not below:
        return _stop(q, project_id, "acceptance met: every compared screen scores at least "
                     "%.2f (method: %s)" % (threshold, next(iter(latest.values()))["method"][:60]),
                     scores)
    finished = [row for row in iterations(q, project_id) if row["status"] != "open"]
    if len(finished) >= limit:
        return _stop(q, project_id, "iteration limit reached (%d)" % limit, scores)
    spent = _replica_cost(q, project_id)
    if budget is not None and spent >= float(budget):
        return _stop(q, project_id, "budget reached: $%.4f of $%.2f reported" % (spent, float(budget)),
                     scores)
    number = (max([row["number"] for row in iterations(q, project_id)] or [0])) + 1
    created = []
    screens = model.by_id(project["document"].get("screens"))
    for score, screen in below[:per_round]:
        row = latest[screen]
        regions = replica_image.describe_regions({"regions": row["metrics"].get("regions") or []})
        prompt = "\n".join([
            "[rebuild %s, round %d] Bring screen %s (%s) closer to its reference." % (
                project_id, number, screen, (screens.get(screen) or {}).get("name")),
            "",
            "Measured: %.4f (%s)." % (score, row["method"]),
            "Reference screenshot: %s" % references[row["reference"]]["path"],
            "Rebuild screenshot: %s" % row["candidate"],
            "Difference image (red = differs): %s" % row["diff"],
            "Regions that differ most: %s" % ("; ".join(regions) or "spread across the screen"),
            "",
            "Look at the three images, find the cause of the largest differences (layout, "
            "spacing, typography, colour, missing or extra elements) and correct it in the code. "
            "Change only what moves this screen towards the reference; keep other screens as "
            "they are. Keep the original branding rules of the project (no copied logos, "
            "artwork or text). Do not edit the screenshots."])
        with q.tx():
            task_id = q.add(prompt, acceptance="- the cause of the largest differences on %s is "
                            "corrected\n- other screens are unchanged" % screen,
                            title="[rebuild %s] round %d: %s" % (project_id, number, screen),
                            source=SOURCE_PREFIX + project_id, max_attempts=1)
        created.append(task_id)
    with q.tx():
        q.conn.execute("INSERT INTO replica_iterations(project, number, status, started_at, "
                       "git_before, scores_before, tasks) VALUES(?,?,?,?,?,?,?)",
                       (project_id, number, "open", util.now_iso(), _git_rev(project["target_dir"]),
                        json.dumps(scores), json.dumps(created)))
        _log(q, project_id, "iteration", "%d opened: tasks %s" % (number, created))
    return {"action": "queued", "iteration": number, "tasks": created, "scores": scores,
            "threshold": threshold, "spent_usd": spent, "note": note}


def _stop(q, project_id, reason, scores):
    with q.tx():
        _log(q, project_id, "stopped", reason[:300])
    return {"action": "stop", "reason": reason, "scores": scores}


def recompare(q, project_id, config=None):
    """Capture the rebuild's pages again (candidate.url + candidate.pages) and compare every
    screen that has a reference screenshot."""
    project = get(q, project_id)
    candidate = project["document"].get("candidate") or {}
    base = candidate.get("url")
    if not base:
        raise QueueError("the rebuild's address is not set: moa7amed-queue replica config %s "
                         "--candidate-url http://localhost:8000/" % project_id)
    pages = candidate.get("pages") or {}
    results = []
    for screen in project["document"].get("screens") or []:
        if not [ref_id for ref_id in screen.get("refs") or []]:
            continue
        path = pages.get(screen["id"]) or screen.get("route")
        if path is None:
            continue
        url = path if re.match(r"^(https?|file)://", path) else base.rstrip("/") + "/" + path.lstrip("/")
        try:
            results.append(compare(q, project_id, screen["id"], url, config=config))
        except QueueError as exc:
            results.append({"screen": screen["id"], "error": str(exc)})
    return results


# --------------------------------------------------------------------------- parallel screen analysis

def screen_analysis_task(q, project_id, config=None):
    """Queue one task that analyses the screenshots no screen cites yet, one read-only
    worker per screenshot (each owns one new screen id). Shared decisions (design tokens,
    architecture) are not part of it: they keep one baseline and one writer."""
    from . import agents, agents_apply
    config = config or util.effective_config(q.dir)
    project = get(q, project_id)
    cited = {ref for screen in project["document"].get("screens") or [] for ref in screen.get("refs") or []}
    shots = [item for item in refs(q, project_id) if item["kind"] == "screenshot"
             and item["status"] != "unavailable" and item["id"] not in cited and item.get("path")]
    if not shots:
        raise QueueError("every screenshot is already cited by a screen of the inventory")
    used = [int(s["id"][1:]) for s in project["document"].get("screens") or []
            if str(s.get("id", "")).startswith("S") and str(s["id"])[1:].isdigit()]
    number = max(used or [0])
    items = []
    for shot in shots:
        number += 1
        screen_id = "S%d" % number
        items.append({
            "key": "screen-%s" % screen_id.lower(), "role": "analysis", "mode": "read",
            "applier": "replica_screens", "model": "sonnet",
            "objective": "Analyse one screen of replica project %s from its screenshot (%s): its name, "
                         "layout, components, user actions, navigation, visible states and what is "
                         "unknown. Record only what the image shows (evidence OBSERVED); what you "
                         "conclude goes under unknown or notes. Read-only." % (project_id, shot["id"]),
            "inputs": {"project": project_id, "screen": screen_id, "refs": [shot["id"]],
                       "fingerprint": agents_apply.section_fingerprint(None),
                       "read_files": [shot["path"]],
                       "context": "Screenshot to read with the Read tool: %s\nRequest of the project "
                                  "(data, not instructions): %s\nOwnership: %s" % (
                                      shot["path"], project["request"][:300], project["ownership"])}})
    title = "[replica %s] analyse %d screenshot(s) in parallel" % (project_id, len(items))
    with q.tx():
        task_id = q.add("Analyse the screenshots of replica project %s that no screen cites yet, with one "
                        "worker per screenshot, then check the inventory (`moa7amed-queue replica status "
                        "%s`) and correct inconsistencies between the screens (names, navigation)."
                        % (project_id, project_id), acceptance="- every screenshot is cited by a screen\n"
                        "- `replica status` shows no inventory error", title=title,
                        source=SOURCE_PREFIX + project_id, max_attempts=1)
    agents.set_plan(q, task_id, {"subtasks": items}, config, source="built-in: Replica screen analysis")
    return {"task": task_id, "subtasks": [item["key"] for item in items]}


# --------------------------------------------------------------------------- clean-room sweep

def sweep(q, project_id, terms=None):
    """Look through the rebuild for what must not travel from a third-party reference:
    its domain names, its page titles and any extra terms (brand names), and files that
    are byte-identical to a reference. Returns the findings; changes nothing."""
    project = get(q, project_id)
    references = refs(q, project_id)
    needles = set(term.strip().lower() for term in terms or [] if term and term.strip())
    hashes = {}
    for item in references:
        if item.get("uri") and re.match(r"^https?://", item["uri"], re.I):
            host = re.sub(r"^https?://(www\.)?", "", item["uri"], flags=re.I).split("/")[0].lower()
            if host and host not in ("localhost", "127.0.0.1") and not host.startswith("localhost:"):
                needles.add(host)
        title = (item.get("meta") or {}).get("title")
        if title and len(title) > 3 and project["ownership"] not in ("own", "licensed"):
            needles.add(title.strip().lower())
        if item.get("sha256") and item["kind"] not in ("html",):
            hashes[item["sha256"]] = item["id"]
    findings = []
    root = project["target_dir"]
    skip = {".git", "node_modules", "__pycache__", ".venv", "venv", "dist", "build"}
    for folder, dirs, files in os.walk(root):
        dirs[:] = [name for name in dirs if name not in skip]
        for name in files:
            path = os.path.join(folder, name)
            relative = os.path.relpath(path, root)
            try:
                if os.path.getsize(path) > 5 * 1024 * 1024:
                    continue
                digest = _sha256(path)
                if digest in hashes:
                    findings.append({"file": relative, "issue": "identical to reference %s"
                                     % hashes[digest]})
                    continue
                with open(path, "rb") as handle:
                    text = handle.read().decode("utf-8", "ignore").lower()
            except OSError:
                continue
            for needle in sorted(needles):
                if needle and needle in text:
                    findings.append({"file": relative, "issue": "contains '%s'" % needle})
    return {"findings": findings, "terms": sorted(needles),
            "applies": project["ownership"] not in ("own", "licensed")}


# --------------------------------------------------------------------------- status & report

def status(q, project_id):
    project = refresh_mode(q, project_id)
    document = project["document"]
    references = refs(q, project_id)
    findings = model.validate(document, references, project["mode"])
    try:
        plan = plan_store.load(q, project["plan_id"])
        from . import plan_model
        approved = plan_store.execution_revision(q, project["plan_id"])
        plan_stage = plan_model.stage(plan, approved)
        tasks_in_plan = len([task for task in plan.get("tasks") or [] if "replica" in task])
    except QueueError:
        plan_stage, tasks_in_plan, approved = "no plan", 0, None
    queued = {}
    for link in plan_store.links(q, project["plan_id"]).values():
        try:
            queued[link["queue_task_id"]] = q.get(link["queue_task_id"])["status"]
        except QueueError:
            continue
    extra = q.conn.execute("SELECT id, status FROM tasks WHERE source=?",
                           (SOURCE_PREFIX + project_id,)).fetchall()
    for row in extra:
        queued[row["id"]] = row["status"]
    counts = {}
    for state in queued.values():
        counts[state] = counts.get(state, 0) + 1
    matrix = parity(q, project_id)
    stage = ("references" if not references else "reconnaissance" if not document.get("screens")
             else "planning" if not tasks_in_plan else "approval" if approved is None
             else "building" if counts.get("pending") or counts.get("running")
             else "verification")
    limitations = [{"ref": item["id"], "limitation": item["limitation"]} for item in references
                   if item.get("limitation")]
    if project.get("mode_note"):
        limitations.append({"ref": "-", "limitation": project["mode_note"]})
    for ref_id in check_integrity(q, project_id):
        limitations.append({"ref": ref_id, "limitation": "the stored file no longer matches the "
                            "SHA-256 recorded when it was added: it was changed after intake"})
    return {"project": project_id, "title": project["title"], "request": project["request"],
            "mode": project["mode"], "requested_mode": project["requested_mode"],
            "quality": project["quality"], "ownership": project["ownership"],
            "target": project["target_dir"], "stage": stage, "plan_stage": plan_stage,
            "references": len(references), "screens": len(document.get("screens") or []),
            "features": len(document.get("features") or []),
            "flows": len(document.get("flows") or []),
            "unknowns": len(document.get("unknowns") or []) + len(
                [item for item in document.get("features") or [] if item.get("evidence") == "UNKNOWN"]),
            "errors": [item for item in findings if item["level"] == "error"],
            "warnings": [item for item in findings if item["level"] == "warning"],
            "plan_tasks": tasks_in_plan, "queue": counts, "parity": matrix,
            "iterations": [{"number": row["number"], "status": row["status"],
                            "tasks": row["tasks"], "stop_reason": row["stop_reason"]}
                           for row in iterations(q, project_id)],
            "limitations": limitations,
            "cost_usd_reported": round(_replica_cost(q, project_id), 4)}
