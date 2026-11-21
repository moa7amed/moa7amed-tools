"""`moa7amed-queue replica ...`: the command line of Replica."""
from __future__ import annotations

import json
import os

from . import replica_model as model
from . import replica_store as rstore
from . import util
from .util import QueueError

NAME = "moa7amed-queue"


def add_parser(command, common):
    p = command("replica", "rebuild an application from evidence: references, screen "
                           "inventory, plan, build in the queue, compare, test, improve")
    actions = p.add_subparsers(dest="replica_action", metavar="ACTION")
    actions.required = True

    def action(name, help_text):
        return actions.add_parser(name, help=help_text, description=help_text, parents=[common])

    a = action("intent", "is this text a request to rebuild an application? (no side effects)")
    a.add_argument("text", nargs="+")
    action("tools", "what this machine can do for Replica (browser capture, image reading...)")
    a = action("new", "start a replica project for a request (kept verbatim)")
    a.add_argument("id", nargs="?", help="project id (default: r1, r2, ...)")
    a.add_argument("--request", required=True, help="the user's request, verbatim")
    a.add_argument("--mode", choices=list(model.MODES))
    a.add_argument("--quality", choices=list(model.QUALITY), default="prototype")
    a.add_argument("--ownership", choices=list(model.OWNERSHIP), default="unknown")
    a.add_argument("--target", help="folder the rebuild is written to (default: this project)")
    a.add_argument("--title")
    action("list", "list the replica projects")
    a = action("show", "print a replica project's inventory as JSON")
    a.add_argument("id")
    a = action("config", "change mode, quality, ownership, target or the rebuild's address")
    a.add_argument("id")
    a.add_argument("--mode", choices=list(model.MODES))
    a.add_argument("--quality", choices=list(model.QUALITY))
    a.add_argument("--ownership", choices=list(model.OWNERSHIP))
    a.add_argument("--target")
    a.add_argument("--candidate-url", help="where the rebuild is served, e.g. http://localhost:8000/")
    a.add_argument("--page", action="append", default=[], metavar="SCREEN=PATH",
                   help="the rebuild's page for a screen, e.g. S1=/index.html (repeatable)")
    a.add_argument("--mask", action="append", default=[], metavar="SCREEN=X,Y,W,H",
                   help="a region excluded from comparison, e.g. a clock (repeatable)")
    a = action("ref", "add or list references (URL, screenshot, recording, source, document)")
    a.add_argument("id")
    a.add_argument("source", nargs="?", help="URL, file or folder; omit to list")
    a.add_argument("--kind", choices=sorted(model.REF_KINDS))
    a.add_argument("--provenance", help="where it comes from")
    a.add_argument("--permission", help="what you may do with it (e.g. 'own assets')")
    a.add_argument("--platform", choices=["web", "android", "ios"])
    a.add_argument("--note")
    a = action("capture", "capture pages with a browser (only the URLs given; no crawling, "
                          "no login)")
    a.add_argument("id")
    a.add_argument("url", nargs="*", help="URL(s) to capture (default: the project's URL references)")
    a.add_argument("--viewport", action="append", default=[], metavar="WxH",
                   help="e.g. 1280x800 (repeatable; default 1280x800)")
    a.add_argument("--full-page", action="store_true")
    a.add_argument("--screen", help="the screen this capture shows")
    a = action("analyze", "merge screens, features, flows and unknowns (JSON: --data, --file "
                          "or stdin) and validate the evidence")
    a.add_argument("id")
    a.add_argument("--data", dest="json_text", metavar="JSON")
    a.add_argument("--file")
    a = action("tokens", "extract design tokens from captures, stylesheets and screenshots")
    a.add_argument("id")
    a = action("plan", "write the reconstruction tasks into the linked project plan")
    a.add_argument("id")
    a = action("compare", "compare a rebuild screenshot (file or URL) with a screen's reference")
    a.add_argument("id")
    a.add_argument("screen", nargs="?", help="screen id; omit with --all")
    a.add_argument("candidate", nargs="?", help="screenshot file or URL of the rebuild")
    a.add_argument("--reference", help="reference id (default: the screen's screenshot)")
    a.add_argument("--all", action="store_true", help="capture and compare every mapped screen")
    a = action("test", "record a functional test result, or import a JUnit report")
    a.add_argument("id")
    a.add_argument("--feature")
    a.add_argument("--flow")
    a.add_argument("--result", choices=["pass", "fail", "skip"])
    a.add_argument("--kind", choices=["real", "mock"], default="real")
    a.add_argument("--name")
    a.add_argument("--evidence")
    a.add_argument("--junit", help="a JUnit XML report; test names carry feature/flow ids")
    a = action("improve", "one bounded round: measure, then queue corrections or stop")
    a.add_argument("id")
    a.add_argument("--run", action="store_true",
                   help="also run the queue and measure again, round after round, until a stop "
                        "condition (bounded by replica_max_iterations)")
    a = action("sweep", "look in the rebuild for the reference's names, domains and files")
    a.add_argument("id")
    a.add_argument("--term", action="append", default=[], help="a brand name to look for")
    a = action("agents", "queue the analysis of uncited screenshots, one worker per screenshot")
    a.add_argument("id")
    a = action("status", "where a replica project stands")
    a.add_argument("id", nargs="?")
    a = action("report", "the reconstruction report (Markdown)")
    a.add_argument("id")
    a.add_argument("--output")


