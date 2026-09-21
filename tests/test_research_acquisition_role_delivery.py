"""Public original provenance survives its use as acquisition evidence."""

import copy

from literature_fixtures import LiteratureCase
from research_harness.errors import ResearchError
from research_harness.review_delivery import _deliver
from research_harness.scientific_delivery import project_delivery


class AcquisitionRoleDeliveryTests(LiteratureCase):
    def setup_case(self, pdf=True):
        identifier = self.metadata()
        capture = self.capture(identifier, pdf_text="Synthetic public article.\f" if pdf else None)
        records = self.store.snapshot()["records"]
        private = self.artifacts.put(b"PRIVATE-AUTHORIZATION-SENTINEL", "text/plain")
        records["source_deferral"] = {"gap": {"authorization": private,
            "acquisition_evidence": [dict(capture["original"], media_type="application/octet-stream")]}}
        return identifier, capture, records, private

    def deliver(self, records, ref, name="delivery"):
        manifest = {"original": ref}
        projected, derived = project_delivery(records, self.artifacts,
            {"payload": {"scientific_delivery": []}}, manifest)
        target = self.root / name
        _deliver(self.store, target, projected, derived=derived, transitive=True)
        self.assertEqual(projected, manifest)
        self.assertEqual((target / ref["path"]).read_bytes(), self.artifacts.read(ref))
        self.assertNotIn(b"PRIVATE-AUTHORIZATION-SENTINEL",
            b"".join(p.read_bytes() for p in target.rglob("*") if p.is_file()))

    def test_verified_public_pdf_can_also_be_acquisition_evidence(self):
        _, capture, records, _ = self.setup_case()
        before = copy.deepcopy(records)
        self.deliver(records, capture["original"])
        self.assertEqual(records, before)

    def test_verified_public_html_can_also_be_acquisition_evidence(self):
        _, capture, records, _ = self.setup_case(pdf=False)
        self.deliver(records, capture["original"])

    def test_authorization_role_still_dominates_public_original(self):
        _, capture, records, _ = self.setup_case()
        records["source_deferral"]["other"] = {"authorization": capture["original"], "acquisition_evidence": []}
        with self.assertRaises(ResearchError) as raised:
            self.deliver(records, capture["original"])
        self.assertEqual(raised.exception.code, "private_mixed_artifact_required")

    def test_unverified_or_pending_original_does_not_become_public_by_acquisition_role(self):
        identifier, capture, original, _ = self.setup_case()
        for condition in ("unverified", "incomplete", "pending", "imported"):
            records = copy.deepcopy(original)
            source = records["source"][capture["source_id"]]
            if condition == "unverified":
                source["origin_verified"] = False
            elif condition == "incomplete":
                source["response_complete"] = False
            elif condition == "pending":
                records["work"][identifier]["fulltexts"][0]["availability"] = "pending"
            else:
                source["capture_method"] = "external_import"
            with self.subTest(condition=condition), self.assertRaises(ResearchError):
                self.deliver(records, capture["original"])
            self.assertFalse((self.root / "delivery").exists())

    def test_private_acquisition_notes_and_extractions_keep_private_role(self):
        _, capture, records, _ = self.setup_case()
        for ref in (capture["text"], self.artifacts.put(b"PRIVATE ACQUISITION NOTE", "text/plain")):
            current = copy.deepcopy(records)
            current["source_deferral"]["gap"]["acquisition_evidence"].append(ref)
            with self.subTest(ref=ref), self.assertRaises(ResearchError) as raised:
                self.deliver(current, ref)
            self.assertEqual(raised.exception.code, "private_mixed_artifact_required")

    def test_bad_acquisition_descriptor_is_not_hidden_by_verified_original(self):
        _, capture, records, _ = self.setup_case()
        records["source_deferral"]["gap"]["acquisition_evidence"][0]["size"] += 1
        with self.assertRaises(ResearchError) as raised:
            self.deliver(records, capture["original"])
        self.assertEqual(raised.exception.code, "artifact_corrupt")

    def test_invalid_available_component_alias_is_not_hidden(self):
        identifier, capture, records, _ = self.setup_case()
        invalid = copy.deepcopy(capture)
        invalid["component"] = {"status": "pending"}
        records["work"][identifier]["fulltexts"].append(invalid)
        for reverse in (False, True):
            current = copy.deepcopy(records)
            if reverse:
                current["work"][identifier]["fulltexts"].reverse()
            with self.subTest(reverse=reverse), self.assertRaises(ResearchError):
                self.deliver(current, capture["original"])
            self.assertFalse((self.root / "delivery").exists())

    def test_public_original_cannot_embed_other_private_bytes(self):
        identifier = self.metadata()
        capture = self.capture(identifier, body="PRIVATE-AUTHORIZATION-SENTINEL")
        private = self.artifacts.put(b"PRIVATE-AUTHORIZATION-SENTINEL", "text/plain")
        records = self.store.snapshot()["records"]
        records["source_deferral"] = {"gap": {"authorization": private,
            "acquisition_evidence": [capture["original"]]}}
        with self.assertRaises(ResearchError) as raised:
            self.deliver(records, capture["original"])
        self.assertEqual(raised.exception.code, "private_mixed_artifact_required")
