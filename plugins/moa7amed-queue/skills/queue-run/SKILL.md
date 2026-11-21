---
name: queue-run
description: Start the runner that executes this project's queued tasks one at a time.
argument-hint: "[--worktree] [--max-tasks N] [--watch] [--on-dirty stop|stash|allow] [--routing-mode POLICY] [--model NAME] [--permission-mode MODE] [--allowed-tools TOOLS] [--dry-run]"
disable-model-invocation: true
allowed-tools:
  - Bash(sh "${CLAUDE_PLUGIN_ROOT}/bin/moa7amed-queue" launch*)
  - Bash(sh "${CLAUDE_PLUGIN_ROOT}/bin/moa7amed-queue" plan*)
  - PowerShell(& "${CLAUDE_PLUGIN_ROOT}/bin/moa7amed-queue.cmd" launch*)
  - PowerShell(& "${CLAUDE_PLUGIN_ROOT}/bin/moa7amed-queue.cmd" plan*)
---

# Run the task queue

Start the queue runner for this project as a separate background process. The runner,
not this session, executes the tasks: it starts one fresh `claude -p` session per task,
strictly one at a time, and commits each task's changes.

Options from the user: $ARGUMENTS

## Steps

1. Translate the options. `--worktree` means `--workspace worktree`. Everything else is
   passed through unchanged (`--max-tasks N`, `--watch`, `--on-dirty stop|stash|allow`,
   `--commit auto|none`, `--permission-mode MODE`, `--allowed-tools "A,B"`,
   `--max-budget-usd N`, `--timeout-minutes N`, `--stop-on-failure`,
   `--routing-mode balanced|quality_first|cost_optimized`, `--no-routing`, `--model NAME`).
   Do not add `--model` yourself: without it every task runs on the model the queue chose
   for it, which is what the user normally wants. `--model NAME` forces one model for
   every task of this run.

2. Show what would happen, without starting anything:

   ```bash
   sh "${CLAUDE_PLUGIN_ROOT}/bin/moa7amed-queue" plan <options>
   ```

   If nothing is runnable, say why (empty queue, tasks blocked by a failed prerequisite,
   a runner already active) and stop here. If the user passed `--dry-run`, stop here too.

3. Start the runner:

   ```bash
   sh "${CLAUDE_PLUGIN_ROOT}/bin/moa7amed-queue" launch <options>
   ```

   On Windows this opens the PowerShell runner in its own window; elsewhere it runs
   detached in the background.

4. Report the runner's process id and log file, and that `/moa7amed-queue:queue-status`
   shows progress. Do not wait for the runner and do not poll it.

## Things to tell the user when they apply

- The default mode runs tasks **in the project directory** and refuses to start while it
  has uncommitted changes, so that nobody's work is overwritten. If this session has
  uncommitted work, or the user wants to keep working here while the queue runs, suggest
  `--worktree`: tasks then run in a dedicated git worktree on the `task-queue` branch, and
  the results are merged later with `git merge task-queue`.
- Tasks run unattended with `--permission-mode acceptEdits` by default: file edits are
  allowed, other tools (for example shell commands) only if the project's permission rules
  or `--allowed-tools` allow them. A task that needs more will report failure and say so.
- Only one runner can work on a project at a time. A second start is refused.
- Each task starts on its own model (the plan shows it). If a model turns out to be
  unavailable the runner falls back to the nearest permitted one and records why; a task
  that repeatedly fails can be moved up one tier once. Neither changes Claude Code's
  default model. `/moa7amed-queue:queue-status` shows what actually ran.

Run `moa7amed-queue` with the Bash tool exactly as shown. Only when there is no Bash tool, use
PowerShell with the same arguments: `& "${CLAUDE_PLUGIN_ROOT}/bin/moa7amed-queue.cmd" <arguments>`.
