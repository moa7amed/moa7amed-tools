"""The plugin package itself: manifest, skills, hook wiring, branding, and validation by
the Claude Code CLI. (The marketplace that lists the plugin is tested at the repository root.)"""
import json
import os
import re
import shutil
import subprocess
import sys
import unittest

from helpers import MQ, ROOT

CLAUDE = shutil.which("claude")
SKILLS = ("queue-add", "queue-run", "queue-status", "queue-retry", "queue-priority",
          "queue-cancel", "model-auto", "model-opus", "model-sonnet", "model-haiku",
          "project-plan", "infrastructure-assess", "architecture-review", "plan-status",
          "plan-execute", "plan-revise", "plan-approve", "replica", "replica-recon",
          "replica-flows", "replica-design", "replica-backend", "replica-plan", "replica-build",
          "replica-compare", "replica-test", "replica-status", "bidstudio", "bid-analyze",
          "bid-compliance", "bid-clarifications", "bid-technical", "bid-estimation", "bid-pricing",
          "bid-commercial", "bid-review", "bid-status", "grillme", "auto")
OLD_BRAND = re.compile(r"claude-task-queue|Claude Task Queue|\bctq\b|\bCTQ_", re.IGNORECASE)


def load(*parts):
    with open(os.path.join(ROOT, *parts), encoding="utf-8") as handle:
        return json.load(handle)


def read(*parts):
    with open(os.path.join(ROOT, *parts), encoding="utf-8") as handle:
        return handle.read()


def frontmatter(text):
    match = re.match(r"---\n(.*?)\n---\n", text, re.S)
    return match.group(1) if match else None


def cli(*args):
    result = subprocess.run([sys.executable, MQ] + list(args), stdout=subprocess.PIPE,
                            stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL)
    return result.stdout.decode("utf-8", errors="replace")


