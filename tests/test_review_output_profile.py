"""R3 task accounting, outcome-complete profiles and frozen review compatibility."""
import base64
import copy
import importlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from research_harness import review_transport as transport
from research_harness.evidence import digest
from test_review_route_capacity import build_route


def load_capacity():
    return importlib.import_module('research_harness.review_route_capacity')


def build_request(route, packet, instructions='Return JSON.'):
    return transport.request_body(route, instructions, packet)


def build_response(thinking=(500, 700), retries=0, users=1, output=5000, completed=True):
    events = [dict(type='system', subtype='thinking_tokens', estimated_tokens_delta=value) for value in thinking]
    if retries:
        events.insert(1, dict(type='system', subtype='api_retry'))
    return dict(
        status='completed' if completed else 'incomplete',
        usage=dict(input_tokens=100, output_tokens=output),
        context_evidence=dict(input_verified=completed, cli_version='2.1.synthetic'),
        output=[dict(type='message', content=[dict(type='output_text', text='{"ok":true}')])],
        execution=dict(
            event_types=['user'] * users + ['assistant'] * (2 if completed else 4),
            system_events=dict(
                truncated=False,
                events=[dict(event_index=index, raw_base64=base64.b64encode(json.dumps(event).encode()).decode()) for index, event in enumerate(events)],
            ),
        ),
    )


def build_sample(characters=100000, thinking=50000, output=65000, completed=True, sends=1):
    return dict(
        task=dict(
            kind='reading',
            characters=characters,
            frame_characters=40000,
            material_characters=max(0, characters - 40000),
            carried_characters=0,
            targets=0,
        ),
        usage=dict(sends=sends, thinking_tokens=thinking, output_tokens=output),
        answer=dict(characters=10000, tokens=None, bound=None),
        output_limit=128000,
        completed_generation=completed,
        cli_version='2.1.synthetic',
        observed_at='2026-11-13T18:00:00Z',
    )


def build_row(level=120000, kind='reading', count=4, over=0, thinking=50000):
    return dict(
        kind=kind,
        task_level=level,
        attempts=count,
        completed_attempts=count - over,
        other_attempts=0,
        measurement_attempts=0,
        at_or_over_limit=over,
        thinking_attempts=count - over,
        largest_thinking_tokens=thinking,
        largest_output_tokens=65000,
        largest_answer_tokens=None,
        cli_versions=[dict(
            version='2.1.synthetic',
            attempts=count,
            first_date='2026-11-12',
            last_date='2026-11-13',
        )],
    )


class AttemptMeasurementTests(unittest.TestCase):

    def test_last_send_thinking_and_all_sends_are_retained(self):
        capacity = load_capacity()
        route = build_route()
        packet = {'evidence': []}
        value = capacity.describe_attempt(
            route,
            'bar',
            packet,
            build_request(route, packet),
            build_response(retries=1),
            observed_at='2026-11-13T18:00:00Z',
        )
        self.assertEqual(value['usage']['thinking_tokens'], 700)
        self.assertEqual(value['usage']['sends'], 2)
        self.assertEqual(value['usage']['thinking_method'], 'cli_estimate')
        self.assertEqual(value['cli_version'], '2.1.synthetic')
        self.assertTrue(value['completed_generation'])
        self.assertEqual(value['observed_at'], '2026-11-13T18:00:00Z')

    def test_injected_over_cap_attempt_enters_its_row_without_contaminating_completed_maxima(self):
        capacity = load_capacity()
        route = build_route()
        packet = {'reading_turn': dict(reading_plan=dict(delivered_now=[]), reading_record={}), 'evidence': []}
        response = build_response(users=2, output=144061, thinking=(130000,), completed=False)
        measured = capacity.describe_attempt(
            route,
            'native_math',
            packet,
            build_request(route, packet),
            response,
        )
        self.assertEqual(measured['usage']['sends'], 2)
        rows = capacity.build_profile_rows([dict(measured, output_limit=128000), build_sample(1000)])
        self.assertEqual(sum((row['attempts'] for row in rows)), 2)
        self.assertEqual(sum((row['at_or_over_limit'] for row in rows)), 1)
        self.assertEqual(max((row['largest_output_tokens'] or 0 for row in rows)), 65000)
        self.assertEqual(max((row['largest_thinking_tokens'] or 0 for row in rows)), 50000)

    def test_truncated_or_malformed_thinking_is_unknown(self):
        capacity = load_capacity()
        route = build_route()
        packet = {}
        request = build_request(route, packet)
        response = build_response()
        response['execution']['system_events']['truncated'] = True
        measured = capacity.describe_attempt(route, 'standalone', packet, request, response)
        self.assertIsNone(measured['usage']['thinking_tokens'])
        response['output'][0]['content'] = None
        measured = capacity.describe_attempt(route, 'standalone', packet, request, response)
        self.assertIsNone(measured['answer']['characters'])


