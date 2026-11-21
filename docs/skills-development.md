# Writing skills

How skills are written in this repository, with `moa7amed-queue` as the example. The
general rules for plugins are in [plugin-development.md](plugin-development.md).

## What a skill is here

A skill is one folder with one file:

```text
plugins/<plugin>/skills/<skill-name>/SKILL.md
```

Users invoke it as `/<plugin>:<skill-name>`; Claude may also invoke it by itself when
its description matches what the user asked for, unless the skill forbids that.

In this repository a skill is a thin, reliable way to run **one command of the plugin's
command line**. The logic lives in the command, where it is tested; the skill tells
Claude which command to run for which request and how to report the result. A skill that
contains logic of its own cannot be tested and drifts.

## The file

```markdown
---
name: queue-retry
description: Put failed or cancelled queue tasks back in line for another attempt.
argument-hint: "[task ids | --failed] [--max-attempts N]"
allowed-tools:
  - Bash(sh "${CLAUDE_PLUGIN_ROOT}/bin/moa7amed-queue" *)
  - PowerShell(& "${CLAUDE_PLUGIN_ROOT}/bin/moa7amed-queue.cmd" *)
---

# Retry tasks

Requested: $ARGUMENTS

1. Work out which tasks are meant ...
2. Run `sh "${CLAUDE_PLUGIN_ROOT}/bin/moa7amed-queue" retry <ids>`.
3. Report ...
```

| Field | Use |
| :-- | :-- |
| `name` | The folder name. Lowercase with hyphens |
| `description` | What it does, and when to use it if that is not obvious. See the next section |
| `argument-hint` | Shown to the user while typing the command |
| `allowed-tools` | Exactly the commands the skill runs, so the user is not asked for permission each time. Always both the `sh` and the PowerShell form |
| `disable-model-invocation: true` | Only the user can start it. Use it for anything that starts work, costs money or is hard to undo |

In the body, `$ARGUMENTS` is what the user typed after the command and
`${CLAUDE_PLUGIN_ROOT}` is the folder of the installed plugin. Always call the command
through `${CLAUDE_PLUGIN_ROOT}`: the installed path changes with every update.

## The description is paid for in every session

The name and description of every skill Claude may invoke by itself are part of every
session of everyone who has the plugin installed, whether or not they use it that day.
The body costs nothing until the skill runs.

So:

- Say what the skill does in one sentence. Add "Use when ..." only if Claude would
  otherwise not connect a typical request to it.
- Put instructions, options, examples and caveats in the body.
- A skill that should only ever be typed by the user gets
  `disable-model-invocation: true`.
- Several near-identical skills (one per model, one per environment) should not each be
  invocable by Claude: make one of them the entry point and say in its body how to do
  the others. `model-auto` does this for `model-opus`, `model-sonnet` and `model-haiku`.

`tests/test_plugin.py` holds the limits (160 characters per description, 600 in total
for skills Claude may invoke). Measure the effect with Claude Code's own estimate:

```bash
claude plugin details moa7amed-queue@moa7amed-tools
```

For 0.3.1 that estimate went from about 545 to about 294 tokens per session by
shortening descriptions and making three skills user-only. It is an estimate by Claude
Code, not a measurement of billed tokens: a real session's token count varies by about
a thousand tokens from run to run for unrelated reasons, which is more than the whole
plugin adds, so the difference cannot be observed directly.

## Pitfalls that have happened here

- **A colon followed by a space in the description breaks the frontmatter.** YAML reads
  `description: Show the queue: pending tasks` as a nested mapping, and Claude Code then
  loads the skill with no metadata at all. Use a dash or rephrase, or quote the whole
  value. `claude plugin validate --strict` catches it; so does the test suite.
- **A skill that names a command which does not exist.** The test suite extracts every
  `bin/moa7amed-queue" <command>` from every skill and checks it against the real
  command list.
- **Assuming a Bash tool.** On Windows a session may only have PowerShell. Every skill
  ends with the same two sentences telling Claude the PowerShell form, and
  `allowed-tools` lists both.
- **Guessing instead of asking.** When a request does not say which tasks are meant, the
  skill shows the queue and asks. Skills that change something never pick a default
  target.

## Adding a skill

1. If the behaviour is new, add the command first (`scripts/mqlib/cli.py`) with tests.
2. Create `skills/<name>/SKILL.md` as above.
3. Add the name to `SKILLS` in `tests/test_plugin.py` (the test fails until you do, on
   purpose: the list of skills is part of the plugin's public surface).
4. Document it in the plugin README.
5. Run:

   ```bash
   claude plugin validate plugins/moa7amed-queue --strict
   cd plugins/moa7amed-queue && python -B -m unittest tests/test_plugin.py
   ```

6. Try it for real in one session, without installing:

   ```bash
   claude --plugin-dir plugins/moa7amed-queue
   ```

Renaming or removing a skill changes what users type. Treat it like any other breaking
change: see [versioning.md](versioning.md).
