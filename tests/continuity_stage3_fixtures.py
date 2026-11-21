"""Stage 3 command journeys; only the reviewer transport and clock are synthetic."""

from contextlib import nullcontext
import copy
import json
from unittest.mock import patch

import continuity_guard_fixtures as matrix
import test_openness_page_replacement as page_replacement
import test_openness_reading_v7_refusal as refusal
from research_harness import openness_reading as reading, openness_reading_v7 as v7
from research_harness import review_protocol as protocol, review_route_capacity as capacity
from research_harness import review_failure_class as failures, stop_classes
from research_harness.review_continuation import plan_next, _describe_chain
from test_review_output_profile import build_row


class Stage3ReadingCase(refusal.V7RefusalTests):
    serving_input_limit = 1300000


class Stage3PagesCase(page_replacement.PageReplacementTests):
    """A v7 chain whose final is in pages, the form that has condensing turns."""


def prepare_turn(owner, stage):
    case = Stage3PagesCase() if stage == 'combine' else Stage3ReadingCase()
    owner.addCleanup(case.doCleanups)
    case.setUp()
    if stage == 'combine':
        native = case.export_targets(9)
        head, _ = case.start_final(native)
        head, _ = case.reach_closing(native, head)
        # A closing page at the output limit is condensed; its replacement is the first condensing turn.
        case.fail_capacity(head)
        head = case.replace(head)
    else:
        native = case.export()
        head = case.assign('turn-1', native)['id'] if stage == 'reading' else case.finish_reading(native)
    records = case.store.snapshot()['records']
    owner.assertEqual(_describe_chain(records['review_assignment'][head]), 'reading')
    owner.assertEqual(reading.find_protocol(case.read_packet(head)), v7.PROTOCOL)
    owner.assertEqual(reading.find_stage(case.read_packet(head)), stage)
    return case, native, head


def execute_size_journey(owner, stage, outcome, detail):
    # An observed profile permits the complete ladder, instead of c=2 ending
    # the over-cap row before all installed reading levels have been exercised.
    # A condensing turn keeps its level and halves its block, so its row runs
    # on the profile of its route, under which the final of its chain is paged.
    priors = nullcontext() if stage == 'combine' else patch.object(capacity, 'read_profile_priors', return_value=[
        build_row(level=None, thinking=1000, count=8, over=1),
        build_row(kind='final', level=None, thinking=1000, count=8, over=1)])
    with priors:
        case, native, head = prepare_turn(owner, stage)
        original = case.store.snapshot()['records']['review_assignment'][head]
        levels, calls, position_calls = [], [], []
        for number in range(failures.TURN_FAILURE_LIMIT + 8):
            records = case.store.snapshot()['records']
            action = plan_next(records, case.artifacts, head)
            if action['state'] == 'stop':
                owner.assertIn(action['code'], stop_classes.STOP_REGISTRY)
                execute_stop_answers(owner, case, head, action)
                return dict(stage=stage, outcome=outcome, detail=detail, levels=levels,
                            calls=calls, code=action['code'], end=action['state'])
            if action['state'] == 'handoff':
                owner.assertEqual(action['command'], 'export')
                reference, assigned = refusal.LegacySuccessorTests.execute_export(case, action)
                owner.assertEqual(plan_next(case.store.snapshot()['records'], case.artifacts, assigned['id'])['state'], 'run')
                case.finish_from(reference, assigned['id'])
                return dict(stage=stage, outcome=outcome, detail=detail, levels=levels,
                            calls=calls, code=action['code'], end=action['state'])
            if action['state'] == 'replace':
                prior = records['review_assignment'][head]
                following = matrix.execute(case, action['command'], action['payload'])
                owner.assertEqual(following['route_digest'], original['route_digest'])
                owner.assertEqual(following['reviewer_id'], original['reviewer_id'])
                owner.assertLess(measure_task(case.read_packet(following['id']), stage),
                                 measure_task(case.read_packet(head), stage))
                owner.assertEqual(following['reading_chain']['turn'], prior['reading_chain']['turn'])
                owner.assertTrue(matrix.check_preflight(case, following))
                head = following['id']
                if stage == 'combine':
                    # Each half of a split block is a new position, as the two pages of a split are (D2 G4); a
                    # reading or final replacement keeps its position, whose failed attempts are counted on.
                    position_calls = []
                continue
            owner.assertEqual(action['state'], 'run', action)
            level = measure_task(case.read_packet(head), stage)
            if not levels or levels[-1] != level:
                levels.append(level)
            with patch.object(protocol.transport, 'send_request', side_effect=matrix.fail_size_request(outcome, detail)):
                attempt = matrix.execute(case, action['command'], action['payload'])
            records = case.store.snapshot()['records']
            classified = failures.classify_attempt(records, case.artifacts, records['review_attempt'][attempt['id']])
            owner.assertEqual((classified['outcome'], classified['signal'].get('detail')), (outcome, detail))
            calls.append(attempt['id'])
            position_calls.append(attempt['id'])
            position = failures.count_position(records, case.artifacts, records['review_assignment'][head])
            owner.assertEqual(position['failed_attempts'], len(position_calls))
            owner.assertEqual(position['failures_by_outcome'][outcome], len(position_calls))
        owner.fail('A size row must end at a decision with an executed next assignment.')


