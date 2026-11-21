"""Every route record that a plan names takes its command, and the stale-route procedure is planned in the order
that works."""

from contextlib import ExitStack
import copy
import json
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest.mock import patch

from research_harness import cli, review_protocol as protocol, review_transport as transport
from research_harness import review_claude_transport as claude, review_continuation as continuation
from research_harness.errors import ResearchError
from research_harness.review_continuation import plan_next
from research_harness.storage import Store
import test_review_route_renewal as renewal

# The four commands that read a stored route record, and whether each refuses a retired one. Each refuses a stale
# one.
REFUSES_RETIRED_ROUTE = {'review-assignment': True, 'review-run': True, 'review-output-correction': True,
                         'review-probe': False}


def list_steps(plan):
    return [(step.get('id'), step['command'], (step.get('payload') or {}).get('route_id'))
            for step in plan['details'].get('steps', [plan['details']['step']])]


def read_standing(records, route_id):
    """Whether a route record is retired, and whether it pins another runtime than the installed one."""
    route = records['review_route'][route_id]
    return dict(retired=route_id in protocol.retired_routes(records),
                stale=transport.runtime(route) != route['runtime'])


def list_refused_uses(records, assignment, plan):
    """Every (command, route record id) of a plan whose route record refuses the command.

    This walk is independent of research_harness.review_route_plan: it visits every object of the details of the
    plan, at any depth, that holds one of the four commands and a payload with route_id, and the action of the plan
    itself. A replay of a recorded call reads no route record: the state resume, review-run with the action
    reconcile or accept, and an action or a step that carries the request_id of the recorded call. The walk asks
    the table of commands alone; the tests of a probe that prepares a run hold the addition of the rule for a
    retired route record.
    """
    uses = []

    def visit(value):
        if isinstance(value, dict):
            route_id = (value.get('payload') or {}).get('route_id') if isinstance(value.get('payload'), dict) else None
            if value.get('command') in REFUSES_RETIRED_ROUTE and route_id is not None and 'request_id' not in value:
                uses.append((value['command'], route_id))
            for member in value.values():
                visit(member)
        elif isinstance(value, list):
            for member in value:
                visit(member)
    visit(plan.get('details') or {})
    payload = plan.get('payload') or {}
    if (plan['state'] in ('continue', 'replace', 'plan', 'run', 'wait')
            and plan.get('command') in REFUSES_RETIRED_ROUTE and 'request_id' not in plan
            and payload.get('action') not in ('reconcile', 'accept')):
        uses.append((plan['command'], payload.get('route_id', assignment['route_id'])))
    refused = []
    for command, route_id in uses:
        if route_id not in records['review_route']:
            continue
        standing = read_standing(records, route_id)
        if standing['stale'] or standing['retired'] and REFUSES_RETIRED_ROUTE[command]:
            refused.append((command, route_id))
    return refused


class RoutePlanCase(renewal.RouteRenewalFixture):
    def build_source_request(self):
        request = self.corrected()
        request['value'].update(status='unresolved', evidence=[])
        request['source_requests'] = [dict(id='page', kind='inventory_page', page=0)]
        return request

    def assign_first_turn(self, identifier='second-first', reviewer='second'):
        return self.assign('bar', reviewer, identifier, **self.case.deliver_supporting(reviewer))

    def build_next_turn_head(self):
        """A first bar turn whose answer asks for a source, so its plan is the next turn of the session."""
        head = self.assign_first_turn()
        self.invoke(head, self.build_source_request())
        self.assertEqual(self.plan(head['id'])['state'], 'continue')
        return head

    def build_answered_head(self):
        """A first bar turn with an observed answer that can be recorded."""
        head = self.assign_first_turn()
        self.invoke(head, self.corrected())
        self.assertEqual(self.plan(head['id'])['state'], 'record')
        return head

    def fail_calls(self, head, code, count, change_runtime=False):
        """Record calls of this head that fail at the runtime check before the request is sent. The check at the
        start of each call passes. With change_runtime the reviewer runtime changes between the two checks of the
        first call, and the real check refuses the call."""
        check, calls = protocol._require_route_runtime, []

        def check_route(route):
            calls.append(route['id'])
            if len(calls) % 2 == 0:
                if not change_runtime:
                    raise ResearchError(code, 'A synthetic refusal of the runtime check.')
                self.make_stale()
            return check(route)
        with patch.object(protocol, '_require_route_runtime', check_route):
            for index in range(count):
                self.case.mutate(protocol.invoke_assignment, dict(id='call-%d' % index, assignment_id=head['id']))
        attempts = [row for row in self.records()['review_attempt'].values() if row['assignment_id'] == head['id']]
        self.assertEqual([row['error']['code'] for row in attempts], [code] * count)

    def fail_probe(self, route_id='route'):
        """A probe whose answer returns a wrong control: the route record is not verified now."""
        self.probe_failure = True
        self.probe(route_id, 'probe-with-a-wrong-control-' + route_id)
        self.probe_failure = False
        self.assertEqual(protocol.route_state(self.records(), self.case.artifacts, route_id)['context_status'],
                         'unverified')

    def take(self, step, **choices):
        """Take one step of a handoff through its command, with the choices that the step leaves open."""
        payload = copy.deepcopy(step['payload'])
        payload.update(choices)
        return self.case.mutate(cli.OPERATIONS[step['command']], payload)['result']

    def take_retirement(self, step):
        self.assertEqual((step['id'], step['command'], step['payload']['kind']), (8, 'review-context', 'route_retired'))
        return self.take(step, id='retire-stale', reason='The owner retired the stale route record.',
                         evidence=[self.case.supporting])

    def take_repair(self, head, plan):
        """Take the last step of a handoff, the context repair, on the route record that the plan names."""
        step = plan['details'].get('steps', [plan['details']['step']])[-1]
        self.assertEqual((step['id'], step['command']), (1, 'review-assignment'))
        route_id = plan['details']['renewal']['route_id']
        payload = copy.deepcopy(step['payload'])
        payload.update(id=head['id'] + '-repair', route_id=route_id, reviewer_id=head['reviewer_id'])
        records = self.records()
        binding = payload['context']['context_repair']
        binding.update(probe_id=protocol.route_state(records, self.case.artifacts, route_id)['probe_id'],
                       reason='Continue the slot on a route record with the installed runtime.')
        return self.case.mutate(cli.OPERATIONS['review-assignment'], payload)['result']

    def count_records(self):
        records = self.records()
        return {kind: len(records.get(kind, {})) for kind in ('review_assignment', 'review_invocation')}

    def build_first_payload(self, reviewer):
        return dict(id=reviewer + '-first', dossier_id=None, role='bar', reviewer_id=reviewer, author_id='author',
                    context=self.case.deliver_supporting(reviewer))

    def build_failed_call(self):
        head = self.assign_first_turn()
        with patch.object(transport, 'send_request', side_effect=ResearchError('review_provider_failure', 'A provider error.')):
            self.case.mutate(protocol.invoke_assignment, dict(id='failed-call', assignment_id=head['id']))
        return head

    def build_missing_program(self):
        head = self.assign_first_turn()
        self.fail_calls(head, 'review_runtime_missing', 7)
        return head

    def build_unverified(self, head):
        self.fail_probe()
        return head


