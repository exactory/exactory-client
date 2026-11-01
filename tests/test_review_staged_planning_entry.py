"""Planning consumes the current context graph produced by the staged core."""

import unittest
import copy

import test_review_staged_contexts as contexts
from research_harness import review_protocol as protocol
from research_harness import review_staged as staged, review_staged_protocol as producer
from research_harness.errors import ResearchError
from research_harness.evidence import digest
from test_review_staged_fixtures import task, observation


class StagedPlanningEntryTests(unittest.TestCase):
    setUp = contexts.StagedContextTests.setUp
    plan = contexts.StagedContextTests.plan

    def test_frame_dependency_expansion_is_used_by_the_planning_entry(self):
        assigned = task(self.epoch, 'frame')
        claims = self.epoch['manifest']['claims']
        output = dict(claims=[dict(row, kind='claim') for row in claims], conditions=[], unresolved=[], dependencies=[
            dict(claim_id=claims[0]['id'], depends_on=claims[1]['id'])])
        captured = observation(self.epoch, assigned, output)
        self.records['review_assignment']['discoverer']['staged_task'] = assigned
        self.records['review_task'] = {assigned['id']: dict(epoch_id=self.epoch['id'], task=assigned)}
        self.records['review_task_acceptance'] = {'frame': dict(task_id=assigned['id'], receipt=dict(
            attempt=dict(id=captured['id'], assignment_id='discoverer', recorded_revision=1),
            staged_observation=captured))}
        planned = self.plan(task(self.epoch, contexts=self.contexts, dependencies=[assigned['id']]))
        packet = protocol._read_json(self.artifacts, planned['packet'])
        self.assertEqual(packet['staged_review']['task']['context_roots'],
                         {key:row['root'] for key,row in self.contexts.items()})
        self.assertEqual(packet['staged_review']['context_graph_root'], planned['context_graph']['root'])
        self.assertEqual(self.epoch, self.before)

    def test_new_frame_claim_is_delivered_and_persisted_with_its_graph(self):
        assigned = task(self.epoch, 'frame')
        claims = [dict(row, kind='claim') for row in self.epoch['manifest']['claims']]
        claims.append(dict(copy.deepcopy(claims[0]), id='reviewer-claim'))
        captured = observation(self.epoch, assigned, dict(claims=claims, conditions=[], dependencies=[], unresolved=[]))
        self.records['review_assignment']['discoverer']['staged_task'] = assigned
        self.records['review_task'] = {assigned['id']: dict(epoch_id=self.epoch['id'], task=assigned)}
        self.records['review_task_acceptance'] = {'frame': dict(task_id=assigned['id'], receipt=dict(
            attempt=dict(id=captured['id'], assignment_id='discoverer', recorded_revision=1),
            staged_observation=captured))}
        graph = staged.derive_context_graph(self.epoch, dict(observations=[captured]), tasks=[assigned])
        read = staged.make_task(self.epoch, 'read', inputs=assigned['inputs'], claim_ids=['reviewer-claim'],
            occurrence_ids=assigned['occurrence_ids'], dependencies=[assigned['id']], contexts=graph['contexts'])
        planned = self.plan(read)
        packet = protocol._read_json(self.artifacts, planned['packet'])['staged_review']
        self.assertEqual([row['id'] for row in packet['claims']], ['reviewer-claim'])
        self.assertEqual(planned['context_graph']['root'], graph['root'])
        self.assertTrue(planned['prompt']['parameters']['claim_range'])
        partitions = producer.split_task(self.epoch, read, contexts=graph['contexts'])
        self.assertEqual(len(partitions), 2)
        for partition in partitions:
            self.assertEqual(partition['context_roots'], read['context_roots'])
        final = staged.make_task(self.epoch, 'final', inputs=assigned['inputs'],
            claim_ids=list(graph['contexts']), dependencies=[assigned['id']], contexts=graph['contexts'])
        final_plan = self.plan(final)
        final_packet = protocol._read_json(self.artifacts, final_plan['packet'])['staged_review']
        self.assertIn('reviewer-claim', [row['id'] for row in final_packet['final']['claims']])
        self.assertEqual(self.epoch, self.before)

    def test_planner_supersession_references_survive_protocol_dependency_remapping(self):
        from test_review_staged_fixtures import profile
        self.controller['staged_review'] = dict(epoch_id=self.epoch['id'])
        planned = producer.plan_remaining_tasks(self.records, self.artifacts, self.controller,
                                                profiles={'read':profile()})
        replacements = {row['id']:row for row in planned['tasks']}
        self.assertTrue(planned['task_supersessions'])
        for supersession in planned['task_supersessions']:
            self.assertTrue(supersession['replacement_task_ids'])
            for identifier in supersession['replacement_task_ids']:
                self.assertIn(identifier, replacements)
                self.assertIn(supersession['task_id'], replacements[identifier]['supersedes'])

    def test_prepared_task_cannot_be_assigned_after_its_context_became_stale(self):
        accepted = self.records.pop('review_task_acceptance')
        planned = self.plan(task(self.epoch, 'original_check'))
        self.records['review_task_acceptance'] = accepted
        self.records['review_task'][planned['id']] = planned
        self.controller.update(role='result', reviewer_id='reviewer-A', author_id='author', dossier_id=None,
            slot_id='A', subject=self.epoch['subject'], route_digest=digest(self.route), context_state={},
            staged_review=dict(kind='controller', protocol=staged.PROTOCOL, epoch_id=self.epoch['id']))
        self.records['review_assignment'][self.controller['id']] = self.controller
        self.records['review_slot_head'] = {'A': dict(epoch_id=self.epoch['id'])}
        value = {key:self.controller[key] for key in ('role', 'reviewer_id', 'author_id', 'route_id', 'dossier_id')}
        value.update(id='stale-child', context=dict(controller_assignment_id=self.controller['id'],
                                                   task_id=planned['id']))
        with self.assertRaises(ResearchError) as caught:
            producer.prepare_child(self.records, self.artifacts, value, 2)
        self.assertEqual(caught.exception.code, 'review_task_dependency_changed')


if __name__ == '__main__':
    unittest.main()
