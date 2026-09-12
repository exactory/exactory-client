"""Authored metadata tests: versions, abstracts, references and pagination."""

import json
import unittest
from urllib.parse import parse_qs, urlsplit

from research_harness.errors import ResearchError
from research_harness.identities import normalize_identifier
from research_harness.providers import Arxiv, OpenAlex, Crossref
from research_fixtures import atom, entry, openalex, crossref


class ProviderTests(unittest.TestCase):
    def test_identifiers_keep_versions_and_do_not_guess_from_titles(self):
        for value, expected in [("https://arxiv.org/abs/physics/9801025v1", "arxiv:physics/9801025v1"),
                                ("arXiv:2601.00001v2", "arxiv:2601.00001v2"),
                                ("https://doi.org/10.1234/EXAMPLE", "doi:10.1234/example"),
                                ("https://openalex.org/W123", "openalex:W123")]:
            self.assertEqual(normalize_identifier(value), expected)
        for invalid in ("An ambiguous title", "2600.00001", "2601.00001v0", "10.1234/", "https://other.org/W123"):
            with self.assertRaises(ResearchError):
                normalize_identifier(invalid)

    def test_arxiv_original_short_abstract_category_and_dates(self):
        raw = atom([entry("physics/9801025v1", abstract="In memory of an experimental pioneer.",
                          category="physics.hist-ph", doi="10.1234/memorial")], total=1)
        work = Arxiv().parse(raw).works[0]
        self.assertEqual(work["id"], "arxiv:physics/9801025v1")
        self.assertEqual(work["work_id"], "arxiv:physics/9801025")
        self.assertEqual(work["abstract_text"], "In memory of an experimental pioneer.")
        self.assertEqual(work["category"], "physics.hist-ph")
        self.assertEqual(work["date_assertions"]["published"], "2026-01-03T12:00:00Z")
        self.assertEqual(work["date_assertions"]["updated"], "2026-02-01T10:00:00Z")
        self.assertEqual(work["aliases"], ["doi:10.1234/memorial"])

    def test_arxiv_malformed_entry_is_accounted_for(self):
        page = Arxiv().parse(atom([entry(category=None), "<entry><id>bad</id></entry>"], total=2))
        self.assertEqual(page.returned_count, 2)
        self.assertEqual(len(page.works), 1)
        self.assertEqual(page.works[0]["category"], None)
        self.assertEqual(len(page.failures), 2)

    def test_openalex_reconstructs_abstract_and_preserves_duplicate_references(self):
        work = OpenAlex().parse(json.dumps(openalex()).encode()).works[0]
        self.assertEqual(work["abstract_text"], "An original abstract.")
        self.assertEqual(work["abstract_representation"], "reconstructed_inverted_index")
        self.assertEqual(len(work["references"]), 2)
        self.assertEqual(work["references"][0]["target"], "openalex:W456")
        self.assertFalse(work["reference_metadata"]["bibliography_complete"])
        data = openalex()
        data["abstract_inverted_index"] = {"missing": [2]}
        work = OpenAlex().parse(json.dumps(data).encode()).works[0]
        self.assertIsNone(work["abstract_text"])
        self.assertEqual(work["abstract_status"], "invalid")

    def test_crossref_keeps_unresolved_nonpaper_references_and_date_precision(self):
        work = Crossref().parse(json.dumps(crossref()).encode()).works[0]
        self.assertEqual(work["publication_date"], "2026-01")
        self.assertEqual(work["date_assertions"]["published-online"], [[2025, 12, 30]])
        self.assertEqual(work["reference_metadata"]["reported_count"], 3)
        self.assertEqual(work["reference_metadata"]["status"], "partial")
        self.assertIsNone(work["references"][1]["target"])
        self.assertIn("laboratory notebook", work["references"][1]["raw"]["unstructured"])

    def test_registry_cursor_contract_and_secrets_in_headers_only(self):
        request = OpenAlex(api_key="secret").search_request({"search": "test", "filter": "is_oa:true"}, cursor="next", page_size=100)
        self.assertEqual(parse_qs(urlsplit(request.url).query)["cursor"], ["next"])
        self.assertNotIn("secret", request.url)
        self.assertEqual(request.headers["Authorization"], "Bearer secret")
        with self.assertRaises(ResearchError):
            OpenAlex().search_request({}, page_size=101)
        request = Crossref().search_request({"query": "test", "filter": "type:book"}, cursor="next", page_size=2)
        self.assertEqual(parse_qs(urlsplit(request.url).query)["filter"], ["type:book"])
        page = Crossref().parse(json.dumps({"message": {"items": [crossref()["message"]],
                  "total-results": 3, "next-cursor": "still-present"}}).encode(), page_size=2)
        self.assertTrue(page.complete)
        self.assertIsNone(page.next_cursor)

    def test_crossref_malformed_fields_cannot_become_valid_work_metadata(self):
        cases = [("title", "Authored title"), ("title", [42]), ("author", ["Ada"]),
                 ("published", None), ("link", [42]), ("reference-count", True)]
        for field, bad_value in cases:
            with self.subTest(field=field, value=bad_value):
                data = crossref()
                data["message"][field] = bad_value
                page = Crossref().parse(json.dumps(data).encode())
                self.assertEqual(page.works, [])
                self.assertEqual(len(page.failures), 1)
                self.assertEqual(page.failures[0]["code"], "invalid_work")

    def test_openalex_malformed_scalars_and_envelope_fail_with_typed_errors(self):
        for field, bad_value in [("title", 42), ("publication_date", "yesterday"),
                                 ("authorships", [None]), ("locations", "not an array")]:
            with self.subTest(field=field):
                data = openalex()
                data[field] = bad_value
                page = OpenAlex().parse(json.dumps(data).encode())
                self.assertEqual(page.works, [])
                self.assertEqual(page.failures[0]["code"], "invalid_work")
        for data in ({"meta": None, "results": []}, {"meta": {"count": -1}, "results": []}):
            with self.assertRaises(ResearchError) as error:
                OpenAlex().parse(json.dumps(data).encode())
            self.assertEqual(error.exception.code, "invalid_response")

    def test_inconsistent_reference_counts_remain_partial(self):
        data = crossref()
        data["message"]["reference-count"] = 0
        page = Crossref().parse(json.dumps(data).encode())
        self.assertEqual(len(page.works[0]["references"]), 2)
        self.assertEqual(page.works[0]["reference_metadata"]["status"], "partial")

    def test_nonfinite_duplicate_key_and_wrong_provider_identity_are_rejected(self):
        for raw in (b'{"id":"https://openalex.org/W123","id":"https://openalex.org/W456"}',
                    b'{"id":"https://openalex.org/W123","score":NaN}'):
            with self.assertRaises(ResearchError):
                OpenAlex().parse(raw)
        data = openalex()
        data["id"] = "https://doi.org/10.1234/foreign"
        page = OpenAlex().parse(json.dumps(data).encode())
        self.assertFalse(page.works)

    def test_invalid_arxiv_dates_and_non_doi_alias_do_not_certify_metadata(self):
        page = Arxiv().parse(atom([entry(published="yesterday")], total=1))
        self.assertFalse(page.works)
        self.assertTrue(page.failures)
        page = Arxiv().parse(atom([entry(doi="W123")], total=1))
        self.assertEqual(page.works[0]["aliases"], [])
        self.assertEqual(page.failures, [])
        self.assertEqual(page.warnings, [{"index": 0, "code": "invalid_alias", "id": "arxiv:2601.00001v1", "raw": "W123"}])

    def test_json_invalid_unicode_and_excessive_nesting_raise_typed_errors(self):
        for raw in (b'{"unused":"\\ud800"}', b'{"unused":' + b'[' * 1500 + b'0' + b']' * 1500 + b'}'):
            with self.assertRaises(ResearchError) as error:
                OpenAlex().parse(raw)
            self.assertEqual(error.exception.code, "invalid_response")

    def test_json_depth_is_bounded_in_unused_fields_independently_of_runtime(self):
        prefix = json.dumps(openalex()).encode()[:-1] + b',"unused":'
        for opening, closing in ((b'[', b']'), (b'{"child":', b'}')):
            with self.subTest(container=opening):
                accepted = prefix + opening * 127 + b'0' + closing * 127 + b'}'
                self.assertEqual(len(OpenAlex().parse(accepted).works), 1)
                rejected = prefix + opening * 128 + b'0' + closing * 128 + b'}'
                with self.assertRaises(ResearchError) as error:
                    OpenAlex().parse(rejected)
                self.assertEqual(error.exception.code, "invalid_response")
        quoted = prefix + json.dumps('[' * 1500 + '\\"' + ']' * 1500).encode() + b'}'
        self.assertEqual(len(OpenAlex().parse(quoted).works), 1)

    def test_arxiv_namespace_and_primary_category_shape_are_validated(self):
        raw = atom([entry()], total=1).replace(b"http://arxiv.org/abs/2601.00001v1", b"https://openalex.org/W123")
        page = Arxiv().parse(raw)
        self.assertFalse(page.works)
        self.assertTrue(page.failures)
        page = Arxiv().parse(atom([entry(category="bad space")], total=1))
        self.assertTrue(page.failures)
        self.assertIsNone(page.works[0]["category"])


if __name__ == "__main__":
    unittest.main()
