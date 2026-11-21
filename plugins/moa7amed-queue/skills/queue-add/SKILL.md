---
name: queue-add
description: Queue a task for later instead of doing it now. Use when the user wants work queued, deferred, or done after the current task.
argument-hint: "[--priority low|normal|high|urgent] [--after ID,ID] [--model opus|sonnet|haiku] <task prompt>"
allowed-tools:
  - Bash(sh "${CLAUDE_PLUGIN_ROOT}/bin/moa7amed-queue" *)
  - PowerShell(& "${CLAUDE_PLUGIN_ROOT}/bin/moa7amed-queue.cmd" *)
---

# Queue a task

Store the request below as a task in this project's persistent queue. **Do not start
working on it** and do not investigate it: your only job is to store it and confirm.

Request:

$ARGUMENTS

## What to store

- **Prompt**: the user's text exactly as written. Never rephrase, translate, summarise,
  expand or improve it; the queue is the record of what was asked. Only leading option
  flags (`--priority`, `--after`, `--max-attempts`, `--model`, `--phased`) are removed
  from it.
- **Acceptance criteria**: criteria written inside the prompt (an `Acceptance criteria:`
  section) are detected automatically, so leave them in the prompt. If the user gave
  criteria separately from the prompt, pass them with `--acceptance "..."`.
- **Priority**: `low`, `normal` (default), `high`, `urgent`, or an integer. Higher runs first.
- **Dependencies**: `--after 3,4` makes the task wait until tasks 3 and 4 are done.
- **Several tasks in one request**: add each as its own task, in the order given. Use
  `--after` with the id you just received when a later task builds on an earlier one.
- **Model**: leave it out. The queue analyses each task and chooses Haiku, Sonnet or Opus
  by itself, and the command prints the choice and the reason. Pass `--model opus`,
  `--model sonnet` or `--model haiku` only when the user asked for a specific model for
  this task. `--phased` (only on request) makes Opus plan a very complex task before a
  second model implements it.
- If the request above is empty, ask the user what to queue instead of guessing.

## How to store it

Pass the prompt on stdin through a quoted heredoc so the shell cannot alter it:

```bash
sh "${CLAUDE_PLUGIN_ROOT}/bin/moa7amed-queue" add --stdin --priority normal <<'TASK_PROMPT'
<the prompt, verbatim>
TASK_PROMPT
```

Add `--after ID,ID`, `--acceptance "..."` or `--max-attempts N` before the `<<` when
needed.

## Afterwards

Report in one or two lines: the task id, its priority, the model chosen for it with the
short reason, what it waits for (if anything) and how many tasks are pending. Mention that `/moa7amed-queue:queue-run` starts the
runner. Then return to whatever was in progress before; the queued task is not yours to do.

Run `moa7amed-queue` with the Bash tool exactly as shown. Only when there is no Bash tool, use
PowerShell with the same arguments: `& "${CLAUDE_PLUGIN_ROOT}/bin/moa7amed-queue.cmd" <arguments>`.
With PowerShell, write the prompt to a temporary UTF-8 file first and pass
`add --prompt-file <file>` instead of the heredoc.
