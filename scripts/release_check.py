#!/usr/bin/env python3
"""Pre-release checks for the moa7amed-tools marketplace and the plugins in it.

    python scripts/release_check.py                    every check below except the test suites
    python scripts/release_check.py --plugin NAME      one plugin only
    python scripts/release_check.py --only versions,changelog
    python scripts/release_check.py --skip clean-install,upgrade
    python scripts/release_check.py --tests            also run the test suites (slow)
    python scripts/release_check.py --worktree         check the working tree, not the last commit
    python scripts/release_check.py --require-cli      a missing Claude Code CLI fails instead of skips
    python scripts/release_check.py --json

Checks

    versions       one version per plugin, stated once, the same everywhere it appears
    changelog      the version being released has release notes; a released version is never reused
    package        what would be shipped: required files, nothing generated, nothing oversized
    secrets        no credentials, private keys, e-mail addresses or local user paths
                   (the owner's approved public address excepted)
    dev-material   none of the specifications, prompts, transcripts or hand-off notes used
                   while developing a plugin (they are not public documentation)
    validate       `claude plugin validate --strict` on the marketplace and every plugin
    archive        the release archive (scripts/package.py) holds exactly the package, the same
                   commit always gives the same archive, and the unpacked archive validates
    clean-install  install from a copy of the repository into an empty Claude Code profile
    update         Claude Code's own commands take an installed earlier version to this one,
                   and back again when the marketplace offers the earlier version
    upgrade        data written by each earlier version still works, and going back works
    tests          the test suites (only with --tests)

Nothing here publishes, pushes, tags or changes the repository. It needs Python 3.8+, git,
and for three of the checks the Claude Code CLI. No network access and no API key are used.

Not covered, because it needs the repository to be hosted: fetching a marketplace over the
network, pinning it to a branch or tag, and Claude Code's background auto-update pass.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CHECKS = ("versions", "changelog", "package", "secrets", "dev-material", "validate", "archive",
          "clean-install", "update", "upgrade", "tests")
VERSION = re.compile(r"^(\d+)\.(\d+)\.(\d+)(?:-(alpha|beta|rc)\.(\d+))?$")
PRE_RANK = {"alpha": 0, "beta": 1, "rc": 2}

REQUIRED_FILES = (".claude-plugin/plugin.json", "release.json", "README.md", "CHANGELOG.md",
                  "LICENSE")
MAX_FILE_BYTES = 512 * 1024
FORBIDDEN = re.compile(
    r"(^|/)(__pycache__|\.pytest_cache|\.mypy_cache|node_modules|\.venv|venv)(/|$)|"
    r"\.(pyc|pyo|db|db-journal|sqlite|sqlite3|log|tmp|bak|swp|pem|p12|pfx|key|keystore)$|"
    r"(^|/)(\.env(\..*)?|id_rsa|id_ed25519|credentials\.json|settings\.local\.json|\.DS_Store|"
    r"Thumbs\.db)$", re.IGNORECASE)
TEXT_SUFFIXES = (".py", ".md", ".sh", ".cmd", ".bat", ".ps1", ".json", ".txt", ".yml", ".yaml",
                 ".toml", ".cfg", ".ini", "")

# What must never be in a published file. Each entry: (label, pattern).
SECRETS = (
    ("Anthropic API key", re.compile(r"sk-ant-[A-Za-z0-9_-]{20,}")),
    ("API key", re.compile(r"\bsk-[A-Za-z0-9]{32,}\b")),
    ("GitHub token", re.compile(r"\b(?:ghp|gho|ghu|ghs|ghr)_[A-Za-z0-9]{30,}\b|\bgithub_pat_[A-Za-z0-9_]{30,}\b")),
    ("AWS access key", re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b")),
    ("Google API key", re.compile(r"\bAIza[0-9A-Za-z_-]{35}\b")),
    ("Slack token", re.compile(r"\bxox[baprs]-[0-9A-Za-z-]{10,}\b")),
    ("private key", re.compile(r"-----BEGIN (?:RSA |EC |DSA |OPENSSH |ENCRYPTED )?PRIVATE KEY-----")),
    ("bearer token", re.compile(r"(?i)\bauthorization:\s*bearer\s+[A-Za-z0-9._-]{20,}")),
    ("password assignment", re.compile(
        r"(?i)\b(?:password|passwd|secret|api_key|apikey|access_token)\s*[=:]\s*[\"'][^\"'\s$<{]{12,}[\"']")),
    ("Windows user folder", re.compile(r"[A-Za-z]:\\\\?Users\\\\?(?!you\b|name\b|Public\b|<)[\w.-]+")),
    ("home folder", re.compile(r"/(?:home|Users)/(?!you\b|name\b|runner\b|<|\.\.\.)[\w.-]+/")),
    ("e-mail address", re.compile(
        r"[\w.+-]+@(?!example\.invalid|example\.com|example\.org|anthropic\.com|"
        r"users\.noreply\.github\.com|github\.com)[\w-]+(?:\.[\w-]+)*\.[a-z]{2,}\b")),
)


# Development material: the specifications, prompts, transcripts of conversations and the
# notes agents hand each other while building a plugin. Public documentation is written for users;
# none of this may be published, quoted or referred to. File names first, then text.
DEV_PATHS = re.compile(
    r"(^|/)(prompts?|specs?|transcripts?|hand-?offs?|dev-notes|private-notes)(/|$)|\.jsonl$|"
    r"(^|/)(prompt|spec|handoff|hand-off|transcript)s?([._-][^/]*)?\.(md|txt)$", re.IGNORECASE)
DEV_TEXT = (
    ("reference to the specifications or prompts used in development", re.compile(
        r"(?i)\b(?:earlier|internal|development|owner'?s|pasted|planning)\s+"
        r"(?:spec|specs|specification|specifications|prompt|prompts|brief)\b")),
    ("numbered item of a specification used in development", re.compile(
        r"(?i)\bcategor(?:y|ies)\s+\d+(?:\s*(?:,|-|and)\s*\d+)*\s*:")),
    ("pasted-content or system-reminder markup", re.compile(
        r"<\s*/?\s*(?:pasted_content|system-reminder)\b")),
    ("transcript of a development conversation", re.compile(
        r"(?i)\b(?:conversation|chat)\s+transcripts?\b|\"(?:originSessionId|parentUuid)\"")),
    ("hand-off note between agents", re.compile(r"(?i)\bagent\s+hand-?offs?\b")),
)


class Report(object):
    def __init__(self):
        self.rows = []

    def add(self, check, subject, status, detail):
        self.rows.append({"check": check, "subject": subject, "status": status, "detail": detail})

    def ok(self, check, subject, detail):
        self.add(check, subject, "ok", detail)

    def fail(self, check, subject, detail):
        self.add(check, subject, "fail", detail)

    def warn(self, check, subject, detail):
        self.add(check, subject, "warn", detail)

    def skip(self, check, subject, detail):
        self.add(check, subject, "skip", detail)

    def failed(self):
        return [row for row in self.rows if row["status"] == "fail"]


# ----------------------------------------------------------------------------- helpers

def run(command, cwd=None, env=None, timeout=900, stdin=None):
    """Run a command. Returns (exit code, combined output)."""
    try:
        result = subprocess.run(command, cwd=cwd, env=env, stdout=subprocess.PIPE,
                                stderr=subprocess.STDOUT, timeout=timeout,
                                stdin=subprocess.DEVNULL if stdin is None else None, input=stdin)
    except FileNotFoundError as exc:
        return 127, str(exc)
    except subprocess.TimeoutExpired:
        return 124, "timed out after %d s: %s" % (timeout, " ".join(command))
    return result.returncode, result.stdout.decode("utf-8", errors="replace")


def read_text(path):
    with open(path, "rb") as handle:
        return handle.read().decode("utf-8-sig", errors="replace")


def load_json(path):
    return json.loads(read_text(path))


def version_key(text):
    match = VERSION.match(text or "")
    if not match:
        return None
    major, minor, patch, label, number = match.groups()
    return (int(major), int(minor), int(patch), PRE_RANK[label] if label else 9, int(number or 0))


def channel_of(version):
    key = version_key(version)
    return None if key is None else ("stable" if key[3] == 9 else "beta")


def is_git(repo):
    """True only when `repo` is itself the top of a git working tree. A plain folder that
    merely sits somewhere inside another repository (a home directory under version
    control, for instance) is not: git would answer for that other repository."""
    code, out = run(["git", "rev-parse", "--show-toplevel"], cwd=repo)
    if code != 0 or not out.strip():
        return False
    try:
        return os.path.samefile(out.strip(), repo)
    except OSError:
        return False


def tracked_files(repo, folder=".", worktree=False):
    """Files that are part of the repository below `folder` (relative, forward slashes).
    With `worktree`, new files that are not ignored count as well. In a plain directory
    (an exported copy) every file counts."""
    if is_git(repo):
        command = ["git", "ls-files", "-z"] + (["-co", "--exclude-standard"] if worktree else [])
        code, out = run(command + ["--", folder], cwd=repo)
        if code == 0:
            return sorted(name for name in out.split("\0") if name)
    found = []
    for base, dirs, files in os.walk(os.path.join(repo, folder)):
        dirs[:] = [name for name in dirs if name != ".git"]
        for name in files:
            found.append(os.path.relpath(os.path.join(base, name), repo).replace("\\", "/"))
    return sorted(found)


def changelog_entries(path):
    entries, current, lines = [], None, []
    for line in read_text(path).splitlines():
        heading = re.match(r"^##\s+\[?v?([0-9][^\s\]]*)\]?", line)
        if heading:
            if current:
                entries.append((current, "\n".join(lines).strip()))
            current, lines = heading.group(1), []
        elif current:
            lines.append(line)
    if current:
        entries.append((current, "\n".join(lines).strip()))
    return entries


def _force_remove(function, path, _info):
    os.chmod(path, stat.S_IWRITE)
    function(path)


def export_tree(repo, target, worktree):
    """Copy what would be published into `target`: the last commit, or with `worktree`
    the files as they are now (tracked and new ones, ignored files left out)."""
    os.makedirs(target)
    if not is_git(repo):
        worktree = True
    if worktree:
        if is_git(repo):
            code, out = run(["git", "ls-files", "-z", "-co", "--exclude-standard"], cwd=repo)
            names = [name for name in out.split("\0") if name] if code == 0 else []
        else:
            names = tracked_files(repo)
        for name in names:
            source = os.path.join(repo, name)
            if os.path.isfile(source):
                destination = os.path.join(target, name)
                os.makedirs(os.path.dirname(destination), exist_ok=True)
                shutil.copy2(source, destination)
        return "the working tree"
    archive = os.path.join(target, "..", "export.tar")
    code, out = run(["git", "archive", "--format=tar", "-o", os.path.abspath(archive), "HEAD"],
                    cwd=repo)
    if code != 0:
        raise RuntimeError("git archive failed: %s" % out.strip())
    import tarfile
    with tarfile.open(archive) as handle:
        handle.extractall(target)
    os.remove(archive)
    return "the last commit"


def claude_cli():
    return shutil.which("claude")


class Context(object):
    def __init__(self, repo, args):
        self.repo = repo
        self.args = args
        self.report = Report()
        self.marketplace = load_json(os.path.join(repo, ".claude-plugin", "marketplace.json"))
        self.plugins = []
        for entry in self.marketplace.get("plugins", []):
            source = entry.get("source")
            if isinstance(source, str) and (not args.plugin or entry.get("name") in args.plugin):
                self.plugins.append((entry["name"], os.path.normpath(os.path.join(repo, source)),
                                     entry))
        self._tmp = None
        self._export = None

    def tmp(self):
        if self._tmp is None:
            self._tmp = os.path.realpath(tempfile.mkdtemp(prefix="release-check-"))
        return self._tmp

    def export(self):
        if self._export is None:
            target = os.path.join(self.tmp(), "export")
            self.export_source = export_tree(self.repo, target, self.args.worktree)
            self._export = target
        return self._export

    def cleanup(self):
        if self._tmp and not self.args.keep:
            shutil.rmtree(self._tmp, onerror=_force_remove)

    def manifest(self, folder):
        return load_json(os.path.join(folder, ".claude-plugin", "plugin.json"))

    def release(self, folder):
        path = os.path.join(folder, "release.json")
        return load_json(path) if os.path.isfile(path) else {}


# ----------------------------------------------------------------------------- checks

def check_versions(ctx):
    report = ctx.report
    market_name = ctx.marketplace.get("name", "")
    beta_marketplace = market_name.endswith("-beta")
    for name, folder, entry in ctx.plugins:
        problems = []
        try:
            manifest = ctx.manifest(folder)
        except (OSError, ValueError) as exc:
            report.fail("versions", name, "plugin.json cannot be read: %s" % exc)
            continue
        version = manifest.get("version")
        if manifest.get("name") != name or os.path.basename(folder) != name:
            problems.append("folder, manifest name and marketplace entry must all be '%s'" % name)
        if version_key(version) is None:
            problems.append("'%s' is not a version of the form 1.2.3 or 1.2.3-beta.4" % version)
        if "version" in entry:
            problems.append("the marketplace entry repeats the version; state it in plugin.json "
                            "only (Claude Code reads plugin.json first, a second copy can only "
                            "disagree)")
        release = ctx.release(folder)
        if not release:
            problems.append("release.json is missing")
        else:
            if release.get("version") != version:
                problems.append("release.json says %s" % release.get("version"))
            if release.get("channel") != channel_of(version):
                problems.append("release.json channel is %s, the version belongs to %s"
                                % (release.get("channel"), channel_of(version)))
            if str(release.get("data_format")) not in [str(item) for item in
                                                       release.get("reads_data_formats") or []]:
                problems.append("release.json: data_format must be one of reads_data_formats")
        scripts = os.path.join(folder, "scripts")
        if os.path.isdir(scripts):
            for package in sorted(os.listdir(scripts)):
                init = os.path.join(scripts, package, "__init__.py")
                if os.path.isfile(init):
                    found = re.search(r'(?m)^__version__\s*=\s*["\']([^"\']+)["\']', read_text(init))
                    if found and found.group(1) != version:
                        problems.append("%s/__init__.py says %s" % (package, found.group(1)))
        if channel_of(version) == "beta" and not beta_marketplace:
            problems.append("a pre-release version in the stable marketplace '%s': pre-releases "
                            "belong on the beta branch, whose marketplace is named '%s-beta'"
                            % (market_name, market_name))
        if problems:
            report.fail("versions", name, "; ".join(problems))
        else:
            report.ok("versions", name, "%s (%s channel), stated once in plugin.json and matched "
                      "by release.json and the code" % (version, channel_of(version)))


def _tags(repo, name):
    code, out = run(["git", "tag", "--list", "%s--v*" % name], cwd=repo)
    if code != 0:
        return []
    return sorted((tag for tag in out.split() if version_key(tag.split("--v", 1)[1])),
                  key=lambda tag: version_key(tag.split("--v", 1)[1]))


def check_changelog(ctx):
    report = ctx.report
    for name, folder, _entry in ctx.plugins:
        path = os.path.join(folder, "CHANGELOG.md")
        try:
            version = ctx.manifest(folder)["version"]
            entries = changelog_entries(path)
        except (OSError, ValueError, KeyError) as exc:
            report.fail("changelog", name, "cannot be read: %s" % exc)
            continue
        problems, notes = [], []
        if not entries:
            problems.append("CHANGELOG.md has no `## <version>` entries")
        else:
            newest, text = entries[0]
            if newest != version:
                problems.append("the newest entry is %s, the version is %s" % (newest, version))
            elif len(text) < 80 or re.search(r"\bTODO\b", text):
                problems.append("the entry for %s is empty or still a placeholder" % version)
            keys = [version_key(item) for item, _ in entries]
            if None in keys:
                problems.append("an entry heading is not a version")
            elif keys != sorted(keys, reverse=True) or len(set(keys)) != len(keys):
                problems.append("entries are not in descending order, or one is repeated")
        if is_git(ctx.repo):
            relative = os.path.relpath(folder, ctx.repo).replace("\\", "/")
            tags = _tags(ctx.repo, name)
            current = "%s--v%s" % (name, version)
            if current in tags:
                code, _ = run(["git", "diff", "--quiet", current, "--", relative], cwd=ctx.repo)
                if code != 0:
                    problems.append("%s is already tagged and the plugin has changed since. "
                                    "Give the change a new version: Claude Code offers an update "
                                    "only when the version changes" % current)
                else:
                    notes.append("identical to the tagged release %s" % current)
            elif tags:
                latest = tags[-1].split("--v", 1)[1]
                if version_key(version) and version_key(version) <= version_key(latest):
                    problems.append("%s is not newer than the latest tagged release %s"
                                    % (version, latest))
                else:
                    notes.append("next release after %s" % latest)
            else:
                notes.append("no release of this plugin has been tagged yet")
        if problems:
            report.fail("changelog", name, "; ".join(problems))
        else:
            report.ok("changelog", name, "release notes for %s present%s"
                      % (version, "; " + "; ".join(notes) if notes else ""))


def check_package(ctx):
    report = ctx.report
    for name, folder, _entry in ctx.plugins:
        relative = os.path.relpath(folder, ctx.repo).replace("\\", "/")
        files = tracked_files(ctx.repo, relative, ctx.args.worktree)
        problems, total = [], 0
        for required in REQUIRED_FILES:
            if "%s/%s" % (relative, required) not in files:
                problems.append("%s is missing%s" % (required, "" if not os.path.isfile(
                    os.path.join(folder, required)) else " from the repository (not committed)"))
        for path in files:
            full = os.path.join(ctx.repo, path)
            if not os.path.isfile(full):
                continue
            size = os.path.getsize(full)
            total += size
            inside = path[len(relative) + 1:]
            if FORBIDDEN.search(inside):
                problems.append("%s must not be shipped" % inside)
            elif size > MAX_FILE_BYTES:
                problems.append("%s is %d KB (limit %d KB)" % (inside, size // 1024,
                                                               MAX_FILE_BYTES // 1024))
            if inside.endswith(".sh") or inside.startswith("bin/") and "." not in os.path.basename(inside):
                with open(full, "rb") as handle:
                    if b"\r\n" in handle.read():
                        problems.append("%s has Windows line endings; sh cannot run it" % inside)
        if not files:
            problems.append("no files found in %s" % relative)
        if problems:
            report.fail("package", name, "; ".join(problems))
        else:
            report.ok("package", name, "%d files, %.0f KB; required files present, nothing "
                      "generated or oversized" % (len(files), total / 1024.0))
        if is_git(ctx.repo):
            code, out = run(["git", "status", "--porcelain", "--untracked-files=all", "--", relative],
                            cwd=ctx.repo)
            dirty = [line for line in out.splitlines() if line.strip()] if code == 0 else []
            if dirty and not ctx.args.worktree:
                report.warn("package", name, "%d uncommitted change(s) in %s: a release is made "
                            "from a commit, these are not part of it yet" % (len(dirty), relative))


def _personal_strings(repo):
    """This machine's home folder and the committer's e-mail address: never publishable,
    and not knowable in advance, so they are looked up when the check runs."""
    found = []
    home = os.path.expanduser("~")
    if len(os.path.basename(home)) >= 3:
        found.append(("this machine's home folder", home))
        found.append(("this machine's home folder", home.replace("\\", "/")))
    code, out = run(["git", "config", "user.email"], cwd=repo)
    if code == 0 and "@" in out and not out.strip().endswith("users.noreply.github.com"):
        found.append(("the committer's e-mail address", out.strip()))
    return found


# The only e-mail address the owner has approved for publication. Declaring an address in
# a manifest does not approve it: any other address in the tree is a finding.
APPROVED_PUBLIC_EMAILS = ("moa7amed@gmail.com",)


def manifest_emails(repo):
    """(file, address) for every e-mail the manifests declare: the marketplace's
    `owner.email` and each plugin's `author.email`."""
    found = []
    path = os.path.join(".claude-plugin", "marketplace.json")
    market = load_json(os.path.join(repo, path))
    people = [(path, market.get("owner"))]
    for entry in market.get("plugins", []):
        source = entry.get("source")
        if isinstance(source, str):
            path = os.path.normpath(os.path.join(source, ".claude-plugin", "plugin.json"))
            if os.path.isfile(os.path.join(repo, path)):
                people.append((path, load_json(os.path.join(repo, path)).get("author")))
    for path, person in people:
        email = person.get("email") if isinstance(person, dict) else None
        if isinstance(email, str):
            found.append((path.replace(os.sep, "/"), email.strip()))
    return found


