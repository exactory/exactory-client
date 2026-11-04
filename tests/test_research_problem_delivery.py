"""The post is a record-authoritative projection of observed problem evidence."""

import json
from pathlib import Path
import unittest
from unittest import mock
from unittest.mock import patch

from test_research_problem_candidates import build_candidate, build_slate
from test_research_problem_claims import ProblemCase
import test_research_problem_reviews as reviewers
from research_harness import problem_candidates, research_decisions, review_protocol
from research_harness.cli import status_report
from research_harness.errors import ResearchError
from research_harness.gates import gate_state
from research_harness.evidence import digest
from test_research_problem_citation_metadata import CitationMetadataCase


def build_staged_delivery(origin="execution", role="result", recovery=None):
    from research_harness import review_composition, review_staged
    from test_review_staged_fixtures import epoch
    frozen = epoch(role=role, **({"focused_scope": {"review_pair_roots": ["review-A", "review-B"],
        "objection_id": "O7", "objection_version": "version-7"}} if role == "adjudicator" else {}))
    completion = review_staged.verify_completion(frozen, {"observations": []})
    next_action = {"workflow": "review-assignment", "scope": {"claim_ids": ["c"]},
                   "prerequisite": "new_evidence"}
    recovery = dict({"workflow": "review-assignment", "command": "review-assignment",
                     "payload": {"claim_ids": ["c"]}, "text": "Supply a proof for claim c."},
                    **(recovery or {}))
    outcome = review_staged.record_incomplete(frozen, completion, origin=origin,
        origin_binding=({"execution_receipts": ["terminated"], "reason": "exhausted"} if origin == "execution"
                        else {"attempt_id": "terminal", "raw_output_digest": digest({"unfinished": True})}),
        unresolved_scope=[{"id": "missing", "scope": {"claim_ids": ["c"]}, "cause": "rereads exhausted",
                           "affected_decisions": ["problem_value"], "exhausted_tasks": ["exhausted"],
                           "scope_kind": "unresolved", "next_action": next_action}],
        recovery_work=[recovery], resource_ledger={"attempts": 20})
    effective = review_composition.effective_review_output("assignment-A", {
        "controller_id": "assignment-A", "kind": "incomplete", "outcome": outcome})
    assignment = {"id": "assignment-A", "role": role, "slot_id": "A", "route_id": "route-A",
                  "reviewer_id": "reviewer-A", "author_id": "author", "context": {}, "recorded_revision": 2}
    state = {"ready": False, "output": None, "raw_output": {"assessment_incomplete": {"unfinished": True}},
             "effective": effective, "epoch_id": frozen["id"], "obligations": [], "refused_attempts": []}
    return frozen, assignment, state, outcome


