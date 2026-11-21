---
name: infrastructure-assess
description: Record a plan's deployment environment, hardware and capacity, with evidence.
argument-hint: "<plan id> [server specifications, hosting plan, or documents]"
disable-model-invocation: true
allowed-tools:
  - Bash(sh "${CLAUDE_PLUGIN_ROOT}/bin/moa7amed-queue" project check*)
  - Bash(sh "${CLAUDE_PLUGIN_ROOT}/bin/moa7amed-queue" project env*)
  - Bash(sh "${CLAUDE_PLUGIN_ROOT}/bin/moa7amed-queue" project target*)
  - PowerShell(& "${CLAUDE_PLUGIN_ROOT}/bin/moa7amed-queue.cmd" project check*)
  - PowerShell(& "${CLAUDE_PLUGIN_ROOT}/bin/moa7amed-queue.cmd" project env*)
  - PowerShell(& "${CLAUDE_PLUGIN_ROOT}/bin/moa7amed-queue.cmd" project target*)
  - Read
  - Grep
  - Glob
---

# Assess the infrastructure

Requested: $ARGUMENTS

Find out where the system will really run **before** anyone recommends a technology.

Rules:

- Use only what the owner states, what documents or deployment configuration show, or an
  inspection the owner explicitly authorised. **Never connect to or inspect a remote or
  production server on your own.**
- The developer's machine is not the deployment target. `project discover` records it
  separately; never copy its figures into a scenario.
- Do not assume cloud, containers, Kubernetes or a GPU. Shared hosting, a Windows server
  with IIS, or a single VM are common and legitimate.
- When the target is unknown, record **scenarios** (for example "A: existing 4 vCPU / 8 GB
  VM", "B: proposed 8 vCPU / 16 GB VM", "C: managed cloud"), each marked ASSUMED, and
  leave the target unset. Decisions that depend on the choice then wait; nothing else does.

## Ask for what is missing

Deployment model (shared hosting, IIS on Windows Server, Linux server, VM, VPS, dedicated,
on-premises, private/public/hybrid cloud, container platform, Kubernetes, managed
services); operating system and supported runtime versions; vCPUs/cores, architecture,
dedicated or shared; RAM and what else uses it; disk space, SSD/HDD, growth, backups;
network limits, firewall rules, connectivity to integrations, outbound internet; GPU and
its memory, or whether external AI services may be used; whether databases, caches or
services can be installed; budget.

## Record each fact with its evidence class

```bash
sh "${CLAUDE_PLUGIN_ROOT}/bin/moa7amed-queue" project env <id> <scenario> <field> <value> --status USER_CONFIRMED --source "<who or what says so>" [--label "<scenario name>"]
sh "${CLAUDE_PLUGIN_ROOT}/bin/moa7amed-queue" project target <id> <scenario>     # only when the target is actually decided
```

Fields: hosting, os, web_server, cpu_vcpus, cpu_arch, cpu_dedicated, ram_gb, disk_gb,
disk_type, gpu_present, gpu_vram_gb, gpu_runtime, outbound_internet, can_install_services,
container_runtime, admin_access, runtimes (comma-separated: dotnet-framework, dotnet, php,
node, python, java), budget_monthly_usd. VERIFIED needs a source.

## Capacity inputs

How to send JSON to the plan: put the fragment on one line in `--data '...'` (keep each
call small: one section at a time). For a large fragment, write it to a file outside the
project (the system temp folder) and pass `--file <path>`; never write it inside the
project, which would leave the workspace dirty. A multi-line here-document is not covered
by this skill's permissions.

Record the workload as measurable inputs, each with its class (`project apply <id> --data '...'`). A fragment:

```json
{"capacity": {"inputs": {
  "concurrent_users": {"value": 200, "status": "USER_CONFIRMED", "source": "owner"},
  "requests_per_user_per_minute": {"value": 6, "status": "ASSUMED"},
  "avg_request_ms": {"value": 250, "status": "ASSUMED"},
  "peak_factor": {"value": 3, "status": "ASSUMED"},
  "uploads_per_day": {"value": 500, "status": "USER_CONFIRMED"},
  "avg_upload_mb": {"value": 2, "status": "USER_CONFIRMED"},
  "retention_days": {"value": 2555, "status": "VERIFIED", "source": "records policy"},
  "documents_stored_externally": {"value": 1, "status": "USER_CONFIRMED"},
  "db_rows_per_day": {"value": 20000, "status": "ASSUMED"},
  "avg_row_kb": {"value": 2, "status": "ASSUMED"}}}}
```

```bash
sh "${CLAUDE_PLUGIN_ROOT}/bin/moa7amed-queue" project check <id>
```

`check` computes requests per second, requests in flight (Little's law), storage and
database growth, each with its formula and the weakest evidence class of its inputs. If
documents live in an external system, they are not sized as local storage.

Report the result to the owner as estimates, never as guarantees: name the measured
figures, vendor-documented limits, calculations and assumptions separately, and recommend
a load test before any capacity commitment. Then continue with
the architecture review (`/moa7amed-queue:architecture-review <id>`, or its instructions in
`${CLAUDE_PLUGIN_ROOT}/skills/architecture-review/SKILL.md` when you are already planning).

Run `moa7amed-queue` with the Bash tool exactly as shown. Only when there is no Bash tool, use
PowerShell with the same arguments: `& "${CLAUDE_PLUGIN_ROOT}/bin/moa7amed-queue.cmd" <arguments>`.
