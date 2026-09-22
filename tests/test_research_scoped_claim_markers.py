"""Continuity markers on a scoped manuscript's claims are round metadata, not claim changes."""

import copy
import json

from source_limited_fixtures import SourceLimitedCase
from research_harness import publication


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
                   "claim_evidence": copy.deepcopy(evidence or self.bundle["claim_evidence"])}
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
