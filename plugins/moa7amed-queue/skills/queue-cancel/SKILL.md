---
name: queue-cancel
description: Cancel queued tasks that have not run yet.
argument-hint: "<task ids>"
disable-model-invocation: true
allowed-tools:
  - Bash(sh "${CLAUDE_PLUGIN_ROOT}/bin/moa7amed-queue" *)
  - PowerShell(& "${CLAUDE_PLUGIN_ROOT}/bin/moa7amed-queue.cmd" *)
---

# Cancel queued tasks

Requested: $ARGUMENTS

1. If no task ids were given, show the queue and ask which tasks to cancel:

   ```bash
   sh "${CLAUDE_PLUGIN_ROOT}/bin/moa7amed-queue" status
   ```

2. Cancel them:

   ```bash
   sh "${CLAUDE_PLUGIN_ROOT}/bin/moa7amed-queue" cancel 4 7
   ```

3. Report what was cancelled and which tasks are now blocked because they depended on
   a cancelled task (the command lists them). Those can be cancelled too, or released
   with `sh "${CLAUDE_PLUGIN_ROOT}/bin/moa7amed-queue" depend <id> --remove <cancelled id>`.

Cancelled tasks are never deleted: the original prompt stays in the history and
`/moa7amed-queue:queue-retry` puts a cancelled task back in the queue. A task that is
running cannot be cancelled; stop the runner first (close its window or press Ctrl+C in
it), which returns the task to the queue.

Run `moa7amed-queue` with the Bash tool exactly as shown. Only when there is no Bash tool, use
PowerShell with the same arguments: `& "${CLAUDE_PLUGIN_ROOT}/bin/moa7amed-queue.cmd" <arguments>`.
