"""Which models can actually be used.

Claude Code has no command that lists the models an account may use, so this
module never claims to know. It combines three kinds of knowledge, from weakest
to strongest:

  * configuration: which model each tier maps to (aliases by default, so no
    version is hardcoded) and an optional allow-list
  * restrictions found in Claude Code settings (`availableModels`,
    `deniedModels`)
  * evidence: what the CLI reported after real runs, kept in
    <MOA7AMED_QUEUE_HOME>/models.json. A model is "confirmed" only after the CLI's own
    usage report named it, and "unavailable" only after the CLI refused it.

Everything else is "unknown": it is tried, and the result becomes evidence.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess

from . import util
from .util import QueueError

TIERS = ("haiku", "sonnet", "opus")  # ascending capability
_DISABLED = ("", "none", "off", "disabled", "false", "-")

# What `claude -p` prints when the requested model cannot be used. Observed with
# Claude Code 2.1.206 and 2.1.329: exit code 1, api_error_status 404 and the first phrase below.
_MODEL_ERROR = re.compile(
    r"issue with the selected model|"
    r"\bmodel\b[^.\n]{0,80}\b(does not exist|doesn'?t exist|not exist|not found|not available|"
    r"unavailable|not supported|unsupported|not permitted|not allowed|no access|"
    r"(do not|don'?t) have access)|"
    r"\b(invalid|unknown|unsupported|unrecognized) model\b|"
    r"not_found_error[^\n]{0,60}\bmodel\b", re.IGNORECASE)

# A model name is passed as its own argument, never through a shell, but it must still
# look like a model name: no spaces, quotes or leading dash that could pose as an option.
_SAFE_MODEL = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:@/\[\]-]{0,119}$")


def validate_model(name):
    name = (name or "").strip()
    if not _SAFE_MODEL.match(name):
        raise QueueError("'%s' is not a valid model name (letters, digits and . _ : @ / [ ] - "
                         "only, not starting with a dash)" % name)
    return name


def family_of(model):
    """haiku, sonnet or opus when the name says so, else None."""
    lowered = (model or "").lower()
    for tier in TIERS:
        if tier in lowered:
            return tier
    return None


def tier_model(config, tier):
    """The model a tier runs on, or None when that tier is switched off."""
    value = str(config.get("routing_model_%s" % tier) or "").strip()
    if value.lower() in _DISABLED:
        return None
    return value


def tier_of(config, model):
    """Which tier a selected value belongs to (a tier name, a configured tier model, or by family)."""
    if model in TIERS:
        return model
    for tier in TIERS:
        if tier_model(config, tier) == model:
            return tier
    return family_of(model)


def resolve_claude(name):
    """Find the Claude Code executable the way a shell would."""
    if os.path.isfile(name):
        return os.path.abspath(name)
    if util.IS_WINDOWS and not os.path.splitext(name)[1]:
        for extension in (".exe", ".cmd", ".bat", ".com"):
            found = shutil.which(name + extension)
            if found:
                return found
    found = shutil.which(name)
    if found:
        return found
    raise QueueError("the Claude Code executable '%s' was not found on PATH. Install Claude "
                     "Code, or set claude_bin / MOA7AMED_QUEUE_CLAUDE_BIN to its full path." % name)


# --------------------------------------------------------------------------- evidence store

def state_path():
    return os.path.join(util.home(), "models.json")


def load_state():
    try:
        state = util.load_json(state_path(), None)
    except QueueError:
        state = None
    if not isinstance(state, dict):
        state = {}
    state.setdefault("models", {})
    state.setdefault("cli", {})
    return state


def save_state(state):
    try:
        util.save_json_atomic(state_path(), state)
    except OSError:
        pass  # evidence is an optimisation; never fail a run because it cannot be saved


def note_confirmed(model, actual):
    state = load_state()
    state["models"][model] = {"status": "confirmed", "actual": actual, "at": util.now_iso()}
    save_state(state)


def note_unavailable(model, error, kind="unavailable"):
    state = load_state()
    state["models"][model] = {"status": kind, "error": (error or "")[:300], "at": util.now_iso()}
    save_state(state)


def reset(model=None):
    state = load_state()
    if model is None:
        state["models"] = {}
    else:
        state["models"].pop(model, None)
    save_state(state)


def evidence(model, config):
    """What past runs showed about a model: (status, detail). Refusals expire."""
    entry = load_state()["models"].get(model)
    if not entry:
        return "unknown", "never tried"
    if entry["status"] == "confirmed":
        return "confirmed", "ran as %s on %s" % (entry.get("actual") or model,
                                                 util.local_time(entry.get("at")))
    ttl = float(config.get("routing_unavailable_ttl_hours") or 0)
    if ttl:
        try:
            age = (util.now() - util.parse_iso(entry["at"])).total_seconds() / 3600.0
        except (KeyError, ValueError):
            age = ttl + 1
        if age > ttl:
            return "unknown", "refused %.0f hours ago; will be tried again" % age
    if entry["status"] == "mismatch":
        return "unavailable", "the CLI ran a different model instead (%s)" % entry.get("error", "")
    return "unavailable", entry.get("error") or "refused by the CLI"


# --------------------------------------------------------------------------- CLI capabilities

def cli_info(claude_bin, remember=True):
    """Version and model-selection support of the installed CLI (cached per executable).
    With `remember=False` nothing is read from or written to the data directory."""
    try:
        path = resolve_claude(claude_bin)
    except QueueError as exc:
        return {"path": None, "error": str(exc), "supports_model": False}
    try:
        stat = os.stat(path)
        key = "%s|%d|%d" % (path, int(stat.st_mtime), stat.st_size)
    except OSError:
        key = path
    state = load_state() if remember else {"cli": {}}
    cached = state["cli"].get(key)
    if cached:
        return cached
    info = {"path": path, "version": None, "supports_model": False, "checked_at": util.now_iso()}
    for argument, field in (("--version", "version"), ("--help", "help")):
        try:
            result = subprocess.run([path, argument], stdout=subprocess.PIPE,
                                    stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                                    timeout=60, **util.no_window_kwargs())
            text = result.stdout.decode("utf-8", errors="replace")
        except (OSError, subprocess.SubprocessError) as exc:
            info["error"] = "could not run `%s %s`: %s" % (claude_bin, argument, exc)
            return info
        if field == "version":
            match = re.search(r"\d+\.\d+\.\d+[\w.-]*", text)
            info["version"] = match.group(0) if match else text.strip()[:60]
        else:
            info["supports_model"] = bool(re.search(r"--model\b", text))
            info["supports_fallback_model"] = bool(re.search(r"--fallback-model\b", text))
    if remember:
        state["cli"] = {key: info}  # keep only the current executable
        save_state(state)
    return info


# --------------------------------------------------------------------------- restrictions

def _settings_files(project_root):
    """Claude Code settings files that may restrict models: (path, is_managed)."""
    config_dir = os.environ.get("CLAUDE_CONFIG_DIR") or os.path.join(os.path.expanduser("~"),
                                                                     ".claude")
    if util.IS_WINDOWS:
        managed = os.path.join(os.environ.get("ProgramFiles", r"C:\Program Files"), "ClaudeCode",
                               "managed-settings.json")
    elif os.uname().sysname == "Darwin":
        managed = "/Library/Application Support/ClaudeCode/managed-settings.json"
    else:
        managed = "/etc/claude-code/managed-settings.json"
    files = [(managed, True), (os.path.join(config_dir, "settings.json"), False)]
    if project_root:
        files += [(os.path.join(project_root, ".claude", "settings.json"), False),
                  (os.path.join(project_root, ".claude", "settings.local.json"), False)]
    return files


def settings_restrictions(project_root):
    """`availableModels` / `deniedModels` from Claude Code settings, best effort.

    A managed list replaces the others; otherwise the lists are combined, as documented.
    This is a pre-check only: what the CLI actually ran is verified after every task.
    """
    available, denied, sources, managed_available = [], [], [], None
    for path, is_managed in _settings_files(project_root):
        try:
            with open(path, "rb") as handle:
                data = json.loads(handle.read().decode("utf-8-sig"))
        except (OSError, ValueError):
            continue
        if not isinstance(data, dict):
            continue
        listed = data.get("availableModels")
        if isinstance(listed, list) and listed:
            sources.append(path)
            if is_managed:
                managed_available = [str(item) for item in listed]
            else:
                available += [str(item) for item in listed if str(item) not in available]
        blocked = data.get("deniedModels")
        if isinstance(blocked, list) and blocked:
            sources.append(path)
            denied += [str(item) for item in blocked if str(item) not in denied]
    if managed_available is not None:
        available = managed_available
    return {"available": available or None, "denied": denied, "sources": sorted(set(sources))}


def _entry_covers(entry, model):
    """Does one settings entry (family, version prefix or full id) cover this model?"""
    entry, model = entry.lower(), model.lower()
    if entry == model or model.startswith(entry):
        return True
    return entry in TIERS and family_of(model) == entry


def restriction(model, config, restrictions):
    """Why a model may not be used, or None. Only definite restrictions are reported."""
    declared = [str(item).lower() for item in (config.get("routing_available_models") or [])]
    if declared:
        tier = tier_of(config, model)
        if model.lower() not in declared and (tier or "") not in declared:
            return ("not in routing_available_models (%s)" % ", ".join(declared))
    for entry in restrictions.get("denied") or []:
        if _entry_covers(entry, model):
            return "denied by Claude Code settings (deniedModels: %s)" % entry
    allowed = restrictions.get("available")
    if allowed:
        family = family_of(model)
        covered = any(_entry_covers(entry, model) for entry in allowed)
        # An alias such as "opus" may resolve to a version the list allows; only a list
        # that names no model of that family at all is a definite "no".
        same_family = any(family and family_of(entry) == family for entry in allowed)
        if not covered and not same_family:
            return ("not in Claude Code's availableModels (%s)" % ", ".join(allowed))
    return None


def check(model, config, restrictions):
    """Can this model be used? Returns (True | False | None, explanation); None = not yet known."""
    reason = restriction(model, config, restrictions)
    if reason:
        return False, reason
    status, detail = evidence(model, config)
    if status == "unavailable":
        return False, detail
    return (True if status == "confirmed" else None), detail


# --------------------------------------------------------------------------- reading results

def is_model_error(parsed, stderr_tail=""):
    """True when the CLI's output says the requested model could not be used."""
    text = "%s\n%s" % ((parsed or {}).get("result") or "", stderr_tail or "")
    if _MODEL_ERROR.search(text):
        return True
    return (parsed or {}).get("api_error_status") == 404 and "model" in text.lower()


