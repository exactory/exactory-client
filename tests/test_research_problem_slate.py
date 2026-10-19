"""The slate gives every current candidate one disposition and binds the selection to two blind bars."""

import copy
import json
from unittest.mock import patch

from research_harness import problem_candidates, research_decisions as decisions, review_protocol as protocol
from research_harness.cli import status_report
from research_harness.errors import ResearchError
from research_harness.evidence import digest
from research_harness.gates import gate_state
from research_harness.report_views import next_summary, status_summary
from test_research_problem_candidates import build_slate
from test_research_problem_delivery import ProblemDeliveryCase


class ProblemSlateTests(ProblemDeliveryCase):
    def setUp(self):
        super().setUp()
        self.initialize_problem()

    def read_records(self):
        return self.store.snapshot()["records"]

    def collect_gate_codes(self):
        return [row["code"] for row in gate_state(self.read_records(), self.artifacts, "post")["obligations"]]

    def build_slate_output(self, candidate_ids):
        output = self.build_review_output("slate")
        for check in output["value"]["assurances"]:
            check["status"] = "not_assessed"
        output["value"]["rubric_potential"] = {identifier: {"significance": {"B1": 2, "B2": 1}, "priority": 2}
                                               for identifier in candidate_ids}
        return output

    def test_slate_needs_two_ready_bars(self):
        self.record_candidate()
        self.assert_error("problem_bar_pending", lambda: self.record_slate())
        self.supporting = self.artifacts.put(b"A captured field standard.", "text/plain")
        self.prepare_review_route()
        self.observe("bar", "first")
        for bar_review_ids in (["review-bar-first", "review-bar-second"], ["review-bar-first", "review-bar-first"],
                               ["review-bar-first"], "review-bar-first"):
            with self.subTest(bar_review_ids=bar_review_ids):
                with self.assertRaises(ResearchError) as caught:
                    self.record_slate(bar_review_ids=bar_review_ids)
                self.assertEqual((caught.exception.code, caught.exception.details),
                                 ("problem_bar_pending", {"bar_review_ids": bar_review_ids}))
        self.observe("bar", "second")
        self.mutate(protocol.record_context_event, {"id": "exposure", "assignment_id": "bar-second",
            "kind": "contamination", "source": "author_history", "reason": "The bar reviewer received author advocacy.",
            "evidence": [self.supporting]})
        self.assert_error("review_context_contaminated", lambda: self.record_slate())
        self.assertNotIn("problem_slate", self.read_records())

    def test_every_current_candidate_has_one_disposition(self):
        self.record_candidate()
        self.record_candidate("candidate-2", statement="Does the bound hold for odd $n$?")
        self.record_candidate("candidate-3", previous="candidate-2", statement=r"Does the bound hold for odd $n \geq 3$?")
        self.observe_bars()
        deferred = [{"candidate_id": "candidate-3", "evidence_needed": "A documented active line for odd lengths."}]
        cases = ((build_slate(), ["candidate-3"], []),
                 (build_slate(deferred=deferred, rejected=[{"candidate_id": "candidate-2", "reason": "Superseded."}]),
                  [], ["candidate-2"]),
                 (build_slate(deferred=deferred, rejected=[{"candidate_id": "candidate-1", "reason": "Also rejected."}]),
                  [], ["candidate-1"]),
                 (build_slate(selected=["candidate-1", "absent"], deferred=deferred), [], ["absent"]),
                 (build_slate(selected=["candidate-1", "candidate-1"], deferred=deferred), [], ["candidate-1"]))
        for payload, missing, extra in cases:
            with self.subTest(missing=missing, extra=extra):
                with self.assertRaises(ResearchError) as caught:
                    self.mutate(problem_candidates.record_slate, payload)
                self.assertEqual((caught.exception.code, caught.exception.details),
                                 ("problem_slate_incomplete", {"missing": missing, "extra": extra}))
        revision = self.store.revision
        saved = self.record_slate(deferred=deferred)
        self.assertEqual(saved, dict(build_slate(deferred=deferred), candidate_ids=["candidate-1", "candidate-3"],
                                     recorded_revision=revision + 1))
        self.assertEqual(problem_candidates.find_slate(self.read_records()), saved)

    def test_selected_candidate_needs_potential_at_threshold(self):
        self.record_candidate()
        self.record_candidate("candidate-low", potential={"significance": {"B1": 1, "B2": 1}, "priority": 3, "readiness": "pass"})
        self.record_candidate("candidate-unready", potential={"significance": {"B1": 0, "B2": 3}, "priority": 2})
        self.observe_bars()
        rest = [{"candidate_id": "candidate-low", "evidence_needed": "A reduction that fixes its place in the line."},
                {"candidate_id": "candidate-unready", "evidence_needed": "Full readings of the decisive sources."}]
        cases = (("candidate-low", [{"item": "significance", "required": "max(B1, B2) >= 2", "graded": {"B1": 1, "B2": 1}}]),
                 ("candidate-unready", [{"item": "readiness", "required": "pass", "graded": None}]))
        for candidate_id, shortfalls in cases:
            with self.subTest(candidate_id=candidate_id):
                payload = build_slate(selected=["candidate-1", candidate_id],
                                      deferred=[row for row in rest if row["candidate_id"] != candidate_id])
                with self.assertRaises(ResearchError) as caught:
                    self.mutate(problem_candidates.record_slate, payload)
                self.assertEqual((caught.exception.code, caught.exception.details),
                                 ("problem_slate_candidate_below_threshold", {"candidate_id": candidate_id, "shortfalls": shortfalls}))
        self.assertEqual(self.record_slate(deferred=rest)["selected"], ["candidate-1"])

    def test_empty_selection_needs_a_reason_and_reports_no_post(self):
        self.record_candidate()
        self.observe_bars()
        rejected = [{"candidate_id": "candidate-1", "reason": "A parameter increment of a settled case."}]
        for reason in ("", "   ", None):
            with self.subTest(reason=reason):
                self.assert_error("problem_slate_reason_missing",
                                  lambda: self.record_slate(selected=[], rejected=rejected, reason=reason))
        self.assert_error("invalid_input", lambda: self.record_slate(reason=None))
        reason = "No candidate reaches potential level 2; the exploration ends without a post."
        saved = self.record_slate(selected=[], rejected=rejected, reason=reason)
        self.assertEqual(saved["selected"], [])
        report = status_report(self.store)
        self.assertFalse(report["ready"])
        self.assertEqual(report["milestone"], "slate")
        self.assertEqual(report["next"]["code"], "problem_no_post")
        self.assertEqual(report["next"]["reason"], reason)
        self.assertEqual(report["candidates"], {"current": 1, "selected": 0, "deferred": 0, "rejected": 1})
        self.assertEqual(report["slate_id"], "slate-1")
        self.select_statement(candidate_id="candidate-1")
        report = status_report(self.store)
        self.assertEqual((report["milestone"], report["next"]["code"]), ("slate", "problem_no_post"))
        summary = status_summary(report)
        self.assertEqual((summary["candidates"], summary["slate_id"]), (report["candidates"], "slate-1"))
        for view in (summary, next_summary(report)):
            self.assertEqual(view["next_hint"]["code"], "problem_no_post")

    def test_statement_candidate_must_be_selected_in_the_latest_slate(self):
        self.record_candidate()
        self.record_candidate("candidate-2", statement="Does the bound hold for odd $n$?")
        self.record_candidate("candidate-old", statement="An earlier formulation.")
        self.record_candidate("candidate-new", previous="candidate-old", statement="A revised formulation.")
        self.observe_bars()
        later = [{"candidate_id": "candidate-new", "evidence_needed": "A documented active line."}]
        self.record_slate(deferred=[{"candidate_id": "candidate-2", "evidence_needed": "A documented active line."}] + later)
        for candidate_id in ("absent", "candidate-old"):
            with self.subTest(candidate_id=candidate_id):
                self.assert_error("problem_candidate_missing", lambda: self.select_statement(candidate_id=candidate_id))
        self.assertEqual(self.select_statement()["candidate_id"], None)
        for identifier, candidate_id in (("statement-1", None), ("statement-2", "candidate-2")):
            if candidate_id is not None:
                self.select_statement(identifier=identifier, candidate_id=candidate_id)
            self.assertEqual(problem_candidates.assess_slate(self.read_records(), self.artifacts), [{
                "code": "problem_candidate_unselected", "statement_id": identifier, "candidate_id": candidate_id,
                "explanation": "Formulate a candidate that the latest slate selects."}])
        self.select_statement(identifier="statement-3", candidate_id="candidate-1")
        self.assertEqual(problem_candidates.assess_slate(self.read_records(), self.artifacts), [])
        self.assertNotIn("problem_candidate_unselected", self.collect_gate_codes())
        self.record_slate("slate-2", selected=["candidate-2"],
                          deferred=[{"candidate_id": "candidate-1", "evidence_needed": "The decisive readings."}] + later)
        self.assertIn("problem_candidate_unselected", self.collect_gate_codes())

    def test_gate_reports_slate_missing_before_any_slate(self):
        self.assertEqual(self.collect_gate_codes()[:2], ["problem_slate_missing", "problem_statement_missing"])
        self.record_candidate()
        self.select_statement(candidate_id="candidate-1")
        codes = self.collect_gate_codes()
        self.assertEqual(codes[0], "problem_slate_missing")
        self.assertNotIn("problem_candidate_unselected", codes)
        self.assert_error("problem_minimum_unmet", lambda: self.mutate(self.load_problem_module().record_decision, {
            "id": "decision", "action": "post", "review_ids": [], "bar_review_ids": [], "reason": "Ready."}))

    def test_milestones_and_next_order(self):
        report = status_report(self.store)
        self.assertEqual((report["milestone"], report["next"]["code"]), ("exploration", "problem_slate_missing"))
        self.assertEqual((report["candidates"], report["slate_id"]),
                         ({"current": 0, "selected": 0, "deferred": 0, "rejected": 0}, None))
        self.record_candidate()
        self.observe_bars()
        self.record_slate()
        report = status_report(self.store)
        self.assertEqual((report["milestone"], report["next"]["code"]), ("slate", "problem_statement_missing"))
        self.assertEqual(report["candidates"], {"current": 1, "selected": 1, "deferred": 0, "rejected": 0})
        self.select_statement(candidate_id="candidate-1")
        report = status_report(self.store)
        self.assertEqual((report["milestone"], report["next"]["code"]), ("formulation", "problem_attempt_missing"))

    def test_optional_slate_review_packet_and_response(self):
        self.record_candidate()
        self.record_candidate("candidate-2", statement="Does the bound hold for odd $n$?",
                              notes="PRIVATE_CANDIDATE_NOTE", source={"kind": "computation",
                              "reference": "Probe of the first twelve odd lengths.", "evidence": []})
        self.observe_bars()
        self.select_statement(candidate_id="candidate-1", title="PRIVATE_TITLE")
        payload = self.observe("slate", "first", self.build_slate_output(["candidate-1"]), record=False)
        records = self.read_records()
        assignment = records["review_assignment"][payload["assignment_id"]]
        packet = json.loads(self.artifacts.read(assignment["packet"]))
        self.assertEqual(assignment["prompt"]["template_id"], "exactory-problem-review-v2/slate")
        self.assertEqual(assignment["subject"], {"kind": "problem", "statement_id": None})
        self.assertEqual(assignment["history_assignment_id"], "bar-first")
        self.assertEqual((packet["protocol"], packet["subject"]), ("exactory-problem-review-v2", assignment["subject"]))
        self.assertEqual(sorted(packet["bar_ids"]), ["review-bar-first", "review-bar-second"])
        self.assertEqual(sorted(bar["id"] for bar in packet["bars"]), sorted(packet["bar_ids"]))
        self.assertEqual(packet["candidates"], [{"id": identifier, "digest": digest(row), "statement": row["statement"],
            "source": {"kind": row["source"]["kind"], "reference": row["source"]["reference"]},
            "observation": row["observation"], "neighbours": row["neighbours"], "potential": row["potential"]}
            for identifier, row in sorted(records["problem_candidate"].items())])
        for withheld in ("PRIVATE_CANDIDATE_NOTE", "PRIVATE_TITLE", "disposition"):
            self.assertNotIn(withheld, json.dumps(packet))
        self.assert_error("invalid_value_review", lambda: self.mutate(decisions.record_value_review, payload))
        marker = records["workspace"]["problem"]
        complete = self.build_slate_output(["candidate-1", "candidate-2"])
        assured = copy.deepcopy(complete)
        assured["value"]["assurances"][0]["status"] = "passed"
        ready = copy.deepcopy(complete)
        ready["value"]["rubric_potential"]["candidate-1"]["readiness"] = "pass"
        excessive = copy.deepcopy(complete)
        excessive["value"]["rubric_potential"]["candidate-2"]["priority"] = 4
        other_bars = dict(packet, bar_ids=["review-bar-first", "review-bar-other"])
        for name, output, supplied in (("assured", assured, packet), ("readiness", ready, packet),
                                       ("level", excessive, packet), ("bars", complete, other_bars)):
            with self.subTest(output=name):
                self.assert_error("invalid_value_review", lambda: decisions._validate_value(
                    records, self.artifacts, output["value"], "slate", None, expected_checks=decisions.PROBLEM_CHECKS,
                    problem=marker, slate_packet=supplied))
        review = self.observe("slate", "second", complete)
        self.assertEqual(review["subject"], {"kind": "problem", "statement_id": None})
        deferred = [{"candidate_id": "candidate-2", "evidence_needed": "A documented active line."}]
        for review_ids in (["review-slate-first"], [review["id"], review["id"]], ["review-bar-first"]):
            with self.subTest(review_ids=review_ids):
                code = "invalid_input" if len(review_ids) != len(set(review_ids)) else "problem_slate_review_unverified"
                self.assert_error(code, lambda: self.record_slate(deferred=deferred, review_ids=review_ids))
        saved = self.record_slate(deferred=deferred, review_ids=[review["id"]])
        self.assertEqual(saved["review_ids"], ["review-slate-second"])
        self.record_candidate("candidate-3", statement="A third formulation found after the review.")
        with self.assertRaises(ResearchError) as caught:
            self.record_slate("slate-2", deferred=[{"candidate_id": identifier, "evidence_needed": "A documented active line."}
                                                    for identifier in ("candidate-2", "candidate-3")], review_ids=[review["id"]])
        self.assertEqual((caught.exception.code, caught.exception.details),
                         ("problem_slate_review_unverified", {"review_id": "review-slate-second"}))

    def test_slate_review_source_request_continues_with_the_same_bars_and_candidates(self):
        self.record_candidate()
        self.observe_bars()
        request = self.build_slate_output(["candidate-1"])
        request["value"]["status"] = "unresolved"
        request["source_requests"] = [{"id": "page", "kind": "inventory_page", "page": 0}]
        self.observe("slate", "first", request, record=False)
        continued = self.assign("slate", "first", identifier="slate-first-continued", prior_assignment_id="slate-first")
        first = json.loads(self.artifacts.read(self.read_records()["review_assignment"]["slate-first"]["packet"]))
        packet = json.loads(self.artifacts.read(continued["packet"]))
        self.assertEqual((packet["bar_ids"], packet["candidates"]), (first["bar_ids"], first["candidates"]))
        self.assertEqual((packet["subject"], packet["pending_source_requests"]), (first["subject"], []))
        final = self.build_slate_output(["candidate-1"])
        with patch("research_harness.review_transport.send_request", return_value=self.build_provider_response(final)):
            self.mutate(protocol.invoke_assignment, {"id": "attempt-continued", "assignment_id": continued["id"]},
                        credential="test-only")
        review = self.mutate(decisions.record_value_review,
                             dict(final, id="review-slate-first", dossier_id=None, assignment_id=continued["id"]))["result"]
        self.assertEqual(self.record_slate(review_ids=[review["id"]])["review_ids"], ["review-slate-first"])

    def test_slate_review_context_repair_keeps_the_bars_and_candidates(self):
        original, review = self.prepare_repaired_slate_review()
        self.assertEqual(self.record_slate(review_ids=[review["id"]])["review_ids"], ["review-repaired-slate"])

    def test_a_reviewer_installed_by_a_slate_repair_refreshes_the_slate(self):
        original, review = self.prepare_repaired_slate_review()
        self.record_candidate("candidate-2", statement="Does the bound hold for odd $n$?")
        refreshed = self.assign("slate", "repaired-first", identifier="repaired-slate-refresh")
        packet = json.loads(self.artifacts.read(refreshed["packet"]))
        repaired = self.read_records()["review_assignment"]["repaired-slate"]
        self.assertEqual((refreshed["context"], refreshed["history_assignment_id"], refreshed["slot_id"]),
                         ({"prior_assignment_id": "repaired-slate"}, "repaired-slate", repaired["slot_id"]))
        self.assertEqual([candidate["id"] for candidate in packet["candidates"]], ["candidate-1", "candidate-2"])
        self.assertEqual([bar["id"] for bar in packet["own_bars"]], ["review-bar-first"])
        self.assertEqual(packet["problem_refresh"]["review_ids"], [original["id"], review["id"]])
        output = self.build_slate_output(["candidate-1", "candidate-2"])
        output["reassessment"] = {"assignment_id": "slate-first", "findings": [{"review_id": original["id"],
            "disposition": "revised", "reason": "The added candidate changes the comparison.", "evidence": [self.supporting]}]}
        with patch("research_harness.review_transport.send_request", return_value=self.build_provider_response(output)):
            self.mutate(protocol.invoke_assignment, {"id": "attempt-repaired-slate-refresh", "assignment_id": refreshed["id"]},
                        credential="test-only")
        saved = self.mutate(decisions.record_value_review,
                            dict(output, id="review-repaired-slate-refresh", dossier_id=None, assignment_id=refreshed["id"]))["result"]
        deferred = [{"candidate_id": "candidate-2", "evidence_needed": "A documented active line."}]
        self.assertEqual(self.record_slate(deferred=deferred, review_ids=[saved["id"]])["review_ids"], [saved["id"]])
        records = self.read_records()
        self.assertEqual(records["review_assignment"]["repaired-slate"]["context"]["context_repair"]["assignment_id"], "slate-first")
        self.assertLessEqual({"review-slate-first", "review-repaired-slate", "review-repaired-slate-refresh"},
                             set(records["value_review"]))

    def prepare_repaired_slate_review(self):
        """A slate review by `first`, contaminated, then repaired by the replacement reviewer `repaired-first`."""
        self.record_candidate()
        self.observe_bars()
        original = self.observe("slate", "first", self.build_slate_output(["candidate-1"]))
        self.mutate(protocol.record_context_event, {"id": "exposure", "assignment_id": "slate-first",
            "kind": "contamination", "source": "author_history", "reason": "The slate reviewer received author advocacy.",
            "evidence": [self.supporting]})
        self.assert_error("problem_slate_review_unverified", lambda: self.record_slate(review_ids=[original["id"]]))

        def respond_to_probe(route, request, credential):
            packet = json.loads(request["input"][0]["content"])
            return self.build_provider_response({"allowed_control": packet["allowed_control"],
                "evidence_control": packet["evidence_control"], "excluded_controls": []})
        with patch("research_harness.review_transport.send_request", side_effect=respond_to_probe):
            self.mutate(protocol.probe_route, {"id": "repair-probe", "route_id": "route"}, credential="test-only")
        repaired = self.assign("slate", "repaired-first", identifier="repaired-slate", context_repair={
            "assignment_id": "slate-first", "event_ids": ["exposure"], "probe_id": "repair-probe",
            "reason": "Reassess the slate in a verified independent context.", "evidence": [self.supporting]})
        first = json.loads(self.artifacts.read(self.read_records()["review_assignment"]["slate-first"]["packet"]))
        packet = json.loads(self.artifacts.read(repaired["packet"]))
        self.assertEqual((packet["bar_ids"], packet["candidates"]), (first["bar_ids"], first["candidates"]))
        output = self.build_slate_output(["candidate-1"])
        output["reassessment"] = {"assignment_id": "slate-first", "findings": [{"review_id": original["id"],
            "disposition": "revised", "reason": "The reassessed potential follows the supplied candidates.",
            "evidence": [self.supporting]}]}
        with patch("research_harness.review_transport.send_request", return_value=self.build_provider_response(output)):
            self.mutate(protocol.invoke_assignment, {"id": "attempt-repaired-slate", "assignment_id": repaired["id"]},
                        credential="test-only")
        review = self.mutate(decisions.record_value_review,
                             dict(output, id="review-repaired-slate", dossier_id=None, assignment_id=repaired["id"]))["result"]
        return original, review

    def test_changed_candidates_refresh_the_slate_reviewer_in_its_session(self):
        self.record_candidate()
        self.observe_bars()
        first = self.observe("slate", "first", self.build_slate_output(["candidate-1"]))
        self.assert_error("review_resampling_forbidden", lambda: self.assign("slate", "first", identifier="slate-first-again"))
        self.record_candidate("candidate-2", statement="Does the bound hold for odd $n$?")
        refreshed = self.observe("slate", "first", self.build_slate_output(["candidate-1", "candidate-2"]),
                                 identifier="slate-first-refreshed")
        records = self.read_records()
        original, assignment = (records["review_assignment"][identifier] for identifier in ("slate-first", "slate-first-refreshed"))
        self.assertEqual((assignment["context"], assignment["history_assignment_id"], assignment["slot_id"]),
                         ({"prior_assignment_id": "slate-first"}, "slate-first", original["slot_id"]))
        packet = json.loads(self.artifacts.read(assignment["packet"]))
        previous = json.loads(self.artifacts.read(original["packet"]))
        self.assertEqual([candidate["id"] for candidate in packet["candidates"]], ["candidate-1", "candidate-2"])
        self.assertEqual(packet["candidate_set_digest"], problem_candidates.compute_candidate_set_digest(records))
        self.assertEqual(packet["problem_refresh"], {"assignment_id": "slate-first",
            "previous_candidate_set_digest": previous["candidate_set_digest"],
            "candidate_set_digest": packet["candidate_set_digest"], "previous_bar_ids": previous["bar_ids"],
            "bar_ids": packet["bar_ids"], "review_ids": [first["id"]]})
        deferred = [{"candidate_id": "candidate-2", "evidence_needed": "A documented active line."}]
        self.assert_error("problem_slate_review_unverified", lambda: self.record_slate(deferred=deferred, review_ids=[first["id"]]))
        self.assertEqual(self.record_slate(deferred=deferred, review_ids=[refreshed["id"]])["review_ids"], [refreshed["id"]])
        self.assert_error("review_resampling_forbidden", lambda: self.assign("slate", "first", identifier="slate-first-third"))

    def test_slate_requires_version_two_bars_and_refreshes_version_one_bars(self):
        original = protocol.canonical_prompt

        def previous_prompt(role, **options):
            if options.get("orchestration") == "problem_creation":
                options["version"] = 1
            return original(role, **options)
        self.record_candidate()
        with patch.object(protocol, "canonical_prompt", side_effect=previous_prompt):
            self.observe_bars()
        with self.assertRaises(ResearchError) as caught:
            self.record_slate()
        self.assertEqual((caught.exception.code, caught.exception.details),
                         ("problem_bar_calibration_required", {"review_id": "review-bar-first"}))
        for reviewer in ("first", "second"):
            self.observe("bar", reviewer, identifier="bar-" + reviewer + "-v2")
        records = self.read_records()
        for reviewer in ("first", "second"):
            with self.subTest(reviewer=reviewer):
                earlier, assignment = (records["review_assignment"]["bar-" + reviewer + suffix] for suffix in ("", "-v2"))
                packet = json.loads(self.artifacts.read(assignment["packet"]))
                self.assertEqual((assignment["context"], assignment["history_assignment_id"], assignment["slot_id"]),
                                 ({"prior_assignment_id": earlier["id"]}, earlier["id"], earlier["slot_id"]))
                self.assertEqual(assignment["prompt"]["template_id"], "exactory-problem-review-v2/bar")
                self.assertEqual(packet["problem_refresh"], {"assignment_id": earlier["id"],
                    "previous_template_id": "exactory-problem-review-v1/bar", "template_id": "exactory-problem-review-v2/bar"})
        self.assert_error("review_resampling_forbidden", lambda: self.assign("bar", "first", identifier="bar-first-third"))
        bar_review_ids = ["review-bar-first-v2", "review-bar-second-v2"]
        self.assertEqual(self.record_slate(bar_review_ids=bar_review_ids)["bar_review_ids"], bar_review_ids)
        self.select_statement(candidate_id="candidate-1")
        result = self.assign("result", "first")
        packet = json.loads(self.artifacts.read(result["packet"]))
        self.assertEqual((result["history_assignment_id"], sorted(packet["bar_ids"])), ("bar-first-v2", bar_review_ids))

    def test_changed_bars_refresh_the_slate_reviewer_in_its_session(self):
        original = protocol.canonical_prompt

        def previous_prompt(role, **options):
            if options.get("orchestration") == "problem_creation":
                options["version"] = 1
            return original(role, **options)
        self.record_candidate()
        with patch.object(protocol, "canonical_prompt", side_effect=previous_prompt):
            self.observe_bars()
        stranded = self.observe("slate", "first", self.build_slate_output(["candidate-1"]))
        for reviewer in ("first", "second"):
            self.observe("bar", reviewer, identifier="bar-" + reviewer + "-v2")
        bar_review_ids = ["review-bar-first-v2", "review-bar-second-v2"]
        self.assert_error("problem_slate_review_unverified",
                          lambda: self.record_slate(bar_review_ids=bar_review_ids, review_ids=[stranded["id"]]))
        output = self.build_slate_output(["candidate-1"])
        output["value"]["bar_ids"] = bar_review_ids
        refreshed = self.observe("slate", "first", output, identifier="slate-first-refreshed")
        records = self.read_records()
        assignment = records["review_assignment"]["slate-first-refreshed"]
        packet = json.loads(self.artifacts.read(assignment["packet"]))
        self.assertEqual((assignment["context"], sorted(packet["bar_ids"])),
                         ({"prior_assignment_id": "slate-first"}, bar_review_ids))
        self.assertEqual((sorted(packet["problem_refresh"]["previous_bar_ids"]), packet["problem_refresh"]["bar_ids"]),
                         (["review-bar-first", "review-bar-second"], packet["bar_ids"]))
        self.assertEqual(self.record_slate(bar_review_ids=bar_review_ids, review_ids=[refreshed["id"]])["review_ids"],
                         [refreshed["id"]])

    def test_slate_objections_name_candidates_of_the_packet(self):
        self.record_candidate()
        self.record_candidate("candidate-2", statement="Does the bound hold for odd $n$?")
        self.observe_bars()
        objection = {"id": "restated", "claim": "Candidate 1 restates a known question.",
                     "reason": "The read survey states it.", "evidence": [self.supporting],
                     "resolution_condition": "Show what the candidate adds to the known question."}
        for reviewer, changes in (("first", {}), ("second", {"candidate_ids": ["candidate-3"]})):
            with self.subTest(reviewer=reviewer):
                output = self.build_slate_output(["candidate-1", "candidate-2"])
                output["objections"] = [dict(objection, **changes)]
                payload = self.observe("slate", reviewer, output, record=False)
                self.assert_error("invalid_value_review", lambda: self.mutate(decisions.record_value_review, payload))
        schema = json.dumps(protocol._PROBLEM_SLATE_SCHEMA["objections"][0])
        self.assertIn('"candidate_ids": []', schema)

    def test_a_bar_refresh_keeps_an_applicable_slate_objection(self):
        original = protocol.canonical_prompt

        def previous_prompt(role, **options):
            if options.get("orchestration") == "problem_creation":
                options["version"] = 1
            return original(role, **options)
        self.record_candidate()
        with patch.object(protocol, "canonical_prompt", side_effect=previous_prompt):
            self.observe_bars()
        output = self.build_slate_output(["candidate-1"])
        output["objections"] = [{"id": "restated", "claim": "Candidate 1 restates a known question.",
            "reason": "The read survey states it.", "evidence": [self.supporting],
            "resolution_condition": "Show what the candidate adds to the known question.", "candidate_ids": ["candidate-1"]}]
        self.observe("slate", "first", output)
        for reviewer in ("first", "second"):
            self.observe("bar", reviewer, identifier="bar-" + reviewer + "-v2")
        self.record_slate(bar_review_ids=["review-bar-first-v2", "review-bar-second-v2"])
        self.select_statement(candidate_id="candidate-1")
        packet = protocol.build_packet(self.read_records(), self.artifacts, None, "result", "first")
        self.assertEqual([row["id"] for row in packet["material_objections"]], ["restated"])
        self.assertEqual(sorted(packet["bar_ids"]), ["review-bar-first-v2", "review-bar-second-v2"])

    def test_a_slate_review_recorded_before_candidate_binding_is_ignored(self):
        self.record_candidate()
        self.observe_bars()
        output = self.build_slate_output(["candidate-1"])
        output["objections"] = [{"id": "restated", "claim": "The slate restates a known question.",
            "reason": "The read survey states it.", "evidence": [self.supporting],
            "resolution_condition": "Show what the slate adds to the known question.", "candidate_ids": []}]
        self.observe("slate", "first", output)
        self.select_statement(candidate_id="candidate-1")
        records = self.read_records()
        self.assertEqual(len(problem_candidates.collect_applicable_slate_objections(records, self.artifacts)), 1)
        without_ids = copy.deepcopy(records)
        del without_ids["value_review"]["review-slate-first"]["payload"]["objections"][0]["candidate_ids"]
        without_digest = copy.deepcopy(records)
        assignment = without_digest["review_assignment"]["slate-first"]
        packet = json.loads(self.artifacts.read(assignment["packet"]))
        del packet["candidate_set_digest"]
        assignment["packet"] = self.artifacts.put(json.dumps(packet).encode(), "application/json")
        for name, changed in (("candidate_ids", without_ids), ("candidate_set_digest", without_digest)):
            with self.subTest(missing=name):
                self.assertEqual(problem_candidates.collect_applicable_slate_objections(changed, self.artifacts), [])

    def test_a_stale_slate_counts_as_no_slate_for_next_and_the_milestone(self):
        self.record_candidate()
        self.observe_bars()
        self.record_slate(selected=[], rejected=[{"candidate_id": "candidate-1", "reason": "A settled case."}],
                          reason="No candidate reaches potential level 2.")
        self.assertEqual(status_report(self.store)["next"]["code"], "problem_no_post")
        self.record_candidate("candidate-2", statement="Does the bound hold for odd $n$?")
        report = status_report(self.store)
        self.assertEqual((report["milestone"], report["next"]["code"], report["slate_id"]),
                         ("exploration", "problem_slate_stale", "slate-1"))
        self.record_slate("slate-2", selected=["candidate-2"],
                          rejected=[{"candidate_id": "candidate-1", "reason": "A settled case."}])
        self.select_statement(candidate_id="candidate-2")
        self.assertEqual(status_report(self.store)["milestone"], "formulation")
        self.record_candidate("candidate-3", statement="A third formulation found later.")
        report = status_report(self.store)
        self.assertEqual((report["milestone"], report["next"]["code"]), ("exploration", "problem_slate_stale"))


