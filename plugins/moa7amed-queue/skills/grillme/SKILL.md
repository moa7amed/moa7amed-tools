---
name: grillme
description: Critically review a task, plan or approach for concrete risks, then carry out the owner's decision.
argument-hint: "[task id | plan id | the approach to review]"
disable-model-invocation: true
allowed-tools:
  - Bash(sh "${CLAUDE_PLUGIN_ROOT}/bin/moa7amed-queue" challenge decide*)
  - Bash(sh "${CLAUDE_PLUGIN_ROOT}/bin/moa7amed-queue" challenge raise*)
  - Bash(sh "${CLAUDE_PLUGIN_ROOT}/bin/moa7amed-queue" challenge review*)
  - PowerShell(& "${CLAUDE_PLUGIN_ROOT}/bin/moa7amed-queue.cmd" challenge decide*)
  - PowerShell(& "${CLAUDE_PLUGIN_ROOT}/bin/moa7amed-queue.cmd" challenge raise*)
  - PowerShell(& "${CLAUDE_PLUGIN_ROOT}/bin/moa7amed-queue.cmd" challenge review*)
---

# Challenge, discuss, then execute

Review: $ARGUMENTS

The owner asked for a critical review. Challenge only what evidence supports; then do
what the owner decides.

## 1. Find the concerns

Read what is being reviewed: a queued task (`moa7amed-queue show <id>`), a plan
(`moa7amed-queue project status <id>`), the code it touches, or the approach the owner
described. Start with the queue's own checks for a task:

```bash
sh "${CLAUDE_PLUGIN_ROOT}/bin/moa7amed-queue" challenge review <task id>
```

Raise a concern only when you can point at the evidence (a file and line, a stated
requirement, a measured fact) and name the impact: data loss, weakened security, an
irreversible or public action, a contradiction in the request, a real cost or
performance problem. A different framework, architecture or coding style that you would
prefer is **not** a concern. If you find nothing material, say so in one line and stop.

## 2. Present them once, together

One message, every concern bundled, each in this shape:

```text
Concern: <what the evidence shows, and what it would lead to>
Recommendation: <the alternative you suggest>
Decision: take the recommendation, or keep the original approach?
```

Say plainly which items are recommendations (the owner may proceed anyway) and which are
real restrictions: the tool permissions queued tasks run with, provider rules, legal
limits, or an authorization nobody has given. Accepting a risk never lifts a restriction;
never present your preference as one. For a routine, reversible point, give a one-line
advisory instead and do not ask.

For a queued task, record each concern so the task waits for the decision:

```bash
sh "${CLAUDE_PLUGIN_ROOT}/bin/moa7amed-queue" challenge raise <task id> --concern "<concern>" --evidence "<evidence>" --impact "<impact>" --recommendation "<alternative>"
```

Add `--restriction` for a real limit, `--advisory` for a note that should not hold the
task. For a project plan use `--plan <plan id>` instead of the task id: the plan's
approval then waits for the decision.

## 3. The owner decides

Wait for the owner's answer; any clear wording counts ("I know the risk; go ahead with my original plan." or "Go with your
recommendation."). Ask again only if it is
genuinely unclear. Only the owner decides: never record a decision they did not give, and
never treat silence or an earlier answer about something else as one.

When a queued task is waiting, the capture hook records the owner's answer by itself
(the session then shows "moa7amed-queue recorded the owner's decision"). If the hook
could not tell which task the answer was for, it gives a statement number; once the owner
says which task, record it:

```bash
sh "${CLAUDE_PLUGIN_ROOT}/bin/moa7amed-queue" challenge decide <task id or C id> --statement <N>
```

A decision command run from this session without the owner's recorded words is refused
by design; the owner can also decide in their own terminal with `--recommendation` or
`--original`.

## 4. Then execute

Follow the chosen direction. Do not repeat the warning, do not substitute your preferred
solution, and apply only safeguards that fit the chosen approach. The accepted risk
covers this one concern, in this task or plan, at this revision. Anything else still needs
its own permission: another destructive step, a change to a live system, publishing,
spending above the set limits, or reaching a system nobody has cleared. Reopen the discussion only if material new evidence
changes the risk. Verify the result and report its limitations honestly; never claim a
restriction was bypassed or a step done when it was not.

Run `moa7amed-queue` with the Bash tool exactly as shown. Only when there is no Bash tool, use
PowerShell with the same arguments: `& "${CLAUDE_PLUGIN_ROOT}/bin/moa7amed-queue.cmd" <arguments>`.
