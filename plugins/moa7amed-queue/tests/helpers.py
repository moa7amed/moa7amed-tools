"""Shared fixtures for the moa7amed-queue test suite.

Every test gets its own MOA7AMED_QUEUE_HOME and its own throwaway project directory, and
a fake `claude` executable, so nothing outside the temporary directory is read
or modified and no real Claude session is started.
"""
import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import time
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MQ = os.path.join(ROOT, "scripts", "mq.py")
FAKE = os.path.join(ROOT, "tests", "fake_claude.py")
RUNNER_PS1 = os.path.join(ROOT, "runner", "run-queue.ps1")
IS_WINDOWS = os.name == "nt"
POWERSHELL = shutil.which("powershell") or shutil.which("pwsh")
SH = shutil.which("sh")

sys.dont_write_bytecode = True  # keep the source tree free of __pycache__
sys.path.insert(0, os.path.join(ROOT, "scripts"))

# Environment that must not leak from the developer's shell into a test.
_NAMES = ("HOME", "PROJECT", "RUNNER", "TASK_ID", "CAPTURE_PREFIX", "PYTHON", "CLAUDE_BIN")
_SCRUB = tuple("MOA7AMED_QUEUE_" + name for name in _NAMES) + tuple("CTQ_" + name for name in _NAMES) + (
          "CLAUDE_PROJECT_DIR", "GIT_DIR", "GIT_WORK_TREE",
          "FAKE_UNAVAILABLE_MODELS", "FAKE_ACTUAL_MODEL", "FAKE_REVIEW_ANSWER",
          "FAKE_NO_MODEL_FLAG", "FAKE_PLUGINS", "ANTHROPIC_MODEL", "DISABLE_UPDATES",
          "DISABLE_AUTOUPDATER", "FORCE_AUTOUPDATE_PLUGINS",
          "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC")

# PowerShell parameter for each runner option used in the tests.
PS_PARAMS = {"max_tasks": "MaxTasks", "on_dirty": "OnDirty", "workspace": "Workspace",
             "commit": "Commit", "timeout_minutes": "TimeoutMinutes", "watch": "Watch",
             "stop_on_failure": "StopOnFailure", "force": "Force",
             "permission_mode": "PermissionMode", "model": "Model",
             "allowed_tools": "AllowedTools", "poll_seconds": "PollSeconds",
             "routing_mode": "RoutingMode", "no_routing": "NoRouting"}


def _force_remove(function, path, _info):
    os.chmod(path, stat.S_IWRITE)
    function(path)


def kill_tree(pid):
    if IS_WINDOWS:
        subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    else:
        import signal
        try:
            os.killpg(os.getpgid(pid), signal.SIGKILL)
        except OSError:
            try:
                os.kill(pid, signal.SIGKILL)
            except OSError:
                pass


