"""Command line interface: `moa7amed-queue <command>`."""
from __future__ import annotations

import argparse
import json
import os
import sys

from . import (DESCRIPTION, LEGACY_COMMAND, NAME, PUBLISHER, TAGLINE, __version__, engine,
               gitops, hook, maintenance, models, pyrunner, routing, store, util)
from .util import QueueError

EPILOG = """\
Tasks are stored per project in %(home)s (override with MOA7AMED_QUEUE_HOME).
A project is the git repository root of the current directory, or the directory itself.
Priorities: low, normal, high, urgent or any integer; higher runs first, ties run in the
order they were added.

`%(legacy)s` is kept as an alias of this command: it was the name before version 0.3.0.
"""

TOOL_KEYS = ("allowed_tools", "disallowed_tools")
OPTION_KEYS = ("routing_enabled", "routing_mode",
               "permission_mode", "model", "allowed_tools", "disallowed_tools",
               "max_budget_usd", "timeout_minutes", "on_dirty", "workspace", "worktree_branch",
               "commit", "claude_bin", "claude_args", "retry_backoff_seconds")


# --------------------------------------------------------------------------- output

def emit(args, data, text):
    """Print JSON when --json was given, otherwise the human-readable text."""
    if getattr(args, "json", False):
        print(json.dumps(data, indent=2, default=str))
    elif text:
        print(text)


def _clip(text, width):
    text = " ".join((text or "").split())
    return text if len(text) <= width else text[:max(0, width - 3)] + "..."


def task_note(q, task, state):
    if state == "waiting":
        return "after " + ", ".join("#%d" % dep["id"] for dep in q.deps(task["id"])
                                    if dep["status"] != "done")
    if state == "blocked":
        needs = ", ".join("#%d (%s)" % (dep["id"], dep["status"]) for dep in q.deps(task["id"])
                          if dep["status"] in ("failed", "cancelled"))
        if needs:
            return "needs " + needs
        reason = q.model_block(task["id"]) or ""
        if reason.startswith(("approval:", "agents:", "decision:")):   # a hold, not a model problem
            return _clip(reason, 110)
        return "no usable model: " + _clip(reason, 110)
    if state == "backoff":
        return "retry at " + util.local_time(task["not_before"])
    if state == "failed":
        return "error: " + _clip(task["last_error"], 90)
    if state == "done" and task["commit_sha"]:
        return "commit " + task["commit_sha"][:10]
    return ""


def task_table(q, tasks):
    if not tasks:
        return "  (no tasks)"
    lines = ["  %4s  %-8s %-9s %-5s %-13s %s" % ("ID", "PRIORITY", "STATE", "TRIES", "MODEL",
                                                  "TITLE")]
    for task in tasks:
        state = q.state_of(task)
        note = task_note(q, task, state)
        lines.append("  %4d  %-8s %-9s %-5s %-13s %s%s" % (
            task["id"], store.priority_label(task["priority"]), state,
            "%d/%d" % (task["attempts"], task["max_attempts"]),
            _clip(routing.short_label(q, task), 13),
            _clip(task["title"], 56), "  [%s]" % note if note else ""))
    return "\n".join(lines)


_SELECTED_BY = {"auto": "automatic", "manual": "manual override",
                "fallback": "automatic, after a fallback",
                "escalation": "automatic, after an escalation"}


def _history_line(entry):
    """One execution-history entry as text."""
    kind = entry.get("type")
    if kind == "escalation":
        return "escalated %s -> %s: %s" % (entry.get("from"), entry.get("to"), entry.get("reason"))
    if kind == "fallback":
        return "fallback %s -> %s: %s" % (entry.get("from"), entry.get("to"), entry.get("reason"))
    if kind == "override":
        return "override %s -> %s" % (entry.get("from"), entry.get("to"))
    if kind == "review":
        if entry.get("error"):
            return "model review skipped: %s" % entry["error"]
        return "model review by %s: %s%s" % (
            entry.get("model"), entry.get("result"),
            " (was %s)" % entry["from"] if entry.get("from") else " (confirmed)")
    if kind == "blocked":
        return "blocked: %s" % entry.get("reason")
    if kind == "phased":
        return "phased workflow %s%s" % ("on" if entry.get("enabled") else "off",
                                         ": %s" % entry["reason"] if entry.get("reason") else "")
    if kind == "restored":
        return "back to %s (was on the fallback %s)" % (entry.get("to"), entry.get("from"))
    if kind == "reclassified":
        return "reclassified as %s (policy %s)" % (entry.get("to"), entry.get("policy"))
    if "run" in entry:
        text = "run %s%s: asked for %s" % (
            entry["run"], " (%s)" % entry["phase"] if entry.get("phase") else "",
            entry.get("requested") or "the CLI default")
        if entry.get("actual"):
            text += ", ran on %s (%s)" % (entry["actual"], entry.get("verified"))
        text += " -> %s" % entry.get("outcome")
        if entry.get("cost_usd") is not None:
            text += ", $%.4f" % entry["cost_usd"]
        return text
    return json.dumps(entry)


def routing_card(q, task, with_history=True):
    """Model routing of one task as a small report."""
    info = routing.describe(q, task)
    state = q.state_of(task)
    lines = ["Task #%d - %s" % (task["id"], _clip(task["title"], 70))]
    if info is None:
        lines += ["  Model:       not classified yet (decided when the task starts)",
                  "  Status:      %s" % state]
        return "\n".join(lines)
    selected = info["selectedModel"] or "-"
    if info["phased"]:
        selected = ("phased: %s in every phase%s" if info["selectedBy"] == "manual"
                    else "phased: opus plans, %s implements%s") % (
            selected, "; now in the %s phase" % info["phase"] if info["phase"] else "")
    lines.append("  Model:       %s (%s)" % (
        selected, _SELECTED_BY.get(info["selectedBy"], info["selectedBy"])))
    if info["recommendedModel"] and info["recommendedModel"] != info["selectedModel"]:
        lines.append("  Recommended: %s" % info["recommendedModel"])
    lines.append("  Reason:      %s" % (info["modelReason"] or "-"))
    lines.append("  Complexity:  %s/100 (policy %s, confidence %s, classified by %s)" % (
        info["complexityScore"], info["routingPolicyVersion"].split("/")[-1],
        info["confidence"], info["classifier"]))
    lines.append("  Override:    %s" % ("yes, set manually" if info["manualOverride"] else "no"))
    lines.append("  Actual:      %s" % (info["actualModel"] or "not run yet"))
    lines.append("  Escalations: %d" % info["escalationCount"])
    if info["blockedReason"]:
        lines.append("  BLOCKED:     %s" % info["blockedReason"])
    costs = [run["usage"].get("cost_usd") for run in info["runs"]
             if run["usage"].get("cost_usd") is not None]
    if costs:
        lines.append("  Usage:       $%.4f over %d run(s), as reported by the CLI" % (
            sum(costs), len(costs)))
    elif info["runs"]:
        lines.append("  Usage:       not reported by the CLI")
    from . import workflow
    flow = workflow.describe(q, task["id"])
    if flow:
        lines.append("  Workflow:    %s (%s); capabilities: %s; agents: %s" % (
            flow["profile"].upper(), flow["reason"] or "-", ", ".join(flow["active"])
            or "verification", flow["agents"]))
    from . import agents
    agent_line = agents.short_line(q, task["id"])
    if agent_line:
        lines.append("  Agents:      %s" % agent_line)
    from . import challenge
    concerns = challenge.summary_line(q, task["id"])
    if concerns:
        lines.append("  Concerns:    %s (`moa7amed-queue challenge show %d`)" % (concerns, task["id"]))
    lines.append("  Status:      %s" % state)
    if with_history and info["executionModelHistory"]:
        lines.append("  History:")
        lines += ["    - " + _history_line(entry) for entry in info["executionModelHistory"]]
    return "\n".join(lines)


def runner_line(info):
    if info is None:
        return "not running"
    where = "pid %s on %s" % (info["pid"], info["host"])
    if not info["alive"]:
        return ("STALE lock left by a runner that is gone (%s); the next run recovers it "
                "automatically, or use `moa7amed-queue recover`" % where)
    text = "running (%s, %s, since %s)" % (info["kind"] or "runner", where,
                                           util.local_time(info["started_at"]))
    if info["current_task_id"]:
        text += ", working on task #%d" % info["current_task_id"]
    return text


def counts_line(counts):
    return ", ".join("%d %s" % (counts[name], name)
                     for name in ("pending", "running", "done", "failed", "cancelled"))


# --------------------------------------------------------------------------- queue commands

def cmd_add(q, args):
    if args.prompt_file:
        prompt = util.read_text_file(args.prompt_file)
    elif args.stdin or args.text == ["-"]:
        prompt = util.read_stdin_text()
    elif args.text:
        prompt = " ".join(args.text)
    else:
        raise QueueError("nothing to queue: pass the prompt as arguments, --prompt-file FILE, "
                         "or --stdin")
    acceptance = args.acceptance
    if args.acceptance_file:
        acceptance = util.read_text_file(args.acceptance_file)
    # Remember the directory the task was queued from, so it later runs there.
    workdir = util.relative_workdir(q.root, os.getcwd()) or util.relative_workdir(q.root, q.start)
    task_id = q.add(prompt, acceptance=acceptance, title=args.title,
                    priority=store.parse_priority(args.priority),
                    after=store.parse_id_list(args.after), max_attempts=args.max_attempts,
                    source=args.source, session_id=args.session_id, workdir=workdir,
                    model=args.model, phased=True if args.phased else None)
    task = q.get(task_id)
    text = "Queued task #%d [%s] %s" % (task_id, store.priority_label(task["priority"]),
                                        task["title"])
    from . import challenge
    config = util.effective_config(q.dir)
    if challenge.mode(config) == "adaptive":
        challenge.gate(q, task, config)
        for row in challenge.for_task(q, task_id):
            if row["status"] == "open" and row["kind"] == "restriction":
                text += "\n  limit C%d: %s\n    %s\n    acknowledge: `moa7amed-queue challenge " \
                        "decide C%d --original` (the step stays out of the task)" % (
                            row["id"], row["concern"], row["recommendation"] or "-", row["id"])
            elif row["status"] == "open":
                text += "\n  concern C%d: %s\n    recommendation: %s\n    decide: " \
                        "`moa7amed-queue challenge decide C%d --recommendation` or `--original`" % (
                            row["id"], row["concern"], row["recommendation"] or "-", row["id"])
            elif row["status"] == "advisory":
                text += "\n  advisory: %s (no decision needed)" % row["concern"]
    info = routing.describe(q, task)
    if info is not None:
        text += "\n  model: %s%s - %s (complexity %s/100)" % (
            "phased, " if info["phased"] else "", info["selectedModel"],
            "manual override" if info["manualOverride"] else info["modelReason"],
            info["complexityScore"])
    prerequisites = q.deps(task_id)
    if prerequisites:
        text += "\n  runs after: " + ", ".join("#%d" % dep["id"] for dep in prerequisites)
    if task["acceptance"]:
        text += "\n  acceptance criteria recorded (%d characters)" % len(task["acceptance"])
    text += "\n  queue: %s (%s)" % (os.path.basename(q.root), counts_line(q.counts()))
    emit(args, q.task_dict(task), text)
    return 0