class StagedDeliveryTests(unittest.TestCase):
    def test_incomplete_advice_cannot_repeat_exhausted_work_through_a_workflow_alias(self):
        from research_harness.problem_delivery import assess_source_requests
        frozen, assignment, state, outcome = build_staged_delivery(recovery={"workflow": "review-run",
            "payload": {"task_id": "exhausted"}, "text": "Repeat the exhausted task."})
        records = {"review_assignment": {assignment["id"]: assignment}, "review_epoch": {frozen["id"]: frozen}}
        with mock.patch.object(review_protocol, "assignment_state", return_value=state):
            item = next(row for row in assess_source_requests(records, object()) if row["code"] == "assessment_incomplete")
        self.assertTrue(item["next"])
        self.assertFalse(any(action.get("workflow") == "review-run" or action.get("command") == "review-run" for action in item["next"]))
        self.assertEqual(item["unresolved_scope"], outcome["unresolved_scope"])
    def test_completed_negative_next_names_the_value_claim_research_and_upheld_objection(self):
        from research_harness import problem_delivery
        records = {"value_review": {"R": {"objection_identities": {"O7": "R:O7"}, "payload": {"value": {"assurances": [
            {"kind": "problem_value", "status": "failed", "reason": "The evidence supports prime inputs only."}]},
            "objections": [{"id": "O7", "resolution_condition": "Prove the claimed bound for composite inputs."}]}}}}
        pending = [{"code": "problem_assurance_pending", "review_id": "R", "assurances": ["problem_value"]},
                   {"code": "research_objection_unresolved", "objection_id": "R:O7"}]
        work = problem_delivery.describe_problem_work(records, pending)
        self.assertEqual(work[0]["next"][0]["workflow"], "problem-value")
        self.assertIn("prime inputs", work[0]["next"][0]["text"])
        self.assertEqual(work[1]["next"][0]["scope"], {"objection_id": "R:O7"})
        self.assertIn("composite inputs", work[1]["next"][0]["prerequisite"])
        self.assertNotIn("review-run", json.dumps(work))

    def test_incomplete_occupies_its_slot_and_names_research_instead_of_another_call(self):
        from research_harness.problem_delivery import assess_source_requests
        frozen, assignment, state, outcome = build_staged_delivery()
        records = {"review_assignment": {assignment["id"]: assignment}, "review_epoch": {frozen["id"]: frozen}}
        with mock.patch.object(review_protocol, "assignment_state", return_value=state):
            pending = assess_source_requests(records, object())
        incomplete = next(row for row in pending if row["code"] == "assessment_incomplete")
        self.assertEqual(incomplete["slot_id"], "A")
        self.assertEqual(incomplete["origin"], "execution")
        self.assertEqual(incomplete["outcome_id"], outcome["id"])
        self.assertEqual(incomplete["next"], outcome["recovery_work"])
        self.assertNotIn("review-run", json.dumps(incomplete["next"]))

    def test_completed_staged_review_still_requires_configuration_measurement(self):
        from research_harness.problem_delivery import assess_source_requests
        frozen, assignment, state, _ = build_staged_delivery()
        state["effective"] = {"kind": "ordinary", "effective_output": {"value": {"status": "sufficient"}},
                              "completion_root": "completed"}
        records = {"review_assignment": {assignment["id"]: assignment}, "review_epoch": {frozen["id"]: frozen}}
        with mock.patch.object(review_protocol, "assignment_state", return_value=state):
            pending = assess_source_requests(records, object())
        qualification = next(row for row in pending if row["code"] == "review_quality_measurement_pending")
        self.assertEqual(qualification["epoch_id"], frozen["id"])
        self.assertEqual(qualification["route_id"], frozen["route_id"])

    def test_pending_successor_keeps_earlier_favorable_output_out_of_current_results(self):
        from research_harness.problem_delivery import assess_source_requests
        frozen, assignment, state, _ = build_staged_delivery()
        state.update(effective={"kind": "pending"}, raw_output=None)
        records = {"review_assignment": {assignment["id"]: assignment}, "review_epoch": {frozen["id"]: frozen}}
        with mock.patch.object(review_protocol, "assignment_state", return_value=state):
            pending = assess_source_requests(records, object())
        self.assertIn("review_task_pending", {row["code"] for row in pending})

    def test_adjudication_incomplete_has_no_scientific_disposition(self):
        from research_harness.problem_delivery import assess_source_requests
        frozen, assignment, state, outcome = build_staged_delivery(origin="reviewer", role="adjudicator")
        records = {"review_assignment": {assignment["id"]: assignment}, "review_epoch": {frozen["id"]: frozen}}
        with mock.patch.object(review_protocol, "assignment_state", return_value=state):
            pending = assess_source_requests(records, object())
        incomplete = next(row for row in pending if row["code"] == "assessment_incomplete")
        self.assertEqual(incomplete["role"], "adjudicator")
        self.assertNotIn("disposition", incomplete)

    def test_projection_uses_effective_reviews_and_retains_terminal_binding(self):
        from research_harness import problem_delivery
        frozen, assignment, state, _ = build_staged_delivery()
        scientific = {"stage": "result", "value": {"reason": "Only prime inputs are supported."}}
        state["effective"] = {"kind": "paged", "effective_output": scientific,
            "effective_output_digest": digest(scientific), "composition_root": "root",
            "terminal_attempt_id": "terminal", "terminal_raw_output_digest": digest({"terminal": True})}
        records = {"review_assignment": {assignment["id"]: assignment}, "review_epoch": {frozen["id"]: frozen},
                   "value_review": {"R": {"id": "R", "assignment_id": assignment["id"],
                       "payload": dict(scientific, id="R", assignment_id=assignment["id"], dossier_id=None)}}}
        statement = {"id": "statement", "title": "Problem", "statement": "Exact claim", "resolution_criteria": "Proof",
                     "area": "math", "formal_statement": None}
        with mock.patch.object(review_protocol, "assignment_state", return_value=state), \
             mock.patch.object(problem_delivery, "find_current_statement", return_value=statement), \
             mock.patch.object(problem_delivery, "find_value", return_value={"motivation": [], "current_state": []}), \
             mock.patch.object(problem_delivery, "find_latest", return_value={"id": "decision", "review_ids": ["R"]}), \
             mock.patch.object(problem_delivery, "project_citations", return_value=[]), \
             mock.patch.object(problem_delivery, "compute_statement_digest", return_value="statement-digest"), \
             mock.patch.object(problem_delivery, "compute_problem_evidence_digest", return_value="evidence"):
            files, manifest = problem_delivery.build_projection(records, object())
        exported = json.loads(files["reviews.json"])
        self.assertEqual(exported[0]["output"], scientific)
        self.assertEqual(exported[0]["binding"]["composition_root"], "root")
        self.assertEqual(exported[0]["binding"]["terminal_attempt_id"], "terminal")
        self.assertIn("reviews.json", manifest["files"])


