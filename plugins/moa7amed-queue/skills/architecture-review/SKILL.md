---
name: architecture-review
description: Compare viable architectures for a plan and record the choice as decision records (ADRs).
argument-hint: "<plan id> [the decision to make]"
disable-model-invocation: true
allowed-tools:
  - Bash(sh "${CLAUDE_PLUGIN_ROOT}/bin/moa7amed-queue" *)
  - PowerShell(& "${CLAUDE_PLUGIN_ROOT}/bin/moa7amed-queue.cmd" *)
  - Read
  - Grep
  - Glob
---

# Review the architecture

Requested: $ARGUMENTS

Start from the recorded requirements and infrastructure:

```bash
sh "${CLAUDE_PLUGIN_ROOT}/bin/moa7amed-queue" project check <id>
sh "${CLAUDE_PLUGIN_ROOT}/bin/moa7amed-queue" project show <id> --section requirements
sh "${CLAUDE_PLUGIN_ROOT}/bin/moa7amed-queue" project show <id> --section infrastructure
```

If there is no infrastructure, assess it first: follow
`${CLAUDE_PLUGIN_ROOT}/skills/infrastructure-assess/SKILL.md`.

How to send JSON to the plan: put the fragment on one line in `--data '...'` (keep each
call small: one section at a time). For a large fragment, write it to a file outside the
project (the system temp folder) and pass `--file <path>`; never write it inside the
project, which would leave the workspace dirty. A multi-line here-document is not covered
by this skill's permissions.

## How to decide

- **Select the simplest option that meets the verified requirements within the real
  constraints.** Newer or more complex is not better by itself. Consider, where relevant:
  monolith, modular monolith, layered or clean architecture, event-driven, microservices,
  serverless components, conventional IIS deployment, containers.
- An existing project keeps its working stack unless evidence shows it cannot meet a
  requirement. Replacing a framework, database or hosting model is a material decision.
- Every additional component (cache, message broker, search engine, container runtime,
  orchestrator, GPU inference, vector database) needs a requirement that needs it:
  `{"id": "redis", "why": ["R7"]}`. Without one, leave it out.
- AI: decide from the requirements whether it is retrieval only, RAG, embeddings, a
  hosted model API, or local inference (CPU or GPU). Do not assume a GPU. Weigh model
  memory (`min_vram_gb`), latency, concurrency, licensing, data residency and recurring
  API cost.
- One decision per subject (application architecture, database, hosting, authentication,
  AI runtime, ...). Category is one of: architecture, infrastructure, database, framework,
  deployment, licensing, data-migration, security (all material: the owner approves them),
  or another word for a non-material decision.
- The matrix is a support, not the decision. Scores are 1 to 5 with a `basis` (VERIFIED,
  USER_CONFIRMED, ASSUMED, UNKNOWN). Missing or assumed scores make the totals ranges; if
  ranges overlap, say the matrix does not decide and name the evidence that would.

## Record it

Components use the catalogue ids the engine checks: dotnet-framework, aspnet-mvc5,
aspnet-webforms, dotnet, aspnet-core, node, python, java, php, sqlserver,
sqlserver-express (add `expected_db_gb`), postgres, mysql, sqlite, mongodb, redis,
rabbitmq, kafka, elasticsearch, docker, kubernetes, vector-db, pgvector, cloud-llm-api,
local-llm-gpu (add `min_vram_gb`), local-llm-cpu, embeddings-api, external-dms. Others
are reported as "not assessed"; check them by hand. Add `"managed": true` to a database
or service the provider runs for you, and `resources` (ram_gb, disk_gb, cpu_vcpus) when
you can estimate them.

A decision fragment (send it with `project apply <id> --data '...'` or `--file`):

```json
{"decisions": [{"id": "ADR-1", "title": "Application architecture", "category": "architecture",
  "context": "...", "requirements": ["R1", "R2"], "constraints": ["Windows shared hosting (USER_CONFIRMED)"],
  "facts": ["F3"],
  "criteria": [{"name": "fits the hosting", "weight": 3}, {"name": "operational effort", "weight": 2},
               {"name": "team experience", "weight": 2}, {"name": "cost", "weight": 1}],
  "options": [
    {"id": "modular-mvc5", "name": "Modular monolith on ASP.NET MVC 5", "style": "modular-monolith",
     "components": ["dotnet-framework", "aspnet-mvc5", {"id": "sqlserver", "managed": true}],
     "scores": {"fits the hosting": {"score": 5, "basis": "USER_CONFIRMED", "rationale": "..."}}},
    {"id": "services", "name": "Microservices in containers", "style": "microservices",
     "components": ["dotnet", "aspnet-core", {"id": "docker", "why": ["R9"]}],
     "scores": {"fits the hosting": {"score": 1, "basis": "USER_CONFIRMED", "rationale": "..."}}}],
  "selected": "modular-mvc5",
  "rejected": [{"option": "services", "reason": "..."}],
  "rationale": "...",
  "implications": {"hardware": "...", "operational": "...", "cost": "..."},
  "risks": ["..."], "assumptions": ["..."]}]}
```

```bash
sh "${CLAUDE_PLUGIN_ROOT}/bin/moa7amed-queue" project check <id>
```

`check` shows, per option and per infrastructure scenario, whether it is compatible,
incompatible or needs information, and why (documented limit, stated fact, or heuristic).
An incompatible selection is an error. A decision whose evidence is incomplete is
NEEDS_INFORMATION and cannot be approved: say exactly which fact is missing.

An **approved** decision is never edited. A change is a new decision with
`"supersedes": "ADR-1"`, approved on its own (`/moa7amed-queue:plan-revise`).

Finish by presenting each decision to the owner: options, compatibility, the reasons, the
risks and assumptions, and what approval means (`/moa7amed-queue:plan-approve <id>`).

Run `moa7amed-queue` with the Bash tool exactly as shown. Only when there is no Bash tool, use
PowerShell with the same arguments: `& "${CLAUDE_PLUGIN_ROOT}/bin/moa7amed-queue.cmd" <arguments>`.
