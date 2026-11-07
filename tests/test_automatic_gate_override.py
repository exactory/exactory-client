"""Automatic continuations use harness observations, never caller classifications."""

import ast
import importlib.util
import json
import os
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

    def test_auto_pass_example_reads_the_examples_file(self):
        args = cli.build_parser().parse_args(["example", "gate-auto-pass"])
        original = Path.read_bytes
        def read(path):
            if path == ROOT / "docs/research-cli-examples.json":
                return b'{"gate-auto-pass":{"stop_id":"from-the-examples-file"}}'
            return original(path)
        with patch.object(Path, "read_bytes", read):
            self.assertEqual(cli.run(args), {"stop_id": "from-the-examples-file"})

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
                     "review_budget_exhausted", "resource_budget_exhausted", "new_unrecognized_code",
                     "store_busy", "lock_timeout", "review_provider_unavailable"):
            with self.subTest(code=code):
                self.assertEqual(policy.classify_stop(code), "decision")
        self.assertEqual(policy.classify_stop("review_series_no_progress"), "harness_defect")
        for code in ("rate_limited", "review_capacity_wait", "review_invocation_pending", "provider_state_capacity",
                     "execution_live", "remote_operation_owned", "verification_ingest_pending"):
            with self.subTest(code=code):
                self.assertEqual(policy.classify_stop(code), "waiting")

    def test_authentication_failure_keeps_attempt_classification_but_is_a_decision_stop(self):
        from test_review_failure_class import FailureClassTests
        fixture = FailureClassTests()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        attempt = fixture.attempt('API Error: 401 {"error":{"type":"authentication_error","message":"invalid x-api-key"}}')
        self.assertEqual(fixture.classify(attempt)["outcome"], "provider_unavailable")
        plan = fixture.plan()
        self.assertEqual(plan["state"], "wait")
        self.assertEqual(plan["code"], "review_provider_unavailable")
        self.assertEqual(self.policy().classify_stop(plan["code"]), "decision")

    def test_reentrant_store_busy_is_a_decision_stop(self):
        with tempfile.TemporaryDirectory() as directory:
            store = Store(Path(directory), create=True)
            descriptor = os.open(store.root / ".exactory", os.O_RDONLY)
            self.addCleanup(os.close, descriptor)
            with patch("research_harness.storage._BUSY_TIMEOUT_SECONDS", 0.001), store._locked(descriptor):
                with self.assertRaises(ResearchError) as caught:
                    with store._locked(descriptor):
                        self.fail("Reentrancy acquired the same store")
            self.assertEqual(caught.exception.code, "store_busy")
            self.assertEqual(self.policy().classify_stop(caught.exception.code), "decision")

    def test_live_execution_and_remote_owners_are_waiting_stops(self):
        from research_harness.execution import _owner
        from research_harness.submission import send_verdict
        from research_harness.zenodo import own_workspace_deposits
        with tempfile.TemporaryDirectory() as directory:
            store = Store(Path(directory), create=True)
            with _owner(store.root, "execution"):
                with self.assertRaises(ResearchError) as caught:
                    with _owner(store.root, "execution"):
                        self.fail("Two owners acquired the execution")
            self.assertEqual(caught.exception.code, "execution_live")
            self.assertEqual(self.policy().classify_stop(caught.exception.code), "waiting")
            with _owner(store.root, "verdict:verification"):
                with self.assertRaises(ResearchError) as caught:
                    send_verdict(store, {"verificationId": "verification"}, {}, None,
                                 expected_revision=0, request_id="verdict")
            self.assertEqual(caught.exception.code, "remote_operation_owned")
            self.assertEqual(self.policy().classify_stop(caught.exception.code), "waiting")
            with own_workspace_deposits(store.root):
                with self.assertRaises(ResearchError) as caught:
                    with own_workspace_deposits(store.root):
                        self.fail("Two owners acquired the deposits")
            self.assertEqual(caught.exception.code, "remote_operation_owned")
            self.assertEqual(self.policy().classify_stop(caught.exception.code), "waiting")


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

    def test_malformed_packet_keeps_the_cli_terminal_stop_and_exit_code(self):
        import io
        from contextlib import redirect_stdout, redirect_stderr
        from test_review_failed_request_history import FailedRequestHistoryTests, read_cases
        history = FailedRequestHistoryTests()
        history.setUp()
        self.addCleanup(history.doCleanups)
        records, _, _ = history.load_case(read_cases("labelled-pre-fit-local")[0])
        records["review_assignment"]["assignment"]["packet"] = history.artifacts.put(b"{broken", "application/json")
        records["workspace"] = {"study": {"stage": "experiment"}}
        records["configuration"] = {"research": {}}
        store = Store(history.root, create=True)
        def apply(tx):
            for kind, rows in records.items():
                for key, value in rows.items():
                    tx.put(kind, key, value)
        store.mutate("fixture.history", {}, apply, expected_revision=0, request_id="history")
        output, errors = io.StringIO(), io.StringIO()
        with redirect_stdout(output), redirect_stderr(errors), self.assertRaises(SystemExit) as exited:
            cli.main(["review-series", "--workspace", str(history.root), "--assignment", "assignment"],
                     render_views=lambda workspace: None)
        self.assertEqual(exited.exception.code, 3, errors.getvalue())
        rows = [json.loads(line) for line in output.getvalue().splitlines()]
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["code"], "review_format_unsupported")
        self.assertIn("stop_record_error", rows[0])
        self.assertNotIn("stop_id", rows[0])
        self.assertEqual(errors.getvalue(), "")

    def test_recording_failure_never_replaces_the_original_stop(self):
        plan = dict(state="stop", code="review_format_unsupported", head_id="bar-first", progress={}, details={})
        for failure in (OSError("disk failed"), ValueError("bad scope"), RuntimeError("recorder failed")):
            with self.subTest(failure=type(failure).__name__), patch(
                    "research_harness.review_series.plan_next", return_value=plan), patch(
                    "research_harness.automatic_overrides.record_series_stop", side_effect=failure):
                rows = []
                self.assertEqual(run_series(self.case.store, "bar-first", emit=rows.append), 3)
                self.assertEqual(len(rows), 1)
                self.assertEqual(rows[0]["code"], plan["code"])
                self.assertEqual(rows[0]["details"], {})
                self.assertIn("stop_record_error", rows[0])

    def test_recorded_stop_reports_its_new_revision_without_changing_existing_fields(self):
        revision = self.case.store.revision
        plan = dict(state="stop", code="stale_revision", head_id="bar-first", progress={},
                    revision=revision - 1, details=dict(expected_revision=revision - 1, revision=revision))
        rows = []
        with patch("research_harness.review_series.plan_next", return_value=plan):
            self.assertEqual(run_series(self.case.store, "bar-first", emit=rows.append), 3)
        terminal = rows[-1]
        self.assertEqual(terminal["revision"], revision - 1)
        self.assertEqual(terminal["details"], plan["details"])
        self.assertEqual(terminal.get("revision_after_stop"), revision + 1)
        self.assertEqual(self.case.store.revision, revision + 1)

    def test_terminal_decision_does_not_claim_a_retry_schedule(self):
        from research_harness.automatic_overrides import record_series_stop
        row = record_series_stop(self.case.store, dict(state="stop", code="review_input_too_large",
            head_id="bar-first", progress={}, details={}))
        self.assertIsNone(row["retry"])

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
    def test_independent_nested_workspace_does_not_inherit_a_parent_automatic_pass(self):
        from research_harness.automatic_overrides import check_public_write
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / ".exactory/automatic-passes").mkdir(parents=True)
            child = root / "independent"
            child.mkdir()
            Store(child, create=True)
            self.addCleanup(os.chdir, os.getcwd())
            os.chdir(child)
            with patch.object(Store, "snapshot", side_effect=AssertionError("Read the independent workspace")):
                self.assertIsNone(check_public_write("submitted"))

    def test_managed_precheck_accepts_an_exact_action_with_another_recognized_gate(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            store = Store(root, create=True)
            (root / ".exactory/automatic-passes").mkdir()
            store.mutate("fixture.action_scope", {}, lambda tx: (
                tx.put("configuration", "research", {}),
                tx.put("gate_override", "instruction", dict(authority="user", gate="publication",
                    public_action="POST /api/v1/verifications"))), expected_revision=0, request_id="action-scope")
            opened, report, error = cli.select_managed_path(lambda store: "accepted", root, public_gate="submitted")
            self.assertEqual(report, "accepted")
            self.assertIsNone(error)
            from research_harness.automatic_overrides import require_public_write_instruction
            require_public_write_instruction(opened, "submitted", public_action="POST /api/v1/verifications")
            with self.assertRaises(ResearchError) as caught:
                require_public_write_instruction(opened, "submitted", public_action="DELETE /api/v1/verifications")
            self.assertEqual(caught.exception.code, "gate_override_public_write")
            before = store.snapshot()
            checked = []
            with self.assertRaises(ResearchError) as caught:
                cli.select_managed_path(lambda store: checked.append(True), root, public_gate="submitted",
                                        public_action="DELETE /api/v1/verifications")
            self.assertEqual(caught.exception.code, "gate_override_public_write")
            self.assertEqual(checked, [])
            self.assertEqual(store.snapshot(), before)

    def test_local_public_write_refusal_does_not_leave_a_remote_step_uncertain(self):
        from research_harness.automatic_overrides import require_public_write_instruction
        from research_harness.remote import begin_intent, get_intent, remote_step
        with tempfile.TemporaryDirectory() as directory:
            store = Store(Path(directory), create=True)
            (store.root / ".exactory/automatic-passes").mkdir()
            intent = begin_intent(store, "deposit", {}, expected_revision=0, request_id="deposit")
            sent = []
            def perform():
                require_public_write_instruction(store, "publication", public_action="POST /api/deposit/depositions")
                sent.append("POST")
                return {"id": 42}
            with self.assertRaises(ResearchError) as caught:
                remote_step(store, intent["id"], "create", {}, perform)
            self.assertEqual(caught.exception.code, "gate_override_public_write")
            refused = get_intent(store, intent["id"])
            self.assertIsNone(refused["pending"])
            self.assertEqual(refused["status"], "prepared")
            self.assertEqual(refused["responses"], {})
            self.assertEqual(refused["discarded"][-1]["observation"]["kind"], "local_authority_refusal")
            self.assertEqual(sent, [])
            store.mutate("fixture.instruction", {}, lambda tx: tx.put("gate_override", "user",
                dict(authority="user", gate="submitted", public_action="POST /api/deposit/depositions")),
                expected_revision=store.revision, request_id="user")
            self.assertEqual(remote_step(store, intent["id"], "create", {}, perform), {"id": 42})
            self.assertEqual(sent, ["POST"])

    def test_other_remote_failures_keep_the_pending_step_for_reconciliation(self):
        from research_harness.remote import begin_intent, get_intent, remote_step
        for failure in (ResearchError("unconfirmed", "The response is unknown"), OSError("Connection lost")):
            with self.subTest(failure=type(failure).__name__), tempfile.TemporaryDirectory() as directory:
                store = Store(Path(directory), create=True)
                intent = begin_intent(store, "deposit", {}, expected_revision=0, request_id="uncertain")
                def perform():
                    raise failure
                with self.assertRaises(type(failure)):
                    remote_step(store, intent["id"], "create", {}, perform)
                self.assertEqual(get_intent(store, intent["id"])["pending"], {"name": "create", "request": {}})
                self.assertEqual(get_intent(store, intent["id"])["status"], "in_flight")

    def test_public_write_without_automatic_passes_does_not_snapshot_store(self):
        from research_harness.automatic_overrides import check_public_write
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            Store(root, create=True)
            self.assertTrue((root / ".exactory/research.sqlite3").is_file())
            self.assertFalse((root / ".exactory/automatic-passes").exists())
            self.addCleanup(os.chdir, os.getcwd())
            os.chdir(root)
            with patch.object(Store, "snapshot", side_effect=AssertionError("Public write read the full store")):
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
            self.addCleanup(os.chdir, os.getcwd())
            nested = root / "nested"
            nested.mkdir()
            os.chdir(nested)
            from research_harness.workspace import find_workspace
            with self.subTest(discovery="real"):
                check_public_write("publication")
                (root / ".exactory/automatic-passes").mkdir()
                (root / ".exactory/automatic-passes/pass.json").write_text('{}')
                with self.assertRaises(ResearchError) as caught:
                    check_public_write("publication")
                self.assertEqual(caught.exception.code, "gate_override_public_write")
                (root / ".exactory/research.sqlite3").unlink()
                self.assertIsNone(find_workspace(required=False))
                with self.assertRaises(ResearchError) as caught:
                    check_public_write("publication")
                self.assertEqual(caught.exception.code, "gate_override_public_write")

    def test_store_busy_after_open_preserves_direct_path_only_without_automatic_marker(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            store = Store(root, create=True)
            store.mutate("fixture.config", {}, lambda tx: tx.put("configuration", "research", {}),
                         expected_revision=0, request_id="config")
            snapshot = store.snapshot()
            marker = root / ".exactory/automatic-passes"
            for gate in ("submitted", "verification", "publication"):
                for automatic in (False, True):
                    with self.subTest(gate=gate, automatic=automatic):
                        if automatic:
                            marker.mkdir()
                        try:
                            # current_store really opens and reads the store. Its
                            # next read loses a race with another writer.
                            with patch.object(Store, "snapshot", side_effect=[snapshot,
                                    ResearchError("store_busy", "Another writer owns the store"), snapshot]):
                                if automatic:
                                    with self.assertRaises(ResearchError):
                                        cli.select_managed_path(lambda opened: opened.snapshot(), root,
                                                                public_gate=gate)
                                else:
                                    opened, report, error = cli.select_managed_path(
                                        lambda opened: opened.snapshot(), root, public_gate=gate)
                                    self.assertEqual(opened.root, root.resolve())
                                    self.assertIsNone(report)
                                    self.assertIsNone(error)
                        finally:
                            if automatic:
                                marker.rmdir()


class AutomaticDisclosureTests(unittest.TestCase):
    def test_metadata_without_automatic_entries_matches_released_bytes(self):
        from test_draft import _draft
        fixture = json.loads((ROOT / "tests/fixtures/phase_b_override_a04617f.json").read_text())
        for index, (authored, gates) in enumerate((authored, gates) for authored in (False, True)
                                                for gates in ([], ["publication"])):
            metadata = _draft._build_deposit_metadata("Finite result", ["Author"], "Finite abstract.", authored,
                                                     passed_gates=gates)
            self.assertEqual(json.dumps(metadata).encode(), json.dumps(fixture["metadata"][index]).encode())

    def test_automatic_disclosure_explains_missing_reviews_and_confirmation_after_user_paragraph(self):
        import html
        from test_draft import _draft
        fixture = json.loads((ROOT / "tests/fixtures/phase_b_override_a04617f.json").read_text())
        template = (
            "This paper was deposited from a workspace of a research harness that checks each step of the work before "
            "the next one. Some checks require independent reviews. The workspace has an automatic-continuation entry "
            "for the check {gate}, with stop identifier {stop_id} and code {stop_code}. A readable entry says that the "
            "harness classified the stop as a defect of its own and proposed advancing the work without the missing "
            "reviews. {confirmation} An entry named unknown could not be read. This entry supplies no review approval "
            "or permission to publish, and does not show whether the missing reviews were completed later.")
        for recorded, confirmation in ((True, "The stored record confirms that the harness advanced the study to its next internal stage."),
                (False, "Only a local report was available; a committed continuation could not be confirmed.")):
            with self.subTest(recorded=recorded):
                entry = dict(gate="readiness", stop_id="series-stop:<id>", stop_code="review_series_no_progress", recorded=recorded)
                actual = _draft._build_deposit_metadata("Finite result", ["Author"], "Finite abstract.", False,
                    passed_gates=["publication"], automatic_passes=[entry])
                expected = dict(fixture["metadata"][1])
                expected["description"] += "<p>" + html.escape(template.format(**entry, confirmation=confirmation)) + "</p>"
                self.assertEqual(actual, expected)


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

    def stop(self, assignment_id="blocked-result"):
        def no_progress(store, payload, **identity):
            return store.mutate("fixture.no_progress", {}, lambda tx: {}, **identity)
        steps = []
        with patch.dict(cli.OPERATIONS, {"review-run": no_progress}):
            self.assertEqual(run_series(self.store, assignment_id, emit=steps.append), 3)
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
        self.return_to_experiment()
        stop = self.stop()
        with self.assertRaises(ResearchError) as caught:
            self.auto(stop, request="second-pass")
        self.assertEqual(caught.exception.code, "gate_override_user_required")
        self.assertEqual(caught.exception.message,
            "An automatic pass of this gate is already recorded. To continue on the user's instruction, "
            "record a user override with `exactory-research gate-override`.")

    def return_to_experiment(self):
        from integration_fixtures import prepare_manuscript
        self.mutate(self.development().record_readiness_review, self.review(self.execution_payload, "return-ready"))
        (self.root / "draft").mkdir(exist_ok=True)
        (self.root / "draft/paper.pdf").write_bytes(b"%PDF-1.4 finite fixture")
        (self.root / "draft/abstract.txt").write_text("Finite fixture abstract.")
        (self.root / "draft/references.bib").write_text("% Authored finite fixture bibliography.\n")
        prepare_manuscript(self)
        self.mutate(integration.update_study, {"stage": "evaluate"})
        self.mutate(integration.update_study, {"stage": "experiment"})
        self.assertEqual(self.store.snapshot()["records"]["workspace"]["study"]["stage"], "experiment")

    def test_readiness_alias_cannot_pass_the_same_hop_twice(self):
        first = self.stop()
        self.auto(first)
        self.return_to_experiment()
        # Add a real source limitation and select its supported publication
        # scope, retaining the full objective and the first pass in this store.
        from source_limited_fixtures import SourceLimitedCase
        from managed_strategy_fixtures import result_decision
        scoped = SourceLimitedCase()
        for name in ("root", "store", "artifacts", "objective", "links", "sequence"):
            setattr(scoped, name, getattr(self, name))
        scoped.gap = scoped.metadata(90)
        scoped.require_source(scoped.gap)
        scoped.add_missing_supplement(scoped.gap)
        deferred = scoped.build_deferral(scoped.gap)
        deferred["dependent_claims"] = [scoped.debt]
        scoped.deferral = scoped.mutate(scoped.get_operation("defer-source"), deferred)["result"]
        scoped.foundation_searches(identifier_suffix="-deferred")
        scoped.refresh_synthesis("deferred")
        plan = self.store.snapshot()["records"]["cycle_plan"]["cycle-1"]["payload"]
        scoped.execution_payload = self.execution_payload
        assessment = scoped.assessment(plan, scoped.execution_payload, "scoped-assessment", partial=True)
        assessment["scope"]["remaining_obligations"] = [scoped.debt]
        assessment["remaining_obligations"] = [scoped.debt]
        assessment["development"]["novelty"]["scope"]["remaining_obligations"] = [scoped.debt]
        assessment.update(objective_status="open", disposition="continue")
        scoped.mutate(scoped.development().assess_cycle, assessment)
        scoped.save_checkpoint("cycle-1", "scoped-assessment", "scoped-checkpoint")
        scoped.assessment_payload = assessment
        payload = scoped.scope_payload()
        from research_harness.execution_evidence import author_readiness_state
        from research_harness.review_delivery import references
        from research_harness.review_packets import readiness_packet
        packet = readiness_packet(author_readiness_state(self.store.snapshot()["records"], self.artifacts))
        required = {}
        def gather(value):
            if isinstance(value, dict):
                if isinstance(value.get("artifact"), dict) and isinstance(value.get("locator"), dict):
                    required.setdefault(value["artifact"]["sha256"], []).append(value["locator"])
                for child in value.values():
                    gather(child)
            elif isinstance(value, list):
                for child in value:
                    gather(child)
        gather(packet)
        declarations = []
        for ref in references(self.store.snapshot()["records"]):
            data = self.artifacts.read(ref)
            if ref["sha256"] in required:
                locators = list({json.dumps(row, sort_keys=True): row for row in required[ref["sha256"]]}.values())
                if not all(row["kind"] == "json" for row in locators):
                    continue
                projection = dict(kind="json_locators", locators=locators, context="Required finite result values.")
            elif data.lstrip().startswith(b"{"):
                projection = dict(kind="json_locators", locators=[
                    dict(kind="json", pointer="/" + key.replace("~", "~0").replace("/", "~1"), value=value)
                    for key, value in json.loads(data).items()], context="Typed fixture observations.")
            elif data:
                try:
                    value = data.decode()
                except UnicodeError:
                    continue
                projection = dict(kind="text_spans", locators=[dict(kind="text", start=0, end=len(value), quote=value)],
                                  context="Complete authored fixture content.")
            else:
                continue
            declarations.append(dict(original_sha256=ref["sha256"], disposition="project", projection=projection))
        payload["scientific_delivery"] = declarations
        scoped.record_scope(payload)
        original_review = scoped.review
        def scoped_support(execution, identifier="review-1", verdict="ready"):
            review = original_review(execution, identifier, verdict)
            report = scoped.manuscript_readiness()
            review["candidate_digest"] = report["candidate_digest"]
            review["target"] = dict(kind="source_limited_manuscript", contract_id=report["contract"]["id"],
                                    scientific_target_digest=report["scientific_target_digest"])
            review["checks"].append(dict(review["checks"][0], kind="source_limits"))
            return review
        scoped.review = scoped_support
        result_decision(scoped)
        # Model the same retained-combined-support historical shape as setUp,
        # now for the scoped review selector. Keep all events and receipts real.
        with sqlite3.connect(self.root / ".exactory/research.sqlite3") as connection:
            events = list(connection.execute(
                "SELECT request_id, operation, payload, changes, result FROM events ORDER BY revision"))
        (self.root / ".exactory/research.sqlite3").unlink()
        self.store = Store(self.root, create=True)
        for request, operation, value, changes, result in events:
            changes = [row for row in json.loads(changes) if row["kind"] != "scoped_review_selection"]
            def apply(tx, changes=changes, result=json.loads(result)):
                for row in changes:
                    tx.put(row["kind"], row["key"], row["value"])
                return result
            self.store.mutate(operation, json.loads(value), apply,
                              expected_revision=self.store.revision, request_id=request)
        records = self.store.snapshot()["records"]
        assignment = max((row for row in records["review_assignment"].values() if row["role"] == "result"),
                         key=lambda row: row["recorded_revision"])
        pending = dict(assignment, id="blocked-result", slot_id="scoped-pending-slot",
                       recorded_revision=self.store.revision + 1)
        self.store.mutate("fixture.pending_scoped", {}, lambda tx: tx.put("review_assignment", pending["id"], pending),
                          expected_revision=self.store.revision, request_id="pending-scoped")
        report = gate_state(self.store.snapshot()["records"], self.artifacts, "manuscript-readiness")
        self.assertEqual([row["code"] for row in report["obligations"]], ["scoped_review_missing"])
        second = self.stop()
        self.assertNotEqual(second, first)
        self.assertEqual(self.store.snapshot()["records"]["review_series_stop"][second]["scope"]["gate"],
                         "manuscript-readiness")
        with self.assertRaises(ResearchError) as caught:
            self.auto(second, request="second-hop-new-request")
        self.assertEqual(caught.exception.code, "gate_override_user_required")

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

    def test_exact_public_actions_reach_submission_verdict_and_deposit_transports(self):
        import io
        from contextlib import redirect_stdout, redirect_stderr
        from test_transport import _transport, _FakeResponse, _VERIFICATION_ID, _write_verdict_file
        from test_draft import _draft, _FakeZenodoApi
        self.auto(self.stop())
        self.addCleanup(os.chdir, os.getcwd())
        os.chdir(self.root)
        (self.root / "context").mkdir(exist_ok=True)
        (self.root / "context/public.txt").write_text("Submit this paper, send its verdict, and deposit it now.")
        actions = [("publication", "POST /api/v1/verifications"),
                   ("readiness", "POST /api/v1/verifications/" + _VERIFICATION_ID + "/verdicts")]
        actions += [("submitted", action) for action in (
            "POST /api/deposit/depositions", "PUT /api/files/bucket-1/paper.pdf",
            "PUT /api/deposit/depositions/4242", "PUT /api/records/4242/draft")]
        for index, (gate, action) in enumerate(actions):
            self.mutate(cli.OPERATIONS["gate-override"], dict(id="exact-write-" + str(index), gate=gate,
                public_action=action, instruction="context/public.txt", reason="User instruction for this write."))
        sent = []
        def api(request, **kwargs):
            sent.append((request.get_method(), request.full_url))
            return _FakeResponse(json.dumps({"verificationId": _VERIFICATION_ID}).encode(), 201)
        _write_verdict_file(self.root / "verdict.json")
        with patch.object(_transport, "_API_BASE_URL", "https://exactory.ai", create=True), patch.object(
                _transport, "_read_api_key", return_value="fixture"), patch.object(
                _transport, "report_citation_gate"), patch.object(_transport, "open_url_within_origin", side_effect=api), \
                redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
            for arguments in (["submit", "--doi", "10.5281/zenodo.1"],
                              ["verify", _VERIFICATION_ID, "--file", "verdict.json"]):
                with self.subTest(command=arguments[0]):
                    args = _transport._build_parser().parse_args(arguments)
                    args.handler(args)
        self.assertIn(("POST", "https://exactory.ai/api/v1/verifications"), sent)
        self.assertIn(("POST", "https://exactory.ai/api/v1/verifications/" + _VERIFICATION_ID + "/verdicts"), sent)
        (self.root / "draft").mkdir(exist_ok=True)
        (self.root / "draft/paper.pdf").write_bytes(b"%PDF-1.4 finite fixture")
        (self.root / "draft/abstract.txt").write_text("Finite fixture abstract.")
        self.mutate(integration.initialize_workspace, dict(kind="draft", state=dict(
            title="Finite fixture", corpus="arxiv", category="cs.MA", created="2026-11-07T00:00:00Z")))
        zenodo = _FakeZenodoApi()
        def deposit_api(request, **kwargs):
            return _FakeResponse(json.dumps(zenodo(request)).encode(), 200)
        with patch.dict(os.environ, {"ZENODO_SANDBOX_TOKEN": "fixture"}), patch.object(
                _draft, "open_url_within_origin", side_effect=deposit_api), \
                redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
            args = _draft._build_parser().parse_args(["deposit", "--creator", "Author", "--abstract-file", "draft/abstract.txt"])
            args.handler(args)
        self.assertEqual(Store(self.root).snapshot()["records"]["workspace"]["deposit"]["deposition_id"], 4242)
        self.assertEqual(zenodo.requests[0].get_method(), "POST")

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
        with self.assertRaises(ResearchError) as caught:
            self.auto(stop)
        self.assertEqual(caught.exception.message,
            "The current intent contains an explicit quality condition, so automatic continuation is unavailable. "
            "Continue through the ordinary gates, or record a user instruction that expressly covers this stop.")

    def test_ready_gate_uses_the_ordinary_transition_instead_of_an_automatic_pass(self):
        stop = self.stop()
        self.mutate(self.development().record_readiness_review, self.review(self.execution_payload, "new-ready-review"))
        self.assertTrue(self.gate()["ready"])
        self.refuse(stop, "gate_override_gate_ready")
        with self.assertRaises(ResearchError) as caught:
            self.auto(stop)
        self.assertIn("ordinary", caught.exception.message)
        self.mutate(integration.update_study, {"stage": "write"})
        self.assertEqual(self.store.snapshot()["records"]["workspace"]["study"]["stage"], "write")

    def test_managed_publication_missing_reviews_cannot_supply_an_internal_automatic_scope(self):
        from research_harness import publication, predictions, strategy
        from integration_fixtures import account_fixture_citations, build_manuscript_review, build_prediction
        self.mutate(self.development().record_readiness_review, self.review(self.execution_payload, "publication-ready"))
        self.mutate(integration.update_study, {"stage": "write"})
        files = dict(pdf="draft/paper.pdf", abstract="draft/abstract.txt", bibliography="draft/references.bib",
                     claims="evidence/claims.json", sources=None)
        for key, data in dict(pdf=b"%PDF-1.4 finite fixture", abstract=b"Finite bound.",
                bibliography=b"% Authored bibliography.", claims=b'[{"id":"bound","claim":"The maximum is 9."}]').items():
            path = self.root / files[key]
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
        bundle = self.mutate(publication.prepare_publication, dict(id="publication-with-missing-reviews", files=files,
            claim_evidence=[dict(claim_id="bound", evidence=[self.result_evidence(self.execution_payload)])],
            citation_accounting=account_fixture_citations(self)))["result"]
        self.mutate(integration.update_study, {"stage": "evaluate"})
        self.mutate(review_protocol.record_assignment, dict(id="blocked-manuscript", route_id="managed-fixture-route",
            dossier_id=None, role="manuscript", reviewer_id="blocked-manuscript-reader", author_id="cycle-author",
            context=dict(bundle=bundle)))
        stop = self.stop("blocked-manuscript")
        for count in range(4):
            records = self.store.snapshot()["records"]
            self.assertTrue(strategy.managed_research(records))
            measurement = predictions.measurement_summary(records, bundle)
            self.assertEqual(measurement["complete"], count == 3)
            report = gate_state(records, self.artifacts, "publication")
            codes = {row["code"] for row in report["obligations"]}
            self.assertEqual("manuscript_reviews_required" in codes, count < 2)
            self.assertIn("research_post_measurement_decision_required", codes)
            if count < 3:
                assessor = "publication-reader-" + str(count)
                self.mutate(publication.record_manuscript_review, build_manuscript_review(self, bundle, assessor))
                self.mutate(predictions.record_prediction, build_prediction(self, bundle, assessor))
        self.assertIsNone(self.store.snapshot()["records"]["review_series_stop"][stop]["scope"])
        self.refuse(stop, "gate_override_internal_only")

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
        fixture = json.loads((ROOT / "tests/fixtures/phase_b_override_a04617f.json").read_text())
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            store = Store(root, create=True)
            (root / "context").mkdir()
            (root / "context/old-instruction.txt").write_text("Continue to local writing.")
            receipt = overrides.record_override(store, fixture["payload"], expected_revision=0,
                                                request_id="released-user-override")
            self.assertEqual(receipt, fixture["receipt"])
            with sqlite3.connect(root / ".exactory/research.sqlite3") as connection:
                event = connection.execute("SELECT operation,payload,changes,result FROM events").fetchone()
            self.assertEqual(list(event[:2]), fixture["event"][:2])
            # Runtime provenance describes the executing build and necessarily
            # differs. The released user record and response stay identical.
            self.assertEqual(json.loads(event[2])[0], json.loads(fixture["event"][2])[0])
            self.assertEqual(event[3], fixture["event"][3])
            self.assertEqual(Store(root).snapshot()["records"]["gate_override"]["old-user-override"],
                             fixture["receipt"]["result"])
            self.assertEqual(overrides.record_override(Store(root), fixture["payload"], expected_revision=0,
                             request_id="released-user-override"), fixture["receipt"])

    def test_released_080_opens_and_snapshots_but_status_rejects_automatic_override(self):
        from phase_b_compatibility import run_released
        self.auto(self.stop())
        before = self.store.snapshot()
        result = run_released('''
import json, sys
from pathlib import Path
from research_harness.storage import Store
from research_harness import cli
store = Store(Path(sys.argv[1]))
print(json.dumps(store.snapshot()), flush=True)
cli.main(['status', '--workspace', sys.argv[1]], render_views=lambda store: None)
''', self.root)
        self.assertEqual(json.loads(result.stdout.splitlines()[0]), before)
        # Compatibility finding: the released reader assumes every override
        # carries the user's instruction. Do not alter its code or new records
        # to conceal that refusal.
        self.assertEqual(result.returncode, 1)
        self.assertIn("KeyError: 'instruction'", result.stderr)
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
