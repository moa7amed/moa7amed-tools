"""`moa7amed-queue agents ...`: how tasks use read-only agents."""
from __future__ import annotations

import datetime
import json

from . import agents, util
from .util import QueueError


def add_parser(command, common):
    p = command("agents", "multi-agent execution: which tasks used read-only agents, what ran "
                          "(measured), and holds")
    actions = p.add_subparsers(dest="agents_action", metavar="ACTION")
    actions.required = True

    def action(name, help_text):
        return actions.add_parser(name, help=help_text, description=help_text, parents=[common])

    a = action("show", "strategy, reason, mechanism, agents, isolation, revisions, timings, usage")
    a.add_argument("id", type=int, nargs="?")
    a = action("evaluate", "how a task would run now (changes nothing)")
    a.add_argument("id", type=int)
    a = action("plan", "give a pending task an explicit read-only decomposition (--data or --file)")
    a.add_argument("id", type=int)
    a.add_argument("--data", dest="json_text", metavar="JSON")
    a.add_argument("--file")
    a = action("release", "release a task held because its live workspace changed while its "
                          "agents ran (after you checked it)")
    a.add_argument("id", type=int)


def _clock(ts):
    if not ts:
        return "-"
    return datetime.datetime.fromtimestamp(ts).strftime("%H:%M:%S.%f")[:-3]


def show_text(info):
    lines = ["Task #%d: %s" % (info["task"], (info["strategy"] or "not evaluated").upper()),
             "  Reason:     %s" % (info["reason"] or "-"),
             "  Mechanism:  %s" % (info["mechanism"] or "none (one agent: the task's own session)")]
    if info["source"]:
        lines.append("  Plan:       %s (analysis rounds used: %s)" % (info["source"], info["rounds"]))
    summary = info["summary"] or {}
    if summary.get("wall_s") is not None:
        lines.append("  Measured:   %d agent(s) at most at the same time; wall %.1fs, agent time %.1fs "
                     "(x%s)" % (summary.get("max_concurrency", 0), summary["wall_s"],
                                summary.get("worker_s", 0), summary.get("parallel_speedup")))
        unknown = summary.get("usage_unknown") or []
        lines.append("  Usage:      $%.4f reported by Claude Code for the agents%s" % (
            summary.get("spent_usd") or 0, "; unknown for %s" % ", ".join(unknown) if unknown else ""))
    for item in info["subtasks"]:
        lines.append("  - %-20s %-22s %-9s pid %-7s %s -> %s  %s" % (
            item["key"], item["role"], item["status"], item["pid"] or "-", _clock(item["started_ts"]),
            _clock(item["finished_ts"]), item["model_used"] or ""))
        if item["depends_on"]:
            lines.append("      after:     %s" % ", ".join(item["depends_on"]))
        if item["isolation"]:
            lines.append("      isolation: %s" % item["isolation"])
        if item["input_fingerprint"]:
            lines.append("      revision:  %s (attempt %d)" % (item["input_fingerprint"], item["attempt"]))
        if item["cost_usd"] is not None:
            lines.append("      cost:      $%.4f reported" % item["cost_usd"])
        elif item["finished_ts"]:
            lines.append("      cost:      not reported (unknown, not zero)")
        for label in ("integration", "error"):
            if item[label]:
                lines.append("      %s: %s" % (label, item[label][:240]))
    return "\n".join(lines)


def run(q, args, emit):
    action = args.agents_action
    config = util.effective_config(q.dir)
    agents.ensure_schema(q)
    if action == "show":
        ids = [args.id] if args.id else [row["task_id"] for row in q.conn.execute(
            "SELECT task_id FROM agent_plans WHERE strategy='parallel' ORDER BY updated_at DESC LIMIT 10")]
        data = [info for info in (agents.describe(q, task_id) for task_id in ids) if info]
        emit(args, data, "\n\n".join(show_text(info) for info in data) or
             "no task has used agents (a task runs as one agent unless independent read-only "
             "analysis is justified)")
        return 0
    if action == "evaluate":
        decision = agents.evaluate(q, q.get(args.id), dict(config), record=False)
        emit(args, decision, "%s: %s%s" % (decision["strategy"].upper(), decision["reason"],
                                           "\n  mechanism: %s" % decision["mechanism"]
                                           if decision["mechanism"] else ""))
        return 0
    if action == "plan":
        text = args.json_text if args.json_text is not None else (
            util.read_text_file(args.file) if args.file else util.read_stdin_text())
        try:
            plan = json.loads(text)
        except ValueError as exc:
            raise QueueError("not valid JSON: %s" % exc)
        items = agents.set_plan(q, args.id, plan, config)
        decision = agents.evaluate(q, q.get(args.id), dict(config), record=True)
        emit(args, {"subtasks": len(items), "decision": decision},
             "Task #%d: %d read-only subtasks recorded; %s (%s)" % (
                 args.id, len(items), decision["strategy"], decision["reason"]))
        return 0
    if action == "release":
        released = agents.release_hold(q, args.id)
        emit(args, {"released": released}, "Task #%d %s" % (
            args.id, "released" if released else "was not held by its agents"))
        return 0
    raise QueueError("unknown agents action %s" % action)
