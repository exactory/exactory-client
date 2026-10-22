"""A strategy obstruction excludes the producer's strategy for its target and decides nothing else."""

import base64
import copy
import json
from pathlib import Path
import unittest

from search_controller.errors import SearchError
from search_controller.model import initial_state, replay
from search_controller.proof import checkpoint_milestone, obligation_support, root_support
from tests.search_fixtures import contract, decomposition_proposal, digest, proposal, provenance, review
from tests.test_search_proof import acceptance_record, admitted, checkpoint_record, event, route_state


TARGET_MISMATCH_ERR_MSG = "Strategy obstruction must name the proposal's target obligation"
CHECKPOINT_KIND_ERR_MSG = "Checkpoint kind differs from its declared milestone"
CHECKPOINT_CLAIM_ERR_MSG = "Checkpoint claim differs from its producer's admitted milestone"

# Computed with the unmodified candidate-3 controller, before the strategy obstruction kind existed.
HISTORICAL_STATE_DIGEST = "cfd52a158cb3f9259d4716de77ac475c0296a1d7b7a321420cdedc810435dc24"
PREVIOUS_MILESTONE_LOG_DIGEST = "f1afd376f1068cdb5be5f1dbc69b4afe76ec35cd3bbd53ff88ff5f49cc0a1b32"
PREVIOUS_MILESTONE_STATE_DIGEST = "882cfe6a0df9e1b7157f11880fe80e17af82dbba51888dc2e5f5b32efc31ddc9"


def build_strategy_obstruction(criterion_id, obligation_id):
    return {"kind": "verified_strategy_obstruction", "criterion_id": criterion_id, "obligation_id": obligation_id,
            "explanation": "An accepted barrier excludes the admitted refutation strategy for this obligation"}


def build_barrier_proposal(state, obligation_id="obligation-000003"):
    """A proposal that tries to refute the premise obligation-000003 of route-000001 with one construction."""
    value = proposal()
    value.update(attack_slug="tail-barrier", claim=copy.deepcopy(state["obligations"]["obligation-000003"]["claim"]),
                 target_obligation="obligation-000003", method="construction-transfer",
                 hypothesis="The construction of a known counterexample transfers to the tail range",
                 success_criterion="Prove that the construction cannot violate the tail claim",
                 failure_criterion="The construction violates the tail claim for one integer",
                 parent_effect="Excludes one refutation strategy and decides neither the premise nor the route")
    value["studies"]["strategies"] = [{"method": "construction-transfer", "digest": "6" * 64}]
    value["contribution"].update(route="route-000001", deduction="The tail claim is a premise of the partition route")
    value["checkpoint_criteria"] = [build_strategy_obstruction("tail-barrier", obligation_id)]
    return value


def build_local_checkpoint(state, node_id, milestone_id, kind="obstruction", claim=None):
    """A checkpoint of the node's first journal move, with the node's target claim unless another is given."""
    value = checkpoint_record(state, state["nodes"][node_id]["obligation_id"], kind)
    value.update(origin={"kind": "journal_move", "node_id": node_id, "move": 1, "journal_prefix_digest": "f" * 64},
                 milestone_id=milestone_id, what_changed="The admitted strategy reached its declared milestone",
                 next_hypothesis="Choose the next strategy for the same obligation")
    if claim is not None:
        value["claim"] = claim
    return value


def append_event(document, kind, payload):
    """Append one event to a replayable log and return the state that the whole log replays to."""
    sequence = len(document["events"]) + 1
    document["events"].append({"sequence": sequence, "request_id": "event-" + str(sequence),
                               "kind": kind, "payload": payload})
    return replay(document)


