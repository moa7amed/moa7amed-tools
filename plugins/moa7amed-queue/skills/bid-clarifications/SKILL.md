---
name: bid-clarifications
description: Keep a tender's clarification register, apply addenda without losing history, and assess bid or no-bid.
argument-hint: "<bid id> [addendum or clarification file]"
disable-model-invocation: true
allowed-tools:
  - Bash(sh "${CLAUDE_PLUGIN_ROOT}/bin/moa7amed-queue" bid decision*)
  - Bash(sh "${CLAUDE_PLUGIN_ROOT}/bin/moa7amed-queue" bid doc*)
  - PowerShell(& "${CLAUDE_PLUGIN_ROOT}/bin/moa7amed-queue.cmd" bid decision*)
  - PowerShell(& "${CLAUDE_PLUGIN_ROOT}/bin/moa7amed-queue.cmd" bid doc*)
  - Read
---

# Clarifications, addenda and bid/no-bid

Requested: $ARGUMENTS

## Clarification register

For every missing, contradictory or commercially significant point:

```json
{"clarifications": [{"id": "Q-01", "requirements": ["REQ-012"],
  "question": "Clause 4.1 states 300 named users; Annex B mentions 500. Which applies?",
  "reason": "conflicting figures", "technical_impact": "sizing of the application tier",
  "pricing_impact": "licence count", "priority": "high", "status": "open"}]}
```

When the client answers, set `"status": "answered"`, `"response"` and
`"updated_assumption"`.

## Addenda and client clarifications

```bash
sh "${CLAUDE_PLUGIN_ROOT}/bin/moa7amed-queue" bid doc <id> <addendum file> --type addendum --amends D1 --date <date>
```

Then, for each changed clause:

1. add the new requirement with `"supersedes": "REQ-0xx"` and the addendum as its source
   (the old one stays, marked superseded: nothing is overwritten);
2. mark requirements you checked and found unchanged with `"checked_against": ["D5"]`;
3. update the compliance entries, the architecture if affected, the estimate
   (`bid estimate <id>`), the pricing, and the proposal sections;
4. `bid revisions <id>` shows the history.

## Bid / no-bid

Rate each factor 1 (very weak) to 5 (strong), with the evidence, or leave it out if it
is unknown:

```json
{"assessment": {"factors": {"technical_capability": {"rating": 4, "evidence": "C-REF-VMS-2024"},
  "certifications": {"rating": 1, "evidence": "no ISO 27001 item in the profile"}},
  "conditions": ["obtain ISO 27001 through partner X before the deadline"]}}
```

Factors: technical_capability, delivery_feasibility, partner_capabilities,
certifications, licensing, timeline, financial_exposure, contractual_risk, resources,
support_obligations, competitiveness.

```bash
sh "${CLAUDE_PLUGIN_ROOT}/bin/moa7amed-queue" bid decision <id>
```

The result (BID, BID_WITH_CONDITIONS, NO_BID, INSUFFICIENT_INFORMATION) is a
recommendation; the owner decides. Never fill in a credential the company does not have.
