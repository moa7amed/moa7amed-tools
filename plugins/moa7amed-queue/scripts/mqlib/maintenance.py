"""Keeping an installation healthy across versions.

Updates themselves are done by Claude Code's marketplace mechanism
(`claude plugin marketplace update`, `claude plugin update`, or the per-marketplace
auto-update setting). Nothing here downloads or installs anything. This module
answers the questions around an update:

    update     which version is installed, which one the marketplace offers, is
               auto-update on, is it safe to update now, and the exact commands
    doctor     is this installation and its data healthy (run it after an update)
    backup     copy settings and queues to a dated folder; restore brings one back
    changelog  what changed, from the changelog shipped with the plugin
    usage      what the tasks of a project cost, exactly as the CLI reported it
"""
from __future__ import annotations

import json
import os
import re
import shutil
import sqlite3
import subprocess
import time

from . import LEGACY_NAME, NAME, __version__, models, routing, store, util
from .util import QueueError

DATA_FORMAT = store.SCHEMA_VERSION   # what this version reads and writes
AUTOMATIC_BACKUP_LABEL = "auto-update-from-"
AUTOMATIC_BACKUPS_KEPT = 5

_VERSION_RE = re.compile(r"^v?(\d+)\.(\d+)\.(\d+)(?:-([A-Za-z]+)\.?(\d+)?)?$")
_PRE_RANK = {"alpha": 0, "beta": 1, "rc": 2}


# --------------------------------------------------------------------------- versions

def parse_version(text):
    """Sortable form of `1.2.3` or `1.2.3-beta.4`; a pre-release sorts before its release."""
    match = _VERSION_RE.match((text or "").strip())
    if not match:
        return None
    major, minor, patch, label, number = match.groups()
    rank = _PRE_RANK.get((label or "").lower(), 1) if label else 9
    return (int(major), int(minor), int(patch), rank, int(number or 0))


def is_newer(candidate, current):
    a, b = parse_version(candidate), parse_version(current)
    return bool(a and b and a > b)


def channel_of(version):
    """`stable` for 1.2.3, `beta` for anything with a pre-release suffix."""
    parsed = parse_version(version)
    return None if parsed is None else ("stable" if parsed[3] == 9 else "beta")


# --------------------------------------------------------------------------- release declaration

def release_info(root=None):
    """What a copy of the plugin declares about itself in release.json: its version and
    channel, the data format it writes, the formats it reads, and what it was verified
    with. Returns {} for a copy that has no such file (versions before 0.3.1)."""
    try:
        data = json.loads(util.read_text_file(os.path.join(root or util.plugin_root(),
                                                           "release.json")))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def compatibility(offered_root):
    """Can the copy in `offered_root` take over the data written here? Returns a check."""
    info = release_info(offered_root)
    if not info:
        return {"name": "compatibility", "status": "warn",
                "detail": "the offered version does not declare which data it reads (versions "
                          "before 0.3.1 do not). Releases 0.1.0 to 0.3.0 all use data format 1; "
                          "this version writes format %s." % DATA_FORMAT}
    problems, notes = [], []
    reads = [str(item) for item in info.get("reads_data_formats") or []]
    if DATA_FORMAT not in reads:
        problems.append("it reads data format(s) %s, the queues here use format %s"
                        % (", ".join(reads) or "?", DATA_FORMAT))
    elif str(info.get("data_format")) != DATA_FORMAT:
        notes.append("it will convert the queues from format %s to %s on first use: back up "
                     "first, the previous version cannot read the result"
                     % (DATA_FORMAT, info.get("data_format")))
    else:
        notes.append("same data format (%s): no migration, and going back is possible"
                     % DATA_FORMAT)
    wanted = parse_version(str(info.get("min_python") or "3.8") + ".0")
    import sys
    if wanted and sys.version_info[:2] < wanted[:2]:
        problems.append("it needs Python %s, this is %s" % (info.get("min_python"),
                                                            sys.version.split()[0]))
    return {"name": "compatibility", "status": "fail" if problems else "ok",
            "detail": "; ".join(problems or notes)}


# --------------------------------------------------------------------------- changelog

def changelog_path(root=None):
    return os.path.join(root or util.plugin_root(), "CHANGELOG.md")


def changelog_entries(path=None):
    """[(version, text)] newest first, from a CHANGELOG.md with `## <version>` headings."""
    try:
        text = util.read_text_file(path or changelog_path())
    except OSError:
        return []
    entries, current, lines = [], None, []
    for line in text.splitlines():
        heading = re.match(r"^##\s+\[?v?([0-9][^\s\]]*)\]?", line)
        if heading:
            if current:
                entries.append((current, "\n".join(lines).strip()))
            current, lines = heading.group(1), []
        elif current:
            lines.append(line)
    if current:
        entries.append((current, "\n".join(lines).strip()))
    return entries


