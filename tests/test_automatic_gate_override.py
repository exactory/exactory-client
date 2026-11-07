"""Automatic continuations use harness observations, never caller classifications."""

import ast
import importlib.util
import json
from pathlib import Path
import shutil
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from research_harness import cli, integration, review_protocol
from research_harness.errors import ResearchError
from research_harness.gates import gate_state
from research_harness.review_series import run_series
from research_harness.storage import Store
import test_review_continuation as continuation_fixtures
from development_fixtures import DevelopmentCase
from integration_fixtures import observed_candidate


ROOT = Path(__file__).resolve().parents[1]


class StopClassificationTests(unittest.TestCase):
    def test_auto_pass_example_contains_only_the_stop_identity(self):
        args = cli.build_parser().parse_args(["example", "gate-auto-pass"])
        self.assertEqual(cli.run(args), {"stop_id": "SERIES_STOP_ID"})

    def policy(self):
        self.assertIsNotNone(importlib.util.find_spec("research_harness.stop_classes"),
                             "The shared stop policy is missing")
        from research_harness import stop_classes
        return stop_classes

    def test_disjoint_lists_name_existing_plugin_codes(self):
        policy = self.policy()
        self.assertFalse(policy.WAITING_CODES & policy.HARNESS_DEFECT_CODES)
        # The allowlists cannot vouch for themselves. Inspect executable plugin
        # sources, excluding the policy, test fixtures, and generated copies.
        emitted = set()
        paths = list((ROOT / "research_harness").glob("*.py"))
        paths += list((ROOT / "skills/math-solver/harness/search_controller").glob("*.py"))
        for path in paths:
            if path.name == "stop_classes.py":
                continue
            for node in ast.walk(ast.parse(path.read_text())):
                values = []
                if isinstance(node, ast.Call):
                    name = getattr(node.func, "id", getattr(node.func, "attr", ""))
                    if name in ("ResearchError", "SearchError"):
                        values.extend(node.args[:1])
                    elif name == "_describe_stop":
                        values.extend(node.args[1:2])
                    elif name == "require":
                        values.extend(node.args[2:3])
                    values.extend(item.value for item in node.keywords if item.arg == "code")
                elif isinstance(node, ast.Assign) and isinstance(node.value, ast.Dict):
                    if any(isinstance(target, ast.Name) and target.id == "OUTCOME_CODES" for target in node.targets):
                        values.extend(node.value.values)
                emitted.update(value.value for value in values
                               if isinstance(value, ast.Constant) and isinstance(value.value, str))
        self.assertLessEqual(policy.WAITING_CODES | policy.HARNESS_DEFECT_CODES, emitted)

    def test_material_unknown_and_budget_codes_are_decisions(self):
        policy = self.policy()
        for code in ("review_input_too_large", "review_format_unsupported", "review_output_over_cap",
                     "review_failure_unknown", "review_transport_failure", "review_timeout",
                     "review_budget_exhausted", "resource_budget_exhausted", "new_unrecognized_code"):
            with self.subTest(code=code):
                self.assertEqual(policy.classify_stop(code), "decision")
        self.assertEqual(policy.classify_stop("review_series_no_progress"), "harness_defect")
        for code in ("store_busy", "rate_limited", "review_provider_unavailable", "review_capacity_wait",
                     "review_invocation_pending", "provider_state_capacity", "lock_timeout"):
            with self.subTest(code=code):
                self.assertEqual(policy.classify_stop(code), "waiting")