def measure_task(packet, stage):
    """The size of a turn's task: the material allowance of a reading or final turn, and the number of entries
    in the block of a condensing turn, which a replacement halves at the same allowance."""
    if stage == 'combine':
        return len(packet['reading_combine']['block']['entries'])
    return packet['reading_turn' if stage == 'reading' else 'reading_final']['capacity']['material_allowance']


def record_finding(case, identifier):
    records = case.store.snapshot()['records']
    final = records['review_assignment'][identifier]
    turns = reading.verify_chain(records, case.artifacts, final)
    result = matrix.execute(case, 'problem-openness', dict(assignment_id=identifier))
    case.assertEqual(result['reading_turns'], turns)
    case.assertEqual(result['outcome'], 'unresolved')
    return result


def execute_stop_answers(owner, case, head, stop):
    """Fork a stopped store and execute every answer, including nested checks."""
    import shutil
    import tempfile
    from pathlib import Path
    from research_harness.storage import Store
    from research_harness.check_mode import check_mutation
    from codex_route_fixtures import build_codex_response
    from ci.continuity_guard import validate_stop_journeys
    journeys = []
    owner.assertTrue(stop['details']['decision']['then'])
    owner.assertEqual(stop_classes.classify_stop(stop['code']), 'decision')
    original = case.store.snapshot()['records']['review_assignment'][head]
    with tempfile.TemporaryDirectory() as directory:
        saved = shutil.copytree(case.root, Path(directory) / 'stopped')
        for index, answer in enumerate(stop['details']['decision']['then']):
            shutil.rmtree(case.root)
            shutil.copytree(saved, case.root)
            case.store = Store(case.root)
            case.artifacts = case.store.build_artifact_store()
            route_id, commands = original['route_id'], []
            identifier = 'answer-%d' % index
            following = None
            end = None
            for step in answer['steps']:
                if step.get('command') == 'review-series':
                    from research_harness.review_series import run_series
                    from codex_route_fixtures import build_codex_response
                    clock = failures.datetime
                    class LaterClock(clock):
                        @classmethod
                        def now(cls, tz=None):
                            return cls(2099, 1, 1, tzinfo=failures.timezone.utc)
                    def respond(route, request, credential):
                        packet = reading.review_excerpts.read_sent_packet(request)
                        if reading.find_protocol(packet) == v7.PROTOCOL:
                            output = (case.build_v7_answer(packet) if 'reading_turn' in packet else
                                      case.build_v7_finding(packet, packet['reading_final']['notes_handle']))
                        else:
                            output = (case.build_reading_response(packet, excerpts=[]) if 'reading_turn' in packet else
                                      case.build_native_result(packet, outcome='unresolved'))
                        return build_codex_response(route, request, output)
                    owner.assertEqual(step['assignment_id'], head)
                    old_findings = set(case.store.snapshot()['records'].get('problem_openness', {}))
                    with patch.object(failures, 'datetime', LaterClock), patch.object(protocol.transport, 'send_request', side_effect=respond):
                        owner.assertEqual(run_series(case.store, step['assignment_id'], emit=lambda row: None), 0)
                    commands.append('review-series')
                    findings = case.store.snapshot()['records']['problem_openness']
                    created = set(findings) - old_findings
                    owner.assertEqual(len(created), 1)
                    record = findings[created.pop()]
                    owner.assertEqual(record['outcome'], 'unresolved')
                    final = case.store.snapshot()['records']['review_assignment'][record['assignment_id']]
                    owner.assertEqual(final['reading_chain']['first_assignment_id'], original['reading_chain']['first_assignment_id'])
                    reading.verify_chain(case.store.snapshot()['records'], case.artifacts, final)
                    end = dict(kind='action', command='review-series', receipt=record)
                elif step['id'] == 7:
                    route_id = identifier + '-route'
                    route_id, route_commands = execute_route_step(owner, case, step, route_id)
                    commands.extend(route_commands)
                elif step['id'] == 4:
                    from research_harness.problem_openness import export_openness
                    payload = copy.deepcopy(step['payload'])
                    owner.assertEqual(payload.pop('kind'), 'problem-openness')
                    payload.update(assignment_id=identifier, reviewer_id=original['reviewer_id'],
                        minimum=dict(searches=[dict(formulation=row['question'], search_id=row['id']) for row in case.searches],
                                     settling_work_id=None, duplicate_resolutions=[]))
                    native = export_openness(case.store, payload, case.root / (identifier + '-export'), accept_gaps=True)['native_packet']
                    commands.append(step['command'])
                    for child in step['then']:
                        payload = copy.deepcopy(child['payload'])
                        payload.setdefault('id', identifier)
                        payload.setdefault('route_id', route_id)
                        payload.setdefault('reviewer_id', original['reviewer_id'])
                        payload.setdefault('context', {})['native_packet'] = native
                        if child.get('check'):
                            result = check_mutation(case.store, protocol.record_assignment, payload,
                                expected_revision=case.store.revision, request_id=identifier + '-check', bounded_payload=payload)
                            owner.assertEqual(result['check'], 'accepted')
                        else:
                            following = matrix.execute(case, child['command'], payload)
                        commands.append(child['command'])
                elif step['id'] == 9:
                    evidence = case.artifacts.put(b'The owner grants one further sequence.', 'text/plain')
                    payload = dict(step['payload'], id=identifier + '-grant',
                                   reason='Continue the retained review.', evidence=[evidence])
                    matrix.execute(case, step['command'], payload)
                    commands.append(step['command'])
                else:
                    owner.fail('Execute the newly declared Stage 3 step: %r' % step)
            if following is None and end is None:
                # The owner extension keeps the head; run its actual assignment
                # once and let the real planner advance or record it.
                packet = case.read_packet(head)
                output = case.build_v7_finding(packet, packet['reading_final']['notes_handle']) if 'reading_final' in packet else case.build_v7_answer(packet)
                case.invoke(head, output, attempt=identifier + '-run')
                commands.append('review-run')
                action = plan_next(case.store.snapshot()['records'], case.artifacts, head)
                owner.assertIn(action['state'], ('record', 'continue'), action)
                if action['state'] == 'record':
                    result = matrix.execute(case, action['command'], action['payload'])
                    end = dict(kind='action', command=action['command'], receipt=result)
                    commands.append(action['command'])
                else:
                    following = matrix.execute(case, action['command'], action['payload'])
            if following is not None:
                owner.assertEqual(plan_next(case.store.snapshot()['records'], case.artifacts, following['id'])['state'], 'run')
                end = dict(kind='assignment', id=following['id'], prior_head=head, eligible=True,
                           preflight=matrix.check_preflight(case, following))
            journeys.append(dict(answer=answer['answer'], accepted_commands=commands, end=end))
    owner.assertEqual(validate_stop_journeys(stop, journeys, stop_classes.STOP_REGISTRY), [])
    return journeys