class RoutePlanRegressionTests(RoutePlanCase):
    def test_refused_head_on_a_retired_route_plans_the_repair_and_no_replacement(self):
        first, _ = self.build_refused()
        self.retire(first, 'retire-route')
        plan = self.plan(first['id'])
        # On the base the plan is replace with review-assignment on the retired route, which the command refuses.
        self.assertEqual((plan['state'], plan.get('code'), plan['command']), ('handoff', 'review_route_retired', 'review-assignment'))
        self.assertNotIn('payload', plan)
        self.assertEqual(list_steps(plan), [(1, 'review-assignment', None)])
        line = self.series(first['id'])[-1]
        self.assertNotIn('error', line)
        self.assertEqual((line['state'], line['code']), ('handoff', 'review_route_retired'))

    def test_next_turn_on_a_stale_route_plans_the_procedure_before_any_command(self):
        head = self.build_next_turn_head()
        self.make_stale()
        before = self.count_records()
        plan = self.plan(head['id'])
        # On the base the plan is continue with review-assignment on the stale route.
        self.assertEqual((plan['state'], plan.get('code')), ('handoff', 'review_route_runtime_stale'))
        self.assertEqual(plan['details']['renewal'], dict(admitted=False, route_id=None, lacks='record'))
        self.assertEqual(list_steps(plan), [(8, 'review-context', None), (7, 'review-route', None),
                                            (1, 'review-assignment', None)])
        self.assertEqual(plan['command'], 'review-context')
        self.assertNotIn('payload', plan)
        self.assertNotIn('capacity_forecast', plan)
        line = self.series(head['id'])[-1]
        # On the base the series executes the assignment, the command refuses it, and the line names step 7 alone.
        self.assertNotIn('error', line)
        self.assertEqual(list_steps(line), list_steps(plan))
        self.assertEqual(self.count_records(), before)

    def test_procedure_names_the_record_with_the_installed_runtime_and_reaches_a_runnable_repair(self):
        head = self.build_next_turn_head()
        self.make_stale()
        self.renew()
        plan = self.plan(head['id'])
        # On the base the plan is continue on the stale route, and the series ends with step 7 alone again, although
        # the route record exists.
        self.assertEqual((plan['state'], plan.get('code')), ('handoff', 'review_route_runtime_stale'))
        self.assertEqual(plan['details']['renewal'], dict(admitted=False, route_id='route-renewed', lacks='probe'))
        self.assertEqual(list_steps(plan), [(8, 'review-context', None), (7, 'review-probe', 'route-renewed'),
                                            (1, 'review-assignment', None)])
        self.take_retirement(plan['details']['steps'][0])
        plan = self.plan(head['id'])
        self.assertEqual((plan['state'], plan['code'], plan['command']), ('handoff', 'review_route_retired', 'review-probe'))
        self.assertEqual(plan['details']['renewal'], dict(admitted=False, route_id='route-renewed', lacks='probe'))
        self.assertEqual(list_steps(plan), [(7, 'review-probe', 'route-renewed'), (1, 'review-assignment', None)])
        self.take(plan['details']['steps'][0], id='probe-after-the-retirement')
        plan = self.plan(head['id'])
        self.assertEqual(plan['details']['renewal'], dict(admitted=False, route_id='route-renewed', lacks=None))
        self.assertEqual(list_steps(plan), [(1, 'review-assignment', None)])
        repair = self.take_repair(head, plan)
        self.assertEqual((repair['route_id'], repair['reviewer_id']), ('route-renewed', head['reviewer_id']))
        self.assertEqual(self.plan(repair['id'])['state'], 'run')

    def test_recorded_turn_without_a_call_on_a_stale_route_plans_the_procedure(self):
        head = self.assign_first_turn()
        self.make_stale()
        before = self.count_records()
        plan = self.plan(head['id'])
        # On the base the plan is run with review-run, and the series ends on its refusal with step 7 alone.
        self.assertEqual((plan['state'], plan.get('code')), ('handoff', 'review_route_runtime_stale'))
        self.assertEqual([step[0] for step in list_steps(plan)], [8, 7, 1])
        line = self.series(head['id'])[-1]
        self.assertNotIn('error', line)
        self.assertEqual(self.count_records(), before)

    def test_retry_after_a_failed_call_on_a_retired_route_plans_the_repair(self):
        head = self.assign_first_turn()
        with patch.object(transport, 'send_request', side_effect=ResearchError('review_provider_failure', 'A provider error.')):
            self.case.mutate(protocol.invoke_assignment, dict(id='failed-call', assignment_id=head['id']))
        self.assertIn(self.plan(head['id'])['state'], ('run', 'wait'))
        self.retire(head, 'retire-route')
        plan = self.plan(head['id'])
        # On the base the plan stays the retry of review-run on the retired route.
        self.assertEqual((plan['state'], plan.get('code')), ('handoff', 'review_route_retired'))
        self.assertEqual(list_steps(plan), [(1, 'review-assignment', None)])

    def test_call_that_failed_because_the_runtime_changed_plans_the_procedure_until_the_repair(self):
        head = self.assign_first_turn()
        # The runtime changes between the start of the call and its request. The call is recorded as failed, and the
        # failure planner gives it the state handoff.
        self.fail_calls(head, 'review_route_runtime_stale', 1, change_runtime=True)
        from research_harness.review_failure_class import plan_after_failure
        failure = plan_after_failure(self.records(), self.case.artifacts, head['id'])
        self.assertEqual((failure['state'], failure['code'], failure['details']['outcome']),
                         ('handoff', 'review_route_runtime_stale', 'runtime_stale'))
        plan = self.plan(head['id'])
        # On the base the plan is the handoff review-route with step 7 alone, before and after a renewed record.
        self.assertEqual((plan['state'], plan['code'], plan['command']),
                         ('handoff', 'review_route_runtime_stale', 'review-context'))
        self.assertEqual(plan['details']['renewal'], dict(admitted=False, route_id=None, lacks='record'))
        self.assertEqual([step[0] for step in list_steps(plan)], [8, 7, 1])
        self.renew()
        plan = self.plan(head['id'])
        self.assertEqual(list_steps(plan), [(8, 'review-context', None), (7, 'review-probe', 'route-renewed'),
                                            (1, 'review-assignment', None)])
        self.assertEqual(list_steps(self.series(head['id'])[-1]), list_steps(plan))
        self.take_retirement(plan['details']['steps'][0])
        plan = self.plan(head['id'])
        self.assertEqual((plan['code'], list_steps(plan)), ('review_route_retired', [
            (7, 'review-probe', 'route-renewed'), (1, 'review-assignment', None)]))
        self.take(plan['details']['steps'][0], id='probe-after-the-retirement')
        repair = self.take_repair(head, self.plan(head['id']))
        self.assertEqual(self.plan(repair['id'])['state'], 'run')

    def test_handoff_after_a_missing_reviewer_program_on_a_stale_route_plans_the_procedure(self):
        head = self.assign_first_turn()
        self.fail_calls(head, 'review_runtime_missing', 7)
        current = self.plan(head['id'])
        self.assertEqual((current['state'], current['code'], list_steps(current)),
                         ('handoff', 'review_runtime_missing', [(7, 'review-probe', 'route')]))
        self.make_stale()
        with self.assertRaises(ResearchError) as refused:
            self.take(current['details']['step'], id='probe-of-the-stale-record')
        self.assertEqual(refused.exception.code, 'review_route_runtime_stale')
        plan = self.plan(head['id'])
        # On the base the plan stays the probe of the stale record, which the command refuses, also after a renewed
        # record exists.
        self.assertEqual((plan['state'], plan['code']), ('handoff', 'review_route_runtime_stale'))
        self.assertEqual([step[0] for step in list_steps(plan)], [8, 7, 1])
        self.renew()
        self.assertEqual(list_steps(self.plan(head['id'])), [
            (8, 'review-context', None), (7, 'review-probe', 'route-renewed'), (1, 'review-assignment', None)])

    def test_probe_that_prepares_a_run_on_a_retired_route_plans_the_repair(self):
        head = self.assign_first_turn()
        self.fail_probe()
        current = self.plan(head['id'])
        self.assertEqual((current['code'], list_steps(current)),
                         ('review_context_unverified', [(7, 'review-probe', 'route')]))
        self.retire(head, 'retire-route')
        plan = self.plan(head['id'])
        # On the base the plan stays the probe of the retired record. The probe is a reviewer call, and after it the
        # head still takes no run on the retired record.
        self.assertEqual((plan['state'], plan['code'], list_steps(plan)),
                         ('handoff', 'review_route_retired', [(1, 'review-assignment', None)]))

    def test_observed_answer_on_a_stale_route_plans_the_retirement_and_then_the_plan_again(self):
        head = self.build_answered_head()
        self.fail_probe()
        current = self.plan(head['id'])
        self.assertEqual((current['code'], list_steps(current)),
                         ('review_context_unverified', [(7, 'review-probe', 'route')]))
        self.make_stale()
        plan = self.plan(head['id'])
        # On the base the plan stays the probe of the stale record, which the command refuses.
        self.assertEqual((plan['state'], plan['code'], plan['command']),
                         ('handoff', 'review_route_runtime_stale', 'review-context'))
        self.assertEqual(list_steps(plan), [(8, 'review-context', None), (None, 'review-series', None)])
        self.assertEqual(plan['details']['steps'][1], dict(command='review-series', assignment_id=head['id']))
        self.take_retirement(plan['details']['steps'][0])
        # The probe that failed holds an answer, so it decides on the retired record as well: the context stays
        # unverified, and the slot continues through a repair.
        plan = self.plan(head['id'])
        self.assertEqual((plan['state'], plan['code'], list_steps(plan)), ('handoff', 'review_route_retired', [
            (7, 'review-route', None), (1, 'review-assignment', None)]))

    def test_plan_of_a_planning_error_on_a_stale_route_plans_the_procedure(self):
        """When planning raises, plan_next completes the code of the error in its error branch. That plan passes the
        rule too. Each of these codes completes to a step on the route of the head."""
        head = self.build_next_turn_head()
        self.make_stale()
        for code in ('review_route_runtime_stale', 'review_context_unverified', 'review_runtime_missing'):
            error = ResearchError(code, 'A synthetic planning error.', dict(assignment_id=head['id']))
            with patch.object(continuation, '_plan_head', side_effect=error):
                plan = self.plan(head['id'])
            # On the base the stale code completes to step 7 alone, and the two others to the probe of the stale
            # record, which the command refuses.
            self.assertEqual((plan['state'], plan['code'], list_steps(plan)), ('handoff', 'review_route_runtime_stale', [
                (8, 'review-context', None), (7, 'review-route', None), (1, 'review-assignment', None)]), code)


