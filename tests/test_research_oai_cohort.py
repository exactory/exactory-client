"""Original OAI traversals supply enumeration evidence, never reading credit."""

import copy
import importlib
import json
import tempfile
import unittest
from datetime import datetime
from email.utils import format_datetime
from pathlib import Path
from urllib.parse import urlencode
from xml.sax.saxutils import escape, quoteattr

from research_harness.errors import ResearchError

DEFINITION = {"corpus": "arxiv", "primaryCategory": "math-ph",
              "windowStart": "2026-03-01", "windowEnd": "2026-08-31"}
ENDPOINT = "https://oaipmh.arxiv.org/oai"


def article(identifier="2603.00001", categories="math-ph math.MP", submitted="2026-03-01T00:00:00Z",
            abstract="An original abstract.", versions=()):
    dates = [submitted, *versions]
    history = "".join('<version version="v%d"><date>%s</date><size>1kb</size></version>' %
                      (i, format_datetime(datetime.fromisoformat(d.replace("Z", "+00:00")), usegmt=True))
                      for i, d in enumerate(dates, 1))
    abstract_xml = "" if abstract is None else "<abstract>" + escape(abstract) + "</abstract>"
    return ('<record><header><identifier>oai:arXiv.org:%s</identifier><datestamp>2026-09-01</datestamp>'
            '<setSpec>physics:math-ph</setSpec></header><metadata><arXivRaw xmlns="http://arxiv.org/OAI/arXivRaw/">'
            '<id>%s</id>%s<title>Original title</title><authors>A. Author (Institute), B. Author</authors>'
            '<categories>%s</categories>%s</arXivRaw></metadata></record>') % (identifier, identifier, history, categories, abstract_xml)


def page(records, previous=None, token=None, at="2026-09-14T02:00:00Z", xml_base="http://oaipmh.arxiv.org/oai"):
    args = {"verb": "ListRecords", "metadataPrefix": "arXivRaw", "set": "physics:math-ph"} if previous is None else {
        "verb": "ListRecords", "resumptionToken": previous}
    attrs = " ".join(k + "=" + quoteattr(v) for k, v in args.items())
    token_xml = "" if token is None else '<resumptionToken expirationDate="2026-09-14T23:59:59Z">' + escape(token) + '</resumptionToken>'
    body = ('<OAI-PMH xmlns="http://www.openarchives.org/OAI/2.0/"><responseDate>' + at + '</responseDate>'
            '<request ' + attrs + '>' + xml_base + '</request><ListRecords>' + ''.join(records) + token_xml + '</ListRecords></OAI-PMH>')
    return {"response": body.encode(), "source_url": ENDPOINT + "?" + urlencode(args), "captured_at": at}