class ProblemDeliveryCase(ProblemCase):
    prepare_review_route = reviewers.ProblemReviewTests.prepare_review_route
    build_provider_response = reviewers.ProblemReviewTests.build_provider_response
    build_review_output = reviewers.ProblemReviewTests.build_review_output
    observe = reviewers.ProblemReviewTests.observe
    deliver_supporting = reviewers.ProblemReviewTests.deliver_supporting
    observe_openness = reviewers.ProblemOpennessReviewTests.observe_openness

    def assign(self, role, reviewer, author="problem-author", identifier=None, **context):
        return self.mutate(review_protocol.record_assignment, {"id": identifier or role + "-" + reviewer,
            "route_id": "route", "dossier_id": None, "role": role, "reviewer_id": reviewer,
            "author_id": author, "context": context})["result"]

    def record_candidate(self, identifier="candidate-1", **changes):
        return self.mutate(problem_candidates.record_candidate, build_candidate(identifier, **changes))["result"]

    def record_slate(self, identifier="slate-1", **changes):
        return self.mutate(problem_candidates.record_slate, build_slate(identifier, **changes))["result"]

    def observe_bars(self):
        self.supporting = self.artifacts.put(b"Field evidence used by the authored review fixture.", "text/plain")
        self.prepare_review_route()
        for reviewer in ("first", "second"):
            self.observe("bar", reviewer)

    def prepare_ready(self, formal=False, solver=False):
        self.initialize_problem()
        self.record_candidate()
        self.observe_bars()
        self.record_slate()
        self.selected = self.select_statement(candidate_id="candidate-1",
            formal_statement={"language": "Lean", "text": "theorem bound : True := by trivial"} if formal else None)
        item = self.record_full_reading()
        from research_harness.problem_attempt import record_attempt
        from test_research_problem_attempt import build_attempt, record_solver_attempt
        if solver:
            record_solver_attempt(self, self.selected)
        else:
            self.mutate(record_attempt, build_attempt(reading=item))
        self.observe_openness()
        self.mutate(self.load_problem_module().record_openness, {"assignment_id": "openness-assignment"})
        self.mutate(self.load_problem_module().record_value, self.build_value([item]))
        from test_research_problem_assessment import build_assessment
        self.mutate(self.load_problem_module().record_assessment, build_assessment(self.selected))
        for reviewer in ("first", "second"):
            self.observe("result", reviewer)

    def record_post_decision(self, identifier="decision"):
        return self.mutate(self.load_problem_module().record_decision, {"id": identifier, "action": "post",
            "review_ids": ["review-result-first", "review-result-second"],
            "bar_review_ids": ["review-bar-first", "review-bar-second"], "reason": "Both independent findings support the exact evidence."})["result"]


