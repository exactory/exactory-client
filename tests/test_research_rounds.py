"""Development rounds: decisions, reviews, admissions, assessments and the round gate."""

from research_harness import rounds
from rounds_fixtures import RoundsCase


class RoundDecisionTests(RoundsCase):
    def test_a_decision_binds_the_exact_current_bundle_and_the_current_round(self):
        bundle = self.pin()
        stale = self.decision_payload(bundle)
        stale["bundle_digest"] = "0" * 64
        self.assert_error("round_bundle_mismatch", lambda: self.mutate(rounds.record_round, stale))
        wrong = self.decision_payload(bundle, closes=2)
        self.assert_error("round_number_mismatch", lambda: self.mutate(rounds.record_round, wrong))
        decision = self.mutate(rounds.record_round, self.decision_payload(bundle))["result"]
        self.assertEqual((decision["closes"], decision["decision"], decision["bundle_digest"]), (1, "continue", bundle["digest"]))
        self.assertEqual(rounds.current_number(self.store.snapshot()["records"]), 1)

    def test_continue_pursues_exactly_one_candidate_and_stop_pursues_none(self):
        bundle = self.pin()
        none = self.decision_payload(bundle)
        none["candidates"][0]["disposition"] = "rejected"
        self.assert_error("invalid_round", lambda: self.mutate(rounds.record_round, none))
        two = self.decision_payload(bundle)
        two["candidates"][1]["disposition"] = "pursue"
        self.assert_error("invalid_round", lambda: self.mutate(rounds.record_round, two))
        stop = self.decision_payload(bundle, decision="stop")
        stop["candidates"][0]["disposition"] = "pursue"
        self.assert_error("invalid_round", lambda: self.mutate(rounds.record_round, stop))
        recorded = self.mutate(rounds.record_round, self.decision_payload(bundle, decision="stop"))["result"]
        self.assertEqual(recorded["decision"], "stop")
        self.assertIsNone(recorded["payload"]["next"])

    def test_a_goal_needs_claim_or_scope_criteria_stop_conditions_and_continuity(self):
        bundle = self.pin()
        for change in ({"success_criteria": []}, {"stop_conditions": []},
                       {"success_criteria": [{"id": "m", "kind": "measurement", "statement": "Percentile median improves by 10."}]}):
            payload = self.decision_payload(bundle)
            payload["next"]["goal"].update(change)
            self.assert_error("invalid_round", lambda: self.mutate(rounds.record_round, payload))
        payload = self.decision_payload(bundle)
        del payload["next"]["goal"]["continuity"]
        self.assert_error("invalid_round", lambda: self.mutate(rounds.record_round, payload))

    def test_a_carried_development_must_be_disposed(self):
        self.recandidate("carry")
        bundle = self.pin()
        omitted = self.decision_payload(bundle)
        self.assert_error("carried_development_missing", lambda: self.mutate(rounds.record_round, omitted))
        carried = [{"assessment_id": "assessment-carry", "kind": "alternative", "question": "Does the bound extend beyond n = 3?",
                    "disposition": "pursue", "reason": "It is the round's goal."}]
        decision = self.mutate(rounds.record_round, self.decision_payload(bundle, carried=carried))["result"]
        self.assertEqual(decision["payload"]["carried"], carried)

    def test_a_repeated_goal_or_rejected_candidate_needs_reopening_with_changed_evidence(self):
        bundle = self.pin()
        first = self.mutate(rounds.record_round, self.decision_payload(bundle))["result"]
        self.mutate(rounds.record_round_review, self.review_payload(first, verdict="not_approved", assessor="first-assessor"))
        repeated = self.decision_payload(bundle, statement="Transfer the bound to real inputs.")
        repeated["candidates"][0]["direction"] = repeated["next"]["goal"]["direction"] = "horizontal"
        self.assert_error("round_goal_repeated", lambda: self.mutate(rounds.record_round, repeated))

    def test_review_evidence_names_a_manuscript_review_of_this_bundle(self):
        bundle = self.pin()
        review_id = bundle["id"] + "-gate-1"
        payload = self.decision_payload(bundle)
        payload["candidates"][0]["evidence"].append({"kind": "review", "review_id": review_id})
        decision = self.mutate(rounds.record_round, payload)["result"]
        self.assertIn(review_id, [e["reference"].get("review_id") for e in decision["evidence"]])
        wrong = self.decision_payload(bundle)
        wrong["candidates"][0]["evidence"].append({"kind": "review", "review_id": "absent"})
        self.assert_error("round_evidence_mismatch", lambda: self.mutate(rounds.record_round, wrong))


class RoundReviewTests(RoundsCase):
    def test_the_review_is_independent_complete_and_bound_to_the_decision(self):
        bundle = self.pin()
        decision = self.mutate(rounds.record_round, self.decision_payload(bundle))["result"]
        author = self.review_payload(decision, assessor="cycle-author")
        self.assert_error("review_not_independent", lambda: self.mutate(rounds.record_round_review, author))
        short = self.review_payload(decision)
        short["checks"] = short["checks"][:-1]
        self.assert_error("invalid_round", lambda: self.mutate(rounds.record_round_review, short))
        doubled = self.review_payload(decision)
        doubled["checks"].append(dict(doubled["checks"][0]))
        self.assert_error("invalid_round", lambda: self.mutate(rounds.record_round_review, doubled))
        stale = self.review_payload(decision)
        stale["round_digest"] = "0" * 64
        self.assert_error("round_review_stale", lambda: self.mutate(rounds.record_round_review, stale))
        review = self.mutate(rounds.record_round_review, self.review_payload(decision, verdict="not_approved"))["result"]
        self.assertEqual(review["verdict"], "not_approved")
        again = self.review_payload(decision, verdict="approved")
        self.assert_error("round_review_duplicate", lambda: self.mutate(rounds.record_round_review, again))

    def test_a_stop_review_has_its_own_checks(self):
        bundle = self.pin()
        decision = self.mutate(rounds.record_round, self.decision_payload(bundle, decision="stop"))["result"]
        wrong = self.review_payload(decision)
        wrong["checks"] = [dict(wrong["checks"][0], kind="impact"), dict(wrong["checks"][1], kind="demand")]
        self.assert_error("invalid_round", lambda: self.mutate(rounds.record_round_review, wrong))
        review = self.mutate(rounds.record_round_review, self.review_payload(decision))["result"]
        self.assertEqual({c["kind"] for c in review["payload"]["checks"]}, set(rounds.CHECKS_STOP))

    def test_one_approved_decision_per_closing_round(self):
        bundle = self.pin()
        first = self.mutate(rounds.record_round, self.decision_payload(bundle))["result"]
        self.mutate(rounds.record_round_review, self.review_payload(first))
        second = self.mutate(rounds.record_round, self.decision_payload(bundle, decision="stop"))["result"]
        self.assert_error("round_decision_duplicate",
                          lambda: self.mutate(rounds.record_round_review, self.review_payload(second, assessor="other-assessor")))
