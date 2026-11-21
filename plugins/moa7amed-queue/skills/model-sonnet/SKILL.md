---
name: model-sonnet
description: Run queued tasks on Claude Sonnet instead of the automatically chosen model.
argument-hint: "<task ids | --pending | --whole-project>"
disable-model-invocation: true
allowed-tools:
  - Bash(sh "${CLAUDE_PLUGIN_ROOT}/bin/moa7amed-queue" model*)
  - Bash(sh "${CLAUDE_PLUGIN_ROOT}/bin/moa7amed-queue" status*)
  - PowerShell(& "${CLAUDE_PLUGIN_ROOT}/bin/moa7amed-queue.cmd" model*)
  - PowerShell(& "${CLAUDE_PLUGIN_ROOT}/bin/moa7amed-queue.cmd" status*)
---

# Run queued tasks on Claude Sonnet

Requested: $ARGUMENTS

The task queue normally chooses a model for each task by itself. This command overrides
that choice for the tasks named here, and for nothing else.

1. Work out the scope from the request:

   | Request | Command |
   | :-- | :-- |
   | task ids, for example `4 7` | `sh "${CLAUDE_PLUGIN_ROOT}/bin/moa7amed-queue" model sonnet 4 7` |
   | every pending task | `sh "${CLAUDE_PLUGIN_ROOT}/bin/moa7amed-queue" model sonnet --pending` |
   | every task of this project, now and later | `sh "${CLAUDE_PLUGIN_ROOT}/bin/moa7amed-queue" model sonnet --whole-project` |

   If the request names no scope, show the queue with
   `sh "${CLAUDE_PLUGIN_ROOT}/bin/moa7amed-queue" status` and ask which tasks are meant. Do not guess.

2. Run the command.

3. Report briefly: which tasks now use Claude Sonnet, and that this is a manual override
   stored with those tasks. Claude Code's own default model and all other tasks are
   unchanged. `/moa7amed-queue:model-auto` returns tasks to automatic selection.

Things to mention when they apply:

- Sonnet is the balanced tier for ordinary feature work, tests and routine fixes. Moving a task the router sent to Opus down to Sonnet saves cost but may not be enough for it; say so if the task's routing reason was a critical one (`moa7amed-queue route <id>` shows it).
- A task that is **running** keeps its model; the command refuses to change it.
- A manual choice is never replaced silently. If Claude Sonnet turns out to be unavailable
  when the task starts, the task is marked blocked with the reason instead of running on
  another model.
- Tasks with a manual override are not escalated automatically.

Run `moa7amed-queue` with the Bash tool exactly as shown. Only when there is no Bash tool, use
PowerShell with the same arguments: `& "${CLAUDE_PLUGIN_ROOT}/bin/moa7amed-queue.cmd" <arguments>`.
