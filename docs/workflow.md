# Adaptive engineering workflow

Since 0.5.0 every queued task gets the amount of engineering process it needs: none of the
ceremony for a one-line text change, a plan and an approval step for an architecture
change. This page says how the choice is made, what each workflow does, how to change it,
and what it costs.

## The three workflows

| Workflow | For | What happens |
| :-- | :-- | :-- |
| **QUICK** | small, low-risk, well-defined changes: text, documentation, configuration values, a mechanical edit | understand, change, verify. No planning, no architecture work, no subagents. |
| **STANDARD** | ordinary development: a feature, a bug fix, a refactoring inside one area | understand, short targeted plan, implement, relevant tests, self-review of the diff, verify. A bug fix starts by reproducing the problem and finding the root cause. |
| **DEEP** | architecture, database design or migration, authentication and security design, infrastructure and deployment, very large high-risk changes | a planning session on the top model (no file edits) with requirements, at least two approaches, the deployment environment and the architectural decision; your approval when the change is material; implementation; tests; a review session when justified; verification. |

Verification is required in every workflow: the task must end with a line

```text
MQ_VERIFIED: <the commands, tests or checks it ran and what they showed>
```

before its `MQ_RESULT` line. A result without it is not accepted (see
[Verification](#verification)).

## How the workflow is chosen

The workflow is decided from the analysis the model router already makes when a task is
queued; no extra model call is made. It uses the router's dimensions and signals, not
the length of the request or a single keyword:

- **DEEP** when the request involves architecture, database design or migration,
  authentication or security design, infrastructure or deployment (the router's critical
  signals), when architecture, database or security impact is high (7 of 10 or more), or
  when the change is both very complex (score 85+) and large.
- **QUICK** only when the router judges the task small (Haiku tier, or a very low score
  with nothing but text, documentation, configuration or mechanical work), it has no
  critical signal, and both regression risk and the number of affected modules are low.
- **STANDARD** otherwise. A short request for hard work ("Fix a deadlock in the payment
  service") is STANDARD on Opus with debugging switched on, not QUICK; a long request for
  a small change is not DEEP.

Arabic and mixed Arabic/English requests go through the same analysis.

Some examples, as decided by the rules (`moa7amed-queue workflow` shows yours):

| Request | Workflow | Model |
| :-- | :-- | :-- |
| Update the login button text to Sign in | QUICK | haiku |
| Implement shipment search with filters on the shipments page | STANDARD | sonnet |
| Fix a race condition in the billing worker | STANDARD, with debugging | opus |
| Migrate the database to PostgreSQL. | DEEP | opus plans, sonnet implements |
| Redesign the enterprise deployment architecture and move the database to a cluster | DEEP, material | opus plans, sonnet implements, opus reviews when justified |

The rules are heuristics. They will sometimes be wrong, which is why the workflow can be
changed by hand and escalates on evidence.

## What each workflow switches on

The table below is the policy the code applies (`scripts/mqlib/workflow.py`, `CAPABILITIES`).
It decides what the task's prompt contains, whether the plan/implement/review phases
run, whether the plan waits for approval, and whether a result is accepted.

| Capability | QUICK | STANDARD | DEEP |
| :-- | :-- | :-- | :-- |
| requirements | minimal | targeted | comprehensive |
| brainstorming (alternatives) | off | when needed | on (two or more approaches) |
| infrastructure assessment | only if relevant | only if affected | on |
| architecture | off | only if changed (otherwise escalate) | on |
| planning | off | light, in the session | a separate planning session |
| test-first | optional | when suitable | preferred |
| debugging (root cause first) | for bug fixes | for bug fixes | for bug fixes |
| subagents | not allowed | only for independent parts | when justified |
| review | targeted | risk based | comprehensive |
| verification | always | always | always |

Only the capabilities that are on are written into the prompt. The workflow adds 550 to
1150 characters to a task prompt (see [Cost](#cost)).

## Approval of a deep plan

A DEEP task that is **material** (architecture, database, security or infrastructure
design) stops after its planning session and waits for you:

```bash
moa7amed-queue workflow              # shows "approval: waiting"
moa7amed-queue show 12               # read the plan
moa7amed-queue workflow approve 12   # implementation starts on the next run
moa7amed-queue cancel 12             # or drop it
```

Choosing the model by hand (`moa7amed-queue model sonnet 12`, or `add --model`) changes
the model of every phase, planning included; it does not skip the planning session or
the approval.

Other tasks keep running in the meantime; tasks that depend on it wait. Neither `retry`
nor `models --reset` gets past an approval, and the wait survives a crash or a restart.
`workflow_deep_approval` decides which deep tasks wait: `material` (default), `always`,
`never`.

Tasks that come from an approved [project plan](planning.md) do not wait again and do not
plan again: their plan, architecture and requirements are already approved.

## Approved architecture is reused, not redone

When the project has an approved plan, a task's prompt carries only the parts of it that
matter for that task: the approved decisions related to what the task touches (database
decisions for database work, and so on), the approved technologies and the deployment
target. QUICK tasks get none of it. A DEEP task in a project with a current approved
baseline does not redo the infrastructure and architecture analysis; it plans how the
work fits the approved architecture and says so when it cannot (that needs a plan
revision).

**Stale baselines.** When a plan is approved, the files that define the stack are
fingerprinted (package manifests, project files, Dockerfiles and compose files, server
and application configuration). If any of them changes later, the baseline is no longer
treated as current: DEEP tasks do the architecture work again and the prompt notes what
changed. Run `/moa7amed-queue:plan-revise` to bring the plan up to date.

## Escalation

The workflow can deepen during a task, once by default (`workflow_max_escalations`):

- a QUICK task that fails is retried as STANDARD;
- a task that reports `MQ_ESCALATE_WORKFLOW: <why>` (it needs an architectural change
  the workflow does not allow) is retried as DEEP, starting with a planning session.

The reason is recorded and shown by `moa7amed-queue workflow`. Work that was completed is
not redone, the number of attempts still bounds the task, and an escalation never lowers
the approval or verification a task needs. A budget stop, an interruption or a missing
verification line is not treated as evidence that a task needs more process. Model
escalation (a stronger model) is separate and works as before.

## Verification

Every task is asked to add an `MQ_VERIFIED:` line saying what it checked. A run that
reports success without one is recorded as failed with the reason, its changes are set
aside like those of any failed run, and the task is retried within its attempts. Planning
sessions produce a plan and need no such line.

The line is a report, not proof: the queue checks that the task said how it verified the
work, not that the checks were good. Read it in `moa7amed-queue workflow`.

**Tasks cannot run commands by default.** With the default `permission_mode`
`acceptEdits`, shell commands (running tests, for example) are denied. A task that cannot
run its tests checks the result by reading it and says so on the verification line,
which is accepted. To let tasks run tests, allow the commands they need:

```bash
moa7amed-queue config set allowed_tools "Bash(python -m unittest *),Bash(npm test *)"
```

Set `workflow_enforce_verification false` to accept results without the line.

## Superpowers and other workflow plugins

If the Superpowers plugin is installed, its brainstorming, planning and execution skills
would run the same process a second time. The queue asks Claude Code which plugins are
installed (`claude plugin list --json`, at most every six hours, cached in the data
directory) and, when Superpowers is enabled, tells the task not to run those skills: the
queue's workflow is the authoritative one. Set `workflow_authority superpowers` to make
Superpowers lead instead; verification is still required. `workflow_superpowers yes|no`
skips the detection. The queue never changes, configures or depends on Superpowers.

## Changing the workflow yourself

```bash
moa7amed-queue workflow                          # every pending task: workflow, reason, model, agents, planning, verification
moa7amed-queue workflow 12                       # one task
moa7amed-queue workflow set 12 --profile deep    # quick, standard, deep, or auto to let the rules decide again
```

A workflow set by hand is kept when the task is classified again. Lowering it never
removes verification, and a task from an approved plan keeps the plan's approval. A model
chosen by hand (`moa7amed-queue model ...`) is kept whatever the workflow.

## Settings

| Setting | Default | Meaning |
| :-- | :-- | :-- |
| `workflow_mode` | `auto` | `auto` chooses a workflow per task; `off` is the 0.4 behaviour (no workflow section, no verification check, phases only when asked for). |
| `workflow_minimal_context` | `true` | Give a task only the approved project context it touches; `false` gives all approved decisions. |
| `workflow_agent_policy` | `on_demand` | Subagents only where the workflow allows them; `never` forbids them. |
| `workflow_reuse_approved_plan` | `true` | Reuse a current approved architecture instead of redoing it. |
| `workflow_risk_based_review` | `true` | Review depth follows risk; `false` asks for a comprehensive review in every task. |
| `workflow_enforce_verification` | `true` | Accept a finished task only with an `MQ_VERIFIED` line. |
| `workflow_deep_approval` | `material` | Deep plans that wait for you: `material`, `always`, `never`. |
| `workflow_max_escalations` | `1` | How often a task's workflow may deepen. |
| `workflow_authority` | `moa7amed` | Which workflow leads when Superpowers is installed: `moa7amed` or `superpowers`. |
| `workflow_superpowers` | `auto` | Whether Superpowers is installed: `auto` asks Claude Code, `yes`, `no`. |

## Cost

What the workflow adds, measured with `python tests/measure_overhead.py` (characters are
counted; tokens are an estimate of about four characters each, not a measurement):

| Task prompt (characters) | off | auto | added | ~tokens added (estimate) |
| :-- | --: | --: | --: | --: |
| quick (button text) | 1165 | 1715 | 550 | ~140 |
| standard (feature) | 1186 | 2173 | 987 | ~250 |
| standard bug fix | 1163 | 2286 | 1123 | ~280 |
| deep, planning session | 1337 | 2006 | 669 | ~170 |
| deep, implementation session | 1363 | 2146 | 783 | ~200 |

About 290 characters of each "added" figure are the verification instruction. For
comparison, a Claude Code session's own system prompt and tool definitions are many
thousands of tokens, so the workflow section is a small share of what a task session
reads.

What every Claude Code session carries is unchanged: no skill was added and no skill
description changed. Choosing the workflow takes no model call and no measurable time
next to the classification that was already made.

With the real CLI (2.1.329), the same two tasks with the workflow off and on cost about
the same as reported by Claude Code: $0.0044 against $0.0042 to $0.0048 for the spelling
fix (Haiku), $0.077 against $0.075 for the small function with a test (Sonnet), one run
each (`tests/live_workflow.py`, about $0.25 per full run). That is an observation, not a
benchmark; the savings the workflow aims at (no planning for small tasks, no repeated
architecture work, plan approval before expensive implementation) depend on the mix of
tasks and were not measured.

## Limitations

- The workflow is chosen by the same rules as the model, and shares their mistakes.
  Check `moa7amed-queue workflow` for tasks that matter and set it by hand when needed.
- The workflow is an instruction to the task's Claude session. The queue enforces what it
  can observe (phases, approval, the verification line, escalation); it cannot enforce
  that a session actually wrote tests first or reviewed its diff.
- Superpowers detection relies on `claude plugin list --json`; when that fails, the
  plugin is treated as not installed.
