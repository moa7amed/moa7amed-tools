"""Discovery of an existing project: what its files show, with the file as evidence.

Two kinds of fact come out of it, and they are kept apart:

    VERIFIED   what is literally in the repository ("Web.config is present",
               "packages.config references Microsoft.AspNet.Mvc 5.2.9")
    ASSUMED    what that suggests ("the application is hosted on IIS"). A file name or a
               configuration file is evidence about the repository, not about production.

Nothing is read outside the project directory, nothing is executed, and no server is
contacted. The developer's own machine is described separately and is never treated as
the deployment target.
"""
from __future__ import annotations

import json
import os
import platform
import re
import subprocess

from . import gitops

SKIP_DIRS = {".git", "node_modules", "bin", "obj", "dist", "build", "out", "vendor", ".venv",
             "venv", "__pycache__", "packages", ".idea", ".vs", ".vscode", "target", ".next",
             ".nuget", "coverage", ".moa7amed"}
MAX_FILES = 20000
MAX_READ = 512 * 1024

LANGUAGES = {".cs": "C#", ".vb": "VB.NET", ".fs": "F#", ".py": "Python", ".js": "JavaScript",
             ".ts": "TypeScript", ".tsx": "TypeScript", ".jsx": "JavaScript", ".java": "Java",
             ".kt": "Kotlin", ".go": "Go", ".rb": "Ruby", ".php": "PHP", ".rs": "Rust",
             ".cshtml": "Razor", ".aspx": "ASP.NET Web Forms", ".sql": "SQL", ".swift": "Swift",
             ".dart": "Dart", ".cpp": "C++", ".c": "C"}


def _read(path):
    try:
        if os.path.getsize(path) > MAX_READ:
            return ""
        with open(path, "rb") as handle:
            return handle.read().decode("utf-8", errors="replace")
    except OSError:
        return ""


def _walk(root):
    count = 0
    for base, dirs, files in os.walk(root):
        dirs[:] = sorted(name for name in dirs if name not in SKIP_DIRS
                         and (not name.startswith(".") or name in (".github", ".gitlab")))
        for name in sorted(files):
            count += 1
            if count > MAX_FILES:
                return
            yield os.path.relpath(os.path.join(base, name), root).replace("\\", "/")


class _Facts(object):
    def __init__(self):
        self.items, self.components = [], []

    def add(self, topic, statement, status, evidence, component=None):
        key = (topic, statement)
        if any((item["topic"], item["statement"]) == key for item in self.items):
            return
        self.items.append({"topic": topic, "statement": statement, "status": status,
                           "evidence": evidence if isinstance(evidence, list) else [evidence],
                           "source": "discovery"})
        if component and component not in self.components:
            self.components.append(component)


