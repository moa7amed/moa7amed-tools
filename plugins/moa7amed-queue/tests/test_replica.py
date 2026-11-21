"""Replica: application reconstruction from evidence.

Uses the sample application in tests/fixtures/replica_sample (owned by this repository,
synthetic data) and images generated here. Browser capture is exercised only when
Playwright and a browser are available; everything else runs anywhere."""
import json
import os
import subprocess
import sys
import unittest

from helpers import MQ, QueueTestCase
from test_planning import decision, env, option

from mqlib import plan_model, plan_store, replica_css, replica_image
from mqlib import replica_model as model
from mqlib import replica_report
from mqlib import replica_store as rstore
from mqlib.util import QueueError

HERE = os.path.dirname(os.path.abspath(__file__))
SAMPLE = os.path.join(HERE, "fixtures", "replica_sample")
REQUEST = "Reconstruct this app from its URL and these screenshots: https://example.com/app"


def picture(path, width=200, height=120, background=(250, 250, 250), boxes=()):
    """A synthetic screenshot: a background with coloured rectangles."""
    image = replica_image.solid(width, height, background)
    for (x, y, w, h, color) in boxes:
        for row in range(y, min(height, y + h)):
            for column in range(x, min(width, x + w)):
                image.pixels[row][column] = color
    replica_image.write_png(path, image)
    return path


LIST_SCREEN = [(0, 0, 200, 20, (43, 108, 176)), (10, 30, 120, 10, (26, 32, 44)),
               (10, 50, 180, 60, (200, 210, 225))]
MID_SCREEN = LIST_SCREEN[:2] + [(10, 50, 150, 60, (200, 210, 225))]   # 0.88 against LIST
DETAIL_SCREEN = [(0, 0, 200, 20, (43, 108, 176)), (10, 30, 60, 10, (26, 32, 44)),
                 (10, 50, 90, 40, (200, 210, 225))]


class ReplicaCase(QueueTestCase):

    def new_project(self, request=REQUEST, project_id="r1", **kwargs):
        return rstore.create(self.queue(), project_id, request, **kwargs)

    def shot(self, name, boxes=LIST_SCREEN, **kwargs):
        return picture(os.path.join(self.tmp, name), boxes=boxes, **kwargs)

    def inventory(self, project_id="r1", mode_refs=("E2",)):
        q = self.queue()
        return rstore.apply_inventory(q, project_id, {
            "screens": [{"id": "S1", "name": "Shipments list", "evidence": "OBSERVED",
                         "refs": list(mode_refs), "layout": "top bar, filters, table",
                         "components": ["top bar", "table"], "navigates_to": ["S2"]},
                        {"id": "S2", "name": "Shipment details", "evidence": "OBSERVED",
                         "refs": ["E3"], "navigates_to": ["S1"]}],
            "features": [{"id": "F1", "name": "Filter by status", "screens": ["S1"],
                          "evidence": "OBSERVED", "refs": ["E2"], "behavior": "only matching rows"},
                         {"id": "F2", "name": "Save a note", "screens": ["S2"],
                          "evidence": "DOCUMENTED", "refs": ["E4"], "backend": True,
                          "behavior": "notes are stored"},
                         {"id": "F3", "name": "Export", "screens": ["S1"], "evidence": "UNKNOWN"}],
            "flows": [{"id": "U1", "name": "Find and open", "evidence": "OBSERVED",
                       "refs": ["E2", "E3"], "steps": [{"screen": "S1", "action": "filter"},
                                                       {"screen": "S2", "action": "view"}]}]})

    def standard_project(self, ownership="own", **kwargs):
        """r1 with a URL (E1), two screenshots (E2, E3) and an API document (E4)."""
        self.new_project(ownership=ownership, **kwargs)
        q = self.queue()
        rstore.add_ref(q, "r1", None, self.shot("list.png"))
        rstore.add_ref(q, "r1", None, self.shot("detail.png", DETAIL_SCREEN))
        api = self.write("docs/api.yaml", "openapi: 3.0.0\npaths:\n  /notes: {}\n",
                         root=os.path.join(self.tmp, "supplied"))
        rstore.add_ref(q, "r1", "api", api)
        return q


# --------------------------------------------------------------------------- intent (4, 26)

