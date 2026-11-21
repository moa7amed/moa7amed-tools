# Architecture

This document describes how the repository is organised and how `moa7amed-queue` works
inside. It is for contributors. Users need only the plugin's
[README](../plugins/moa7amed-queue/README.md).

## The repository is a marketplace

```text
.claude-plugin/marketplace.json   lists the plugins; carries no versions
plugins/
  moa7amed-queue/                 a plugin: everything a user receives is in this folder
.github/workflows/                CI and the release record (not shipped)
scripts/                          release tooling for maintainers (not shipped)
release/baselines.json            earlier versions the upgrade tests start from (not shipped)
docs/                             contributor documentation (not shipped)
tests/                            checks for the marketplace as a whole (not shipped)
```

Claude Code installs a plugin by copying its folder into a versioned cache directory.
Whatever is outside `plugins/<name>/` never reaches a user, and a plugin can rely on
nothing outside its own folder. That one fact drives most rules in this repository:

| Rule | Reason |
| :-- | :-- |
| No shared library folder; plugins never import from each other | A plugin must work when it is the only one installed, and must be releasable without touching another |
| Each plugin has its own version, changelog, tests and license | They are released at different times |
| The version is written in `plugin.json` only | Claude Code reads it there first; a second copy can only disagree |
| Standard library only (Python 3.8+), no build step | Nothing to install after the plugin itself; the installed copy is the source |
| Every user-visible name derives from the plugin name | Commands, data directories and environment variables of two plugins cannot collide |

## moa7amed-queue

### What runs where

```text
 Claude Code session                         a terminal, or a detached process
 -------------------                         ---------------------------------
 skills/*/SKILL.md      ----.                runner/run-queue.ps1   (Windows PowerShell)
 hooks/capture-prompt.sh    |                scripts/mqlib/pyrunner.py (every platform)
            |               v                          |
            |        bin/moa7amed-queue  <-------------'   the runners use the same command
            |               |
            '------> scripts/mq.py -> scripts/mqlib/
                                        cli.py          commands, text and JSON output
                                        store.py        SQLite schema, tasks, ordering
                                        engine.py       lock, claim, finish, recovery
                                        routing.py      model selection per task and run
                                        workflow.py     quick/standard/deep process per task
                                        challenge*.py   concerns, owner decisions and their
                                                        provenance, decision holds, CLI
                                        agents*.py      multi-agent execution: evaluation,
                                                        workers, integration, CLI
                                        replica_*.py    application reconstruction: model,
                                                        store, image comparison, design
                                                        tokens, CLI, report
                                        bid_*.py        tenders and proposals: model, pricing,
                                                        store, documents, CLI
                                        ooxml.py        DOCX/XLSX writer and reader
                                        (scripts/replica_browser.py: page capture and PDF
                                         printing, its own program, started only by
                                         `replica capture` and `bid generate`)
                                        router.py       rule-based classifier (English)
                                        router_ar.py    Arabic vocabulary for the classifier
                                        models.py       model availability, CLI capabilities
                                        gitops.py       clean-tree checks, commits, stashes
                                        maintenance.py  update report, doctor, backup, usage
                                        plan_model.py   plan rules: evidence, matrix, tasks
                                        plan_catalog.py technologies and compatibility rules
                                        plan_store.py   plans, revisions, approvals, import
                                        plan_discover.py, plan_report.py
                                        hook.py         prompt capture
                                        util.py         paths, settings, process identity
                                              |
                                              v
                             ~/.moa7amed-queue/projects/<key>/queue.db
```

The session side only reads and writes the queue. Tasks are executed by a runner, a
separate process that starts one `claude -p` session per task. Because the runner is
outside the session, a queued prompt cannot interrupt the work in progress, and the
queue survives the session, the terminal and a reboot.

### Data

```text
~/.moa7amed-queue/                 MOA7AMED_QUEUE_HOME overrides the location
  config.json                      global settings
  models.json                      what is known about model availability and the CLI
  state.json                       which version last ran here (used to notice an update)
  backups/<stamp>-v<version>...    backups, manual and automatic
  projects/<key>/
    project.json                   the project directory this queue belongs to
    queue.db                       the queue (SQLite)
    config.json                    project settings
    runner.log                     what the runners did
    runs/task-0001/run-000001.*    prompt, output and error stream of every run
```

One database per project. A project is the git repository root of the working directory,
or the directory itself when it is not in a repository.

The database has a format number (`meta.schema_version`, currently `1`). The rules for
changing it are in [versioning.md](versioning.md); in short, every release so far reads
and writes format 1, and new tables are added next to the existing ones rather than
altering them, so an older version keeps working on a queue a newer one has used.

Three properties are enforced by the database itself, not by convention:

- The original prompt, acceptance criteria, working directory, creation time and origin
  of a task cannot be updated (a trigger aborts the statement).
