# Plugin development guide

How to add to a plugin in this marketplace, how to add a plugin, how to test either, and
how a change reaches users. Facts about Claude Code are taken from its documentation
(code.claude.com/docs) and were checked against Claude Code 2.1.206 and 2.1.329; where this
repository has tried something itself, that is said.

Read [architecture.md](architecture.md) first for the rules every plugin follows and why.

## Where things go

```text
plugins/moa7amed-<name>/
  .claude-plugin/plugin.json     the manifest, and nothing else in this folder
  release.json                   what this release declares about itself (see below)
  README.md  CHANGELOG.md  LICENSE
  skills/<skill>/SKILL.md        skills
  agents/<agent>.md              agents
  hooks/hooks.json               hooks
  .mcp.json                      MCP servers
  bin/                           executables; on the Bash tool's PATH while the plugin is enabled
  scripts/                       the plugin's own code
  tests/                         its tests
```

Only `plugin.json` belongs in `.claude-plugin/`; components put there do not load. A
`CLAUDE.md` at the plugin root is not loaded as context: if Claude needs to know
something, it goes in a skill.

## What each kind of component costs

Decide what to add by what it costs the people who install the plugin, in every session,
whether they use it that day or not.

| Component | In every session | When used |
| :-- | :-- | :-- |
| Skill Claude may invoke | Its name and description | Its body, which then stays in the conversation |
| Skill with `disable-model-invocation: true` | Nothing | Its body |
| Agent | Its name and description | Runs in its own context |
| Hook | Nothing in the model's context. Time: the command runs on every matching event | |
| MCP server | A running process per session, the tool names and the server's instructions; tool definitions are deferred until needed (tool search, on by default) | Tool definitions and results |

So, in order of preference for a new capability:

1. A command in the plugin's command line, reached through a **skill**. Testable, costs
   one description.
2. A **hook**, when something must happen without Claude deciding to do it. Costs no
   tokens, but its run time is added to the event, so the common case must be fast.
3. An **agent**, when a task needs its own context window or a different model.
4. An **MCP server**, only when Claude needs tools that a command cannot provide.
   It is the only component that keeps a process running.

`claude plugin details <plugin>@<marketplace>` prints Claude Code's estimate of the
first column. `release.json` holds a budget for it (`always_on_token_budget`) which the
`clean-install` release check enforces. Note that the estimate counts every skill,
including the user-only ones that, according to the documentation, are not in context at
all; the real figure is therefore lower than the estimate.

## Adding a skill

See [skills-development.md](skills-development.md). In short: add the command first,
then a `SKILL.md` that runs it, keep the description to one sentence, register the name
in `tests/test_plugin.py`.

## Adding an agent

No plugin here ships an agent yet. When one does:

```markdown
---
name: reviewer
description: Reviews a diff for defects. Use after a change is complete.
tools: Read, Grep, Glob
model: sonnet
---

You review code changes. ...the system prompt...
```

- File: `agents/<name>.md`. It is addressed as `<plugin>:<name>`.
- The description is in every session, like a skill's. One sentence.
- Give it the fewest tools that do the job. An agent that only reads should not be able
  to write.
- In a plugin agent, `permissionMode`, `hooks` and `mcpServers` in the frontmatter are
  ignored by Claude Code. Ship hooks in `hooks/hooks.json` and servers in `.mcp.json`.
- The system prompt cannot be unit-tested. Keep decisions that matter in code the agent
  calls, and test that code. Check the agent itself by hand with
  `claude --plugin-dir plugins/<plugin> --agent <plugin>:<name>`.

## Adding a hook

`hooks/hooks.json`:

```json
{
  "description": "what the hook does, in one sentence",
  "hooks": {
    "UserPromptSubmit": [
      {
        "hooks": [
          {
            "type": "command",
            "command": "sh \"${CLAUDE_PLUGIN_ROOT}/hooks/capture-prompt.sh\"",
            "timeout": 30
          }
        ]
      }
    ]
  }
}
```

Rules learned from the capture hook of `moa7amed-queue`:

- **It runs for everyone, on every event it matches, whether or not they use the
  feature.** Decide the common case in the cheapest possible way and exit. The capture
  hook is a shell script that compares the beginning of the prompt and exits; it starts
  Python only for a prompt it is going to handle. Measured on the development machine:
  about 95 ms for an ordinary prompt, of which about 50 ms is starting `sh` itself.
