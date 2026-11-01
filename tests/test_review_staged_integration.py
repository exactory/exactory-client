"""Full-card accounting, graph closure, and assurance constraints."""

import copy
import unittest

from research_harness import review_staged as staged
from research_harness.evidence import digest
from tests.test_review_staged_fixtures import epoch, full_card, selection


class IntegrationTests(unittest.TestCase):
    def check(self, value, tasks, selected):
        for row in selected['observations']:
            row['raw_output_digest'] = digest(row['output'])
        return staged.verify_completion(value, selected, tasks=tasks)

    def test_lost_full_card_condition_blocks_completion(self):
        value = epoch()
        card = full_card(value, conditions=[{'id': 'prime-only', 'text': 'Only prime inputs.',
                                           'grounds': []}])
        tasks, selected = selection(value, [card])
        integration = selected['observations'][0]['output']['integrations'][0]
        integration['obligation_ids'].remove('prime-only')
        integration['dispositions'] = [x for x in integration['dispositions'] if x['id'] != 'prime-only']
        report = self.check(value, tasks, selected)
        self.assertFalse(report['complete'])
        self.assertIn('prime-only', str(report['issues']))

    def test_unaccounted_conclusion(self):
        value = epoch()
        tasks, selected = selection(value)
        selected['observations'][0]['output']['integrations'][0]['dispositions'].pop()
        self.assertFalse(self.check(value, tasks, selected)['complete'])

    def test_missing_child_and_omitted_dependency(self):
        value = epoch()
        tasks, selected = selection(value, integration_changes={'child_ids': ['missing'], 'child_union': []})
        self.assertFalse(self.check(value, tasks, selected)['complete'])
        tasks, selected = selection(value, integration_changes={'dependencies': ['unread-source']})
        self.assertFalse(self.check(value, tasks, selected)['complete'])

    def test_all_unresolved_cells_cannot_pass_assurances(self):
        value = epoch()
        card = full_card(value)
        for cell in card['judgments']:
            cell.update(status='unresolved', scope='unresolved')
        tasks, selected = selection(value, [card], terminal={'assurances': {x: 'passed' for x in staged.ASSURANCES},
                                                          'visible_conditions': []})
        report = self.check(value, tasks, selected)
        self.assertFalse(report['complete'])
        self.assertEqual(report['progress']['unresolved_cells'], len(card['judgments']))
        self.assertIn('review_assurance_dependency_pending', [x['code'] for x in report['issues']])

    def test_incompatible_positive_assurance_and_unclassified_finding(self):
        value = epoch()
        tasks, selected = selection(value)
        out = selected['observations'][0]['output']
        out['cells'][0]['status'] = 'contradicted'
        out['terminal']['assurances']['problem_statement'] = 'passed'
        self.assertFalse(self.check(value, tasks, selected)['complete'])
        tasks, selected = selection(value)
        selected['observations'][0]['output']['findings'] = [dict(id='adverse', adverse=True,
                    classification=None, reason='The condition may fail.')]
        selected['observations'][0]['output']['terminal']['assurances']['stop'] = 'passed'
        self.assertFalse(self.check(value, tasks, selected)['complete'])

    def test_new_dependency_invalidates_integrations(self):
        value = epoch()
        tasks, selected = selection(value)
        out = selected['observations'][0]['output']
        out['new_dependencies'] = [{'claim_id': value['manifest']['claims'][0]['id'],
                                    'depends_on': value['manifest']['claims'][1]['id']}]
        self.assertFalse(self.check(value, tasks, selected)['complete'])

    def test_revised_conclusion_requires_original_check(self):
        value = epoch()
        tasks, selected = selection(value)
        disposition = selected['observations'][0]['output']['integrations'][0]['dispositions'][0]
        disposition['disposition'] = 'revised'
        self.assertFalse(self.check(value, tasks, selected)['complete'])

    def test_compact_projection_retains_full_obligation_root(self):
        value = epoch()
        card = full_card(value, conditions=[dict(id='condition', text='Prime inputs.', grounds=[])])
        compact = staged.compact_card(card)
        self.assertEqual(compact['full_card_root'], digest(card))
        self.assertIn('condition', compact['obligation_ids'])
        self.assertEqual(card['conditions'][0]['text'], compact['conditions'][0]['text'])


if __name__ == '__main__':
    unittest.main()
