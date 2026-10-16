"""Managed claims retain their own evidence and historical digest contracts."""

import copy
import json
import unittest
from collections import Counter
from unittest.mock import patch

from development_fixtures import DevelopmentCase
from managed_strategy_fixtures import ManagedStrategy, bind_plan
from source_limited_fixtures import SourceLimitedCase
from research_harness import development, principles, research_decisions, strategy
from research_harness.errors import ResearchError
from research_harness.evidence import digest


class ClaimDependencyTests(DevelopmentCase):
    def build_managed_study(self):
        self.prepared_study()
        ManagedStrategy(self).ensure_intent()

    def build_managed_candidate(self):
        self.build_managed_study()
        plan, execution = self.run_cycle(bind_plan(self, self.plan()))
        self.execution_payload = execution
        self.mutate(development.assess_cycle, self.assessment(plan, execution))
        self.save_checkpoint()
        ManagedStrategy(self).decide(phase="result")
        return plan, execution

    def read_state(self):
        records = self.store.snapshot()["records"]
        return development.readiness_state(records, self.artifacts)

    def record_impact(self, material=False):
        records = self.store.snapshot()["records"]
        decision = strategy.selected(records, "decision", "research_decision")
        dossier = records["strategy_dossier"][decision["payload"]["dossier_id"]]
        sources = strategy.source_delta(records, dossier)
        self.mutate(research_decisions.record_source_impact, {
            "id": "impact-" + str(self.store.revision), "decision_id": decision["id"],
            "source_impact": {"sources": [{"source_id": identifier,
                "impact": "material" if material else "unrelated", "reason": "Assess the new fixture source explicitly.",
                "evidence": [self.source_evidence()]} for identifier in sources],
                "aspects": {key: "material" if material else "unchanged"
                            for key in ("nearest_work", "scope", "transfer", "bar")}}})

    def assert_current(self, before):
        report = self.read_state()
        self.assertTrue(report["ready"], report["obligations"])

        self.assertEqual(report["candidate"]["digest"], before["candidate"]["digest"])
        records = self.store.snapshot()["records"]
        assessment = development._Context(records, self.artifacts).assess_record("assessment-1")
        self.assertTrue(assessment["complete"], assessment["obligations"])
        self.assertEqual(assessment["digest"], before["candidate"]["assessment_digest"])
        decision = research_decisions.decision_state(records, self.artifacts, "write")
        self.assertTrue(decision["ready"], decision["obligations"])

    def test_unrelated_reading_keeps_assessment_checkpoint_candidate_and_support_current(self):
        self.build_managed_candidate()
        before = self.read_state()
        self.read_source(2)
        self.record_impact()
        self.assert_current(before)

    def test_new_search_judgment_keeps_the_supported_claim_current(self):
        self.build_managed_candidate()
        before = self.read_state()
        self.record_purpose("direct", "later-direct")
        self.record_impact()
        self.assert_current(before)

    def test_pending_plan_and_admission_survive_an_unrelated_search_judgment(self):
        from research_harness.gates import gate_report
        self.build_managed_study()
        self.mutate(development.plan_cycle, bind_plan(self, self.plan()))
        self.record_purpose("direct", "later-direct")
        self.record_impact()
        admission = self.admit()
        records = self.store.snapshot()["records"]
        self.assertEqual(development.validate_admitted_execution(records, self.artifacts, admission["id"]), admission)
        self.assertTrue(gate_report(self.store, "execution")["ready"])

    def test_changed_synthesis_keeps_the_supported_claim_current(self):
        self.build_managed_candidate()
        before = self.read_state()
        payload = self.standards(self.links[1], identifier="different-standards")
        self.mutate(self.api().record_standards, payload)
        self.assert_current(before)

    def test_constitution_revalidation_keeps_the_supported_claim_current(self):
        self.build_managed_candidate()
        before = self.read_state()
        changed = self.root / "constitution-v7.md"
        changed.write_text("# Research constitution\n\nVersion: 7\n\nA revised contract.\n", encoding="utf-8")
        with patch.object(principles, "CONSTITUTION_PATH", changed):
            self.mutate(principles.revalidate_constitution, {
                "previous_sha256": before["synthesis"]["configuration"]["constitution"]["sha256"],
                "reason": "Adopt the revised distributed contract."})
            self.assert_current(before)

    def bind_configuration_reference(self, reference):
        record_strategy = strategy.record_strategy

        def record_bound_strategy(store, payload, **identity):
            payload = copy.deepcopy(payload)
            payload["dependencies"].append({"kind": "configuration", "id": "research", "digest": reference["digest"]})
            payload["candidates"][0]["adequacy"]["evidence"].append(reference)
            return record_strategy(store, payload, **identity)

        return patch.object(strategy, "record_strategy", side_effect=record_bound_strategy)

    def adopt_constitution(self):
        changed = self.root / "constitution-v8.md"
        changed.write_text("# Research constitution\n\nVersion: 8\n\nAn adopted revision.\n", encoding="utf-8")
        previous = self.store.snapshot()["records"]["configuration"]["research"]["constitution"]["sha256"]
        with patch.object(principles, "CONSTITUTION_PATH", changed):
            self.mutate(principles.revalidate_constitution, {"previous_sha256": previous, "reason": "Adopt Version 8."})
        return patch.object(principles, "CONSTITUTION_PATH", changed)

    def test_explicit_configuration_dependencies_and_evidence_survive_adoption(self):
        self.build_managed_study()
        reference = {"kind": "record", "record_kind": "configuration", "id": "research",
                     "digest": digest(self.store.snapshot()["records"]["configuration"]["research"])}
        with self.bind_configuration_reference(reference):
            plan, execution = self.run_cycle(bind_plan(self, self.plan()))
            self.execution_payload = execution
            self.mutate(development.assess_cycle, self.assessment(plan, execution))
            self.save_checkpoint()
            ManagedStrategy(self).decide(phase="result")
        before = self.read_state()
        with self.adopt_constitution():
            records = self.store.snapshot()["records"]
            self.assertEqual(strategy.evidence(records, self.artifacts, [reference]), [reference])
            self.assert_current(before)
            report = research_decisions.decision_state(dict(records), self.artifacts, "write")
            self.assertIn("configuration/research", {item.get("record_id") for item in report["obligations"]
                                                   if item["code"] == "claim_reassessment_required"})

    def test_a_new_strategy_can_retain_a_recorded_configuration_reference_after_adoption(self):
        self.build_managed_study()
        reference = {"kind": "record", "record_kind": "configuration", "id": "research",
                     "digest": digest(self.store.snapshot()["records"]["configuration"]["research"])}
        with self.adopt_constitution(), self.bind_configuration_reference(reference):
            ManagedStrategy(self).decide()
            records = self.store.snapshot()["records"]
            report = research_decisions.decision_state(records, self.artifacts, "cycle")
            self.assertTrue(report["ready"], report["obligations"])

    def test_configuration_references_keep_science_and_require_historical_inputs(self):
        self.build_managed_study()
        original = self.store.snapshot()["records"]
        reference = {"kind": "record", "record_kind": "configuration", "id": "research",
                     "digest": digest(original["configuration"]["research"])}
        with self.adopt_constitution():
            records = self.store.snapshot()["records"]
            for key, value in (("target", dict(self.objective, statement="A changed objective.")),
                               ("profile", "verification")):
                changed = copy.deepcopy(records)
                changed["configuration"]["research"][key] = value
                self.assert_error("strategy_evidence_stale", lambda: strategy.evidence(changed, self.artifacts, [reference]))
            for unavailable in (dict(records), records.read_snapshot(0).copy()):
                unavailable.update(records)
                with self.assertRaises(ResearchError) as raised:
                    strategy.evidence(unavailable, self.artifacts, [reference])
                self.assertEqual(raised.exception.code, "claim_reassessment_required")
                self.assertEqual(raised.exception.details["record_id"], "configuration/research")
            unmanaged = copy.deepcopy(records)
            unmanaged.pop("research_intent", None)
            unmanaged.pop("workspace", None)
            self.assert_error("strategy_evidence_stale", lambda: strategy.evidence(unmanaged, self.artifacts, [reference]))
            for kind in ("constitution", "synthesis"):
                identifier, saved = next(iter(records[kind].items()))
                exact = {"kind": "record", "record_kind": kind, "id": identifier, "digest": digest(saved)}
                changed = copy.deepcopy(records)
                changed[kind][identifier]["unexpected"] = "changed immutable evidence"
                self.assert_error("strategy_evidence_stale", lambda: strategy.evidence(changed, self.artifacts, [exact]))

    def test_material_source_impact_still_requires_reassessment(self):
        self.build_managed_candidate()
        self.read_source(20)
        self.assert_error("research_source_reassessment_required", lambda: self.record_impact(material=True))
        records = self.store.snapshot()["records"]
        report = research_decisions.decision_state(records, self.artifacts, "write")
        self.assertFalse(report["ready"])
        self.assertIn("research_source_impact_required", {o["code"] for o in report["obligations"]})

    def test_objective_and_result_bytes_remain_claim_dependencies(self):
        self.build_managed_candidate()
        records = self.store.snapshot()["records"]
        changed = copy.deepcopy(records)
        target = dict(self.objective, id="wider", statement="Establish the bound through n = 4.")
        changed["configuration"]["research"]["target"] = target
        changed["research_objective"][target["id"]] = target
        report = development.readiness_state(changed, self.artifacts)
        self.assertFalse(report["ready"])
        self.assertIn("development_dependencies_stale", {o["code"] for o in report["obligations"]})
        artifact = self.root / self.execution_payload["outputs"][0]["artifact"]["path"]
        artifact.chmod(0o600)
        artifact.write_bytes(b"changed result")
        report = self.read_state()
        self.assertFalse(report["ready"])
        self.assertIn("artifact_corrupt", {o["code"] for o in report["obligations"]})

    def test_successor_inherits_across_unrelated_reading_without_refresh(self):
        plan, execution = self.build_managed_candidate()
        link = self.read_source(20)
        self.links[0] = link
        child = self.plan("child")
        child.update(question="Does a second enumeration confirm the retained finite result?", predecessor="checkpoint-1")
        child["inheritance"] = [{"checkpoint_id": "checkpoint-1", "assessment_id": "assessment-1",
            "use": "validated_result", "evidence": [self.result_evidence(execution)],
            "assumptions": plan["scope"]["assumptions"], "deduction": "Use the exact earlier finite result."}]
        self.mutate(development.plan_cycle, bind_plan(self, child))
        self.admit("child", "child-run")

    def test_a_branch_named_synthesis_cannot_hide_a_changed_assessed_claim(self):
        self.build_managed_study()
        first, run = self.run_cycle(bind_plan(self, self.plan("synthesis")))
        original = self.assessment(first, run, "first-assessment")
        self.mutate(development.assess_cycle, original)
        second = self.plan()
        second["strategy"]["mechanism"] += " Use a distinct independent enumeration."
        second, execution = self.run_cycle(bind_plan(self, second), run_id="second-run")
        assessed = self.assessment(second, execution)
        assessed["development"]["branches"].append({"cycle_id": "synthesis", "disposition": "resolved",
            "reason": "Retain the independent first enumeration.", "evidence": [self.result_evidence(run)]})
        self.mutate(development.assess_cycle, assessed)
        self.save_checkpoint()
        self.execution_payload = execution
        ManagedStrategy(self).decide(phase="result")
        before = self.read_state()["candidate"]["digest"]
        changed = copy.deepcopy(original)
        changed.update(id="first-reassessed")
        changed["result"]["statement"] = "The earlier interpretation is now explicitly qualified."
        self.mutate(development.assess_cycle, changed)
        after = self.read_state()
        self.assertNotEqual(after["candidate"]["digest"], before)
        self.assertFalse(after["ready"])

    def test_cycle_and_writing_need_configuration_and_decisions_without_synthesis_sections(self):
        from integration_fixtures import observe_run
        from research_harness.gates import gate_report
        self.build_managed_study()
        def clear_sections(tx):
            for kind in ("standards", "rationale", "context", "innovation"):
                tx.put("synthesis_selection", "research:" + kind, {"id": None})
        self.store.mutate("fixture.no-sections", {}, clear_sections,
                          expected_revision=self.store.revision, request_id="no-sections")
        plan = self.plan()
        execution = observe_run(self, plan=plan)
        self.execution_payload = execution
        report = self.mutate(development.assess_cycle, self.assessment(plan, execution))["result"]
        self.assertTrue(report["complete"], report["obligations"])
        self.save_checkpoint()
        ManagedStrategy(self).decide(phase="result")
        self.assertTrue(self.read_state()["ready"], self.read_state()["obligations"])
        report = gate_report(self.store, "readiness")
        self.assertTrue(report["ready"], report["obligations"])

    def test_legacy_assessment_checkpoint_candidate_and_review_keep_their_standing(self):
        self.build_managed_study()
        # This disables the new currency composition, retaining the 0.50.1 formulas.
        with patch.object(development, "uses_claim_dependencies", return_value=False):
            plan, execution = self.run_cycle(bind_plan(self, self.plan()))
            self.execution_payload = execution
            self.mutate(development.assess_cycle, self.assessment(plan, execution))
            self.save_checkpoint()
            ManagedStrategy(self).decide(phase="result")
            legacy = self.read_state()
        self.assertTrue(self.read_state()["ready"], self.read_state()["obligations"])
        state = research_decisions.decision_state(self.store.snapshot()["records"], self.artifacts, "write")
        self.assertTrue(state["ready"], state["obligations"])
        self.assertNotEqual(legacy["candidate"]["digest"], self.read_state()["candidate"]["digest"])
        equivalent = dict(state["decision"]["payload"], support_candidate_digest=self.read_state()["candidate"]["digest"])
        research_decisions._decision_structure(self.store.snapshot()["records"], self.artifacts, equivalent)
        # Original hashes remain bound to their recorded inputs after upgrade.
        self.read_source(2)
        self.record_impact()
        self.record_purpose("direct", "legacy-later-direct")
        self.record_impact()
        self.assertTrue(self.read_state()["ready"], self.read_state()["obligations"])
        records = self.store.snapshot()["records"]
        report = development._Context(records, self.artifacts).assess_record("assessment-1")
        self.assertTrue(report["validated_result"], report["obligations"])
        self.assertEqual(report["digest"], legacy["candidate"]["assessment_digest"])
        self.assertTrue(research_decisions.decision_state(records, self.artifacts, "write")["ready"])
        self.links[0] = self.read_source(20)
        child = self.plan("legacy-child")
        child.update(question="Does an independent enumeration extend the retained result?", predecessor="checkpoint-1")
        child["inheritance"] = [{"checkpoint_id": "checkpoint-1", "assessment_id": "assessment-1",
            "use": "validated_result", "evidence": [self.result_evidence(execution)],
            "assumptions": plan["scope"]["assumptions"], "deduction": "Use the original finite result."}]
        self.mutate(development.plan_cycle, bind_plan(self, child))
        self.admit("legacy-child", "legacy-child-run")

    def test_legacy_pending_plan_and_admission_survive_explicit_adoption(self):
        from research_harness.gates import gate_state
        self.build_managed_study()
        with patch.object(development, "uses_claim_dependencies", return_value=False):
            self.mutate(development.plan_cycle, bind_plan(self, self.plan()))
            admission = self.admit()
        changed = self.root / "constitution-v8.md"
        changed.write_text("# Research constitution\n\nVersion: 8\n\nAn adopted revision.\n", encoding="utf-8")
        previous = self.store.snapshot()["records"]["configuration"]["research"]["constitution"]["sha256"]
        with patch.object(principles, "CONSTITUTION_PATH", changed):
            self.mutate(principles.revalidate_constitution, {"previous_sha256": previous, "reason": "Adopt Version 8."})
            records = self.store.snapshot()["records"]
            self.assertEqual(development.validate_admitted_execution(records, self.artifacts, admission["id"]), admission)
            self.assertTrue(gate_state(records, self.artifacts, "execution")["ready"])
            self.assertTrue(research_decisions.decision_state(records, self.artifacts, "cycle")["ready"])

    def test_a_new_review_can_bind_an_authenticated_legacy_candidate_alias(self):
        self.build_managed_study()
        with patch.object(development, "uses_claim_dependencies", return_value=False):
            plan, execution = self.run_cycle(bind_plan(self, self.plan()))
            self.execution_payload = execution
            self.mutate(development.assess_cycle, self.assessment(plan, execution))
            self.save_checkpoint()
            payload = self.review(execution)
            self.mutate(development.record_readiness_review, payload)
        self.read_source(2)
        payload["id"] = "another-legacy-support"
        self.mutate(development.record_readiness_review, payload)
        report = self.read_state()
        self.assertTrue(report["ready"], report["obligations"])
        records = self.store.snapshot()["records"]
        records["readiness_review"][payload["id"]].pop("candidate")
        report = development.readiness_state(records, self.artifacts)
        missing = [item for item in report["obligations"] if item["code"] == "claim_reassessment_required"]
        self.assertTrue(missing, report["obligations"])
        self.assertTrue(all(item.get("record_id") for item in missing))

    def test_the_first_legacy_result_response_can_arrive_after_upgrade(self):
        self.build_managed_study()
        captured = []
        def capture_review(store, payload, **identity):
            captured.append(copy.deepcopy(payload))
            raise RuntimeError("Pause after the recorded assignment and response")
        with patch.object(development, "uses_claim_dependencies", return_value=False):
            plan, execution = self.run_cycle(bind_plan(self, self.plan()))
            self.execution_payload = execution
            self.mutate(development.assess_cycle, self.assessment(plan, execution))
            self.save_checkpoint()
            with patch.object(research_decisions, "record_value_review", side_effect=capture_review):
                with self.assertRaisesRegex(RuntimeError, "Pause after"):
                    ManagedStrategy(self).decide(phase="result")
        self.read_source(2)
        self.mutate(research_decisions.record_value_review, captured[0])
        report = self.read_state()
        self.assertTrue(report["ready"], report["obligations"])

    def test_a_conflicting_recorded_candidate_cannot_hide_behind_a_current_hash(self):
        self.build_managed_candidate()
        records = self.store.snapshot()["records"]
        identifier = records["development_selection"]["review"]["review_id"]
        records["readiness_review"][identifier]["candidate"]["constitution"]["sha256"] = "conflicting-recorded-audit-input"
        report = development.readiness_state(records, self.artifacts)
        self.assertIn("claim_reassessment_required", {o["code"] for o in report["obligations"]})

    def test_cached_candidate_proofs_still_require_the_callers_historical_binding(self):
        from research_harness.evaluation import Evaluation
        self.build_managed_study()
        captured = []
        def capture_review(store, payload, **identity):
            captured.append(copy.deepcopy(payload))
            raise RuntimeError("Pause before the first result response")
        with patch.object(development, "uses_claim_dependencies", return_value=False):
            plan, execution = self.run_cycle(bind_plan(self, self.plan()))
            self.execution_payload = execution
            self.mutate(development.assess_cycle, self.assessment(plan, execution))
            self.save_checkpoint()
            with patch.object(research_decisions, "record_value_review", side_effect=capture_review):
                with self.assertRaisesRegex(RuntimeError, "Pause before"):
                    ManagedStrategy(self).decide(phase="result")
        payload = captured[0]
        records = self.store.snapshot()["records"]
        assignment = dict(records["review_assignment"][payload["assignment_id"]], id="wrong-history", recorded_revision=1)
        self.store.mutate("fixture.assignment", {}, lambda tx: tx.put("review_assignment", assignment["id"], assignment),
                          expected_revision=self.store.revision, request_id="wrong-history")
        records = self.store.snapshot()["records"]
        root = Evaluation(records, self.artifacts)
        first = development._Context(records, root)
        candidate, _ = first.build_candidate()
        recorded = payload["support"]["candidate_digest"]
        self.assertTrue(first.verify_candidate_digest(recorded, candidate, assignment_id=payload["assignment_id"]))
        for assignment_id in (None, "wrong-history"):
            with self.subTest(assignment_id=assignment_id):
                with self.assertRaises(ResearchError) as raised:
                    development._Context(records, root).verify_candidate_digest(recorded, candidate, assignment_id=assignment_id)
                self.assertEqual(raised.exception.code, "claim_reassessment_required")

    def test_a_plan_payload_cannot_change_behind_its_recorded_digest(self):
        self.build_managed_candidate()
        records = self.store.snapshot()["records"]
        records["cycle_plan"]["cycle-1"]["payload"]["question"] = "A different scientific question."
        with self.assertRaises(ResearchError) as raised:
            development._Context(records, self.artifacts).assess_record("assessment-1")
        self.assertEqual(raised.exception.code, "claim_reassessment_required")
        self.assertEqual(raised.exception.details["record_id"], "cycle-1")

    def test_legacy_hash_only_candidate_recovers_all_original_component_identities(self):
        self.build_managed_study()
        with patch.object(development, "uses_claim_dependencies", return_value=False):
            plan, execution = self.run_cycle(bind_plan(self, self.plan()))
            self.execution_payload = execution
            self.mutate(development.assess_cycle, self.assessment(plan, execution))
            self.save_checkpoint()
            ManagedStrategy(self).decide(phase="result")
        self.read_source(2)
        self.record_impact()
        records = self.store.snapshot()["records"]
        for saved in records["readiness_review"].values():
            saved.pop("candidate")
        report = development.readiness_state(records, self.artifacts)
        self.assertTrue(report["ready"], report["obligations"])
        self.assertTrue(research_decisions.decision_state(records, self.artifacts, "write")["ready"])

    def test_legacy_claim_and_decision_survive_explicit_constitution_adoption(self):
        self.build_managed_study()
        with patch.object(development, "uses_claim_dependencies", return_value=False):
            plan, execution = self.run_cycle(bind_plan(self, self.plan()))
            self.execution_payload = execution
            self.mutate(development.assess_cycle, self.assessment(plan, execution))
            self.save_checkpoint()
            ManagedStrategy(self).decide(phase="result")
        before = self.read_state()
        changed = self.root / "constitution-v8.md"
        changed.write_text("# Research constitution\n\nVersion: 8\n\nAn explicitly adopted contract.\n", encoding="utf-8")
        with patch.object(principles, "CONSTITUTION_PATH", changed):
            self.assertEqual(principles.configuration_report(self.store, "research")["constitution"]["version"], "6")
            self.mutate(principles.revalidate_constitution, {
                "previous_sha256": before["candidate"]["constitution"]["sha256"], "reason": "Adopt the new version explicitly."})
            self.assert_current(before)
            self.read_source(2)
            self.record_impact()
            self.assert_current(before)

    def test_legacy_result_bytes_still_invalidate_the_claim_after_upgrade(self):
        self.build_managed_study()
        with patch.object(development, "uses_claim_dependencies", return_value=False):
            plan, execution = self.run_cycle(bind_plan(self, self.plan()))
            self.execution_payload = execution
            self.mutate(development.assess_cycle, self.assessment(plan, execution))
            self.save_checkpoint()
            ManagedStrategy(self).decide(phase="result")
        self.assertTrue(self.read_state()["ready"])
        artifact = self.root / execution["outputs"][0]["artifact"]["path"]
        artifact.chmod(0o600)
        artifact.write_bytes(b"a changed scientific result")
        self.assertIn("artifact_corrupt", {o["code"] for o in self.read_state()["obligations"]})

    def test_legacy_candidate_without_recorded_inputs_requires_named_reassessment(self):
        self.build_managed_study()
        with patch.object(development, "uses_claim_dependencies", return_value=False):
            plan, execution = self.run_cycle(bind_plan(self, self.plan()))
            self.execution_payload = execution
            self.mutate(development.assess_cycle, self.assessment(plan, execution))
            self.save_checkpoint()
            ManagedStrategy(self).decide(phase="result")
        records = json.loads(json.dumps(self.store.snapshot()["records"]))
        for saved in records["readiness_review"].values():
            saved.pop("candidate")
        for saved in records["cycle_assessment"].values():
            for key in ("preparation", "constitution"):
                saved["dependencies"].pop(key)
        for saved in records["checkpoint"].values():
            for key in ("preparation", "constitution"):
                saved["dependencies"].pop(key)
        report = research_decisions.decision_state(records, self.artifacts, "write")
        missing = [o for o in report["obligations"] if o["code"] == "claim_reassessment_required"]
        self.assertTrue(missing, report["obligations"])
        self.assertTrue(all(o.get("record_id") for o in missing))