def cmd_list(q, args):
    statuses = None
    if args.status:
        statuses = [s.strip() for s in args.status.split(",") if s.strip()]
    tasks = q.tasks(statuses)
    emit(args, [q.task_dict(task) for task in tasks], task_table(q, tasks))
    return 0


def cmd_status(q, args):
    info = engine.lock_info(q)
    counts = q.counts()
    open_tasks = q.tasks(["running", "pending", "failed"])
    recent = q.conn.execute("SELECT * FROM tasks WHERE status='done' "
                            "ORDER BY finished_at DESC, id DESC LIMIT 5").fetchall()
    order = [task["id"] for task in q.execution_order()]
    data = {"project": q.root, "data_dir": q.dir, "runner": info, "counts": counts,
            "next": order, "tasks": [q.task_dict(task) for task in open_tasks],
            "recently_done": [q.task_dict(task) for task in recent]}
    config = util.effective_config(q.dir)
    if config["model"]:
        routing_text = "fixed model %s for every task (per-task overrides still apply)" % config["model"]
    elif config["routing_enabled"]:
        routing_text = "automatic, %s policy  (* manual, a>b moved, ! blocked)" % config["routing_mode"]
    else:
        routing_text = "off (tasks use Claude Code's default model)"
    data["routing"] = {"enabled": config["routing_enabled"], "mode": config["routing_mode"],
                       "fixed_model": config["model"]}
    lines = ["Queue   %s  (%s)" % (os.path.basename(q.root), q.root),
             "Runner  %s" % runner_line(info),
             "Tasks   %s" % counts_line(counts),
             "Models  %s" % routing_text]
    if util.home_info()[1] == "legacy":
        lines.append("Data    %s (from before 0.3.0, still fully supported; `%s migrate` moves "
                     "it to %s)" % (util.home(), NAME, util.default_home()))
    if order:
        lines.append("Next    " + ", ".join("#%d" % task_id for task_id in order[:10]))
    hold = engine._reconcile_hold(q)
    if hold:
        data["held"] = {"task_id": hold.get("task_id"), "paths": hold["paths"]}
        lines.append("Held    task #%s finished after a crash; its changes are not committed. "
                     "`%s reconcile` shows them and what to do." % (hold.get("task_id"), NAME))
    lines += ["", task_table(q, open_tasks)]
    if recent:
        lines += ["", "Recently done:", task_table(q, list(reversed(recent)))]
    emit(args, data, "\n".join(lines))
    return 0


def cmd_show(q, args):
    task = q.get(args.id)
    data = q.task_dict(task, with_runs=True)
    data["events"] = [dict(event) for event in q.events(task["id"], 200)]
    if getattr(args, "json", False):
        emit(args, data, None)
        return 0
    state = q.state_of(task)
    lines = ["Task #%d  %s" % (task["id"], task["title"]),
             "  state      %s%s" % (state, "  [%s]" % task_note(q, task, state)
                                    if task_note(q, task, state) else ""),
             "  priority   %s" % store.priority_label(task["priority"]),
             "  attempts   %d of %d" % (task["attempts"], task["max_attempts"]),
             "  added      %s (via %s)" % (util.local_time(task["created_at"]), task["source"]),
             "  runs in    %s" % os.path.normpath(os.path.join(q.root, task["workdir"]))]
    prerequisites = q.deps(task["id"])
    if prerequisites:
        lines.append("  after      " + ", ".join("#%d (%s)" % (dep["id"], dep["status"])
                                                 for dep in prerequisites))
    followers = q.dependents(task["id"])
    if followers:
        lines.append("  needed by  " + ", ".join("#%d" % dep["id"] for dep in followers))
    if task["result_summary"]:
        lines.append("  result     %s" % task["result_summary"])
    if task["commit_sha"]:
        lines.append("  commit     %s" % task["commit_sha"])
    if task["last_error"] and state != "done":
        lines.append("  last error %s" % task["last_error"])
    lines += ["", "Original prompt:", _indent(task["prompt"]), "",
              "Acceptance criteria:", _indent(task["acceptance"] or "(none recorded)"),
              "", "Model routing:", _indent("\n".join(routing_card(q, task).splitlines()[1:]))]
    runs = q.runs(task["id"])
    if runs:
        lines += ["", "Runs:"]
    for run in runs:
        lines.append("  run %d, attempt %d: %s  (%s -> %s)" % (
            run["id"], run["attempt"], run["outcome"] or "in progress",
            util.local_time(run["started_at"]), util.local_time(run["finished_at"])))
        used = q.conn.execute("SELECT * FROM run_routing WHERE run_id=?", (run["id"],)).fetchone()
        if used is not None:
            lines.append("      model:   asked for %s%s, ran on %s" % (
                used["model_arg"] or "the CLI default",
                " (%s phase)" % used["phase"] if used["phase"] else "",
                "%s (%s)" % (used["actual_model"], used["verified"]) if used["actual_model"]
                else "unknown (not reported)"))
        if run["error"]:
            lines.append("      error:   %s" % run["error"])
        if run["commit_sha"]:
            lines.append("      commit:  %s" % run["commit_sha"])
        files = json.loads(run["files_changed"] or "[]")
        if files:
            lines.append("      changed: %s" % ", ".join(files[:12])
                         + (" (+%d more)" % (len(files) - 12) if len(files) > 12 else ""))
        if run["cost_usd"] is not None:
            lines.append("      cost:    $%.4f" % run["cost_usd"])
        if run["session_id"]:
            lines.append("      session: %s  (inspect with: claude --resume %s)"
                         % (run["session_id"], run["session_id"]))
        if run["stdout_file"]:
            lines.append("      output:  %s" % run["stdout_file"])
    print("\n".join(lines))
    return 0


def _indent(text):
    return "\n".join("    " + line for line in (text or "").splitlines())


def cmd_history(q, args):
    events = q.events(args.id, args.limit)
    data = [dict(event) for event in events]
    lines = []
    for event in events:
        lines.append("%s  %-5s %-15s %s" % (
            util.local_time(event["ts"]),
            "#%d" % event["task_id"] if event["task_id"] else "-",
            event["type"], _clip(event["detail"], 150)))
    emit(args, data, "\n".join(lines) or "(no history yet)")
    return 0


def cmd_priority(q, args):
    ids = store.parse_id_list(args.ids)
    level = store.parse_priority(args.level)
    q.set_priority(ids, level)
    order = [task["id"] for task in q.execution_order()]
    emit(args, {"updated": ids, "priority": level, "next": order},
         "Priority of %s set to %s.\nNext up: %s" % (
             ", ".join("#%d" % i for i in ids), store.priority_label(level),
             ", ".join("#%d" % i for i in order[:10]) or "(nothing runnable)"))
    return 0


def cmd_depend(q, args):
    add = store.parse_id_list(args.on)
    remove = store.parse_id_list(args.remove)
    if not add and not remove:
        raise QueueError("use --on IDS to add prerequisites or --remove IDS to drop them")
    if add:
        q.add_deps(args.id, add)
    if remove:
        q.remove_deps(args.id, remove)
    now = [dep["id"] for dep in q.deps(args.id)]
    emit(args, {"task_id": args.id, "depends_on": now},
         "Task #%d now runs after: %s" % (args.id, ", ".join("#%d" % i for i in now) or "nothing"))
    return 0


def cmd_retry(q, args):
    ids = store.parse_id_list(args.ids)
    if args.failed:
        ids += [task["id"] for task in q.tasks(["failed"]) if task["id"] not in ids]
    if not ids:
        raise QueueError("nothing to retry: give task ids or --failed" if not args.failed
                         else "there are no failed tasks")
    retried = q.retry(ids, args.max_attempts)
    emit(args, {"retried": retried},
         "Back in the queue: %s\nStart the runner to execute them."
         % ", ".join("#%d" % i for i in retried))
    return 0


def cmd_cancel(q, args):
    ids = store.parse_id_list(args.ids)
    q.cancel(ids)
    blocked = sorted({dep["id"] for i in ids for dep in q.dependents(i)
                      if dep["status"] == "pending"})
    text = "Cancelled: %s" % ", ".join("#%d" % i for i in ids)
    if blocked:
        text += "\nNow blocked (they depend on a cancelled task): %s" % ", ".join(
            "#%d" % i for i in blocked)
    emit(args, {"cancelled": ids, "blocked": blocked}, text)
    return 0


def cmd_projects(args):
    projects = store.all_projects()
    rows = []
    for info in projects:
        entry = {"root": info["root"], "key": info.get("key"), "exists": os.path.isdir(info["root"])}
        if entry["exists"]:
            q = store.Queue(info["root"])
            entry["counts"] = q.counts()
            lock = engine.lock_info(q)
            entry["runner"] = "running" if lock and lock["alive"] else ("stale" if lock else "idle")
            q.close()
        rows.append(entry)
    lines = []
    for entry in rows:
        if entry["exists"]:
            lines.append("%s\n    %s; runner %s" % (entry["root"], counts_line(entry["counts"]),
                                                    entry["runner"]))
        else:
            lines.append("%s\n    (directory no longer exists)" % entry["root"])
    emit(args, rows, "\n".join(lines) or "No project has a queue yet.")
    return 0


# --------------------------------------------------------------------------- model routing

def cmd_model(q, args):
    """Manual model override for tasks, or for the whole project."""
    choice = args.model.strip()
    lowered = choice.lower()
    if lowered in ("auto", "automatic") + models.TIERS:
        choice = "auto" if lowered.startswith("auto") else lowered
    config = util.effective_config(q.dir)
    ids = store.parse_id_list(args.ids)
    if args.pending:
        ids += [task["id"] for task in q.tasks(["pending"]) if task["id"] not in ids]
    if args.whole_project:
        if ids:
            raise QueueError("use either task ids or --whole-project, not both")
        if not config["routing_allow_manual_override"]:
            raise QueueError("manual model overrides are switched off "
                             "(routing_allow_manual_override)")
        path = util.project_config_path(q.dir)
        current = util.load_json(path, {}) or {}
        if choice == "auto":
            current.pop("model", None)
            text = ("This project is back to automatic model selection: each task runs on "
                    "its routed model.")
        else:
            model = models.tier_model(config, choice) if choice in models.TIERS else \
                models.validate_model(choice)
            if model is None:
                raise QueueError("the %s tier is switched off (routing_model_%s)" % (choice, choice))
            current["model"] = model
            text = ("Every task of this project now runs on %s unless it has its own override. "
                    "Claude Code's own default model was not changed." % model)
        util.save_json_atomic(path, current)
        emit(args, {"scope": "project", "model": current.get("model")}, text)
        return 0
    if not ids:
        raise QueueError("which tasks? Give task ids, --pending for every pending task, or "
                         "--whole-project for the whole project")
    changed = routing.set_model(q, ids, choice, config)
    data = [{"id": i, "routing": routing.describe(q, q.get(i))} for i in changed]
    if choice == "auto":
        text = "Automatic selection restored for %s:" % ", ".join("#%d" % i for i in changed)
        text += "".join("\n  #%d -> %s (%s)" % (item["id"], item["routing"]["selectedModel"],
                                                item["routing"]["modelReason"]) for item in data)
    else:
        text = "Model of %s set to %s (manual override). Other tasks and Claude Code's default " \
               "model are unchanged." % (", ".join("#%d" % i for i in changed), choice)
    emit(args, data, text)
    return 0


