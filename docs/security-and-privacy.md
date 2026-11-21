# Security and privacy

What the plugins in this repository store, start and send. Statements here describe the
code as it is and are meant to be checkable; where a statement is enforced by a test or
a release check, that is named.

## moa7amed-queue

### What is stored, and where

Everything is stored on your machine, in `~/.moa7amed-queue` (or where
`MOA7AMED_QUEUE_HOME` points). Nothing is stored anywhere else.

| Data | Where | Notes |
| :-- | :-- | :-- |
| The prompts you queue, and their acceptance criteria | `projects/<key>/queue.db` | Kept verbatim and never rewritten |
| What Claude answered for each run, its error stream | `projects/<key>/runs/` | Plain files, one set per run |
| Session ids, cost and token figures reported by Claude Code | `queue.db` | Recorded as reported; nothing is estimated |
| Commit hashes and the paths of files a task changed | `queue.db` | No file contents |
| Project paths, host name, process ids of runners | `queue.db`, `project.json` | Used for locking and recovery |
| Settings | `config.json` | No credentials: there is no setting that holds one |
| Backups | `backups/` | Copies of the queues and settings, so they contain your prompts |

Treat the data directory like the projects it describes: prompts often quote code and
describe unreleased work. It inherits the permissions of your home directory; the plugin
adds no protection of its own on a shared machine.

To remove everything: delete the data directory. `claude plugin uninstall` removes the
plugin and leaves your data.

### What leaves your machine

The plugin itself makes **no network connection**. It contains no HTTP client, no
telemetry and no update check of its own. `tests/test_plugin.py` fails if a module that
can open a connection is imported anywhere in the shipped code, or if a git command that
talks to a remote is added. The one exception is Replica's page capture (below): a
separate program, `scripts/replica_browser.py`, that drives a browser, and the same test
fails if browser automation is imported anywhere else.

It starts other programs, and two of them use the network on your behalf:

| Program | When | What it sends |
| :-- | :-- | :-- |
| `claude` | To run a task, and for the optional one-line review of a routing decision | The task prompt, exactly as if you had typed it, under your own Claude Code login and its data policies |
| `claude plugin marketplace update` | Only when you run `moa7amed-queue update --refresh` | Claude Code contacts the marketplace's git host |
| `claude plugin list`, `claude plugin marketplace list`, `claude --version` | `moa7amed-queue update` and `doctor` | Nothing: they read Claude Code's local state |
| `git` | Before and after each task | Local commands only: status, commit, stash, worktree. The plugin never runs `git push`, `git fetch` or `git pull` |
| A browser, through Playwright (`scripts/replica_browser.py`) | Only when you run `moa7amed-queue replica capture` (or `replica compare` with a URL); `bid generate` uses the same program to print local HTML to PDF with every network request blocked | Loads the URLs you named, as an anonymous visitor: a fresh browser context each time, no stored cookies or logins, no form input, no link following. The page loads what it loads (images, scripts) as in any browser |

`moa7amed-queue update` without `--refresh` reads the marketplace copy Claude Code
already keeps on disk; it does not contact anything.

### Credentials

The plugin has no credentials and asks for none. Claude Code authenticates itself the
way it does in any terminal. A task's process inherits the environment of the runner
that started it, so anything you export in that shell is visible to the task, as it
would be to an interactive session started from the same shell.

### What a task is allowed to do

Tasks run unattended, so the permission mode matters more than in an interactive
session.

- Default: `--permission-mode acceptEdits`. Claude may edit files in the workspace
  without asking; other tools follow your Claude Code permission settings, and a tool
  that would need a prompt is denied because nobody is there to answer.
- You can change the mode (`permission_mode`), allow or deny specific tools
  (`allowed_tools`, `disallowed_tools`) and cap spending (`max_budget_usd` per task,
  `cost_limit_run_usd` per runner session).
- The plugin never adds `--dangerously-skip-permissions`. If you put it in
  `claude_args` yourself, tasks run without any permission check; do that only in a
  disposable environment.
- Worktree mode (`--workspace worktree`) runs tasks on a separate branch in a separate
  directory, which keeps unattended edits away from the files you are working on.

Queued prompts are instructions that will be executed later with those permissions.
Anyone who can write to your data directory, or type into a session where the capture
prefix is active, can queue work. Review `moa7amed-queue list` before starting a runner
on a queue you did not fill yourself.

### Your uncommitted work

A task never starts in a workspace with uncommitted changes unless you chose `stash` or
`allow`. Changes are committed or stashed, never discarded: the code contains no
`git reset`, `git checkout --`, `git clean` or `git stash drop`. After a crash, files a
finished task left behind are reported and left untouched, and no task starts on top of
them under any `on_dirty` setting. They are committed or stashed only when you run
`moa7amed-queue reconcile --commit --yes` or `--stash --yes`, and `--commit` takes only
the files that changed while the task ran.

### Prompts you queue while Claude is working

The capture hook stores a `queue:` prompt as soon as you press Enter. Claude Code then
puts the prompt back into your input box (observed with 2.1.329); the plugin refuses to
store the same text twice from one session and tells you so. Ordinary prompts are never
stored by the plugin.

### The capture hook

The hook sees every prompt you submit, because that is how Claude Code hooks work. It
compares the beginning of the prompt with the capture prefix (`queue:` by default) in a
shell script. A prompt that does not match is passed on untouched: it is not stored,
logged or modified, and no further program is started. Set `capture_prefix` to `off` to
disable capture entirely.

