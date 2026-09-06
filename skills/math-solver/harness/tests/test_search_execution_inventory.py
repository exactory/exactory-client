"""Native reviews pin installed toolchain dependencies before any launch."""

import json
import os
from pathlib import Path
from unittest import mock

from tests.support import WorkspaceTest
from tests.search_execution_support import admit_workspace, begin_spec, invoke, review_native_inputs
from search_controller.evidence import audit_toolchain, capture_toolchain, installed_lean
from search_controller.errors import SearchError


class ToolchainInventoryTests(WorkspaceTest):
    def test_one_remaining_unit_cannot_start_a_two_command_lean_workload(self):
        from tests.support import FAKE_BIN
        controller = admit_workspace(self.attack_root, max_runs=1)
        step = self.workspace / "deterministic/formal"
        step.mkdir()
        log = self.attack_root / "lake.log"
        (step / "Main.lean").write_text("theorem exact_decl : True := True.intro\n")
        (step / "lakefile.toml").write_text('name = "fixture"\n')
        (step / "lean-toolchain").write_text("fixture-version\n")
        (step / "step.json").write_text(json.dumps({"theorem": "exact_decl", "requested_type": "True",
            "environment": {"PATH": str(FAKE_BIN) + os.pathsep + os.environ["PATH"], "FAKE_LAKE_LOG": str(log)}}))
        invoke(controller, "begin", begin_spec())
        review_native_inputs(controller, self.slug, "formal", "lean")
        status, out, err = self.run_cli("verify", "lean", self.slug, "formal")
        self.assertEqual(status, 1)
        self.assertIn("budget_exhausted", err)
        self.assertFalse(log.exists())
        self.assertEqual(controller.status()["runs"], {})

    def test_inventory_refuses_omitted_added_and_missing_dependency_members(self):
        runtime = self.attack_root / "synthetic-runtime"
        runtime.mkdir()
        library = runtime / "Library.olean"
        library.write_bytes(b"fixed library")
        inventory = capture_toolchain(runtime)
        audit_toolchain(inventory)
        with self.assertRaises(SearchError):
            audit_toolchain(dict(inventory, files=[]))
        extra = runtime / "Added.olean"
        extra.write_bytes(b"added library")
        with self.assertRaises(SearchError):
            audit_toolchain(inventory)
        extra.unlink()
        library.unlink()
        with self.assertRaises(SearchError):
            audit_toolchain(inventory)

    def test_missing_pinned_elan_toolchain_never_invokes_the_selector(self):
        runtime = self.attack_root / "elan/bin"
        runtime.mkdir(parents=True)
        selector = runtime / "elan"
        selector.write_text("An inert selector fixture")
        os.link(selector, runtime / "lake")
        project = self.attack_root / "project"
        project.mkdir()
        (project / "lean-toolchain").write_text("leanprover/lean4:v0.0.0-uninstalled")
        (runtime / "lake").chmod(0o755)
        with self.assertRaises(SearchError) as caught:
            installed_lean(project, str(runtime))
        self.assertEqual(caught.exception.code, "missing_toolchain")

    def test_changed_toolchain_dependency_invalidates_the_prelaunch_review(self):
        controller = admit_workspace(self.attack_root)
        step = self.workspace / "deterministic/formal"
        step.mkdir()
        (step / "Main.lean").write_text("theorem exact_decl : True := True.intro\n")
        (step / "lakefile.toml").write_text('name = "fixture"\n')
        (step / "lean-toolchain").write_text("fixture-version\n")
        (step / "step.json").write_text(json.dumps({"theorem": "exact_decl", "requested_type": "True"}))
        runtime = self.attack_root / "runtime"
        runtime.mkdir()
        library = runtime / "Dependency.olean"
        library.write_bytes(b"original dependency")
        marker = self.attack_root / "tool-ran"
        lake = runtime / "lake"
        lake.write_text("#!/bin/sh\nprintf ran > '" + str(marker) + "'\nif [ \"$1\" = build ]; then exit 0; fi\nprintf \"'exact_decl' depends on axioms: []\\n\"\n")
        lake.chmod(0o755)
        invoke(controller, "begin", begin_spec())
        with mock.patch.dict(os.environ, {"PATH": str(runtime) + os.pathsep + os.environ["PATH"]}):
            review_native_inputs(controller, self.slug, "formal", "lean")
            library.write_bytes(b"changed dependency")
            status, out, err = self.run_cli("verify", "lean", self.slug, "formal")
        self.assertNotEqual(status, 0)
        self.assertIn("digest_mismatch", err)
        self.assertFalse(marker.exists())
        self.assertEqual(controller.status()["totals"]["used_runs"], 0)
