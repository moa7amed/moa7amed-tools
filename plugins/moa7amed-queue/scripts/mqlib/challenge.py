"""Challenge, discuss, then execute (the `grillme` capability).

When a task carries a concrete, evidence-backed risk, the queue says so once, offers a
practical alternative, and lets the owner decide: take the recommendation, or proceed with
the original approach and accept the trade-off. The decision is stored, scoped to the task
and its revision, handed to every later run of the task, and never asked again unless new
evidence changes the risk.

    challenge_mode       off       no optional challenges (approval gates, permissions,
                                   verification and cost limits stay exactly as they are)
                         adaptive  rules check each task before it runs (no model call), and
                                   a task's session may stop once to ask (MQ_CONCERN)
                         explicit  only reviews the owner asks for (`challenge review`, the
                                   /moa7amed-queue:grillme skill)
    challenge_max_rounds how many times one task may be held for a decision; concerns
                         beyond that are recorded as advisories. A concern reopened by
                         new evidence does not use up a round.

Two kinds of concern:

    recommendation  the owner may accept the trade-off and proceed with the original
    restriction     a real limit (the task's tool permissions, provider rules, an
                    authorization the queue does not have). Accepting a risk cannot lift
                    it; the owner can only acknowledge it (the step stays out of the task)
                    or change the permission themselves.

Who may decide: only the owner. A decision is accepted from the owner's own terminal,
from a prompt the owner typed into Claude Code (recorded by the capture hook), or from a
Claude Code session that quotes such a recorded prompt. Queue tasks and agents can never
decide. This prevents a session from recording a decision on its own; it is not a security
boundary against a process that can write the queue database directly.
"""
from __future__ import annotations

import hashlib
import json
import os
import re

from . import util
from .util import QueueError

SCHEMA = """
CREATE TABLE IF NOT EXISTS challenges (
  id             INTEGER PRIMARY KEY AUTOINCREMENT,
  task_id        INTEGER,
  plan_ref       TEXT,
  revision       TEXT NOT NULL,
  rule           TEXT NOT NULL,
  concern_key    TEXT NOT NULL,
  kind           TEXT NOT NULL DEFAULT 'recommendation',
  severity       TEXT NOT NULL DEFAULT 'material',
  concern        TEXT NOT NULL,
  evidence       TEXT NOT NULL,
  evidence_key   TEXT NOT NULL,
  impact         TEXT,
  recommendation TEXT,
  raised_by      TEXT NOT NULL,
  run_id         INTEGER,
  round          INTEGER,
  status         TEXT NOT NULL DEFAULT 'open',
  decision       TEXT,
  tradeoff       TEXT,
  decided_by     TEXT,
  response       TEXT,
  statement_id   INTEGER,
  reopens        INTEGER,
  note           TEXT,
  created_at     TEXT NOT NULL,
  decided_at     TEXT
);
CREATE TABLE IF NOT EXISTS owner_statements (
  id          INTEGER PRIMARY KEY AUTOINCREMENT,
  session_id  TEXT,
  text        TEXT NOT NULL,
  intent      TEXT,
  captured_at TEXT NOT NULL,
  used_by     TEXT
);
"""
MODES = ("off", "adaptive", "explicit")
HOLD = "decision:"
DECISIONS = ("recommendation", "original", "acknowledged")
STATEMENT_MAX_AGE_S = 2 * 3600
CONCERN_RE = re.compile(r"^[ \t>*_`-]*MQ_CONCERN:[ \t]*(.+?)[ \t*_`]*$", re.I | re.M)


# --------------------------------------------------------------------------- rules

def _p(*parts):
    return re.compile("|".join(parts), re.I)


