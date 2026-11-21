# Changelog

Versions 0.1.0 and 0.2.0 were published as **claude-task-queue** with the command `ctq`;
their entries below describe them under those names.

## 0.8.0

Multi-agent foundation (stage A): one coordinator, concurrent read-only analysis agents,
one writer. Queues, settings and data of 0.7.0 keep working (new, additive tables); most
tasks stay single-agent; going back to 0.7.0 is possible.

- Every task is evaluated when it starts, by rules and without a model call. Agents are
  used only for justified analysis roles taken from the router's dimensions
  (`security_review`, `architecture_review`, `test_gap_analysis`,
  `repository_analysis`, `requirements_analysis`, `documentation_review`; none for
  QUICK, at most two for STANDARD), for the built-in BidStudio intake and drafting and
  Replica screen analysis decompositions, or for an explicit read-only plan
  (`agents plan`), when safe, worth it, possible and affordable; analysis rounds are
  bounded.
- Agents are separate `claude -p` sessions started concurrently by the queue during the
  task's claim (both runners). Read-only is enforced by Claude Code's tool and
  permission controls (`--tools Read,Grep,Glob`, editing/shell/web/subagent tools
  denied, `--permission-mode dontAsk`, the task's permissions not passed on) and a
  detached snapshot of `HEAD` per agent; the live workspace is checked before and after
  and a change holds the task until `agents release`. Agents cannot run queue runners.
- The coordinator (the task's own session) is the only writer: it gets the agents'
  findings, implements, verifies, and completes the task. Parallel implementation writes
  are not implemented (future stage); a plan with writing subtasks is refused.
- Results carry their input revision: stale and duplicate results are rejected and
  integration never accepts a result twice. BidStudio sections and requirements and
  Replica screens are applied by the queue with fingerprint checks; approvals, pricing,
  document generation and submission stay with you and the engine.
- Recovery: bounded attempts (`agent_max_attempts`) and time (`agent_timeout_seconds`);
  cancellation stops only the task's own agent processes and keeps diagnostics; after a
  crash finished output is collected, interrupted agents rerun, finished agents never
  rerun.
- Budget: agents share the task's budget with a coordinator reserve; agent spend counts
  in the cost limit and in `usage`; unreported cost is shown as unknown.
- Visibility: `agents show`, `agents evaluate`, an `Agents:` line in `show`.
- New settings: `agent_mode`, `agent_max_parallel`, `agent_write_policy`,
  `agent_timeout_seconds`, `agent_max_attempts`, `agent_max_analysis_rounds`,
  `agent_parallel_min_benefit`, `agent_budget_policy`, `agent_reserve_share`. New
  commands: `agents`, `replica agents`.
- Measured with real Claude Code (Haiku, small security fix): two agents overlapped and
  cut the analysis phase from 123.6 s to 103.6 s; agents cost $0.015-0.019 and added about
  1,400 tokens to the coordinator's prompt; a run without agents was faster and cheaper on
  a fix this small. A runner killed after the agents finished resumed without rerunning them.
- Fixed: choosing a DEEP task's model by hand (`model`, `add --model`) dropped its
  planning session and therefore the owner's approval of a material plan. The chosen
  model now runs every phase, and the plan still waits for `workflow approve`. Found
  while recording the demo.
- Fixed (also found by the demo): `route`, `show` and `workflow` said "opus plans" for a
  task whose model was chosen by hand; `status` labelled an approval or agents hold as
  "no usable model"; the implement and review phases of a deep task overwrote its
  agents' measured record; `workflow` with no open tasks said workflow_mode was off.
- Documentation: a demo page (docs/demo.md) with screenshots of a real session,
  diagrams, and silent videos with English and Arabic captions (docs/media).
- Challenge, discuss, then execute (`grillme`): a task with a concrete, evidence-backed
  risk can wait for the owner's decision. `challenge_mode` `explicit` (default: only
  reviews on request), `adaptive` (rules check each task before it runs, without a model
  call, and a task's session may stop once, before changing anything, with
  `MQ_CONCERN`) or `off`; `challenge_max_rounds` (default 1). Concerns are
  recommendations or real restrictions (accepting a risk cannot lift a restriction);
  minor ones are advisories that do not hold the task. Decisions (take the
  recommendation, or proceed with the original approach and accept the trade-off) are
  stored per task and revision in the queue database, handed to the task's session,
  and not asked again unless new evidence appears. Only the owner decides: from their
  terminal, or in Claude Code, where the capture hook recognises a clear decision in
  their own words; queued tasks and agents are refused. A held task resumes once,
  without a used attempt; other tasks keep running. Permissions, plan approval,
  verification and cost limits are unchanged by any decision. New: `challenge` command
  group, `/moa7amed-queue:grillme` (user-invoked), docs/challenge.md. Ten skill
  descriptions were shortened so what every session carries went down (about 1155
  tokens by Claude Code's estimate).
- Release check: a `dev-material` check fails the release when a tracked file (and so
  the archive) holds or refers to the material used while developing the plugin
  (specifications, prompts, transcripts, hand-off notes). Example requests in the guides
  and tests were rewritten.
- Guide: docs/agents.md. Live check: tests/agents_live.py.

## 0.7.0

BidStudio: tender analysis, technical and commercial proposals. Queues, plans, replica
projects, settings and data of 0.6.0 keep working (BidStudio lives in new, additive tables)
and going back to 0.6.0 is possible.

- Ten new skills: `bidstudio` (the entry point; Claude may start it for tender work),
  `bid-analyze`, `bid-compliance`, `bid-clarifications`, `bid-technical`,
  `bid-estimation`, `bid-pricing`, `bid-commercial`, `bid-review` (you start them) and
  the read-only `bid-status`.
- New command group `moa7amed-queue bid ...` (intent, tools, new, list, show, config, doc,
  text, pages, read, apply, check, decision, estimate, labour, price, approve, generate,
  review, checklist, queue, revisions, status, company).
- Modes analyze, technical, commercial, complete, review; tender requests are recognised
  in English and Arabic; a queued tender request becomes a bid project
  (`bid_hook_intake`).
- Document intake with provenance (id, SHA-256, type, version, date, source, amends or
  supersedes): PDF (pypdf, PyMuPDF or pdftotext), DOCX and XLSX (built-in readers), text;
  pages without text are flagged, rendered to images and recorded when read visually.
- Requirements in the tender's own words with their source; the engine checks every
  quote against the document. Classes EXPLICIT_REQUIREMENT, VERIFIED_SUPPORTING_EVIDENCE,
  PROPOSED_SOLUTION, ASSUMPTION, UNKNOWN, CONFLICTING. Addenda supersede requirements,
  never overwrite them; every change is an immutable revision.
- Compliance matrix (COMPLIANT ... NOT_APPLICABLE) with checks: no restated answers,
  evidence required, company claims only from approved profile items, disqualifying
  conditions listed apart. Clarification register. Bid/no-bid recommendation from rated
  factors and the eligibility results.
- Three-point effort estimate (PERT) by role with optimistic/baseline/conservative totals
  and a dependency schedule.
- Deterministic pricing engine: cost, markup and gross margin (distinct), contingency
  built into prices, discount, tax, FX with assumption flags, sources per line
  (quotation, rate card, planning estimate, pending), BOQ that reconciles to the cent,
  sensitivity scenarios.
- Documents: technical and commercial proposals and an executive summary (DOCX; PDF
  printed by the browser when Playwright is installed), internal pricing model, BOQ,
  compliance matrix and clarification register (XLSX with formulas), English or Arabic
  (right to left). Every file is read back and checked: required sections, no price in
  the technical envelope, no internal figure in customer documents.
- A customer compliance matrix (technical envelope) and an internal one with evidence,
  owner, risk and clarifications; `bid outputs` lists the generated files and prints
  their text.
- Independent review (verified findings and advisory ones kept apart), submission
  checklist, approval gates (architecture, compliance commitments, licensing, pricing,
  contract exceptions, release) voided by later changes. Never submits.
- Company profiles and rate cards in the data directory, outside projects and the plugin.
- `bid queue` runs the stages as ordinary queue tasks with dependencies; their prompts
  carry the stage instructions; the tasks may run only the `bid` and `project` commands
  and use the bid folder. Stage model tiers: opus for the architecture, sonnet otherwise
  (`bid_stage_models auto` lets the router decide).
- New settings: `bid_hook_intake`, `bid_stage_models`.
- What every session carries grew: Claude Code's estimate rose from about 864 to about 1189
  tokens (it counts all 37 skills; the two new ones Claude may start, `bidstudio` and
  `bid-status`, add about 190 characters of description). The plugin's own budget is now 1200.
- Guide: docs/bidstudio.md. Live check: tests/bid_live.py.

## 0.6.0

Replica: rebuild an application from evidence. Queues, plans, settings and data of 0.5.0
keep working (Replica lives in new, additive tables) and going back to 0.5.0 is possible.

- Ten new skills: `replica` (the entry point; Claude may start it when you ask to rebuild
  an application), `replica-recon`, `replica-flows`, `replica-design`, `replica-backend`,
  `replica-plan`, `replica-build`, `replica-compare`, `replica-test` (you start them) and
  the read-only `replica-status`.
- New command group `moa7amed-queue replica ...`: intent, tools, new, list, show, config,
  ref, capture, analyze, tokens, plan, compare, test, improve, sweep, status, report.
- Requests to rebuild an application are recognised in English, Arabic and Egyptian
  Arabic (REPLICA_DISCOVER, _VISUAL, _FUNCTIONAL, _FULL, _COMPARE, _IMPROVE); questions
  about a website, "clone this repository", "reproduce this bug" stay ordinary. A queued
  rebuild request (`queue: ...`) becomes a replica project rather than a single task
  (`replica_hook_intake`).
- Modes visual, functional, full (full only with source, API specifications, schemas or
  documentation; otherwise it runs as functional and says why); quality targets
  prototype, mvp, production.
- References: URLs, screenshots, recordings (metadata with ffprobe), HTML/CSS files,
  source folders (fingerprinted in place), documents, API specifications, schemas, mobile
  screens. Files are copied into the data folder with their SHA-256; changes after intake
  are reported. Page capture with Playwright when it is installed (own Chromium, else
  Chrome or Edge, else another installed Chromium; nothing is downloaded): screenshot,
  page source, computed-style summary, same-site links; no crawling, no login, no stored
  cookies; login pages and errors are recorded as limitations.
- Screen inventory, feature matrix and user flows with evidence classes OBSERVED,
  DOCUMENTED, USER_CONFIRMED, INFERRED, UNKNOWN, checked by code (an OBSERVED claim must
  cite an available visual reference; an inference is never a requirement; an unobserved
  flow cannot claim a successful outcome).
- Design tokens counted from computed styles, page sources, stylesheets and screenshot
  palettes.
- `replica plan` writes grouped reconstruction tasks into the linked project plan;
  architecture, hosting, approval, import, routing, workflows and compliance are the
  planning engine's and the queue's. Tasks of a replica project get read access to its
  reference folder (`--add-dir`).
- Visual comparison: share of matching content pixels (the reference background
  excluded; a one-pixel tolerance for anti-aliasing; whole-image share recorded too), method stated with every score, regions
  ranked, difference images, masks; exit codes 0/1/2. Pillow when installed, a built-in
  PNG reader otherwise.
- Functional parity matrix: only a real passing test verifies a feature; mocks, built
  but untested, failing and unknown are reported as such; JUnit import by feature/flow
  ids.
- Generated tasks use synthetic data; for an owned reference whose sample data the
  owner confirms is synthetic (`sample_data`), they reproduce the rows the inventory
  lists, so screens and behaviour stay comparable.
- Bounded improvement rounds (`replica improve [--run]`): stop on acceptance, round
  limit, budget, failed corrections, stall or regression (naming the commit before the
  round); acceptance is never declared while a screen with a reference screenshot has
  not been measured.
- Clean-room rules for third-party references in every generated task, and `replica
  sweep` for the reference's domains, titles, brand terms and identical files.
- Routing: plan tasks are classified on their work, without the approved-architecture
  and reference blocks every task carries (their wording could send a simple task to the
  top model). Router policy 4: the word "replica" alone is no longer read as database
  replication.
- Settings: `replica_python`, `replica_visual_threshold`, `replica_pixel_tolerance`,
  `replica_pixel_shift`, `replica_max_iterations`, `replica_fixes_per_iteration`, `replica_budget_usd`,
  `replica_capture_timeout_s`, `replica_capture_wait_ms`, `replica_hook_intake`.
- What every session carries grew: Claude Code's estimate rose from about 539 to about 864
  tokens (it counts all 27 skills; the two new ones Claude may start, `replica` and
  `replica-status`, add about 200 characters of description). The plugin's own budget
  (`always_on_token_budget` in release.json) is now 900.
- Guide: docs/replica.md. End to end with the real CLI: tests/replica_e2e.py.

## 0.5.0

Adaptive engineering workflow: each task gets the process it needs. Queues, commands and
data of 0.4.0 keep working (the workflow lives in a new, additive table) and going back
to 0.4.0 is possible. Two defaults change what you see; `workflow_mode off` restores the
0.4 behaviour.

- Every task gets a workflow, QUICK, STANDARD or DEEP, decided from the router's analysis
  (complexity, critical signals, architecture, database and security impact, regression
  risk, affected modules, kinds of work); no extra model call. The capabilities each
  workflow switches on (requirements, brainstorming, infrastructure, architecture,
  planning, test-first, debugging, subagents, review, verification) are a table the code
  applies: only those that are on reach the prompt.
- **Changed default:** DEEP tasks run as plan (Opus, no file edits), implement (Sonnet)
  and review when justified. A material deep plan (architecture, database, security,
  infrastructure) waits for `moa7amed-queue workflow approve <id>`; other tasks keep
  running. `workflow_deep_approval` = material (default), always, never.
- **Changed default:** a task must end with `MQ_VERIFIED: <what it checked>`; a `DONE`
  without it is recorded as failed and retried (`workflow_enforce_verification`).
  Planning sessions are exempt. A task that may not run commands verifies by reading and
  says so, which is accepted.
- Bounded escalation: a failed QUICK task is retried as STANDARD; a task reporting
  `MQ_ESCALATE_WORKFLOW` is retried as DEEP; once by default (`workflow_max_escalations`),
  with the reason recorded. Budget stops, interruptions and a missing verification line
  do not escalate.
- Approved project plans are reused: plan tasks do not plan again; deep tasks in a project
  with a current approved baseline do not redo the architecture analysis; each task gets
  only the approved decisions it touches (QUICK tasks none). An approved plan now records
  a fingerprint of the stack's manifests; a change marks the baseline as stale.
- Superpowers coexistence: detected through `claude plugin list --json` (cached six
  hours); the task is told not to run its brainstorming, planning and execution skills as
  well, or Superpowers leads with `workflow_authority superpowers`. Superpowers is never
  modified or required.
- New command `moa7amed-queue workflow` (show, `set --profile`, `approve`); the workflow
  appears in `route` and `show`.
- New settings: `workflow_mode`, `workflow_minimal_context`, `workflow_agent_policy`,
  `workflow_reuse_approved_plan`, `workflow_risk_based_review`,
  `workflow_enforce_verification`, `workflow_deep_approval`, `workflow_max_escalations`,
  `workflow_authority`, `workflow_superpowers`.
- Router policy 3: "migrate/move/switch the database to <engine>" is recognised as a
  database migration (it was classified as an ordinary Sonnet task).
- Cost: the workflow adds 550 to 1150 characters to a task prompt (about 140 to 280
  tokens, estimated). What every session carries is unchanged. With the real CLI the same
  two tasks cost about the same with and without the workflow (one run each; details in
  docs/workflow.md).
- Guide: docs/workflow.md. Live check: tests/live_workflow.py.

## 0.4.0

Project planning: plan a new system or a major change before building it, and let the
queue execute the approved plan. Nothing changes for queues without plans: commands,
settings and data of 0.3.2 work as before, the data format is the same (plans live in
new, additive tables), and going back to 0.3.2 is possible.

- Seven new skills: `project-plan`, `infrastructure-assess`, `architecture-review`,
  `plan-approve`, `plan-execute`, `plan-revise` (you start them) and `plan-status`.
- New command group `moa7amed-queue project ...`: new, list, show, discover, apply, env,
  target, depth, check, approve, execute, status, revise, report.
- Evidence classes on every fact, requirement and infrastructure value (VERIFIED with
  evidence, USER_CONFIRMED, ASSUMED, UNKNOWN, CONFLICTING). Discovery of existing
  projects separates what files show from what they suggest, and keeps the developer's
  machine apart from the deployment target.
- Infrastructure scenarios with hardware, hosting and capability fields; capacity
  arithmetic (requests per second, requests in flight, storage and database growth) with
  the formula and the weakest evidence class of its inputs, never presented as a
  measurement.
- Architecture options checked against each scenario by a small catalogue of
  documented limits and stated facts (heuristics can only ask for information).
  Unjustified infrastructure, replacing an existing stack without a material decision,
  and a needlessly complex choice are reported. Decision matrices give ranges when
  scores are missing or assumed.
- Architecture decision records: PROPOSED, NEEDS_INFORMATION, APPROVED, SUPERSEDED,
  REJECTED. Approval only through `project approve --yes`, which freezes an immutable
  revision with its SHA-256 and lists what each decision rests on that nobody confirmed.
  Approved decisions change only through an amendment that supersedes them.
- Tasks with milestones, dependencies (cycles detected and named), acceptance criteria,
  linked requirements and decisions. Tasks held by an unapproved decision are not
  imported; nothing else is held.
- `project execute` imports approved tasks into the queue in dependency order, in one
  transaction per task, without duplicates; changed tasks replace their queued copies,
  completed work stays with its revision. Model selection follows the task or the router.
- After a plan task commits, its change is checked against the approved architecture
  (`plan_compliance`, default `block`): a technology the plan did not approve fails the
  task with the reason, keeps the commit and holds its dependents.
- Reports: twenty-section planning report, requirements, infrastructure, ADRs, roadmap
  with a dependency graph, tasks.json, risks, assumptions, progress.
- Guide with a worked example: docs/planning.md.

What every session carries grew with the seven skills: Claude Code's estimate is about
539 tokens (it counts all seventeen skills; only the six Claude may start by itself are
in context according to its documentation).

## 0.3.2

Production readiness. Queues, settings and commands of 0.3.1 work unchanged and the data
format is the same; going back to 0.3.1 is possible. Nothing needs to be done when
updating.

Prompt capture

- Claude Code (2.1.329) puts a prompt the capture hook blocked back into the input box,
  so pressing Enter again, or typing on, queued the same request again. A prompt from
  the same session that repeats or extends a recent capture is now refused with an
  explanation, and the confirmation says to press Esc twice to clear the input box.
- The behaviour of `queue:` while Claude is working was measured in a real interactive
  session and is described in the README: the prompt is stored the moment Enter is
  pressed, also during a tool call, and the running work is not interrupted. Ordinary
  messages are not queued.

Recovery

- New `moa7amed-queue reconcile`: why the queue is blocked, which files a task found
  finished after a crash left uncommitted, who most likely changed each one (while the
  task ran, after it finished, before it started), other uncommitted changes, and the
  commands that resolve each case. `--commit` commits only the files changed while the
  task ran, `--stash` sets the task's files aside, `--release` lifts the hold; each needs
  `--yes`, and none runs while a runner is working.
- New setting `on_interrupted` (`retry` or `hold`): with `hold`, a task cut off by a crash
  waits for review instead of being run again, for tasks with effects outside the
  repository. `status` shows when files are held.

Cost

- A task stopped by Claude Code for reaching its budget is no longer retried
  automatically (retrying would spend the same again), and Claude Code's own message is
  shown. After a crash, such a result is recorded rather than run again.
- With `cost_limit_run_usd`, each task is given at most what the session has left as its
  `--max-budget-usd`, so the session limit is enforced by Claude Code during the last
  task too, not only between tasks.
- The README states what each control guarantees: Claude Code's figures are its own
  client-side estimates, and a budget can be passed by up to one model response
  (measured: $0.0138 for a $0.01 budget).

Verified with the real Claude Code CLI (2.1.329)

- Routing: automatic choice of Haiku and Sonnet, a manual override, fallback from a
  refused model, escalation bounded at one step, a task on Opus, and the model that ran
  recorded with each task (`tests/live_routing.py`).
- The queue end to end, including crashes (`tests/live_validation.py`).

Distribution

- Ownership stated consistently: created by Mohamed Ibrahim Ramadan, published as
  moa7amed, https://www.moa7amed.com/, MIT licence. No licence key, no payment; Claude
  usage is billed by Anthropic under the user's own plan.
- Release archive (`scripts/package.py`): reproducible, attached to GitHub releases, and
  loadable with `claude --plugin-dir <archive>.zip`.
- CI covers Windows, Linux and macOS and Python 3.8 to 3.13 (not yet run: the repository
  is not on GitHub).

## 0.3.1

A reliability and maintenance release. Queues, settings and commands of 0.3.0 work
unchanged, the data format is the same, and going back to 0.3.0 is possible. Nothing
needs to be done when updating.

Reliability

- A task whose Claude process had finished when its runner was killed, or stopped with
  Ctrl+C, is no longer run a second time. The next runner reads the result Claude left
  on disk and records the task as done or failed. Files the task changed are left
  exactly as found and are named in the log; they are not committed or stashed on its
  behalf, and no further task starts in that workspace until you have committed them,
  whatever `on_dirty` is set to.
- A runner lock is tied to the start time of the process that holds it, so a process id
  that the system has reused for another program can no longer keep a dead runner's
  lock, or a dead task, looking alive.
- A runner cannot be given a second task while its current one has no recorded result.
- Damaged bookkeeping (routing history, the state file) no longer stops a run.
- Fixed: a task that modified an existing file had that file recorded with the first
  letter of its name missing (`EADME.md`), and a new folder was recorded instead of the
  files in it. The list now comes from the task's commit. Present since 0.1.0; found by
  running the queue against the real Claude Code CLI.
- Fixed: `config set` refused a value that begins with a dash, which made `claude_args`
  impossible to set from the command line. `moa7amed-queue config set claude_args
  --safe-mode` now works.

Updates and data

- `moa7amed-queue update` shows the installed version, the version the marketplace
  offers, whether Claude Code updates the marketplace automatically, what changed, and
  whether it is safe to update now. The update itself is done by Claude Code.
- `moa7amed-queue doctor` checks an installation and its data and changes nothing. Run
  it after an update.
- `moa7amed-queue backup` and `restore` copy and bring back settings and queues. On the
  first run after an update the data is backed up by itself, before the new version
  writes anything (`backup_on_update`).
- `moa7amed-queue changelog` prints these notes.

Model routing

- Requests written in Arabic (standard and Egyptian), or mixing Arabic and English, are
  read by the same rules as English ones. On a set of 30 prompts written after the
  rules were fixed, the rules alone chose the intended tier for 19, and for 29 with the
  review step; every miss went to the middle tier, none to the smallest. Arabic requests
  the rules leave on the middle tier are therefore marked for the review step. These
  figures describe the test sets (`python tests/routing_eval.py`), not Arabic prompts
  in general.
- The rules version recorded with each decision is now 2. Decisions already stored are
  kept as they are.

Cost

- `cost_warn_task_usd` and `cost_warn_run_usd` log a warning when a task or a runner
  session reaches a cost. `cost_limit_run_usd` starts no further task once a session has
  cost that much: the runner exits with the new code 5, nothing is interrupted and the
  rest stays pending. All three are unset by default.
- `moa7amed-queue usage` totals what Claude Code reported, per model and per task.

Lighter

- Shorter skill descriptions, and three model skills that only you can invoke
  (`model-opus`, `model-sonnet`, `model-haiku`; ask for a model in plain words and
  `model-auto` handles it). Claude Code's estimate of what the plugin adds to every
  session went from about 545 to about 294 tokens.
- The capture hook no longer starts Python for every prompt once a setting has been
  saved (measured: 300 to 600 ms per prompt before, about 100 ms now).
- Commands that classify nothing no longer load the classifier (`status` measured at
  roughly half the time).

For maintainers: the marketplace entry no longer repeats the version (it is stated in
`plugin.json` only), and the repository gained release checks, upgrade tests from 0.2.0
and 0.3.0, CI workflows and contributor documentation.

## 0.3.0

The plugin gets its permanent name: **moa7amed-queue**, the Intelligent AI Coding Task
Orchestrator by moa7amed. Queue. Route. Execute. Verify. No feature was removed or
changed in behaviour.

- Plugin identifier `moa7amed-queue`, published in the `moa7amed-tools` marketplace.
  Skills are now invoked as `/moa7amed-queue:queue-add`, `/moa7amed-queue:model-opus`
  and so on; the skill names themselves are unchanged.
- The command is `moa7amed-queue`. `ctq` remains as an alias (in the plugin and as a
  shim), and `install-cli` adds the short alias `mq` when no other program uses that
  name.
- New data directory `~/.moa7amed-queue`. Data in `~/.claude-task-queue` is found and
  used where it is; `moa7amed-queue migrate [--dry-run]` moves it in one safe step
  (refused while a runner is active, never merges, re-registers git worktrees).
- Environment variables are now `MOA7AMED_QUEUE_HOME`, `_PYTHON`, `_CLAUDE_BIN` and
  `_CAPTURE_PREFIX`; the `CTQ_*` names are still honoured.
- Tasks are asked to end with `MQ_RESULT:` (and `MQ_ESCALATE:`); the `CTQ_` spellings
  are still understood.
- Unchanged: the database format, every setting, the `queue:` capture prefix, the
  `Task-Queue-Id` commit trailer and the `task-queue` worktree branch.
- The repository is now a marketplace that can hold further independent plugins; this
  plugin lives in `plugins/moa7amed-queue` and is installed and versioned on its own.

See "Upgrading from claude-task-queue" in the README.

## 0.2.0

Automatic model routing.

- Every queued task is classified (eleven scored dimensions, clause-level intent analysis,
  negation handling, workspace checks) and routed to Haiku, Sonnet or Opus. The runner
  starts each task with its own `--model`; Claude Code's default model is never changed.
- Routing policies: `balanced`, `quality_first`, `cost_optimized`.
- Manual overrides per task, for all pending tasks or for a whole project: `ctq model`,
  `ctq add --model`, and the skills `model-auto`, `model-opus`, `model-sonnet`,
  `model-haiku`.
- Model availability: tiers map to aliases (no hardcoded versions), restrictions in
  Claude Code settings are respected, the model each run actually used is read from the
  CLI's usage report and verified, and a refused model triggers a recorded fallback to
  the nearest permitted tier. A task with no permitted model is blocked, never run on an
  unknown one. New commands: `ctq models [--probe] [--reset]`, `ctq route`.
- Controlled escalation to a stronger model after repeated failure or at the model's own
  request, limited by `routing_max_escalations`.
- Optional model review of borderline classifications (one small request, tools off).
- Optional phased workflow: plan with Opus, implement with Sonnet, review when justified.
- Status, `show` and JSON output include the selected model, the reason, the override
  state, the actual model, the escalation history and the usage reported by the CLI.
- Runner options `-Model` / `--model` (one model for a run), `-RoutingMode`, `-NoRouting`.
- Routing data is stored in two additional tables; queues from 0.1.0 work unchanged.

## 0.1.0

First release.

- Persistent per-project task queue stored in SQLite under `~/.claude-task-queue`.
- Original prompts and acceptance criteria are stored verbatim and are immutable.
- Three ways to add tasks: the `queue:` prompt prefix (`UserPromptSubmit` hook), the
  `queue-add` skill, and the `ctq add` command.
- Skills: `queue-add`, `queue-run`, `queue-status`, `queue-retry`, `queue-priority`,
  `queue-cancel`.
- Runners: `runner/run-queue.ps1` (Windows PowerShell 5.1 and PowerShell 7) and the
  portable `ctq run` / `runner/run-queue.sh`; `ctq launch` starts one in the background.
- One runner per project (lock with heartbeat), recovery after a crashed runner, task
  dependencies with cycle detection, priorities, automatic and manual retries, execution
  history.
- Git integration: clean-workspace check with `stop` / `stash` / `allow` policies, one
  commit per successful task with a `Task-Queue-Id` trailer, leftovers of failed tasks
  preserved in a stash, optional dedicated worktree.
- Marketplace manifest for distribution from GitHub.
