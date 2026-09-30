"""Native consumption rechecks scientific preparation at its commit boundary."""

import copy
import unittest
from unittest.mock import patch

from search_controller.errors import SearchError
from search_controller.service import Controller
from tests.native_reviewer_support import observe_review
from tests.search_execution_support import admit_workspace, begin_spec
from tests.search_fixtures import contract, review
from tests.support import WorkspaceTest
from tests.test_search_cli import SearchCLIWorkspace


class FoundationRaceChecks:
    def assert_foundation_update_blocks_commit(self, controller, command, spec, target):
        from research_harness.storage import Store
        from research_harness.synthesis import record_rationale

        common = Store(controller.root)
        records = common.snapshot()["records"]
        selected = records["synthesis_selection"]["research:rationale"]["id"]
        changed = copy.deepcopy(records["synthesis"][selected]["payload"])
        changed["id"] = "rationale-concurrent-with-native-consumption"
        changed["therefore"]["test"] = "Check the revised scientific question before any new native work."
        before = controller.store.read()
        original = controller._build
        observations = []

        def build_then_update(*args, **kwargs):
            candidate = original(*args, **kwargs)
            receipt = record_rationale(common, changed, expected_revision=common.revision,
                                       request_id="concurrent-native-foundation-update")
            observations.append(receipt)
            return candidate

        with patch.object(controller, "_build", side_effect=build_then_update):
            with self.assertRaises(SearchError) as caught:
                controller.command(command, spec, controller.status()["revision"],
                                   "consume-after-foundation-update", target)

        self.assertEqual(caught.exception.code, "research_foundation_stale")
        self.assertEqual(len(observations), 1)
        current = common.snapshot()["records"]
        self.assertEqual(current["synthesis_selection"]["research:rationale"]["id"], changed["id"])
        self.assertEqual(controller.store.read(), before)


class NativeAdmissionRaceTests(FoundationRaceChecks, SearchCLIWorkspace, unittest.TestCase):
    def test_foundation_changed_after_admission_build_cannot_create_a_native_node(self):
        controller = Controller(self.root)
        controller.command("init", {"contract": contract()}, 0, "initialize")
        proposed = self.prepared_proposal()
        controller.command("propose", proposed, 1, "propose")
        value = observe_review(controller, review(proposed["proposal"]), proposed["proposal"])
        controller.command("review", {"proposal_id": "proposal-000001", "review": value,
                                      "inputs": []}, 2, "observed-review")

        self.assert_foundation_update_blocks_commit(controller, "admit", {}, "proposal-000001")
        self.assertFalse(controller.status()["nodes"])


class NativeBeginRaceTests(FoundationRaceChecks, WorkspaceTest):
    def test_foundation_changed_after_begin_build_cannot_reserve_a_move(self):
        controller = admit_workspace(self.attack_root)
        before = copy.deepcopy(controller.status()["totals"])

        self.assert_foundation_update_blocks_commit(controller, "begin", begin_spec(), "node-000001")
        self.assertEqual(controller.status()["totals"], before)
        self.assertFalse(controller.status()["service"]["moves"])

    def test_foundation_changed_after_amendment_build_cannot_select_stale_preparation(self):
        from research_harness.native_export import export_native
        from research_harness.storage import Store
        from tests.research_support import amendment_spec

        controller = admit_workspace(self.attack_root)
        delivery = export_native(Store(self.attack_root), self.attack_root,
                                 self.attack_root / "review-race-foundation")
        amendment = amendment_spec(controller, "node-000001", delivery["foundation"], delivery["inputs"])

        self.assert_foundation_update_blocks_commit(controller, "amend-foundation", amendment, "node-000001")
        self.assertFalse(controller.status()["service"]["foundation_amendments"])


class NativeAdoptionRaceTests(FoundationRaceChecks, SearchCLIWorkspace, unittest.TestCase):
    def test_foundation_changed_after_adoption_build_cannot_allocate_verification_allowance(self):
        from tests.native_reviewer_support import attest_spec
        from tests.test_search_adoption import AdoptionTests

        fixture = AdoptionTests()
        fixture.root = self.root
        fixture.create_legacy()
        controller = Controller(self.root)
        controller.command("init", {"contract": contract()}, 0, "initialize")
        spec = fixture.verification_spec()
        attest_spec(controller, spec)

        self.assert_foundation_update_blocks_commit(controller, "adopt", spec, None)
        state = controller.status()
        self.assertFalse(state["nodes"])
        self.assertFalse(state["accounts"])
        self.assertFalse(state["service"]["adoption_allowances"])
        self.assertFalse((self.root / "verify-legacy").exists())
        self.assertEqual((self.root / "legacy" / "units" / "FINISHED.json").read_bytes(), fixture.finish)
