"""One snapshot is evaluated once; bytes are verified once; results stay identical."""

import weakref

from literature_fixtures import LiteratureCase


class EvaluationTests(LiteratureCase):
    def test_historical_evaluations_are_shared_by_revision_and_keep_their_bounds(self):
        from research_harness.evaluation import Evaluation
        self.store.mutate("fixture.managed", {}, lambda tx: (
            tx.put("configuration", "research", {"profile": "research"}),
            tx.put("workspace", "study", {"stage": "evaluate"})),
            expected_revision=self.store.revision, request_id="managed")
        first_revision = self.store.revision
        self.store.mutate("fixture.later", {}, lambda tx: tx.put("fixture", "later", {}),
                          expected_revision=first_revision, request_id="later")
        records = self.store.snapshot()["records"]
        root = Evaluation(records, self.artifacts)
        first = root.read_snapshot(first_revision)
        self.assertIsInstance(first, Evaluation)
        second = root.read_snapshot(first_revision + 1)
        self.assertIs(first, root.read_snapshot(first_revision))
        self.assertIs(first, first.read_snapshot(first_revision))
        self.assertIs(Evaluation.of(first.records, root), first)
        self.assertIs(second, Evaluation.of(second.records, first))
        self.assert_error("claim_reassessment_required", lambda: first.read_snapshot(first_revision + 1))
        fresh = Evaluation(records, root)
        self.assertIsNot(fresh.read_snapshot(first_revision), first)
        self.assertEqual(first.compute_once_in_root("proof", lambda: "first"), "first")
        self.assertEqual(first.compute_once_in_root("proof", lambda: "repeated"), "first")
        self.assertEqual(second.compute_once_in_root("proof", lambda: "second"), "second")
        reference = self.artifacts.put(b"shared bytes", "text/plain")
        self.assertEqual(first.read(reference), b"shared bytes")
        self.assertIs(root.read(reference), first.read(reference))
        self.assertIs(second.text(reference), root.text(reference))
        self.assertEqual((first.counters["artifacts_verified"], root.counters["artifacts_verified"]), (1, 0))
        records["fixture"]["later"] = {"uncommitted": True}
        self.assertEqual(fresh.read_snapshot(first_revision + 1).records["fixture"]["later"], {})
        retained = weakref.ref(root)
        del root
        self.assertIsNone(retained())
        self.assert_error("claim_reassessment_required", lambda: Evaluation({}, self.artifacts).read_snapshot(0))

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

    def test_a_malformed_artifact_reference_fails_typed_instead_of_crashing(self):
        from research_harness.errors import ResearchError
        from research_harness.evaluation import Evaluation
        evaluation = Evaluation({}, self.artifacts)
        reference = self.artifacts.put(b"Saved bytes.", "text/plain")
        self.assertEqual(evaluation.read(reference), b"Saved bytes.")
        for field, value in (("sha256", [reference["sha256"]]), ("size", {"bytes": 12}), ("path", [reference["path"]]),
                             ("media_type", ["text/plain"])):
            with self.subTest(field=field):
                with self.assertRaises(ResearchError):
                    evaluation.read(dict(reference, **{field: value}))

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
        self.mutate(initialize_research, {"profile": "research", "target": None, "preparation_policy": "exhaustive-v1"})
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
