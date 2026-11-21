"""The planning engine: evidence, infrastructure, architecture decisions, tasks, approval,
and execution through the queue.

Each scenario is a typical starting point for a plan. The assertions never fix a
universal answer ("use a monolith"); they check that the engine follows the evidence and
the constraints it was given, and that it refuses to decide what nobody has decided."""
import json
import os
import sqlite3
import unittest

from helpers import QueueTestCase

from mqlib import plan_catalog as catalog
from mqlib import plan_discover, plan_model, plan_report, plan_store
from mqlib.util import QueueError


def env(**fields):
    """Infrastructure fields; a value may be (value, status)."""
    out = {}
    for name, value in fields.items():
        value, status = value if isinstance(value, tuple) else (value, "USER_CONFIRMED")
        out[name] = {"value": value, "status": status, "source": "test"}
    return out


def option(option_id, *components, **extra):
    data = {"id": option_id, "name": option_id, "components": list(components)}
    data.update(extra)
    return data


def decision(decision_id, options, selected=None, category="architecture", **extra):
    data = {"id": decision_id, "title": decision_id, "category": category, "options": options,
            "selected": selected, "rationale": "chosen for the reasons recorded" if selected else "",
            "status": "PROPOSED"}
    data.update(extra)
    return data


def task(task_id, *deps, **extra):
    data = {"id": task_id, "title": "Task %s does a specific thing" % task_id,
            "depends_on": list(deps), "acceptance": ["%s is verified by a test" % task_id],
            "kind": "implementation"}
    data.update(extra)
    return data


class PlanCase(QueueTestCase):

    def make(self, plan_id="p", mode="new", **sections):
        q = self.queue()
        plan_store.create(q, plan_id, "Plan %s" % plan_id, mode)
        if sections:
            plan_store.apply(q, plan_id, sections)
        return q

    def plan(self, plan_id="p"):
        plan = plan_store.load(self.queue(), plan_id)
        plan.pop("_revision")
        return plan

    def errors(self, plan_id="p", code=None):
        return [item for item in plan_model.validate(self.plan(plan_id))
                if item["level"] == "error" and (code is None or item["code"] == code)]


# --------------------------------------------------------------------------- Scenario A

class SharedHostingMvc5(PlanCase):
    """Scenario A: an ASP.NET MVC 5 system on Windows shared hosting."""

    def setUp(self):
        super().setUp()
        self.write("Web/packages.config", '<packages>\n  <package id="Microsoft.AspNet.Mvc" '
                   'version="5.2.9" targetFramework="net48" />\n</packages>\n')
        self.write("Web/Web.config", '<configuration><connectionStrings><add name="db" '
                   'connectionString="Data Source=.;Initial Catalog=App;" providerName='
                   '"System.Data.SqlClient" /></connectionStrings></configuration>\n')
        self.write("Web/Web.csproj", "<Project><PropertyGroup><TargetFrameworkVersion>v4.8"
                   "</TargetFrameworkVersion></PropertyGroup></Project>\n")
        self.shared = {"id": "shared", "label": "current shared hosting",
                       "env": env(hosting="shared", os="windows", web_server="iis")}

    def test_discovery_separates_what_files_show_from_what_they_suggest(self):
        found = plan_discover.discover(self.project)
        statements = {item["statement"]: item for item in found["facts"]}
        mvc = next(item for item in found["facts"] if "ASP.NET MVC 5.2.9" in item["statement"])
        self.assertEqual((mvc["status"], mvc["evidence"]), ("VERIFIED", ["Web/packages.config"]))
        iis = next(item for item in found["facts"] if "IIS" in item["statement"])
        self.assertEqual(iis["status"], "ASSUMED")       # a Web.config is not proof of IIS
        self.assertEqual(set(found["existing_components"]) & {"aspnet-mvc5", "dotnet-framework",
                                                               "sqlserver"},
                         {"aspnet-mvc5", "dotnet-framework", "sqlserver"})
        self.assertTrue(any("Web/Web.config is present" == text for text in statements))

    def test_container_orchestration_is_rejected_and_the_existing_stack_fits(self):
        q = self.make(mode="existing", infrastructure={"target": "shared",
                                                       "scenarios": [self.shared]})
        keep = option("keep", "dotnet-framework", "aspnet-mvc5", "sqlserver", style="modular-monolith")
        rebuild = option("rebuild", "aspnet-core", "dotnet", {"id": "docker", "why": ["R1"]},
                         {"id": "kubernetes", "why": ["R1"]}, {"id": "redis", "why": ["R1"]},
                         style="microservices")
        plan_store.apply(q, "p", {"requirements": [{"id": "R1", "kind": "scalability",
                                                    "text": "grow", "status": "ASSUMED"}],
                                  "decisions": [decision("ADR-1", [keep, rebuild], "rebuild")]})
        messages = " ".join(item["message"] for item in self.errors(code="incompatible"))
        self.assertIn("Kubernetes", messages)
        self.assertIn("Redis runs on linux", messages)
        results = plan_model.evaluate_option(self.plan(), keep)
        self.assertEqual(results["shared"]["verdict"], "needs_information")   # runtimes unconfirmed
        plan_store.set_env(q, "p", "shared", "runtimes", "dotnet-framework", "USER_CONFIRMED", "host")
        plan_store.set_env(q, "p", "shared", "can_install_services", "false", "USER_CONFIRMED", "host")
        keep_sql = option("keep", "dotnet-framework", "aspnet-mvc5",
                          {"id": "sqlserver", "managed": True}, style="modular-monolith")
        plan_store.apply(q, "p", {"decisions": [decision("ADR-1", [keep_sql, rebuild], "keep")]})
        self.assertEqual(self.errors(code="incompatible"), [])
        self.assertEqual(plan_model.evaluate_option(self.plan(), keep_sql)["shared"]["verdict"],
                         "compatible")
        self.assertEqual(plan_model.suggested_status(self.plan(), self.plan()["decisions"][0])[0],
                         "PROPOSED")

    def test_replacing_the_existing_framework_is_a_material_decision(self):
        q = self.make(mode="existing", infrastructure={"target": "shared", "scenarios": [self.shared]},
                      existing_components=["aspnet-mvc5", "dotnet-framework"])
        core = option("core", "dotnet", "aspnet-core")
        stay = option("stay", "dotnet-framework", "aspnet-mvc5")
        plan_store.apply(q, "p", {"decisions": [decision("ADR-1", [stay, core], "core",
                                                         category="refactoring")]})
        self.assertTrue(self.errors(code="replaces-existing"))
        plan_store.apply(q, "p", {"decisions": [{"id": "ADR-1", "material": True}]})
        self.assertEqual(self.errors(code="replaces-existing"), [])

    def test_a_framework_decision_cannot_be_declared_immaterial(self):
        self.make(decisions=[decision("ADR-1", [option("a", "php"), option("b", "node")], "a",
                                      category="framework", material=False)])
        self.assertTrue(self.errors(code="materiality"))