def cmd_route(q, args):
    """Show (or recompute) the routing decision of tasks."""
    ids = store.parse_id_list(args.ids)
    config = util.effective_config(q.dir)
    if ids:
        tasks = [q.get(i) for i in ids]
    else:
        tasks = q.tasks(None if args.all else ["running", "pending", "failed"])
    if args.phased:
        if not ids:
            raise QueueError("--phased needs task ids")
        for task in tasks:
            routing.set_phased(q, task["id"], args.phased == "on")
    if args.reclassify:
        done = routing.reclassify(q, [task["id"] for task in tasks], config)
        if not getattr(args, "json", False):
            print("Reclassified under the %s policy: %s\n" % (
                config["routing_mode"], ", ".join("#%d" % i for i in done) or "nothing"))
    else:
        with q.tx():  # classify tasks that predate routing, so the report is complete
            for task in tasks:
                if routing.get(q, task["id"]) is None and task["status"] in ("pending", "failed"):
                    routing.classify_and_store(q, task, config)
    data = [{"id": task["id"], "title": task["title"], "state": q.state_of(task),
             "routing": routing.describe(q, task)} for task in tasks]
    emit(args, data, "\n\n".join(routing_card(q, task) for task in tasks) or "(no tasks)")
    return 0


def cmd_models(q, args):
    """What is known about model availability on this machine."""
    config = util.effective_config(q.dir)
    claude_bin = config["claude_bin"] or util.env("CLAUDE_BIN") or "claude"
    if args.reset:
        models.reset()
        cleared = routing.unblock(q)
        if not getattr(args, "json", False):
            print("Forgot what earlier runs showed about models%s.\n" % (
                "; unblocked %s" % ", ".join("#%d" % i for i in cleared) if cleared else ""))
    info = models.cli_info(claude_bin)
    restrictions = models.settings_restrictions(q.root)
    tiers = []
    for tier in reversed(models.TIERS):
        model = models.tier_model(config, tier)
        entry = {"tier": tier, "model": model}
        if model is None:
            entry.update(status="off", detail="switched off (routing_model_%s)" % tier)
        else:
            if args.probe:
                entry["probe"] = models.probe(claude_bin, model, config)
            ok, detail = models.check(model, config, restrictions)
            entry.update(status="confirmed" if ok else "unavailable" if ok is False else "unknown",
                         detail=detail)
        tiers.append(entry)
    data = {"cli": info, "restrictions": restrictions, "tiers": tiers,
            "policy": config["routing_mode"], "enabled": config["routing_enabled"]}
    lines = ["Claude Code  %s%s" % (
        info.get("version") or "not found",
        "  (%s)" % info["path"] if info.get("path") else "  %s" % info.get("error", "")),
        "Selection    %s" % ("per task, with `claude --model`" if info.get("supports_model")
                             else "this CLI has no --model option: tasks use its default model"),
        "Routing      %s, %s policy" % ("on" if config["routing_enabled"] else "off",
                                        config["routing_mode"]),
        "", "  %-7s %-28s %-12s %s" % ("TIER", "MODEL", "STATUS", "EVIDENCE")]
    for entry in tiers:
        lines.append("  %-7s %-28s %-12s %s" % (entry["tier"], entry["model"] or "-",
                                                entry["status"], entry["detail"]))
    if restrictions["available"] or restrictions["denied"]:
        lines += ["", "Claude Code settings restrict models:"]
        if restrictions["available"]:
            lines.append("  availableModels: %s" % ", ".join(restrictions["available"]))
        if restrictions["denied"]:
            lines.append("  deniedModels:    %s" % ", ".join(restrictions["denied"]))
        lines += ["  from: %s" % path for path in restrictions["sources"]]
    lines += ["", "Claude Code has no command that lists the models an account may use. "
              "\"unknown\" means not tried yet: the model is tried when a task needs it and the "
              "outcome is remembered. `moa7amed-queue models --probe` checks every tier now with one tiny "
              "request each."]
    emit(args, data, "\n".join(lines))
    return 0


# --------------------------------------------------------------------------- settings

def _arguments_for_claude(argv):
    """`config set claude_args --flag value ...`: the value of this one setting consists of
    options meant for `claude`, which the option parser would take for its own. Everything
    after the setting's name is therefore the value, in the order given, except this
    command's own options (written in full). Returns (the arguments left to parse, value)."""
    for index in range(len(argv) - 2):
        if argv[index:index + 3] == ["config", "set", "claude_args"]:
            break
    else:
        return argv, None
    kept, value, rest = [], [], argv[index + 3:]
    position = 0
    while position < len(rest):
        item = rest[position]
        if item in ("--global", "--json", "-h", "--help"):
            kept.append(item)
        elif item == "--project" and position + 1 < len(rest):
            kept += rest[position:position + 2]
            position += 1
        elif item.startswith("--project="):
            kept.append(item)
        elif item != "--":
            value.append(item)
        position += 1
    return argv[:index + 3] + kept, value


def cmd_config(args):
    pdir = None
    root = None
    if not args.use_global or args.action == "show":
        try:
            root = util.resolve_project_root(args.project)
            pdir = util.project_dir(root)
        except QueueError:
            if args.action != "show":
                raise
    path = util.global_config_path() if args.use_global else util.project_config_path(pdir)
    if args.action == "show":
        config = util.effective_config(pdir)
        lines = ["Effective settings for %s" % (root or "(no project)"),
                 "  global file:  %s" % util.global_config_path()]
        if pdir:
            lines.append("  project file: %s" % util.project_config_path(pdir))
        lines.append("")
        for key in sorted(util.CONFIG_SPEC):
            lines.append("  %-22s %-14s %s" % (key, json.dumps(config[key]),
                                               util.CONFIG_SPEC[key][2]))
        emit(args, config, "\n".join(lines))
        return 0
    if util.config_key(args.key or "") == "capture_prefix" and not args.use_global:
        raise QueueError("capture_prefix applies to every project; add --global")
    if args.key is not None:
        args.key = util.config_key(args.key)
    current = util.load_json(path, {}) or {}
    if args.action == "set":
        if args.key is None or not args.value:
            raise QueueError("usage: moa7amed-queue config set KEY VALUE [--global]")
        raw = args.value if len(args.value) > 1 else args.value[0]
        current[args.key] = util.coerce_config_value(args.key, raw)
    else:
        if args.key is None:
            raise QueueError("usage: moa7amed-queue config unset KEY [--global]")
        if args.key not in util.CONFIG_SPEC:
            raise QueueError("unknown setting '%s'" % args.key)
        current.pop(args.key, None)
    util.save_json_atomic(path, current)
    emit(args, current, "Saved %s" % path)
    return 0


# --------------------------------------------------------------------------- upkeep

_MARKS = {"ok": "ok  ", "warn": "WARN", "fail": "FAIL"}


def _check_lines(checks):
    return ["  [%s] %-20s %s" % (_MARKS.get(check["status"], "?   "), check["name"],
                                 check["detail"]) for check in checks]


def _claude_bin():
    try:
        return util.effective_config().get("claude_bin") or util.env("CLAUDE_BIN") or "claude"
    except QueueError:
        return util.env("CLAUDE_BIN") or "claude"


def cmd_update(args):
    """Report on updates. The update itself is done by Claude Code, never by this command."""
    status = maintenance.update_status(_claude_bin(), refresh=args.refresh)
    status["preflight"] = maintenance.preflight()
    if status.get("compatibility"):
        status["preflight"].insert(0, status["compatibility"])
    status["commands"] = maintenance.update_commands(status)
    lines = ["%s %s (%s channel)" % (NAME, __version__, status["channel"] or "unknown")]
    installed, market = status["installed"], status["marketplace"]
    if installed:
        lines.append("  installed:    %s, version %s, %s scope, %s"
                     % (installed["id"], installed["version"], installed["scope"],
                        "enabled" if installed["enabled"] else "DISABLED"))
    if market:
        where = market.get("repo") or market.get("url") or market.get("path") or ""
        ref = " at %s" % market["ref"] if market.get("ref") else ""
        lines.append("  marketplace:  %s (%s %s%s)" % (market["name"], market.get("source"),
                                                      where, ref))
        if market.get("last_refreshed"):
            lines.append("  listing from: %s%s" % (
                util.local_time(market["last_refreshed"]),
                "" if status["refreshed"] else "  (--refresh asks the marketplace again)"))
        offered = status["available_version"]
        if offered and not status.get("comparable"):
            lines.append("  offered:      %s  (cannot be compared with the installed %s)"
                         % (offered, installed["version"] if installed else "version"))
        elif offered and status["update_available"]:
            lines.append("  offered:      %s  ->  an update is available" % offered)
        elif offered and installed and offered != installed["version"]:
            lines.append("  offered:      %s  (older than the installed version)" % offered)
        elif offered:
            lines.append("  offered:      %s  ->  up to date" % offered)
        auto, blocked = status["auto_update"], status["auto_update_switched_off_by"]
        lines.append("  auto-update:  %s" % (
            "on, but switched off in this environment by %s" % ", ".join(blocked)
            if auto and blocked else "on" if auto else "off" if auto is False else
            "off (Claude Code updates a third-party marketplace automatically only after "
            "you turn that on)"))
    for other in status["legacy_installed"]:
        lines.append("  also installed under its old name: %s (`claude plugin uninstall %s` "
                     "removes it; its data is kept)" % (other, other))
    for note in status["notes"]:
        lines.append("  note: %s" % note)
    if status["whats_new"]:
        lines += ["", "What is new"]
        for entry in status["whats_new"]:
            lines += ["  %s" % entry["version"], _indent(entry["text"])]
    lines += ["", "Before updating"] + _check_lines(status["preflight"])
    if market:
        lines += ["", "To update (Claude Code does the update)" if status["update_available"]
                  else "When a new version is offered, update with (Claude Code does the update)"]
        lines += ["  %s" % command for command in status["commands"]]
        if not status["auto_update"]:
            lines += ["", "To have Claude Code update this plugin by itself: run /plugin, open the "
                      "Marketplaces tab,", "select %s and turn on auto-update." % market["name"]]
    emit(args, status, "\n".join(lines))
    return 0


def cmd_doctor(args):
    checks = maintenance.doctor(_claude_bin())
    failed = [check for check in checks if check["status"] == "fail"]
    warned = [check for check in checks if check["status"] == "warn"]
    verdict = ("%d problem(s) found" % len(failed) if failed else
               "healthy, %d note(s)" % len(warned) if warned else "healthy")
    emit(args, {"healthy": not failed, "checks": checks},
         "\n".join(["%s %s: %s" % (NAME, __version__, verdict)] + _check_lines(checks)))
    return 1 if failed else 0


