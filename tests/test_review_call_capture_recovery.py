"""Ordinary instrumented observations survive their original calling process."""

import copy
import json
import subprocess
import sys
import time
from unittest.mock import PropertyMock, patch

from literature_fixtures import LiteratureCase
from research_harness import review_protocol as protocol, review_task_execution as execution
from research_harness import review_output_correction as correction, review_transport as transport
from research_harness.errors import ResearchError
from research_harness.evidence import digest
from research_harness.operations import immutable_record
from research_harness.storage import Store
import test_research_review_protocol as fixtures
import test_review_output_correction as corrections
from test_review_task_execution import TaskExecutionFixture


_PROCESS = r'''
import json, os, sys
from pathlib import Path
from unittest.mock import patch
from research_harness import cli, review_protocol as protocol, review_transport as transport
from research_harness.storage import Store
root, command, payload, request_id, revision, mode, output = json.loads(sys.argv[1])
root, store = Path(root), Store(Path(root))
prepare = protocol._prepare_review_mutation
def stop(operation_store, operation, *args, **kwargs):
    if operation in ('review.invoke', 'review.route_probe', 'review.output_correction'):
        os._exit(73)
    return prepare(operation_store, operation, *args, **kwargs)
def send(route, request, credential):
    if mode == 'resume':
        raise AssertionError('Recovery must never call the transport')
    with (root / 'transport-calls').open('a') as target: target.write(command + '\n')
    if command == 'review-probe':
        packet = json.loads(request['input'][0]['content'][len(transport.PACKET_LABEL):])
        result = {key: packet[key] for key in ('allowed_control', 'evidence_control')}
        result['excluded_controls'] = []
    else:
        result = output
    return {'id':'provider-response', 'model':'fixture-model', 'status':'completed',
        'output':[{'type':'message', 'role':'assistant', 'content':[{'type':'output_text', 'text':json.dumps(result)}]}],
        'usage':{'input_tokens':37, 'output_tokens':12}}
with patch.object(transport, 'send_request', side_effect=send):
    if mode == 'capture':
        with patch.object(protocol, '_prepare_review_mutation', side_effect=stop):
            cli.OPERATIONS[command](store, payload, expected_revision=revision, request_id=request_id)
    else:
        result = cli.OPERATIONS[command](store, payload, expected_revision=revision, request_id=request_id)
        print(json.dumps(result))
'''