# --------------------------------------------------------------------------- Scenario B

class ExternalDms(PlanCase):
    """Scenario B: a .NET application for 200 users with an external DMS."""

    def test_documents_kept_in_the_dms_are_not_sized_as_local_storage(self):
        inputs = {"concurrent_users": {"value": 200, "status": "USER_CONFIRMED"},
                  "requests_per_user_per_minute": {"value": 6, "status": "ASSUMED"},
                  "avg_request_ms": {"value": 250, "status": "ASSUMED"},
                  "peak_factor": {"value": 3, "status": "ASSUMED"},
                  "uploads_per_day": {"value": 500, "status": "USER_CONFIRMED"},
                  "avg_upload_mb": {"value": 2, "status": "USER_CONFIRMED"},
                  "documents_stored_externally": {"value": 1, "status": "USER_CONFIRMED"}}
        results = {item["name"]: item for item in plan_model.capacity(inputs)}
        self.assertEqual(results["requests_per_second"]["value"], 20.0)
        self.assertEqual(results["requests_per_second"]["status"], "ASSUMED")   # weakest input
        self.assertEqual(results["peak_requests_per_second"]["value"], 60.0)
        self.assertEqual(results["requests_in_flight"]["value"], 15.0)
        self.assertEqual(results["application_document_storage_gb"]["value"], 0.0)
        self.assertNotIn("document_storage_gb", results)
        inputs["documents_stored_externally"] = {"value": 0, "status": "USER_CONFIRMED"}
        local = {item["name"]: item for item in plan_model.capacity(inputs)}
        self.assertAlmostEqual(local["document_storage_gb"]["value"], 356.4, places=1)
        self.assertIn("365 (assumed)", local["document_storage_gb"]["method"])

    def test_report_states_that_estimates_are_not_measurements(self):
        q = self.make(capacity={"inputs": {"concurrent_users": {"value": 200,
                                                               "status": "USER_CONFIRMED"},
                                           "requests_per_user_per_minute": {"value": 6,
                                                                            "status": "ASSUMED"}}})
        text = plan_report.infrastructure_md(self.plan())
        self.assertIn("not measurements", text)
        self.assertIn("load-tested", text)
        self.assertIn("rests on assumptions", text)


# --------------------------------------------------------------------------- Scenario C

