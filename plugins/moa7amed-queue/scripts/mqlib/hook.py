"""UserPromptSubmit capture hook.

A prompt that starts with the capture prefix is stored as a task and blocked,
so it never reaches Claude and cannot interrupt the work in progress.

This runs on every prompt, so nothing heavy is imported until a prompt
actually matches.
"""
from __future__ import annotations

import json
import os
import sys

DEFAULT_PREFIX = "queue:"
# Claude Code gives a UserPromptSubmit hook 30 s; a hook that has not answered by then is
# cancelled and the prompt goes on to Claude. Everything the hook does is bounded well below
# that: the database wait, the workspace check, and starting a runner.
DB_WAIT_SECONDS = 8
START_WAIT_SECONDS = 12


def _env(name):
    """MOA7AMED_QUEUE_<NAME>, or CTQ_<NAME> from before the rebrand (see util.env)."""
    value = os.environ.get("MOA7AMED_QUEUE_" + name)
    return os.environ.get("CTQ_" + name) if value is None else value


def _home():
    """The data directory; the same rules as util.home_info, kept here so the common
    case of this hook imports nothing."""
    override = os.environ.get("MOA7AMED_QUEUE_HOME") or os.environ.get("CTQ_HOME")
    if override:
        return os.path.abspath(os.path.expanduser(override))
    user = os.path.expanduser("~")
    new, old = os.path.join(user, ".moa7amed-queue"), os.path.join(user, ".claude-task-queue")
    return old if not os.path.isdir(new) and os.path.isdir(old) else new


def refresh_plugin_pointer(force=False):
    """Keep the `moa7amed-queue install-cli` shims pointing at the plugin copy Claude Code is using.

    The install directory of a plugin changes with every update; the shims read
    the current location from <MOA7AMED_QUEUE_HOME>/plugin-root."""
    home = _home()
    if not force and not os.path.isdir(os.path.join(home, "bin")):
        return
    pointer = os.path.join(home, "plugin-root")
    root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    try:
        with open(pointer, "r", encoding="utf-8") as handle:
            current = handle.read().strip()
    except OSError:
        current = None
    if current != root:
        try:
            os.makedirs(home, exist_ok=True)
            with open(pointer, "w", encoding="utf-8", newline=chr(10)) as handle:
                handle.write(root + chr(10))
        except OSError:
            pass


def capture_prefix():
    """MOA7AMED_QUEUE_CAPTURE_PREFIX, else `capture_prefix` in the global settings, else "queue:"."""
    prefix = _env("CAPTURE_PREFIX")
    if prefix is None:
        try:
            with open(os.path.join(_home(), "config.json"), "rb") as handle:
                prefix = json.loads(handle.read().decode("utf-8-sig")).get("capture_prefix")
        except (OSError, ValueError, AttributeError):
            prefix = None
    if not isinstance(prefix, str):
        prefix = DEFAULT_PREFIX
    prefix = prefix.strip()
    return "" if prefix.lower() in ("off", "none", "false") else prefix


REPEAT_WINDOW_HOURS = 12


def _repeat_of_earlier_capture(q, session_id, body):
    """Claude Code puts a prompt the hook blocked back into the input box (observed with
    Claude Code 2.1.329, whether Claude was busy or idle). Pressing Enter again, or typing
    more after it, sends that text again. Returns None when `body` does not start with a
    recent capture of this session, else {"kind": "same" | "extends", "task_id", "added"}."""
    if not session_id:
        return None
    from . import util
    import datetime
    since = util.iso(util.now() - datetime.timedelta(hours=REPEAT_WINDOW_HOURS))
    rows = q.conn.execute(
        "SELECT id, prompt FROM tasks WHERE source='hook' AND session_id=? AND created_at>=? "
        "ORDER BY id DESC LIMIT 20", (session_id, since)).fetchall()
    for row in rows:
        earlier = row["prompt"]
        if body == earlier:
            return {"kind": "same", "task_id": row["id"], "added": ""}
        if body.startswith(earlier) and len(body) > len(earlier):
            return {"kind": "extends", "task_id": row["id"], "added": body[len(earlier):].strip()}
    return None


