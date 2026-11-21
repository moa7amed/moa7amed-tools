"""`moa7amed-queue challenge ...`: concerns, the owner's decisions, and reviews on request."""
from __future__ import annotations

import re

from . import challenge, util
from .util import QueueError


def add_parser(command, common):
    p = command("challenge", "concerns about tasks and the owner's decisions on them "
                             "(challenge, discuss, then execute)")
    actions = p.add_subparsers(dest="challenge_action", metavar="ACTION")

    def action(name, help_text):
        return actions.add_parser(name, help=help_text, description=help_text, parents=[common])

    action("list", "open concerns, and recent decisions (the default)")
    a = action("show", "a task's concerns and decisions, or one concern (C3)")
    a.add_argument("ref")
    a = action("review", "check a task now with the rules, whatever challenge_mode says")
    a.add_argument("id", type=int)
    a.add_argument("--no-hold", action="store_true",
                   help="record what is found as advisories; do not hold the task")
    a = action("raise", "record a concern from a review (needs its evidence); holds the "
                        "task, or the approval of a plan (--plan)")
    a.add_argument("id", type=int, nargs="?")
    a.add_argument("--plan", metavar="PLAN", help="a concern about a project plan's current revision")
    a.add_argument("--concern", required=True)
    a.add_argument("--evidence", required=True, help="what in the task, code or plan shows it")
    a.add_argument("--impact")
    a.add_argument("--recommendation", help="the practical alternative")
    a.add_argument("--restriction", action="store_true",
                   help="a real limit (permissions, provider rules, authorization), not a "
                        "recommendation: accepting the risk cannot lift it")
    a.add_argument("--advisory", action="store_true", help="note it; do not hold the task")
    a = action("decide", "record the owner's decision: --recommendation or --original")
    a.add_argument("refs", nargs="*", help="concerns (C3) or tasks (5); default: every open one")
    choice = a.add_mutually_exclusive_group()
    choice.add_argument("--recommendation", dest="decision", action="store_const",
                        const="recommendation", help="take the recommendation")
    choice.add_argument("--original", dest="decision", action="store_const", const="original",
                        help="proceed with the original approach and accept the trade-off")
    a.add_argument("--said", metavar="TEXT",
                   help="the owner's own words; the decision is read from them when no option "
                        "is given (from Claude Code: must match a prompt the owner typed)")
    a.add_argument("--statement", type=int, metavar="N",
                   help="a prompt the owner typed, recorded by the capture hook (S<N>)")
    a.add_argument("--tradeoff", help="the trade-off the owner accepts, in their words")


def _refs(refs):
    ids, tasks = [], []
    for ref in refs or []:
        match = re.match(r"^[Cc](\d+)$", ref)
        if match:
            ids.append(int(match.group(1)))
        elif ref.lstrip("#").isdigit():
            tasks.append(int(ref.lstrip("#")))
        else:
            raise QueueError("'%s' is neither a concern (C3) nor a task id" % ref)
    return ids, tasks


def decide(q, args):
    """The decision path for commands. Only the owner decides: from their terminal, or from
    Claude Code when the decision is in a prompt the owner typed (recorded by the hook)."""
    source = challenge.provenance()
    statement = None
    if source == "session":
        statement = challenge.find_statement(q, statement_id=args.statement, said=args.said)
        if statement is None:
            raise QueueError(
                "this command runs in a shell Claude Code started, so it cannot speak for the "
                "owner on its own. The decision has to come from the owner: they type it in "
                "Claude Code (the capture hook records it; then pass --statement N or the "
                "owner's exact words with --said), or run this command in their own terminal")
    elif args.statement is not None:
        statement = challenge.find_statement(q, statement_id=args.statement)
        if statement is None:
            raise QueueError("no recent, unused owner statement S%d" % args.statement)
    decision = args.decision
    words = args.said or (statement["text"] if statement is not None else None)
    if decision is None:
        decision = challenge.classify_decision(words or "")
        if decision is None:
            raise QueueError("the decision is not clear from the words given; use "
                             "--recommendation or --original")
    elif statement is not None:
        read = challenge.classify_decision(statement["text"])
        if read is not None and read != decision:
            raise QueueError("the owner's words (S%d) read as '%s', not '%s'"
                             % (statement["id"], read, decision))
        if read is None and source == "session":
            raise QueueError("the owner's words (S%d) do not state a clear decision; ask them"
                             % statement["id"])
    ids, tasks = _refs(args.refs)
    rows = challenge.targets(q, ids, tasks)
    if not rows:
        raise QueueError("no open concern to decide")
    done = challenge.decide(q, rows, decision,
                            "owner-prompt" if statement is not None else source,
                            response=words, tradeoff=args.tradeoff,
                            statement_id=statement["id"] if statement is not None else None)
    return done


