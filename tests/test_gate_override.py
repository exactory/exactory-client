"""User instructions move work forward without giving unmet gates any credit."""

import hashlib
import html
import io
import json
import os
from pathlib import Path
import subprocess
import sys
from unittest import mock
import zipfile

from research_harness import cli, integration, overrides, problem_claims, problem_delivery
from research_harness.errors import ResearchError
from research_harness.gates import STAGES
from research_harness.storage import Store
from test_research_check_mode import CheckCommandMixin
from test_research_problem_claims import ProblemCase
from test_research_problem_delivery import ProblemDeliveryCase


PLUGIN = Path(__file__).resolve().parents[1]
INSTRUCTION = "Continue through the named gate on my instruction. Keep the missing reviews visible.\n"
DISCLOSURE = ("This paper was deposited from a workspace of a research harness that checks each step of the work before "
              "the next one. Some of these checks require independent reviews. The record of that workspace holds entries "
              "for these checks: {gates}. A readable entry says that the user of the session instructed the work to go on "
              "without waiting for that check. The agent that ran the work recorded the entry. An entry named unknown could "
              "not be read. The record does not show who that user was, whether the entry was used, or whether the check "
              "was completed later.")
ACTION_DISCLOSURE = (
    "The workspace has a user-instruction entry {id} for the exact public write {public_action}. "
    "The agent recorded that the user instructed this write. {confirmation} "
    "This entry does not pass a research gate or show who the user was, whether the instruction was used, "
    "or whether the write completed.")
AUTOMATIC_DISCLOSURE = (
    "This paper was deposited from a workspace of a research harness that checks each step of the work before "
    "the next one. Some checks require independent reviews. The workspace has an automatic-continuation entry "
    "for the check {gate}, with stop identifier {stop_id} and code {stop_code}. A readable entry says that the "
    "harness classified the stop as a defect of its own and proposed advancing the work without the missing "
    "reviews. {confirmation} An entry named unknown could not be read. This entry supplies no review approval "
    "or permission to publish, and does not show whether the missing reviews were completed later.")


class OverrideCommandMixin(CheckCommandMixin):
    def write_instruction(self):
        (self.root / "context").mkdir(exist_ok=True)
        (self.root / "context/instruction.txt").write_text(INSTRUCTION, encoding="utf-8")

    def invoke_arguments(self, arguments):
        result = self.invoke_process("exactory-research", arguments)
        return result.returncode, result.stdout, result.stderr

    def invoke_process(self, command, arguments, **environment):
        return subprocess.run([sys.executable, str(PLUGIN / "bin" / command), *arguments],
                              cwd=self.root, capture_output=True, text=True,
                              env=dict(os.environ, EXACTORY_API_KEY="", **environment))

    def build_override(self, identifier="override-1", gate="readiness", **changes):
        return dict(id=identifier, gate=gate, instruction="context/instruction.txt",
                    reason="The user requested continued work with the obligations retained.", **changes)

    def record_override(self, payload=None, request=None):
        payload = payload or self.build_override()
        result = self.invoke_command("gate-override", payload, check=False,
                                     request=request or payload["id"])
        self.assertEqual(result[0], 0, result[2])
        return json.loads(result[1])

    def read_report(self, command="status", summary=False):
        arguments = [command, "--workspace", str(self.root)]
        if summary:
            arguments.append("--summary")
        response = self.invoke_process("exactory-research", arguments)
        self.assertEqual(response.returncode, 0, response.stderr)
        return json.loads(response.stdout)

    def build_deposit_description(self, store):
        from test_draft import _draft
        return _draft._build_deposit_metadata("Paper", ["Author"], "Abstract", False,
            passed_gates=overrides.list_passed_gates(store))["description"]

    def assert_unreadable_log_is_reported(self, count, line_number, gates):
        identifier = "unreadable-line-" + str(line_number)
        for command in ("status", "next"):
            for summary in (False, True):
                with self.subTest(command=command, summary=summary):
                    rows = self.read_report(command, summary)["unrecorded_overrides"]
                    self.assertEqual(len(rows), count)
                    self.assertTrue(all(isinstance(row, dict) for row in rows), rows)
                    unknown, = [row for row in rows if row["gate"] == "unknown"]
                    self.assertEqual(unknown["id"], identifier)
                    instruction = unknown["instruction"] if summary else unknown["instruction"]["text"]
                    self.assertIn("could not be read", instruction)
        description = self.build_deposit_description(self.store)
        self.assertTrue(description.endswith("<p>" + DISCLOSURE.format(gates=", ".join(gates)) + "</p>"), description)

    def assert_override_refused(self, payload, code=None):
        before = self.store.snapshot()
        for check in (True, False):
            response = self.invoke_command("gate-override", payload, check=check,
                                           request="refused-override")
            self.assertEqual(response[0], 1, response)
            error = json.loads(response[2].splitlines()[0])["error"]
            if code:
                self.assertEqual(error["code"], code, error)
            else:
                self.assertTrue(error["code"].startswith("gate_override_"), error)
        self.assertEqual(self.store.snapshot(), before)

    def export_problem(self, kind="problem-override", *arguments):
        return self.invoke_process("exactory-research", ["export", "--kind", kind, *arguments])

    def assert_report_overrides(self, exists):
        for command in ("status", "next"):
            for summary in (False, True):
                report = self.read_report(command, summary)
                self.assertEqual("overrides" in report, exists, report)
                if exists:
                    row = report["overrides"][0]
                    self.assertEqual(row["id"], "override-1")
                    self.assertEqual(row["instruction"], INSTRUCTION)
                    self.assertEqual(row["gate"], self.override_gate)
                    self.assertEqual(row["transition"], None)
                    self.assertEqual(row["stop_code"], "readiness_required")
                    if summary:
                        self.assertEqual(row["obligations"], self.obligation_codes[:6])
                        self.assertEqual(row["omitted_obligations"], max(0, len(self.obligation_codes) - 6))
                    else:
                        self.assertEqual(row["obligations"], self.obligation_codes)


