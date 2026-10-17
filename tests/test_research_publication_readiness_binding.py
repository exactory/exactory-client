"""Legacy pins preserve scientific identity when the support reviewer changes."""

import json
import unittest
from unittest.mock import patch

from research_harness import publication
from research_harness.errors import ResearchError
from research_harness.evidence import digest
from development_fixtures import DevelopmentCase


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

    def test_managed_scientific_binding_projects_advisory_preparation(self):
        records, unused, current = self.reports()
        records.update(configuration={"research": {"profile": "research"}}, workspace={"study": {}})
        original = dict(current, synthesis={"preparation_digest": "old"},
                        review_inputs={"synthesis": {"digest": "old"}, "branches": {}})
        refreshed = dict(current, synthesis={"preparation_digest": "new"},
                         review_inputs={"synthesis": {"digest": "new"}, "branches": {}})
        self.assertEqual(publication._scientific_readiness(original, records),
                         publication._scientific_readiness(refreshed, records))
        refreshed["candidate"] = {"digest": "changed-evidence"}
        self.assertNotEqual(publication._scientific_readiness(original, records),
                            publication._scientific_readiness(refreshed, records))


class ManagedPublicationCurrencyTests(DevelopmentCase):
    def prepare_managed_bundle(self, legacy=False, *, extra_abstract=False):
        from contextlib import ExitStack
        from managed_strategy_fixtures import ManagedStrategy
        from integration_fixtures import observed_candidate, account_fixture_citations
        from research_harness import development
        self.prepared_study()
        if extra_abstract:
            from research_harness.reading import record_reading
            self.mutate(record_reading, self.abstract_note(self.links[0]["version_id"], "earlier-unused-abstract"))
        ManagedStrategy(self).ensure_intent()
        with ExitStack() as stack:
            if legacy:
                stack.enter_context(patch.object(development, "uses_claim_dependencies", return_value=False))
                stack.enter_context(patch.object(publication, "uses_claim_dependencies", return_value=False))
            observed_candidate(self)
            (self.root / "draft").mkdir()
            (self.root / "draft/paper.pdf").write_bytes(b"%PDF-1.4\n% finite fixture\n%%EOF")
            (self.root / "draft/abstract.txt").write_text("The maximum is nine.")
            (self.root / "draft/references.bib").write_text("@article{bounded,title={Authored bound}}\n")
            (self.root / "draft/claims.json").write_text(json.dumps([{"id": "bound", "claim": "The maximum is nine."}]))
            return self.mutate(publication.prepare_publication, {"id": "managed-paper",
                "files": {"pdf": "draft/paper.pdf", "abstract": "draft/abstract.txt", "bibliography": "draft/references.bib",
                          "claims": "draft/claims.json", "sources": None},
                "claim_evidence": [{"claim_id": "bound", "evidence": [self.result_evidence(self.execution_payload)]}],
                "citation_accounting": account_fixture_citations(self)})["result"]

    def test_managed_manuscript_binding_survives_unrelated_synthesis_and_search(self):
        bundle = self.prepare_managed_bundle()
        self.refresh_synthesis("later-synthesis")
        self.record_purpose("direct", "later-direct")
        self.assertEqual(publication._bundle(self.store.snapshot()["records"], self.artifacts), bundle)

    def test_legacy_manuscript_binding_survives_an_unrelated_reading(self):
        bundle = self.prepare_managed_bundle(legacy=True)
        self.assertEqual(publication._bundle(self.store.snapshot()["records"], self.artifacts), bundle)
        self.read_source(2)
        self.assertEqual(publication._bundle(self.store.snapshot()["records"], self.artifacts), bundle)

    def test_previous_broad_pin_preserves_a_legacy_alias_review(self):
        import copy
        from integration_fixtures import account_fixture_citations
        from research_harness import development
        from research_harness.reading import record_reading
        original = self.prepare_managed_bundle(legacy=True, extra_abstract=True)
        records = self.store.snapshot()["records"]
        review_id = records["development_selection"]["review"]["review_id"]
        payload = copy.deepcopy(records["readiness_review"][review_id]["payload"])
        payload["id"] = "previous-broad-alias"
        project = publication.project_claim_dependencies

        def project_broad(value, **options):
            return project(value, **dict(options, project_readings=False))

        with patch.object(development, "project_source_inputs", lambda sources, evidence: sources), \
                patch.object(publication, "project_claim_dependencies", project_broad):
            self.mutate(development.record_readiness_review, payload)
            bundle = self.mutate(publication.prepare_publication, {
                "id": "previous-broad-pin",
                "files": {kind: item["path"] if item else None for kind, item in original["files"].items()},
                "claim_evidence": original["claim_evidence"],
                "citation_accounting": account_fixture_citations(self)})["result"]
        self.assertEqual(publication._bundle(self.store.snapshot()["records"], self.artifacts), bundle)
        self.mutate(record_reading, self.abstract_note(self.links[0]["version_id"], "later-unused-abstract"))
        self.assertEqual(publication._bundle(self.store.snapshot()["records"], self.artifacts), bundle)

    def test_legacy_manuscript_accepts_new_projected_support_for_unchanged_science(self):
        bundle = self.prepare_managed_bundle(legacy=True)
        self.mutate(self.development().record_readiness_review, self.review(self.execution_payload, identifier="new-support"))
        self.assertEqual(publication._bundle(self.store.snapshot()["records"], self.artifacts), bundle)

    def test_older_manuscript_without_a_binding_accepts_new_projected_support(self):
        bundle = self.prepare_managed_bundle(legacy=True)
        self.mutate(self.development().record_readiness_review, self.review(self.execution_payload, identifier="new-support"))
        records = self.store.snapshot()["records"]
        original = dict(bundle)
        original.pop("readiness_binding")
        original["digest"] = digest({key: value for key, value in original.items() if key != "digest"})
        records["publication_bundle"][bundle["id"]] = original
        self.assertEqual(publication._bundle(records, self.artifacts), original)

    def test_changed_supported_science_requires_repinning_the_manuscript(self):
        self.prepare_managed_bundle()
        plan = self.store.snapshot()["records"]["cycle_plan"]["cycle-1"]["payload"]
        assessed = self.assessment(plan, self.execution_payload, identifier="reassessed")
        assessed["result"]["statement"] = "The finite maximum is attained at n = 3."
        self.mutate(self.development().assess_cycle, assessed)
        self.save_checkpoint(assessment_id="reassessed", identifier="new-checkpoint")
        self.mutate(self.development().record_readiness_review, self.review(self.execution_payload, identifier="new-support"))
        self.assertTrue(self.development().readiness_report(self.store)["ready"])
        self.assert_error("publication_readiness_stale", lambda: publication._bundle(self.store.snapshot()["records"], self.artifacts))
