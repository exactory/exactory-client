"""The records that finish paid work are written while other writers commit to the same store: an admitted run, a
remote intent, an acquisition, the pages of a cohort, a direct deposit and the concept DOI of a version draft."""

import contextlib
import io
import json
import os
import sqlite3
import subprocess
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from development_fixtures import DevelopmentCase
from integration_fixtures import admit_lab
from literature_fixtures import LiteratureCase
from research_fixtures import atom, client, crossref, entry, json_response, xml_response
from research_harness import (acquisition, development, execution, finishing, fulltext_jobs, integration, principles,
                              remote, storage, submission, zenodo)
from research_harness.errors import ResearchError
from research_harness.evidence import digest
from research_harness.operations import prepared_mutation
from research_harness.storage import Store
from test_draft import _load_bin_module

_draft = _load_bin_module("exactory-draft", "finishing_write_draft")
ORIGINAL_MUTATE = Store.mutate
# The commit entries of the store. A release before the one that adds mutate_serialized has the first two.
COMMIT_ENTRIES = [name for name in ("mutate", "mutate_scoped", "mutate_serialized") if hasattr(Store, name)]
# The members of the records of a managed run that 0.91.6 writes.
PRELAUNCH_MEMBERS_0_91_6 = ["admission_id", "binding_digest", "revision"]
EXECUTION_MEMBERS_0_91_6 = ["admitted_revision", "digest", "id", "payload", "plan_digest", "recorded_revision", "request_id"]
OBSERVATION_MEMBERS_0_91_6 = ["admission_id", "binding_digest", "claim_digest", "execution", "files", "log", "summary", "terminal"]
DEFINITION = {"corpus": "arxiv", "primaryCategory": "cs.LG", "windowStart": "2026-01-01", "windowEnd": "2026-06-30"}
ZENODO_BASE = "http://zenodo.test/api"
# Answers in the shapes that the services give (review opus-finishing-writes.md, section 2), with synthetic values.
CREATED_DEPOSITION = {
    "conceptrecid": "4241", "created": "2026-11-20T08:00:00+00:00", "files": [], "id": 4242,
    "links": {"bucket": "https://zenodo.test/api/files/bucket-1", "html": "https://zenodo.test/deposit/4242",
              "self": "https://zenodo.test/api/deposit/depositions/4242"},
    "metadata": {"title": "A synthetic study", "creators": [{"name": "Author, A."}],
                 "prereserve_doi": {"doi": "10.5072/zenodo.4242", "recid": 4242}},
    "modified": "2026-11-20T08:00:00+00:00", "owner": 1234, "record_id": 4242, "state": "unsubmitted",
    "submitted": False, "title": "A synthetic study"}
PUBLISHED_DEPOSITION = dict(CREATED_DEPOSITION, state="done", submitted=True, doi="10.5072/zenodo.4242",
                            conceptdoi="10.5072/zenodo.4241",
                            links=dict(CREATED_DEPOSITION["links"], record_html="https://zenodo.test/records/4242"))
UPLOADED_FILE = {"checksum": "md5:0cc175b9c0f1b6a831c399e269772661", "created": "2026-11-20T08:01:00+00:00",
                 "delete_marker": False, "is_head": True, "key": "paper.pdf",
                 "links": {"self": "https://zenodo.test/api/files/bucket-1/paper.pdf"}, "mimetype": "application/pdf",
                 "size": 1234, "updated": "2026-11-20T08:01:00+00:00", "version_id": "c4e3f0d2"}
PREVIEWED_DRAFT = {"id": "4242", "files": {"default_preview": "paper.pdf"},
                   "links": {"self": "https://zenodo.test/api/records/4242/draft",
                             "access_links": "https://zenodo.test/api/records/4242/access/links"},
                   "owners": [{"id": "1234"}], "recid": "4242", "revision": 3, "state": "draft", "status": "draft"}
CREATED_VERIFICATION = {"id": "verification-1", "status": "open", "doi": "10.5072/zenodo.4242",
                        "createdAt": "2026-11-20T08:00:00Z", "webUrl": "https://www.exactory.test/verifications/verification-1",
                        "supersedes": None, "supersededBy": None}
CREATED_VERDICT = {"id": "verdict-1", "stance": "sound", "summary": "A synthetic summary.", "rationaleSections": [],
                   "wouldChange": "A synthetic condition.", "suggestions": [], "status": "submitted",
                   "supersedesVerdictId": None, "verifierName": "A Verifier", "byRequester": False,
                   "createdAt": "2026-11-20T08:00:00Z", "findings": [{"id": "finding-1"}], "prediction": None, "votes": {}}


class WaitlessTime:
    """What research_harness.finishing sees as the time module, and no other module: it never sleeps. With ticking,
    each reading of its clock passes ten seconds, so that the wait of a finishing write ends without real time."""

    def __init__(self, ticking=False):
        self.ticking, self.now = ticking, 0.0

    def sleep(self, seconds):
        pass

    def monotonic(self):
        if not self.ticking:
            return time.monotonic()
        self.now += 10.0
        return self.now


class OtherWriters:
    """Another writer of the store of a test case, a held write lock, and a reader. The case gives self.root."""

    def start_other_writers(self):
        self.unrelated_count = 0
        self.attempts = {}
        waits = mock.patch.object(finishing, "time", WaitlessTime())
        waits.start()
        self.addCleanup(waits.stop)

    def commit_unrelated(self):
        """One commit of another writer: a record of a kind that no finishing write reads."""
        self.unrelated_count += 1
        number = self.unrelated_count
        other = Store(self.root)
        ORIGINAL_MUTATE(other, "fixture.unrelated", {"n": number},
                        lambda tx: tx.put("fixture_unrelated", "row-" + str(number), {"n": number}),
                        expected_revision=other.revision, request_id="unrelated-" + str(number))

    def meet_commits(self, operations, act=None, limit=None):
        """Before each commit of these operations another writer acts (by default it commits one unrelated record), at
        most limit times. Every commit entry of the store is wrapped, so a test does not depend on which of them a
        write uses. Returns a function that ends the meeting."""
        act = act or self.commit_unrelated
        patches = []

        def wrap(original):
            def commit(store, operation, payload, apply, **kwargs):
                if operation in operations:
                    count = self.attempts[operation] = self.attempts.get(operation, 0) + 1
                    if limit is None or count <= limit:
                        act()
                return original(store, operation, payload, apply, **kwargs)
            return commit
        for name in COMMIT_ENTRIES:
            patch = mock.patch.object(Store, name, wrap(getattr(Store, name)))
            patch.start()
            patches.append(patch)
            self.addCleanup(patch.stop)
        return lambda: [patch.stop() for patch in patches]

    def hold_write_lock(self):
        connection = sqlite3.connect(str(self.root / ".exactory" / "research.sqlite3"), isolation_level=None)
        connection.execute("BEGIN IMMEDIATE")
        self.addCleanup(connection.close)
        return connection

    def hold_read_lock(self):
        """A reader in the middle of a read: it holds the SHARED lock of the store until its transaction ends."""
        connection = sqlite3.connect(str(self.root / ".exactory" / "research.sqlite3"), isolation_level=None)
        self.addCleanup(connection.close)
        connection.execute("BEGIN")
        connection.execute("SELECT revision FROM metadata").fetchone()
        return connection

    def refuse_beyond_the_wait(self, operation, run):
        """Hold the write lock from the first commit of operation on, until run is refused; then release it and end
        the meeting."""
        held = []
        end = self.meet_commits((operation,), act=lambda: held or held.append(self.hold_write_lock()))
        with mock.patch.object(storage, "_BUSY_TIMEOUT_SECONDS", 0.01), \
                mock.patch.object(finishing, "time", WaitlessTime(ticking=True)):
            with self.assertRaises(ResearchError) as caught:
                run()
        held[0].execute("ROLLBACK")
        end()
        return caught.exception


