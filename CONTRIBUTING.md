# Contributing

moa7amed-tools is a marketplace of independent plugins. This page is the short version;
the documents it links to hold the detail.

| To | Read |
| :-- | :-- |
| Understand how the repository and the queue plugin are built | [docs/architecture.md](docs/architecture.md) |
| Add a skill, agent, hook, MCP server or a whole plugin | [docs/plugin-development.md](docs/plugin-development.md) |
| Write a skill | [docs/skills-development.md](docs/skills-development.md) |
| Choose a version number, or change stored data | [docs/versioning.md](docs/versioning.md) |
| Release | [docs/release-checklist.md](docs/release-checklist.md) |
| Know how updates reach users and how to go back | [docs/updates-and-rollback.md](docs/updates-and-rollback.md) |
| Know what is stored, started and sent | [docs/security-and-privacy.md](docs/security-and-privacy.md) |

## The rules that are checked

`tests/test_marketplace.py` and `scripts/release_check.py` enforce these, so a change
that breaks one fails before review.

- **A plugin is a self-contained folder** under `plugins/`, named `moa7amed-<name>`, the
  same in its folder, its manifest and its marketplace entry. It has its own
  `README.md`, `CHANGELOG.md`, `LICENSE`, `release.json` and `tests/`.
- **No shared code.** A plugin does not import from, or point at, another plugin's
  folder, and there is no common library folder. `scripts/` and `docs/` at the root are
  for maintainers and are not part of any plugin.
- **One version per plugin, written in its `plugin.json`.** The marketplace entry
  carries no version. `scripts/bump_version.py` changes it everywhere it is repeated.
- **Every released version has a changelog entry**, and a version that has been tagged
  is never reused for different content.
- **Nothing personal in the repository**: no local paths, user names, e-mail addresses,
  tokens or machine-specific configuration.
- **Names a user may depend on keep working.** When one changes, the old one stays as a
  documented alias, and existing data is used where it is.
- **What every session carries stays within the plugin's budget.**

## Working on a change

```bash
# the plugin's tests (about ten minutes for moa7amed-queue)
cd plugins/moa7amed-queue && python -B -m unittest discover -s tests -t tests

# the marketplace's own checks
python -B -m unittest discover -s tests -t tests

# everything a release needs, on your working tree
python -B scripts/release_check.py --worktree
```

Try a plugin in a real session without installing it:

```bash
claude --plugin-dir plugins/moa7amed-queue
```

- Reproduce a defect with a failing test before fixing it.
- Tests use no network, no Claude account and no real project.
- Python 3.8+, standard library only. No build step.
- Shell scripts keep Unix line endings on every platform (`.gitattributes`).

## Releasing

Nothing is released automatically. The whole procedure, including what must happen
before the first public release, is in
[docs/release-checklist.md](docs/release-checklist.md). The three commands at its core:

```bash
python scripts/bump_version.py moa7amed-queue patch          # then write the changelog entry
python -B scripts/release_check.py --worktree --require-cli --tests
claude plugin tag plugins/moa7amed-queue --push              # after the change is merged
```

Claude Code offers an update to users only when the version changes.