class SmallVps(PlanCase):
    """Scenario C: a small SaaS with PostgreSQL on a modest VPS."""

    def setUp(self):
        super().setUp()
        self.vps = {"id": "vps", "env": env(hosting="vps", os="linux", cpu_vcpus=2, ram_gb=4,
                                            can_install_services=True)}

    def test_a_cache_needs_a_reason(self):
        q = self.make(infrastructure={"target": "vps", "scenarios": [self.vps]})
        plain = option("plain", "python", "postgres")
        cached = option("cached", "python", "postgres", "redis")
        plan_store.apply(q, "p", {"decisions": [decision("ADR-1", [plain, cached], "cached")]})
        self.assertTrue(self.errors(code="unjustified-component"))
        plan_store.apply(q, "p", {"requirements": [{"id": "R1", "kind": "performance",
                                                    "text": "p95 under 200 ms",
                                                    "status": "USER_CONFIRMED"}],
                                  "decisions": [decision("ADR-1", [plain, option(
                                      "cached", "python", "postgres", {"id": "redis", "why": ["R1"]})],
                                      "cached")]})
        self.assertEqual(self.errors(code="unjustified-component"), [])

    def test_more_memory_than_the_server_has_is_incompatible(self):
        self.make(infrastructure={"target": "vps", "scenarios": [self.vps]})
        heavy = option("heavy", "java", "postgres", resources={"ram_gb": 6})
        result = plan_model.evaluate_option(self.plan(), heavy)["vps"]
        self.assertEqual(result["verdict"], "incompatible")
        self.assertIn("6 GB of memory", " ".join(item["reason"] for item in result["findings"]))
        tight = option("tight", "python", "postgres", resources={"ram_gb": 3.6})
        warnings = [item for item in plan_model.evaluate_option(self.plan(), tight)["vps"]["findings"]
                    if item["verdict"] == "warning"]
        self.assertTrue(warnings)

    def test_the_simpler_option_is_preferred_when_the_matrix_does_not_decide(self):
        criteria = [{"name": "cost", "weight": 2}, {"name": "scale", "weight": 1}]
        mono = option("mono", "python", "postgres", style="monolith",
                      scores={"cost": {"score": 4, "basis": "USER_CONFIRMED"},
                              "scale": {"score": 3, "basis": "ASSUMED"}})
        micro = option("micro", "python", "postgres", {"id": "rabbitmq", "why": ["R1"]},
                       style="microservices",
                       scores={"cost": {"score": 3, "basis": "ASSUMED"},
                               "scale": {"score": 5, "basis": "ASSUMED"}})
        self.make(infrastructure={"target": "vps", "scenarios": [self.vps]},
                  requirements=[{"id": "R1", "kind": "scalability", "text": "x", "status": "ASSUMED"}],
                  decisions=[decision("ADR-1", [mono, micro], "micro", criteria=criteria)])
        table = plan_model.matrix(self.plan()["decisions"][0])
        self.assertFalse(table["decisive"])
        self.assertNotEqual(table["options"]["micro"]["low"], table["options"]["micro"]["high"])
        warnings = [item for item in plan_model.validate(self.plan()) if item["code"] == "simpler-option"]
        self.assertTrue(warnings)
        self.assertIn("mono", warnings[0]["message"])


# --------------------------------------------------------------------------- Scenarios D and E

class AiWorkloads(PlanCase):

    def test_cloud_apis_without_a_gpu(self):
        """Scenario D: hosted model APIs; no local GPU."""
        scenario = {"id": "vm", "env": env(hosting="vm", os="linux", gpu_present=False,
                                           outbound_internet=True, can_install_services=True)}
        self.make(infrastructure={"target": "vm", "scenarios": [scenario]})
        api = option("api", "python", "postgres", "cloud-llm-api")
        local = option("local", "python", "postgres", {"id": "local-llm-gpu", "why": ["R1"],
                                                       "min_vram_gb": 16})
        plan = self.plan()
        self.assertEqual(plan_model.evaluate_option(plan, api)["vm"]["verdict"], "compatible")
        result = plan_model.evaluate_option(plan, local)["vm"]
        self.assertEqual(result["verdict"], "incompatible")
        self.assertIn("needs a GPU", " ".join(item["reason"] for item in result["findings"]))

    def test_data_residency_rules_out_a_hosted_api(self):
        scenario = {"id": "vm", "env": env(hosting="vm", os="linux", outbound_internet=True)}
        self.make(infrastructure={"target": "vm", "scenarios": [scenario]},
                  requirements=[{"id": "R9", "kind": "compliance", "status": "USER_CONFIRMED",
                                 "text": "personal data stays in the country",
                                 "tags": ["data-residency"]}])
        result = plan_model.evaluate_option(self.plan(), option("api", "cloud-llm-api"))["vm"]
        self.assertEqual(result["verdict"], "incompatible")
        self.assertIn("R9", " ".join(item["reason"] for item in result["findings"]))

    def test_local_inference_follows_the_stated_gpu_memory(self):
        """Scenario E: on-premises inference with defined compute."""
        scenario = {"id": "onprem", "env": env(hosting="onprem", os="linux", gpu_present=True,
                                               gpu_vram_gb=24)}
        self.make(infrastructure={"target": "onprem", "scenarios": [scenario]})
        fits = option("fits", {"id": "local-llm-gpu", "why": ["R1"], "min_vram_gb": 16})
        too_big = option("big", {"id": "local-llm-gpu", "why": ["R1"], "min_vram_gb": 48})
        unstated = option("unstated", {"id": "local-llm-gpu", "why": ["R1"]})
        plan = self.plan()
        self.assertEqual(plan_model.evaluate_option(plan, fits)["onprem"]["verdict"], "compatible")
        self.assertEqual(plan_model.evaluate_option(plan, too_big)["onprem"]["verdict"],
                         "incompatible")
        self.assertEqual(plan_model.evaluate_option(plan, unstated)["onprem"]["verdict"],
                         "needs_information")

    def test_unknown_gpu_is_not_assumed_either_way(self):
        scenario = {"id": "x", "env": env(hosting="onprem", os="linux")}
        self.make(infrastructure={"target": "x", "scenarios": [scenario]})
        result = plan_model.evaluate_option(self.plan(), option(
            "o", {"id": "local-llm-gpu", "why": ["R1"], "min_vram_gb": 8}))["x"]
        self.assertEqual(result["verdict"], "needs_information")


