# Release checklist

Three lists: what has to happen once before the first public release, what happens for
every release, and what to do when a release turns out bad.

Nothing in this repository releases by itself. A release is two deliberate actions by a
maintainer: merging a commit that changes a plugin's version, and pushing its tag.

## Once, before the first public release

Until these are done the marketplace exists only on the maintainer's machine. No user
can add it, and no update is delivered to anyone.

- [ ] **Decide the repository name.** The manifests assume
      `github.com/moa7amed/moa7amed-tools`. If the repository gets another owner or
      name, change `homepage` and `repository` in
      `plugins/moa7amed-queue/.claude-plugin/plugin.json`, `homepage` in
      `.claude-plugin/marketplace.json`, and the install commands in both READMEs
      (search for `moa7amed/moa7amed-tools`).
- [ ] **Run everything locally on the commit to be published.**

      ```bash
      python -B scripts/release_check.py --require-cli --tests
      ```

- [ ] **Create the repository on GitHub, empty** (no README, no license: they exist
      here), then push:

      ```bash
      git remote add origin https://github.com/moa7amed/moa7amed-tools.git
      git push -u origin main
      ```

- [ ] **Watch the first CI run.** The workflows in `.github/workflows` have never run.
      Expect to fix things: the test suite has run on Windows (Python 3.8, 3.9, 3.14)
      and on Linux under WSL (3.12), never on macOS and never on a GitHub runner. Do
      not tag until `CI` is green on `main`.
- [ ] **Protect `main`:** require the `CI` checks to pass before merging; no direct
      pushes.
- [ ] **Turn on private vulnerability reporting** (Settings, Security), which
      `docs/security-and-privacy.md` tells reporters to use.
- [ ] **Install from GitHub into an empty profile** and check it:

      ```bash
      export CLAUDE_CONFIG_DIR="$(mktemp -d)"        # an empty Claude Code profile
      claude plugin marketplace add moa7amed/moa7amed-tools
      claude plugin install moa7amed-queue@moa7amed-tools
      claude plugin list
      ```

      then, in a real session with that plugin: `/moa7amed-queue:queue-status`.
- [ ] **Tag the first public release.** This starts the release workflow, which repeats
      all checks and records the GitHub release:

      ```bash
      claude plugin tag plugins/moa7amed-queue --dry-run
      claude plugin tag plugins/moa7amed-queue --push
      ```

- [ ] **Verify what could not be verified offline** (see "Not verified yet" below), in
      particular adding the marketplace at a tag and the automatic update. Correct
      `docs/updates-and-rollback.md` if reality differs, and remove its status note once
      it is confirmed.
- [ ] Only now: remove the "Status: not published yet" notes from the root `README.md`
      and from the plugin README. From here on the READMEs may say that updates are
      delivered automatically to users who turn auto-update on.

### Not verified yet

| What | Why not | How to verify after publishing |
| :-- | :-- | :-- |
| `claude plugin marketplace add moa7amed/moa7amed-tools` from GitHub | The repository is not hosted | The install step above |
| Adding the marketplace at a branch or tag (`#beta`, `#moa7amed-queue--v0.3.1`) | This CLI accepts no local git URL, so a ref cannot be tried offline | Add it at the release tag in an empty profile; `claude plugin list` must show that version |
| The background auto-update pass and its notification | Needs a hosted marketplace and a second release | With auto-update on, publish the next version, start a session, wait up to ten minutes, look for `Plugin updated: moa7amed-queue` |
| The GitHub workflows | They have never run | The first push |
| The test suite on macOS | No Mac was available. (Python 3.8 has been run on Windows; Linux only on 3.12, under WSL) | The CI matrix: macOS with Python 3.10 and 3.13, Linux and Windows from 3.8 |
| The `queue:` capture during a running turn on macOS and Linux, and with other Claude Code versions | Observed only with Claude Code 2.1.329 on Windows; Claude Code does not document it | Repeat the interactive check (type `queue: ...` while a tool runs; one task, no interruption) |
| Auto-update of a private repository through git credential helpers | Not applicable until there is one | Only if the repository is made private |

