#!/usr/bin/env python3
"""The worked example of docs/planning.md, produced through the real command line.

    python tests/planning_example.py [--keep]

Scenario: a .NET correspondence register for about 200 staff that keeps its documents in
an existing document management system; the server is known to run Windows with IIS, its
memory is not confirmed. Everything happens in a temporary folder with its own data
directory. Nothing here calls a model, so it costs nothing."""
import json
import os
import shutil
import subprocess
import sys
import tempfile

MQ = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts", "mq.py")
WORK = os.path.realpath(tempfile.mkdtemp(prefix="mq-planning-example-"))
project = os.path.join(WORK, "correspondence")
os.makedirs(project)
env = dict(os.environ, MOA7AMED_QUEUE_HOME=os.path.join(WORK, "home"), PYTHONIOENCODING="utf-8")
subprocess.run(["git", "init", "-q", "-b", "main"], cwd=project)
outputs = []


def mq(*args, stdin=None):
    result = subprocess.run([sys.executable, "-B", MQ, "--project", project] + list(args),
                            input=stdin.encode("utf-8") if stdin else None, env=env,
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    text = result.stdout.decode("utf-8", errors="replace").replace(WORK, "<work>")
    outputs.append(("moa7amed-queue " + " ".join(args), text))
    return text


mq("project", "new", "corr", "--title", "Correspondence register", "--mode", "new",
   "--summary", "Register incoming and outgoing correspondence for about 200 staff; documents "
                "stay in the existing document management system (DMS).")
for scenario, field, value, status, source in (
        ("current", "hosting", "vm", "USER_CONFIRMED", "IT department"),
        ("current", "os", "windows", "USER_CONFIRMED", "IT department"),
        ("current", "web_server", "iis", "USER_CONFIRMED", "IT department"),
        ("current", "can_install_services", "true", "USER_CONFIRMED", "IT department"),
        ("current", "ram_gb", "8", "ASSUMED", "typical for this VM class; not confirmed"),
        ("current", "gpu_present", "false", "USER_CONFIRMED", "IT department")):
    mq("project", "env", "corr", scenario, field, value, "--status", status, "--source", source,
       "--label", "existing Windows VM")
mq("project", "target", "corr", "current")
mq("project", "apply", "corr", stdin=json.dumps({
    "requirements": [
        {"id": "R1", "kind": "functional", "text": "Register incoming correspondence with sender, "
         "date, subject and a link to the scanned document in the DMS", "status": "USER_CONFIRMED",
         "evidence": ["meeting 2026-11-18"]},
        {"id": "R2", "kind": "integration", "text": "Documents are stored in and opened from the "
         "existing DMS; the application keeps only references", "status": "USER_CONFIRMED",
         "evidence": ["meeting 2026-11-18"]},
        {"id": "R3", "kind": "performance", "text": "About 200 staff use it during office hours",
         "status": "USER_CONFIRMED", "evidence": ["meeting 2026-11-18"]},
        {"id": "R4", "kind": "security", "text": "Sign-in with the organisation's Active "
         "Directory accounts", "status": "ASSUMED"}],
    "facts": [{"id": "F1", "topic": "dms.api", "statement": "The DMS offers an API the "
               "application can call", "status": "UNKNOWN"}],
    "capacity": {"inputs": {
        "concurrent_users": {"value": 60, "status": "ASSUMED", "source": "30% of 200 staff"},
        "requests_per_user_per_minute": {"value": 4, "status": "ASSUMED"},
        "avg_request_ms": {"value": 300, "status": "ASSUMED"},
        "peak_factor": {"value": 2, "status": "ASSUMED"},
        "uploads_per_day": {"value": 400, "status": "USER_CONFIRMED"},
        "avg_upload_mb": {"value": 1.5, "status": "USER_CONFIRMED"},
        "documents_stored_externally": {"value": 1, "status": "USER_CONFIRMED"},
        "db_rows_per_day": {"value": 2000, "status": "ASSUMED"},
        "avg_row_kb": {"value": 4, "status": "ASSUMED"}}},
    "decisions": [{
        "id": "ADR-1", "title": "Application architecture and stack", "category": "architecture",
        "context": "A line-of-business web application on an existing Windows VM with IIS.",
        "requirements": ["R1", "R2", "R3"], "constraints": ["Windows Server with IIS (USER_CONFIRMED)"],
        "criteria": [{"name": "fits the server", "weight": 3}, {"name": "operational effort", "weight": 2},
                     {"name": "team experience", "weight": 2}],
        "options": [
            {"id": "monolith", "name": "Modular monolith, ASP.NET Core on IIS, SQL Server",
             "style": "modular-monolith", "components": ["dotnet", "aspnet-core", "sqlserver"],
             "resources": {"ram_gb": 4},
             "scores": {"fits the server": {"score": 5, "basis": "USER_CONFIRMED"},
                        "operational effort": {"score": 4, "basis": "ASSUMED"},
                        "team experience": {"score": 4, "basis": "ASSUMED"}}},
            {"id": "services", "name": "Microservices in containers with a message broker",
             "style": "microservices",
             "components": ["dotnet", "aspnet-core", "sqlserver", {"id": "docker", "why": ["R3"]},
                            {"id": "rabbitmq", "why": ["R3"]}],
             "resources": {"ram_gb": 10},
             "scores": {"fits the server": {"score": 2, "basis": "ASSUMED"},
                        "operational effort": {"score": 1, "basis": "ASSUMED"},
                        "team experience": {"score": 2, "basis": "ASSUMED"}}}],
        "selected": "monolith",
        "rejected": [{"option": "services", "reason": "200 users do not need independent scaling; "
                      "containers and a broker add operations work the team does not have"}],
        "rationale": "The simplest option that fits the confirmed server and meets R1 to R3.",
        "implications": {"cost": "no additional licences beyond the existing SQL Server"}},
        {"id": "ADR-2", "title": "Integration with the DMS", "category": "architecture",
         "requirements": ["R2"], "facts": ["F1"],
         "options": [{"id": "api", "name": "Call the DMS API", "components": ["external-dms"]},
                     {"id": "share", "name": "Shared folder export", "components": ["external-dms"]}],
         "selected": "api", "rationale": "Keeps documents in the DMS (R2)."}],
    "milestones": [{"id": "M1", "title": "Register correspondence"}],
    "tasks": [
        {"id": "T1", "title": "Create the Correspondence table and its EF Core migration",
         "milestone": "M1", "requirements": ["R1"], "decisions": ["ADR-1"],
         "components": ["sqlserver"], "acceptance": ["The migration applies to an empty database"],
         "effort": {"estimate": 3, "unit": "hours", "uncertainty": "low"}},
        {"id": "T2", "title": "Registration form and list page", "depends_on": ["T1"],
         "milestone": "M1", "requirements": ["R1"], "decisions": ["ADR-1"],
         "acceptance": ["A user can register an item and find it in the list"],
         "effort": {"estimate": 1, "unit": "day", "uncertainty": "medium"}},
        {"id": "T3", "title": "Link a registered item to its DMS document", "depends_on": ["T2"],
         "milestone": "M1", "requirements": ["R2"], "decisions": ["ADR-2"],
         "acceptance": ["The item opens its document from the DMS"]}]}))
mq("project", "check", "corr")
mq("project", "approve", "corr")
mq("project", "approve", "corr", "--yes")
mq("project", "execute", "corr", "--yes")
mq("project", "status", "corr")
mq("project", "report", "corr", "--out", os.path.join(WORK, "report"))
for command, text in outputs:
    sys.stdout.write("$ %s\n%s\n" % (command, text.replace("\r\n", "\n")))
if "--keep" in sys.argv:
    print("kept: %s (the report is in report/)" % WORK)
else:
    shutil.rmtree(WORK, ignore_errors=True)
