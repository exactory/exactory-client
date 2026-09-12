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

    def test_a_decision_needs_a_pinned_bundle(self):
        unpinned = self.decision_payload({"digest": "0" * 64})
        self.assert_error("publication_bundle_missing", lambda: self.mutate(rounds.record_round, unpinned))

    def test_the_next_round_is_well_formed(self):
        bundle = self.pin()
        for edit in (lambda p: p["next"].update(number=3),
                     lambda p: p["next"]["goal"].update(statement="Another statement than the pursued candidate's."),
                     lambda p: p["next"].update(objective_lineage={"previous_id": self.objective["id"], "containment": "Unchanged."}),
                     lambda p: p["next"]["goal"].update(field_change={"corpus": "arxiv", "primaryCategory": "math.CO"})):
            payload = self.decision_payload(bundle)
            edit(payload)
            self.assert_error("invalid_round", lambda: self.mutate(rounds.record_round, payload))
        wider = dict(self.objective, id="objective-wide", statement=self.objective["statement"] + " The bound also holds at n = 4.")
        locked = self.decision_payload(bundle, objective=wider, lineage={"previous_id": "absent", "containment": "The wider range contains [0, 3]."})
        self.assert_error("objective_locked", lambda: self.mutate(rounds.record_round, locked))

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
        stop = self.decision_payload(bundle, decision="stop", carried=carried)
        self.assert_error("invalid_round", lambda: self.mutate(rounds.record_round, stop))
        decision = self.mutate(rounds.record_round, self.decision_payload(bundle, carried=carried))["result"]
        self.assertEqual(decision["payload"]["carried"], carried)

    def test_a_late_reassessment_of_an_earlier_cycle_is_carried_by_the_closing_round(self):
        first = self.pin()
        decision = self.mutate(rounds.record_round, self.decision_payload(first))["result"]
        self.write_round(decision, "round-2")
        self.recandidate("late")
        second = self.pin()
        self.write_round_assessment("round-2", True, second["digest"])
        statement = "Extend the finite bound to every integer in [0, 7]."
        omitted = self.decision_payload(second, closes=2, statement=statement)
        self.assert_error("carried_development_missing", lambda: self.mutate(rounds.record_round, omitted))
        carried = [{"assessment_id": "assessment-late", "kind": "alternative", "question": "Does the bound extend beyond n = 3?",
                    "disposition": "deferred", "reason": "The wider range comes first."}]
        recorded = self.mutate(rounds.record_round, self.decision_payload(second, closes=2, statement=statement, carried=carried))["result"]
        self.assertEqual(recorded["payload"]["carried"], carried)

    def test_an_admitted_round_is_assessed_before_the_next_decision(self):
        bundle = self.pin()
        decision = self.mutate(rounds.record_round, self.decision_payload(bundle))["result"]
        self.write_round(decision, "round-2")
        second = self.decision_payload(bundle, closes=2, statement="Extend the finite bound to every integer in [0, 7].")
        self.assert_error("round_assessment_missing", lambda: self.mutate(rounds.record_round, second))

    def test_an_admitted_round_is_assessed_on_this_bundle_before_the_next_decision(self):
        bundle = self.pin()
        decision = self.mutate(rounds.record_round, self.decision_payload(bundle))["result"]
        self.write_round(decision, "round-2", successful=True, bundle_digest="0" * 64)
        second = self.decision_payload(bundle, closes=2, statement="Extend the finite bound to every integer in [0, 7].")
        self.assert_error("round_assessment_missing", lambda: self.mutate(rounds.record_round, second))

    def test_a_rejected_candidate_cannot_become_a_goal(self):
        bundle = self.pin()
        first = self.mutate(rounds.record_round, self.decision_payload(bundle))["result"]
        self.mutate(rounds.record_round_review, self.review_payload(first, verdict="not_approved", assessor="first-assessor"))
        repeated = self.decision_payload(bundle, statement="Transfer the bound to real inputs.")
        repeated["candidates"][0]["direction"] = repeated["next"]["goal"]["direction"] = "horizontal"
        self.assert_error("round_goal_repeated", lambda: self.mutate(rounds.record_round, repeated))

    def test_a_deferred_candidate_may_become_a_later_goal(self):
        bundle = self.pin()
        first = self.decision_payload(bundle)
        first["candidates"][1]["disposition"] = "deferred"
        first["candidates"][1]["reason"] = "Demand is not evidenced yet."
        decision = self.mutate(rounds.record_round, first)["result"]
        self.write_round(decision, "round-2", successful=True, bundle_digest=bundle["digest"])
        pursued = {"id": "cand-transfer", "direction": "horizontal", "statement": "Transfer the bound to real inputs.",
                   "disposition": "pursue", "reason": "Deferred earlier; demand is now evidenced.", "evidence": self.round_evidence()}
        later = self.decision_payload(bundle, closes=2, direction="horizontal", statement="Transfer the bound to real inputs.",
                                      candidates=[pursued])
        recorded = self.mutate(rounds.record_round, later)["result"]
        self.assertEqual(recorded["payload"]["next"]["goal"]["statement"], "Transfer the bound to real inputs.")

    def test_a_repeated_round_goal_reopens_that_round_with_changed_evidence(self):
        bundle = self.pin()
        first = self.mutate(rounds.record_round, self.decision_payload(bundle))["result"]
        self.write_round(first, "round-2", successful=False, bundle_digest=bundle["digest"])
        second = self.mutate(rounds.record_round, self.decision_payload(
            bundle, closes=2, statement="Extend the finite bound to every integer in [0, 7]."))["result"]
        self.write_round(second, "round-3", successful=False, bundle_digest=bundle["digest"])
        changed = [{"kind": "review", "review_id": bundle["id"] + "-gate-1"}]
        repeated = self.decision_payload(bundle, closes=3)
        self.assert_error("round_goal_repeated", lambda: self.mutate(rounds.record_round, repeated))
        other = self.decision_payload(bundle, closes=3, reopening={"round_id": "round-3", "reason": "The picture changed.", "evidence": changed})
        self.assert_error("round_goal_repeated", lambda: self.mutate(rounds.record_round, other))
        unchanged = self.decision_payload(bundle, closes=3, reopening={"round_id": "round-2", "reason": "The picture changed.",
                                                                       "evidence": self.round_evidence()})
        self.assert_error("round_reopening_unchanged", lambda: self.mutate(rounds.record_round, unchanged))
        reopened = self.decision_payload(bundle, closes=3, reopening={"round_id": "round-2", "reason": "The picture changed.", "evidence": changed})
        recorded = self.mutate(rounds.record_round, reopened)["result"]
        self.assertEqual(recorded["payload"]["next"]["reopening"]["round_id"], "round-2")

    def test_a_successful_round_is_not_reopened(self):
        bundle = self.pin()
        first = self.mutate(rounds.record_round, self.decision_payload(bundle))["result"]
        self.write_round(first, "round-2", successful=True, bundle_digest=bundle["digest"])
        changed = [{"kind": "review", "review_id": bundle["id"] + "-gate-1"}]
        repeated = self.decision_payload(bundle, closes=2, reopening={"round_id": "round-2", "reason": "The picture changed.", "evidence": changed})
        self.assert_error("invalid_round", lambda: self.mutate(rounds.record_round, repeated))

    def test_an_exhausted_direction_reopens_one_of_its_two_unsuccessful_rounds(self):
        bundle = self.pin()
        decision = self.mutate(rounds.record_round, self.decision_payload(bundle))["result"]
        self.write_round(decision, "round-2", successful=False, bundle_digest=bundle["digest"])
        for closes, direction, statement in ((2, "vertical", "Extend the finite bound to every integer in [0, 7]."),
                                             (3, "horizontal", "Apply the bound to a neighbouring category.")):
            decision = self.mutate(rounds.record_round, self.decision_payload(bundle, closes=closes, direction=direction, statement=statement))["result"]
            self.write_round(decision, "round-" + str(closes + 1), successful=False, bundle_digest=bundle["digest"])
        statement = "Extend the finite bound to every integer in [0, 11]."
        changed = [{"kind": "review", "review_id": bundle["id"] + "-gate-1"}]
        same = self.decision_payload(bundle, closes=4, statement=statement)
        self.assert_error("round_direction_exhausted", lambda: self.mutate(rounds.record_round, same))
        earlier = self.decision_payload(bundle, closes=4, statement=statement,
                                        reopening={"round_id": "round-2", "reason": "The picture changed.", "evidence": changed})
        self.assert_error("round_direction_exhausted", lambda: self.mutate(rounds.record_round, earlier))
        reopened = self.decision_payload(bundle, closes=4, statement=statement,
                                         reopening={"round_id": "round-3", "reason": "The picture changed.", "evidence": changed})
        self.assertEqual(self.mutate(rounds.record_round, reopened)["result"]["payload"]["next"]["reopening"]["round_id"], "round-3")

    def test_review_evidence_names_a_manuscript_review_of_this_bundle(self):
        first = self.pin()
        review_id = first["id"] + "-gate-1"
        payload = self.decision_payload(first)
        payload["candidates"][0]["evidence"].append({"kind": "review", "review_id": review_id})
        decision = self.mutate(rounds.record_round, payload)["result"]
        self.assertIn(review_id, [e["reference"].get("review_id") for e in decision["evidence"]])
        wrong = self.decision_payload(first)
        wrong["candidates"][0]["evidence"].append({"kind": "review", "review_id": "absent"})
        self.assert_error("round_evidence_mismatch", lambda: self.mutate(rounds.record_round, wrong))
        (self.root / "draft/abstract.txt").write_text("The exact finite bound was enumerated, revised.")
        second = self.pin()
        other = self.decision_payload(second)
        other["candidates"][0]["evidence"].append({"kind": "review", "review_id": review_id})
        self.assert_error("round_evidence_mismatch", lambda: self.mutate(rounds.record_round, other))


