"""What the Claude plugin directory reads from this plugin, checked locally.

These do not prove acceptance: the directory also applies publisher rules (hooks, bin/,
shell pre-approvals and scripts need a Verified organization) and a policy review. They keep
the parts this repository controls correct: front matter, listing fields, the icon's bytes,
no download-and-run commands, and no credentials in what the plugin reads or writes.
"""
import json
import os
import re
import struct
import subprocess
import sys
import unittest

from helpers import MQ, ROOT, QueueTestCase

SHIPPED_TEXT = (".md", ".py", ".sh", ".ps1", ".cmd", ".json", "")


def read(*parts):
    with open(os.path.join(ROOT, *parts), encoding="utf-8") as handle:
        return handle.read()


def frontmatter(text):
    return text.split("---", 2)[1]


class Manifest(unittest.TestCase):

    def test_front_matter_values_are_unambiguous(self):
        # A plain YAML scalar must not contain ": " or " #" (a parser reads a mapping or a
        # comment there); such values are written in double quotes.
        for name in sorted(os.listdir(os.path.join(ROOT, "skills"))):
            head = frontmatter(read("skills", name, "SKILL.md"))
            for line in head.splitlines():
                match = re.match(r"^([\w-]+): (.+)$", line)
                if not match:
                    continue
                value = match.group(2)
                if value.startswith('"'):
                    self.assertTrue(value.endswith('"') and len(value) > 1, (name, line))
                    inner = value[1:-1].replace('\\\\', "").replace('\\"', "")
                    self.assertNotIn('"', inner, (name, line))
                else:
                    self.assertNotIn(": ", value, (name, line))
                    self.assertNotIn(" #", value, (name, line))
                    self.assertFalse(value[0] in "&*!|>%@`[{'", (name, line))

    def test_listing_fields(self):
        manifest = json.loads(read(".claude-plugin", "plugin.json"))
        self.assertEqual(manifest["icon"], "./assets/icon.png")
        self.assertRegex(manifest["privacyPolicyUrl"],
                         r"^https://github\.com/moa7amed/moa7amed-tools/blob/main/docs/[\w-]+\.md$")
        document = os.path.join(ROOT, "..", "..", manifest["privacyPolicyUrl"].rsplit("/main/", 1)[1])
        if not os.path.isdir(os.path.join(ROOT, "..", "..", "docs")):
            self.skipTest("an installed copy: the repository's docs are not here")
        self.assertTrue(os.path.isfile(document), document)
        self.assertEqual(manifest["license"], "MIT")
        self.assertTrue(os.path.isfile(os.path.join(ROOT, "LICENSE")))

    def test_the_hook_keeps_the_one_line_sh_form(self):
        # Claude Code runs a one-line `sh ...` hook through Git Bash on Windows; the
        # command/args form starts only where Windows finds sh on PATH.
        entry = json.loads(read("hooks", "hooks.json"))["hooks"]["UserPromptSubmit"][0]["hooks"][0]
        self.assertEqual(entry["command"], 'sh "${CLAUDE_PLUGIN_ROOT}/hooks/capture-prompt.sh"')
        self.assertNotIn("args", entry)


class Assets(unittest.TestCase):

    def test_the_icon_is_a_plain_png_and_nothing_else(self):
        with open(os.path.join(ROOT, "assets", "icon.png"), "rb") as handle:
            data = handle.read()
        self.assertEqual(data[:8], b"\x89PNG\r\n\x1a\n")
        width, height = struct.unpack(">II", data[16:24])
        self.assertEqual((width, height), (1024, 1024))
        self.assertLess(len(data), 2 * 1024 * 1024)
        # Walk the chunks: the file ends with IEND, with nothing appended (no archive, no
        # program hidden after the image) and only ordinary chunk types.
        offset, kinds = 8, []
        while offset < len(data):
            length, kind = struct.unpack(">I4s", data[offset:offset + 8])
            kinds.append(kind)
            offset += 12 + length
            if kind == b"IEND":
                break
        self.assertEqual(offset, len(data))
        self.assertTrue(set(kinds) <= {b"IHDR", b"PLTE", b"IDAT", b"IEND", b"sRGB", b"gAMA",
                                       b"pHYs", b"tRNS", b"cHRM", b"iCCP", b"tIME"}, kinds)
        self.assertNotIn(b"PK\x03\x04", data)

    def test_the_icon_is_the_only_image_and_nothing_names_it_in_a_command(self):
        images = []
        for folder, _dirs, files in os.walk(ROOT):
            for name in files:
                if re.search(r"\.(png|jpe?g|gif|webp|ico|ttf|otf|woff2?)$", name, re.I):
                    images.append(os.path.relpath(os.path.join(folder, name), ROOT))
        self.assertEqual([i.replace(os.sep, "/") for i in images], ["assets/icon.png"])
        for part in ("hooks/hooks.json", "hooks/capture-prompt.sh", "bin/moa7amed-queue",
                     "bin/moa7amed-queue.cmd"):
            self.assertNotIn("icon.png", read(*part.split("/")), part)