def _backup_line(item):
    tasks = sum(sum(project.get("tasks", {}).values()) for project in item.get("projects", []))
    return "  %-44s %s, %d project(s), %d task(s)%s" % (
        item["name"], util.local_time(item.get("created_at")), len(item.get("projects", [])),
        tasks, "  [%s]" % item["label"] if item.get("label") else "")


def cmd_backup(args):
    if args.list:
        found = maintenance.list_backups()
        emit(args, found, "\n".join(["Backups in %s" % maintenance.backups_dir()]
                                    + [_backup_line(item) for item in found])
             if found else "No backups yet. `%s backup` makes one." % NAME)
        return 0
    made = maintenance.create_backup(label=args.label, output=args.output)
    emit(args, made, "Backup written to %s\n%s\nRestore it with: %s restore %s"
         % (made["path"], _backup_line(made), NAME, made["name"]))
    return 0


def cmd_restore(args):
    plan = maintenance.restore_backup(args.name, confirmed=args.yes)
    lines = ["Backup %s (made %s by version %s)" % (plan["backup"],
                                                    util.local_time(plan["created_at"]),
                                                    plan["version"]),
             "  settings: %s" % (", ".join(plan["files"]) or "none"),
             "  queues:   %s" % (", ".join(plan["projects"]) or "none")]
    if plan["restored"]:
        lines.append("Restored. The state from before the restore was saved as %s."
                     % plan["safety_backup"])
    else:
        lines.append("Nothing was changed. Add --yes to replace the current settings and the "
                     "queues listed above with this backup (the current state is backed up "
                     "first).")
    emit(args, plan, "\n".join(lines))
    return 0


def cmd_changelog(args):
    entries = maintenance.changelog_entries()
    if args.since and maintenance.parse_version(args.since) is None:
        raise QueueError("'%s' is not a version (expected something like 0.3.0)" % args.since)
    if args.since:
        entries = [(version, text) for version, text in entries
                   if maintenance.is_newer(version, args.since)]
    elif not args.all:
        entries = entries[:1]
    data = [{"version": version, "text": text} for version, text in entries]
    emit(args, data, "\n\n".join("%s %s\n%s" % (NAME, version, text) for version, text in entries)
         or "no changelog entries%s" % (" newer than %s" % args.since if args.since else ""))
    return 0


def cmd_usage(q, args):
    data = maintenance.usage(q, days=args.days)
    config = util.effective_config(q.dir)
    data["limits"] = {key: config[key] for key in ("max_budget_usd", "cost_warn_task_usd",
                                                   "cost_warn_run_usd", "cost_limit_run_usd")}
    lines = ["Cost of %s%s, as reported by Claude Code" % (
        q.root, " in the last %d day(s)" % args.days if args.days else ""),
        "  runs: %d (%d with a reported cost, %d without)" % (
            data["runs"], data["runs_with_cost"], data["runs_without_cost"]),
        "  tasks: $%.4f    model reviews of routing decisions: $%.4f" % (
            data["total_cost_usd"], data["review_cost_usd"]),
        "  workers of multi-agent tasks: $%.4f over %d session(s)%s" % (
            data["worker_cost_usd"], data["workers"]["runs"],
            "; %d without a reported cost (unknown, not zero)" % data["workers"]["without_cost"]
            if data["workers"]["without_cost"] else "")]
    if data["by_model"]:
        lines += ["", "  %-34s %5s %10s %12s %12s" % ("model", "runs", "cost", "input tok",
                                                      "output tok")]
        for name, bucket in sorted(data["by_model"].items(), key=lambda item: -item[1]["cost_usd"]):
            lines.append("  %-34s %5d %10s %12d %12d" % (
                _clip(name, 34), bucket["runs"], "$%.4f" % bucket["cost_usd"],
                bucket["input_tokens"], bucket["output_tokens"]))
    if data["by_task"]:
        lines += ["", "  most expensive tasks"]
        for task in data["by_task"][:8]:
            lines.append("  #%-4d %9s  %d run(s)  %s" % (task["task_id"], "$%.4f" % task["cost_usd"],
                                                        task["runs"], _clip(task["title"], 60)))
    limits = ["%s = %s" % (key, value) for key, value in data["limits"].items() if value is not None]
    lines += ["", "  limits: %s" % (", ".join(limits) if limits else
                                    "none set (see `%s config`: max_budget_usd, cost_warn_task_usd, "
                                    "cost_warn_run_usd, cost_limit_run_usd)" % NAME)]
    emit(args, data, "\n".join(lines))
    return 0


def cmd_reconcile(q, args):
    chosen = [name for name in ("commit", "stash", "release") if getattr(args, name)]
    if len(chosen) > 1:
        raise QueueError("choose one of --commit, --stash, --release")
    report = engine.reconcile_report(q)
    if chosen and not args.yes:
        report["would_do"] = chosen[0]
        lines = _reconcile_lines(report)
        lines += ["", "Nothing was changed. Add --yes to %s." % {
            "commit": "commit the files changed while the task ran (and only those)",
            "stash": "set the task's uncommitted files aside in a git stash",
            "release": "lift the hold and leave the files as they are"}[chosen[0]]]
        emit(args, report, "\n".join(lines))
        return 0
    if chosen:
        result = engine.reconcile_resolve(q, chosen[0])
        emit(args, result, result["message"])
        return 0 if result["done"] else 1
    emit(args, report, "\n".join(_reconcile_lines(report)))
    return 0


def _reconcile_lines(report):
    lines = ["Project  %s" % report["project"],
             "Runner   %s" % runner_line(report["runner"]),
             "Blocked  %s" % (report["blocked"] or "nothing: the queue can run")]
    if report["open_runs"]:
        lines += ["", "Runs that were never recorded:"]
        lines += ["  task #%d (run %d): %s" % (run["task_id"], run["run_id"],
                  "Claude had finished; the result is recorded, not re-run, by the next runner"
                  if run["finished_by_claude"] else "cut off; the next runner handles it as "
                  "on_interrupted=%s says" % report["on_interrupted"]) for run in report["open_runs"]]
    hold = report["hold"]
    if hold:
        lines += ["", "Task #%s finished before its runner stopped. Its files in %s:"
                  % (hold["task_id"], hold["workspace"])]
        for item in hold["files"]:
            lines.append("  %-10s %-40s %s" % ("uncommitted" if item["uncommitted"] else "committed",
                                              item["path"], item["why"]))
    if report["other_changes"]:
        lines += ["", "Other uncommitted changes (not attributed to a queued task):"]
        lines += ["  %s" % path for path in report["other_changes"][:20]]
    if report["held_tasks"]:
        lines += ["", "Cut off by a crash and held for review (on_interrupted=hold):"]
        lines += ["  #%d %s" % (task["task_id"], task["title"]) for task in report["held_tasks"]]
    if report["actions"]:
        lines += ["", "What you can do:"] + ["  %s" % action for action in report["actions"]]
    return lines


# --------------------------------------------------------------------------- planning

def _finding_lines(findings):
    marks = {"error": "ERROR", "warning": "warn ", "info": "info "}
    return ["  [%s] %-12s %s" % (marks.get(item["level"], item["level"]), item["where"],
                                 item["message"]) for item in findings]


