"""Regression cases for the release review of prospective context checks."""

import json
import unittest
from unittest.mock import Mock, patch

import test_call_context_present as context
import test_openness_reading_v7_refusal as refusal
import test_openness_reading_v7_unchecked_answer as unchecked
import test_review_route_plan as route_plan
from openness_reading_fixtures import OpennessReadingCase
from research_harness import cli, openness_reading as reading, openness_reading_v7 as v7
from research_harness import review_call_admission as admission, review_context_repair, review_output_correction
from research_harness import review_protocol as protocol, review_series
from research_harness import review_task_execution as execution
from research_harness.errors import ResearchError
from research_harness.review_continuation import annotate_review_obligations
from research_harness.review_plan_steps import complete_plan
from research_harness.review_stop import build_chain_scope


def apply_step(case, step, **choices):
    return case.mutate(cli.OPERATIONS[step['command']], dict(step['payload'], **choices))['result']


def take_probe(case, plan, identifier='verifying-probe'):
    step = plan['details']['step']
    case.assertEqual(step['command'], 'review-probe')
    context.complete_probe(case, step['payload']['route_id'], identifier)


def answer_with_excluded_control(case, identifier='excluded-probe'):
    def respond(route, request, credential):
        invocation = case.read_records()['review_probe_invocation'][identifier]
        controls = protocol._read_json(case.artifacts, invocation['controls'])
        packet = json.loads(request['input'][0]['content'][len(protocol.transport.PACKET_LABEL):])
        return context.build_response(route, request, dict(allowed_control=packet['allowed_control'],
            evidence_control=packet['evidence_control'], excluded_controls=[next(iter(controls.values()))]))
    with patch.object(protocol.transport, 'send_request', side_effect=respond):
        case.mutate(protocol.probe_route, dict(id=identifier, route_id='route'), credential='fixture')


def take_retirement(case, plan, identifier='retirement'):
    step = plan['details']['step']
    case.assertEqual(step['command'], 'review-context')
    evidence = case.artifacts.put(b'A synthetic instruction retiring the route.', 'text/plain')
    return apply_step(case, step, id=identifier, reason='The stale route is retired.', evidence=[evidence])


class ReplacementContextTests(context.V7ChainSupport, OpennessReadingCase):
    def test_refusal_replacement_discards_the_exposed_position_and_records_a_finding(self):
        """Drive through the remaining reading turns to prove that the replacement's finding counts."""
        self.start_two_turns()
        self.run_turn('new-t2', dict(stage='reading', notes=42))
        before = context.read_plan(self, 'new-t2')
        self.assertEqual((before['state'], before['command']), ('replace', 'review-assignment'))
        context.contaminate(self, 'new-t2')
        plan = context.read_plan(self, 'new-t2')
        self.assertEqual((plan['state'], plan['command'], plan['payload']),
                         ('replace', 'review-assignment', before['payload']))
        replaced = self.mutate(protocol.record_assignment, plan['payload'])['result']
        self.assertEqual([row['id'] for row in protocol.list_review_turns(context.read_records(self), replaced)],
                         ['new', replaced['id']])
        with patch.object(review_series.signal, 'signal'):
            code, _, calls = self.drive(replaced['id'])
        # The count of reviewer calls is not the subject: 3 on d3112e9b, 5 after the merge with track O.
        self.assertEqual(code, 0)
        self.assertTrue(calls)
        self.assertTrue(context.read_records(self).get('problem_openness'))

    def test_replacement_waits_for_verifying_probe_of_its_route(self):
        """Contract. An unanswered probe still prevents replacement on the base."""
        self.start_two_turns()
        self.run_turn('new-t2', dict(stage='reading', notes=42))
        replacement = context.read_plan(self, 'new-t2')
        self.assertEqual(replacement['state'], 'replace')
        context.fail_probe(self, 'second-route')
        blocked = context.read_plan(self, 'new-t2')
        self.assertEqual((blocked['state'], blocked['code']), ('handoff', context.UNVERIFIED))
        take_probe(self, blocked)
        plan = context.read_plan(self, 'new-t2')
        self.assertEqual(plan['payload'], replacement['payload'])
        self.mutate(protocol.record_assignment, plan['payload'])

    def test_status_names_context_turn_alongside_output_pending(self):
        self.start_two_turns()
        self.assertEqual(context.read_plan(self, 'new-t2')['state'], 'run')
        report = cli._build_status_report(self.store)
        self.assertTrue(any(row['code'] == 'review_output_pending' and row.get('assignment_id') == 'new-t2'
                            for row in report['obligations']))
        context.contaminate(self, 'new')
        rows = annotate_review_obligations(context.read_records(self), self.artifacts, report)['obligations']
        rows = [row for row in rows if row.get('assignment_id') == 'new-t2']
        self.assertIn('review_output_pending', [row['code'] for row in rows])
        row, = [row for row in rows if row['code'] == context.CONTAMINATED]
        self.assert_own_turn(row, head='new-t2', turn='new', events=['exposure'])


