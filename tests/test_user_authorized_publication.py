"""The shared decision point of submit, verify, and deposit: managed when the
workspace supports a receipt, direct otherwise, never refused."""

from __future__ import annotations

import contextlib
import io
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from research_harness.cli import note_managed_record_skipped, select_managed_path
from research_harness.citation_report import report_citation_gate
from research_harness.errors import ResearchError
from research_harness.storage import Store

_PLUGIN_ROOT = Path(__file__).resolve().parent.parent
_CHECK_COMMAND_PATH = _PLUGIN_ROOT / "bin" / "exactory-check"
_TESTS_DIR = Path(__file__).resolve().parent
if str(_TESTS_DIR) not in sys.path:
    sys.path.insert(0, str(_TESTS_DIR))


def _pass_check(store):
    return "checked at revision " + str(store.revision)


def _fail_check(store):
    raise ResearchError("readiness_required", "Publish the reviewed bundle first")


class TestSelectManagedPath(unittest.TestCase):
    def setUp(self) -> None:
        scratch = tempfile.TemporaryDirectory()
        self.addCleanup(scratch.cleanup)
        self.root = Path(scratch.name)

    def _select(self, check, start=None) -> tuple[tuple[object, object, object], str]:
        sink = io.StringIO()
        with contextlib.redirect_stderr(sink):
            selected = select_managed_path(check, start or self.root)
        return selected, sink.getvalue()

    def test_no_workspace_selects_the_direct_path_silently(self) -> None:
        self.assertEqual(self._select(_pass_check), ((None, None, None), ""))

    def test_a_legacy_draft_workspace_without_a_store_selects_the_direct_path_silently(self) -> None:
        (self.root / ".exactory").mkdir()
        (self.root / ".exactory" / "draft.json").write_text(json.dumps({"title": "Legacy"}))
        self.assertEqual(self._select(_pass_check), ((None, None, None), ""))

    def test_a_store_without_a_research_configuration_selects_the_direct_path_silently(self) -> None:
        Store(self.root, create=True)
        self.assertEqual(self._select(_pass_check), ((None, None, None), ""))

    def test_a_corrupt_store_is_noted_once_and_names_the_failure_that_kept_it_shut(self) -> None:
        (self.root / ".exactory").mkdir()
        (self.root / ".exactory" / "research.sqlite3").write_bytes(b"not a database")
        (store, report, store_open_error), stderr_text = self._select(_pass_check)
        self.assertIsNone(store)
        self.assertIsNone(report)
        # A store file that exists and does not open is not the same as no store:
        # a record the command keeps in its own file is the only copy of it.
        self.assertEqual(store_open_error.code, "corrupt_state")
        self.assertEqual(store_open_error.message, "Research database has an invalid SQLite header")
        self.assertEqual(stderr_text,
                         "Managed record skipped (corrupt_state): Research database has an invalid SQLite header\n")

    def test_a_failing_check_is_noted_and_hands_back_the_open_store(self) -> None:
        from integration_fixtures import prepare_research
        case = prepare_research(self.root)
        (store, report, store_open_error), stderr_text = self._select(_fail_check)
        # The direct path writes its own record through this store, so the
        # command never opens the workspace a second time to decide again.
        self.assertEqual(store.revision, case.store.revision)
        self.assertIsNone(report)
        self.assertIsNone(store_open_error)
        self.assertEqual(stderr_text,
                         "Managed record skipped (readiness_required): Publish the reviewed bundle first\n")

    def test_a_passing_check_selects_the_managed_path_from_a_subdirectory(self) -> None:
        from integration_fixtures import prepare_research
        case = prepare_research(self.root)
        nested = self.root / "draft" / "figures"
        nested.mkdir(parents=True)
        (store, checked, store_open_error), stderr_text = self._select(_pass_check, nested)
        self.assertEqual(store.revision, case.store.revision)
        self.assertEqual(checked, "checked at revision " + str(case.store.revision))
        self.assertIsNone(store_open_error)
        self.assertEqual(stderr_text, "")


