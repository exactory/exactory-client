"""A7 applies prospectively while earlier problem records retain their contracts."""

import copy
import json
import unittest
from unittest.mock import patch

from research_harness import cli, problem_rubric as rubric
from research_harness import research_decisions as decisions, review_protocol as protocol
from research_harness import review_transport as transport
from research_harness.errors import ResearchError
from research_harness.gates import gate_state
from test_research_problem_assessment import build_assessment
from test_research_problem_delivery import ProblemDeliveryCase


A7_DEFINITION = (
    "Direction after a refutation: when a counterexample or another proof that the record has read in full "
    "refutes a conjecture that the question concerns, the question studies the world that the refutation "
    "reveals: the mechanism or the structure of the counterexamples, how far and how strongly the statement "
    "fails, what falls with it, or the statement that replaces it. A question whose purpose is to find where "
    "the refuted statement still holds, on a restricted class or under added hypotheses, fails, whatever its "
    "other grades. A question that no refutation concerns passes.")
BAR_POINT = (
    "Sixth, the conjectures of this field that a counterexample or another proof has refuted, with the works "
    "that refute them, and the questions about the world that each refutation reveals. A question whose "
    "purpose is to find where a refuted statement still holds is not one this field should take up.")
SLATE_SENTENCE = (
    "Where a candidate's purpose is to find where a refuted statement still holds, say so as an objection "
    "that names the candidate in candidate_ids.")
REGRADE_EXPLANATION = (
    "Record a new self-assessment that grades A7, and obtain both result reviews on the current result "
    "template, before the post.")
PREVIOUS_HASHES = {
    ('bar', False): 'e402dcf92926adac0a536d702e93a6e0d33c317f8ec5ff9440028731f695703b',
    ('bar', True): '6b383f8edaf372617444e25f84d5022563bd5b45fb8767810d867e90aaf8bcea',
    ('slate', False): '4ec89a2c1947308006fcf53a68f382660970c0fa61bcb401e72437da55023381',
    ('slate', True): '41b566a961b90d92fbdf4798d67aecdfe556b20979de4759f83c8164e01f20bf',
    ('result', False): 'c94242e16a09ff7249e409e25e44bc08a1e8c0a978e8455bfb54e85869d94682',
    ('result', True): 'a30080740a6d75258353df01e098efc1534b30fd18b3bf15bc2ff430a9bccfaa',
}