class CallCaptureRecoveryTests(LiteratureCase):
    protocol = fixtures.ReviewProtocolTests.protocol
    seed = fixtures.ReviewProtocolTests.seed
    route = fixtures.ReviewProtocolTests.route
    response = fixtures.ReviewProtocolTests.response
    run_probe = fixtures.ReviewProtocolTests.run_probe
    assign = fixtures.ReviewProtocolTests.assign
    records = fixtures.ReviewProtocolTests.records

    def setUp(self):
        super().setUp()
        self.route()

    def run_process(self, command, payload, request_id, revision, mode, output=None, root=None):
        result = subprocess.run([sys.executable, '-c', _PROCESS, json.dumps([
            str(root or self.root), command, payload, request_id, revision, mode, output])],
            capture_output=True, text=True, timeout=60)
        self.assertEqual(result.returncode, 73 if mode == 'capture' else 0, result.stderr)
        return None if mode == 'capture' else json.loads(result.stdout)

    def assert_capture(self, store, operation, payload, request_id, *, pending=True):
        records = store.snapshot()['records']
        identifier = execution.identify_call(operation, payload['id'])
        self.assertIn(identifier, records.get('review_call_observation', {}),
                      'The live call must durably capture before scientific finishing')
        captured = records['review_call_observation'][identifier]
        self.assertEqual(captured['operation'], operation)
        self.assertEqual(captured['invocation_id'], payload['id'])
        self.assertEqual(captured['request_id'], request_id)
        self.assertEqual(captured['payload_digest'], digest(payload))
        self.assertEqual(records['review_pool_membership'][identifier]['released'], not pending)
        invocation_kind, _ = execution.read_call_kinds(operation)
        self.assertGreater(captured['recorded_revision'], records[invocation_kind][payload['id']]['recorded_revision'])
        return captured

    def test_another_process_finishes_original_ordinary_call_without_transport(self):
        self.run_probe()
        self.assign()
        payload, request_id = dict(id='ordinary', assignment_id='assignment'), 'recover-ordinary'
        revision = self.store.revision
        self.run_process('review-run', payload, request_id, revision, 'capture', {'finding': 'retained'})
        captured = self.assert_capture(self.store, 'review.invoke', payload, request_id)
        self.assertNotIn('ordinary', self.records().get('review_attempt', {}))
        released = execution.reconcile_call(self.store, dict(operation='review.invoke', invocation_id='ordinary'),
                                            request_id='reconcile-before-recovery')
        self.assertTrue(released['result']['released'])
        with patch.object(protocol, '_observed', side_effect=AssertionError('Do not resample a committed capture')):
            with self.assertRaises(ResearchError) as pending:
                protocol.invoke_assignment(self.store, dict(payload, id='another-output'),
                    expected_revision=self.store.revision, request_id='another-output')
        self.assertEqual(pending.exception.code, 'review_invocation_pending')
        # A captured call is finished by its own command, not by reconciliation or an import.
        self.assertIn('original request id', str(pending.exception))
        self.assertEqual(pending.exception.details['invocation_id'], 'ordinary')
        result = self.run_process('review-run', payload, request_id, revision, 'resume')
        self.assertEqual(result['result']['output'], captured['observation']['output'])
        self.assertEqual(self.records()['review_call_observation'][captured['id']], captured)
        self.assertTrue(self.records()['review_pool_membership'][captured['id']]['released'])
        self.assertEqual((self.root / 'transport-calls').read_text().splitlines(), ['review-run'])
        self.assertEqual(self.run_process('review-run', payload, request_id, revision, 'resume'), result)

    def test_another_process_finishes_original_route_probe_without_transport(self):
        payload, request_id = dict(id='recover-probe', route_id='route'), 'recover-probe'
        revision = self.store.revision
        self.run_process('review-probe', payload, request_id, revision, 'capture')
        captured = self.assert_capture(self.store, 'review.route_probe', payload, request_id)
        with self.assertRaises(ResearchError) as changed:
            protocol.probe_route(self.store, dict(payload, id='different'), expected_revision=revision, request_id=request_id)
        self.assertEqual(changed.exception.code, 'request_id_conflict')
        result = self.run_process('review-probe', payload, request_id, revision, 'resume')
        self.assertEqual(result['result']['output'], captured['observation']['output'])
        self.assertEqual(protocol.route_state(self.records(), self.artifacts, 'route')['context_status'], 'verified_for_route')
        self.assertEqual((self.root / 'transport-calls').read_text().splitlines(), ['review-probe'])

    def test_another_process_finishes_original_output_correction_without_transport(self):
        case = corrections.CorrectionTests()
        self.addCleanup(case.doCleanups)
        case.setUp()
        case.prepare_refused()
        store = case.fixture.store
        payload = dict(id='correction', assignment_id=case.case.assignment['id'], route_id='route')
        revision = store.revision
        self.run_process('review-output-correction', payload, 'recover-correction', revision, 'capture',
                         case.delta(), root=case.fixture.root)
        self.assert_capture(store, 'review.output_correction', payload, 'recover-correction')
        result = self.run_process('review-output-correction', payload, 'recover-correction', revision, 'resume',
                                  root=case.fixture.root)
        self.assertEqual(result['result']['correction_status'], 'accepted', result['result']['correction_error'])
        self.assertEqual(json.loads(case.fixture.artifacts.read(result['result']['corrected_output'])), case.corrected())
        self.assertEqual((case.fixture.root / 'transport-calls').read_text().splitlines(), ['review-output-correction'])

    def test_pending_without_capture_remains_unknown_and_reconciliation_records_failure(self):
        payload = dict(id='uncaptured', route_id='route')
        revision = self.store.revision
        with patch.object(protocol, '_observed', side_effect=RuntimeError('Process ended before capture')):
            with self.assertRaisesRegex(RuntimeError, 'before capture'):
                protocol.probe_route(self.store, payload, expected_revision=revision, request_id='uncaptured')
        with patch.object(protocol, '_observed', side_effect=AssertionError('Never resend')):
            with self.assertRaises(ResearchError) as raised:
                protocol.probe_route(self.store, payload, expected_revision=revision, request_id='uncaptured')
        self.assertEqual(raised.exception.code, 'review_invocation_pending')
        self.assertFalse(self.records().get('review_call_observation'))
        result = execution.reconcile_call(self.store, dict(operation='review.route_probe', invocation_id='uncaptured'),
                                          request_id='reconcile-uncaptured')
        self.assertTrue(result['result']['released'])
        self.assertEqual(self.records()['review_route_probe']['uncaptured']['error']['code'], 'review_invocation_interrupted')

    def test_final_receipt_returns_before_changed_capture_dependencies(self):
        self.run_probe()
        payload = dict(id='probe', route_id='route')
        captured = self.assert_capture(self.store, 'review.route_probe', payload, 'task3-2', pending=False)
        # Do not depend on the fixture's sequence: read the durable original identity.
        request_id = captured['request_id']
        expected = self.store.committed_request(request_id)['response']
        self.seed('review_route', 'route', dict(id='route', adapter='unavailable'))
        with patch.object(protocol, '_observed', side_effect=AssertionError('Never resend')):
            result = protocol.probe_route(self.store, payload, expected_revision=0, request_id=request_id)
        self.assertEqual(result, expected)


