---
name: project-plan
description: "Build a verifiable plan for a project or major change: requirements, infrastructure, decisions, tasks."
argument-hint: "<plan id> [what to build or change, or paths to SRS/RFP documents]"
disable-model-invocation: true
allowed-tools:
  - Bash(sh "${CLAUDE_PLUGIN_ROOT}/bin/moa7amed-queue" project apply*)
  - Bash(sh "${CLAUDE_PLUGIN_ROOT}/bin/moa7amed-queue" project check*)
  - Bash(sh "${CLAUDE_PLUGIN_ROOT}/bin/moa7amed-queue" project depth*)
  - Bash(sh "${CLAUDE_PLUGIN_ROOT}/bin/moa7amed-queue" project discover*)
  - Bash(sh "${CLAUDE_PLUGIN_ROOT}/bin/moa7amed-queue" project list*)
  - Bash(sh "${CLAUDE_PLUGIN_ROOT}/bin/moa7amed-queue" project new*)
  - Bash(sh "${CLAUDE_PLUGIN_ROOT}/bin/moa7amed-queue" project report*)
  - PowerShell(& "${CLAUDE_PLUGIN_ROOT}/bin/moa7amed-queue.cmd" project apply*)
  - PowerShell(& "${CLAUDE_PLUGIN_ROOT}/bin/moa7amed-queue.cmd" project check*)
  - PowerShell(& "${CLAUDE_PLUGIN_ROOT}/bin/moa7amed-queue.cmd" project depth*)
  - PowerShell(& "${CLAUDE_PLUGIN_ROOT}/bin/moa7amed-queue.cmd" project discover*)
  - PowerShell(& "${CLAUDE_PLUGIN_ROOT}/bin/moa7amed-queue.cmd" project list*)
  - PowerShell(& "${CLAUDE_PLUGIN_ROOT}/bin/moa7amed-queue.cmd" project new*)
  - PowerShell(& "${CLAUDE_PLUGIN_ROOT}/bin/moa7amed-queue.cmd" project report*)
  - Read
  - Grep
  - Glob
---

# Plan a project

Requested: $ARGUMENTS

You are building a plan that someone will approve and that the task queue will execute.
Its value is that every statement in it can be traced to evidence. **Never invent a
requirement, a hardware figure or a technology choice to make the plan look complete.**
Where something is not known, record it as UNKNOWN and ask.

## 0. Decide whether this needs a plan at all

A small, well-defined change (a bug fix, a form field, a report) does not: queue it with
`/moa7amed-queue:queue-add` and stop. Plan when it is a new system, a major feature, an
infrastructure or database change, or several dependent stages.

## 1. Create or open the plan

```bash
sh "${CLAUDE_PLUGIN_ROOT}/bin/moa7amed-queue" project list
sh "${CLAUDE_PLUGIN_ROOT}/bin/moa7amed-queue" project new <id> --title "<title>" --mode new|existing --summary "<one paragraph>"
```

