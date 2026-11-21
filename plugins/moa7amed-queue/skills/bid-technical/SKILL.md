---
name: bid-technical
description: Design a tender's solution against its real hosting constraints and write the technical proposal sections.
argument-hint: "<bid id>"
disable-model-invocation: true
allowed-tools:
  - Bash(sh "${CLAUDE_PLUGIN_ROOT}/bin/moa7amed-queue" bid generate*)
  - PowerShell(& "${CLAUDE_PLUGIN_ROOT}/bin/moa7amed-queue.cmd" bid generate*)
  - Read
---

# Solution and technical proposal

Bid: $ARGUMENTS

## 1. Solution, through the planning engine

The bid has a linked project plan of the same id. Follow
`${CLAUDE_PLUGIN_ROOT}/skills/infrastructure-assess/SKILL.md` and
`${CLAUDE_PLUGIN_ROOT}/skills/architecture-review/SKILL.md` for plan `<id>`: users and
concurrency, workload, data volume and growth, integrations, availability, DR, security,
hosting, CPU, RAM, storage, network, GPU only where needed, licensing, maintenance; each
value with its evidence class; unknown hardware as labelled scenarios. Do not choose
microservices, Kubernetes, cloud or AI because they are popular. Capacity figures are
estimates, never guaranteed performance. A material architecture needs the owner's
approval: `bid approve <id> architecture`.

Record the solution summary in the bid:

```json
{"solution": {"summary": "...", "components": [{"id": "registration-module", "description": "..."}],
  "infrastructure": "...", "bom": [{"item": "Application server VM", "spec": "8 vCPU, 32 GB", "qty": 2}],
  "plan": "<id>"}}
```

## 2. The technical proposal

Use the tender's mandated structure, order, templates and terminology when it gives one
(record it as `"required_sections"`); otherwise the usual order: Executive Summary,
Understanding of Requirements, Company Profile, Relevant Experience, Proposed Solution,
Solution Architecture, Functional Scope, Technical Specifications, Infrastructure and
Hardware, Security and Integration, Implementation Methodology, Project Governance,
Project Team, Implementation Schedule, Testing and Acceptance, Training and Knowledge
Transfer, Warranty and Support, Compliance Matrix, Assumptions and Dependencies, Risks
and Mitigations, Required Appendices.

```json
{"technical": {"required_sections": ["Executive Summary", "Compliance Matrix"],
  "sections": [
   {"title": "Executive Summary", "content": "Paragraphs separated by blank lines.\n\n- bullets like this"},
   {"title": "Implementation Schedule", "content": [{"p": "..."}, {"table": {"header": ["Phase", "Weeks"], "rows": [["Design", "3"]]}}]},
   {"title": "Compliance Matrix", "auto": "compliance"},
   {"title": "Risks and Mitigations", "auto": "risks"},
   {"title": "Assumptions and Dependencies", "auto": "assumptions"}],
  "assumptions": ["The Authority provides the Active Directory service account."]}}
```

- Company profile, experience, team, certifications: only from approved profile items
  (`bid company show <company>`). Missing: say "to be provided" and list it; never invent.
- No prices, rates or currency amounts anywhere in the technical proposal.
- Respect page limits; tables where they help.

Send one section per call. Then generate a draft and read it:

```bash
sh "${CLAUDE_PLUGIN_ROOT}/bin/moa7amed-queue" bid generate <id> --kind technical --kind compliance
```
