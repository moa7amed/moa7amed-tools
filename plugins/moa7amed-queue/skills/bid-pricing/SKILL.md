---
name: bid-pricing
description: Build a tender's commercial model (costs, margin, contingency, FX, tax) with the pricing engine.
argument-hint: "<bid id>"
disable-model-invocation: true
allowed-tools:
  - Bash(sh "${CLAUDE_PLUGIN_ROOT}/bin/moa7amed-queue" bid apply*)
  - Bash(sh "${CLAUDE_PLUGIN_ROOT}/bin/moa7amed-queue" bid company*)
  - Bash(sh "${CLAUDE_PLUGIN_ROOT}/bin/moa7amed-queue" bid price*)
  - PowerShell(& "${CLAUDE_PLUGIN_ROOT}/bin/moa7amed-queue.cmd" bid apply*)
  - PowerShell(& "${CLAUDE_PLUGIN_ROOT}/bin/moa7amed-queue.cmd" bid company*)
  - PowerShell(& "${CLAUDE_PLUGIN_ROOT}/bin/moa7amed-queue.cmd" bid price*)
  - Read
---

# Pricing model

Bid: $ARGUMENTS

**You do no arithmetic.** You record inputs; the engine computes every figure.

## Inputs

```json
{"pricing": {"currency": "SAR", "tax_rate": "0.15", "contingency": "0.05", "discount": "0",
  "minimum_margin": "0.15",
  "fx": {"USD": {"rate": "3.75", "status": "ASSUMPTION", "source": "planning rate 2026-11"}},
  "pricing_rules": {"default": {"markup": "0.20"}, "hardware": {"margin": "0.12"}},
  "items": [
   {"id": "HW-01", "category": "hardware", "description": "Badge printer, thermal, QR capable",
    "customer_description": "Visitor badge printer", "quantity": 4, "unit": "each",
    "unit_cost": "610", "currency": "USD", "source": {"type": "quotation", "ref": "D4 Q-7781"},
    "taxable": true},
   {"id": "LIC-01", "category": "license", "description": "...", "quantity": 1, "unit": "lot",
    "unit_cost": null, "source": {"type": "pending", "ref": "awaiting vendor quote"}}]}}
```

- `source.type`: **quotation** (a document of this bid: cite it), **rate_card** (the
  company's rate card), **estimate** (a planning estimate: labelled as such everywhere),
  **pending** (no figure yet). Never invent a vendor price.
- Labour: after `bid estimate`, `bid labour <id>` adds a line per role at the company's
  rate card (roles without a rate stay pending). The rate card and the company's pricing
  policy: `sh "${CLAUDE_PLUGIN_ROOT}/bin/moa7amed-queue" bid company ratecard <company>`.
- `markup`: selling = cost x (1 + markup). `margin`: selling = cost / (1 - margin).
  They are different; use the one the company's pricing policy names.
- Contingency is a cost provision built into the selling prices; customers never see it
  as a line.

```bash
sh "${CLAUDE_PLUGIN_ROOT}/bin/moa7amed-queue" bid apply <id> --data '<one line of JSON>'
sh "${CLAUDE_PLUGIN_ROOT}/bin/moa7amed-queue" bid price <id> --scenarios
```

Report the status (complete, estimated, incomplete), pending lines, planning estimates,
assumed exchange rates, the margin and the sensitivity to exchange rates, effort and
licence costs. Never reach a target price by dropping mandatory scope.

Final selling prices need the owner's approval: `bid approve <id> pricing` shows what
would be approved; the owner confirms with `--yes`. Any change afterwards voids it.