# Each rule: a risk that can be named from the task's own words. Never a preference about
# architecture, framework or style. `restriction_without_shell`: the step needs commands a
# queued task may not run unless the owner has granted them.
RULES = [
    {"id": "secret-in-code", "severity": "material",
     "pattern": _p(r"\bhard[- ]?cod(?:e|ed|ing)\b[^.\n]{0,40}\b(?:api[ _-]?keys?|passwords?|secrets?|"
                   r"tokens?|credentials?|connection strings?)\b",
                   r"\b(?:api[ _-]?keys?|passwords?|secrets?|tokens?|credentials?)\b[^.\n]{0,30}"
                   r"\bhard[- ]?cod",
                   r"\bcommit\b[^.\n]{0,20}\.env\b",
                   r"\b(?:store|save|keep)\b[^.\n]{0,30}\bpasswords?\b[^.\n]{0,30}\b(?:in )?"
                   r"(?:plain ?text|clear ?text|unhashed|unencrypted)\b",
                   r"\bplain ?text passwords?\b"),
     "pattern_ar": re.compile(u"(كلمات السر|كلمه السر|كلمات المرور|كلمه المرور|الباسورد)"
                              u"[^.؟\n]{0,25}(بدون تشفير|نص عادي|زي ما هي)"),
     "concern": "credentials would be kept in the code or in plain text",
     "impact": "anyone with the repository, a backup or a log can read them, and replacing "
               "them later means finding every copy, including the git history",
     "recommendation": "read the value from an environment variable or a secret store, and "
                       "hash passwords with a slow hash (bcrypt, scrypt or Argon2)"},
    {"id": "security-off", "severity": "material",
     "pattern": _p(r"\b(?:disable|turn off|switch off|remove|skip|bypass)\b[^.\n]{0,25}\b(?:csrf|xsrf)\b",
                   r"\b(?:disable|turn off|switch off|skip|bypass|ignore)\b[^.\n]{0,25}\b(?:ssl|tls|"
                   r"certificate|cert)\b[^.\n]{0,15}\b(?:verification|validation|checks?|errors?)\b",
                   r"\bverify\s*=\s*false\b",
                   r"\b(?:disable|turn off|remove|bypass|skip)\b[^.\n]{0,20}\b(?:authentication|"
                   r"authori[sz]ation|auth checks?|access control|permission checks?|login "
                   r"requirement)\b",
                   r"\ballow (?:all|any) origins\b", r"access-control-allow-origin:\s*\*"),
     "concern": "a security control would be switched off",
     "impact": "the application accepts requests or connections it rejects today, and an "
               "exception made for convenience is easily left in place",
     "recommendation": "keep the control and remove the cause instead (trust the specific "
                       "certificate, allow the specific origin, or limit the exception to a "
                       "development setting)"},
    {"id": "destructive-data", "severity": "material",
     "pattern": _p(r"\bdrop\s+(?:the\s+)?(?:[\w.\"`-]+\s+){0,2}(?:table|database|schema|column|"
                   r"collection)s?\b",
                   r"\btruncate\b[^.\n]{0,20}\btables?\b",
                   r"\bdelete (?:all|every)\b[^.\n]{0,25}\b(?:rows?|records?|data|users?|customers?|"
                   r"files?|entries)\b",
                   r"\brm\s+-rf\b",
                   r"\b(?:wipe|destroy)\b[^.\n]{0,20}\b(?:database|data|db|tables?|records?)\b"),
     "pattern_ar": re.compile(u"(احذف|امسح|حذف|مسح)[^.؟\n]{0,25}(البيانات|الجدول|الجداول|"
                              u"قاعده البيانات|السجلات)"),
     "concern": "data would be deleted, and that cannot be undone",
     "impact": "without a backup the deleted data is gone, and a mistake in the selection "
               "removes more than intended",
     "recommendation": "take a backup or an export first and make the change a reversible "
                       "migration (or mark the rows deleted instead of removing them)"},
    {"id": "history-rewrite", "severity": "material", "restriction_without_shell": True,
     "pattern": _p(r"\bforce[- ]push\b", r"\bpush\s+(?:-f|--force)\b",
                   r"\brewrite\b[^.\n]{0,15}\b(?:git |commit )?history\b", r"\breset\s+--hard\b",
                   r"\bfilter-(?:branch|repo)\b"),
     "concern": "the git history would be rewritten",
     "impact": "commits other people or other clones rely on disappear, and work done on top "
               "of them has to be recovered by hand",
     "recommendation": "add a new commit that reverts or fixes the change, or rewrite on a "
                       "separate branch after a backup"},
    {"id": "production-publish", "severity": "material", "restriction_without_shell": True,
     "pattern": _p(r"\b(?:deploy|release|ship|publish)\b[^.\n]{0,25}\b(?:to |on |in )?(?:the )?"
                   r"(?:production|prod|live (?:server|site|environment)|npm|pypi|app store|"
                   r"play store)\b",
                   r"\bpush\b[^.\n]{0,12}\b(?:to )?(?:origin|main|master)\b",
                   r"\b(?:on|against|in) (?:the )?production (?:server|database|db|environment)\b"),
     "concern": "the task would change production or publish something",
     "impact": "an unattended session would act on a live system or on something public, "
               "where a mistake reaches users before anyone reviews it",
     "recommendation": "let the task prepare and verify the change, then run the production "
                       "or publishing step yourself"},
    {"id": "skip-verification", "severity": "advisory",
     "pattern": _p(r"\b(?:without|skip|skipping|don'?t (?:write|add|run))\s+(?:writing\s+|adding\s+|"
                   r"running\s+|the\s+|any\s+)*(?:unit\s+)?(?:tests?|linting|the linter|type ?checks?)\b",
                   r"--no-verify\b"),
     "concern": "tests or checks would be skipped",
     "impact": "a regression can go unnoticed; the queue still requires a verification "
               "report, so the task will say how it checked the result instead",
     "recommendation": "keep at least one focused test or check for the changed behaviour"},
    {"id": "workaround", "severity": "advisory",
     "pattern": _p(r"\b(?:quick|temporary|dirty) (?:hack|workaround)\b", r"\bhack around\b",
                   r"\b(?:silence|suppress|ignore) (?:the |all )?(?:warnings?|errors?|failing tests?)\b",
                   r"eslint-disable", r"#\s*type:\s*ignore", r"@ts-ignore"),
     "concern": "the change would hide a problem rather than fix it",
     "impact": "the underlying fault stays and is harder to find later",
     "recommendation": "fix the cause, or leave a dated note next to the workaround"},
]
RULES_BY_ID = {rule["id"]: rule for rule in RULES}