# --------------------------------------------------------------------------- unknown infrastructure

class UnknownInfrastructure(PlanCase):

    def test_no_target_means_scenarios_and_needs_information(self):
        scenarios = [{"id": "A", "label": "existing 4 vCPU / 8 GB VM", "status": "ASSUMED",
                      "env": env(hosting=("vm", "ASSUMED"), os=("linux", "ASSUMED"),
                                 ram_gb=(8, "ASSUMED"))},
                     {"id": "B", "label": "managed cloud", "status": "ASSUMED",
                      "env": env(hosting=("public_cloud", "ASSUMED"))}]
        self.make(infrastructure={"target": None, "scenarios": scenarios},
                  decisions=[decision("ADR-1", [option("a", "python", "postgres"),
                                                option("b", "node", "postgres")], "a",
                                      category="infrastructure")])
        status, reasons = plan_model.suggested_status(self.plan(), self.plan()["decisions"][0])
        self.assertEqual(status, "NEEDS_INFORMATION")
        self.assertIn("deployment target is not known", " ".join(reasons))
        stored = self.plan()["decisions"][0]          # the stored status follows the evidence
        self.assertEqual(stored["status"], "NEEDS_INFORMATION")
        self.assertTrue(stored["missing"])
        verdicts = plan_model.evaluate_option(self.plan(), self.plan()["decisions"][0]["options"][0])
        self.assertEqual(set(verdicts), {"A", "B"})      # evaluated per scenario

    def test_the_developer_machine_cannot_be_the_target(self):
        self.make(infrastructure={"target": "dev", "scenarios": [
            {"id": "dev", "source": "developer-machine", "env": env(os="windows")}]})
        self.assertTrue(self.errors(code="developer-machine-target"))

    def test_assumed_infrastructure_never_makes_an_option_incompatible(self):
        scenario = {"id": "s", "env": env(os=("linux", "ASSUMED"))}
        self.make(infrastructure={"target": "s", "scenarios": [scenario]})
        result = plan_model.evaluate_option(self.plan(), option("o", "aspnet-mvc5", "dotnet-framework"))
        self.assertEqual(result["s"]["verdict"], "needs_information")

    def test_unknown_technology_is_reported_not_guessed(self):
        result = catalog.check_option(option("o", "some-new-db"), env(os="linux"))
        self.assertEqual(result["verdict"], "not_assessed")


# --------------------------------------------------------------------------- evidence

class Evidence(PlanCase):

    def test_verified_needs_evidence_and_classes_are_fixed(self):
        self.make(facts=[{"id": "F1", "topic": "x", "statement": "y", "status": "VERIFIED"},
                         {"id": "F2", "topic": "x", "statement": "z", "status": "PROBABLY"}])
        codes = [item["code"] for item in self.errors()]
        self.assertIn("verified-without-evidence", codes)
        self.assertIn("evidence-class", codes)

    def test_conflicts_and_unknowns_are_listed(self):
        self.make(facts=[{"id": "F1", "topic": "db", "statement": "SQL Server 2019 or 2012?",
                          "status": "CONFLICTING"},
                         {"id": "F2", "topic": "users", "statement": "number of users",
                          "status": "UNKNOWN"}])
        findings = plan_model.validate(self.plan())
        self.assertTrue(any(item["code"] == "conflicting" for item in findings))
        self.assertTrue(any(item["code"] == "unknown" for item in findings))
        self.assertIn("F2", plan_report.requirements_md(self.plan()))

    def test_a_decision_resting_on_an_unknown_fact_needs_information(self):
        scenario = {"id": "s", "env": env(hosting="vps", os="linux", can_install_services=True)}
        self.make(infrastructure={"target": "s", "scenarios": [scenario]},
                  facts=[{"id": "F1", "topic": "users", "statement": "concurrent users",
                          "status": "UNKNOWN"}],
                  decisions=[decision("ADR-1", [option("a", "python", "postgres"),
                                                option("b", "node", "postgres")], "a",
                                      facts=["F1"])])
        status, reasons = plan_model.suggested_status(self.plan(), self.plan()["decisions"][0])
        self.assertEqual(status, "NEEDS_INFORMATION")
        self.assertIn("F1", reasons[0])


# --------------------------------------------------------------------------- tasks

