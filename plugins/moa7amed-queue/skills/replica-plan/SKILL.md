---
name: replica-plan
description: Turn a replica project's inventory into an architecture and an approvable task plan.
argument-hint: "<replica id>"
disable-model-invocation: true
allowed-tools:
  - Bash(sh "${CLAUDE_PLUGIN_ROOT}/bin/moa7amed-queue" project check*)
  - Bash(sh "${CLAUDE_PLUGIN_ROOT}/bin/moa7amed-queue" replica plan*)
  - PowerShell(& "${CLAUDE_PLUGIN_ROOT}/bin/moa7amed-queue.cmd" project check*)
  - PowerShell(& "${CLAUDE_PLUGIN_ROOT}/bin/moa7amed-queue.cmd" replica plan*)
  - Read
---

# Replica plan

Project: $ARGUMENTS

The replica project has a linked project plan of the same id. Architecture, hosting and
approval are the planning engine's; this step adds the reconstruction tasks.

## 1. Infrastructure and architecture first

Do not choose technology from the reference's appearance. Follow
`${CLAUDE_PLUGIN_ROOT}/skills/infrastructure-assess/SKILL.md` and then
`${CLAUDE_PLUGIN_ROOT}/skills/architecture-review/SKILL.md` for plan `<id>`: target
hosting, OS, CPU, RAM, storage, database, network, users and concurrency, availability,
security, licensing, budget and maintenance, each with its evidence class; then options
compared against that target and recorded as decisions.

Examples of proportion: Windows shared hosting with ASP.NET MVC support suggests a .NET
MVC rebuild; a small static site needs no backend; a modest VPS does not need
microservices or Kubernetes; AI features need a decision between local inference and an
external API, and no GPU is assumed.

## 2. Generate the tasks

```bash
sh "${CLAUDE_PLUGIN_ROOT}/bin/moa7amed-queue" replica plan <id>
sh "${CLAUDE_PLUGIN_ROOT}/bin/moa7amed-queue" project check <id>
```

`replica plan` writes grouped tasks (foundation, a few screens per task, flows, backend,
tests) into the plan, each with only the inventory entries it needs, its acceptance
criteria and tests. It refuses while the inventory has evidence errors. Running it again
after the inventory changes replaces the generated tasks.

Link tasks to the approved decisions they implement (`project apply <id> --data
'{"tasks": [{"id": "T2", "decisions": ["ADR-1"], "components": ["aspnet-mvc"]}]}'`) so
the post-task compliance check knows the boundaries.

## 3. Hand over for approval

Report the architecture, the tasks, the open questions and the unknowns, and tell the
owner to approve with `/moa7amed-queue:plan-approve <id>`. Do not approve or execute it
yourself.
