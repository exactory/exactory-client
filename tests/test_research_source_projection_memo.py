"""Immutable source projections reuse parsing without skipping locator checks."""

import copy
import io
import json
from pathlib import Path
import struct
import tempfile
import unittest
from unittest import mock
import zipfile

from research_harness import scientific_delivery, source_links
from research_harness.artifacts import ArtifactStore, describe_artifact
from research_harness.errors import ResearchError
from research_harness.evidence import digest
from test_research_numerical_projection import npy


class SourceProjectionMemoTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.artifacts = ArtifactStore(Path(temporary.name))

    def create_projector(self):
        return scientific_delivery.ScientificDelivery({}, self.artifacts,
            {"payload": {"scientific_delivery": []}}, {})

    def create_projection(self, locators, kind="json_locators"):
        return {"kind": kind, "context": "Exact synthetic observations.", "locators": locators}

    def create_locator(self, pointer, value):
        return {"kind": "json", "pointer": pointer, "value": value}

    def test_distinct_json_selectors_parse_once_and_validate_every_locator(self):
        ref = self.artifacts.put(b'{"left":1,"right":{"values":[2,3]}}', "application/json")
        locators = [self.create_locator("/left", 1), self.create_locator("/right", {"values": [2, 3]}),
                    self.create_locator("/right/values/1", 3)]
        projector = self.create_projector()
        with mock.patch.object(source_links, "_json", wraps=source_links._json) as parsed:
            with mock.patch.object(scientific_delivery, "read_locator", wraps=source_links.read_locator) as checked:
                projected, mapping = projector._project(ref, self.create_projection(locators))
        result = json.loads(projector.derived[projected["path"]])
        self.assertEqual([entry["value"] for entry in result["entries"]], [1, {"values": [2, 3]}, 3])
        self.assertEqual([(entry["original_locator"]["pointer"], entry["original_locator"]["locator_digest"]) for entry in mapping],
                         [(locator["pointer"], digest(locator)) for locator in locators])
        self.assertTrue(all("value" not in entry["original_locator"] for entry in mapping))
        self.assertEqual(checked.call_count, 3)
        self.assertEqual(parsed.call_count, 1)

    def test_each_warm_selector_keeps_pointer_type_and_expected_value_checks(self):
        ref = self.artifacts.put(b'{"value":1,"fraction":1.0,"array":[2],"a/b":{"~":3}}', "application/json")
        first = self.create_locator("/value", 1)
        invalid = [self.create_locator("/value", 1.0), self.create_locator("/value", True),
                   self.create_locator("/value", 2), self.create_locator("/fraction", 1),
                   self.create_locator("/missing", 1), self.create_locator("/array/00", 2),
                   self.create_locator("/array/3", 2), self.create_locator(0, 1), dict(first, extra=True)]
        for locator in invalid:
            with self.subTest(locator=locator), self.assertRaises(ResearchError) as raised:
                self.create_projector()._project(ref, self.create_projection([first, locator]))
            self.assertEqual(raised.exception.code, "invalid_locator")
        projector = self.create_projector()
        result, _ = projector._project(ref, self.create_projection([first, self.create_locator("/a~1b/~0", 3)]))
        self.assertEqual(json.loads(projector.derived[result["path"]])["entries"][1]["value"], 3)

    def test_warm_reader_rejects_malformed_descriptors(self):
        ref = self.artifacts.put(b'{"left":1,"right":2}', "application/json")
        locators = [self.create_locator("/left", 1), self.create_locator("/right", 2)]
        calls = []

        def check_read(reader, reference, locator):
            value = source_links.read_locator(reader, reference, locator)
            calls.append(reader)
            for key, invalid in (("size", float(reference["size"])), ("size", True),
                                 ("path", "../unsafe"), ("sha256", "0" * 64),
                                 ("media_type", "invalid media")):
                with self.subTest(key=key, invalid=invalid), self.assertRaises(ResearchError):
                    source_links.read_locator(reader, dict(reference, **{key: invalid}), locator)
            return value

        with mock.patch.object(scientific_delivery, "read_locator", side_effect=check_read):
            self.create_projector()._project(ref, self.create_projection(locators))
        self.assertEqual(len(calls), 2)

    def test_selected_container_mutation_does_not_modify_later_values(self):
        ref = self.artifacts.put(b'{"result":{"values":[1,2]}}', "application/json")
        locators = [self.create_locator("/result", {"values": [1, 2]}), self.create_locator("/result/values", [1, 2])]
        projector = self.create_projector()

        def change_selected_value(value):
            if isinstance(value, dict) and "values" in value:
                value["values"].append(99)

        with mock.patch.object(projector, "_public_nested", side_effect=change_selected_value):
            result, _ = projector._project(ref, self.create_projection(locators))
        self.assertEqual(json.loads(projector.derived[result["path"]])["entries"][1]["value"], [1, 2])

    def test_each_projection_gets_a_fresh_parse_and_exact_source_bytes(self):
        projector = self.create_projector()
        values = []
        with mock.patch.object(source_links, "_json", wraps=source_links._json) as parsed:
            for expected in (1, 2, 1):
                ref = self.artifacts.put(json.dumps({"value": expected}).encode(), "application/json")
                result, _ = projector._project(ref, self.create_projection([self.create_locator("/value", expected)]))
                values.append(json.loads(projector.derived[result["path"]])["entries"][0]["value"])
        self.assertEqual(values, [1, 2, 1])
        self.assertEqual(parsed.call_count, 3)

    def test_failed_parses_are_not_reused_between_projections(self):
        invalid = [b'{"value":1,"value":1}', b'{"value":NaN}', b'{"value":Infinity}', b'{"value":1e999}',
                   b'{"value":"\\ud800"}', b'{"value":' + b'[' * 80 + b'0' + b']' * 80 + b'}']
        for data in invalid:
            ref = self.artifacts.put(data, "application/json")
            projector = self.create_projector()
            with self.subTest(data=data[:30]), mock.patch.object(source_links, "_json", wraps=source_links._json) as parsed:
                for _ in range(2):
                    with self.assertRaises(ResearchError) as raised:
                        projector._project(ref, self.create_projection([self.create_locator("/value", 1)]))
                    self.assertEqual(raised.exception.code, "invalid_locator")
                self.assertEqual(parsed.call_count, 2)

    def test_text_and_span_keep_exact_utf8_semantics_without_json_parsing(self):
        content = "alpha βeta gamma"
        ref = self.artifacts.put(content.encode(), "text/plain")
        locators = [{"kind": "text", "start": 0, "end": 5, "quote": "alpha"},
                    source_links.span_locator(content, 6, 10)]
        projector = self.create_projector()
        with mock.patch.object(source_links, "_json", wraps=source_links._json) as parsed:
            with mock.patch.object(scientific_delivery, "read_locator", wraps=source_links.read_locator) as checked:
                result, _ = projector._project(ref, self.create_projection(locators, "text_spans"))
        self.assertEqual([entry["value"] for entry in json.loads(projector.derived[result["path"]])["entries"]], ["alpha", "βeta"])
        self.assertEqual((parsed.call_count, checked.call_count), (0, 2))
        with self.assertRaises(ResearchError):
            projector._project(ref, self.create_projection([locators[0], dict(locators[1], sha256="0" * 64)], "text_spans"))
        invalid = self.artifacts.put(content.encode("utf-16"), "text/plain")
        with self.assertRaises(ResearchError):
            projector._project(invalid, self.create_projection(locators, "text_spans"))

    def test_archive_members_keep_separate_parses_and_exact_hashes(self):
        members = []
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w") as archive:
            for name, value in (("a.json", 1), ("b.json", 2)):
                data = json.dumps({"value": value, "other": value + 1}).encode()
                archive.writestr(zipfile.ZipInfo(name), data)
                members.append({"path": name, "sha256": describe_artifact(data, "application/json")["sha256"],
                    "media_type": "application/json", "projection": self.create_projection([
                        self.create_locator("/value", value), self.create_locator("/other", value + 1)])})
            data = npy("<f8", (2,), struct.pack("<2d", 1.25, 2.5))
            archive.writestr(zipfile.ZipInfo("values.npy"), data)
            members.append({"path": "values.npy", "sha256": describe_artifact(data, "application/octet-stream")["sha256"],
                "media_type": "application/octet-stream", "projection": {"kind": "npy_array", "context": "Exact numerical observations."}})
        ref = self.artifacts.put(buffer.getvalue(), "application/zip")
        projection = {"kind": "archive_members", "context": "Complete synthetic members.", "members": members}
        projector = self.create_projector()
        with mock.patch.object(source_links, "_json", wraps=source_links._json) as parsed:
            with mock.patch.object(scientific_delivery, "read_locator", wraps=source_links.read_locator) as checked:
                result, _ = projector._project(ref, projection)
        self.assertEqual((parsed.call_count, checked.call_count), (2, 4))
        content = json.loads(projector.derived[result["path"]])
        array = json.loads(projector.derived[content["entries"][2]["artifact"]["path"]])["array"]
        self.assertEqual(array["values"], [1.25, 2.5])
        altered = copy.deepcopy(projection)
        altered["members"][0]["sha256"] = "0" * 64
        with self.assertRaises(ResearchError):
            self.create_projector()._project(ref, altered)

    def test_later_projection_still_detects_original_file_corruption(self):
        ref = self.artifacts.put(b'{"value":1}', "application/json")
        projection = self.create_projection([self.create_locator("/value", 1)])
        projector = self.create_projector()
        projector._project(ref, projection)
        path = self.artifacts.root / ref["path"]
        path.chmod(0o600)
        path.write_bytes(b'{"value":2}')
        with self.assertRaises(ResearchError) as raised:
            projector._project(ref, projection)
        self.assertEqual(raised.exception.code, "artifact_corrupt")
