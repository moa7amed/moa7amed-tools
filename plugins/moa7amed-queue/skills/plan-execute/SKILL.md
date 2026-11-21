---
name: plan-execute
description: Import an approved plan revision's tasks into the queue, in order, without duplicates.
argument-hint: "<plan id>"
disable-model-invocation: true
allowed-tools:
  - Bash(sh "${CLAUDE_PLUGIN_ROOT}/bin/moa7amed-queue" project execute*)
  - PowerShell(& "${CLAUDE_PLUGIN_ROOT}/bin/moa7amed-queue.cmd" project execute*)
---

# Execute an approved plan

Requested: $ARGUMENTS

The existing task queue executes the plan: one task at a time, with its locks, crash
recovery, commits and model routing. There is no second engine.

1. Preview the import (changes nothing):

   ```bash
   sh "${CLAUDE_PLUGIN_ROOT}/bin/moa7amed-queue" project execute <id>
   ```

   It uses the newest revision approved for execution. It lists tasks to add, tasks
   already queued (unchanged, not added again), tasks whose queued copy will be replaced
   because the approved plan changed them, completed tasks that are kept, and tasks held
   because a decision they depend on is not approved.

2. Tell the owner what will be queued and what stays held, then import:

   ```bash
   sh "${CLAUDE_PLUGIN_ROOT}/bin/moa7amed-queue" project execute <id> --yes
   ```

3. The queue runs them like any other task. Starting the runner is the owner's choice:
   suggest `/moa7amed-queue:queue-run`. Do not start it yourself.

What happens while it runs: each task carries the approved architecture in its prompt.
After a task commits, its change is checked against the plan; a change that adds a
technology the approved decisions do not cover (a container setup, another database, a
cache...) fails the task with the reason, keeps the commit, and holds its dependents
(`plan_compliance warn` only records it). Progress: `/moa7amed-queue:plan-status <id>`.
Importing again is safe at any time.

Run `moa7amed-queue` with the Bash tool exactly as shown. Only when there is no Bash tool, use
PowerShell with the same arguments: `& "${CLAUDE_PLUGIN_ROOT}/bin/moa7amed-queue.cmd" <arguments>`.
