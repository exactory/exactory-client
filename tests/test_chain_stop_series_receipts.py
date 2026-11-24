"""Retained command refusals for a bounded reading chain."""

from copy import deepcopy
import unittest
from unittest.mock import Mock, patch

from codex_route_fixtures import probe_codex_route
import continuity_forms_fixtures as forms
import openness_reading_fixtures as reading_fixtures
import test_chain_stop_math_successor as successor_fixtures
import test_openness_chain_content_stop as chain_fixtures
import test_openness_reading_math_v2 as math_fixtures
import test_research_problem_openness_minimum as minimum_fixtures
from research_harness import cli, review_call_admission as admission, review_protocol as protocol, review_series
from research_harness import review_task_execution as execution
from research_harness.errors import ResearchError
from research_harness.openness_reading_v7 import require_current_units
from research_harness.review_continuation import plan_next
from research_harness.review_stop import build_chain_scope


def assert_command_step(case, ending, command, *, next_plan=None):
    refusal = ending['error']['details'] if next_plan is None else next_plan['details']
    step = refusal['step']
    case.assertEqual(step['command'], command)
    argument, value = ('--attack-root', step['root']) if command.startswith('search ') else (
        '--workspace', str(case.root.resolve()))
    arguments = dict(step.get('arguments', {}))
    arguments.setdefault(argument, value)
    expected = dict(step, arguments=arguments)
    case.assertEqual(ending['state'], 'handoff')
    case.assertEqual(ending['command'], command)
    case.assertNotIn('payload', ending)
    if next_plan is None:
        case.assertNotIn('next', ending['details'])
    else:
        case.assertEqual(ending['details']['next'], next_plan)
    case.assertEqual(ending['details']['step'], expected)
    retained = case.store.snapshot()['records']['review_series_stop'][ending['stop_id']]['plan']
    case.assertEqual(retained['command'], command)
    case.assertEqual(retained['details']['step'], expected)
    case.assertEqual(retained['details'].get('next'), next_plan)
    case.assertEqual(retained['error'], ending['error'])


def assert_refusal_ending(case, plan, error, command, *, next_plan=None):
    rows = []
    original = deepcopy(error.as_dict())
    code = review_series._end_series(case.store, plan, error=error, exit_code=3, output=rows.append)
    case.assertEqual(code, 3)
    ending, = rows
    case.assertEqual(ending['code'], error.code)
    assert_command_step(case, ending, command, next_plan=next_plan)
    case.assertEqual(ending['error'], original)


def run_queued_series(case, change):
    scope = build_chain_scope(case.store.snapshot()['records'], 'new')
    admit = execution.admit_review_call

    def deny_capacity(transaction, operation, invocation_id, route_id, *, invocation):
        if operation == 'review.route_probe' and invocation_id == 'failed-probe':
            # Simulate a probe already admitted when the review queued.
            return
        if operation == 'review.invoke' and invocation['assignment_id'] == 'new' and not clock.sleep.called:
            call = dict(operation=operation, invocation_id=invocation_id, route_id=route_id,
                        scope=scope, assignment_id='new', epoch_id=None, task_id=None)
            raise ResearchError('review_capacity_wait', 'Synthetic occupied capacity.',
                                dict(call=call, wait_seconds=30))
        return admit(transaction, operation, invocation_id, route_id, invocation=invocation)

    def wake(_seconds):
        wait, = case.store.snapshot()['records']['review_call_wait'].values()
        case.assertEqual(wait['state'], 'waiting')
        change()

    clock = Mock(wraps=admission.time)
    clock.monotonic.return_value = 0
    clock.sleep.side_effect = wake
    rows = []
    with patch.object(execution, 'admit_review_call', side_effect=deny_capacity), \
            patch.object(admission, 'time', clock), patch.object(review_series.signal, 'signal'), \
            patch.object(protocol.transport, 'send_request',
                         side_effect=AssertionError('The queued review must never reach the transport.')) as send:
        code = review_series.run_series(case.store, 'new', emit=rows.append)
    case.assertEqual(code, 3)
    clock.sleep.assert_called_once()
    send.assert_not_called()
    records = case.store.snapshot()['records']
    wait, = records['review_call_wait'].values()
    case.assertEqual((wait['state'], wait['reason']), ('withdrawn', 'command_ended'))
    case.assertLess(wait['requested_revision'], records['review_route_probe']['failed-probe']['recorded_revision'])
    case.assertNotIn(wait['invocation_id'], records['review_invocation'])
    return rows[-1]