class StudyOverrideTests(OverrideCommandMixin, ProblemCase):
    def setUp(self):
        super().setUp()
        state = dict(slug="override-study", stage="literature", status="in_progress", autopilot=True,
                     waiting=None, loop=dict(target=None, budget=None, notes=""),
                     created="2026-11-06T00:00:00Z", updated="2026-11-06T00:00:00Z")
        self.mutate(integration.initialize_workspace, dict(kind="study", state=state))
        self.write_instruction()

    def set_stage(self, stage=None, status=None):
        arguments = ["state", "set", "--expected-revision", str(self.store.revision),
                     "--request-id", "stage-" + str(self.sequence)]
        self.sequence += 1
        if stage:
            arguments.extend(["--stage", stage])
        if status:
            arguments.extend(["--status", status])
        return self.invoke_process("exactory-lab", arguments)

    def test_each_gated_hop_and_same_stage_completion_require_the_exact_override(self):
        before = self.store.snapshot()["records"]
        stages = STAGES[STAGES.index("literature"):]
        for source, target in zip(stages, stages[1:]):
            with self.subTest(source=source, target=target):
                self.assertNotEqual(self.set_stage(target).returncode, 0)
                self.record_override(self.build_override("other-" + source,
                    transition={"from": source, "to": source}))
                self.assertNotEqual(self.set_stage(target).returncode, 0)
                self.record_override(self.build_override("hop-" + source,
                    transition={"from": source, "to": target}))
                response = self.set_stage(target)
                self.assertEqual(response.returncode, 0, response.stderr)
                self.assertEqual(json.loads(response.stdout)["stage"], target)
        self.assertNotEqual(self.set_stage(status="done").returncode, 0)
        self.record_override(self.build_override("finish", transition={"from": "complete", "to": "complete"}))
        response = self.set_stage(status="done")
        self.assertEqual(response.returncode, 0, response.stderr)
        self.assertEqual(json.loads(response.stdout)["status"], "done")
        after = self.store.snapshot()["records"]
        self.assertEqual(set(after) - set(before), {"gate_override"})
        for kind in before:
            if kind not in ("literature_operation", "workspace"):
                self.assertEqual(after[kind], before[kind])
        self.assertEqual(set(after["workspace"]), set(before["workspace"]))
        self.assertFalse(set(after) & {"readiness_review", "manuscript_review", "research_decision",
                                     "problem_decision", "value_review", "problem_post"})
        self.assertFalse(self.read_report()["ready"])

    def test_direct_jump_requires_its_own_override(self):
        self.assertNotEqual(self.set_stage("complete").returncode, 0)
        self.record_override(self.build_override(transition={"from": "literature", "to": "ideate"}))
        self.assertNotEqual(self.set_stage("complete").returncode, 0)
        self.record_override(self.build_override("jump", transition={"from": "literature", "to": "complete"}))
        response = self.set_stage("complete", "done")
        self.assertEqual(response.returncode, 0, response.stderr)

    def test_all_fourteen_gate_commands_keep_their_reports(self):
        manuscript = dict(id="paper", files={key: "draft/" + key for key in
            ("pdf", "abstract", "bibliography", "claims", "sources")}, claim_evidence=[])
        manuscript_before = self.invoke_command("manuscript", manuscript, check=True)
        self.assertNotEqual(manuscript_before[0], 0)

        def read_gates():
            reports = {}
            for gate in cli.GATES:
                response = self.invoke_process("exactory-research", ["gate", gate])
                data = json.loads(response.stdout if response.returncode == 0 else response.stderr)
                if response.returncode == 0:
                    data.pop("revision", None)
                reports[gate] = (response.returncode, data)
            return reports
        before = read_gates()
        self.assertEqual(len(before), 14)
        for gate in cli.GATES:
            if gate != "post":
                self.record_override(self.build_override("pass-" + gate, gate=gate))
        self.assertEqual(read_gates(), before)
        response = self.invoke_command("manuscript", manuscript, check=False)
        self.assertNotEqual(response[0], 0)
        self.assertEqual(json.loads(response[2]), json.loads(manuscript_before[2]))

    def test_instruction_is_pinned_and_retry_replays_after_file_removal(self):
        payload = self.build_override(stop_code="readiness_required")
        revision = self.store.revision
        receipt = self.record_override(payload, request="original")
        record = receipt["result"]
        self.assertEqual(record["authority"], "user")
        self.assertEqual(record["evidence"], "reported_by_agent")
        self.assertEqual(record["recorded_revision"], revision + 1)
        self.assertEqual(record["instruction"]["path"], payload["instruction"])
        self.assertEqual(record["instruction"]["text"], INSTRUCTION)
        self.assertEqual(self.artifacts.read(record["instruction"]["artifact"]), INSTRUCTION.encode())
        self.assertEqual(record["instruction"]["artifact"]["sha256"], hashlib.sha256(INSTRUCTION.encode()).hexdigest())
        self.assertTrue(record["obligations"])
        self.assertTrue(all(set(row) == {"code", "explanation"} for row in record["obligations"]))
        (self.root / payload["instruction"]).unlink()
        before = self.read_workspace()
        for check in (False, True):
            response = self.invoke_command("gate-override", payload, revision=revision, request="original", check=check)
            self.assertEqual(response[0], 0, response[2])
            self.assertEqual(json.loads(response[1]), receipt)
        self.assertEqual(self.read_workspace(), before)

    def test_check_writes_nothing_and_one_instruction_can_back_several_overrides(self):
        before = self.read_workspace()
        self.assert_accepted_check(self.invoke_command("gate-override", self.build_override()), self.store.revision)
        self.assertEqual(self.read_workspace(), before)
        first = self.record_override()["result"]
        second = self.record_override(self.build_override("second"))["result"]
        self.assertEqual(first["instruction"], second["instruction"])
        self.assert_override_refused(dict(self.build_override(), reason="Different content"))

    def test_refuses_missing_empty_parent_symlink_outside_and_oversized_instructions(self):
        paths = {"missing": "context/missing.txt",
                 "parent": "context/../context/instruction.txt", "outside": "instruction.txt",
                 "empty": "context/empty.txt", "oversized": "context/large.txt",
                 "symlink": "context/link.txt", "linked-parent": "context/linked/instruction.txt",
                 "directory": "context", "invalid-utf8": "context/invalid.txt"}
        (self.root / "instruction.txt").write_text(INSTRUCTION)
        (self.root / "context/empty.txt").write_text(" \n")
        (self.root / "context/large.txt").write_bytes(b"x" * (64 * 1024 + 1))
        (self.root / "context/invalid.txt").write_bytes(b"\xff")
        (self.root / "context/link.txt").symlink_to(self.root / "context/instruction.txt")
        (self.root / "context/linked").symlink_to(self.root / "context", target_is_directory=True)
        for name, path in paths.items():
            with self.subTest(name=name):
                payload = self.build_override()
                payload["instruction"] = path
                self.assert_override_refused(payload, code='unsafe_path'
                                             if name in ('parent', 'symlink', 'linked-parent') else None)
        for missing in ("id", "gate", "instruction", "reason"):
            payload = self.build_override()
            del payload[missing]
            self.assert_override_refused(payload)

    def test_absolute_instruction_pins_the_same_relative_record_and_replays_without_the_file(self):
        relative = self.build_override()
        absolute = dict(relative, instruction=str(self.root / relative['instruction']))
        revision = self.store.revision
        first = self.assert_accepted_check(self.invoke_command('gate-override', relative), revision)
        second = self.assert_accepted_check(self.invoke_command('gate-override', absolute), revision)
        self.assertEqual(first['payload_digest'], second['payload_digest'])
        receipt = self.record_override(absolute, request='absolute-instruction')
        self.assertEqual(receipt['result']['instruction']['path'], relative['instruction'])
        (self.root / relative['instruction']).unlink()
        replay = self.invoke_command('gate-override', relative, check=False, revision=revision,
                                     request='absolute-instruction')
        self.assertEqual(replay[0], 0, replay[2])
        self.assertEqual(json.loads(replay[1]), receipt)

    def test_refuses_unknown_gate_backward_wrong_source_and_unknown_stage(self):
        self.assert_override_refused(self.build_override(gate="unknown"))
        for transition in ({"from": "literature", "to": "cohort"}, {"from": "ideate", "to": "write"},
                           {"from": "literature", "to": "unknown"}):
            with self.subTest(transition=transition):
                self.assert_override_refused(self.build_override(transition=transition))

    def test_status_next_and_both_summaries_only_add_overrides_when_present(self):
        self.assert_report_overrides(False)
        record = self.record_override(self.build_override(stop_code="readiness_required"))["result"]
        self.override_gate = "readiness"
        self.obligation_codes = [row["code"] for row in record["obligations"]]
        self.assert_report_overrides(True)

    def test_output_export_places_only_existing_overrides_under_final(self):
        response = self.invoke_process("exactory-research", ["export", "--kind", "outputs"])
        self.assertEqual(response.returncode, 0, response.stderr)
        self.assertFalse((self.root / "final/overrides").exists())
        record = self.record_override()["result"]
        response = self.invoke_process("exactory-research", ["export", "--kind", "outputs"])
        self.assertEqual(response.returncode, 0, response.stderr)
        views = list((self.root / "final/overrides").glob("*.json"))
        self.assertEqual(len(views), 1)
        self.assertEqual(json.loads(views[0].read_text()), record)

    def test_corrupt_store_retains_the_instruction_without_obligations_and_status_lists_it_after_repair(self):
        database = self.root / ".exactory/research.sqlite3"
        original = database.read_bytes()
        revision = self.store.revision
        database.write_bytes(b"not sqlite")
        try:
            response = self.invoke_command("gate-override", self.build_override(), check=True, revision=revision)
            self.assertNotEqual(response[0], 0)
            path = self.root / ".exactory/unrecorded-overrides.jsonl"
            self.assertFalse(path.exists())
            response = self.invoke_command("gate-override", self.build_override(), check=False, revision=revision)
            self.assertEqual(response[0], 0, response[2])
            receipt = json.loads(response[1])
            self.assertIs(receipt["recorded"], False)
            self.assertEqual(Path(receipt["path"]), path)
            row, = [json.loads(line) for line in path.read_text().splitlines()]
            self.assertEqual(row["instruction"]["text"], INSTRUCTION)
            self.assertEqual(row["instruction"]["sha256"], hashlib.sha256(INSTRUCTION.encode()).hexdigest())
            self.assertNotIn("obligations", row)
        finally:
            database.write_bytes(original)
        self.store = Store(self.root)
        report = self.read_report()
        self.assertEqual(report["unrecorded_overrides"], [row])
        self.assertNotIn("gate_override", self.store.snapshot()["records"])
        for command in ("status", "next"):
            summary = self.read_report(command, True)
            self.assertNotIn("obligations", summary["unrecorded_overrides"][0])

    def test_storage_write_failure_uses_the_same_local_fallback(self):
        self.assertIn("gate-override", cli.OPERATIONS)
        payload_path = self.root.parent / "fallback.json"
        payload_path.write_text(json.dumps(self.build_override()))
        with mock.patch.object(Store, "mutate", side_effect=ResearchError("storage_io", "Disk cannot write")):
            receipt = cli.run(cli.build_parser().parse_args(["gate-override", "--workspace", str(self.root),
                "--file", str(payload_path), "--expected-revision", str(self.store.revision), "--request-id", "fallback"]))
        self.assertIs(receipt["recorded"], False)
        self.assertTrue(Path(receipt["path"]).is_file())

    def test_damaged_local_log_rows_preserve_reports_summaries_and_deposit_disclosure(self):
        path = self.root / ".exactory/unrecorded-overrides.jsonl"
        first = dict(id="first", gate="readiness", instruction=dict(text=INSTRUCTION))
        last = dict(id="last", gate="publication", instruction=dict(text=INSTRUCTION))
        malformed = [("json", b"{broken line"), ("array", b"[1, 2]"), ("scalar", b"null"),
            ("blank", b""), ("utf8", b"\xff"),
            ("id-missing", dict(gate="post", instruction=dict(text=INSTRUCTION))),
            ("id-type", dict(first, id=1)), ("gate-missing", dict(id="bad", instruction=dict(text=INSTRUCTION))),
            ("gate-type", dict(first, gate=[])), ("instruction-missing", dict(id="bad", gate="post")),
            ("instruction-type", dict(first, instruction=[])),
            ("text-missing", dict(first, instruction={})), ("text-type", dict(first, instruction=dict(text=1)))]
        for name, value in malformed:
            with self.subTest(damage=name):
                line = value if isinstance(value, bytes) else json.dumps(value).encode()
                path.write_bytes(json.dumps(first).encode() + b"\n" + line + b"\n" + json.dumps(last).encode() + b"\n")
                self.assert_unreadable_log_is_reported(3, 2, ["publication", "readiness", "unknown"])
                rows = overrides.read_unrecorded_overrides(self.root)
                self.assertEqual((rows[0], rows[-1]), (first, last))

    def test_unreadable_local_log_preserves_reports_summaries_and_deposit_disclosure(self):
        path = self.root / ".exactory/unrecorded-overrides.jsonl"
        for damage in ("directory", "permission", "symlink"):
            with self.subTest(damage=damage):
                if damage == "directory":
                    path.mkdir()
                elif damage == "symlink":
                    path.symlink_to(self.root / "context/instruction.txt")
                else:
                    path.write_text("{broken line\n")
                    path.chmod(0)
                try:
                    self.assert_unreadable_log_is_reported(1, 1, ["unknown"])
                finally:
                    if damage == "directory":
                        path.rmdir()
                    else:
                        if damage == "permission":
                            path.chmod(0o600)
                        path.unlink()

    def test_deposit_disclosure_keeps_local_gates_when_store_snapshot_fails(self):
        self.record_override()
        path = self.root / ".exactory/unrecorded-overrides.jsonl"
        baseline = self.build_deposit_description(None)
        for local in (False, True):
            if local:
                path.write_text(json.dumps(dict(id="local", gate="publication", instruction=dict(text=INSTRUCTION))) + "\n")
            for code in ("corrupt_state", "storage_io"):
                with self.subTest(local=local, code=code), \
                        mock.patch.object(Store, "snapshot", side_effect=ResearchError(code, "Cannot read")):
                    self.assertEqual(overrides.list_passed_gates(self.store), ["publication"] if local else [])
                    expected = baseline + ("<p>" + DISCLOSURE.format(gates="publication") + "</p>" if local else "")
                    self.assertEqual(self.build_deposit_description(self.store), expected)

    def test_deposit_without_store_finds_workspace_from_subdirectory_and_falls_back_to_cwd(self):
        standalone = self.root.parent / "standalone"
        standalone.mkdir()
        (standalone / ".exactory").mkdir()
        subdirectory = self.root / "draft"
        subdirectory.mkdir()
        original = Path.cwd()
        try:
            for root, current in ((self.root, subdirectory), (standalone, standalone)):
                with self.subTest(root=root):
                    (root / ".exactory/unrecorded-overrides.jsonl").write_text(
                        json.dumps(dict(id="local", gate="publication", instruction=dict(text=INSTRUCTION))) + "\n")
                    os.chdir(current)
                    self.assertEqual(overrides.list_passed_gates(None), ["publication"])
                    self.assertTrue(self.build_deposit_description(None).endswith(
                        "<p>" + DISCLOSURE.format(gates="publication") + "</p>"))
        finally:
            os.chdir(original)

    def test_second_local_append_replaces_read_only_log_and_keeps_lock_private(self):
        path = self.root / ".exactory/unrecorded-overrides.jsonl"
        lock = path.with_name(path.name + ".lock")
        receipts = []
        modes = []
        for identifier in ("first", "second"):
            receipts.append(overrides.append_unrecorded_override(self.root, self.build_override(identifier),
                expected_revision=self.store.revision, request_id=identifier))
            modes.append(path.stat().st_mode & 0o777)
            if identifier == "first":
                original = path.read_bytes()
        self.assertEqual(modes, [0o400, 0o400])
        self.assertEqual(lock.stat().st_mode & 0o777, 0o600)
        self.assertTrue(path.read_bytes().startswith(original))
        self.assertEqual([row["id"] for row in overrides.read_unrecorded_overrides(self.root)], ["first", "second"])
        self.assertEqual(receipts, [dict(recorded=False, path=str(path))] * 2)

    def test_gate_report_error_is_recorded_as_an_obligation(self):
        self.assertIn("gate-override", cli.OPERATIONS)
        with mock.patch.object(cli, "gate_state", side_effect=ResearchError("fixture_gate_error", "Restore the input")):
            receipt = cli.OPERATIONS["gate-override"](self.store, self.build_override(),
                expected_revision=self.store.revision, request_id="gate-error")
        self.assertEqual(receipt["result"]["obligations"],
                         [{"code": "fixture_gate_error", "explanation": "Restore the input"}])

    def test_summary_bounds_hold_with_many_long_quoted_instructions(self):
        self.assertIn("gate-override", cli.OPERATIONS)
        (self.root / "context/instruction.txt").write_text("\x01" * (64 * 1024))
        for index in range(20):
            self.record_override(self.build_override("override-" + str(index)))
        for command, limit in (("next", 4096), ("status", 16 * 1024)):
            report = self.read_report(command, True)
            self.assertIn("overrides", report)
            self.assertTrue(report["overrides"][0]["instruction"])
            self.assertLessEqual(len(json.dumps(report, ensure_ascii=False, indent=2).encode()), limit)

    def test_submit_names_the_recorded_override_in_the_skip_line(self):
        self.record_override()
        response = self.invoke_process("exactory", ["submit", "--doi", "10.5281/zenodo.4242"],
            EXACTORY_API_URL="http://127.0.0.1:9", XDG_CONFIG_HOME=str(self.root / "empty-config"))
        self.assertNotEqual(response.returncode, 0)
        line = next((line for line in response.stderr.splitlines() if "Managed record skipped" in line), "")
        self.assertIn("override-1", line, response.stderr)

    def test_skip_note_survives_a_store_that_becomes_unreadable(self):
        error = ResearchError("readiness_required", "Publish the reviewed bundle first")
        sink = io.StringIO()
        with mock.patch.object(Store, "snapshot", side_effect=ResearchError("storage_io", "Cannot read")), \
                mock.patch("sys.stderr", new=sink):
            cli.note_managed_record_skipped(error, self.store)
        self.assertEqual(sink.getvalue(),
            "Managed record skipped (readiness_required): Publish the reviewed bundle first\n")

    def test_direct_deposit_discloses_recorded_and_unrecorded_gates_and_managed_deposit_still_refuses(self):
        from test_draft import _draft, _FakeZenodoApi
        self.record_override()
        path = self.root / ".exactory/unrecorded-overrides.jsonl"
        path.write_text(json.dumps(dict(id="local", gate="publication", instruction=dict(text=INSTRUCTION))) + "\n")
        (self.root / "draft").mkdir()
        (self.root / "draft/paper.pdf").write_bytes(b"%PDF-1.4 fixture")
        (self.root / "draft/abstract.txt").write_text("A synthetic abstract.")
        self.mutate(integration.initialize_workspace, dict(kind="draft", state=dict(title="A paper", category="math.CO")))
        fake = _FakeZenodoApi()
        original = Path.cwd()
        try:
            os.chdir(self.root)
            args = _draft._build_parser().parse_args(["deposit", "--pdf", "draft/paper.pdf",
                "--abstract-file", "draft/abstract.txt", "--creator", "Fixture, A.", "--managed-only"])
            with mock.patch.object(_draft, "_open_url", fake), mock.patch.dict(os.environ,
                    {"EXACTORY_API_KEY": "", "ZENODO_SANDBOX_TOKEN": "fixture-token"}), \
                    mock.patch("sys.stdout", new=io.StringIO()), mock.patch("sys.stderr", new=io.StringIO()):
                with self.assertRaises(ResearchError):
                    args.handler(args)
                self.assertFalse(fake.requests)
                args.managed_only = False
                args.handler(args)
        finally:
            os.chdir(original)
        request, = [row for row in fake.requests if row.get_method() == "PUT" and row.full_url.endswith("/deposit/depositions/4242")]
        description = json.loads(request.data)["metadata"]["description"]
        self.assertTrue(description.endswith("<p>" + DISCLOSURE.format(gates="publication, readiness") + "</p>"), description)


