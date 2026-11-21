"""The release tooling itself: the checks must pass on this repository, and must fail when
something is wrong. Each negative test breaks one thing in a copy of the repository.

Also checked here: the workflows only ever publish on a maintainer's deliberate action,
and the links between the documents resolve."""
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import unittest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CHECK = os.path.join(REPO, "scripts", "release_check.py")
BUMP = os.path.join(REPO, "scripts", "bump_version.py")
NOTES = os.path.join(REPO, "scripts", "release_notes.py")
PACKAGE = os.path.join(REPO, "scripts", "package.py")
PLUGIN = "moa7amed-queue"
OFFLINE = "versions,changelog,package,secrets,dev-material"


def run(*command):
    result = subprocess.run([sys.executable, "-B"] + list(command), stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE, stdin=subprocess.DEVNULL, timeout=600)
    return (result.returncode, result.stdout.decode("utf-8", errors="replace"),
            result.stderr.decode("utf-8", errors="replace"))


def read(*parts):
    with open(os.path.join(*parts), encoding="utf-8") as handle:
        return handle.read()


def _force_remove(function, path, _info):
    os.chmod(path, stat.S_IWRITE)
    function(path)


class CopyOfTheRepository(unittest.TestCase):
    """A plain copy (no git history) that a test may damage."""

    def setUp(self):
        self.tmp = os.path.realpath(tempfile.mkdtemp(prefix="release-tooling-"))
        self.copy = os.path.join(self.tmp, "repo")
        shutil.copytree(REPO, self.copy, ignore=shutil.ignore_patterns(
            ".git", "__pycache__", "*.pyc", ".pytest_cache"))
        self.plugin = os.path.join(self.copy, "plugins", PLUGIN)

    def tearDown(self):
        shutil.rmtree(self.tmp, onerror=_force_remove)

    def check(self, only=OFFLINE):
        code, out, err = run(CHECK, "--repo", self.copy, "--only", only, "--json")
        self.assertIn(code, (0, 1), err)
        data = json.loads(out)
        return code, {(row["check"], row["subject"]): row for row in data["results"]}

    def edit_json(self, path, change):
        with open(path, encoding="utf-8") as handle:
            data = json.load(handle)
        change(data)
        with open(path, "w", encoding="utf-8", newline="\n") as handle:
            json.dump(data, handle, indent=2)

    def write(self, relative, text):
        path = os.path.join(self.plugin, relative)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(text)

    def version(self):
        return json.loads(read(self.plugin, ".claude-plugin", "plugin.json"))["version"]


