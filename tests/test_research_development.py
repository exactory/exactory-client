import copy
from unittest.mock import patch

from development_fixtures import DevelopmentCase
from research_harness.artifacts import ArtifactStore
from research_harness.errors import ResearchError
from research_harness.literature import import_bundle


class DevelopmentTests(DevelopmentCase):
    def test_a_post_execution_full_scope_change_cannot_claim_the_prospective_plan(self):
        api = self.development()
        self.prepared_study()
        plan, execution = self.run_cycle()
        payload = self.assessment(plan, execution)
        payload["scope"] = dict(payload["scope"], id="unplanned-weaker-scope", assumptions=[])
        payload["assumptions"] = []
        payload["development"]["novelty"]["scope"] = payload["scope"]
        result = self.mutate(api.assess_cycle, payload)["result"]
        self.assertFalse(result["validated_result"])
        self.assertIn("result_scope_unplanned", {o["code"] for o in result["obligations"]})

    def test_a_distinct_full_scope_can_weaken_assumptions_without_replacing_the_objective(self):
        api = self.development()
        plan, execution, payload = self.prepared_candidate()
        successor = self.plan("weaker-branch")
        successor["scope"] = dict(successor["scope"], id="weaker-scope", assumptions=[])
        successor["literature"]["scope"] = successor["scope"]
        successor["strategy"]["kind"] = "weaker_assumptions"
        successor["question"] = "Can the finite result be derived without retaining the extra stated premise?"
        successor["predecessor"] = "checkpoint-1"
        successor["inheritance"] = [{"checkpoint_id": "checkpoint-1", "assessment_id": payload["id"], "use": "validated_result",
            "evidence": [self.result_evidence(execution)], "assumptions": plan["scope"]["assumptions"],
            "deduction": "The prior conditional result remains intact; the weaker formulation needs its own new test."}]
        self.mutate(api.plan_cycle, successor)
        records = self.store.snapshot()["records"]
        self.assertEqual(records["configuration"]["research"]["target"], self.objective)
        self.assertEqual(records["development_scope"]["weaker-scope"]["scope"]["assumptions"], [])
        self.assertEqual(records["development_scope"][plan["scope"]["id"]]["scope"]["assumptions"], plan["scope"]["assumptions"])
        self.assertFalse(api.readiness_report(self.store)["ready"])

    def test_validated_inheritance_cannot_substitute_an_unassessed_locator(self):
        api = self.development()
        _, execution, payload = self.prepared_candidate()
        successor = self.plan("unassessed-inheritance")
        successor.update(predecessor="checkpoint-1", question="Does a different deduction resolve the remaining development?")
        unrelated = self.result_evidence(execution)
        unrelated["locator"] = {"kind": "json", "pointer": "/validation", "value": {"passed": True}}
        successor["inheritance"] = [{"checkpoint_id": "checkpoint-1", "assessment_id": payload["id"], "use": "validated_result",
            "evidence": [unrelated], "assumptions": payload["assumptions"],
            "deduction": "This locator exists in the run output but was not the established scientific result."}]
        self.assert_error("inheritance_result_mismatch", lambda: self.mutate(api.plan_cycle, successor))

    def test_assessment_cannot_drop_its_declared_scope_assumptions(self):
        api = self.development()
        self.prepared_study()
        plan, execution = self.run_cycle()
        payload = self.assessment(plan, execution)
        payload["assumptions"] = []
        self.assert_error("scope_assumptions_missing", lambda: self.mutate(api.assess_cycle, payload))

    def test_a_separate_checker_must_take_the_exact_result_as_an_admitted_input(self):
        api = self.development()
        self.prepared_study()
        plan, execution = self.run_cycle()
        result_artifact = execution["outputs"][0]["artifact"]
        input_path = self.root / result_artifact["path"]
        program = ("import json\nwith open(%r) as source:\n    data = json.load(source)\n"
                   "passed = data['result']['values'] == [0, 1, 4, 9] and data['result']['bound'] == 9\n"
                   "print(json.dumps({'result': data['result'], 'validation': {'passed': passed}}))\n" % str(input_path)).encode()
        admission = self.admit(identifier="checker", program_data=program, inputs=[result_artifact])
        checked = self.execution(admission)
        self.mutate(api.record_execution, checked)
        payload = self.assessment(plan, execution)
        payload["execution_ids"].append(checked["id"])
        payload["validity_checks"][0]["evidence"] = [self.result_evidence(checked, True)]
        result = self.mutate(api.assess_cycle, payload)["result"]
        self.assertTrue(result["validated_result"])
        self.assertTrue(result["complete"])

    def test_policy_adoption_requires_fresh_synthesis_development_checkpoint_and_review(self):
        api = self.development()
        _, execution, payload = self.prepared_candidate()
        review = self.review(execution)
        receipt = self.mutate(api.record_readiness_review, review)
        principles = self.api("principles")
        original_policy = principles.CONSTITUTION_PATH.read_bytes()
        policy = self.root / "revised-constitution.md"
        policy.write_bytes(original_policy.replace(b"Version: 1", b"Version: 2") + b"\nA test-only policy revision.\n")
        with patch.object(principles, "CONSTITUTION_PATH", policy):
            self.assertFalse(api.readiness_report(self.store)["ready"])
            old = self.store.snapshot()["records"]["configuration"]["research"]["constitution"]["sha256"]
            adopted = self.mutate(principles.revalidate_constitution, {"previous_sha256": old, "reason": "Review the revised policy explicitly."})
            self.assertFalse(adopted["result"]["decisions_revalidated"])
            self.assertFalse(api.readiness_report(self.store)["ready"])
            self.refresh_synthesis("current-policy")
            self.assertIn("development_dependencies_stale", self.readiness_codes())
            payload["id"] = "current-policy-assessment"
            self.mutate(api.assess_cycle, payload)
            self.assertFalse(api.readiness_report(self.store)["ready"])
            self.save_checkpoint(assessment_id=payload["id"], identifier="current-policy-checkpoint")
            self.assertIn("readiness_review_stale", self.readiness_codes())
            self.mutate(api.record_readiness_review, self.review(execution, "current-policy-review"))
            self.assertTrue(api.readiness_report(self.store)["ready"])
            self.assertEqual(api.record_readiness_review(self.store, review, expected_revision=0, request_id=receipt["request_id"]), receipt)

    def test_a_new_current_synthesis_does_not_reauthorize_an_old_unexecuted_plan(self):
        api = self.development()
        self.prepared_study()
        self.mutate(api.plan_cycle, self.plan())
        self.refresh_synthesis("new-current-comparison")
        self.assertTrue(self.api().synthesis_report(self.store, "research")["ready"])
        self.assert_error("plan_dependencies_stale", lambda: self.admit())
        self.assertNotIn("execution_admission", self.store.snapshot()["records"])

    def test_a_failed_independent_review_is_preserved_and_cannot_grant_readiness(self):
        api = self.development()
        _, execution, _ = self.prepared_candidate()
        negative = self.review(execution, verdict="not_ready")
        self.mutate(api.record_readiness_review, negative)
        self.assertIn("independent_review_pending", self.readiness_codes())
        optimistic = self.review(execution, "optimistic-review")
        optimistic["checks"][0]["status"] = "failed"
        self.mutate(api.record_readiness_review, optimistic)
        self.assertFalse(api.readiness_report(self.store)["ready"])
        self.mutate(api.record_readiness_review, self.review(execution, "resolved-review"))
        self.assertTrue(api.readiness_report(self.store)["ready"])
        self.assertEqual(self.store.snapshot()["records"]["readiness_review"][negative["id"]]["payload"], negative)

    def test_every_declared_evidence_requirement_needs_actual_output(self):
        api = self.development()
        self.prepared_study()
        plan = self.plan()
        plan["evidence_requirements"].append({"id": "sensitivity", "kind": "validation", "description": "An additional planned sensitivity check."})
        plan, execution = self.run_cycle(plan)
        assessed = self.mutate(api.assess_cycle, self.assessment(plan, execution))["result"]
        self.assertFalse(assessed["complete"])
        self.assertIn("required_evidence_missing", {o["code"] for o in assessed["obligations"]})

    def test_an_unrelated_successful_validation_run_does_not_validate_this_result(self):
        api = self.development()
        self.prepared_study()
        _, first_execution = self.run_cycle()
        plan = self.plan("different-cycle")
        plan["distinguishing_test"] = "A distinct finite producer requiring its own verification."
        plan, execution = self.run_cycle(plan, run_id="different-run")
        payload = self.assessment(plan, execution, "different-assessment")
        payload["validity_checks"][0]["evidence"] = [self.result_evidence(first_execution, True)]
        assessed = self.mutate(api.assess_cycle, payload)["result"]
        self.assertFalse(assessed["validated_result"])
        self.assertIn("validity_evidence_unbound", {o["code"] for o in assessed["obligations"]})

    def test_result_locators_are_validated_against_actual_bytes(self):
        api = self.development()
        self.prepared_study()
        plan, execution = self.run_cycle()
        payload = self.assessment(plan, execution)
        payload["result"]["evidence"][0]["locator"]["value"]["bound"] = 100
        self.assert_error("invalid_locator", lambda: self.mutate(api.assess_cycle, payload))
        self.assertNotIn("cycle_assessment", self.store.snapshot()["records"])

    def test_malformed_resource_and_reference_values_fail_without_history_changes(self):
        api = self.development()
        self.prepared_study()
        for limit in (True, -1, 10 ** 400):
            payload = self.plan()
            payload["resource_limits"]["max_units"] = limit
            before = self.store.snapshot()
            with self.subTest(limit_type=type(limit).__name__):
                self.assert_error("invalid_development", lambda: self.mutate(api.plan_cycle, payload))
                self.assertEqual(self.store.snapshot(), before)
        self.mutate(api.plan_cycle, self.plan())
        self.assert_error("invalid_development", lambda: self.mutate(api.record_execution,
            {"cycle_id": "cycle-1", "origin": {"kind": "managed", "admission_id": []}}))

    def test_review_inputs_include_current_reading_records_and_source_provenance(self):
        api = self.development()
        self.prepared_candidate()
        inputs = api.readiness_report(self.store)["review_inputs"]
        self.assertTrue("sources" in inputs, "Independent inputs omit original source and reading records")
        work = self.links[0]["version_id"]
        self.assertEqual(inputs["sources"]["work"][work]["id"], work)
        reading_ids = {r["id"] for r in inputs["sources"]["reading"].values() if r["version_id"] == work}
        self.assertTrue(reading_ids)
        self.assertIn(self.links[0]["source_id"], inputs["sources"]["source"])

    def test_a_verified_negative_result_survives_a_failed_hypothesis(self):
        api = self.development()
        self.prepared_study()
        plan = self.plan()
        plan["hypothesis"] = "Every tested square is strictly less than 9."
        plan, execution = self.run_cycle(plan)
        payload = self.assessment(plan, execution)
        payload["result"]["statement"] = "The input n = 3 refutes the strict inequality while retaining the observed finite values."
        payload["failures"][0]["status"] = "observed"
        payload["failures"][0]["explanation"] = "The retained value 9 is a counterexample to the strict hypothesis."
        payload.update(disposition="failed", objective_status="open", remaining_obligations=["Assess the non-strict bound separately."])
        result = self.mutate(api.assess_cycle, payload)["result"]
        self.assertTrue(result["validated_result"])
        self.assertFalse(result["complete"])
        saved = self.save_checkpoint(select=False)
        self.assertEqual(saved["failures"][0]["status"], "observed")
        self.assertEqual(self.store.snapshot()["records"]["cycle"][plan["id"]]["status"], "failed")

    def test_a_failed_validity_signal_cannot_be_reclassified_as_a_valid_result(self):
        api = self.development()
        self.prepared_study()
        plan = self.plan()
        plan, execution = self.run_cycle(plan)
        payload = self.assessment(plan, execution)
        payload["failures"][0]["status"] = "observed"
        payload["validity_checks"][0]["status"] = "failed"
        result = self.mutate(api.assess_cycle, payload)["result"]
        self.assertFalse(result["validated_result"])
        self.assertFalse(result["complete"])

    def test_failure_assessment_binds_the_planned_target_estimand(self):
        api = self.development()
        self.prepared_study()
        plan, execution = self.run_cycle()
        payload = self.assessment(plan, execution)
        payload["failures"][0]["target_claim"] = "A different component-specific or transferred claim."
        self.assert_error("failure_scope_mismatch", lambda: self.mutate(api.assess_cycle, payload))
        self.assertNotIn("cycle_assessment", self.store.snapshot()["records"])

    def test_reopening_a_failed_strategy_requires_new_evidence_addressing_its_recorded_signal(self):
        api = self.development()
        self.prepared_study()
        plan, execution = self.run_cycle()
        payload = self.assessment(plan, execution)
        payload["failures"][0]["status"] = "observed"
        payload.update(disposition="failed", objective_status="open", remaining_obligations=["Investigate the recorded failure."])
        self.mutate(api.assess_cycle, payload)
        saved = self.save_checkpoint(select=False)
        successor = copy.deepcopy(plan)
        successor.update(id="reopened", predecessor=saved["id"], question="Does a newly checked control resolve the recorded failure?")
        successor["inheritance"] = [{"checkpoint_id": saved["id"], "assessment_id": payload["id"], "use": "failure",
            "evidence": [self.result_evidence(execution)], "assumptions": payload["assumptions"],
            "deduction": "The failed attempt limits this method until a changed control addresses the observed condition."}]
        self.assert_error("branch_reopening_required", lambda: self.mutate(api.plan_cycle, successor))
        successor["reopening"] = [{"assessment_id": payload["id"], "signal_id": "counterexample", "reason": "Reconsider the failed control.",
                                   "evidence": payload["failures"][0]["evidence"]}]
        self.assert_error("reopening_evidence_unchanged", lambda: self.mutate(api.plan_cycle, successor))
        # Merely using an old but previously uncited locator does not establish
        # changed evidence. The checkpoint already had these exact result bytes.
        successor["reopening"][0]["evidence"] = [self.result_evidence(execution)]
        self.assert_error("reopening_evidence_unchanged", lambda: self.mutate(api.plan_cycle, successor))
        changed_source = self.read_source(7)
        successor["reopening"][0]["evidence"] = [{"kind": "source", "link": changed_source}]
        self.mutate(api.plan_cycle, successor)
        self.assertEqual(self.store.snapshot()["records"]["cycle_plan"]["reopened"]["payload"]["reopening"], successor["reopening"])

    def test_admission_replay_is_the_same_reservation_and_unknown_outcomes_block_another_run(self):
        api = self.development()
        self.prepared_study()
        self.mutate(api.plan_cycle, self.plan())
        admission = self.admit(units=2)
        before = self.store.snapshot()
        original = {k: admission[k] for k in ("id", "cycle_id", "plan_digest", "command", "reserved_units")}
        replay = api.admit_execution(self.store, original, expected_revision=0, request_id=admission["request_id"])
        self.assertEqual(replay["result"], admission)
        self.assertEqual(self.store.snapshot(), before)
        self.assert_error("execution_pending", lambda: self.admit(identifier="replacement"))
        self.assertEqual(list(before["records"]["strategy_account"].values())[0]["charged_units"], 2)
        self.mutate(api.record_execution, self.execution(admission, status="interrupted", exit_code=None, used_units=None))
        self.admit(identifier="recovered-next", units=1)
        account = list(self.store.snapshot()["records"]["strategy_account"].values())[0]
        self.assertEqual((account["executions"], account["charged_units"]), (2, 3))

    def test_observed_resource_overrun_preserves_results_and_an_incomplete_checkpoint(self):
        api = self.development()
        self.prepared_study()
        plan = self.plan(max_executions=3, max_units=2)
        self.mutate(api.plan_cycle, plan)
        admission = self.admit(units=1)
        execution = self.execution(admission, used_units=3)
        self.mutate(api.record_execution, execution)
        assessed = self.mutate(api.assess_cycle, self.assessment(plan, execution))["result"]
        self.assertTrue(assessed["validated_result"])
        self.assertFalse(assessed["complete"])
        self.assertIn("resource_limit_exceeded", {o["code"] for o in assessed["obligations"]})
        records = self.store.snapshot()["records"]
        self.assertEqual(list(records["strategy_account"].values())[0]["charged_units"], 3)
        self.assertEqual(records["cycle"][plan["id"]]["status"], "budget_paused")
        self.assertTrue(any(c["status"] == "incomplete" and c["remaining_obligations"] for c in records["checkpoint"].values()))

    def test_interrupted_run_can_be_recorded_after_its_literature_source_is_corrupt(self):
        api = self.development()
        self.prepared_study()
        self.mutate(api.plan_cycle, self.plan(max_executions=1, max_units=1))
        admission = self.admit()
        execution = self.execution(admission, status="interrupted", exit_code=None, used_units=None)
        source = self.root / self.links[0]["artifact"]["path"]
        source.chmod(0o600)
        source.write_bytes(b"source corruption after launch")
        try:
            self.mutate(api.record_execution, execution)
        except ResearchError as error:
            self.fail("An interrupted admitted outcome must be retained despite changed literature: " + error.code)
        records = self.store.snapshot()["records"]
        self.assertEqual(records["execution_outcome"][admission["id"]]["execution_id"], execution["id"])
        self.assertTrue(any(c["status"] == "incomplete" for c in records["checkpoint"].values()))
        self.assertFalse(api.readiness_report(self.store)["ready"])

    def test_review_inputs_contain_actual_branch_plans_runs_and_assessments(self):
        api = self.development()
        plan, execution, payload = self.prepared_candidate()
        report = api.readiness_report(self.store)
        self.assertTrue("branches" in report["review_inputs"], "Independent inputs omit actual branch evidence")
        branch = report["review_inputs"]["branches"][plan["id"]]
        self.assertEqual(branch["plan"]["payload"], plan)
        self.assertEqual(branch["executions"][execution["id"]]["payload"], execution)
        self.assertEqual(branch["assessment"]["payload"], payload)
        self.assertEqual(len(branch["admissions"]), 1)
        self.assertIn("synthesis", report["review_inputs"])

    def test_an_unverified_branch_cannot_be_declared_resolved_by_the_candidate(self):
        api = self.development()
        plan, execution, payload = self.prepared_candidate()
        other_plan, other_execution = self.run_cycle(self.plan("other-cycle", partial=True), run_id="other-run")
        other = self.assessment(other_plan, other_execution, "other-assessment", partial=True)
        other["validity_checks"] = []
        self.mutate(api.assess_cycle, other)
        payload["id"] = "updated-candidate"
        payload["development"]["branches"].append({"cycle_id": other_plan["id"], "disposition": "resolved",
            "reason": "An unsupported assertion that this partial branch was resolved.", "evidence": [self.result_evidence(other_execution)]})
        self.mutate(api.assess_cycle, payload)
        self.save_checkpoint(assessment_id=payload["id"], identifier="updated-checkpoint")
        self.assertIn("branch_resolution_unverified", self.readiness_codes())

    def test_cycle_count_cannot_make_unassessed_processes_ready(self):
        api = self.development()
        self.prepared_study()
        for index in range(2):
            plan = self.plan("cycle-" + str(index))
            plan["distinguishing_test"] += " Independent fixture comparison " + str(index) + "."
            self.run_cycle(plan, run_id="repeat-" + str(index))
        report = api.readiness_report(self.store)
        self.assertEqual(report["counts"]["cycles"], 2)
        self.assertFalse(report["ready"])
        self.assertIn("development_assessment_missing", {o["code"] for o in report["obligations"]})

    def test_changed_result_scope_needs_a_matching_current_novelty_decision(self):
        api = self.development()
        self.prepared_study()
        plan, execution = self.run_cycle()
        payload = self.assessment(plan, execution, partial=True)
        payload["development"]["novelty"]["scope"] = self.result_scope()
        assessed = self.mutate(api.assess_cycle, payload)["result"]
        self.assertFalse(assessed["complete"])
        self.assertIn("novelty_comparison_stale", {o["code"] for o in assessed["obligations"]})

    def test_execution_requires_an_existing_prospective_plan(self):
        api = self.development()
        self.prepared_study()
        before = self.store.snapshot()
        self.assert_error("unknown_cycle", lambda: self.mutate(api.record_execution, {"cycle_id": "absent"}))
        self.assertEqual(self.store.snapshot(), before)

    def test_managed_execution_requires_a_matching_prior_admission(self):
        api = self.development()
        self.prepared_study()
        self.mutate(api.plan_cycle, self.plan())
        self.assert_error("execution_not_admitted", lambda: self.mutate(api.record_execution,
            {"id": "invented", "cycle_id": "cycle-1", "origin": {"kind": "managed", "admission_id": "missing"}}))
        admission = self.admit()
        payload = self.execution(admission)
        payload["command"]["argv"] = ["another-program"]
        self.assert_error("execution_identity_mismatch", lambda: self.mutate(api.record_execution, payload))
        self.assertNotIn("execution", self.store.snapshot()["records"])

    def test_process_exit_zero_never_substitutes_for_validity_checks(self):
        api = self.development()
        self.prepared_study()
        plan, execution = self.run_cycle()
        self.assertIn("development_assessment_missing", self.readiness_codes())
        payload = self.assessment(plan, execution)
        payload["validity_checks"] = []
        result = self.mutate(api.assess_cycle, payload)["result"]
        self.assertFalse(result["validated_result"])
        self.save_checkpoint()
        self.assertIn("validity_check_missing", self.readiness_codes())
        self.assertFalse(api.readiness_report(self.store)["ready"])

    def test_partial_scope_cannot_close_the_immutable_complete_objective(self):
        api = self.development()
        self.prepared_study()
        plan, execution = self.run_cycle(self.plan(partial=True))
        payload = self.assessment(plan, execution, partial=True)
        payload.update(objective_status="achieved", disposition="complete", remaining_obligations=[])
        self.mutate(api.assess_cycle, payload)
        self.save_checkpoint()
        report = api.readiness_report(self.store)
        self.assertFalse(report["ready"])
        self.assertIn("objective_scope_incomplete", {o["code"] for o in report["obligations"]})
        records = self.store.snapshot()["records"]
        self.assertEqual(records["research_objective"][self.objective["id"]], self.objective)
        self.assertTrue(records["cycle"][plan["id"]]["remaining_obligations"])
        self.assertEqual(records["checkpoint"]["checkpoint-1"]["status"], "incomplete")

    def test_a_full_valid_result_still_requires_substantive_development(self):
        api = self.development()
        self.prepared_study()
        plan, execution = self.run_cycle()
        result = self.mutate(api.assess_cycle, self.assessment(plan, execution, development=False))["result"]
        self.assertTrue(result["validated_result"])
        saved = self.save_checkpoint()
        self.assertTrue("obligations" in saved, "The checkpoint omits its unresolved development obligations")
        self.assertIn("development_assessment_missing", {o["code"] for o in saved["obligations"]})
        self.assertIn("development_assessment_missing", self.readiness_codes())
        self.assertFalse(api.readiness_report(self.store)["ready"])

    def test_first_good_result_is_ready_only_after_exact_independent_review(self):
        api = self.development()
        plan, execution, _ = self.prepared_candidate()
        before = api.readiness_report(self.store)
        self.assertFalse(before["ready"])
        self.assertEqual({o["code"] for o in before["obligations"]}, {"independent_review_missing"})
        self.assertEqual(before["candidate"]["objective"], self.objective)
        self.assertEqual(before["candidate"]["scope"], plan["scope"])
        self.mutate(api.record_readiness_review, self.review(execution))
        report = api.readiness_report(self.store)
        self.assertTrue(report["ready"])
        self.assertEqual(report["counts"]["cycles"], 1)
        self.assertTrue(report["mechanical_only"])
        self.assertFalse(report["native_proof_acceptance"])
        self.assertEqual(report["revision"], self.store.revision)

    def test_review_rejects_self_assessment_and_unrelated_result_references(self):
        api = self.development()
        _, execution, _ = self.prepared_candidate()
        payload = self.review(execution)
        payload["assessor"]["id"] = "  CYCLE-AUTHOR  "
        self.assert_error("review_not_independent", lambda: self.mutate(api.record_readiness_review, payload))
        payload = self.review(execution)
        payload["checks"][0]["evidence"][1]["execution_id"] = "foreign-run"
        self.assert_error("unknown_execution", lambda: self.mutate(api.record_readiness_review, payload))
        self.assertNotIn("readiness_review", self.store.snapshot()["records"])

    def test_result_artifact_corruption_invalidates_current_readiness_without_rewriting_history(self):
        api = self.development()
        _, execution, _ = self.prepared_candidate()
        payload = self.review(execution)
        receipt = self.mutate(api.record_readiness_review, payload)
        self.assertTrue(api.readiness_report(self.store)["ready"])
        before = self.store.snapshot()
        path = self.root / execution["outputs"][0]["artifact"]["path"]
        path.chmod(0o600)
        path.write_bytes(b"changed result bytes")
        report = api.readiness_report(self.store)
        self.assertFalse(report["ready"])
        self.assertIn("artifact_corrupt", {o["code"] for o in report["obligations"]})
        self.assertEqual(self.store.snapshot(), before)
        self.assertEqual(api.record_readiness_review(self.store, payload,
            expected_revision=0, request_id=receipt["request_id"]), receipt)

    def test_changed_required_source_units_invalidate_development_and_review(self):
        api = self.development()
        _, execution, _ = self.prepared_candidate()
        self.mutate(api.record_readiness_review, self.review(execution))
        records = self.store.snapshot()["records"]
        bundle = records["source_bundle"][records["bundle_selection"][self.links[0]["version_id"]]["bundle_id"]]
        payload = {k: copy.deepcopy(bundle[k]) for k in ("version_id", "source_id", "scope", "completeness", "units", "inventory", "bibliography", "resolutions")}
        payload["id"] = "expanded-required-source"
        payload["units"].append({"id": "supplement", "kind": "supplement", "required": True, "link": None,
                                 "reason": "A required appendix was identified.", "url": "https://example.org/appendix"})
        self.mutate(import_bundle, payload)
        self.assertFalse(api.readiness_report(self.store)["ready"])
        self.assertIn("reading_missing", self.readiness_codes())

    def test_reservations_survive_interruption_and_new_ids_cannot_reset_strategy_budget(self):
        api = self.development()
        self.prepared_study()
        plan = self.plan(max_executions=1, max_units=1)
        self.mutate(api.plan_cycle, plan)
        admission = self.admit(units=1)
        self.assert_error("strategy_budget_exhausted", lambda: self.admit(identifier="run-again"))
        execution = self.execution(admission, status="interrupted", exit_code=None, used_units=None)
        self.mutate(api.record_execution, execution)
        records = self.store.snapshot()["records"]
        self.assertEqual(records["cycle"][plan["id"]]["status"], "budget_paused")
        checkpoints = list(records["checkpoint"].values())
        self.assertTrue(any(c["status"] == "incomplete" and c["cycle_id"] == plan["id"] for c in checkpoints))
        renamed = copy.deepcopy(plan)
        renamed["id"] = "new-label"
        renamed["resource_limits"] = {"max_executions": 100, "max_units": 100, "unit": "fixture_step"}
        self.assert_error("strategy_budget_exhausted", lambda: self.mutate(api.plan_cycle, renamed))
        self.assertEqual(len(self.store.snapshot()["records"]["execution_admission"]), 1)

    def test_negative_findings_checkpoint_and_explicit_inheritance_preserve_lineage(self):
        api = self.development()
        self.prepared_study()
        plan, execution = self.run_cycle(self.plan(partial=True))
        payload = self.assessment(plan, execution, partial=True)
        payload["findings"] = [{"statement": "The source does not establish the unbounded extension.",
            "scope": "Integers outside the finite range.", "source_support": "source_not_supported", "scientific_status": "unresolved",
            "evidence": [self.source_evidence()], "uncertainties": ["No impossibility result is claimed."]}]
        self.mutate(api.assess_cycle, payload)
        checkpoint = self.save_checkpoint(select=False)
        successor = self.plan("cycle-2")
        successor["predecessor"] = checkpoint["id"]
        successor["inheritance"] = [{"checkpoint_id": checkpoint["id"], "assessment_id": payload["id"], "use": "validated_result",
            "evidence": [self.result_evidence(execution)], "assumptions": plan["scope"]["assumptions"],
            "deduction": "The n = 0 result covers one admissible input; n = 1, 2, 3 and equality remain to be checked."}]
        successor["question"] = "Can the remaining finite cases establish the complete objective?"
        self.mutate(api.plan_cycle, successor)
        records = self.store.snapshot()["records"]
        self.assertEqual(records["cycle_assessment"][payload["id"]]["payload"]["findings"], payload["findings"])
        self.assertEqual(records["cycle_plan"]["cycle-2"]["payload"]["predecessor"], checkpoint["id"])
        self.assertEqual(records["checkpoint"][checkpoint["id"]]["objective"], self.objective)
        self.assertTrue(records["checkpoint"][checkpoint["id"]]["remaining_obligations"])
        self.assertFalse(api.readiness_report(self.store)["ready"])

    def test_imported_results_keep_their_origin_and_cannot_claim_a_prospective_execution(self):
        api = self.development()
        self.prepared_study()
        plan = self.plan()
        self.mutate(api.plan_cycle, plan)
        provenance = self.artifacts.put(b'{"run":"old-run","executed_at":"2020-01-01T00:00:00Z"}', "application/json")
        artifact = self.artifacts.put(b'{"result":{"values":[0,1,4,9],"bound":9},"validation":{"passed":true}}', "application/json")
        imported = {"id": "imported-1", "cycle_id": plan["id"],
                    "origin": {"kind": "imported", "original": {"run_id": "old-run", "executed_at": "2020-01-01T00:00:00Z",
                        "provenance": provenance}, "reason": "The prior finite run motivates this current hypothesis.",
                        "deduction": "The prior values are evidence to reassess, not a newly executed prospective test."},
                    "command": None, "status": "completed", "exit_code": 0, "usage": {"units": None, "reason": "Original usage was not reported."},
                    "outputs": [{"id": "result", "requirement_id": "measurements", "artifact": artifact},
                                {"id": "validation", "requirement_id": "checks", "artifact": artifact}],
                    "notes": "The import retains the original execution provenance."}
        self.mutate(api.record_execution, imported)
        assessed = self.mutate(api.assess_cycle, self.assessment(plan, imported))["result"]
        self.assertTrue(assessed["validated_result"])
        self.save_checkpoint()
        self.assertIn("prospective_execution_missing", self.readiness_codes())
        self.assertFalse(api.readiness_report(self.store)["ready"])
        self.assertEqual(self.store.snapshot()["records"]["execution"]["imported-1"]["payload"]["origin"], imported["origin"])
        unknown_date = copy.deepcopy(imported)
        unknown_date["id"] = "imported-unknown-date"
        unknown_date["origin"]["original"] = {"run_id": "undated-run", "executed_at": None,
            "provenance": self.artifacts.put(b'{"run":"undated-run","executed_at":null}', "application/json")}
        self.mutate(api.record_execution, unknown_date)
        saved = self.store.snapshot()["records"]["execution"][unknown_date["id"]]
        self.assertIsNone(saved["payload"]["origin"]["original"]["executed_at"])
        self.assertIsNone(saved["admitted_revision"])

    def test_current_readiness_ignores_unrelated_revision_but_replays_original_mutations(self):
        api = self.development()
        _, execution, _ = self.prepared_candidate()
        payload = self.review(execution)
        receipt = self.mutate(api.record_readiness_review, payload)
        before = api.readiness_report(self.store)
        self.store.mutate("unrelated-note", {}, lambda tx: tx.put("note", "outside", {"text": "Unrelated bookkeeping."}),
                          expected_revision=self.store.revision, request_id="unrelated-note")
        after = api.readiness_report(self.store)
        self.assertTrue(after["ready"])
        self.assertEqual(after["candidate"]["digest"], before["candidate"]["digest"])
        self.assertEqual(api.record_readiness_review(self.store, payload, expected_revision=0, request_id=receipt["request_id"]), receipt)
        changed = copy.deepcopy(payload)
        changed["verdict"] = "unresolved"
        self.assert_error("request_id_conflict", lambda: api.record_readiness_review(self.store, changed,
            expected_revision=0, request_id=receipt["request_id"]))

    def test_plan_preparation_uses_one_snapshot_and_cas_rejects_a_concurrent_write(self):
        api = self.development()
        self.prepared_study()
        payload = self.plan()
        revision = self.store.revision
        actual = api.synthesis_state

        def assess_with_race(records, artifacts, profile):
            result = actual(records, artifacts, profile)
            self.store.mutate("racing-note", {}, lambda tx: tx.put("note", "racing", {"text": "Concurrent writer."}),
                              expected_revision=self.store.revision, request_id="racing-note")
            return result

        with patch.object(api, "synthesis_state", side_effect=assess_with_race):
            self.assert_error("stale_revision", lambda: api.plan_cycle(self.store, payload,
                expected_revision=revision, request_id="racing-plan"))
        self.assertNotIn("cycle_plan", self.store.snapshot()["records"])
        self.assertEqual(self.store.revision, revision + 1)

    def test_verification_does_not_acquire_author_cycles_or_a_private_objective(self):
        api = self.development()
        self.configured("verification")
        before = self.store.snapshot()
        report = api.readiness_report(self.store)
        self.assertFalse(report["ready"])
        self.assertIsNone(report["candidate"])
        self.assertIn("profile_inapplicable", {o["code"] for o in report["obligations"]})
        self.assert_error("profile_inapplicable", lambda: self.mutate(api.plan_cycle, {}))
        self.assertEqual(self.store.snapshot(), before)

    def test_snapshot_readiness_has_no_store_access_or_history_mutation(self):
        api = self.development()
        _, execution, _ = self.prepared_candidate()
        self.mutate(api.record_readiness_review, self.review(execution))
        snapshot = self.store.snapshot()
        records = copy.deepcopy(snapshot["records"])
        report = api.readiness_state(records, ArtifactStore(self.root))
        self.assertTrue(report["ready"])
        self.assertEqual(records, snapshot["records"])
        self.assertEqual(self.store.snapshot(), snapshot)
