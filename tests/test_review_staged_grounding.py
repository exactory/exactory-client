"""Scientific grounds and conditions cannot be certified by empty scope."""

import unittest

from research_harness import review_staged as staged
from research_harness.errors import ResearchError
from research_harness.evidence import digest
from tests.test_review_staged_fixtures import epoch, full_card, selection


class StagedGroundingTests(unittest.TestCase):
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