def changes_since(version, path=None):
    """Changelog entries newer than `version` (all of them when it is None)."""
    return [(found, text) for found, text in changelog_entries(path)
            if version is None or is_newer(found, version)]


# --------------------------------------------------------------------------- install state

def state_path():
    return os.path.join(util.home(), "state.json")


def note_version(backup=True):
    """Remember which version last ran here, so an update is recognised afterwards.

    Normally this is one small read. On the first run after the version changed (and
    before this version has written anything) the settings and queues are backed up,
    because that is the last moment the data is exactly as the previous version left it.
    `backup=False` only records the version (used once a command has finished)."""
    home = util.home()
    if not os.path.isdir(home):
        return None
    state = _read_state()
    if state.get("version") == __version__:
        return state
    previous = state.get("version") if isinstance(state.get("version"), str) else None
    from_earlier = previous or ("earlier" if not state and _written_by_earlier_version() else None)
    needs_backup = bool(from_earlier) and bool(_projects())
    if needs_backup and not backup:
        # Recording the version now would make the next command believe the backup was
        # made. Leave it: the next command starts by making it.
        return state
    history = state.get("history") if isinstance(state.get("history"), list) else []
    if previous:
        history.append({"version": previous, "until": util.now_iso()})
    new = {"version": __version__, "previous_version": previous,
           "since": util.now_iso(), "history": history[-20:]}
    if needs_backup:
        # The backup comes first and the version is recorded after it. If this is cut
        # short (a closed terminal, a hook that ran out of time), nothing is recorded and
        # the next command tries again.
        try:
            if util.effective_config().get("backup_on_update"):
                recent = _recent_automatic_backup(from_earlier)
                new["update_backup"] = recent or create_backup(
                    label=AUTOMATIC_BACKUP_LABEL + from_earlier)["name"]
                prune_automatic_backups()
        except (QueueError, OSError, sqlite3.Error) as exc:
            new["update_backup_error"] = str(exc)[:200]
    try:
        util.save_json_atomic(state_path(), new)
    except OSError:
        pass
    return new


def _read_state():
    """The state file as a dictionary; a missing or damaged file reads as empty."""
    try:
        state = util.load_json(state_path(), None)
    except QueueError:
        return {}
    return state if isinstance(state, dict) else {}


def _recent_automatic_backup(from_version, hours=24):
    """The name of an automatic backup this version already made for an update from
    `from_version` within the last day. Two copies of the plugin taking turns (a runner
    still working from the previous version's folder, say) would otherwise back up on
    every turn and push the one real pre-update backup out of the kept set."""
    label = AUTOMATIC_BACKUP_LABEL + from_version
    for item in list_backups():
        if item.get("label") != label or item.get("version") != __version__:
            continue
        try:
            age = (util.now() - util.parse_iso(item.get("created_at"))).total_seconds()
        except (TypeError, ValueError):
            continue
        if age < hours * 3600:
            return item["name"]
    return None


def _sqlite_uri(path):
    """The address SQLite needs to open the file at this absolute path read-only. A path
    on another machine (\\\\server\\share\\...) keeps both of its leading slashes after the
    empty host part; a `file://server/...` address is refused by SQLite."""
    text = path.replace("\\", "/").replace("%", "%25").replace("?", "%3f").replace("#", "%23")
    return "file://%s%s?mode=ro" % ("" if text.startswith("/") else "/", text)


def _open_readonly(path):
    """Open a database without being able to change it (or to create it by accident)."""
    path = os.path.abspath(path)
    if not os.path.isfile(path):
        raise sqlite3.OperationalError("unable to open database file %s" % path)
    try:
        return sqlite3.connect(_sqlite_uri(path), uri=True, timeout=30)
    except sqlite3.Error:
        # A location that address form cannot express: open it normally and forbid writing.
        conn = sqlite3.connect(path, timeout=30)
        conn.execute("PRAGMA query_only = ON")
        return conn


def _written_by_earlier_version():
    """True when a queue here was last written by a version before 0.3.1. Those versions
    kept no state file; their databases are recognised by the table 0.3.1 adds on first use."""
    for _name, folder, _root in _projects():
        try:
            conn = _open_readonly(os.path.join(folder, "queue.db"))
            try:
                if conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND "
                                "name='process_tokens'").fetchone() is None:
                    return True
            finally:
                conn.close()
        except sqlite3.Error:
            return True   # unreadable: let the backup try, and report why it could not
    return False


def _claude_json(claude_bin, *args):
    """Run a `claude ... --json` command. Returns (data, error)."""
    try:
        executable = models.resolve_claude(claude_bin)
        result = subprocess.run([executable] + list(args), stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE, stdin=subprocess.DEVNULL, timeout=120,
                                **util.no_window_kwargs())
    except (QueueError, OSError, subprocess.SubprocessError) as exc:
        return None, str(exc)
    text = result.stdout.decode("utf-8", errors="replace").strip()
    if result.returncode != 0:
        return None, (result.stderr.decode("utf-8", errors="replace").strip() or text)[:300]
    try:
        return json.loads(text), None
    except ValueError:
        return None, "unexpected output from `claude %s`" % " ".join(args)


