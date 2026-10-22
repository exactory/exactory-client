"""A focused adjudicator receives complete current sources without changing original correction scope."""

import copy
import json
from unittest.mock import patch

from research_harness import problem_candidates
from research_harness import review_protocol as protocol, review_transport as transport
from research_harness.errors import ResearchError
import test_research_problem_delivery as delivery_fixtures
import test_research_problem_adjudication_contract as contract_fixtures


class AdjudicatorCurrentSourceTests(delivery_fixtures.ProblemDeliveryCase):
    prepare_reviews = contract_fixtures.ProblemAdjudicationContractTests.prepare_reviews
    prepare_problem_evidence = contract_fixtures.ProblemAdjudicationContractTests.prepare_problem_evidence
    make_objection = contract_fixtures.ProblemAdjudicationContractTests.make_objection
    make_finding = contract_fixtures.ProblemAdjudicationContractTests.make_finding
    add_new_source = contract_fixtures.ProblemAdjudicationContractTests.add_new_source
    assign_focused_review = contract_fixtures.ProblemAdjudicationContractTests.assign_focused_review
    observe_focused_review = contract_fixtures.ProblemAdjudicationContractTests.observe_focused_review
    build_correction = contract_fixtures.ProblemAdjudicationContractTests.build_correction
    read_records = contract_fixtures.ProblemAdjudicationContractTests.read_records

    def prepare_current_comparator(self):
        self.prepare_reviews(result_reviewer="first")
        self.new_source = self.add_new_source()
        assignment = self.assign_focused_review()
        packet = json.loads(self.artifacts.read(assignment["packet"]))
        return assignment, packet

    def test_actual_focused_input_contains_complete_current_source_and_current_citation_works(self):
        assignment, packet = self.prepare_current_comparator()
        self.assertIn(self.new_source, packet["current_evidence"])
        self.assertNotIn(self.new_source, packet["original_evidence"])
        manifest = packet["current_source_delivery"]
        self.assertEqual(manifest["protocol"], "focused-current-sources-v1")
        self.assertEqual({row["id"] for row in manifest["readings"]}, {"read-full", "read-new-comparator"})
        request = transport.request_body(self.read_records()["review_route"][assignment["route_id"]],
            assignment["prompt"]["text"], packet, assignment.get("history", ()))
        actual = json.loads(transport.logical_messages(request)[-1]["content"].removeprefix(transport.PACKET_LABEL))
        body = next(row for row in actual["evidence"] if row["artifact"] == self.new_source)
        self.assertEqual(body["encoding"], "utf-8")
        self.assertEqual(body["content"].encode("utf-8"), self.artifacts.read(self.new_source))
        self.assertIn("A new comparator B proves the previously missing implication.", body["content"])
        for identifier in ("result-first", "result-second"):
            original = json.loads(self.artifacts.read(self.read_records()["review_assignment"][identifier]["packet"]))
            self.assertNotIn(self.new_source, [row["artifact"] for row in original["evidence"]])
        saved = self.observe_focused_review(self.new_source)
        self.assertTrue(protocol.adjudication_state(self.read_records(), self.artifacts, saved["id"])["ready"])
        self.assert_error("invalid_review_correction", lambda: self.assign("adjudicator", "another-focused",
            identifier="invalid-new-correction", review_ids=["review-result-first", "review-result-second"],
            objection_id="unsupported-implication", correction=self.build_correction(self.new_source)))

    def test_descriptors_or_altered_bytes_cannot_satisfy_current_source_delivery(self):
        from research_harness import review_adjudicator_sources as current
        _, packet = self.prepare_current_comparator()
        for label in ("missing", "descriptor only", "altered", "false completion",
                      "missing unit requirement", "wrong requested version", "unbacked main"):
            with self.subTest(label=label):
                changed = copy.deepcopy(packet)
                if label == "false completion":
                    changed["current_source_delivery"]["sources"][0]["source_scope_pending"] = [
                        {"code": "required_source_unit_missing", "unit_id": "figure-1"}]
                elif label == "missing unit requirement":
                    changed["current_source_delivery"]["required_units"].pop()
                elif label == "wrong requested version":
                    changed["current_source_delivery"]["sources"][0]["request"]["version_id"] = "arxiv:2601.99999v1"
                elif label == "unbacked main":
                    changed["current_source_delivery"]["sources"][0]["source_alternatives"] = []
                else:
                    entry = next(row for row in changed["evidence"] if row["artifact"] == self.new_source)
                    if label == "missing":
                        changed["evidence"].remove(entry)
                    elif label == "descriptor only":
                        entry.pop("content")
                    else:
                        entry["content"] += "altered"
                self.assert_error("review_current_source_incomplete", lambda: current.validate(self.artifacts, changed))
        current.validate(self.artifacts, packet)

    def test_actual_authenticated_input_cannot_omit_a_required_body_present_in_the_saved_packet(self):
        assignment, packet = self.prepare_current_comparator()
        self.assertTrue(any(row["artifact"] == self.new_source and row.get("content")
                            for row in packet["evidence"]))
        original = transport.request_body
        def elide_current_body(route, prompt, frozen, history=()):
            request = original(route, prompt, frozen, history)
            if "current_source_delivery" in frozen:
                messages = transport.logical_messages(request)
                actual = json.loads(messages[-1]["content"].removeprefix(transport.PACKET_LABEL))
                entry = next(row for row in actual["evidence"] if row["artifact"] == self.new_source)
                entry.pop("content")
                entry["encoding"] = "omitted"
                entry["delivery"] = "body-elided-route-limit"
                messages[-1]["content"] = transport.PACKET_LABEL + json.dumps(actual, sort_keys=True, ensure_ascii=False)
                request["input"] = messages
            return request
        with patch.object(transport, "request_body", side_effect=elide_current_body):
            self.assert_error("review_current_source_incomplete", lambda: self.observe_focused_review(self.supporting))
            state = protocol.assignment_state(self.read_records(), self.artifacts, assignment["id"])
            self.assertTrue(state["ready"], state["obligations"])
            attempt = self.read_records()["review_attempt"][state["attempt_id"]]
            actual_request = json.loads(self.artifacts.read(attempt["request"]))
            actual = json.loads(transport.logical_messages(actual_request)[-1]["content"].removeprefix(transport.PACKET_LABEL))
            entry = next(row for row in actual["evidence"] if row["artifact"] == self.new_source)
            self.assertEqual(entry["encoding"], "omitted")
            self.assertNotIn("content", entry)
            self.assertNotIn("focused-finding", self.read_records().get("review_adjudication", {}))
            self.assertEqual(self.read_records().get("review_output_refusal", {}), {})
            legacy = dict(state["output"], id="legacy-incomplete", assignment_id=assignment["id"], dossier_id=None)
            records = copy.deepcopy(self.read_records())
            records.setdefault("review_adjudication", {})[legacy["id"]] = legacy
            saved_state = protocol.adjudication_state(records, self.artifacts, legacy["id"])
            self.assertFalse(saved_state["ready"])
            self.assertIn("review_current_source_incomplete", {row["code"] for row in saved_state["obligations"]})

    def test_binary_units_require_actual_attachments_but_original_provenance_alone_does_not(self):
        from research_harness import review_adjudicator_sources as current
        assignment, frozen = self.prepare_current_comparator()
        route = self.read_records()["review_route"][assignment["route_id"]]
        binary = self.artifacts.put(b"%PDF-1.7\nExact required visual unit bytes.\n%%EOF\n", "application/pdf")
        manifest = frozen["current_source_delivery"]
        original_unit = copy.deepcopy(manifest["required_units"][0])
        manifest["required_units"][0]["artifact"] = binary
        manifest["sources"][0]["evidence"].append(binary)
        frozen["current_evidence"].append(binary)
        frozen["evidence"] = protocol._evidence(self.artifacts, frozen)
        current.validate(self.artifacts, frozen)
        actual = transport.request_body(route, assignment["prompt"]["text"], frozen)
        self.assertIsInstance(actual["input"][-1]["content"], list)
        current.validate_request(self.artifacts, frozen, actual)
        altered_attachment = copy.deepcopy(actual)
        attached = next(row for row in altered_attachment["input"][-1]["content"] if row["type"] == "input_file")
        attached["file_data"] = "data:application/pdf;base64,YWx0ZXJlZA=="
        self.assert_error("review_current_source_incomplete",
                          lambda: current.validate_request(self.artifacts, frozen, altered_attachment))
        missing_file = copy.deepcopy(actual)
        missing_file["input"][-1]["content"] = [row for row in missing_file["input"][-1]["content"]
                                                   if row["type"] == "input_text"]
        self.assert_error("review_current_source_incomplete",
                          lambda: current.validate_request(self.artifacts, frozen, missing_file))
        for label in ("retained attached content", "raw base64 content", "raw utf-8 binary content"):
            with self.subTest(label=label):
                retained = copy.deepcopy(missing_file)
                text = retained["input"][-1]["content"][0]
                actual_packet = json.loads(text["text"].removeprefix(transport.PACKET_LABEL))
                actual_entry = next(row for row in actual_packet["evidence"] if row["artifact"] == binary)
                actual_entry["content"] = next(row["content"] for row in frozen["evidence"] if row["artifact"] == binary)
                if label == "raw base64 content":
                    actual_entry.pop("delivery")
                elif label == "raw utf-8 binary content":
                    actual_entry["encoding"] = "utf-8"
                    actual_entry["content"] = self.artifacts.read(binary).decode("utf-8")
                text["text"] = transport.PACKET_LABEL + json.dumps(actual_packet, sort_keys=True, ensure_ascii=False)
                self.assert_error("review_current_source_incomplete",
                                  lambda: current.validate_request(self.artifacts, frozen, retained))
        local = dict(route, adapter="codex_cli_v1", endpoint="codex://local")
        omitted = transport.request_body(local, assignment["prompt"]["text"], frozen)
        self.assert_error("review_current_source_incomplete",
                          lambda: current.validate_request(self.artifacts, frozen, omitted))
        manifest["required_units"][0] = original_unit
        manifest["sources"][0]["evidence"].remove(binary)
        frozen["current_evidence"].remove(binary)
        manifest["sources"][0]["source_provenance"].append({
            "original_descriptor": protocol.bar_sources.descriptor(binary), "delivery_mode": "extracted-text-only"})
        extraction_only = transport.request_body(local, assignment["prompt"]["text"], frozen)
        current.validate_request(self.artifacts, frozen, extraction_only)

    def test_missing_required_source_unit_blocks_assignment_before_model_observation(self):
        self.prepare_reviews(result_reviewer="first")
        self.add_new_source()
        original = protocol.bar_sources._source
        def missing_unit(*args, **kwargs):
            delivery = original(*args, **kwargs)
            if delivery["request"]["version_id"] == "arxiv:2601.00002v1":
                delivery["status"] = "partial"
                delivery["source_scope_pending"] = [{"code": "required_source_unit_missing", "unit_id": "figure-1"}]
            return delivery
        with patch.object(protocol.bar_sources, "_source", side_effect=missing_unit), \
                patch("research_harness.review_transport.send_request") as send:
            self.assert_error("review_current_source_incomplete", self.assign_focused_review)
        send.assert_not_called()
        self.assertNotIn("focused", self.read_records().get("review_assignment", {}))

    def test_missing_current_bytes_block_recording_even_when_output_cites_another_delivered_artifact(self):
        from research_harness import review_adjudicator_sources as current
        self.prepare_reviews(result_reviewer="first")
        self.new_source = self.add_new_source()
        original = protocol.build_packet
        def forged(*args, **kwargs):
            packet = original(*args, **kwargs)
            packet["evidence"] = [row for row in packet["evidence"] if row["artifact"] != self.new_source]
            packet["digest"] = protocol.digest({key: value for key, value in packet.items() if key != "digest"})
            return packet
        with patch.object(protocol, "build_packet", side_effect=forged):
            self.assign_focused_review()
        self.assert_error("review_current_source_incomplete", lambda: self.observe_focused_review(self.supporting))
        self.assertNotIn("focused-finding", self.read_records().get("review_adjudication", {}))
        self.assertEqual(self.read_records().get("review_output_refusal", {}), {})
