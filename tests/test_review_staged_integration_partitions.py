"""Measured integration partitions retain every assigned full card and child root."""

import copy
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from research_harness.artifacts import ArtifactStore
from research_harness import review_protocol as protocol
from research_harness import operations
from research_harness import review_staged as staged
from research_harness import review_staged_protocol as producer
from research_harness.evidence import digest
from test_review_staged_fixtures import epoch, full_card, observation, profile, task


class IntegrationPartitionTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.artifacts = ArtifactStore(Path(temporary.name))
        self.epoch = epoch(count=4, route_id='route')
        self.cards = [full_card(self.epoch, index=i) for i in range(4)]
        for index, card in enumerate(self.cards):
            card['establishes'] = ('Card %d retains its original scientific conclusion. ' % index) * 100
        self.reading = task(self.epoch)
        self.observed = observation(self.epoch, self.reading, dict(cards=self.cards))
        self.route = dict(id='route', adapter='codex_cli_v1', model='fixture', endpoint='codex://local',
                          configuration=dict(max_output_tokens=10000, timeout_seconds=600))
        self.controller = dict(id=self.epoch['controller_assignment_id'], route_id='route',
            role='result', reviewer_id='reviewer-A', author_id='author', dossier_id=None,
            staged_review=dict(kind='controller', epoch_id=self.epoch['id']),
            packet=protocol._put_json(self.artifacts, {}))
        self.records = dict(review_route={'route':self.route}, review_epoch={self.epoch['id']:self.epoch},
            review_assignment={self.controller['id']:self.controller},
            review_task={}, review_task_acceptance={},
            review_slot_head={self.epoch['slot_id']:dict(slot_id=self.epoch['slot_id'],
                epoch_id=self.epoch['id'], state='pending', outcome=None)})
        self.add_observation(self.reading, self.observed, 'reader')
        active = patch.object(producer, 'authenticate_observation', return_value=True)
        active.start()
        self.addCleanup(active.stop)

    def add_observation(self, assigned, observed, identifier):
        child = dict(id=identifier, staged_task=assigned,
                     staged_review=dict(kind='task', epoch_id=self.epoch['id']))
        attempt = dict(id=observed['id'], assignment_id=identifier, recorded_revision=1)
        self.records['review_assignment'][identifier] = child
        self.records['review_task'][assigned['id']] = dict(epoch_id=self.epoch['id'], task=assigned)
        self.records['review_task_acceptance'][identifier] = dict(task_id=assigned['id'],
            receipt=dict(attempt=attempt, staged_observation=observed))

    def make_leaf(self, cards):
        return task(self.epoch, 'integrate', occurrence_ids=[], card_ids=[row['id'] for row in cards],
                    obligation_ids=staged.card_obligations(cards), dependencies=[self.reading['id']])

    def read_packet(self, planned):
        return protocol._read_json(self.artifacts, planned['packet'])['staged_review']

    def test_leaf_projection_contains_only_its_assigned_cards_and_obligations(self):
        assigned = self.make_leaf(self.cards[:1])
        packet = producer.build_packet(self.epoch, assigned, observations=[self.observed])['staged_review']
        self.assertEqual([row['id'] for row in packet['integrate']['full_cards']], assigned['card_ids'])
        self.assertEqual({row['id'] for row in packet['integrate']['conclusions']}, set(assigned['obligation_ids']))
        self.assertEqual([row['id'] for row in packet['original_check']['cards']], assigned['card_ids'])
        self.assertEqual(packet['integrate']['copy']['obligation_ids'], sorted(assigned['obligation_ids']))

    def test_actual_renderer_splits_large_integration_to_complete_card_leaves(self):
        one = producer.prepare_task_plan(self.records, self.artifacts, self.controller,
                                         self.epoch, self.make_leaf(self.cards[:1]))
        bound = producer.build_profiles(self.records, self.artifacts, self.route, self.epoch)['integrate']
        limit = max(one['task']['measurement']['input_characters'] + 300,
                    bound['context_characters'] + bound['envelope_characters'] + 1000)
        assigned = self.make_leaf(self.cards)
        with patch.object(producer, 'read_input_limit', return_value=limit):
            changes, plans, blocked = producer.prepare_partitions(self.records, self.artifacts,
                self.controller, self.epoch, assigned)
        self.assertIsNone(blocked)
        self.assertGreater(len(plans), 1)
        self.assertEqual(sorted(key for row in plans for key in row['task']['card_ids']),
                         sorted(assigned['card_ids']))
        self.assertEqual(sorted(key for row in plans for key in row['task']['obligation_ids']),
                         sorted(assigned['obligation_ids']))
        for planned in plans:
            self.assertEqual(len(planned['task']['card_ids']), 1)
            self.assertLessEqual(planned['task']['measurement']['input_characters'], limit)
            self.assertEqual([row['id'] for row in self.read_packet(planned)['integrate']['full_cards']],
                             planned['task']['card_ids'])
        self.assertTrue(any(kind == 'review_task_repair' for kind, _, _ in changes))

    def add_integration_roots(self):
        receipts, observed = [], []
        for index, card in enumerate(self.cards):
            assigned = self.make_leaf([card])
            receipt = staged.bind_integration_lists(dict(id='integration-' + str(index), child_ids=[]),
                epoch=self.epoch, task=assigned, cards=[card], observations=[self.observed])
            receipt.update(dispositions=[dict(id=key, disposition='retained', reason='The stated scope is retained. ' * 600,
                grounds=[key]) for key in receipt['obligation_ids']], findings=[], answers=[], selected_groups=[],
                retrievals=[], dependencies=[], unresolved=[], decision_constraints={})
            captured = observation(self.epoch, assigned, dict(integrations=[receipt]),
                                   dependencies={self.reading['id']:self.observed['raw_output_digest']})
            captured['actual_input']['full_card_roots'] = {card['id']:digest(card)}
            self.add_observation(assigned, captured, 'integrator-' + str(index))
            receipts.append(receipt)
            observed.append(captured)
        return receipts, observed

    def make_hierarchy(self, receipts, observations):
        return staged.make_task(self.epoch, 'integrate', child_ids=[row['id'] for row in receipts],
            dependencies=[row['task_id'] for row in observations], scope='segmented_original',
            measurement=staged.joint_fit([], 1000000, 10000, profile()))

    def test_hierarchy_packet_uses_child_receipts_without_full_card_replay(self):
        receipts, observations = self.add_integration_roots()
        assigned = self.make_hierarchy(receipts[:2], observations[:2])
        packet = producer.build_packet(self.epoch, assigned, observations=observations)['staged_review']
        self.assertEqual(packet['integrate']['full_cards'], [])
        self.assertEqual({row['id'] for row in packet['integrate']['receipts']}, set(assigned['child_ids']))
        self.assertEqual(packet['integrate']['copy']['child_union'],
                         sorted({key for row in receipts[:2] for key in row['obligation_ids']}))

    def test_hierarchy_splitting_partitions_child_ids_and_producer_dependencies(self):
        receipts, observations = self.add_integration_roots()
        assigned = self.make_hierarchy(receipts, observations)
        children = producer.split_task(self.epoch, assigned, cards=self.cards, observations=observations)
        self.assertEqual(len(children), 2)
        self.assertEqual(sorted(key for child in children for key in child['child_ids']), sorted(assigned['child_ids']))
        producers = {receipt['id']:receipt['task_id'] for receipt in receipts}
        for child in children:
            self.assertEqual(child['dependencies'], sorted(producers[key] for key in child['child_ids']))
            self.assertEqual(child['card_ids'], [])
            self.assertEqual(child['obligation_ids'], [])

    def test_actual_renderer_partitions_hierarchy_fanout(self):
        receipts, observations = self.add_integration_roots()
        single = producer.prepare_task_plan(self.records, self.artifacts, self.controller, self.epoch,
                                            self.make_hierarchy(receipts[:1], observations[:1]))
        limit = single['task']['measurement']['input_characters'] + 300
        assigned = self.make_hierarchy(receipts, observations)
        with patch.object(producer, 'read_input_limit', return_value=limit):
            _, plans, blocked = producer.prepare_partitions(self.records, self.artifacts,
                self.controller, self.epoch, assigned)
        self.assertIsNone(blocked)
        self.assertEqual(sorted(key for planned in plans for key in planned['task']['child_ids']),
                         sorted(assigned['child_ids']))
        self.assertTrue(all(len(planned['task']['child_ids']) == 1 for planned in plans))

    def test_one_card_can_partition_its_exact_obligation_scope(self):
        assigned = self.make_leaf(self.cards[:1])
        children = producer.split_task(self.epoch, assigned, cards=self.cards, observations=[self.observed])
        self.assertEqual(len(children), 2)
        self.assertEqual(sorted(key for child in children for key in child['obligation_ids']),
                         sorted(assigned['obligation_ids']))
        for child in children:
            self.assertEqual(child['card_ids'], assigned['card_ids'])
            self.assertEqual(child['context_roots'], assigned['context_roots'])

    def test_partition_dependencies_exclude_historical_discovery_from_direct_science(self):
        historical = copy.deepcopy(self.observed)
        historical.update(id='historical-discovery', task_id='historical-task')
        first_claim = next(iter(historical['actual_input']['context_roots']))
        historical['actual_input']['context_roots'][first_claim] = 'superseded-context-root'
        assigned = producer.remake_task(self.epoch, self.make_leaf(self.cards),
                                       dependencies=[historical['task_id'], self.reading['id']])
        children = producer.split_task(self.epoch, assigned, cards=self.cards,
                                        observations=[historical, self.observed])
        self.assertTrue(children)
        self.assertTrue(all(child['dependencies'] == [self.reading['id']] for child in children))

    def test_frontier_assigns_every_root_before_measured_partitioning(self):
        self.epoch = epoch(count=20, route_id='route')
        self.cards = [full_card(self.epoch, index=i) for i in range(20)]
        self.reading = task(self.epoch)
        self.observed = observation(self.epoch, self.reading, dict(cards=self.cards))
        self.records.update(review_epoch={self.epoch['id']:self.epoch}, review_task={}, review_task_acceptance={},
                            review_assignment={self.controller['id']:self.controller})
        self.add_observation(self.reading, self.observed, 'reader')
        receipts, observed = self.add_integration_roots()
        rendered = []

        def prepare_partitions(records, artifacts, controller, value, assigned, **options):
            rendered.append(assigned)
            return [], [dict(id=assigned['id'], epoch_id=value['id'], task=assigned)], None

        def prepare_mutation(store, operation, payload, prepare, **identity):
            changes, result = prepare(self.records, payload)
            return dict(result=result, changes=changes)

        store = type('PartitionStore', (), dict(revision=1, build_artifact_store=lambda _:self.artifacts))()
        with patch.object(operations, 'prepared_task_mutation', side_effect=prepare_mutation), \
                patch.object(producer, 'matches_currency', return_value=True), \
                patch.object(producer, 'plan_remaining_tasks', return_value=dict(tasks=[])), \
                patch.object(producer, 'prepare_partitions', side_effect=prepare_partitions), \
                patch.object(producer, 'prepare_child', return_value=([], dict(id='hierarchy-child'))):
            producer.plan_frontier(store, dict(controller_assignment_id=self.controller['id']),
                                   expected_revision=1, request_id='all-roots')
        self.assertEqual(len(rendered), 1)
        self.assertEqual(rendered[0]['child_ids'], sorted(row['id'] for row in receipts))
        self.assertEqual(rendered[0]['dependencies'], sorted(row['task_id'] for row in observed))
        self.assertEqual(rendered[0]['card_ids'], [])


if __name__ == '__main__':
    unittest.main()
