---
name: queue-retry
description: Put failed or cancelled queue tasks back in line for another attempt.
argument-hint: "[task ids | --failed] [--max-attempts N]"
allowed-tools:
  - Bash(sh "${CLAUDE_PLUGIN_ROOT}/bin/moa7amed-queue" list*)
  - Bash(sh "${CLAUDE_PLUGIN_ROOT}/bin/moa7amed-queue" retry*)
  - PowerShell(& "${CLAUDE_PLUGIN_ROOT}/bin/moa7amed-queue.cmd" list*)
  - PowerShell(& "${CLAUDE_PLUGIN_ROOT}/bin/moa7amed-queue.cmd" retry*)
---

# Retry queued tasks

Requested: $ARGUMENTS

1. If no task ids were given and the user did not say "all failed", first list the
   candidates and their errors, then ask which ones to retry:

   ```bash
   sh "${CLAUDE_PLUGIN_ROOT}/bin/moa7amed-queue" list --status failed,cancelled
   ```

2. Requeue them. Each task gets a fresh set of attempts; its original prompt and
   acceptance criteria are unchanged.

   ```bash
   sh "${CLAUDE_PLUGIN_ROOT}/bin/moa7amed-queue" retry 4 7          # specific tasks
   sh "${CLAUDE_PLUGIN_ROOT}/bin/moa7amed-queue" retry --failed     # every failed task
   ```

   Add `--max-attempts N` to change how many attempts they get.

3. Report which tasks are pending again and which tasks were waiting on them (they are
   unblocked automatically). Remind the user that `/moa7amed-queue:queue-run` starts
   the runner; retrying does not start anything by itself.

Before retrying a task that failed for a reason a rerun will not fix (a tool was denied,
a prerequisite is missing, the request cannot be done as written), say so. A denied tool
needs `--allowed-tools` or another `--permission-mode` on the next run. Changes left by
a failed attempt were saved in a git stash named in the task's error; `moa7amed-queue show <id>`
prints it.

Run `moa7amed-queue` with the Bash tool exactly as shown. Only when there is no Bash tool, use
PowerShell with the same arguments: `& "${CLAUDE_PLUGIN_ROOT}/bin/moa7amed-queue.cmd" <arguments>`.