class FailureOperationContextTests(context.RoleFixtures, unittest.TestCase):
    def test_capacity_replacement_discards_the_exposed_failed_position(self):
        from test_review_output_profile import build_row
        from research_harness import review_route_capacity as capacity

        case = self.start(refusal.V7RefusalTests())
        with patch.object(capacity, 'read_profile_priors', return_value=[
                build_row(level=None, thinking=1000, count=8, over=1)]):
            native = case.export()
            case.assign('turn-1', native)
            case.invoke('turn-1', case.build_v7_answer(case.read_packet('turn-1')))
            head = case.assign('size-position', native, prior='turn-1')['id']
            with patch.object(protocol.transport, 'send_request', side_effect=ResearchError(
                    'review_input_too_large', 'The synthetic input does not fit.')):
                case.mutate(protocol.invoke_assignment, dict(id='size-call', assignment_id=head), credential='fixture')
            original = context.read_plan(case, head)
            self.assertEqual(original['state'], 'replace')
            context.contaminate(case, head)
            plan = context.read_plan(case, head)
            self.assertEqual((plan['state'], plan['code'], plan['payload']),
                             ('replace', 'review_input_too_large', original['payload']))
            replacement = case.mutate(protocol.record_assignment, plan['payload'])['result']
        self.assertEqual(replacement['capacity_replacement_of'], head)

    def test_v5_size_stop_keeps_admissible_route_decision_after_unanswered_probe(self):
        from research_harness import review_claude_transport as claude
        from test_research_claude_transport import clean_stream, completed
        from v7_capacity_fixtures import build_route

        case = self.start(refusal.LegacySuccessorTests())
        head = case.fail_legacy_final()

        def answer_probe(route, request, credential):
            supplied = reading.review_excerpts.read_sent_packet(request)
            output = dict(allowed_control=supplied['allowed_control'], evidence_control=supplied['evidence_control'],
                          excluded_controls=[])
            stream = clean_stream(request, output=json.dumps(output), model=route['model'])
            with patch.object(claude, '_run_owned', return_value=completed(stream)):
                return claude.send_request(route, request, credential)

        with patch.object(reading, 'NEW_PROBLEM_CHAIN_PROTOCOLS', (v7.PROTOCOL, reading.PROTOCOL_V6)), \
                patch.object(protocol.transport, 'CODEX_INPUT_LIMIT_CHARS', 60000):
            context.fail_probe(case, 'route')
            plan = context.read_plan(case, head)
            self.assertEqual((plan['state'], plan['code']), ('stop', 'review_input_too_large'))
            choices = plan['details']['decision']['then']
            # The statement has one target and no smaller statement fits (largest_one_request_targets 0), so the
            # stop offers only the route (row f6 of the continuity guard, lane guard-findings-2).
            self.assertEqual([row['answer'] for row in choices], ['Run the review on another route'])
            route_step, export_step = choices[0]['steps']
            self.assertEqual((route_step['command'], export_step['command']), ('review-route', 'export'))
            route = dict(build_route(effort='xhigh'), id='serving-route')
            with patch.object(claude, '_cli', return_value=('fixture-claude', 'fixture-version')), \
                    patch.object(protocol.transport, 'send_request', side_effect=answer_probe):
                case.mutate(protocol.record_route, route)
                case.mutate(protocol.probe_route, dict(id='serving-probe', route_id='serving-route'),
                            credential='fixture')
                # The successor export is the first export using the new rules.
                case.historical_v5 = False
                reference = case.export(name='recovered-export', assignment_id='recovered')
                case.mutate(protocol.record_assignment, dict(id='recovered', route_id='serving-route', dossier_id=None,
                    role='native_math', reviewer_id='openness-reviewer', author_id='problem-author',
                    context=dict(native_packet=reference)))
                self.assertEqual(context.read_plan(case, 'recovered')['state'], 'run')

    def test_v5_size_stop_hands_off_for_event_on_inherited_turn(self):
        """Contract. An earlier event still calls for an export on the base."""
        case = self.start(refusal.LegacySuccessorTests())
        head = case.fail_legacy_final()
        records = context.read_records(case)
        earlier = protocol.list_review_turns(records, records['review_assignment'][head])[0]['id']
        self.assertNotEqual(earlier, head)
        context.contaminate(case, earlier)
        with patch.object(protocol.transport, 'CODEX_INPUT_LIMIT_CHARS', 60000):
            plan = context.read_plan(case, head)
            self.assertEqual((plan['state'], plan['code'], plan['details']['step']['command']),
                             ('handoff', context.CONTAMINATED, 'export'))
            self.assertEqual(plan['details']['turn']['assignment_id'], earlier)
            case.historical_v5 = False
            self.assertTrue(case.export(name='new-context-packet', assignment_id='new-context'))

    def test_unchecked_answer_replays_committed_request_before_probe(self):
        case = self.start(unchecked.UncheckedAnswerTests())
        case.refuse_turn_1()
        context.fail_probe(case, 'route')
        plan = context.read_plan(case, 'turn-1')
        self.assertEqual((plan['state'], plan['command'], plan['request_id']),
                         ('resume', 'review-run', 'invoke-attempt-turn-1'))
        result = unchecked.run_recorded_request(case, plan['payload'], plan['request_id'])
        self.assertEqual(result['output_refusal']['code'], 'invalid_reading_response')
        following = context.read_plan(case, 'turn-1')
        self.assertEqual((following['state'], following['code']), ('handoff', context.UNVERIFIED))
        take_probe(case, following)
        self.assertEqual(context.read_plan(case, 'turn-1')['state'], 'replace')

    def test_renewed_correction_applies_standing_of_the_destination(self):
        fixture = self.start(route_plan.CorrectionRouteTests())
        fixture.make_stale()
        fixture.origin.record_second_route(fixture.case)
        case = fixture.fixture
        earlier = fixture.assignment['context']['context_repair']['assignment_id']
        route_id = fixture.assignment['route_id']
        lost = dict(assignment_id=earlier, events=[], route=dict(id=route_id, context_status='unverified'), count=1)
        with patch.object(protocol, 'find_unverified_context', return_value=lost), \
                patch.object(protocol.transport, 'send_request', side_effect=AssertionError('No correction may start.')) as send:
            plan = fixture.plan()
            self.assertEqual((plan['state'], plan['code'], plan['details']['turn']),
                             ('handoff', context.UNVERIFIED, lost))
            with self.assertRaises(ResearchError) as caught:
                case.mutate(review_output_correction.invoke_correction, dict(
                    id='blocked-correction', assignment_id=fixture.assignment['id'], route_id='second-route'),
                    credential='fixture')
            send.assert_not_called()
        self.assertEqual((caught.exception.code, caught.exception.details['turn']), (context.UNVERIFIED, lost))
        self.assertFalse(case.read_records().get('review_output_correction_invocation'))
        retirement = take_retirement(case, plan)
        self.assertEqual(retirement['assignment_id'], earlier)