def _claude_config_dir():
    return os.environ.get("CLAUDE_CONFIG_DIR") or os.path.join(os.path.expanduser("~"), ".claude")


def auto_update_setting(marketplace, project=None):
    """Whether Claude Code auto-updates this marketplace: (True | False | None, where it says so).
    Third-party marketplaces are not auto-updated unless the user turns it on. Read in the
    order Claude Code documents: an `extraKnownMarketplaces` entry in the settings (local,
    project, user), then the file the `/plugin` toggle writes."""
    base = _claude_config_dir()
    start = project or os.getcwd()
    for path, key in ((os.path.join(start, ".claude", "settings.local.json"), "extraKnownMarketplaces"),
                      (os.path.join(start, ".claude", "settings.json"), "extraKnownMarketplaces"),
                      (os.path.join(base, "settings.json"), "extraKnownMarketplaces"),
                      (os.path.join(base, "plugins", "known_marketplaces.json"), None)):
        try:
            data = json.loads(util.read_text_file(path))
        except (OSError, ValueError):
            continue
        entry = (data.get(key) if key else data) or {}
        entry = entry.get(marketplace) if isinstance(entry, dict) else None
        if isinstance(entry, dict) and isinstance(entry.get("autoUpdate"), bool):
            return entry["autoUpdate"], path
    return None, None


def auto_update_switched_off_by():
    """Environment variables that switch Claude Code's whole auto-update pass off, whatever
    the marketplace setting says. FORCE_AUTOUPDATE_PLUGINS=1 keeps plugin updates on."""
    if os.environ.get("FORCE_AUTOUPDATE_PLUGINS", "").strip() == "1":
        return []
    found = [name for name in ("DISABLE_UPDATES", "DISABLE_AUTOUPDATER")
             if os.environ.get(name, "").strip().lower() in ("1", "true", "yes")]
    if os.environ.get("CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC"):   # any value switches it off
        found.append("CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC")
    return found


def _same_path(a, b):
    try:
        return os.path.normcase(os.path.realpath(a)) == os.path.normcase(os.path.realpath(b))
    except (OSError, TypeError):
        return False