def build_previous_milestone_log():
    """A log that admits and evaluates every milestone kind that existed before the strategy obstruction."""
    document = {"schema_version": 1, "objective_id": "objective-000001", "contract": contract(), "events": []}
    value = decomposition_proposal()
    value["checkpoint_criteria"] = [
        {"kind": "accepted_obligation", "criterion_id": "base-proof", "obligation_id": "new:base",
         "explanation": "An accepted proof of the base cases"},
        {"kind": "accepted_case_set", "criterion_id": "base-cases", "obligation_id": "new:base", "case_ids": ["5", "6"],
         "explanation": "An accepted proof of the two base cases"},
        {"kind": "verified_reduction", "criterion_id": "base-reduction", "obligation_id": "new:base",
         "explanation": "A verified reduction of the base cases"},
        {"kind": "verified_obstruction", "criterion_id": "partition-obstruction", "route_id": "new:partition",
         "explanation": "A verified obstruction to the partition route"},
        {"kind": "hypothesis_recorded", "criterion_id": "base-hypothesis", "explanation": "A recorded next hypothesis"}]
    append_event(document, "proposal_recorded", {"proposal": value, "digest": digest(value)})
    append_event(document, "review_recorded", {"proposal_id": "proposal-000001", "review": review(value),
                                               "digest": digest(review(value))})
    state = append_event(document, "proposal_admitted", {"proposal_id": "proposal-000001"})
    for milestone_id, kind, outcome in [("partition-obstruction", "obstruction", "obstruction"),
                                        ("base-reduction", "reduction", "reduction"),
                                        ("base-hypothesis", "hypothesis", None),
                                        ("base-cases", "proof", None),
                                        ("base-proof", "proof", "proof")]:
        cp = build_local_checkpoint(state, "node-000001", milestone_id, kind)
        state = append_event(document, "checkpoint_recorded", {"checkpoint": cp, "digest": digest(cp)})
        if outcome is not None:
            acceptance = acceptance_record(state, "obligation-000002", outcome=outcome)
            state = append_event(document, "result_accepted", {"acceptance": acceptance, "digest": digest(acceptance)})
    return document, state


class StrategyObstructionAdmissionTests(unittest.TestCase):
    def assert_refused(self, state, value, expected):
        with self.assertRaises(SearchError) as caught:
            event(state, "proposal_recorded", {"proposal": value, "digest": digest(value)})
        self.assertEqual((caught.exception.code, caught.exception.message), expected)

    def test_a_strategy_obstruction_on_a_new_target_resolves_to_its_stable_id(self):
        value = decomposition_proposal()
        value["checkpoint_criteria"] = [build_strategy_obstruction("base-barrier", "new:base")]
        state = admitted(initial_state(contract(), "objective-000001"), value)
        node = state["nodes"]["node-000001"]
        self.assertEqual(node["obligation_id"], "obligation-000002")
        self.assertEqual(node["checkpoint_criteria"], [build_strategy_obstruction("base-barrier", "obligation-000002")])

    def test_a_strategy_obstruction_on_an_existing_premise_of_a_route_is_admitted(self):
        state = route_state()
        state = admitted(state, build_barrier_proposal(state))
        node = state["nodes"]["node-000002"]
        self.assertEqual((node["obligation_id"], node["route_id"]), ("obligation-000003", "route-000001"))
        self.assertEqual(node["checkpoint_criteria"], [build_strategy_obstruction("tail-barrier", "obligation-000003")])

    def test_a_strategy_obstruction_cannot_name_another_obligation(self):
        state = route_state()
        for obligation_id in ["obligation-000001", "obligation-000002", "obligation-000004"]:
            with self.subTest(obligation_id=obligation_id):
                self.assert_refused(state, build_barrier_proposal(state, obligation_id),
                                    ("invalid_record", TARGET_MISMATCH_ERR_MSG))
        value = decomposition_proposal()
        value["checkpoint_criteria"] = [build_strategy_obstruction("base-barrier", "new:tail")]
        self.assert_refused(initial_state(contract(), "objective-000001"), value,
                            ("invalid_record", TARGET_MISMATCH_ERR_MSG))
        self.assert_refused(state, build_barrier_proposal(state, "obligation-999999"),
                            ("dangling_reference", "Unknown criterion obligation reference"))

    def test_a_standalone_proposal_cannot_declare_a_strategy_obstruction(self):
        value = proposal("standalone")
        value["checkpoint_criteria"] = [build_strategy_obstruction("standalone-barrier", "obligation-000001")]
        self.assert_refused(initial_state(contract(), "objective-000001"), value,
                            ("invalid_record", TARGET_MISMATCH_ERR_MSG))

    def test_criteria_keep_exactly_their_own_reference_fields(self):
        state = route_state()
        strategy_fields = "Record must contain exactly: kind criterion_id explanation obligation_id"
        route_fields = "Record must contain exactly: kind criterion_id explanation route_id"
        for criterion, message in [
                ({"kind": "verified_strategy_obstruction", "criterion_id": "tail-barrier", "route_id": "route-000001",
                  "explanation": "A route in place of the target"}, strategy_fields),
                (dict(build_strategy_obstruction("tail-barrier", "obligation-000003"), route_id="route-000001"),
                 strategy_fields),
                ({"kind": "verified_obstruction", "criterion_id": "tail-barrier", "obligation_id": "obligation-000003",
                  "explanation": "An obligation in place of the route"}, route_fields)]:
            value = build_barrier_proposal(state)
            value["checkpoint_criteria"] = [criterion]
            with self.subTest(criterion=criterion):
                self.assert_refused(state, value, ("invalid_record", message))