class ContextStepTests(context.RoleFixtures, unittest.TestCase):
    def test_excluded_control_without_event_plans_verifying_probe(self):
        case, head = self.build_bar()
        answer_with_excluded_control(case)
        plan = context.read_plan(case, head)
        self.assertEqual((plan['state'], plan['code'], plan['details']['turn']['events']),
                         ('handoff', context.CONTAMINATED, []))
        take_probe(case, plan)
        self.assertEqual(context.read_plan(case, head)['state'], 'run')

    def check_retirement(self, *, other_slot=False):
        case, head = self.build_bar()
        if other_slot:
            other = case.assign('bar', 'second', **case.deliver_supporting('second'))
        answer_with_excluded_control(case)
        route = case.read_records()['review_route']['route']
        case.mutate(protocol.record_route, dict(id='renewed', **{
            key: route[key] for key in ('adapter', 'model', 'endpoint', 'configuration')}))
        installed = protocol.transport.runtime

        def read_runtime(current=None):
            runtime = installed(current)
            if current and current['id'] == 'route':
                return dict(runtime, implementation_sha256='0' * 64)
            return runtime

        with patch.object(protocol.transport, 'runtime', side_effect=read_runtime):
            if other_slot:
                take_retirement(case, dict(details=dict(step=dict(command='review-context', payload=dict(
                    assignment_id=other['id'], kind='route_retired', source='owner')))), 'other-retirement')
            plan = context.read_plan(case, head)
            self.assertEqual((plan['state'], plan['code']), ('handoff', context.CONTAMINATED))
            retirement = take_retirement(case, plan)
            self.assertEqual(retirement['assignment_id'], head)
            self.assertEqual(plan['details']['steps'][1], dict(command='review-series', assignment_id=head))
            following = context.read_plan(case, head)
            step = following['details']['step']
            self.assertEqual((step['id'], step['command']), (1, 'review-assignment'))
            repair = step['payload']['context']['context_repair']
            self.assertEqual(repair['event_ids'], ['retirement'])
            context.complete_probe(case, 'renewed')
            recorded = apply_step(case, step, id='bar-repair', route_id='renewed', reviewer_id='repairer',
                context=dict(context_repair=dict(repair, probe_id='completed-probe', reason='Repair the exposed slot.')))
            self.assertEqual(recorded['context']['context_repair']['event_ids'], ['retirement'])

    def test_stale_excluded_control_without_event_retires_the_named_turn(self):
        self.check_retirement()

    def test_retirement_elsewhere_requires_a_cause_in_this_slot(self):
        self.check_retirement(other_slot=True)

    def test_ordinary_adjudicator_takes_step_three(self):
        case, head = self.build_adjudicator()
        context.contaminate(case, head)
        plan = context.read_plan(case, head)
        step = plan['details']['step']
        self.assertEqual((plan['state'], plan['code'], step['id']), ('handoff', context.CONTAMINATED, 3))
        context.complete_probe(case, 'route')
        replacement = apply_step(case, step, id='new-adjudicator', route_id='route', reviewer_id='independent-fourth')
        self.assertEqual(replacement['context']['review_ids'], step['payload']['context']['review_ids'])

    def test_runtime_mismatched_probes_do_not_change_frozen_assignment(self):
        """Contract. The matching-runtime rule passes on the base (design section 14.4)."""
        case, head = self.build_bar()
        context.fail_probe(case, 'route', 'unanswered')
        good = case.read_records()['review_route_probe']['probe']
        other = dict(good['runtime'], implementation_sha256='0' * 64)
        for identifier, revision in (('probe', good['recorded_revision']), ('foreign-probe', case.store.revision + 1)):
            case.put('review_route_probe', identifier, dict(good, id=identifier, runtime=other, recorded_revision=revision))
        records = case.read_records()
        saved = records['review_assignment'][head]
        self.assertEqual(saved['context_state']['context_status'], 'verified_for_route')
        self.assertEqual(protocol.find_unverified_context(records, case.artifacts, saved)['assignment_id'], head)
        error, sent = context.run_directly(case, head)
        self.assertEqual((error.code, sent), (context.UNVERIFIED, 0))
        take_probe(case, error.details['next'])
        self.assertIsNone(protocol.find_unverified_context(case.read_records(), case.artifacts, saved))


