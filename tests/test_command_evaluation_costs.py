"""Command ownership bounds repeated review work without weakening checks."""

import copy
import json
import unittest
import weakref
from unittest.mock import patch

from literature_fixtures import LiteratureCase
from research_harness import review_protocol as protocol, review_state_evaluation as states
from research_harness import review_transport as transport
from research_harness.errors import ResearchError
from research_harness.evaluation import Evaluation
from research_harness.evidence import digest


class CountedInventory(dict):
    def __init__(self, rows):
        super().__init__(rows)
        self.visits = 0

    def values(self):
        for row in super().values():
            self.visits += 1
            yield row


class CommandEvaluationTests(LiteratureCase):
    def test_explicit_scope_observes_boundaries_and_isolates_returned_states(self):
        records = {'review_route': {'route': {'id': 'route', 'adapter': transport.ADAPTER}}}
        evaluation = Evaluation(records, self.artifacts)
        calls = []
        def compute(records, artifacts, identifier):
            calls.append(identifier)
            return {'ready': True, 'output': {'evidence': ['original']}}
        with patch.object(transport, 'runtime', wraps=transport.runtime) as runtime:
            with states.runtime_scope(records, evaluation):
                for _ in range(5):
                    with states.runtime_scope(records, evaluation):
                        result = states.evaluate_state(records, evaluation, 'assignment', 'one', compute)
                        self.assertEqual(result['output']['evidence'], ['original'])
                        result['output']['evidence'].clear()
            self.assertEqual(runtime.call_count, 2)
        self.assertEqual(calls, ['one'])

    def test_nested_origin_evaluations_share_runtime_boundaries(self):
        records = {'review_route': {'route': {'id': 'route', 'adapter': transport.ADAPTER}}}
        evaluation = Evaluation(records, self.artifacts)
        with patch.object(transport, 'runtime', wraps=transport.runtime) as runtime:
            for _ in range(2):
                with states.runtime_scope(records, evaluation):
                    origin = evaluation.once('origin', lambda: Evaluation.of(dict(records), evaluation))
                    for _ in range(3):
                        result = states.evaluate_state(origin.records, origin, 'assignment', 'one',
                                                       lambda *args: {'ready': True})
                        self.assertTrue(result['ready'])
            self.assertEqual(runtime.call_count, 4)

    def test_zero_budget_child_shares_command_runtime_observations(self):
        records = {'review_route': {'route': {'id': 'route', 'adapter': transport.ADAPTER}}}
        evaluation = Evaluation(records, self.artifacts)
        with patch.object(transport, 'runtime', wraps=transport.runtime) as runtime:
            with states.runtime_scope(records, evaluation):
                child = Evaluation.of(dict(records), evaluation, retention_budget_bytes=0)
                for _ in range(3):
                    self.assertTrue(states.evaluate_state(child.records, child, 'assignment', 'one',
                                                         lambda *args: {'ready': True})['ready'])
            self.assertEqual(runtime.call_count, 2)

    def test_scope_drift_refuses_and_discards_warm_state(self):
        records = {'review_route': {'route': {'id': 'route', 'adapter': transport.ADAPTER}}}
        evaluation = Evaluation(records, self.artifacts)
        with patch.object(transport, 'runtime', side_effect=[{'version': 1}, {'version': 2}]):
            with self.assertRaises(ResearchError) as error:
                with states.runtime_scope(records, evaluation):
                    states.evaluate_state(records, evaluation, 'assignment', 'one',
                                          lambda *args: {'ready': True})
            self.assertEqual(error.exception.code, 'review_runtime_changed')
        result = states.evaluate_state(records, evaluation, 'assignment', 'one', lambda *args: {'ready': False})
        self.assertFalse(result['ready'])

    def test_drift_discards_child_states_from_a_separate_retention_budget(self):
        records = {'review_route': {'route': {'id': 'route', 'adapter': transport.ADAPTER}}}
        root = Evaluation(records, self.artifacts, retention_budget_bytes=20000)
        child = Evaluation.of(dict(records), root, retention_budget_bytes=10000)
        with patch.object(transport, 'runtime', side_effect=[{'version': 1}, {'version': 2}]):
            with self.assertRaises(ResearchError) as error:
                with states.runtime_scope(records, root):
                    self.assertTrue(states.evaluate_state(child.records, child, 'assignment', 'one',
                                                         lambda *args: {'ready': True})['ready'])
            self.assertEqual(error.exception.code, 'review_runtime_changed')
        self.assertEqual(child.retention_report()['optional_bytes'], 0)
        with patch.object(transport, 'runtime', return_value={'version': 1}):
            with states.runtime_scope(records, root):
                self.assertFalse(states.evaluate_state(child.records, child, 'assignment', 'one',
                                                       lambda *args: {'ready': False})['ready'])

    def test_runtime_drift_clears_requests_in_every_participating_arena(self):
        records = {'review_route': {'route': {'id': 'route', 'adapter': transport.ADAPTER}}}
        root = Evaluation(records, self.artifacts, retention_budget_bytes=20000)
        child = Evaluation.of(dict(records), root, retention_budget_bytes=10000)
        with patch.object(transport, 'runtime', side_effect=[{'version': 1}, {'version': 2}]):
            with self.assertRaises(ResearchError) as error:
                with states.runtime_scope(records, root):
                    for evaluation in (root, child):
                        evaluation.request_once(('warm',), lambda: b'compressed request')
                        evaluation.once('ordinary', lambda: 'warm state')
            self.assertEqual(error.exception.code, 'review_runtime_changed')
        for evaluation in (root, child):
            self.assertEqual(evaluation.retention_report()['optional_bytes'], 0)
            self.assertEqual(evaluation.retention_report()['request_entries'], 0)

    def test_runtime_scope_does_not_retain_inactive_evaluation_views(self):
        records = {'review_route': {'route': {'id': 'route', 'adapter': transport.ADAPTER}}}
        root = Evaluation(records, self.artifacts, retention_budget_bytes=1000)
        with states.runtime_scope(records, root):
            child = Evaluation.of(dict(records, payload='large input' * 1000000), root)
            retained = weakref.ref(child)
            child.request_once(('oversized',), lambda: b'compressed request' * 100)
            del child
            self.assertIsNone(retained())
            self.assertEqual(root.retention_report()['optional_bytes'], 0)

    def test_unavailable_scope_fingerprint_keeps_native_uncached_checks(self):
        records = {'review_route': {'route': {'id': 'route', 'adapter': transport.ADAPTER}}}
        evaluation = Evaluation(records, self.artifacts)
        results = iter([{'ready': False}, {'ready': True}])
        with patch.object(transport, 'runtime', side_effect=ResearchError('review_runtime_missing', 'Unavailable')):
            with states.runtime_scope(records, evaluation):
                for expected in (False, True):
                    self.assertEqual(states.evaluate_state(records, evaluation, 'assignment', 'one',
                                     lambda *args: next(results))['ready'], expected)

    def test_all_assignments_share_one_attempt_inventory_and_keep_equal_revision_order(self):
        from test_research_review_protocol import ReviewProtocolTests
        fixture = ReviewProtocolTests()
        self.addCleanup(fixture.doCleanups)
        fixture.setUp()
        fixture.route()
        fixture.run_probe()
        fixture.assign()
        records = fixture.records()
        template = records['review_assignment']['assignment']
        records['review_assignment'] = {}
        records['review_attempt'] = CountedInventory({})
        for index in range(8):
            identifier = 'assignment-' + str(index)
            records['review_assignment'][identifier] = dict(template, id=identifier)
            for suffix in ('first', 'second'):
                key = identifier + suffix
                records['review_attempt'][key] = {'id': key, 'assignment_id': identifier,
                    'status': 'completed' if index == 0 else 'failed', 'recorded_revision': 1}
        evaluation = Evaluation(records, fixture.artifacts)
        for identifier in records['review_assignment']:
            state = protocol.assignment_state(records, evaluation, identifier)
            self.assertFalse(state['ready'])
            if identifier == 'assignment-0':
                self.assertEqual(state['attempt_id'], 'assignment-0first')
            request = protocol.build_assignment_request(records, evaluation, records['review_assignment'][identifier])
            self.assertEqual(request['tools'], [])
        self.assertEqual(records['review_attempt'].visits, 16)

    def test_probe_inventory_is_indexed_once_for_all_routes(self):
        records = {'review_route': {str(i): {'id': str(i), 'adapter': transport.ADAPTER} for i in range(8)},
                   'review_route_probe': CountedInventory({str(i): {'id': str(i), 'route_id': str(i),
                       'recorded_revision': 1} for i in range(8)})}
        evaluation = Evaluation(records, self.artifacts)
        for identifier in records['review_route']:
            self.assertEqual(protocol.route_state(records, evaluation, identifier)['context_status'], 'unverified')
        self.assertEqual(records['review_route_probe'].visits, 8)

    def test_shared_ancestry_checks_each_packet_inventory_once_and_keeps_cycle_guard(self):
        from research_harness import bar_sources
        rows = []
        inventory = {'artifact_descriptor': bar_sources.descriptor(protocol._put_json(self.artifacts, rows)),
                     'total': 0, 'digest': digest(rows), 'page_size': 10,
                     'first_page': bar_sources._page(rows, digest(rows), 0)}
        packet = {'source_inventory': inventory}
        reference = protocol._put_json(self.artifacts, packet)
        assignments = {key: {'id': key, 'role': 'standalone', 'packet': reference,
                            'history_assignment_id': parent}
                       for key, parent in [('root', None), ('left', 'root'), ('right', 'root')]}
        records = {'review_assignment': assignments}
        evaluation = Evaluation(records, self.artifacts)
        with patch.object(bar_sources, 'validate_inventory', wraps=bar_sources.validate_inventory) as validate:
            for identifier in ('left', 'right', 'root'):
                protocol.validate_assignment_inventories(records, evaluation, identifier)
            self.assertEqual(validate.call_count, 1)
        cyclic = copy.deepcopy(records)
        cyclic['review_assignment']['root']['history_assignment_id'] = 'left'
        with self.assertRaises(ResearchError) as error:
            protocol.validate_assignment_inventories(cyclic, evaluation, 'left')
        self.assertEqual(error.exception.code, 'review_context_repair_mismatch')

    def test_long_acyclic_lineage_preserves_iteration_order(self):
        from research_harness.review_supplied_evidence import lineage_packets
        assignments = {}
        reference = protocol._put_json(self.artifacts, {'retained': 'packet'})
        for index in range(1200):
            assignments[str(index)] = {'id': str(index), 'role': 'standalone',
                'history_assignment_id': str(index - 1) if index else None,
                'packet': reference}
        records = {'review_assignment': assignments}
        evaluation = Evaluation(records, self.artifacts)
        with patch.object(protocol, 'source_delivery_packet', wraps=protocol.source_delivery_packet) as read_packet:
            packets = lineage_packets(records, evaluation, ['1199'])
            self.assertEqual([call.args[2]['id'] for call in read_packet.call_args_list],
                             [str(index) for index in range(1199, -1, -1)])
        self.assertEqual(len(packets), 1200)
        protocol.validate_assignment_inventories(records, evaluation, '1199')

    def test_invocation_preparation_passes_one_evaluation_to_shared_checks(self):
        from test_research_review_protocol import ReviewProtocolTests
        fixture = ReviewProtocolTests()
        self.addCleanup(fixture.doCleanups)
        fixture.setUp()
        fixture.route()
        fixture.run_probe()
        fixture.assign()
        observed = []
        original = protocol.validate_assignment_inventories
        def validate(records, artifacts, identifier):
            observed.append(artifacts)
            return original(records, artifacts, identifier)
        with patch.object(protocol, 'validate_assignment_inventories', side_effect=validate), \
                patch.object(transport, 'send_request', return_value=fixture.response({'assessment': 'retained'})):
            fixture.mutate(protocol.invoke_assignment, {'id': 'attempt', 'assignment_id': 'assignment'}, credential='fixture')
        self.assertTrue(observed)
        self.assertTrue(all(isinstance(item, Evaluation) for item in observed))


