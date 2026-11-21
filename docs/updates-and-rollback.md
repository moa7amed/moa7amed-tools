# Updates and rollback

How a plugin from this marketplace is updated, how to make that automatic, how to check
an update, and how to go back.

Claude Code performs every update. The plugins contain no updater of their own:
`moa7amed-queue update` reports and advises, it never downloads or installs.

> **Status.** The marketplace is hosted on GitHub. Adding it from GitHub, installing
> 0.8.0 and running `claude plugin marketplace update` / `claude plugin update` against it
> were verified on 2026-11-21 in an empty profile. Following a branch or tag and the
> background auto-update to a later version are described from the Claude Code
> documentation and have not been exercised yet: they need a second published version.
> What has been exercised with the real Claude Code CLI and a marketplace in a local
> folder is marked **verified**.

## Updating by hand

```bash
moa7amed-queue update                                 # what is installed, what is offered, is it safe
moa7amed-queue backup                                 # optional: the plugin also backs up by itself
claude plugin marketplace update moa7amed-tools       # refresh the listing
claude plugin update moa7amed-queue@moa7amed-tools    # install the offered version
```

Then restart Claude Code, or run `/reload-plugins` in a running session, and check:

```bash
moa7amed-queue doctor
```

Inside a session the same is done from `/plugin`: the **Marketplaces** tab has
**Update marketplace**, the **Installed** tab has **Update now**.

**Verified:** with 0.3.0 installed from a marketplace folder and 0.3.1 then placed in
that folder, these two `claude` commands reported `updated from 0.3.0 to 0.3.1`; the
queue written by 0.3.0 was intact, and 0.3.1 backed it up on its first run.
`python scripts/release_check.py --only update` repeats this.

### What `moa7amed-queue update` tells you

```text
moa7amed-queue 0.3.1 (stable channel)
  installed:    moa7amed-queue@moa7amed-tools, version 0.3.1, user scope, enabled
  marketplace:  moa7amed-tools (github moa7amed/moa7amed-tools)
  listing from: 2026-11-20 10:31:14  (--refresh asks the marketplace again)
  offered:      0.3.2  ->  an update is available
  auto-update:  off (Claude Code updates a third-party marketplace automatically only after you turn that on)

What is new
  0.3.2
    ...the changelog entry of the offered version...

Before updating
  [ok  ] compatibility        same data format (1): no migration, and going back is possible
  [ok  ] no runner is active  no runner is working on any project
  [ok  ] queues are readable  2 project queue(s) open and pass their integrity check
  [ok  ] data format          all queues use data format 1 ...
  [ok  ] backup               latest backup: 20261120-063118-v0.3.1-...
```

- **offered** is the version in the copy of the marketplace Claude Code keeps on this
  machine, as of the time shown. `--refresh` runs `claude plugin marketplace update`
  first. Nothing else contacts the network.
- **compatibility** compares what the offered version says it can read
  (`release.json`) with the data on disk. `FAIL` means: do not update yet.
- **no runner is active**: update between runs. Claude Code installs a new version into
  its own folder and leaves the previous one in place, so a runner started before the
  update goes on running the previous version's code while new commands use the new
  one. Two versions working on one queue at the same time is not something the tests
  cover.

## Updating automatically

Claude Code updates its official marketplace by itself. For every other marketplace,
this one included, automatic updates are **off until you turn them on**:

1. Run `/plugin` in a Claude Code session.
2. Open the **Marketplaces** tab and select `moa7amed-tools`.
3. Turn auto-update on.

Or declare it in your settings (`~/.claude/settings.json`), which also works for a team
through a project's `.claude/settings.json`:

```json
{
  "extraKnownMarketplaces": {
    "moa7amed-tools": {
      "source": { "source": "github", "repo": "moa7amed/moa7amed-tools" },
      "autoUpdate": true
    }
  }
}
```

What then happens, according to the Claude Code documentation:

- In an interactive session, some time after your first message (a random delay of up
  to ten minutes), Claude Code refreshes the marketplaces that have auto-update on and
  updates the plugins installed from them, on disk.
- You see `Plugin updated: moa7amed-queue · Run /reload-plugins to apply`. Reload, or do
  nothing: the new version loads at the next launch either way.
- The whole pass is switched off by `DISABLE_UPDATES=1`, `DISABLE_AUTOUPDATER=1` or
  `CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC` unless `FORCE_AUTOUPDATE_PLUGINS=1` is also
  set. `moa7amed-queue update` shows whether one of them is set in your environment.
