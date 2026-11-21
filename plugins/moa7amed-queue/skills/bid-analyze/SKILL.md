---
name: bid-analyze
description: Read a tender package and record its facts and requirements, each traced to its document and page.
argument-hint: "<bid id>"
disable-model-invocation: true
allowed-tools:
  - Bash(sh "${CLAUDE_PLUGIN_ROOT}/bin/moa7amed-queue" *)
  - PowerShell(& "${CLAUDE_PLUGIN_ROOT}/bin/moa7amed-queue.cmd" *)
  - Read
---

# Tender intake and requirements

Bid: $ARGUMENTS

## 1. Read everything

```bash
sh "${CLAUDE_PLUGIN_ROOT}/bin/moa7amed-queue" bid doc <id>
sh "${CLAUDE_PLUGIN_ROOT}/bin/moa7amed-queue" bid text <id> <doc>
```

Read the extracted text of every document completely. A document marked "partly read"
or "not read" has pages without text: render them and read them as images, then record
what they say (never skip a page silently):

```bash
sh "${CLAUDE_PLUGIN_ROOT}/bin/moa7amed-queue" bid pages <id> <doc>
sh "${CLAUDE_PLUGIN_ROOT}/bin/moa7amed-queue" bid read <id> <doc> <page> --data "<the page's text>"
```

## 2. Record the tender facts

```bash
sh "${CLAUDE_PLUGIN_ROOT}/bin/moa7amed-queue" bid apply <id> --data '{"tender": {"title": "...", "reference": "...", "client": "...", "deadline": "2026-12-15", "envelopes": "separate technical and commercial", "page_limit": "40 pages", "evaluation": "70/30", "currency": "SAR", "validity_days": 90, "source": "D1 1.1-1.3, 8.1"}, "deadline": "2026-12-15", "delivery_days": 120, "language": "en"}'
```

`language` is the language of the response documents (`ar` writes right-to-left).

## 3. Record every requirement, in the tender's own words

```json
{"requirements": [{"id": "REQ-001", "class": "EXPLICIT_REQUIREMENT", "category": "eligibility",
  "mandatory": true, "text": "The bidder must hold a valid ISO/IEC 27001 certificate.",
  "source": {"doc": "D1", "section": "2.2", "page": 2}}]}
```

- `text` is copied from the document (the engine checks it is there; skip words with
  `...`). Never paraphrase into a different obligation.
- Categories: submission, eligibility, certification, registration, scope, functional,
  non_functional, security, infrastructure, integration, delivery, training, warranty,
  sla, support, payment, performance, penalty, contractual, evaluation, local_content,
  forms, commercial, technical, other.
- Classes: EXPLICIT_REQUIREMENT (the tender says it), VERIFIED_SUPPORTING_EVIDENCE,
  PROPOSED_SOLUTION, ASSUMPTION, UNKNOWN, CONFLICTING (two sources disagree: record both).
- Required forms and declarations also go into `forms`:
  `{"forms": [{"name": "Form A Bidder Declaration", "required": true, "signature": true, "status": "missing"}]}`.
- An addendum that changes a clause: add the new requirement with `"supersedes": "REQ-0xx"`
  and the addendum as its source. Never edit the old one.

Send one section per call (`--data '<one line>'`, or `--file` in the system temp folder
for a long list). Fix every BLOCKER the command reports, then `bid check <id>`.

## 4. Report (analyze mode)

Summary, scope, mandatory requirements, eligibility checklist, risks, missing
information, and continue with the compliance and clarification stages.
