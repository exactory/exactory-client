"""Publication readers enforce the recorded selection and retain its provenance."""

import json
from pathlib import Path
from unittest import mock

from research_harness import automatic_overrides, cli, output_views, overrides, problem_claims, problem_delivery
from research_harness.errors import ResearchError
from research_harness.evidence import digest
from test_gate_override import OverrideCommandMixin
import test_gate_override as override_fixtures
from test_override_instruction_parts import build_instruction_parts
from test_research_problem_claims import ProblemCase
import test_research_problem_openness_minimum as openness_fixtures


POST_ACTION = "POST /api/v1/math-problems"
OPEN_CONDITION = {"text": "Publish after an open finding.", "part": 1, "check": "openness_supported_open"}


class OverrideReaderCase(OverrideCommandMixin, ProblemCase):
    prepare_problem = override_fixtures.ProblemOverrideTests.prepare_problem

    def setUp(self):
        super().setUp()
        self.initialize_problem()
        self.write_instruction()
        self.selected = self.prepare_problem()
        self.parts = build_instruction_parts()
        self.parts["parts"][3]["reports"] = "conditional"
        (self.root / "context/instruction.json").write_text(json.dumps(self.parts))

    def build_conditional(self, identifier="conditional", **changes):
        return dict(self.build_override(identifier, gate="post"), instruction="context/instruction.json", **changes)

    def record_condition(self, **changes):
        return self.mutate(overrides.record_override, self.build_conditional(condition=OPEN_CONDITION, **changes))

    def record_lift(self):
        return self.mutate(overrides.record_override, self.build_conditional("lift", lifts=[
            dict(override_id="conditional", basis="after_report", part=4, quote=self.parts["parts"][4]["text"])]))

    def record_finding(self, outcome="supported_open"):
        search = openness_fixtures.ProblemOpennessMinimumTests.search(self)
        finding = openness_fixtures.ProblemOpennessMinimumTests.build_finding(self, [search], outcome=outcome)
        finding.update(id=outcome, statement_digest=problem_claims.compute_statement_digest(self.selected),
                       recorded_revision=self.store.revision + 1)
        self.put("problem_openness", outcome, finding)
        return finding

    def check_transport(self, gate="post", action=POST_ACTION):
        with mock.patch("pathlib.Path.cwd", return_value=self.root):
            return automatic_overrides.check_public_write(gate, public_action=action)

    def assert_condition_refused(self, action):
        with self.assertRaises(ResearchError) as caught:
            action()
        self.assertEqual(caught.exception.code, "gate_override_condition_unmet", caught.exception.as_dict())
        self.assertTrue(caught.exception.details["conditions"])
        return next(row for row in caught.exception.details["conditions"] if row["state"] not in ("met", "lifted"))


