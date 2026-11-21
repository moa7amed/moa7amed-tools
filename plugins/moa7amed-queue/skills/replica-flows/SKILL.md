---
name: replica-flows
description: Model the user flows of a replica project - steps, inputs, validation, outcomes, gaps and test cases.
argument-hint: "<replica id>"
disable-model-invocation: true
allowed-tools:
  - Bash(sh "${CLAUDE_PLUGIN_ROOT}/bin/moa7amed-queue" *)
  - PowerShell(& "${CLAUDE_PLUGIN_ROOT}/bin/moa7amed-queue.cmd" *)
  - Read
---

# Replica user flows

Project: $ARGUMENTS

From the screen inventory (`replica show <id>`) and the references, describe how a user
moves through the application, for example Login -> Dashboard -> Search -> Details ->
Edit -> Save.

```json
{"flows": [{"id": "U1", "name": "Find and open a shipment", "evidence": "OBSERVED",
  "refs": ["E2", "E4"], "entry": "S1",
  "steps": [{"screen": "S1", "action": "choose status 'In transit' and apply"},
            {"screen": "S1", "action": "open the first row"},
            {"screen": "S2", "action": "details are shown"}],
  "inputs": ["status"], "validation": ["date range: end not before start (seen as an error text)"],
  "outcome": "the details screen of the chosen shipment",
  "errors": ["no rows: empty state"], "permissions": "unknown",
  "backend": ["list shipments filtered by status", "get shipment by id"],
  "tests": ["filter, open, details visible"], "gaps": ["what happens on a server error"]}]}
```

```bash
sh "${CLAUDE_PLUGIN_ROOT}/bin/moa7amed-queue" replica analyze <id> --data '<one line of JSON>'
```

Rules:

- Flow ids are U1, U2.... Each step names its screen when it has one.
- **Never invent a successful outcome.** A flow you could not follow (behind a login,
  not documented) is INFERRED or UNKNOWN, lists its gaps, and claims no outcome
  (`outcome_claimed` stays unset). The engine rejects a claimed outcome on such a flow.
- Inputs and test cases use synthetic data, never values copied from the reference.
- `backend` lists what the flow needs from a server; that feeds the backend design.

Report the flows, which are settled by evidence, and the gaps.
