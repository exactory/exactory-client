"""Recorded text requests keep their exact reductions without rebuilding each trial."""

import copy
import json
import unittest
from unittest.mock import patch

from literature_fixtures import LiteratureCase
from problem_parts_fixtures import ResultPartsCase
from codex_route_fixtures import capture_codex_response
from research_harness import cli, review_protocol as protocol, review_transport as transport
from research_harness import review_request_evaluation as requests
from research_harness.evaluation import Evaluation
from research_harness.evidence import digest
from research_harness.review_state_evaluation import runtime_scope
from test_review_html_delivery import ROUTE, entry, packet as html_packet
from test_review_source_capacity import source_packet


def build_pinned(route, prompt, packet, history, order):
    return transport.request_body(route, prompt, packet, history, order)


def make_reduction_packet():
    packet = html_packet(6000, 120)
    bodies = [entry(('source %d λ😀 "\\\n\t' % index) * 400, 'text/plain') for index in range(5)]
    packet['evidence'].extend(bodies)
    packet['previously_supplied_evidence'] = [bodies[0]['artifact']]
    packet['native'] = {'scientific_context': {'text_delivery_priority': [bodies[1]['artifact']['sha256']]}}
    packet['problem_evidence'] = {
        'openness_searches': [{'sources': [{'response': bodies[2]['artifact']}]}],
        'readings': [{'version_id': 'source-v1', 'depth': 'fulltext'}]}
    packet['link'] = {'version_id': 'source-v1', 'artifact': bodies[3]['artifact']}
    packet['scientific_history'] = [
        {'packet': {'digest': 'earlier', 'evidence': [entry('historical "\\\nλ' * 800, 'text/plain')]}}]
    packet['metadata'] = [None, True, False, 17, 1.0, -0.0, 'λ😀\u2028\u2029' + ''.join(map(chr, range(32)))]
    return packet


def make_history():
    earlier = {'digest': 'prior', 'evidence': [entry('earlier λ "\\\n' * 700, 'text/plain')]}
    return [{'role': 'user', 'content': transport.PACKET_LABEL + json.dumps(earlier, ensure_ascii=False)},
            {'role': 'assistant', 'content': 'An unresolved "claim".\nλ😀'},
            {'role': 'user', 'content': 'A no-op history reduction.'}]


