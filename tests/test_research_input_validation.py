"""Malformed research payloads refuse at the CLI and before execution ownership."""

import copy
from contextlib import redirect_stderr, redirect_stdout
import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

from development_fixtures import DevelopmentCase
from research_harness import cli, execution, principles
from research_harness.errors import ResearchError
from research_harness.storage import Store


PLUGIN = Path(__file__).resolve().parents[1]


class PayloadCommandMixin:
    def invoke_payload(self, command, payload, *, check=False, request="invalid-input"):
        path = self.root.parent / "payload.json"
        path.write_text(json.dumps(payload))
        arguments = [command, "--workspace", str(self.root), "--file", str(path),
                     "--expected-revision", str(self.store.revision), "--request-id", request]
        if check:
            arguments.append("--check")
        stdout, stderr = io.StringIO(), io.StringIO()
        with redirect_stdout(stdout), redirect_stderr(stderr):
            try:
                cli.main(arguments)
            except SystemExit as error:
                return error.code, stdout.getvalue(), stderr.getvalue()
        return 0, stdout.getvalue(), stderr.getvalue()

    def assert_structured_refusal(self, response):
        status, stdout, stderr = response
        self.assertEqual(status, 1, (stdout, stderr))
        self.assertEqual(stdout, "")
        self.assertNotIn("Traceback", stderr)
        error = json.loads(stderr)["error"]
        self.assertIsInstance(error["code"], str)
        self.assertTrue(error["code"])
        return error["code"]


class ResearchPayloadValidationTests(PayloadCommandMixin, unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name) / "study"
        self.store = Store(self.root, create=True)
        principles.initialize_research(self.store, {"profile": "research", "target": None,
            "preparation_policy": "exhaustive-v1"}, expected_revision=0, request_id="initialize")
        self.examples = json.loads((PLUGIN / "docs/research-cli-examples.json").read_text())
        self.commands = (*cli.OPERATIONS, *cli.ACQUISITION)
        for target in ("socket.socket.connect", "socket.getaddrinfo"):
            patcher = mock.patch(target, side_effect=AssertionError("Unexpected network access"))
            patcher.start()
            self.addCleanup(patcher.stop)

    def assert_payload_refusals(self, make_payload):
        before = self.store.snapshot()
        for command in self.commands:
            for check in (False, True):
                with self.subTest(command=command, check=check):
                    self.assert_structured_refusal(self.invoke_payload(command, make_payload(command), check=check,
                        request=command + "-" + str(check)))
        self.assertEqual(self.store.snapshot(), before)

    def test_every_operation_refuses_an_empty_object(self):
        self.assert_payload_refusals(lambda command: {})

    def test_every_operation_refuses_one_required_field_with_the_wrong_type(self):
        def build_malformed_payload(command):
            payload = copy.deepcopy(self.examples[command])
            field = "response_file" if command == "import-response" else next(iter(payload))
            payload[field] = {} if isinstance(payload[field], list) else []
            return payload

        self.assert_payload_refusals(build_malformed_payload)

    def test_every_operation_refuses_non_object_json(self):
        for payload in ([], None, "payload", 0, True):
            with self.subTest(payload=payload):
                self.assert_payload_refusals(lambda command: payload)

    def test_reconciliation_refuses_before_acquiring_a_lock_in_both_ownership_paths(self):
        for payload in ({}, {"admission_id": []}, [], None, "payload", 0, True):
            for owned in (False, True):
                with self.subTest(payload=payload, owned=owned), mock.patch.object(execution, "_owner") as owner:
                    with self.assertRaises(ResearchError) as caught:
                        execution.reconcile_execution(self.store, payload, expected_revision=self.store.revision,
                                                      request_id="reconcile", owned=owned)
                    self.assertEqual(caught.exception.code, "invalid_input")
                    owner.assert_not_called()

    def test_reconciliation_cli_emits_json_instead_of_a_traceback(self):
        path = self.root.parent / "empty.json"
        path.write_text("{}")
        for check in (False, True):
            with self.subTest(check=check):
                arguments = [sys.executable, str(PLUGIN / "bin/exactory-research"), "reconcile-run",
                    "--workspace", str(self.root), "--file", str(path), "--expected-revision",
                    str(self.store.revision), "--request-id", "reconcile-" + str(check)]
                result = subprocess.run(arguments + (["--check"] if check else []), capture_output=True, text=True)
                code = self.assert_structured_refusal((result.returncode, result.stdout, result.stderr))
                self.assertEqual(code, "check_unsupported" if check else "invalid_input")

    def test_selection_identifiers_refuse_wrong_types_before_record_lookup(self):
        for command, fields, code in (
            ("select-cohort-abstract", ("collection_id", "work_id", "unresolved_assertion_id", "selected_assertion_id"),
             "invalid_cohort_selection"),
            ("select-publication-scope", ("id",), "invalid_publication_scope"),
        ):
            for field in fields:
                for invalid in ([], {}, 0, True, ""):
                    for check in (False, True):
                        with self.subTest(command=command, field=field, invalid=invalid, check=check):
                            payload = dict(self.examples[command], **{field: invalid})
                            self.assertEqual(self.assert_structured_refusal(
                                self.invoke_payload(command, payload, check=check)), code)

    def test_import_response_preview_refuses_a_non_path_before_opening_it(self):
        for invalid in ([], {}, None, 0, True):
            with self.subTest(invalid=invalid):
                path = self.root.parent / "preview.json"
                path.write_text(json.dumps(dict(self.examples["import-response"], response_file=invalid)))
                with self.assertRaises(ResearchError) as caught:
                    cli.run(cli.build_parser().parse_args(["import-response", "--preview", "--workspace",
                        str(self.root), "--file", str(path)]))
                self.assertEqual(caught.exception.code, "invalid_input")