class TaskMeasurementTests(unittest.TestCase):

    def describe(self, packet, operation='review.invoke', instructions='Return JSON.'):
        capacity = load_capacity()
        route = build_route()
        request = build_request(route, packet, instructions)
        return (
            capacity.describe_task(
                'native_math',
                packet,
                route=route,
                request=request,
                operation=operation,
                count_carried_state=False,
            ),
            request,
        )

    def test_frame_and_material_are_in_the_task_and_carried_reading_state_is_separate(self):
        body = 'synthetic body ' * 100
        packet = dict(
            reading_turn=dict(
                reading_plan=dict(delivered_now=[dict(characters=len(body))],
                                  delivered_before=[dict(unit_id='earlier', digest='a' * 64)]),
                reading_record=dict(turns=[dict(notes='carried note ' * 100)], excerpts=[], passages=[]),
            ),
            evidence=[dict(artifact=dict(sha256='body'), encoding='utf-8', content=body)],
        )
        task, request = self.describe(packet)
        self.assertEqual(task['kind'], 'reading')
        self.assertEqual(
            task['characters'] + task['carried_characters'],
            load_capacity().measure_request(build_route(), request)['text_characters'],
        )
        self.assertEqual(
            task['characters'],
            task['frame_characters'] + task['material_characters'],
        )
        self.assertGreater(task['carried_characters'], 1000)
        self.assertGreater(task['material_characters'], len(body) - 1)
        without_answers = copy.deepcopy(packet)
        without_answers['reading_turn']['reading_record']['turns'] = []
        fresh, _ = self.describe(without_answers)
        self.assertEqual(task['characters'], fresh['characters'])
        self.assertEqual(task['frame_characters'], fresh['frame_characters'])
        larger, _ = self.describe(packet, instructions='I' * 200000)
        rows = load_capacity().build_profile_rows(
            [dict(build_sample(), task=task), dict(build_sample(), task=larger)],
        )
        self.assertEqual(len(rows), 2)

    def test_a_body_named_twice_counts_once_in_material(self):
        evidence = dict(artifact=dict(sha256='same'), encoding='utf-8', content='synthetic' * 200)
        packet = dict(
            reading_turn=dict(reading_plan=dict(delivered_now=[]), reading_record={}),
            evidence=[evidence],
        )
        first, _ = self.describe(packet)
        packet['evidence'].append(copy.deepcopy(evidence))
        second, _ = self.describe(packet)
        self.assertEqual(first['material_characters'], second['material_characters'])
        self.assertGreater(second['frame_characters'], first['frame_characters'])

    def test_every_call_operation_and_packet_form_has_its_own_kind(self):
        cases = [({}, 'review.invoke', 'single'), ({'reading_record': {}}, 'review.invoke', 'final'), ({'reviewer_excerpts': {}}, 'review.invoke', 'request'), (
            {'protocol': 'exactory-staged-review-v3', 'staged_review': {'task': {}}},
            'review.invoke',
            'staged',
        ), ({}, 'review.route_probe', 'probe'), (
            {'protocol': 'exactory-reassessment-disposition-correction-v1'},
            'review.output_correction',
            'correction',
        )]
        for packet, operation, expected in cases:
            with self.subTest(expected=expected):
                task, request = self.describe(packet, operation)
                self.assertEqual(task['kind'], expected)
                if expected not in ('reading', 'read'):
                    self.assertEqual(
                        task['characters'],
                        load_capacity().measure_request(build_route(), request)['text_characters'],
                    )
        from research_harness.review_budget import CALL_OPERATIONS
        from ci.route_capacity_profile import COMPLETION_OPERATIONS
        self.assertEqual(
            {operation for _, operation in COMPLETION_OPERATIONS},
            set(CALL_OPERATIONS),
        )
        self.assertEqual({operation for _, operation, _ in cases}, set(CALL_OPERATIONS))

    def test_final_state_is_material_and_not_a_carried_exemption(self):
        packet = dict(
            reading_record=dict(turns=[dict(notes='note')], excerpts=[dict(text='x' * 90000)], passages=[]),
            native=dict(scientific_context=dict(openness_minimum=dict(neighbours=['a', 'b']))),
            evidence=[],
        )
        task, _ = self.describe(packet)
        self.assertEqual(task['kind'], 'final')
        self.assertEqual(task['targets'], 3)
        self.assertEqual(task['carried_characters'], 0)
        self.assertGreater(task['material_characters'], 90000)