class OverrideReaderTests(OverrideReaderCase):
    def test_incident_export_refuses_before_lift_and_manifest_keeps_lifting_authority(self):
        self.record_condition()
        self.mutate(overrides.record_override, self.build_conditional("continue"))
        before = self.store.snapshot()
        condition = self.assert_condition_refused(lambda: problem_delivery.export_problem_override(self.store))
        self.assertEqual(condition["state"], "unmet")
        self.assertEqual(condition["obligations"][0]["code"], "problem_openness_missing")
        self.assertFalse((self.root / "final/problem/exports").exists())
        self.assertEqual(self.store.snapshot(), before)
        self.record_lift()
        revision = self.store.revision
        result = problem_delivery.export_problem_override(self.store)
        authority = result["authority"]
        self.assertEqual(authority["override_ids"], ["conditional", "continue", "lift"])
        self.assertEqual(authority["evaluated_at_revision"], revision)
        self.assertEqual(authority["conditions"][0]["state"], "lifted")
        self.assertEqual(authority["conditions"][0]["lifted_by"], ["lift"])
        directory = Path(result["destination"])
        self.assertEqual(json.loads((directory / "problem-export.json").read_bytes())["authority"], authority)
        output_views.render_all(self.store)
        self.assertTrue(directory.is_dir())

    def test_record_and_check_show_authority_without_putting_state_in_the_override(self):
        payload = self.build_conditional(condition=OPEN_CONDITION)
        before = self.read_workspace()
        response = self.invoke_command("gate-override", payload)
        self.assertEqual(response[0], 0, response)
        checked = json.loads(response[1])
        self.assertEqual(checked["write_authorities"][0]["conditions"][0]["state"], "unmet")
        self.assertEqual(self.read_workspace(), before)
        receipt = self.record_condition()
        self.assertEqual(receipt["write_authorities"][0]["conditions"][0]["state"], "unmet")
        stored = self.store.snapshot()["records"]["gate_override"]["conditional"]
        self.assertNotIn("write_authorities", stored)
        self.assertNotIn("state", stored["condition"])

    def test_status_next_and_summaries_show_authority_and_met_export_advisory(self):
        self.record_condition()
        self.record_finding()
        for command in ("status", "next"):
            for summary in (False, True):
                report = self.read_report(command, summary)
                self.assertTrue(report["override_export_authority"]["authorized"])
                self.assertEqual(report["overrides"][0]["condition"]["state"], "met")
                advisory, = [row for row in report["advisories"] if row["code"] == "gate_override_condition_met"]
                self.assertIn("export --kind problem-override", advisory["reason"])
        result = problem_delivery.export_problem_override(self.store)
        self.assertEqual(result["authority"]["conditions"][0]["evidence"][0]["id"], "supported_open")

    def test_public_write_checks_all_overlapping_conditions_without_an_automatic_pass(self):
        self.record_condition()
        self.mutate(overrides.record_override, self.build_conditional("exact", public_action=POST_ACTION))
        self.assertFalse((self.root / ".exactory/automatic-passes").exists())
        self.assert_condition_refused(self.check_transport)
        self.record_finding("unresolved")
        condition = self.assert_condition_refused(self.check_transport)
        self.assertEqual(condition["obligations"][0]["code"], "problem_openness_unresolved")
        self.record_finding()
        self.assertIsNone(self.check_transport())
        self.selected = self.select_statement(identifier="changed", statement="Does a cubic bound hold?")
        self.assert_condition_refused(self.check_transport)
        self.record_lift()
        with mock.patch("research_harness.problem_openness.assess_openness", side_effect=AssertionError("lifted check ran")):
            self.assertIsNone(self.check_transport())

    def test_exact_action_condition_is_enforced_at_every_transport_gate(self):
        self.record_condition(public_action="POST /api/v1/submissions")
        for gate in (None, "post", "submitted", "verification", "publication"):
            self.assert_condition_refused(lambda: self.check_transport(gate, "POST /api/v1/submissions"))
        self.assertIsNone(self.check_transport("submitted", "POST /unrelated"))

    def test_local_unknown_and_user_decision_conditions_refuse_and_explicit_lifts_allow(self):
        overrides.append_unrecorded_override(self.root, self.build_conditional(condition=OPEN_CONDITION),
            expected_revision=self.store.revision, request_id="local-condition")
        from research_harness.storage import Store
        with mock.patch.object(Store, "read_kinds", side_effect=ResearchError("storage_io", "Unreadable store")):
            condition = self.assert_condition_refused(self.check_transport)
        self.assertEqual(condition["state"], "unknown")
        overrides.append_unrecorded_override(self.root, self.build_conditional("local-lift", lifts=[
            dict(override_id="conditional", basis="after_report", part=4, quote=self.parts["parts"][4]["text"])]),
            expected_revision=self.store.revision, request_id="local-lift")
        with mock.patch.object(Store, "read_kinds", side_effect=ResearchError("storage_io", "Unreadable store")):
            self.assertIsNone(self.check_transport())
        self.mutate(overrides.record_override, self.build_conditional("manual", condition=dict(OPEN_CONDITION, check=None)))
        condition = self.assert_condition_refused(self.check_transport)
        self.assertEqual(condition["state"], "user_decides")

    def test_authority_exports_retire_on_new_condition_and_keep_their_bytes(self):
        self.mutate(overrides.record_override, self.build_conditional("initial"))
        exported = problem_delivery.export_problem_override(self.store)
        self.assertIn("authority", exported)
        first = Path(exported["destination"])
        files = {path.name: path.read_bytes() for path in first.iterdir()}
        self.record_condition()
        output_views.render_all(self.store)
        historical = self.root / "final/history/problem/exports" / first.name
        self.assertFalse(first.exists())
        self.assertEqual({path.name: path.read_bytes() for path in historical.iterdir()}, files)

    def test_stored_0853_projection_and_override_digest_stay_unchanged(self):
        record = self.mutate(overrides.record_override, self.build_conditional("legacy"))["result"]
        records = self.store.snapshot()["records"]
        original = digest(record)
        self.assertIn("authority", problem_delivery.export_problem_override(self.store))
        files, manifest = problem_delivery.build_override_projection(records, self.artifacts, record)
        self.assertNotIn("authority", manifest)
        directory = self.root / "final/problem/exports/legacy"
        directory.mkdir(parents=True)
        for name, data in files.items():
            (directory / name).write_bytes(data)
        output_views.render_all(self.store)
        self.assertEqual({path.name: path.read_bytes() for path in directory.iterdir()}, files)
        self.assertEqual(digest(self.store.snapshot()["records"]["gate_override"]["legacy"]), original)

    def test_export_blocks_local_and_user_decision_conditions_with_their_current_states(self):
        self.mutate(overrides.record_override, self.build_conditional("cover"))
        overrides.append_unrecorded_override(self.root, self.build_conditional("local", condition=OPEN_CONDITION),
            expected_revision=self.store.revision, request_id="local")
        condition = self.assert_condition_refused(lambda: problem_delivery.export_problem_override(self.store))
        self.assertEqual(condition["override_id"], "local")
        self.record_finding()
        self.mutate(overrides.record_override, self.build_conditional("manual", condition=dict(OPEN_CONDITION, check=None)))
        condition = self.assert_condition_refused(lambda: problem_delivery.export_problem_override(self.store))
        self.assertEqual(condition["state"], "user_decides")

    def test_new_statement_export_retains_earlier_condition_and_cites_current_cover(self):
        self.record_condition()
        self.selected = self.select_statement(identifier="changed", statement="Does a cubic bound hold?")
        reading = self.store.snapshot()["records"]["reading"]["read-full"]
        evidence = {"kind": "reading", "version_id": reading["version_id"], "reading_id": reading["id"]}
        self.mutate(problem_claims.record_value, dict(self.build_value([evidence], identifier="new-value"), statement_id=self.selected["id"]))
        self.mutate(overrides.record_override, self.build_conditional("new-text"))
        condition = self.assert_condition_refused(lambda: problem_delivery.export_problem_override(self.store))
        self.assertEqual(condition["override_id"], "conditional")
        self.record_lift()
        result = problem_delivery.export_problem_override(self.store)
        self.assertEqual(result["authority"]["override_ids"], ["conditional", "new-text", "lift"])

    def test_all_public_transport_entrypoints_refuse_before_sending(self):
        from test_draft import _load_bin_module
        from urllib.request import Request
        api = _load_bin_module("exactory", "override_reader_api")
        draft = _load_bin_module("exactory-draft", "override_reader_draft")
        paths = ["/api/v1/math-problems", "/api/v1/verifications",
                 "/api/v1/verifications/fixture/verdicts", "/api/deposit/depositions"]
        for index, path in enumerate(paths):
            self.mutate(overrides.record_override, self.build_conditional("action-" + str(index),
                condition=OPEN_CONDITION, public_action="POST " + path))
        with mock.patch("pathlib.Path.cwd", return_value=self.root):
            for path in paths[:3]:
                with mock.patch.object(api, "open_url_within_origin", side_effect=AssertionError("write sent")):
                    self.assert_condition_refused(lambda: api._send_request("POST", path, {}, authenticated=False))
            with mock.patch.object(draft, "open_url_within_origin", side_effect=AssertionError("deposit sent")):
                self.assert_condition_refused(lambda: draft._open_url(Request(
                    "https://example.org" + paths[3], data=b"{}", method="POST")))

    def test_snapshot_failure_retains_a_discovered_standing_condition_as_unknown(self):
        self.record_condition()
        from research_harness.storage import Store
        with mock.patch.object(Store, "snapshot", side_effect=ResearchError("storage_io", "Unreadable history")):
            condition = self.assert_condition_refused(self.check_transport)
        self.assertEqual(condition["state"], "unknown")

    def test_summary_keeps_lift_part_index_and_bounds_many_condition_previews(self):
        self.record_condition()
        self.record_lift()
        from research_harness.report_views import summarize_overrides
        shown = summarize_overrides(cli.status_report(self.store))
        self.assertEqual(shown["overrides"][1]["lifts"][0]["part"], 4)
        for index in range(4):
            self.mutate(overrides.record_override, self.build_conditional("manual-" + str(index),
                condition=dict(OPEN_CONDITION, check=None)))
        for command, limit in (("status", 16384), ("next", 4096)):
            shown = self.read_report(command, True)
            self.assertFalse(shown["override_export_authority"]["authorized"])
            self.assertLessEqual(len(json.dumps(shown, ensure_ascii=False, indent=2).encode()), limit)

    def test_managed_precheck_defers_to_the_exact_action_that_lifts_a_gate_condition(self):
        self.record_condition()
        self.mutate(overrides.record_override, self.build_conditional("exact-lift", public_action=POST_ACTION,
            lifts=[dict(override_id="conditional", basis="after_report", part=4,
                        quote=self.parts["parts"][4]["text"])]))
        store, report, error = cli.select_managed_path(lambda opened: "ready to choose a request", self.root,
                                                      public_gate="post")
        self.assertEqual(store.root, self.store.root)
        self.assertEqual(report, "ready to choose a request")
        self.assertIsNone(error)
        self.assertIsNone(self.check_transport())
        self.assert_condition_refused(lambda: self.check_transport(action="POST /another-action"))

    def test_summary_bounds_a_long_finding_identifier_in_condition_evidence(self):
        self.record_condition()
        finding = self.record_finding()
        finding = dict(finding, id="f" * 20000, recorded_revision=self.store.revision + 1)
        self.put("problem_openness", finding["id"], finding)
        for command, limit in (("status", 16384), ("next", 4096)):
            shown = self.read_report(command, True)
            self.assertTrue(shown["override_export_authority"]["authorized"])
            self.assertLessEqual(len(json.dumps(shown, ensure_ascii=False, indent=2).encode()), limit)

    def test_local_fallback_receipt_shows_unknown_and_explicitly_lifted_conditions(self):
        from research_harness.storage import Store
        with mock.patch.object(Store, "snapshot", side_effect=ResearchError("storage_io", "Unreadable store")):
            receipt = overrides.append_unrecorded_override(self.root, self.build_conditional(condition=OPEN_CONDITION),
                expected_revision=self.store.revision, request_id="local-condition")
            self.assertEqual(receipt["write_authorities"][0]["conditions"][0]["state"], "unknown")
            receipt = overrides.append_unrecorded_override(self.root, self.build_conditional("local-lift", lifts=[
                dict(override_id="conditional", basis="after_report", part=4, quote=self.parts["parts"][4]["text"])]),
                expected_revision=self.store.revision, request_id="local-lift")
            self.assertEqual(receipt["write_authorities"][0]["conditions"][0]["state"], "lifted")

    def test_committed_override_keeps_its_receipt_when_authority_observation_cannot_read_store(self):
        with mock.patch.object(self.store, "snapshot", side_effect=ResearchError("storage_io", "Read failed after commit")):
            receipt = self.record_condition()
        self.assertEqual(receipt["revision"], self.store.revision)
        self.assertEqual(receipt["write_authorities"][0]["conditions"][0]["state"], "unknown")
        self.assertEqual(self.store.snapshot()["records"]["gate_override"]["conditional"], receipt["result"])
        self.assertIsNone(overrides.read_unrecorded_overrides(self.root))

    def test_failed_receipt_read_keeps_other_standing_conditions_on_commit_and_replay(self):
        self.record_condition()
        self.mutate(overrides.record_override, self.build_conditional("second", condition=OPEN_CONDITION))
        payload = self.build_conditional("lift", lifts=[dict(override_id="conditional", basis="after_report", part=4,
                                                            quote=self.parts["parts"][4]["text"])])
        revision = self.store.revision
        request = "receipt-with-two-conditions"
        with mock.patch.object(self.store, "snapshot", side_effect=ResearchError("storage_io", "Read failed after commit")):
            for index in range(2):
                receipt = overrides.record_override(self.store, payload, expected_revision=revision, request_id=request)
                authority = receipt["write_authorities"][0]
                self.assertFalse(authority["authorized"])
                self.assertFalse(authority["available"])
                if index == 0:
                    self.assertEqual([(row["override_id"], row["state"]) for row in authority["conditions"]],
                                     [("conditional", "lifted"), ("second", "unknown")])
                else:
                    self.assertFalse(authority["covered"])

    def test_next_summary_bounds_four_byte_instruction_condition_and_lifting_identifiers(self):
        symbol = "\U0001f680"
        identifier = symbol * 120
        path = self.root / "context/instruction.txt"
        path.write_text(symbol * 120)
        self.mutate(overrides.record_override, self.build_override(identifier, gate="post", stop_code=symbol * 48,
            condition=dict(text=symbol * 120, part=0, check=None)))
        quote = "Publish without the stated condition."
        path.write_text(quote)
        for index in range(3):
            self.mutate(overrides.record_override, self.build_override(symbol * 96 + str(index), gate="post",
                lifts=[dict(override_id=identifier, basis="named", part=0, quote=quote)]))
        shown = self.read_report("next", True)
        self.assertLessEqual(len(json.dumps(shown, ensure_ascii=False, indent=2).encode()), 4096)
        self.assertEqual(shown["overrides"][0]["condition"]["state"], "lifted")

    def test_condition_refusal_clears_a_remote_claim_and_a_lift_allows_one_retry(self):
        from research_harness.remote import begin_intent, get_intent, remote_step
        self.record_condition()
        intent = begin_intent(self.store, "deposit", {}, expected_revision=self.store.revision,
                              request_id="conditional-deposit")
        sent = []
        def perform():
            self.check_transport()
            sent.append("POST")
            return {"id": 42}
        self.assert_condition_refused(lambda: remote_step(self.store, intent["id"], "create", {}, perform))
        stopped = get_intent(self.store, intent["id"])
        self.assertIsNone(stopped["pending"])
        self.assertEqual(stopped["status"], "prepared")
        self.assertEqual(stopped["responses"], {})
        self.assertEqual(stopped["discarded"][-1]["observation"],
                         {"kind": "local_authority_refusal", "code": "gate_override_condition_unmet"})
        self.assertEqual(sent, [])
        self.record_lift()
        self.assertEqual(remote_step(self.store, intent["id"], "create", {}, perform), {"id": 42})
        self.assertEqual(sent, ["POST"])