- Tasks cannot be deleted. Cancelling is a state.
- All multi-step changes happen inside `BEGIN IMMEDIATE` transactions, so two processes
  cannot interleave them.

### Task states

```text
              add                     claim                    finish: success
  (prompt) ---------> pending -----------------> running -----------------------> done
                        ^  ^                       |
          retry / requeue  |                       | finish: failure, attempts left
                        |  '-----------------------'   (after a pause, if configured)
                        |                          |
                        |                          | finish: failure, no attempts left
                        |                          v
                        '----- retry ----------- failed
     pending/failed --cancel--> cancelled --retry--> pending
```

A task leaves `running` only through `finish` (its runner reports the result) or through
recovery (another runner finds it abandoned). There is no other path, which is what
makes "every task runs at most once at a time, and a finished task is not run again"
checkable. A runner that lets go of the project (`release`) puts an unfinished task back
in line, but leaves a task whose result is complete on disk for the next runner to
record: whether Claude finished is decided by the result file, never by how the runner
came to stop.

### The runner protocol

Both runners are thin loops around five commands. Anything that decides something is in
`engine.py`, so the PowerShell and the Python runner cannot drift apart in behaviour.

| Step | Command | What the engine does |
| :-- | :-- | :-- |
| Start | `runner-acquire` | Takes the project's single lock, or refuses if a live runner holds it. Recovers what a dead runner left behind. |
| Next task | `claim` | Picks the next runnable task, chooses its model, checks the workspace, marks it running and returns the exact `claude` command line. Or says why nothing can start. |
| While it runs | `heartbeat` | Proves the runner is alive; records the task's process. |
| Result | `finish` | Reads Claude's output, judges it, commits or sets aside changes, schedules a retry or escalation. |
| Stop | `runner-release` | Gives the lock back. A task still open is put back in line. |

Exit codes of a runner: `0` everything done, `1` a task failed or is blocked, `2` another
runner holds the project, `3` stopped to protect uncommitted changes or because no
permitted model is available, `4` the lock was lost, `5` the cost limit of the session
was reached, `130` interrupted.

### Locking and ownership

One row in `runner_lock` is the lock. It records the runner's process id, host and a
heartbeat time. A lock is considered alive when

- it is on this host and the recorded process is still the same process: the id exists
  **and** its start time matches the one recorded when the lock was taken (process ids
  are reused; a start time is not), or
- it is on another host and its heartbeat is recent (`stale_lock_seconds`).

The same identity check applies to the Claude process of a running task, so recovery
waits for a task that is genuinely still running and does not wait for an unrelated
process that inherited its id.

### Recovery and reconciliation

When a runner starts and finds a dead runner's lock, each task that runner left
`running` is examined:

