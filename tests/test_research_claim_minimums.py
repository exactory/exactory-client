"""Direction commitments require decisive reading, field standards and a challenge."""

import copy

from test_research_synthesis import SynthesisCase
from test_research_source_deferrals import DeferralCase
from research_harness import literature, reading, synthesis


class DirectionMinimumTests(DeferralCase, SynthesisCase):
    def assess_minimum(self, records=None):
        from research_harness.claim_minimums import assess_direction
        return assess_direction(self.store.snapshot()["records"] if records is None else records, self.artifacts)

    def prepare_direction(self):
        self.root_version = self.configured()
        self.links = [self.read_source(n) for n in (1, 2, 3)]
        self.decisive = self.links[2]["version_id"]
        search = self.capture_search(found=[self.decisive])
        # Relevant works need full reading even when the author does not cite them.
        self.mutate(literature.record_search, search)
        self.mutate(synthesis.record_standards, self.standards(self.links[1]))
        self.mutate(self.api("challenge").record_grand_challenge, self.grand_challenge(self.links[1]))
        return self.store.snapshot()["records"]

    def assert_only(self, code, records=None, **identities):
        obligations = self.assess_minimum(records)
        self.assertEqual({item["code"] for item in obligations}, {code}, obligations)
        for key, value in identities.items():
            self.assertTrue(any(item.get(key) == value for item in obligations), obligations)
        return obligations

    def remove_readings(self, records, version):
        records["reading"] = {key: value for key, value in records["reading"].items() if value["version_id"] != version}

    def test_complete_minimum_does_not_require_the_other_searches_or_synthesis_sections(self):
        records = self.prepare_direction()
        before = copy.deepcopy(records)
        self.assertEqual(self.assess_minimum(records), [])
        self.assertEqual(records, before)

    def test_direct_search_missing_or_outside_current_scope(self):
        records = self.prepare_direction()
        coverage = [{"query": records["literature_search"]["direct"]["queries"][0], "kind": "top_k"}]
        records["literature_search"]["direct"]["coverage"] = coverage
        for change in ("unselected", "scope", "profile", "purpose"):
            changed = copy.deepcopy(records)
            if change == "unselected":
                changed["search_selection"].pop("research:direct")
            else:
                changed["literature_search"]["direct"][{"scope": "scope_digest"}.get(change, change)] = "other"
            with self.subTest(change=change):
                findings = self.assert_only("direction_search_missing", changed, search_id=None if change == "unselected" else "direct")
                if change != "unselected":
                    self.assertEqual(findings[0]["coverage"], coverage)

    def test_bounded_search_and_legacy_bounded_enumeration_pass(self):
        records = self.prepare_direction()
        search = records["literature_search"]["direct"]
        search["pending"] = [{"code": "search_response_incomplete", "returned_count": 1, "reported_count": 50}]
        self.assertEqual(self.assess_minimum(records), [])
        for kind in ("complete", "top_k", "bounded_unordered"):
            search["coverage"] = [{"query": search["queries"][0], "requested_k": 1, "returned_count": 1,
                "inspected_count": 1, "page_positions": [0], "provider": "mcp", "ordering": None, "kind": kind}]
            search["integrity_errors"] = []
            with self.subTest(kind=kind):
                self.assertEqual(self.assess_minimum(records), [])

    def test_integrity_errors_block_even_when_coverage_is_bounded(self):
        records = self.prepare_direction()
        search = records["literature_search"]["direct"]
        search["coverage"] = [{"query": search["queries"][0], "kind": "bounded_unordered"}]
        search["integrity_errors"] = [{"code": "invalid_work"}]
        obligations = self.assert_only("direction_search_incomplete", records, search_id="direct")
        self.assertEqual(obligations[0]["coverage"], search["coverage"])
        self.assertEqual(obligations[0]["integrity_errors"], search["integrity_errors"])

    def test_direct_search_integrity_failure_or_without_dispositions_is_incomplete(self):
        records = self.prepare_direction()
        for change in ("integrity_errors", "legacy_pending", "dispositions"):
            changed = copy.deepcopy(records)
            search = changed["literature_search"]["direct"]
            if change == "integrity_errors":
                search["pending"] = search["integrity_errors"] = [{"code": "search_page_missing"}]
            elif change == "legacy_pending":
                # A search recorded before integrity_errors existed derives them from pending.
                del search["integrity_errors"]
                search["pending"] = [{"code": "search_page_missing"}]
            else:
                search.pop("dispositions")
            with self.subTest(change=change):
                self.assert_only("direction_search_incomplete", changed, search_id="direct")

    def test_relevant_and_contradictory_uncited_works_need_full_reading(self):
        records = self.prepare_direction()
        self.remove_readings(records, self.decisive)
        for disposition in ("relevant", "contradictory"):
            records["literature_search"]["direct"]["dispositions"][0]["disposition"] = disposition
            with self.subTest(disposition=disposition):
                self.assert_only("direction_reading_missing", records, version_id=self.decisive)

    def test_uncited_decisive_work_needs_every_distinct_original_body(self):
        self.prepare_direction()
        self.capture(self.decisive, body="A distinct original has a changed decisive derivation. References: none.")
        self.assert_only("direction_reading_missing", version_id=self.decisive)

    def test_every_root_needs_full_reading(self):
        records = self.prepare_direction()
        self.remove_readings(records, self.root_version)
        self.assert_only("direction_root_reading_missing", records, version_id=self.root_version)

    def test_standards_selection_missing(self):
        records = self.prepare_direction()
        records["synthesis_selection"].pop("research:standards")
        self.assert_only("direction_standards_missing", records)

    def test_inapplicable_standards_are_unresolved(self):
        self.prepare_direction()
        payload = self.standards(self.links[1], identifier="inapplicable")
        payload["applicability_questions"] = [self.gap()]
        self.mutate(synthesis.record_standards, payload)
        self.assert_only("direction_standards_unresolved", section_id="inapplicable")

    def test_unread_standards_source_is_unresolved(self):
        records = self.prepare_direction()
        self.remove_readings(records, self.links[1]["version_id"])
        self.assert_only("direction_standards_unresolved", records, section_id="standards")

    def test_standards_stale_only_by_foundation_pass_but_other_staleness_blocks(self):
        records = self.prepare_direction()
        # Bounded preparation sections bind the global foundation in this key.
        from research_harness.principles import configuration_state
        from research_harness.evaluation import Evaluation
        records["configuration"]["research"]["preparation_policy"] = {"id": "sampled-v1"}
        evaluation = Evaluation(records, self.artifacts)
        current = synthesis._assess(records, evaluation, "standards", records["synthesis"]["standards"]["payload"],
                                    configuration_state(records, evaluation, "research"),
                                    literature.foundation_state(records, evaluation, "research"))
        records["synthesis"]["standards"]["dependencies"] = dict(current["dependencies"], foundation="previous-foundation")
        self.assertFalse(synthesis.synthesis_state(records, self.artifacts, "research")["sections"]["standards"]["ready"])
        self.assertEqual(self.assess_minimum(records), [])
        records["synthesis"]["standards"]["dependencies"]["scope"] = "previous-scope"
        self.assert_only("direction_standards_unresolved", records, section_id="standards")

    def test_challenge_missing(self):
        records = self.prepare_direction()
        records.pop("grand_challenge_selection")
        self.assert_only("grand_challenge_missing", records)

    def test_deferred_decisive_source_remains_a_reading_gap(self):
        self.prepare_direction()
        gap = self.metadata(4)
        search = self.capture_search(found=[gap])
        search.update(id="with-gap", cited_work_ids=[gap])
        self.mutate(literature.record_search, search)
        self.defer(gap)
        self.assert_only("direction_reading_missing", version_id=gap, deferral_id="defer-1")