class ContextReceiptTests(context.RoleFixtures, unittest.TestCase):
    def test_complete_refusal_step_without_top_level_route(self):
        case, head = self.build_bar()
        context.fail_probe(case, 'route')
        error, sent = context.run_directly(case, head)
        self.assertEqual((error.code, sent), (context.UNVERIFIED, 0))
        self.assertNotIn('route_id', error.details)
        records = case.read_records()
        planned = complete_plan(records, case.artifacts, records['review_assignment'][head],
                                dict(state='handoff', code=error.code, details=error.details))
        self.assertEqual(planned['details']['step'], error.details['next']['details']['step'])
        take_probe(case, planned)

    def test_receipt_reconstructs_probe_when_refusals_next_is_run(self):
        case, head = self.build_bar()
        original = context.read_plan(case, head)
        context.fail_probe(case, 'route')
        error, sent = context.run_directly(case, head)
        self.assertEqual((error.code, sent), (context.UNVERIFIED, 0))
        rows = []
        error = ResearchError(error.code, str(error), dict(error.details, next=original))
        review_series._end_series(case.store, original, error=error, exit_code=3, output=rows.append,
                                  refused_action=original)
        ending, = rows
        self.assertNotIn('omitted', ending['details'])
        self.assertEqual(ending['details']['next'], original)
        take_probe(case, ending)

    def check_queued_refusal(self, *, contaminated):
        case, head = self.build_bar()
        scope = build_chain_scope(case.read_records(), head)
        admit = execution.admit_review_call

        def deny_capacity(transaction, operation, invocation_id, route_id, *, invocation):
            if operation == 'review.route_probe' and invocation_id == 'failed-probe':
                return
            if operation == 'review.invoke' and not clock.sleep.called:
                call = dict(operation=operation, invocation_id=invocation_id, route_id=route_id,
                            scope=scope, assignment_id=head, epoch_id=None, task_id=None)
                raise ResearchError('review_capacity_wait', 'Synthetic occupied capacity.',
                                    dict(call=call, wait_seconds=30))
            return admit(transaction, operation, invocation_id, route_id, invocation=invocation)

        def wake(_seconds):
            wait, = case.read_records()['review_call_wait'].values()
            self.assertEqual(wait['state'], 'waiting')
            if contaminated:
                context.contaminate(case, head)
            else:
                context.fail_probe(case, 'route')

        clock = Mock(wraps=admission.time)
        clock.monotonic.return_value = 0
        clock.sleep.side_effect = wake
        rows = []
        with patch.object(execution, 'admit_review_call', side_effect=deny_capacity), \
                patch.object(admission, 'time', clock), patch.object(review_series.signal, 'signal'), \
                patch.object(protocol.transport, 'send_request',
                             side_effect=AssertionError('No queued reviewer request may start.')) as send:
            self.assertEqual(review_series.run_series(case.store, head, emit=rows.append), 3)
        clock.sleep.assert_called_once()
        send.assert_not_called()
        ending = rows[-1]
        expected = ((context.CONTAMINATED, 'review-assignment') if contaminated
                    else (context.UNVERIFIED, 'review-probe'))
        self.assertEqual((ending['code'], ending['command']), expected)
        self.assertEqual(ending['details']['refused_action']['command'], 'review-run')
        self.assertNotIn('omitted', ending['details'])
        if contaminated:
            step = ending['details']['step']
            self.assertEqual(step['id'], 1)
            binding = step['payload']['context']['context_repair']
            self.assertEqual(binding['event_ids'], ['exposure'])
            context.complete_probe(case, 'route')
            apply_step(case, step, id='bar-repair', route_id='route', reviewer_id='repairer',
                context=dict(context_repair=dict(binding, probe_id='completed-probe', reason='Repair the exposed slot.')))
        else:
            self.assertEqual(ending['details']['step']['payload'], dict(route_id='route'))
            take_probe(case, ending)

    def test_queued_contamination_uses_the_refusals_role_step(self):
        """Contract. The second chain-content-stop build retains the nested role step on the base."""
        self.check_queued_refusal(contaminated=True)

    def test_queued_unverified_context_uses_the_refusals_probe_step(self):
        self.check_queued_refusal(contaminated=False)


