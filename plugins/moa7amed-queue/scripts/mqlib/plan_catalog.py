"""What the planning engine knows about technologies, and how it checks them against an
infrastructure.

This is deliberately a small, conservative catalogue. Every rule says what it rests on:

    documented  a limit or requirement the vendor documents (for example: the .NET Framework
                runs only on Windows)
    stated      a fact about this project's infrastructure, as recorded in the plan with its
                evidence status
    heuristic   a common situation that is not guaranteed (for example: shared hosting plans
                rarely allow installing services). A heuristic never makes an option
                incompatible on its own; it turns the verdict into "needs information".

Unknown technologies are not guessed at: a component the catalogue does not know is
reported as "not assessed" and has to be justified and checked by a person.
"""
from __future__ import annotations

# Infrastructure fields the rules read. Each is stored as {"value", "status", "source"}.
ENV_FIELDS = {
    "hosting": "shared | vm | vps | dedicated | onprem | private_cloud | public_cloud | hybrid | "
               "container_platform | kubernetes | managed",
    "os": "windows | linux",
    "web_server": "iis | nginx | apache | other",
    "cpu_vcpus": "number of vCPUs or cores",
    "cpu_arch": "x86_64 | arm64",
    "cpu_dedicated": "true | false (shared vCPUs)",
    "ram_gb": "memory available to the application, GB",
    "disk_gb": "disk space available, GB",
    "disk_type": "ssd | hdd | network",
    "gpu_present": "true | false",
    "gpu_vram_gb": "GPU memory, GB",
    "gpu_runtime": "cuda | rocm | directml | none",
    "outbound_internet": "true | false (can the server reach external APIs)",
    "can_install_services": "true | false (can you install databases, caches, brokers)",
    "container_runtime": "true | false (can containers run there)",
    "admin_access": "true | false",
    "runtimes": "comma-separated runtimes the host supports (dotnet-framework, dotnet, php, node, python, java)",
    "budget_monthly_usd": "infrastructure budget per month",
}

SERVICE_HOSTINGS = ("vm", "vps", "dedicated", "onprem", "private_cloud", "public_cloud", "hybrid",
                    "container_platform", "kubernetes")