class Intent(unittest.TestCase):

    def test_reconstruction_requests_are_recognised(self):
        for text, mode in (
                ("Replicate this site: https://example.com", "visual"),
                ("Make a working app from these screenshots.", "functional"),
                ("Rebuild our MVC application from its source code.", "full"),
                ("Recreate this mobile interface from the screen recordings.", "visual"),
                ("Reconstruct this app from its URL and these screenshots.", "functional"),
                ("Clone the UI of https://example.com/dashboard", "visual")):
            found = model.intent(text)
            self.assertIsNotNone(found["intent"], text)
            self.assertEqual(found["mode"], mode, text)

    def test_other_requests_and_questions_are_not(self):
        for text in ("What does https://example.com do?", "Summarise https://example.com/blog",
                     "git clone https://github.com/example/repo", "Reproduce the bug in the login form",
                     "Rebuild the docker image", "How would I rebuild this website?",
                     "Fix the typo in the README", "Is https://example.com down?"):
            self.assertIsNone(model.intent(text)["intent"], text)

    def test_the_word_replica_is_not_database_replication(self):
        from mqlib import router
        self.assertEqual(router.classify("Rebuild this website as a replica of the original")
                         ["critical"], [])
        for text in ("Add a read replica for the reporting database",
                     "Set up a MongoDB replica set", "Configure Postgres replication"):
            self.assertTrue(router.classify(text)["critical"], text)

    def test_compare_improve_and_discover(self):
        self.assertEqual(model.intent("Bring the rebuild's look closer to the reference.")
                         ["intent"], "REPLICA_IMPROVE")
        self.assertEqual(model.intent("Compare the rebuilt app with the original")["intent"],
                         "REPLICA_COMPARE")
        self.assertEqual(model.intent("Analyse the screens of https://example.com for a rebuild")
                         ["intent"], "REPLICA_DISCOVER")

    def test_arabic_and_egyptian_requests(self):
        rebuild = u"اعمل نسخة من الموقع ده https://example.com"
        self.assertEqual(model.intent(rebuild)["intent"], "REPLICA_VISUAL")
        working = u"أعد بناء التطبيق ده بنفس الوظائف من الصور"
        self.assertEqual(model.intent(working)["intent"], "REPLICA_FUNCTIONAL")
        question = u"إيه هو الموقع ده https://example.com؟"
        self.assertIsNone(model.intent(question)["intent"])


# --------------------------------------------------------------------------- intake (1-4, 8, 28, 29)

class ReferenceIntake(ReplicaCase):

    def test_url_reference(self):
        project = self.new_project()
        refs = rstore.refs(self.queue(), "r1")
        self.assertEqual(project["request"], REQUEST)          # verbatim
        self.assertEqual([(r["kind"], r["uri"], r["status"]) for r in refs],
                         [("url", "https://example.com/app", "not captured")])
        again = rstore.add_ref(self.queue(), "r1", None, "https://example.com/app")
        self.assertTrue(again["duplicate"])
        with self.assertRaises(QueueError):
            rstore.add_ref(self.queue(), "r1", None, "https://example.com/app?token=abc")

    def test_screenshot_reference_is_copied_with_its_size(self):
        self.new_project()
        item = rstore.add_ref(self.queue(), "r1", None, self.shot("s.png"))
        self.assertEqual((item["kind"], item["meta"]["size"]), ("screenshot", [200, 120]))
        self.assertTrue(item["path"].startswith(self.queue().dir))
        self.assertNotIn(self.project_dir_name(), item["path"])
        self.assertTrue(rstore.add_ref(self.queue(), "r1", None, self.shot("s.png"))["duplicate"])

    def project_dir_name(self):
        return os.path.join(self.project, "")

    def test_video_metadata_without_ffprobe(self):
        self.new_project()
        video = self.write("rec/demo.mp4", "not really a video")
        original = rstore.shutil.which
        rstore.shutil.which = lambda name: None
        try:
            item = rstore.add_ref(self.queue(), "r1", None, video)
        finally:
            rstore.shutil.which = original
        self.assertEqual(item["kind"], "video")
        self.assertIn("ffprobe is not installed", item["limitation"])

    def test_source_folder_is_fingerprinted_in_place(self):
        self.new_project(request="Recreate my existing application using the supplied source code.")
        source = os.path.join(self.tmp, "legacy")
        self.write("package.json", '{"dependencies": {"express": "4"}}', root=source)
        item = rstore.add_ref(self.queue(), "r1", None, source)
        self.assertEqual(item["kind"], "source")
        self.assertIn("package.json", item["meta"]["fingerprint"])
        self.assertEqual(item["path"], os.path.abspath(source))

    def test_missing_reference_is_recorded_not_guessed(self):
        self.new_project()
        item = rstore.add_ref(self.queue(), "r1", "screenshot", os.path.join(self.tmp, "gone.png"))
        self.assertEqual(item["status"], "unavailable")
        self.assertIn("not found", item["limitation"])

    def test_mobile_reference_limitation(self):
        self.new_project(request="Recreate this mobile interface from the screenshots.")
        original = rstore.shutil.which
        rstore.shutil.which = lambda name: None
        try:
            item = rstore.add_ref(self.queue(), "r1", None, self.shot("m.png", width=90, height=160),
                                  platform="android")
        finally:
            rstore.shutil.which = original
        self.assertIn("layout and visible behaviour only", item["limitation"])
        self.assertIn("not installed", item["limitation"])

    def test_missing_browser_automation_is_a_limitation(self):
        self.new_project()
        self.set_config(replica_python=os.path.join(self.tmp, "no-python-here"))
        from mqlib import util
        config = util.effective_config(self.queue().dir)
        result = rstore.capture(self.queue(), "r1", url="https://example.com/app", config=config)
        self.assertFalse(result["ok"])
        self.assertIn("no browser automation", result["error"])
        self.assertIn("no browser automation", rstore.refs(self.queue(), "r1")[0]["limitation"])
        self.assertFalse(rstore.tools(config)["capture"])


# --------------------------------------------------------------------------- modes (5-7)