class Tasks(PlanCase):

    def test_cycles_unknown_dependencies_and_vague_tasks_are_errors(self):
        self.make(tasks=[task("T1", "T3"), task("T2", "T1"), task("T3", "T2"), task("T4", "T9"),
                         {"id": "T5", "title": "Implement backend"},
                         {"id": "T6", "title": "Add the login form", "kind": "implementation"}])
        codes = [item["code"] for item in self.errors()]
        self.assertIn("cycle", codes)
        self.assertIn("unknown-dependency", codes)
        self.assertIn("vague-task", codes)
        self.assertIn("no-acceptance", codes)
        cycle = next(item for item in self.errors() if item["code"] == "cycle")
        self.assertIn("->", cycle["message"])

    def test_tasks_wait_for_dependencies_and_are_held_by_unapproved_decisions(self):
        self.make(decisions=[decision("ADR-1", [option("a", "php"), option("b", "node")], "a")],
                  tasks=[task("T1"), task("T2", "T1"), task("T3", decisions=["ADR-1"]),
                         task("T4", "T3")])
        states = plan_model.readiness(self.plan())
        self.assertEqual(states["T1"][0], "ready")
        self.assertEqual(states["T2"][0], "waiting")
        self.assertEqual(states["T3"][0], "held")
        self.assertEqual(states["T4"][0], "held")
        later = plan_model.readiness(self.plan(), done={"T1"}, failed=set())
        self.assertEqual(later["T2"][0], "ready")
        blocked = plan_model.readiness(self.plan(), done=set(), failed={"T1"})
        self.assertEqual(blocked["T2"][0], "blocked")

    def test_a_task_cannot_bring_technology_no_decision_selects(self):
        self.make(decisions=[decision("ADR-1", [option("a", "python", "postgres"),
                                                option("b", "node", "postgres")], "a")],
                  tasks=[task("T1", components=["postgres"]), task("T2", components=["mongodb"])])
        unplanned = self.errors(code="unplanned-technology")
        self.assertEqual([item["where"] for item in unplanned], ["T2"])

    def test_uncovered_must_requirements_are_reported(self):
        self.make(requirements=[{"id": "R1", "kind": "functional", "text": "x",
                                 "status": "USER_CONFIRMED"},
                                {"id": "R2", "kind": "functional", "text": "y",
                                 "status": "USER_CONFIRMED", "priority": "could"}],
                  tasks=[task("T1", requirements=[])])
        warnings = [item["where"] for item in plan_model.validate(self.plan())
                    if item["code"] == "uncovered-requirement"]
        self.assertEqual(warnings, ["R1"])


# --------------------------------------------------------------------------- depth and models

class Depth(PlanCase):

    def test_depth_follows_the_plan_and_can_be_overridden(self):
        self.make()
        self.assertEqual(plan_model.planning_depth({"mode": "existing"})[0], "quick")
        deep = {"mode": "new", "requirements": [{"kind": "security"}, {"kind": "availability"}]}
        depth, reasons = plan_model.planning_depth(deep)
        self.assertEqual(depth, "deep")
        self.assertIn("a new system", reasons)
        self.assertEqual(plan_model.planning_depth(dict(deep, depth="quick",
                                                        depth_source="manual"))[0], "quick")
        models = plan_model.stage_models("deep")
        self.assertEqual(models["architecture"], "opus")
        self.assertEqual(models["implementation"], "auto")
        for value in models.values():          # tiers, never model versions
            self.assertIn(value, ("opus", "sonnet", "haiku", "auto"))


# --------------------------------------------------------------------------- approval and revisions

def ready_plan(case, plan_id="p"):
    """A complete, approvable plan on a stated Linux VPS."""
    scenario = {"id": "vps", "env": env(hosting="vps", os="linux", can_install_services=True,
                                        ram_gb=8, cpu_vcpus=4)}
    q = case.make(plan_id, infrastructure={"target": "vps", "scenarios": [scenario]},
                  requirements=[{"id": "R1", "kind": "functional", "text": "customers can sign up",
                                 "status": "USER_CONFIRMED"}],
                  milestones=[{"id": "M1", "title": "Sign-up"}],
                  decisions=[decision("ADR-1", [option("a", "python", "postgres", style="monolith"),
                                                option("b", "node", "mongodb", style="monolith")],
                                      "a", requirements=["R1"])],
                  tasks=[task("T1", milestone="M1", requirements=["R1"], decisions=["ADR-1"],
                              components=["postgres"]),
                         task("T2", "T1", milestone="M1", requirements=["R1"], decisions=["ADR-1"])])
    return q