def ensure_schema(conn):
    found = conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND "
                         "name='owner_statements'").fetchone()
    if found is None:
        conn.executescript(SCHEMA)


def mode(config):
    value = str(config.get("challenge_mode") or "adaptive")
    return value if value in MODES else "adaptive"


def max_rounds(config):
    try:
        return max(0, int(config.get("challenge_max_rounds", 1)))
    except (TypeError, ValueError):
        return 1


def shell_allowed(config):
    """Can a queued task's session run commands? Only if the owner granted it."""
    if str(config.get("permission_mode") or "") == "bypassPermissions":
        return True
    granted = list(config.get("allowed_tools") or []) + list(config.get("claude_args") or [])
    return any(re.match(r"^(Bash|PowerShell)\b", str(item)) for item in granted)


def _key(*parts):
    return hashlib.sha1("\x1f".join(str(part) for part in parts).encode("utf-8")).hexdigest()[:16]


def _normal(text):
    return re.sub(r"\s+", " ", str(text or "").strip().lower())


def task_revision(task):
    """A task's prompt and criteria never change once queued; the revision ties a decision
    to exactly that text."""
    return "t:" + _key(task["prompt"], task["acceptance"] or "")


def check_text(text, config):
    """Rule-based concerns in a task's words. No model call; evidence is the quoted words."""
    from . import router_ar
    raw = text or ""
    normalised = router_ar.normalize(raw)
    shell = shell_allowed(config)
    found = []
    for rule in RULES:
        hits = [match.group(0) for match in rule["pattern"].finditer(raw)]
        if rule.get("pattern_ar") is not None:
            hits += [match.group(0) for match in rule["pattern_ar"].finditer(normalised)]
        if not hits:
            continue
        quoted = []
        for hit in hits:
            hit = re.sub(r"\s+", " ", hit.strip())[:80]
            if hit.lower() not in [item.lower() for item in quoted]:
                quoted.append(hit)
        restriction = bool(rule.get("restriction_without_shell")) and not shell
        concern = {
            "rule": rule["id"], "concern_key": rule["id"],
            "kind": "restriction" if restriction else "recommendation",
            "severity": "material" if restriction else rule["severity"],
            "concern": rule["concern"],
            "evidence": "the task asks: %s" % "; ".join('"%s"' % item for item in quoted),
            "evidence_key": "|".join(sorted(_normal(item) for item in quoted)),
            "impact": rule["impact"], "recommendation": rule["recommendation"],
        }
        if restriction:
            concern["impact"] = ("queued tasks run without permission to execute commands "
                                 "(permission_mode %s, no Bash or PowerShell in allowed_tools), "
                                 "so the session cannot do this step; accepting the risk does "
                                 "not change that" % config.get("permission_mode"))
            concern["recommendation"] = ("run that step yourself after the task, or grant the "
                                         "specific command in allowed_tools if you want queued "
                                         "tasks to run it")
        found.append(concern)
    return found


def parse_reported(text):
    """MQ_CONCERN lines a task session wrote:
    MQ_CONCERN: <concern> | evidence: <...> | impact: <...> | recommendation: <...>"""
    found = []
    for match in CONCERN_RE.finditer(text or ""):
        fields = {"concern": "", "evidence": "", "impact": "", "recommendation": ""}
        parts = [part.strip() for part in match.group(1).split("|")]
        fields["concern"] = parts[0]
        for part in parts[1:]:
            name, _, value = part.partition(":")
            name = name.strip().lower()
            if name in fields and value.strip():
                fields[name] = value.strip()
        if not fields["concern"]:
            continue
        if not fields["evidence"]:
            fields["evidence"] = "(no evidence given)"
        found.append({
            "rule": "reported", "concern_key": "r:" + _key(_normal(fields["concern"])[:120]),
            "kind": "recommendation", "severity": "material",
            "concern": fields["concern"][:300], "evidence": fields["evidence"][:500],
            "evidence_key": _normal(fields["evidence"])[:500],
            "impact": fields["impact"][:300] or None,
            "recommendation": fields["recommendation"][:300] or None})
    return found


# --------------------------------------------------------------------------- storage

def get(q, challenge_id):
    ensure_schema(q.conn)
    return q.conn.execute("SELECT * FROM challenges WHERE id=?", (challenge_id,)).fetchone()


def for_task(q, task_id, statuses=None):
    ensure_schema(q.conn)
    rows = q.conn.execute("SELECT * FROM challenges WHERE task_id=? ORDER BY id",
                          (task_id,)).fetchall()
    return [row for row in rows if statuses is None or row["status"] in statuses]


def open_blocking(q, task_id=None):
    ensure_schema(q.conn)
    sql = "SELECT * FROM challenges WHERE status='open' AND severity='material'"
    args = ()
    if task_id is not None:
        sql += " AND task_id=?"
        args = (task_id,)
    return q.conn.execute(sql + " ORDER BY id", args).fetchall()


