#!/usr/bin/env python3
"""A stand-in for the `claude` CLI, used by the test suite.

It accepts the arguments the runner passes (`-p --output-format json --model X ...`),
reads the task prompt from stdin and prints a result object shaped like the one
Claude Code prints, including the `modelUsage` report. Lines in the task text
that start with `FAKE:` script its behaviour:

    FAKE: write <path> <text>     create/overwrite a file in the working directory
    FAKE: append <path> <text>    append a line to a file
    FAKE: sleep <seconds>         wait
    FAKE: sleep-once <seconds>    wait on the first attempt only
    FAKE: fail <reason>           report failure through the MQ_RESULT marker
    FAKE: fail-once <reason>      fail on the first attempt only
    FAKE: fail-on <tier> <reason> fail whenever the requested model belongs to that tier
    FAKE: escalate <reason>       fail and ask for a stronger model (MQ_ESCALATE)
    FAKE: escalate-on <tier> <reason>   the same, only on that tier
    FAKE: review-reject-once <reason>   in a review phase: reject the first time
    FAKE: exit <code>             exit with this code and no result object
    FAKE: error                   print a result object with is_error=true
    FAKE: commit <message>        commit the working tree itself
    FAKE: no-marker               succeed without the MQ_RESULT marker
    FAKE: cost <usd>              report this cost instead of the default 0.0123
    FAKE: no-cost                 report no cost at all
    FAKE: no-verify               succeed without the MQ_VERIFIED line
    FAKE: escalate-workflow <why> fail and ask for a deeper workflow (MQ_ESCALATE_WORKFLOW)
    FAKE: concern <text>          before any decision of the owner: report MQ_CONCERN <text>
                                  and stop (FAILED); in the plan phase only list it; once the
                                  prompt carries <owner_decisions>, work normally
    FAKE: concern-always <text>   report MQ_CONCERN <text> on every run (and stop, outside
                                  the plan phase)
    FAKE: budget                  stop as Claude Code does when --max-budget-usd is reached
                                  (as observed with 2.1.329: subtype error_max_budget_usd, no
                                  result text, an errors list, exit code 1, spend above the cap)

Environment switches model behaviour:

    FAKE_UNAVAILABLE_MODELS  comma-separated models the CLI refuses (as the real CLI does)
    FAKE_ACTUAL_MODEL        model id to report whatever was requested (silent substitution)
    FAKE_REVIEW_ANSWER       answer to a routing review request (default: tier=sonnet)
    FAKE_NO_MODEL_FLAG       pretend to be a CLI without a --model option
    FAKE_STATE_DIR           directory for "only once" markers
    FAKE_SCRIPT              JSON file {"<task id>": ["write a.txt x", ...]} with directives
                             for a task, so its prompt can stay a natural request
    FAKE_PLUGINS             JSON file {"plugins": [...], "marketplaces": [...]} that
                             `plugin list --json` and `plugin marketplace list --json` print;
                             `plugin marketplace update` copies "after_refresh" over it

Every start and end is appended to the JSON-lines file named by FAKE_CLAUDE_LOG.
"""
import json
import os
import re
import subprocess
import sys
import time

HELP = """Usage: claude [options] [command] [prompt]

Options:
  -p, --print                 Print response and exit
  --output-format <format>    Output format
%s  --permission-mode <mode>    Permission mode to use for the session
"""
MODEL_HELP = ("  --model <model>             Model for the current session\n"
              "  --fallback-model <model>    Enable automatic fallback\n")


def log(event, **fields):
    path = os.environ.get("FAKE_CLAUDE_LOG")
    if not path:
        return
    fields.update(event=event, time=time.time(), pid=os.getpid())
    with open(path, "a", encoding="utf-8") as handle:
        handle.write(json.dumps(fields) + "\n")


def option(argv, name):
    return argv[argv.index(name) + 1] if name in argv and argv.index(name) + 1 < len(argv) else None


def model_id(requested):
    """The id the CLI would report for a requested model."""
    forced = os.environ.get("FAKE_ACTUAL_MODEL")
    if forced:
        return forced
    if not requested:
        return "claude-default-test-1"
    if requested in ("opus", "sonnet", "haiku"):
        return "claude-%s-test-1" % requested
    return requested


