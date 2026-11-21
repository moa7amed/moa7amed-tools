---
name: replica-backend
description: Design a replica project's data model and backend from source, schemas, API specs and observed behaviour.
argument-hint: "<replica id>"
disable-model-invocation: true
allowed-tools:
  - Bash(sh "${CLAUDE_PLUGIN_ROOT}/bin/moa7amed-queue" *)
  - PowerShell(& "${CLAUDE_PLUGIN_ROOT}/bin/moa7amed-queue.cmd" *)
  - Read
  - Grep
  - Glob
---

# Replica backend

Project: $ARGUMENTS

Only for functional and full mode. Use, in this order of weight: approved source code,
supplied database schemas, API specifications, the owner's documentation and
confirmations, then observed behaviour.

Separate four things and never blur them:

| Category | Meaning |
| :-- | :-- |
| reconstructed | behaviour shown by source, schema or specification (DOCUMENTED) |
| equivalent | newly designed behaviour that produces what was OBSERVED |
| mock | placeholder behaviour, labelled as a mock in code and in the result |
| not implemented | known, out of scope or blocked by missing evidence |

Never infer an exact database schema from screenshots, never present a placeholder API as
a production integration, and never call a private API or production database of the
reference.

Write the design into the features (`backend: true`, `behavior`, `category`) and into the
linked plan:

- domain model, relationships and validation rules: as plan requirements and facts with
  their evidence (`moa7amed-queue project apply <id> --data ...`)
- persistence, authentication and authorization choices: as architecture decisions,
  through the planning engine (they need the owner's approval)
- API contracts and test fixtures: in the task descriptions the plan generates

```bash
sh "${CLAUDE_PLUGIN_ROOT}/bin/moa7amed-queue" replica analyze <id> --data '{"features": [{"id": "F1", "backend": true, "category": "equivalent", "behavior": "..."}]}'
sh "${CLAUDE_PLUGIN_ROOT}/bin/moa7amed-queue" project apply <id> --data '{"requirements": [...]}'
```