class CitationMetadataDeliveryTests(CitationMetadataCase, ProblemDeliveryCase):
    def test_actual_result_delivery_supports_citing_the_nested_metadata_record(self):
        from research_harness.problem_evidence import describe_evidence
        from research_harness.problem_claims import compute_problem_evidence_digest
        self.record_metadata()
        self.observe_bars()
        evidence = describe_evidence(self.store.snapshot()["records"], self.artifacts)
        metadata = evidence["readings"][0]["citation_metadata"]
        output = self.build_review_output("result")
        output["value"]["evidence"] = [metadata["cite"]]
        review = self.observe("result", "first", output)
        records = self.store.snapshot()["records"]
        packet = json.loads(self.artifacts.read(records["review_assignment"][review["assignment_id"]]["packet"]))
        self.assertEqual(packet["problem_evidence"]["readings"][0]["citation_metadata"], metadata)
        self.assertEqual(research_decisions.find_undelivered_review_references(records, self.artifacts, review), [])
        self.assertEqual(research_decisions._assess_problem_coverage(records, self.artifacts, [review], evidence,
            compute_problem_evidence_digest(records, self.artifacts)), [])
        # Only the server-derived excerpts travel; the README response stays held.
        self.assertNotIn(self.response_source["response"]["sha256"],
                         {row["artifact"]["sha256"] for row in packet["evidence"]})

    def check_incorrect_metadata_delivery(self, omit):
        from research_harness.problem_evidence import describe_evidence
        from research_harness.problem_claims import compute_problem_evidence_digest
        self.record_metadata()
        self.observe_bars()
        original = review_protocol.add_problem_evidence
        def change_metadata(records, artifacts, packet):
            original(records, artifacts, packet)
            reading = packet["problem_evidence"]["readings"][0]
            if omit:
                reading.pop("citation_metadata")
            else:
                reading["citation_metadata"]["evidence"]["title"]["start"] += 1
        with patch.object(review_protocol, "add_problem_evidence", side_effect=change_metadata):
            review = self.observe("result", "first")
        records = self.store.snapshot()["records"]
        pending = research_decisions._assess_problem_coverage(records, self.artifacts, [review],
            describe_evidence(records, self.artifacts), compute_problem_evidence_digest(records, self.artifacts))
        self.assertEqual([row["code"] for row in pending], ["problem_evidence_unassessed"])

    def test_omitted_metadata_does_not_cover_the_current_evidence(self):
        self.check_incorrect_metadata_delivery(omit=True)

    def test_altered_metadata_does_not_cover_the_current_evidence(self):
        self.check_incorrect_metadata_delivery(omit=False)


