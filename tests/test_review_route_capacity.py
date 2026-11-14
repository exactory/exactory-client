"""Capacity is diagnostic metadata; stored routes and review inputs retain their identities."""
import copy
import importlib
import json
import math
import unittest
from research_harness import review_transport as transport


def build_route(adapter='claude_cli_v1', model='claude-opus-5-5', effort='max', output=128000):
    config = dict(max_output_tokens=output, timeout_seconds=600)
    if adapter == 'claude_cli_v1':
        config['effort'] = effort
    return dict(
        id='route',
        adapter=adapter,
        model=model,
        endpoint='claude://local' if adapter == 'claude_cli_v1' else 'codex://local' if adapter.startswith('codex_') else 'https://example.test/responses',
        configuration=config,
    )


class RouteLimitTests(unittest.TestCase):

    def setUp(self):
        self.capacity = importlib.import_module('research_harness.review_route_capacity')

    def test_every_transport_has_listed_and_unlisted_capacity(self):
        for adapter in (transport.ADAPTER,) + tuple(transport.LOCAL_ADAPTERS):
            with self.subTest(adapter=adapter):
                model = 'claude-opus-5-5' if adapter == 'claude_cli_v1' else 'gpt-6-astra'
                route = build_route(adapter, model)
                before = copy.deepcopy(route)
                value = self.capacity.describe_route_capacity(route)
                self.assertTrue(value['listed'])
                self.assertEqual(route, before)
                self.assertIn(value['input_tokens']['source'], ('documented', 'observed'))
                self.assertEqual(
                    value['binary_inputs'],
                    'unmeasured' if adapter in (transport.ADAPTER, 'codex_cli_visual_v1') else 'unsupported',
                )
                unknown = self.capacity.describe_route_capacity(build_route(adapter, 'unlisted-model'))
                self.assertFalse(unknown['listed'])
                self.assertEqual(unknown['input_tokens']['source'], 'default')
                self.assertEqual(
                    unknown['input_tokens']['value'],
                    72000 if adapter == 'claude_cli_v1' else 150000,
                )
                self.assertEqual(unknown['request_measure'], 'bytes')
                self.assertTrue(unknown['raises_capacity'])

    def test_model_and_transport_limits_keep_their_sources(self):
        value = self.capacity.describe_route_capacity(build_route(output=600000))
        self.assertEqual(value['output_tokens']['value'], 128000)
        self.assertEqual(value['output_tokens']['source'], 'documented')
        self.assertEqual(value['input_tokens']['value'], 839000)
        self.assertEqual(value['input_characters']['value'], 1300000)
        self.assertEqual(value['input_characters']['source'], 'transport')
        self.assertIn('https://', value['output_tokens']['url'])
        for adapter, model, tokens in (
            ('codex_cli_v1', 'gpt-6-astra', 359900),
            ('codex_cli_v1', 'gpt-6.1-sol', 316707),
            ('codex_cli_v1', 'gpt-6-luna', 193797),
            ('codex_cli_framed_v1', 'gpt-6-astra', 307507),
            ('codex_cli_visual_v1', 'gpt-6-astra', 364458),
            ('codex_cli_visual_v1', 'gpt-6.1-sol', 358029),
        ):
            with self.subTest(adapter=adapter, model=model):
                result = self.capacity.describe_route_capacity(build_route(adapter, model))
                self.assertEqual(result['input_tokens']['value'], tokens)
                self.assertEqual(result['input_tokens']['source'], 'observed')
                self.assertEqual(result['effort'], 'high')
                self.assertFalse(result['provider_enforces_output'])

    def test_verified_profile_raises_observed_input_but_cumulative_usage_cannot(self):
        route = build_route('codex_cli_v1', 'unlisted-model')
        for observed in (100, 150000):
            with self.subTest(observed=observed):
                smaller_profile = dict(verified_input_tokens=observed)
                unchanged = self.capacity.describe_route_capacity(route, smaller_profile)
                self.assertEqual(unchanged['input_tokens']['value'], 150000)
                self.assertEqual(unchanged['input_tokens']['source'], 'default')
                self.assertEqual(smaller_profile['verified_input_tokens'], observed)
        profile = dict(verified_input_tokens=200000)
        result = self.capacity.describe_route_capacity(route, profile)
        self.assertEqual(result['input_tokens']['value'], 200000)
        self.assertEqual(result['input_tokens']['source'], 'observed')
        self.assertEqual(result['request_measure'], 'bytes')

    def test_request_measure_counts_native_packet_and_instructions(self):
        for adapter in ('claude_cli_v1', 'codex_cli_v1', 'codex_cli_framed_v1'):
            with self.subTest(adapter=adapter):
                model = 'claude-opus-5-5' if adapter == 'claude_cli_v1' else 'gpt-6-astra'
                route = build_route(adapter, model)
                request = transport.request_body(route, 'Return JSON.', {'text': 'x' * 1000000})
                result = self.capacity.measure_request(route, request)
                native = transport._local_adapter(route).packet_text(request)
                ratio = 1.36 if adapter == 'claude_cli_v1' else 2.1
                self.assertEqual(result['characters'], len(native))
                self.assertEqual(result['text_characters'], len(native) + len(request['instructions']))
                self.assertEqual(result['tokens_upper'], math.ceil(result['text_characters'] / ratio))
                self.assertEqual(result['measure'], 'calibrated')
                if adapter.startswith('codex_'):
                    self.assertFalse(result['fits'])

    def test_unicode_and_unknown_models_use_utf8_bytes(self):
        for model, text in [('claude-opus-5-5', '漢字🙂' * 400), ('unlisted', 'x' * 1000)]:
            route = build_route(model=model)
            request = transport.request_body(route, 'JSON', {'text': text})
            result = self.capacity.measure_request(route, request)
            self.assertEqual(result['measure'], 'bytes')
            self.assertEqual(result['tokens_upper'], result['utf8_bytes'])
            self.assertEqual(
                result['utf8_bytes'],
                len(
                    (request['instructions'] + transport._local_adapter(route).packet_text(request)).encode(
                        'utf-8',
                    ),
                ),
            )

    def test_https_measures_text_and_labels_binary_inputs_unmeasured(self):
        route = build_route('openai_responses_v1', 'gpt-6-astra')
        request = transport.request_body(route, 'JSON', {'text': 'hello'})
        result = self.capacity.measure_request(route, request)
        self.assertIsNone(result['character_limit'])
        self.assertEqual(result['measure'], 'bytes')
        self.assertEqual(result['binary_inputs'], 'unmeasured')
        self.assertGreater(result['text_characters'], 5)


