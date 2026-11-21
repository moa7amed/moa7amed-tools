---
name: model-auto
description: Set the model of queued tasks (opus, sonnet, haiku) or back to automatic, and explain which model a task uses.
argument-hint: "<task ids | --pending | --whole-project>"
allowed-tools:
  - Bash(sh "${CLAUDE_PLUGIN_ROOT}/bin/moa7amed-queue" config set*)
  - Bash(sh "${CLAUDE_PLUGIN_ROOT}/bin/moa7amed-queue" model*)
  - Bash(sh "${CLAUDE_PLUGIN_ROOT}/bin/moa7amed-queue" route*)
  - Bash(sh "${CLAUDE_PLUGIN_ROOT}/bin/moa7amed-queue" status*)
  - PowerShell(& "${CLAUDE_PLUGIN_ROOT}/bin/moa7amed-queue.cmd" config set*)
  - PowerShell(& "${CLAUDE_PLUGIN_ROOT}/bin/moa7amed-queue.cmd" model*)
  - PowerShell(& "${CLAUDE_PLUGIN_ROOT}/bin/moa7amed-queue.cmd" route*)
  - PowerShell(& "${CLAUDE_PLUGIN_ROOT}/bin/moa7amed-queue.cmd" status*)
---

# Let the queue choose the model again

Requested: $ARGUMENTS

Removes a manual model override, so the task queue's router chooses the model again.

If the request is instead to run tasks on a particular model, use the same command with
`opus`, `sonnet` or `haiku` in place of `auto`, for example
`sh "${CLAUDE_PLUGIN_ROOT}/bin/moa7amed-queue" model opus 4 7`. That is a manual override
stored with those tasks: it is never replaced silently (if the model is unavailable the
task is blocked with the reason), and such tasks are not escalated automatically. Say so
when reporting.

1. Work out the scope from the request:

   | Request | Command |
   | :-- | :-- |
   | task ids, for example `4 7` | `sh "${CLAUDE_PLUGIN_ROOT}/bin/moa7amed-queue" model auto 4 7` |
   | every pending task | `sh "${CLAUDE_PLUGIN_ROOT}/bin/moa7amed-queue" model auto --pending` |
   | the project-wide model set with `--whole-project` | `sh "${CLAUDE_PLUGIN_ROOT}/bin/moa7amed-queue" model auto --whole-project` |

   If the request names no scope, show the queue with
   `sh "${CLAUDE_PLUGIN_ROOT}/bin/moa7amed-queue" status` (a `*` after a model marks a manual
   override) and ask which tasks are meant.

2. Run the command. It classifies the tasks again under the current routing policy and
   prints the model each one now gets.

3. Report the model and the one-line reason for each task. For the full reasoning of a
   task, including its complexity score and history, use
   `sh "${CLAUDE_PLUGIN_ROOT}/bin/moa7amed-queue" route <id>`.

How the automatic choice works, in one paragraph for the user if they ask: each task is
scored on complexity, reasoning depth, affected modules, expected changes, architecture,
database and security involvement, testing needs, ambiguity and regression risk. Simple
documentation and text changes go to Haiku, ordinary development to Sonnet, and
architecture, hard debugging, migrations, security design and high-risk changes to Opus.
A short prompt is never assumed to be an easy one. The policy can be `balanced`
(default), `quality_first` or `cost_optimized`:
`sh "${CLAUDE_PLUGIN_ROOT}/bin/moa7amed-queue" config set routing_mode quality_first`.

A task that is running keeps its model; the command refuses to change it.

Run `moa7amed-queue` with the Bash tool exactly as shown. Only when there is no Bash tool, use
PowerShell with the same arguments: `& "${CLAUDE_PLUGIN_ROOT}/bin/moa7amed-queue.cmd" <arguments>`.