- For a private repository the background check uses your git credential helpers and
  never prompts. A token in an environment variable is not enough by itself; for GitHub
  run `gh auth login` and `gh auth setup-git`.

`moa7amed-queue update` shows the current setting on its `auto-update` line.

Automatic updates are safe for this plugin because of what it does on its side, all of
which is verified by the test suite:

- On the first run after any version change, before writing anything, it copies your
  settings and every queue to `backups/` (`backup_on_update`, on by default; the five
  most recent automatic backups are kept, manual backups are never removed). The new
  version is recorded only after that backup exists, so a first run that is cut short
  makes it on the next one.
- A release that would change the data format says so in `moa7amed-queue update`
  beforehand; none has so far.
- A runner that is working when an update arrives finishes on the code it was started
  with (the previous version's folder stays on disk). Start runners again afterwards so
  they use the new version.

## Checking an update

```bash
moa7amed-queue doctor
```

```text
moa7amed-queue 0.3.1: healthy
  [ok  ] version        moa7amed-queue 0.3.1 at ... (updated from 0.3.0 on ...); the data was backed up first: ...
  [ok  ] manifest       plugin.json says moa7amed-queue 0.3.1
  [ok  ] plugin files   launchers, capture hook and runners present; 10 skills
  [ok  ] queues         2 project queue(s), 14 task(s), all readable and intact
  [ok  ] Claude Code    version 2.1.329
  ...
```

It exits with 1 when something is wrong and changes nothing. `moa7amed-queue changelog`
prints what the installed version changed; `changelog --since 0.3.0` prints everything
newer than a version.

## Going back

Claude Code has no rollback command. It installs whatever version the marketplace
offers, so going back means pointing the marketplace at the earlier release, and, only
if the data needs it, restoring a backup.

### 1. Install the earlier version

Every release is tagged `<plugin>--v<version>`. Add the marketplace at that tag:

```bash
claude plugin marketplace remove moa7amed-tools       # this also uninstalls its plugins; your data stays
claude plugin marketplace add moa7amed/moa7amed-tools#moa7amed-queue--v0.3.0
claude plugin install moa7amed-queue@moa7amed-tools
```

Restart Claude Code. While the marketplace is pinned to a tag it does not move, so no
update arrives; to return to the current releases, repeat the three commands with
`moa7amed/moa7amed-tools` in the second one.

**Verified** with a marketplace in a local folder: removing a marketplace uninstalls the
plugins installed from it (so the last command is `install`, not `update`); adding it
again and installing gives the version it now offers; when a marketplace that is still
registered starts offering an earlier version, `claude plugin update` installs that
(`updated from 0.3.1 to 0.3.0`); and none of these steps touches the data directory.
**Not yet verified:** the `#tag` form against the hosted repository.

### 2. Decide about the data

| Situation | What to do |
| :-- | :-- |
| The release notes say the data format is unchanged (true for every release so far) | Nothing. The earlier version reads the queue as it is, including tasks added since. The upgrade tests run the earlier version against a queue the newer one has used |
| The newer version migrated the data (`moa7amed-queue update` announced it) | Restore the backup made before the update, with the **earlier** version installed |
| You want the queue exactly as it was before the update, for any reason | Restore that backup |

```bash
moa7amed-queue backup --list
moa7amed-queue restore 20261120-063118-v0.3.1-auto-update-from-0.3.0          # shows what it would do
moa7amed-queue restore 20261120-063118-v0.3.1-auto-update-from-0.3.0 --yes
```

`restore` refuses while a runner is active, and first saves the current state as a
backup labelled `before-restore`, so a restore can itself be undone. Each queue is
restored whole or not at all; if a restore stops part-way, the message names that safety
backup. A backup holds the queues and settings; it does not hold your repositories,
which git already protects.

### If a release is bad for everyone

Maintainers do not un-publish. They release a new version that reverts the change
(`0.3.2` containing the code of `0.3.0`), because every installation follows the version
number forward on its own. See [release-checklist.md](release-checklist.md).

## For maintainers: what makes an update reach users

1. The version in `plugin.json` changes on the branch the marketplace follows. Nothing
   else delivers an update: not a tag, not a GitHub release, not a commit.
2. Users with auto-update on receive it in their next session; everyone else when they
   run the two commands at the top of this page.

Before the repository is public none of this happens for anyone. See the release
checklist for the exact steps that remain.
