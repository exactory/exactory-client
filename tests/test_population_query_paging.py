"""Population queries expose coverage and traverse one authenticated revision."""

import json

from literature_fixtures import LiteratureCase
from research_fixtures import atom, client, entry, xml_response
from research_harness.acquisition import collect_cohort
from research_harness.errors import ResearchError
from research_harness.evidence import digest
from research_harness.lineage import population_query
from research_harness.storage import Store


class PopulationQueryPagingTests(LiteratureCase):
    def collect_entries(self, entries):
        http, _, _ = client([xml_response(atom(entries, total=len(entries)))])
        self.sequence += 1
        return collect_cohort(self.store, {
            "corpus": "arxiv", "primaryCategory": "cs.LG",
            "windowStart": "2026-01-01", "windowEnd": "2026-01-31",
        }, http=http, expected_revision=self.store.revision,
            request_id="population-" + str(self.sequence))

    def test_default_page_discloses_the_thirty_first_match_without_writes(self):
        self.cohort(range(1, 32))
        before = self.store.snapshot()
        files_before = {p.relative_to(self.root): p.read_bytes()
                        for p in self.root.rglob("*") if p.is_file()}

        first = population_query(self.store, ["bounded"])
        self.assertEqual(first["terms"], ["bounded"])
        self.assertEqual(first["population"], 31)
        self.assertTrue(first["read_only"])
        self.assertEqual(first["revision"], before["revision"])
        self.assertEqual(first["query_digest"], digest(["bounded"]))
        self.assertEqual((first["total_matches"], first["returned"], first["offset"]), (31, 30, 0))
        self.assertEqual((first["omitted"], first["remaining"]), (1, 1))
        self.assertEqual((first["searchable"], first["unavailable"]), (31, 0))
        self.assertTrue(first["bound_reached"])
        self.assertIsInstance(first["next_cursor"], str)

        second = population_query(Store(self.root), ["bounded"], cursor=first["next_cursor"])
        self.assertEqual((second["total_matches"], second["returned"], second["offset"]), (31, 1, 30))
        self.assertEqual((second["omitted"], second["remaining"]), (30, 0))
        self.assertEqual(second["matches"][0]["version_id"], "arxiv:2601.00031v1")
        self.assertEqual(second["ranking_version"], first["ranking_version"])
        self.assertFalse(second["bound_reached"])
        self.assertIsNone(second["next_cursor"])
        self.assert_error("invalid_input", lambda: population_query(
            self.store, ["finite"], cursor=first["next_cursor"]))
        self.assertEqual(self.store.snapshot(), before)
        self.assertEqual({p.relative_to(self.root): p.read_bytes()
                          for p in self.root.rglob("*") if p.is_file()}, files_before)

    def test_traversal_preserves_scores_prefix_matching_and_all_match_fields(self):
        abstracts = {
            1: "Gravity and finite models.", 2: "Gravity alone.",
            3: "Finite models.", 4: "Gravitational models.",
            5: "GRAVITY and FINITELY many models.", 6: "Antigravity alone.",
            7: "Unrelated models.", 8: "Gravity and finite bounds.",
        }
        self.collect_entries([entry("2601.%05dv1" % n, abstract=abstracts[n])
                              for n in reversed(sorted(abstracts))])
        expected = []
        for number, matched in ((1, ["gravity", "finite"]), (5, ["gravity", "finite"]),
                                (8, ["gravity", "finite"]), (2, ["gravity"]), (3, ["finite"])):
            expected.append({"version_id": "arxiv:2601.%05dv1" % number,
                             "work_id": "arxiv:2601.%05d" % number,
                             "title": "An authored example", "matched_terms": matched,
                             "score": len(matched)})
        actual, cursor, offset = [], None, 0
        for size in (2, 1, 2):
            page = population_query(Store(self.root), ["gravity", "finite"], limit=size, cursor=cursor)
            self.assertEqual(page["offset"], offset)
            self.assertEqual(page["total_matches"], len(expected))
            self.assertEqual(page["omitted"], len(expected) - len(page["matches"]))
            offset += len(page["matches"])
            self.assertEqual(page["remaining"], len(expected) - offset)
            actual.extend(page["matches"])
            cursor = page["next_cursor"]
        self.assertEqual(actual, expected)
        self.assertEqual(len({match["version_id"] for match in actual}), len(expected))
        self.assertIsNone(cursor)
        self.assertTrue(page["bound_reached"])

    def test_tied_scores_continue_in_version_id_order(self):
        self.cohort((12, 2, 11, 1, 10, 3))
        first = population_query(self.store, ["bounded", "finite"], limit=3)
        second = population_query(self.store, ["bounded", "finite"], limit=3, cursor=first["next_cursor"])
        self.assertEqual([m["version_id"] for m in first["matches"] + second["matches"]],
                         ["arxiv:2601.%05dv1" % n for n in (1, 2, 3, 10, 11, 12)])
        self.assertEqual([m["score"] for m in first["matches"] + second["matches"]], [2] * 6)
        self.assertTrue(second["bound_reached"])
        self.assertIsNone(second["next_cursor"])

    def test_any_committed_revision_change_stales_a_cursor(self):
        self.cohort((1, 2, 3))
        first = population_query(self.store, ["bounded"], limit=1)
        self.metadata(999)
        before = self.store.snapshot()
        with self.assertRaises(ResearchError) as raised:
            population_query(Store(self.root), ["bounded"], cursor=first["next_cursor"])
        self.assertEqual(raised.exception.code, "stale_cursor")
        self.assertEqual(raised.exception.details["revision"], before["revision"])
        self.assertEqual(self.store.snapshot(), before)

    def test_cursor_binds_the_ordered_query(self):
        self.cohort((1, 2, 3))
        first = population_query(self.store, ["bounded", "finite"], limit=1)
        for terms in (["finite", "bounded"], ["bounded"], ["Bounded", "finite"], ["other"]):
            with self.subTest(terms=terms):
                self.assert_error("invalid_input", lambda: population_query(
                    self.store, terms, cursor=first["next_cursor"]))

    def test_missing_abstracts_are_counted_separately_from_searchable_nonmatches(self):
        title_only = entry("2601.00001v1", abstract=None).replace(
            "<title>An authored example</title>", "<title>Gravity title only</title>")
        self.collect_entries([title_only, entry("2601.00002v1", abstract="Gravity model."),
                              entry("2601.00003v1", abstract="Unrelated model."),
                              entry("2601.00004v1", abstract=None)])
        page = population_query(self.store, ["gravity"])
        self.assertEqual((page["population"], page["searchable"], page["unavailable"]), (4, 2, 2))
        self.assertEqual((page["total_matches"], page["returned"], page["omitted"], page["remaining"]),
                         (1, 1, 0, 0))
        self.assertEqual([m["version_id"] for m in page["matches"]], ["arxiv:2601.00002v1"])
        self.assertFalse(page["bound_reached"])
        self.assertIsNone(page["next_cursor"])

    def test_empty_population_and_no_matches_report_zero_coverage(self):
        empty = population_query(self.store, ["gravity"])
        self.assertEqual((empty["population"], empty["searchable"], empty["unavailable"]), (0, 0, 0))
        self.cohort((1, 2))
        page = population_query(self.store, ["gravity"])
        self.assertEqual((page["population"], page["searchable"], page["unavailable"]), (2, 2, 0))
        for result in (empty, page):
            self.assertEqual((result["total_matches"], result["returned"], result["offset"],
                              result["omitted"], result["remaining"]), (0, 0, 0, 0, 0))
            self.assertEqual(result["matches"], [])
            self.assertFalse(result["bound_reached"])
            self.assertIsNone(result["next_cursor"])

    def test_a_full_terminal_page_reports_the_bound_without_continuation(self):
        self.cohort(range(1, 31))
        page = population_query(self.store, ["bounded"])
        self.assertEqual((page["returned"], page["omitted"], page["remaining"]), (30, 0, 0))
        self.assertTrue(page["bound_reached"])
        self.assertIsNone(page["next_cursor"])

    def test_malformed_cursors_produce_invalid_input(self):
        self.cohort((1, 2))
        for cursor in ("", "not a cursor", "[]", "null", "{}", 1, True, [], {}, "[" * 1100):
            with self.subTest(cursor=repr(cursor)[:80]):
                self.assert_error("invalid_input", lambda: population_query(self.store, ["bounded"], cursor=cursor))

    def test_cursor_format_ranking_and_offset_are_validated(self):
        self.cohort((1, 2, 3))
        first = population_query(self.store, ["bounded"], limit=1)
        cursor = json.loads(first["next_cursor"])
        self.assertEqual(cursor, {"version": 1, "revision": first["revision"],
                                  "query_digest": first["query_digest"],
                                  "ranking_version": first["ranking_version"], "offset": 1})
        for key, value in (("version", 2), ("version", True), ("ranking_version", "unknown"),
                           ("ranking_version", True), ("revision", True), ("revision", -1),
                           ("offset", -1), ("offset", 4), ("offset", True), ("offset", 1.5),
                           ("offset", "1"), ("query_digest", []), ("extra", 1)):
            with self.subTest(key=key, value=value):
                malformed = dict(cursor, **{key: value})
                self.assert_error("invalid_input", lambda: population_query(
                    self.store, ["bounded"], cursor=json.dumps(malformed)))

    def test_continuation_reverifies_saved_abstracts(self):
        self.cohort((1, 2))
        first = population_query(self.store, ["bounded"], limit=1)
        reference = self.store.snapshot()["records"]["work"]["arxiv:2601.00001v1"]["abstract"]
        path = self.root / reference["path"]
        path.chmod(0o600)
        path.write_bytes(b"Changed after the first page.")
        self.assert_error("artifact_corrupt", lambda: population_query(
            Store(self.root), ["bounded"], cursor=first["next_cursor"]))
