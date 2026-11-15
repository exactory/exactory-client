"""Command-backed size-boundary journeys shared by the continuity matrix."""

import copy
import json
import shutil
import tempfile
from contextlib import nullcontext
from unittest.mock import patch

from codex_route_fixtures import build_codex_response, invoke_codex, record_codex_route, probe_codex_route
from research_harness import cli, review_protocol as protocol, review_transport as transport
from research_harness import review_preflight, review_excerpts
from research_harness.errors import ResearchError
from research_harness.review_continuation import plan_next
from review_parts_fixtures import make_output
from test_review_parts_study import VerificationPartsTests
from openness_reading_fixtures import OpennessReadingCase
from test_research_problem_reviews import ProblemReviewTests
from test_research_review_protocol import ReviewProtocolTests

PARTS_KIND_MESSAGE = 'The real parts series did not reach {kind}'
UNEXECUTED_SIZE_STEP_MESSAGE = 'Execute the newly declared size answer step: {step}'
STAGED_END_MESSAGE = 'The failed staged task did not resume to its recorded review.'


class MatrixPartsCase(VerificationPartsTests):
    def setUp(self):
        self.serving_input_limit = transport.CODEX_INPUT_LIMIT_CHARS
        super().setUp()


def execute(case, command, payload):
    return cli.OPERATIONS[command](case.store, payload,
        expected_revision=case.store.revision,
        request_id='size-matrix-' + str(case.store.revision))['result']


def fail_size_request(outcome, detail):
    def send(route, request, credential):
        if outcome == 'response_over_limit':
            raise ResearchError('review_response_too_large', 'Synthetic retained response body limit.')
        if outcome == 'input_over_limit' and detail is None:
            raise ResearchError('review_input_too_large', 'Synthetic retained input limit.')
        response = (build_codex_response(route, request, {}) if route['adapter'] == 'codex_cli_v1'
            else dict(id='provider-response', model=route['model'], usage=dict(input_tokens=37, output_tokens=12),
                output=[dict(type='message', content=[dict(type='output_text', text='{}')])]))
        response['status'] = 'incomplete'
        if outcome == 'output_over_cap':
            response['usage']['output_tokens'] = route['configuration']['max_output_tokens']
            maximum = route['configuration']['max_output_tokens'] // (2 if detail else 1)
            response['output'][0]['content'][0]['text'] = (
                "API Error: Claude's response exceeded the %d output token maximum." % maximum)
        elif outcome == 'timeout':
            response['usage']['output_tokens'] = 0
            response['output'][0]['content'][0]['text'] = ''
            response.setdefault('execution', {})['timeout'] = True
        else:
            response['context_evidence'] = dict(issues=['uninspected_compaction'])
        return response
    return send


def prepare_assignment(owner, kind):
    if kind == 'correction':
        return prepare_correction(owner)
    if kind == 'staged':
        from test_review_continuation_staged import StagedContinuationTests
        fixture = owner.prepare(StagedContinuationTests)
        fixture.execute(fixture.plan())
        output = fixture.case.build_review_output('result')
        output['value'].update(objective_adequacy='not_assessed', method_adequacy='not_assessed')
        (fixture.case.root / 'scientific.json').write_text(json.dumps(output))
        return fixture.case, fixture.controller, fixture
    if kind in ('read', 'combine', 'check', 'final'):
        fixture = owner.prepare(MatrixPartsCase)
        fixture.enlarge_verification()
        head = fixture.assign()
        for number in range(20):
            packet = fixture.packet(head)
            current = packet['review_parts']['kind']
            if current == kind:
                return fixture.case, head, fixture
            output = make_output(packet)
            if current == 'combine' and kind == 'check':
                unit = packet['review_parts_state']['cards'][0]['unit']
                output = make_output(packet, [dict(units=[unit], question='Does the original support this card?',
                    view='Check its exact original.', read=[dict(unit=unit)])])
            fixture.invoke(head, output)
            head = fixture.assign('prepare-' + str(number), head['id'])
        raise AssertionError(PARTS_KIND_MESSAGE.format(kind=kind))
    if kind == 'reading':
        case = owner.prepare(OpennessReadingCase)
        case.limit_input()
        reference = case.export(assignment_id='size-head')
        return case, case.assign('size-head', reference), case
    if kind == 'single':
        case = owner.prepare(ReviewProtocolTests)
        record_codex_route(case, output_tokens=128000)
        return case, case.assign()['result'], case
    case = owner.prepare(ProblemReviewTests)
    case.supporting = case.artifacts.put(b'A synthetic field standard.', 'text/plain')
    record_codex_route(case, output_tokens=128000)
    initial = case.assign('bar', 'first', **case.deliver_supporting('first'))
    output = case.build_review_output('bar')
    output.update(source_requests=[dict(id='inventory', kind='inventory_page', page=0)])
    output['value']['status'] = 'unresolved'
    invoke_codex(case, initial['id'], output)
    plan = plan_next(case.store.snapshot()['records'], case.artifacts, initial['id'])
    return case, execute(case, plan['command'], plan['payload']), case


