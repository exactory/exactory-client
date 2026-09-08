"""Native service boundaries require the reviewed complete common preparation."""

import copy
from pathlib import Path
import unittest
import sys
import time
from unittest.mock import patch

from search_controller.errors import SearchError
from search_controller.service import Controller
from tests.search_fixtures import contract, review
from tests.test_search_cli import SearchCLIWorkspace
from tests.research_support import pin_research
from research_harness.storage import Store


class NativeResearchTests(SearchCLIWorkspace, unittest.TestCase):
    def native(self):
        controller = Controller(self.root)
        controller.command("init", {"contract": contract()}, 0, "initialize")
        return controller

    def test_unprepared_legacy_shape_cannot_create_a_new_public_proposal(self):
        controller = self.native()
        value = self.prepared_proposal(common=False)
        before = controller.store.read()
        with self.assertRaises(SearchError) as caught:
            controller.command("propose", value, 1, "new-proposal")
        self.assertEqual(caught.exception.code, "research_foundation_required")
        self.assertEqual(controller.store.read(), before)

    def test_current_complete_synthesis_is_pinned_and_source_change_prevents_admission(self):
        controller = self.native()
        value = self.prepared_proposal(common=False)
        pin_research(self.root, value["proposal"], value["inputs"])
        controller.command("propose", value, 1, "new-proposal")
        controller.command("review", {"proposal_id": "proposal-000001", "review": review(value["proposal"]), "inputs": []}, 2, "review")
        snapshot = controller.store.get_blob(value["proposal"]["foundation"]["snapshot_digest"])
        common = Store(self.root)
        reference = next(iter(common.snapshot()["records"]["source"].values()))["response"]
        path = self.root / reference["path"]
        before = path.read_bytes()
        path.chmod(0o600)
        path.write_bytes(b"Changed source after the native review.")
        with self.assertRaises(SearchError) as caught:
            controller.command("admit", {}, 3, "admit", "proposal-000001")
        self.assertEqual(caught.exception.code, "research_foundation_stale")
        self.assertEqual(controller.status()["nodes"], {})
        self.assertEqual(controller.store.get_blob(value["proposal"]["foundation"]["snapshot_digest"]), snapshot)
        path.write_bytes(before)
        controller.command("admit", {}, 3, "admit", "proposal-000001")
        self.assertEqual(controller.status()["totals"]["used_runs"], 0)

    def test_narrower_literature_foundation_does_not_replace_complete_preparation(self):
        controller = self.native()
        value = self.prepared_proposal(common=False)
        pin_research(self.root, value["proposal"], value["inputs"])
        common = Store(self.root)
        snapshot = common.snapshot()
        from research_harness.synthesis import record_innovation
        records = snapshot["records"]
        selected = records["synthesis_selection"]["research:innovation"]["id"]
        incomplete = copy.deepcopy(records["synthesis"][selected]["payload"])
        incomplete["id"] = "incomplete-innovation"
        incomplete["cases"] = incomplete["cases"][:1]
        record_innovation(common, incomplete, expected_revision=snapshot["revision"], request_id="change-innovation")
        with self.assertRaises(SearchError):
            controller.command("propose", value, 1, "new-proposal")
        self.assertEqual(controller.status()["proposals"], {})

    def test_unrelated_common_revision_does_not_invalidate_the_reviewed_preparation(self):
        controller = self.native()
        value = self.prepared_proposal()
        controller.command("propose", value, 1, "new-proposal")
        controller.command("review", {"proposal_id": "proposal-000001", "review": review(value["proposal"]), "inputs": []}, 2, "review")
        common = Store(self.root)
        revision = common.revision
        common.mutate("fixture.note", {"text": "An unrelated operational note."},
                      lambda tx: tx.put("note", "operational", {"text": "An unrelated operational note."}),
                      expected_revision=revision, request_id="note-only")
        self.assertGreater(common.revision, value["proposal"]["foundation"]["revision"])
        controller.command("admit", {}, 3, "admit", "proposal-000001")
        self.assertEqual(controller.status()["proof_status"], "open")

    def test_explicit_reviewed_legacy_amendment_preserves_original_history_and_accounts(self):
        from search_controller.evidence import import_inputs
        from search_controller.model import apply_event, replay
        from tests.search_fixtures import digest
        from tests.research_support import amendment_spec
        controller = self.native()
        value = self.prepared_proposal(common=False)
        approved = review(value["proposal"])
        import_inputs(self.root, value["inputs"], controller.store)
        controller.store.put_blob(value["proposal"])
        controller.store.put_blob(approved)
        for kind, payload in (("proposal_recorded", {"proposal": value["proposal"], "digest": digest(value["proposal"])}),
                              ("review_recorded", {"proposal_id": "proposal-000001", "review": approved, "digest": digest(approved)}),
                              ("proposal_admitted", {"proposal_id": "proposal-000001"})):
            controller.store.append(kind, payload, controller.status()["revision"], "historical:" + kind,
                validate=lambda doc, event: apply_event(replay(doc), event))
        historical = copy.deepcopy(controller.store.read())
        before = controller.status()
        with self.assertRaises(SearchError) as caught:
            controller.guard_legacy("plan", "attempt", {})
        self.assertEqual(caught.exception.code, "research_foundation_amendment_required")
        inputs = []
        delivery = pin_research(self.root, copy.deepcopy(value["proposal"]), inputs)
        amended = amendment_spec(controller, "node-000001", delivery["foundation"], inputs)
        wrong = copy.deepcopy(amended)
        wrong["review"]["reviewer"] = value["proposal"]["author"]
        with self.assertRaises(SearchError) as caught:
            controller.command("amend-foundation", wrong, before["revision"], "bad-amendment", "node-000001")
        self.assertEqual(caught.exception.code, "review_not_independent")
        first = controller.command("amend-foundation", amended, before["revision"], "amendment", "node-000001")
        self.assertEqual(controller.command("amend-foundation", amended, before["revision"], "amendment", "node-000001"), first)
        self.assertEqual(controller.guard_legacy("plan", "attempt", {})["id"], "node-000001")
        after = controller.status()
        for key in ("accounts", "totals", "acceptances", "proof_status", "nodes"):
            self.assertEqual(before[key], after[key])
        self.assertEqual(controller.store.read()["events"][:len(historical["events"])], historical["events"])

    def test_verification_profile_requires_the_exact_native_claim_source_binding(self):
        from integration_fixtures import prepare_verification
        from research_harness.native_export import export_native
        from tests.search_fixtures import digest
        controller = self.native()
        value = self.prepared_proposal(common=False)
        case = prepare_verification(self.root)
        correspondence = {"root_claim_digest": digest(contract()["original_claim"]), "claim_digest": digest(value["proposal"]["claim"]),
                          "evidence": [case.linked], "reason": "The independently reviewed fixture maps the native proposition to this exact source passage."}
        delivery = export_native(case.store, self.root, self.root / "verification-inputs", claim_binding=correspondence)
        value["inputs"].extend(delivery["inputs"])
        value["proposal"].update(schema_version=3, computation=None, foundation=delivery["foundation"])
        with self.assertRaises(SearchError) as caught:
            controller.command("propose", value, 1, "wrong-profile")
        self.assertEqual(caught.exception.code, "research_profile_mismatch")
        value["proposal"]["role"] = "verification"
        wrong = copy.deepcopy(value)
        wrong["proposal"]["foundation"]["claim_binding"]["claim_digest"] = "f" * 64
        with self.assertRaises(SearchError) as caught:
            controller.command("propose", wrong, 1, "wrong-claim")
        self.assertEqual(caught.exception.code, "research_target_mismatch")
        wrong["proposal"]["foundation"]["claim_binding"] = copy.deepcopy(correspondence)
        wrong["proposal"]["foundation"]["claim_binding"]["evidence"][0]["version_id"] = "arxiv:2601.00002v1"
        with self.assertRaises(SearchError) as caught:
            controller.command("propose", wrong, 1, "wrong-source")
        self.assertEqual(caught.exception.code, "research_target_mismatch")
        controller.command("propose", value, 1, "external-verification")
        controller.command("review", {"proposal_id": "proposal-000001", "review": review(value["proposal"]), "inputs": []}, 2, "review")
        controller.command("admit", {}, 3, "admit", "proposal-000001")
        self.assertEqual(controller.status()["nodes"]["node-000001"]["role"], "verification")


