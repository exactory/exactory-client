"""Post facts are sourced history, with no new commitment for a posted statement."""

import copy
from datetime import datetime, timezone
import json
from unittest.mock import patch

import test_problem_post_external as external
import test_research_problem_delivery as fixtures
from test_research_check_mode import CheckCommandMixin
from test_research_output_views import read_tree
from research_harness import cli, problem_claims, problem_delivery, problem_evidence, review_protocol
from research_harness.evidence import digest
from research_harness.output_views import render_all
from research_harness.reading import record_reading
from research_harness.report_views import next_summary, obligations_page, status_summary


class PostFactTests(CheckCommandMixin, fixtures.ProblemDeliveryCase):
    def prepare_post(self, *, gated=False, legacy=False):
        if gated:
            self.prepare_ready()
            self.statement = self.selected
            self.record_post_decision()
            exported = problem_delivery.export_problem(self.store)
            self.post = self.mutate(problem_claims.record_posted, {
                'problem_id': '656c336e-e892-4c47-80d2-a71d022f4116',
                'export_directory': exported['destination']})['result']
        else:
            self.initialize_problem()
            self.statement = self.select_statement()
            payload = dict(id='external-post', problem_id='656c336e-e892-4c47-80d2-a71d022f4116',
                           statement_digest=problem_claims.compute_statement_digest(self.statement),
                           reason='The owner requested publication.', evidence='The publication instruction.')
            if legacy:
                _, receipt = external.record_legacy_post(self, payload)
                self.post = receipt['result']
            else:
                self.post = self.mutate(problem_claims.record_post_external, payload)['result']
        (self.root / 'reply.txt').write_text('The reply identifies an earlier statement of this bound.\n')
        source = self.mutate(cli.OPERATIONS['artifact'], {
            'id': 'reply', 'path': 'reply.txt', 'media_type': 'text/plain'})['result']['artifact']
        self.payload = dict(id='fact-1', post_id=self.post['id'],
                            fact='The same bound was stated in an earlier paper.', source=source,
                            date='unknown', bears_on=dict(value='earlier_statement',
                            description='An earlier statement of the same mathematical content.'))

    def record_fact(self, payload=None, *, request='post-fact', revision=None):
        return cli.OPERATIONS['problem-post-fact'](
            self.store, self.payload if payload is None else payload,
            expected_revision=self.store.revision if revision is None else revision, request_id=request)

    def read_record_bytes(self):
        with self.store._connection() as connection:
            return {(kind, key): value for kind, key, value in connection.execute(
                'SELECT kind, key, value FROM records')}

    def assert_fact_only_changes_history(self):
        render_all(self.store)
        records = self.store.snapshot()['records']
        original_bytes = self.read_record_bytes()
        before = problem_delivery.assess_post(records, self.artifacts)
        binding = problem_evidence.describe_binding(records, self.artifacts)
        projection = problem_evidence.describe_evidence(records, self.artifacts)
        requests = {key: review_protocol.build_assignment_request(records, self.artifacts, row)
                    for key, row in records.get('review_assignment', {}).items()}
        views = read_tree(self.root)
        saved = self.record_fact()['result']
        self.assertEqual(saved, dict(self.payload, recorded_revision=self.store.revision))
        after_records = self.store.snapshot()['records']
        after_bytes = self.read_record_bytes()
        self.assertEqual({key: after_bytes[key] for key in original_bytes}, original_bytes)
        self.assertEqual(problem_evidence.describe_binding(after_records, self.artifacts), binding)
        self.assertEqual(problem_evidence.describe_evidence(after_records, self.artifacts), projection)
        if records.get('problem_value'):
            problem_evidence.validate_value(after_records, self.artifacts, problem_claims.find_value(after_records))
        self.assertEqual({key: review_protocol.build_assignment_request(after_records, self.artifacts, row)
                          for key, row in after_records.get('review_assignment', {}).items()}, requests)
        after = problem_delivery.assess_post(after_records, self.artifacts)
        self.assertEqual({key: value for key, value in after.items() if key != 'after_post'},
                         {key: value for key, value in before.items() if key != 'after_post'})
        self.assertEqual(after['after_post'], before['after_post'] + [dict(
            saved, code='problem_post_fact', explanation=saved['fact'], reason=saved['fact'])])
        report = cli.status_report(self.store)
        self.assertEqual(report['next']['code'], 'problem_already_posted')
        self.assertEqual(report['milestone'], 'post')
        self.assertFalse(report['ready'])
        self.assertEqual(report['obligations'], before['obligations'])
        render_all(self.store)
        rendered = read_tree(self.root)
        self.assertEqual({path: rendered[path] for path in views if not path.endswith('/index.json')},
                         {path: content for path, content in views.items() if not path.endswith('/index.json')})
        path = 'final/problem/post-facts/fact-1.json'
        self.assertEqual(json.loads(rendered[path]), saved)
        before_index = json.loads(views['final/index.json'])
        after_index = json.loads(rendered['final/index.json'])
        entry = dict(kind='problem_post_fact', id='fact-1', recorded_revision=saved['recorded_revision'],
                     status='current', path='problem/post-facts/fact-1.json', type='view')
        self.assertIn(entry, after_index['files'])
        after_index['files'].remove(entry)
        self.assertEqual(after_index, before_index)

    def test_accepts_fact_for_gated_post_without_changing_stored_evidence_or_gate(self):
        self.prepare_post(gated=True)
        self.assert_fact_only_changes_history()

    def test_accepts_fact_for_external_post_without_any_value_record(self):
        self.prepare_post()
        self.assertFalse(self.store.snapshot()['records'].get('problem_value'))
        self.assert_fact_only_changes_history()
        self.assertFalse(self.store.snapshot()['records'].get('problem_value'))

    def test_accepts_fact_for_legacy_external_post_without_rewriting_the_post_or_receipt(self):
        self.prepare_post(legacy=True)
        receipt = self.store.committed_request('legacy-post-request')
        self.assert_fact_only_changes_history()
        self.assertEqual(self.store.committed_request('legacy-post-request'), receipt)

    def test_accepts_reading_of_this_store_as_source_without_requiring_fulltext(self):
        self.prepare_post()
        version = self.metadata()
        self.mutate(record_reading, self.abstract_note(version))
        reading = self.store.snapshot()['records']['reading']['abstract']
        source = dict(kind='record', record_kind='reading', id=reading['id'], digest=digest(reading))
        saved = self.record_fact(dict(self.payload, source=source, date='Spring 1997'))['result']
        self.assertEqual(saved['source'], source)
        self.assertEqual(saved['date'], 'Spring 1997')

    def test_lists_each_fact_with_its_id_and_open_bearing_in_recording_order(self):
        self.prepare_post()
        saved = []
        for number, bearing in enumerate(('earlier_statement', 'alternative', 'relation', 'refutation', 'proof_gap')):
            payload = dict(self.payload, id='fact-' + str(5 - number), date='1997-03-04',
                           bears_on=dict(value=bearing, description='The reported finding concerns ' + bearing + '.'))
            saved.append(self.record_fact(payload, request='fact-request-' + str(number))['result'])
        report = cli.status_report(self.store)
        facts = [row for row in report['after_post'] if row['code'] == 'problem_post_fact']
        self.assertEqual([row['id'] for row in facts], [row['id'] for row in saved])
        self.assertEqual([row['bears_on'] for row in facts], [row['bears_on'] for row in saved])
        self.assertTrue(all(row['post_id'] == self.post['id'] and 'next' not in row for row in facts))
        for summary in (next_summary(report), status_summary(report)):
            self.assertEqual(summary['after_post'], len(report['after_post']))
        self.assertEqual(obligations_page(report, 'problem_post_fact', limit=10, cursor=None)['total'], 0)

    def test_binds_an_older_post_by_id_and_follows_its_post_in_views_and_status(self):
        self.prepare_post()
        original = copy.deepcopy(self.statement)
        self.select_statement(identifier='new-statement', statement='A different synthetic bound.')
        saved = self.record_fact()['result']
        self.assertEqual(saved['post_id'], self.post['id'])
        self.assertNotIn('after_post', cli.status_report(self.store))
        render_all(self.store)
        historical = self.root / 'final/history/problem/post-facts/fact-1.json'
        self.assertEqual(json.loads(historical.read_text()), saved)
        original.pop('recorded_revision')
        self.mutate(problem_claims.record_statement, original)
        report = cli.status_report(self.store)
        self.assertEqual(report['after_post'][-1]['id'], saved['id'])
        render_all(self.store)
        self.assertFalse(historical.exists())
        self.assertEqual(json.loads((self.root / 'final/problem/post-facts/fact-1.json').read_text()), saved)

    def test_check_has_no_writes_and_cli_mutation_renders_the_fact(self):
        self.prepare_post()
        render_all(self.store)
        before = self.read_workspace()
        checked = self.invoke_command('problem-post-fact', self.payload)
        self.assert_accepted_check(checked, self.store.revision)
        self.assertEqual(self.read_workspace(), before)
        written = self.invoke_command('problem-post-fact', self.payload, check=False)
        self.assertEqual(written[0], 0, written[2])
        saved = json.loads(written[1])['result']
        self.assertEqual(json.loads((self.root / 'final/problem/post-facts/fact-1.json').read_text()), saved)

    def test_missing_post_and_missing_source_have_refusal_parity_without_writes(self):
        self.prepare_post()
        self.assert_refusal_parity('problem-post-fact', dict(self.payload, post_id='missing'), 'problem_post_missing')
        source = dict(kind='record', record_kind='reading', id='missing', digest='0' * 64)
        self.assert_refusal_parity('problem-post-fact', dict(self.payload, source=source), 'strategy_reference_missing')
        (self.root / self.payload['source']['path']).unlink()
        self.assert_refusal_parity('problem-post-fact', self.payload, 'artifact_missing')

    def test_rejects_a_reading_from_another_store_or_a_stale_reading_reference(self):
        self.prepare_post()
        source = dict(kind='record', record_kind='reading', id='read-elsewhere', digest='0' * 64)
        self.assert_refusal_parity('problem-post-fact', dict(self.payload, source=source), 'strategy_reference_missing')
        version = self.metadata()
        reading = self.mutate(record_reading, self.abstract_note(version))['result']
        source['id'] = reading['id']
        self.assert_refusal_parity('problem-post-fact', dict(self.payload, source=source), 'strategy_evidence_stale')

    def test_rejects_empty_text_missing_fields_and_sources_other_than_artifacts_or_readings(self):
        self.prepare_post()
        for field in ('id', 'post_id', 'fact', 'date'):
            for value in ('', ' \n', None, 3):
                with self.subTest(field=field, value=value):
                    self.assert_refusal_parity('problem-post-fact', dict(self.payload, **{field: value}), 'invalid_input')
        for value in (None, {}, [], 'relation', dict(value='relation'), dict(value='', description='Relation'),
                      dict(value='relation', description=' \n'), dict(value='relation', description=4),
                      dict(value='relation', description='Relation', extra=True)):
            with self.subTest(bears_on=value):
                self.assert_refusal_parity('problem-post-fact', dict(self.payload, bears_on=value), 'invalid_input')
        for value in ('Unstored reply', None, [], {}, dict(kind='record', record_kind='problem_post', id=self.post['id']),
                      dict(kind='source', link={})):
            with self.subTest(source=value):
                self.assert_refusal_parity('problem-post-fact', dict(self.payload, source=value), 'invalid_input')
        for field in self.payload:
            payload = dict(self.payload)
            payload.pop(field)
            self.assert_refusal_parity('problem-post-fact', payload, 'invalid_input')
        self.assert_refusal_parity('problem-post-fact', dict(self.payload, extra=True), 'invalid_input')

    def test_refuses_outside_a_problem_record(self):
        payload = dict(id='fact', post_id='post', fact='A reported finding.', source={}, date='unknown',
                       bears_on=dict(value='relation', description='A relation.'))
        self.assert_refusal_parity('problem-post-fact', payload, 'orchestration_conflict')

    def test_keeps_fact_immutable_and_replays_before_source_or_current_statement_validation(self):
        self.prepare_post()
        revision = self.store.revision
        receipt = self.record_fact()
        self.assert_refusal_parity('problem-post-fact', dict(self.payload, fact='A different finding.'),
                                  'record_conflict', request='different-fact')
        self.assertEqual(self.record_fact(request='same-fact-again')['result'], receipt['result'])
        self.select_statement(identifier='later', statement='A different bound.')
        (self.root / self.payload['source']['path']).unlink()
        before = self.read_workspace()
        self.assertEqual(self.record_fact(revision=revision), receipt)
        checked = self.invoke_command('problem-post-fact', self.payload, revision=revision, request='post-fact')
        self.assertEqual(json.loads(checked[1]), receipt)
        self.assertEqual(self.read_workspace(), before)
        self.assert_refusal_parity('problem-post-fact', dict(self.payload, fact='Changed replay.'),
                                  'request_id_conflict', revision=revision, request='post-fact')

    def test_example_has_the_exact_payload_fields_and_a_reading_reference(self):
        payload = cli.run(cli.build_parser().parse_args(['example', 'problem-post-fact']))
        self.assertEqual(set(payload), {'id', 'post_id', 'fact', 'source', 'date', 'bears_on'})
        self.assertEqual(set(payload['bears_on']), {'value', 'description'})
        self.assertEqual(payload['source']['record_kind'], 'reading')