def cmd_project(q, args):
    from . import plan_discover, plan_model, plan_report, plan_store
    action = args.project_action
    if action == "new":
        plan = plan_store.create(q, args.id, args.title or args.id, args.mode, args.summary or "")
        emit(args, plan, "Created plan %s (%s project). Next: `%s project discover %s` for an "
             "existing project, then record requirements and infrastructure."
             % (args.id, args.mode, NAME, args.id))
        return 0
    if action == "list":
        rows = plan_store.plans(q)
        emit(args, rows, "\n".join("  %-20s r%-3d %s" % (row["id"], row["revision"], row["title"])
                                   for row in rows) or "no plans in this project")
        return 0
    plan_id = args.id
    if action == "show":
        plan = plan_store.load(q, plan_id)
        if args.section:
            plan = {args.section: plan.get(args.section)}
        print(json.dumps(plan, indent=2, ensure_ascii=False))
        return 0
    if action == "apply":
        if args.json_text is not None:
            text = args.json_text
        elif args.file:
            text = util.read_text_file(args.file)
        else:
            text = util.read_stdin_text()
        try:
            fragment = json.loads(text)
        except ValueError as exc:
            raise QueueError("the plan fragment is not valid JSON: %s" % exc)
        plan = plan_store.apply(q, plan_id, fragment, args.note or "applied")
        findings = plan_model.validate(plan)
        errors = [item for item in findings if item["level"] == "error"]
        emit(args, {"plan": plan_id, "findings": findings},
             "Saved plan %s (draft revision %s): %d error(s), %d warning(s)%s" % (
                 plan_id, plan_store.load(q, plan_id)["_revision"], len(errors),
                 len([item for item in findings if item["level"] == "warning"]),
                 "\n" + "\n".join(_finding_lines(errors)) if errors else ""))
        return 0
    if action == "discover":
        found = plan_discover.discover(q.root)
        machine = plan_discover.developer_machine()
        plan = plan_store.load(q, plan_id)
        kept = [item for item in plan.get("facts") or [] if item.get("source") != "discovery"]
        fragment = {"facts": [], "existing_components": found["existing_components"],
                    "infrastructure": {"developer_machine": machine}, "mode": "existing"}
        plan["facts"] = kept
        plan_store.save(q, plan_id, plan, "discovery reset")
        fragment["facts"] = found["facts"]
        plan_store.apply(q, plan_id, fragment, "discovery: %d facts" % len(found["facts"]))
        lines = ["Discovered %d fact(s) in %s (%d files scanned):" % (
            len(found["facts"]), q.root, found["summary"]["files_scanned"])]
        lines += ["  %-4s %-14s %s" % (item["id"], item["status"].lower(), item["statement"])
                  for item in found["facts"]]
        lines += ["", "Existing technologies: %s" % (", ".join(found["existing_components"])
                                                     or "none recognised"),
                  "Developer machine recorded separately (%s, %s CPUs, %s GB RAM): it is not the "
                  "deployment target." % (machine.get("os"), machine.get("cpu_count"),
                                          machine.get("ram_gb", "?"))]
        emit(args, found, "\n".join(lines))
        return 0
    if action == "env":
        plan_store.set_env(q, plan_id, args.scenario, args.field, args.value, args.status,
                           args.source, args.label)
        emit(args, {"ok": True}, "Recorded %s.%s = %s (%s)" % (args.scenario, args.field,
                                                             args.value, args.status))
        return 0
    if action == "target":
        plan = plan_store.load(q, plan_id)
        if args.scenario != "none" and args.scenario not in plan_model.by_id(
                plan_model.scenarios(plan)):
            raise QueueError("no scenario %s in plan %s" % (args.scenario, plan_id))
        plan_store.apply(q, plan_id, {"infrastructure": {"target": None if args.scenario == "none"
                                                         else args.scenario}},
                         "target: %s" % args.scenario)
        emit(args, {"target": args.scenario}, "Deployment target: %s" % args.scenario)
        return 0
    if action == "depth":
        plan = plan_store.load(q, plan_id)
        if args.depth == "auto":
            fragment = {"depth": None, "depth_source": None}
        else:
            fragment = {"depth": args.depth, "depth_source": "manual"}
        plan_store.apply(q, plan_id, fragment, "depth: %s" % args.depth)
        depth, reasons = plan_model.planning_depth(plan_store.load(q, plan_id))
        models = plan_model.stage_models(depth)
        emit(args, {"depth": depth, "reasons": reasons, "models": models},
             "Planning depth: %s (%s)\nSuggested model tier per stage: %s" % (
                 depth, "; ".join(reasons), ", ".join("%s %s" % item for item in models.items())))
        return 0
    if action == "check":
        plan = plan_store.load(q, plan_id)
        plan.pop("_revision", None)
        findings = plan_model.validate(plan)
        depth, reasons = plan_model.planning_depth(plan)
        decisions = []
        for decision in plan.get("decisions") or []:
            status, why = plan_model.suggested_status(plan, decision)
            options = {}
            for option in decision.get("options") or []:
                options[option["id"]] = {scenario: result["verdict"] for scenario, result in
                                         plan_model.evaluate_option(plan, option).items()}
            decisions.append({"id": decision["id"], "status": decision.get("status"),
                              "evidence_says": status, "missing": why,
                              "compatibility": options, "matrix": plan_model.matrix(decision)})
        data = {"plan": plan_id, "stage": plan_model.stage(plan,
                                                           plan_store.execution_revision(q, plan_id)),
                "depth": depth, "findings": findings, "decisions": decisions,
                "capacity": plan_model.capacity((plan.get("capacity") or {}).get("inputs")),
                "readiness": plan_model.readiness(plan)}
        lines = ["Plan %s: %s; planning depth %s" % (plan_id, data["stage"], depth)]
        for item in decisions:
            lines.append("  %s %s (evidence says %s)" % (item["id"], item["status"],
                                                        item["evidence_says"]))
            for option, verdicts in item["compatibility"].items():
                lines.append("      %-12s %s" % (option, ", ".join("%s: %s" % pair for pair in
                                                                   verdicts.items()) or "no scenarios"))
            for reason in item["missing"]:
                lines.append("      missing: %s" % reason)
            if item["matrix"]["note"]:
                lines.append("      matrix: %s" % item["matrix"]["note"])
        lines += _finding_lines(findings) or ["  no findings"]
        emit(args, data, "\n".join(lines))
        return 1 if any(item["level"] == "error" for item in findings) else 0
    if action == "approve":
        result = plan_store.approve(q, plan_id, decisions=args.decision or None,
                                    execution=not args.decisions_only, note=args.note,
                                    confirmed=args.yes)
        lines = ["Plan %s, revision %s:" % (plan_id, result["revision"])]
        for item in result["decisions"]:
            lines.append("  approve %s" % item)
            for assumption in result.get("assumptions", {}).get(item, []):
                lines.append("      rests on %s" % assumption)
        if not result["decisions"]:
            lines.append("  no decision to approve")
        lines += ["  NOT approvable %s: %s" % (item["decision"], "; ".join(item["reasons"]))
                  for item in result["refused"]]
        if result.get("message"):
            lines.append(result["message"])
        elif result["approved"]:
            lines.append("Approved%s. Revision %s is frozen (sha256 %s...)." % (
                " for execution" if result["execution"] else " (decisions only)",
                result["revision"], result["sha256"][:12]))
        else:
            lines.append("Nothing was changed. Add --yes to approve%s." % (
                " these decisions and the plan for execution" if result["execution"]
                else " these decisions"))
        emit(args, result, "\n".join(lines))
        return 0 if result["approved"] or not args.yes else 1
    if action == "execute":
        report = plan_store.execute(q, plan_id, confirmed=args.yes)
        lines = ["Plan %s, approved revision %s:" % (plan_id, report["revision"])]
        for key, label in (("added", "add"), ("unchanged", "already queued"),
                           ("replaced", "replace (the task changed)"),
                           ("held", "hold"), ("cancelled", "cancel (now held)"),
                           ("kept_done", "keep (done)"), ("running", "leave (running)")):
            for item in report[key]:
                lines.append("  %-26s %s%s%s" % (label, item["task"],
                                                 " -> #%s" % item["queue_task"]
                                                 if item.get("queue_task") else "",
                                                 ": %s" % (item.get("reason") or item.get("note"))
                                                 if item.get("reason") or item.get("note") else ""))
        lines.append("Imported. Run the queue as usual (`%s run`)." % NAME if report["applied"]
                     else "Nothing was changed. Add --yes to import.")
        emit(args, report, "\n".join(lines))
        return 0
    if action == "status":
        data = plan_store.progress(q, plan_id)
        lines = ["Plan %s: %s" % (plan_id, data["title"]),
                 "Stage    %s" % data["stage"],
                 "Revision approved for execution: %s; draft revision %s" % (
                     data["execution_revision"] or "none", data["draft_revision"]),
                 "Depth    %s (%s)" % (data["depth"][0], "; ".join(data["depth"][1]))]
        for milestone in data["milestones"]:
            lines.append("  %s %-40s %d/%d done" % (milestone["id"], milestone["title"],
                                                   milestone["done"], milestone["total"]))
        lines.append("")
        for row in data["tasks"]:
            lines.append("  %-6s %-13s %s%s" % (row["task"], row["state"], row["title"],
                                                " (%s)" % row["reason"] if row["reason"] else ""))
        for item in data["findings"]:
            lines.append("  compliance %s %s: %s" % (item["level"], item["plan_task_id"],
                                                   item["message"]))
        emit(args, data, "\n".join(lines))
        return 0
    if action == "revise":
        result = plan_store.revise(q, plan_id, args.issue, args.task or [], args.decision or [])
        lines = ["Recorded issue %s." % result["issue"]["id"],
                 "Affected tasks: %s" % (", ".join(result["affected"]) or "none"),
                 "Completed and kept: %s" % (", ".join(result["completed_kept"]) or "none"),
                 "To re-plan: %s" % (", ".join(result["to_replan"]) or "none"),
                 "Next: %s" % result["next"]]
        emit(args, result, "\n".join(lines))
        return 0
    if action == "report":
        plan = plan_store.load(q, plan_id)
        plan.pop("_revision", None)
        if args.to_project:
            folder = os.path.join(q.root, ".moa7amed", "plans", plan_id)
        else:
            folder = args.out or os.path.join(q.dir, "plans", plan_id)
        written = plan_report.export(plan, folder, plan_model.validate(plan),
                                     plan_store.progress(q, plan_id),
                                     plan_store.approvals(q, plan_id))
        emit(args, {"files": written}, "Wrote %d file(s) to %s%s" % (
            len(written), folder, "\nThey are in the project: commit them, or the next task "
            "stops for uncommitted changes." if args.to_project else ""))
        return 0
    raise QueueError("unknown project action %s" % action)


def _workflow_lines(info):
    lines = ["Task #%d - %s" % (info["task"], _clip(info["title"], 70)),
             "  Workflow:     %s (%s)%s" % (info["profile"].upper(), info["reason"] or "-",
                                            "" if info["source"] in ("auto", "plan")
                                            else ", " + info["source"]),
             "  Model:        %s" % (info["model"] or "decided when the task starts"),
             "  Capabilities: %s" % (", ".join(info["active"]) or "verification"),
             "  Agents:       %s" % info["agents"],
             "  Planning:     %s" % info["planning"],
             "  Verification: %s" % info["verification"]]
    if info["runs"]:
        lines.append("  Usage:        %s over %d run(s), as reported by Claude Code" % (
            "$%.4f" % info["reported_cost_usd"] if info["reported_cost_usd"] is not None
            else "cost not reported", info["runs"]))
    for entry in info["history"][-5:]:
        if entry.get("type") == "escalation":
            lines.append("  Escalated:    %s -> %s (%s)" % (entry["from"], entry["to"],
                                                          entry.get("reason")))
        elif entry.get("type") == "escalation-refused":
            lines.append("  Not escalated: limit reached (%s)" % entry.get("reason"))
    return lines


def cmd_workflow(q, args):
    from . import workflow
    config = util.effective_config(q.dir)
    if args.action.isdigit():
        args.ids, args.action = [int(args.action)] + list(args.ids), "show"
    if args.action not in ("show", "set", "approve"):
        raise QueueError("usage: moa7amed-queue workflow [show|set|approve] [ID...]")
    if args.action == "set":
        if not args.ids or not args.profile:
            raise QueueError("usage: moa7amed-queue workflow set ID... quick|standard|deep|auto")
        for task_id in args.ids:
            workflow.set_profile(q, task_id, args.profile, config)
        emit(args, {"tasks": args.ids, "profile": args.profile},
             "Workflow of %s: %s" % (", ".join("#%d" % i for i in args.ids), args.profile))
        return 0
    if args.action == "approve":
        if not args.ids:
            raise QueueError("usage: moa7amed-queue workflow approve ID")
        for task_id in args.ids:
            workflow.approve(q, task_id)
        emit(args, {"approved": args.ids}, "Approved the plan of %s; implementation follows on "
             "the next run." % ", ".join("#%d" % i for i in args.ids))
        return 0
    tasks = [q.get(task_id) for task_id in args.ids] if args.ids else q.tasks(
        ["pending", "running", "failed"])
    data = [workflow.describe(q, task["id"]) for task in tasks]
    data = [item for item in data if item]
    if config.get("workflow_mode") == "off":
        empty = "workflow_mode is off"
    elif not tasks:
        empty = "no open tasks (a finished task's workflow: `moa7amed-queue workflow ID`)"
    else:
        empty = "no workflow recorded (the tasks are not classified yet)"
    emit(args, data, "\n\n".join("\n".join(_workflow_lines(item)) for item in data) or empty)
    return 0


def cmd_challenge(q, args):
    from . import challenge_cli
    return challenge_cli.run(q, args, emit)


def cmd_agents(q, args):
    from . import agents_cli
    return agents_cli.run(q, args, emit)


def cmd_bid(q, args):
    from . import bid_cli
    return bid_cli.run(q, args, emit)


def cmd_replica(q, args):
    from . import replica_cli
    return replica_cli.run(q, args, emit)


def cmd_where(q, args):
    root = util.plugin_root()
    home, origin = util.home_info()
    data = {"project": q.root, "data_dir": q.dir, "database": q.db_path,
            "runner_log": os.path.join(q.dir, "runner.log"), "home": home,
            "home_origin": {"setting": "set by MOA7AMED_QUEUE_HOME",
                            "legacy-setting": "set by CTQ_HOME (name used before 0.3.0)",
                            "legacy": "directory of an earlier version, used in place",
                            "default": "default location"}[origin],
            "plugin_root": root,
            "runner_powershell": os.path.join(root, "runner", "run-queue.ps1"),
            "runner_shell": os.path.join(root, "runner", "run-queue.sh"),
            "python": sys.executable}
    emit(args, data, "\n".join("%-18s %s" % (key, value) for key, value in data.items()))
    return 0