class Modes(ReplicaCase):

    def test_least_extensive_mode_by_default_and_explicit_choice(self):
        self.assertEqual(self.new_project("Replicate this site: https://example.com")["mode"], "visual")
        self.assertEqual(self.new_project("Replicate this site: https://example.com", "r2",
                                      mode="functional")["mode"], "functional")

    def test_full_needs_evidence_behind_the_screens(self):
        self.new_project("Rebuild the complete application including the backend: https://example.com")
        project = rstore.refresh_mode(self.queue(), "r1")
        self.assertEqual((project["requested_mode"], project["mode"]), ("full", "functional"))
        self.assertIn("source code, an API specification", project["mode_note"])
        rstore.add_ref(self.queue(), "r1", None, self.shot("a.png"))
        rstore.add_ref(self.queue(), "r1", "schema", self.write("db/schema.sql", "create table t(id int);"))
        self.assertEqual(rstore.refresh_mode(self.queue(), "r1")["mode"], "full")

    def test_visual_plan_builds_screens_only(self):
        q = self.standard_project(mode="visual")
        self.inventory()
        tasks = rstore.make_plan(q, "r1")["tasks"]
        self.assertEqual([t["replica"].get("features") for t in tasks if t["id"] != "T1"], [[]])
        self.assertFalse(any("flows" in t["replica"] for t in tasks))

    def test_functional_plan_adds_flows_and_tests(self):
        q = self.standard_project(mode="functional")
        self.inventory()
        tasks = rstore.make_plan(q, "r1")["tasks"]
        self.assertTrue(any(t["replica"].get("flows") == ["U1"] for t in tasks))
        self.assertTrue(any(t["kind"] == "test" for t in tasks))
        self.assertFalse(any(t["milestone"] == "M4" for t in tasks))

    def test_full_plan_adds_the_backend(self):
        q = self.standard_project(mode="full")
        self.inventory()
        tasks = rstore.make_plan(q, "r1")["tasks"]
        backend = [t for t in tasks if t["milestone"] == "M4"]
        self.assertEqual(backend[0]["replica"]["features"], ["F2"])
        self.assertIn("mark anything else as a mock", " ".join(backend[0]["reference"]))


# --------------------------------------------------------------------------- inventory (9-11, 30)

class Inventory(ReplicaCase):

    def test_screen_inventory_and_feature_matrix(self):
        self.standard_project()
        findings = self.inventory()
        self.assertEqual([f for f in findings if f["level"] == "error"], [])
        self.assertIn("unknown", [f["code"] for f in findings])     # F3 is listed as unknown
        status = rstore.status(self.queue(), "r1")
        self.assertEqual((status["screens"], status["features"], status["flows"]), (2, 3, 1))

    def test_evidence_must_be_real(self):
        self.standard_project()
        findings = rstore.apply_inventory(self.queue(), "r1", {
            "screens": [{"id": "S1", "name": "List", "evidence": "OBSERVED", "refs": []},
                        {"id": "S9", "name": "Ghost", "evidence": "OBSERVED", "refs": ["E99"]}],
            "features": [{"id": "F1", "name": "Guess", "evidence": "INFERRED", "requirement": True},
                         {"id": "F2", "name": "Spec", "evidence": "DOCUMENTED", "refs": ["E2"]}]})
        codes = {f["code"] for f in findings if f["level"] == "error"}
        self.assertTrue({"observed-without-reference", "unknown-reference", "inferred-requirement",
                         "documented-without-document"} <= codes, codes)
        with self.assertRaises(QueueError):
            rstore.make_plan(self.queue(), "r1")

    def test_flows_cannot_invent_outcomes(self):
        self.standard_project()
        findings = rstore.apply_inventory(self.queue(), "r1", {
            "screens": [{"id": "S1", "name": "List", "evidence": "OBSERVED", "refs": ["E2"]}],
            "flows": [{"id": "U1", "name": "Checkout", "evidence": "INFERRED", "outcome_claimed": True,
                       "steps": [{"screen": "S7", "action": "pay"}]},
                      {"id": "U2", "name": "Empty", "evidence": "OBSERVED", "refs": ["E2"], "steps": []}]})
        codes = [f["code"] for f in findings if f["level"] == "error"]
        self.assertIn("invented-outcome", codes)
        self.assertIn("unknown-screen", codes)
        self.assertIn("flow-steps", codes)

    def test_only_settled_features_become_requirements(self):
        q = self.standard_project()
        self.inventory()
        plan = rstore.make_plan(q, "r1")
        self.assertEqual(sorted(r["id"] for r in plan["requirements"]), ["R-F1", "R-F2"])
        joined = " ".join(line for t in plan["tasks"] for line in t.get("reference") or [])
        self.assertIn("Feature F3 Export (UNKNOWN)", joined)
        self.assertIn("build it as a clearly labelled mock", joined)


# --------------------------------------------------------------------------- design tokens (12)

class DesignTokens(ReplicaCase):

    def test_tokens_from_the_sample_stylesheet(self):
        with open(os.path.join(SAMPLE, "style.css"), encoding="utf-8") as handle:
            counter = replica_css.from_css(handle.read(), "E1")
        tokens = replica_css.tokens(counter, lambda source: "DOCUMENTED")
        values = {(t["group"], t["value"]) for t in tokens}
        self.assertIn(("color", "#2b6cb0"), values)
        self.assertIn(("font-size", "14px"), values)
        self.assertIn(("radius", "6px"), values)
        self.assertIn(("breakpoint", "768px"), values)
        self.assertTrue(any(t["group"] == "variable" and "--accent" in t["value"] for t in tokens))

    def test_extracted_tokens_keep_manual_ones_and_add_a_palette(self):
        self.new_project()
        q = self.queue()
        rstore.add_ref(q, "r1", None, self.shot("s.png"))
        rstore.add_ref(q, "r1", "html", os.path.join(SAMPLE, "style.css"))
        rstore.apply_inventory(q, "r1", {"design": {"tokens": [
            {"group": "color", "name": "primary", "value": "#123456", "evidence": "USER_CONFIRMED"}]}})
        result = rstore.extract_tokens(q, "r1")
        names = [t["name"] for t in result["tokens"]]
        self.assertIn("primary", names)
        self.assertTrue(any(t["source"] == "auto" for t in result["tokens"]))
        self.assertEqual(result["palettes"][0]["colors"][0]["value"][:1], "#")
        documented = [t for t in result["tokens"] if t.get("source") == "auto"]
        self.assertTrue(all(t["evidence"] == "DOCUMENTED" for t in documented))