def model_error_text(parsed, stderr_tail=""):
    text = ((parsed or {}).get("result") or "").strip() or (stderr_tail or "").strip()
    return text.splitlines()[0][:300] if text else "the CLI rejected the model"


def actual_models(parsed):
    """Models the CLI reports having used, busiest first: [(model id, usage dict)]."""
    usage = (parsed or {}).get("model_usage")
    if not isinstance(usage, dict):
        return []
    entries = [(name, data if isinstance(data, dict) else {}) for name, data in usage.items()]
    entries.sort(key=lambda item: -(item[1].get("outputTokens") or 0))
    return entries


def verify(requested, config, parsed):
    """Compare what was asked for with what ran.

    Returns (actual model id or None, verdict) where verdict is "confirmed",
    "mismatch" (the CLI clearly ran a model of another family) or "unverified"
    (no usage report, or names that cannot be compared).
    """
    used = actual_models(parsed)
    if not used:
        return None, "unverified"
    primary = used[0][0]
    if not requested:
        return primary, "unverified"
    wanted = (tier_of(config, requested) if requested in TIERS or family_of(requested)
              else None)
    names = [name.lower() for name, _ in used]
    if requested.lower() in names or any(name.startswith(requested.lower()) for name in names):
        return primary, "confirmed"
    if wanted:
        if any(family_of(name) == wanted for name in names):
            match = next(name for name, _ in used if family_of(name) == wanted)
            return match, "confirmed"
        if family_of(primary):
            return primary, "mismatch"
    return primary, "unverified"