class Approval(PlanCase):

    def test_a_proposal_never_becomes_approved_by_editing(self):
        q = ready_plan(self)
        with self.assertRaises(QueueError) as raised:
            plan_store.apply(q, "p", {"decisions": [{"id": "ADR-1", "status": "APPROVED"}]})
        self.assertIn("project approve", str(raised.exception))

    def test_preview_changes_nothing_and_approval_freezes_a_revision(self):
        q = ready_plan(self)
        preview = plan_store.approve(q, "p")
        self.assertEqual((preview["approved"], preview["decisions"]), (False, ["ADR-1"]))
        self.assertEqual(plan_store.approvals(q, "p"), [])
        done = plan_store.approve(q, "p", confirmed=True)
        self.assertTrue(done["approved"])
        frozen = plan_store.revision(q, "p", 1)
        self.assertEqual(frozen["decisions"][0]["status"], "APPROVED")
        self.assertEqual(plan_store.approvals(q, "p")[0]["sha256"], plan_model.content_hash(frozen))
        self.assertEqual(plan_store.load(q, "p")["_revision"], 2)
        with self.assertRaises(sqlite3.DatabaseError):
            with q.tx():
                q.conn.execute("UPDATE plan_revisions SET content='{}'")
        with self.assertRaises(sqlite3.DatabaseError):
            with q.tx():
                q.conn.execute("DELETE FROM plan_approvals")
        self.assertEqual(plan_model.stage(frozen, 1), "approved for execution")

    def test_an_approved_decision_is_changed_only_by_an_amendment(self):
        q = ready_plan(self)
        plan_store.approve(q, "p", confirmed=True)
        with self.assertRaises(QueueError) as raised:
            plan_store.apply(q, "p", {"decisions": [{"id": "ADR-1", "selected": "b"}]})
        self.assertIn("supersedes", str(raised.exception))
        plan_store.apply(q, "p", {"decisions": [decision(
            "ADR-2", [option("a", "python", "postgres"), option("c", "python", "mysql")], "c",
            supersedes="ADR-1", category="database")]})
        result = plan_store.approve(q, "p", decisions=["ADR-2"], execution=False, confirmed=True)
        self.assertEqual(result["decisions"], ["ADR-2"])
        statuses = {item["id"]: item["status"] for item in plan_store.revision(q, "p", 2)["decisions"]}
        self.assertEqual(statuses, {"ADR-1": "SUPERSEDED", "ADR-2": "APPROVED"})

    def test_a_decision_that_needs_information_is_not_approved_and_holds_only_its_tasks(self):
        """Adversarial: the owner asks to go ahead while the hosting is still unknown."""
        q = ready_plan(self)
        plan_store.apply(q, "p", {"decisions": [decision(
            "ADR-3", [option("gpu", {"id": "local-llm-gpu", "why": ["R1"], "min_vram_gb": 16}),
                      option("api", "cloud-llm-api")], "gpu", category="infrastructure")],
            "tasks": [task("T3", decisions=["ADR-3"], requirements=["R1"]),
                      task("T4", "T3", requirements=["R1"])]})
        result = plan_store.approve(q, "p", confirmed=True)
        self.assertEqual(result["decisions"], ["ADR-1"])
        self.assertEqual([item["decision"] for item in result["refused"]], ["ADR-3"])
        self.assertIn("GPU", " ".join(result["refused"][0]["reasons"]))
        report = plan_store.execute(q, "p", confirmed=True)
        self.assertEqual([item["task"] for item in report["added"]], ["T1", "T2"])
        self.assertEqual(sorted(item["task"] for item in report["held"]), ["T3", "T4"])
        frozen = plan_store.revision(q, "p", 1)
        self.assertEqual(model_status(frozen, "ADR-3"), "NEEDS_INFORMATION")

    def test_approval_names_what_the_decision_rests_on_that_nobody_confirmed(self):
        scenario = {"id": "vm", "env": env(hosting="vm", os="windows", ram_gb=(8, "ASSUMED"),
                                           can_install_services=True)}
        q = self.make(infrastructure={"target": "vm", "scenarios": [scenario]},
                      requirements=[{"id": "R1", "kind": "performance", "text": "200 users",
                                     "status": "ASSUMED"}],
                      decisions=[decision("ADR-1", [option("a", "dotnet", "aspnet-core", "sqlserver",
                                                           resources={"ram_gb": 4}),
                                                    option("b", "node", "postgres")], "a",
                                          requirements=["R1"], assumptions=["the team knows .NET"])])
        preview = plan_store.approve(q, "p", execution=False)
        rests_on = " ".join(preview["assumptions"]["ADR-1"])
        self.assertIn("ram_gb = 8 (assumed)", rests_on)
        self.assertIn("requirement R1", rests_on)
        self.assertIn("the team knows .NET", rests_on)
        out = self.mq("project", "approve", "p", "--decisions-only")[1]
        self.assertIn("rests on infrastructure: ram_gb = 8 (assumed)", out)
        findings = plan_model.evaluate_option(self.plan(), self.plan()["decisions"][0]["options"][0])
        self.assertEqual(findings["vm"]["verdict"], "compatible")
        self.assertTrue(any(item["verdict"] == "assumed" for item in findings["vm"]["findings"]))

    def test_execution_needs_an_approved_revision_and_a_clean_check(self):
        q = ready_plan(self)
        with self.assertRaises(QueueError) as raised:
            plan_store.execute(q, "p")
        self.assertIn("no revision approved", str(raised.exception))
        plan_store.apply(q, "p", {"tasks": [task("T9", "T8")]})
        result = plan_store.approve(q, "p", confirmed=True)
        self.assertFalse(result["approved"])
        self.assertIn("cannot be approved for execution", result["message"])


def model_status(plan, decision_id):
    return plan_model.by_id(plan["decisions"])[decision_id]["status"]


# --------------------------------------------------------------------------- queue integration

