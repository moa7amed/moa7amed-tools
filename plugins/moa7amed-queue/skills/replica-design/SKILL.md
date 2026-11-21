---
name: replica-design
description: Extract a replica project's design system (colours, type, spacing, components) from its references.
argument-hint: "<replica id>"
disable-model-invocation: true
allowed-tools:
  - Bash(sh "${CLAUDE_PLUGIN_ROOT}/bin/moa7amed-queue" replica analyze*)
  - Bash(sh "${CLAUDE_PLUGIN_ROOT}/bin/moa7amed-queue" replica tokens*)
  - PowerShell(& "${CLAUDE_PLUGIN_ROOT}/bin/moa7amed-queue.cmd" replica analyze*)
  - PowerShell(& "${CLAUDE_PLUGIN_ROOT}/bin/moa7amed-queue.cmd" replica tokens*)
  - Read
---

# Replica design system

Project: $ARGUMENTS

## 1. Measure

```bash
sh "${CLAUDE_PLUGIN_ROOT}/bin/moa7amed-queue" replica tokens <id>
```

This counts colours, font families and sizes, weights, spacing, radii, shadows,
breakpoints, CSS variables and text direction in the captured pages' computed styles
and sources and in supplied stylesheets, and the dominant colours of screenshots. The
counts are evidence, not decisions.

## 2. Write the design specification

Read the screenshots and the counts, then record the tokens the rebuild will use, each
with its evidence, and the component patterns (navigation, forms, tables, cards, dialogs,
icons, motion, RTL/LTR behaviour):

```json
{"design": {"tokens": [{"group": "color", "name": "primary", "value": "#1f6feb",
   "evidence": "OBSERVED", "refs": ["E3"], "note": "buttons and links"},
   {"group": "font-size", "name": "body", "value": "14px", "evidence": "OBSERVED", "refs": ["E3"]}],
  "components": ["table: zebra rows, 40px row height, sticky header"],
  "breakpoints": ["768px", "1200px"], "direction": "ltr"}}
```

```bash
sh "${CLAUDE_PLUGIN_ROOT}/bin/moa7amed-queue" replica analyze <id> --data '<one line of JSON>'
```

## Ownership decides what may be reused

`replica status <id>` shows the ownership.

- **own / licensed:** the owner's assets (logo, images, fonts they hold rights to) may be
  migrated as supplied.
- **third_party / unknown:** create **original** branding. Choose a different primary
  colour and typeface where the original's are distinctive, keep spacing, layout and
  interaction patterns, and never copy logos, illustrations, icons sets that are not
  freely licensed, or text content. Record which tokens were changed on purpose.
