"""Exact manuscript, dual review and remote-intent publication boundaries."""

import hashlib
import importlib
import json
from pathlib import Path
from research_harness.errors import ResearchError

from development_fixtures import DevelopmentCase
from integration_fixtures import approve_publication_stop, observed_candidate


PLUGIN = Path(__file__).resolve().parents[1]


class ResearchPublicationTests(DevelopmentCase):
    def setUp(self):
        super().setUp()
        self.prepared_study()
        self.execution_payload = observed_candidate(self)
        (self.root / "draft").mkdir()
        (self.root / "evidence").mkdir()
        (self.root / "draft/paper.pdf").write_bytes(b"%PDF-1.4\n% Authored publication boundary fixture.\n%%EOF")
        (self.root / "draft/abstract.txt").write_text("The exact finite bound was enumerated.")
        (self.root / "draft/references.bib").write_text("@article{bounded,title={Authored bound}}\n")
        (self.root / "evidence/claims.json").write_text(json.dumps([{"id": "bound", "claim": "The maximum is 9.", "source": "The recorded finite enumeration."}]))

    def publication(self):
        self.assertTrue((PLUGIN / "research_harness/publication.py").is_file(), "The exact publication contract is missing")
        return importlib.import_module("research_harness.publication")

    def bundle_payload(self):
        return {"id": "paper-1", "files": {"pdf": "draft/paper.pdf", "abstract": "draft/abstract.txt",
            "bibliography": "draft/references.bib", "claims": "evidence/claims.json", "sources": None},
            "claim_evidence": [{"claim_id": "bound", "evidence": [self.result_evidence(self.execution_payload)]}]}

    def core(self, decision="accept"):
        return {"summary": "The authored finite result is explicitly scoped.", "strengths": ["All four integers are enumerated."],
                "weaknesses": ["The result establishes no unbounded generalization."], "soundness": 3,
                "presentation": 3, "contribution": 3, "overall": 6, "decision": decision}

    def manuscript_review(self, bundle, assessor, decision="accept"):
        return {"id": assessor, "bundle_digest": bundle["digest"], "assessor": {"id": assessor, "kind": "agent",
            "provenance": self.artifacts.put(("Authored independent context " + assessor).encode(), "text/plain"),
            "relationship": "A separate fixture assessor.", "independence_basis": "A new blind context received the manuscript and exact evidence bytes."},
            "review": self.artifacts.put(json.dumps(self.core(decision)).encode(), "application/json"), "blind": True}

    def test_readiness_alone_is_not_a_manuscript_or_dual_review_receipt(self):
        api = self.publication()
        self.assertFalse(api.publication_report(self.store)["ready"])
        bundle = self.mutate(api.prepare_publication, self.bundle_payload())["result"]
        self.assertFalse(api.publication_report(self.store)["ready"])
        self.mutate(api.record_manuscript_review, self.manuscript_review(bundle, "reviewer-a"))
        self.assertFalse(api.publication_report(self.store)["ready"])
        self.mutate(api.record_manuscript_review, self.manuscript_review(bundle, "reviewer-b"))
        self.assertTrue(api.publication_report(self.store)["ready"])

    def test_claim_continuity_markers_have_the_stated_shape(self):
        api = self.publication()
        claim = {"id": "bound", "claim": "The maximum is 9.", "source": "The recorded finite enumeration."}
        for marker in ({"revised": None}, {"revised": True}, {"revised": {}}, {"revised": {"previous": "", "reason": "Sharpened."}},
                       {"revised": {"previous": "The maximum is 8.", "reason": "Sharpened.", "extra": 1}},
                       {"superseded": {}}, {"superseded": {"reason": None}},
                       {"revised": {"previous": "The maximum is 8.", "reason": "Sharpened."}, "superseded": {"reason": "Widened."}}):
            (self.root / "evidence/claims.json").write_text(json.dumps([dict(claim, **marker)]))
            with self.subTest(marker=marker):
                self.assert_error("publication_claims_missing", lambda: self.mutate(api.prepare_publication, self.bundle_payload()))
        for marker in ({"revised": {"previous": "The maximum is 8.", "reason": "Sharpened."}}, {"superseded": {"reason": "Widened."}}):
            (self.root / "evidence/claims.json").write_text(json.dumps([dict(claim, **marker)]))
            payload = dict(self.bundle_payload(), id="paper-" + next(iter(marker)))
            self.assertEqual(self.mutate(api.prepare_publication, payload)["result"]["id"], payload["id"])

    def test_changed_pdf_abstract_bibliography_and_claims_invalidate_the_exact_bundle(self):
        api = self.publication()
        bundle = self.mutate(api.prepare_publication, self.bundle_payload())["result"]
        self.mutate(api.record_manuscript_review, self.manuscript_review(bundle, "reviewer-a"))
        self.mutate(api.record_manuscript_review, self.manuscript_review(bundle, "reviewer-b"))
        for path in ("draft/paper.pdf", "draft/abstract.txt", "draft/references.bib", "evidence/claims.json"):
            original = (self.root / path).read_bytes()
            (self.root / path).write_bytes(original + b" changed")
            with self.subTest(path=path):
                self.assertFalse(api.publication_report(self.store)["ready"])
            (self.root / path).write_bytes(original)
        self.assertTrue(api.publication_report(self.store)["ready"])

    def test_manuscript_delivery_contains_the_actual_candidate_bytes_and_no_review_scores(self):
        api = self.publication()
        bundle = self.mutate(api.prepare_publication, self.bundle_payload())["result"]
        self.mutate(api.record_manuscript_review, self.manuscript_review(bundle, "reviewer-a"))
        delivery = importlib.import_module("research_harness.review_delivery")
        directory = self.root / "blind-context"
        result = delivery.deliver_manuscript(self.store, directory)
        manifest = json.loads((directory / "inputs.json").read_text())
        self.assertEqual(manifest["bundle_digest"], bundle["digest"])
        for item in result["artifacts"]:
            self.assertEqual((directory / item["path"]).read_bytes(), self.artifacts.read(item))
        self.assertEqual((directory / bundle["files"]["pdf"]["artifact"]["path"]).read_bytes(), (self.root / "draft/paper.pdf").read_bytes())
        self.assertNotIn("readiness_review", json.dumps(manifest))
        self.assertNotIn('"overall"', json.dumps(manifest))

    def test_unrelated_pdf_fails_before_any_publication_intent(self):
        api = self.publication()
        bundle = self.mutate(api.prepare_publication, self.bundle_payload())["result"]
        for assessor in ("reviewer-a", "reviewer-b"):
            self.mutate(api.record_manuscript_review, self.manuscript_review(bundle, assessor))
        (self.root / "other.pdf").write_bytes(b"%PDF-1.4 unrelated")
        before = self.store.snapshot()
        self.assert_error("publication_artifact_mismatch", lambda: api.validate_upload(self.store,
            self.root / "other.pdf", self.root / "draft/abstract.txt", None))
        self.assertEqual(self.store.snapshot(), before)

    def remote_binding(self):
        api = self.publication()
        bundle = self.mutate(api.prepare_publication, self.bundle_payload())["result"]
        for assessor in ("reviewer-a", "reviewer-b"):
            self.mutate(api.record_manuscript_review, self.manuscript_review(bundle, assessor))
        approve_publication_stop(self, bundle)
        return {"bundle_digest": bundle["digest"], "prepared_revision": bundle["prepared_revision"],
                "base_url": "https://zenodo.org/api", "environment": "production", "new_version": False,
                "publish": True, "metadata": {"title": "Authored finite result", "description": "The exact finite enumeration."},
                "uploads": [{"name": "paper.pdf", "artifact": bundle["files"]["pdf"]["artifact"]}], "prior": None}

    def publish_remote(self, binding, lost=False):
        from research_harness.zenodo import deposit
        calls, created, uploaded = [], [], []
        def client(method, url, **kwargs):
            calls.append((method, url, kwargs))
            if method == "POST" and url.endswith("/deposit/depositions"):
                record = {"id": 91, "metadata": kwargs["json_body"]["metadata"],
                          "links": {"bucket": "https://zenodo.org/api/files/bucket", "html": "https://zenodo.org/deposit/91"}}
                created.append(record)
                if lost:
                    raise OSError("The server created the record, then the response was lost.")
                return record
            if method == "GET" and "/deposit/depositions?page=" in url:
                return created
            if method == "PUT" and "/files/" in url:
                uploaded.append(kwargs["file_bytes"])
                return {"filename": "paper.pdf"}
            if url.endswith("/actions/publish"):
                return {"id": 91, "doi": "10.5281/zenodo.91", "conceptdoi": "10.5281/zenodo.90", "links": {"record_html": "https://zenodo.org/records/91"}}
            return {}
        revision = self.store.revision
        if lost:
            with self.assertRaises(OSError):
                deposit(self.store, binding, client, expected_revision=revision, request_id="deposit-1")
        receipt = deposit(self.store, binding, client, expected_revision=revision, request_id="deposit-1")
        return receipt, calls, created, uploaded

    def test_lost_deposit_creation_is_reconciled_before_any_repeat_write(self):
        binding = self.remote_binding()
        receipt, calls, created, uploaded = self.publish_remote(binding, lost=True)
        # The deposit itself reads the listing, finds the record its intent token
        # names, and finishes on it. No second creation is sent.
        self.assertEqual(len(created), 1)
        self.assertEqual(sum(method == "POST" and url.endswith("/deposit/depositions") for method, url, _ in calls), 1)
        self.assertIn(("GET", "https://zenodo.org/api/deposit/depositions?page=1&size=100", {}), calls)
        self.assertEqual(uploaded, [(self.root / "draft/paper.pdf").read_bytes()])
        self.assertEqual(receipt["bundle_digest"], binding["bundle_digest"])
        self.assertEqual(self.store.snapshot()["records"]["remote_intent"]["deposit-1"]["status"], "complete")

    def test_a_creation_the_listing_shows_never_landed_is_discarded_and_the_deposit_creates_its_record(self):
        from research_harness.zenodo import deposit
        binding = self.remote_binding()
        attempts, created, calls = [], [], []

        def client(method, url, **kwargs):
            calls.append((method, url))
            if method == "POST" and url.endswith("/deposit/depositions"):
                attempts.append(url)
                if len(attempts) == 1:
                    raise OSError("The creation never reached the server.")
                record = {"id": 93, "metadata": kwargs["json_body"]["metadata"],
                          "links": {"bucket": "https://zenodo.org/api/files/bucket", "html": "https://zenodo.org/deposit/93"}}
                created.append(record)
                return record
            if method == "GET" and "/deposit/depositions?page=" in url:
                return created
            if url.endswith("/actions/publish"):
                return {"id": 93, "doi": "10.5281/zenodo.93", "conceptdoi": "10.5281/zenodo.92",
                        "links": {"record_html": "https://zenodo.org/records/93"}}
            return {}

        revision = self.store.revision
        with self.assertRaises(OSError):
            deposit(self.store, binding, client, expected_revision=revision, request_id="deposit-1")
        receipt = deposit(self.store, binding, client, expected_revision=revision, request_id="deposit-1")
        # The account's whole deposition listing carries no record of this intent,
        # so the claimed creation is discarded and the deposit creates its record.
        self.assertIn(("GET", "https://zenodo.org/api/deposit/depositions?page=1&size=100"), calls)
        self.assertEqual(len(attempts), 2)
        self.assertEqual(len(created), 1)
        self.assertEqual(receipt["doi"], "10.5281/zenodo.93")
        intent = self.store.snapshot()["records"]["remote_intent"]["deposit-1"]
        self.assertEqual(intent["status"], "complete")
        self.assertEqual(intent["responses"]["create"]["response"]["id"], 93)
        self.assertEqual(intent["discarded"], [{"name": "create", "observation": {
            "kind": "remote_read", "listing": "complete", "notes": "Exactory publication intent deposit-1"}}])

    def deposit_after_a_lost_write(self, binding, lost_url_ending):
        """Deposit twice, losing the response of one write that never landed.

        The fake record holds only what landed, so the second run reads a record
        with no trace of the lost write. Returns the receipt and every call."""
        from research_harness.zenodo import deposit
        calls = []
        record = {"id": 91, "submitted": False, "metadata": {}, "files": [],
                  "links": {"bucket": "https://zenodo.org/api/files/bucket", "html": "https://zenodo.org/deposit/91"}}
        draft_document = {"metadata": {"title": "Authored finite result"}, "files": {"enabled": True}}
        is_response_lost = [True]

        def client(method, url, **kwargs):
            calls.append((method, url))
            if method != "GET" and url.endswith(lost_url_ending) and is_response_lost[0]:
                is_response_lost[0] = False
                raise OSError("The write never reached the server.")
            if method == "POST" and url.endswith("/deposit/depositions"):
                record["metadata"] = kwargs["json_body"]["metadata"]
                return dict(record)
            if method == "PUT" and "/files/" in url:
                record["files"] = [{"filename": "paper.pdf",
                                    "checksum": "md5:" + hashlib.md5(kwargs["file_bytes"]).hexdigest()}]
                return {"filename": "paper.pdf"}
            if method == "PUT" and url.endswith("/deposit/depositions/91"):
                record["metadata"] = kwargs["json_body"]["metadata"]
                return dict(record)
            if method == "PUT" and url.endswith("/records/91/draft"):
                draft_document.update(kwargs["json_body"])
                return dict(draft_document)
            if url.endswith("/actions/publish"):
                record.update(submitted=True, doi="10.5281/zenodo.91", conceptdoi="10.5281/zenodo.90",
                              links=dict(record["links"], record_html="https://zenodo.org/records/91"))
                return dict(record)
            if method == "GET" and url.endswith("/records/91/draft"):
                return dict(draft_document)
            if method == "GET" and url.endswith("/deposit/depositions/91"):
                return dict(record)
            return {}

        revision = self.store.revision
        with self.assertRaises(OSError):
            deposit(self.store, binding, client, expected_revision=revision, request_id="deposit-1")
        receipt = deposit(self.store, binding, client, expected_revision=revision, request_id="deposit-1")
        return receipt, calls

    def assert_the_discarded_step_finished_the_deposit(self, receipt, calls, step_name, sent):
        self.assertEqual(receipt["doi"], "10.5281/zenodo.91")
        intent = self.store.snapshot()["records"]["remote_intent"]["deposit-1"]
        self.assertEqual(intent["status"], "complete")
        self.assertEqual([entry["name"] for entry in intent["discarded"]], [step_name])
        self.assertEqual(intent["discarded"][0]["observation"], {"kind": "remote_read", "deposition_id": 91})
        # Once lost, once after the record showed it never landed.
        self.assertEqual(calls.count(sent), 2)

    def test_an_upload_the_record_shows_never_landed_is_sent_again_and_the_deposit_finishes(self):
        receipt, calls = self.deposit_after_a_lost_write(self.remote_binding(), "/files/bucket/paper.pdf")
        self.assert_the_discarded_step_finished_the_deposit(
            receipt, calls, "upload:paper.pdf", ("PUT", "https://zenodo.org/api/files/bucket/paper.pdf"))

    def test_a_preview_the_draft_shows_never_landed_is_sent_again_and_the_deposit_finishes(self):
        receipt, calls = self.deposit_after_a_lost_write(self.remote_binding(), "/records/91/draft")
        self.assert_the_discarded_step_finished_the_deposit(
            receipt, calls, "preview", ("PUT", "https://zenodo.org/api/records/91/draft"))

    def test_a_publish_the_record_shows_never_landed_is_sent_to_that_same_record(self):
        receipt, calls = self.deposit_after_a_lost_write(self.remote_binding(), "/actions/publish")
        self.assert_the_discarded_step_finished_the_deposit(
            receipt, calls, "publish", ("POST", "https://zenodo.org/api/deposit/depositions/91/actions/publish"))
        # One record, published once: the deposit finished on the deposition the
        # interrupted run created.
        self.assertEqual(sum(method == "POST" and url.endswith("/deposit/depositions") for method, url in calls), 1)
        self.assertEqual(receipt["deposition_id"], 91)

    def test_a_published_record_that_does_not_match_the_intent_keeps_the_refusal(self):
        from research_harness.zenodo import deposit
        binding = self.remote_binding()

        def client(method, url, **kwargs):
            if method == "POST" and url.endswith("/deposit/depositions"):
                return {"id": 91, "metadata": kwargs["json_body"]["metadata"],
                        "links": {"bucket": "https://zenodo.org/api/files/bucket", "html": "https://zenodo.org/deposit/91"}}
            if url.endswith("/actions/publish"):
                raise OSError("The server published the record, then the response was lost.")
            if method == "GET" and url.endswith("/deposit/depositions/91"):
                # Submitted, and holding neither this intent's DOI nor its file.
                return {"id": 91, "submitted": True, "files": []}
            return {}

        revision = self.store.revision
        with self.assertRaises(OSError):
            deposit(self.store, binding, client, expected_revision=revision, request_id="deposit-1")
        # A published record no repeat can change keeps the claim and the refusal.
        self.assert_error("remote_reconciliation_required", lambda: deposit(
            self.store, binding, client, expected_revision=revision, request_id="deposit-1"))
        intent = self.store.snapshot()["records"]["remote_intent"]["deposit-1"]
        self.assertEqual(intent["pending"]["name"], "publish")
        self.assertNotIn("discarded", intent)

    def deposit_an_unpublished_draft(self, binding):
        """Deposit the same reviewed bundle again without publishing it. Its receipt
        carries no DOI, and neither does the workspace deposit record it leaves."""
        from research_harness.zenodo import deposit

        def client(method, url, **kwargs):
            if method == "POST" and url.endswith("/deposit/depositions"):
                return {"id": 94, "metadata": kwargs["json_body"]["metadata"],
                        "links": {"bucket": "https://zenodo.org/api/files/draft", "html": "https://zenodo.org/deposit/94"}}
            return {}

        return deposit(self.store, dict(binding, publish=False), client,
                       expected_revision=self.store.revision, request_id="deposit-draft")

    def test_a_publication_receipt_without_a_doi_is_not_a_submission_candidate(self):
        from research_harness.submission import validate_submission
        binding = self.remote_binding()
        publication, _, _, _ = self.publish_remote(binding)
        draft = self.deposit_an_unpublished_draft(binding)
        self.assertNotIn("doi", draft)
        self.assertNotIn("doi", self.store.snapshot()["records"]["workspace"]["deposit"])
        self.assertTrue(self.publication().publication_report(self.store, "deposited")["ready"])
        # The published record stays the only candidate; the draft receipt carries
        # no DOI to compare, so the submit is refused instead of failing on it.
        self.assert_error("publication_receipt_required",
                          lambda: validate_submission(self.store, {"doi": publication["doi"]}))

    def test_managed_submit_keeps_concept_doi_distinct_from_concrete_record(self):
        from research_harness.submission import submit_managed, continue_submission
        publication, _, _, _ = self.publish_remote(self.remote_binding())
        calls, mode = [], ["pending"]
        task = {"verificationId": "11111111-1111-4111-8111-111111111111", "doi": "10.5281/zenodo.90",
                "source": "zenodo", "sourceId": "91", "sourceVersion": None, "url": "https://zenodo.org/records/91"}
        def client(method, path, body=None, **kwargs):
            calls.append((method, path, body))
            if method == "POST":
                return {"verificationId": task["verificationId"], "doi": task["doi"]}, 201
            if mode[0] == "pending":
                return {}, 404
            if mode[0] == "wrong":
                return dict(task, sourceId="89", url="https://zenodo.org/records/89"), 200
            return task, 200
        before = self.store.snapshot()
        self.assert_error("publication_identifier_mismatch", lambda: submit_managed(self.store, {"doi": "10.5281/zenodo.89"}, client,
            expected_revision=self.store.revision, request_id="wrong"))
        self.assertEqual(self.store.snapshot(), before)
        self.assertEqual(calls, [])
        self.assert_error("verification_ingest_pending", lambda: submit_managed(self.store, {"doi": publication["doi"]}, client,
            expected_revision=self.store.revision, request_id="submit-1"))
        mode[0] = "wrong"
        self.assert_error("verification_target_mismatch", lambda: continue_submission(self.store, "submit-1", client))
        self.assertFalse(self.publication().publication_report(self.store, "submitted")["ready"])
        mode[0] = "correct"
        receipt = continue_submission(self.store, "submit-1", client)
        self.assertEqual(receipt["record_doi"], "10.5281/zenodo.91")
        self.assertEqual(receipt["canonical_doi"], "10.5281/zenodo.90")
        self.assertEqual(sum(method == "POST" for method, _, _ in calls), 1)
        self.assertTrue(self.publication().publication_report(self.store, "submitted")["ready"])

    def test_unknown_submit_keeps_one_intent_across_record_doi_and_url_spellings(self):
        from research_harness.submission import submit_managed, continue_submission
        publication, _, _, _ = self.publish_remote(self.remote_binding())
        calls, ingested = [], [False]
        task = {"verificationId": "11111111-1111-4111-8111-111111111111", "doi": "10.5281/zenodo.90",
                "source": "zenodo", "sourceId": "91", "sourceVersion": None, "url": "https://zenodo.org/records/91"}
        def client(method, path, body=None, **kwargs):
            calls.append((method, path, body))
            if method == "POST":
                if sum(call[0] == "POST" for call in calls) == 1:
                    raise OSError("The server accepted the request, then its response was lost")
                return {"verificationId": task["verificationId"], "doi": task["doi"]}, 201
            return (task, 200) if ingested[0] else ({}, 404)
        from research_harness.errors import ResearchError
        with self.assertRaises(OSError):
            submit_managed(self.store, {"doi": publication["doi"]}, client,
                expected_revision=self.store.revision, request_id="lost-submission")
        # The record URL names the same publication, so the managed path reaches
        # the one intent the lost POST left and refuses before any second write.
        with self.assertRaises(ResearchError) as raised:
            submit_managed(self.store, {"url": publication["record_url"]}, client,
                expected_revision=self.store.revision, request_id="same-record-url")
        self.assertEqual(raised.exception.code, "remote_reconciliation_required")
        self.assertEqual(raised.exception.details["intent_id"], "lost-submission")
        self.assertEqual(sum(call[0] == "POST" for call in calls), 1, calls)
        self.assertEqual({record["id"] for record in self.store.snapshot()["records"]["remote_intent"].values()
                          if record["kind"] == "submit"}, {"lost-submission"})
        ingested[0] = True
        receipt = continue_submission(self.store, "lost-submission", client)
        self.assertEqual(receipt["request_body"], {"doi": publication["doi"]})
        self.assertEqual(receipt["record_doi"], publication["doi"])
        self.assertEqual(sum(call[0] == "POST" for call in calls), 1)

    def test_validate_assessor_is_shared_and_refuses_authors(self):
        api = self.publication()
        from research_harness.evaluation import Evaluation
        records = self.store.snapshot()["records"]
        evaluation = Evaluation(records, self.artifacts)
        assessor = self.manuscript_review({"digest": "x"}, "reviewer-a")["assessor"]
        self.assertEqual(api.validate_assessor(evaluation, assessor, ["cycle-author"]), assessor)
        self.assert_error("review_not_independent", lambda: api.validate_assessor(evaluation, dict(assessor, id="CYCLE-AUTHOR "), ["cycle-author"]))
        self.assert_error("review_not_independent", lambda: api.validate_assessor(evaluation, dict(assessor, kind="robot"), []))
