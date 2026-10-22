"""Reviewers who cannot see each other's objections keep distinct objection identities when they choose one id."""

import json
import unittest
from unittest.mock import patch

from research_harness import research_decisions as decisions, review_protocol as protocol
from research_harness.errors import ResearchError
from strategy_fixtures import StrategyCase
import test_research_problem_delivery as fixtures
import test_research_problem_objections as objection_fixtures
import test_research_problem_slate as slate_fixtures

SHARED_ID = "counterexample-unverified"
QUALIFIED_ID = SHARED_ID + "@review-bar-second"
CLAIMS = {"first": "The comparator result is accepted as reported, not as verified.",
          "second": "Every refutation premise rests on one unreviewed manuscript."}
BARS = ["review-bar-first", "review-bar-second"]


def list_identity_pairs(objections):
    """Return the id and the stated id of each objection; the stated id is None for an objection under its own id."""
    return [(objection["id"], objection.get("stated_id")) for objection in objections]


class BlindObjectionIdentityTests(fixtures.ProblemDeliveryCase):
    build_slate_output = slate_fixtures.ProblemSlateTests.build_slate_output
    prepare_problem_evidence = objection_fixtures.ProblemObjectionTests.prepare_problem_evidence
    assert_blocked_delivery = objection_fixtures.ProblemObjectionTests.assert_blocked_delivery
    assert_delivered = objection_fixtures.ProblemObjectionTests.assert_delivered

    def make_objection(self, reviewer):
        return {"id": SHARED_ID, "claim": CLAIMS[reviewer], "reason": "No independent check was supplied.",
                "evidence": [self.supporting], "resolution_condition": "Supply an independent check."}

    def make_finding(self, identifier, status="resolved"):
        return {"id": identifier, "status": status, "reason": "The supplied comparator establishes this disposition.",
                "evidence": [self.supporting]}

    def observe_colliding_bars(self):
        self.prepare_problem_evidence()
        for reviewer in ("first", "second"):
            output = self.build_review_output("bar")
            output["objections"] = [self.make_objection(reviewer)]
            self.observe("bar", reviewer, output)

    def observe_colliding_bars_on_previous_prompt(self):
        """Record the colliding bars on the previous bar template, so that a later bar of each reviewer is a refresh."""
        original = protocol.canonical_prompt

        def build_previous_prompt(role, **options):
            if options.get("orchestration") == "problem_creation":
                options["version"] = 1
            return original(role, **options)
        self.prepare_problem_evidence()
        with patch.object(protocol, "canonical_prompt", side_effect=build_previous_prompt):
            for reviewer in ("first", "second"):
                output = self.build_review_output("bar")
                output["objections"] = [self.make_objection(reviewer)]
                self.observe("bar", reviewer, output)

    def refresh_bar(self, reviewer, identity, claim):
        """Refresh the reviewer's bar: a continuing finding on the identity of its objection, which it states again
        under the shared id with this claim."""
        output = self.build_review_output("bar")
        output["value"]["objection_findings"] = [self.make_finding(identity, "continuing")]
        output["objections"] = [dict(self.make_objection(reviewer), claim=claim)]
        return self.observe("bar", reviewer, output, identifier="bar-" + reviewer + "-v2")

    def prepare_colliding_bars(self):
        self.observe_colliding_bars()
        self.record_slate()

    def observe_results(self, statuses):
        for reviewer in ("first", "second"):
            output = self.build_review_output("result")
            output["value"]["objection_findings"] = [self.make_finding(identifier, status)
                                                     for identifier, status in statuses.items()]
            self.observe("result", reviewer, output)

    def find_unresolved_identities(self):
        reviews = decisions.assess_problem_reviews(self.store.snapshot()["records"], self.artifacts)
        return sorted(item["objection_id"] for item in reviews["obligations"] if item["code"] == "research_objection_unresolved")

    def adjudicate_not_upheld(self, objection_id, review_ids):
        self.assign("adjudicator", "focused-third", identifier="focused", review_ids=review_ids, objection_id=objection_id)
        output = {"objection_id": objection_id, "disposition": "not_upheld",
                  "reason": "The exact supplied comparator resolves the disputed claim.",
                  "evidence": [self.supporting], "correction_admissible": None}
        with patch("research_harness.review_transport.send_request", return_value=self.build_provider_response(output)):
            self.mutate(protocol.invoke_assignment, {"id": "focused-attempt", "assignment_id": "focused"}, credential="test-only")
        self.mutate(protocol.record_adjudication, dict(output, id="focused-finding", assignment_id="focused"))

    def repair_slot(self, role, predecessor, replacement, suffix="", earlier_event_ids=()):
        """Continue the predecessor assignment's slot through a context repair, as for a contaminated or retired route.
        A later repair of the same lineage passes a suffix for its own event, probe and assignment ids, and the ids of
        the earlier events, which it binds as well."""
        event_id = "exposure" + suffix
        self.mutate(protocol.record_context_event, {"id": event_id, "assignment_id": predecessor,
            "kind": "contamination", "source": "author_history", "reason": "Author advocacy entered the review context.",
            "evidence": [self.supporting]})

        def respond_to_probe(route, request, credential):
            packet = json.loads(request["input"][0]["content"].removeprefix("The packet below is JSON.\n"))
            return self.build_provider_response({"allowed_control": packet["allowed_control"],
                "evidence_control": packet["evidence_control"], "excluded_controls": []})
        with patch("research_harness.review_transport.send_request", side_effect=respond_to_probe):
            self.mutate(protocol.probe_route, {"id": "repair-probe" + suffix, "route_id": "route"}, credential="test-only")
        return self.assign(role, replacement, identifier="repaired-" + role + suffix, context_repair={
            "assignment_id": predecessor, "event_ids": [*earlier_event_ids, event_id], "probe_id": "repair-probe" + suffix,
            "reason": "Reassess the original review and its objection in a verified independent context.",
            "evidence": [self.supporting]})

    def confirm_refreshed_bar(self, reviewer, replacement, objection_id, **changes):
        """Refresh the reviewer's bar with its objection restated verbatim, repair the refresh, and record a repaired bar
        that repeats the refresh, its objection written under objection_id with the given changes, and labels the
        refresh confirmed."""
        self.observe_colliding_bars_on_previous_prompt()
        refreshed = self.refresh_bar(reviewer, QUALIFIED_ID if reviewer == "second" else SHARED_ID, CLAIMS[reviewer])
        assignment = self.repair_slot("bar", refreshed["assignment_id"], replacement)
        output = {key: refreshed["payload"][key] for key in ("stage", "support", "value", "objections", "limitations")}
        output["objections"] = [dict(objection, id=objection_id, **changes) for objection in output["objections"]]
        output["reassessment"] = {"assignment_id": refreshed["assignment_id"], "findings": [
            {"review_id": "review-bar-" + reviewer, "disposition": "revised",
             "reason": "The objection is now also carried as a finding.", "evidence": [self.supporting]},
            {"review_id": refreshed["id"], "disposition": "confirmed",
             "reason": "The refreshed finding stands unchanged.", "evidence": [self.supporting]}]}
        with patch("research_harness.review_transport.send_request", return_value=self.build_provider_response(output)):
            self.mutate(protocol.invoke_assignment, {"id": "repair-attempt", "assignment_id": assignment["id"]}, credential="test-only")
        return self.mutate(decisions.record_value_review, dict(output, id="review-repaired-bar", dossier_id=None,
                                                                assignment_id=assignment["id"]))["result"]

    def repair_result_recorded_under_the_identity(self, reviewer, disposition, **changes):
        """Record the reviewer's result review, which gives findings on both identities and restates the qualified
        objection under the qualified identity itself, repair it, and record a repaired result that repeats it, its
        objection written under the stated id with the given changes, with the given disposition for it."""
        self.prepare_colliding_bars()
        output = self.build_review_output("result")
        output["value"]["objection_findings"] = [self.make_finding(SHARED_ID), self.make_finding(QUALIFIED_ID, "continuing")]
        output["objections"] = [dict(self.make_objection("second"), id=QUALIFIED_ID)]
        original = self.observe("result", reviewer, output)
        assignment = self.repair_slot("result", original["assignment_id"], "replacement-" + reviewer)
        repaired = {key: original["payload"][key] for key in ("stage", "support", "value", "objections", "limitations")}
        repaired["objections"] = [dict(objection, id=SHARED_ID, **changes) for objection in repaired["objections"]]
        repaired["reassessment"] = {"assignment_id": original["assignment_id"], "findings": [
            {"review_id": original["id"], "disposition": disposition, "reason": "The result finding stands unchanged.",
             "evidence": [self.supporting]}]}
        with patch("research_harness.review_transport.send_request", return_value=self.build_provider_response(repaired)):
            self.mutate(protocol.invoke_assignment, {"id": "repair-attempt", "assignment_id": assignment["id"]}, credential="test-only")
        return self.mutate(decisions.record_value_review, dict(repaired, id="review-repaired-result", dossier_id=None,
                                                                assignment_id=assignment["id"]))["result"]

    def adjudicate(self, identifier, objection_id, review_ids, disposition, *, earlier_prompt=False):
        """Assign the focused adjudicator, observe the given disposition and record it; with earlier_prompt the
        assignment carries the adjudicator prompt from before the not_binding disposition."""
        prompt = protocol._PROBLEM_ADJUDICATOR_PROMPT_BEFORE_NOT_BINDING if earlier_prompt else protocol._PROBLEM_ADJUDICATOR_PROMPT
        with patch.object(protocol, "_PROBLEM_ADJUDICATOR_PROMPT", prompt):
            self.assign("adjudicator", "focused-third", identifier=identifier, review_ids=review_ids, objection_id=objection_id)
        output = {"objection_id": objection_id, "disposition": disposition,
                  "reason": "The claim holds on the evidence; the statement does not rest on the contested premise.",
                  "evidence": [self.supporting], "correction_admissible": None}
        with patch("research_harness.review_transport.send_request", return_value=self.build_provider_response(output)):
            self.mutate(protocol.invoke_assignment, {"id": identifier + "-attempt", "assignment_id": identifier}, credential="test-only")
        return self.mutate(protocol.record_adjudication, dict(output, id=identifier + "-finding", assignment_id=identifier))

    def test_an_objection_that_holds_but_does_not_bind_is_resolved_by_a_refreshed_adjudication(self):
        self.prepare_colliding_bars()
        self.observe_results({SHARED_ID: "continuing", QUALIFIED_ID: "continuing"})
        self.adjudicate("focused", QUALIFIED_ID, BARS, "upheld", earlier_prompt=True)
        self.assertIn(QUALIFIED_ID, self.find_unresolved_identities())
        self.adjudicate("focused-2", QUALIFIED_ID, BARS, "not_binding")
        records = self.store.snapshot()["records"]
        packet = json.loads(self.artifacts.read(records["review_assignment"]["focused-2"]["packet"]))
        self.assertEqual(packet["focused_refresh"]["reason"], "protocol_changed")
        self.assertNotIn(QUALIFIED_ID, self.find_unresolved_identities())
        self.assertIn(SHARED_ID, self.find_unresolved_identities())

    def test_the_earlier_adjudicator_prompt_offers_no_not_binding_disposition(self):
        self.prepare_colliding_bars()
        self.observe_results({SHARED_ID: "continuing", QUALIFIED_ID: "continuing"})
        with self.assertRaises(ResearchError) as refused:
            self.adjudicate("focused", QUALIFIED_ID, BARS, "not_binding", earlier_prompt=True)
        self.assertEqual(refused.exception.code, "invalid_review_adjudication")

    def test_blind_bars_keep_two_objections_under_distinct_identities(self):
        self.prepare_colliding_bars()
        records = self.store.snapshot()["records"]
        self.assertNotIn("objection_identities", records["value_review"]["review-bar-first"])
        second = records["value_review"]["review-bar-second"]
        self.assertEqual(second["objection_identities"], {SHARED_ID: QUALIFIED_ID})
        self.assertEqual(second["payload"]["objections"][0]["id"], SHARED_ID)
        stated, = decisions.list_stated_objections(second)
        self.assertEqual((stated["id"], stated["stated_id"]), (QUALIFIED_ID, SHARED_ID))

    def test_result_packets_deliver_both_objections_with_their_own_claims(self):
        self.prepare_colliding_bars()
        assignment = self.assign("result", "first")
        packet = json.loads(self.artifacts.read(assignment["packet"]))
        claims = {objection["id"]: objection["claim"] for objection in packet["material_objections"]}
        self.assertEqual(claims, {SHARED_ID: CLAIMS["first"], QUALIFIED_ID: CLAIMS["second"]})
        delivered = {bar["id"]: [objection["id"] for objection in bar["objections"]] for bar in packet["bars"]}
        self.assertEqual(delivered, {"review-bar-first": [SHARED_ID], "review-bar-second": [QUALIFIED_ID]})

    def test_a_result_review_gives_a_finding_on_each_identity(self):
        self.prepare_colliding_bars()
        output = self.build_review_output("result")
        output["value"]["objection_findings"] = [self.make_finding(SHARED_ID)]
        with self.assertRaises(ResearchError) as raised:
            self.observe("result", "first", output)
        self.assertEqual(raised.exception.code, "review_objection_history_incomplete")

    def test_both_identities_resolved_allow_delivery(self):
        self.prepare_colliding_bars()
        self.observe_results({SHARED_ID: "resolved", QUALIFIED_ID: "resolved"})
        self.assert_delivered()

    def test_an_unresolved_qualified_identity_keeps_the_post_blocked(self):
        self.prepare_colliding_bars()
        self.observe_results({SHARED_ID: "resolved", QUALIFIED_ID: "continuing"})
        self.assertEqual(self.find_unresolved_identities(), [QUALIFIED_ID])
        self.assert_blocked_delivery()

    def test_a_reviewer_that_received_the_objection_cannot_restate_its_id_with_another_claim(self):
        self.prepare_colliding_bars()
        output = self.build_review_output("result")
        output["value"]["objection_findings"] = [self.make_finding(SHARED_ID), self.make_finding(QUALIFIED_ID)]
        output["objections"] = [dict(self.make_objection("first"), claim="A third claim under the delivered id.")]
        with self.assertRaises(ResearchError) as raised:
            self.observe("result", "first", output)
        self.assertEqual(raised.exception.code, "research_objection_identity_conflict")

    def test_a_restatement_with_another_claim_is_a_recorded_refusal(self):
        self.prepare_colliding_bars()
        output = self.build_review_output("result")
        output["value"]["objection_findings"] = [self.make_finding(SHARED_ID), self.make_finding(QUALIFIED_ID)]
        output["objections"] = [dict(self.make_objection("first"), claim="A third claim under the delivered id.")]
        with self.assertRaises(ResearchError):
            self.observe("result", "first", output)
        refusal, = self.store.snapshot()["records"]["review_output_refusal"].values()
        self.assertEqual((refusal["code"], refusal["check"]), ("research_objection_identity_conflict", "review.objections"))

    def test_the_qualified_slot_restates_its_own_objection_under_its_identity(self):
        self.observe_colliding_bars()
        output = self.build_slate_output(["candidate-1"])
        output["objections"] = [dict(self.make_objection("second"), candidate_ids=[])]
        saved = self.observe("slate", "second", output)
        self.assertEqual(saved["objection_identities"], {SHARED_ID: QUALIFIED_ID})

    def test_a_bar_pair_adjudication_resolves_the_qualified_identity(self):
        self.prepare_colliding_bars()
        self.observe_results({SHARED_ID: "resolved", QUALIFIED_ID: "continuing"})
        self.adjudicate_not_upheld(QUALIFIED_ID, BARS)
        self.assertEqual(self.find_unresolved_identities(), [])
        self.assert_delivered()

    def test_a_context_repair_of_the_qualified_slot_continues_under_its_identity(self):
        self.observe_colliding_bars()
        assignment = self.repair_slot("bar", "bar-second", "replacement")
        packet = json.loads(self.artifacts.read(assignment["packet"]))
        repaired = [[objection["id"] for objection in entry["payload"]["objections"]] for entry in packet["historical_reviews"]]
        self.assertEqual(repaired, [[QUALIFIED_ID]])
        output = self.build_review_output("bar")
        output["value"]["objection_findings"] = [self.make_finding(QUALIFIED_ID, "continuing")]
        output["reassessment"] = {"assignment_id": "bar-second", "findings": [{
            "review_id": "review-bar-second", "disposition": "revised",
            "reason": "The prior material objection still stands.", "evidence": [self.supporting]}]}
        with patch("research_harness.review_transport.send_request", return_value=self.build_provider_response(output)):
            self.mutate(protocol.invoke_assignment, {"id": "repair-attempt", "assignment_id": "repaired-bar"}, credential="test-only")
        self.mutate(decisions.record_value_review, dict(output, id="review-repaired-bar", dossier_id=None, assignment_id="repaired-bar"))
        records = self.store.snapshot()["records"]
        versions, _ = decisions._fold_objection_versions(records["value_review"].values(),
                                                         decisions._collect_superseded_review_ids(records, self.artifacts))
        self.assertEqual([row["review_id"] for row in versions[QUALIFIED_ID]], ["review-bar-second", "review-repaired-bar"])
        self.assertEqual([row["review_id"] for row in versions[SHARED_ID]], ["review-bar-first"])

    def test_a_context_repair_confirms_an_unchanged_qualified_review_written_under_the_stated_id(self):
        saved = self.confirm_refreshed_bar("second", "replacement", SHARED_ID)
        self.assertEqual(list_identity_pairs(decisions.list_stated_objections(saved)), [(QUALIFIED_ID, SHARED_ID)])

    def test_a_context_repair_confirms_an_unchanged_qualified_review_written_under_its_identity(self):
        saved = self.confirm_refreshed_bar("second", "replacement", QUALIFIED_ID)
        self.assertEqual(list_identity_pairs(decisions.list_stated_objections(saved)), [(QUALIFIED_ID, None)])

    def test_a_context_repair_confirms_an_unchanged_review_of_the_other_slot(self):
        saved = self.confirm_refreshed_bar("first", "replacement-first", SHARED_ID)
        self.assertEqual(list_identity_pairs(decisions.list_stated_objections(saved)), [(SHARED_ID, None)])

    def test_a_second_context_repair_confirms_a_review_recorded_under_its_identity_written_under_the_stated_id(self):
        repaired = self.confirm_refreshed_bar("second", "replacement", QUALIFIED_ID)
        assignment = self.repair_slot("bar", repaired["assignment_id"], "replacement-2", suffix="-2",
                                      earlier_event_ids=["exposure"])
        packet = json.loads(self.artifacts.read(assignment["packet"]))
        # The confirmed review was recorded under the identity itself, so only the two earlier reviews deliver the stated id.
        self.assertEqual([(entry["id"], list_identity_pairs(entry["payload"]["objections"])) for entry in packet["historical_reviews"]],
                         [("review-bar-second", [(QUALIFIED_ID, SHARED_ID)]), ("review-bar-second-v2", [(QUALIFIED_ID, SHARED_ID)]),
                          (repaired["id"], [(QUALIFIED_ID, None)])])
        output = {key: repaired["payload"][key] for key in ("stage", "support", "value", "objections", "limitations")}
        output["objections"] = [dict(objection, id=SHARED_ID) for objection in output["objections"]]
        revised = {"disposition": "revised", "reason": "The objection is now also carried as a finding.",
                   "evidence": [self.supporting]}
        output["reassessment"] = {"assignment_id": repaired["assignment_id"], "findings": [
            dict(revised, review_id="review-bar-second"), dict(revised, review_id="review-bar-second-v2"),
            {"review_id": repaired["id"], "disposition": "confirmed",
             "reason": "The repaired finding stands unchanged.", "evidence": [self.supporting]}]}
        with patch("research_harness.review_transport.send_request", return_value=self.build_provider_response(output)):
            self.mutate(protocol.invoke_assignment, {"id": "repair-attempt-2", "assignment_id": assignment["id"]}, credential="test-only")
        saved = self.mutate(decisions.record_value_review, dict(output, id="review-repaired-bar-2", dossier_id=None,
                                                                assignment_id=assignment["id"]))["result"]
        self.assertEqual(list_identity_pairs(decisions.list_stated_objections(saved)), [(QUALIFIED_ID, SHARED_ID)])

    def test_a_context_repair_cannot_confirm_a_qualified_review_with_the_other_slot_claim_under_the_stated_id(self):
        with self.assertRaises(ResearchError) as raised:
            self.confirm_refreshed_bar("second", "replacement", SHARED_ID, claim=CLAIMS["first"])
        self.assertEqual(raised.exception.code, "review_reassessment_mismatch")
        self.assertEqual(self.store.snapshot()["records"]["review_output_refusal"]["repair-attempt"]["code"],
                         "review_reassessment_mismatch")

    def test_a_context_repair_cannot_confirm_a_qualified_review_with_a_changed_reason_under_the_stated_id(self):
        with self.assertRaises(ResearchError) as raised:
            self.confirm_refreshed_bar("second", "replacement", SHARED_ID, reason="A changed reason.")
        self.assertEqual(raised.exception.code, "review_reassessment_mismatch")
        self.assertEqual(self.store.snapshot()["records"]["review_output_refusal"]["repair-attempt"]["code"],
                         "review_reassessment_mismatch")

    def test_a_context_repair_confirms_a_result_recorded_under_the_identity_written_under_the_stated_id(self):
        saved = self.repair_result_recorded_under_the_identity("second", "confirmed")
        self.assertEqual(list_identity_pairs(decisions.list_stated_objections(saved)), [(QUALIFIED_ID, SHARED_ID)])
        records = self.store.snapshot()["records"]
        packet = json.loads(self.artifacts.read(records["review_assignment"][saved["assignment_id"]]["packet"]))
        # The inherited result delivers no stated id; the stated id and claim reach the packet in its material objections.
        self.assertEqual([(entry["id"], list_identity_pairs(entry["payload"]["objections"])) for entry in packet["historical_reviews"]],
                         [("review-result-second", [(QUALIFIED_ID, None)])])
        self.assertIn((QUALIFIED_ID, SHARED_ID), list_identity_pairs(packet["material_objections"]))

    def test_a_context_repair_of_the_other_slot_confirms_a_result_recorded_under_the_identity_written_under_the_stated_id(self):
        saved = self.repair_result_recorded_under_the_identity("first", "confirmed")
        self.assertEqual(list_identity_pairs(decisions.list_stated_objections(saved)), [(QUALIFIED_ID, SHARED_ID)])

    def test_a_context_repair_revises_a_result_recorded_under_the_identity_under_the_same_identity(self):
        saved = self.repair_result_recorded_under_the_identity("second", "revised")
        self.assertEqual(list_identity_pairs(decisions.list_stated_objections(saved)), [(QUALIFIED_ID, SHARED_ID)])

    def test_a_context_repair_cannot_confirm_a_result_recorded_under_the_identity_with_a_changed_reason(self):
        with self.assertRaises(ResearchError) as raised:
            self.repair_result_recorded_under_the_identity("second", "confirmed", reason="A changed reason.")
        self.assertEqual(raised.exception.code, "review_reassessment_mismatch")
        self.assertEqual(self.store.snapshot()["records"]["review_output_refusal"]["repair-attempt"]["code"],
                         "review_reassessment_mismatch")

    def test_a_bar_refresh_of_the_qualified_slot_cannot_reword_its_objection_under_the_stated_id(self):
        self.observe_colliding_bars_on_previous_prompt()
        with self.assertRaises(ResearchError) as raised:
            self.refresh_bar("second", QUALIFIED_ID, CLAIMS["second"] + " Reworded by the refresh.")
        self.assertEqual(raised.exception.code, "research_objection_identity_conflict")

    def test_a_bar_refresh_of_the_other_slot_cannot_reword_its_objection(self):
        self.observe_colliding_bars_on_previous_prompt()
        with self.assertRaises(ResearchError) as raised:
            self.refresh_bar("first", SHARED_ID, CLAIMS["first"] + " Reworded by the refresh.")
        self.assertEqual(raised.exception.code, "research_objection_identity_conflict")

    def test_a_bar_refresh_of_the_qualified_slot_restates_its_objection_under_its_identity(self):
        self.observe_colliding_bars_on_previous_prompt()
        saved = self.refresh_bar("second", QUALIFIED_ID, CLAIMS["second"])
        self.assertEqual(saved["objection_identities"], {SHARED_ID: QUALIFIED_ID})
        records = self.store.snapshot()["records"]
        versions, held = decisions._fold_objection_versions(records["value_review"].values(),
                                                            decisions._collect_superseded_review_ids(records, self.artifacts))
        self.assertEqual({identity: [row["review_id"] for row in rows if row["material_version"] in held]
                          for identity, rows in versions.items()},
                         {SHARED_ID: ["review-bar-first"], QUALIFIED_ID: ["review-bar-second-v2"]})

    def test_a_version_derived_from_a_finding_keeps_the_stated_id(self):
        self.prepare_colliding_bars()
        self.observe_results({SHARED_ID: "resolved", QUALIFIED_ID: "continuing"})
        assignment = self.assign("adjudicator", "focused-third", identifier="focused", review_ids=BARS, objection_id=QUALIFIED_ID)
        derived = json.loads(self.artifacts.read(assignment["packet"]))["objection"]
        records = self.store.snapshot()["records"]
        stated, = decisions.list_stated_objections(records["value_review"]["review-bar-second"])
        result = records["value_review"]["review-result-first"]
        finding, = [item for item in result["payload"]["value"]["objection_findings"] if item["id"] == QUALIFIED_ID]
        # Version 2 derived the same version without stated_id; material_version does not digest stated_id.
        unchanged = decisions.state_objection(result, dict({key: stated[key] for key in ("claim", "resolution_condition")},
                                                           **{key: finding[key] for key in ("id", "reason", "evidence")}))
        self.assertEqual((derived["id"], derived.get("stated_id"), derived["review_id"]),
                         (QUALIFIED_ID, SHARED_ID, "review-result-first"))
        self.assertEqual(derived["material_version"], unchanged["material_version"])

    def test_mapping_objections_twice_equals_mapping_them_once(self):
        self.prepare_colliding_bars()
        records = self.store.snapshot()["records"]
        stored = records["value_review"]["review-bar-second"]
        for delivered in (protocol.deliver_review(records, stored["payload"]), protocol.deliver_review(records, stored)):
            self.assertEqual(protocol.deliver_review(records, delivered), delivered)
        # The second map names an identity as a stated id, which only contrived reviewer output can produce.
        for identities in (stored["objection_identities"], {"A": "A@R1", "A@R1": "A@R1@R"}):
            once = decisions.apply_objection_identities(identities, [{"id": identifier} for identifier in identities])
            self.assertEqual(decisions.apply_objection_identities(identities, once), once)

    def test_the_adjudicator_receives_the_bars_and_the_stating_review_under_their_identities(self):
        self.prepare_colliding_bars()
        assignment = self.assign("adjudicator", "focused-third", identifier="focused", review_ids=BARS, objection_id=QUALIFIED_ID)
        packet = json.loads(self.artifacts.read(assignment["packet"]))
        self.assertEqual([list_identity_pairs(review["objections"]) for review in packet["assessments"]],
                         [[(SHARED_ID, None)], [(QUALIFIED_ID, SHARED_ID)]])
        self.assertEqual(list_identity_pairs(packet["version_review"]["objections"]), [(QUALIFIED_ID, SHARED_ID)])

    def test_a_slate_repair_delivers_each_bar_identity_once_with_its_stated_id(self):
        self.observe_colliding_bars()
        slate = self.build_slate_output(["candidate-1"])
        slate["objections"] = [dict(self.make_objection("second"), candidate_ids=[])]
        original = self.observe("slate", "second", slate)
        repaired = self.repair_slot("slate", "slate-second", "repaired-second")
        records = self.store.snapshot()["records"]
        predecessor = json.loads(self.artifacts.read(records["review_assignment"]["slate-second"]["packet"]))
        packet = json.loads(self.artifacts.read(repaired["packet"]))
        self.assertEqual({key: [(bar["id"], list_identity_pairs(bar["objections"])) for bar in packet[key]]
                          for key in ("bars", "own_bars")},
                         {"bars": [("review-bar-first", [(SHARED_ID, None)]), ("review-bar-second", [(QUALIFIED_ID, SHARED_ID)])],
                          "own_bars": [("review-bar-second", [(QUALIFIED_ID, SHARED_ID)])]})
        self.assertEqual((packet["bars"], packet["own_bars"]), (predecessor["bars"], predecessor["own_bars"]))
        output = self.build_slate_output(["candidate-1"])
        output["value"]["objection_findings"] = [self.make_finding(QUALIFIED_ID, "continuing")]
        output["reassessment"] = {"assignment_id": "slate-second", "findings": [{"review_id": original["id"],
            "disposition": "revised", "reason": "The objection is now carried as a finding.", "evidence": [self.supporting]}]}
        with patch("research_harness.review_transport.send_request", return_value=self.build_provider_response(output)):
            self.mutate(protocol.invoke_assignment, {"id": "repair-attempt", "assignment_id": repaired["id"]}, credential="test-only")
        saved = self.mutate(decisions.record_value_review, dict(output, id="review-repaired-slate", dossier_id=None,
                                                                 assignment_id=repaired["id"]))["result"]
        self.assertNotIn("objection_identities", saved)

    def test_a_qualified_identity_that_is_already_used_keeps_the_conflict(self):
        self.prepare_problem_evidence()
        output = self.build_review_output("bar")
        output["objections"] = [self.make_objection("first"), dict(self.make_objection("first"), id=QUALIFIED_ID,
                                                                    claim="The first bar states this claim under the qualified identity.")]
        self.observe("bar", "first", output)
        output = self.build_review_output("bar")
        output["objections"] = [self.make_objection("second")]
        with self.assertRaises(ResearchError) as raised:
            self.observe("bar", "second", output)
        self.assertEqual(raised.exception.code, "research_objection_identity_conflict")


class DossierObjectionIdentityTests(StrategyCase):
    """AI Science dossiers keep one claim per objection id for every reviewer."""

    def setUp(self):
        super().setUp()
        self.setup_dossier()
        self.route()

    def test_a_second_dossier_bar_cannot_name_another_claim_under_the_same_id(self):
        def make_bar(reviewer):
            output = self.review_response("bar")
            output["objections"] = [{"id": SHARED_ID, "claim": CLAIMS[reviewer], "reason": "No independent check was supplied.",
                                     "evidence": [self.evidence], "resolution_condition": "Supply an independent check."}]
            return output
        self.record_response("bar-a", "reviewer-a", make_bar("first"))
        with self.assertRaises(ResearchError) as raised:
            self.record_response("bar-b", "reviewer-b", make_bar("second"))
        self.assertEqual(raised.exception.code, "research_objection_identity_conflict")


if __name__ == "__main__":
    unittest.main()
