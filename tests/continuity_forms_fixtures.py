"""Command journeys of the chain kinds of 0.92.0 for the continuity guard (G1, K7, K8).

The kinds are a v7 chain whose final is in pages, a chain of the math protocol v2, a session under the rules v4 and a
series of the parts protocol v3. Each builder sets the new form of its chain for the length of the test that calls
it (the patch of the unit fixture that it reuses), so the rows pass before and after the release switches the form
constants. Only the reviewer transport and the clock are synthetic: every plan is executed with the real command.
"""

import copy
import os
import shutil
import tempfile
from contextlib import contextmanager, nullcontext
from pathlib import Path
from unittest.mock import patch

from ci.continuity_guard import validate_stop_journeys
from codex_route_fixtures import build_codex_response
import continuity_guard_fixtures as matrix
from research_harness import openness_reading as reading, review_failure_class as failures
from research_harness import review_protocol as protocol, stop_classes
from research_harness.review_continuation import plan_next
from research_harness.errors import ResearchError
from research_harness.storage import Store

UNEXECUTED_STEP_MESSAGE = 'Execute the newly declared step of a stop answer: {step}'
NO_BOUNDARY_MESSAGE = 'The {family} {turn} row reached no boundary within {limit} plans.'
TURN_MESSAGE = 'The {family} fixture did not reach its {turn} turn.'


class FormTurn:
    """A head of one chain kind of 0.92.0, with the fixture that built it and the answer of each of its turns."""

    def __init__(self, family, turn, fixture, case, head):
        self.family, self.turn, self.fixture, self.case, self.head = family, turn, fixture, case, head

    def records(self):
        return self.case.store.snapshot()['records']

    def assignment(self, identifier=None):
        return self.records()['review_assignment'][identifier or self.head]

    def packet(self, identifier=None):
        return protocol._read_json(self.case.artifacts, self.assignment(identifier)['packet'])

    def plan(self, identifier=None):
        return plan_next(self.records(), self.case.artifacts, identifier or self.head)

    def answer(self, identifier=None):
        """A valid answer of the turn, as the unit fixture of its chain kind builds it."""
        identifier = identifier or self.head
        if self.family == 'session-v4':
            packet = self.packet(identifier)
            if 'session_turn' in packet:
                return dict(schema_version=1, stage='request', notes='Bounded notes.', excerpts=[],
                            requests=[], settled_requests=[])
            return self.fixture.answer(self.assignment(identifier))
        if self.family == 'parts-v3':
            return self.fixture.answer(self.assignment(identifier))
        if self.family == 'v7-pages':
            return self.fixture.answer(self.packet(identifier))
        return answer_math_turn(self.fixture, self.packet(identifier), self.assignment(identifier))


def answer_math_turn(fixture, packet, assignment):
    stage = reading.find_stage(packet)
    if stage == 'reading':
        return fixture.build_v7_answer(packet)
    if stage == 'combine':
        binding = packet['reading_combine']
        return dict(block=binding['block']['handle'], summary='Condensed supporting entries.',
                    refs=[binding['block']['entries'][0]['handle']])
    if stage == 'page':
        return dict(targets=[dict(formulation=target, coverage='The sources cover this formulation.',
            sentence='This formulation is covered.') for target in packet['reading_final']['page']['targets']])
    records = fixture.store.snapshot()['records']
    seen = {}
    for row in records['review_assignment'].values():
        earlier = protocol._read_json(fixture.artifacts, row['packet'])
        if row.get('reading_chain', {}).get('first_assignment_id') == assignment['reading_chain']['first_assignment_id'] \
                and 'reading_turn' in earlier:
            seen.update((item['handle'], item['sha256']) for item in earlier['reading_turn']['plan']['now'])
    return fixture.build_final_answer(packet, assignment, seen)


def prepare_unit_fixture(owner, cls, method):
    fixture = cls(method)
    fixture.setUp()
    owner.addCleanup(fixture.doCleanups)
    return fixture


# The turns that one chain passes in order; a state that several rows start from is built once.
TURN_CHAINS = {
    'session-v4': (('request',), ('page', 'closing'), ('final',), ('combine',)),
    'parts-v3': (('read', 'combine', 'page', 'material', 'closing'),),
    'v7-pages': (('page', 'closing'),),
    'math-v2': (('reading', 'final'), ('page', 'closing', 'combine')),
}