class OutputProfileTests(unittest.TestCase):

    def test_bins_use_whole_tasks_and_keep_retried_completions_and_over_cap_attempts(self):
        capacity = load_capacity()
        rows = capacity.build_profile_rows(
            [build_sample(100000, sends=2), build_sample(100000, output=140000, thinking=140000, completed=False, sends=2), build_sample(200000), dict(
                build_sample(1000, completed=False),
                usage=dict(sends=None, thinking_tokens=None, output_tokens=None),
            )],
        )
        self.assertEqual([row['task_level'] for row in rows], [15000, 120000, 240000])
        self.assertEqual(rows[0]['other_attempts'], 1)
        rows = rows[1:]
        self.assertEqual(rows[0]['attempts'], 2)
        self.assertEqual(rows[0]['at_or_over_limit'], 1)
        self.assertEqual(rows[0]['largest_thinking_tokens'], 50000)
        self.assertEqual(rows[0]['cli_versions'][0]['attempts'], 2)

    def test_cli_versions_keep_counts_and_date_ranges(self):
        capacity = load_capacity()
        a = build_sample()
        b = copy.deepcopy(a)
        b.update(cli_version='2.1.next', observed_at='2026-11-14T00:00:00Z')
        c = copy.deepcopy(a)
        c['observed_at'] = '2026-11-12T00:00:00Z'
        rows = capacity.build_profile_rows([a, b, c])
        versions = {row['version']: row for row in rows[0]['cli_versions']}
        self.assertEqual(versions['2.1.synthetic']['attempts'], 2)
        self.assertEqual(versions['2.1.synthetic']['first_date'], '2026-11-12')
        self.assertEqual(versions['2.1.synthetic']['last_date'], '2026-11-13')

    def test_rule_selects_by_frame_plus_level_and_reports_every_level_sample(self):
        capacity = load_capacity()
        route = build_route()
        profile = dict(
            rows=[build_row(120000, over=1), build_row(240000)],
            frame_policy=dict(borrowing_ready=True),
        )
        value = capacity.select_task_limits(route, profile, 'reading', frame_characters=40000)
        self.assertEqual(value['state'], 'fits')
        self.assertEqual(value['material_allowance'], 120000)
        self.assertEqual(value['task_characters'], 160000)
        self.assertEqual(value['sample_size'], 4)
        self.assertEqual(value['largest_output_tokens'], 65000)
        self.assertEqual(len(value['levels']), 4)
        risky = capacity.select_task_limits(
            route,
            dict(rows=[build_row(over=1)]),
            'reading',
            frame_characters=0,
        )
        self.assertEqual(risky['state'], 'over_limit_expected')

    def test_sparse_rows_and_missing_frame_measurement_do_not_invent_a_fitting_final(self):
        capacity = load_capacity()
        route = build_route()
        profile = dict(rows=[build_row()], frame_policy=dict(borrowing_ready=False))
        self.assertEqual(
            capacity.select_task_limits(route, profile, 'final', frame_characters=0)['state'],
            'unmeasured',
        )
        profile['frame_policy'].update(
            borrowing_ready=True,
            largest_text_characters=395305,
            largest_material_characters=36127,
        )
        self.assertIsNone(
            capacity.select_task_limits(route, profile, 'final', frame_characters=0)['borrowed_kind'],
        )
        self.assertEqual(
            capacity.select_task_limits(route, profile, 'final', frame_characters=0)['state'],
            'unmeasured',
        )
        self.assertEqual(
            capacity.select_task_limits(
                route,
                dict(rows=[build_row(count=3)]),
                'reading',
                frame_characters=0,
            )['state'],
            'unmeasured',
        )

    def test_recent_500_keeps_input_high_water_and_never_changes_effort(self):
        capacity = load_capacity()
        route = build_route()
        attempts = {str(index): dict(
            build_sample(),
            id=str(index),
            origin='instrumented',
            assignment_id='a',
            recorded_revision=index,
            verified_input_tokens=400000 if index == 0 else None,
        ) for index in range(501)}
        records = dict(
            review_route={'route': route},
            review_assignment={'a': dict(route_id='route')},
            review_attempt=attempts,
        )
        value = capacity.read_output_profile(records, route, priors=())
        self.assertEqual(value['store_attempts'], 500)
        self.assertEqual(value['verified_input_tokens'], 400000)
        start = dict(material_allowance=120000, state='fits')
        self.assertEqual(
            capacity.derive_material_allowance(
                'reading',
                start,
                [dict(material_characters=119371, output_tokens=96000)],
                128000,
            ),
            60000,
        )
        self.assertEqual(route['configuration']['effort'], 'max')
        smaller_route = copy.deepcopy(route)
        smaller_route['id'] = 'smaller'
        smaller_route['configuration']['max_output_tokens'] = 64000
        shared_records = dict(
            review_route={'route': route, 'smaller': smaller_route},
            review_assignment={'a': dict(route_id='smaller'), 'b': dict(route_id='route')},
            review_attempt={identifier: dict(
                build_sample(), id=identifier, assignment_id=identifier,
                origin='instrumented', recorded_revision=index,
            ) for index, identifier in enumerate(('a', 'b'))},
        )
        for reported_route in (route, smaller_route):
            with self.subTest(reported_route=reported_route['id']):
                profile = capacity.read_output_profile(shared_records, reported_route, priors=())
                self.assertEqual(profile['rows'][0]['attempts'], 2)
                self.assertEqual(profile['rows'][0]['at_or_over_limit'], 1)


