---
name: replica-status
description: Show a replica project's stage, evidence, scores and feature parity.
argument-hint: "[replica id]"
allowed-tools:
  - Bash(sh "${CLAUDE_PLUGIN_ROOT}/bin/moa7amed-queue" replica status*)
  - PowerShell(& "${CLAUDE_PLUGIN_ROOT}/bin/moa7amed-queue.cmd" replica status*)
---

# Replica status

Requested: $ARGUMENTS

```bash
sh "${CLAUDE_PLUGIN_ROOT}/bin/moa7amed-queue" replica status [<id>]
```

Without an id and with several projects, list them and ask which. Report briefly: the
stage (references, reconnaissance, planning, approval, building, verification), the
evidence counts and unknowns, queue progress, per-screen scores with the method (pixel
comparison), the feature verdicts (verified only means a real test passed), the
improvement rounds, the limitations, and the single next step.

Read-only: change nothing. Run `moa7amed-queue` with the Bash tool exactly as shown. Only
when there is no Bash tool, use PowerShell with the same arguments:
`& "${CLAUDE_PLUGIN_ROOT}/bin/moa7amed-queue.cmd" <arguments>`.