class QueueIntegration(PlanCase):

    def approved(self):
        q = ready_plan(self)
        plan_store.approve(q, "p", confirmed=True)
        return q

    def test_import_preserves_dependencies_and_is_idempotent(self):
        q = self.approved()
        self.add("an older task that has nothing to do with the plan")
        first = plan_store.execute(q, "p", confirmed=True)
        ids = {item["task"]: item["queue_task"] for item in first["added"]}
        self.assertEqual([row["id"] for row in q.deps(ids["T2"])], [ids["T1"]])
        queued = q.get(ids["T1"])
        self.assertEqual(queued["source"], "plan:p")
        self.assertIn("Approved architecture", queued["prompt"])
        self.assertIn("postgres", queued["prompt"])
        self.assertEqual(queued["acceptance"], "- T1 is verified by a test")
        again = plan_store.execute(q, "p", confirmed=True)
        self.assertEqual(again["added"], [])
        self.assertEqual(len(again["unchanged"]), 2)
        self.assertEqual(len(q.tasks()), 3)        # the older task and two plan tasks
        self.assertEqual(q.get(1)["source"], "cli")

    def test_a_changed_task_replaces_its_pending_queue_task_and_done_work_is_kept(self):
        q = self.approved()
        first = {item["task"]: item["queue_task"] for item in
                 plan_store.execute(q, "p", confirmed=True)["added"]}
        with q.tx():
            q.conn.execute("UPDATE tasks SET status='done' WHERE id=?", (first["T1"],))
        plan_store.apply(q, "p", {"tasks": [{"id": "T1", "description": "changed"},
                                            {"id": "T2", "description": "changed too"}]})
        plan_store.approve(q, "p", confirmed=True)
        report = plan_store.execute(q, "p", confirmed=True)
        self.assertEqual([item["task"] for item in report["kept_done"]], ["T1"])
        self.assertEqual([item["task"] for item in report["replaced"]], ["T2"])
        self.assertEqual(q.get(first["T2"])["status"], "cancelled")
        new_t2 = report["added"][0]["queue_task"]
        self.assertEqual([row["id"] for row in q.deps(new_t2)], [first["T1"]])
        self.assertEqual(plan_store.links(q, "p")["T2"]["revision"], 2)
        self.assertEqual(plan_store.links(q, "p")["T1"]["revision"], 1)   # stays with its revision

    def test_a_queue_task_whose_link_was_lost_is_adopted_not_duplicated(self):
        q = self.approved()
        plan = plan_store.revision(q, "p", 1)
        prompt, acceptance = plan_model.compose_task_prompt(plan, plan["tasks"][0])
        orphan = q.add(prompt, source="plan:p")
        report = plan_store.execute(q, "p", confirmed=True)
        self.assertEqual([item["task"] for item in report["unchanged"]], ["T1"])
        self.assertEqual(plan_store.links(q, "p")["T1"]["queue_task_id"], orphan)

    def test_the_task_model_is_honoured_and_analysis_in_deep_plans_gets_the_strongest_tier(self):
        q = ready_plan(self)
        plan_store.apply(q, "p", {"depth": "deep", "depth_source": "manual",
                                  "tasks": [task("T1", model="haiku", milestone="M1",
                                                 requirements=["R1"], decisions=["ADR-1"],
                                                 components=["postgres"]),
                                            task("T3", kind="analysis", acceptance=[],
                                                 requirements=["R1"])]})
        plan_store.approve(q, "p", confirmed=True)
        ids = {item["task"]: item["queue_task"]
               for item in plan_store.execute(q, "p", confirmed=True)["added"]}
        routing = {task_id: self.task(queue_id)["routing"] for task_id, queue_id in ids.items()}
        self.assertEqual((routing["T1"]["selectedModel"], routing["T1"]["manualOverride"]),
                         ("haiku", True))
        self.assertEqual(routing["T3"]["selectedModel"], "opus")
        self.assertFalse(routing["T2"]["manualOverride"])     # left to the router

    def test_plan_tasks_run_in_dependency_order_and_progress_is_read_from_the_queue(self):
        q = self.approved()
        plan_store.execute(q, "p", confirmed=True)
        self.assertEqual(self.mq("run")[0], 0)
        starts = [task_id for task_id, _ in self.starts()]
        self.assertEqual(starts, sorted(starts))
        progress = plan_store.progress(q, "p")
        self.assertEqual([row["state"] for row in progress["tasks"]], ["done", "done"])
        self.assertEqual(progress["milestones"][0]["done"], 2)
        self.assertEqual(progress["stage"], "approved for execution")

    def test_a_change_that_adds_unapproved_infrastructure_is_stopped(self):
        q = self.approved()
        ids = {item["task"]: item["queue_task"]
               for item in plan_store.execute(q, "p", confirmed=True)["added"]}
        self.script(ids["T1"], "write Dockerfile FROM python:3.12")
        code, out, err = self.mq("run", check=False)
        t1, t2 = self.task(ids["T1"]), self.task(ids["T2"])
        self.assertEqual(t1["status"], "failed")
        self.assertIn("does not comply with the approved plan", t1["last_error"])
        self.assertIn("docker", t1["last_error"])
        self.assertEqual(t1["attempts"], 1)                    # not retried
        self.assertEqual(t2["status"], "pending")              # its dependent waits (blocked)
        self.assertEqual(self.queue().state_of(self.queue().get(ids["T2"])), "blocked")
        self.assertTrue(os.path.exists(os.path.join(self.project, "Dockerfile")))   # kept
        findings = plan_store.progress(q, "p")["findings"]
        self.assertEqual([item["component"] for item in findings], ["docker"])

    def test_warn_policy_records_the_finding_and_lets_the_task_finish(self):
        q = self.approved()
        ids = {item["task"]: item["queue_task"]
               for item in plan_store.execute(q, "p", confirmed=True)["added"]}
        self.script(ids["T1"], "write Dockerfile FROM python:3.12")
        self.set_config(plan_compliance="warn")
        self.assertEqual(self.mq("run")[0], 0)
        self.assertEqual(self.task(ids["T1"])["status"], "done")
        self.assertEqual(len(plan_store.progress(q, "p")["findings"]), 1)

    def test_a_rejected_technology_is_a_violation_even_when_warning_only(self):
        q = self.approved()
        ids = {item["task"]: item["queue_task"]
               for item in plan_store.execute(q, "p", confirmed=True)["added"]}
        self.write("package.json", '{"dependencies": {}}\n')
        self.git("add", "-A")
        self.git("commit", "-q", "-m", "package.json")
        self.script(ids["T1"], 'write package.json {"dependencies": {"mongoose": "8.0.0"}}')
        self.set_config(plan_compliance="warn")
        self.mq("run", check=False)
        t1 = self.task(ids["T1"])
        self.assertEqual(t1["status"], "failed")
        self.assertIn("rejected", t1["last_error"])

    def test_revision_names_the_affected_tasks_and_keeps_completed_work(self):
        q = self.approved()
        ids = {item["task"]: item["queue_task"]
               for item in plan_store.execute(q, "p", confirmed=True)["added"]}
        with q.tx():
            q.conn.execute("UPDATE tasks SET status='done' WHERE id=?", (ids["T1"],))
        result = plan_store.revise(q, "p", "the hosting has only 2 GB of memory",
                                   decision_ids=["ADR-1"])
        self.assertEqual(result["affected"], ["T1", "T2"])
        self.assertEqual(result["completed_kept"], ["T1"])
        self.assertEqual(result["to_replan"], ["T2"])
        self.assertEqual(result["approved_decisions_impacted"], ["ADR-1"])
        self.assertIn("supersedes", result["next"])
        self.assertEqual(plan_store.load(q, "p")["issues"][0]["id"], "I1")