class ProbeReaderTests(unittest.TestCase):

    def test_corrected_retried_probe_is_counted_by_task_and_last_send_thinking(self):
        from ci import route_capacity_profile as recompute
        rows = [dict(
            arm='W',
            status='completed',
            user_events=1,
            assistant_events=2,
            api_retries=1,
            effort='max',
            delivered_characters=119371,
            request_characters=173774,
            thinking_truncated=False,
            thinking_tokens=84400,
            output_tokens=95955,
            answer_characters=27376,
            cli_version='2.1.325',
            call=1,
        )]
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'summary.jsonl'
            path.write_text(json.dumps(rows[0]) + '\n')
            samples = recompute.read_probe_samples(path)
        self.assertEqual(samples[0]['task']['material_characters'], 136190)
        self.assertEqual(samples[0]['task']['frame_characters'], 37584)
        profile = recompute.build_prior_rows(samples, source='probe', date='2026-11-13')
        self.assertEqual(profile[0]['attempts'], 1)
        self.assertEqual(profile[0]['task_level'], 240000)
        self.assertEqual(profile[0]['largest_thinking_tokens'], 84400)
        self.assertEqual(profile[0]['measurement_attempts'], 1)
        fixture = json.loads((Path(__file__).with_name('fixtures') / 'route_capacity_measurements.json').read_text())
        measured = fixture['probe_task_partitions']['rows']
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'summary.jsonl'
            path.write_text(''.join(json.dumps(dict(rows[0], arm=row['arm'],
                                                     request_characters=row['characters'])) + '\n'
                                    for row in measured))
            measured_samples = recompute.read_probe_samples(path)
        for sample, expected in zip(measured_samples, measured):
            with self.subTest(arm=expected['arm']):
                self.assertEqual({key: sample['task'][key] for key in
                                  ('characters', 'material_characters', 'frame_characters')},
                                 {key: value for key, value in expected.items() if key != 'arm'})

    def test_partial_frame_rows_keep_attempt_tasks_separate_and_plans_conservative(self):
        from ci import route_capacity_profile as recompute
        base = [dict(
            effort='max',
            size='quarter',
            thinking_tokens=58850,
            status='completed',
            assistant_events=2,
            user_events=1,
        )]
        frame = [dict(
            effort='max',
            part='record',
            arm='R-Q1',
            text_characters=584965,
            base_text_characters=71984,
            delivered_characters=29260,
            status='completed',
            assistant_events=2,
            user_events=1,
            thinking_tokens=60000,
            output_tokens=70000,
            answer_characters=12000,
            cli_version='2.1.325',
        )]
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'summary.jsonl'
            path.write_text(json.dumps(frame[0]) + '\n')
            samples, policy = recompute.read_frame_probe_samples(path, base)
        self.assertTrue(policy['count_carried_state'])
        self.assertEqual(policy['evidence'][0]['reading'], 'inconclusive')
        self.assertEqual(samples[0]['task']['characters'], 71984)
        self.assertEqual(samples[0]['task']['carried_characters'], 512981)
        self.assertEqual(samples[0]['task']['material_characters'], 32846)
        self.assertEqual(samples[0]['task']['frame_characters'], 39138)
        lists = dict(frame[0], part='lists', arm='L-Q1', text_characters=392548)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'summary.jsonl'
            path.write_text(json.dumps(lists) + '\n')
            list_samples, _ = recompute.read_frame_probe_samples(path, base)
        self.assertEqual(list_samples[0]['task']['material_characters'], 32846)
        self.assertEqual(list_samples[0]['task']['frame_characters'], 359702)
        self.assertEqual(list_samples[0]['task']['carried_characters'], 0)
        frame[0]['thinking_tokens'] = 90000
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'summary.jsonl'
            path.write_text(json.dumps(frame[0]) + '\n')
            samples, policy = recompute.read_frame_probe_samples(path, base)
        self.assertTrue(policy['count_carried_state'])
        self.assertEqual(samples[0]['task']['characters'], 71984)


class ProfileIntegrationTests(unittest.TestCase):

    def build_case(self):
        from test_research_review_protocol import ReviewProtocolTests
        case = ReviewProtocolTests()
        case.setUp()
        self.addCleanup(case.doCleanups)
        case.route()
        case.run_probe()
        return case

    def test_new_attempt_records_profile_fields_and_old_attempt_stays_verifiable(self):
        from research_harness import review_protocol as protocol
        case = self.build_case()
        assignment = case.assign()['result']
        result = case.invoke({'finding': 'synthetic'})['result']
        self.assertNotIn('task', assignment)
        task = load_capacity().describe_assignment_task(case.records(), case.artifacts, assignment)
        self.assertIn('characters', task)
        self.assertEqual(task['kind'], 'single')
        self.assertEqual(result['task']['kind'], 'single')
        self.assertIn('cli_version', result)
        legacy = copy.deepcopy(result)
        for key in ('task', 'answer', 'verified_input_tokens', 'cli_version', 'completed_generation', 'capacity_evidence'):
            legacy.pop(key, None)
        for key in ('thinking_tokens', 'thinking_method', 'sends', 'request_characters'):
            legacy['usage'].pop(key, None)
        self.assertTrue(
            protocol.authenticate_attempt(case.records(), case.artifacts, assignment, legacy),
        )
        before = copy.deepcopy(case.records())
        load_capacity().describe_route_report(
            case.records(),
            case.records()['review_route']['route'],
        )
        self.assertEqual(case.records(), before)
        legacy_assignment = copy.deepcopy(assignment)
        self.assertTrue(
            protocol.authenticate_attempt(before, case.artifacts, legacy_assignment, legacy),
        )
        self.assertEqual(
            protocol.build_assignment_request(before, case.artifacts, assignment),
            json.loads(case.artifacts.read(result['request'])),
        )

    def test_real_shape_max_bar_warns_and_assessment_names_large_attempts(self):
        from research_harness import cli, review_protocol as protocol
        route = build_route()
        assignments = {'bar': dict(id='bar', role='bar', route_id='route', context={}, recorded_revision=3)}
        attempts = {str(index): dict(
            id=str(index),
            assignment_id='bar',
            status='failed',
            origin='instrumented',
            usage=dict(output_tokens=value),
            recorded_revision=index,
        ) for index, value in enumerate((138371, 179808))}
        records = dict(
            review_route={'route': route},
            review_assignment=assignments,
            review_attempt=attempts,
        )
        report = protocol.describe_route_report(records, 'route')
        self.assertEqual(
            [row['output_tokens'] for row in report['near_limit_attempts']],
            [138371, 179808],
        )
        self.assertIn('capacity_warning', cli._review_status(records, None)['routes']['route'])
        self.assertIn('final', report['task_limits'])

    def test_malformed_provider_response_still_records_a_failed_attempt(self):
        case = self.build_case()
        case.assign()
        bad = dict(
            id='provider-response',
            model='fixture-model',
            status='completed',
            output=[dict(type='message', content=None)],
            usage=dict(output_tokens=12),
        )
        with patch.object(case, 'response', return_value=bad):
            result = case.invoke({})['result']
        self.assertEqual(result['status'], 'failed')
        self.assertIn('attempt', case.records()['review_attempt'])
        self.assertIsNone(result['answer']['characters'])
        from research_harness import review_codex_invocation as capture, review_protocol as protocol
        records = case.records()
        route = records['review_route']['route']
        request = protocol.build_assignment_request(
            records,
            case.artifacts,
            records['review_assignment']['assignment'],
        )
        partial = dict(
            request_digest=digest(request),
            route_digest=digest(route),
            runtime=route['runtime'],
            response=None,
            output=None,
            status='failed',
            error=dict(kind='timeout'),
            usage=dict(input_tokens=100, output_tokens=140000, cost_usd=None, wall_seconds=1),
        )
        with patch.object(capture, 'observe', return_value=partial):
            observed = protocol._observed(case.artifacts, route, request, None)
        self.assertEqual(observed['usage']['output_tokens'], 140000)
        self.assertEqual(observed['task']['kind'], 'single')
        self.assertFalse(observed['completed_generation'])


