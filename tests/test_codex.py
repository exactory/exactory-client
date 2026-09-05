"""Codex packaging and real shared-hook behavior through the Codex adapter."""
from __future__ import annotations

import hashlib
import contextlib
import io
import json
from pathlib import Path
import runpy
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent


class TestCodexPackage(unittest.TestCase):
    def test_manifest_selects_codex_entrypoints_and_hooks(self):
        manifest = json.loads((ROOT / ".codex-plugin/plugin.json").read_text())
        claude = json.loads((ROOT / ".claude-plugin/plugin.json").read_text())
        self.assertEqual(manifest["name"], claude["name"])
        self.assertEqual(manifest["version"], claude["version"])
        self.assertEqual(manifest["skills"], "./codex/skills/")
        self.assertEqual(manifest["hooks"], "./codex/hooks.json")

    def test_codex_entries_reference_every_shared_skill(self):
        for path in (ROOT / "skills").glob("*/SKILL.md"):
            with self.subTest(skill=path.parent.name):
                entry = ROOT / "codex/skills" / path.parent.name / "SKILL.md"
                text = entry.read_text()
                self.assertIn(f"name: {path.parent.name}\n", text)
                self.assertIn("../../../skills/", text)
                self.assertIn(f"skills/{path.parent.name}/SKILL.md", text)
                self.assertIn("../../README.md", text)

    def test_codex_routes_every_existing_hook_through_the_adapter(self):
        original = json.loads((ROOT / "hooks/hooks.json").read_text())["hooks"]
        adapted = json.loads((ROOT / "codex/hooks.json").read_text())["hooks"]
        for event, groups in original.items():
            expected = [h["command"].split("/")[-1].rstrip('"')
                        for g in groups for h in g["hooks"]]
            actual = [h["command"].split()[-1] for g in adapted[event]
                      for h in g["hooks"] if "hook.py" in h["command"]]
            self.assertCountEqual(actual, expected)
        self.assertIn("apply_patch", json.dumps(adapted["PreToolUse"]))
        self.assertIn("apply_patch", json.dumps(adapted["PostToolUse"]))

    def test_generated_files_are_current(self):
        process = subprocess.run([sys.executable, str(ROOT / "codex/generate.py"), "--check"],
                                 text=True, capture_output=True, timeout=60)
        self.assertEqual(process.returncode, 0, process.stderr)

    def test_generator_detects_changed_descriptions_and_removed_skills(self):
        namespace = runpy.run_path(str(ROOT / "codex/generate.py"))
        with tempfile.TemporaryDirectory() as scratch:
            root = Path(scratch)
            (root / "skills/sample").mkdir(parents=True)
            (root / "hooks").mkdir()
            source = root / "skills/sample/SKILL.md"
            source.write_text("---\ndescription: Sample workflow\n---\nBody stays shared.\n")
            (root / "hooks/hooks.json").write_bytes((ROOT / "hooks/hooks.json").read_bytes())
            with patch.dict(namespace["main"].__globals__, {"ROOT": root}):
                with patch.object(sys, "argv", ["generate.py"]):
                    self.assertEqual(namespace["main"](), 0)
                with patch.object(sys, "argv", ["generate.py", "--check"]):
                    self.assertEqual(namespace["main"](), 0)
                    source.write_text("---\ndescription: Changed workflow\n---\nBody stays shared.\n")
                    with contextlib.redirect_stderr(io.StringIO()):
                        self.assertEqual(namespace["main"](), 1)
                source.unlink()
                with patch.object(sys, "argv", ["generate.py", "--check"]):
                    with contextlib.redirect_stderr(io.StringIO()):
                        self.assertEqual(namespace["main"](), 1)


