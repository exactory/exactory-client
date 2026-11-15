"""UB2 readings of another record of a work, on synthetic incident-shaped stores."""

import copy
import json
from unittest.mock import patch

from test_research_problem_claims import ProblemCase
import test_research_problem_openness_minimum as fixtures
import test_research_work_families as family_fixtures
from research_harness import problem_openness
from research_harness.errors import ResearchError
from research_harness.evidence import digest
from research_harness.literature import import_bundle
from research_harness.openness_minimum import audit_minimum, list_minimum_gaps
from research_harness.reading import record_reading


class FamilyReadingTests(ProblemCase):
    search = fixtures.ProblemOpennessMinimumTests.search
    read = fixtures.ProblemOpennessMinimumTests.read
    import_work = family_fixtures.WorkFamilyTests.import_work
    acquire_shared_doi_pair = family_fixtures.WorkFamilyTests.acquire_shared_doi_pair

    def setUp(self):
        super().setUp()
        self.initialize_problem()
        self.selected = self.select_statement()

    def build_spec(self, searches, family_readings=()):
        return {"assignment_id": "family-review", "reviewer_id": "family-reviewer", "minimum": {
            "searches": [{"formulation": row["question"], "search_id": row["id"]} for row in searches],
            "settling_work_id": None, "duplicate_resolutions": [], "family_readings": list(family_readings)}}

    def build_minimum(self, searches, family_readings=()):
        return problem_openness.build_minimum(self.selected, [(row["question"], row) for row in searches], {},
            self.build_spec(searches, family_readings)["minimum"], records=self.store.snapshot()["records"],
            artifacts=self.artifacts)

    def build_binding(self, search, found, version):
        return {"search_digest": digest(search), "work_id": found, "version_id": version}

    def audit(self, minimum, *, records=None, delivered=None, outcome="supported_open"):
        evidence = [row["search_digest"] for row in minimum["searches"]] + minimum["reading_digests"]
        finding = {"outcome": outcome, "minimum": minimum, "evidence": evidence, "searched_at": "2026-10-17"}
        audit_minimum(finding, self.selected["statement"], records or self.store.snapshot()["records"], self.artifacts,
                      set(evidence) if delivered is None else delivered)

    def export_v2(self, spec, destination):
        # UB6 changes this one schema choice in production. All export work is real.
        builder = problem_openness.build_minimum
        def build_v2(*args, **kwargs):
            kwargs["schema_version"] = 2
            return builder(*args, **kwargs)
        with patch.object(problem_openness, "build_minimum", build_v2):
            return problem_openness.export_openness(self.store, spec, destination)

    def assert_bound(self, minimum, search, found, version, reading):
        self.assertEqual(minimum["family_readings"], [self.build_binding(search, found, version)])
        self.assertEqual(minimum["reading_digests"], [digest(reading)])
        self.audit(minimum)

    def test_search_export_family_reading_and_supported_open_audit_journey(self):
        # Source: 20261112135015-openness-reading-turn-stopped-by-provider-safeguards T9.
        # The search alone imports the versionless hit, as a citation-list response does.
        version = self.metadata()
        reading = self.read(version)
        found = "arxiv:2601.00001"
        search = self.search([found])
        before = self.store.snapshot()["records"]
        result = self.export_v2(self.build_spec([search]), self.root / "family-packet")
        packet = json.loads(self.artifacts.read(result["native_packet"]))
        minimum = packet["scientific_context"]["openness_minimum"]
        self.assert_bound(minimum, search, found, version, reading)
        self.assertEqual(result["derived_family_readings"], [dict(self.build_binding(search, found, version),
            link={"kind": "arxiv_version", "identifier": found, "authors_compared": False})])
        self.assertEqual(result["audit_forecast"]["outcomes"]["supported_open"], "possible")
        delivered = {row["digest"] for row in packet["evidence"]}
        self.audit(minimum, delivered=delivered)
        capture = before["work"][version]["fulltexts"][0]
        self.assertIn(capture["text"]["sha256"], delivered)
        self.assertEqual(self.store.snapshot()["records"], before)

    def test_shared_doi_asserted_by_both_acquired_families_binds_and_reports_author_comparison(self):
        found, version, doi = self.acquire_shared_doi_pair()
        reading = self.read(version)
        search = self.search([found])
        result = self.export_v2(self.build_spec([search]), self.root / "shared-packet")
        minimum = json.loads(self.artifacts.read(result["native_packet"]))["scientific_context"]["openness_minimum"]
        self.assert_bound(minimum, search, found, version, reading)
        self.assertEqual(result["derived_family_readings"][0]["link"],
                         {"kind": "shared_doi", "identifier": doi, "authors_compared": True})

    def test_datacite_work_id_and_alias_bind_the_read_version(self):
        version = self.metadata()
        reading = self.read(version)
        doi = "doi:10.48550/arxiv.2601.00001"
        second_version = self.metadata(2)
        second_reading = self.read(second_version, "second-full")
        alias = "doi:10.48550/arxiv.2601.00002"
        works = [self.import_work(doi, authors=()), self.import_work("openalex:W123", aliases=[alias], authors=())]
        search = self.search(works)
        minimum = self.build_minimum([search])
        self.assertEqual(minimum["family_readings"], [self.build_binding(search, works[0], version),
                                                   self.build_binding(search, works[1], second_version)])
        self.assertEqual(set(minimum["reading_digests"]), {digest(reading), digest(second_reading)})
        self.audit(minimum)
        result = self.export_v2(self.build_spec([search]), self.root / "datacite-packet")
        self.assertEqual([row["link"] for row in result["derived_family_readings"]],
                         [{"kind": "datacite_doi", "identifier": identifier, "authors_compared": False}
                          for identifier in (doi, alias)])

    def test_doi_link_with_missing_authors_on_either_record_binds_and_reports_no_comparison(self):
        found, version, doi = self.acquire_shared_doi_pair()
        self.read(version)
        search = self.search([found])
        for side in (found, version):
            record = copy.deepcopy(self.store.snapshot()["records"]["work"][side])
            self.put("work", side, dict(record, authors=[]))
            result = self.export_v2(self.build_spec([search]), self.root / ("missing-authors-" + str(self.sequence)))
            self.assertEqual(result["derived_family_readings"][0]["link"],
                             {"kind": "shared_doi", "identifier": doi, "authors_compared": False})
            self.put("work", side, record)

    def test_third_family_doi_assertion_prevents_derivation_and_invalidates_a_bound_link(self):
        found, version, doi = self.acquire_shared_doi_pair()
        self.read(version)
        search = self.search([found])
        minimum = self.build_minimum([search])
        self.assertEqual(minimum["family_readings"], [self.build_binding(search, found, version)])
        self.import_work("openalex:W999", aliases=[doi], authors=["A. Researcher"])
        self.assertEqual(self.build_minimum([search])["family_readings"], [])
        self.assert_error("openness_reading_gap", lambda: self.audit(minimum))
        self.assert_error("openness_reading_gap", lambda: self.build_minimum([search], minimum["family_readings"]))

    def test_author_disagreement_or_no_shared_identifier_leaves_a_reading_gap(self):
        version = self.metadata()
        self.read(version)
        doi = "doi:10.1234/shared"
        record = copy.deepcopy(self.store.snapshot()["records"]["work"][version])
        record["aliases"] = [doi]
        self.put("work", version, record)
        found = self.import_work("openalex:W123", aliases=[doi], authors=["Different Author"])
        unrelated = self.import_work("openalex:W456")
        search = self.search([found, unrelated])
        minimum = self.build_minimum([search])
        self.assertEqual(minimum["family_readings"], [])
        for work in (found, unrelated):
            self.assert_error("openness_reading_gap", lambda: self.build_minimum(
                [search], [self.build_binding(search, work, version)]))
        self.assert_error("openness_reading_gap", lambda: self.audit(minimum))

    def test_latest_complete_version_is_numeric_and_spec_can_choose_an_older_reading(self):
        found = "arxiv:2601.00001"
        first, latest = self.metadata(version=2), self.metadata(version=10)
        old_reading, latest_reading = self.read(first, "version-two"), self.read(latest, "version-ten")
        self.metadata(version=11)  # Unread newer versions cannot displace a complete reading.
        search = self.search([found])
        self.assert_bound(self.build_minimum([search]), search, found, latest, latest_reading)
        selected = self.build_minimum([search], [self.build_binding(search, found, first)])
        self.assert_bound(selected, search, found, first, old_reading)
        result = self.export_v2(self.build_spec([search], selected["family_readings"]), self.root / "chosen-packet")
        self.assertEqual(result["derived_family_readings"], [])
        packet = json.loads(self.artifacts.read(result["native_packet"]))
        self.assertEqual(packet["scientific_context"]["openness_minimum"], selected)
        self.audit(selected, delivered={row["digest"] for row in packet["evidence"]})

    def test_spec_refuses_unread_partial_unknown_and_versionless_targets_before_export_writes(self):
        version = self.metadata()
        partial = self.metadata(version=2)
        bundle = self.bundle(partial)
        self.mutate(import_bundle, bundle)
        self.mutate(record_reading, self.full_note(bundle, "partial", omit=("body",)))
        found = "arxiv:2601.00001"
        search = self.search([found])
        for target in (version, partial, found, "arxiv:2601.00001v99"):
            before = sorted(self.root.rglob("*"))
            with self.subTest(target=target):
                self.assert_error("openness_reading_gap", lambda: self.export_v2(
                    self.build_spec([search], [self.build_binding(search, found, target)]), self.root / "refused"))
                self.assertEqual(sorted(self.root.rglob("*")), before)

    def test_spec_refuses_a_nondecisive_work_or_an_unbound_search(self):
        version = self.metadata()
        self.read(version)
        found = "arxiv:2601.00001"
        search = self.search([found], dispositions={found: "out_of_scope"})
        for row in (self.build_binding(search, found, version),
                    dict(self.build_binding(search, found, version), search_digest="f" * 64)):
            with self.subTest(binding=row):
                self.assert_error("openness_reading_gap", lambda: self.build_minimum([search], [row]))

    def test_family_bindings_remain_specific_to_each_search(self):
        version = self.metadata()
        reading = self.read(version)
        found = "arxiv:2601.00001"
        first = self.search([found])
        second = self.search([found], query="Another query")
        minimum = self.build_minimum([first, second])
        self.assertEqual(minimum["family_readings"], [self.build_binding(search, found, version) for search in (first, second)])
        self.assertEqual(minimum["reading_digests"], [digest(reading)])
        self.audit(minimum)
        minimum["family_readings"].pop()
        self.assert_error("openness_reading_gap", lambda: self.audit(minimum))

    def test_one_search_bound_to_two_formulations_derives_each_family_reading_once(self):
        neighbour = "An equivalent formulation of the bound."
        self.selected = self.select_statement(identifier="statement-2", neighbours=[neighbour])
        version = self.metadata()
        reading = self.read(version)
        found = "arxiv:2601.00001"
        search = self.search([found], formulation=neighbour, query=self.selected["statement"])
        spec = self.build_spec([search])
        spec["minimum"]["searches"].insert(0, {"formulation": self.selected["statement"], "search_id": search["id"]})
        result = self.export_v2(spec, self.root / "two-formulations")
        packet = json.loads(self.artifacts.read(result["native_packet"]))
        minimum = packet["scientific_context"]["openness_minimum"]
        self.assertEqual(len(minimum["searches"]), 2)
        self.assert_bound(minimum, search, found, version, reading)
        self.assertEqual(len(result["derived_family_readings"]), 1)

    def test_audit_requires_the_chosen_versions_pinned_complete_reading_and_delivery(self):
        version = self.metadata()
        reading = self.read(version)
        search = self.search(["arxiv:2601.00001"])
        minimum = self.build_minimum([search])
        self.assertEqual(minimum["reading_digests"], [digest(reading)])
        self.audit(minimum)
        self.assert_error("openness_evidence_mismatch", lambda: self.audit(minimum, delivered={digest(search)}))
        minimum["reading_digests"] = []
        self.assert_error("openness_reading_gap", lambda: self.audit(minimum))
        gaps = list_minimum_gaps(minimum, self.selected["statement"], self.store.snapshot()["records"], self.artifacts)
        self.assertEqual([gap["work_id"] for gap in gaps if gap["condition"] == "reading_missing"], ["arxiv:2601.00001"])

    def test_own_complete_reading_takes_precedence_and_family_spec_cannot_replace_it(self):
        found, version, _ = self.acquire_shared_doi_pair()
        self.read(version)
        own = self.read(found, "own-full")
        search = self.search([found])
        minimum = self.build_minimum([search])
        self.assertEqual(minimum["family_readings"], [])
        self.assertEqual(minimum["reading_digests"], [digest(own)])
        self.audit(minimum)
        self.assert_error("openness_reading_gap", lambda: self.build_minimum(
            [search], [self.build_binding(search, found, version)]))

    def test_audit_refuses_a_forged_search_work_or_version_binding(self):
        version = self.metadata()
        self.read(version)
        found = "arxiv:2601.00001"
        other = self.metadata(2)
        search = self.search([found, other], dispositions={other: "out_of_scope"})
        minimum = self.build_minimum([search])
        self.assertEqual(minimum["family_readings"], [self.build_binding(search, found, version)])
        for change in ({"search_digest": "f" * 64}, {"work_id": other}, {"version_id": found},
                       {"version_id": "arxiv:2601.00001v99"}, {"version_id": found, "work_id": found}):
            candidate = copy.deepcopy(minimum)
            candidate["family_readings"][0].update(change)
            with self.subTest(change=change):
                self.assert_error("openness_reading_gap", lambda: self.audit(candidate))

    def test_audit_requires_the_reading_of_the_named_version_even_when_another_is_complete(self):
        first, second = self.metadata(), self.metadata(version=2)
        old_reading, new_reading = self.read(first, "old-full"), self.read(second, "new-full")
        found = "arxiv:2601.00001"
        search = self.search([found])
        minimum = self.build_minimum([search], [self.build_binding(search, found, first)])
        self.assertEqual(minimum["reading_digests"], [digest(old_reading)])
        minimum["reading_digests"] = [digest(new_reading)]
        self.assert_error("openness_reading_gap", lambda: self.audit(minimum))

    def test_settling_work_uses_its_family_reading_for_an_already_solved_finding(self):
        version = self.metadata()
        reading = self.read(version)
        found = "arxiv:2601.00001"
        search = self.search([found], dispositions={found: "contradictory"})
        spec = self.build_spec([search])["minimum"]
        spec["settling_work_id"] = found
        minimum = problem_openness.build_minimum(self.selected, [(search["question"], search)], {}, spec,
            records=self.store.snapshot()["records"], artifacts=self.artifacts)
        self.assertEqual(minimum["family_readings"], [self.build_binding(search, found, version)])
        self.assertEqual(minimum["reading_digests"], [digest(reading)])
        self.audit(minimum, outcome="already_solved")
        self.assert_error("openness_settling_source", lambda: self.audit(minimum))

    def test_v1_keeps_its_gap_and_exact_bytes_when_a_family_reading_is_available(self):
        version = self.metadata()
        reading = self.read(version)
        search = self.search(["arxiv:2601.00001"])
        minimum = self.build_minimum([search])
        self.assertTrue(minimum["family_readings"])
        v1 = {key: value for key, value in minimum.items()
              if key not in {"family_readings", "supporting_reading_digests", "results"}}
        v1["schema_version"] = 1
        original = problem_openness.serialize_record(v1)
        self.assert_error("missing_version", lambda: self.audit(v1))
        self.assertEqual(problem_openness.serialize_record(v1), original)
        self.assertEqual(v1["reading_digests"], [digest(reading)])
