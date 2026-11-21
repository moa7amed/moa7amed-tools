---
name: queue-status
description: Show this project's task queue - pending, running, failed and finished tasks, the runner, or one task's details and history.
argument-hint: "[task id | history | all]"
allowed-tools:
  - Bash(sh "${CLAUDE_PLUGIN_ROOT}/bin/moa7amed-queue" *)
  - PowerShell(& "${CLAUDE_PLUGIN_ROOT}/bin/moa7amed-queue.cmd" *)
---

# Task queue status

Requested: $ARGUMENTS

Pick the command that answers the request and run it:

| Request | Command |
| :-- | :-- |
| nothing, or a general "what is queued?" | `sh "${CLAUDE_PLUGIN_ROOT}/bin/moa7amed-queue" status` |
| a task id (for example `7`) | `sh "${CLAUDE_PLUGIN_ROOT}/bin/moa7amed-queue" show 7` |
| `all` / every task including finished ones | `sh "${CLAUDE_PLUGIN_ROOT}/bin/moa7amed-queue" list` |
| `history`, or "what happened?" | `sh "${CLAUDE_PLUGIN_ROOT}/bin/moa7amed-queue" history` (add a task id to narrow it) |
| the runner's own log | `sh "${CLAUDE_PLUGIN_ROOT}/bin/moa7amed-queue" log` |
| the result text of a finished task | `sh "${CLAUDE_PLUGIN_ROOT}/bin/moa7amed-queue" log 7` |
| queues of other projects | `sh "${CLAUDE_PLUGIN_ROOT}/bin/moa7amed-queue" projects` |
| which model each task uses and why | `sh "${CLAUDE_PLUGIN_ROOT}/bin/moa7amed-queue" route` (add task ids to narrow it) |
| which models can be used on this machine | `sh "${CLAUDE_PLUGIN_ROOT}/bin/moa7amed-queue" models` |

Then summarise for the user, briefly:

- what is running now and what comes next;
- the model of each task when it matters. The MODEL column shows the tier chosen for the
  task: `*` marks a manual override, `a>b` a task moved from one model to another by a
  fallback or an escalation, `!` a task blocked because no permitted model is available,
  `-` a task not classified yet. `route` gives the reason, the model that actually ran,
  the escalation history and the usage the CLI reported;
- anything that needs their attention: **failed** tasks (with the reason), **blocked**
  tasks (which prerequisite failed or was cancelled), a **stale** runner lock;
- the useful next step: `/moa7amed-queue:queue-run` to start the runner,
  `/moa7amed-queue:queue-retry` for failures, `/moa7amed-queue:queue-priority` to
  reorder, `/moa7amed-queue:model-opus`, `model-sonnet`, `model-haiku` or `model-auto`
  to change a task's model.

This skill only reads the queue. Do not start, change or work on any queued task.

Run `moa7amed-queue` with the Bash tool exactly as shown. Only when there is no Bash tool, use
PowerShell with the same arguments: `& "${CLAUDE_PLUGIN_ROOT}/bin/moa7amed-queue.cmd" <arguments>`.