class RouteReceiptTests(unittest.TestCase):

    def build_case(self):
        from test_research_review_protocol import ReviewProtocolTests
        case = ReviewProtocolTests()
        case.setUp()
        self.addCleanup(case.doCleanups)
        return case

    def read_command_report(self, case, command, payload, receipt):
        from research_harness import cli
        from unittest.mock import patch
        path = case.root / 'capacity-command.json'
        path.write_text(json.dumps(payload))
        args = cli.build_parser().parse_args(
            [command, '--workspace', str(case.root), '--file', str(path), '--expected-revision', str(case.store.revision), '--request-id', 'report-fixture'],
        )
        with patch.dict(cli.OPERATIONS, {command: lambda *args, **kwargs: receipt}):
            with patch.object(cli, 'Store', return_value=case.store):
                with patch.object(cli, 'find_workspace', return_value=case.root):
                    return cli.run(args)

    def test_review_receipts_report_capacity_without_changing_saved_records(self):
        case = self.build_case()
        receipt = case.route()
        self.assertNotIn('route_capacity', receipt)
        payload = {key: receipt['result'][key] for key in ('id', 'adapter', 'model', 'endpoint', 'configuration')}
        reported = self.read_command_report(case, 'review-route', payload, receipt)
        self.assertIn('route_capacity', reported)
        self.assertEqual(reported['result'], receipt['result'])
        self.assertEqual(
            case.store.committed_request(receipt['request_id'])['response'],
            receipt,
        )
        saved = copy.deepcopy(case.records()['review_route']['route'])
        self.assertNotIn('route_capacity', saved)
        case.run_probe()
        from research_harness import review_protocol as protocol
        probe = case.records()['review_route_probe']['probe']
        self.assertEqual(probe['task']['kind'], 'probe')
        with case.store._connection() as connection:
            request_id = connection.execute(
                'select request_id from events where revision=?',
                (probe['recorded_revision'],),
            ).fetchone(
            )[0]
        probe_receipt = case.store.committed_request(request_id)['response']
        reported = self.read_command_report(
            case,
            'review-probe',
            dict(id='probe', route_id='route'),
            probe_receipt,
        )
        self.assertIn('route_capacity', reported)
        self.assertEqual(probe_receipt['result'], probe)
        self.assertEqual(reported['result'], probe)
        self.assertEqual(case.store.committed_request(request_id)['response'], probe_receipt)
        from research_harness.check_mode import check_mutation
        self.assertEqual(
            check_mutation(
                case.store,
                protocol.record_route,
                payload,
                expected_revision=receipt['revision'] - 1,
                request_id=receipt['request_id'],
                bounded_payload=payload,
            ),
            receipt,
        )
        report = protocol.describe_route_report(case.records(), 'route')
        self.assertEqual(report['route_capacity']['input_tokens']['source'], 'default')
        self.assertEqual(case.records()['review_route']['route'], saved)
        self.assertNotIn(
            'route_capacity',
            protocol.route_state(case.records(), case.artifacts, 'route'),
        )
        original_state = protocol.route_state(case.records(), case.artifacts, 'route')
        legacy_records = copy.deepcopy(case.records())
        for probe in legacy_records['review_route_probe'].values():
            for key in ('task', 'answer', 'verified_input_tokens', 'cli_version', 'completed_generation'):
                probe.pop(key, None)
            for key in ('thinking_tokens', 'thinking_method', 'sends', 'request_characters'):
                probe['usage'].pop(key, None)
        self.assertEqual(
            protocol.route_state(legacy_records, case.artifacts, 'route'),
            original_state,
        )
        assigned = case.assign()
        assignment = case.records()['review_assignment']['assignment']
        self.assertEqual(assigned['result'], assignment)
        self.assertNotIn('task', assignment)
        self.assertNotIn('capacity_warning', assigned)
        assignment_payload = {key: assignment[key] for key in (
            'id', 'route_id', 'reviewer_id', 'author_id', 'dossier_id', 'role', 'context')}
        reported = self.read_command_report(case, 'review-assignment', assignment_payload, assigned)
        self.assertIn('capacity_warning', reported)
        self.assertEqual(reported['result'], assignment)
        self.assertEqual(case.store.committed_request(assigned['request_id'])['response'], assigned)
        self.assertEqual(
            check_mutation(case.store, protocol.record_assignment, assignment_payload,
                           expected_revision=assigned['revision'] - 1,
                           request_id=assigned['request_id'], bounded_payload=assignment_payload),
            assigned,
        )
        self.assertNotIn(
            'route_capacity',
            case.records()['review_assignment']['assignment']['context_state'],
        )


