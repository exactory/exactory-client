"""Research depth amounts are prospective policy, outside evidence dependencies."""

import json
from pathlib import Path

from development_fixtures import DevelopmentCase
from research_harness import cli, depth, lineage
from research_harness.errors import ResearchError
from research_harness.limits import limits_report
from test_research_lineage import LineageCase
from test_research_synthesis import SynthesisCase


DEFAULTS = {"loop_abstracts": 100, "round_loop_abstracts": 40, "hits_per_query": 10,
            "innovation_candidates": 10, "innovation_cases": 5, "sample_size": 100,
            "search_readings": 20, "core_fulltexts": 10}


class DepthPlanTests(LineageCase):
    def record_depth_plan(self, values, identifier="depth-1"):
        self.assertIn("depth", cli.OPERATIONS)
        return self.mutate(cli.OPERATIONS["depth"], {"id": identifier, "values": values, "reason": "Match the question's required depth."})

    def test_defaults_without_a_plan(self):
        records = self.store.snapshot()["records"]
        self.assertNotIn("depth", limits_report(records))
        self.assertEqual(depth.resolve_depth_values(records), DEFAULTS)

    def test_partial_plans_retain_current_values_and_immutable_history(self):
        first = self.record_depth_plan({"loop_abstracts": 150})["result"]
        revision = self.store.revision
        self.assertEqual(first, {"id": "depth-1", "values": dict(DEFAULTS, loop_abstracts=150),
                                 "reason": "Match the question's required depth.", "revision": revision})
        second = self.record_depth_plan({"hits_per_query": 12}, "depth-2")["result"]
        self.assertEqual(second["values"], dict(DEFAULTS, loop_abstracts=150, hits_per_query=12))
        records = self.store.snapshot()["records"]
        self.assertEqual(records["depth_plan"]["depth-1"], first)
        self.assertEqual(records["depth_plan_selection"]["current"], {"id": "depth-2"})
        self.assertEqual(limits_report(records)["depth"], {"plan_id": "depth-2", "values": second["values"]})
        self.assert_error("record_conflict", lambda: self.record_depth_plan({"loop_abstracts": 160}))

    def test_all_eight_accessors_follow_the_plan(self):
        from research_harness import sampling
        values = {key: default * 2 for key, default in DEFAULTS.items()}
        self.record_depth_plan(values)
        records = self.store.snapshot()["records"]
        self.assertEqual([lineage.resolve_loop_limit(records), lineage.resolve_round_loop_limit(records), lineage.resolve_hits_per_query(records),
                          lineage.resolve_innovation_candidates(records), lineage.resolve_innovation_cases(records), sampling.resolve_sample_limit(records),
                          sampling.resolve_search_reading_limit(records), sampling.resolve_core_limit(records)], list(values.values()))

    def test_bounds_unknown_keys_types_and_case_relationship(self):
        for key, default in DEFAULTS.items():
            for bad in (0, -1, True, 1.5, "2", None):
                with self.subTest(key=key, bad=bad):
                    self.assert_error("invalid_input", lambda: self.record_depth_plan({key: bad}))
        for bad in ({"unknown": 1}, [], None, {"innovation_candidates": 4}, {"innovation_cases": 11}):
            with self.subTest(values=bad):
                self.assert_error("invalid_input", lambda: self.record_depth_plan(bad))
        self.record_depth_plan({key: value * 1000 + 1 for key, value in DEFAULTS.items()}, "unbounded")
        self.record_depth_plan({key: 1 for key in DEFAULTS}, "minimum")
        for reason in ("", " ", None, 2):
            self.assert_error("invalid_input", lambda: self.mutate(cli.OPERATIONS["depth"],
                {"id": "bad-reason", "values": {}, "reason": reason}))

    def test_raised_loop_limit_admits_the_101st_reading(self):
        from research_harness.acquisition import import_response
        from research_fixtures import atom, entry
        import_response(self.store, "arxiv", atom([entry("2601.%05dv1" % n) for n in range(1, 102)], total=101, size=101),
                        source_url="https://export.arxiv.org/api/query?search_query=all:bounded",
                        captured_at="2026-09-07T12:00:00Z", expected_revision=self.store.revision, request_id="loop-metadata")
        versions = ["arxiv:2601.%05dv1" % n for n in range(1, 102)]
        for start in range(0, 100, 20):
            self.loop_read(versions[start:start + 20], "direct", "relevant")
        self.assert_error("loop_limit_reached", lambda: self.loop_read(versions[100:], "direct", "relevant"))
        self.record_depth_plan({"loop_abstracts": 101})
        self.loop_read(versions[100:], "direct", "relevant")
        state = lineage.loop_state(self.store.snapshot()["records"])
        self.assertEqual((state["readings"], state["limit"]), (101, 101))

    def test_lowered_limit_refuses_with_the_current_value(self):
        versions = [self.metadata(n) for n in (1, 2)]
        self.loop_read(versions[:1], "direct", "relevant")
        self.record_depth_plan({"loop_abstracts": 1})
        with self.assertRaises(ResearchError) as raised:
            self.loop_read(versions[1:], "direct", "relevant")
        self.assertEqual(raised.exception.code, "loop_limit_reached")
        self.assertEqual(raised.exception.details["limit"], 1)
        self.assertIn("at most 1 abstracts", raised.exception.message)

    def test_verification_profile_refuses_and_keeps_defaults(self):
        from research_harness.principles import initialize_research
        # A separate store is needed because profiles themselves cannot be replaced.
        from research_harness.storage import Store
        store = Store(self.root / "verification", create=True)
        from research_harness.acquisition import import_response
        from research_fixtures import atom, entry
        import_response(store, "arxiv", atom([entry("2601.00999v1")], total=1),
                        source_url="https://export.arxiv.org/api/query?id_list=2601.00999v1",
                        captured_at="2026-09-07T12:00:00Z", expected_revision=0, request_id="target")
        initialize_research(store, {"profile": "verification", "target": {"kind": "work", "id": "arxiv:2601.00999v1",
                            "source_id": None, "sha256": None}}, expected_revision=store.revision, request_id="init")
        self.assertIn("depth", cli.OPERATIONS)
        self.assert_error("profile_inapplicable", lambda: cli.OPERATIONS["depth"](store,
            {"id": "depth", "values": {"sample_size": 200}, "reason": "More sampling."},
            expected_revision=store.revision, request_id="depth"))
        records = store.snapshot()["records"]
        self.assertNotIn("depth", limits_report(records))
        self.assertEqual(depth.resolve_depth_values(records), DEFAULTS)
        self.assert_error("profile_inapplicable", lambda: depth.resolve_depth_values(records, {"sample_size": 200}))

    def test_call_overrides_resolve_without_changing_the_plan(self):
        self.record_depth_plan({"loop_abstracts": 150, "innovation_candidates": 20})
        records = self.store.snapshot()["records"]
        self.assertEqual(depth.resolve_depth_values(records, {"loop_abstracts": 2001}, keys=("loop_abstracts",)),
                         {"loop_abstracts": 2001})
        self.assertEqual(depth.resolve_depth_values(records, keys=("loop_abstracts",)), {"loop_abstracts": 150})
        for invalid in ({"loop_abstracts": True}, {"unknown": 1}, {"innovation_cases": 21}, []):
            self.assert_error("invalid_input", lambda: depth.resolve_depth_values(records, invalid))

    def test_loop_batch_and_closure_record_their_override_without_relabeling_history(self):
        from literature_fixtures import FIELDS
        from research_harness.reading import record_reading_batch
        version = self.metadata(1)
        self.record_depth_plan({"loop_abstracts": 50})
        item = {"version_id": version, "note": "Read the complete abstract.",
                "notes": {f: {"text": "The abstract discusses " + f + ".", "status": "present"} for f in FIELDS},
                "loop": {"purposes": ["direct"], "disposition": "out_of_scope", "source": "search"}}
        result = self.mutate(record_reading_batch, {"id": "override-batch", "items": [item],
                            "depth": {"loop_abstracts": 1, "round_loop_abstracts": 2}})["result"]
        self.assertEqual(result["effective_depth"], {"loop_abstracts": 1, "round_loop_abstracts": 2})
        purposes = {p: {"status": "gap", "note": "The requested limit was reached."} for p in lineage.SEARCH_PURPOSES}
        closure = self.mutate(lineage.record_loop_closure, {"id": "override-closure", "purposes": purposes,
                              "depth": {"loop_abstracts": 1}})["result"]
        self.assertEqual(closure["depth"], {"loop_abstracts": 1})
        before = self.store.snapshot()["records"]["reading_batch"]["override-batch"]
        self.assertEqual(before["effective_depth"], result["effective_depth"])
        self.record_depth_plan({"loop_abstracts": 200}, "later")
        records = self.store.snapshot()["records"]
        self.assertEqual(records["reading_batch"]["override-batch"], before)
        self.assertEqual(records["loop_closure"]["override-closure"], closure)
        self.assertEqual(lineage.loop_obligations(records), [])

    def test_population_query_uses_the_current_limit(self):
        self.record_depth_plan({"loop_abstracts": 1})
        self.assert_error("invalid_input", lambda: lineage.population_query(self.store, ["bounded"], limit=2))
        self.record_depth_plan({"loop_abstracts": 120}, "raised")
        self.assertEqual(lineage.population_query(self.store, ["bounded"], limit=101)["matches"], [])


