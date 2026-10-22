"""A native_math review output is checked against the native review shape when it is run."""

import copy
import json
import unittest
from unittest.mock import patch

from search_controller.service import Controller
from tests.test_search_cli import SearchCLIWorkspace
from tests.native_reviewer_support import mutate, response
from tests.search_fixtures import contract, review
from research_harness import review_protocol
from research_harness.artifacts import ArtifactStore
from research_harness.errors import ResearchError
from research_harness.native_review import validate_output
from research_harness.storage import Store

ASSIGNMENT_ID = "native-admission"


class NativeReviewOutputCheckTests(SearchCLIWorkspace, unittest.TestCase):
    def setUp(self):
        super().setUp()
        self.controller = Controller(self.root)
        self.controller.command("init", {"contract": contract()}, 0, "initialize")
        self.proposed = self.prepared_proposal()
        self.controller.command("propose", self.proposed, 1, "propose")
        self.expected = review(self.proposed["proposal"])
        self.expected["reviewer"].update(source="host", attestation_id="review-assignment:" + ASSIGNMENT_ID)
        exported = self.controller.command("review-packet", {
            "kind": "proposal", "subject": self.proposed["proposal"], "claim_digest": self.expected["claim_digest"],
            "reviewer": self.expected["reviewer"], "inputs": self.proposed["inputs"]}, None, None)
        self.store = Store(exported["workspace"])
        self.artifacts = ArtifactStore(self.store.root)
        mutate(self.store, review_protocol.record_route, {"id": "route", "adapter": "openai_responses_v1",
            "model": "fixture-model", "endpoint": "https://api.openai.com/v1/responses",
            "configuration": {"max_output_tokens": 10000, "timeout_seconds": 5}})

        def positive(route, request, credential):
            packet = json.loads(request["input"][0]["content"].removeprefix("The packet below is JSON.\n"))
            return response({"allowed_control": packet["allowed_control"],
                             "evidence_control": packet["evidence_control"], "excluded_controls": []})
        with patch("research_harness.review_transport.send_request", side_effect=positive):
            mutate(self.store, review_protocol.probe_route, {"id": "probe", "route_id": "route"}, credential="fixture")
        mutate(self.store, review_protocol.record_assignment, {"id": ASSIGNMENT_ID, "route_id": "route",
            "dossier_id": None, "role": "native_math", "reviewer_id": self.expected["reviewer"]["actor_id"],
            "author_id": "fixture-author", "context": {"native_packet": exported["native_packet"]}})

    def run_assignment(self, attempt_id, output):
        with patch("research_harness.review_transport.send_request", return_value=response(output)):
            return mutate(self.store, review_protocol.invoke_assignment, {"id": attempt_id, "assignment_id": ASSIGNMENT_ID},
                          credential="fixture")

    def state(self):
        return review_protocol.assignment_state(self.store.snapshot()["records"], self.artifacts, ASSIGNMENT_ID)

    def test_a_proposal_review_with_a_reviewer_string_is_refused_and_the_assignment_runs_again(self):
        malformed = copy.deepcopy(self.expected)
        malformed["reviewer"] = "claude-fable-5-1-admission"
        receipt = self.run_assignment("attempt-1", malformed)
        refusal = self.store.snapshot()["records"]["review_output_refusal"]["attempt-1"]
        self.assertEqual((refusal["code"], refusal["check"]), ("invalid_native_review", "native.output"))
        # The receipt reports the refusal and the revision after it, so the next command starts from that revision.
        self.assertEqual((receipt["revision"], receipt["output_refusal"]["check"]), (self.store.revision, "native.output"))
        self.assertFalse(self.state()["ready"])
        self.run_assignment("attempt-2", self.expected)
        state = self.state()
        self.assertTrue(state["ready"])
        self.assertEqual((state["attempt_id"], state["output"]), ("attempt-2", self.expected))
        self.controller.command("review", {"proposal_id": "proposal-000001", "review": self.expected, "inputs": []},
                                2, "review")

    def test_a_well_formed_approval_is_recorded_unchanged(self):
        self.run_assignment("attempt-1", self.expected)
        self.assertNotIn("attempt-1", self.store.snapshot()["records"].get("review_output_refusal", {}))
        self.assertEqual(self.state()["output"], self.expected)

    def test_each_part_of_the_native_proposal_shape_is_checked(self):
        cases = {"subject": dict(self.expected, subject_digest="0" * 64),
                 "claim": dict(self.expected, claim_digest="0" * 64),
                 "decision": dict(self.expected, decision="accept"),
                 "finding": dict(self.expected, findings=dict(self.expected["findings"], novelty=None)),
                 "objection": dict(self.expected, unresolved_objections=[{"blocking": "yes", "description": "x"}]),
                 "extra field": dict(self.expected, verdict="approve")}
        for name, output in cases.items():
            with self.subTest(name):
                packet = json.loads(self.artifacts.read(
                    self.store.snapshot()["records"]["review_assignment"][ASSIGNMENT_ID]["packet"]))["native"]
                with self.assertRaises(ResearchError) as caught:
                    validate_output(packet, output)
                self.assertEqual(caught.exception.code, "invalid_native_review")


class NativeResultAssessmentTests(unittest.TestCase):
    """A result or recovery that is not approved authorizes nothing, so it needs only its decision and a reason."""

    packet = {"kind": "result", "subject_digest": "1" * 64, "claim_digest": "2" * 64, "contract": {},
              "reviewer": {"source": "host", "actor_id": "reviewer", "attestation_id": "review-assignment:a"}}

    def test_a_revision_with_findings_or_without_objections_is_kept(self):
        findings = {key: "Assessed." for key in ("statement", "assumptions", "scope", "dependencies", "policy")}
        for output in ({"decision": "revise", "reason": "The proof omits a case.", "findings": findings},
                       {"decision": "reject", "reason": "The claim is false.", "objections": []},
                       {"decision": "revise", "reason": "A gap remains."}):
            with self.subTest(output=output):
                validate_output(self.packet, output)

    def test_a_revision_without_a_reason_or_with_another_decision_is_refused(self):
        for output in ({"decision": "revise"}, {"decision": "revise", "reason": " "}, {"decision": "pending", "reason": "x"}):
            with self.subTest(output=output), self.assertRaises(ResearchError):
                validate_output(dict(self.packet, kind="recovery", claim_digest=None), output)


if __name__ == "__main__":
    unittest.main()