# category, description, runtime needs and the rules that apply.
#   os:           operating systems it runs on (documented)
#   service:      it is a separate service that has to be installed and run
#   needs:        capability flags it requires
#   optional:     it is an additional moving part that must be justified by a requirement
#   complexity:   1 (simplest) to 5, used only to prefer the simpler of two equal options
COMPONENTS = {
    # --- application runtimes and frameworks
    "dotnet-framework": {"category": "runtime", "name": ".NET Framework 4.x",
                         "os": ["windows"], "basis": "documented: the .NET Framework runs only on Windows",
                         "runtime": "dotnet-framework", "complexity": 1},
    "aspnet-mvc5": {"category": "framework", "name": "ASP.NET MVC 5 (.NET Framework)",
                    "os": ["windows"], "requires": ["dotnet-framework"], "web_server": ["iis"],
                    "basis": "documented: ASP.NET MVC 5 runs on the .NET Framework under IIS",
                    "runtime": "dotnet-framework", "complexity": 1},
    "aspnet-webforms": {"category": "framework", "name": "ASP.NET Web Forms", "os": ["windows"],
                        "requires": ["dotnet-framework"], "web_server": ["iis"],
                        "basis": "documented: Web Forms runs only on the .NET Framework",
                        "runtime": "dotnet-framework", "complexity": 1},
    "dotnet": {"category": "runtime", "name": ".NET (6 and later)", "os": ["windows", "linux"],
               "basis": "documented: modern .NET is cross-platform", "runtime": "dotnet",
               "complexity": 1},
    "aspnet-core": {"category": "framework", "name": "ASP.NET Core", "os": ["windows", "linux"],
                    "requires": ["dotnet"], "runtime": "dotnet",
                    "basis": "documented: runs on Windows (IIS needs the ASP.NET Core Hosting "
                             "Bundle) and Linux", "complexity": 1},
    "node": {"category": "runtime", "name": "Node.js", "os": ["windows", "linux"], "runtime": "node",
             "complexity": 1},
    "python": {"category": "runtime", "name": "Python", "os": ["windows", "linux"],
               "runtime": "python", "complexity": 1},
    "java": {"category": "runtime", "name": "Java / JVM", "os": ["windows", "linux"],
             "runtime": "java", "complexity": 2},
    "php": {"category": "runtime", "name": "PHP", "os": ["windows", "linux"], "runtime": "php",
            "complexity": 1},
    # --- databases
    "sqlserver": {"category": "database", "name": "SQL Server", "os": ["windows", "linux"],
                  "service": True, "complexity": 2,
                  "basis": "documented: SQL Server runs on Windows and Linux"},
    "sqlserver-express": {"category": "database", "name": "SQL Server Express",
                          "os": ["windows", "linux"], "service": True, "complexity": 1,
                          "max_db_gb": 10,
                          "basis": "documented: SQL Server Express limits a database to 10 GB"},
    "postgres": {"category": "database", "name": "PostgreSQL", "os": ["windows", "linux"],
                 "service": True, "complexity": 2},
    "mysql": {"category": "database", "name": "MySQL / MariaDB", "os": ["windows", "linux"],
              "service": True, "complexity": 2},
    "sqlite": {"category": "database", "name": "SQLite (embedded)", "os": ["windows", "linux"],
               "complexity": 1, "single_writer": True,
               "basis": "documented: SQLite allows one writer at a time"},
    "mongodb": {"category": "database", "name": "MongoDB", "os": ["windows", "linux"],
                "service": True, "complexity": 3},
    # --- optional infrastructure (each must be justified)
    "redis": {"category": "cache", "name": "Redis", "os": ["linux"], "service": True,
              "optional": True, "complexity": 2,
              "basis": "documented: Redis does not officially support Windows"},
    "rabbitmq": {"category": "broker", "name": "RabbitMQ", "os": ["windows", "linux"],
                 "service": True, "optional": True, "complexity": 3},
    "kafka": {"category": "broker", "name": "Apache Kafka", "os": ["linux", "windows"],
              "service": True, "optional": True, "complexity": 5},
    "elasticsearch": {"category": "search", "name": "Elasticsearch / OpenSearch",
                      "os": ["linux", "windows"], "service": True, "optional": True,
                      "complexity": 4},
    "docker": {"category": "container", "name": "Docker containers", "os": ["linux", "windows"],
               "needs": ["container_runtime"], "optional": True, "complexity": 3},
    "kubernetes": {"category": "orchestrator", "name": "Kubernetes", "os": ["linux"],
                   "needs": ["container_runtime"], "hosting": ["kubernetes", "container_platform"],
                   "optional": True, "complexity": 5},
    "vector-db": {"category": "vector", "name": "Dedicated vector database",
                  "os": ["linux", "windows"], "service": True, "optional": True, "complexity": 3},
    "pgvector": {"category": "vector", "name": "pgvector (PostgreSQL extension)",
                 "requires": ["postgres"], "optional": True, "complexity": 2},
    # --- AI
    "cloud-llm-api": {"category": "ai", "name": "Hosted model API (cloud)",
                      "needs": ["outbound_internet"], "external_data": True, "complexity": 1},
    "local-llm-gpu": {"category": "ai", "name": "Local model inference on GPU",
                      "gpu": True, "optional": True, "complexity": 4},
    "local-llm-cpu": {"category": "ai", "name": "Local model inference on CPU",
                      "optional": True, "complexity": 3,
                      "note": "heuristic: CPU inference of large models is slow; measure latency "
                              "with the intended model before committing"},
    "embeddings-api": {"category": "ai", "name": "Hosted embeddings API",
                       "needs": ["outbound_internet"], "external_data": True, "complexity": 1},
    "external-dms": {"category": "integration", "name": "External document management system",
                     "complexity": 1, "stores_documents": True},
}

# Architecture styles, simplest first. Only used to prefer the simpler of equal options.
STYLES = {"monolith": 1, "layered": 1, "modular-monolith": 2, "clean-architecture": 2,
          "serverless": 3, "event-driven": 4, "microservices": 5}


def env_value(env, field):
    """(value, status) of an infrastructure field, (None, "UNKNOWN") when it is not recorded."""
    item = (env or {}).get(field)
    if isinstance(item, dict):
        return item.get("value"), item.get("status") or "UNKNOWN"
    if item is None:
        return None, "UNKNOWN"
    return item, "ASSUMED"


def _truthy(value):
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in ("true", "yes", "1", "y")


