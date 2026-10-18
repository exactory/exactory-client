"""Only actual, authenticated native completions enter the common evidence store."""

import copy
import json
from pathlib import Path
import subprocess
import sys
import unittest
from unittest.mock import patch

from research_harness.storage import Store as CommonStore
from search_controller.errors import SearchError
from search_controller.reviewer import common_root
from search_controller.service import Controller
from tests.native_reviewer_support import attest_spec
from tests.search_fixtures import digest
from tests.test_search_cli import CLI, SearchCLIWorkspace
from tests import test_search_openness_closure as closure_tests


class DeliverClosureTests(SearchCLIWorkspace, unittest.TestCase):
    artifact = closure_tests.OpennessClosureTests.artifact
    external_checkpoint = closure_tests.OpennessClosureTests.external_checkpoint
    setup_external = closure_tests.OpennessClosureTests.setup_external
    set_up_accepted_proof = closure_tests.OpennessClosureTests.set_up_accepted_proof
    build_closure_spec = closure_tests.OpennessClosureTests.build_closure_spec

    def complete_objective(self):
        self.set_up_accepted_proof("already_solved")
        self.search("complete", self.build_closure_spec(), revision=3)
        return Controller(self.root)

    def test_observed_closure_approval_without_completion_cannot_be_delivered(self):
        self.set_up_accepted_proof("already_solved")
        controller = Controller(self.root)
        attest_spec(controller, self.build_closure_spec())
        common = CommonStore(common_root(self.root, controller.status()))
        before = common.snapshot()
        result = self.search("deliver-closure", {}, revision=3, success=False)
        self.assertEqual(result["error"]["code"], "native_completion_required")
        self.assertEqual(common.snapshot(), before)

    def test_delivers_exact_native_closure_once_without_changing_native_history(self):
        controller = self.complete_objective()
        state = controller.status()
        before_native = controller.store.read()
        result = self.search("deliver-closure", {}, revision=4)
        identity = digest(state["control"]["closure"])
        self.assertEqual(result["native_closure_digest"], identity)
        common = CommonStore(common_root(self.root, state))
        first = common.snapshot()
        self.assertEqual(first["records"]["native_closure"][identity], {
            "schema_version": 1, "objective_id": "objective-000001",
            "attack_root": str(self.root.resolve()), "contract": state["contract"],
            "closure": state["control"]["closure"],
            "provenance": controller.provenance("deliver-closure")})
        self.search("deliver-closure", {}, revision=4)
        self.assertEqual(common.snapshot(), first)
        self.assertEqual(controller.store.read(), before_native)

    def test_reopened_objective_delivers_another_closure_and_preserves_the_first(self):
        controller = self.complete_objective()
        self.search("deliver-closure", {}, revision=4)
        common = CommonStore(common_root(self.root, controller.status()))
        original = copy.deepcopy(common.snapshot()["records"]["native_closure"])
        changed = self.external_checkpoint()
        changed["checkpoint"].update(kind="hypothesis", what_changed="A new settling source needs review")
        changed["inputs"][1] = self.artifact("new-source.md", "New material mathematical evidence.")
        changed["checkpoint"]["origin"]["source_digest"] = changed["inputs"][1]["digest"]
        self.search("checkpoint", changed, revision=4)
        refused = self.search("deliver-closure", {}, revision=5, success=False)
        self.assertEqual(refused["error"]["code"], "native_completion_required")
        updated = self.build_closure_spec()
        updated["review"]["findings"]["openness"]["evidence"].append(changed["inputs"][1]["digest"])
        self.search("complete", updated, revision=5)
        result = self.search("deliver-closure", {}, revision=6)
        records = common.snapshot()["records"]["native_closure"]
        self.assertEqual(len(records), 2)
        for identity, record in original.items():
            self.assertEqual(records[identity], record)
        self.assertNotIn(result["native_closure_digest"], original)

    def test_delivers_to_associated_common_workspace_using_canonical_attack_root(self):
        self.initialize()
        proposed = self.prepared_proposal()
        proposed["review_workspace"] = ".."
        self.search("propose", proposed, revision=1)
        self.search("checkpoint", self.external_checkpoint(), revision=2)
        controller = Controller(self.root)
        cp = controller.status()["checkpoints"]["checkpoint-000001"]
        reviewed = closure_tests.build_openness_review(cp, cp["claim"], "already_solved")
        from tests.openness_support import attach_minimum
        reviewed = attach_minimum(controller, reviewed)
        self.search("accept", {"obligation_id": "obligation-000001", "outcome": "proof",
            "dependency_ids": [], "route_bindings": [], "review": reviewed, "inputs": []},
            target=cp["id"], revision=3)
        self.search("complete", self.build_closure_spec(), revision=4)
        alias = self.root.parent / "attack-alias"
        alias.symlink_to(self.root, target_is_directory=True)
        result = Controller(alias).command("deliver-closure", {}, 5, "deliver-through-alias")
        record = CommonStore(self.root.parent).snapshot()["records"]["native_closure"][result["native_closure_digest"]]
        self.assertEqual(record["attack_root"], str(self.root.resolve()))
        self.assertEqual(record["objective_id"], "objective-000001")
        self.assertFalse((self.root / ".exactory/research.sqlite3").exists())

    def test_missing_observed_closure_packet_prevents_delivery(self):
        controller = self.complete_objective()
        state = controller.status()
        common = CommonStore(common_root(self.root, state))
        assignment = state["control"]["closure"]["review"]["reviewer"]["attestation_id"].split(":", 1)[1]
        packet = common.snapshot()["records"]["review_assignment"][assignment]["context"]["native_packet"]
        (Path(common.root) / packet["path"]).unlink()
        result = self.search("deliver-closure", {}, revision=4, success=False)
        self.assertEqual(result["error"]["code"], "audit_failed")
        self.assertNotIn("native_closure", common.snapshot()["records"])

    def test_delivery_rejects_a_stale_native_revision(self):
        controller = self.complete_objective()
        before = CommonStore(common_root(self.root, controller.status())).snapshot()
        result = self.search("deliver-closure", {}, revision=3, success=False)
        self.assertEqual(result["error"]["code"], "revision_conflict")
        self.assertEqual(CommonStore(self.root).snapshot(), before)

    def test_common_revision_retry_reaudits_a_late_exploratory_origin(self):
        controller = self.complete_objective()
        from search_controller.closure_delivery import audit_state
        from tests.test_search_common_evidence import CommonEvidenceTests
        changed = []

        def audit_then_record_origin(*args, **kwargs):
            failures = audit_state(*args, **kwargs)
            if not changed:
                changed.append(True)
                CommonEvidenceTests.record_probe_output(self, (self.root / "proof.md").read_bytes())
            return failures

        # Only schedule the competing common mutation; all proof, origin and
        # revision checks run through their real production implementations.
        with patch("search_controller.closure_delivery.audit_state", side_effect=audit_then_record_origin):
            with self.assertRaises(SearchError) as caught:
                controller.command("deliver-closure", {}, 4, "deliver-with-concurrent-origin")
        self.assertEqual(caught.exception.code, "audit_failed")
        self.assertNotIn("native_closure", CommonStore(self.root).snapshot()["records"])

    def test_delivery_accepts_file_payload_without_accepting_a_caller_supplied_closure(self):
        controller = self.complete_objective()
        source = self.root / "deliver.json"
        source.write_text(json.dumps({"closure": controller.status()["control"]["closure"]}))
        result = subprocess.run([sys.executable, str(CLI), "--attack-root", str(self.root), "search",
            "deliver-closure", "--file", str(source), "--expected-revision", "4", "--request-id", "deliver-file"],
            capture_output=True, text=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(json.loads(result.stderr)["error"]["code"], "invalid_record")
        source.write_text("{}")
        result = subprocess.run([sys.executable, str(CLI), "--attack-root", str(self.root), "search",
            "deliver-closure", "--file", str(source), "--expected-revision", "4", "--request-id", "deliver-file"],
            capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("native_closure_digest", json.loads(result.stdout))


if __name__ == "__main__":
    unittest.main()