def fail_outcome_request(case, outcome, *, scheduled=True):
    """Retain the provider's failure evidence through the normal invocation edge."""
    from codex_route_fixtures import build_codex_response, bind_codex_response
    errors = dict(runtime_unavailable='review_runtime_missing', runtime_stale='review_route_runtime_stale',
                  format_unsupported='review_format_unsupported', interrupted='review_invocation_interrupted')
    def respond(route, request, credential):
        if outcome in errors:
            from research_harness.errors import ResearchError
            raise ResearchError(errors[outcome], 'Synthetic retained reviewer failure.')
        response = build_codex_response(route, request, {})
        response['status'] = 'incomplete'
        answer = dict(usage_limit="You've reached your usage limit", provider_unavailable='API Error: unavailable',
                      execution_incomplete='Incomplete answer', answer_malformed='not JSON', unknown='').get(outcome, '')
        if outcome == 'unknown' and not scheduled:
            answer = 'Undecided failure'
            response['status'] = 'failed'
        if outcome == 'answer_malformed':
            response['status'] = 'completed'
        response['output'][0]['content'][0]['text'] = answer
        response = bind_codex_response(route, request, response)
        if outcome == 'context_unverified':
            response['status'] = 'completed'
            response['output'][0]['content'][0]['text'] = '{}'
            response['context_evidence'].update(inspected=True, input_verified=False)
        if outcome == 'model_at_capacity':
            # The public transport retains the same local rollout diagnostic
            # read by the normal error classifier, without a classifier patch.
            folder = case.root / 'synthetic-codex/sessions'
            folder.mkdir(parents=True, exist_ok=True)
            (folder / 'matrix-provider.jsonl').write_text(json.dumps(dict(payload=dict(type='task_complete',
                error=dict(codex_error_info='server_overloaded', message='Synthetic capacity diagnostic.')))) + '\n')
            response['context_evidence']['thread_id'] = 'matrix-provider'
        return response
    return respond