def check_secrets(ctx):
    report = ctx.report
    contacts = set(APPROVED_PUBLIC_EMAILS)
    personal = [(label, literal) for label, literal in _personal_strings(ctx.repo)
                if literal.lower() not in contacts]
    findings, scanned = [], 0
    for path, email in manifest_emails(ctx.repo):
        if email.lower() not in contacts:
            findings.append("%s declares an e-mail address that is not approved for "
                            "publication (%s...)" % (path, email[:12]))
    for path in tracked_files(ctx.repo, ".", ctx.args.worktree):
        full = os.path.join(ctx.repo, path)
        if not os.path.isfile(full) or os.path.splitext(path)[1].lower() not in TEXT_SUFFIXES:
            continue
        if os.path.getsize(full) > 4 * 1024 * 1024:
            continue
        scanned += 1
        text = read_text(full)
        for number, line in enumerate(text.splitlines(), 1):
            for label, pattern in SECRETS:
                for match in pattern.finditer(line):
                    if label == "e-mail address" and match.group(0).lower() in contacts:
                        continue
                    findings.append("%s:%d %s (%s...)" % (path, number, label, match.group(0)[:12]))
                    break
            lowered = line.lower()
            for label, literal in personal:
                if literal.lower() in lowered:
                    findings.append("%s:%d %s" % (path, number, label))
    if not scanned:
        report.fail("secrets", "repository", "no file was scanned: the check cannot vouch for "
                    "anything (is %s the repository?)" % ctx.repo)
    elif findings:
        report.fail("secrets", "repository", "%d finding(s): %s%s" % (
            len(findings), "; ".join(findings[:12]), " ..." if len(findings) > 12 else ""))
    else:
        report.ok("secrets", "repository", "%d text files scanned: no credentials, private keys, "
                  "e-mail addresses or local user paths" % scanned)