class StoredPostCompatibilityTests(CheckCommandMixin, fixtures.ProblemDeliveryCase):
    def read_legacy_outputs(self, gate):
        fixed_time = datetime(2026, 11, 9, 12, 0, tzinfo=timezone.utc)
        with patch.object(problem_claims, 'datetime') as clock:
            clock.now.return_value = fixed_time
            self.initialize_problem()
            statement = self.select_statement()
        post = dict(id='stored-post', problem_id='656c336e-e892-4c47-80d2-a71d022f4116',
                    statement_id=statement['id'], statement_digest=problem_claims.compute_statement_digest(statement),
                    posted_at=fixed_time.isoformat(), recorded_revision=self.store.revision + 1)
        if gate == 'legacy_bypassed':
            post.update(gate='bypassed', reason='An externally recorded post.', evidence='The owner instruction.')
        elif gate == 'bypassed':
            post.update(gate='bypassed', reason='An externally recorded post.', evidence='The owner instruction.',
                        recorded_at=fixed_time.isoformat(), posted_at=None)
        else:
            post.update(problem_evidence_digest='a' * 64, export_digest='b' * 64)
        self.put('problem_post', post['id'], post)
        render_all(self.store)
        with patch.object(self.store, 'describe_runtime', return_value={'fixture': 'runtime'}):
            report = cli.status_report(self.store)
        return dict(status=report, next=next_summary(report),
                    obligations=obligations_page(report, 'problem_already_posted', limit=10, cursor=None),
                    views={path: content.decode() for path, content in read_tree(self.root).items()})

    def test_stored_gated_post_keeps_0820_status_next_obligations_and_view_bytes(self):
        # Captured with the problem_delivery and output_views modules of 0.82.0 (645a791).
        self.assertEqual(digest(self.read_legacy_outputs('passed')),
                         '44445551f9209ed7cec0ecdc2342ea77af49d07bcdad4251fe14d1e09d2f6407')

    def test_stored_external_post_keeps_0820_status_next_obligations_and_view_bytes(self):
        self.assertEqual(digest(self.read_legacy_outputs('bypassed')),
                         '354a3c3b9519f3da7e0d21b3c46c505e00021ad21e5a8ee4e9dee66c7c1bfd7b')

    def test_stored_legacy_external_post_keeps_0820_status_next_obligations_and_view_bytes(self):
        self.assertEqual(digest(self.read_legacy_outputs('legacy_bypassed')),
                         'aef49d8896977dc83e494cc11a9272abc62afe77393e9583265c07b1480e744b')