def discover(root):
    """{"facts": [...], "existing_components": [...], "summary": {...}} for a project."""
    facts = _Facts()
    files = list(_walk(root))
    counts = {}
    for path in files:
        language = LANGUAGES.get(os.path.splitext(path)[1].lower())
        if language:
            counts[language] = counts.get(language, 0) + 1
    for language, number in sorted(counts.items(), key=lambda item: -item[1])[:6]:
        facts.add("code.language", "%d %s source file(s)" % (number, language), "VERIFIED",
                  "file count by extension")

    def full(path):
        return os.path.join(root, path)

    for path in files:
        name = os.path.basename(path).lower()
        text = None
        if name.endswith((".csproj", ".vbproj", ".fsproj")):
            text = _read(full(path))
            for match in re.finditer(r"<TargetFramework(?:Version|s)?>([^<]+)<", text):
                value = match.group(1)
                facts.add("framework.target", "%s targets %s" % (path, value), "VERIFIED", path)
                if re.match(r"(v4|net4)", value, re.I):
                    facts.add("framework.runtime", "the project builds for the .NET Framework "
                              "(Windows only)", "VERIFIED", path, "dotnet-framework")
                elif re.match(r"net\d+\.\d+|netcoreapp", value, re.I):
                    facts.add("framework.runtime", "the project builds for modern .NET",
                              "VERIFIED", path, "dotnet")
            for package, component, label in (
                    ("Microsoft.AspNetCore", "aspnet-core", "ASP.NET Core"),
                    ("Microsoft.EntityFrameworkCore.SqlServer", "sqlserver", "SQL Server via EF Core"),
                    ("Npgsql", "postgres", "PostgreSQL (Npgsql)"),
                    ("StackExchange.Redis", "redis", "Redis client"),
                    ("Microsoft.Data.Sqlite", "sqlite", "SQLite")):
                if package.lower() in text.lower():
                    facts.add("dependency", "%s references %s" % (path, label), "VERIFIED", path,
                              component)
            if 'Sdk="Microsoft.NET.Sdk.Web"' in text:
                facts.add("framework.web", "%s is an ASP.NET Core web project" % path, "VERIFIED",
                          path, "aspnet-core")
        elif name == "packages.config":
            text = _read(full(path))
            mvc = re.search(r'id="Microsoft\.AspNet\.Mvc"\s+version="([^"]+)"', text)
            if mvc:
                facts.add("framework.web", "ASP.NET MVC %s (packages.config)" % mvc.group(1),
                          "VERIFIED", path, "aspnet-mvc5")
            if re.search(r'id="EntityFramework"', text):
                facts.add("dependency", "Entity Framework 6 (packages.config)", "VERIFIED", path)
        elif name == "web.config":
            text = _read(full(path))
            facts.add("hosting.config", "%s is present" % path, "VERIFIED", path)
            facts.add("hosting.server", "the application is probably hosted on IIS (it has a "
                      "Web.config)", "ASSUMED", path)
            if re.search(r"providerName=\"System\.Data\.SqlClient\"|Initial Catalog=|"
                         r"Data Source=[^;\"]+;", text, re.I):
                facts.add("database.engine", "a SQL Server connection string is configured in %s"
                          % path, "VERIFIED", path, "sqlserver")
        elif name == "package.json":
            try:
                data = json.loads(_read(full(path)) or "{}")
            except ValueError:
                data = {}
            deps = dict(data.get("dependencies") or {})
            deps.update(data.get("devDependencies") or {})
            for package, component, label in (
                    ("react", None, "React"), ("vue", None, "Vue"), ("@angular/core", None, "Angular"),
                    ("next", None, "Next.js"), ("express", "node", "Express"),
                    ("@nestjs/core", "node", "NestJS"), ("pg", "postgres", "PostgreSQL client"),
                    ("mysql2", "mysql", "MySQL client"), ("mongoose", "mongodb", "Mongoose"),
                    ("redis", "redis", "Redis client"), ("ioredis", "redis", "Redis client"),
                    ("openai", "cloud-llm-api", "OpenAI SDK"),
                    ("@anthropic-ai/sdk", "cloud-llm-api", "Anthropic SDK")):
                if package in deps:
                    facts.add("dependency", "%s depends on %s %s" % (path, label, deps[package]),
                              "VERIFIED", path, component)
        elif name in ("requirements.txt", "pyproject.toml", "pipfile"):
            text = _read(full(path)).lower()
            facts.add("framework.runtime", "Python dependencies are declared in %s" % path,
                      "VERIFIED", path, "python")
            for package, component, label in (("django", None, "Django"), ("flask", None, "Flask"),
                                              ("fastapi", None, "FastAPI"),
                                              ("psycopg", "postgres", "PostgreSQL driver"),
                                              ("redis", "redis", "Redis client"),
                                              ("openai", "cloud-llm-api", "OpenAI SDK"),
                                              ("anthropic", "cloud-llm-api", "Anthropic SDK"),
                                              ("torch", None, "PyTorch"),
                                              ("transformers", None, "Hugging Face Transformers")):
                if re.search(r"(^|[\s\"'])%s\b" % re.escape(package), text, re.M):
                    facts.add("dependency", "%s lists %s" % (path, label), "VERIFIED", path,
                              component)
        elif name in ("pom.xml", "build.gradle", "build.gradle.kts"):
            facts.add("framework.runtime", "JVM build file %s" % path, "VERIFIED", path, "java")
        elif name == "go.mod":
            facts.add("framework.runtime", "Go module %s" % path, "VERIFIED", path)
        elif name == "composer.json":
            facts.add("framework.runtime", "PHP dependencies in %s" % path, "VERIFIED", path, "php")
        elif name == "dockerfile" or re.match(r"(docker-)?compose[^/]*\.ya?ml$", name):
            facts.add("deployment.container", "%s is present" % path, "VERIFIED", path)
            facts.add("deployment.model", "containers are probably used somewhere (development "
                      "or production: not shown by the file)", "ASSUMED", path)
        elif name.endswith((".yml", ".yaml")) and re.search(r"(^|/)(k8s|kubernetes|helm|charts?)/",
                                                            path, re.I):
            text = _read(full(path))
            if re.search(r"^kind:\s*(Deployment|StatefulSet|Service|Ingress)", text, re.M):
                facts.add("deployment.kubernetes", "Kubernetes manifest %s" % path, "VERIFIED", path)
        elif path.startswith(".github/workflows/") or name in ("azure-pipelines.yml",
                                                               ".gitlab-ci.yml", "jenkinsfile"):
            facts.add("delivery.ci", "CI configuration %s" % path, "VERIFIED", path)
        elif name in ("readme.md", "readme.txt", "readme"):
            facts.add("docs", "%s is present" % path, "VERIFIED", path)
    tests = [path for path in files if re.search(r"(^|/)(tests?|spec|__tests__)/|[._-]tests?\.",
                                                 path, re.I)]
    if tests:
        facts.add("tests", "%d test file(s), for example %s" % (len(tests), tests[0]), "VERIFIED",
                  tests[0])
    else:
        facts.add("tests", "no test files were found by name", "VERIFIED", "file names")
    if gitops.is_repo(root):
        branch = gitops.branch(root)
        try:
            dirty = len(gitops.status_lines(root))
        except Exception:  # noqa: BLE001 - discovery must not fail on git
            dirty = None
        facts.add("git", "git repository on branch %s, %s uncommitted change(s)" % (
            branch or "?", "?" if dirty is None else dirty), "VERIFIED", "git status")
    else:
        facts.add("git", "the project is not a git repository", "VERIFIED", "git")
    numbered = []
    for position, item in enumerate(facts.items, 1):
        item["id"] = "D%d" % position
        numbered.append(item)
    return {"facts": numbered, "existing_components": facts.components,
            "summary": {"files_scanned": len(files), "languages": counts}}