# --------------------------------------------------------------------------- planning (13-17)

class PlanningAndQueue(ReplicaCase):

    def approve_and_execute(self, q):
        plan_store.apply(q, "r1", {
            "infrastructure": {"target": "vps", "scenarios": [
                {"id": "vps", "env": env(hosting="vps", os="linux", can_install_services=True,
                                         ram_gb=4, cpu_vcpus=2)}]},
            "decisions": [decision("ADR-1", [option("static", "nginx", style="monolith"),
                                             option("node", "node", "express", style="monolith")],
                                   "static", requirements=["R-F1"])]})
        result = plan_store.approve(q, "r1", confirmed=True)
        self.assertTrue(result["approved"], result)
        return plan_store.execute(q, "r1", confirmed=True)

    def test_the_plan_lives_in_the_planning_engine(self):
        q = self.standard_project()
        self.inventory()
        rstore.make_plan(q, "r1")
        plan = plan_store.load(q, "r1")
        self.assertEqual(sorted(m["id"] for m in plan["milestones"]), ["M2", "M3", "M6"])
        errors = [f for f in plan_model.validate(plan) if f["level"] == "error"]
        self.assertEqual(errors, [])
        # grouped, not one task per element; every implementation task has acceptance
        self.assertLessEqual(len(plan["tasks"]), 5)
        self.assertTrue(all(t.get("acceptance") for t in plan["tasks"]))

    def test_hardware_constraints_come_from_the_planning_engine(self):
        q = self.standard_project()
        self.inventory()
        rstore.make_plan(q, "r1")
        plan_store.apply(q, "r1", {
            "infrastructure": {"target": "shared", "scenarios": [
                {"id": "shared", "env": env(hosting="shared", os="windows", web_server="iis")}]},
            "requirements": [{"id": "R9", "kind": "scalability", "text": "grow", "status": "ASSUMED"}],
            "decisions": [decision("ADR-1", [option("k8s", "aspnet-core", {"id": "kubernetes", "why": ["R9"]},
                                                    style="microservices"),
                                             option("mvc", "aspnet-mvc5", style="monolith")], "k8s")]})
        messages = " ".join(f["message"] for f in plan_model.validate(plan_store.load(q, "r1"))
                            if f["code"] == "incompatible")
        self.assertIn("Kubernetes", messages)

    def test_import_into_the_queue_is_idempotent_and_routed(self):
        q = self.standard_project()
        self.inventory()
        rstore.make_plan(q, "r1")
        first = self.approve_and_execute(q)
        self.assertGreater(len(first["added"]), 0)
        again = plan_store.execute(q, "r1", confirmed=True)
        self.assertEqual(again["added"], [])
        tasks = [t for t in q.tasks() if t["source"] == "plan:r1"]
        self.assertEqual(len(tasks), len(first["added"]))
        prompt = q.get(tasks[1]["id"])["prompt"]
        self.assertIn("Reference (from the replica inventory", prompt)
        self.assertIn("Screen S1 Shipments list (OBSERVED)", prompt)
        self.assertIn("reference image:", prompt)
        for task in tasks:            # every imported task was classified by the router,
            routing = self.routing(task["id"])     # on its work, not on the context blocks
            self.assertIsNotNone(routing["recommendedModel"])
            self.assertNotIn("database design", routing["modelReason"])
        self.assertEqual(plan_model.routing_text(prompt).count("Reference ("), 0)
        self.assertIn("Build screens", plan_model.routing_text(prompt))
        by_feature, by_screen = rstore.queue_tasks(q, "r1")
        self.assertIn("S1", by_screen)
        self.assertEqual(rstore.status(q, "r1")["stage"], "building")

    def test_replica_tasks_may_read_their_references_and_nothing_else_changes(self):
        q = self.standard_project()
        self.inventory()
        rstore.make_plan(q, "r1")
        self.approve_and_execute(q)
        ordinary = self.add("Fix the typo in the README title.")
        from mqlib import pyrunner
        pyrunner.run(self.queue(), {}, quiet=True, max_tasks=1)
        starts = self.events_of("start")
        folder = os.path.join(q.dir, "replica", "r1")
        self.assertEqual(starts[0]["argv"][starts[0]["argv"].index("--add-dir") + 1], folder)
        self.assertEqual(self.task(starts[0]["task"])["status"], "done")
        q.conn.execute("UPDATE tasks SET priority=100 WHERE id=?", (ordinary,))
        q.conn.commit()
        pyrunner.run(self.queue(), {}, quiet=True, max_tasks=1)
        last = self.events_of("start")[-1]
        self.assertEqual(last["task"], ordinary)
        self.assertNotIn("--add-dir", last["argv"])
        # a reference changed after intake is reported
        shot = rstore.ref(q, "r1", "E2")["path"]
        with open(shot, "ab") as handle:
            handle.write(b"tampered")
        self.assertIn("E2", [item["ref"] for item in rstore.status(q, "r1")["limitations"]])

    def test_third_party_reference_gets_the_clean_room_rule(self):
        q = self.standard_project(ownership="third_party")
        self.inventory()
        tasks = rstore.make_plan(q, "r1")["tasks"]
        self.assertIn("Do not copy its logos", tasks[0]["reference"][0])
        q2 = self.queue()
        rstore.configure(q2, "r1", ownership="own")
        tasks = rstore.make_plan(q2, "r1")["tasks"]
        self.assertIn("its own assets may be reused", tasks[0]["reference"][0])

    def test_sample_data_is_reproduced_only_when_the_owner_says_it_is_synthetic(self):
        q = self.standard_project(ownership="own")
        self.inventory()
        rstore.apply_inventory(q, "r1", {"screens": [{"id": "S1", "data": "SH-1 Riyadh-Jeddah"}]})
        lines = " ".join(rstore.make_plan(q, "r1")["tasks"][1]["reference"])
        self.assertIn("Use synthetic data only", lines)
        self.assertNotIn("SH-1 Riyadh-Jeddah", lines)
        rstore.apply_inventory(q, "r1", {"sample_data": {"synthetic": True,
                                                         "evidence": "USER_CONFIRMED"}})
        lines = " ".join(rstore.make_plan(q, "r1")["tasks"][1]["reference"])
        self.assertIn("reproduce the rows listed", lines)
        self.assertIn("sample data: SH-1 Riyadh-Jeddah", lines)
        rstore.configure(q, "r1", ownership="third_party")     # never for third parties
        lines = " ".join(rstore.make_plan(q, "r1")["tasks"][1]["reference"])
        self.assertIn("Use synthetic data only", lines)

    def test_regenerating_replaces_generated_tasks(self):
        q = self.standard_project(mode="functional")
        self.inventory()
        rstore.make_plan(q, "r1")
        rstore.apply_inventory(q, "r1", {"remove": {"flows": ["U1"]}})
        rstore.make_plan(q, "r1")
        titles = [t["title"] for t in plan_store.load(q, "r1")["tasks"]]
        self.assertFalse(any("user flows" in title for title in titles))