def result_object(text, requested, is_error=False, session="fake-session", cost=0.0123):
    used = model_id(requested)
    data = {"type": "result", "subtype": "error_during_execution" if is_error else "success",
            "is_error": is_error, "result": text, "session_id": session,
            "total_cost_usd": cost, "duration_ms": 42, "num_turns": 1,
            "usage": {"input_tokens": 100, "output_tokens": 20},
            "modelUsage": {used: {"inputTokens": 100, "outputTokens": 20, "costUSD": cost}},
            "permission_denials": []}
    if cost is None:
        del data["total_cost_usd"]
        del data["modelUsage"][used]["costUSD"]
    return data


def plugin_command(argv):
    """`claude plugin ...`: the listings come from the FAKE_PLUGINS file; anything that would
    change an installation is only logged, so a test can assert it never happened."""
    path = os.environ.get("FAKE_PLUGINS")
    state = {}
    if path and os.path.exists(path):
        with open(path, encoding="utf-8") as handle:
            state = json.load(handle)
    log("plugin", argv=argv)
    if argv[:2] == ["plugin", "list"]:
        print(json.dumps(state.get("plugins", [])))
        return 0
    if argv[:3] == ["plugin", "marketplace", "list"]:
        print(json.dumps(state.get("marketplaces", [])))
        return 0
    if argv[:3] == ["plugin", "marketplace", "update"]:
        if state.get("refresh_fails"):
            sys.stderr.write("fake claude: could not reach the marketplace\n")
            return 1
        if state.get("after_refresh") and path:
            with open(path, "w", encoding="utf-8") as handle:
                json.dump(state["after_refresh"], handle)
        print("Updated marketplace")
        return 0
    sys.stderr.write("fake claude: unexpected plugin command %r\n" % (argv,))
    return 2


def once(name):
    """True the first time it is called for this name (needs FAKE_STATE_DIR)."""
    folder = os.environ.get("FAKE_STATE_DIR")
    if not folder:
        return True
    marker = os.path.join(folder, "once-%s" % name)
    if os.path.exists(marker):
        return False
    with open(marker, "w") as handle:
        handle.write("x")
    return True


def agent_worker(key, prompt, argv, requested):
    """A worker of a multi-agent task: directives come from FAKE_AGENT_SCRIPT
    {"<subtask key>": ["sleep 1", "write path text", "result {...}", "fail why",
    "fail-once why", "cost 0.02", "no-cost", "no-result"]}."""
    directives = []
    path = os.environ.get("FAKE_AGENT_SCRIPT")
    if path and os.path.exists(path):
        with open(path, encoding="utf-8") as handle:
            directives = json.load(handle).get(key, [])
    log("agent-start", key=key, argv=argv, cwd=os.getcwd(), model=requested, prompt=prompt)
    revision = re.search(r"Input revision: (\S+)", prompt)
    result = {"summary": "fake worker %s" % key, "evidence": ["fake"], "tests": [],
              "limitations": [], "input_revision": revision.group(1) if revision else None}
    failure, cost, with_result = None, 0.0123, True
    for directive in directives:
        verb, _, rest = directive.partition(" ")
        if verb == "sleep":
            time.sleep(float(rest))
        elif verb == "write":
            target, _, text = rest.partition(" ")
            if os.path.dirname(target):
                os.makedirs(os.path.dirname(target), exist_ok=True)
            with open(target, "w", encoding="utf-8") as handle:
                handle.write(text + "\n")
        elif verb == "result":
            result.update(json.loads(rest))
        elif verb == "fail":
            failure = rest or "scripted failure"
        elif verb == "fail-once":
            marker = os.path.join(os.environ.get("FAKE_STATE_DIR", "."), "agent-once-%s" % key)
            if not os.path.exists(marker):
                open(marker, "w").close()
                failure = rest or "scripted failure (once)"
        elif verb == "cost":
            cost = float(rest)
        elif verb == "no-cost":
            cost = None
        elif verb == "no-result":
            with_result = False
        elif verb == "wrong-revision":
            result["input_revision"] = "0000"
        elif verb == "touch-live":
            # Simulates an agent escaping its snapshot: writes into the live project.
            with open(os.environ["FAKE_AGENT_TOUCH"], "w", encoding="utf-8") as handle:
                handle.write("escaped\n")
    text = "Worker %s done." % key
    if failure:
        text += "\nMQ_RESULT: FAILED - %s" % failure
    else:
        if with_result:
            text += "\nMQ_AGENT_RESULT_BEGIN\n%s\nMQ_AGENT_RESULT_END" % json.dumps(result)
        text += "\nMQ_RESULT: DONE - worker %s" % key
    log("agent-end", key=key, failed=bool(failure))
    sys.stdout.write(json.dumps(result_object(text, requested, cost=cost)))
    return 0