class RunWriteTests(OtherWriters, DevelopmentCase):
    """The writes of exactory-lab run: the record that releases the program, and the records after the program."""

    def setUp(self):
        super().setUp()
        self.prepared_study()
        self.start_other_writers()

    def launch(self, admission, request_id="launch"):
        return execution.launch_execution(self.store, admission["id"], expected_revision=self.store.revision,
                                          request_id=request_id)

    def run_folder(self, admission):
        return self.root / "research/runs" / digest({"admission_id": admission["id"]})

    def records(self):
        return self.store.snapshot()["records"]

    def test_run_is_recorded_when_a_commit_meets_every_write_after_the_program(self):
        admission = admit_lab(self, body="print('{\"metric\": 7}')\n")
        self.meet_commits(("development.execution", "execution.observe", "execution.reconcile"))
        result = self.launch(admission)
        self.assertTrue(result["ok"])
        self.assertEqual(self.attempts, {"development.execution": 1, "execution.observe": 1, "execution.reconcile": 1})
        records = self.records()
        self.assertEqual(len(records["execution"]), 1)
        self.assertIn(admission["id"], records["execution_observation"])

    def test_observation_is_recorded_when_a_commit_meets_every_attempt(self):
        admission = admit_lab(self, body="print('{\"metric\": 7}')\n")
        self.meet_commits(("execution.observe",))
        self.assertTrue(self.launch(admission)["ok"])
        self.assertEqual(self.attempts, {"execution.observe": 1})
        self.assertIn(admission["id"], self.records()["execution_observation"])

    def test_receipt_of_the_reconciliation_is_recorded_when_a_commit_meets_every_attempt(self):
        admission = admit_lab(self, body="print('{\"metric\": 7}')\n")
        self.meet_commits(("execution.reconcile",))
        result = self.launch(admission)
        self.assertTrue(result["ok"])
        self.assertEqual(self.attempts, {"execution.reconcile": 1})
        self.assertEqual(self.store.committed_request("reconcile-launch")["response"]["result"], result)

    def test_launcher_reconciles_its_run_when_a_commit_lands_before_its_first_read(self):
        admission = admit_lab(self, body="print('{\"metric\": 7}')\n")
        snapshot, met = Store.snapshot, []

        def read(store):
            # Another writer commits once, after the claim, between the launcher's last revision read and its
            # reconciliation.
            if not met and (self.run_folder(admission) / "terminal.json").exists():
                met.append(True)
                self.commit_unrelated()
            return snapshot(store)
        with mock.patch.object(Store, "snapshot", read):
            result = self.launch(admission)
        self.assertEqual(met, [True])
        self.assertTrue(result["ok"])

    def test_start_is_recorded_when_a_commit_meets_every_attempt(self):
        admission = admit_lab(self, body="print('{\"metric\": 7}')\n")
        self.meet_commits(("execution.prelaunch",))
        result = self.launch(admission)
        self.assertTrue(result["ok"])
        self.assertEqual(self.attempts, {"execution.prelaunch": 1})
        prelaunch = self.records()["execution_prelaunch"][admission["id"]]
        receipt = self.store.committed_request("prelaunch-" + digest(self.records()["execution_claim"][admission["id"]]))
        self.assertEqual(prelaunch["revision"], receipt["response"]["revision"] - 1)

    def test_start_is_prepared_again_when_a_checked_record_changes(self):
        admission = admit_lab(self, body="print('{\"metric\": 7}')\n")

        def change_checked_record():
            # A new budget of the profile is one of the records that the check before the start reads.
            other = Store(self.root)
            from research_harness.resources import UNITS, set_budget
            set_budget(other, {"profile": "research", "purpose": "literature", "limits": {unit: None for unit in UNITS},
                               "reason": "Another command changes a record of the check."},
                       expected_revision=other.revision, request_id="other-budget")
        self.meet_commits(("execution.prelaunch",), act=change_checked_record, limit=1)
        result = self.launch(admission)
        self.assertTrue(result["ok"])
        self.assertEqual(self.attempts["execution.prelaunch"], 2)

    def test_store_busy_beyond_the_wait_before_the_start_runs_no_program_and_names_the_way_on(self):
        admission = admit_lab(self, body="print('{\"metric\": 7}')\n")
        refusal = self.refuse_beyond_the_wait("execution.prelaunch", lambda: self.launch(admission))
        self.assertEqual((refusal.code, refusal.message), ("store_busy", execution.RUN_START_STORE_BUSY_MSG))
        self.assertIn(refusal.details["cause"]["code"], finishing.SCOPED_FINISH_RETRY_CODES)
        self.assertEqual((refusal.details["admission_id"], refusal.details["request_id"]), (admission["id"], "launch"))
        records = self.records()
        self.assertIn(admission["id"], records["execution_claim"])
        self.assertNotIn(admission["id"], records.get("execution_prelaunch", {}))
        self.assertFalse((self.run_folder(admission) / "terminal.json").exists())
        self.assertFalse((self.run_folder(admission) / "stdout").exists())
        # The message names the same request, which starts the program (test_claimed_run_start). Recording the run as
        # interrupted stays possible.
        summary = execution.reconcile_execution(self.store, {"admission_id": admission["id"], "resolution": "interrupted",
            "reason": "The store stayed busy before the start."}, expected_revision=self.store.revision, request_id="way-on")
        self.assertFalse(summary["ok"])
        self.assertEqual([row["payload"]["status"] for row in self.records()["execution"].values()], ["interrupted"])

    def test_store_busy_beyond_the_wait_after_the_program_keeps_the_run_and_the_same_request_finishes(self):
        admission = admit_lab(self, body="print('{\"metric\": 7}')\n")
        with mock.patch.object(execution.subprocess, "Popen", wraps=subprocess.Popen) as started:
            refusal = self.refuse_beyond_the_wait("development.execution", lambda: self.launch(admission))
            self.assertEqual((refusal.code, refusal.message), ("store_busy", execution.RUN_RECORD_STORE_BUSY_MSG))
            self.assertIn(refusal.details["cause"]["code"], finishing.SCOPED_FINISH_RETRY_CODES)
            self.assertEqual((refusal.details["admission_id"], refusal.details["request_id"]), (admission["id"], "launch"))
            self.assertNotIn("execution", self.records())
            self.assertTrue((self.run_folder(admission) / "terminal.json").exists())
            result = self.launch(admission)
        self.assertTrue(result["ok"])
        self.assertEqual(started.call_count, 1)
        self.assertEqual([row["payload"]["status"] for row in self.records()["execution"].values()], ["completed"])

    def test_resolution_refused_beyond_the_wait_names_reconcile_run_and_the_same_command_records_it(self):
        admission = admit_lab(self, body="print('{\"metric\": 7}')\n")
        execution._claim(self.store, admission["id"], self.store.revision, "launch")
        payload = {"admission_id": admission["id"], "resolution": "interrupted",
                   "reason": "The launcher ended before the release."}
        refusal = self.refuse_beyond_the_wait("development.execution", lambda: execution.reconcile_execution(
            self.store, payload, expected_revision=self.store.revision, request_id="interrupted"))
        self.assertEqual((refusal.code, refusal.message), ("store_busy", execution.RUN_RESOLUTION_STORE_BUSY_MSG))
        self.assertNotIn("execution", self.records())
        # The way on that the message names.
        summary = execution.reconcile_execution(self.store, payload, expected_revision=self.store.revision,
                                                request_id="interrupted")
        self.assertFalse(summary["ok"])
        self.assertEqual([row["payload"]["status"] for row in self.records()["execution"].values()], ["interrupted"])

    def test_reconcile_run_names_the_start_of_a_claim_that_no_record_released(self):
        admission = admit_lab(self, body="print('{\"metric\": 7}')\n")
        execution._claim(self.store, admission["id"], self.store.revision, "launch")
        with self.assertRaises(ResearchError) as caught:
            execution.reconcile_execution(self.store, {"admission_id": admission["id"]},
                                          expected_revision=self.store.revision, request_id="reconcile")
        refusal = caught.exception
        self.assertEqual((refusal.code, refusal.message), ("execution_recovery_required", execution.RUN_NOT_RELEASED_MSG))
        self.assertEqual(refusal.details, {"admission_id": admission["id"], "request_id": "launch"})
        # The way on that the message names.
        self.assertTrue(self.launch(admission, request_id=refusal.details["request_id"])["ok"])

    def test_outcome_holds_the_revision_of_its_commit_and_the_digest_of_its_members(self):
        admission = admit_lab(self, body="print('{\"metric\": 7}')\n")
        self.meet_commits(("development.execution",))
        self.assertTrue(self.launch(admission)["ok"])
        records = self.records()
        record = next(iter(records["execution"].values()))
        receipt = self.store.committed_request("launcher-outcome-" + digest(records["execution_claim"][admission["id"]]))
        self.assertEqual(record["recorded_revision"], receipt["response"]["revision"])
        self.assertEqual(receipt["response"]["result"], record)
        self.assertEqual(record["digest"], digest({key: value for key, value in record.items() if key != "digest"}))

    def test_checkpoint_of_a_paused_cycle_names_the_revision_of_its_commit(self):
        plan = self.plan(max_executions=1, max_units=1)
        self.mutate(development.plan_cycle, plan)
        admission = self.admit(units=1)
        interrupted = self.execution(admission, status="interrupted", exit_code=None, used_units=None)
        self.meet_commits(("development.execution",), limit=1)
        receipt = finishing.retry_store_write(
            lambda: development.record_execution(self.store, interrupted, expected_revision=None, request_id="paused"),
            codes=finishing.SCOPED_FINISH_RETRY_CODES)
        self.assertEqual(self.attempts["development.execution"], 2)
        records = self.records()
        self.assertEqual(records["cycle"][plan["id"]]["status"], "budget_paused")
        checkpoint = next(row for row in records["checkpoint"].values() if row["cycle_id"] == plan["id"])
        record = records["execution"][interrupted["id"]]
        self.assertEqual((checkpoint["created_revision"], record["recorded_revision"]), (receipt["revision"],) * 2)
        self.assertEqual(checkpoint["execution_digests"], {record["id"]: record["digest"]})
        self.assertEqual(record["digest"], digest({key: value for key, value in record.items() if key != "digest"}))

    def test_reconcile_run_command_keeps_its_revision_input(self):
        admission = admit_lab(self, body="print('{\"metric\": 7}')\n")
        self.assertTrue(self.launch(admission)["ok"])
        self.assert_error("invalid_input", lambda: execution.reconcile_execution(
            self.store, {"admission_id": admission["id"]}, expected_revision=-1, request_id="invalid-revision"))

    def test_reconcile_run_command_refuses_a_stale_revision_before_a_new_observation(self):
        admission = admit_lab(self, body="print('{\"metric\": 7}')\n")
        with mock.patch.object(execution, "reconcile_execution", side_effect=ResearchError("fixture_stop", "Stop before the records")):
            self.assert_error("fixture_stop", lambda: self.launch(admission))
        stale = self.store.revision
        self.commit_unrelated()
        self.assert_error("stale_revision", lambda: execution.reconcile_execution(
            self.store, {"admission_id": admission["id"]}, expected_revision=stale, request_id="stale-reconcile"))
        self.assertNotIn("execution", self.records())

    def test_records_of_a_run_have_the_members_of_0_91_6(self):
        admission = admit_lab(self, body="print('{\"metric\": 7}')\n")
        self.assertTrue(self.launch(admission)["ok"])
        records = self.records()
        self.assertEqual(sorted(records["execution_prelaunch"][admission["id"]]), PRELAUNCH_MEMBERS_0_91_6)
        self.assertEqual(sorted(next(iter(records["execution"].values()))), EXECUTION_MEMBERS_0_91_6)
        self.assertEqual(sorted(records["execution_observation"][admission["id"]]), OBSERVATION_MEMBERS_0_91_6)
        operations = {row["operation"] for row in records["literature_operation"].values()}
        self.assertTrue({"execution.prelaunch", "development.execution", "execution.observe"} <= operations)
        self.assertNotIn("execution.reconcile", operations)