class FramedTaskTests(unittest.TestCase):

    def test_verbatim_frame_is_measured_without_disabling_its_encoder(self):
        from research_harness.artifacts import describe_artifact
        capacity = load_capacity()
        route = build_route('codex_cli_framed_v1', 'gpt-6-astra')
        body = 'synthetic evidence ' * 100
        packet = dict(
            role='native_math',
            native=dict(
                protocol='native-math-review-v1',
                kind='result',
                scientific_context=dict(
                    review_text_encoding='openness-verbatim-evidence-v1',
                    source_evidence=[dict(
                        source_record=describe_artifact(body.encode(), 'text/plain'),
                        delivered_artifact=describe_artifact(body.encode(), 'text/plain'),
                    )],
                ),
            ),
            evidence=[dict(
                artifact=describe_artifact(body.encode(), 'text/plain'),
                encoding='utf-8',
                content=body,
            )],
        )
        request = build_request(route, packet)
        before = copy.deepcopy(request)
        task = capacity.describe_task('native_math', packet, route=route, request=request)
        self.assertGreaterEqual(task['material_characters'], len(body))
        self.assertEqual(
            task['characters'],
            task['frame_characters'] + task['material_characters'],
        )
        self.assertEqual(request, before)


class CliVersionStatusTests(unittest.TestCase):

    def test_new_cli_version_is_marked_against_the_pinned_profile(self):
        from research_harness.artifacts import ArtifactStore
        capacity = load_capacity()
        route = build_route()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            artifacts = ArtifactStore(root)
            plan = artifacts.put(
                json.dumps(dict(profile=dict(rows=[build_row()]))).encode(),
                'application/json',
            )
            assignment = dict(
                id='open',
                role='bar',
                route_id='route',
                context={},
                recorded_revision=1,
                capacity_plan=dict(artifact=plan),
            )
            records = dict(review_route={'route': route}, review_assignment={'open': assignment})
            with patch.object(capacity, 'read_installed_cli_version', return_value='2.1.next'):
                report = capacity.describe_open_route_reports(records, artifacts)
        self.assertEqual(report['route']['cli_profile_status']['state'], 'unmeasured_version')


class RevisedFramePolicyTests(unittest.TestCase):

    def test_attempt_task_excludes_carried_state_even_when_the_plan_counts_it(self):
        capacity = load_capacity()
        route = build_route()
        packet = dict(
            reading_turn=dict(
                reading_record=dict(turns=[dict(notes='notes ' * 500)]),
                reading_plan=dict(delivered_now=[]),
            ),
            evidence=[],
        )
        request = build_request(route, packet)
        task = capacity.describe_attempt(
            route,
            'native_math',
            packet,
            request,
            build_response(),
            count_carried_state=True,
        )['task']
        self.assertGreater(task['carried_characters'], 2000)
        self.assertEqual(
            task['characters'] + task['carried_characters'],
            capacity.measure_request(route, request)['text_characters'],
        )

    def test_frame_attempt_rows_never_enter_level_rows(self):
        capacity = load_capacity()
        route = build_route()
        prior = dict(
            build_row(),
            source='probe-frame',
            date='2026-11-13',
            thinking_method='cli_estimate',
        )
        value = capacity.read_output_profile({}, route, priors=[prior])
        self.assertEqual(value['rows'], [])
        self.assertEqual(value['frame_measurements'], [prior])

    def test_reading_borrowing_requires_matching_class_frame_and_material_scope(self):
        capacity = load_capacity()
        route = build_route()
        policy = dict(
            borrowing_ready=True,
            largest_text_characters=395305,
            largest_material_characters=36127,
        )
        profile = dict(rows=[build_row()], frame_policy=policy)
        ordinary = capacity.select_task_limits(route, profile, 'read')
        self.assertEqual(ordinary['state'], 'unmeasured')
        scoped = capacity.select_task_limits(route, profile, 'read', frame_matches_reading=True)
        self.assertEqual(scoped['material_allowance'], 18750)
        self.assertEqual(scoped['borrowed_kind'], 'reading')
        final = capacity.select_task_limits(route, profile, 'final', frame_matches_reading=True)
        self.assertEqual(final['state'], 'unmeasured')

    def test_no_effect_requires_both_quarters_and_all_planned_calls(self):
        from ci import route_capacity_profile as recompute
        base = [dict(
            effort='max',
            size='quarter',
            thinking_tokens=58850,
            status='completed',
            user_events=1,
        )]
        good = dict(
            effort='max',
            part='lists',
            arm='L-Q1',
            text_characters=392548,
            base_text_characters=71984,
            delivered_characters=29914,
            status='completed',
            assistant_events=2,
            user_events=1,
            thinking_tokens=50000,
            output_tokens=60000,
            answer_characters=10000,
            cli_version='2.1.synthetic',
        )
        failed = dict(good, status='failed', thinking_tokens=0, output_tokens=0)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'summary.jsonl'
            path.write_text(
                ''.join((json.dumps(row) + '\n' for row in [good, failed, failed, failed])),
            )
            _, policy = recompute.read_frame_probe_samples(path, base)
            lists = next(
                (row for row in policy['evidence'] if row['effort'] == 'max' and row['part'] == 'lists'),
            )
            self.assertEqual(lists['reading'], 'inconclusive')
            self.assertEqual(lists['calls'], 4)
            self.assertEqual(lists['valid_observations'], 1)
            path.write_text(
                ''.join(
                    (json.dumps(dict(good, arm='L-' + quarter)) + '\n' for quarter in ('Q1', 'Q3', 'Q1', 'Q3')),
                ),
            )
            _, policy = recompute.read_frame_probe_samples(path, base)
            lists = next(
                (row for row in policy['evidence'] if row['effort'] == 'max' and row['part'] == 'lists'),
            )
            self.assertEqual(lists['reading'], 'no effect')
            self.assertEqual(lists['largest_text_characters'], 392548)
            self.assertEqual(len(policy['summary_sha256']), 64)


