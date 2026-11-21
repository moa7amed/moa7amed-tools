# Claude plugin directory: submission materials

Prepared for the owner. **Nothing has been submitted**, and no icon has been saved in the
developer portal. Submitting is an owner action (steps at the end).

**Status:** the portal's validation of `main @ ebbfa8e` (2026-11-21) reported 4 blocking
findings, 3 warnings and 47 policy holds. The four blocking findings are publisher rules:
command hooks, files in `bin/`, `allowed-tools` beyond Read/Grep/Glob/LS, and scripts are
accepted only from a **Verified organization**. moa7amed-queue needs all four to work (the
`queue:` capture hook, the command launchers, the skills that run the queue's commands, and
the Python runner), so the full plugin cannot pass under an unverified publisher, whatever
is changed in the code. The plugin stays available, unchanged in function, from this
repository's own marketplace.

## Findings and what was done

| Finding (portal) | Kind | What it is at run time | Done | Remaining |
| :-- | :-- | :-- | :-- | :-- |
| Command hooks only from Verified organizations (`hooks/hooks.json`) | Publisher rule | The `queue:` capture hook (`UserPromptSubmit`) | Kept: it is the product's intake | Verified status |
| Files in `bin/` only from Verified organizations | Publisher rule | The `moa7amed-queue` launchers the skills and runners call | Kept | Verified status |
| `allowed-tools` beyond Read/Grep/Glob/LS only from Verified organizations (39 skills, one shown) | Publisher rule | Skills pre-approve the plugin's own command | Narrowed: each skill now pre-approves only the commands (and actions) it runs, never the command with arbitrary arguments; checked by `tests/test_plugin.py` | Verified status |
| Files outside the allowed list only from Verified organizations | Publisher rule | Python sources, shell and PowerShell scripts, test fixtures | Kept (the plugin is these programs) | Verified status |
| Pre-approves broad shell access (policy hold, 39) | Follows from the rule above | as above | Narrowed as above | Held until Verified |
| `allowed-tools` holds a `$` (39) | Follows from the rule above | `${CLAUDE_PLUGIN_ROOT}` in the rules | Kept (the documented way to name plugin files) | Held until Verified |
| Image or font file the plugin's code could run (policy hold, 3) | Probably the test fixtures (paths not shown in the portal) | DOCX, XLSX and PDF test fixtures are read as documents by the tests; nothing runs them. The only image, `assets/icon.png`, is checked: a plain PNG, nothing appended, named in no command (`tests/test_directory.py`) | Icon verified | Expanded report from the portal; see "Tests in the submission" |
| Files the validator could not inspect (`tests/fixtures/bid/quotation-hardware.xlsx`) | Packaging | A test fixture | Kept | See "Tests in the submission" |
| Scripts the validator could not follow (`hooks/capture-prompt.sh`) | Follows from the publisher rule | The capture hook script | Kept | Held until Verified |
| Uses a credential from the user's machine (policy hold, 3) | Follows from refused code | The plugin reads two values from Claude Code's settings files (`availableModels`/`deniedModels`, a marketplace's `autoUpdate`) and passes the environment to the `claude` sessions it starts, as any program starting Claude Code does. It stores and logs none of it | Tested: a key in `settings.json` or the environment never reaches the plugin's results or files (`tests/test_directory.py`) | Held until Verified |
| Contains a download-and-run command (warning, 2) | Defect | An install instruction for Playwright in an error message; `npx playwright test` in the replica-test skill | Fixed: both replaced by links to Playwright's documentation; a test fails if such a command returns | Re-validation |
| Field from another tool's manifest: `icon` (warning) | Notice | The directory reads it for the listing | Kept, as the portal says ("no action needed"); `privacyPolicyUrl` added the same way | — |
| Hook written as one line, not `command` and `args` (info) | Portability notice | `sh "${CLAUDE_PLUGIN_ROOT}/hooks/capture-prompt.sh"` | Kept: Claude Code runs the one-line `sh` form through Git Bash on Windows, while the `args` form only starts where Windows finds `sh` on PATH (the portal's own explanation) | — |
| Frontmatter value may be misread (`project-plan`, info) | Defect | `description` contained `: ` unquoted | Fixed: quoted (also `help`); every skill now parses as strict YAML, checked by a test | Re-validation |
| Images and fonts passed without a code check (`assets/icon.png`, info) | Notice | The listing icon | — | — |

The portal shows a sample of files per finding; it offers no export of the full report
(not documented). The "image or font" holds name no paths in the screenshot: confirm them in
the portal's expanded rows.

### Routes considered

- **A. Full plugin from a Verified organization (preferred).** The portal offers **Request
  verified** (Submissions → Help and appeals). Anthropic's documentation names two
  requirements, a paid plan and a proven domain, and says a person at Anthropic decides; it
  does not document whether an individual account can be verified, how a domain is proven,
  or what verification changes for plugins. A personal paid plan alone does not make an
  organization Verified.
- **B. A separate instruction-only edition.** Not built. Without hooks, launchers and the
  runner it could not save, persist, route or execute anything; it would be a different
  product under a similar name, and the skills would have nothing to call. It may be worth
  designing later as its own product (for example a planning or review method that works
  with Claude's read-only tools), not as a stand-in for moa7amed-queue.
- **C. Keep distributing the full plugin from this marketplace.** In effect now:
  `claude plugin marketplace add moa7amed/moa7amed-tools`.

### Tests in the submission

The directory scans the selected folder (`plugins/moa7amed-queue`) and some repository-wide
properties (archive size, file count, the root `.gitattributes`); no way to exclude paths
inside the folder is documented. The test suite (with its DOCX, XLSX and PDF fixtures) lives
in that folder today. Moving it out of the plugin folder would remove those files from the
scanned bundle and from installed copies without weakening any test, but it changes how the
suite, CI and the release checks find the plugin; it is worth doing together with the
verified submission, not as a way around the publisher rules.

## Listing

| Field | Value |
| :-- | :-- |
| Plugin name | `moa7amed-queue` |
| Short description | moa7amed-queue is an intelligent task orchestrator plugin for Claude Code: persistent queues, model routing, adaptive workflows and verified execution. |
| Long description | The plugin README ([plugins/moa7amed-queue/README.md](../plugins/moa7amed-queue/README.md)), which the directory shows as the listing description |
| Publisher | moa7amed |
| Contact | moa7amed@gmail.com |
| Website | https://moa7amed.com/ |
| Repository | https://github.com/moa7amed/moa7amed-tools (public) |
| Plugin folder | `plugins/moa7amed-queue` |
| Branch | `main` |
| License | MIT (`LICENSE` in the plugin folder; `license` in `plugin.json`) |
| Version | read from `plugin.json` |
| Icon | `plugins/moa7amed-queue/assets/icon.png` (`icon` in `plugin.json`): 1024 × 1024 PNG, about 23 KB, an original mark in the colours of the project's banner; editable source [media/source/icon.svg](media/source/icon.svg) |
| Screenshots and demo | [demo.md](demo.md) (silent videos with captions, real terminal output) and [media/images/auto-queue.png](media/images/auto-queue.png) (real output of an interactive session) |

![The listing icon](../plugins/moa7amed-queue/assets/icon.png)

The icon contains no Anthropic or Claude logo and no third-party artwork. **Choose
deliberately:** the plugin validator of Claude Code 2.1.329 says that the icon may become the
listing icon only once, the first time the plugin is saved or submitted in the developer
portal, and that changing it later does not change the listing icon.

## Runtime dependencies

- Claude Code (tested with 2.1.329). Queued tasks are executed by the Claude Code CLI with
  the user's own login.
- Python 3.8 or newer, standard library only.
- git (recommended: commits, snapshots, worktrees).
- A POSIX `sh` for the prompt hook (on Windows: Git for Windows).
- Optional: a PDF text extractor for BidStudio; Playwright with Chromium for Replica page
  captures and PDF output.

## Permissions

- One hook: `UserPromptSubmit`. A prompt that starts with `queue:` is saved as a task and
  blocked; every other prompt passes untouched (decided in a shell script, no data stored).
  Registered with `onFailure: "block"`, so a failing or timed-out hook never also sends a
  `queue:` prompt to Claude.
- Skills run only the plugin's own command (`Bash(sh "${CLAUDE_PLUGIN_ROOT}/bin/moa7amed-queue" *)`
  and its PowerShell equivalent). Most are invoked by the user only.
- Queued tasks run in separate `claude -p` sessions with the permission mode configured for
  the project (default `acceptEdits`: file edits, no shell commands unless allowed). Nothing is
  widened; no global Claude Code setting is changed.
- Auto Queue (off by default, per project) starts the runner in the background after a task is
  saved. No service, scheduled task or startup entry is installed.

## Network behaviour

- The plugin's own code makes no network requests and sends no telemetry.
- The Claude Code sessions it starts for tasks talk to the model provider configured in Claude
  Code, as any Claude Code session does.
- `moa7amed-queue update` runs Claude Code's own `claude plugin` commands, which fetch the
  marketplace.
- Replica's optional page capture loads, with Playwright, exactly the URLs the owner gives it.
- `network.hosts` is not declared in `plugin.json` (the validator lists this as information;
  it does not allow or block anything).

## Data, privacy and cost

- Stored locally only, in the user's data folder (`~/.moa7amed-queue`): queued prompts,
  acceptance criteria, results, run logs, costs as reported by Claude Code, project plans and
  bid/replica records. The plugin itself opens no network connection; the Claude Code
  sessions it starts send their task prompts to the configured model provider under the
  user's login, and Replica's optional capture loads the URLs the owner names. See
  [security-and-privacy.md](security-and-privacy.md).
- Personal data: only what users put into their own prompts and documents. No accounts, no
  analytics, no retention outside the user's machine.
- Cost: tasks use the user's Claude Code account. Limits per task, per runner session and an
  allowance for automatic runs are soft limits on the amounts Claude Code reports; they do not
  buy credits, reserve funds or cap spending at the provider. Runs that report no usage are
  shown as unknown.

## Known limitations

- Native integration is for Claude Code only; Codex and Cursor adapters are not implemented.
- Real Claude Code execution was verified on Windows 11 only; the test suites pass on
  Windows, Linux and macOS in CI.
- Auto Queue captures only explicit submissions; Claude Code puts a captured prompt back
  into the input box, which the user clears with Esc twice. Claude Code before 2.1.295 does
  not know `onFailure`, so there a prompt hook that runs out of time can let the prompt
  through.
- Updating between two published versions through Claude Code's background auto-update has
  not been observed yet.

## Owner steps (not done)

1. Ask for Verified status: https://claude.ai/directory/manage → **Submissions** → **Help
   and appeals** → **Request verified** (a paid plan and a proven domain; Anthropic decides).
   Questions: directory@anthropic.com.
2. Until then, the portal's four blocking findings stay; do not submit.
3. After verification (and after the current version is on `main`, since the portal scans
   the default branch): **Submit new → Plugin bundle**, source `moa7amed/moa7amed-tools`,
   plugin path `plugins/moa7amed-queue`, branch `main`; **Validate**, then review the
   remaining holds.
4. Check the icon preview before saving (see the note above), then fill in the
   data-handling questions and the compliance acknowledgements with the contact address
   above.
5. Submit. Every version is validated and scanned automatically; a person reviews a new
   listing before it goes live. Review time is not fixed.

Sources (read 2026-11-21): https://code.claude.com/docs/en/plugins/publish,
https://claude.com/docs/plugins/submit, https://claude.com/docs/plugins/pre-submission-checklist,
https://claude.com/docs/directory/publish, and the output of `claude plugin validate --strict`
(Claude Code 2.1.329).