class RecordedSeriesStopTests(unittest.TestCase):
    def setUp(self):
        self.fixture = continuation_fixtures.ContinuationTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.case = self.fixture.case

    def stop_series(self):
        # A genuine assignment and planner dispatch an operation that fails to
        # advance its review. This reproduces the driver's harness-only defect.
        def unrelated_change(store, payload, **identity):
            return store.mutate("fixture.unrelated", {},
                lambda tx: tx.put("unrelated", "entry", {"revision": identity["expected_revision"]}), **identity)
        steps = []
        with patch.dict(cli.OPERATIONS, {"review-run": unrelated_change}):
            result = run_series(self.case.store, self.fixture.assignment["id"], emit=steps.append)
        self.assertEqual(result, 3, steps)
        self.assertEqual(steps[-1]["code"], "review_series_no_progress")
        return steps[-1]

    def test_unchanged_repeat_records_the_harness_stop_and_ended_schedule(self):
        terminal = self.stop_series()
        self.assertIn("stop_id", terminal, "The series printed its stop without retaining a basis")
        self.assertEqual(terminal.get("stop_class"), "harness_defect")
        records = self.case.read_records()
        row = records["review_series_stop"][terminal["stop_id"]]
        self.assertEqual(row["code"], "review_series_no_progress")
        self.assertEqual(row["origin"], "harness")
        self.assertTrue(row["retry"]["exhausted"])
        self.assertEqual(row["retry"]["executions"], 1)
        self.assertEqual(row["assignment_id"], "bar-first")
        receipt = self.case.store.committed_request(row["request_id"])
        self.assertEqual(receipt["operation"], "review.series_stop")
        self.assertEqual(receipt["response"]["result"], row)

    def test_intermediate_replans_do_not_claim_exhausted_retries(self):
        from research_harness.review_continuation import plan_next
        calls = []
        def plan(records, artifacts, identifier):
            calls.append(identifier)
            if len(calls) <= 2:
                return dict(state="stop", code="store_busy", head_id=identifier, progress={}, details={})
            return plan_next(records, artifacts, identifier)
        with patch("research_harness.review_series.plan_next", side_effect=plan), patch(
                "research_harness.review_transport.send_request",
                return_value=self.case.build_provider_response(self.case.build_review_output("bar"))):
            self.assertEqual(run_series(self.case.store, "bar-first", emit=lambda row: None), 0)
        self.assertNotIn("review_series_stop", self.case.read_records())

    def test_auto_command_accepts_only_a_recorded_stop_identity(self):
        self.assertIn("gate-auto-pass", cli.OPERATIONS)
        terminal = self.stop_series()
        before = self.case.store.snapshot()
        for payload, code in (({"stop_id": terminal["stop_id"], "class": "harness_defect"},
                               "gate_override_invalid"),
                              ({"stop_id": "missing"}, "gate_override_stop_missing"),
                              ({"stop_id": terminal["stop_id"]}, "gate_override_internal_only")):
            with self.subTest(payload=payload), self.assertRaises(ResearchError) as caught:
                cli.OPERATIONS["gate-auto-pass"](self.case.store, payload,
                    expected_revision=self.case.store.revision, request_id="auto-refusal")
            self.assertEqual(caught.exception.code, code)
        self.assertEqual(self.case.store.snapshot(), before)

    def test_resume_stop_retains_the_original_action_identity(self):
        from research_harness import review_call_capture
        original = review_call_capture.capture_call
        def capture(*args, **kwargs):
            original(*args, **kwargs)
            raise RuntimeError("Interrupted after capture")
        with patch.object(review_call_capture, "capture_call", side_effect=capture):
            with self.assertRaises(RuntimeError):
                self.fixture.invoke(self.case.build_review_output("bar"))
        terminal = self.stop_series()
        row = self.case.read_records()["review_series_stop"][terminal["stop_id"]]
        self.assertEqual(row["retry"]["action_state"], "resume")
        with self.assertRaises(ResearchError) as caught:
            cli.OPERATIONS["gate-auto-pass"](self.case.store, {"stop_id": row["id"]},
                expected_revision=self.case.store.revision, request_id="resume-auto")
        self.assertEqual(caught.exception.code, "gate_override_internal_only")

    def test_pending_retry_schedule_cannot_supply_an_automatic_pass(self):
        terminal = self.stop_series()
        original = self.case.read_records()["review_series_stop"][terminal["stop_id"]]
        pending = dict(original, id="pending-stop", request_id="pending-stop",
                       recorded_revision=self.case.store.revision + 1,
                       retry=dict(original["retry"], exhausted=False))
        def apply(tx):
            tx.put("review_series_stop", pending["id"], pending)
            return pending
        self.case.store.mutate("review.series_stop", {"stop": pending}, apply,
            expected_revision=self.case.store.revision, request_id=pending["request_id"])
        with self.assertRaises(ResearchError) as caught:
            cli.OPERATIONS["gate-auto-pass"](self.case.store, {"stop_id": pending["id"]},
                expected_revision=self.case.store.revision, request_id="pending-auto")
        self.assertEqual(caught.exception.code, "gate_override_retry_pending")