- **Measure before and after** (`python tests/measure_overhead.py`). In 0.3.0 the hook
  fell back to Python for every prompt as soon as any setting had been saved, which
  nobody had noticed: roughly 300 to 600 ms per prompt instead of about 100. A regression test now
  fails if an interpreter is started for a prompt the hook has no reason to look at.
- **Never fail the user's action because the hook failed.** Exit 0 and say nothing when
  there is nothing to do. A hook that blocks must say why and must not lose what the
  user typed.
- **Never capture inside your own automation.** The runner sets an environment variable
  and the hook exits immediately when it sees it.
- Always call scripts through `${CLAUDE_PLUGIN_ROOT}`; the install path changes with
  every update. Unix line endings for anything `sh` runs (`.gitattributes` enforces it).
- A hook can run where there is no POSIX shell. State the requirement in the README and
  make everything else work without the hook.

Test hooks the way Claude Code calls them: the event JSON on standard input, the
decision on standard output (`tests/test_hook.py`).

## Adding an MCP server

No plugin here ships one, and the bar is high: a server is a process that starts with
every session of every user.

If one is justified, declare it in `.mcp.json` at the plugin root:

```json
{
  "mcpServers": {
    "queue": {
      "command": "python",
      "args": ["${CLAUDE_PLUGIN_ROOT}/scripts/server.py"]
    }
  }
}
```

- Standard library only, like the rest of the plugin: there is no install step in which
  dependencies could be fetched.
- It must start in well under a second and must not fail the session when its data is
  missing.
- Few tools, short descriptions. Tool definitions are loaded on demand, but tool names
  and the server's instructions are in every session.
- Its tools must be the same operations the command line offers, implemented by the
  same functions, so that the tests of the command line cover them.
- Add its cost to the README: `claude plugin details` does not count MCP tool schemas.

## Where a plugin keeps data

| Kind of data | Where | Why |
| :-- | :-- | :-- |
| What the user would be upset to lose (queues, history, settings) | `~/.<plugin-name>/`, overridable by `<PLUGIN_NAME>_HOME` | Survives uninstalling the plugin, and is reachable by programs that run outside Claude Code |
| Caches, downloaded dependencies | `${CLAUDE_PLUGIN_DATA}` | Kept across updates; removed by Claude Code when the plugin is uninstalled |
| Nothing | `${CLAUDE_PLUGIN_ROOT}` | A new folder for every version |

`moa7amed-queue` uses the first row for everything: its runner is started from a
terminal, where `${CLAUDE_PLUGIN_DATA}` does not exist, and a queue must not disappear
because a plugin was reinstalled. This repository has verified that uninstalling the
plugin, and removing its marketplace, leave `~/.moa7amed-queue` untouched.

Stored data needs a format number and a compatibility rule from the first release; see
[versioning.md](versioning.md).

## release.json

Each plugin declares, next to its manifest, what tooling needs to know about a release:

```json
{
  "name": "moa7amed-queue",
  "version": "0.3.1",
  "channel": "stable",
  "data_format": "1",
  "reads_data_formats": ["1"],
  "min_python": "3.8",
  "tested_claude_code": "2.1.329",
  "always_on_token_budget": 400,
  "isolation_env": { "MOA7AMED_QUEUE_HOME": "{tmp}/queue-home" },
  "smoke": [["{python}", "-B", "scripts/mq.py", "doctor"]]
}
```

| Field | Used by |
| :-- | :-- |
| `version`, `channel` | `release_check.py` (must match `plugin.json`); the plugin's own `update` command |
| `data_format`, `reads_data_formats` | The plugin, to say before an update whether the offered version can take over the data |
| `tested_claude_code` | `doctor`, the CI workflows |
| `always_on_token_budget` | The `clean-install` check |
| `isolation_env`, `smoke` | The `clean-install` and `update` checks: commands run inside the installed copy, with the plugin's data directory pointed at a temporary folder |

It is a separate file because `plugin.json` is validated strictly by Claude Code and
fields it does not know fail validation.

## Testing

| Level | What | How |
| :-- | :-- | :-- |
| Unit and integration | The plugin's code, with a stand-in for `claude` | `cd plugins/<plugin> && python -B -m unittest discover -s tests -t tests` |
| Package | Manifest, skills, hook wiring, nothing personal, context budget | `tests/test_plugin.py`, `claude plugin validate plugins/<plugin> --strict` |
| Marketplace | Every plugin listed once, independent, conventions kept | `python -B -m unittest discover -s tests -t tests` at the repository root |
| Release | Install, update, upgrade from earlier versions, secrets | `python scripts/release_check.py --worktree` |
| Live | The real `claude` on throwaway projects. Costs money | `python tests/live_validation.py --budget 1.00` |
| By hand | A real session with the plugin loaded, not installed | `claude --plugin-dir plugins/<plugin>`, then `/reload-plugins` after edits |