def iterate_turns(owner, family, turns):
    """Yield a head of each named turn, in the order of its chain. The caller works on a copy of the store at each
    turn (fork_store), so the chain continues from the same state. Each fixture sets its new form for its own test."""
    for chain in TURN_CHAINS[family]:
        wanted = [turn for turn in chain if turn in turns]
        if wanted:
            yield from WALKERS[family](owner, chain[:chain.index(wanted[-1]) + 1], wanted)


def build_turn(owner, family, turn):
    return next(iterate_turns(owner, family, [turn]))


def walk_session(owner, chain, wanted):
    import test_review_session_v4_pages as pages
    from research_harness import review_session_v4 as session
    fixture = prepare_unit_fixture(owner, pages.SessionPagesTests, 'test_a5_a14_bar_pages_reach_recorded_review')
    if chain == ('final',):
        head = fixture.start(targets=0)
        owner.assertNotIn('page_plan_digest', session.read_session_binding(fixture.read_packet(head)))
        yield FormTurn('session-v4', 'final', fixture, fixture.case, head['id'])
        return
    if chain == ('combine',):
        head = fixture.start_condensing_session()
        owner.assertIn('session_combine', fixture.read_packet(head))
        yield FormTurn('session-v4', 'combine', fixture, fixture.case, head['id'])
        return
    if chain == ('request',):
        yield FormTurn('session-v4', 'request', fixture, fixture.case,
                       fixture.case.assign('bar', 'first', identifier='start')['id'])
        return
    # Nine targets need a final in pages; start answers every request turn before the final.
    form = FormTurn('session-v4', 'page', fixture, fixture.case, fixture.start(targets=9)['id'])
    owner.assertIn('page', session.read_session_binding(form.packet()))
    if 'page' in wanted:
        yield form
    while 'closing' in wanted and 'closing' not in session.read_session_binding(form.packet()):
        form.head = fixture.advance(form.assignment())['id']
    if 'closing' in wanted:
        form.turn = 'closing'
        yield form


def walk_series(owner, chain, wanted):
    import test_review_parts_v3_pages as pages
    fixture = prepare_unit_fixture(owner, pages.PagedSeriesTests,
                                   'test_a6_refused_closing_is_replaced_with_notice_then_records')
    form = FormTurn('parts-v3', 'read', fixture, fixture.case, fixture.start(0)['id'])
    for turn in chain:
        for _ in range(20):
            current = name_series_turn(form.packet())
            if current == turn:
                break
            output = None
            if (current, turn) in (('page', 'material'), ('material', 'closing')):
                # A page that writes an argument in two parts: the second part ends the pending request.
                output = dict(stage='material', title='Independent argument', part=1 + (current == 'material'),
                              text='A part of the finite argument.', more=current == 'page')
            form.head = fixture.advance(form.assignment(), output)['id']
        owner.assertEqual(name_series_turn(form.packet()), turn, TURN_MESSAGE.format(family='parts-v3', turn=turn))
        form.turn = turn
        if turn in wanted:
            yield form


def walk_v7_pages(owner, chain, wanted):
    import test_openness_page_replacement as pages
    fixture = prepare_unit_fixture(owner, pages.PageReplacementTests,
                                   'test_floor_retries_once_then_executes_export_and_route_answers')
    native = fixture.export_targets(9)
    head, _ = fixture.start_final(native)
    form = FormTurn('v7-pages', 'page', fixture, fixture, head)
    if 'page' in wanted:
        yield form
    if 'closing' in wanted:
        form.head, _ = fixture.reach_closing(native, form.head)
        form.turn = 'closing'
        owner.assertEqual(reading.find_stage(form.packet()), 'closing')
        yield form