def _without_restored_text(q, session_id, body, prefix):
    """Separate a new request from text Claude Code restored into the input box.

    Returns (body to save or None, refusal reason or None, ids of tasks whose restored text
    was left out). The restored text of a saved task is never saved again. What follows it
    is saved only when it starts with the capture prefix itself, the explicit sign of a new
    request; anything else may be a change to that task or a message for Claude, so nothing
    is saved and nothing is sent, and the owner is told what happened."""
    left_out = []
    while True:
        found = _repeat_of_earlier_capture(q, session_id, body)
        if found is None:
            return body, None, left_out
        added = found["added"]
        if found["kind"] == "extends" and prefix and added.lower().startswith(prefix.lower()):
            left_out.append(found["task_id"])
            body = added[len(prefix):].strip()
            if not body:
                return None, ("NOT queued: nothing followed the second '%s' (the text before it "
                              "is task #%d, already saved). Press Esc twice to clear the input "
                              "box." % (prefix, found["task_id"])), left_out
            continue
        if found["kind"] == "same":
            return None, ("NOT queued again: this is the same text as task #%d, which was "
                          "already queued from this session. Claude Code leaves a captured "
                          "prompt in the input box; press Esc twice to clear it before typing "
                          "your next message. To queue the same work twice on purpose, use "
                          "`moa7amed-queue add`." % found["task_id"]), left_out
        shown = added[:200] + ("..." if len(added) > 200 else "")
        return None, ("NOT queued and NOT sent to Claude: this starts with the text of task #%d "
                      "(already saved; Claude Code left it in the input box), followed by more "
                      "text that does not start with '%s': \"%s\". That could be a change to "
                      "task #%d, a new task or a message for Claude, so nothing was saved and "
                      "nothing ran. Press Esc twice to clear the input box, then submit the new "
                      "part on its own: with '%s' in front to queue it, or without it to send "
                      "it to Claude. The text is kept in `moa7amed-queue history`."
                      % (found["task_id"], prefix, shown, found["task_id"], prefix)), left_out


def _replica_intake(q, body):
    """(project, None) when the queued text asks to rebuild an application and replica
    intake is on; (None, reason) for a repeat of a request already saved; (None, None)
    otherwise (the text is queued as an ordinary task)."""
    from . import replica_model, util
    if not util.effective_config(q.dir).get("replica_hook_intake", True):
        return None, None
    found = replica_model.intent(body)
    if found["intent"] not in ("REPLICA_VISUAL", "REPLICA_FUNCTIONAL", "REPLICA_FULL",
                               "REPLICA_DISCOVER"):
        return None, None
    from . import replica_store
    replica_store.ensure_schema(q)
    import datetime
    since = util.iso(util.now() - datetime.timedelta(hours=REPEAT_WINDOW_HOURS))
    earlier = q.conn.execute("SELECT id FROM replica_projects WHERE created_at>=? AND "
                             "(request=? OR substr(?, 1, length(request))=request) "
                             "ORDER BY created_at DESC", (since, body, body)).fetchone()
    if earlier is not None:
        return None, ("NOT saved again: this repeats the request of replica project '%s', "
                      "saved earlier (Claude Code leaves a captured prompt in the input box). "
                      "Continue with /moa7amed-queue:replica %s. Press Esc twice to clear the "
                      "input box." % (earlier["id"], earlier["id"]))
    project_id = replica_store.next_id(q)
    return replica_store.create(q, project_id, body), None


def _bid_intake(q, body):
    """Like _replica_intake, for tender work (BidStudio)."""
    from . import bid_model, util
    if not util.effective_config(q.dir).get("bid_hook_intake", True):
        return None, None
    found = bid_model.intent(body)
    if found["intent"] is None or found["mode"] not in bid_model.MODES:
        return None, None
    from . import bid_store
    bid_store.ensure_schema(q)
    import datetime
    since = util.iso(util.now() - datetime.timedelta(hours=REPEAT_WINDOW_HOURS))
    earlier = q.conn.execute("SELECT id FROM bid_projects WHERE created_at>=? AND "
                             "(request=? OR substr(?, 1, length(request))=request) "
                             "ORDER BY created_at DESC", (since, body, body)).fetchone()
    if earlier is not None:
        return None, ("NOT saved again: this repeats the request of bid project '%s', saved "
                      "earlier. Continue with /moa7amed-queue:bidstudio %s. Press Esc twice to "
                      "clear the input box." % (earlier["id"], earlier["id"]))
    return bid_store.create(q, bid_store.next_id(q), body), None


