---
name: auto
description: Turn on, pause or check Auto Queue for this project.
argument-hint: "[status|enable|pause|resume|stop|disable|blockers|budget]"
allowed-tools:
  - Bash(sh "${CLAUDE_PLUGIN_ROOT}/bin/moa7amed-queue" *)
  - PowerShell(& "${CLAUDE_PLUGIN_ROOT}/bin/moa7amed-queue.cmd" *)
---

# Auto Queue

With Auto Queue on, a task saved in this project (a `queue:` prompt,
`/moa7amed-queue:queue-add`, or `moa7amed-queue add`) starts a runner in the background when
none is working. The runner executes eligible tasks one at a time and exits after a while
without work. It is off until the owner turns it on for this project.

Request: $ARGUMENTS

Run the command with the Bash tool exactly as shown (PowerShell only when there is no Bash
tool: `& "${CLAUDE_PLUGIN_ROOT}/bin/moa7amed-queue.cmd" auto ...`). Do not start or execute
any task yourself.

- **No argument or `status`:** `sh "${CLAUDE_PLUGIN_ROOT}/bin/moa7amed-queue" auto status`.
  Report the state, the runner and any blocker in a few lines.
- **`enable`:** first run `sh "${CLAUDE_PLUGIN_ROOT}/bin/moa7amed-queue" auto enable` (add
  `--worktree`, `--budget USD` or `--no-budget` if the user asked). It changes nothing and
  prints what Auto Queue means for this project. Show that text, then ask the user to
  confirm. Only after an explicit yes, run the same command with `--yes`. If this session
  has uncommitted work in the project, mention `--worktree` (tasks then run in a separate
  git worktree, so they do not wait for a clean folder).
- **`pause`, `resume`, `disable`, `blockers`:** `... auto <action>`. Pause lets the running
  task finish and starts no new one; disable stops automatic starting and keeps every task.
- **`stop`:** `... auto stop` ends the runner after its current task. Only if the user
  explicitly asks to stop the running task immediately: explain that the task is cut off,
  held for review and automatic execution paused, and after their confirmation run
  `... auto stop --now --yes`.
- **`budget`:** `... auto budget` shows the allowance for automatic runs; `--renew` counts
  it again from now, `--set USD` changes it. Change it only when the user asks.

Ordinary messages are never queued by Auto Queue: only `queue:` prompts and explicit
queue-add requests are. Claude Code puts a captured `queue:` prompt back into the input box;
the user should press Esc twice before typing the next message.