def walk_math(owner, chain, wanted):
    import test_openness_reading_math_v2 as math
    fixture = prepare_unit_fixture(owner, math.MathV2RecoveryTests,
                                   'test_reading_at_output_limit_is_replaced_at_next_lower_level')
    native = fixture.build_math_packet(neighbours=0 if chain[0] == 'reading' else 40)
    fixture.assign('math-1', native)
    form = FormTurn('math-v2', 'reading', fixture, fixture, 'math-1')
    if 'reading' in wanted:
        yield form
    while reading.find_stage(form.packet()) == 'reading':
        fixture.invoke(form.head, form.answer())
        following = 'math-read-%d' % len(form.records()['review_assignment'])
        fixture.assign(following, native, prior=form.head)
        form.head = following
    for turn in chain[1:] if chain[0] == 'reading' else chain:
        while turn in ('closing', 'combine') and reading.find_stage(form.packet()) == 'page':
            fixture.invoke(form.head, form.answer())
            following = 'math-page-%d' % len(form.records()['review_assignment'])
            fixture.assign(following, native, prior=form.head)
            form.head = following
        if turn == 'combine':
            # A closing page at its output limit is condensed: its replacement is the first condensing turn.
            fixture.invoke_usage(form.head, form.answer(), tokens=128000)
            form.head = fixture.replace(form.head)
        form.turn = turn
        owner.assertEqual(reading.find_stage(form.packet()), turn)
        if turn in wanted:
            yield form


WALKERS = {'session-v4': walk_session, 'parts-v3': walk_series, 'v7-pages': walk_v7_pages, 'math-v2': walk_math}


def name_series_turn(packet):
    """The turn of a series of the parts protocol v3, by the names of list_form_turns."""
    from research_harness import review_parts
    header = packet[review_parts.KEY]
    if 'review_parts_material_request' in packet:
        return 'material'
    if header['kind'] == 'combine':
        return 'condense' if header.get('mode') == 'condense' else 'combine'
    if header['kind'] == 'final':
        return {None: 'final', 'combine': 'condense'}.get(header.get('final_form'), header.get('final_form'))
    return header['kind']


@contextmanager
def fork_store(form):
    """Each row works on a copy of the state that it starts from; the copy is restored after the row."""
    with tempfile.TemporaryDirectory() as directory:
        saved = shutil.copytree(form.case.root, Path(directory) / 'saved')
        try:
            yield form
        finally:
            shutil.rmtree(form.case.root)
            shutil.copytree(saved, form.case.root)
            form.case.store = Store(form.case.root)
            form.case.artifacts = form.case.store.build_artifact_store()


def build_due_clock(deadline):
    """A clock of the failure planner that stands at the time that a wait names."""
    clock = failures.datetime

    class DueClock(clock):
        @classmethod
        def now(cls, tz=None):
            return deadline
    return DueClock


def plan_after_waits(form, identifier=None):
    plan = form.plan(identifier)
    waits = []
    if plan['state'] == 'wait':
        waits.append(plan.get('code'))
        deadline = failures.datetime.fromisoformat(plan['not_before'])
        with patch.object(failures, 'datetime', build_due_clock(deadline)):
            plan = form.plan(identifier)
    return plan, waits


def run_head(form, respond, identifier=None):
    """Execute the run that the plan names, with the transport stub in place of the reviewer."""
    plan = form.plan(identifier)
    if plan['state'] == 'wait':
        plan, _ = plan_after_waits(form, identifier)
    assert plan['state'] == 'run', plan
    with patch.dict(os.environ, {'CODEX_HOME': str(form.case.root / 'synthetic-codex')}), patch.object(
            protocol.transport, 'send_request', side_effect=respond), patch.object(protocol.time, 'sleep'):
        return matrix.execute(form.case, plan['command'], plan['payload'])


def drive_failures(owner, form, respond, outcome, limit=40):
    """Run the head with the stub until its plan is no longer a run or a due wait; count failures by sequence."""
    counts, waits = {}, []
    for _ in range(limit):
        plan, waited = plan_after_waits(form)
        waits.extend(waited)
        for code in waited:
            owner.assertIn(code, stop_classes.STOP_REGISTRY)
        if plan['state'] != 'run':
            return plan, counts, waits
        attempt = run_head(form, respond)
        records = form.records()
        classified = failures.classify_attempt(records, form.case.artifacts, records['review_attempt'][attempt['id']])
        owner.assertEqual(classified['outcome'], outcome)
        position = failures.count_position(records, form.case.artifacts, records['review_assignment'][form.head])
        counts[position['sequence']] = counts.get(position['sequence'], 0) + 1
    owner.fail(NO_BOUNDARY_MESSAGE.format(family=form.family, turn=form.turn, limit=limit))


