"""A retired route accepts no new work, and a preserved slot continues through a context repair on another route."""

import json
from unittest.mock import patch

from research_harness.errors import ResearchError

from strategy_fixtures import StrategyCase
from research_harness import research_decisions, review_context_repair, review_protocol


class RouteRetirementCase(StrategyCase):
    def retire(self, identifier="retirement", assignment="assignment-bar-a"):
        return self.mutate(review_protocol.record_context_event, {"id": identifier, "assignment_id": assignment,
            "kind": "route_retired", "source": "owner",
            "reason": "The owner withdrew this route on 2026-10-21; no further call on it is permitted.",
            "evidence": [self.evidence]})

    def second_route(self, probe_id="repair-probe"):
        self.mutate(review_protocol.record_route, {"id": "route-2", "adapter": "openai_responses_v1", "model": "fixture-model-2",
                    "endpoint": "https://api.openai.com/v1/responses", "configuration": {"max_output_tokens": 1500, "timeout_seconds": 5}})

        def provider(route, request, credential):
            packet = json.loads(request["input"][0]["content"].removeprefix("The packet below is JSON.\n"))
            return self.provider_response({"allowed_control": packet["allowed_control"],
                "evidence_control": packet["evidence_control"], "excluded_controls": []})

        with patch("research_harness.review_transport.send_request", side_effect=provider):
            return review_protocol.probe_route(self.store, {"id": probe_id, "route_id": "route-2"},
                expected_revision=self.store.revision, request_id=probe_id + "-call", credential="fixture-only")

    def repair_payload(self, predecessor="assignment-bar-a", events=("retirement",)):
        return {"id": "repair-a", "route_id": "route-2", "dossier_id": "dossier-1", "role": "bar",
            "reviewer_id": "repair-reviewer", "author_id": "author", "context": {"context_repair": {
                "assignment_id": predecessor, "event_ids": list(events), "probe_id": "repair-probe",
                "reason": "The route of the slot was retired; a reviewer on another route continues it.", "evidence": [self.evidence]}}}

    def state(self, assignment):
        return review_protocol.assignment_state(self.store.snapshot()["records"], self.artifacts, assignment)


class RetiredRouteTests(RouteRetirementCase):
    def setUp(self):
        super().setUp()
        self.setup_dossier()
        self.route()
        self.record_response("bar-a", "reviewer-a", self.review_response("bar", status="insufficient"))
        self.record_response("bar-b", "reviewer-b", self.review_response("bar"))

    def test_a_retired_route_accepts_no_new_assignment_and_no_run(self):
        slate = {"id": "assignment-slate-a", "route_id": "route", "dossier_id": "dossier-1", "role": "slate",
                 "reviewer_id": "reviewer-a", "author_id": "author", "context": {}}
        self.mutate(review_protocol.record_assignment, slate)
        self.retire()
        with self.assertRaises(ResearchError) as refused:
            self.mutate(review_protocol.record_assignment, dict(slate, id="assignment-slate-b", reviewer_id="reviewer-b"))
        self.assertEqual(refused.exception.code, "review_route_retired")
        with self.assertRaises(ResearchError) as refused:
            with patch("research_harness.review_transport.send_request", return_value=self.provider_response(self.review_response())):
                review_protocol.invoke_assignment(self.store, {"id": "attempt-slate-a", "assignment_id": "assignment-slate-a"},
                                                  expected_revision=self.store.revision, request_id="invoke-slate-a", credential="fixture-only")
        self.assertEqual(refused.exception.code, "review_route_retired")
        self.assertIn("review_route_retired", [item["code"] for item in self.state("assignment-slate-a")["obligations"]])

    def test_a_retirement_keeps_the_observed_assignments_verified(self):
        self.retire()
        state = self.state("assignment-bar-a")
        self.assertTrue(state["ready"], state["obligations"])
        self.assertEqual(state["context_status"], "verified_for_route")
        records = self.store.snapshot()["records"]
        self.assertEqual(review_protocol.route_state(records, self.artifacts, "route")["context_status"], "verified_for_route")
        self.assertEqual(review_protocol.retired_routes(records), {"route"})

    def test_a_retirement_is_a_cause_for_a_repair_on_another_route(self):
        self.retire()
        self.second_route()
        saved = self.mutate(review_protocol.record_assignment, self.repair_payload())["result"]
        records = self.store.snapshot()["records"]
        self.assertEqual(review_context_repair.slot_id(records, saved), "assignment-bar-a")
        packet = json.loads(self.artifacts.read(saved["packet"]))
        self.assertEqual(packet["context_repair"]["event_ids"], ["retirement"])
        self.assertEqual(packet["context_repair"]["cause"], [{"id": "retirement", "kind": "route_retired", "source": "owner",
            "reason": "The owner withdrew this route on 2026-10-21; no further call on it is permitted."}])
        self.assertEqual(packet["context_repair"]["historical_review_ids"], ["bar-a"])
        # The repair binds the retirement exactly: another event id or other evidence is refused.
        payload = self.repair_payload(events=("other",))
        payload["id"] = "repair-x"
        with self.assertRaises(ResearchError) as refused:
            self.mutate(review_protocol.record_assignment, payload)
        self.assertEqual(refused.exception.code, "review_context_repair_mismatch")


