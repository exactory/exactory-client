"""Admission rejects unbound review, arbitrary computation, and budget resets."""

import copy
import unittest

from search_controller.errors import SearchError
from search_controller.model import initial_state, apply_event, replay
from tests.search_fixtures import contract, decomposition_proposal, digest, proposal, review, successor

class AdmissionTests(unittest.TestCase):
    def setUp(self):
        self.state = initial_state(contract(), "objective-000001")

    def event(self, state, kind, payload):
        sequence = state["revision"] + 1
        return apply_event(state, {"sequence": sequence, "request_id": "request-" + str(sequence),
                                   "kind": kind, "payload": payload})

    def proposed(self, category="main", value=None, state=None):
        value = proposal(category) if value is None else value
        state = self.state if state is None else state
        state = self.event(state, "proposal_recorded", {"proposal": value, "digest": digest(value)})
        return state, list(state["proposals"])[-1]

    def add_review(self, state, proposal_id, actor="reviewer-one", decision="approve", value=None):
        subject = state["proposals"][proposal_id]["record"]
        value = review(subject, actor, decision) if value is None else value
        return self.event(state, "review_recorded", {"proposal_id": proposal_id, "review": value,
                                                     "digest": digest(value)})

    def admit(self, state, proposal_id):
        return self.event(state, "proposal_admitted", {"proposal_id": proposal_id})

    def approved(self, value=None, state=None):
        state, pid = self.proposed(value=value, state=state)
        return self.admit(self.add_review(state, pid), pid)

    def test_unreviewed_proposal_cannot_execute(self):
        state, pid = self.proposed()
        with self.assertRaises(SearchError) as caught:
            self.admit(state, pid)
        self.assertEqual(caught.exception.code, "admission_required")
        self.assertEqual(state["nodes"], {})

    def test_direct_root_needs_no_self_bridge_and_replay_is_pure(self):
        before = copy.deepcopy(self.state)
        value = proposal()
        event = {"sequence": 1, "request_id": "first", "kind": "proposal_recorded",
                 "payload": {"proposal": value, "digest": digest(value)}}
        document = {"schema_version": 1, "objective_id": "objective-000001",
                    "contract": contract(), "events": [event]}
        saved = copy.deepcopy(document)
        state = replay(document)
        self.assertEqual(state["revision"], 1)
        self.assertEqual(document, saved)
        self.assertEqual(self.state, before)
        state = self.approved()
        self.assertEqual(state["nodes"]["node-000001"]["status"], "admitted")
        self.assertEqual(state["proof_status"], "open")
        self.assertEqual(state["routes"], {})

    def test_author_and_model_authored_reviews_are_rejected(self):
        state, pid = self.proposed()
        for actor, source in [("author", "host"), ("reviewer-one", "model")]:
            value = review(proposal(), actor)
            value["reviewer"]["source"] = source
            with self.subTest(actor=actor), self.assertRaises(SearchError):
                self.add_review(state, pid, value=value)

    def test_standalone_needs_two_independent_approvals(self):
        state, pid = self.proposed("standalone")
        state = self.add_review(state, pid)
        with self.assertRaises(SearchError) as caught:
            self.admit(state, pid)
        self.assertEqual(caught.exception.code, "admission_required")
        state = self.add_review(state, pid, "reviewer-two")
        admitted = self.admit(state, pid)
        self.assertEqual(len(admitted["nodes"]), 1)
        self.assertIsNone(admitted["nodes"]["node-000001"]["obligation_id"])

    def test_standalone_missing_findings_or_blocking_objection_fails(self):
        for defect in ["novelty", "significance", "objection"]:
            state, pid = self.proposed("standalone")
            value = review(proposal("standalone"))
            if defect == "objection":
                value["unresolved_objections"] = [{"blocking": True, "description": "Novelty comparison is incomplete"}]
            else:
                value["findings"][defect] = ""
            with self.subTest(defect=defect), self.assertRaises(SearchError):
                state = self.add_review(state, pid, value=value)
                state = self.add_review(state, pid, "reviewer-two")
                self.admit(state, pid)

    def test_duplicate_review_provenance_does_not_supply_second_approval(self):
        state, pid = self.proposed("standalone")
        state = self.add_review(state, pid)
        with self.assertRaises(SearchError):
            state = self.add_review(state, pid)
            self.admit(state, pid)

    def test_review_and_proposal_digests_are_bound(self):
        state, pid = self.proposed()
        for field in ["subject_digest", "claim_digest"]:
            value = review(proposal())
            value[field] = "0" * 64
            with self.subTest(field=field), self.assertRaises(SearchError):
                self.add_review(state, pid, value=value)
        with self.assertRaises(SearchError):
            self.event(self.state, "proposal_recorded", {"proposal": proposal(), "digest": "0" * 64})

    def test_finite_decision_requires_each_necessity_field(self):
        value = proposal()
        value["task"]["kind"] = "finite_decision"
        necessity = {"obligation_id": "obligation-000001",
                     "omission_consequence": "The recurrence sign decision blocks induction",
                     "domain_justification": "The coefficient has exactly these eleven integer arguments",
                     "outcomes": [{"outcome": "All nonnegative", "next_action": "Prove induction"},
                                  {"outcome": "A negative coefficient", "next_action": "Retreat from recurrence"}],
                     "stopping_condition": "Stop after all eleven exact rational signs are known"}
        for missing in [None] + list(necessity):
            candidate = copy.deepcopy(value)
            candidate["contribution"]["necessity"] = copy.deepcopy(necessity)
            if missing is None:
                candidate["contribution"]["necessity"] = None
            else:
                del candidate["contribution"]["necessity"][missing]
            with self.subTest(missing=missing), self.assertRaises(SearchError):
                self.proposed(value=candidate)
        value["contribution"]["necessity"] = necessity
        state = self.approved(value)
        self.assertEqual(state["acceptances"], {})

    def test_closed_records_reject_invalid_limits_fields_and_scopes(self):
        changes = [("limits", "max_moves", True), ("limits", "max_moves", 25),
                   ("limits", "max_runs", 0), ("limits", "workers", 2),
                   ("claim", "statement", " "), ("claim", "extra", "ignored"),
                   ("claim", "proof_policy", "trusted")]
        for section, key, value in changes:
            candidate = proposal()
            candidate[section][key] = value
            with self.subTest(key=key, value=value), self.assertRaises(SearchError):
                self.proposed(value=candidate)
        for scope in [{"kind": "integer_interval", "lower": 7, "upper": 5,
                       "lower_inclusive": True, "upper_inclusive": True},
                      {"kind": "integer_interval", "lower": 5, "upper": 5,
                       "lower_inclusive": False, "upper_inclusive": True}]:
            candidate = proposal()
            candidate["claim"]["scope"] = scope
            with self.assertRaises(SearchError):
                self.proposed(value=candidate)

    def test_target_claim_policy_and_assumptions_cannot_be_changed(self):
        for key, replacement in [("statement", "Only P(5)"), ("proof_policy", "certificate"),
                                 ("assumption_ids", ["unrecorded-assumption"])]:
            value = proposal()
            value["claim"][key] = replacement
            with self.subTest(key=key), self.assertRaises(SearchError):
                self.proposed(value=value)
        value = proposal()
        value["target_obligation"] = "obligation-999999"
        with self.assertRaises(SearchError):
            self.proposed(value=value)

    def test_renamed_equivalent_attempt_inherits_cumulative_account(self):
        state = self.approved()
        account = state["accounts"]["account-000001"]
        account["used_moves"] = 10
        account["used_runs"] = 7
        value = proposal()
        value["attack_slug"] = "renamed"
        value["equivalent_node_ids"] = ["node-000001"]
        state = self.approved(value, state)
        self.assertEqual(len(state["accounts"]), 1)
        self.assertEqual(state["nodes"]["node-000002"]["account_id"], "account-000001")
        self.assertEqual(state["accounts"]["account-000001"]["used_moves"], 10)

    def test_exhausted_copy_and_hypothesis_cannot_reset_account(self):
        state = self.approved()
        state["accounts"]["account-000001"]["used_moves"] = 24
        for relation in ["main", "alternative", "continuation"]:
            value = successor(state)
            value.update(attack_slug="retry", relationship=relation,
                         logical_predecessor="node-000001", equivalent_node_ids=["node-000001"])
            with self.subTest(relation=relation), self.assertRaises(SearchError) as caught:
                self.approved(value, state)
            self.assertEqual(caught.exception.code, "budget_exhausted")

    def test_unknown_imported_usage_is_not_zeroed(self):
        state = self.approved()
        account = state["accounts"]["account-000001"]
        account.update(historical_usage="unknown", historical_moves=None, historical_runs=None)
        value = proposal()
        value["attack_slug"] = "retry"
        with self.assertRaises(SearchError) as caught:
            self.approved(value, state)
        self.assertEqual(caught.exception.code, "usage_unknown")
        self.assertIsNone(account["historical_moves"])

    def test_three_nonprogress_replans_block_new_admission(self):
        state = copy.deepcopy(self.state)
        state["control"]["nonprogress_replans"] = 3
        with self.assertRaises(SearchError) as caught:
            self.approved(state=state)
        self.assertEqual(caught.exception.code, "replan_limit")

    def test_unknown_events_and_revisions_fail_closed(self):
        with self.assertRaises(SearchError):
            self.event(self.state, "root_claim_changed", {"statement": "P(5)"})
        with self.assertRaises(SearchError):
            apply_event(self.state, {"sequence": True, "request_id": "r", "kind": "proposal_recorded",
                                     "payload": {"proposal": proposal(), "digest": digest(proposal())}})

    def test_reviewed_decomposition_creates_stable_obligations_route_and_account(self):
        state = self.approved(decomposition_proposal())
        self.assertEqual(len(state["obligations"]), 4)
        self.assertEqual(state["routes"]["route-000001"]["premises"],
                         ["obligation-000002", "obligation-000003"])
        self.assertEqual(state["nodes"]["node-000001"]["obligation_id"], "obligation-000002")
        self.assertEqual(state["accounts"]["account-000001"]["owner_obligation"], "obligation-000002")

    def test_decomposition_rejects_unreachable_cycle_and_conflicting_coverage(self):
        for defect in ["cycle", "unreachable", "coverage"]:
            value = decomposition_proposal()
            if defect == "cycle":
                value["decomposition"]["routes"][0]["premises"].append("obligation-000001")
            elif defect == "unreachable":
                value["decomposition"]["routes"][0]["premises"].remove("new:base")
            else:
                value["contribution"]["coverage"]["scope"] = {"kind": "case_ids", "case_ids": ["17"]}
            with self.subTest(defect=defect), self.assertRaises(SearchError):
                self.approved(value)

    def test_continuation_retains_original_category(self):
        state = self.approved()
        value = successor(state)
        state = self.approved(value, state)
        self.assertEqual(state["nodes"]["node-000002"]["category"], "main")
        self.assertEqual(state["nodes"]["node-000002"]["relationship"], "continuation")

    def test_contract_rejects_policy_mismatch_and_unknown_fields(self):
        for field, value in [("proof_policy", "lean-kernel"), ("schema_version", True), ("extra", "ignored")]:
            record = contract()
            record[field] = value
            with self.subTest(field=field), self.assertRaises(SearchError):
                initial_state(record, "objective-000001")

    def test_successor_must_bind_a_checkpoint_and_initial_evidence_cannot_be_invented(self):
        state = self.approved()
        value = proposal()
        value.update(attack_slug="next", relationship="continuation", logical_predecessor="node-000001")
        with self.assertRaises(SearchError):
            self.proposed(value=value, state=state)
        value = proposal()
        value["inherited_evidence"] = ["a" * 64]
        with self.assertRaises(SearchError):
            self.proposed(value=value)

    def renewal(self, state):
        value = successor(state, method="spectral-reduction")
        value["relationship"] = "alternative"
        value["budget"].update(mode="renew", account_id="account-000001",
                               basis_checkpoint_id=value["anchor"]["checkpoint_id"],
                               basis_checkpoint_digest=value["anchor"]["digest"])
        return value

    def test_verified_external_progress_permits_distinct_route_preserving_history(self):
        state = self.approved()
        state["accounts"]["account-000001"]["used_moves"] = 24
        value = self.renewal(state)
        state = self.approved(value, state)
        account = state["accounts"]["account-000002"]
        self.assertEqual(account["historical_moves"], 24)
        self.assertEqual(account["lineage_owner"], "account-000001")
        self.assertEqual(state["accounts"]["account-000001"]["used_moves"], 24)
        copied = successor(state, method="spectral-reduction")
        copied.update(attack_slug="next-again", logical_predecessor="node-000002")
        state = self.approved(copied, state)
        self.assertEqual(state["nodes"]["node-000003"]["account_id"], "account-000002")

    def test_hypothesis_stale_acceptance_or_reused_progress_cannot_renew(self):
        for defect in ["hypothesis", "stale", "unaccepted", "same_method"]:
            state = self.approved()
            state["accounts"]["account-000001"]["used_moves"] = 24
            value = self.renewal(state)
            cp = state["checkpoints"]["checkpoint-000001"]
            if defect == "hypothesis":
                cp["kind"] = "hypothesis"
                value["anchor"]["digest"] = digest(cp)
                value["budget"]["basis_checkpoint_digest"] = digest(cp)
                state["acceptances"]["acceptance-000001"]["checkpoint_digest"] = digest(cp)
            elif defect == "stale":
                state["acceptances"]["acceptance-000001"]["status"] = "invalidated"
            elif defect == "unaccepted":
                state["acceptances"] = {}
            else:
                value["method"] = "induction"
                value["studies"]["strategies"][0]["method"] = "induction"
            with self.subTest(defect=defect), self.assertRaises(SearchError):
                self.approved(value, state)
        state = self.approved()
        state["accounts"]["account-000001"]["used_moves"] = 24
        state = self.approved(self.renewal(state), state)
        state["accounts"]["account-000002"]["used_moves"] = 24
        value = self.renewal(state)
        value.update(attack_slug="third", method="another-method", logical_predecessor="node-000002")
        value["studies"]["strategies"][0]["method"] = "another-method"
        value["budget"]["account_id"] = "account-000002"
        with self.assertRaises(SearchError):
            self.approved(value, state)

    def test_reviewed_renewal_preserves_unknown_history(self):
        state = self.approved()
        state["accounts"]["account-000001"].update(historical_usage="unknown", historical_moves=None, historical_runs=None)
        state = self.approved(self.renewal(state), state)
        account = state["accounts"]["account-000002"]
        self.assertEqual(account["historical_usage"], "unknown")
        self.assertIsNone(account["historical_moves"])
        self.assertIsNone(account["historical_runs"])

    def test_admitted_reviews_cannot_be_replaced_and_total_cap_survives_new_obligation(self):
        state = self.approved()
        with self.assertRaises(SearchError):
            self.add_review(state, "proposal-000001", "later-reviewer", "reject")
        configured = contract()
        configured["resource_policy"]["max_total_moves"] = 24
        state = initial_state(configured, "objective-000001")
        state["totals"]["used_moves"] = 24
        value = decomposition_proposal()
        value["anchor"]["digest"] = digest(configured)
        with self.assertRaises(SearchError) as caught:
            self.approved(value, state)
        self.assertEqual(caught.exception.code, "budget_exhausted")

    def test_store_validation_rejects_admission_without_persisting_candidate(self):
        import tempfile
        from pathlib import Path
        from search_controller.storage import Store
        with tempfile.TemporaryDirectory() as directory:
            store = Store(Path(directory) / ".search")
            store.initialize(contract(), "objective-000001")
            validator = lambda document, event: apply_event(replay(document), event)
            store.append("proposal_recorded", {"proposal": proposal(), "digest": digest(proposal())},
                         0, "propose", validate=validator)
            before = store.read()
            with self.assertRaises(SearchError):
                store.append("proposal_admitted", {"proposal_id": "proposal-000001"},
                             1, "admit", validate=validator)
            self.assertEqual(store.read(), before)

    def test_standalone_finite_task_can_bind_its_own_prospective_claim(self):
        value = proposal("standalone")
        value["task"]["kind"] = "finite_proof"
        value["contribution"]["necessity"] = {
            "obligation_id": None, "omission_consequence": "The prospective theorem needs its exceptional cases",
            "domain_justification": "The reduction leaves exactly these finite exceptions",
            "outcomes": [{"outcome": "Certified", "next_action": "Assemble the theorem"},
                         {"outcome": "Failed", "next_action": "Retreat from the proposed theorem"}],
            "stopping_condition": "Finish the finite certificate list or stop on the first failed case"}
        state, pid = self.proposed(value=value)
        state = self.add_review(self.add_review(state, pid), pid, "reviewer-two")
        self.assertEqual(len(self.admit(state, pid)["nodes"]), 1)

    def test_same_case_set_with_different_order_cannot_reset_standalone_allowance(self):
        value = proposal("standalone")
        value["claim"]["scope"] = {"kind": "case_ids", "case_ids": ["a", "b"]}
        state, pid = self.proposed(value=value)
        state = self.admit(self.add_review(self.add_review(state, pid), pid, "reviewer-two"), pid)
        state["accounts"]["account-000001"]["used_moves"] = 24
        value["attack_slug"] = "reordered"
        value["claim"]["scope"]["case_ids"].reverse()
        state, pid = self.proposed(value=value, state=state)
        state = self.add_review(self.add_review(state, pid), pid, "reviewer-two")
        with self.assertRaises(SearchError) as caught:
            self.admit(state, pid)
        self.assertEqual(caught.exception.code, "budget_exhausted")

    def test_continuation_cannot_select_another_accounts_allowance(self):
        state = self.approved()
        state["accounts"]["account-000001"]["used_moves"] = 24
        state = self.approved(self.renewal(state), state)
        value = successor(state)
        value["attack_slug"] = "misdirected-continuation"
        value["budget"].update(mode="inherit", account_id="account-000002")
        with self.assertRaises(SearchError):
            self.approved(value, state)

    def test_invalidated_renewal_basis_cannot_support_further_admission(self):
        state = self.approved()
        state["accounts"]["account-000001"]["used_moves"] = 24
        state = self.approved(self.renewal(state), state)
        value = successor(state, method="spectral-reduction")
        value.update(attack_slug="after-invalidation", logical_predecessor="node-000002")
        state["acceptances"]["acceptance-000001"]["status"] = "invalidated"
        with self.assertRaises(SearchError):
            self.approved(value, state)

    def test_invalid_review_records_are_rejected_without_affecting_state(self):
        state, pid = self.proposed()
        for defect in ["unknown", "boolean", "attestation", "verdict"]:
            value = review(proposal(), "reviewer-two")
            if defect == "unknown":
                value["findings"]["extra"] = "Ignored assurance"
            elif defect == "boolean":
                value["unresolved_objections"] = [{"blocking": 1, "description": "Concern"}]
            elif defect == "attestation":
                value["reviewer"]["attestation_id"] = "host-author"
            else:
                value["decision"] = "accepted"
            before = copy.deepcopy(state)
            with self.subTest(defect=defect), self.assertRaises(SearchError):
                self.add_review(state, pid, value=value)
            self.assertEqual(state, before)

    def test_unknown_objective_history_blocks_renewal_under_finite_total_cap(self):
        configured = contract()
        configured["resource_policy"]["max_total_runs"] = 48
        state = initial_state(configured, "objective-000001")
        value = proposal()
        value["anchor"]["digest"] = digest(configured)
        state = self.approved(value, state)
        state["accounts"]["account-000001"].update(historical_usage="unknown", historical_moves=None, historical_runs=None)
        state["totals"]["historical_usage"] = "unknown"
        with self.assertRaises(SearchError) as caught:
            self.approved(self.renewal(state), state)
        self.assertEqual(caught.exception.code, "usage_unknown")
        self.assertEqual(state["totals"]["historical_usage"], "unknown")

    def test_equivalent_decomposition_cannot_mint_a_fresh_obligation_account(self):
        state = self.approved(decomposition_proposal())
        state["accounts"]["account-000001"]["used_moves"] = 24
        value = decomposition_proposal()
        value["attack_slug"] = "copied-base-cases"
        with self.assertRaises(SearchError) as caught:
            self.approved(value, state)
        self.assertEqual(caught.exception.code, "budget_exhausted")
        self.assertEqual(len(state["obligations"]), 4)

    def test_root_statement_and_policy_are_frozen_in_derived_state(self):
        for target in ["contract", "obligation"]:
            state = copy.deepcopy(self.state)
            if target == "contract":
                state["contract"]["original_claim"]["statement"] = "Only P(5)"
            else:
                state["obligations"]["obligation-000001"]["claim"]["proof_policy"] = "certificate"
            with self.subTest(target=target), self.assertRaises(SearchError) as caught:
                self.proposed(state=state)
            self.assertEqual(caught.exception.code, "contract_changed")

    def test_copying_a_checkpoint_under_a_new_id_does_not_make_new_progress(self):
        state = self.approved()
        state["accounts"]["account-000001"]["used_moves"] = 24
        state = self.approved(self.renewal(state), state)
        state["accounts"]["account-000002"]["used_moves"] = 24
        value = self.renewal(state)
        copied = copy.deepcopy(state["checkpoints"]["checkpoint-000001"])
        copied["id"] = "checkpoint-000002"
        state["checkpoints"][copied["id"]] = copied
        state["acceptances"]["acceptance-000002"] = {
            "checkpoint_id": copied["id"], "checkpoint_digest": digest(copied), "status": "accepted"}
        value.update(attack_slug="copied-progress", method="third-method", logical_predecessor="node-000002")
        value["studies"]["strategies"][0]["method"] = "third-method"
        value["anchor"].update(checkpoint_id=copied["id"], digest=digest(copied))
        value["budget"].update(account_id="account-000002", basis_checkpoint_id=copied["id"],
                               basis_checkpoint_digest=digest(copied))
        with self.assertRaises(SearchError) as caught:
            self.approved(value, state)
        self.assertEqual(caught.exception.code, "budget_exhausted")

    def second_renewal(self, state, method="third-method", account_id="account-000002"):
        value = self.renewal(state)
        fresh = copy.deepcopy(state["checkpoints"]["checkpoint-000001"])
        fresh.update(id="checkpoint-000002", evidence_digests=["b" * 64],
                     origin={"kind": "external_result", "source": "New exact obstruction theorem"})
        state["checkpoints"][fresh["id"]] = fresh
        state["acceptances"]["acceptance-000002"] = {
            "checkpoint_id": fresh["id"], "checkpoint_digest": digest(fresh), "status": "accepted"}
        value.update(attack_slug="third", method=method, logical_predecessor="node-000002", inherited_evidence=fresh["evidence_digests"][:])
        value["studies"]["strategies"][0]["method"] = method
        value["anchor"].update(checkpoint_id=fresh["id"], digest=digest(fresh))
        value["budget"].update(account_id=account_id, basis_checkpoint_id=fresh["id"],
                               basis_checkpoint_digest=digest(fresh))
        return value

    def two_account_lineage(self, first_moves=24):
        state = self.approved()
        state["accounts"]["account-000001"].update(used_moves=first_moves, used_runs=7)
        state = self.approved(self.renewal(state), state)
        state["accounts"]["account-000002"].update(used_moves=24, used_runs=11)
        return state

    def test_explicit_superseded_account_cannot_renew_and_lose_later_usage(self):
        state = self.two_account_lineage()
        value = self.second_renewal(state, method="spectral-reduction", account_id="account-000001")
        before = copy.deepcopy(state)
        with self.assertRaises(SearchError) as caught:
            self.approved(value, state)
        self.assertEqual(caught.exception.code, "account_superseded")
        self.assertEqual(state, before)

    def test_current_segment_renewal_retains_all_lineage_usage(self):
        state = self.two_account_lineage()
        state = self.approved(self.second_renewal(state), state)
        account = state["accounts"]["account-000003"]
        self.assertEqual(account["historical_moves"], 48)
        self.assertEqual(account["historical_runs"], 18)
        self.assertEqual(account["predecessor_account_id"], "account-000002")
        self.assertEqual(account["lineage_owner"], "account-000001")

    def test_current_renewal_cannot_recycle_a_method_from_an_earlier_segment(self):
        state = self.two_account_lineage()
        value = self.second_renewal(state, method="induction")
        with self.assertRaises(SearchError) as caught:
            self.approved(value, state)
        self.assertEqual(caught.exception.code, "budget_exhausted")

    def test_superseded_account_cannot_inherit_or_continue_unused_allowance(self):
        for relation in ["main", "continuation"]:
            state = self.two_account_lineage(first_moves=10)
            value = successor(state)
            value.update(attack_slug="old-unused-allowance", relationship=relation)
            value["budget"].update(mode="inherit", account_id="account-000001")
            with self.subTest(relation=relation):
                with self.assertRaises(SearchError) as caught:
                    self.approved(value, state)
                self.assertEqual(caught.exception.code, "account_superseded")

    def test_any_pending_lineage_reservation_blocks_renewal(self):
        for field in ["reserved_moves", "reserved_runs"]:
            state = self.two_account_lineage()
            state["accounts"]["account-000001"][field] = 1
            with self.subTest(field=field):
                with self.assertRaises(SearchError) as caught:
                    self.approved(self.second_renewal(state), state)
                self.assertEqual(caught.exception.code, "budget_exhausted")

    def test_execution_account_guard_rejects_superseded_node_account(self):
        from search_controller import admission
        guard = getattr(admission, "require_current_account", None)
        self.assertIsNotNone(guard, "Execution needs a state-aware current-account guard")
        state = self.two_account_lineage(first_moves=10)
        before = copy.deepcopy(state)
        with self.assertRaises(SearchError) as caught:
            guard(state, "account-000001")
        self.assertEqual(caught.exception.code, "account_superseded")
        self.assertEqual(guard(state, "account-000002")["id"], "account-000002")
        self.assertEqual(state, before)


if __name__ == "__main__":
    unittest.main()