class TaskLimitRuleTests(unittest.TestCase):

    def test_reserve_bound_and_uncalibrated_answer_room(self):
        capacity = load_capacity()
        route = build_route()
        profile = dict(rows=[build_row(thinking=100000)])
        value = capacity.select_task_limits(route, profile, 'reading', frame_characters=0)
        self.assertEqual(value['thinking_reserve_tokens'], 115000)
        self.assertEqual(value['answer_bound'], 23400)
        self.assertEqual(value['state'], 'fits')
        value = capacity.select_task_limits(
            route,
            profile,
            'reading',
            frame_characters=0,
            calibrated=False,
        )
        self.assertEqual(value['answer_room_characters'], 13000)
        self.assertEqual(value['state'], 'over_limit_expected')
        self.assertEqual(value['answer_bound'], 18000)

    def test_risky_start_minimizes_expected_calls_and_excludes_all_over_rows(self):
        capacity = load_capacity()
        route = build_route()
        profile = dict(
            rows=[build_row(120000, count=4, over=3), build_row(60000, count=4, over=1), build_row(30000, count=4, over=4)],
        )
        value = capacity.select_task_limits(route, profile, 'reading', frame_characters=0)
        self.assertEqual(value['state'], 'over_limit_expected')
        self.assertEqual(value['step5_material_allowance'], 60000)
        self.assertEqual(value['material_allowance'], 15000)
        self.assertEqual(value['at_or_over_limit_share'], 1)
        self.assertEqual(value['start_rule'], 'below_risky_ranges')

    def test_final_targets_halve_until_the_minimum_bound_fits(self):
        capacity = load_capacity()
        route = build_route()
        profile = dict(rows=[build_row(480000, kind='final', thinking=107000)])
        value = capacity.select_task_limits(route, profile, 'final', targets=8, frame_characters=0)
        self.assertEqual(value['state'], 'fits')
        self.assertEqual(value['target_allowance'], 1)
        projection = capacity.describe_final_projection(value, dict(material_allowance=120000))
        self.assertEqual(projection['sample_size'], 4)
        self.assertEqual(projection['entry_pool'], 467600)

    def test_unmeasured_floor_can_grow_and_recorded_replay_is_deterministic(self):
        capacity = load_capacity()
        start = dict(state='unmeasured', material_allowance=15000)
        samples = [dict(material_characters=15000, output_tokens=44800), dict(material_characters=30000, output_tokens=60000), dict(material_characters=30000, output_tokens=96000)]
        self.assertEqual(
            capacity.derive_material_allowance(
                'reading',
                start,
                [dict(material_allowance=15000, output_tokens=44800)],
                128000,
            ),
            30000,
        )
        self.assertEqual(
            capacity.derive_material_allowance('reading', start, samples[:1], 128000),
            30000,
        )
        self.assertEqual(
            capacity.derive_material_allowance('reading', start, samples, 128000),
            15000,
        )
        measured = dict(state='fits', material_allowance=30000)
        self.assertEqual(
            capacity.derive_material_allowance(
                'reading',
                measured,
                [dict(material_characters=30000, output_tokens=0)],
                128000,
            ),
            30000,
        )

    def test_missing_thinking_cannot_qualify_and_route_classes_do_not_borrow(self):
        capacity = load_capacity()
        route = build_route()
        self.assertEqual(
            capacity.select_task_limits(route, dict(rows=[build_row(thinking=None)]), 'reading')['state'],
            'over_limit_expected',
        )
        with patch.object(
            capacity,
            'FRAME_PROBE_EVIDENCE',
            (('max', 'lists', 4, 4, ('Q1', 'Q3'), 0, 'no effect', 395305, 36127, None),),
        ):
            self.assertTrue(capacity.describe_frame_policy(route)['borrowing_ready'])
            self.assertFalse(
                capacity.describe_frame_policy(build_route(model='claude-sonnet-5-5'))['borrowing_ready'],
            )