MANIFESTS = re.compile(r"(^|/)(package\.json|packages\.config|web\.config|requirements[^/]*\.txt|"
                       r"pyproject\.toml|pipfile|go\.mod|pom\.xml|build\.gradle(\.kts)?|"
                       r"composer\.json|gemfile|dockerfile|(docker-)?compose[^/]*\.ya?ml|"
                       r"[^/]+\.(cs|vb|fs)proj|appsettings[^/]*\.json)$", re.I)


def fingerprint(root):
    """{path: sha256} of the files that define the stack (manifests, project files,
    container and server configuration). An approved baseline records it; a difference
    later means the baseline may no longer describe the project."""
    import hashlib
    found = {}
    for path in _walk(root):
        if MANIFESTS.search(path):
            try:
                with open(os.path.join(root, path), "rb") as handle:
                    found[path] = hashlib.sha256(handle.read()).hexdigest()[:16]
            except OSError:
                continue
    return found


def developer_machine():
    """The machine this runs on. Useful for development estimates only: it says nothing
    about the server the application will run on."""
    info = {"os": platform.system(), "release": platform.release(), "machine": platform.machine(),
            "cpu_count": os.cpu_count(), "note": "the developer's machine: not the deployment "
                                                 "target"}
    try:
        if os.name == "nt":
            import ctypes

            class Status(ctypes.Structure):
                _fields_ = [("length", ctypes.c_ulong), ("load", ctypes.c_ulong),
                            ("total", ctypes.c_ulonglong), ("avail", ctypes.c_ulonglong),
                            ("pagetotal", ctypes.c_ulonglong), ("pageavail", ctypes.c_ulonglong),
                            ("virttotal", ctypes.c_ulonglong), ("virtavail", ctypes.c_ulonglong),
                            ("extended", ctypes.c_ulonglong)]
            status = Status()
            status.length = ctypes.sizeof(Status)
            ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status))
            info["ram_gb"] = round(status.total / 1024.0 ** 3, 1)
        elif hasattr(os, "sysconf") and "SC_PHYS_PAGES" in os.sysconf_names:
            info["ram_gb"] = round(os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES")
                                   / 1024.0 ** 3, 1)
    except Exception:  # noqa: BLE001 - optional detail
        pass
    try:
        result = subprocess.run(["nvidia-smi", "--query-gpu=name,memory.total",
                                 "--format=csv,noheader"], stdout=subprocess.PIPE,
                                stderr=subprocess.DEVNULL, stdin=subprocess.DEVNULL, timeout=10)
        if result.returncode == 0 and result.stdout.strip():
            info["gpu"] = result.stdout.decode("utf-8", errors="replace").strip().splitlines()
    except (OSError, subprocess.SubprocessError):
        info["gpu"] = []
    return info
