"""Recorded controller histories distinguish runtime mismatches from damaged records."""

import copy
import json
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
HARNESS = ROOT / "skills/math-solver/harness"
if str(HARNESS) not in sys.path:
    sys.path.insert(0, str(HARNESS))

from search_controller.errors import SearchError
from search_controller.model import replay
from search_controller.storage import Store


class MathHookReplayTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="recorded math hook ")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        (self.root / ".search").mkdir()
        self.tree = self.root / ".search/tree.json"
        fixture = ROOT / "tests/fixtures/math-search/standalone-finalize-begin.json"
        self.document = json.loads(fixture.read_text())
        self.version = json.loads((ROOT / ".claude-plugin/plugin.json").read_text())["version"]
        self.writer = {"plugin_version": "0.64.0-local", "controller_digest": "f" * 64,
                       "launcher": str(self.root / "other runtime/bin/exactory-math")}

    def write_record(self, runtime=None, document=None):
        recorded = copy.deepcopy(self.document if document is None else document)
        sidecar_path = self.root / ".search/writer-runtime.json"
        if runtime is not None:
            sidecar_path.write_text(json.dumps(runtime))
        elif sidecar_path.exists():
            sidecar_path.unlink()
        self.tree.write_text(json.dumps(recorded))

    def read_installed_runtime(self):
        store = Store(self.root / "identity-store")
        store.initialize({}, "fixture-identity")
        sidecar_path = store.root / "writer-runtime.json"
        self.assertTrue(sidecar_path.is_file(), "Every committed root must record its writer in a sidecar")
        return json.loads(sidecar_path.read_text())

    def invoke_hook(self, script, host="claude", **fields):
        payload = {"session_id": "fixture-session", "cwd": str(self.root), **fields}
        argv = [sys.executable, str(ROOT / "hooks" / script)]
        if host == "codex":
            argv = [sys.executable, str(ROOT / "codex/hook.py"), script]
        result = subprocess.run(argv, input=json.dumps(payload), capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stderr)
        return json.loads(result.stdout) if result.stdout.strip() else None

    def read_denial(self, host="claude", tool="Write", raw=None):
        if raw is None:
            raw = {"file_path": str(self.root / "notes.md")}
        output = self.invoke_hook("guard_attack_files.py", host, hook_event_name="PreToolUse",
                                  tool_name=tool, tool_input=raw)
        self.assertIsNotNone(output, "An unreadable managed record must keep the guard closed")
        self.assertEqual(output["hookSpecificOutput"]["permissionDecision"], "deny")
        return output["hookSpecificOutput"]["permissionDecisionReason"]

    def read_stop_context(self, host="claude"):
        output = self.invoke_hook("continue_attack.py", host, hook_event_name="Stop")
        self.assertIsNotNone(output)
        self.assertNotEqual(output.get("decision"), "block")
        return output["hookSpecificOutput"]["additionalContext"]

    def assert_reads_are_exempt(self, host):
        for tool, raw in [("Read", {"file_path": str(self.tree)}), ("Glob", {"pattern": "**/*"}),
                          ("Grep", {"pattern": "fixture"}), ("WebSearch", {"query": "fixture sources"}),
                          ("WebFetch", {"url": "https://example.invalid/fixture"})]:
            with self.subTest(host=host, read=tool):
                self.assertIsNone(self.invoke_hook("guard_attack_files.py", host, hook_event_name="PreToolUse",
                                                    tool_name=tool, tool_input=raw))

    def test_recorded_begin_mismatch_names_both_runtimes_and_keeps_guards_closed(self):
        with self.assertRaises(SearchError) as caught:
            replay(self.document)
        self.assertEqual(caught.exception.code, "frontier_required")
        for host in ("claude", "codex"):
            for version in (self.writer["plugin_version"], self.version):
                writer = dict(self.writer, plugin_version=version)
                self.write_record(writer)
                before = self.tree.read_bytes()
                messages = [self.read_denial(host, tool) for tool in ("Write", "Edit", "Delete")]
                messages.append(self.read_denial(host, "exec_command", {"cmd": "ls notes.md"}))
                messages.append(self.read_stop_context(host))
                for message in messages:
                    with self.subTest(host=host, version=version, message=message):
                        self.assertIn("cannot read a record written by", message)
                        self.assertIn(version, message)
                        self.assertIn(self.version, message)
                        self.assertIn(writer["controller_digest"], message)
                        self.assertIn(writer["launcher"], message)
                        self.assertIn(str(ROOT / "bin/exactory-math"), message)
                        self.assertEqual(len(re.findall(r"controller sha256 [0-9a-f]{64}", message)), 2)
                        self.assertIn("frontier_required", message)
                        self.assertIn("--attack-root", message)
                        self.assertIn("search", message)
                        self.assertNotIn("recover", message.lower())
                self.assert_reads_are_exempt(host)
                self.assertEqual(self.tree.read_bytes(), before)

    def test_recorded_begin_without_runtime_reports_unreadable_record_without_recovery(self):
        self.write_record()
        before = self.tree.read_bytes()
        for host in ("claude", "codex"):
            for message in (self.read_denial(host), self.read_stop_context(host)):
                self.assertIn("cannot read this record", message)
                self.assertIn(str(ROOT / "bin/exactory-math"), message)
                self.assertIn(self.version, message)
                self.assertIn("frontier_required", message)
                self.assertNotIn("recover", message.lower())
            self.assert_reads_are_exempt(host)
        self.assertEqual(self.tree.read_bytes(), before)

    def test_damaged_event_under_its_own_runtime_keeps_recovery_after_launcher_relocation(self):
        installed = self.read_installed_runtime()
        damaged = copy.deepcopy(self.document)
        damaged["events"][0]["payload"]["proposal"]["category"] = "damaged-category"
        for launcher in (installed["launcher"], str(self.root / "relocated/bin/exactory-math")):
            self.write_record(dict(installed, launcher=launcher), damaged)
            before = self.tree.read_bytes()
            for host in ("claude", "codex"):
                for message in (self.read_denial(host), self.read_stop_context(host)):
                    self.assertIn("Controller recovery required", message)
                    self.assertIn("invalid_record", message)
                    self.assertNotIn("cannot read", message)
                self.assert_reads_are_exempt(host)
            self.assertEqual(self.tree.read_bytes(), before)

    def test_replayable_record_from_another_runtime_does_not_refuse_an_allowed_write(self):
        document = dict(self.document, events=self.document["events"][:-1])
        self.write_record(self.writer, document)
        before = self.tree.read_bytes()
        for host in ("claude", "codex"):
            self.assertIsNone(self.invoke_hook("guard_attack_files.py", host, hook_event_name="PreToolUse",
                                               tool_name="Write", tool_input={"file_path": "notes.md"}))
            message = self.read_stop_context(host)
            self.assertIn("focus_required", message)
            self.assertIn("recover", message.lower())
            self.assertNotIn("cannot read", message)
        self.assertEqual(self.tree.read_bytes(), before)
        registry_path = self.root / ".exactory/math-search.json"
        registry_path.parent.mkdir()
        registry_path.write_text(json.dumps({"schema_version": 1, "sessions": {}, "roots": [
            {"path": str(self.root), "objective_id": "another-objective", "contract_digest": "a" * 64}]}))
        for host in ("claude", "codex"):
            message = self.read_denial(host)
            self.assertIn("discovery_conflict", message)
            self.assertIn("recover", message.lower())
            self.assertNotIn("cannot read", message)
        self.assertEqual(self.tree.read_bytes(), before)

    def test_malformed_record_without_readable_identity_does_not_claim_recovery(self):
        self.tree.write_text("{invalid")
        for message in (self.read_denial(), self.read_stop_context()):
            self.assertIn("cannot read this record", message)
            self.assertNotIn("recover", message.lower())

    def test_malformed_or_missing_sidecar_uses_unknown_writer_for_a_damaged_record(self):
        from search_controller.runtime import read_runtime_identity
        damaged = copy.deepcopy(self.document)
        damaged["events"][0]["payload"]["proposal"]["category"] = "damaged-category"
        self.write_record(read_runtime_identity(), damaged)
        sidecar_path = self.root / ".search/writer-runtime.json"
        for message in (self.read_denial(), self.read_stop_context()):
            self.assertIn("Controller recovery required", message)
        before = self.tree.read_bytes()
        duplicate_version = json.dumps(read_runtime_identity())[:-1] + ',"plugin_version":"duplicate"}'
        malformed_values = ["{invalid", "[]", "{}", '{"plugin_version":"fixture"}', duplicate_version, "[" * 2000]
        for malformed in malformed_values + [None]:
            with self.subTest(sidecar=malformed):
                if malformed is None:
                    sidecar_path.unlink()
                else:
                    sidecar_path.write_text(malformed)
                for message in (self.read_denial(), self.read_stop_context()):
                    self.assertIn("cannot read this record", message)
                    self.assertNotIn("recover", message.lower())
        self.assertEqual(self.tree.read_bytes(), before)


if __name__ == "__main__":
    unittest.main()
