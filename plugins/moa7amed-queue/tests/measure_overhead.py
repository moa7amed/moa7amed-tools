#!/usr/bin/env python3
"""Measure what the plugin costs: time added to a prompt, time to classify a task,
and the size of what every session carries.

    python tests/measure_overhead.py            a table
    python tests/measure_overhead.py --json     machine-readable

Nothing here calls a model, so it is free to run. Token figures are not measured by this
script: Claude Code reports its own estimate with `claude plugin details <plugin>`, and
the cost of real runs is recorded per task (`moa7amed-queue usage`).

The numbers are those of the machine it runs on. They are measurements, not guarantees.
"""
import json
import os
import platform
import re
import shutil
import statistics
import subprocess
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
MQ = os.path.join(ROOT, "scripts", "mq.py")
HOOK = os.path.join(ROOT, "hooks", "capture-prompt.sh")

sys.dont_write_bytecode = True
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(ROOT, "scripts"))


def timed(command, env, stdin=b"", repeat=15, cwd=None):
    """Median and worst wall-clock time of a command, in milliseconds."""
    samples = []
    for _ in range(repeat):
        start = time.perf_counter()
        subprocess.run(command, input=stdin, env=env, cwd=cwd, stdout=subprocess.DEVNULL,
                       stderr=subprocess.DEVNULL)
        samples.append((time.perf_counter() - start) * 1000.0)
    return {"median_ms": round(statistics.median(samples), 1), "max_ms": round(max(samples), 1),
            "runs": repeat}


def payload(prompt, cwd):
    return json.dumps({"session_id": "measure", "cwd": cwd, "hook_event_name": "UserPromptSubmit",
                       "prompt": prompt}).encode("utf-8")


def measure_hook(tmp):
    sh = shutil.which("sh")
    if not sh:
        return {"skipped": "no sh on PATH"}
    env = dict(os.environ)
    for key in list(env):
        if key.startswith(("MOA7AMED_QUEUE_", "CTQ_")):
            del env[key]
    env["MOA7AMED_QUEUE_HOME"] = os.path.join(tmp, "home")
    project = os.path.join(tmp, "project")
    os.makedirs(project, exist_ok=True)
    ordinary = payload("Fix the failing test in the parser module, please.", project)
    captured = payload("queue: add a retry to the uploader", project)
    result = {
        "shell_start_only": timed([sh, "-c", "exit 0"], env),
        "ordinary_prompt": timed([sh, HOOK], env, ordinary),
        "python_start_only": timed([sys.executable, "-c", "pass"], env),
    }
    # The same prompt once settings exist, and once the prefix itself was changed (only
    # then does the hook have to start Python to compare).
    os.makedirs(env["MOA7AMED_QUEUE_HOME"], exist_ok=True)
    settings = os.path.join(env["MOA7AMED_QUEUE_HOME"], "config.json")
    with open(settings, "w") as handle:
        json.dump({"max_attempts": 3, "cost_warn_task_usd": 1.0}, handle)
    result["ordinary_prompt_with_settings"] = timed([sh, HOOK], env, ordinary)
    with open(settings, "w") as handle:
        json.dump({"capture_prefix": "later:"}, handle)
    result["ordinary_prompt_custom_prefix"] = timed([sh, HOOK], env, ordinary, repeat=8)
    os.remove(settings)
    result["captured_prompt"] = timed([sh, HOOK], env, captured, repeat=6)
    return result


def measure_commands(tmp):
    env = dict(os.environ)
    env["MOA7AMED_QUEUE_HOME"] = os.path.join(tmp, "home")
    project = os.path.join(tmp, "project")
    return {
        "version": timed([sys.executable, "-B", MQ, "--version"], env, repeat=8),
        "status": timed([sys.executable, "-B", MQ, "--project", project, "status"], env, repeat=8),
        "add": timed([sys.executable, "-B", MQ, "--project", project, "add", "measure me"], env,
                     repeat=6),
    }


def measure_classifier():
    import routing_corpus as english
    import routing_corpus_multilingual as multilingual
    from mqlib import router
    prompts = {"english": english.HAIKU + english.SONNET + english.OPUS,
               "arabic_and_mixed": [case[0] for case in multilingual.DEVELOPMENT
                                    + multilingual.HELD_OUT + multilingual.FRESH]}
    result = {}
    for name, items in prompts.items():
        best = None
        for _ in range(5):
            start = time.perf_counter()
            for prompt in items:
                router.classify(prompt)
            elapsed = (time.perf_counter() - start) * 1000.0 / len(items)
            best = elapsed if best is None else min(best, elapsed)
        result[name] = {"prompts": len(items), "ms_per_prompt": round(best, 2)}
    start = time.perf_counter()
    subprocess.run([sys.executable, "-B", "-c",
                    "import sys; sys.path.insert(0, %r); from mqlib import router" %
                    os.path.join(ROOT, "scripts")])
    result["import_and_compile_ms"] = round((time.perf_counter() - start) * 1000.0, 1)
    return result


def measure_text():
    skills, always_on, bodies = {}, 0, 0
    folder = os.path.join(ROOT, "skills")
    for name in sorted(os.listdir(folder)):
        with open(os.path.join(folder, name, "SKILL.md"), encoding="utf-8") as handle:
            text = handle.read()
        head = re.match(r"---\n(.*?)\n---\n", text, re.S).group(1)
        description = re.search(r"(?m)^description: (.*)$", head).group(1)
        by_model = "disable-model-invocation: true" not in head
        skills[name] = {"description_chars": len(description), "body_chars": len(text),
                        "claude_may_invoke": by_model}
        always_on += len(description) if by_model else 0
        bodies += len(text)
    return {"skills": skills, "description_chars_claude_may_invoke": always_on,
            "all_skill_files_chars": bodies}