class StoredPriorTests(unittest.TestCase):

    def test_stored_priors_keep_all_max_reading_attempts_and_corrected_probe_rows(self):
        capacity = load_capacity()
        rows = capacity.read_profile_priors(build_route())
        live = [row for row in rows if row['source'] == 'live-records' and row['kind'] == 'reading']
        self.assertEqual(sum((row['attempts'] for row in live)), 37)
        self.assertEqual(sum((row['at_or_over_limit'] for row in live)), 3)
        self.assertEqual(sum((row['other_attempts'] for row in live)), 4)
        probe = [row for row in rows if row['source'] == 'probe']
        self.assertEqual(
            [(row['task_level'], row['attempts'], row['largest_thinking_tokens'], row['largest_output_tokens']) for row in probe],
            [(120000, 8, 70550, 78441), (240000, 4, 85250, 97134)],
        )
        self.assertTrue(all((row['thinking_method'] == 'cli_estimate' for row in probe)))
        self.assertTrue(
            all(
                (row['date'] == '2026-11-13' and row['source_record'] == capacity.MEASUREMENT_SOURCE for row in rows),
            ),
        )
        profile = capacity.read_output_profile({}, build_route())
        self.assertTrue(profile['frame_measurements'])
        self.assertEqual(
            sum((row['attempts'] for row in profile['rows'] if row['kind'] == 'reading')),
            49,
        )
        limits = capacity.select_task_limits(build_route(), profile, 'reading')
        self.assertEqual(limits['state'], 'over_limit_expected')
        self.assertTrue(
            all(
                (row['sample_size'] and row['largest_output_tokens'] is not None for row in limits['levels']),
            ),
        )

    def test_xhigh_whole_thinking_stays_an_estimate_and_final_has_its_own_start(self):
        capacity = load_capacity()
        route = build_route(effort='xhigh')
        rows = capacity.read_profile_priors(route)
        whole = next((row for row in rows if row['source'] == 'probe' and row['task_level'] == 240000))
        self.assertEqual(whole['attempts'], 2)
        self.assertEqual(whole['largest_thinking_tokens'], 12550)
        self.assertEqual(whole['thinking_method'], 'cli_estimate')
        report = capacity.describe_route_report({}, route)
        self.assertEqual(report['task_limits']['reading']['state'], 'fits')
        final = report['task_limits']['final']
        self.assertIsNone(final['borrowed_kind'])
        self.assertIn('sample_size', final)
        self.assertEqual(
            report['final_reference_projection']['sample_size'],
            final['sample_size'],
        )


class FrozenAttemptReaderTests(unittest.TestCase):

    def write_case(self, root, route, request):
        import sqlite3
        from research_harness.artifacts import ArtifactStore
        artifacts = ArtifactStore(root)
        reference = artifacts.put(json.dumps(request).encode(), 'application/json')
        attempt = dict(
            id='failed',
            assignment_id='a',
            origin='instrumented',
            request=reference,
            response=None,
            usage=dict(output_tokens=16),
            runtime=dict(version='synthetic-cli 1'),
            observed_at='2026-11-13T01:00:00Z',
        )
        (root / '.exactory').mkdir(exist_ok=True)
        connection = sqlite3.connect(str(root / '.exactory/research.sqlite3'))
        connection.execute('create table records (kind text,key text,value text)')
        for kind, key, value in (
            ('review_route', 'route', route),
            ('review_assignment', 'a', dict(route_id='route', role='bar')),
            ('review_attempt', 'failed', attempt),
        ):
            connection.execute('insert into records values (?,?,?)', (kind, key, json.dumps(value)))
        connection.commit()
        connection.close()

    def test_read_only_recomputation_keeps_a_null_response_failure(self):
        from ci import route_capacity_profile as recompute
        route = build_route()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.write_case(root, route, build_request(route, {'evidence': []}))
            before = (root / '.exactory/research.sqlite3').read_bytes()
            samples = recompute.read_store_samples(root)
            self.assertEqual(len(samples), 1)
            self.assertEqual(samples[0]['usage']['output_tokens'], 16)
            self.assertEqual(samples[0]['cli_version'], 'synthetic-cli 1')
            self.assertFalse(samples[0]['completed_generation'])
            self.assertEqual((root / '.exactory/research.sqlite3').read_bytes(), before)
            self.assertEqual(load_capacity().build_profile_rows(samples)[0]['other_attempts'], 1)

    def test_unsupported_historical_request_is_retained_as_an_unmeasured_task(self):
        from ci import route_capacity_profile as recompute
        route = build_route('codex_cli_v1', 'gpt-6-astra')
        request = dict(
            instructions='Return JSON.',
            input=[dict(
                role='user',
                content=[dict(type='input_text', text='Review packet JSON:\n{}'), dict(type='input_image', image_url='data:image/png;base64,AA==')],
            )],
        )
        with tempfile.TemporaryDirectory() as directory:
            self.write_case(Path(directory), route, request)
            samples = recompute.read_store_samples(Path(directory))
        self.assertEqual(len(samples), 1)
        self.assertIsNone(samples[0]['task']['characters'])
        self.assertIsNone(samples[0]['usage']['request_characters'])
        self.assertEqual(samples[0]['task']['measurement_error'], 'review_format_unsupported')
        rows = load_capacity().build_profile_rows(samples)
        self.assertEqual(rows[0]['task_level'], 'unmeasured')
        self.assertEqual(rows[0]['other_attempts'], 1)
        self.assertEqual(
            load_capacity().select_task_limits(route, dict(rows=rows), 'single')['state'],
            'unmeasured',
        )