def _rounds_used(q, task_id):
    row = q.conn.execute("SELECT COUNT(DISTINCT round) AS n FROM challenges WHERE task_id=? "
                         "AND round IS NOT NULL", (task_id,)).fetchone()
    return row["n"] or 0


def _covered(old_key, new_key):
    """New evidence is material only when it adds something the decision did not see."""
    old = set(filter(None, (old_key or "").split("|")))
    new = set(filter(None, (new_key or "").split("|")))
    return new <= old if new else True


def record(q, task, concerns, raised_by, config, run_id=None, allow_round=True):
    """Store concerns for a task (inside a transaction). Returns the ids of the material
    concerns that now wait for the owner. A concern the owner already decided for this
    revision is not raised again unless its evidence is new; a concern beyond the round
    limit is kept as an advisory."""
    ensure_schema(q.conn)
    revision = task_revision(task)
    stamp = util.now_iso()
    waiting, round_number = [], None
    used = _rounds_used(q, task["id"])
    for item in concerns:
        earlier = q.conn.execute(
            "SELECT * FROM challenges WHERE task_id=? AND concern_key=? ORDER BY id DESC LIMIT 1",
            (task["id"], item["concern_key"])).fetchone()
        reopens = None
        if earlier is not None and earlier["status"] != "withdrawn":
            if earlier["status"] == "open":
                continue                        # already waiting; asked once
            same_revision = earlier["revision"] == revision
            if same_revision and _covered(earlier["evidence_key"], item["evidence_key"]):
                continue                        # decided (or noted) on this evidence
            if earlier["status"] == "decided":
                reopens = earlier["id"]         # new evidence: the question is open again
        severity, status, note = item["severity"], "open", None
        if severity != "material":
            status = "advisory"
        elif reopens is None and item["kind"] != "restriction":
            if not allow_round or used >= max_rounds(config):
                severity, status = "advisory", "advisory"
                note = ("recorded as an advisory: this task already used its %d decision "
                        "round(s) (challenge_max_rounds)" % max_rounds(config))
        number = None
        if status == "open":
            if reopens is None:
                if round_number is None:
                    round_number = used + 1
                number = round_number
        cursor = q.conn.execute(
            "INSERT INTO challenges(task_id, revision, rule, concern_key, kind, severity, "
            "concern, evidence, evidence_key, impact, recommendation, raised_by, run_id, round, "
            "status, reopens, note, created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (task["id"], revision, item["rule"], item["concern_key"], item["kind"], severity,
             item["concern"], item["evidence"], item["evidence_key"], item.get("impact"),
             item.get("recommendation"), raised_by, run_id, number, status, reopens, note,
             stamp))
        if status == "open":
            waiting.append(cursor.lastrowid)
            q.log_event(task["id"], run_id, "challenge",
                        "C%d %s: %s%s" % (cursor.lastrowid, item["kind"], item["concern"],
                                          " (reopened by new evidence, was C%d)" % reopens
                                          if reopens else ""))
    return waiting


def hold_reason(q, task_id):
    rows = open_blocking(q, task_id)
    if not rows:
        return None
    first = rows[0]
    more = " (+%d more)" % (len(rows) - 1) if len(rows) > 1 else ""
    if all(row["kind"] == "restriction" for row in rows):
        choice = "acknowledge it (the step stays out of the task) or cancel the task"
    else:
        choice = "take the recommendation, or proceed with the original approach"
    return ("%s %s%s. Read it with `moa7amed-queue challenge show %d`, then %s"
            % (HOLD, first["concern"], more, task_id, choice))


def _set_hold(q, task_id, config):
    """Mark the task blocked while a decision is open; clear a stale decision hold."""
    from . import routing
    reason = hold_reason(q, task_id)
    row = routing.get(q, task_id)
    current = row["blocked_reason"] if row is not None else None
    if reason:
        if current and not str(current).startswith(HOLD):
            return current                      # another hold (approval, agents) comes first
        if row is None:
            routing.classify_and_store(q, q.get(task_id), config)
        q.conn.execute("UPDATE task_routing SET blocked_reason=? WHERE task_id=?",
                       (reason, task_id))
        return reason
    if current and str(current).startswith(HOLD):
        q.conn.execute("UPDATE task_routing SET blocked_reason=NULL WHERE task_id=?", (task_id,))
    return None


def gate(q, task, config):
    """Called before a task is claimed. In adaptive mode the rules look at the task first.
    Returns the hold reason when the task must wait for the owner, else None."""
    current = mode(config)
    if current == "off":
        return None
    with q.tx():
        if current == "adaptive":
            record(q, task, check_text("%s\n%s" % (task["prompt"], task["acceptance"] or ""),
                                       config), "rules", config)
        reason = _set_hold(q, task["id"], config)
    update_marker(q)
    return reason


