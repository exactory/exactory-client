"""OAI records bind current abstracts to an explicit latest version only."""
import tempfile
import unittest
from pathlib import Path

from research_harness.acquisition import import_response
from research_harness.artifacts import ArtifactStore
from research_harness.errors import ResearchError
from research_harness.providers import Arxiv
from research_harness.storage import Store
from research_fixtures import atom, entry


def record():
    return b'''<OAI-PMH xmlns="http://www.openarchives.org/OAI/2.0/">
<responseDate>2026-09-14T01:29:09Z</responseDate>
<request verb="GetRecord" metadataPrefix="arXivRaw" identifier="oai:arXiv.org:2608.28914">https://oaipmh.arxiv.org/oai</request>
<GetRecord><record><header><identifier>oai:arXiv.org:2608.28914</identifier><datestamp>2026-09-01</datestamp><setSpec>physics:math-ph</setSpec></header>
<metadata><arXivRaw xmlns="http://arxiv.org/OAI/arXivRaw/">
<id>2608.28914</id>
<version version="v1"><date>Fri, 28 Aug 2026 22:22:10 GMT</date><size>127kb</size></version>
<version version="v2"><date>Sun, 30 Aug 2026 10:00:00 GMT</date><size>128kb</size></version>
<title>A test title</title><authors>A. Author (Institute), B. Author</authors>
<categories>quant-ph math-ph math.MP</categories><doi>10.1234/test</doi>
<abstract>First line.\nSecond line with $x &lt; y$.</abstract>
</arXivRaw></metadata></record></GetRecord></OAI-PMH>'''