class LegacyExposureTests(context.RoleFixtures, unittest.TestCase):
    def test_empty_history_keeps_refresh_only_predecessor_with_event(self):
        case, old = self.build_bar()
        context.contaminate(case, old)
        records = case.read_records()
        assignments = records['review_assignment']
        assignments[old] = dict(assignments[old], history_assignment_id='')
        refreshed = dict(assignments[old], id='refreshed', history_assignment_id=None,
                         context=dict(session_refresh=dict(assignment_id=old)))
        assignments[refreshed['id']] = refreshed
        self.assertFalse(records.get('problem_post'))
        lost = protocol.find_unverified_context(records, case.artifacts, refreshed)
        self.assertEqual((lost['assignment_id'], lost['events']), (old, ['exposure']))

    def test_empty_history_keeps_refused_predecessor_on_unverified_other_route(self):
        case, refused = self.build_bar()
        route = case.read_records()['review_route']['route']
        case.mutate(protocol.record_route, dict(id='other-route', **{
            key: route[key] for key in ('adapter', 'model', 'endpoint', 'configuration')}))
        context.fail_probe(case, 'route')
        context.complete_probe(case, 'other-route')
        records = case.read_records()
        assignments = records['review_assignment']
        assignments[refused] = dict(assignments[refused], history_assignment_id='')
        second = dict(assignments[refused], id='second', route_id='other-route', history_assignment_id=None,
                      context=dict(output_refusal=dict(assignment_id=refused)))
        assignments[second['id']] = second
        lost = protocol.find_unverified_context(records, case.artifacts, second)
        self.assertEqual(lost, dict(assignment_id=refused, events=[],
                                   route=dict(id='route', context_status='unverified'), count=1))
        assignments[refused].pop('history_assignment_id')
        self.assertEqual(protocol.list_review_turns(records, second),
                         review_context_repair.list_context_turns(records, second))