For an **existing project**, record what its files show (this reads only the project
folder, runs nothing, and keeps the developer's machine apart from the deployment target):

```bash
sh "${CLAUDE_PLUGIN_ROOT}/bin/moa7amed-queue" project discover <id>
```

Then read the code and documents yourself (Read, Grep, Glob) to understand the
architecture, conventions, integrations and tests. Preserve the existing architecture
unless there is evidence it cannot meet a requirement; a small feature never justifies a
rewrite.

For a **new project**, read every document the owner gave you (SRS, RFP, proposal, user
stories, diagrams, constraints, budget).

## 2. Record facts and requirements with their evidence class

| Class | Use it when |
| :-- | :-- |
| VERIFIED | Source code, a document or a measurement shows it. Name the evidence (file, section) |
| USER_CONFIRMED | The owner stated it |
| ASSUMED | Reasonable, not confirmed. It must be confirmed before a decision rests on it |
| UNKNOWN | Still needed |
| CONFLICTING | Two sources disagree. Record both and ask |

A file name or a configuration file is evidence about the repository, not about
production. Requirement kinds: functional, non_functional, security, performance,
availability, scalability, compliance, integration, operational, deployment, budget.
Tag data-location restrictions with `"tags": ["data-residency"]`.

How to send JSON to the plan: put the fragment on one line in `--data '...'` (keep each
call small: one section at a time). For a large fragment, write it to a file outside the
project (the system temp folder) and pass `--file <path>`; never write it inside the
project, which would leave the workspace dirty. A multi-line here-document is not covered
by this skill's permissions.

Entries are merged by id:

```bash
sh "${CLAUDE_PLUGIN_ROOT}/bin/moa7amed-queue" project apply <id> --data '{"requirements": [...], "facts": [...]}'
```

A fragment looks like this (shown on several lines for reading):

```json
{"requirements": [
   {"id": "R1", "kind": "functional", "text": "Staff can register incoming correspondence",
    "priority": "must", "status": "VERIFIED", "evidence": ["SRS 3.2.1"]},
   {"id": "R2", "kind": "performance", "text": "200 concurrent users",
    "priority": "must", "status": "USER_CONFIRMED", "evidence": ["owner, 2026-11-20"]}],
 "facts": [
   {"id": "F1", "topic": "users.peak", "statement": "Peak concurrency during month end",
    "status": "UNKNOWN"}],
 "open_questions": ["Which hosting provider and plan is used today?"]}
```

## 3. Infrastructure, architecture, tasks

Unless the owner asked you to stop after the requirements, continue yourself (the next
two skills are started by the owner, so read their instructions and follow them):

- Infrastructure: follow `${CLAUDE_PLUGIN_ROOT}/skills/infrastructure-assess/SKILL.md`. It
  is mandatory for new architecture and for infrastructure changes. No technology is
  chosen before it. Record only what is stated or documented; ask for the rest.
- Architecture: follow `${CLAUDE_PLUGIN_ROOT}/skills/architecture-review/SKILL.md`. When a
  decision needs information that is missing, record it as NEEDS_INFORMATION and say
  what is missing instead of choosing.
- Tasks: break the selected architecture into milestones and tasks (format below).
  Tasks that depend on an undecided decision link to it: they will wait for it, and
  nothing else will.

```json
{"milestones": [{"id": "M1", "title": "Correspondence register", "goal": "..."}],
 "tasks": [{"id": "T1", "title": "Create the Correspondence table and EF migration",
   "description": "...", "milestone": "M1", "requirements": ["R1"], "decisions": ["ADR-1"],
   "depends_on": [], "priority": "high", "kind": "implementation",
   "effort": {"estimate": 4, "unit": "hours", "uncertainty": "medium"},
   "files": ["Data/Migrations"], "components": ["sqlserver"], "model": "auto",
   "acceptance": ["The migration applies to an empty database", "..."],
   "tests": ["Repository tests for insert and query"], "risk": "low",
   "done": ["Code reviewed", "Tests pass"]}]}
```

Tasks must be executable and verifiable ("Create the Correspondence table and its
migration", not "Implement backend"). Prefer a dozen meaningful tasks to a hundred tiny
ones. `kind` is implementation, infrastructure, analysis, documentation, test or
migration. Mark tasks with effects outside the repository (sending, deploying, calling a
paid API) with `"side_effects": true`: they get a single attempt. `model` is `auto` unless
there is a reason; the router chooses per task.

## 4. Check and report

```bash
sh "${CLAUDE_PLUGIN_ROOT}/bin/moa7amed-queue" project check <id>
sh "${CLAUDE_PLUGIN_ROOT}/bin/moa7amed-queue" project depth <id> auto
sh "${CLAUDE_PLUGIN_ROOT}/bin/moa7amed-queue" project report <id>
```

Fix every error `check` reports. Tell the owner, plainly: what is verified, what is
assumed, what is still unknown, which decisions wait for their approval, and how to
approve (`/moa7amed-queue:plan-approve <id>`). If the planning depth is `deep` and this
session is not on the strongest model, say so: the owner may want `/model opus` for the
architecture review.

Run `moa7amed-queue` with the Bash tool exactly as shown. Only when there is no Bash tool, use
PowerShell with the same arguments: `& "${CLAUDE_PLUGIN_ROOT}/bin/moa7amed-queue.cmd" <arguments>`.
