"""Pinned task identity and exact local verdict assessments."""

import copy
import importlib
import json
from pathlib import Path
import threading
from unittest import mock

from test_research_synthesis import SynthesisCase


class ResearchVerificationTests(SynthesisCase):
    def setUp(self):
        super().setUp()
        work = self.configured("verification")
        self.linked = self.read_source()
        self.complete_foundation(work, "verification")
        self.mutate(self.api().record_standards, self.standards(self.linked, "verification"))
        self.task = {"verificationId": "11111111-1111-4111-8111-111111111111", "doi": "10.48550/arxiv.2601.00001",
                     "source": "arxiv", "sourceId": "2601.00001", "sourceVersion": 1,
                     "url": "https://arxiv.org/abs/2601.00001v1", "requestedByViewer": True, "viewerVerdictId": None}

    def verifier(self):
        self.assertTrue((Path(__file__).parents[1] / "research_harness/verification.py").is_file(), "Exact verification binding is missing")
        return importlib.import_module("research_harness.verification")

    def verdict(self):
        return {"stance": "sound", "summary": "The finite bound is supported.", "rationaleSections": [],
                "prediction": {"corpus": "arxiv", "category": "cs.LG", "windowStart": "2026-01-01", "windowEnd": "2026-01-31", "percentile": 50, "band": {"best": 35, "worst": 65}}}

    def bind(self, api):
        task = self.mutate(api.record_task, {"task": self.task})["result"]
        payload = {"id": "verdict-1", "task_digest": task["digest"],
                   "body": self.artifacts.put(json.dumps(self.verdict()).encode(), "application/json"),
                   "assessment": {"assessor": "independent-verifier", "provenance": self.artifacts.put(b"Authored separate verification context.", "text/plain"),
                       "independence_basis": "The verifier is not an author and read no other verdicts.", "blind": True,
                       "checks": [{"dimension": dimension, "reason": "The scoped source supports this separate assessment.", "evidence": [self.linked]}
                                  for dimension in ("soundness", "novelty", "impact")]}}
        return self.mutate(api.bind_verdict, payload)["result"]

    def test_request_creator_can_bind_but_wrong_version_cannot_mutate(self):
        api = self.verifier()
        self.mutate(api.record_task, {"task": self.task})
        before = self.store.snapshot()
        wrong = dict(self.task, sourceVersion=2, url="https://arxiv.org/abs/2601.00001v2")
        self.assert_error("verification_target_mismatch", lambda: self.mutate(api.record_task, {"task": wrong}))
        self.assertEqual(before, self.store.snapshot())

    def test_exact_verdict_body_and_task_are_checked_before_post(self):
        api = self.verifier()
        self.bind(api)
        self.assertTrue(api.validate_verdict(self.store, self.task, self.verdict())["ready"])
        changed = dict(self.verdict(), summary="A different conclusion.")
        self.assert_error("verdict_body_mismatch", lambda: api.validate_verdict(self.store, self.task, changed))
        self.assert_error("verification_target_mismatch", lambda: api.validate_verdict(self.store, dict(self.task, sourceVersion=None), self.verdict()))

    def test_same_doi_with_different_original_bytes_invalidates_verdict(self):
        api = self.verifier()
        self.bind(api)
        target = self.store.snapshot()["records"]["configuration"]["research"]["target"]
        original = self.store.snapshot()["records"]["source"][target["source_id"]]["response"]
        path = self.root / original["path"]
        path.chmod(0o600)
        path.write_bytes(b"Changed original under the same source identity.")
        self.assert_error("readiness_required", lambda: api.validate_verdict(self.store, self.task, self.verdict()))

    def test_unknown_verdict_response_retains_intent_and_refuses_repeated_post(self):
        from research_harness.submission import send_verdict
        api = self.verifier()
        bound = self.bind(api)
        calls = []
        def client(method, path, body=None):
            calls.append((method, path, body))
            raise OSError("Remote response lost after acceptance.")
        with self.assertRaises(OSError):
            send_verdict(self.store, self.task, self.verdict(), client, expected_revision=self.store.revision, request_id="send-1")
        observed = dict(self.task, viewerVerdictId="22222222-2222-4222-8222-222222222222")
        self.assert_error("verdict_reconciliation_pending", lambda: send_verdict(self.store, observed, self.verdict(), client,
            expected_revision=self.store.revision, request_id="attempt-2"))
        revised = dict(self.verdict(), summary="A separately assessed revision cannot resolve an unknown earlier body.",
                       supersedesVerdictId=observed["viewerVerdictId"])
        payload = {k: copy.deepcopy(bound[k]) for k in ("id", "task_digest", "body", "assessment")}
        payload.update(id="assessment-after-unknown", body=self.artifacts.put(json.dumps(revised).encode(), "application/json"))
        self.mutate(api.bind_verdict, payload)
        self.assert_error("verdict_reconciliation_pending", lambda: send_verdict(self.store, observed, revised, client,
            expected_revision=self.store.revision, request_id="attempt-different-assessment"))
        self.assertEqual(len(calls), 1)
        records = self.store.snapshot()["records"]
        self.assertEqual(records["remote_intent"]["send-1"]["status"], "in_flight")
        self.assertTrue(records["remote_observation"])
        self.assertNotIn("verdict_receipt", records)

    def test_concurrent_verdict_assessments_cannot_create_two_posts_for_one_target(self):
        from research_harness import submission
        from research_harness.errors import ResearchError
        api = self.verifier()
        bound = self.bind(api)
        prepared, release = threading.Event(), threading.Event()
        calls, results, errors = [], [], []
        original = submission.begin_intent
        def hold_first(*args, **kwargs):
            intent = original(*args, **kwargs)
            if kwargs["request_id"] == "concurrent-first":
                prepared.set()
                if not release.wait(10):
                    raise OSError("The test did not release its prepared first caller")
            return intent
        def client(method, path, body=None):
            calls.append((method, path, body))
            return {"id": "22222222-2222-4222-8222-222222222222"}, 201
        first_revision = self.store.revision
        def first():
            try:
                results.append(submission.send_verdict(self.store, self.task, self.verdict(), client,
                    expected_revision=first_revision, request_id="concurrent-first"))
            except BaseException as error:
                errors.append(error)
        with mock.patch.object(submission, "begin_intent", side_effect=hold_first):
            thread = threading.Thread(target=first)
            thread.start()
            try:
                self.assertTrue(prepared.wait(10))
                # A second caller legitimately observes the newer revision and
                # binds another assessment while the first intent is prepared.
                payload = {k: copy.deepcopy(bound[k]) for k in ("id", "task_digest", "body", "assessment")}
                payload["id"] = "concurrent-second-assessment"
                self.mutate(api.bind_verdict, payload)
                before = self.store.snapshot()
                second_error = None
                try:
                    submission.send_verdict(self.store, self.task, self.verdict(), client,
                        expected_revision=self.store.revision, request_id="concurrent-second")
                except ResearchError as error:
                    second_error = error
                after_second = self.store.snapshot()
            finally:
                release.set()
                thread.join(10)
            self.assertFalse(thread.is_alive())
        self.assertEqual(errors, [])
        self.assertEqual(len(results), 1)
        self.assertEqual(len(calls), 1, calls)
        self.assertIsNotNone(second_error, "Two callers prepared distinct verdict intents for one target")
        self.assertEqual(second_error.code, "remote_operation_owned")
        self.assertEqual(after_second, before)
        self.assertEqual(set(self.store.snapshot()["records"]["remote_intent"]), {"concurrent-first"})

    def test_another_verification_owner_does_not_block_this_exact_target(self):
        from research_harness.submission import send_verdict
        from research_harness.execution import _owner
        self.bind(self.verifier())
        calls = []
        def client(method, path, body=None):
            calls.append((method, path, body))
            return {"id": "22222222-2222-4222-8222-222222222222"}, 201
        with _owner(self.store.root, "verdict:33333333-3333-4333-8333-333333333333"):
            receipt = send_verdict(self.store, self.task, self.verdict(), client,
                expected_revision=self.store.revision, request_id="different-target")
        self.assertEqual(len(calls), 1)
        self.assertEqual(receipt["task"]["verificationId"], self.task["verificationId"])

    def test_known_success_replay_and_explicit_revision_preserve_the_wire_body(self):
        from research_harness.submission import send_verdict
        api = self.verifier()
        bound = self.bind(api)
        calls = []
        def client(method, path, body=None):
            calls.append((method, path, body))
            return {"id": "22222222-2222-4222-8222-222222222222", "summary": body["summary"]}, 201
        body = self.verdict()
        first = send_verdict(self.store, self.task, body, client, expected_revision=self.store.revision, request_id="send-1")
        observed = dict(self.task, viewerVerdictId=first["response"]["id"])
        repeated = send_verdict(self.store, observed, body, client, expected_revision=self.store.revision, request_id="replay-send")
        self.assertEqual(first, repeated)
        revised = dict(body, summary="The revised bound statement has narrower scope.", supersedesVerdictId=observed["viewerVerdictId"])
        payload = {k: copy.deepcopy(bound[k]) for k in ("id", "task_digest", "body", "assessment")}
        payload["id"] = "verdict-revision"
        payload["body"] = self.artifacts.put(json.dumps(revised).encode(), "application/json")
        self.mutate(api.bind_verdict, payload)
        send_verdict(self.store, observed, revised, client, expected_revision=self.store.revision, request_id="send-revision")
        self.assertEqual([c[2] for c in calls], [body, revised])

    def test_stale_task_does_not_erase_confirmed_verdict_revision_history(self):
        from research_harness.submission import send_verdict
        api = self.verifier()
        bound = self.bind(api)
        calls = []
        identifiers = ["22222222-2222-4222-8222-222222222222", "33333333-3333-4333-8333-333333333333",
                       "44444444-4444-4444-8444-444444444444"]
        def client(method, path, body=None):
            calls.append(body)
            return {"id": identifiers[len(calls) - 1]}, 201
        def assess(identifier, body):
            payload = {k: copy.deepcopy(bound[k]) for k in ("id", "task_digest", "body", "assessment")}
            payload.update(id=identifier, body=self.artifacts.put(json.dumps(body).encode(), "application/json"))
            self.mutate(api.bind_verdict, payload)
        body = self.verdict()
        send_verdict(self.store, self.task, body, client, expected_revision=self.store.revision, request_id="confirmed-first")
        bare = dict(body, summary="A new assessment still needs an explicit predecessor.")
        assess("bare-assessment", bare)
        self.assert_error("verdict_revision_required", lambda: send_verdict(self.store, self.task, bare, client,
            expected_revision=self.store.revision, request_id="stale-without-predecessor"))
        revised = dict(bare, supersedesVerdictId=identifiers[0])
        assess("explicit-second", revised)
        send_verdict(self.store, self.task, revised, client, expected_revision=self.store.revision, request_id="confirmed-second")
        stale = dict(self.task, viewerVerdictId=identifiers[0])
        third = dict(revised, summary="The next revision must name the latest confirmed predecessor.")
        assess("old-predecessor", third)
        self.assert_error("verdict_revision_required", lambda: send_verdict(self.store, stale, third, client,
            expected_revision=self.store.revision, request_id="stale-predecessor"))
        third["supersedesVerdictId"] = identifiers[1]
        assess("explicit-third", third)
        send_verdict(self.store, stale, third, client, expected_revision=self.store.revision, request_id="confirmed-third")
        self.assertEqual(calls, [body, revised, third])
