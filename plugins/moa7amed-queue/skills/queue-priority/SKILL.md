---
name: queue-priority
description: Change the priority or prerequisites of queued tasks to control what runs first.
argument-hint: "<task ids> <low|normal|high|urgent|integer>"
allowed-tools:
  - Bash(sh "${CLAUDE_PLUGIN_ROOT}/bin/moa7amed-queue" depend*)
  - Bash(sh "${CLAUDE_PLUGIN_ROOT}/bin/moa7amed-queue" priority*)
  - Bash(sh "${CLAUDE_PLUGIN_ROOT}/bin/moa7amed-queue" status*)
  - PowerShell(& "${CLAUDE_PLUGIN_ROOT}/bin/moa7amed-queue.cmd" depend*)
  - PowerShell(& "${CLAUDE_PLUGIN_ROOT}/bin/moa7amed-queue.cmd" priority*)
  - PowerShell(& "${CLAUDE_PLUGIN_ROOT}/bin/moa7amed-queue.cmd" status*)
---

# Change what runs first

Requested: $ARGUMENTS

The runner always starts the runnable task with the highest priority; tasks with equal
priority run in the order they were added. Named levels: `low` (-10), `normal` (0),
`high` (10), `urgent` (20). Any integer works; higher runs first.

- Set a priority (several ids are fine):

  ```bash
  sh "${CLAUDE_PLUGIN_ROOT}/bin/moa7amed-queue" priority 5 high
  sh "${CLAUDE_PLUGIN_ROOT}/bin/moa7amed-queue" priority 5,6,9 low
  ```

- "Run task 9 next" means giving it a priority above every other pending task; check
  the current values with `sh "${CLAUDE_PLUGIN_ROOT}/bin/moa7amed-queue" status` and use `urgent`
  or a larger integer.

- Make a task wait for others, or stop waiting:

  ```bash
  sh "${CLAUDE_PLUGIN_ROOT}/bin/moa7amed-queue" depend 9 --on 4,5
  sh "${CLAUDE_PLUGIN_ROOT}/bin/moa7amed-queue" depend 9 --remove 4
  ```

  A prerequisite always wins over priority: a task never starts before the tasks it
  depends on are done. Dependency cycles are rejected.

Afterwards show the new order (the command prints "Next up") in one line. Priorities
of finished or cancelled tasks cannot be changed, and a running task is not interrupted.

Run `moa7amed-queue` with the Bash tool exactly as shown. Only when there is no Bash tool, use
PowerShell with the same arguments: `& "${CLAUDE_PLUGIN_ROOT}/bin/moa7amed-queue.cmd" <arguments>`.
