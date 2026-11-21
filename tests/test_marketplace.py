"""Checks for the moa7amed-tools marketplace as a whole: the manifest, the conventions in
CONTRIBUTING.md, and the independence of the plugins it lists.

Each plugin has its own test suite in its folder; nothing here runs plugin code."""
import json
import os
import re
import shutil
import subprocess
import unittest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CLAUDE = shutil.which("claude")
TEXT = (".py", ".md", ".sh", ".cmd", ".ps1", ".json", ".txt", "")
SKIP_DIRS = {".git", "__pycache__"}
MAINTAINER_FOLDERS = {".github", "docs", "release", "scripts", "tests"}


def load(*parts):
    with open(os.path.join(REPO, *parts), encoding="utf-8") as handle:
        return json.load(handle)


def read(*parts):
    with open(os.path.join(REPO, *parts), encoding="utf-8") as handle:
        return handle.read()


def text_files(root):
    for base, dirs, files in os.walk(root):
        dirs[:] = [name for name in dirs if name not in SKIP_DIRS]
        for name in files:
            if os.path.splitext(name)[1] in TEXT:
                yield os.path.join(base, name)


class Marketplace(unittest.TestCase):

    def setUp(self):
        self.manifest = load(".claude-plugin", "marketplace.json")
        self.entries = self.manifest["plugins"]
        self.folders = sorted(name for name in os.listdir(os.path.join(REPO, "plugins"))
                              if os.path.isdir(os.path.join(REPO, "plugins", name)))

    def test_marketplace_identity(self):
        self.assertEqual(self.manifest["name"], "moa7amed-tools")
        self.assertEqual(self.manifest["owner"]["name"], "moa7amed")
        self.assertTrue(self.manifest["description"])

    def test_every_plugin_folder_is_listed_exactly_once(self):
        self.assertEqual(sorted(entry["name"] for entry in self.entries), self.folders)

    def test_entries_point_at_their_own_folder_and_agree_with_the_manifest(self):
        for entry in self.entries:
            name = entry["name"]
            self.assertRegex(name, r"^moa7amed-[a-z0-9]+(-[a-z0-9]+)*$")
            self.assertEqual(entry["source"], "./plugins/%s" % name)
            manifest = load("plugins", name, ".claude-plugin", "plugin.json")
            self.assertEqual(manifest["name"], name)
            # The version is stated once, in the plugin's manifest: Claude Code reads it
            # there first, and a second copy in the marketplace entry could only disagree.
            self.assertNotIn("version", entry, name)
            self.assertRegex(manifest["version"], r"^\d+\.\d+\.\d+(-(alpha|beta|rc)\.\d+)?$")
            self.assertEqual(manifest["author"]["name"], "moa7amed")
            self.assertEqual(manifest.get("license"), "MIT")

    def test_every_plugin_is_complete_on_its_own(self):
        for name in self.folders:
            for required in ("README.md", "CHANGELOG.md", "LICENSE", "release.json", "tests"):
                self.assertTrue(os.path.exists(os.path.join(REPO, "plugins", name, required)),
                                "%s lacks %s" % (name, required))
            version = load("plugins", name, ".claude-plugin", "plugin.json")["version"]
            self.assertIn("## %s" % version, read("plugins", name, "CHANGELOG.md"),
                          "%s: no changelog entry for %s" % (name, version))
            self.assertIn(name, read("README.md"), "%s is not listed in the README" % name)

    def test_plugins_do_not_depend_on_each_other(self):
        # No shared library folder, and no plugin that names a sibling's folder. The other
        # top-level folders are for maintainers; nothing in them is installed with a plugin.
        top = {name for name in os.listdir(REPO)
               if os.path.isdir(os.path.join(REPO, name)) and name not in SKIP_DIRS}
        self.assertEqual(top - MAINTAINER_FOLDERS, {".claude-plugin", "plugins"})
        for folder in ("scripts", "docs", "release"):
            for name in self.folders:
                for path in text_files(os.path.join(REPO, "plugins", name)):
                    if path.endswith((".py", ".sh", ".ps1", ".cmd", ".json")):
                        with open(path, encoding="utf-8", errors="replace") as handle:
                            self.assertNotIn("../../%s" % folder, handle.read().replace("\\", "/"),
                                             "%s reaches into %s/" % (os.path.relpath(path, REPO),
                                                                      folder))
        for name in self.folders:
            others = [other for other in self.folders if other != name]
            for path in text_files(os.path.join(REPO, "plugins", name)):
                with open(path, encoding="utf-8", errors="replace") as handle:
                    text = handle.read()
                for other in others:
                    self.assertNotIn("plugins/%s" % other, text.replace("\\", "/"),
                                     "%s refers to %s" % (os.path.relpath(path, REPO), other))
                inside = os.path.relpath(path, os.path.join(REPO, "plugins", name))
                if path.endswith(".py") and not inside.startswith("tests"):
                    # The code a user runs never reaches outside its own plugin folder.
                    # (Tests may build a pretend marketplace, which has such a path in it;
                    # the check above still keeps them away from a sibling plugin.)
                    self.assertNotRegex(text, r"plugins[/\\]", os.path.relpath(path, REPO))

    def test_ownership_and_license_are_stated_the_same_everywhere(self):
        # Publisher moa7amed, created by Mohamed Ibrahim Ramadan, https://moa7amed.com/.
        notice = "Copyright (c) 2026 Mohamed Ibrahim Ramadan (moa7amed)"
        licenses = [read("LICENSE")] + [read("plugins", name, "LICENSE") for name in self.folders]
        for text in licenses:
            self.assertIn(notice, text)
            self.assertTrue(text.startswith("MIT License"))
            self.assertIn("Permission is hereby granted, free of charge, to any person", text)
            self.assertIn('THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND', text)
        self.assertEqual(len(set(licenses)), 1)
        self.assertEqual(self.manifest["owner"], {"name": "moa7amed",
                                                  "email": "moa7amed@gmail.com",
                                                  "url": "https://moa7amed.com/"})
        for name in self.folders:
            manifest = load("plugins", name, ".claude-plugin", "plugin.json")
            self.assertEqual(manifest["author"], {"name": "moa7amed",
                                                  "email": "moa7amed@gmail.com",
                                                  "url": "https://moa7amed.com/"})
            self.assertEqual(manifest["license"], "MIT")
        for entry in self.entries:
            self.assertEqual(entry.get("license"), "MIT")
        self.assertIn("Mohamed Ibrahim Ramadan", read("README.md"))

    def test_nothing_asks_for_a_licence_key_or_a_payment(self):
        # Free software: no activation, licence check or payment step anywhere in the code.
        # (Words such as "stripe" or "payment" do occur: the model router reads them in
        # task descriptions as a sign of payment-integration work. That is not a payment.)
        pattern = re.compile(r"(?i)licen[cs]e[ _-]?(key|server|check)|activation[ _-]?(code|key)|"
                             r"serial[ _-]?number|trial[ _-]?(period|expired)")
        for path in text_files(os.path.join(REPO, "plugins")):
            if path.endswith((".py", ".sh", ".ps1", ".cmd")) or "bin" + os.sep in path:
                with open(path, encoding="utf-8", errors="replace") as handle:
                    found = pattern.search(handle.read())
                self.assertIsNone(found, "%s: %s" % (os.path.relpath(path, REPO),
                                                     found.group(0) if found else ""))

    def test_nothing_personal_is_shipped(self):
        # The owner's approved public address is the only exception.
        contact = re.escape("moa7amed" + "@" + "gmail.com")
        suspicious = re.compile(
            r"[A-Za-z]:\\Users\\(?!you\b|name\b|<)[\w.-]+|"
            r"/(?:home|Users)/(?!you\b|name\b|<|\.\.\.)[\w.-]+/|"
            r"(?<![\w.+-])(?!%s\b)[\w.+-]+@(?!example\.invalid|anthropic\.com)[\w-]+\.[a-z]{2,}"
            % contact)
        for path in text_files(REPO):
            with open(path, encoding="utf-8", errors="replace") as handle:
                found = suspicious.search(handle.read())
            self.assertIsNone(found, "%s: %s" % (os.path.relpath(path, REPO),
                                                 found.group(0) if found else ""))

    @unittest.skipUnless(CLAUDE, "the Claude Code CLI is not installed")
    def test_claude_cli_accepts_the_marketplace_and_every_plugin(self):
        targets = [REPO] + [os.path.join(REPO, "plugins", name) for name in self.folders]
        for target in targets:
            result = subprocess.run([CLAUDE, "plugin", "validate", target, "--strict"],
                                    stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                    stdin=subprocess.DEVNULL, timeout=180)
            output = result.stdout.decode("utf-8", errors="replace")
            self.assertEqual(result.returncode, 0, "%s\n%s" % (target, output))
            self.assertIn("Validation passed", output)


if __name__ == "__main__":
    unittest.main()