class QueueTestCase(unittest.TestCase):
    """Base class: an isolated queue home, a project directory and a fake claude."""

    use_git = True

    def setUp(self):
        self.tmp = os.path.realpath(tempfile.mkdtemp(prefix="mq-test-"))
        self.home = os.path.join(self.tmp, "home")
        self.project = os.path.join(self.tmp, "project")
        os.makedirs(self.project)
        self.fake_log_path = os.path.join(self.tmp, "fake-claude.jsonl")
        self.fake_claude = self._make_fake_claude()
        self._saved_env = dict(os.environ)
        for name in _SCRUB:
            os.environ.pop(name, None)
        os.environ.update({
            "MOA7AMED_QUEUE_HOME": self.home,
            "MOA7AMED_QUEUE_CLAUDE_BIN": self.fake_claude,
            "FAKE_CLAUDE_LOG": self.fake_log_path,
            "FAKE_STATE_DIR": self.tmp,
            "FAKE_SCRIPT": os.path.join(self.tmp, "fake-script.json"),
            # Claude Code settings that could restrict models must come from the test only.
            "CLAUDE_CONFIG_DIR": os.path.join(self.tmp, "claude-config"),
            # Commits made by the tests must not depend on (or touch) the user's git setup.
            "GIT_CONFIG_GLOBAL": os.path.join(self.tmp, "gitconfig"),
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_AUTHOR_NAME": "Queue Test", "GIT_AUTHOR_EMAIL": "queue-test@example.invalid",
            "GIT_COMMITTER_NAME": "Queue Test", "GIT_COMMITTER_EMAIL": "queue-test@example.invalid",
        })
        self._background = []
        self._queues = []
        if self.use_git:
            self.init_git(self.project)
        # Retries without the default 30 s pause keep the suite fast. The routing review
        # (an extra model call for borderline tasks) is tested on its own.
        os.makedirs(self.home, exist_ok=True)
        with open(os.path.join(self.home, "config.json"), "w") as handle:
            # Superpowers detection asks the CLI; its own tests switch this back to auto.
            json.dump({"retry_backoff_seconds": 0, "routing_classifier": "off",
                       "workflow_superpowers": "no"}, handle)
        self.trust_fake_cli()

    def trust_fake_cli(self):
        """Record that the fake CLI supports --model, as `moa7amed-queue` would after asking it
        (saves two process starts per test; tests of the detection remove this file)."""
        stat = os.stat(self.fake_claude)
        key = "%s|%d|%d" % (self.fake_claude, int(stat.st_mtime), stat.st_size)
        with open(os.path.join(self.home, "models.json"), "w") as handle:
            json.dump({"models": {}, "cli": {key: {"path": self.fake_claude, "version": "9.9.9",
                                                   "supports_model": True}}}, handle)

    def set_config(self, **values):
        """Change global settings in-process (fast)."""
        path = os.path.join(self.home, "config.json")
        with open(path) as handle:
            config = json.load(handle)
        config.update(values)
        with open(path, "w") as handle:
            json.dump(config, handle)

    def script(self, task_id, *directives):
        """Tell the fake CLI how to behave for a task without putting it in the prompt."""
        path = os.environ["FAKE_SCRIPT"]
        data = {}
        if os.path.exists(path):
            with open(path) as handle:
                data = json.load(handle)
        data.setdefault(str(task_id), []).extend(directives)
        with open(path, "w") as handle:
            json.dump(data, handle)

    def routing(self, task_id):
        return self.task(task_id)["routing"]

    def events_of(self, kind):
        return [event for event in self.fake_events() if event["event"] == kind]

    def starts(self):
        """Task runs the fake CLI actually began: [(task id, requested model)]."""
        return [(event["task"], event["model"]) for event in self.fake_events()
                if event["event"] == "start"]

    def tearDown(self):
        for process in self._background:
            if process.poll() is None:
                kill_tree(process.pid)
                process.wait()
        for q in self._queues:
            q.close()
        os.environ.clear()
        os.environ.update(self._saved_env)
        for _ in range(5):
            try:
                shutil.rmtree(self.tmp, onerror=_force_remove)
                break
            except OSError:
                time.sleep(0.5)

    # ------------------------------------------------------------- fixtures

    def _make_fake_claude(self):
        if IS_WINDOWS:
            path = os.path.join(self.tmp, "fake-claude.cmd")
            with open(path, "w", newline="\r\n") as handle:
                handle.write('@echo off\n"%s" "%s" %%*\n' % (sys.executable, FAKE))
        else:
            path = os.path.join(self.tmp, "fake-claude")
            with open(path, "w") as handle:
                handle.write('#!/bin/sh\nexec "%s" "%s" "$@"\n' % (sys.executable, FAKE))
            os.chmod(path, 0o755)
        return path

    def git(self, *args, cwd=None, check=True):
        result = subprocess.run(["git"] + list(args), cwd=cwd or self.project,
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        out = result.stdout.decode("utf-8", errors="replace").strip()
        if check and result.returncode != 0:
            self.fail("git %s failed: %s" % (" ".join(args),
                                             result.stderr.decode("utf-8", errors="replace")))
        return out

    def init_git(self, path):
        self.git("init", "-q", "-b", "main", cwd=path)
        with open(os.path.join(path, "README.md"), "w") as handle:
            handle.write("# sample project\n")
        self.git("add", "-A", cwd=path)
        self.git("commit", "-q", "-m", "initial commit", cwd=path)

    def write(self, relative, text, root=None):
        path = os.path.join(root or self.project, relative)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(text)
        return path

    def read(self, relative, root=None):
        with open(os.path.join(root or self.project, relative), encoding="utf-8") as handle:
            return handle.read()

    # ------------------------------------------------------------- moa7amed-queue

    def mq(self, *args, project=None, stdin=None, check=True, cwd=None):
        """Run the real command line in a subprocess. Returns (exit code, stdout, stderr)."""
        command = [sys.executable, MQ, "--project", project or self.project] + list(args)
        result = subprocess.run(command, input=stdin, cwd=cwd, stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE)
        out = result.stdout.decode("utf-8", errors="replace")
        err = result.stderr.decode("utf-8", errors="replace")
        if check and result.returncode != 0:
            self.fail("moa7amed-queue %s exited %d\nstdout: %s\nstderr: %s"
                      % (" ".join(args), result.returncode, out, err))
        return result.returncode, out, err

    def mq_json(self, *args, **kwargs):
        _, out, _ = self.mq(*(list(args) + ["--json"]), **kwargs)
        return json.loads(out)

    def queue(self, project=None):
        """Open the queue in-process (fast path for arranging and inspecting state)."""
        from mqlib import store, util
        q = store.Queue(util.resolve_project_root(project or self.project))
        self._queues.append(q)
        return q

    def add(self, prompt, **kwargs):
        return self.queue().add(prompt, **kwargs)

    def task(self, task_id, project=None):
        q = self.queue(project)
        return q.task_dict(q.get(task_id), with_runs=True)

    def statuses(self):
        return {task["id"]: task["status"] for task in self.queue().tasks()}

    def fake_events(self):
        if not os.path.exists(self.fake_log_path):
            return []
        with open(self.fake_log_path, encoding="utf-8") as handle:
            return [json.loads(line) for line in handle if line.strip()]

    def wait_for(self, predicate, timeout=60, message="condition"):
        deadline = time.time() + timeout
        while time.time() < deadline:
            value = predicate()
            if value:
                return value
            time.sleep(0.2)
        self.fail("timed out waiting for %s" % message)


class RunnerMixin(object):
    """Tests written against this mixin run once per runner implementation."""

    runner_kind = "python"

    def runner_command(self, **options):
        if self.runner_kind == "powershell":
            command = [POWERSHELL, "-NoProfile", "-ExecutionPolicy", "Bypass", "-File",
                       RUNNER_PS1, "-Project", self.project]
            for key, value in options.items():
                if key == "model":
                    command += ["-Model", str(value)]
                    continue
                if value is True:
                    command.append("-" + PS_PARAMS[key])
                elif value is not False and value is not None:
                    command += ["-" + PS_PARAMS[key], str(value)]
            return command
        command = [sys.executable, MQ, "--project", self.project, "run"]
        for key, value in options.items():
            flag = "--" + key.replace("_", "-")
            if value is True:
                command.append(flag)
            elif value is not False and value is not None:
                command.append("%s=%s" % (flag, value))
        return command

    def run_queue(self, **options):
        """Run the queue to completion. Returns (exit code, combined output)."""
        result = subprocess.run(self.runner_command(**options), stdout=subprocess.PIPE,
                                stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL, timeout=600)
        return result.returncode, result.stdout.decode("utf-8", errors="replace")

    def start_queue(self, **options):
        """Start a runner in the background; the test is responsible for waiting or killing."""
        kwargs = {}
        if IS_WINDOWS:
            kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
        else:
            kwargs["start_new_session"] = True
        process = subprocess.Popen(self.runner_command(**options), stdout=subprocess.DEVNULL,
                                   stderr=subprocess.DEVNULL, stdin=subprocess.DEVNULL, **kwargs)
        self._background.append(process)
        return process


def runner_variants(mixin_tests):
    """Class decorator helper: build one TestCase per available runner."""
    variants = {"Python": "python"}
    if POWERSHELL and IS_WINDOWS:   # run-queue.ps1 is the Windows runner
        variants["PowerShell"] = "powershell"
    cases = {}
    for label, kind in variants.items():
        name = "%s%sRunner" % (mixin_tests.__name__, label)
        cases[name] = type(name, (mixin_tests, RunnerMixin, QueueTestCase), {"runner_kind": kind})
    return cases