class CarriedRouteReceiptTests(chain_fixtures.ChainStopSupport, reading_fixtures.OpennessReadingCase):
    chain_route = 'second-route'

    def test_retirement_and_renewal_use_the_refusals_concrete_step(self):
        self.start_carrying_chain()
        plan = self.read_plan('new')
        self.assertEqual((plan['state'], plan['command']), ('run', 'review-run'))
        self.fail_probe()
        chain_fixtures.make_stale(self, 'route')
        retirement = self.refuse_call('new', chain_fixtures.CARRIED)
        self.assertEqual(retirement.details['step']['payload']['assignment_id'], 'turn-1')
        with self.subTest(command='review-context'):
            assert_refusal_ending(self, plan, retirement, 'review-context')

        self.hide_extraction_in_bundle(3)
        chain_fixtures.make_stale(self, 'second-route')
        records = self.store.snapshot()['records']
        # The runtime guard normally refuses first. Exercise C25's refusal directly at the content guard.
        with self.assertRaises(ResearchError) as caught:
            require_current_units(records, self.artifacts, records['review_assignment']['new'])
        self.assertEqual(caught.exception.code, chain_fixtures.OUTDATED)
        self.assertEqual(caught.exception.details['renewal']['lacks'], 'record')
        with self.subTest(command='review-route'):
            assert_refusal_ending(self, plan, caught.exception, 'review-route')
        with self.subTest(command='export'):
            rows = []
            error = ResearchError(chain_fixtures.OUTDATED, 'Synthetic refusal with no step.')
            review_series._end_series(self.store, plan, error=error, exit_code=3, output=rows.append)
            self.assertEqual(rows[0]['command'], 'export')
            self.assertEqual(rows[0]['details']['step']['command'], 'export')

    def test_own_contamination_refusal_retains_its_nested_handoff(self):
        self.start_carrying_chain()
        plan = self.read_plan('new')
        self.fail_probe()
        self.contaminate('new')
        refusal = self.refuse_call('new', 'review_context_contaminated')
        next_plan = refusal.details['next']
        self.assertEqual(next_plan['state'], 'handoff')
        assert_refusal_ending(self, plan, refusal, next_plan['command'], next_plan=next_plan)

    def test_queued_call_rechecks_the_origin_and_retains_its_probe_step(self):
        self.start_carrying_chain()
        ending = run_queued_series(self, self.fail_probe)
        self.assertEqual(ending['code'], chain_fixtures.CARRIED)
        self.assertEqual(ending['details']['refused_action']['command'], 'review-run')
        self.assertEqual(ending['error']['details']['step']['payload'], dict(route_id='route'))
        assert_command_step(self, ending, 'review-probe')


class OwnRouteReceiptTests(chain_fixtures.ChainStopSupport, reading_fixtures.OpennessReadingCase):
    def test_queued_call_retains_the_own_route_probe_from_its_nested_refusal(self):
        self.start_carrying_chain()
        self.assertEqual(self.describe_plan('new'), ('run', 'review-run', None))

        def change():
            self.hide_extraction_in_bundle(3)
            self.fail_probe()

        ending = run_queued_series(self, change)
        self.assertEqual(ending['code'], 'review_context_unverified')
        self.assertEqual(ending['details']['refused_action']['command'], 'review-run')
        next_plan = ending['error']['details']['next']
        self.assertEqual(next_plan['code'], chain_fixtures.OUTDATED)
        self.assertEqual(next_plan['details']['step']['payload'], dict(route_id='route'))
        self.assertEqual(next_plan['details']['step']['then'], [dict(command='review-series', assignment_id='new')])
        assert_command_step(self, ending, 'review-probe', next_plan=next_plan)
        self.assertNotIn('step', ending['details'].get('omitted', {}))
        probe_codex_route(self, 'verified-probe', ending['details']['step']['payload']['route_id'])
        self.assertEqual(self.describe_plan('new'), ('handoff', 'export', chain_fixtures.OUTDATED))

    def test_probe_targets_have_distinct_receipts_and_repeating_the_target_reuses_it(self):
        self.start_carrying_chain()
        self.hide_extraction_in_bundle(3)
        self.fail_probe()

        def read_ending(route_id):
            rows = []
            with patch.object(review_series.signal, 'signal'), \
                    patch.object(protocol.transport, 'send_request',
                                 side_effect=AssertionError('An outdated review must never reach the transport.')) as send:
                self.assertEqual(review_series.run_series(self.store, 'new', emit=rows.append), 3)
            send.assert_not_called()
            ending = rows[-1]
            self.assertEqual((ending['command'], ending['code']), ('review-probe', chain_fixtures.OUTDATED))
            self.assertEqual(ending['details']['step']['payload'], dict(route_id=route_id))
            return ending

        first = read_ending('route')
        chain_fixtures.make_stale(self, 'route')
        plan = self.read_plan('new')
        self.assertEqual(plan['command'], 'review-route')
        self.mutate(cli.OPERATIONS['review-route'], dict(plan['details']['step']['payload'], id='new-route'))
        second = read_ending('new-route')
        self.assertNotEqual(first['stop_id'], second['stop_id'])
        self.assertEqual(read_ending('new-route')['stop_id'], second['stop_id'])
        stops = self.store.snapshot()['records']['review_series_stop']
        for ending, target in ((first, 'route'), (second, 'new-route')):
            retained = stops[ending['stop_id']]['plan']['details']['step']
            self.assertEqual(retained, ending['details']['step'])
            self.assertEqual(retained['payload'], dict(route_id=target))
        probe_codex_route(self, 'renewed-probe', second['details']['step']['payload']['route_id'])
        self.assertEqual(self.describe_plan('new'), ('handoff', 'export', chain_fixtures.OUTDATED))


