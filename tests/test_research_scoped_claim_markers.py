"""Continuity markers on a scoped manuscript's claims are round metadata, not claim changes."""

import copy
import json
from pathlib import Path
import tempfile
import unittest

from integration_fixtures import account_fixture_citations
from source_limited_fixtures import SourceLimitedCase
from research_harness import publication
from research_harness.artifacts import ArtifactStore, describe_artifact
from research_harness.errors import ResearchError
from research_harness.scientific_delivery import ScientificDelivery


class ScopedClaimMarkerTests(SourceLimitedCase):
    def setUp(self):
        super().setUp()
        self.prepare_source_limited()
        self.record_scope()
        self.accept_scope()
        self.bundle = self.scoped_manuscript()
        self.original = json.loads((self.root / self.bundle["files"]["claims"]["path"]).read_text())

    def pin_with(self, claims, identifier, evidence=None):
        path = self.root / self.bundle["files"]["claims"]["path"]
        path.write_text(json.dumps(claims))
        payload = {"id": identifier, "files": {k: v["path"] if v else None for k, v in self.bundle["files"].items()},
                   "claim_evidence": copy.deepcopy(evidence or self.bundle["claim_evidence"]),
                   "citation_accounting": account_fixture_citations(self)}
        return self.mutate(publication.prepare_publication, payload)["result"]

    def test_a_revised_marker_on_an_approved_claim_pins(self):
        claims = copy.deepcopy(self.original)
        claims[0]["revised"] = {"previous": "The earlier wording of this claim.",
                                "reason": "The scope states the corrected wording."}
        result = self.pin_with(claims, "revised-marker")
        self.assertEqual(result["id"], "revised-marker")
        pinned = json.loads((self.root / result["files"]["claims"]["path"]).read_text())
        self.assertEqual(pinned[0]["revised"]["previous"], "The earlier wording of this claim.")

    def test_a_superseded_opening_claim_outside_the_scope_pins(self):
        claims = copy.deepcopy(self.original)
        claims.append({"id": "withdrawn-claim", "claim": "A claim the scope no longer supports.",
                       "superseded": {"reason": "Withdrawn after the comparison failed."}})
        evidence = copy.deepcopy(self.bundle["claim_evidence"])
        evidence.append({"claim_id": "withdrawn-claim", "evidence": evidence[0]["evidence"]})
        result = self.pin_with(claims, "superseded-marker", evidence)
        self.assertEqual(result["id"], "superseded-marker")

    def test_a_revised_marker_does_not_excuse_a_changed_text(self):
        claims = copy.deepcopy(self.original)
        claims[0]["claim"] = "Every integer is bounded by 9."
        claims[0]["revised"] = {"previous": self.original[0]["claim"], "reason": "Widened."}
        self.assert_error("publication_scope_claim_mismatch", lambda: self.pin_with(claims, "changed-text"))

    def test_a_superseded_approved_claim_is_still_a_mismatch(self):
        claims = copy.deepcopy(self.original)
        claims[0]["superseded"] = {"reason": "Withdrawn although the scope still supports it."}
        self.assert_error("publication_scope_claim_mismatch", lambda: self.pin_with(claims, "withdrawn-approved"))


class ScopedMarkerDeliveryTests(SourceLimitedCase):
    def test_blind_delivery_of_a_marked_scoped_bundle_omits_the_markers(self):
        from research_harness.review_delivery import deliver_manuscript
        self.prepare_delivery()
        bundle = self.scoped_manuscript()
        path = self.root / bundle["files"]["claims"]["path"]
        claims = json.loads(path.read_text())
        claims[0]["revised"] = {"previous": "EARLIER-CLAIM-SENTINEL", "reason": "REVISION-REASON-SENTINEL"}
        claims.append({"id": "withdrawn-claim", "claim": "WITHDRAWN-CLAIM-SENTINEL",
                       "superseded": {"reason": "WITHDRAWAL-REASON-SENTINEL"}})
        path.write_text(json.dumps(claims))
        evidence = copy.deepcopy(bundle["claim_evidence"])
        evidence.append({"claim_id": "withdrawn-claim", "evidence": evidence[0]["evidence"]})
        marked = self.mutate(publication.prepare_publication, {"id": "marked-scoped",
            "files": {k: v["path"] if v else None for k, v in bundle["files"].items()}, "claim_evidence": evidence,
            "citation_accounting": account_fixture_citations(self)})["result"]
        destination = self.root / "blind-marked"
        result = deliver_manuscript(self.store, destination)
        manifest = json.loads((destination / "inputs.json").read_text())
        delivered = json.loads((destination / manifest["files"]["claims"]["artifact"]["path"]).read_text())
        self.assertEqual([c["id"] for c in delivered], [c["id"] for c in claims if "superseded" not in c])
        self.assertTrue(all("revised" not in c and "superseded" not in c for c in delivered))
        self.assertEqual([link["claim_id"] for link in manifest["claim_evidence"]], [c["id"] for c in delivered])
        self.assertEqual(manifest["bundle_digest"], marked["digest"])
        for file in destination.rglob("*"):
            if file.is_file():
                contents = file.read_bytes()
                for secret in (b"EARLIER-CLAIM-SENTINEL", b"REVISION-REASON-SENTINEL",
                               b"WITHDRAWN-CLAIM-SENTINEL", b"WITHDRAWAL-REASON-SENTINEL"):
                    self.assertNotIn(secret, contents)
        from research_harness.artifacts import ArtifactStore
        for reference in result["artifacts"]:
            self.assertEqual(ArtifactStore(destination).read(reference), (destination / reference["path"]).read_bytes())


class PreGeneratedDeliveryBytesTests(unittest.TestCase):
    def test_pre_generated_bytes_sit_at_the_path_of_their_content(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        artifacts = ArtifactStore(Path(temporary.name))
        contract = {"payload": {"scientific_delivery": []}}
        data = b'[{"id": "claim", "claim": "A current claim."}]\n'
        ScientificDelivery({}, artifacts, contract, {}, derived={describe_artifact(data, "application/json")["path"]: data})
        elsewhere = describe_artifact(b"other bytes", "application/json")["path"]
        with self.assertRaises(ResearchError) as error:
            ScientificDelivery({}, artifacts, contract, {}, derived={elsewhere: data})
        self.assertEqual(error.exception.code, "artifact_corrupt")
