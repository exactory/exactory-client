"""Replay preserves reducer semantics without copying accumulated history."""

import copy
import unittest
from unittest.mock import patch

from search_controller import model
from search_controller.errors import SearchError
from search_controller.storage import canonical_bytes
from tests.search_fixtures import contract, digest, proposal, review
from tests.test_search_proof import acceptance_record, checkpoint_record, closure_record


class ReplayScalingTests(unittest.TestCase):
    def make_document(self, count):
        return {"schema_version": 1, "objective_id": "objective-000001", "contract": contract(),
                "events": [{"sequence": i, "request_id": "render-" + str(i), "kind": "service_operation",
                            "payload": {"command": "render", "target": None, "spec_digest": digest({}),
                                        "operations": [], "effects": []}}
                           for i in range(1, count + 1)]}

    def replay_without_state_copies(self, document):
        original_copy = copy.deepcopy
        accumulated_copies = []

        def track_copy(value, memo=None):
            if isinstance(value, dict) and {"revision", "requests", "contract"} <= set(value):
                accumulated_copies.append(len(value["requests"]))
            return original_copy(value, memo)

        with patch.object(model.copy, "deepcopy", side_effect=track_copy):
            state = model.replay(document)
        self.assertEqual(accumulated_copies, [], "Replay must not copy accumulated state per event")
        return state

    def test_growing_render_history_does_not_copy_accumulated_state(self):
        for count in (32, 128, 512):
            with self.subTest(events=count):
                document = self.make_document(count)
                state = self.replay_without_state_copies(document)
                self.assertEqual(state["revision"], count)
                self.assertEqual(state["requests"], {event["request_id"]: digest(event)
                                                     for event in document["events"]})

    def test_replay_matches_public_reducer_and_detaches_nested_inputs(self):
        document = self.make_document(0)
        oracle = model.initial_state(document["contract"], document["objective_id"])

        def append(kind, payload):
            nonlocal oracle
            event = {"sequence": len(document["events"]) + 1, "request_id": "event-" + str(len(document["events"])),
                     "kind": kind, "payload": payload}
            previous = oracle
            before = copy.deepcopy(previous)
            oracle = model.apply_event(oracle, event)
            self.assertEqual(previous, before)
            self.assertEqual(before, model.replay(document))
            document["events"].append(event)

        value = proposal()
        append("proposal_recorded", {"proposal": value, "digest": digest(value)})
        append("review_recorded", {"proposal_id": "proposal-000001", "review": review(value), "digest": digest(review(value))})
        append("proposal_admitted", {"proposal_id": "proposal-000001"})
        cp = checkpoint_record(oracle, "obligation-000001")
        append("checkpoint_recorded", {"checkpoint": cp, "digest": digest(cp)})
        acceptance = acceptance_record(oracle, "obligation-000001")
        append("result_accepted", {"acceptance": acceptance, "digest": digest(acceptance)})
        closure = closure_record(oracle)
        append("objective_completed", {"closure": closure, "digest": digest(closure)})
        for event in self.make_document(32)["events"]:
            append(event["kind"], event["payload"])

        original = copy.deepcopy(document)
        state = self.replay_without_state_copies(document)
        self.assertEqual(canonical_bytes(state), canonical_bytes(oracle))
        self.assertEqual(document, original)
        state["proposals"]["proposal-000001"]["record"]["claim"]["scope"]["lower"] = 0
        state["contract"]["original_claim"]["scope"]["upper"] = 100
        self.assertEqual(document, original)
        document["events"][0]["payload"]["proposal"]["studies"]["strategies"][0]["method"] = "changed"
        self.assertEqual(oracle["proposals"]["proposal-000001"]["record"]["studies"]["strategies"][0]["method"], "induction")

    def test_replay_retains_event_validation_and_failure_isolation(self):
        document = self.make_document(2)
        for defect, code in (("extra", "invalid_record"), ("sequence", "revision_conflict"),
                             ("request", "request_conflict"), ("kind", "unknown_event"),
                             ("payload", "invalid_record")):
            bad = copy.deepcopy(document)
            event = bad["events"][1]
            if defect == "extra":
                event["extra"] = True
            elif defect == "sequence":
                event["sequence"] = 3
            elif defect == "request":
                event["request_id"] = bad["events"][0]["request_id"]
            elif defect == "kind":
                event["kind"] = "unavailable"
            else:
                event["payload"] = []
            original = copy.deepcopy(bad)
            with self.subTest(defect=defect), self.assertRaises(SearchError) as raised:
                model.replay(bad)
            self.assertEqual(raised.exception.code, code)
            self.assertEqual(bad, original)

        state = model.replay(document)
        event = self.make_document(3)["events"][-1]
        event["payload"]["operations"] = [{"kind": "unavailable", "payload": {}}]
        original = copy.deepcopy(state)
        with self.assertRaises(SearchError):
            model.apply_event(state, event)
        self.assertEqual(state, original)
        for mutate in (lambda value: value["contract"].update(requested_outcome="changed"),
                       lambda value: value["obligations"]["obligation-000001"]["claim"].update(statement="changed")):
            bad = copy.deepcopy(state)
            mutate(bad)
            with self.assertRaises(SearchError) as raised:
                model.apply_event(bad, self.make_document(3)["events"][-1])
            self.assertEqual(raised.exception.code, "contract_changed")


if __name__ == "__main__":
    unittest.main()