class RoutePlanGuardTests(RoutePlanCase):
    """What the rule leaves as it is, and what it holds for every plan."""
    maxDiff = None

    def test_recording_a_verified_output_needs_no_route_record(self):
        head = self.build_answered_head()
        current = self.plan(head['id'])
        self.make_stale()
        self.assertEqual(self.plan(head['id']), current)
        self.assertEqual(self.series(head['id'])[-1]['state'], 'done')

    def test_head_on_a_route_with_the_installed_runtime_keeps_its_plan_beside_other_records(self):
        head = self.build_next_turn_head()
        current = self.plan(head['id'])
        self.renew('route-twin')
        self.assertEqual(json.dumps(self.plan(head['id'])), json.dumps(current))

    def test_unreadable_runtime_leaves_the_plan_as_it_is(self):
        from research_harness import review_claude_transport as claude
        head = self.build_next_turn_head()
        current = self.plan(head['id'])
        with patch.object(claude, 'runtime', side_effect=ResearchError('review_runtime_missing', 'No reviewer CLI.')):
            self.assertEqual(self.plan(head['id']), current)

    def test_probe_that_verifies_an_observed_answer_stays_on_a_retired_route(self):
        head = self.build_answered_head()
        self.fail_probe()
        self.retire(head, 'retire-route')
        plan = self.plan(head['id'])
        self.assertEqual((plan['state'], plan['code'], list_steps(plan)),
                         ('handoff', 'review_context_unverified', [(7, 'review-probe', 'route')]))
        self.take(plan['details']['step'], id='probe-of-the-retired-record')
        self.assertEqual(self.plan(head['id'])['state'], 'record')

    def test_details_of_any_shape_are_read_without_a_route_record(self):
        """details.decision is the answer of the reviewer in one handoff, a step can hold any member, and the step
        that finishes a captured call of a stopped chain is a replay."""
        from research_harness.review_route_plan import list_route_uses
        head = dict(id='head', route_id='route')
        plan = dict(state='handoff', command='export', code='openness_review_not_approved', details=dict(
            decision='revise', reason='A missing argument.', step='export', steps=None, then=dict(command='review-series'),
            next=[dict(command='review-probe', payload='route'), dict(command=['review-probe'], payload=dict(route_id='route'))],
            calls=[dict(step=dict(command='review-probe', payload=dict(id='probe', route_id='route'), request_id='request'))]))
        self.assertEqual(list_route_uses(head, plan), [])
        replay = dict(state='wait', command='review-run', request_id='request', payload=dict(id='call', assignment_id='head'))
        self.assertEqual(list_route_uses(head, replay), [])
        plan['details']['decision'] = dict(then=[dict(answer='Probe', steps=[
            dict(command='review-probe', payload=dict(route_id='other'), then=[
                dict(command='review-assignment', payload=dict(route_id='route'))])])])
        self.assertEqual(sorted(list_route_uses(head, plan)), [('review-assignment', 'route'), ('review-probe', 'other')])

    def test_commands_refuse_a_stale_route_record_and_all_but_the_probe_a_retired_one(self):
        """The table of commands of the rule, compared with the commands. The output correction is covered by
        CorrectionRouteTests.test_correction_refuses_a_stale_and_a_retired_route_record."""
        from research_harness import review_route_plan
        self.assertEqual(review_route_plan.REFUSES_RETIRED_ROUTE, REFUSES_RETIRED_ROUTE)
        self.renew('route-other')
        heads = {route_id: self.assign('bar', reviewer, reviewer + '-first', route_id=route_id,
                                       **self.case.deliver_supporting(reviewer))
                 for route_id, reviewer in (('route', 'second'), ('route-other', 'third'))}

        def refuse(route_id):
            """The refusal code of each command on this route record, or None when the command is taken."""
            payloads = {'review-assignment': dict(self.build_first_payload('fourth'), route_id=route_id),
                        'review-run': dict(id='run-on-' + route_id, assignment_id=heads[route_id]['id']),
                        'review-probe': dict(id='probe-%s-%d' % (route_id, len(self.calls)), route_id=route_id)}
            codes = {}
            for command, payload in payloads.items():
                try:
                    self.case.mutate(cli.OPERATIONS[command], payload)
                    codes[command] = None
                except ResearchError as error:
                    codes[command] = error.code
            return codes
        self.retire(heads['route'], 'retire-route')
        self.assertEqual(refuse('route'), {'review-assignment': 'review_route_retired',
                                           'review-run': 'review_route_retired', 'review-probe': None})
        self.make_stale()
        self.assertEqual(refuse('route-other'), dict.fromkeys(
            ('review-assignment', 'review-run', 'review-probe'), 'review_route_runtime_stale'))

    def test_every_route_record_that_a_plan_names_takes_its_command(self):
        """Eight heads, which reach eight branches of the head planner and of the failure planner, each on seven
        standings of its route record, which reach each row of the table of commands and each answer of the
        selector: current, stale, retired, both, and stale or both with a record of the same identity that pins the
        installed runtime, with and without its probe. Each head is built once on its own store, and each standing
        starts from a copy of that store. On the base 33 of these 56 plans name a route record that refuses the
        command."""
        builders = dict(
            recorded=lambda case: case.assign_first_turn(),
            next_turn=lambda case: case.build_next_turn_head(),
            answered=lambda case: case.build_answered_head(),
            refused=lambda case: case.build_refused()[0],
            failed_call=lambda case: case.build_failed_call(),
            missing_program=lambda case: case.build_missing_program(),
            unverified_recorded=lambda case: case.build_unverified(case.assign_first_turn()),
            unverified_answered=lambda case: case.build_unverified(case.build_answered_head()))
        standings = dict(current=(), stale=('stale',), retired=('retired',), retired_and_stale=('retired', 'stale'),
                         stale_with_a_record=('stale', 'record'), stale_with_a_probed_record=('stale', 'record', 'probe'),
                         retired_and_stale_with_a_probed_record=('retired', 'stale', 'record', 'probe'))
        stale_runtime = dict(claude.runtime(), implementation_sha256=renewal.STALE_IMPLEMENTATION_SHA256)
        refused = {}
        for name, build in sorted(builders.items()):
            case = RoutePlanCase('setUp')
            case.setUp()
            try:
                head = build(case)
                fixture = case.case
                with tempfile.TemporaryDirectory() as directory:
                    saved = shutil.copytree(fixture.root, Path(directory) / 'head')
                    for label, changes in sorted(standings.items()):
                        shutil.rmtree(fixture.root)
                        shutil.copytree(saved, fixture.root)
                        fixture.store = Store(fixture.root)
                        fixture.artifacts = fixture.store.build_artifact_store()
                        if 'retired' in changes:
                            case.retire(head, 'retire-route')
                        with ExitStack() as installed:
                            if 'stale' in changes:
                                installed.enter_context(patch.object(claude, 'runtime', return_value=stale_runtime))
                            if 'record' in changes:
                                case.renew(probe='probe' in changes)
                            plan = case.plan(head['id'])
                            uses = list_refused_uses(case.records(), head, plan)
                        if uses:
                            refused[name + ' / ' + label] = (plan['state'], plan.get('code'), uses)
            finally:
                case.doCleanups()
        self.assertEqual(refused, {})