def _start_if_auto(q, task_id):
    """With Auto Queue on for this project, make sure a runner works on it. Returns the short
    status for the message, or None when the project uses the manual workflow. A failure here
    never costs the saved task."""
    import threading
    from . import autoqueue, util
    try:
        config = util.effective_config(q.dir)
        if not autoqueue.enabled(config):
            return None
    except Exception as exc:  # noqa: BLE001
        return "Automatic start failed: %s. Start the runner with /moa7amed-queue:queue-run." % exc
    outcome = {}

    def start():
        # Its own connection: the thread may outlive the hook's answer.
        from . import store
        try:
            own = store.Queue(q.root, timeout=DB_WAIT_SECONDS)
            try:
                outcome["result"] = autoqueue.ensure_runner(own, config, task_id)
            finally:
                own.close()
        except Exception as exc:  # noqa: BLE001 - the task is saved; say what went wrong
            outcome["error"] = exc

    worker = threading.Thread(target=start, daemon=True)
    worker.start()
    worker.join(START_WAIT_SECONDS)
    if worker.is_alive():
        return ("Starting a runner did not finish within %d s; it may still start. "
                "`moa7amed-queue auto` shows the state." % START_WAIT_SECONDS)
    if "error" in outcome:
        return ("Automatic start failed: %s. Start the runner with /moa7amed-queue:queue-run."
                % outcome["error"])
    return outcome["result"]["message"] or "Saved."


def _left_out_note(left_out):
    if not left_out:
        return ""
    return (" The text of task %s that Claude Code had left in the input box was not saved "
            "again." % ", ".join("#%d" % task_id for task_id in left_out))


def owner_decision(payload, prompt):
    """A prompt the owner typed while a queued task waits for their decision on a concern.
    When it clearly decides (no special phrase needed) and it is clear which concerns it
    answers, the decision is recorded; the prompt always goes on to Claude unchanged."""
    try:
        if not os.listdir(os.path.join(_home(), "open-decisions")):
            return 0
    except OSError:
        return 0
    from . import challenge, store, util
    intent = challenge.classify_decision(prompt)
    if intent is None:
        return 0
    try:
        project = (_env("PROJECT") or os.environ.get("CLAUDE_PROJECT_DIR") or payload.get("cwd"))
        root, _start = util.resolve_project(project)
        q = store.Queue(root, timeout=DB_WAIT_SECONDS)
    except Exception:  # noqa: BLE001 - an ordinary prompt must never fail because of this
        return 0
    try:
        open_rows = challenge.open_blocking(q)
        if not open_rows:
            challenge.update_marker(q)
            return 0
        ids, tasks = challenge.references(prompt)
        rows = ([row for row in open_rows if row["id"] in ids or row["task_id"] in tasks]
                if ids or tasks else open_rows)
        if not rows:
            return 0
        statement = challenge.capture_statement(q, prompt, payload.get("session_id"), intent)
        bundles = sorted({challenge.subject(row) for row in rows})
        if len(bundles) == 1:
            done = challenge.decide(q, rows, intent, "owner-prompt", response=prompt,
                                    statement_id=statement)
            lines = ["moa7amed-queue recorded the owner's decision from this message "
                     "(statement S%d):" % statement]
            for item in done:
                lines.append("- C%d, %s (%s): %s" % (
                    item["id"], item["subject"], item["concern"],
                    {"original": "proceed with the original approach; the trade-off is "
                                 "accepted for this task only",
                     "recommendation": "follow the recommendation",
                     "acknowledged": "a real limit, acknowledged; the step stays out of the "
                                     "task (accepting a risk cannot lift it)"}[item["decision"]]))
            lines.append("%s is released and goes ahead with this decision. Confirm it "
                         "briefly; do not argue the concern again."
                         % (bundles[0][0].upper() + bundles[0][1:]))
        else:
            lines = ["The owner's message reads as a decision (%s), but concerns of several "
                     "tasks are open (%s). Ask which task it is for; then record it with "
                     "`moa7amed-queue challenge decide <task> --statement %d`."
                     % (intent, ", ".join(bundles), statement)]
    except Exception:  # noqa: BLE001
        return 0
    finally:
        q.close()
    print(json.dumps({"hookSpecificOutput": {"hookEventName": "UserPromptSubmit",
                                             "additionalContext": "\n".join(lines)}}))
    return 0