Rules for tests in this repository:

- No network, no Claude account, no real project. Every test creates its own data
  directory, its own Claude Code profile and its own git repositories in a temporary
  folder.
- A defect is reproduced by a failing test before it is fixed.
- A stand-in is only as good as its resemblance to the real thing. The live validation
  exists because it found what the stand-in could not: a task that modified an existing
  file (the stand-in tests only created new ones) had its file name recorded with the
  first letter missing.
- Measurements are recorded with the machine and date they were taken on, and are not
  presented as guarantees.
- `claude --plugin-dir` must point at the plugin folder. Pointed at the repository
  root it loads nothing and reports no error.

## Publishing a change

A change reaches users only when the plugin's version changes. The steps are in
[release-checklist.md](release-checklist.md); the meaning of the numbers in
[versioning.md](versioning.md).

In review, ask of every change:

1. Does it change anything in the "What users can rely on" table? Then it is not a patch
   release.
2. Does it add to what every session carries or to what every prompt waits for? Measure.
3. Can the previous version still read the data afterwards? The upgrade check answers.
4. Is there a test that fails without it?

## Adding a plugin

1. Create `plugins/moa7amed-<name>/` with the layout at the top of this page. Start at
   version `0.1.0`.
2. Add it to `.claude-plugin/marketplace.json`, without a `version`:

   ```json
   {
     "name": "moa7amed-<name>",
     "source": "./plugins/moa7amed-<name>",
     "description": "...",
     "license": "MIT"
   }
   ```

3. List it in the table in the root `README.md`.
4. Give it a `tests/test_plugin.py` with a context budget, and a `release.json`.
5. Run `python scripts/release_check.py --worktree --plugin moa7amed-<name>`.

The marketplace tests check the rest: the folder, the manifest and the entry agree; the
plugin is complete on its own; it does not refer to another plugin's folder.

### Conventions every plugin follows

| Thing | Rule | For `moa7amed-queue` |
| :-- | :-- | :-- |
| Plugin name | `moa7amed-<name>`, lowercase, hyphens | `moa7amed-queue` |
| Command | The plugin name. Short aliases are opt-in and never shadow an existing program | `moa7amed-queue` |
| Skills | `<area>-<verb>`, addressed as `/<plugin>:<skill>` | `/moa7amed-queue:queue-add` |
| Data directory | `~/.<plugin name>` | `~/.moa7amed-queue` |
| Environment variables | `<PLUGIN_NAME>_...` | `MOA7AMED_QUEUE_HOME` |
| Markers in model output | A prefix of the plugin's own | `MQ_RESULT:` |
| Dependencies | Python 3.8+ standard library; no install step | |
| Shared code | None. No common library folder, no imports from another plugin | |
| Working together | Through documented commands and files, never through imports. A plugin must be fully useful when it is the only one installed | |

### Plugins that are planned, not built

These names are reserved so that nothing else takes them. Nothing exists for them yet,
and nothing in this repository depends on them.

| Plugin | Intended purpose | Would use |
| :-- | :-- | :-- |
| `moa7amed-review` | Code review of a change before it is committed | command `moa7amed-review`, data `~/.moa7amed-review`, variables `MOA7AMED_REVIEW_*` |
| `moa7amed-ui` | Checking user interface work against what was asked for | command `moa7amed-ui`, data `~/.moa7amed-ui`, variables `MOA7AMED_UI_*` |
| `moa7amed-qa` | Running and judging test plans | command `moa7amed-qa`, data `~/.moa7amed-qa`, variables `MOA7AMED_QA_*` |
| `moa7amed-release` | Release preparation for a user's own project | command `moa7amed-release`, data `~/.moa7amed-release`, variables `MOA7AMED_RELEASE_*` |

Two notes for whoever builds them:

- If one of them wants to hand work to the queue, it does so by running
  `moa7amed-queue add` when that command exists, and works without it when it does not.
  That is a documented command, so the queue can change inside without breaking them.
- Resist extracting a shared library when the second plugin needs a helper the first
  one has. Copy it. Extract only when three plugins have needed the same thing in the
  same form for some time, and then as a versioned package, not as a folder two plugins
  reach into.
