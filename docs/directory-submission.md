# Claude plugin directory: submission materials

Prepared for the owner. **Nothing has been submitted**, and no icon has been saved in the
developer portal. Submitting is an owner action (steps at the end).

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
  bid/replica records. Nothing is sent anywhere by the plugin. See
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

1. Sign in at https://claude.ai/directory/manage with a paid plan (Pro, Max, Ultra; on Team or
   Enterprise an Owner, or a role with the Directory permission).
2. **Submit new → Plugin bundle.**
3. Source: `moa7amed/moa7amed-tools`, plugin path `plugins/moa7amed-queue`, branch `main`.
4. Check the icon preview before saving (see the note above), then fill in the data-handling
   questions and the compliance acknowledgements with the contact address above.
5. Submit. Every version is validated and scanned automatically; a person reviews a new
   listing before it goes live. Review time is not fixed. Questions: directory@anthropic.com.

Sources (read 2026-11-21): https://code.claude.com/docs/en/plugins/publish,
https://claude.com/docs/plugins/submit, https://claude.com/docs/plugins/pre-submission-checklist,
https://claude.com/docs/directory/publish, and the output of `claude plugin validate --strict`
(Claude Code 2.1.329).