class ExhaustedRepairTests(RouteRetirementCase):
    """An exhausted repair position gives way to a new repair of the same slot."""

    def setUp(self):
        super().setUp()
        self.setup_dossier()
        self.route()
        self.record_response("bar-a", "reviewer-a", self.review_response("bar", status="insufficient"))
        self.record_response("bar-b", "reviewer-b", self.review_response("bar"))
        self.retire()
        self.second_route()

    def refuse_outputs(self, assignment, attempts=("first", "second")):
        """Refuse outputs that change the inherited bar while labeling it confirmed."""
        output = dict(self.review_response("bar"), reassessment={"assignment_id": "assignment-bar-a", "findings": [
            {"review_id": "bar-a", "disposition": "confirmed", "reason": "The standard stands.", "evidence": [self.evidence]}]})
        for attempt in attempts:
            with patch("research_harness.review_transport.send_request", return_value=self.provider_response(output)):
                review_protocol.invoke_assignment(self.store, {"id": assignment + "-" + attempt, "assignment_id": assignment},
                    expected_revision=self.store.revision, request_id="invoke-" + assignment + "-" + attempt, credential="fixture-only")
            with self.assertRaises(ResearchError) as refused:
                self.mutate(research_decisions.record_value_review,
                            dict(output, id="repaired-" + attempt, dossier_id="dossier-1", assignment_id=assignment))
            self.assertEqual(refused.exception.code, "review_reassessment_mismatch")

    def test_a_new_repair_continues_the_slot_after_two_refused_outputs(self):
        self.mutate(review_protocol.record_assignment, self.repair_payload())
        self.refuse_outputs("repair-a")
        payload = dict(self.repair_payload(), id="repair-b", reviewer_id="repair-reviewer-2")
        with self.assertRaises(ResearchError) as refused:
            self.mutate(review_protocol.record_assignment, payload)
        self.assertEqual(refused.exception.code, "review_resampling_forbidden")
        from research_harness.review_continuation import plan_next
        head = "repair-a"
        for _ in range(2):
            plan = plan_next(self.store.snapshot()["records"], self.artifacts, head)
            self.assertEqual(plan["state"], "replace", plan)
            replacement = self.mutate(review_protocol.record_assignment, plan["payload"])["result"]
            head = replacement["id"]
            self.refuse_outputs(head, attempts=("first",))
        saved = self.mutate(review_protocol.record_assignment, payload)["result"]
        self.assertEqual(review_context_repair.slot_id(self.store.snapshot()["records"], saved), "assignment-bar-a")

    def test_a_repair_with_a_recordable_output_still_holds_the_slot(self):
        self.mutate(review_protocol.record_assignment, self.repair_payload())
        with patch("research_harness.review_transport.send_request",
                   return_value=self.provider_response(self.review_response("bar"))):
            review_protocol.invoke_assignment(self.store, {"id": "repair-a-first", "assignment_id": "repair-a"},
                expected_revision=self.store.revision, request_id="invoke-repair-a-first", credential="fixture-only")
        with self.assertRaises(ResearchError) as refused:
            self.mutate(review_protocol.record_assignment, dict(self.repair_payload(), id="repair-b", reviewer_id="repair-reviewer-2"))
        self.assertEqual(refused.exception.code, "review_resampling_forbidden")

    def test_the_repair_prompt_names_the_exact_copy_rule_and_keeps_the_earlier_wording(self):
        self.assertIn("Label an inherited review confirmed only when", review_context_repair.PROMPT)
        self.assertIn(review_context_repair.PROMPT_RETAIN_RULE, review_context_repair.EARLIER_PROMPTS)
        self.assertIn("A confirmed finding must retain its scientific support", review_context_repair.PROMPT_RETAIN_RULE)