class TestCodexHooks(unittest.TestCase):
    def setUp(self):
        scratch = tempfile.TemporaryDirectory(prefix="exactory codex ")
        self.addCleanup(scratch.cleanup)
        self.root = Path(scratch.name)
        self.attack = self.root / "attack/sample"
        (self.attack / "units/1").mkdir(parents=True)
        (self.attack / "problem.json").write_text('{"claim":"sample"}')
        (self.attack / "journal.jsonl").write_text("")

    def run_hook(self, name, command, event="PreToolUse", tool="apply_patch"):
        proc = subprocess.run(
            [sys.executable, str(ROOT / "codex/hook.py"), name],
            input=json.dumps({"hook_event_name": event, "tool_name": tool,
                              "tool_input": {"command": command}, "cwd": str(self.root)}),
            capture_output=True, text=True, timeout=60,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        return json.loads(proc.stdout) if proc.stdout.strip() else None

    def assert_denied(self, output, text):
        self.assertIsNotNone(output)
        decision = output["hookSpecificOutput"]
        self.assertEqual(decision["permissionDecision"], "deny")
        self.assertIn(text, decision["permissionDecisionReason"])

    def patch(self, body):
        return "*** Begin Patch\n" + body + "\n*** End Patch"

    def test_add_update_delete_and_move_protect_the_harness_records(self):
        bodies = [
            "*** Add File: attack/sample/tasks.json\n+{}",
            "*** Update File: attack/sample/journal.jsonl\n@@\n+{}",
            "*** Delete File: attack/sample/journal.jsonl",
            "*** Update File: attack/sample/notes.md\n*** Move to: attack/sample/tasks.json\n@@\n+x",
            "*** Update File: attack/sample/journal.jsonl\n*** Move to: notes.md\n@@\n+x",
            "*** Add File: safe.md\n+ok\n*** Delete File: attack/sample/journal.jsonl",
        ]
        for body in bodies:
            with self.subTest(body=body):
                self.assert_denied(self.run_hook("guard_attack_files.py", self.patch(body)), "harness")

    def test_safe_patch_and_header_like_content_are_allowed(self):
        patch = self.patch("*** Add File: notes.md\n+*** Delete File: attack/sample/journal.jsonl")
        self.assertIsNone(self.run_hook("guard_attack_files.py", patch))

    def test_safe_patch_with_envelope_whitespace_is_allowed(self):
        patch = "*** Begin Patch \n*** Add File: notes.md\n+safe\n *** End Patch"
        self.assertIsNone(self.run_hook("guard_attack_files.py", patch))

    def test_envelope_whitespace_still_checks_protected_files(self):
        patch = "*** Begin Patch \n*** Delete File: attack/sample/journal.jsonl\n *** End Patch"
        self.assert_denied(self.run_hook("guard_attack_files.py", patch), "harness")

    def test_paths_with_spaces_and_absolute_paths_are_checked(self):
        other = self.root / "attack/space name"
        other.mkdir()
        (other / "problem.json").write_text("{}")
        self.assert_denied(self.run_hook("guard_attack_files.py", self.patch(
            f"*** Add File: {other}/tasks.json\n+{{}}")), "harness")

    def test_header_whitespace_cannot_bypass_record_protection(self):
        for body in (
            "*** Delete File: attack/sample/journal.jsonl ",
            "*** Add File: notes.md\n+safe\n  *** Delete File: attack/sample/journal.jsonl",
            "*** Update File: notes.md\n*** Move to: attack/sample/tasks.json \n@@\n+x",
        ):
            with self.subTest(body=body):
                output = self.run_hook("guard_attack_files.py", self.patch(body))
                self.assertIsNotNone(output)
                self.assertEqual(output["hookSpecificOutput"]["permissionDecision"], "deny")

    def test_header_like_update_context_remains_content(self):
        patch_text = self.patch("*** Update File: notes.md\n@@\n *** Delete File: attack/sample/journal.jsonl\n+x")
        self.assertIsNone(self.run_hook("guard_attack_files.py", patch_text))

    def test_unit_flow_reuses_existing_cashout_and_check_gates(self):
        patch = self.patch("*** Add File: attack/sample/units/1/draft.md\n+draft")
        self.assert_denied(self.run_hook("enforce_unit_flow.py", patch), "INVENTORY")
        (self.attack / "units/INVENTORY.md").write_text("inventory")
        self.assert_denied(self.run_hook("enforce_unit_flow.py", patch), "check-unit")
        self.write_checked_unit()
        self.assertIsNone(self.run_hook("enforce_unit_flow.py", patch))

    def write_checked_unit(self):
        (self.attack / "units/1/unit.json").write_text("{}")
        (self.attack / "units/1/check-unit.json").write_text(json.dumps({
            "unit_sha256": hashlib.sha256(b"{}").hexdigest()}))

    def test_one_patch_cannot_change_a_checked_unit_and_draft_it(self):
        (self.attack / "units/INVENTORY.md").write_text("inventory")
        self.write_checked_unit()
        patch = self.patch("*** Update File: attack/sample/units/1/unit.json\n@@\n-{}\n+{ }\n"
                           "*** Add File: attack/sample/units/1/draft.md\n+draft")
        self.assert_denied(self.run_hook("enforce_unit_flow.py", patch), "check-unit")

    def test_batch_unit_check_only_applies_inside_an_attack(self):
        patch = self.patch("*** Add File: examples/unit.json\n+{}\n"
                           "*** Add File: examples/draft.md\n+draft")
        self.assertIsNone(self.run_hook("enforce_unit_flow.py", patch))

    def test_bash_still_reaches_shared_submission_gate(self):
        (self.root / ".exactory").mkdir()
        (self.root / ".exactory/draft.json").write_text("{}")
        self.assert_denied(self.run_hook("enforce_citation_check.py",
                          "exactory submit --doi 10.1234/example", tool="Bash"), "references")

    def test_post_patch_records_paper_authorship_and_checks_references(self):
        (self.root / ".exactory").mkdir()
        (self.root / ".exactory/draft.json").write_text("{}")
        (self.root / "draft").mkdir()
        (self.root / "draft/paper.tex").write_text("paper")
        (self.root / "draft/references.bib").write_text("@article{bad, title={Missing metadata}}")
        patch = self.patch("*** Add File: draft/paper.tex\n+paper\n"
                           "*** Add File: draft/references.bib\n+@article{bad}")
        self.run_hook("record_paper_authorship.py", patch, "PostToolUse")
        self.assertTrue((self.root / ".exactory/authorship.json").is_file())
        output = self.run_hook("check_references_edit.py", patch, "PostToolUse")
        self.assertIn("Offline check", output["hookSpecificOutput"]["additionalContext"])

    def test_post_patch_records_activity_for_each_touched_attack(self):
        patch = self.patch("*** Add File: attack/sample/notes.md\n+notes")
        self.run_hook("record_attack_activity.py", patch, "PostToolUse")
        rows = (self.attack / "activity.jsonl").read_text().splitlines()
        self.assertEqual(len(rows), 1)
        self.assertEqual(json.loads(rows[0])["target"], "notes.md")

    def test_deleted_paper_is_not_recorded_as_authored(self):
        (self.root / ".exactory").mkdir()
        (self.root / ".exactory/draft.json").write_text("{}")
        patch = self.patch("*** Delete File: draft/paper.tex")
        self.run_hook("record_paper_authorship.py", patch, "PostToolUse")
        self.assertFalse((self.root / ".exactory/authorship.json").exists())

    def test_renaming_a_human_paper_does_not_claim_authorship(self):
        (self.root / ".exactory").mkdir()
        (self.root / ".exactory/draft.json").write_text("{}")
        (self.root / "draft").mkdir()
        (self.root / "draft/renamed.tex").write_text("Human paper")
        patch_text = self.patch("*** Update File: draft/human.tex\n*** Move to: draft/renamed.tex\n@@\n Human paper")
        self.run_hook("record_paper_authorship.py", patch_text, "PostToolUse")
        self.assertFalse((self.root / ".exactory/authorship.json").exists())

    def test_deleting_attack_notes_records_the_activity(self):
        patch_text = self.patch("*** Delete File: attack/sample/notes.md")
        self.run_hook("record_attack_activity.py", patch_text, "PostToolUse")
        rows = (self.attack / "activity.jsonl").read_text().splitlines()
        self.assertEqual(len(rows), 1)
        self.assertEqual(json.loads(rows[0])["target"], "notes.md")

    def test_multiple_bibliographies_keep_all_advisories(self):
        (self.root / ".exactory").mkdir()
        (self.root / ".exactory/draft.json").write_text("{}")
        (self.root / "one.bib").write_text("@article{one, title={One}}")
        (self.root / "two.bib").write_text("@article{two, title={Two}}")
        patch = self.patch("*** Update File: one.bib\n@@\n+x\n"
                           "*** Update File: two.bib\n@@\n+y")
        output = self.run_hook("check_references_edit.py", patch, "PostToolUse")
        context = output["hookSpecificOutput"]["additionalContext"]
        self.assertIn("one.bib", context)
        self.assertIn("two.bib", context)

    def test_non_file_tool_is_neutral(self):
        self.assertIsNone(self.run_hook("guard_attack_files.py", "", tool="WebSearch"))

    def test_empty_file_header_is_denied(self):
        self.assert_denied(self.run_hook("guard_attack_files.py", self.patch("*** Add File: \n+x")), "path")

    def test_unknown_hook_cannot_execute_a_path_outside_the_hook_directory(self):
        proc = subprocess.run([sys.executable, str(ROOT / "codex/hook.py"), "../bin/exactory"],
                              input="{}", capture_output=True, text=True, timeout=60)
        self.assertEqual(proc.returncode, 2)
        self.assertIn("Unknown shared hook", proc.stderr)

    def test_stop_and_resume_use_shared_hooks(self):
        output = self.run_hook("continue_attack.py", "", "Stop", "")
        self.assertEqual(output["decision"], "block")
        output = self.run_hook("resume_attack.py", "", "SessionStart", "")
        self.assertIn("sample", output["hookSpecificOutput"]["additionalContext"])

    def test_malformed_patch_is_explicitly_denied(self):
        for patch in (
            "not a patch",
            "*** Begin Patch extra\n*** Add File: notes.md\n+safe\n *** End Patch",
            "*** Begin Patch \n*** Add File: notes.md\n+safe\n *** End Patch extra",
        ):
            with self.subTest(patch=patch):
                self.assert_denied(self.run_hook("guard_attack_files.py", patch), "patch")

    def test_bootstrap_exposes_installed_bin_path_with_shell_quoting(self):
        proc = subprocess.run([sys.executable, str(ROOT / "codex/session_start.py")],
                              capture_output=True, text=True, timeout=60)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        context = json.loads(proc.stdout)["hookSpecificOutput"]["additionalContext"]
        self.assertIn(str(ROOT / "bin"), context)
        self.assertIn("PATH", context)
        self.assertIn("Codex", context)


if __name__ == "__main__":
    unittest.main()