class DepthDependencyTests(DevelopmentCase):
    def test_a_depth_change_preserves_current_synthesis_assessment_and_candidate(self):
        self.prepared_candidate()
        before = self.api().synthesis_report(self.store, "research")
        candidate = self.candidate()
        self.assertIn("depth", cli.OPERATIONS)
        self.mutate(cli.OPERATIONS["depth"], {"id": "deeper", "values": {key: value * 2 for key, value in DEFAULTS.items()},
                                             "reason": "Deepen future tool work."})
        after = self.api().synthesis_report(self.store, "research")
        self.assertEqual(after["configuration"]["digest"], before["configuration"]["digest"])
        self.assertEqual(after["preparation_digest"], before["preparation_digest"])
        self.assertEqual(after["sections"], before["sections"])
        self.assertEqual(self.candidate(), candidate)
        self.assertNotIn("development_dependencies_stale", self.readiness_codes())


class LegacyDepthReportTests(SynthesisCase):
    """Whole reports frozen from 0.50.1 HEAD, with legacy deficits and a recorded verification sample."""

    def compare_reports(self, profile):
        from constitution_fixtures import distribute_version_six
        from research_harness import principles, sampling, synthesis
        from research_harness.evaluation import Evaluation
        from research_harness.literature import foundation_state
        # The legacy reports of 0.50.1 were recorded under Version 6.
        with distribute_version_six():
            work = self.metadata()
            target = {"kind": "objective", "id": "full-objective", "statement": "Establish finite bounds for all bounded input sequences."}
            if profile == "verification":
                capture = self.capture(work)
                target = {"kind": "work", "id": work, "source_id": capture["source_id"], "sha256": capture["original"]["sha256"]}
            self.mutate(principles.initialize_research, {"profile": profile, "target": target,
                        "preparation_policy": "lineage-v1" if profile == "research" else "sampled-v1"})
            self.scope([work], profile=profile, **({"target": target} if profile == "verification" else {}))
            link = self.read_source(2)
            if profile == "verification":
                collection = self.cohort((1, 2))
                self.mutate(sampling.record_sample, {"id": "legacy-sample", "collection_id": collection,
                            "size": 1, "seed": "legacy-seed"})
            records = self.store.snapshot()["records"]
            # Model only the record shapes the old writer produced. The four expected reports were
            # exported by the same authored setup running the untouched 0.50.1 implementation.
            for sample in records.get("cohort_sample", {}).values():
                sample.pop("depth", None)
            values = [("standards", self.standards(link, profile=profile))]
            if profile == "research":
                values.append(("innovation", self.innovation([self.case(link, 1)])))
            for kind, payload in values:
                evaluation = Evaluation(records, self.artifacts)
                configuration = principles.configuration_state(records, evaluation, profile)
                foundation = foundation_state(records, evaluation, profile)
                assessment = synthesis._assess(records, evaluation, kind, payload, configuration, foundation)
                records.setdefault("synthesis", {})[payload["id"]] = {"id": payload["id"], "kind": kind,
                    "profile": profile, "payload": payload, "dependencies": assessment["dependencies"], "assessment": assessment}
                records.setdefault("synthesis_selection", {})[profile + ":" + kind] = {"id": payload["id"]}
        # The current distribution is Version 7; the store that adopted Version 6 keeps its reports.
        self.assertEqual(principles.constitution_contract()["version"], "7")
        reports = {"foundation": foundation_state(records, Evaluation(records, self.artifacts), profile),
                   "synthesis": synthesis.synthesis_state(records, self.artifacts, profile)}
        for name, report in reports.items():
            with self.subTest(profile=profile, report=name):
                fixture = Path(__file__).parent / "fixtures" / "research" / ("legacy-0.50.1-" + profile + "-" + name + ".json")
                self.assertEqual(report, json.loads(fixture.read_text(encoding="utf-8")))

    def test_research_foundation_and_synthesis_are_unchanged(self):
        self.compare_reports("research")

    def test_verification_foundation_and_synthesis_are_unchanged(self):
        self.compare_reports("verification")