def check_dev_material(ctx):
    """Every tracked file, which covers the release archive (a subset of them). Findings
    name the file, the line and the kind of material, never the text itself."""
    report = ctx.report
    findings, scanned = [], 0
    for path in tracked_files(ctx.repo, ".", ctx.args.worktree):
        full = os.path.join(ctx.repo, path)
        if not os.path.isfile(full):
            continue
        if DEV_PATHS.search(path.replace(os.sep, "/")):
            findings.append("%s (file name)" % path)
        if os.path.splitext(path)[1].lower() not in TEXT_SUFFIXES + (".svg", ".vtt", ".html") \
                or os.path.getsize(full) > 4 * 1024 * 1024:
            continue
        scanned += 1
        for number, line in enumerate(read_text(full).splitlines(), 1):
            for label, pattern in DEV_TEXT:
                if pattern.search(line):
                    findings.append("%s:%d %s" % (path, number, label))
    if not scanned:
        report.fail("dev-material", "repository", "no file was scanned: the check cannot "
                    "vouch for anything (is %s the repository?)" % ctx.repo)
    elif findings:
        report.fail("dev-material", "repository", "%d finding(s): %s%s" % (
            len(findings), "; ".join(findings[:12]), " ..." if len(findings) > 12 else ""))
    else:
        report.ok("dev-material", "repository", "%d text files scanned: no development "
                  "specifications, prompts, transcripts or hand-off notes" % scanned)


