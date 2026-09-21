"""Cached access preserves the artifact and snapshot trust boundaries."""

import tempfile
import unittest
from pathlib import Path

from research_harness.artifacts import ArtifactStore
from research_harness.errors import ResearchError
from research_harness.evaluation import Evaluation
from research_harness.source_links import read_locator


class EvaluationBoundaryTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.artifacts = ArtifactStore(Path(directory.name))
        self.records = {}
        self.evaluation = Evaluation(self.records, self.artifacts)
        self.reference = self.artifacts.put(b'{"value":1}', "application/json")
        self.locator = {"kind": "json", "pointer": "/value", "value": 1}

    def test_warm_bytes_and_text_keep_exact_descriptor_validation(self):
        self.evaluation.read(self.reference)
        self.evaluation.text(self.reference)
        malformed = [None, {}, {k: v for k, v in self.reference.items() if k != "media_type"}]
        malformed.extend(dict(self.reference, **{field: value}) for field, value in (
            ("size", float(self.reference["size"])), ("size", True), ("size", -1), ("size", []),
            ("sha256", "invalid"), ("sha256", []), ("path", "../outside"), ("path", []),
            ("media_type", "invalid"), ("media_type", [])))
        for reference in malformed:
            with self.subTest(reference=reference):
                with self.assertRaises(ResearchError) as direct:
                    self.artifacts.read(reference)
                for method in (self.evaluation.read, self.evaluation.text):
                    with self.subTest(method=method.__name__):
                        with self.assertRaises(ResearchError) as cached:
                            method(reference)
                        self.assertEqual(cached.exception.code, direct.exception.code)

    def test_boolean_size_cannot_equal_a_valid_one_byte_cache_key(self):
        reference = self.artifacts.put(b"1", "application/json")
        self.assertEqual(self.evaluation.read(reference), b"1")
        for size in (True, 1.0):
            with self.subTest(size=size):
                with self.assertRaises(ResearchError) as error:
                    read_locator(self.evaluation, dict(reference, size=size),
                                 {"kind": "json", "pointer": "", "value": 1})
                self.assertEqual(error.exception.code, "invalid_input")

    def test_new_snapshot_and_explicit_constructor_recheck_tampered_bytes(self):
        self.assertEqual(read_locator(self.evaluation, self.reference, self.locator), 1)
        self.evaluation.text(self.reference)
        self.evaluation.once("derived", lambda: "old")
        path = self.artifacts.root / self.reference["path"]
        path.chmod(0o600)
        path.write_bytes(b'{"value":2}')
        for create in (lambda: Evaluation.of({}, self.evaluation),
                       lambda: Evaluation({}, self.evaluation),
                       lambda: Evaluation(self.records, self.evaluation)):
            successor = create()
            self.assertIsNot(successor, self.evaluation)
            self.assertEqual(successor.once("derived", lambda: "new"), "new")
            with self.subTest(create=create):
                with self.assertRaises(ResearchError) as error:
                    read_locator(successor, self.reference, self.locator)
                self.assertEqual(error.exception.code, "artifact_corrupt")
                with self.assertRaises(ResearchError) as text_error:
                    successor.text(self.reference)
                self.assertEqual(text_error.exception.code, "artifact_corrupt")

    def test_same_snapshot_reuses_verified_bytes_and_derived_values(self):
        for _ in range(3):
            self.assertEqual(self.evaluation.text(self.reference), '{"value":1}')
            self.assertEqual(read_locator(self.evaluation, self.reference, self.locator), 1)
        shared = Evaluation.of(self.records, self.evaluation)
        self.assertIs(shared, self.evaluation)
        self.assertEqual(shared.counters["artifacts_verified"], 1)
        self.assertEqual(shared.once("derived", lambda: "same"), "same")
        self.assertEqual(self.evaluation.once("derived", lambda: "different"), "same")

    def test_valid_metadata_variants_and_extra_fields_keep_store_semantics(self):
        for reference in (self.reference, dict(self.reference, media_type="text/plain"),
                          dict(self.reference, note="Additional metadata is allowed by ArtifactStore.")):
            self.assertEqual(self.evaluation.read(reference), self.artifacts.read(reference))
            self.assertEqual(self.evaluation.text(reference), self.artifacts.read(reference).decode())