def _read(args):
    if args.json_text is not None:
        text = args.json_text
    elif args.file:
        text = util.read_text_file(args.file)
    else:
        text = util.read_stdin_text()
    try:
        return json.loads(text)
    except ValueError as exc:
        raise QueueError("the inventory is not valid JSON: %s" % exc)


def _findings_text(findings):
    return "\n".join("  [%s] %s: %s" % (item["level"], item["where"], item["message"])
                     for item in findings) or "  no findings"


def run(q, args, emit):
    action = args.replica_action
    config = util.effective_config(q.dir)
    if action == "intent":
        found = model.intent(" ".join(args.text))
        emit(args, found, "%s%s: %s" % (found["intent"] or "not a reconstruction request",
                                        " (%s mode)" % found["mode"] if found["mode"] else "",
                                        found["reason"]))
        return 0
    if action == "tools":
        found = rstore.tools(config, probe=True)
        lines = ["browser capture (Playwright): %s" % (
            "yes, %s" % found.get("browser") if found["capture"] else
            "no (%s) - add screenshots by hand" % (found.get("browser") or "Playwright not "
                                                   "installed for this Python")),
                 "image reading: %s" % ("Pillow" if found["pillow"] else
                                        "built-in PNG reader (PNG only)"),
                 "recording metadata (ffprobe): %s" % ("yes" if found["ffprobe"] else "no"),
                 "Android device automation (adb): %s" % ("yes" if found["adb"] else "no"),
                 "iOS tooling (xcrun): %s" % ("yes" if found["xcrun"] else "no"),
                 "Python used for captures: %s" % found["python"]]
        emit(args, found, "\n".join(lines))
        return 0
    if action == "new":
        project_id = args.id or rstore.next_id(q)
        project = rstore.create(q, project_id, args.request, title=args.title, mode=args.mode,
                                quality=args.quality, ownership=args.ownership,
                                target_dir=os.path.abspath(args.target) if args.target else None)
        project = rstore.refresh_mode(q, project_id)
        emit(args, {key: project[key] for key in ("id", "mode", "requested_mode", "quality",
                                                  "ownership", "target_dir", "intent")},
             "Created replica project %s: %s mode, %s, ownership %s%s.\nNext: add references "
             "(`%s replica ref %s <url|file|folder>`), capture them, then the inventory."
             % (project_id, project["mode"], project["quality"], project["ownership"],
                "\n" + project["mode_note"] if project.get("mode_note") else "", NAME, project_id))
        return 0
    if action == "list":
        rows = rstore.projects(q)
        emit(args, rows, "\n".join("  %-12s %-10s %-10s %s" % (row["id"], row["mode"],
                                                              row["quality"], row["title"])
                                   for row in rows) or "no replica projects")
        return 0
    if action == "status" and not args.id:
        rows = rstore.projects(q)
        if len(rows) == 1:
            args.id = rows[0]["id"]
        else:
            emit(args, rows, "\n".join("  %s" % row["id"] for row in rows)
                 or "no replica projects")
            return 0
    project_id = args.id
    if action == "show":
        project = rstore.get(q, project_id)
        project["references"] = rstore.refs(q, project_id)
        print(json.dumps(project, indent=2, ensure_ascii=False))
        return 0
    if action == "config":
        pages, masks = {}, {}
        for item in args.page:
            screen, _, path = item.partition("=")
            pages[screen.strip()] = path.strip()
        for item in args.mask:
            screen, _, box = item.partition("=")
            masks.setdefault(screen.strip(), []).append([int(v) for v in box.split(",")])
        current = rstore.get(q, project_id)["document"].get("candidate") or {}
        if pages:
            pages = dict(current.get("pages") or {}, **pages)
        if masks:
            merged = dict(current.get("masks") or {})
            merged.update(masks)
            masks = merged
        project = rstore.configure(q, project_id, mode=args.mode, quality=args.quality,
                                   ownership=args.ownership, target_dir=os.path.abspath(args.target)
                                   if args.target else None, url=args.candidate_url,
                                   pages=pages or None, masks=masks or None)
        emit(args, {"mode": project["mode"], "candidate": project["document"].get("candidate")},
             "Replica %s: %s mode%s" % (project_id, project["mode"],
                                        "; " + project["mode_note"] if project.get("mode_note")
                                        else ""))
        return 0
    if action == "ref":
        if not args.source:
            rows = rstore.refs(q, project_id)
            emit(args, rows, "\n".join(
                "  %-4s %-10s %-12s %s%s" % (row["id"], row["kind"], row["status"],
                                             row["uri"] or row["path"] or "",
                                             "\n       limitation: " + row["limitation"]
                                             if row.get("limitation") else "")
                for row in rows) or "no references yet")
            return 0
        item = rstore.add_ref(q, project_id, args.kind, args.source, provenance=args.provenance,
                              permission=args.permission, platform=args.platform, note=args.note)
        emit(args, item, "%s %s %s (%s)%s%s" % (
            "Already recorded as" if item.get("duplicate") else "Recorded", item["id"],
            item["kind"], item["status"], "\n  limitation: " + item["limitation"]
            if item.get("limitation") else "", "\n  capture it: %s replica capture %s"
            % (NAME, project_id) if item["kind"] == "url" else ""))
        return 0
    if action == "capture":
        urls = args.url or [row["uri"] for row in rstore.refs(q, project_id) if row["kind"] == "url"]
        if not urls:
            raise QueueError("no URL to capture: give one, or add a URL reference")
        viewports = []
        for item in args.viewport or ["1280x800"]:
            width, _, height = item.lower().partition("x")
            viewports.append((int(width), int(height)))
        results = []
        for url in urls:
            for width, height in viewports:
                results.append(rstore.capture(q, project_id, url=url, width=width, height=height,
                                              full_page=args.full_page, screen=args.screen,
                                              config=config))
        lines = []
        for item in results:
            if item["ok"]:
                lines.append("captured %s -> %s (screenshot), %s (page source)%s%s" % (
                    item["url"], item["screenshot"], item["html"],
                    "; asks for a login: nothing behind it was captured" if item["login_required"]
                    else "", "; changed since %s" % item["changed_since"]
                    if item.get("changed_since") else ""))
            else:
                lines.append("NOT captured %s: %s" % (item["url"], item["error"]))
        emit(args, results, "\n".join(lines))
        return 0 if all(item["ok"] for item in results) else 1
    if action == "analyze":
        findings = rstore.apply_inventory(q, project_id, _read(args))
        status = rstore.status(q, project_id)
        emit(args, {"findings": findings, "screens": status["screens"],
                    "features": status["features"], "flows": status["flows"]},
             "Inventory: %d screens, %d features, %d flows, %d unknown\n%s" % (
                 status["screens"], status["features"], status["flows"], status["unknowns"],
                 _findings_text(findings)))
        return 1 if any(item["level"] == "error" for item in findings) else 0
    if action == "tokens":
        result = rstore.extract_tokens(q, project_id)
        groups = {}
        for token in result["tokens"]:
            groups.setdefault(token["group"], []).append("%s (%s, x%s)" % (
                token["value"], token["evidence"], token.get("count", "-")))
        emit(args, result, "\n".join("  %-12s %s" % (group, ", ".join(values[:6]))
                                     for group, values in sorted(groups.items()))
             or "no tokens found (capture pages or add stylesheets and screenshots)")
        return 0
    if action == "plan":
        result = rstore.make_plan(q, project_id)
        emit(args, result, "Wrote %d tasks into plan %s:\n%s\nNext: architecture and "
             "infrastructure in the plan (/moa7amed-queue:architecture-review %s), then `%s "
             "project check %s` and your approval (/moa7amed-queue:plan-approve %s)." % (
                 len(result["tasks"]), result["plan"], "\n".join(
                     "  %s %s" % (task["id"], task["title"]) for task in result["tasks"]),
                 result["plan"], NAME, result["plan"], result["plan"]))
        return 0
    if action == "compare":
        if args.all:
            results = rstore.recompare(q, project_id, config=config)
        else:
            if not args.screen or not args.candidate:
                raise QueueError("usage: %s replica compare ID SCREEN <file|url> (or --all)" % NAME)
            results = [rstore.compare(q, project_id, args.screen, args.candidate,
                                      reference=args.reference, config=config)]
        threshold = float(config.get("replica_visual_threshold", 0.9))
        lines = []
        for item in results:
            if item.get("error"):
                lines.append("%s: %s" % (item["screen"], item["error"]))
                continue
            lines.append("%s: score %.4f (%s)\n  %s\n  difference image: %s%s" % (
                item["screen"], item["score"], "meets %.2f" % threshold
                if item["score"] >= threshold else "below %.2f" % threshold,
                "\n  ".join(item["findings"]) or "no region differs", item["diff"],
                "".join("\n  note: " + note for note in item["notes"])))
        if results and not results[0].get("error"):
            lines.append("method: %s" % results[0]["method"])
        emit(args, results, "\n".join(lines) or "nothing compared (set the rebuild's pages "
                                                "with `replica config --page S1=/path`)")
        if any(item.get("error") for item in results):
            return 2
        return 0 if all(item["score"] >= threshold for item in results) else 1
    if action == "test":
        if args.junit:
            result = rstore.import_junit(q, project_id, args.junit)
            emit(args, result, "Recorded %d test(s)%s" % (
                result["recorded"], "; not linked to a feature or flow id: %s"
                % ", ".join(result["unmatched"][:8]) if result["unmatched"] else ""))
            return 0
        if not args.result or not (args.feature or args.flow):
            raise QueueError("usage: %s replica test ID --feature F1|--flow U1 --result "
                             "pass|fail [--kind real|mock] [--evidence TEXT]" % NAME)
        recorded = rstore.record_test(q, project_id, args.result, args.kind, feature=args.feature,
                                      flow=args.flow, name=args.name, evidence=args.evidence)
        emit(args, {"recorded": bool(recorded)}, "Recorded" if recorded else
             "A skipped check is not recorded (it is neither a pass nor a fail)")
        return 0
    if action == "improve":
        rounds = []
        while True:
            step = rstore.improve(q, project_id, config=config)
            rounds.append(step)
            if not args.run or step["action"] != "queued":
                break
            from . import pyrunner
            pyrunner.run(q, {}, quiet=True)
            rstore.recompare(q, project_id, config=config)
        last = rounds[-1]
        if last["action"] == "queued":
            text = ("Iteration %d: queued corrections %s for the screens below %.2f. Run the "
                    "queue, compare again (`%s replica compare %s --all`), then improve again."
                    % (last["iteration"], ", ".join("#%d" % i for i in last["tasks"]),
                       last["threshold"], NAME, project_id))
        elif last["action"] == "wait":
            text = last["reason"]
        else:
            text = "Stopped: %s" % last["reason"]
        text += "\nscores: %s" % ", ".join("%s %.4f" % item for item in sorted(last["scores"].items()))
        emit(args, rounds, text)
        return 0
    if action == "sweep":
        result = rstore.sweep(q, project_id, args.term)
        emit(args, result, ("\n".join("  %s: %s" % (item["file"], item["issue"])
                                      for item in result["findings"])
                            or "nothing found") + "\n(looked for: %s)%s" % (
            ", ".join(result["terms"]) or "-", "" if result["applies"]
            else "\nownership is own/licensed: these may be legitimate"))
        return 1 if result["findings"] and result["applies"] else 0
    if action == "agents":
        result = rstore.screen_analysis_task(q, project_id, config)
        emit(args, result, "Queued task #%d with %d screen workers (%s). Run the queue; `%s agents "
             "show %d` shows what ran." % (result["task"], len(result["subtasks"]),
                                            ", ".join(result["subtasks"]), NAME, result["task"]))
        return 0
    if action == "status":
        data = rstore.status(q, project_id)
        emit(args, data, status_text(data))
        return 0
    if action == "report":
        from . import replica_report
        text = replica_report.render(q, project_id)
        if args.output:
            with open(args.output, "w", encoding="utf-8", newline="\n") as handle:
                handle.write(text)
            emit(args, {"output": args.output}, "Report written to %s" % args.output)
        else:
            print(text)
        return 0
    raise QueueError("unknown replica action %s" % action)


