"""Every reader that rebuilds the payload of a started owned call gives back the payload of its start.

The readers are the record check of its capture, the step that a review stop publishes, the plan of its head, the call
that the step runs and the staged task run that resumes a recorded dispatch. Each test adds a member to the definition
of its call kind, as a later release adds an optional member, and every reader carries it with no change of its own."""

import json
import unittest
from unittest.mock import Mock, patch

from research_harness import cli, record_check, review_continuation, review_output_correction as correction
from research_harness import review_protocol as protocol, review_staged_protocol as staged, review_stop
from research_harness import review_transport as transport
from research_harness.errors import ResearchError
from research_harness.review_budget import CALL_OPERATIONS
from research_harness.review_task_execution import identify_call, read_call_kinds, rebuild_call_payload
import test_research_cli as cli_fixtures
import test_research_review_protocol as review_fixtures
import test_review_capacity_probe as capacity_fixtures
import test_review_output_correction as correction_fixtures
import test_review_stop_queue as stop_fixtures

# The definition of each call kind: the module that starts the call and the name of its constant.
DEFINITIONS = {'review.invoke': (protocol, 'INVOKE_PAYLOAD_FIELDS'),
               'review.route_probe': (protocol, 'PROBE_PAYLOAD_FIELDS'),
               'review.output_correction': (correction, 'PAYLOAD_FIELDS')}
# A valid value of each optional member of today's definitions. A member added to a definition needs a value here.
OPTIONAL_VALUES = {('review.route_probe', 'capacity'): {'input_tokens': 25000}}
ADDED_MEMBER = 'added_optional_member'


def add_member(operation):
    """Add ADDED_MEMBER to the optional members of the definition of the operation."""
    module, name = DEFINITIONS[operation]
    required, optional = getattr(module, name)
    return patch.object(module, name, (required, optional + (ADDED_MEMBER,)))


def build_payload(operation, required_values):
    """The payload with every optional member of today's definition and the added member."""
    module, name = DEFINITIONS[operation]
    payload = dict(required_values)
    payload.update((member, OPTIONAL_VALUES[(operation, member)]) for member in getattr(module, name)[1])
    payload[ADDED_MEMBER] = 'kept by every rebuild'
    return payload


