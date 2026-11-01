"""Scientific grounds and conditions cannot be certified by empty scope."""

import unittest

from research_harness import review_staged as staged
from research_harness.errors import ResearchError
from research_harness.evidence import digest
from test_review_staged_fixtures import add_original_check, epoch, full_card, observation, selection


class StagedGroundingTests(unittest.TestCase):
    def test_original_check_identity_cannot_resolve_an_unsupported_card_conclusion(self):
        for nested in (False, True):
            with self.subTest(nested=nested):
                value = epoch()
                card = full_card(value)
                card['conclusions'][0]['grounds'] = []
                tasks, selected = selection(value, [card])
                add_original_check(value, tasks, selected,
                    **{('conclusion_id' if nested else 'identifier'): card['conclusions'][0]['id']})
                report = staged.verify_completion(value, selected, tasks=tasks)
                self.assertFalse(report['complete'], report['issues'])
                self.assertTrue(any(row['code'] == 'invalid_review_staged' for row in report['issues']))

    def test_conflicting_definitions_across_record_families_are_rejected(self):
        for family in ('assurance_checks', 'decision_checks', 'answers', 'findings',
                       'retrievals', 'selected_groups', 'segment_readings', 'integrations', 'combinations', 'cards', 'cells'):
            with self.subTest(family=family):
                value = epoch()
                tasks, selected = selection(value)
                output = selected['observations'][0]['output']
                if family in ('answers', 'findings', 'retrievals', 'selected_groups', 'decision_checks', 'segment_readings'):
                    # A conflicting definition is invalid even before its science is eligible.
                    output[family] = [dict(id='collision', text='Different scientific content.')]
                else:
                    output[family][0]['id'] = 'collision'
                selected['observations'][0]['raw_output_digest'] = digest(output)
                check = add_original_check(value, tasks, selected, identifier='collision')
                report = staged.verify_completion(value, selected, tasks=tasks)
                self.assertTrue(any(row['code'] == 'invalid_review_staged' and
                    row.get('id') == check['id'] for row in report['issues']), report['issues'])

    def test_nested_record_definitions_cannot_share_resolution_credit(self):
        for family in ('card_check_conclusion', 'check_conclusion'):
            with self.subTest(family=family):
                value = epoch()
                card = full_card(value)
                tasks, selected = selection(value, [card])
                check = add_original_check(value, tasks, selected)
                if family == 'card_check_conclusion':
                    check['conclusions'] = [dict(card['conclusions'][0])]
                else:
                    check['conclusions'].append(dict(check['conclusions'][0], text='A conflicting conclusion.'))
                selected['observations'][-1]['output']['original_checks'] = [check]
                selected['observations'][-1]['raw_output_digest'] = digest(selected['observations'][-1]['output'])
                report = staged.verify_completion(value, selected, tasks=tasks)
                self.assertFalse(report['complete'], report['issues'])
                self.assertTrue(any(row['code'] == 'invalid_review_staged' and
                    'Conflicting scientific record definitions' in row.get('reason', '') for row in report['issues']))

    def test_card_questions_cannot_disappear_from_completion(self):
        value = epoch()
        card = full_card(value)
        card['unresolved'] = [dict(text='Is a missing hypothesis required?',
                                  claim_ids=list(value['manifest']['contexts']), needs='A proof of the hypothesis.')]
        tasks, selected = selection(value, [card])
        compact = staged.compact_card(card)
        self.assertEqual(compact['question_obligations'], [dict(
            id='card-unresolved:' + digest([card['id'], card['unresolved'][0]]), question=card['unresolved'][0])])
        report = staged.verify_completion(value, selected, tasks=tasks)
        self.assertFalse(report['complete'], report['issues'])
        self.assertTrue(any(row['code'] == 'review_card_question_pending' for row in report['issues']))

    def test_card_questions_require_grounded_dependent_answers_or_adverse_assessment(self):
        for disposition, grounded, dependent, adverse in (
                ('answered', True, True, False), ('answered', False, True, False),
                ('answered', True, False, False), ('retained', True, True, False),
                ('retained', True, True, True), ('unresolved', True, True, True)):
            with self.subTest(disposition=disposition, grounded=grounded, dependent=dependent, adverse=adverse):
                value = epoch()
                card = full_card(value)
                question = dict(text='Is the missing hypothesis necessary?',
                    claim_ids=list(value['manifest']['contexts']), needs='Original hypothesis evidence.')
                card['unresolved'] = [question]
                terminal = dict(assurances={kind: 'failed' if adverse else 'passed' for kind in staged.ASSURANCES},
                                visible_conditions=[])
                tasks, selected = selection(value, [card], terminal=terminal)
                identifier = 'card-unresolved:' + digest([card['id'], question])
                output = selected['observations'][0]['output']
                answer = dict(id='hypothesis-answer', finding_id=identifier, disposition=disposition,
                              reason='The original specifies exactly this hypothesis.',
                              grounds=card['critical_spans'] if grounded else [])
                output['answers'] = [answer]
                output['integrations'][0]['answers'] = [answer['id']]
                if not dependent:
                    output['answers'] = []
                    output['integrations'][0]['answers'] = []
                    selected['observations'][1]['output']['answers'] = [answer]
                    selected['observations'][1]['raw_output_digest'] = digest(selected['observations'][1]['output'])
                selected['observations'][0] = observation(value, tasks[1], output,
                    dependencies={tasks[0]['id']: selected['observations'][1]['raw_output_digest']})
                report = staged.verify_completion(value, selected, tasks=tasks)
                self.assertEqual(report['complete'], grounded and dependent and
                    (disposition == 'answered' or (disposition == 'retained' and adverse)), report['issues'])

    def test_empty_critical_and_judgment_quotations_cannot_complete(self):
        value = epoch()
        card = full_card(value)
        source = next(row for row in value['manifest']['sources']
                      if row['id'] == card['critical_spans'][0]['source_id'])
        empty = staged.source_span(source, 0, 0)
        card['critical_spans'] = [empty]
        card['critical_quotations'] = [dict(span=empty, text='', kind='quotation')]
        for row in card['judgments'] + card['conclusions']:
            row['grounds'] = [empty]
        tasks, selected = selection(value, [card])
        report = staged.verify_completion(value, selected, tasks=tasks)
        self.assertFalse(report['complete'], report['issues'])
        self.assertEqual(report['progress']['claim_cells'], 0)

    def test_normalization_empty_spans_cannot_ground_scientific_records(self):
        for text in ('', ' \t\n', '\u2003\u00a0'):
            with self.subTest(text=text):
                source = staged.make_source('s', 'frame', text, identity={'path': ['empty']})
                span = staged.source_span(source, 0, len(text))
                with self.assertRaises(ResearchError):
                    staged.materialize_quotation(source, span)

    def test_empty_frame_field_remains_in_inventory_without_becoming_a_quotation(self):
        manifest = staged.build_manifest({'statement': 'A claim.', 'note': ''}, [], [])
        empty = next(row for row in manifest['sources'] if row['text'] == '')
        self.assertEqual(empty['required_intervals'], [])
        self.assertTrue(any(row['source_id'] == empty['id'] for row in manifest['frame_fields']))
        self.assertTrue(manifest['contexts'])

    def test_false_condition_blocks_passing_assurances(self):
        value = epoch()
        card = full_card(value)
        card['conditions'] = [dict(id='prime-only', text='Inputs must be prime.',
                                   grounds=card['critical_spans'], satisfied=False)]
        tasks, selected = selection(value, [card])
        report = staged.verify_completion(value, selected, tasks=tasks)
        self.assertFalse(report['complete'], report['issues'])
        self.assertTrue(any(row['code'] == 'review_assurance_dependency_pending'
                            for row in report['issues']), report['issues'])

    def test_record_dependencies_and_grounds_are_resolved_individually(self):
        for change in ('dependency', 'empty_grounds', 'cycle'):
            with self.subTest(change=change):
                value = epoch()
                card = full_card(value)
                conclusion = card['conclusions'][0]
                if change == 'dependency':
                    conclusion['dependencies'] = ['missing-retrieval']
                elif change == 'empty_grounds':
                    conclusion['grounds'] = []
                else:
                    conclusion.update(grounds=['other'], dependencies=['other'])
                    card['conclusions'].append(dict(id='other', text='A circular premise.',
                        grounds=[conclusion['id']], dependencies=[conclusion['id']]))
                tasks, selected = selection(value, [card])
                report = staged.verify_completion(value, selected, tasks=tasks)
                self.assertFalse(report['complete'], report['issues'])

    def test_satisfied_grounded_condition_can_complete(self):
        value = epoch()
        card = full_card(value)
        card['conditions'] = [dict(id='prime-only', text='Inputs are prime.',
                                   grounds=card['critical_spans'], satisfied=True,
                                   dependencies=[card['conclusions'][0]['id']])]
        tasks, selected = selection(value, [card])
        report = staged.verify_completion(value, selected, tasks=tasks)
        self.assertTrue(report['complete'], report['issues'])

    def test_false_condition_allows_a_completed_adverse_assessment(self):
        value = epoch()
        card = full_card(value)
        card['conditions'] = [dict(id='prime-only', text='Inputs must be prime.',
                                   grounds=card['critical_spans'], satisfied=False)]
        terminal = dict(assurances={kind: 'failed' for kind in staged.ASSURANCES},
                        visible_conditions=['prime-only'])
        tasks, selected = selection(value, [card], terminal=terminal)
        for check in selected['observations'][0]['output']['assurance_checks']:
            check['grounds'] = ['prime-only']
        selected['observations'][0]['raw_output_digest'] = digest(selected['observations'][0]['output'])
        report = staged.verify_completion(value, selected, tasks=tasks)
        self.assertTrue(report['complete'], report['issues'])

    def test_condition_cannot_use_a_cell_with_an_unresolved_dependency(self):
        value = epoch()
        card = full_card(value)
        card['judgments'][0]['dependencies'] = ['missing-retrieval']
        card['conditions'] = [dict(id='prime-only', text='Inputs are prime.', satisfied=True,
                                   grounds=[card['judgments'][0]['id']])]
        tasks, selected = selection(value, [card])
        report = staged.verify_completion(value, selected, tasks=tasks)
        self.assertFalse(report['complete'], report['issues'])

    def test_supported_cell_with_pending_dependency_cannot_support_pass(self):
        value = epoch()
        card = full_card(value)
        card['judgments'][0]['dependencies'] = ['missing-retrieval']
        tasks, selected = selection(value, [card])
        report = staged.verify_completion(value, selected, tasks=tasks)
        self.assertFalse(report['complete'], report['issues'])


if __name__ == '__main__':
    unittest.main()
