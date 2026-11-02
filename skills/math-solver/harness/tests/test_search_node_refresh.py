"""Bound local observations independently of durable native acknowledgments."""

import contextlib
import io
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import attack
from search_controller import integration, model, service
from search_controller.errors import SearchError
from search_controller.storage import canonical_bytes
from tests.search_fixtures import contract, digest, proposal, provenance, review
from tests.test_search_proof import acceptance_record, checkpoint_record


class NodeRefreshTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.controller = service.Controller(self.root)
        # These tests exercise transaction and observation boundaries. Scientific
        # review ingress and advisory publication have separate integration tests.
        for target in ('search_controller.service.render', 'search_controller.output_views.render_after_command',
                       'search_controller.reviewer.validate_submitted'):
            patcher = patch(target)
            patcher.start()
            self.addCleanup(patcher.stop)

    def read_state(self):
        return model.replay(self.controller.store.read())

    def create_nodes(self, count):
        document = self.controller.store.initialize(contract(), 'objective-000001')
        for number in range(1, count + 1):
            candidate = proposal()
            candidate['attack_slug'] = 'attempt-' + str(number)
            approved = review(candidate)
            self.controller.store.put_blob(candidate)
            self.controller.store.put_blob(approved)
            for kind, payload in (
                ('proposal_recorded', {'proposal': candidate, 'digest': digest(candidate)}),
                ('review_recorded', {'proposal_id': 'proposal-{:06d}'.format(number),
                                     'review': approved, 'digest': digest(approved)}),
                ('proposal_admitted', {'proposal_id': 'proposal-{:06d}'.format(number)}),
            ):
                sequence = len(document['events']) + 1
                document['events'].append({'sequence': sequence, 'request_id': 'historical-' + str(sequence),
                                           'kind': kind, 'payload': payload})
            workspace = self.root / candidate['attack_slug']
            workspace.mkdir()
            (workspace / 'journal.jsonl').write_bytes(b'')
        # Install a validated historical fixture without changing public ingress.
        state = model.replay(document)
        self.controller.store.tree_path.write_bytes(canonical_bytes(document))
        return state

    def invoke(self, command, spec, request=None, target=None):
        revision = self.read_state()['revision']
        return self.controller.command(command, spec, revision, request or 'request-' + str(revision), target,
                                       workspace_root=self.root if command == 'focus' else None)

    def start_native(self):
        node = self.read_state()['nodes']['node-000001']
        args = SimpleNamespace(command='stall', slug=node['attack_slug'])
        identity, ownership = integration.begin_native_intent(self.controller, node, args)
        self.addCleanup(ownership.close)
        return {'controller': self.controller, 'node': node, 'command': 'stall',
                'native_intent': identity, 'native_ownership': ownership}

    def test_unrelated_observation_failure_does_not_block_native_acknowledgment(self):
        self.create_nodes(2)
        context = self.start_native()
        observe = integration.observe_node
        seen = []

        def inspect(controller, state, node, content=None):
            seen.append(node['id'])
            if node['id'] != 'node-000001':
                raise SearchError('observation_failed', 'Unrelated malformed journal')
            return observe(controller, state, node, content)

        try:
            with patch.object(integration, 'observe_node', side_effect=inspect):
                integration.after_legacy(context, 0)
        except SearchError as error:
            self.fail('Unrelated observation blocked successful acknowledgment: ' + str(error))
        state = self.read_state()
        self.assertEqual(state['service']['native_intents'], {})
        self.assertEqual(state['service']['native_receipts'][context['native_intent']]['outcome'], 'succeeded')
        self.assertEqual(seen, ['node-000001'])

    def test_acknowledgment_with_256_nodes_refreshes_only_its_producer(self):
        self.create_nodes(256)
        context = self.start_native()
        with patch.object(integration, 'observe_node', wraps=integration.observe_node) as observe:
            try:
                integration.after_legacy(context, 0)
            except SearchError as error:
                self.fail('Acknowledgment exceeded the operation envelope: ' + str(error))
        self.assertEqual([call.args[2]['id'] for call in observe.call_args_list], ['node-000001'])
        events = self.controller.store.read()['events']
        ack = next(event for event in events if any(op['kind'] == 'native_acknowledged'
                   for op in event.get('payload', {}).get('operations', [])))
        self.assertEqual([op['kind'] for op in ack['payload']['operations']], ['native_acknowledged'])
        self.assertLess(ack['sequence'], events[-1]['sequence'])
        self.assertEqual(set(self.read_state()['control']['node_facts']), {'node-000001'})

    def test_producer_observation_failure_leaves_acknowledgment_durable(self):
        self.create_nodes(1)
        context = self.start_native()
        with patch.object(integration, 'observe_node', side_effect=OSError('Malformed producer workspace')):
            with self.assertRaises((SearchError, OSError)) as caught:
                integration.after_legacy(context, 0)
        state = self.read_state()
        self.assertEqual(state['service']['native_intents'], {}, 'Observation failure must not erase the acknowledgment')
        self.assertEqual(state['service']['native_receipts'][context['native_intent']]['outcome'], 'succeeded')
        self.assertIsInstance(caught.exception, SearchError)
        self.assertEqual(caught.exception.code, 'recovery_required')
        self.assertEqual(caught.exception.details['node_ids'], ['node-000001'])
        self.assertIn('acknowledged', str(caught.exception).lower())

    def test_reconcile_refreshes_only_the_recovered_producer(self):
        self.create_nodes(2)
        context = self.start_native()
        context['native_ownership'].close()
        with patch.object(integration, 'observe_node', wraps=integration.observe_node) as observe:
            self.invoke('reconcile', {})
        self.assertEqual([call.args[2]['id'] for call in observe.call_args_list], ['node-000001'])
        self.assertEqual(self.read_state()['service']['native_intents'], {})

    def test_reconcile_observation_failure_keeps_its_acknowledgment(self):
        self.create_nodes(1)
        context = self.start_native()
        context['native_ownership'].close()
        with patch.object(integration, 'observe_node', side_effect=OSError('Cannot observe producer')):
            with self.assertRaises((SearchError, OSError)):
                self.invoke('reconcile', {})
        self.assertEqual(self.read_state()['service']['native_intents'], {})

    def build_checkpoint(self, local):
        state = self.read_state()
        cp = checkpoint_record(state, 'obligation-000001')
        del cp['verification_status']
        cp['evidence_digests'] = [self.controller.store.put_blob({'conclusion': {
            'outcome': 'proof', 'dependency_ids': [], 'route_bindings': []}})]
        if local:
            cp.update(origin={'kind': 'journal_move', 'node_id': 'node-000001', 'move': 1,
                              'journal_prefix_digest': 'a' * 64}, milestone_id='root-proof')
        return {'checkpoint': cp, 'inputs': []}

    def test_checkpoint_and_acceptance_observe_only_exact_local_producer(self):
        self.assert_checkpoint_observations(True)

    def test_external_checkpoint_and_acceptance_do_not_observe_local_nodes(self):
        self.assert_checkpoint_observations(False)

    def build_acceptance(self):
        state = self.read_state()
        cp = state['checkpoints']['checkpoint-000001']
        approved = acceptance_record(state, 'obligation-000001')['review']
        approved['schema_version'] = 2
        approved['findings']['openness'] = {'outcome': 'unresolved', 'statement_digest': digest(cp['claim']),
            'evidence': cp['evidence_digests'], 'searched_at': '2026-11-01',
            'coverage': 'Exact fixture statement', 'reason': 'No openness claim'}
        return {'obligation_id': 'obligation-000001', 'outcome': 'proof', 'dependency_ids': [],
                'route_bindings': [], 'review': approved, 'inputs': []}

    def test_external_acceptance_retains_reducer_refusal_before_review_ingress(self):
        self.create_nodes(1)
        with patch.object(service, 'audit_checkpoint', return_value=('analytical', 'reviewed')), \
                patch.object(service, 'audit_state', return_value={}):
            self.invoke('checkpoint', self.build_checkpoint(False))
            spec = dict(self.build_acceptance(), claims_openness=True)
            with patch('search_controller.reviewer.validate_submitted',
                       side_effect=SearchError('review_assignment_required', 'No observed review')):
                with self.assertRaises(SearchError) as caught:
                    self.invoke('accept', spec, target='checkpoint-000001')
        self.assertEqual(caught.exception.code, 'openness_not_supported')

    def assert_checkpoint_observations(self, local):
        self.create_nodes(2)
        with patch.object(service, 'audit_checkpoint', return_value=('analytical', 'reviewed')), \
                patch.object(service, 'audit_state', return_value={}), \
                patch.object(integration, 'observe_node', wraps=integration.observe_node) as observe:
            self.invoke('checkpoint', self.build_checkpoint(local), target='node-000001' if local else None)
            expected = ['node-000001'] if local else []
            self.assertEqual([call.args[2]['id'] for call in observe.call_args_list], expected)
            observe.reset_mock()
            self.invoke('accept', self.build_acceptance(), target='checkpoint-000001')
            self.assertEqual([call.args[2]['id'] for call in observe.call_args_list], expected)

    def test_refresh_accepts_256_nodes_in_sorted_order_and_emits_only_changes(self):
        state = self.create_nodes(256)
        ids = sorted(state['nodes'])
        with patch.object(integration, 'observe_node', wraps=integration.observe_node) as observe:
            try:
                self.invoke('refresh', {'node_ids': list(reversed(ids))})
            except SearchError as error:
                self.fail('A valid full refresh batch was refused: ' + error.code)
        self.assertEqual([call.args[2]['id'] for call in observe.call_args_list], ids)
        event = self.controller.store.read()['events'][-1]
        self.assertEqual(len(event['payload']['operations']), 256)
        self.assertTrue(all(op['kind'] == 'node_facts_recorded' for op in event['payload']['operations']))
        self.invoke('refresh', {'node_ids': ids})
        self.assertEqual(self.controller.store.read()['events'][-1]['payload']['operations'], [])

    def test_refresh_rejects_invalid_batches_before_observing_or_committing(self):
        self.create_nodes(257)
        ids = sorted(self.read_state()['nodes'])
        for spec in ({'node_ids': []}, {'node_ids': ids}, {'node_ids': ids[:1] * 2},
                     {'node_ids': [ids[0], 'unknown']}, {'node_ids': ids[:1], 'extra': True}, {'node_ids': 'node-000001'}):
            before = self.controller.store.tree_path.read_bytes()
            with self.subTest(spec=spec), patch.object(integration, 'observe_node') as observe:
                with self.assertRaises(SearchError) as caught:
                    self.invoke('refresh', spec)
                self.assertNotEqual(caught.exception.code, 'invalid_command', 'Refresh must validate its closed node batch')
                observe.assert_not_called()
            self.assertEqual(self.controller.store.tree_path.read_bytes(), before)

    def test_refresh_rejects_imported_history(self):
        self.create_nodes(1)
        subject = {'attack_slug': 'legacy', 'claim': contract()['original_claim'],
            'target_obligation': 'obligation-000001', 'logical_predecessor': None,
            'snapshot_digest': 'a' * 64, 'snapshot_paths': ['journal.jsonl'], 'usage': {'moves': 0, 'runs': 0}}
        approved = provenance('import-reviewer')
        result_review = {'subject_digest': digest(subject), 'claim_digest': digest(subject['claim']),
                        'reviewer': approved, 'decision': 'approve',
                        'findings': {key: 'Checked historical scope' for key in ('statement', 'assumptions', 'scope', 'dependencies', 'policy')}}
        self.controller.store.append('legacy_imported', {'subject': subject, 'review': result_review,
            'native_parent': None, 'finished': False, 'journal_moves': 0}, self.read_state()['revision'], 'import')
        before = self.controller.store.tree_path.read_bytes()
        with patch.object(integration, 'observe_node') as observe, self.assertRaises(SearchError) as caught:
            self.invoke('refresh', {'node_ids': ['node-000001', 'node-000002']})
        self.assertEqual(caught.exception.code, 'admission_required')
        observe.assert_not_called()
        self.assertEqual(self.controller.store.tree_path.read_bytes(), before)

    def test_refresh_observation_failure_commits_no_partial_batch(self):
        self.create_nodes(2)
        before = self.controller.store.tree_path.read_bytes()
        observe = integration.observe_node

        def fail_second(controller, state, node, content=None):
            if node['id'] == 'node-000002':
                raise OSError('Second observation failed')
            return observe(controller, state, node, content)

        with patch.object(integration, 'observe_node', side_effect=fail_second) as observed:
            with self.assertRaises((SearchError, OSError)):
                self.invoke('refresh', {'node_ids': ['node-000001', 'node-000002']})
        self.assertEqual(observed.call_count, 2)
        self.assertEqual(self.controller.store.tree_path.read_bytes(), before)

    def test_stop_overflow_returns_255_ids_without_continuation_charge_and_refresh_converges(self):
        state = self.create_nodes(257)
        ids = sorted(state['nodes'])
        self.invoke('focus', {'focus': 'focused', 'session_id': 'codex:refresh-test', 'provenance': provenance('operator')})
        before = self.controller.store.tree_path.read_bytes()
        spec = {'delivery_id': 'one-stop', 'session_id': 'codex:refresh-test', 'turn_id': None, 'stop_hook_active': None}
        with patch.object(integration, 'observe_node', wraps=integration.observe_node) as observe:
            with self.assertRaises(SearchError) as caught:
                self.invoke('hook-stop', spec, request='stop')
        self.assertEqual(caught.exception.code, 'recovery_required')
        self.assertEqual(caught.exception.details, {'node_ids': ids[:255], 'has_more': True})
        self.assertEqual(observe.call_count, 256)
        self.assertEqual(self.controller.store.tree_path.read_bytes(), before)
        self.assertEqual(self.read_state()['control']['stop_count'], 0)
        self.invoke('refresh', {'node_ids': caught.exception.details['node_ids']})
        self.invoke('hook-stop', spec, request='stop')
        state = self.read_state()
        self.assertEqual(state['control']['stop_count'], 1)
        self.assertEqual(set(state['control']['node_facts']), set(ids))
        self.invoke('hook-stop', spec, request='duplicate-stop')
        self.assertEqual(self.read_state()['control']['stop_count'], 1)

    def test_refresh_retries_keep_historical_response_and_request_revision_checks(self):
        self.create_nodes(2)
        spec = {'node_ids': ['node-000001']}
        revision = self.read_state()['revision']
        try:
            first = self.controller.command('refresh', spec, revision, 'refresh-once')
        except SearchError as error:
            self.fail('Refresh request semantics are unavailable: ' + error.code)
        self.invoke('pause', {'reason': 'Separate later event'})
        with patch.object(integration, 'observe_node', side_effect=AssertionError('A retry must not observe')):
            self.assertEqual(self.controller.command('refresh', spec, 999, 'refresh-once'), first)
        before = self.controller.store.tree_path.read_bytes()
        for changed, request, expected, code in (({'node_ids': ['node-000002']}, 'refresh-once', revision, 'request_id_conflict'),
                                                 (spec, 'stale-refresh', revision, 'revision_conflict')):
            with self.assertRaises(SearchError) as caught:
                self.controller.command('refresh', changed, expected, request)
            self.assertEqual(caught.exception.code, code)
        self.assertEqual(self.controller.store.tree_path.read_bytes(), before)

    def test_refresh_preserves_native_and_pending_move_recovery_precedence(self):
        self.create_nodes(1)
        context = self.start_native()
        with self.assertRaises(SearchError) as caught:
            self.invoke('refresh', {'node_ids': []})
        self.assertEqual(caught.exception.code, 'recovery_required')
        integration.after_legacy(context, 0)
        node = self.read_state()['nodes']['node-000001']
        reservation = {'id': 'move-node-000001-1', 'node_id': node['id'], 'account_id': node['account_id'],
            'move': 1, 'pass': 1, 'strategy': 'induction', 'entry': 'induction', 'walk': 'induction',
            'trigger_features': [], 'step_cites': [], 'problem_digest': 'a' * 64, 'journal_prefix_digest': 'b' * 64}
        self.controller.store.append('move_reserved', {'reservation': reservation}, self.read_state()['revision'], 'reserve')
        before = self.controller.store.tree_path.read_bytes()
        with patch.object(integration, 'observe_node') as observe, self.assertRaises(SearchError) as caught:
            self.invoke('refresh', {'node_ids': []})
        self.assertEqual(caught.exception.code, 'recovery_required')
        observe.assert_not_called()
        self.assertEqual(self.controller.store.tree_path.read_bytes(), before)

    def test_refresh_cli_writes_json_and_refuses_check_without_mutation(self):
        self.create_nodes(1)
        source = self.root / 'refresh.json'
        source.write_text(json.dumps({'node_ids': ['node-000001']}))
        arguments = ['--attack-root', str(self.root), 'search', 'refresh', '--spec', str(source),
                     '--expected-revision', str(self.read_state()['revision']), '--request-id', 'cli-refresh', '--json']
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            result = attack.main(arguments)
        self.assertEqual(result, 0, err.getvalue())
        self.assertEqual(json.loads(out.getvalue())['command'], 'refresh')
        before = self.controller.store.tree_path.read_bytes()
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()) as err:
            self.assertEqual(attack.main(arguments + ['--check']), 1)
        self.assertEqual(json.loads(err.getvalue())['error']['code'], 'check_unsupported')
        self.assertEqual(self.controller.store.tree_path.read_bytes(), before)


if __name__ == '__main__':
    unittest.main()