class ReviewFindingTests(unittest.TestCase):

    def test_incomplete_verified_codex_send_cannot_raise_observed_input_capacity(self):
        capacity = load_capacity()
        route = build_route('codex_cli_v1', 'gpt-6-astra')
        packet = {'evidence': []}
        response = build_response(output=130000, completed=False)
        response['context_evidence']['input_verified'] = True
        response['usage']['input_tokens'] = 900000
        measured = capacity.describe_attempt(route, 'bar', packet, build_request(route, packet), response)
        self.assertIsNone(measured['verified_input_tokens'])
        attempt = dict(
            measured,
            id='failed',
            origin='instrumented',
            assignment_id='a',
            verified_input_tokens=900000,
        )
        records = dict(
            review_route={'route': route},
            review_assignment={'a': dict(route_id='route')},
            review_attempt={'failed': attempt},
        )
        self.assertEqual(
            capacity.read_output_profile(records, route, priors=())['verified_input_tokens'],
            0,
        )

    def test_status_uses_the_active_assignments_frame_and_target_count(self):
        capacity = load_capacity()
        route = build_route()
        profile = dict(rows=[build_row(480000, kind='final')], frame_policy={})
        assignment = dict(
            id='a',
            role='native_math',
            route_id='route',
            context={},
            recorded_revision=1,
            task=dict(kind='final', targets=20, frame_characters=1000, carried_characters=0),
        )
        records = dict(review_route={'route': route}, review_assignment={'a': assignment})
        with patch.object(capacity, 'read_output_profile', return_value=profile):
            report = capacity.describe_open_route_reports(records)['route']
        final = report['task_limits']['final']
        self.assertEqual(final['target_allowance'], 20)
        self.assertEqual(final['frame_characters'], 1000)
        self.assertEqual(final['answer_bound'], 50000)


class RecomputeCliTests(unittest.TestCase):

    def test_empty_read_only_input_reports_inconclusive_frame_without_invented_rows(self):
        import contextlib
        import io
        from ci import route_capacity_profile as recompute
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'roots.md'
            path.write_text('# No records\n')
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                self.assertEqual(recompute.run_cli(['--roots-file', str(path)]), 0)
        result = json.loads(output.getvalue())
        self.assertEqual(result['rows'], [])
        self.assertIsNone(result['frame_policy']['summary_sha256'])
        self.assertTrue(result['frame_policy']['count_carried_state'])
        self.assertEqual(len(result['frame_policy']['evidence']), 4)
        self.assertTrue(
            all((row['reading'] == 'inconclusive' for row in result['frame_policy']['evidence'])),
        )


class MissingFrameBaselineTests(unittest.TestCase):

    def test_incomplete_evidence_cannot_establish_no_effect_but_the_cap_establishes_an_effect(self):
        from ci import route_capacity_profile as recompute
        baseline = [dict(
            effort='xhigh',
            size='quarter',
            arm='Q1',
            thinking_tokens=14000,
            status='completed',
            user_events=1,
            assistant_events=2,
        )]
        row = dict(
            effort='xhigh',
            part='lists',
            text_characters=392548,
            base_text_characters=71984,
            delivered_characters=29914,
            status='completed',
            user_events=1,
            assistant_events=2,
            thinking_tokens=10000,
            output_tokens=20000,
            answer_characters=1000,
        )
        fractional = [dict(baseline[0], thinking_tokens=14001)]
        cases = [(fractional, dict(thinking_tokens=16102), 'effect'), ([], {}, 'inconclusive'), (baseline, dict(thinking_truncated=True), 'inconclusive'), (baseline, dict(assistant_events=3), 'inconclusive'), ([], dict(status='failed', output_tokens=130000, thinking_tokens=None), 'effect')]
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'summary.jsonl'
            for base, changes, expected in cases:
                with self.subTest(changes=changes, base=bool(base)):
                    path.write_text(
                        ''.join(
                            (json.dumps(dict(row, arm='L-' + quarter, **changes)) + '\n' for quarter in ('Q1', 'Q3')),
                        ),
                    )
                    _, policy = recompute.read_frame_probe_samples(path, base)
                    reading = next(
                        (value for value in policy['evidence'] if value['effort'] == 'xhigh' and value['part'] == 'lists'),
                    )
                    self.assertEqual(reading['reading'], expected)


class StagedMetricTests(unittest.TestCase):
    def setUp(self):
        from research_harness import bar_sources, review_protocol
        released = patch.object(review_protocol, 'NEW_SESSION_RULES', (bar_sources.PROTOCOL_V3,))
        released.start()
        self.addCleanup(released.stop)
        super().setUp()

    def test_staged_request_metrics_preserve_controller_and_child_request_digests(self):
        from test_review_staged_protocol import StagedEntryTests
        from research_harness import review_protocol as protocol
        case = StagedEntryTests()
        self.addCleanup(case.doCleanups)
        case.setUp()
        case.oversized()
        controller = case.assign_staged('controller')
        self.assertNotIn('task', controller)
        task_id = controller['staged_review']['task_ids'][0]
        child = case.case.assign('result','first',identifier='child',
            controller_assignment_id=controller['id'],task_id=task_id)
        records = case.case.read_records()
        request = protocol.build_assignment_request(records,case.case.artifacts,child)
        route = records['review_route'][child['route_id']]
        self.assertNotIn('task', child)
        task = load_capacity().describe_attempt(route, child['role'], {}, request, build_response())['task']
        self.assertEqual(task['kind'],'staged')
        self.assertEqual(task['characters'],load_capacity().measure_request(route,request)['text_characters'])
        self.assertEqual(digest(request),child['planned_request_digest'])
        legacy = dict(child)
        self.assertEqual(protocol.build_assignment_request(records,case.case.artifacts,legacy),request)