def usage_summary(parsed):
    """Token and cost figures exactly as the CLI reported them (nothing is estimated)."""
    summary = {}
    used = actual_models(parsed)
    if used:
        summary["models"] = {
            name: {key: data.get(key) for key in ("inputTokens", "outputTokens",
                                                  "cacheReadInputTokens",
                                                  "cacheCreationInputTokens", "costUSD")
                   if data.get(key) is not None}
            for name, data in used}
    for key in ("cost_usd", "num_turns", "duration_ms"):
        if (parsed or {}).get(key) is not None:
            summary[key] = parsed[key]
    usage = (parsed or {}).get("usage")
    if isinstance(usage, dict):
        tokens = {key: usage.get(key) for key in ("input_tokens", "output_tokens",
                                                  "cache_read_input_tokens",
                                                  "cache_creation_input_tokens")
                  if usage.get(key) is not None}
        if tokens:
            summary["tokens"] = tokens
    return summary


# --------------------------------------------------------------------------- policy helpers

def fallback_order(tier, floor):
    """Tiers to try when `tier` cannot be used, nearest first, never below `floor`."""
    order = {"opus": ["sonnet", "haiku"], "sonnet": ["opus", "haiku"],
             "haiku": ["sonnet", "opus"]}.get(tier, ["sonnet", "opus", "haiku"])
    minimum = TIERS.index(floor) if floor in TIERS else 0
    return [name for name in order if TIERS.index(name) >= minimum]