class ExactFitTests(LiteratureCase):
    def routes(self):
        from research_harness import review_claude_transport as claude
        return [ROUTE, dict(ROUTE, adapter='claude_cli_v1', endpoint=claude.ENDPOINT,
                            configuration=dict(ROUTE['configuration'], effort='high'))]

    def test_differential_at_every_observed_boundary_for_all_five_orders(self):
        from research_harness import review_claude_transport as claude
        shapes = [(make_reduction_packet(), make_history()), (source_packet(), make_history()),
                  ({'evidence': [], 'problem_evidence': {}, 'metadata': 'irreducible λ"\\' * 100}, make_history()),
                  ({'evidence': [], 'metadata': 'x' * 8000}, []),
                  ({'evidence': [dict(artifact=entry('binary', 'application/pdf')['artifact'],
                                      encoding='base64', content='YmluYXJ5')]}, [])]
        for protection in ('shared', 'outside', 'pending'):
            packet = source_packet()
            shared = packet['evidence'][-1]['artifact']
            if protection == 'shared':
                packet['source_deliveries'][0]['evidence'].append(shared)
            elif protection == 'outside':
                packet['claim_evidence'] = [shared]
            else:
                packet['pending_source_requests'] = [{'id': 'unavailable', 'kind': 'source',
                    'version_id': 'unavailable-v1', 'depth': 'fulltext'}]
            shapes.append((packet, make_history()))
        for route in self.routes():
            adapter = transport._local_adapter(route)
            for order in (transport.FIT_ORDER,) + transport.EARLIER_FIT_ORDERS:
                for packet, history in shapes:
                    boundaries = set()
                    native_measure = adapter.packet_text

                    def measure(request):
                        text = native_measure(request)
                        boundaries.add(len(text))
                        return text

                    for limit in (0, 60000, 80000, 100000):
                        with patch.object(transport, 'CODEX_INPUT_LIMIT_CHARS', limit), \
                                patch.object(claude, 'INPUT_LIMIT_CHARACTERS', limit), \
                                patch.object(adapter, 'packet_text', side_effect=measure):
                            build_pinned(route, 'Review JSON λ"\\\n', packet, history, order)
                    for boundary in sorted(boundaries):
                        for limit in (boundary - 1, boundary, boundary + 1):
                            with self.subTest(adapter=route['adapter'], order=order, limit=limit), \
                                    patch.object(transport, 'CODEX_INPUT_LIMIT_CHARS', limit), \
                                    patch.object(claude, 'INPUT_LIMIT_CHARACTERS', limit):
                                expected = build_pinned(route, 'Review JSON λ"\\\n', packet, history, order)
                                actual = requests.request_for_order(Evaluation({}, self.artifacts), route,
                                    'Review JSON λ"\\\n', packet, history, order)
                                self.assertEqual(json.dumps(actual, ensure_ascii=False),
                                                 json.dumps(expected, ensure_ascii=False))

    def test_each_fit_candidate_materializes_once_including_source_trials(self):
        for packet, history, limit in ((make_reduction_packet(), make_history(), 1000),
                                       (source_packet(), make_history(), 80000)):
            for route in self.routes():
                for order in (transport.FIT_ORDER,) + transport.EARLIER_FIT_ORDERS:
                    with self.subTest(adapter=route['adapter'], order=order), \
                            patch.object(transport, 'local_input_limit', return_value=limit), \
                            patch.object(transport, '_build_local_request', wraps=transport._build_local_request) as build:
                        requests.request_for_order(Evaluation({}, self.artifacts), route, 'JSON', packet, history, order)
                    self.assertEqual(build.call_count, 1)

    def test_long_history_does_not_remeasure_unchanged_packet_scalars(self):
        from research_harness import review_request_fit as fitting
        packet = make_reduction_packet()
        packet['metadata'] = [dict(index=index, statement='A bounded mathematical statement.',
            binding=dict(kind='record', version_id='arxiv:2601.00001v1', sha256='a' * 64)) for index in range(100)]
        history = make_history() * 6
        with patch.object(transport, 'CODEX_INPUT_LIMIT_CHARS', 1000):
            expected = build_pinned(ROUTE, 'JSON', packet, history, transport.FIT_ORDER)
            with patch.object(fitting, '_measure_string', wraps=fitting._measure_string) as measure:
                actual = requests._build_request(ROUTE, 'JSON', packet, history, transport.FIT_ORDER)
            self.assertEqual(actual, expected)
            self.assertLess(measure.call_count, 10000)

    def test_json_lengths_include_nested_escaping_and_independent_container_occurrences(self):
        from research_harness.review_request_fit import measure_json
        value = make_reduction_packet()
        shared = value['evidence'][0]
        value['evidence'].append(shared)
        for escaped in (False, True):
            encoded = json.dumps(value, sort_keys=True, ensure_ascii=False)
            expected = len(json.dumps(encoded, ensure_ascii=False)) - 2 if escaped else len(encoded)
            self.assertEqual(measure_json(value, escaped=escaped), expected)
        for order in (transport.FIT_ORDER,) + transport.EARLIER_FIT_ORDERS:
            with patch.object(transport, 'CODEX_INPUT_LIMIT_CHARS', 1000):
                self.assertEqual(requests.request_for_order(Evaluation({}, self.artifacts), ROUTE, 'JSON', value, [], order),
                                 build_pinned(ROUTE, 'JSON', value, [], order))

    def test_unsupported_representations_call_the_unchanged_builder(self):
        cases = [(dict(ROUTE, adapter=adapter), {})
                 for adapter in ('codex_cli_framed_v1', 'codex_cli_visual_v1', transport.ADAPTER)]
        cases += [(ROUTE, {'complete_source_delivery': {}}), (ROUTE, {'staged_review': {}}),
                  (ROUTE, {'tuple': (1, 2)})]
        for route, packet in cases:
            with self.subTest(adapter=route['adapter'], packet=packet), \
                    patch.object(transport, 'request_body', return_value={'pinned': True}) as build:
                self.assertEqual(requests._build_request(route, 'JSON', packet, [], transport.FIT_ORDER), {'pinned': True})
                build.assert_called_once_with(route, 'JSON', packet, [])
        with patch.object(transport, 'request_body', return_value={'pinned': True}) as build:
            self.assertEqual(requests._build_request([], 'JSON', {}, [], transport.FIT_ORDER), {'pinned': True})
            build.assert_called_once_with([], 'JSON', {}, [])

    def test_malformed_binary_descriptor_preserves_the_pinned_exception(self):
        packet = {'evidence': [{'encoding': 'base64', 'content': 'YmluYXJ5'}]}
        with self.assertRaises(KeyError):
            build_pinned(ROUTE, 'JSON', packet, [], transport.FIT_ORDER)
        with self.assertRaises(KeyError):
            requests.request_for_order(Evaluation({}, self.artifacts), ROUTE, 'JSON', packet, [], transport.FIT_ORDER)

    def test_untracked_reduction_mutations_fall_back_to_the_pinned_builder(self):
        from research_harness import review_request_fit as fitting
        mutations = [
            ('insert', lambda packet: packet['items'].insert(0, 'inserted λ"\\\n')),
            ('extend', lambda packet: packet['items'].extend([4, 5])),
            ('pop', lambda packet: packet['items'].pop()),
            ('remove', lambda packet: packet['items'].remove(1)),
            ('clear_list', lambda packet: packet['items'].clear()),
            ('delete', lambda packet: packet['items'].__delitem__(0)),
            ('slice', lambda packet: packet['items'].__setitem__(slice(1, 2), ['replacement'])),
            ('iadd', lambda packet: packet['items'].__iadd__([4])),
            ('imul', lambda packet: packet['items'].__imul__(2)),
            ('sort', lambda packet: packet['items'].sort()),
            ('reverse', lambda packet: packet['items'].reverse()),
            ('popitem', lambda packet: packet['object'].popitem()),
            ('clear_dict', lambda packet: packet['object'].clear()),
            ('ior', lambda packet: packet['object'].__ior__({'added': 'λ"\\\n'})),
        ]
        for name, mutate in mutations:
            packet = {'evidence': [], 'items': [3, 1, 2], 'object': {'a': 1, 'b': 2}}
            original = copy.deepcopy(packet)

            def reduce(fit):
                mutate(fit.supplied)
                yield

            with self.subTest(mutation=name), patch.object(transport, 'CODEX_INPUT_LIMIT_CHARS', 1), \
                    patch.dict(transport._REDUCTIONS, history_bodies=reduce):
                expected = build_pinned(ROUTE, 'JSON', packet, [], transport.FIT_ORDER)
                self.assertIsNone(fitting.try_build_request(ROUTE, 'JSON', packet, [], transport.FIT_ORDER))
                with patch.object(transport, 'request_body', wraps=transport.request_body) as build:
                    actual = requests._build_request(ROUTE, 'JSON', packet, [], transport.FIT_ORDER)
                build.assert_called_once_with(ROUTE, 'JSON', packet, [])
                self.assertEqual(json.dumps(actual, ensure_ascii=False), json.dumps(expected, ensure_ascii=False))
                self.assertEqual(packet, original)

    def test_untracked_source_capacity_mutation_falls_back_to_the_pinned_builder(self):
        from research_harness import review_request_fit as fitting, review_source_capacity as capacity
        original_fit = capacity.fit
        packet = source_packet()

        def fit_sources(supplied, *args, **kwargs):
            supplied['evidence'].insert(0, entry('Additional complete source λ"\\\n', 'text/plain'))
            return original_fit(supplied, *args, **kwargs)

        with patch.object(transport, 'CODEX_INPUT_LIMIT_CHARS', 80000), \
                patch.object(capacity, 'fit', side_effect=fit_sources):
            expected = build_pinned(ROUTE, 'JSON', packet, [], transport.FIT_ORDER)
            self.assertIsNone(fitting.try_build_request(ROUTE, 'JSON', packet, [], transport.FIT_ORDER))
            with patch.object(transport, 'request_body', wraps=transport.request_body) as build:
                actual = requests._build_request(ROUTE, 'JSON', packet, [], transport.FIT_ORDER)
            build.assert_called_once_with(ROUTE, 'JSON', packet, [])
            self.assertEqual(json.dumps(actual, ensure_ascii=False), json.dumps(expected, ensure_ascii=False))

    def test_deep_native_json_keeps_the_pinned_request_and_character_length(self):
        packet = {'leaf': 'λ"\\\n'}
        for _ in range(450):
            packet = {'nested': packet}
        expected = build_pinned(ROUTE, 'JSON', packet, [], transport.FIT_ORDER)
        actual = requests.request_for_order(Evaluation({}, self.artifacts), ROUTE, 'JSON', packet, [], transport.FIT_ORDER)
        self.assertEqual(json.dumps(actual, ensure_ascii=False), json.dumps(expected, ensure_ascii=False))

    def test_circular_values_use_the_pinned_refusal(self):
        packet = {'evidence': []}
        packet['circular'] = packet
        with self.assertRaises(ValueError):
            requests.request_for_order(Evaluation({}, self.artifacts), ROUTE, 'JSON', packet, [], transport.FIT_ORDER)

    def test_surrogate_pairs_keep_the_pinned_json_roundtrip(self):
        packet = {'evidence': [], 'metadata': '\ud83d\ude00'}
        self.assertEqual(requests._build_request(ROUTE, 'JSON', packet, [], transport.FIT_ORDER),
                         build_pinned(ROUTE, 'JSON', packet, [], transport.FIT_ORDER))


