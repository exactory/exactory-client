"""Legacy pins preserve scientific identity when the support reviewer changes."""

import unittest
from unittest.mock import patch

from research_harness import publication
from research_harness.errors import ResearchError
from research_harness.evidence import digest


class LegacyReadinessBindingTests(unittest.TestCase):
    def reports(self):
        original = {"ready": True, "candidate": {"digest": "same-scientific-candidate"},
                    "review": {"ready": True, "digest": "original-review"}, "obligations": []}
        current = dict(original, review={"ready": True, "digest": "current-review"})
        bundle = {"id": "paper", "digest": "paper-digest", "files": {},
                  "readiness_digest": digest(original),
                  "readiness_review": {"id": "original", "assessment": original["review"]}}
        records = {"publication_selection": {"bundle": {"id": "paper"}}, "publication_bundle": {"paper": bundle}}
        return records, bundle, current

    def test_legacy_pin_accepts_a_new_passing_review_of_unchanged_science(self):
        records, bundle, current = self.reports()
        with patch.object(publication, "author_readiness_state", return_value=current):
            self.assertEqual(publication._bundle(records, None), bundle)
        self.assertNotIn("readiness_binding", bundle)

    def test_legacy_pin_still_rejects_changed_scientific_evidence(self):
        records, unused, current = self.reports()
        current["candidate"] = {"digest": "changed-scientific-candidate"}
        with patch.object(publication, "author_readiness_state", return_value=current):
            with self.assertRaises(ResearchError) as caught:
                publication._bundle(records, None)
        self.assertEqual(caught.exception.code, "publication_readiness_stale")

    def test_legacy_pin_cannot_replace_current_failed_support_with_its_old_review(self):
        records, unused, current = self.reports()
        current.update(ready=False, obligations=[{"code": "independent_review_pending"}])
        with patch.object(publication, "author_readiness_state", return_value=current):
            with self.assertRaises(ResearchError) as caught:
                publication._bundle(records, None)
        self.assertEqual(caught.exception.code, "readiness_required")