class CorrectionRouteTests(unittest.TestCase):
    """The handoff of an output correction, with the store of tests/test_review_correction_origin.py."""

    def setUp(self):
        import test_review_correction_origin as origin
        self.origin = origin.CorrectionOriginCase('build_refused')
        self.addCleanup(self.origin.doCleanups)
        self.case = self.origin.build_refused()
        self.fixture, self.assignment = self.case.fixture, self.case.case.assignment

    def plan(self):
        return plan_next(self.fixture.read_records(), self.fixture.artifacts, self.assignment['id'])

    def make_stale(self):
        route = self.fixture.read_records()['review_route'][self.assignment['route_id']]
        stale = patch.object(transport, 'runtime', return_value=dict(route['runtime'], implementation_sha256='0' * 64))
        stale.start()
        self.addCleanup(stale.stop)

    def make_stale_route(self, route_id):
        """This route record also pins another runtime than the installed one."""
        installed = transport.runtime

        def read_runtime(route):
            runtime = installed(route)
            return dict(runtime, implementation_sha256='0' * 64) if route['id'] == route_id else runtime
        stale = patch.object(transport, 'runtime', side_effect=read_runtime)
        stale.start()
        self.addCleanup(stale.stop)

    def test_correction_on_a_stale_route_names_what_the_store_lacks_and_then_the_renewed_record(self):
        current = self.plan()
        self.assertEqual((current['command'], current['details']['step']['payload']['route_id']),
                         ('review-output-correction', 'route'))
        self.make_stale()
        plan = self.plan()
        # On the base the plan is unchanged: the step names the stale route, and the command refuses it.
        self.assertEqual((plan['state'], plan['code'], plan['command']), ('handoff', 'review_route_runtime_stale', 'review-route'))
        self.assertEqual(plan['details']['renewal'], dict(admitted=True, route_id=None, lacks='record'))
        self.assertEqual([row['command'] for row in plan['details']['step']['then']], ['review-probe', 'review-series'])
        self.origin.record_second_route(self.case)
        plan = self.plan()
        self.assertEqual((plan['state'], plan['command']), ('handoff', 'review-output-correction'))
        self.assertEqual(plan['details']['step']['payload'], dict(assignment_id=self.assignment['id'], route_id='second-route'))
        result = self.origin.invoke_correction(self.case, plan['details']['step']['payload']['route_id'])
        self.assertEqual(result['correction_status'], 'accepted')

    def test_probe_step_of_an_unverified_correction_names_the_route_record_of_the_correction(self):
        self.origin.record_second_route(self.case)
        self.origin.invoke_correction(self.case, 'second-route')
        self.origin.probe(self.case, 'second-route-later-probe', 'second-route', verified=False)
        plan = self.plan()
        self.assertEqual((plan['state'], plan['code'], plan['command']), ('handoff', 'review_context_unverified', 'review-probe'))
        # On the base the step names the route of the assignment, whose probe does not end the state.
        self.assertEqual(plan['details']['step']['payload'], dict(route_id='second-route'))
        self.origin.probe(self.case, 'second-route-completed-probe', plan['details']['step']['payload']['route_id'])
        self.assertNotEqual(self.plan().get('code'), 'review_context_unverified')

    def test_unverified_correction_on_a_stale_record_is_the_decision_of_the_owner(self):
        """The stop is given for the route record whose probe ends the state, the record of the accepted correction
        (find_unverified_route). A plan of the same code that names another route record that refuses its probe
        is returned as it is: lane call-context-present plans that case."""
        self.origin.record_second_route(self.case)
        self.origin.invoke_correction(self.case, 'second-route')
        self.origin.probe(self.case, 'second-route-later-probe', 'second-route', verified=False)
        self.make_stale_route('second-route')
        with self.assertRaises(ResearchError) as refused:
            self.origin.probe(self.case, 'probe-of-the-stale-record', 'second-route')
        self.assertEqual(refused.exception.code, 'review_route_runtime_stale')
        plan = self.plan()
        # On the base the plan is the probe of the route of the assignment, which does not end the state. No probe
        # can: the record of the correction takes none.
        self.assertEqual((plan['state'], plan['code']), ('stop', 'review_context_unverified'))
        self.assertEqual(plan['details']['route_id'], 'second-route')
        self.assertNotIn('step', plan['details'])
        records = self.fixture.read_records()
        self.assertEqual(list_refused_uses(records, self.assignment, plan), [])
        self.assertTrue(plan['details']['decision']['then'])
        from research_harness.review_route_plan import apply_route_rule
        route = records['review_route']['route']
        self.fixture.mutate(protocol.record_route, dict(
            {key: copy.deepcopy(route[key]) for key in renewal.IDENTITY_MEMBERS}, id='third-route'))
        self.make_stale_route('third-route')
        other = dict(state='handoff', command='review-probe', code='review_context_unverified',
                     details=dict(assignment_id=self.assignment['id'], step=dict(
                         command='review-probe', payload=dict(route_id='third-route'), chooses=['id'])))
        records = self.fixture.read_records()
        self.assertEqual(apply_route_rule(records, self.fixture.artifacts, self.assignment, copy.deepcopy(other)), other)

    def test_correction_refuses_a_stale_and_a_retired_route_record(self):
        from research_harness import review_route_plan
        self.assertIs(review_route_plan.REFUSES_RETIRED_ROUTE['review-output-correction'], True)
        self.make_stale()
        with self.assertRaises(ResearchError) as refused:
            self.origin.invoke_correction(self.case, 'route')
        self.assertEqual(refused.exception.code, 'review_route_runtime_stale')
        self.origin.record_event(self.case, 'retire-route', self.assignment['id'], 'route_retired')
        with self.assertRaises(ResearchError) as refused:
            self.origin.invoke_correction(self.case, 'route')
        self.assertEqual(refused.exception.code, 'review_correction_ineligible')


