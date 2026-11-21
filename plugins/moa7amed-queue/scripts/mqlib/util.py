"""Shared helpers: time, paths, project identity, configuration, process checks."""
from __future__ import annotations

import datetime as _dt
import hashlib
import json
import locale
import os
import re
import socket
import subprocess
import sys

APP_DIR_NAME = ".moa7amed-queue"
LEGACY_APP_DIR_NAME = ".claude-task-queue"   # data directory of versions 0.1.0 and 0.2.0
ENV_PREFIX = "MOA7AMED_QUEUE_"
LEGACY_ENV_PREFIX = "CTQ_"                   # environment variables of those versions
HOST = socket.gethostname()
IS_WINDOWS = os.name == "nt"


class QueueError(Exception):
    """A user-facing error. The CLI prints the message and exits with `code`."""

    code = 1

    def __init__(self, message, code=None, info=None):
        super().__init__(message)
        if code is not None:
            self.code = code
        self.info = info or {}


# --------------------------------------------------------------------------- time

def now():
    return _dt.datetime.now(_dt.timezone.utc).replace(microsecond=0)


def iso(dt):
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def now_iso():
    return iso(now())


def parse_iso(text):
    return _dt.datetime.strptime(text, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=_dt.timezone.utc)


def local_time(text):
    """Render a stored UTC timestamp in the local timezone for display."""
    if not text:
        return "-"
    try:
        return parse_iso(text).astimezone().strftime("%Y-%m-%d %H:%M:%S")
    except ValueError:
        return text


# --------------------------------------------------------------------------- io

def decode_bytes(data):
    """Decode text that should be UTF-8 but may come from a legacy console."""
    if data.startswith(b"\xff\xfe") or data.startswith(b"\xfe\xff"):
        return data.decode("utf-16", errors="replace")
    try:
        return data.decode("utf-8-sig")
    except UnicodeDecodeError:
        return data.decode(locale.getpreferredencoding(False) or "utf-8", errors="replace")


def read_text_file(path):
    with open(path, "rb") as handle:
        return decode_bytes(handle.read())


def read_stdin_text():
    return decode_bytes(sys.stdin.buffer.read())


