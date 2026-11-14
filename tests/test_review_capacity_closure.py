"""Capacity closure C1, C3, C10 and ruling27 with synthetic evidence."""
import copy
import importlib
import json
import unittest
from pathlib import Path
from unittest.mock import patch

from research_harness import review_route_capacity as capacity
from research_harness.errors import ResearchError
from test_review_route_capacity import build_route
from test_review_output_profile import build_request, build_sample, build_row


def build_runtime(adapter='codex_cli_v1'):
    if adapter == 'openai_responses_v1':
        return dict(adapter=adapter, implementation_sha256='a' * 64,
                    input_contract='explicit-packet-only-v1', host='synthetic-host',
                    platform='synthetic-platform', python='3.9')
    if adapter == 'claude_cli_v1':
        return dict(adapter=adapter, implementation_sha256='a' * 64, flags=['-p'],
                    input_contract='observed-print-stream-v1', credential_mode='claude-subscription',
                    output_limit='provider maximum', wall_limit='owned-process-group-timeout')
    return dict(adapter=adapter, implementation_sha256='a' * 64, version='synthetic-cli 1',
                controls=['synthetic-control'], executable='/synthetic/cli',
                input_contract='synthetic-input-contract', credential_mode='chatgpt-subscription',
                output_limit='checked-after-generation', wall_limit='owned-process-group-timeout')


def build_evidence_route(adapter='codex_cli_v1', model='gpt-6-astra', output=8000):
    return dict(build_route(adapter, model, output=output), runtime=build_runtime(adapter))


def load_evidence():
    return importlib.import_module('research_harness.review_capacity_evidence')


def build_prior(route, tokens=200000):
    return dict(id='synthetic-prior', key=load_evidence().describe_capacity_key(route),
                input_tokens=tokens, capacity_probe=False, source_record='synthetic-capacity',
                date='2026-11-14', fixture='synthetic-fixture', fixture_row=0)


def build_capacity_records(route, tokens=30000, *, capacity_probe=False):
    observation = dict(key=load_evidence().describe_capacity_key(route), input_tokens=tokens)
    attempt = dict(id='attempt', origin='instrumented', route_id=route['id'],
                   runtime=copy.deepcopy(route['runtime']), capacity_evidence=observation,
                   usage=dict(input_tokens=tokens, output_tokens=500, sends=1))
    if capacity_probe:
        attempt['capacity'] = dict(input_tokens=tokens)
    return dict(review_route={route['id']: route}, review_route_probe={'attempt': attempt}) if capacity_probe else dict(
        review_route={route['id']: route}, review_attempt={'attempt': attempt})