def check_replacement(owner, form, plan):
    """A replacement on the same route and reviewer that passes the preflight and whose next plan runs."""
    prior = form.assignment()
    following = matrix.execute(form.case, plan['command'], plan['payload'])
    owner.assertEqual((following['route_id'], following['reviewer_id']), (prior['route_id'], prior['reviewer_id']))
    route = form.records()['review_route'][prior['route_id']]
    owner.assertEqual(route['configuration'], form.records()['review_route'][following['route_id']]['configuration'])
    preflight = matrix.check_preflight(form.case, following)
    owner.assertTrue(preflight)
    owner.assertEqual(form.plan(following['id'])['state'], 'run')
    return dict(kind='assignment', id=following['id'], prior_head=prior['id'], eligible=True, preflight=preflight,
                record=following)


def answer_probe(case):
    def respond(route, request, credential):
        packet = reading.review_excerpts.read_sent_packet(request)
        output = {key: packet[key] for key in ('allowed_control', 'evidence_control')}
        output['excluded_controls'] = []
        response = build_codex_response(route, request, output)
        response['usage']['input_tokens'] = getattr(case, 'capacity_probe_tokens', response['usage']['input_tokens'])
        return response
    return respond


def execute_route_step(form, step, identifier):
    """Step 7: record the route of the step with its payload, or probe the named record, and run its nested probe."""
    payload = copy.deepcopy(step['payload'])
    payload['id'] = identifier
    if 'configuration' in step.get('chooses', []):
        # The answer chooses another route: one whose output limit holds the smallest task of the review.
        payload['configuration']['max_output_tokens'] = max(payload['configuration']['max_output_tokens'], 128000)
    if step['command'] == 'review-probe':
        payload.setdefault('route_id', step.get('route_id'))
    with patch.object(protocol.transport, 'send_request', side_effect=answer_probe(form.case)):
        receipt = matrix.execute(form.case, step['command'], payload)
    commands = [step['command']]
    route_id = receipt['id'] if step['command'] == 'review-route' else receipt['route_id']
    for child in step.get('then', []):
        child_payload = dict(child.get('payload', {}))
        child_payload.setdefault('id', identifier + '-probe')
        child_payload.setdefault('route_id', route_id)
        with patch.object(protocol.transport, 'send_request', side_effect=answer_probe(form.case)):
            matrix.execute(form.case, child['command'], child_payload)
        commands.append(child['command'])
    return route_id, commands


def record_evidence(form, text):
    return form.case.artifacts.put(text.encode(), 'text/plain')


def execute_new_chain_step(owner, form, step, identifier, route_id, original=None):
    """Steps 4 and 5: a new packet for a new first assignment, then each nested assignment (a check first). The
    original is the stopped head, or the refused payload of a first assignment."""
    from research_harness.check_mode import check_mutation
    original = original or form.assignment()
    if step['id'] == 4:
        from research_harness.problem_openness import export_openness
        payload = copy.deepcopy(step['payload'])
        owner.assertEqual(payload.pop('kind'), 'problem-openness')
        payload.update(assignment_id=identifier, reviewer_id=original['reviewer_id'],
            minimum=dict(searches=[dict(formulation=row['question'], search_id=row['id']) for row in form.fixture.searches],
                         settling_work_id=None, duplicate_resolutions=[]))
        native = export_openness(form.case.store, payload, form.case.root / (identifier + '-export'),
                                 accept_gaps=True)['native_packet']
    else:
        # The math controller attests a new packet for the new first assignment (step 5); the fixture of the unit
        # lane builds that packet as the controller does.
        native = form.fixture.build_math_packet(name=identifier + '-export', assignment_id=identifier)
    commands = [step['command']]
    following = None
    for child in step['then']:
        payload = copy.deepcopy(child['payload'])
        payload.setdefault('id', identifier)
        payload['route_id'] = payload.get('route_id') or route_id
        payload['reviewer_id'] = payload.get('reviewer_id') or original['reviewer_id']
        payload.setdefault('context', {})['native_packet'] = native
        if child.get('check'):
            result = check_mutation(form.case.store, protocol.record_assignment, payload,
                expected_revision=form.case.store.revision, request_id=identifier + '-check', bounded_payload=payload)
            owner.assertEqual(result['check'], 'accepted')
        else:
            following = matrix.execute(form.case, child['command'], payload)
        commands.append(child['command'])
    return following, commands