def update_status(claude_bin="claude", refresh=False):
    """Everything known about the installed and the available version. Read-only, except that
    `refresh` asks Claude Code to refresh the marketplace listing (its own mechanism)."""
    root = util.plugin_root()
    status = {"plugin": NAME, "running_version": __version__, "plugin_root": root,
              "installed": None, "legacy_installed": [], "marketplace": None,
              "available_version": None, "update_available": None, "auto_update": None,
              "auto_update_source": None, "auto_update_switched_off_by": [],
              "channel": channel_of(__version__),
              "whats_new": [], "notes": [], "refreshed": False}
    plugins, error = _claude_json(claude_bin, "plugin", "list", "--json")
    if plugins is not None and not isinstance(plugins, list):
        plugins, error = None, "unexpected answer from `claude plugin list --json`"
    if plugins is None:
        status["notes"].append("could not ask Claude Code which plugins are installed: %s" % error)
        return status
    plugins = [item for item in plugins if isinstance(item, dict)]
    mine = [item for item in plugins if str(item.get("id", "")).split("@")[0] == NAME]
    status["legacy_installed"] = [item.get("id") for item in plugins
                                  if str(item.get("id", "")).split("@")[0] == LEGACY_NAME]
    chosen = next((item for item in mine if _same_path(item.get("installPath"), root)),
                  mine[0] if mine else None)
    if chosen is None:
        status["notes"].append("this copy is not installed through a marketplace (it runs from "
                               "%s); Claude Code cannot update it" % root)
        return status
    marketplace = str(chosen["id"]).split("@", 1)[1] if "@" in str(chosen["id"]) else None
    status["installed"] = {key: chosen.get(key) for key in ("id", "version", "scope", "enabled",
                                                            "installPath", "lastUpdated")}
    if not _same_path(chosen.get("installPath"), root):
        status["notes"].append("the copy answering this command (%s) is not the installed one "
                               "(%s)" % (root, chosen.get("installPath")))
    if not marketplace:
        return status
    if refresh:
        try:
            result = subprocess.run([models.resolve_claude(claude_bin), "plugin", "marketplace",
                                     "update", marketplace], stdout=subprocess.PIPE,
                                    stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL, timeout=300,
                                    **util.no_window_kwargs())
            status["refreshed"] = result.returncode == 0
            if result.returncode != 0:
                status["notes"].append("refreshing the marketplace failed: %s" % result.stdout.decode(
                    "utf-8", errors="replace").strip()[-200:])
        except (QueueError, OSError, subprocess.SubprocessError) as exc:
            status["notes"].append("refreshing the marketplace failed: %s" % exc)
    markets, error = _claude_json(claude_bin, "plugin", "marketplace", "list", "--json")
    markets = [item for item in markets if isinstance(item, dict)] \
        if isinstance(markets, list) else []
    market = next((item for item in markets if item.get("name") == marketplace), None)
    if market is None:
        status["notes"].append("marketplace %s is not configured any more%s"
                               % (marketplace, ": %s" % error if error else ""))
        return status
    status["marketplace"] = {key: market.get(key) for key in ("name", "source", "repo", "url",
                                                              "path", "ref")}
    location = market.get("installLocation") or market.get("path")
    offered = None
    if location:
        folder = os.path.join(location, "plugins", NAME)
        try:
            listing = json.loads(util.read_text_file(
                os.path.join(location, ".claude-plugin", "marketplace.json")))
            entry = next((item for item in listing.get("plugins", []) if item.get("name") == NAME), {})
            if isinstance(entry.get("source"), str):
                folder = os.path.normpath(os.path.join(location, entry["source"]))
            offered = entry.get("version")
        except (OSError, ValueError):
            pass
        try:
            manifest = json.loads(util.read_text_file(
                os.path.join(folder, ".claude-plugin", "plugin.json")))
            offered = manifest.get("version") or offered   # the manifest wins, as in Claude Code
        except (OSError, ValueError):
            pass
        installed_version = chosen.get("version")
        if offered and offered != installed_version:
            status["compatibility"] = compatibility(folder)
        if offered and is_newer(offered, installed_version):
            status["whats_new"] = [{"version": version, "text": text} for version, text
                                   in changes_since(installed_version,
                                                    os.path.join(folder, "CHANGELOG.md"))]
    try:
        known = json.loads(util.read_text_file(os.path.join(
            _claude_config_dir(), "plugins", "known_marketplaces.json")))
        stamp = (known.get(marketplace) or {}).get("lastUpdated")
        # Claude Code writes milliseconds; the queue's own timestamps have none.
        status["marketplace"]["last_refreshed"] = re.sub(r"\.\d+Z$", "Z", stamp) if stamp else None
    except (OSError, ValueError, AttributeError):
        pass
    status["available_version"] = offered
    if offered:
        status["comparable"] = bool(parse_version(offered) and parse_version(chosen.get("version")))
        status["update_available"] = is_newer(offered, chosen.get("version")) \
            if status["comparable"] else None
        status["available_channel"] = channel_of(offered)
    else:
        status["notes"].append("the marketplace copy on this machine does not state a version")
    if market.get("source") in ("directory", "file"):
        status["notes"].append("this marketplace is a local folder: it is not a published "
                               "release channel, and Claude Code reads it in place")
    status["auto_update"], status["auto_update_source"] = auto_update_setting(marketplace)
    status["auto_update_switched_off_by"] = auto_update_switched_off_by()
    return status


def update_commands(status):
    """The official commands that perform the update, in order."""
    marketplace = (status.get("marketplace") or {}).get("name") or "moa7amed-tools"
    return ["%s backup" % NAME,
            "claude plugin marketplace update %s" % marketplace,
            "claude plugin update %s@%s" % (NAME, marketplace),
            "(restart Claude Code, or run /reload-plugins in a running session)",
            "%s doctor" % NAME]


# --------------------------------------------------------------------------- health

def _projects():
    base = os.path.join(util.home(), "projects")
    found = []
    if os.path.isdir(base):
        for name in sorted(os.listdir(base)):
            folder = os.path.join(base, name)
            if os.path.isfile(os.path.join(folder, "queue.db")):
                info = {}
                try:
                    info = util.load_json(os.path.join(folder, "project.json"), {}) or {}
                except QueueError:
                    pass
                found.append((name, folder, info.get("root")))
    return found


def _inspect_database(path):
    """Open a queue database read-only. Returns (facts, problem)."""
    try:
        conn = _open_readonly(path)
    except sqlite3.Error as exc:
        return None, "cannot be opened: %s" % exc
    try:
        conn.row_factory = sqlite3.Row
        version = conn.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()
        integrity = conn.execute("PRAGMA integrity_check").fetchone()[0]
        counts = {row["status"]: row["n"] for row in conn.execute(
            "SELECT status, COUNT(*) AS n FROM tasks GROUP BY status")}
        lock = conn.execute("SELECT pid, host, runner_id, heartbeat_at FROM runner_lock").fetchone()
        lock = dict(lock) if lock else None
        if lock:
            try:
                token = conn.execute("SELECT token FROM process_tokens WHERE kind='runner' AND "
                                     "ref=?", (str(lock["runner_id"]),)).fetchone()
                lock["token"] = token["token"] if token else None
            except sqlite3.Error:      # a queue of a version that kept no start times
                lock["token"] = None
        facts = {"format": version["value"] if version else None, "integrity": integrity,
                 "tasks": counts, "lock": lock}
        return facts, None
    except sqlite3.Error as exc:
        return None, "cannot be read: %s" % exc
    finally:
        conn.close()