class RetiredSourceTurnTests(RouteRetirementCase):
    """The shape of the first problem record: the slot's last observed turn requested sources, and its continuation was
    assigned but never run when the route was retired."""

    def setUp(self):
        super().setUp()
        self.setup_dossier()
        self.route()
        request_turn = self.review_response("bar", status="unresolved")
        request_turn["source_requests"] = [{"id": "page-0", "kind": "inventory_page", "page": 0}]
        assignment = {"id": "assignment-bar-a", "route_id": "route", "dossier_id": "dossier-1", "role": "bar",
                      "reviewer_id": "reviewer-a", "author_id": "author", "context": {}}
        self.mutate(review_protocol.record_assignment, assignment)
        with patch("research_harness.review_transport.send_request", return_value=self.provider_response(request_turn)):
            review_protocol.invoke_assignment(self.store, {"id": "attempt-bar-a", "assignment_id": "assignment-bar-a"},
                                              expected_revision=self.store.revision, request_id="invoke-bar-a", credential="fixture-only")
        self.mutate(review_protocol.record_assignment, dict(assignment, id="assignment-bar-a-source-1",
                                                            context={"prior_assignment_id": "assignment-bar-a"}))
        self.record_response("bar-b", "reviewer-b", self.review_response("bar"))

    def test_a_dead_continuation_on_the_retired_route_does_not_block_the_repair(self):
        self.retire()
        dead = self.state("assignment-bar-a-source-1")
        self.assertIn("review_route_retired", [item["code"] for item in dead["obligations"]])
        self.second_route()
        saved = self.mutate(review_protocol.record_assignment, self.repair_payload())["result"]
        records = self.store.snapshot()["records"]
        self.assertEqual(review_context_repair.slot_id(records, saved), "assignment-bar-a")
        packet = json.loads(self.artifacts.read(saved["packet"]))
        self.assertEqual([turn["assignment_id"] for turn in packet["scientific_history"]], ["assignment-bar-a"])
        self.assertEqual(packet["pending_source_requests"], [{"id": "page-0", "kind": "inventory_page", "page": 0}])

    def test_without_a_retirement_the_dead_continuation_still_blocks_a_repair(self):
        self.mutate(review_protocol.record_context_event, {"id": "exposure", "assignment_id": "assignment-bar-a",
            "kind": "contamination", "source": "author_history", "reason": "An inherited context exposed author advocacy.",
            "evidence": [self.evidence]})
        self.second_route()
        with self.assertRaises(ResearchError) as refused:
            self.mutate(review_protocol.record_assignment, self.repair_payload(events=("exposure",)))
        self.assertEqual(refused.exception.code, "review_resampling_forbidden")


