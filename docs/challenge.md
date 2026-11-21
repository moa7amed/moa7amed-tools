# Challenge, discuss, then execute

moa7amed-queue can stop a task that carries a concrete risk, say why in a few lines, offer
an alternative, and then do what you decide. You can take the recommendation or proceed
with your original approach; either way the decision is stored and the warning is not
repeated. This is the `grillme` capability: `/moa7amed-queue:grillme` in Claude Code, and
the `moa7amed-queue challenge` commands.

- [Modes](#modes)
- [What counts as a concern](#what-counts-as-a-concern)
- [A held task, step by step](#a-held-task-step-by-step)
- [Deciding](#deciding)
- [What a decision does and does not change](#what-a-decision-does-and-does-not-change)
- [A critical review on request](#a-critical-review-on-request)
- [Commands and settings](#commands-and-settings)
- [Storage](#storage)
- [Limits](#limits)

## Modes

| `challenge_mode` | What happens |
| :-- | :-- |
| `explicit` (default) | Nothing automatic. A review runs when you ask: `moa7amed-queue challenge review ID` or `/moa7amed-queue:grillme`. |
| `adaptive` | Before a task starts, rules check its wording (no model call). A material concern holds that task until you decide; other tasks keep running. A task's own session may also stop once, before changing anything, to ask about a risk it found (`MQ_CONCERN`). |
| `off` | No optional challenges; holds they placed are released. Plan approval, tool permissions, the verification check and cost limits are unchanged. |

```bash
moa7amed-queue config set challenge_mode adaptive        # this project
moa7amed-queue config set challenge_max_rounds 1         # holds per task (default 1)
```

`challenge_max_rounds` bounds how often one task can be held. A concern that arrives after
the rounds are used is recorded as an advisory instead. A concern reopened by new
evidence does not use up a round.

## What counts as a concern

A concern names a risk and the evidence for it. A preference for another framework,
architecture or coding style is never one. The rules look for:

| Rule | Example wording | Kind |
| :-- | :-- | :-- |
| `secret-in-code` | "hard-code the API key", "store passwords in plain text" | material |
| `security-off` | "disable SSL certificate verification", "allow all origins" | material |
| `destructive-data` | "drop the users table", "delete all records" | material |
| `history-rewrite` | "force-push", "rewrite the history" | material; a restriction when tasks cannot run commands |
| `production-publish` | "deploy to production", "publish to npm" | material; a restriction when tasks cannot run commands |
| `skip-verification` | "without tests", "--no-verify" | advisory |
| `workaround` | "suppress the warnings", "quick hack" | advisory |

Arabic wording is recognised for deleting data and for unencrypted passwords.

**Material** concerns hold the task. **Advisories** do not: they are shown when the task
is added, passed to the task's session, and the task runs.

**Recommendation or restriction.** A recommendation is advice: you may proceed anyway. A
restriction is a real limit: by default queued tasks run with `acceptEdits` and cannot run
commands, so a task asked to push or deploy cannot do that step whatever is decided. You
can acknowledge it (the step stays out of the task and the session says so), cancel the
task, or grant the specific command yourself in `allowed_tools`.

## A held task, step by step

```text
$ moa7amed-queue add "Drop the users table and recreate it with an email column"
Queued task #2 [normal] Drop the users table and recreate it with an email column
  concern C1: data would be deleted, and that cannot be undone
    recommendation: take a backup or an export first and make the change a reversible
    migration (or mark the rows deleted instead of removing them)
    decide: `moa7amed-queue challenge decide C1 --recommendation` or `--original`

$ moa7amed-queue status
     2  normal   blocked   0/2   sonnet!   Drop the users table ...  [decision: data would be deleted, ...]
```

(Output shortened.) Task #2 waits; every other eligible task runs under the usual queue
rules. A task whose session stopped to ask is back in the queue without having used an
attempt; it changed nothing, so nothing runs twice. After your decision it starts once,
with the decision in its prompt.

## Deciding

Say it in your own words. No special phrase is needed:

```text
I know the risk; go ahead with my original plan.
Go with your recommendation.
أنا فاهم المخاطر، كمل زي ما قلت
```

- **In Claude Code.** While a decision is open, the capture hook reads what you type.
  When it clearly decides and it is clear which task it is for (only one is waiting, or
  you name it: "task #2", "C1"), it is recorded at once and the message still reaches
  Claude. A question, a negation, or a message that says both things is not taken as a
  decision. When several tasks wait, the hook keeps your words and Claude asks which task
  you mean.
- **In a terminal:**

  ```bash
  moa7amed-queue challenge decide C1 --original --tradeoff "the table holds test data only"
  moa7amed-queue challenge decide 2 --recommendation
  moa7amed-queue challenge decide 2 --said "Known risk, go ahead as I asked."
  ```

**Only you decide.** A decision is accepted from your own terminal, from a prompt you
typed into Claude Code, or from a Claude Code session that refers to such a prompt
(`--statement N`). It is refused when it comes from a queued task or an agent, or from a
Claude Code session that cannot point to your words. A recorded prompt can be used once
and only within two hours. This keeps a session from deciding on your behalf. It is not
a security boundary against a program that can write the queue's database directly.

## What a decision does and does not change

After you decide, the task's session receives the decision: follow the recommendation;
or proceed as asked with safeguards that fit that approach and report its limitations;
or, for a restriction, leave the step out. It is told not to raise the concern again and
not to swap in another solution.

A decision covers one concern, one task and that task's revision (its exact prompt and
criteria). It is asked again only when new evidence appears, for example when the task's
session reports the same concern with evidence the decision did not cover.

A decision never changes:

- the tool permissions tasks run with, or what Claude Code and the model provider allow;
- the approval of a material DEEP plan (`workflow approve`), which still waits;
- the verification check, the per-task budget or the session cost limit;
- anything outside that task. Another destructive step, a change to a live system,
  publishing, or reaching a system you have not cleared each still needs its own
  permission.

## A critical review on request

`/moa7amed-queue:grillme [task, plan or approach]` asks Claude for a critical review.
It checks the task with the rules, reads what it touches, and presents every material
concern in one message:

```text
Concern: <what the evidence shows, and what it would lead to>
Recommendation: <the alternative>
Decision: take the recommendation, or keep the original approach?
```

For a queued task each concern is recorded (`challenge raise`), so the task waits for you;
for a project plan (`challenge raise --plan`), its approval waits. If nothing material is
found, Claude says so and stops.

## Commands and settings

```text
moa7amed-queue challenge [list]                 open concerns and recent decisions
moa7amed-queue challenge show ID | C3           a task's concerns, or one concern
moa7amed-queue challenge review ID [--no-hold]  check a task now, whatever the mode
moa7amed-queue challenge raise ID | --plan PLAN --concern T --evidence T [--impact T]
                         [--recommendation T] [--restriction] [--advisory]
moa7amed-queue challenge decide [C3|ID ...] --recommendation | --original
                         [--said TEXT] [--statement N] [--tradeoff TEXT]
```

| Setting | Default | Meaning |
| :-- | :-- | :-- |
| `challenge_mode` | `explicit` | `off`, `adaptive` or `explicit` (see [Modes](#modes)). |
| `challenge_max_rounds` | `1` | Holds per task; later concerns become advisories. |

`status`, `show` and the runner's idle message name tasks waiting for a decision.

## Storage

Concerns and decisions live in the project's queue database (table `challenges`), next
to the tasks: the task, the revision, the rule, the concern and its evidence, impact and
recommendation, who raised it, the decision, the accepted trade-off, who decided and the
owner's words, and the times. Prompts the hook kept as possible decisions are in
`owner_statements`. Both tables are additive: earlier versions ignore them. While a
decision is open, a marker file in `open-decisions/` in the data directory lets the hook
skip Python for ordinary prompts whenever nothing is open.

## Limits

- The rules read the task's wording. They find what is said ("drop the table"), not
  what a vaguely worded task might end up doing. The task's session and the
  `grillme` review cover the rest.
- A task's session can only ask before it changes files. A concern it reports after
  changing files is recorded as an advisory, and the run counts as usual.
- Plans have no rule-based check: a concern about a plan is raised by a review
  (`challenge raise --plan ID`). It applies to the plan's current revision, and
  `project approve --yes` refuses until you have decided it.
