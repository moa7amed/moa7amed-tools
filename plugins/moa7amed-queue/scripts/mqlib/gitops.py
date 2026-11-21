"""Git helpers. Nothing here ever discards work: the only state-changing
operations are `add`+`commit`, `stash push` and `worktree add`."""
from __future__ import annotations

import os
import subprocess

from . import util

EMPTY_TREE = "4b825dc642cb6eb9a060e54bf8d69288fbee4904"
_IN_PROGRESS = ("MERGE_HEAD", "CHERRY_PICK_HEAD", "REVERT_HEAD", "BISECT_LOG",
                "rebase-merge", "rebase-apply")


def git(cwd, *args, **kwargs):
    """Run git; returns (returncode, stdout, stderr) and never raises for a git failure."""
    stdin_text = kwargs.pop("input", None)
    limit = kwargs.pop("timeout", 300)
    env = dict(os.environ)
    env["GIT_TERMINAL_PROMPT"] = "0"
    env.pop("GIT_DIR", None)
    env.pop("GIT_WORK_TREE", None)
    try:
        result = subprocess.run(
            ["git", "-c", "core.quotepath=false"] + list(args), cwd=cwd, env=env,
            input=stdin_text.encode("utf-8") if stdin_text is not None else None,
            stdin=None if stdin_text is not None else subprocess.DEVNULL,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=limit,
            **util.no_window_kwargs())
    except FileNotFoundError:
        return 127, "", "git is not installed or not on PATH"
    except subprocess.SubprocessError as exc:
        return 1, "", "git %s: %s" % (args[0] if args else "", exc)
    # Only line ends are trimmed from the output: `git status --porcelain` starts a line
    # with a space for a file that is modified but not staged, and that column matters.
    return (result.returncode,
            result.stdout.decode("utf-8", errors="replace").rstrip().lstrip(chr(13) + chr(10)),
            result.stderr.decode("utf-8", errors="replace").strip())


def is_repo(cwd):
    """True only when `cwd` is the top of a git working tree.

    A directory nested inside some other repository is deliberately not a
    match: staging or stashing there would reach far outside the project."""
    code, out, _ = git(cwd, "rev-parse", "--show-toplevel")
    if code != 0 or not out:
        return False
    try:
        return os.path.samefile(out, cwd)
    except OSError:
        return False


def head(cwd):
    code, out, _ = git(cwd, "rev-parse", "--verify", "-q", "HEAD")
    return out if code == 0 and out else None


def branch(cwd):
    code, out, _ = git(cwd, "symbolic-ref", "-q", "--short", "HEAD")
    return out if code == 0 and out else None


def status_lines(cwd, every_file=False, timeout=300):
    """Uncommitted changes, tracked and untracked (ignored files are not listed). With
    `every_file`, a new folder is listed file by file instead of as the folder."""
    code, out, err = git(cwd, "status", "--porcelain", *(["-uall"] if every_file else []),
                         timeout=timeout)
    if code != 0:
        raise util.QueueError("git status failed in %s: %s" % (cwd, err))
    return [line for line in out.splitlines() if line.strip()]


def operation_in_progress(cwd):
    """Name of an unfinished merge/rebase/cherry-pick/bisect, or None."""
    code, out, _ = git(cwd, "rev-parse", "--git-dir")
    if code != 0:
        return None
    git_dir = out if os.path.isabs(out) else os.path.join(cwd, out)
    for marker in _IN_PROGRESS:
        if os.path.exists(os.path.join(git_dir, marker)):
            return marker
    return None


def stash_all(cwd, message):
    """Move every uncommitted change (untracked files included) into a stash.

    Returns (stash_sha, error). The stash is the user's safety copy: it is
    never popped or dropped by this tool."""
    code, _, err = git(cwd, "stash", "push", "--include-untracked", "-m", message)
    if code != 0:
        return None, err or "git stash failed"
    code, sha, _ = git(cwd, "rev-parse", "--verify", "-q", "refs/stash")
    if code != 0 or not sha:
        return None, "git stash reported success but created no stash"
    if status_lines(cwd):
        return sha, "workspace is still not clean after stashing"
    return sha, None


def commit_all(cwd, message):
    """Stage and commit everything. Returns (sha, error); (None, None) if nothing to commit."""
    code, _, err = git(cwd, "add", "-A")
    if code != 0:
        return None, "git add failed: %s" % err
    code, _, _ = git(cwd, "diff", "--cached", "--quiet")
    if code == 0:
        return None, None
    code, out, err = git(cwd, "commit", "-q", "-F", "-", input=message)
    if code != 0:
        return None, "git commit failed: %s" % (err or out)
    return head(cwd), None


def commit_paths(cwd, paths, message):
    """Commit exactly these paths and nothing else (whatever else is staged stays staged
    and uncommitted). Returns (sha, error)."""
    if not paths:
        return None, "nothing to commit"
    code, _, err = git(cwd, "add", "-A", "--", *paths)
    if code != 0:
        return None, "git add failed: %s" % err
    code, out, err = git(cwd, "commit", "-q", "--only", "-F", "-", "--", *paths, input=message)
    if code != 0:
        return None, "git commit failed: %s" % (err or out)
    return head(cwd), None


def stash_paths(cwd, paths, message):
    """Set exactly these paths aside in a stash (untracked ones included). Returns (sha, error)."""
    if not paths:
        return None, "nothing to set aside"
    code, _, err = git(cwd, "stash", "push", "--include-untracked", "-m", message, "--", *paths)
    if code != 0:
        return None, err or "git stash failed"
    code, sha, _ = git(cwd, "rev-parse", "--verify", "-q", "refs/stash")
    return (sha, None) if code == 0 and sha else (None, "git stash created no stash")


def changed_between(cwd, old, new):
    code, out, _ = git(cwd, "diff", "--name-only", old or EMPTY_TREE, new)
    return [line for line in out.splitlines() if line] if code == 0 else []


def commits_between(cwd, old, new):
    """Commits made after `old` up to `new`, oldest first: [(sha, committer time, message)]."""
    code, out, _ = git(cwd, "log", "--reverse", "--format=%H%x1f%ct%x1f%B%x1e",
                       "%s..%s" % (old, new) if old else new)
    commits = []
    if code == 0:
        for record in out.split("\x1e"):
            parts = record.strip("\r\n").split("\x1f")
            if len(parts) == 3 and parts[1].strip().isdigit():
                commits.append((parts[0].strip(), int(parts[1]), parts[2]))
    return commits


def paths_from_status(lines):
    paths = []
    for line in lines:
        path = line[3:] if len(line) > 3 else line
        if " -> " in path:
            path = path.split(" -> ", 1)[1]
        paths.append(path.strip().strip('"'))
    return paths


def ensure_worktree(root, path, branch_name):
    """Create (or reuse) the queue's dedicated worktree. Returns an error string or None."""
    if head(root) is None:
        return "worktree mode needs at least one commit in the repository"
    git(root, "worktree", "prune")
    if os.path.isdir(path) and is_repo(path):
        return None
    code, _, _ = git(root, "show-ref", "--verify", "--quiet", "refs/heads/%s" % branch_name)
    if code == 0:
        code, _, err = git(root, "worktree", "add", path, branch_name)
    else:
        code, _, err = git(root, "worktree", "add", "-b", branch_name, path, "HEAD")
    if code != 0:
        return "could not create worktree at %s: %s" % (path, err)
    return None