class StrategyObstructionCheckpointTests(unittest.TestCase):
    def setUp(self):
        state = route_state()
        self.state = admitted(state, build_barrier_proposal(state))

    def record(self, state, checkpoint):
        return event(state, "checkpoint_recorded", {"checkpoint": checkpoint, "digest": digest(checkpoint)})

    def assert_checkpoint_refused(self, checkpoint, message):
        with self.assertRaises(SearchError) as caught:
            self.record(self.state, checkpoint)
        self.assertEqual((caught.exception.code, caught.exception.message), ("invalid_record", message))

    def test_an_obstruction_checkpoint_with_the_producers_claim_records(self):
        state = self.record(self.state, build_local_checkpoint(self.state, "node-000002", "tail-barrier"))
        recorded = state["checkpoints"]["checkpoint-000001"]
        self.assertEqual((recorded["kind"], recorded["milestone_id"]), ("obstruction", "tail-barrier"))
        self.assertEqual(recorded["claim"], self.state["nodes"]["node-000002"]["claim"])
        self.assertEqual(digest(recorded["claim"]), self.state["obligations"]["obligation-000003"]["claim_digest"])

    def test_no_other_checkpoint_kind_records_under_a_strategy_obstruction(self):
        for kind in ["proof", "reduction", "hypothesis"]:
            with self.subTest(kind=kind):
                self.assert_checkpoint_refused(build_local_checkpoint(self.state, "node-000002", "tail-barrier", kind),
                                               CHECKPOINT_KIND_ERR_MSG)

    def test_a_checkpoint_with_another_claim_is_refused_under_a_strategy_obstruction(self):
        narrowed = copy.deepcopy(self.state["nodes"]["node-000002"]["claim"])
        narrowed["scope"]["upper"] = 14
        for claim in [copy.deepcopy(self.state["obligations"]["obligation-000002"]["claim"]), narrowed]:
            with self.subTest(statement=claim["statement"], scope=claim["scope"]):
                self.assert_checkpoint_refused(
                    build_local_checkpoint(self.state, "node-000002", "tail-barrier", claim=claim), CHECKPOINT_CLAIM_ERR_MSG)

    def test_an_accepted_obstruction_on_the_target_meets_the_milestone_without_proof_credit(self):
        state = self.record(self.state, build_local_checkpoint(self.state, "node-000002", "tail-barrier"))
        self.assertFalse(checkpoint_milestone(state, "checkpoint-000001"))

        def read_decisions(value):
            return {"root": root_support(value),
                    "obligations": {key: item["status"] for key, item in value["obligations"].items()},
                    "routes": {key: item["status"] for key, item in value["routes"].items()}}

        before = read_decisions(state)
        acceptance = acceptance_record(state, "obligation-000003", outcome="obstruction")
        state = event(state, "result_accepted", {"acceptance": acceptance, "digest": digest(acceptance)})
        self.assertTrue(checkpoint_milestone(state, "checkpoint-000001"))
        self.assertIsNone(obligation_support(state, "obligation-000003"))
        self.assertEqual(state["obligations"]["obligation-000003"]["status"], "open")
        self.assertIsNone(root_support(state))
        self.assertEqual(read_decisions(state), before)
        state = event(state, "evidence_invalidated", {"acceptance_ids": ["acceptance-000001"],
                                                      "reason": "The barrier proof lost its pinned artifact",
                                                      "provenance": provenance("auditor")})
        self.assertFalse(checkpoint_milestone(state, "checkpoint-000001"))


class PreviousMilestoneReplayTests(unittest.TestCase):
    def test_the_recorded_historical_log_replays_to_its_previous_state_digest(self):
        fixture = json.loads((Path(__file__).parent / "fixtures/historical-v1-completed-run.json").read_text())
        state = replay(json.loads(base64.b64decode(fixture["tree_base64"])))
        self.assertEqual(digest(state), HISTORICAL_STATE_DIGEST)

    def test_a_log_with_every_previous_milestone_kind_replays_to_its_previous_state_digest(self):
        document, state = build_previous_milestone_log()
        self.assertEqual(digest(document), PREVIOUS_MILESTONE_LOG_DIGEST)
        self.assertEqual(digest(state), PREVIOUS_MILESTONE_STATE_DIGEST)
        self.assertEqual({key: checkpoint_milestone(state, key) for key in state["checkpoints"]},
                         {"checkpoint-000001": True, "checkpoint-000002": True, "checkpoint-000003": True,
                          "checkpoint-000004": False, "checkpoint-000005": True})
        self.assertEqual(state["obligations"]["obligation-000002"]["status"], "accepted")


if __name__ == "__main__":
    unittest.main()