class SuccessorRouteTests(unittest.TestCase):
    """The handoff export of a stopped problem chain, with the store of tests/test_openness_reading_v7_refusal.py."""

    def setUp(self):
        import test_openness_reading_v7_refusal as refusal_tests
        from research_harness import openness_reading as reading, openness_reading_v7 as v7
        self.v7 = v7
        case = refusal_tests.LegacySuccessorTests('test_k7_v5_input_limit_exports_to_v7_carries_and_records')
        case.setUp()
        self.addCleanup(case.doCleanups)
        case.prepare_bound_readings()
        self.case, self.head = case, case.fail_legacy_final()
        protocols = patch.object(reading, 'NEW_PROBLEM_CHAIN_PROTOCOLS', (v7.PROTOCOL, reading.PROTOCOL_V6))
        protocols.start()
        self.addCleanup(protocols.stop)

    def plan(self):
        return plan_next(self.case.store.snapshot()['records'], self.case.artifacts, self.head)

    def record_twin(self, identifier='route-renewed'):
        route = self.case.store.snapshot()['records']['review_route']['route']
        protocol.record_route(self.case.store, dict(id=identifier, **{key: route[key] for key in renewal.IDENTITY_MEMBERS}),
                              expected_revision=self.case.store.revision, request_id='twin-' + identifier)

    def test_export_of_a_stopped_chain_names_a_route_record_that_takes_its_assignment(self):
        """The admission of the new chain is computed for the record that the step names. On a route record that
        takes the assignment, the step keeps the member order of its payloads, because review-series prints a row
        with the member order of its dicts."""
        from codex_route_fixtures import probe_codex_route
        from research_harness import openness_reading as reading
        from research_harness.problem_openness import export_openness
        case = self.case
        current = self.plan()
        self.assertEqual((current['command'], current['details']['step']['then'][1]['payload']['route_id']),
                         ('export', 'route'))
        for row in current['details']['step']['then']:
            self.assertEqual(list(row['payload']), ['role', 'dossier_id', 'author_id', 'route_id', 'reviewer_id'])
            self.assertEqual(row['chooses'], ['id', 'context.native_packet'])
        self.assertEqual([list(row) for row in current['details']['step']['then']],
                         [['command', 'payload', 'chooses', 'check'], ['command', 'payload', 'chooses']])
        renewal.make_codex_stale(self)
        stale = self.plan()
        # On the base the plan is unchanged: the step fixes the stale route, and review-assignment refuses it.
        self.assertEqual((stale['state'], stale['code'], stale['command']),
                         ('handoff', 'review_route_runtime_stale', 'review-route'))
        self.assertEqual(stale['details']['renewal'], dict(admitted=True, route_id=None, lacks='record'))
        self.assertEqual([row['command'] for row in stale['details']['step']['then']], ['review-probe', 'review-series'])
        # Until the new record is verified, the admission is that on the route record of the stopped chain.
        self.assertEqual(stale['details']['successor']['protocol'], self.v7.PROTOCOL)
        self.assertNotIn('route_id', stale['details']['successor'])
        self.record_twin()
        unprobed = self.plan()
        self.assertEqual((unprobed['code'], unprobed['command']), ('review_route_runtime_stale', 'review-probe'))
        self.assertEqual(unprobed['details']['renewal'], dict(admitted=True, route_id='route-renewed', lacks='probe'))
        self.assertEqual(unprobed['details']['step']['payload'], dict(route_id='route-renewed'))
        self.assertEqual(unprobed['details']['step']['then'], [dict(command='review-series', assignment_id=self.head)])
        probe_codex_route(case, 'probe-route-renewed', 'route-renewed')
        choose, routes = reading.choose_chain_protocol, []

        def choose_on(records, artifacts, route, native, **options):
            routes.append(route['id'])
            return choose(records, artifacts, route, native, **options)
        with patch.object(reading, 'choose_chain_protocol', choose_on):
            ready = self.plan()
        # On the base the admission is computed for the stale record of the stopped chain.
        self.assertEqual(set(routes), {'route-renewed'})
        self.assertEqual((ready['state'], ready['code'], ready['command']), ('handoff', current['code'], 'export'))
        self.assertNotIn('renewal', ready['details'])
        self.assertEqual(ready['details']['successor']['route_id'], 'route-renewed')
        step = ready['details']['step']
        self.assertEqual([row['payload']['route_id'] for row in step['then']], ['route-renewed', 'route-renewed'])
        minimum = dict(searches=[dict(formulation=row['question'], search_id=row['id']) for row in case.searches],
                       settling_work_id=None, duplicate_resolutions=[])
        reference = export_openness(case.store, dict(assignment_id='successor', reviewer_id='openness-reviewer',
            minimum=minimum), case.root / 'successor-packet', accept_gaps=True)['native_packet']
        payload = dict(step['then'][1]['payload'], id='successor', context=dict(native_packet=reference))
        receipt = case.mutate(protocol.record_assignment, payload)['result']
        # One export, one assignment, no refused command.
        self.assertEqual((receipt['route_id'], receipt['protocol_selection']['protocol']), ('route-renewed', self.v7.PROTOCOL))
        self.assertEqual(receipt['carried_readings'], ready['details']['successor']['carried_readings'])

    def test_verified_record_that_does_not_admit_the_new_chain_gives_the_stop_of_its_admission(self):
        """The capacity evidence of a route is keyed by its runtime. A plain probe of real size verifies the renewed
        record and shows no input of the size of the new chain, so the admission on that record is refused, where
        the admission on the stale record accepted the chain. The plan is the stop of the admission on the record
        that the step would name, not the export (decision D9)."""
        from codex_route_fixtures import probe_codex_route
        renewal.make_codex_stale(self)
        self.record_twin()
        self.case.capacity_probe_tokens = 5000
        probe_codex_route(self.case, 'probe-route-renewed', 'route-renewed')
        plan = self.plan()
        # On the base the plan is the handoff export, and its step names the stale record.
        self.assertEqual((plan['state'], plan['code'], plan.get('command')), ('stop', 'review_input_too_large', None))
        successor = plan['details']['successor']
        self.assertEqual((successor['route_id'], successor['protocol']), ('route-renewed', None))
        self.assertIn('review_route_capacity_below_floor', [row['code'] for row in successor['admissions']])
        records = self.case.store.snapshot()['records']
        self.assertEqual(list_refused_uses(records, records['review_assignment'][self.head], plan), [])

    def test_export_after_a_retirement_leaves_the_route_to_the_agent(self):
        records = self.case.store.snapshot()['records']
        evidence = self.case.artifacts.put(b'The owner retired this route.', 'text/plain')
        protocol.record_context_event(self.case.store, dict(
            id='retire-route', assignment_id=self.head, kind='route_retired', source='owner',
            reason='The owner retired this route.', evidence=[evidence]),
            expected_revision=self.case.store.revision, request_id='retire-route')
        plan = self.plan()
        self.assertEqual((plan['state'], plan['command']), ('handoff', 'export'))
        # On the base the step fixes the retired route, and review-assignment refuses it.
        for assign in plan['details']['step']['then']:
            self.assertNotIn('route_id', assign['payload'])
            self.assertEqual(assign['chooses'], ['id', 'route_id', 'context.native_packet'])
        self.assertEqual(list_refused_uses(self.case.store.snapshot()['records'], records['review_assignment'][self.head],
                                           plan), [])


