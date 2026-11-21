#!/usr/bin/env python3
"""Print the changelog entry of one version of a plugin, as release notes.

    python scripts/release_notes.py moa7amed-queue            the current version
    python scripts/release_notes.py moa7amed-queue 0.3.0
    python scripts/release_notes.py moa7amed-queue 0.3.1 --expect-current

With --expect-current the command fails unless that version is the one in the plugin's
plugin.json: a release tag must name the version that is actually in the commit.
"""
from __future__ import annotations

import argparse
import io
import json
import os
import re
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def read(path):
    with io.open(path, encoding="utf-8-sig") as handle:
        return handle.read()


def entry(text, version):
    lines, inside = [], False
    for line in text.splitlines():
        heading = re.match(r"^##\s+\[?v?([0-9][^\s\]]*)\]?", line)
        if heading:
            if inside:
                break
            inside = heading.group(1) == version
        elif inside:
            lines.append(line)
    return "\n".join(lines).strip()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("plugin")
    parser.add_argument("version", nargs="?")
    parser.add_argument("--expect-current", action="store_true")
    parser.add_argument("--repo", default=REPO, help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    folder = os.path.join(args.repo, "plugins", args.plugin)
    try:
        current = json.loads(read(os.path.join(folder, ".claude-plugin", "plugin.json")))["version"]
        changelog = read(os.path.join(folder, "CHANGELOG.md"))
    except (OSError, ValueError, KeyError) as exc:
        sys.stderr.write("release_notes: %s\n" % exc)
        return 2
    version = (args.version or current).lstrip("v")
    if args.expect_current and version != current:
        sys.stderr.write("release_notes: the tag says %s, plugin.json says %s\n"
                         % (version, current))
        return 1
    notes = entry(changelog, version)
    if not notes:
        sys.stderr.write("release_notes: CHANGELOG.md has no entry for %s\n" % version)
        return 1
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):
        pass
    print(notes)
    return 0


if __name__ == "__main__":
    sys.exit(main())