class ChainTests(unittest.TestCase):
    def validate(self, pages, definition=None):
        spec = importlib.util.find_spec("research_harness.oai_cohort")
        self.assertIsNotNone(spec, "The native original-chain validator must exist")
        return importlib.import_module("research_harness.oai_cohort").validate_chain(pages, definition or DEFINITION)

    def test_complete_single_page_uses_v1_dates_latest_version_and_ordered_categories(self):
        raw = page([article(versions=("2026-09-01T00:00:00Z",)),
                    article("2608.00002", "quant-ph math-ph math.MP", "2026-08-31T23:59:59Z"),
                    article("2602.00003", submitted="2026-02-28T23:59:59Z")])
        result = self.validate([raw])
        self.assertEqual(result["returned_count"], 3)
        self.assertEqual(result["projected_count"], 2)
        self.assertEqual(result["member_count"], 1)
        works = result["pages"][0]["works"]
        self.assertEqual([w["id"] for w in works], ["arxiv:2603.00001v2", "arxiv:2608.00002v1"])
        self.assertEqual(works[0]["publication_date"], "2026-03-01T00:00:00Z")
        self.assertEqual(works[0]["categories"], ["math-ph", "math.MP"])
        self.assertEqual(works[0]["category"], "math-ph")
        self.assertEqual(works[0]["authors"], [])
        self.assertEqual(works[0]["authors_raw"], "A. Author (Institute), B. Author")
        self.assertEqual(works[0]["source_locator"]["record_index"], 0)
        self.assertEqual(result["ledger"][-1]["disposition"], "out_of_scope_date")
        self.assertEqual(result["pages"][0]["response"], raw["response"])

    def test_opaque_token_chain_preserves_expiry_without_rechecking_import_time(self):
        token = "verb%3DListRecords%26from%3D1992-01-01%26skip%3D1"
        result = self.validate([page([article()], token=token),
                                page([article("2603.00002")], previous=token, token="", at="2026-09-14T02:01:00Z")])
        self.assertEqual(result["returned_count"], 2)
        self.assertEqual(result["pages"][0]["token_attributes"], {"expirationDate": "2026-09-14T23:59:59Z"})

    def test_original_nonmonotone_dates_are_retained_for_excluded_records_and_counted(self):
        supplied = page([article("math-ph/0302016", submitted="2003-02-07T16:15:27Z", versions=("2002-12-01T00:00:00Z",))])
        result = self.validate([supplied])
        self.assertEqual(result["projected_count"], 0)
        self.assertEqual(result["date_anomaly_count"], 1)
        diagnostic = result["ledger"][0]["date_diagnostics"][0]
        self.assertEqual(diagnostic, {"code": "nonmonotone_version_dates", "earlier_version": "v1",
            "earlier_date": "Fri, 07 Feb 2003 16:15:27 GMT", "later_version": "v2", "later_date": "Sun, 01 Dec 2002 00:00:00 GMT",
            "earlier_timestamp": "2003-02-07T16:15:27Z", "later_timestamp": "2002-12-01T00:00:00Z"})
        self.assertEqual(len(result["ledger"][0]["version_history"]), 2)
        self.assertEqual(result["ledger"][0]["chronology_status"], "nonmonotone")
        self.assertEqual(result["ledger"][0]["source_url"], supplied["source_url"])
        self.assertEqual(len(result["ledger"][0]["response_sha256"]), 64)
        self.assertEqual(result["ledger"][0]["publication_date"], "2003-02-07T16:15:27Z")

    def test_multiple_and_equal_date_pairs_use_version_numbers_and_exact_v1_window(self):
        records = [article(versions=("2026-02-28T23:59:59Z", "2026-02-28T23:59:59Z", "2026-02-27T12:00:00Z", "2026-09-01T00:00:00Z")),
                   article("2602.00002", submitted="2026-02-28T23:59:59Z", versions=("2026-03-01T00:00:00Z",))]
        result = self.validate([page(records)])
        self.assertEqual(result["projected_count"], 1)
        work = result["pages"][0]["works"][0]
        self.assertEqual(work["id"], "arxiv:2603.00001v5")
        self.assertEqual(len(work["date_assertions"]["diagnostics"]), 2)
        self.assertEqual(result["date_anomaly_count"], 1)
        self.assertEqual(result["ledger"][1]["disposition"], "out_of_scope_date")
        with self.assertRaises(ResearchError):
            self.validate([page([article(versions=("2026-09-15T00:00:00Z", "2026-04-01T00:00:00Z"))])])

    def test_missing_abstract_is_preserved_but_malformed_abstract_is_rejected_even_outside_window(self):
        result = self.validate([page([article(abstract=None)])])
        self.assertEqual(result["pages"][0]["works"][0]["abstract_status"], "missing")
        for bad in [article(abstract=" "), article().replace("An original abstract.", "<b>nested</b>")]:
            with self.subTest(bad=bad), self.assertRaises(ResearchError):
                self.validate([page([bad.replace("2603.00001", "2602.00001").replace("01 Mar 2026", "01 Feb 2026")])])

    def test_bad_chains_never_receive_enumeration_credit(self):
        first, second = page([article()], token="next%26x"), page([article("2603.00002")], previous="next%26x", token="")
        cases = [[first], [second, first], [first, first, second], [first, page([article()], previous="next%26x", token="")],
                 [first, page([article("2603.00002")], previous="wrong", token="")],
                 [first, page([article("2603.00002")], previous="next%26x", token="next%26x")],
                 [page([article()]), second], [first, page([article("2603.00002")], previous="next%26x", token="", at="2026-09-15T00:00:00Z")]]
        for pages in cases:
            with self.subTest(pages=pages), self.assertRaises(ResearchError):
                self.validate(pages)

    def test_exact_official_endpoint_and_parameter_schema(self):
        for base in ["http://oaipmh.arxiv.org/oai", "https://oaipmh.arxiv.org/oai"]:
            self.assertEqual(self.validate([page([article()], xml_base=base)])["returned_count"], 1)
        for url in ["http://oaipmh.arxiv.org/oai", "https://oaipmh.arxiv.org:443/oai", "https://x@oaipmh.arxiv.org/oai",
                    "https://oaipmh.arxiv.org.evil/oai", "https://oaipmh.arxiv.org/oai/", "https://oaipmh.arxiv.org/oai#fragment"]:
            bad = page([article()]); bad["source_url"] = bad["source_url"].replace(ENDPOINT, url)
            with self.subTest(url=url), self.assertRaises(ResearchError):
                self.validate([bad])
        for suffix in ["&from=2026-03-01", "&set=physics%3Amath-ph", "&x=", "&verb=", "&x=%0A"]:
            bad = page([article()]); bad["source_url"] += suffix
            with self.subTest(suffix=suffix), self.assertRaises(ResearchError):
                self.validate([bad])
        with self.assertRaises(ResearchError):
            self.validate([page([article()], xml_base="https://arxiv.org/oai")])
        with self.assertRaises(ResearchError):
            self.validate([page([article()])], dict(DEFINITION, primaryCategory="math.MP"))

    def test_url_whitespace_controls_and_container_text_are_not_silently_normalized(self):
        supplied = page([article()])
        for url in (" " + supplied["source_url"], supplied["source_url"] + " ", "https://[invalid/oai?verb=ListRecords"):
            with self.subTest(url=url), self.assertRaises(ResearchError):
                self.validate([dict(supplied, source_url=url)])
        for marker in (b'<ListRecords>', b'<record>', b'<metadata>'):
            with self.subTest(marker=marker), self.assertRaises(ResearchError):
                self.validate([dict(supplied, response=supplied["response"].replace(marker, marker + b'Unexpected text'))])
        for token in (" ", "token\u0085"):
            with self.subTest(token=token), self.assertRaises(ResearchError):
                self.validate([page([article()], token=token), page([article("2603.00002")], previous=token, token="")])

    def test_every_record_and_envelope_is_validated_before_date_filtering(self):
        original = page([article(submitted="2026-02-01T00:00:00Z")])
        replacements = [(b'<header>', b'<header status="deleted">'),
                        (b'<id>2603.00001</id>', b'<id>2603.00002</id>'),
                        (b'version="v1"', b'version="v2"'), (b'01 Feb 2026', b'31 Feb 2026'),
                        (b'<categories>math-ph math.MP</categories>', b''),
                        (b'<title>Original title</title>', b'<title>One</title><title>Two</title>'),
                        (b'<version version=', b'<version xmlns="urn:foreign" version='),
                        (b'</ListRecords>', b'<resumptionToken/><resumptionToken/></ListRecords>'),
                        (b'<ListRecords>', b'<error code="badArgument">Bad</error><ListRecords>'),
                        (b'</OAI-PMH>', b'<ListRecords/></OAI-PMH>')]
        for before, after in replacements:
            bad = dict(original, response=original["response"].replace(before, after))
            with self.subTest(before=before), self.assertRaises(ResearchError):
                self.validate([bad])
        dtd = '<!DOCTYPE OAI-PMH [<!ENTITY x "x">]>' + original["response"].decode()
        for raw in [dtd.encode(), dtd.encode("utf-16")]:
            with self.assertRaises(ResearchError):
                self.validate([dict(original, response=raw)])