| What is found | What happens |
| :-- | :-- |
| The task's Claude process is still alive | Recovery refuses; the task is still running (`recover --force` overrides) |
| Claude's result is complete on disk | **Reconciled**: recorded as done or failed from that result. The task is not run again. Files it changed are left exactly as they are and are listed; nothing is committed or stashed on its behalf, because time has passed and the changes may no longer be only the task's. Until they are committed, no task starts in that workspace under any `on_dirty` policy (the list is kept in the queue's `meta` table). A commit found since the run started is attributed to the run only if it carries the task's `Task-Queue-Id` or predates Claude's result |
| No complete result | With `on_interrupted=hold`: marked failed as "held for review", for tasks that may have had effects outside the repository; `retry` runs it again. Otherwise (the default): recorded as interrupted and put back in line if it has attempts left. The crashed run counts as an attempt, so a task that keeps taking its runner down cannot loop forever; stopping a runner deliberately does not count. Files the run left half-changed are never built upon or discarded: by default the next runner stops and lists them, with `on_dirty=stash` it sets them aside in a git stash first |

`moa7amed-queue reconcile` reads all of this back: the blocking reason, the held files with
their likely owner, unrelated changes and held tasks, and resolves a hold only on an
explicit `--commit`, `--stash` or `--release` with `--yes`, while holding the runner lock.

### Project planning

`project` commands keep plans in additive tables of the same database: `plans` (drafts),
`plan_revisions` and `plan_approvals` (immutable: triggers refuse updates and deletes),
`plan_links` (plan task to queue task: the single record of ownership), `plan_findings`
and `plan_log`. An import adds a queue task and its link in one transaction; re-importing
is idempotent. Plan tasks are ordinary queue tasks (source `plan:<id>`), so their state is
always the queue's. `finish` runs the compliance check for such tasks after the commit; a
failure of the check itself is logged and never loses the task's result. Details:
[planning.md](planning.md).

### Adaptive workflow

`workflow.assign` runs inside the transaction that classifies a task and stores its
profile and capability levels in the additive table `task_workflow`; tasks classified
before 0.5.0 get one when they are next claimed. The profile decides the workflow
section of the prompt (`workflow.prompt_block`), whether the task runs in phases, and
whether a planning phase ends in an approval wait (a `blocked_reason` starting with
`approval:`, which `retry` and `models --reset` leave alone). `finish` refuses a `DONE`
without an `MQ_VERIFIED` line before the git handling, so unverified work is set aside
like any failed run, and records verification and a bounded escalation in the same
transaction as the result. Details: [workflow.md](workflow.md).

### Replica

A replica project is a row in `replica_projects` (the request verbatim, mode, quality,
ownership, the inventory document) linked to a project plan of the same id. References
(`replica_refs`), comparisons, test results, improvement rounds and a log are additive
tables of the same database; files live in `projects/<key>/replica/<id>/`. `replica plan`
writes grouped tasks with only the inventory entries they need into the plan, so
architecture, approval, import, routing, workflows and compliance are the planning
engine's and the queue's. Correction rounds add ordinary queue tasks with source
`replica:<id>`. The engine gives tasks of a replica project `--add-dir` for its folder.
Details: [replica.md](replica.md).

### BidStudio

A bid project is a row in `bid_projects` (the request, mode, company profile and the bid
document: tender facts, requirements, compliance, clarifications, assessment, work
breakdown, solution, proposal texts, pricing inputs) linked to a project plan of the same
id for the solution. Documents (`bid_documents`) are copied into
`projects/<key>/bids/<id>/documents` with their extracted text; every change of the bid
document is an immutable row of `bid_revisions` (a trigger refuses updates); approvals
store the hash of what was approved; generated files are recorded in `bid_outputs` with
the problems their checks found. Pricing (`bid_pricing.py`) is pure decimal arithmetic;
documents (`bid_docs.py`) are built as blocks and written by `ooxml.py` (DOCX, XLSX) and
printed to PDF by the browser program. `bid queue` adds the stages as ordinary tasks
(source `bid:<id>`) whose prompts carry their stage's instructions; the engine gives
those tasks `--add-dir` for the bid folder and `--allowedTools` for the `bid` and
`project` commands only. Company profiles and rate cards are files in
`<data>/companies/<id>/`. Details: [bidstudio.md](bidstudio.md).

### Multi-agent execution

`claim` evaluates the task (`agents.evaluate`: analysis roles from the router's
dimensions, a built-in BidStudio/Replica decomposition or an explicit read-only plan;
safety, expected benefit, mechanism, budget, rounds). For a parallel task it runs the
read-only agents before creating the coordinator's run: separate `claude -p` processes
started by the claiming process (`--tools Read,Grep,Glob`, `dontAsk`, a detached
worktree of `HEAD` per agent), which heartbeats the runner lock while they run and
integrates each result as it arrives (`agents_apply.py`: revision-checked,
fingerprint-checked shared-state updates in short transactions; reports kept for the
coordinator). The live workspace's `HEAD` and status are compared before and after.
Subtasks, attempts, pids, timestamps, isolation, revisions, results and outcomes are in
`agent_subtasks`; the plan and its measured summary in `agent_plans`; events in
`agent_events` (all additive). The coordinator's prompt gets an `<agents>` section; its
run is an ordinary run and the only writer. Agent spend counts in the session's cost
limit and in `usage`. Details: [agents.md](agents.md).

### Model routing

`claim` asks `routing.select_for_run` which model the task gets. The decision is made
once per run and stays with it. The classifier (`router.py`) reads the request clause by
clause, scores eleven dimensions and applies guard rules (the smallest model needs
positive evidence that the task is simple). Borderline decisions can be confirmed by one
short model call (`routing_classifier`). `router_ar.py` adds Arabic vocabulary to the
same rules; how well that works is measured, not assumed, see
[Arabic and mixed-language requests](../plugins/moa7amed-queue/README.md#arabic-and-mixed-language-requests).

### Git safety

Before a task: the workspace must be clean, or the configured `on_dirty` action applies
(`stop` by default: nothing is touched). After a task: its changes are committed with a
`Task-Queue-Id` trailer; changes of a failed run are stashed. Nothing in the plugin runs
`git reset`, `git checkout --`, `git clean` or `git stash drop`.

### What a session pays for the plugin

| Component | Cost |
| :-- | :-- |
| Skill names and descriptions | In every session. About 290 tokens by Claude Code's own estimate (`claude plugin details`), down from about 545 in 0.3.0 |
| Skill bodies | Only when a skill is invoked |
| The capture hook | No tokens. A shell script that exits without starting Python unless the prompt begins with the capture prefix |
| MCP servers, agents | None are shipped |

`tests/test_plugin.py` holds a budget for the first row and `release.json` one for the
installed plugin (`always_on_token_budget`), so growth is noticed in review.

## Release tooling

`scripts/release_check.py` is the single entry point; CI and the release workflow call
it and add nothing of their own. See [release-checklist.md](release-checklist.md).