class NoDownloadAndRun(unittest.TestCase):

    def test_no_shipped_file_downloads_and_runs_a_program(self):
        pattern = re.compile(r"(?i)\b(?:curl|wget|Invoke-WebRequest|iwr|Invoke-RestMethod)\b[^\n]*"
                             r"\|\s*(?:sh|bash|python|iex)\b|\bnpx\s+\S|\buvx\s+\S|"
                             r"\bpip3?\s+install\s+\S|\bplaywright\s+install\b|"
                             r"\bnpm\s+(?:i|install|ci)\b|\bbrew\s+install\b")
        for folder, dirs, files in os.walk(ROOT):
            dirs[:] = [d for d in dirs if d not in ("tests", "__pycache__")]
            for name in files:
                if os.path.splitext(name)[1] not in SHIPPED_TEXT:
                    continue
                path = os.path.join(folder, name)
                with open(path, encoding="utf-8", errors="replace") as handle:
                    found = pattern.search(handle.read())
                self.assertIsNone(found, "%s: %s" % (os.path.relpath(path, ROOT),
                                                     found.group(0) if found else ""))


class Credentials(QueueTestCase):
    """The plugin reads Claude Code's settings for two values only and never writes what it
    finds there, or the environment, into its own files."""

    # Built at run time, so that this file does not itself contain what it plants.
    SECRET = "sk-" + "test-not-a-real-key-" + "0123456789"

    def test_settings_readers_take_only_what_they_need(self):
        from mqlib import maintenance, models
        config_dir = os.environ["CLAUDE_CONFIG_DIR"]
        os.makedirs(config_dir, exist_ok=True)
        with open(os.path.join(config_dir, "settings.json"), "w") as handle:
            json.dump({"env": {"ANTHROPIC_API_KEY": self.SECRET}, "apiKeyHelper": "x",
                       "availableModels": ["sonnet"],
                       "extraKnownMarketplaces": {"moa7amed-tools": {"autoUpdate": True}}},
                      handle)
        restrictions = models.settings_restrictions(self.project)
        self.assertNotIn(self.SECRET, json.dumps(restrictions, default=str))
        self.assertIn("sonnet", json.dumps(restrictions, default=str))
        setting = maintenance.auto_update_setting("moa7amed-tools", self.project)
        self.assertEqual(setting[0], True)
        self.assertNotIn(self.SECRET, json.dumps(setting))

    def test_secrets_in_the_environment_never_reach_the_queue_files(self):
        os.environ["ANTHROPIC_API_KEY"] = self.SECRET
        self.add("Fix the typo in the README heading")
        result = subprocess.run([sys.executable, MQ, "--project", self.project, "run"],
                                stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=300)
        self.assertNotIn(self.SECRET.encode(), result.stdout)
        self.mq("doctor")
        self.mq("status", "--json")
        for folder, _dirs, files in os.walk(self.home):
            for name in files:
                with open(os.path.join(folder, name), "rb") as handle:
                    self.assertNotIn(self.SECRET.encode(), handle.read(),
                                     os.path.relpath(os.path.join(folder, name), self.home))


if __name__ == "__main__":
    unittest.main()
