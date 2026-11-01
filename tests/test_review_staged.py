"""Frozen identity, exact coverage, task scope, progress, and finite outcomes."""

import copy
import unittest

from research_harness import review_staged as staged
from research_harness.errors import ResearchError
from research_harness.evidence import digest
from test_review_staged_fixtures import epoch, profile, task, observation, selection


class StagedReviewTests(unittest.TestCase):
    def test_objection_resolution_uses_the_0693_packet_contract(self):
        from research_harness.review_objection_evidence import resolve_objection_entry
        self.assertIs(staged.resolve_objection_entry, resolve_objection_entry)
        full = {'cite': {'id': 'source'}, 'content': 'Exact original evidence.'}
        packet = {'material_objections': [{'id': 'O1', 'version_evidence': [full]}]}
        alias = {'delivery': 'repeated-in-material-objections', 'objection_id': 'O1',
                 'field': 'version_evidence', 'index': 0, 'digest': digest(full)}
        before = copy.deepcopy(packet)
        self.assertEqual(staged.resolve_objection_entry(packet, alias), full)
        self.assertEqual(packet, before)

    def test_completion_roots_bind_the_versioned_bounded_diagnostics(self):
        value = epoch()
        tasks, selected = selection(value)
        complete = staged.verify_completion(value, selected, tasks=tasks)
        incomplete = staged.verify_completion(value, {'observations': []}, tasks=tasks)
        self.assertTrue(complete['complete'])
        self.assertFalse(incomplete['complete'])
        self.assertEqual(complete['schema'], 'exactory-review-completion-v2')
        self.assertEqual(complete['digest'], '2c607ab81f888dbaa27755cb0251e55d64dba750391a4cb34ec77e3896b27ded')
        self.assertEqual(incomplete['digest'], '42d68702b97600624b4585e8c60a2af8a41b3d60eda8ad0db83843271d568559')

    def test_occurrence_identity_includes_position_and_full_descriptor(self):
        items = [{'key': 'readings', 'index': i, 'item': {'same': True}, 'artifacts': [], 'source_ids': []}
                 for i in range(2)]
        manifest = staged.build_manifest({'statement': 'x'}, items, [])
        self.assertNotEqual(manifest['occurrences'][0]['id'], manifest['occurrences'][1]['id'])
        self.assertEqual(manifest, staged.build_manifest({'statement': 'x'}, items, []))
        changed = copy.deepcopy(items)
        changed[0]['artifacts'] = [{'sha256': 'a', 'path': 'one', 'media_type': 'text/plain'}]
        with self.assertRaises(ResearchError):
            staged.build_manifest({'statement': 'x'}, changed, [])

    def test_descriptor_dedup_preserves_owners_and_distinct_descriptors(self):
        descriptor = {'sha256': 'body', 'path': 'body', 'media_type': 'text/plain'}
        sources = [dict(id='body', kind='artifact', descriptor=descriptor, text='abc', identity={'id': 'body'})]
        items = [dict(key='readings', index=i, item={'i': i}, artifacts=[descriptor], source_ids=['body'])
                 for i in range(2)]
        manifest = staged.build_manifest({}, items, sources)
        self.assertEqual(len(manifest['bodies']), 1)
        self.assertEqual(len(manifest['bodies'][0]['occurrence_ids']), 2)
        sources.append(dict(id='other', kind='artifact', descriptor=dict(descriptor, path='other'),
                            text='abc', identity={'id': 'other'}))
        items[1]['artifacts'] = [sources[-1]['descriptor']]
        items[1]['source_ids'] = ['other']
        self.assertEqual(len(staged.build_manifest({}, items, sources)['bodies']), 2)

    def test_unicode_spans_and_exact_interval_union(self):
        source = staged.make_source('source', 'source_document', 'αβ🙂 delta', identity={'version': 1})
        span = staged.source_span(source, 1, 3)
        self.assertEqual(staged.materialize_quotation(source, span)['text'], 'β🙂')
        self.assertEqual(staged.interval_gaps([[0, 8]], [[4, 8], [0, 3], [2, 5]]), [])
        self.assertEqual(staged.interval_gaps([[0, 8]], [[0, 3], [4, 8]]), [[3, 4]])
        altered = dict(span, sha256='altered')
        with self.assertRaises(ResearchError):
            staged.materialize_quotation(source, altered)
        with self.assertRaises(ResearchError):
            staged.materialize_quotation(source, span, delivered=[dict(span, end=2)])

    def test_source_and_claim_partitioning_exact_union(self):
        value = epoch()
        plans = staged.plan_tasks(value, route_limit=130, output_tokens=17,
                                  profiles={'read': profile(output_tokens_per_claim=10)})
        reads = [t for t in plans['tasks'] if t['type'] == 'read']
        self.assertGreater(len(reads), 1)
        for source in value['manifest']['sources']:
            delivered = [[s['start'], s['end']] for t in reads for s in t['inputs'] if s['source_id'] == source['id']]
            self.assertEqual(staged.interval_gaps(source['required_intervals'], delivered), [])
        for occurrence in value['manifest']['occurrences']:
            for claim in value['manifest']['claims']:
                portions = [s for t in reads if claim['id'] in t['claim_ids'] and occurrence['id'] in t['occurrence_ids']
                            for s in t['inputs'] if s['source_id'] in occurrence['source_ids']]
                self.assertTrue(portions)
        self.assertTrue(all(len(t['claim_ids']) == 1 for t in reads))

    def test_dependency_expansion_invalidates_only_affected_context(self):
        value = epoch()
        claims = copy.deepcopy(value['manifest']['claims'])
        claims[0]['dependencies'] = [claims[1]['id']]
        contexts = staged.build_contexts(claims, value['manifest']['sources'])
        tasks, selected = selection(value)
        report = staged.verify_completion(value, selected, tasks=tasks, contexts=contexts)
        self.assertFalse(report['complete'])
        self.assertIn('review_task_dependency_changed', [x['code'] for x in report['issues']])
        self.assertEqual(contexts[claims[1]['id']], value['manifest']['contexts'][claims[1]['id']])

    def test_frozen_epoch_is_a_copy_and_detects_tampering(self):
        value = epoch()
        before = copy.deepcopy(value)
        tasks, selected = selection(value)
        staged.verify_completion(value, selected, tasks=tasks)
        self.assertEqual(value, before)
        value['reviewer_id'] = 'other'
        with self.assertRaises(ResearchError):
            staged.verify_completion(value, selected, tasks=tasks)

    def test_exact_progress_repeated_delivery_adds_no_credit(self):
        value = epoch()
        tasks, selected = selection(value)
        report = staged.verify_completion(value, selected, tasks=tasks)
        self.assertTrue(report['complete'], report['issues'])
        repeated = copy.deepcopy(selected)
        repeated['observations'].append(copy.deepcopy(repeated['observations'][0]))
        again = staged.verify_completion(value, repeated, tasks=tasks)
        self.assertEqual(report['progress'], again['progress'])
        self.assertEqual(report['selected_observations_root'], again['selected_observations_root'])
        changed = copy.deepcopy(selected)
        changed['observations'][0]['actual_input']['intervals'].pop()
        incomplete = staged.verify_completion(value, changed, tasks=tasks)
        self.assertFalse(incomplete['complete'])

    def test_joint_original_needs_owned_body_again(self):
        value = epoch()
        tasks, selected = selection(value)
        selected['observations'][0]['actual_input']['intervals'] = []
        cell = selected['observations'][0]['output']['cells'][0]
        with self.assertRaises(ResearchError):
            staged.validate_cell(cell, value['manifest'], actual_input=selected['observations'][0]['actual_input'])
        report = staged.verify_completion(value, selected, tasks=tasks)
        self.assertFalse(report['complete'])
        self.assertEqual(report['progress']['claim_cells'], 2)

    def test_finite_exhaustion_and_next_payloads(self):
        value = epoch()
        assigned = task(value)
        ledger = dict(attempts=2, retries=1, rereads=0, subdivisions=0, epochs=0,
                      elapsed_seconds=10, unknown_usage=True)
        state = staged.task_state(value, assigned, ledger=ledger, failures=2)
        self.assertEqual(state['state'], 'exhausted')
        self.assertFalse(any(x.get('command') == 'review-run' for x in state['next']))
        ready = staged.task_state(value, assigned)
        self.assertEqual(ready['next'][0]['payload'], {'assignment_id': 'assignment-A', 'epoch_id': 'epoch-A',
                                                    'task_id': assigned['id']})
        captured = staged.task_state(value, assigned, captured=True)
        self.assertEqual(captured['code'], 'review_observation_acceptance_pending')
        running = staged.task_state(value, assigned, worker_unreconciled=True)
        self.assertEqual(running['code'], 'review_worker_reconciliation_required')

    def test_incomplete_shape_slot_occupancy_and_pending_successor(self):
        value = epoch()
        report = staged.verify_completion(value, {'observations': []})
        unresolved = [dict(id='missing', scope={'claim_ids': ['c']}, cause='exhausted',
                           affected_decisions=['problem_value'], exhausted_tasks=['read'], scope_kind='unresolved',
                           next_action={'workflow': 'problem-evidence', 'scope': {'evidence': 'new source'}, 'prerequisite': 'new evidence'})]
        outcome = staged.record_incomplete(value, report, origin='execution',
                    origin_binding={'execution_receipts': ['terminated'], 'reason': 'exhausted'},
                    unresolved_scope=unresolved, recovery_work=[dict(workflow='problem-evidence', command='problem-evidence', payload={'evidence': 'new source'}, text='Supply the missing source.')],
                    resource_ledger={'attempts': 2}, recorded_revision=4)
        self.assertEqual(outcome['kind'], 'assessment_incomplete')
        self.assertNotIn('value_review', outcome)
        heads = [dict(slot_id='A', epoch_id=value['id'], state='incomplete', outcome=outcome),
                 dict(slot_id='B', epoch_id='B', state='complete', outcome={'id': 'review-B'})]
        slots = staged.collect_slot_outcomes(heads)
        self.assertEqual(slots['occupied'], 2)
        self.assertEqual(len(slots['incomplete']), 1)
        self.assertFalse(slots['ready'])
        heads[0] = dict(slot_id='A', epoch_id='successor', state='pending', outcome=None,
                        inherited_adverse_findings=['finding'])
        slots = staged.collect_slot_outcomes(heads)
        self.assertFalse(slots['ready'])
        self.assertEqual(slots['adverse_findings'], ['finding'])
        with self.assertRaises(ResearchError):
            staged.record_incomplete(value, report, origin='reviewer', origin_binding={},
                                     unresolved_scope=unresolved, recovery_work=[], resource_ledger={})

    def test_unobserved_output_and_nonempty_history_rejected(self):
        value = epoch()
        tasks, selected = selection(value)
        selected['observations'][0]['actual_input']['history'] = ['old output']
        self.assertFalse(staged.verify_completion(value, selected, tasks=tasks)['complete'])
        selected['observations'][0]['raw_output_digest'] = digest({})
        self.assertFalse(staged.verify_completion(value, selected, tasks=tasks)['complete'])


if __name__ == '__main__':
    unittest.main()