class MathPacketReceiptTests(math_fixtures.MathV2Support):
    hide_extraction_in_bundle = chain_fixtures.MathContentStopTests.hide_extraction_in_bundle
    attest = chain_fixtures.MathContentStopTests.attest

    def test_math_refusal_retains_the_review_packet_command_and_root(self):
        self.assign('math-1', self.attest(self.build_math_packet(), 'math-1'))
        plan = plan_next(self.store.snapshot()['records'], self.artifacts, 'math-1')
        self.assertEqual((plan['state'], plan['command']), ('run', 'review-run'))
        self.hide_extraction_in_bundle(1)
        with patch.object(protocol.transport, 'send_request',
                          side_effect=AssertionError('The changed review must never reach the transport.')) as send:
            with self.assertRaises(ResearchError) as caught:
                protocol.invoke_assignment(self.store, dict(id='math-attempt', assignment_id='math-1'),
                    credential='fixture', expected_revision=self.store.revision, request_id='math-invocation')
        send.assert_not_called()
        self.assertEqual(caught.exception.code, chain_fixtures.OUTDATED)
        self.assertEqual(caught.exception.details['step']['root'], str(self.root.resolve()))
        assert_refusal_ending(self, plan, caught.exception, 'search review-packet')


class MathMinimumReceiptTests(unittest.TestCase):
    def test_second_material_search_gets_a_distinct_retained_export(self):
        fixture = forms.prepare_unit_fixture(self, successor_fixtures.MathSuccessorSubjectTests,
                                             'test_carried_stop_opens_minimum_rejected_by_the_controller')
        case = fixture.prepare_math_case()
        controller, _, _ = fixture.start_carried_math_chain(case)
        chain_fixtures.ChainStopSupport.contaminate(case, 'old')

        def read_ending():
            rows = []
            with patch.object(review_series.signal, 'signal'), \
                    patch.object(protocol.transport, 'send_request',
                                 side_effect=AssertionError('The carried stop must not send.')) as send:
                self.assertEqual(review_series.run_series(case.store, 'head', emit=rows.append), 3)
            send.assert_not_called()
            ending = rows[-1]
            self.assertEqual((ending['state'], ending['code'], ending['command']),
                             ('handoff', chain_fixtures.CARRIED, 'search review-packet'))
            return ending

        def take_export(step, *, openness=None):
            spec = fixture.choose_controller_inputs(step)
            if openness is not None:
                self.assertIn('openness', step['chooses'])
                self.assertNotIn('openness', spec)
                spec['openness'] = openness
            exported = controller.command('review-packet', spec, None, None)
            plan = fixture.take_math_assignment(case, step, exported['native_packet'])
            self.assertEqual((plan['state'], plan['command']), ('run', 'review-run'))

        form = forms.FormTurn('math-v2', 'reading', case, case, 'head')
        with forms.fork_store(form):
            current = read_ending()
            self.assertNotIn('stale_minimum', current['details'])
            take_export(current['details']['step'])

        record_search = minimum_fixtures.ProblemOpennessMinimumTests.search
        first_search = record_search(case, case.versions)
        case.searches.append(first_search)
        first = read_ending()
        one_search = fixture.build_minimum(case, 'read-1')
        with forms.fork_store(form):
            take_export(first['details']['step'], openness=one_search)

        second_search = record_search(case, case.versions)
        case.searches.append(second_search)
        second = read_ending()
        self.assertNotEqual(first['stop_id'], second['stop_id'])
        self.assertEqual(read_ending()['stop_id'], second['stop_id'])

        receipts = case.store.snapshot()['records']['review_series_stop']
        first_ids = [first_search['id']]
        second_ids = sorted([first_search['id'], second_search['id']])
        for ending, search_ids in ((first, first_ids), (second, second_ids)):
            with self.subTest(search_ids=search_ids):
                retained = receipts[ending['stop_id']]['plan']
                self.assertEqual(retained['details']['stale_minimum'],
                                 dict(cause='openness_search_stale', search_ids=search_ids))
                self.assertEqual(retained['details']['step'], ending['details']['step'])
                self.assertEqual(retained['details']['step']['prerequisites'][0]['search_ids'], search_ids)

        latest_step = receipts[second['stop_id']]['plan']['details']['step']
        with self.assertRaises(fixture.controller_error) as stale:
            take_export(latest_step, openness=one_search)
        self.assertEqual((stale.exception.code, stale.exception.details),
                         ('openness_search_stale', dict(search_ids=[second_search['id']])))
        take_export(latest_step, openness=fixture.build_minimum(case, 'read-1'))
