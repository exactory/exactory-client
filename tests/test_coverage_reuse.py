"""Coverage reuse preserves exact originals, cited readings and report bytes."""

import copy
from unittest.mock import patch

from literature_fixtures import LiteratureCase
from test_research_problem_delivery import ProblemDeliveryCase
from research_harness import graph, problem_delivery, problem_evidence, reading
from research_harness.evaluation import Evaluation
from research_harness.evidence import digest
from research_harness.literature import foundation_state, import_bundle


class CountedBundles(dict):
    def __init__(self, rows):
        super().__init__(rows)
        self.visits = 0

    def values(self):
        for row in super().values():
            self.visits += 1
            yield row


class CountedUnits(list):
    def __init__(self, rows):
        super().__init__(rows)
        self.visits = 0

    def __iter__(self):
        for row in super().__iter__():
            self.visits += 1
            yield row


class CoverageReuseTests(LiteratureCase):
    def prepare_counted_units(self):
        bundles = {identifier: {"id": identifier, "version_id": version, "original_sha256": "original",
            "source_id": identifier, "sequence": sequence,
            "units": CountedUnits([{"kind": "text", "link": None}])}
            for identifier, version, sequence in (("first", "main", 1), ("latest", "main", 2), ("other", "other", 3))}
        return {"source_bundle": bundles}

    def test_unindexed_selection_does_not_traverse_units(self):
        records = self.prepare_counted_units()
        selected = graph.selected_bundle(records, "main", {"id": "main", "sha256": "original"})
        self.assertEqual(selected["id"], "latest")
        self.assertEqual(sum(bundle["units"].visits for bundle in records["source_bundle"].values()), 0)

    def test_unindexed_captures_do_not_traverse_unrelated_version_units(self):
        records = self.prepare_counted_units()
        work = {"id": "main", "fulltexts": [{"source_id": "first", "availability": "available",
                                               "original": {"sha256": "original"}}]}
        self.assertEqual(graph.main_captures(records, work), work["fulltexts"])
        self.assertEqual(records["source_bundle"]["other"]["units"].visits, 0)

    def prepare_readings(self):
        version = self.metadata()
        first = self.bundle(version, bundle_id="first")
        self.mutate(import_bundle, first)
        self.mutate(reading.record_reading, self.full_note(first, "read-first"))
        second = self.bundle(version, self.capture(version,
            body="A second original gives a different bounded example. References: none."), "second")
        self.mutate(import_bundle, second)
        self.mutate(reading.record_reading, self.full_note(second, "read-second"))
        self.mutate(reading.record_reading, self.full_note(second, "read-partial", omit=("bibliography",)))
        self.scope([version])
        return version, first, second

    def test_graph_checks_many_originals_with_one_bundle_inventory(self):
        works, bundles = {}, CountedBundles({})
        for work_index in range(8):
            version = "arxiv:2601.%05dv1" % (work_index + 1)
            captures = []
            for original_index in range(8):
                sha = str(original_index)
                source = version + ":" + sha
                captures.append({"source_id": source, "availability": "available", "original": {"sha256": sha}})
                for label in ("first", "equal", "latest"):
                    identifier = source + ":" + label
                    bundles[identifier] = {"id": identifier, "version_id": version, "source_id": source,
                        "original_sha256": sha, "sequence": 2 if label == "latest" else 1,
                        "units": [], "scope": "article", "bibliography": {"complete": label == "first"}}
            works[version] = {"id": version, "work_id": version, "fulltexts": captures}
        records = {"work": works, "source_bundle": bundles,
                   "bundle_selection": {version: {"bundle_id": version + ":0:first"} for version in works},
                   "literature_scope": {"research": {"roots": list(works)}}}
        report = graph.citation_graph(records, "research")
        self.assertEqual([(row["version_id"], row["source_id"]) for row in report["obligations"]],
                         [(version, version + ":" + str(index)) for version in sorted(works) for index in range(1, 8)])
        self.assertEqual(bundles.visits, len(bundles))

    def test_identical_coverage_requests_reuse_one_assessment(self):
        version, _, _ = self.prepare_readings()
        records = self.store.snapshot()["records"]
        evaluation = Evaluation(records, self.artifacts)
        with patch.object(reading, "main_captures", wraps=reading.main_captures) as captures:
            results = [reading.fulltext_coverage(records, evaluation, version) for _ in range(4)]
        self.assertTrue(all(result["complete"] for result in results))
        self.assertEqual(set(results[0]["readings"]), {"read-first", "read-second"})
        self.assertEqual(captures.call_count, 1)
        self.assertEqual(evaluation.counters["readings_assessed"], 3)

    def test_identical_current_readings_reuse_selection_and_unit_checks(self):
        version, _, second = self.prepare_readings()
        records = self.store.snapshot()["records"]
        target = {"id": version, "sha256": records["source_bundle"][second["id"]]["original_sha256"]}
        evaluation = Evaluation(records, self.artifacts)
        with patch.object(reading, "selected_bundle", wraps=reading.selected_bundle) as select:
            for _ in range(4):
                accepted, partial = reading.current_readings(records, evaluation, version, target=target)
                self.assertEqual([row["id"] for row in accepted], ["read-second"])
                self.assertEqual([row["id"] for row in partial], ["read-first", "read-partial"])
        self.assertEqual(select.call_count, 1)

    def test_coverage_shares_one_bundle_inventory_for_all_originals(self):
        version, _, _ = self.prepare_readings()
        records = self.store.snapshot()["records"]
        bundles = records["source_bundle"] = CountedBundles(records["source_bundle"])
        evaluation = Evaluation(records, self.artifacts)
        target = {"id": version, "sha256": bundles["first"]["original_sha256"]}
        for target in (None, target):
            self.assertTrue(reading.fulltext_coverage(records, evaluation, version, target=target)["complete"])
        self.assertEqual(bundles.visits, len(bundles))

    def test_cited_reading_reuses_the_exact_evaluation_without_copying_records(self):
        version, _, _ = self.prepare_readings()
        records = self.store.snapshot()["records"]
        evaluation = Evaluation(records, self.artifacts)
        seen = []
        assess = reading._assess

        def assess_reading(view, artifacts, value):
            seen.append((view, artifacts))
            return assess(view, artifacts, value)

        with patch.object(reading, "_assess", side_effect=assess_reading):
            for _ in range(2):
                result = problem_evidence.validate_reading(records, evaluation,
                    {"kind": "reading", "version_id": version, "reading_id": "read-first"})
                self.assertEqual(result["id"], "read-first")
        self.assertTrue(seen)
        self.assertTrue(all(view is records and artifacts is evaluation for view, artifacts in seen))
        self.assertEqual(evaluation.counters["readings_assessed"], 3)

    def test_target_and_eligible_reading_sets_are_isolated_in_a_warm_evaluation(self):
        version, _, _ = self.prepare_readings()
        records = self.store.snapshot()["records"]
        evaluation = Evaluation(records, self.artifacts)
        pins = {name: {"id": version, "sha256": records["source_bundle"][name]["original_sha256"]}
                for name in ("first", "second")}
        cases = [(None, None, True, {"read-first", "read-second"}),
                 (pins["first"], None, True, {"read-first"}),
                 (pins["second"], {"read-partial"}, False, set()),
                 (pins["second"], {"read-second"}, True, {"read-second"}),
                 (pins["first"], {"read-second"}, False, set()),
                 (pins["second"], set(), False, set()),
                 (None, {"read-first"}, False, {"read-first"}),
                 (pins["first"], {"read-first", "read-partial"}, True, {"read-first"})]
        for target, eligible, complete, identifiers in cases + list(reversed(cases)):
            with self.subTest(target=target, eligible=eligible):
                result = reading.fulltext_coverage(records, evaluation, version, target=target,
                                                   eligible_reading_ids=eligible)
                self.assertEqual(result["complete"], complete)
                self.assertEqual(set(result["readings"]), identifiers)
        for eligible in ({"read-partial"}, set()):
            self.assert_error("reading_missing", lambda: reading.validate_read_evidence(records, evaluation,
                records["reading"]["read-second"]["inspections"][0]["link"], target=pins["second"],
                eligible_reading_ids=eligible))

    def test_complete_unrelated_reading_cannot_satisfy_a_cited_incomplete_reading(self):
        version, _, _ = self.prepare_readings()
        records = self.store.snapshot()["records"]
        evaluation = Evaluation(records, self.artifacts)
        self.assertTrue(reading.fulltext_coverage(records, evaluation, version)["complete"])
        self.assert_error("problem_value_reading_missing", lambda: problem_evidence.validate_reading(records, evaluation,
            {"kind": "reading", "version_id": version, "reading_id": "read-partial"}))
        self.assertEqual(problem_evidence.validate_reading(records, evaluation,
            {"kind": "reading", "version_id": version, "reading_id": "read-second"})["id"], "read-second")

    def test_source_and_selection_changes_get_fresh_evaluations(self):
        version, first, _ = self.prepare_readings()
        records = self.store.snapshot()["records"]
        evaluation = Evaluation(records, self.artifacts)
        self.assertTrue(reading.fulltext_coverage(records, evaluation, version)["complete"])
        later = copy.deepcopy(first)
        later["id"] = "later"
        later["units"].append({"id": "supplement", "kind": "supplement", "required": True,
            "link": None, "reason": "A proof supplement is required.", "url": "https://example.org/proof"})
        self.mutate(import_bundle, later)
        selected = self.store.snapshot()["records"]
        self.assertIsNot(Evaluation.of(selected, evaluation), evaluation)
        result = reading.fulltext_coverage(selected, evaluation, version)
        self.assertFalse(result["complete"])
        self.assertEqual(result["missing"][0]["bundle_id"], "later")
        self.assertTrue(reading.fulltext_coverage(records, evaluation, version)["complete"])
        self.capture(version, body="A third acquired original requires its own reading. References: none.")
        captured = self.store.snapshot()["records"]
        result = reading.fulltext_coverage(captured, evaluation, version)
        self.assertFalse(result["complete"])
        self.assertEqual(len(result["missing"]), 2)

    def test_zero_retention_recomputes_the_same_coverage(self):
        version, _, _ = self.prepare_readings()
        records = self.store.snapshot()["records"]
        evaluation = Evaluation(records, self.artifacts, retention_budget_bytes=0)
        first = reading.fulltext_coverage(records, evaluation, version)
        second = reading.fulltext_coverage(records, evaluation, version)
        self.assertEqual(first, second)
        self.assertEqual(evaluation.counters["readings_assessed"], 12)
        self.assertEqual(evaluation.retention_report()["optional_bytes"], 0)

    def test_coverage_and_foundation_report_bytes_match_the_unchanged_base(self):
        version, _, _ = self.prepare_readings()
        records = self.store.snapshot()["records"]
        evaluation = Evaluation(records, self.artifacts)
        reports = {"graph": graph.citation_graph(records, "research"),
                   "foundation": foundation_state(records, evaluation, "research"),
                   "coverage": reading.fulltext_coverage(records, evaluation, version),
                   "current": reading.current_readings(records, evaluation, version)}
        # Captured on the unchanged D4-1 base, including obligation order and selected readings.
        self.assertEqual(digest(reports), "eaeeeb686deb966cd85401c1bd79b350264b14bbe104ef89a974f9bb4ff7d848")