class ProblemOverrideTests(OverrideCommandMixin, ProblemCase):
    def setUp(self):
        super().setUp()
        self.initialize_problem()
        self.write_instruction()

    def prepare_problem(self, formal=False):
        statement = self.select_statement(formal_statement=dict(language="Lean", text="theorem bound : True := by trivial") if formal else None)
        evidence = self.record_full_reading()
        self.mutate(problem_claims.record_value, self.build_value([evidence]))
        return statement

    def test_damaged_local_log_is_visible_in_problem_reports_summaries_and_deposit_disclosure(self):
        (self.root / ".exactory/unrecorded-overrides.jsonl").write_text("{broken line\n")
        self.assert_unreadable_log_is_reported(1, 1, ["unknown"])

    def test_post_override_requires_statement_and_refuses_study_transition(self):
        self.assert_override_refused(self.build_override(gate="post"), "problem_statement_missing")
        self.select_statement()
        self.assert_override_refused(self.build_override(gate="post", transition={"from": "literature", "to": "ideate"}))

    def test_problem_composer_exports_without_reviews_and_external_post_keeps_bypassed_gate(self):
        statement = self.prepare_problem(formal=True)
        before = self.store.snapshot()["records"]
        response = self.export_problem("problem")
        self.assertNotEqual(response.returncode, 0)
        response = self.export_problem()
        self.assertNotEqual(response.returncode, 0)
        self.assertIn("gate_override_required", response.stderr)
        gate_before = self.invoke_process("exactory-research", ["gate", "post"])
        receipt = self.record_override(self.build_override(gate="post", stop_code="review_input_too_large"))
        record = receipt["result"]
        self.assertEqual(record["statement_id"], statement["id"])
        self.assertEqual(record["statement_digest"], problem_claims.compute_statement_digest(statement))
        gate_after = self.invoke_process("exactory-research", ["gate", "post"])
        self.assertEqual(gate_after.stderr, gate_before.stderr)
        response = self.export_problem()
        self.assertEqual(response.returncode, 0, response.stderr)
        manifest = json.loads(response.stdout)
        directory = Path(manifest.pop("destination"))
        self.assertEqual(directory.parent, self.root.resolve() / "final/problem/exports")
        self.assertEqual(directory.name, problem_delivery.compute_export_digest({path.name: path.read_bytes() for path in directory.iterdir()}))
        expected = {"title.txt": statement["title"] + "\n", "statement.md": statement["statement"] + "\n",
                    "criteria.md": statement["resolution_criteria"] + "\n",
                    "motivation.md": "The bound would connect the finite constructions.\n",
                    "state.md": "Only bounded examples have been established.\n",
                    "formal-statement.txt": statement["formal_statement"]["text"] + "\n",
                    "citations.json": json.dumps(problem_delivery.project_citations(before, self.artifacts), ensure_ascii=False, sort_keys=True, indent=2) + "\n"}
        self.assertEqual(set(path.name for path in directory.iterdir()), set(expected) | {"problem-export.json"})
        for name, content in expected.items():
            self.assertEqual((directory / name).read_bytes(), content.encode())
        self.assertEqual(json.loads((directory / "problem-export.json").read_text()), manifest)
        self.assertEqual(manifest, dict(statement_id=statement["id"], statement_digest=record["statement_digest"],
            area="math.CO", formal_statement_language="Lean", files={name: hashlib.sha256(content.encode()).hexdigest() for name, content in expected.items()},
            decision_id=None, problem_evidence_digest=None, override_id="override-1", gate="bypassed"))
        self.assertNotEqual(self.export_problem("problem").returncode, 0)
        replay = self.export_problem()
        self.assertEqual(replay.returncode, 0, replay.stderr)
        response = self.invoke_command("problem-posted", dict(problem_id="656c336e-e892-4c47-80d2-a71d022f4116", export_directory=str(directory)), check=False)
        self.assertNotEqual(response[0], 0)
        after = self.store.snapshot()["records"]
        self.assertEqual(set(after) - set(before), {"gate_override"})
        self.assertFalse(set(after) & {"problem_decision", "value_review", "problem_post"})
        response = self.invoke_command("problem-post-external", dict(id="external", problem_id="656c336e-e892-4c47-80d2-a71d022f4116",
            statement_digest=record["statement_digest"], reason="User instructed the direct post.", evidence=record["instruction"]["artifact"]), check=False)
        self.assertEqual(response[0], 0, response[2])
        self.assertEqual(self.read_report()["post"]["gate"], "bypassed")

    def test_new_statement_requires_a_new_override(self):
        self.prepare_problem()
        self.record_override(self.build_override(gate="post"))
        self.select_statement(identifier="statement-2", statement="A different mathematical bound.")
        response = self.export_problem()
        self.assertNotEqual(response.returncode, 0)
        self.assertIn("gate_override_required", response.stderr)
        self.record_override(self.build_override("override-2", gate="post"))
        self.assertEqual(self.export_problem().returncode, 0)

    def test_missing_value_refuses_and_destination_rules_match_normal_problem_export(self):
        self.select_statement()
        self.record_override(self.build_override(gate="post"))
        response = self.export_problem()
        self.assertNotEqual(response.returncode, 0)
        self.assertIn("problem_value_missing", response.stderr)
        response = self.export_problem("problem-override", "--destination", str(self.root / "outside"))
        self.assertNotEqual(response.returncode, 0)
        self.assertIn("export_destination_outside_final", response.stderr)

    def test_problem_status_next_and_summaries_expose_the_quoted_instruction(self):
        self.prepare_problem()
        self.assert_report_overrides(False)
        record = self.record_override(self.build_override(gate="post", stop_code="readiness_required"))["result"]
        self.override_gate = "post"
        self.obligation_codes = [row["code"] for row in record["obligations"]]
        self.assert_report_overrides(True)


