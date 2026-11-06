"""Reconstruct frozen failed request forms without authenticating their failures."""

import copy
import gzip
import hashlib
import json
from pathlib import Path
import runpy
import tempfile
import unittest
from unittest.mock import patch

from research_harness import record_check, review_protocol as protocol
from research_harness import review_transport as transport
from research_harness.artifacts import ArtifactStore
from research_harness.errors import ResearchError
from research_harness.evidence import digest
from research_harness.storage import Store


ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / 'tests/fixtures/failed_request_history'
PROFILES = ('unlabelled-probe', 'labelled-pre-fit-local',
            'codex-order-before-source-capacity', 'claude-order-before-full-text-steps',
            'complete-source-exact-text-v1')


def read_cases(profile):
    return json.loads(gzip.decompress((FIXTURES / (profile + '.json.gz')).read_bytes()))['cases']


class FailedRequestHistoryTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.artifacts = ArtifactStore(self.root)

    def load_case(self, frozen):
        route, prompt = frozen['route'], frozen['prompt']
        reference = self.artifacts.put(frozen['request'].encode(), 'application/json')
        self.assertEqual(reference['sha256'], frozen['request_sha256'])
        invocation = dict(id=frozen['name'], route_id=route['id'], request=reference,
                          recorded_revision=3)
        observation = dict(id=invocation['id'], status='failed', output=None,
            response=None, request=reference, request_digest=frozen['request_digest'],
            route_digest=digest(route), runtime=route['runtime'], recorded_revision=4,
            usage=dict(input_tokens=None, output_tokens=None, wall_seconds=0.1))
        records = dict(review_route={route['id']: route})
        if frozen['profile'] == 'unlabelled-probe':
            kind = 'review_probe_invocation'
            controls = {source: 'excluded-' + source for source in transport.EXCLUDED_SOURCES}
            invocation.update(positive_controls=frozen['packet'],
                controls=protocol._put_json(self.artifacts, controls),
                excluded_sources=list(transport.EXCLUDED_SOURCES),
                input_manifest=dict(instructions=digest(prompt['text']), packet=digest(frozen['packet']),
                                    history=[], tools=[], local_context_sources=[]))
            observation = dict(invocation, **observation)
            records['review_route_probe'] = {observation['id']: observation}
        else:
            kind = 'review_invocation'
            assignment = dict(id='assignment', role='result', route_id=route['id'],
                reviewer_id='synthetic-reviewer', author_id='synthetic-author',
                dossier_id='synthetic-dossier', context={},
                context_state=dict(context_status='unverified'),
                packet=protocol._put_json(self.artifacts, frozen['packet']),
                packet_digest=frozen['packet']['digest'], prompt=prompt,
                prompt_hash=prompt['rendered_prompt_hash'], history=frozen['history'],
                session_id='synthetic-session', route_digest=digest(route),
                recorded_revision=2)
            invocation.update(assignment_id=assignment['id'], route_digest=digest(route),
                packet_digest=assignment['packet_digest'], prompt_hash=assignment['prompt_hash'],
                invocation_mode='explicit-bar-continuation' if frozen['history'] else 'fresh-explicit-input',
                session_id=assignment['session_id'])
            error_code = ('review_format_unsupported' if frozen['name'] == 'attachment'
                          else 'review_input_too_large')
            observation.update(assignment_id=assignment['id'], invocation_id=invocation['id'],
                origin='instrumented', packet_digest=assignment['packet_digest'],
                prompt_hash=assignment['prompt_hash'], error=dict(code=error_code))
            records.update(review_assignment={assignment['id']: assignment},
                           review_attempt={observation['id']: observation})
        records[kind] = {invocation['id']: invocation}
        return records, invocation, kind

    def rebuild(self, records, invocation, kind):
        return record_check.rebuild_invocation(records, self.artifacts, invocation, kind=kind)

    def assert_changed(self, records, invocation, kind):
        with self.assertRaises(ResearchError) as caught:
            self.rebuild(records, invocation, kind)
        self.assertEqual(caught.exception.code, 'record_request_changed')

    def assert_frozen_profile(self, profile):
        for frozen in read_cases(profile):
            with self.subTest(shape=frozen['name']):
                records, invocation, kind = self.load_case(frozen)
                before = copy.deepcopy(records)
                if kind == 'review_invocation':
                    assignment = records['review_assignment']['assignment']
                    observation = records['review_attempt'][invocation['id']]
                    classified = record_check.describe_observation(records, self.artifacts,
                                                                    assignment, observation)
                    self.assertFalse(classified['authenticated'])
                    self.assertEqual(classified['classification']['outcome'],
                        'format_unsupported' if frozen['name'] == 'attachment' else 'input_over_limit')
                else:
                    observation = records['review_route_probe'][invocation['id']]
                    classified = record_check.describe_probe_observation(records, self.artifacts, observation)
                try:
                    rebuilt = self.rebuild(records, invocation, kind)
                except ResearchError as error:
                    self.fail('Frozen historical form was refused: ' + error.code)
                self.assertEqual(rebuilt, dict(sha256=frozen['request_sha256'],
                    request_digest=frozen['request_digest'], historical_profile=profile))
                self.assertEqual(records, before)
                if kind == 'review_invocation':
                    self.assertEqual(record_check.describe_observation(records, self.artifacts,
                        assignment, observation), classified)
                else:
                    self.assertEqual(record_check.describe_probe_observation(records, self.artifacts,
                        observation), classified)
                self.assertFalse(classified['authenticated'])
                self.assertEqual(classified['status'], 'failed')

    def test_unlabelled_probe(self):
        self.assert_frozen_profile('unlabelled-probe')

    def test_labelled_pre_fit_local(self):
        self.assert_frozen_profile('labelled-pre-fit-local')

    def test_codex_order_before_source_capacity(self):
        self.assert_frozen_profile('codex-order-before-source-capacity')

    def test_claude_order_before_full_text_steps(self):
        self.assert_frozen_profile('claude-order-before-full-text-steps')

    def test_complete_source_exact_text_v1(self):
        self.assert_frozen_profile('complete-source-exact-text-v1')

    def test_new_requests_keep_the_frozen_ordinary_bytes_and_do_not_need_observations(self):
        for profile in PROFILES:
            for frozen in read_cases(profile):
                with self.subTest(profile=profile, shape=frozen['name']):
                    records, invocation, kind = self.load_case(frozen)
                    current = transport.request_body(frozen['route'], frozen['prompt']['text'],
                                                     frozen['packet'], frozen['history'])
                    raw = json.dumps(current, sort_keys=True, ensure_ascii=False, allow_nan=False).encode()
                    self.assertEqual(hashlib.sha256(raw).hexdigest(), frozen['current_sha256'])
                    invocation['request'] = self.artifacts.put(raw, 'application/json')
                    records.pop('review_route_probe' if kind == 'review_probe_invocation' else 'review_attempt')
                    self.assertEqual(self.rebuild(records, invocation, kind),
                                     dict(sha256=frozen['current_sha256'], request_digest=digest(current)))

    def test_unidentified_or_successful_observations_never_use_history(self):
        for profile in PROFILES:
            for change in ('missing', 'completed', 'output', 'output-missing', 'identity', 'request',
                           'request_digest', 'route_digest', 'runtime', 'imported'):
                with self.subTest(profile=profile, change=change):
                    records, invocation, kind = self.load_case(read_cases(profile)[0])
                    key = 'review_route_probe' if kind == 'review_probe_invocation' else 'review_attempt'
                    observed = records[key][invocation['id']]
                    if change == 'missing':
                        records.pop(key)
                    elif change == 'completed':
                        observed['status'] = 'completed'
                    elif change == 'output':
                        observed['output'] = protocol._put_json(self.artifacts, {})
                    elif change == 'output-missing':
                        observed.pop('output')
                    elif change == 'identity':
                        observed['id'] = 'different'
                    elif change == 'runtime':
                        observed['runtime'] = dict(observed['runtime'], input_contract='unsupported')
                    elif change == 'imported':
                        if kind == 'review_probe_invocation':
                            observed['route_id'] = 'different'
                        else:
                            observed['origin'] = 'imported'
                    else:
                        observed[change] = 'different'
                    self.assert_changed(records, invocation, kind)

    def test_unknown_runtime_or_adapter_never_matches_even_with_coherent_route_digests(self):
        for profile in PROFILES:
            for field in ('adapter', 'implementation_sha256', 'input_contract'):
                with self.subTest(profile=profile, field=field):
                    records, invocation, kind = self.load_case(read_cases(profile)[0])
                    route = records['review_route']['route']
                    if field == 'adapter':
                        # Framed routes still build valid ordinary candidates.
                        if route['adapter'] != 'codex_cli_v1':
                            route['runtime']['adapter'] = 'unknown'
                        else:
                            route['adapter'] = 'codex_cli_framed_v1'
                            route['runtime']['adapter'] = route['adapter']
                    else:
                        route['runtime'][field] = 'unsupported'
                    key = 'review_route_probe' if kind == 'review_probe_invocation' else 'review_attempt'
                    records[key][invocation['id']]['route_digest'] = digest(route)
                    if kind == 'review_invocation':
                        records['review_assignment']['assignment']['route_digest'] = digest(route)
                        invocation['route_digest'] = digest(route)
                    if profile == 'complete-source-exact-text-v1' and field == 'adapter':
                        with self.assertRaises(ResearchError) as caught:
                            self.rebuild(records, invocation, kind)
                        self.assertEqual(caught.exception.code, 'review_complete_source_route')
                    else:
                        self.assert_changed(records, invocation, kind)

    def test_altered_assignment_and_invocation_bindings_never_match(self):
        profile = 'labelled-pre-fit-local'
        for location, field in (
                ('assignment', 'packet_digest'), ('assignment', 'prompt_hash'),
                ('assignment', 'route_digest'), ('assignment', 'route_id'),
                ('assignment', 'id'),
                ('invocation', 'packet_digest'), ('invocation', 'prompt_hash'),
                ('invocation', 'route_digest'), ('invocation', 'session_id'),
                ('invocation', 'invocation_mode'), ('observation', 'packet_digest'),
                ('observation', 'prompt_hash'), ('observation', 'assignment_id'),
                ('observation', 'invocation_id')):
            with self.subTest(location=location, field=field):
                records, invocation, kind = self.load_case(read_cases(profile)[0])
                targets = dict(assignment=records['review_assignment']['assignment'],
                               invocation=invocation, observation=records['review_attempt'][invocation['id']])
                if field == 'route_id':
                    records['review_route']['different'] = dict(records['review_route']['route'], id='different')
                targets[location][field] = 'different'
                self.assert_changed(records, invocation, kind)

    def test_altered_retained_inputs_never_match(self):
        for profile in PROFILES:
            for field in ('packet', 'prompt', 'history', 'serialized-request'):
                with self.subTest(profile=profile, field=field):
                    records, invocation, kind = self.load_case(read_cases(profile)[0])
                    if kind == 'review_probe_invocation':
                        if field == 'packet':
                            invocation['positive_controls']['allowed_control'] = 'different'
                        elif field == 'prompt':
                            invocation['input_manifest']['instructions'] = 'different'
                        elif field == 'history':
                            invocation['input_manifest']['history'] = ['unexpected']
                    else:
                        assignment = records['review_assignment']['assignment']
                        if field == 'packet':
                            packet = protocol._read_json(self.artifacts, assignment['packet'])
                            packet['role'] = 'bar'
                            assignment['packet'] = protocol._put_json(self.artifacts, packet)
                        elif field == 'prompt':
                            assignment['prompt']['text'] += ' altered'
                        elif field == 'history':
                            assignment['history'].append(dict(role='assistant', content='altered'))
                    if field == 'serialized-request':
                        request = protocol._read_json(self.artifacts, invocation['request'])
                        request['store'] = True
                        reference = protocol._put_json(self.artifacts, request)
                        invocation['request'] = reference
                        key = 'review_route_probe' if kind == 'review_probe_invocation' else 'review_attempt'
                        records[key][invocation['id']].update(request=reference, request_digest=digest(request))
                    if profile == 'complete-source-exact-text-v1' and field == 'history':
                        with self.assertRaises(ResearchError) as caught:
                            self.rebuild(records, invocation, kind)
                        self.assertEqual(caught.exception.code, 'review_complete_source_mismatch')
                    else:
                        self.assert_changed(records, invocation, kind)

    def test_probe_checks_both_positive_control_manifests(self):
        for target in ('invocation', 'observation'):
            for field in ('instructions', 'packet'):
                with self.subTest(target=target, field=field):
                    records, invocation, kind = self.load_case(read_cases('unlabelled-probe')[0])
                    observed = records['review_route_probe'][invocation['id']]
                    observed['input_manifest'] = copy.deepcopy(observed['input_manifest'])
                    (invocation if target == 'invocation' else observed)['input_manifest'][field] = 'different'
                    self.assert_changed(records, invocation, kind)

    def test_builder_errors_and_artifact_failures_are_not_reinterpreted(self):
        records, invocation, kind = self.load_case(read_cases('unlabelled-probe')[0])
        for error in (ResearchError('review_format_unsupported', 'synthetic builder refusal'),
                      RuntimeError('synthetic builder defect')):
            def fail_after_candidate(*args):
                yield {'unmatched': True}
                raise error
            with self.subTest(error=type(error).__name__), \
                    patch.object(transport, 'list_built_requests', side_effect=fail_after_candidate):
                with self.assertRaises(type(error)) as caught:
                    self.rebuild(records, invocation, kind)
                self.assertIs(caught.exception, error)
        reference = invocation['request']
        path = self.root / reference['path']
        path.chmod(0o600)
        path.write_bytes(b'corrupt')
        with self.assertRaises(ResearchError) as caught:
            self.rebuild(records, invocation, kind)
        self.assertNotEqual(caught.exception.code, 'record_request_changed')

    def test_unknown_profile_and_invocation_kind_are_not_supported(self):
        from research_harness.review_failed_request_history import build_historical_request
        frozen = read_cases('labelled-pre-fit-local')[0]
        self.assertIsNone(build_historical_request('unknown', frozen['route'],
            frozen['prompt']['text'], frozen['packet'], frozen['history']))
        records, invocation, kind = self.load_case(frozen)
        records['unsupported_invocation'] = records[kind]
        self.assert_changed(records, invocation, 'unsupported_invocation')

    def test_independent_fingerprint_and_receipts_keep_the_ordinary_failure(self):
        readers = runpy.run_path(str(ROOT / 'ci/record_fingerprint.py'))
        for profile in PROFILES:
            with self.subTest(profile=profile):
                records, invocation, kind = self.load_case(read_cases(profile)[0])
                store = Store(self.root, create=True)
                def apply(transaction):
                    for record_kind, rows in records.items():
                        for key, value in rows.items():
                            transaction.put(record_kind, key, value)
                    return {'synthetic': profile}
                receipt = store.mutate('synthetic.history', {'profile': profile}, apply,
                    expected_revision=store.revision, request_id=profile)
                before = store.snapshot()
                fingerprint = (readers['fingerprint_probe'](self.root, invocation['id'])
                    if kind == 'review_probe_invocation' else readers['fingerprint_snapshot'](self.root))
                result = self.rebuild(records, invocation, kind)
                self.assertEqual(result['historical_profile'], profile)
                after = (readers['fingerprint_probe'](self.root, invocation['id'])
                    if kind == 'review_probe_invocation' else readers['fingerprint_snapshot'](self.root))
                self.assertEqual(after, fingerprint)
                if kind == 'review_probe_invocation':
                    self.assertEqual(fingerprint, {'error': 'record_request_changed'})
                else:
                    self.assertEqual(fingerprint['assignments']['assignment']['rebuilt_requests']
                                     [invocation['id']], {'error': 'record_request_changed'})
                self.assertEqual(store.snapshot(), before)
                replayed = store.mutate('synthetic.history', {'profile': profile},
                    lambda transaction: self.fail('Receipt replay must not execute'),
                    expected_revision=before['revision'], request_id=profile)
                self.assertEqual(replayed, receipt)

    def test_oversized_history_and_codec_boundaries_keep_current_request_bytes(self):
        for profile in ('labelled-pre-fit-local', 'complete-source-exact-text-v1',
                        'claude-order-before-full-text-steps'):
            frozen_file = json.loads(gzip.decompress((FIXTURES / (profile + '.json.gz')).read_bytes()))
            for frozen in frozen_file['boundary_cases']:
                with self.subTest(profile=profile, measured=frozen['measured_characters']):
                    records, invocation, kind = self.load_case(frozen)
                    request = json.loads(frozen['request'])
                    adapter = transport._local_adapter(frozen['route'])
                    self.assertEqual(len(adapter.packet_text(request)), frozen['measured_characters'])
                    ordinary = transport.request_body(frozen['route'], frozen['prompt']['text'],
                                                      frozen['packet'], frozen['history'])
                    raw = json.dumps(ordinary, sort_keys=True, ensure_ascii=False, allow_nan=False).encode()
                    self.assertEqual(hashlib.sha256(raw).hexdigest(), frozen['current_sha256'])
                    rebuilt = self.rebuild(records, invocation, kind)
                    if frozen['measured_characters'] <= frozen.get('input_limit', 1048576):
                        self.assertNotIn('historical_profile', rebuilt)
                        self.assertEqual(frozen['request_sha256'], frozen['current_sha256'])
                    else:
                        self.assertEqual(rebuilt['historical_profile'], profile)
                        self.assertNotEqual(frozen['request_sha256'], frozen['current_sha256'])

    def test_protected_bytes_and_fit_orders_remain_at_the_released_baseline(self):
        baseline = json.loads((FIXTURES / 'protected-baseline.json').read_text())
        for name, expected in baseline['hashes'].items():
            with self.subTest(module=name):
                self.assertEqual(hashlib.sha256((ROOT / name).read_bytes()).hexdigest(), expected)
        self.assertEqual(list(transport.FIT_ORDER), baseline['fit_order'])
        self.assertEqual([list(order) for order in transport.EARLIER_FIT_ORDERS], baseline['earlier_fit_orders'])

    def test_paged_history_rebuilds_and_keeps_its_digest_validation(self):
        from research_harness.review_bodies import write_history
        frozen = read_cases('labelled-pre-fit-local')[1]
        records, invocation, kind = self.load_case(frozen)
        assignment = records['review_assignment']['assignment']
        history = assignment.pop('history')
        assignment['history_ref'] = write_history(self.artifacts, assignment, history)
        self.assertEqual(self.rebuild(records, invocation, kind)['historical_profile'], frozen['profile'])
        assignment['history_ref']['history_digest'] = 'different'
        with self.assertRaises(ResearchError) as caught:
            self.rebuild(records, invocation, kind)
        self.assertEqual(caught.exception.code, 'review_history_invalid')

    def test_altered_evidence_in_elided_history_is_not_hidden_by_reduction(self):
        frozen = read_cases('codex-order-before-source-capacity')[0]
        records, invocation, kind = self.load_case(frozen)
        history = records['review_assignment']['assignment']['history']
        earlier = json.loads(history[0]['content'][len(transport.PACKET_LABEL):])
        body = earlier['evidence'][0]['content']
        earlier['evidence'][0]['content'] = 'Y' * len(body)
        history[0]['content'] = transport.PACKET_LABEL + json.dumps(earlier, sort_keys=True)
        self.assert_changed(records, invocation, kind)

    def test_missing_or_detached_invocation_is_not_positive_identification(self):
        for profile in PROFILES:
            with self.subTest(profile=profile):
                records, invocation, kind = self.load_case(read_cases(profile)[0])
                records[kind].pop(invocation['id'])
                self.assert_changed(records, invocation, kind)
                records[kind][invocation['id']] = dict(invocation, recorded_revision=99)
                self.assert_changed(records, invocation, kind)

    def test_route_identity_must_match_its_record_key(self):
        for profile in ('unlabelled-probe', 'labelled-pre-fit-local'):
            with self.subTest(profile=profile):
                records, invocation, kind = self.load_case(read_cases(profile)[0])
                route = records['review_route']['route']
                route['id'] = 'different'
                key = 'review_route_probe' if kind == 'review_probe_invocation' else 'review_attempt'
                records[key][invocation['id']]['route_digest'] = digest(route)
                if kind == 'review_invocation':
                    records['review_assignment']['assignment']['route_digest'] = digest(route)
                    invocation['route_digest'] = digest(route)
                self.assert_changed(records, invocation, kind)

    def test_failure_receipt_is_still_failed_and_assignment_uses_the_current_builder(self):
        for profile in PROFILES[1:]:
            with self.subTest(profile=profile):
                frozen = read_cases(profile)[0]
                records, invocation, kind = self.load_case(frozen)
                before = protocol.build_assignment_request(records, self.artifacts,
                                                           records['review_assignment']['assignment'])
                self.rebuild(records, invocation, kind)
                after = protocol.build_assignment_request(records, self.artifacts,
                                                          records['review_assignment']['assignment'])
                self.assertEqual(before, after)
                self.assertEqual(hashlib.sha256(json.dumps(after, sort_keys=True, ensure_ascii=False,
                    allow_nan=False).encode()).hexdigest(), frozen['current_sha256'])
                self.assertEqual(records['review_attempt'][invocation['id']]['status'], 'failed')
                self.assertIsNone(records['review_attempt'][invocation['id']]['output'])

    def test_retained_body_descriptors_cover_utf8_base64_and_prior_elisions(self):
        from research_harness.review_failed_request_history import _matches_retained_bodies
        reference = dict(size=3, sha256='ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad')
        for encoding, original, changed in (('utf-8', 'abc', 'abd'), ('base64', 'YWJj', 'YWJk')):
            with self.subTest(encoding=encoding):
                packet = dict(evidence=[dict(artifact=reference, encoding=encoding, content=original)])
                self.assertTrue(_matches_retained_bodies(packet))
                packet['evidence'][0]['content'] = changed
                self.assertFalse(_matches_retained_bodies(packet))
                packet['evidence'][0]['content'] = ''
                packet['evidence'][0]['delivery'] = 'body-elided-from-replayed-history'
                self.assertTrue(_matches_retained_bodies(packet))

    def test_history_reader_runs_once_only_after_every_ordinary_candidate_misses(self):
        from research_harness import review_failed_request_history as historical
        frozen = read_cases('unlabelled-probe')[0]
        records, invocation, kind = self.load_case(frozen)
        original = historical.find_historical_request
        events = []

        def candidates(*args):
            for index in range(2):
                events.append('ordinary-' + str(index))
                yield {'unmatched': index}
            events.append('exhausted')

        def counted_reader(*args, **kwargs):
            events.append('historical')
            return original(*args, **kwargs)

        with patch.object(transport, 'list_built_requests', side_effect=candidates), \
                patch.object(historical, 'find_historical_request', side_effect=counted_reader):
            self.assertEqual(self.rebuild(records, invocation, kind)['historical_profile'], 'unlabelled-probe')
        self.assertEqual(events, ['ordinary-0', 'ordinary-1', 'exhausted', 'historical'])
        events.clear()

        def matching_candidates(*args):
            events.append('ordinary-miss')
            yield {'unmatched': True}
            events.append('ordinary-match')
            yield json.loads(frozen['request'])
            self.fail('An ordinary match must end reconstruction')

        with patch.object(transport, 'list_built_requests', side_effect=matching_candidates), \
                patch.object(historical, 'find_historical_request', side_effect=counted_reader):
            rebuilt = self.rebuild(records, invocation, kind)
        self.assertEqual(rebuilt, dict(sha256=frozen['request_sha256'], request_digest=frozen['request_digest']))
        self.assertEqual(events, ['ordinary-miss', 'ordinary-match'])
