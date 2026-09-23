"""Current reassessment may explicitly refresh an exact inherited checkpoint.

These are isolated harness fixtures. They do not operate on a research study.
"""
import copy
from unittest.mock import patch

from development_fixtures import DevelopmentCase
from research_harness.errors import ResearchError


class InheritanceRefreshTests(DevelopmentCase):
    def prepare_stale_descendant(self, *, inherited_use="validated_result"):
        api = self.development()
        self.prepared_study()
        parent, parent_run = self.run_cycle()
        original_parent_assessment = self.assessment(parent, parent_run)
        self.mutate(api.assess_cycle, original_parent_assessment)
        original_checkpoint = self.save_checkpoint(select=False)
        child = self.plan("child-cycle")
        child.update(predecessor=original_checkpoint["id"], question="Does a second independent enumeration retain the finite bound?")
        child["inheritance"] = [{"checkpoint_id": original_checkpoint["id"], "assessment_id": original_parent_assessment["id"],
            "use": inherited_use, "evidence": [self.result_evidence(parent_run)],
            "assumptions": parent["scope"]["assumptions"] + ["The inherited enumeration covers only the declared finite domain."],
            "deduction": "Retain the exact parent enumeration as conditional evidence for the independent second check."}]
        child, child_run = self.run_cycle(child, run_id="child-run")
        child_assessment = self.assessment(child, child_run, "child-old")
        self.mutate(api.assess_cycle, child_assessment)
        child_checkpoint = self.save_checkpoint(child["id"], child_assessment["id"], "child-old-checkpoint", select=False)
        self.refresh_synthesis("fresh-preparation")
        current_parent = self.assessment(parent, parent_run, "parent-current")
        current_parent["assumptions"] = current_parent["assumptions"] + ["The current parent qualification is retained explicitly."]
        self.mutate(api.assess_cycle, current_parent)
        current_checkpoint = self.save_checkpoint(parent["id"], current_parent["id"], "parent-current-checkpoint", select=False)
        records = self.store.snapshot()["records"]
        payload = self.assessment(child, child_run, "child-current")
        assumptions = list(dict.fromkeys(child["inheritance"][0]["assumptions"] + current_parent["assumptions"]))
        payload["assumptions"] = list(dict.fromkeys(payload["assumptions"] + assumptions))
        payload["inheritance_refresh"] = {"plan_digest": records["cycle_plan"][child["id"]]["digest"], "bindings": [{
            "previous_checkpoint_id": original_checkpoint["id"], "previous_assessment_id": original_parent_assessment["id"],
            "checkpoint_id": current_checkpoint["id"], "assessment_id": current_parent["id"],
            "evidence": copy.deepcopy(child["inheritance"][0]["evidence"]), "assumptions": assumptions,
            "deduction": "The same assessed result bytes remain valid under the current parent qualification; retain both old and new assumptions without changing the admitted child test."}]}
        payload["development"]["branches"].append({"cycle_id": parent["id"], "disposition": "resolved",
            "reason": "The same retained parent result was explicitly reassessed under current preparation.",
            "evidence": [self.result_evidence(parent_run)]})
        return {"parent": parent, "parent_run": parent_run, "current_parent": current_parent,
                "child": child, "child_run": child_run, "payload": payload, "child_checkpoint": child_checkpoint}

    def assess_refresh(self, payload):
        try:
            return self.mutate(self.development().assess_cycle, payload)
        except ResearchError as error:
            self.fail("An explicit exact current inheritance refresh must permit reassessment: " + error.code)

    def test_refresh_reassesses_the_same_execution_without_changing_plan_or_budget(self):
        data = self.prepare_stale_descendant()
        api, payload = self.development(), data["payload"]
        missing = copy.deepcopy(payload)
        missing.pop("inheritance_refresh")
        self.assert_error("inherited_result_stale", lambda: self.mutate(api.assess_cycle, missing))
        before = self.store.snapshot()
        receipt = self.assess_refresh(payload)
        self.assertTrue(receipt["result"]["validated_result"])
        after = self.store.snapshot()["records"]
        for kind in ("cycle_plan", "execution_admission", "execution", "execution_outcome", "checkpoint"):
            self.assertEqual(after[kind], before["records"][kind])
        self.assertEqual(after["strategy_account"], before["records"]["strategy_account"])
        self.assertEqual(after["cycle"]["child-cycle"]["execution_ids"], [data["child_run"]["id"]])
        self.assertEqual(after["cycle"]["child-cycle"]["assessment_id"], payload["id"])
        self.assertEqual(after["cycle_assessment"]["child-old"], before["records"]["cycle_assessment"]["child-old"])
        self.assertIn("inheritance_refresh", receipt["result"]["dependencies"])
        self.save_checkpoint("child-cycle", payload["id"], "child-current-checkpoint")
        report = api.readiness_report(self.store)
        self.assertIsNotNone(report["candidate"])
        self.assertIn("independent_review_missing", {item["code"] for item in report["obligations"]})
        self.assertEqual(report["review_inputs"]["assessment"]["payload"]["inheritance_refresh"], payload["inheritance_refresh"])
        self.mutate(api.record_readiness_review, self.review(data["child_run"]))
        self.assertTrue(api.readiness_report(self.store)["ready"])
        current = self.store.snapshot()
        replay = api.assess_cycle(self.store, payload, expected_revision=0, request_id=receipt["request_id"])
        self.assertEqual(replay, receipt)
        self.assertEqual(self.store.snapshot(), current)

    def test_refresh_rejects_changed_original_identity_or_plan(self):
        data = self.prepare_stale_descendant()
        api = self.development()
        for mutation in ("plan", "old_checkpoint", "old_assessment", "new_pair", "duplicate", "empty"):
            with self.subTest(mutation=mutation):
                payload = copy.deepcopy(data["payload"])
                refresh = payload["inheritance_refresh"]
                binding = refresh["bindings"][0]
                if mutation == "plan":
                    refresh["plan_digest"] = "0" * 64
                elif mutation == "old_checkpoint":
                    binding["previous_checkpoint_id"] = data["child_checkpoint"]["id"]
                elif mutation == "old_assessment":
                    binding["previous_assessment_id"] = "child-old"
                elif mutation == "new_pair":
                    binding["assessment_id"] = "assessment-1"
                elif mutation == "duplicate":
                    refresh["bindings"].append(copy.deepcopy(binding))
                else:
                    refresh["bindings"] = []
                before = self.store.snapshot()
                self.assert_error("inheritance_refresh_mismatch", lambda: self.mutate(api.assess_cycle, payload))
                self.assertEqual(self.store.snapshot(), before)

    def test_refresh_rejects_a_different_ancestor_cycle(self):
        data = self.prepare_stale_descendant()
        payload = data["payload"]
        binding = payload["inheritance_refresh"]["bindings"][0]
        binding.update(checkpoint_id=data["child_checkpoint"]["id"], assessment_id="child-old")
        self.assert_error("inheritance_refresh_mismatch", lambda: self.mutate(self.development().assess_cycle, payload))

    def test_refresh_rejects_changed_or_dropped_relied_on_locators(self):
        data = self.prepare_stale_descendant()
        for replace in (False, True):
            with self.subTest(replace=replace):
                payload = copy.deepcopy(data["payload"])
                payload["inheritance_refresh"]["bindings"][0]["evidence"] = [self.result_evidence(data["parent_run"], True)] if replace else []
                self.assert_error("inheritance_refresh_evidence_changed", lambda: self.mutate(self.development().assess_cycle, payload))

    def test_refresh_retains_old_and_new_assumptions_in_binding_and_assessment(self):
        data = self.prepare_stale_descendant()
        for target in ("old_binding", "new_binding", "assessment"):
            with self.subTest(target=target):
                payload = copy.deepcopy(data["payload"])
                if target == "assessment":
                    payload["assumptions"] = data["child"]["scope"]["assumptions"]
                else:
                    values = payload["inheritance_refresh"]["bindings"][0]["assumptions"]
                    values.remove("The inherited enumeration covers only the declared finite domain." if target == "old_binding"
                                  else "The current parent qualification is retained explicitly.")
                self.assert_error("inheritance_assumptions_missing", lambda: self.mutate(self.development().assess_cycle, payload))

    def test_refresh_preserves_an_assessed_unresolved_edge_without_upgrading_credit(self):
        data = self.prepare_stale_descendant(inherited_use="unresolved")
        report = self.assess_refresh(data["payload"])["result"]
        self.assertEqual(report["dependencies"]["inheritance"][0]["use"], "unresolved")
        self.assertEqual(self.store.snapshot()["records"]["cycle_plan"]["child-cycle"]["payload"]["inheritance"][0]["use"], "unresolved")

    def test_new_parent_reassessment_makes_an_explicit_binding_stale_again(self):
        data = self.prepare_stale_descendant()
        self.assess_refresh(data["payload"])
        self.save_checkpoint("child-cycle", data["payload"]["id"], "child-current-checkpoint")
        newer = copy.deepcopy(data["current_parent"])
        newer["id"] = "parent-newer"
        self.mutate(self.development().assess_cycle, newer)
        self.assertIn("inheritance_refresh_stale", self.readiness_codes())
        payload = copy.deepcopy(data["payload"])
        payload["id"] = "child-cannot-use-old-current-parent"
        self.assert_error("inheritance_refresh_stale", lambda: self.mutate(self.development().assess_cycle, payload))

    def test_current_binding_cannot_survive_a_later_preparation_change(self):
        data = self.prepare_stale_descendant()
        self.assess_refresh(data["payload"])
        self.save_checkpoint("child-cycle", data["payload"]["id"], "child-current-checkpoint")
        self.refresh_synthesis("changed-again")
        self.assertFalse(self.development().readiness_report(self.store)["ready"])
        payload = self.assessment(data["child"], data["child_run"], "child-after-source-change")
        payload["assumptions"] = data["payload"]["assumptions"]
        payload["inheritance_refresh"] = data["payload"]["inheritance_refresh"]
        self.assert_error("inherited_result_stale", lambda: self.mutate(self.development().assess_cycle, payload))

    def test_replacement_must_explicitly_reassess_the_original_result_locator(self):
        data = self.prepare_stale_descendant()
        api = self.development()
        current = copy.deepcopy(data["current_parent"])
        current["id"] = "parent-different-result-locator"
        result = self.result_evidence(data["parent_run"])
        result["locator"] = {"kind": "json", "pointer": "/result/values", "value": [0, 1, 4, 9]}
        current["result"]["evidence"] = [result]
        self.assertTrue(self.mutate(api.assess_cycle, current)["result"]["validated_result"])
        checkpoint = self.save_checkpoint("cycle-1", current["id"], "parent-other-locator", select=False)
        payload = data["payload"]
        payload["inheritance_refresh"]["bindings"][0].update(checkpoint_id=checkpoint["id"], assessment_id=current["id"])
        self.assert_error("inheritance_result_mismatch", lambda: self.mutate(api.assess_cycle, payload))

    def test_current_but_failed_parent_cannot_supply_refreshed_validated_credit(self):
        data = self.prepare_stale_descendant()
        api = self.development()
        current = copy.deepcopy(data["current_parent"])
        current["id"] = "parent-validity-unresolved"
        current["validity_checks"][0]["status"] = "unresolved"
        self.assertFalse(self.mutate(api.assess_cycle, current)["result"]["validated_result"])
        checkpoint = self.save_checkpoint("cycle-1", current["id"], "parent-unresolved", select=False)
        payload = data["payload"]
        payload["inheritance_refresh"]["bindings"][0].update(checkpoint_id=checkpoint["id"], assessment_id=current["id"])
        self.assert_error("inherited_result_stale", lambda: self.mutate(api.assess_cycle, payload))

    def test_refresh_requires_a_deduction_and_disallows_a_credit_override(self):
        data = self.prepare_stale_descendant()
        for mutation in ("empty_deduction", "extra_credit"):
            with self.subTest(mutation=mutation):
                payload = copy.deepcopy(data["payload"])
                binding = payload["inheritance_refresh"]["bindings"][0]
                if mutation == "empty_deduction":
                    binding["deduction"] = " "
                else:
                    binding["use"] = "validated_result"
                self.assert_error("invalid_development", lambda: self.mutate(self.development().assess_cycle, payload))

    def test_refresh_does_not_bypass_result_artifact_integrity(self):
        data = self.prepare_stale_descendant()
        reference = data["payload"]["inheritance_refresh"]["bindings"][0]["evidence"][0]
        artifact = self.root / reference["artifact"]["path"]
        original = artifact.read_bytes()
        mode = artifact.stat().st_mode
        artifact.chmod(0o600)
        before = self.store.snapshot()
        try:
            artifact.write_bytes(original + b" ")
            with self.assertRaises(ResearchError) as raised:
                self.mutate(self.development().assess_cycle, data["payload"])
            self.assertEqual(raised.exception.code, "artifact_corrupt")
            context = self.development()._Context(before["records"], self.artifacts)
            self.assert_error("artifact_corrupt", lambda: context.assessment("child-old"))
            self.assertEqual(self.store.snapshot(), before)
        finally:
            artifact.write_bytes(original)
            artifact.chmod(mode)

    def test_refresh_preparation_keeps_cas_and_request_conflict_protection(self):
        data = self.prepare_stale_descendant()
        api, payload = self.development(), data["payload"]
        revision = self.store.revision
        actual = api.synthesis_state

        def assess_with_race(records, artifacts, profile):
            result = actual(records, artifacts, profile)
            self.store.mutate("racing-refresh-note", {}, lambda tx: tx.put("note", "racing", {"text": "Concurrent writer."}),
                              expected_revision=self.store.revision, request_id="racing-refresh-note")
            return result

        with patch.object(api, "synthesis_state", side_effect=assess_with_race):
            self.assert_error("stale_revision", lambda: api.assess_cycle(self.store, payload,
                expected_revision=revision, request_id="racing-refresh"))
        self.assertEqual(self.store.revision, revision + 1)
        self.assertNotIn(payload["id"], self.store.snapshot()["records"]["cycle_assessment"])
        receipt = self.assess_refresh(payload)
        changed = copy.deepcopy(payload)
        changed["inheritance_refresh"]["bindings"][0]["deduction"] += " A different request body."
        before = self.store.snapshot()
        self.assert_error("request_id_conflict", lambda: api.assess_cycle(self.store, changed,
            expected_revision=0, request_id=receipt["request_id"]))
        self.assertEqual(self.store.snapshot(), before)

    def test_historical_recursive_staleness_returns_an_explicit_nonvalidated_report(self):
        data = self.prepare_stale_descendant()
        api = self.development()
        snapshot = self.store.snapshot()
        context = api._Context(snapshot["records"], self.artifacts)
        try:
            historical = context.assessment("child-old")
        except ResearchError as error:
            self.fail("Historical stale ancestry must be retained as a nonvalidated report: " + error.code)
        self.assertFalse(historical["validated_result"])
        self.assertFalse(historical["complete"])
        self.assertIn("inherited_result_stale", {item["code"] for item in historical["obligations"]})
        self.assertIn("development_dependencies_stale", {item["code"] for item in historical["obligations"]})
        original = snapshot["records"]["cycle_assessment"]["child-old"]
        self.assertEqual(historical["payload"], original["payload"])
        self.assertEqual(historical["evidence"], original["assessment"]["evidence"])
        self.assertEqual(self.store.snapshot(), snapshot)

    def test_historical_forged_lineage_is_not_converted_into_staleness(self):
        self.prepare_stale_descendant()
        records = copy.deepcopy(self.store.snapshot()["records"])
        records["cycle_plan"]["child-cycle"]["payload"]["inheritance"][0]["assessment_id"] = "parent-current"
        context = self.development()._Context(records, self.artifacts)
        self.assert_error("inheritance_mismatch", lambda: context.assessment("child-old"))

    def test_stale_first_edge_must_not_hide_corrupt_later_checkpoint(self):
        api = self.development()
        self.prepared_study()
        parents = []
        for index in (1, 2):
            plan = self.plan("parent-" + str(index))
            plan["strategy"]["mechanism"] += " Independently authored parent role " + str(index) + "."
            plan, execution = self.run_cycle(plan, run_id="parent-run-" + str(index))
            assessment = self.assessment(plan, execution, "parent-assessment-" + str(index))
            self.mutate(api.assess_cycle, assessment)
            checkpoint = self.save_checkpoint(plan["id"], assessment["id"], "parent-checkpoint-" + str(index), select=False)
            parents.append((plan, execution, assessment, checkpoint))
        child = self.plan("child")
        child.update(question="Does an independent child check preserve the same bounded result?", predecessor=parents[0][3]["id"])
        child["inheritance"] = [
            {"checkpoint_id": checkpoint["id"], "assessment_id": assessment["id"], "use": "validated_result",
             "evidence": [self.result_evidence(execution)], "assumptions": assessment["assumptions"],
             "deduction": "Use the preserved finite enumeration within its exact declared domain."}
            for plan, execution, assessment, checkpoint in parents
        ]
        child, execution = self.run_cycle(child, run_id="child-run")
        assessment = self.assessment(child, execution, "child-old")
        self.mutate(api.assess_cycle, assessment)
        self.refresh_synthesis("independent-stale")
        records = self.store.snapshot()["records"]
        reference = parents[1][3]["artifact"]
        artifact = self.root / reference["path"]
        original, mode = artifact.read_bytes(), artifact.stat().st_mode
        artifact.chmod(0o600)
        artifact.write_bytes(original + b" ")
        try:
            with self.assertRaises(ResearchError) as control:
                self.artifacts.read(reference)
            self.assertEqual(control.exception.code, "artifact_corrupt")
            with self.assertRaises(ResearchError) as error:
                api._Context(records, self.artifacts).assessment("child-old")
            self.assertEqual(error.exception.code, "artifact_corrupt")
        finally:
            artifact.write_bytes(original)
            artifact.chmod(mode)

    def test_stale_ancestor_must_not_hide_forged_development_evidence(self):
        self.prepare_stale_descendant()
        api = self.development()
        records = copy.deepcopy(self.store.snapshot()["records"])
        payload = records["cycle_assessment"]["child-old"]["payload"]
        forged = copy.deepcopy(payload["development"]["alternatives"][0]["evidence"][0])
        forged["locator"]["value"] = {"bound": 123456789}
        payload["development"]["alternatives"][0]["evidence"] = [forged]
        with self.assertRaises(ResearchError) as control:
            api._Evidence(api._Context(records, self.artifacts)).one(forged)
        with self.assertRaises(ResearchError) as error:
            api._Context(records, self.artifacts).assessment("child-old")
        self.assertEqual(error.exception.code, control.exception.code)

    def test_stale_refresh_must_not_hide_forged_development_evidence(self):
        data = self.prepare_stale_descendant()
        self.assess_refresh(data["payload"])
        newer = self.assessment(data["parent"], data["parent_run"], "parent-newer")
        self.mutate(self.development().assess_cycle, newer)
        records = copy.deepcopy(self.store.snapshot()["records"])
        payload = records["cycle_assessment"]["child-current"]["payload"]
        payload["development"]["alternatives"][0]["evidence"][0]["locator"]["value"] = {"bound": 123456789}
        with self.assertRaises(ResearchError):
            self.development()._Context(records, self.artifacts).assessment("child-current")

    def test_three_level_mixed_failure_chain_refreshes_without_erasing_failures(self):
        self.check_mixed_failure_chain()

    def test_refresh_retains_observed_failure_history_and_reopening_requirements(self):
        self.check_mixed_failure_chain(observed_failures=True)

    def check_mixed_failure_chain(self, *, observed_failures=False):
        api = self.development()
        self.prepared_study()
        chain = []
        for index in range(4):
            plan = self.plan("chain-" + str(index))
            plan["strategy"]["mechanism"] += " Independent fixture role " + str(index)
            plan["question"] = "Does finite fixture role " + str(index) + " retain its declared evidence?"
            if observed_failures and index in (1, 2):
                plan["hypothesis"] = "Every square in the finite domain is strictly less than 9."
                plan["failure_signals"][0]["statement"] = "An admissible input has square at least 9."
            if chain:
                parent = chain[-1]
                plan["predecessor"] = parent["checkpoint"]["id"]
                plan["inheritance"] = [{"checkpoint_id": parent["checkpoint"]["id"], "assessment_id": parent["assessment"]["id"],
                    "use": "validated_result" if index == 1 else "failure", "evidence": [self.result_evidence(parent["execution"])],
                    "assumptions": parent["assessment"]["assumptions"],
                    "deduction": "Preserve the exact declared parent evidence and its original credit classification."}]
            plan, execution = self.run_cycle(plan, run_id="chain-run-" + str(index))
            assessment = self.assessment(plan, execution, "chain-assessment-" + str(index))
            if index in (1, 2):
                assessment["validity_checks"][0].update(status="unresolved", explanation="This fixture intentionally leaves the methodological validation unestablished.")
                assessment.update(objective_status="open", disposition="failed", remaining_obligations=["Retain the unestablished methodological validation."])
                if observed_failures:
                    assessment["failures"][0].update(status="observed", evidence=[self.result_evidence(execution)],
                        explanation="The retained value 9 at n = 3 violates the planned strict bound.")
            self.mutate(api.assess_cycle, assessment)
            checkpoint = self.save_checkpoint(plan["id"], assessment["id"], "chain-checkpoint-" + str(index), select=False)
            chain.append({"plan": plan, "execution": execution, "assessment": assessment, "checkpoint": checkpoint})
        self.refresh_synthesis("mixed-chain-current")
        before = self.store.snapshot()["records"]
        refreshed = []
        for index, item in enumerate(chain):
            payload = copy.deepcopy(item["assessment"])
            payload["id"] += "-current"
            payload["development"]["novelty"]["literature_digest"] = self.api().synthesis_report(self.store, "research")["literature_digest"]
            if index:
                prior = item["plan"]["inheritance"][0]
                parent = refreshed[-1]
                payload["inheritance_refresh"] = {"plan_digest": before["cycle_plan"][item["plan"]["id"]]["digest"], "bindings": [{
                    "previous_checkpoint_id": prior["checkpoint_id"], "previous_assessment_id": prior["assessment_id"],
                    "checkpoint_id": parent["checkpoint"]["id"], "assessment_id": parent["assessment"]["id"],
                    "evidence": copy.deepcopy(prior["evidence"]), "assumptions": prior["assumptions"],
                    "deduction": "The same failure or validated evidence remains preserved under the explicitly current parent assessment; no failure is promoted."}]}
                missing = copy.deepcopy(payload)
                missing.pop("inheritance_refresh")
                self.assert_error("inherited_result_stale", lambda: self.mutate(api.assess_cycle, missing))
            if index == 3:
                payload["development"]["branches"].extend({"cycle_id": earlier["plan"]["id"], "disposition": "not_useful",
                    "reason": "Retain the bounded ancestor evidence and unresolved methodology without treating the branch as completed.",
                    "evidence": [self.result_evidence(earlier["execution"])]} for earlier in chain[:-1])
            report = self.assess_refresh(payload)["result"]
            self.assertEqual(report["validated_result"], index not in (1, 2))
            if index:
                self.assertEqual(report["dependencies"]["inheritance"][0]["use"], item["plan"]["inheritance"][0]["use"])
            checkpoint = self.save_checkpoint(item["plan"]["id"], payload["id"], "chain-current-checkpoint-" + str(index), select=index == 3)
            refreshed.append({"assessment": payload, "checkpoint": checkpoint})
        after = self.store.snapshot()["records"]
        for kind in ("cycle_plan", "execution", "execution_admission", "execution_outcome"):
            self.assertEqual(after[kind], before[kind])
        for item in chain:
            identifier = item["assessment"]["id"]
            self.assertEqual(after["cycle_assessment"][identifier], before["cycle_assessment"][identifier])
        for key, old_account in before["strategy_account"].items():
            current_account = after["strategy_account"][key]
            self.assertEqual({k: v for k, v in current_account.items() if k != "failures"},
                             {k: v for k, v in old_account.items() if k != "failures"})
            expected = old_account["failures"] + [dict(f, assessment_id=f["assessment_id"] + "-current")
                                                  for f in old_account["failures"]]
            self.assertEqual(current_account["failures"], expected)
        if observed_failures:
            self.assertEqual(sum(len(a["failures"]) for a in before["strategy_account"].values()), 2)
            self.assertEqual(sum(len(a["failures"]) for a in after["strategy_account"].values()), 4)
            successor = copy.deepcopy(chain[1]["plan"])
            successor.update(id="reopened-observed-failure", question="Can changed evidence resolve the strict-bound failure?",
                             predecessor=refreshed[1]["checkpoint"]["id"])
            successor["literature"]["literature_digest"] = self.api().synthesis_report(self.store, "research")["literature_digest"]
            successor["inheritance"] = [{"checkpoint_id": refreshed[1]["checkpoint"]["id"],
                "assessment_id": refreshed[1]["assessment"]["id"], "use": "failure",
                "evidence": [self.result_evidence(chain[1]["execution"])], "assumptions": chain[1]["assessment"]["assumptions"],
                "deduction": "Retain the observed strict-bound failure and its unresolved methodological qualification."}]
            snapshot = self.store.snapshot()
            self.assert_error("branch_reopening_required", lambda: self.mutate(api.plan_cycle, successor))
            self.assertEqual(self.store.snapshot(), snapshot)
        status = api.readiness_report(self.store)
        self.assertIsNotNone(status["candidate"])
        self.assertIn("independent_review_missing", {item["code"] for item in status["obligations"]})
        self.assertEqual(status["review_inputs"]["branches"]["chain-1"]["assessment"]["payload"]["validity_checks"][0]["status"], "unresolved")
        from research_harness.cli import status_report
        status = status_report(self.store)
        self.assertEqual(status["revision"], self.store.revision)
        self.assertNotIn("inherited_result_stale", {item["code"] for item in status["obligations"]})
