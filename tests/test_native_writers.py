"""CLI publication modes and compatibility with writable record-check copies."""

import hashlib
import json
import os
from pathlib import Path
import stat
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

from research_harness.record_check import check_record, copy_snapshot


PLUGIN = Path(__file__).resolve().parents[1]


def assert_rejects_direct_writes(test, path):
    before = path.read_bytes()
    test.assertEqual(path.stat().st_mode & 0o222, 0)
    for operator in (">", ">>"):
        result = subprocess.run(
            ["/bin/sh", "-c", 'echo changed ' + operator + ' "$1"', "sh", str(path)],
            capture_output=True, text=True,
        )
        test.assertNotEqual(result.returncode, 0, result)
        test.assertIn("Permission denied", result.stderr)
    with test.assertRaises(PermissionError):
        with open(path, "a") as stream:
            stream.write("changed")
    test.assertEqual(path.read_bytes(), before)


class ReportAndHookNativeWritersTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        (self.root / ".exactory").mkdir()
        (self.root / "draft").mkdir()
        (self.root / ".exactory/draft.json").write_text("{}")

    def run_command(self, relative, *args, payload=None, environment=None):
        result = subprocess.run(
            [sys.executable, "-I", "-B", str(PLUGIN / relative), *args],
            input=json.dumps(payload) if payload is not None else None,
            cwd=self.root, capture_output=True, text=True,
            env={**os.environ, **(environment or {})},
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stderr, "")
        return result.stdout

    def test_lookup_republishes_legacy_reports_and_cache_readonly_and_gate_accepts(self):
        bibliography = self.root / "draft/references.bib"
        bibliography.write_text(
            "@article{sample, title={Synthetic citation}, author={Example, Alice}, "
            "year={2026}, doi={10.1234/synthetic}}\n"
        )
        (self.root / "draft/paper.tex").write_text(
            "\\documentclass{article}\n\\begin{document}\\cite{sample}\\end{document}\n"
        )
        report = self.root / ".exactory/citation-check.json"
        cache = self.root / ".exactory/citation-cache.json"
        cache.write_text(json.dumps({"version": 2, "records": {"doi:10.1234/synthetic": {
            "source": "crossref", "title": "Synthetic citation", "titles": ["Synthetic citation"],
            "authors": ["Alice Example"], "year": 2026, "doi": "10.1234/synthetic",
        }}}))
        # A writable report and cache from an earlier release remain readable.
        report.write_text("{}")
        for _ in range(2):
            output = self.run_command("bin/exactory-check", "lookup")
            self.assertTrue(json.loads(report.read_text())["ok"], output)
            for path in (report, cache):
                assert_rejects_direct_writes(self, path)
            self.assertIn("Citation gate: pass", self.run_command("bin/exactory-check", "gate"))
        with bibliography.open("a") as stream:
            stream.write("% still author writable\n")

    def test_derivation_report_rewrites_legacy_and_readonly_files(self):
        steps = self.root / "steps.json"
        report = self.root / ".exactory/derivation-check.json"
        report.write_text("{}")
        for value in (2, 3):
            steps.write_text(json.dumps([{
                "label": "constant " + str(value), "from": str(value), "to": str(value), "vars": {},
            }]))
            output = self.run_command("bin/exactory-derive", "check", "--steps-file", str(steps))
            self.assertEqual(json.loads(report.read_text()), json.loads(output))
            self.assertEqual(json.loads(output)["steps"][0]["label"], "constant " + str(value))
            assert_rejects_direct_writes(self, report)

    def test_authorship_hooks_republish_legacy_evidence_readonly(self):
        record = self.root / ".exactory/authorship.json"
        for host in ("direct", "codex"):
            with self.subTest(host=host):
                record.unlink(missing_ok=True)
                record.write_text('{"written_by_exactory": false}')
                payload = {"cwd": str(self.root), "hook_event_name": "PostToolUse",
                           "tool_name": "Write", "tool_input": {"file_path": "draft/paper.tex"}}
                command = ("hooks/record_paper_authorship.py",)
                if host == "codex":
                    command = ("codex/hook.py", "record_paper_authorship.py")
                self.assertEqual(self.run_command(*command, payload=payload), "")
                self.assertEqual(json.loads(record.read_text()), {"written_by_exactory": True})
                assert_rejects_direct_writes(self, record)
                self.assertEqual(self.run_command(*command, payload=payload), "")
                # A person can replace the record to turn the claim off again.
                record.unlink()
                record.write_text('{"written_by_exactory": false}')
                record.chmod(0o400)
                self.run_command(*command, payload=payload)
                assert_rejects_direct_writes(self, record)

    def test_authorship_hooks_harden_true_legacy_records_without_changing_bytes(self):
        record = self.root / ".exactory/authorship.json"
        original = b'{"written_by_exactory": true, "note": "kept"}\r\n'
        payload = {"cwd": str(self.root), "hook_event_name": "PostToolUse",
                   "tool_name": "Write", "tool_input": {"file_path": "draft/paper.tex"}}
        for command in (("hooks/record_paper_authorship.py",),
                        ("codex/hook.py", "record_paper_authorship.py")):
            with self.subTest(command=command):
                record.unlink(missing_ok=True)
                record.write_bytes(original)
                self.assertEqual(self.run_command(*command, payload=payload), "")
                self.assertEqual(record.read_bytes(), original)
                assert_rejects_direct_writes(self, record)
                inode = record.stat().st_ino
                self.assertEqual(self.run_command(*command, payload=payload), "")
                self.assertEqual(record.stat().st_ino, inode)

    def test_autopilot_hooks_count_pause_resume_and_reset_readonly_legacy_counters(self):
        counter = self.root / ".exactory/autopilot_count"
        state_path = self.root / ".exactory/study.json"
        state = {"slug": "sample", "stage": "experiment", "status": "running",
                 "autopilot": True, "waiting": None}
        for host in ("direct", "codex"):
            with self.subTest(host=host):
                counter.unlink(missing_ok=True)
                counter.write_text("0001\r\n")
                state_path.write_text(json.dumps(state))
                payload = {"cwd": str(self.root), "hook_event_name": "Stop", "stop_hook_active": False}
                command = ("hooks/continue_autopilot.py",)
                if host == "codex":
                    command = ("codex/hook.py", "continue_autopilot.py")
                environment = {"EXACTORY_AUTOPILOT_MAX": "2", "EXACTORY_ASSIGNED_SESSION": ""}
                result = json.loads(self.run_command(*command, payload=payload, environment=environment))
                self.assertIn("advance 2/2", result["reason"])
                self.assertEqual(counter.read_text(), "2")
                assert_rejects_direct_writes(self, counter)
                payload["stop_hook_active"] = True
                result = json.loads(self.run_command(*command, payload=payload, environment=environment))
                self.assertIn("safety cap", result["reason"])
                self.assertEqual(counter.read_text(), "-1")
                assert_rejects_direct_writes(self, counter)
                self.assertEqual(self.run_command(*command, payload=payload, environment=environment), "")
                payload["stop_hook_active"] = False
                self.run_command(*command, payload=payload, environment=environment)
                self.assertEqual(counter.read_text(), "1")
                state_path.write_text(json.dumps(dict(state, waiting="approval")))
                self.assertEqual(self.run_command(*command, payload=payload, environment=environment), "")
                self.assertEqual(counter.read_text(), "0")
                assert_rejects_direct_writes(self, counter)


class ResearchNativeWritersTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name) / "record"
        self.root.mkdir()
        payload = self.root / "init.json"
        payload.write_text(json.dumps({"profile": "research", "target": None}))
        result = subprocess.run([sys.executable, str(PLUGIN / "bin/exactory-research"), "init",
                                 "--workspace", str(self.root), "--file", str(payload),
                                 "--expected-revision", "0", "--request-id", "init"],
                                cwd=PLUGIN, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_cli_output_views_and_compatibility_projections_are_readonly(self):
        paths = [path for folder in ("final", "intermediate") for path in (self.root / folder).rglob("*")
                 if path.is_file()]
        paths += [path for path in (self.root / ".exactory").glob("*.json")]
        self.assertGreater(len(paths), 4)
        for path in paths:
            with self.subTest(path=path):
                self.assertEqual(path.stat().st_mode & 0o222, 0)
                with self.assertRaises(PermissionError):
                    path.open("a")
        self.assertTrue(os.access(self.root / ".exactory/research.sqlite3", os.W_OK))

    def test_native_publication_does_not_add_group_or_other_read_access(self):
        marker = self.root / ".exactory/output-views.json"
        self.assertEqual(marker.stat().st_mode & 0o077, 0)

    def test_old_writable_record_passes_record_check_without_source_changes(self):
        for path in self.root.rglob("*"):
            if path.is_file():
                path.chmod(path.stat().st_mode | 0o200)
        before = {path.relative_to(self.root): (hashlib.sha256(path.read_bytes()).hexdigest(),
                                               path.stat().st_mode) for path in self.root.rglob("*") if path.is_file()}
        report = check_record(self.root, isolate=False)
        self.assertTrue(report["ready"], report)
        after = {path.relative_to(self.root): (hashlib.sha256(path.read_bytes()).hexdigest(),
                                              path.stat().st_mode) for path in self.root.rglob("*") if path.is_file()}
        self.assertEqual(after, before)

    def test_record_check_makes_its_copy_writable_and_native_render_republishes_readonly(self):
        clone = self.root.parent / "copy"
        copy_snapshot(self.root, clone)
        marker = clone / ".exactory/output-views.json"
        self.assertTrue(marker.stat().st_mode & 0o200)
        result = subprocess.run([sys.executable, str(PLUGIN / "bin/exactory-research"), "export",
                                 "--workspace", str(clone), "--kind", "outputs"],
                                cwd=PLUGIN, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(marker.stat().st_mode & 0o222, 0)

    def test_concurrent_activity_appends_keep_every_entry_under_the_directory_lock(self):
        result = subprocess.run([sys.executable, str(PLUGIN / "bin/exactory-math"), "init", "sample"],
                                cwd=self.root, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        workspace = self.root / "attack/sample"
        processes = []
        for number in range(12):
            process = subprocess.Popen([sys.executable, str(PLUGIN / "hooks/record_attack_activity.py")],
                                       stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                       text=True, cwd=self.root)
            payload = {"tool_name": "Edit", "tool_input": {"file_path": str(workspace / (str(number) + ".md"))},
                       "tool_response": {"success": True}, "cwd": str(self.root), "session_id": "solver"}
            process.stdin.write(json.dumps(payload))
            process.stdin.close()
            process.stdin = None
            processes.append(process)
        try:
            for process in processes:
                output, errors = process.communicate(timeout=30)
                self.assertEqual((process.returncode, output, errors), (0, "", ""))
        finally:
            for process in processes:
                if process.poll() is None:
                    process.kill()
                process.communicate()
        log = workspace / "activity.jsonl"
        entries = [json.loads(line) for line in log.read_text().splitlines()]
        self.assertEqual(len(entries), 12)
        self.assertEqual({entry["target"] for entry in entries}, {str(number) + ".md" for number in range(12)})
        with self.assertRaises(PermissionError):
            log.open("a")

    def test_process_exit_before_rename_leaves_previous_cli_view_intact(self):
        target = self.root / "final/README.md"
        before = target.read_bytes()
        entries = set(target.parent.iterdir())
        script = """
import os
import sys
from pathlib import Path
from unittest.mock import patch
from research_harness.native_writes import write_atomic
target = Path(sys.argv[1])
def interrupt(source, destination):
    temporary = Path(source)
    assert temporary.parent == target.parent
    assert temporary.read_bytes() == b'complete replacement'
    assert not temporary.stat().st_mode & 0o222
    os._exit(73)
with patch.object(os, 'replace', interrupt):
    write_atomic(target, b'complete replacement')
"""
        result = subprocess.run([sys.executable, "-c", script, str(target)], cwd=PLUGIN,
                                capture_output=True, text=True)
        self.assertEqual((result.returncode, result.stderr), (73, ""))
        self.assertEqual(target.read_bytes(), before)

        leftovers = set(target.parent.iterdir()) - entries
        self.assertEqual(len(leftovers), 1)
        for path in leftovers:
            self.assertEqual(path.read_bytes(), b"complete replacement")
            self.assertEqual(path.stat().st_mode & 0o222, 0)
        result = subprocess.run([sys.executable, str(PLUGIN / "bin/exactory-research"), "export",
                                 "--workspace", str(self.root), "--kind", "outputs"],
                                cwd=PLUGIN, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(target.read_bytes(), before)
        for path in leftovers:
            path.unlink()

    def test_failed_file_sync_preserves_previous_cli_view_and_removes_temporary(self):
        from research_harness.workspace import write_projection
        target = self.root / "final/README.md"
        before = target.read_bytes()
        entries = set(target.parent.iterdir())
        synchronize = os.fsync

        def fail_file_sync(descriptor):
            if stat.S_ISREG(os.fstat(descriptor).st_mode):
                raise OSError("sync failed")
            return synchronize(descriptor)

        with mock.patch.object(os, "fsync", side_effect=fail_file_sync):
            with self.assertRaisesRegex(OSError, "sync failed"):
                write_projection(self.root, "final/README.md", b"replacement")
        self.assertEqual(target.read_bytes(), before)
        self.assertEqual(set(target.parent.iterdir()), entries)