def status_text(data):
    parity = data["parity"]
    lines = ["Replica %s - %s" % (data["project"], data["title"]),
             "  Request:    %s" % data["request"][:160],
             "  Mode:       %s%s, quality %s, ownership %s" % (
                 data["mode"], "" if data["mode"] == data["requested_mode"]
                 else " (asked: %s)" % data["requested_mode"], data["quality"], data["ownership"]),
             "  Stage:      %s (plan: %s)" % (data["stage"], data["plan_stage"]),
             "  Evidence:   %d references; %d screens, %d features, %d flows, %d unknown" % (
                 data["references"], data["screens"], data["features"], data["flows"],
                 data["unknowns"]),
             "  Queue:      %s" % (", ".join("%d %s" % (count, state) for state, count in
                                             sorted(data["queue"].items())) or "nothing queued yet"),
             "  Features:   %s" % (", ".join("%d %s" % (count, verdict) for verdict, count in
                                             sorted(parity["totals"].items())) or "-")]
    for screen in parity["screens"]:
        lines.append("  Screen %-4s score %.4f (pixel comparison, %s)" % (
            screen["screen"], screen["score"], screen["at"][:16]))
    for item in data["iterations"][-3:]:
        lines.append("  Iteration %d: %s" % (item["number"], item["status"]))
    for item in data["errors"][:5]:
        lines.append("  Error:      %s: %s" % (item["where"], item["message"]))
    for item in data["limitations"][:6]:
        lines.append("  Limitation: %s: %s" % (item["ref"], item["limitation"]))
    lines.append("  Cost:       $%.4f reported for its tasks" % data["cost_usd_reported"])
    return "\n".join(lines)
