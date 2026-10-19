"""Material problem objections require observed resolution or focused adjudication."""

import json
from unittest.mock import patch

from research_harness import research_decisions as decisions, review_protocol as protocol
from research_harness.errors import ResearchError
from research_harness.problem_delivery import export_problem, assess_post
import test_research_problem_delivery as fixtures
import test_research_problem_slate as slate_fixtures


class ProblemObjectionTests(fixtures.ProblemDeliveryCase):
    build_slate_output = slate_fixtures.ProblemSlateTests.build_slate_output

    def make_objection(self):
        return {"id": "unsupported-implication", "claim": "The stated implication exceeds the read comparator.",
            "reason": "The exact quantifier is unsupported.", "evidence": [self.supporting],
            "resolution_condition": "Supply the missing implication or narrow the supported claim."}

    def make_finding(self, status):
        return {"id": "unsupported-implication", "status": status,
            "reason": "The supplied comparator and changed paragraph establish this disposition.",
            "evidence": [self.supporting]}

    def prepare_problem_evidence(self):
        self.initialize_problem()
        self.record_candidate()
        self.selected = self.select_statement(candidate_id="candidate-1")
        self.item = self.record_full_reading()
        from research_harness.problem_attempt import record_attempt
        from test_research_problem_attempt import build_attempt
        self.mutate(record_attempt, build_attempt(reading=self.item))
        self.observe_openness()
        self.mutate(self.load_problem_module().record_openness, {"assignment_id": "openness-assignment"})
        self.mutate(self.load_problem_module().record_value, self.build_value([self.item]))
        from test_research_problem_assessment import build_assessment
        self.mutate(self.load_problem_module().record_assessment, build_assessment(self.selected))
        self.supporting = self.artifacts.put(b"The read comparator states an existential bound only.", "text/plain")

    def prepare_reviews(self, *, bar_reviewer=None, result_reviewer=None, slate_reviewer=None, results=True):
        self.prepare_problem_evidence()
        for reviewer in ("first", "second"):
            output = self.build_review_output("bar")
            if reviewer == bar_reviewer:
                output["objections"] = [self.make_objection()]
            self.observe("bar", reviewer, output)
        if slate_reviewer is not None:
            output = self.build_slate_output(["candidate-1"])
            output["objections"] = [dict(self.make_objection(), candidate_ids=["candidate-1"])]
            self.observe("slate", slate_reviewer, output)
        self.record_slate(review_ids=[] if slate_reviewer is None else ["review-slate-" + slate_reviewer])
        if not results:
            return
        for reviewer in ("first", "second"):
            output = self.build_review_output("result")
            if bar_reviewer is not None or slate_reviewer is not None:
                output["value"]["objection_findings"] = [self.make_finding("continuing")]
            if reviewer == result_reviewer:
                output["objections"] = [self.make_objection()]
            self.observe("result", reviewer, output)

    def refresh_reviews(self, status):
        changed = self.build_value([self.item], identifier="value-2")
        changed["motivation"][0]["text"] = "A narrower implication follows from the supplied comparator."
        self.mutate(self.load_problem_module().record_value, changed)
        for reviewer in ("first", "second"):
            output = self.build_review_output("result")
            output["value"]["objection_findings"] = [self.make_finding(status if reviewer == "first" else "resolved")]
            self.observe("result", reviewer, output, identifier="refreshed-" + reviewer)

    def prepare_repaired_result(self, *, changed, slot="first"):
        self.prepare_reviews(result_reviewer="second")
        if changed:
            self.mutate(self.load_problem_module().record_value, self.build_value([self.item], identifier="value-copy"))
        self.mutate(protocol.record_context_event, {"id": "exposure", "assignment_id": "result-" + slot,
            "kind": "contamination", "source": "author_history", "reason": "The original assessor received author advocacy.",
            "evidence": [self.supporting]})
        def respond_probe(route, request, credential):
            packet = json.loads(request["input"][0]["content"])
            return self.build_provider_response({"allowed_control": packet["allowed_control"],
                "evidence_control": packet["evidence_control"], "excluded_controls": []})
        with patch("research_harness.review_transport.send_request", side_effect=respond_probe):
            self.mutate(protocol.probe_route, {"id": "repair-probe", "route_id": "route"}, credential="test-only")
        assignment = self.assign("result", "repaired-" + slot, identifier="repaired-result",
            context_repair={"assignment_id": "result-" + slot, "event_ids": ["exposure"], "probe_id": "repair-probe",
                "reason": "Reassess the original science in the independently verified context.", "evidence": [self.supporting]})
        return json.loads(self.artifacts.read(assignment["packet"]))

    def prepare_contested_repair(self):
        self.prepare_repaired_result(changed=False, slot="second")
        output = self.build_review_output("result")
        output["value"]["objection_findings"] = [self.make_finding("contested")]
        output["reassessment"] = {"assignment_id": "result-second", "findings": [{
            "review_id": "review-result-second", "disposition": "revised",
            "reason": "The supplied evidence leaves the historical objection contested.", "evidence": [self.supporting]}]}
        with patch("research_harness.review_transport.send_request", return_value=self.build_provider_response(output)):
            self.mutate(protocol.invoke_assignment, {"id": "repaired-attempt", "assignment_id": "repaired-result"}, credential="test-only")
        self.mutate(decisions.record_value_review,
            dict(output, id="review-repaired-result", dossier_id=None, assignment_id="repaired-result"))
        self.assert_blocked_delivery()

    def assert_blocked_delivery(self, decision_code="research_objection_unresolved"):
        records = self.store.snapshot()["records"]
        reviews = decisions.assess_problem_reviews(records, self.artifacts)
        self.assertIn("research_objection_unresolved", {item["code"] for item in reviews["obligations"]})
        self.assertFalse(assess_post(records, self.artifacts)["ready"])
        payload = {"id": "decision", "action": "post", "review_ids": reviews["review_ids"],
            "bar_review_ids": reviews["bar_review_ids"], "reason": "All five assurances passed."}
        actions = ((lambda: self.mutate(self.load_problem_module().record_decision, payload), decision_code),
            (lambda: export_problem(self.store, self.root / "blocked-export"), "readiness_required"),
            (lambda: self.mutate(self.load_problem_module().record_posted,
                {"problem_id": "blocked-post", "export_directory": str(self.root / "blocked-export")}), "readiness_required"))
        for action, code in actions:
            with self.assertRaises(ResearchError) as caught:
                action()
            self.assertEqual(caught.exception.code, code)
            self.assertIn("research_objection_unresolved", {item["code"] for item in caught.exception.details["obligations"]})

    def assert_delivered(self):
        reviews = decisions.assess_problem_reviews(self.store.snapshot()["records"], self.artifacts)
        self.assertTrue(reviews["ready"], reviews["obligations"])
        self.mutate(self.load_problem_module().record_decision, {"id": "decision", "action": "post", "review_ids": reviews["review_ids"],
            "bar_review_ids": reviews["bar_review_ids"], "reason": "Every material objection has an evidenced disposition."})
        directory = self.root / "approved-export"
        export_problem(self.store, directory)
        saved = self.mutate(self.load_problem_module().record_posted,
            {"problem_id": "approved-post", "export_directory": str(directory)})["result"]
        self.assertEqual(saved["statement_id"], self.selected["id"])

    def observe_adjudication(self, disposition="not_upheld", reviewer="focused-third", review_ids=None, identifier="focused"):
        reviews = decisions.assess_problem_reviews(self.store.snapshot()["records"], self.artifacts)
        assignment = self.assign("adjudicator", reviewer, identifier=identifier, review_ids=review_ids or reviews["review_ids"],
            objection_id="unsupported-implication")
        packet = json.loads(self.artifacts.read(assignment["packet"]))
        self.assertEqual(packet["subject"], {"kind": "problem", "statement_id": self.selected["id"]})
        self.assertNotIn("dossier", packet)
        output = {"objection_id": "unsupported-implication", "disposition": disposition,
            "reason": "The exact supplied quantifier resolves the disputed implication.",
            "evidence": [self.supporting], "correction_admissible": None}
        with patch("research_harness.review_transport.send_request", return_value=self.build_provider_response(output)):
            self.mutate(protocol.invoke_assignment, {"id": identifier + "-attempt", "assignment_id": identifier}, credential="test-only")
        return self.mutate(protocol.record_adjudication, dict(output, id=identifier + "-finding", assignment_id=identifier))["result"]

    def test_current_first_bar_objection_blocks_decision_export_and_posted(self):
        self.prepare_reviews(bar_reviewer="first")
        self.assert_blocked_delivery()

    def test_current_second_bar_objection_blocks_decision_export_and_posted(self):
        self.prepare_reviews(bar_reviewer="second")
        self.assert_blocked_delivery()

    def test_current_first_result_objection_blocks_decision_export_and_posted(self):
        self.prepare_reviews(result_reviewer="first")
        self.assert_blocked_delivery()

    def test_current_second_result_objection_blocks_decision_export_and_posted(self):
        self.prepare_reviews(result_reviewer="second")
        self.assert_blocked_delivery()

    def test_continuing_predecessor_finding_blocks_decision_export_and_posted(self):
        self.prepare_reviews(result_reviewer="first")
        self.refresh_reviews("continuing")
        self.assert_blocked_delivery()

    def test_contested_predecessor_finding_blocks_decision_export_and_posted(self):
        self.prepare_reviews(result_reviewer="second")
        self.refresh_reviews("contested")
        self.assert_blocked_delivery()

    def test_both_observed_evidenced_resolutions_allow_delivery(self):
        self.prepare_reviews(result_reviewer="first")
        self.refresh_reviews("resolved")
        self.assert_delivered()

    def test_resolution_of_a_bar_objection_allows_delivery(self):
        self.prepare_reviews(bar_reviewer="second")
        self.refresh_reviews("resolved")
        self.assert_delivered()

    def test_observed_focused_adjudication_allows_delivery_without_a_dossier(self):
        self.prepare_reviews(result_reviewer="first")
        self.observe_adjudication()
        self.assert_delivered()

    def test_upheld_adjudication_keeps_the_post_blocked(self):
        self.prepare_reviews(result_reviewer="first")
        self.observe_adjudication("upheld")
        self.assert_blocked_delivery()

    def test_unresolved_adjudication_keeps_the_post_blocked(self):
        self.prepare_reviews(result_reviewer="first")
        self.observe_adjudication("unresolved")
        self.assert_blocked_delivery()

    def test_cited_slate_review_objection_reaches_the_result_packets_and_blocks_the_post(self):
        self.prepare_reviews(slate_reviewer="first")
        records = self.store.snapshot()["records"]
        for reviewer in ("first", "second"):
            packet = json.loads(self.artifacts.read(records["review_assignment"]["result-" + reviewer]["packet"]))
            self.assertEqual([objection["id"] for objection in packet["material_objections"]], ["unsupported-implication"])
        self.assert_blocked_delivery()

    def test_result_without_a_finding_for_a_cited_slate_objection_is_refused(self):
        self.prepare_reviews(slate_reviewer="second", results=False)
        payload = self.observe("result", "first", self.build_review_output("result"), record=False)
        self.assert_error("review_objection_history_incomplete", lambda: self.mutate(decisions.record_value_review, payload))

    def test_evidenced_resolution_of_a_slate_objection_allows_delivery(self):
        self.prepare_reviews(slate_reviewer="first")
        self.refresh_reviews("resolved")
        self.assert_delivered()

    def test_focused_adjudication_can_resolve_a_slate_objection_with_the_bars(self):
        self.prepare_reviews(slate_reviewer="first")
        self.observe_adjudication(review_ids=["review-bar-first", "review-bar-second"])
        records = self.store.snapshot()["records"]
        packet = json.loads(self.artifacts.read(records["review_assignment"]["focused"]["packet"]))
        self.assertEqual([assessment["id"] for assessment in packet["assessments"]],
                         ["review-bar-first", "review-bar-second", "review-slate-first"])
        self.assertEqual(packet["objection"]["id"], "unsupported-implication")
        self.assert_delivered()

    def test_a_slate_objection_against_the_selected_candidate_survives_an_unrelated_candidate(self):
        self.prepare_reviews(slate_reviewer="first", results=False)
        self.record_candidate("candidate-2", statement="Does the bound hold for odd $n$?")
        self.record_slate("slate-2", deferred=[{"candidate_id": "candidate-2", "evidence_needed": "A documented active line."}])
        packet = protocol.build_packet(self.store.snapshot()["records"], self.artifacts, None, "result", "first")
        self.assertEqual([objection["id"] for objection in packet["material_objections"]], ["unsupported-implication"])
        for reviewer in ("first", "second"):
            output = self.build_review_output("result")
            output["value"]["objection_findings"] = [self.make_finding("continuing")]
            self.observe("result", reviewer, output)
        self.assert_blocked_delivery()
        self.refresh_reviews("resolved")
        self.assert_delivered()

    def test_a_slate_objection_naming_only_a_deferred_candidate_does_not_block_the_selected_statement(self):
        self.prepare_reviews(results=False)
        self.record_candidate("candidate-2", statement="Does the bound hold for odd $n$?")
        output = self.build_slate_output(["candidate-1", "candidate-2"])
        output["objections"] = [dict(self.make_objection(), candidate_ids=["candidate-2"])]
        self.observe("slate", "first", output)
        self.record_slate("slate-2", deferred=[{"candidate_id": "candidate-2", "evidence_needed": "A documented active line."}],
                          review_ids=["review-slate-first"])
        for reviewer in ("first", "second"):
            self.observe("result", reviewer)
        packet = json.loads(self.artifacts.read(self.store.snapshot()["records"]["review_assignment"]["result-first"]["packet"]))
        self.assertEqual(packet["material_objections"], [])
        self.assert_delivered()

    def test_a_slate_objection_follows_new_versions_of_the_selected_candidate(self):
        self.prepare_reviews(slate_reviewer="first", results=False)
        for identifier, previous, changes in (
                ("candidate-1b", "candidate-1", {"notes": "A note that no reviewer receives."}),
                ("candidate-1c", "candidate-1b", {"statement": r"For every $n \geq 3$, does $B(n)\leq n^2$ hold?"})):
            with self.subTest(candidate=identifier):
                self.record_candidate(identifier, previous=previous, **changes)
                self.record_slate("slate-" + identifier, selected=[identifier])
                self.select_statement(identifier="statement-" + identifier, candidate_id=identifier)
                packet = protocol.build_packet(self.store.snapshot()["records"], self.artifacts, None, "result", "first")
                self.assertEqual([(row["id"], row["candidate_ids"]) for row in packet["material_objections"]],
                                 [("unsupported-implication", [identifier])])

    def test_a_whole_slate_objection_survives_an_added_candidate(self):
        self.prepare_reviews(results=False)
        output = self.build_slate_output(["candidate-1"])
        output["objections"] = [dict(self.make_objection(), candidate_ids=[])]
        self.observe("slate", "first", output)
        self.record_candidate("candidate-2", statement="Does the bound hold for odd $n$?")
        self.record_slate("slate-2", deferred=[{"candidate_id": "candidate-2", "evidence_needed": "A documented active line."}])
        packet = protocol.build_packet(self.store.snapshot()["records"], self.artifacts, None, "result", "first")
        self.assertEqual([(row["id"], row["candidate_ids"]) for row in packet["material_objections"]],
                         [("unsupported-implication", [])])

    def test_a_slate_objection_on_a_rejected_lineage_does_not_block_the_selected_statement(self):
        self.prepare_reviews(results=False)
        self.record_candidate("candidate-2", statement="Does the bound hold for odd $n$?")
        output = self.build_slate_output(["candidate-1", "candidate-2"])
        output["objections"] = [dict(self.make_objection(), candidate_ids=["candidate-2"])]
        self.observe("slate", "first", output)
        self.record_slate("slate-2", rejected=[{"candidate_id": "candidate-2", "reason": "A restatement of a known question."}],
                          review_ids=["review-slate-first"])
        for reviewer in ("first", "second"):
            self.observe("result", reviewer)
        packet = json.loads(self.artifacts.read(self.store.snapshot()["records"]["review_assignment"]["result-first"]["packet"]))
        self.assertEqual(packet["material_objections"], [])
        self.assert_delivered()

    def test_a_repaired_result_reviewer_refreshes_with_the_current_bars(self):
        self.prepare_contested_repair()
        changed = self.build_value([self.item], identifier="value-2")
        changed["motivation"][0]["text"] = "A narrower implication follows from the supplied comparator."
        self.mutate(self.load_problem_module().record_value, changed)
        refreshed = self.assign("result", "repaired-second", identifier="repaired-refresh")
        packet = json.loads(self.artifacts.read(refreshed["packet"]))
        self.assertEqual(refreshed["context"], {"prior_assignment_id": "repaired-result"})
        self.assertEqual((sorted(packet["bar_ids"]), [bar["id"] for bar in packet["own_bars"]]),
                         (["review-bar-first", "review-bar-second"], ["review-bar-second"]))
        output = self.build_review_output("result")
        output["value"]["objection_findings"] = [self.make_finding("resolved")]
        output["reassessment"] = {"assignment_id": "result-second", "findings": [{
            "review_id": "review-result-second", "disposition": "revised",
            "reason": "The changed paragraph narrows the implication to the supplied comparator.", "evidence": [self.supporting]}]}
        with patch("research_harness.review_transport.send_request", return_value=self.build_provider_response(output)):
            self.mutate(protocol.invoke_assignment, {"id": "repaired-refresh-attempt", "assignment_id": "repaired-refresh"},
                        credential="test-only")
        saved = self.mutate(decisions.record_value_review,
                            dict(output, id="review-repaired-refresh", dossier_id=None, assignment_id="repaired-refresh"))["result"]
        self.assertEqual(saved["assignment_id"], "repaired-refresh")

    def update_slate_objection(self):
        """After a new candidate, the slate reviewer restates the objection with new evidence: a new material version."""
        self.record_candidate("candidate-2", statement="Does the bound hold for odd $n$?")
        counterexample = self.artifacts.put(b"A counterexample to the earlier resolution.", "text/plain")
        output = self.build_slate_output(["candidate-1", "candidate-2"])
        output["objections"] = [dict(self.make_objection(), candidate_ids=["candidate-1"], evidence=[counterexample],
            reason="The supplied counterexample refutes the earlier resolution.",
            resolution_condition="Answer the counterexample or narrow the claim.")]
        output["value"]["objection_findings"] = [self.make_finding("contested")]
        updated = self.observe("slate", "first", output, identifier="slate-first-refreshed")
        self.record_slate("slate-2", deferred=[{"candidate_id": "candidate-2", "evidence_needed": "A documented active line."}],
                          review_ids=[updated["id"]])
        return updated, counterexample

    def test_a_changed_objection_version_refreshes_the_fixed_adjudicator(self):
        bars = ["review-bar-first", "review-bar-second"]
        self.prepare_reviews(slate_reviewer="first")
        self.refresh_reviews("resolved")
        self.observe_adjudication(review_ids=bars)

        def reassign(reviewer, identifier):
            return self.assign("adjudicator", reviewer, identifier=identifier, review_ids=bars,
                               objection_id="unsupported-implication")
        self.assert_error("review_adjudicator_fixed", lambda: reassign("focused-third", "focused-unchanged"))
        self.update_slate_objection()
        for reviewer, status in (("first", "resolved"), ("second", "contested")):
            output = self.build_review_output("result")
            output["value"]["objection_findings"] = [self.make_finding(status)]
            self.observe("result", reviewer, output, identifier="reassessed-" + reviewer)
        reviews = decisions.assess_problem_reviews(self.store.snapshot()["records"], self.artifacts)
        self.assertIn("research_objection_unresolved", {row["code"] for row in reviews["obligations"]})
        self.assert_error("review_adjudicator_fixed", lambda: reassign("focused-fourth", "focused-other"))
        self.observe_adjudication(review_ids=bars, identifier="focused-refreshed")
        records = self.store.snapshot()["records"]
        previous, refreshed = (json.loads(self.artifacts.read(records["review_assignment"][identifier]["packet"]))
                               for identifier in ("focused", "focused-refreshed"))
        # The contested finding with its own reason and evidence is the latest version that the gate holds.
        [current] = decisions.collect_problem_post_objections(records, self.artifacts, [records["value_review"][identifier]
            for identifier in bars + ["review-reassessed-first", "review-reassessed-second"]])
        self.assertEqual(current["review_id"], "review-reassessed-second")
        self.assertEqual(refreshed["objection"]["material_version"], current["material_version"])
        self.assertEqual(refreshed["focused_refresh"], {"assignment_id": "focused", "reason": "version_changed",
            "previous_problem_evidence_digest": previous["problem_evidence_digest"],
            "problem_evidence_digest": refreshed["problem_evidence_digest"],
            "previous_material_version": previous["objection"]["material_version"],
            "material_version": current["material_version"]})
        self.assertEqual(records["review_assignment"]["focused-refreshed"]["history_assignment_id"], "focused")
        self.assert_error("review_adjudicator_fixed", lambda: reassign("focused-third", "focused-repeat"))
        self.assert_delivered()

    def test_an_updated_slate_objection_needs_a_resolution_of_its_new_version(self):
        self.prepare_reviews(slate_reviewer="first")
        self.refresh_reviews("resolved")
        self.assertTrue(decisions.assess_problem_reviews(self.store.snapshot()["records"], self.artifacts)["ready"])
        self.observe_adjudication(review_ids=["review-bar-first", "review-bar-second"])
        updated, counterexample = self.update_slate_objection()
        records = self.store.snapshot()["records"]
        packet = protocol.build_packet(records, self.artifacts, None, "result", "first")
        [objection] = packet["material_objections"]
        self.assertEqual((objection["review_id"], objection["evidence"]), (updated["id"], [counterexample]))
        # The first results' continuing finding gave its own reason, so it is the version between the two statements.
        self.assertEqual([row["review_id"] for row in objection["history"]], ["review-slate-first", "review-result-first"])
        reviews = decisions.assess_problem_reviews(records, self.artifacts)
        self.assertIn("research_objection_unresolved", {row["code"] for row in reviews["obligations"]})
        for reviewer in ("first", "second"):
            output = self.build_review_output("result")
            output["value"]["objection_findings"] = [self.make_finding("resolved")]
            self.observe("result", reviewer, output, identifier="reassessed-" + reviewer)
            assignment = self.store.snapshot()["records"]["review_assignment"]["reassessed-" + reviewer]
            self.assertEqual(assignment["context"], {"prior_assignment_id": "refreshed-" + reviewer})
        self.assert_delivered()

    def test_a_contested_slate_finding_with_new_evidence_is_a_new_objection_version(self):
        """Astra's N1 reproduction: the slate reviewer contests O1 with new evidence in its findings only."""
        self.prepare_reviews(slate_reviewer="first")
        self.refresh_reviews("resolved")
        self.assertTrue(decisions.assess_problem_reviews(self.store.snapshot()["records"], self.artifacts)["ready"])
        self.record_candidate("candidate-2", statement="Does the bound hold for odd $n$?")
        counterexample = self.artifacts.put(b"A counterexample to the earlier resolution.", "text/plain")
        finding = dict(self.make_finding("contested"), evidence=[counterexample],
                       reason="The supplied counterexample refutes the earlier resolution.")
        output = self.build_slate_output(["candidate-1", "candidate-2"])
        output["value"]["objection_findings"] = [finding]
        updated = self.observe("slate", "first", output, identifier="slate-first-refreshed")
        self.assertEqual(updated["payload"]["objections"], [])
        self.record_slate("slate-2", deferred=[{"candidate_id": "candidate-2", "evidence_needed": "A documented active line."}],
                          review_ids=[updated["id"]])
        records = self.store.snapshot()["records"]
        [objection] = protocol.build_packet(records, self.artifacts, None, "result", "first")["material_objections"]
        stated = dict(self.make_objection(), candidate_ids=["candidate-1"])
        self.assertEqual({key: objection[key] for key in stated}, dict(stated, reason=finding["reason"], evidence=[counterexample]))
        self.assertEqual((objection["review_id"], objection["recorded_revision"]), (updated["id"], updated["recorded_revision"]))
        self.assertEqual([row["review_id"] for row in objection["history"]], ["review-slate-first", "review-result-first"])
        self.assertIn("research_objection_unresolved",
                      {row["code"] for row in decisions.assess_problem_reviews(records, self.artifacts)["obligations"]})
        for reviewer in ("first", "second"):
            output = self.build_review_output("result")
            output["value"]["objection_findings"] = [self.make_finding("resolved")]
            self.observe("result", reviewer, output, identifier="reassessed-" + reviewer)
            assignment = self.store.snapshot()["records"]["review_assignment"]["reassessed-" + reviewer]
            self.assertEqual(assignment["context"], {"prior_assignment_id": "refreshed-" + reviewer})
        self.assert_delivered()

    def reassess_on_the_same_evidence(self, *, bar_reviewer=None, result_reviewer=None, first_status="resolved"):
        """A slate objection o2 changes the currency, so both result reviewers reassess on the same evidence."""
        self.prepare_reviews(bar_reviewer=bar_reviewer, result_reviewer=result_reviewer)
        self.record_candidate("candidate-2", statement="Does the bound hold for odd $n$?")
        output = self.build_slate_output(["candidate-1", "candidate-2"])
        output["objections"] = [dict(self.make_objection(), id="o2", claim="The bound restates a known estimate.",
                                     candidate_ids=["candidate-1"])]
        slate = self.observe("slate", "first", output)
        self.record_slate("slate-2", deferred=[{"candidate_id": "candidate-2", "evidence_needed": "A documented active line."}],
                          review_ids=[slate["id"]])
        for reviewer in ("first", "second"):
            output = self.build_review_output("result")
            findings = [dict(self.make_finding("resolved"), id="o2")]
            if bar_reviewer is not None or reviewer == result_reviewer:
                findings.append(self.make_finding(first_status if reviewer == "first" else "resolved"))
            output["value"]["objection_findings"] = findings
            self.observe("result", reviewer, output, identifier="reassessed-" + reviewer)
            assignment = self.store.snapshot()["records"]["review_assignment"]["reassessed-" + reviewer]
            self.assertEqual(assignment["context"], {"prior_assignment_id": "result-" + reviewer})
        records = self.store.snapshot()["records"]
        return decisions.collect_problem_post_objections(records, self.artifacts, [records["value_review"][identifier]
            for identifier in ("review-bar-first", "review-bar-second", "review-reassessed-first", "review-reassessed-second")])

    def test_a_superseded_finding_on_the_same_evidence_is_history(self):
        """The wave review's I1 reproduction (R37): the earlier results kept O1 continuing in their own words."""
        objections = self.reassess_on_the_same_evidence(bar_reviewer="first")
        current = {objection["id"]: objection for objection in objections}["unsupported-implication"]
        self.assertEqual(current["review_id"], "review-bar-first")
        self.assertEqual([row["review_id"] for row in current["history"]], ["review-result-first", "review-result-second"])
        self.assert_delivered()

    def test_a_continuing_finding_of_the_latest_result_still_blocks(self):
        objections = self.reassess_on_the_same_evidence(bar_reviewer="first", first_status="continuing")
        current = {objection["id"]: objection for objection in objections}["unsupported-implication"]
        self.assertEqual(current["review_id"], "review-reassessed-first")
        self.assert_blocked_delivery()

    def test_an_objection_of_a_superseded_result_on_the_same_evidence_is_history(self):
        """The stated variant: the reviewer withdraws its own objection when it reassesses on the same evidence."""
        objections = self.reassess_on_the_same_evidence(result_reviewer="first")
        self.assertEqual([objection["id"] for objection in objections], ["o2"])
        self.assert_delivered()

    def prepare_version_one_bar_objection(self):
        """The re-review's reproduction: both bars recorded on the version 1 template, the first stating O1."""
        original = protocol.canonical_prompt

        def previous_prompt(role, **options):
            if options.get("orchestration") == "problem_creation":
                options["version"] = 1
            return original(role, **options)
        self.prepare_problem_evidence()
        with patch.object(protocol, "canonical_prompt", side_effect=previous_prompt):
            for reviewer in ("first", "second"):
                output = self.build_review_output("bar")
                if reviewer == "first":
                    output["objections"] = [self.make_objection()]
                self.observe("bar", reviewer, output)

    def refresh_version_one_bars(self, findings):
        """Refresh both bars on version 2, the first with these findings on O1, and cite them in the slate."""
        for reviewer in ("first", "second"):
            output = self.build_review_output("bar")
            if reviewer == "first":
                output["value"]["objection_findings"] = findings
            self.observe("bar", reviewer, output, identifier="bar-" + reviewer + "-v2")
        self.record_slate(bar_review_ids=["review-bar-first-v2", "review-bar-second-v2"])
        return protocol.build_packet(self.store.snapshot()["records"], self.artifacts, None, "result", "first")["material_objections"]

    def build_calibrated_result(self):
        output = self.build_review_output("result")
        output["value"]["bar_ids"] = ["review-bar-first-v2", "review-bar-second-v2"]
        return output

    def test_a_refreshed_bar_must_give_a_finding_on_its_earlier_objection(self):
        self.prepare_version_one_bar_objection()
        self.assert_error("review_objection_history_incomplete", lambda: self.observe(
            "bar", "first", self.build_review_output("bar"), identifier="bar-first-v2"))
        records = self.store.snapshot()["records"]
        self.assertEqual(records["review_assignment"]["bar-first-v2"]["context"], {"prior_assignment_id": "bar-first"})
        # Until the refreshed bar is recorded, the version 1 bar's objection stays current.
        [objection] = decisions.collect_problem_post_objections(records, self.artifacts, [records["value_review"][identifier]
            for identifier in ("review-bar-first", "review-bar-second")])
        self.assertEqual(objection["review_id"], "review-bar-first")

    def test_a_continuing_finding_of_a_refreshed_bar_keeps_its_objection_binding(self):
        self.prepare_version_one_bar_objection()
        [objection] = self.refresh_version_one_bars([self.make_finding("continuing")])
        self.assertEqual((objection["review_id"], objection["claim"]), ("review-bar-first-v2", self.make_objection()["claim"]))
        self.assert_error("review_objection_history_incomplete",
                          lambda: self.observe("result", "first", self.build_calibrated_result()))

    def test_a_bar_refresh_packet_delivers_the_earlier_bar_and_its_objection(self):
        """R40: the refreshed bar sees the objection it must address in its packet."""
        self.prepare_version_one_bar_objection()
        self.refresh_version_one_bars([self.make_finding("continuing")])
        records = self.store.snapshot()["records"]
        for reviewer in ("first", "second"):
            with self.subTest(reviewer=reviewer):
                packet = json.loads(self.artifacts.read(records["review_assignment"]["bar-" + reviewer + "-v2"]["packet"]))
                self.assertEqual(packet["historical_reviews"], [records["value_review"]["review-bar-" + reviewer]["payload"]])
                self.assertEqual(sorted(packet["problem_refresh"]), ["assignment_id", "previous_template_id", "template_id"])
        packet = json.loads(self.artifacts.read(records["review_assignment"]["bar-first-v2"]["packet"]))
        self.assertEqual(packet["historical_reviews"][0]["objections"], [self.make_objection()])
        self.assertEqual(records["value_review"]["review-bar-first-v2"]["payload"]["value"]["objection_findings"],
                         [self.make_finding("continuing")])

    def test_a_resolved_finding_of_a_refreshed_bar_clears_its_objection(self):
        self.prepare_version_one_bar_objection()
        self.assertEqual(self.refresh_version_one_bars([self.make_finding("resolved")]), [])
        for reviewer in ("first", "second"):
            self.observe("result", reviewer, self.build_calibrated_result())
        self.assert_delivered()

    def refresh_slate_review_output(self, status=None):
        """The slate reviewer stated O1 against candidate-1; candidate-2 makes it refresh its review, with a finding of
        this status on O1, or none."""
        self.prepare_reviews(slate_reviewer="first", results=False)
        self.record_candidate("candidate-2", statement="Does the bound hold for odd $n$?")
        output = self.build_slate_output(["candidate-1", "candidate-2"])
        output["value"]["objection_findings"] = [] if status is None else [self.make_finding(status)]
        return output

    def observe_refreshed_slate_review(self, output):
        review = self.observe("slate", "first", output, identifier="slate-first-refreshed")
        self.record_slate("slate-2", deferred=[{"candidate_id": "candidate-2", "evidence_needed": "A documented active line."}],
                          review_ids=[review["id"]])
        return protocol.build_packet(self.store.snapshot()["records"], self.artifacts, None, "result", "first")["material_objections"]

    def test_a_refreshed_slate_review_must_give_a_finding_on_its_earlier_objection(self):
        output = self.refresh_slate_review_output()
        self.assert_error("review_objection_history_incomplete",
                          lambda: self.observe("slate", "first", output, identifier="slate-first-refreshed"))
        packet = protocol.build_packet(self.store.snapshot()["records"], self.artifacts, None, "result", "first")
        self.assertEqual([(row["id"], row["review_id"]) for row in packet["material_objections"]],
                         [("unsupported-implication", "review-slate-first")])

    def test_a_continuing_finding_of_a_refreshed_slate_review_keeps_its_objection_binding(self):
        [objection] = self.observe_refreshed_slate_review(self.refresh_slate_review_output("continuing"))
        self.assertEqual((objection["review_id"], objection["claim"], objection["candidate_ids"]),
                         ("review-slate-first-refreshed", self.make_objection()["claim"], ["candidate-1"]))
        self.assert_error("review_objection_history_incomplete",
                          lambda: self.observe("result", "first", self.build_review_output("result")))

    def test_a_resolved_finding_of_a_refreshed_slate_review_clears_its_objection(self):
        self.assertEqual(self.observe_refreshed_slate_review(self.refresh_slate_review_output("resolved")), [])
        for reviewer in ("first", "second"):
            self.observe("result", reviewer, self.build_review_output("result"))
        self.assert_delivered()

    def prepare_version_evidence(self):
        """Astra's N2 reproduction: a later assessment supplies E2, a refreshed result states a new O1 version citing
        it, and a subsequent assessment stops citing E2 while O1 stays contested."""
        from test_research_problem_assessment import build_assessment
        self.prepare_reviews(result_reviewer="first")
        self.counterexample = self.artifacts.put(b"A counterexample that the stated implication misses.", "text/plain")
        assessment = build_assessment(self.selected, identifier="assessment-2")
        assessment["items"]["priority"]["evidence"].append(self.counterexample)
        self.mutate(self.load_problem_module().record_assessment, assessment)
        version = dict(self.make_objection(), reason="The supplied counterexample defeats the implication.",
                       evidence=[self.counterexample])
        contested = {"id": version["id"], "status": "contested", "reason": version["reason"], "evidence": version["evidence"]}
        for reviewer in ("first", "second"):
            output = self.build_review_output("result")
            output["value"]["objection_findings"] = [contested]
            if reviewer == "first":
                output["objections"] = [version]
            self.observe("result", reviewer, output, identifier="refreshed-" + reviewer)
        self.mutate(self.load_problem_module().record_assessment, build_assessment(self.selected, identifier="assessment-3"))
        for reviewer in ("first", "second"):
            output = self.build_review_output("result")
            output["value"]["objection_findings"] = [contested]
            self.observe("result", reviewer, output, identifier="reassessed-" + reviewer)
        records = self.store.snapshot()["records"]
        self.assertNotIn(self.counterexample["sha256"], json.dumps(records["problem_assessment"]["assessment-3"]))
        return records

    def test_the_adjudicator_receives_the_stating_review_and_evidence_of_the_version(self):
        records = self.prepare_version_evidence()
        original_pair = ["review-result-first", "review-result-second"]
        self.observe_adjudication(review_ids=original_pair)
        packet = json.loads(self.artifacts.read(self.store.snapshot()["records"]["review_assignment"]["focused"]["packet"]))
        stating = records["value_review"]["review-refreshed-first"]
        [current] = decisions.collect_problem_post_objections(records, self.artifacts, [records["value_review"][identifier]
            for identifier in ("review-reassessed-first", "review-reassessed-second")])
        self.assertEqual((current["review_id"], current["evidence"]), (stating["id"], [self.counterexample]))
        self.assertEqual(packet["objection"]["material_version"], current["material_version"])
        self.assertEqual((packet["version_review"]["id"], packet["version_review"]["objections"][0]["reason"]),
                         (stating["id"], stating["payload"]["objections"][0]["reason"]))
        self.assertEqual((packet["version_evidence"], packet["version_source_mappings"]), ([self.counterexample], []))
        delivered = {entry["artifact"]["sha256"]: entry["content"] for entry in packet["evidence"]}
        self.assertEqual(delivered[self.counterexample["sha256"]], "A counterexample that the stated implication misses.")
        self.assertNotIn(self.counterexample, packet["original_evidence"])
        self.assert_delivered()

    def test_an_adjudication_without_the_version_evidence_does_not_resolve_and_refreshes(self):
        self.prepare_version_evidence()
        original_pair = ["review-result-first", "review-result-second"]
        real = protocol.add_focused_adjudication

        def build_without_version(*arguments, **options):
            real(*arguments, **options)
            for key in ("version_review", "version_evidence", "version_source_mappings"):
                arguments[2].pop(key)
        with patch.object(protocol, "add_focused_adjudication", side_effect=build_without_version):
            self.observe_adjudication(review_ids=original_pair)
        packet = json.loads(self.artifacts.read(self.store.snapshot()["records"]["review_assignment"]["focused"]["packet"]))
        self.assertNotIn(self.counterexample["sha256"], {entry["artifact"]["sha256"] for entry in packet["evidence"]})
        self.assert_blocked_delivery()
        self.assert_error("review_adjudicator_fixed", lambda: self.assign("adjudicator", "focused-fourth",
            identifier="focused-other", review_ids=original_pair, objection_id="unsupported-implication"))
        self.observe_adjudication(review_ids=original_pair, identifier="focused-complete")
        refreshed = self.store.snapshot()["records"]["review_assignment"]["focused-complete"]
        refresh = json.loads(self.artifacts.read(refreshed["packet"]))["focused_refresh"]
        self.assertEqual((refreshed["history_assignment_id"], refresh["assignment_id"], refresh["reason"], refresh["undelivered_keys"]),
                         ("focused", "focused", "packet_incomplete", ["version_evidence", "version_review", "version_source_mappings"]))
        self.assertEqual(refresh["material_version"], refresh["previous_material_version"])
        self.assert_delivered()

    def replace_first_bar(self):
        """A legitimate context repair replaces the first bar by another reviewer's bar."""
        self.mutate(protocol.record_context_event, {"id": "exposure", "assignment_id": "bar-first",
            "kind": "contamination", "source": "author_history", "reason": "Author advocacy entered the bar context.",
            "evidence": [self.supporting]})
        def respond_to_probe(route, request, credential):
            packet = json.loads(request["input"][0]["content"])
            return self.build_provider_response({"allowed_control": packet["allowed_control"],
                "evidence_control": packet["evidence_control"], "excluded_controls": []})
        with patch("research_harness.review_transport.send_request", side_effect=respond_to_probe):
            self.mutate(protocol.probe_route, {"id": "repair-probe", "route_id": "route"}, credential="test-only")
        self.assign("bar", "replacement", identifier="bar-replaced", context_repair={
            "assignment_id": "bar-first", "event_ids": ["exposure"], "probe_id": "repair-probe",
            "reason": "Reassess the field bar in a verified independent context.", "evidence": [self.supporting]})
        output = self.build_review_output("bar")
        output["reassessment"] = {"assignment_id": "bar-first", "findings": [{"review_id": "review-bar-first",
            "disposition": "revised", "reason": "The independent context gives the same field standard.",
            "evidence": [self.supporting]}]}
        with patch("research_harness.review_transport.send_request", return_value=self.build_provider_response(output)):
            self.mutate(protocol.invoke_assignment, {"id": "attempt-bar-replaced", "assignment_id": "bar-replaced"},
                        credential="test-only")
        self.mutate(decisions.record_value_review, dict(output, id="review-bar-replaced", dossier_id=None,
                                                        assignment_id="bar-replaced"))

    def test_a_complete_adjudication_of_an_unchanged_version_is_not_sampled_again_after_a_bar_repair(self):
        """R38: other original calibrations of another pair do not make the earlier complete packet incomplete."""
        self.prepare_reviews(bar_reviewer="second", results=False)
        self.observe_adjudication(review_ids=["review-bar-first", "review-bar-second"])
        self.replace_first_bar()
        records = self.store.snapshot()["records"]
        [objection] = decisions.collect_problem_post_objections(records, self.artifacts, [records["value_review"][identifier]
            for identifier in ("review-bar-replaced", "review-bar-second")])
        previous = json.loads(self.artifacts.read(records["review_assignment"]["focused"]["packet"]))
        self.assertEqual(objection["material_version"], previous["objection"]["material_version"])
        self.assert_error("review_adjudicator_fixed", lambda: self.assign("adjudicator", "focused-third",
            identifier="focused-after-repair", review_ids=["review-bar-replaced", "review-bar-second"],
            objection_id="unsupported-implication"))

    def test_changed_evidence_refreshes_the_fixed_adjudicator_and_names_the_reason(self):
        self.prepare_reviews(result_reviewer="first")
        self.observe_adjudication("upheld")
        self.mutate(self.load_problem_module().record_value, self.build_value([self.item], identifier="value-copy"))
        self.observe_adjudication("upheld", identifier="refreshed-focused")
        records = self.store.snapshot()["records"]
        refresh = json.loads(self.artifacts.read(records["review_assignment"]["refreshed-focused"]["packet"]))["focused_refresh"]
        self.assertEqual((refresh["reason"], refresh["material_version"]), ("evidence_changed", refresh["previous_material_version"]))
        self.assertNotEqual(refresh["problem_evidence_digest"], refresh["previous_problem_evidence_digest"])
        self.assertNotIn("undelivered_keys", refresh)

    def test_a_material_adjudication_without_the_bar_calibrations_does_not_resolve(self):
        self.prepare_reviews(bar_reviewer="second")
        real = protocol.add_focused_adjudication

        def build_without_calibrations(*arguments, **options):
            real(*arguments, **options)
            arguments[2].pop("bar_calibrations")
        with patch.object(protocol, "add_focused_adjudication", side_effect=build_without_calibrations):
            self.observe_adjudication(review_ids=["review-bar-first", "review-bar-second"])
        self.assert_blocked_delivery()

    def test_a_later_slate_objection_permits_the_result_reviewers_to_reassess(self):
        self.prepare_reviews()
        for reviewer in ("first", "second"):
            with self.subTest(reviewer=reviewer):
                self.assert_error("review_resampling_forbidden",
                                  lambda: self.assign("result", reviewer, identifier="unchanged-" + reviewer))
        output = self.build_slate_output(["candidate-1"])
        output["objections"] = [dict(self.make_objection(), candidate_ids=["candidate-1"])]
        self.observe("slate", "second", output)
        self.assertIn("research_objection_unresolved",
                      {row["code"] for row in decisions.assess_problem_reviews(self.store.snapshot()["records"], self.artifacts)["obligations"]})
        for reviewer in ("first", "second"):
            output = self.build_review_output("result")
            output["value"]["objection_findings"] = [self.make_finding("resolved")]
            review = self.observe("result", reviewer, output, identifier="reassessed-" + reviewer)
            records = self.store.snapshot()["records"]
            assignment = records["review_assignment"]["reassessed-" + reviewer]
            packet = json.loads(self.artifacts.read(assignment["packet"]))
            self.assertEqual((assignment["context"], [objection["id"] for objection in packet["material_objections"]]),
                             ({"prior_assignment_id": "result-" + reviewer}, ["unsupported-implication"]))
            self.assertEqual(review["id"], "review-reassessed-" + reviewer)
            self.assert_error("review_resampling_forbidden",
                              lambda: self.assign("result", reviewer, identifier="again-" + reviewer))
        self.assert_delivered()

    def test_focused_adjudication_can_resolve_a_bar_objection(self):
        self.prepare_reviews(bar_reviewer="second")
        self.observe_adjudication(review_ids=["review-bar-first", "review-bar-second"])
        self.assert_delivered()

    def test_focused_adjudication_can_resolve_a_contested_result_history(self):
        self.prepare_reviews(result_reviewer="second")
        self.refresh_reviews("contested")
        self.observe_adjudication(review_ids=["review-result-first", "review-result-second"])
        self.assert_delivered()

    def test_original_assessor_cannot_adjudicate_the_dispute(self):
        self.prepare_reviews(result_reviewer="first")
        self.assert_error("review_not_independent", lambda: self.observe_adjudication(reviewer="first"))

    def test_a_focused_adjudication_requires_two_independent_assessors(self):
        self.prepare_reviews(result_reviewer="first")
        self.assert_error("research_review_pair_required", lambda: self.observe_adjudication(
            review_ids=["review-bar-first", "review-result-first"]))
        self.assert_blocked_delivery()

    def test_a_focused_adjudication_requires_one_review_phase(self):
        self.prepare_reviews(result_reviewer="first")
        self.assert_error("research_review_phase_mismatch", lambda: self.observe_adjudication(
            review_ids=["review-bar-second", "review-result-first"]))
        self.assert_blocked_delivery()

    def test_new_value_evidence_cannot_replace_an_upheld_focused_adjudicator(self):
        self.prepare_reviews(result_reviewer="first")
        self.observe_adjudication("upheld")
        self.mutate(self.load_problem_module().record_value, self.build_value([self.item], identifier="value-copy"))
        self.assert_error("review_adjudicator_fixed", lambda: self.observe_adjudication(
            reviewer="different-fourth", identifier="replacement-focused"))
        self.assert_blocked_delivery("problem_evidence_unassessed")

    def test_new_value_evidence_retains_the_observed_focused_session(self):
        self.prepare_reviews(result_reviewer="first")
        original = self.observe_adjudication("upheld")
        self.refresh_reviews("continuing")
        original_pair = ["review-result-first", "review-result-second"]
        refreshed = self.observe_adjudication(identifier="refreshed-focused", review_ids=original_pair)
        records = self.store.snapshot()["records"]
        previous = records["review_assignment"][original["assignment_id"]]
        assignment = records["review_assignment"][refreshed["assignment_id"]]
        self.assertEqual(assignment["history_assignment_id"], previous["id"])
        self.assertEqual(assignment["session_id"], previous["session_id"])
        attempt = records["review_attempt"]["focused-attempt"]
        request = json.loads(self.artifacts.read(attempt["request"]))
        self.assertEqual(assignment["history"][:-1], request["input"])
        self.assertEqual(json.loads(assignment["history"][-1]["content"])["disposition"], "upheld")
        self.assertNotEqual(refreshed["problem_evidence_digest"], original["problem_evidence_digest"])
        self.assert_error("review_adjudicator_fixed", lambda: self.observe_adjudication(
            identifier="resampled-focused", review_ids=original_pair))
        self.assert_delivered()

    def test_result_context_repair_retains_prior_peer_material_objections(self):
        packet = self.prepare_repaired_result(changed=True)
        self.assertEqual(packet["problem_evidence"]["value"]["id"], "value-copy")
        [objection] = packet["material_objections"]
        self.assertEqual({key: objection[key] for key in self.make_objection()}, self.make_objection())
        self.assertEqual((objection["review_id"], objection["history"]), ("review-result-second", []))

    def test_result_context_repair_withholds_the_current_peer_assessment(self):
        packet = self.prepare_repaired_result(changed=False)
        self.assertEqual(packet["material_objections"], [])
        self.assertNotIn("unsupported-implication", json.dumps(packet))

    def test_focused_adjudication_can_use_the_observed_original_pair_after_repair(self):
        self.prepare_contested_repair()
        self.observe_adjudication(review_ids=["review-result-first", "review-result-second"])
        self.assert_delivered()

    def test_focused_adjudication_can_use_the_current_repaired_pair_history(self):
        self.prepare_contested_repair()
        self.observe_adjudication()
        self.assert_delivered()

    def test_a_stale_adjudication_cannot_resolve_new_evidence(self):
        self.prepare_reviews(result_reviewer="first")
        self.observe_adjudication()
        self.refresh_reviews("continuing")
        self.assert_blocked_delivery()

    def test_an_unsupported_resolution_cannot_be_recorded(self):
        self.prepare_reviews(result_reviewer="first")
        changed = self.build_value([self.item], identifier="value-2")
        self.mutate(self.load_problem_module().record_value, changed)
        output = self.build_review_output("result")
        finding = self.make_finding("resolved")
        finding["evidence"] = []
        output["value"]["objection_findings"] = [finding]
        payload = self.observe("result", "first", output, record=False, identifier="unsupported-refresh")
        self.assert_error("invalid_strategy", lambda: self.mutate(decisions.record_value_review, payload))
        self.assert_blocked_delivery("problem_evidence_unassessed")

    def test_a_recorded_resolution_requires_its_evidence_to_remain_available(self):
        self.prepare_reviews(result_reviewer="first")
        self.refresh_reviews("resolved")
        (self.root / self.supporting["path"]).unlink()
        self.assert_blocked_delivery("artifact_missing")

    def test_an_adjudication_without_evidence_cannot_clear_the_objection(self):
        self.prepare_reviews(result_reviewer="first")
        self.assign("adjudicator", "focused-third", identifier="focused",
            review_ids=["review-result-first", "review-result-second"], objection_id="unsupported-implication")
        output = {"objection_id": "unsupported-implication", "disposition": "not_upheld",
            "reason": "The objection is said to be resolved without supporting evidence.",
            "evidence": [], "correction_admissible": None}
        with patch("research_harness.review_transport.send_request", return_value=self.build_provider_response(output)):
            self.mutate(protocol.invoke_assignment, {"id": "focused-attempt", "assignment_id": "focused"}, credential="test-only")
        self.assert_error("invalid_strategy", lambda: self.mutate(protocol.record_adjudication,
            dict(output, id="unsupported-adjudication", assignment_id="focused")))
        self.assert_blocked_delivery()
