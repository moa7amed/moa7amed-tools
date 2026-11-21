---
name: replica-compare
description: Compare a rebuild's screens with the reference screenshots and run bounded correction rounds.
argument-hint: "<replica id> [screen id] [screenshot or URL of the rebuild]"
disable-model-invocation: true
allowed-tools:
  - Bash(sh "${CLAUDE_PLUGIN_ROOT}/bin/moa7amed-queue" replica compare*)
  - Bash(sh "${CLAUDE_PLUGIN_ROOT}/bin/moa7amed-queue" replica config*)
  - Bash(sh "${CLAUDE_PLUGIN_ROOT}/bin/moa7amed-queue" replica improve*)
  - PowerShell(& "${CLAUDE_PLUGIN_ROOT}/bin/moa7amed-queue.cmd" replica compare*)
  - PowerShell(& "${CLAUDE_PLUGIN_ROOT}/bin/moa7amed-queue.cmd" replica config*)
  - PowerShell(& "${CLAUDE_PLUGIN_ROOT}/bin/moa7amed-queue.cmd" replica improve*)
  - Read
---

# Replica comparison

Requested: $ARGUMENTS

## 1. Tell Replica where the rebuild runs (once)

Ask the owner how the rebuild is served (for example `python -m http.server 8000` in its
folder, `dotnet run`, `npm run dev`); they start it. Then map screens to its pages:

```bash
sh "${CLAUDE_PLUGIN_ROOT}/bin/moa7amed-queue" replica config <id> --candidate-url http://localhost:8000/ --page S1=/index.html --page S2=/details.html
```

Regions with moving content (a clock, a carousel) can be excluded:
`--mask S1=1100,10,160,30` (x, y, width, height in reference pixels).

## 2. Compare

```bash
sh "${CLAUDE_PLUGIN_ROOT}/bin/moa7amed-queue" replica compare <id> --all
sh "${CLAUDE_PLUGIN_ROOT}/bin/moa7amed-queue" replica compare <id> S1 <screenshot.png | url>
```

Exit code 0: every compared screen meets `replica_visual_threshold`; 1: some do not;
2: something could not be compared. Read the reference, the rebuild screenshot and the
difference image (red = differs) with the Read tool, and report per screen the score, the
regions that differ and the likely cause. Always quote the method line: the score is a
pixel comparison, not a perceptual or functional measure. Never state a similarity
figure that was not measured.

## 3. Correct, within limits

```bash
sh "${CLAUDE_PLUGIN_ROOT}/bin/moa7amed-queue" replica improve <id>
```

Each call closes the previous round with the latest measurements and either queues
correction tasks for the screens that differ most or stops: acceptance met, round limit
(`replica_max_iterations`), budget (`replica_budget_usd`), every correction failed, two
rounds without measurable gain, or a screen got worse (it names the commit to return
to). After the corrections ran, compare again and call `improve` again. With the owner's
agreement, `replica improve <id> --run` runs the queue and measures again round after
round until a stop condition.
