"""Real controller routing and publication behavior with synthetic session identities."""

import concurrent.futures
import json
from pathlib import Path
import subprocess
import sys
import time
import unittest
from unittest.mock import patch

from search_controller.errors import SearchError
from search_controller.service import Controller
from tests.search_fixtures import contract, provenance
from tests.test_search_cli import CLI, SearchCLIWorkspace
from tests.support import WorkspaceTest
from tests.search_execution_support import admit_workspace, begin_spec, command_spec, invoke


class DiscoveryTests(SearchCLIWorkspace, unittest.TestCase):
    def registry(self, workspace=None):
        return json.loads(((workspace or self.root.parent) / ".exactory/math-search.json").read_text())

    def focus(self, controller=None, request="focus-a", session="codex:session", focused=True, workspace=None):
        controller = controller or Controller(self.root)
        spec = {"focus": "focused" if focused else "unrelated", "session_id": session,
                "provenance": provenance("operator")}
        return controller.command("focus", spec, controller.status()["revision"], request,
                                  workspace_root=workspace)

    def test_init_registers_identity_in_direct_parent_without_session_authority(self):
        self.initialize()
        path = self.root.parent / ".exactory/math-search.json"
        self.assertTrue(path.is_file(), "Successful init must publish discoverable root identity")
        registry = self.registry()
        self.assertEqual(registry["sessions"], {})
        self.assertEqual(registry["roots"], [{"path": str(self.root.resolve()),
            "objective_id": "objective-000001", "contract_digest": Controller(self.root).status()["contract_digest"]}])

    def test_cli_custom_workspace_routing_is_bound_to_request_identity(self):
        workspace = self.root.parent / "explicit"
        workspace.mkdir()
        spec = self.root.parent / "init.json"
        spec.write_text(json.dumps({"contract": contract()}))
        base = [sys.executable, str(CLI), "--attack-root", str(self.root), "search", "init",
                "--spec", str(spec), "--expected-revision", "0", "--request-id", "init", "--json"]
        first = subprocess.run(base + ["--workspace-root", str(workspace)], capture_output=True, text=True)
        self.assertEqual(first.returncode, 0, first.stderr)
        self.assertEqual(len(self.registry(workspace)["roots"]), 1)
        changed = subprocess.run(base + ["--workspace-root", str(self.root.parent)], capture_output=True, text=True)
        self.assertEqual(json.loads(changed.stderr)["error"]["code"], "request_id_conflict")
        self.assertFalse((self.root.parent / ".exactory/math-search.json").exists())

    def test_one_owner_does_not_reset_allowance_or_block_collaborative_status(self):
        self.initialize()
        self.focus()
        controller = Controller(self.root)
        controller.command("hook-stop", {"delivery_id": None, "session_id": "codex:session",
            "turn_id": None, "stop_hook_active": False}, 2, "stop")
        self.focus(request="focus-b", session="claude:other")
        state = controller.command("status", {}, None, None)
        self.assertEqual(state["control"]["stop_count"], 1)
        self.assertEqual(state["control"]["focus_record"]["session_id"], "claude:other")
        before = controller.store.tree_path.read_bytes()
        response = controller.command("next", {"session_id": "codex:session", "focus_request_id": "focus-a"}, None, None)
        self.assertEqual(response["kind"], "handoff")
        self.assertEqual(controller.store.tree_path.read_bytes(), before)

    def test_public_stop_also_requires_the_current_session_owner(self):
        self.initialize()
        controller = Controller(self.root)
        spec = {"delivery_id": "unowned", "session_id": "codex:intruder", "turn_id": None, "stop_hook_active": False}
        for focused in (False, True):
            if focused:
                self.focus()
            before = controller.store.tree_path.read_bytes()
            with self.assertRaises(SearchError) as error:
                controller.command("hook-stop", spec, controller.status()["revision"], "unowned-stop")
            self.assertEqual(error.exception.code, "focus_required")
            self.assertEqual(controller.store.tree_path.read_bytes(), before)

    def test_failed_publication_then_new_focus_cannot_be_overwritten_by_replay(self):
        self.initialize()
        from search_controller import discovery
        with patch.object(discovery, "publish", side_effect=OSError("interrupted publication")):
            with self.assertRaises(SearchError) as failure:
                self.focus()
        self.assertEqual(failure.exception.code, "discovery_unpublished")
        self.focus(request="focus-b")
        before = self.registry()
        with self.assertRaises(SearchError) as stale:
            self.focus(request="focus-a")
        self.assertEqual(stale.exception.code, "discovery_unpublished")
        self.assertEqual(self.registry(), before)

    def test_cross_objective_focus_and_tombstone_prevent_stale_publication(self):
        self.initialize()
        from search_controller import discovery
        with patch.object(discovery, "publish", side_effect=OSError("interrupted publication")):
            with self.assertRaises(SearchError):
                self.focus()
        other = Controller(self.root.parent / "other")
        other.command("init", {"contract": contract()}, 0, "other-init")
        self.focus(other, request="other-focus")
        self.focus(other, request="other-clear", focused=False)
        entry = self.registry()["sessions"]["codex:session"]
        self.assertIsNone(entry["target"])
        self.assertTrue(entry["generation"])
        with self.assertRaises(SearchError):
            self.focus(request="focus-a")
        self.assertEqual(self.registry()["sessions"]["codex:session"], entry)

    def test_concurrent_root_registration_preserves_all_identities(self):
        roots = [self.root.parent / ("root-%d" % number) for number in range(4)]
        def initialize(root):
            return Controller(root).command("init", {"contract": contract()}, 0, "init")
        with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
            list(pool.map(initialize, roots))
        path = self.root.parent / ".exactory/math-search.json"
        self.assertTrue(path.exists(), "Concurrent initialization must publish registrations")
        self.assertEqual({value["path"] for value in self.registry()["roots"]}, {str(root.resolve()) for root in roots})

    def test_direct_root_session_cannot_ignore_later_cross_objective_focus(self):
        self.initialize()
        self.focus()
        other = Controller(self.root.parent / "other")
        other.command("init", {"contract": contract()}, 0, "other-init")
        self.focus(other, request="other-focus")
        original = Controller(self.root)
        before = original.store.tree_path.read_bytes()
        result = original.command("next", {"session_id": "codex:session", "focus_request_id": None}, None, None)
        self.assertEqual(result["kind"], "handoff")
        self.assertEqual(original.store.tree_path.read_bytes(), before)

    def test_symlinked_registry_directory_is_rejected_without_external_write(self):
        elsewhere = self.root.parent / "outside"
        elsewhere.mkdir()
        (self.root.parent / ".exactory").symlink_to(elsewhere, target_is_directory=True)
        result = self.search("init", {"contract": contract()}, success=False)
        self.assertEqual(result["error"]["code"], "unsafe_path")
        self.assertEqual(list(elsewhere.iterdir()), [])