def respond_with(output):
    def respond(route, request, credential):
        return build_codex_response(route, request, output)
    return respond


def execute_series_step(owner, form, step):
    """review-series on the head: the stub answers the head with its valid answer, and the series records."""
    from research_harness.review_series import run_series
    owner.assertEqual(step.get('assignment_id', form.head), form.head)
    before = {kind: set(form.records().get(kind, {})) for kind in RECORDED_KINDS}
    attempts = set(form.records().get('review_attempt', {}))
    output = form.answer()
    with patch.object(failures, 'datetime', build_due_clock(failures.datetime(2099, 1, 1, tzinfo=failures.timezone.utc))), \
            patch.object(protocol.transport, 'send_request', side_effect=respond_with(output)):
        status = run_series(form.case.store, form.head, emit=lambda row: None)
    records = form.records()
    ran = [row for key, row in records['review_attempt'].items() if key not in attempts and row['assignment_id'] == form.head]
    owner.assertTrue(ran, status)
    created = [row for kind in RECORDED_KINDS for key, row in records.get(kind, {}).items() if key not in before[kind]]
    return dict(kind='action', command='review-series', receipt=created[0] if created else ran[-1])


RECORDED_KINDS = ('value_review', 'problem_openness', 'review_adjudication', 'verdict_assessment')


def execute_answer(owner, form, answer, index):
    """Execute the steps of one answer in order, each with its real command, and return its end."""
    original = form.assignment()
    identifier = 'answer-%d' % index
    route_id, commands, event, following, end = original['route_id'], [], None, None, None
    for step in answer['steps']:
        number = step.get('id')
        if step.get('command') == 'review-series':
            end = execute_series_step(owner, form, step)
            commands.append('review-series')
        elif number == 8:
            evidence = record_evidence(form, 'The owner retires this route record for the stopped review.')
            event = matrix.execute(form.case, step['command'], dict(step['payload'], id=identifier + '-retire',
                reason='The owner moves the stopped review to another route.', evidence=[evidence]))
            commands.append(step['command'])
        elif number == 9:
            evidence = record_evidence(form, 'The owner grants one further sequence.')
            matrix.execute(form.case, step['command'], dict(step['payload'], id=identifier + '-grant',
                reason='Continue the retained review.', evidence=[evidence]))
            commands.append(step['command'])
        elif number == 7:
            route_id, route_commands = execute_route_step(form, step, identifier + '-route')
            commands.extend(route_commands)
        elif number in (4, 5):
            following, new_commands = execute_new_chain_step(owner, form, step, identifier, route_id)
            commands.extend(new_commands)
        elif number in (1, 2, 6, 14):
            payload = copy.deepcopy(step['payload'])
            payload.update(id=identifier, route_id=route_id)
            payload.setdefault('reviewer_id', original['reviewer_id'])
            if payload.get('reviewer_id') is None:
                payload['reviewer_id'] = original['reviewer_id']
            binding = payload.get('context', {}).get('context_repair')
            if binding is not None and event is not None:
                binding.update(event_ids=binding['event_ids'] + [event['id']],
                               evidence=binding['evidence'] + event['evidence'],
                               probe_id=identifier + '-route-probe', reason='Continue the same stopped review.')
            following = matrix.execute(form.case, step['command'], payload)
            commands.append(step['command'])
        else:
            owner.fail(UNEXECUTED_STEP_MESSAGE.format(step=step))
    if following is not None:
        owner.assertNotEqual(following['id'], original['id'])
        owner.assertEqual(form.plan(following['id'])['state'], 'run')
        # The answer continues the same review: the same role, dossier, subject and statement.
        subject = describe_review_subject(form, following)
        owner.assertEqual(subject, describe_review_subject(form, original))
        end = dict(kind='assignment', id=following['id'], prior_head=original['id'], eligible=True,
                   preflight=matrix.check_preflight(form.case, following), record=following, subject=subject,
                   packet_kind=describe_packet_kind(form, following),
                   protocol=reading.find_protocol(protocol._read_json(form.case.artifacts, following['packet'])))
    owner.assertIsNotNone(end, answer)
    return dict(answer=answer['answer'], accepted_commands=commands, end=end)