# --------------------------------------------------------------------------- comparison (18)

class Comparison(ReplicaCase):

    def compare_project(self):
        self.new_project()
        q = self.queue()
        rstore.add_ref(q, "r1", None, self.shot("ref.png"))
        rstore.apply_inventory(q, "r1", {"screens": [
            {"id": "S1", "name": "List", "evidence": "OBSERVED", "refs": ["E2"]}]})
        return q

    def test_identical_close_and_different_screens(self):
        q = self.compare_project()
        same = rstore.compare(q, "r1", "S1", self.shot("same.png"))
        self.assertEqual(same["score"], 1.0)
        close = rstore.compare(q, "r1", "S1", self.shot("close.png", LIST_SCREEN[:2] + [
            (10, 52, 180, 58, (200, 210, 225))]))
        other = rstore.compare(q, "r1", "S1", self.shot("other.png", DETAIL_SCREEN))
        self.assertGreater(close["score"], other["score"])
        self.assertLess(other["score"], 0.9)
        self.assertGreater(other["whole_image_score"], other["score"])   # background inflates it
        self.assertIn("not the reference background", other["method"])
        self.assertTrue(os.path.isfile(other["diff"]))
        self.assertTrue(other["findings"])
        self.assertEqual(len(rstore.comparisons(q, "r1", latest_only=False)), 3)

    def test_size_difference_and_masks(self):
        q = self.compare_project()
        taller = rstore.compare(q, "r1", "S1", self.shot("tall.png", height=160))
        self.assertTrue(any("sizes differ" in note for note in taller["notes"]))
        self.assertLess(taller["score"], 1.0)
        clock = self.shot("clock.png", LIST_SCREEN + [(150, 2, 40, 14, (0, 0, 0))])
        self.assertLess(rstore.compare(q, "r1", "S1", clock)["score"], 1.0)
        masked = rstore.compare(q, "r1", "S1", clock, masks=[[148, 0, 44, 20]])
        self.assertEqual(masked["score"], 1.0)
        self.assertIn("masked", " ".join(masked["notes"]))

    def test_one_pixel_offsets_are_absorbed_only_when_allowed(self):
        reference = self.shot("a.png")
        moved = self.shot("b.png", [(x + 1, y, w, h, c) for (x, y, w, h, c) in LIST_SCREEN])
        strict = replica_image.compare(reference, moved, shift=0)
        tolerant = replica_image.compare(reference, moved, shift=1)
        self.assertLess(strict["score"], 0.99)
        self.assertEqual(tolerant["score"], 1.0)
        self.assertIn("one pixel away", tolerant["method"])
        self.assertNotIn("one pixel away", strict["method"])
        far = self.shot("c.png", [(x + 6, y, w, h, c) for (x, y, w, h, c) in LIST_SCREEN])
        self.assertLess(replica_image.compare(reference, far, shift=1)["score"], 0.99)

    def test_the_built_in_reader_measures_the_same(self):
        reference = self.shot("a.png")
        candidate = self.shot("b.png", DETAIL_SCREEN)
        with_pillow = replica_image.compare(reference, candidate)
        original = replica_image._pillow
        replica_image._pillow = lambda: None
        try:
            built_in = replica_image.compare(reference, candidate)
        finally:
            replica_image._pillow = original
        self.assertAlmostEqual(with_pillow["score"], built_in["score"], places=3)
        self.assertEqual(built_in["reader"], "built-in PNG reader")
        image = replica_image.read_png(reference)
        self.assertEqual((image.width, image.height, image.pixel(0, 0)), (200, 120, (43, 108, 176)))


