# Publishing to GitHub: owner steps

The steps the repository owner runs to publish moa7amed-tools with moa7amed-queue
0.8.0. Every step that leaves your machine (create, push, tag, release) is yours to
start, in this order, after the previous step succeeded.

The public history starts with a single commit holding the validated tree of 0.8.0; the
development history before it stays local (step 5). Nothing in the repository may
depend on commits that are not public: `release/baselines.json` therefore lists no
earlier version yet, and later releases add 0.8.0 by its public commit or tag.

The location `github.com/moa7amed/moa7amed-tools` is used in the manifests and READMEs.
If you choose another owner or name, change it first (step 2).

Commands are for a POSIX shell (Git Bash on Windows works).

## 1. Review what will be published

```bash
cd /path/to/moa7amed-tools
git status                      # must be clean; commit or set aside anything else first
git log --oneline -15
git ls-files | less             # every file that will become public
```

Make sure nothing private is tracked: no local paths, keys or passwords, no queue data,
no documents or output from real customers, and none of the notes, prompts or
conversations used while developing the plugin. The release check below scans for them too,
but read the file list yourself once.

## 2. Confirm the repository name

The manifests assume `moa7amed/moa7amed-tools`:

```bash
grep -rn "moa7amed/moa7amed-tools" .claude-plugin plugins/moa7amed-queue/.claude-plugin README.md plugins/moa7amed-queue/README.md docs
```

If the owner or name differs, change every hit (`homepage` and `repository` in
`plugins/moa7amed-queue/.claude-plugin/plugin.json`, `homepage` in
`.claude-plugin/marketplace.json`, the install commands in both READMEs), commit, and
continue.

## 3. Run every local check on the commit to be published

```bash
python -B scripts/release_check.py --require-cli --tests
```

All checks must pass. Optional, costs money (Haiku, budget-capped):

```bash
python -B plugins/moa7amed-queue/tests/agents_live.py --budget 1.50
```

## 4. Create the GitHub repository (empty)

On github.com: **New repository**, owner `moa7amed`, name `moa7amed-tools`, public,
**without** README, license or .gitignore (they exist here). Or with the GitHub CLI:

```bash
gh repo create moa7amed/moa7amed-tools --public --homepage "https://moa7amed.com/" \
  --description "Intelligent task orchestration for Claude Code: persistent queues, model routing, adaptive workflows, and verified execution."
gh repo edit moa7amed/moa7amed-tools --add-topic claude-code --add-topic claude-code-plugin \
  --add-topic task-queue --add-topic model-routing --add-topic developer-tools \
  --add-topic workflow-automation
```

moa7amed-tools is an independent project; the description names Claude Code as the tool
it extends, not as an affiliation.

Do not let GitHub initialise it: the first push must be this repository's own history.

## 5. Configure the remote and push (explicit authorization)

The first push publishes one parentless commit of the checked tree, made on a separate
local branch, so local `main` keeps its full history:

```bash
git config --local user.name  "moa7amed"
git config --local user.email "moa7amed@gmail.com"      # the public contact address
commit=$(git commit-tree "main^{tree}" -m "moa7amed-tools: moa7amed-queue 0.8.0")
git branch publish "$commit"
git clone --branch publish --single-branch . ../publish-check   # validate this clone (step 3)
git remote add origin https://github.com/moa7amed/moa7amed-tools.git
git remote -v                   # check before pushing
git push origin publish:main    # only this branch; no --all, --mirror, --tags or --force
```

Later commits are made on `publish` (or a branch from it) and pushed to `main` the same
way; `main` and `publish` share no history.

## 6. Wait for CI

Open **Actions**. The `CI` workflow runs the tests on Linux, Windows and macOS for
several Python versions, and the release checks. Fix on a branch, merge, and do not tag
until `CI` is green on `main`.

Then, in **Settings**:

- **Branches:** protect `main`, require the `CI` checks, no direct pushes.
- **Security:** turn on private vulnerability reporting.

## 7. Tag and release (explicit authorization)

```bash
claude plugin tag plugins/moa7amed-queue --dry-run       # shows moa7amed-queue--v0.8.0
claude plugin tag plugins/moa7amed-queue --push          # creates and pushes the tag
```

The tag starts `.github/workflows/release.yml`, which repeats the checks and records the
GitHub release. Check the release page afterwards.

## 8. Install from the hosted marketplace

In an empty Claude Code profile, so nothing local interferes:

```bash
export CLAUDE_CONFIG_DIR="$(mktemp -d)"
claude plugin marketplace add moa7amed/moa7amed-tools
claude plugin install moa7amed-queue@moa7amed-tools
claude plugin list                                       # moa7amed-queue 0.8.0
```

Then in a session: `/moa7amed-queue:queue-status`, and in a terminal
`moa7amed-queue doctor`.

## 9. Enable updates

Automatic updates for third-party marketplaces are off by default. In Claude Code:
`/plugin`, **Marketplaces** tab, select `moa7amed-tools`, turn on auto-update. By hand:

```bash
claude plugin marketplace update moa7amed-tools
claude plugin update moa7amed-queue@moa7amed-tools
```

## 10. Update and roll back

Going back to an earlier version, for users and maintainers:
[updates-and-rollback.md](updates-and-rollback.md). A bad release:
[release-checklist.md](release-checklist.md#when-a-release-is-bad).

## 11. After publishing

- Verify the items under "Not verified yet" in [release-checklist.md](release-checklist.md)
  and correct the documentation where reality differs.
- Do not describe CI as passing, or a platform as supported, before you have seen it.

## Where the first publication stands

Done on 2026-11-21: steps 1 to 6 and 8. The repository is public, its first commit is
the single-commit history described in step 5, CI passed on Linux, Windows and macOS,
and installing from the hosted marketplace into an empty profile worked. Not done:
step 7 (no tag, no GitHub release), the branch protection and vulnerability reporting
settings in step 6, and an update from one published version to the next.
