"""The controller's final/ and intermediate/ views: placement, rendering and the failure rule."""

import copy
import contextlib
import fcntl
import hashlib
import io
import json
import os
from pathlib import Path
import re
import shutil
import tempfile
import time
import unittest
from unittest import mock

from search_controller import output_views
from search_controller.errors import SearchError
from search_controller.model import EVENT_HANDLERS, apply_event, initial_state
from search_controller.service import Controller
from search_controller.storage import Store
# search_controller puts the plugin root, which holds the research harness, on the import path.
from research_harness import cli as research_cli, output_views as research_views, review_protocol
from research_harness.storage import Store as ResearchStore
from tests.native_reviewer_support import mutate
from tests.search_execution_support import admit_workspace, begin_spec, invoke
from tests.search_fixtures import contract, digest, proposal, provenance, review
from tests.support import WorkspaceTest, make_move

UNKNOTTING_ATTACK_ROOT = Path("/Users/ryshiro/exactory/math-conjectures/202610282209-unknotting-connected-sum/attack")


def observe(actor, assignment):
    return dict(provenance(actor), attestation_id="review-assignment:" + assignment)


def result_review(subject, assignment):
    return {"subject_digest": digest(subject), "claim_digest": digest(subject["claim"]),
            "reviewer": observe("result-reviewer", assignment), "decision": "approve",
            "findings": {key: "Exact checked statement" for key in ("statement", "assumptions", "scope", "dependencies",
                                                                   "policy")}}


def is_lock_free(path):
    descriptor = os.open(str(path), os.O_RDWR | os.O_CREAT, 0o600)
    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        return True
    except OSError:
        return False
    finally:
        os.close(descriptor)


class ControllerViewsTest(unittest.TestCase):
    """A controller root at <workspace>/attack whose events are recorded one by one."""

    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.workspace = Path(temporary.name).resolve() / "record"
        self.root = self.workspace / "attack"
        self.root.mkdir(parents=True)
        self.store = Store(self.root / ".search")
        self.store.initialize(contract(), "objective-000001")
        self.state = initial_state(contract(), "objective-000001")

    def record(self, kind, payload):
        sequence = self.state["revision"] + 1
        request = "event-%d" % sequence
        self.state = apply_event(self.state, {"sequence": sequence, "request_id": request, "kind": kind,
                                              "payload": payload})
        self.store.append(kind, payload, sequence - 1, request)

    def admit(self):
        candidate = proposal()
        self.record("proposal_recorded", {"proposal": candidate, "digest": digest(candidate)})
        approval = review(candidate)
        approval["reviewer"] = observe("reviewer-one", "admission-1")
        self.record("review_recorded", {"proposal_id": "proposal-000001", "review": approval, "digest": digest(approval)})
        self.record("proposal_admitted", {"proposal_id": "proposal-000001"})

    def accept(self, proof=b"A complete proof of every case.\n"):
        claim = copy.deepcopy(self.state["obligations"]["obligation-000001"]["claim"])
        manifest = {"schema_version": 1, "kind": "analytical", "claim_digest": digest(claim),
                    "conclusion": {"outcome": "proof", "dependency_ids": [], "route_bindings": []},
                    "artifacts": [{"path": "attempt/work/proof.md", "digest": self.store.put_artifact(proof),
                                   "role": "proof"}],
                    "dependencies": [], "external_dependencies": [], "verification": None}
        checkpoint = {"schema_version": 1, "kind": "proof", "claim": claim,
                      "origin": {"kind": "external_result", "source": "Pinned proof", "source_digest": "a" * 64,
                                 "statement": claim["statement"], "statement_digest": digest(claim),
                                 "study_digest": "b" * 64},
                      "evidence_digests": [self.store.put_blob(manifest)], "verification_status": "pending",
                      "what_changed": "An exact proof is available", "remaining_obligation_ids": [],
                      "next_hypothesis": "Close the objective", "milestone_id": None}
        self.record("checkpoint_recorded", {"checkpoint": checkpoint, "digest": digest(checkpoint)})
        recorded = self.state["checkpoints"]["checkpoint-000001"]
        acceptance = {"schema_version": 1, "checkpoint_id": recorded["id"], "checkpoint_digest": digest(recorded),
                      "obligation_id": "obligation-000001", "outcome": "proof", "classification": "analytical",
                      "standard": "reviewed", "dependency_ids": [], "route_bindings": [],
                      "review": result_review(recorded, "result-1"),
                      "audit": {"subject_digest": digest(recorded), "dependency_ids": [],
                                "evidence_digests": recorded["evidence_digests"],
                                "provenance": provenance("audit-service")}}
        self.record("result_accepted", {"acceptance": acceptance, "digest": digest(acceptance)})

    def build(self, document=None):
        return output_views.build_views(self.root, self.store, document or self.store.read(), self.state).files

    def read_view(self, relative):
        return json.loads((self.workspace / relative).read_bytes())


