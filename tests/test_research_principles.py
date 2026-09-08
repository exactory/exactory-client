import copy
import importlib
from unittest.mock import patch

from literature_fixtures import LiteratureCase
from research_harness.evidence import digest


class PrinciplesTests(LiteratureCase):
    def api(self):
        return importlib.import_module("research_harness.principles")

    def test_initialization_is_preparation_and_does_not_invent_an_objective(self):
        api = self.api()
        before = self.store.snapshot()
        report = api.configuration_report(self.store, "research")
        self.assertFalse(report["ready"])
        self.assertEqual(self.store.snapshot(), before)
        result = self.mutate(api.initialize_research, {"profile": "research", "target": None})
        config = self.store.snapshot()["records"]["configuration"]["research"]
        self.assertIsNone(config["target"])
        self.assertEqual(config["constitution"]["sha256"], result["result"]["constitution"]["sha256"])
        self.assertIn("objective_missing", {x["code"] for x in api.configuration_report(self.store, "research")["obligations"]})

    def test_full_objective_is_fixed_separately_from_branch_scope_and_replay(self):
        api = self.api()
        self.mutate(api.initialize_research, {"profile": "research", "target": None})
        target = {"kind": "objective", "id": "objective-1", "statement": "Prove the bound for every integer from 5 through 15."}
        payload = {"target": target, "reason": "Fix the complete authorized problem."}
        revision = self.store.revision
        result = api.set_target(self.store, payload, expected_revision=revision, request_id="full-objective")
        self.store.mutate("branch", {}, lambda tx: tx.put("branch_scope", "five", {"statement": "Prove the n=5 case."}),
                          expected_revision=self.store.revision, request_id="branch")
        bad = copy.deepcopy(payload)
        bad["target"]["statement"] = "Prove the n=5 case."
        self.assert_error("objective_locked", lambda: self.mutate(api.set_target, bad))
        self.assertEqual(api.set_target(self.store, payload, expected_revision=revision, request_id="full-objective"), result)
        self.assertEqual(self.store.snapshot()["records"]["research_objective"]["objective-1"], target)
        self.assertTrue(api.configuration_report(self.store, "research")["ready"])

    def test_verification_pin_uses_original_bytes_and_scope_agreement(self):
        api = self.api()
        work = self.metadata()
        target = {"kind": "work", "id": work, "source_id": None, "sha256": None}
        self.mutate(api.initialize_research, {"profile": "verification", "target": target})
        self.scope([work], profile="verification", target=target)
        self.assertIn("target_source_pin_missing", {x["code"] for x in api.configuration_report(self.store, "verification")["obligations"]})
        capture = self.capture(work)
        bad = dict(target, source_id=capture["source_id"], sha256=capture["text"]["sha256"])
        self.assert_error("invalid_target", lambda: self.mutate(api.set_target, {"target": bad, "reason": "Extraction is not the original."}))
        pin = dict(target, source_id=capture["source_id"], sha256=capture["original"]["sha256"])
        self.mutate(api.set_target, {"target": pin, "reason": "Pin the acquired original body."})
        self.assertIn("target_mismatch", {x["code"] for x in api.configuration_report(self.store, "verification")["obligations"]})
        self.scope([work], profile="verification", target=pin)
        self.assertTrue(api.configuration_report(self.store, "verification")["ready"])
        self.assertNotIn("research_objective", self.store.snapshot()["records"])

    def test_changed_constitution_requires_explicit_adoption_and_preserves_previous_bytes(self):
        api = self.api()
        target = {"kind": "objective", "id": "root", "statement": "Establish the complete finite bound."}
        first = self.mutate(api.initialize_research, {"profile": "research", "target": target})
        old = first["result"]["constitution"]
        changed = self.root / "new-policy.md"
        changed.write_text("# Research constitution\n\nVersion: 2\n\nRequire explicit current assessment.\n", encoding="utf-8")
        with patch.object(api, "CONSTITUTION_PATH", changed):
            before = self.store.snapshot()
            self.assertIn("constitution_revalidation_required", {x["code"] for x in api.configuration_report(self.store, "research")["obligations"]})
            self.assertEqual(before, self.store.snapshot())
            payload = {"previous_sha256": old["sha256"], "reason": "Review the new policy; dependent decisions require their own current assessments."}
            self.mutate(api.revalidate_constitution, payload)
            report = api.configuration_report(self.store, "research")
            self.assertTrue(report["ready"])
            self.assertNotEqual(report["constitution"]["sha256"], old["sha256"])
            self.assertEqual(report["target"], target)
            historical = self.store.snapshot()["records"]["constitution"][old["sha256"]]
            self.assertTrue(self.artifacts.read(historical["artifact"]))

    def test_configuration_cannot_switch_profile_or_reinitialize_over_a_fixed_objective(self):
        api = self.api()
        self.mutate(api.initialize_research, {"profile": "research", "target": None})
        before = digest(self.store.snapshot())
        self.assert_error("configuration_exists", lambda: self.mutate(api.initialize_research, {"profile": "verification", "target": None}))
        self.assertEqual(digest(self.store.snapshot()), before)

    def test_revalidation_checks_previous_contract_and_old_request_replays_after_release(self):
        api = self.api()
        payload = {"profile": "research", "target": None}
        first = api.initialize_research(self.store, payload, expected_revision=0, request_id="initialize")
        self.assert_error("constitution_conflict", lambda: self.mutate(api.revalidate_constitution,
            {"previous_sha256": "0" * 64, "reason": "An unrelated policy must not be adopted as the predecessor."}))
        changed = self.root / "policy.md"
        changed.write_text("# Research constitution\n\nVersion: 2\n\nA revised contract.\n", encoding="utf-8")
        with patch.object(api, "CONSTITUTION_PATH", changed):
            self.assertEqual(api.initialize_research(self.store, payload, expected_revision=0, request_id="initialize"), first)
            self.assertIn("constitution_revalidation_required", {x["code"] for x in api.configuration_report(self.store, "research")["obligations"]})

    def test_configuration_race_cannot_publish_a_prepared_objective(self):
        api = self.api()
        original = api._constitution

        def policy_after_concurrent_write():
            policy = original()
            self.store.mutate("concurrent-note", {}, lambda tx: tx.put("note", "one", {"text": "Concurrent work."}),
                              expected_revision=self.store.revision, request_id="concurrent")
            return policy

        with patch.object(api, "_constitution", side_effect=policy_after_concurrent_write):
            self.assert_error("stale_revision", lambda: api.initialize_research(self.store,
                {"profile": "research", "target": {"kind": "objective", "id": "root", "statement": "The complete original objective."}},
                expected_revision=0, request_id="racing-init"))
        self.assertEqual(self.store.snapshot()["records"], {"note": {"one": {"text": "Concurrent work."}}})
