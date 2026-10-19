"""Problem adjudication can use current evidence while repairs retain objection history."""

import json
from unittest.mock import patch

import test_research_problem_delivery as delivery_fixtures
import test_research_problem_objections as objection_fixtures
from research_harness import literature, reading, research_decisions as decisions, review_protocol as protocol


class ProblemAdjudicationContractTests(delivery_fixtures.ProblemDeliveryCase):
    make_objection = objection_fixtures.ProblemObjectionTests.make_objection
    make_finding = objection_fixtures.ProblemObjectionTests.make_finding
    prepare_reviews = objection_fixtures.ProblemObjectionTests.prepare_reviews

    def read_records(self):
        return self.store.snapshot()["records"]

    def add_new_source(self):
        version = self.metadata(2)
        capture = self.capture(version, body="A new comparator B proves the previously missing implication. References: none.")
        bundle = self.bundle(version, capture=capture, bundle_id="new-comparator")
        self.mutate(literature.import_bundle, bundle)
        self.mutate(reading.record_reading, self.full_note(bundle, "read-new-comparator"))
        item = {"kind": "reading", "version_id": version, "reading_id": "read-new-comparator"}
        self.mutate(self.load_problem_module().record_value,
            self.build_value([self.item, item], identifier="value-2"))
        return self.artifacts.put(self.artifacts.read(capture["text"]), "text/plain")

    def assign_focused_review(self, **context):
        return self.assign("adjudicator", "focused-third", identifier="focused",
            review_ids=["review-result-first", "review-result-second"],
            objection_id="unsupported-implication", **context)

    def observe_focused_review(self, evidence, *, correction_admissible=None):
        output = {"objection_id": "unsupported-implication", "disposition": "not_upheld",
            "reason": "The delivered comparator establishes the exact disputed implication.",
            "evidence": [evidence], "correction_admissible": correction_admissible}
        with patch("research_harness.review_transport.send_request", return_value=self.build_provider_response(output)):
            self.mutate(protocol.invoke_assignment, {"id": "focused-attempt", "assignment_id": "focused"}, credential="test-only")
        return self.mutate(protocol.record_adjudication,
            dict(output, id="focused-finding", assignment_id="focused"))["result"]

    def build_correction(self, evidence):
        return {"kind": "omission", "claim": "The original comparator's exact quantifier was overlooked.",
            "reason": "Reconsider the cited passage supplied to the original reviewers.",
            "evidence": [evidence], "prior_adjudication_id": None, "new_error_explanation": None}

    def test_focused_review_delivers_and_permits_a_genuinely_new_source(self):
        self.prepare_reviews(result_reviewer="first")
        original = self.read_records()["review_assignment"]["result-first"]
        original_digest = json.loads(self.artifacts.read(original["packet"]))["problem_evidence_digest"]
        new_source = self.add_new_source()
        assignment = self.assign_focused_review()
        packet = json.loads(self.artifacts.read(assignment["packet"]))
        self.assertEqual(assignment["prompt"]["template_id"], "exactory-problem-review-v2/adjudicator")
        self.assertEqual(packet["protocol"], "exactory-problem-review-v2")
        self.assertIn("current_evidence", assignment["prompt"]["text"])
        self.assertNotIn("Assess only original_evidence", assignment["prompt"]["text"])
        self.assertIn(new_source, packet["current_evidence"])
        self.assertNotIn(new_source, packet["original_evidence"])
        self.assertNotEqual(packet["problem_evidence_digest"], original_digest)
        delivered = next(entry for entry in packet["evidence"] if entry["artifact"] == new_source)
        self.assertEqual(delivered["content"].encode(), self.artifacts.read(new_source))
        self.assertIn(b"new comparator B", self.artifacts.read(new_source))
        saved = self.observe_focused_review(new_source)
        self.assertEqual(saved["problem_evidence_digest"], packet["problem_evidence_digest"])
        state = protocol.adjudication_state(self.read_records(), self.artifacts, saved["id"])
        self.assertTrue(state["ready"], state["obligations"])

    def test_problem_adjudicator_prompt_preserves_old_assignment_authentication(self):
        problem = {"role": "adjudicator", "subject": {"kind": "problem", "statement_id": "statement-1"}}
        prompt = protocol.canonical_prompt("adjudicator", orchestration="problem_creation")
        self.assertTrue(protocol._prompt_matches(dict(problem, prompt=prompt)))
        self.assertTrue(protocol._prompt_matches(dict(problem, prompt=protocol.canonical_prompt("adjudicator"))))
        changed = dict(prompt, text=prompt["text"] + " Widen correction admissibility.")
        self.assertFalse(protocol._prompt_matches(dict(problem, prompt=changed)))
        self.assertFalse(protocol._prompt_matches({"role": "adjudicator", "prompt": prompt}))

    def test_focused_problem_adjudication_cannot_be_a_bar_context_repair(self):
        self.assert_error("invalid_review_context", lambda: protocol.canonical_prompt(
            "adjudicator", orchestration="problem_creation", repair=True))

    def test_new_source_cannot_establish_a_correction_to_the_original_context(self):
        self.prepare_reviews(result_reviewer="first")
        new_source = self.add_new_source()
        self.assert_error("invalid_review_correction", lambda: self.assign_focused_review(
            correction=self.build_correction(new_source)))

    def test_original_delivered_source_remains_admissible_for_a_correction(self):
        self.prepare_reviews(result_reviewer="first")
        self.add_new_source()
        original = self.read_records()["review_assignment"]["result-first"]
        packet = json.loads(self.artifacts.read(original["packet"]))
        evidence = packet["evidence"][0]["artifact"]
        assignment = self.assign_focused_review(correction=self.build_correction(evidence))
        focused = json.loads(self.artifacts.read(assignment["packet"]))
        self.assertIn(evidence, focused["original_evidence"])
        saved = self.observe_focused_review(evidence, correction_admissible=True)
        self.assertTrue(protocol.adjudication_state(self.read_records(), self.artifacts, saved["id"])["ready"])

    def prepare_bar_repair(self):
        self.initialize_problem()
        self.supporting = self.artifacts.put(b"The original field bar leaves an implication unsupported.", "text/plain")
        self.prepare_review_route()
        output = self.build_review_output("bar")
        output["objections"] = [self.make_objection()]
        self.observe("bar", "first", output)
        self.mutate(protocol.record_context_event, {"id": "exposure", "assignment_id": "bar-first",
            "kind": "contamination", "source": "author_history", "reason": "Author advocacy entered the original bar context.",
            "evidence": [self.supporting]})
        def respond_to_probe(route, request, credential):
            packet = json.loads(request["input"][0]["content"])
            return self.build_provider_response({"allowed_control": packet["allowed_control"],
                "evidence_control": packet["evidence_control"], "excluded_controls": []})
        with patch("research_harness.review_transport.send_request", side_effect=respond_to_probe):
            self.mutate(protocol.probe_route, {"id": "repair-probe", "route_id": "route"}, credential="test-only")
        return self.assign("bar", "replacement", identifier="repaired-bar", context_repair={
            "assignment_id": "bar-first", "event_ids": ["exposure"], "probe_id": "repair-probe",
            "reason": "Reassess the original bar and its objection in a verified independent context.",
            "evidence": [self.supporting]})

    def observe_repaired_bar(self, findings):
        output = self.build_review_output("bar")
        output["value"]["objection_findings"] = findings
        output["reassessment"] = {"assignment_id": "bar-first", "findings": [{
            "review_id": "review-bar-first", "disposition": "revised",
            "reason": "The prior material objection still requires the missing implication.", "evidence": [self.supporting]}]}
        with patch("research_harness.review_transport.send_request", return_value=self.build_provider_response(output)):
            self.mutate(protocol.invoke_assignment, {"id": "repair-attempt", "assignment_id": "repaired-bar"}, credential="test-only")
        return self.mutate(decisions.record_value_review,
            dict(output, id="review-repaired-bar", dossier_id=None, assignment_id="repaired-bar"))["result"]

    def test_bar_repair_prompt_requires_and_records_the_predecessor_objection(self):
        assignment = self.prepare_bar_repair()
        self.assertIn("despite the ordinary empty-bar default", assignment["prompt"]["text"])
        packet = json.loads(self.artifacts.read(assignment["packet"]))
        self.assertEqual(packet["historical_reviews"][0]["payload"]["objections"][0]["id"], self.make_objection()["id"])
        saved = self.observe_repaired_bar([self.make_finding("continuing")])
        self.assertEqual(saved["payload"]["value"]["objection_findings"], [self.make_finding("continuing")])
        self.assertEqual(self.read_records()["value_review"]["review-bar-first"]["payload"]["objections"], [self.make_objection()])

    def test_bar_repair_still_refuses_to_drop_the_predecessor_objection(self):
        self.prepare_bar_repair()
        self.assert_error("review_objection_history_incomplete", lambda: self.observe_repaired_bar([]))