class ImportTests(unittest.TestCase):
    def setUp(self):
        from research_harness.storage import Store
        from research_harness.acquisition import collect_cohort
        from research_fixtures import client
        # Keep disposable Stores inside the checkout without private SDD state.
        self.directory = tempfile.TemporaryDirectory(prefix="oai-cohort-test-", dir=Path(__file__).resolve().parents[1])
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.store = Store(self.root, create=True)
        http, _, _ = client([(503, {"Content-Type": "text/plain"}, b"Original failed Atom response")], max_retries=0)
        result = collect_cohort(self.store, DEFINITION, request_id="failed-atom", expected_revision=0, http=http)
        self.collection_id = result["collection_id"]
        self.counter = 0

    def inputs(self, pages):
        result = []
        for i, supplied in enumerate(pages):
            name = "original-%d-%d.xml" % (self.counter, i)
            (self.root / name).write_bytes(supplied["response"])
            result.append({"response_file": name, "source_url": supplied["source_url"], "captured_at": supplied["captured_at"]})
        self.counter += 1
        return result

    def run_import(self, inputs=None, request_id="oai-import", revision=None):
        from research_harness import acquisition
        self.assertTrue(hasattr(acquisition, "import_oai_cohort"), "Acquisition must expose the atomic OAI cohort import")
        if inputs is None:
            inputs = self.inputs([page([article()])])
        return acquisition.import_oai_cohort(self.store, self.collection_id, inputs, request_id=request_id,
            expected_revision=self.store.revision if revision is None else revision)

    def test_cli_import_dispatches_exact_payload_and_rejects_authored_claims(self):
        import subprocess
        import sys
        binary = Path(__file__).resolve().parents[1] / "bin" / "exactory-research"
        payload = {"collection_id": self.collection_id, "pages": self.inputs([page([article()])])}
        path = self.root / "payload.json"
        path.write_text(json.dumps(payload))
        result = subprocess.run([sys.executable, str(binary), "import-oai-cohort", "--file", str(path),
                                 "--expected-revision", str(self.store.revision), "--request-id", "cli-import"],
                                 cwd=self.root, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)["member_count"], 1)
        from research_harness.cli import acquisition_command
        for value in (dict(payload, complete=True), dict(payload, total=1), dict(payload, works=[])):
            with self.assertRaises(ResearchError):
                acquisition_command(self.store, "import-oai-cohort", value,
                                    {"request_id": "invalid", "expected_revision": self.store.revision})
        example = subprocess.run([sys.executable, str(binary), "example", "import-oai-cohort"], cwd=self.root,
                                 capture_output=True, text=True)
        self.assertEqual(example.returncode, 0, example.stderr)
        self.assertEqual(set(json.loads(example.stdout)), {"collection_id", "pages"})

    def test_cli_cannot_create_a_store_for_a_nonexistent_collection(self):
        import subprocess
        import sys
        binary = Path(__file__).resolve().parents[1] / "bin" / "exactory-research"
        with tempfile.TemporaryDirectory(dir=self.root.parent) as directory:
            fresh = Path(directory)
            payload = fresh / "payload.json"
            payload.write_text(json.dumps({"collection_id": "missing", "pages": []}))
            result = subprocess.run([sys.executable, str(binary), "import-oai-cohort", "--file", str(payload),
                                     "--expected-revision", "0", "--request-id", "missing"], cwd=fresh,
                                     capture_output=True, text=True)
            self.assertNotEqual(result.returncode, 0)
            self.assertFalse((fresh / ".exactory/research.sqlite3").exists())

    def test_nonmonotone_original_dates_do_not_establish_historical_priority(self):
        from research_harness.acquisition import import_response
        from research_harness.literature import _historical_status
        from test_research_arxiv_oai import record
        raw = record().replace(b'2608.28914', b'math-ph/0302016').replace(
            b'Fri, 28 Aug 2026 22:22:10 GMT', b'Fri, 07 Feb 2003 16:15:27 GMT').replace(
            b'Sun, 30 Aug 2026 10:00:00 GMT', b'Sun, 01 Dec 2002 00:00:00 GMT')
        import_response(self.store, "arxiv", raw, source_url=ENDPOINT + "?verb=GetRecord",
                        captured_at="2026-09-14T02:00:00Z", request_id="inversion", expected_revision=self.store.revision)
        work = self.store.snapshot()["records"]["work"]["arxiv:math-ph/0302016v2"]
        self.assertEqual(_historical_status(work, "2002-12-31"), "unknown")
        self.assertEqual(_historical_status(work, None), "not_requested")
        independent = copy.deepcopy(work)
        independent["date_assertions"].append({"source_id": "independent-fixture", "values": {"updated": "2002-12-20T00:00:00Z"}})
        self.assertEqual(_historical_status(independent, "2002-12-31"), "known_before")
        independent["date_assertions"][-1]["values"]["updated"] = "2003-03-01T00:00:00Z"
        self.assertEqual(_historical_status(independent, "2002-12-31"), "later_capture")

    def test_original_sources_and_failed_history_survive_one_atomic_new_epoch(self):
        from research_harness.acquisition import collection_status
        from research_harness.artifacts import ArtifactStore
        before = self.store.snapshot()
        original = page([article(), article("2602.00002", submitted="2026-02-01T00:00:00Z")])
        result = self.run_import(self.inputs([original]))
        after = self.store.snapshot(); records = after["records"]
        self.assertEqual(after["revision"], before["revision"] + 1)
        for kind in ("source", "collection_page", "acquisition_operation"):
            for key, value in before["records"][kind].items():
                self.assertEqual(records[kind][key], value)
        status = collection_status(self.store, self.collection_id)
        self.assertEqual(status["status"], "complete")
        self.assertEqual(status["definition"], DEFINITION)
        self.assertEqual(status["source_count"], 2)
        self.assertEqual(status["returned_count"], 2)
        self.assertEqual(status["member_count"], 1)
        source = records["source"][result["source_ids"][0]]
        self.assertEqual(source["capture_method"], "external_import")
        self.assertFalse(source["origin_verified"])
        self.assertIsNone(source["http_status"])
        self.assertEqual(source["headers"], {})
        self.assertEqual(ArtifactStore(self.root).read(source["response"]), original["response"])
        self.assertEqual(result["attempts_used"], 0)
        self.assertEqual(result["external_response_count"], 1)
        self.assertEqual(result["imported_bytes"], len(original["response"]))
        epoch = records["cohort_enumeration"][result["enumeration_id"]]
        self.assertEqual(len(epoch["runtime"]["package_digest"]), 64)
        ledger = json.loads(ArtifactStore(self.root).read(epoch["projection"]))
        self.assertEqual([r["disposition"] for r in ledger], ["member", "out_of_scope_date"])
        self.assertEqual(epoch["counts"], {"returned": 2, "projected": 1, "members": 1, "out_of_window": 1})
        history = json.loads(ArtifactStore(self.root).read(epoch["history"]))
        self.assertEqual(history, before["records"]["collection"][self.collection_id])
        partition = records["collection"][self.collection_id]["partitions"][0]
        self.assertEqual(partition["total_basis"], "local_submission_date_projection")
        self.assertEqual(partition["total"], 1)
        self.assertIsNone(partition["provider_reported_total"])

    def test_import_grants_no_reading_or_readiness_and_missing_abstract_stays_pending(self):
        from research_harness.cohort_evidence import cohort_reading_report
        from research_harness.acquisition import collection_status
        self.run_import(self.inputs([page([article(abstract=None)])]))
        records = self.store.snapshot()["records"]
        for kind in ("reading", "screening", "standards", "admission", "readiness"):
            self.assertNotIn(kind, records)
        status = collection_status(self.store, self.collection_id)
        self.assertIn("missing_abstract", [p["code"] for p in status["pending"]])
        report = cohort_reading_report(self.store, [self.collection_id])
        self.assertFalse(report["ready"])
        self.assertEqual(report["counts"]["abstracts_read"], 0)

    def test_same_version_conflicts_keep_old_preferred_abstract_and_new_category_assertion(self):
        from research_harness.acquisition import import_response, collection_status
        from research_harness.artifacts import ArtifactStore
        from research_fixtures import atom, entry
        import_response(self.store, "arxiv", atom([entry("2603.00001v1", category="quant-ph", abstract="Earlier abstract.")], total=1),
                        source_url="https://export.arxiv.org/api/query?id_list=2603.00001v1", captured_at="2026-09-14T01:00:00Z",
                        request_id="earlier", expected_revision=self.store.revision)
        result = self.run_import()
        records = self.store.snapshot()["records"]
        work = records["work"]["arxiv:2603.00001v1"]
        self.assertEqual(work["category"], "quant-ph")
        self.assertEqual(ArtifactStore(self.root).read(work["abstract"]), b"Earlier abstract.")
        self.assertEqual(collection_status(self.store, self.collection_id)["member_count"], 1)
        assertion = next(a for a in records["work_assertion"].values() if a["source_id"] in result["source_ids"])
        self.assertEqual(assertion["category"], "math-ph")
        self.assertEqual(ArtifactStore(self.root).read(assertion["abstract"]), b"An original abstract.")

    def test_prior_seen_members_exclusions_and_versionless_families_remain_population_obligations(self):
        from research_harness.acquisition import collection_status
        for index, kind in enumerate(("cohort_member", "cohort_exclusion", "cohort_seen")):
            family = "arxiv:2601.0000" + str(index + 1)
            record = {"collection_id": self.collection_id, "work_id": family, "version_ids": [family], "source_ids": []}
            def put(tx, kind=kind, record=record, index=index):
                tx.put(kind, "old-" + str(index), record)
                tx.put("cohort_seen", "seen-" + str(index), record)
            self.store.mutate("fixture.history", {"index": index}, put, expected_revision=self.store.revision, request_id="history-" + str(index))
        self.run_import()
        status = collection_status(self.store, self.collection_id)
        lost = next(p for p in status["pending"] if p["code"] == "population_changed")
        self.assertEqual(lost["retained_work_ids"], ["arxiv:2601.00001", "arxiv:2601.00002", "arxiv:2601.00003"])

    def test_replay_uses_exact_bytes_urls_order_and_capture_times_before_current_revision(self):
        inputs = self.inputs([page([article()])]); revision = self.store.revision
        result = self.run_import(inputs, revision=revision)
        self.store.mutate("fixture.later", {}, lambda tx: tx.put("fixture", "later", {}), expected_revision=self.store.revision, request_id="later")
        snapshot = self.store.snapshot()
        self.assertEqual(self.run_import(inputs, revision=revision), result)
        self.assertEqual(self.store.snapshot(), snapshot)
        for field, value in (("captured_at", "2026-09-14T02:01:00Z"), ("source_url", inputs[0]["source_url"] + "&x=bad")):
            bad = copy.deepcopy(inputs); bad[0][field] = value
            with self.subTest(field=field), self.assertRaises(ResearchError) as error:
                self.run_import(bad, revision=revision)
            self.assertEqual(error.exception.code, "request_id_conflict")
        (self.root / inputs[0]["response_file"]).write_bytes(b"changed original")
        with self.assertRaises(ResearchError) as error:
            self.run_import(inputs, revision=revision)
        self.assertEqual(error.exception.code, "request_id_conflict")
        self.assertEqual(self.store.snapshot(), snapshot)

    def test_invalid_input_stale_revision_and_active_operation_never_publish_state(self):
        bad = self.inputs([page([article()], token="unfinished")])
        before = self.store.snapshot()
        with self.assertRaises(ResearchError):
            self.run_import(bad)
        self.assertEqual(self.store.snapshot(), before)
        with self.assertRaises(ResearchError) as error:
            self.run_import(revision=0)
        self.assertEqual(error.exception.code, "stale_revision")
        def active(tx):
            tx.put("acquisition_operation", "busy", {"operation": "cohort.resume", "state": "admitted", "target": self.collection_id, "payload": {"max_requests": 1}})
        self.store.mutate("fixture.active", {}, active, expected_revision=self.store.revision, request_id="active")
        before = self.store.snapshot()
        with self.assertRaises(ResearchError) as error:
            self.run_import()
        self.assertEqual(error.exception.code, "operation_conflict")
        self.assertEqual(self.store.snapshot(), before)

    def test_checked_workspace_boundary_rejects_traversal_and_symlinks(self):
        inputs = self.inputs([page([article()])])
        (self.root / "link.xml").symlink_to(self.root / inputs[0]["response_file"])
        for path in ("../outside.xml", str(self.root / inputs[0]["response_file"]), "link.xml"):
            bad = copy.deepcopy(inputs); bad[0]["response_file"] = path
            with self.subTest(path=path), self.assertRaises(ResearchError):
                self.run_import(bad)

    def test_exact_byte_budget_allows_import_with_no_network_budget_and_replay_no_charge(self):
        from research_harness.principles import initialize_research
        from research_harness.resources import set_budget, UNITS
        initialize_research(self.store, {"profile": "research", "target": None}, expected_revision=self.store.revision, request_id="init")
        raw = page([article()]); inputs = self.inputs([raw])
        amounts = {unit: None for unit in UNITS}; amounts.update(network_requests=0, source_bytes=len(raw["response"]) - 1)
        set_budget(self.store, {"profile": "research", "purpose": "literature", "limits": amounts, "reason": "Fixture exact bound."},
                   expected_revision=self.store.revision, request_id="budget-small")
        before = self.store.snapshot()
        with self.assertRaises(ResearchError) as error:
            self.run_import(inputs)
        self.assertEqual(error.exception.code, "resource_budget_exhausted")
        self.assertEqual(self.store.snapshot(), before)
        amounts["source_bytes"] += 1
        set_budget(self.store, {"profile": "research", "purpose": "literature", "limits": amounts, "reason": "Fixture exact bound."},
                   expected_revision=self.store.revision, request_id="budget-exact")
        revision = self.store.revision
        result = self.run_import(inputs)
        account = self.store.snapshot()["records"]["resource_account"]["research:literature"]
        self.assertEqual(account["charged"]["network_requests"], 0)
        self.assertEqual(account["charged"]["source_bytes"], len(raw["response"]))
        self.assertEqual(self.run_import(inputs, revision=revision), result)
        self.assertEqual(self.store.snapshot()["records"]["resource_account"]["research:literature"], account)

    def test_intermediate_artifact_failure_and_concurrent_revision_leave_no_partial_import(self):
        from unittest.mock import patch
        from research_harness.artifacts import ArtifactStore
        inputs = self.inputs([page([article()], token="next"), page([article("2603.00002")], previous="next", token="")])
        original = ArtifactStore.put
        calls = []
        def failing(artifacts, data, media):
            calls.append(data)
            if len(calls) == 2:
                raise OSError("Fixture disk failure")
            return original(artifacts, data, media)
        before = self.store.snapshot()
        with patch.object(ArtifactStore, "put", failing), self.assertRaises(OSError):
            self.run_import(inputs)
        self.assertEqual(self.store.snapshot(), before)
        changed = []
        def racing(artifacts, data, media):
            if not changed:
                changed.append(True)
                self.store.mutate("fixture.concurrent", {}, lambda tx: tx.put("fixture", "concurrent", {}),
                                  expected_revision=self.store.revision, request_id="concurrent")
            return original(artifacts, data, media)
        with patch.object(ArtifactStore, "put", racing), self.assertRaises(ResearchError) as error:
            self.run_import(inputs)
        self.assertEqual(error.exception.code, "stale_revision")
        after = self.store.snapshot()
        self.assertEqual(after["revision"], before["revision"] + 1)
        self.assertEqual(after["records"]["collection"], before["records"]["collection"])
        self.assertNotIn("cohort_enumeration", after["records"])

    def test_every_chain_artifact_is_status_and_preparation_dependency_even_without_members(self):
        from research_harness.acquisition import collection_status
        from research_harness.cohort_evidence import cohort_reading_report
        originals = [page([article("2602.00001", submitted="2026-02-01T00:00:00Z")], token="one"),
                     page([article("2602.00002", submitted="2026-02-01T00:00:00Z")], previous="one", token="two"),
                     page([article("2602.00003", submitted="2026-02-01T00:00:00Z")], previous="two", token="")]
        result = self.run_import(self.inputs(originals)); records = self.store.snapshot()["records"]
        self.assertEqual(collection_status(self.store, self.collection_id)["member_count"], 0)
        epoch = records["cohort_enumeration"][result["enumeration_id"]]
        refs = [records["source"][sid]["response"] for sid in result["source_ids"]] + [epoch["projection"], epoch["manifest"], epoch["history"]]
        for ref in refs:
            path = self.root / ref["path"]; original = path.read_bytes(); path.chmod(0o600)
            for bad in (b"corrupted", None):
                if bad is None:
                    path.unlink()
                else:
                    path.write_bytes(bad)
                for check in (lambda: collection_status(self.store, self.collection_id), lambda: cohort_reading_report(self.store, [self.collection_id])):
                    with self.subTest(path=ref["path"], missing=bad is None), self.assertRaises(ResearchError):
                        check()
                path.write_bytes(original)

    def test_native_source_assertion_and_epoch_record_changes_fail_shared_evidence_check(self):
        from research_harness.acquisition import collection_status
        from research_harness.cohort_evidence import cohort_reading_report
        result = self.run_import()
        before = self.store.snapshot()["records"]
        targets = [("source", result["source_ids"][0], "origin_verified", True),
                   ("cohort_enumeration", result["enumeration_id"], "limits", "Changed claim."),
                   ("work_assertion", next(iter(before["work_assertion"])), "category", "quant-ph")]
        for i, (kind, key, field, value) in enumerate(targets):
            original = before[kind][key]
            def change(tx):
                tx.put(kind, key, dict(original, **{field: value}))
            self.store.mutate("fixture.change", {"i": i}, change, expected_revision=self.store.revision, request_id="tamper-" + str(i))
            for check in (lambda: collection_status(self.store, self.collection_id), lambda: cohort_reading_report(self.store, [self.collection_id])):
                with self.assertRaises(ResearchError):
                    check()
            self.store.mutate("fixture.restore", {"i": i}, lambda tx: tx.put(kind, key, original),
                              expected_revision=self.store.revision, request_id="restore-" + str(i))

    def test_original_xml_cannot_be_used_as_an_arbitrary_single_work_reading_span(self):
        from research_harness.source_links import validate_link
        from research_harness.artifacts import ArtifactStore
        result = self.run_import(self.inputs([page([article(), article("2603.00002")])]))
        records = self.store.snapshot()["records"]
        source = records["source"][result["source_ids"][0]]
        link = {"version_id": "arxiv:2603.00001v1", "source_id": source["id"], "artifact": source["response"],
                "locator": {"kind": "text", "start": 0, "end": 50}}
        with self.assertRaises(ResearchError) as error:
            validate_link(records, ArtifactStore(self.root), link)
        self.assertEqual(error.exception.code, "source_mismatch")

    def test_new_epoch_with_identical_abstracts_changes_preparation_digest(self):
        from research_harness.cohort_evidence import cohort_reading_report
        self.run_import()
        before = cohort_reading_report(self.store, [self.collection_id])
        self.run_import(self.inputs([page([article()], at="2026-09-14T03:00:00Z")]), request_id="new-epoch")
        after = cohort_reading_report(self.store, [self.collection_id])
        self.assertNotEqual(before["digest"], after["digest"])
        self.assertEqual(before["counts"], after["counts"])


if __name__ == "__main__":
    unittest.main()
