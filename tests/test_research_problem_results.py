"""Only current accepted solver results provide mathematical value evidence."""

import copy
import json
from unittest.mock import patch

from research_harness import problem_attempt, problem_evidence, research_decisions, review_protocol
from research_harness.errors import ResearchError
from research_harness.evidence import digest
from test_research_problem_attempt import build_acceptance, build_attempt, build_native_attempt, put_native_attempt
from test_research_problem_claims import ProblemCase
from test_research_problem_delivery import ProblemDeliveryCase


class ProblemResultTests(ProblemCase):
    def setUp(self):
        super().setUp()
        self.initialize_problem()
        self.statement = self.select_statement()

    def bind_result(self, acceptance, identifier="attempt-1"):
        native = build_native_attempt(self.statement["statement"], [acceptance], {})
        payload = build_attempt(identifier, put_native_attempt(self, native))
        self.mutate(problem_attempt.record_attempt, payload)
        return {"kind": "result", "attempt_id": identifier, "acceptance_id": acceptance["id"]}

    def test_result_evidence_accepted_for_an_accepted_reduction(self):
        for outcome, obligation_id in (("proof", "obligation-000002"), ("reduction", "obligation-000002"),
                                       ("obstruction", "obligation-000002"), ("reduction", "obligation-000001")):
            identifier = outcome + obligation_id
            item = self.bind_result(build_acceptance(outcome=outcome, obligation_id=obligation_id), "attempt-" + identifier)
            saved = self.mutate(self.load_problem_module().record_value, self.build_value([item], "value-" + identifier))["result"]
            self.assertEqual(saved["motivation"][0]["evidence"], [item])

    def test_result_evidence_refused_for_an_invalidated_or_hypothesis_acceptance(self):
        for status, outcome in (("invalidated", "reduction"), ("hypothesis", "reduction"),
                                ("accepted", "hypothesis"), ("accepted", "counterexample")):
            with self.subTest(status=status, outcome=outcome):
                item = self.bind_result(build_acceptance(status=status, outcome=outcome), status + outcome)
                with self.assertRaises(ResearchError) as caught:
                    self.mutate(self.load_problem_module().record_value, self.build_value([item], status + outcome))
                self.assertEqual(caught.exception.code, "problem_value_result_missing")
                self.assertEqual(caught.exception.details, {key: item[key] for key in ("attempt_id", "acceptance_id")})
        item = self.bind_result(build_acceptance(), "current")
        item["acceptance_id"] = "absent"
        with self.assertRaises(ResearchError) as caught:
            self.mutate(self.load_problem_module().record_value, self.build_value([item], "absent-acceptance"))
        self.assertEqual(caught.exception.code, "problem_value_result_missing")
        self.assertEqual(caught.exception.details, {"attempt_id": "current", "acceptance_id": "absent"})

    def test_previous_statement_attempt_and_results_are_not_projected(self):
        item = self.bind_result(build_acceptance())
        self.mutate(self.load_problem_module().record_value, self.build_value([item]))
        self.select_statement(identifier="statement-2", statement="A second exact question.")
        records = self.store.snapshot()["records"]
        for project in (problem_evidence.describe_binding, problem_evidence.describe_evidence):
            evidence = project(records, self.artifacts)
            self.assertIsNone(evidence["attempt"])
            self.assertEqual(evidence["results"], [])

    def test_result_evidence_refused_without_a_current_solver_attempt(self):
        item = {"kind": "result", "attempt_id": "absent", "acceptance_id": "acceptance-1"}
        with self.assertRaises(ResearchError) as caught:
            self.mutate(self.load_problem_module().record_value, self.build_value([item]))
        self.assertEqual(caught.exception.code, "problem_value_result_missing")
        self.assertEqual(caught.exception.details, {"attempt_id": "absent", "acceptance_id": "acceptance-1"})
        item = self.bind_result(build_acceptance())
        records = self.store.snapshot()["records"]
        for variant in ("missing-native", "stale", "literature", "another-attempt"):
            with self.subTest(variant=variant):
                changed = copy.deepcopy(records)
                if variant == "missing-native":
                    changed.pop("native_attempt")
                elif variant == "stale":
                    changed["problem_attempt"]["attempt-1"]["statement_id"] = "old-statement"
                elif variant == "literature":
                    changed["problem_attempt"]["attempt-1"].update(kind="literature", solver=None,
                        literature={"readings": [], "description": "An assessed literature attempt."})
                else:
                    newer = dict(changed["problem_attempt"]["attempt-1"], id="attempt-2", recorded_revision=self.store.revision + 1)
                    changed["problem_attempt"]["attempt-2"] = newer
                with self.assertRaises(ResearchError) as caught:
                    problem_evidence.validate_value(changed, self.artifacts, self.build_value([item]))
                self.assertEqual(caught.exception.code, "problem_value_result_missing")
                self.assertEqual(caught.exception.details, {"attempt_id": "attempt-1", "acceptance_id": "acceptance-1"})