class PluginPackage(unittest.TestCase):

    def test_manifest_identifies_the_plugin(self):
        manifest = load(".claude-plugin", "plugin.json")
        self.assertEqual(manifest["name"], "moa7amed-queue")
        self.assertEqual(manifest["author"]["name"], "moa7amed")
        self.assertRegex(manifest["version"], r"^\d+\.\d+\.\d+$")
        self.assertEqual(manifest["license"], "MIT")
        self.assertIn("Intelligent AI Coding Task Orchestrator", manifest["description"])
        self.assertIn('__version__ = "%s"' % manifest["version"],
                      read("scripts", "mqlib", "__init__.py"))
        self.assertTrue("## %s" % manifest["version"] in read("CHANGELOG.md"),
                        "CHANGELOG.md has no entry for %s" % manifest["version"])

    def test_plugin_is_self_contained(self):
        # Installable and versioned on its own: everything it needs is inside its folder.
        for name in ("README.md", "CHANGELOG.md", "LICENSE", "tests"):
            self.assertTrue(os.path.exists(os.path.join(ROOT, name)), name)
        for folder, _dirs, files in os.walk(os.path.join(ROOT, "scripts")):
            for name in files:
                if name.endswith(".py"):
                    text = read(os.path.relpath(os.path.join(folder, name), ROOT))
                    self.assertNotRegex(text, r"plugins[/\\]", name)  # no reach into siblings

    def test_every_skill_is_complete(self):
        self.assertEqual(sorted(os.listdir(os.path.join(ROOT, "skills"))), sorted(SKILLS))
        for name in SKILLS:
            text = read("skills", name, "SKILL.md")
            head = frontmatter(text)
            self.assertIsNotNone(head, name)
            self.assertIn("name: %s\n" % name, head + "\n")
            self.assertRegex(head, r"(?m)^description: \S", name)
            # Least privilege: a skill pre-approves only the commands (and, for grouped
            # commands, the actions) that its own instructions run; never the command with
            # arbitrary arguments, and the PowerShell rules mirror the Bash rules.
            body = text.split("---", 2)[2]
            bash = re.findall(r'(?m)^  - Bash\(sh "\$\{CLAUDE_PLUGIN_ROOT\}/bin/moa7amed-queue" '
                              r'(.+)\*\)$', head)
            power = re.findall(r'(?m)^  - PowerShell\(& "\$\{CLAUDE_PLUGIN_ROOT\}/bin/'
                               r'moa7amed-queue\.cmd" (.+)\*\)$', head)
            self.assertTrue(bash, name)
            self.assertEqual(bash, power, name)
            self.assertNotIn('moa7amed-queue" *)', head, name)
            used = re.findall(r'bin/moa7amed-queue(?:\.cmd)?" (\w[\w-]*(?: [a-z][a-z-]*)?)', body)
            for command in used:
                self.assertTrue(any(command == rule or command.startswith(rule + " ")
                                    or command.split(" ")[0] == rule for rule in bash),
                                "%s runs `%s` but does not pre-approve it" % (name, command))
            for rule in bash:
                self.assertTrue(any(command == rule or command.startswith(rule + " ")
                                    or command.split(" ")[0] == rule.split(" ")[0]
                                    and " " not in rule and True for command in used),
                                "%s pre-approves `%s` without using it" % (name, rule))
            # Every command a skill tells Claude to run must be a real command.
            commands = re.findall(r'bin/moa7amed-queue" (\w[\w-]*)', text)
            self.assertTrue(commands, name)
            for command in commands:
                self.assertIn(command, self.commands(), "%s uses `%s`" % (name, command))
            # Skills refer to each other by the plugin's current name.
            for reference in re.findall(r"/([\w-]+):(?:queue|model)-\w+", text):
                self.assertEqual(reference, "moa7amed-queue", name)

    def commands(self):
        sys.path.insert(0, os.path.join(ROOT, "scripts"))
        from mqlib import cli as module
        return set(module.QUEUE_COMMANDS) | set(module.GLOBAL_COMMANDS)

    def test_side_effecting_skills_are_user_invoked_only(self):
        for name in ("queue-run", "queue-cancel", "model-opus", "model-sonnet", "model-haiku",
                     # planning: they analyse, decide, approve or import, so only the user starts them
                     "project-plan", "infrastructure-assess", "architecture-review",
                     "plan-execute", "plan-revise", "plan-approve",
                     # replica: only the entry point (it records the request) and the
                     # read-only status may be started by Claude
                     "replica-recon", "replica-flows", "replica-design", "replica-backend",
                     "replica-plan", "replica-build", "replica-compare", "replica-test",
                     # BidStudio: only the entry point and the read-only status
                     "bid-analyze", "bid-compliance", "bid-clarifications", "bid-technical",
                     "bid-estimation", "bid-pricing", "bid-commercial", "bid-review",
                     # a review the owner asks for
                     "grillme"):
            self.assertIn("disable-model-invocation: true",
                          frontmatter(read("skills", name, "SKILL.md")), name)

    def test_what_every_session_carries_stays_small(self):
        # The description of a skill Claude may invoke by itself is part of every session,
        # whether the queue is used or not. Keep that text short; details belong in the body,
        # which is only read when the skill runs.
        always_on = {}
        for name in SKILLS:
            head = frontmatter(read("skills", name, "SKILL.md"))
            description = re.search(r"(?m)^description: (.*)$", head).group(1)
            self.assertLessEqual(len(description), 160, name)
            if "disable-model-invocation: true" not in head:
                always_on[name] = description
        self.assertEqual(sorted(always_on), sorted(["model-auto", "plan-status", "queue-add",
                                             "queue-priority", "queue-retry", "queue-status",
                                             "replica", "replica-status", "bidstudio",
                                             "bid-status", "auto"]))
        # 611 characters before Replica (0.5.0); Replica and BidStudio add about 200 each.
        self.assertLessEqual(sum(len(text) for text in always_on.values()), 1050)
        # One hook, and it starts no interpreter unless a prompt is being captured.
        self.assertEqual(list(load("hooks", "hooks.json")["hooks"]), ["UserPromptSubmit"])
        # A capture hook that runs out of time must not hand the prompt to Claude as well:
        # Claude Code (2.1.295 and later) then blocks the prompt instead (verified live with
        # 2.1.329: without it the queued request was also answered by the session).
        entry = load("hooks", "hooks.json")["hooks"]["UserPromptSubmit"][0]["hooks"][0]
        self.assertEqual(entry.get("onFailure"), "block")
        self.assertEqual(entry.get("timeout"), 30)
        self.assertFalse(os.path.exists(os.path.join(ROOT, ".mcp.json")))
        self.assertFalse(os.path.isdir(os.path.join(ROOT, "agents")))

    def test_every_source_file_is_valid_python_3_8(self):
        # The plugin declares Python 3.8 as its minimum. Wherever the suite runs, the
        # sources are parsed with the 3.8 grammar, so newer syntax cannot slip in. (Library
        # calls that 3.8 lacks are only caught by running on 3.8 itself, which CI does.)
        import ast
        checked = 0
        for top in ("scripts", "tests"):
            for folder, _dirs, files in os.walk(os.path.join(ROOT, top)):
                for name in files:
                    if name.endswith(".py"):
                        path = os.path.join(folder, name)
                        with open(path, encoding="utf-8") as handle:
                            ast.parse(handle.read(), filename=path, feature_version=(3, 8))
                        checked += 1
        self.assertGreater(checked, 20)

    def test_the_shipped_code_contains_no_network_client(self):
        # docs/security-and-privacy.md says the plugin makes no network connection itself.
        # `socket` is imported for the host name only.
        forbidden = re.compile(
            r"^\s*(?:import|from)\s+(?:urllib|http|ftplib|smtplib|poplib|imaplib|telnetlib|"
            r"xmlrpc|socketserver|ssl|asyncio|requests|httpx|aiohttp|websocket)\b", re.M)
        for folder, _dirs, files in os.walk(os.path.join(ROOT, "scripts")):
            for name in files:
                if not name.endswith(".py"):
                    continue
                text = read(os.path.relpath(os.path.join(folder, name), ROOT))
                self.assertIsNone(forbidden.search(text), name)
                uses = re.findall(r"\bsocket\.(\w+)", text)
                self.assertEqual([use for use in uses if use != "gethostname"], [], name)
                # The one exception: Replica's page capture drives a browser, in its own
                # program, started only by `replica capture` for the URLs the user gives.
                if re.search(r"^\s*(?:import|from)\s+(?:playwright|selenium|pyppeteer)\b", text,
                             re.M):
                    self.assertEqual(name, "replica_browser.py")
        for name in ("hooks/capture-prompt.sh", "bin/moa7amed-queue", "runner/run-queue.ps1"):
            self.assertNotRegex(read(name), r"(?i)\b(curl|wget|Invoke-WebRequest|Invoke-RestMethod|"
                                            r"System\.Net|nc -)", name)
        # The only programs it starts that use the network do so on the user's behalf.
        git_verbs = set()
        for name in ("gitops.py", "engine.py", "cli.py", "pyrunner.py"):
            git_verbs.update(re.findall(r'\bgit\(\w+, "([a-z-]+)"', read("scripts", "mqlib", name)))
        self.assertEqual(git_verbs & {"push", "fetch", "pull", "clone", "remote", "reset",
                                      "clean", "checkout"}, set())

    def test_hook_configuration_points_at_an_existing_script(self):
        hooks = load("hooks", "hooks.json")["hooks"]
        self.assertEqual(list(hooks), ["UserPromptSubmit"])
        handler = hooks["UserPromptSubmit"][0]["hooks"][0]
        self.assertEqual(handler["type"], "command")
        relative = re.search(r"\$\{CLAUDE_PLUGIN_ROOT\}/([\w./-]+)", handler["command"]).group(1)
        self.assertTrue(os.path.isfile(os.path.join(ROOT, relative)), relative)

    def test_shell_scripts_use_unix_line_endings(self):
        for relative in ("bin/moa7amed-queue", "bin/ctq", "hooks/capture-prompt.sh",
                         "runner/run-queue.sh"):
            with open(os.path.join(ROOT, relative), "rb") as handle:
                self.assertNotIn(b"\r", handle.read(), relative)

    def test_powershell_runner_is_ascii(self):
        # Windows PowerShell 5.1 reads a script without a BOM in the system code page.
        with open(os.path.join(ROOT, "runner", "run-queue.ps1"), "rb") as handle:
            handle.read().decode("ascii")

    @unittest.skipUnless(CLAUDE, "the Claude Code CLI is not installed")
    def test_claude_cli_accepts_the_plugin(self):
        result = subprocess.run([CLAUDE, "plugin", "validate", ROOT, "--strict"],
                                stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                stdin=subprocess.DEVNULL, timeout=180)
        output = result.stdout.decode("utf-8", errors="replace")
        self.assertEqual(result.returncode, 0, output)
        self.assertIn("Validation passed", output)