One exception: while a queued task waits for your decision on a concern (see
[challenge.md](challenge.md)), a marker file exists and the hook reads ordinary prompts
too. A prompt that clearly states a decision is stored in the project's queue database
(`owner_statements`) as the record of your answer, and the decision is applied; the
prompt still reaches Claude unchanged. Every other prompt is passed on and not stored.
When no decision is open, nothing changes from the paragraph above.

### Decisions on concerns

Only the owner's answer counts. A decision command is refused inside a queued task or an
agent (they carry `MOA7AMED_QUEUE_RUNNER`), and from a shell started by Claude Code unless
it refers to a prompt the owner typed and the hook recorded (used once, within two
hours). This stops a session from deciding on the owner's behalf; it is not a protection
against a program that can write the queue database directly. A decision never changes
tool permissions, plan approvals, the verification check or cost limits.

### Project plans

Plans (requirements, infrastructure details, decisions, tasks) are stored in the project's
queue database in the data directory, like tasks. They can contain sensitive details of
your infrastructure: treat them like the rest of the data directory. `project report`
writes Markdown and JSON to the data directory, or into your project only with
`--to-project`. Discovery reads files inside the project folder only, runs nothing, and
contacts no server; the planning skills are told never to inspect a remote or production
server without your explicit authorisation.

### Replica (application reconstruction)

- **References** (screenshots, recordings, documents, captured pages and their page
  source) are copied into `projects/<key>/replica/<id>/` in the data directory, never into
  the application being built and never into the plugin package. Source folders are
  fingerprinted where they are, not copied. Each stored file's SHA-256 is recorded; a file
  changed after intake is reported.
- **Access:** a capture never signs in, fills forms, solves CAPTCHAs or follows links; a
  page that asks for a login is recorded as a limitation. URLs that carry credentials
  (`?token=`, `?session=`...) are refused, and notes that look like credentials are
  redacted before they are stored. No cookies or session storage are kept.
- **Task sessions** of a replica project get read access to that project's reference
  folder (`claude --add-dir`), and to nothing else outside their project.
- **Prompts** carry the inventory entries a task needs and the paths of its reference
  images, not the content of reference documents. Captured page text is data for the
  analysis, never instructions; the skills say so.
- **Third-party references:** the generated tasks require original code, branding,
  artwork and text, and `replica sweep` looks in the rebuild for the reference's domain
  names, page titles, given brand terms and byte-identical files. It finds what it
  looks for; it does not prove a clean room.
- **Personal data:** tasks are told to use synthetic data only. Nothing extracts or
  exports data from a reference application.

### Multi-agent agents (read-only)

Analysis agents are Claude Code sessions with less permission than their task: only the
Read, Grep and Glob tools exist in them, editing, shell, web and subagent tools are
denied, the permission mode is `dontAsk` (nothing is prompted, anything not
pre-approved is denied), and the task's own permission mode, allowed tools and extra
directories are not passed on. In a git project each agent works in a detached snapshot
of `HEAD` stored in the queue's data directory, removed afterwards; the live workspace is
compared before and after and any change holds the task. Agents cannot run or join queue
runners and cannot approve, generate, apply, publish or submit anything. Shared project
state (bid sections and requirements, Replica screens) is changed only by the queue
process, after revision and fingerprint checks. These controls are Claude Code's own tool
and permission rules plus a separate checkout, not an operating-system sandbox; MCP
servers configured in your own settings are outside them.

### BidStudio (tenders and proposals)

- Tender documents, their extracted text, the bid record, generated proposals and
  workbooks are stored in `projects/<key>/bids/<id>/` in the data directory. They are
  confidential business documents: treat the data directory accordingly.
- Company profiles and rate cards are in `companies/<id>/` in the data directory, never
  in a project, an output folder shared with customers, or the plugin package. Several
  companies are kept in separate folders.
- The pricing model workbook is named `...-pricing-model-INTERNAL.xlsx` and marked
  RESTRICTED. Customer documents are built from selling prices only, and each generated
  file is checked for internal costs, markups, margins, contingency and supplier
  references (a BLOCKER if found); the technical envelope is checked for prices.
- Tasks of a bid project may run only the `bid` and `project` commands of this plugin
  (`--allowedTools`) and use the bid's folder (`--add-dir`).
- Nothing is uploaded, sent or submitted by the plugin. What leaves your machine is what
  Claude reads in your sessions and tasks, under your Claude Code login and its data
  policies (the same as for any file you open with Claude).

### Backups

`moa7amed-queue backup` and the automatic backup after an update write to `backups/`
inside the data directory. They contain the queue databases (your prompts, and the
result text recorded for each run) and the settings. The per-run output files and logs
are not included. Backups are never uploaded. Delete them like any other folder.

## What is checked before a release

`python scripts/release_check.py` runs these on every push in CI and again on a release
tag:

| Check | Looks for |
| :-- | :-- |
| `secrets` | API keys and tokens of common providers, private key blocks, password assignments, e-mail addresses, Windows and Unix user folders, and the home folder and git e-mail address of the machine the check runs on |
| `package` | Generated files, databases, logs, environment files, key files, oversized files |
| `versions`, `changelog` | A release that silently reuses a version number |

A pattern scan cannot prove the absence of a secret. It catches the common accidents;
reviewing the diff of a release remains necessary.

The GitHub workflows use the repository's own short-lived token and no stored secrets.
The CI workflow can only read the repository. The release workflow can additionally
create a release entry; it cannot push commits.

## Reporting a problem

Please report a suspected vulnerability privately, through GitHub's "Report a
vulnerability" form on the repository's Security tab, rather than in a public issue.
Include the plugin version (`moa7amed-queue --version`) and, if you can, the output of
`moa7amed-queue doctor`.
