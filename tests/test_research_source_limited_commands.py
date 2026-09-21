"""Strict publication entrypoints must fail before any remote write."""

import contextlib
import io
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from test_transport import _load_bin_module
from source_limited_fixtures import SourceLimitedCase


class SourceLimitedCommandTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.addCleanup(os.chdir, os.getcwd())
        os.chdir(self.root)
        self.deposit = _load_bin_module("exactory-draft", "strict_draft")
        self.submit = _load_bin_module("exactory", "strict_submit")
        self.calls = []

    def prepare_files(self):
        (self.root / ".exactory").mkdir(exist_ok=True)
        (self.root / ".exactory/draft.json").write_text(json.dumps({"version": 1, "title": "Finite fixture", "corpus": "arxiv",
                                                                "category": "cs.MA", "created": "2026-01-01T00:00:00Z"}))
        (self.root / "paper.pdf").write_bytes(b"%PDF-1.4\nFixture")
        (self.root / "abstract.txt").write_text("A bounded fixture.")

    def remote(self, method, path, *args, **kwargs):
        self.calls.append((method, path))
        return {"verificationId": "fixture"}, 201

    def invoke(self, module, arguments):
        stdout, stderr = io.StringIO(), io.StringIO()
        with patch.object(sys, "argv", [module.__name__] + arguments), contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            with self.assertRaises(SystemExit) as stopped:
                module.main()
        self.assertEqual(stopped.exception.code, 1, stderr.getvalue())
        self.assertEqual(self.calls, [])
        return stderr.getvalue()

    def test_strict_submission_without_workspace_has_native_failure_and_zero_writes(self):
        with patch.object(self.submit, "_send_request", self.remote):
            error = self.invoke(self.submit, ["submit", "--doi", "10.5281/zenodo.100", "--managed-only",
                                             "--scope-contract-id", "scope", "--scope-target-digest", "a" * 64])
        self.assertIn("managed_workspace_required", error)

    def test_strict_deposit_without_store_has_native_failure_and_zero_writes(self):
        self.prepare_files()
        with patch.object(self.deposit, "_call_zenodo", self.remote), patch.object(self.deposit, "_read_zenodo_token", return_value="fixture-token"):
            error = self.invoke(self.deposit, ["deposit", "--pdf", "paper.pdf", "--abstract-file", "abstract.txt", "--creator", "Fixture",
                "--managed-only", "--scope-contract-id", "scope", "--scope-target-digest", "a" * 64])
        self.assertIn("migration_required", error)

    def test_scope_arguments_require_strict_mode_and_each_other(self):
        with patch.object(self.submit, "_send_request", self.remote):
            for flags in (["--scope-contract-id", "scope"], ["--managed-only", "--scope-contract-id", "scope"]):
                with self.subTest(flags=flags):
                    error = self.invoke(self.submit, ["submit", "--doi", "10.5281/zenodo.100"] + flags)
                    self.assertIn("managed_scope_arguments", error)

    def test_both_strict_commands_reject_missing_native_configuration(self):
        from research_harness.storage import Store
        self.prepare_files()
        Store(self.root, create=True)
        flags = ["--managed-only", "--scope-contract-id", "scope", "--scope-target-digest", "a" * 64]
        with patch.object(self.deposit, "_call_zenodo", self.remote), patch.object(self.submit, "_send_request", self.remote):
            for module, args in ((self.deposit, ["deposit", "--creator", "Fixture", "--abstract-file", "abstract.txt"]),
                                 (self.submit, ["submit", "--doi", "10.5281/zenodo.100"])):
                self.assertIn("migration_required", self.invoke(module, args + flags))

    def test_corrupt_native_store_never_falls_through_to_submission(self):
        self.prepare_files()
        (self.root / ".exactory/research.sqlite3").write_bytes(b"corrupt native state")
        with patch.object(self.submit, "_send_request", self.remote):
            error = self.invoke(self.submit, ["submit", "--doi", "10.5281/zenodo.100", "--managed-only",
                                             "--scope-contract-id", "scope", "--scope-target-digest", "a" * 64])
        self.assertIn("corrupt_state", error)


