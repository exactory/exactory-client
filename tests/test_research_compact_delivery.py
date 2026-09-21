"""Scientific delivery preserves values while accounting for serialized bytes."""

import hashlib
import json
import math
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from unittest import mock

from research_harness import scientific_delivery
from research_harness.artifacts import ArtifactStore
from research_harness.errors import ResearchError
from research_harness.review_delivery import _deliver


class CompactDeliveryTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.artifacts = ArtifactStore(self.root)
        self.contract = {"payload": {"scientific_delivery": []}}

    def create_projector(self):
        return scientific_delivery.ScientificDelivery({}, self.artifacts, self.contract, {})

    def test_manifest_uses_only_actual_output_bytes_at_the_existing_total_bound(self):
        manifest = {"observations": [{"index": 0, "value": 0.0}, {"index": 1, "value": 0.125}], "context": "β"}
        compact = '{"observations":[{"index":0,"value":0.0},{"index":1,"value":0.125}],"context":"β"}\n'.encode()
        with mock.patch.object(scientific_delivery, "_MAX_OUTPUT_BYTES", len(compact)):
            projected, derived = scientific_delivery.project_delivery({}, self.artifacts, self.contract, manifest)
        self.assertEqual(projected, manifest)
        self.assertEqual(derived, {})
        with mock.patch.object(scientific_delivery, "_MAX_OUTPUT_BYTES", len(compact) - 1):
            with self.assertRaises(ResearchError) as error:
                scientific_delivery.project_delivery({}, self.artifacts, self.contract, manifest)
        self.assertEqual(error.exception.code, "invalid_scientific_delivery")

    def test_generated_values_and_exact_descriptor_survive_compact_encoding(self):
        value = {"numbers": [0, -0.0, 1.0, 2**63 + 1, -(2**63 + 1), 1e-200, 5e-324, 1.7976931348623157e308],
                 "text": "alpha β\n  retained whitespace", "nested": {"": [True, None, False]},
                 "encoded": '{ "β": [1, 2], "text": "keep : , spacing" }'}
        projector = self.create_projector()
        ref = projector._emit(value)
        data = projector.derived[ref["path"]]
        expected = ('{"numbers":[0,-0.0,1.0,9223372036854775809,-9223372036854775809,1e-200,5e-324,1.7976931348623157e+308],'
                    '"text":"alpha β\\n  retained whitespace","nested":{"":['
                    'true,null,false]},"encoded":"{ \\"β\\": [1, 2], \\"text\\": \\"keep : , spacing\\" }"}\n').encode()
        self.assertEqual(data, expected)
        self.assertEqual(ref["size"], len(data))
        self.assertEqual(ref["sha256"], hashlib.sha256(data).hexdigest())
        restored = json.loads(data)
        self.assertEqual(restored, value)
        self.assertIs(type(restored["numbers"][0]), int)
        self.assertIs(type(restored["numbers"][2]), float)
        self.assertEqual(math.copysign(1, restored["numbers"][1]), -1)
        for index in (1, 2, 5, 6, 7):
            self.assertEqual(restored["numbers"][index].hex(), value["numbers"][index].hex())
        self.assertEqual(projector.output_bytes, len(data))

    def test_counted_manifest_bytes_equal_written_scoped_manifest(self):
        manifest = {"observations": [{"x": 1, "y": [2, 3]}], "context": "β"}
        counted = []
        original = scientific_delivery.ScientificDelivery._count_output

        def observe(projector, ref, data):
            counted.append(data)
            return original(projector, ref, data)

        with mock.patch.object(scientific_delivery.ScientificDelivery, "_count_output", observe):
            projected, derived = scientific_delivery.project_delivery({}, self.artifacts, self.contract, manifest)
        destination = self.root / "scoped"
        _deliver(SimpleNamespace(root=self.root, revision=1), destination, projected,
                 derived=derived, transitive=True)
        written = (destination / "inputs.json").read_bytes()
        self.assertEqual(counted, [written])
        self.assertEqual(written, '{"observations":[{"x":1,"y":[2,3]}],"context":"β"}\n'.encode())

    def test_counted_closure_matches_exported_bytes_and_exact_source_identity(self):
        source_bytes = b'{\r\n  "source" : [1,  2], "number": 1.00\r\n}\r\n'
        source = self.artifacts.put(source_bytes, "application/json")
        checkpoint = self.artifacts.put(b'{"original": "service record"}', "application/json")
        records = {"checkpoint": {"checkpoint": {"artifact": checkpoint, "values": [1, 2], "source": source}}}
        manifest = {"checkpoint": checkpoint, "source": source, "same_checkpoint": checkpoint}
        counted = {}
        original = scientific_delivery.ScientificDelivery._count_output

        def observe(projector, ref, data):
            counted[ref["path"]] = data
            return original(projector, ref, data)

        with mock.patch.object(scientific_delivery.ScientificDelivery, "_count_output", observe):
            projected, derived = scientific_delivery.project_delivery(
                records, self.artifacts, self.contract, manifest, manuscript_files=[source])
        total = sum(map(len, counted.values()))
        with mock.patch.object(scientific_delivery, "_MAX_OUTPUT_BYTES", total):
            scientific_delivery.project_delivery(records, self.artifacts, self.contract, manifest, manuscript_files=[source])
        with mock.patch.object(scientific_delivery, "_MAX_OUTPUT_BYTES", total - 1):
            with self.assertRaises(ResearchError) as error:
                scientific_delivery.project_delivery(records, self.artifacts, self.contract, manifest, manuscript_files=[source])
        self.assertEqual(error.exception.code, "invalid_scientific_delivery")
        destination = self.root / "closure"
        result = _deliver(SimpleNamespace(root=self.root, revision=1), destination, projected,
                          derived=derived, transitive=True)
        written = [path.read_bytes() for path in destination.rglob("*") if path.is_file()]
        self.assertCountEqual(written, counted.values())
        self.assertEqual(sum(map(len, written)), total)
        self.assertEqual((destination / source["path"]).read_bytes(), source_bytes)
        self.assertEqual(projected["source"], source)
        self.assertEqual(projected["checkpoint"], projected["same_checkpoint"])
        self.assertEqual(len(derived), 1)
        for ref in result["artifacts"]:
            data = (destination / ref["path"]).read_bytes()
            self.assertEqual(ref["size"], len(data))
            self.assertEqual(ref["sha256"], hashlib.sha256(data).hexdigest())
        derivative = json.loads(derived[projected["checkpoint"]["path"]])
        self.assertEqual(derivative, {"derivative": True, "original_sha256": checkpoint["sha256"],
                                    "projection_kind": "service_record", "value": {"values": [1, 2], "source": source}})

    def test_exact_public_and_unscoped_bytes_are_preserved(self):
        data = b'{\n  "source": [1, 2]\n}\n'
        ref = self.artifacts.put(data, "application/json")
        manifest = {"source": ref}
        for transitive in (False, True):
            destination = self.root / str(transitive)
            _deliver(SimpleNamespace(root=self.root, revision=1), destination, manifest, transitive=transitive)
            self.assertEqual((destination / ref["path"]).read_bytes(), data)
        self.assertEqual((self.root / "False" / "inputs.json").read_bytes(),
                         (json.dumps(manifest, ensure_ascii=False, indent=2) + "\n").encode())

    def test_previous_private_serialization_and_compact_private_bytes_both_refuse(self):
        value = {"private": ["A", "B"]}
        forms = ['{\n  "private": [\n    "A",\n    "B"\n  ]\n}', '{"private": ["A", "B"]}', '{"private":["A","B"]}']
        for form in forms:
            with self.subTest(form=form):
                projector = self.create_projector()
                projector.private_bytes = (form.encode(),)
                projector.minimum_private_bytes = len(form.encode())
                with self.assertRaises(ResearchError) as error:
                    projector._emit(value)
                self.assertEqual(error.exception.code, "private_mixed_artifact_required")

    def test_manifest_retains_all_previous_and_compact_privacy_checks(self):
        value = {"private": ["A", "B"]}
        forms = [b'{\n  "private": [\n    "A",\n    "B"\n  ]\n}', b'{"private": ["A", "B"]}', b'{"private":["A","B"]}']
        for form in forms:
            with self.subTest(form=form):
                private = self.artifacts.put(form, "application/json")
                records = {"source_deferral": {"gap": {"authorization": private, "acquisition_evidence": []}}}
                with self.assertRaises(ResearchError) as error:
                    scientific_delivery.project_delivery(records, self.artifacts, self.contract, value)
                self.assertEqual(error.exception.code, "private_mixed_artifact_required")

    def test_generated_and_manifest_depth_limits_are_retained(self):
        value = 0
        for _ in range(40):
            value = {"value": value}
        for manifest in (False, True):
            with self.subTest(manifest=manifest):
                if manifest:
                    project = lambda content: scientific_delivery.project_delivery({}, self.artifacts, self.contract, content)
                else:
                    project = self.create_projector()._emit
                project(value)
                with self.assertRaises(ResearchError) as error:
                    project({"value": value})
                self.assertEqual(error.exception.code, "invalid_scientific_delivery")

    def test_original_per_artifact_limit_still_applies(self):
        value = {"values": list(range(10))}
        pretty = (json.dumps(value, ensure_ascii=False, indent=2) + "\n").encode()
        with mock.patch.object(scientific_delivery, "_MAX_BYTES", len(pretty) - 1):
            with self.assertRaises(ResearchError) as error:
                self.create_projector()._emit(value)
        self.assertEqual(error.exception.code, "invalid_scientific_delivery")


if __name__ == "__main__":
    unittest.main()