class ProblemDirectionTests(ProblemDeliveryCase):
    def build_current_assessment(self):
        payload = build_assessment(self.selected)
        payload['grades']['admission']['A7'] = 'pass'
        payload['items']['A7'] = copy.deepcopy(payload['items']['A1'])
        return payload

    def prepare_statement_and_bars(self):
        self.initialize_problem()
        self.selected = self.select_statement()
        self.observe_bars()

    def prepare_previous_record(self, *, failed_item=None):
        claims = self.load_problem_module()
        original_prompt, original_output = protocol.canonical_prompt, self.build_review_output

        def build_previous_prompt(role, **options):
            if options.get('orchestration') == 'problem_creation' and role in ('bar', 'slate', 'result'):
                options.setdefault('version', 4)
            return original_prompt(role, **options)

        def build_previous_output(stage, *args):
            output = original_output(stage, *args)
            if stage == 'result':
                output['value']['rubric']['admission'].pop('A7', None)
                output['value']['rubric_evidence'].pop('A7', None)
                if failed_item is not None:
                    output['value']['rubric']['admission'][failed_item] = 'fail'
            return output

        def seed_previous_assessment(store, payload, *, expected_revision, request_id):
            payload = copy.deepcopy(payload)
            payload['grades']['admission'].pop('A7', None)
            payload['items'].pop('A7', None)
            saved = dict(payload, recorded_revision=expected_revision + 1)
            # Store the exact pre-A7 record shape, without passing it through the new-write validator.
            self.put('problem_assessment', saved['id'], saved)
            return {'result': saved}

        with patch.object(protocol, 'canonical_prompt', side_effect=build_previous_prompt), \
                patch.object(self, 'build_review_output', side_effect=build_previous_output), \
                patch.object(claims, 'record_assessment', side_effect=seed_previous_assessment):
            self.prepare_ready()
            output = self.build_review_output('slate')
            for check in output['value']['assurances']:
                check['status'] = 'not_assessed'
            output['value']['rubric_potential'] = {'candidate-1': {
                'significance': {'B1': 2, 'B2': 0}, 'priority': 2}}
            self.observe('slate', 'first', output)
        self.put('problem_decision', 'decision', {'id': 'decision', 'action': 'post',
            'review_ids': ['review-result-first', 'review-result-second'],
            'bar_review_ids': ['review-bar-first', 'review-bar-second'],
            'reason': 'Both observed results support the exact evidence.',
            'problem_evidence_digest': claims.compute_problem_evidence_digest(self.store.snapshot()['records'], self.artifacts),
            'recorded_revision': self.store.revision + 1})

    def test_new_self_assessment_requires_a7(self):
        self.initialize_problem()
        self.selected = self.select_statement()
        payload = self.build_current_assessment()
        del payload['grades']['admission']['A7']
        payload['items'].pop('A7')
        self.assert_error('invalid_rubric_grades', lambda: self.mutate(
            self.load_problem_module().record_assessment, payload))

    def test_new_self_assessment_requires_a7_evidence(self):
        self.initialize_problem()
        self.selected = self.select_statement()
        payload = self.build_current_assessment()
        del payload['items']['A7']
        with self.assertRaises(ResearchError) as caught:
            self.mutate(self.load_problem_module().record_assessment, payload)
        self.assertEqual((caught.exception.code, caught.exception.details),
                         ('problem_assessment_evidence_missing', {'item': 'A7'}))

    def test_failed_self_a7_is_a_post_shortfall(self):
        self.prepare_ready()
        payload = self.build_current_assessment()
        payload['id'] = 'assessment-a7'
        payload['grades']['admission']['A7'] = 'fail'
        try:
            self.mutate(self.load_problem_module().record_assessment, payload)
        except ResearchError as error:
            self.fail('An A7 self-assessment must be recordable: ' + str(error))
        report = gate_state(self.store.snapshot()['records'], self.artifacts, 'post')
        shortfalls = [row for row in report['obligations'] if row['code'] == 'problem_rubric_below_threshold']
        self.assertEqual([(row['source'], row['assessment_id'], row['shortfalls']) for row in shortfalls], [
            ('self', 'assessment-a7', [{'item': 'A7', 'required': 'pass', 'graded': 'fail'}])])

    def test_new_result_requires_a7(self):
        self.prepare_statement_and_bars()
        output = self.build_review_output('result')
        output['value']['rubric']['admission'].pop('A7', None)
        output['value']['rubric_evidence'].pop('A7', None)
        payload = self.observe('result', 'first', output, record=False)
        self.assert_error('invalid_rubric_grades', lambda: self.mutate(decisions.record_value_review, payload))
        self.record_candidate()
        self.assign('slate', 'first')
        records = self.store.snapshot()['records']
        for identifier in ('bar-first', 'bar-second', 'slate-first', 'result-first'):
            assignment = records['review_assignment'][identifier]
            self.assertEqual(assignment['prompt']['template_id'], 'exactory-problem-review-v5/' + assignment['role'])

    def test_new_result_requires_a7_evidence(self):
        self.prepare_statement_and_bars()
        output = self.build_review_output('result')
        output['value']['rubric']['admission']['A7'] = 'pass'
        output['value']['rubric_evidence'].pop('A7', None)
        payload = self.observe('result', 'first', output, record=False)
        self.assert_error('invalid_value_review', lambda: self.mutate(decisions.record_value_review, payload))

    def test_failed_result_a7_is_released_only_by_its_not_upheld_adjudication(self):
        self.prepare_statement_and_bars()
        for reviewer in ('first', 'second'):
            output = self.build_review_output('result')
            output['value']['rubric']['admission']['A7'] = 'fail' if reviewer == 'first' else 'pass'
            output['value']['rubric_evidence']['A7'] = 'The delivered statement fixes the direction after the refutation.'
            try:
                self.observe('result', reviewer, output)
            except ResearchError as error:
                self.fail('An A7 result must be recordable: ' + str(error))
        records = self.store.snapshot()['records']
        report = decisions.assess_problem_reviews(records, self.artifacts)
        below = [row for row in report['obligations'] if row['code'] == 'problem_rubric_below_threshold']
        self.assertEqual([(row['review_id'], row['shortfalls']) for row in below], [
            ('review-result-first', [{'item': 'A7', 'required': 'pass', 'graded': 'fail'}])])
        identifier = 'rubric:review-result-first:A7'
        assignment = self.assign('adjudicator', 'focused-third', identifier='focused',
            review_ids=report['review_ids'], objection_id=identifier)
        packet = json.loads(self.artifacts.read(assignment['packet']))
        self.assertEqual(packet['rubric_definition'], {'item': 'A7', 'definition': A7_DEFINITION, 'threshold': 'pass'})
        output = {'objection_id': identifier, 'disposition': 'not_upheld',
            'reason': 'The delivered evidence supports the direction of the question.',
            'evidence': [self.supporting], 'correction_admissible': None}
        with patch.object(transport, 'send_request', return_value=self.build_provider_response(output)):
            self.mutate(protocol.invoke_assignment, {'id': 'focused-attempt', 'assignment_id': assignment['id']}, credential='test-only')
        self.mutate(protocol.record_adjudication, dict(output, id='focused-finding', assignment_id=assignment['id']))
        after = decisions.assess_problem_reviews(self.store.snapshot()['records'], self.artifacts)
        self.assertTrue(after['ready'], after['obligations'])

    def test_previous_records_read_and_name_one_regrade_obligation_without_mutation(self):
        self.prepare_previous_record()
        before = self.store.snapshot()
        expected = {'code': 'problem_rubric_regrade_required', 'explanation': REGRADE_EXPLANATION,
            'assessment_id': 'assessment-1', 'review_ids': ['review-result-first', 'review-result-second']}
        reviews = decisions.assess_problem_reviews(before['records'], self.artifacts)
        self.assertEqual(reviews['obligations'], [expected])
        self.assertEqual(gate_state(before['records'], self.artifacts, 'post')['obligations'], [expected])
        reversed_pair = copy.deepcopy(before['records'])
        reversed_pair['problem_decision']['decision']['review_ids'].reverse()
        self.assertEqual(gate_state(reversed_pair, self.artifacts, 'post')['obligations'], [expected])
        for command in ('status', 'next'):
            for summary in (False, True):
                args = [command, '--workspace', str(self.root)] + (['--summary'] if summary else [])
                report = cli.run(cli.build_parser().parse_args(args))
                self.assertFalse(report['ready'])
                if not summary:
                    self.assertEqual(report['obligations'], [expected])
        self.assertEqual(self.store.snapshot(), before)
        for review in before['records']['value_review'].values():
            state = protocol.assignment_state(before['records'], self.artifacts, review['assignment_id'])
            self.assertTrue(state['ready'], state['obligations'])
            payload = dict(review['payload'], id=review['id'], assignment_id=review['assignment_id'])
            self.assertEqual(self.mutate(decisions.record_value_review, payload)['result'], review)
        after = self.store.snapshot()['records']
        for kind, rows in before['records'].items():
            for identifier, saved in rows.items():
                self.assertEqual(after[kind][identifier], saved)
        posted = copy.deepcopy(before['records'])
        posted['problem_post'] = {'posted': {'statement_digest': self.load_problem_module().compute_statement_digest(self.selected)}}
        self.assertEqual(decisions.assess_problem_reviews(posted, self.artifacts)['obligations'], [])

    def test_previous_records_still_refuse_other_admission_item_sets(self):
        self.prepare_previous_record()
        records = self.store.snapshot()['records']
        self.assertEqual([row['code'] for row in decisions.assess_problem_reviews(records, self.artifacts)['obligations']],
                         ['problem_rubric_regrade_required'])
        for source in ('self', 'review'):
            for variant in ('missing', 'extra'):
                changed = copy.deepcopy(records)
                grades = (changed['problem_assessment']['assessment-1']['grades'] if source == 'self' else
                          changed['value_review']['review-result-first']['payload']['value']['rubric'])
                if variant == 'missing':
                    del grades['admission']['A6']
                else:
                    grades['admission']['A8'] = 'pass'
                with self.subTest(source=source, variant=variant), self.assertRaises(ResearchError) as caught:
                    decisions.assess_problem_reviews(changed, self.artifacts)
                self.assertEqual(caught.exception.code, 'invalid_rubric_grades')

    def test_partial_result_refresh_merges_regrade_obligations_from_the_saved_decision(self):
        self.prepare_previous_record()
        try:
            self.observe('result', 'first', identifier='result-a7-first')
        except ResearchError as error:
            self.fail('The old result must continue on the current contract: ' + str(error))
        report = gate_state(self.store.snapshot()['records'], self.artifacts, 'post')
        self.assertEqual(report['obligations'], [{
            'code': 'problem_rubric_regrade_required', 'explanation': REGRADE_EXPLANATION,
            'assessment_id': 'assessment-1', 'review_ids': ['review-result-first', 'review-result-second']}])

    def test_previous_result_refresh_retains_reviewer_session_slot_and_recorded_requests(self):
        self.prepare_previous_record()
        before = self.store.snapshot()['records']
        prior = before['review_assignment']['result-first']
        self.assertTrue(protocol.is_previous_prompt(prior))
        assessment = self.build_current_assessment()
        assessment['id'] = 'assessment-a7'
        self.mutate(self.load_problem_module().record_assessment, assessment)
        successor = self.assign('result', 'first', identifier='result-a7')
        self.assertEqual(successor['prompt']['template_id'], 'exactory-problem-review-v5/result')
        for key in ('reviewer_id', 'session_id', 'slot_id', 'route_id', 'author_id', 'subject'):
            self.assertEqual(successor[key], prior[key])
        self.assertEqual(successor['history_assignment_id'], prior['id'])
        refresh = json.loads(self.artifacts.read(successor['packet']))['problem_refresh']
        self.assertEqual(refresh['reason'], 'prompt_contract_changed')
        self.assertEqual(refresh['previous_template_id'], prior['prompt']['template_id'])
        self.assertEqual(refresh['previous_prompt_hash'], prior['prompt']['rendered_prompt_hash'])
        self.assertEqual(refresh['template_id'], successor['prompt']['template_id'])
        self.assertEqual(refresh['prompt_hash'], successor['prompt']['rendered_prompt_hash'])
        after = self.store.snapshot()['records']
        for kind in ('review_assignment', 'review_attempt', 'value_review'):
            for identifier, saved in before[kind].items():
                self.assertEqual(after[kind][identifier], saved)
        for role in ('bar', 'slate'):
            self.assert_error('review_resampling_forbidden', lambda: self.assign(role, 'first', identifier=role + '-repeat'))
        output = self.build_review_output('result')
        with patch.object(transport, 'send_request', return_value=self.build_provider_response(output)):
            self.mutate(protocol.invoke_assignment, {'id': 'a7-attempt', 'assignment_id': successor['id']}, credential='test-only')
        self.mutate(decisions.record_value_review, dict(output, id='review-result-a7', dossier_id=None,
                                                       assignment_id=successor['id']))
        partial = decisions.assess_problem_reviews(self.store.snapshot()['records'], self.artifacts)
        regrade = [row for row in partial['obligations'] if row['code'] == 'problem_rubric_regrade_required']
        self.assertEqual(regrade, [{'code': 'problem_rubric_regrade_required', 'explanation': REGRADE_EXPLANATION,
                                   'assessment_id': None, 'review_ids': ['review-result-second']}])
        self.observe('result', 'second', identifier='result-a7-second')
        current = decisions.assess_problem_reviews(self.store.snapshot()['records'], self.artifacts)
        self.assertTrue(current['ready'], current['obligations'])
        self.mutate(self.load_problem_module().record_decision, {'id': 'decision-a7', 'action': 'post',
            'review_ids': current['review_ids'], 'bar_review_ids': current['bar_review_ids'],
            'reason': 'Both retained reviewers graded A7 on the current evidence.'})
        report = gate_state(self.store.snapshot()['records'], self.artifacts, 'post')
        self.assertTrue(report['ready'], report['obligations'])

    def test_previous_rubric_shortfall_can_still_be_adjudicated(self):
        self.prepare_previous_record(failed_item='A1')
        before = self.store.snapshot()['records']
        report = decisions.assess_problem_reviews(before, self.artifacts)
        self.assertEqual([row['code'] for row in report['obligations']], ['problem_rubric_regrade_required'])
        assignment = self.assign('adjudicator', 'focused-third', identifier='old-focused',
            review_ids=report['review_ids'], objection_id='rubric:review-result-first:A1')
        self.assertEqual(json.loads(self.artifacts.read(assignment['packet']))['rubric_definition']['item'], 'A1')
        output = {'objection_id': 'rubric:review-result-first:A1', 'disposition': 'not_upheld',
            'reason': 'The delivered statement is precise and has exact resolution criteria.',
            'evidence': [self.supporting], 'correction_admissible': None}
        with patch.object(transport, 'send_request', return_value=self.build_provider_response(output)):
            self.mutate(protocol.invoke_assignment, {'id': 'old-focused-attempt', 'assignment_id': assignment['id']}, credential='test-only')
        self.mutate(protocol.record_adjudication, dict(output, id='old-finding', assignment_id=assignment['id']))
        state = protocol.adjudication_state(self.store.snapshot()['records'], self.artifacts, 'old-finding')
        self.assertTrue(state['ready'], state['obligations'])
        self.assertEqual(state['disposition'], 'not_upheld')


