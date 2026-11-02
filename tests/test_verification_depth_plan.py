"""Verification depth expands decisive literature without changing the sample."""

import copy

from literature_fixtures import FIELDS, LiteratureCase
from research_harness import depth, literature, sampling
from research_harness.errors import ResearchError
from research_harness.evidence import digest
from research_harness.limits import describe_depth, limits_report
from research_harness.principles import initialize_research
from research_harness.reading import record_reading_batch, require_fulltext


class VerificationDepthPlanTests(LiteratureCase):
    def setUp(self):
        super().setUp()
        self.target = self.verification_target()
        self.mutate(initialize_research, {"profile": "verification", "target": self.target,
                                          "preparation_policy": "sampled-v1"})

    def record_plan(self, values, identifier="verification-depth", **extra):
        payload = {"id": identifier, "profile": "verification", "values": values,
                   "reason": "Check additional decisive literature.",
                   "stopping_criterion": "Resolve the remaining competing bounds and assumptions."}
        return self.mutate(depth.record_depth, dict(payload, **extra))["result"]

    def record_core(self, number, **extra):
        return self.mutate(require_fulltext, dict({"id": "core-" + str(number), "profile": "verification",
            "version_id": "arxiv:2601.%05dv1" % number, "purpose": "core",
            "reason": "The verdict depends on this bound.", "depends_on": "finding-1"}, **extra))["result"]

    def record_hits(self, numbers, identifier="hits", **extra):
        items = [{"version_id": "arxiv:2601.%05dv1" % n, "note": "Read the complete saved abstract.",
                  "notes": {f: {"text": "The abstract discusses " + f + ".", "status": "present"} for f in FIELDS},
                  "search_hit": True} for n in numbers]
        return self.mutate(record_reading_batch, dict({"id": identifier, "depth": "abstract", "items": items}, **extra))["result"]

    def test_plan_admits_eleventh_core_and_preserves_critical_history(self):
        self.cohort(tuple(range(1, 12)))
        for n in range(1, 11):
            self.record_core(n)
        self.assert_error("core_limit_reached", lambda: self.record_core(11))
        self.record_plan({"core_fulltexts": 11})
        saved = self.record_core(11)
        self.assertEqual(saved["depth"], {"core_fulltexts": 11})
        before = copy.deepcopy(self.store.snapshot()["records"]["fulltext_requirement"])
        self.record_plan({"core_fulltexts": 10}, "later")
        records = self.store.snapshot()["records"]
        self.assertEqual(records["fulltext_requirement"], before)
        self.assertEqual(len(sampling.core_requirements(records)), 11)
        self.assertTrue(all(r["critical"] for r in before.values()))

    def test_plan_admits_twenty_first_reading_and_keeps_recorded_amounts(self):
        self.cohort(tuple(range(1, 22)))
        first = self.record_hits(range(1, 21))
        self.assertEqual(first["effective_depth"], {"search_readings": 20})
        self.assert_error("search_reading_limit_reached", lambda: self.record_hits([21], "extra"))
        self.record_plan({"search_readings": 21})
        last = self.record_hits([21], "extra")
        self.assertEqual(last["effective_depth"], {"search_readings": 21})
        before = copy.deepcopy(self.store.snapshot()["records"]["reading_batch"])
        self.record_plan({"search_readings": 20}, "later")
        records = self.store.snapshot()["records"]
        self.assertEqual(records["reading_batch"], before)
        self.assertEqual(len(sampling.search_readings(records)), 21)

    def test_plan_admits_larger_search_and_preserves_evidence_digest(self):
        self.scope([self.target["id"]], profile="verification", target=self.target)
        payload = dict(self.capture_search(found=["arxiv:2601.%05dv1" % n for n in range(1, 12)]),
                       profile="verification")
        self.assert_error("invalid_search", lambda: self.mutate(literature.record_search, payload))
        self.record_plan({"hits_per_query": 11})
        self.mutate(literature.record_search, payload)
        saved = self.store.snapshot()["records"]["literature_search"][payload["id"]]
        self.assertEqual(saved["hits_per_query"], 11)
        self.assertEqual(saved["coverage"][0]["requested_k"], 11)
        before = digest(saved)
        self.record_plan({"hits_per_query": 10}, "later")
        records = self.store.snapshot()["records"]
        self.assertEqual(digest(records["literature_search"][payload["id"]]), before)
        report = literature.foundation_report(self.store, "verification")
        self.assertNotIn("search_evidence_stale", {o["code"] for o in report["obligations"] + report["notices"]})

    def test_per_call_overrides_apply_without_a_plan(self):
        self.cohort(tuple(range(1, 22)))
        self.assertEqual(self.record_core(1, depth={"core_fulltexts": 11})["depth"], {"core_fulltexts": 11})
        self.record_hits(range(1, 21))
        self.assertEqual(self.record_hits([21], "extra", depth_values={"search_readings": 21})["effective_depth"],
                         {"search_readings": 21})
        payload = dict(self.capture_search(found=["arxiv:2601.%05dv1" % n for n in range(1, 12)]),
                       profile="verification", question="Check competing bounds.", hits_per_query=11)
        self.mutate(literature.record_search, payload)
        records = self.store.snapshot()["records"]
        self.assertEqual(records["literature_search"][payload["id"]]["hits_per_query"], 11)
        self.assertIsNone(depth.find_current_depth_plan(records))

    def test_profile_selections_are_isolated_and_report_stopping_criterion(self):
        research = self.mutate(depth.record_depth, {"id": "research-depth", "profile": "research",
            "values": {"hits_per_query": 2, "sample_size": 500}, "reason": "Research-only amounts."})["result"]
        verification = self.record_plan({"hits_per_query": 12})
        records = self.store.snapshot()["records"]
        self.assertEqual(records["depth_plan_selection"], {"research": {"id": research["id"]},
                                                          "verification": {"id": verification["id"]}})
        self.assertEqual(depth.find_current_depth_plan(records, profile="research"), research)
        self.assertEqual(depth.find_current_depth_plan(records), verification)
        self.assertEqual(depth.resolve_depth_value(records, "hits_per_query", 10, profile="research"), 2)
        self.assertEqual(depth.resolve_depth_values(records)["sample_size"], 100)
        self.assertEqual(describe_depth(records), {"plan_id": verification["id"], "profile": "verification",
            "stopping_criterion": verification["stopping_criterion"], "values": depth.resolve_depth_values(records)})
        self.assertEqual(limits_report(records)["core_papers"]["limit"], 10)

    def test_legacy_selection_and_untagged_plan_remain_research_only(self):
        records = {"configuration": {"research": {"profile": "verification"}},
                   "depth_plan": {"old": {"id": "old", "values": {"hits_per_query": 50, "sample_size": 200}}},
                   "depth_plan_selection": {"current": {"id": "old"}}}
        self.assertIsNone(depth.find_current_depth_plan(records))
        self.assertEqual(depth.resolve_depth_values(records)["hits_per_query"], 10)
        self.assertEqual(depth.resolve_depth_values(records, profile="research")["hits_per_query"], 50)
        records["depth_plan_selection"]["verification"] = {"id": "old"}
        self.assertIsNone(depth.find_current_depth_plan(records))

    def test_forbidden_verification_keys_are_refused(self):
        for key in ("sample_size", "loop_abstracts", "round_loop_abstracts", "innovation_candidates", "innovation_cases"):
            with self.subTest(key=key):
                self.assert_error("profile_inapplicable", lambda: self.record_plan({key: 200}))
                self.assert_error("profile_inapplicable", lambda: depth.resolve_depth_values(
                    self.store.snapshot()["records"], {key: 200}))

    def test_verification_plan_requires_explicit_profile_and_stopping_criterion(self):
        self.assert_error("profile_inapplicable", lambda: self.mutate(depth.record_depth,
            {"id": "implicit", "values": {"core_fulltexts": 11}, "reason": "Expand decisive reading."}))
        for bad in (None, "", " ", 7):
            with self.subTest(stopping_criterion=bad):
                self.assert_error("invalid_input", lambda: self.record_plan({"core_fulltexts": 11}, stopping_criterion=bad))
        self.assert_error("invalid_input", lambda: self.mutate(depth.record_depth,
            {"id": "no-stop", "profile": "verification", "values": {}, "reason": "Expand decisive reading."}))

    def assert_floor_refused(self, key, default):
        before = self.store.snapshot()
        for amount in (default - 1, 0, -1):
            for action in (lambda: self.record_plan({key: amount}),
                           lambda: depth.resolve_depth_values(before["records"], {key: amount})):
                with self.subTest(key=key, amount=amount), self.assertRaises(ResearchError) as raised:
                    action()
                self.assertEqual(raised.exception.code, "invalid_input")
                for part in (key, str(amount), str(default)):
                    self.assertIn(part, raised.exception.message)
        self.assertEqual(self.store.snapshot(), before)
        self.record_plan({key: default})

    def test_hits_per_query_cannot_drop_below_verification_default(self):
        self.assert_floor_refused("hits_per_query", 10)

    def test_search_readings_cannot_drop_below_verification_default(self):
        self.assert_floor_refused("search_readings", 20)

    def test_core_fulltexts_cannot_drop_below_verification_default(self):
        self.assert_floor_refused("core_fulltexts", 10)

    def test_malformed_amounts_are_refused(self):
        for key in ("hits_per_query", "search_readings", "core_fulltexts"):
            for amount in (True, None, "21", 21.5):
                with self.subTest(key=key, amount=amount):
                    self.assert_error("invalid_input", lambda: self.record_plan({key: amount}))
                    self.assert_error("invalid_input", lambda: depth.resolve_depth_values(
                        self.store.snapshot()["records"], {key: amount}))

    def test_sample_identity_redraw_and_prediction_do_not_change(self):
        collection = self.cohort((1, 2, 3))
        self.mutate(sampling.record_sample, {"id": "sample", "collection_id": collection, "size": 2, "seed": "fixed"})
        before = self.store.snapshot()["records"]
        prediction = sampling.prediction(before)
        self.record_plan({"hits_per_query": 50, "search_readings": 50, "core_fulltexts": 50})
        records = self.store.snapshot()["records"]
        self.assertEqual(sampling.current_sample(records), sampling.current_sample(before))
        self.assertEqual(sampling.prediction(records), prediction)
        self.assertEqual(sampling.resolve_sample_limit(records), 100)
        self.assert_error("sample_exists", lambda: self.mutate(sampling.record_sample,
            {"id": "redraw", "collection_id": collection, "size": 2, "seed": "different"}))
        self.assert_error("invalid_input", lambda: self.mutate(sampling.record_sample,
            {"id": "larger", "collection_id": collection, "size": 101, "seed": "fixed"}))