class RecordedFitTests(ResultPartsCase):
    def seed_continuations(self):
        records = self.case.read_records()
        original = records['review_assignment']['bar-first']
        original_attempt = records['review_attempt']['attempt-bar-first']
        route = records['review_route'][original['route_id']]
        accepted = protocol._read_json(self.case.artifacts, original_attempt['output'])
        refused = dict(accepted, value=dict(accepted['value'], evidence=[]))
        pairs = []
        for index in range(6):
            packet = protocol._read_json(self.case.artifacts, original['packet'])
            packet.update(make_reduction_packet(), continuation={'assignment_id': original['id']})
            packet['digest'] = digest(packet)
            history = make_history() * 6
            assignment = dict(original, id='large-' + str(index), packet=protocol._put_json(self.case.artifacts, packet),
                              packet_digest=packet['digest'], history=history, recorded_revision=100 + index * 10)
            assignment.pop('history_ref', None)
            request = build_pinned(route, assignment['prompt']['text'], packet, history, transport.EARLIER_FIT_ORDERS[-1])
            self.case.put('review_assignment', assignment['id'], assignment)
            for turn, output in enumerate((refused, refused, accepted, accepted)):
                response = capture_codex_response(route, request, output)
                attempt = dict(original_attempt, id=assignment['id'] + '-attempt-' + str(turn),
                    assignment_id=assignment['id'], packet_digest=packet['digest'],
                    request=protocol._put_json(self.case.artifacts, request), request_digest=digest(request),
                    output=protocol._put_json(self.case.artifacts, output),
                    response=protocol._put_json(self.case.artifacts, response), recorded_revision=101 + index * 10 + turn)
                self.case.put('review_attempt', attempt['id'], attempt)
                if turn < 2:
                    self.case.put('review_output_refusal', attempt['id'], dict(id=attempt['id']))
                pairs.append((assignment, attempt))
        return pairs

    def test_large_recorded_continuation_materializations_bindings_and_complete_report(self):
        from research_harness.review_refusal import classify_observed_output
        with patch.object(transport, 'CODEX_INPUT_LIMIT_CHARS', 12000):
            pairs = self.seed_continuations()
            records = self.case.read_records()
            with patch.object(requests, '_build_request', side_effect=build_pinned):
                expected = cli.status_report(self.case.store)
                expected_bindings = [protocol._selected_attempt(records, self.case.artifacts, row['id'])
                                     for row in records['review_assignment'].values()]
            evaluation = Evaluation(records, self.case.artifacts)
            with runtime_scope(records, evaluation), \
                    patch.object(transport, '_build_local_request', wraps=transport._build_local_request) as build:
                for assignment, attempt in pairs:
                    self.assertTrue(protocol.authenticate_provenance(records, evaluation, assignment, attempt))
                bindings = [protocol._selected_attempt(records, evaluation, row['id'])
                            for row in records['review_assignment'].values()]
            self.assertEqual(bindings, expected_bindings)
            for assignment, _ in pairs[::4]:
                binding, refusals, _ = protocol._selected_attempt(records, evaluation, assignment['id'])
                self.assertEqual(binding['id'], assignment['id'] + '-attempt-2')
                self.assertTrue(protocol.authenticate_attempt(records, evaluation, assignment, binding))
                self.assertIsNone(classify_observed_output(records, evaluation, binding['id']))
                self.assertEqual([row['attempt_id'] for row in refusals],
                                 [assignment['id'] + '-attempt-0', assignment['id'] + '-attempt-1'])
            self.assertLessEqual(build.call_count, len(records['review_assignment']) * 5)
            self.assertEqual(cli.status_report(self.case.store), expected)

    def test_repeated_provenance_derives_output_once_within_runtime_scope(self):
        records = self.case.read_records()
        assignment = records['review_assignment']['bar-first']
        attempt = records['review_attempt']['attempt-bar-first']
        evaluation = Evaluation(records, self.case.artifacts)
        with runtime_scope(records, evaluation), patch.object(protocol, 'derive_output', wraps=protocol.derive_output) as derive:
            for _ in range(3):
                self.assertTrue(protocol.authenticate_provenance(records, evaluation, assignment, attempt))
        self.assertEqual(derive.call_count, 3)

    def test_refusal_selection_does_not_add_verification_or_request_entries(self):
        from research_harness.review_refusal import refused_attempts
        with patch.object(transport, 'CODEX_INPUT_LIMIT_CHARS', 12000):
            pairs = self.seed_continuations()
            records = self.case.read_records()
            assignment = pairs[0][0]
            completed = [attempt for assigned, attempt in pairs if assigned['id'] == assignment['id']]

            def select(native):
                evaluation = Evaluation(records, self.case.artifacts)
                with runtime_scope(records, evaluation):
                    for attempt in completed:
                        self.assertTrue(protocol.authenticate_provenance(records, evaluation, assignment, attempt))
                    before = evaluation.counters.copy()
                    requests_before = evaluation.retention_report()['request_entries']
                    if native:
                        refused = refused_attempts(records, evaluation, completed)
                        refused_ids = {item['attempt_id'] for item in refused}
                        usable = [attempt for attempt in completed if attempt['id'] not in refused_ids]
                        observed = [attempt for attempt in usable if protocol._context_observed(evaluation, attempt)]
                        selected = (observed or usable or [None])[0], refused, completed
                    else:
                        selected = protocol._selected_attempt(records, evaluation, assignment['id'])
                    counters = {name: count - before[name] for name, count in evaluation.counters.items()}
                    entries = evaluation.retention_report()['request_entries'] - requests_before
                    return selected, counters, entries

            expected, baseline, entries = select(True)
            actual, candidate, candidate_entries = select(False)
            self.assertEqual(actual, expected)
            for name, count in candidate.items():
                with self.subTest(counter=name):
                    self.assertLessEqual(count, baseline[name])
            self.assertLessEqual(candidate_entries, entries)

    def test_failed_cheap_provenance_predicates_do_not_read_history(self):
        records = self.case.read_records()
        assignment = records['review_assignment']['bar-first']
        attempt = records['review_attempt']['attempt-bar-first']
        for changes in ({'origin': 'imported'}, {'runtime': {}}, {'route_digest': '0' * 64}, {'request': None}):
            with patch.object(protocol, 'read_history', wraps=protocol.read_history) as read:
                self.assertFalse(protocol.authenticate_provenance(records, self.case.artifacts,
                                                                  assignment, dict(attempt, **changes)))
            self.assertEqual(read.call_count, 0)

    def test_predicates_bind_same_id_substitutions_and_historical_views(self):
        from research_harness.storage import _RecordHistory, _RecordedSnapshot, _validate
        history = _RecordHistory()
        with self.case.store._connection() as connection:
            revision = _validate(connection, history=history)
        records = _RecordedSnapshot(self.case.read_records(), history=history, revision=revision)
        assignment = records['review_assignment']['bar-first']
        attempt = records['review_attempt']['attempt-bar-first']
        evaluation = Evaluation(records, self.case.artifacts)
        before = copy.deepcopy(records)
        with runtime_scope(records, evaluation):
            self.assertTrue(protocol.authenticate_provenance(records, evaluation, assignment, attempt))
            changed_prompt = dict(assignment, prompt=dict(assignment['prompt'], text='Substituted prompt'))
            changed_history = dict(assignment, history=[{'role': 'user', 'content': 'Substituted ancestry'}])
            changed_history.pop('history_ref', None)
            changed_output = dict(attempt, output=protocol._put_json(self.case.artifacts, {'substituted': True}))
            changed_descriptor = dict(attempt, request=dict(attempt['request'], size=attempt['request']['size'] + 1))
            for assigned, observed in ((changed_prompt, attempt), (changed_history, attempt),
                                       (assignment, changed_output), (assignment, changed_descriptor),
                                       (assignment, dict(attempt, origin='imported'))):
                self.assertFalse(protocol.authenticate_provenance(records, evaluation, assigned, observed))
            historical = evaluation.read_snapshot(records.read_revision() - 1)
            with patch.object(protocol, 'derive_output', wraps=protocol.derive_output) as derive:
                self.assertTrue(protocol.authenticate_provenance(historical.records, historical, assignment, attempt))
            self.assertEqual(derive.call_count, 1)
        self.assertEqual(records, before)
        with runtime_scope(records, evaluation), patch.object(protocol, 'derive_output', wraps=protocol.derive_output) as derive:
            self.assertTrue(protocol.authenticate_provenance(records, evaluation, assignment, attempt))
        self.assertEqual(derive.call_count, 1)

    def test_refusal_reuse_preserves_order_first_binding_and_budget_policy(self):
        from research_harness import review_refusal
        with patch.object(transport, 'CODEX_INPUT_LIMIT_CHARS', 12000):
            pairs = self.seed_continuations()
            assignment = pairs[0][0]
            records = self.case.read_records()
            for budget in (0, 1, 64 * 1024 * 1024):
                evaluation = Evaluation(records, self.case.artifacts, retention_budget_bytes=budget)
                with runtime_scope(records, evaluation), patch.object(review_refusal, 'classify_observed_output',
                        wraps=review_refusal.classify_observed_output) as classify:
                    results = [protocol._selected_attempt(records, evaluation, assignment['id']) for _ in range(3)]
                    results[0][1][0]['code'] = 'Caller substitution'
                    self.assertNotEqual(protocol._selected_attempt(records, evaluation, assignment['id'])[1][0]['code'],
                                        'Caller substitution')
                    results[0][1][0]['code'] = results[1][1][0]['code']
                self.assertEqual(results[0], results[1])
                self.assertEqual(results[1], results[2])
                self.assertEqual(results[0][0]['id'], assignment['id'] + '-attempt-2')
                self.assertEqual([row['attempt_id'] for row in results[0][1]],
                                 [assignment['id'] + '-attempt-0', assignment['id'] + '-attempt-1'])
                self.assertEqual(classify.call_count, 8)
                self.assertLessEqual(evaluation.retention_report()['optional_bytes'], budget)

    def test_runtime_and_emitter_drift_discard_command_predicates(self):
        from research_harness import review_state_evaluation as states
        from research_harness.errors import ResearchError
        records = self.case.read_records()
        assignment = records['review_assignment']['bar-first']
        attempt = records['review_attempt']['attempt-bar-first']
        for target, name in ((transport, 'runtime'), (states, 'profile')):
            evaluation = Evaluation(records, self.case.artifacts)
            with self.assertRaises(ResearchError) as error, runtime_scope(records, evaluation):
                self.assertTrue(protocol.authenticate_provenance(records, evaluation, assignment, attempt))
                changed = patch.object(target, name, return_value={'changed': True})
                changed.start()
            changed.stop()
            self.assertEqual(error.exception.code, 'review_runtime_changed')
            with runtime_scope(records, evaluation), patch.object(protocol, 'derive_output', wraps=protocol.derive_output) as derive:
                self.assertTrue(protocol.authenticate_provenance(records, evaluation, assignment, attempt))
            self.assertEqual(derive.call_count, 1)

    def test_only_last_metadata_matching_probe_is_authenticated_even_when_failed(self):
        records = self.case.read_records()
        saved = records['review_route_probe']['probe-route']
        for index in range(12):
            records['review_route_probe']['probe-' + str(index)] = dict(saved, id='probe-' + str(index),
                                                                      recorded_revision=min(100 + index, 110))
        records['review_route_probe']['unrelated'] = dict(saved, id='unrelated', route_digest='0' * 64,
                                                         recorded_revision=999)
        records['review_route_probe']['other-runtime'] = dict(saved, id='other-runtime', runtime={'different': True},
                                                             recorded_revision=1000)
        for failed in (False, True):
            records['review_route_probe']['probe-11']['status'] = 'failed' if failed else 'completed'
            with patch.object(protocol, 'derive_output', wraps=protocol.derive_output) as derive:
                state = protocol._route_state(records, Evaluation(records, self.case.artifacts), 'route')
            self.assertEqual(state['probe_id'], None if failed else 'probe-11')
            self.assertEqual(state['context_status'], 'unverified' if failed else 'verified_for_route')
            self.assertEqual(derive.call_count, 0 if failed else 1)


