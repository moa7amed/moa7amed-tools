#!/usr/bin/env python3
"""Set the version of one plugin everywhere it is written, and open its changelog entry.

    python scripts/bump_version.py moa7amed-queue 0.3.2
    python scripts/bump_version.py moa7amed-queue 0.4.0-beta.1     (on the beta branch)
    python scripts/bump_version.py moa7amed-queue patch|minor|major
    python scripts/bump_version.py moa7amed-queue 0.3.2 --dry-run

It edits files and nothing else: no commit, no tag, no push. The version is written to

    plugins/<name>/.claude-plugin/plugin.json     the one place Claude Code reads it from
    plugins/<name>/release.json                   version and channel
    plugins/<name>/scripts/*/__init__.py          __version__, where a plugin has one
    plugins/<name>/CHANGELOG.md                   a new, empty entry for you to fill in

The marketplace entry deliberately carries no version. `release_check.py` refuses to pass
while the changelog entry is still the placeholder.
"""
from __future__ import annotations

import argparse
import io
import json
import os
import re
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
VERSION = re.compile(r"^(\d+)\.(\d+)\.(\d+)(?:-(alpha|beta|rc)\.(\d+))?$")
PLACEHOLDER = "TODO: describe what changed for users of this plugin."


def key(version):
    match = VERSION.match(version)
    if not match:
        return None
    major, minor, patch, label, number = match.groups()
    return (int(major), int(minor), int(patch),
            {"alpha": 0, "beta": 1, "rc": 2}[label] if label else 9, int(number or 0))


def read(path):
    with io.open(path, encoding="utf-8", newline="") as handle:
        return handle.read()


def write(path, text):
    with io.open(path, "w", encoding="utf-8", newline="") as handle:
        handle.write(text)


def next_version(current, step):
    major, minor, patch = key(current)[:3]
    if step == "major":
        return "%d.0.0" % (major + 1)
    if step == "minor":
        return "%d.%d.0" % (major, minor + 1)
    return "%d.%d.%d" % (major, minor, patch + 1)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("plugin")
    parser.add_argument("version", help="a version, or patch / minor / major")
    parser.add_argument("--dry-run", action="store_true", help="show what would change")
    parser.add_argument("--repo", default=REPO, help=argparse.SUPPRESS)
    args = parser.parse_args(argv)

    folder = os.path.join(args.repo, "plugins", args.plugin)
    manifest_path = os.path.join(folder, ".claude-plugin", "plugin.json")
    if not os.path.isfile(manifest_path):
        sys.stderr.write("bump_version: no plugin at %s\n" % folder)
        return 2
    manifest_text = read(manifest_path)
    current = json.loads(manifest_text)["version"]
    version = next_version(current, args.version) if args.version in ("patch", "minor", "major") \
        else args.version.lstrip("v")
    if key(version) is None:
        sys.stderr.write("bump_version: '%s' is not a version like 1.2.3 or 1.2.3-beta.4\n" % version)
        return 2
    if key(version) <= key(current):
        sys.stderr.write("bump_version: %s is not newer than the current %s\n" % (version, current))
        return 2
    channel = "stable" if key(version)[3] == 9 else "beta"
    marketplace = json.loads(read(os.path.join(args.repo, ".claude-plugin", "marketplace.json")))
    if channel == "beta" and not marketplace["name"].endswith("-beta"):
        sys.stderr.write(
            "bump_version: %s is a pre-release, and this is the stable marketplace '%s'.\n"
            "Pre-releases are made on the beta branch, where the marketplace is named '%s-beta'\n"
            "(see docs/versioning.md).\n" % (version, marketplace["name"], marketplace["name"]))
        return 2

    changes = []

    def replace(path, pattern, replacement, required=True):
        text = read(path)
        new, count = re.subn(pattern, replacement, text, count=1)
        if count == 0:
            if required:
                raise SystemExit("bump_version: could not find the version in %s" % path)
            return
        if new != text:
            changes.append(os.path.relpath(path, args.repo))
            if not args.dry_run:
                write(path, new)

    replace(manifest_path, r'("version"\s*:\s*")[^"]+(")', r"\g<1>%s\g<2>" % version)
    release_path = os.path.join(folder, "release.json")
    if os.path.isfile(release_path):
        replace(release_path, r'("version"\s*:\s*")[^"]+(")', r"\g<1>%s\g<2>" % version)
        replace(release_path, r'("channel"\s*:\s*")[^"]+(")', r"\g<1>%s\g<2>" % channel,
                required=False)
    scripts = os.path.join(folder, "scripts")
    if os.path.isdir(scripts):
        for package in sorted(os.listdir(scripts)):
            init = os.path.join(scripts, package, "__init__.py")
            if os.path.isfile(init):
                replace(init, r'(?m)^(__version__\s*=\s*["\'])[^"\']+(["\'])',
                        r"\g<1>%s\g<2>" % version, required=False)

    changelog = os.path.join(folder, "CHANGELOG.md")
    text = read(changelog) if os.path.isfile(changelog) else "# Changelog\n\n"
    if not re.search(r"(?m)^##\s+\[?v?%s\]?\s*$" % re.escape(version), text):
        newline = "\r\n" if "\r\n" in text else "\n"
        entry = "## %s%s%s%s%s%s" % (version, newline, newline, PLACEHOLDER, newline, newline)
        first = re.search(r"(?m)^##\s", text)
        text = text[:first.start()] + entry + text[first.start():] if first else text + entry
        changes.append(os.path.relpath(changelog, args.repo))
        if not args.dry_run:
            write(changelog, text)

    print("%s %s -> %s (%s channel)%s" % (args.plugin, current, version, channel,
                                          "  [dry run, nothing written]" if args.dry_run else ""))
    for path in changes:
        print("  %s" % path.replace("\\", "/"))
    print("Next: write the changelog entry, run `python scripts/release_check.py --tests`, "
          "commit.\nSee docs/release-checklist.md.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
