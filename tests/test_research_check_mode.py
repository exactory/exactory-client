"""Mutation checks share validation while leaving all persistent bytes unchanged."""

from contextlib import redirect_stderr, redirect_stdout
import io
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

from research_harness import cli, integration, operations, storage
from research_harness.artifacts import ArtifactStore
from research_harness.storage import Store
from development_fixtures import DevelopmentCase


PLUGIN = Path(__file__).resolve().parents[1]


class CheckCommandMixin:
    def invoke_command(self, command, payload, *, check=True, revision=None, request="check-request"):
        payload_path = self.root.parent / "check-input.json"
        payload_path.write_text(json.dumps(payload))
        arguments = [command, "--workspace", str(self.root), "--file", str(payload_path),
                     "--expected-revision", str(self.store.revision if revision is None else revision),
                     "--request-id", request]
        if check:
            arguments.append("--check")
        return self.invoke_arguments(arguments)

    def invoke_arguments(self, arguments):
        stdout, stderr = io.StringIO(), io.StringIO()
        with redirect_stdout(stdout), redirect_stderr(stderr):
            try:
                cli.main(arguments)
            except SystemExit as error:
                return error.code, stdout.getvalue(), stderr.getvalue()
        return 0, stdout.getvalue(), stderr.getvalue()

    def read_workspace(self):
        values = {}
        for path in sorted((self.root, *self.root.rglob("*"))):
            info = path.lstat()
            content = os.readlink(path) if path.is_symlink() else path.read_bytes() if path.is_file() else None
            values[str(path.relative_to(self.root))] = (info.st_mode, info.st_mtime_ns, content)
        return values

    def assert_accepted_check(self, response, revision):
        self.assertEqual(response[0], 0, response[2])
        diagnostic = json.loads(response[1])
        self.assertEqual(diagnostic["check"], "accepted")
        self.assertEqual(diagnostic["revision"], revision)
        self.assertIs(diagnostic["recorded"], False)
        self.assertNotIn("request_id", diagnostic)
        self.assertNotIn("result", diagnostic)
        return diagnostic

    def assert_refusal_parity(self, command, payload, code, **identity):
        before = self.read_workspace()
        checked = self.invoke_command(command, payload, **identity)
        self.assertEqual(checked[0], 1, checked[2])
        error = json.loads(checked[2])["error"]
        self.assertEqual(error["code"], code)
        self.assertEqual(self.read_workspace(), before)
        written = self.invoke_command(command, payload, check=False, **identity)
        self.assertEqual(written[0], 1, written[2])
        self.assertEqual(json.loads(written[2])["error"], error)


