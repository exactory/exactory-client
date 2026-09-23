"""Post-measurement query captures belong only to their program assessment."""

import copy
import io
import json
from pathlib import Path
import tempfile
import tarfile
import unittest
import zipfile
from types import SimpleNamespace

from research_harness.artifacts import ArtifactStore
from research_harness.errors import ResearchError
from research_harness.scientific_delivery import project_delivery
from research_harness.review_delivery import _deliver


class RoundInvestigationDeliveryTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.artifacts = ArtifactStore(Path(self.directory.name))
        self.contract = {"payload": {"scientific_delivery": []}}

    def prepare(self, data, media_type="application/json"):
        ref = self.artifacts.put(data, media_type)
        analysis = {"id": "analysis", "bundle_digest": "bundle", "investigation": [
            {"query": "Current frontier", "response": ref, "finding": "A candidate to inspect."}]}
        records = {"contribution_analysis": {"analysis": {"bundle_digest": "bundle", "payload": analysis}}}
        manifest = {"kind": "round", "manuscript": {"bundle_digest": "bundle"},
                    "contribution_analysis": copy.deepcopy(analysis)}
        return ref, records, manifest

    def project(self, records, manifest):
        return project_delivery(records, self.artifacts, self.contract, manifest)

    def assert_private(self, records, manifest):
        with self.assertRaises(ResearchError) as raised:
            self.project(records, manifest)
        self.assertEqual(raised.exception.code, "private_mixed_artifact_required")

    def test_original_json_string_and_text_responses_keep_exact_bytes(self):
        for data, media in ((b'{"results":[]}', "application/json"),
                            (b'"Captured web search transcript"', "application/json"),
                            (b"Captured web search transcript", "text/plain"),
                            (b"", "text/plain")):
            with self.subTest(media=media, data=data):
                ref, records, manifest = self.prepare(data, media)
                before = copy.deepcopy(records)
                result, derived = self.project(records, manifest)
                self.assertEqual(result, manifest)
                self.assertEqual(derived, {})
                self.assertEqual(self.artifacts.read(ref), data)
                self.assertEqual(records, before)

    def test_unbound_or_changed_analysis_and_other_review_roles_do_not_qualify(self):
        _, records, manifest = self.prepare(b'{"results":[]}')
        variants = []
        for role in ("manuscript", "readiness"):
            variants.append(dict(manifest, kind=role))
        variants.append(dict(manifest, manuscript={"bundle_digest": "other"}))
        changed = copy.deepcopy(manifest)
        changed["contribution_analysis"]["investigation"][0]["finding"] = "Changed finding"
        variants.append(changed)
        for value in variants:
            with self.subTest(value=value):
                self.assert_private(records, value)
        self.assert_private({}, manifest)

    def test_private_role_and_embedded_private_content_still_fail(self):
        for embed in (False, True):
            private = self.artifacts.put(b"PRIVATE-ROLE-SENTINEL", "text/plain")
            data = b"before PRIVATE-ROLE-SENTINEL after" if embed else b"PRIVATE-ROLE-SENTINEL"
            _, records, manifest = self.prepare(data, "text/plain")
            records["source_deferral"] = {"gap": {"authorization": private, "acquisition_evidence": []}}
            with self.subTest(embed=embed):
                self.assert_private(records, manifest)

    def test_decoded_plain_text_cannot_hide_private_content(self):
        private = self.artifacts.put(b"Private authorization\nSecond private line", "text/plain")
        for encoding in ("utf-16", "utf-16-le", "utf-16-be", "utf-32", "utf-32-le", "utf-32-be"):
            data = ("prefix " + self.artifacts.read(private).decode() + " suffix").encode(encoding)
            _, records, manifest = self.prepare(data, "text/plain; charset=" + encoding)
            records["round_review"] = {"private": {"artifact": private}}
            with self.subTest(encoding=encoding):
                self.assert_private(records, manifest)

    def test_empty_json_captures_preserve_descriptor_through_transitive_delivery(self):
        for index, media in enumerate(("application/json", "application/problem+json; charset=utf-8")):
            with self.subTest(media=media):
                ref, records, manifest = self.prepare(b"", media)
                projected, derived = self.project(records, manifest)
                self.assertEqual(projected, manifest)
                destination = self.artifacts.root / ("delivery-" + str(index))
                _deliver(SimpleNamespace(root=self.artifacts.root, revision=0), destination,
                         projected, derived=derived, transitive=True, empty_captures=(ref,))
                self.assertEqual((destination / ref["path"]).read_bytes(), b"")
                self.assertEqual(json.loads((destination / "inputs.json").read_bytes()), manifest)

    def test_nested_and_encoded_unbound_artifacts_are_not_authorized(self):
        nested = self.artifacts.put(b"Other authored content", "text/plain")
        for value in ({"nested": nested}, {"nested": json.dumps(nested)}):
            _, records, manifest = self.prepare(json.dumps(value).encode())
            with self.subTest(value=value):
                self.assert_private(records, manifest)

    def test_json_string_in_plain_text_cannot_encode_private_bytes_or_references(self):
        data = b"Private line one\nPrivate line two"
        private = self.artifacts.put(data, "text/plain")
        for value in (data.decode(), json.dumps(private)):
            _, records, manifest = self.prepare(json.dumps(value).encode(), "text/plain")
            records["round_review"] = {"private": {"artifact": private}}
            with self.subTest(value=value):
                self.assert_private(records, manifest)

    def test_archive_capture_cannot_bypass_member_projections(self):
        output = io.BytesIO()
        with zipfile.ZipFile(output, "w") as archive:
            archive.writestr("private.txt", "Other authored content")
        _, records, manifest = self.prepare(output.getvalue(), "application/zip")
        self.assert_private(records, manifest)

    def test_tar_and_compressed_captures_cannot_hide_private_content(self):
        private = self.artifacts.put(b"PRIVATE-ARCHIVE-CONTENT", "text/plain")
        for mode, media in (("w", "application/octet-stream"),
                            ("w", "application/x-tar; charset=binary"),
                            ("w:gz", "application/gzip"),
                            ("w:bz2", "application/octet-stream"),
                            ("w:xz", "application/octet-stream")):
            buffer = io.BytesIO()
            with tarfile.open(fileobj=buffer, mode=mode) as archive:
                item = tarfile.TarInfo("private.txt")
                data = self.artifacts.read(private)
                item.size = len(data)
                archive.addfile(item, io.BytesIO(data))
            _, records, manifest = self.prepare(buffer.getvalue(), media)
            records["round_review"] = {"private": {"artifact": private}}
            with self.subTest(mode=mode, media=media):
                self.assert_private(records, manifest)

    def test_descriptor_extensions_cannot_hide_private_references(self):
        _, records, manifest = self.prepare(b'{"results":[]}')
        nested = self.artifacts.put(b"Private descriptor attachment", "text/plain")
        for value in (records["contribution_analysis"]["analysis"]["payload"], manifest["contribution_analysis"]):
            value["investigation"][0]["response"]["attachment"] = nested
        self.assert_private(records, manifest)

    def test_same_bytes_with_another_descriptor_do_not_gain_a_round_role(self):
        ref, records, manifest = self.prepare(b'{"results":[]}')
        manifest["unrelated"] = dict(ref, media_type="text/plain")
        self.assert_private(records, manifest)

    def test_corrupt_capture_is_rejected(self):
        ref, records, manifest = self.prepare(b'{"results":[]}')
        path = Path(self.directory.name) / ref["path"]
        path.chmod(0o600)
        path.write_bytes(b"Corrupt")
        with self.assertRaises(ResearchError) as raised:
            self.project(records, manifest)
        self.assertEqual(raised.exception.code, "artifact_corrupt")
