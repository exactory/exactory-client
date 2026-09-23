"""Later exact native captures establish provenance without erasing failed history."""

import copy
from pathlib import Path

import test_research_components as component_fixtures
from literature_fixtures import LiteratureCase
from research_fixtures import client
from research_harness.acquisition import acquire_fulltext
from research_harness.errors import ResearchError
from research_harness.review_delivery import _deliver
from research_harness.scientific_delivery import project_delivery


class SourceRecaptureDeliveryTests(LiteratureCase):
    def recapture(self, *, recovered=True):
        identifier = self.metadata()
        jpeg = (Path(__file__).parent / "fixtures/research/authored-grid.jpg").read_bytes()
        data = b"%PDF-1.4\n% Synthetic recaptured original.\nstream\n" + jpeg + b"\nendstream\n%%EOF"
        text = "Synthetic original text with identical bytes in both captures.\f"
        captures = []
        for index, status in enumerate(("partial", "extracted") if recovered else ("partial",)):
            http, _, _ = client([(200, {"Content-Type": "application/pdf"}, data)], max_retries=0)
            captures.append(acquire_fulltext(self.store, identifier, "https://arxiv.org/pdf/" + identifier[6:],
                http=http, extractor=lambda value, status=status: {"status": status, "text": text},
                expected_revision=self.store.revision, request_id="recapture-" + str(index))["capture"])
        self.assertEqual(captures[0]["availability"], "pending")
        if recovered:
            self.assertEqual(captures[1]["availability"], "available")
            for field in ("original", "text"):
                self.assertEqual(captures[0][field], captures[1][field])
        return identifier, captures

    def deliver(self, owner, records, capture, destination):
        manifest = {key: capture[key] for key in ("original", "text")}
        contract = {"payload": {"scientific_delivery": []}}
        projected, derived = project_delivery(records, owner.artifacts, contract, manifest)
        target = owner.root / destination
        _deliver(owner.store, target, projected, derived=derived, transitive=True)
        self.assertEqual(projected, manifest)
        for ref in manifest.values():
            self.assertEqual((target / ref["path"]).read_bytes(), owner.artifacts.read(ref))

    def test_later_available_exact_original_and_text_deliver_in_either_order(self):
        identifier, captures = self.recapture()
        before = self.store.snapshot()
        for reverse in (False, True):
            records = copy.deepcopy(before["records"])
            if reverse:
                records["work"][identifier]["fulltexts"].reverse()
            self.deliver(self, records, captures[1], "delivery-" + str(reverse))
        self.assertEqual(self.store.snapshot(), before)

    def test_pending_only_cannot_fall_back_to_raw_source_response(self):
        _, captures = self.recapture(recovered=False)
        records = self.store.snapshot()["records"]
        self.assertEqual(records["source"][captures[0]["source_id"]]["response"], captures[0]["original"])
        with self.assertRaises(ResearchError) as error:
            self.deliver(self, records, captures[0], "refused")
        self.assertEqual(error.exception.code, "private_mixed_artifact_required")
        self.assertFalse((self.root / "refused").exists())

    def component_owner(self):
        owner = component_fixtures.ComponentTests()
        owner.setUp()
        self.addCleanup(owner.doCleanups)
        return owner

    def test_pending_component_then_exact_bound_component_delivers(self):
        owner = self.component_owner()
        pending = owner.acquire(assessment=dict(owner.assessment, status="pending"))["capture"]
        available = owner.acquire()["capture"]
        self.assertEqual(pending["availability"], "pending")
        self.assertEqual(available["availability"], "available")
        self.assertEqual(pending["original"], available["original"])
        self.assertEqual(pending["text"], available["text"])
        before = owner.store.snapshot()
        for reverse in (False, True):
            records = copy.deepcopy(before["records"])
            if reverse:
                records["work"][owner.identifier]["fulltexts"].reverse()
            self.deliver(owner, records, available, "component-" + str(reverse))
        self.assertEqual(owner.store.snapshot(), before)

    def test_invalid_available_component_cannot_use_raw_response_fallback(self):
        owner = self.component_owner()
        capture = owner.acquire()["capture"]
        records = owner.store.snapshot()["records"]
        records["work"][owner.identifier]["fulltexts"][-1]["component"]["status"] = "pending"
        with self.assertRaises(ResearchError):
            self.deliver(owner, records, capture, "refused")
        self.assertFalse((owner.root / "refused").exists())

    def test_invalid_available_alias_cannot_hide_behind_valid_capture_order(self):
        identifier, captures = self.recapture()
        for reverse in (False, True):
            records = self.store.snapshot()["records"]
            invalid = copy.deepcopy(captures[1])
            invalid["component"] = {"status": "pending"}
            records["work"][identifier]["fulltexts"].append(invalid)
            if reverse:
                records["work"][identifier]["fulltexts"].reverse()
            with self.assertRaises(ResearchError):
                self.deliver(self, records, captures[1], "refused")
            self.assertFalse((self.root / "refused").exists())

    def test_exact_jpeg_span_uses_later_available_original(self):
        _, captures = self.recapture()
        jpeg = (Path(__file__).parent / "fixtures/research/authored-grid.jpg").read_bytes()
        ref = self.artifacts.put(jpeg, "image/jpeg")
        original = captures[1]["original"]
        start = self.artifacts.read(original).index(jpeg)
        projection = {"kind": "source_component", "context": "The exact recaptured public figure.",
                      "source_sha256": original["sha256"], "start": start, "end": start + len(jpeg)}
        contract = {"payload": {"scientific_delivery": [{"original_sha256": ref["sha256"],
                    "disposition": "project", "projection": projection}]}}
        projected, derived = project_delivery(self.store.snapshot()["records"], self.artifacts, contract,
                                             {"figure": ref, "source": original})
        target = self.root / "figure-delivery"
        _deliver(self.store, target, projected, derived=derived, transitive=True)
        self.assertEqual((target / ref["path"]).read_bytes(), jpeg)
        self.assertEqual((target / original["path"]).read_bytes(), self.artifacts.read(original))

    def test_private_roles_override_both_capture_orders(self):
        identifier, captures = self.recapture()
        for field in ("original", "text"):
            for reverse in (False, True):
                records = self.store.snapshot()["records"]
                records["source_deferral"] = {"private": {"authorization": captures[1][field], "acquisition_evidence": []}}
                if reverse:
                    records["work"][identifier]["fulltexts"].reverse()
                with self.assertRaises(ResearchError) as error:
                    self.deliver(self, records, captures[1], "refused")
                self.assertEqual(error.exception.code, "private_mixed_artifact_required")
                self.assertFalse((self.root / "refused").exists())

    def test_recapture_does_not_hide_corrupt_original_bytes(self):
        _, captures = self.recapture()
        ref = captures[1]["original"]
        path = self.root / ref["path"]
        path.chmod(0o600)
        path.write_bytes(b"Corrupted fixture original")
        with self.assertRaises(ResearchError) as error:
            self.deliver(self, self.store.snapshot()["records"], captures[1], "refused")
        self.assertEqual(error.exception.code, "artifact_corrupt")
        self.assertFalse((self.root / "refused").exists())