def _lock_alive(lock):
    """Is the runner that holds this lock still there? The same rule the engine uses,
    applied to what a read-only look at the queue found."""
    if lock["host"] == util.HOST:
        return util.process_alive(lock["pid"], lock.get("token"))
    try:
        age = (util.now() - util.parse_iso(lock["heartbeat_at"])).total_seconds()
        return age < util.effective_config()["stale_lock_seconds"]
    except (TypeError, ValueError, QueueError):
        return True   # cannot tell: assume it is working


def preflight():
    """Is it safe to update right now? Returns a list of {name, status, detail}.
    Looks at every queue without writing to any of them."""
    checks = []
    busy, unreadable, foreign = [], [], []
    for name, folder, root in _projects():
        facts, problem = _inspect_database(os.path.join(folder, "queue.db"))
        if problem:
            unreadable.append("%s (%s)" % (root or name, problem))
            continue
        if facts["integrity"] != "ok":
            unreadable.append("%s (integrity check: %s)" % (root or name, facts["integrity"]))
        if facts["format"] != DATA_FORMAT:
            foreign.append("%s (format %s)" % (root or name, facts["format"]))
        if facts["lock"] and _lock_alive(facts["lock"]):
            busy.append("%s (pid %s)" % (root or name, facts["lock"]["pid"]))
    checks.append({"name": "no runner is active",
                   "status": "fail" if busy else "ok",
                   "detail": "a runner is working on: %s. Let it finish first." % "; ".join(busy)
                             if busy else "no runner is working on any project"})
    checks.append({"name": "queues are readable",
                   "status": "fail" if unreadable else "ok",
                   "detail": "; ".join(unreadable) if unreadable
                             else "%d project queue(s) open and pass their integrity check"
                                  % len(_projects())})
    checks.append({"name": "data format",
                   "status": "warn" if foreign else "ok",
                   "detail": "written in another data format: %s" % "; ".join(foreign) if foreign
                             else "all queues use data format %s, which this version and every "
                                  "release since 0.1.0 read" % DATA_FORMAT})
    backups = list_backups()
    checks.append({"name": "backup",
                   "status": "ok" if backups else "warn",
                   "detail": "latest backup: %s" % backups[0]["name"] if backups
                             else "no backup yet; `%s backup` makes one" % NAME})
    return checks


