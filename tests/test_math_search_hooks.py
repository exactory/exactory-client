"""Synthetic host payloads exercise real managed objectives and hook subprocesses."""

import hashlib
import json
from pathlib import Path
import runpy
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
HARNESS = ROOT / "skills/math-solver/harness"
if str(HARNESS) not in sys.path:
    sys.path.insert(0, str(HARNESS))
from search_controller.service import Controller

FIXTURES = runpy.run_path(str(HARNESS / "tests/search_fixtures.py"))


def managed_objective(root, host="claude", session="session", workspace=None):
    root.mkdir(parents=True, exist_ok=True)
    controller = Controller(root)
    controller.command("init", {"contract": FIXTURES["contract"]()}, 0, "init", workspace_root=workspace)
    proposal = FIXTURES["proposal"]()
    inputs = []
    for name in ("problem", "novelty", "induction"):
        source = root / (name + ".md")
        source.write_text("Synthetic reviewed fixture study: " + name)
        digest = hashlib.sha256(source.read_bytes()).hexdigest()
        inputs.append({"path": source.name, "digest": digest, "kind": "artifact"})
        if name == "induction":
            proposal["studies"]["strategies"][0]["digest"] = digest
        else:
            proposal["studies"][name] = digest
    from integration_fixtures import pin_native_research
    pin_native_research(root, proposal, inputs, controller.status()["contract"])
    controller.command("propose", {"proposal": proposal, "inputs": inputs}, 1, "propose")
    controller.command("review", {"proposal_id": "proposal-000001", "review": FIXTURES["review"](proposal), "inputs": []}, 2, "review")
    controller.command("admit", {}, 3, "admit", "proposal-000001")
    focus_objective(controller, host, session, workspace=workspace)
    return controller


def focus_objective(controller, host, session, request="focus", workspace=None):
    revision = controller.status()["revision"]
    return controller.command("focus", {"focus": "focused", "session_id": host + ":" + session,
        "provenance": {"source": "operator", "actor_id": "fixture-operator", "attestation_id": request}},
        revision, request, workspace_root=workspace)


class MathSearchHookTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="math hooks ")
        self.addCleanup(temporary.cleanup)
        self.workspace = Path(temporary.name).resolve()
        self.root = self.workspace / "custom-research"
        self.controller = managed_objective(self.root)

    def hook(self, script, event="Stop", host="claude", **fields):
        payload = {"hook_event_name": event, "session_id": "session", "cwd": str(self.workspace), **fields}
        argv = [sys.executable, str(ROOT / "hooks" / script)]
        if host == "codex":
            argv = [sys.executable, str(ROOT / "codex/hook.py"), script]
        result = subprocess.run(argv, input=json.dumps(payload), capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stderr)
        return json.loads(result.stdout) if result.stdout.strip() else None

    def denied(self, output):
        self.assertIsNotNone(output)
        self.assertEqual(output["hookSpecificOutput"]["permissionDecision"], "deny")
        return output["hookSpecificOutput"]["permissionDecisionReason"]

    def test_custom_root_is_resumed_from_workspace_and_node_without_mutation(self):
        before = self.controller.store.tree_path.read_bytes()
        for cwd in [self.workspace, self.root / "attempt"]:
            output = self.hook("resume_attack.py", "SessionStart", cwd=str(cwd))
            self.assertIsNotNone(output, "Registered root must be discoverable")
            self.assertIn("execute_node", output["hookSpecificOutput"]["additionalContext"])
        self.assertEqual(self.controller.store.tree_path.read_bytes(), before)

    def test_unrelated_ancestor_registration_does_not_manage_sibling_paths(self):
        unrelated = self.workspace / "unrelated"
        unrelated.mkdir()
        pointer = self.workspace / ".exactory/math-search.json"
        registry = json.loads(pointer.read_text())
        registry["roots"].append({**registry["roots"][0], "path": str(self.workspace / "removed-fixture")})
        pointer.write_text(json.dumps(registry))
        before = pointer.read_bytes()
        self.assertIsNone(self.hook("continue_attack.py", cwd=str(unrelated)))
        self.assertIsNone(self.hook("guard_attack_files.py", "PreToolUse", cwd=str(unrelated),
            tool_name="Write", tool_input={"file_path": "notes.md"}))
        self.assertIsNone(self.hook("guard_attack_files.py", "PreToolUse", cwd=str(unrelated),
            tool_name="functions.exec", tool_input={"code": "unrelated_operation()"}))
        self.assertEqual(pointer.read_bytes(), before)

    def test_external_root_node_uses_its_original_explicit_workspace_pointer(self):
        registration = self.workspace / "registration"
        registration.mkdir()
        external = managed_objective(self.workspace / "external/objective", workspace=registration)
        before = external.store.tree_path.read_bytes()
        output = self.hook("resume_attack.py", "SessionStart", cwd=str(external.root / "attempt"))
        self.assertIn("execute_node", output["hookSpecificOutput"]["additionalContext"])
        self.assertEqual(external.store.tree_path.read_bytes(), before)

    def test_stop_uses_objective_even_after_all_local_finished_files(self):
        (self.root / "attempt/units/FINISHED.json").write_text('{"outcome":"cashed-out","units":[]}')
        output = self.hook("continue_attack.py")
        self.assertIsNotNone(output, "Local finish cannot close the original objective")
        self.assertEqual(output["decision"], "block")
        self.assertEqual(self.controller.status()["proof_status"], "open")
        self.assertEqual(self.controller.status()["control"]["stop_count"], 1)

    def test_missing_or_conflicting_focus_requests_handoff_without_charge(self):
        for session in [None, "another-session"]:
            output = self.hook("continue_attack.py", session_id=session)
            self.assertIsNotNone(output, "Ambiguous managed focus needs explicit recovery context")
            self.assertNotEqual(output.get("decision"), "block")
            self.assertIn("focus", json.dumps(output).lower())
        self.assertEqual(self.controller.status()["control"]["stop_count"], 0)

    def test_duplicate_delivery_counts_once_but_missing_delivery_counts_each_stop(self):
        for _ in range(2):
            output = self.hook("continue_attack.py", delivery_id="delivery", stop_hook_active=False)
            self.assertEqual(output["decision"], "block")
        self.assertEqual(self.controller.status()["control"]["stop_count"], 1)
        for _ in range(2):
            self.hook("continue_attack.py", stop_hook_active=False)
        self.assertEqual(self.controller.status()["control"]["stop_count"], 3)

    def test_unchanged_observations_are_not_recorded_again(self):
        self.hook("continue_attack.py")
        self.hook("continue_attack.py")
        document = json.loads(self.controller.store.tree_path.read_text())
        self.assertEqual([op["kind"] for op in document["events"][-1]["payload"]["operations"]], ["control_recorded"])

    def test_draft_and_evaluation_edits_refresh_before_stop_decision(self):
        attack = self.root / "attempt"
        (attack / "openings.json").write_text('{"openings":[]}')
        (attack / "units/INVENTORY.md").write_text("Synthetic local inventory")
        unit = attack / "units/1"
        unit.mkdir()
        (unit / "unit.json").write_text("{}")
        (unit / "check-unit.json").write_text(json.dumps({"unit_sha256": hashlib.sha256(b"{}").hexdigest()}))
        first = self.hook("continue_attack.py", delivery_id="delivery")
        self.assertIn('"step": "draft"', first["reason"])
        (unit / "draft.md").write_text("The actual draft was written after that Stop delivery.")
        repeated = self.hook("continue_attack.py", delivery_id="delivery")
        self.assertIn('"step": "evaluation"', repeated["reason"])
        (unit / "evaluation.md").write_text("The actual local evaluation is present.")
        final = self.hook("continue_attack.py", delivery_id="delivery")
        self.assertIn('"step": "finish"', final["reason"])
        state = self.controller.status()
        self.assertEqual(state["control"]["stop_count"], 1)
        self.assertEqual(state["proof_status"], "open")

    def test_native_record_shell_basename_cannot_bypass_protection(self):
        self.denied(self.hook("guard_attack_files.py", "PreToolUse", tool_name="exec_command",
            tool_input={"cmd": "printf '{}' > journal.jsonl", "workdir": str(self.root / "attempt")}))

    def test_absolute_write_executables_and_clobber_redirect_are_denied(self):
        # These strings are hook inputs only, never executed by a shell.
        commands = ("/bin/rm .search/tree.json", "/bin/mv .search/tree.json notes.json",
                    "/bin/cp notes.json .search/tree.json", "/usr/bin/tee .search/tree.json",
                    "/usr/bin/truncate -s 0 .search/tree.json", "/bin/dd of=.search/tree.json",
                    "/usr/bin/sed -i '' .search/tree.json", "printf '{}' >| .search/tree.json")
        for host in ("claude", "codex"):
            for command in commands:
                with self.subTest(host=host, command=command):
                    self.denied(self.hook("guard_attack_files.py", "PreToolUse", host=host,
                        tool_name="exec_command", tool_input={"cmd": command, "workdir": str(self.root)}))
            for command in ("/bin/cat .search/tree.json", "printf '%s' 'rm' .search/tree.json"):
                with self.subTest(host=host, read_only=command):
                    self.assertIsNone(self.hook("guard_attack_files.py", "PreToolUse", host=host,
                        tool_name="exec_command", tool_input={"cmd": command, "workdir": str(self.root)}))

    def test_discovery_lock_cannot_be_written_replaced_moved_or_deleted(self):
        lock = self.workspace / ".exactory/math-search.lock"
        before = (lock.stat().st_ino, lock.read_bytes())
        for tool in ("Write", "Edit", "Delete"):
            with self.subTest(tool=tool):
                self.denied(self.hook("guard_attack_files.py", "PreToolUse", tool_name=tool,
                    tool_input={"file_path": str(lock)}))
        for command in ("rm ../.exactory/math-search.lock", "printf x >| ../.exactory/math-search.lock",
                        "cp notes ../.exactory/math-search.lock", "mv notes ../.exactory/math-search.lock",
                        "mv ../.exactory/math-search.lock notes"):
            with self.subTest(command=command):
                self.denied(self.hook("guard_attack_files.py", "PreToolUse", tool_name="exec_command",
                    tool_input={"cmd": command, "workdir": str(self.root)}))
        sibling = self.workspace / "unrelated"
        sibling.mkdir()
        self.denied(self.hook("guard_attack_files.py", "PreToolUse", cwd=str(sibling), tool_name="Delete",
            tool_input={"file_path": str(lock)}))
        self.assertIsNone(self.hook("guard_attack_files.py", "PreToolUse", tool_name="exec_command",
            tool_input={"cmd": "/bin/cat ../.exactory/math-search.lock", "workdir": str(self.root)}))
        self.assertEqual((lock.stat().st_ino, lock.read_bytes()), before)

    def test_failed_custom_root_tool_does_not_record_successful_activity(self):
        self.hook("record_attack_activity.py", "PostToolUse", tool_name="exec_command",
            tool_input={"cmd": "cat study/problem.md", "workdir": str(self.root / "attempt")}, tool_response={"exit_code": 1})
        self.assertFalse((self.root / "attempt/activity.jsonl").exists())

    def test_managed_shell_activity_records_relative_target_without_raw_arguments(self):
        self.hook("record_attack_activity.py", "PostToolUse", tool_name="exec_command",
            tool_input={"cmd": "cat study/problem.md --fixture-secret=do-not-record", "workdir": str(self.root / "attempt")},
            tool_response={"exit_code": 0})
        rows = (self.root / "attempt/activity.jsonl").read_text().splitlines()
        self.assertEqual(json.loads(rows[0])["target"], "study/problem.md")
        self.assertNotIn("do-not-record", rows[0])

    def test_owned_controller_snapshots_and_views_are_protected(self):
        for relative in [".search/tree.json", ".search/blobs/owned.json", ".search/artifacts/owned",
                         "SEARCH_TREE.md", "attempt/LINEAGE.md", "attempt/journal.jsonl"]:
            output = self.hook("guard_attack_files.py", "PreToolUse", tool_name="Write",
                tool_input={"file_path": str(self.root / relative)})
            self.denied(output)
        self.denied(self.hook("guard_attack_files.py", "PreToolUse", tool_name="Edit",
            tool_input={"file_path": str(self.workspace / ".exactory/math-search.json")}))

    def test_exec_command_cmd_workdir_and_path_bootstrap_are_normalized(self):
        for host in ("claude", "codex"):
            self.denied(self.hook("guard_attack_files.py", "PreToolUse", host=host, tool_name="exec_command",
                tool_input={"cmd": "printf '{}' > .search/tree.json", "workdir": str(self.root)}))
            output = self.hook("guard_attack_files.py", "PreToolUse", host=host, tool_name="exec_command",
                tool_input={"cmd": 'export PATH="/installed/exactory/bin:$PATH"\nexactory-math --attack-root ' +
                    repr(str(self.root)) + ' search status --json', "workdir": str(self.workspace)})
            self.assertIsNone(output)

    def test_unsupported_visible_managed_wrapper_fails_closed(self):
        output = self.hook("guard_attack_files.py", "PreToolUse", tool_name="functions.exec",
            tool_input={"code": 'await tools.apply_patch("*** Delete File: ' + str(self.root / ".search/tree.json") + '")'})
        self.assertIn("direct", self.denied(output).lower())

    def test_relative_managed_wrapper_and_shell_programs_fail_closed(self):
        self.denied(self.hook("guard_attack_files.py", "PreToolUse", tool_name="functions.exec",
            tool_input={"code": 'await write("custom-research/.search/tree.json", "bad")'}))
        for command in ('cat notes.md\nprintf bad > .search/tree.json',
                        'cat notes.md | python3 -c "open(\'.search/tree.json\',\'w\').write(\'bad\')"',
                        'cat "$(python3 mutate.py)"', 'rm "$TARGET"'):
            self.denied(self.hook("guard_attack_files.py", "PreToolUse", tool_name="exec_command",
                tool_input={"cmd": command, "workdir": str(self.root)}))

    def test_controller_timeout_is_a_managed_recovery_error(self):
        module = runpy.run_path(str(ROOT / "hooks/math_search.py"))
        with patch.object(module["subprocess"], "run", side_effect=subprocess.TimeoutExpired("controller", 20)):
            with self.assertRaises(module["ManagedError"]):
                module["controller_call"](self.root, "status")

    def test_read_only_research_tool_is_allowed_inside_managed_root(self):
        self.assertIsNone(self.hook("guard_attack_files.py", "PreToolUse", tool_name="WebSearch",
                                   tool_input={"query": "primary sources"}, cwd=str(self.root)))

    def test_read_only_result_references_and_command_like_prose_are_allowed(self):
        for command in ('cat attempt/deterministic/job/result.json',
                        "printf '%s' 'exactory-math result.json'", "cat exactory-math-notes.md"):
            self.assertIsNone(self.hook("guard_attack_files.py", "PreToolUse", tool_name="exec_command",
                tool_input={"cmd": command, "workdir": str(self.root)}))
        self.assertIsNone(self.hook("guard_attack_files.py", "PreToolUse", tool_name="Write",
            tool_input={"file_path": str(self.root / "attempt/notes.md"), "content": "The result.json reference and exactory-math text are explanatory prose."}))

    def test_malformed_managed_state_denies_edits_and_reports_stop_recovery(self):
        self.controller.store.tree_path.write_text("{invalid")
        output = self.hook("guard_attack_files.py", "PreToolUse", tool_name="Write",
            tool_input={"file_path": str(self.root / "attempt/notes.md")})
        self.assertIn("recover", self.denied(output).lower())
        output = self.hook("continue_attack.py")
        self.assertIsNotNone(output)
        self.assertNotEqual(output.get("decision"), "block")
        self.assertIn("recover", json.dumps(output).lower())

    def test_codex_custom_root_preserves_combined_unit_draft_sequencing(self):
        path = self.root / "attempt/units/1"
        path.mkdir()
        (self.root / "attempt/units/INVENTORY.md").write_text("inventory")
        (path / "unit.json").write_text("{}")
        (path / "check-unit.json").write_text(json.dumps({"unit_sha256": hashlib.sha256(b"{}").hexdigest()}))
        command = "*** Begin Patch\n*** Update File: " + str(path / "unit.json") + "\n@@\n-{}\n+{ }\n*** Add File: " + str(path / "draft.md") + "\n+draft\n*** End Patch"
        output = self.hook("enforce_unit_flow.py", "PreToolUse", host="codex", tool_name="apply_patch", tool_input={"command": command})
        self.assertIn("check-unit", self.denied(output))
