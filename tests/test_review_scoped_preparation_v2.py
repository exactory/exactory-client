"""Ordinary review preparations bind selected groups across concurrent commits."""

import copy
import sqlite3
import subprocess
import sys
from unittest.mock import patch

import test_research_review_protocol as fixtures
import test_research_context_repair_integration as repair_fixtures
from literature_fixtures import LiteratureCase
from research_harness import operations, review_protocol as protocol, storage
from research_harness.errors import ResearchError
from research_harness.evaluation import Evaluation


class ScopedPreparationTests(LiteratureCase):
    protocol = fixtures.ReviewProtocolTests.protocol
    seed = fixtures.ReviewProtocolTests.seed
    route = fixtures.ReviewProtocolTests.route
    response = fixtures.ReviewProtocolTests.response
    run_probe = fixtures.ReviewProtocolTests.run_probe
    assign = fixtures.ReviewProtocolTests.assign
    dossier = fixtures.ReviewProtocolTests.dossier
    records = fixtures.ReviewProtocolTests.records

    def setUp(self):
        super().setUp()
        self.route()

    def refuse(self, code, action):
        with self.assertRaises(ResearchError) as raised:
            action()
        self.assertEqual(raised.exception.code, code)

    def read_group(self, records, kind, **where):
        read = getattr(storage, 'read_filtered_records', None)
        self.assertTrue(callable(read), 'Filtered membership must bind even an empty selected group')
        return read(records, kind, **where)

    def test_unrelated_assignments_and_attempts_preserve_ordinary_assignment_preparation(self):
        build = protocol.build_packet
        before = self.store.revision

        def intervene(*args, **kwargs):
            packet = build(*args, **kwargs)
            self.seed('review_assignment', 'foreign', dict(id='foreign', role='standalone',
                route_id='route', dossier_id=None, reviewer_id='other', author_id='author',
                context={}, recorded_revision=before + 1))
            self.seed('review_attempt', 'foreign-attempt', dict(id='foreign-attempt',
                assignment_id='foreign', status='failed', recorded_revision=before + 2))
            return packet

        with patch.object(protocol, 'build_packet', side_effect=intervene):
            result = self.assign()
        self.assertEqual(result['revision'], before + 3)
        self.assertEqual(result['result']['recorded_revision'], before + 3)
        self.assertEqual(self.records()['review_assignment']['assignment'], result['result'])

    def test_start_and_finish_accept_unrelated_commits_and_stamp_actual_revisions(self):
        self.run_probe()
        self.assign()
        build = protocol.build_assignment_request
        before = self.store.revision

        def intervene(*args, **kwargs):
            request = build(*args, **kwargs)
            self.seed('review_attempt', 'foreign-start', dict(id='foreign-start',
                assignment_id='foreign', status='failed', recorded_revision=before + 1))
            return request

        def observe(*args):
            self.seed('review_attempt', 'foreign-call', dict(id='foreign-call',
                assignment_id='foreign', status='failed', recorded_revision=self.store.revision + 1))
            return dict(status='failed', request=None, response=None, output=None)

        finish_write = self.store.mutate_scoped
        injected = []

        def finish_intervene(operation, *args, **kwargs):
            if operation == 'review.invoke' and not injected:
                injected.append(True)
                self.seed('review_attempt', 'foreign-finish', dict(id='foreign-finish',
                    assignment_id='foreign', status='failed', recorded_revision=self.store.revision + 1))
            return finish_write(operation, *args, **kwargs)

        with patch.object(protocol, 'build_assignment_request', side_effect=intervene), \
                patch.object(protocol, '_observed', side_effect=observe), \
                patch.object(self.store, 'mutate_scoped', side_effect=finish_intervene):
            result = protocol.invoke_assignment(self.store, dict(id='attempt', assignment_id='assignment'),
                expected_revision=before, request_id='invoke', credential=None)
        records = self.records()
        self.assertEqual(records['review_invocation']['attempt']['recorded_revision'], before + 2)
        from research_harness.review_task_execution import identify_call
        self.assertEqual(records['review_pool_membership'][identify_call('review.invoke', 'attempt')]['recorded_revision'],
                         result['revision'])
        self.assertEqual(result['result']['recorded_revision'], result['revision'])
        self.assertEqual(result['revision'], before + 6)

    def test_relevant_attempt_inserted_after_empty_lookup_invalidates_start(self):
        self.run_probe()
        self.assign()
        before = self.store.revision
        build = protocol.build_assignment_request

        def intervene(*args, **kwargs):
            request = build(*args, **kwargs)
            self.seed('review_attempt', 'relevant', dict(id='relevant', assignment_id='assignment',
                status='failed', recorded_revision=before + 1))
            return request

        with patch.object(protocol, 'build_assignment_request', side_effect=intervene), \
                patch.object(protocol, '_observed', side_effect=AssertionError('No call after changed dependencies')):
            self.refuse('review_task_dependency_changed', lambda: protocol.invoke_assignment(self.store,
                dict(id='attempt', assignment_id='assignment'), expected_revision=before,
                request_id='invoke', credential=None))
        self.assertNotIn('attempt', self.records().get('review_invocation', {}))

    def test_bar_assignment_and_invocation_ignore_other_assignment_groups(self):
        self.dossier()
        self.run_probe()
        build = protocol.build_packet

        def intervene(*args, **kwargs):
            packet = build(*args, **kwargs)
            self.seed('review_assignment', 'foreign', dict(id='foreign', role='standalone',
                dossier_id=None, route_id='route', context={}, recorded_revision=self.store.revision + 1))
            return packet
        with patch.object(protocol, 'build_packet', side_effect=intervene):
            self.assign(role='bar')
        request = protocol.build_assignment_request

        def before_start(*args, **kwargs):
            result = request(*args, **kwargs)
            self.seed('review_assignment', 'other', dict(id='other', role='standalone',
                dossier_id=None, route_id='route', context={}, recorded_revision=self.store.revision + 1))
            return result
        with patch.object(protocol, 'build_assignment_request', side_effect=before_start), \
                patch.object(protocol, '_observed', return_value=dict(status='failed', output=None)):
            result = protocol.invoke_assignment(self.store, dict(id='attempt', assignment_id='assignment'),
                expected_revision=self.store.revision, request_id='bar-run', credential=None)
        self.assertEqual(result['result']['status'], 'failed')

    def test_repair_preparation_ignores_unrelated_assignments_attempts_and_exposures(self):
        case = repair_fixtures.ContextRepairIntegrationTests()
        self.addCleanup(case.doCleanups)
        case.setUp()
        case.expose()
        case.probe_repair()
        commit = case.store.mutate_scoped

        def intervene(operation, *args, **kwargs):
            if operation == 'review.assignment':
                revision = case.store.revision
                case.store.mutate('unrelated', {}, lambda tx: (
                    tx.put('review_assignment', 'unrelated', dict(id='unrelated', role='standalone',
                        dossier_id=None, context={}, route_id='route', recorded_revision=revision + 1)),
                    tx.put('review_attempt', 'unrelated', dict(id='unrelated', assignment_id='unrelated', status='failed')),
                    tx.put('review_context_event', 'unrelated', dict(id='unrelated', assignment_id='unrelated',
                        kind='contamination'))), expected_revision=revision, request_id='unrelated')
            return commit(operation, *args, **kwargs)

        with patch.object(case.store, 'mutate_scoped', side_effect=intervene):
            result = case.mutate(protocol.record_assignment, case.repair_payload())
        self.assertEqual(result['result']['slot_id'], 'assignment-bar-a')
        self.assertEqual(result['result']['recorded_revision'], result['revision'])

    def test_filtered_empty_membership_and_moves_are_revalidated(self):
        for kind, where in (
                ('review_attempt', dict(assignment_id='own')),
                ('review_invocation', dict(assignment_id='own')),
                ('review_route_probe', dict(route_id='own')),
                ('review_assignment', dict(slot_id='own')),
                ('review_assignment', {'staged_review.controller_assignment_id': 'own'}),
                ('review_context_event', dict(assignment_id='own')),
                ('review_output_correction', dict(assignment_id='own')),
                ('review_selection', dict(controller_assignment_id='own'))):
            with self.subTest(kind=kind, where=where):
                def prepare(records, value):
                    self.assertEqual(self.read_group(records, kind, **where), {})
                    row = dict(id='new')
                    for field, expected in where.items():
                        if '.' in field:
                            outer, inner = field.split('.')
                            row[outer] = {inner: expected}
                        else:
                            row[field] = expected
                    self.seed(kind, 'new', row)
                    return [], {}
                self.refuse('review_task_dependency_changed', lambda: operations.prepared_task_mutation(
                    self.store, 'filtered', {}, prepare, request_id='filtered-' + kind + str(where)))
                self.seed(kind, 'new', dict(id='new', assignment_id='foreign', route_id='foreign', slot_id='foreign'))

    def test_another_process_changes_only_the_selected_membership(self):
        program = '''import json, sys
from pathlib import Path
from research_harness.storage import Store
store = Store(Path(sys.argv[1]))
row = {'id':sys.argv[2], 'assignment_id':sys.argv[3], 'status':'failed'}
store.mutate('other-process', row, lambda tx: tx.put('review_attempt', row['id'], row),
             expected_revision=store.revision, request_id=row['id'])
'''
        for identifier, assignment in (('foreign-process', 'other'), ('own-process', 'own')):
            def prepare(records, value):
                self.assertEqual(self.read_group(records, 'review_attempt', assignment_id='own'), {})
                subprocess.run([sys.executable, '-c', program, str(self.root), identifier, assignment],
                               check=True, capture_output=True, text=True, timeout=30)
                return [], dict(ok=True)
            action = lambda: operations.prepared_task_mutation(self.store, 'process-race', {}, prepare,
                                                               request_id=identifier + '-prepared')
            if assignment == 'own':
                self.refuse('review_task_dependency_changed', action)
            else:
                self.assertTrue(action()['result']['ok'])

    def test_call_allowance_is_checked_after_unrelated_completion_in_start_transaction(self):
        self.run_probe()
        self.assign()
        self.seed('review_budget', 'store', dict(id='store', max_calls=2, reason='One call remains'))
        commit = self.store.mutate_scoped

        def intervene(operation, *args, **kwargs):
            if operation == 'review.invoke_start':
                self.seed('review_attempt', 'last-allowance', dict(id='last-allowance',
                    assignment_id='foreign', status='failed', origin='imported'))
            return commit(operation, *args, **kwargs)

        with patch.object(self.store, 'mutate_scoped', side_effect=intervene), \
                patch.object(protocol, '_observed', side_effect=AssertionError('Admission must prevent model I/O')):
            self.refuse('review_budget_exhausted', lambda: protocol.invoke_assignment(self.store,
                dict(id='blocked', assignment_id='assignment'), expected_revision=self.store.revision,
                request_id='blocked', credential=None))
        self.assertNotIn('blocked', self.records().get('review_invocation', {}))

    def test_scoped_finish_reprepares_after_dependency_change_without_repeating_model_io(self):
        self.assign()
        commit = self.store.mutate_scoped
        injected = []
        observed = []

        def intervene(operation, *args, **kwargs):
            if operation == 'review.invoke' and not injected:
                injected.append(True)
                self.seed('review_route', 'route', self.records()['review_route']['route'])
            return commit(operation, *args, **kwargs)

        def observe(*args):
            observed.append(True)
            return dict(status='failed', output=None)

        with patch.object(self.store, 'mutate_scoped', side_effect=intervene), \
                patch.object(storage, '_BUSY_TIMEOUT_SECONDS', .001), \
                patch.object(protocol, '_observed', side_effect=observe):
            result = protocol.invoke_assignment(self.store, dict(id='attempt', assignment_id='assignment'),
                expected_revision=self.store.revision, request_id='race-finish', credential=None)
        self.assertEqual(result['result']['status'], 'failed')
        self.assertEqual(observed, [True])
        self.assertEqual(self.records()['review_attempt']['attempt']['recorded_revision'], result['revision'])

    def test_exhausted_scoped_finish_keeps_dependency_error_explicit(self):
        self.assign()
        commit = self.store.mutate_scoped
        refused = []

        def intervene(operation, *args, **kwargs):
            if operation == 'review.invoke':
                refused.append(True)
                self.seed('review_route', 'route', self.records()['review_route']['route'])
            return commit(operation, *args, **kwargs)

        with patch.object(self.store, 'mutate_scoped', side_effect=intervene), \
                patch.object(storage, '_BUSY_TIMEOUT_SECONDS', .001), \
                patch.object(protocol, '_FINISH_ATTEMPT_COUNT', 2), \
                patch.object(protocol, '_observed', return_value=dict(status='failed', output=None)):
            self.refuse('review_task_dependency_changed', lambda: protocol.invoke_assignment(self.store,
                dict(id='attempt', assignment_id='assignment'), expected_revision=self.store.revision,
                request_id='race-finish', credential=None))
        self.assertEqual(refused, [True, True])
        self.assertNotIn('attempt', self.records().get('review_attempt', {}))

    def test_explicit_null_expected_revision_stays_invalid_input(self):
        self.assign()
        payload = dict(id='new', route_id='route', dossier_id=None, role='standalone',
                       reviewer_id='reviewer', author_id='author',
                       context=self.records()['review_assignment']['assignment']['context'])
        self.refuse('invalid_input', lambda: protocol.record_assignment(self.store, payload,
            expected_revision=None, request_id='null-assignment'))
        self.refuse('invalid_input', lambda: protocol.invoke_assignment(self.store,
            dict(id='attempt', assignment_id='assignment'), expected_revision=None,
            request_id='null-invoke', credential=None))

    def test_filtered_rows_bind_content_and_membership_without_binding_siblings(self):
        self.seed('review_attempt', 'own', dict(assignment_id='a', status='failed'))
        self.seed('review_attempt', 'foreign', dict(assignment_id='b', status='failed'))

        def prepare(records, value):
            selected = self.read_group(records, 'review_attempt', assignment_id='a')
            self.seed('review_attempt', 'foreign', dict(assignment_id='b', status='completed'))
            self.seed('review_attempt', 'other', dict(assignment_id='b', status='failed'))
            return [], selected
        result = operations.prepared_task_mutation(self.store, 'filtered', {}, prepare, request_id='unrelated')
        self.assertEqual(result['result'], {'own': dict(assignment_id='a', status='failed')})
        for row in (dict(assignment_id='a', status='completed'), dict(assignment_id='b', status='failed')):
            def change(records, value):
                self.read_group(records, 'review_attempt', assignment_id='a')
                self.seed('review_attempt', 'own', row)
                return [], {}
            self.refuse('review_task_dependency_changed', lambda: operations.prepared_task_mutation(
                self.store, 'filtered', {}, change, request_id='changed'))

    def test_nested_helper_views_retain_ancestor_correction_groups(self):
        self.seed('review_output_correction_invocation', 'ancestor', dict(assignment_id='ancestor'))
        self.seed('review_output_correction_invocation', 'own', dict(assignment_id='own'))

        def prepare(records, value):
            lineage = storage.select_record_groups(records, {
                'review_output_correction_invocation': [dict(assignment_id='ancestor')]})
            child = storage.select_record_groups(lineage, {
                'review_output_correction_invocation': [dict(assignment_id='own')]})
            self.assertEqual(set(child['review_output_correction_invocation']), {'ancestor', 'own'})
            self.seed('review_output_correction_invocation', 'foreign', dict(assignment_id='foreign'))
            return [], {}
        operations.prepared_task_mutation(self.store, 'nested', {}, prepare, request_id='nested')

    def test_unset_optional_selector_reads_as_missing_without_binding_its_kind(self):
        def prepare(records, value):
            self.assertIsNone(records.get('source_bundle', {}).get(None))
            self.assertNotIn(None, records.get('source_bundle', {}))
            self.seed('source_bundle', 'unrelated', dict(id='unrelated'))
            return [], {}
        operations.prepared_task_mutation(self.store, 'optional', {}, prepare, request_id='optional')

    def test_cached_requests_bind_selected_attempts_and_ignore_unrelated_index_rows(self):
        self.assign()
        self.assign(identifier='foreign', reviewer='other')
        # An index built outside preparation cannot provide untracked results.
        warm = self.records()
        evaluation = Evaluation(warm, self.artifacts)
        protocol.build_assignment_request(warm, evaluation, warm['review_assignment']['assignment'])

        def prepare(records, value):
            from research_harness.review_state_evaluation import runtime_scope
            with runtime_scope(records, evaluation) as owned:
                foreign = records['review_assignment']['foreign']
                own = records['review_assignment']['assignment']
                protocol.build_assignment_request(records, owned, foreign)
                first = protocol.build_assignment_request(records, owned, own)
                second = protocol.build_assignment_request(records, owned, own)
                self.assertEqual(first, second)
                second.clear()
            self.seed('review_attempt', 'unrelated', dict(id='unrelated', assignment_id='another',
                status='failed', recorded_revision=self.store.revision + 1))
            return [], first
        result = operations.prepared_task_mutation(self.store, 'cached', {}, prepare, request_id='cached')
        self.assertEqual(result['result']['tools'], [])

    def test_new_preparation_does_not_reuse_previous_request_evaluation(self):
        self.assign()
        builds = []
        build = protocol.transport.request_body

        def count(*args, **kwargs):
            builds.append(True)
            return build(*args, **kwargs)

        def prepare(records, value):
            assignment = records['review_assignment']['assignment']
            protocol.build_assignment_request(records, self.artifacts, assignment)
            protocol.build_assignment_request(records, self.artifacts, assignment)
            return [], {}
        with patch.object(protocol.transport, 'request_body', side_effect=count):
            for index in range(2):
                protocol._prepare_review_mutation(self.store, 'prepare', {}, prepare,
                    expected_revision=self.store.revision, request_id='fresh-' + str(index))
        self.assertEqual(len(builds), 2)

    def test_assignment_receipt_and_conflict_precede_revision_and_domain_checks(self):
        result = self.assign()
        payload = copy.deepcopy(result['result'])
        payload = {key: payload[key] for key in ('id', 'route_id', 'dossier_id', 'role', 'reviewer_id', 'author_id', 'context')}
        self.seed('review_route', 'route', dict(id='route', changed=True))
        with patch.object(protocol, 'build_packet', side_effect=AssertionError('Replay precedes domain work')):
            self.assertEqual(protocol.record_assignment(self.store, payload, expected_revision=0,
                request_id=result['request_id']), result)
            self.refuse('request_id_conflict', lambda: protocol.record_assignment(self.store,
                dict(payload, reviewer_id='changed'), expected_revision=0, request_id=result['request_id']))
        self.refuse('stale_revision', lambda: protocol.record_assignment(self.store,
            dict(payload, id='new'), expected_revision=0, request_id='stale'))

    def test_diagnostic_assignment_preparation_and_receipt_replay_keep_read_only_behavior(self):
        from research_harness.check_mode import check_mutation
        self.assign()
        assignment = self.records()['review_assignment']['assignment']
        payload = {key: assignment[key] for key in
                   ('id', 'route_id', 'dossier_id', 'role', 'reviewer_id', 'author_id', 'context')}
        payload['id'] = 'checked'
        revision = self.store.revision
        checked = check_mutation(self.store, protocol.record_assignment, payload,
            expected_revision=revision, request_id='checked', bounded_payload=payload)
        self.assertEqual(checked['check'], 'accepted')
        self.assertEqual(self.store.revision, revision)
        self.assertNotIn('checked', self.records()['review_assignment'])
        invocation = dict(id='recorded-probe', route_id='removed-route')
        receipt = self.store.mutate('review.route_probe', invocation, lambda tx: {},
            expected_revision=revision, request_id='recorded-probe')
        checked = check_mutation(self.store, protocol.probe_route, invocation,
            expected_revision=0, request_id='recorded-probe', bounded_payload=invocation)
        self.assertEqual(checked, receipt)

    def test_invocation_request_conflict_precedes_invalid_assignment_selector(self):
        self.assign()
        with patch.object(protocol, '_observed', return_value=dict(status='failed', output=None)):
            protocol.invoke_assignment(self.store, dict(id='attempt', assignment_id='assignment'),
                expected_revision=self.store.revision, request_id='receipt', credential=None)
        with patch.object(protocol, '_observed', side_effect=AssertionError('A conflict never dispatches')):
            for selector in ('', ' ', 3, False):
                with self.subTest(selector=selector):
                    self.refuse('request_id_conflict', lambda: protocol.invoke_assignment(self.store,
                        dict(id='attempt', assignment_id=selector), expected_revision=0,
                        request_id='receipt', credential=None))

    def test_commit_stamping_preserves_sealed_auxiliary_records(self):
        from research_harness import review_staged
        sealed = review_staged._seal(dict(id='sealed', recorded_revision=None, origin='execution'))

        def prepare(records, value):
            return [operations.immutable_record(records, 'assessment_incomplete', 'sealed', sealed)], dict(ok=True)
        protocol._prepare_review_mutation(self.store, 'review.assignment', {}, prepare, scoped=True,
            expected_revision=self.store.revision, request_id='sealed')
        stored = self.records()['assessment_incomplete']['sealed']
        self.assertEqual(stored, sealed)
        review_staged._verify_seal(stored)

    def test_corrupt_history_after_preparation_still_prevents_assignment_commit(self):
        build = protocol.build_packet

        def corrupt_projection(*args, **kwargs):
            packet = build(*args, **kwargs)
            with sqlite3.connect(str(self.store._path)) as connection:
                connection.execute("UPDATE records SET value = '{}' WHERE kind = 'review_route'")
            return packet
        with patch.object(protocol, 'build_packet', side_effect=corrupt_projection):
            self.refuse('corrupt_state', self.assign)
