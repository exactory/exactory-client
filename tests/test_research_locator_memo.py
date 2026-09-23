"""Result JSON parsing is local to a checked evaluation, without weaker locators."""

import copy
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from research_harness.artifacts import ArtifactStore
from research_harness.errors import ResearchError
from research_harness.evaluation import Evaluation
from research_harness import source_links


class LocatorMemoTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name) / "study"
        self.artifacts = ArtifactStore(self.root)
        self.records = {}
        self.evaluation = Evaluation(self.records, self.artifacts)

    def locator(self, pointer, value):
        return {"kind": "json", "pointer": pointer, "value": value}

    def test_distinct_and_repeated_locators_parse_one_verified_artifact_once(self):
        reference = self.artifacts.put(b'{"left":1.25,"right":2.5}', "application/json")
        locators = [self.locator("/left", 1.25), self.locator("/right", 2.5)]
        with mock.patch.object(source_links, "_json", wraps=source_links._json) as parser:
            values = [source_links.read_locator(self.evaluation, reference, locator)
                      for _ in range(4) for locator in locators]
        self.assertEqual(values, [1.25, 2.5, 1.25, 2.5, 1.25, 2.5, 1.25, 2.5])
        self.assertEqual(parser.call_count, 1)
        self.assertEqual(self.evaluation.counters["artifacts_verified"], 1)

    def test_mutating_a_returned_container_cannot_change_later_locator_results(self):
        reference = self.artifacts.put(b'{"result":{"values":[1,2]}}', "application/json")
        locator = self.locator("/result", {"values": [1, 2]})
        value = source_links.read_locator(self.evaluation, reference, locator)
        value["values"].append(99)
        again = source_links.read_locator(self.evaluation, reference, locator)
        self.assertEqual(again, {"values": [1, 2]})
        self.assertEqual(source_links.read_locator(self.evaluation, reference, self.locator("/result/values/1", 2)), 2)
        with self.assertRaises(ResearchError) as raised:
            source_links.read_locator(self.evaluation, reference, self.locator("/result", value))
        self.assertEqual(raised.exception.code, "invalid_locator")

    def test_warmed_parse_still_checks_each_exact_locator_and_artifact_descriptor(self):
        reference = self.artifacts.put(b'{"value":1,"fraction":1.0}', "application/json")
        self.assertEqual(source_links.read_locator(self.evaluation, reference, self.locator("/value", 1)), 1)
        invalid = [self.locator("/value", 2), self.locator("/value", 1.0),
                   self.locator("/fraction", 1), self.locator("/missing", 1),
                   dict(self.locator("/value", 1), unexpected=True)]
        for locator in invalid:
            with self.subTest(locator=locator), self.assertRaises(ResearchError) as raised:
                source_links.read_locator(self.evaluation, reference, locator)
            self.assertEqual(raised.exception.code, "invalid_locator")
        for field, value in (("size", reference["size"] + 1), ("path", "research/sources/objects/wrong"),
                             ("media_type", "invalid media"), ("sha256", "0" * 64)):
            with self.subTest(field=field), self.assertRaises(ResearchError):
                source_links.read_locator(self.evaluation, dict(reference, **{field: value}), self.locator("/value", 1))

    def test_fresh_evaluation_catches_same_revision_corruption_after_cached_success(self):
        reference = self.artifacts.put(b'{"value":1}', "application/json")
        locator = self.locator("/value", 1)
        self.assertEqual(source_links.read_locator(self.evaluation, reference, locator), 1)
        path = self.root / reference["path"]
        path.chmod(0o600)
        path.write_bytes(b'{"value":2}')
        with self.assertRaises(ResearchError) as raised:
            source_links.read_locator(Evaluation(self.records, self.artifacts), reference, locator)
        self.assertEqual(raised.exception.code, "artifact_corrupt")

    def test_duplicate_keys_nonfinite_unicode_and_depth_rejections_match_uncached_reads(self):
        invalid = [b'{"value":1,"value":1}', b'{"value":NaN}', b'{"value":Infinity}',
                   b'{"value":1e999}', b'{"value":"\\ud800"}', b'{"value":' + b'[' * 80 + b'0' + b']' * 80 + b'}',
                   '{"value":1}'.encode("utf-16")]
        for data in invalid:
            reference = self.artifacts.put(data, "application/json")
            for artifacts in (self.artifacts, self.evaluation, self.evaluation):
                with self.subTest(data=data[:30], cached=isinstance(artifacts, Evaluation)):
                    with self.assertRaises(ResearchError) as raised:
                        source_links.read_locator(artifacts, reference, self.locator("/value", 1))
                    self.assertEqual(raised.exception.code, "invalid_locator")

    def test_new_record_snapshot_does_not_reuse_previous_parsed_json(self):
        reference = self.artifacts.put(b'{"value":[1,2]}', "application/json")
        locator = self.locator("/value", [1, 2])
        with mock.patch.object(source_links, "_json", wraps=source_links._json) as parser:
            first = source_links.read_locator(self.evaluation, reference, locator)
            second = source_links.read_locator(Evaluation.of(copy.deepcopy(self.records), self.evaluation), reference, locator)
        self.assertEqual(first, [1, 2])
        self.assertEqual(second, [1, 2])
        self.assertEqual(parser.call_count, 2)
