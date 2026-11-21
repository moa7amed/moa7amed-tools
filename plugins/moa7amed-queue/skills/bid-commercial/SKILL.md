---
name: bid-commercial
description: Write a tender's customer-facing commercial proposal from the approved pricing model.
argument-hint: "<bid id>"
disable-model-invocation: true
allowed-tools:
  - Bash(sh "${CLAUDE_PLUGIN_ROOT}/bin/moa7amed-queue" bid apply*)
  - Bash(sh "${CLAUDE_PLUGIN_ROOT}/bin/moa7amed-queue" bid generate*)
  - PowerShell(& "${CLAUDE_PLUGIN_ROOT}/bin/moa7amed-queue.cmd" bid apply*)
  - PowerShell(& "${CLAUDE_PLUGIN_ROOT}/bin/moa7amed-queue.cmd" bid generate*)
  - Read
---

# Commercial proposal

Bid: $ARGUMENTS

The prices, the BOQ, the totals and the payment amounts come from the pricing engine; you
write the texts around them, following the tender's required format:

```json
{"commercial": {"cover_letter": "Paragraphs...", "date": "2026-12-10",
  "payment_milestones": [{"name": "Design approval", "percent": 30}, {"name": "Acceptance", "percent": 50},
                         {"name": "End of warranty", "percent": 20}],
  "assumptions": ["Prices exclude VAT unless stated."], "exclusions": ["Civil works"],
  "validity": "This proposal is valid for 90 days from the submission date.",
  "notes": ["Payment terms as per clause 7.2 of the RFP."], "exceptions": []}}
```

- Milestone shares must add up to 100%; the engine computes the amounts.
- Never write a figure into these texts: amounts come from the engine.
- Contractual exceptions (`exceptions`) need the owner's approval:
  `bid approve <id> contract_exceptions`.
- Customer documents never show cost, markup, margin, contingency or supplier prices;
  the generator stops them (BLOCKER).

```bash
sh "${CLAUDE_PLUGIN_ROOT}/bin/moa7amed-queue" bid apply <id> --data '<one line of JSON>'
sh "${CLAUDE_PLUGIN_ROOT}/bin/moa7amed-queue" bid generate <id> --kind commercial --kind boq --kind pricing
```

Drafts carry a "DRAFT" header until the prices are approved and the release gate is given.
