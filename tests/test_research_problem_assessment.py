"""Self-assessments are immutable and bound to the current problem statement."""

import copy

from research_harness.evidence import digest
from research_harness.errors import ResearchError
from research_harness.problem_evidence import describe_binding, describe_evidence
from research_harness.problem_rubric import ITEMS
from test_research_problem_claims import ProblemCase
from test_research_problem_rubric import passing_grades


def build_assessment(statement, identifier='assessment-1'):
    reference = {'kind': 'record', 'record_kind': 'problem_statement',
                 'id': statement['id'], 'digest': digest(statement)}
    return {'id': identifier, 'statement_id': statement['id'], 'grades': passing_grades(),
            'items': {item: {'evidence': [copy.deepcopy(reference)],
                             'note': 'The exact statement and recorded evidence support this judgment.'}
                      for item in ITEMS}, 'primary': {'ground': 'B1', 'criterion': 'V3'}}


class ProblemAssessmentTests(ProblemCase):
    def setUp(self):
        super().setUp()
        self.initialize_problem()
        self.statement = self.select_statement()
        self.payload = build_assessment(self.statement)

    def record_assessment(self, payload=None):
        return self.mutate(self.load_problem_module().record_assessment,
                           self.payload if payload is None else payload)['result']

    def test_records_an_assessment_bound_to_the_current_statement(self):
        revision = self.store.revision
        saved = self.record_assessment()
        records = self.store.snapshot()['records']
        self.assertEqual(saved, dict(self.payload, recorded_revision=revision + 1))
        self.assertEqual(records['problem_assessment']['assessment-1'], saved)
        self.assertEqual(self.load_problem_module().find_assessment(records), saved)
        self.assertEqual(self.load_problem_module().assess_assessment(records, self.artifacts), [])

    def test_refuses_an_assessment_for_another_statement(self):
        self.payload['statement_id'] = 'absent'
        self.assert_error('problem_statement_missing', self.record_assessment)

    def test_refuses_missing_or_empty_item_evidence(self):
        for item in ITEMS:
            for variant in ('item', 'evidence', 'empty'):
                payload = copy.deepcopy(self.payload)
                if variant == 'item':
                    del payload['items'][item]
                elif variant == 'evidence':
                    del payload['items'][item]['evidence']
                else:
                    payload['items'][item]['evidence'] = []
                with self.subTest(item=item, variant=variant):
                    with self.assertRaises(ResearchError) as caught:
                        self.record_assessment(payload)
                    self.assertEqual(caught.exception.code, 'problem_assessment_evidence_missing')
                    self.assertEqual(caught.exception.details, {'item': item})

    def test_refuses_a_primary_ground_graded_zero(self):
        self.payload['primary']['ground'] = 'B2'
        self.assert_error('invalid_input', self.record_assessment)
        self.payload['primary'] = {'ground': 'B1', 'criterion': 'V9'}
        self.assert_error('invalid_input', self.record_assessment)

    def test_evidence_record_reference_must_match_its_digest(self):
        self.payload['items']['A1']['evidence'][0]['digest'] = '0' * 64
        with self.assertRaises(ResearchError) as caught:
            self.record_assessment()
        self.assertEqual(caught.exception.code, 'problem_assessment_evidence_missing')
        self.assertEqual(caught.exception.details, {'item': 'A1', 'record_kind': 'problem_statement', 'id': 'statement-1'})

    def test_configuration_references_use_the_same_strict_digest_rule_at_record_and_gate_time(self):
        from research_harness.gates import gate_state
        configuration = self.store.snapshot()['records']['configuration']['research']
        reference = {'kind': 'record', 'record_kind': 'configuration', 'id': 'research', 'digest': digest(configuration)}
        self.payload['items']['A1']['evidence'] = [reference]
        saved = self.record_assessment()
        self.assertEqual(saved['items']['A1']['evidence'], [reference])
        self.assertIn(dict(reference, record=configuration),
                      describe_evidence(self.store.snapshot()['records'], self.artifacts)['assessment_evidence'])
        changed = dict(configuration, assessment_fixture='Changed mutable configuration record.')
        self.put('configuration', 'research', changed)
        records = self.store.snapshot()['records']
        expected = {'item': 'A1', 'record_kind': 'configuration', 'id': 'research'}
        with self.assertRaises(ResearchError) as caught:
            describe_evidence(records, self.artifacts)
        self.assertEqual(caught.exception.code, 'problem_assessment_evidence_missing')
        self.assertEqual(caught.exception.details, expected)
        report = gate_state(records, self.artifacts, 'post')
        self.assertEqual([row for row in report['obligations'] if row['code'] == 'problem_assessment_evidence_missing'], [{
            'code': 'problem_assessment_evidence_missing', 'explanation': 'Deliver the exact record cited by this assessment', **expected}])
        replacement = copy.deepcopy(self.payload)
        replacement['id'] = 'assessment-2'
        with self.assertRaises(ResearchError) as caught:
            self.record_assessment(replacement)
        self.assertEqual(caught.exception.code, 'problem_assessment_evidence_missing')
        self.assertEqual(caught.exception.details, expected)
        replacement['items']['A1']['evidence'][0]['digest'] = digest(changed)
        self.record_assessment(replacement)
        self.assertEqual([row for row in gate_state(self.store.snapshot()['records'], self.artifacts, 'post')['obligations']
                          if row['code'] == 'problem_assessment_evidence_missing'], [])

    def test_assess_assessment_reports_missing_stale_and_below_threshold(self):
        claims = self.load_problem_module()
        pending = claims.assess_assessment(self.store.snapshot()['records'], self.artifacts)
        self.assertEqual(pending[0]['code'], 'problem_assessment_missing')
        self.assertEqual(pending[0]['statement_id'], self.statement['id'])
        self.payload['grades']['priority'] = 1
        self.record_assessment()
        pending = claims.assess_assessment(self.store.snapshot()['records'], self.artifacts)
        self.assertEqual(pending[0]['code'], 'problem_rubric_below_threshold')
        self.assertEqual(pending[0]['source'], 'self')
        self.assertEqual(pending[0]['assessment_id'], 'assessment-1')
        self.assertEqual(pending[0]['shortfalls'], [{'item': 'priority', 'required': '>= 2', 'graded': 1}])
        self.select_statement(identifier='statement-2', statement='A revised question.')
        pending = claims.assess_assessment(self.store.snapshot()['records'], self.artifacts)
        stale = next(row for row in pending if row['code'] == 'problem_assessment_stale')
        self.assertEqual((stale['assessment_id'], stale['statement_id']), ('assessment-1', 'statement-2'))
        latest = self.record_assessment(build_assessment(
            claims.find_current_statement(self.store.snapshot()['records']), 'assessment-2'))
        self.assertEqual(claims.find_assessment(self.store.snapshot()['records']), latest)
        self.assertEqual(claims.assess_assessment(self.store.snapshot()['records'], self.artifacts), [])

    def test_assessment_enters_the_evidence_digest_and_the_reviewer_evidence(self):
        claims = self.load_problem_module()
        records = self.store.snapshot()['records']
        before = claims.compute_problem_evidence_digest(records, self.artifacts)
        self.assertIsNone(describe_binding(records, self.artifacts)['assessment'])
        self.assertIsNone(describe_evidence(records, self.artifacts)['assessment'])
        saved = self.record_assessment()
        records = self.store.snapshot()['records']
        after = claims.compute_problem_evidence_digest(records, self.artifacts)
        self.assertNotEqual(before, after)
        self.assertEqual(describe_binding(records, self.artifacts)['assessment'], saved)
        self.assertEqual(describe_evidence(records, self.artifacts)['assessment'], {
            key: saved[key] for key in ('id', 'grades', 'items', 'primary')})
        newer = build_assessment(self.statement, 'assessment-2')
        newer['items']['B1']['note'] = 'A more detailed account of the mathematical payoff.'
        self.record_assessment(newer)
        self.assertNotEqual(after, claims.compute_problem_evidence_digest(self.store.snapshot()['records'], self.artifacts))

    def test_assessment_references_deliver_bound_records_in_stable_order(self):
        other = self.select_statement(identifier='comparison', statement='A related weaker bound.')
        self.select_statement()
        reference = {'kind': 'record', 'record_kind': 'problem_statement',
                     'id': other['id'], 'digest': digest(other)}
        artifact = self.artifacts.put(b'A comparison argument.', 'text/plain')
        self.payload['items']['A2']['evidence'] = [reference, artifact]
        self.payload['items']['priority']['evidence'] = [reference]
        self.record_assessment()
        records = self.store.snapshot()['records']
        expected = [dict(self.payload['items']['A1']['evidence'][0], record=self.statement),
                    dict(reference, record=other), artifact]
        self.assertEqual(describe_evidence(records, self.artifacts)['assessment_evidence'], expected)
        self.assertEqual(describe_binding(records, self.artifacts)['assessment_evidence'], [
            {key: value for key, value in row.items() if key != 'record'} for row in expected])
        for variant in ('missing', 'digest-mismatch'):
            with self.subTest(variant=variant):
                changed = copy.deepcopy(records)
                if variant == 'missing':
                    del changed['problem_statement']['comparison']
                else:
                    changed['problem_statement']['comparison']['statement'] = 'Superseding content under the same id.'
                for project in (describe_evidence, describe_binding):
                    with self.assertRaises(ResearchError) as caught:
                        project(changed, self.artifacts)
                    self.assertEqual(caught.exception.code, 'problem_assessment_evidence_missing')
                    self.assertEqual(caught.exception.details, {
                        'item': 'A2', 'record_kind': 'problem_statement', 'id': 'comparison'})

    def test_assessment_source_references_collect_full_readings_and_citations(self):
        from research_harness.problem_evidence import collect_readings
        self.record_full_reading()
        records = self.store.snapshot()['records']
        reading = records['reading']['read-full']
        link = reading['inspections'][0]['link']
        reference = {'kind': 'source', 'link': link}
        self.payload['items']['A1']['evidence'] = [reference]
        self.record_assessment()
        records = self.store.snapshot()['records']
        evidence = describe_evidence(records, self.artifacts)
        self.assertEqual(evidence['assessment_evidence'][0], dict(reference, reading_ids=['read-full']))
        self.assertEqual([row['id'] for row in collect_readings(records)], ['read-full'])
        self.assertEqual(evidence['readings'][0]['reading'], reading)
        self.assertIn('read-full', [row['reading_id'] for row in evidence['passages']])
        self.assertEqual([row['locator'] for row in evidence['citations']], ['2601.00001v1'])
        changed = copy.deepcopy(records)
        changed['reading'].clear()
        with self.assertRaises(ResearchError) as caught:
            describe_evidence(changed, self.artifacts)
        self.assertEqual(caught.exception.code, 'problem_assessment_evidence_missing')
        self.assertEqual(caught.exception.details, {'item': 'A1', 'link': link})

    def test_assessment_is_immutable_and_replay_preserves_the_revision(self):
        claims = self.load_problem_module()
        revision = self.store.revision
        first = claims.record_assessment(self.store, self.payload, expected_revision=revision, request_id='assessment-replay')
        replay = claims.record_assessment(self.store, self.payload, expected_revision=revision, request_id='assessment-replay')
        self.assertEqual(first['result'], replay['result'])
        self.assertEqual(self.store.revision, revision + 1)
        self.payload['items']['B1']['note'] = 'Changed content under the same identifier.'
        self.assert_error('record_conflict', self.record_assessment)

    def test_accepts_all_three_shared_evidence_reference_shapes(self):
        self.record_full_reading()
        records = self.store.snapshot()['records']
        reading = records['reading']['read-full']
        self.payload['items']['A2']['evidence'] = [{'kind': 'source', 'link': reading['inspections'][0]['link']}]
        self.payload['items']['B1']['evidence'] = [self.artifacts.put(b'A scoped mathematical argument.', 'text/plain')]
        saved = self.record_assessment()
        self.assertEqual(saved['items'], self.payload['items'])

    def test_assessment_command_uses_file_revision_and_request_id(self):
        import json
        from research_harness import cli
        payload_file = self.root / 'assessment.json'
        payload_file.write_text(json.dumps(self.payload))
        parser = cli.build_parser()
        args = parser.parse_args(['problem-assessment', '--file', str(payload_file),
                                  '--expected-revision', str(self.store.revision), '--request-id', 'assessment-cli'])
        self.assertEqual((args.file, args.expected_revision, args.request_id),
                         (str(payload_file), self.store.revision, 'assessment-cli'))
        saved = cli.OPERATIONS['problem-assessment'](self.store, json.loads(payload_file.read_text()),
            expected_revision=args.expected_revision, request_id=args.request_id)['result']
        self.assertEqual(saved['grades'], passing_grades())
        self.assertEqual(set(cli.run(parser.parse_args(['example', 'problem-assessment']))), set(self.payload))