def _need_cli(ctx, check):
    if claude_cli():
        return True
    message = "the Claude Code CLI (`claude`) is not installed"
    if ctx.args.require_cli:
        ctx.report.fail(check, "environment", message)
    else:
        ctx.report.skip(check, "environment", message + "; not checked")
    return False


def check_validate(ctx):
    if not _need_cli(ctx, "validate"):
        return
    targets = [("marketplace", ctx.repo)] + [(name, folder) for name, folder, _ in ctx.plugins]
    for subject, target in targets:
        code, out = run([claude_cli(), "plugin", "validate", target, "--strict"], timeout=300)
        if code == 0 and "Validation passed" in out:
            ctx.report.ok("validate", subject, "claude plugin validate --strict passed")
        else:
            ctx.report.fail("validate", subject, out.strip()[-600:])


def check_archive(ctx):
    report = ctx.report
    sys.path.insert(0, os.path.join(ctx.repo, "scripts"))
    try:
        import package
    except ImportError as exc:
        report.fail("archive", "tooling", "scripts/package.py cannot be loaded: %s" % exc)
        return
    if not is_git(ctx.repo):
        report.skip("archive", "repository", "not a git repository")
        return
    for name, folder, _entry in ctx.plugins:
        out_a, out_b = os.path.join(ctx.tmp(), "archive-a"), os.path.join(ctx.tmp(), "archive-b")
        try:
            target, digest, count, version = package.build(name, out_a, ctx.args.worktree)
            _target, digest_b, _count, _version = package.build(name, out_b, ctx.args.worktree)
        except SystemExit as exc:
            report.fail("archive", name, str(exc))
            continue
        import zipfile
        with zipfile.ZipFile(target) as archive:
            inside = sorted(archive.namelist())
            unpacked = os.path.join(ctx.tmp(), "archive-unpacked-%s" % name)
            archive.extractall(unpacked)
        relative = os.path.relpath(folder, ctx.repo).replace("\\", "/")
        expected = sorted(path[len(relative) + 1:] for path in
                          tracked_files(ctx.repo, relative, ctx.args.worktree)
                          if os.path.isfile(os.path.join(ctx.repo, path)))
        problems = []
        if inside != expected:
            problems.append("the archive differs from the package: missing %s, extra %s"
                            % (sorted(set(expected) - set(inside))[:5],
                               sorted(set(inside) - set(expected))[:5]))
        if digest != digest_b:
            problems.append("building twice gave different archives")
        if claude_cli():
            code, out = run([claude_cli(), "plugin", "validate", unpacked, "--strict"], timeout=300)
            if code != 0:
                problems.append("the unpacked archive fails validation: %s" % out.strip()[-300:])
        if problems:
            report.fail("archive", name, "; ".join(problems))
        else:
            report.ok("archive", name, "%s: %d files, sha256 %s..., reproducible%s"
                      % (os.path.basename(target), count, digest[:16],
                         ", unpacked copy validates" if claude_cli() else ""))


