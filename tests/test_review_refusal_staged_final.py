"""Ordinary objection grammar is enforced before accepting a staged final."""

import copy
from unittest.mock import patch

from research_harness import review_protocol as protocol, review_refusal
from research_harness.evidence import digest
from problem_parts_fixtures import ResultPartsCase
from test_review_staged_fixtures import epoch, task


class StagedFinalObjectionRefusalTests(ResultPartsCase):
    staged_entry = True

    def setUp(self):
        super().setUp()
        self.records = self.case.read_records()
        assignment = copy.deepcopy(self.records['review_assignment']['bar-first'])
        assigned = task(epoch(), 'final')
        assignment.update(id='terminal-child', role='result', staged_task=assigned,
                          staged_review=dict(kind='task', epoch_id='epoch-A'))
        assignment['packet'] = protocol._put_json(self.case.artifacts, dict(staged_review={'task': assigned}))
        self.records['review_assignment'][assignment['id']] = assignment
        self.objection = dict(id='scope-gap', claim='The supplied claim omits its hypothesis.',
            reason='The original establishes the implication under a stated hypothesis only.',
            evidence=[self.case.supporting], resolution_condition='State and justify the missing hypothesis.')

    def classify_output(self, raw):
        attempt = dict(id='terminal-attempt', assignment_id='terminal-child', status='completed',
                       output=protocol._put_json(self.case.artifacts, raw))
        self.records['review_attempt'][attempt['id']] = attempt
        with patch.object(protocol, 'authenticate_attempt', return_value=True):
            failure = review_refusal.classify_observed_output(self.records, self.case.artifacts, attempt['id'])
            observed = protocol.read_raw_observation(self.records, self.case.artifacts, attempt['id'])
        self.assertEqual(observed['raw_output'], raw)
        self.assertEqual(observed['raw_output_digest'], digest(raw))
        return failure

    def classify_objections(self, objections):
        scientific = self.case.build_review_output('result')
        scientific['objections'] = objections
        return self.classify_output(dict(scientific, terminal={'assurances': {}, 'visible_conditions': []}))

    def test_partial_scientific_objects_require_every_ordinary_field(self):
        assignment = self.records['review_assignment']['terminal-child']
        cases = [('result', self.case.build_review_output('result')),
                 ('adjudicator', dict(objection_id='scope-gap', disposition='upheld', reason='The original confirms the gap.',
                                      evidence=[self.case.supporting], correction_admissible=None))]
        for role, scientific in cases:
            assignment['role'] = role
            for field in scientific:
                with self.subTest(role=role, field=field):
                    raw = dict(scientific, terminal={})
                    del raw[field]
                    failure = self.classify_output(raw)
                    self.assertIsNotNone(failure)
                    self.assertEqual(failure['check'], 'review.staged_final')
                    self.assertIn(field, failure['details'].get('missing_fields', []))
            with self.subTest(role=role, complete=True):
                self.assertIsNone(self.classify_output(dict(scientific, terminal={})))

    def test_pure_terminal_and_retrieval_outputs_need_no_ordinary_fields(self):
        for raw in ({'terminal': {}}, {'retrievals': []}):
            with self.subTest(output=raw):
                self.assertIsNone(self.classify_output(raw))

    def test_pages_require_the_assigned_task_and_ownership(self):
        from test_review_composition import make_fixture
        plan, pages, _, _ = make_fixture()
        value = epoch(id=plan['epoch_id'])
        ownership = [owner for owner in plan['ownership'] if owner['page_id'] == pages[0]['output']['page_id']]
        assigned = task(value, 'answer', answer_ranges=ownership, composition_role=plan['role'],
                        composition_plan_root=digest(plan))
        assignment = self.records['review_assignment']['terminal-child']
        assignment.update(staged_task=assigned, staged_review=dict(kind='task', epoch_id=value['id']),
                          composition_plan_id=digest(plan))
        assignment['packet'] = protocol._put_json(self.case.artifacts, dict(staged_review={'task': assigned}))
        self.records['review_epoch'] = {value['id']: value}
        self.records['review_composition_plan'] = {digest(plan): plan}
        valid = dict(pages[0]['output'], task_id=assigned['id'])
        wrong_task = dict(valid, task_id='another-answer-task')
        wrong_ownership = dict(pages[1]['output'], task_id=assigned['id'])
        for page, rejected in ((valid, False), (wrong_task, True), (wrong_ownership, True)):
            with self.subTest(page_id=page['page_id'], task_id=page['task_id']):
                failure = self.classify_output(dict(pages=[page]))
                if rejected:
                    self.assertIsNotNone(failure)
                    self.assertEqual(failure['check'], 'review.staged_page')
                else:
                    self.assertIsNone(failure)

    def test_every_objection_field_is_required(self):
        for field in ('id', 'claim', 'reason', 'evidence', 'resolution_condition'):
            with self.subTest(field=field):
                missing = dict(self.objection)
                del missing[field]
                failure = self.classify_objections([missing])
                self.assertIsNotNone(failure)
                self.assertEqual((failure['code'], failure['check']), ('invalid_value_review', 'review.objections'))
                self.assertEqual(failure['details']['path'], '/objections/0')
                self.assertEqual(failure['details']['missing_fields'], [field])

    def test_objection_rows_and_text_fields_have_the_required_types(self):
        for row in (None, 'not an objection', 7, []):
            with self.subTest(row=row):
                failure = self.classify_objections([row])
                self.assertIsNotNone(failure)
                self.assertEqual(failure['check'], 'review.objections')
                self.assertEqual(failure['details']['path'], '/objections/0')
        for field in ('id', 'claim', 'reason', 'resolution_condition'):
            for value in (None, 7, {}, [], ' '):
                with self.subTest(field=field, value=value):
                    failure = self.classify_objections([dict(self.objection, **{field: value})])
                    self.assertIsNotNone(failure)
                    self.assertEqual(failure['check'], 'review.objections')
                    self.assertEqual(failure['details']['path'], '/objections/0/' + field)

    def test_objection_evidence_must_be_a_nonempty_evidence_array(self):
        for value in (None, {}, 'not an evidence array', [], [{}]):
            with self.subTest(evidence=value):
                failure = self.classify_objections([dict(self.objection, evidence=value)])
                self.assertIsNotNone(failure)
                self.assertEqual(failure['details'].get('path'), '/objections/0/evidence')

    def test_duplicate_objection_ids_are_refused(self):
        duplicate = dict(self.objection, claim='The motivation has a different missing condition.')
        failure = self.classify_objections([self.objection, duplicate])
        self.assertIsNotNone(failure)
        self.assertEqual((failure['code'], failure['check']), ('invalid_value_review', 'review.objections'))
        self.assertEqual(failure['details']['path'], '/objections/1/id')

    def test_distinct_valid_objections_remain_accepted(self):
        other = dict(self.objection, id='motivation-gap', claim='The motivation needs the same hypothesis.')
        self.assertIsNone(self.classify_objections([self.objection, other]))