def release_when_off(q, config):
    """With challenge_mode off, optional challenges hold nothing (approval gates stay)."""
    if mode(config) != "off":
        return []
    ensure_schema(q.conn)
    rows = q.conn.execute("SELECT task_id FROM task_routing WHERE blocked_reason LIKE ?",
                          (HOLD + "%",)).fetchall()
    if not rows:
        return []
    with q.tx():
        for row in rows:
            q.conn.execute("UPDATE task_routing SET blocked_reason=NULL WHERE task_id=?",
                           (row["task_id"],))
            q.conn.execute("UPDATE challenges SET status='withdrawn', note='challenge_mode off' "
                           "WHERE task_id=? AND status='open'", (row["task_id"],))
            q.log_event(row["task_id"], None, "challenge", "released: challenge_mode is off")
    update_marker(q)
    return [row["task_id"] for row in rows]


def after_run(q, task, run, text, changed, phase, config, outcome):
    """Concerns a task session reported. A session that stopped before changing anything
    waits for the owner (no attempt is used, nothing ran twice); after changes, or in the
    planning phase, the concerns are recorded for the owner and the run counts as usual.
    Returns the hold message when the task now waits, else None."""
    if mode(config) == "off" or not text:
        return None
    reported = parse_reported(text)
    if not reported:
        return None
    stops = (mode(config) == "adaptive" and phase in (None, "implement") and not changed
             and outcome == "failed")
    with q.tx():
        if not stops:
            for item in reported:
                if phase != "plan":
                    item["severity"] = "advisory"
            waiting = record(q, task, reported, "task-run", config, run_id=run["id"],
                             allow_round=phase == "plan" and mode(config) == "adaptive")
            if phase == "plan" and waiting:
                _set_hold(q, task["id"], config)
            reason = None
        else:
            waiting = record(q, task, reported, "task-run", config, run_id=run["id"])
            reason = _set_hold(q, task["id"], config) if waiting else None
    update_marker(q)
    if stops and waiting:
        return reason or hold_reason(q, task["id"])
    return None


# --------------------------------------------------------------------------- decisions

_NEGATION = re.compile(r"\b(?:don'?t|do not|never|not|no)\b[^.,;!?]{0,12}\b(?:proceed|go ahead|"
                       r"continue|do it|accept|use|follow|keep)\b", re.I)
_ORIGINAL = [re.compile(p, re.I) for p in (
    r"\b(?:proceed|go ahead|continue|carry on|keep going|do it|go on)\b[^.!?]{0,40}\b(?:original|"
    r"as (?:i|we) (?:said|asked|planned|requested|wrote)|anyway|regardless|my (?:way|approach|plan)|"
    r"as is|as requested|as planned|as written)\b",
    r"\b(?:i|we)(?: fully)? (?:understand|accept|acknowledge|know|am aware of|are aware of)"
    r" (?:the |these |this |all |those )?(?:risks?|trade-?offs?|concerns?|downsides?|consequences)\b",
    r"\b(?:keep|stick with|stay with) (?:my|the original|our|the current) (?:approach|plan|way|"
    r"design|choice|solution|request)\b",
    r"\b(?:reject|decline|ignore|override) (?:the|your) (?:recommendation|suggestion|warning|"
    r"advice|alternative)\b",
    r"\boriginal approach\b")]
_RECOMMENDATION = [re.compile(p, re.I) for p in (
    r"\b(?:accept|take|use|follow|go with|apply|adopt|agree with|do) (?:the|your) (?:recommendation|"
    r"recommended (?:approach|option|alternative|fix)|suggestion|alternative|advice|proposal)\b",
    r"\b(?:do it|go with) your way\b", r"\bswitch to the (?:alternative|recommended)\b",
    r"\bfollow the recommendation\b")]
_AR_ORIGINAL = re.compile(u"((كمل|استمر|نفذ|امشي|اكمل)[^.؟!\n]{0,30}(زي ما|كما|بالطريقه الاصليه|"
                          u"بطريقتي|برضه|علي اي حال|رغم))|((فاهم|مدرك|عارف|اقبل|قابل)"
                          u"[^.؟!\n]{0,10}(المخاطر|المخاطره|العواقب))")
_AR_RECOMMENDATION = re.compile(u"(موافق|اقبل|نفذ|طبق|خد|خذ|استخدم|امشي)[^.؟!\n]{0,15}"
                                u"(التوصيه|الاقتراح|البديل|اقتراحك|توصيتك)")
_AR_NEGATION = re.compile(u"(لا|مش|ما)\\s*(تكمل|تنفذ|تستمر|تمشي)")


def classify_decision(text):
    """'original', 'recommendation' or None (not a clear decision). No special phrase is
    needed; a question, a negation or a message that says both is not a decision."""
    from . import router_ar
    raw = (text or "").strip()
    if not raw or len(raw) > 600 or raw.endswith(("?", u"؟")):
        return None
    normalised = router_ar.normalize(raw)
    if _NEGATION.search(raw) or _AR_NEGATION.search(normalised):
        return None
    original = any(p.search(raw) for p in _ORIGINAL) or bool(_AR_ORIGINAL.search(normalised))
    recommended = any(p.search(raw) for p in _RECOMMENDATION) or bool(
        _AR_RECOMMENDATION.search(normalised))
    if original and recommended:
        return None
    if recommended:
        return "recommendation"
    return "original" if original else None


