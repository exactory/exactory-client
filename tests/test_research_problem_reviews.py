"""Problem bars withhold the claim; observed results cover its exact evidence."""

import copy
import json
import sys
import types
from unittest.mock import patch

from literature_fixtures import LiteratureCase
from research_harness import review_protocol as protocol
from research_harness import research_decisions as decisions
from research_harness.evidence import digest
from test_research_problem_claims import ProblemCase


CHECKS = ("problem_statement", "problem_openness", "problem_value", "problem_citations", "stop")


class ProblemReviewTests(LiteratureCase):
    def setUp(self):
        super().setUp()
        self.put("workspace", "problem", {"version": 1, "orchestration": "problem_creation",
            "slug": "bounded-sequences", "area": "math.CO", "field": "Extremal bounded sequences",
            "authors": ["author", "coauthor"]})
        self.statement = {"id": "statement-1", "title": "PRIVATE_TITLE", "area": "math.CO",
            "statement": "PRIVATE_STATEMENT", "resolution_criteria": "PRIVATE_CRITERIA",
            "neighbours": ["PRIVATE_NEIGHBOUR"], "formal_statement": None}
        self.put("problem_statement", self.statement["id"], self.statement)
        self.put("problem_selection", "current", {"statement_id": self.statement["id"]})
        self.expected = {"claim": {"statement_id": self.statement["id"],
            "statement_digest": digest({key: self.statement[key] for key in ("statement", "resolution_criteria")}),
            **{key: self.statement[key] for key in ("title", "area", "statement", "resolution_criteria", "formal_statement")}},
            "openness": {"id": "open-1", "outcome": "supported_open", "minimum": {}},
            "value": {"id": "value-1", "motivation": [{"text": "PRIVATE_VALUE", "evidence": []}], "current_state": []},
            "citations": [], "readings": [], "passages": [], "computations": [], "formal_checks": []}
        # Isolate the review contract from the independently implemented claim validator.
        claims = types.ModuleType("research_harness.problem_claims")
        claims.find_current_statement = lambda records: records.get("problem_statement", {}).get(
            records.get("problem_selection", {}).get("current", {}).get("statement_id"))
        claims.describe_problem_evidence = lambda records, artifacts: copy.deepcopy(self.expected)
        claims.compute_problem_evidence_digest = lambda records, artifacts: digest(self.expected)
        replacement = patch.dict(sys.modules, {"research_harness.problem_claims": claims})
        replacement.start()
        self.addCleanup(replacement.stop)

    def put(self, kind, identifier, value):
        self.sequence += 1
        self.store.mutate("fixture.seed", {"kind": kind, "id": identifier},
            lambda tx: tx.put(kind, identifier, value), expected_revision=self.store.revision,
            request_id="problem-seed-" + str(self.sequence))

    def read_records(self):
        return self.store.snapshot()["records"]

    def build_provider_response(self, output):
        return {"id": "provider-response", "model": "fixture-model", "status": "completed",
            "output": [{"type": "message", "role": "assistant", "content": [
                {"type": "output_text", "text": json.dumps(output)}]}],
            "usage": {"input_tokens": 37, "output_tokens": 12}}

    def prepare_review_route(self):
        self.mutate(protocol.record_route, {"id": "route", "adapter": "openai_responses_v1",
            "model": "fixture-model", "endpoint": "https://api.openai.com/v1/responses",
            "configuration": {"max_output_tokens": 1000, "timeout_seconds": 5}})
        def respond_to_probe(route, request, credential):
            packet = json.loads(request["input"][0]["content"])
            return self.build_provider_response({"allowed_control": packet["allowed_control"],
                "evidence_control": packet["evidence_control"], "excluded_controls": []})
        with patch("research_harness.review_transport.send_request", side_effect=respond_to_probe):
            self.mutate(protocol.probe_route, {"id": "probe", "route_id": "route"}, credential="test-only")

    def assign(self, role, reviewer, author="author", identifier=None, **context):
        return self.mutate(protocol.record_assignment, {"id": identifier or role + "-" + reviewer,
            "route_id": "route", "dossier_id": None, "role": role, "reviewer_id": reviewer,
            "author_id": author, "context": context})["result"]

    def build_review_output(self, stage, checks=CHECKS):
        return {"stage": stage, "support": None, "value": {"status": "sufficient",
            "consequence": "A new connection between extremal bounds.",
            "bar_ids": [] if stage == "bar" else ["review-bar-first", "review-bar-second"],
            "objective_adequacy": "not_assessed" if stage == "bar" else "adequate",
            "method_adequacy": "not_assessed" if stage == "bar" else "adequate",
            "reason": "The supplied sources support this consequence.", "evidence": [self.supporting],
            "work_items": [], "assurances": [] if stage == "bar" else [
                {"kind": kind, "status": "passed", "reason": "Checked the exact supplied evidence.",
                 "evidence": [self.supporting]} for kind in checks], "objection_findings": []},
            "objections": [], "limitations": ["Assessment is limited to the delivered evidence."]}

    def observe(self, stage, reviewer, output=None, record=True, identifier=None):
        assignment = self.assign(stage, reviewer, identifier=identifier)
        output = output or self.build_review_output(stage)
        with patch("research_harness.review_transport.send_request", return_value=self.build_provider_response(output)):
            self.mutate(protocol.invoke_assignment, {"id": "attempt-" + assignment["id"],
                "assignment_id": assignment["id"]}, credential="test-only")
        payload = dict(output, id="review-" + assignment["id"], dossier_id=None, assignment_id=assignment["id"])
        if record:
            return self.mutate(decisions.record_value_review, payload)["result"]
        return payload

    def observe_bar_reviews(self):
        self.supporting = self.artifacts.put(b"A captured field standard.", "text/plain")
        self.prepare_review_route()
        for reviewer in ("first", "second"):
            self.observe("bar", reviewer)

    def observe_result_reviews(self):
        self.observe_bar_reviews()
        for reviewer in ("first", "second"):
            self.observe("result", reviewer)

    def add_reading_and_computation(self):
        from research_harness import literature, reading
        from research_harness.direction_sources import build_reading_passages
        version = self.metadata(76001)
        bundle = self.bundle(version)
        self.mutate(literature.import_bundle, bundle)
        self.mutate(reading.record_reading, self.full_note(bundle, note_id="decisive-reading"))
        saved = self.read_records()["reading"]["decisive-reading"]
        passages = []
        for passage, data in build_reading_passages(self.read_records(), self.artifacts, [saved]):
            self.assertEqual(self.artifacts.put(data, passage["excerpt"]["media_type"]), passage["excerpt"])
            passages.append(passage)
        self.expected["readings"] = [{"id": saved["id"], "version_id": version,
            "digest": digest(saved), "depth": "fulltext", "reading": saved}]
        self.expected["passages"] = passages
        output = self.artifacts.put(b"{\"verified_inputs\":[1,2,3]}", "application/json")
        validation = self.artifacts.put(b"Validation checked all three inputs.", "text/plain")
        self.expected["computations"] = [{"execution_id": "execution-1", "admission": {"id": "admission-1"},
            "execution": {"id": "execution-1", "outputs": [{"id": "output-1", "artifact": output}]},
            "assessment": {"id": "assessment-1", "validations": [validation]}}]
        self.expected["formal_checks"] = [{"id": "formal-1", "native_closure": {
            "contract": {"original_claim": {"statement": "The requested finite bound", "proof_policy": "lean-kernel"}},
            "closure": {"subject": {"outcome": "proof"}, "review": {"decision": "approve"},
                "audit": {"subject_digest": "checked-subject"}}}}]
        return output, validation

    def test_problem_prompts_are_separate_and_existing_prompt_bytes_are_preserved(self):
        hashes = {"bar": "88b53e388fa267f8aa5f72da297b28ebdab5ac6e020f81b4be6c2b756d30f3ca",
            "slate": "38fed1d12ce4376f4144d3dd6c57e2e9475db73a30b7a86c8471f00c84588312",
            "result": "236703cb1f256d93b60cfa4c82ea7e2eb76fcd7a66c9984542767c675e4e32bc",
            "native_math": "911fd529e31ca9bf8efba3c743ecdd4d9c7e4cfe11ed7d97ed830e7002c81872"}
        for role, expected in hashes.items():
            self.assertEqual(protocol.canonical_prompt(role)["template_hash"], expected)
        for role in ("bar", "result"):
            prompt = protocol.canonical_prompt(role, orchestration="problem_creation")
            self.assertEqual(prompt["template_id"], "exactory-problem-review-v1/" + role)
        self.assertIn("nearest known problems", protocol.canonical_prompt("bar", orchestration="problem_creation")["text"])
        for kind in CHECKS:
            self.assertIn(kind, protocol.canonical_prompt("result", orchestration="problem_creation")["text"])

    def test_bar_packet_withholds_statement_title_neighbours_value_and_criteria(self):
        packet = protocol.build_packet(self.read_records(), self.artifacts, None, "bar", "first")
        self.assertEqual((packet["area"], packet["field"]), ("math.CO", "Extremal bounded sequences"))
        self.assertEqual(packet["subject"], {"kind": "problem", "statement_id": "statement-1"})
        for forbidden in ("PRIVATE_STATEMENT", "PRIVATE_TITLE", "PRIVATE_NEIGHBOUR", "PRIVATE_VALUE", "PRIVATE_CRITERIA"):
            self.assertNotIn(forbidden, json.dumps(packet))
        self.assertIn("source_inventory", packet)
        self.assertIn("source_deliveries", packet)

    def test_bar_can_precede_a_statement(self):
        records = self.read_records()
        records.pop("problem_selection")
        packet = protocol.build_packet(records, self.artifacts, None, "bar", "first")
        self.assertEqual(packet["subject"], {"kind": "problem", "statement_id": None})

    def test_result_requires_both_observed_bars(self):
        self.assert_error("review_bar_pending", lambda: protocol.build_packet(
            self.read_records(), self.artifacts, None, "result", "first"))
        self.supporting = self.artifacts.put(b"Field standard.", "text/plain")
        self.prepare_review_route()
        self.observe("bar", "first")
        self.assert_error("review_bar_pending", lambda: protocol.build_packet(
            self.read_records(), self.artifacts, None, "result", "first"))

    def test_changed_bar_field_or_area_is_refused_for_result(self):
        self.observe_bar_reviews()
        for key, changed in (("field", "A changed field"), ("area", "math.NT")):
            records = self.read_records()
            records["workspace"]["problem"][key] = changed
            self.assert_error("review_bar_mismatch", lambda: protocol.build_packet(records, self.artifacts, None, "result", "first"))

    def test_problem_result_continues_its_own_bar_session(self):
        self.observe_bar_reviews()
        self.assert_error("review_bar_continuity", lambda: self.assign("result", "third"))
        result = self.assign("result", "first")
        bar = self.read_records()["review_assignment"]["bar-first"]
        self.assertEqual(result["session_id"], bar["session_id"])
        self.assertEqual(result["history_assignment_id"], bar["id"])
        self.assertEqual(result["slot_id"], bar["slot_id"])
        self.assertEqual([turn["role"] for turn in result["history"]], ["user", "assistant"])

    def test_marker_authors_are_the_independence_authority(self):
        self.prepare_review_route()
        self.assert_error("review_not_independent", lambda: self.assign("bar", " CoAuthor "))
        self.assert_error("problem_author_unknown", lambda: self.assign("bar", "first", author="invented-author"))

    def test_result_packet_delivers_readings_outputs_validations_and_native_closure(self):
        output, validation = self.add_reading_and_computation()
        self.observe_bar_reviews()
        assignment = self.assign("result", "first")
        self.assertEqual(assignment["subject"], {"kind": "problem", "statement_id": "statement-1"})
        packet = json.loads(self.artifacts.read(assignment["packet"]))
        self.assertEqual(packet["problem_evidence"], self.expected)
        self.assertEqual(packet["problem_evidence_digest"], digest(self.expected))
        delivered = {entry["artifact"]["sha256"]: entry for entry in packet["evidence"]}
        for reference in (output, validation, self.expected["passages"][0]["excerpt"]):
            self.assertEqual(delivered[reference["sha256"]]["content"].encode(), self.artifacts.read(reference))
        self.assertIn("original_claim", json.dumps(packet["problem_evidence"]["formal_checks"]))

    def test_exact_problem_assurance_set_is_required(self):
        self.observe_bar_reviews()
        payload = self.observe("result", "first", self.build_review_output("result", decisions.ALL_CHECKS), record=False)
        self.assert_error("invalid_value_review", lambda: self.mutate(decisions.record_value_review, payload))
        payload = self.observe("result", "second", self.build_review_output("result", CHECKS[:-1]), record=False)
        self.assert_error("invalid_value_review", lambda: self.mutate(decisions.record_value_review, payload))

    def test_problem_not_assessed_is_recorded_and_blocks_assurance(self):
        self.observe_bar_reviews()
        output = self.build_review_output("result")
        output["value"]["assurances"][0]["status"] = "not_assessed"
        self.observe("result", "first", output)
        self.observe("result", "second")
        state = decisions.assess_problem_reviews(self.read_records(), self.artifacts)
        self.assertIn("problem_assurance_pending", {item["code"] for item in state["obligations"]})

    def test_current_observed_pair_covers_the_problem_evidence(self):
        self.add_reading_and_computation()
        self.observe_result_reviews()
        with patch.object(type(self.artifacts), "put", side_effect=AssertionError("read-only coverage")):
            state = decisions.assess_problem_reviews(self.read_records(), self.artifacts)
        self.assertTrue(state["ready"], state["obligations"])
        self.assertEqual(set(state["review_ids"]), {"review-result-first", "review-result-second"})
        self.assertEqual(set(state["bar_review_ids"]), {"review-bar-first", "review-bar-second"})

    def test_missing_pair_and_duplicate_reviewer_do_not_authorize_post(self):
        self.observe_result_reviews()
        for identifiers in (["review-result-first"], ["review-result-first", "review-result-first"]):
            state = decisions.assess_problem_reviews(self.read_records(), self.artifacts, review_ids=identifiers)
            self.assertIn("problem_review_pair_required", {item["code"] for item in state["obligations"]})

    def test_structural_review_assessment_does_not_describe_unmet_evidence(self):
        self.observe_result_reviews()
        claims = sys.modules["research_harness.problem_claims"]
        with patch.object(claims, "describe_problem_evidence", side_effect=AssertionError("unavailable claim evidence")):
            state = decisions.assess_problem_reviews(self.read_records(), self.artifacts, check_evidence=False)
        self.assertTrue(state["ready"], state["obligations"])
        self.assertIsNone(state["problem_evidence_digest"])

    def test_unobserved_results_cannot_cover_problem_evidence(self):
        self.observe_result_reviews()
        records = self.read_records()
        records["review_attempt"]["attempt-result-first"]["origin"] = "imported"
        state = decisions.assess_problem_reviews(records, self.artifacts)
        self.assertIn("problem_evidence_unassessed", {item["code"] for item in state["obligations"]})

    def test_changed_claim_or_computation_invalidates_coverage(self):
        self.add_reading_and_computation()
        self.observe_result_reviews()
        self.expected["computations"][0]["assessment"]["interpretation"] = "Changed interpretation"
        state = decisions.assess_problem_reviews(self.read_records(), self.artifacts)
        self.assertIn("problem_evidence_unassessed", {item["code"] for item in state["obligations"]})

    def test_records_without_delivered_output_bytes_do_not_count_as_coverage(self):
        output, _ = self.add_reading_and_computation()
        original = protocol._evidence
        def omit_output(artifacts, value, **kwargs):
            return [entry for entry in original(artifacts, value, **kwargs)
                    if entry["artifact"]["sha256"] != output["sha256"]]
        with patch.object(protocol, "_evidence", side_effect=omit_output):
            self.observe_result_reviews()
        state = decisions.assess_problem_reviews(self.read_records(), self.artifacts)
        self.assertIn("problem_evidence_unassessed", {item["code"] for item in state["obligations"]})

    def test_reading_digest_without_delivered_passages_does_not_count_as_coverage(self):
        self.add_reading_and_computation()
        original = protocol._evidence
        omitted = {passage["excerpt"]["sha256"] for passage in self.expected["passages"]}
        def omit_passages(artifacts, value, **kwargs):
            return [entry for entry in original(artifacts, value, **kwargs)
                    if entry["artifact"]["sha256"] not in omitted]
        with patch.object(protocol, "_evidence", side_effect=omit_passages):
            self.observe_result_reviews()
        state = decisions.assess_problem_reviews(self.read_records(), self.artifacts)
        self.assertIn("problem_evidence_unassessed", {item["code"] for item in state["obligations"]})

    def test_source_request_continuation_preserves_the_problem_bar_boundary(self):
        self.add_reading_and_computation()
        version = self.expected["readings"][0]["version_id"]
        self.supporting = self.artifacts.put(b"A field standard.", "text/plain")
        self.prepare_review_route()
        output = self.build_review_output("bar")
        output["value"]["status"] = "unresolved"
        output["source_requests"] = [{"id": "decisive-source", "kind": "source",
            "version_id": version, "depth": "fulltext"}]
        pending = self.observe("bar", "first", output, record=False)
        self.assert_error("review_sources_pending", lambda: self.mutate(decisions.record_value_review, pending))
        assignment = self.assign("bar", "first", identifier="bar-first-source", prior_assignment_id="bar-first")
        packet = json.loads(self.artifacts.read(assignment["packet"]))
        self.assertEqual(packet["subject"], {"kind": "problem", "statement_id": "statement-1"})
        self.assertEqual(packet["pending_source_requests"], [])
        self.assertEqual(packet["source_deliveries"][0]["status"], "delivered")
        self.assertNotIn("PRIVATE_STATEMENT", json.dumps(packet))
        final = self.build_review_output("bar")
        with patch("research_harness.review_transport.send_request", return_value=self.build_provider_response(final)):
            self.mutate(protocol.invoke_assignment, {"id": "attempt-source-final", "assignment_id": assignment["id"]},
                credential="test-only")
        saved = self.mutate(decisions.record_value_review, dict(final, id="review-bar-first",
            dossier_id=None, assignment_id=assignment["id"]))["result"]
        self.assertEqual(saved["subject"], assignment["subject"])
        self.assertTrue(protocol.assignment_state(self.read_records(), self.artifacts, assignment["id"])["ready"])

    def test_new_statement_requires_new_result_reviews_and_preserves_field_bars(self):
        self.observe_result_reviews()
        revised = dict(self.statement, id="statement-2", statement="A changed exact statement")
        self.put("problem_statement", revised["id"], revised)
        self.put("problem_selection", "current", {"statement_id": revised["id"]})
        self.expected["claim"].update(statement_id=revised["id"], statement=revised["statement"])
        state = decisions.assess_problem_reviews(self.read_records(), self.artifacts)
        self.assertIn("problem_review_pair_required", {item["code"] for item in state["obligations"]})
        for reviewer in ("first", "second"):
            self.observe("result", reviewer, identifier="result-revised-" + reviewer)
        state = decisions.assess_problem_reviews(self.read_records(), self.artifacts)
        self.assertTrue(state["ready"], state["obligations"])


