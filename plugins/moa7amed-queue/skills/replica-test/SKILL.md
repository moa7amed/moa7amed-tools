---
name: replica-test
description: Verify a rebuild's features and flows with real tests and record the feature parity matrix.
argument-hint: "<replica id> [JUnit report]"
disable-model-invocation: true
allowed-tools:
  - Bash(sh "${CLAUDE_PLUGIN_ROOT}/bin/moa7amed-queue" *)
  - PowerShell(& "${CLAUDE_PLUGIN_ROOT}/bin/moa7amed-queue.cmd" *)
  - Read
---

# Replica functional parity

Requested: $ARGUMENTS

A screenshot match is not evidence that anything works. A feature is **verified** only
when a real test (a browser or HTTP client against the running rebuild) passed. A test
against a mock is recorded as a mock; a clickable button proves nothing about the backend.

## 1. The tests

The plan's test task writes browser or API tests named with the feature and flow ids
(`test_F3_filter_by_status`, `U1 find and open a shipment`), with "mock" in the name when
they exercise a mock. The owner (or a queue task allowed to run commands) runs them with a
JUnit report, for example `python -m pytest --junitxml=report.xml` or
`npx playwright test --reporter=junit`.

## 2. Record the results

```bash
sh "${CLAUDE_PLUGIN_ROOT}/bin/moa7amed-queue" replica test <id> --junit <report.xml>
sh "${CLAUDE_PLUGIN_ROOT}/bin/moa7amed-queue" replica test <id> --feature F3 --result pass|fail --kind real|mock --evidence "<what was run>"
```

A skipped check is recorded as nothing, never as a pass.

## 3. Report

```bash
sh "${CLAUDE_PLUGIN_ROOT}/bin/moa7amed-queue" replica status <id>
sh "${CLAUDE_PLUGIN_ROOT}/bin/moa7amed-queue" replica report <id>
```

Report the parity matrix: verified, built but not verified, mock, failing, unknown; the
flows; and what is missing to verify the rest. For a third-party reference, run
`replica sweep <id> --term "<brand name>"` and report what it finds.