def cmd_log(q, args):
    if args.id is not None:
        run = q.conn.execute("SELECT * FROM runs WHERE task_id=? ORDER BY id DESC LIMIT 1",
                             (args.id,)).fetchone()
        if run is None:
            raise QueueError("task #%d has not been run yet" % args.id)
        print("Task #%d, run %d (%s)\n" % (args.id, run["id"], run["outcome"] or "in progress"))
        print(run["result_text"] or "(no result text recorded)")
        print("\nprompt: %s\noutput: %s\nstderr: %s" % (run["prompt_file"], run["stdout_file"],
                                                        run["stderr_file"]))
        return 0
    path = os.path.join(q.dir, "runner.log")
    try:
        lines = util.read_text_file(path).splitlines()
    except FileNotFoundError:
        print("(no runner has run for this project yet)")
        return 0
    print("\n".join(lines[-args.lines:]))
    return 0


SHIM_SH = """#!/bin/sh
# moa7amed-queue command line shim (created by `moa7amed-queue install-cli`).
root=$(cat "$(dirname "$0")/../plugin-root" 2>/dev/null)
if [ ! -f "$root/bin/moa7amed-queue" ]; then
  echo "moa7amed-queue: the plugin was not found at '$root'. Run /moa7amed-queue:queue-status in Claude Code, or reinstall the plugin and run 'moa7amed-queue install-cli' again." >&2
  exit 127
fi
exec sh "$root/bin/moa7amed-queue" "$@"
"""

SHIM_CMD = """@echo off
rem moa7amed-queue command line shim (created by `moa7amed-queue install-cli`).
setlocal
set "MQ_ROOT="
set /p MQ_ROOT=<"%~dp0..\\plugin-root"
if not exist "%MQ_ROOT%\\bin\\moa7amed-queue.cmd" (
  echo moa7amed-queue: the plugin was not found at "%MQ_ROOT%". Reinstall the plugin and run "moa7amed-queue install-cli" again. 1>&2
  exit /b 127
)
call "%MQ_ROOT%\\bin\\moa7amed-queue.cmd" %*
exit /b %ERRORLEVEL%
"""

SHIM_RUNNER_CMD = """@echo off
rem Starts the PowerShell queue runner (created by `moa7amed-queue install-cli`).
setlocal
set "MQ_ROOT="
set /p MQ_ROOT=<"%~dp0..\\plugin-root"
powershell -NoProfile -ExecutionPolicy Bypass -File "%MQ_ROOT%\\runner\\run-queue.ps1" %*
exit /b %ERRORLEVEL%
"""

SHORT_ALIAS = "mq"


def _foreign_command(name, bin_dir):
    """Path of a command with this name that is not one of our own shims, or None."""
    import shutil

    found = shutil.which(name)
    if found and os.path.normcase(os.path.dirname(os.path.abspath(found))) != os.path.normcase(bin_dir):
        return found
    return None


def cmd_install_cli(args):
    """Create command shims in <data directory>/bin.

    `moa7amed-queue` is the command. `ctq`, its name before 0.3.0, is created as an
    alias. The short `mq` is only created when no other program of that name is on
    PATH, so it can never shadow somebody else's tool."""
    bin_dir = os.path.join(util.home(), "bin")
    os.makedirs(bin_dir, exist_ok=True)
    hook.refresh_plugin_pointer(force=True)
    created, skipped = [], []
    names = [NAME, LEGACY_COMMAND]
    conflict = _foreign_command(SHORT_ALIAS, bin_dir)
    if args.no_short:
        skipped.append("%s: not requested" % SHORT_ALIAS)
    elif conflict:
        skipped.append("%s: another program already uses that name (%s)" % (SHORT_ALIAS, conflict))
    else:
        names.append(SHORT_ALIAS)
    shims = []
    for name in names:
        shims.append((name, SHIM_SH))
        if util.IS_WINDOWS:
            shims.append((name + ".cmd", SHIM_CMD))
            if name != SHORT_ALIAS:
                shims.append((name + "-run.cmd", SHIM_RUNNER_CMD))
    for name, content in shims:
        path = os.path.join(bin_dir, name)
        with open(path, "w", encoding="utf-8", newline="\r\n" if name.endswith(".cmd") else "\n") as handle:
            handle.write(content)
        if not util.IS_WINDOWS:
            os.chmod(path, 0o755)
        created.append(path)
    on_path = any(os.path.normcase(os.path.abspath(part)) == os.path.normcase(bin_dir)
                  for part in os.environ.get("PATH", "").split(os.pathsep) if part)
    lines = ["Created:"] + ["  " + path for path in created]
    lines += ["Skipped %s" % item for item in skipped]
    lines.append("Commands: `%s` (the command), `%s` (alias, its name before 0.3.0)%s."
                 % (NAME, LEGACY_COMMAND, ", `%s` (short alias)" % SHORT_ALIAS
                    if SHORT_ALIAS in names else ""))
    if on_path:
        lines.append("%s is already on your PATH: `moa7amed-queue` works in any terminal." % bin_dir)
    elif util.IS_WINDOWS:
        lines += ["Add the folder to your PATH once (PowerShell), then open a new terminal:",
                  "  [Environment]::SetEnvironmentVariable('Path', "
                  "[Environment]::GetEnvironmentVariable('Path','User') + ';%s', 'User')" % bin_dir]
    else:
        lines += ["Add the folder to your PATH once, then open a new terminal:",
                  "  echo 'export PATH=\"%s:$PATH\"' >> ~/.profile" % bin_dir]
    emit(args, {"created": created, "skipped": skipped, "commands": names, "bin_dir": bin_dir,
                "on_path": on_path}, "\n".join(lines))
    return 0


# --------------------------------------------------------------------------- migration

def _legacy_projects():
    """(root, data folder) of every project queue under the data directory in use."""
    base = os.path.join(util.home(), "projects")
    found = []
    if os.path.isdir(base):
        for name in sorted(os.listdir(base)):
            info = util.load_json(os.path.join(base, name, "project.json"), None)
            if info and info.get("root"):
                found.append((info["root"], os.path.join(base, name)))
    return found


def cmd_migrate(args):
    """Move the data directory of versions 0.1.0 and 0.2.0 to the new default location.

    Nothing needs this to keep working: the old directory is used where it is. The move
    is one rename of the whole directory, done only when no runner is active, so it
    either happens completely or not at all."""
    home, origin = util.home_info()
    legacy, target = util.legacy_home(), util.default_home()
    report = {"legacy": legacy, "target": target, "in_use": home, "moved": False,
              "dry_run": bool(args.dry_run)}
    if origin in ("setting", "legacy-setting"):
        variable = "MOA7AMED_QUEUE_HOME" if origin == "setting" else "CTQ_HOME"
        report["reason"] = "explicit"
        emit(args, report, "The data directory is set explicitly (%s=%s). It is used as it is; "
                           "there is nothing to migrate." % (variable, home))
        return 0
    if not os.path.isdir(legacy):
        report["reason"] = "nothing"
        emit(args, report, "Nothing to migrate: no data from an earlier version was found (%s).\n"
                           "Data directory in use: %s" % (legacy, home))
        return 0
    if os.path.isdir(target) and os.listdir(target):
        report["reason"] = "both-exist"
        emit(args, report,
             "Both directories exist, so nothing was moved:\n  in use:     %s\n  left alone: %s "
             "(from before 0.3.0)\nIf the old one still holds queues you need, stop every runner "
             "and move its folders under projects\\ into the new one by hand."
             % (target, legacy))
        return 1
    projects = _legacy_projects()
    busy = []
    for root, _folder in projects:
        if not os.path.isdir(root):
            continue
        q = store.Queue(root)
        try:
            info = engine.lock_info(q)
            if info and info["alive"]:
                busy.append("%s (runner pid %s)" % (root, info["pid"]))
        finally:
            q.close()
    if busy:
        raise QueueError("a runner is active, so the data cannot be moved now: %s. Wait for it "
                         "or stop it, then run `%s migrate` again. Nothing was changed."
                         % ("; ".join(busy), NAME))
    worktrees = [(root, os.path.join(target, os.path.relpath(folder, legacy), "worktree"))
                 for root, folder in projects if os.path.isdir(os.path.join(folder, "worktree"))]
    report.update(projects=len(projects), worktrees=len(worktrees))
    if args.dry_run:
        emit(args, report,
             "Would move %s\n        to %s\n%d project queue(s) with their tasks, history, "
             "routing data and settings. No runner is active. Nothing was changed."
             % (legacy, target, len(projects)))
        return 0
    try:
        if os.path.isdir(target):
            os.rmdir(target)  # empty, checked above
        os.rename(legacy, target)
    except OSError as exc:
        raise QueueError("could not move %s to %s: %s. Nothing was changed; the old directory "
                         "stays in use." % (legacy, target, exc))
    repaired = []
    for root, worktree in worktrees:
        if os.path.isdir(root):
            # git records a worktree by absolute path; tell it where the folder went
            code, _out, err = gitops.git(root, "worktree", "repair", worktree)
            repaired.append({"project": root, "worktree": worktree, "ok": code == 0,
                             "detail": err if code else ""})
    util.save_json_atomic(os.path.join(target, "migration.json"),
                          {"migrated_at": util.now_iso(), "from": legacy,
                           "by": "%s %s" % (NAME, __version__)})
    report.update(moved=True, in_use=target, repaired=repaired)
    lines = ["Moved %s" % legacy, "   to %s" % target,
             "%d project queue(s) moved with their tasks, history, routing data and settings."
             % len(projects)]
    for item in repaired:
        lines.append("Worktree of %s: %s" % (item["project"],
                                              "re-registered with git" if item["ok"]
                                              else "could not be re-registered (%s); run "
                                                   "`git worktree repair` in that project"
                                                   % item["detail"]))
    if os.path.isdir(os.path.join(target, "bin")):
        lines.append("Command shims moved with it: if %s was on your PATH, replace that entry "
                     "with %s (or run `%s install-cli`)."
                     % (os.path.join(legacy, "bin"), os.path.join(target, "bin"), NAME))
    emit(args, report, "\n".join(lines))
    return 0


# --------------------------------------------------------------------------- runner commands

def options_from_args(args):
    options = {}
    for key in OPTION_KEYS:
        value = getattr(args, key, None)
        if value is None or value == []:
            continue
        if key in TOOL_KEYS:
            flat = []
            for item in value:
                flat += util.split_list(item)
            value = flat
        options[key] = value
    return options


def runner_flags_from_args(args):
    return {name: getattr(args, name, None) for name in pyrunner.RUNNER_FLAGS}


