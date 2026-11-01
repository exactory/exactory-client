"""Automatic rereads retain exact original scope and a shared finite budget."""

import copy
import unittest

from research_harness import review_composition as composition, review_staged as staged
from research_harness import review_staged_followups as followups
from research_harness.evidence import digest
from research_harness.errors import ResearchError
from test_review_staged_fixtures import epoch, full_card, observation, profile, task
from test_review_composition import SOURCE, make_fixture, worked_adjudication, worked_result


def freeze(assigned):
    measured = staged.joint_fit([row['end'] - row['start'] for row in assigned['inputs']],
                                100000, 10000, profile(), claim_count=len(assigned['claim_ids']))
    return staged.freeze_task(assigned, digest(['rendered', assigned['id']]), staged.SERIALIZATION,
                              measurement=measured)


def request(assigned, identifier='retrieve', question='Does the original state the hypothesis?'):
    return dict(id=identifier, question=question, inputs=copy.deepcopy(assigned['inputs']),
                claim_ids=assigned['claim_ids'][:], context_roots=assigned['context_roots'].copy(),
                status='pending', answer=None, observation_ids=[], requested_by=assigned['id'])


class RetrievalFollowupTests(unittest.TestCase):
    def setUp(self):
        self.value = epoch()
        self.requester = task(self.value, 'final')
        self.pending = request(self.requester)
        self.first = observation(self.value, self.requester, {'retrievals': [self.pending]})

    def plan(self, observations=None, tasks=None):
        return followups.plan_retrieval_tasks(self.value, observations or [self.first], tasks or [self.requester])

    def reread(self):
        plan = self.plan()
        reading = freeze(plan['tasks'][0])
        read = observation(self.value, reading, {'cards': [full_card(self.value)]},
                           dependencies={self.requester['id']: self.first['raw_output_digest']})
        return reading, read

    def test_pending_request_creates_an_exact_original_read_depending_on_the_request(self):
        plan = self.plan()
        self.assertFalse(plan['exhausted'])
        self.assertEqual(len(plan['tasks']), 1)
        reading = plan['tasks'][0]
        self.assertEqual(reading['type'], 'read')
        self.assertEqual(reading['dependencies'], [self.requester['id']])
        self.assertEqual({digest(row) for row in reading['inputs']}, {digest(row) for row in self.pending['inputs']})
        self.assertEqual(reading['claim_ids'], self.pending['claim_ids'])
        self.assertEqual(reading['context_roots'], self.pending['context_roots'])
        self.assertEqual(reading['occurrence_ids'], [row['id'] for row in self.value['manifest']['occurrences']])
        binding = plan['bindings'][reading['id']]
        self.assertEqual(binding['kind'], 'read')
        self.assertEqual(binding['retrievals'], [self.pending])
        self.assertEqual(binding['observation_ids'], [])
        self.assertEqual(self.plan()['tasks'], plan['tasks'])

    def test_an_active_read_is_reused_without_allocating_another_reread(self):
        reading = freeze(self.plan()['tasks'][0])
        plan = self.plan(tasks=[self.requester, reading])
        self.assertEqual([row['id'] for row in plan['tasks']], [reading['id']])
        self.assertFalse(plan['exhausted'])

    def test_observed_rereading_creates_a_resolution_final_bound_to_request_and_read(self):
        reading, read = self.reread()
        plan = self.plan([self.first, read], [self.requester, reading])
        resolve = plan['tasks'][0]
        self.assertEqual(resolve['type'], 'final')
        self.assertEqual(resolve['scope'], 'segmented_original')
        self.assertEqual(set(resolve['dependencies']), {self.requester['id'], reading['id']})
        self.assertEqual(resolve['inputs'], [])
        self.assertEqual(plan['bindings'][resolve['id']]['kind'], 'resolve')
        self.assertEqual(plan['bindings'][resolve['id']]['retrievals'], [self.pending])
        self.assertEqual(plan['bindings'][resolve['id']]['observation_ids'], [read['id']])

    def test_a_narrow_reread_keeps_exact_inputs_and_states_its_segmented_scope(self):
        source = self.value['manifest']['occurrences'][0]['item_source_id']
        self.pending['inputs'] = [row for row in self.pending['inputs'] if row['source_id'] == source]
        self.first = observation(self.value, self.requester, {'retrievals': [self.pending]})
        plan = self.plan()
        reading = plan['tasks'][0]
        self.assertEqual(reading['inputs'], self.pending['inputs'])
        self.assertEqual(reading['scope'], 'original_segment')

    def test_frame_only_reread_renders_and_accepts_the_frame_output_schema(self):
        from research_harness import review_staged_protocol as producer
        frame_sources = {row['source_id'] for row in self.value['manifest']['frame_fields']}
        self.pending['inputs'] = [row for row in self.pending['inputs'] if row['source_id'] in frame_sources]
        self.first = observation(self.value, self.requester, {'retrievals': [self.pending]})
        plan = self.plan()
        reading = freeze(plan['tasks'][0])
        self.assertEqual(reading['type'], 'frame')
        self.assertEqual(reading['occurrence_ids'], [])
        self.assertEqual(plan['bindings'][reading['id']]['kind'], 'read')
        packet = producer.build_packet(self.value, reading, observations=[self.first])
        fields = packet['staged_review']['frame']['fields']
        self.assertEqual({digest(row['span']) for row in fields}, {digest(row) for row in self.pending['inputs']})
        output = dict(claims=[dict(id='reread-' + str(index), kind='record', spans=[row['span']],
            dependencies=[], children=[]) for index, row in enumerate(fields)],
            conditions=[], dependencies=[], unresolved=[])
        staged.validate_task_output(reading, output, manifest=self.value['manifest'])
        read = observation(self.value, reading, output,
                           dependencies={self.requester['id']: self.first['raw_output_digest']})
        result = self.plan([self.first, read], [self.requester, reading])
        resolve = result['tasks'][0]
        self.assertEqual(resolve['type'], 'final')
        self.assertEqual(result['bindings'][resolve['id']]['observation_ids'], [read['id']])

    def test_bound_resolution_removes_the_retrieval_frontier(self):
        reading, read = self.reread()
        plan = self.plan([self.first, read], [self.requester, reading])
        resolve = freeze(plan['tasks'][0])
        receipt = dict(self.pending, status='resolved', answer='The hypothesis is explicit.', observation_ids=[read['id']])
        answered = observation(self.value, resolve, {'retrievals': [receipt]}, dependencies={
            self.requester['id']: self.first['raw_output_digest'], reading['id']: read['raw_output_digest']})
        result = self.plan([answered, read, self.first], [self.requester, reading, resolve])
        self.assertEqual(result['tasks'], [])
        self.assertEqual(result['unresolved'], [])
        self.assertFalse(result['exhausted'])

    def test_duplicate_question_and_source_version_share_one_read(self):
        other = task(self.value, 'final', dependencies=[self.requester['id']])
        duplicate = request(other, 'renamed-request', '  DOES the original state the hypothesis?  ')
        observed = observation(self.value, other, {'retrievals': [duplicate]})
        plan = self.plan([observed, self.first], [self.requester, other])
        self.assertEqual(len(plan['tasks']), 1)
        reading = plan['tasks'][0]
        self.assertEqual(set(reading['dependencies']), {self.requester['id'], other['id']})
        self.assertEqual({row['id'] for row in plan['bindings'][reading['id']]['retrievals']},
                         {'retrieve', 'renamed-request'})

    def test_identical_text_in_distinct_source_versions_keeps_separate_obligations(self):
        sources = [staged.make_source('version-' + version, 'source_document', 'The same visible text.',
                    identity={'source_id': 'paper', 'version': version}) for version in ('one', 'two')]
        manifest = staged.build_manifest({'statement': 'The claim.'},
            [dict(key='readings', index=0, item={'id': 'reading'}, artifacts=[],
                  source_ids=[row['id'] for row in sources])], sources)
        value = epoch(manifest=manifest)
        first = task(value, 'final')
        other = task(value, 'final', dependencies=[first['id']])
        requests = [request(assigned, 'request-' + str(index)) for index, assigned in enumerate((first, other))]
        for receipt, source in zip(requests, sources):
            receipt['inputs'] = [staged.source_span(source, 0, len(source['text']))]
        observed = [observation(value, assigned, {'retrievals': [receipt]})
                    for assigned, receipt in zip((first, other), requests)]
        result = followups.plan_retrieval_tasks(value, observed, [first, other])
        self.assertEqual(len(result['tasks']), 2)
        self.assertEqual(len({row['obligation_id'] for row in result['bindings'].values()}), 2)

    def test_missing_actual_original_scope_does_not_authorize_a_resolution(self):
        reading, read = self.reread()
        read['actual_input']['intervals'] = []
        result = self.plan([self.first, read], [self.requester, reading])
        self.assertEqual(result['tasks'][0]['type'], 'read')
        self.assertNotEqual(result['tasks'][0]['id'], reading['id'])
        self.assertFalse(result['exhausted'])

    def test_changed_request_ids_cannot_reset_the_same_question_reread_cap(self):
        observations, tasks = [self.first], [self.requester]
        for index in range(2):
            result = self.plan(observations, tasks)
            reading = freeze(result['tasks'][0])
            read = observation(self.value, reading, {'cards': [full_card(self.value)]})
            read['actual_input']['intervals'] = []
            tasks.append(reading)
            observations.append(read)
            other = task(self.value, 'final', dependencies=[reading['id']])
            observations.append(observation(self.value, other, {'retrievals': [request(other, 'renamed-' + str(index))]}))
            tasks.append(other)
        result = self.plan(observations, tasks)
        self.assertTrue(result['exhausted'])
        self.assertEqual(result['tasks'], [])
        self.assertEqual(result['unresolved'][0]['cause'], 'reread_limit')
        self.assertEqual(result['unresolved'][0]['rereads'], 2)
        self.assertEqual(set(result['unresolved'][0]['retrieval_ids']), {'retrieve', 'renamed-0', 'renamed-1'})

    def test_a_reviewer_unresolved_answer_becomes_explicit_incomplete_scope(self):
        reading, read = self.reread()
        plan = self.plan([self.first, read], [self.requester, reading])
        resolve = freeze(plan['tasks'][0])
        receipt = dict(self.pending, status='unresolved', answer='The original leaves this undecided.', observation_ids=[read['id']])
        answered = observation(self.value, resolve, {'retrievals': [receipt]})
        result = self.plan([self.first, read, answered], [self.requester, reading, resolve])
        self.assertTrue(result['exhausted'])
        self.assertEqual(result['tasks'], [])
        self.assertEqual(result['unresolved'][0]['cause'], 'reviewer_unresolved')
        self.assertEqual(result['unresolved'][0]['inputs'], self.pending['inputs'])

    def test_resolution_cannot_borrow_the_requesting_observation_as_a_reread(self):
        resolved = dict(self.pending, status='resolved', answer='Claimed without rereading.', observation_ids=[self.first['id']])
        other = task(self.value, 'final', dependencies=[self.requester['id']])
        observed = observation(self.value, other, {'retrievals': [resolved]})
        with self.assertRaises(ResearchError):
            self.plan([self.first, observed], [self.requester, other])

    def test_conflicting_lifecycle_scope_is_rejected(self):
        changed = dict(self.pending, question='A different question.', status='unresolved')
        other = task(self.value, 'final', dependencies=[self.requester['id']])
        observed = observation(self.value, other, {'retrievals': [changed]})
        with self.assertRaises(ResearchError):
            self.plan([self.first, observed], [self.requester, other])

    def test_an_earlier_unrelated_read_cannot_close_a_later_request(self):
        earlier = task(self.value)
        read = observation(self.value, earlier, {'cards': [full_card(self.value)]})
        resolved = dict(self.pending, status='resolved', answer='Claimed from an earlier read.', observation_ids=[read['id']])
        other = task(self.value, 'final', dependencies=[self.requester['id'], earlier['id']])
        answered = observation(self.value, other, {'retrievals': [resolved]})
        with self.assertRaises(ResearchError):
            self.plan([read, self.first, answered], [earlier, self.requester, other])

    def test_source_subdivision_shares_one_reread_round(self):
        planned = self.plan()['tasks'][0]
        children, observed = [], []
        for index, span in enumerate(planned['inputs']):
            child = freeze(staged.make_task(self.value, 'read', inputs=[span], claim_ids=planned['claim_ids'],
                occurrence_ids=planned['occurrence_ids'], dependencies=planned['dependencies'],
                obligation_ids=planned['obligation_ids']))
            children.append(child)
            observed.append(observation(self.value, child, {'cards': [full_card(self.value)]},
                dependencies={self.requester['id']: self.first['raw_output_digest']}))
        result = self.plan([self.first] + observed, [self.requester] + children)
        self.assertFalse(result['exhausted'])
        resolve = result['tasks'][0]
        self.assertEqual(resolve['type'], 'final')
        self.assertEqual(result['bindings'][resolve['id']]['reread'], 1)
        self.assertEqual(set(result['bindings'][resolve['id']]['observation_ids']), {row['id'] for row in observed})

    def test_an_unanswered_resolution_uses_the_next_bounded_reread_round(self):
        reading, read = self.reread()
        plan = self.plan([self.first, read], [self.requester, reading])
        resolve = freeze(plan['tasks'][0])
        unanswered = observation(self.value, resolve, {'retrievals': [self.pending]})
        result = self.plan([self.first, read, unanswered], [self.requester, reading, resolve])
        self.assertEqual(result['tasks'][0]['type'], 'read')
        self.assertEqual(result['bindings'][result['tasks'][0]['id']]['reread'], 2)
        self.assertIn(resolve['id'], result['tasks'][0]['dependencies'])

    def test_missing_claim_context_requires_rereading_that_claim(self):
        reading, read = self.reread()
        read['actual_input']['context_roots'] = {}
        result = self.plan([self.first, read], [self.requester, reading])
        self.assertEqual(result['tasks'][0]['type'], 'read')
        self.assertEqual(result['bindings'][result['tasks'][0]['id']]['reread'], 2)


