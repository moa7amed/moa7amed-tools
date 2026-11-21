# moa7amed-queue

**Intelligent AI Coding Task Orchestrator**

Queue. Route. Execute. Verify.

**moa7amed-queue is an intelligent task orchestrator plugin for Claude Code.** It is
published by **moa7amed** in the [moa7amed-tools](../../README.md) marketplace. It runs
inside [Claude Code](https://code.claude.com/docs) and executes queued tasks with the
Claude Code CLI; it is not a Codex or Cursor extension (see [Compatibility](#compatibility)).

When you think of the next three things while Claude is still busy with the first, you
normally have two bad options: interrupt it, or try to remember. moa7amed-queue gives you
a third: every prompt becomes an independent, persistent **task**; each task is
**routed** to the model its difficulty calls for; an external runner **executes** the
tasks one after another, each in its own fresh Claude Code session; and every result is
**verified** before it is committed: the task has to check its acceptance criteria and
report an explicit result, and the model that actually ran is checked against the one
requested.

```text
 you, any time                             later, unattended
 -------------                             -----------------
 queue: fix the flaky test          \
 /moa7amed-queue:queue-add ...       +--> queue --> route --> execute --> verify --> commit
 moa7amed-queue add "update docs"   /     (one per   (haiku,   (one task   (criteria,
                                          project)   sonnet,   at a time)  result line)
                                                     opus)
```

What you get:

- **Nothing is lost.** The original prompt and its acceptance criteria are stored verbatim
  and can never be edited or deleted (the database enforces it).
- **Strictly sequential.** One runner per project, one task at a time, guarded by a lock.
- **Your work is safe.** The runner refuses to start on a workspace with uncommitted
  changes, never discards anything, and can work in a separate git worktree instead.
- **Survives crashes.** A killed runner or a reboot leaves the queue consistent. A task
  that was interrupted goes back in line; a task that had in fact finished is recorded
  from the result it left and is **not run a second time**.
- **Dependencies, priorities, retries** and a full execution history per task.
- **The right model for each task.** Every task is analysed and runs on Haiku, Sonnet or
  Opus according to its difficulty, with manual overrides, safe fallback and controlled
  escalation. See [Automatic model routing](#automatic-model-routing).
- **Git tracking.** Each successful task becomes one commit carrying a `Task-Queue-Id` trailer.
- **One queue per project**, as many projects as you like, installed once for all of them.
- **Spending under control.** Warnings and a limit per runner session, a limit per task,
  and a report of what each task cost, all from the figures Claude Code itself reports.
  See [Cost](#cost).
- **Safe to update.** A health check, backups (also made automatically after an update)
  and a report of what an update would change. See [Updating](#updating).

## Contents

- [Requirements](#requirements)
- [Compatibility](#compatibility)
- [Install](#install)
- [Quick start](#quick-start)
- [Adding tasks](#adding-tasks)
- [Running the queue](#running-the-queue)
- [Watching and managing the queue](#watching-and-managing-the-queue)
- [How a task is executed](#how-a-task-is-executed)
- [Adaptive engineering workflow](#adaptive-engineering-workflow)
- [Multi-agent execution](#multi-agent-execution-stage-a)
- [Challenge, discuss, then execute](#challenge-discuss-then-execute)
- [Automatic model routing](#automatic-model-routing)
- [Project planning](#project-planning)
- [Replica: rebuilding an application](#replica-rebuilding-an-application)
- [BidStudio: tenders and proposals](#bidstudio-tenders-and-proposals)
- [Git safety](#git-safety)
- [Locking and crash recovery](#locking-and-crash-recovery)
- [Projects and data](#projects-and-data)
- [Cost](#cost)
- [Settings](#settings)
- [Command reference](#command-reference)
- [Tests](#tests)
- [Troubleshooting](#troubleshooting)
- [Upgrading from claude-task-queue](#upgrading-from-claude-task-queue)
- [Updating](#updating)
- [Backups](#backups)
- [Removing](#removing)
- [Releasing](#releasing)
- [Limitations](#limitations)

## Requirements

| Needed | Notes |
| :-- | :-- |
| Claude Code CLI | Tested with 2.1.329 (0.3.2) and 2.1.206 (up to 0.3.1). `claude` must be on `PATH` for the runner. |
| Python 3.8+ | Standard library only; nothing to `pip install`. |
| git | Optional, but without it there are no commits and no uncommitted-change protection. |
| Windows | Windows PowerShell 5.1 or PowerShell 7 for the native runner; Git for Windows (Git Bash) for the capture hook. |
| macOS / Linux | A POSIX `sh`. PowerShell is not needed. |

## Compatibility

The current release, 0.8.0, is a Claude Code plugin. What works where:

| Where | What works | Needs | Tested |
| :-- | :-- | :-- | :-- |
| **Claude Code CLI, plugin installed** (native) | Everything: the `/moa7amed-queue:...` skills, the `queue:` prompt hook, the runner, planning, Replica, BidStudio | Claude Code, Python 3.8+, git (recommended), `sh` for the hook | Real sessions with Claude Code 2.1.329 on Windows 11; test suites on Windows and on Linux (Ubuntu) |
| **`moa7amed-queue` command in a terminal** (standalone) | Queue management without a model: `add` (routing is local rules), `status`, `list`, `show`, `cancel`, `priority`, `config`, `usage`, and the `project`, `bid` and `replica` records | Python 3.8+ and the plugin's files (an installed plugin or a clone); see [the plugin README](#the-command-moa7amed-queue) | Windows, also with `claude` removed from `PATH`; Linux test suites |
| **Executing tasks** (`run`, `launch`, the runners), from any terminal | Each task runs in a new Claude Code session (`claude -p`) | **The Claude Code CLI, installed and signed in.** Without it no task can start: the attempt is recorded as failed with the reason, and `doctor` reports Claude Code as missing | Windows with the real CLI |
| **An editor's integrated terminal** (VS Code, Cursor, the shell of Codex or another tool) | The same as any terminal. This is terminal access, not an integration: tasks still run in Claude Code, not in the editor's own agent | As above | Not tested in Cursor or Codex |
| **Claude Code's IDE extensions** (VS Code, JetBrains) | Not verified | | Not tested |
| **Codex, Cursor or other agents, natively** | **Not implemented.** No adapters exist. Claude Code skills, hooks and plugin installation do not work in Codex or Cursor | | |
| **macOS** | Expected to work like Linux, not verified | | Not tested (the CI workflow includes it) |

## Install

### From the moa7amed-tools marketplace (recommended)

> **Status: not published yet.** These two commands work once the repository is public
> at `github.com/moa7amed/moa7amed-tools`. Until then use
> [From a local clone](#from-a-local-clone) with the folder you have.

```bash
# in a terminal (uses the Claude Code CLI)
claude plugin marketplace add moa7amed/moa7amed-tools
claude plugin install moa7amed-queue@moa7amed-tools
```

This installs at **user scope**: the plugin is available in every project on your
machine. Use `--scope project` to record it in a repository's `.claude/settings.json`
for your team instead, or `--scope local` for one checkout only.

The same two steps work inside Claude Code as `/plugin marketplace add ...` and
`/plugin install ...`. Restart Claude Code (or run `/reload-plugins`) afterwards.

Coming from `claude-task-queue`? See
[Upgrading from claude-task-queue](#upgrading-from-claude-task-queue): your queues and
settings carry over as they are.

### From a local clone

```bash
# in a terminal
git clone https://github.com/moa7amed/moa7amed-tools
claude plugin marketplace add ./moa7amed-tools
claude plugin install moa7amed-queue@moa7amed-tools
```

### For one session only (no installation)

```bash
# in a terminal: starts a Claude Code session with the plugin loaded
claude --plugin-dir /path/to/moa7amed-tools/plugins/moa7amed-queue
```

### The command: `moa7amed-queue`

The plugin's command line tool is what the skills, the hook and the runners use, and you
can use it yourself in any terminal, including an editor's integrated terminal. Managing
the queue needs only Python; executing tasks needs the Claude Code CLI
([Compatibility](#compatibility)).

| Name | What it is |
| :-- | :-- |
| `moa7amed-queue` | The command. The name is unique, so it cannot collide with anything else on your machine. |
| `ctq` | Alias, kept for compatibility: it was the command's name before version 0.3.0. Everything that works with `moa7amed-queue` works with `ctq`. |
| `mq` | Optional short alias. It is only created when no other program called `mq` is on your `PATH`, and never inside the plugin itself, so it cannot shadow somebody else's tool. |

To call it from any terminal, create the command shims once:

```bash
# macOS, Linux, Git Bash. Marketplace install (adjust the version folder):
sh ~/.claude/plugins/cache/moa7amed-tools/moa7amed-queue/0.3.1/bin/moa7amed-queue install-cli
# or from a clone:
sh /path/to/moa7amed-tools/plugins/moa7amed-queue/bin/moa7amed-queue install-cli
```

```powershell
# PowerShell. Marketplace install (adjust the version folder):
& "$env:USERPROFILE\.claude\plugins\cache\moa7amed-tools\moa7amed-queue\0.3.1\bin\moa7amed-queue.cmd" install-cli
# or from a clone:
& C:\path\to\moa7amed-tools\plugins\moa7amed-queue\bin\moa7amed-queue.cmd install-cli
```

It prints the folder to add to your `PATH` (`~/.moa7amed-queue/bin`) and which names it
created. Add `--no-short` to skip `mq`. The shims keep working after the plugin is
updated: they follow the copy Claude Code is using.

## Quick start

Inside Claude Code, in your project:

```text
queue: Add input validation to the signup form. Acceptance criteria: empty email is rejected with a message.
queue: Update the README to describe the new validation.
/moa7amed-queue:queue-status
/moa7amed-queue:queue-run
```

The two `queue:` prompts are stored and never reach Claude. `queue-run` opens the runner,
which executes task 1, commits it, executes task 2, commits it, and stops. Each task runs
on the model chosen for it: here Sonnet for the validation work and Haiku for the README.

## Adding tasks

There are three ways; all of them store the prompt exactly as written.

**1. The `queue:` prefix** (fastest; no model call). A prompt that starts with `queue:` is
captured by the plugin's `UserPromptSubmit` hook, saved as a task and blocked before it
reaches Claude:

```text
queue: Rename the config module and fix its imports.
```

Claude Code confirms with the task id. The prefix is case-insensitive and can be changed
or switched off (see [Settings](#settings)).

**2. The skill.** Supports options and lets Claude split a multi-part request into tasks:

```text
/moa7amed-queue:queue-add --priority high --after 3 Migrate the data once the schema task is done
```

Claude also uses this skill on its own when you say things like "queue this for later".

**3. The command line**, from any terminal, even while Claude Code is busy:

```bash
moa7amed-queue add "Fix the typo in CONTRIBUTING.md"
moa7amed-queue add --priority urgent --after 4,5 --acceptance "all tests pass" "Bump the SDK to v3"
moa7amed-queue add --prompt-file task.md              # long prompts: read from a file
cat task.md | moa7amed-queue add --stdin              # or from stdin
```

### While Claude is in the middle of a turn

| How you add the task | What happens |
| :-- | :-- |
| `moa7amed-queue add …` in another terminal | Stored immediately. Claude Code is not involved at all. |
| `/moa7amed-queue:queue-add …` | Claude Code holds commands typed during a turn until the turn ends, then runs them, so the work in progress is not interrupted. |
| `queue: …` | Stored the moment you press Enter, also during a turn or a running tool call, and not sent to Claude. See below for what Claude Code does with the text afterwards. |
| a message without the prefix | Goes to Claude, not to the queue. Claude Code queues it and hands it to Claude as soon as the current tool calls finish, within the same turn. |

What was observed in a real interactive session (Claude Code 2.1.329, Windows, the TUI
driven through a pseudo-terminal, hooks logging every event with a timestamp):

- `queue:` typed while Claude was writing a long answer, and again while a 20-second
  shell command was running: the prompt hook ran about one second after Enter, long
  before the turn or the tool call ended. The task was stored, the prompt never reached
  Claude, and the turn and the tool call completed normally.
- After the hook has blocked a prompt, **Claude Code puts the text back into the input
  box**, whether Claude was busy or idle, and also when the hook asks it not to repeat
  the prompt (`suppressOriginalPrompt`). Whatever you type next is added to that text.
  In the first test this queued the same request three times.
- The plugin therefore refuses a prompt from the same session that repeats or extends a
  request it queued in the last 12 hours, and says why ("NOT queued again" / "NOT
  queued: this starts with the text of task #N..."). Nothing is lost: the text is still in
  the input box. **Press Esc twice to clear the input box after each capture.**
- In one early run the model moved its shell command into the background, and the
  keystrokes typed meanwhile went to the background-task panel instead of the input
  box: that prompt was never submitted at all. Interface state can swallow input; check
  that you see "Queued as task #N".

Claude Code does not document how hooks treat messages typed during a turn, so this may
change between versions. When it matters, use `moa7amed-queue add` in another terminal:
it does not involve Claude Code at all.

### Acceptance criteria

Criteria are stored next to the prompt and handed to Claude with it. Either write them
inside the prompt under an `Acceptance criteria:` line or heading (they are detected
automatically and the prompt stays whole), or pass them separately with `--acceptance`
or `--acceptance-file`.

### Priorities and dependencies

- Priority is `low`, `normal`, `high`, `urgent` or any integer. Higher runs first; equal
  priorities run in the order they were added.
- `--after 3,4` makes a task wait until tasks 3 and 4 are **done**. A dependency always
  beats priority. If a prerequisite fails or is cancelled, the dependent task is shown as
  **blocked** until you retry the prerequisite or remove the dependency.

## Running the queue

The runner is a separate process. It never runs inside your interactive session.

### From Claude Code

```text
/moa7amed-queue:queue-run
/moa7amed-queue:queue-run --worktree --max-tasks 3
```

Shows the plan, then starts the runner in the background (on Windows: the PowerShell
runner in its own window) and returns immediately.

### Windows: the PowerShell runner

```powershell
# from a clone (run-queue.cmd wraps the script so the execution policy is not an issue)
C:\path\to\moa7amed-tools\plugins\moa7amed-queue\runner\run-queue.cmd -Project C:\src\my-app

# or directly
powershell -ExecutionPolicy Bypass -File C:\path\to\moa7amed-tools\plugins\moa7amed-queue\runner\run-queue.ps1 -Project C:\src\my-app
```

After `moa7amed-queue install-cli`: `moa7amed-queue-run -Project C:\src\my-app`.

### macOS, Linux, Git Bash: the portable runner

```bash
sh /path/to/moa7amed-tools/plugins/moa7amed-queue/runner/run-queue.sh --project ~/src/my-app
moa7amed-queue run                      # same thing, in the current project
moa7amed-queue launch                   # same, detached in the background
```

`run-queue.ps1` is written for PowerShell 7 on macOS and Linux as well; the test suite
exercises it wherever `powershell` or `pwsh` is installed.

### Runner options

| PowerShell | `moa7amed-queue run` / `moa7amed-queue launch` | Meaning |
| :-- | :-- | :-- |
| `-Project DIR` | `--project DIR` | Project to work on (default: current directory). |
| `-MaxTasks N` | `--max-tasks N` | Stop after N tasks. |
| `-Watch` | `--watch` | Keep running and pick up tasks as they are added. |
| `-OnDirty stop\|stash\|allow` | `--on-dirty …` | Uncommitted changes before a task: stop (default), stash them, or run anyway. |
| `-Workspace inplace\|worktree` | `--workspace …` | Run in the project directory (default) or in a dedicated git worktree. |
| `-Commit auto\|none` | `--commit …` | Commit each successful task (default) or leave changes uncommitted. With `none`, later tasks of the same run build on the uncommitted changes, and the next run needs them committed or stashed first. |
| `-PermissionMode MODE` | `--permission-mode MODE` | Passed to `claude --permission-mode` (default `acceptEdits`). |
| `-AllowedTools "A,B"` | `--allowed-tools "A,B"` | Passed to `claude --allowedTools`. |
| `-DisallowedTools "A,B"` | `--disallowed-tools "A,B"` | Passed to `claude --disallowedTools`. |
| `-Model NAME` | `--model NAME` | Run every task of this run on one model instead of routing each task. |
| `-RoutingMode POLICY` | `--routing-mode POLICY` | Routing policy for this run. |
| `-NoRouting` | `--no-routing` | Do not choose models; use Claude Code's default. |
| `-MaxBudgetUsd N` | `--max-budget-usd N` | Passed to `claude --max-budget-usd`, per task. |
| `-TimeoutMinutes N` | `--timeout-minutes N` | Stop a task after N minutes (default 60, 0 = no limit). |
| `-StopOnFailure` | `--stop-on-failure` | Stop the runner when a task attempt fails. |
| `-ClaudeCommand PATH` | `--claude-bin PATH` | Claude Code executable to use. |
| `-ClaudeArgs …` | `--claude-args=--flag` | Extra arguments for every `claude` call. |
| `-DryRun` | `--dry-run` (or `moa7amed-queue plan`) | Show what would run and the exact `claude` command; start nothing. |
| `-Force` | `--force` | Take over even if another runner still looks alive. |

Exit codes: `0` everything done, `1` a task failed or is blocked, `2` another runner holds
the project, `3` stopped to protect uncommitted changes, `4` the lock was taken over,
`5` the cost limit of the session was reached, `130` interrupted.

## Watching and managing the queue

| In Claude Code | Command line | What it does |
| :-- | :-- | :-- |
| `/moa7amed-queue:queue-status` | `moa7amed-queue status` | Runner state, counts, open tasks, what runs next. |
| `/moa7amed-queue:queue-status 7` | `moa7amed-queue show 7` | One task: original prompt, criteria, every run, commits, cost. |
| `/moa7amed-queue:queue-status history` | `moa7amed-queue history [ID]` | Execution history. |
| | `moa7amed-queue list [--status failed,done]` | All tasks. |
| | `moa7amed-queue log` / `moa7amed-queue log 7` | Runner log / the result text of a task's last run. |
| `/moa7amed-queue:queue-retry 7` | `moa7amed-queue retry 7` / `moa7amed-queue retry --failed` | Requeue failed or cancelled tasks with fresh attempts. |
| `/moa7amed-queue:queue-priority 7 high` | `moa7amed-queue priority 7 high` | Change priority. |
| | `moa7amed-queue depend 7 --on 3` / `--remove 3` | Change prerequisites (cycles are rejected). |
| `/moa7amed-queue:queue-cancel 7` | `moa7amed-queue cancel 7` | Cancel a task that has not run. It stays in the history. |
| | `moa7amed-queue projects` | Every project that has a queue. |
| `/moa7amed-queue:queue-status` | `moa7amed-queue route [ID]` | Which model each task uses and why, what actually ran, escalations, usage. |
| | `moa7amed-queue models [--probe]` | Which models can be used on this machine. |
| `/moa7amed-queue:model-opus 7` (also `model-sonnet`, `model-haiku`, `model-auto`) | `moa7amed-queue model opus 7` | Choose a task's model yourself, or return it to automatic. |

Task states: `pending`, `waiting` (prerequisites not done), `blocked` (a prerequisite
failed or was cancelled, or no permitted model is available), `backoff` (retry scheduled), `running`, `done`, `failed`,
`cancelled`.

## How a task is executed

For each task the runner starts:

```text
claude -p --output-format json --permission-mode acceptEdits --model <the task's model> [your options]
```

in the directory the task was queued from, with the prompt on **stdin**. Every value is
passed as its own argument, never through a shell, and model names are validated first. The prompt
contains your original text and acceptance criteria verbatim, the results of the
prerequisite tasks, the reason a previous attempt failed (on a retry), and a short set of
rules for unattended work, including the task's [workflow](#adaptive-engineering-workflow).
Claude is asked to end with a verification line and a status line:

```text
MQ_VERIFIED: <the commands, tests or checks it ran and what they showed>
MQ_RESULT: DONE - <summary>        or        MQ_RESULT: FAILED - <reason>
```

A run that reports `DONE` without the verification line is not accepted
(`workflow_enforce_verification`).

A task **fails** when Claude reports `FAILED`, exits with an error, returns an error
result, exceeds the time limit, or reports success without saying how it verified it. A failed task is retried automatically until its
attempts are used up (`max_attempts`, default 2, with a growing pause in between). If
[escalation](#escalation) applies it then gets one more attempt on a stronger model.
After that it is marked `failed`. `moa7amed-queue retry` gives it a fresh set.

**Permissions.** Tasks run with nobody to approve prompts. With the default
`acceptEdits`, file edits work; anything else that would ask for permission (most shell
commands) is denied unless your project's permission rules or `-AllowedTools` allow it,
for example `-AllowedTools "Bash(npm test *),Bash(git diff *)"` (or the setting
`allowed_tools`). A task that could not do its work because a tool was denied reports
failure and says so; one that could only not run its tests checks the result by reading
it and says so on its verification line.

Each run's exact prompt, raw output and stderr are kept under the project's data
directory (see below), and the Claude session id is recorded so you can open it with
`claude --resume <id>`.

## Adaptive engineering workflow

Every task gets the amount of process it needs, decided from the same analysis that
picks its model (no extra model call):

| Workflow | For | What happens |
| :-- | :-- | :-- |
| QUICK | text, documentation, configuration, small mechanical changes | change and verify; no planning, no architecture work, no subagents |
| STANDARD | features, bug fixes, refactoring in one area | short targeted plan, implement, relevant tests, self-review, verify; bug fixes start from the root cause |
| DEEP | architecture, database, security, infrastructure, very large high-risk changes | planning session on Opus, your approval when material, implementation, tests, review when justified, verify |

```bash
moa7amed-queue workflow                          # workflow, reason, model, agents, planning, verification
moa7amed-queue workflow approve 12               # a material deep plan waits for this
moa7amed-queue workflow set 12 --profile deep    # quick, standard, deep, auto
```

A short request for hard work ("Fix a race condition in the billing worker") is not QUICK; a
long request for a small change is not DEEP. Tasks from an approved project plan reuse
the plan instead of planning again, and get only the approved decisions they touch; a
change to the project's manifests since the approval marks the baseline as stale. A
failed QUICK task is retried as STANDARD, and a task that reports it needs an
architectural change is retried as DEEP, once (`workflow_max_escalations`). When
Superpowers is installed, the task is told not to run its planning and execution skills
as well. `workflow_mode off` restores the 0.4 behaviour.

The workflow adds 550 to 1150 characters to a task prompt (about 140 to 280 tokens,
estimated); what every session carries is unchanged. Full guide, the capability table,
settings and limitations: [Adaptive engineering workflow](../../docs/workflow.md).

## Multi-agent execution (stage A)

When a task's analysis is justified (security, architecture, test gaps, repository
breadth, ambiguous requirements; BidStudio intake and drafting; Replica screen
analysis), read-only agents run at the same time in separate Claude Code sessions
before the task's own session, the coordinator, does the work. Agents only read and
report: they get `--tools Read,Grep,Glob`, permission mode `dontAsk`, no subagent tools
and a detached snapshot of `HEAD` as their working directory; the live workspace is
checked before and after. The coordinator is the only writer, integrates the findings,
and must verify. Every task is evaluated by rules when it starts, without a model call;
most stay single. Parallel implementation writes are a future stage, not implemented.

```bash
moa7amed-queue agents show 12       # strategy, roles, measured overlap, revisions, usage
moa7amed-queue agents evaluate 12   # how it would run (changes nothing)
```

Guide, enforcement boundary, recovery and settings:
[Multi-agent execution](../../docs/agents.md).

## Challenge, discuss, then execute

A task with a concrete risk (credentials in code, a security control switched off, data
deleted for good, history rewritten, a production or publishing step) can wait for your
decision instead of running unasked. The queue states the concern, its evidence, the
impact and an alternative; you take the recommendation or proceed with the original
approach, in your own words. The decision is stored with the task and its revision,
handed to the task's session, and not asked again unless new evidence appears. Other
tasks keep running meanwhile.

```bash
moa7amed-queue config set challenge_mode adaptive     # rules check each task (default: explicit)
moa7amed-queue challenge                              # what waits for a decision
moa7amed-queue challenge decide C1 --original         # or --recommendation
```

In Claude Code, `/moa7amed-queue:grillme` asks for a critical review of a task, plan or
approach. While a decision is open, typing "I know the risk; go ahead with my original plan." in
Claude Code records it. Only you can decide: queued tasks and
agents cannot, and a decision never lifts a tool permission, the approval of a deep plan,
the verification check or a cost limit. Guide: [Challenge, discuss, then
execute](../../docs/challenge.md).

## Automatic model routing

Every task runs on the model its difficulty calls for. A README fix does not need the
strongest model, and a deadlock should not be handed to the smallest one. The queue
decides per task, starts each task with its own `claude --model …`, and records what
actually ran. Claude Code's default model and your settings are never changed.

| Tier | Used for |
| :-- | :-- |
| **Opus** | Architecture, difficult debugging and root-cause analysis, database design and migrations, authentication and authorization design, complex or cross-module refactoring, deep performance investigation, complex integration failures, high-risk production changes. |
| **Sonnet** | Normal feature development, backend APIs, frontend UI, forms and validation, dashboards, search and filtering, tests, standard refactoring, routine bug fixes. |
| **Haiku** | Simple documentation, README updates, small text changes, basic file organisation, simple explanations, straightforward repetitive edits. |

### How a task is classified

When a task is queued, a rule-based classifier reads the prompt and its acceptance
criteria. It does not look for a keyword and pick a model. It works out:

- **what is asked, clause by clause**: documentation, a text change, an explanation, a
  mechanical edit, a fix, new functionality, design work. "Update the README. Then fix
  the deadlock" is two different things, and the harder one decides;
- **technical signals with a strength**, ignoring negated ones ("without touching the
  schema") and treating them as the *subject* when the request is only about
  documentation ("document the authentication API");
- **structure**: files and modules named, work items, acceptance criteria, stack traces,
  questions and hedging;
- **the workspace**: whether the files named exist and how large they are.

From that it scores eleven dimensions from 0 to 10 (technical complexity, reasoning
depth, affected modules, expected code changes, architecture impact, database
involvement, security implications, testing requirements, ambiguity, regression risk,
effort), combines them into a complexity score from 0 to 100, and picks a tier:

- **Haiku only with positive evidence** that the change is trivial and no evidence of
  risk. A short prompt is never assumed to be easy: "Fix the deadlock in the payment
  worker" goes to Opus, "Fix checkout bug" to Sonnet, and something as vague as "Fix it"
  to Sonnet with a note that it needs a second look.
- **Opus** when a critical signal is present (hard debugging, concurrency, architecture,
  schema or data migration, security design, a production-risk change, cross-codebase
  refactoring) or the score is high.
- **Sonnet** otherwise.

Classification takes a few milliseconds, uses no tokens, and the same text always gives
the same result.

**A second opinion for borderline cases.** When the rules are not confident (the score
is close to a threshold, or the request is too vague), the task is reviewed once by the
smallest available model just before it starts: one request with tools switched off and
a task excerpt of at most 1,800 characters. In our test with Claude Code 2.1.206 that
call cost about one cent. The model may move the task by one tier; it can never send a
task to Haiku that the rules found ineligible. If the call fails, the rule-based result
stands. Set `routing_classifier` to `off` to never make this call, or `always` to
review every task.

### Arabic and mixed-language requests

Requests written in Arabic (Modern Standard and Egyptian), or mixing Arabic with English
technical terms, are read by the same rules as English ones: the same clause-by-clause
reading, the same signals, the same guard that the smallest model needs positive
evidence of a simple task. Spelling variants are treated alike (hamza forms, final
`ة`/`ه`, `ى`/`ي`, diacritics).

What was measured, and what it does and does not show:

| Set of prompts | Result | What it tells you |
| :-- | :-- | :-- |
| English reference set (75) | 75 of 75 | The Arabic vocabulary changed no English decision |
| Arabic and mixed, written while building the rules (79) | 23 wrong before, 0 after | The rules do what they were written to do; nothing more |
| Arabic and mixed, set aside while building the rules (24) | 9 wrong before, 0 after | A little more, but not much: it was drafted in the same sitting by the same author |
| A second set (30), written **after** the rules were frozen, in other words, scored once | **19 of 30** with the rules alone | The honest figure. All 11 misses went to Sonnet: ten hard tasks that deserved Opus and one trivial one that deserved Haiku. **No hard task went to Haiku** |
| The same 30 with the review step on (real calls) | **29 of 30** | The one-line model review is what catches unfamiliar phrasing. 23 review calls, $0.28 in total |

Those 30 have since been used to extend the vocabulary, so they are no longer an
independent test. The sets are in `tests/routing_corpus_multilingual.py`;
`python tests/routing_eval.py` prints the current numbers per language and topic.

What follows for you:

- The rules recognise the Arabic phrasing they know, and fall back to **Sonnet**, never
  to Haiku, on phrasing they do not. A hard task in unfamiliar words is the realistic
  failure, and it fails towards the middle tier.
- An Arabic request that the rules leave on Sonnet is therefore marked for the review
  step. Keep `routing_classifier` at its default (`ambiguous`) if you queue in Arabic;
  the review costs about one cent per task it is used on.
- These figures describe those prompt sets. They are not an accuracy claim for Arabic
  prompts in general, and other dialects were not measured at all.
- If a task is misjudged, `moa7amed-queue model opus ID` fixes that task, and the prompt
  is welcome as an issue: the sets above are how the vocabulary grows.

### Routing policies

```bash
moa7amed-queue config set routing_mode balanced         # default
moa7amed-queue config set routing_mode quality_first
moa7amed-queue config set routing_mode cost_optimized
```

| Policy | Behaviour |
| :-- | :-- |
| `balanced` | Sonnet for ordinary development, Opus for complex tasks, Haiku for simple ones. |
| `quality_first` | Also sends substantial refactoring, database, security or production-sensitive work to Opus, and is stricter about what counts as trivial. |
| `cost_optimized` | Needs stronger evidence before using Opus and lets larger documentation jobs use Haiku. It never sends a code change to Haiku. |

The policies differ only on borderline tasks. A deadlock goes to Opus and a typo to
Haiku under all three. A policy applies to tasks queued after it was set; `moa7amed-queue route
--reclassify` applies it to tasks already waiting (manual overrides are kept).

### Seeing what was decided

```text
$ moa7amed-queue status
Models  automatic, balanced policy  (* manual, a>b moved, ! blocked)
    ID  PRIORITY STATE     TRIES MODEL         TITLE
     1  normal   running   1/2   opus          Split the orders table without downtime
     2  normal   pending   0/2   sonnet        Master data search
     3  normal   pending   0/2   haiku*        README update

$ moa7amed-queue route 1
Task #1 - Split the orders table without downtime
  Model:       opus (automatic)
  Reason:      Complex task: database design or migration (schema change, database migration); high-risk production change (zero downtime)
  Complexity:  100/100 (policy balanced, confidence high, classified by rules)
  Override:    no
  Actual:      claude-opus-…        (the id the CLI reported)
  Escalations: 0
  Usage:       $0.4210 over 1 run(s), as reported by the CLI
  Status:      running
  History:
    - run 12: asked for opus, ran on claude-opus-… (confirmed) -> done, $0.4210
```

`moa7amed-queue show ID` includes the same report plus the model of every run.
`/moa7amed-queue:queue-status` shows it inside Claude Code. Usage and cost are stored
exactly as the CLI reports them; when the CLI reports nothing, nothing is shown.

### Choosing the model yourself

| In Claude Code | Command line | Effect |
| :-- | :-- | :-- |
| `/moa7amed-queue:model-opus 4 7` | `moa7amed-queue model opus 4 7` | Tasks 4 and 7 run on Opus. |
| `/moa7amed-queue:model-sonnet 4` | `moa7amed-queue model sonnet 4` | Task 4 runs on Sonnet. |
| `/moa7amed-queue:model-haiku 4` | `moa7amed-queue model haiku 4` | Task 4 runs on Haiku. |
| `/moa7amed-queue:model-auto 4` | `moa7amed-queue model auto 4` | Task 4 returns to automatic selection. |
| | `moa7amed-queue model opus --pending` | Every pending task. |
| | `moa7amed-queue model sonnet --whole-project` | Every task of this project, now and later. `moa7amed-queue model auto --whole-project` undoes it. |
| | `moa7amed-queue add --model opus "…"` | Queue a task with a fixed model. |
| | `moa7amed-queue model claude-opus-5-5 4` | Any model alias or id the CLI accepts. |

An override belongs to the task (or the project) it was given to. It never changes
Claude Code's default model, your settings files, or any other task. Precedence: a task's
own override, then a model set for the run or the project (`-Model`, `--model`,
`--whole-project`), then automatic routing.

A task that is **running keeps its model**. A change is refused, and nothing ever
restarts a running task to switch models.

The three commands that pick a model are yours to type; Claude does not start them by
itself. If you ask in plain words ("run task 4 on Opus"), Claude uses `model-auto`, which
knows how to set a model as well as how to clear one.

### When a model is not available

Claude Code has no command that lists the models an account may use, so the queue does
not pretend to know. `moa7amed-queue models` shows what it has actually learned:

```text
$ moa7amed-queue models
Claude Code  2.1.206  (…/claude)
Selection    per task, with `claude --model`
  TIER    MODEL    STATUS       EVIDENCE
  opus    opus     unavailable  There's an issue with the selected model (opus). It may not exist or you may not have access to it.
  sonnet  sonnet   confirmed    ran as claude-sonnet-… on 2026-11-20 10:12:03
  haiku   haiku    unknown      never tried
```

- **confirmed**: a run's own usage report named that model.
- **unavailable**: the CLI refused it, Claude Code's settings exclude it
  (`availableModels`, `deniedModels`), or the plugin settings exclude it.
- **unknown**: not tried yet. It is tried when a task needs it, and the outcome is
  remembered. `moa7amed-queue models --probe` checks every tier right away with one tiny request
  each.

What happens when a task's model cannot be used:

1. The refusal is detected from the CLI's own error.
2. The task moves to the **nearest permitted tier** (Opus to Sonnet; Sonnet to Opus;
   Haiku to Sonnet, then Opus) and the reason is recorded in its history. A task is
   never moved below the lowest tier its classification allows, so a code change does
   not end up on Haiku this way.
3. The refused start does not count as an attempt, and the task is not run twice: a
   refused model has done nothing in the workspace.
4. Later tasks skip the refused model without asking again, for 24 hours
   (`routing_unavailable_ttl_hours`) or until `moa7amed-queue models --reset`. After that, tasks
   return to their recommended model.
5. If no permitted model is left, the task is marked **blocked** with the explanation and
   the rest of the queue carries on. It is never run on an unknown model. `moa7amed-queue retry ID`,
   `moa7amed-queue model … ID` or `moa7amed-queue models --reset` release it.

A **manual** choice is not replaced: if the model you picked is unavailable, the task is
blocked and tells you. A model set for a whole run that turns out to be unavailable stops
the runner (exit code 3).

After every run the model the CLI reports is compared with the one requested. Claude
Code can start a session on its default model when the requested one is not allowed; the
queue notices the difference, shows it in the task's history, and treats the requested
model as unavailable from then on.

If your plan has no access to a tier, say so once and the queue never asks for it:

```bash
moa7amed-queue config set routing_available_models "sonnet,haiku" --global   # only these tiers
moa7amed-queue config set routing_model_opus none --global                    # or switch one tier off
```

To pin tiers to exact versions instead of the moving aliases:

```bash
moa7amed-queue config set routing_model_opus claude-opus-5-5 --global
```

### Escalation

A task that turns out to be harder than it looked can move up one tier
(Haiku to Sonnet to Opus), under control:

- **When**: after `routing_escalate_after_failures` failed attempts on the same model
  (default 2), or when the model itself reports that the task is beyond it. A single
  failing test is not a reason, and neither is a tool denied by the permission mode.
- **How often**: at most `routing_max_escalations` times per task (default 1). The
  stronger model gets `routing_escalation_attempts` attempt(s) (default 1). After that the
  task is failed like any other, so retries and token use are bounded: with the defaults
  a task runs at most three times.
- **What is kept**: commits stay; the unfinished changes of failed attempts stay in their
  stashes; the stronger model is told what failed and why.
- **Where to**: only to a model that can be used. Tasks with a manual override are not
  escalated.

The reason is stored in the task's history (`moa7amed-queue route ID`). Switch it off with
`moa7amed-queue config set routing_allow_escalation false`.

### Optional: plan with Opus, implement with Sonnet

DEEP tasks of the [adaptive workflow](#adaptive-engineering-workflow) run in these phases
automatically. For any other task you can ask for them; each phase is a separate
`claude -p` session:

1. **Plan** (Opus, file-editing tools withheld): produces a technical plan, which is stored.
2. **Implement** (Sonnet): receives the plan and does the work; changes are committed.
3. **Review** (Opus), only when justified (very high complexity, or heavy security,
   database or regression risk): receives the plan, the implementation report and the
   commit, and can fix defects or reject. A rejected review sends the task back to
   implementation with the findings; the plan is not redone.

```bash
moa7amed-queue add --phased "Redesign the permission system for per-tenant roles"   # one task
moa7amed-queue route 12 --phased on                                                 # an existing task
moa7amed-queue config set routing_phased auto              # every task routed to Opus with a score of 70+
moa7amed-queue config set routing_phased_review always     # auto (default), always, never
```

The phases of a task run back to back; no other task is started in between. The whole
thing counts as one attempt. Without access to Opus, a phased task runs as a single
phase on its own model.

### Switching routing off

```bash
moa7amed-queue config set routing_enabled false     # this project; add --global for all
moa7amed-queue model sonnet --whole-project         # or: one fixed model for the project
```

```powershell
run-queue.cmd -Project . -NoRouting      # one run without routing
run-queue.cmd -Project . -Model sonnet   # one run on one model
```

With routing off and no model set, tasks use Claude Code's default model, as in version
0.1. Per-task overrides are still honoured.

### Routing settings

`moa7amed-queue config set <name> <value>` (add `--global` for every project). The `modelRouting.*`
names are accepted as aliases.

| Setting | Alias | Default | Meaning |
| :-- | :-- | :-- | :-- |
| `routing_enabled` | `modelRouting.enabled` | `true` | Choose a model per task. |
| `routing_mode` | `modelRouting.defaultMode` | `balanced` | `balanced`, `quality_first` or `cost_optimized`. |
| `routing_allow_manual_override` | `modelRouting.allowManualOverride` | `true` | Honour `moa7amed-queue model` overrides. |
| `routing_allow_escalation` | `modelRouting.allowEscalation` | `true` | Allow moving a task to a stronger model. |
| `routing_max_escalations` | `modelRouting.maxEscalations` | `1` | Escalations per task. |
| `routing_preserve_active_task_model` | `modelRouting.preserveActiveTaskModel` | `true` | Refuse model changes for a running task. With `false` a change is accepted but still only applies to later attempts. |
| `routing_fallback_on_unavailable` | `modelRouting.fallbackOnUnavailableModel` | `true` | Use the nearest permitted model; with `false` the task is blocked instead. |
| `routing_escalate_after_failures` | | `2` | Failed attempts on one model before escalating. |
| `routing_escalation_attempts` | | `1` | Attempts on the stronger model. |
| `routing_model_opus`, `routing_model_sonnet`, `routing_model_haiku` | | `opus`, `sonnet`, `haiku` | Model of each tier: an alias or a full id; `none` switches the tier off. |
| `routing_available_models` | | (none) | Tiers or models this account may use. |
| `routing_classifier` | | `ambiguous` | Model review of borderline decisions: `off`, `ambiguous`, `always`. |
| `routing_phased` | | `off` | `auto` enables the phased workflow for very complex tasks. |
| `routing_phased_review` | | `auto` | Final review phase: `auto`, `always`, `never`. |
| `routing_unavailable_ttl_hours` | | `24` | Ask again for a refused model after this long (0 = never). |

### What is stored per task

`moa7amed-queue show ID --json` returns a `routing` object:

| Field | Meaning |
| :-- | :-- |
| `modelSelectionMode` | `auto` or `manual`. |
| `recommendedModel` | The tier the classifier recommends. |
| `selectedModel` | The tier or model the next run will use. |
| `actualModel` | The model id the CLI reported for the latest run. |
| `modelReason` | The explanation of the recommendation. |
| `complexityScore` | 0 to 100. |
| `routingPolicyVersion` | Classifier version and policy, for example `1/balanced`. |
| `classificationTimestamp` | When the task was classified. |
| `manualOverride` | `true` when the model was chosen by hand. |
| `escalationCount` | How often the task was escalated. |
| `executionModelHistory` | Every run (requested model, actual model, verification, outcome, cost) and every override, fallback, escalation, review and block. |

Routing data lives in two tables of its own next to the existing ones. Queues created by
version 0.1 keep working without any conversion: their tasks are classified the first
time they are needed, finished tasks are left as they are, and the original tables are
not altered.

## Project planning

For a new system or a major change, the queue can plan before it builds: requirements
with their evidence, the real deployment environment, architecture options compared
against it, recorded decisions, and a task breakdown that the queue then executes. Small,
well-defined changes do not need any of this: keep queueing them as before.

```text
/moa7amed-queue:project-plan <id> <what to build, or the documents>
/moa7amed-queue:infrastructure-assess <id>
/moa7amed-queue:architecture-review <id>
/moa7amed-queue:plan-approve <id>
/moa7amed-queue:plan-execute <id>
/moa7amed-queue:plan-status <id>
/moa7amed-queue:plan-revise <id> <what was found>
```

What it guarantees:

- Every fact, requirement and infrastructure value is VERIFIED (with its evidence), USER
  CONFIRMED, ASSUMED, UNKNOWN or CONFLICTING. Unknowns are listed and asked about, never
  filled in. Your development machine is never taken for the server.
- Each architecture option is checked against each infrastructure scenario
  (incompatible, needs information, compatible) with the reason and what it rests on.
  Extra infrastructure (a cache, a broker, containers, a GPU...) must name the requirement
  that needs it, and a simpler option that is not shown to be worse is pointed out.
- Decisions are recorded as ADRs and become APPROVED only through your explicit approval
  of a specific, frozen plan revision. An approved decision can only be superseded.
- Only approved tasks enter the queue, in dependency order, without duplicates; tasks
  that wait for an unapproved decision are held and nothing else is.
- After a plan task commits, its change is checked against the approved architecture; a
  change that brings in a technology the plan did not approve fails the task with the
  reason (the commit is kept) and holds its dependents.

The analysis itself is done by Claude in your session; the engine checks and records it
and calls no model. Full guide, a worked example with real output, and the limitations:
[Project planning](../../docs/planning.md).

## Replica: rebuilding an application

*Understand. Reconstruct. Compare. Improve.* Ask Claude to rebuild an application from a
URL, screenshots, screen recordings, source code or specifications, and Replica turns
the request into evidence, a plan you approve, and queue tasks that build the result in
small verified steps. It then measures the rebuild against the reference screenshots and
checks its behaviour in a real browser.

```text
Reconstruct this app from its URL and these screenshots: https://...
queue: Replicate this site: https://...           (saved as a replica project, not a task)
/moa7amed-queue:replica r1                        continue a saved project
```

| Stage | Skill | Command |
| :-- | :-- | :-- |
| References and screens | `replica-recon` | `replica ref`, `replica capture`, `replica analyze` |
| User flows | `replica-flows` | `replica analyze` |
| Design system | `replica-design` | `replica tokens` |
| Backend (functional, full) | `replica-backend` | `replica analyze`, `project apply` |
| Architecture and plan | `replica-plan` | `replica plan`, then the planning engine |
| Build | `replica-build` | `project execute`, the queue |
| Visual comparison and bounded correction | `replica-compare` | `replica compare`, `replica improve` |
| Functional parity | `replica-test` | `replica test`, `replica report` |
| Status | `replica-status` | `replica status` |

- **Modes:** visual (the interface), functional (plus observable behaviour), full (plus
  backend, data and deployment; only with source, API specifications, schemas or
  documentation). The least extensive mode that satisfies the request is the default.
- **Evidence:** every screen, feature and flow is OBSERVED, DOCUMENTED, USER_CONFIRMED,
  INFERRED or UNKNOWN; inferences never become requirements, and a flow nobody observed
  cannot claim a successful outcome.
- **Comparison** is a measured pixel comparison of content (the reference background
  excluded), with the method printed next to every score, difference images and the
  regions that differ most. It never verifies a feature: only a real test does.
- **Bounded improvement:** correction rounds stop when every screen meets
  `replica_visual_threshold`, at `replica_max_iterations`, at `replica_budget_usd`, when all
  corrections fail, after two rounds without gain, or when a screen gets worse.
- **Clean room:** for third-party references the tasks require original code, branding,
  artwork and text; `replica sweep` checks the rebuild for the reference's names and files.

Guide, examples, limitations and how this compares to other reconstruction tools:
[Replica](../../docs/replica.md).

## BidStudio: tenders and proposals

*Analyze. Design. Price. Propose. Verify.* Give Claude a tender package and ask for the
analysis, the technical proposal, the commercial proposal, the complete package or a
review of an existing proposal:

```text
Draft our technical and commercial offer for this tender.
Analyze this tender.   |   Check our price quotation against the RFP.   |   جهز العرض الفني والمالي
```

| Stage | Skill | Command |
| :-- | :-- | :-- |
| Documents and requirements | `bid-analyze` | `bid doc`, `bid text`, `bid pages`, `bid read`, `bid apply` |
| Compliance matrix | `bid-compliance` | `bid apply`, `bid check` |
| Clarifications, addenda, bid/no-bid | `bid-clarifications` | `bid doc --amends`, `bid decision` |
| Solution and technical proposal | `bid-technical` | the planning engine, `bid generate` |
| Effort estimate | `bid-estimation` | `bid estimate` |
| Pricing | `bid-pricing` | `bid labour`, `bid price --scenarios`, `bid approve pricing` |
| Commercial proposal | `bid-commercial` | `bid generate` |
| Review and documents | `bid-review` | `bid review`, `bid checklist`, `bid approve release` |
| Status | `bid-status` | `bid status` |

- Every requirement is quoted from its document and the engine checks the quote; changes
  from addenda supersede, never overwrite; every change is an immutable revision.
- COMPLIANT needs a real answer and evidence; claims about the company need approved
  items of its profile (kept in the data directory, never in a project or the plugin).
- Pricing is computed by code with decimal numbers (cost, markup, gross margin,
  contingency, discount, tax, total; FX scenarios); the workbook's formulas reproduce it.
- Writes the technical and commercial proposals and an executive summary (DOCX and, with
  a browser, PDF), the internal pricing model, the BOQ, the compliance matrix and the
  clarification register (XLSX), in English or Arabic (right to left), and checks every
  file: no price in the technical envelope, no internal figure in customer documents.
- Approvals for architecture, compliance commitments, licensing, prices, contract
  exceptions and release are yours; BidStudio never submits.

Guide, formulas, document checks and limitations: [BidStudio](../../docs/bidstudio.md).

## Git safety

The rule the runner follows: **never overwrite and never discard uncommitted work.**

- **Before a task** the workspace must be clean. If it is not:
  - `stop` (default): the runner exits with code 3, lists the files, changes nothing.
  - `stash`: everything, untracked files included, is moved into a labelled `git stash`.
    The stash is never popped or dropped for you.
  - `allow`: the task runs anyway; nothing is committed automatically.
- **After a successful task** its changes are committed as one commit:

  ```text
  <task title> (task #7)

  Queued task #7, attempt 1, executed by moa7amed-queue.

  Task-Queue-Id: 7
  ```

  Find a task's commits with `git log --grep "Task-Queue-Id: 7"`. Commits Claude made
  itself during the task are recorded too.
- **After a failed, timed-out or interrupted task** whatever it left behind is moved into
  a labelled stash (named in the task's error), so the next task starts clean and nothing
  is lost. Restore it with `git stash apply <sha>`.
- The runner refuses to start in the middle of a merge, rebase, cherry-pick or bisect.
- It never resets, checks out, force-pushes, pushes, or deletes a stash.

### Keep working while the queue runs: worktree mode

`-Workspace worktree` (`--worktree` in the skill) runs every task in a dedicated git
worktree on the `task-queue` branch, stored outside your project. Your project directory,
including uncommitted work, is not touched at all. Merge the results when you want them:

```bash
git merge task-queue
```

The worktree and branch are reused by later runs. To bring newer work from your branch
into the queue's branch, merge the other way inside the worktree (`moa7amed-queue where` shows the
data directory; the worktree is its `worktree` subfolder).

### Without git

In a directory that is not a git repository, tasks simply run in place: no commits, no
stashes, no change detection. The lock still guarantees one task at a time.

## Locking and crash recovery

- **One runner per project.** Starting a second one fails immediately with exit code 2
  and does not disturb the first. The lock records the runner's process id, host and a
  heartbeat.
- **A runner that died** (killed, crashed, reboot) leaves a stale lock. The next runner
  detects that the process is gone and takes over. `moa7amed-queue status` shows a stale
  lock; `moa7amed-queue recover` clears it by hand. What happens to the task it was
  running depends on how far that task got:
  - **Claude had not finished.** The run is recorded as `interrupted` and the task goes
    back in the queue, or is marked failed if it has no attempts left. A run lost to a
    crash counts as an attempt, so a task that keeps taking its runner down cannot loop
    forever.
  - **Claude had finished, and only the bookkeeping was lost.** The result Claude left
    on disk is read and the task is recorded as done or failed accordingly. It is **not
    run again**. Because time has passed, the files it changed are left exactly as they
    are, not committed and not stashed, and the runner tells you which ones:
    `task #4 had already finished before the runner stopped; recorded as done. Its
    changes were left as they are, uncommitted, in ...: review and commit them
    (README.md)`. No further task starts in that workspace until those files are
    committed (or you have set them aside yourself). This does not depend on `on_dirty`:
    neither `stash` nor `allow` touches or builds on them. A commit is recorded as the
    task's only if the task made it, or the runner made it for the task; one you made in
    the meantime is not.
- **A process id that was reused.** The lock remembers when its runner process started,
  not only its id. If the operating system has since given that id to another program,
  the lock is still recognised as dead. The same applies to the Claude process of a
  task.
- **An orphaned Claude process.** If the runner died but the Claude process it started is
  still running, nobody is allowed to take over until that process exits, because it may
  still be changing the workspace. Stop it, or use `moa7amed-queue recover --force` if the process
  id now belongs to something unrelated.
- **Half-finished changes after a crash** are treated like any other uncommitted change:
  the next run stops (or stashes them with `-OnDirty stash`). They are never overwritten.
- **Stopping on purpose.** Ctrl+C in the runner stops the running task, sets its changes
  aside, returns the task to the queue without using up an attempt, and releases the lock.
  If the task had just finished when you pressed it, its result counts: it is recorded,
  now or by the next runner, and the task is not run again.
- **A task cut off part-way may already have done something outside the repository**
  (sent a message, called an API, deployed). By default it is run again
  (`on_interrupted=retry`). With `on_interrupted=hold` it is marked failed with the reason
  "held for review" instead, tasks that depend on it wait, and `moa7amed-queue retry N`
  runs it again once you have checked. For tasks that must never run twice, add them
  with `--max-attempts 1` as well.
- **One task at a time, also by mistake.** A runner cannot be given a second task while
  the result of its current one has not been recorded.
- **The database** is SQLite. Every change is one transaction, so a process killed
  mid-write cannot leave a half-written task.

### Seeing and resolving what a crash left behind

```bash
moa7amed-queue reconcile                     # why the queue is blocked, and what each file is
moa7amed-queue reconcile --commit --yes      # commit the files the task changed (only those)
moa7amed-queue reconcile --stash --yes       # set the task's uncommitted files aside
moa7amed-queue reconcile --release --yes     # you dealt with them yourself: let the queue go on
```

`reconcile` reads only, unless you give one of the three options together with `--yes`.
It shows:

- the single reason nothing runs (a runner is working; a finished task is not recorded
  yet; a finished task's files are uncommitted; other uncommitted changes; tasks held
  for review);
- for each file a task left uncommitted, who most likely changed it, judged by when the
  file was last changed: **while the task ran**, **after the task had finished**
  (possibly yours), or **before it started**;
- uncommitted changes that belong to no queued task;
- the commands that resolve each case.

`--commit` commits only the files changed while the task ran, with the task's
`Task-Queue-Id`, and leaves everything else (staged or not) alone. Files whose owner is
unclear stay held until you commit, stash or release them yourself. Nothing is ever
committed without `--yes`, and nothing is resolved while a runner is working.

`moa7amed-queue status` shows a `Held` line while files are held.

These cases were also run against the real Claude Code CLI, not only the test stand-in:
a prompt submitted while a task was running, a runner killed while its task's Claude
process carried on to the end, and a runner killed together with its task
(`tests/live_validation.py`, see [Tests](#tests)).

## Projects and data

A **project** is the git repository root of the directory you are in, so every
subdirectory of a repository shares one queue and one lock. Each task remembers the
subdirectory it was queued from and runs there. Outside a repository the directory itself
is the project. (A repository rooted at your home directory or a drive root is ignored for
this purpose, so a plain folder under a dotfiles repo gets its own queue.)

Nothing is written into your project. Everything lives under `~/.moa7amed-queue`
(override with the `MOA7AMED_QUEUE_HOME` environment variable):

```text
~/.moa7amed-queue/
  config.json                         global settings (optional)
  models.json                         what earlier runs showed about each model
  state.json                          which version last ran here (to notice an update)
  backups/<date>-v<version>[-label]/  backups: settings and every queue
  projects/<name>-<hash>/
    queue.db                          tasks, runs, history, routing data, lock
    config.json                       project settings (optional)
    runner.log                        what the runners did
    runs/task-0007/run-000012.prompt.md   exact prompt of a run
                    run-000012.stdout.json  raw output of claude
                    run-000012.stderr.log
    worktree/                         the dedicated worktree (worktree mode)
```

If you used the plugin before version 0.3.0 your data is in `~/.claude-task-queue`. It is
found and used where it is; see
[Upgrading from claude-task-queue](#upgrading-from-claude-task-queue).

`moa7amed-queue where` prints these paths for the current project and says which data
directory is in use and why; `moa7amed-queue projects` lists all queues.

## Cost

Every figure here is what Claude Code reported for a run (`total_cost_usd` and its token
counts). Nothing is estimated; a run for which Claude Code reported no cost counts as
"not reported", not as zero.

### What the figures are

The cost of a run is the `total_cost_usd` Claude Code reports at the end of it. Claude
Code computes it on your machine from the tokens used and a price table, and documents
it as a client-side estimate, not billing data: **your Anthropic bill is the authority.**
The plugin estimates nothing itself. A run that was killed reports nothing, and is shown
as "not reported", never as zero.

### Limits and warnings

| Setting | What happens | Before or after the money is spent | Exact? |
| :-- | :-- | :-- | :-- |
| `max_budget_usd` | Passed to Claude Code as `--max-budget-usd` for each task. Claude Code stops the task once its estimate reaches the amount | During the task, checked after each model response | **No.** The response that crosses the amount still completes. Measured with 2.1.329: a $0.01 budget stopped at $0.0138 |
| `cost_limit_run_usd` | No further task starts once the session has cost this much (exit code 5). Each task is also given at most what is left as its `--max-budget-usd` | Checked before each task; the remainder is enforced by Claude Code during it | Within one response of the limit, for the reasons above |
| `cost_warn_task_usd` | `COST WARNING` in the runner log when one run cost at least this much | After the run | Warning only |
| `cost_warn_run_usd` | `COST WARNING`, once, when the session reaches this | After the run | Warning only |

A task that Claude Code stopped for its budget is marked failed and **not retried
automatically**: running it again would spend the same again. Its work so far is kept
(set aside in a git stash, as for any failed task). Raise the budget and
`moa7amed-queue retry N`.

The optional review of a routing decision costs about one cent per call and counts
towards the session; switch it off with `routing_classifier off`.

A "session" is one start of a runner. The session limit is checked between tasks: a
running task is never interrupted for it, the remaining tasks stay pending, and starting
the runner again continues with them (a new session, a new allowance). The session total
includes the review calls made for routing decisions. A value of 0 means "not set", as
for `max_budget_usd`.

```bash
moa7amed-queue config set max_budget_usd 2 --global
moa7amed-queue config set cost_warn_task_usd 1 --global
moa7amed-queue config set cost_limit_run_usd 10 --global
```

None of them is set by default.

### What was spent

```bash
moa7amed-queue usage             # this project: per model and per task
moa7amed-queue usage --days 7
```

For example:

```text
Cost of /src/my-app, as reported by Claude Code
  runs: 14 (13 with a reported cost, 1 without)
  tasks: $3.1204    model reviews of routing decisions: $0.0370

  model                               runs       cost    input tok   output tok
  claude-opus-4-8                        2    $1.9020      412,330       18,204
  claude-sonnet-5                        9    $1.1010      988,112       41,950
  claude-haiku-4-5-20251001              3    $0.1174      301,222        4,410
```

### What the plugin itself costs

Measured on the development machine (Windows 11, Python 3.9, Claude Code 2.1.206) on
2026-11-20. `python tests/measure_overhead.py` repeats the time measurements on yours.

| What | 0.3.0 | 0.3.1 | How it was measured |
| :-- | :-- | :-- | :-- |
| Added to the context of every session | about 545 tokens | about 294 tokens | Claude Code's own estimate, `claude plugin details`. It counts all ten skills; by the documentation the five that only you can invoke are not in context at all, so the real figure is lower still |
| Time added to an ordinary prompt by the capture hook | 100 to 135 ms | unchanged (90 to 140 ms) | Median of 15 runs, several times. About 50 ms of it is starting `sh` on this machine |
| The same, once any setting had been saved | 300 to 600 ms | 90 to 120 ms | 0.3.0 started Python for every prompt in that case; 0.3.1 decides in the shell |
| A prompt that is captured and queued | about 870 ms | 580 to 740 ms | |
| `moa7amed-queue status` | 570 to 900 ms | 300 to 450 ms | The classifier's rules are now loaded only by commands that classify |
| Classifying one task by the rules | not measured this way | under 1 ms | 183 prompts in one process; loading the rules once costs about 0.2 s |
| The optional review of a borderline routing decision | about $0.012 per call | unchanged | 23 real calls on Haiku |
| A task session's own context, before your task | 35,300 to 36,200 tokens | unchanged | Two real calls; this is Claude Code and this machine's setup, not the plugin |

The time figures vary by a third or more between runs on this machine; they show the
direction and the size, not a guarantee. The plugin's share of a session's context could
not be observed directly: two identical real sessions differed by about a thousand
tokens for unrelated reasons, more than the whole plugin adds.

### Leaner task sessions

Each task starts a fresh Claude Code session, which loads your instructions, your other
plugins and their hooks and MCP servers before it reads the task. If your tasks do not
need those, Claude Code can leave them out:

```bash
moa7amed-queue config set claude_args --safe-mode             # this project
moa7amed-queue config set claude_args --safe-mode --global    # every project
```

Measured here with one real call each: 28,250 tokens of context instead of about 35,300.
How much it saves depends entirely on what you have installed, and tasks then run
without your `CLAUDE.md`, skills and MCP tools, so use it only where that is what you
want. `--strict-mcp-config` alone made no measurable difference on this machine.

## Settings

```bash
moa7amed-queue config                                  # effective settings and where they come from
moa7amed-queue config set on_dirty stash               # this project
moa7amed-queue config set model sonnet --global        # every project
moa7amed-queue config unset model --global
```

Runner options given on the command line win over project settings, which win over
global settings.

| Setting | Default | Meaning |
| :-- | :-- | :-- |
| `permission_mode` | `acceptEdits` | `claude --permission-mode` for tasks. |
| `model` | (none) | One fixed model for every task. Unset: each task is routed (see [Routing settings](#routing-settings)). |
| `allowed_tools`, `disallowed_tools` | none | `claude --allowedTools` / `--disallowedTools`. |
| `max_budget_usd` | none | `claude --max-budget-usd`, per task. |
| `cost_warn_task_usd`, `cost_warn_run_usd` | none | Log a warning when a task, or a runner session, has cost this much. See [Cost](#cost). |
| `cost_limit_run_usd` | none | Start no further task once a runner session has cost this much. |
| `claude_args` | none | Extra arguments for every `claude` call. |
| `claude_bin` | `claude` | Executable to run (also `MOA7AMED_QUEUE_CLAUDE_BIN`). |
| `timeout_minutes` | `60` | Per-task time limit; `0` disables it. |
| `on_dirty` | `stop` | `stop`, `stash` or `allow`. |
| `on_interrupted` | `retry` | A task cut off by a crash: `retry` runs it again, `hold` waits for you. |
| `workflow_mode` | `auto` | Choose a QUICK, STANDARD or DEEP workflow per task; `off` is the 0.4 behaviour. The other `workflow_*` settings are described in [docs/workflow.md](../../docs/workflow.md#settings). |
| `workflow_enforce_verification` | `true` | Accept a finished task only with an `MQ_VERIFIED` line. |
| `workflow_deep_approval` | `material` | Deep plans that wait for your approval: `material`, `always`, `never`. |
| `challenge_mode` | `explicit` | `adaptive`: rules check each task before it runs and a task may stop once to ask; `explicit`: only reviews you ask for; `off`: none. See [docs/challenge.md](../../docs/challenge.md). |
| `challenge_max_rounds` | `1` | How often one task may be held for a decision. |
| `plan_compliance` | `block` | A plan task that brings in technology the approved plan does not cover: `block` fails it, `warn` only records it. A technology the plan rejected always fails it. |
| `workspace` | `inplace` | `inplace` or `worktree`. |
| `worktree_branch` | `task-queue` | Branch of the dedicated worktree. |
| `commit` | `auto` | `auto` or `none`. |
| `max_attempts` | `2` | Attempts per task (applies to tasks added afterwards). |
| `retry_backoff_seconds` | `30` | Pause before attempt N+1 is N times this. |
| `stale_lock_seconds` | `900` | When a lock held from another machine counts as stale. |
| `capture_prefix` | `queue:` | Prefix captured by the hook; `off` disables capture. Global only. |
| `backup_on_update` | `true` | Back up settings and queues on the first run after the plugin was updated. |

Environment variables:

| Variable | Meaning | Name before 0.3.0 (still honoured) |
| :-- | :-- | :-- |
| `MOA7AMED_QUEUE_HOME` | Data directory. | `CTQ_HOME` |
| `MOA7AMED_QUEUE_PYTHON` | Python interpreter to use. | `CTQ_PYTHON` |
| `MOA7AMED_QUEUE_CLAUDE_BIN` | Claude Code executable. | `CTQ_CLAUDE_BIN` |
| `MOA7AMED_QUEUE_CAPTURE_PREFIX` | Overrides `capture_prefix`. | `CTQ_CAPTURE_PREFIX` |

When both names are set, the new one wins. Tasks started by the runner see
`MOA7AMED_QUEUE_RUNNER`, `MOA7AMED_QUEUE_TASK_ID` and `MOA7AMED_QUEUE_PROJECT` (and the
`CTQ_*` equivalents).

## Command reference

```text
moa7amed-queue add [TEXT...] [--stdin | --prompt-file F] [--acceptance T | --acceptance-file F]
                   [--priority P] [--after IDS] [--max-attempts N] [--title T]
                   [--model opus|sonnet|haiku|NAME] [--phased]
moa7amed-queue status | list [--status S] | show ID | history [ID] | log [ID]
moa7amed-queue priority ID... LEVEL
moa7amed-queue depend ID --on IDS | --remove IDS
moa7amed-queue retry [ID...] [--failed]
moa7amed-queue cancel ID...
moa7amed-queue model auto|opus|sonnet|haiku|NAME [ID...] [--pending] [--whole-project]
moa7amed-queue route [ID...] [--all] [--reclassify] [--phased on|off]
moa7amed-queue models [--probe] [--reset]
moa7amed-queue run [options]                 run the queue in this terminal
moa7amed-queue launch [options] [--hidden]   start a runner in the background
moa7amed-queue plan [options]                show what would run
moa7amed-queue recover [--force]             clear a stale lock, requeue interrupted tasks
moa7amed-queue project new|list|show|discover|apply|env|target|depth|check|approve|execute|status|revise|report ...
                                             project planning (see docs/planning.md)
moa7amed-queue replica intent|tools|new|list|show|config|ref|capture|analyze|tokens|plan|
                       compare|test|improve|sweep|status|report ...
                                             application reconstruction (see docs/replica.md)
moa7amed-queue bid intent|tools|new|list|show|config|doc|text|pages|read|apply|check|decision|
                   estimate|labour|price|approve|generate|review|checklist|queue|revisions|status|company ...
                                             tenders and proposals (see docs/bidstudio.md)
moa7amed-queue agents show [ID] | evaluate ID | plan ID --data JSON | reconcile ID --discard|--retry
                                             multi-agent execution (see docs/agents.md)
moa7amed-queue workflow [ID...] | set ID... --profile quick|standard|deep|auto | approve ID
                                             each task's engineering workflow
moa7amed-queue challenge [list] | show ID|C3 | review ID | raise ID|--plan P --concern T --evidence T |
                         decide [C3|ID...] --recommendation|--original [--said T] [--statement N]
                                             concerns and the owner's decisions (see docs/challenge.md)
moa7amed-queue reconcile [--commit|--stash|--release] [--yes]
                                             why the queue is blocked; resolve a crash's leftovers
moa7amed-queue usage [--days N]              what this project's tasks cost
moa7amed-queue projects | where | config ...
moa7amed-queue install-cli [--no-short]      create the command shims
moa7amed-queue migrate [--dry-run]           move data written before 0.3.0 to the new location
moa7amed-queue update [--refresh]            installed and offered version, and whether updating is safe
moa7amed-queue doctor                        health check of the installation and its data
moa7amed-queue backup [--list] [--label L] [--output DIR]
moa7amed-queue restore NAME [--yes]          bring a backup back
moa7amed-queue changelog [--since V | --all] what changed
```

Every command accepts `--project DIR` and `--json`. `moa7amed-queue <command> --help`
lists its options. `ctq` accepts exactly the same.

Exit codes of a runner (`run`, `run-queue.ps1`, `run-queue.sh`):

| Code | Meaning |
| :-- | :-- |
| 0 | Every task that could run is done |
| 1 | A task failed, or pending tasks are blocked |
| 2 | Another runner holds the project (or a task of a dead runner is still running) |
| 3 | Stopped before a task to protect uncommitted changes, or no permitted model is available |
| 4 | The lock was taken away from this runner |
| 5 | The cost limit of the session was reached (`cost_limit_run_usd`) |
| 130 | Interrupted |

`doctor` exits with 1 when it finds a problem.

## Tests

Run from the plugin folder (`plugins/moa7amed-queue`):

```bash
python -B -m unittest discover -s tests -v
```

The suite needs no network and no Claude account: a fake `claude` executable stands in
for the real one, and every test uses its own temporary data directory, home directory
and throwaway git repositories, so nothing outside the temp directory is read or changed.
Runner tests execute against both the Python runner and, where PowerShell is available,
`run-queue.ps1`.

| File | Covers |
| :-- | :-- |
| `tests/test_queue.py` | Queue integrity: verbatim storage, immutability, ordering, dependencies, cycles, concurrent writers, a writer killed mid-write, project isolation, settings. |
| `tests/test_runner.py` | Sequential execution, commits, retries, failure handling, every uncommitted-change policy, worktree mode, time limits, a second runner being refused, detached launch. |
| `tests/test_recovery.py` | Stale and live locks, a runner killed mid-task, orphaned task processes, leftover changes after a crash, a task that finished before its runner died, reused process ids, a runner asked for two tasks at once. |
| `tests/test_hook.py` | The capture hook, including the shell wrapper Claude Code calls. |
| `tests/test_router.py` | The classifier: a reference corpus of prompts per tier (`tests/routing_corpus.py`), short-but-hard prompts, negation, policies, the bounds on the model review, and the Arabic and mixed-language sets (`tests/routing_corpus_multilingual.py`). |
| `tests/test_routing.py` | Routing end to end: one model per task in queue order, overrides, refused and restricted models, fallback, blocking, silent substitution, escalation limits, phases, running tasks left alone, argument safety, queues from version 0.1. |
| `tests/test_compat.py` | Compatibility with versions published as claude-task-queue: data used where it is, `migrate`, legacy environment variables, the `ctq` alias and shims, legacy result markers. |
| `tests/test_plugin.py` | Manifest, skills, hook wiring, branding, no personal data in the package, the budget for what every session carries, and `claude plugin validate --strict` when the CLI is installed. |
| `tests/test_maintenance.py` | The update report against a simulated installation, the health check, backup and restore, the automatic backup after an update, cost warnings and the session limit with both runners, the usage report. |
| `tests/test_planning.py` | The planning engine: evidence classes, discovery, compatibility in the five reference scenarios (MVC 5 on shared hosting, an external DMS, a small VPS, cloud AI without a GPU, on-premises GPU), unknown hardware, decision matrix ranges, ADR rules, task graphs, approval and frozen revisions, idempotent import, model selection, compliance after a task, revisions, the command-line workflow. |
| `tests/test_reconcile.py` | The `reconcile` report and its three resolutions, file ownership, holding cut-off tasks for review, budget stops and the per-task budget taken from the session limit. |

Tools in the same folder that are not part of the suite:

| File | What it is for |
| :-- | :-- |
| `tests/routing_eval.py` | Prints the classifier's accuracy on every prompt set, per language and topic. |
| `tests/measure_overhead.py` | Measures hook latency, command start-up and classification time on your machine. Calls no model. |
| `tests/upgrade_check.py` | Lets an earlier version of the plugin write a queue, then checks that this version takes it over and that the earlier one can still read it afterwards. Run for every earlier release by the release check at the repository root. |
| `tests/live_validation.py` | The queue driven by the **real** `claude`, on throwaway projects, under a hard spending ceiling. See below. |
| `tests/planning_example.py` | Builds the worked example of the planning guide through the real commands. Calls no model. |
| `tests/agents_live.py` | Multi-agent execution with the **real** `claude`: three read-only workers run concurrently and serially; prints the measured timestamps, overlap, wall time and reported cost (a few cents). |
| `tests/bid_live.py` | BidStudio stages through the queue with the **real** `claude`: the synthetic tender in `tests/fixtures/bid` is read, its requirements recorded with verified quotes, the addendum applied by supersession, and the compliance matrix answered without unsupported claims. |
| `tests/replica_e2e.py` | Replica end to end with the **real** `claude` and a real browser: the sample application in `tests/fixtures/replica_sample` is captured, inventoried, planned, approved, built by the queue, compared, tested in a browser and improved in bounded rounds. See docs/replica.md for the last result and its cost. |
| `tests/live_workflow.py` | The adaptive workflow with the **real** `claude`: the same two tasks with the workflow off and on; checks the profiles, the models and that real sessions report `MQ_VERIFIED`. About $0.25 per full run. |
| `tests/live_routing.py` | Model routing with the **real** `claude`: automatic choice, override, fallback from a refused model, bounded escalation, and one task on Opus. About $0.26 when last run. |

### Live validation

Everything above uses a stand-in for `claude`. This one does not, and it costs money:

```bash
python tests/live_validation.py --budget 1.00 --rehearse    # free: the same scenarios with the stand-in
python tests/live_validation.py --budget 1.00               # the real thing
```

| Scenario | What is checked |
| :-- | :-- |
| `queue` | Task A is running; task B is submitted through the capture hook. B is stored without disturbing A, A finishes and meets its criteria, B starts only afterwards, each in its own session, each committed once |
| `reconcile` | The runner is killed mid-task and the task's Claude process finishes alone. The next runner records the result, does not run the task again, leaves its files untouched and does not start the next task on top of them |
| `interrupt` | The runner and the task's Claude process are both killed. The next runner records the interruption, runs the task again and completes it exactly once |

`--budget` is a ceiling for the whole script; a scenario that the remaining budget does
not cover is reported as not verified instead of being started. Task sessions run with
`claude --safe-mode`, so your other plugins do not record the throwaway sessions.


The marketplace that lists the plugin has its own checks at the repository root
(`python -B -m unittest discover -s tests`). Validate the package with the CLI directly:

```bash
claude plugin validate plugins/moa7amed-queue --strict     # from the repository root
```

## Troubleshooting

**Start here: `moa7amed-queue doctor`.** It checks the installation, the data directory,
every queue, the Claude Code CLI and git, changes nothing, and says what to do about
what it finds. **Nothing runs and you do not know why: `moa7amed-queue reconcile`.**

**"NOT queued again" / "NOT queued: this starts with the text of task #N"**. Claude Code
put the prompt you just queued back into the input box, and it was submitted again,
possibly with more text after it. Nothing was lost or queued twice. Press Esc twice to
clear the input box, then type your next message.

**A task "stopped by Claude Code because the task reached its budget"**. Raise
`max_budget_usd` (or the session limit) and `moa7amed-queue retry N`. It is not retried
by itself.

**A task is "held for review"**. It was cut off by a crash and `on_interrupted` is
`hold`. Check whether it already did part of its work, then `moa7amed-queue retry N`.

**"the workspace has uncommitted changes, so task #N was not started"** (exit code 3).
Working as intended. Commit or stash your changes, or run with `-OnDirty stash`, or use
`-Workspace worktree` to leave the project directory alone.

**"task #N had already finished before the runner stopped; recorded as done. Its changes
were left as they are, uncommitted"**, followed on later runs by **"Task #N finished
before the runner stopped and its changes were never committed"** (exit code 3). A
runner died after its task had completed. The task was not run again; its files are in
your workspace, exactly as it left them. Look at them, commit them, and start the runner
again. The queue waits, whatever `on_dirty` is set to, because committing or stashing
them for you after an unknown amount of time could mix them with something that is not
the task's.

**"the cost limit for one runner session has been reached"** (exit code 5). Nothing was
interrupted and the remaining tasks are still pending. `moa7amed-queue usage` shows what
was spent. Start the runner again to continue, or raise `cost_limit_run_usd`.

**An update does not arrive.** Claude Code updates a marketplace other than its own only
if you turned that on, and only when the plugin's version changed.
`moa7amed-queue update --refresh` shows the installed and the offered version, the
auto-update setting and whether an environment variable switches updates off. See
[Updating](#updating).

**Something is wrong after an update.** `moa7amed-queue doctor`, then
`moa7amed-queue changelog`. Your data was backed up on the first run of the new version
(`moa7amed-queue backup --list`). Going back to the earlier version is described in
[Updating](#updating).

**"another runner is already working on this project"** (exit code 2). `moa7amed-queue status` shows
its process id. If it is really gone, the next run recovers by itself; `moa7amed-queue recover`
forces the check now.

**"…its Claude process (pid N) still appears to be running"**. A previous runner died but
the task it started is still alive. Let it finish or end that process, then run again.

**A task failed with "tool call(s) were denied by the permission mode".** The task needed
a tool that unattended mode may not use. Allow it (`-AllowedTools "Bash(npm test *)"`,
or a permission rule in the project's `.claude/settings.json`) and `moa7amed-queue retry` the task.

**A task is `blocked` with "no usable model".** Every model the task may run on was
refused or is excluded. `moa7amed-queue route ID` shows the reason and `moa7amed-queue models` what is known
about each tier. Fix the access problem or pick a model by hand (`moa7amed-queue model sonnet ID`),
then `moa7amed-queue retry ID` or `moa7amed-queue models --reset`.

**A task got the wrong model.** `moa7amed-queue route ID` shows the reasoning. Override that task
(`moa7amed-queue model opus ID`), change the policy (`moa7amed-queue config set routing_mode quality_first`),
or switch routing off. If a kind of task is misjudged regularly, please open an issue
with the prompt: the reference corpus in `tests/routing_corpus.py` is how the classifier
is calibrated.

**"asked for opus but the CLI ran …".** Claude Code started the session on another
model, usually because settings restrict the requested one. The task's history records
it and later tasks fall back openly. `moa7amed-queue models` lists restrictions found in Claude Code
settings.

**My plan has no Opus (or another tier).** Nothing breaks: the first refusal makes tasks
fall back and is remembered. To avoid even that first request, set
`routing_available_models` or `routing_model_opus none` (see
[When a model is not available](#when-a-model-is-not-available)).

**I do not want the extra review call.** `moa7amed-queue config set routing_classifier off --global`.

**A task is `blocked`.** One of its prerequisites failed or was cancelled. `moa7amed-queue show ID`
names it. Retry the prerequisite, or drop the dependency with `moa7amed-queue depend ID --remove N`.

**`queue:` prompts are sent to Claude instead of being queued.** The hook is not running.
Check that the plugin is enabled (`claude plugin list`), restart Claude Code, and on
Windows make sure Git for Windows is installed (the hook runs under `sh`). `claude --debug hooks`
shows hook activity. `moa7amed-queue add` and the skill work without the hook.

**"Python 3.8 or newer was not found".** Install Python or set `MOA7AMED_QUEUE_PYTHON` to the full
path of an interpreter. If you replaced or removed the interpreter the tool was using,
delete `~/.moa7amed-queue/python-path`.

**"the Claude Code executable 'claude' was not found on PATH".** The runner runs outside
Claude Code and needs `claude` on `PATH`, or `-ClaudeCommand` / `claude_bin` with a full path.

**PowerShell says running scripts is disabled.** Use `runner\run-queue.cmd`, or
`powershell -ExecutionPolicy Bypass -File …\run-queue.ps1`.

**`-AllowedTools` with several tools via `-File`.** Pass one comma-separated string:
`-AllowedTools "Read,Edit,Bash(npm test *)"`.

**Where did my changes go after a failed task?** Into a git stash; the task's error
(`moa7amed-queue show ID`) gives the hash. `git stash list` shows them, `git stash apply <sha>`
restores one.

**See exactly what Claude was told and what it answered.** `moa7amed-queue show ID` prints the paths
of each run's prompt and output files.

## Upgrading from claude-task-queue

Versions 0.1.0 and 0.2.0 of this plugin were called **claude-task-queue**, with the
command `ctq`. Version 0.3.0 is the same plugin under its permanent name. Nothing you
have is lost and nothing is converted.

**1. Replace the plugin.** The plugin and marketplace names changed, so remove the old
entry and add the new one:

```bash
claude plugin uninstall claude-task-queue@claude-task-queue
claude plugin marketplace remove claude-task-queue
claude plugin marketplace add moa7amed/moa7amed-tools
claude plugin install moa7amed-queue@moa7amed-tools
```

Uninstalling the plugin does not touch your queues: they are not stored in the plugin.

**2. Your data keeps working where it is.** Queues, tasks, history, routing decisions and
settings in `~/.claude-task-queue` are found automatically and used in place. The
database format is unchanged. `moa7amed-queue status` shows a `Data` line while the old
location is in use.

**3. Optionally move the data to the new location.**

```bash
moa7amed-queue migrate --dry-run     # shows what would happen, changes nothing
moa7amed-queue migrate               # moves ~/.claude-task-queue to ~/.moa7amed-queue
```

The move is a single rename of the whole directory. It is refused while a runner is
active, it never merges into an existing `~/.moa7amed-queue`, and if it cannot be done
completely it is not done at all. Git worktrees used by the queue are re-registered
automatically. There is no deadline: the old location keeps working.

**4. What changed name, and what still works**

| Before 0.3.0 | Now | Compatibility |
| :-- | :-- | :-- |
| plugin `claude-task-queue` | `moa7amed-queue` | Reinstall once (step 1). |
| marketplace `claude-task-queue` | `moa7amed-tools` | Reinstall once (step 1). |
| `/claude-task-queue:queue-add` and the other skills | `/moa7amed-queue:queue-add` ... | Skill names themselves are unchanged. |
| command `ctq` | `moa7amed-queue` | `ctq` remains an alias, in the plugin and as a shim. Shims created by 0.2.0 keep working. |
| `~/.claude-task-queue` | `~/.moa7amed-queue` | Old directory used in place; `migrate` is optional. |
| `CTQ_HOME`, `CTQ_PYTHON`, `CTQ_CLAUDE_BIN`, `CTQ_CAPTURE_PREFIX` | `MOA7AMED_QUEUE_...` | Old names still honoured. |
| result line `CTQ_RESULT:` | `MQ_RESULT:` | Both are understood. |
| settings (`on_dirty`, `routing_mode`, ...) | unchanged | Unchanged. |
| commit trailer `Task-Queue-Id`, branch `task-queue`, prefix `queue:` | unchanged | Unchanged. |

If you created command shims with `ctq install-cli`, run `moa7amed-queue install-cli`
once to add the new name next to them.

## Updating

Claude Code does the updating. The plugin has no updater of its own; it tells you where
you stand and looks after your data.

```bash
moa7amed-queue update                                 # installed and offered version; is it safe now
claude plugin marketplace update moa7amed-tools
claude plugin update moa7amed-queue@moa7amed-tools
moa7amed-queue doctor                                 # after restarting Claude Code, or /reload-plugins
```

Queues and settings are kept: they live in the data directory, not in the plugin, and
their format has not changed since 0.1.0. On the first run after any update the plugin
copies them to `backups/` before it writes anything.

**Automatic updates** are off for every marketplace except Claude Code's own until you
turn them on: run `/plugin`, open the **Marketplaces** tab, select `moa7amed-tools`,
turn on auto-update. `moa7amed-queue update` shows the setting. A marketplace added from
a local folder is never updated automatically.

**Going back.** Claude Code installs whatever the marketplace offers, so add the
marketplace at the tag of the earlier release:

```bash
claude plugin marketplace remove moa7amed-tools      # also uninstalls the plugin; your data stays
claude plugin marketplace add moa7amed/moa7amed-tools#moa7amed-queue--v0.3.0
claude plugin install moa7amed-queue@moa7amed-tools
```

Because the data format is unchanged, the earlier version reads the queue as it is; this
is tested for every release by running the earlier version against a queue the newer one
has used. To get the queue exactly as it was before the update, restore the backup made
then (see [Backups](#backups)).

Until the marketplace is published, the network parts of this (adding it from GitHub, at
a tag, and the automatic update) are described from the Claude Code documentation and
have not been tried against the hosted repository. The update and the way back were
tried with the real Claude Code commands and a marketplace in a local folder. Details:
[Updates and rollback](../../docs/updates-and-rollback.md).

## Backups

```bash
moa7amed-queue backup                       # settings and every project's queue, to backups/<date>-v<version>
moa7amed-queue backup --label before-cleanup
moa7amed-queue backup --list
moa7amed-queue restore <name>               # shows what would be replaced, changes nothing
moa7amed-queue restore <name> --yes
```

- A backup is a consistent copy even while a queue is in use. It holds the queue
  databases and the settings, not the per-run output files and not your repositories.
- `restore` refuses while a runner is active, and saves the current state first as a
  backup labelled `before-restore`, so a restore can be undone. Each queue comes back
  whole or not at all. Settings come back as they were, which includes removing a
  settings file that did not exist when the backup was made. A project whose queue was
  created after the backup is left as it is.
- After an update, a backup labelled `auto-update-from-<version>` is made by itself on
  the first run of the new version (`backup_on_update`). The five most recent automatic
  backups are kept; backups you make are never removed.
- Backups contain your prompts. They stay on your machine.

## Removing

```bash
claude plugin uninstall moa7amed-queue@moa7amed-tools
claude plugin marketplace remove moa7amed-tools
```

That removes the plugin, its skills and its hook. Your queues stay on disk. To delete
them as well, stop any runner and remove the data directory (and the `PATH` entry, if you
used `install-cli`). If your data is still in the pre-0.3.0 location, the folder is
`.claude-task-queue` instead:

```bash
rm -rf ~/.moa7amed-queue                                   # macOS, Linux, Git Bash
```

```powershell
Remove-Item -Recurse -Force "$env:USERPROFILE\.moa7amed-queue"   # PowerShell
```

If you used worktree mode, remove the worktree and branch from each repository first:
`git worktree prune` and `git branch -D task-queue`.

## Releasing

moa7amed-queue is versioned on its own inside the
[moa7amed-tools](../../README.md) repository. From the repository root:

```bash
python scripts/bump_version.py moa7amed-queue patch          # then write the CHANGELOG.md entry
python -B scripts/release_check.py --worktree --require-cli --tests
claude plugin tag plugins/moa7amed-queue --push              # after the change is merged
```

The version is written in `.claude-plugin/plugin.json`; the marketplace entry carries
none. Claude Code offers an update only when that version changes. The full procedure,
the version rules and what remains before the first public release are in
[Release checklist](../../docs/release-checklist.md) and
[Versioning](../../docs/versioning.md).

To offer the plugin to a whole team automatically, add the marketplace to a repository's
`.claude/settings.json` under `extraKnownMarketplaces` and install the plugin with
`--scope project`.

## Limitations

- **`queue:` during a running turn** works as described in
  [While Claude is in the middle of a turn](#while-claude-is-in-the-middle-of-a-turn)
  with Claude Code 2.1.329 on Windows. It relies on behaviour Claude Code does not
  document, and Claude Code puts a captured prompt back into the input box. Only
  messages with the prefix are queued; ordinary messages go to Claude.
- **The hook needs `sh`.** On Windows without Git for Windows the hook does not run; use
  the skill or `moa7amed-queue add`.
- **Tasks are unattended.** Claude cannot ask you anything, and permission prompts are
  denied rather than shown.
- **In-place mode trusts a clean working tree.** It cannot see an interactive session
  that is about to edit the same directory. If you keep working there, use worktree mode.
- **Automatic commits use `git add -A`** and therefore include every non-ignored file the
  task created. Use `-Commit none` to review first.
- **Crash detection uses process ids and start times** on the same machine and a
  heartbeat across machines. Sharing one data directory between machines is not a tested
  setup.
- **A finished task found after a crash is recorded, not committed.** Its files wait for
  you. That is deliberate (see [Locking and crash recovery](#locking-and-crash-recovery))
  and it means the queue pauses until you have committed them.
- **The cost limit acts between tasks.** One task can take the session past the limit;
  `max_budget_usd` is the limit for a single task. The cost of a run that was killed is
  unknown and counts as not reported.
- **Model routing is a classifier, not an oracle.** It is deterministic and tested
  against a reference corpus, but a prompt can still be misjudged; overrides, policies
  and escalation exist for that. It reads English task descriptions best; for Arabic see
  the measured figures in
  [Arabic and mixed-language requests](#arabic-and-mixed-language-requests). Requests in
  other languages are not recognised by the rules, which then fall back to Sonnet unless
  English technical terms in the request say otherwise.
- **Platforms.** The test suite has been run on Windows 11 with Python 3.8, 3.9 and
  3.14, and on Linux under WSL with Python 3.12. **It has not been run on macOS**; the CI
  workflow includes macOS and will show it once the repository is on GitHub. The
  PowerShell runner is tested on Windows PowerShell 5.1 only.
- **Planning checks, it does not analyse.** The engine enforces evidence classes,
  compatibility with the recorded infrastructure, decision and approval rules, and
  compliance by files and package names; the quality of the analysis is that of the
  session and of the information given. Its technology catalogue is small on purpose.
  Not implemented: critical-path analysis, cost optimisation, multi-agent planning,
  drift detection beyond the post-task check, benchmarking.
- **Ownership of files after a crash is a judgement by modification time.** A file you
  edit while a task runs looks like the task's. That is why `reconcile --commit` needs
  `--yes` and shows the list first.
- **Not published yet.** Installing from GitHub, following a branch or tag, and Claude
  Code's automatic update have not been exercised against a hosted copy of this
  marketplace.
- **Model availability cannot be listed.** The CLI offers no such command, so a model
  counts as available only after a run proved it. The first task that needs a tier you
  cannot use pays one refused request.
- **Verifying the model relies on the CLI's usage report** and on recognising `opus`,
  `sonnet` or `haiku` in the reported id. With providers that use other names the result
  is recorded as "unverified" rather than confirmed.
- **Tasks are never deleted**, only cancelled. Remove a project's folder under
  `~/.moa7amed-queue/projects` to wipe its queue.

## License

[MIT](LICENSE). Copyright (c) 2026 Mohamed Ibrahim Ramadan, published as moa7amed
([moa7amed.com](https://moa7amed.com/)). Free to use, modify and distribute;
no licence key, no payment. Running tasks uses your own Claude account, billed by
Anthropic under your plan. The plugin contains no third-party code: it uses the Python
standard library and the programs on your machine (Claude Code, git).