def references(text):
    """Challenge ids (C3) and task ids (#5, task 5) named in a message."""
    challenges = [int(n) for n in re.findall(r"\bC(\d+)\b", text or "")]
    tasks = [int(n) for n in re.findall(r"(?:#|\btask\s+#?)(\d+)\b", text or "", re.I)]
    return challenges, tasks


def provenance():
    """Where a decision command comes from: 'terminal' (the owner's own shell), 'session'
    (a shell Claude Code started: needs the owner's recorded words) or raises for a queued
    task or agent, which can never decide."""
    if util.env("RUNNER") or util.env("TASK_ID") or util.env("AGENT"):
        raise QueueError("a queued task or agent cannot make decisions for the owner; the "
                         "owner decides in Claude Code or in a terminal")
    if os.environ.get("CLAUDECODE") or os.environ.get("CLAUDE_CODE_ENTRYPOINT"):
        return "session"
    return "terminal"


def capture_statement(q, text, session_id, intent):
    ensure_schema(q.conn)
    with q.tx():
        cursor = q.conn.execute("INSERT INTO owner_statements(session_id, text, intent, "
                                "captured_at) VALUES(?,?,?,?)",
                                (session_id, text[:2000], intent, util.now_iso()))
    return cursor.lastrowid


def find_statement(q, statement_id=None, said=None):
    """An unused prompt the owner typed recently, by id or by its words."""
    ensure_schema(q.conn)
    rows = q.conn.execute("SELECT * FROM owner_statements WHERE used_by IS NULL ORDER BY id "
                          "DESC LIMIT 50").fetchall()
    now = util.now()
    for row in rows:
        try:
            age = (now - util.parse_iso(row["captured_at"])).total_seconds()
        except (TypeError, ValueError):
            continue
        if age > STATEMENT_MAX_AGE_S:
            continue
        if statement_id is not None and row["id"] == statement_id:
            return row
        if said and _normal(said) and _normal(said) in _normal(row["text"]):
            return row
    return None


def targets(q, ids=None, task_ids=None):
    """Open challenges named by id, by task, or (with neither) every open one."""
    ensure_schema(q.conn)
    rows = []
    for challenge_id in ids or []:
        row = get(q, challenge_id)
        if row is None:
            raise QueueError("challenge C%d does not exist" % challenge_id)
        if row["status"] != "open":
            raise QueueError("challenge C%d is not open (%s)" % (challenge_id, row["status"]))
        rows.append(row)
    for task_id in task_ids or []:
        rows += [row for row in open_blocking(q, task_id) if row["id"] not in
                 [item["id"] for item in rows]]
    if not ids and not task_ids:
        rows = list(open_blocking(q))
    return rows


def decide(q, rows, decision, decided_by, response=None, tradeoff=None, statement_id=None):
    """Record the owner's decision on open challenges and release the tasks they held.
    'original' on a restriction is recorded as 'acknowledged': the limit stays."""
    if decision not in ("recommendation", "original"):
        raise QueueError("a decision is 'recommendation' or 'original'")
    if not rows:
        raise QueueError("no open challenge to decide")
    stamp = util.now_iso()
    done, tasks = [], set()
    config = util.effective_config(q.dir)
    with q.tx():
        for row in rows:
            fresh = get(q, row["id"])
            if fresh is None or fresh["status"] != "open":
                continue
            value = decision
            if decision == "original" and fresh["kind"] == "restriction":
                value = "acknowledged"
            accepted = tradeoff
            if value == "original" and not accepted:
                accepted = fresh["impact"] or fresh["concern"]
            q.conn.execute(
                "UPDATE challenges SET status='decided', decision=?, tradeoff=?, decided_by=?, "
                "response=?, statement_id=?, decided_at=? WHERE id=?",
                (value, accepted if value == "original" else None, decided_by,
                 (response or "")[:2000] or None, statement_id, stamp, fresh["id"]))
            q.log_event(fresh["task_id"], None, "challenge-decided",
                        "C%d: %s (by the owner, %s)" % (fresh["id"], value, decided_by))
            done.append({"id": fresh["id"], "task": fresh["task_id"], "plan": fresh["plan_ref"],
                         "subject": subject(fresh), "decision": value,
                         "kind": fresh["kind"], "concern": fresh["concern"]})
            if fresh["task_id"] is not None:
                tasks.add(fresh["task_id"])
        if statement_id is not None:
            q.conn.execute("UPDATE owner_statements SET used_by=? WHERE id=?",
                           (",".join("C%d" % item["id"] for item in done), statement_id))
        for task_id in tasks:
            _set_hold(q, task_id, config)
    update_marker(q)
    return done


