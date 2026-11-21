"""Persistent queue storage: one SQLite database per project."""
from __future__ import annotations

import contextlib
import json
import os
import re
import sqlite3

from . import routing, util
from .util import QueueError

SCHEMA_VERSION = "1"

SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (
  key   TEXT PRIMARY KEY,
  value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS tasks (
  id             INTEGER PRIMARY KEY AUTOINCREMENT,
  title          TEXT NOT NULL,
  prompt         TEXT NOT NULL,
  acceptance     TEXT,
  workdir        TEXT NOT NULL DEFAULT '',
  priority       INTEGER NOT NULL DEFAULT 0,
  status         TEXT NOT NULL DEFAULT 'pending'
                 CHECK (status IN ('pending','running','done','failed','cancelled')),
  attempts       INTEGER NOT NULL DEFAULT 0,
  max_attempts   INTEGER NOT NULL DEFAULT 2 CHECK (max_attempts >= 1),
  not_before     TEXT,
  source         TEXT NOT NULL DEFAULT 'cli',
  session_id     TEXT,
  created_at     TEXT NOT NULL,
  updated_at     TEXT NOT NULL,
  started_at     TEXT,
  finished_at    TEXT,
  last_error     TEXT,
  result_summary TEXT,
  commit_sha     TEXT
);

CREATE TABLE IF NOT EXISTS task_deps (
  task_id    INTEGER NOT NULL REFERENCES tasks(id),
  depends_on INTEGER NOT NULL REFERENCES tasks(id),
  PRIMARY KEY (task_id, depends_on),
  CHECK (task_id != depends_on)
);

CREATE TABLE IF NOT EXISTS runs (
  id            INTEGER PRIMARY KEY AUTOINCREMENT,
  task_id       INTEGER NOT NULL REFERENCES tasks(id),
  attempt       INTEGER NOT NULL,
  runner_id     TEXT NOT NULL,
  host          TEXT,
  started_at    TEXT NOT NULL,
  finished_at   TEXT,
  outcome       TEXT,
  exit_code     INTEGER,
  error         TEXT,
  cwd           TEXT,
  workdir       TEXT,
  child_pid     INTEGER,
  is_git        INTEGER NOT NULL DEFAULT 0,
  clean_start   INTEGER NOT NULL DEFAULT 0,
  branch        TEXT,
  head_before   TEXT,
  head_after    TEXT,
  commit_sha    TEXT,
  stash_ref     TEXT,
  files_changed TEXT,
  session_id    TEXT,
  cost_usd      REAL,
  duration_ms   INTEGER,
  num_turns     INTEGER,
  result_text   TEXT,
  prompt_file   TEXT,
  stdout_file   TEXT,
  stderr_file   TEXT
);

CREATE TABLE IF NOT EXISTS events (
  id      INTEGER PRIMARY KEY AUTOINCREMENT,
  ts      TEXT NOT NULL,
  task_id INTEGER,
  run_id  INTEGER,
  type    TEXT NOT NULL,
  detail  TEXT
);

CREATE TABLE IF NOT EXISTS runner_lock (
  id             INTEGER PRIMARY KEY CHECK (id = 1),
  runner_id      TEXT NOT NULL,
  pid            INTEGER NOT NULL,
  host           TEXT NOT NULL,
  kind           TEXT,
  started_at     TEXT NOT NULL,
  heartbeat_at   TEXT NOT NULL,
  options        TEXT NOT NULL DEFAULT '{}',
  tasks_run      INTEGER NOT NULL DEFAULT 0,
  current_run_id INTEGER
);

CREATE INDEX IF NOT EXISTS idx_tasks_status ON tasks(status, priority, id);
CREATE INDEX IF NOT EXISTS idx_runs_task ON runs(task_id);
CREATE INDEX IF NOT EXISTS idx_events_task ON events(task_id);

-- The original request is the contract for a task: it can never be rewritten or deleted.
CREATE TRIGGER IF NOT EXISTS tasks_original_is_immutable
BEFORE UPDATE OF prompt, acceptance, workdir, created_at, source ON tasks
BEGIN
  SELECT RAISE(ABORT, 'the original prompt and acceptance criteria of a task are immutable');
END;

CREATE TRIGGER IF NOT EXISTS tasks_are_never_deleted
BEFORE DELETE ON tasks
BEGIN
  SELECT RAISE(ABORT, 'tasks are never deleted; cancel them instead');
END;
"""

# Added in 0.3.1, next to the existing tables (which are never altered). It records when a
# runner or task process started, so a reused process id is not mistaken for a live process.
ADDITIVE_SCHEMA = """
CREATE TABLE IF NOT EXISTS process_tokens (
  kind  TEXT NOT NULL,
  ref   TEXT NOT NULL,
  pid   INTEGER NOT NULL,
  token TEXT,
  PRIMARY KEY (kind, ref)
);
"""

PRIORITY_NAMES = {"low": -10, "normal": 0, "high": 10, "urgent": 20}
ACTIVE_STATES = ("pending", "running", "failed")


def parse_priority(value):
    if value is None:
        return 0
    text = str(value).strip().lower()
    if text in PRIORITY_NAMES:
        return PRIORITY_NAMES[text]
    try:
        return int(text)
    except ValueError:
        raise QueueError("invalid priority '%s' (use low, normal, high, urgent or an integer; "
                         "higher runs first)" % value)


def priority_label(number):
    for name, value in PRIORITY_NAMES.items():
        if value == number:
            return name
    return str(number)


def derive_title(prompt):
    for line in prompt.splitlines():
        line = re.sub(r"\s+", " ", line).strip(" #*-\t")
        if line:
            return line if len(line) <= 72 else line[:69].rstrip() + "..."
    return "(untitled)"


# "Acceptance criteria: ...", "**Acceptance:** ...", or a bare "## Acceptance criteria" heading.
_ACCEPTANCE_RE = re.compile(
    r"^[ \t>*#-]*acceptance(?:[ \t]+criteri(?:a|on))?\**[ \t]*(?::[ \t]*\**[ \t]*(.*))?$",
    re.IGNORECASE | re.MULTILINE)


def extract_acceptance(prompt):
    """Find an 'Acceptance criteria:' section inside a prompt (the prompt itself is kept whole)."""
    match = _ACCEPTANCE_RE.search(prompt)
    if not match:
        return None
    text = ((match.group(1) or "") + prompt[match.end():]).strip()
    return text or None


def parse_id_list(values):
    """Accept ['3', '4,5', '#6'] and return [3, 4, 5, 6]."""
    ids = []
    for value in values or []:
        for part in str(value).replace(";", ",").split(","):
            part = part.strip().lstrip("#")
            if not part:
                continue
            if not part.isdigit():
                raise QueueError("'%s' is not a task id" % part)
            if int(part) not in ids:
                ids.append(int(part))
    return ids


class Queue(object):
    """The task queue of one project."""

    def __init__(self, root, timeout=60):
        """`timeout` (seconds) is how long a write waits for another process's transaction;
        the capture hook uses a short one so that it always answers in time."""
        self.root = root
        self.start = root  # directory the command was started from (set by the CLI)
        self.key = util.project_key(root)
        self.dir = util.project_dir(root)
        self.db_path = os.path.join(self.dir, "queue.db")
        os.makedirs(self.dir, exist_ok=True)
        self._depth = 0
        self.conn = sqlite3.connect(self.db_path, timeout=timeout, isolation_level=None)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA busy_timeout = %d" % int(timeout * 1000))
        self.conn.execute("PRAGMA foreign_keys = ON")
        self._init_schema()
        info_path = os.path.join(self.dir, "project.json")
        if not os.path.exists(info_path):
            util.save_json_atomic(info_path, {"root": root, "key": self.key,
                                              "created_at": util.now_iso()})

    def _init_schema(self):
        row = None
        try:
            row = self.conn.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()
        except sqlite3.OperationalError:
            pass
        if row is not None:
            if row["value"] != SCHEMA_VERSION:
                raise QueueError("queue database %s has schema version %s; this version of "
                                 "moa7amed-queue expects %s. Update the plugin."
                                 % (self.db_path, row["value"], SCHEMA_VERSION))
            routing.ensure_schema(self.conn)
            self._ensure_additive()
            return
        self.conn.executescript(SCHEMA)
        routing.ensure_schema(self.conn)
        self._ensure_additive()
        with self.tx():
            self.conn.execute("INSERT OR IGNORE INTO meta(key, value) VALUES('schema_version', ?)",
                              (SCHEMA_VERSION,))
            self.conn.execute("INSERT OR IGNORE INTO meta(key, value) VALUES('project_root', ?)",
                              (self.root,))

    def _ensure_additive(self):
        from . import autoqueue, challenge, workflow
        workflow.ensure_schema(self.conn)
        challenge.ensure_schema(self.conn)
        autoqueue.ensure_schema(self.conn)
        self._ensure_process_tokens()

    def _ensure_process_tokens(self):
        found = self.conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND "
                                  "name='process_tokens'").fetchone()
        if found is None:
            self.conn.executescript(ADDITIVE_SCHEMA)

    def close(self):
        self.conn.close()

    @contextlib.contextmanager
    def tx(self):
        """A write transaction. Takes the database write lock up front, so two
        processes can never interleave a read-modify-write of the queue."""
        if self._depth:
            self._depth += 1
            try:
                yield
            finally:
                self._depth -= 1
            return
        self.conn.execute("BEGIN IMMEDIATE")
        self._depth = 1
        try:
            yield
        except BaseException:
            self._depth = 0
            self.conn.execute("ROLLBACK")
            raise
        else:
            self._depth = 0
            self.conn.execute("COMMIT")

    # ----------------------------------------------------------------- events

    def log_event(self, task_id, run_id, kind, detail=None):
        self.conn.execute("INSERT INTO events(ts, task_id, run_id, type, detail) VALUES(?,?,?,?,?)",
                          (util.now_iso(), task_id, run_id, kind, detail))

    def events(self, task_id=None, limit=50):
        if task_id is None:
            rows = self.conn.execute("SELECT * FROM events ORDER BY id DESC LIMIT ?", (limit,))
        else:
            rows = self.conn.execute(
                "SELECT * FROM events WHERE task_id=? ORDER BY id DESC LIMIT ?", (task_id, limit))
        return list(reversed(rows.fetchall()))

    # ----------------------------------------------------------------- reads

    def get(self, task_id):
        row = self.conn.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
        if row is None:
            raise QueueError("task #%s does not exist in this project's queue" % task_id)
        return row

    def tasks(self, statuses=None):
        if statuses:
            marks = ",".join("?" for _ in statuses)
            sql = "SELECT * FROM tasks WHERE status IN (%s) ORDER BY id" % marks
            return self.conn.execute(sql, tuple(statuses)).fetchall()
        return self.conn.execute("SELECT * FROM tasks ORDER BY id").fetchall()

    def counts(self):
        counts = {name: 0 for name in ("pending", "running", "done", "failed", "cancelled")}
        for row in self.conn.execute("SELECT status, COUNT(*) AS n FROM tasks GROUP BY status"):
            counts[row["status"]] = row["n"]
        return counts

    def deps(self, task_id):
        """Prerequisites of a task, as task rows."""
        return self.conn.execute(
            "SELECT t.* FROM task_deps d JOIN tasks t ON t.id = d.depends_on "
            "WHERE d.task_id=? ORDER BY t.id", (task_id,)).fetchall()

    def dependents(self, task_id):
        return self.conn.execute(
            "SELECT t.* FROM task_deps d JOIN tasks t ON t.id = d.task_id "
            "WHERE d.depends_on=? ORDER BY t.id", (task_id,)).fetchall()

    def runs(self, task_id=None):
        if task_id is None:
            return self.conn.execute("SELECT * FROM runs ORDER BY id").fetchall()
        return self.conn.execute("SELECT * FROM runs WHERE task_id=? ORDER BY id",
                                 (task_id,)).fetchall()

    def state_of(self, task):
        """Display state: a pending task may really be waiting, blocked or in retry backoff."""
        if task["status"] != "pending":
            return task["status"]
        prerequisites = self.deps(task["id"])
        if any(dep["status"] in ("failed", "cancelled") for dep in prerequisites):
            return "blocked"
        if self.model_block(task["id"]):
            return "blocked"
        if any(dep["status"] != "done" for dep in prerequisites):
            return "waiting"
        if task["not_before"] and task["not_before"] > util.now_iso():
            return "backoff"
        return "pending"

    def model_block(self, task_id):
        """Why no model can run this task, or None."""
        row = self.conn.execute("SELECT blocked_reason FROM task_routing WHERE task_id=?",
                                (task_id,)).fetchone()
        return row["blocked_reason"] if row else None

    def execution_order(self):
        """Pending tasks in the order the runner would start them right now.

        Priority first, then age. A task that is between two phases of a phased
        run goes first, so no other task can slip in between its phases; a task
        with no usable model is left out until that is resolved."""
        return self.conn.execute(
            "SELECT t.* FROM tasks t LEFT JOIN task_routing r ON r.task_id = t.id "
            "WHERE t.status='pending' "
            "AND (t.not_before IS NULL OR t.not_before <= ?) "
            "AND r.blocked_reason IS NULL "
            "AND NOT EXISTS (SELECT 1 FROM task_deps d JOIN tasks p ON p.id = d.depends_on "
            "                WHERE d.task_id = t.id AND p.status != 'done') "
            "ORDER BY (r.phase IS NOT NULL AND r.phase != 'plan') DESC, t.priority DESC, t.id ASC",
            (util.now_iso(),)).fetchall()

    def next_eligible(self):
        order = self.execution_order()
        return order[0] if order else None

    def next_retry_time(self):
        """Earliest moment a task in retry backoff becomes runnable, or None."""
        row = self.conn.execute(
            "SELECT MIN(t.not_before) AS at FROM tasks t WHERE t.status='pending' "
            "AND t.not_before IS NOT NULL AND t.not_before > ? "
            "AND NOT EXISTS (SELECT 1 FROM task_deps d JOIN tasks p ON p.id = d.depends_on "
            "                WHERE d.task_id = t.id AND p.status != 'done')",
            (util.now_iso(),)).fetchone()
        return row["at"] if row else None

    # ----------------------------------------------------------------- writes

    def add(self, prompt, acceptance=None, title=None, priority=0, after=None,
            max_attempts=None, source="cli", session_id=None, workdir="", model=None,
            phased=None, request_id=None):
        """Save a task and return its id. With a `request_id`, a second submission carrying
        the same id returns the task saved the first time (self.last_add_was_repeat is then
        True); without one, every call saves a new task, also for identical text."""
        self.last_add_was_repeat = False
        prompt = (prompt or "").replace("\r\n", "\n").strip("\n")
        if not prompt.strip():
            raise QueueError("cannot queue an empty prompt")
        acceptance = (acceptance or "").replace("\r\n", "\n").strip() or extract_acceptance(prompt)
        title = re.sub(r"\s+", " ", title).strip() if title else derive_title(prompt)
        if max_attempts is None:
            max_attempts = util.effective_config(self.dir)["max_attempts"]
        if int(max_attempts) < 1:
            raise QueueError("max attempts must be at least 1")
        stamp = util.now_iso()
        with self.tx():
            if request_id:
                from . import autoqueue
                earlier = autoqueue.known_request(self, request_id)
                if earlier is not None:
                    self.last_add_was_repeat = True
                    return earlier
            for dep in after or []:
                self.get(dep)
            cursor = self.conn.execute(
                "INSERT INTO tasks(title, prompt, acceptance, workdir, priority, max_attempts, "
                "source, session_id, created_at, updated_at) VALUES(?,?,?,?,?,?,?,?,?,?)",
                (title, prompt, acceptance, workdir or "", int(priority), int(max_attempts),
                 source, session_id, stamp, stamp))
            task_id = cursor.lastrowid
            if request_id:
                from . import autoqueue
                autoqueue.remember_request(self, request_id, task_id, source)
            for dep in after or []:
                self.conn.execute("INSERT OR IGNORE INTO task_deps(task_id, depends_on) VALUES(?,?)",
                                  (task_id, dep))
            detail = "priority=%s source=%s" % (priority_label(int(priority)), source)
            if after:
                detail += " after=%s" % ",".join("#%d" % dep for dep in after)
            self.log_event(task_id, None, "added", detail)
            if model:
                # An explicit model is part of the request: a bad one must fail the add.
                routing.on_task_added(self, task_id, model=model, phased=phased)
            else:
                try:
                    routing.on_task_added(self, task_id, phased=phased)
                except Exception as exc:  # routing must never cost the user a queued prompt
                    self.log_event(task_id, None, "routing-error", str(exc)[:300])
        return task_id

    def set_priority(self, task_ids, priority):
        with self.tx():
            for task_id in task_ids:
                task = self.get(task_id)
                if task["status"] in ("done", "cancelled"):
                    raise QueueError("task #%d is %s; its priority no longer matters"
                                     % (task_id, task["status"]))
                self.conn.execute("UPDATE tasks SET priority=?, updated_at=? WHERE id=?",
                                  (int(priority), util.now_iso(), task_id))
                self.log_event(task_id, None, "priority",
                               "%s -> %s" % (priority_label(task["priority"]),
                                             priority_label(int(priority))))

    def _would_cycle(self, task_id, depends_on):
        """True if `depends_on` already (transitively) depends on `task_id`."""
        seen, stack = set(), [depends_on]
        while stack:
            current = stack.pop()
            if current == task_id:
                return True
            if current in seen:
                continue
            seen.add(current)
            stack.extend(row["depends_on"] for row in self.conn.execute(
                "SELECT depends_on FROM task_deps WHERE task_id=?", (current,)))
        return False

    def add_deps(self, task_id, dep_ids):
        with self.tx():
            task = self.get(task_id)
            if task["status"] in ("running", "done"):
                raise QueueError("task #%d is %s; its prerequisites can no longer change"
                                 % (task_id, task["status"]))
            for dep in dep_ids:
                self.get(dep)
                if dep == task_id:
                    raise QueueError("task #%d cannot depend on itself" % task_id)
                if self._would_cycle(task_id, dep):
                    raise QueueError("task #%d cannot depend on #%d: that would create a "
                                     "dependency cycle" % (task_id, dep))
                self.conn.execute("INSERT OR IGNORE INTO task_deps(task_id, depends_on) VALUES(?,?)",
                                  (task_id, dep))
            self.log_event(task_id, None, "depends",
                           "added %s" % ",".join("#%d" % dep for dep in dep_ids))

    def remove_deps(self, task_id, dep_ids):
        with self.tx():
            self.get(task_id)
            for dep in dep_ids:
                self.conn.execute("DELETE FROM task_deps WHERE task_id=? AND depends_on=?",
                                  (task_id, dep))
            self.log_event(task_id, None, "depends",
                           "removed %s" % ",".join("#%d" % dep for dep in dep_ids))

    def cancel(self, task_ids):
        with self.tx():
            for task_id in task_ids:
                task = self.get(task_id)
                if task["status"] == "running":
                    raise QueueError("task #%d is running; stop the runner before cancelling it"
                                     % task_id)
                if task["status"] == "done":
                    raise QueueError("task #%d is already done" % task_id)
                if task["status"] == "cancelled":
                    continue
                self.conn.execute("UPDATE tasks SET status='cancelled', not_before=NULL, "
                                  "updated_at=? WHERE id=?", (util.now_iso(), task_id))
                self.log_event(task_id, None, "cancelled", None)

    def retry(self, task_ids, max_attempts=None):
        """Put failed or cancelled tasks back in line with a fresh set of attempts."""
        retried = []
        with self.tx():
            for task_id in task_ids:
                task = self.get(task_id)
                if str(self.model_block(task_id) or "").startswith("agents:"):
                    raise QueueError("task #%d is held by its agents: %s. After checking, release it "
                                     "with `moa7amed-queue agents release %d`"
                                     % (task_id, self.model_block(task_id)[8:], task_id))
                if str(self.model_block(task_id) or "").startswith("decision:"):
                    raise QueueError("task #%d waits for your decision on a concern; see "
                                     "`moa7amed-queue challenge show %d`, then decide with "
                                     "`moa7amed-queue challenge decide %d --recommendation` or "
                                     "`--original` (or cancel it)" % (task_id, task_id, task_id))
                if str(self.model_block(task_id) or "").startswith("approval:"):
                    raise QueueError("task #%d waits for approval of its plan; approve it with "
                                     "`moa7amed-queue workflow approve %d` or cancel it"
                                     % (task_id, task_id))
                if task["status"] == "pending" and self.model_block(task_id):
                    # Blocked for lack of a usable model: give it another chance.
                    self.conn.execute("UPDATE task_routing SET blocked_reason=NULL "
                                      "WHERE task_id=?", (task_id,))
                    self.log_event(task_id, None, "retry", "model block cleared")
                    retried.append(task_id)
                    continue
                if task["status"] not in ("failed", "cancelled"):
                    raise QueueError("task #%d is %s; only failed or cancelled tasks can be retried"
                                     % (task_id, task["status"]))
                self.conn.execute(
                    "UPDATE tasks SET status='pending', attempts=0, not_before=NULL, "
                    "max_attempts=COALESCE(?, max_attempts), finished_at=NULL, updated_at=? "
                    "WHERE id=?", (max_attempts, util.now_iso(), task_id))
                self.log_event(task_id, None, "retry", "was %s" % task["status"])
                retried.append(task_id)
        return retried

    # ----------------------------------------------------------------- export

    def task_dict(self, task, with_runs=False):
        data = dict(task)
        data["state"] = self.state_of(task)
        data["priority_label"] = priority_label(task["priority"])
        data["depends_on"] = [dep["id"] for dep in self.deps(task["id"])]
        data["routing"] = routing.describe(self, task)
        if with_runs:
            runs = []
            for run in self.runs(task["id"]):
                item = dict(run)
                item["files_changed"] = json.loads(run["files_changed"] or "[]")
                used = self.conn.execute("SELECT * FROM run_routing WHERE run_id=?",
                                         (run["id"],)).fetchone()
                item["requested_model"] = used["model_arg"] if used else None
                item["actual_model"] = used["actual_model"] if used else None
                item["phase"] = used["phase"] if used else None
                runs.append(item)
            data["runs"] = runs
        return data


def all_projects():
    """Every project that has a queue under MOA7AMED_QUEUE_HOME."""
    base = os.path.join(util.home(), "projects")
    found = []
    if not os.path.isdir(base):
        return found
    for name in sorted(os.listdir(base)):
        info = util.load_json(os.path.join(base, name, "project.json"), None)
        if info and os.path.exists(os.path.join(base, name, "queue.db")):
            found.append(info)
    return found