class PlainStoreCase(OtherWriters, unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.store = Store(self.root, create=True)
        self.start_other_writers()

    def records(self):
        return self.store.snapshot()["records"]


class RemoteWriteTests(PlainStoreCase):
    """The writes of a remote intent: the steps of a deposit, a submission or a verdict, and the receipt."""

    def setUp(self):
        super().setUp()
        remote.begin_intent(self.store, "fixture", {"id": "item"}, expected_revision=0, request_id="intent")
        self.sent = []

    def send(self):
        self.sent.append(True)
        return {"id": "confirmed"}

    def lose_response(self):
        raise ResearchError("fixture_failed", "Lost response")

    def test_answer_of_a_step_is_recorded_when_a_commit_meets_every_attempt(self):
        self.meet_commits(("remote.resolve",))
        self.assertEqual(remote.remote_step(self.store, "intent", "create", {}, self.send), {"id": "confirmed"})
        self.assertEqual((self.attempts, len(self.sent)), ({"remote.resolve": 1}, 1))
        intent = remote.get_intent(self.store, "intent")
        self.assertEqual((intent["status"], intent["pending"]), ("confirmed", None))
        self.assertEqual(intent["responses"]["create"]["response"], {"id": "confirmed"})

    def test_step_without_a_caller_revision_starts_when_a_commit_meets_its_claim(self):
        self.meet_commits(("remote.claim",))
        self.assertEqual(remote.remote_step(self.store, "intent", "create", {}, self.send), {"id": "confirmed"})
        self.assertEqual((self.attempts, len(self.sent)), ({"remote.claim": 1}, 1))

    def test_step_with_a_caller_revision_is_refused_at_once_when_the_store_moved(self):
        checked = self.store.revision
        self.commit_unrelated()
        with self.assertRaises(ResearchError) as caught:
            remote.remote_step(self.store, "intent", "create", {}, self.send, expected_revision=checked)
        self.assertEqual((caught.exception.code, self.sent), ("stale_revision", []))
        self.assertIsNone(remote.get_intent(self.store, "intent")["pending"])

    def test_refusal_of_a_checked_step_says_that_no_request_was_sent(self):
        checked = self.store.revision
        self.commit_unrelated()
        with self.assertRaises(ResearchError) as caught:
            remote.remote_step(self.store, "intent", "create", {}, self.send, expected_revision=checked)
        refusal = caught.exception
        self.assertEqual((refusal.code, refusal.message), ("stale_revision", remote.REMOTE_CLAIM_STORE_CHANGED_MSG))
        self.assertEqual(refusal.details["cause"]["details"], {"expected_revision": checked, "revision": checked + 1})
        self.assertEqual(self.sent, [])

    def test_busy_store_at_a_checked_step_says_that_no_request_was_sent(self):
        held = self.hold_write_lock()
        with mock.patch.object(storage, "_BUSY_TIMEOUT_SECONDS", 0.01):
            with self.assertRaises(ResearchError) as caught:
                remote.remote_step(self.store, "intent", "create", {}, self.send, expected_revision=self.store.revision)
        held.execute("ROLLBACK")
        refusal = caught.exception
        self.assertEqual((refusal.code, refusal.message), ("store_busy", remote.REMOTE_CLAIM_STORE_BUSY_MSG))
        self.assertEqual((refusal.details["cause"]["code"], self.sent), ("store_busy", []))
        self.assertIsNone(remote.get_intent(self.store, "intent")["pending"])

    def test_cleared_step_is_recorded_when_a_commit_meets_every_attempt(self):
        with self.assertRaises(ResearchError):
            remote.remote_step(self.store, "intent", "create", {}, self.lose_response)
        self.meet_commits(("remote.discard",))
        remote.discard_step(self.store, "intent", "create", {"kind": "remote_read"})
        intent = remote.get_intent(self.store, "intent")
        self.assertEqual((self.attempts, intent["pending"], intent["status"]), ({"remote.discard": 1}, None, "prepared"))

    def test_cleared_step_refused_beyond_the_wait_keeps_the_step_pending(self):
        with self.assertRaises(ResearchError):
            remote.remote_step(self.store, "intent", "create", {"request": 1}, self.lose_response)
        refusal = self.refuse_beyond_the_wait(
            "remote.discard", lambda: remote.discard_step(self.store, "intent", "create", {"kind": "remote_read"}))
        self.assertEqual((refusal.code, refusal.message), ("store_busy", remote.REMOTE_DISCARD_STORE_BUSY_MSG))
        self.assertEqual(remote.get_intent(self.store, "intent")["pending"], {"name": "create", "request": {"request": 1}})

    def test_receipt_is_recorded_when_a_commit_meets_every_attempt(self):
        remote.remote_step(self.store, "intent", "create", {}, self.send)
        self.meet_commits(("remote.finish",))
        receipt = remote.finish_intent(self.store, "intent", "fixture_receipt", {"id": "confirmed"}, workspace={"doi": "10.1234/x"})
        self.assertEqual((receipt, self.attempts), ({"id": "confirmed"}, {"remote.finish": 1}))
        records = self.records()
        self.assertEqual(records["remote_intent"]["intent"]["status"], "complete")
        self.assertEqual(records["workspace"]["deposit"], {"doi": "10.1234/x"})

    def test_answer_refused_beyond_the_wait_carries_the_members_that_the_step_needs(self):
        cases = [
            ("deposit", "create", CREATED_DEPOSITION,
             {"id": 4242, "record_id": 4242, "conceptrecid": "4241", "state": "unsubmitted", "submitted": False,
              "links": {"html": "https://zenodo.test/deposit/4242"},
              "metadata": {"prereserve_doi": {"doi": "10.5072/zenodo.4242"}}}),
            ("deposit", "publish", PUBLISHED_DEPOSITION,
             {"id": 4242, "record_id": 4242, "conceptrecid": "4241", "state": "done", "submitted": True,
              "doi": "10.5072/zenodo.4242", "conceptdoi": "10.5072/zenodo.4241",
              "links": {"html": "https://zenodo.test/deposit/4242", "record_html": "https://zenodo.test/records/4242"},
              "metadata": {"prereserve_doi": {"doi": "10.5072/zenodo.4242"}}}),
            ("deposit", "upload:paper.pdf", UPLOADED_FILE,
             {"key": "paper.pdf", "checksum": "md5:0cc175b9c0f1b6a831c399e269772661", "size": 1234}),
            ("deposit", "preview", PREVIEWED_DRAFT, {"id": "4242"}),
            ("submit", "submit", CREATED_VERIFICATION, CREATED_VERIFICATION),
            ("verdict", "verdict", CREATED_VERDICT,
             {"id": "verdict-1", "status": "submitted", "stance": "sound", "supersedesVerdictId": None,
              "createdAt": "2026-11-20T08:00:00Z"}),
        ]
        for kind, step, answer, kept in cases:
            with self.subTest(kind=kind, step=step):
                identifier = kind + "-" + step
                remote.begin_intent(self.store, kind, {"id": identifier}, expected_revision=self.store.revision,
                                    request_id=identifier)
                refusal = self.refuse_beyond_the_wait("remote.resolve", lambda: remote.remote_step(
                    self.store, identifier, step, {"request": 1}, lambda: answer))
                # The answer is checked before the message, so that a release that loses it fails on the answer.
                self.assertEqual(refusal.code, "store_busy")
                self.assertEqual((refusal.details["intent_id"], refusal.details["step"], refusal.details["response"]),
                                 (identifier, step, kept))
                self.assertEqual(remote.get_intent(self.store, identifier)["pending"],
                                 {"name": step, "request": {"request": 1}})
                self.assertEqual(refusal.message, remote.REMOTE_ANSWER_STORE_BUSY_MSG)
        # The members that can identify an account, or that hold the text of a verdict, stay out of every refusal.
        deposition = self.refuse_beyond_the_wait("remote.resolve", lambda: remote.remote_step(
            self.store, self.begin("deposit", "account-check"), "create", {}, lambda: CREATED_DEPOSITION)).details["response"]
        self.assertNotIn("owner", deposition)
        self.assertNotIn("bucket", deposition["links"])
        self.assertNotIn("creators", deposition["metadata"])
        verdict = self.refuse_beyond_the_wait("remote.resolve", lambda: remote.remote_step(
            self.store, self.begin("verdict", "text-check"), "verdict", {}, lambda: CREATED_VERDICT)).details["response"]
        self.assertFalse({"summary", "findings", "verifierName"} & set(verdict))

    def begin(self, kind, identifier):
        remote.begin_intent(self.store, kind, {"id": identifier}, expected_revision=self.store.revision,
                            request_id=identifier)
        return identifier

    def test_answer_of_a_step_outside_the_list_refused_beyond_the_wait_carries_no_response(self):
        refusal = self.refuse_beyond_the_wait(
            "remote.resolve", lambda: remote.remote_step(self.store, "intent", "create", {"request": 1}, self.send))
        self.assertEqual((refusal.code, refusal.message), ("store_busy", remote.REMOTE_ANSWER_STORE_BUSY_MSG))
        self.assertEqual((refusal.details["intent_id"], refusal.details["step"]), ("intent", "create"))
        self.assertNotIn("response", refusal.details)
        self.assertIn(refusal.details["cause"]["code"], finishing.SCOPED_FINISH_RETRY_CODES)
        self.assertEqual(len(self.sent), 1)
        self.assertEqual(remote.get_intent(self.store, "intent")["pending"], {"name": "create", "request": {"request": 1}})
        with self.assertRaises(ResearchError) as caught:
            remote.remote_step(self.store, "intent", "create", {"request": 1}, self.send)
        self.assertEqual((caught.exception.code, len(self.sent)), ("remote_reconciliation_required", 1))

    def test_deposition_read_refused_beyond_the_wait_names_the_read(self):
        metadata = {"title": "A synthetic study"}
        identifier = "deposit-read"
        remote.begin_intent(self.store, "deposit", {"base_url": ZENODO_BASE, "new_version": False, "uploads": []},
                            expected_revision=self.store.revision, request_id=identifier)
        remote.remote_step(self.store, identifier, "create", {}, lambda: CREATED_DEPOSITION)
        with self.assertRaises(ResearchError):
            remote.remote_step(self.store, identifier, "metadata", {"metadata": metadata}, self.lose_response)
        reads = []

        def read_service(method, url, **kwargs):
            reads.append((method, url))
            return dict(CREATED_DEPOSITION, metadata=dict(CREATED_DEPOSITION["metadata"], **metadata))
        refusal = self.refuse_beyond_the_wait("remote.resolve", lambda: zenodo.reconcile_pending(
            self.store, identifier, read_service))
        self.assertEqual((refusal.code, refusal.message), ("store_busy", remote.REMOTE_READ_STORE_BUSY_MSG))
        self.assertEqual(reads, [("GET", ZENODO_BASE + "/deposit/depositions/4242")])
        self.assertEqual(refusal.details["response"]["id"], 4242)
        self.assertNotIn("owner", refusal.details["response"])
        self.assertEqual(remote.get_intent(self.store, identifier)["pending"]["name"], "metadata")
        # The way on that the message names: the same command reads the service again and records the step.
        self.assertIsNone(zenodo.reconcile_pending(self.store, identifier, read_service)["pending"])

    def test_task_read_refused_beyond_the_wait_names_the_read(self):
        publication = {"deposition_id": 4242, "doi": "10.5072/zenodo.4242", "concept_doi": "10.5072/zenodo.4241"}
        identifier = "submit-read"
        remote.begin_intent(self.store, "submit", {"bundle_digest": "fixture", "publication": publication,
                                                   "body": {"doi": publication["doi"]}},
                            expected_revision=self.store.revision, request_id=identifier)
        with self.assertRaises(ResearchError):
            remote.remote_step(self.store, identifier, "submit", {"doi": publication["doi"]}, self.lose_response)
        task = {"verificationId": "verification-1", "doi": publication["doi"], "source": "zenodo", "sourceId": "4242",
                "sourceVersion": 1, "url": "https://zenodo.org/records/4242"}

        def read_task(method, path, body=None, allow_missing=False):
            return task, 200
        refusal = self.refuse_beyond_the_wait("remote.resolve", lambda: submission.continue_submission(
            self.store, identifier, read_task))
        self.assertEqual((refusal.code, refusal.message), ("store_busy", remote.REMOTE_READ_STORE_BUSY_MSG))
        self.assertEqual(refusal.details["response"], {"verificationId": "verification-1", "doi": publication["doi"]})
        self.assertEqual(remote.get_intent(self.store, identifier)["pending"]["name"], "submit")

    def test_receipt_refused_beyond_the_wait_is_recorded_by_the_same_call(self):
        remote.remote_step(self.store, "intent", "create", {}, self.send)
        refusal = self.refuse_beyond_the_wait(
            "remote.finish", lambda: remote.finish_intent(self.store, "intent", "fixture_receipt", {"id": "confirmed"}))
        self.assertEqual((refusal.code, refusal.message), ("store_busy", remote.REMOTE_RECEIPT_STORE_BUSY_MSG))
        self.assertEqual(remote.finish_intent(self.store, "intent", "fixture_receipt", {"id": "confirmed"}), {"id": "confirmed"})
        self.assertEqual((remote.get_intent(self.store, "intent")["status"], len(self.sent)), ("complete", 1))

    def test_task_observation_is_recorded_when_a_commit_meets_every_attempt(self):
        self.meet_commits(("submission.observe",))
        observed = submission._observation(self.store, "submit", "matched", {"verificationId": "confirmed"})
        self.assertEqual(observed["result"]["response"], {"verificationId": "confirmed"})
        self.assertEqual(self.attempts, {"submission.observe": 1})

    def test_publication_read_back_is_recorded_when_a_commit_meets_every_attempt(self):
        self.meet_commits(("publication.read_back",))
        receipt = {"environment": "sandbox", "deposition_id": 123, "draft_url": "https://example.org/123"}
        published = zenodo._record_read_back_publication(self.store, "publish", receipt, {"doi": "10.1234/example"})
        self.assertEqual(published["doi"], "10.1234/example")
        self.assertEqual(self.attempts, {"publication.read_back": 1})
        self.assertEqual(self.records()["publication_read_back"]["publish"]["doi"], "10.1234/example")

    def test_direct_deposit_is_recorded_when_a_commit_meets_every_attempt(self):
        state = {"environment": "sandbox", "deposition_id": 123, "draft_url": "https://example.org/123", "doi": "10.5072/x"}
        self.meet_commits(("workspace.direct_deposit",))
        finishing.retry_store_write(lambda: integration.record_direct_deposit(self.store, state, expected_revision=None,
                                    request_id="direct"), codes=finishing.SCOPED_FINISH_RETRY_CODES)
        self.assertEqual(self.attempts, {"workspace.direct_deposit": 1})
        self.assertEqual(self.records()["workspace"]["deposit"], state)

    def test_request_id_of_a_step_change_ends_with_the_revision_of_its_preparation(self):
        before = self.store.revision
        remote.remote_step(self.store, "intent", "create", {}, self.send)
        self.assertIsNotNone(self.store.committed_request("intent:claim:" + str(before)))
        self.assertIsNotNone(self.store.committed_request("intent:resolve:" + str(before + 1)))


class AcquisitionWriteTests(PlainStoreCase):
    """The writes of an acquisition: its result, and each page of a cohort collection."""

    def acquire(self, http, request_id="acquire"):
        return acquisition.acquire_work(self.store, "2601.00001v1", request_id=request_id,
                                        expected_revision=self.store.revision, http=http)

    def one_work(self):
        return client([xml_response(atom([entry()], total=1))])

    def two_pages(self):
        return client([xml_response(atom([entry()], total=2, size=1)),
                       xml_response(atom([entry("2601.00002v1")], total=2, start=1, size=1))])

    def test_result_is_recorded_when_a_commit_meets_every_attempt(self):
        http, wire, _ = self.one_work()
        self.meet_commits(("acquisition.finish",))
        result = self.acquire(http)
        self.assertEqual((result["status"], len(wire.requests), self.attempts), ("complete", 1, {"acquisition.finish": 1}))
        self.assertIn("arxiv:2601.00001v1", self.records()["work"])

    def test_result_names_the_revision_of_its_commit(self):
        http, _, _ = self.one_work()
        self.meet_commits(("acquisition.finish",))
        result = self.acquire(http)
        operation = self.records()["acquisition_operation"]["acquire"]
        self.assertEqual((result["revision"], operation["result"]["revision"]), (self.store.revision,) * 2)
        self.assertEqual(operation["state"], "finished")

    def test_pages_of_a_cohort_are_recorded_when_a_commit_meets_every_attempt(self):
        http, wire, _ = self.two_pages()
        self.meet_commits(("cohort.page", "acquisition.finish"))
        result = acquisition.collect_cohort(self.store, DEFINITION, request_id="collect", expected_revision=0,
                                            http=http, page_size=1)
        self.assertEqual((result["status"], len(wire.requests)), ("complete", 2))
        self.assertEqual(self.attempts, {"cohort.page": 2, "acquisition.finish": 1})

    def test_result_refused_beyond_the_wait_says_what_is_kept_and_a_new_request_replaces_it(self):
        http, wire, _ = self.one_work()
        refusal = self.refuse_beyond_the_wait("acquisition.finish", lambda: self.acquire(http))
        self.assertEqual((refusal.code, refusal.message), ("store_busy", acquisition.ACQUISITION_RESULT_STORE_BUSY_MSG))
        self.assertIn(refusal.details["cause"]["code"], finishing.SCOPED_FINISH_RETRY_CODES)
        self.assertEqual((self.records()["acquisition_operation"]["acquire"]["state"], len(wire.requests)), ("admitted", 1))
        self.assertEqual(self.acquire(http)["pending"], [{"code": "operation_incomplete"}])
        again, wire_again, _ = self.one_work()
        self.assertEqual(self.acquire(again, request_id="acquire-again")["status"], "complete")
        states = {key: row["state"] for key, row in self.records()["acquisition_operation"].items()}
        self.assertEqual((states, len(wire_again.requests)), ({"acquire": "superseded", "acquire-again": "finished"}, 1))

    def test_page_refused_beyond_the_wait_keeps_the_position_and_resume_goes_on(self):
        http, wire, _ = self.two_pages()
        refusal = self.refuse_beyond_the_wait("cohort.page", lambda: acquisition.collect_cohort(
            self.store, DEFINITION, request_id="collect", expected_revision=0, http=http, page_size=1))
        self.assertEqual((refusal.code, refusal.message), ("store_busy", acquisition.COHORT_PAGE_STORE_BUSY_MSG))
        collection = next(iter(self.records()["collection"].values()))
        self.assertEqual((collection["sequence"], collection["active_operation"], len(wire.requests)), (0, "collect", 1))
        again, _, _ = self.two_pages()
        result = acquisition.resume_cohort(self.store, collection["id"], request_id="resume",
                                           expected_revision=self.store.revision, http=again)
        self.assertEqual((result["status"], result["member_count"]), ("complete", 2))

    def test_end_of_a_cohort_refused_beyond_the_wait_keeps_every_page_and_resume_ends_it(self):
        http, wire, _ = self.two_pages()
        refusal = self.refuse_beyond_the_wait("acquisition.finish", lambda: acquisition.collect_cohort(
            self.store, DEFINITION, request_id="collect", expected_revision=0, http=http, page_size=1))
        self.assertEqual((refusal.code, refusal.message), ("store_busy", acquisition.COHORT_END_STORE_BUSY_MSG))
        collection = next(iter(self.records()["collection"].values()))
        self.assertEqual((collection["sequence"], collection["active_operation"], len(wire.requests)), (2, "collect", 2))
        again, wire_again, _ = client([])
        result = acquisition.resume_cohort(self.store, collection["id"], request_id="resume",
                                           expected_revision=self.store.revision, http=again)
        self.assertEqual((result["status"], result["member_count"], len(wire_again.requests)), ("complete", 2, 0))

    def test_import_of_a_response_keeps_the_revision_of_its_snapshot(self):
        end = self.meet_commits(("acquisition.finish",))
        with self.assertRaises(ResearchError) as caught:
            acquisition.import_response(self.store, "arxiv", atom([entry()], total=1),
                                        source_url="https://export.arxiv.org/api/query?id_list=2601.00001v1",
                                        captured_at="2026-09-07T12:00:00Z", expected_revision=0, request_id="import")
        end()
        self.assertEqual(caught.exception.code, "stale_revision")


class ImportWriteTests(PlainStoreCase):
    """The result of import-fulltext, which sends no request."""

    def setUp(self):
        super().setUp()
        principles.initialize_research(self.store, {"profile": "research", "target": None,
                                                    "preparation_policy": "exhaustive-v1"},
                                       expected_revision=0, request_id="init")
        http, _, _ = client([json_response(crossref())])
        acquisition.acquire_work(self.store, "10.1234/example", http=http, expected_revision=self.store.revision,
                                 request_id="metadata")
        (self.root / "inputs").mkdir()
        (self.root / "inputs/survey.txt").write_text("A synthetic survey. References: none.", encoding="utf-8")

    def import_text(self, request_id):
        return acquisition.import_fulltext(self.store, "doi:10.1234/example", "inputs/survey.txt", {
            "saved_by": "A reader", "obtained_from": "https://publisher.example/survey.txt",
            "obtained_at": "2026-11-20T08:00:00-07:00", "statement": "Saved the complete text using a browser."},
            request_id=request_id, expected_revision=self.store.revision)

    def test_import_refused_beyond_the_wait_names_a_new_request_that_imports_the_file_again(self):
        refusal = self.refuse_beyond_the_wait("acquisition.finish", lambda: self.import_text("import"))
        self.assertEqual((refusal.code, refusal.message), ("store_busy", acquisition.IMPORT_RESULT_STORE_BUSY_MSG))
        self.assertEqual(self.records()["acquisition_operation"]["import"]["state"], "admitted")
        self.assertEqual(self.import_text("import")["pending"], [{"code": "operation_incomplete"}])
        # The way on that the message names.
        self.assertEqual(self.import_text("import-again")["status"], "complete")


class VisualWriteTests(OtherWriters, LiteratureCase):
    """The result of an acquisition of a visual asset, which ends with the same write as every acquisition."""

    def setUp(self):
        super().setUp()
        self.start_other_writers()

    def test_result_of_a_visual_asset_is_recorded_when_a_commit_meets_every_attempt(self):
        from research_harness.visual_assets import acquire_visual_asset
        from test_research_source_contracts import SourceContractTests
        version = self.metadata(9)
        capture = self.capture(version, body='<figure><img src="/assets/required.png"><figcaption>CHECKED FIGURE.'
                                             '</figcaption></figure> References: none.')
        link = {"version_id": version, "source_id": capture["source_id"], "artifact": capture["original"],
                "locator": {"kind": "html", "anchor": self.span(capture["original"],
                                                                "<figcaption>CHECKED FIGURE.</figcaption>", kind="span")}}
        http, wire, _ = client([(200, {"Content-Type": "image/png"}, SourceContractTests.png(self))])
        self.meet_commits(("acquisition.finish",))
        result = acquire_visual_asset(self.store, link, "https://arxiv.org/assets/required.png", http=http,
                                      expected_revision=self.store.revision, request_id="visual")
        self.assertEqual((result["status"], len(wire.requests), self.attempts), ("complete", 1, {"acquisition.finish": 1}))
        self.assertIn("visual", self.store.snapshot()["records"]["visual_asset"])


class DraftDepositWriteTests(OtherWriters, unittest.TestCase):
    """The records that exactory-draft writes after a deposit: a direct deposit, and the concept DOI of a version
    draft."""

    def setUp(self):
        scratch = tempfile.TemporaryDirectory()
        self.addCleanup(scratch.cleanup)
        self.addCleanup(os.chdir, os.getcwd())
        os.chdir(scratch.name)
        self.root = Path.cwd()
        self.store = Store(self.root, create=True)
        self.start_other_writers()
        self.state = {"environment": "sandbox", "deposition_id": 4343, "draft_url": ZENODO_BASE + "/deposit/4343",
                      "doi": "10.5072/zenodo.4343"}

    def capture_exit(self, call):
        err = io.StringIO()
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(err):
            with self.assertRaises(SystemExit) as caught:
                call()
        self.assertEqual(caught.exception.code, 1)
        return err.getvalue()

    def read_local_record(self):
        return json.loads((self.root / ".exactory/deposit.json").read_text())

    def test_direct_deposit_after_the_publish_is_recorded_when_a_commit_meets_every_attempt(self):
        self.meet_commits(("workspace.direct_deposit",))
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            _draft._save_deposit_state(self.store, None, self.state)
        self.assertEqual(self.attempts, {"workspace.direct_deposit": 1})
        self.assertEqual(self.store.snapshot()["records"]["workspace"]["deposit"], self.state)
        self.assertEqual(self.read_local_record(), self.state)

    def test_direct_deposit_refused_beyond_the_wait_says_that_the_store_did_not_record_it(self):
        held = []
        end = self.meet_commits(("workspace.direct_deposit",), act=lambda: held or held.append(self.hold_write_lock()))
        with mock.patch.object(storage, "_BUSY_TIMEOUT_SECONDS", 0.01), \
                mock.patch.object(finishing, "time", WaitlessTime(ticking=True)):
            err = self.capture_exit(lambda: _draft._save_deposit_state(self.store, None, self.state))
        held[0].execute("ROLLBACK")
        end()
        self.assertIn("Managed record skipped (store_busy): " + _draft._DIRECT_DEPOSIT_STORE_BUSY_MSG, err)
        self.assertIn("The workspace store did not record this deposit.", err)
        self.assertEqual(self.read_local_record(), self.state)
        self.assertNotIn("workspace", self.store.snapshot()["records"])

    def begin_version_draft(self):
        self.enterContext(mock.patch.object(_draft, "_ZENODO_API_URLS", {"sandbox": ZENODO_BASE}))
        self.enterContext(mock.patch.object(_draft, "_open_url",
                                            side_effect=AssertionError("a completed intent sent a Zenodo request")))
        self.concept_doi = "10.5072/zenodo.42"
        draft = {"environment": "sandbox", "deposition_id": 4343, "draft_url": ZENODO_BASE + "/deposit/4343"}
        binding = {"environment": "sandbox", "base_url": ZENODO_BASE, "new_version": True, "publish": False,
                   "prior": {"concept_doi": self.concept_doi}}
        remote.begin_intent(self.store, "deposit", binding, expected_revision=self.store.revision,
                            request_id="version-intent")
        remote.finish_intent(self.store, "version-intent", "publication_receipt", dict(draft, intent_id="version-intent"),
                             workspace=draft)
        return draft

    # Python 3.9's TestCase has no enterContext.
    def enterContext(self, manager):
        value = manager.__enter__()
        self.addCleanup(manager.__exit__, None, None, None)
        return value

    def continue_version_draft(self):
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            return _draft._continue_deposit_and_track_publish(self.store, "version-intent",
                                                              _draft._build_zenodo_client("sandbox", "test-token"))

    def test_concept_of_a_version_draft_is_recorded_when_the_store_moves_once(self):
        draft = self.begin_version_draft()
        self.meet_commits(("workspace.version_draft_concept",), limit=1)
        self.continue_version_draft()
        self.assertEqual(self.attempts, {"workspace.version_draft_concept": 1})
        self.assertEqual(self.store.snapshot()["records"]["workspace"]["deposit"], dict(draft, concept_doi=self.concept_doi))
        self.assertEqual(self.read_local_record()["concept_doi"], self.concept_doi)

    def test_concept_of_a_version_draft_leaves_a_newer_deposit_that_lands_before_its_commit(self):
        self.begin_version_draft()
        newer = {"environment": "sandbox", "deposition_id": 4545, "draft_url": ZENODO_BASE + "/deposit/4545"}

        def record_newer_deposit():
            other = Store(self.root)
            integration.record_direct_deposit(other, newer, expected_revision=other.revision, request_id="newer-draft")
        self.meet_commits(("workspace.version_draft_concept",), act=record_newer_deposit, limit=1)
        self.continue_version_draft()
        self.assertEqual(self.attempts, {"workspace.version_draft_concept": 1})
        self.assertEqual(self.store.snapshot()["records"]["workspace"]["deposit"], newer)


class ReaderAtCommitTests(PlainStoreCase):
    """A COMMIT that a reader of the store holds up: a finishing write keeps its transaction and commits once the
    reader has finished; every other write is refused after the busy timeout."""

    def setUp(self):
        super().setUp()
        remote.begin_intent(self.store, "fixture", {"id": "item"}, expected_revision=0, request_id="intent")
        remote.remote_step(self.store, "intent", "create", {}, lambda: {"id": "confirmed"})

    def observe_commits(self, refused, on_refusal):
        """Open every connection of the store so that a COMMIT that SQLite refuses is counted, and on_refusal runs
        before the refusal is raised."""
        connect = sqlite3.connect

        class ObservedConnection(sqlite3.Connection):
            def commit(self):
                try:
                    return super().commit()
                except sqlite3.OperationalError:
                    refused.append(True)
                    on_refusal()
                    raise
        return mock.patch.object(storage.sqlite3, "connect",
                                 lambda *args, **kwargs: connect(*args, factory=ObservedConnection, **kwargs))

    def test_commit_of_a_finishing_write_that_a_reader_holds_up_is_tried_again_in_the_same_transaction(self):
        reader, refused = self.hold_read_lock(), []
        self.meet_commits(("remote.finish",), act=lambda: None)
        # The reader ends its read after the first refused COMMIT, as a read of the store ends.
        with self.observe_commits(refused, lambda: reader.execute("COMMIT")), \
                mock.patch.object(storage, "_BUSY_TIMEOUT_SECONDS", 0.01):
            receipt = remote.finish_intent(self.store, "intent", "fixture_receipt", {"id": "confirmed"})
        self.assertEqual((receipt, refused, self.attempts), ({"id": "confirmed"}, [True], {"remote.finish": 1}))
        self.assertEqual(remote.get_intent(self.store, "intent")["status"], "complete")

    def test_commit_of_a_finishing_write_that_readers_hold_up_beyond_the_wait_is_refused_with_its_message(self):
        reader, refused, clock = self.hold_read_lock(), [], WaitlessTime(ticking=True)
        with self.observe_commits(refused, lambda: None), mock.patch.object(storage, "_BUSY_TIMEOUT_SECONDS", 0.01), \
                mock.patch.object(finishing, "time", clock), mock.patch.object(storage, "time", clock):
            with self.assertRaises(ResearchError) as caught:
                remote.finish_intent(self.store, "intent", "fixture_receipt", {"id": "confirmed"})
        reader.execute("COMMIT")
        self.assertEqual((caught.exception.code, caught.exception.message),
                         ("store_busy", remote.REMOTE_RECEIPT_STORE_BUSY_MSG))
        self.assertGreater(len(refused), 1)
        self.assertEqual(remote.get_intent(self.store, "intent")["status"], "confirmed")

    def test_commit_of_another_write_that_a_reader_holds_up_is_refused_after_the_busy_timeout(self):
        reader, refused = self.hold_read_lock(), []
        with self.observe_commits(refused, lambda: None), mock.patch.object(storage, "_BUSY_TIMEOUT_SECONDS", 0.01):
            with self.assertRaises(ResearchError) as caught:
                ORIGINAL_MUTATE(self.store, "fixture.other", {}, lambda tx: tx.put("fixture_other", "row", {"n": 1}),
                                expected_revision=self.store.revision, request_id="other")
        reader.execute("COMMIT")
        self.assertEqual((caught.exception.code, refused), ("store_busy", [True]))
        self.assertNotIn("fixture_other", self.records())


class EarlierReleaseTests(PlainStoreCase):
    """Requests that an earlier release committed with the compare-and-swap of the whole store are replayed."""

    def test_receipt_of_an_intent_that_an_earlier_release_recorded_is_replayed_without_a_new_event(self):
        remote.begin_intent(self.store, "fixture", {"id": "item"}, expected_revision=0, request_id="intent")
        remote.remote_step(self.store, "intent", "create", {}, lambda: {"id": "confirmed"})
        receipt = {"id": "confirmed"}

        def prepare(records, value):
            return [("fixture_receipt", "intent", receipt),
                    ("remote_intent", "intent", dict(records["remote_intent"]["intent"], status="complete"))], receipt
        # The receipt write of 0.91.7: the same operation, payload and request ID, at the revision of the store.
        prepared_mutation(self.store, "remote.finish", {"id": "intent", "kind": "fixture_receipt", "receipt": receipt,
                                                        "workspace": None}, prepare,
                          expected_revision=self.store.revision, request_id="intent:finish:" + digest(receipt))
        revision = self.store.revision
        self.assertEqual(remote.finish_intent(self.store, "intent", "fixture_receipt", receipt), receipt)
        self.assertEqual(self.store.revision, revision)

    def test_acquisition_that_an_earlier_release_finished_is_replayed_without_a_request(self):
        def finish_at_the_revision_of_the_store(store, request_id, *arguments, apply=None, **options):
            # The result write of 0.91.7: the payload names the revision that the write expected. The result is the
            # last positional argument of _finish in every release.
            return acquisition._finish_once(store, request_id, store.revision, arguments[-1], apply=apply)
        http, _, _ = client([xml_response(atom([entry()], total=1))])
        with mock.patch.object(acquisition, "_finish", finish_at_the_revision_of_the_store):
            first = acquisition.acquire_work(self.store, "2601.00001v1", request_id="acquire",
                                             expected_revision=self.store.revision, http=http)
        revision = self.store.revision
        again, wire, _ = client([])
        self.assertEqual(acquisition.acquire_work(self.store, "2601.00001v1", request_id="acquire",
                                                  expected_revision=revision, http=again), first)
        self.assertEqual((self.store.revision, len(wire.requests)), (revision, 0))


class FulltextJobWriteTests(PlainStoreCase):
    """The records of a full-text job: each unit of a transfer or an extraction, and the end of an invocation."""

    def setUp(self):
        super().setUp()
        ORIGINAL_MUTATE(self.store, "fixture.job", {}, lambda tx: tx.put("fulltext_job", "job", {"id": "job", "units": 0}),
                        expected_revision=0, request_id="job")

    def commit_unit(self):
        def apply(transaction, job, revision):
            transaction.put("fulltext_job", "job", dict(job, units=job["units"] + 1))
            return {"units": job["units"] + 1}
        return fulltext_jobs.commit_job(self.store, "job", "fixture.unit", {"unit": 1}, "unit-1", apply)

    def refuse_as_busy(self):
        raise ResearchError("store_busy", "Research store is busy; retry the same request after the writer finishes")

    def test_unit_is_recorded_when_the_store_is_busy_for_nine_attempts(self):
        self.meet_commits(("fixture.unit",), act=self.refuse_as_busy, limit=9)
        self.assertEqual(self.commit_unit(), {"units": 1})
        self.assertEqual(self.attempts, {"fixture.unit": 10})

    def test_unit_refused_beyond_the_wait_says_how_the_job_goes_on(self):
        refusal = self.refuse_beyond_the_wait("fixture.unit", self.commit_unit)
        self.assertEqual((refusal.code, refusal.message), ("store_busy", fulltext_jobs.FULLTEXT_JOB_STORE_BUSY_MSG))
        self.assertEqual(self.records()["fulltext_job"]["job"]["units"], 0)
        self.assertEqual(self.commit_unit(), {"units": 1})


class SharedWaitTests(PlainStoreCase):
    """Every finishing write waits as the finish of a review call does."""

    def test_finishing_writes_use_the_one_wait(self):
        calls = []
        retry = finishing.retry_store_write

        def record(write, **options):
            calls.append((options.get("attempts", 8), options.get("seconds", 8.0), tuple(options.get("codes", ("store_busy", "stale_revision")))))
            return retry(write, **options)
        shared = (finishing.SCOPED_FINISH_ATTEMPT_COUNT, finishing.SCOPED_FINISH_WAIT_SECONDS, finishing.SCOPED_FINISH_RETRY_CODES)
        with mock.patch.object(finishing, "retry_store_write", record), \
                mock.patch.object(remote, "retry_store_write", record, create=True), \
                mock.patch.object(acquisition, "retry_store_write", record, create=True), \
                mock.patch.object(submission, "retry_store_write", record, create=True):
            remote.begin_intent(self.store, "fixture", {"id": "item"}, expected_revision=0, request_id="intent")
            remote.remote_step(self.store, "intent", "create", {}, lambda: {"id": "confirmed"})
            remote.finish_intent(self.store, "intent", "fixture_receipt", {"id": "confirmed"})
            submission._observation(self.store, "intent", "matched", {"verificationId": "confirmed"})
            http, _, _ = client([xml_response(atom([entry()], total=1))])
            acquisition.acquire_work(self.store, "2601.00001v1", request_id="acquire",
                                     expected_revision=self.store.revision, http=http)
        self.assertEqual(len(calls), 5)
        self.assertEqual(set(calls), {shared})


if __name__ == "__main__":
    unittest.main()