def _isolated_env(ctx, label):
    """An environment in which Claude Code and the plugins see nothing of this machine's
    real configuration: an empty Claude Code profile and throwaway data directories."""
    base = os.path.join(ctx.tmp(), label)
    os.makedirs(base, exist_ok=True)
    env = dict(os.environ)
    for key in list(env):
        if key.startswith(("MOA7AMED_", "CTQ_")) or key in ("CLAUDE_PROJECT_DIR", "GIT_DIR",
                                                            "GIT_WORK_TREE"):
            del env[key]
    env["CLAUDE_CONFIG_DIR"] = os.path.join(base, "claude-config")
    return base, env


def check_clean_install(ctx):
    if not _need_cli(ctx, "clean-install"):
        return
    report, cli = ctx.report, claude_cli()
    try:
        export = ctx.export()
    except RuntimeError as exc:
        report.fail("clean-install", "marketplace", str(exc))
        return
    base, env = _isolated_env(ctx, "clean-install")
    market = ctx.marketplace["name"]
    code, out = run([cli, "plugin", "marketplace", "add", export], env=env, timeout=300)
    if code != 0:
        report.fail("clean-install", "marketplace", "could not add the marketplace: %s"
                    % out.strip()[-400:])
        return
    for name, folder, _entry in ctx.plugins:
        version = ctx.manifest(folder)["version"]
        release = ctx.release(folder)
        plugin_id = "%s@%s" % (name, market)
        code, out = run([cli, "plugin", "install", plugin_id], env=env, timeout=300)
        if code != 0:
            report.fail("clean-install", name, "install failed: %s" % out.strip()[-400:])
            continue
        code, out = run([cli, "plugin", "list", "--json"], env=env, timeout=120)
        try:
            listed = [item for item in json.loads(out) if item.get("id") == plugin_id]
        except ValueError:
            listed = []
        if code != 0 or not listed:
            report.fail("clean-install", name, "not listed after installing: %s" % out.strip()[-300:])
            continue
        installed = listed[0]
        root = installed.get("installPath") or ""
        problems = []
        if installed.get("version") != version:
            problems.append("installed as version %s, expected %s" % (installed.get("version"),
                                                                      version))
        if not installed.get("enabled"):
            problems.append("installed but not enabled")
        if not os.path.isfile(os.path.join(root, ".claude-plugin", "plugin.json")):
            problems.append("the installed copy at %s is incomplete" % root)
        always_on = None
        code, details = run([cli, "plugin", "details", plugin_id], env=env, timeout=120)
        found = re.search(r"Always-on:\s*~?([\d,]+)\s*tok", details)
        if found:
            always_on = int(found.group(1).replace(",", ""))
            budget = release.get("always_on_token_budget")
            if budget and always_on > int(budget):
                problems.append("adds ~%d tokens to every session (Claude Code's estimate); the "
                                "plugin's own budget is %d" % (always_on, budget))
        smoke_env = dict(env)
        for key, value in (release.get("isolation_env") or {}).items():
            smoke_env[key] = value.replace("{tmp}", base)
        ran = 0
        for command in release.get("smoke") or []:
            command = [sys.executable if part == "{python}" else part for part in command]
            code, out = run(command, cwd=root, env=smoke_env, timeout=300)
            ran += 1
            if code != 0:
                problems.append("`%s` failed in the installed copy: %s"
                                % (" ".join(command[1:]), out.strip()[-300:]))
        if problems:
            report.fail("clean-install", name, "; ".join(problems))
        else:
            report.ok("clean-install", name, "installed %s from %s into an empty profile; "
                      "enabled; %d smoke command(s) passed%s"
                      % (version, ctx.export_source, ran,
                         "; always-on context ~%d tokens (Claude Code's estimate)" % always_on
                         if always_on is not None else ""))