class ProblemDeliveryTests(ProblemDeliveryCase):
    def test_post_decision_export_and_posted_are_bound_to_the_observed_claim(self):
        from research_harness.problem_delivery import export_problem
        self.prepare_ready(formal=True)
        self.assertEqual(status_report(self.store)["milestone"], "openness")
        self.assertEqual(status_report(self.store)["next"]["code"], "problem_decision_missing")
        decision = self.record_post_decision()
        report = status_report(self.store)
        self.assertTrue(report["ready"], report["obligations"])
        self.assertEqual(report["milestone"], "value")
        destination = self.root / "post"
        exported = export_problem(self.store, destination)
        manifest = json.loads((destination / "problem-export.json").read_text())
        self.assertEqual(manifest["decision_id"], decision["id"])
        self.assertEqual(manifest["statement_digest"], self.load_problem_module().compute_statement_digest(self.selected))
        self.assertEqual(manifest["formal_statement_language"], "Lean")
        self.assertEqual(set(manifest["files"]), {"title.txt", "statement.md", "motivation.md", "state.md", "criteria.md", "citations.json", "formal-statement.txt"})
        self.assertEqual((destination / "statement.md").read_text(), self.selected["statement"] + "\n")
        self.assertTrue(json.loads((destination / "citations.json").read_text())[0]["locator"].startswith("2601."))
        post = self.mutate(self.load_problem_module().record_posted, {"problem_id": "posted-id", "export_directory": str(destination)})["result"]
        self.assertEqual(post["statement_id"], self.selected["id"])
        self.assertEqual(status_report(self.store)["milestone"], "post")
        self.assert_error("problem_already_posted", lambda: self.mutate(self.load_problem_module().record_posted,
            {"problem_id": "again", "export_directory": str(destination)}))

    def test_export_refuses_until_post_gate_is_ready(self):
        from research_harness.problem_delivery import export_problem
        self.initialize_problem()
        self.assert_error("readiness_required", lambda: export_problem(self.store, self.root / "not-ready"))

    def test_exported_parts_and_manifest_are_never_the_authority(self):
        from research_harness.problem_delivery import export_problem
        self.prepare_ready()
        self.record_post_decision()
        directory = self.root / "post"
        export_problem(self.store, directory)
        (directory / "statement.md").write_text("An unreviewed claim.\n")
        self.assert_error("problem_export_mismatch", lambda: self.mutate(self.load_problem_module().record_posted,
            {"problem_id": "changed", "export_directory": str(directory)}))
        import hashlib
        manifest = json.loads((directory / "problem-export.json").read_text())
        manifest["files"]["statement.md"] = hashlib.sha256((directory / "statement.md").read_bytes()).hexdigest()
        (directory / "problem-export.json").write_text(json.dumps(manifest))
        self.assert_error("problem_export_mismatch", lambda: self.mutate(self.load_problem_module().record_posted,
            {"problem_id": "forged-manifest", "export_directory": str(directory)}))

    def test_export_defaults_to_final_and_reuses_an_identical_export(self):
        from research_harness.problem_delivery import export_problem
        self.prepare_ready()
        self.record_post_decision()
        exported = export_problem(self.store)
        directory = Path(exported["destination"])
        self.assertEqual(directory.parent, self.store.root / "final/problem/exports")
        self.assertEqual(json.loads((directory / "problem-export.json").read_text())["files"], exported["files"])
        self.assertEqual(export_problem(self.store), exported)
        # The library still exports anywhere, and an identical export there is returned as it is too.
        elsewhere = self.root.parent / "elsewhere"
        self.assertEqual(export_problem(self.store, elsewhere)["destination"], str(elsewhere))
        self.assertEqual(export_problem(self.store, elsewhere)["destination"], str(elsewhere))
        (elsewhere / "statement.md").write_text("An unreviewed claim.\n")
        self.assert_error("review_destination_exists", lambda: export_problem(self.store, elsewhere))
        self.assertEqual((elsewhere / "statement.md").read_text(), "An unreviewed claim.\n")
        # A dot file that a file browser adds leaves the exported set as it is, and the folder is named by the
        # export digest that the post records.
        (directory / ".DS_Store").write_bytes(b"\x00\x01")
        post = self.mutate(self.load_problem_module().record_posted,
                           {"problem_id": "posted-id", "export_directory": str(directory)})["result"]
        self.assertEqual(post["export_digest"], directory.name)

    def test_the_cli_exports_the_problem_only_inside_the_exports_folder(self):
        from research_harness import cli
        self.prepare_ready()
        self.record_post_decision()
        parser = cli.build_parser()

        def export(*arguments):
            return cli.run(parser.parse_args(["export", "--workspace", str(self.root), "--kind", "problem", *arguments]))

        folder = self.store.root / "final/problem/exports"
        for outside in (self.root / "problem-export", self.root / "final/../problem-export", self.root / "finally",
                        self.root.parent / "elsewhere", self.root / "final/notes", self.root / "final/problem/exports",
                        self.root / "final/problem/exports-old/x", self.root / "final/problem/exports/../x"):
            with self.subTest(destination=outside):
                with self.assertRaises(ResearchError) as caught:
                    export("--destination", str(outside))
                self.assertEqual(caught.exception.code, "export_destination_outside_final")
                self.assertEqual(caught.exception.message,
                                 "Leave out --destination, or give a directory under final/problem/exports/ of the "
                                 "workspace. Without --destination the export goes to final/problem/exports/<export "
                                 "digest>/.")
                self.assertEqual(caught.exception.details, {"destination": str(outside), "folder": str(folder)})
                self.assertFalse(outside.exists())
        exported = export()
        self.assertEqual(Path(exported["destination"]).parent, self.store.root / "final/problem/exports")
        chosen = self.root / "final/problem/exports/chosen"
        self.assertEqual(export("--destination", str(chosen))["destination"], str(chosen))
        self.assertEqual((chosen / "statement.md").read_bytes(),
                         (Path(exported["destination"]) / "statement.md").read_bytes())

    def test_statement_selection_makes_prior_finding_and_decision_stale(self):
        self.prepare_ready()
        self.record_post_decision()
        self.select_statement(identifier="statement-2", statement="A changed exact mathematical claim.")
        report = status_report(self.store)
        codes = {item["code"] for item in report["obligations"]}
        self.assertIn("problem_openness_stale", codes)
        self.assertIn("problem_decision_stale", codes)
        self.assertEqual(report["milestone"], "formulation")

    def test_decision_refuses_missing_observed_pair_then_records(self):
        self.prepare_ready()
        self.assert_error("problem_review_pair_required", lambda: self.mutate(self.load_problem_module().record_decision, {
            "id": "bad", "action": "post", "review_ids": ["review-result-first"],
            "bar_review_ids": ["review-bar-first", "review-bar-second"], "reason": "One result is missing."}))
        self.assertEqual(self.record_post_decision()["action"], "post")

    def test_gate_and_status_are_read_only_and_extra_unused_readings_keep_currency(self):
        self.prepare_ready()
        self.record_post_decision()
        before = self.store.snapshot()
        first = status_report(self.store)
        self.assertTrue(first["ready"], first["obligations"])
        self.assertEqual(self.store.snapshot(), before)
        version = next(iter(before["records"]["work"]))
        from research_harness.reading import record_reading
        self.mutate(record_reading, self.abstract_note(version, "unused-abstract"))
        current = status_report(self.store)
        self.assertTrue(current["ready"], current["obligations"])
        self.assertEqual(first["problem_evidence_digest"], current["problem_evidence_digest"])