class NormalProblemExportTests(OverrideCommandMixin, ProblemDeliveryCase):
    def test_normal_problem_export_preserves_every_file_byte(self):
        self.prepare_ready(formal=True)
        decision = self.record_post_decision()
        records = self.store.snapshot()["records"]
        statement = self.selected
        expected = {"title.txt": statement["title"] + "\n", "statement.md": statement["statement"] + "\n",
            "criteria.md": statement["resolution_criteria"] + "\n", "motivation.md": "The bound would connect the finite constructions.\n",
            "state.md": "Only bounded examples have been established.\n", "formal-statement.txt": statement["formal_statement"]["text"] + "\n",
            "citations.json": json.dumps(problem_delivery.project_citations(records, self.artifacts), ensure_ascii=False, sort_keys=True, indent=2) + "\n"}
        manifest = dict(statement_id=statement["id"], statement_digest=problem_claims.compute_statement_digest(statement),
            problem_evidence_digest=problem_claims.compute_problem_evidence_digest(records, self.artifacts), decision_id=decision["id"],
            area=statement["area"], formal_statement_language="Lean", files={name: hashlib.sha256(content.encode()).hexdigest() for name, content in sorted(expected.items())})
        expected["problem-export.json"] = json.dumps(manifest, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
        response = self.export_problem("problem")
        self.assertEqual(response.returncode, 0, response.stderr)
        directory = Path(json.loads(response.stdout)["destination"])
        self.assertEqual({path.name: path.read_bytes() for path in directory.iterdir()}, {name: content.encode() for name, content in expected.items()})


class OverrideDepositMetadataTests(ProblemCase):
    def test_metadata_is_byte_identical_without_gates_and_appends_one_escaped_paragraph_with_gates(self):
        from test_draft import _draft
        for written in (True, False):
            metadata = _draft._build_deposit_metadata("Paper", ["Author <A>"], "Abstract one.\n\nAbstract two.", written)
            disclosure = (_draft._WRITTEN_BY_EXACTORY_DISCLOSURE_TEMPLATE if written else _draft._DEPOSITED_THROUGH_EXACTORY_DISCLOSURE_TEMPLATE).format(author="Author <A>")
            expected = "<p>Abstract one.</p><p>Abstract two.</p><p>" + html.escape(disclosure) + "</p>"
            self.assertEqual(metadata["description"], expected)
            explicit = _draft._build_deposit_metadata("Paper", ["Author <A>"], "Abstract one.\n\nAbstract two.", written, passed_gates=[])
            self.assertEqual(json.dumps(explicit), json.dumps(metadata))
            extra = _draft._build_deposit_metadata("Paper", ["Author <A>"], "Abstract one.\n\nAbstract two.", written, passed_gates=["publication", "readiness"])
            self.assertEqual(extra["description"], expected + "<p>" + DISCLOSURE.format(gates="publication, readiness") + "</p>")

    def test_deposit_package_lists_override_and_pinned_instruction(self):
        from research_harness.deposit_package import build_package
        self.initialize_problem()
        (self.root / "context").mkdir()
        (self.root / "context/instruction.txt").write_text(INSTRUCTION)
        self.assertIn("gate-override", cli.OPERATIONS)
        record = self.mutate(cli.OPERATIONS["gate-override"], dict(id="package-override", gate="readiness", instruction="context/instruction.txt", reason="Continue on instruction."))["result"]
        claims = self.artifacts.put(b'[{"id":"claim","claim":"A finite example."}]', "application/json")
        bundle = dict(id="paper", digest="a" * 64, files={"claims": dict(path="claims.json", artifact=claims)}, claim_evidence=[])
        self.put("publication_bundle", "paper", bundle)
        self.put("publication_selection", "bundle", dict(id="paper"))
        destination = self.root / "package.zip"
        build_package(self.store, destination)
        with zipfile.ZipFile(destination) as archive:
            package = json.loads(archive.read("package.json"))
            name = package["records"]["gate_override"]["package-override"]
            self.assertEqual(json.loads(archive.read(name)), record)
            self.assertIn(INSTRUCTION.encode(), [archive.read(name) for name in archive.namelist()])


class ActionOverrideDisclosureTests(OverrideCommandMixin, ProblemCase):
    def setUp(self):
        super().setUp()
        self.write_instruction()
        self.instruction = 'PRIVATE-CONTEXT-8492: Publish only this <record> & keep "missing reviews" visible.\n'
        (self.root / "context/instruction.txt").write_text(self.instruction)

    def record_action(self, identifier="public-write", action="POST /deposit/depositions/42/actions/publish"):
        return self.mutate(overrides.record_override,
            self.build_override(identifier, public_action=action))["result"]

    def append_action(self, identifier="local-write", action="PUT /deposit/depositions/42"):
        overrides.append_unrecorded_override(self.root, self.build_override(identifier, gate="publication",
            public_action=action), expected_revision=self.store.revision, request_id=identifier)

    def metadata(self, store, written=False):
        from test_draft import _draft
        return _draft._build_deposit_metadata("Finite result", ["Author"], "Finite abstract.", written,
            **overrides.describe_deposit_overrides(store))

    def action_paragraph(self, identifier, action, recorded):
        confirmation = ("The instruction is in the stored record." if recorded else
            "Only a local fallback entry was available; recording in the store could not be confirmed.")
        return "<p>" + html.escape(ACTION_DISCLOSURE.format(id=identifier, public_action=action,
            confirmation=confirmation)) + "</p>"

    def released_metadata(self, index=0):
        fixture = json.loads((PLUGIN / "tests/fixtures/phase_b_override_a04617f.json").read_text())
        return fixture["metadata"][index]

    def test_recorded_public_write_passes_no_gate_and_discloses_action_without_private_words(self):
        action = "POST /deposit/depositions/42/actions/publish"
        self.record_action(action=action)
        self.assertEqual(overrides.list_passed_gates(self.store), [])
        disclosure = overrides.describe_deposit_overrides(self.store)
        self.assertEqual(disclosure["passed_gates"], [])
        actual = self.metadata(self.store)
        self.assertNotIn("PRIVATE-CONTEXT-8492", json.dumps(actual))
        self.assertIn(html.escape(action), actual["description"])
        self.assertEqual(disclosure["public_action_overrides"], [dict(id="public-write", public_action=action,
            recorded=True)])
        expected = self.released_metadata()
        expected["description"] += self.action_paragraph("public-write", action, True)
        self.assertEqual(actual, expected)

    def test_local_public_write_passes_no_gate_with_missing_or_unreadable_store(self):
        action = "PUT /deposit/depositions/42"
        self.append_action(action=action)
        expected = self.released_metadata()
        expected["description"] += self.action_paragraph("local-write", action, False)
        original = Path.cwd()
        try:
            os.chdir(self.root)
            for store, unreadable in ((self.store, False), (self.store, True), (None, False)):
                with self.subTest(store=store is not None, unreadable=unreadable):
                    snapshot = mock.patch.object(Store, "snapshot", side_effect=ResearchError("storage_io", "Cannot read"))
                    if unreadable:
                        snapshot.start()
                    try:
                        self.assertEqual(overrides.list_passed_gates(store), [])
                        disclosure = overrides.describe_deposit_overrides(store)
                        self.assertEqual(disclosure["passed_gates"], [])
                        actual = self.metadata(store)
                        self.assertNotIn("PRIVATE-CONTEXT-8492", json.dumps(actual))
                        self.assertIn(html.escape(action), actual["description"])
                        self.assertEqual(disclosure["public_action_overrides"], [dict(id="local-write",
                            public_action=action, recorded=False)])
                        self.assertEqual(actual, expected)
                    finally:
                        if unreadable:
                            snapshot.stop()
        finally:
            os.chdir(original)

    def test_malformed_local_actions_use_unknown_fallback_without_losing_valid_or_legacy_rows(self):
        path = self.root / ".exactory/unrecorded-overrides.jsonl"
        first = dict(id="first", gate="publication", public_action="POST /deposit/depositions/42/actions/publish",
            instruction=dict(text=self.instruction))
        last = dict(id="last", gate="readiness", public_action="PUT /deposit/depositions/42",
            instruction=dict(text=self.instruction))
        legacy = dict(id="legacy", gate="legacy-unregistered-gate", instruction=dict(text=""), extra="retain this")
        for invalid in (None, {}, [], 7, True, "", "GET /records/42", "POST relative", "POST /write\nnext", "PUT /write\rnext"):
            with self.subTest(public_action=invalid):
                bad = dict(first, id="bad", public_action=invalid)
                contents = "\n".join(json.dumps(row) for row in (first, bad, legacy, last)) + "\n"
                path.write_text(contents)
                try:
                    metadata = self.metadata(self.store)
                except TypeError as error:
                    self.fail("A malformed local action crashed deposit metadata: " + str(error))
                rows = overrides.read_unrecorded_overrides(self.root)
                self.assertEqual((rows[0], rows[2], rows[3]), (first, legacy, last))
                self.assertEqual(rows[1]["id"], "unreadable-line-2")
                self.assertEqual(rows[1]["gate"], "unknown")
                self.assertNotIn("public_action", rows[1])
                self.assertIn("could not be read", rows[1]["instruction"]["text"])
                self.assertEqual(overrides.list_passed_gates(self.store), ["legacy-unregistered-gate", "unknown"])
                self.assertEqual(overrides.describe_deposit_overrides(self.store)["public_action_overrides"], [
                    dict(id="first", public_action=first["public_action"], recorded=False),
                    dict(id="last", public_action=last["public_action"], recorded=False)])
                expected = self.released_metadata()
                expected["description"] += "<p>" + DISCLOSURE.format(gates="legacy-unregistered-gate, unknown") + "</p>"
                expected["description"] += self.action_paragraph("first", first["public_action"], False)
                expected["description"] += self.action_paragraph("last", last["public_action"], False)
                self.assertEqual(metadata, expected)
                self.assertEqual(path.read_text(), contents)

    def test_mixed_entries_keep_gate_and_automatic_disclosures_separate_from_public_writes(self):
        self.mutate(overrides.record_override, self.build_override("gate-entry", gate="publication"))
        overrides.append_unrecorded_override(self.root, self.build_override("local-gate", gate="cohort"),
            expected_revision=self.store.revision, request_id="local-gate")
        action = "POST /deposit/depositions/42/actions/publish"
        local_action = "PUT /deposit/depositions/42"
        self.record_action(action=action)
        self.append_action(action=local_action)
        self.put("gate_override", "automatic", dict(id="automatic", authority="harness", gate="execution",
            stop_id="series-stop:<recorded>", stop_code="review_series_no_progress"))
        reports = self.root / ".exactory/automatic-passes"
        reports.mkdir()
        (reports / "local.json").write_text(json.dumps(dict(override_id="local-automatic", gate="readiness",
            stop_id="series-stop:<local>", stop_code="review_series_no_progress")))
        disclosure = overrides.describe_deposit_overrides(self.store)
        self.assertEqual(disclosure["passed_gates"], ["cohort", "publication"])
        self.assertEqual(overrides.list_passed_gates(self.store), ["cohort", "execution", "publication"])
        self.assertEqual(disclosure["automatic_passes"], [
            dict(gate="execution", stop_id="series-stop:<recorded>", stop_code="review_series_no_progress", recorded=True),
            dict(gate="readiness", stop_id="series-stop:<local>", stop_code="review_series_no_progress", recorded=False)])
        expected = self.released_metadata()
        expected["description"] += "<p>" + DISCLOSURE.format(gates="cohort, publication") + "</p>"
        for gate, stop_id, confirmation in (
                ("execution", "series-stop:<recorded>", "The stored record confirms that the harness advanced the study to its next internal stage."),
                ("readiness", "series-stop:<local>", "Only a local report was available; a committed continuation could not be confirmed.")):
            expected["description"] += "<p>" + html.escape(AUTOMATIC_DISCLOSURE.format(gate=gate,
                stop_id=stop_id, stop_code="review_series_no_progress", confirmation=confirmation)) + "</p>"
        expected["description"] += self.action_paragraph("public-write", action, True)
        expected["description"] += self.action_paragraph("local-write", local_action, False)
        actual = self.metadata(self.store)
        self.assertNotIn("PRIVATE-CONTEXT-8492", json.dumps(actual))
        self.assertEqual(actual, expected)

    def test_no_public_write_entries_preserve_released_metadata_bytes_with_gates_and_automatic_passes(self):
        for authored in (False, True):
            for gate in (False, True):
                for automatic in (False, True):
                    with self.subTest(authored=authored, gate=gate, automatic=automatic):
                        records = {}
                        if gate:
                            records["gate"] = dict(id="gate", gate="publication", authority="user")
                        if automatic:
                            records["automatic"] = dict(id="automatic", gate="readiness", authority="harness",
                                stop_id="series-stop:baseline", stop_code="review_series_no_progress")
                        with mock.patch.object(Store, "snapshot", return_value={"records": {"gate_override": records}}):
                            disclosure = overrides.describe_deposit_overrides(self.store)
                            self.assertEqual(set(disclosure), {"passed_gates", "automatic_passes"})
                            actual = self.metadata(self.store, authored)
                        expected = self.released_metadata(2 * int(authored) + int(gate))
                        if automatic:
                            expected["description"] += "<p>" + AUTOMATIC_DISCLOSURE.format(gate="readiness",
                                stop_id="series-stop:baseline", stop_code="review_series_no_progress",
                                confirmation="The stored record confirms that the harness advanced the study to its next internal stage.") + "</p>"
                        self.assertEqual(json.dumps(actual).encode(), json.dumps(expected).encode())