def describe_packet_kind(form, assignment):
    """A native review continues over a packet of its kind: a problem openness packet or a math packet."""
    if assignment['role'] != 'native_math':
        return assignment['role']
    from research_harness.native_review import load_packet
    native = load_packet(form.case.artifacts, assignment['context']['native_packet'], assignment['reviewer_id'])
    return 'problem-openness' if reading.is_openness_packet(native) else 'math'


def execute_stop_answers(owner, form, stop, defect=None):
    """Each answer runs on its own copy of the stopped store; the journeys must satisfy the stop contract, and an
    answer that starts a new chain continues the review over a packet of its kind. A cell of
    continuity_guard.FORM_DEFECTS holds the behaviour of the tree, which the report names for the supervisor."""
    owner.assertEqual(stop_classes.classify_stop(stop['code']), 'decision')
    owner.assertIn(stop['code'], stop_classes.STOP_REGISTRY)
    answers = stop['details']['decision']['then']
    owner.assertTrue(answers)
    from ci.continuity_guard import FORM_DEFECTS, REPAIR_HISTORY_FINDING
    kind = describe_packet_kind(form, form.assignment())
    journeys, refused = [], []
    for index, answer in enumerate(answers):
        with owner.subTest(answer=answer['answer']), fork_store(form):
            if FORM_DEFECTS.get((form.family, form.turn, answer['answer'])) == REPAIR_HISTORY_FINDING:
                with owner.assertRaises(ResearchError) as caught:
                    execute_answer(owner, form, answer, index)
                owner.assertEqual(caught.exception.code, 'review_context_repair_history_unrecorded')
                refused.append(answer['answer'])
                continue
            journey = execute_answer(owner, form, answer, index)
            if journey['end'].get('packet_kind') is not None:
                owner.assertEqual(journey['end']['packet_kind'] != kind, defect is not None, (defect, journey['end']))
            journeys.append(journey)
    executed = dict(stop, details=dict(stop['details'], decision=dict(stop['details']['decision'], then=[
        answer for answer in answers if answer['answer'] not in refused])))
    owner.assertEqual(validate_stop_journeys(executed, journeys, stop_classes.STOP_REGISTRY), [])
    return journeys


def execute_handoff(owner, form, plan):
    """A handoff with its step (or its steps in order) executed to an assignment that runs or a verified route."""
    steps = plan['details'].get('steps') or [plan['details']['step']]
    owner.assertEqual(plan['command'], steps[0]['command'])
    journey = execute_answer(owner, form, dict(answer='handoff', steps=steps), 0) if any(
        step.get('id') in (1, 2, 4, 5, 6, 14) for step in steps) else None
    if journey is None:
        from ci.continuity_guard import FORM_DEFECTS, RUNTIME_PROBE_FINDING
        route_id, commands = execute_route_step(form, steps[0], 'handoff-route')
        owner.assertEqual(protocol.route_state(form.records(), form.case.artifacts, route_id)['context_status'],
                          'verified_for_route')
        # The handoff names the time after which the first attempt starts a new sequence.
        deadline = failures.datetime.fromisoformat(plan['not_before']) if plan.get('not_before') else None
        with patch.object(failures, 'datetime', build_due_clock(deadline)) if deadline else nullcontext():
            following = form.plan()
            if FORM_DEFECTS.get(('handoff', plan['code'])) == RUNTIME_PROBE_FINDING:
                owner.assertEqual((following['state'], following['code'], following['command']),
                                  (plan['state'], plan['code'], plan['command']))
                # The store accepts the first attempt after the probe, which starts a new sequence.
                with patch.object(protocol.transport, 'send_request', side_effect=respond_with(form.answer())):
                    receipt = matrix.execute(form.case, 'review-run', dict(id=form.head + '-after-probe',
                                                                           assignment_id=form.head))
                commands.append('review-run')
                owner.assertNotEqual(form.plan().get('code'), plan['code'])
                return dict(commands=commands, end=dict(kind='action', command='review-run', receipt=receipt))
            owner.assertEqual(following['state'], 'run')
        return dict(commands=commands, end=dict(kind='run', id=form.head))
    return dict(commands=journey['accepted_commands'], end=journey['end'])