def execute_route_step(owner, case, step, identifier):
    """Execute a route/probe handoff with its fixed bindings and nested steps."""
    from codex_route_fixtures import build_codex_response
    payload = copy.deepcopy(step['payload'])
    payload['id'] = identifier
    if 'configuration' in step['chooses']:
        payload['configuration']['timeout_seconds'] = 10
    def probe(route, request, credential):
        packet = reading.review_excerpts.read_sent_packet(request)
        output = {key: packet[key] for key in ('allowed_control', 'evidence_control')}
        output['excluded_controls'] = []
        response = build_codex_response(route, request, output)
        response['usage']['input_tokens'] = case.capacity_probe_tokens
        return response
    with patch.object(protocol.transport, 'send_request', side_effect=probe):
        receipt = matrix.execute(case, step['command'], payload)
    commands = [step['command']]
    route_id = receipt['id'] if step['command'] == 'review-route' else receipt['route_id']
    for child in step.get('then', []):
        child_payload = dict(child.get('payload', {}))
        child_payload.setdefault('id', identifier + '-probe')
        child_payload.setdefault('route_id', route_id)
        with patch.object(protocol.transport, 'send_request', side_effect=probe):
            matrix.execute(case, child['command'], child_payload)
        commands.append(child['command'])
    return route_id, commands