class ProblemOpennessReviewTests(ProblemCase):
    prepare_review_route = ProblemReviewTests.prepare_review_route
    build_provider_response = ProblemReviewTests.build_provider_response
    read_records = ProblemReviewTests.read_records
    build_review_output = ProblemReviewTests.build_review_output
    observe = ProblemReviewTests.observe

    def assign(self, role, reviewer, author="problem-author", identifier=None, **context):
        return ProblemReviewTests.assign(self, role, reviewer, author, identifier, **context)

    def setUp(self):
        super().setUp()
        self.initialize_problem()
        self.selected = self.select_statement()

    def observe_openness(self, outcome="supported_open", change_output=None):
        from research_harness.literature import record_search
        from research_harness.problem_openness import export_openness
        search = self.capture_search()
        search["question"] = self.selected["statement"]
        self.mutate(record_search, search)
        exported = export_openness(self.store, {"assignment_id": "openness-assignment", "reviewer_id": "native-reviewer",
            "minimum": {"searches": [{"formulation": self.selected["statement"], "search_id": search["id"]}],
                "settling_work_id": None, "duplicate_resolutions": []}}, self.root / "openness-packet")
        native = json.loads(self.artifacts.read(exported["native_packet"]))
        self.prepare_review_route()
        self.mutate(protocol.record_assignment, {"id": "openness-assignment", "route_id": "route", "dossier_id": None,
            "role": "native_math", "reviewer_id": "native-reviewer", "author_id": "problem-author",
            "context": {"native_packet": exported["native_packet"]}})
        openness = {"outcome": outcome, "statement_digest": native["claim_digest"],
            "evidence": [entry["digest"] for entry in native["evidence"]], "searched_at": "2026-09-07",
            "coverage": "The exact formulation was searched in the captured provider response.",
            "reason": "No settling result was exposed in the bounded captured search."}
        if outcome != "unresolved":
            openness["minimum"] = native["scientific_context"]["openness_minimum"]
        output = {"schema_version": 2, "subject_digest": native["subject_digest"], "claim_digest": native["claim_digest"],
            "reviewer": native["reviewer"], "decision": "approve", "findings": {
                **{key: "Assessed the exact supplied subject." for key in ("statement", "assumptions", "scope", "dependencies", "policy")},
                "openness": openness}}
        if change_output is not None:
            change_output(output)
        with patch("research_harness.review_transport.send_request", return_value=self.build_provider_response(output)):
            self.mutate(protocol.invoke_assignment, {"id": "openness-attempt", "assignment_id": "openness-assignment"},
                credential="test-only")
        return output

    def test_exported_native_problem_packet_accepts_an_observed_supported_open_finding(self):
        output = self.observe_openness()
        saved = self.mutate(self.load_problem_module().record_openness, {"assignment_id": "openness-assignment"})["result"]
        self.assertEqual(saved["outcome"], "supported_open")
        self.assertEqual(saved["minimum"], output["findings"]["openness"]["minimum"])
        self.assertEqual(saved["statement_id"], self.selected["id"])

    def test_observed_unresolved_finding_records_without_minimum_audit(self):
        self.observe_openness("unresolved")
        with patch("research_harness.openness_minimum.audit_minimum", side_effect=AssertionError("unresolved audit")):
            saved = self.mutate(self.load_problem_module().record_openness, {"assignment_id": "openness-assignment"})["result"]
        self.assertEqual(saved["outcome"], "unresolved")

    def test_observed_finding_for_a_different_statement_is_refused(self):
        self.observe_openness(change_output=lambda output: output["findings"]["openness"].update(statement_digest="0" * 64))
        self.assert_error("openness_statement_mismatch", lambda: self.mutate(self.load_problem_module().record_openness,
            {"assignment_id": "openness-assignment"}))

    def test_unresolved_output_cannot_rebind_an_old_packet_to_a_new_statement(self):
        def change(output):
            revised = self.select_statement(identifier="statement-2", statement="A different exact proposition.")
            output["findings"]["openness"]["statement_digest"] = self.load_problem_module().compute_statement_digest(revised)
        self.observe_openness("unresolved", change_output=change)
        self.assert_error("openness_statement_mismatch", lambda: self.mutate(self.load_problem_module().record_openness,
            {"assignment_id": "openness-assignment"}))

    def test_real_problem_evidence_is_observed_and_assessed_without_artifact_writes(self):
        self.observe_openness()
        self.mutate(self.load_problem_module().record_openness, {"assignment_id": "openness-assignment"})
        reading = self.record_full_reading()
        self.mutate(self.load_problem_module().record_value, self.build_value([reading]))
        self.supporting = self.artifacts.put(b"The original comparison establishes the field standard.", "text/plain")
        for stage in ("bar", "result"):
            for reviewer in ("first", "second"):
                self.observe(stage, reviewer)
        with patch.object(type(self.artifacts), "put", side_effect=AssertionError("read-only review assessment")):
            state = decisions.assess_problem_reviews(self.read_records(), self.artifacts)
        self.assertTrue(state["ready"], state["obligations"])
        from research_harness.reading import record_reading
        self.mutate(record_reading, self.abstract_note(reading["version_id"], note_id="unused-abstract"))
        after = decisions.assess_problem_reviews(self.read_records(), self.artifacts)
        self.assertTrue(after["ready"], after["obligations"])
        self.assertEqual(after["problem_evidence_digest"], state["problem_evidence_digest"])

    def prepare_changed_value_reviews(self):
        self.observe_openness()
        self.mutate(self.load_problem_module().record_openness, {"assignment_id": "openness-assignment"})
        item = self.record_full_reading()
        self.mutate(self.load_problem_module().record_value, self.build_value([item]))
        self.supporting = self.artifacts.put(b"The exact earlier result comparison.", "text/plain")
        for reviewer in ("first", "second"):
            self.observe("bar", reviewer)
        old = self.build_review_output("result")
        old["objections"] = [{"id": "prior-result-objection", "claim": "PRIVATE_PRIOR_RESULT_OBJECTION",
            "reason": "The earlier value paragraph needs a narrower interpretation.", "evidence": [self.supporting],
            "resolution_condition": "Bind the narrower interpretation to the read comparator."}]
        self.observe("result", "first", old)
        self.observe("result", "second")
        before = decisions.assess_problem_reviews(self.read_records(), self.artifacts)
        self.assertFalse(before["ready"], before["obligations"])
        self.assertIn("research_objection_unresolved", {item["code"] for item in before["obligations"]})
        changed = self.build_value([item], identifier="value-2")
        changed["motivation"][0]["text"] = "The narrowed consequence connects these exact finite constructions."
        self.mutate(self.load_problem_module().record_value, changed)
        return before

    def build_refreshed_result(self):
        output = self.build_review_output("result")
        output["value"]["objection_findings"] = [{"id": "prior-result-objection", "status": "resolved",
            "reason": "The new bound value paragraph makes the narrower supported claim.", "evidence": [self.supporting]}]
        return output

    def test_changed_value_can_refresh_observed_results_in_their_original_slots(self):
        before = self.prepare_changed_value_reviews()
        pending = decisions.assess_problem_reviews(self.read_records(), self.artifacts)
        self.assertIn("problem_evidence_unassessed", {item["code"] for item in pending["obligations"]})
        self.assertIn("research_objection_unresolved", {item["code"] for item in pending["obligations"]})
        for reviewer in ("first", "second"):
            output = self.build_refreshed_result()
            self.observe("result", reviewer, output, identifier="refreshed-" + reviewer)
            records = self.read_records()
            original = records["review_assignment"]["result-" + reviewer]
            refreshed = records["review_assignment"]["refreshed-" + reviewer]
            packet = json.loads(self.artifacts.read(refreshed["packet"]))
            self.assertEqual(refreshed["subject"], {"kind": "problem", "statement_id": self.selected["id"]})
            self.assertEqual(refreshed["slot_id"], original["slot_id"])
            self.assertEqual(refreshed["session_id"], original["session_id"])
            self.assertEqual(refreshed["history_assignment_id"], original["id"])
            self.assertEqual(packet["problem_refresh"]["assignment_id"], original["id"])
            self.assertNotEqual(packet["problem_evidence_digest"], before["problem_evidence_digest"])
            if reviewer == "first":
                self.assertIn("PRIVATE_PRIOR_RESULT_OBJECTION", json.dumps(refreshed["history"]))
            self.assert_error("review_resampling_forbidden", lambda: self.assign("result", reviewer,
                identifier="same-evidence-" + reviewer))
            self.assert_error("review_resampling_forbidden", lambda: self.assign("result", reviewer,
                identifier="same-evidence-explicit-" + reviewer, prior_assignment_id=refreshed["id"]))
            if reviewer == "first":
                partial = decisions.assess_problem_reviews(self.read_records(), self.artifacts)
                self.assertFalse(partial["ready"])
                self.assertIn("problem_evidence_unassessed", {item["code"] for item in partial["obligations"]})
        state = decisions.assess_problem_reviews(self.read_records(), self.artifacts)
        self.assertTrue(state["ready"], state["obligations"])
        self.assertEqual(set(state["review_ids"]), {"review-refreshed-first", "review-refreshed-second"})
        self.assertIn("review-result-first", self.read_records()["value_review"])
        decision = self.mutate(self.load_problem_module().record_decision, {"id": "refreshed-decision", "action": "post",
            "review_ids": state["review_ids"], "bar_review_ids": state["bar_review_ids"],
            "reason": "Both original reviewers assessed the changed bound value evidence."})["result"]
        self.assertEqual(decision["problem_evidence_digest"], state["problem_evidence_digest"])

    def test_changed_value_refresh_requires_the_prior_result_objections(self):
        self.prepare_changed_value_reviews()
        pending = decisions.assess_problem_reviews(self.read_records(), self.artifacts)
        self.assertIn("research_objection_unresolved", {item["code"] for item in pending["obligations"]})
        payload = self.observe("result", "first", record=False, identifier="refreshed-first")
        self.assert_error("review_objection_history_incomplete", lambda: self.mutate(decisions.record_value_review, payload))


    def test_settled_source_request_repair_does_not_leave_its_old_parent_pending(self):
        from research_harness.problem_delivery import assess_source_requests
        version = self.metadata(76002)
        self.supporting = self.artifacts.put(b"The observed context exposure.", "text/plain")
        self.prepare_review_route()
        output = self.build_review_output("bar")
        output["value"]["status"] = "unresolved"
        output["source_requests"] = [{"id": "abstract", "kind": "source", "version_id": version, "depth": "abstract"}]
        self.observe("bar", "first", output, record=False)
        self.mutate(protocol.record_context_event, {"id": "exposure", "assignment_id": "bar-first",
            "kind": "contamination", "source": "author_history", "reason": "Author advocacy entered the context.",
            "evidence": [self.supporting]})
        def respond_to_probe(route, request, credential):
            packet = json.loads(request["input"][0]["content"])
            return self.build_provider_response({"allowed_control": packet["allowed_control"],
                "evidence_control": packet["evidence_control"], "excluded_controls": []})
        with patch("research_harness.review_transport.send_request", side_effect=respond_to_probe):
            self.mutate(protocol.probe_route, {"id": "repair-probe", "route_id": "route"}, credential="test-only")
        self.assign("bar", "repair-reviewer", identifier="repair", context_repair={
            "assignment_id": "bar-first", "event_ids": ["exposure"], "probe_id": "repair-probe",
            "reason": "Reassess the exact source request in a verified fresh context.", "evidence": [self.supporting]})
        repair_output = self.build_review_output("bar")
        repair_output["value"]["status"] = "unresolved"
        repair_output["source_requests"] = [{"id": "page", "kind": "inventory_page", "page": 0}]
        with patch("research_harness.review_transport.send_request", return_value=self.build_provider_response(repair_output)):
            self.mutate(protocol.invoke_assignment, {"id": "repair-attempt", "assignment_id": "repair"}, credential="test-only")
        fulfilled = self.assign("bar", "repair-reviewer", identifier="fulfilled", prior_assignment_id="repair")
        packet = json.loads(self.artifacts.read(fulfilled["packet"]))
        self.assertEqual(packet["pending_source_requests"], [])
        final = self.build_review_output("bar")
        final["reassessment"] = {"assignment_id": "bar-first", "findings": []}
        with patch("research_harness.review_transport.send_request", return_value=self.build_provider_response(final)):
            self.mutate(protocol.invoke_assignment, {"id": "fulfilled-attempt", "assignment_id": "fulfilled"}, credential="test-only")
        self.mutate(decisions.record_value_review, dict(final, id="review-bar-repaired", dossier_id=None, assignment_id="fulfilled"))
        self.assertEqual(assess_source_requests(self.read_records(), self.artifacts), [])

