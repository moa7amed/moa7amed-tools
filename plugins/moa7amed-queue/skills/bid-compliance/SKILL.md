---
name: bid-compliance
description: Build a tender's compliance matrix - each requirement answered with evidence, disqualifying conditions apart.
argument-hint: "<bid id>"
disable-model-invocation: true
allowed-tools:
  - Bash(sh "${CLAUDE_PLUGIN_ROOT}/bin/moa7amed-queue" *)
  - PowerShell(& "${CLAUDE_PLUGIN_ROOT}/bin/moa7amed-queue.cmd" *)
  - Read
---

# Compliance matrix

Bid: $ARGUMENTS

For every requirement (`bid show <id> --section requirements`), record how the proposal
meets it:

```json
{"compliance": [{"id": "REQ-007", "status": "COMPLIANT",
  "response": "Visitors are registered on a single screen capturing name, organisation, host and purpose; the host is chosen from the Active Directory list.",
  "evidence": ["SOLUTION:registration-module"], "section": "8. Functional Scope",
  "owner": "solution architect", "risk": "low", "claim": "proposed"}]}
```

| Status | When |
| :-- | :-- |
| COMPLIANT | the proposed solution meets it, and you can say how |
| PARTIALLY_COMPLIANT | some of it; say what is missing |
| NON_COMPLIANT | it is not met |
| PENDING_VERIFICATION | depends on evidence or a decision not yet available |
| NOT_APPLICABLE | it does not apply to this bid; say why |

- **A response that repeats the requirement is not an answer**; the engine rejects it.
  Say how it is met.
- `evidence`: company profile items (`C-...`, approved, with their evidence), solution
  components (`SOLUTION:...`), or documents (`D2 p.3`). Something the company has done
  or holds (certificates, references, staff, products) needs a `C-` item; without one it
  is PENDING_VERIFICATION, never COMPLIANT.
- `claim`: `proposed` (what the bid will deliver) or `existing_capability` (what the
  company already has: needs a `C-` item).
- Disqualifying conditions (mandatory items not met or still open) are listed by
  `bid status <id>`; report them first.

```bash
sh "${CLAUDE_PLUGIN_ROOT}/bin/moa7amed-queue" bid apply <id> --data '<one line of JSON>'
sh "${CLAUDE_PLUGIN_ROOT}/bin/moa7amed-queue" bid check <id>
```

Commitments that are not plainly met by the solution (unsupported compliance, an SLA
beyond the standard offering) need the owner's approval:
`bid approve <id> compliance_commitments` (the owner adds `--yes`).