def raise_manual(q, task_id, concern, evidence, impact=None, recommendation=None,
                 restriction=False, advisory=False, raised_by="session"):
    """A concern from an explicit review (the grillme skill or the owner)."""
    task = q.get(task_id)
    if task["status"] not in ("pending", "failed"):
        raise QueueError("task #%d is %s; only pending or failed tasks can be held for a "
                         "decision" % (task_id, task["status"]))
    if not (evidence or "").strip():
        raise QueueError("a concern needs its evidence (--evidence): what in the task, the "
                         "code or the plan shows the risk")
    item = {"rule": "review", "concern_key": "v:" + _key(_normal(concern)[:120]),
            "kind": "restriction" if restriction else "recommendation",
            "severity": "advisory" if advisory else "material", "concern": concern[:300],
            "evidence": evidence[:500], "evidence_key": _normal(evidence)[:500],
            "impact": (impact or "")[:300] or None,
            "recommendation": (recommendation or "")[:300] or None}
    config = util.effective_config(q.dir)
    with q.tx():
        waiting = record(q, task, [item], raised_by, config)
        _set_hold(q, task_id, config)
    update_marker(q)
    return waiting


def plan_revision(q, plan_id):
    from . import plan_store
    plan_store.ensure_schema(q)
    row = q.conn.execute("SELECT revision FROM plans WHERE id=?", (plan_id,)).fetchone()
    if row is None:
        raise QueueError("no plan %s" % plan_id)
    return "p:%s@%d" % (plan_id, row["revision"])


def open_for_plan(q, plan_id):
    """Material concerns about the plan's current revision that wait for the owner."""
    ensure_schema(q.conn)
    try:
        revision = plan_revision(q, plan_id)
    except QueueError:
        return []
    return q.conn.execute("SELECT * FROM challenges WHERE plan_ref=? AND revision=? AND "
                          "status='open' AND severity='material' ORDER BY id",
                          (plan_id, revision)).fetchall()


def raise_for_plan(q, plan_id, concern, evidence, impact=None, recommendation=None,
                   restriction=False, advisory=False, raised_by="session"):
    """A concern about a project plan. It holds the plan's approval for execution (in
    `project approve`) until the owner decides; it is scoped to the plan's revision."""
    if not (evidence or "").strip():
        raise QueueError("a concern needs its evidence (--evidence): what in the task, the "
                         "code or the plan shows the risk")
    ensure_schema(q.conn)
    revision = plan_revision(q, plan_id)
    key = "v:" + _key(_normal(concern)[:120])
    evidence_key = _normal(evidence)[:500]
    earlier = q.conn.execute("SELECT * FROM challenges WHERE plan_ref=? AND concern_key=? "
                             "ORDER BY id DESC LIMIT 1", (plan_id, key)).fetchone()
    reopens = None
    if earlier is not None and earlier["status"] != "withdrawn":
        if earlier["status"] == "open" or (earlier["revision"] == revision and
                                           _covered(earlier["evidence_key"], evidence_key)):
            return []
        if earlier["status"] == "decided":
            reopens = earlier["id"]
    status = "advisory" if advisory else "open"
    with q.tx():
        cursor = q.conn.execute(
            "INSERT INTO challenges(plan_ref, revision, rule, concern_key, kind, severity, "
            "concern, evidence, evidence_key, impact, recommendation, raised_by, status, "
            "reopens, created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (plan_id, revision, "review", key, "restriction" if restriction else "recommendation",
             "advisory" if advisory else "material", concern[:300], evidence[:500], evidence_key,
             (impact or "")[:300] or None, (recommendation or "")[:300] or None, raised_by,
             status, reopens, util.now_iso()))
        q.log_event(None, None, "challenge", "C%d plan %s: %s" % (cursor.lastrowid, plan_id,
                                                                   concern[:200]))
    update_marker(q)
    return [cursor.lastrowid] if status == "open" else []


def review(q, task_id, hold=True):
    """An explicit review by the rules, whatever the mode. Returns (concerns, waiting ids)."""
    task = q.get(task_id)
    config = util.effective_config(q.dir)
    found = check_text("%s\n%s" % (task["prompt"], task["acceptance"] or ""), config)
    waiting = []
    if found:
        if not hold:
            for item in found:
                item["severity"] = "advisory"
        with q.tx():
            waiting = record(q, task, found, "review", config)
            _set_hold(q, task_id, config)
        update_marker(q)
    return found, waiting


# --------------------------------------------------------------------------- prompt