def prepare_correction(owner):
    case = owner.prepare(ProblemReviewTests)
    from research_harness import review_route_capacity as capacity
    from test_review_output_profile import build_row
    profile = patch.object(capacity, 'read_profile_priors', return_value=[
        dict(build_row(level=None, kind='single', thinking=1), largest_output_tokens=12)])
    profile.start()
    case.addCleanup(profile.stop)
    case.supporting = case.artifacts.put(b'A synthetic field standard.', 'text/plain')
    record_codex_route(case, output_tokens=128000)
    for role, reviewer in (('bar', 'first'), ('bar', 'second'), ('result', 'first')):
        context = case.deliver_supporting(reviewer) if role == 'bar' else {}
        assignment = case.assign(role, reviewer, **context)
        output = case.build_review_output(role)
        invoke_codex(case, assignment['id'], output)
        execute(case, 'value-review', dict(output, id='review-' + assignment['id'],
            dossier_id=None, assignment_id=assignment['id']))
    execute(case, 'review-context', dict(id='matrix-exposure', assignment_id=assignment['id'],
        kind='contamination', source='author_history', reason='The earlier context was exposed.',
        evidence=[case.supporting]))
    probe_codex_route(case, 'matrix-repair-probe')
    head = execute(case, 'review-assignment', dict(id='matrix-correction-head', route_id='route',
        dossier_id=None, role='result', reviewer_id='first', author_id='author',
        context=dict(context_repair=dict(assignment_id=assignment['id'], event_ids=['matrix-exposure'],
            probe_id='matrix-repair-probe', reason='Reassess the preserved result.', evidence=[case.supporting]))))
    output = case.build_review_output('result')
    output['value']['reason'] = 'The retained scientific wording differs from the predecessor.'
    output['reassessment'] = dict(assignment_id=assignment['id'], findings=[dict(
        review_id='review-' + assignment['id'], disposition='confirmed',
        reason='The exact scientific assessment remains unchanged.', evidence=[case.supporting])])
    for number in range(2):
        invoke_codex(case, head['id'], output, 'matrix-refused-' + str(number))
        with owner.assertRaises(ResearchError) as caught:
            execute(case, 'value-review', dict(output, id='matrix-refused-review',
                dossier_id=None, assignment_id=head['id']))
        owner.assertEqual(caught.exception.code, 'review_reassessment_mismatch')
    return case, head, case


def check_preflight(case, assignment):
    records = case.store.snapshot()['records']
    request = protocol.build_assignment_request(records, case.artifacts, assignment)
    return review_preflight.check_request(
        review_preflight.build_assignment_report(records, case.artifacts, assignment, request), request,
        packet=json.loads(case.artifacts.read(assignment['packet'])), artifacts=case.artifacts)


def describe_subject(case, assignment):
    packet = json.loads(case.artifacts.read(assignment['packet']))
    native = packet.get('native', {})
    return dict(role=assignment['role'], dossier_id=assignment.get('dossier_id'),
        subject=assignment.get('subject', packet.get('subject')),
        native_subject=native.get('subject_digest'), claim=native.get('claim_digest'))