class CallCapturePreparationTests(TaskExecutionFixture):
    def invoke(self, finish, request_id='ordinary', observe=None):
        payload = dict(id='ordinary', route_id='route')
        def start(records, value):
            row = dict(value, request=self.request)
            return [immutable_record(records, 'review_invocation', 'ordinary', row)], row
        with patch.object(protocol, '_observed', side_effect=observe or (lambda *args:
                          dict(status='failed', request=self.request, response=None, output=None))):
            return protocol._invoke_once(self.store, payload, 'review.invoke', start, finish,
                expected_revision=0 if self.store.committed_request(request_id + ':start') else self.store.revision,
                request_id=request_id, credential=None)

    def finish(self, records, value, invocation, observation, revision):
        self.assertIn(execution.identify_call('review.invoke', 'ordinary'), records.get('review_call_observation', {}),
                      'Scientific finishing must consume a committed capture')
        row = dict(invocation, **observation, recorded_revision=revision)
        return [immutable_record(records, 'review_attempt', 'ordinary', row)], row

    def test_unrelated_commit_does_not_repeat_scientific_finishing_preparation(self):
        write, finished = self.store.mutate_scoped, []
        def finish(*args):
            finished.append(True)
            return self.finish(*args)
        def intervene(operation, *args, **kwargs):
            if operation == 'review.invoke':
                self.put('unrelated', 'committed', dict(value=True))
            return write(operation, *args, **kwargs)
        with patch.object(self.store, 'mutate_scoped', side_effect=intervene):
            result = self.invoke(finish)
        self.assertEqual(finished, [True])
        self.assertEqual(result['result']['recorded_revision'], result['revision'])

    def test_finishing_does_not_validate_standalone_revision(self):
        def observe(*args):
            guard = patch.object(Store, 'revision', new_callable=PropertyMock,
                                 side_effect=AssertionError('Finishing must own its preparation revision'))
            guard.start()
            self.addCleanup(guard.stop)
            return dict(status='failed', request=self.request, response=None, output=None)
        self.invoke(self.finish, observe=observe)

    def test_capture_survives_scientific_finishing_refusal_and_reconciliation(self):
        def refuse(*args):
            raise ResearchError('scientific_refusal', 'Finishing refused')
        with self.assertRaises(ResearchError):
            self.invoke(refuse)
        records = self.store.snapshot()['records']
        identifier = execution.identify_call('review.invoke', 'ordinary')
        self.assertIn(identifier, records.get('review_call_observation', {}), 'Capture must survive finishing refusal')
        captured = records['review_call_observation'][identifier]
        result = execution.reconcile_call(self.store, dict(operation='review.invoke', invocation_id='ordinary'),
                                          request_id='reconcile-captured')
        self.assertTrue(result['result']['released'])
        self.assertNotIn('ordinary', self.store.snapshot()['records'].get('review_attempt', {}))
        self.put('review_assignment', 'assignment', dict(id='assignment'))
        self.refuse('review_invocation_pending', lambda: protocol.record_attempt(self.store,
            dict(id='ordinary', assignment_id='assignment', status='failed', output=None,
                 reason='Manual interruption placeholder', usage={}), expected_revision=self.store.revision,
            request_id='import-after-capture'))
        result = self.invoke(self.finish)
        self.assertEqual(result['result']['request'], captured['observation']['request'])

    def test_route_change_during_call_preserves_original_route_binding(self):
        original_route = self.store.snapshot()['records']['review_route']['route']
        def observe(*args):
            self.put('review_route', 'route', dict(original_route, changed=True))
            return dict(status='failed', request=self.request, response=None, output=None)
        self.invoke(self.finish, observe=observe)
        captured = self.store.snapshot()['records']['review_call_observation'][execution.identify_call('review.invoke', 'ordinary')]
        self.assertEqual(captured['route']['digest'], digest(original_route))

    def test_original_request_id_can_use_capture_prefix(self):
        request_id = 'review-call-capture:' + execution.identify_call('review.invoke', 'ordinary')
        result = self.invoke(self.finish, request_id=request_id)
        self.assertEqual(result['request_id'], request_id)

    def test_capture_receipt_collisions_do_not_lose_the_observation_or_resend(self):
        from research_harness import review_call_capture as capture
        def occupy(request_id):
            self.store.mutate('fixture', {}, lambda tx: None,
                              expected_revision=self.store.revision, request_id=request_id)
        occupy('ordinary:capture')
        mutate, raced, observations = capture._mutate_current, [], []
        def race(*args, **kwargs):
            if kwargs['request_id'] != 'ordinary:capture' and not raced:
                # Another writer occupies the next receipt identity immediately before its serialized write.
                occupy(kwargs['request_id'])
                raced.append(kwargs['request_id'])
            return mutate(*args, **kwargs)
        def observe(*args):
            observations.append(True)
            return dict(status='failed', request=self.request, response=None, output=None)
        with patch.object(capture, '_mutate_current', side_effect=race):
            result = self.invoke(self.finish, observe=observe)
        self.assertEqual(len(raced), 1)
        self.assertEqual(observations, [True])
        self.assertIn(execution.identify_call('review.invoke', 'ordinary'),
                      self.store.snapshot()['records']['review_call_observation'])
        self.assertEqual(self.invoke(self.finish, observe=lambda *args: self.fail('Never resend')), result)

    def test_recovery_rejects_substituted_reservation_identity_and_route(self):
        from research_harness.review_call_capture import read_call_observation
        self.invoke(self.finish)
        original = self.store.snapshot()['records']
        identifier = execution.identify_call('review.invoke', 'ordinary')
        for field in ('id', 'route_id'):
            with self.subTest(field=field):
                changed = copy.deepcopy(original)
                changed['review_pool_membership'][identifier][field] = 'substituted'
                self.refuse('review_invocation_unverified', lambda: read_call_observation(changed,
                    self.store.build_artifact_store(), 'review.invoke', dict(id='ordinary', route_id='route'), 'ordinary'))

    def test_capture_requires_original_live_authority(self):
        from research_harness.review_call_capture import capture_call
        from research_harness.review_worker import own_call
        payload = dict(id='ordinary', route_id='route')
        invocation = dict(payload, request=self.request)
        route = self.store.snapshot()['records']['review_route']['route']
        observation = dict(status='failed', request=self.request, response=None, output=None)
        self.refuse('review_instrumented_capture_required', lambda: capture_call(self.store, 'review.invoke', payload,
            invocation, route, observation, request_id='forged'))
        def stop(*args):
            raise RuntimeError('After admission')
        with self.assertRaises(RuntimeError):
            self.invoke(self.finish, observe=stop)
        # A new owner of the same lock cannot impersonate the original admitted capability.
        with own_call(self.store, 'review.invoke', 'ordinary') as replacement:
            self.refuse('review_instrumented_capture_required', lambda: capture_call(self.store, 'review.invoke', payload,
                invocation, route, observation, request_id='ordinary', _authority=replacement))
        self.assertFalse(self.store.snapshot()['records'].get('review_call_observation'))

    def test_finished_capture_keeps_capacity_until_transport_child_terminates(self):
        child, ready, release = [], self.root / 'child-ready', self.root / 'release-child'
        def observe(*args):
            child.append(subprocess.Popen([sys.executable, '-c',
                'import sys,time\nfrom pathlib import Path\nPath(sys.argv[1]).touch()\n'
                'while not Path(sys.argv[2]).exists(): time.sleep(.01)', str(ready), str(release)],
                start_new_session=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE))
            deadline = time.monotonic() + 30
            while not ready.exists() and time.monotonic() < deadline:
                time.sleep(.01)
            self.assertTrue(ready.exists(), 'The owned transport child must have registered and started')
            return dict(status='failed', request=self.request, response=None, output=None)
        try:
            result = self.invoke(self.finish, observe=observe)
            records = self.store.snapshot()['records']
            identifier = execution.identify_call('review.invoke', 'ordinary')
            self.assertEqual(records['review_attempt']['ordinary'], result['result'])
            self.assertFalse(records['review_pool_membership'][identifier]['released'])
            self.refuse('review_capacity_wait', lambda: self.reserve())
        finally:
            release.touch()
            for process in child:
                _, errors = process.communicate(timeout=60)
                self.assertEqual(process.returncode, 0, errors)
        result = execution.reconcile_call(self.store, dict(operation='review.invoke', invocation_id='ordinary'),
                                          request_id='release-finished')
        self.assertTrue(result['result']['released'])


