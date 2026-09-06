"""Local completion audits exact publication and proof artifact versions."""

import json
import unittest

from search_controller.errors import SearchError
from search_controller.storage import Store
from tests.test_search_cli import SearchCLIWorkspace
from tests.support import make_move


class DeliveryTests(SearchCLIWorkspace, unittest.TestCase):
    def delivery(self):
        self.admit()
        workspace = self.root / "attempt"
        store = Store(self.root / ".search")
        unit = workspace / "units" / "1"
        unit.mkdir()
        (workspace / "journal.jsonl").write_text(json.dumps(make_move(1, closes=True)) + "\n")
        (workspace / "units" / "INVENTORY.md").write_text("The exact root result was inventoried.\n")
        (workspace / "units" / "consolidation.md").write_text("Each proof step and its assumptions were consolidated.\n")
        (workspace / "HANDOFF.md").write_text("The original claim is proved and all local delivery is complete.\n")
        (unit / "proof.md").write_text("Exact analytical proof.\n")
        value = {"statement": "For every n the original bound holds.", "form": "full-proof", "evidence": "units/1/proof.md",
                 "novelty": "Primary-source comparison found the original gap", "moves": [1], "costs": []}
        (unit / "unit.json").write_text(json.dumps(value))
        raw = store.put_artifact((unit / "unit.json").read_bytes())
        (unit / "check-unit.json").write_text(json.dumps({"unit_sha256": raw}))
        (unit / "draft.md").write_text("Complete proof draft.\n")
        (unit / "evaluation.md").write_text("The exact statement and evidence were evaluated.\n")
        (workspace / "units" / "FINISHED.json").write_text(json.dumps({"outcome": "cashed-out", "units": [1]}))
        files = [{"path": str(p.relative_to(workspace)), "digest": store.put_artifact(p.read_bytes())}
                 for p in sorted(unit.iterdir())]
        package = {"schema_version": 1, "node_id": "node-000001", "unit_number": 1, "files": files}
        delivery = {"node_id": "node-000001", "inventory_digest": store.put_artifact((workspace / "units" / "INVENTORY.md").read_bytes()),
                    "checked_unit_digests": [store.put_blob(package)],
                    "consolidation_digest": store.put_artifact((workspace / "units" / "consolidation.md").read_bytes()),
                    "draft_digests": [store.put_artifact((unit / "draft.md").read_bytes())],
                    "evaluation_digests": [store.put_artifact((unit / "evaluation.md").read_bytes())],
                    "handoff_digest": store.put_artifact((workspace / "HANDOFF.md").read_bytes()),
                    "finish": {"kind": "finished", "unused_child_ids": []}}
        return delivery, workspace, store

    def test_changed_evidence_invalidates_unchanged_unit_stamp(self):
        from search_controller import evidence
        self.assertTrue(hasattr(evidence, "audit_local_delivery"), "Local delivery needs a real file audit")
        delivery, workspace, store = self.delivery()
        state = self.search("status")
        evidence.audit_local_delivery(self.root, state, delivery, store)
        (workspace / "units" / "1" / "proof.md").write_text("The proof input changed after review.")
        with self.assertRaises(SearchError):
            evidence.audit_local_delivery(self.root, state, delivery, store)

    def test_unused_child_exception_does_not_hide_missing_evaluation(self):
        from search_controller import evidence
        self.assertTrue(hasattr(evidence, "audit_local_delivery"), "Local delivery needs a real file audit")
        delivery, workspace, store = self.delivery()
        (workspace / "units" / "1" / "evaluation.md").unlink()
        delivery["finish"] = {"kind": "local_finish_pending_unused_children", "unused_child_ids": ["node-000002"]}
        with self.assertRaises(SearchError):
            evidence.audit_local_delivery(self.root, self.search("status"), delivery, store)

    def test_new_checked_package_cannot_replace_the_accepted_proof_version(self):
        from search_controller import evidence
        delivery, workspace, store = self.delivery()
        state = self.search("status")
        proof = workspace / "units" / "1" / "proof.md"
        accepted_digest = store.put_artifact(proof.read_bytes())
        manifest = {"artifacts": [{"path": "attempt/units/1/proof.md", "digest": accepted_digest, "role": "proof"}]}
        state["checkpoints"]["checkpoint-000001"] = {"origin": {"node_id": "node-000001"}, "evidence_digests": [store.put_blob(manifest)]}
        state["acceptances"]["acceptance-000001"] = {"status": "accepted", "checkpoint_id": "checkpoint-000001"}
        proof.write_text("A different proof after the accepted version.")
        package = store.get_blob(delivery["checked_unit_digests"][0])
        for item in package["files"]:
            if item["path"] == "units/1/proof.md":
                item["digest"] = store.put_artifact(proof.read_bytes())
        delivery["checked_unit_digests"] = [store.put_blob(package)]
        with self.assertRaises(SearchError):
            evidence.audit_local_delivery(self.root, state, delivery, store)


if __name__ == "__main__":
    unittest.main()