def refuse_provider(route, request, credential):
    """A safeguard stop of the provider (the outcome provider_refused), as the CLI states it."""
    from codex_route_fixtures import bind_codex_response
    response = build_codex_response(route, request, {})
    response['status'] = 'incomplete'
    response['output'][0]['content'][0]['text'] = 'API Error: safeguards flagged this message'
    return bind_codex_response(route, request, response)


def fail_size_request(outcome, detail):
    """The size failures of the 0.91.0 matrix. A compacted input writes no output, so that a turn whose answer bound is
    small (a read turn of a series) is not read as over its output cap."""
    send = matrix.fail_size_request(outcome, detail)

    def respond(route, request, credential):
        response = send(route, request, credential)
        if detail == 'compacted':
            response['usage']['output_tokens'] = 0
        return response
    return respond


def describe_review_subject(form, assignment):
    """What a replacement and a new chain of the same review keep: the role, the dossier, the subject and, for a
    native review, the subject and the claim of its native packet, whatever form the turn has."""
    subject = dict(role=assignment['role'], dossier_id=assignment.get('dossier_id'), subject=assignment.get('subject'))
    if assignment['role'] == 'native_math':
        from research_harness.native_review import load_packet
        native = load_packet(form.case.artifacts, assignment['context']['native_packet'], assignment['reviewer_id'])
        subject.update(native_subject=native.get('subject'), claim=native.get('claim_digest'))
    return subject


def build_size_row(owner, form, outcome, detail):
    """K8: the size outcome on the head, to a replacement that passes the preflight or to the decision stop with every
    answer executed."""
    from ci.continuity_guard import FORM_DEFECTS, SERIES_READ_COMPACTION_FINDING
    head = form.assignment()
    subject = describe_review_subject(form, head)
    defect = FORM_DEFECTS.get((form.family, form.turn, outcome, detail))
    classified_as = 'output_over_cap' if defect == SERIES_READ_COMPACTION_FINDING else outcome
    plan, _, _ = drive_failures(owner, form, fail_size_request(outcome, detail), classified_as, limit=4)
    records = form.records()
    attempt = max((row for row in records['review_attempt'].values() if row['assignment_id'] == form.head),
                  key=lambda row: row['recorded_revision'])
    if classified_as == outcome:
        classified = failures.classify_attempt(records, form.case.artifacts, attempt)
        owner.assertEqual(classified['signal'].get('detail'), detail)
    if plan['state'] == 'replace':
        boundary = check_replacement(owner, form, plan)
        boundary['subject'] = describe_review_subject(form, boundary['record'])
        commands = [plan['command']]
        owner.assertEqual(boundary['subject'], subject)
    else:
        owner.assertEqual(plan['state'], 'stop', plan)
        journeys = execute_stop_answers(owner, form, plan, defect)
        boundary, commands = journeys[0]['end'], journeys[0]['accepted_commands']
    return dict(family=form.family, turn=form.turn, outcome=outcome, detail=detail, assignment_id=form.head,
                subject=subject, attempt_id=attempt['id'], request_digest=attempt['request_digest'], call=attempt,
                accepted_commands=commands, boundary=boundary, plan=dict(state=plan['state'], code=plan.get('code')))


def walk_to_floor(owner, form, outcome='output_over_cap', limit=40):
    """G4 and K7: each replacement of a size outcome is a smaller task on the same route, the steps end, and the
    decision stop at the floor is executed with every answer. Returns the levels walked and the stop."""
    respond = fail_size_request(outcome, None)
    replacements, start = 0, form.head
    try:
        for _ in range(limit):
            plan, _, _ = drive_failures(owner, form, respond, outcome, limit=4)
            if plan['state'] == 'replace':
                boundary = check_replacement(owner, form, plan)
                form.head = boundary['id']
                replacements += 1
                continue
            owner.assertEqual(plan['state'], 'stop', plan)
            return replacements, plan, execute_stop_answers(owner, form, plan)
        owner.fail(NO_BOUNDARY_MESSAGE.format(family=form.family, turn=form.turn, limit=limit))
    finally:
        # The walk runs on a copy of the store; the chain of the caller continues from its own head.
        form.head = start