class PublicWriteCompatibilityTests(unittest.TestCase):
    def test_public_write_without_automatic_passes_does_not_snapshot_store(self):
        from research_harness.automatic_overrides import check_public_write
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            Store(root, create=True)
            self.assertTrue((root / ".exactory/research.sqlite3").is_file())
            self.assertFalse((root / ".exactory/automatic-passes").exists())
            with patch("research_harness.workspace.find_workspace", return_value=root), patch.object(
                    Store, "snapshot", side_effect=AssertionError("Public write read the full store")):
                self.assertIsNone(check_public_write("publication"))

    def test_public_write_instruction_without_automatic_passes_does_not_snapshot_store(self):
        from research_harness.automatic_overrides import require_public_write_instruction
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            store = Store(root, create=True)
            self.assertTrue((root / ".exactory/research.sqlite3").is_file())
            self.assertFalse((root / ".exactory/automatic-passes").exists())
            with patch.object(Store, "snapshot", side_effect=AssertionError("Public write read the full store")):
                self.assertIsNone(require_public_write_instruction(store, "publication"))

    def test_legacy_unreadable_store_keeps_direct_path_but_local_automatic_report_blocks_it(self):
        from research_harness.automatic_overrides import check_public_write
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / ".exactory").mkdir()
            (root / ".exactory/research.sqlite3").write_bytes(b"Unreadable legacy store")
            with patch("research_harness.workspace.find_workspace", return_value=root):
                check_public_write("publication")
                (root / ".exactory/automatic-passes").mkdir()
                (root / ".exactory/automatic-passes/pass.json").write_text('{}')
                with self.assertRaises(ResearchError) as caught:
                    check_public_write("publication")
                self.assertEqual(caught.exception.code, "gate_override_public_write")
                (root / ".exactory/research.sqlite3").unlink()
                with self.assertRaises(ResearchError) as caught:
                    check_public_write("publication")
                self.assertEqual(caught.exception.code, "gate_override_public_write")


