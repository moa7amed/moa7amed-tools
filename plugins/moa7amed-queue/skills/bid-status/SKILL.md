---
name: bid-status
description: Show a bid's documents, requirements, compliance, pricing, approvals and outputs.
argument-hint: "[bid id]"
allowed-tools:
  - Bash(sh "${CLAUDE_PLUGIN_ROOT}/bin/moa7amed-queue" *)
  - PowerShell(& "${CLAUDE_PLUGIN_ROOT}/bin/moa7amed-queue.cmd" *)
---

# Bid status

Requested: $ARGUMENTS

```bash
sh "${CLAUDE_PLUGIN_ROOT}/bin/moa7amed-queue" bid status [<id>]
sh "${CLAUDE_PLUGIN_ROOT}/bin/moa7amed-queue" bid checklist <id>
```

Without an id and with several bids, list them and ask which. Report briefly: the tender
and deadline, documents not fully read, requirements and compliance counts, disqualifying
conditions, open clarifications, the recommendation, pricing status (pending lines and
estimates, never internal costs or margins unless the owner asks), approvals, generated
documents and their problems, and the single next step.

Read-only: change nothing. Run `moa7amed-queue` with the Bash tool exactly as shown. Only
when there is no Bash tool, use PowerShell with the same arguments:
`& "${CLAUDE_PLUGIN_ROOT}/bin/moa7amed-queue.cmd" <arguments>`.
