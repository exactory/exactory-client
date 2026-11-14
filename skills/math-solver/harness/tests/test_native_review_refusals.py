"""Observed native non-approvals identify the scientific repair without authorizing a transition."""

import copy
import unittest

from search_controller.errors import SearchError
from search_controller.service import Controller
from tests.search_execution_support import admit_workspace
from tests.search_fixtures import digest
from tests.strategy_refresh_support import assessment_spec
from tests.support import run
from tests.test_search_cli import SearchCLIWorkspace
from tests import test_search_evidence as evidence_tests
from tests import test_search_openness_closure as openness_tests


class NativeReviewRefusalTests(SearchCLIWorkspace, unittest.TestCase):
    artifact = evidence_tests.EvidenceTests.artifact
    external_checkpoint = evidence_tests.EvidenceTests.external_checkpoint
    setup_external = evidence_tests.EvidenceTests.setup_external
    result_review = evidence_tests.EvidenceTests.result_review
    accept_spec = evidence_tests.EvidenceTests.accept_spec
    set_up_accepted_proof = openness_tests.OpennessClosureTests.set_up_accepted_proof
    build_closure_spec = openness_tests.OpennessClosureTests.build_closure_spec

    def prepare_command(self, command):
        directory = self.root / command
        directory.mkdir()
        self.root = directory
        target = None
        if command == "reassess":
            run(["init", "sample"], self.root)
            controller = admit_workspace(self.root)
            spec = assessment_spec(controller.command("strategy-context", {}, None, None))
            spec["review"]["claim_digest"] = digest(controller.status()["contract"]["original_claim"])
        elif command == "review":
            self.initialize()
            spec = self.prepared_proposal()
            self.search("propose", spec, revision=1)
            from tests.search_fixtures import review
            spec = {"proposal_id": "proposal-000001", "review": review(spec["proposal"]), "inputs": []}
        elif command == "accept":
            self.setup_external()
            spec = self.accept_spec()
            target = "checkpoint-000001"
        else:
            self.set_up_accepted_proof()
            spec = self.build_closure_spec("unresolved")
        controller = Controller(self.root)
        approved = spec["review"]
        spec["review"] = {key: approved[key] for key in ("subject_digest", "claim_digest", "reviewer")}
        spec["review"].update(
            decision="revise",
            reason="The strategy assessment does not reconcile its predicted outcome with the retreat rules.",
            objections=[
                {"blocking": True, "description": "Order the outstanding checkpoints before the predicted retreat."},
                {"blocking": False, "description": "Deliver the exact failure signal with the revised assessment."},
            ])
        return controller, spec, target

    def assert_refuses(self, command, controller, spec, target, code):
        before = controller.store.tree_path.read_bytes()
        files = sorted(str(path) for path in controller.store.root.rglob("*"))
        result = self.search(command, spec, target=target, revision=controller.status()["revision"], success=False)
        self.assertEqual(result["error"]["code"], code, result)
        self.assertEqual(controller.store.tree_path.read_bytes(), before)
        self.assertEqual(sorted(str(path) for path in controller.store.root.rglob("*")), files)
        return result["error"]

    def test_each_command_refuses_revise_and_reject_with_the_observed_objections(self):
        root = self.root
        for command in ("reassess", "accept", "review", "complete"):
            self.root = root
            controller, spec, target = self.prepare_command(command)
            for decision in ("revise", "reject"):
                with self.subTest(command=command, decision=decision):
                    spec["review"]["decision"] = decision
                    error = self.assert_refuses(command, controller, spec, target, "native_review_not_approved")
                    self.assertEqual(error["details"], {key: spec["review"][key]
                                     for key in ("decision", "reason", "objections")})
                    self.assertIn("revise", error["message"].lower())
                    self.assertIn("assessment", error["message"])
                    self.assertIn("work", error["message"])
                    self.assertIn("new review", error["message"])
                    self.assertEqual(error["stop_class"], "decision")
            if command == "review":
                # Versioned proposal reviews have always recorded revise/reject findings.
                # Their existing observations and stored digests retain that meaning.
                from tests.native_reviewer_support import observe_review
                from tests.search_fixtures import review
                proposed = controller.status()["proposals"]["proposal-000001"]["record"]
                for decision in ("revise", "reject", "approve"):
                    value = observe_review(controller, review(proposed, reviewer="proposal-" + decision,
                                                               decision=decision), proposed)
                    self.search("review", dict(spec, review=value), revision=controller.status()["revision"])
                    stored = list(controller.status()["reviews"].values())[-1]
                    self.assertEqual(stored["record"], value)
                    self.assertEqual(stored["digest"], digest(value))

    def test_malformed_nonapproval_keeps_invalid_record_for_each_command(self):
        root = self.root
        malformed = [
            lambda value: value.pop("decision"),
            lambda value: value.update(decision="approve"),
            lambda value: value.update(decision="maybe"),
            lambda value: value.update(decision=None),
            lambda value: value.pop("reason"),
            lambda value: value.pop("objections"),
            lambda value: value.update(reason=" "),
            lambda value: value.update(objections="Revise the assessment"),
            lambda value: value.update(objections=[{"blocking": "yes", "description": "Repair"}]),
            lambda value: value.update(objections=[{"blocking": True, "description": " "}]),
            lambda value: value.update(objections=[{"description": "Repair"}]),
            lambda value: value.update(subject_digest="not-a-digest"),
            lambda value: value.update(claim_digest=None),
            lambda value: value.update(reviewer={"actor_id": "reviewer"}),
            lambda value: value.update(unexpected=True),
        ]
        for command in ("reassess", "accept", "review", "complete"):
            self.root = root
            controller, spec, target = self.prepare_command(command)
            for decision in ("revise", "reject"):
                for index, mutate in enumerate(malformed):
                    with self.subTest(command=command, decision=decision, defect=index):
                        broken = copy.deepcopy(spec)
                        broken["review"]["decision"] = decision
                        mutate(broken["review"])
                        before = controller.store.tree_path.read_bytes()
                        with self.assertRaises(SearchError) as caught:
                            controller.command(command, broken, controller.status()["revision"],
                                               "malformed-review-" + str(index), target)
                        self.assertEqual(caught.exception.code, "invalid_record")
                        self.assertEqual(controller.store.tree_path.read_bytes(), before)