def run(q, args, emit):
    act = args.challenge_action or "list"
    config = util.effective_config(q.dir)
    challenge.release_when_off(q, config)
    if act == "list":
        challenge.ensure_schema(q.conn)
        rows = q.conn.execute("SELECT * FROM challenges WHERE status='open' OR "
                              "(status='decided' AND decided_at >= datetime('now', '-14 days')) "
                              "ORDER BY id").fetchall()
        open_rows = [row for row in rows if row["status"] == "open"]
        text = "challenge_mode: %s (rounds per task: %d)" % (challenge.mode(config),
                                                             challenge.max_rounds(config))
        if not rows:
            text += "\nno open concerns"
        for row in rows:
            text += "\n\n" + challenge.text_of(row)
        if open_rows:
            text += ("\n\nDecide: `moa7amed-queue challenge decide <C id or task> "
                     "--recommendation` or `--original`")
        emit(args, {"mode": challenge.mode(config), "concerns": [challenge.to_json(r) for r in rows]},
             text)
        return 0
    if act == "show":
        ids, tasks = _refs([args.ref])
        rows = [challenge.get(q, ids[0])] if ids else challenge.for_task(q, tasks[0])
        rows = [row for row in rows if row is not None]
        if tasks:
            q.get(tasks[0])                 # a clear error for an unknown task
        emit(args, [challenge.to_json(row) for row in rows],
             "\n\n".join(challenge.text_of(row) for row in rows) or "no concerns recorded")
        return 0
    if act == "review":
        if challenge.mode(config) == "off":
            args.no_hold = True
        found, waiting = challenge.review(q, args.id, hold=not args.no_hold)
        if not found:
            text = ("Task #%d: the rules found no concern (they look for credentials in code, "
                    "switched-off security controls, irreversible data deletion, history "
                    "rewrites, production or publishing steps, skipped checks and "
                    "workarounds). For a deeper review use /moa7amed-queue:grillme." % args.id)
        else:
            text = "\n\n".join(challenge.text_of(row) for row in challenge.for_task(q, args.id))
            if waiting:
                text += "\n\nTask #%d is held until you decide." % args.id
        emit(args, {"task": args.id, "found": found, "waiting": waiting}, text)
        return 0
    if act == "raise":
        source = challenge.provenance()     # a queued task reports MQ_CONCERN instead
        advisory = args.advisory or challenge.mode(config) == "off"
        raised_by = "session" if source == "session" else "owner"
        if args.plan:
            waiting = challenge.raise_for_plan(q, args.plan, args.concern, args.evidence,
                                               args.impact, args.recommendation,
                                               restriction=args.restriction, advisory=advisory,
                                               raised_by=raised_by)
            text = ("Recorded for plan %s%s." % (args.plan, "; its approval waits for the "
                    "owner's decision" if waiting else " (advisory, or already decided on this "
                    "evidence)"))
            emit(args, {"plan": args.plan, "waiting": waiting}, text)
            return 0
        if args.id is None:
            raise QueueError("name the task (or --plan PLAN) the concern is about")
        waiting = challenge.raise_manual(q, args.id, args.concern, args.evidence, args.impact,
                                         args.recommendation, restriction=args.restriction,
                                         advisory=advisory, raised_by=raised_by)
        text = ("Recorded for task #%d%s." % (args.id, "; the task waits for the owner's "
                "decision" if waiting else " (advisory, or already decided on this evidence)"))
        emit(args, {"task": args.id, "waiting": waiting}, text)
        return 0
    if act == "decide":
        done = decide(q, args)
        lines = []
        for item in done:
            if item["decision"] == "acknowledged":
                lines.append("C%d (%s): acknowledged. This is a real limit; the step stays "
                             "out." % (item["id"], item["subject"]))
            elif item["decision"] == "original":
                lines.append("C%d (%s): proceed with the original approach; the trade-off "
                             "is recorded and will not be raised again." % (item["id"], item["subject"]))
            else:
                lines.append("C%d (%s): the recommendation will be followed."
                             % (item["id"], item["subject"]))
        emit(args, {"decided": done}, "\n".join(lines))
        return 0
    raise QueueError("unknown challenge action %s" % act)
