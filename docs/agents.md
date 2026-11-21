# Multi-agent execution (stage A)

Since 0.8.0 a queued task can have **read-only analysis agents** run at the same time
before its own session, the **coordinator**, does the work. Stage A is deliberately
narrow:

- one authoritative coordinator per task: the task's own Claude Code session;
- concurrent agents that **only read and report** (repository, requirements,
  architecture, security, test-gap and documentation analysis; BidStudio intake and
  drafting; Replica screen analysis);
- **one writer at a time**: the coordinator. Agents never change the project.

Parallel implementation writes are **not implemented** (see [Not in stage A](#not-in-stage-a)).
Most tasks do not use agents at all and run exactly as before.

## Mechanism

| Mechanism | Used | Notes |
| :-- | :-- | :-- |
| Separate Claude Code sessions (`claude -p`) started concurrently by the queue | **yes** | Each agent is its own OS process with its own model, budget, tools and working directory. Start/end time and pid are recorded |
| Subagents inside a session (Claude Code's Agent/Task tool) | no | Denied to agents (`--disallowedTools Agent Task`), so an agent cannot start further agents. The coordinator keeps the tools its task normally has |
| Claude Code background agents (`claude agents`, `--bg`), custom agents (`--agents`), `--worktree` | no | The queue manages its own processes and snapshots |
| One session prompted in several roles in turn | no | Not reported as multi-agent |

The agents run during the task's claim step, before the coordinator's run exists; the
coordinator never runs at the same time as its own agents. `agent_max_parallel` counts
agents only.

## When a task uses agents

Every task is evaluated when it starts, by rules, without a model call:

1. **A decomposition must exist.**
   - Analysis roles from the router's dimension scores, only when justified:

     | Role | Chosen when |
     | :-- | :-- |
     | `security_review` | security implications ≥ 5, or a critical security/auth signal |
     | `architecture_review` | architecture impact ≥ 5 |
     | `test_gap_analysis` | testing ≥ 4, or testing ≥ 3 with code changes ≥ 3, or code changes ≥ 4 with regression risk ≥ 4 |
     | `repository_analysis` | affected modules ≥ 4, or code changes ≥ 6 |
     | `requirements_analysis` | ambiguity ≥ 6 |
     | `documentation_review` | documentation work with code changes ≥ 3 |

     QUICK tasks get none. STANDARD tasks at most two. DEEP tasks at most
     `max(2, agent_max_parallel)`: DEEP means *justified* analysis, not the most agents
     or the most expensive model. At least two roles must apply, otherwise one agent is
     enough and the task runs single. Requests in Arabic and English are classified the
     same way.
   - Built-in decompositions: BidStudio intake (stage T1, one agent per readable tender
     document), BidStudio drafting (stage T7, sections without content split between up
     to `agent_max_parallel` agents), Replica screen analysis
     (`moa7amed-queue replica agents <id>`, one agent per uncited screenshot).
   - An explicit plan: `moa7amed-queue agents plan <task> --data '{"subtasks": [...]}'`.
     Every subtask is read-only; a plan with `"mode": "write"` is refused.
2. **It must be safe**: no unknown or circular dependencies.
3. **It must be worth it**: expected benefit = 1 − critical path / total work. Below
   `agent_parallel_min_benefit` (25 %) the task runs single.
4. **A mechanism must be available**: the Claude Code CLI must be found and answer.
5. **The budget must allow it**: a budget too small to share runs single.
6. **Rounds are bounded**: `agent_max_analysis_rounds` (1) per task, across retries.

`agent_mode parallel` skips only the benefit threshold, never the safety, approval,
dependency or budget checks. `agent_mode single` turns agents off. Only tasks without a
workflow phase, or in the planning phase, are analysed.

## How read-only is enforced

Not by prompt alone. Each agent gets three layers:

1. **Tool restriction** (Claude Code flags): `--tools Read,Grep,Glob` (the only
   built-in tools that exist in the session) and `--disallowedTools Bash PowerShell
   Edit Write NotebookEdit MultiEdit Agent Task WebFetch WebSearch`. The task's own
   `--permission-mode`, `--allowedTools` and `--add-dir` are not passed on.
2. **Permission mode** `dontAsk`: anything not pre-approved is denied, never prompted.
3. **Isolated snapshot**: in a git project the agent's working directory is a detached
   worktree of `HEAD` in the queue's data directory, removed afterwards. Outside git, only
   layers 1 and 2 apply (recorded as "tool restrictions only").

Plus a check: the live workspace's `HEAD` and `git status` are compared before and after
the agents. Any difference holds the task (`agents:` in `status`); `retry` and
`models --reset` do not release it, `moa7amed-queue agents release <task>` does, after
you have looked. Agents also cannot run or join queue runners (`run`, `launch`,
`runner-acquire` refuse when `MOA7AMED_QUEUE_AGENT` is set).

**Boundary.** These are Claude Code's own tool and permission controls plus a separate
checkout; they are not an OS sandbox. A defect in Claude Code's enforcement, or an MCP
server configured in your settings that writes, is outside what the plugin controls (the
before/after check would then stop the task). Agents see committed files (`HEAD`), not
your uncommitted changes. Read-only access to bid and Replica folders is given with
`--add-dir`.

## Input revisions, integration and stale results

Each agent is told its input revision (a fingerprint of its inputs plus the commit) and
must echo it in its result. Results are integrated one at a time, as each agent
finishes, by the queue process in short transactions (no transaction is held while an
agent runs):

| Result | Integration |
| :-- | :-- |
| Analysis report | kept for the coordinator (`reported`) |
| BidStudio sections | applied only for the agent's assigned sections, and only if each is unchanged since the agent started (per-section fingerprint); a changed section is `stale`, never overwritten; other accepted sections are kept |
| BidStudio requirements | numbered by the queue, duplicates dropped, every quote checked against the document; an invented quote is rejected |
| Replica screen | only the agent's screen, only if unchanged; design tokens in results are ignored (the design baseline has one writer) |

A result with the wrong input revision is `stale`; a second identical result is
`duplicate`; integration is guarded so a result is never accepted twice. Whatever an
agent did not deliver (failed, stale, skipped, timed out) becomes `taken_over`: the
coordinator does it.

## The coordinator and authority

The coordinator gets an `<agents>` section with every agent's status, summary and
findings, does the implementation as the single writer, runs the task's verification and
must report it (`MQ_VERIFIED`); the parent task completes only through it.

Agents cannot approve anything (architecture, prices, contract exceptions, releases),
change pricing rules, generate or submit documents, publish or deploy. Fields such as
`"approve"` in a result are ignored. Reference content (tender documents, screenshots,
task context) is data: agents are told so, and whatever it says, their tools stay the
same.

## Recovery

Subtasks, attempts, pids, timestamps, revisions, results, usage and outcomes are stored
in the queue database (`agent_plans`, `agent_subtasks`, `agent_events`).

- A failed agent is retried up to `agent_max_attempts` (2) in total; then taken over.
- An agent exceeding `agent_timeout_seconds` (900) is stopped and taken over.
- Cancelling the task stops its own agent processes (process tree of the recorded pid),
  marks them `cancelled` and keeps prompts and output for diagnosis. Other processes are
  not touched.
- After a runner crash: an agent that finished has its output collected (not rerun); one
  still running is waited for; an interrupted one runs again (it changed nothing).
- A coordinator failure or retry never reruns finished agents or accepts a result twice.

## Budget and usage

- `agent_budget_policy shared`: agents share the task's budget (`max_budget_usd`, or
  what is left of `cost_limit_run_usd`); it is never multiplied. `agent_reserve_share`
  (30 %) stays with the coordinator. An agent starts only if its share of what is left,
  after what running agents may still spend, is at least $0.05; that share is its
  `--max-budget-usd`. The coordinator's budget is reduced by what the agents spent.
- Agent spend counts toward `cost_limit_run_usd` and appears in `moa7amed-queue usage`.
  Cost not reported by Claude Code is shown as **unknown**, not zero.
- Budgets are soft limits: Claude Code checks after spending, so a session can end
  slightly above.

## Visibility

```text
moa7amed-queue show 12            # Agents: 2 read-only agent(s), ...
moa7amed-queue agents show 12     # strategy, reason, mechanism, roles, pids, start/end,
                                  # isolation, revision, integration, cost (or unknown)
moa7amed-queue agents evaluate 12 # how it would run now (changes nothing)
moa7amed-queue agents release 12  # release a workspace-changed hold after checking
moa7amed-queue replica agents r1  # queue concurrent screen analysis
```

`agents show` prints only what was measured; a plan that never ran shows no timings.

## Settings

| Setting | Default | |
| :-- | :-- | :-- |
| `agent_mode` | `auto` | `auto`, `single`, `parallel` |
| `agent_max_parallel` | `3` | agents of one task at the same time (coordinator not counted; it never overlaps them) |
| `agent_write_policy` | `coordinator_only` | the only value in stage A |
| `agent_timeout_seconds` | `900` | per agent session |
| `agent_max_attempts` | `2` | attempts per agent (failures only) |
| `agent_max_analysis_rounds` | `1` | per task, across retries |
| `agent_parallel_min_benefit` | `0.25` | expected share of wall time saved |
| `agent_budget_policy` | `shared` | the only policy |
| `agent_reserve_share` | `0.3` | budget kept for the coordinator |

## Measured

Real Claude Code 2.1.329 on Windows 11, 2026-11-20, `tests/agents_live.py`: a small
login module with an SQL injection; task "Fix the SQL injection in the login function in
app/auth.py and add regression tests for the authentication flow"; every session on
Haiku (manual override, to keep the test cheap). The rules chose `security_review` and
`test_gap_analysis`.

| | Parallel (2 at once) | Serial (1 at a time) | Single (no agents) |
| :-- | :-- | :-- | :-- |
| Agents | 2, started 0.9 s apart, overlapped | 2, one after the other | 0 |
| Agent wall time | 103.6 s | 123.6 s | - |
| Agent cost (reported) | $0.0189 | $0.0152 | - |
| Coordinator cost | $0.0205 (two runs: the runner was killed after it started) | $0.0159 | $0.0130 |
| Task wall time | 208.7 s (includes the kill and restart) | 195.7 s | 59.9 s |
| `<agents>` section in the coordinator's prompt | 6085 chars (~1500 tokens) | 5412 chars (~1350 tokens) | 0 |
| Verification reported, tests pass afterwards | yes | yes | yes |

In the parallel run the runner was killed once the coordinator had started and then
restarted: neither finished agent ran again, and the restarted coordinator received
their findings. On every run, agents worked on a snapshot of the starting commit,
echoed their input revision, left no snapshot behind, and the live workspace was changed
only by the coordinator.

What this shows: concurrency shortens the analysis phase (here by about 16 %), and
agents always add time, cost and prompt context compared with no agents. On a fix this
small the single-session run was fastest and cheapest. Concurrent sessions also pay
for their own prompt cache, while serial sessions reuse a warm one. That is why agents
run only when the rules find independent, justified analysis, and QUICK tasks never use
them. Whether the analysis improves the result is not measured here. No skill was
added, so what every Claude Code session carries is unchanged (~1189 tokens, Claude
Code's estimate).

## Not in stage A

- **Parallel implementation writes** (several agents changing files at once, with file
  ownership, isolated branches and integration) are a future stage and are not
  implemented. Reasons: merging concurrent code changes safely needs ownership plans per
  project, conflict handling that never touches your uncommitted work, and verification
  per change; until that exists, one writer is the safe default.
- Skills in your interactive session are not split by the plugin; they hand parallel
  analysis to the queue (`replica agents`, `bid queue`).
- An agent's own output is not streamed to the runner window.
