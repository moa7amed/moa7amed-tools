# Versioning policy

How version numbers are chosen, what users can rely on between versions, and how the
stable and beta channels work.

## One version per plugin, written once

Every plugin has its own version, in `plugins/<name>/.claude-plugin/plugin.json`. Claude
Code reads it there first, so that is the only place it is stated for Claude Code. The
marketplace entry carries **no** `version`: a second copy can only disagree, and when it
does Claude Code silently uses the one in `plugin.json`.

Inside a plugin two more files repeat the number for the plugin's own use
(`release.json`, and `__version__` in the code). `scripts/bump_version.py` writes all of
them together and `scripts/release_check.py` fails when they differ.

**The version is what delivers an update.** Claude Code offers a plugin update when the
version string in the marketplace differs from the installed one. A change committed
without a new version reaches nobody; a version reused for different content reaches
only new installers. The `changelog` release check refuses both.

## What the numbers mean

Versions follow `MAJOR.MINOR.PATCH`, with an optional pre-release suffix
(`0.4.0-beta.1`).

While the major version is 0 (a MINOR bump may also mark a substantial new capability
that changes nothing for existing users, as 0.4.0 did):

| Change | Bump | Examples |
| :-- | :-- | :-- |
| Compatible: a user who updates without reading anything is fine | **PATCH** | Fixes; new commands, settings and options with defaults that keep earlier behaviour; performance; documentation |
| Something a user must know before updating | **MINOR** | A changed default; a renamed or removed command, skill, setting or environment variable; a data format the previous release cannot read; a new required dependency |

From 1.0.0 on, standard semantic versioning applies: MINOR for compatible additions,
MAJOR for anything in the second row.

A release in the second row always ships with: the old name kept as an alias where a
name changed, an upgrade note in the README, and a changelog entry that begins with what
the user has to do.

### 0.3.1, as an example

0.3.1 adds commands and settings, changes nothing a 0.3.0 user relies on, reads and
writes the same data format, and can be rolled back to 0.3.0. That is the first row, so
it is a patch release.

## What users can rely on

These are the plugin's public surface. Changing any of them is a second-row change.

| Surface | moa7amed-queue |
| :-- | :-- |
| Command names, options and exit codes | `moa7amed-queue ...`, the alias `ctq`, runner exit codes 0, 1, 2, 3, 4, 5, 130 |
| Skill names | `/moa7amed-queue:queue-add` and the nine others |
| Setting names and their defaults | `moa7amed-queue config` (0.3.2 added `on_interrupted`, default `retry`, which keeps earlier behaviour) |
| Environment variables | `MOA7AMED_QUEUE_*`, and the legacy `CTQ_*` |
| Data location and format | `~/.moa7amed-queue`, format 1 |
| What is written into your repository | the `Task-Queue-Id` commit trailer, the `task-queue` worktree branch |
| What a task is asked to print | `MQ_RESULT:` and `MQ_ESCALATE:` (and the legacy `CTQ_` spellings) |
| Text of `--json` output | Fields are added, not renamed or removed |

Human-readable output (tables, messages, log lines) is not part of the surface and may
be reworded in any release.

## Data format

The queue database carries a format number. `release.json` declares, for each release,
the format it writes (`data_format`) and the formats it can read
(`reads_data_formats`).

- **Adding** tables or columns next to the existing ones does not change the format
  number, because an older release keeps working on such a database. 0.2.0 (routing
  tables), 0.3.1 (process identity) and 0.4.0 (plans) were all additions. The upgrade tests prove this
  for every release by running the previous version against a queue the new one has
  used.
- **Changing or removing** something an older release reads is a new format number, a
  MINOR release at least, and needs: a migration that runs once, a backup before it
  (automatic, see below), an upgrade test from every supported earlier release, and a
  note that rolling back afterwards requires restoring the backup.
- A release must read the format of the release before it. Skipping releases when
  updating must work.

On the first run after any version change, the plugin copies settings and queues to
`backups/` before it writes anything (`backup_on_update`, on by default).
`moa7amed-queue update` compares the offered release's `reads_data_formats` with the
data on disk and says before the update whether a migration will happen.

## Channels

| | Stable | Beta |
| :-- | :-- | :-- |
| Branch | `main` | `beta` |
| Marketplace name (in `marketplace.json` on that branch) | `moa7amed-tools` | `moa7amed-tools-beta` |
| Versions | `1.2.3` | `1.3.0-beta.1`, `-beta.2`, ... |
| Users add it with | `moa7amed/moa7amed-tools` | `moa7amed/moa7amed-tools#beta` |
| Meant for | Everyone | People who agreed to test |

The two marketplaces have different names because Claude Code identifies a marketplace
by its name; that is also what lets one person have both. A marketplace added with
`#beta` follows the latest commit of that branch.

Rules, enforced by `release_check.py`:

- A pre-release version never appears in the stable marketplace.
- `1.3.0-beta.2` sorts after `1.3.0-beta.1` and before `1.3.0`.
- A beta is promoted by releasing the same code as `1.3.0` on `main`, never by renaming
  a tag.

The beta branch does not exist yet. It is created when the first pre-release is needed:
branch from `main`, change the marketplace `name` to `moa7amed-tools-beta` in that
branch only, and keep that one-line difference when merging `main` into `beta`.

## Tags and releases

A release is tagged `<plugin>--v<version>` (`moa7amed-queue--v0.3.1`) with
`claude plugin tag`, which checks the manifest before tagging. The tag starts the
release workflow, which repeats every check on the tagged commit and records a GitHub
release with the changelog entry as its notes.

Tags are a record and the anchor for rollback (a marketplace can be added at a tag).
They are not what delivers an update; the version in `plugin.json` on the branch is.
Nothing is released automatically: without a version change in a commit, and without a
tag pushed by a maintainer, no user receives anything and no release is recorded.