class NativeCorrectionCaptureTests(LiteratureCase):
    def test_correction_uses_retained_native_rollout_after_source_is_removed(self):
        import test_visual_context_repair as visual
        from research_harness import research_decisions as decisions, review_codex_transport as codex
        fixture = visual.VisualContextRepairTests()
        self.addCleanup(fixture.doCleanups)
        fixture.setUp()
        case, artifacts = fixture.case, fixture.artifacts
        assignment = fixture.assign()
        packet = json.loads(artifacts.read(assignment['packet']))
        original = copy.deepcopy(fixture.output)
        original['value']['reason'] = 'The retained scientific wording differs from the prior review.'
        original['reassessment'] = dict(assignment_id=fixture.prior['id'], findings=[
            dict(review_id=row['id'], disposition='confirmed', reason='Exact comparison is needed.',
                 evidence=[dict(kind='source', link=fixture.fixture.pdf_link)]) for row in packet['historical_reviews']])
        for identifier in ('native-one', 'native-two'):
            with patch.object(transport, 'send_request', side_effect=lambda route, request, credential:
                              fixture.fixture.response(route, request, original)):
                case.mutate(protocol.invoke_assignment, dict(id=identifier, assignment_id=assignment['id']), credential='test')
            with self.assertRaises(ResearchError):
                case.mutate(decisions.record_value_review, dict(original, id='final', dossier_id=None, assignment_id=assignment['id']))
        delta = dict(source_output_digest=digest(original), findings=[dict(review_id=row['review_id'],
            disposition='revised', reason='The scientific wording differs.') for row in original['reassessment']['findings']])
        source = case.root / 'original-rollout.json'
        def respond(route, request, credential):
            response = case.build_provider_response(delta)
            events = [dict(type='session_meta', payload=dict(id=response['id'], source='exec',
                base_instructions=dict(text=request['instructions']))),
                dict(type='turn_context', payload=dict(model=route['model'], effort='high', cwd='/fixture')),
                dict(type='response_item', payload=dict(type='message', role='user',
                    content=[dict(type='input_text', text=codex.packet_text(request))])),
                dict(type='response_item', payload=copy.deepcopy(response['output'][0]))]
            evidence = codex.inspect_rollout(events, request, response['id'], '/fixture', [])
            evidence.update(inspected=True, route_digest=digest(route), runtime=codex.runtime(), event_digest=digest(events))
            response['context_evidence'] = evidence
            source.write_text(json.dumps(events))
            return response
        prepare = protocol._prepare_review_mutation
        def stop(store, operation, *args, **kwargs):
            if operation == 'review.output_correction':
                raise RuntimeError('Stopped after capture')
            return prepare(store, operation, *args, **kwargs)
        payload = dict(id='native-correction', assignment_id=assignment['id'], route_id='route')
        revision = case.store.revision
        with patch.object(transport, 'send_request', side_effect=respond), \
                patch.object(codex, '_rollout', side_effect=lambda *args: json.loads(source.read_text())), \
                patch.object(protocol, '_prepare_review_mutation', side_effect=stop):
            with self.assertRaisesRegex(RuntimeError, 'after capture'):
                correction.invoke_correction(case.store, payload, expected_revision=revision, request_id='native-correction')
        captured = case.read_records().get('review_call_observation', {}).get(execution.identify_call(
            'review.output_correction', payload['id']))
        self.assertIsNotNone(captured, 'Native evidence must be retained before scientific finishing')
        source.unlink()
        with patch.object(transport, 'send_request', side_effect=AssertionError('Never resend')), \
                patch.object(codex, '_rollout', side_effect=AssertionError('Original rollout is gone')):
            result = correction.invoke_correction(Store(case.root), payload, expected_revision=revision,
                                                   request_id='native-correction')['result']
        self.assertEqual(result['correction_status'], 'accepted', result['correction_error'])
        self.assertEqual(result['native_rollout'], captured['retained_evidence']['native_rollout'])