class RecordedRequestMeasureTests(unittest.TestCase):

    def test_measure_bounds_the_verified_request_fixture_table(self):
        import base64
        import hashlib
        from pathlib import Path
        from research_harness import review_route_capacity as capacity
        from test_research_codex_visual_transport import png
        table = json.loads(
            (Path(__file__).parent / 'fixtures' / 'route_capacity_measurements.json').read_text(),
        )
        self.assertEqual(table['source'], capacity.MEASUREMENT_SOURCE)
        for row in table['rows']:
            with self.subTest(adapter=row['adapter'], model=row['model']):
                route = build_route(row['adapter'], row['model'])
                request = dict(
                    instructions='I' * (row['text_characters'] - row['characters']),
                    input=[dict(role='user', content='')],
                )
                if row['adapter'] == 'codex_cli_visual_v1':
                    data = png(90)
                    request['exactory_image_inputs'] = [dict(
                        artifact=dict(
                            path='synthetic.png',
                            sha256=hashlib.sha256(data).hexdigest(),
                            size=len(data),
                            media_type='image/png',
                        ),
                        data_url='data:image/png;base64,' + base64.b64encode(data).decode(),
                    )]
                overhead = capacity.measure_request(route, request)['characters']
                request['input'][0]['content'] = 'x' * (row['characters'] - overhead)
                measured = capacity.measure_request(route, request)
                self.assertEqual(measured['characters'], row['characters'])
                self.assertEqual(measured['text_characters'], row['text_characters'])
                self.assertEqual(measured['tokens_upper'], row['tokens_upper'])
                self.assertGreaterEqual(measured['tokens_upper'], row['input_tokens'])
                if row['adapter'] == 'codex_cli_visual_v1':
                    self.assertEqual(measured['native_images'], 1)
                    self.assertEqual(measured['binary_inputs'], 'unmeasured')

    def test_exact_visible_request_counts_encoded_wire_text(self):
        from research_harness import review_route_capacity as capacity, review_exact_text_v2 as codec
        from research_harness import review_codex_framed_transport as framed
        messages = [dict(role='user', content='JSON ' + 'synthetic source ' * 30)]
        encoded = codec.encode(messages, {})
        request = dict(instructions='Instructions', input=[dict(role='user', content=encoded)])
        route = build_route('codex_cli_framed_v1', 'gpt-6-astra')
        result = capacity.measure_request(route, request)
        self.assertEqual(result['characters'], len(framed.packet_text(request)))
        self.assertEqual(result['text_characters'], len(framed.packet_text(request)) + 12)

    def test_capacity_guard_requires_a_row_for_every_transport(self):
        from research_harness import review_route_capacity as capacity
        self.assertEqual(
            set(capacity.CAPACITY_ADAPTERS),
            {transport.ADAPTER} | set(transport.LOCAL_ADAPTERS),
        )