def execute_decision(owner, case, head, stop, fixture):
    # The serving backend must fit the retained repair history as well as the old turn.
    context = (patch.object(transport, 'CODEX_INPUT_LIMIT_CHARS', fixture.serving_input_limit)
               if isinstance(fixture, MatrixPartsCase) else nullcontext())
    with context:
        return _execute_decision(owner, case, head, stop, fixture)


def _execute_decision(owner, case, head, stop, fixture):
    """Execute every supplied answer; current size stops each have one route answer."""
    answers = stop['details']['decision']['then']
    owner.assertEqual(len(answers), 1, 'A new answer needs its own independent fixture journey.')
    commands = []
    event = None
    following = None
    for step in answers[0]['steps']:
        number = step['id']
        if number == 8:
            evidence = case.artifacts.put(b'Continue this stopped review on a serving route.', 'text/plain')
            event = execute(case, step['command'], dict(step['payload'], id='matrix-retire',
                reason='The earlier request reached its size boundary.', evidence=[evidence]))
            commands.append(step['command'])
        elif number == 7:
            record_codex_route(case, 'matrix-serving', output_tokens=128000)
            commands.extend(['review-route', 'review-probe'])
        elif number == 4:
            reference = fixture.export(name='matrix-new-export', assignment_id='matrix-following')
            commands.append('export')
            following = execute(case, 'review-assignment', dict(id='matrix-following', role='native_math',
                route_id='matrix-serving', dossier_id=None, reviewer_id='openness-reviewer',
                author_id=head['author_id'], context=dict(native_packet=reference)))
            commands.append('review-assignment')
        elif number in (1, 6):
            payload = copy.deepcopy(step['payload'])
            payload.update(id='matrix-following', route_id='matrix-serving', reviewer_id=head['reviewer_id'])
            if number == 1:
                binding = payload['context']['context_repair']
                binding.update(event_ids=binding['event_ids'] + [event['id']],
                    evidence=binding['evidence'] + event['evidence'],
                    probe_id='probe-matrix-serving', reason='Continue the same stopped review.')
            following = execute(case, step['command'], payload)
            commands.append(step['command'])
        else:
            raise AssertionError(UNEXECUTED_SIZE_STEP_MESSAGE.format(step=step))
    owner.assertIsNotNone(following)
    owner.assertNotEqual(following['id'], head['id'])
    owner.assertEqual(describe_subject(case, following), describe_subject(case, head))
    preflight = check_preflight(case, following)
    plan = plan_next(case.store.snapshot()['records'], case.artifacts, following['id'])
    owner.assertEqual(plan['state'], 'run', plan)
    boundary = dict(kind='assignment', id=following['id'], prior_head=head['id'], eligible=True,
        preflight=preflight, subject=describe_subject(case, following), record=following)
    from ci.continuity_guard import validate_stop_journeys
    from research_harness.stop_classes import STOP_REGISTRY
    owner.assertEqual(validate_stop_journeys(stop, [dict(answer=answers[0]['answer'],
        accepted_commands=commands, end=boundary)], STOP_REGISTRY), [])
    return boundary, commands