class ProblemSlateCurrencyTests(ProblemDeliveryCase):
    def test_gate_reports_slate_stale_after_a_new_or_superseded_candidate(self):
        self.prepare_ready()
        self.record_post_decision()
        self.assertTrue(status_report(self.store)["ready"])
        explanation = "Record a new slate that disposes of every current candidate."
        self.record_candidate("candidate-2", statement="Does the bound hold for odd $n$?")
        report = status_report(self.store)
        expected = {"code": "problem_slate_stale", "explanation": explanation, "slate_id": "slate-1",
                    "missing": ["candidate-2"], "extra": []}
        self.assertEqual(([row for row in report["obligations"] if row["code"] == "problem_slate_stale"], report["next"]),
                         ([expected], expected))
        deferred = {"candidate_id": "candidate-2", "evidence_needed": "A documented active line for odd lengths."}
        self.record_slate("slate-2", deferred=[deferred])
        report = status_report(self.store)
        self.assertTrue(report["ready"], report["obligations"])
        self.record_candidate("candidate-1b", previous="candidate-1", statement=r"For every $n \geq 3$, does $B(n)\leq n^2$ hold?")
        report = status_report(self.store)
        expected = {"code": "problem_slate_stale", "explanation": explanation, "slate_id": "slate-2",
                    "missing": ["candidate-1b"], "extra": ["candidate-1"]}
        self.assertEqual(([row for row in report["obligations"] if row["code"] == "problem_slate_stale"], report["next"]),
                         ([expected], expected))
        self.record_slate("slate-3", selected=["candidate-1b"], deferred=[deferred])
        codes = {row["code"] for row in status_report(self.store)["obligations"]}
        self.assertNotIn("problem_slate_stale", codes)
        self.assertIn("problem_candidate_unselected", codes)