# --------------------------------------------------------------------------- parity (19, 30)

class Parity(ReplicaCase):

    def test_only_a_real_passing_test_verifies(self):
        q = self.standard_project()
        self.inventory()
        rstore.record_test(q, "r1", "pass", "real", feature="F1", evidence="pytest -k F1")
        rstore.record_test(q, "r1", "pass", "mock", feature="F2", evidence="against a stub")
        self.assertIsNone(rstore.record_test(q, "r1", "skip", "real", feature="F1"))
        verdicts = {row["feature"]: row["verdict"] for row in rstore.parity(q, "r1")["features"]}
        self.assertEqual(verdicts, {"F1": "verified", "F2": "works against a mock",
                                    "F3": "unknown behaviour"})

    def test_a_comparison_never_verifies_a_feature(self):
        q = self.standard_project()
        self.inventory()
        rstore.compare(q, "r1", "S1", self.shot("same.png"))
        verdicts = {row["feature"]: row["verdict"] for row in rstore.parity(q, "r1")["features"]}
        self.assertNotEqual(verdicts["F1"], "verified")

    def test_junit_import_maps_feature_and_flow_ids(self):
        q = self.standard_project()
        self.inventory()
        report = self.write("report.xml", """<testsuite>
  <testcase classname="tests.test_list" name="test_F1_filter_by_status"/>
  <testcase classname="tests.test_notes" name="test_F2_save_note_mock"><failure>boom</failure></testcase>
  <testcase classname="tests.flows" name="U1 find and open"/>
  <testcase classname="tests.misc" name="test_unrelated"/>
  <testcase classname="tests.misc" name="test_F1_skipped"><skipped/></testcase>
</testsuite>""")
        result = rstore.import_junit(q, "r1", report)
        self.assertEqual(result["recorded"], 3)
        self.assertEqual(len(result["unmatched"]), 1)
        matrix = rstore.parity(q, "r1")
        verdicts = {row["feature"]: row["verdict"] for row in matrix["features"]}
        self.assertEqual(verdicts["F1"], "verified")
        self.assertEqual(verdicts["F2"], "failing")
        self.assertEqual(matrix["flows"][0]["verdict"], "verified")

    def test_report_separates_verified_partial_and_unknown(self):
        q = self.standard_project(ownership="third_party")
        self.inventory()
        rstore.record_test(q, "r1", "pass", "real", feature="F1")
        rstore.apply_inventory(q, "r1", {"features": [{"id": "F2", "state": "mock"}]})
        text = replica_report.render(q, "r1")
        self.assertIn("> " + REQUEST, text)
        self.assertIn("- Verified: F1", text)
        self.assertIn("F2 (mock)", text)
        self.assertIn("F3: Export", text)
        self.assertIn("third-party application", text)
        self.assertNotIn("openapi: 3.0.0", text)     # reference contents never copied


# --------------------------------------------------------------------------- improvement (20-22)