class PlacementTests(ControllerViewsTest):
    def test_the_objective_and_an_admitted_proposal_with_its_review_are_final(self):
        self.admit()
        files = self.build()
        objective = json.loads(files["final/math/attack/objective.json"][0])
        self.assertEqual(objective["contract"], contract())
        self.assertEqual(objective["proof_status"], "open")
        admission = json.loads(files["final/math/attack/admissions/proposal-000001.json"][0])
        self.assertEqual(admission["node_id"], "node-000001")
        self.assertEqual([item["id"] for item in admission["reviews"]], ["review-000001"])

    def test_a_proposal_not_admitted_is_intermediate(self):
        candidate = proposal()
        self.record("proposal_recorded", {"proposal": candidate, "digest": digest(candidate)})
        files = self.build()
        self.assertIn("intermediate/math/attack/proposals/proposal-000001.json", files)
        self.assertFalse([path for path in files if "/admissions/" in path])

    def test_an_accepted_result_is_final_with_its_artifacts(self):
        self.admit()
        self.accept()
        files = self.build()
        folder = "final/math/attack/results/acceptance-000001/"
        value = json.loads(files[folder + "acceptance.json"][0])
        self.assertEqual(value["checkpoint"]["id"], "checkpoint-000001")
        [artifact] = value["evidence"][0]["artifacts"]
        self.assertEqual((artifact["path"], artifact["view"]), ("attempt/work/proof.md",
                                                                folder + "artifacts/attempt/work/proof.md"))
        self.assertEqual(files[folder + "artifacts/attempt/work/proof.md"][0], b"A complete proof of every case.\n")
        self.assertIn("intermediate/math/attack/checkpoints/checkpoint-000001.json", files)

    def test_a_large_or_missing_artifact_is_listed_by_digest_without_stopping_the_render(self):
        self.admit()
        self.accept(proof=b"x" * (output_views.COPY_LIMIT_BYTES + 1))
        files = self.build()
        value = json.loads(files["final/math/attack/results/acceptance-000001/acceptance.json"][0])
        [artifact] = value["evidence"][0]["artifacts"]
        self.assertEqual((artifact["view"], artifact["reason"]), (None, "larger than the 1 MiB copy limit"))
        os.unlink(str(self.root / ".search/artifacts" / artifact["digest"]))
        files = self.build()
        value = json.loads(files["final/math/attack/results/acceptance-000001/acceptance.json"][0])
        self.assertTrue(value["evidence"][0]["artifacts"][0]["reason"].startswith("missing: "))

    def test_an_invalidated_result_is_intermediate_with_its_artifacts(self):
        self.admit()
        self.accept()
        self.record("evidence_invalidated", {"acceptance_ids": ["acceptance-000001"], "reason": "Evidence audit failed",
                                             "provenance": provenance("audit-service")})
        files = self.build()
        self.assertFalse([path for path in files if "/results/" in path])
        folder = "intermediate/math/attack/invalidated/acceptance-000001/"
        self.assertEqual(json.loads(files[folder + "acceptance.json"][0])["acceptance"]["status"], "invalidated")
        self.assertIn(folder + "artifacts/attempt/work/proof.md", files)

    def test_the_current_reassessment_is_final_and_earlier_ones_are_history(self):
        self.state["control"]["strategy_refresh"]["assessments"] = [
            {"id": "assessment-00000%d" % number, "predecessor_id": None, "recorded_revision": number,
             "assessment": {"what_changed": "Round %d" % number}, "digest": "c" * 64, "context": {},
             "review": dict(result_review({"claim": {}}, "reassess-%d" % number), subject_digest="c" * 64)}
            for number in (1, 2)]
        files = self.build()
        self.assertIn("final/math/attack/reassessments/assessment-000002.json", files)
        self.assertIn("final/history/math/attack/reassessments/assessment-000001.json", files)
        self.assertNotIn("final/math/attack/reassessments/assessment-000001.json", files)

    def closure_document(self, sequences):
        deliverable = self.store.put_artifact(b"The written proof.\n")
        closure = {"subject": {"acceptance_ids": ["acceptance-000001"], "outcome": "proof",
                               "deliverables": [{"requirement": "Conventional proof", "digest": deliverable}]},
                   "review": result_review({"claim": contract()["original_claim"]}, "closure-1"), "audit": {}}
        self.state["control"]["closure"] = closure
        return closure, {"events": [{"sequence": sequence, "request_id": "complete-%d" % sequence,
                                     "kind": "objective_completed", "payload": {"closure": closure, "digest": "d" * 64}}
                                    for sequence in sequences]}

    def test_a_standing_closure_is_final_with_its_deliverables(self):
        self.admit()
        self.accept()
        _, document = self.closure_document([7])
        self.state["proof_status"] = "proved"
        files = self.build(document)
        value = json.loads(files["final/math/attack/closure/closure.json"][0])
        self.assertEqual(value["deliverables"][0]["view"], "final/math/attack/closure/deliverables/01")
        self.assertEqual(files["final/math/attack/closure/deliverables/01"][0], b"The written proof.\n")

    def test_a_reopened_closure_is_history_and_an_unsupported_one_is_intermediate(self):
        self.admit()
        self.accept()
        _, document = self.closure_document([7, 9])
        self.state["proof_status"] = "open"
        files = self.build(document)
        self.assertIn("final/history/math/attack/closure/closure-r000007/closure.json", files)
        self.assertIn("final/history/math/attack/closure/closure-r000009/closure.json", files)
        self.state["acceptances"]["acceptance-000001"]["status"] = "invalidated"
        files = self.build(document)
        self.assertIn("intermediate/math/attack/invalidated/closure-r000009/closure.json", files)
        self.assertFalse([path for path in files if "/closure/" in path])

    def test_replans_retreats_amendments_and_runs_are_intermediate(self):
        self.admit()
        replan = {"route_orders": [], "progress_acceptance_ids": [], "reason": "Try the other route first"}
        self.record("replan_recorded", replan)
        self.state["control"]["retreats"] = [{"node_id": "node-000001", "criterion": {"kind": "move_limit"}}]
        amendment = {"subject": {"node_id": "node-000001"}, "review": result_review({"claim": {}}, "amend-1"),
                     "digest": "e" * 64}
        self.state["service"]["computation_amendments"]["node-000001"] = amendment
        self.state["service"]["foundation_amendments"]["f" * 64] = amendment
        self.state["service"]["foundation_selection"]["node-000001"] = "f" * 64
        stdout = self.store.put_artifact(b"checked 11 cases\n")
        stderr = self.store.put_artifact(b"e" * (output_views.COPY_LIMIT_BYTES + 1))
        self.state["runs"]["run-000001"] = {
            "id": "run-000001", "node_id": "node-000001", "status": "terminal", "outputs": [],
            "spec_digest": self.store.put_blob({"kind": "command"}),
            "result_digest": self.store.put_blob({"commands": [{"stdout_digest": stdout, "stderr_digest": stderr}]})}
        self.state["service"]["run_interpretations"]["run-000001"] = {"interpretation": {"run_id": "run-000001"},
                                                                       "digest": "0" * 64}
        files = self.build()
        base = "intermediate/math/attack/"
        self.assertEqual(json.loads(files[base + "replans/replan-r000004.json"][0])["replan"], replan)
        self.assertIn(base + "retreats/retreat-000001.json", files)
        self.assertIn(base + "amendments/computation-node-000001.json", files)
        self.assertTrue(json.loads(files[base + "amendments/foundation-" + "f" * 64 + ".json"][0])["selected"])
        self.assertEqual(files[base + "runs/run-000001/0.stdout"][0], b"checked 11 cases\n")
        self.assertNotIn(base + "runs/run-000001/0.stderr", files)
        run = json.loads(files[base + "runs/run-000001/run.json"][0])
        self.assertEqual([item["view"] for item in run["streams"]], [base + "runs/run-000001/0.stdout", None])
        self.assertIn(base + "runs/run-000001/interpretation.json", files)

    def test_each_node_has_an_index_of_its_native_outputs_by_path_and_digest(self):
        self.admit()
        workspace = self.root / "attempt"
        (workspace / "deterministic/check").mkdir(parents=True)
        (workspace / "units/1").mkdir(parents=True)
        (workspace / "journal.jsonl").write_text(json.dumps(make_move(1)) + "\n" + json.dumps(make_move(2)) + "\n")
        (workspace / "deterministic/check/result.json").write_text("{}\n")
        (workspace / "deterministic/check/check.sh").write_text("exit 0\n")
        (workspace / "units/1/draft.md").write_text("Draft\n")
        (workspace / "units/FINISHED.json").write_text("{}\n")
        before = sorted(path for path in workspace.rglob("*"))
        files = self.build()
        index = json.loads(files["intermediate/math/attack/nodes/attempt/index.json"][0])
        self.assertEqual([move["move"] for move in index["journal"]["moves"]], [1, 2])
        self.assertEqual(index["deterministic"][0]["files"][0]["path"], "attack/attempt/deterministic/check/result.json")
        self.assertEqual([item["path"] for item in index["units"]], ["attack/attempt/units/1/draft.md"])
        self.assertEqual(index["finish"]["path"], "attack/attempt/units/FINISHED.json")
        self.assertEqual(sorted(path for path in workspace.rglob("*")), before)

    def test_the_final_index_lists_each_adopted_native_review_with_its_assignment(self):
        self.admit()
        self.accept()
        output_views.render_views(self.root, self.store)
        index = self.read_view("final/math/attack/index.json")
        self.assertEqual([(item["assignment_id"], item["adopting_record"]["kind"], item["adopting_record"]["id"])
                          for item in index["adoptions"]],
                         [("admission-1", "admission_review", "review-000001"), ("result-1", "acceptance",
                                                                                 "acceptance-000001")])
        self.assertEqual(index["adoptions"][1]["adopting_record"]["path"],
                         "final/math/attack/results/acceptance-000001/acceptance.json")

    def test_an_invalidated_result_moves_from_final_to_intermediate_on_disk(self):
        self.admit()
        self.accept()
        output_views.render_views(self.root, self.store)
        accepted = self.workspace / "final/math/attack/results/acceptance-000001"
        self.assertTrue((accepted / "artifacts/attempt/work/proof.md").is_file())
        self.record("evidence_invalidated", {"acceptance_ids": ["acceptance-000001"], "reason": "Evidence audit failed",
                                             "provenance": provenance("audit-service")})
        output_views.render_views(self.root, self.store)
        self.assertFalse((self.workspace / "final/math/attack/results").exists())
        moved = self.workspace / "intermediate/math/attack/invalidated/acceptance-000001"
        self.assertEqual((moved / "artifacts/attempt/work/proof.md").read_bytes(), b"A complete proof of every case.\n")
        self.assertEqual(self.read_view("final/math/attack/index.json")["adoptions"][1]["adopting_record"]["status"],
                         "invalidated")