class PostAssessmentReuseTests(ProblemDeliveryCase):
    def test_post_minimum_and_milestone_share_attempt_and_openness_assessments(self):
        self.prepare_ready()
        records = self.store.snapshot()["records"]
        with patch.object(problem_delivery, "assess_attempt", wraps=problem_delivery.assess_attempt) as attempt, \
                patch.object(problem_delivery, "assess_openness", wraps=problem_delivery.assess_openness) as openness:
            report = problem_delivery.assess_post(records, self.artifacts)
        self.assertEqual(report["milestone"], "openness")
        self.assertEqual([row["code"] for row in report["obligations"]], ["problem_decision_missing"])
        self.assertEqual(attempt.call_count, 1)
        self.assertEqual(openness.call_count, 1)

    def test_post_report_and_obligation_bytes_match_the_unchanged_base(self):
        self.initialize_problem()
        self.put_selecting_slate()
        self.select_statement(candidate_id="candidate-1")
        records = self.store.snapshot()["records"]
        evaluation = Evaluation(records, self.artifacts)
        reports = {"post": problem_delivery.assess_post(records, evaluation),
                   "minimum": problem_delivery.assess_minimum(records, evaluation)}
        self.assertEqual(digest(reports), "9e319475473ac79e5c494ba418440821279a4698fe1ca11a07fd6e7922f0ceed")