class RoundReviewTests(RoundsCase):
    def test_the_review_is_independent_complete_and_bound_to_the_decision(self):
        bundle = self.pin()
        decision = self.mutate(rounds.record_round, self.decision_payload(bundle))["result"]
        author = self.review_payload(decision, assessor="cycle-author")
        self.assert_error("review_not_independent", lambda: self.mutate(rounds.record_round_review, author))
        unknown = self.review_payload(decision)
        unknown["round_id"] = "absent"
        self.assert_error("unknown_round_decision", lambda: self.mutate(rounds.record_round_review, unknown))
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

    def test_a_review_binds_the_current_bundle(self):
        first = self.pin()
        decision = self.mutate(rounds.record_round, self.decision_payload(first))["result"]
        (self.root / "draft/abstract.txt").write_text("The exact finite bound was enumerated, revised.")
        self.pin()
        self.assert_error("round_review_stale", lambda: self.mutate(rounds.record_round_review, self.review_payload(decision)))

    def test_an_author_of_a_cycle_assessment_cannot_review_the_round(self):
        self.recandidate("second", alternative="not_useful", author="assessment-only-author")
        bundle = self.pin()
        decision = self.mutate(rounds.record_round, self.decision_payload(bundle))["result"]
        author = self.review_payload(decision, assessor="assessment-only-author")
        self.assert_error("review_not_independent", lambda: self.mutate(rounds.record_round_review, author))

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
        agreed = self.mutate(rounds.record_round_review, self.review_payload(first, assessor="second-assessor"))["result"]
        self.assertEqual((agreed["round_id"], agreed["verdict"]), (first["id"], "approved"))
        second = self.mutate(rounds.record_round, self.decision_payload(bundle, decision="stop"))["result"]
        self.assert_error("round_decision_duplicate",
                          lambda: self.mutate(rounds.record_round_review, self.review_payload(second, assessor="other-assessor")))
