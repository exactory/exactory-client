"""Openness packets bind common searches and exact selected formulations."""

from pathlib import Path

from test_research_problem_claims import ProblemCase
from research_harness.evidence import digest
from research_harness.literature import record_search
from unittest.mock import patch
import json


class ProblemOpennessTests(ProblemCase):
    def setUp(self):
        super().setUp()
        self.initialize_problem()
        self.selected = self.select_statement()

    def export(self, searches=()):
        from research_harness.problem_openness import export_openness
        return export_openness(self.store, {"assignment_id": "open-review", "reviewer_id": "openness-reviewer",
            "minimum": {"searches": list(searches), "settling_work_id": None, "duplicate_resolutions": []}}, self.root / "packet")

    def test_export_binds_exact_assignment_and_current_statement(self):
        result = self.export()
        from research_harness.workspace import strict_json
        packet = strict_json(self.artifacts.read(result["native_packet"]))
        self.assertEqual(packet["reviewer"], {"source": "host", "actor_id": "openness-reviewer",
                                            "attestation_id": "review-assignment:open-review"})
        self.assertEqual(packet["subject"], self.selected)
        self.assertEqual(packet["claim_digest"], self.problem().statement_digest(self.selected))
        self.assertTrue((self.root / "packet" / "inputs.json").is_file())

    def test_new_neighbour_and_material_search_make_finding_stale(self):
        from research_harness.problem_openness import assess_openness
        finding = {"id": "openness", "outcome": "supported_open", "statement_digest": self.problem().statement_digest(self.selected),
            "minimum": {"schema_version": 1, "statement": self.selected["statement"], "neighbours": [], "searches": [],
                        "reading_digests": [], "settling_work_id": None}, "evidence": [], "recorded_revision": self.store.revision + 1}
        self.put("problem_openness", "openness", finding)
        self.select_statement(identifier="statement-2", neighbours=["A new nearby formulation"])
        pending = assess_openness(self.store.snapshot()["records"], self.artifacts)
        self.assertEqual(pending[0]["code"], "problem_openness_stale")
        self.assertIn("A new nearby formulation", pending[0]["formulations"])

    def test_unresolved_and_settled_findings_block_with_actionable_next(self):
        from research_harness.problem_openness import assess_openness
        import test_research_problem_openness_minimum as fixtures
        work = self.metadata()
        reading = fixtures.ProblemOpennessMinimumTests.read(self, work)
        search = fixtures.ProblemOpennessMinimumTests.search(self, [work])
        for outcome, code in (("unresolved", "problem_openness_unresolved"), ("already_solved", "problem_already_solved")):
            finding = fixtures.ProblemOpennessMinimumTests.finding(self, [search], [reading], outcome=outcome,
                                                                 settling=work if outcome == "already_solved" else None)
            self.put("problem_openness", outcome, dict(finding, id=outcome,
                statement_digest=self.problem().statement_digest(self.selected), recorded_revision=self.store.revision + 1))
            report = assess_openness(self.store.snapshot()["records"], self.artifacts)
            self.assertEqual(report[0]["code"], code)
            from research_harness.cli import status_report
            self.assertEqual(status_report(self.store)["next"]["code"], code)
            if outcome == "already_solved":
                self.assertIn("redirect", report[0]["explanation"].lower())
                self.assertEqual(report[0]["settling_work_id"], work)

    def test_settled_finding_also_becomes_stale_when_a_neighbour_is_added(self):
        from research_harness.problem_openness import assess_openness
        self.put("problem_openness", "settled", {"id": "settled", "outcome": "already_solved",
            "statement_digest": self.problem().statement_digest(self.selected),
            "minimum": {"statement": self.selected["statement"], "neighbours": [], "settling_work_id": "settler"},
            "recorded_revision": self.store.revision + 1})
        self.select_statement(identifier="changed-neighbours", neighbours=["A different unresolved formulation"])
        pending = assess_openness(self.store.snapshot()["records"], self.artifacts)
        self.assertEqual(pending[0]["code"], "problem_openness_stale")

    def test_unresolved_finding_with_a_minimum_tracks_declared_neighbour_currency(self):
        from research_harness.problem_openness import assess_openness
        self.put("problem_openness", "unresolved", {"id": "unresolved", "outcome": "unresolved",
            "statement_digest": self.problem().statement_digest(self.selected),
            "minimum": {"statement": self.selected["statement"], "neighbours": []},
            "recorded_revision": self.store.revision + 1})
        self.select_statement(identifier="changed-neighbours", neighbours=["A new formulation"])
        self.assertEqual(assess_openness(self.store.snapshot()["records"], self.artifacts)[0]["code"], "problem_openness_stale")


