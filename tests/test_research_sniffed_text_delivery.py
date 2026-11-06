"""Undeclared text that merely begins like JSON is delivered as exact text."""

import json

from literature_fixtures import LiteratureCase
from research_harness.errors import ResearchError
from research_harness.review_delivery import deliver_packet
from research_harness.scientific_delivery import project_delivery
from research_harness.scientific_json import parse_scientific_json


_TRANSCRIPT = ("[1803.00959v1] Robustness of the Insulating Bulk (https://arxiv.org/abs/1803.00959v1)\n"
               "L2: Search arXiv [Input: Search papers by title] [Input] [Input]\nL8: [Submitted on 2 Mar 2018]\n")
_INTERVALS = "[Abstract] " + " ".join("[0, %d)" % k for k in range(1, 45))
_DEEP_JSON = b"[" * 41 + b"]" * 41


class SniffedTextDeliveryTests(LiteratureCase):
    def test_undeclared_text_that_no_json_decoder_reads_is_text(self):
        for data in (_TRANSCRIPT.encode(), _INTERVALS.encode(), b"[" * 41, b"{not json", b'"unterminated',
                     b'"' + b"[" * 41, b"\xff[not text either"):
            with self.subTest(data=data):
                self.assertEqual(parse_scientific_json(data, "text/plain"), (False, None))
        self.assertEqual(parse_scientific_json(b'{"a": [1, 2]}', "text/plain"), (True, {"a": [1, 2]}))
        self.assertEqual(parse_scientific_json(b'"[text]"', ""), (True, "[text]"))

    def test_declared_json_keeps_strict_parsing(self):
        for data in (_TRANSCRIPT.encode(), b'{"duplicate": 1, "duplicate": 2}', b"plain words"):
            with self.subTest(data=data), self.assertRaises(ResearchError) as error:
                parse_scientific_json(data, "application/json")
            self.assertEqual(error.exception.code, "invalid_json")

    def test_text_that_a_json_decoder_reads_stays_strict_declared_or_not(self):
        # Delivered as text, its decoded strings would miss the privacy checks.
        for media_type in ("text/plain", "application/json"):
            for data, code in ((_DEEP_JSON, "invalid_scientific_delivery"), (b"[NaN]", "invalid_input"),
                               (b'{"duplicate": 1, "duplicate": 2}', "invalid_json"),
                               (b'{"note": "\xed\xa0\x80"}', "invalid_input")):
                with self.subTest(media_type=media_type, data=data):
                    with self.assertRaises(ResearchError) as error:
                        parse_scientific_json(data, media_type)
                    self.assertEqual(error.exception.code, code)
        with self.assertRaises(ResearchError) as error:
            parse_scientific_json(b"[" * 41, "application/json")
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
        deliver_packet(self.store, target, projected, derived=derived, transitive=True)
        self.assertEqual((target / capture["text"]["path"]).read_bytes(), self.artifacts.read(capture["text"]))
        self.assertEqual(self.artifacts.read(capture["text"]).decode(), _TRANSCRIPT)

    def test_decodable_text_hides_no_escaped_private_text(self):
        private = self.artifacts.put(b"Private authorization of this fixture", "text/plain")
        escaped = "".join("\\u%04x" % byte for byte in self.artifacts.read(private))
        # Refused as private where this interpreter parses the text strictly,
        # else as invalid JSON (duplicate fields; integers beyond its digit limit).
        refusals = {"private_mixed_artifact_required", "invalid_json"}
        texts = ['{"note": 1, "note": "' + escaped + '"}', '"' + escaped + '"',
                 '[' + "7" * 5000 + ', "' + escaped + '"]']
        captures = [self.capture(self.metadata(number), pdf_text=text) for number, text in enumerate(texts, 1)]
        records = dict(self.store.snapshot()["records"],
                       source_deferral={"gap": {"authorization": private, "acquisition_evidence": []}})
        for text, capture in zip(texts, captures):
            for manifest in ({"source_text": capture["text"]}, {"log": text}):
                with self.subTest(text=text[:20], manifest=list(manifest)):
                    with self.assertRaises(ResearchError) as error:
                        project_delivery(records, self.artifacts, {"payload": {"scientific_delivery": []}}, manifest)
                    self.assertIn(error.exception.code, refusals)

    def deliver_public_text(self, text, private):
        capture = self.capture(self.metadata(), pdf_text=text)
        records = dict(self.store.snapshot()["records"],
                       source_deferral={"gap": {"authorization": private, "acquisition_evidence": []}})
        manifest = {"source_text": capture["text"]}
        projected, derived = project_delivery(records, self.artifacts, {"payload": {"scientific_delivery": []}}, manifest)
        target = self.root / "delivery"
        deliver_packet(self.store, target, projected, derived=derived, transitive=True)
        return capture, target

    def test_text_that_names_a_private_artifact_neither_follows_nor_copies_it(self):
        private = self.artifacts.put(b"Private authorization of this fixture", "text/plain")
        text = '{"artifact": ' + json.dumps(private) + ', "unterminated": "'
        capture, target = self.deliver_public_text(text, private)
        delivered = sorted(path.relative_to(target).as_posix() for path in target.rglob("*") if path.is_file())
        self.assertEqual(delivered, sorted([capture["text"]["path"], "inputs.json"]))
        self.assertEqual((target / capture["text"]["path"]).read_bytes(), text.encode())

    def test_text_with_private_bytes_is_refused(self):
        private = self.artifacts.put(b"Private authorization of this fixture", "text/plain")
        text = '{"note": "Private authorization of this fixture", "unterminated": "'
        with self.assertRaises(ResearchError) as error:
            self.deliver_public_text(text, private)
        self.assertEqual(error.exception.code, "private_mixed_artifact_required")
        self.assertFalse((self.root / "delivery").exists())

    def test_text_in_another_unicode_encoding_is_checked_as_text(self):
        private = self.artifacts.put(b"Private authorization of this fixture", "text/plain")
        records = dict(self.store.snapshot()["records"],
                       source_deferral={"gap": {"authorization": private, "acquisition_evidence": []}})
        for encoding in ("utf-16", "utf-32"):
            for text in ("Notes: Private authorization of this fixture", "{Notes: Private authorization of this fixture"):
                exact = self.artifacts.put(text.encode(encoding), "text/plain")
                with self.subTest(encoding=encoding, text=text[:7]):
                    with self.assertRaises(ResearchError) as error:
                        project_delivery(records, self.artifacts, {"payload": {"scientific_delivery": []}},
                                         {"file": exact}, manuscript_files=[exact])
                    self.assertEqual(error.exception.code, "private_mixed_artifact_required")

    def test_bomless_non_ascii_private_text_is_refused_in_all_byte_orders(self):
        for secret in ('漢Private authorization of this fixture', '漢字' * 30):
            private = self.artifacts.put(secret.encode(), 'text/plain')
            records = {'source_deferral': {'gap': {'authorization': private, 'acquisition_evidence': []}}}
            for encoding in ('utf-16-le', 'utf-16-be', 'utf-32-le', 'utf-32-be'):
                data = secret.encode(encoding)
                exact = self.artifacts.put(data, 'text/plain')
                with self.subTest(secret=secret[:4], encoding=encoding):
                    with self.assertRaises(ResearchError) as error:
                        project_delivery(records, self.artifacts, {'payload': {'scientific_delivery': []}},
                                         {'file': exact}, manuscript_files=[exact])
                    self.assertEqual(error.exception.code, 'private_mixed_artifact_required')
                    self.assertEqual(self.artifacts.read(exact), data)

    def test_clean_unicode_and_large_inputs_keep_bytes_and_json_interpretation(self):
        private = self.artifacts.put('漢Private authorization of this fixture'.encode(), 'text/plain')
        records = {'source_deferral': {'gap': {'authorization': private, 'acquisition_evidence': []}}}
        cases = []
        for encoding in ('utf-8-sig', 'utf-16', 'utf-16-le', 'utf-16-be', 'utf-32', 'utf-32-le', 'utf-32-be'):
            cases.append(('漢Public scientific observations'.encode(encoding), 'text/plain', (False, None)))
            cases.append((json.dumps({'result': '漢Public observations'}, ensure_ascii=False).encode(encoding),
                          'application/json', (True, {'result': '漢Public observations'})))
        cases.append((b'Clean scientific observations\n' * 20000, 'text/plain', (False, None)))
        for index, (data, media, interpretation) in enumerate(cases):
            with self.subTest(index=index):
                self.assertEqual(parse_scientific_json(data, media), interpretation)
                exact = self.artifacts.put(data, media)
                manifest = {'file': exact}
                projected, derived = project_delivery(records, self.artifacts,
                    {'payload': {'scientific_delivery': []}}, manifest, manuscript_files=[exact])
                self.assertEqual(projected, manifest)
                self.assertEqual(derived, {})
                target = self.root / ('clean-encoded-' + str(index))
                deliver_packet(self.store, target, projected, derived=derived, transitive=True)
                delivered = (target / exact['path']).read_bytes()
                self.assertEqual(delivered, data)
                self.assertEqual(parse_scientific_json(delivered, media), interpretation)

    def test_binary_private_artifact_keeps_literal_byte_protection(self):
        secret = b'\xff\x00Private binary authorization\x80'
        private = self.artifacts.put(secret, 'application/octet-stream')
        records = {'source_deferral': {'gap': {'authorization': private, 'acquisition_evidence': []}}}
        exact = self.artifacts.put(b'prefix' + secret + b'suffix', 'application/octet-stream')
        with self.assertRaises(ResearchError) as error:
            project_delivery(records, self.artifacts, {'payload': {'scientific_delivery': []}},
                             {'file': exact}, manuscript_files=[exact])
        self.assertEqual(error.exception.code, 'private_mixed_artifact_required')
