"""Openness packets bind common searches and exact selected formulations."""

from pathlib import Path

from test_research_problem_claims import ProblemCase
from test_research_problem_delivery import ProblemDeliveryCase
from research_harness import problem_evidence, research_decisions as decisions, review_protocol
from research_harness.evidence import digest
from research_harness.literature import record_search
from unittest.mock import patch
import json


class ProblemOpennessTests(ProblemCase):
    def setUp(self):
        super().setUp()
        self.initialize_problem()
        self.put_selecting_slate()
        self.selected = self.select_statement(candidate_id="candidate-1")

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
        self.assertEqual(packet["claim_digest"], self.load_problem_module().compute_statement_digest(self.selected))
        self.assertTrue((self.root / "packet" / "inputs.json").is_file())

    def test_new_neighbour_and_material_search_make_finding_stale(self):
        from research_harness.problem_openness import assess_openness
        finding = {"id": "openness", "outcome": "supported_open", "statement_digest": self.load_problem_module().compute_statement_digest(self.selected),
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
        from test_research_problem_attempt import record_solver_attempt
        record_solver_attempt(self, self.selected)
        work = self.metadata()
        reading = fixtures.ProblemOpennessMinimumTests.read(self, work)
        search = fixtures.ProblemOpennessMinimumTests.search(self, [work])
        for outcome, code in (("unresolved", "problem_openness_unresolved"), ("already_solved", "problem_already_solved")):
            finding = fixtures.ProblemOpennessMinimumTests.build_finding(self, [search], [reading], outcome=outcome,
                                                                 settling=work if outcome == "already_solved" else None)
            self.put("problem_openness", outcome, dict(finding, id=outcome,
                statement_digest=self.load_problem_module().compute_statement_digest(self.selected), recorded_revision=self.store.revision + 1))
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
            "statement_digest": self.load_problem_module().compute_statement_digest(self.selected),
            "minimum": {"statement": self.selected["statement"], "neighbours": [], "settling_work_id": "settler"},
            "recorded_revision": self.store.revision + 1})
        self.select_statement(identifier="changed-neighbours", neighbours=["A different unresolved formulation"])
        pending = assess_openness(self.store.snapshot()["records"], self.artifacts)
        self.assertEqual(pending[0]["code"], "problem_openness_stale")

    def test_unresolved_finding_with_a_minimum_tracks_declared_neighbour_currency(self):
        from research_harness.problem_openness import assess_openness
        self.put("problem_openness", "unresolved", {"id": "unresolved", "outcome": "unresolved",
            "statement_digest": self.load_problem_module().compute_statement_digest(self.selected),
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
        self.prepare_review_route = fixtures.ProblemReviewTests.prepare_review_route.__get__(self)
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
        self.assert_error("openness_statement_mismatch", lambda: self.mutate(self.load_problem_module().record_openness, {"assignment_id": "openness-assignment"}))

    def test_observed_unresolved_without_minimum_becomes_stale_after_a_new_neighbour(self):
        import test_research_problem_reviews as fixtures
        self.initialize_problem()
        self.selected = self.select_statement()
        self.prepare_review_route = fixtures.ProblemReviewTests.prepare_review_route.__get__(self)
        fixtures.ProblemOpennessReviewTests.observe_openness(self, "unresolved")
        self.mutate(self.load_problem_module().record_openness, {"assignment_id": "openness-assignment"})
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
        self.prepare_review_route = fixtures.ProblemReviewTests.prepare_review_route.__get__(self)
        fixtures.ProblemOpennessReviewTests.observe_openness(self, "unresolved")
        self.mutate(self.load_problem_module().record_openness, {"assignment_id": "openness-assignment"})
        material = minimum_fixtures.ProblemOpennessMinimumTests.search(self, [self.metadata(2)])
        from research_harness.problem_openness import assess_openness
        pending = assess_openness(self.store.snapshot()["records"], self.artifacts)
        self.assertEqual(pending[0]["code"], "problem_openness_stale")
        self.assertIn(material["id"], pending[0]["search_ids"])

    def test_observed_openness_requires_the_native_result_schema(self):
        import test_research_problem_reviews as fixtures
        self.initialize_problem()
        self.selected = self.select_statement()
        self.prepare_review_route = fixtures.ProblemReviewTests.prepare_review_route.__get__(self)
        self.build_provider_response = fixtures.ProblemReviewTests.build_provider_response.__get__(self)
        fixtures.ProblemOpennessReviewTests.observe_openness(self, change_output=lambda output: output["findings"].pop("policy"))
        # The run refuses the output at once, so the assignment can run again.
        refusal = self.store.snapshot()["records"]["review_output_refusal"]["openness-attempt"]
        self.assertEqual((refusal["code"], refusal["check"]), ("invalid_native_review", "native.output"))
        self.assert_error("review_context_unverified",
                          lambda: self.mutate(self.load_problem_module().record_openness, {"assignment_id": "openness-assignment"}))

    def prepare_claude_export(self):
        """A problem record with a Claude route and one bound search whose relevant work is fully read; returns the
        export spec and the version id of that work."""
        import test_research_problem_openness_minimum as minimum_fixtures
        self.initialize_problem()
        self.selected = self.select_statement()
        self.mutate(review_protocol.record_route, {"id": "route-claude", "adapter": "claude_cli_v1", "model": "claude-opus-5-5",
            "endpoint": "claude://local", "configuration": {"effort": "high", "max_output_tokens": 64000, "timeout_seconds": 1800}})
        evidence = self.record_full_reading()
        search = minimum_fixtures.ProblemOpennessMinimumTests.search(self, [evidence["version_id"]])
        spec = {"assignment_id": "openness-assignment", "reviewer_id": "native-reviewer",
                "minimum": {"searches": [{"formulation": self.selected["statement"], "search_id": search["id"]}],
                            "settling_work_id": None, "duplicate_resolutions": []}}
        return spec, evidence["version_id"]

    def export_openness(self, spec, name="packet"):
        from research_harness.problem_openness import export_openness
        return export_openness(self.store, spec, self.root / name)

    def test_an_export_above_a_text_route_limit_without_a_priority_is_warned(self):
        spec, _ = self.prepare_claude_export()
        with patch("research_harness.review_claude_transport.INPUT_LIMIT_CHARACTERS", 2000):
            warning, = self.export_openness(spec)["warnings"]
        self.assertEqual(warning["code"], "review_input_too_large")
        self.assertEqual(warning["details"]["route_ids"], ["route-claude"])
        self.assertGreater(warning["details"]["estimated_characters"], 2000)

    def test_an_export_with_a_text_priority_or_within_the_limit_has_no_warning(self):
        spec, version_id = self.prepare_claude_export()
        self.assertNotIn("warnings", self.export_openness(spec, "within-limit"))
        spec["minimum"]["text_priority"] = [version_id]
        with patch("research_harness.review_claude_transport.INPUT_LIMIT_CHARACTERS", 2000):
            self.assertNotIn("warnings", self.export_openness(spec, "with-priority"))

    def test_resolved_finding_cannot_claim_reading_records_without_delivered_source_bytes(self):
        import test_research_problem_reviews as reviewers
        import test_research_problem_openness_minimum as minimum_fixtures
        from research_harness.problem_openness import export_openness
        from research_harness import review_protocol
        self.initialize_problem()
        self.selected = self.select_statement()
        evidence = self.record_full_reading()
        search = minimum_fixtures.ProblemOpennessMinimumTests.search(self, [evidence["version_id"]])
        exported = export_openness(self.store, {"assignment_id": "openness-assignment", "reviewer_id": "native-reviewer",
            "minimum": {"searches": [{"formulation": self.selected["statement"], "search_id": search["id"]}],
                        "settling_work_id": None, "duplicate_resolutions": []}}, self.root / "packet")
        native = json.loads(self.artifacts.read(exported["native_packet"]))
        minimum = native["scientific_context"]["openness_minimum"]
        record_digests = {row["search_digest"] for row in minimum["searches"]} | set(minimum["reading_digests"]) | {native["subject_digest"]}
        native["evidence"] = [row for row in native["evidence"] if row["digest"] in record_digests]
        forged = self.artifacts.put(json.dumps(native).encode(), "application/json")
        reviewers.ProblemReviewTests.prepare_review_route(self)
        self.mutate(review_protocol.record_assignment, {"id": "openness-assignment", "route_id": "route", "dossier_id": None,
            "role": "native_math", "reviewer_id": "native-reviewer", "author_id": "problem-author", "context": {"native_packet": forged}})
        output = {"schema_version": 2, "subject_digest": native["subject_digest"], "claim_digest": native["claim_digest"],
            "reviewer": native["reviewer"], "decision": "approve", "findings": {
                **{key: "Checked the supplied subject." for key in ("statement", "assumptions", "scope", "dependencies", "policy")},
                "openness": {"outcome": "supported_open", "statement_digest": native["claim_digest"], "minimum": minimum,
                    "evidence": sorted(record_digests), "searched_at": "2026-10-17", "coverage": "The captured exact-formulation search.",
                    "reason": "No settling source was found."}}}
        with patch("research_harness.review_transport.send_request", return_value=reviewers.ProblemReviewTests.build_provider_response(self, output)):
            self.mutate(review_protocol.invoke_assignment, {"id": "openness-attempt", "assignment_id": "openness-assignment"}, credential="test-only")
        self.assert_error("openness_evidence_mismatch", lambda: self.mutate(self.load_problem_module().record_openness, {"assignment_id": "openness-assignment"}))

    def build_provider_response(self, output):
        import test_research_problem_reviews as reviewers
        return reviewers.ProblemReviewTests.build_provider_response(self, output)


class ProblemOpennessMinimumSchemaTests(ProblemCase):
    def setUp(self):
        super().setUp()
        self.initialize_problem()
        self.put_selecting_slate()
        self.selected = self.select_statement(candidate_id="candidate-1")
        from test_research_problem_attempt import record_solver_attempt
        record_solver_attempt(self, self.selected)

    def prepare_review_route(self):
        from test_research_problem_reviews import ProblemReviewTests
        return ProblemReviewTests.prepare_review_route(self)

    def build_provider_response(self, output):
        from test_research_problem_reviews import ProblemReviewTests
        return ProblemReviewTests.build_provider_response(self, output)

    def observe_minimum(self, outcome, variant):
        from test_research_problem_reviews import ProblemOpennessReviewTests

        def change_output(output):
            finding = output["findings"]["openness"]
            if variant == "omitted":
                finding.pop("minimum", None)
            elif variant == "null":
                finding["minimum"] = None
            elif variant == "malformed":
                finding["minimum"] = {"schema_version": 1}
            else:
                finding["minimum"] = {"schema_version": 1, "statement": self.selected["statement"],
                    "neighbours": [], "searches": [], "reading_digests": [], "settling_work_id": None}

        ProblemOpennessReviewTests.observe_openness(self, outcome, change_output=change_output)

    def record_finding(self):
        return self.mutate(self.load_problem_module().record_openness, {"assignment_id": "openness-assignment"})["result"]

    def assert_refused_at_run(self):
        """A minimum that the shared schema refuses is refused when the run completes, and the finding waits for a new run."""
        refusal = self.store.snapshot()["records"]["review_output_refusal"]["openness-attempt"]
        self.assertEqual((refusal["code"], refusal["check"]), ("invalid_record", "native.output"))
        self.assert_error("review_context_unverified", self.record_finding)

    def assert_unresolved_status(self):
        from research_harness.cli import status_report
        from research_harness.problem_delivery import assess_post
        from research_harness.problem_evidence import collect_readings, describe_evidence
        records = self.store.snapshot()["records"]
        self.assertEqual(collect_readings(records), [])
        self.assertEqual(describe_evidence(records, self.artifacts)["readings"], [])
        self.assertIn("problem_openness_unresolved", {item["code"] for item in assess_post(records, self.artifacts)["obligations"]})
        self.assertEqual(status_report(self.store)["next"]["code"], "problem_openness_unresolved")

    def test_unresolved_omitted_minimum_records_and_reports_the_gap(self):
        self.observe_minimum("unresolved", "omitted")
        self.assertNotIn("minimum", self.record_finding())
        self.assert_unresolved_status()

    def test_unresolved_null_minimum_is_refused_by_the_shared_schema(self):
        self.observe_minimum("unresolved", "null")
        self.assert_refused_at_run()

    def test_unresolved_malformed_minimum_is_refused_by_the_shared_schema(self):
        self.observe_minimum("unresolved", "malformed")
        self.assert_refused_at_run()

    def test_unresolved_incomplete_minimum_records_without_evidence_audit(self):
        self.observe_minimum("unresolved", "incomplete")
        with patch("research_harness.openness_minimum.audit_minimum", side_effect=AssertionError("unresolved evidence audit")):
            self.assertEqual(self.record_finding()["minimum"]["searches"], [])
        self.assert_unresolved_status()

    def test_resolved_omitted_minimum_requires_the_evidence_minimum(self):
        self.observe_minimum("supported_open", "omitted")
        self.assert_error("openness_minimum_required", self.record_finding)

    def test_resolved_null_minimum_is_refused_by_the_shared_schema(self):
        self.observe_minimum("supported_open", "null")
        self.assert_refused_at_run()

    def test_resolved_malformed_minimum_is_refused_by_the_shared_schema(self):
        self.observe_minimum("supported_open", "malformed")
        self.assert_refused_at_run()

    def test_resolved_incomplete_minimum_still_requires_evidence_audit(self):
        self.observe_minimum("supported_open", "incomplete")
        self.assert_error("openness_search_missing", self.record_finding)

    def test_existing_unresolved_null_minimum_does_not_poison_status(self):
        self.observe_minimum("unresolved", "omitted")
        saved = self.record_finding()
        self.put("problem_openness", saved["id"], dict(saved, minimum=None))
        self.assert_unresolved_status()

    def test_legacy_assessment_reading_keeps_its_identity_through_observed_finding(self):
        from research_harness import reading, review_protocol
        from research_harness.literature import import_bundle
        from research_harness.problem_openness import export_openness
        from test_research_problem_openness_minimum import ProblemOpennessMinimumTests

        version = self.metadata()
        bundle = self.bundle(version)
        self.mutate(import_bundle, bundle)
        note = self.full_note(bundle, "legacy-full")
        current = reading._assess(self.store.snapshot()["records"], self.artifacts, note)
        legacy = dict(current, bundle_digest="0" * 64)
        # Record the reading once, as the legacy implementation would have.
        with patch("research_harness.reading._assess", return_value=legacy):
            self.mutate(reading.record_reading, note)
        original = self.store.snapshot()["records"]["reading"][note["id"]]
        coverage = reading.fulltext_coverage(self.store.snapshot()["records"], self.artifacts, version)
        self.assertTrue(coverage["complete"])
        self.assertNotEqual(coverage["readings"][note["id"]]["assessment"], original["assessment"])
        search = ProblemOpennessMinimumTests.search(self, [version])
        exported = export_openness(self.store, {"assignment_id": "openness-assignment", "reviewer_id": "native-reviewer",
            "minimum": {"searches": [{"formulation": self.selected["statement"], "search_id": search["id"]}],
                        "settling_work_id": None, "duplicate_resolutions": []}}, self.root / "legacy-packet")
        packet = json.loads(self.artifacts.read(exported["native_packet"]))
        self.prepare_review_route()
        self.mutate(review_protocol.record_assignment, {"id": "openness-assignment", "route_id": "route", "dossier_id": None,
            "role": "native_math", "reviewer_id": "native-reviewer", "author_id": "problem-author",
            "context": {"native_packet": exported["native_packet"]}})
        output = {"schema_version": 2, "subject_digest": packet["subject_digest"], "claim_digest": packet["claim_digest"],
            "reviewer": packet["reviewer"], "decision": "approve", "findings": {
                **{key: "Assessed the exact supplied subject." for key in ("statement", "assumptions", "scope", "dependencies", "policy")},
                "openness": {"outcome": "supported_open", "statement_digest": packet["claim_digest"],
                    "minimum": packet["scientific_context"]["openness_minimum"],
                    "evidence": [entry["digest"] for entry in packet["evidence"]], "searched_at": "2026-10-17",
                    "coverage": "The recorded exact-formulation search and full legacy reading.",
                    "reason": "The read comparator does not settle the declared problem."}}}
        with patch("research_harness.review_transport.send_request", return_value=self.build_provider_response(output)):
            self.mutate(review_protocol.invoke_assignment, {"id": "openness-attempt", "assignment_id": "openness-assignment"},
                credential="test-only")
        saved = self.record_finding()
        self.assertEqual(saved["minimum"]["reading_digests"], [digest(original)])
        entry = next(entry for entry in packet["evidence"] if entry["digest"] == digest(original))
        self.assertEqual(json.loads(self.artifacts.read(entry["artifact"])), original)
        self.assertEqual(self.store.snapshot()["records"]["reading"], {note["id"]: original})


class ProblemOpennessDeliveryTests(ProblemDeliveryCase):
    """Result reviewers receive the complete openness finding and the searches its minimum binds."""

    def emulate_truncated_projection(self, *, binding):
        """The result packets of the previous release: four openness fields and no bound searches; with `binding`,
        also the evidence binding of that release."""
        real_evidence, real_binding = problem_evidence.describe_evidence, problem_evidence.describe_binding

        def truncate_evidence(records, artifacts):
            evidence = real_evidence(records, artifacts)
            evidence.pop("openness_searches")
            evidence["openness"] = {key: evidence["openness"][key] for key in ("id", "outcome", "statement_digest", "minimum")}
            return evidence

        def truncate_binding(records, artifacts):
            return {key: value for key, value in real_binding(records, artifacts).items() if key != "openness_searches"}
        patches = [patch.object(problem_evidence, "describe_evidence", side_effect=truncate_evidence)]
        if binding:
            patches.append(patch.object(problem_evidence, "describe_binding", side_effect=truncate_binding))
        return patches

    def prepare_with(self, patches):
        for active in patches:
            active.start()
        try:
            self.prepare_ready()
        finally:
            for active in patches:
                active.stop()

    def find_unassessed(self):
        reviews = decisions.assess_problem_reviews(self.store.snapshot()["records"], self.artifacts)
        return [row for row in reviews["obligations"] if row["code"] == "problem_evidence_unassessed"]

    def test_result_packets_carry_the_complete_finding_and_its_bound_searches(self):
        self.prepare_ready()
        records = self.store.snapshot()["records"]
        finding = records["problem_openness"]["openness-assignment"]
        packet = json.loads(self.artifacts.read(records["review_assignment"]["result-first"]["packet"]))
        self.assertEqual(packet["problem_evidence"]["openness"], dict({key: finding[key] for key in (
            "id", "outcome", "statement_digest", "reason", "coverage", "searched_at", "evidence", "minimum")},
            digest=digest(finding), cite={"kind": "record", "record_kind": "problem_openness", "id": finding["id"]}))
        [bound] = packet["problem_evidence"]["openness_searches"]
        search = next(row for row in records["literature_search"].values() if digest(row) == bound["search_digest"])
        self.assertEqual((bound["formulation"], bound["search"]), (self.selected["statement"], search))
        self.assertEqual(bound["sources"], [records["source"][row["source_id"]] for row in search["responses"]])
        self.assertTrue(any(entry["artifact"] == bound["sources"][0]["response"] for entry in packet["evidence"]))
        self.assertEqual(self.find_unassessed(), [])

    def test_a_result_without_the_captured_response_bytes_still_covers_the_bound_searches(self):
        real = review_protocol._evidence

        def attach_without_responses(artifacts, value, **options):
            searches = (value.get("problem_evidence") or {}).get("openness_searches", [])
            responses = {source["response"]["sha256"] for row in searches for source in row["sources"]}
            return [entry for entry in real(artifacts, value, **options) if entry["artifact"]["sha256"] not in responses]
        with patch.object(review_protocol, "_evidence", side_effect=attach_without_responses):
            self.prepare_ready()
        self.assertEqual(self.find_unassessed(), [])

    def test_a_packet_without_the_openness_keys_leaves_the_evidence_unassessed(self):
        self.prepare_with(self.emulate_truncated_projection(binding=False))
        self.assertEqual({tuple(row["missing"]) for row in self.find_unassessed()}, {("openness", "openness_searches")})

    def test_results_on_the_truncated_projection_are_refreshed_in_their_sessions(self):
        self.prepare_with(self.emulate_truncated_projection(binding=True))
        self.assertEqual(len(self.find_unassessed()), 2)
        for reviewer in ("first", "second"):
            self.observe("result", reviewer, identifier="refreshed-" + reviewer)
            records = self.store.snapshot()["records"]
            assignment, original = (records["review_assignment"][identifier] for identifier in ("refreshed-" + reviewer, "result-" + reviewer))
            self.assertEqual((assignment["context"], assignment["slot_id"]), ({"prior_assignment_id": "result-" + reviewer}, original["slot_id"]))
        reviews = decisions.assess_problem_reviews(self.store.snapshot()["records"], self.artifacts)
        self.assertTrue(reviews["ready"], reviews["obligations"])
