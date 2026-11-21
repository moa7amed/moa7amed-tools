---
name: replica-build
description: Build an approved replica plan through the task queue, step by step, with git checkpoints.
argument-hint: "<replica id>"
disable-model-invocation: true
allowed-tools:
  - Bash(sh "${CLAUDE_PLUGIN_ROOT}/bin/moa7amed-queue" launch*)
  - Bash(sh "${CLAUDE_PLUGIN_ROOT}/bin/moa7amed-queue" project execute*)
  - Bash(sh "${CLAUDE_PLUGIN_ROOT}/bin/moa7amed-queue" project status*)
  - Bash(sh "${CLAUDE_PLUGIN_ROOT}/bin/moa7amed-queue" replica status*)
  - PowerShell(& "${CLAUDE_PLUGIN_ROOT}/bin/moa7amed-queue.cmd" launch*)
  - PowerShell(& "${CLAUDE_PLUGIN_ROOT}/bin/moa7amed-queue.cmd" project execute*)
  - PowerShell(& "${CLAUDE_PLUGIN_ROOT}/bin/moa7amed-queue.cmd" project status*)
  - PowerShell(& "${CLAUDE_PLUGIN_ROOT}/bin/moa7amed-queue.cmd" replica status*)
---

# Replica build

Project: $ARGUMENTS

1. Check the state:

   ```bash
   sh "${CLAUDE_PLUGIN_ROOT}/bin/moa7amed-queue" replica status <id>
   sh "${CLAUDE_PLUGIN_ROOT}/bin/moa7amed-queue" project status <id>
   ```

   The plan must be approved for execution. If it is not, stop and say so.

2. Look at the target folder (`replica status` shows it) before anything is written.
   If it holds work that is not committed, tell the owner; the queue does not start on
   top of uncommitted changes unless they allow it. For a long build while the owner keeps
   working, suggest worktree mode (`moa7amed-queue config set workspace worktree`).

3. Import the approved tasks into the queue (idempotent; nothing is added twice):

   ```bash
   sh "${CLAUDE_PLUGIN_ROOT}/bin/moa7amed-queue" project execute <id> --yes
   ```

4. Start the runner in the background:

   ```bash
   sh "${CLAUDE_PLUGIN_ROOT}/bin/moa7amed-queue" launch --hidden
   ```

Each task runs in its own Claude Code session on the model the router chooses, follows
its workflow (quick, standard or deep), must report how it verified its work, and is
committed on its own. Do not build anything in this session.

Report what was imported, what is held and why, and how to follow progress
(`/moa7amed-queue:replica-status <id>`).
