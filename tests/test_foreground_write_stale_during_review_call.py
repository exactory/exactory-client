"""T7 of 20261114165500-math-conjecturer-phenomenon-run-observations.

An explicit foreground revision protects the whole store, including review
lifecycle commits. Read the new revision and retry the refused request.
"""

from concurrent.futures import ThreadPoolExecutor
import json
from threading import Event
from unittest.mock import patch

from test_research_problem_claims import ProblemCase
from research_harness import cli, review_protocol, review_transport, stop_classes
from research_harness.errors import ResearchError
from research_harness.review_task_execution import identify_call
from research_harness.storage import Store


class ForegroundWriteStaleDuringReviewCallTests(ProblemCase):
    def setUp(self):
        super().setUp()
        self.initialize_problem()
        self.select_statement()
        self.value = self.build_value([self.record_full_reading()])
        self.payload_path = self.root / 'value.json'
        self.payload_path.write_text(json.dumps(self.value), encoding='utf-8')
        self.mutate(review_protocol.record_route, dict(id='route', adapter='openai_responses_v1',
            model='fixture-model', endpoint='https://stub.invalid/v1/responses',
            configuration=dict(max_output_tokens=1000, timeout_seconds=5)))

    def invoke_value(self, revision):
        arguments = ['problem-value', '--workspace', str(self.root), '--file', str(self.payload_path),
                     '--expected-revision', str(revision), '--request-id', 'foreground-value']
        return cli.run(cli.build_parser().parse_args(arguments))

    def respond_to_probe(self, route, request, credential):
        packet = json.loads(request['input'][0]['content'].removeprefix('The packet below is JSON.\n'))
        output = dict(allowed_control=packet['allowed_control'], evidence_control=packet['evidence_control'],
                      excluded_controls=[])
        return dict(id='synthetic-response', model='fixture-model', status='completed',
            output=[dict(type='message', role='assistant', content=[
                dict(type='output_text', text=json.dumps(output))])],
            usage=dict(input_tokens=1000000, output_tokens=12))

    def assert_refusal_during_completion(self, boundary):
        review_waiting, finish_allowed = Event(), Event()
        finish_call = review_protocol._finish_call
        read_preparation, mutate = Store._read_preparation, Store.mutate

        def finish_after_signal(*args, **kwargs):
            # The real invocation and observation are committed. Only completion waits.
            review_waiting.set()
            self.assertTrue(finish_allowed.wait(30), 'Foreground write did not release review completion')
            return finish_call(*args, **kwargs)

        with patch.object(review_transport, 'send_request', side_effect=self.respond_to_probe), \
                patch.object(review_protocol, '_finish_call', side_effect=finish_after_signal), \
                ThreadPoolExecutor(max_workers=1) as executor:
            reviewer = executor.submit(review_protocol.probe_route, self.store, dict(id='probe', route_id='route'),
                expected_revision=self.store.revision, request_id='review-probe', credential='test-only')
            try:
                self.assertTrue(review_waiting.wait(30), 'Review did not reach its captured observation')
                before = self.store.snapshot()
                expected_revision = before['revision']
                lifecycle_id = identify_call('review.route_probe', 'probe')
                capture = before['records']['review_call_observation'][lifecycle_id]
                self.assertEqual(capture['recorded_revision'], expected_revision)
                self.assertNotIn('probe', before['records'].get('review_route_probe', {}))

                def complete_review():
                    finish_allowed.set()
                    reviewer.result(timeout=30)

                def read_after_completion(store, *args, **kwargs):
                    complete_review()
                    return read_preparation(store, *args, **kwargs)

                def commit_after_completion(store, operation, *args, **kwargs):
                    if operation == 'problem.value':
                        complete_review()
                    return mutate(store, operation, *args, **kwargs)

                hook = (patch.object(Store, '_read_preparation', new=read_after_completion)
                        if boundary == 'preparation' else patch.object(Store, 'mutate', new=commit_after_completion))
                with hook, self.assertRaises(ResearchError) as caught:
                    self.invoke_value(expected_revision)
            finally:
                finish_allowed.set()
            review_receipt = reviewer.result(timeout=30)

        error = caught.exception
        self.assertEqual(error.code, 'stale_revision')
        self.assertEqual(error.details, dict(expected_revision=expected_revision, revision=expected_revision + 1))
        self.assertIn('read the current revision before a new mutation', error.message)
        self.assertEqual(stop_classes.STOP_REGISTRY[error.code]['next'], dict(kind='continue'))
        terminal = stop_classes.describe_terminal(error.code)
        self.assertEqual((terminal['state'], terminal['stop_class']), ('continue', 'decision'))

        after = self.store.snapshot()
        self.assertEqual(after['revision'], expected_revision + 1)
        self.assertEqual(review_receipt['revision'], after['revision'])
        self.assertEqual(review_receipt['result']['status'], 'completed')
        self.assertEqual(after['records']['review_route_probe']['probe'], review_receipt['result'])
        self.assertEqual(after['records']['review_call_observation'][lifecycle_id], capture)
        self.assertTrue(after['records']['review_pool_membership'][lifecycle_id]['released'])
        # The review changed no scientific input, but the explicit whole-store CAS still applies.
        for kind, records in before['records'].items():
            if not kind.startswith('review_') and kind != 'literature_operation':
                self.assertEqual(after['records'][kind], records)
        self.assertNotIn(self.value['id'], after['records'].get('problem_value', {}))
        self.assertNotIn('foreground-value', after['records']['literature_operation'])
        self.assertIsNone(self.store.committed_request('foreground-value'))

        # Follow the CLI's remedy with the same payload and request ID, then replay the receipt.
        receipt = self.invoke_value(self.store.revision)
        self.assertEqual(receipt['revision'], after['revision'] + 1)
        self.assertEqual(receipt['result']['recorded_revision'], receipt['revision'])
        self.assertEqual(receipt['result']['id'], self.value['id'])
        saved = self.store.snapshot()
        self.assertEqual(saved['records']['problem_value'][self.value['id']], receipt['result'])
        replay = self.invoke_value(expected_revision)
        self.assertEqual(replay, {key: receipt[key] for key in ('revision', 'request_id', 'result')})
        self.assertEqual(self.store.snapshot(), saved)

    def test_refuse_revision_read_before_review_completion_and_retry(self):
        self.assert_refusal_during_completion('preparation')

    def test_refuse_prepared_value_after_review_completion_and_retry(self):
        self.assert_refusal_during_completion('commit')
