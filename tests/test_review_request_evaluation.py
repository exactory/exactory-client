"""Derived requests share work without sharing trust or mutable values."""

import copy
import json
import random
import unittest
from unittest.mock import patch

from literature_fixtures import LiteratureCase
from problem_parts_fixtures import ResultPartsCase
from research_harness import review_protocol as protocol, review_transport as transport
from research_harness import cli, review_request_evaluation as request_evaluation
from research_harness.errors import ResearchError
from research_harness.evaluation import Evaluation, _weigh_value
from research_harness.evidence import digest
from research_harness.review_bodies import write_history
from review_request_builder_fixtures import use_emulated_request_builders


class RequestReuseTests(ResultPartsCase):
    def setUp(self):
        super().setUp()
        self.records = self.case.read_records()
        self.assignment = self.records['review_assignment']['bar-first']
        self.attempt = self.records['review_attempt']['attempt-bar-first']

    def exercise(self, evaluation, assignment=None, attempt=None):
        assignment, attempt = assignment or self.assignment, attempt or self.attempt
        first = protocol.build_assignment_request(self.records, evaluation, assignment)
        self.assertTrue(protocol.authenticate_provenance(self.records, evaluation, assignment, attempt))
        self.assertEqual(protocol.build_assignment_request(self.records, evaluation, assignment), first)
        self.assertTrue(protocol.authenticate_provenance(self.records, evaluation, assignment, attempt))
        return first

    def test_build_authenticate_build_authenticate_derives_once(self):
        evaluation = Evaluation(self.records, self.case.artifacts)
        with patch.object(request_evaluation, '_build_request', wraps=request_evaluation._build_request) as derive:
            self.exercise(evaluation)
        self.assertEqual(derive.call_count, 1)

    def test_four_parameter_request_body_supports_build_and_authentication(self):
        original = transport.request_body

        def request_body(route, prompt, packet, history=()):
            return original(route, prompt, packet, history)

        route = self.records['review_route'][self.assignment['route_id']]
        packet = protocol._read_json(self.case.artifacts, self.assignment['packet'])
        history = protocol.read_history(self.case.artifacts, self.assignment)
        expected = protocol._read_json(self.case.artifacts, self.attempt['request'])
        with patch.object(transport, 'request_body', side_effect=request_body):
            self.assertEqual(next(transport.list_built_requests(
                route, self.assignment['prompt']['text'], packet, history)), expected)
            for authenticate_first in (False, True):
                with self.subTest(authenticate_first=authenticate_first):
                    evaluation = Evaluation(self.records, self.case.artifacts)
                    if authenticate_first:
                        self.assertTrue(protocol.authenticate_provenance(
                            self.records, evaluation, self.assignment, self.attempt))
                    self.assertEqual(self.exercise(evaluation), expected)

    def test_independent_evaluations_derive_identical_inputs_twice(self):
        with patch.object(request_evaluation, '_build_request', wraps=request_evaluation._build_request) as derive:
            first = self.exercise(Evaluation(self.records, self.case.artifacts))
            self.assertEqual(derive.call_count, 1)
            second = self.exercise(Evaluation(self.records, self.case.artifacts))
        self.assertEqual(second, first)
        self.assertEqual(derive.call_count, 2)

    def test_request_survives_streaming_more_than_the_total_budget(self):
        evaluation = Evaluation(self.records, self.case.artifacts, retention_budget_bytes=64000)
        with patch.object(request_evaluation, '_build_request', wraps=request_evaluation._build_request) as derive:
            expected = self.exercise(evaluation)
            for index in range(6):
                evaluation.read(self.case.artifacts.put(bytes([index]) * 40000, 'text/plain'))
            self.assertEqual(self.exercise(evaluation), expected)
        self.assertEqual(derive.call_count, 1)
        self.assertLessEqual(evaluation.retention_report()['optional_bytes'], 64000)

    def test_large_assignment_working_set_derives_each_input_once(self):
        evaluation = Evaluation(self.records, self.case.artifacts, retention_budget_bytes=256000)
        pairs = []
        for index in range(12):
            packet = protocol._read_json(self.case.artifacts, self.assignment['packet'])
            packet.update(index=index, padding='large repeated evidence ' * 8000)
            assignment = dict(self.assignment, id='large-' + str(index),
                              packet=protocol._put_json(self.case.artifacts, packet), packet_digest=digest(packet))
            request = transport.request_body(self.records['review_route'][assignment['route_id']],
                                            assignment['prompt']['text'], packet)
            attempt = dict(self.attempt, id='attempt-' + assignment['id'], assignment_id=assignment['id'], packet_digest=digest(packet),
                           request=protocol._put_json(self.case.artifacts, request), request_digest=digest(request))
            pairs.append((assignment, attempt))
        self.records['review_assignment'] = {assignment['id']: assignment for assignment, _ in pairs}
        self.records['review_attempt'] = {attempt['id']: attempt for _, attempt in pairs}
        with patch.object(request_evaluation, '_build_request', wraps=request_evaluation._build_request) as derive:
            for _ in range(2):
                for assignment, attempt in pairs:
                    self.exercise(evaluation, assignment, attempt)
        self.assertEqual(derive.call_count, len(pairs))
        self.assertLessEqual(evaluation.retention_report()['optional_bytes'], 256000)

    def test_returned_mutation_does_not_change_authentication(self):
        evaluation = Evaluation(self.records, self.case.artifacts)
        first = self.exercise(evaluation)
        expected = copy.deepcopy(first)
        first['input'][-1]['content'] = 'Caller substitution'
        first['text']['format']['type'] = 'Caller substitution'
        self.assertEqual(self.exercise(evaluation), expected)

    def test_evicted_packet_or_history_is_verified_before_reusing_request(self):
        for kind in ('packet', 'history'):
            with self.subTest(kind=kind):
                assignment = copy.deepcopy(self.assignment)
                if kind == 'history':
                    assignment.pop('history', None)
                    assignment['history_ref'] = write_history(self.case.artifacts, assignment,
                        [{'role': 'user', 'content': 'A verified history body.'}])
                    page = protocol._read_json(self.case.artifacts, assignment['history_ref']['pages'][0])
                    reference = page['messages'][0]['body']
                else:
                    packet = protocol._read_json(self.case.artifacts, assignment['packet'])
                    reference = assignment['packet'] = protocol._put_json(self.case.artifacts, dict(packet, unique=kind))
                evaluation = Evaluation(self.records, self.case.artifacts, retention_budget_bytes=64000)
                protocol.build_assignment_request(self.records, evaluation, assignment)
                for index in range(12):
                    evaluation.read(self.case.artifacts.put(bytes([index]) * 8000, 'text/plain'))
                artifact_path = self.case.root / reference['path']
                original = artifact_path.read_bytes()
                artifact_path.chmod(0o600)
                artifact_path.write_bytes(b'corrupted')
                try:
                    for reader in (evaluation, Evaluation(self.records, self.case.artifacts)):
                        with self.assertRaises(ResearchError) as failure:
                            protocol.build_assignment_request(self.records, reader, assignment)
                        self.assertEqual(failure.exception.code, 'artifact_corrupt')
                finally:
                    artifact_path.write_bytes(original)

    def test_changed_history_root_is_rejected_before_a_warm_lookup(self):
        assignment = copy.deepcopy(self.assignment)
        assignment.pop('history', None)
        assignment['history_ref'] = write_history(self.case.artifacts, assignment,
            [{'role': 'user', 'content': 'A verified history body.'}])
        evaluation = Evaluation(self.records, self.case.artifacts)
        protocol.build_assignment_request(self.records, evaluation, assignment)
        assignment['history_ref']['history_digest'] = '0' * 64
        with self.assertRaises(ResearchError) as failure:
            protocol.build_assignment_request(self.records, evaluation, assignment)
        self.assertEqual(failure.exception.code, 'review_history_invalid')

    def test_forged_requests_and_provenance_get_no_credit_from_warm_memo(self):
        evaluation = Evaluation(self.records, self.case.artifacts)
        request = self.exercise(evaluation)
        forged = copy.deepcopy(request)
        forged['instructions'] = 'An author-supplied prompt'
        changes = dict(request=protocol._put_json(self.case.artifacts, forged), origin='imported',
                       runtime={}, route_digest='0' * 64, packet_digest='0' * 64, prompt_hash='0' * 64)
        for name, value in changes.items():
            with self.subTest(field=name):
                attempt = dict(self.attempt, **{name: value})
                self.assertFalse(protocol.authenticate_provenance(self.records, evaluation, self.assignment, attempt))
        self.assertTrue(protocol.authenticate_provenance(self.records, evaluation, self.assignment, self.attempt))

    def test_value_review_preparation_shares_one_evaluation(self):
        from codex_route_fixtures import invoke_codex, record_codex_route
        from research_harness import research_decisions as decisions
        from test_research_problem_reviews import ProblemReviewTests
        fixture = ProblemReviewTests()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        fixture.supporting = fixture.artifacts.put(b'A captured field standard.', 'text/plain')
        record_codex_route(fixture)
        assignment = fixture.assign('bar', 'first', **fixture.deliver_supporting('first'))
        output = fixture.build_review_output('bar')
        invoke_codex(fixture, assignment['id'], output)
        observed = []
        def observe(function):
            def call(records, artifacts, *args, **kwargs):
                observed.append(artifacts)
                return function(records, artifacts, *args, **kwargs)
            return call
        with patch.object(protocol, 'validate_assignment_inventories',
                          side_effect=observe(protocol.validate_assignment_inventories)), \
                patch.object(decisions, 'validate_review_contract', side_effect=observe(decisions.validate_review_contract)):
            fixture.mutate(decisions.record_value_review, dict(output, id='review-' + assignment['id'],
                             dossier_id=None, assignment_id=assignment['id']))
        self.assertGreater(len(observed), 1)
        self.assertTrue(all(isinstance(item, Evaluation) for item in observed))
        self.assertEqual(len({id(item._requests) for item in observed}), 1)

    def test_refusal_checks_share_one_evaluation(self):
        from research_harness import review_refusal as refusals
        observed = []
        def observe(function):
            def call(records, artifacts, *args, **kwargs):
                observed.append(artifacts)
                return function(records, artifacts, *args, **kwargs)
            return call
        with patch.object(protocol, 'assignment_state', side_effect=observe(protocol.assignment_state)), \
                patch.object(refusals, 'classify_observed_output', side_effect=observe(refusals.classify_observed_output)):
            refusals._record_refusal(self.case.store, {'assignment_id': self.assignment['id']},
                                    expected_revision=self.case.store.revision, request_id='check-refusal')
        self.assertGreater(len(observed), 1)
        self.assertTrue(all(isinstance(item, Evaluation) for item in observed))
        self.assertEqual(len({id(item._requests) for item in observed}), 1)

    def test_problem_status_exposes_requested_evaluation_diagnostics(self):
        plain = cli.status_report(self.case.store)
        counted = cli.status_report(self.case.store, counters=True)
        diagnostics = counted.pop('evaluation')
        self.assertEqual(counted, plain)
        self.assertIn('elapsed_seconds', diagnostics)
        self.assertIn('request_bytes', diagnostics['retention'])