class Improvement(ReplicaCase):

    def setup_loop(self, **config):
        self.set_config(**config)
        self.new_project()
        q = self.queue()
        rstore.add_ref(q, "r1", None, self.shot("ref1.png"))
        rstore.add_ref(q, "r1", None, self.shot("ref2.png", DETAIL_SCREEN))
        rstore.apply_inventory(q, "r1", {"screens": [
            {"id": "S1", "name": "List", "evidence": "OBSERVED", "refs": ["E2"]},
            {"id": "S2", "name": "Details", "evidence": "OBSERVED", "refs": ["E3"]}]})
        return q

    def finish_tasks(self, q, task_ids, status="done"):
        with q.tx():
            for task_id in task_ids:
                q.conn.execute("UPDATE tasks SET status=? WHERE id=?", (status, task_id))

    def config(self):
        from mqlib import util
        return util.effective_config(self.queue().dir)

    def test_rounds_until_acceptance(self):
        q = self.setup_loop()
        rstore.compare(q, "r1", "S1", self.shot("c1.png", DETAIL_SCREEN))
        rstore.compare(q, "r1", "S2", self.shot("c2.png", DETAIL_SCREEN))
        step = rstore.improve(q, "r1", self.config())
        self.assertEqual(step["action"], "queued")
        self.assertEqual(len(step["tasks"]), 1)              # only S1 is below the threshold
        task = q.get(step["tasks"][0])
        self.assertIn("Difference image", task["prompt"])
        self.assertEqual(task["source"], "replica:r1")
        self.assertEqual(rstore.improve(q, "r1", self.config())["action"], "wait")
        self.finish_tasks(q, step["tasks"])
        rstore.compare(q, "r1", "S1", self.shot("c3.png"))
        final = rstore.improve(q, "r1", self.config())
        self.assertEqual(final["action"], "stop")
        self.assertIn("acceptance met", final["reason"])
        self.assertEqual(rstore.iterations(q, "r1")[0]["status"], "done")

    def test_no_acceptance_while_a_screen_is_unmeasured(self):
        q = self.setup_loop()
        rstore.compare(q, "r1", "S2", self.shot("c2.png", DETAIL_SCREEN))
        stop = rstore.improve(q, "r1", self.config())
        self.assertEqual(stop["action"], "stop")
        self.assertIn("cannot conclude: S1 has a reference screenshot but no comparison",
                      stop["reason"])

    def test_iteration_limit(self):
        q = self.setup_loop(replica_max_iterations=1)
        rstore.compare(q, "r1", "S1", self.shot("c1.png", DETAIL_SCREEN))
        step = rstore.improve(q, "r1", self.config())
        self.finish_tasks(q, step["tasks"])
        rstore.compare(q, "r1", "S1", self.shot("c2.png", MID_SCREEN))
        stop = rstore.improve(q, "r1", self.config())
        self.assertEqual(stop["action"], "stop")
        self.assertIn("iteration limit", stop["reason"])

    def test_regression_stops_and_names_the_checkpoint(self):
        q = self.setup_loop()
        rstore.compare(q, "r1", "S1", self.shot("c1.png", MID_SCREEN))
        step = rstore.improve(q, "r1", self.config())
        self.finish_tasks(q, step["tasks"])
        rstore.compare(q, "r1", "S1", self.shot("c2.png", DETAIL_SCREEN))
        stop = rstore.improve(q, "r1", self.config())
        self.assertIn("regressed", stop["reason"])
        self.assertIn("git revert", stop["reason"])

    def test_failed_corrections_and_stalls_stop(self):
        q = self.setup_loop()
        rstore.compare(q, "r1", "S1", self.shot("c1.png", DETAIL_SCREEN))
        step = rstore.improve(q, "r1", self.config())
        self.finish_tasks(q, step["tasks"], "failed")
        stop = rstore.improve(q, "r1", self.config())
        self.assertIn("repeated failures", stop["reason"])

    def test_stall_rule(self):
        q = self.setup_loop(replica_max_iterations=10)
        rstore.compare(q, "r1", "S1", self.shot("c1.png", DETAIL_SCREEN))
        for _ in range(2):
            step = rstore.improve(q, "r1", self.config())
            self.assertEqual(step["action"], "queued")
            self.finish_tasks(q, step["tasks"])
            rstore.compare(q, "r1", "S1", self.shot("cx.png", DETAIL_SCREEN))
        stop = rstore.improve(q, "r1", self.config())
        self.assertIn("stalled", stop["reason"])

    def test_budget_stops_the_loop(self):
        q = self.setup_loop(replica_budget_usd=0.01)
        rstore.compare(q, "r1", "S1", self.shot("c1.png", DETAIL_SCREEN))
        step = rstore.improve(q, "r1", self.config())
        with q.tx():
            q.conn.execute("INSERT INTO runs(task_id, attempt, runner_id, started_at, finished_at, "
                           "outcome, cost_usd) VALUES(?, 1, 'test', '2026-01-01', '2026-01-01', "
                           "'done', 0.05)", (step["tasks"][0],))
        self.finish_tasks(q, step["tasks"])
        rstore.compare(q, "r1", "S1", self.shot("c2.png", MID_SCREEN))
        stop = rstore.improve(q, "r1", self.config())
        self.assertIn("budget reached", stop["reason"])

    def test_state_survives_reopening(self):
        q = self.setup_loop()
        rstore.compare(q, "r1", "S1", self.shot("c1.png", DETAIL_SCREEN))
        step = rstore.improve(q, "r1", self.config())
        q.close()
        reopened = self.queue()
        self.assertEqual(rstore.iterations(reopened, "r1")[0]["status"], "open")
        self.assertEqual(rstore.improve(reopened, "r1", self.config())["action"], "wait")
        self.assertEqual(rstore.status(reopened, "r1")["queue"], {"pending": 1})
        del step


# --------------------------------------------------------------------------- isolation, privacy, compatibility (23-25, 27)