class RevisionMemoTests(SourceLimitedCase):
    def test_status_computes_science_once_per_revision_for_a_legacy_scoped_pin(self):
        from contextlib import ExitStack
        from research_harness import cli, literature, publication, publication_scope, synthesis
        from research_harness.evaluation import Evaluation
        with patch.object(development, "uses_claim_dependencies", return_value=False), \
                patch.object(publication, "uses_claim_dependencies", return_value=False):
            self.prepare_source_limited()
            self.mutate(development.record_readiness_review, self.review(self.execution_payload))
            self.record_scope()
            self.accept_scope()
            self.scoped_manuscript()
        ManagedStrategy(self).ensure_intent()
        self.store.mutate("fixture.stage", {}, lambda tx: tx.put("workspace", "study", {
            "stage": "evaluate", "status": "in_progress"}),
            expected_revision=self.store.revision, request_id="evaluate")
        counts, roots = {}, []

        def count_calls(module, name, identify):
            function = getattr(module, name)
            counts[name] = Counter()
            def count(*args, **kwargs):
                counts[name][identify(*args, **kwargs)] += 1
                return function(*args, **kwargs)
            return patch.object(module, name, new=count)

        def capture_evaluation(records, artifacts):
            evaluation = Evaluation(records, artifacts)
            roots.append(evaluation)
            return evaluation

        with ExitStack() as stack:
            stack.enter_context(patch.object(cli, "Evaluation", side_effect=capture_evaluation))
            for module, name, identify in (
                (synthesis, "_synthesis_state", lambda evaluation, profile: evaluation.records.read_revision()),
                (literature, "_foundation_state", lambda evaluation, profile: evaluation.records.read_revision()),
                (development, "_build_candidate", lambda context: context.records.read_revision()),
                (development, "_branch_inputs", lambda context: context.records.read_revision()),
                (development._Context, "_verify_candidate_digest", lambda context, recorded, candidate, **kw: (
                    context.records.read_revision(), recorded,
                    candidate if isinstance(candidate, str) else candidate["digest"], kw.get("scoped", False))),
                (publication, "_rebuild_pinned_readiness", lambda records, artifacts, bundle: (bundle["id"], bundle["prepared_revision"])),
                (publication_scope, "_rebuild_scoped_dependencies", lambda records, artifacts, candidate=None: (
                    records.read_revision(), None if candidate is None else digest(candidate))),
            ):
                stack.enter_context(count_calls(module, name, identify))
            report = cli.status_report(self.store)
            root = roots[0]
            bundle = publication.find_selected_bundle(root.records)
            first = publication.rebuild_pinned_readiness(root.records, root, bundle)
            self.assertIs(first, publication.rebuild_pinned_readiness(root.records, root, bundle))
            original = next(iter(root.records["readiness_review"].values()))
            one, two = development._Context(root.records, root), development._Context(root.records, root)
            candidate, _ = one.build_candidate()
            recorded = original["payload"]["candidate_digest"]
            self.assertTrue(one.verify_candidate_digest(recorded, candidate))
            self.assertTrue(two.verify_candidate_digest(recorded, candidate))
            self.assertTrue(one.candidate_inputs)
            self.assertEqual(two.candidate_inputs, one.candidate_inputs)
        self.assertEqual([item["code"] for item in report["manuscript_obligations"]], ["strategy_dossier_missing"])
        for name, revisions in counts.items():
            self.assertTrue(revisions, name)
            self.assertTrue(all(count == 1 for count in revisions.values()), (name, revisions))
        self.assertGreater(len(counts["_synthesis_state"]), 1)
        self.assertEqual(set(counts["_synthesis_state"]), set(counts["_build_candidate"]))
        altered = copy.deepcopy(bundle)
        altered["review_inputs"]["candidate"]["source_records_digest"] = "unrecorded-input"
        with self.assertRaises(ResearchError) as raised:
            publication.rebuild_pinned_readiness(root.records, root, altered)
        self.assertEqual(raised.exception.code, "claim_reassessment_required")