class NativePrelaunchTests(unittest.TestCase):
    def test_source_changed_after_run_reservation_never_receives_an_execution_token(self):
        import tempfile
        from tests.support import run
        from tests.search_execution_support import admit_workspace, begin_spec, invoke, command_spec
        from search_controller.execution import launch
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve() / "attack"
            root.mkdir()
            self.assertEqual(run(["init", "sample"], root)[0], 0)
            controller = admit_workspace(root, computational=True)
            workspace = root / "sample"
            step = workspace / "deterministic/job"
            step.mkdir()
            marker = root / "scientific-process-ran"
            (step / "job.py").write_text("from pathlib import Path\nPath(" + repr(str(marker)) + ").write_text('executed')\n")
            invoke(controller, "begin", begin_spec())
            with patch("search_controller.execution.launch"):
                invoke(controller, "run", command_spec(workspace, [sys.executable, "job.py"]))
            reserved = controller.status()["runs"]["run-000001"]
            common = Store(root)
            artifact = next(iter(common.snapshot()["records"]["source"].values()))["response"]
            source = root / artifact["path"]
            source.chmod(0o600)
            source.write_bytes(b"Changed after reservation but before actual subprocess release.")
            with self.assertRaises(SearchError) as caught:
                launch(controller, reserved)
            self.assertEqual(caught.exception.code, "research_foundation_stale")
            terminal = Path(reserved["snapshot_root"]).parent / "terminal.json"
            deadline = time.monotonic() + 5
            while not terminal.exists() and time.monotonic() < deadline:
                time.sleep(0.01)
            self.assertTrue(terminal.exists())
            self.assertFalse(marker.exists())
            invoke(controller, "reconcile", {}, None)
            observed = controller.status()["runs"]["run-000001"]
            self.assertEqual(observed["status"], "terminal")
            self.assertEqual(observed["started_units"], 0)
            self.assertEqual(observed["termination"], "never_started")