class ResearchCheckModeTests(CheckCommandMixin, unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name) / "study"
        self.store = Store(self.root, create=True)
        (self.root / "authored.txt").write_bytes(b"Authored local bytes.\n")
        self.payload = {"id": "authored", "path": "authored.txt", "media_type": "text/plain"}

    def test_check_accepts_without_artifacts_receipts_or_request_reservation(self):
        before = self.read_workspace()
        for _ in range(2):
            diagnostic = self.assert_accepted_check(self.invoke_command("artifact", self.payload), 0)
            self.assertEqual(diagnostic["operation"], "workspace.artifact")
            self.assertEqual(len(diagnostic["payload_digest"]), 64)
            self.assertEqual(len(diagnostic["snapshot_digest"]), 64)
            self.assertEqual(self.read_workspace(), before)
        written = self.invoke_command("artifact", self.payload, check=False)
        self.assertEqual(written[0], 0, written[2])
        self.assertEqual(json.loads(written[1])["revision"], 1)
        self.assertTrue((self.root / "research/sources/objects").is_dir())

    def test_check_reports_the_same_missing_field_refusal(self):
        self.assert_refusal_parity("artifact", {"id": "authored"}, "invalid_input")

    def test_check_reports_the_same_missing_artifact_refusal(self):
        self.assert_refusal_parity("artifact", dict(self.payload, path="missing.txt"), "artifact_missing")

    def test_check_reports_the_same_immutable_record_conflict_after_staging(self):
        integration.pin_artifact(self.store, self.payload, expected_revision=0, request_id="original")
        (self.root / "authored.txt").write_bytes(b"Different authored bytes.\n")
        self.assert_refusal_parity("artifact", self.payload, "record_conflict")

    def test_check_reports_the_same_stale_revision_before_reading_source(self):
        self.assert_refusal_parity("artifact", dict(self.payload, path="missing"), "stale_revision", revision=9)

    def test_check_reports_the_same_command_input_bound_before_domain_validation(self):
        payload = {"value": 0}
        for _ in range(32):
            payload = {"value": payload}
        self.assert_refusal_parity("artifact", payload, "invalid_input")

    def test_check_replays_original_receipt_after_source_removal_and_later_writes(self):
        receipt = integration.pin_artifact(self.store, self.payload, expected_revision=0, request_id="original")
        (self.root / "authored.txt").unlink()
        self.store.mutate("fixture.later", {}, lambda tx: None, expected_revision=1, request_id="later")
        before = self.read_workspace()
        response = self.invoke_command("artifact", self.payload, revision=0, request="original")
        self.assertEqual(response[0], 0, response[2])
        self.assertEqual(json.loads(response[1]), receipt)
        self.assertEqual(self.read_workspace(), before)

    def test_check_replays_a_historical_payload_beyond_current_input_bounds(self):
        payload = dict(self.payload, historical={"value": 0})
        for _ in range(33):
            payload["historical"] = {"value": payload["historical"]}
        receipt = self.store.mutate("workspace.artifact", payload, lambda tx: {"historical": True},
                                    expected_revision=0, request_id="historical")
        before = self.read_workspace()
        response = self.invoke_command("artifact", payload, revision=0, request="historical")
        self.assertEqual(response[0], 0, response[2])
        self.assertEqual(json.loads(response[1]), receipt)
        self.assertEqual(self.read_workspace(), before)

    def test_check_preserves_request_conflict_before_stale_revision(self):
        integration.pin_artifact(self.store, self.payload, expected_revision=0, request_id="original")
        self.assert_refusal_parity("artifact", dict(self.payload, path="missing"), "request_id_conflict",
                                   revision=0, request="original")
        self.assert_refusal_parity("cycle", self.payload, "request_id_conflict", revision=0, request="original")

    def test_check_authenticates_exactly_one_snapshot_and_never_opens_a_writer(self):
        validate = storage._validate
        validations = []
        connection = Store._connection

        def record_validation(*args, **kwargs):
            validations.append(kwargs.get("replay", True))
            return validate(*args, **kwargs)

        def open_reader(store, *, writable=False):
            self.assertFalse(writable)
            return connection(store, writable=writable)

        with mock.patch.object(storage, "_validate", side_effect=record_validation), \
                mock.patch.object(Store, "_connection", open_reader), \
                mock.patch.object(Store, "mutate", side_effect=AssertionError("Check reached a writer")):
            response = self.invoke_command("artifact", self.payload, revision=0)
        self.assert_accepted_check(response, 0)
        self.assertEqual(validations.count(True), 1)

    def test_check_detects_same_revision_record_tampering(self):
        self.store.mutate("fixture.seed", {}, lambda tx: tx.put("fixture", "one", {"value": 1}),
                          expected_revision=0, request_id="seed")
        database = self.root / ".exactory/research.sqlite3"
        with sqlite3.connect(database) as connection:
            connection.execute("UPDATE records SET value = '{}' WHERE kind = 'fixture'")
        self.assert_refusal_parity("artifact", self.payload, "corrupt_state", revision=1)

    def test_check_preserves_request_validation_before_history_authentication(self):
        self.store.mutate("fixture.seed", {}, lambda tx: tx.put("fixture", "one", {"value": 1}),
                          expected_revision=0, request_id="seed")
        with sqlite3.connect(self.root / ".exactory/research.sqlite3") as connection:
            connection.execute("UPDATE records SET value = '{}' WHERE kind = 'fixture'")
        self.assert_refusal_parity("budget", {}, "invalid_input", request="", revision=1)

    def test_check_preserves_payload_validation_before_history_authentication(self):
        self.store.mutate("fixture.seed", {}, lambda tx: tx.put("fixture", "one", {"value": 1}),
                          expected_revision=0, request_id="seed")
        with sqlite3.connect(self.root / ".exactory/research.sqlite3") as connection:
            connection.execute("UPDATE records SET value = '{}' WHERE kind = 'fixture'")
        self.assert_refusal_parity("budget", [], "invalid_input", revision=1)

    def test_check_preserves_empty_request_refusal_on_a_payload_beyond_input_bounds(self):
        payload = {"value": 0}
        for _ in range(32):
            payload = {"value": payload}
        self.assert_refusal_parity("budget", payload, "invalid_input", request="")

    def test_check_rejects_a_corrupt_existing_object_without_overwriting_it(self):
        reference = ArtifactStore(self.root).put(b"Authored local bytes.\n", "text/plain")
        path = self.root / reference["path"]
        path.chmod(0o600)
        path.write_bytes(b"Corrupted immutable object")
        self.assert_refusal_parity("artifact", self.payload, "artifact_corrupt")

    def test_check_rejects_symlinked_object_directory_without_publishing(self):
        outside = self.root.parent / "outside"
        outside.mkdir()
        directory = self.root / "research/sources/objects"
        directory.parent.mkdir(parents=True)
        directory.symlink_to(outside, target_is_directory=True)
        self.assert_refusal_parity("artifact", self.payload, "unsafe_path")
        self.assertEqual(list(outside.iterdir()), [])

    def test_check_cannot_authorize_a_write_after_the_source_changes(self):
        self.assert_accepted_check(self.invoke_command("artifact", self.payload), 0)
        (self.root / "authored.txt").unlink()
        response = self.invoke_command("artifact", self.payload, check=False)
        self.assertEqual(response[0], 1, response[2])
        self.assertEqual(json.loads(response[2])["error"]["code"], "artifact_missing")
        self.assertEqual(self.store.revision, 0)

    def test_check_refuses_acquisition_and_reviewer_invocation_without_effects(self):
        before = self.read_workspace()
        for command in (*cli.ACQUISITION, "review-probe", "review-run", "review-output-correction", "reconcile-run"):
            with self.subTest(command=command):
                response = self.invoke_command(command, {})
                self.assertEqual(response[0], 1, response[2])
                self.assertEqual(json.loads(response[2])["error"]["code"], "check_unsupported")
                self.assertEqual(self.read_workspace(), before)

    def test_check_refuses_recovery_and_restore_without_effects(self):
        before = self.read_workspace()
        for arguments in (["recover"], ["restore", "--bundle", "0" * 64], ["export"],
                          ["batches", "--destination", str(self.root / "batches")]):
            response = self.invoke_arguments([*arguments, "--workspace", str(self.root), "--check"])
            self.assertEqual(response[0], 1, response[2])
            self.assertEqual(json.loads(response[2])["error"]["code"], "check_unsupported")
            self.assertEqual(self.read_workspace(), before)

    def test_check_refuses_local_runtime_inspection_without_starting_a_process(self):
        payload = {"id": "local-route", "adapter": "codex_cli_v1", "model": "fixture-model",
                   "endpoint": "codex://local", "configuration": {"max_output_tokens": 100, "timeout_seconds": 5}}
        before = self.read_workspace()
        with mock.patch("research_harness.review_codex_transport.shutil.which", return_value=sys.executable), \
                mock.patch("research_harness.review_codex_transport._run_owned",
                           side_effect=AssertionError("Check started a local runtime process")):
            response = self.invoke_command("review-route", payload)
        self.assertEqual(response[0], 1, response[2])
        self.assertEqual(json.loads(response[2])["error"]["code"], "check_unsupported")
        self.assertEqual(self.read_workspace(), before)

    def test_check_refuses_a_probe_pin_without_reserving_the_origin_request(self):
        launch = {"id": "probe-one", "directory": "research/probe-one", "output_dir": "outputs", "initial": {}}
        self.store.mutate("fixture.probe", {}, lambda tx: (
            tx.put("probe_intent", "probe-one", {}), tx.put("probe_launch", "probe-one", launch)),
            expected_revision=0, request_id="probe-launch")
        output = self.root / "research/probe-one/work/outputs/result.txt"
        output.parent.mkdir(parents=True)
        output.write_bytes(b"Captured probe result")
        payload = dict(self.payload, path="research/probe-one/work/outputs/result.txt")
        before = self.read_workspace()
        response = self.invoke_command("artifact", payload)
        self.assertEqual(response[0], 1, response[2])
        self.assertEqual(json.loads(response[2])["error"]["code"], "check_unsupported")
        self.assertEqual(self.read_workspace(), before)

    def test_check_of_initialization_does_not_create_a_missing_store(self):
        self.root = self.root.parent / "new-workspace"
        response = self.invoke_command("init", {"profile": "research", "target": None}, revision=0)
        self.assertEqual(response[0], 1, response[2])
        self.assertEqual(json.loads(response[2])["error"]["code"], "migration_required")
        self.assertFalse(self.root.exists())

    def test_check_of_initialization_stages_constitution_bytes_without_writing(self):
        before = self.read_workspace()
        payload = {"profile": "research", "target": None}
        self.assert_accepted_check(self.invoke_command("init", payload), 0)
        self.assertEqual(self.read_workspace(), before)
        response = self.invoke_command("init", payload, check=False)
        self.assertEqual(response[0], 0, response[2])

    def test_check_serves_newly_staged_bytes_to_later_validation(self):
        def record_fixture(store, payload, **identity):
            artifacts = store.build_artifact_store()

            def prepare(records, value):
                reference = artifacts.put(b"staged fixture", "text/plain")
                self.assertEqual(artifacts.read(reference), b"staged fixture")
                return [("fixture", "one", {"artifact": reference})], reference

            return operations.prepared_mutation(store, "fixture.stage", payload, prepare, **identity)

        before = self.read_workspace()
        with mock.patch.dict(cli.OPERATIONS, {"artifact": record_fixture}):
            self.assert_accepted_check(self.invoke_command("artifact", {}), 0)
        self.assertEqual(self.read_workspace(), before)

    def test_check_reports_the_digests_of_existing_artifact_inputs(self):
        reference = ArtifactStore(self.root).put(b"abc", "text/plain")

        def record_fixture(store, payload, **identity):
            artifacts = store.build_artifact_store()

            def prepare(records, value):
                artifacts.read(reference)
                return [], {}

            return operations.prepared_mutation(store, "fixture.read", payload, prepare, **identity)

        with mock.patch.dict(cli.OPERATIONS, {"artifact": record_fixture}):
            diagnostic = self.assert_accepted_check(self.invoke_command("artifact", {}), 0)
        self.assertEqual(diagnostic["artifact_digests"],
                         ["ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad"])

    def test_check_refuses_runtime_fingerprinting_before_review_state_validation(self):
        from research_harness import review_protocol
        route = {"id": "local-route", "adapter": "codex_cli_v1", "runtime": {}}
        self.store.mutate("fixture.route", {}, lambda tx: tx.put("review_route", "local-route", route),
                          expected_revision=0, request_id="route")

        def record_fixture(store, payload, **identity):
            artifacts = store.build_artifact_store()

            def prepare(records, value):
                return [], review_protocol.route_state(records, artifacts, "local-route")

            return operations.prepared_mutation(store, "fixture.review", payload, prepare, **identity)

        before = self.read_workspace()
        with mock.patch.dict(cli.OPERATIONS, {"artifact": record_fixture}), \
                mock.patch("research_harness.review_codex_transport.shutil.which", return_value=sys.executable), \
                mock.patch("research_harness.review_codex_transport._run_owned",
                           side_effect=AssertionError("Check started a local runtime process")):
            response = self.invoke_command("artifact", {})
        self.assertEqual(response[0], 1, response[2])
        self.assertEqual(json.loads(response[2])["error"]["code"], "check_unsupported")
        self.assertEqual(self.read_workspace(), before)

    def test_check_refuses_pdf_inspection_before_creating_temporary_files(self):
        from research_harness.source_links import read_locator
        reference = ArtifactStore(self.root).put(b"%PDF-1.4\nAuthored result", "application/pdf")

        def record_fixture(store, payload, **identity):
            artifacts = store.build_artifact_store()

            def prepare(records, value):
                read_locator(artifacts, reference, {"kind": "pdf", "page_index": 0, "printed_page": None,
                                                   "region": [0, 0, 1, 1]}, result=True)
                return [], {}

            return operations.prepared_mutation(store, "fixture.pdf", payload, prepare, **identity)

        before = self.read_workspace()
        with mock.patch.dict(cli.OPERATIONS, {"artifact": record_fixture}), \
                mock.patch("research_harness.source_links.shutil.which", return_value=sys.executable), \
                mock.patch("research_harness.source_links.run_bounded",
                           side_effect=AssertionError("Check started PDF inspection")):
            response = self.invoke_command("artifact", {})
        self.assertEqual(response[0], 1, response[2])
        self.assertEqual(json.loads(response[2])["error"]["code"], "check_unsupported")
        self.assertEqual(self.read_workspace(), before)

    def test_check_refuses_visual_delivery_before_inspecting_the_renderer(self):
        from research_harness.review_visual_sources import prepare_packet

        def record_fixture(store, payload, **identity):
            def prepare(records, value):
                return [], prepare_packet(records, store.build_artifact_store(), {})

            return operations.prepared_mutation(store, "fixture.visual", payload, prepare, **identity)

        before = self.read_workspace()
        with mock.patch.dict(cli.OPERATIONS, {"artifact": record_fixture}), \
                mock.patch("research_harness.review_pdf_pages.profile",
                           side_effect=AssertionError("Check inspected PDF renderer")):
            response = self.invoke_command("artifact", {})
        self.assertEqual(response[0], 1, response[2])
        self.assertEqual(json.loads(response[2])["error"]["code"], "check_unsupported")
        self.assertEqual(self.read_workspace(), before)

    def test_check_replays_a_recorded_acquisition_before_refusing_external_effects(self):
        payload = {"identifier": "arxiv:2601.00001v1", "provider": "arxiv", "max_requests": 0}
        receipt = self.store.mutate("work.acquire", payload, lambda tx: {"status": "admitted"},
                                    expected_revision=0, request_id="acquisition")
        before = self.read_workspace()
        response = self.invoke_command("acquire", {"identifier": payload["identifier"], "max_requests": 0},
                                       request="acquisition", revision=0)
        self.assertEqual(response[0], 0, response[2])
        self.assertEqual(json.loads(response[1]), receipt)
        self.assertEqual(self.read_workspace(), before)

    def test_check_preserves_a_recorded_acquisition_conflict_before_stale_revision(self):
        payload = {"identifier": "arxiv:2601.00001v1", "provider": "arxiv", "max_requests": 0}
        self.store.mutate("work.acquire", payload, lambda tx: {"status": "admitted"},
                          expected_revision=0, request_id="acquisition")
        self.assert_refusal_parity("acquire", {"identifier": "arxiv:2601.00002v1", "max_requests": 0},
                                   "request_id_conflict", request="acquisition", revision=0)

    def test_check_replays_a_recorded_reviewer_invocation_without_revalidating_sources(self):
        payload = {"id": "review", "assignment_id": "removed-assignment"}
        receipt = self.store.mutate("review.invoke", payload, lambda tx: tx.put(
            "literature_operation", "invocation", {"operation": "review.invoke"}),
            expected_revision=0, request_id="invocation")
        before = self.read_workspace()
        response = self.invoke_command("review-run", payload, request="invocation", revision=0)
        self.assertEqual(response[0], 0, response[2])
        self.assertEqual(json.loads(response[1]), receipt)
        self.assertEqual(self.read_workspace(), before)

    def test_check_replays_reconciliation_without_creating_owner_locks(self):
        payload = {"admission_id": "removed-admission"}
        receipt = self.store.mutate("execution.reconcile", payload, lambda tx: {"recorded": True},
                                    expected_revision=0, request_id="reconciliation")
        before = self.read_workspace()
        response = self.invoke_command("reconcile-run", payload, request="reconciliation", revision=0)
        self.assertEqual(response[0], 0, response[2])
        self.assertEqual(json.loads(response[1]), receipt)
        self.assertEqual(self.read_workspace(), before)

    def test_check_rejects_an_acquisition_id_before_preparing_a_reviewer_invocation(self):
        self.store.mutate("work.acquire", {}, lambda tx: {}, expected_revision=0, request_id="taken")
        for command, payload in (("review-probe", {"id": "probe", "route_id": "missing"}),
                                 ("review-output-correction", {"id": "correction", "assignment_id": "missing",
                                                                "route_id": "missing"})):
            with self.subTest(command=command):
                self.assert_refusal_parity(command, payload, "request_id_conflict", request="taken", revision=0)

    def test_check_rejects_a_recorded_id_before_validating_visual_source_files(self):
        self.store.mutate("workspace.artifact", {}, lambda tx: {}, expected_revision=0, request_id="taken")
        payload = {"link": {"version_id": "arxiv:2601.00001v1", "source_id": "missing",
                            "artifact": {"path": "research/sources/objects/" + "0" * 64, "sha256": "0" * 64,
                                         "size": 0, "media_type": "text/html"},
                            "locator": {"kind": "html", "anchor": "figure"}},
                   "url": "https://example.org/figure.png", "max_requests": 0}
        self.assert_refusal_parity("visual", payload, "request_id_conflict", request="taken", revision=0)

    def test_check_rejects_a_recorded_id_before_validating_imported_result_origin(self):
        self.store.mutate("workspace.artifact", {}, lambda tx: {}, expected_revision=0, request_id="taken")
        self.assert_refusal_parity("result", {}, "request_id_conflict", request="taken", revision=0)

    def test_check_validates_prepared_record_and_result_storage_bounds(self):
        for invalid_part in ("record", "result"):
            with self.subTest(part=invalid_part):
                def record_fixture(store, payload, **identity):
                    def prepare(records, value):
                        return [("fixture", "one", {"bad": object()})] if invalid_part == "record" else [], \
                            object() if invalid_part == "result" else {}
                    return operations.prepared_mutation(store, "fixture.invalid", payload, prepare, **identity)

                with mock.patch.dict(cli.OPERATIONS, {"artifact": record_fixture}):
                    self.assert_refusal_parity("artifact", {}, "invalid_input")

    def test_interrupted_preparation_leaves_no_artifacts_or_receipts(self):
        before = self.read_workspace()

        def record_fixture(store, payload, **identity):
            artifacts = store.build_artifact_store()

            def prepare(records, value):
                artifacts.put(b"interrupted fixture", "text/plain")
                raise KeyboardInterrupt("Interrupted after artifact staging")

            return operations.prepared_mutation(store, "fixture.interrupt", payload, prepare, **identity)

        with mock.patch.dict(cli.OPERATIONS, {"artifact": record_fixture}):
            with self.assertRaises(KeyboardInterrupt):
                self.invoke_command("artifact", {})
        self.assertEqual(self.read_workspace(), before)

    def test_check_subprocess_has_explicit_paths_and_needs_no_cli_installation(self):
        payload_path = self.root.parent / "payload.json"
        payload_path.write_text(json.dumps(self.payload))
        environment = dict(os.environ, PATH="/usr/bin:/bin", EXACTORY_API_KEY="")
        environment.pop("PYTHONPATH", None)
        response = subprocess.run([sys.executable, str(PLUGIN / "bin/exactory-research"), "artifact",
                                   "--workspace", str(self.root), "--file", str(payload_path),
                                   "--expected-revision", "0", "--request-id", "subprocess", "--check"],
                                  cwd=self.root.parent, env=environment, capture_output=True, text=True)
        self.assert_accepted_check((response.returncode, response.stdout, response.stderr), 0)

    def test_runtime_metadata_reads_git_without_optional_index_writes(self):
        from research_harness.provenance import _git
        git_directory = self.root / ".git"
        git_directory.mkdir()
        index = git_directory / "index"
        index.write_bytes(b"Original index bytes")
        before = index.read_bytes(), index.stat().st_mtime_ns

        def run_git(command, **options):
            if (options.get("env", {}).get("GIT_OPTIONAL_LOCKS") != "0"
                    or "core.fsmonitor=false" not in command):
                index.write_bytes(b"Refreshed optional index")
            return subprocess.CompletedProcess(command, 0, stdout="head\n", stderr="")

        with mock.patch("research_harness.provenance.shutil.which", return_value=sys.executable), \
                mock.patch("research_harness.provenance.subprocess.run", side_effect=run_git):
            _git(self.root)
        self.assertEqual((index.read_bytes(), index.stat().st_mtime_ns), before)