def main():
    if _env("RUNNER"):
        return 0  # never capture inside a task started by the runner
    refresh_plugin_pointer()
    try:
        payload = json.loads(sys.stdin.buffer.read().decode("utf-8-sig", errors="replace") or "{}")
    except ValueError:
        return 0
    prompt = payload.get("prompt") if isinstance(payload, dict) else None
    if not isinstance(prompt, str):
        return 0
    prefix = capture_prefix()
    text = prompt.lstrip()
    if not prefix or not text.lower().startswith(prefix.lower()):
        return owner_decision(payload, prompt)

    from . import store, util  # only now: the prompt is ours to handle

    body = text[len(prefix):].strip()
    try:
        if not body:
            raise util.QueueError("nothing followed '%s'. Usage: %s <task to run later>"
                                  % (prefix, prefix))
        project = (_env("PROJECT") or os.environ.get("CLAUDE_PROJECT_DIR")
                   or payload.get("cwd"))
        root, start = util.resolve_project(project)
        try:   # as for every command: back the data up on the first run after an update, but
            from . import maintenance   # only when that is quick; else the next command does it
            maintenance.note_version(backup=maintenance.quick_to_back_up())
        except Exception:
            pass
        q = store.Queue(root, timeout=DB_WAIT_SECONDS)
        replica_project = None
        auto = None
        try:
            kept, repeat, left_out = _without_restored_text(q, payload.get("session_id"),
                                                            body, prefix)
            if repeat is not None:
                with q.tx():   # the owner can see later what was not saved, and why
                    q.log_event(None, None, "intake-refused",
                                "%s | submitted text: %s" % (repeat.split(":")[0], body[:2000]))
            else:
                body = kept
            bid_project = None
            if repeat is None:
                replica_project, repeat = _replica_intake(q, body)
            if repeat is None and replica_project is None:
                bid_project, repeat = _bid_intake(q, body)
            if repeat is None and replica_project is None and bid_project is None:
                # The task later runs in the directory the session was in when it was queued.
                workdir = (util.relative_workdir(root, payload.get("cwd") or start)
                           or util.relative_workdir(root, start))
                # Claude Code gives every prompt an id (prompt_id): the same submission seen
                # twice is saved once. Without an id, nothing is deduplicated here.
                prompt_id = payload.get("prompt_id")
                task_id = q.add(body, source="hook", session_id=payload.get("session_id"),
                                workdir=workdir,
                                request_id="prompt:%s" % prompt_id if prompt_id else None)
                if q.last_add_was_repeat:
                    repeat = ("Already saved as task #%d (the same prompt was submitted twice); "
                              "not saved again." % task_id)
                else:
                    pending = q.counts()["pending"]
                    auto = _start_if_auto(q, task_id)   # the task is committed by now
        finally:
            q.close()
        if repeat is not None:
            reason = repeat
        elif bid_project is not None:
            reason = ("Saved as bid project '%s' (%s mode): tender work is prepared step by step "
                      "from the tender documents, so it was NOT queued as a single task and NOT "
                      "sent to Claude. Continue with /moa7amed-queue:bidstudio %s and add the "
                      "tender documents. Press Esc twice to clear the input box."
                      % (bid_project["id"], bid_project["mode"], bid_project["id"]))
        elif replica_project is not None:
            reason = ("Saved as replica project '%s' (%s mode): a request to rebuild an "
                      "application is planned before anything is built, so it was NOT queued "
                      "as a single task and NOT sent to Claude. Continue with "
                      "/moa7amed-queue:replica %s when you are ready (reference analysis, "
                      "architecture and a plan you approve; the building then runs in the "
                      "queue). Press Esc twice to clear the input box."
                      % (replica_project["id"], replica_project["mode"], replica_project["id"]))
        elif auto is not None:
            reason = ("Task #%d saved for %s. %s%s Not sent to Claude, so the current work is not "
                      "interrupted. Claude Code leaves this text in the input box: press Esc "
                      "twice to clear it. Progress: /moa7amed-queue:queue-status."
                      % (task_id, os.path.basename(q.root), auto, _left_out_note(left_out)))
        else:
            reason = ("Queued as task #%d for %s (%d pending).%s It was saved to the task queue "
                      "and NOT sent to Claude, so the current work is not interrupted. Claude "
                      "Code leaves this text in the input box: press Esc twice to clear it "
                      "before your next message. Run the queue with /moa7amed-queue:queue-run "
                      "and inspect it with /moa7amed-queue:queue-status."
                      % (task_id, os.path.basename(q.root), pending, _left_out_note(left_out)))
    except Exception as exc:  # a prompt the user asked to queue must not reach Claude
        reason = ("moa7amed-queue could NOT save this prompt (%s). It was not sent to "
                  "Claude either; your text is shown below so you can submit it again." % exc)
    print(json.dumps({"decision": "block", "reason": reason}))
    return 0