class ProblemResultReviewTests(ProblemDeliveryCase):
    def test_empty_attempt_and_results_fields_must_be_present_in_review_packets(self):
        self.initialize_problem()
        self.selected = self.select_statement()
        self.observe_bars()
        keys = ["claim", "openness", "value", "assessment", "assessment_evidence", "attempt", "results", "citations", "readings", "computations", "formal_checks"]
        original = review_protocol.add_problem_evidence
        def omit_fields(records, artifacts, packet):
            original(records, artifacts, packet)
            self.assertIsNone(packet["problem_evidence"]["attempt"])
            self.assertEqual(packet["problem_evidence"]["results"], [])
            for key in keys:
                packet["problem_evidence"].pop(key)
        with patch.object(review_protocol, "add_problem_evidence", side_effect=omit_fields):
            for reviewer in ("first", "second"):
                self.observe("result", reviewer)
        assessed = research_decisions.assess_problem_reviews(self.store.snapshot()["records"], self.artifacts)
        pending = [row for row in assessed["obligations"] if row["code"] == "problem_evidence_unassessed"]
        self.assertEqual(len(pending), 2)
        self.assertTrue(all(row["missing"] == keys for row in pending), pending)

    def test_assessment_references_are_delivered_and_covered(self):
        from research_harness.literature import import_bundle
        from research_harness.reading import record_reading
        from test_research_problem_assessment import build_assessment
        self.prepare_ready()
        claims = self.load_problem_module()
        other = self.select_statement(identifier="comparison", statement="A weaker comparison bound.")
        self.select_statement(candidate_id="candidate-1")
        version = self.metadata(2)
        bundle = self.bundle(version, bundle_id="assessment-only-body")
        self.mutate(import_bundle, bundle)
        self.mutate(record_reading, self.full_note(bundle, "assessment-only-reading"))
        records = self.store.snapshot()["records"]
        reading = records["reading"]["assessment-only-reading"]
        record_ref = {"kind": "record", "record_kind": "problem_statement", "id": "comparison", "digest": digest(other)}
        source_ref = {"kind": "source", "link": reading["inspections"][0]["link"]}
        payload = build_assessment(self.selected, "assessment-references")
        payload["items"]["priority"]["evidence"] = [record_ref, source_ref]
        self.mutate(claims.record_assessment, payload)
        for reviewer in ("first", "second"):
            identifier = "assessment-content-" + reviewer
            self.observe("result", reviewer, identifier=identifier)
            assignment = self.store.snapshot()["records"]["review_assignment"][identifier]
            packet = json.loads(self.artifacts.read(assignment["packet"]))
            evidence = packet["problem_evidence"]
            self.assertIn(dict(record_ref, record=other), evidence["assessment_evidence"])
            self.assertIn(dict(source_ref, reading_ids=["assessment-only-reading"]), evidence["assessment_evidence"])
            delivered = next(row for row in evidence["readings"] if row["id"] == "assessment-only-reading")
            self.assertEqual((delivered["digest"], delivered["reading"]["notes"], delivered["reading"]["version_id"]),
                             (digest(reading), reading["notes"], reading["version_id"]))
            self.assertIn("assessment-only-reading", [row["reading_id"] for row in evidence["passages"]])
            self.assertIn("2601.00002v1", [row["locator"] for row in evidence["citations"]])
        assessed = research_decisions.assess_problem_reviews(self.store.snapshot()["records"], self.artifacts)
        self.assertTrue(assessed["ready"], assessed["obligations"])
        self.mutate(claims.record_decision, {"id": "assessment-citations", "action": "post",
            "review_ids": assessed["review_ids"], "bar_review_ids": assessed["bar_review_ids"],
            "reason": "Both reviewers received the comparison record and source."})
        from research_harness.problem_delivery import export_problem
        export_problem(self.store, self.root / "assessment-export")
        citations = json.loads((self.root / "assessment-export" / "citations.json").read_text())
        self.assertIn("2601.00002v1", [row["locator"] for row in citations])
        payload = copy.deepcopy(payload)
        payload["id"] = "assessment-references-updated"
        payload["items"]["priority"]["note"] += " Additional comparison context."
        self.mutate(claims.record_assessment, payload)
        original = review_protocol.add_problem_evidence
        def omit_references(records, artifacts, packet):
            original(records, artifacts, packet)
            packet["problem_evidence"].pop("assessment_evidence")
        with patch.object(review_protocol, "add_problem_evidence", side_effect=omit_references):
            for reviewer in ("first", "second"):
                self.observe("result", reviewer, identifier="assessment-omitted-" + reviewer)
        assessed = research_decisions.assess_problem_reviews(self.store.snapshot()["records"], self.artifacts)
        self.assertEqual(sorted([row for row in assessed["obligations"] if row["code"] == "problem_evidence_unassessed"],
                                key=lambda row: row["review_id"]), [{
            "code": "problem_evidence_unassessed",
            "explanation": "Obtain both observed results covering the current problem evidence and every delivered source and output.",
            "review_id": "review-assessment-omitted-" + reviewer,
            "missing": ["assessment_evidence"], "reading_ids": [], "artifact_digests": []}
            for reviewer in ("first", "second")])

    def test_assessment_artifact_bytes_are_attached_and_covered(self):
        from test_research_problem_assessment import build_assessment
        self.prepare_ready()
        artifact = self.artifacts.put(b'A complete scoped comparison argument.', 'text/plain')
        payload = build_assessment(self.selected, 'assessment-artifact')
        payload['items']['priority']['evidence'] = [artifact]
        self.mutate(self.load_problem_module().record_assessment, payload)
        original = review_protocol._evidence
        def omit_artifact(artifacts, value, **options):
            return [row for row in original(artifacts, value, **options) if row['artifact'] != artifact]
        for omitted in (False, True):
            for reviewer in ('first', 'second'):
                identifier = 'assessment-artifact-' + str(omitted) + '-' + reviewer
                if omitted:
                    with patch.object(review_protocol, '_evidence', side_effect=omit_artifact):
                        self.observe('result', reviewer, identifier=identifier)
                else:
                    self.observe('result', reviewer, identifier=identifier)
                assignment = self.store.snapshot()['records']['review_assignment'][identifier]
                packet = json.loads(self.artifacts.read(assignment['packet']))
                self.assertIn(artifact, packet['problem_evidence']['assessment_evidence'])
                if not omitted:
                    self.assertIn({'artifact': artifact, 'content': 'A complete scoped comparison argument.',
                                   'encoding': 'utf-8'}, packet['evidence'])
            assessed = research_decisions.assess_problem_reviews(self.store.snapshot()['records'], self.artifacts)
            if not omitted:
                self.assertTrue(assessed['ready'], assessed['obligations'])
                # A material assessment update permits a fresh result review in each bar session.
                payload = copy.deepcopy(payload)
                payload['id'] = 'assessment-artifact-updated'
                payload['items']['priority']['note'] += ' Explain the comparison scope.'
                self.mutate(self.load_problem_module().record_assessment, payload)
            else:
                self.assertEqual(sorted([row for row in assessed['obligations']
                                         if row['code'] == 'problem_evidence_unassessed'], key=lambda row: row['review_id']), [{
                    'code': 'problem_evidence_unassessed',
                    'explanation': 'Obtain both observed results covering the current problem evidence and every delivered source and output.',
                    'review_id': 'review-assessment-artifact-True-' + reviewer,
                    'missing': [], 'reading_ids': [], 'artifact_digests': [artifact['sha256']]}
                    for reviewer in ('first', 'second')])

    def test_assessment_local_artifact_record_bytes_are_attached_and_covered(self):
        from test_research_problem_assessment import build_assessment
        self.prepare_ready()
        artifact = self.artifacts.put(b'A complete scoped comparison argument.', 'text/plain')
        record = {'id': 'comparison-artifact', 'path': 'results/comparison.txt', 'artifact': artifact}
        self.put('local_artifact', record['id'], record)
        reference = {'kind': 'record', 'record_kind': 'local_artifact', 'id': record['id'], 'digest': digest(record)}
        payload = build_assessment(self.selected, 'assessment-artifact')
        payload['items']['priority']['evidence'] = [reference]
        self.mutate(self.load_problem_module().record_assessment, payload)
        original = review_protocol._evidence
        def omit_artifact(artifacts, value, **options):
            return [row for row in original(artifacts, value, **options) if row['artifact'] != artifact]
        for omitted in (False, True):
            for reviewer in ('first', 'second'):
                identifier = 'assessment-artifact-' + str(omitted) + '-' + reviewer
                if omitted:
                    with patch.object(review_protocol, '_evidence', side_effect=omit_artifact):
                        self.observe('result', reviewer, identifier=identifier)
                else:
                    self.observe('result', reviewer, identifier=identifier)
                assignment = self.store.snapshot()['records']['review_assignment'][identifier]
                packet = json.loads(self.artifacts.read(assignment['packet']))
                self.assertIn(dict(reference, record=record), packet['problem_evidence']['assessment_evidence'])
                if not omitted:
                    self.assertIn({'artifact': artifact, 'content': 'A complete scoped comparison argument.',
                                   'encoding': 'utf-8'}, packet['evidence'])
            assessed = research_decisions.assess_problem_reviews(self.store.snapshot()['records'], self.artifacts)
            if not omitted:
                self.assertTrue(assessed['ready'], assessed['obligations'])
                # A material assessment update permits a fresh result review in each bar session.
                payload = copy.deepcopy(payload)
                payload['id'] = 'assessment-artifact-updated'
                payload['items']['priority']['note'] += ' Explain the comparison scope.'
                self.mutate(self.load_problem_module().record_assessment, payload)
            else:
                self.assertEqual(sorted([row for row in assessed['obligations']
                                         if row['code'] == 'problem_evidence_unassessed'], key=lambda row: row['review_id']), [{
                    'code': 'problem_evidence_unassessed',
                    'explanation': 'Obtain both observed results covering the current problem evidence and every delivered source and output.',
                    'review_id': 'review-assessment-artifact-True-' + reviewer,
                    'missing': [], 'reading_ids': [], 'artifact_digests': [artifact['sha256']]}
                    for reviewer in ('first', 'second')])

    def test_results_enter_binding_coverage_and_reviewer_evidence(self):
        self.prepare_ready(solver=True)
        claims = self.load_problem_module()
        before = claims.compute_problem_evidence_digest(self.store.snapshot()["records"], self.artifacts)
        acceptance = build_acceptance()
        native = build_native_attempt(self.selected["statement"], [acceptance], {})
        self.mutate(problem_attempt.record_attempt, build_attempt("result-attempt", put_native_attempt(self, native)))
        item = {"kind": "result", "attempt_id": "result-attempt", "acceptance_id": "acceptance-1"}
        value = self.build_value([item], "result-value")
        self.mutate(claims.record_value, value)
        records = self.store.snapshot()["records"]
        self.assertNotEqual(before, claims.compute_problem_evidence_digest(records, self.artifacts))
        self.assertEqual(problem_evidence.describe_binding(records, self.artifacts)["results"], [
            {"attempt_id": "result-attempt", "acceptance_id": "acceptance-1", "digest": digest(acceptance)}])
        expected = [{"attempt_id": "result-attempt", "acceptance_id": "acceptance-1",
                     "claim": native["checkpoints"][0]["claim"], "outcome": "reduction", "standard": "reviewed"}]
        self.assertEqual(problem_evidence.describe_evidence(records, self.artifacts)["results"], expected)
        original = review_protocol.add_problem_evidence
        for omitted in (None, "results", "attempt"):
            if omitted is not None:
                value = self.build_value([item], "value-without-" + omitted)
                value["motivation"][0]["text"] += " Updated to explain " + omitted + "."
                self.mutate(claims.record_value, value)
            def deliver(records, artifacts, packet):
                original(records, artifacts, packet)
                if omitted is not None:
                    packet["problem_evidence"].pop(omitted)
            with patch.object(review_protocol, "add_problem_evidence", side_effect=deliver):
                for reviewer in ("first", "second"):
                    identifier = "results-" + str(omitted) + "-" + reviewer
                    self.observe("result", reviewer, identifier=identifier)
                    assignment = self.store.snapshot()["records"]["review_assignment"][identifier]
                    packet = json.loads(self.artifacts.read(assignment["packet"]))
                    if omitted is None:
                        self.assertEqual(packet["problem_evidence"]["results"], expected)
            assessed = research_decisions.assess_problem_reviews(self.store.snapshot()["records"], self.artifacts)
            if omitted is None:
                self.assertTrue(assessed["ready"], assessed["obligations"])
            else:
                pending = [row for row in assessed["obligations"] if row["code"] == "problem_evidence_unassessed"]
                self.assertEqual({row["review_id"] for row in pending}, {
                    "review-results-" + omitted + "-first", "review-results-" + omitted + "-second"})
                self.assertTrue(all(row["missing"] == [omitted] for row in pending), pending)