class DevelopmentCheckModeTests(CheckCommandMixin, DevelopmentCase):
    def test_assessment_check_reports_stale_inheritance_before_refresh(self):
        from test_research_inheritance_refresh import InheritanceRefreshTests
        payload = InheritanceRefreshTests.prepare_stale_descendant(self)["payload"]
        payload.pop("inheritance_refresh")
        self.assert_refusal_parity("assess", payload, "inherited_result_stale")

    def test_manuscript_check_reads_staged_claims_and_preserves_refusal_order(self):
        from integration_fixtures import account_fixture_citations, observed_candidate
        self.prepared_study()
        execution = observed_candidate(self)
        (self.root / "draft").mkdir()
        (self.root / "evidence").mkdir()
        (self.root / "draft/paper.pdf").write_bytes(b"%PDF-1.4\n% Authored check fixture.\n%%EOF")
        (self.root / "draft/abstract.txt").write_text("The finite bound was enumerated.")
        (self.root / "draft/references.bib").write_text("@article{bounded,title={Authored bound}}\n")
        claims = self.root / "evidence/claims.json"
        claims.write_text("[]")
        payload = {"id": "check-paper", "files": {"pdf": "draft/paper.pdf", "abstract": "draft/abstract.txt",
                   "bibliography": "draft/references.bib", "claims": "evidence/claims.json", "sources": None},
                   "claim_evidence": [{"claim_id": "bound", "evidence": [self.result_evidence(execution)]}],
                   "citation_accounting": account_fixture_citations(self)}
        self.assert_refusal_parity("manuscript", payload, "publication_claims_missing")
        claims.write_text(json.dumps([{"id": "bound", "claim": "The maximum is 9."}]))
        before = self.read_workspace()
        revision = self.store.revision
        self.assert_accepted_check(self.invoke_command("manuscript", payload), revision)
        self.assertEqual(self.read_workspace(), before)
        response = self.invoke_command("manuscript", payload, check=False)
        self.assertEqual(response[0], 0, response[2])

    def test_plan_check_preserves_the_first_scope_refusal_and_then_accepts(self):
        self.prepared_study()
        payload = self.plan()
        del payload["scope"]["remaining_obligations"]
        self.assert_refusal_parity("cycle", payload, "invalid_development")
        payload = self.plan()
        before = self.read_workspace()
        revision = self.store.revision
        self.assert_accepted_check(self.invoke_command("cycle", payload), revision)
        self.assertEqual(self.read_workspace(), before)
        response = self.invoke_command("cycle", payload, check=False)
        self.assertEqual(response[0], 0, response[2])

    def test_assessment_check_preserves_the_first_development_refusal_and_then_accepts(self):
        self.prepared_study()
        plan, execution = self.run_cycle()
        payload = self.assessment(plan, execution)
        payload["development"]["alternatives"] = []
        self.assert_refusal_parity("assess", payload, "invalid_development")
        payload = self.assessment(plan, execution)
        before = self.read_workspace()
        revision = self.store.revision
        self.assert_accepted_check(self.invoke_command("assess", payload), revision)
        self.assertEqual(self.read_workspace(), before)
        response = self.invoke_command("assess", payload, check=False)
        self.assertEqual(response[0], 0, response[2])

    def test_check_result_cannot_supply_a_recorded_plan_to_admission(self):
        self.prepared_study()
        self.assert_accepted_check(self.invoke_command("cycle", self.plan()), self.store.revision)
        self.assertNotIn("cycle_plan", self.store.snapshot()["records"])
        response = self.invoke_command("admit", {"id": "run", "cycle_id": "cycle-1", "plan_digest": "0" * 64,
                                                "command": {}, "reserved_units": 1}, check=False)
        self.assertEqual(response[0], 1, response[2])
        self.assertEqual(json.loads(response[2])["error"]["code"], "unknown_cycle")


if __name__ == "__main__":
    unittest.main()