class CommandLine(PlanCase):

    def test_the_whole_workflow_through_the_command_line(self):
        self.write("app/requirements.txt", "flask==3.0\npsycopg2-binary==2.9\n")
        self.git("add", "-A")
        self.git("commit", "-q", "-m", "app")
        self.mq("project", "new", "shop", "--title", "Shop", "--mode", "existing")
        out = self.mq("project", "discover", "shop")[1]
        self.assertIn("not the deployment target", out)
        for field, value in (("hosting", "vps"), ("os", "linux"), ("can_install_services", "true")):
            self.mq("project", "env", "shop", "vps", field, value, "--status", "USER_CONFIRMED",
                    "--source", "hosting contract")
        self.mq("project", "target", "shop", "vps")
        fragment = {"requirements": [{"id": "R1", "kind": "functional", "text": "orders export",
                                      "status": "USER_CONFIRMED"}],
                    "decisions": [decision("ADR-1", [option("a", "python", "postgres"),
                                                     option("b", "python", "mysql")], "a",
                                           category="database", requirements=["R1"])],
                    "tasks": [task("T1", requirements=["R1"], decisions=["ADR-1"])]}
        self.mq("project", "apply", "shop", stdin=json.dumps(fragment).encode("utf-8"))
        self.mq("project", "apply", "shop", "--data", json.dumps(
            {"open_questions": ["Which payment provider?"]}))
        self.assertEqual(self.plan("shop")["open_questions"], ["Which payment provider?"])
        code, out, _ = self.mq("project", "check", "shop", check=False)
        self.assertEqual(code, 0, out)
        self.assertIn("ADR-1 PROPOSED (evidence says PROPOSED)", out)
        self.assertIn("Nothing was changed", self.mq("project", "approve", "shop")[1])
        self.assertIn("Approved for execution", self.mq("project", "approve", "shop", "--yes")[1])
        self.assertIn("add", self.mq("project", "execute", "shop", "--yes")[1])
        status = self.mq_json("project", "status", "shop")
        self.assertEqual(status["tasks"][0]["state"], "pending")
        written = self.mq_json("project", "report", "shop")["files"]
        names = {os.path.basename(path) for path in written}
        for expected in ("report.md", "requirements.md", "infrastructure.md", "roadmap.md",
                         "tasks.json", "ADR-1.md", "plan.json"):
            self.assertIn(expected, names)
        with open(next(path for path in written if path.endswith("report.md")),
                  encoding="utf-8") as handle:
            report = handle.read()
        for number in range(1, 21):
            self.assertIn("## %d. " % number, report)
        # The report is written to the data directory unless asked otherwise: the
        # workspace stays clean for the runner.
        self.assertEqual(self.git("status", "--porcelain"), "")

    def test_plan_tables_do_not_disturb_the_queue(self):
        self.mq("project", "new", "x")
        self.add("ordinary work")
        self.assertEqual(self.mq("run")[0], 0)
        self.assertIn("1 done", self.mq("status")[1])


if __name__ == "__main__":
    unittest.main()