class AutomaticStudyPassTests(DevelopmentCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        case = DevelopmentCase()
        case.setUp()
        cls.addClassCleanup(case.doCleanups)
        case.prepared_study()
        state = dict(slug="automatic-study", stage="experiment", status="in_progress", autopilot=True,
                     waiting=None, loop=dict(target=None, budget=None, notes=""),
                     created="2026-11-07T00:00:00Z", updated="2026-11-07T00:00:00Z")
        case.mutate(integration.initialize_workspace, dict(kind="study", state=state))
        case.execution_payload = observed_candidate(case)
        cls.source_case = case

    def setUp(self):
        super().setUp()
        import copy
        for name in ("objective", "links", "execution_payload", "sequence"):
            setattr(self, name, copy.deepcopy(getattr(self.source_case, name)))
        # Model stored shapes with retained combined support and no selected
        # standalone readiness review. No historical writer is claimed.
        # All scientific records and event revisions remain real. This is a
        # compatibility shape, not a claim that a measured live record has it.
        original_root = self.source_case.root
        self.root = self.root.parent / "historical-study"
        shutil.copytree(original_root, self.root)
        (self.root / ".exactory/research.sqlite3").unlink()
        self.store = Store(self.root, create=True)
        with sqlite3.connect("file:" + str(original_root / ".exactory/research.sqlite3") + "?immutable=1",
                             uri=True) as connection:
            for request, operation, payload, changes, result in connection.execute(
                    "SELECT request_id, operation, payload, changes, result FROM events ORDER BY revision"):
                changes = [row for row in json.loads(changes)
                           if (row["kind"], row["key"]) != ("development_selection", "review")]
                def apply(tx, changes=changes, result=json.loads(result)):
                    for row in changes:
                        tx.put(row["kind"], row["key"], row["value"])
                    return result
                self.store.mutate(operation, json.loads(payload), apply,
                                  expected_revision=self.store.revision, request_id=request)
        from research_harness.artifacts import ArtifactStore
        self.artifacts = ArtifactStore(self.root)
        self.assertEqual(self.gate()["obligations"][0]["code"], "independent_review_missing")
        self.assertEqual(len(self.gate()["obligations"]), 1, self.gate()["obligations"])
        records = self.store.snapshot()["records"]
        assignment = next(row for row in records["review_assignment"].values() if row["role"] == "result")
        # Stored assignment rows replay without applying today's creation rule.
        # This modeled uncompleted slot uses the actual packet and route above.
        pending = dict(assignment, id="blocked-result", slot_id="legacy-readiness-slot",
                       recorded_revision=self.store.revision + 1)
        self.store.mutate("fixture.legacy_assignment", {},
            lambda tx: tx.put("review_assignment", pending["id"], pending),
            expected_revision=self.store.revision, request_id="legacy-pending-slot")

    def gate(self):
        return gate_state(self.store.snapshot()["records"], self.artifacts, "readiness")

    def stop(self):
        def no_progress(store, payload, **identity):
            return store.mutate("fixture.no_progress", {}, lambda tx: {}, **identity)
        steps = []
        with patch.dict(cli.OPERATIONS, {"review-run": no_progress}):
            self.assertEqual(run_series(self.store, "blocked-result", emit=steps.append), 3)
        self.assertIn("stop_id", steps[-1], steps[-1])
        return steps[-1]["stop_id"]

    def auto(self, stop, request="automatic-pass"):
        self.assertIn("gate-auto-pass", cli.OPERATIONS)
        return cli.OPERATIONS["gate-auto-pass"](self.store, {"stop_id": stop},
            expected_revision=self.store.revision, request_id=request)

    def refuse(self, stop, code):
        before = self.store.snapshot()
        with self.assertRaises(ResearchError) as caught:
            self.auto(stop)
        self.assertEqual(caught.exception.code, code, caught.exception.as_dict())
        self.assertEqual(self.store.snapshot(), before)

    def test_recorded_defect_advances_only_the_internal_stage_and_preserves_claims(self):
        stop = self.stop()
        before = self.store.snapshot()
        gate = self.gate()
        receipt = self.auto(stop)
        row = receipt["result"]
        self.assertEqual(row["authority"], "harness")
        self.assertEqual(row["stop_code"], "review_series_no_progress")
        self.assertEqual(row["stop_id"], stop)
        self.assertNotIn("instruction", row)
        self.assertTrue((self.root / row["report_path"]).is_file())
        recorded_stop = self.store.snapshot()["records"]["review_series_stop"][stop]
        self.assertIn("action_digest", recorded_stop["retry"])
        self.assertIn("progress_digest", recorded_stop["retry"])
        self.assertIn("successful_request_id", recorded_stop["retry"])
        report = json.loads((self.root / row["report_path"]).read_text())
        self.assertEqual(report["override_id"], row["id"])
        self.assertEqual(report["stop_id"], stop)
        self.assertEqual(self.gate(), gate)
        after = self.store.snapshot()["records"]
        self.assertEqual(after["workspace"]["study"]["stage"], "write")
        self.assertEqual(after["workspace"]["study"]["status"], "in_progress")
        for kind in before["records"]:
            if kind not in ("workspace", "literature_operation"):
                self.assertEqual(after[kind], before["records"][kind], kind)
        self.assertEqual(set(after) - set(before["records"]), {"gate_override"})
        # A replay restores its report without consuming another automatic pass.
        (self.root / row["report_path"]).unlink()
        self.assertEqual(self.auto(stop), receipt)
        self.assertTrue((self.root / row["report_path"]).is_file())

    def test_second_automatic_pass_of_same_gate_needs_user_even_after_return(self):
        stop = self.stop()
        self.auto(stop)
        with self.assertRaises(ResearchError) as caught:
            self.auto(stop, request="second-pass")
        self.assertEqual(caught.exception.code, "gate_override_user_required")
        self.assertIn("user", caught.exception.message)
        from research_harness.gates import validate_transition
        records = self.store.snapshot()["records"]
        before = dict(records["workspace"]["study"], stage="experiment")
        after = dict(before, stage="write")
        with self.assertRaises(ResearchError):
            validate_transition(records, self.artifacts, before, after)

    def test_public_write_stays_stopped_until_user_instruction_covers_it(self):
        self.auto(self.stop())
        from research_harness import automatic_overrides
        self.assertTrue(hasattr(automatic_overrides, "require_public_write_instruction"))
        require_public_write_instruction = automatic_overrides.require_public_write_instruction
        import os
        from test_transport import _transport as _cli
        from test_draft import _draft
        import urllib.request
        api_url = patch.object(_cli, "_API_BASE_URL", "https://exactory.ai", create=True)
        api_url.start()
        self.addCleanup(api_url.stop)
        self.addCleanup(os.chdir, os.getcwd())
        os.chdir(self.root)
        paths = (("POST", "/api/v1/verifications", "submitted"),
                 ("POST", "/api/v1/verifications/x/verdicts", "verification"),
                 ("POST", "/api/v1/math-problems", "post"), ("POST", "/api/v1/reports", None),
                 ("DELETE", "/api/v1/verifications", None))
        with patch.object(_cli, "open_url_within_origin", side_effect=AssertionError("Public request escaped")):
            for method, path, gate in paths:
                with self.subTest(path=path), self.assertRaises(ResearchError) as caught:
                    _cli._send_request(method, path, {}, authenticated=False)
                self.assertEqual(caught.exception.code, "gate_override_public_write")
                self.assertEqual(caught.exception.details["gate"], gate)
        with patch.object(_draft, "open_url_within_origin", side_effect=AssertionError("Deposit escaped")):
            with self.assertRaises(ResearchError) as caught:
                _draft._open_url(urllib.request.Request("https://zenodo.org/api/deposit/depositions", data=b"{}"))
            self.assertEqual(caught.exception.code, "gate_override_public_write")
        from test_transport import _FakeResponse
        with patch.object(_cli, "open_url_within_origin", side_effect=lambda *args, **kwargs: _FakeResponse(b'{"ok":true}', 200)):
            for path in ("/api/v1/email-codes", "/api/v1/api-keys"):
                self.assertEqual(_cli._send_request("POST", path, {}, authenticated=False), ({"ok": True}, 200))
        for gate in ("publication", "submitted", "verification", "post"):
            with self.subTest(gate=gate), self.assertRaises(ResearchError) as caught:
                require_public_write_instruction(self.store, gate)
            self.assertEqual(caught.exception.code, "gate_override_public_write")
        (self.root / "context").mkdir(exist_ok=True)
        (self.root / "context/public-instruction.txt").write_text("Deposit this draft now; retain its missing reviews.")
        self.mutate(cli.OPERATIONS["gate-override"], dict(id="public-deposit", gate="publication",
            instruction="context/public-instruction.txt", reason="The user instructed this deposit."))
        require_public_write_instruction(self.store, "publication")
        with self.assertRaises(ResearchError):
            require_public_write_instruction(self.store, "submitted")
        with patch.object(_cli, "open_url_within_origin", side_effect=AssertionError("Uncovered report escaped")):
            with self.assertRaises(ResearchError) as caught:
                _cli._send_request("POST", "/api/v1/reports", {}, authenticated=False)
            self.assertEqual(caught.exception.details["public_action"], "POST /api/v1/reports")
        (self.root / "context/public-instruction.txt").write_text("Send this report now with POST /api/v1/reports.")
        self.mutate(cli.OPERATIONS["gate-override"], dict(id="public-report", gate="publication",
            public_action="POST /api/v1/reports", instruction="context/public-instruction.txt",
            reason="User instruction for this exact report action."))
        require_public_write_instruction(self.store, None, public_action="POST /api/v1/reports")
        with patch.object(_cli, "open_url_within_origin", return_value=_FakeResponse(b'{"ok":true}', 200)):
            self.assertEqual(_cli._send_request("POST", "/api/v1/reports", {}, authenticated=False), ({"ok": True}, 200))
        with self.assertRaises(ResearchError):
            require_public_write_instruction(self.store, None, public_action="POST /api/v1/grand-challenges")

    def test_cli_check_is_read_only_and_write_uses_the_same_stop(self):
        from test_research_check_mode import CheckCommandMixin
        class Command(CheckCommandMixin):
            pass
        command = Command()
        command.root, command.store = self.root, self.store
        stop = self.stop()
        before = command.read_workspace()
        response = command.invoke_command("gate-auto-pass", {"stop_id": stop})
        self.assertEqual(response[0], 0, response[2])
        self.assertEqual(json.loads(response[1])["check"], "accepted")
        self.assertEqual(command.read_workspace(), before)
        response = command.invoke_command("gate-auto-pass", {"stop_id": stop}, check=False)
        self.assertEqual(response[0], 0, response[2])
        row = json.loads(response[1])["result"]
        self.assertEqual(row["stop_id"], stop)
        self.assertTrue((self.root / row["report_path"]).is_file())

    def test_report_failure_prevents_the_automatic_mutation(self):
        stop = self.stop()
        before = self.store.snapshot()
        from research_harness import automatic_overrides
        self.assertTrue(hasattr(automatic_overrides, "_write_report"))
        with patch.object(automatic_overrides, "_write_report", side_effect=OSError("No local report")):
            with self.assertRaises(ResearchError) as caught:
                self.auto(stop)
        self.assertEqual(caught.exception.code, "gate_override_report")
        self.assertEqual(self.store.snapshot(), before)

    def test_store_write_failure_keeps_a_pending_local_report_without_stage_movement(self):
        stop = self.stop()
        before = self.store.snapshot()
        with patch.object(self.store, "mutate", side_effect=ResearchError("storage_io", "Finishing write failed")):
            with self.assertRaises(ResearchError) as caught:
                self.auto(stop)
        self.assertIn("report_path", caught.exception.details or {})
        self.assertTrue((self.root / caught.exception.details["report_path"]).is_file())
        self.assertEqual(self.store.snapshot(), before)
        reports = list((self.root / ".exactory/automatic-passes").glob("*.json"))
        self.assertEqual(len(reports), 1)
        row = json.loads(reports[0].read_text())
        self.assertIs(row["recorded"], False)
        self.assertEqual(row["stop_id"], stop)
        from research_harness.overrides import describe_overrides
        pending, = describe_overrides(before["records"], self.root)["unrecorded_overrides"]
        self.assertEqual(pending["stop_id"], stop)

    def test_explicit_quality_condition_remains_binding(self):
        stop = self.stop()
        records = self.store.snapshot()["records"]
        identifier = records["strategy_selection"]["intent"]["id"]
        import copy
        row = copy.deepcopy(records["research_intent"][identifier])
        row["payload"]["publication"]["quality_condition"] = "Obtain every independent review before continuing."
        self.store.mutate("fixture.user_condition", {}, lambda tx: tx.put("research_intent", identifier, row),
                          expected_revision=self.store.revision, request_id="explicit-quality")
        self.refuse(stop, "gate_override_quality_condition")

    def test_new_review_progress_invalidates_the_old_stop(self):
        stop = self.stop()
        with patch("research_harness.review_transport.send_request", side_effect=OSError("Provider unavailable")):
            self.mutate(review_protocol.invoke_assignment, {"id": "later-failed-review", "assignment_id": "blocked-result"},
                        credential="fixture")
        self.refuse(stop, "gate_override_stop_stale")

    def test_a_current_wait_cannot_be_passed_using_an_older_defect_stop(self):
        stop = self.stop()
        original = self.store.snapshot()["records"]["review_series_stop"][stop]["plan"]
        waiting = dict(original, state="wait", code="review_provider_unavailable",
                       not_before="2099-01-01T00:00:00Z")
        # Capacity may change without changing the scientific progress vector.
        # Keep the real gate and recorded stop; substitute only that current
        # planner observation to exercise the admission boundary.
        with patch("research_harness.review_continuation.plan_next", return_value=waiting):
            self.refuse(stop, "gate_override_stop_stale")

    def test_an_unmet_decision_is_not_treated_as_a_missing_review(self):
        stop = self.stop()
        self.store.mutate("fixture.unselected_decision", {},
            lambda tx: tx.put("strategy_selection", "decision", {"id": "not-selected"}),
            expected_revision=self.store.revision, request_id="unselected-decision")
        self.refuse(stop, "gate_override_other_obligations")

    def test_status_next_final_and_archive_cite_defect_without_user_words(self):
        row = self.auto(self.stop())["result"]
        from research_harness import output_views
        from research_harness.report_views import status_summary, next_summary
        report = cli.status_report(self.store)
        for view in (report, status_summary(report), next_summary(report)):
            shown, = view["overrides"]
            self.assertEqual(shown["stop_code"], "review_series_no_progress")
            self.assertEqual(shown["stop_id"], row["stop_id"])
            self.assertNotIn("instruction", shown)
        from research_harness import overrides
        self.assertTrue(hasattr(overrides, "describe_deposit_overrides"))
        disclosure = overrides.describe_deposit_overrides(self.store)
        self.assertEqual(disclosure["passed_gates"], [])
        automatic, = disclosure["automatic_passes"]
        self.assertEqual(automatic["stop_id"], row["stop_id"])
        from test_draft import _draft
        description = _draft._build_deposit_metadata("Finite result", ["Author"], "Finite result abstract.", False,
                                                     **disclosure)["description"]
        self.assertIn(row["stop_code"], description)
        self.assertIn(row["stop_id"], description)
        self.assertNotIn("user of the session instructed", description)
        output_views.render_all(self.store)
        exported, = (self.root / "final/overrides").glob("*.json")
        self.assertEqual(json.loads(exported.read_text()), row)
        from research_harness.deposit_package import build_package
        # Package the same real-shape record through the archive's ordinary
        # explicit bundle interface; automatic reports are artifact references.
        claims = self.artifacts.put(b'[{"id":"bound","claim":"Finite bound."}]', "application/json")
        bundle = dict(id="archive", digest="a" * 64,
                      files={"claims": dict(path="claims.json", artifact=claims)}, claim_evidence=[])
        self.store.mutate("fixture.archive", {}, lambda tx: tx.put("publication_bundle", "archive", bundle),
                          expected_revision=self.store.revision, request_id="archive-fixture")
        archive = build_package(self.store, self.root.parent / "deposit-package.zip", bundle="archive")
        import zipfile
        with zipfile.ZipFile(archive["path"]) as package:
            manifest = json.loads(package.read("package.json"))
            name = manifest["records"]["gate_override"][row["id"]]
            self.assertEqual(json.loads(package.read(name)), row)

    def test_old_user_override_receipt_and_record_read_unchanged(self):
        from research_harness import overrides
        (self.root / "context").mkdir(exist_ok=True)
        (self.root / "context/old-instruction.txt").write_text("Continue to local writing.")
        value = dict(id="old-user-override", gate="readiness", instruction="context/old-instruction.txt", reason="User instruction.")
        old = self.mutate(overrides.record_override, value)
        before = self.store.snapshot()
        self.assertEqual(Store(self.root).snapshot(), before)
        receipt = overrides.record_override(Store(self.root), value,
            expected_revision=old["revision"] - 1, request_id="task3-" + str(self.sequence))
        self.assertEqual(receipt, old)
        self.assertEqual(self.store.snapshot(), before)

    def test_rejecting_review_keeps_gate_stopped(self):
        stop = self.stop()
        review = self.review(self.execution_payload, identifier="rejecting", verdict="not_ready")
        self.mutate(self.development().record_readiness_review, review)
        self.refuse(stop, "gate_override_other_obligations")
        self.assertFalse(self.gate()["ready"])

    def test_an_unselected_rejection_cannot_be_hidden_by_a_missing_review_selector(self):
        stop = self.stop()
        review = self.review(self.execution_payload, identifier="retained-rejection", verdict="not_ready")
        from research_harness.development import prepare_combined_support
        changes, _ = prepare_combined_support(self.store.snapshot()["records"], self.artifacts, review,
            revision=self.store.revision + 1, request_id="legacy-retained-rejection")
        def apply(tx):
            for kind, key, row in changes:
                if kind == "readiness_review":
                    tx.put(kind, key, row)
        self.store.mutate("fixture.legacy_unselected_rejection", {}, apply,
            expected_revision=self.store.revision, request_id="legacy-retained-rejection")
        self.assertEqual([row["code"] for row in self.gate()["obligations"]], ["independent_review_missing"])
        self.refuse(stop, "gate_override_other_obligations")

    def test_an_unmet_decision_or_budget_keeps_gate_stopped(self):
        stop = self.stop()
        from research_harness.review_budget import read_budget_usage, record_budget
        usage = read_budget_usage(self.store.snapshot()["records"])
        self.mutate(record_budget, dict(max_calls=usage["charged_calls"], reason="No more calls."))
        self.refuse(stop, "gate_override_budget_exhausted")

    def test_exhausted_strategy_and_zero_loop_budget_are_decisions(self):
        stop = self.stop()
        records = self.store.snapshot()["records"]
        key, account = next(iter(records["strategy_account"].items()))
        exhausted = dict(account, charged_units=account["limits"]["max_units"])
        self.store.mutate("fixture.exhausted_strategy", {},
            lambda tx: tx.put("strategy_account", key, exhausted),
            expected_revision=self.store.revision, request_id="strategy-budget")
        self.refuse(stop, "gate_override_budget_exhausted")
        self.store.mutate("fixture.restore_strategy", {}, lambda tx: tx.put("strategy_account", key, account),
                          expected_revision=self.store.revision, request_id="restore-strategy-budget")
        study = self.store.snapshot()["records"]["workspace"]["study"]
        study = dict(study, loop=dict(study["loop"], budget=0))
        self.store.mutate("fixture.zero_loop", {}, lambda tx: tx.put("workspace", "study", study),
                          expected_revision=self.store.revision, request_id="zero-loop-budget")
        self.refuse(stop, "gate_override_budget_exhausted")

    def test_unknown_waiting_and_material_stops_never_pass(self):
        from research_harness.automatic_overrides import record_series_stop
        for code in ("review_input_too_large", "review_format_unsupported", "review_output_over_cap",
                     "review_failure_unknown", "review_provider_unavailable", "store_busy",
                     "review_budget_exhausted"):
            with self.subTest(code=code):
                row = record_series_stop(self.store, dict(state="stop", code=code, head_id="blocked-result",
                    progress={}, details={}))
                self.refuse(row["id"], "gate_override_stop_class")

    def test_forged_stop_and_pending_schedule_are_refused(self):
        stop = self.stop()
        original = self.store.snapshot()["records"]["review_series_stop"][stop]
        def put(row):
            self.store.mutate("fixture.forged", {},
                lambda tx: tx.put("review_series_stop", row["id"], row),
                expected_revision=self.store.revision, request_id="forge-" + str(self.store.revision))
        forged = dict(original, id="forged")
        put(forged)
        self.refuse("forged", "gate_override_stop_untrusted")
        # Even a correctly shaped row cannot change the original harness receipt.
        put(dict(original, retry=dict(original["retry"], exhausted=False)))
        self.refuse(stop, "gate_override_stop_untrusted")