def escalation_order(tier):
    """Stronger tiers than `tier`, nearest first."""
    if tier not in TIERS:
        return []
    return list(TIERS[TIERS.index(tier) + 1:])


def probe(claude_bin, model, config):
    """Verify one model with a minimal real request. Returns (status, detail).

    This is the only way to confirm a model before a task needs it, and it makes a
    (very small) request on the user's account, so it only runs when asked for."""
    command = [resolve_claude(claude_bin), "-p", "--model", validate_model(model),
               "--output-format", "json", "--no-session-persistence", "--disable-slash-commands",
               "--strict-mcp-config", "--tools", "", "--system-prompt",
               "Reply with the single word OK."]
    env = dict(os.environ)
    # Marks the request as the queue's own, under the current name and the legacy one
    # (CTQ_RUNNER) that a capture hook from before the rebrand looks for.
    env["MOA7AMED_QUEUE_RUNNER"] = env["CTQ_RUNNER"] = "1"
    try:
        result = subprocess.run(command, input=b"OK?\n", stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE, timeout=180, env=env,
                                **util.no_window_kwargs())
    except (OSError, subprocess.SubprocessError) as exc:
        return "unknown", "the probe could not run: %s" % exc
    raw = result.stdout.decode("utf-8", errors="replace").strip()
    stderr = result.stderr.decode("utf-8", errors="replace").strip()
    try:
        payload = json.loads(raw)
        if isinstance(payload, list):
            payload = [item for item in payload if isinstance(item, dict)][-1]
    except (ValueError, IndexError):
        payload = {"result": raw}
    parsed = {"result": payload.get("result") or "", "model_usage": payload.get("modelUsage"),
              "api_error_status": payload.get("api_error_status")}
    if is_model_error(parsed, stderr):
        message = model_error_text(parsed, stderr)
        note_unavailable(model, message)
        return "unavailable", message
    if result.returncode != 0 or payload.get("is_error"):
        return "unknown", "the probe failed for another reason: %s" % (
            model_error_text(parsed, stderr))
    actual, verdict = verify(model, config, parsed)
    if verdict == "mismatch":
        note_unavailable(model, "asked for %s, the CLI ran %s" % (model, actual), kind="mismatch")
        return "unavailable", "the CLI ran %s instead" % actual
    if verdict == "confirmed":
        note_confirmed(model, actual)
        return "confirmed", "runs as %s" % actual
    return "unknown", "the CLI answered but did not report which model ran"
