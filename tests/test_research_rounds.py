"""Development rounds: decisions, reviews, admissions, assessments and the round gate."""

from research_harness import resources, rounds
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

    def test_an_approved_decision_on_a_superseded_bundle_does_not_block_the_round(self):
        first = self.pin()
        stopped = self.mutate(rounds.record_round, self.decision_payload(first, decision="stop"))["result"]
        self.mutate(rounds.record_round_review, self.review_payload(stopped))
        (self.root / "draft/abstract.txt").write_text("The exact finite bound was enumerated, revised after the plateau.")
        revised = self.pin()
        again = self.mutate(rounds.record_round, self.decision_payload(revised, decision="stop"))["result"]
        approved = self.mutate(rounds.record_round_review, self.review_payload(again, assessor="second-assessor"))["result"]
        self.assertEqual((approved["round_id"], approved["verdict"]), (again["id"], "approved"))


class RoundAdmissionTests(RoundsCase):
    def test_admission_needs_an_approved_continue_decision_and_records_the_opening_state(self):
        bundle = self.pin()
        decision = self.mutate(rounds.record_round, self.decision_payload(bundle))["result"]
        early = {"id": "round-2", "round_id": decision["id"], "review_id": "absent", "reason": "Too early."}
        self.assert_error("unknown_round_review", lambda: self.mutate(rounds.admit_round, early))
        review = self.mutate(rounds.record_round_review, self.review_payload(decision, verdict="not_approved"))["result"]
        pending = dict(early, review_id=review["id"])
        self.assert_error("round_review_required", lambda: self.mutate(rounds.admit_round, pending))
        approved = self.mutate(rounds.record_round_review, self.review_payload(decision, assessor="second-assessor"))["result"]
        before = self.store.snapshot()["records"]
        admission = self.mutate(rounds.admit_round, dict(early, review_id=approved["id"]))["result"]
        self.assertEqual((admission["number"], admission["objective"]), (2, self.objective))
        opening = admission["opening"]
        self.assertEqual((opening["bundle_id"], opening["claim_ids"], opening["cycle_ids"]), (bundle["id"], ["bound"], ["cycle-1"]))
        self.assertEqual(set(opening["search_selection"]), set(rounds.OPENING_PURPOSES))
        self.assertEqual(opening["search_selection"]["downstream"], None)
        self.assertEqual(opening["search_selection"]["direct"], "direct")
        self.assertEqual(opening["requirement_ids"], sorted(before.get("fulltext_requirement", {})))
        self.assertEqual(opening["reading_count"], len(before["reading"]))
        self.assertEqual(opening["accounts"], resources.account_report(before, "research"))
        self.assertEqual(opening["accounts"]["literature"]["network_requests"]["charged"], 7)
        records = self.store.snapshot()["records"]
        self.assertEqual(rounds.current_number(records), 2)
        self.assertEqual(rounds.active_round(records)["id"], "round-2")
        again = {"id": "round-2b", "round_id": decision["id"], "review_id": approved["id"], "reason": "Twice."}
        self.assert_error("round_active", lambda: self.mutate(rounds.admit_round, again))

    def test_admission_needs_a_review_of_this_decision(self):
        bundle = self.pin()
        first, approving = self.approve(self.decision_payload(bundle))
        other = self.mutate(rounds.record_round, self.decision_payload(bundle, statement="Extend the finite bound to every integer in [0, 7]."))["result"]
        absent = {"id": "round-2", "round_id": "absent", "review_id": approving["id"], "reason": "No such decision."}
        self.assert_error("unknown_round_decision", lambda: self.mutate(rounds.admit_round, absent))
        borrowed = dict(absent, round_id=other["id"], reason="Borrowed approval.")
        self.assert_error("round_review_required", lambda: self.mutate(rounds.admit_round, borrowed))
        admitted = self.mutate(rounds.admit_round, dict(absent, round_id=first["id"], reason="The approved decision."))["result"]
        self.assertEqual(admitted["decision_id"], first["id"])

    def test_admission_widens_the_objective_and_charges_the_development_budget(self):
        from research_harness.resources import set_budget
        limits = {unit: None for unit in ("network_requests", "source_bytes", "readings", "screenings",
                                          "model_input_tokens", "model_output_tokens", "wall_seconds", "rounds")}
        self.mutate(set_budget, {"profile": "research", "purpose": "development", "limits": dict(limits, rounds=1),
                                 "reason": "One development round at most."})
        wider = {"kind": "objective", "id": "wider-square-bound",
                 "statement": "For every integer n in [0, 5], n squared is at most 25, with equality at n = 5."}
        lineage = {"previous_id": self.objective["id"], "containment": "The range [0, 3] is contained in [0, 5]."}
        decision, review, admission = self.open_round(objective=wider, lineage=lineage)
        records = self.store.snapshot()["records"]
        self.assertEqual(records["configuration"]["research"]["target"], wider)
        self.assertEqual(records["objective_lineage"][wider["id"]]["round_id"], admission["id"])
        self.assertEqual(records["resource_account"]["research:development"]["charged"]["rounds"], 1)
        self.assertEqual(records["research_objective"][self.objective["id"]], self.objective)

    def test_an_exhausted_development_budget_refuses_the_decision(self):
        from research_harness.resources import set_budget
        limits = {unit: None for unit in ("network_requests", "source_bytes", "readings", "screenings",
                                          "model_input_tokens", "model_output_tokens", "wall_seconds", "rounds")}
        self.mutate(set_budget, {"profile": "research", "purpose": "development", "limits": dict(limits, rounds=0),
                                 "reason": "No development round."})
        bundle = self.pin()
        self.assert_error("resource_budget_exhausted", lambda: self.mutate(rounds.record_round, self.decision_payload(bundle)))

    def test_a_field_change_stays_in_the_corpus_and_needs_literature_room(self):
        bundle = self.pin()
        decision, review = self.approve(self.field_change_payload(bundle, "pubmed", "q-bio.QM"))
        payload = {"id": "round-2", "round_id": decision["id"], "review_id": review["id"], "reason": "Move fields."}
        self.assert_error("round_field_change_refused", lambda: self.mutate(rounds.admit_round, payload))
        bare = self.field_change_payload(bundle, "arxiv", "math.CO")
        bare["next"]["resource_limits"] = {"experiment": {"wall_seconds": 10}}
        self.assert_error("round_field_change_refused", lambda: self.mutate(rounds.record_round, bare))

    def test_a_field_change_adds_a_category_the_cohort_lacks(self):
        bundle = self.pin()
        collections = self.store.snapshot()["records"]["collection"].values()
        cohort_category = next(iter(collections))["definition"]["primaryCategory"]
        decision, review = self.approve(self.field_change_payload(bundle, "arxiv", cohort_category))
        payload = {"id": "round-2", "round_id": decision["id"], "review_id": review["id"], "reason": "Same category."}
        self.assert_error("round_field_change_refused", lambda: self.mutate(rounds.admit_round, payload))

    def test_a_field_change_to_a_new_category_is_admitted(self):
        decision, review = self.approve(self.field_change_payload(self.pin(), "arxiv", "math.CO"))
        payload = {"id": "round-2", "round_id": decision["id"], "review_id": review["id"], "reason": "Add the category."}
        admission = self.mutate(rounds.admit_round, payload)["result"]
        self.assertEqual(admission["goal"]["field_change"], {"corpus": "arxiv", "primaryCategory": "math.CO"})

    def test_a_stop_decision_opens_no_round(self):
        stop, review = self.approve(self.decision_payload(self.pin(), decision="stop"))
        payload = {"id": "round-2", "round_id": stop["id"], "review_id": review["id"], "reason": "A stop opens nothing."}
        self.assert_error("invalid_round", lambda: self.mutate(rounds.admit_round, payload))

    def test_admission_needs_the_decision_bundle_to_be_current(self):
        decision, review = self.approve(self.decision_payload(self.pin()))
        (self.root / "draft/abstract.txt").write_text("Rewritten after the approval.")
        self.pin()
        payload = {"id": "round-2", "round_id": decision["id"], "review_id": review["id"], "reason": "Stale."}
        self.assert_error("round_review_stale", lambda: self.mutate(rounds.admit_round, payload))

    def test_admission_needs_development_budget_room(self):
        from research_harness.resources import set_budget
        limits = {unit: None for unit in ("network_requests", "source_bytes", "readings", "screenings",
                                          "model_input_tokens", "model_output_tokens", "wall_seconds", "rounds")}
        self.mutate(set_budget, {"profile": "research", "purpose": "development", "limits": dict(limits, rounds=1),
                                 "reason": "One round."})
        decision, review = self.approve(self.decision_payload(self.pin()))
        self.mutate(set_budget, {"profile": "research", "purpose": "development", "limits": dict(limits, rounds=0),
                                 "reason": "No round after all."})
        payload = {"id": "round-2", "round_id": decision["id"], "review_id": review["id"], "reason": "Open."}
        self.assert_error("resource_budget_exhausted", lambda: self.mutate(rounds.admit_round, payload))

    def test_admission_records_the_revision_and_limits_and_closes_only_the_current_round(self):
        decision, review, admission = self.open_round()
        self.assertEqual(admission["admitted_revision"], self.store.revision)
        self.assertEqual(admission["resource_limits"], decision["payload"]["next"]["resource_limits"])
        self.write_round_assessment(admission["id"], True, decision["bundle_digest"])
        payload = {"id": "round-2b", "round_id": decision["id"], "review_id": review["id"], "reason": "Round 1 is closed."}
        self.assert_error("round_number_mismatch", lambda: self.mutate(rounds.admit_round, payload))