## Every release

Example: releasing `moa7amed-queue` 0.3.2.

1. **Branch** from `main`.
2. **Set the version** and open the changelog entry:

   ```bash
   python scripts/bump_version.py moa7amed-queue 0.3.2      # or: patch | minor | major
   ```

   Which number: [versioning.md](versioning.md).
3. **Write the changelog entry** in `plugins/moa7amed-queue/CHANGELOG.md`, for users:
   what changes for them, and first of all anything they must do. The check fails while
   the placeholder is still there.
4. **If the release changes what a user relies on**, also update the upgrade notes in
   the plugin README, and if the data format changes follow "Data format" in
   versioning.md.
5. **If an earlier release must remain a tested starting point**, add it to
   `release/baselines.json` with its tag. Keep at least the previous release there.
   A baseline must be a commit or tag in the public history; a clone cannot check
   out anything else.
6. **Run the checks.** About 15 minutes with the test suites:

   ```bash
   python -B scripts/release_check.py --worktree --require-cli --tests
   ```

   | Check | Proves |
   | :-- | :-- |
   | `versions` | The version is stated once and matches everywhere |
   | `changelog` | Release notes exist; a tagged version is not being reused |
   | `package` | Required files are there; nothing generated, secret or oversized ships |
   | `secrets` | No credentials, keys, e-mail addresses or local user paths |
   | `dev-material` | None of the specifications, prompts, transcripts or hand-off notes used during development, in any tracked file (and so in the archive) |
   | `validate` | `claude plugin validate --strict` passes |
   | `clean-install` | It installs into an empty Claude Code profile and runs; reports the always-on context estimate against the plugin's budget |
   | `update` | Claude Code's own commands update an installed previous version to this one, and back |
   | `upgrade` | A queue written by each earlier version works in this one, and still works in the earlier one afterwards |
   | `tests` | The test suites |

7. **Optionally run the live validation.** It uses the real CLI and costs money (about
   $0.15 on the cheapest model when last run); it has found a defect the stand-in tests
   could not:

   ```bash
   cd plugins/moa7amed-queue
   python tests/live_validation.py --budget 1.00 --rehearse     # free: checks the script
   python tests/live_validation.py --budget 1.00
   ```

8. **Commit, open a pull request, wait for CI.**
9. **Merge.** This is the release: from this moment Claude Code offers the new version.
10. **Tag** the merge commit. The release workflow records the GitHub release:

    ```bash
    git switch main && git pull
    claude plugin tag plugins/moa7amed-queue --push
    ```

11. **Check as a user:**

    ```bash
    claude plugin marketplace update moa7amed-tools
    claude plugin update moa7amed-queue@moa7amed-tools
    moa7amed-queue doctor
    ```

### A beta

Same list, on the `beta` branch, with a pre-release version
(`python scripts/bump_version.py moa7amed-queue 0.4.0-beta.1`). The tool refuses a
pre-release version in the stable marketplace. The release workflow marks the GitHub
release as a pre-release. Promote by releasing `0.4.0` on `main`.

## When a release is bad

| Situation | Action |
| :-- | :-- |
| A defect, data is safe | Fix forward: release the next patch version |
| The release must be withdrawn quickly | Release a new patch version whose code is the last good one (`git revert` the release commits, bump, changelog entry saying so). Installations follow the version number, so this reaches everyone the same way the bad release did |
| Data was migrated or damaged | As above, and tell users in the changelog entry to run `moa7amed-queue restore <the auto-update backup>`. Every installation made that backup on its first run of the bad version |

Do not delete or move a release tag, and do not publish different content under a
version that was already released: installations that already have that version would
never receive the correction.

What a single user does to go back is in
[updates-and-rollback.md](updates-and-rollback.md).