def _baselines(ctx):
    path = os.path.join(ctx.repo, "release", "baselines.json")
    return load_json(path) if os.path.isfile(path) else {}


def _export_commit(ctx, ref, target):
    """Check out one commit of this repository into `target` (a plain folder)."""
    code, _ = run(["git", "cat-file", "-e", "%s^{commit}" % ref], cwd=ctx.repo)
    if code != 0:
        return "%s is not in this clone (a shallow clone? fetch the full history)" % ref
    archive = os.path.join(ctx.tmp(), "commit.tar")
    code, out = run(["git", "archive", "--format=tar", "-o", archive, ref], cwd=ctx.repo)
    if code != 0:
        return "git archive failed: %s" % out.strip()
    import tarfile
    os.makedirs(target)
    with tarfile.open(archive) as handle:
        handle.extractall(target)
    os.remove(archive)
    return None


def _installed(cli, env, plugin_id):
    code, out = run([cli, "plugin", "list", "--json"], env=env, timeout=120)
    try:
        return next((item for item in json.loads(out) if item.get("id") == plugin_id), None)
    except ValueError:
        return None


def check_update(ctx):
    """Install an earlier version from a marketplace folder, put this version into that
    folder (as a published update would), and let Claude Code's own commands update the
    installation. Then offer the earlier version again and let them take it back."""
    if not _need_cli(ctx, "update"):
        return
    report, cli = ctx.report, claude_cli()
    if not is_git(ctx.repo):
        report.skip("update", "repository", "not a git repository: earlier versions cannot be "
                    "checked out")
        return
    baselines = _baselines(ctx)
    for name, folder, _entry in ctx.plugins:
        entries = [entry for entry in baselines.get(name) or [] if entry.get("update_in_place")]
        if not entries:
            report.skip("update", name, "no earlier version marked update_in_place in "
                        "release/baselines.json")
            continue
        version = ctx.manifest(folder)["version"]
        release = ctx.release(folder)
        for entry in entries:
            subject = "%s %s -> %s" % (name, entry["version"], version)
            label = "update-%s-%s" % (name, entry["version"])
            old = os.path.join(ctx.tmp(), label + "-old")
            error = _export_commit(ctx, entry.get("tag") or entry["commit"], old)
            if error:
                report.fail("update", subject, error)
                continue
            base, env = _isolated_env(ctx, label)
            market = os.path.join(base, "marketplace")
            shutil.copytree(old, market)
            market_name = load_json(os.path.join(market, ".claude-plugin", "marketplace.json"))["name"]
            plugin_id = "%s@%s" % (name, market_name)

            def publish(source):
                shutil.rmtree(market, onerror=_force_remove)
                shutil.copytree(source, market)
                steps = ([cli, "plugin", "marketplace", "update", market_name],
                         [cli, "plugin", "update", plugin_id])
                for command in steps:
                    code, out = run(command, env=env, timeout=300)
                    if code != 0:
                        return "`%s` failed: %s" % (" ".join(command[1:]), out.strip()[-300:])
                return None

            problems = []
            for command in ([cli, "plugin", "marketplace", "add", market],
                            [cli, "plugin", "install", plugin_id]):
                code, out = run(command, env=env, timeout=300)
                if code != 0:
                    problems.append("`%s` failed: %s" % (" ".join(command[1:]), out.strip()[-300:]))
                    break
            before = None if problems else _installed(cli, env, plugin_id)
            if not problems and (before or {}).get("version") != entry["version"]:
                problems.append("expected %s to be installed first, found %s"
                                % (entry["version"], (before or {}).get("version")))
            if not problems:
                error = publish(ctx.export())
                after = _installed(cli, env, plugin_id)
                if error:
                    problems.append(error)
                elif (after or {}).get("version") != version:
                    problems.append("after the update the installed version is %s, not %s"
                                    % ((after or {}).get("version"), version))
                else:
                    root = after.get("installPath") or ""
                    smoke_env = dict(env)
                    for key, value in (release.get("isolation_env") or {}).items():
                        smoke_env[key] = value.replace("{tmp}", base)
                    for command in release.get("smoke") or []:
                        command = [sys.executable if part == "{python}" else part for part in command]
                        code, out = run(command, cwd=root, env=smoke_env, timeout=300)
                        if code != 0:
                            problems.append("`%s` failed in the updated copy: %s"
                                            % (" ".join(command[1:]), out.strip()[-300:]))
            if not problems:
                error = publish(old)
                back = _installed(cli, env, plugin_id)
                if error:
                    problems.append("going back: %s" % error)
                elif (back or {}).get("version") != entry["version"]:
                    problems.append("after offering %s again the installed version is %s"
                                    % (entry["version"], (back or {}).get("version")))
            if problems:
                report.fail("update", subject, "; ".join(problems))
            else:
                report.ok("update", subject, "`claude plugin marketplace update` and `claude "
                          "plugin update` moved the installation %s -> %s, the updated copy "
                          "passed its smoke commands, and the same commands moved it back to %s "
                          "when the marketplace offered that version again"
                          % (entry["version"], version, entry["version"]))