class CapacityEvidenceTests(unittest.TestCase):
    def describe(self, route, records=None, priors=()):
        with patch.object(load_evidence(), 'CAPACITY_PRIORS', priors):
            return capacity.describe_route_capacity(route, records=records or {})

    def test_two_endpoints_share_the_profile_class_but_no_input_capacity(self):
        a = build_evidence_route('openai_responses_v1', 'unlisted-model')
        b = dict(copy.deepcopy(a), id='route-b', endpoint='https://other.test/responses')
        records = build_capacity_records(a, 200000, capacity_probe=True)
        records['review_route'][b['id']] = b
        self.assertEqual(capacity.describe_route_class(a), capacity.describe_route_class(b))
        self.assertEqual(self.describe(a, records)['input_tokens']['value'], 192000)
        report = self.describe(b, records)
        self.assertEqual(report['input_tokens']['value'], 0)
        self.assertEqual(report['input_tokens']['source'], 'observed')
        self.assertEqual(report['capacity_evidence']['evidence'], [])

    def test_same_key_evidence_does_not_replace_the_default_probe_hint(self):
        route = build_evidence_route('openai_responses_v1', 'unlisted-model')
        result = self.describe(route, build_capacity_records(route, 25000, capacity_probe=True))
        self.assertEqual(result['input_tokens']['value'], 17000)
        self.assertEqual(result['capacity_evidence']['probe_input_tokens'], 150000)

    def prepare_native_case(self):
        from test_review_codex_invocation import CodexInvocationTests
        case = CodexInvocationTests()
        case.setUp()
        self.addCleanup(case.doCleanups)
        case.runtime.update(build_runtime())
        return case

    def test_native_settings_authenticate_before_an_ordinary_attempt_raises_capacity(self):
        case = self.prepare_native_case()
        for change in (lambda rows: None,
                       lambda rows: rows[1]['payload'].update(model='other-model'),
                       lambda rows: rows[1]['payload'].update(effort='xhigh'),
                       lambda rows: rows.append(copy.deepcopy(rows[1]))):
            events = copy.deepcopy(case.events)
            change(events)
            response = case.capture(case.build_response(events), events)
            response['usage']['input_tokens'] = 30000
            measured = capacity.describe_attempt(case.route, 'single', {}, case.request, response)
            if events == case.events:
                self.assertIsNotNone(measured['capacity_evidence'])
            else:
                self.assertIsNone(measured['capacity_evidence'])
            from research_harness.artifacts import ArtifactStore
            from research_harness.review_protocol import _put_json
            artifacts = ArtifactStore(case.directory)
            attempt = dict(id='attempt', origin='instrumented', route_id=case.route['id'],
                           runtime=case.runtime, usage=dict(response['usage'], sends=1), verified_input_tokens=30000,
                           request=_put_json(artifacts, case.request), response=_put_json(artifacts, response))
            records = dict(review_route={case.route['id']: case.route}, review_attempt=dict(attempt=attempt))
            self.assertEqual(capacity.describe_route_capacity(case.route, records=records)['input_tokens']['value'], 0)
            report = capacity.describe_route_capacity(case.route, records=records, artifacts=artifacts)
            self.assertEqual(report['input_tokens']['value'], 29000 if events == case.events else 0)

    def test_native_multiple_sends_are_counted_and_raise_no_capacity(self):
        case = self.prepare_native_case()
        response = case.capture(case.build_response(), case.events)
        response['execution'] = dict(event_types=['turn.started', 'turn.started'])
        measured = capacity.describe_attempt(case.route, 'single', {}, case.request, response)
        self.assertEqual(measured['usage']['sends'], 2)
        self.assertIsNone(measured['capacity_evidence'])

    def test_transport_change_needs_its_own_evidence_and_keeps_probe_hint(self):
        a = build_evidence_route('openai_responses_v1', 'unlisted-model')
        records = build_capacity_records(a, 200000, capacity_probe=True)
        b = copy.deepcopy(a)
        b['runtime']['implementation_sha256'] = 'b' * 64
        result = self.describe(b, records)
        self.assertEqual(result['input_tokens']['value'], 0)
        self.assertEqual(result['capacity_evidence']['probe_input_tokens'], 200000)
        self.assertEqual(result['capacity_evidence']['evidence'], [])

    def test_short_ordinary_answer_still_leaves_the_full_reservation(self):
        route = build_evidence_route('openai_responses_v1', 'unlisted-model')
        result = self.describe(route, build_capacity_records(route))
        self.assertEqual(result['input_tokens']['value'], 22000)
        route['configuration']['max_output_tokens'] = 40000
        self.assertEqual(self.describe(route, build_capacity_records(route))['input_tokens']['value'], 0)

    def test_codex_prior_matches_version_file_and_controls_only_under_its_key(self):
        route = build_evidence_route()
        prior = build_prior(route)
        self.assertEqual(self.describe(route, priors=(prior,))['input_tokens']['value'], 192000)
        for member, value in [('version', 'synthetic-cli 2'), ('implementation_sha256', 'b' * 64),
                              ('controls', ['different-control'])]:
            changed = copy.deepcopy(route)
            changed['runtime'][member] = value
            with self.subTest(member=member):
                report = self.describe(changed, priors=(prior,))
                self.assertEqual(report['input_tokens']['value'], 0)
                self.assertEqual(report['capacity_evidence']['evidence'], [])
                self.assertEqual(report['capacity_evidence']['probe_input_tokens'], 200000)
                records = build_capacity_records(changed, 25000, capacity_probe=True)
                self.assertEqual(self.describe(changed, records, (prior,))['input_tokens']['value'], 17000)

    def test_each_prior_pins_the_measured_fixture_input_and_execution_key(self):
        evidence = load_evidence()
        fixture = json.loads((Path(__file__).parent / 'fixtures' / 'route_capacity_measurements.json').read_text())
        priors = evidence.read_capacity_priors()
        self.assertEqual(len(priors), len(fixture['rows']))
        evidence.validate_capacity_inventory([], priors=priors)
        for prior in priors:
            with self.subTest(prior=prior['id']):
                measured = fixture['rows'][prior['fixture_row']]
                self.assertEqual(prior['id'], measured['capacity_prior_id'])
                self.assertEqual(prior['key'], measured['capacity_key'])
                self.assertEqual(prior['input_tokens'], measured['input_tokens'])
                self.assertEqual(prior['date'], fixture['date'])
                self.assertEqual(prior['source_record'], fixture['source'])

    def test_unkeyed_prior_supplies_no_limit(self):
        route = build_evidence_route()
        prior = build_prior(route)
        prior.pop('key')
        self.assertEqual(self.describe(route, priors=(prior,))['input_tokens']['value'], 0)

    def test_partial_prior_key_supplies_no_claude_limit_and_fails_k3(self):
        route = build_evidence_route('claude_cli_v1', 'claude-opus-5-5', 128000)
        for member in ('runtime', 'endpoint', 'model', 'configuration', 'adapter'):
            for missing in (True, False):
                prior = build_prior(route, 687640)
                if missing:
                    prior['key'].pop(member)
                else:
                    prior['key'][member] = None
                with self.subTest(member=member, missing=missing):
                    self.assertEqual(self.describe(route, priors=(prior,))['input_tokens']['value'], 0)
                    with self.assertRaises(ResearchError) as caught:
                        load_evidence().validate_capacity_inventory([], priors=[prior])
                    self.assertEqual(caught.exception.details['reason'], 'prior_key_missing')

    def test_host_and_installation_location_do_not_change_input_treatment(self):
        evidence = load_evidence()
        route = build_evidence_route('openai_responses_v1', 'unlisted-model')
        changed = copy.deepcopy(route)
        changed['runtime'].update(host='another-host', platform='another-platform', python='3.14')
        changed['configuration']['timeout_seconds'] = 5
        self.assertEqual(evidence.describe_capacity_key(route), evidence.describe_capacity_key(changed))
        route = build_evidence_route()
        changed = copy.deepcopy(route)
        changed['runtime']['executable'] = '/another/cli'
        self.assertEqual(evidence.describe_capacity_key(route), evidence.describe_capacity_key(changed))

    def test_every_runtime_and_configuration_member_has_a_reasoned_classification(self):
        evidence = load_evidence()
        for adapter in capacity.CAPACITY_ADAPTERS:
            route = build_evidence_route(adapter)
            rows = evidence.describe_capacity_members(route)
            self.assertEqual({row['member'] for row in rows if row['scope'] == 'runtime'}, set(route['runtime']))
            self.assertEqual({row['member'] for row in rows if row['scope'] == 'configuration'}, set(route['configuration']))
            self.assertTrue(all(row['reason'] for row in rows))
            evidence.validate_capacity_inventory([route], priors=())

    def test_k3_refuses_an_unclassified_runtime_member(self):
        route = build_evidence_route()
        route['runtime']['new_context_loader'] = True
        self.assertIsNone(load_evidence().describe_capacity_key(route))
        with self.assertRaises(ResearchError) as caught:
            load_evidence().validate_capacity_inventory([route], priors=())
        self.assertEqual(caught.exception.code, 'review_capacity_evidence_invalid')
        self.assertEqual(caught.exception.details['reason'], 'runtime_member_unclassified')

    def test_k3_refuses_an_unclassified_configuration_member(self):
        route = build_evidence_route()
        route['configuration']['new_context_setting'] = True
        with self.assertRaises(ResearchError) as caught:
            load_evidence().validate_capacity_inventory([route], priors=())
        self.assertEqual(caught.exception.details['reason'], 'configuration_member_unclassified')

    def test_k3_refuses_a_prior_without_its_key(self):
        unknown_version = build_prior(build_evidence_route())
        unknown_version['key']['runtime']['version'] = None
        route_without_version = dict(build_evidence_route(), runtime=unknown_version['key']['runtime'])
        self.assertIsNone(load_evidence().describe_capacity_key(route_without_version))
        for prior in [dict(id='unkeyed', input_tokens=200000), unknown_version]:
            with self.subTest(prior=prior['id']):
                with self.assertRaises(ResearchError) as caught:
                    load_evidence().validate_capacity_inventory([], priors=[prior])
                self.assertEqual(caught.exception.details['reason'], 'prior_key_missing')

    def test_k3_refuses_a_join_without_documented_ground(self):
        route = build_evidence_route()
        prior = build_prior(route)
        prior['joined_keys'] = [prior['key'], dict(prior['key'], endpoint='https://other.test')]
        with self.assertRaises(ResearchError) as caught:
            load_evidence().validate_capacity_inventory([], priors=[prior])
        self.assertEqual(caught.exception.details['reason'], 'prior_join_undocumented')

    def test_documented_join_retains_the_actual_prior_key_and_its_ground(self):
        evidence = load_evidence()
        first = build_evidence_route('openai_responses_v1', 'unlisted-model')
        second = dict(copy.deepcopy(first), endpoint='https://other.test/responses')
        prior = build_prior(first)
        prior['joined_keys'] = [prior['key'], evidence.describe_capacity_key(second)]
        prior['documented_ground'] = dict(statement='Both fixture endpoints accept the same input window.',
                                           url='https://example.test/provider-capacity')
        evidence.validate_capacity_inventory([first, second], priors=[prior])
        report = self.describe(second, priors=(prior,))
        self.assertEqual(report['input_tokens']['value'], 192000)
        source = report['capacity_evidence']['evidence'][0]
        self.assertEqual(source['key'], prior['key'])
        self.assertEqual(source['documented_ground'], prior['documented_ground'])

    def test_unverified_retried_and_compacted_observations_raise_no_bound(self):
        evidence = load_evidence()
        route = build_evidence_route('openai_responses_v1', 'unlisted-model')
        response = dict(status='completed', usage=dict(input_tokens=30000),
                        context_evidence=dict(input_verified=True, issues=[], runtime=route['runtime']))
        self.assertEqual(evidence.describe_capacity_observation(route, response, sends=1, request=build_request(route, {}))['input_tokens'], 30000)
        for sends, verified, issues in [(2, True, []), (1, False, []), (1, True, ['compacted'])]:
            changed = copy.deepcopy(response)
            changed['context_evidence'].update(input_verified=verified, issues=issues)
            self.assertIsNone(evidence.describe_capacity_observation(route, changed, sends=sends, request=build_request(route, {})))

    def test_malformed_provider_metadata_supplies_no_capacity_witness(self):
        route = build_evidence_route('openai_responses_v1', 'unlisted-model')
        for response in [[], dict(context_evidence='invalid', usage=dict(input_tokens=30000)),
                         dict(context_evidence=dict(input_verified=True), usage='invalid'),
                         dict(usage=dict(input_tokens=30000), context_evidence=dict(input_verified=True,
                              runtime=['adapter', 'implementation_sha256', 'input_contract']))]:
            with self.subTest(response=response):
                self.assertIsNone(load_evidence().describe_capacity_observation(
                    route, response, sends=1, request=build_request(route, {})))

    def test_incomplete_http_answer_can_still_verify_its_stateless_input(self):
        route = build_evidence_route('openai_responses_v1', 'unlisted-model')
        request = build_request(route, {})
        response = dict(status='incomplete', usage=dict(input_tokens=30000))
        measured = capacity.describe_attempt(route, 'single', {}, request, response)
        self.assertEqual(measured['capacity_evidence']['input_tokens'], 30000)
        self.assertFalse(measured['completed_generation'])
        request['previous_response_id'] = 'synthetic-prior-response'
        self.assertIsNone(capacity.describe_attempt(route, 'single', {}, request, response)['capacity_evidence'])

    def test_probe_requires_completed_output_and_both_controls(self):
        route = build_evidence_route('openai_responses_v1', 'unlisted-model')
        controls = dict(allowed_control='A', evidence_control='B')
        response = dict(status='completed', usage=dict(input_tokens=30000),
                        context_evidence=dict(input_verified=True, issues=[], runtime=route['runtime']), output=[dict(
            type='message', role='assistant', content=[dict(type='output_text', text=json.dumps(dict(
                controls, excluded_controls=[])))])])
        evidence = load_evidence()
        self.assertIsNotNone(evidence.describe_capacity_observation(route, response, sends=1, request=build_request(route, {}), probe_controls=controls))
        response['status'] = 'incomplete'
        self.assertIsNone(evidence.describe_capacity_observation(route, response, sends=1, request=build_request(route, {}), probe_controls=controls))
        response['status'] = 'completed'
        self.assertIsNone(evidence.describe_capacity_observation(route, response, sends=1, request=build_request(route, {}),
                                                                probe_controls=dict(controls, evidence_control='other')))

    def test_new_attempt_freezes_its_key_and_legacy_response_supplies_the_same_limit(self):
        from test_research_review_protocol import ReviewProtocolTests
        case = ReviewProtocolTests()
        case.setUp()
        self.addCleanup(case.doCleanups)
        case.route()
        case.run_probe()
        case.assign()
        original = case.response
        def respond(output):
            return dict(original(output), usage=dict(input_tokens=30000, output_tokens=500))
        with patch.object(case, 'response', side_effect=respond):
            attempt = case.invoke(dict(finding='Synthetic finding.'))['result']
        records = case.records()
        route = records['review_route']['route']
        self.assertEqual(attempt['capacity_evidence']['key'], load_evidence().describe_capacity_key(route))
        self.assertEqual(capacity.describe_route_capacity(route, records=records)['input_tokens']['value'], 29000)
        legacy = copy.deepcopy(records)
        for key in ('capacity_evidence', 'verified_input_tokens'):
            legacy['review_attempt']['attempt'].pop(key)
        before = copy.deepcopy(legacy)
        self.assertEqual(capacity.describe_route_capacity(route, records=legacy, artifacts=case.artifacts)['input_tokens']['value'], 29000)
        self.assertEqual(legacy, before)
        self.assertEqual(legacy['review_attempt']['attempt']['request'], attempt['request'])

    def test_verified_claude_capacity_probe_replaces_the_unknown_model_default(self):
        route = build_evidence_route('claude_cli_v1', 'unlisted-model', 128000)
        self.assertEqual(self.describe(route)['input_tokens']['value'], 72000)
        for tokens in (900000, 100000):
            with self.subTest(tokens=tokens):
                records = build_capacity_records(route, tokens, capacity_probe=True)
                report = self.describe(route, records)
                self.assertEqual(report['input_tokens']['value'], max(0, tokens - 128000))
                self.assertEqual(report['input_tokens']['source'], 'observed')
                self.assertEqual(report['capacity_evidence']['verified_capacity_probe']['id'], 'attempt')
        unverified = build_capacity_records(route, 900000, capacity_probe=True)
        unverified['review_route_probe']['attempt'].pop('capacity_evidence')
        self.assertEqual(self.describe(route, unverified)['input_tokens']['value'], 72000)
        changed = copy.deepcopy(route)
        changed['runtime']['implementation_sha256'] = 'b' * 64
        self.assertEqual(self.describe(changed, build_capacity_records(route, 900000, capacity_probe=True))[
            'input_tokens']['value'], 72000)

    def test_claude_model_without_a_documented_compaction_threshold_uses_its_default(self):
        route = build_evidence_route('claude_cli_v1', 'gpt-6-astra', 128000)
        result = self.describe(route)
        self.assertEqual(result['input_tokens']['value'], 72000)
        self.assertEqual(result['input_tokens']['source'], 'default')
        self.assertIsNone(result['input_limits']['documented'])

    def test_claude_limit_is_the_minimum_and_ordinary_input_takes_no_second_reservation(self):
        route = build_evidence_route('claude_cli_v1', 'claude-opus-5-5', 128000)
        prior = build_prior(route, 687640)
        result = self.describe(route, priors=(prior,))
        self.assertEqual(result['input_limits']['documented']['value'], 839000)
        self.assertEqual(result['input_limits']['observed']['value'], 687640)
        self.assertEqual(result['input_tokens']['value'], 687640)
        self.assertGreater(700000, result['input_tokens']['value'])
        self.assertLessEqual(680000, result['input_tokens']['value'])
        records = build_capacity_records(route, 900000, capacity_probe=True)
        self.assertEqual(self.describe(route, records, (prior,))['input_tokens']['value'], 772000)

    def test_claude_threshold_shares_across_models_and_runtimes_but_not_thresholds(self):
        route = build_evidence_route('claude_cli_v1', 'claude-opus-5-5', 128000)
        prior = build_prior(route, 687640)
        other = build_evidence_route('claude_cli_v1', 'claude-sonnet-5-5', 128000)
        other['runtime']['implementation_sha256'] = 'b' * 64
        self.assertEqual(self.describe(other, priors=(prior,))['input_tokens']['value'], 687640)
        with patch.object(capacity, 'MODEL_LIMITS', capacity.MODEL_LIMITS + (('synthetic-small', 200000, 167000, 128000, 'https://example.test/model'),)):
            other['model'] = 'synthetic-small'
            self.assertEqual(self.describe(other, priors=(prior,))['input_tokens']['value'], 0)