def doctor(claude_bin="claude"):
    """Health check of this installation and its data. Returns a list of checks."""
    checks = []

    def add(name, status, detail):
        checks.append({"name": name, "status": status, "detail": detail})

    root = util.plugin_root()
    state = _read_state() if os.path.isdir(util.home()) else None
    detail = "%s %s at %s" % (NAME, __version__, root)
    if state and state.get("version") == __version__ and (state.get("previous_version")
                                                         or state.get("update_backup")):
        detail += " (updated from %s on %s)" % (
            state.get("previous_version") or "an earlier version",
            util.local_time(state.get("since")))
    if state and state.get("update_backup"):
        detail += "; the data was backed up first: %s" % state["update_backup"]
    add("version", "ok", detail)
    if state and state.get("update_backup_error"):
        add("backup on update", "warn", "the automatic backup after the update failed: %s. "
            "`%s backup` makes one now." % (state["update_backup_error"], NAME))
    try:
        manifest = json.loads(util.read_text_file(os.path.join(root, ".claude-plugin", "plugin.json")))
        same = manifest.get("version") == __version__ and manifest.get("name") == NAME
        add("manifest", "ok" if same else "fail",
            "plugin.json says %s %s" % (manifest.get("name"), manifest.get("version"))
            + ("" if same else ", the code says %s %s: the installation is mixed up"
               % (NAME, __version__)))
    except (OSError, ValueError) as exc:
        add("manifest", "fail", "plugin.json cannot be read: %s" % exc)
    import sys
    add("python", "ok" if sys.version_info >= (3, 8) else "fail",
        "Python %s" % sys.version.split()[0])
    missing = [name for name in ("bin/moa7amed-queue", "bin/moa7amed-queue.cmd", "bin/ctq",
                                 "hooks/hooks.json", "hooks/capture-prompt.sh",
                                 "runner/run-queue.ps1", "runner/run-queue.sh")
               if not os.path.isfile(os.path.join(root, name))]
    skills = os.path.join(root, "skills")
    count = len([name for name in os.listdir(skills)
                 if os.path.isfile(os.path.join(skills, name, "SKILL.md"))]) if os.path.isdir(skills) else 0
    add("plugin files", "fail" if missing else "ok",
        "missing: %s" % ", ".join(missing) if missing
        else "launchers, capture hook and runners present; %d skills" % count)
    if util.IS_WINDOWS and not shutil.which("sh"):
        add("capture hook", "warn", "no `sh` on PATH: the `queue:` capture hook needs Git for "
            "Windows. The skills and the command line work without it.")
    else:
        add("capture hook", "ok", "`sh` is available for the capture hook")

    home, origin = util.home_info()
    if not os.path.isdir(home):
        add("data directory", "ok", "%s does not exist yet (nothing has been queued)" % home)
    else:
        writable = os.access(home, os.W_OK)
        note = {"legacy": " (location of an earlier version; `%s migrate` moves it)" % NAME,
                "legacy-setting": " (set by CTQ_HOME)", "setting": " (set by MOA7AMED_QUEUE_HOME)"
                }.get(origin, "")
        add("data directory", "ok" if writable else "fail",
            "%s%s%s" % (home, note, "" if writable else " is not writable"))
        for path in (util.global_config_path(),):
            if os.path.isfile(path):
                try:
                    data = util.load_json(path, {}) or {}
                    unknown = sorted(key for key in data if key not in util.CONFIG_SPEC)
                    add("settings", "warn" if unknown else "ok",
                        "unknown settings are ignored: %s" % ", ".join(unknown) if unknown
                        else "%d setting(s) in %s" % (len(data), path))
                except QueueError as exc:
                    add("settings", "fail", str(exc))
        problems, stale, total = [], [], 0
        for name, folder, project_root in _projects():
            facts, problem = _inspect_database(os.path.join(folder, "queue.db"))
            if problem:
                problems.append("%s: %s" % (project_root or name, problem))
                continue
            total += sum(facts["tasks"].values())
            if facts["integrity"] != "ok":
                problems.append("%s: integrity check says %s" % (project_root or name,
                                                                  facts["integrity"]))
            if facts["format"] != DATA_FORMAT:
                problems.append("%s: data format %s, this version uses %s"
                                % (project_root or name, facts["format"], DATA_FORMAT))
            if facts["lock"] and not _lock_alive(facts["lock"]):
                stale.append(project_root or name)
        add("queues", "fail" if problems else "ok",
            "; ".join(problems) if problems
            else "%d project queue(s), %d task(s), all readable and intact"
                 % (len(_projects()), total))
        if stale:
            add("runner locks", "warn", "stale lock left by a runner that is gone in: %s. The "
                "next run recovers it; `%s recover` does it now." % ("; ".join(stale), NAME))
        pointer = os.path.join(home, "plugin-root")
        if os.path.isdir(os.path.join(home, "bin")):
            try:
                target = util.read_text_file(pointer).strip()
            except OSError:
                target = None
            add("command shims", "ok" if target and _same_path(target, root) else "warn",
                "point at this copy" if target and _same_path(target, root)
                else "point at %s, not at this copy; `%s install-cli` fixes it" % (target, NAME))

    # A health check leaves no trace: without a data directory, none is created for this.
    info = models.cli_info(claude_bin, remember=os.path.isdir(home))
    if info.get("error") or not info.get("path"):
        add("Claude Code", "fail", info.get("error") or "the `claude` command was not found")
    else:
        notes = []
        if not info.get("supports_model"):
            notes.append("it has no --model option, so tasks run on its default model")
        tested = release_info(root).get("tested_claude_code")
        if tested and info.get("version") != tested:
            notes.append("this release was verified with %s" % tested)
        add("Claude Code", "ok" if info.get("supports_model") else "warn",
            "version %s%s" % (info.get("version"), "; " + "; ".join(notes) if notes else ""))
    add("git", "ok" if shutil.which("git") else "warn",
        "available" if shutil.which("git") else "not found: tasks still run, but without commits "
        "or protection of uncommitted changes")
    return checks


# --------------------------------------------------------------------------- backup and restore

def backups_dir():
    return os.path.join(util.home(), "backups")


def list_backups():
    base = backups_dir()
    found = []
    if os.path.isdir(base):
        for name in sorted(os.listdir(base), reverse=True):
            manifest = os.path.join(base, name, "manifest.json")
            if os.path.isfile(manifest):
                try:
                    data = json.loads(util.read_text_file(manifest))
                except (OSError, ValueError):
                    continue
                data["name"], data["path"] = name, os.path.join(base, name)
                found.append(data)
    return found