class ArxivOaiTests(unittest.TestCase):
    def test_latest_version_and_whole_original_abstract(self):
        page = Arxiv().parse(record())
        self.assertEqual(page.failures, [])
        self.assertEqual(page.returned_count, 1)
        self.assertIsNone(page.total)
        self.assertIsNone(page.start)
        self.assertFalse(page.complete)
        work, = page.works
        self.assertEqual(work["id"], "arxiv:2608.28914v2")
        self.assertEqual(work["abstract_text"], "First line.\nSecond line with $x < y$.")
        self.assertEqual(work["abstract_status"], "available")
        self.assertEqual(work["publication_date"], "2026-08-28T22:22:10Z")
        self.assertEqual(work["date_assertions"]["updated"], "2026-08-30T10:00:00Z")
        self.assertEqual(work["date_assertions"]["oai_datestamp"], "2026-09-01")
        self.assertEqual(work["date_assertions"]["version_history"][0]["date"], "Fri, 28 Aug 2026 22:22:10 GMT")
        self.assertEqual(work["authors"], [])
        self.assertEqual(work["authors_raw"], "A. Author (Institute), B. Author")
        self.assertIsNone(work["category"])
        self.assertEqual(work["categories"], ["quant-ph", "math-ph", "math.MP"])
        self.assertEqual(work["aliases"], ["doi:10.1234/test"])
        self.assertEqual(work["fulltext_urls"][0], "https://arxiv.org/pdf/2608.28914v2")

    def test_protocol_errors_and_other_operations_are_rejected(self):
        cases = [record().replace(b'GetRecord', b'ListRecords'),
                 record().replace(b'metadataPrefix="arXivRaw"', b'metadataPrefix="arXiv"'),
                 record().replace(b'<GetRecord>', b'<error code="idDoesNotExist">Missing</error><GetRecord>'),
                 record().replace(b'<header>', b'<header status="deleted">'),
                 record().replace(b'http://arxiv.org/OAI/arXivRaw/', b'http://example.org/foreign/')]
        for raw in cases:
            with self.subTest(raw=raw):
                with self.assertRaises(ResearchError):
                    Arxiv().parse(raw)

    def test_getrecord_requires_its_requested_identifier(self):
        raw = record().replace(b' identifier="oai:arXiv.org:2608.28914"', b'')
        with self.assertRaises(ResearchError):
            Arxiv().parse(raw)

    def test_conflicting_identifiers_and_duplicate_records_are_rejected(self):
        cases = [record().replace(b'identifier="oai:arXiv.org:2608.28914"', b'identifier="oai:arXiv.org:2608.00001"'),
                 record().replace(b'<id>2608.28914</id>', b'<id>2608.00001</id>'),
                 record().replace(b'<id>2608.28914</id>', b'<id>2608.28914</id><id>2608.00001</id>'),
                 record().replace(b'</GetRecord>', b'<record/></GetRecord>')]
        for raw in cases:
            with self.subTest(raw=raw):
                with self.assertRaises(ResearchError):
                    Arxiv().parse(raw)

    def test_foreign_provider_identifier_cannot_become_an_arxiv_record(self):
        raw = record().replace(b'<id>2608.28914</id>', b'<id>10.1234/test</id>')
        raw = raw.replace(b'oai:arXiv.org:2608.28914', b'oai:arXiv.org:.1234/test')
        with self.assertRaises(ResearchError):
            Arxiv().parse(raw)

    def test_misnamespaced_latest_version_is_not_silently_omitted(self):
        raw = record().replace(b'<version version="v2">', b'<version xmlns="http://example.org/foreign/" version="v2">')
        with self.assertRaises(ResearchError):
            Arxiv().parse(raw)

    def test_invalid_history_cannot_certify_exact_version(self):
        cases = [record().replace(b'version="v2"', b'version="v3"'),
                 record().replace(b'version="v2"', b'version="v1"'),
                 record().replace(b'version="v1"', b'version="v0"'),
                 record().replace(b'Sun, 30 Aug 2026 10:00:00 GMT', b'unknown'),
                 record().replace(b'<title>A test title</title>', b'<title/>')]
        for raw in cases:
            with self.subTest(raw=raw):
                with self.assertRaises(ResearchError):
                    Arxiv().parse(raw)

    def test_version_numbers_determine_current_even_when_original_dates_run_backwards(self):
        raw = record().replace(b'Sun, 30 Aug 2026 10:00:00 GMT', b'Thu, 27 Aug 2026 10:00:00 GMT')
        work = Arxiv().parse(raw).works[0]
        self.assertEqual(work["id"], "arxiv:2608.28914v2")
        self.assertEqual(work["publication_date"], "2026-08-28T22:22:10Z")
        self.assertEqual(work["date_assertions"]["updated"], "2026-08-27T10:00:00Z")
        self.assertEqual(work["date_assertions"]["diagnostics"][0]["code"], "nonmonotone_version_dates")

    def test_future_earlier_version_is_rejected_even_if_latest_version_date_is_past(self):
        raw = record().replace(b'Fri, 28 Aug 2026 22:22:10 GMT', b'Tue, 15 Sep 2026 22:22:10 GMT')
        with self.assertRaises(ResearchError):
            Arxiv().parse(raw)

    def test_missing_abstract_and_bad_alias_remain_explicit(self):
        raw = record().replace(b'<abstract>First line.\nSecond line with $x &lt; y$.</abstract>', b'')
        raw = raw.replace(b'10.1234/test', b'not a DOI')
        page = Arxiv().parse(raw)
        self.assertIsNone(page.works[0]["abstract_text"])
        self.assertEqual(page.works[0]["abstract_status"], "missing")
        self.assertEqual(page.works[0]["aliases"], [])
        self.assertTrue(any(w["code"] == "invalid_alias" for w in page.warnings))

    def test_dtd_and_entity_declarations_are_rejected(self):
        raw = b'<!DOCTYPE OAI-PMH [<!ENTITY injected "false content">]>' + record()
        for encoded in (raw, raw.decode().encode("utf-16")):
            with self.subTest(encoding=encoded[:4]):
                with self.assertRaises(ResearchError):
                    Arxiv().parse(encoded)

    def test_empty_abstract_is_missing(self):
        for empty in (b'<abstract/>', b'<abstract> \n </abstract>'):
            raw = record().replace(b'<abstract>First line.\nSecond line with $x &lt; y$.</abstract>', empty)
            work = Arxiv().parse(raw).works[0]
            self.assertIsNone(work["abstract_text"])
            self.assertEqual(work["abstract_status"], "missing")

    def test_empty_optional_doi_does_not_reject_the_article(self):
        for empty in (b'<doi/>', b'<doi> \n </doi>'):
            raw = record().replace(b'<doi>10.1234/test</doi>', empty)
            page = Arxiv().parse(raw)
            self.assertEqual(page.works[0]["aliases"], [])
            self.assertEqual(page.works[0]["abstract_status"], "available")

    def test_import_preserves_sources_authors_and_older_version(self):
        with tempfile.TemporaryDirectory() as directory:
            store = Store(Path(directory), create=True)
            for version in ("v1", "v2"):
                import_response(store, "arxiv", atom([entry("2608.28914" + version)], total=1),
                    source_url="https://export.arxiv.org/api/query?id_list=2608.28914" + version,
                    captured_at="2026-09-14T01:29:10Z", request_id="atom-" + version,
                    expected_revision=store.revision)
            before = store.snapshot()["records"]["work"]["arxiv:2608.28914v1"]
            result = import_response(store, "arxiv", record(),
                source_url="https://oaipmh.arxiv.org/oai?verb=GetRecord&metadataPrefix=arXivRaw&identifier=oai%3AarXiv.org%3A2608.28914",
                captured_at="2026-09-14T01:29:10Z", request_id="oai", expected_revision=store.revision,
                media_type="text/xml")
            self.assertEqual(result["status"], "complete")
            records = store.snapshot()["records"]
            self.assertEqual(records["work"]["arxiv:2608.28914v1"], before)
            latest = records["work"]["arxiv:2608.28914v2"]
            self.assertEqual(latest["authors"], ["A. Researcher"])
            source = records["source"][result["source_ids"][0]]
            self.assertFalse(source["origin_verified"])
            self.assertIsNone(source["http_status"])
            artifacts = ArtifactStore(Path(directory))
            self.assertEqual(artifacts.read(source["response"]), record())
            abstract, = [a for a in latest["abstracts"] if a["source_id"] == source["id"]]
            self.assertEqual(abstract["completeness"], "complete")
            self.assertEqual(abstract["version"], "v2")
            self.assertEqual(artifacts.read(abstract["artifact"]), b"First line.\nSecond line with $x < y$.")
            self.assertNotIn("reading", records)
            self.assertNotIn("collection", records)


if __name__ == "__main__":
    unittest.main()