def execute_assignment_size(owner, kind, outcome, detail, prepared=None):
    case, head, fixture = prepared or prepare_assignment(owner, kind)
    if kind == 'correction':
        return execute_correction_size(owner, outcome, detail, prepared=(case, head, fixture))
    if kind == 'staged':
        return execute_staged_size(owner, outcome, detail, (case, head, fixture))
    packet = json.loads(case.artifacts.read(head['packet']))
    from research_harness.review_route_capacity import describe_task_kind
    owner.assertEqual(describe_task_kind(head['role'], packet), kind)
    attempt = None
    for _ in range(3):
        plan = plan_next(case.store.snapshot()['records'], case.artifacts, head['id'])
        owner.assertEqual(plan['state'], 'run', plan)
        with patch.object(transport, 'send_request', side_effect=fail_size_request(outcome, detail)):
            attempt = execute(case, plan['command'], plan['payload'])
        plan = plan_next(case.store.snapshot()['records'], case.artifacts, head['id'])
        if plan['state'] != 'run':
            break
    records = case.store.snapshot()['records']
    from research_harness.review_failure_class import classify_attempt
    classified = classify_attempt(records, case.artifacts, records['review_attempt'][attempt['id']])
    owner.assertEqual((classified['outcome'], classified['signal'].get('detail')), (outcome, detail))
    if plan['state'] == 'replace':
        following = execute(case, plan['command'], plan['payload'])
        preflight = check_preflight(case, following)
        records = case.store.snapshot()['records']
        owner.assertGreater(following['recorded_revision'], head['recorded_revision'])
        owner.assertEqual(plan_next(records, case.artifacts, following['id'])['state'], 'run')
        owner.assertEqual(protocol.context_repair.slot_id(records, following),
            protocol.context_repair.slot_id(records, head))
        owner.assertEqual(describe_subject(case, following), describe_subject(case, head))
        boundary = dict(kind='assignment', id=following['id'], prior_head=head['id'],
            eligible=True, preflight=preflight, subject=describe_subject(case, following), record=following)
        commands = [plan['command']]
    else:
        owner.assertEqual(plan['state'], 'stop', plan)
        boundary, commands = execute_decision(owner, case, head, plan, fixture)
    return dict(kind=kind, outcome=outcome, detail=detail, assignment_id=head['id'],
        subject=describe_subject(case, head), attempt_id=attempt['id'], request_digest=attempt['request_digest'],
        call=attempt, accepted_commands=commands, boundary=boundary)


def execute_assignment_sizes(owner, kind, outcomes):
    """Fork one real prepared store per outcome, preserving independent command histories."""
    from research_harness.storage import Store
    prepared = prepare_assignment(owner, kind)
    case = prepared[0]
    rows = []
    with tempfile.TemporaryDirectory() as directory:
        saved = shutil.copytree(case.root, directory + '/prepared')
        for outcome, detail in outcomes:
            with owner.subTest(kind=kind, outcome=outcome, detail=detail):
                shutil.rmtree(case.root)
                shutil.copytree(saved, case.root)
                case.store = Store(case.root)
                case.artifacts = case.store.build_artifact_store()
                rows.append(execute_assignment_size(owner, kind, outcome, detail, prepared))
    return rows


def execute_correction_size(owner, outcome, detail, prepared=None):
    from research_harness.review_failure_class import classify_attempt
    from codex_route_fixtures import install_codex_runtime
    case, head, fixture = prepared or prepare_correction(owner)
    install_codex_runtime(case)
    with patch.object(transport, 'send_request', side_effect=fail_size_request(outcome, detail)):
        attempt = execute(case, 'review-output-correction',
            dict(id='matrix-correction', assignment_id=head['id'], route_id=head['route_id']))
    request = json.loads(case.artifacts.read(attempt['request']))
    owner.assertEqual(review_excerpts.read_sent_packet(request)['protocol'],
        'exactory-reassessment-disposition-correction-v1')
    records = case.read_records()
    classified = classify_attempt(records, case.artifacts, attempt)
    owner.assertEqual((classified['outcome'], classified['signal'].get('detail')), (outcome, detail))
    stop = plan_next(records, case.artifacts, head['id'])
    owner.assertEqual((stop['state'], stop['code']), ('stop', 'review_slot_exhausted'))
    boundary, commands = execute_decision(owner, case, head, stop, fixture)
    return dict(kind='correction', outcome=outcome, detail=detail, assignment_id=head['id'],
        subject=describe_subject(case, head), attempt_id=attempt['id'], request_digest=attempt['request_digest'],
        call=attempt, accepted_commands=['review-output-correction'] + commands, boundary=boundary)