def prune_automatic_backups(keep=AUTOMATIC_BACKUPS_KEPT):
    """Remove all but the newest automatic backups. Backups made by hand are never removed."""
    automatic = [item for item in list_backups()
                 if str(item.get("label") or "").startswith(AUTOMATIC_BACKUP_LABEL)]
    removed = []
    for item in automatic[keep:]:
        if _same_path(os.path.dirname(item["path"]), backups_dir()):
            shutil.rmtree(item["path"], ignore_errors=True)
            removed.append(item["name"])
    # An automatic backup that was cut short has no manifest and is of no use to anyone.
    base = backups_dir()
    if os.path.isdir(base):
        for name in os.listdir(base):
            folder = os.path.join(base, name)
            try:
                unfinished = (AUTOMATIC_BACKUP_LABEL in name and os.path.isdir(folder)
                              and not os.path.isfile(os.path.join(folder, "manifest.json"))
                              and time.time() - os.path.getmtime(folder) > 3600)
            except OSError:
                continue
            if unfinished:
                shutil.rmtree(folder, ignore_errors=True)
                removed.append(name)
    return removed


def _copy_database(source, target):
    """A consistent copy of a database, even while something else is using it."""
    os.makedirs(os.path.dirname(target), exist_ok=True)
    original = _open_readonly(source)
    try:
        copy = sqlite3.connect(target)
        try:
            original.backup(copy)
        finally:
            copy.close()
    finally:
        original.close()


def create_backup(label=None, output=None):
    """Copy settings and every project queue to a dated folder. Run logs and worktrees
    are not included: they are large and can be recreated or are plain files."""
    home = util.home()
    if not os.path.isdir(home):
        raise QueueError("there is nothing to back up yet (%s does not exist)" % home)
    name = "%s-v%s%s" % (util.now().strftime("%Y%m%d-%H%M%S"), __version__,
                         "-" + re.sub(r"[^A-Za-z0-9._-]+", "-", label) if label else "")
    target = os.path.join(output or backups_dir(), name)
    if os.path.exists(target):
        raise QueueError("%s already exists" % target)
    try:
        os.makedirs(target)
        files, projects = [], []
        for name_ in ("config.json", "models.json", "state.json"):
            source = os.path.join(home, name_)
            if os.path.isfile(source):
                shutil.copy2(source, os.path.join(target, name_))
                files.append(name_)
        for key, folder, root in _projects():
            destination = os.path.join(target, "projects", key)
            _copy_database(os.path.join(folder, "queue.db"), os.path.join(destination, "queue.db"))
            for extra in ("config.json", "project.json"):
                if os.path.isfile(os.path.join(folder, extra)):
                    shutil.copy2(os.path.join(folder, extra), os.path.join(destination, extra))
            facts, _problem = _inspect_database(os.path.join(destination, "queue.db"))
            projects.append({"key": key, "root": root, "tasks": (facts or {}).get("tasks", {})})
        manifest = {"created_at": util.now_iso(), "created_by": "%s %s" % (NAME, __version__),
                    "version": __version__, "data_format": DATA_FORMAT, "label": label,
                    "files": files, "projects": projects}
        util.save_json_atomic(os.path.join(target, "manifest.json"), manifest)
    except BaseException as exc:
        # Half a backup is worse than none: it would look like one.
        shutil.rmtree(target, ignore_errors=True)
        if isinstance(exc, (OSError, sqlite3.Error)):
            raise QueueError("the backup could not be made: %s" % exc)
        raise
    manifest["name"], manifest["path"] = os.path.basename(target), target
    return manifest


def _restore_database(source, target):
    """Put the contents of a backup copy into the live queue. SQLite does this as one
    change: complete, or not at all. Other programs that have the queue open simply see
    the restored contents afterwards (replacing the file under them would not work on
    Windows and would silently lose their writes elsewhere)."""
    os.makedirs(os.path.dirname(target), exist_ok=True)
    original = _open_readonly(source)
    try:
        live = sqlite3.connect(target, timeout=60)
        try:
            original.backup(live)
        finally:
            live.close()
    finally:
        original.close()