def cmd_plan(q, args):
    options = {}
    if getattr(args, "options_file", None):
        options = util.load_json(args.options_file, {}) or {}
    options.update(options_from_args(args))
    data = pyrunner.plan(q, options)
    lines = ["Project  %s" % data["project"],
             "Runner   %s" % runner_line(data["lock"]),
             "Tasks    %s" % counts_line(data["counts"]),
             "Command  %s [--model <per task>]   (the prompt is passed on stdin)"
             % " ".join(data["command"]),
             "Workspace %s, uncommitted changes: %s, commits: %s, timeout: %s min" % (
                 data["options"]["workspace"], data["options"]["on_dirty"],
                 data["options"]["commit"], data["options"]["timeout_minutes"]),
             "", "Would run, in this order:"]
    lines += ["  #%d [%s] %-22s %s" % (task["id"], store.priority_label(task["priority"]),
                                      "model: %s" % task["model"], task["title"])
              for task in data["next_tasks"]] or ["  (nothing is runnable right now)"]
    if data["later_tasks"]:
        lines += ["", "Not runnable yet:"]
        lines += ["  #%d (%s) %s" % (task["id"], task["state"], task["title"])
                  for task in data["later_tasks"]]
    emit(args, data, "\n".join(lines))
    return 0


def _refuse_inside_worker(what):
    """A worker of a multi-agent task must not start or join queue runners."""
    if util.env("AGENT"):
        raise QueueError("%s is not allowed inside a worker of a multi-agent task" % what)


def cmd_run(q, args):
    _refuse_inside_worker("running the queue")
    if args.dry_run:
        return cmd_plan(q, args)
    return pyrunner.run(q, options_from_args(args), max_tasks=args.max_tasks, watch=args.watch,
                        poll_seconds=args.poll_seconds, stop_on_failure=args.stop_on_failure,
                        force=args.force, quiet=args.quiet)


def cmd_launch(q, args):
    _refuse_inside_worker("starting a runner")
    result = pyrunner.launch(q, options_from_args(args), runner_flags_from_args(args),
                             hidden=args.hidden, engine_kind=args.runner)
    text = "Started the %s runner in the background (pid %d)%s.\n  log:    %s\n  status: moa7amed-queue status" % (
        result["runner"], result["pid"], " in its own window" if result["window"] else "",
        result["log"])
    emit(args, result, text)
    return 0


def cmd_recover(q, args):
    result = engine.recover(q, args.force)
    lines = ["Removed the runner lock." if result["lock_removed"] else "No runner lock was set."]
    for item in result["recovered"]:
        if item["reconciled"]:
            lines.append(item["message"][0].upper() + item["message"][1:])
        else:
            lines.append("Task #%s was interrupted -> %s" % (item["task_id"], item["status"]))
    emit(args, result, "\n".join(lines))
    return 0


def cmd_runner_acquire(q, args):
    _refuse_inside_worker("acquiring the runner lock")
    options = {}
    if args.options_file:
        options = util.load_json(args.options_file, {}) or {}
    for key in TOOL_KEYS:
        if isinstance(options.get(key), str):
            options[key] = util.split_list(options[key])
    result = engine.acquire(q, args.pid, options, kind=args.kind, force=args.force)
    print(json.dumps(result))
    return 0


def cmd_claim(q, args):
    print(json.dumps(engine.claim(q, args.runner_id)))
    return 0


def cmd_heartbeat(q, args):
    print(json.dumps(engine.heartbeat(q, args.runner_id, args.run_id, args.child_pid)))
    return 0


def cmd_finish(q, args):
    if args.start_error:
        run = q.conn.execute("SELECT stderr_file FROM runs WHERE id=?", (args.run_id,)).fetchone()
        if run and run["stderr_file"]:
            util.write_text_file(run["stderr_file"], args.start_error + "\n")
    print(json.dumps(engine.finish(q, args.runner_id, args.run_id, exit_code=args.exit_code,
                                   timed_out=args.timed_out, interrupted=args.interrupted)))
    return 0


def cmd_runner_release(q, args):
    print(json.dumps(engine.release(q, args.runner_id)))
    return 0


# --------------------------------------------------------------------------- capture hook

def cmd_hook_prompt(args):
    return hook.main()


# --------------------------------------------------------------------------- parser