def refuse_below_floor(owner, family):
    """A first assignment on a route below the minimum of a final (entry A5), with the new forms installed: the
    command refuses it before any write, and the transport stub receives no request. Returns the holder of the store,
    the refused payload and the refusal."""
    from codex_route_fixtures import record_codex_route
    from unittest.mock import Mock
    if family == 'session-v4':
        import test_review_session_v4_turns as turns
        fixture = prepare_unit_fixture(owner, turns.SessionTurnsTests, 'test_default_remains_v3_and_new_v3_bytes_match_fallback')
        fixture.add_sources([2000])
        case = fixture.case
        record_codex_route(case, 'small-route', model='gpt-6-astra', output_tokens=1500)
        payload = dict(id='small-slot', route_id='small-route', dossier_id=None, role='bar', reviewer_id='second',
                       author_id='author', context={})
    else:
        import test_openness_final_pages as pages
        fixture = prepare_unit_fixture(owner, pages.FinalAdmissionFloorTests,
                                       'test_first_assignment_stops_when_route_cannot_answer_one_target')
        case = fixture
        native = fixture.export_targets(1)
        record_codex_route(case, 'low-output', output_tokens=1000, model='gpt-6-astra')
        payload = dict(id='turn-1', route_id='low-output', dossier_id=None, role='native_math',
                       reviewer_id='openness-reviewer', author_id='problem-author', context=dict(native_packet=native))
    revision = case.store.revision
    sender = Mock(side_effect=AssertionError('An admission stop sent a request'))
    with patch.object(protocol.transport, 'send_request', sender), owner.assertRaises(ResearchError) as caught:
        matrix.execute(case, 'review-assignment', payload)
    owner.assertEqual(sender.call_count, 0)
    owner.assertEqual(case.store.revision, revision)
    return FormTurn(family, 'admission', fixture, case, None), payload, caught.exception


def execute_admission_answers(owner, form, stop, refused):
    """Each answer of an admission stop, on its own copy of the store, to a first assignment of the same review
    that runs and passes the preflight."""
    from ci.continuity_guard import FORM_DEFECTS
    journeys, unserved = [], []
    for index, answer in enumerate(stop['details']['decision']['then']):
        with owner.subTest(answer=answer['answer']), fork_store(form):
            if FORM_DEFECTS.get((form.family, form.turn, answer['answer'])) is not None:
                with owner.assertRaises(ResearchError) as caught:
                    execute_new_chain_step(owner, form, answer['steps'][0], 'admission-%d' % index,
                                           refused['route_id'], original=refused)
                owner.assertEqual(caught.exception.code, stop['code'])
                unserved.append(answer['answer'])
                continue
            commands, following = [], None
            route_id = refused['route_id']
            for step in answer['steps']:
                if step.get('id') == 7:
                    route_id, route_commands = execute_route_step(form, step, 'admission-route-%d' % index)
                    commands.extend(route_commands)
                elif step.get('id') == 4:
                    following, new_commands = execute_new_chain_step(owner, form, step, 'admission-%d' % index,
                                                                     route_id, original=refused)
                    commands.extend(new_commands)
                elif step.get('id') == 14:
                    following = matrix.execute(form.case, step['command'], copy.deepcopy(step['payload']))
                    commands.append(step['command'])
                else:
                    owner.fail(UNEXECUTED_STEP_MESSAGE.format(step=step))
            owner.assertIsNotNone(following)
            owner.assertEqual(form.plan(following['id'])['state'], 'run')
            journeys.append(dict(answer=answer['answer'], accepted_commands=commands, end=dict(
                kind='assignment', id=following['id'], prior_head=None, eligible=True,
                preflight=matrix.check_preflight(form.case, following), record=following)))
    served = dict(stop, details=dict(decision=dict(then=[answer for answer in stop['details']['decision']['then']
                                                         if answer['answer'] not in unserved])))
    owner.assertEqual(validate_stop_journeys(served, journeys, stop_classes.STOP_REGISTRY), [])
    return journeys


def list_page_targets(form, identifier=None):
    """The targets of a page of a final in pages, in the binding of its family."""
    packet = form.packet(identifier)
    if form.family == 'session-v4':
        from research_harness import review_session_v4 as session
        return session.read_session_binding(packet)['page']['targets']
    if form.family == 'parts-v3':
        from research_harness import review_parts
        return packet[review_parts.KEY]['page']['targets']
    return packet['reading_final']['page']['targets']
