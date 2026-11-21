---
name: bid-review
description: Independently review a tender response package - compliance, claims, timeline, prices, envelopes, forms.
argument-hint: "<bid id>"
disable-model-invocation: true
allowed-tools:
  - Bash(sh "${CLAUDE_PLUGIN_ROOT}/bin/moa7amed-queue" *)
  - PowerShell(& "${CLAUDE_PLUGIN_ROOT}/bin/moa7amed-queue.cmd" *)
  - Read
---

# Package review

Bid: $ARGUMENTS

## 1. Generate and check everything

```bash
sh "${CLAUDE_PLUGIN_ROOT}/bin/moa7amed-queue" bid generate <id>
sh "${CLAUDE_PLUGIN_ROOT}/bin/moa7amed-queue" bid review <id>
```

The engine finds verified defects: requirements not traced to their source, mandatory
items unanswered or open, answers that repeat the requirement, claims without approved
company evidence, expired certificates, schedule beyond the delivery period, pending and
estimated prices, assumed exchange rates, BOQ not reconciling with the pricing model,
prices in the technical envelope, internal figures in customer documents, missing
required sections, forms and signatures, missing approvals.

## 2. Read as an evaluator would

Read the generated documents and the tender side by side:
`bid outputs <id>` lists them (with the PDF page counts in `--text technical/pdf`), and
`bid outputs <id> --text technical` (commercial, boq, compliance, summary...) prints a
document's text.
Record what you find that the engine cannot know (unclear answers, contradictions between
sections, weak responses to heavily weighted criteria, legal or contractual red flags) as
advisory findings:

```json
{"advisory": [{"id": "A-01", "severity": "MEDIUM", "code": "weak-answer", "where": "REQ-014",
  "message": "The training answer gives no schedule or class sizes."}]}
```

Severities: BLOCKER, HIGH, MEDIUM, LOW. Advisory findings are labelled as such; never
present them as verified, and never state that the package is legally or contractually
compliant: that needs the owner's specialists.

## 3. Fix, regenerate, report

Fix what can be fixed through the stages, regenerate, and report the submission
checklist:

```bash
sh "${CLAUDE_PLUGIN_ROOT}/bin/moa7amed-queue" bid checklist <id>
```

Release is the owner's decision (`bid approve <id> release --yes`, then `bid generate
<id> --final`). BidStudio never submits.