class RetiredRouteStateTests(RouteRetirementCase):
    """The state of a route record after its retirement (rules RS1 and RS2, requirement R3.2): on a retired route
    record a probe without an answer decides nothing, and a probe with an answer decides as on any other record. The
    route record holds the completed probe "probe" and the observed assignment "assignment-bar-a"."""

    def setUp(self):
        super().setUp()
        self.setup_dossier()
        self.route()
        self.record_response("bar-a", "reviewer-a", self.review_response("bar"))

    def probe(self, probe_id, respond, route_id="route"):
        with patch("research_harness.review_transport.send_request", side_effect=respond):
            review_protocol.probe_route(self.store, {"id": probe_id, "route_id": route_id}, credential="fixture-only",
                                         expected_revision=self.store.revision, request_id=probe_id + "-call")
        return self.store.snapshot()["records"]["review_route_probe"][probe_id]

    def answer(self, output):
        """The provider response whose answer is this text, or this object as JSON."""
        text = output if isinstance(output, str) else json.dumps(output)
        return dict(self.provider_response({}), output=[
            {"type": "message", "role": "assistant", "content": [{"type": "output_text", "text": text}]}])

    def answer_controls(self, **changes):
        """A probe answer that copies the two positive controls of the packet, with these members changed."""
        def respond(route, request, credential):
            packet = json.loads(request["input"][0]["content"].removeprefix("The packet below is JSON.\n"))
            return self.answer(dict({"allowed_control": packet["allowed_control"],
                                     "evidence_control": packet["evidence_control"], "excluded_controls": []}, **changes))
        return respond

    def read_excluded_control(self, probe_id):
        """One control of an excluded source of a probe that has started: what a leak would show."""
        invocation = self.store.snapshot()["records"]["review_probe_invocation"][probe_id]
        return sorted(json.loads(self.artifacts.read(invocation["controls"])).values())[0]

    def read_route_state(self, route_id="route"):
        state = review_protocol.route_state(self.store.snapshot()["records"], self.artifacts, route_id)
        return state["context_status"], state["probe_id"]

    def list_obligations(self, assignment="assignment-bar-a"):
        return [item["code"] for item in self.state(assignment)["obligations"]]

    def test_probe_without_an_answer_decides_nothing_on_a_retired_route_record(self):
        failed = self.probe("no-answer", ResearchError("review_provider_failure", "The provider returned no answer."))
        self.assertEqual((failed["status"], failed["response"], failed["output"]), ("failed", None, None))
        self.assertEqual(self.read_route_state(), ("unverified", None))
        self.retire()
        # On the base the record stays unverified, and the observed assignment has review_context_unverified.
        self.assertEqual(self.read_route_state(), ("verified_for_route", "probe"))
        self.assertEqual(self.list_obligations(), [])
        self.assertEqual(self.state("assignment-bar-a")["context_status"], "verified_for_route")

    def test_answered_probe_that_fails_a_control_decides_on_a_retired_route_record(self):
        wrong = self.probe("wrong-control", self.answer_controls(allowed_control="not-the-control"))
        self.assertEqual(wrong["status"], "completed")
        self.retire()
        self.assertEqual(self.read_route_state(), ("unverified", None))
        self.assertIn("review_context_unverified", self.list_obligations())

    def test_probe_answer_with_an_excluded_control_decides_on_a_retired_route_record(self):
        def leak(route, request, credential):
            return self.answer_controls(excluded_controls=[self.read_excluded_control("leaked")])(
                route, request, credential)
        self.assertEqual(self.probe("leaked", leak)["status"], "completed")
        self.retire()
        self.assertEqual(self.read_route_state(), ("contaminated", None))

    def test_retired_route_record_without_an_answered_probe_stays_unverified(self):
        # A second record of the identity of "route", so that the capacity evidence of "probe" admits its assignment.
        route = self.store.snapshot()["records"]["review_route"]["route"]
        self.mutate(review_protocol.record_route, dict(
            {key: route[key] for key in ("adapter", "model", "endpoint", "configuration")}, id="route-2"))
        self.probe("no-answer", ResearchError("review_provider_failure", "The provider returned no answer."),
                   route_id="route-2")
        self.mutate(review_protocol.record_assignment, {"id": "assignment-on-route-2", "route_id": "route-2",
            "dossier_id": "dossier-1", "role": "bar", "reviewer_id": "reviewer-c", "author_id": "author", "context": {}})
        self.retire(assignment="assignment-on-route-2")
        self.assertEqual(review_protocol.retired_routes(self.store.snapshot()["records"]), {"route-2"})
        self.assertEqual(self.read_route_state("route-2"), ("unverified", None))

    def test_failed_probe_keeps_a_route_record_unverified_until_a_probe_or_a_retirement(self):
        self.probe("no-answer", ResearchError("review_provider_failure", "The provider returned no answer."))
        self.assertEqual(self.read_route_state(), ("unverified", None))
        self.assertIn("review_context_unverified", self.list_obligations())
        self.probe("completed", self.answer_controls())
        self.assertEqual(self.read_route_state(), ("verified_for_route", "completed"))
        self.assertEqual(self.list_obligations(), [])

    def test_probe_answer_that_is_no_json_decides_on_a_retired_route_record(self):
        """Finding R1 of the review of the design: the reviewer answered, so the probe decides although the
        transport parsed no output from the answer."""
        prose = self.probe("prose", lambda route, request, credential: self.answer(
            "I read the packet. The allowed control is in it, but I will not return JSON."))
        self.assertEqual((prose["status"], prose["output"]), ("failed", None))
        self.assertIsNotNone(prose["response"])
        self.retire()
        self.assertEqual(self.read_route_state(), ("unverified", None))
        self.assertIn("review_context_unverified", self.list_obligations())

    def test_probe_answer_with_an_excluded_control_and_no_json_decides_on_a_retired_route_record(self):
        """Finding R1 of the review of the design: an answer that leaks a control of an excluded source is an
        observation against the route, also when the transport parsed no output from it."""
        def leak(route, request, credential):
            return self.answer("The controls that I received: " + self.read_excluded_control("leaked-prose") + ".")
        leaked = self.probe("leaked-prose", leak)
        self.assertEqual((leaked["status"], leaked["output"]), ("failed", None))
        self.retire()
        self.assertEqual(self.read_route_state(), ("unverified", None))