class LegacyReportShapeTests(SourceLimitedCase):
    def test_complete_earlier_reports_derive_empty_prerequisites(self):
        self.prepared_study()
        assess = development._assess
        def assess_earlier_shape(context, payload, **options):
            report = assess(context, payload, **options)
            if not options.get("historical"):
                report.pop("prerequisites")
            return report
        with patch.object(development, "uses_claim_dependencies", return_value=False), \
                patch.object(development, "_assess", side_effect=assess_earlier_shape):
            plan, execution = self.run_cycle(bind_plan(self, self.plan()))
            self.mutate(development.assess_cycle, self.assessment(plan, execution))
            self.save_checkpoint()
            self.mutate(development.record_readiness_review, self.review(execution))
        records = self.store.snapshot()["records"]
        self.assertNotIn("prerequisites", records["cycle_assessment"]["assessment-1"]["assessment"])
        report = development.readiness_state(records, self.artifacts)
        self.assertTrue(report["ready"], report["obligations"])

    def test_adverse_hash_only_scoped_review_uses_its_recorded_target(self):
        from research_harness.publication_scope import find_required_corrections
        review = {"id": "old-scoped-review", "payload": {"verdict": "not_ready", "checks": []},
                  "scientific_target_digest": "recorded-scientific-target"}
        findings = find_required_corrections({"scoped_readiness_review": {review["id"]: review}})
        self.assertEqual(findings[0]["scientific_target_digest"], "recorded-scientific-target")

    def test_adverse_review_without_a_recoverable_target_returns_named_reassessment(self):
        from research_harness.publication_scope import assess_manuscript_readiness
        records = self.store.snapshot()["records"]
        review = {"id": "old-full-review", "payload": {"verdict": "not_ready", "checks": []}}
        records["readiness_review"] = {review["id"]: review}
        report = assess_manuscript_readiness(records, self.artifacts)
        missing = [item for item in report["obligations"] if item["code"] == "claim_reassessment_required"]
        self.assertTrue(missing, report["obligations"])
        self.assertEqual(missing[0]["record_id"], review["id"])

    def test_old_assessment_reports_return_readiness_and_status_reports(self):
        from research_harness.cli import status_report
        from research_harness.gates import gate_state
        assess = development._assess

        def assess_earlier_shape(context, payload, **options):
            report = assess(context, payload, **options)
            if not options.get("historical"):
                report.pop("prerequisites")
            return report

        with patch.object(development, "uses_claim_dependencies", return_value=False), \
                patch.object(development, "_assess", side_effect=assess_earlier_shape):
            self.prepare_source_limited()
            self.mutate(development.record_readiness_review, self.review(self.execution_payload))
            self.record_scope()
            self.accept_scope()
        self.store.mutate("fixture.stage", {}, lambda tx: tx.put("workspace", "study", {
            "stage": "evaluate", "status": "in_progress"}), expected_revision=self.store.revision, request_id="evaluate")
        records = self.store.snapshot()["records"]
        self.assertNotIn("prerequisites", records["cycle_assessment"]["assessment-1"]["assessment"])
        for report in (status_report(self.store), gate_state(records, self.artifacts, "readiness"),
                       gate_state(records, self.artifacts, "manuscript-readiness")):
            findings = [item for item in report["obligations"] if item["code"] == "claim_reassessment_required"]
            self.assertTrue(findings, report["obligations"])
            self.assertTrue(all(item.get("record_id") for item in findings))


