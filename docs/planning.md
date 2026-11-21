# Project planning

moa7amed-queue can plan a project, or a major change to one, before anything is built:
requirements with their evidence, the real deployment environment, a comparison of
architectures against it, recorded decisions, and a task breakdown that the queue then
executes. This page explains how it works, what it guarantees and what it does not, with
a worked example whose output was produced by the commands shown.

## Two modes

| | Quick task mode | Project planning mode |
| :-- | :-- | :-- |
| For | A bug fix, a form field, a report: a small, well-defined change | A new system, a major feature, an infrastructure or database change, work in dependent stages |
| How | `/moa7amed-queue:queue-add ...` or `moa7amed-queue add ...` | `/moa7amed-queue:project-plan <id> ...` |
| Cost | Nothing extra | The analysis happens in your Claude session; the engine itself calls no model |

Nothing forces a plan. Queues without plans work exactly as before.

## Who does what

The **analysis** is done by Claude in the planning skills, in your session, with you:
reading the code and the documents you provide, asking for what is missing, proposing
options. The **engine** (the `moa7amed-queue project` commands) holds the result and
enforces rules that do not depend on anyone's judgement:

| Rule | What the engine does |
| :-- | :-- |
| Evidence | Every fact, requirement and infrastructure value is VERIFIED (with evidence), USER_CONFIRMED, ASSUMED, UNKNOWN or CONFLICTING. VERIFIED without evidence is an error. Unknowns and conflicts are listed, never filled in |
| Developer machine | Discovery records your machine separately; it can never be the deployment target |
| Compatibility | Each option is checked against each infrastructure scenario: incompatible, needs information, or compatible, with the reason and what it rests on (a vendor-documented limit, a stated fact, or a heuristic, which can only ask for information, never rule an option out) |
| Justification | A cache, broker, container runtime, orchestrator, GPU inference or vector database must name the requirement that needs it |
| Simplicity | If a simpler compatible option is not shown to be worse, the engine says so |
| Existing systems | Replacing an existing framework, runtime or database is a material decision |
| No false precision | A decision matrix with missing or assumed scores gives ranges; overlapping ranges are reported as "the matrix does not decide" |
| Decisions | PROPOSED, NEEDS_INFORMATION, APPROVED, SUPERSEDED, REJECTED. Only an explicit approval sets APPROVED; an approved decision can only be superseded by a new one |
| Tasks | Unique ids, known dependencies, no cycles (the cycle is named), acceptance criteria, no vague titles, no technology that no decision selects |
| Approval | Freezes an immutable revision with a SHA-256 hash; the database refuses to change or delete approved revisions and approvals |
| Execution | Imports only approved tasks, in dependency order, idempotently; holds only the tasks whose decision is not approved |
| Compliance | After a plan task commits, its change is compared with the approved architecture |

## The workflow

```text
/moa7amed-queue:project-plan <id>             requirements, facts, open questions (+ discovery for existing code)
/moa7amed-queue:infrastructure-assess <id>    deployment environment, hardware, capacity inputs
/moa7amed-queue:architecture-review <id>      options, compatibility, decision matrix, ADRs
/moa7amed-queue:plan-approve <id>             you approve specific decisions and a specific revision
/moa7amed-queue:plan-execute <id>             approved tasks enter the queue
/moa7amed-queue:queue-run                     the queue executes them, one at a time
/moa7amed-queue:plan-status <id>              progress, blockers, compliance findings
/moa7amed-queue:plan-revise <id> ...          when implementation reveals a mismatch
```

The plan moves through these stages: **analysis** (no decisions yet), **architecture
proposed**, **architecture approved**, **plan ready** (approved decisions and a clean
task breakdown), **approved for execution**.

### Approval: what needs it, and what does not

Your explicit approval is needed for material decisions: a new architecture baseline,
infrastructure changes, a database engine, a major framework, the deployment model,
significant licensing cost, destructive data migrations, changes to security
requirements. The preview shows each decision, what it rests on that nobody has
confirmed, and the decisions that cannot be approved yet with exactly what is missing.
Once a revision is approved for execution, its tasks run under the normal queue rules;
routine tasks need no further approval.