class MathSuccessorRouteTests(unittest.TestCase):
    """The answer of an owner's decision that starts the admitted successor of a stopped math chain, with the store
    of tests/test_openness_math_successor.py."""

    def setUp(self):
        import test_openness_math_successor as math_tests
        from research_harness import openness_successor as successor
        self.successor = successor
        case = math_tests.MathSuccessorTests('test_a25_exact_final_stop_names_the_successor_and_its_steps_reach_a_recorded_review')
        case.setUp()
        self.addCleanup(case.doCleanups)
        self.native = case.build_math_packet()
        case.stop_v1_final(self.native)
        self.case = case

    def read_answer(self):
        """The successor answer of the stop that plan_next gives the last recorded turn of the chain."""
        plan = plan_next(self.case.store.snapshot()['records'], self.case.artifacts, 'math-3')
        self.assertEqual((plan['state'], plan['code']), ('stop', 'review_chain_projection_over_limit'))
        answer = plan['details']['decision']['then'][0]
        self.assertEqual(answer['answer'], self.successor.MATH_SUCCESSOR_ANSWER)
        return plan, answer

    def describe(self, answer):
        return [(row['id'], row['command'], (row.get('payload') or {}).get('route_id')) for row in answer['steps']]

    def test_math_successor_answer_on_a_stale_route_gives_the_route_step_and_then_the_successor_step(self):
        from codex_route_fixtures import probe_codex_route
        from research_harness import review_route_capacity as capacity
        plan, answer = self.read_answer()
        self.assertEqual(self.describe(answer), [(5, 'search review-packet', None)])
        self.assertEqual([row['payload']['route_id'] for row in answer['steps'][0]['then']], ['route', 'route'])
        renewal.make_codex_stale(self)
        plan, answer = self.read_answer()
        # On the base the answer holds the successor step alone, and its assignment names the stale route.
        self.assertEqual(self.describe(answer), [(7, 'review-route', None)])
        self.assertEqual([row['command'] for row in answer['steps'][0]['then']], ['review-probe', 'review-series'])
        records = self.case.store.snapshot()['records']
        self.assertEqual(list_refused_uses(records, records['review_assignment']['math-3'], plan), [])
        # The same answer of the decision after an output over the limit of a math turn gives the same step.
        decision = capacity.describe_capacity_decision(records, self.case.artifacts, records['review_assignment']['math-1'],
            'output_over_cap', dict(protocol=self.successor.reading.MATH_PROTOCOL, kind='reading', handler='nothing'))
        self.assertEqual(decision['then'][0]['answer'], self.successor.MATH_SUCCESSOR_ANSWER)
        self.assertEqual(self.describe(decision['then'][0]), [(7, 'review-route', None)])
        self.assertEqual([row['command'] for row in decision['then'][0]['steps'][0]['then']],
                         ['review-probe', 'review-series'])
        route = records['review_route']['route']
        protocol.record_route(self.case.store, dict(id='route-renewed', **{key: route[key] for key in renewal.IDENTITY_MEMBERS}),
                              expected_revision=self.case.store.revision, request_id='twin-route-renewed')
        plan, answer = self.read_answer()
        self.assertEqual(self.describe(answer), [(7, 'review-probe', 'route-renewed')])
        self.assertEqual(answer['steps'][0]['then'], [dict(command='review-series', assignment_id='math-3')])
        probe_codex_route(self.case, 'probe-route-renewed', 'route-renewed')
        plan, answer = self.read_answer()
        # The route record is verified: the answer is the successor step, and its assignment names that record.
        self.assertEqual(self.describe(answer), [(5, 'search review-packet', None)])
        self.assertEqual([row['payload']['route_id'] for row in answer['steps'][0]['then']],
                         ['route-renewed', 'route-renewed'])
        self.assertEqual((plan['details']['successor']['protocol'], plan['details']['successor']['route_id']),
                         (self.successor.reading.MATH_PROTOCOL_V2, 'route-renewed'))


if __name__ == '__main__':
    unittest.main()