class ConstitutionCompatibilityTests(DevelopmentCase):
    def check_previous_contract(self, profile):
        from research_harness.cohort_evidence import cohort_report
        from research_harness.literature import foundation_state
        from research_harness.synthesis import synthesis_state
        if profile == "research":
            self.prepared_study()
        else:
            work = self.configured(profile)
            link = self.read_source()
            self.complete_foundation(work, profile)
            self.mutate(self.api().record_standards, self.standards(link, profile))
        records = self.store.snapshot()["records"]
        def read_reports(snapshot):
            return [principles.configuration_state(snapshot, self.artifacts, profile),
                    foundation_state(snapshot, self.artifacts, profile),
                    synthesis_state(snapshot, self.artifacts, profile),
                    cohort_report(snapshot, self.artifacts, []),
                    principles.configuration_state(snapshot, self.artifacts,
                        "verification" if profile == "research" else "research")]
        before = read_reports(records)
        changed = self.root / "constitution-v7.md"
        changed.write_text("# Research constitution\n\nVersion: 7\n\nA revised distributed contract.\n", encoding="utf-8")
        with patch.object(principles, "CONSTITUTION_PATH", changed):
            self.assertEqual(read_reports(records), before)
            # Full serialized reports are the native frozen-snapshot audit contract.
            self.assertEqual(json.dumps(read_reports(records), ensure_ascii=False).encode("utf-8"),
                             json.dumps(before, ensure_ascii=False).encode("utf-8"))
            self.assertEqual(digest(read_reports(records)), digest(before))
            adopted = records["configuration"]["research"]["constitution"]
            missing = copy.deepcopy(records)
            missing["constitution"] = {}
            self.assertFalse(principles.configuration_state(missing, self.artifacts, profile)["ready"])
            different = copy.deepcopy(records)
            different["configuration"]["research"]["constitution"]["version"] = "5"
            self.assertIn("constitution_revalidation_required", {o["code"] for o in
                principles.configuration_state(different, self.artifacts, profile)["obligations"]})
            self.mutate(principles.revalidate_constitution, {
                "previous_sha256": adopted["sha256"], "reason": "Explicitly adopt Version 7."})
            self.assertEqual(principles.configuration_report(self.store, profile)["constitution"]["version"], "7")

    def test_version_six_research_reports_are_identical_after_the_distribution_changes(self):
        self.check_previous_contract("research")

    def test_version_six_verification_reports_are_identical_after_the_distribution_changes(self):
        self.check_previous_contract("verification")