class HistoryAuthenticationTests(LiteratureCase):
    def test_status_elapsed_time_includes_snapshot_authentication(self):
        from research_harness import cli
        clock = [10.0]
        snapshot = self.store.snapshot
        def authenticate():
            clock[0] += 7.0
            return snapshot()
        with patch.object(self.store, 'snapshot', side_effect=authenticate), \
                patch.object(cli.time, 'monotonic', side_effect=lambda: clock[0]):
            result = cli.status_report(self.store, counters=True)
        self.assertEqual(result['evaluation']['elapsed_seconds'], 7.0)

    def test_warm_command_does_not_hide_event_receipt_or_projection_corruption(self):
        import sqlite3
        from research_harness.cli import status_report
        for table in ('events', 'receipts', 'records'):
            with self.subTest(table=table):
                fixture = LiteratureCase()
                self.addCleanup(fixture.doCleanups)
                fixture.setUp()
                fixture.store.mutate('fixture', {}, lambda tx: tx.put('fixture', 'one', {'value': 1}),
                                     expected_revision=0, request_id='one')
                status_report(fixture.store)
                with sqlite3.connect(str(fixture.root / '.exactory/research.sqlite3')) as connection:
                    column = 'response' if table == 'receipts' else 'value' if table == 'records' else 'payload'
                    trigger = None
                    if table != 'records':
                        trigger = connection.execute("SELECT sql FROM sqlite_master WHERE name = ?",
                                                     (table + '_no_update',)).fetchone()[0]
                        connection.execute('DROP TRIGGER ' + table + '_no_update')
                    connection.execute('UPDATE ' + table + ' SET ' + column + ' = ?', ('{"altered":true}',))
                    if trigger:
                        connection.execute(trigger)
                with self.assertRaises(ResearchError):
                    status_report(fixture.store)