class ClosureStartTests(unittest.TestCase):
    def profile(self, rows):
        return dict(rows=rows, frame_policy=dict(borrowing_ready=False, count_carried_state=True))

    def test_session_v4_uses_request_rows_and_counts_its_whole_request(self):
        route = build_route()
        for version in (2, 3, 4):
            with self.subTest(version=version):
                packet = dict(reviewer_excerpts=dict(protocol='exactory-review-session-v' + str(version)),
                              reading_turn=dict(reading_record=dict(turns=[dict(notes='carried' * 100)])))
                request = build_request(route, packet)
                task = capacity.describe_task('bar', packet, route=route, request=request)
                self.assertEqual(task['kind'], 'request')
                self.assertEqual(task['characters'], capacity.measure_request(route, request)['text_characters'])
                rows = [build_row(240000, kind='reading', count=8), build_row(480000, kind='request', count=8, over=3)]
                result = capacity.select_task_limits(route, self.profile(rows), task['kind'], frame_characters=200000)
                self.assertEqual(result['state'], 'over_limit_expected')
                self.assertIsNone(result['borrowed_kind'])
                self.assertEqual(result['sample_size'], 8)
                self.assertEqual(result['at_or_over_limit'], 3)

    def test_ruling27_starts_below_an_own_risky_range_beside_step5(self):
        rows = [build_row(240000, kind='request', count=3), build_row(480000, kind='request', count=4, over=3)]
        result = capacity.select_task_limits(build_route(), self.profile(rows), 'request', frame_characters=200000)
        self.assertEqual(result['material_allowance'], 30000)
        self.assertEqual(result['step5_material_allowance'], 120000)
        self.assertEqual(result['start_rule'], 'below_risky_ranges')

    def test_five_percent_is_strict_and_sparse_own_rows_do_not_define_risk(self):
        for count, over in [(20, 1), (3, 1)]:
            with self.subTest(count=count):
                rows = [build_row(480000, kind='request', count=count, over=over)]
                result = capacity.select_task_limits(build_route(), self.profile(rows), 'request', frame_characters=200000)
                self.assertEqual(result['start_rule'], 'step5' if count >= 4 else 'unmeasured')

    def test_no_lower_level_uses_step5_and_missing_thinking_never_establishes_fit(self):
        row = build_row(120000, kind='request', count=8, over=1)
        row['largest_thinking_tokens'] = None
        row['thinking_attempts'] = 0
        result = capacity.select_task_limits(build_route(), self.profile([row]), 'request', frame_characters=70000)
        self.assertEqual(result['state'], 'over_limit_expected')
        self.assertEqual(result['material_allowance'], result['step5_material_allowance'])
        self.assertEqual(result['start_rule'], 'step5')
        self.assertFalse(any(level['qualifies'] for level in result['levels']))
        row['at_or_over_limit'] = 0
        result = capacity.select_task_limits(build_route(), self.profile([row]), 'request', frame_characters=70000)
        self.assertEqual(result['state'], 'over_limit_expected')
        self.assertEqual(result['sample_size'], 8)
        self.assertIsNone(result['largest_thinking_tokens'])

    def test_output_profile_classification_does_not_depend_on_input_evidence_key(self):
        route = build_evidence_route()
        rows = [build_row(240000, kind='reading', count=8)]
        with patch.object(capacity, 'describe_route_capacity', return_value=dict(
                output_tokens=dict(value=128000), input_text_characters=dict(value=0), request_measure='calibrated')):
            self.assertEqual(capacity.select_task_limits(route, self.profile(rows), 'reading')['state'], 'fits')


if __name__ == '__main__':
    unittest.main()