class SourceLimitedLifecycleTests(SourceLimitedCase):
    def setUp(self):
        super().setUp()
        self.addCleanup(os.chdir, os.getcwd())
        os.chdir(self.root)
        self.deposit_command = _load_bin_module("exactory-draft", "scope_deposit")
        self.submit_command = _load_bin_module("exactory", "scope_submit")
        self.writes = []

    def prepare_publication(self):
        from integration_fixtures import approve_publication_stop, build_manuscript_review
        from research_harness.publication import record_manuscript_review
        self.prepare_source_limited()
        self.contract = self.record_scope()
        self.accept_scope()
        self.bundle = self.scoped_manuscript()
        for actor in ("manuscript-reviewer-one", "manuscript-reviewer-two"):
            self.mutate(record_manuscript_review, build_manuscript_review(self, self.bundle, actor))
        approve_publication_stop(self, self.bundle)
        (self.root / ".exactory/draft.json").write_text(json.dumps({"version": 1, "title": "Finite fixture", "corpus": "arxiv",
                                                                "category": "cs.MA", "created": "2026-01-01T00:00:00Z"}))
        self.flags = ["--managed-only", "--scope-contract-id", self.contract["id"],
                      "--scope-target-digest", self.contract["scientific_target_digest"]]

    def zenodo(self, method, url, *args, **kwargs):
        if method != "GET":
            self.writes.append(("zenodo", method, url))
        if method == "POST" and url.endswith("/deposit/depositions"):
            return {"id": 91, "links": {"bucket": "https://zenodo.org/api/files/test", "html": "https://zenodo.org/deposit/91"}}
        if url.endswith("/actions/publish"):
            return {"id": 91, "doi": "10.5281/zenodo.91", "conceptdoi": "10.5281/zenodo.90",
                    "links": {"record_html": "https://zenodo.org/records/91"}}
        return {}

    def server(self, method, path, *args, **kwargs):
        if method != "GET":
            self.writes.append(("server", method, path))
            return {"verificationId": "test-verification"}, 201
        return {"verificationId": "test-verification", "doi": "10.5281/zenodo.90",
                "source": "zenodo", "sourceId": "91", "sourceVersion": None, "url": "https://zenodo.org/records/91"}, 200

    def invoke(self, command, arguments, error=None):
        stdout, stderr = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr), patch.object(sys, "argv", ["fixture"] + arguments):
            if error is None:
                command.main()
            else:
                before = list(self.writes)
                with self.assertRaises(SystemExit) as stopped:
                    command.main()
                self.assertEqual(stopped.exception.code, 1, stderr.getvalue())
                self.assertIn(error, stderr.getvalue())
                self.assertEqual(before, self.writes)
        return stdout.getvalue()

    def deposit_args(self):
        return ["deposit", "--production", "--publish", "--confirm-publish", "--pdf", "draft/paper.pdf",
                "--abstract-file", "draft/abstract.txt", "--creator", "Fixture"] + self.flags

    def test_actual_strict_publication_and_submission_preserve_receipts_after_source_recovery(self):
        from research_harness.publication import publication_report
        self.prepare_publication()
        with patch.object(self.deposit_command, "_call_zenodo", self.zenodo), \
                patch.object(self.deposit_command, "_read_zenodo_token", return_value="synthetic-token"), \
                patch.object(self.submit_command, "_send_request", self.server):
            self.invoke(self.deposit_command, self.deposit_args())
            self.assertTrue(self.writes)
            self.invoke(self.submit_command, ["submit", "--doi", "10.5281/zenodo.91"] + self.flags)
            records = self.store.snapshot()["records"]
            deposit = next(iter(records["publication_receipt"].values()))
            submit = next(iter(records["submission_receipt"].values()))
            self.assertEqual(deposit["publication_scope"]["contract_id"], self.contract["id"])
            self.assertEqual(submit["publication_scope"], deposit["publication_scope"])
            self.assertFalse(deposit["publication_scope"]["objective_complete"])
            self.mutate(self.get_operation("resume-source"), {"id": "source-recovered", "profile": "research", "version_id": self.gap,
                "deferral_id": self.deferral["id"], "reason": "The missing external comparison can now be acquired."})
            self.invoke(self.deposit_command, self.deposit_args(), "manuscript_readiness_required")
            self.invoke(self.submit_command, ["submit", "--doi", "10.5281/zenodo.91"] + self.flags, "manuscript_readiness_required")
            current = publication_report(self.store)
            self.assertFalse(current["ready"])
            self.assertEqual(current["historical_publications"], [deposit])
            self.assertEqual(current["historical_submissions"], [submit])

    def test_actual_entrypoints_reject_wrong_target_changed_bundle_rejection_and_missing_scope(self):
        self.prepare_publication()
        deposit_args = self.deposit_args()
        submit_args = ["submit", "--doi", "10.5281/zenodo.91"] + self.flags
        with patch.object(self.deposit_command, "_call_zenodo", self.zenodo), \
                patch.object(self.deposit_command, "_read_zenodo_token", return_value="synthetic-token"), \
                patch.object(self.submit_command, "_send_request", self.server):
            for command, arguments in ((self.deposit_command, deposit_args), (self.submit_command, submit_args)):
                invalid = list(arguments)
                invalid[-1] = "0" * 64
                self.invoke(command, invalid, "publication_scope_mismatch")
            (self.root / "draft/paper.pdf").write_bytes(b"%PDF-1.4 changed")
            for command, arguments in ((self.deposit_command, deposit_args), (self.submit_command, submit_args)):
                self.invoke(command, arguments, "readiness_required")
            self.mutate(self.scope_api().record_scoped_readiness_review, self.scope_review("negative", verdict="not_ready", assessor="later-scoped-reviewer"))
            for command, arguments in ((self.deposit_command, deposit_args), (self.submit_command, submit_args)):
                self.invoke(command, arguments, "manuscript_readiness_required")
            self.mutate(self.scope_api().select_publication_scope, {"id": None})
            for command, arguments in ((self.deposit_command, deposit_args), (self.submit_command, submit_args)):
                self.invoke(command, arguments, "publication_scope_missing")
        self.assertEqual(self.writes, [])
        self.assertFalse(self.store.snapshot()["records"].get("publication_receipt"))

    def recover_source(self):
        self.mutate(self.get_operation("resume-source"), {"id": "recovered-during-command", "profile": "research",
            "version_id": self.gap, "deferral_id": self.deferral["id"], "reason": "The required comparison became available."})

    def test_deposit_gate_revision_is_bound_to_remote_claim(self):
        from research_harness import zenodo
        self.prepare_publication()
        original = zenodo.remote_step
        def invalidate(store, identifier, name, request, perform, **kwargs):
            if name == "create":
                self.recover_source()
            return original(store, identifier, name, request, perform, **kwargs)
        with patch.object(zenodo, "remote_step", invalidate), patch.object(self.deposit_command, "_call_zenodo", self.zenodo), \
                patch.object(self.deposit_command, "_read_zenodo_token", return_value="synthetic-token"):
            self.invoke(self.deposit_command, self.deposit_args(), "stale_revision")
        self.assertEqual(self.writes, [])

    def test_submit_gate_revision_is_bound_to_remote_claim(self):
        from research_harness import submission
        self.prepare_publication()
        with patch.object(self.deposit_command, "_call_zenodo", self.zenodo), \
                patch.object(self.deposit_command, "_read_zenodo_token", return_value="synthetic-token"):
            self.invoke(self.deposit_command, self.deposit_args())
        self.writes.clear()
        original = submission.remote_step
        def invalidate(store, identifier, name, request, perform, **kwargs):
            self.recover_source()
            return original(store, identifier, name, request, perform, **kwargs)
        with patch.object(submission, "remote_step", invalidate), patch.object(self.submit_command, "_send_request", self.server):
            self.invoke(self.submit_command, ["submit", "--doi", "10.5281/zenodo.91"] + self.flags, "stale_revision")
        self.assertEqual(self.writes, [])

    def test_caller_target_cannot_switch_between_preflight_and_intent(self):
        from integration_fixtures import approve_publication_stop, build_manuscript_review
        from research_harness.publication import record_manuscript_review
        from research_harness import publication_scope
        self.prepare_publication()
        original = publication_scope.validate_expected_scope
        def change_target(store, contract_id, target_digest):
            original(store, contract_id, target_digest)
            payload = self.scope_payload("other-target")
            payload["supported_claims"][0]["statement"] = "The finite test reports values 0, 1, 4, and 9."
            self.record_scope(payload)
            self.mutate(self.scope_api().record_scoped_readiness_review, self.scope_review("other-review", assessor="other-reviewer"))
            bundle = self.scoped_manuscript("other-manuscript")
            for actor in ("other-reviewer-one", "other-reviewer-two"):
                self.mutate(record_manuscript_review, build_manuscript_review(self, bundle, actor))
            approve_publication_stop(self, bundle)
        with patch.object(publication_scope, "validate_expected_scope", change_target), \
                patch.object(self.deposit_command, "_call_zenodo", self.zenodo), \
                patch.object(self.deposit_command, "_read_zenodo_token", return_value="synthetic-token"):
            self.invoke(self.deposit_command, self.deposit_args(), "publication_scope_mismatch")
        self.assertEqual(self.writes, [])