class Branding(unittest.TestCase):

    def test_command_presents_the_product(self):
        self.assertRegex(cli("--version").strip(), r"^moa7amed-queue \d+\.\d+\.\d+$")
        text = cli("--help")
        for expected in ("usage: moa7amed-queue", "Intelligent AI Coding Task Orchestrator",
                         "Queue. Route. Execute. Verify.", "Published by moa7amed."):
            self.assertIn(expected, text)
        # The only mention of the old name is the note that it still works as an alias.
        old = [line for line in text.splitlines() if OLD_BRAND.search(line)]
        self.assertEqual(old, ["`ctq` is kept as an alias of this command: it was the name "
                               "before version 0.3.0."])

    def test_help_of_every_command_uses_the_new_name(self):
        for command in ("add", "run", "launch", "model", "route", "models", "migrate",
                        "install-cli", "config", "retry"):
            text = cli(command, "--help")
            self.assertIn("usage: moa7amed-queue %s" % command, text)
            self.assertIsNone(OLD_BRAND.search(text), command)

    def test_what_claude_code_shows_carries_no_old_branding(self):
        public = [os.path.join("hooks", "hooks.json"), os.path.join(".claude-plugin", "plugin.json")]
        public += [os.path.join("skills", name, "SKILL.md") for name in SKILLS]
        for relative in public:
            self.assertIsNone(OLD_BRAND.search(read(relative)), relative)

    def test_old_names_survive_only_as_documented_compatibility(self):
        # Wherever an old identifier remains, the same file says why it is still there.
        explained = re.compile(r"before the rebrand|before 0\.3\.0|versions? 0\.1|earlier version|"
                               r"Backward-compatible|LEGACY|legacy", re.IGNORECASE)
        for folder in ("bin", "hooks", "runner", "scripts"):
            for base, dirs, files in os.walk(os.path.join(ROOT, folder)):
                dirs[:] = [d for d in dirs if d != "__pycache__"]
                for name in files:
                    relative = os.path.relpath(os.path.join(base, name), ROOT)
                    text = read(relative)
                    if OLD_BRAND.search(text):
                        self.assertTrue(explained.search(text),
                                        "%s uses an old name without saying why" % relative)

    def test_no_personal_data_or_local_paths_are_shipped(self):
        # The owner's approved public address is the only exception.
        contact = re.escape("moa7amed" + "@" + "gmail.com")
        suspicious = re.compile(
            r"[A-Za-z]:\\Users\\(?!you\b|name\b|<)[\w.-]+|/(?:home|Users)/(?!you\b|name\b|<|\.\.\.)[\w.-]+/|"
            r"(?<![\w.+-])(?!%s\b)[\w.+-]+@(?!example\.invalid|anthropic\.com)[\w-]+\.[a-z]{2,}" % contact)
        for base, dirs, files in os.walk(ROOT):
            dirs[:] = [d for d in dirs if d not in ("__pycache__", ".git")]
            for name in files:
                if os.path.splitext(name)[1] not in (".py", ".md", ".sh", ".cmd", ".ps1", ".json", ""):
                    continue
                relative = os.path.relpath(os.path.join(base, name), ROOT)
                found = suspicious.search(read(relative))
                self.assertIsNone(found, "%s: %s" % (relative, found.group(0) if found else ""))


if __name__ == "__main__":
    unittest.main()