WORKFLOW_SAMPLES = (
    ("quick", "Update the login button text to Sign in", None),
    ("standard", "Implement shipment search with filters on the shipments page", None),
    ("standard (bug)", "Fix a race condition in the billing worker", None),
    ("deep, plan phase", "Redesign the enterprise deployment architecture and move the "
                         "database to a cluster", "plan"),
    ("deep, implement phase", "Redesign the enterprise deployment architecture and move the "
                              "database to a cluster", "implement"),
)


def measure_workflow(tmp):
    """The task prompt with the adaptive workflow (workflow_mode=auto) against the same
    prompt without it (off, the 0.4 behaviour), and the time it adds to queueing a task.
    Characters are counted; tokens are an estimate (characters / 4), not a measurement."""
    from mqlib import engine, store, util
    saved = os.environ.get("MOA7AMED_QUEUE_HOME")
    result = {"prompts": [], "add_ms": {}}
    try:
        for mode in ("off", "auto"):
            home = os.path.join(tmp, "wf-home-" + mode)
            project = os.path.join(tmp, "wf-project-" + mode)
            os.makedirs(home)
            os.makedirs(project)
            with open(os.path.join(home, "config.json"), "w") as handle:
                json.dump({"workflow_mode": mode, "workflow_superpowers": "no",
                           "routing_classifier": "off"}, handle)
            os.environ["MOA7AMED_QUEUE_HOME"] = home
            q = store.Queue(util.resolve_project_root(project))
            samples = []
            for _ in range(5):
                for label, prompt, phase in WORKFLOW_SAMPLES:
                    start = time.perf_counter()
                    task_id = q.add(prompt)
                    samples.append((time.perf_counter() - start) * 1000.0)
            result["add_ms"][mode] = {"median_ms": round(statistics.median(samples), 1),
                                      "max_ms": round(max(samples), 1)}
            for number, (label, prompt, phase) in enumerate(WORKFLOW_SAMPLES, 1):
                text = engine.compose_prompt(q, q.get(number), 1, project, project,
                                             {"phase": phase})
                result["prompts"].append({"mode": mode, "task": label, "chars": len(text)})
            q.close()
    finally:
        if saved is None:
            os.environ.pop("MOA7AMED_QUEUE_HOME", None)
        else:
            os.environ["MOA7AMED_QUEUE_HOME"] = saved
    return result


def main(argv):
    tmp = os.path.realpath(tempfile.mkdtemp(prefix="mq-measure-"))
    try:
        data = {"measured_at": time.strftime("%Y-%m-%d %H:%M"),
                "machine": "%s %s, Python %s" % (platform.system(), platform.release(),
                                                 platform.python_version()),
                "hook": measure_hook(tmp), "commands": measure_commands(tmp),
                "classifier": measure_classifier(), "text": measure_text(),
                "workflow": measure_workflow(tmp)}
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    if "--json" in argv:
        print(json.dumps(data, indent=2))
        return 0
    print("Measured %s on %s\n" % (data["measured_at"], data["machine"]))
    hook = data["hook"]
    if "skipped" in hook:
        print("Capture hook: %s" % hook["skipped"])
    else:
        print("Capture hook (time added to submitting a prompt; median, worst)")
        for label, key in (("starting sh alone, for comparison", "shell_start_only"),
                           ("an ordinary prompt (not captured)", "ordinary_prompt"),
                           ("the same, with other settings saved", "ordinary_prompt_with_settings"),
                           ("the same, with a custom prefix", "ordinary_prompt_custom_prefix"),
                           ("a prompt that is captured and queued", "captured_prompt"),
                           ("starting Python alone, for comparison", "python_start_only")):
            print("  %-40s %7.1f ms  %7.1f ms" % (label, hook[key]["median_ms"],
                                                  hook[key]["max_ms"]))
    print("\nCommands (median, worst)")
    for key, value in data["commands"].items():
        print("  %-40s %7.1f ms  %7.1f ms" % ("moa7amed-queue " + key, value["median_ms"],
                                              value["max_ms"]))
    print("\nRule-based classification (no model call)")
    for key in ("english", "arabic_and_mixed"):
        value = data["classifier"][key]
        print("  %-40s %7.2f ms per prompt (%d prompts)" % (key.replace("_", " "),
                                                           value["ms_per_prompt"],
                                                           value["prompts"]))
    print("  %-40s %7.1f ms once per process" % ("loading the rules",
                                                 data["classifier"]["import_and_compile_ms"]))
    text = data["text"]
    print("\nText")
    print("  %-40s %7d characters" % ("skill descriptions in every session",
                                      text["description_chars_claude_may_invoke"]))
    print("  %-40s %7d characters (read only when a skill runs)"
          % ("all skill files", text["all_skill_files_chars"]))
    flow = data["workflow"]
    print("\nTask prompt, adaptive workflow off -> auto (characters; tokens ~ chars/4, estimated)")
    by_mode = {}
    for item in flow["prompts"]:
        by_mode.setdefault(item["task"], {})[item["mode"]] = item["chars"]
    for label, sizes in by_mode.items():
        print("  %-40s %7d -> %5d  (%+d)" % (label, sizes["off"], sizes["auto"],
                                            sizes["auto"] - sizes["off"]))
    print("  %-40s %7.1f ms -> %.1f ms (median)" % ("queueing a task (in process)",
                                                   flow["add_ms"]["off"]["median_ms"],
                                                   flow["add_ms"]["auto"]["median_ms"]))
    print("\nToken estimate of the installed plugin: claude plugin details moa7amed-queue@<marketplace>")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
