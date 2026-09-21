"""Derived mapping parsing stays local, byte-bound and independently checked."""

import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from research_harness import scientific_delivery
from research_harness.artifacts import ArtifactStore, describe_artifact
from research_harness.errors import ResearchError


class DerivedMappingMemoTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.artifacts = ArtifactStore(Path(temporary.name))
        self.ref = self.artifacts.put(b'{"left":1,"right":{"values":[1,2]}}', "application/json")
        self.locators = [{"kind": "json", "pointer": "/left", "value": 1},
                         {"kind": "json", "pointer": "/right", "value": {"values": [1, 2]}}]
        self.contract = {"payload": {"scientific_delivery": [{"original_sha256": self.ref["sha256"],
            "disposition": "project", "projection": {"kind": "json_locators", "context": "Exact authored results.",
                                                      "locators": self.locators}}]}}

    def create_projector(self):
        return scientific_delivery.ScientificDelivery({}, self.artifacts, self.contract, {})

    def create_evidence(self, index=0, **overrides):
        return dict({"artifact": self.ref, "locator": self.locators[index]}, **overrides)

    def test_repeated_mapping_parses_once_but_original_locators_still_validate(self):
        projector = self.create_projector()
        with mock.patch.object(scientific_delivery, "strict_json", wraps=scientific_delivery.strict_json) as parsed:
            with mock.patch.object(scientific_delivery, "read_locator", wraps=scientific_delivery.read_locator) as checked:
                results = [projector.walk(self.create_evidence(i)) for _ in range(4) for i in range(2)]
        self.assertEqual([result["locator"]["value"] for result in results], [1, {"values": [1, 2]}] * 4)
        self.assertEqual([result["locator"]["pointer"] for result in results], ["/entries/0/value", "/entries/1/value"] * 4)
        self.assertEqual(checked.call_count, 10)
        self.assertEqual(parsed.call_count, 1)

    def test_independent_projectors_do_not_share_mapping_parses(self):
        with mock.patch.object(scientific_delivery, "strict_json", wraps=scientific_delivery.strict_json) as parsed:
            self.create_projector().walk(self.create_evidence())
            self.create_projector().walk(self.create_evidence())
        self.assertEqual(parsed.call_count, 2)

    def test_mutating_returned_values_cannot_poison_private_mapping(self):
        projector = self.create_projector()
        first = projector.walk(self.create_evidence(1))
        first["locator"]["value"]["values"].append(9)
        second = projector.walk(self.create_evidence(1))
        self.assertEqual(second["locator"]["value"], {"values": [1, 2]})
        self.assertEqual(second["locator"]["pointer"], "/entries/1/value")

    def test_warm_mapping_preserves_wrong_locator_and_original_descriptor_refusal(self):
        projector = self.create_projector()
        projector.walk(self.create_evidence())
        for locator in (dict(self.locators[0], value=2), dict(self.locators[0], value=1.0),
                        dict(self.locators[0], value=True), dict(self.locators[0], pointer="/missing"),
                        dict(self.locators[0], extra=True)):
            with self.subTest(locator=locator), self.assertRaises(ResearchError):
                projector.walk(self.create_evidence(locator=locator))
        for field, value in (("size", float(self.ref["size"])), ("size", self.ref["size"] + 1),
                             ("path", "../unsafe"), ("sha256", "0" * 64), ("media_type", "invalid")):
            with self.subTest(field=field, value=value), self.assertRaises(ResearchError):
                projector.walk(self.create_evidence(artifact=dict(self.ref, **{field: value})))

    def test_changed_derived_bytes_cannot_reuse_a_warm_mapping(self):
        projector = self.create_projector()
        result = projector.walk(self.create_evidence())
        ref = result["artifact"]
        original = projector.derived[ref["path"]]
        corrupted = json.loads(original)
        corrupted["locator_mapping"][0]["derived_pointer"] = "/unverified"
        projector.derived[ref["path"]] = json.dumps(corrupted).encode()
        with self.assertRaises(ResearchError) as error:
            projector.walk(self.create_evidence())
        self.assertEqual(error.exception.code, "artifact_corrupt")

    def test_changed_derived_descriptor_is_checked_before_a_cache_hit(self):
        for field, value in (("size", 0), ("size", 1.0), ("path", "../unsafe"), ("media_type", "invalid")):
            projector = self.create_projector()
            result = projector.walk(self.create_evidence())
            result["artifact"][field] = value
            with self.subTest(field=field, value=value), self.assertRaises(ResearchError):
                projector.walk(self.create_evidence())

    def test_new_exact_derived_bytes_still_receive_strict_json_validation(self):
        for data, code in ((b'{"locator_mapping":[],"locator_mapping":[]}', "invalid_json"),
                           (b'{"value":NaN}', "invalid_input"), (b'{"broken":', "invalid_json")):
            projector = self.create_projector()
            projector.walk(self.create_evidence())
            ref = describe_artifact(data, "application/json")
            projector.derived[ref["path"]] = data
            projector.mapped[self.ref["sha256"]] = ref
            with self.subTest(data=data), self.assertRaises(ResearchError) as error:
                projector.walk(self.create_evidence())
            self.assertEqual(error.exception.code, code)

    def test_warm_mapping_does_not_hide_later_original_byte_corruption(self):
        projector = self.create_projector()
        projector.walk(self.create_evidence())
        path = self.artifacts.root / self.ref["path"]
        path.chmod(0o600)
        path.write_bytes(b'{"left":2}')
        with self.assertRaises(ResearchError) as error:
            projector.walk(self.create_evidence())
        self.assertEqual(error.exception.code, "artifact_corrupt")