def main():
    argv = sys.argv[1:]
    if argv[:1] == ["--version"]:
        print("9.9.9 (Fake Claude Code)")
        return 0
    if argv[:1] == ["--help"]:
        print(HELP % ("" if os.environ.get("FAKE_NO_MODEL_FLAG") else MODEL_HELP))
        return 0
    if argv[:1] == ["plugin"]:
        return plugin_command(argv)

    requested = option(argv, "--model")
    system_prompt = option(argv, "--system-prompt") or ""
    prompt = sys.stdin.buffer.read().decode("utf-8")
    refused = [name for name in os.environ.get("FAKE_UNAVAILABLE_MODELS", "").split(",") if name]
    if requested and requested in refused:
        # The shape Claude Code 2.1.206 prints for a model that does not exist or is not allowed.
        log("refused", model=requested, argv=argv)
        sys.stdout.write(json.dumps({
            "type": "result", "subtype": "success", "is_error": True, "api_error_status": 404,
            "num_turns": 1, "total_cost_usd": 0, "modelUsage": {}, "session_id": "fake-refused",
            "result": "There's an issue with the selected model (%s). It may not exist or you "
                      "may not have access to it." % requested}))
        return 1

    if "triage software development tasks" in system_prompt:
        answer = os.environ.get("FAKE_REVIEW_ANSWER", "tier=sonnet; reason=fake review")
        log("review", model=requested, argv=argv, prompt=prompt)
        sys.stdout.write(json.dumps(result_object(answer, requested)))
        return 0
    if "single word OK" in system_prompt:
        log("probe", model=requested)
        sys.stdout.write(json.dumps(result_object("OK", requested)))
        return 0

    worker = re.search(r"^MQ_AGENT_SUBTASK: (\S+)", prompt, re.M)
    if worker:
        return agent_worker(worker.group(1), prompt, argv, requested)

    header = re.search(r"This is task #(\d+), attempt (\d+) of (\d+)", prompt)
    task_id = int(header.group(1)) if header else 0
    attempt = int(header.group(2)) if header else 1
    body = re.search(r"<task>\n(.*?)\n</task>", prompt, re.S)
    body = body.group(1) if body else prompt
    phase = ("plan" if "PLANNING phase" in prompt else "implement" if "IMPLEMENTATION phase" in prompt
             else "review" if "REVIEW phase" in prompt else None)
    log("start", task=task_id, attempt=attempt, argv=argv, cwd=os.getcwd(), prompt=prompt,
        model=requested, phase=phase,
        runner_env=os.environ.get("MOA7AMED_QUEUE_RUNNER"),
        project_env=os.environ.get("MOA7AMED_QUEUE_PROJECT"),
        legacy_env={name: os.environ.get(name) for name in ("CTQ_RUNNER", "CTQ_TASK_ID",
                                                            "CTQ_PROJECT")})

    lines = body.splitlines()
    script = os.environ.get("FAKE_SCRIPT")
    if script and os.path.exists(script):
        with open(script, encoding="utf-8") as handle:
            lines += ["FAKE: " + item for item in json.load(handle).get(str(task_id), [])]
    failure, escalate, marker, is_error, cost = None, None, True, False, 0.0123
    verified, deeper = True, None
    concerns = []
    # A session that is going to ask the owner stops before it changes anything.
    stopping = phase != "plan" and any(
        re.match(r"\s*FAKE:\s*concern-always\b", line) or (
            re.match(r"\s*FAKE:\s*concern\b", line) and "<owner_decisions>" not in prompt)
        for line in lines)
    for line in lines:
        match = re.match(r"\s*FAKE:\s*(\S+)\s*(.*)$", line)
        if not match:
            continue
        verb, rest = match.group(1), match.group(2).strip()
        if verb in ("write", "append"):
            if phase == "plan" or stopping:
                continue  # a planning run, or one that stops to ask, does not touch files
            path, _, text = rest.partition(" ")
            folder = os.path.dirname(path)
            if folder:
                os.makedirs(folder, exist_ok=True)
            with open(path, "a" if verb == "append" else "w", encoding="utf-8") as handle:
                handle.write(text + "\n")
        elif verb == "sleep" or (verb == "sleep-once" and attempt == 1):
            time.sleep(float(rest))
        elif verb == "fail" or (verb == "fail-once" and attempt == 1):
            failure = rest or "scripted failure"
        elif verb in ("fail-on", "escalate-on"):
            tier, _, reason = rest.partition(" ")
            if tier in (requested or "") and phase != "plan":
                if verb == "fail-on":
                    failure = reason or "too hard for %s" % tier
                else:
                    escalate = reason or "needs a stronger model"
        elif verb == "escalate":
            escalate = rest or "needs a stronger model"
        elif verb == "review-reject-once":
            if phase == "review" and once("review-%d" % task_id):
                failure = rest or "the review found a defect"
        elif verb == "exit":
            log("end", task=task_id, attempt=attempt, exit=int(rest))
            sys.stderr.write("fake claude: scripted exit %s\n" % rest)
            return int(rest)
        elif verb == "error":
            is_error = True
        elif verb == "commit":
            subprocess.check_call(["git", "add", "-A"])
            subprocess.check_call(["git", "commit", "-q", "-m", rest or "fake commit"])
        elif verb == "no-marker":
            marker = False
        elif verb == "no-verify":
            verified = False
        elif verb == "escalate-workflow":
            deeper = rest or "needs a deeper workflow"
        elif verb == "concern" and "<owner_decisions>" not in prompt:
            concerns.append(rest)
        elif verb == "concern-always":
            concerns.append(rest)
        elif verb == "cost":
            cost = float(rest)
        elif verb == "no-cost":
            cost = None
        elif verb == "budget":
            cap = float(option(argv, "--max-budget-usd") or 0.01)
            log("end", task=task_id, attempt=attempt, exit=1, model=requested, phase=phase,
                budget=cap)
            sys.stdout.write(json.dumps({
                "type": "result", "subtype": "error_max_budget_usd", "is_error": True,
                "num_turns": 3, "total_cost_usd": round(cap * 1.37, 6), "session_id": "fake-budget",
                "stop_reason": "tool_use", "errors": ["Reached maximum budget ($%g)" % cap],
                "usage": {"input_tokens": 100, "output_tokens": 20},
                "modelUsage": {model_id(requested): {"costUSD": round(cap * 1.37, 6)}}}))
            return 1

    if phase == "plan":
        text = "PLAN for task #%d\n1. change the files\n2. verify the criteria" % task_id
    else:
        text = "Fake Claude handled task #%d (attempt %d)." % (task_id, attempt)
    for concern in concerns:
        text += "\nMQ_CONCERN: %s" % concern
    if concerns and phase != "plan":
        failure = failure or "waiting for the owner's decision"
    if deeper:
        text += "\nMQ_ESCALATE_WORKFLOW: %s\nMQ_RESULT: FAILED - %s" % (deeper, deeper)
    elif escalate:
        text += "\nMQ_ESCALATE: %s\nMQ_RESULT: FAILED - beyond this model" % escalate
    elif failure:
        text += "\nMQ_RESULT: FAILED - %s" % failure
    elif not marker and verified and phase != "plan":
        text += "\nMQ_VERIFIED: the fake checked its own output"
    elif marker:
        if verified and phase != "plan":
            text += "\nMQ_VERIFIED: the fake checked its own output"
        text += "\nMQ_RESULT: DONE - %s" % ("plan ready" if phase == "plan"
                                              else "fake result for task %d" % task_id)
    log("end", task=task_id, attempt=attempt, exit=0, model=requested, phase=phase)
    sys.stdout.write(json.dumps(result_object(text, requested, is_error,
                                              "fake-session-%d-%d" % (task_id, attempt), cost)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
