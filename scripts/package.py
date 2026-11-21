#!/usr/bin/env python3
"""Build the release archive of one plugin: exactly the files git tracks in its folder.

    python scripts/package.py moa7amed-queue                 from the last commit
    python scripts/package.py moa7amed-queue --worktree      from the files as they are now
    python scripts/package.py moa7amed-queue --out dist

Writes dist/<plugin>-<version>.zip and a .sha256 file next to it, and prints both paths.
The archive holds the plugin folder's contents at its top level, so it can be loaded with
`claude --plugin-dir <archive>.zip` or unpacked anywhere. Users normally install through
the marketplace instead; the archive is for the GitHub release and for offline use.

Nothing is published. File dates inside the archive are fixed, so the same commit always
gives the same archive (and the same checksum).
"""
from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
import subprocess
import sys
import zipfile

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FIXED_DATE = (2026, 1, 1, 0, 0, 0)
EXECUTABLE = ("bin/", "hooks/", "runner/")


def git(*args):
    result = subprocess.run(["git"] + list(args), cwd=REPO, stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE)
    if result.returncode != 0:
        raise SystemExit("package: git %s failed: %s" % (" ".join(args),
                                                         result.stderr.decode(errors="replace")))
    return result.stdout


def files_of(folder, worktree):
    """(path inside the plugin, bytes) for every file that would be released."""
    if worktree:
        names = git("ls-files", "-z", "-co", "--exclude-standard", "--", folder).decode().split("\0")
        for name in sorted(filter(None, names)):
            full = os.path.join(REPO, name)
            if os.path.isfile(full):
                with open(full, "rb") as handle:
                    yield name[len(folder) + 1:], handle.read()
        return
    names = git("ls-tree", "-r", "-z", "--name-only", "HEAD", "--", folder).decode().split("\0")
    for name in sorted(filter(None, names)):
        yield name[len(folder) + 1:], git("show", "HEAD:%s" % name)


def build(plugin, out, worktree):
    folder = "plugins/%s" % plugin
    manifest = os.path.join(REPO, folder, ".claude-plugin", "plugin.json")
    if not os.path.isfile(manifest):
        raise SystemExit("package: no plugin at %s" % folder)
    if worktree:
        with open(manifest, encoding="utf-8") as handle:
            version = json.load(handle)["version"]
    else:
        version = json.loads(git("show", "HEAD:%s/.claude-plugin/plugin.json" % folder))["version"]
    os.makedirs(out, exist_ok=True)
    target = os.path.join(out, "%s-%s.zip" % (plugin, version))
    buffer = io.BytesIO()
    count = 0
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, data in files_of(folder, worktree):
            info = zipfile.ZipInfo(name, date_time=FIXED_DATE)
            info.compress_type = zipfile.ZIP_DEFLATED
            mode = 0o755 if name.startswith(EXECUTABLE) and not name.endswith((".json", ".cmd")) \
                else 0o644
            info.external_attr = (0o100000 | mode) << 16
            archive.writestr(info, data)
            count += 1
    data = buffer.getvalue()
    with open(target, "wb") as handle:
        handle.write(data)
    digest = hashlib.sha256(data).hexdigest()
    with open(target + ".sha256", "w", newline="\n") as handle:
        handle.write("%s  %s\n" % (digest, os.path.basename(target)))
    return target, digest, count, version


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("plugin")
    parser.add_argument("--out", default=os.path.join(REPO, "dist"))
    parser.add_argument("--worktree", action="store_true")
    args = parser.parse_args(argv)
    target, digest, count, version = build(args.plugin, os.path.abspath(args.out), args.worktree)
    print("%s %s: %d files" % (args.plugin, version, count))
    print(target)
    print("%s.sha256  %s" % (target, digest))
    return 0


if __name__ == "__main__":
    sys.exit(main())
