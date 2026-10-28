"""New closure commands wait for side work while historical closures still replay."""

import json
from pathlib import Path
import unittest

from search_controller.model import apply_event, replay
from search_controller.service import Controller
from tests import test_search_adoption as adoption_tests
from tests import test_search_openness_closure as openness_tests
from tests.native_reviewer_support import attest_spec
from tests.search_fixtures import digest, review
from tests.test_search_cli import SearchCLIWorkspace
from tests.test_search_scheduler import facts_record
from tests.test_search_standalone_proof import standalone_proposal


class StandaloneClosureTests(SearchCLIWorkspace, unittest.TestCase):
    artifact = openness_tests.OpennessClosureTests.artifact
    external_checkpoint = openness_tests.OpennessClosureTests.external_checkpoint
    setup_external = openness_tests.OpennessClosureTests.setup_external
    set_up_accepted_proof = openness_tests.OpennessClosureTests.set_up_accepted_proof
    build_closure_spec = openness_tests.OpennessClosureTests.build_closure_spec

    def admit_side_node(self, slug="side"):
        spec = self.prepared_proposal()
        candidate = spec["proposal"]
        standalone = standalone_proposal()
        candidate.update(category="standalone", relationship="standalone", attack_slug=slug,
                         target_obligation=None, claim=standalone["claim"],
                         checkpoint_criteria=standalone["checkpoint_criteria"])
        candidate["contribution"]["standalone"] = standalone["contribution"]["standalone"]
        candidate["contribution"]["standalone"]["primary_sources"] = [candidate["studies"]["problem"]]
        candidate["retreat_criteria"] = [{"kind": "stagnation_window", "threshold": 1}]
        controller = Controller(self.root)
        self.search("propose", spec, revision=controller.status()["revision"])
        proposal_id = list(controller.status()["proposals"])[-1]
        for actor in ["reviewer-one", "reviewer-two"]:
            self.search("review", {"proposal_id": proposal_id, "review": review(candidate, actor), "inputs": []},
                        revision=controller.status()["revision"])
        self.search("admit", {}, target=proposal_id, revision=controller.status()["revision"])
        return list(controller.status()["nodes"])[-1]

    def record_facts(self, node_id, **updates):
        controller = Controller(self.root)
        revision = controller.status()["revision"]
        controller.store.append("node_facts_recorded", {"facts": facts_record(node_id, **updates)},
                                revision, "fixture-facts-" + str(revision),
                                validate=lambda document, event: apply_event(replay(document), event))

    def assert_complete_refused(self, node_ids):
        controller = Controller(self.root)
        spec = self.build_closure_spec()
        attest_spec(controller, spec)
        before = controller.store.read()
        result = self.search("complete", spec, revision=len(before["events"]), success=False)
        self.assertEqual(result["error"]["code"], "standalone_closure_pending")
        self.assertEqual(result["error"]["details"]["node_ids"], node_ids)
        for node_id in node_ids:
            self.assertIn(node_id, result["error"]["message"])
        self.assertEqual(controller.store.read(), before)
        self.assertIsNone(controller.status()["control"]["closure"])
        return spec

    def assert_historical_replay(self, name):
        fixture = Path(__file__).with_name("fixtures") / name
        original = fixture.read_bytes()
        captured = json.loads(original)
        document = captured["document"]
        self.assertEqual(digest(document), captured["document_digest"])
        state = replay(document)
        result = {key: state[key] for key in ["revision", "proof_status", "execution_status", "nodes",
                                            "accounts", "totals", "checkpoints", "acceptances"]}
        result["closure"] = state["control"]["closure"]
        result["moves"] = state["service"]["moves"]
        self.assertEqual(digest(result), captured["result_digest"])
        self.assertEqual(state["nodes"]["node-000001"]["status"], "active")
        self.assertEqual(state["execution_status"], "resolved")
        self.assertEqual(state["proof_status"], "proved")
        self.assertEqual(state["totals"]["used_moves"], 1)
        self.assertEqual(state["totals"]["reserved_moves"], 0)
        self.assertEqual(replay(document), state)
        self.assertEqual(fixture.read_bytes(), original)
        return document

    def test_release_logs_replay_and_new_completion_waits_for_standalone_finish(self):
        self.assert_historical_replay("standalone-release-replay.json")
        self.set_up_accepted_proof()
        node_id = self.admit_side_node()
        self.record_facts(node_id)
        self.assert_complete_refused([node_id])
        self.record_facts(node_id, status="finished")
        controller = Controller(self.root)
        revision = controller.status()["revision"]
        spec = self.build_closure_spec()
        result = self.search("complete", spec, revision=revision, request="complete-after-finish")
        self.assertEqual(controller.status()["execution_status"], "resolved")
        self.assertEqual(controller.status()["proof_status"], "proved")
        self.assertEqual(controller.status()["closure_blocking_node_ids"], [])
        self.assertEqual(self.search("complete", spec, revision=revision, request="complete-after-finish"), result)

    def test_draft_begin_replays_and_new_completion_waits_for_standalone_retreat(self):
        document = self.assert_historical_replay("standalone-draft-replay.json")
        move_index = next(index for index, event in enumerate(document["events"]) if event["kind"] == "move_reserved")
        prefix = replay(dict(document, events=document["events"][:move_index]))
        from search_controller.proof import root_support
        from search_controller.scheduler import next_action
        self.assertIsNotNone(root_support(prefix))
        self.assertEqual(next_action(prefix), {"kind": "execute_node", "node_id": "node-000001"})
        self.set_up_accepted_proof()
        node_id = self.admit_side_node()
        self.record_facts(node_id, stagnation_moves=1)
        self.assert_complete_refused([node_id])
        controller = Controller(self.root)
        self.search("retreat", {"criterion": {"kind": "stagnation_window", "threshold": 1},
                    "failed_hypothesis": "The admitted strategy advances the claim",
                    "observation": "The recorded stagnation criterion fired", "last_checkpoint_id": None,
                    "remaining_assumption_ids": [],
                    "reconsideration": "A different strategy is independently justified",
                    "abandoned_route_ids": [], "abandoned_assumption_ids": []},
                    target=node_id, revision=controller.status()["revision"])
        self.search("complete", self.build_closure_spec(), revision=controller.status()["revision"])
        self.assertEqual(controller.status()["nodes"][node_id]["status"], "retreated")
        self.assertEqual(controller.status()["execution_status"], "resolved")

    def test_next_and_status_name_every_unfinished_standalone_closure_blocker(self):
        self.set_up_accepted_proof()
        node_id = self.admit_side_node()
        controller = Controller(self.root)
        for status in ["admitted", "active", "waiting", "result_ready"]:
            with self.subTest(status=status):
                self.record_facts(node_id, status=status, external_block="Awaiting independent input")
                before = controller.store.read()
                self.assertEqual(self.search("status").get("closure_blocking_node_ids", []), [node_id])
                action = self.search("next")
                self.assertEqual(action["kind"], "finalize_root")
                self.assertEqual(action["closure_blocking_node_ids"], [node_id])
                self.assertEqual(controller.store.read(), before)
        self.assert_complete_refused([node_id])
        self.record_facts(node_id, status="finished")
        self.assertEqual(self.search("status")["closure_blocking_node_ids"], [])
        self.assertNotIn("closure_blocking_node_ids", self.search("next"))

    def test_completion_waits_for_all_standalone_nodes(self):
        self.set_up_accepted_proof()
        adoption_tests.AdoptionTests.create_legacy(self)
        (self.root / "legacy/units/FINISHED.json").unlink()
        spec = adoption_tests.AdoptionTests.adoption_spec(self)
        mapping = spec["mappings"][0]
        mapping["target_obligation"] = None
        subject = {key: value for key, value in mapping.items() if key not in {"review", "verification"}}
        mapping["review"]["subject_digest"] = digest(subject)
        controller = Controller(self.root)
        self.search("adopt", spec, revision=controller.status()["revision"])
        imported = controller.status()["nodes"]["node-000001"]
        self.assertEqual(imported["category"], "standalone")
        self.assertIsNone(imported["proposal_id"])
        first = self.admit_side_node()
        second = self.admit_side_node("another-side")
        self.assert_complete_refused([first, second])
        self.record_facts(first, status="finished")
        self.assert_complete_refused([second])
        self.assertEqual(self.search("next")["closure_blocking_node_ids"], [second])
        self.record_facts(second, status="finished")
        controller = Controller(self.root)
        self.search("complete", self.build_closure_spec(), revision=controller.status()["revision"])
        self.assertEqual(controller.status()["execution_status"], "resolved")
