"""Canonical scientific actions cannot bypass evidence, authority or independence."""

import copy
from unittest.mock import patch

from strategy_fixtures import StrategyCase


class DecisionContractTests(StrategyCase):
    def test_gate_version_is_record_metadata_and_does_not_change_payload_fingerprint(self):
        from research_harness.evidence import digest
        self.reviewed_dossier()
        payload = self.decision()
        saved = self.mutate(self.module("research_decisions").record_research_decision, payload)["result"]
        self.assertEqual(saved["gate_version"], 2)
        self.assertNotIn("gate_version", saved["payload"])
        self.assertEqual(saved["digest"], digest(payload))
        self.assertEqual(saved["fingerprint"], digest({key: value for key, value in payload.items() if key not in ("id", "reason")}))

    def prepare_unread_direction(self):
        self.prepare_direction()
        fixture = self.direction_fixture
        decisive = fixture.metadata(90002)
        bundle = fixture.bundle(decisive, bundle_id="decisive-bundle")
        fixture.mutate(self.module("literature").import_bundle, bundle)
        fixture.mutate(self.module("literature").record_search, fixture.capture_search(found=[decisive]))
        self.mutate(self.module("strategy").record_intent, self.intent())
        self.mutate(self.module("strategy").record_strategy, self.dossier())
        self.route()
        for stage in ("bar", "slate"):
            for suffix in ("a", "b"):
                self.record_response(stage + "-" + suffix, "reviewer-" + suffix, self.review_response(stage))
        return fixture, bundle

    def test_decision_refused_until_decisive_reading_exists_before_reviews(self):
        self.prepare_unread_direction()
        error = self.error("direction_minimum_unmet", lambda: self.mutate(
            self.module("research_decisions").record_research_decision, self.decision()))
        self.assertEqual({item["code"] for item in error.details["obligations"]}, {"direction_reading_missing"})
        self.assertIn("before the reviews", error.message)
        self.assertNotIn("research_decision", self.store.snapshot()["records"])

    def test_reviews_predating_decisive_reading_cannot_authorize_new_decision(self):
        fixture, bundle = self.prepare_unread_direction()
        fixture.mutate(self.module("reading").record_reading, fixture.full_note(bundle, note_id="decisive-reading"))
        self.assertEqual(self.module("claim_minimums").assess_direction(self.store.snapshot()["records"], self.artifacts), [])
        error = self.error("direction_evidence_unassessed", lambda: self.mutate(
            self.module("research_decisions").record_research_decision, self.decision()))
        self.assertEqual({item["review_id"] for item in error.details["obligations"]}, {"slate-a", "slate-b"})

    def test_historical_decision_keeps_standing_but_new_target_needs_reviewed_evidence(self):
        self.prepare_unread_direction()
        # A decision recorded before gate version 2 could retain an unread gap.
        # Keep that historical standing, but require review of a new commitment.
        dossier = self.store.snapshot()["records"]["strategy_dossier"]["dossier-1"]
        intent = self.store.snapshot()["records"]["research_intent"]["intent-1"]
        from research_harness.evidence import digest
        payload = self.decision()
        self.put("research_decision", "decision-1", {"id": "decision-1", "payload": payload,
            "digest": digest(payload), "fingerprint": digest({key: value for key, value in payload.items() if key not in ("id", "reason")}),
            "dossier_digest": dossier["digest"], "intent_digest": intent["digest"], "recorded_revision": self.store.revision + 1})
        self.put("strategy_selection", "decision", {"id": "decision-1"})
        self.assertTrue(self.state()["ready"], self.state()["obligations"])
        decisions = self.module("research_decisions")
        target = {"kind": "objective", "id": "new-target", "statement": self.intent()["objective"]["statement"]}
        self.error("direction_minimum_unmet", lambda: decisions.prepare_objective_commitment(
            self.store.snapshot()["records"], self.artifacts, target, decision_id="decision-1",
            source_impact=payload["source_impact"], revision=self.store.revision + 1))
        fixture = self.direction_fixture
        bundle = self.store.snapshot()["records"]["source_bundle"]["decisive-bundle"]
        fixture.mutate(self.module("reading").record_reading, fixture.full_note(bundle, note_id="new-target-reading"))
        self.assertTrue(self.state()["ready"], self.state()["obligations"])
        error = self.error("direction_evidence_unassessed", lambda: decisions.prepare_objective_commitment(
            self.store.snapshot()["records"], self.artifacts, target, decision_id="decision-1",
            source_impact=payload["source_impact"], revision=self.store.revision + 1))
        self.assertEqual({item["review_id"] for item in error.details["obligations"]}, {"slate-a", "slate-b"})
        reading = self.store.snapshot()["records"]["reading"]["new-target-reading"]
        successor = self.dossier("reviewed-direction")
        successor.update(previous="dossier-1", material_change={"kind": "new_evidence",
            "reason": "The decisive reading is now available for scientific assessment.",
            "evidence": [{"kind": "record", "record_kind": "reading", "id": reading["id"], "digest": digest(reading)}],
            "prior_objections": []})
        self.mutate(self.module("strategy").record_strategy, successor)
        for suffix in ("a", "b"):
            self.record_response("fresh-" + suffix, "reviewer-" + suffix, self.review_response(), dossier_id=successor["id"])
        reviewed = dict(self.decision("fresh-decision"), dossier_id=successor["id"], review_ids=["fresh-a", "fresh-b"])
        self.mutate(decisions.record_research_decision, reviewed)
        changes, commitment = decisions.prepare_objective_commitment(self.store.snapshot()["records"], self.artifacts, target,
            decision_id=reviewed["id"], source_impact=reviewed["source_impact"], revision=self.store.revision + 1)
        self.assertTrue(changes)
        self.assertEqual(commitment["decision_id"], reviewed["id"])

    def test_packet_digest_alone_does_not_establish_claim_coverage(self):
        protocol = self.module("review_protocol")
        original = protocol.build_direction_evidence
        def omit_claim(*args, **kwargs):
            value = original(*args, **kwargs)
            value["claim"] = {"digest": value["claim"]["digest"]}
            return value
        with patch.object(protocol, "build_direction_evidence", side_effect=omit_claim):
            self.reviewed_dossier()
        error = self.error("direction_evidence_unassessed", lambda: self.mutate(
            self.module("research_decisions").record_research_decision, self.decision()))
        self.assertEqual({tuple(item["missing"]) for item in error.details["obligations"]}, {("claim",)})

    def test_decision_evidence_digest_freezes_the_minimum_and_claim_packets(self):
        self.reviewed_dossier()
        saved = self.mutate(self.module("research_decisions").record_research_decision, self.decision())["result"]
        report = self.module("claim_minimums").describe_direction(self.store.snapshot()["records"], self.artifacts)
        self.assertEqual(saved["direction_evidence_digest"], report["digest"])
        for identifier in ("slate-a", "slate-b"):
            import json
            assignment = self.store.snapshot()["records"]["review_assignment"]["assignment-" + identifier]
            packet = json.loads(self.artifacts.read(assignment["packet"]))
            self.assertEqual(packet["direction_evidence"]["binding"], report["evidence"])
            for supplied in packet["direction_evidence"]["readings"]:
                reading = self.store.snapshot()["records"]["reading"][supplied["id"]]
                self.assertEqual(supplied["reading"]["notes"], reading["notes"])
                self.assertTrue(supplied["reading"]["inspections"])
        self.assertTrue(self.state()["ready"], self.state()["obligations"])

    def test_new_relevant_hit_and_reading_cannot_activate_existing_approval_or_target(self):
        self.reviewed_dossier()
        decisions = self.module("research_decisions")
        saved = self.mutate(decisions.record_research_decision, self.decision())["result"]
        fixture = self.direction_fixture
        decisive = fixture.metadata(90002)
        bundle = fixture.bundle(decisive, bundle_id="later-decisive")
        fixture.mutate(self.module("literature").import_bundle, bundle)
        fixture.mutate(self.module("reading").record_reading, fixture.full_note(bundle, note_id="later-reading"))
        fixture.mutate(self.module("literature").record_search, fixture.capture_search(found=[decisive]))
        self.assertIn("direction_evidence_unassessed", self.codes(self.state()))
        target = {"kind": "objective", "id": "committed", "statement": self.intent()["objective"]["statement"]}
        impact = copy.deepcopy(self.decision()["source_impact"])
        dossier = self.store.snapshot()["records"]["strategy_dossier"]["dossier-1"]
        impact["sources"] = [{"source_id": identifier, "impact": "unrelated", "reason": "Test the direction gate independently of source-impact classification.",
                              "evidence": [self.evidence]} for identifier in self.module("strategy").source_delta(self.store.snapshot()["records"], dossier)]
        error = self.error("direction_evidence_unassessed", lambda: decisions.prepare_objective_commitment(
            self.store.snapshot()["records"], self.artifacts, target, decision_id=saved["id"],
            source_impact=impact, revision=self.store.revision + 1))
        self.assertIn("direction_evidence_unassessed", {item["code"] for item in error.details["obligations"]})
        self.assertEqual(saved, self.store.snapshot()["records"]["research_decision"][saved["id"]])

    def test_legacy_packet_reading_coverage_uses_verified_record_chronology(self):
        protocol = self.module("review_protocol")
        original = protocol.build_direction_evidence
        def omit_reading_ids(*args, **kwargs):
            value = original(*args, **kwargs)
            value["readings"] = []
            return value
        with patch.object(protocol, "build_direction_evidence", side_effect=omit_reading_ids):
            self.reviewed_dossier()
        self.mutate(self.module("research_decisions").record_research_decision, self.decision())
        self.assertTrue(self.state()["ready"], self.state()["obligations"])

    def test_explicit_mismatched_reading_cannot_use_legacy_inventory_fallback(self):
        protocol = self.module("review_protocol")
        original = protocol.build_direction_evidence
        def change_inspection(*args, **kwargs):
            value = original(*args, **kwargs)
            value["readings"][0]["reading"]["inspections"] = []
            return value
        with patch.object(protocol, "build_direction_evidence", side_effect=change_inspection):
            self.reviewed_dossier()
        error = self.error("direction_evidence_unassessed", lambda: self.mutate(
            self.module("research_decisions").record_research_decision, self.decision()))
        self.assertTrue(all(item["reading_ids"] for item in error.details["obligations"]))

    def test_observed_source_supplements_cover_new_decisive_reading(self):
        self.prepare_direction()
        fixture = self.direction_fixture
        decisive = fixture.metadata(90002)
        bundle = fixture.bundle(decisive, bundle_id="supplement-decisive")
        fixture.mutate(self.module("literature").import_bundle, bundle)
        fixture.mutate(self.module("literature").record_search, fixture.capture_search(found=[decisive]))
        self.mutate(self.module("strategy").record_intent, self.intent())
        self.mutate(self.module("strategy").record_strategy, self.dossier())
        self.route()
        protocol, decisions = self.module("review_protocol"), self.module("research_decisions")
        for suffix in ("a", "b"):
            self.record_response("bar-" + suffix, "reviewer-" + suffix, self.review_response("bar"))
        for suffix in ("a", "b"):
            self.mutate(protocol.record_assignment, {"id": "request-" + suffix, "route_id": "route", "dossier_id": "dossier-1",
                "role": "slate", "reviewer_id": "reviewer-" + suffix, "author_id": "author", "context": {}})
            output = self.review_response(status="unresolved")
            output["source_requests"] = [{"id": "read-decisive", "kind": "source", "version_id": decisive, "depth": "fulltext"}]
            with patch("research_harness.review_transport.send_request", return_value=self.provider_response(output)):
                self.mutate(protocol.invoke_assignment, {"id": "request-attempt-" + suffix, "assignment_id": "request-" + suffix}, credential="fixture-only")
        fixture.mutate(self.module("reading").record_reading, fixture.full_note(bundle, note_id="supplement-reading"))
        for suffix in ("a", "b"):
            identifier = "assignment-slate-" + suffix
            self.mutate(protocol.record_assignment, {"id": identifier, "route_id": "route", "dossier_id": "dossier-1", "role": "slate",
                "reviewer_id": "reviewer-" + suffix, "author_id": "author", "context": {"prior_assignment_id": "request-" + suffix}})
            output = self.review_response()
            with patch("research_harness.review_transport.send_request", return_value=self.provider_response(output)):
                self.mutate(protocol.invoke_assignment, {"id": "supplement-attempt-" + suffix, "assignment_id": identifier}, credential="fixture-only")
            self.mutate(decisions.record_value_review, dict(output, id="slate-" + suffix, dossier_id="dossier-1", assignment_id=identifier))
        self.mutate(decisions.record_research_decision, self.decision())
        self.assertTrue(self.state()["ready"], self.state()["obligations"])

    def test_historical_decision_keeps_obligations_without_direction_or_q1_upgrade(self):
        response = self.review_response()
        response["value"]["assurances"] = [item for item in response["value"]["assurances"] if item["kind"] != "q1_venue"]
        self.reviewed_dossier(response_a=response, response_b=response, previous_prompts=True)
        self.mutate(self.module("research_decisions").record_research_decision, self.decision())
        records = self.store.snapshot()["records"]
        records["research_decision"]["decision-1"].pop("gate_version", None)
        records.pop("search_selection")
        state = self.module("research_decisions").decision_state(records, self.artifacts, "cycle")
        self.assertEqual(state["obligations"], [])

    def test_new_decision_reports_q1_missing_from_each_previous_protocol_review(self):
        response = self.review_response()
        response["value"]["assurances"] = [item for item in response["value"]["assurances"] if item["kind"] != "q1_venue"]
        self.reviewed_dossier(response_a=response, response_b=response, previous_prompts=True)
        self.mutate(self.module("research_decisions").record_research_decision, self.decision())
        missing = [item for item in self.state()["obligations"] if item["code"] == "q1_venue_missing"]
        self.assertEqual({item["review_id"] for item in missing}, {"slate-a", "slate-b"})

    def test_current_assignment_cannot_record_a_review_without_q1(self):
        from research_harness.errors import ResearchError
        response = self.review_response()
        response["value"]["assurances"] = [item for item in response["value"]["assurances"] if item["kind"] != "q1_venue"]
        with self.assertRaises(ResearchError) as caught:
            self.reviewed_dossier(response_b=response)
        self.assertEqual(caught.exception.code, "invalid_value_review")

    def test_failed_q1_assurance_blocks_investigation_and_pivot(self):
        response = self.review_response()
        next(item for item in response["value"]["assurances"] if item["kind"] == "q1_venue")["status"] = "failed"
        self.reviewed_dossier(response_b=response)
        decisions = self.module("research_decisions")
        for action in ("investigate", "pivot"):
            with self.subTest(action=action):
                self.mutate(decisions.record_research_decision, self.decision("decision-" + action, action=action))
                self.assertEqual(self.codes(self.state()), {"research_assurance_pending"})
                pending = self.state()["obligations"][0]
                self.assertEqual((pending["review_id"], pending["check"]), ("slate-b", "q1_venue"))

    def state(self, boundary="cycle", **kwargs):
        return self.module("research_decisions").decision_state(self.store.snapshot()["records"], self.artifacts, boundary, **kwargs)

    def codes(self, report):
        return {item["code"] for item in report["obligations"]}

    def test_shared_research_profile_is_exempt_but_explicit_intent_requires_review(self):
        decisions = self.module("research_decisions")
        report = self.state()
        self.assertFalse(report["required"])
        self.assertTrue(report["ready"])
        self.mutate(self.module("strategy").record_intent, self.intent())
        report = self.state()
        self.assertTrue(report["required"])
        self.assertFalse(report["ready"])
        self.assertIn("strategy_dossier_missing", self.codes(report))

    def test_managed_legacy_requires_current_intent_without_fabricated_history(self):
        self.put("workspace", "study", {"stage": "ideate", "status": "pending"})
        report = self.state()
        self.assertIn("research_intent_missing", self.codes(report))
        self.assertEqual(report["legacy_status"], "legacy_unassessed")
        self.assertNotIn("research_intent", self.store.snapshot()["records"])

    def test_two_observed_reviews_authorize_only_the_bounded_investigation(self):
        self.reviewed_dossier()
        decisions = self.module("research_decisions")
        saved = self.mutate(decisions.record_research_decision, self.decision())["result"]
        self.assertTrue(self.state()["ready"], self.state()["obligations"])
        self.assertFalse(self.state("write")["ready"])
        self.assertEqual(self.state()["decision"]["id"], saved["id"])

    def test_favorable_value_with_an_unresolved_method_does_not_approve(self):
        response = self.review_response()
        response["value"]["method_adequacy"] = "unresolved"
        self.reviewed_dossier(response_b=response)
        self.mutate(self.module("research_decisions").record_research_decision, self.decision())
        self.assertFalse(self.state()["ready"])
        self.assertIn("research_method_unresolved", self.codes(self.state()))

    def test_easy_small_objective_is_checked_against_the_independent_bar(self):
        response = self.review_response()
        response["value"].update(objective_adequacy="inadequate", reason="Completing a single calibration cannot resolve the user's mechanism question.")
        self.reviewed_dossier(response_b=response)
        self.mutate(self.module("research_decisions").record_research_decision, self.decision())
        self.assertIn("research_objective_unresolved", self.codes(self.state()))
        self.assertEqual(len(self.store.snapshot()["records"]["value_review"]), 4)

    def test_equally_fitting_proxy_mechanisms_keep_the_transfer_gap(self):
        response = self.review_response(status="insufficient")
        response["value"].update(method_adequacy="inadequate", consequence="The fitted proxy leaves both rival mechanisms compatible with every observation.")
        self.reviewed_dossier(response_b=response)
        self.mutate(self.module("research_decisions").record_research_decision, self.decision())
        self.assertIn("research_consequence_insufficient", self.codes(self.state()))
        self.assertIn("research_method_unresolved", self.codes(self.state()))

    def test_proxy_target_commitment_requires_a_transfer_plan(self):
        dossier = self.dossier()
        dossier["candidates"][0]["transfer"].update(kind="unestablished", plan=None)
        self.reviewed_dossier(dossier_payload=dossier)
        self.mutate(self.module("research_decisions").record_research_decision, self.decision())
        self.assertIn("research_transfer_plan_missing", self.codes(self.state("target")))

    def test_a_bounded_transfer_probe_can_resolve_the_unknown_proxy_relation(self):
        dossier = self.dossier()
        dossier["candidates"][0]["transfer"].update(kind="unestablished", plan="Compare the proxy and target controls within this bounded tranche; retain both mechanisms if they coincide.")
        self.reviewed_dossier(dossier_payload=dossier)
        self.mutate(self.module("research_decisions").record_research_decision, self.decision())
        self.assertTrue(self.state("target")["ready"], self.state("target")["obligations"])

    def test_an_available_test_prevents_an_unsupported_resource_closure(self):
        response = self.review_response(status="insufficient")
        for check in response["value"]["assurances"]:
            if check["kind"] == "stop":
                check.update(status="failed", reason="The local discriminator remains feasible within the current resource contract.")
        self.reviewed_dossier(response_b=response)
        self.mutate(self.module("research_decisions").record_research_decision, self.decision(action="close_branch"))
        self.assertIn("research_assurance_pending", self.codes(self.state("round")))
        self.assertIn("decision-1", self.store.snapshot()["records"]["research_decision"])

    def test_rejected_scientific_decision_is_retained(self):
        self.reviewed_dossier(response_b=self.review_response(status="insufficient"))
        self.mutate(self.module("research_decisions").record_research_decision, self.decision())
        self.assertFalse(self.state()["ready"])
        self.assertIn("decision-1", self.store.snapshot()["records"]["research_decision"])

    def test_one_reviewer_cannot_supply_both_reviews_or_bar_identities(self):
        self.reviewed_dossier()
        payload = self.decision()
        payload["review_ids"] = ["slate-a", "slate-a"]
        self.error("research_review_pair_required", lambda: self.mutate(self.module("research_decisions").record_research_decision, payload))

    def test_response_content_cannot_be_replaced_at_record_time(self):
        self.reviewed_dossier()
        decisions = self.module("research_decisions")
        payload = self.review_response(status="insufficient")
        payload.update(id="altered", dossier_id="dossier-1", assignment_id="assignment-slate-a")
        self.error("value_review_output_mismatch", lambda: self.mutate(decisions.record_value_review, payload))

    def test_a_counterexample_cannot_be_resolved_by_author_assertion(self):
        objection = {"id": "counterexample", "claim": "The deciding inference", "reason": "Two mechanisms fit the evidence.",
                     "evidence": [self.evidence], "resolution_condition": "Supply a discriminator or withdraw the inference."}
        self.reviewed_dossier(response_b=self.review_response(objections=[objection]))
        payload = self.decision()
        payload["objections"] = [{"id": "counterexample", "disposition": "resolved", "reason": "The author disagrees.",
                                  "evidence": [self.evidence], "adjudication_id": None}]
        self.mutate(self.module("research_decisions").record_research_decision, payload)
        self.assertIn("research_objection_unresolved", self.codes(self.state()))

    def test_later_contamination_invalidates_current_approval_and_retains_science(self):
        self.reviewed_dossier()
        self.mutate(self.module("research_decisions").record_research_decision, self.decision())
        self.assertTrue(self.state()["ready"])
        self.mutate(self.module("review_protocol").record_context_event, {"id": "exposure", "assignment_id": "assignment-slate-b",
                    "kind": "contamination", "source": "prior_score_memory", "reason": "A prior score reached this reviewer.", "evidence": [self.evidence]})
        self.assertFalse(self.state()["ready"])
        self.assertIn("review_context_contaminated", self.codes(self.state()))
        self.assertEqual(self.store.snapshot()["records"]["value_review"]["slate-b"]["payload"]["value"]["status"], "sufficient")

    def test_closing_a_branch_requires_no_manuscript_and_keeps_the_full_goal_open(self):
        self.reviewed_dossier(response_b=self.review_response(status="insufficient"))
        payload = self.decision(action="close_branch")
        self.mutate(self.module("research_decisions").record_research_decision, payload)
        self.assertTrue(self.state("round")["ready"], self.state("round")["obligations"])
        self.assertFalse(self.state("write")["ready"])
        self.assertEqual(self.state("round")["decision"]["payload"]["goal_status"], "open")

    def test_branch_closure_does_not_assert_complete_scientific_success(self):
        self.reviewed_dossier()
        payload = self.decision(action="close_branch")
        payload.update(goal_status="achieved", remaining_objective=[])
        self.error("research_goal_not_achieved", lambda: self.mutate(self.module("research_decisions").record_research_decision, payload))

    def test_commitment_consumes_exact_proposed_content_with_a_new_native_id(self):
        self.reviewed_dossier()
        decisions = self.module("research_decisions")
        self.mutate(decisions.record_research_decision, self.decision())
        target = {"kind": "objective", "id": "native-first-target", "statement": self.intent()["objective"]["statement"]}
        changes, commitment = decisions.prepare_objective_commitment(self.store.snapshot()["records"], self.artifacts, target,
            decision_id="decision-1", source_impact=self.decision()["source_impact"], revision=self.store.revision + 1)
        self.assertEqual(commitment["objective"], target)
        self.assertEqual(commitment["proposal"]["id"], "proposal-1")
        self.assertEqual(changes[0][0], "research_commitment")
        changed = dict(target, statement="A conveniently smaller target.")
        self.error("research_objective_commitment_mismatch", lambda: decisions.prepare_objective_commitment(
            self.store.snapshot()["records"], self.artifacts, changed, decision_id="decision-1",
            source_impact=self.decision()["source_impact"], revision=self.store.revision + 1))

    def test_new_source_requires_explicit_impact_before_commitment(self):
        self.reviewed_dossier()
        decisions = self.module("research_decisions")
        self.mutate(decisions.record_research_decision, self.decision())
        self.put("source", "new-source", {"id": "new-source", "status": "captured", "response_complete": True, "response": self.evidence})
        target = {"kind": "objective", "id": "native-first-target", "statement": self.intent()["objective"]["statement"]}
        self.error("research_source_impact_required", lambda: decisions.prepare_objective_commitment(
            self.store.snapshot()["records"], self.artifacts, target, decision_id="decision-1",
            source_impact=self.decision()["source_impact"], revision=self.store.revision + 1))
        impact = copy.deepcopy(self.decision()["source_impact"])
        impact["sources"] = [{"source_id": "new-source", "impact": "material", "reason": "This comparator resolves the question already.", "evidence": [self.evidence]}]
        self.error("research_source_reassessment_required", lambda: decisions.prepare_objective_commitment(
            self.store.snapshot()["records"], self.artifacts, target, decision_id="decision-1",
            source_impact=impact, revision=self.store.revision + 1))
        impact["sources"][0].update(impact="unrelated", reason="A transport fixture unrelated to the scientific comparison.")
        changes, _ = decisions.prepare_objective_commitment(self.store.snapshot()["records"], self.artifacts, target,
            decision_id="decision-1", source_impact=impact, revision=self.store.revision + 1)
        self.assertTrue(changes)

    def test_duplicate_decision_does_not_resample_existing_findings(self):
        self.reviewed_dossier()
        decisions = self.module("research_decisions")
        self.mutate(decisions.record_research_decision, self.decision())
        self.error("research_decision_duplicate", lambda: self.mutate(decisions.record_research_decision, self.decision("renamed")))

    def test_an_unlinked_extra_cycle_cannot_spend_an_approved_tranche(self):
        self.reviewed_dossier()
        decisions = self.module("research_decisions")
        decision = self.mutate(decisions.record_research_decision, self.decision())["result"]
        plan = {"id": "cycle", "candidate_id": "candidate-a", "decision_id": decision["id"],
                "question": "Does the control separate mechanisms?", "distinguishing_test": "Discriminator",
                "strategy": {"mechanism": "Discriminator"},
                "resource_limits": {"max_executions": 2, "max_units": 10, "unit": "seconds"}}
        decisions.validate_cycle_decision(self.store.snapshot()["records"], self.artifacts, plan, decision)
        unrelated = dict(plan, candidate_id="unreviewed")
        self.error("research_cycle_mismatch", lambda: decisions.validate_cycle_decision(self.store.snapshot()["records"], self.artifacts, unrelated, decision))
        self.put("execution_admission", "spent", {"id": "spent", "research_decision": {"id": decision["id"], "digest": decision["digest"]}, "reserved_units": 9})
        self.error("research_tranche_exhausted", lambda: decisions.validate_cycle_decision(self.store.snapshot()["records"], self.artifacts, plan, decision, reserved_units=2))

    def budget_plan(self, resource_unit="seconds"):
        intent = self.intent()
        intent["resources"] = [{"kind": "compute", "limit": 5, "unit": resource_unit, "authorization": "instruction"}]
        self.intent = lambda identifier="intent-1": copy.deepcopy(intent)
        self.reviewed_dossier()
        decisions = self.module("research_decisions")
        decision = self.mutate(decisions.record_research_decision, self.decision())["result"]
        plan = {"id": "cycle", "candidate_id": "candidate-a", "decision_id": decision["id"],
                "question": "Does the control separate mechanisms?", "distinguishing_test": "Discriminator",
                "strategy": {"mechanism": "Discriminator"}, "resource_limits": {"max_executions": 2, "max_units": 4, "unit": "seconds"}}
        return decisions, decision, plan

    def test_authorized_same_unit_budget_caps_the_reviewed_tranche(self):
        decisions, decision, plan = self.budget_plan()
        plan["resource_limits"]["max_units"] = 6
        self.error("research_intent_budget_exceeded", lambda: decisions.validate_cycle_decision(
            self.store.snapshot()["records"], self.artifacts, plan, decision))

    def test_execution_reservations_cannot_reset_the_authorized_budget(self):
        decisions, decision, plan = self.budget_plan()
        self.put("execution_admission", "spent", {"id": "spent", "research_decision": {"id": decision["id"], "digest": decision["digest"]}, "reserved_units": 4})
        decisions.validate_cycle_decision(self.store.snapshot()["records"], self.artifacts, plan, decision, reserved_units=1)
        self.error("research_intent_budget_exceeded", lambda: decisions.validate_cycle_decision(
            self.store.snapshot()["records"], self.artifacts, plan, decision, reserved_units=2))

    def test_an_unrelated_resource_unit_does_not_create_an_invented_conversion(self):
        decisions, decision, plan = self.budget_plan(resource_unit="dollars")
        plan["resource_limits"]["max_units"] = 6
        self.assertEqual(decisions.validate_cycle_decision(self.store.snapshot()["records"], self.artifacts, plan, decision)["limit"]["amount"], 10)

    def test_observed_focused_quantifier_correction_changes_only_the_disputed_decision(self):
        objection = {"id": "quantifier", "claim": "The supplied statement is universal.",
                     "reason": "The reviewer read the supplied universal quantifier as existential.",
                     "evidence": [self.evidence], "resolution_condition": "Check the quantifier in the supplied statement."}
        self.reviewed_dossier(response_b=self.review_response(objections=[objection]))
        decisions, protocol = self.module("research_decisions"), self.module("review_protocol")
        original = self.decision()
        original["objections"] = [{"id": "quantifier", "disposition": "resolved", "reason": "The supplied statement says every input.",
                                   "evidence": [self.evidence], "adjudication_id": None}]
        self.mutate(decisions.record_research_decision, original)
        self.assertIn("research_objection_unresolved", self.codes(self.state()))
        self.mutate(protocol.record_assignment, {"id": "focused", "route_id": "route", "dossier_id": "dossier-1",
            "role": "adjudicator", "reviewer_id": "reviewer-c", "author_id": "author",
            "context": {"review_ids": ["slate-a", "slate-b"], "objection_id": "quantifier",
                        "correction": {"kind": "misreading", "claim": "The statement says every input, not some input.",
                                       "reason": "The cited evidence already supplied the quantifier.", "evidence": [self.evidence],
                                       "prior_adjudication_id": None, "new_error_explanation": None}}})
        response = {"objection_id": "quantifier", "disposition": "not_upheld", "reason": "The supplied quantifier is universal.",
                    "evidence": [self.evidence], "correction_admissible": True}
        with patch("research_harness.review_transport.send_request", return_value=self.provider_response(response)):
            protocol.invoke_assignment(self.store, {"id": "focused-attempt", "assignment_id": "focused"},
                expected_revision=self.store.revision, request_id="focused-call", credential="fixture-only")
        self.mutate(protocol.record_adjudication, dict(response, id="quantifier-correction", assignment_id="focused"))
        corrected = copy.deepcopy(original)
        corrected["id"] = "corrected-decision"
        corrected["objections"][0]["adjudication_id"] = "quantifier-correction"
        self.mutate(decisions.record_research_decision, corrected)
        self.assertTrue(self.state()["ready"], self.state()["obligations"])
        self.assertEqual(len(self.store.snapshot()["records"]["value_review"]), 4)
        self.assertEqual(self.store.snapshot()["records"]["research_decision"][original["id"]]["payload"], original)
        from research_harness.evidence import digest
        finding = self.store.snapshot()["records"]["review_adjudication"]["quantifier-correction"]
        changed = self.dossier("corrected-dossier")
        changed.update(previous="dossier-1", material_change={"kind": "review_correction", "reason": "Reassess the demonstrated quantifier error.",
            "evidence": [{"kind": "record", "record_kind": "review_adjudication", "id": finding["id"], "digest": digest(finding)}],
            "prior_objections": [{"id": "quantifier", "status": "resolved", "reason": "The exact supplied quantifier was independently checked.", "evidence": [self.evidence]}]})
        self.mutate(self.module("strategy").record_strategy, changed)
        repeated = copy.deepcopy(changed)
        repeated.update(id="repeated-correction", previous="corrected-dossier")
        repeated["material_change"]["prior_objections"] = []
        self.error("strategy_material_change_unsubstantiated", lambda: self.mutate(self.module("strategy").record_strategy, repeated))
        later_objection = dict(objection, reason="The new statement has a distinct unresolved quantifier issue.")
        for suffix in ("a", "b"):
            observed = self.review_response(objections=[later_objection])
            observed["value"]["objection_findings"] = [{"id": "quantifier", "status": "continuing",
                "reason": "The prior correction does not resolve the new statement.", "evidence": [self.evidence]}]
            self.record_response("later-" + suffix, "reviewer-" + suffix, observed, dossier_id="corrected-dossier")
        later = copy.deepcopy(corrected)
        later.update(id="later-decision", dossier_id="corrected-dossier", review_ids=["later-a", "later-b"])
        self.mutate(decisions.record_research_decision, later)
        self.assertIn("research_objection_unresolved", self.codes(self.state()))
        self.assertEqual(self.store.snapshot()["records"]["review_adjudication"]["quantifier-correction"], finding)

    def test_two_favorable_slate_reviews_do_not_erase_an_earlier_material_counterexample(self):
        self.prepare_direction()
        self.setup_dossier()
        self.route()
        objection = {"id": "counterexample", "claim": "The proposed discriminator separates mechanisms.",
                     "reason": "The known control yields the same response for two mechanisms.", "evidence": [self.evidence],
                     "resolution_condition": "Refute the exhibited pair or supply a discriminating observation."}
        self.record_response("bar-a", "reviewer-a", self.review_response("bar", objections=[objection]))
        self.record_response("bar-b", "reviewer-b", self.review_response("bar"))
        self.record_response("slate-a", "reviewer-a", self.review_response())
        self.record_response("slate-b", "reviewer-b", self.review_response())
        payload = self.decision()
        self.error("research_objection_disposition_missing", lambda: self.mutate(self.module("research_decisions").record_research_decision, payload))
        payload["objections"] = [{"id": "counterexample", "disposition": "resolved", "reason": "Both later reviewers favor the approach.",
                                  "evidence": [self.evidence], "adjudication_id": None}]
        self.mutate(self.module("research_decisions").record_research_decision, payload)
        self.assertIn("research_objection_unresolved", self.codes(self.state()))

    def test_completed_bounded_prerequisite_allows_the_remaining_deciding_test(self):
        dossier = self.dossier()
        prerequisite = {"description": "Calibrate the control.", "end_condition": "Control calibration checked.", "exit_condition": "The control response is identified."}
        dossier["candidates"][0]["next_test"]["prerequisites"] = [prerequisite]
        self.reviewed_dossier(dossier_payload=dossier)
        decisions = self.module("research_decisions")
        decision = self.mutate(decisions.record_research_decision, self.decision())["result"]
        self.put("cycle_plan", "baseline", {"id": "baseline", "research_decision": {"id": decision["id"], "binding": {"prerequisite": True}},
            "payload": {"question": prerequisite["description"], "distinguishing_test": prerequisite["exit_condition"], "strategy": {"mechanism": "Control calibration"}}})
        self.put("cycle", "baseline", {"id": "baseline", "status": "complete", "assessment_id": "baseline-assessment"})
        self.put("cycle_assessment", "baseline-assessment", {"id": "baseline-assessment", "assessment": {"complete": True},
            "payload": {"cycle_id": "baseline", "failures": [{"signal_id": "bad-control", "status": "not_observed"}]}})
        plan = {"id": "deciding", "candidate_id": "candidate-a", "decision_id": decision["id"], "question": "Does the control separate mechanisms?",
                "distinguishing_test": "Discriminator", "strategy": {"mechanism": "Discriminator"},
                "resource_limits": {"max_executions": 1, "max_units": 5, "unit": "seconds"}}
        result = decisions.validate_cycle_decision(self.store.snapshot()["records"], self.artifacts, plan, decision, reserved_units=1)
        self.assertFalse(result["prerequisite"])
        self.put("cycle", "baseline", {"id": "baseline", "status": "failed", "assessment_id": "baseline-assessment"})
        self.error("research_tranche_reassessment_required", lambda: decisions.validate_cycle_decision(
            self.store.snapshot()["records"], self.artifacts, plan, decision, reserved_units=1))

    def test_new_decision_cannot_reset_reservations_for_the_same_tranche(self):
        self.reviewed_dossier()
        decisions = self.module("research_decisions")
        first = self.mutate(decisions.record_research_decision, self.decision())["result"]
        self.put("execution_admission", "spent", {"id": "spent", "research_decision": {"id": first["id"]}, "reserved_units": 9})
        revised = self.decision("decision-2", action="pivot")
        revised["reason"] = "Continue the same approved tranche with its retained cost."
        second = self.mutate(decisions.record_research_decision, revised)["result"]
        plan = {"id": "additional", "candidate_id": "candidate-a", "decision_id": second["id"], "question": "Does the control separate mechanisms?",
                "distinguishing_test": "Discriminator", "strategy": {"mechanism": "Discriminator"},
                "resource_limits": {"max_executions": 1, "max_units": 5, "unit": "seconds"}}
        self.error("research_tranche_exhausted", lambda: decisions.validate_cycle_decision(
            self.store.snapshot()["records"], self.artifacts, plan, second, reserved_units=2))