def prompt_block(q, task, route, config):
    """What a task's session needs to know: the owner's decisions (follow them, do not
    argue again), advisories, and in adaptive mode how to ask once."""
    ensure_schema(q.conn)
    rows = for_task(q, task["id"])
    revision = task_revision(task)
    decided = [row for row in rows if row["status"] == "decided" and row["revision"] == revision]
    advisories = [row for row in rows if row["status"] == "advisory"]
    current = mode(config)
    lines = []
    if decided:
        lines += ["", "<owner_decisions>",
                  "The owner has decided these concerns. Follow each decision; do not raise "
                  "them again or substitute another approach. Each applies only to this task."]
        for row in decided:
            if row["decision"] == "recommendation":
                lines.append("- C%d (%s): the owner chose the recommendation: %s"
                             % (row["id"], row["concern"], row["recommendation"] or "-"))
            elif row["decision"] == "acknowledged":
                lines.append("- C%d (%s): a real limit, acknowledged by the owner. Do not try "
                             "this step and do not work around the limit; say in your summary "
                             "that it was left out." % (row["id"], row["concern"]))
            else:
                lines.append("- C%d (%s): the owner chose the original approach and accepts "
                             "this trade-off: %s. Do it as asked, with reasonable safeguards "
                             "that fit that approach, and report its limitations."
                             % (row["id"], row["concern"], row["tradeoff"] or "-"))
        lines += ["Accepting these risks does not permit anything else: no other destructive "
                  "operation, production change, publishing or extra spending.",
                  "</owner_decisions>"]
    if advisories:
        lines += ["", "<advisories>", "Noted, no decision needed; handle sensibly and mention "
                  "it in your summary:"]
        for row in advisories[-5:]:
            lines.append("- %s (%s)" % (row["concern"], row["evidence"][:160]))
        lines.append("</advisories>")
    phase = (route or {}).get("phase")
    if current == "adaptive" and phase != "review":
        can_stop = _rounds_used(q, task["id"]) < max_rounds(config)
        lines += ["", "<concerns>"]
        if phase == "plan":
            lines.append("If the plan meets a material, evidence-backed risk the owner should "
                         "decide (data loss, weakened security, an irreversible or public "
                         "action, a contradiction in the request), add one line per concern "
                         "to the plan:")
        elif can_stop:
            lines.append("Only for a material, evidence-backed risk the owner should decide "
                         "(data loss, weakened security, an irreversible or public action, a "
                         "contradiction in the request): stop BEFORE changing any file, end "
                         "with one line per concern, and then MQ_RESULT: FAILED - waiting for "
                         "the owner's decision:")
        else:
            lines.append("The owner's decision round for this task is used: do not stop to "
                         "ask. Proceed under the decisions above; report remaining concerns "
                         "as lines like this at the end, without stopping:")
        lines += ["  MQ_CONCERN: <concern> | evidence: <file:line or fact> | impact: <what "
                  "happens> | recommendation: <practical alternative>",
                  "Never stop for style, framework or architecture preferences, or for routine "
                  "reversible choices: decide those yourself.", "</concerns>"]
    return lines


# --------------------------------------------------------------------------- views

def describe(q, task_id):
    rows = for_task(q, task_id)
    return [dict(row) for row in rows]


def summary_line(q, task_id):
    rows = for_task(q, task_id)
    if not rows:
        return None
    counts = {}
    for row in rows:
        counts[row["status"]] = counts.get(row["status"], 0) + 1
    return ", ".join("%d %s" % (n, status) for status, n in sorted(counts.items()))


def marker_path(q):
    return os.path.join(util.home(), "open-decisions", os.path.basename(q.dir))


def update_marker(q):
    """A file per project with open decisions, so the capture hook can tell, without
    starting Python, whether an ordinary prompt might be the owner's answer."""
    path = marker_path(q)
    try:
        if open_blocking(q):
            os.makedirs(os.path.dirname(path), exist_ok=True)
            if not os.path.exists(path):
                with open(path, "w", encoding="utf-8") as handle:
                    handle.write(q.root + "\n")
        elif os.path.exists(path):
            os.remove(path)
    except OSError:
        pass


def subject(row):
    if row["task_id"] is not None:
        return "task #%d" % row["task_id"]
    return "plan %s" % row["plan_ref"]


def to_json(row):
    data = dict(row)
    data["ref"] = "C%d" % row["id"]
    data["subject"] = subject(row)
    return data


def text_of(row):
    lines = ["C%d  %s  %s %s  [%s]" % (row["id"], subject(row), row["severity"],
                                      row["kind"], row["status"]),
             "  Concern:        %s" % row["concern"],
             "  Evidence:       %s" % row["evidence"]]
    if row["impact"]:
        lines.append("  Impact:         %s" % row["impact"])
    if row["recommendation"]:
        lines.append("  Recommendation: %s" % row["recommendation"])
    if row["kind"] == "restriction":
        lines.append("  Note:           a real limit; accepting the risk cannot lift it")
    if row["reopens"]:
        lines.append("  Reopens:        C%d (new evidence)" % row["reopens"])
    if row["note"]:
        lines.append("  Note:           %s" % row["note"])
    if row["status"] == "decided":
        lines.append("  Decision:       %s (%s, %s)" % (row["decision"], row["decided_by"],
                                                    util.local_time(row["decided_at"])))
        if row["tradeoff"]:
            lines.append("  Accepted:       %s" % row["tradeoff"])
        if row["response"]:
            lines.append("  Owner said:     %s" % row["response"][:200])
    lines.append("  Raised:         %s by %s, revision %s" % (
        util.local_time(row["created_at"]), row["raised_by"], row["revision"]))
    return "\n".join(lines)