class ClaimProjectionCompatibilityTests(unittest.TestCase):
    def test_projection_preserves_user_ids_and_locator_values_named_like_audit_fields(self):
        from research_harness.claim_dependencies import project_claim_dependencies
        evidence = [{"reference": {"kind": "result", "locator": {"value": {"constitution": "scientific output"}}}}]
        branches = {name: {"assessment": {"payload": {"scope": {"id": "preparation"}},
                     "evidence": evidence, "dependencies": {"objective": "retained", "preparation": "advisory"}}}
                    for name in ("synthesis", "preparation", "configuration", "constitution")}
        projected = project_claim_dependencies(branches, managed=True, kind="branches")
        self.assertEqual(set(projected), set(branches))
        for branch in projected.values():
            self.assertEqual(branch["assessment"]["evidence"], evidence)
            self.assertEqual(branch["assessment"]["payload"], {"scope": {"id": "preparation"}})
            self.assertEqual(branch["assessment"]["dependencies"], {"objective": "retained"})

    def test_recorded_inputs_are_authenticated_before_comparing_claim_projections(self):
        from research_harness.claim_dependencies import accept_claim_dependencies, project_claim_dependencies
        original = {"objective": {"id": "objective", "statement": "Complete claim"},
                    "preparation": "old-preparation", "configuration": "old-configuration", "constitution": "v6",
                    "evidence": "same-evidence", "inheritance": [], "strategy_failures": "same-failures"}
        current = dict(original, preparation="new-preparation", configuration="new-configuration", constitution="v8")
        projected = project_claim_dependencies(current, managed=True)
        for recorded in (digest(original), digest(projected)):
            self.assertTrue(accept_claim_dependencies(recorded, projected, managed=True, recorded_inputs=original))
            self.assertFalse(accept_claim_dependencies(recorded, projected, managed=True,
                recorded_inputs=dict(original, evidence="tampered")))
        self.assertFalse(accept_claim_dependencies(digest(original), digest(projected), managed=True))
        for key in ("objective", "evidence", "inheritance", "strategy_failures"):
            changed = project_claim_dependencies(dict(current, **{key: "changed"}), managed=True)
            with self.subTest(key=key):
                self.assertFalse(accept_claim_dependencies(digest(original), changed, managed=True, recorded_inputs=original))

    def test_unmanaged_research_and_verification_keep_exact_comparisons(self):
        from research_harness.claim_dependencies import accept_claim_dependencies, project_claim_dependencies, uses_claim_dependencies
        for records in ({"configuration": {"research": {"profile": "research"}}},
                        {"configuration": {"research": {"profile": "verification"}}, "workspace": {"study": {}}}):
            self.assertFalse(uses_claim_dependencies(records))
            value = {"preparation": "old", "evidence": "retained"}
            self.assertEqual(project_claim_dependencies(value, managed=False), value)
            self.assertFalse(accept_claim_dependencies(value, dict(value, preparation="new"), managed=False))