class StopOwnershipTests(WorkspaceTest):
    def test_live_owned_workload_allows_stop_without_spending_allowance(self):
        controller = admit_workspace(self.attack_root, computational=True)
        controller.command("focus", {"focus": "focused", "session_id": "codex:session", "provenance": provenance("operator")},
                           controller.status()["revision"], "focus")
        step = self.workspace / "deterministic/job"
        step.mkdir()
        (step / "job.py").write_text("import time\ntime.sleep(1.5)\n")
        invoke(controller, "begin", begin_spec())
        spec = command_spec(self.workspace, [sys.executable, "job.py"], 3)
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            workload = pool.submit(invoke, controller, "run", spec)
            deadline = time.monotonic() + 3
            while time.monotonic() < deadline:
                state = controller.status()
                if any(run["status"] == "launched" for run in state["runs"].values()):
                    break
                time.sleep(0.01)
            self.assertTrue(any(run["status"] == "launched" for run in state["runs"].values()))
            result = controller.command("hook-stop", {"delivery_id": "waiting", "session_id": "codex:session",
                "turn_id": None, "stop_hook_active": False}, state["revision"], "stop",
                hook_session={"focus_request_id": "focus"})
            self.assertEqual(result["decision"]["kind"], "allow_stop")
            self.assertEqual(controller.status()["control"]["stop_count"], 0)
            workload.result(timeout=5)

    def test_owned_native_intent_prevents_observing_partial_edits(self):
        from argparse import Namespace
        from search_controller.integration import begin_native_intent
        controller = admit_workspace(self.attack_root)
        node = controller.status()["nodes"]["node-000001"]
        _, ownership = begin_native_intent(controller, node, Namespace(command="plan"))
        try:
            before = controller.store.tree_path.read_bytes()
            with self.assertRaises(SearchError) as error:
                controller.command("hook-stop", {"delivery_id": "native", "session_id": None,
                    "turn_id": None, "stop_hook_active": False}, controller.status()["revision"], "stop")
            self.assertEqual(error.exception.code, "recovery_required")
            self.assertEqual(controller.store.tree_path.read_bytes(), before)
        finally:
            ownership.close()