class RoundLiteratureTests(RoundsCase):
    def test_a_development_purpose_is_a_search_purpose(self):
        self.record_purpose("downstream", "downstream-early")
        self.assertEqual(self.store.snapshot()["records"]["search_selection"]["research:downstream"], {"search_id": "downstream-early"})

    def test_an_active_round_requires_fresh_consequence_searches_and_an_exemplar(self):
        self.record_purpose("downstream", "downstream-early")
        self.assertNotIn("round_search_missing", self.codes())
        self.assertNotIn("round_exemplar_missing", self.codes())
        self.open_round()
        missing = [o for o in self.store_obligations("research") if o["code"] == "round_search_missing"]
        self.assertEqual(sorted(o["purpose"] for o in missing), ["changes", "downstream", "exemplars", "next_step"])
        self.assertIn("round_exemplar_missing", self.codes())
        for purpose in ("downstream", "next_step", "exemplars"):
            self.record_purpose(purpose, purpose + "-round-2")
        missing = [o for o in self.store_obligations("research") if o["code"] == "round_search_missing"]
        self.assertEqual([o["purpose"] for o in missing], ["changes"])
        self.record_purpose("changes", "changes-round-2")
        self.assertNotIn("round_search_missing", self.codes())
        self.exemplar_requirement("round-2")
        self.assertNotIn("round_exemplar_missing", self.codes())

    def test_development_searches_enter_the_judgments_that_synthesis_depends_on(self):
        from research_harness.synthesis import synthesis_report
        self.open_round()
        before = synthesis_report(self.store, "research")["literature_digest"]
        self.record_purpose("downstream", "downstream-round-2")
        self.assertNotEqual(synthesis_report(self.store, "research")["literature_digest"], before)