class RevisedTokenRoomTests(unittest.TestCase):

    def test_claude_token_estimate_limits_new_plans_but_not_legacy_admission(self):
        from research_harness import review_route_capacity as capacity
        route = build_route()
        value = capacity.describe_route_capacity(route)
        self.assertEqual(value['input_text_characters']['value'], 1141040)
        self.assertEqual(capacity.describe_text_room(route, 10000), 1131040)
        self.assertEqual(
            capacity.describe_text_room(route, 10000, legacy_protocol=True),
            1300000,
        )
        request = transport.request_body(route, 'JSON', {'text': 'x' * 1200000})
        self.assertFalse(capacity.measure_request(route, request)['fits'])
        self.assertTrue(capacity.measure_request(route, request)['token_estimate'])

    def test_token_constants_keep_the_five_percent_measured_margin(self):
        from pathlib import Path
        from research_harness import review_route_capacity as capacity
        table = json.loads(
            (Path(__file__).parent / 'fixtures' / 'route_capacity_measurements.json').read_text(),
        )
        self.assertEqual(len(table['probe_requests']), 18)
        for row in table['probe_requests']:
            if row['sends'] == 1:
                self.assertGreaterEqual(
                    math.ceil(row['text_characters'] / capacity.CLAUDE_CHARACTERS_PER_TOKEN),
                    row['input_tokens'],
                )
            else:
                self.assertEqual(row['sends'], 2)
        for pair in table['material_pairs']:
            ratio = (pair['second_text_characters'] - pair['first_text_characters']) / (pair['second_input_tokens'] - pair['first_input_tokens'])
            self.assertGreaterEqual(
                pair['second_text_characters'] - pair['first_text_characters'],
                20000,
            )
            self.assertLessEqual(capacity.CLAUDE_CHARACTERS_PER_TOKEN, 0.95 * ratio)
        estimate = table['interim_material_estimate']
        self.assertLessEqual(
            capacity.CLAUDE_CHARACTERS_PER_TOKEN,
            0.95 * estimate['text_characters'] / estimate['input_tokens'],
        )
        self.assertLessEqual(capacity.CODEX_CHARACTERS_PER_TOKEN, 0.95 * 2.22)


class OutputReservationTests(unittest.TestCase):

    def test_input_limits_state_their_output_reservation(self):
        from research_harness import review_route_capacity as capacity
        for adapter, model, basis in (
            ('claude_cli_v1', 'claude-opus-5-5', 'compaction_threshold'),
            ('codex_cli_v1', 'gpt-6-astra', 'completed_answer'),
            ('openai_responses_v1', 'gpt-6-astra', 'model_window'),
            ('openai_responses_v1', 'unlisted-model', 'adapter_default'),
        ):
            with self.subTest(adapter=adapter, model=model):
                limit = capacity.describe_route_capacity(build_route(adapter, model))['input_tokens']
                self.assertEqual(limit['output_reservation_tokens'], 128000)
                self.assertEqual(limit['reservation_basis'], basis)

    def test_measure_keeps_its_calibration_assumption(self):
        from research_harness import review_route_capacity as capacity
        route = build_route()
        value = capacity.measure_request(
            route,
            transport.request_body(route, 'JSON', {'text': 'synthetic'}),
        )
        self.assertEqual(value['characters_per_token'], 1.36)
        self.assertTrue(value['token_estimate'])
