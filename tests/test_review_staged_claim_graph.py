"""Frame decomposition and context changes remain authoritative across replanning."""

import copy
import unittest

from research_harness import review_staged as staged
from research_harness.evidence import digest
from tests.test_review_staged_fixtures import epoch, full_card, observation, profile, selection, task


class StagedClaimGraphTests(unittest.TestCase):
    def build_frame(self, value, **changes):
        assigned = task(value, 'frame')
        output = dict(claims=[dict(row, kind='claim') for row in value['manifest']['claims']],
                      conditions=[], dependencies=[], unresolved=[])
        output.update(changes)
        return assigned, observation(value, assigned, output)

    def test_frame_unresolved_work_blocks_an_otherwise_complete_assessment(self):
        value = epoch()
        tasks, selected = selection(value)
        assigned, observed = self.build_frame(value, unresolved=[dict(text='The quantifier needs clarification.',
            source_ids=[value['manifest']['frame_fields'][0]['source_id']],
            claim_ids=[value['manifest']['claims'][0]['id']])])
        tasks.append(assigned)
        selected['observations'].append(observed)
        report = staged.verify_completion(value, selected, tasks=tasks)
        self.assertFalse(report['complete'], report['issues'])
        self.assertTrue(any(row['code'] == 'review_frame_unresolved' for row in report['issues']))
        verified = staged.collect_planning_records(value, selected, tasks=tasks)
        self.assertEqual(len(verified['context_graph']['unresolved']), 1)

    def test_frame_dependencies_expand_only_affected_contexts(self):
        value = epoch()
        first, second = value['manifest']['claims']
        assigned, observed = self.build_frame(value, dependencies=[dict(claim_id=first['id'], depends_on=second['id'])])
        verified = staged.collect_planning_records(value, {'observations': [observed]}, tasks=[assigned])
        self.assertNotEqual(verified['contexts'][first['id']]['root'], value['manifest']['contexts'][first['id']]['root'])
        self.assertEqual(verified['contexts'][second['id']], value['manifest']['contexts'][second['id']])
        self.assertEqual(verified['context_graph']['versions'][-1]['observation_id'], observed['id'])

    def test_frame_can_classify_a_mechanically_seeded_field_as_a_condition(self):
        value = epoch()
        first, second = value['manifest']['claims']
        assigned, observed = self.build_frame(value, claims=[dict(second, kind='claim')],
                                             conditions=[dict(first, kind='condition')])
        graph = staged.derive_context_graph(value, {'observations': [observed]}, tasks=[assigned])
        current = staged.bind_context_graph(value, graph)
        self.assertIn(first['id'], staged.collect_condition_ids(current, []))

    def test_frame_claims_and_conditions_become_planned_reading_obligations(self):
        value = epoch()
        assigned, observed = self.build_frame(value)
        observed['output']['claims'].append(dict(id='precise-claim', kind='claim',
            spans=value['manifest']['claims'][0]['spans'], dependencies=['premise'], children=[]))
        observed['output']['conditions'] = [dict(id='premise', kind='condition',
            spans=value['manifest']['claims'][1]['spans'], dependencies=[], children=[])]
        observed['raw_output_digest'] = digest(observed['output'])
        selected = {'observations': [observed]}
        verified = staged.collect_planning_records(value, selected, tasks=[assigned])
        self.assertIn('precise-claim', verified['contexts'])
        self.assertIn('premise', verified['contexts']['precise-claim']['claim_ids'])
        self.assertTrue(any('premise' in seeds for seeds in verified['context_graph']['assurance_dependencies'].values()))
        plan = staged.plan_tasks(value, route_limit=100000, output_tokens=10000,
            profiles={'read': profile()}, tasks=[assigned], selection=selected)
        self.assertEqual(set(plan['tasks'][0]['claim_ids']), set(verified['contexts']))
        self.assertEqual(plan['context_graph']['digest'], verified['context_graph']['digest'])
        # A cold consumer can construct the packet manifest from this exact graph.
        current = staged.bind_context_graph(value, verified['context_graph'])
        self.assertIn('premise', {row['id'] for row in current['claims']})
        self.assertEqual(value['manifest']['claims'], epoch()['manifest']['claims'])

    def test_forged_discovery_cannot_change_the_context_graph(self):
        value = epoch()
        assigned, observed = self.build_frame(value, dependencies=[dict(
            claim_id=value['manifest']['claims'][0]['id'], depends_on=value['manifest']['claims'][1]['id'])])
        observed['raw_output_digest'] = 'not-the-output-digest'
        verified = staged.collect_planning_records(value, {'observations': [observed]}, tasks=[assigned])
        self.assertEqual(verified['contexts'], value['manifest']['contexts'])

    def test_forged_read_discovery_cannot_change_the_context_graph(self):
        value = epoch()
        assigned = task(value)
        observed = observation(value, assigned, dict(cards=[full_card(value)], new_dependencies=[dict(
            claim_id=value['manifest']['claims'][0]['id'], depends_on=value['manifest']['claims'][1]['id'])]))
        observed['raw_output_digest'] = 'not-the-output-digest'
        verified = staged.collect_planning_records(value, {'observations': [observed]}, tasks=[assigned])
        self.assertEqual(verified['contexts'], value['manifest']['contexts'])

    def test_current_projection_does_not_create_an_extra_graph_version(self):
        value = epoch()
        assigned, observed = self.build_frame(value, dependencies=[dict(
            claim_id=value['manifest']['claims'][0]['id'], depends_on=value['manifest']['claims'][1]['id'])])
        selected = {'observations': [observed]}
        graph = staged.derive_context_graph(value, selected, tasks=[assigned])
        repeated = staged.derive_context_graph(value, selected, tasks=[assigned], contexts=graph['contexts'])
        self.assertEqual(repeated, graph)

    def test_only_an_explicit_grounded_answer_discharges_frame_work(self):
        for disposition in ('answered', 'retained', 'unresolved'):
            with self.subTest(disposition=disposition):
                value = epoch()
                tasks, selected = selection(value)
                assigned, observed = self.build_frame(value, unresolved=[dict(text='Clarify the quantifier.',
                    source_ids=[], claim_ids=[value['manifest']['claims'][0]['id']])])
                graph = staged.derive_context_graph(value, {'observations': [observed]}, tasks=[assigned])
                reading = selected['observations'][1]
                combined = task(value, 'combine', card_ids=['card-0'], scope='joint_card',
                                dependencies=[tasks[0]['id'], assigned['id']])
                output = selected['observations'][0]['output']
                output['integrations'][0]['task_id'] = combined['id']
                output['combinations'][0]['task_id'] = combined['id']
                output['answers'] = [dict(id='clarification', finding_id=graph['unresolved'][0]['id'],
                    disposition=disposition, reason='The original quantifier specifies prime inputs.',
                    grounds=value['manifest']['claims'][0]['spans'])]
                output['integrations'][0]['answers'] = ['clarification']
                result = observation(value, combined, output, dependencies={
                    tasks[0]['id']: reading['raw_output_digest'], assigned['id']: observed['raw_output_digest']})
                report = staged.verify_completion(value, {'observations': [result, reading, observed]},
                                                   tasks=[tasks[0], combined, assigned])
                self.assertEqual(report['complete'], disposition == 'answered', report['issues'])

    def test_replanned_context_has_explicit_exact_task_supersession(self):
        value = epoch()
        assigned = task(value)
        discovered = observation(value, assigned, dict(cards=[full_card(value)],
            new_dependencies=[dict(claim_id=value['manifest']['claims'][0]['id'],
                                   depends_on=value['manifest']['claims'][1]['id'])]))
        planned = staged.plan_tasks(value, route_limit=100000, output_tokens=10000,
            profiles={'read': profile()}, tasks=[assigned], selection={'observations': [discovered]})
        replacement = planned['tasks'][0]
        self.assertEqual(replacement['supersedes'], [assigned['id']])
        self.assertNotEqual(replacement['context_roots'], assigned['context_roots'])

    def test_replanned_frame_graph_completes_in_the_same_frozen_epoch(self):
        value = epoch()
        before = copy.deepcopy(value)
        assigned, observed = self.build_frame(value)
        observed['output']['claims'].append(dict(id='precise-claim', kind='claim',
            spans=value['manifest']['claims'][0]['spans'], dependencies=[], children=[]))
        observed['output']['conditions'].append(dict(id='premise', kind='condition',
            spans=value['manifest']['claims'][1]['spans'], dependencies=[], children=[]))
        observed['raw_output_digest'] = digest(observed['output'])
        graph = staged.derive_context_graph(value, {'observations': [observed]}, tasks=[assigned])
        view = dict(value, manifest=staged.bind_context_graph(value, graph))
        card = full_card(view)
        prototypes, selected = selection(value, [card], contexts=graph['contexts'])

        def build_current_task(kind, **options):
            draft = staged.make_task(value, kind, inputs=prototypes[0]['inputs'],
                claim_ids=list(graph['contexts']), occurrence_ids=prototypes[0]['occurrence_ids'],
                measurement=prototypes[0]['measurement'], context_graph=graph, **options)
            return staged.freeze_task(draft, 'request-' + draft['id'], staged.SERIALIZATION)

        reading = build_current_task('read', dependencies=[assigned['id']])
        read = observation(value, reading, selected['observations'][1]['output'],
                           dependencies={assigned['id']: observed['raw_output_digest']})
        combined = build_current_task('combine', card_ids=[card['id']], scope='joint_card', dependencies=[reading['id']])
        output = selected['observations'][0]['output']
        output['integrations'][0].update(task_id=combined['id'], selected_observations=[read['id']],
                                         context_roots=combined['context_roots'])
        output['combinations'][0]['task_id'] = combined['id']
        for check in output['assurance_checks']:
            check['claim_ids'] = list(graph['contexts'])
        output['terminal']['visible_conditions'] = ['premise']
        result = observation(value, combined, output, dependencies={reading['id']: read['raw_output_digest']})
        report = staged.verify_completion(value, {'observations': [result, read, observed]},
                                           tasks=[reading, combined, assigned])
        self.assertTrue(report['complete'], report['issues'])
        self.assertEqual(report['context_graph_root'], graph['digest'])
        self.assertEqual(value, before)
        result['output']['terminal']['visible_conditions'] = []
        result['raw_output_digest'] = digest(result['output'])
        missing = staged.verify_completion(value, {'observations': [result, read, observed]},
                                            tasks=[reading, combined, assigned])
        self.assertFalse(missing['complete'], missing['issues'])

    def test_unrelated_current_task_cannot_supersede_a_historical_card_combination(self):
        value = epoch()
        old = task(value, 'combine', card_ids=['old-card'], scope='joint_card')
        claims = copy.deepcopy(value['manifest']['claims'])
        claims[0]['dependencies'] = [claims[1]['id']]
        contexts = staged.build_contexts(claims, value['manifest']['sources'])
        card = full_card(value)
        for cell in card['judgments']:
            cell['context_root'] = contexts[cell['claim_id']]['root']
        tasks, selected = selection(value, [card], contexts=contexts)
        tasks.append(old)
        report = staged.verify_completion(value, selected, tasks=tasks, contexts=contexts)
        self.assertFalse(report['complete'], report['issues'])
        self.assertTrue(any(row.get('task_id') == old['id'] and row['code'] == 'review_task_dependency_changed'
                            for row in staged.iter_completion_issues(value, selected, tasks=tasks, contexts=contexts)), report['issues'])


if __name__ == '__main__':
    unittest.main()