class CallPayloadRebuildTests(unittest.TestCase):
    def use_fixture(self, case):
        self.addCleanup(case.doCleanups)
        case.setUp()
        return case

    def start_and_lose_finish(self, start, store, payload, request_id, response):
        """Start one owned call whose finish is lost after its capture commits."""
        with patch.object(transport, 'send_request', side_effect=response), \
                patch.object(protocol, '_finish_call', side_effect=RuntimeError('Lost after capture')):
            with self.assertRaisesRegex(RuntimeError, 'Lost after capture'):
                start(store, payload, expected_revision=store.revision, request_id=request_id, credential='test-only')

    def assert_every_reader_rebuilds(self, store, artifacts, operation, payload, request_id, assignment_id=None):
        records = store.snapshot()['records']
        self.assertEqual(store.committed_request(request_id + ':start')['payload'], payload)
        invocation_kind, finished_kind = read_call_kinds(operation)
        self.assertEqual(rebuild_call_payload(operation, records[invocation_kind][payload['id']]), payload)
        call = dict(operation=operation, invocation_id=payload['id'])
        row = review_stop.describe_call(records, call)
        self.assertEqual((row['state'], row['step']['payload'], row['step']['request_id']),
                         ('captured', payload, request_id))
        if assignment_id is not None:
            plan = review_continuation.plan_next(records, artifacts, assignment_id)
            self.assertEqual((plan['state'], plan['payload'], plan['request_id']), ('resume', payload, request_id))
        with patch.object(transport, 'send_request', side_effect=AssertionError('The resume sends nothing')):
            cli.OPERATIONS[row['step']['command']](store, row['step']['payload'],
                request_id=row['step']['request_id'], expected_revision=store.revision)
        records = store.snapshot()['records']
        self.assertEqual(records[finished_kind][payload['id']][ADDED_MEMBER], payload[ADDED_MEMBER])
        self.assertEqual(review_stop.describe_call(records, call)['state'], 'settled')
        self.assertIn(identify_call(operation, payload['id']), record_check.check_authentication(store)['captures'])

    def test_every_call_kind_has_one_definition_and_every_optional_member_a_value(self):
        self.assertEqual(set(DEFINITIONS), set(CALL_OPERATIONS))
        optional = {(operation, member) for operation, (module, name) in DEFINITIONS.items()
                    for member in getattr(module, name)[1]}
        self.assertEqual(optional, set(OPTIONAL_VALUES))

    def test_route_probe_readers_rebuild_every_member(self):
        case = self.use_fixture(capacity_fixtures.CapacityProbeTests())
        case.route()
        def send(route, request, credential):
            packet = json.loads(request['input'][0]['content'][len(transport.PACKET_LABEL):])
            response = case.response(dict(allowed_control=packet['allowed_control'],
                                          evidence_control=packet['evidence_control'], excluded_controls=[]))
            response['usage']['input_tokens'] = OPTIONAL_VALUES[('review.route_probe', 'capacity')]['input_tokens']
            return response
        payload = build_payload('review.route_probe', dict(id='probe', route_id='route'))
        with add_member('review.route_probe'):
            self.start_and_lose_finish(protocol.probe_route, case.store, payload, 'probe-call', send)
            self.assert_every_reader_rebuilds(case.store, case.artifacts, 'review.route_probe', payload, 'probe-call')

    def test_invoke_readers_rebuild_every_member(self):
        case = self.use_fixture(review_fixtures.ReviewProtocolTests())
        case.route()
        case.run_probe()
        case.assign()
        payload = build_payload('review.invoke', dict(id='attempt', assignment_id='assignment'))
        with add_member('review.invoke'):
            self.start_and_lose_finish(protocol.invoke_assignment, case.store, payload, 'invoke-call',
                                       lambda route, request, credential: case.response({'verdict': 'sound'}))
            self.assert_every_reader_rebuilds(case.store, case.artifacts, 'review.invoke', payload, 'invoke-call',
                                              assignment_id='assignment')

    def test_output_correction_readers_rebuild_every_member(self):
        case = self.use_fixture(correction_fixtures.CorrectionTests())
        case.prepare_refused()
        assignment_id = case.case.assignment['id']
        payload = build_payload('review.output_correction',
                                dict(id='correction', assignment_id=assignment_id, route_id='route'))
        with add_member('review.output_correction'):
            self.start_and_lose_finish(correction.invoke_correction, case.fixture.store, payload, 'correction-call',
                                       lambda route, request, credential: case.fixture.build_provider_response(case.delta()))
            self.assert_every_reader_rebuilds(case.fixture.store, case.fixture.artifacts, 'review.output_correction',
                                              payload, 'correction-call', assignment_id=assignment_id)

    def test_staged_dispatch_step_rebuilds_every_member(self):
        case = self.use_fixture(stop_fixtures.StagedStopTests())
        reservation = case.reserve(request_id='original:reserve')['result']
        payload = build_payload('review.invoke', dict(id='attempt', assignment_id='first'))
        with add_member('review.invoke'):
            case.put('review_task_dispatch', 'original', dict(payload, task_id='a'))
            case.put('review_observation', reservation['id'], dict(reservation_id=reservation['id'],
                     observation=dict(status='failed', output=None)))
            case.put('review_reservation', reservation['id'], dict(reservation, released=True, state='captured',
                     observation_id=reservation['id']))
            row = review_stop.describe_call(case.store.snapshot()['records'],
                                            dict(operation='review.task_reserve', reservation_id=reservation['id']))
        self.assertEqual(row['state'], 'captured')
        self.assertEqual(row['step'], dict(command='review-run', request_id='original', payload=payload))

    def test_staged_task_run_rebuilds_a_recorded_dispatch(self):
        records, _, assigned = cli_fixtures.StagedReviewCliTests().build_records(
            {'state': 'captured', 'attempts': 1, 'reservation_id': 'reservation'})
        records['review_observation'] = {'reservation': {'observation': {'status': 'completed'}}}
        dispatched = build_payload('review.invoke', dict(id='capture-attempt', assignment_id='child'))
        dispatches = {'original': dict(dispatched, task_id=assigned['id'])}
        records['review_task_dispatch'] = dispatches
        store = Mock()
        store.snapshot.return_value = {'records': records}
        store.committed_request.side_effect = lambda key: ({'operation': 'review.task_reserve', 'payload': {},
            'response': {'revision': 9, 'result': {'id': 'reservation'}}} if key == 'original:reserve' else None)
        task_run = dict(assignment_id='assignment-A', epoch_id='epoch-A', task_id=assigned['id'])
        accept_run = dict(task_run, action='accept')
        sent = {}
        with add_member('review.invoke'), patch.object(protocol, 'invoke_assignment', return_value={'revision': 9}) as invoke:
            # Under the dispatch's own request id (the planned resume) and under another one (the owner of the capture).
            for request_id in ('original', 'resume-accept'):
                cli._review_run(store, accept_run, expected_revision=8, request_id=request_id)
                sent[request_id] = invoke.call_args.args[1]
            del records['review_task_dispatch']
            cli._review_run(store, accept_run, expected_revision=8, request_id='new-dispatch')
            sent['new-dispatch'] = invoke.call_args.args[1]
            # A run repeated under its own request id after its capture failed: the reservation is released and its
            # observation failed, so no dispatch owns the reservation, and only the dispatch of the request id matches.
            store.snapshot.return_value = {'records': dict(records, review_task_dispatch=dispatches,
                review_reservation={'reservation': {'id': 'reservation', 'released': True, 'state': 'captured'}},
                review_observation={'reservation': {'observation': {'status': 'failed'}}})}
            cli._review_run(store, task_run, expected_revision=8, request_id='original')
            sent['repeated'] = invoke.call_args.args[1]
        self.assertEqual(sent['original'], dispatched)
        self.assertEqual(sent['resume-accept'], dispatched)
        self.assertEqual(sent['repeated'], dispatched)
        # A run that starts a new dispatch cannot carry a call member: it sends the required members only.
        self.assertEqual(set(sent['new-dispatch']), set(protocol.INVOKE_PAYLOAD_FIELDS[0]))

    def test_staged_task_run_takes_only_the_recorded_dispatch_of_its_own_attempt(self):
        # A run under the request id of a recorded dispatch of another attempt, or of another assignment, does not take
        # that dispatch: it sends its own id and assignment, and the replay of the dispatch then refuses it as before.
        for other in (dict(id='first-attempt', assignment_id='child'), dict(id='retry-attempt', assignment_id='other')):
            with self.subTest(recorded=other):
                records, _, assigned = cli_fixtures.StagedReviewCliTests().build_records(
                    {'state': 'unknown', 'attempts': 1, 'reservation_id': 'dead'})
                records['review_reservation'] = {'dead': {'released': True, 'observation_id': None}}
                records['review_task_dispatch'] = {'first': dict(build_payload('review.invoke', other),
                                                                 task_id=assigned['id'])}
                store = Mock()
                store.snapshot.return_value = {'records': records}
                store.committed_request.return_value = None
                task_run = dict(assignment_id='assignment-A', epoch_id='epoch-A', task_id=assigned['id'],
                                id='retry-attempt')
                with add_member('review.invoke'), \
                        patch.object(protocol, 'invoke_assignment', return_value={'revision': 9}) as invoke:
                    cli._review_run(store, task_run, expected_revision=8, request_id='first')
                self.assertEqual(invoke.call_args.args[1], dict(id='retry-attempt', assignment_id='child'))

    def test_staged_start_check_reads_the_definition(self):
        # A literal key list in the staged start refuses the added member with invalid_input before it reads the store.
        case = self.use_fixture(stop_fixtures.StagedStopTests())
        payload = build_payload('review.invoke', dict(id='attempt', assignment_id='no-such-assignment'))
        with add_member('review.invoke'):
            with self.assertRaises(ResearchError) as refused:
                staged.invoke_task(case.store, payload, expected_revision=case.store.revision, request_id='staged-start')
        self.assertEqual(refused.exception.code, 'review_record_missing')


if __name__ == '__main__':
    unittest.main()