class RefusalDependencyTests(unittest.TestCase):
    def test_dependency_tracing_keeps_the_callers_active_ancestry_guards(self):
        from research_harness.errors import ResearchError
        from pathlib import Path
        import tempfile
        from research_harness.artifacts import ArtifactStore
        with tempfile.TemporaryDirectory() as directory:
            records = {'review_assignment': {'active': {'id': 'active'}}}
            evaluation = Evaluation(records, ArtifactStore(Path(directory)))
            evaluation._active_states.add(('assignment', 'active'))

            def compute(reader):
                return protocol.assignment_state(records, reader, 'active')

            with runtime_scope(records, evaluation), self.assertRaises(ResearchError) as error:
                compute(evaluation)
            self.assertEqual(error.exception.code, 'review_context_repair_mismatch')

    def test_evicted_locator_bytes_are_reverified_before_reusing_a_refusal(self):
        from test_research_problem_adjudication_resolution import ReviewOutputRefusalTests
        from research_harness import review_refusal
        from research_harness.errors import ResearchError
        fixture = ReviewOutputRefusalTests()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        output = fixture.prepare_bar()
        link = dict(fixture.inside, locator={'kind': 'span', 'start': 0, 'end': 999999, 'sha256': 'bad'})
        output['value']['evidence'] = [{'kind': 'source', 'link': link}]
        self.assertEqual(fixture.refuse(output).code, 'invalid_locator')
        records = fixture.records()
        evaluation = Evaluation(records, fixture.artifacts, retention_budget_bytes=64000)
        identifier = 'bar-first'
        target = fixture.supporting['sha256']
        with runtime_scope(records, evaluation):
            self.assertTrue(protocol._selected_attempt(records, evaluation, identifier)[1])
            def count_predicates():
                return sum(key[0] == 'memo' and isinstance(key[2], tuple) and key[2][0] == 'encoded_value'
                           and key[2][1][0] == 'review_predicate' and key[2][1][3] == 'refusal'
                           for key in evaluation._optional)

            self.assertEqual(count_predicates(), 0)
            # Isolate the eviction that interleaved artifact streaming can cause,
            # keeping the existing provenance inputs warm.
            removed = 0
            for key in list(evaluation._optional):
                if target in repr(key):
                    _, weight = evaluation._optional.pop(key)
                    evaluation._retained_bytes[0] -= weight
                    removed += 1
            self.assertGreater(removed, 0)
            self.assertEqual(count_predicates(), 0)
            native_read = fixture.artifacts.read

            def read(reference):
                if reference['sha256'] == target:
                    raise ResearchError('artifact_missing', 'Synthetic unavailable source')
                return native_read(reference)

            with patch.object(fixture.artifacts, 'read', side_effect=read):
                self.assertEqual(review_refusal.refused_attempts(records, Evaluation(records, fixture.artifacts),
                    [records['review_attempt']['refused-1']]), [])
                self.assertEqual(count_predicates(), 0)
                self.assertEqual(protocol._selected_attempt(records, evaluation, identifier)[1], [])


if __name__ == '__main__':
    unittest.main()