When information is missing (the hosting, a GPU, an integration's API), analysis
continues: the dependent decision stays NEEDS_INFORMATION, the tasks that depend on it
are held, and everything else goes ahead.

### Execution and compliance

Each plan task becomes an ordinary queue task (source `plan:<id>`) whose prompt carries
the approved architecture. The queue's locks, crash recovery, retries, commits and model
routing apply unchanged. A task's state is read from the queue, so the plan and the queue
cannot disagree.

After a plan task commits, the change is checked for technologies it introduces: container
files, Kubernetes manifests, and dependencies added to `package.json`, Python requirement
files, .NET project files and `packages.config`. A technology that an approved decision
rejected, or that is incompatible with the target, is a **violation**; one that no
approved decision covers is **unapproved**. By default (`plan_compliance block`) either
fails the task with the reason, keeps the commit (nothing is reverted), does not retry it,
and leaves its dependents blocked. With `plan_compliance warn`, unapproved findings are
only recorded; violations still fail the task.

This check recognises the technologies in the catalogue by their usual files and package
names. It is not a review of the code: it does not see a hand-written HTTP call to a new
service, or architectural boundaries crossed inside the code.

### Revisions

`/moa7amed-queue:plan-revise` records an issue, names the affected tasks (and everything
that depends on them), and keeps completed work. An approved decision is changed by a new
decision with `"supersedes"`; once approved, the old one becomes SUPERSEDED. On the next
import, queued tasks whose content changed are replaced, tasks that now wait for an
unapproved decision are withdrawn from the queue (cancelled, not deleted), and completed
tasks stay with the revision they were done under.

### Model use

The planning depth (quick, standard, deep) is chosen from the plan (a new system,
security or availability requirements, several integrations, infrastructure decisions,
the words of the request) and can be set by hand (`project depth <id> deep`). It suggests
a model tier per stage, for example Opus for the architecture review of a deep plan. The
skills run in your current session and say so when a stronger model is advisable; they
do not switch models or make extra model calls by themselves. Imported tasks are routed
by the existing router; a task can name its model, and analysis tasks of a deep plan go to
the strongest tier.

## Worked example

A .NET correspondence register for about 200 staff, documents kept in an existing document
management system (DMS), an existing Windows VM with IIS whose memory nobody has confirmed.
Every output below was produced by the real commands;
`python plugins/moa7amed-queue/tests/planning_example.py` repeats the whole example
(free: no model is called).

`moa7amed-queue project check corr`

```text
Plan corr: architecture proposed; planning depth deep
  ADR-1 PROPOSED (evidence says PROPOSED)
      monolith     current: compatible
      services     current: needs_information
  ADR-2 NEEDS_INFORMATION (evidence says NEEDS_INFORMATION)
      api          current: compatible
      share        current: compatible
      missing: fact F1 is unknown
      matrix: no weighted criteria: the matrix says nothing
  [warn ] R3           no task implements this requirement
  [warn ] R4           no task implements this requirement
  [info ] F1           still unknown: The DMS offers an API the application can call
```

The containerised option is not ruled out by an assumption, but it cannot be confirmed
either, from `decisions/ADR-1.md`:

```text
### services: Microservices in containers with a message broker
Components: dotnet, aspnet-core, sqlserver, docker (needed by R3), rabbitmq (needed by R3)
- current: needs_information
  - docker (stated): Docker containers needs container runtime, which is not recorded for the target
  - resources (stated): estimated to need 10 GB of memory; the target has 8
```

| Option | fits the server (w 3) | operational effort (w 2) | team experience (w 2) | Weighted total |
| :-- | :-- | :-- | :-- | :-- |
| monolith | 5 (user_confirmed) | 4 (assumed) | 4 (assumed) | 3.86 to 5.00 |
| services | 2 (assumed) | 1 (assumed) | 2 (assumed) | 1.00 to 2.71 |

`moa7amed-queue project approve corr`

```text
Plan corr, revision 1:
  approve ADR-1
      rests on infrastructure: ram_gb = 8 (assumed)
  NOT approvable ADR-2: fact F1 is unknown
Nothing was changed. Add --yes to approve these decisions and the plan for execution.
```

After `approve --yes` and `execute --yes`, `moa7amed-queue project status corr`:

```text
Stage    architecture proposed
Revision approved for execution: 1; draft revision 2
  M1 Register correspondence                  0/3 done
  T1     pending       Create the Correspondence table and its EF Core migration
  T2     waiting       Registration form and list page (waits for T1)
  T3     held          Link a registered item to its DMS document (waits for the approval of ADR-2)
```

Capacity, from `infrastructure.md`: the documents live in the DMS, so they are not sized
as local storage, and every estimate says what it rests on.

| Estimate | Value | Method | Confidence |
| :-- | :-- | :-- | :-- |
| requests_per_second | 4.0 | concurrent_users x requests_per_user_per_minute / 60 | rests on assumptions |
| peak_requests_per_second | 8.0 | requests_per_second x peak_factor | rests on assumptions |
| requests_in_flight | 2.4 | Little's law: (peak) requests per second x average duration | rests on assumptions |
| application_document_storage_gb | 0.0 | documents are kept in an external system | owner-stated inputs |
| database_growth_gb_per_year | 2.78 | db_rows_per_day x avg_row_kb x 365 | rests on assumptions |

## Hardware-aware examples the tests check

| Situation (as recorded) | What the engine says |
| :-- | :-- |
| ASP.NET MVC 5 on Windows shared hosting; an option with Kubernetes, containers and Redis | Kubernetes needs a cluster: incompatible. Redis does not officially support Windows: incompatible. Containers on shared hosting: needs information. The existing stack: compatible once the host confirms the runtime and the database is managed |
| Replace the existing MVC 5 framework with ASP.NET Core | An error unless the decision is marked material |
| A cache added "for performance" without a performance requirement | Unjustified component |
| An option estimated at 6 GB on a 4 GB VPS | Incompatible; at 3.6 GB: a warning about headroom |
| Hosted model API, no GPU | Compatible with outbound internet; local GPU inference incompatible |
| Hosted API and a confirmed data-residency requirement | Incompatible |
| On-premises GPU with 24 GB; models needing 16 / 48 GB / unstated | Compatible / incompatible / needs information |
| Hosting assumed, not confirmed | Never "incompatible" on an assumption: needs information |
| No target decided, scenarios A and B | Every option is evaluated per scenario; decisions that depend on the choice need information |

## Commands

```text
moa7amed-queue project new ID [--title T] [--mode new|existing] [--summary S]
moa7amed-queue project list | show ID [--section S]
moa7amed-queue project discover ID                 what the project's files show
moa7amed-queue project apply ID [--data JSON | --file F]   merge a JSON fragment (stdin by default)
moa7amed-queue project env ID SCENARIO FIELD VALUE --status S [--source TEXT] [--label L]
moa7amed-queue project target ID SCENARIO|none
moa7amed-queue project depth ID quick|standard|deep|auto
moa7amed-queue project check ID                    exit code 1 while there are errors
moa7amed-queue project approve ID [--decision ADR..] [--decisions-only] [--note N] [--yes]
moa7amed-queue project execute ID [--yes]
moa7amed-queue project status ID
moa7amed-queue project revise ID --issue TEXT [--task T] [--decision D]
moa7amed-queue project report ID [--out DIR | --to-project]
```

The JSON format of a plan is documented at the top of `scripts/mqlib/plan_model.py` and by
example in the skills.

## Where plans are kept

In the project's queue database, in the data directory (`~/.moa7amed-queue`), next to
its tasks: the draft, every approved revision, approvals, links to queue tasks, compliance
findings and a log. They are covered by `backup` and `restore`. Nothing is written into
your project unless you ask: `project report --to-project` writes the documents to
`<project>/.moa7amed/plans/<id>/` (commit them, or the next task stops for uncommitted
changes). The tables are additive; older versions of the plugin ignore them.

## Limitations

- The engine checks; it does not analyse. The quality of requirements, options and scores
  is the quality of the analysis in the session and of the information you give it.
- The technology catalogue is deliberately small. Anything outside it is reported as "not
  assessed" and must be checked by a person.
- Capacity figures are arithmetic on the inputs, not benchmarks. Load-test before
  committing to a configuration.
- Compliance after a task recognises technologies by files and package names, not by
  reading the code.
- Not implemented (roadmap): critical-path analysis, cost optimisation of architectures,
  multi-agent planning, automated drift detection beyond the post-task check, and
  infrastructure benchmarking.