class RegistryTests(unittest.TestCase):
    def test_every_inner_operation_kind_has_a_placement(self):
        self.assertEqual(sorted(set(EVENT_HANDLERS) - set(output_views.OPERATION_GROUPS)), [])
        self.assertEqual(sorted(set(output_views.OPERATION_GROUPS) - set(EVENT_HANDLERS)), [])

    def test_every_state_collection_has_a_placement(self):
        initial = output_views.list_state_paths(initial_state(contract(), "objective-000001"))
        package = Path(output_views.__file__).parent
        written = set()
        for path in package.glob("*.py"):
            for group, name in re.findall(r'\["(service|control)"\](?:\[|\.setdefault\(|\.get\()"([a-z_]+)"',
                                          path.read_text()):
                written.add(group + "." + name)
        # Controller.status adds this member to its answer; replay never records it.
        written.discard("service.pending_effect_ids")
        self.assertEqual(sorted((initial | written) - set(output_views.STATE_GROUPS)), [])
        self.assertEqual(sorted(set(output_views.STATE_GROUPS) - initial - written), [])

    def test_every_group_is_a_view_group_and_every_view_group_is_used(self):
        names = [name for name, _ in output_views.VIEW_GROUPS]
        used = set(output_views.STATE_GROUPS.values()) | set(output_views.OPERATION_GROUPS.values())
        self.assertEqual(used - {None}, set(names))

    def test_an_unregistered_state_collection_stops_the_render(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "attack"
            store = Store(root / ".search")
            store.initialize(contract(), "objective-000001")
            state = initial_state(contract(), "objective-000001")
            state["service"]["new_collection"] = {}
            with self.assertRaises(SearchError) as raised:
                output_views.build_views(root, store, store.read(), state)
            self.assertEqual(raised.exception.code, "output_views_unregistered")


class RenderTests(unittest.TestCase):
    """A controller initialized through its public command, rendered after every command."""

    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.workspace = Path(temporary.name).resolve() / "record"
        self.root = self.workspace / "attack"
        self.root.mkdir(parents=True)
        self.controller = Controller(self.root)
        self.command("init", {"contract": contract()}, "init")
        self.marker = self.root / ".search" / output_views.MARKER_NAME

    def command(self, name, spec, request):
        revision = self.controller.status()["revision"] if name != "init" else 0
        stderr = io.StringIO()
        with contextlib.redirect_stderr(stderr):
            result = self.controller.command(name, spec, revision, request)
        return result, stderr.getvalue()

    def count_writes(self, function):
        written = []
        original = output_views.replace_bytes
        def record(path, data):
            if path.name != output_views.MARKER_NAME:
                written.append(path.relative_to(self.workspace).as_posix())
            return original(path, data)
        with mock.patch.object(output_views, "replace_bytes", side_effect=record):
            function()
        return written

    def test_a_command_renders_the_views_and_its_marker(self):
        self.assertTrue((self.workspace / "final/math/attack/objective.json").is_file())
        self.assertTrue((self.workspace / "intermediate/math/attack/index.json").is_file())
        marker = json.loads(self.marker.read_bytes())
        document = self.controller.store.read()
        self.assertEqual((marker["revision"], marker["event_digest"]), (1, digest(document["events"][-1])))
        self.assertEqual(marker["plugin_version"], json.loads(
            (Path(output_views.__file__).resolve().parents[4] / ".claude-plugin/plugin.json").read_text())["version"])
        entry = marker["files"]["final/math/attack/objective.json"]
        data = (self.workspace / "final/math/attack/objective.json").read_bytes()
        self.assertEqual((entry["size"], entry["digest"]), (len(data), hashlib.sha256(data).hexdigest()))

    def test_a_second_render_writes_nothing(self):
        written = self.count_writes(lambda: self.command("render", {}, "render-1"))
        self.assertEqual(written, [])
        self.assertEqual(json.loads(self.marker.read_bytes())["revision"], 2)

    def test_only_files_whose_bytes_differ_are_written(self):
        objective = self.workspace / "final/math/attack/objective.json"
        original = objective.read_bytes()
        objective.write_bytes(original + b"edited")
        index = self.workspace / "intermediate/math/attack/index.json"
        later = time.time() + 5
        os.utime(str(index), (later, later))
        written = self.count_writes(lambda: self.command("render", {}, "render-1"))
        self.assertEqual(written, ["final/math/attack/objective.json"])
        self.assertEqual(objective.read_bytes(), original)
        self.assertEqual(json.loads(self.marker.read_bytes())["files"]["intermediate/math/attack/index.json"]
                         ["mtime_ns"], index.stat().st_mtime_ns)
        written = self.count_writes(lambda: self.command("pause", {"reason": "Owner asked to pause"}, "pause-1"))
        self.assertEqual(written, ["final/math/attack/objective.json"])
        self.assertEqual(json.loads(objective.read_bytes())["execution_status"], "paused")

    def test_the_marker_identity_decides_the_fast_skip(self):
        objective = self.workspace / "final/math/attack/objective.json"
        original = objective.read_bytes()
        objective.write_bytes(b"edited")
        self.assertIsNone(output_views.render_views(self.root, self.controller.store))
        self.assertEqual(objective.read_bytes(), b"edited")
        for field, value in (("event_digest", "0" * 64), ("plugin_version", "0.0.0")):
            objective.write_bytes(b"edited")
            marker = json.loads(self.marker.read_bytes())
            self.marker.write_bytes(json.dumps(dict(marker, **{field: value})).encode())
            self.assertIsNotNone(output_views.render_views(self.root, self.controller.store))
            self.assertEqual(objective.read_bytes(), original)

    def test_the_writer_lock_is_free_while_the_views_render(self):
        observed = []
        original = output_views.build_views
        def build(*args):
            observed.append(is_lock_free(self.root / ".search/tree.lock"))
            return original(*args)
        with mock.patch.object(output_views, "build_views", side_effect=build):
            self.command("pause", {"reason": "Owner asked to pause"}, "pause-1")
        self.assertEqual(observed, [True])

    def test_a_failed_render_keeps_the_response_and_reports_an_advisory(self):
        with mock.patch.object(output_views, "build_views", side_effect=OSError("No space left on device")):
            response, stderr = self.command("pause", {"reason": "Owner asked to pause"}, "pause-1")
        self.assertEqual(response, {"objective_id": "objective-000001", "revision": 2, "proof_status": "open",
                                    "command": "pause"})
        self.assertEqual(len(stderr.splitlines()), 1)
        self.assertIn("No space left on device", stderr)
        status = self.controller.command("status", {}, None, None)
        action = self.controller.command("next", {}, None, None)
        self.assertEqual(action["kind"], "paused")
        for value in (status, action):
            [advisory] = value["advisories"]
            self.assertEqual((advisory["code"], advisory["action"], advisory["reason"], advisory["rendered_revision"],
                              advisory["revision"]), ("output_views_stale", "exactory-math search render",
                                                      "render_failed", 1, 2))
            self.assertIn("No space left on device", advisory["error"])
            self.assertEqual(advisory["path"], str(self.workspace / "final/math/attack"))
        self.command("render", {}, "render-1")
        self.assertNotIn("advisories", self.controller.command("status", {}, None, None))
        self.assertNotIn("advisories", self.controller.command("next", {}, None, None))
        self.assertEqual(json.loads((self.workspace / "final/math/attack/objective.json").read_bytes())
                         ["execution_status"], "paused")

    def test_every_advisory_names_its_reason_with_the_research_fields(self):
        def advise(reason, rendered, revision=1):
            return [{"code": "output_views_stale", "action": "exactory-math search render", "reason": reason,
                     "path": None, "error": None, "rendered_revision": rendered, "revision": revision}]

        def read_advisories():
            return [self.controller.command(name, {}, None, None).get("advisories") for name in ("status", "next")]

        self.assertEqual(read_advisories(), [None, None])
        marker = json.loads(self.marker.read_bytes())
        for field, value, reason, rendered in (("plugin_version", "0.0.0", "plugin_changed", 1),
                                               ("event_digest", "0" * 64, "history_changed", 1),
                                               ("revision", 2, "history_changed", 2)):
            self.marker.write_bytes(json.dumps(dict(marker, **{field: value})).encode())
            self.assertEqual(read_advisories(), [advise(reason, rendered)] * 2, field)
        self.marker.unlink()
        self.assertEqual(read_advisories(), [advise("not_rendered", 0)] * 2)
        self.marker.write_bytes(json.dumps(marker).encode())
        with mock.patch.object(output_views, "render_views", return_value=None):
            self.command("pause", {"reason": "Owner asked to pause"}, "pause-1")
        self.assertEqual(read_advisories(), [advise("behind", 1, 2)] * 2)

    def test_a_controller_root_named_deliveries_renders_nothing(self):
        # Its views would share final/math/deliveries/ with the research harness's views of the native deliveries.
        workspace = self.workspace.parent / "reserved"
        (workspace / "deliveries").mkdir(parents=True)
        controller = Controller(workspace / "deliveries")
        stderr = io.StringIO()
        with contextlib.redirect_stderr(stderr):
            controller.command("init", {"contract": contract()}, 0, "init")
            controller.command("render", {}, 1, "render-1")
        self.assertEqual(stderr.getvalue(), "")
        self.assertFalse((workspace / "final").exists() or (workspace / "intermediate").exists())
        self.assertFalse((controller.root / ".search" / output_views.MARKER_NAME).exists())
        expected = [{"code": "output_views_stale", "action": "exactory-math search render",
                     "reason": "reserved_controller_root_name", "path": str(controller.root), "error": None,
                     "rendered_revision": None, "revision": 2}]
        self.assertEqual(controller.command("status", {}, None, None)["advisories"], expected)
        self.assertEqual(controller.command("next", {}, None, None)["advisories"], expected)

    def test_a_foreign_file_in_the_way_stops_the_render_until_it_moves(self):
        workspace = self.workspace.parent / "other"
        root = workspace / "attack"
        foreign = workspace / "final/math/attack/objective.json"
        foreign.parent.mkdir(parents=True)
        foreign.write_text("notes\n")
        stray = workspace / "final/math/attack/notes.txt"
        stray.write_text("my notes\n")
        root.mkdir()
        controller = Controller(root)
        stderr = io.StringIO()
        with contextlib.redirect_stderr(stderr):
            controller.command("init", {"contract": contract()}, 0, "init")
        self.assertIn("output_view_conflict", stderr.getvalue())
        [advisory] = controller.command("status", {}, None, None)["advisories"]
        self.assertEqual(advisory["path"], str(foreign))
        self.assertEqual(foreign.read_text(), "notes\n")
        foreign.unlink()
        stderr = io.StringIO()
        with contextlib.redirect_stderr(stderr):
            controller.command("render", {}, 1, "render-1")
        self.assertEqual(stderr.getvalue(), "")
        self.assertTrue(foreign.is_file())
        self.assertEqual(json.loads((workspace / "final/math/attack/index.json").read_bytes())["unowned"],
                         ["final/math/attack/notes.txt"])
        self.assertEqual(stray.read_text(), "my notes\n")

    def test_a_command_renders_from_the_state_it_already_replayed(self):
        with mock.patch.object(output_views, "replay", wraps=output_views.replay) as replayed:
            self.command("pause", {"reason": "Owner asked to pause"}, "pause-1")
        self.assertEqual(replayed.call_count, 0)
        self.assertEqual(json.loads(self.marker.read_bytes())["revision"], 2)

    def test_a_replayed_request_renders_what_its_first_run_left_unrendered(self):
        with mock.patch.object(output_views, "build_views", side_effect=OSError("interrupted")):
            self.command("pause", {"reason": "Owner asked to pause"}, "pause-1")
        self.assertEqual(json.loads(self.marker.read_bytes())["revision"], 1)
        stderr = io.StringIO()
        with contextlib.redirect_stderr(stderr):
            response = self.controller.command("pause", {"reason": "Owner asked to pause"}, 1, "pause-1")
        self.assertEqual((response["revision"], stderr.getvalue()), (2, ""))
        self.assertEqual(json.loads(self.marker.read_bytes())["revision"], 2)

    def test_a_read_only_command_renders_nothing(self):
        written = self.count_writes(lambda: self.controller.command("status", {}, None, None))
        self.assertEqual(written, [])
        self.assertEqual(json.loads(self.marker.read_bytes())["revision"], 1)


class NativeRenderTests(WorkspaceTest):
    def test_a_native_journal_add_renders_its_node_index_outside_every_lock(self):
        controller = admit_workspace(self.attack_root)
        invoke(controller, "begin", begin_spec())
        observed = []
        original = output_views.build_views
        def build(*args):
            observed.append((is_lock_free(self.attack_root / ".search/tree.lock"),
                             is_lock_free(self.attack_root / ".search/journal-node-000001.lock")))
            return original(*args)
        with mock.patch.object(output_views, "build_views", side_effect=build):
            status, out, err = self.run_cli("journal", "add", self.slug, "--json", json.dumps(make_move(1)))
        self.assertEqual((status, err), (0, ""))
        self.assertEqual(observed, [(True, True)])
        workspace = self.attack_root.parent
        index = json.loads((workspace / "intermediate/math/attack/nodes/sample/index.json").read_bytes())
        self.assertEqual([move["move"] for move in index["journal"]["moves"]], [1])
        marker = json.loads((self.attack_root / ".search" / output_views.MARKER_NAME).read_bytes())
        self.assertEqual(marker["revision"], controller.status()["revision"])


def read_owned_files(marker):
    return set(json.loads(marker.read_bytes())["files"])


def list_view_files(workspace):
    """{path from the workspace root: (bytes, inode, modification time)} of every file in final/ and intermediate/."""
    found = {}
    for folder in ("final", "intermediate"):
        for parent, _, names in os.walk(str(workspace / folder)):
            for name in names:
                path = Path(parent) / name
                info = path.stat()
                found[path.relative_to(workspace).as_posix()] = (path.read_bytes(), info.st_ino, info.st_mtime_ns)
    return found


def check_shared_workspace_root(test, workspace, rel):
    """Both renderers' files fill final/ and intermediate/ of one workspace root: each file has one owner, each index
    lists only its owner's files, and no file is unowned. Returns the research and the controller files."""
    research = read_owned_files(workspace / ".exactory/output-views.json")
    controller = read_owned_files(workspace / rel / ".search" / output_views.MARKER_NAME)
    test.assertEqual(research & controller, set())
    test.assertEqual(research | controller, set(list_view_files(workspace)))
    for folder in ("final", "intermediate"):
        index = json.loads((workspace / folder / "index.json").read_bytes())
        test.assertEqual(index["unowned"], [], folder)
        for entry in index["files"]:
            test.assertIn(entry["type"], ("view", "controller"), entry)
            test.assertIn(folder + "/" + entry["path"], controller if entry["type"] == "controller" else research)
        own = json.loads((workspace / folder / "math" / rel / "index.json").read_bytes())
        test.assertEqual(own["unowned"], [], folder)
        test.assertLessEqual({entry["path"] for entry in own["files"]}, controller, folder)
    test.assertIn({"path": "math/" + rel + "/index.json", "type": "controller"},
                  json.loads((workspace / "final/index.json").read_bytes())["files"])
    return research, controller


def find_series_adoptions(workspace):
    """{assignment id: the controller adoptions in its turn} over every reviewer series of the research store."""
    adopted = {}
    for path in sorted((workspace / "intermediate/reviews").glob("*/*/series.json")):
        for turn in json.loads(path.read_bytes())["turns"]:
            adopted[turn["assignment_id"]] = [item for item in turn["adopted_by"] if item["kind"] == "controller"]
    return adopted


def check_series_adoptions(test, workspace, rel):
    """Every adoption in the controller's final index appears in the series of the assignment it names."""
    index = "final/math/" + rel + "/index.json"
    expected = {}
    for adoption in json.loads((workspace / index).read_bytes())["adoptions"]:
        expected.setdefault(adoption["assignment_id"], []).append(
            {"kind": "controller", "index": index, "review_id": adoption["review_id"],
             "adopting_record": adoption["adopting_record"]})
    adopted = find_series_adoptions(workspace)
    test.assertEqual({assignment: adopted.get(assignment) for assignment in expected}, expected)
    return expected


def check_second_renders_write_nothing(test, workspace, controller):
    """A controller command that builds the whole controller model, then a full research render, write no view."""
    before = list_view_files(workspace)
    stderr = io.StringIO()
    with contextlib.redirect_stderr(stderr):
        controller.command("render", {}, controller.status()["revision"], "render-again")
        research_views.render_all(ResearchStore(workspace))
    test.assertEqual(stderr.getvalue(), "")
    test.assertEqual(list_view_files(workspace), before)


def build_route(identifier):
    return {"id": identifier, "adapter": "openai_responses_v1", "model": "fixture-model",
            "endpoint": "https://api.openai.com/v1/responses",
            "configuration": {"max_output_tokens": 10000, "timeout_seconds": 5}}


class SharedWorkspaceRootTests(WorkspaceTest):
    """A problem record's layout: the research store at the workspace root and the controller root attack/ in it."""

    def research(self, command, payload, request):
        workspace = self.attack_root.parent
        path = workspace / "work" / (request + ".json")
        path.parent.mkdir(exist_ok=True)
        path.write_text(json.dumps(payload))
        revision = ResearchStore(workspace).revision if (workspace / ".exactory/research.sqlite3").is_file() else 0
        stdout, stderr = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            research_cli.main([command, "--workspace", str(workspace), "--file", str(path), "--expected-revision",
                               str(revision), "--request-id", request])
        self.assertEqual(stderr.getvalue(), "")
        return json.loads(stdout.getvalue())

    def test_both_renderers_share_one_workspace_root(self):
        workspace = self.attack_root.parent
        self.research("problem-init", {"slug": "fixture-problem", "area": "math.CO", "field": "Fixture propositions",
                                       "authors": ["native-fixture-author"]}, "problem-init")
        stderr = io.StringIO()
        with contextlib.redirect_stderr(stderr):
            controller = admit_workspace(self.attack_root, review_workspace="..")
        self.assertEqual(stderr.getvalue(), "")
        self.research("review-route", build_route("second-route"), "second-route")
        research, own = check_shared_workspace_root(self, workspace, "attack")
        self.assertIn("intermediate/reviews/routes/second-route/route.json", research)
        self.assertIn("final/math/attack/admissions/proposal-000001.json", own)
        self.assertIn("intermediate/math/attack/nodes/sample/index.json", own)
        attestation = controller.status()["reviews"]["review-000001"]["record"]["reviewer"]["attestation_id"]
        assignment = attestation[len("review-assignment:"):]
        [adoption] = check_series_adoptions(self, workspace, "attack")[assignment]
        self.assertEqual(adoption["adopting_record"], {"kind": "admission_review", "id": "review-000001",
                                                       "status": "current",
                                                       "path": "final/math/attack/admissions/proposal-000001.json"})
        check_second_renders_write_nothing(self, workspace, controller)
        # Each harness reports views that are behind its records with the same fields; the research entry also
        # carries its explanation.
        with mock.patch.object(output_views, "render_views", return_value=None):
            controller.command("pause", {"reason": "Owner asked to pause"}, controller.status()["revision"], "pause-1")
        store = ResearchStore(workspace)
        mutate(store, review_protocol.record_route, build_route("third-route"))
        [research_advisory] = research_cli.status_report(store)["advisories"]
        [controller_advisory] = controller.command("status", {}, None, None)["advisories"]
        self.assertEqual(set(research_advisory) - {"explanation"}, set(controller_advisory))
        for advisory in (research_advisory, controller_advisory):
            self.assertEqual((advisory["code"], advisory["reason"], advisory["path"], advisory["error"]),
                             ("output_views_stale", "behind", None, None))
            self.assertLess(advisory["rendered_revision"], advisory["revision"])


class AcceptanceCheckTests(unittest.TestCase):
    """A local check on a copy of a real record; it skips where the record is absent."""

    @unittest.skipUnless(UNKNOTTING_ATTACK_ROOT.is_dir(), "the unknotting record is not on this machine")
    def test_a_copy_of_the_unknotting_record_renders(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "record" / "attack"
            shutil.copytree(str(UNKNOTTING_ATTACK_ROOT), str(root), symlinks=True)
            store = Controller(root).store
            started = time.monotonic()
            output_views.render_views(root, store)
            first = time.monotonic() - started
            started = time.monotonic()
            self.assertIsNone(output_views.render_views(root, store))
            skipped = time.monotonic() - started
            index = json.loads((root.parent / "final/math/attack/index.json").read_bytes())
            self.assertEqual(len(index["adoptions"]), 10)
            print("\nunknotting copy: first render %.3f s, skipped render %.4f s, %d final files"
                  % (first, skipped, len(index["files"])))

    @unittest.skipUnless((UNKNOTTING_ATTACK_ROOT.parent / ".exactory/research.sqlite3").is_file(),
                         "the unknotting record is not on this machine")
    def test_a_copy_of_the_unknotting_record_renders_both_views_side_by_side(self):
        with tempfile.TemporaryDirectory() as temporary:
            workspace = Path(temporary).resolve() / "record"
            shutil.copytree(str(UNKNOTTING_ATTACK_ROOT), str(workspace / "attack"), symlinks=True)
            (workspace / ".exactory").mkdir()
            shutil.copy2(str(UNKNOTTING_ATTACK_ROOT.parent / ".exactory/research.sqlite3"),
                         str(workspace / ".exactory/research.sqlite3"))
            objects = UNKNOTTING_ATTACK_ROOT.parent / "research/sources/objects"
            if objects.is_dir():
                shutil.copytree(str(objects), str(workspace / "research/sources/objects"))
            controller = Controller(workspace / "attack")
            stderr = io.StringIO()
            started = time.monotonic()
            with contextlib.redirect_stderr(stderr):
                output_views.render_after_command(controller)
            elapsed = time.monotonic() - started
            self.assertEqual(stderr.getvalue(), "")
            _, own = check_shared_workspace_root(self, workspace, "attack")
            self.assertTrue([path for path in own if path.startswith("final/history/math/attack/")])
            adoptions = check_series_adoptions(self, workspace, "attack")
            check_second_renders_write_nothing(self, workspace, controller)
            print("\nunknotting copy, both renderers: first render %.3f s, %d adopted assignments"
                  % (elapsed, len(adoptions)))


if __name__ == "__main__":
    unittest.main()