class ScientificCompositionFollowupTests(unittest.TestCase):
    def prepare_output(self, output=None, role='result'):
        output = copy.deepcopy(output if output is not None else worked_result())
        original_plan, _, choices, _ = make_fixture(output, role)
        changes = dict(role=role, completion_requirements={'permissible_decisions': {
            key: [value] for key, value in choices.items()}})
        if role == 'adjudicator':
            changes['focused_scope'] = dict(review_pair_roots=[digest('first'), digest('second')],
                objection_id='O7', objection_version=digest('version'))
        value = epoch(**changes)
        observed = observation(value, task(value, 'final'), output)
        bindings = {key: copy.deepcopy(original_plan[key]) for key in
                    ('evidence_currency', 'source_root', 'dependency_root', 'selected_observations_root',
                     'integration_root', 'findings', 'evidence_obligations')}
        return value, bindings, [observed], output

    def assemble(self, plan, observations):
        fragments = followups.read_composition_source_fragments(plan, observations)
        pages = []
        for index, page_id in enumerate(plan['required_pages']):
            owners = [owner for owner in plan['ownership'] if owner['page_id'] == page_id]
            findings = plan['findings'] if index == 0 else []
            page = dict(protocol=composition.PROTOCOL, epoch_id=plan['epoch_id'], role=plan['role'],
                task_id='answer-' + page_id, page_id=page_id, revision=0, source_root=plan['source_root'],
                dependency_root=plan['dependency_root'], ownership=owners,
                fragments=[dict(row, grounds=[SOURCE]) for row in fragments if row['page_id'] == page_id],
                grounding=[SOURCE], findings=findings, dispositions=[dict(finding_id=key, status='retained',
                    reason='Retain the original adverse finding.', grounds=[SOURCE]) for key in findings], replaces=None)
            pages.append(dict(attempt_id='attempt-' + page_id, raw_output_digest=digest(page), output=page))
        root = composition.build_root(plan, pages)
        terminal = dict(protocol=composition.PROTOCOL, epoch_id=plan['epoch_id'], role=plan['role'],
                        composition_root=root['digest'], choices={key: values[0] for key, values in plan['terminal_choices'].items()})
        return composition.assemble_result(plan, pages, root, terminal)

    def test_result_pages_reassemble_every_original_field_with_exact_unicode_and_empty_lists(self):
        output = worked_result()
        output['value']['reason'] = ('The prime restriction holds.\n漢字 and e\u0301 remain exact. ' * 80)
        output['limitations'] = ['Repeated limitation.', 'Repeated limitation.']
        output['excerpts'] = []
        value, bindings, observations, expected = self.prepare_output(output)
        plan = followups.build_composition_plan(value, bindings, observations, page_characters=512)
        self.assertGreater(len(plan['required_pages']), 1)
        self.assertEqual(plan['terminal_choices']['/value/status'], ['insufficient'])
        self.assertEqual(plan['findings'], ['O7'])
        self.assertEqual(plan['selected_observations_root'], bindings['selected_observations_root'])
        self.assertEqual(self.assemble(plan, observations), expected)

    def test_focused_pages_preserve_the_objection_and_scalar_disposition(self):
        value, bindings, observations, expected = self.prepare_output(worked_adjudication(), 'adjudicator')
        plan = followups.build_composition_plan(value, bindings, observations, page_characters=512)
        self.assertEqual(plan['role'], 'adjudicator')
        self.assertEqual(plan['terminal_choices']['/objection_id'], ['O7'])
        self.assertEqual(self.assemble(plan, observations), expected)

    def test_an_oversized_atomic_object_requires_a_smaller_scientific_scope(self):
        output = worked_result()
        output['objections'] = [dict(id='large', reason='x' * 1000)]
        value, bindings, observations, _ = self.prepare_output(output)
        with self.assertRaises(ResearchError) as raised:
            followups.build_composition_plan(value, bindings, observations, page_characters=512)
        self.assertEqual(raised.exception.code, 'review_composition_pending')

    def test_missing_observed_scientific_output_stays_pending(self):
        value, bindings, _, _ = self.prepare_output()
        with self.assertRaises(ResearchError) as raised:
            followups.build_composition_plan(value, bindings, [], page_characters=512)
        self.assertEqual(raised.exception.code, 'review_composition_pending')

    def test_conflicting_observed_scientific_outputs_need_explicit_reconciliation(self):
        value, bindings, observations, _ = self.prepare_output()
        other = copy.deepcopy(observations[0])
        other['id'] = 'different-final'
        other['output']['value']['reason'] = 'A different scientific conclusion.'
        with self.assertRaises(ResearchError) as raised:
            followups.build_composition_plan(value, bindings, observations + [other], page_characters=512)
        self.assertEqual(raised.exception.code, 'review_composition_conflict')

    def test_a_scalar_outside_integrated_choices_cannot_be_authorized(self):
        value, bindings, observations, _ = self.prepare_output()
        observations[0]['output']['value']['status'] = 'sufficient'
        with self.assertRaises(ResearchError) as raised:
            followups.build_composition_plan(value, bindings, observations, page_characters=512)
        self.assertEqual(raised.exception.code, 'review_composition_conflict')

    def test_nonempty_work_items_cannot_be_silently_lost_in_composition(self):
        output = worked_result()
        output['value']['work_items'] = [{'kind': 'research', 'reason': 'Keep this requested work.'}]
        value, bindings, observations, _ = self.prepare_output(output)
        with self.assertRaises(ResearchError) as raised:
            followups.build_composition_plan(value, bindings, observations, page_characters=512)
        self.assertEqual(raised.exception.code, 'review_composition_conflict')