class IsolationAndSafety(ReplicaCase):

    def test_projects_are_isolated(self):
        self.new_project()
        other_root = os.path.join(self.tmp, "other")
        os.makedirs(other_root)
        self.init_git(other_root)
        rstore.create(self.queue(other_root), "r1", "Replicate this site: https://example.org")
        first = rstore.refs(self.queue(), "r1")
        second = rstore.refs(self.queue(other_root), "r1")
        self.assertEqual([r["uri"] for r in first], ["https://example.com/app"])
        self.assertEqual([r["uri"] for r in second], ["https://example.org"])
        self.assertNotEqual(self.queue().dir, self.queue(other_root).dir)

    def test_secrets_are_not_stored(self):
        self.new_project()
        item = rstore.add_ref(self.queue(), "r1", None, self.shot("s.png"),
                              note="login with password=hunter2 and token: abc")
        self.assertNotIn("hunter2", json.dumps(item))
        self.assertNotIn("abc", item["meta"]["note"])

    def test_clean_room_sweep(self):
        q = self.standard_project(ownership="third_party")
        self.write("site/index.html", "<title>Example App</title> see example.com for more")
        found = rstore.sweep(q, "r1", ["Acme"])
        files = [item["file"] for item in found["findings"]]
        self.assertIn(os.path.join("site", "index.html"), files)
        self.assertTrue(found["applies"])
        import shutil
        os.makedirs(os.path.join(self.project, "img"))
        shutil.copyfile(self.shot("list.png"), os.path.join(self.project, "img", "hero.png"))
        issues = {item["file"]: item["issue"] for item in rstore.sweep(q, "r1")["findings"]}
        self.assertIn("identical to reference E2", issues[os.path.join("img", "hero.png")])

    def test_existing_queue_gains_replica_tables_without_change(self):
        task_id = self.add("An ordinary task")
        before = self.task(task_id)
        self.new_project()
        after = self.task(task_id)
        self.assertEqual((before["prompt"], before["status"]), (after["prompt"], after["status"]))
        names = {row[0] for row in self.queue().conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")}
        self.assertTrue({"replica_projects", "replica_refs", "replica_comparisons", "replica_tests",
                         "replica_iterations", "replica_log", "tasks", "plans"} <= names)


class HookIntake(ReplicaCase):

    def hook(self, prompt, session="s-1"):
        payload = json.dumps({"session_id": session, "cwd": self.project, "prompt": prompt,
                              "hook_event_name": "UserPromptSubmit"}).encode("utf-8")
        result = subprocess.run([sys.executable, MQ, "hook-prompt"], input=payload,
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE, cwd=self.tmp)
        self.assertEqual(result.returncode, 0, result.stderr)
        out = result.stdout.decode("utf-8").strip()
        return json.loads(out) if out else None

    def test_a_queued_rebuild_request_becomes_a_replica_project(self):
        answer = self.hook("queue: Replicate this site: https://example.com")
        self.assertEqual(answer["decision"], "block")
        self.assertIn("replica project 'r1'", answer["reason"])
        self.assertEqual(self.queue().tasks(), [])                # not a runnable task
        project = rstore.get(self.queue(), "r1")
        self.assertEqual(project["request"], "Replicate this site: https://example.com")
        again = self.hook("queue: Replicate this site: https://example.com")
        self.assertIn("NOT saved again", again["reason"])
        self.assertEqual(len(rstore.projects(self.queue())), 1)
        ordinary = self.hook("queue: Fix the typo in the README")
        self.assertIn("Queued as task #1", ordinary["reason"])

    def test_intake_can_be_switched_off(self):
        self.set_config(replica_hook_intake=False)
        answer = self.hook("queue: Replicate this site: https://example.com")
        self.assertIn("Queued as task", answer["reason"])
        self.assertEqual(rstore.projects(self.queue()), [])

    def test_unprefixed_prompts_are_not_touched(self):
        self.assertIsNone(self.hook("Replicate this site: https://example.com"))


class ReplicaCli(ReplicaCase):

    def test_command_line_round_trip(self):
        out = self.mq("replica", "intent", "Replicate this site: https://example.com")[1]
        self.assertIn("REPLICA_VISUAL", out)
        self.mq("replica", "new", "--request", REQUEST, "--ownership", "own")
        shot = self.shot("list.png")
        self.assertIn("Recorded E2 screenshot", self.mq("replica", "ref", "r1", shot)[1])
        inventory = json.dumps({"screens": [{"id": "S1", "name": "List", "evidence": "OBSERVED",
                                             "refs": ["E2"]}]})
        self.mq("replica", "analyze", "r1", "--data", inventory)
        code, out, _ = self.mq("replica", "compare", "r1", "S1", self.shot("c.png", DETAIL_SCREEN),
                               check=False)
        self.assertEqual(code, 1)
        self.assertIn("method: pixel comparison", out)
        self.assertEqual(self.mq("replica", "compare", "r1", "S1", shot, check=False)[0], 0)
        status = self.mq_json("replica", "status", "r1")
        self.assertEqual(status["screens"], 1)
        self.assertIn("Replica r1", self.mq("replica", "report", "r1")[1])
        self.assertIn("r1", self.mq("replica", "list")[1])


@unittest.skipUnless(os.environ.get("MQ_TEST_BROWSER", "auto") != "off", "browser tests switched off")
class RealCapture(ReplicaCase):
    """A real browser capture of the sample application (skipped without Playwright)."""

    def setUp(self):
        super().setUp()
        found = rstore.tools(probe=True)
        if not found["capture"]:
            self.skipTest("no browser automation on this machine: %s" % found.get("browser"))

    def test_capture_the_sample_application(self):
        self.new_project(ownership="own")
        url = "file:///" + os.path.join(SAMPLE, "index.html").replace("\\", "/").lstrip("/")
        result = rstore.capture(self.queue(), "r1", url=url, width=1024, height=700)
        self.assertTrue(result["ok"], result)
        refs = {r["id"]: r for r in rstore.refs(self.queue(), "r1")}
        shot = refs[result["screenshot"]]
        self.assertEqual(shot["meta"]["size"], [1024, 700])
        tokens = rstore.extract_tokens(self.queue(), "r1")["tokens"]
        self.assertIn("#2b6cb0", [t["value"] for t in tokens if t["group"] == "color"])
        same = rstore.capture(self.queue(), "r1", url=url, width=1024, height=700)
        self.assertNotIn("changed_since", same)        # the page did not change


if __name__ == "__main__":
    unittest.main()