class ProblemNativeResultShapeTests(ProblemCase):
    def test_observed_packet_subject_must_be_the_exact_current_statement_record(self):
        import test_research_problem_reviews as fixtures
        from research_harness import problem_openness
        self.initialize_problem()
        self.selected = self.select_statement()
        self.route = fixtures.ProblemReviewTests.route.__get__(self)
        real_export = problem_openness.export_openness
        def replace_subject(store, spec, destination):
            exported = real_export(store, spec, destination)
            packet = json.loads(self.artifacts.read(exported["native_packet"]))
            packet["subject"] = {"id": "another-statement", "statement": "A different reviewed claim", "resolution_criteria": "Different criteria"}
            packet["subject_digest"] = digest(packet["subject"])
            exported["native_packet"] = self.artifacts.put(json.dumps(packet).encode(), "application/json")
            return exported
        with patch("research_harness.problem_openness.export_openness", side_effect=replace_subject):
            fixtures.ProblemOpennessReviewTests.observe_openness(self)
        self.error("openness_statement_mismatch", lambda: self.mutate(self.problem().record_openness, {"assignment_id": "openness-assignment"}))

    def test_observed_unresolved_without_minimum_becomes_stale_after_a_new_neighbour(self):
        import test_research_problem_reviews as fixtures
        self.initialize_problem()
        self.selected = self.select_statement()
        self.route = fixtures.ProblemReviewTests.route.__get__(self)
        fixtures.ProblemOpennessReviewTests.observe_openness(self, "unresolved")
        self.mutate(self.problem().record_openness, {"assignment_id": "openness-assignment"})
        self.select_statement(identifier="new-neighbour", neighbours=["A newly declared nearby formulation"])
        from research_harness.problem_openness import assess_openness
        pending = assess_openness(self.store.snapshot()["records"], self.artifacts)
        self.assertEqual(pending[0]["code"], "problem_openness_stale")
        self.assertIn("A newly declared nearby formulation", pending[0]["formulations"])

    def test_observed_unresolved_without_minimum_becomes_stale_after_new_material_search(self):
        import test_research_problem_reviews as fixtures
        import test_research_problem_openness_minimum as minimum_fixtures
        self.initialize_problem()
        self.selected = self.select_statement()
        self.route = fixtures.ProblemReviewTests.route.__get__(self)
        fixtures.ProblemOpennessReviewTests.observe_openness(self, "unresolved")
        self.mutate(self.problem().record_openness, {"assignment_id": "openness-assignment"})
        material = minimum_fixtures.ProblemOpennessMinimumTests.search(self, [self.metadata(2)])
        from research_harness.problem_openness import assess_openness
        pending = assess_openness(self.store.snapshot()["records"], self.artifacts)
        self.assertEqual(pending[0]["code"], "problem_openness_stale")
        self.assertIn(material["id"], pending[0]["search_ids"])

    def test_observed_openness_requires_the_native_result_schema(self):
        import test_research_problem_reviews as fixtures
        self.initialize_problem()
        self.selected = self.select_statement()
        self.route = fixtures.ProblemReviewTests.route.__get__(self)
        self.response = fixtures.ProblemReviewTests.response.__get__(self)
        fixtures.ProblemOpennessReviewTests.observe_openness(self, change_output=lambda output: output["findings"].pop("policy"))
        self.error("invalid_record", lambda: self.mutate(self.problem().record_openness, {"assignment_id": "openness-assignment"}))

    def test_resolved_finding_cannot_claim_reading_records_without_delivered_source_bytes(self):
        import test_research_problem_reviews as reviewers
        import test_research_problem_openness_minimum as minimum_fixtures
        from research_harness.problem_openness import export_openness
        from research_harness import review_protocol
        self.initialize_problem()
        self.selected = self.select_statement()
        evidence = self.full_reading()
        search = minimum_fixtures.ProblemOpennessMinimumTests.search(self, [evidence["version_id"]])
        exported = export_openness(self.store, {"assignment_id": "openness-assignment", "reviewer_id": "native-reviewer",
            "minimum": {"searches": [{"formulation": self.selected["statement"], "search_id": search["id"]}],
                        "settling_work_id": None, "duplicate_resolutions": []}}, self.root / "packet")
        native = json.loads(self.artifacts.read(exported["native_packet"]))
        minimum = native["scientific_context"]["openness_minimum"]
        record_digests = {row["search_digest"] for row in minimum["searches"]} | set(minimum["reading_digests"]) | {native["subject_digest"]}
        native["evidence"] = [row for row in native["evidence"] if row["digest"] in record_digests]
        forged = self.artifacts.put(json.dumps(native).encode(), "application/json")
        reviewers.ProblemReviewTests.route(self)
        self.mutate(review_protocol.record_assignment, {"id": "openness-assignment", "route_id": "route", "dossier_id": None,
            "role": "native_math", "reviewer_id": "native-reviewer", "author_id": "problem-author", "context": {"native_packet": forged}})
        output = {"schema_version": 2, "subject_digest": native["subject_digest"], "claim_digest": native["claim_digest"],
            "reviewer": native["reviewer"], "decision": "approve", "findings": {
                **{key: "Checked the supplied subject." for key in ("statement", "assumptions", "scope", "dependencies", "policy")},
                "openness": {"outcome": "supported_open", "statement_digest": native["claim_digest"], "minimum": minimum,
                    "evidence": sorted(record_digests), "searched_at": "2026-10-17", "coverage": "The captured exact-formulation search.",
                    "reason": "No settling source was found."}}}
        with patch("research_harness.review_transport.send_request", return_value=reviewers.ProblemReviewTests.response(self, output)):
            self.mutate(review_protocol.invoke_assignment, {"id": "openness-attempt", "assignment_id": "openness-assignment"}, credential="test-only")
        self.error("openness_evidence_mismatch", lambda: self.mutate(self.problem().record_openness, {"assignment_id": "openness-assignment"}))

    def response(self, output):
        import test_research_problem_reviews as reviewers
        return reviewers.ProblemReviewTests.response(self, output)
