---
name: bid-estimation
description: Estimate a tender's work as a work breakdown with three-point effort per role, basis and confidence.
argument-hint: "<bid id>"
disable-model-invocation: true
allowed-tools:
  - Bash(sh "${CLAUDE_PLUGIN_ROOT}/bin/moa7amed-queue" *)
  - PowerShell(& "${CLAUDE_PLUGIN_ROOT}/bin/moa7amed-queue.cmd" *)
  - Read
---

# Effort estimate

Bid: $ARGUMENTS

Break the work into packages (requirements and analysis, solution design, UI/UX,
backend, database, integration, migration, testing, security verification, deployment,
documentation, training, project management, warranty and support) and give each role's
effort in person-days as optimistic, likely and pessimistic:

```json
{"wbs": {"packages": [
  {"id": "WP1", "name": "Requirements and analysis", "deliverable": "approved requirements specification",
   "roles": [{"role": "business analyst", "optimistic": 8, "likely": 10, "pessimistic": 15}],
   "depends_on": [], "complexity": "medium", "basis": "heuristic", "confidence": "medium",
   "assumptions": "two workshops with the client", "team": 1}]}}
```

- `basis`: historical (the company's own past projects; name them), benchmark (a
  published source; name it) or heuristic (expert judgement). Say which; never present
  a heuristic as measured.
- Use the role names of the company's rate card (`bid company ratecard <company>` shows
  them), so labour can be priced.
- `team`: people working on the package at once (for the schedule).

```bash
sh "${CLAUDE_PLUGIN_ROOT}/bin/moa7amed-queue" bid apply <id> --data '<one line of JSON>'
sh "${CLAUDE_PLUGIN_ROOT}/bin/moa7amed-queue" bid estimate <id>
```

The engine computes the PERT expectation (o + 4m + p) / 6 per role, the optimistic,
baseline and conservative totals, and a schedule from the dependencies. Compare the
schedule with the tender's delivery period; a schedule that does not fit is a risk to
report, not something to hide by cutting scope.