def write_text_file(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as handle:
        handle.write(text)


def load_json(path, default=None):
    try:
        return json.loads(read_text_file(path))
    except FileNotFoundError:
        return default
    except (OSError, ValueError) as exc:
        raise QueueError("cannot read %s: %s" % (path, exc))


def save_json_atomic(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = "%s.%d.tmp" % (path, os.getpid())
    with open(tmp, "w", encoding="utf-8", newline="\n") as handle:
        json.dump(data, handle, indent=2, sort_keys=True)
        handle.write("\n")
    os.replace(tmp, path)


def no_window_kwargs():
    """Keep helper processes from flashing console windows on Windows."""
    if IS_WINDOWS:
        return {"creationflags": 0x08000000}  # CREATE_NO_WINDOW
    return {}


# --------------------------------------------------------------------------- paths

def env(name, default=None):
    """An environment setting: MOA7AMED_QUEUE_<NAME>, or CTQ_<NAME> from before the rebrand."""
    value = os.environ.get(ENV_PREFIX + name)
    if value is None:
        value = os.environ.get(LEGACY_ENV_PREFIX + name)
    return default if value is None else value


def default_home():
    return os.path.join(os.path.expanduser("~"), APP_DIR_NAME)


def legacy_home():
    return os.path.join(os.path.expanduser("~"), LEGACY_APP_DIR_NAME)


def home_info():
    """Where the data lives and why: (path, origin).

    origin is "setting" (MOA7AMED_QUEUE_HOME), "legacy-setting" (CTQ_HOME), "legacy"
    (the directory of an earlier version, used where it is) or "default".

    Data written by versions 0.1.0 and 0.2.0 is never moved or copied behind the
    user's back: while only the old directory exists it simply stays in use.
    `moa7amed-queue migrate` moves it on request.
    """
    for prefix, origin in ((ENV_PREFIX, "setting"), (LEGACY_ENV_PREFIX, "legacy-setting")):
        override = os.environ.get(prefix + "HOME")
        if override:
            return os.path.abspath(os.path.expanduser(override)), origin
    new, old = default_home(), legacy_home()
    if not os.path.isdir(new) and os.path.isdir(old):
        return old, "legacy"
    return new, "default"


def home():
    return home_info()[0]


def plugin_root():
    return os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def git_toplevel(path):
    try:
        result = subprocess.run(
            ["git", "-C", path, "rev-parse", "--show-toplevel"],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, stdin=subprocess.DEVNULL,
            timeout=20, **no_window_kwargs())
    except (OSError, subprocess.SubprocessError):
        return None
    if result.returncode != 0:
        return None
    top = result.stdout.decode("utf-8", errors="replace").strip()
    return top or None


def _too_broad(path):
    """A repository rooted at the home directory or a drive root (dotfiles, an
    accidental `git init`) is not "the project" of the folders beneath it."""
    if os.path.dirname(path) == path:
        return True
    try:
        return os.path.samefile(path, os.path.expanduser("~"))
    except OSError:
        return False


def resolve_project(explicit=None):
    """Map a directory to its queue. Returns (root, start_directory).

    Order: explicit argument, MOA7AMED_QUEUE_PROJECT (set by the runner for the tasks it
    starts), then the current directory. Inside a git repository the queue
    belongs to the repository root, so every subdirectory shares one queue and
    one workspace lock. A directory that merely sits below a repository rooted
    at the home directory or a drive root keeps its own queue and is treated
    as a plain (non-git) directory.
    """
    path = explicit or env("PROJECT") or os.getcwd()
    path = os.path.abspath(os.path.expanduser(path))
    if not os.path.isdir(path):
        raise QueueError("project directory does not exist: %s" % path)
    start = os.path.realpath(path)
    top = git_toplevel(path)
    if top:
        top = os.path.realpath(top)
        if _too_broad(top) and os.path.normcase(top) != os.path.normcase(start):
            top = None
    return (top or start), start


def resolve_project_root(explicit=None):
    return resolve_project(explicit)[0]


def relative_workdir(root, directory):
    """`directory` relative to `root` with forward slashes; '' if it is the root or outside it."""
    try:
        relative = os.path.relpath(os.path.realpath(directory), root)
    except (ValueError, OSError):
        return ""
    if relative == os.curdir or relative.startswith(os.pardir):
        return ""
    return relative.replace(os.sep, "/")


def project_key(root):
    norm = os.path.normcase(root).replace("\\", "/").rstrip("/") or "/"
    digest = hashlib.sha1(norm.encode("utf-8")).hexdigest()[:10]
    slug = re.sub(r"[^A-Za-z0-9._-]+", "-", os.path.basename(root.rstrip("\\/")))
    slug = slug.strip("-.")[:40] or "root"
    return "%s-%s" % (slug, digest)


def project_dir(root):
    return os.path.join(home(), "projects", project_key(root))


# --------------------------------------------------------------------------- processes

def pid_alive(pid):
    """True when a process with this id exists on this machine."""
    try:
        pid = int(pid)
    except (TypeError, ValueError):
        return False
    if pid <= 0:
        return False
    if IS_WINDOWS:
        import ctypes
        from ctypes import wintypes

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.OpenProcess.restype = wintypes.HANDLE
        kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        kernel32.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
        kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
        handle = kernel32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
        if not handle:
            return ctypes.get_last_error() == 5  # access denied: it exists
        try:
            code = wintypes.DWORD()
            if not kernel32.GetExitCodeProcess(handle, ctypes.byref(code)):
                return True
            return code.value == 259  # STILL_ACTIVE
        finally:
            kernel32.CloseHandle(handle)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True


def process_token(pid):
    """A value that identifies one particular process: when it started.

    Process ids are reused, especially after a reboot, so "a process with this id
    exists" does not mean "the process that took the lock is still running". Two
    processes that get the same id at different times have different tokens.
    Returns None when the start time cannot be read."""
    try:
        pid = int(pid)
    except (TypeError, ValueError):
        return None
    if pid <= 0:
        return None
    try:
        if IS_WINDOWS:
            import ctypes
            from ctypes import wintypes

            kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
            kernel32.OpenProcess.restype = wintypes.HANDLE
            kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
            kernel32.GetProcessTimes.argtypes = [wintypes.HANDLE] + [ctypes.POINTER(wintypes.FILETIME)] * 4
            kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
            handle = kernel32.OpenProcess(0x1000, False, pid)
            if not handle:
                return None
            try:
                times = [wintypes.FILETIME() for _ in range(4)]
                if not kernel32.GetProcessTimes(handle, *[ctypes.byref(item) for item in times]):
                    return None
                return str((times[0].dwHighDateTime << 32) | times[0].dwLowDateTime)
            finally:
                kernel32.CloseHandle(handle)
        stat = "/proc/%d/stat" % pid
        if os.path.exists(stat):
            with open(stat, "rb") as handle:
                # The 22nd field is the start time. The name (2nd field) may contain spaces,
                # brackets and bytes that are not text, so the file is read as bytes.
                return handle.read().rsplit(b")", 1)[1].split()[19].decode("ascii")
        # Elsewhere (macOS, BSD): `ps` prints the start time in the caller's language and
        # time zone, so both are fixed; the same process must give the same answer from a
        # terminal and from a program started by a desktop application.
        env = dict(os.environ)
        env.update({"LC_ALL": "C", "LANG": "C", "TZ": "UTC"})
        result = subprocess.run(["ps", "-o", "lstart=", "-p", str(pid)], stdout=subprocess.PIPE,
                                stderr=subprocess.DEVNULL, stdin=subprocess.DEVNULL, timeout=10,
                                env=env)
        return result.stdout.decode("utf-8", errors="replace").strip() or None
    except (OSError, IndexError, ValueError, subprocess.SubprocessError):
        return None


def process_alive(pid, token=None):
    """True when the process that had this id and this start token is still running."""
    if not pid_alive(pid):
        return False
    if token:
        current = process_token(pid)
        if current is not None and current != token:
            return False  # the id now belongs to a different process
    return True


def kill_tree(pid):
    """Terminate a process and everything it started."""
    if IS_WINDOWS:
        subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                       stdin=subprocess.DEVNULL, **no_window_kwargs())
        return
    import signal
    import time

    try:
        group = os.getpgid(pid)
    except OSError:
        return
    for sig in (signal.SIGTERM, signal.SIGKILL):
        try:
            os.killpg(group, sig)
        except OSError:
            return
        for _ in range(25):
            if not pid_alive(pid):
                return
            time.sleep(0.2)


# --------------------------------------------------------------------------- config

# key -> (kind, default, description). kind is one of: str, int, float, list, choice:<a|b>
CONFIG_SPEC = {
    "permission_mode": ("str", "acceptEdits",
                        "value for `claude --permission-mode` when the runner starts a task"),
    "model": ("str", None, "value for `claude --model` (unset: Claude Code's default)"),
    "allowed_tools": ("list", [], "values for `claude --allowedTools`"),
    "disallowed_tools": ("list", [], "values for `claude --disallowedTools`"),
    "max_budget_usd": ("float", None, "value for `claude --max-budget-usd`, per task"),
    "claude_args": ("list", [], "extra arguments appended to every `claude` invocation"),
    "claude_bin": ("str", None, "path or name of the Claude Code executable (default: claude)"),
    "timeout_minutes": ("float", 60, "stop a task that runs longer than this (0 = no limit)"),
    "on_dirty": ("choice:stop|stash|allow", "stop",
                 "what to do when the workspace has uncommitted changes before a task"),
    "workspace": ("choice:inplace|worktree", "inplace",
                  "run tasks in the project directory or in a dedicated git worktree"),
    "worktree_branch": ("str", "task-queue", "branch used by the dedicated worktree"),
    "commit": ("choice:auto|none", "auto", "commit each task's changes when it succeeds"),
    "max_attempts": ("int", 2, "attempts per task before it is marked failed"),
    # --- adaptive engineering workflow ---
    "workflow_mode": ("choice:auto|off", "auto",
                      "choose quick, standard or deep process per task (off: the 0.4 behaviour)"),
    "workflow_minimal_context": ("bool", True,
                                 "give a task only the approved project context it touches"),
    "workflow_agent_policy": ("choice:on_demand|never", "on_demand",
                              "subagents: only where the workflow profile justifies them, or never"),
    "workflow_reuse_approved_plan": ("bool", True,
                                     "reuse a current approved architecture instead of redoing it"),
    "workflow_risk_based_review": ("bool", True,
                                   "review depth follows risk (false: full review for every change)"),
    "workflow_enforce_verification": ("bool", True,
                                      "accept a finished task only with an MQ_VERIFIED report"),
    "workflow_deep_approval": ("choice:material|always|never", "material",
                               "deep tasks whose plan waits for your approval"),
    "workflow_max_escalations": ("int", 1, "how often a task's workflow may deepen"),
    "workflow_authority": ("choice:moa7amed|superpowers", "moa7amed",
                           "which workflow leads when Superpowers is also installed"),
    "workflow_superpowers": ("choice:auto|yes|no", "auto",
                             "whether Superpowers is installed (auto: ask Claude Code)"),
    # --- replica (application reconstruction) ---
    "replica_python": ("str", None, "Python interpreter with Playwright, for page captures "
                                    "(default: the one running moa7amed-queue)"),
    "replica_visual_threshold": ("float", 0.9, "pixel-comparison score at which a screen "
                                               "counts as close enough (0 to 1)"),
    "replica_pixel_tolerance": ("int", 24, "per-channel colour difference still counted as equal"),
    "replica_pixel_shift": ("int", 1, "a pixel also matches its neighbour this far away (0 or 1; "
                                      "absorbs anti-aliasing and one-pixel offsets)"),
    "replica_max_iterations": ("int", 5, "correction rounds of the improvement loop"),
    "replica_fixes_per_iteration": ("int", 3, "screens corrected per round"),
    "replica_budget_usd": ("float", None, "stop the improvement loop when the replica's tasks "
                                          "have cost this much (as reported)"),
    "replica_capture_timeout_s": ("int", 45, "time limit for capturing one page"),
    "replica_capture_wait_ms": ("int", 500, "pause after a page has loaded, before capturing"),
    "replica_hook_intake": ("bool", True, "a queued request to rebuild an application becomes "
                                          "a replica project instead of a single task"),
    # --- challenge, discuss, then execute ---
    "challenge_mode": ("choice:off|adaptive|explicit", "explicit",
                       "optional challenges: adaptive (rules check each task before it runs, "
                       "and a task may stop once to ask), explicit (only reviews you ask for), "
                       "off (none; approval gates and permissions are unchanged)"),
    "challenge_max_rounds": ("int", 1, "how often one task may be held for a decision; later "
                                       "concerns are recorded as advisories"),
    # --- adaptive multi-agent execution ---
    "agent_mode": ("choice:auto|single|parallel", "auto",
                   "workers for tasks whose work splits safely (single: never; parallel: whenever "
                   "a safe decomposition exists, whatever the expected benefit)"),
    "agent_max_parallel": ("int", 3, "agents running at the same time, per task (the coordinator "
                                      "never runs at the same time as its agents)"),
    "agent_write_policy": ("choice:coordinator_only", "coordinator_only",
                           "who writes: only the coordinator (agents are read-only; parallel "
                           "implementation writes are a future stage)"),
    "agent_timeout_seconds": ("int", 900, "time limit of one agent session"),
    "agent_max_attempts": ("int", 2, "attempts of one read-only agent (failures only)"),
    "agent_max_analysis_rounds": ("int", 1, "analysis rounds per task, across retries"),
    "agent_parallel_min_benefit": ("float", 0.25, "expected share of wall time saved below which "
                                                  "a task runs single (auto mode)"),
    "agent_budget_policy": ("choice:shared", "shared",
                            "workers share the task's budget; it is never multiplied"),
    "agent_reserve_share": ("float", 0.3, "share of the task's budget kept for the coordinator"),
    "bid_stage_models": ("choice:policy|auto", "policy",
                         "model tier of queued bid stages: policy (opus for the architecture, "
                         "sonnet otherwise) or auto (the router decides)"),
    "bid_hook_intake": ("bool", True, "a queued request for tender work becomes a bid project "
                                      "instead of a single task"),
    "plan_compliance": ("choice:block|warn", "block",
                        "a plan task that adds technology the approved plan does not cover: "
                        "fail it (block) or only record it (warn)"),
    "on_interrupted": ("choice:retry|hold", "retry",
                       "a task cut off by a crash: run it again (retry) or wait for you (hold)"),
    "retry_backoff_seconds": ("int", 30, "delay before attempt N+1 is N times this value"),
    "stale_lock_seconds": ("int", 900,
                           "a runner lock from another host is stale after this long without a heartbeat"),
    "capture_prefix": ("str", "queue:",
                       "prompt prefix the capture hook queues instead of sending to Claude (off = disabled)"),
    "backup_on_update": ("bool", True,
                         "back up settings and queues on the first run after the plugin was updated"),
    # --- cost, as reported by Claude Code (nothing is estimated) ---
    "cost_warn_task_usd": ("float", None,
                           "warn when one run of a task is reported to cost at least this much"),
    "cost_warn_run_usd": ("float", None,
                          "warn when one runner session is reported to cost at least this much"),
    "cost_limit_run_usd": ("float", None,
                           "start no further task once a runner session has cost this much"),
    # --- Auto Queue (per project, opt-in) ---
    "auto_queue_enabled": ("bool", False,
                           "start a runner automatically when a task is saved (this project "
                           "only; `moa7amed-queue auto enable`)"),
    "auto_queue_idle_timeout_seconds": ("int", 300,
                                        "an automatic runner exits after this long without a "
                                        "task (the next task starts one again)"),
    "auto_queue_launch_retry_limit": ("int", 3,
                                      "failed automatic launches in a row before automatic "
                                      "launching stops (0 = no limit)"),
    "auto_queue_budget_usd": ("float", None,
                              "automatic runs stop once this much has been reported since the "
                              "allowance was granted (0 or unset = no allowance)"),
    # --- automatic model routing ---
    "routing_enabled": ("bool", True, "choose a model for each task automatically"),
    "routing_mode": ("choice:balanced|quality_first|cost_optimized", "balanced",
                     "routing policy: balanced, quality_first or cost_optimized"),
    "routing_allow_manual_override": ("bool", True, "honour per-task model overrides"),
    "routing_allow_escalation": ("bool", True,
                                 "move a task to a stronger model when the evidence calls for it"),
    "routing_max_escalations": ("int", 1, "how many times one task may be escalated"),
    "routing_escalate_after_failures": ("int", 2,
                                        "failed attempts on one model before escalating"),
    "routing_escalation_attempts": ("int", 1,
                                    "extra attempts a task gets on the stronger model"),
    "routing_preserve_active_task_model": ("bool", True,
                                           "refuse model changes for a task that is running"),
    "routing_fallback_on_unavailable": ("bool", True,
                                        "use the nearest permitted model when one is unavailable"),
    "routing_model_opus": ("str", "opus", "model for the opus tier (alias or id; none = off)"),
    "routing_model_sonnet": ("str", "sonnet", "model for the sonnet tier (alias or id; none = off)"),
    "routing_model_haiku": ("str", "haiku", "model for the haiku tier (alias or id; none = off)"),
    "routing_available_models": ("list", [],
                                 "tiers or models this account may use (empty = no declared limit)"),
    "routing_classifier": ("choice:off|ambiguous|always", "ambiguous",
                           "ask a small model to confirm borderline (or all) rule-based decisions"),
    "routing_phased": ("choice:off|auto", "off",
                       "plan with opus, implement with sonnet, for very complex tasks"),
    "routing_phased_review": ("choice:auto|always|never", "auto",
                              "final opus review in the phased workflow"),
    "routing_unavailable_ttl_hours": ("int", 24,
                                      "retry a refused model after this many hours (0 = never)"),
}

# The names used in the documentation of the routing feature map onto the flat keys above.
CONFIG_ALIASES = {
    "modelrouting.enabled": "routing_enabled",
    "modelrouting.defaultmode": "routing_mode",
    "modelrouting.allowmanualoverride": "routing_allow_manual_override",
    "modelrouting.allowescalation": "routing_allow_escalation",
    "modelrouting.maxescalations": "routing_max_escalations",
    "modelrouting.preserveactivetaskmodel": "routing_preserve_active_task_model",
    "modelrouting.fallbackonunavailablemodel": "routing_fallback_on_unavailable",
}
_TRUE, _FALSE = ("true", "yes", "on", "1"), ("false", "no", "off", "0")


def config_key(name):
    """Resolve a setting name, accepting the `modelRouting.*` spellings."""
    return CONFIG_ALIASES.get(str(name).lower(), name)


def coerce_config_value(key, raw):
    if key not in CONFIG_SPEC:
        raise QueueError("unknown setting '%s' (known: %s)" % (key, ", ".join(sorted(CONFIG_SPEC))))
    kind = CONFIG_SPEC[key][0]
    if raw is None:
        return None
    try:
        if kind == "bool":
            if isinstance(raw, bool):
                return raw
            text = str(raw).strip().lower()
            if text in _TRUE:
                return True
            if text in _FALSE:
                return False
            raise ValueError("expected true or false")
        if kind == "int":
            value = int(raw)
            if value < 0:
                raise ValueError("must not be negative")
            return value
        if kind == "float":
            value = float(raw)
            if value < 0:
                raise ValueError("must not be negative")
            return value
        if kind == "list":
            if isinstance(raw, (list, tuple)):
                return [str(item) for item in raw if str(item) != ""]
            text = str(raw).strip()
            if text.startswith("["):
                return [str(item) for item in json.loads(text)]
            return split_list(text)
        if kind.startswith("choice:"):
            choices = kind.split(":", 1)[1].split("|")
            raw = str(raw).strip()
            relaxed = raw.lower().replace("-", "_").replace(" ", "_")
            if raw not in choices and relaxed in choices:
                raw = relaxed  # "Quality First" and "cost-optimized" are accepted
            if raw not in choices:
                raise ValueError("expected one of: %s" % ", ".join(choices))
            return raw
        value = str(raw)
        if value == "":
            raise ValueError("must not be empty")
        return value
    except (TypeError, ValueError) as exc:
        raise QueueError("invalid value for %s: %s" % (key, exc))


def split_list(text):
    """Split on commas that are not inside parentheses: 'Read,Bash(a, b)' -> 2 items."""
    items, depth, current = [], 0, ""
    for char in text:
        if char == "(":
            depth += 1
        elif char == ")":
            depth = max(0, depth - 1)
        if char == "," and depth == 0:
            items.append(current.strip())
            current = ""
        else:
            current += char
    items.append(current.strip())
    return [item for item in items if item]


def global_config_path():
    return os.path.join(home(), "config.json")


def project_config_path(pdir):
    return os.path.join(pdir, "config.json")


# Settings that only a project's own settings file may turn on: installing the plugin, or
# a global setting, must never make every project execute tasks by itself.
PROJECT_ONLY_KEYS = ("auto_queue_enabled",)


def effective_config(pdir=None, overrides=None):
    """defaults < global config < project config < overrides (None values are skipped)."""
    config = {key: spec[1] for key, spec in CONFIG_SPEC.items()}
    glob = dict(load_json(global_config_path(), {}) or {})
    for key in PROJECT_ONLY_KEYS:
        glob.pop(key, None)
    sources = [glob]
    if pdir:
        sources.append(load_json(project_config_path(pdir), {}) or {})
    sources.append(overrides or {})
    for source in sources:
        for key, value in source.items():
            if key in CONFIG_SPEC and value is not None:
                config[key] = coerce_config_value(key, value)
    return config