class ChecksCatchMistakes(CopyOfTheRepository):

    def test_untouched_copy_passes(self):
        code, rows = self.check()
        self.assertEqual(code, 0, rows)
        self.assertEqual({row["status"] for row in rows.values()}, {"ok"})

    def test_version_repeated_in_the_marketplace_entry(self):
        self.edit_json(os.path.join(self.copy, ".claude-plugin", "marketplace.json"),
                       lambda data: data["plugins"][0].update(version=self.version()))
        code, rows = self.check("versions")
        self.assertEqual(code, 1)
        self.assertIn("plugin.json only", rows[("versions", PLUGIN)]["detail"])

    def test_version_that_differs_between_files(self):
        self.edit_json(os.path.join(self.plugin, "release.json"),
                       lambda data: data.update(version="9.9.9"))
        code, rows = self.check("versions")
        self.assertEqual(code, 1)
        self.assertIn("release.json says 9.9.9", rows[("versions", PLUGIN)]["detail"])

    def test_version_in_the_code_that_was_not_updated(self):
        init = os.path.join(self.plugin, "scripts", "mqlib", "__init__.py")
        text = read(init)
        with open(init, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(text.replace('__version__ = "%s"' % self.version(), '__version__ = "0.0.1"'))
        code, rows = self.check("versions")
        self.assertEqual(code, 1)
        self.assertIn("__init__.py says 0.0.1", rows[("versions", PLUGIN)]["detail"])

    def test_pre_release_in_the_stable_marketplace(self):
        for name in (".claude-plugin/plugin.json", "release.json"):
            self.edit_json(os.path.join(self.plugin, name),
                           lambda data: data.update(version="9.9.0-beta.1"))
        self.edit_json(os.path.join(self.plugin, "release.json"),
                       lambda data: data.update(channel="beta"))
        code, rows = self.check("versions")
        self.assertEqual(code, 1)
        self.assertIn("pre-release", rows[("versions", PLUGIN)]["detail"])
        # The same version is fine in the beta marketplace.
        self.edit_json(os.path.join(self.copy, ".claude-plugin", "marketplace.json"),
                       lambda data: data.update(name=data["name"] + "-beta"))
        init = os.path.join(self.plugin, "scripts", "mqlib", "__init__.py")
        text = read(init)
        with open(init, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(re.sub(r'__version__ = "[^"]+"', '__version__ = "9.9.0-beta.1"', text))
        code, rows = self.check("versions")
        self.assertEqual(code, 0, rows)

    def test_missing_or_placeholder_release_notes(self):
        changelog = os.path.join(self.plugin, "CHANGELOG.md")
        text = read(changelog)
        version = self.version()
        with open(changelog, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(text.replace("## %s" % version, "## 0.0.9", 1))
        code, rows = self.check("changelog")
        self.assertEqual(code, 1)
        self.assertIn("newest entry is", rows[("changelog", PLUGIN)]["detail"])
        with open(changelog, "w", encoding="utf-8", newline="\n") as handle:
            handle.write("# Changelog\n\n## %s\n\nTODO: describe what changed.\n\n## 0.3.0\n\nx\n"
                         % version)
        code, rows = self.check("changelog")
        self.assertEqual(code, 1)
        self.assertIn("placeholder", rows[("changelog", PLUGIN)]["detail"])

    def test_credentials_and_personal_data(self):
        # Built at run time, so that this file does not itself contain what it plants.
        planted = {
            "Anthropic API key": "key = sk-" + "ant-" + "a1B2" * 8,
            "GitHub token": "token: gh" + "p_" + "A1b2C3d4" * 5,
            "private key": "-----BEGIN " + "RSA PRIVATE KEY-----",
            "Windows user folder": "path = C:" + "\\Users\\" + "somebody\\project",
            "home folder": "path = /ho" + "me/somebody/project/",
            "e-mail address": "contact: some.body" + "@" + "mailhost.io",
        }
        for label, line in planted.items():
            self.write("notes.md", "# notes\n\n%s\n" % line)
            code, rows = self.check("secrets")
            self.assertEqual(code, 1, label)
            self.assertIn(label, rows[("secrets", "repository")]["detail"], label)
        self.write("notes.md", "# notes\n\nnothing to see, mail queue-test" + "@" + "example.invalid\n")
        self.assertEqual(self.check("secrets")[0], 0)

    def test_only_the_approved_public_address_may_appear(self):
        approved = "moa7amed" + "@" + "gmail.com"
        other = "someone.else" + "@" + "mailhost.io"
        self.write("notes.md", "# notes\n\nWrite to %s.\n" % approved)
        self.assertEqual(self.check("secrets")[0], 0)       # the approved address
        self.write("notes.md", "# notes\n\nWrite to %s or %s.\n" % (approved, other))
        code, rows = self.check("secrets")
        self.assertEqual(code, 1)                           # any other address fails
        self.assertIn("someone.else", rows[("secrets", "repository")]["detail"])
        # Declaring an address in a manifest does not approve it.
        self.write("notes.md", "# notes\n\nnothing here\n")
        manifest = os.path.join(self.plugin, ".claude-plugin", "plugin.json")
        self.edit_json(manifest, lambda data: data["author"].update(email=other))
        code, rows = self.check("secrets")
        self.assertEqual(code, 1)
        self.assertIn("not approved for publication", rows[("secrets", "repository")]["detail"])

    def test_development_material(self):
        # Built at run time, so that this file does not itself contain what it plants.
        planted = {
            "reference to the specifications or prompts used in development":
                "As the earlier " + "specification says, the router has a mode.",
            "numbered item of a specification used in development":
                '    """Categ' + 'ories 3, 11: the deep process."""',
            "pasted-content or system-reminder markup": "<" + "pasted_content id='1'>",
            "transcript of a development conversation": "See the conversation " + "transcript for details.",
            "hand-off note between agents": "From the agent " + "hand-off: done.",
        }
        for label, line in planted.items():
            self.write("notes.md", "# notes\n\n%s\n" % line)
            code, rows = self.check("dev-material")
            self.assertEqual(code, 1, label)
            detail = rows[("dev-material", "repository")]["detail"]
            self.assertIn("notes.md:3 " + label, detail, label)
            self.assertNotIn(line.strip()[:20], detail)    # the text itself is not repeated
        self.write("notes.md", "# notes\n\nAn API specification and a short prompt are fine.\n")
        self.assertEqual(self.check("dev-material")[0], 0)
        for name in ("prompts/build.md", "docs/SPEC.md", "handoff-notes.txt", "session.jsonl"):
            self.write(name, "text\n")
            code, rows = self.check("dev-material")
            self.assertEqual(code, 1, name)
            self.assertIn("(file name)", rows[("dev-material", "repository")]["detail"], name)
            os.remove(os.path.join(self.plugin, name))

    def test_files_that_must_not_ship(self):
        for name in (".env", "queue.db", "scripts/mqlib/__pycache__/x.pyc", "debug.log",
                     "id_rsa"):
            self.write(name, "x")
            code, rows = self.check("package")
            self.assertEqual(code, 1, name)
            self.assertIn("must not be shipped", rows[("package", PLUGIN)]["detail"], name)
            os.remove(os.path.join(self.plugin, name))
        self.write("big.md", "x" * (600 * 1024))
        code, rows = self.check("package")
        self.assertIn("limit", rows[("package", PLUGIN)]["detail"])

    def test_missing_required_file_and_windows_line_endings_in_a_shell_script(self):
        os.remove(os.path.join(self.plugin, "LICENSE"))
        code, rows = self.check("package")
        self.assertEqual(code, 1)
        self.assertIn("LICENSE is missing", rows[("package", PLUGIN)]["detail"])
        shutil.copy(os.path.join(REPO, "plugins", PLUGIN, "LICENSE"), self.plugin)
        hook = os.path.join(self.plugin, "hooks", "capture-prompt.sh")
        with open(hook, "rb") as handle:
            data = handle.read()
        with open(hook, "wb") as handle:
            handle.write(data.replace(b"\n", b"\r\n"))
        code, rows = self.check("package")
        self.assertEqual(code, 1)
        self.assertIn("Windows line endings", rows[("package", PLUGIN)]["detail"])


class VersionBump(CopyOfTheRepository):

    def bump(self, *arguments):
        return run(BUMP, PLUGIN, *(list(arguments) + ["--repo", self.copy]))

    def test_bump_writes_every_copy_and_opens_the_changelog_entry(self):
        before = self.version()
        major, minor, patch = (int(part) for part in before.split("."))
        expected = "%d.%d.%d" % (major, minor, patch + 1)
        code, out, err = self.bump("patch")
        self.assertEqual(code, 0, err)
        self.assertIn("%s -> %s" % (before, expected), out)
        self.assertEqual(self.version(), expected)
        self.assertEqual(json.loads(read(self.plugin, "release.json"))["version"], expected)
        self.assertIn('__version__ = "%s"' % expected,
                      read(self.plugin, "scripts", "mqlib", "__init__.py"))
        changelog = read(self.plugin, "CHANGELOG.md")
        self.assertLess(changelog.index("## %s" % expected), changelog.index("## %s" % before))
        # The marketplace entry still carries no version.
        entry = json.loads(read(self.copy, ".claude-plugin", "marketplace.json"))["plugins"][0]
        self.assertNotIn("version", entry)
        # Consistent, but not releasable until the notes are written.
        code, rows = self.check("versions,changelog")
        self.assertEqual(rows[("versions", PLUGIN)]["status"], "ok")
        self.assertEqual(rows[("changelog", PLUGIN)]["status"], "fail")
        self.assertIn("placeholder", rows[("changelog", PLUGIN)]["detail"])

    def test_dry_run_changes_nothing(self):
        before = self.version()
        code, out, _ = self.bump("minor", "--dry-run")
        self.assertEqual(code, 0)
        self.assertIn("dry run", out)
        self.assertEqual(self.version(), before)

    def test_refused_versions(self):
        for version, reason in (("0.0.1", "not newer"), ("banana", "not a version"),
                                (self.version(), "not newer"),
                                ("9.9.0-beta.1", "pre-release")):
            code, _, err = self.bump(version)
            self.assertEqual(code, 2, version)
            self.assertIn(reason, err, version)
        self.assertEqual(self.check("versions")[0], 0)
        code, _, err = run(BUMP, "moa7amed-nothing", "patch", "--repo", self.copy)
        self.assertEqual(code, 2)


class ReleaseNotes(unittest.TestCase):

    def test_notes_are_the_changelog_entry_of_the_version(self):
        version = json.loads(read(REPO, "plugins", PLUGIN, ".claude-plugin", "plugin.json"))["version"]
        code, out, _ = run(NOTES, PLUGIN)
        self.assertEqual(code, 0)
        self.assertGreater(len(out), 200)
        self.assertNotIn("## ", out)
        self.assertEqual(run(NOTES, PLUGIN, version, "--expect-current")[0], 0)
        code, _, err = run(NOTES, PLUGIN, "0.3.0", "--expect-current")
        self.assertEqual(code, 1)
        self.assertIn("plugin.json says", err)
        self.assertEqual(run(NOTES, PLUGIN, "7.7.7")[0], 1)


class ThisRepository(unittest.TestCase):

    def test_offline_checks_pass_here(self):
        # In a git checkout the checks look at what git would ship. An unpacked archive of
        # the repository has no such list, and may hold bytecode left behind by a test run,
        # so there a copy without it is checked instead.
        result = subprocess.run(["git", "rev-parse", "--show-toplevel"], cwd=REPO,
                                stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
        top = result.stdout.decode("utf-8", errors="replace").strip()
        if result.returncode == 0 and top and os.path.samefile(top, REPO):
            code, out, err = run(CHECK, "--only", OFFLINE, "--worktree", "--json")
        else:
            tmp = tempfile.mkdtemp(prefix="release-tooling-")
            self.addCleanup(shutil.rmtree, tmp, onerror=_force_remove)
            copy = os.path.join(tmp, "repo")
            shutil.copytree(REPO, copy, ignore=shutil.ignore_patterns(
                ".git", "__pycache__", "*.pyc", ".pytest_cache"))
            code, out, err = run(CHECK, "--repo", copy, "--only", OFFLINE, "--json")
        data = json.loads(out)
        failed = [row for row in data["results"] if row["status"] == "fail"]
        self.assertEqual((code, failed), (0, []), err)

    def test_release_scripts_are_valid_python_3_8(self):
        import ast
        for name in sorted(os.listdir(os.path.join(REPO, "scripts"))):
            if name.endswith(".py"):
                ast.parse(read(REPO, "scripts", name), filename=name, feature_version=(3, 8))

    def test_earlier_versions_for_the_upgrade_tests_are_declared(self):
        baselines = json.loads(read(REPO, "release", "baselines.json"))
        for entry in baselines[PLUGIN]:
            self.assertRegex(entry["version"], r"^\d+\.\d+\.\d+$")
            self.assertRegex(entry["commit"], r"^[0-9a-f]{40}$")
            self.assertTrue(entry["cli"].endswith(".py"))
        versions = [entry["version"] for entry in baselines[PLUGIN]]
        self.assertEqual(versions, sorted(versions))
        self.assertTrue(os.path.isfile(os.path.join(REPO, "plugins", PLUGIN, "tests",
                                                    "upgrade_check.py")))

    def test_workflows_never_publish_on_their_own(self):
        ci = read(REPO, ".github", "workflows", "ci.yml")
        release = read(REPO, ".github", "workflows", "release.yml")
        for text in (ci, release):
            self.assertNotIn("schedule:", text)                 # nothing runs unattended
            self.assertNotRegex(text, r"\bgit\s+push\b")          # nothing is pushed
            self.assertNotRegex(text, r"secrets\.(?!GITHUB_TOKEN)")   # no stored secrets
            self.assertIn("permissions:\n  contents: read", text)   # read-only by default
            self.assertNotIn("\r", text)
        # CI can never write; it has no publishing step at all.
        self.assertNotIn("contents: write", ci)
        self.assertNotRegex(ci, r"gh release|plugin tag|npm publish")
        # A release is recorded only for a tag a maintainer pushed, or on request.
        trigger = release.split("permissions:")[0]
        self.assertIn('tags:\n      - "moa7amed-*--v*"', trigger)
        self.assertIn("workflow_dispatch:", trigger)
        self.assertNotIn("branches:", trigger)
        self.assertNotIn("pull_request", trigger)
        self.assertEqual(release.count("contents: write"), 1)
        self.assertLess(release.index("release_check.py"), release.index("gh release create"))
        self.assertIn("needs: verify", release)
        # User-controlled text reaches the shell only through environment variables.
        for text in (ci, release):
            for line in text.splitlines():
                if line.strip().startswith(("run:", "echo ", "gh ", "python ")) or "  run: " in line:
                    self.assertNotIn("${{ github.event", line)
                    self.assertNotIn("${{ inputs", line)

    def test_ci_covers_every_platform_and_the_oldest_supported_python(self):
        ci = read(REPO, ".github", "workflows", "ci.yml")
        for runner in ("ubuntu", "windows", "macos"):
            self.assertIn("os: %s" % runner, ci)
        minimum = json.loads(read(REPO, "plugins", PLUGIN, "release.json"))["min_python"]
        self.assertIn('python: "%s"' % minimum, ci)
        self.assertIn("scripts/package.py", ci)

    def test_release_archive_is_reproducible_and_holds_exactly_the_package(self):
        import zipfile
        result = subprocess.run(["git", "rev-parse", "--show-toplevel"], cwd=REPO,
                                stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
        top = result.stdout.decode("utf-8", errors="replace").strip()
        if result.returncode != 0 or not top or not os.path.samefile(top, REPO):
            self.skipTest("the archive is built from git, and this is not a git checkout")
        out = tempfile.mkdtemp(prefix="release-archive-")
        self.addCleanup(shutil.rmtree, out, onerror=_force_remove)
        first = run(PACKAGE, PLUGIN, "--out", os.path.join(out, "a"), "--worktree")
        second = run(PACKAGE, PLUGIN, "--out", os.path.join(out, "b"), "--worktree")
        self.assertEqual((first[0], second[0]), (0, 0), first[2] + second[2])
        digest = lambda text: text.strip().splitlines()[-1].split()[-1]
        self.assertEqual(digest(first[1]), digest(second[1]))
        version = json.loads(read(REPO, "plugins", PLUGIN, ".claude-plugin", "plugin.json"))["version"]
        archive = os.path.join(out, "a", "%s-%s.zip" % (PLUGIN, version))
        with zipfile.ZipFile(archive) as handle:
            names = handle.namelist()
            self.assertIn(".claude-plugin/plugin.json", names)
            self.assertIn("LICENSE", names)
            self.assertFalse([name for name in names if name.startswith(("plugins/", "/"))
                              or "__pycache__" in name or name.endswith(".pyc")])
            mode = handle.getinfo("bin/moa7amed-queue").external_attr >> 16
            self.assertTrue(mode & 0o100)       # the launcher stays executable when unpacked
        with open(archive + ".sha256") as handle:
            self.assertEqual(handle.read().split()[0], digest(first[1]))

    def test_workflows_use_the_claude_code_version_the_release_was_verified_with(self):
        tested = json.loads(read(REPO, "plugins", PLUGIN, "release.json"))["tested_claude_code"]
        for name in ("ci.yml", "release.yml"):
            self.assertIn('CLAUDE_CODE_VERSION: "%s"' % tested,
                          read(REPO, ".github", "workflows", name), name)

    def test_links_between_documents_resolve(self):
        documents = [os.path.join(REPO, name) for name in ("README.md", "CONTRIBUTING.md")]
        for folder, _, names in os.walk(os.path.join(REPO, "docs")):     # docs/media has Markdown too
            documents += [os.path.join(folder, name) for name in sorted(names) if name.endswith(".md")]
        documents += [os.path.join(REPO, "plugins", PLUGIN, name)
                      for name in ("README.md", "CHANGELOG.md")]
        self.assertGreaterEqual(len(documents), 11)
        for document in documents:
            text = re.sub(r"```.*?```", "", read(document), flags=re.S)
            for target in re.findall(r"\]\(([^)\s]+)\)", text):
                if re.match(r"^[a-z]+:", target):
                    continue   # an external address
                path, _, anchor = target.partition("#")
                full = os.path.normpath(os.path.join(os.path.dirname(document), path)) \
                    if path else document
                where = "%s -> %s" % (os.path.relpath(document, REPO), target)
                self.assertTrue(os.path.exists(full), where)
                if anchor and full.endswith(".md"):
                    self.assertIn(anchor, anchors(read(full)), where)

    def test_every_document_is_reachable_from_the_readme(self):
        readme = read(REPO, "README.md")
        for name in os.listdir(os.path.join(REPO, "docs")):
            self.assertIn("docs/%s" % name, readme, name)


def anchors(markdown):
    """The anchors GitHub generates for the headings of a document."""
    found = set()
    for heading in re.findall(r"(?m)^#{1,6}\s+(.+?)\s*$", re.sub(r"```.*?```", "", markdown, flags=re.S)):
        text = re.sub(r"`", "", heading).lower()
        text = re.sub(r"[^\w\- ]", "", text)
        found.add(text.replace(" ", "-"))
    return found


if __name__ == "__main__":
    unittest.main()