class ReadinessAssessorValidationTests(PayloadCommandMixin, DevelopmentCase):
    def test_readiness_review_keeps_each_boolean_and_legacy_absence(self):
        _, result, _ = self.prepared_candidate()
        for kind in ("human", "agent"):
            for present in (None, False, True):
                identifier = "memory-" + kind + "-" + str(present)
                with self.subTest(kind=kind, present=present):
                    payload = self.review(result, identifier=identifier)
                    payload["assessor"]["kind"] = kind
                    if present is not None:
                        payload["assessor"]["author_memory_notes_present"] = present
                    before = self.store.snapshot()
                    checked = self.invoke_payload("review", payload, check=True, request=identifier)
                    self.assertEqual(checked[0], 0, checked[2])
                    self.assertEqual(json.loads(checked[1])["check"], "accepted")
                    self.assertEqual(self.store.snapshot(), before)
                    written = self.invoke_payload("review", payload, request=identifier)
                    self.assertEqual(written[0], 0, written[2])
                    saved = self.store.snapshot()["records"]["readiness_review"][identifier]
                    self.assertEqual(saved["payload"], payload)
                    archived = json.loads(self.artifacts.read(saved["artifact"]))
                    self.assertEqual(archived, payload)
                    for assessor in (saved["payload"]["assessor"], archived["assessor"]):
                        if present is None:
                            self.assertNotIn("author_memory_notes_present", assessor)
                        else:
                            self.assertIs(assessor["author_memory_notes_present"], present)
                    replayed = self.invoke_payload("review", payload, request=identifier)
                    self.assertEqual(replayed[0], 0, replayed[2])
                    self.assertEqual(json.loads(replayed[1]), json.loads(written[1]))
                    self.assertTrue(self.development().readiness_report(self.store)["ready"])

    def test_readiness_review_refuses_non_boolean_memory_disclosures(self):
        _, result, _ = self.prepared_candidate()
        for present in (None, 0, 1, "false", "true", [], {}):
            payload = self.review(result)
            payload["assessor"]["author_memory_notes_present"] = present
            for check in (False, True):
                with self.subTest(present=present, check=check):
                    before = self.store.snapshot()
                    response = self.invoke_payload("review", payload, check=check)
                    self.assertEqual(self.assert_structured_refusal(response), "invalid_development")
                    self.assertIn("boolean", json.loads(response[2])["error"]["message"])
                    self.assertEqual(self.store.snapshot(), before)