def execute_probe_size(owner, outcome, detail):
    from codex_route_fixtures import install_codex_runtime
    from research_harness import review_route_capacity as capacity
    from research_harness.evidence import digest
    case = owner.prepare(ReviewProtocolTests)
    install_codex_runtime(case)
    execute(case, 'review-route', dict(id='route', adapter='codex_cli_v1', model='fixture-model',
        endpoint='codex://local', configuration=dict(max_output_tokens=128000, timeout_seconds=5)))
    with patch.object(transport, 'send_request', side_effect=fail_size_request(outcome, detail)) as sender:
        probe = execute(case, 'review-probe', dict(id='matrix-probe', route_id='route'))
    owner.assertEqual(sender.call_count, 1)
    records = case.records()
    route = records['review_route']['route']
    request = json.loads(case.artifacts.read(probe['request']))
    response = json.loads(case.artifacts.read(probe['response'])) if probe.get('response') else {}
    profile = capacity.describe_attempt(route, 'probe', review_excerpts.read_sent_packet(request),
        request, response, operation='review.route_probe', attempt_error=probe.get('error'))
    owner.assertEqual(profile['task']['kind'], 'probe')
    from research_harness.review_failure_class import find_capacity_signal
    answer = ''.join(part['text'] for item in response.get('output', [])
        for part in item.get('content', []))
    signal = find_capacity_signal(route, probe, response, answer)
    if outcome in ('output_over_cap', 'response_over_limit') or detail == 'compacted':
        owner.assertEqual((signal['outcome'], signal['signal'].get('detail')), (outcome, detail))
    elif outcome == 'timeout':
        owner.assertTrue(response['execution']['timeout'])
    else:
        owner.assertEqual(probe['error']['code'], 'review_input_too_large')
    owner.assertFalse(profile['verified_input_tokens'])
    owner.assertFalse(profile['completed_generation'])
    owner.assertEqual(protocol.route_state(records, case.artifacts, 'route')['context_status'], 'unverified')
    # Failure is the recorded action for a probe; it supplies no eligible reviewer assignment.
    return dict(kind='probe', outcome=outcome, detail=detail, attempt_id=probe['id'],
        request_digest=digest(request), call=probe, accepted_commands=['review-probe'],
        boundary=dict(kind='action', command='review-probe', receipt=probe))


def execute_staged_size(owner, outcome, detail, prepared):
    import sys
    from test_review_staged_frontier import WORKER
    from research_harness import review_worker
    from research_harness.review_failure_class import classify_attempt
    case, head, fixture = prepared
    worker = WORKER.replace("    output = {} if (root / 'refuse').exists() else build_output(body, root)",
        "    failure = root / 'size-failure.json'\n"
        "    if failure.exists():\n"
        "        from continuity_guard_fixtures import fail_size_request\n"
        "        outcome, detail = __import__('json').loads(failure.read_text())\n"
        "        failure.unlink()\n"
        "        return fail_size_request(outcome, detail)(route, request, credential)\n"
        "    output = build_output(body, root)")
    (case.root / 'size-failure.json').write_text(json.dumps([outcome, detail]))
    commands = []
    with patch.object(review_worker, '_entry_command', return_value=[sys.executable, '-c', worker]):
        plan = plan_next(case.read_records(), case.artifacts, head['id'])
        owner.assertEqual(plan['state'], 'run', plan)
        attempt = execute(case, plan['command'], plan['payload'])
        commands.append(plan['command'])
        records = case.read_records()
        packet = json.loads(case.artifacts.read(records['review_assignment'][attempt['assignment_id']]['packet']))
        owner.assertIn('staged_review', packet)
        classified = classify_attempt(records, case.artifacts, records['review_attempt'][attempt['id']])
        owner.assertEqual((classified['outcome'], classified['signal'].get('detail')), (outcome, detail))
        for _ in range(20):
            plan = plan_next(case.read_records(), case.artifacts, head['id'])
            owner.assertIn(plan['state'], ('run', 'plan', 'record'), plan)
            result = execute(case, plan['command'], plan['payload'])
            commands.append(plan['command'])
            if plan['state'] == 'record':
                owner.assertEqual(result['assignment_id'], head['id'])
                owner.assertEqual(plan_next(case.read_records(), case.artifacts, head['id'])['state'], 'done')
                break
        else:
            raise AssertionError(STAGED_END_MESSAGE)
    return dict(kind='staged', outcome=outcome, detail=detail, assignment_id=head['id'],
        subject=describe_subject(case, head), attempt_id=attempt['id'], request_digest=attempt['request_digest'],
        call=attempt, accepted_commands=commands,
        boundary=dict(kind='action', command=plan['command'], receipt=result))
