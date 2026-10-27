"""Proposal reviews retain exact evidence across unrelated native completion."""

import copy
import json
from unittest.mock import patch

from research_harness.artifacts import ArtifactStore
from research_harness.storage import Store
from search_controller.errors import SearchError
from search_controller.model import replay
from search_controller.reviewer import packet, packet_context_drift, require_review
from search_controller.schema import digest
from search_controller.service import Controller
from search_controller.storage import canonical_bytes
from tests.native_reviewer_support import observe_review
from tests.search_execution_support import admit_workspace, begin_spec, invoke
from tests.search_fixtures import provenance, review
from tests.support import WorkspaceTest, make_move
from tests.test_search_cli import SearchCLIWorkspace
from tests.test_search_standalone_proof import standalone_proposal


class ProposalReviewContextTests(WorkspaceTest):
    def setUp(self):
        super().setUp()
        self.controller = admit_workspace(self.attack_root)
        invoke(self.controller, "begin", begin_spec())
        self.assertEqual(self.run_cli("journal", "add", self.slug, "--json",
                                     json.dumps(make_move(1, closes=True)))[0], 0)
        self.root = self.attack_root
        proposed = SearchCLIWorkspace.prepared_proposal(self)
        self.proposal = proposed["proposal"]
        standalone = standalone_proposal()
        for key in ("category", "relationship", "claim", "target_obligation", "checkpoint_criteria", "contribution"):
            self.proposal[key] = standalone[key]
        self.proposal["anchor"]["digest"] = digest(self.controller.status()["contract"])
        self.proposal["contribution"]["standalone"]["primary_sources"] = [self.proposal["studies"]["novelty"]]
        self.command("propose", proposed)
        self.proposal_id = "proposal-000002"

    def command(self, name, spec, target=None):
        revision = self.controller.status()["revision"]
        return self.controller.command(name, spec, revision, "context-{}-{}".format(name, revision), target)

    def observed(self, actor="reviewer-one", **options):
        return observe_review(self.controller, review(self.proposal, actor), self.proposal, **options)

    def submit(self, value):
        return self.command("review", {"proposal_id": self.proposal_id, "review": value, "inputs": []})

    def packet(self, value):
        identifier = value["reviewer"]["attestation_id"].split(":", 1)[1]
        assignment = Store(self.root).snapshot()["records"]["review_assignment"][identifier]
        return json.loads(ArtifactStore(self.root).read(assignment["context"]["native_packet"]))

    def finish_unrelated_node(self):
        for command in ("stall", "finish"):
            result = self.run_cli(command, self.slug)
            self.assertEqual(result[0], 0, result)
        self.command("reconcile", {})
        self.assertEqual(self.controller.status()["nodes"]["node-000001"]["status"], "finished")

    def test_parallel_proposal_reviews_survive_unrelated_native_finish(self):
        first, second = self.observed(), self.observed("reviewer-two")
        supplied = self.packet(second)
        before = copy.deepcopy(supplied)
        self.assertEqual(supplied["scientific_context"]["nodes"][0]["status"], "active")
        self.assertEqual(len(supplied["scientific_context"]["current"]["strategies"]), 1)
        self.submit(first)
        self.finish_unrelated_node()
        state = self.controller.status()
        expected = packet(self.root, state, self.controller.store, self.proposal, "proposal",
                          digest(self.proposal["claim"]), second["reviewer"], ArtifactStore(self.root),
                          common_records=Store(self.root).snapshot()["records"])
        # Establish the exact reported failure: no scientific evidence changed.
        comparable = copy.deepcopy(expected)
        comparable["scientific_context"]["nodes"][0]["status"] = "active"
        comparable["scientific_context"]["current"]["strategies"] = supplied["scientific_context"]["current"]["strategies"]
        self.assertEqual(comparable, supplied)
        for status in ("admitted", "active", "waiting", "result_ready"):
            with self.subTest(earlier_status=status):
                earlier = copy.deepcopy(supplied)
                earlier["scientific_context"]["nodes"][0]["status"] = status
                before_comparison = canonical_bytes([earlier, expected, state])
                receipt = packet_context_drift(earlier, expected, state)
                self.assertEqual(receipt[-1], {"path": "/scientific_context/nodes/0/status",
                                              "from": status, "to": "finished"})
                self.assertEqual(canonical_bytes([earlier, expected, state]), before_comparison)
        revision = state["revision"]
        legacy_document = self.controller.store.read()
        legacy_bytes = canonical_bytes(legacy_document)
        legacy_state = replay(legacy_document)
        common_before = Store(self.root).snapshot()
        original_replace = self.controller.store._atomic_replace
        def interrupt_commit(path, raw):
            if path == self.controller.store.tree_path:
                candidate = json.loads(raw)
                payload = candidate["events"][-1]["payload"]["operations"][0]["payload"]
                self.assertIn("context_drift", payload)
                self.assertEqual(payload["review"], second)
                self.assertEqual(payload["digest"], digest(second))
                raise OSError("Interrupted native review commit")
            return original_replace(path, raw)
        with patch.object(self.controller.store, "_atomic_replace", side_effect=interrupt_commit):
            with self.assertRaisesRegex(OSError, "Interrupted native review commit"):
                self.submit(second)
        self.assertEqual(canonical_bytes(self.controller.store.read()), legacy_bytes)
        self.controller = Controller(self.root)
        response = self.submit(second)
        saved = Controller(self.root).status()["reviews"]["review-000003"]
        self.assertEqual(saved["record"], second)
        self.assertEqual(saved["digest"], digest(second))
        drift = [{"path": "/scientific_context/current/strategies",
                  "from": supplied["scientific_context"]["current"]["strategies"], "to": []},
                 {"path": "/scientific_context/nodes/0/status", "from": "active", "to": "finished"}]
        self.assertEqual(saved["context_drift"], drift)
        self.assertNotIn("context_drift", self.controller.status()["reviews"]["review-000002"])
        event = self.controller.store.read()["events"][-1]
        self.assertEqual(event["payload"]["operations"][0]["payload"]["context_drift"], drift)
        self.assertEqual(self.packet(second), before)
        self.controller = Controller(self.root)
        before_retry = self.controller.store.tree_path.read_bytes()
        retry = self.controller.command("review", {"proposal_id": self.proposal_id, "review": second, "inputs": []},
                                        revision, "context-review-{}".format(revision))
        self.assertEqual(retry, response)
        self.assertEqual(self.controller.store.tree_path.read_bytes(), before_retry)
        self.assertEqual(len(self.controller.status()["reviews"]), 3)
        self.assertEqual(Store(self.root).snapshot(), common_before)
        third = self.observed("reviewer-three")
        self.submit(third)
        mixed_document = self.controller.store.read()
        old_prefix = dict(mixed_document, events=mixed_document["events"][:revision])
        self.assertEqual(canonical_bytes(old_prefix), legacy_bytes)
        self.assertEqual(replay(old_prefix), legacy_state)
        restarted = Controller(self.root).status()
        for identity, old_review in legacy_state["reviews"].items():
            self.assertEqual(restarted["reviews"][identity], old_review)
            self.assertNotIn("context_drift", restarted["reviews"][identity])
        self.assertEqual(restarted["reviews"]["review-000003"], saved)
        self.assertEqual(restarted["reviews"]["review-000004"]["record"], third)
        self.assertNotIn("context_drift", restarted["reviews"]["review-000004"])
        self.command("admit", {}, "proposal-000002")
        self.assertEqual(self.controller.status()["nodes"]["node-000002"]["category"], "standalone")

    def assert_refused(self, value, code="review_packet_mismatch"):
        before = self.controller.store.read()
        before_bytes = self.controller.store.tree_path.read_bytes()
        with self.assertRaises(SearchError) as caught:
            self.submit(value)
        self.assertEqual(caught.exception.code, code)
        self.assertEqual(self.controller.store.read(), before)
        self.assertEqual(self.controller.store.tree_path.read_bytes(), before_bytes)

    def test_packet_json_types_remain_exact_without_context_drift(self):
        alterations = {
            "contract_boolean": lambda p: p["contract"]["resource_policy"].update(max_workers=True),
            "contract_float": lambda p: p["contract"]["resource_policy"].update(max_workers=1.0),
            "context_boolean": lambda p: p["scientific_context"]["obligations"]["obligation-000001"].update(
                schema_version=True),
            "context_float": lambda p: p["scientific_context"]["obligations"]["obligation-000001"].update(
                schema_version=1.0),
        }
        for name, alter in alterations.items():
            with self.subTest(field=name):
                self.assert_refused(self.observed("reviewer-" + name, alter_packet=alter))

    def test_packet_json_types_remain_exact_with_unrelated_finish(self):
        alterations = {
            "contract_boolean": lambda p: p["contract"]["resource_policy"].update(max_workers=True),
            "contract_float": lambda p: p["contract"]["resource_policy"].update(max_workers=1.0),
            "context_boolean": lambda p: p["scientific_context"]["obligations"]["obligation-000001"].update(
                schema_version=True),
            "context_float": lambda p: p["scientific_context"]["obligations"]["obligation-000001"].update(
                schema_version=1.0),
        }
        observed = {name: self.observed("reviewer-" + name, alter_packet=alter)
                    for name, alter in alterations.items()}
        self.finish_unrelated_node()
        for name, value in observed.items():
            with self.subTest(field=name):
                self.assert_refused(value)

    def test_rebinding_observed_review_records_drift_on_each_binding(self):
        value = self.observed()
        self.submit(value)
        original = self.controller.status()["reviews"]["review-000002"]
        self.command("propose", {"proposal": self.proposal, "inputs": []})
        self.proposal_id = "proposal-000003"
        self.finish_unrelated_node()
        self.submit(value)
        reviews = Controller(self.root).status()["reviews"]
        self.assertEqual(reviews["review-000002"], original)
        self.assertEqual(reviews["review-000003"]["record"], value)
        self.assertEqual(reviews["review-000003"]["digest"], digest(value))
        self.assertEqual([row["path"] for row in reviews["review-000003"]["context_drift"]],
                         ["/scientific_context/current/strategies", "/scientific_context/nodes/0/status"])

    def test_rebinding_observed_review_rechecks_protected_context(self):
        from tests.strategy_refresh_support import reassess_fixture
        value = self.observed()
        self.submit(value)
        self.command("propose", {"proposal": self.proposal, "inputs": []})
        self.proposal_id = "proposal-000003"
        reassess_fixture(self.controller, set())
        self.finish_unrelated_node()
        self.assert_refused(value)

    def test_protected_packet_changes_are_refused_even_with_unrelated_finish(self):
        study = self.proposal["studies"]["problem"]
        alterations = {
            "contract": lambda p: p["contract"]["required_deliverables"].append("Changed requirement"),
            "omitted_study": lambda p: p.update(evidence=[e for e in p["evidence"] if e["digest"] != study]),
            "execution_provenance": lambda p: p["execution_provenance"].append({
                "path": "/changed/checker", "digest": "f" * 64, "size": 1, "role": "native_verifier_executable"}),
            "unmaterialized_digests": lambda p: p["unmaterialized_digests"].append("e" * 64),
            "node_claim": lambda p: p["scientific_context"]["nodes"][0]["claim"].update(statement="Changed claim"),
            "node_relationship": lambda p: p["scientific_context"]["nodes"][0].update(relationship="standalone"),
            "node_removed": lambda p: p["scientific_context"].update(nodes=[]),
            "malformed_nodes": lambda p: p["scientific_context"].update(nodes={}),
            "invalid_status": lambda p: p["scientific_context"]["nodes"][0].update(status="invented"),
            "study_binding": lambda p: p["scientific_context"]["studies"]["node-000001"].update(problem="e" * 64),
            "strategy_digest": lambda p: p["scientific_context"]["current"]["strategies"][0].update(study_digest="e" * 64),
            "strategy_removed_while_active": lambda p: p["scientific_context"]["current"].update(strategies=[]),
            "obligations": lambda p: p["scientific_context"].update(obligations={}),
            "routes": lambda p: p["scientific_context"]["routes"].update({"omitted-route": {}}),
            "assessments": lambda p: p["scientific_context"]["previous_assessments"].append({"what_changed": "New findings"}),
            "planning": lambda p: p["scientific_context"]["current"]["planning_priorities"].update(deferred_node_ids=["node-000001"]),
            "current_evidence": lambda p: p["scientific_context"]["current"]["evidence"].update({"new-finding": {}}),
            "openness": lambda p: p["scientific_context"].update(openness_minimum={"statement": "Another statement"}),
        }
        observed = {name: self.observed(alter_packet=alter) for name, alter in alterations.items()}
        self.finish_unrelated_node()
        for name, value in observed.items():
            with self.subTest(field=name):
                self.assert_refused(value)

    def test_different_subject_and_claim_cannot_bind_to_this_proposal(self):
        observed = []
        for change_claim in (False, True):
            subject = copy.deepcopy(self.proposal)
            if change_claim:
                subject["claim"]["statement"] = "A different mathematical assertion"
            else:
                subject["hypothesis"] = "A different proposed proof method"
            self.controller.store.put_blob(subject)
            observed.append(observe_review(self.controller, review(subject), subject))
        self.finish_unrelated_node()
        for value in observed:
            with self.subTest(claim_digest=value["claim_digest"]):
                self.assert_refused(value, "digest_mismatch")

    def test_reviewer_identity_and_observed_findings_remain_exact(self):
        value = self.observed()
        self.finish_unrelated_node()
        for field in ("reviewer", "findings"):
            changed = copy.deepcopy(value)
            if field == "reviewer":
                changed["reviewer"]["actor_id"] = "substitute-reviewer"
            else:
                changed["findings"]["scope"] = "Author-written replacement finding"
            with self.subTest(field=field):
                self.assert_refused(changed, "review_output_mismatch")

    def test_native_parent_completion_is_not_unrelated_context(self):
        subject = copy.deepcopy(self.proposal)
        value = self.observed()
        supplied = self.packet(value)
        self.proposal.update(native_parent="node-000001", attack_slug="native-child")
        self.command("propose", {"proposal": self.proposal, "inputs": []})
        self.proposal_id = "proposal-000003"
        value = self.observed()
        self.finish_unrelated_node()
        self.assert_refused(value)
        state = self.controller.status()
        expected = packet(self.root, state, self.controller.store, subject, "proposal",
                          digest(subject["claim"]), supplied["reviewer"], ArtifactStore(self.root),
                          common_records=Store(self.root).snapshot()["records"])
        self.assertTrue(packet_context_drift(supplied, expected, state))
        alterations = {
            "relationship": lambda p: p.update(relationship="alternative"),
            "predecessor": lambda p: p.update(logical_predecessor="node-000001"),
            "equivalent": lambda p: p.update(equivalent_node_ids=["node-000001"]),
            "evidence": lambda p: p.update(inherited_evidence=[p["studies"]["problem"]]),
            "assumptions": lambda p: p["claim"].update(assumption_ids=["obligation-000001"]),
            "inherited_assumptions": lambda p: p.update(inherited_assumption_ids=["obligation-000001"]),
            "budget": lambda p: p["budget"].update(mode="inherit", account_id="account-000001"),
            "renewal": lambda p: p["budget"].update(mode="renew", account_id="account-000001",
                                                   basis_checkpoint_id="checkpoint-000001",
                                                   basis_checkpoint_digest="a" * 64),
            "anchor": lambda p: p["anchor"].update(kind="checkpoint", checkpoint_id="checkpoint-000001"),
        }
        for name, alter in alterations.items():
            old, current = copy.deepcopy(supplied), copy.deepcopy(expected)
            for candidate in (old, current):
                alter(candidate["subject"])
                candidate["subject_digest"] = digest(candidate["subject"])
                candidate["claim_digest"] = digest(candidate["subject"]["claim"])
            with self.subTest(dependency=name), self.assertRaises(SearchError) as caught:
                packet_context_drift(old, current, state)
            self.assertEqual(caught.exception.code, "review_packet_mismatch")

    def test_equivalent_claim_completion_is_not_unrelated_context(self):
        self.proposal.update(claim=self.controller.status()["contract"]["original_claim"], attack_slug="same-claim")
        self.command("propose", {"proposal": self.proposal, "inputs": []})
        self.proposal_id = "proposal-000003"
        value = self.observed()
        self.finish_unrelated_node()
        self.assert_refused(value)

    def test_new_strategy_assessment_still_invalidates_proposal_packet(self):
        from tests.strategy_refresh_support import reassess_fixture
        value = self.observed()
        reassess_fixture(self.controller, set())
        self.assert_refused(value)

    def test_strategy_review_still_requires_its_current_objective_context(self):
        from tests.strategy_refresh_support import assessment_spec
        spec = assessment_spec(self.controller.command("strategy-context", {}, None, None), set())
        spec["review"]["claim_digest"] = digest(self.controller.status()["contract"]["original_claim"])
        subjects = {"strategy": spec["assessment"],
                    "recovery": {"incident_digest": "a" * 64},
                    "root_closure": {"objective_id": self.controller.status()["objective_id"],
                                     "acceptance_ids": []}}
        recovery = {"schema_version": 1, "subject_digest": digest(subjects["recovery"]),
                    "reviewer": provenance("recovery-reviewer"), "decision": "approve",
                    "findings": {key: "Checked the exact recovery evidence" for key in
                                 ("read_only_origin", "delta", "authority", "preservation", "quiescence")},
                    "unresolved_objections": []}
        closure = dict(copy.deepcopy(spec["review"]), subject_digest=digest(subjects["root_closure"]),
                       reviewer=provenance("closure-reviewer"))
        outputs = {"strategy": spec["review"], "recovery": recovery, "root_closure": closure}
        observed = {name: observe_review(self.controller, outputs[name], subject)
                    for name, subject in subjects.items()}
        self.finish_unrelated_node()
        before = self.controller.store.tree_path.read_bytes()
        for name, value in observed.items():
            with self.subTest(kind=name), self.assertRaises(SearchError) as caught:
                require_review(self.root, self.controller.status(), self.controller.store, value,
                               subject=subjects[name], new=True)
            self.assertEqual(caught.exception.code, "review_packet_mismatch")
        self.assertEqual(self.controller.store.tree_path.read_bytes(), before)

    def test_result_openness_minimum_and_its_evidence_remain_exact(self):
        from tests.openness_support import attach_minimum
        from tests.test_search_openness_closure import build_openness_review
        subject = {"claim": self.proposal["claim"], "evidence_digests": [self.proposal["studies"]["novelty"]]}
        value = attach_minimum(self.controller, build_openness_review(subject, subject["claim"], "supported_open"),
                               subject=subject)
        search = value["findings"]["openness"]["minimum"]["searches"][0]["search_digest"]
        alterations = {
            "minimum": lambda p: p["scientific_context"]["openness_minimum"].update(statement="Changed statement"),
            "evidence": lambda p: p.update(evidence=[e for e in p["evidence"] if e["digest"] != search]),
        }
        observed = {name: observe_review(self.controller, value, subject, alter_packet=alter)
                    for name, alter in alterations.items()}
        self.finish_unrelated_node()
        for name, value in observed.items():
            with self.subTest(field=name), self.assertRaises(SearchError) as caught:
                require_review(self.root, self.controller.status(), self.controller.store, value, subject=subject, new=True)
            self.assertEqual(caught.exception.code, "review_packet_mismatch")
