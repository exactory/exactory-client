"""Optional retention is bounded across artifacts, reports and historical views."""

import weakref

from literature_fixtures import LiteratureCase
from research_harness.errors import ResearchError
from research_harness.evaluation import Evaluation
from research_harness.source_links import read_locator


class EvaluationMemoryBudgetTests(LiteratureCase):
    def test_payload_eviction_reverifies_and_catches_tampering(self):
        evaluation = Evaluation({}, self.artifacts, retention_budget_bytes=12000)
        references = [self.artifacts.put(bytes([n]) * 8000, 'application/octet-stream') for n in range(5)]
        for reference in references:
            self.assertEqual(evaluation.read(reference), bytes([references.index(reference)]) * 8000)
            self.assertLessEqual(evaluation.retention_report()['optional_bytes'], 12000)
        self.assertEqual(evaluation.read(references[0]), b'\0' * 8000)
        self.assertEqual(evaluation.counters['artifacts_verified'], 6)
        path = self.root / references[-1]['path']
        path.chmod(0o600)
        path.write_bytes(b'changed')
        with self.assertRaises(ResearchError) as error:
            evaluation.read(references[-1])
        self.assertEqual(error.exception.code, 'artifact_corrupt')

    def test_lru_hits_keep_recent_payloads_and_oversized_values_are_not_retained(self):
        evaluation = Evaluation({}, self.artifacts, retention_budget_bytes=15000)
        first, second, third = [self.artifacts.put(bytes([n]) * 5000, 'application/octet-stream') for n in range(3)]
        evaluation.read(first)
        evaluation.read(second)
        evaluation.read(first)
        evaluation.read(third)
        evaluation.read(first)
        self.assertEqual(evaluation.counters['artifacts_verified'], 3)
        evaluation.read(second)
        self.assertEqual(evaluation.counters['artifacts_verified'], 4)
        huge = self.artifacts.put(b'x' * 20000, 'text/plain')
        evaluation.text(huge)
        evaluation.text(huge)
        self.assertEqual(evaluation.counters['artifacts_verified'], 6)
        self.assertLessEqual(evaluation.retention_report()['optional_bytes'], 15000)

    def test_zero_budget_releases_bytes_text_parsed_json_links_and_reports(self):
        evaluation = Evaluation({}, self.artifacts, retention_budget_bytes=0)
        reference = self.artifacts.put(b'{"value":{"large":"payload"}}', 'application/json')
        locator = {'kind': 'json', 'pointer': '/value', 'value': {'large': 'payload'}}
        for _ in range(2):
            self.assertEqual(evaluation.text(reference), '{"value":{"large":"payload"}}')
            self.assertEqual(read_locator(evaluation, reference, locator), {'large': 'payload'})
            self.assertEqual(evaluation.once(('report',), lambda: {'payload': ['large']}), {'payload': ['large']})
        self.assertEqual(evaluation.counters['artifacts_verified'], 4)
        self.assertEqual(evaluation.counters['computed'], 4)
        self.assertEqual(evaluation.retention_report()['optional_bytes'], 0)
        identifier = self.metadata()
        records = self.store.snapshot()['records']
        evaluation = Evaluation(records, self.artifacts, retention_budget_bytes=0)
        abstract = records['work'][identifier]['abstracts'][0]
        link = self.link(identifier, abstract['source_id'], abstract['artifact'])
        first = evaluation.link(link)
        self.assertEqual(first['value'], self.artifacts.read(abstract['artifact']).decode())
        first['value'] = 'substituted'
        self.assertEqual(evaluation.link(link)['value'], self.artifacts.read(abstract['artifact']).decode())
        self.assertEqual(evaluation.counters['links_validated'], 2)
        self.assertEqual(evaluation.retention_report()['optional_bytes'], 0)

    def test_budget_includes_container_overhead_and_structures_are_reported_separately(self):
        evaluation = Evaluation({}, self.artifacts, retention_budget_bytes=15000)
        for index in range(10):
            evaluation.once(('containers', index), lambda: [{} for _ in range(1000)])
        self.assertEqual(evaluation.retention_report()['optional_bytes'], 0)
        evaluation.readings_for('none')
        self.assertGreater(evaluation.retention_report()['structural_bytes'], 0)

    def test_new_snapshot_explicit_constructor_and_historical_children_inherit_policy(self):
        self.store.mutate('fixture', {}, lambda tx: (tx.put('configuration', 'research', {'profile': 'research'}),
                                                 tx.put('workspace', 'study', {'stage': 'evaluate'})),
                          expected_revision=0, request_id='one')
        self.store.mutate('fixture', {}, lambda tx: tx.put('fixture', 'later', {}),
                          expected_revision=1, request_id='two')
        records = self.store.snapshot()['records']
        root = Evaluation(records, self.artifacts, retention_budget_bytes=0)
        reference = self.artifacts.put(b'body', 'text/plain')
        for child in (Evaluation({}, root), Evaluation.of({}, root), root.read_snapshot(1)):
            self.assertEqual(child.retention_report()['budget_bytes'], 0)
            child.text(reference)
            child.text(reference)
            self.assertEqual(child.counters['artifacts_verified'], 2)
        self.assertEqual(root.retention_report()['optional_bytes'], 0)

    def test_historical_children_share_budget_and_evicted_children_are_rebuilt(self):
        self.store.mutate('fixture', {}, lambda tx: (tx.put('configuration', 'research', {'profile': 'research'}),
                                                 tx.put('workspace', 'study', {'stage': 'evaluate'})),
                          expected_revision=0, request_id='one')
        for revision in range(1, 8):
            self.store.mutate('fixture', {}, lambda tx: tx.put('fixture', str(revision), {}),
                              expected_revision=revision, request_id=str(revision + 1))
        root = Evaluation(self.store.snapshot()['records'], self.artifacts, retention_budget_bytes=5000)
        first = root.read_snapshot(1)
        for revision in range(2, 9):
            child = root.read_snapshot(revision)
            child.once('large-report', lambda: {'values': list(range(100))})
        self.assertLessEqual(root.retention_report()['optional_bytes'], 5000)
        self.assertEqual(first.records.read_revision(), 1)
        self.assertEqual(first.retention_report()['budget_bytes'], 5000)
        retained = weakref.ref(first)
        del first
        self.assertIsNone(retained())
        self.assertEqual(root.read_snapshot(1).records.read_revision(), 1)

    def test_inactive_historical_views_reuse_science_while_within_budget(self):
        self.store.mutate('fixture', {}, lambda tx: (
            tx.put('configuration', 'research', {'profile': 'research'}),
            tx.put('workspace', 'study', {'stage': 'evaluate'})), expected_revision=0, request_id='one')
        root = Evaluation(self.store.snapshot()['records'], self.artifacts, retention_budget_bytes=100000)
        first = root.read_snapshot(1)
        self.assertEqual(first.compute_once_in_root('science', lambda: 'original'), 'original')
        del first
        again = root.read_snapshot(1)
        self.assertEqual(again.compute_once_in_root('science', lambda: 'recomputed'), 'original')

    def test_selecting_another_revision_evicts_decodes_without_reading_it(self):
        from research_harness import storage
        from unittest.mock import patch
        self.store.mutate('fixture', {}, lambda tx: (
            tx.put('configuration', 'research', {'profile': 'research'}),
            tx.put('workspace', 'study', {'stage': 'evaluate'}),
            tx.put('fixture', 'one', {'value': 0})), expected_revision=0, request_id='one')
        for index in range(1, 3):
            self.store.mutate('fixture', {}, lambda tx, index=index: tx.put('fixture', 'one', {'value': index}),
                              expected_revision=index, request_id=str(index))
        evaluation = Evaluation(self.store.snapshot()['records'], self.artifacts)
        with patch.object(storage.zlib, 'decompress', wraps=storage.zlib.decompress) as decode:
            first = evaluation.read_snapshot(1).records
            self.assertEqual(first['fixture']['one']['value'], 0)
            self.assertEqual(decode.call_count, 1)
            evaluation.read_snapshot(2)
            self.assertEqual(first['fixture']['one']['value'], 0)
            self.assertEqual(decode.call_count, 2)

    def test_unchanged_historical_rows_borrow_authenticated_current_values(self):
        self.store.mutate('fixture', {}, lambda tx: (
            tx.put('configuration', 'research', {'profile': 'research'}),
            tx.put('workspace', 'study', {'stage': 'evaluate'}),
            tx.put('fixture', 'row', {'payload': 'shared current value'})), expected_revision=0, request_id='one')
        records = self.store.snapshot()['records']
        root = Evaluation(records, self.artifacts)
        old = root.read_snapshot(1)
        self.assertIs(old.records['fixture']['row'], records['fixture']['row'])
        records['fixture']['row'] = {'payload': 'uncommitted substitution'}
        fresh = Evaluation(records, self.artifacts)
        self.assertEqual(fresh.read_snapshot(1).records['fixture']['row']['payload'], 'shared current value')

    def test_cached_origin_evaluations_share_one_optional_budget(self):
        root = Evaluation({}, self.artifacts, retention_budget_bytes=20000)
        references = [self.artifacts.put(bytes([index]) * 10000, 'application/octet-stream')
                      for index in range(5)]
        for index, reference in enumerate(references):
            origin = root.once(('origin', index), lambda: Evaluation.of({}, root))
            origin.read(reference)
            self.assertEqual(origin.retention_report()['optional_bytes'], root.retention_report()['optional_bytes'])
            self.assertLessEqual(root.retention_report()['optional_bytes'], 20000)
        root.read(references[0])
        self.assertEqual(root.counters['artifacts_verified'], 1)

    def test_zero_budget_historical_overlay_does_not_fill_the_parent_json_cache(self):
        self.store.mutate('fixture', {}, lambda tx: (
            tx.put('configuration', 'research', {'profile': 'research'}),
            tx.put('workspace', 'study', {'stage': 'evaluate'}),
            tx.put('fixture', 'row', {'payload': 'old' * 10000})), expected_revision=0, request_id='one')
        self.store.mutate('fixture', {}, lambda tx: tx.put('fixture', 'row', {'payload': 'new'}),
                          expected_revision=1, request_id='two')
        root = Evaluation(self.store.snapshot()['records'], self.artifacts, retention_budget_bytes=200000)
        old = root.read_snapshot(1)
        overlay = old.records.copy()
        overlay['replacement'] = {'row': {'payload': 'authored'}}
        zero = Evaluation(overlay, old, retention_budget_bytes=0)
        before = root.retention_report()['optional_bytes']
        for _ in range(2):
            self.assertEqual(zero.records['fixture']['row']['payload'], 'old' * 10000)
            self.assertEqual(zero.records['replacement']['row']['payload'], 'authored')
        self.assertEqual(zero.retention_report()['optional_bytes'], 0)
        self.assertEqual(root.retention_report()['optional_bytes'], before)

    def test_explicit_budget_on_of_overrides_inherited_policy(self):
        root = Evaluation({}, self.artifacts, retention_budget_bytes=1000)
        child = Evaluation.of(root.records, root, retention_budget_bytes=0)
        reference = self.artifacts.put(b'body', 'text/plain')
        child.read(reference)
        child.read(reference)
        self.assertEqual(child.counters['artifacts_verified'], 2)
        self.assertEqual(root.retention_report()['budget_bytes'], 1000)
