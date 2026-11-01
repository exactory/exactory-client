"""Scientific acceptance binds its dependency frontier, including context discoveries."""

import copy
import json
import sys
import tempfile
from pathlib import Path
import unittest
from unittest.mock import patch

from research_harness import review_staged as staged, review_staged_protocol as producer
from research_harness.errors import ResearchError
from research_harness.operations import prepared_task_mutation
from research_harness.storage import Store
import test_review_staged_contexts as fixtures
import test_review_staged_frontier as frontier
from research_harness import review_worker


class ScopedAcceptanceTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.StagedContextTests()
        self.addCleanup(self.fixture.doCleanups)
        self.fixture.setUp()
        self.epoch = self.fixture.epoch
        self.artifacts = self.fixture.artifacts
        self.records = self.fixture.records
        self.records['review_task_acceptance_index'] = {
            self.fixture.discovery['task_id']: dict(reservation_id='discovery')}
        self.records['review_epoch_discoveries'] = {
            self.epoch['id']: dict(acceptance_ids=['discovery'])}
        self.planned = self.fixture.plan(self.fixture.expanded_task())
        self.card = copy.deepcopy(self.fixture.discovery['output']['cards'][0])
        for cell in self.card['judgments']:
            cell['context_root'] = self.fixture.contexts[cell['claim_id']]['root']
        self.captured = dict(reservation_id='current', recorded_revision=2,
            observation=dict(status='completed', request=self.planned['request'],
                output=producer.protocol._put_json(self.artifacts, dict(cards=[self.card]))))
        self.assignment = dict(id='reader', staged_task=self.planned['task'], packet_digest='packet',
            prompt_hash='prompt', staged_review=dict(kind='task', epoch_id=self.epoch['id']))
        self.create_store()

    def create_store(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.store = Store(Path(temporary.name), create=True)
        self.store.mutate('fixture', {}, lambda tx: [tx.put(kind, key, row)
            for kind, rows in self.records.items() for key, row in rows.items()],
            expected_revision=0, request_id='fixture')

    def write(self, kind, key, value):
        self.store.mutate('sibling', {}, lambda tx: tx.put(kind, key, value),
            expected_revision=self.store.revision, request_id='sibling')

    def accept_during(self, update):
        validate = staged._validate_observation

        def validate_and_commit(*args, **kwargs):
            result = validate(*args, **kwargs)
            update()
            return result

        def prepare(records, value):
            receipt = producer.accept_capture(records, self.artifacts, self.assignment, self.captured, 'attempt')
            return [('receipt', 'current', receipt)], receipt

        with patch.object(producer, 'matches_currency', return_value=True), \
                patch.object(staged, '_validate_observation', side_effect=validate_and_commit):
            return prepared_task_mutation(self.store, 'accept', {}, prepare, request_id='accept')

    def test_sibling_acceptance_does_not_invalidate_prepared_dependency_frontier(self):
        result = self.accept_during(lambda: self.write('review_task_acceptance', 'sibling',
            dict(task_id='unrelated', receipt={})))
        self.assertEqual(result['revision'], 3)
        self.assertEqual(result['result']['staged_observation']['dependencies'], {
            self.fixture.discovery['task_id']:self.fixture.discovery['raw_output_digest']})

    def test_changing_the_selected_dependency_invalidates_prepared_acceptance(self):
        with self.assertRaises(ResearchError) as failure:
            self.accept_during(lambda: self.write('review_task_acceptance_index',
                self.fixture.discovery['task_id'], dict(reservation_id='replacement')))
        self.assertEqual(failure.exception.code, 'review_task_dependency_changed')
        self.assertNotIn('receipt', self.store.snapshot()['records'])

    def test_new_context_discovery_invalidates_prepared_acceptance(self):
        with self.assertRaises(ResearchError) as failure:
            self.accept_during(lambda: self.write('review_epoch_discoveries', self.epoch['id'],
                dict(acceptance_ids=['discovery', 'new-discovery'])))
        self.assertEqual(failure.exception.code, 'review_task_dependency_changed')

    def test_g2_task_head_resolves_an_accepted_dependency_without_scanning_siblings(self):
        self.records.pop('review_task_acceptance_index')
        self.records['review_task_execution'] = {self.fixture.discovery['task_id']:
            dict(state='accepted', reservation_id='discovery')}
        self.create_store()
        result = self.accept_during(lambda: self.write('review_task_acceptance', 'sibling',
            dict(task_id='unrelated', receipt={})))
        self.assertEqual(result['result']['attempt']['id'], 'attempt')

    def test_cold_legacy_collection_ignores_receipts_without_staged_observations(self):
        self.records.pop('review_epoch_discoveries')
        self.records['review_task_acceptance']['non-scientific'] = dict(task_id='generic',
            receipt=dict(staged_observation=None))
        observed, _, issues = producer.collect_observations(self.records, self.artifacts, self.epoch)
        self.assertEqual(observed, [self.fixture.discovery])
        self.assertEqual(issues, [])

    def test_empty_optional_retrievals_do_not_read_sibling_acceptances(self):
        self.captured['observation']['output'] = producer.protocol._put_json(self.artifacts,
            dict(cards=[self.card], retrievals=[]))
        result = self.accept_during(lambda: self.write('review_task_acceptance', 'sibling',
            dict(task_id='unrelated', receipt={})))
        self.assertEqual(result['result']['staged_observation']['output']['retrievals'], [])

    def test_pending_retrieval_reads_only_its_requesting_dependency_frontier(self):
        from test_review_staged_followups import request
        retrieval = request(self.planned['task'])
        self.captured['observation']['output'] = producer.protocol._put_json(self.artifacts,
            dict(cards=[self.card], retrievals=[retrieval]))
        result = self.accept_during(lambda: self.write('review_task_acceptance', 'sibling',
            dict(task_id='unrelated', receipt={})))
        self.assertEqual(result['result']['staged_observation']['output']['retrievals'], [retrieval])

    def test_a_falsy_malformed_retrieval_array_is_still_refused(self):
        for invalid in (None, False, '', {}):
            with self.subTest(invalid=invalid):
                self.captured['observation']['output'] = producer.protocol._put_json(self.artifacts,
                    dict(cards=[self.card], retrievals=invalid))
                with self.assertRaises(ResearchError) as failure:
                    self.accept_during(lambda:None)
                self.assertEqual(failure.exception.code, 'invalid_review_staged')


class EpochFinalAcceptanceTests(unittest.TestCase):
    def setUp(self):
        self.fixture = frontier.FrontierTests()
        self.addCleanup(self.fixture.doCleanups)
        self.fixture.setUp()
        controller = self.fixture.create_controller()
        output = self.fixture.case.build_review_output('result')
        output['value'].update(objective_adequacy='not_assessed', method_adequacy='not_assessed')
        (self.fixture.case.root / 'scientific.json').write_text(json.dumps(output))
        self.worker = patch.object(review_worker, '_entry_command', return_value=[sys.executable, '-c', frontier.WORKER])
        self.worker.start()
        self.addCleanup(self.worker.stop)
        for _ in range(4):
            planned = self.fixture.command('review-assignment', controller['next'][0]['payload'])
            self.fixture.command('review-run', planned['next'][0]['payload'])
        self.planned = self.fixture.command('review-assignment', controller['next'][0]['payload'])
        self.assertEqual(self.fixture.case.read_records()['review_task'][self.planned['task_ids'][0]]['task']['type'], 'final')

    def accept_during(self, update):
        accept = producer.accept_capture

        def accept_and_commit(*args, **kwargs):
            receipt = accept(*args, **kwargs)
            update()
            return receipt

        with patch.object(producer, 'accept_capture', side_effect=accept_and_commit):
            return self.fixture.command('review-run', self.planned['next'][0]['payload'])

    def test_terminal_validation_does_not_conflict_with_another_epochs_acceptance(self):
        result = self.accept_during(lambda: self.fixture.case.put('review_task_acceptance', 'other-epoch',
            dict(task_id='other-task', receipt=dict(staged_observation=dict(epoch_id='other-epoch')))))
        self.assertNotIn('refusal', result)
        self.assertTrue(any(row['receipt']['attempt']['id'] == result['id']
            for row in self.fixture.case.read_records()['review_task_acceptance'].values() if row['task_id'] != 'other-task'))

    def test_terminal_validation_rechecks_its_own_epoch_acceptance_selectors(self):
        records = self.fixture.case.read_records()
        task_id = next(row['task_id'] for row in records['review_task_acceptance'].values())
        with self.assertRaises(ResearchError) as failure:
            self.accept_during(lambda: self.fixture.case.put('review_task_acceptance_index', task_id,
                dict(reservation_id='changed-selection')))
        self.assertEqual(failure.exception.code, 'review_task_dependency_changed')
