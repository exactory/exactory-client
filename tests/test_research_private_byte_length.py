"""A length shortcut preserves exact privacy decisions and byte limits."""

import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from research_harness.artifacts import ArtifactStore
from research_harness.errors import ResearchError
from research_harness.scientific_delivery import ScientificDelivery


class ObservedBytes(bytes):
    def __contains__(self, pattern):
        self.comparisons += 1
        return super().__contains__(pattern)


class PrivateByteLengthTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.artifacts = ArtifactStore(Path(temporary.name))

    def projector(self, payloads):
        refs = [self.artifacts.put(data, "text/plain") for data in payloads]
        records = {"source_deferral": {str(i): {"authorization": ref, "acquisition_evidence": []}
                                      for i, ref in enumerate(refs)}}
        return ScientificDelivery(records, self.artifacts, {"payload": {"scientific_delivery": []}}, {})

    def test_short_candidate_does_not_compare_impossible_private_patterns(self):
        projector = self.projector([("Private fixture %02d" % i).encode() for i in range(32)])
        data = ObservedBytes(b"12345678")
        data.comparisons = 0
        projector._check_bytes(data)
        self.assertEqual(data.comparisons, 0)

    def test_eligible_candidate_still_checks_every_private_pattern(self):
        projector = self.projector([b"private-A", b"private-B", b"private-C"])
        data = ObservedBytes(b"A harmless candidate longer than every pattern")
        data.comparisons = 0
        projector._check_bytes(data)
        self.assertEqual(data.comparisons, 3)

    def test_empty_one_byte_and_exact_length_containment_decisions(self):
        cases = [([], b"anything", False), ([b""], b"", False), ([b"", b"x"], b"", False),
                 ([b"x"], b"x", True), ([b"x"], b"y", False), ([b"x"], b"prefix-x-suffix", True),
                 ([b"abc"], b"ab", False), ([b"abc"], b"abc", True), ([b"abc"], b"abd", False),
                 ([b"long-private-value", b"xy"], b"xy", True), ([b"aba"], b"ababa", True),
                 ([b"\x00\xff"], b"\x00", False), ([b"\x00\xff"], b"x\x00\xffy", True)]
        for payloads, candidate, refused in cases:
            with self.subTest(payloads=payloads, candidate=candidate):
                projector = self.projector(payloads)
                if refused:
                    with self.assertRaises(ResearchError) as error:
                        projector._check_bytes(candidate)
                    self.assertEqual(error.exception.code, "private_mixed_artifact_required")
                else:
                    projector._check_bytes(candidate)

    def test_byte_limit_precedes_short_or_empty_inventory_shortcut(self):
        for payloads in ([], [b""], [b"A long private payload"]):
            projector = self.projector(payloads)
            with self.subTest(payloads=payloads), patch("research_harness.scientific_delivery._MAX_BYTES", 4):
                projector._check_bytes(b"1234")
                with self.assertRaises(ResearchError) as error:
                    projector._check_bytes(b"12345")
                self.assertEqual(error.exception.code, "invalid_scientific_delivery")
                projector._check_bytes(b"12345", artifact=False)

    def test_escaped_multilayer_json_keys_and_values_still_reveal_private_text(self):
        secret = "PRIVATE-CONTEXT"
        projector = self.projector([secret.encode()])
        escaped = "".join("\\u%04x" % ord(char) for char in secret)
        for value in ('{"key":"' + escaped + '"}', '{"' + escaped + '":"value"}'):
            for layers in (0, 1, 3):
                encoded = value
                for _ in range(layers):
                    encoded = json.dumps(encoded)
                for prefix in ("", "\ufeff"):
                    with self.subTest(layers=layers, prefix=prefix), self.assertRaises(ResearchError) as error:
                        projector.walk(prefix + encoded)
                    self.assertEqual(error.exception.code, "private_mixed_artifact_required")

    def test_private_alias_and_inventory_are_fixed_when_projector_is_constructed(self):
        ref = self.artifacts.put(b"Original private content", "text/plain")
        records = {"source_deferral": {"gap": {"authorization": ref, "acquisition_evidence": []}},
                   "source": {"alias": {"id": "alias", "response": ref}}}
        projector = ScientificDelivery(records, self.artifacts, {"payload": {"scientific_delivery": []}}, {})
        records["source_deferral"].clear()
        with self.assertRaises(ResearchError) as error:
            projector.reference(ref)
        self.assertEqual(error.exception.code, "private_mixed_artifact_required")
        with self.assertRaises(ResearchError):
            projector._check_bytes(b"A copy: Original private content")
