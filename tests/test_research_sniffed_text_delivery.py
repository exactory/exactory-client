"""Undeclared text that merely begins like JSON is delivered as exact text."""

from literature_fixtures import LiteratureCase
from research_harness.errors import ResearchError
from research_harness.review_delivery import _deliver
from research_harness.scientific_delivery import project_delivery
from research_harness.scientific_json import scientific_json


_TRANSCRIPT = ("[1803.00959v1] Robustness of the Insulating Bulk (https://arxiv.org/abs/1803.00959v1)\n"
               "L2: Search arXiv [Input: Search papers by title] [Input] [Input]\nL8: [Submitted on 2 Mar 2018]\n")


class SniffedTextDeliveryTests(LiteratureCase):
    def test_undeclared_text_that_starts_like_json_is_not_json(self):
        for data, encoded in ((_TRANSCRIPT.encode(), False), (b"{not json", False), (b'"unterminated', True),
                              (b'{"duplicate": 1, "duplicate": 2}', False)):
            with self.subTest(data=data):
                self.assertEqual(scientific_json(data, "text/plain", encoded_string=encoded), (False, None))
        self.assertEqual(scientific_json(b'{"a": [1, 2]}', "text/plain"), (True, {"a": [1, 2]}))
        self.assertEqual(scientific_json(b'"[text]"', "", encoded_string=True), (True, "[text]"))

    def test_declared_json_keeps_strict_parsing(self):
        for data in (_TRANSCRIPT.encode(), b'{"duplicate": 1, "duplicate": 2}', b"plain words"):
            with self.subTest(data=data), self.assertRaises(ResearchError) as error:
                scientific_json(data, "application/json")
            self.assertEqual(error.exception.code, "invalid_json")

    def test_undeclared_text_keeps_the_nesting_bound(self):
        with self.assertRaises(ResearchError) as error:
            scientific_json(b"[" * 41, "text/plain")
        self.assertEqual(error.exception.code, "invalid_scientific_delivery")

    def test_public_source_text_starting_like_json_is_delivered_exactly(self):
        capture = self.capture(self.metadata(), pdf_text=_TRANSCRIPT)
        records = self.store.snapshot()["records"]
        contract = {"payload": {"scientific_delivery": []}}
        manifest = {"source_text": capture["text"], "note": "[not json either", "encoded": '{"kept": "[value]"}'}
        projected, derived = project_delivery(records, self.artifacts, contract, manifest)
        self.assertEqual(projected, manifest)
        self.assertEqual(derived, {})
        target = self.root / "delivery"
        _deliver(self.store, target, projected, derived=derived, transitive=True)
        self.assertEqual((target / capture["text"]["path"]).read_bytes(), self.artifacts.read(capture["text"]))
        self.assertEqual(self.artifacts.read(capture["text"]).decode(), _TRANSCRIPT)
