"""Problem reviewers can resolve an objection independently of an unused adjudication."""

import json
from unittest.mock import patch

import test_research_problem_delivery as delivery_fixtures
import test_research_problem_objections as objection_fixtures

from research_harness import research_decisions as decisions, review_protocol as protocol
from research_harness.errors import ResearchError
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
    cite_a_requested_source = objection_fixtures.ProblemObjectionTests.cite_a_requested_source
    replace_first_bar = objection_fixtures.ProblemObjectionTests.replace_first_bar

    def prepare_bar_adjudication(self):
        self.prepare_reviews(bar_reviewer="second")
        changed = self.build_value([self.item], identifier="value-2")
        changed["motivation"][0]["text"] = "The supplied comparator supports the exact stated implication."
        self.mutate(self.load_problem_module().record_value, changed)
        return self.observe_adjudication(review_ids=["review-bar-first", "review-bar-second"])

    def observe_later_resolutions(self, first_status="resolved", *, new_continuing_evidence=False):
        """Later results resolve with new evidence, which each reviewer requests as a source and receives (R51). A
        continuing finding repeats the adjudicated version unless it carries the new evidence, which makes it a new
        version."""
        def build_findings(status, delivered):
            finding = self.make_finding(status)
            if status == "resolved" or new_continuing_evidence:
                finding["evidence"] = [delivered]
            return [finding]
        for reviewer in ("first", "second"):
            status = first_status if reviewer == "first" else "resolved"
            self.resolution_evidence = self.cite_a_requested_source(reviewer, "resolved-" + reviewer,
                lambda delivered, status=status: build_findings(status, delivered))

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

    def test_missing_problem_evidence_raises_a_typed_error_during_adjudication_coverage(self):
        """Removing a stored assessment attachment must not leak FileNotFoundError."""
        self.prepare_reviews(result_reviewer="first")
        self.observe_adjudication(review_ids=["review-result-first", "review-result-second"])
        records = self.store.snapshot()["records"]
        self.assertIn(self.supporting, records["problem_assessment"]["assessment-1"]["items"]["priority"]["evidence"])
        [version] = decisions.collect_problem_post_objections(records, self.artifacts, [records["value_review"][identifier]
            for identifier in ("review-result-first", "review-result-second")])
        self.assertEqual(decisions.find_undelivered_adjudication_keys(records, self.artifacts, "focused",
            version_evidence=protocol.describe_version_evidence(records, self.artifacts, version)), [])
        (self.root / self.supporting["path"]).unlink()
        with self.assertRaises(ResearchError) as caught:
            decisions.find_undelivered_adjudication_keys(records, self.artifacts, "focused",
                version_evidence=protocol.describe_version_evidence(records, self.artifacts, version))
        self.assertEqual((caught.exception.code, caught.exception.details),
                         ("artifact_missing", {"path": self.supporting["path"]}))

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
        payload = dict(output, id="review-slate-first-refreshed", dossier_id=None, assignment_id=assignment["id"])
        if late_candidate:
            # R45 refuses the citation at record time; a review recorded before that rule still reaches the adjudicator.
            # Model pre-R60 history: the old rejection probe wrote no refusal certificate.
            with patch("research_harness.review_refusal._record_refusal"), self.assertRaises(ResearchError) as caught:
                self.mutate(decisions.record_value_review, payload)
            self.assertEqual((caught.exception.code, caught.exception.details["id"]),
                             ("review_evidence_undelivered", "candidate-3"))
            with patch.object(protocol, "find_carried_record", return_value={}):
                review = self.mutate(decisions.record_value_review, payload)["result"]
        else:
            review = self.mutate(decisions.record_value_review, payload)["result"]
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
        """R42: candidate-3 is in the store at the cited digest, but the stating slate review never received it. R45
        refuses such a citation at record time; the review here emulates one recorded before that rule."""
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

    def test_a_legacy_review_citing_an_unsupplied_artifact_is_undelivered(self):
        """R51 rejects new citations like this; a pre-R51 record still yields an incomplete version delivery."""
        self.prepare_reviews(results=False)
        unsupplied = self.artifacts.put(b"Evidence that no packet of the stating review delivered.", "text/plain")
        output = self.build_review_output("result")
        output["objections"] = [dict(self.make_objection(), evidence=[unsupplied])]
        payload = self.observe("result", "first", output, record=False)
        # Model a pre-R60 record; the historical refusal probe created no certificate.
        with patch("research_harness.review_refusal._record_refusal"), self.assertRaises(ResearchError) as caught:
            self.mutate(decisions.record_value_review, payload)
        self.assertEqual((caught.exception.code, caught.exception.details),
                         ("review_evidence_undelivered", {"sha256": unsupplied["sha256"]}))
        records = self.store.snapshot()["records"]
        assignment = records["review_assignment"][payload["assignment_id"]]
        state = protocol.assignment_state(records, self.artifacts, assignment["id"])
        # Write the historical record directly, preserving its actual observed output and assignment binding.
        saved = dict(id=payload["id"], payload=payload, digest=digest(payload), recorded_revision=self.store.revision + 1,
            dossier_id=None, dossier_digest=None, assignment_id=assignment["id"], reviewer_id=state["reviewer_id"],
            packet_digest=assignment["packet_digest"], prompt_hash=assignment["prompt_hash"],
            output_digest=state["output_digest"], support=None, independence_at_recording=state["context_status"],
            subject=assignment["subject"])
        self.put("value_review", saved["id"], saved)
        self.observe("result", "second")
        records = self.store.snapshot()["records"]
        version = decisions.state_objection(saved, output["objections"][0])
        expected = {"kind": "undelivered", "identity": unsupplied["sha256"], "reference": {"artifact_descriptor": {
            "location": unsupplied["path"], "sha256": unsupplied["sha256"], "size": unsupplied["size"],
            "media_type": "text/plain", "extensions": {}}}}
        delivery = protocol.describe_version_evidence(records, self.artifacts, version)
        self.assertEqual(delivery["version_evidence"], [expected])
        self.assertNotIn(unsupplied, [entry["artifact"] for entry in self.read_packet("result-first")["evidence"]])
        self.observe_adjudication(review_ids=["review-result-first", "review-result-second"])
        self.assertEqual(self.read_packet("focused")["version_evidence"], [expected])
        records = self.store.snapshot()["records"]
        missing = ["version_evidence:" + unsupplied["sha256"]]
        self.assertEqual(decisions.find_undelivered_adjudication_keys(records, self.artifacts, "focused",
            version_evidence=delivery), missing)
        self.assert_blocked_delivery()
        self.observe_adjudication(review_ids=["review-result-first", "review-result-second"], identifier="focused-refresh")
        refresh = self.read_packet("focused-refresh")["focused_refresh"]
        self.assertEqual((refresh["reason"], refresh["undelivered_keys"]), ("packet_incomplete", missing))

    def cite_record(self, kind, identifier):
        record = self.store.snapshot()["records"][kind][identifier]
        return {"kind": "record", "record_kind": kind, "id": identifier, "digest": digest(record)}

    def adjudicate_a_result_objection_citing(self, build_evidence):
        """The first result states O1 with the evidence built from the current records; the result pair is
        adjudicated. Returns the adjudicator packet and the coverage of its version delivery."""
        self.prepare_reviews(results=False)
        evidence = build_evidence(self.store.snapshot()["records"])
        for reviewer in ("first", "second"):
            output = self.build_review_output("result")
            if reviewer == "first":
                output["objections"] = [dict(self.make_objection(), evidence=evidence)]
            self.observe("result", reviewer, output)
        self.observe_adjudication(review_ids=["review-result-first", "review-result-second"])
        records = self.store.snapshot()["records"]
        [version] = decisions.collect_problem_post_objections(records, self.artifacts, [records["value_review"][identifier]
            for identifier in ("review-result-first", "review-result-second")])
        return self.read_packet("focused"), decisions.find_undelivered_adjudication_keys(
            records, self.artifacts, "focused", version_evidence=protocol.describe_version_evidence(records, self.artifacts, version))

    def test_a_result_review_citing_a_reading_and_the_value_record_is_adjudicated_complete(self):
        packet, undelivered = self.adjudicate_a_result_objection_citing(lambda records: [
            self.cite_record("reading", self.item["reading_id"]), self.cite_record("problem_value", "value-1")])
        reading, value = packet["version_evidence"]
        stating = self.read_packet("result-first")
        self.assertEqual(stating["problem_evidence"]["value"]["digest"], value["digest"])
        self.assertEqual((reading["record_kind"], reading["record"]["id"], reading["record"]["digest"]),
                         ("reading", self.item["reading_id"], reading["digest"]))
        supplied = [entry["artifact"] for entry in stating["evidence"]]
        self.assertTrue(reading["artifacts"])
        self.assertTrue(all(artifact in supplied for artifact in reading["artifacts"]))
        self.assertEqual((value["record_kind"], value["record"]["motivation"], value["artifacts"]),
                         ("problem_value", stating["problem_evidence"]["value"]["motivation"], []))
        self.assertEqual(undelivered, [])
        self.assert_delivered()

    def test_the_attempt_record_cited_by_a_result_review_is_delivered(self):
        from research_harness.problem_attempt import find_current_attempt
        packet, undelivered = self.adjudicate_a_result_objection_citing(lambda records: [
            self.cite_record("problem_attempt", find_current_attempt(records)["id"])])
        [attempt] = packet["version_evidence"]
        stating = self.read_packet("result-first")["problem_evidence"]["attempt"]
        self.assertEqual((attempt["record_kind"], attempt["record"]["id"], attempt["record"]["digest"]),
                         ("problem_attempt", stating["id"], stating["digest"]))
        self.assertEqual(undelivered, [])

    def test_a_result_review_citing_a_record_its_packets_never_carried_is_refused(self):
        self.prepare_reviews(results=False)
        output = self.build_review_output("result")
        output["objections"] = [dict(self.make_objection(), evidence=[self.cite_record("problem_candidate", "candidate-1")])]
        with self.assertRaises(ResearchError) as caught:
            self.observe("result", "first", output)
        self.assertEqual((caught.exception.code, caught.exception.details),
                         ("review_evidence_undelivered", {key: output["objections"][0]["evidence"][0][key]
                                                          for key in ("record_kind", "id", "digest")}))
        self.assertIn("problem_candidate candidate-1", caught.exception.message)

    def add_unbound_full_source(self):
        """A fully read source that the current problem evidence does not cite; returns the link of its first unit."""
        from research_harness import literature, reading
        version = self.metadata(2)
        capture = self.capture(version, body="The bound fails for the sequence 1, 3, 2 at n = 3, which the comparator "
                               "misses. The rest of this unit discusses unrelated constructions at length. References: none.")
        bundle = self.bundle(version, capture=capture, bundle_id="unbound-source")
        self.mutate(literature.import_bundle, bundle)
        self.mutate(reading.record_reading, self.full_note(bundle, "read-unbound"))
        return self.store.snapshot()["records"]["reading"]["read-unbound"]["inspections"][0]["link"]

    def adjudicate_a_bar_objection_on_a_passage(self, raw, unbound=False):
        """The second bar receives the first half of a fully read source unit as prior context, without the rest of
        the unit, and states O1 citing it, as the source link or as the raw artifact descriptor of the same artifact;
        the bar pair is adjudicated. With unbound, the source is not in the current problem evidence."""
        mapping = self.state_a_bar_objection_on_a_passage(raw, unbound)
        self.observe_adjudication(review_ids=["review-bar-first", "review-bar-second"])
        records = self.store.snapshot()["records"]
        [version] = decisions.collect_problem_post_objections(records, self.artifacts, [records["value_review"][identifier]
            for identifier in ("review-bar-first", "review-bar-second")])
        self.assertEqual(version["review_id"], "review-bar-second")
        self.assertNotIn(self.unread_tail, json.dumps(self.read_packet("bar-second")))
        packet = self.read_packet("focused")
        self.assertEqual((packet["version_evidence"], packet["version_source_mappings"]), ([], [mapping]))
        self.assertEqual(decisions.find_undelivered_adjudication_keys(records, self.artifacts, "focused",
            version_evidence=protocol.describe_version_evidence(records, self.artifacts, version)), [])

    def state_a_bar_objection_on_a_passage(self, raw, unbound=False):
        """The second bar states O1 on a passage-only artifact; returns the bar packet's passage mapping."""
        self.prepare_problem_evidence()
        unit = (self.add_unbound_full_source() if unbound else
                self.store.snapshot()["records"]["reading"][self.item["reading_id"]]["inspections"][0]["link"])
        self.unit = unit
        content = self.artifacts.read(unit["artifact"]).decode()
        self.unread_tail = content[len(content) // 2:]
        link = self.passage_link = self.link(unit["version_id"], unit["source_id"], unit["artifact"], content[:len(content) // 2])
        cited = link["artifact"] if raw else {"kind": "source", "link": link}
        self.observe("bar", "first")
        # The bar also receives self.supporting, which its output cites (R51, R53).
        assignment = self.assign("bar", "second", source_links=[link] + self.deliver_supporting("second")["source_links"])
        [mapping] = [row for row in json.loads(self.artifacts.read(assignment["packet"]))["prior_context"]
                     if row["origin"]["artifact_descriptor"]["sha256"] == unit["artifact"]["sha256"]]
        output = self.build_review_output("bar")
        output["objections"] = [dict(self.make_objection(), evidence=[cited])]
        with patch("research_harness.review_transport.send_request", return_value=self.build_provider_response(output)):
            self.mutate(protocol.invoke_assignment, {"id": "attempt-bar-second", "assignment_id": "bar-second"},
                        credential="test-only")
        self.mutate(decisions.record_value_review, dict(output, id="review-bar-second", dossier_id=None,
                                                        assignment_id="bar-second"))
        self.record_slate(review_ids=[])
        return mapping

    def test_a_bar_version_citing_a_delivered_passage_is_adjudicated_complete(self):
        self.adjudicate_a_bar_objection_on_a_passage(raw=False)

    def test_a_raw_descriptor_of_a_passage_only_artifact_is_delivered_through_its_passages(self):
        self.adjudicate_a_bar_objection_on_a_passage(raw=True)

    def test_a_malformed_objection_gets_its_typed_refusal_before_the_record_citation_check(self):
        """The record-time citation check reads only references that passed the shape checks."""
        self.prepare_reviews(results=False)
        cited = self.cite_record("reading", self.item["reading_id"])
        malformed = {"first": ({key: value for key, value in self.make_objection().items() if key != "evidence"},
                               "invalid_value_review"),
                     "second": (dict(self.make_objection(), evidence=[{key: cited[key] for key in ("kind", "record_kind", "id")}]),
                                "invalid_strategy")}
        for reviewer, (objection, code) in malformed.items():
            with self.subTest(reviewer=reviewer):
                output = self.build_review_output("result")
                output["objections"] = [objection]
                with self.assertRaises(ResearchError) as caught:
                    self.observe("result", reviewer, output)
                self.assertEqual(caught.exception.code, code)
        # A slate packet carries no reading, so a well-formed citation of one still meets the record-time rule.
        output = self.build_slate_output(["candidate-1"])
        output["objections"] = [dict(self.make_objection(), evidence=[cited], candidate_ids=["candidate-1"])]
        with self.assertRaises(ResearchError) as caught:
            self.observe("slate", "first", output)
        self.assertEqual((caught.exception.code, caught.exception.details), ("review_evidence_undelivered",
                         {key: cited[key] for key in ("record_kind", "id", "digest")}))

    def test_the_bar_standards_attach_no_bytes_beyond_a_supplied_passage(self):
        """Astra's F3 reproduction: the adjudicator receives a passage-only source within its bounds."""
        self.adjudicate_a_bar_objection_on_a_passage(raw=False, unbound=True)
        packet = self.read_packet("focused")
        attached = {entry["artifact"]["sha256"] for entry in packet["evidence"]}
        self.assertNotIn(self.unit["artifact"]["sha256"], attached)
        self.assertNotIn(self.unit["artifact"], packet["current_evidence"] + packet["original_evidence"])
        cited = packet["bar_calibrations"][1]["objections"][0]["evidence"][0]["link"]
        self.assertEqual((cited["artifact_reference_template"]["artifact_descriptor"]["sha256"], "artifact" in cited),
                         (self.unit["artifact"]["sha256"], False))
        self.assertEqual([bar["id"] for bar in packet["bar_calibrations"]], ["review-bar-first", "review-bar-second"])

    def test_an_adjudication_on_raw_calibrations_stays_complete(self):
        """A packet built before the standards travelled as templates still covers them."""
        self.prepare_reviews(result_reviewer="first")
        real = protocol.add_focused_adjudication

        def build_with_raw_calibrations(*arguments, **options):
            real(*arguments, **options)
            records, packet = arguments[0], arguments[2]
            packet["bar_calibrations"] = [records["value_review"][bar["id"]]["payload"] for bar in packet["bar_calibrations"]]
        with patch.object(protocol, "add_focused_adjudication", side_effect=build_with_raw_calibrations):
            self.observe_adjudication(review_ids=["review-result-first", "review-result-second"])
        records = self.store.snapshot()["records"]
        self.assertNotIn("record_reference", self.read_packet("focused")["bar_calibrations"][0])
        [version] = decisions.collect_problem_post_objections(records, self.artifacts, [records["value_review"][identifier]
            for identifier in ("review-result-first", "review-result-second")])
        self.assertEqual(decisions.find_undelivered_adjudication_keys(records, self.artifacts, "focused",
            version_evidence=protocol.describe_version_evidence(records, self.artifacts, version)), [])
        self.assert_delivered()

    def test_a_result_review_cites_a_bar_by_the_reference_its_packet_exposes(self):
        """R48: the packet alone gives the exact reference of a delivered review."""
        self.prepare_reviews(results=False)
        assignment = self.assign("result", "first")
        [bar] = [row for row in json.loads(self.artifacts.read(assignment["packet"]))["bars"] if row["id"] == "review-bar-second"]
        reference = bar["record_reference"]
        self.assertEqual(reference, self.cite_record("value_review", "review-bar-second"))
        output = self.build_review_output("result")
        output["objections"] = [dict(self.make_objection(), evidence=[reference])]
        with patch("research_harness.review_transport.send_request", return_value=self.build_provider_response(output)):
            self.mutate(protocol.invoke_assignment, {"id": "attempt-result-first", "assignment_id": "result-first"},
                        credential="test-only")
        self.mutate(decisions.record_value_review, dict(output, id="review-result-first", dossier_id=None,
                                                        assignment_id="result-first"))
        self.observe("result", "second")
        self.observe_adjudication(review_ids=["review-result-first", "review-result-second"])
        [entry] = self.read_packet("focused")["version_evidence"]
        self.assertEqual((entry["record_kind"], entry["record"]["id"], entry["record"]["stage"], entry["record"]["record_reference"]),
                         ("value_review", "review-bar-second", "bar", reference))
        self.assert_delivered()

    def test_a_refreshed_result_reviewer_cites_the_records_its_material_objection_delivered(self):
        """Astra's F4 reproduction: the result packet carries the objection's cited records as content."""
        self.contest_with_a_candidate_record(late_candidate=True)
        late = self.cite_candidate("candidate-3")
        [projected] = [row for row in self.read_packet("slate-first-refreshed")["candidates"] if row["id"] == "candidate-2"]
        for reviewer, cited in (("first", late), ("second", self.cited)):
            with self.subTest(reviewer=reviewer):
                assignment = self.assign("result", reviewer, identifier="reassessed-" + reviewer)
                [objection] = json.loads(self.artifacts.read(assignment["packet"]))["material_objections"]
                delivered, undelivered = objection["version_evidence"]
                self.assertEqual((delivered["kind"], delivered["id"], delivered["record"]),
                                 ("record", "candidate-2", projected))
                self.assertEqual((undelivered["kind"], undelivered["identity"]), ("undelivered", "problem_candidate:candidate-3"))
                output = self.build_review_output("result")
                output["value"]["objection_findings"] = [dict(self.make_finding("contested"), evidence=[cited])]
                with patch("research_harness.review_transport.send_request", return_value=self.build_provider_response(output)):
                    self.mutate(protocol.invoke_assignment, {"id": "attempt-" + assignment["id"], "assignment_id": assignment["id"]},
                                credential="test-only")
                payload = dict(output, id="review-" + assignment["id"], dossier_id=None, assignment_id=assignment["id"])
                if cited is late:
                    with self.assertRaises(ResearchError) as caught:
                        self.mutate(decisions.record_value_review, payload)
                    self.assertEqual((caught.exception.code, caught.exception.details["id"]),
                                     ("review_evidence_undelivered", "candidate-3"))
                else:
                    self.assertEqual(self.mutate(decisions.record_value_review, payload)["result"]["id"], "review-reassessed-second")

    def test_a_bar_payload_that_cites_a_passage_attaches_none_of_its_source(self):
        """R49: the other reviewer's slate and result packets carry the bar readable, without the cited source."""
        self.state_a_bar_objection_on_a_passage(raw=False, unbound=True)
        stored = self.store.snapshot()["records"]["value_review"]["review-bar-second"]["payload"]
        for role in ("slate", "result"):
            with self.subTest(role=role):
                packet = json.loads(self.artifacts.read(self.assign(role, "first", identifier=role + "-first")["packet"]))
                self.assertNotIn(self.unit["artifact"]["sha256"], {entry["artifact"]["sha256"] for entry in packet["evidence"]})
                [bar] = [row for row in packet["bars"] if row["id"] == "review-bar-second"]
                self.assertEqual((bar["value"]["consequence"], bar["objections"][0]["claim"]),
                                 (stored["value"]["consequence"], stored["objections"][0]["claim"]))
                link = bar["objections"][0]["evidence"][0]["link"]
                self.assertEqual((link["artifact_reference_template"]["artifact_descriptor"]["sha256"], "artifact" in link),
                                 (self.unit["artifact"]["sha256"], False))

    def test_a_result_review_citing_an_artifact_it_never_received_is_refused(self):
        """R51: an artifact that no packet of the review delivered, as bytes or as a passage, is refused at record
        time, after the shape checks."""
        self.prepare_reviews(results=False)
        never_received = self.artifacts.put(b"A field standard that no packet delivered.", "text/plain")
        malformed = {key: value for key, value in dict(self.make_objection(), evidence=[never_received]).items() if key != "reason"}
        for reviewer, objection, code in (("first", malformed, "invalid_value_review"),
                                          ("second", dict(self.make_objection(), evidence=[never_received]),
                                           "review_evidence_undelivered")):
            with self.subTest(reviewer=reviewer):
                output = self.build_review_output("result")
                output["objections"] = [objection]
                with self.assertRaises(ResearchError) as caught:
                    self.observe("result", reviewer, output)
                self.assertEqual(caught.exception.code, code)
        self.assertEqual(caught.exception.details, {"sha256": never_received["sha256"]})
        self.assertNotIn(never_received["sha256"], {entry["artifact"]["sha256"] for entry in self.read_packet("result-second")["evidence"]})

    def test_result_packets_stored_with_raw_review_payloads_stay_complete(self):
        """Results observed on packets built before R49 (raw payloads, no record_reference) are judged the same way."""
        self.prepare_reviews(results=False)
        with patch.object(protocol, "deliver_review", side_effect=lambda records, payload: payload):
            for reviewer in ("first", "second"):
                output = self.build_review_output("result")
                if reviewer == "first":
                    output["objections"] = [self.make_objection()]
                self.observe("result", reviewer, output)
        bar = self.read_packet("result-first")["bars"][0]
        self.assertEqual(("record_reference" in bar, bar["value"]["evidence"]), (False, [self.supporting]))
        self.observe_adjudication(review_ids=["review-result-first", "review-result-second"])
        records = self.store.snapshot()["records"]
        [version] = decisions.collect_problem_post_objections(records, self.artifacts, [records["value_review"][identifier]
            for identifier in ("review-result-first", "review-result-second")])
        self.assertEqual(decisions.find_undelivered_adjudication_keys(records, self.artifacts, "focused",
            version_evidence=protocol.describe_version_evidence(records, self.artifacts, version)), [])
        self.assert_delivered()

    def record_result(self, reviewer, output, identifier=None):
        """Assign, invoke and record one result review of the given output."""
        assignment = self.assign("result", reviewer, identifier=identifier)
        with patch("research_harness.review_transport.send_request", return_value=self.build_provider_response(output)):
            self.mutate(protocol.invoke_assignment, {"id": "attempt-" + assignment["id"], "assignment_id": assignment["id"]},
                        credential="test-only")
        return self.mutate(decisions.record_value_review, dict(output, id="review-" + assignment["id"], dossier_id=None,
                                                               assignment_id=assignment["id"]))["result"]

    def test_a_result_reviewer_copies_the_passage_link_of_a_material_objection(self):
        """R50: O1's passage reaches the result reviewer within its bounds, and a copied link is delivered."""
        # R56: the citation is built from the received mapping alone.
        self.copy_the_passage_of_a_material_objection(lambda mapping: {"kind": "source", "link": mapping["citation"]})

    def test_a_result_reviewer_cites_the_full_descriptor_of_a_passage_it_received(self):
        """R44 and R50: the descriptor that the passage's template names is delivered through the passages."""
        def build_descriptor(mapping):
            template = mapping["origin"]["artifact_descriptor"]
            return {"path": template["location"], **{key: template[key] for key in ("sha256", "size", "media_type")}}
        self.copy_the_passage_of_a_material_objection(build_descriptor)

    def copy_the_passage_of_a_material_objection(self, build_citation):
        """The second bar states O1 on a passage of an unbound source; the first result reviewer contests O1 citing
        what the citation builder makes of O1's delivered passage, and the result pair is adjudicated."""
        mapping = self.state_a_bar_objection_on_a_passage(raw=False, unbound=True)
        packet = json.loads(self.artifacts.read(self.assign("result", "first")["packet"]))
        [objection] = packet["material_objections"]
        cited = build_citation(objection["version_source_mappings"][0])
        self.assertEqual((objection["version_evidence"], objection["version_source_mappings"]), ([], [mapping]))
        attached = {entry["artifact"]["sha256"] for entry in packet["evidence"]}
        self.assertEqual((self.unit["artifact"]["sha256"] in attached, mapping["excerpt"]["sha256"] in attached), (False, True))
        for reviewer, finding in (("first", dict(self.make_finding("contested"), evidence=[cited])),
                                  ("second", self.make_finding("resolved"))):
            output = self.build_review_output("result")
            output["value"]["objection_findings"] = [finding]
            if reviewer == "first":
                with patch("research_harness.review_transport.send_request", return_value=self.build_provider_response(output)):
                    self.mutate(protocol.invoke_assignment, {"id": "attempt-result-first", "assignment_id": "result-first"},
                                credential="test-only")
                self.mutate(decisions.record_value_review, dict(output, id="review-result-first", dossier_id=None,
                                                                assignment_id="result-first"))
            else:
                self.record_result(reviewer, output)
        self.observe_adjudication(review_ids=["review-result-first", "review-result-second"])
        records = self.store.snapshot()["records"]
        [version] = decisions.collect_problem_post_objections(records, self.artifacts, [records["value_review"][identifier]
            for identifier in ("review-bar-first", "review-bar-second", "review-result-first", "review-result-second")])
        self.assertEqual(version["review_id"], "review-result-first")
        adjudicator = self.read_packet("focused")
        self.assertEqual((adjudicator["version_evidence"], adjudicator["version_source_mappings"]), ([], [mapping]))
        self.assertEqual(decisions.find_undelivered_adjudication_keys(records, self.artifacts, "focused",
            version_evidence=protocol.describe_version_evidence(records, self.artifacts, version)), [])
        for identifier in ("bar-second", "result-first", "result-second", "focused"):
            self.assertNotIn(self.unread_tail, json.dumps(self.read_packet(identifier)))

    def test_a_first_result_packet_delivers_the_evidence_of_a_contaminated_lineage(self):
        """R52: an objection of another slot's contaminated review arrives with its passage, and a finding copying its
        link is recorded and deliverable."""
        mapping = self.state_a_bar_objection_on_a_passage(raw=False, unbound=True)
        cited = {"kind": "source", "link": self.passage_link}
        output = self.build_review_output("result")
        output["objections"] = [dict(self.make_objection(), id="o2", claim="The passage leaves the bound unproved.",
                                     evidence=[cited])]
        output["value"]["objection_findings"] = [dict(self.make_finding("contested"), evidence=[cited])]
        self.record_result("second", output)
        self.mutate(protocol.record_context_event, {"id": "exposure", "assignment_id": "result-second",
            "kind": "contamination", "source": "author_history", "reason": "The assessor received author advocacy.",
            "evidence": [self.supporting]})
        packet = json.loads(self.artifacts.read(self.assign("result", "first")["packet"]))
        [historical] = packet["historical_reviews"]
        [stated] = historical["review"]["objections"]
        self.assertEqual((stated["id"], stated["version_evidence"], stated["version_source_mappings"]), ("o2", [], [mapping]))
        output = self.build_review_output("result")
        output["value"]["objection_findings"] = [self.make_finding("resolved"),
                                                 dict(self.make_finding("contested"), id="o2", evidence=[cited])]
        with patch("research_harness.review_transport.send_request", return_value=self.build_provider_response(output)):
            self.mutate(protocol.invoke_assignment, {"id": "attempt-result-first", "assignment_id": "result-first"},
                        credential="test-only")
        saved = self.mutate(decisions.record_value_review, dict(output, id="review-result-first", dossier_id=None,
                                                                assignment_id="result-first"))["result"]
        records = self.store.snapshot()["records"]
        version = decisions.state_objection(saved, dict(self.make_finding("contested"), id="o2", evidence=[cited]))
        delivery = protocol.describe_version_evidence(records, self.artifacts, version)
        self.assertEqual((delivery["version_evidence"], delivery["version_source_mappings"]), ([], [mapping]))

    def test_a_result_version_citing_a_record_of_a_material_objection_delivers_its_projection(self):
        """M1: the adjudicator receives the slate's candidate projection itself, without the delivery entry around it."""
        self.contest_with_a_candidate_record()
        [projected] = [row for row in self.read_packet("slate-first-refreshed")["candidates"] if row["id"] == "candidate-2"]
        for reviewer, finding in (("first", self.make_finding("resolved")),
                                  ("second", dict(self.make_finding("contested"), evidence=[self.cited]))):
            output = self.build_review_output("result")
            output["value"]["objection_findings"] = [finding]
            self.record_result(reviewer, output, identifier="reassessed-" + reviewer)
        self.observe_adjudication(review_ids=["review-reassessed-first", "review-reassessed-second"])
        [entry] = self.read_packet("focused")["version_evidence"]
        self.assertEqual((entry["kind"], entry["id"], entry["record"]), ("record", "candidate-2", projected))

    def cite_an_assessment_through_an_objection(self):
        """Astra's F6 reproduction: the first results contest O1 citing assessment S1, which attaches A; S2 without A
        replaces S1; the refreshed second result receives S1 and A through O1's version_evidence and contests O1
        citing S1."""
        from test_research_problem_assessment import build_assessment
        self.prepare_reviews(bar_reviewer="second", results=False)
        self.attachment = self.artifacts.put(b"A table of the extremal values that only assessment S1 attaches.", "text/plain")
        first = build_assessment(self.selected, identifier="assessment-s1")
        first["items"]["priority"]["evidence"] += [self.supporting, self.attachment]
        self.mutate(self.load_problem_module().record_assessment, first)
        contested = dict(self.make_finding("contested"), evidence=[self.cite_record("problem_assessment", "assessment-s1")],
                         reason="Assessment S1 attaches the decisive table.")
        for reviewer in ("first", "second"):
            output = self.build_review_output("result")
            output["value"]["objection_findings"] = [contested]
            self.observe("result", reviewer, output)
        second = build_assessment(self.selected, identifier="assessment-s2")
        second["items"]["priority"]["evidence"].append(self.supporting)
        self.mutate(self.load_problem_module().record_assessment, second)
        for reviewer, finding in (("first", self.make_finding("resolved")),
                                  ("second", dict(contested, reason="The table of S1 still refutes the resolution."))):
            output = self.build_review_output("result")
            output["value"]["objection_findings"] = [finding]
            self.record_result(reviewer, output, identifier="refreshed-" + reviewer)
        [objection] = self.read_packet("refreshed-second")["material_objections"]
        [entry] = objection["version_evidence"]
        self.assertIn(self.attachment, entry["artifacts"])

    def test_a_record_cited_through_an_objection_keeps_its_attachments(self):
        """R54: the adjudicator of the second hop receives S1 with its attachment A, which no current evidence holds."""
        self.cite_an_assessment_through_an_objection()
        self.observe_adjudication(review_ids=["review-bar-first", "review-bar-second"])
        packet = self.read_packet("focused")
        [entry] = packet["version_evidence"]
        self.assertEqual((entry["id"], self.attachment in entry["artifacts"]), ("assessment-s1", True))
        self.assertIn(self.attachment["sha256"], {row["artifact"]["sha256"] for row in packet["evidence"]})
        self.assertNotIn(self.attachment, packet["current_evidence"] + packet["original_evidence"])
        self.assert_delivered()

    def test_an_adjudication_without_a_record_attachment_is_incomplete_and_refreshes(self):
        self.cite_an_assessment_through_an_objection()
        real = protocol.add_focused_adjudication

        def build_without_the_attachment(*arguments, **options):
            real(*arguments, **options)
            for entry in arguments[2]["version_evidence"]:
                entry["artifacts"] = [artifact for artifact in entry["artifacts"] if artifact != self.attachment]
        with patch.object(protocol, "add_focused_adjudication", side_effect=build_without_the_attachment):
            self.observe_adjudication(review_ids=["review-bar-first", "review-bar-second"])
        records = self.store.snapshot()["records"]
        [version] = [row for row in decisions.collect_problem_post_objections(records, self.artifacts, [
            records["value_review"][identifier] for identifier in ("review-bar-first", "review-bar-second",
                                                                   "review-refreshed-first", "review-refreshed-second")])]
        self.assertEqual(decisions.find_undelivered_adjudication_keys(records, self.artifacts, "focused",
            version_evidence=protocol.describe_version_evidence(records, self.artifacts, version)),
            ["version_evidence:problem_assessment:assessment-s1"])
        self.assert_blocked_delivery()
        self.observe_adjudication(review_ids=["review-bar-first", "review-bar-second"], identifier="focused-complete")
        refresh = self.read_packet("focused-complete")["focused_refresh"]
        self.assertEqual((refresh["reason"], refresh["undelivered_keys"]),
                         ("packet_incomplete", ["version_evidence:problem_assessment:assessment-s1"]))
        self.assert_delivered()

    def contest_on_a_span_of_the_unbound_unit(self, start, end):
        """The second bar states O1 on the first half of an unbound source unit; returns the first result's output that
        contests O1 citing the given span of that unit by offsets and hash, with the cited link."""
        from research_harness.source_links import span_locator
        self.state_a_bar_objection_on_a_passage(raw=False, unbound=True)
        content = self.artifacts.read(self.unit["artifact"]).decode()
        link = {key: self.unit[key] for key in ("version_id", "source_id", "artifact")}
        link["locator"] = span_locator(content, start(content), end(content))
        output = self.build_review_output("result")
        output["value"]["objection_findings"] = [dict(self.make_finding("contested"), evidence=[{"kind": "source", "link": link}])]
        return output, link

    def adjudicate_the_result_pair(self):
        output = self.build_review_output("result")
        output["value"]["objection_findings"] = [self.make_finding("resolved")]
        self.record_result("second", output)
        self.observe_adjudication(review_ids=["review-result-first", "review-result-second"])
        records = self.store.snapshot()["records"]
        [version] = decisions.collect_problem_post_objections(records, self.artifacts, [records["value_review"][identifier]
            for identifier in ("review-bar-first", "review-bar-second", "review-result-first", "review-result-second")])
        self.assertEqual(version["review_id"], "review-result-first")
        return records, version

    def test_a_citation_beyond_a_supplied_passage_is_refused(self):
        """R55: Astra's reproduction; the first half of the unit was supplied, the second half is cited."""
        output, link = self.contest_on_a_span_of_the_unbound_unit(lambda text: len(text) // 2, len)
        with self.assertRaises(ResearchError) as caught:
            self.observe("result", "first", output)
        self.assertEqual((caught.exception.code, caught.exception.details),
                         ("review_evidence_undelivered", {"sha256": link["artifact"]["sha256"], "locator": link["locator"]}))

    def test_a_citation_within_a_supplied_passage_is_recorded_and_delivered(self):
        output, _ = self.contest_on_a_span_of_the_unbound_unit(lambda text: 0, lambda text: len(text) // 4)
        self.observe("result", "first", output)
        records, version = self.adjudicate_the_result_pair()
        packet = self.read_packet("focused")
        self.assertEqual(packet["version_evidence"], [])
        self.assertEqual([row["origin"]["artifact_descriptor"]["sha256"] for row in packet["version_source_mappings"]],
                         [self.unit["artifact"]["sha256"]])
        self.assertEqual(decisions.find_undelivered_adjudication_keys(records, self.artifacts, "focused",
            version_evidence=protocol.describe_version_evidence(records, self.artifacts, version)), [])

    def test_a_legacy_citation_beyond_a_supplied_passage_is_undelivered(self):
        """R55: a version recorded before the bounds check, citing beyond the passage, stays undelivered."""
        output, link = self.contest_on_a_span_of_the_unbound_unit(lambda text: len(text) // 2, len)
        with patch("research_harness.review_supplied_evidence.is_received", return_value=True):
            self.observe("result", "first", output)
        records, version = self.adjudicate_the_result_pair()
        [entry] = self.read_packet("focused")["version_evidence"]
        self.assertEqual((entry["kind"], entry["identity"]), ("undelivered", link["artifact"]["sha256"]))
        self.assertEqual(decisions.find_undelivered_adjudication_keys(records, self.artifacts, "focused",
            version_evidence=protocol.describe_version_evidence(records, self.artifacts, version)),
            ["version_evidence:" + link["artifact"]["sha256"]])
        self.assert_blocked_delivery()

    def read_a_json_passage(self):
        """A recorded JSON metadata response read through an inspection of its /title field; returns that JSON link."""
        from research_harness.reading import record_reading
        version = self.metadata(78001, references=[{"title": "A cited comparator", "year": 2020}])
        records = self.store.snapshot()["records"]
        [source_id] = [identifier for identifier in records["work"][version]["source_ids"]
                       if records["source"][identifier]["response"]["media_type"] == "application/json"]
        response = records["source"][source_id]["response"]
        link = {"version_id": version, "source_id": source_id, "artifact": response,
                "locator": {"kind": "json", "pointer": "/title", "value": json.loads(self.artifacts.read(response))["title"]}}
        note = self.abstract_note(version, note_id="json-note")
        note["inspections"].append({"unit_id": None, "link": link, "note": "Read the title field of the response."})
        self.mutate(record_reading, note)
        return link

    def test_a_correction_cites_a_json_passage_without_offering_an_output_citation(self):
        """R61: JSON is admissible correction input, independently of adjudicator output evidence."""
        self.prepare_problem_evidence()
        link = self.read_a_json_passage()
        self.observe("bar", "first")
        assignment = self.assign("bar", "second", source_links=[link] + self.deliver_supporting("second")["source_links"])
        [mapping] = [row for row in json.loads(self.artifacts.read(assignment["packet"]))["prior_context"]
                     if row["origin"]["locator"]["kind"] == "json"]
        self.assertNotIn("citation", mapping)
        output = self.build_review_output("bar")
        output["objections"] = [self.make_objection()]
        with patch("research_harness.review_transport.send_request", return_value=self.build_provider_response(output)):
            self.mutate(protocol.invoke_assignment, {"id": "attempt-bar-second", "assignment_id": "bar-second"},
                        credential="test-only")
        self.mutate(decisions.record_value_review, dict(output, id="review-bar-second", dossier_id=None,
                                                        assignment_id="bar-second"))
        self.record_slate(review_ids=[])
        correction = {"kind": "omission", "claim": "The comparator's title field was overlooked.",
                      "reason": "The supplied passage names the comparator.", "evidence": [link],
                      "prior_adjudication_id": None, "new_error_explanation": None}
        focused = self.assign("adjudicator", "focused-third", identifier="focused", review_ids=["review-bar-first", "review-bar-second"],
                              objection_id="unsupported-implication", correction=correction)
        packet = json.loads(self.artifacts.read(focused["packet"]))
        self.assertIn(mapping, packet["supplied_source_mappings"])
        self.assertTrue(all("citation" not in row for row in packet["supplied_source_mappings"]
                            if row["origin"]["locator"]["kind"] == "json"))
        self.assertNotIn(link["artifact"]["sha256"], {row["artifact"]["sha256"] for row in packet["evidence"]})
        output = self.adjudication_output([self.supporting], correction_admissible=True)
        self.invoke_focused(output)
        saved = self.mutate(protocol.record_adjudication,
            dict(output, id="focused-finding", assignment_id="focused"))["result"]
        self.assertTrue(protocol.adjudication_state(self.store.snapshot()["records"], self.artifacts, saved["id"])["ready"])

    def test_legacy_passage_mappings_offer_only_recordable_citations(self):
        from research_harness.review_supplied_evidence import supplied_context
        self.prepare_problem_evidence()
        full = self.add_unbound_full_source()
        json_link = self.read_a_json_passage()
        assignment = self.assign("bar", "first", source_links=[full, json_link]
                                 + self.deliver_supporting("first")["source_links"])
        packet = json.loads(self.artifacts.read(assignment["packet"]))
        for mapping in packet["prior_context"]:
            mapping.pop("citation", None)
        self.put("review_assignment", assignment["id"], dict(assignment, packet=protocol._put_json(self.artifacts, packet)))
        _, mappings = supplied_context(self.store.snapshot()["records"], self.artifacts, [assignment["id"]])
        self.assertEqual(["citation" in row for row in mappings], [True, False, False])
        # Stored legacy citations are subject to the same rule on subsequent delivery.
        for mapping, link in zip(packet["prior_context"], [full, json_link, self.supporting_links[self.supporting["sha256"]]]):
            mapping["citation"] = link
        self.put("review_assignment", assignment["id"], dict(assignment, packet=protocol._put_json(self.artifacts, packet)))
        _, mappings = supplied_context(self.store.snapshot()["records"], self.artifacts, [assignment["id"]])
        self.assertEqual(["citation" in row for row in mappings], [True, False, False])

    def adjudication_output(self, evidence, correction_admissible=None):
        return {"objection_id": "unsupported-implication", "disposition": "not_upheld",
                "reason": "The delivered evidence resolves the exact disputed implication.",
                "evidence": evidence, "correction_admissible": correction_admissible}

    def invoke_focused(self, output, identifier="focused"):
        with patch("research_harness.review_transport.send_request", return_value=self.build_provider_response(output)):
            self.mutate(protocol.invoke_assignment, {"id": identifier + "-attempt", "assignment_id": identifier},
                        credential="test-only")

    def test_adjudication_refuses_a_fully_read_source_absent_from_every_input(self):
        self.prepare_reviews(result_reviewer="first")
        link = self.add_unbound_full_source()
        self.assign("adjudicator", "focused-third", identifier="focused",
                    review_ids=["review-result-first", "review-result-second"], objection_id="unsupported-implication")
        self.assertNotIn(link["artifact"]["sha256"], json.dumps(self.read_packet("focused")))
        output = self.adjudication_output([{"kind": "source", "link": link}])
        self.invoke_focused(output)
        with self.assertRaises(ResearchError) as caught:
            self.mutate(protocol.record_adjudication, dict(output, id="focused-finding", assignment_id="focused"))
        self.assertEqual((caught.exception.code, caught.exception.details),
                         ("review_evidence_undelivered", {"sha256": link["artifact"]["sha256"], "locator": link["locator"]}))

    def test_adjudication_refuses_an_artifact_absent_from_every_input(self):
        self.prepare_reviews(result_reviewer="first")
        artifact = self.artifacts.put(b"Held bytes absent from the adjudicator's inputs.", "text/plain")
        self.assign("adjudicator", "focused-third", identifier="focused",
                    review_ids=["review-result-first", "review-result-second"], objection_id="unsupported-implication")
        output = self.adjudication_output([artifact])
        self.invoke_focused(output)
        with self.assertRaises(ResearchError) as caught:
            self.mutate(protocol.record_adjudication, dict(output, id="focused-finding", assignment_id="focused"))
        self.assertEqual((caught.exception.code, caught.exception.details),
                         ("review_evidence_undelivered", {"sha256": artifact["sha256"]}))

    def test_adjudication_refuses_a_record_absent_from_every_input(self):
        self.prepare_reviews(result_reviewer="first")
        reference = self.cite_candidate("candidate-1")
        self.assign("adjudicator", "focused-third", identifier="focused",
                    review_ids=["review-result-first", "review-result-second"], objection_id="unsupported-implication")
        output = self.adjudication_output([reference])
        self.invoke_focused(output)
        with self.assertRaises(ResearchError) as caught:
            self.mutate(protocol.record_adjudication, dict(output, id="focused-finding", assignment_id="focused"))
        self.assertEqual((caught.exception.code, caught.exception.details), ("review_evidence_undelivered",
                         {key: reference[key] for key in ("record_kind", "id", "digest")}))

    def test_adjudication_refuses_a_source_locator_outside_its_delivered_passage(self):
        from research_harness.source_links import span_locator
        self.state_a_bar_objection_on_a_passage(raw=False, unbound=True)
        self.assign("adjudicator", "focused-third", identifier="focused",
                    review_ids=["review-bar-first", "review-bar-second"], objection_id="unsupported-implication")
        content = self.artifacts.read(self.unit["artifact"]).decode()
        link = dict(self.unit, locator=span_locator(content, len(content) // 2, len(content)))
        output = self.adjudication_output([{"kind": "source", "link": link}])
        self.invoke_focused(output)
        with self.assertRaises(ResearchError) as caught:
            self.mutate(protocol.record_adjudication, dict(output, id="focused-finding", assignment_id="focused"))
        self.assertEqual((caught.exception.code, caught.exception.details),
                         ("review_evidence_undelivered", {"sha256": link["artifact"]["sha256"], "locator": link["locator"]}))

    def test_reassessment_findings_refuse_a_source_added_after_the_packet_was_frozen(self):
        objection_fixtures.ProblemObjectionTests.prepare_repaired_result(self, changed=False)
        link = self.add_unbound_full_source()
        packet = self.read_packet("repaired-result")
        self.assertNotIn(link["artifact"]["sha256"], json.dumps(packet))
        output = self.build_review_output("result")
        output["reassessment"] = {"assignment_id": "result-first", "findings": [{
            "review_id": "review-result-first", "disposition": "revised",
            "reason": "The previously recorded grade remains supported.", "evidence": [{"kind": "source", "link": link}]}]}
        self.invoke_focused(output, "repaired-result")
        with self.assertRaises(ResearchError) as caught:
            self.mutate(decisions.record_value_review,
                dict(output, id="review-repaired-result", dossier_id=None, assignment_id="repaired-result"))
        self.assertEqual((caught.exception.code, caught.exception.details),
                         ("review_evidence_undelivered", {"sha256": link["artifact"]["sha256"], "locator": link["locator"]}))

    def test_legacy_undelivered_adjudication_blocks_post_and_refreshes_the_fixed_adjudicator(self):
        from research_harness.cli import status_report
        from research_harness.problem_delivery import assess_post
        from research_harness.source_links import span_locator
        # Only the objection's passages deliver this source, independently of the current problem evidence.
        self.state_a_bar_objection_on_a_passage(raw=False, unbound=True)
        for reviewer in ("first", "second"):
            output = self.build_review_output("result")
            output["value"]["objection_findings"] = [self.make_finding("continuing")]
            self.observe("result", reviewer, output)
        self.assign("adjudicator", "focused-third", identifier="focused",
                    review_ids=["review-bar-first", "review-bar-second"], objection_id="unsupported-implication")
        artifact = self.artifacts.put(b"Unsupported historical disposition evidence.", "text/plain")
        candidate = self.cite_candidate("candidate-1")
        content = self.artifacts.read(self.unit["artifact"]).decode()
        outside = dict(self.unit, locator=span_locator(content, len(content) // 2, len(content)))
        output = self.adjudication_output([artifact, candidate, {"kind": "source", "link": outside}])
        self.invoke_focused(output)
        packet = self.read_packet("focused")
        saved = dict(output, id="legacy-finding", assignment_id="focused", dossier_id=None,
                     subject=packet["subject"], problem_evidence_digest=packet["problem_evidence_digest"],
                     recorded_revision=self.store.revision + 1)
        self.put("review_adjudication", saved["id"], saved)
        records = self.store.snapshot()["records"]
        state = protocol.adjudication_state(records, self.artifacts, saved["id"])
        self.assertFalse(state["ready"])
        [pending] = [item for item in state["obligations"] if item["code"] == "review_adjudication_evidence_undelivered"]
        references = [{"sha256": artifact["sha256"]}, {key: candidate[key] for key in ("record_kind", "id", "digest")},
                      {"sha256": outside["artifact"]["sha256"], "locator": outside["locator"]}]
        self.assertEqual(pending["undelivered_references"], references)
        self.assertFalse(assess_post(records, self.artifacts)["ready"])
        self.assertIn(pending["code"], {item["code"] for item in status_report(self.store)["obligations"]})
        self.assert_blocked_delivery()
        context = {"review_ids": ["review-bar-first", "review-bar-second"], "objection_id": "unsupported-implication"}
        # Each part of the fixed identity remains mandatory.
        self.assert_error("review_adjudicator_fixed", lambda: self.assign("adjudicator", "another-third",
                          identifier="wrong-reviewer", **context))
        self.put("workspace", "problem", dict(records["workspace"]["problem"], authors=["problem-author", "coauthor"]))
        self.assert_error("review_adjudicator_fixed", lambda: self.assign("adjudicator", "focused-third", author="coauthor",
                          identifier="wrong-author", **context))
        route = dict(records["review_route"]["route"], id="another-route")
        self.put("review_route", route["id"], route)
        self.assert_error("review_adjudicator_fixed", lambda: self.mutate(protocol.record_assignment, {
            "id": "wrong-route", "route_id": route["id"], "dossier_id": None, "role": "adjudicator",
            "reviewer_id": "focused-third", "author_id": "problem-author", "context": context}))
        refreshed = self.assign("adjudicator", "focused-third", identifier="focused-refreshed", **context)
        refresh = self.read_packet(refreshed["id"])["focused_refresh"]
        self.assertEqual((refresh["reason"], refresh["undelivered_references"]), ("disposition_unsupported", references))
        self.assertEqual(refreshed["history_assignment_id"], "focused")
        inside = dict(self.unit, locator=span_locator(content, 0, len(content) // 4))
        statement = self.store.snapshot()["records"]["problem_statement"][self.selected["id"]]
        statement_ref = {"kind": "record", "record_kind": "problem_statement", "id": statement["id"], "digest": digest(statement)}
        # The objection's version artifact, its bounded passage and a projected problem record all remain usable.
        output = self.adjudication_output([self.unit["artifact"], {"kind": "source", "link": inside}, statement_ref])
        self.invoke_focused(output, refreshed["id"])
        self.mutate(protocol.record_adjudication, dict(output, id="refreshed-finding", assignment_id=refreshed["id"]))
        self.assertEqual(self.store.snapshot()["records"]["review_adjudication"][saved["id"]], saved)
        self.assert_error("review_adjudicator_fixed", lambda: self.assign("adjudicator", "focused-third",
                          identifier="unneeded-refresh", **context))
        self.assert_delivered()

    def test_a_result_packet_offers_a_citation_only_for_a_passage_a_review_can_record(self):
        """R58: O1 cites a text passage and a JSON passage; the result packet carries a citation for the text passage
        only. A link built to the JSON passage is refused with the typed reading code; the text citation is recorded."""
        self.prepare_problem_evidence()
        json_link = self.read_a_json_passage()
        unit = self.add_unbound_full_source()
        content = self.artifacts.read(unit["artifact"]).decode()
        text_link = self.link(unit["version_id"], unit["source_id"], unit["artifact"], content[:len(content) // 2])
        self.observe("bar", "first")
        assignment = self.assign("bar", "second", source_links=[text_link, json_link]
                                 + self.deliver_supporting("second")["source_links"])
        prior = {row["origin"]["artifact_descriptor"]["sha256"]: row
                 for row in json.loads(self.artifacts.read(assignment["packet"]))["prior_context"]}
        output = self.build_review_output("bar")
        output["objections"] = [dict(self.make_objection(), evidence=[
            {"kind": "source", "link": prior[unit["artifact"]["sha256"]]["citation"]}, json_link["artifact"]])]
        with patch("research_harness.review_transport.send_request", return_value=self.build_provider_response(output)):
            self.mutate(protocol.invoke_assignment, {"id": "attempt-bar-second", "assignment_id": "bar-second"},
                        credential="test-only")
        self.mutate(decisions.record_value_review, dict(output, id="review-bar-second", dossier_id=None,
                                                        assignment_id="bar-second"))
        self.record_slate(review_ids=[])
        packet = json.loads(self.artifacts.read(self.assign("result", "first")["packet"]))
        [objection] = packet["material_objections"]
        mappings = {row["origin"]["locator"]["kind"]: row for row in objection["version_source_mappings"]}
        self.assertEqual(("citation" in mappings["text"], "citation" in mappings["json"]), (True, False))
        template = mappings["json"]["origin"]["artifact_descriptor"]
        built = {"version_id": mappings["json"]["version_id"], "source_id": mappings["json"]["origin"]["source_id"],
                 "artifact": {"path": template["location"], **{key: template[key] for key in ("sha256", "size", "media_type")}},
                 "locator": {"kind": "json", "pointer": mappings["json"]["origin"]["locator"]["pointer"],
                             "value": self.artifacts.read(mappings["json"]["excerpt"]).decode()}}
        for reviewer, cited in (("first", {"kind": "source", "link": built}),
                                ("second", {"kind": "source", "link": mappings["text"]["citation"]})):
            output = self.build_review_output("result")
            output["value"]["objection_findings"] = [dict(self.make_finding("contested"), evidence=[cited])]
            with self.subTest(reviewer=reviewer):
                if reviewer == "first":
                    with patch("research_harness.review_transport.send_request", return_value=self.build_provider_response(output)):
                        self.mutate(protocol.invoke_assignment, {"id": "attempt-result-first", "assignment_id": "result-first"},
                                    credential="test-only")
                    with self.assertRaises(ResearchError) as caught:
                        self.mutate(decisions.record_value_review, dict(output, id="review-result-first", dossier_id=None,
                                                                        assignment_id="result-first"))
                    self.assertEqual(caught.exception.code, "reading_missing")
                else:
                    self.assertEqual(self.record_result(reviewer, output)["id"], "review-result-second")

    def repair_the_first_bar_that_stated_o1(self):
        """Astra's F9 reproduction: the first bar states O1 and is repaired; the replacement resolves O1. Returns the
        second reviewer's first slate assignment."""
        self.prepare_problem_evidence()
        output = self.build_review_output("bar")
        output["objections"] = [self.make_objection()]
        self.observe("bar", "first", output)
        self.observe("bar", "second")
        self.replace_first_bar(findings=[self.make_finding("resolved")])
        return self.assign("slate", "second")

    def test_a_first_slate_after_another_bar_repair_receives_the_historical_objection(self):
        """R57: the slate packet delivers the objection on which the slate review must give a finding."""
        assignment = self.repair_the_first_bar_that_stated_o1()
        [objection] = json.loads(self.artifacts.read(assignment["packet"]))["material_objections"]
        stated = self.make_objection()
        self.assertEqual({key: objection[key] for key in ("id", "claim", "reason", "resolution_condition")},
                         {key: stated[key] for key in ("id", "claim", "reason", "resolution_condition")})
        self.assertEqual((objection["review_id"], objection["version_evidence"], objection["version_source_mappings"]),
                         ("review-bar-first", [self.supporting], json.loads(self.artifacts.read(
                             self.store.snapshot()["records"]["review_assignment"]["bar-first"]["packet"]))["prior_context"]))
        self.assertIn("material_version", objection)
        output = self.build_slate_output(["candidate-1"])
        output["value"]["bar_ids"] = json.loads(self.artifacts.read(assignment["packet"]))["bar_ids"]
        output["value"]["objection_findings"] = [self.make_finding("resolved")]
        with patch("research_harness.review_transport.send_request", return_value=self.build_provider_response(output)):
            self.mutate(protocol.invoke_assignment, {"id": "attempt-slate-second", "assignment_id": "slate-second"},
                        credential="test-only")
        saved = self.mutate(decisions.record_value_review, dict(output, id="review-slate-second", dossier_id=None,
                                                                assignment_id="slate-second"))["result"]
        self.assertEqual(saved["payload"]["value"]["objection_findings"], [self.make_finding("resolved")])

    def test_a_first_slate_without_a_finding_on_the_historical_objection_is_refused(self):
        assignment = self.repair_the_first_bar_that_stated_o1()
        output = self.build_slate_output(["candidate-1"])
        output["value"]["bar_ids"] = json.loads(self.artifacts.read(assignment["packet"]))["bar_ids"]
        with patch("research_harness.review_transport.send_request", return_value=self.build_provider_response(output)):
            self.mutate(protocol.invoke_assignment, {"id": "attempt-slate-second", "assignment_id": "slate-second"},
                        credential="test-only")
        with self.assertRaises(ResearchError) as caught:
            self.mutate(decisions.record_value_review, dict(output, id="review-slate-second", dossier_id=None,
                                                            assignment_id="slate-second"))
        self.assertEqual(caught.exception.code, "review_objection_history_incomplete")


class RecordedReviewEvidenceTests(delivery_fixtures.ProblemDeliveryCase):
    """R65 audits legacy records and refreshes only their affected reviewer slots."""

    prepare_reviews = objection_fixtures.ProblemObjectionTests.prepare_reviews
    prepare_problem_evidence = objection_fixtures.ProblemObjectionTests.prepare_problem_evidence
    make_objection = objection_fixtures.ProblemObjectionTests.make_objection
    make_finding = objection_fixtures.ProblemObjectionTests.make_finding
    build_slate_output = objection_fixtures.ProblemObjectionTests.build_slate_output
    observe_adjudication = objection_fixtures.ProblemObjectionTests.observe_adjudication
    add_unbound_full_source = ProblemAdjudicationResolutionTests.add_unbound_full_source
    read_packet = ProblemAdjudicationResolutionTests.read_packet
    invoke_focused = ProblemAdjudicationResolutionTests.invoke_focused

    def record_observed(self, assignment, output, *, legacy=False):
        self.invoke_focused(output, assignment['id'])
        payload = dict(output, id='review-' + assignment['id'], dossier_id=None, assignment_id=assignment['id'])
        if legacy:
            # The 0.55.0 path accepted this exact observation without the delivery check or an R60 refusal.
            with patch('research_harness.review_supplied_evidence.undelivered_references', return_value=[]):
                return self.mutate(decisions.record_value_review, payload)['result']
        return self.mutate(decisions.record_value_review, payload)['result']

    def prepare_legacy_reassessment(self):
        objection_fixtures.ProblemObjectionTests.prepare_repaired_result(self, changed=False, slot='second')
        link = self.add_unbound_full_source()
        reference = {'kind': 'source', 'link': link}
        output = self.build_review_output('result')
        output['value']['objection_findings'] = [dict(self.make_finding('continuing'), reason=self.make_objection()['reason'])]
        output['reassessment'] = {'assignment_id': 'result-second', 'findings': [{
            'review_id': 'review-result-second', 'disposition': 'revised',
            'reason': 'The earlier assessment remains supported.', 'evidence': [reference]}]}
        assignment = self.store.snapshot()['records']['review_assignment']['repaired-result']
        saved = self.record_observed(assignment, output, legacy=True)
        self.observe_adjudication(review_ids=['review-result-first', 'review-repaired-result'])
        records = self.store.snapshot()['records']
        request = json.loads(self.artifacts.read(records['review_attempt']['repaired-result-attempt']['request']))
        self.assertNotIn(link['artifact']['sha256'], json.dumps(request))
        self.assertNotIn(self.artifacts.read(link['artifact']).decode(), json.dumps(request))
        return saved, {'sha256': link['artifact']['sha256'], 'locator': link['locator']}

    def prepare_legacy_value(self, stage):
        self.prepare_problem_evidence()
        legacy = None
        for reviewer in ('first', 'second'):
            context = self.deliver_supporting(reviewer)
            assignment = self.assign('bar', reviewer, **context)
            output = self.build_review_output('bar')
            if stage == 'bar' and reviewer == 'first':
                artifact = self.artifacts.put(b'Undelivered legacy bar evidence.', 'text/plain')
                output['value']['evidence'] = [artifact]
                legacy = self.record_observed(assignment, output, legacy=True)
            else:
                self.record_observed(assignment, output)
        if stage == 'slate':
            assignment = self.assign('slate', 'first')
            artifact = self.artifacts.put(b'Undelivered legacy slate evidence.', 'text/plain')
            output = self.build_slate_output(['candidate-1'])
            output['value']['evidence'] = [artifact]
            legacy = self.record_observed(assignment, output, legacy=True)
        self.record_slate(review_ids=[] if stage == 'bar' else [legacy['id']])
        for reviewer in ('first', 'second'):
            self.observe('result', reviewer)
        return legacy, {'sha256': artifact['sha256']}

    def seed_legacy_post_decision(self):
        from research_harness.problem_claims import compute_problem_evidence_digest
        records = self.store.snapshot()['records']
        state = decisions.assess_problem_reviews(records, self.artifacts)
        self.put('problem_decision', 'legacy-decision', {'id': 'legacy-decision', 'action': 'post',
            'review_ids': state['review_ids'], 'bar_review_ids': state['bar_review_ids'],
            'problem_evidence_digest': compute_problem_evidence_digest(records, self.artifacts),
            'reason': 'The legacy post decision accepted the recorded pair.', 'recorded_revision': self.store.revision + 1})

    def assert_audited(self, review, details):
        from research_harness.gates import gate_state
        from research_harness.cli import status_report
        before = self.store.snapshot()
        records = before['records']
        reports = (decisions.assess_problem_reviews(records, self.artifacts),
                   gate_state(records, self.artifacts, 'post'), status_report(self.store))
        for report in reports:
            with self.subTest(report=report.get('orchestration', 'assessment')):
                self.assertFalse(report['ready'])
                matching = [row for row in report['obligations'] if row['code'] == 'review_evidence_undelivered']
                self.assertEqual([(row['review_id'], row['undelivered_references']) for row in matching],
                                 [(review['id'], [details])])
        self.assertEqual(self.store.snapshot(), before)
        self.assertEqual(records['value_review'][review['id']], review)
        self.assertEqual(records.get('review_output_refusal', {}), {})

    def refresh(self, review):
        records = self.store.snapshot()['records']
        prior = records['review_assignment'][review['assignment_id']]
        refreshed = self.assign(prior['role'], prior['reviewer_id'], identifier='evidence-refresh')
        packet = self.read_packet(refreshed['id'])
        self.assertEqual((refreshed['session_id'], refreshed['slot_id'], refreshed['history_assignment_id']),
                         (prior['session_id'], prior['slot_id'], prior['id']))
        self.assertEqual(packet['problem_refresh']['reason'], 'evidence_undelivered')
        self.assertIn(review['id'], packet['problem_refresh']['review_ids'])
        self.assertIn(review['id'], [row.get('payload', row)['id'] for row in packet['historical_reviews']])
        output = (self.build_slate_output(['candidate-1']) if prior['role'] == 'slate'
                  else self.build_review_output(prior['role']))
        if prior['role'] != 'bar':
            output['value']['bar_ids'] = packet['bar_ids']
        if prior['role'] == 'result':
            output['value']['objection_findings'] = [dict(self.make_finding('continuing'), reason=self.make_objection()['reason'])]
            output['reassessment'] = {'assignment_id': 'result-second', 'findings': [{
                'review_id': 'review-result-second', 'disposition': 'revised',
                'reason': 'The delivered comparator supports this finding.', 'evidence': [self.supporting]}]}
        saved = self.record_observed(refreshed, output)
        self.assertEqual(self.store.snapshot()['records']['value_review'][review['id']], review)
        self.assert_error('review_resampling_forbidden', lambda: self.assign(
            prior['role'], prior['reviewer_id'], identifier='unneeded-evidence-refresh'))
        return saved

    def assert_ready_post(self):
        from research_harness.gates import gate_state
        records = self.store.snapshot()['records']
        state = decisions.assess_problem_reviews(records, self.artifacts)
        self.assertTrue(state['ready'], state['obligations'])
        self.mutate(self.load_problem_module().record_decision, {'id': 'delivered-decision', 'action': 'post',
            'review_ids': state['review_ids'], 'bar_review_ids': state['bar_review_ids'],
            'reason': 'The refreshed pair uses delivered evidence.'})
        report = gate_state(self.store.snapshot()['records'], self.artifacts, 'post')
        self.assertTrue(report['ready'], report['obligations'])

    def test_legacy_reassessment_evidence_blocks_assessment_and_post_without_a_refusal(self):
        review, details = self.prepare_legacy_reassessment()
        self.seed_legacy_post_decision()
        self.assert_audited(review, details)

    def test_undelivered_result_refresh_preserves_session_and_history_then_clears_obligation(self):
        review, _ = self.prepare_legacy_reassessment()
        saved = self.refresh(review)
        state = decisions.assess_problem_reviews(self.store.snapshot()['records'], self.artifacts)
        self.assertIn(saved['id'], state['review_ids'])
        self.assertNotIn(review['id'], state['review_ids'])
        self.assert_ready_post()

    def test_legacy_bar_value_evidence_blocks_assessment_and_post(self):
        review, details = self.prepare_legacy_value('bar')
        self.seed_legacy_post_decision()
        self.assert_audited(review, details)

    def test_undelivered_bar_refresh_recovers_and_forbids_another_sample(self):
        review, _ = self.prepare_legacy_value('bar')
        saved = self.refresh(review)
        for reviewer in ('first', 'second'):
            output = self.build_review_output('result')
            output['value']['bar_ids'] = [saved['id'], 'review-bar-second']
            self.observe('result', reviewer, output, identifier='bar-refresh-result-' + reviewer)
        self.assert_ready_post()

    def test_legacy_slate_value_evidence_blocks_assessment_and_post(self):
        review, details = self.prepare_legacy_value('slate')
        self.seed_legacy_post_decision()
        self.assert_audited(review, details)

    def test_undelivered_slate_refresh_recovers_and_forbids_another_sample(self):
        review, _ = self.prepare_legacy_value('slate')
        self.refresh(review)
        self.assert_ready_post()

    def test_delivered_current_reviews_forbid_refresh_while_legacy_evidence_is_audited(self):
        self.prepare_reviews()
        state = decisions.assess_problem_reviews(self.store.snapshot()['records'], self.artifacts)
        self.assertTrue(state['ready'], state['obligations'])
        for stage in ('bar', 'result'):
            self.assert_error('review_resampling_forbidden', lambda: self.assign(stage, 'first', identifier='extra-' + stage))
        output = self.build_slate_output(['candidate-1'])
        self.observe('slate', 'first', output)
        self.assert_error('review_resampling_forbidden', lambda: self.assign('slate', 'first', identifier='extra-slate'))
        assignment = self.assign('slate', 'second')
        artifact = self.artifacts.put(b'Undelivered second slate evidence.', 'text/plain')
        output['value']['evidence'] = [artifact]
        review = self.record_observed(assignment, output, legacy=True)
        self.seed_legacy_post_decision()
        self.assert_audited(review, {'sha256': artifact['sha256']})

    def test_audit_uses_frozen_delivery_after_current_readings_are_removed_or_added(self):
        self.prepare_problem_evidence()
        link = self.add_unbound_full_source()
        context = self.deliver_supporting('first')
        assignment = self.assign('bar', 'first', source_links=context['source_links'] + [link])
        output = self.build_review_output('bar')
        output['value']['evidence'] = [{'kind': 'source', 'link': link}]
        self.record_observed(assignment, output)
        self.observe('bar', 'second')
        self.record_slate()
        for reviewer in ('first', 'second'):
            self.observe('result', reviewer)
        records = self.store.snapshot()['records']
        before = decisions.assess_problem_reviews(records, self.artifacts)
        self.assertTrue(before['ready'], before['obligations'])
        records['reading'].pop('read-unbound')
        self.assertEqual(decisions.assess_problem_reviews(records, self.artifacts), before)
        # A new reading cannot expand a different review's already frozen delivery.
        assignment = self.assign('slate', 'first')
        from research_harness import literature, reading
        version = self.metadata(3)
        capture = self.capture(version, body='A newly read source absent from the slate packet. References: none.')
        bundle = self.bundle(version, capture=capture, bundle_id='late-frozen-source')
        self.mutate(literature.import_bundle, bundle)
        self.mutate(reading.record_reading, self.full_note(bundle, 'late-frozen-reading'))
        late_reading = self.store.snapshot()['records']['reading']['late-frozen-reading']
        late_link = late_reading['inspections'][0]['link']
        output = self.build_slate_output(['candidate-1'])
        output['value']['evidence'] = [{'kind': 'source', 'link': late_link}]
        review = self.record_observed(assignment, output, legacy=True)
        current = self.store.snapshot()['records']
        current['reading'].pop('late-frozen-reading')
        missing = decisions.assess_problem_reviews(current, self.artifacts)
        current['reading']['late-frozen-reading'] = late_reading
        self.assertEqual(decisions.assess_problem_reviews(current, self.artifacts), missing)
        self.assertEqual([(row['review_id'], row['undelivered_references']) for row in missing['obligations']
                          if row['code'] == 'review_evidence_undelivered'],
                         [(review['id'], [{'sha256': late_link['artifact']['sha256'], 'locator': late_link['locator']}])])


class ReviewOutputRefusalTests(delivery_fixtures.ProblemDeliveryCase):
    """R60 recovery uses authenticated observed bytes, frozen delivery and two refused outputs."""

    prepare_reviews = objection_fixtures.ProblemObjectionTests.prepare_reviews
    prepare_problem_evidence = objection_fixtures.ProblemObjectionTests.prepare_problem_evidence
    make_objection = objection_fixtures.ProblemObjectionTests.make_objection
    make_finding = objection_fixtures.ProblemObjectionTests.make_finding
    build_slate_output = objection_fixtures.ProblemObjectionTests.build_slate_output

    def records(self):
        return self.store.snapshot()["records"]

    def prepare_resolved_bar_refresh(self, *, historical=False, source_request=False):
        """Retain a legacy result's packet after its delivered bar objection is resolved."""
        objection_fixtures.ProblemObjectionTests.prepare_version_one_bar_objection(self)
        original = protocol.canonical_prompt

        def previous_prompt(role, **options):
            if options.get("orchestration") == "problem_creation":
                options["version"] = 1
            return original(role, **options)

        with patch.object(protocol, "canonical_prompt", side_effect=previous_prompt), \
                patch.object(protocol, "require_calibrated_bars", return_value=None):
            for reviewer in ("second", "first"):
                output = self.build_review_output("result")
                del output["value"]["rubric"]
                del output["value"]["rubric_evidence"]
                output["value"]["objection_findings"] = [self.make_finding("continuing")]
                if historical and reviewer == "second":
                    output["objections"] = [dict(self.make_objection(), id="historical-result",
                        claim="The historical result leaves the boundary case unproved.")]
                if historical and reviewer == "first":
                    output["value"]["objection_findings"].append(
                        dict(self.make_finding("continuing"), id="historical-result"))
                if source_request and reviewer == "first":
                    output["source_requests"] = [{"id": "page", "kind": "inventory_page", "page": 0}]
                    output["value"]["status"] = "unresolved"
                self.observe("result", reviewer, output, record=not (source_request and reviewer == "first"))
                if historical and reviewer == "second":
                    self.mutate(protocol.record_context_event, {"id": "result-exposure", "assignment_id": "result-second",
                        "kind": "contamination", "source": "author_history", "reason": "The assessor received author advocacy.",
                        "evidence": [self.supporting]})
        self.assertEqual(objection_fixtures.ProblemObjectionTests.refresh_version_one_bars(
            self, [self.make_finding("resolved")]), [])

    def calibrated_result(self):
        return objection_fixtures.ProblemObjectionTests.build_calibrated_result(self)

    def test_refreshed_results_omit_resolved_material_objection_and_are_ready(self):
        self.prepare_resolved_bar_refresh()
        first_packet = json.loads(self.artifacts.read(self.records()["review_assignment"]["result-first"]["packet"]))
        self.assertEqual([row["id"] for row in first_packet["material_objections"]], ["unsupported-implication"])
        outcomes = {}
        for reviewer in ("first", "second"):
            payload = self.observe("result", reviewer, self.calibrated_result(), record=False,
                                   identifier="refreshed-" + reviewer)
            packet = json.loads(self.artifacts.read(self.records()["review_assignment"][payload["assignment_id"]]["packet"]))
            self.assertEqual(packet["material_objections"], [])
            self.assertEqual(payload["value"]["objection_findings"], [])
            try:
                self.mutate(decisions.record_value_review, payload)
                outcomes[reviewer] = "recorded"
            except ResearchError as error:
                outcomes[reviewer] = error.code
        self.assertEqual(outcomes, {"first": "recorded", "second": "recorded"})
        self.assertEqual(self.records().get("review_output_refusal", {}), {})
        assessment = decisions.assess_problem_reviews(self.records(), self.artifacts)
        self.assertTrue(assessment["ready"], assessment["obligations"])

    def check_retained_historical_result(self, *, source_request):
        self.prepare_resolved_bar_refresh(historical=True, source_request=source_request)
        first = self.records()["review_assignment"]["result-first"]
        first_packet = json.loads(self.artifacts.read(first["packet"]))
        [historical] = first_packet["historical_reviews"]
        self.assertEqual([row["id"] for row in historical["review"]["objections"]], ["historical-result"])
        if source_request:
            assignment = self.assign("result", "first", identifier="continued-first", prior_assignment_id=first["id"])
        else:
            assignment = self.assign("result", "first", identifier="refreshed-first")
        packet = json.loads(self.artifacts.read(assignment["packet"]))
        self.assertEqual(packet["material_objections"], [])
        self.assertFalse(any(review.get("review", review).get("objections") for review in packet.get("historical_reviews", [])))
        if source_request:
            self.assertEqual(packet.get("historical_reviews", []), [])
        output = self.calibrated_result()
        with self.assertRaises(ResearchError) as caught:
            decisions.validate_review_contract(self.records(), self.artifacts, assignment, output)
        self.assertEqual(caught.exception.code, "review_objection_history_incomplete")
        output["value"]["objection_findings"] = [dict(self.make_finding("resolved"), id="historical-result")]
        self.invoke_output(output, assignment["id"])
        self.assertIsNone(self.classifier()(self.records(), self.artifacts, "refused-1"))
        self.record_cli(output, assignment["id"])
        self.assertEqual(self.records().get("review_output_refusal", {}), {})

    def test_refreshed_result_retains_other_slots_delivered_historical_objection(self):
        self.check_retained_historical_result(source_request=False)

    def test_result_source_continuation_retains_first_packets_historical_objection(self):
        self.check_retained_historical_result(source_request=True)

    def test_status_refusal_listing_matches_state_without_evaluating_assignments(self):
        from research_harness import cli
        output = self.prepare_bar()
        self.refuse(output, attempt="z-first")
        self.refuse(output, attempt="a-second")
        records = self.records()
        expected = protocol.assignment_state(records, self.artifacts, "bar-first")["refused_attempts"]
        self.assertEqual(expected, [{"attempt_id": "z-first", "code": "review_evidence_undelivered"},
                                    {"attempt_id": "a-second", "code": "review_evidence_undelivered"}])
        self.assertEqual(cli.status_report(self.store)["review"]["assignments"]["bar-first"]["refused_attempts"], expected)
        with patch.object(protocol, "assignment_state", wraps=protocol.assignment_state) as state:
            report = cli._review_status(records, self.artifacts)
        self.assertEqual(state.call_count, 0, "_review_status must avoid assignment_state")
        state.assert_not_called()
        self.assertEqual(report["assignments"]["bar-first"]["refused_attempts"], expected)

    def prepare_bar(self):
        from research_harness import literature, reading
        self.initialize_problem()
        self.prepare_review_route()
        version = self.metadata(78001)
        bundle = self.bundle(version)
        self.mutate(literature.import_bundle, bundle)
        self.mutate(reading.record_reading, self.full_note(bundle))
        full = bundle["units"][0]["link"]
        self.supporting = full["artifact"]
        self.inside = self.link(version, full["source_id"], self.supporting, "The finite example")
        self.outside = self.link(version, full["source_id"], self.supporting, "References: none.")
        self.assign("bar", "first", source_links=[self.inside])
        output = self.build_review_output("bar")
        output["value"]["evidence"] = [{"kind": "source", "link": self.outside}]
        return output

    def invoke_output(self, output, assignment="bar-first", attempt="refused-1"):
        with patch("research_harness.review_transport.send_request", return_value=self.build_provider_response(output)):
            return self.mutate(protocol.invoke_assignment, {"id": attempt, "assignment_id": assignment}, credential="test-only")

    def record_cli(self, output, assignment="bar-first", command="value-review", request=None):
        from research_harness import cli
        payload = dict(output, id="finding-" + assignment, assignment_id=assignment)
        if command == "value-review":
            payload["dossier_id"] = None
        self.sequence += 1
        path = self.root / "refusal-payload.json"
        path.write_text(json.dumps(payload))
        args = cli.build_parser().parse_args([command, "--workspace", str(self.root), "--file", str(path),
            "--expected-revision", str(self.store.revision), "--request-id", request or "refusal-cli-" + str(self.sequence)])
        return cli.run(args)

    def refuse(self, output, assignment="bar-first", attempt="refused-1", command="value-review"):
        self.invoke_output(output, assignment, attempt)
        with self.assertRaises(ResearchError) as caught:
            self.record_cli(output, assignment, command)
        return caught.exception

    def assert_refused(self, attempts, assignment="bar-first"):
        state = protocol.assignment_state(self.records(), self.artifacts, assignment)
        self.assertEqual(state.get("refused_attempts"), [
            {"attempt_id": identifier, "code": "review_evidence_undelivered"} for identifier in attempts])
        self.assertIsNone(state["attempt_id"])
        self.assertIsNone(state["output"])
        self.assertIn("review_output_refused", {item["code"] for item in state["obligations"]})
        self.assertNotIn("review_output_pending", {item["code"] for item in state["obligations"]})
        return state

    def test_bar_refusal_is_immutable_and_repeated_recording_keeps_original_error(self):
        output = self.prepare_bar()
        error = self.refuse(output)
        self.assertEqual(error.code, "review_evidence_undelivered")
        records = self.records()
        refusal = records.get("review_output_refusal", {}).get("refused-1")
        self.assertIsNotNone(refusal)
        self.assertEqual((refusal["id"], refusal["assignment_id"], refusal["attempt_id"], refusal["code"]),
                         ("refused-1", "bar-first", "refused-1", "review_evidence_undelivered"))
        self.assertEqual(refusal["output_digest"], digest(output))
        self.assertEqual(refusal["request_digest"], records["review_attempt"]["refused-1"]["request_digest"])
        self.assertTrue(refusal["check"])
        self.assertTrue(refusal["protocol"])
        revision = self.store.revision
        with self.assertRaises(ResearchError):
            self.record_cli(output)
        self.assertEqual(self.store.revision, revision)
        self.assertEqual(self.records()["review_output_refusal"], {"refused-1": refusal})
        self.assertEqual(records["literature_operation"]["refusal-cli-" + str(self.sequence - 1) + ":refusal"]["operation"],
                         "review.refusal")

    def test_refused_bar_has_no_binding_attempt(self):
        self.refuse(self.prepare_bar())
        self.assert_refused(["refused-1"])

    def test_retry_reuses_exact_request_then_records_valid_output_and_keeps_refusal(self):
        output = self.prepare_bar()
        self.refuse(output)
        valid = json.loads(json.dumps(output))
        valid["value"]["evidence"] = [{"kind": "source", "link": self.inside}]
        self.invoke_output(valid, attempt="valid-2")
        attempts = self.records()["review_attempt"]
        self.assertEqual(attempts["refused-1"]["request_digest"], attempts["valid-2"]["request_digest"])
        self.assertEqual(attempts["refused-1"]["request"], attempts["valid-2"]["request"])
        self.record_cli(valid)
        state = protocol.assignment_state(self.records(), self.artifacts, "bar-first")
        self.assertEqual((state["attempt_id"], state["output"]), ("valid-2", valid))
        self.assertEqual(state["refused_attempts"], [{"attempt_id": "refused-1", "code": "review_evidence_undelivered"}])
        self.assertEqual(set(self.records()["review_output_refusal"]), {"refused-1"})

    def test_two_refused_outputs_exhaust_invocation(self):
        output = self.prepare_bar()
        self.refuse(output)
        self.refuse(output, attempt="refused-2")
        self.assert_refused(["refused-1", "refused-2"])
        with self.assertRaises(ResearchError) as caught:
            self.invoke_output(output, attempt="forbidden-3")
        self.assertEqual(caught.exception.code, "review_slot_exhausted")
        self.assertNotIn("forbidden-3", self.records()["review_invocation"])

    def test_exhausted_initial_bar_accepts_new_reviewer_but_never_same_reviewer(self):
        output = self.prepare_bar()
        self.refuse(output)
        self.refuse(output, attempt="refused-2")
        with self.assertRaises(ResearchError) as caught:
            self.assign("bar", " FIRST ", identifier="same-reviewer")
        self.assertEqual(caught.exception.code, "review_resampling_forbidden")
        replacement = self.assign("bar", "new-reviewer", identifier="replacement")
        self.assertEqual(replacement["slot_id"], "bar-first")
        self.assertEqual(len(self.records()["review_attempt"]), 2)

    def test_failed_only_initial_slot_also_prohibits_same_reviewer(self):
        output = self.prepare_bar()
        with patch("research_harness.review_transport.send_request", side_effect=TimeoutError("offline")):
            self.mutate(protocol.invoke_assignment, {"id": "failed", "assignment_id": "bar-first"}, credential="test-only")
        with self.assertRaises(ResearchError) as caught:
            self.assign("bar", "first", identifier="same-reviewer")
        self.assertEqual(caught.exception.code, "review_resampling_forbidden")
        self.assertEqual(self.assign("bar", "new-reviewer")["slot_id"], "bar-first")

    def exhaust_later_role(self, role):
        self.prepare_reviews(results=False)
        output = self.build_slate_output(["candidate-1"]) if role == "slate" else self.build_review_output(role)
        late = self.artifacts.put(b"Content created after freezing the scientific review inputs.", "text/plain")
        output["value"]["evidence"] = [late]
        assignment = self.assign(role, "first")["id"]
        self.refuse(output, assignment)
        self.refuse(output, assignment, "refused-2")
        self.assert_refused(["refused-1", "refused-2"], assignment)
        with self.assertRaises(ResearchError) as caught:
            self.assign(role, "third", identifier="replacement")
        self.assertEqual(caught.exception.code, "review_bar_continuity")
        self.assertNotIn("replacement", self.records()["review_assignment"])

    def test_exhausted_slate_preserves_bar_continuity(self):
        self.exhaust_later_role("slate")

    def test_exhausted_result_preserves_bar_continuity(self):
        self.exhaust_later_role("result")

    def test_adjudicator_undelivered_output_is_refused_and_retried_via_cli(self):
        self.prepare_reviews(result_reviewer="first")
        self.assign("adjudicator", "third", identifier="focused", review_ids=["review-result-first", "review-result-second"],
                    objection_id="unsupported-implication")
        output = {"objection_id": "unsupported-implication", "disposition": "not_upheld", "reason": "Exact evidence resolves it.",
            "evidence": [self.artifacts.put(b"Undelivered adjudicator content.", "text/plain")], "correction_admissible": None}
        self.assertEqual(self.refuse(output, "focused", command="review-adjudication").code, "review_evidence_undelivered")
        self.assert_refused(["refused-1"], "focused")
        output["evidence"] = [self.supporting]
        self.invoke_output(output, "focused", "valid-2")
        self.record_cli(output, "focused", "review-adjudication")
        self.assertIn("finding-focused", self.records()["review_adjudication"])
        attempts = self.records()["review_attempt"]
        self.assertEqual(attempts["refused-1"]["request_digest"], attempts["valid-2"]["request_digest"])
        with self.assertRaises(ResearchError) as caught:
            self.assign("adjudicator", "fourth", identifier="replacement", review_ids=["review-result-first", "review-result-second"],
                        objection_id="unsupported-implication")
        self.assertEqual(caught.exception.code, "review_adjudicator_fixed")

    def test_missing_saved_request_is_never_a_refusal_or_retry(self):
        self.prepare_reviews(results=False)
        output = self.build_review_output("result")
        self.assign("result", "first")
        self.invoke_output(output, "result-first")
        saved = self.records()["review_attempt"]["refused-1"]
        (self.root / saved["request"]["path"]).unlink()
        with self.assertRaises(ResearchError):
            self.record_cli(output, "result-first")
        self.assertEqual(self.records().get("review_output_refusal", {}), {})
        with self.assertRaises(ResearchError) as caught:
            self.invoke_output(output, "result-first", "forbidden-2")
        self.assertEqual(caught.exception.code, "review_resampling_forbidden")
        self.assertTrue(hasattr(protocol, "authenticate_attempt"), "R60 requires shared exact-attempt authentication")
        self.assertFalse(protocol.authenticate_attempt(self.records(), self.artifacts,
            self.records()["review_assignment"]["result-first"], saved))

    def test_malformed_observed_output_is_classified_from_stored_bytes(self):
        import importlib.util
        self.prepare_bar()
        output = {"stage": "bar", "value": {}}
        self.invoke_output(output)
        with self.assertRaises(ResearchError) as caught:
            self.record_cli({"stage": "bar", "value": {"operator": "substitution"}})
        self.assertEqual(caught.exception.code, "invalid_value_review")
        self.assertIsNotNone(importlib.util.find_spec("research_harness.review_refusal"), "R60 classifier missing")
        from research_harness.review_refusal import classify_observed_output
        failure = classify_observed_output(self.records(), self.artifacts, "refused-1")
        self.assertEqual(failure["code"], "invalid_value_review")
        self.assertEqual(self.records()["review_output_refusal"]["refused-1"]["output_digest"], digest(output))

    def test_status_lists_refused_attempts_and_usage_counts_both_calls(self):
        from research_harness.cli import status_report
        output = self.prepare_bar()
        self.refuse(output)
        self.refuse(output, attempt="refused-2")
        report = status_report(self.store)
        self.assertEqual(report["review"]["assignments"]["bar-first"]["refused_attempts"], [
            {"attempt_id": "refused-1", "code": "review_evidence_undelivered"},
            {"attempt_id": "refused-2", "code": "review_evidence_undelivered"}])
        usage = protocol.usage_report(self.records())
        self.assertEqual(usage["calls"], 3)  # One route probe, both observed refusals.
        self.assertEqual(usage["observed"]["input_tokens"], 111)

    def test_recheck_restores_binding_and_requires_recording_if_contract_becomes_recordable(self):
        """Frozen packets cannot legitimately change in place. Simulate a future pure-validator correction only."""
        output = self.prepare_bar()
        self.refuse(output)
        self.assert_refused(["refused-1"])
        # No packet, output, response or request is changed; this is the direct classification recheck R60 requests.
        from research_harness.review_refusal import classify_observed_output
        with patch.object(decisions, "validate_review_contract", return_value=None):
            self.assertIsNone(classify_observed_output(self.records(), self.artifacts, "refused-1"))
            state = protocol.assignment_state(self.records(), self.artifacts, "bar-first")
            self.assertEqual((state["attempt_id"], state["output"], state["refused_attempts"]), ("refused-1", output, []))
            with self.assertRaises(ResearchError) as caught:
                self.invoke_output(output, attempt="forbidden-2")
            self.assertEqual(caught.exception.code, "review_resampling_forbidden")
            self.assertIn("record the observed output", str(caught.exception).lower())
        self.assertIn("refused-1", self.records()["review_output_refusal"])

    def test_classifier_excludes_current_readings_and_never_uses_own_assignment_state(self):
        import importlib.util
        output = self.prepare_bar()
        output["value"]["evidence"] = [{"kind": "source", "link": self.inside}]
        self.invoke_output(output)
        records = self.records()
        records["reading"] = {}
        self.assertIsNotNone(importlib.util.find_spec("research_harness.review_refusal"), "R60 classifier missing")
        from research_harness.review_refusal import classify_observed_output
        with patch.object(protocol, "assignment_state", side_effect=AssertionError("recursive own state")):
            self.assertIsNone(classify_observed_output(records, self.artifacts, "refused-1"))
        with self.assertRaises(ResearchError) as caught:
            from research_harness.strategy import evidence
            evidence(records, self.artifacts, output["value"]["evidence"])
        self.assertEqual(caught.exception.code, "reading_missing")

    def test_valid_pending_source_request_is_not_a_refusal(self):
        import importlib.util
        output = self.prepare_bar()
        output["value"].update(status="unresolved", evidence=[])
        output["source_requests"] = [{"id": "next", "kind": "inventory_page", "page": 0}]
        self.invoke_output(output)
        with self.assertRaises(ResearchError) as caught:
            self.record_cli(output)
        self.assertEqual(caught.exception.code, "review_sources_pending")
        self.assertEqual(self.records().get("review_output_refusal", {}), {})
        self.assertIsNotNone(importlib.util.find_spec("research_harness.review_refusal"), "R60 classifier missing")
        from research_harness.review_refusal import classify_observed_output
        self.assertIsNone(classify_observed_output(self.records(), self.artifacts, "refused-1"))

    def classifier(self):
        import importlib.util
        self.assertIsNotNone(importlib.util.find_spec("research_harness.review_refusal"), "R60 classifier missing")
        from research_harness.review_refusal import classify_observed_output
        return classify_observed_output

    def test_classifier_covers_source_request_shape(self):
        output = self.prepare_bar()
        output["source_requests"] = "not-an-array"
        self.invoke_output(output)
        classify = self.classifier()
        self.assertEqual(classify(self.records(), self.artifacts, "refused-1")["code"], "invalid_review_source_request")

    def classify_result_change(self, change):
        self.prepare_reviews(bar_reviewer="first", results=False)
        self.assign("result", "first")
        output = self.build_review_output("result")
        output["value"]["objection_findings"] = [self.make_finding("continuing")]
        change(output)
        self.invoke_output(output, "result-first")
        return self.classifier()(self.records(), self.artifacts, "refused-1")

    def test_classifier_covers_rubric_ranges(self):
        failure = self.classify_result_change(lambda value: value["value"]["rubric"].update(priority=9))
        self.assertEqual(failure["code"], "invalid_rubric_grades")

    def test_classifier_requires_two_delivered_bars(self):
        failure = self.classify_result_change(lambda value: value["value"].update(bar_ids=["review-bar-first"]))
        self.assertEqual(failure["code"], "review_bar_pending")

    def test_classifier_rejects_bar_ids_outside_frozen_packet(self):
        failure = self.classify_result_change(lambda value: value["value"].update(bar_ids=["review-bar-first", "unknown"]))
        self.assertEqual(failure["code"], "review_bar_mismatch")

    def test_classifier_requires_findings_on_delivered_objections(self):
        failure = self.classify_result_change(lambda value: value["value"].update(objection_findings=[]))
        self.assertEqual(failure["code"], "review_objection_history_incomplete")

    def test_classifier_uses_frozen_work_item_contract_without_lookup(self):
        failure = self.classify_result_change(lambda value: value["value"].update(work_items=[{
            "id": "unknown", "classification": "validity", "status": "accepted", "reason": "Not a delivered work item.",
            "evidence": [self.supporting]}]))
        self.assertEqual(failure["code"], "review_work_items_incomplete")

    def test_classifier_does_not_turn_missing_delivered_bytes_into_locator_refusal(self):
        output = self.prepare_bar()
        output["value"]["evidence"] = [{"kind": "source", "link": self.inside}]
        output["value"]["evidence"][0]["link"] = dict(self.inside, locator={"kind": "span", "start": 0, "end": 999999, "sha256": "bad"})
        self.invoke_output(output)
        classify = self.classifier()
        self.assertEqual(classify(self.records(), self.artifacts, "refused-1")["code"], "invalid_locator")
        (self.root / self.supporting["path"]).unlink()
        self.assertIsNone(classify(self.records(), self.artifacts, "refused-1"))

    def test_malformed_work_item_id_is_a_refusal_without_live_lookup(self):
        output = self.prepare_bar()
        output["value"]["evidence"] = [{"kind": "source", "link": self.inside}]
        output["value"]["work_items"] = [{"id": [], "classification": "validity", "status": "accepted",
            "reason": "Malformed observed identity.", "evidence": [self.supporting]}]
        error = self.refuse(output)
        self.assertEqual(error.code, "invalid_value_review")
        self.assertEqual(self.records()["review_output_refusal"]["refused-1"]["code"], "invalid_value_review")

    def test_malformed_record_reference_id_is_a_refusal_without_live_lookup(self):
        output = self.prepare_bar()
        output["value"]["evidence"] = [{"kind": "record", "record_kind": "value_review", "id": [], "digest": "bad"}]
        error = self.refuse(output)
        self.assertEqual(error.code, "invalid_strategy")
        self.assertEqual(self.records()["review_output_refusal"]["refused-1"]["code"], "invalid_strategy")

    def test_delivery_classification_uses_frozen_bounds_when_live_work_lookup_changes(self):
        self.refuse(self.prepare_bar())
        classify = self.classifier()
        records = self.records()
        records["work"] = {}
        self.assertEqual(classify(records, self.artifacts, "refused-1")["code"], "review_evidence_undelivered")

    def test_retry_checks_completed_request_digest_before_reserving(self):
        output = self.prepare_bar()
        self.refuse(output)
        attempt = self.records()["review_attempt"]["refused-1"]
        self.put("review_attempt", "refused-1", dict(attempt, request_digest="different-request"))
        with self.assertRaises(ResearchError) as caught:
            self.invoke_output(output, attempt="forbidden-2")
        self.assertEqual(caught.exception.code, "review_request_changed")
        self.assertNotIn("forbidden-2", self.records()["review_invocation"])

    def test_malformed_artifact_descriptor_is_refused_before_byte_availability(self):
        output = self.prepare_bar()
        output["value"]["evidence"] = [dict(self.supporting, size="bad-size")]
        self.assertEqual(self.refuse(output).code, "invalid_input")
        self.assertEqual(self.records()["review_output_refusal"]["refused-1"]["code"], "invalid_input")
        output["value"]["evidence"] = [{"kind": "source", "link": dict(self.inside,
            artifact=dict(self.inside["artifact"], size="bad-size"))}]
        self.assertEqual(self.refuse(output, attempt="refused-2").code, "invalid_input")
        self.assertEqual(self.records()["review_output_refusal"]["refused-2"]["code"], "invalid_input")

    def test_malformed_source_identity_is_classified_before_record_lookup(self):
        output = self.prepare_bar()
        output["value"]["evidence"] = [{"kind": "source", "link": dict(self.inside, source_id=[])}]
        self.assertEqual(self.refuse(output).code, "source_mismatch")
        self.assertEqual(self.records()["review_output_refusal"]["refused-1"]["code"], "source_mismatch")

    def test_native_adjudicator_retry_preserves_existing_evidence_contract(self):
        import test_research_review_protocol as native
        case = native.ReviewProtocolTests("test_inadmissible_correction_is_preserved_without_clearing_the_finding")
        case.setUp()
        self.addCleanup(case.doCleanups)
        p = case.protocol()
        case.bar_reviews()
        case.mutate(p.record_assignment, {"id": "focused", "route_id": "route", "dossier_id": "dossier", "role": "adjudicator",
            "reviewer_id": "third", "author_id": "author", "context": {"review_ids": ["review-first", "review-second"],
                "objection_id": "objection-first"}})
        output = {"objection_id": "wrong-objection", "disposition": "unresolved", "reason": "The focused finding remains unresolved.",
            "evidence": [], "correction_admissible": None}
        case.invoke(output, "bad-native", "focused")
        with self.assertRaises(ResearchError) as caught:
            case.mutate(p.record_adjudication, dict(output, id="finding", assignment_id="focused"))
        self.assertEqual(caught.exception.code, "review_adjudication_mismatch")
        self.assertIn("bad-native", case.records().get("review_output_refusal", {}))
        output["objection_id"] = "objection-first"
        case.invoke(output, "valid-native", "focused")
        case.mutate(p.record_adjudication, dict(output, id="finding", assignment_id="focused"))
        self.assertEqual(case.records()["review_adjudication"]["finding"]["evidence"], [])

    def test_legacy_record_lookup_failure_cannot_create_a_pure_refusal(self):
        self.prepare_reviews(results=False)
        with patch.object(protocol, "deliver_review", side_effect=lambda records, payload: payload):
            self.assign("result", "first")
        bar = self.records()["value_review"]["review-bar-second"]
        output = self.build_review_output("result")
        output["value"]["evidence"] = [{"kind": "record", "record_kind": "value_review", "id": bar["id"], "digest": digest(bar)}]
        self.invoke_output(output, "result-first")
        classify = self.classifier()
        self.assertIsNone(classify(self.records(), self.artifacts, "refused-1"))
        records = self.records()
        del records["value_review"][bar["id"]]
        self.assertIsNone(classify(records, self.artifacts, "refused-1"))

    def test_generic_pdf_format_qualification_is_not_an_output_refusal(self):
        self.classifier()
        from research_harness.review_refusal import validate_evidence_contract
        original = self.artifacts.put(b"%PDF-1.4\n% Authored generic-binary fixture.\n%%EOF", "application/octet-stream")
        page_map = self.artifacts.put(b"One extracted page.", "text/plain")
        link = {"version_id": "arxiv:2601.78001v1", "source_id": "original", "artifact": original,
            "locator": {"kind": "pdf", "page_index": 0, "printed_page": None, "region": [0, 0, 1, 1]}}
        packet = {"source_deliveries": [{"request": {"version_id": link["version_id"]}, "evidence": [original, page_map],
            "source_provenance": [{"source_id": "original", "original_descriptor": {"sha256": original["sha256"]},
                "delivered_sha256": [original["sha256"], page_map["sha256"]]}]}]}
        # The frozen delivery has no format-inference qualification. Current native capture qualification remains state.
        self.assertIsNone(validate_evidence_contract({}, self.artifacts, [{"kind": "source", "link": link}], packets=[packet]))