def restore_backup(name, confirmed=False):
    """Bring back a backup. The current state is backed up first, so this can be undone."""
    candidates = [item for item in list_backups() if item["name"] == name
                  or item["name"].startswith(name)]
    if os.path.isfile(os.path.join(name, "manifest.json")):
        data = json.loads(util.read_text_file(os.path.join(name, "manifest.json")))
        data["name"], data["path"] = os.path.basename(os.path.normpath(name)), name
        candidates = [data]
    if len(candidates) != 1:
        raise QueueError("%s backup matches '%s'; `%s backup --list` shows them"
                         % ("no" if not candidates else "more than one", name, NAME))
    backup = candidates[0]
    if backup.get("data_format") != DATA_FORMAT:
        raise QueueError("that backup uses data format %s; this version uses %s"
                         % (backup.get("data_format"), DATA_FORMAT))
    busy = [check for check in preflight() if check["name"] == "no runner is active"
            and check["status"] == "fail"]
    if busy:
        raise QueueError("cannot restore while a runner is active: %s" % busy[0]["detail"])
    plan = {"backup": backup["name"], "created_at": backup.get("created_at"),
            "version": backup.get("version"), "files": backup.get("files", []),
            "projects": [item.get("root") or item["key"] for item in backup.get("projects", [])],
            "restored": False}
    if not confirmed:
        return plan
    plan["safety_backup"] = create_backup(label="before-restore")["name"]
    home = util.home()

    def put_back(source_folder, destination_folder, name_, present):
        """A settings file comes back as it was; one that did not exist then goes."""
        destination = os.path.join(destination_folder, name_)
        if present:
            shutil.copy2(os.path.join(source_folder, name_), destination)
        elif os.path.isfile(destination):
            os.remove(destination)

    try:
        # Queues first: they are what matters, and each one is restored whole or not at all.
        for item in backup.get("projects", []):
            source = os.path.join(backup["path"], "projects", item["key"])
            destination = os.path.join(home, "projects", item["key"])
            _restore_database(os.path.join(source, "queue.db"),
                              os.path.join(destination, "queue.db"))
            put_back(source, destination, "config.json",
                     os.path.isfile(os.path.join(source, "config.json")))
            if os.path.isfile(os.path.join(source, "project.json")):
                shutil.copy2(os.path.join(source, "project.json"),
                             os.path.join(destination, "project.json"))
        # Which version runs here (state.json) is a fact, not something to restore.
        for name_ in ("config.json", "models.json"):
            put_back(backup["path"], home, name_, name_ in backup.get("files", []))
    except (OSError, sqlite3.Error) as exc:
        raise QueueError(
            "the restore stopped part-way (%s). Nothing is lost: the state from before it is "
            "in the backup %s. Close whatever is using the queue and run the restore again, "
            "or return to the previous state with `%s restore %s --yes`."
            % (exc, plan["safety_backup"], NAME, plan["safety_backup"]))
    plan["restored"] = True
    return plan


# --------------------------------------------------------------------------- usage

def usage(q, days=None):
    """What the runs of this project cost, as the CLI reported it. Nothing is estimated:
    runs for which the CLI reported no cost count as "not reported"."""
    since = None
    if days:
        import datetime
        since = util.iso(util.now() - datetime.timedelta(days=days))
    rows = q.conn.execute(
        "SELECT r.id, r.task_id, r.outcome, r.cost_usd, r.started_at, rr.model_arg, "
        "rr.actual_model, rr.phase, rr.usage FROM runs r LEFT JOIN run_routing rr "
        "ON rr.run_id = r.id WHERE r.finished_at IS NOT NULL %s ORDER BY r.id"
        % ("AND r.started_at >= ?" if since else ""), (since,) if since else ()).fetchall()
    per_model, per_task = {}, {}
    total, reported, unreported = 0.0, 0, 0
    for row in rows:
        model = row["actual_model"] or row["model_arg"] or "default model"
        bucket = per_model.setdefault(model, {"runs": 0, "cost_usd": 0.0, "input_tokens": 0,
                                              "output_tokens": 0, "not_reported": 0})
        bucket["runs"] += 1
        if row["cost_usd"] is None:
            unreported += 1
            bucket["not_reported"] += 1
        else:
            reported += 1
            total += row["cost_usd"]
            bucket["cost_usd"] += row["cost_usd"]
            task = per_task.setdefault(row["task_id"], {"task_id": row["task_id"], "runs": 0,
                                                        "cost_usd": 0.0})
            task["runs"] += 1
            task["cost_usd"] += row["cost_usd"]
        try:
            tokens = (json.loads(row["usage"] or "{}") or {}).get("tokens") or {}
        except ValueError:
            tokens = {}
        bucket["input_tokens"] += (tokens.get("input_tokens") or 0) + \
            (tokens.get("cache_read_input_tokens") or 0) + (tokens.get("cache_creation_input_tokens") or 0)
        bucket["output_tokens"] += tokens.get("output_tokens") or 0
    reviews = routing.review_costs(q, since)
    for task in per_task.values():
        task["title"] = q.get(task["task_id"])["title"]
    from . import agents
    agents.ensure_schema(q)
    since_ts = util.parse_iso(since).timestamp() if since else 0
    worker_rows = q.conn.execute("SELECT cost_usd FROM agent_subtasks WHERE started_ts >= ? AND "
                                 "finished_ts IS NOT NULL", (since_ts,)).fetchall()
    workers = {"runs": len(worker_rows),
               "cost_usd": round(sum(row["cost_usd"] or 0 for row in worker_rows), 6),
               "without_cost": len([row for row in worker_rows if row["cost_usd"] is None])}
    return {"since": since, "runs": len(rows), "runs_with_cost": reported,
            "runs_without_cost": unreported, "total_cost_usd": round(total, 6),
            "review_cost_usd": round(reviews, 6), "worker_cost_usd": workers["cost_usd"],
            "workers": workers, "by_model": per_model,
            "by_task": sorted(per_task.values(), key=lambda item: -item["cost_usd"])[:15]}