def _number(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def component_id(item):
    return item.get("id") if isinstance(item, dict) else str(item)


def check_component(item, env, requirements=()):
    """Check one component against an infrastructure. Returns a list of findings:
    {"verdict": "ok" | "incompatible" | "needs_information" | "not_assessed" | "warning",
     "component", "reason", "basis"}."""
    cid = component_id(item)
    params = item if isinstance(item, dict) else {}
    spec = COMPONENTS.get(cid)
    if spec is None:
        return [{"verdict": "not_assessed", "component": cid, "basis": "none",
                 "reason": "not in the catalogue: check its requirements by hand"}]
    findings = []

    def add(verdict, reason, basis):
        findings.append({"verdict": verdict, "component": cid, "reason": reason, "basis": basis})

    weak = ("ASSUMED", "UNKNOWN", "CONFLICTING")
    os_value, os_status = env_value(env, "os")
    if spec.get("os") and os_value:
        if str(os_value).lower() not in spec["os"]:
            add("incompatible" if os_status not in weak else "needs_information",
                "%s runs on %s; the target is %s (%s)" % (spec["name"], " or ".join(spec["os"]),
                                                          os_value, os_status.lower()),
                spec.get("basis", "documented"))
    elif spec.get("os") and len(spec["os"]) == 1:
        add("needs_information", "%s needs %s; the target operating system is not known"
            % (spec["name"], spec["os"][0]), spec.get("basis", "documented"))

    server, server_status = env_value(env, "web_server")
    if spec.get("web_server") and server and str(server).lower() not in spec["web_server"]:
        add("incompatible" if server_status not in weak else "needs_information",
            "%s is hosted by %s; the target web server is %s" % (
                spec["name"], " or ".join(spec["web_server"]).upper(), server),
            spec.get("basis", "documented"))

    hosting, hosting_status = env_value(env, "hosting")
    hosting = str(hosting).lower() if hosting else None
    for need in spec.get("needs", []):
        value, status = env_value(env, need)
        if value is not None and not _truthy(value):
            add("incompatible" if status not in weak else "needs_information",
                "%s needs %s; the infrastructure says it is not available (%s)"
                % (spec["name"], need.replace("_", " "), status.lower()), "stated")
        elif value is None:
            if need in ("container_runtime",) and hosting == "shared":
                add("needs_information", "%s needs a container runtime; shared hosting plans "
                    "rarely provide one. Confirm with the provider" % spec["name"], "heuristic")
            else:
                add("needs_information", "%s needs %s, which is not recorded for the target"
                    % (spec["name"], need.replace("_", " ")), "stated")
    if spec.get("hosting") and hosting and hosting not in spec["hosting"]:
        add("incompatible" if hosting_status not in weak else "needs_information",
            "%s needs a %s; the target is %s hosting" % (
                spec["name"], " or ".join(spec["hosting"]).replace("_", " "), hosting), "stated")
    elif spec.get("hosting") and not hosting:
        add("needs_information", "%s needs a %s; the hosting is not known"
            % (spec["name"], " or ".join(spec["hosting"])), "stated")

    if spec.get("service"):
        value, status = env_value(env, "can_install_services")
        if value is not None and not _truthy(value) and not params.get("managed"):
            add("incompatible" if status not in weak else "needs_information",
                "%s is a separate service; the infrastructure does not allow installing services "
                "(%s). A managed offering would have to be recorded as such" % (spec["name"],
                                                                              status.lower()),
                "stated")
        elif value is None and hosting == "shared" and not params.get("managed"):
            add("needs_information", "%s is a separate service; shared hosting plans rarely allow "
                "installing one. Confirm with the provider, or use a managed service"
                % spec["name"], "heuristic")

    runtime = spec.get("runtime")
    runtimes, runtimes_status = env_value(env, "runtimes")
    if runtime and runtimes:
        offered = [item.strip().lower() for item in str(runtimes).split(",") if item.strip()]
        if runtime not in offered:
            add("incompatible" if runtimes_status not in weak else "needs_information",
                "the host lists the runtimes %s; %s needs %s" % (", ".join(offered), spec["name"],
                                                                runtime), "stated")
    elif runtime and hosting == "shared":
        add("needs_information", "shared hosting supports a fixed set of runtimes; confirm that "
            "%s is one of them" % spec["name"], "heuristic")

    if spec.get("gpu"):
        present, present_status = env_value(env, "gpu_present")
        vram, vram_status = env_value(env, "gpu_vram_gb")
        needed = _number(params.get("min_vram_gb"))
        if present is not None and not _truthy(present):
            add("incompatible" if present_status not in weak else "needs_information",
                "local GPU inference needs a GPU; the target has none (%s)" % present_status.lower(),
                "stated")
        elif present is None:
            add("needs_information", "local GPU inference needs a GPU; whether the target has "
                "one is not recorded", "stated")
        elif needed is None:
            add("needs_information", "the GPU memory the model needs (min_vram_gb) is not "
                "stated for this option", "stated")
        elif _number(vram) is None:
            add("needs_information", "the model needs %g GB of GPU memory; the target's GPU "
                "memory is not recorded" % needed, "stated")
        elif _number(vram) < needed:
            add("incompatible" if vram_status not in weak else "needs_information",
                "the model needs %g GB of GPU memory; the target GPU has %g GB" % (needed, _number(vram)),
                "stated")
    if spec.get("note"):
        add("warning", spec["note"], "heuristic")

    if spec.get("external_data"):
        for requirement in requirements:
            if "data-residency" in (requirement.get("tags") or []):
                add("incompatible" if requirement.get("status") in ("VERIFIED", "USER_CONFIRMED")
                    else "needs_information",
                    "%s sends data to an external provider; requirement %s restricts where data "
                    "may go" % (spec["name"], requirement.get("id")), "stated")

    max_db = spec.get("max_db_gb")
    if max_db:
        expected = _number(params.get("expected_db_gb"))
        if expected is not None and expected > max_db:
            add("incompatible", "%s allows %g GB per database; %g GB is expected"
                % (spec["name"], max_db, expected), spec.get("basis", "documented"))
        elif expected is None:
            add("warning", "%s allows %g GB per database; check expected growth"
                % (spec["name"], max_db), spec.get("basis", "documented"))
    if not findings:
        add("ok", "no conflict with the recorded infrastructure", "stated")
    return findings


def check_option(option, env, requirements=()):
    """Check an architecture option against one infrastructure. Returns
    {"verdict", "findings", "resources"} where verdict is the worst of its components:
    incompatible > needs_information > not_assessed > ok."""
    findings = []
    ids = [component_id(item) for item in option.get("components") or []]
    for item in option.get("components") or []:
        findings += check_component(item, env, requirements)
        for required in (COMPONENTS.get(component_id(item)) or {}).get("requires", []):
            if required not in ids:
                findings.append({"verdict": "needs_information", "component": component_id(item),
                                 "basis": "documented",
                                 "reason": "%s needs %s, which the option does not include"
                                           % (component_id(item), required)})
    # Resources the option is estimated to need, against what the target has.
    resources = option.get("resources") or {}
    for field, label in (("ram_gb", "memory"), ("disk_gb", "disk space"), ("cpu_vcpus", "vCPUs")):
        needed = _number(resources.get(field))
        if needed is None:
            continue
        have, status = env_value(env, field)
        have = _number(have)
        if have is None:
            findings.append({"verdict": "needs_information", "component": "resources",
                             "basis": "stated", "reason": "the option is estimated to need "
                             "%g %s; the target's %s is not recorded" % (needed, label, label)})
        elif needed > have:
            findings.append({"verdict": "incompatible" if status not in ("ASSUMED", "UNKNOWN")
                             else "needs_information", "component": "resources",
                             "basis": "stated", "reason": "estimated to need %g GB of %s; the "
                             "target has %g" % (needed, label, have) if field != "cpu_vcpus"
                             else "estimated to need %g vCPUs; the target has %g" % (needed, have)})
        elif status in ("ASSUMED", "UNKNOWN", "CONFLICTING"):
            findings.append({"verdict": "assumed", "component": "resources", "basis": "stated",
                             "reason": "fits only if the %s %s of %s is right (%g needed)"
                                       % (status.lower(), _fmt(have), label, needed)})
        elif needed > 0.8 * have:
            findings.append({"verdict": "warning", "component": "resources", "basis": "stated",
                             "reason": "estimated to use %d%% of the target's %s: little headroom"
                                       % (round(100 * needed / have), label)})
    order = ["incompatible", "needs_information", "not_assessed"]
    verdict = next((name for name in order if any(item["verdict"] == name for item in findings)),
                   "compatible")
    return {"verdict": verdict, "findings": findings}


def _fmt(value):
    return ("%g GB" % value) if isinstance(value, float) else str(value)


def assumptions(option, env):
    """Infrastructure values the option's fit rests on that are not settled (ASSUMED,
    UNKNOWN, CONFLICTING). Approval is allowed, but only with these named."""
    used = {"os", "hosting"}
    for item in option.get("components") or []:
        spec = COMPONENTS.get(component_id(item)) or {}
        used.update(spec.get("needs", []))
        if spec.get("web_server"):
            used.add("web_server")
        if spec.get("service"):
            used.add("can_install_services")
        if spec.get("gpu"):
            used.update(("gpu_present", "gpu_vram_gb"))
        if spec.get("runtime"):
            used.add("runtimes")
    used.update(field for field in (option.get("resources") or {}) if field in ENV_FIELDS)
    found = []
    for field in sorted(used):
        value, status = env_value(env, field)
        if value is not None and status in ("ASSUMED", "CONFLICTING"):
            found.append("%s = %s (%s)" % (field, value, status.lower()))
    return found


def complexity(option):
    """A rough rank of how much an option asks of the people who run it."""
    style = STYLES.get(str(option.get("style") or "").lower(), 2)
    parts = sum((COMPONENTS.get(component_id(item)) or {}).get("complexity", 2)
                for item in option.get("components") or [])
    return style * 10 + parts


def unjustified(option):
    """Optional components (caches, brokers, containers, GPU inference, ...) that the option
    includes without naming a requirement or constraint that needs them."""
    missing = []
    for item in option.get("components") or []:
        spec = COMPONENTS.get(component_id(item))
        if spec and spec.get("optional"):
            why = item.get("why") if isinstance(item, dict) else None
            if not why:
                missing.append(component_id(item))
    return missing
