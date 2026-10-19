"""Problem reviewers can resolve an objection independently of an unused adjudication."""

import json
from unittest.mock import patch

import test_research_problem_delivery as delivery_fixtures
import test_research_problem_objections as objection_fixtures

from research_harness import research_decisions as decisions, review_protocol as protocol
from research_harness.evidence import digest


class ProblemAdjudicationResolutionTests(delivery_fixtures.ProblemDeliveryCase):
    make_objection = objection_fixtures.ProblemObjectionTests.make_objection
    make_finding = objection_fixtures.ProblemObjectionTests.make_finding
    prepare_problem_evidence = objection_fixtures.ProblemObjectionTests.prepare_problem_evidence
    prepare_reviews = objection_fixtures.ProblemObjectionTests.prepare_reviews
    observe_adjudication = objection_fixtures.ProblemObjectionTests.observe_adjudication
    assert_delivered = objection_fixtures.ProblemObjectionTests.assert_delivered
    assert_blocked_delivery = objection_fixtures.ProblemObjectionTests.assert_blocked_delivery
    refresh_reviews = objection_fixtures.ProblemObjectionTests.refresh_reviews
    build_slate_output = objection_fixtures.ProblemObjectionTests.build_slate_output

    def prepare_bar_adjudication(self):
        self.prepare_reviews(bar_reviewer="second")
        changed = self.build_value([self.item], identifier="value-2")
        changed["motivation"][0]["text"] = "The supplied comparator supports the exact stated implication."
        self.mutate(self.load_problem_module().record_value, changed)
        return self.observe_adjudication(review_ids=["review-bar-first", "review-bar-second"])

    def observe_later_resolutions(self, first_status="resolved", *, new_continuing_evidence=False):
        """Later results resolve with new evidence. A continuing finding repeats the adjudicated version unless it
        carries the new evidence, which makes it a new version."""
        self.resolution_evidence = self.artifacts.put(
            b"The exact implication follows from the comparator's stated hypotheses.", "text/plain")
        for reviewer in ("first", "second"):
            output = self.build_review_output("result")
            finding = self.make_finding(first_status if reviewer == "first" else "resolved")
            if finding["status"] == "resolved" or new_continuing_evidence:
                finding["evidence"] = [self.resolution_evidence]
            output["value"]["objection_findings"] = [finding]
            self.observe("result", reviewer, output, identifier="resolved-" + reviewer)

    def remove_adjudication_response(self):
        response = self.store.snapshot()["records"]["review_attempt"]["focused-attempt"]["response"]
        (self.root / response["path"]).unlink()

    def test_both_evidenced_result_resolutions_allow_post_without_adjudication_response(self):
        adjudication = self.prepare_bar_adjudication()
        self.observe_later_resolutions()
        self.remove_adjudication_response()
        records = self.store.snapshot()["records"]
        self.assertEqual(records["review_adjudication"][adjudication["id"]], adjudication)
        focused = protocol.adjudication_state(records, self.artifacts, adjudication["id"])
        self.assertFalse(focused["ready"])
        self.assertIn("review_output_unavailable", {item["code"] for item in focused["obligations"]})
        self.assert_delivered()

    def test_unavailable_adjudication_and_continuing_result_block_post(self):
        self.prepare_bar_adjudication()
        self.observe_later_resolutions(first_status="continuing")
        self.remove_adjudication_response()
        assessment = decisions.assess_problem_reviews(self.store.snapshot()["records"], self.artifacts)
        self.assertIn("review_output_unavailable", {item["code"] for item in assessment["obligations"]})
        self.assert_blocked_delivery()

    def test_available_adjudication_resolves_a_continuing_result(self):
        self.prepare_bar_adjudication()
        self.observe_later_resolutions(first_status="continuing")
        self.assert_delivered()

    def test_a_continuing_finding_with_new_evidence_is_a_version_the_adjudication_did_not_dispose_of(self):
        self.prepare_bar_adjudication()
        self.observe_later_resolutions(first_status="continuing", new_continuing_evidence=True)
        records = self.store.snapshot()["records"]
        [objection] = decisions.collect_problem_post_objections(records, self.artifacts, [records["value_review"][identifier]
            for identifier in ("review-bar-first", "review-bar-second", "review-resolved-first", "review-resolved-second")])
        self.assertEqual((objection["review_id"], objection["evidence"], objection["claim"]),
                         ("review-resolved-first", [self.resolution_evidence], self.make_objection()["claim"]))
        self.assert_blocked_delivery()

    def test_missing_resolution_evidence_and_unavailable_adjudication_block_post(self):
        self.prepare_bar_adjudication()
        self.observe_later_resolutions()
        self.remove_adjudication_response()
        (self.root / self.resolution_evidence["path"]).unlink()
        assessment = decisions.assess_problem_reviews(self.store.snapshot()["records"], self.artifacts)
        self.assertTrue({"artifact_missing", "review_output_unavailable", "research_objection_unresolved"}
                        <= {item["code"] for item in assessment["obligations"]})
        self.assert_blocked_delivery("artifact_missing")

    def cite_candidate(self, identifier):
        candidate = self.store.snapshot()["records"]["problem_candidate"][identifier]
        return {"kind": "record", "record_kind": "problem_candidate", "id": identifier, "digest": digest(candidate)}

    def contest_with_a_candidate_record(self, late_candidate=False):
        """Astra's F1 reproduction: O1 is resolved against candidate-1; candidate-2 holds a counterexample in its
        observation; the refreshed slate review contests O1 citing candidate-2's exact record reference; the slate
        selects candidate-1 and defers candidate-2. With late_candidate, candidate-3 is recorded after the slate
        review's packet and is cited too, so no packet of that review carried it."""
        self.prepare_reviews(slate_reviewer="first")
        self.refresh_reviews("resolved")
        self.assertTrue(decisions.assess_problem_reviews(self.store.snapshot()["records"], self.artifacts)["ready"])
        self.counterexample_text = "The sequence 1, 3, 2 violates the stated bound at n = 3."
        self.record_candidate("candidate-2", statement="Does the bound hold for odd $n$?", observation=self.counterexample_text)
        self.cited = self.cite_candidate("candidate-2")
        assignment = self.assign("slate", "first", identifier="slate-first-refreshed")
        deferred = [{"candidate_id": "candidate-2", "evidence_needed": "A documented active line."}]
        evidence = [self.cited]
        if late_candidate:
            self.record_candidate("candidate-3", statement="Does the bound hold for prime $n$?")
            evidence.append(self.cite_candidate("candidate-3"))
            deferred.append({"candidate_id": "candidate-3", "evidence_needed": "A slate review of this candidate."})
        output = self.build_slate_output(["candidate-1", "candidate-2"])
        output["value"]["objection_findings"] = [dict(self.make_finding("contested"), evidence=evidence,
            reason="The candidate-2 observation is a counterexample to the resolution.")]
        with patch("research_harness.review_transport.send_request", return_value=self.build_provider_response(output)):
            self.mutate(protocol.invoke_assignment, {"id": "attempt-" + assignment["id"], "assignment_id": assignment["id"]},
                        credential="test-only")
        review = self.mutate(decisions.record_value_review, dict(output, id="review-slate-first-refreshed",
                                                                 dossier_id=None, assignment_id=assignment["id"]))["result"]
        # A review that did not assess candidate-3 cannot support the slate, but it still binds the post.
        self.record_slate("slate-2", deferred=deferred, review_ids=[] if late_candidate else [review["id"]])
        self.assert_blocked_delivery()
        records = self.store.snapshot()["records"]
        current = decisions.collect_problem_post_objections(records, self.artifacts, [records["value_review"][identifier]
            for identifier in ("review-bar-first", "review-bar-second", "review-refreshed-first", "review-refreshed-second")])
        [self.version] = [objection for objection in current if objection["id"] == "unsupported-implication"]
        self.assertEqual(self.version["review_id"], review["id"])

    def read_packet(self, identifier):
        return json.loads(self.artifacts.read(self.store.snapshot()["records"]["review_assignment"][identifier]["packet"]))

    def test_the_adjudicator_receives_a_cited_record_with_its_content(self):
        self.contest_with_a_candidate_record()
        self.observe_adjudication(review_ids=["review-bar-first", "review-bar-second"])
        records = self.store.snapshot()["records"]
        [entry] = self.read_packet("focused")["version_evidence"]
        self.assertEqual({key: entry[key] for key in self.cited}, self.cited)
        # R42: the record is the slate packet's projection, without the author's notes and disposition.
        [projected] = [row for row in self.read_packet("slate-first-refreshed")["candidates"] if row["id"] == "candidate-2"]
        self.assertEqual((entry["record"], entry["artifacts"]), (projected, []))
        self.assertEqual(entry["record"]["observation"], self.counterexample_text)
        self.assertEqual({"notes", "disposition"} & set(entry["record"]), set())
        self.assertEqual(digest(records["problem_candidate"]["candidate-2"]), entry["digest"])
        self.assertEqual(decisions.find_undelivered_adjudication_keys(records, self.artifacts, "focused",
            version_evidence=protocol.describe_version_evidence(records, self.artifacts, self.version)), [])
        self.assert_delivered()

    def test_an_adjudication_without_the_cited_record_is_incomplete_and_refreshes(self):
        self.contest_with_a_candidate_record()
        real = protocol.add_focused_adjudication

        def build_before_records_were_delivered(*arguments, **options):
            real(*arguments, **options)
            packet = arguments[2]
            packet["version_evidence"] = [entry for entry in packet["version_evidence"] if entry.get("kind") != "record"]
        with patch.object(protocol, "add_focused_adjudication", side_effect=build_before_records_were_delivered):
            saved = self.observe_adjudication(review_ids=["review-bar-first", "review-bar-second"])
        self.assertEqual((saved["disposition"], self.read_packet("focused")["version_evidence"]), ("not_upheld", []))
        records = self.store.snapshot()["records"]
        self.assertEqual(decisions.find_undelivered_adjudication_keys(records, self.artifacts, "focused",
            version_evidence=protocol.describe_version_evidence(records, self.artifacts, self.version)),
            ["version_evidence:problem_candidate:candidate-2"])
        self.assert_blocked_delivery()
        self.observe_adjudication(review_ids=["review-bar-first", "review-bar-second"], identifier="focused-complete")
        refresh = self.read_packet("focused-complete")["focused_refresh"]
        self.assertEqual((refresh["assignment_id"], refresh["reason"], refresh["undelivered_keys"]),
                         ("focused", "packet_incomplete", ["version_evidence:problem_candidate:candidate-2"]))
        self.assert_delivered()

    def test_a_cited_record_that_no_packet_of_the_stating_review_carried_is_undelivered(self):
        """R42: candidate-3 is in the store at the cited digest, but the stating slate review never received it."""
        self.contest_with_a_candidate_record(late_candidate=True)
        records = self.store.snapshot()["records"]
        self.assertNotIn("candidate-3", {row["id"] for row in self.read_packet("slate-first-refreshed")["candidates"]})
        self.observe_adjudication(review_ids=["review-bar-first", "review-bar-second"])
        records = self.store.snapshot()["records"]
        delivered, undelivered = self.read_packet("focused")["version_evidence"]
        self.assertEqual((delivered["kind"], delivered["id"]), ("record", "candidate-2"))
        self.assertEqual((undelivered["kind"], undelivered["identity"]), ("undelivered", "problem_candidate:candidate-3"))
        self.assertEqual(decisions.find_undelivered_adjudication_keys(records, self.artifacts, "focused",
            version_evidence=protocol.describe_version_evidence(records, self.artifacts, self.version)),
            ["version_evidence:problem_candidate:candidate-3"])
        self.assert_blocked_delivery()