class ProblemDirectionPromptTests(unittest.TestCase):
    def test_result_prompt_delivers_the_a7_anchor_and_complete_response_shapes(self):
        prompt = protocol.canonical_prompt('result', orchestration='problem_creation')
        self.assertIn('A7. ' + A7_DEFINITION, prompt['text'])
        self.assertEqual(rubric.ANCHORS['A7']['threshold'], 'pass')
        marker = 'Return the exact JSON response shape: '
        shape, _ = json.JSONDecoder().raw_decode(prompt['text'].split(marker, 1)[1])
        marker = 'A valid final response, with the placeholders replaced by the delivered values: '
        example, _ = json.JSONDecoder().raw_decode(prompt['text'].split(marker, 1)[1])
        for value in (shape['value'], example['value']):
            self.assertIn('A7', value['rubric']['admission'])
            self.assertIn('A7', value['rubric_evidence'])
        for example_text in (protocol._PROBLEM_RESULT_EXAMPLE, protocol._PROBLEM_RESULT_EXAMPLE_0551):
            value = json.loads(example_text)['value']
            self.assertIn('A7', value['rubric']['admission'])
            self.assertIn('A7', value['rubric_evidence'])

    def test_bar_prompt_states_the_sixth_point_after_the_fifth(self):
        text = protocol.canonical_prompt('bar', orchestration='problem_creation')['text']
        self.assertIn(BAR_POINT, text)
        self.assertGreater(text.index(BAR_POINT), text.index('Fifth,'))
        self.assertGreater(text.index(BAR_POINT), text.index(
            'the explanation the resolution would give is itself the significance.'))

    def test_slate_prompt_names_the_excluded_candidate_in_an_objection(self):
        text = protocol.canonical_prompt('slate', orchestration='problem_creation')['text']
        self.assertIn(SLATE_SENTENCE, text)
        self.assertGreater(text.index(SLATE_SENTENCE), text.index('or rests on a pattern alone'))

    def test_previous_prompt_bytes_authenticate_without_refreshing_bars_or_slates(self):
        for (role, repair), expected_hash in PREVIOUS_HASHES.items():
            with self.subTest(role=role, repair=repair):
                old = protocol.canonical_prompt(role, version=4, repair=repair, orchestration='problem_creation')
                current = protocol.canonical_prompt(role, repair=repair, orchestration='problem_creation')
                suffix = '/context-repair' if repair else ''
                self.assertEqual(old['template_id'], ('exactory-problem-review-v4/' if role == 'result' else
                                                     'exactory-problem-review-v2/') + role + suffix)
                self.assertEqual(old['template_hash'], expected_hash)
                assignment = {'role': role, 'subject': {'kind': 'problem'}, 'prompt': old, 'reassessment_required': repair}
                self.assertTrue(protocol._prompt_matches(assignment))
                self.assertEqual(protocol.is_previous_prompt(assignment), role == 'result')
                self.assertEqual(current['template_id'], 'exactory-problem-review-v5/' + role + suffix)
                self.assertNotEqual(old['template_hash'], current['template_hash'])
                altered = copy.deepcopy(assignment)
                altered['prompt']['text'] += ' '
                self.assertFalse(protocol._prompt_matches(altered))
        adjudicator = protocol.canonical_prompt('adjudicator', orchestration='problem_creation')
        self.assertEqual(adjudicator['template_id'], 'exactory-problem-review-v2/adjudicator')
        self.assertEqual(adjudicator['template_hash'], '352e433bb6ebe3a989437c65a1e9150d46bf1971d55b040a940ead24ceb873b2')