def check_upgrade(ctx):
    report = ctx.report
    baselines = _baselines(ctx)
    if not is_git(ctx.repo):
        report.skip("upgrade", "repository", "not a git repository: earlier versions cannot be "
                    "checked out")
        return
    for name, folder, _entry in ctx.plugins:
        script = os.path.join(folder, "tests", "upgrade_check.py")
        entries = baselines.get(name) or []
        if not entries:
            report.skip("upgrade", name, "no earlier version listed in release/baselines.json")
            continue
        if not os.path.isfile(script):
            report.fail("upgrade", name, "tests/upgrade_check.py is missing")
            continue
        for entry in entries:
            subject = "%s from %s" % (name, entry["version"])
            ref = entry.get("tag") or entry["commit"]
            code, _ = run(["git", "cat-file", "-e", "%s^{commit}" % ref], cwd=ctx.repo)
            if code != 0:
                report.fail("upgrade", subject, "%s is not in this clone (a shallow clone? "
                            "fetch the full history)" % ref)
                continue
            old = os.path.join(ctx.tmp(), "old-%s-%s" % (name, entry["version"]))
            os.makedirs(old)
            archive = os.path.join(ctx.tmp(), "old.tar")
            code, out = run(["git", "archive", "--format=tar", "-o", archive, ref], cwd=ctx.repo)
            if code != 0:
                report.fail("upgrade", subject, "git archive failed: %s" % out.strip())
                continue
            import tarfile
            with tarfile.open(archive) as handle:
                handle.extractall(old)
            os.remove(archive)
            _base, env = _isolated_env(ctx, "upgrade-%s-%s" % (name, entry["version"]))
            code, out = run([sys.executable, "-B", script,
                             "--old-root", os.path.join(old, entry.get("root", ".")),
                             "--old-cli", entry["cli"], "--old-version", entry["version"]],
                            cwd=folder, env=env, timeout=900)
            lines = [line for line in out.strip().splitlines() if line.strip()]
            if code == 0:
                report.ok("upgrade", subject, lines[-1] if lines else "passed")
            else:
                report.fail("upgrade", subject, " | ".join(lines[-6:]))


