---
name: plan-approve
description: Record the owner's explicit approval of a plan revision and its architecture decisions.
argument-hint: "<plan id> [ADR ids] [--decisions-only]"
disable-model-invocation: true
allowed-tools:
  - Bash(sh "${CLAUDE_PLUGIN_ROOT}/bin/moa7amed-queue" project approve*)
  - Bash(sh "${CLAUDE_PLUGIN_ROOT}/bin/moa7amed-queue" project check*)
  - PowerShell(& "${CLAUDE_PLUGIN_ROOT}/bin/moa7amed-queue.cmd" project approve*)
  - PowerShell(& "${CLAUDE_PLUGIN_ROOT}/bin/moa7amed-queue.cmd" project check*)
---

# Approve a plan

Requested: $ARGUMENTS

Approval is the owner's decision, made by them in this conversation. Never approve on
your own initiative, and never treat silence, an earlier approval, or an approval of a
different revision as consent.

1. Show what would be approved (this changes nothing):

   ```bash
   sh "${CLAUDE_PLUGIN_ROOT}/bin/moa7amed-queue" project check <id>
   sh "${CLAUDE_PLUGIN_ROOT}/bin/moa7amed-queue" project approve <id> [--decision ADR-1 ...] [--decisions-only]
   ```

2. Present it: the revision number, each decision with its selected option, the main
   reasons, risks, assumptions and cost implications, the decisions that **cannot** be
   approved yet and exactly what they are missing, and the tasks that will stay held
   because of them. Material decisions (architecture baseline, infrastructure, database
   engine, framework, deployment model, licensing cost, destructive data migration,
   security requirements) need this explicit approval; routine tasks inside an approved
   plan do not.

3. Only after the owner says yes to that specific list, run the same command with `--yes`.
   The revision is then frozen: it cannot be changed, only superseded by a later
   revision. Report the revision and its hash, and the next step:
   `/moa7amed-queue:plan-execute <id>`.

`--decisions-only` approves architecture decisions without approving the plan for
execution (for example while the task breakdown is still being written).

Run `moa7amed-queue` with the Bash tool exactly as shown. Only when there is no Bash tool, use
PowerShell with the same arguments: `& "${CLAUDE_PLUGIN_ROOT}/bin/moa7amed-queue.cmd" <arguments>`.
