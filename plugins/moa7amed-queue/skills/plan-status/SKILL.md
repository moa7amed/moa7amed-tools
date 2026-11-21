---
name: plan-status
description: Show a project plan's stage, milestones, tasks, blockers and compliance findings.
argument-hint: "[plan id]"
allowed-tools:
  - Bash(sh "${CLAUDE_PLUGIN_ROOT}/bin/moa7amed-queue" *)
  - PowerShell(& "${CLAUDE_PLUGIN_ROOT}/bin/moa7amed-queue.cmd" *)
---

# Plan status

Requested: $ARGUMENTS

1. Without a plan id, list the plans: `sh "${CLAUDE_PLUGIN_ROOT}/bin/moa7amed-queue" project list`.
   One plan: use it. Several: ask which.
2. Show it:

   ```bash
   sh "${CLAUDE_PLUGIN_ROOT}/bin/moa7amed-queue" project status <id>
   sh "${CLAUDE_PLUGIN_ROOT}/bin/moa7amed-queue" project check <id>
   ```

3. Report briefly: the stage (analysis, architecture proposed, architecture approved, plan
   ready, approved for execution), milestone progress, tasks that are held and the
   decision they wait for, failed or blocked tasks with their reason, compliance findings,
   and the single next step. Task states come from the queue itself.

Read-only: change nothing. Run `moa7amed-queue` with the Bash tool exactly as shown. Only
when there is no Bash tool, use PowerShell with the same arguments:
`& "${CLAUDE_PLUGIN_ROOT}/bin/moa7amed-queue.cmd" <arguments>`.