def check_tests(ctx):
    if not ctx.args.tests:
        ctx.report.skip("tests", "all", "not run (add --tests)")
        return
    suites = [("marketplace", ctx.repo)] + [(name, folder) for name, folder, _ in ctx.plugins]
    for subject, folder in suites:
        if not os.path.isdir(os.path.join(folder, "tests")):
            ctx.report.fail("tests", subject, "no tests folder")
            continue
        code, out = run([sys.executable, "-B", "-m", "unittest", "discover", "-s", "tests",
                         "-t", "tests"], cwd=folder, timeout=3600)
        tail = [line for line in out.strip().splitlines() if line.strip()][-3:]
        summary = " ".join(tail)
        if code == 0:
            ctx.report.ok("tests", subject, summary)
        else:
            failures = [line for line in out.splitlines() if line.startswith(("FAIL:", "ERROR:"))]
            ctx.report.fail("tests", subject, "%s %s" % (summary, "; ".join(failures[:8])))


RUNNERS = {"versions": check_versions, "changelog": check_changelog, "package": check_package,
           "secrets": check_secrets, "dev-material": check_dev_material,
           "validate": check_validate, "archive": check_archive,
           "clean-install": check_clean_install, "update": check_update,
           "upgrade": check_upgrade, "tests": check_tests}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0],
                                     formatter_class=argparse.RawDescriptionHelpFormatter,
                                     epilog="\n\n".join(__doc__.split("\n\n")[1:]))
    parser.add_argument("--repo", default=REPO, help=argparse.SUPPRESS)
    parser.add_argument("--plugin", action="append", help="check only this plugin (repeatable)")
    parser.add_argument("--only", help="comma-separated checks to run")
    parser.add_argument("--skip", help="comma-separated checks to leave out")
    parser.add_argument("--tests", action="store_true", help="also run the test suites")
    parser.add_argument("--worktree", action="store_true",
                        help="install and test the files as they are now, not the last commit")
    parser.add_argument("--require-cli", action="store_true",
                        help="fail when the Claude Code CLI is not installed")
    parser.add_argument("--keep", action="store_true", help="keep the temporary directory")
    parser.add_argument("--json", action="store_true", help="print machine-readable JSON")
    args = parser.parse_args(argv)
    selected = [name.strip() for name in (args.only or ",".join(CHECKS)).split(",") if name.strip()]
    skipped = [name.strip() for name in (args.skip or "").split(",") if name.strip()]
    unknown = [name for name in selected + skipped if name not in CHECKS]
    if unknown:
        parser.error("unknown check(s): %s (known: %s)" % (", ".join(unknown), ", ".join(CHECKS)))
    if args.tests and "tests" not in selected:
        selected.append("tests")
    try:
        ctx = Context(os.path.abspath(args.repo), args)
    except (OSError, ValueError) as exc:
        sys.stderr.write("release_check: the marketplace manifest cannot be read: %s\n" % exc)
        return 2
    if args.plugin and not ctx.plugins:
        sys.stderr.write("release_check: no such plugin: %s\n" % ", ".join(args.plugin))
        return 2
    try:
        for name in CHECKS:
            if name in selected and name not in skipped:
                if name == "tests" and not args.tests:
                    continue
                RUNNERS[name](ctx)
    finally:
        ctx.cleanup()
    report = ctx.report
    if args.json:
        print(json.dumps({"passed": not report.failed(), "results": report.rows}, indent=2))
    else:
        marks = {"ok": "ok  ", "fail": "FAIL", "warn": "WARN", "skip": "skip"}
        for row in report.rows:
            print("[%s] %-14s %-28s %s" % (marks[row["status"]], row["check"], row["subject"],
                                           row["detail"]))
        counts = {status: len([row for row in report.rows if row["status"] == status])
                  for status in marks}
        print("\n%d passed, %d failed, %d warning(s), %d skipped"
              % (counts["ok"], counts["fail"], counts["warn"], counts["skip"]))
    return 1 if report.failed() else 0


if __name__ == "__main__":
    sys.exit(main())