class RequestIteratorTests(LiteratureCase):
    def setUp(self):
        super().setUp()
        use_emulated_request_builders(self)
        self.route = dict(adapter=transport.ADAPTER, endpoint='https://example.org/v1/responses', model='fixture',
                          configuration=dict(max_output_tokens=1000, timeout_seconds=5), runtime={'version': 1})
        self.packet = {'evidence': [], 'value': 'original'}
        self.evaluation = Evaluation({}, self.artifacts)

    def derive(self, **changes):
        from research_harness.review_request_evaluation import request_for_order
        values = dict(route=self.route, prompt='Read this packet', packet=self.packet,
                      history=[], order=transport.FIT_ORDER)
        values.update(changes)
        return request_for_order(self.evaluation, **values)

    def test_every_actual_input_changes_the_key(self):
        with patch.object(transport, 'request_body', wraps=transport.request_body) as derive:
            self.derive()
            self.derive()
            self.assertEqual(derive.call_count, 1)
            changes = [dict(route=dict(self.route, runtime={'version': 2})),
                       dict(prompt='Changed prompt'), dict(packet=dict(self.packet, value='changed')),
                       dict(history=[{'role': 'user', 'content': 'Earlier input'}]),
                       dict(order=transport.EARLIER_FIT_ORDERS[0])]
            for index, change in enumerate(changes, 2):
                self.derive(**change)
                self.assertEqual(derive.call_count, index)
            with patch.object(transport, 'local_input_limit', return_value=123456):
                self.derive()
            self.assertEqual(derive.call_count, 7)

    def test_request_body_uses_pinned_arguments_on_every_derivation_path(self):
        orders = (transport.FIT_ORDER, transport.EARLIER_FIT_ORDERS[0], tuple(list(transport.FIT_ORDER)))
        for path in ('cached', 'iterator-history', 'non-native-packet'):
            for order in orders:
                with self.subTest(path=path, order=order):
                    self.evaluation = Evaluation({}, self.artifacts)
                    history = iter(()) if path == 'iterator-history' else []
                    packet = {'value': (1, 2)} if path == 'non-native-packet' else self.packet
                    with patch.object(transport, 'request_body', wraps=transport.request_body) as derive:
                        self.derive(packet=packet, history=history, order=order)
                    args = (self.route, 'Read this packet', packet, history)
                    if order is not transport.FIT_ORDER:
                        args += (order,)
                    derive.assert_called_once_with(*args)
                    self.assertIs(derive.call_args.args[-1], args[-1])

    def test_key_preserves_types_and_object_order(self):
        with patch.object(transport, 'request_body', wraps=transport.request_body) as derive:
            for packet in ({'a': 1, 'b': 2}, {'b': 2, 'a': 1}, {'a': 1.0, 'b': 2}, {'a': True, 'b': 2}):
                self.derive(packet=packet)
        self.assertEqual(derive.call_count, 4)

    def test_iterator_history_matches_pinned_requests_without_polluting_list_history(self):
        from research_harness.review_request_evaluation import iter_built_requests
        history = [{'role': 'user', 'content': 'Earlier question'}]
        expected = list(transport.list_built_requests(self.route, 'Read this packet', self.packet, iter(history)))
        self.assertEqual(expected[0]['input'][:-1], history)
        with patch.object(transport, 'request_body', wraps=transport.request_body) as derive:
            for _ in range(2):
                original = iter(history)
                actual = list(iter_built_requests(self.evaluation, self.route, 'Read this packet', self.packet, original))
                self.assertEqual(actual, expected)
                self.assertIs(derive.call_args.args[3], original)
                self.assertEqual(list(original), [])
            self.assertEqual(derive.call_count, 2)
            self.assertEqual(self.evaluation.retention_report()['request_entries'], 0)
            self.assertEqual(self.derive(history=history), expected[0])
            self.assertEqual(self.derive(history=history), expected[0])
            self.assertEqual(self.derive(history=tuple(history)), expected[0])
        self.assertEqual(derive.call_count, 3)

    def test_list_order_preserves_native_fit_after_warming_tuple_order(self):
        from test_review_html_delivery import ROUTE, entry
        body = entry('x' * (transport.CODEX_INPUT_LIMIT_CHARS + 1), 'text/plain')
        packet = {'problem_evidence': {}, 'evidence': [body],
                  'source_deliveries': [{'status': 'delivered', 'evidence': [body['artifact']]}]}
        order = list(transport.FIT_ORDER)
        expected_tuple = transport.request_body(ROUTE, 'Read this packet', packet, [], order=transport.FIT_ORDER)
        expected_list = transport.request_body(ROUTE, 'Read this packet', packet, [], order=order)
        supplied_tuple = json.loads(expected_tuple['input'][-1]['content'][len(transport.PACKET_LABEL):])
        supplied_list = json.loads(expected_list['input'][-1]['content'][len(transport.PACKET_LABEL):])
        self.assertEqual(supplied_tuple['evidence'][0]['content'], body['content'])
        self.assertEqual(supplied_list['evidence'][0]['content'], '')
        with patch.object(request_evaluation, '_build_request', wraps=request_evaluation._build_request) as derive:
            self.assertEqual(self.derive(route=ROUTE, packet=packet), expected_tuple)
            for _ in range(2):
                self.assertEqual(self.derive(route=ROUTE, packet=packet, order=order), expected_list)
                self.assertIs(derive.call_args.args[4], order)
            self.assertEqual(self.derive(route=ROUTE, packet=packet), expected_tuple)
        self.assertEqual(derive.call_count, 3)
        self.assertEqual(self.evaluation.retention_report()['request_entries'], 1)

    def test_history_and_order_subclasses_bypass_retention(self):
        class HistoryList(list):
            pass
        class Sequence(tuple):
            pass
        class Step(str):
            pass
        for changes in (dict(history=HistoryList()), dict(history=Sequence()),
                        dict(order=Sequence(transport.FIT_ORDER)),
                        dict(order=tuple(Step(step) for step in transport.FIT_ORDER))):
            with self.subTest(changes=changes):
                self.evaluation = Evaluation({}, self.artifacts)
                with patch.object(transport, 'request_body', wraps=transport.request_body) as derive:
                    first = self.derive(**changes)
                    self.assertEqual(self.derive(**changes), first)
                self.assertEqual(derive.call_count, 2)
                self.assertEqual(self.evaluation.retention_report()['request_entries'], 0)

    def test_lazy_iterator_matches_pinned_order_duplicates_and_exceptions(self):
        from research_harness.review_request_evaluation import iter_built_requests
        local = dict(self.route, adapter='codex_cli_v1')
        outcomes = [{'input': ['current']}, {'input': ['current']}, {'input': ['earlier']},
                    {'input': ['earlier']}, ResearchError('later_failure', 'A later candidate refuses')]
        for stop_after in (1, 2, None):
            traces = []
            for use_memo in (False, True):
                evaluation = Evaluation({}, self.artifacts)
                with patch.object(transport, 'request_body', side_effect=outcomes) as derive, \
                        patch.object(request_evaluation, '_build_request',
                                     side_effect=lambda route, prompt, packet, history, order:
                                     transport.request_body(route, prompt, packet, history, order)):
                    built = (iter_built_requests(evaluation, local, 'prompt', self.packet, []) if use_memo
                             else transport.list_built_requests(local, 'prompt', self.packet, []))
                    values, refusal = [], None
                    try:
                        while stop_after is None or len(values) < stop_after:
                            values.append(next(built))
                    except ResearchError as error:
                        refusal = error.as_dict()
                    traces.append((values, refusal, derive.call_count))
            self.assertEqual(traces[0], traces[1])
            self.assertEqual(traces[0][2], {1: 1, 2: 3, None: 5}[stop_after])

    def test_real_iterator_matches_pinned_requests_for_all_text_adapters(self):
        from research_harness.review_request_evaluation import iter_built_requests
        from codex_route_fixtures import record_codex_route
        from test_research_review_protocol import ReviewProtocolTests
        fixture = ReviewProtocolTests()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        record_codex_route(fixture)
        local = fixture.records()['review_route']['route']
        from research_harness import review_claude_transport as claude
        routes = [self.route, local, dict(local, adapter='claude_cli_v1', endpoint=claude.ENDPOINT,
                                        configuration=dict(local['configuration'], effort='high')),
                  dict(local, adapter='codex_cli_framed_v1')]
        for route in routes:
            with self.subTest(adapter=route['adapter']):
                self.assertEqual(list(iter_built_requests(self.evaluation, route, 'prompt', self.packet, [])),
                                 list(transport.list_built_requests(route, 'prompt', self.packet, [])))

    def test_zero_tiny_and_incompressible_requests_obey_budget_and_keep_errors(self):
        generator = random.Random(731)
        packet = {'value': ''.join(chr(generator.randrange(32, 127)) for _ in range(20000))}
        expected = transport.request_body(self.route, 'Read this packet', packet)
        for budget in (0, 1, 4000, 100000):
            with self.subTest(budget=budget):
                self.evaluation = Evaluation({}, self.artifacts, retention_budget_bytes=budget)
                with patch.object(transport, 'request_body', wraps=transport.request_body) as derive:
                    for _ in range(2):
                        self.assertEqual(self.derive(packet=packet), expected)
                    self.assertEqual(derive.call_count, 1 if budget == 100000 else 2)
                report = self.evaluation.retention_report()
                self.assertLessEqual(report['request_bytes'], budget // 2)
                self.assertLessEqual(report['optional_bytes'], budget)
                with self.assertRaises(ResearchError) as failure:
                    self.derive(route=dict(self.route, endpoint='http://example.org'))
                self.assertEqual(failure.exception.code, 'invalid_review_route')

    def test_compressed_entries_retain_no_expanded_requests_or_inputs(self):
        self.derive(packet={'value': 'Repeated evidence' * 50000})
        report = self.evaluation.retention_report()
        self.assertEqual(report['request_entries'], 1)
        self.assertLess(report['request_bytes'], 20000)
        self.assertGreaterEqual(report['request_bytes'], _weigh_value(self.evaluation._requests))
        for value, weight in self.evaluation._requests.values():
            self.assertIs(type(value), bytes)

    def test_codec_keeps_unicode_order_and_finite_native_types_exact(self):
        value = {'z': ['λ😀', None, True, 1, 1.0, -0.0], 'a': {'second': 2, 'first': 1}}
        with patch.object(transport, 'request_body', return_value=value) as derive:
            first, second = self.derive(), self.derive()
        self.assertEqual(derive.call_count, 1)
        self.assertEqual(json.dumps(first, ensure_ascii=False), json.dumps(value, ensure_ascii=False))
        self.assertEqual(json.dumps(second, ensure_ascii=False), json.dumps(value, ensure_ascii=False))
        self.assertIsNot(first, second)

    def test_codec_uses_uncached_path_for_surrogates_and_non_native_values(self):
        class Text(str):
            pass
        values = ['\ud800', '\ud83d\ude00', (1, 2), {1: 'integer key'}, float('nan'),
                  float('inf'), {'native': Text('subclass')}, {1, 2}]
        for value in values:
            with self.subTest(value=repr(value)):
                self.evaluation = Evaluation({}, self.artifacts)
                with patch.object(transport, 'request_body', return_value=value) as derive:
                    self.assertIs(self.derive(), value)
                    self.assertIs(self.derive(), value)
                self.assertEqual(derive.call_count, 2)
                self.assertEqual(self.evaluation.retention_report()['request_entries'], 0)

    def test_encoding_failure_does_not_add_a_refusal(self):
        from research_harness import review_request_evaluation as requests
        value = {'valid': ['original']}
        dumps = requests.json.dumps
        def encode(item, **kwargs):
            if item is value:
                raise UnicodeError('Cannot encode this request')
            return dumps(item, **kwargs)
        with patch.object(transport, 'request_body', return_value=value) as derive, \
                patch.object(requests.json, 'dumps', side_effect=encode):
            self.assertIs(self.derive(), value)
            self.assertIs(self.derive(), value)
        self.assertEqual(derive.call_count, 2)

    def test_unencodable_inputs_take_the_native_path(self):
        for changes in (dict(prompt='\ud800'), dict(packet={'value': (1, 2)}),
                        dict(history=[{'role': 'user', 'content': '\ud83d\ude00'}])):
            with self.subTest(changes=repr(changes)):
                with patch.object(transport, 'request_body', wraps=transport.request_body) as derive:
                    first = self.derive(**changes)
                    self.assertEqual(self.derive(**changes), first)
                self.assertEqual(derive.call_count, 2)
                self.assertEqual(self.evaluation.retention_report()['request_entries'], 0)


if __name__ == '__main__':
    unittest.main()