class TestNote(unittest.TestCase):
    def test_the_note_names_the_code_and_the_message(self) -> None:
        sink = io.StringIO()
        with contextlib.redirect_stderr(sink):
            note_managed_record_skipped(ResearchError("readiness_required", "Publish the reviewed bundle first"))
        self.assertEqual(sink.getvalue(),
                         "Managed record skipped (readiness_required): Publish the reviewed bundle first\n")

    def test_the_note_names_the_pending_obligation_codes(self) -> None:
        sink = io.StringIO()
        error = ResearchError("readiness_required", "Current research readiness is required for deposit",
                              {"obligations": [{"code": "round_decision_missing", "explanation": "Decide."},
                                               {"code": "manuscript_reviews_required", "explanation": "Review."}]})
        with contextlib.redirect_stderr(sink):
            note_managed_record_skipped(error)
        self.assertEqual(sink.getvalue(),
                         "Managed record skipped (readiness_required): Current research readiness is required for deposit"
                         " Pending: round_decision_missing, manuscript_reviews_required.\n")

    def test_the_cli_reference_documents_both_parts_of_the_line(self) -> None:
        line_template = "Managed record skipped (<code>): <message>"
        pending_template = " Pending: <codes>."
        reference = (_PLUGIN_ROOT / "docs" / "research-cli.md").read_text(encoding="utf-8")
        self.assertIn("`" + line_template + "`", reference)
        self.assertIn("`" + pending_template + "`", reference)
        sink = io.StringIO()
        with contextlib.redirect_stderr(sink):
            note_managed_record_skipped(ResearchError("readiness_required", "Publish the reviewed bundle first",
                                                      {"obligations": [{"code": "round_decision_missing"}]}))
        self.assertEqual(sink.getvalue(),
                         line_template.replace("<code>", "readiness_required")
                         .replace("<message>", "Publish the reviewed bundle first")
                         + pending_template.replace("<codes>", "round_decision_missing") + "\n")


class TestCitationReport(unittest.TestCase):
    def setUp(self) -> None:
        scratch = tempfile.TemporaryDirectory()
        self.addCleanup(scratch.cleanup)
        self.workspace = Path(scratch.name)
        (self.workspace / ".exactory").mkdir()
        (self.workspace / "draft").mkdir()
        (self.workspace / ".exactory" / "draft.json").write_text(json.dumps({"title": "Report"}))

    def test_a_failing_gate_is_printed(self) -> None:
        sink = io.StringIO()
        with contextlib.redirect_stderr(sink):
            report_citation_gate(self.workspace, _CHECK_COMMAND_PATH)
        self.assertTrue(sink.getvalue().startswith("Citation report: "))
        self.assertIn("references.bib", sink.getvalue())

    def test_a_passing_gate_prints_nothing(self) -> None:
        from test_transport import _write_passing_citation_report
        _write_passing_citation_report(self.workspace)
        sink = io.StringIO()
        with contextlib.redirect_stderr(sink):
            report_citation_gate(self.workspace, _CHECK_COMMAND_PATH)
        self.assertEqual(sink.getvalue(), "")


class TestCommandHelp(unittest.TestCase):
    """`--help` prints the module docstring, so it states the paths the tests
    above measure: a refused receipt prints one line, a workspace with no store
    prints none, and the citation check prints only when it fails."""

    def _read_help(self, command: str) -> str:
        completed = subprocess.run([sys.executable, str(_PLUGIN_ROOT / "bin" / command), "--help"],
                                   capture_output=True, text=True, check=True)
        return " ".join(completed.stdout.split())

    def test_the_deposit_help_separates_the_noted_refusal_from_the_silent_deposit(self) -> None:
        help_text = self._read_help("exactory-draft")
        self.assertIn("a workspace that refuses the receipt deposits directly and prints one"
                      " Managed record skipped line", help_text)
        self.assertIn("a workspace with no store deposits directly with no line", help_text)

    def test_the_help_of_both_commands_states_that_only_a_failing_check_prints(self) -> None:
        for command in ("exactory", "exactory-draft"):
            with self.subTest(command=command):
                help_text = self._read_help(command)
                self.assertIn("runs the offline citation check (exactory-check gate)", help_text)
                self.assertIn("a failing check prints one Citation report line", help_text)


if __name__ == "__main__":
    unittest.main()
