"""Append-only time statements project old external posts without rewriting them."""

import copy
from datetime import datetime, timezone
import json
from unittest.mock import patch

import test_problem_post_external as external
import test_research_problem_delivery as fixtures
from test_research_check_mode import CheckCommandMixin
from research_harness import cli, problem_claims, problem_delivery, review_protocol
from research_harness.output_views import render_all


class PostTimeTests(CheckCommandMixin, fixtures.ProblemDeliveryCase):
    def prepare_post(self, *, ready=False):
        if ready:
            self.prepare_ready()
            self.statement = self.selected
        else:
            self.initialize_problem()
            self.statement = self.select_statement()
        self.post_payload = {
            'id': 'old-external-post', 'problem_id': '656c336e-e892-4c47-80d2-a71d022f4116',
            'statement_digest': problem_claims.compute_statement_digest(self.statement),
            'reason': 'The owner authorized publication.', 'evidence': 'The publication instruction.'}
        self.old_revision, self.old_receipt = external.record_legacy_post(self, self.post_payload)
        response = self.artifacts.put(json.dumps({'createdAt': '2026-11-04T14:49:22.236Z',
                                                 'id': self.post_payload['problem_id']}).encode(), 'application/json')
        self.payload = dict(post_id=self.post_payload['id'], posted_at='2026-11-04T14:49:22.236Z', evidence=response)

    def record_time(self, payload=None, request='time-request', revision=None):
        self.assertIn('problem-post-time', cli.OPERATIONS)
        return cli.OPERATIONS['problem-post-time'](
            self.store, self.payload if payload is None else payload,
            expected_revision=self.store.revision if revision is None else revision, request_id=request)

    def read_original_bytes(self):
        with self.store._connection() as connection:
            post = connection.execute("SELECT value FROM records WHERE kind = 'problem_post' AND key = ?",
                                      (self.post_payload['id'],)).fetchone()[0]
            receipt = connection.execute("SELECT response FROM receipts WHERE request_id = ?",
                                         ('legacy-post-request',)).fetchone()[0]
        return post.encode('utf-8'), receipt.encode('utf-8')

    def test_server_response_time_statement_projects_old_post_and_exports_separately(self):
        self.prepare_post()
        self.assertIsNone(cli.status_report(self.store)['post']['posted_at'])
        render_all(self.store)
        raw = {path: path.read_bytes() for path in (self.root / 'final/problem/posts').rglob('*.json')}
        old = self.store.snapshot()['records']['problem_post']
        receipt = self.store.committed_request('legacy-post-request')
        original_bytes = self.read_original_bytes()
        revision = self.store.revision
        now = datetime(2026, 11, 6, 8, 0, tzinfo=timezone.utc)
        with patch.object(problem_claims, 'datetime') as clock:
            clock.now.return_value = now
            statement = self.record_time()['result']
        self.assertEqual(statement, dict(self.payload, recorded_at=now.isoformat(), recorded_revision=revision + 1))
        shown = cli.status_report(self.store)['post']
        self.assertEqual(shown, dict(self.old_receipt['result'], posted_at=self.payload['posted_at'],
                                    recorded_at=self.old_receipt['result']['posted_at'], time_statement=statement))
        self.assertEqual(self.store.snapshot()['records']['problem_post'], old)
        self.assertEqual(self.store.committed_request('legacy-post-request'), receipt)
        self.assertEqual(self.read_original_bytes(), original_bytes)
        render_all(self.store)
        self.assertEqual({path: path.read_bytes() for path in raw}, raw)
        views = list((self.root / 'final/problem/post-times').rglob('*.json'))
        self.assertEqual(len(views), 1)
        self.assertEqual(json.loads(views[0].read_text()), statement)

    def test_accepted_check_and_text_evidence_have_parity_without_writes(self):
        self.prepare_post()
        self.assertIn('problem-post-time', cli.OPERATIONS)
        payload = dict(self.payload, evidence='The saved server response gives this timestamp.',
                       posted_at='2099-11-04T22:49:22.236123+08:00')
        render_all(self.store)
        before = self.read_workspace()
        self.assert_accepted_check(self.invoke_command('problem-post-time', payload), self.store.revision)
        self.assertEqual(self.read_workspace(), before)
        written = self.invoke_command('problem-post-time', payload, check=False, request='time-request')
        self.assertEqual(written[0], 0, written[2])
        saved = json.loads(written[1])['result']
        self.assertTrue(list((self.root / 'final/problem/post-times').rglob('*.json')))
        self.assertEqual(saved['posted_at'], payload['posted_at'])
        self.assertEqual(saved['evidence'], payload['evidence'])

    def test_replay_precedes_eligibility_after_current_statement_changes(self):
        self.prepare_post()
        revision = self.store.revision
        original = self.record_time()
        self.select_statement(identifier='new-statement', statement='A different synthetic bound.')
        render_all(self.store)
        before = self.read_workspace()
        self.assertEqual(self.record_time(copy.deepcopy(self.payload), revision=revision), original)
        checked = self.invoke_command('problem-post-time', self.payload, revision=revision, request='time-request')
        self.assertEqual(json.loads(checked[1]), original)
        self.assertEqual(self.read_workspace(), before)
        self.assert_refusal_parity('problem-post-time', dict(self.payload, posted_at='2026-11-04T00:00:00Z'),
                                  'request_id_conflict', revision=revision, request='time-request')

    def test_old_post_is_found_by_id_after_current_statement_changes(self):
        self.prepare_post()
        self.select_statement(identifier='new-statement', statement='A different synthetic bound.')
        self.assertEqual(self.record_time()['result']['post_id'], self.post_payload['id'])

    def test_a_workspace_without_a_problem_record_is_refused_without_writes(self):
        self.assertIn('problem-post-time', cli.OPERATIONS)
        self.put('workspace', 'study', {'stage': 'initiate'})
        payload = dict(post_id='external-post-1', posted_at='2026-11-04T14:49:22.236Z',
                       evidence='The saved server response states this creation time.')
        render_all(self.store)
        before = self.read_workspace()
        self.assert_refusal_parity('problem-post-time', payload, 'orchestration_conflict')
        self.assertEqual(self.read_workspace(), before)

    def test_missing_post_and_second_statement_are_refused_without_writes(self):
        self.prepare_post()
        self.assertIn('problem-post-time', cli.OPERATIONS)
        self.assert_refusal_parity('problem-post-time', dict(self.payload, post_id='missing'), 'problem_post_missing')
        self.record_time()
        render_all(self.store)
        before = self.read_workspace()
        self.assert_refusal_parity('problem-post-time', self.payload, 'problem_post_time_already_recorded', request='second')
        self.assertEqual(self.read_workspace(), before)

    def test_passed_internal_and_new_external_posts_are_inapplicable(self):
        self.prepare_post()
        self.assertIn('problem-post-time', cli.OPERATIONS)
        for gate, recorded in (('passed', False), (None, False), ('bypassed', True)):
            post = dict(self.old_receipt['result'])
            if gate is None:
                post.pop('gate')
            else:
                post['gate'] = gate
            if recorded:
                post['recorded_at'] = post['posted_at']
            self.put('problem_post', post['id'], post)
            with self.subTest(gate=gate, recorded=recorded):
                render_all(self.store)
                before = self.read_workspace()
                self.assert_refusal_parity('problem-post-time', self.payload, 'problem_post_time_inapplicable')
                checked = self.invoke_command('problem-post-time', self.payload)
                self.assertEqual(json.loads(checked[2])['error']['details']['gate'], gate)
                self.assertEqual(self.read_workspace(), before)

    def test_malformed_fields_and_artifact_evidence_are_refused(self):
        self.prepare_post()
        self.assertIn('problem-post-time', cli.OPERATIONS)
        invalid = {'post_id': ('', None, 5),
                   'posted_at': ('2026-11-04T14:49:22', '2026-11-04', 'nonsense', None, 7),
                   'evidence': ('', ' \n', None, [], {}, {'kind': 'record', 'id': 'response'})}
        for field, values in invalid.items():
            for value in values:
                with self.subTest(field=field, value=value):
                    render_all(self.store)
                    before = self.read_workspace()
                    self.assert_refusal_parity('problem-post-time', dict(self.payload, **{field: value}), 'invalid_input')
                    self.assertEqual(self.read_workspace(), before)
        for field in self.payload:
            value = dict(self.payload)
            value.pop(field)
            self.assert_refusal_parity('problem-post-time', value, 'invalid_input')
        self.assert_refusal_parity('problem-post-time', dict(self.payload, extra=True), 'invalid_input')
        (self.root / self.payload['evidence']['path']).unlink()
        self.assert_refusal_parity('problem-post-time', self.payload, 'artifact_missing')

    def test_statement_changes_neither_scientific_digest_gate_nor_rebuilt_requests(self):
        self.prepare_post(ready=True)
        records = self.store.snapshot()['records']
        evidence_digest = problem_claims.compute_problem_evidence_digest(records, self.artifacts)
        gate = problem_delivery.assess_post(records, self.artifacts)
        requests = {key: review_protocol.build_assignment_request(records, self.artifacts, row)
                    for key, row in records['review_assignment'].items()}
        self.record_time()
        after = self.store.snapshot()['records']
        self.assertEqual(problem_claims.compute_problem_evidence_digest(after, self.artifacts), evidence_digest)
        after_gate = problem_delivery.assess_post(after, self.artifacts)
        self.assertEqual({key: value for key, value in after_gate.items() if key != 'post'},
                         {key: value for key, value in gate.items() if key != 'post'})
        self.assertEqual({key: review_protocol.build_assignment_request(after, self.artifacts, row)
                          for key, row in after['review_assignment'].items()}, requests)

    def test_cli_example_has_exact_payload_fields(self):
        self.assertIn('problem-post-time', cli.OPERATIONS)
        value = cli.run(cli.build_parser().parse_args(['example', 'problem-post-time']))
        self.assertEqual(set(value), {'post_id', 'posted_at', 'evidence'})
