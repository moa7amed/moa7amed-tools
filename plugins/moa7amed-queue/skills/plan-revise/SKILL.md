---
name: plan-revise
description: Record a mismatch found during implementation and make the smallest justified plan change.
argument-hint: "<plan id> <what was found>"
disable-model-invocation: true
allowed-tools:
  - Bash(sh "${CLAUDE_PLUGIN_ROOT}/bin/moa7amed-queue" project apply*)
  - Bash(sh "${CLAUDE_PLUGIN_ROOT}/bin/moa7amed-queue" project check*)
  - Bash(sh "${CLAUDE_PLUGIN_ROOT}/bin/moa7amed-queue" project revise*)
  - PowerShell(& "${CLAUDE_PLUGIN_ROOT}/bin/moa7amed-queue.cmd" project apply*)
  - PowerShell(& "${CLAUDE_PLUGIN_ROOT}/bin/moa7amed-queue.cmd" project check*)
  - PowerShell(& "${CLAUDE_PLUGIN_ROOT}/bin/moa7amed-queue.cmd" project revise*)
  - Read
  - Grep
---

# Revise a plan

Requested: $ARGUMENTS

Never rewrite the whole plan. Find the smallest change that resolves the issue, keep
completed work, and let unaffected tasks continue.

1. Record the issue and see what it affects:

   ```bash
   sh "${CLAUDE_PLUGIN_ROOT}/bin/moa7amed-queue" project revise <id> --issue "<what was found, with its evidence>" [--task T5] [--decision ADR-2]
   ```

   It lists the affected tasks (those named, those linked to the decisions named, and
   everything depending on them), which of them are already done (kept), and whether an
   approved decision is impacted.

2. Make the change in the draft:

   - A task detail: edit the task (`project apply`, merged by id).
   - An approved decision: it cannot be edited. Record an amendment, a new decision with
     `"supersedes": "ADR-2"`, its own options, evidence and rationale, and link the
     affected tasks to it. Until the owner approves it, those tasks are held; nothing
     else is.

   ```bash
   sh "${CLAUDE_PLUGIN_ROOT}/bin/moa7amed-queue" project apply <id> --data '{"decisions": [{"id": "ADR-4", "title": "...", "category": "database", "supersedes": "ADR-2", "options": [...], "selected": "...", "rationale": "...", "requirements": ["R3"]}], "tasks": [{"id": "T7", "decisions": ["ADR-4"]}]}'
   sh "${CLAUDE_PLUGIN_ROOT}/bin/moa7amed-queue" project check <id>
   ```

   (Large fragments: write them to a file outside the project and use `--file`.)

3. Present the change to the owner and continue with `/moa7amed-queue:plan-approve <id>`
   and `/moa7amed-queue:plan-execute <id>`. On import, queued tasks the revision changed
   are replaced, tasks that now wait for the amendment are withdrawn from the queue,
   and completed tasks stay with the revision they were done under.

Run `moa7amed-queue` with the Bash tool exactly as shown. Only when there is no Bash tool, use
PowerShell with the same arguments: `& "${CLAUDE_PLUGIN_ROOT}/bin/moa7amed-queue.cmd" <arguments>`.
