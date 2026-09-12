"""One snapshot is evaluated once; bytes are verified once; results stay identical."""

from literature_fixtures import LiteratureCase


class EvaluationTests(LiteratureCase):
    def test_bytes_are_verified_once_per_evaluation_and_tampering_is_still_caught(self):
        from research_harness.evaluation import Evaluation
        from research_harness.reading import record_reading
        a = self.metadata()
        self.mutate(record_reading, self.abstract_note(a))
        records = self.store.snapshot()["records"]
        evaluation = Evaluation(records, self.artifacts)
        reference = records["work"][a]["abstract"]
        self.assertEqual(evaluation.read(reference), evaluation.read(reference))
        self.assertEqual(evaluation.counters["artifacts_verified"], 1)
        self.assertEqual(evaluation.counters["reads"], 2)
        self.assertEqual(evaluation.text(reference), evaluation.read(reference).decode())
        path = self.root / reference["path"]
        path.chmod(0o600)
        path.write_bytes(b"changed")
        self.assert_error("artifact_corrupt", lambda: Evaluation(records, self.artifacts).read(reference))

    def test_of_shares_memo_for_the_same_records_and_not_for_others(self):
        from research_harness.evaluation import Evaluation
        records = self.store.snapshot()["records"]
        evaluation = Evaluation(records, self.artifacts)
        self.assertIs(Evaluation.of(records, evaluation), evaluation)
        self.assertIsNot(Evaluation.of(self.store.snapshot()["records"], evaluation), evaluation)
        self.assertEqual(evaluation.once("k", lambda: 1), 1)
        self.assertEqual(evaluation.once("k", lambda: 2), 1)
        self.assertEqual(evaluation.counters["computed"], 1)

    def test_status_computes_the_cohort_report_and_graph_once(self):
        from research_harness.cli import status_report
        from research_harness.principles import initialize_research
        from research_harness.reading import record_reading
        self.mutate(initialize_research, {"profile": "research", "target": None})
        collection = self.cohort((1, 2))
        self.mutate(record_reading, self.abstract_note("arxiv:2601.00001v1", "n1"))
        self.scope(["arxiv:2601.00001v1"], [collection])
        report = status_report(self.store, counters=True)
        self.assertEqual(report["evaluation"]["cohort_reports"], 1)
        self.assertEqual(report["evaluation"]["graph_builds"], 1)
        self.assertEqual(report["preparation"]["counts"]["obligations"], 0 if report["preparation"]["ready"] else report["preparation"]["counts"]["obligations"])
        self.assertLessEqual(report["evaluation"]["readings_assessed"], 1)

    def test_reports_are_identical_between_fresh_and_shared_evaluations(self):
        from research_harness.evaluation import Evaluation
        from research_harness.literature import foundation_state
        from research_harness.reading import record_reading
        a = self.metadata()
        self.mutate(record_reading, self.abstract_note(a))
        self.scope([a])
        records = self.store.snapshot()["records"]
        shared = Evaluation(records, self.artifacts)
        first = foundation_state(records, shared, "research")
        second = foundation_state(records, shared, "research")
        fresh = foundation_state(records, self.artifacts, "research")
        self.assertIs(first, second)
        self.assertEqual(first, fresh)
        self.assertEqual(shared.counters["graph_builds"], 1)