class ProblemResultCalibrationTests(ProblemDeliveryCase):
    """A result reviewer assesses calibrated bars only after they reach it."""

    version_two_bar_ids = ["review-bar-first-v2", "review-bar-second-v2"]

    def prepare_legacy_results(self):
        """Emulate a store of 0.53.0: version 1 bars and result reviews, recorded before the calibration refusal."""
        original = protocol.canonical_prompt

        def previous_prompt(role, **options):
            if options.get("orchestration") == "problem_creation":
                options["version"] = 1
            return original(role, **options)
        self.initialize_problem()
        self.record_candidate()
        self.selected = self.select_statement(candidate_id="candidate-1")
        item = self.record_full_reading()
        from research_harness.problem_attempt import record_attempt
        from test_research_problem_attempt import build_attempt
        from test_research_problem_assessment import build_assessment
        self.mutate(record_attempt, build_attempt(reading=item))
        with patch.object(protocol, "canonical_prompt", side_effect=previous_prompt):
            self.observe_bars()
        self.observe_openness()
        self.mutate(self.load_problem_module().record_openness, {"assignment_id": "openness-assignment"})
        self.mutate(self.load_problem_module().record_value, self.build_value([item]))
        self.mutate(self.load_problem_module().record_assessment, build_assessment(self.selected))
        with patch.object(protocol, "canonical_prompt", side_effect=previous_prompt), \
                patch.object(protocol, "require_calibrated_bars", return_value=None):
            for reviewer in ("first", "second"):
                output = self.build_review_output("result")
                del output["value"]["rubric"]
                del output["value"]["rubric_evidence"]
                self.observe("result", reviewer, output)
        self.mutate(self.load_problem_module().record_assessment, build_assessment(self.selected, identifier="assessment-2"))
        with self.assertRaises(ResearchError) as caught:
            self.assign("result", "first", identifier="uncalibrated")
        self.assertEqual((caught.exception.code, caught.exception.details),
                         ("problem_bar_calibration_required", {"review_id": "review-bar-first"}))
        for reviewer in ("first", "second"):
            self.observe("bar", reviewer, identifier="bar-" + reviewer + "-v2")
        self.record_slate(bar_review_ids=self.version_two_bar_ids)

    def build_calibrated_result(self):
        output = self.build_review_output("result")
        output["value"]["bar_ids"] = list(self.version_two_bar_ids)
        return output

    def test_a_refreshed_result_receives_the_calibrated_bars_and_the_gate_is_ready(self):
        self.prepare_legacy_results()
        for reviewer in ("first", "second"):
            self.observe("result", reviewer, self.build_calibrated_result(), identifier="refreshed-" + reviewer)
            assignment = self.store.snapshot()["records"]["review_assignment"]["refreshed-" + reviewer]
            packet = json.loads(self.artifacts.read(assignment["packet"]))
            self.assertEqual(assignment["context"], {"prior_assignment_id": "result-" + reviewer})
            self.assertEqual((sorted(packet["bar_ids"]), sorted(bar["id"] for bar in packet["bars"])),
                             (self.version_two_bar_ids, self.version_two_bar_ids))
            self.assertEqual([bar["id"] for bar in packet["own_bars"]], ["review-bar-" + reviewer + "-v2"])
        reviews = decisions.assess_problem_reviews(self.store.snapshot()["records"], self.artifacts)
        self.assertTrue(reviews["ready"], reviews["obligations"])
        self.mutate(self.load_problem_module().record_decision, {"id": "decision", "action": "post",
            "review_ids": reviews["review_ids"], "bar_review_ids": reviews["bar_review_ids"],
            "reason": "Both results assess the calibrated bars they received."})
        report = gate_state(self.store.snapshot()["records"], self.artifacts, "post")
        self.assertTrue(report["ready"], report["obligations"])

    def test_a_result_repair_after_a_bar_refresh_receives_the_calibrated_bars(self):
        self.prepare_legacy_results()
        self.mutate(protocol.record_context_event, {"id": "exposure", "assignment_id": "result-first",
            "kind": "contamination", "source": "author_history", "reason": "The result reviewer received author advocacy.",
            "evidence": [self.supporting]})

        def respond_to_probe(route, request, credential):
            supplied = json.loads(request["input"][0]["content"])
            return self.build_provider_response({"allowed_control": supplied["allowed_control"],
                "evidence_control": supplied["evidence_control"], "excluded_controls": []})
        with patch("research_harness.review_transport.send_request", side_effect=respond_to_probe):
            self.mutate(protocol.probe_route, {"id": "repair-probe", "route_id": "route"}, credential="test-only")
        repaired = self.assign("result", "repaired-first", identifier="repaired-result", context_repair={
            "assignment_id": "result-first", "event_ids": ["exposure"], "probe_id": "repair-probe",
            "reason": "Reassess the result in a verified independent context.", "evidence": [self.supporting]})
        packet = json.loads(self.artifacts.read(repaired["packet"]))
        self.assertEqual((sorted(packet["bar_ids"]), sorted(bar["id"] for bar in packet["bars"]),
                          [bar["id"] for bar in packet["own_bars"]]),
                         (self.version_two_bar_ids, self.version_two_bar_ids, ["review-bar-first-v2"]))
        output = self.build_calibrated_result()
        output["reassessment"] = {"assignment_id": "result-first", "findings": [{"review_id": "review-result-first",
            "disposition": "revised", "reason": "The calibrated bars change the grading scale.", "evidence": [self.supporting]}]}
        with patch("research_harness.review_transport.send_request", return_value=self.build_provider_response(output)):
            self.mutate(protocol.invoke_assignment, {"id": "repaired-attempt", "assignment_id": "repaired-result"},
                        credential="test-only")
        saved = self.mutate(decisions.record_value_review,
                            dict(output, id="review-repaired-result", dossier_id=None, assignment_id="repaired-result"))["result"]
        self.assertEqual(saved["payload"]["value"]["bar_ids"], self.version_two_bar_ids)

    def test_a_result_naming_bars_never_delivered_is_refused_and_blocks_the_gate(self):
        self.prepare_legacy_results()

        def build_bars_without_delivery(records, artifacts, packet, reviewer_id, slot=None):
            """The result continuation of the previous wave, which listed no bars."""
            return [review for review, _ in protocol._bars(records, artifacts, None, subject=packet["subject"])]
        with patch.object(protocol, "add_problem_bars", side_effect=build_bars_without_delivery):
            payload = self.observe("result", "second", self.build_calibrated_result(), record=False,
                                   identifier="refreshed-second")
        packet = json.loads(self.artifacts.read(self.store.snapshot()["records"]["review_assignment"]["refreshed-second"]["packet"]))
        self.assertNotIn("bars", packet)
        with self.assertRaises(ResearchError) as caught:
            self.mutate(decisions.record_value_review, payload)
        self.assertEqual((caught.exception.code, caught.exception.details),
                         ("review_bar_mismatch", {"bar_ids": self.version_two_bar_ids}))
        with patch.object(decisions, "find_undelivered_bar_ids", return_value=[]):
            self.mutate(decisions.record_value_review, payload)
        self.observe("result", "first", self.build_calibrated_result(), identifier="refreshed-first")
        reviews = decisions.assess_problem_reviews(self.store.snapshot()["records"], self.artifacts)
        self.assertIn({"code": "review_bar_mismatch", "explanation": "Each problem result must receive the bars it assesses.",
                       "review_id": "review-refreshed-second", "bar_ids": self.version_two_bar_ids}, reviews["obligations"])
        self.assertFalse(reviews["ready"])
