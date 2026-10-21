"""A problem packet delivers each inspected span once, as inline text that the coverage check verifies."""

import copy
import json

from test_research_problem_claims import ProblemCase
from research_harness import direction_sources, problem_evidence, review_protocol, review_transport
from research_harness.literature import import_bundle
from research_harness.reading import record_reading


class InlinePassageTests(ProblemCase):
    def setUp(self):
        super().setUp()
        self.initialize_problem()
        self.select_statement()
        self.version = self.metadata()
        self.source_bundle = self.bundle(self.version)
        self.mutate(import_bundle, self.source_bundle)
        self.note = self.full_note(self.source_bundle, "read-full")
        self.mutate(record_reading, self.note)
        self.mutate(self.load_problem_module().record_value,
                    self.build_value([{"kind": "reading", "version_id": self.version, "reading_id": "read-full"}]))
        self.records = self.store.snapshot()["records"]
        self.reading = self.records["reading"]["read-full"]
        self.text = self.artifacts.read(self.source_bundle["units"][0]["link"]["artifact"]).decode()

    def test_a_whole_text_inspection_yields_no_passage_and_a_span_yields_its_exact_text(self):
        passages = direction_sources.build_inline_passages(self.records, self.artifacts, [self.reading])
        indices = sorted(passage["inspection_index"] for passage, _ in passages)
        self.assertNotIn(0, indices)  # the body unit covers the complete text
        self.assertTrue(indices)
        for passage, data in passages:
            self.assertIsNone(data)
            inspection = self.reading["inspections"][passage["inspection_index"]]
            locator = inspection["link"]["locator"]
            self.assertEqual((passage["start"], passage["end"]), (locator["start"], locator["end"]))
            self.assertEqual(passage["text"], self.text[locator["start"]:locator["end"]])
            self.assertEqual(passage["reading_id"], "read-full")
            self.assertEqual(passage["version_id"], self.version)

    def packets(self, passages):
        return [{"direction_evidence": {"passages": [passage for passage, _ in passages]}, "evidence": []}]

    def test_inline_passages_cover_the_reading_and_any_change_breaks_the_coverage(self):
        passages = direction_sources.build_inline_passages(self.records, self.artifacts, [self.reading])
        delivered = direction_sources.find_delivered_readings(self.records, self.artifacts, [self.reading],
                                                              self.packets(passages), whole_text=False)
        self.assertEqual(delivered, {"read-full"})
        # The same packet does not cover the reading when the whole text is still required as chunks.
        self.assertEqual(direction_sources.find_delivered_readings(self.records, self.artifacts, [self.reading],
                                                                   self.packets(passages)), set())
        for label, change in (("changed character", lambda p: p.update(text=("X" if p["text"][0] != "X" else "Y") + p["text"][1:])),
                              ("shifted offsets", lambda p: p.update(start=p["start"] + 1, end=p["end"] + 1)),
                              ("an inspection that does not contain the span", lambda p: p.update(inspection_index=2)),
                              ("other reading", lambda p: p.update(reading_id="read-other"))):
            with self.subTest(change=label):
                tampered = copy.deepcopy(passages)
                change(tampered[0][0])
                self.assertEqual(direction_sources.find_delivered_readings(
                    self.records, self.artifacts, [self.reading], self.packets(tampered), whole_text=False), set())

    def test_a_reading_inspected_only_as_a_whole_needs_its_attached_full_text(self):
        whole = dict(self.reading, inspections=[self.reading["inspections"][0]])
        self.assertEqual(direction_sources.build_inline_passages(self.records, self.artifacts, [whole]), [])
        bare = [{"direction_evidence": {"passages": []}, "evidence": []}]
        self.assertEqual(direction_sources.find_delivered_readings(self.records, self.artifacts, [whole], bare,
                                                                   whole_text=False), set())
        artifact = whole["inspections"][0]["link"]["artifact"]
        attached = [{"direction_evidence": {"passages": []},
                     "evidence": [{"artifact": artifact, "content": self.text, "encoding": "utf-8"}]}]
        self.assertEqual(direction_sources.find_delivered_readings(self.records, self.artifacts, [whole], attached,
                                                                   whole_text=False), {"read-full"})

    def test_the_problem_evidence_names_full_texts_and_delivers_spans_once(self):
        evidence = problem_evidence.describe_evidence(self.records, self.artifacts)
        self.assertEqual([row["id"] for row in evidence["readings"]], ["read-full"])
        row = evidence["readings"][0]
        self.assertEqual(row["cite"], {"kind": "record", "record_kind": "reading", "id": "read-full"})
        self.assertNotIn("link", json.dumps(row["reading"]["inspections"][0]))
        self.assertIs(row["reading"]["inspections"][0]["whole_text"], True)
        self.assertEqual([entry["whole_text"] for entry in row["reading"]["inspections"]][1:], [False, False])
        self.assertEqual(row["reading"]["notes"], self.reading["notes"])
        self.assertEqual(list(review_protocol.collect_references(evidence["readings"])), [])
        self.assertTrue(evidence["passages"])
        self.assertTrue(all("text" in passage for passage in evidence["passages"]))
        self.assertEqual(evidence["delivery"]["protocol"], "inline-passages-v1")
        self.assertIn("source_requests", evidence["delivery"]["note"])
        self.assertEqual(evidence["claim"]["cite"], {"kind": "record", "record_kind": "problem_statement", "id": "statement-1"})
        self.assertEqual(evidence["value"]["cite"], {"kind": "record", "record_kind": "problem_value", "id": "value-1"})

    def test_a_packet_with_problem_evidence_asks_for_a_compact_replay(self):
        packet = {"subject": {"kind": "problem", "statement_id": "statement-1"}}
        review_protocol.add_problem_evidence(self.records, self.artifacts, packet)
        self.assertEqual(packet["history_delivery"], review_transport.HISTORY_COMPACT_V2)
        self.assertEqual(packet["problem_evidence"]["readings"][0]["id"], "read-full")
        self.assertEqual(packet["problem_evidence_digest"],
                         problem_evidence.digest(problem_evidence.describe_binding(self.records, self.artifacts)))
