"""Lossless numerical review delivery using synthetic NPY/NPZ bytes only."""

import io
import itertools
import json
from pathlib import Path
import struct
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import zipfile

from research_harness.artifacts import ArtifactStore, describe_artifact
from research_harness.errors import ResearchError
from research_harness.numerical_projection import decode_npy
from research_harness.review_delivery import _deliver
from research_harness.scientific_delivery import project_delivery


def npy(dtype, shape, payload, *, fortran=False, version=1):
    header = repr({"descr": dtype, "fortran_order": fortran, "shape": shape}).encode("utf-8" if version == 3 else "latin-1")
    prefix = 10 if version == 1 else 12
    header += b" " * ((64 - ((prefix + len(header) + 1) % 64)) % 64) + b"\n"
    return b"\x93NUMPY" + bytes((version, 0)) + struct.pack("<H" if version == 1 else "<I", len(header)) + header + payload


class NumericalProjectionTests(unittest.TestCase):
    def assert_invalid(self, data, **kwargs):
        with self.assertRaises(ResearchError) as raised:
            decode_npy(data, **kwargs)
        self.assertEqual(raised.exception.code, "invalid_numerical_projection")

    def test_endian_and_c_fortran_coordinate_reconstruction(self):
        shape = (2, 3)
        for endian, fortran, version in itertools.product(("<", ">"), (False, True), (1, 2, 3)):
            with self.subTest(endian=endian, fortran=fortran, version=version):
                coordinates = list(itertools.product(range(2), range(3)))
                storage = sorted(coordinates, key=lambda c: (c[1], c[0]) if fortran else c)
                values = [10.0 * i + j for i, j in storage]
                array = decode_npy(npy(endian + "f8", shape, struct.pack(endian + "6d", *values), fortran=fortran, version=version))
                for i, j in coordinates:
                    flat = i * array["element_strides"][0] + j * array["element_strides"][1]
                    self.assertEqual(array["values"][flat], 10.0 * i + j)
                    self.assertEqual(array["element_hex"][flat], struct.pack(endian + "d", 10.0 * i + j).hex())
                self.assertEqual(array["storage_order"], "F" if fortran else "C")

    def test_scalar_empty_exact_integers_and_boolean_values(self):
        scalar = decode_npy(npy(">u8", (), struct.pack(">Q", 2**64 - 1)))
        self.assertEqual(scalar["values"], [{"integer": "18446744073709551615"}])
        self.assertEqual(scalar["shape"], [])
        self.assertEqual(scalar["element_count"], 1)
        self.assertEqual(decode_npy(npy("<i8", (1,), struct.pack("<q", -2**63)))["values"], [{"integer": "-9223372036854775808"}])
        self.assertEqual(decode_npy(npy("<f8", (4, 0, 2), b""))["values"], [])
        self.assertEqual(decode_npy(npy("|b1", (2,), b"\x00\x01"))["values"], [False, True])
        self.assert_invalid(npy("|b1", (1,), b"\x02"))

    def test_complex_component_nonfinite_tags_preserve_exact_nan_bits(self):
        for endian in ("<", ">"):
            with self.subTest(endian=endian):
                nan_bits = 0x7ff8000000001234
                payload = struct.pack(endian + "QQ", nan_bits, 0xfff0000000000000)
                array = decode_npy(npy(endian + "c16", (1,), payload))
                self.assertEqual(array["values"], [{"complex": {"real": {"nonfinite": "nan"}, "imag": {"nonfinite": "negative_infinity"}}}])
                self.assertEqual(array["element_hex"], [payload.hex()])
                single = decode_npy(npy(endian + "c8", (), struct.pack(endian + "ff", float("inf"), -0.0)))
                self.assertEqual(single["values"][0]["complex"]["real"], {"nonfinite": "positive_infinity"})
                self.assertEqual(single["element_hex"][0], struct.pack(endian + "ff", float("inf"), -0.0).hex())

    def test_unicode_ucs4_padding_interior_null_and_invalid_scalars(self):
        for endian in ("<", ">"):
            raw = struct.pack(endian + "5I", ord("A"), 0, 0x1F52C, 0, 0)
            array = decode_npy(npy(endian + "U5", (1,), raw))
            self.assertEqual(array["values"], ["A\x00🔬"])
            self.assertEqual(array["element_hex"], [raw.hex()])
            for invalid in (0xD800, 0x110000):
                self.assert_invalid(npy(endian + "U1", (), struct.pack(endian + "I", invalid)))

    def test_parser_rejects_unsupported_shapes_dtypes_truncation_and_bounds(self):
        for dtype in ("=f8", "|f8", "<O8", "<V8", "<f2", ">c32", [("x", "<f8")]):
            with self.subTest(dtype=dtype):
                self.assert_invalid(npy(dtype, (1,), b"\x00" * 8))
        for shape in ((True,), (-1,), (1.5,), (1,) * 33):
            self.assert_invalid(npy("<f8", shape, b""))
        data = npy("<f8", (1,), struct.pack("<d", 1.0))
        for invalid in (data[:-1], data + b"x", data[:9], data.replace(b"\x01\x00", b"\x04\x00", 1)):
            self.assert_invalid(invalid)
        self.assert_invalid(data, remaining_elements=0)
        self.assert_invalid(b"\x93NUMPY\x02\x00" + struct.pack("<I", 65537) + b" " * 65537)
        self.assert_invalid(npy("<f8", (8_000_001,), b""))


class NumericalDeliveryTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.artifacts = ArtifactStore(self.root)

    def project(self, data, projection, *, private=None):
        original = self.artifacts.put(data, "application/octet-stream")
        records = {}
        if private is not None:
            internal = self.artifacts.put(private, "text/plain")
            records["source_deferral"] = {"gap": {"authorization": internal, "acquisition_evidence": []}}
        contract = {"payload": {"scientific_delivery": [{"original_sha256": original["sha256"], "disposition": "project", "projection": projection}]}}
        manifest, derived = project_delivery(records, self.artifacts, contract, {"scientific_evidence": original})
        destination = self.root / "delivered"
        result = _deliver(SimpleNamespace(root=self.root, revision=1), destination, manifest, derived=derived, transitive=True)
        return original, destination, result

    def test_uint8_and_unicode_private_payloads_cannot_be_laundered(self):
        private = b"PRIVATE-AUTHORIZATION-INSTRUCTION"
        for dtype, data in (("|u1", npy("|u1", (len(private),), private)),
                            ("<U36", npy("<U36", (1,), private.decode().encode("utf-32-le").ljust(144, b"\x00")))):
            with self.subTest(dtype=dtype):
                with self.assertRaises(ResearchError) as raised:
                    self.project(data, {"kind": "npy_array", "context": "All exact observed values."}, private=private)
                self.assertEqual(raised.exception.code, "private_mixed_artifact_required")
                self.assertFalse((self.root / "delivered").exists())

    def archive(self, count, *, full_shape=False):
        buffer, members = io.BytesIO(), []
        with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            for index in range(count):
                shape = (242,) if index < 10586 else (3, 242)
                if not full_shape:
                    shape = (1,)
                values = 242 if len(shape) == 1 and full_shape else 726 if full_shape else 1
                # Every member has distinct numerical bytes, preventing deduplication.
                data = npy("<f8", shape, struct.pack("<d", index + 0.25) * values)
                name = "sample_%05d.npy" % index
                archive.writestr(name, data)
                members.append({"path": name, "sha256": describe_artifact(data, "application/octet-stream")["sha256"],
                    "media_type": "application/octet-stream", "projection": {"kind": "npy_array", "context": "The complete synthetic observed array."}})
        return buffer.getvalue(), {"kind": "archive_members", "context": "Every member of the complete synthetic population.", "members": members}

    def test_large_numerical_archive_requires_exact_member_population(self):
        data, projection = self.archive(1002)
        projection["members"].pop()
        with self.assertRaises(ResearchError) as raised:
            self.project(data, projection)
        self.assertEqual(raised.exception.code, "scientific_projection_incomplete")
        self.assertFalse((self.root / "delivered").exists())

    def test_total_output_and_aggregate_elements_fail_before_destination_creation(self):
        from research_harness import scientific_delivery
        self.assertEqual(scientific_delivery._MAX_OUTPUT_BYTES, 512 * 1024 * 1024)
        data, projection = self.archive(3)
        with patch("research_harness.numerical_projection.MAX_ELEMENTS", 2):
            with self.assertRaises(ResearchError) as raised:
                self.project(data, projection)
            self.assertEqual(raised.exception.code, "invalid_numerical_projection")
            self.assertFalse((self.root / "delivered").exists())
        with patch.object(scientific_delivery, "_MAX_OUTPUT_BYTES", 1024):
            with self.assertRaises(ResearchError) as raised:
                self.project(data, projection)
            self.assertEqual(raised.exception.code, "invalid_scientific_delivery")
            self.assertFalse((self.root / "delivered").exists())

    def test_repeated_required_locators_allow_manifest_above_individual_artifact_limit(self):
        value = "Scientific context " + "x" * 65536
        data = json.dumps({"result": value}).encode()
        original = self.artifacts.put(data, "application/json")
        locator = {"kind": "json", "pointer": "/result", "value": value}
        contract = {"payload": {"scientific_delivery": [{"original_sha256": original["sha256"], "disposition": "project",
            "projection": {"kind": "json_locators", "context": "A repeated required result across cumulative scientific branches.", "locators": [locator]}}]}}
        packet = {"branches": [{"artifact": original, "locator": locator}] * 540}
        manifest, derived = project_delivery({}, self.artifacts, contract, packet)
        encoded = json.dumps(manifest, indent=2, ensure_ascii=False).encode()
        self.assertGreater(len(encoded), 64 * 1024 * 1024)
        self.assertLess(len(encoded) + sum(map(len, derived.values())), 512 * 1024 * 1024)
        for entry in manifest["branches"]:
            self.assertEqual(entry["original_locator"], locator)
            self.assertEqual(entry["locator"]["value"], value)
            self.assertEqual(entry["original_sha256"], original["sha256"])
        print("Synthetic repeated-locator manifest bytes=" + str(len(encoded)))

    def test_complete_10964_member_population_fits_with_exact_values_and_byte_closure(self):
        data, projection = self.archive(10964, full_shape=True)
        original, directory, delivered = self.project(data, projection)
        manifest = json.loads((directory / "inputs.json").read_bytes())
        population = json.loads((directory / manifest["scientific_evidence"]["path"]).read_bytes())
        self.assertEqual(population["original_sha256"], original["sha256"])
        self.assertEqual(len(population["entries"]), 10964)
        self.assertEqual(len(delivered["artifacts"]), 10965)
        elements = 0
        for index, entry in enumerate(population["entries"]):
            array = json.loads((directory / entry["artifact"]["path"]).read_bytes())["array"]
            expected_count = 242 if index < 10586 else 726
            self.assertEqual(array["shape"], [242] if index < 10586 else [3, 242])
            self.assertEqual(array["values"], [index + 0.25] * expected_count)
            self.assertEqual(array["element_hex"], [struct.pack("<d", index + 0.25).hex()] * expected_count)
            self.assertEqual(entry["original_sha256"], projection["members"][index]["sha256"])
            elements += array["element_count"]
        self.assertEqual(elements, 2836240)
        total = sum(path.stat().st_size for path in directory.rglob("*") if path.is_file())
        self.assertLess(total, 512 * 1024 * 1024)
        self.assertFalse((directory / original["path"]).exists())
        print("Synthetic complete NPZ: members=10964, elements=2836240, delivered_bytes=" + str(total))