def build_parser():
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--project", default=argparse.SUPPRESS, metavar="DIR",
                        help="project directory (default: the current directory)")
    common.add_argument("--json", action="store_true", default=argparse.SUPPRESS,
                        help="print machine-readable JSON")

    parser = argparse.ArgumentParser(
        prog=NAME,
        description="%s %s - %s\n%s\n\nA persistent task queue for Claude Code: every prompt "
                    "becomes a task, each task runs on the model its difficulty calls for, one "
                    "at a time, and the result is checked and committed.\nPublished by %s."
                    % (NAME, __version__, DESCRIPTION, TAGLINE, PUBLISHER),
        epilog=EPILOG % {"home": util.home(), "legacy": LEGACY_COMMAND},
        formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--project", default=None, metavar="DIR",
                        help="project directory (default: the current directory)")
    parser.add_argument("--json", action="store_true", default=False,
                        help="print machine-readable JSON")
    parser.add_argument("--version", action="version", version="%s %s" % (NAME, __version__))
    sub = parser.add_subparsers(dest="command", metavar="COMMAND")

    def command(name, help_text, **kwargs):
        return sub.add_parser(name, help=help_text, description=help_text, parents=[common],
                              **kwargs)

    p = command("add", "add a task to the queue (it is not started)")
    p.add_argument("text", nargs="*", help="the prompt ('-' reads it from stdin)")
    p.add_argument("--stdin", action="store_true", help="read the prompt from stdin, verbatim")
    p.add_argument("--prompt-file", metavar="FILE", help="read the prompt from a UTF-8 file")
    p.add_argument("--acceptance", metavar="TEXT", help="acceptance criteria for the task")
    p.add_argument("--acceptance-file", metavar="FILE", help="read acceptance criteria from a file")
    p.add_argument("--title", help="short label (default: the first line of the prompt)")
    p.add_argument("--priority", default="normal", help="low, normal, high, urgent or an integer")
    p.add_argument("--after", action="append", metavar="IDS",
                   help="run only after these task ids are done (comma-separated, repeatable)")
    p.add_argument("--max-attempts", type=int, help="attempts before the task counts as failed")
    p.add_argument("--model", metavar="MODEL",
                   help="run this task on opus, sonnet, haiku or a named model instead of the "
                        "automatically chosen one")
    p.add_argument("--phased", action="store_true",
                   help="plan with opus, then implement with the routed model")
    p.add_argument("--source", default="cli", help=argparse.SUPPRESS)
    p.add_argument("--session-id", help=argparse.SUPPRESS)

    p = command("status", "summary of the queue, the runner and the open tasks")
    p = command("list", "list tasks")
    p.add_argument("--status", metavar="STATES",
                   help="only these states: pending,running,done,failed,cancelled")
    p = command("show", "everything about one task: original prompt, criteria, runs, history")
    p.add_argument("id", type=int)
    p = command("history", "execution history (most recent events last)")
    p.add_argument("id", type=int, nargs="?", help="only this task")
    p.add_argument("--limit", type=int, default=50)
    p = command("log", "tail the runner log, or print the last result of a task")
    p.add_argument("id", type=int, nargs="?", help="task id")
    p.add_argument("-n", "--lines", type=int, default=40)

    p = command("priority", "change the priority of tasks")
    p.add_argument("ids", nargs="+", metavar="ID", help="task ids")
    p.add_argument("level", help="low, normal, high, urgent or an integer (higher runs first)")
    p = command("depend", "change which tasks a task must wait for")
    p.add_argument("id", type=int)
    p.add_argument("--on", action="append", metavar="IDS", help="add prerequisites")
    p.add_argument("--remove", action="append", metavar="IDS", help="remove prerequisites")
    p = command("retry", "put failed or cancelled tasks back in the queue")
    p.add_argument("ids", nargs="*", metavar="ID")
    p.add_argument("--failed", action="store_true", help="every failed task")
    p.add_argument("--max-attempts", type=int, help="new attempt limit for these tasks")
    p = command("cancel", "cancel tasks that have not run (they stay in the history)")
    p.add_argument("ids", nargs="+", metavar="ID")

    p = command("model", "choose the model of tasks yourself, or return them to automatic")
    p.add_argument("model", help="auto, opus, sonnet, haiku, or a model alias or id")
    p.add_argument("ids", nargs="*", metavar="ID", help="task ids")
    p.add_argument("--pending", action="store_true", help="every pending task")
    p.add_argument("--whole-project", dest="whole_project", action="store_true",
                   help="every task of this project (tasks with their own override keep it)")
    p = command("route", "show why each task was given its model")
    p.add_argument("ids", nargs="*", metavar="ID", help="task ids (default: open tasks)")
    p.add_argument("--all", action="store_true", help="include finished tasks")
    p.add_argument("--reclassify", action="store_true",
                   help="classify again under the current policy (manual overrides are kept)")
    p.add_argument("--phased", choices=["on", "off"],
                   help="switch the phased workflow on or off for these tasks")
    p = command("models", "show which models can be used and what earlier runs proved")
    p.add_argument("--probe", action="store_true",
                   help="verify each tier now with one very small real request")
    p.add_argument("--reset", action="store_true",
                   help="forget earlier evidence and unblock tasks waiting for a model")

    def runner_options(p):
        p.add_argument("--permission-mode", metavar="MODE",
                       help="claude --permission-mode for the tasks (default: acceptEdits)")
        p.add_argument("--model",
                       help="run every task on this model instead of routing each one")
        p.add_argument("--routing-mode", metavar="POLICY",
                       help="routing policy: balanced, quality_first or cost_optimized")
        p.add_argument("--no-routing", dest="routing_enabled", action="store_const",
                       const=False, default=None,
                       help="do not choose models; use Claude Code's default")
        p.add_argument("--allowed-tools", action="append", metavar="TOOLS",
                       help="claude --allowedTools (comma-separated, repeatable)")
        p.add_argument("--disallowed-tools", action="append", metavar="TOOLS",
                       help="claude --disallowedTools (comma-separated, repeatable)")
        p.add_argument("--max-budget-usd", type=float, metavar="USD",
                       help="claude --max-budget-usd, per task")
        p.add_argument("--timeout-minutes", type=float, metavar="N",
                       help="stop a task after N minutes (0 = no limit, default 60)")
        p.add_argument("--on-dirty", choices=["stop", "stash", "allow"],
                       help="uncommitted changes before a task: stop (default), stash, or allow")
        p.add_argument("--workspace", choices=["inplace", "worktree"],
                       help="run in the project directory (default) or a dedicated git worktree")
        p.add_argument("--worktree-branch", metavar="NAME", help="branch of the dedicated worktree")
        p.add_argument("--commit", choices=["auto", "none"],
                       help="commit each successful task (default: auto)")
        p.add_argument("--retry-backoff-seconds", type=int, metavar="N")
        p.add_argument("--claude-bin", metavar="PATH", help="Claude Code executable")
        p.add_argument("--claude-args", action="append", metavar="ARG",
                       help="extra argument for claude; write --claude-args=--flag (repeatable)")
        p.add_argument("--max-tasks", type=int, default=0, metavar="N",
                       help="stop after N tasks (default: until nothing is runnable)")
        p.add_argument("--watch", action="store_true",
                       help="keep running and pick up tasks as they are added")
        p.add_argument("--poll-seconds", type=int, default=15, metavar="N")
        p.add_argument("--stop-on-failure", action="store_true",
                       help="stop the runner as soon as a task attempt fails")
        p.add_argument("--force", action="store_true",
                       help="take over the lock even if another runner looks alive")

    p = command("run", "run queued tasks one after another in this terminal (portable runner)")
    runner_options(p)
    p.add_argument("--dry-run", action="store_true", help="show what would run, start nothing")
    p.add_argument("--quiet", action="store_true", help="log to the runner log only")
    p = command("launch", "start a runner in the background and return immediately")
    runner_options(p)
    p.add_argument("--hidden", action="store_true", help="Windows: do not open a console window")
    p.add_argument("--runner", choices=["powershell", "python"],
                   help="which runner to start (default: PowerShell on Windows, else Python)")
    p = command("plan", "show what the runner would do now, without starting anything")
    runner_options(p)
    p.add_argument("--options-file", help=argparse.SUPPRESS)
    p = command("project", "plan a project or a major change: requirements, infrastructure, "
                           "architecture decisions, tasks; approve and import into the queue")
    actions = p.add_subparsers(dest="project_action", metavar="ACTION")
    actions.required = True

    def action(name, help_text):
        return actions.add_parser(name, help=help_text, description=help_text, parents=[common])

    a = action("new", "start a plan")
    a.add_argument("id", help="plan id: lowercase letters, digits, . _ -")
    a.add_argument("--title")
    a.add_argument("--mode", choices=["new", "existing"], default="new")
    a.add_argument("--summary")
    action("list", "list the plans of this project")
    a = action("show", "print a plan (the draft) as JSON")
    a.add_argument("id")
    a.add_argument("--section")
    a = action("apply", "merge a JSON fragment into the draft (--data, --file or stdin)")
    a.add_argument("id")
    a.add_argument("--data", dest="json_text", metavar="JSON",
                   help="the fragment itself, as one argument")
    a.add_argument("--file")
    a.add_argument("--note")
    a = action("discover", "record what the project's files show (existing projects)")
    a.add_argument("id")
    a = action("env", "record one infrastructure fact for a scenario")
    a.add_argument("id")
    a.add_argument("scenario")
    a.add_argument("field")
    a.add_argument("value")
    a.add_argument("--status", required=True, choices=["VERIFIED", "USER_CONFIRMED", "ASSUMED",
                                                       "UNKNOWN", "CONFLICTING"])
    a.add_argument("--source")
    a.add_argument("--label")
    a = action("target", "choose the deployment target scenario (or none)")
    a.add_argument("id")
    a.add_argument("scenario")
    a = action("depth", "set the planning depth, or auto")
    a.add_argument("id")
    a.add_argument("depth", choices=["quick", "standard", "deep", "auto"])
    a = action("check", "validate the plan: evidence, compatibility, decisions, tasks")
    a.add_argument("id")
    a = action("approve", "approve decisions and the plan for execution (needs --yes)")
    a.add_argument("id")
    a.add_argument("--decision", action="append", help="approve only these decisions")
    a.add_argument("--decisions-only", action="store_true",
                   help="approve decisions without approving the plan for execution")
    a.add_argument("--note")
    a.add_argument("--yes", action="store_true")
    a = action("execute", "import the approved tasks into the queue (needs --yes)")
    a.add_argument("id")
    a.add_argument("--yes", action="store_true")
    a = action("status", "milestones, tasks, blockers and progress")
    a.add_argument("id")
    a = action("revise", "record an implementation issue and show what it affects")
    a.add_argument("id")
    a.add_argument("--issue", required=True)
    a.add_argument("--task", action="append")
    a.add_argument("--decision", action="append")
    a = action("report", "write the planning documents (Markdown and JSON)")
    a.add_argument("id")
    a.add_argument("--out", metavar="DIR")
    a.add_argument("--to-project", action="store_true",
                   help="write to <project>/.moa7amed/plans/<id>/")
    from . import replica_cli
    replica_cli.add_parser(command, common)
    from . import bid_cli
    bid_cli.add_parser(command, common)
    from . import agents_cli
    agents_cli.add_parser(command, common)
    from . import challenge_cli
    challenge_cli.add_parser(command, common)
    p = command("workflow", "show each task's engineering workflow (quick, standard, deep), "
                            "set it by hand, or approve a deep task's plan")
    p.add_argument("action", nargs="?", default="show",
                   help="show (default), set or approve; or a task id to show")
    p.add_argument("ids", nargs="*", type=int)
    p.add_argument("--profile", choices=["quick", "standard", "deep", "auto"])
    p = command("reconcile", "show what a crash left behind and why the queue is blocked; "
                             "resolve it explicitly")
    p.add_argument("--commit", action="store_true",
                   help="commit the files changed while the found-finished task ran (only those)")
    p.add_argument("--stash", action="store_true",
                   help="set the task's uncommitted files aside in a git stash")
    p.add_argument("--release", action="store_true",
                   help="lift the hold; the files stay exactly as they are")
    p.add_argument("--yes", action="store_true", help="do it (without it, only show the plan)")
    p = command("recover", "remove a stale runner lock and requeue interrupted tasks")
    p.add_argument("--force", action="store_true",
                   help="also when the old runner or its Claude process still looks alive")

    p = command("projects", "list every project that has a queue")
    p = command("config", "show or change settings")
    p.add_argument("action", nargs="?", default="show", choices=["show", "set", "unset"])
    p.add_argument("key", nargs="?")
    p.add_argument("value", nargs="*")
    p.add_argument("--global", dest="use_global", action="store_true",
                   help="apply to every project instead of this one")
    p = command("where", "print the paths this project uses")
    p = command("install-cli", "create command shims so `moa7amed-queue` works in any terminal")
    p.add_argument("--no-short", action="store_true",
                   help="do not create the short alias `mq`")
    p = command("migrate", "move data written by versions before 0.3.0 to the new location")
    p.add_argument("--dry-run", action="store_true", help="show what would be moved")
    p = command("update", "show the installed and the offered version and how to update "
                          "(Claude Code performs the update)")
    p.add_argument("--refresh", action="store_true",
                   help="first ask Claude Code to refresh the marketplace listing")
    p = command("doctor", "check that this installation and its data are healthy")
    p = command("backup", "copy the settings and every project queue to a dated folder")
    p.add_argument("--list", action="store_true", help="list the existing backups")
    p.add_argument("--label", help="short label added to the backup's name")
    p.add_argument("--output", metavar="DIR", help="write the backup below this folder")
    p = command("restore", "bring back a backup (asks for --yes before changing anything)")
    p.add_argument("name", help="name of a backup, a unique start of it, or a backup folder")
    p.add_argument("--yes", action="store_true", help="replace the current data with the backup")
    p = command("changelog", "show what changed in this version")
    p.add_argument("--since", metavar="VERSION", help="everything newer than this version")
    p.add_argument("--all", action="store_true", help="every version")
    p = command("usage", "show what this project's tasks cost, as reported by Claude Code")
    p.add_argument("--days", type=int, help="only runs started in the last N days")

    # Protocol used by the runners; not meant to be typed by hand.
    p = command("runner-acquire", argparse.SUPPRESS)
    p.add_argument("--pid", type=int, required=True)
    p.add_argument("--kind", default="powershell")
    p.add_argument("--options-file")
    p.add_argument("--force", action="store_true")
    p = command("claim", argparse.SUPPRESS)
    p.add_argument("--runner-id", required=True)
    p = command("heartbeat", argparse.SUPPRESS)
    p.add_argument("--runner-id", required=True)
    p.add_argument("--run-id", type=int)
    p.add_argument("--child-pid", type=int)
    p = command("finish", argparse.SUPPRESS)
    p.add_argument("--runner-id", required=True)
    p.add_argument("--run-id", type=int, required=True)
    p.add_argument("--exit-code", type=int)
    p.add_argument("--timed-out", action="store_true")
    p.add_argument("--interrupted", action="store_true")
    p.add_argument("--start-error")
    p = command("runner-release", argparse.SUPPRESS)
    p.add_argument("--runner-id", required=True)
    p = command("hook-prompt", argparse.SUPPRESS)
    return parser


QUEUE_COMMANDS = {
    "add": cmd_add, "status": cmd_status, "list": cmd_list, "show": cmd_show,
    "history": cmd_history, "log": cmd_log, "priority": cmd_priority, "depend": cmd_depend,
    "retry": cmd_retry, "cancel": cmd_cancel, "run": cmd_run, "launch": cmd_launch,
    "model": cmd_model, "route": cmd_route, "models": cmd_models,
    "plan": cmd_plan, "recover": cmd_recover, "where": cmd_where, "usage": cmd_usage,
    "reconcile": cmd_reconcile, "project": cmd_project, "workflow": cmd_workflow,
    "replica": cmd_replica, "bid": cmd_bid, "agents": cmd_agents,
    "challenge": cmd_challenge,
    "runner-acquire": cmd_runner_acquire, "claim": cmd_claim, "heartbeat": cmd_heartbeat,
    "finish": cmd_finish, "runner-release": cmd_runner_release,
}
GLOBAL_COMMANDS = {"projects": cmd_projects, "config": cmd_config, "migrate": cmd_migrate,
                   "install-cli": cmd_install_cli, "hook-prompt": cmd_hook_prompt,
                   "update": cmd_update, "doctor": cmd_doctor, "backup": cmd_backup,
                   "restore": cmd_restore, "changelog": cmd_changelog}
PROTOCOL_COMMANDS = ("runner-acquire", "claim", "heartbeat", "finish", "runner-release")
RUNNER_STEP_COMMANDS = ("claim", "heartbeat", "finish", "runner-release")


def main(argv=None):
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
    parser = build_parser()
    argv, claude_arguments = _arguments_for_claude(sys.argv[1:] if argv is None else list(argv))
    args = parser.parse_args(argv)
    if claude_arguments:
        args.value = claude_arguments
    if not args.command:
        parser.print_help()
        return 0
    try:
        # Before anything is written: on the first run after an update this backs the data up.
        # A runner does that once, when it starts; the calls it makes while working do not,
        # so a runner started before an update cannot keep re-recording its own version.
        track_version = args.command not in ("hook-prompt",) + RUNNER_STEP_COMMANDS
        if track_version:
            maintenance.note_version()
        if args.command in GLOBAL_COMMANDS:
            return GLOBAL_COMMANDS[args.command](args)
        root, start = util.resolve_project(args.project)
        q = store.Queue(root)
        q.start = start
        try:
            return QUEUE_COMMANDS[args.command](q, args)
        finally:
            q.close()
            if track_version:
                maintenance.note_version(backup=False)   # a data directory created just now
    except QueueError as exc:
        if args.command in PROTOCOL_COMMANDS or args.json:
            print(json.dumps({"error": str(exc), "code": exc.code}))
        if args.command not in PROTOCOL_COMMANDS:
            sys.stderr.write("moa7amed-queue: %s\n" % exc)
        return exc.code
    except KeyboardInterrupt:
        return 130
