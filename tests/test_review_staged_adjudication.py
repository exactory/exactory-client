"""Focused adjudication consumes effective science and preserves its frozen scope."""

import copy
import unittest
from unittest.mock import patch

from research_harness import direction_reviews, research_decisions as decisions
from research_harness import review_composition as composition, review_protocol as protocol
from research_harness import review_staged as staged, review_supplied_evidence as supplied
from research_harness.errors import ResearchError
from research_harness.evidence import digest
import test_research_problem_adjudication_contract as contract_fixtures
from test_review_composition import make_fixture, observe, terminal_for
from test_review_staged_fixtures import epoch


class StagedAdjudicationTests(unittest.TestCase):
    def setUp(self):
        self.case = contract_fixtures.ProblemAdjudicationContractTests(methodName='runTest')
        self.case.setUp()
        self.addCleanup(self.case.doCleanups)
        self.addCleanup(self.case.tearDown)
        self.case.prepare_reviews(result_reviewer='first')
        self.artifacts = self.case.artifacts

    def compose(self, identifier, output, role='adjudicator'):
        plan, pages, choices, _ = make_fixture(output, role=role)
        root = composition.build_root(plan, pages)
        return composition.effective_review_output(identifier, dict(controller_id=identifier, kind='paged',
            plan=plan, observations=pages, root=root, terminal=observe(terminal_for(plan, root, choices), 'terminal'),
            completion_root=digest('completed assessment')))

    def prepare_finding(self, disposition='not_upheld', paged=True):
        self.new_source = self.case.add_new_source()
        self.assignment = self.case.assign_focused_review()
        self.packet = protocol._read_json(self.artifacts, self.assignment['packet'])
        self.output = dict(objection_id=self.packet['objection']['id'], disposition=disposition,
            correction_admissible=None, reason='Current evidence resolves the originally unsupported implication.',
            evidence=[self.new_source])
        self.records = self.case.read_records()
        self.effective = (self.compose(self.assignment['id'], self.output) if paged else
            composition.effective_review_output(self.assignment['id'], dict(controller_id=self.assignment['id'],
                kind='ordinary', output=self.output, output_digest=digest(self.output), terminal_attempt_id='terminal',
                terminal_raw_output_digest=digest({'terminal': 'raw authorization'}))))
        self.state = dict(ready=True, role='adjudicator', reviewer_id=self.assignment['reviewer_id'], dossier_id=None,
            obligations=[], output={'terminal': 'raw authorization'}, output_digest=digest('raw'),
            effective=self.effective, attempt_id='terminal')
        self.assignment = dict(self.assignment, staged_review=dict(kind='controller', epoch_id='focused-epoch'))
        self.case.put('review_assignment', self.assignment['id'], self.assignment)
        self.records = self.case.read_records()
        sources, observations = [], []
        for reference in self.packet['current_evidence']:
            content = self.artifacts.read(reference).decode('utf-8')
            source = staged.make_source('artifact:' + digest(reference), 'artifact', content,
                identity={'artifact': reference}, descriptor=reference)
            sources.append(source)
            middle = max(1, len(content) // 2)
            for start, end in ((0, middle), (middle, len(content))):
                observations.append({'actual_input': {'intervals': [staged.source_span(source, start, end)]}})
        packets = []
        for index, observed in enumerate(observations):
            span = observed['actual_input']['intervals'][0]
            source = next(row for row in sources if row['id'] == span['source_id'])
            packet = dict(self.packet, evidence=[], staged_review={'originals': [dict(span=span,
                text=staged.materialize_quotation(source, span)['text'])]})
            packets.append((dict(self.assignment, id='selected-child-' + str(index)), packet))
        frozen = epoch(id='focused-epoch', controller_assignment_id=self.assignment['id'], role='adjudicator',
            subject=self.assignment['subject'], slot_id=self.assignment.get('slot_id', self.assignment['id']),
            reviewer_id=self.assignment['reviewer_id'], route_id=self.assignment['route_id'],
            author_id=self.assignment['author_id'], manifest=staged.build_manifest(self.packet, [], sources),
            focused_scope=dict(
                review_pair_roots=[digest(self.records['value_review'][key]) for key in self.assignment['context']['review_ids']],
                objection_id=self.packet['objection']['id'], objection_version=digest(self.packet['objection'])))
        self.case.put('review_epoch', frozen['id'], frozen)
        self.case.put('review_slot_head', frozen['slot_id'], dict(slot_id=frozen['slot_id'], epoch_id=frozen['id'],
                                                               state='pending', outcome=None))
        self.records = self.case.read_records()
        self.graph = dict(epoch=frozen,
            completion={'complete': True, 'issues': []}, selection={'observations': observations},
            packets=packets, frame_packet=dict(self.packet, evidence=[]))

    def record_finding(self):
        return self.case.mutate(protocol.record_adjudication,
            dict(self.output, id='focused-finding', assignment_id=self.assignment['id']))['result']

    def test_paged_finding_records_effective_disposition_and_raw_binding(self):
        self.prepare_finding()
        with patch.object(protocol, 'assignment_state', return_value=self.state), \
                patch.object(direction_reviews, 'collect_selected_task_graph', return_value=self.graph):
            changed = dict(self.output, reason='An author substitution.')
            with self.assertRaises(ResearchError) as rejected:
                self.case.mutate(protocol.record_adjudication,
                    dict(changed, id='substituted', assignment_id=self.assignment['id']))
            self.assertEqual(rejected.exception.code, 'review_output_mismatch')
            saved = self.record_finding()
            state = protocol.adjudication_state(self.case.read_records(), self.artifacts, saved['id'])
        self.assertTrue(state['ready'], state['obligations'])
        self.assertEqual(saved['effective_binding'], decisions.bind_effective_result(self.effective))
        self.assertNotEqual(saved['effective_binding']['effective_output_digest'],
                            saved['effective_binding']['terminal_raw_output_digest'])
        head = self.case.read_records()['review_slot_head'][self.graph['epoch']['slot_id']]
        self.assertEqual(head['state'], 'complete')
        self.assertEqual(head['outcome'], dict(kind='review_adjudication', id=saved['id'],
                                             output_digest=digest(self.output)))

    def test_staged_ordinary_final_allows_material_not_binding(self):
        self.prepare_finding(disposition='not_binding', paged=False)
        self.assignment['prompt'] = protocol.canonical_prompt('adjudicator', orchestration='problem_creation',
                                                            staged_task={'type': 'final'})
        with patch.object(protocol, 'assignment_state', return_value=self.state), \
                patch.object(direction_reviews, 'collect_selected_task_graph', return_value=self.graph):
            protocol.validate_adjudication_contract(self.records, self.artifacts, self.assignment, self.packet, self.output)
            self.packet['rubric_definition'] = {'item': 'priority'}
            with self.assertRaises(ResearchError) as rejected:
                protocol.validate_adjudication_contract(self.records, self.artifacts, self.assignment, self.packet, self.output)
        self.assertEqual(rejected.exception.code, 'invalid_review_adjudication')

    def test_exact_pair_and_objection_version_are_required(self):
        self.prepare_finding()
        for field, changed in (('review_pair_roots', [digest('replacement'), digest('other')]),
                               ('objection_version', digest('a later version')),
                               ('objection_id', 'another-objection')):
            graph = copy.deepcopy(self.graph)
            graph['epoch']['focused_scope'][field] = changed
            with self.subTest(field=field), \
                    patch.object(direction_reviews, 'collect_selected_task_graph', return_value=graph):
                with self.assertRaises(ResearchError) as rejected:
                    protocol.validate_adjudication_contract(self.records, self.artifacts, self.assignment, self.packet, self.output)
                self.assertEqual(rejected.exception.code, 'review_adjudication_mismatch')

    def test_incomplete_task_graph_cannot_authorize_a_disposition(self):
        self.prepare_finding()
        self.graph['completion'].update(complete=False, issues=[{'code': 'review_source_coverage_pending'}])
        with patch.object(direction_reviews, 'collect_selected_task_graph', return_value=self.graph):
            with self.assertRaises(ResearchError) as rejected:
                protocol.validate_adjudication_contract(self.records, self.artifacts, self.assignment, self.packet, self.output)
        self.assertEqual(rejected.exception.code, 'review_task_pending')

    def test_current_source_gap_blocks_recording_and_historical_readiness(self):
        self.prepare_finding()
        with patch.object(protocol, 'assignment_state', return_value=self.state), \
                patch.object(direction_reviews, 'collect_selected_task_graph', return_value=self.graph):
            saved = self.record_finding()
            self.graph['selection']['observations'].pop(0)
            self.graph['packets'].pop(0)
            state = protocol.adjudication_state(self.case.read_records(), self.artifacts, saved['id'])
            self.assertFalse(state['ready'])
            self.assertIn('review_current_source_incomplete', [row['code'] for row in state['obligations']])
            with self.assertRaises(ResearchError) as rejected:
                self.record_finding()
        self.assertEqual(rejected.exception.code, 'review_current_source_incomplete')

    def test_changed_selection_invalidates_recorded_effective_finding(self):
        self.prepare_finding()
        with patch.object(protocol, 'assignment_state', return_value=self.state), \
                patch.object(direction_reviews, 'collect_selected_task_graph', return_value=self.graph):
            saved = self.record_finding()
            self.state['output'] = self.output
            self.state['effective'] = dict(self.effective, selected_observations_root=digest('changed selection'))
            state = protocol.adjudication_state(self.case.read_records(), self.artifacts, saved['id'])
            self.assertFalse(state['ready'])
            self.assertIn('research_review_binding_stale', [row['code'] for row in state['obligations']])
            records = self.case.read_records()
            records['review_adjudication'][saved['id']].pop('effective_binding')
            self.state['effective'] = self.effective
            self.assertFalse(protocol.adjudication_state(records, self.artifacts, saved['id'])['ready'])

    def test_unavailable_selected_graph_returns_a_historical_obligation(self):
        self.prepare_finding()
        with patch.object(protocol, 'assignment_state', return_value=self.state), \
                patch.object(direction_reviews, 'collect_selected_task_graph', return_value=self.graph) as graph:
            saved = self.record_finding()
            graph.side_effect = ResearchError('research_review_binding_stale', 'A selected task binding changed.')
            state = protocol.adjudication_state(self.case.read_records(), self.artifacts, saved['id'])
        self.assertFalse(state['ready'])
        self.assertEqual(state['adjudication'], saved)
        self.assertIn('research_review_binding_stale', [row['code'] for row in state['obligations']])

    def prepare_paged_source(self):
        records = self.case.read_records()
        review = records['value_review']['review-result-first']
        identifier = review['assignment_id']
        self.source_assignment = records['review_assignment'][identifier]
        self.source_packets = supplied.lineage_packets(records, self.artifacts, [identifier])
        self.source_state = protocol.assignment_state(records, self.artifacts, identifier)
        effective = self.compose(identifier, decisions._response(review['payload']), role='result')
        self.case.put('review_assignment', identifier,
                      dict(self.source_assignment, staged_review={'kind': 'controller', 'epoch_id': 'source-epoch'}))
        self.case.put('value_review', review['id'], dict(review, effective_binding=decisions.bind_effective_result(effective)))
        self.source_state.update(output={'terminal': 'raw source authorization'}, output_digest=digest('raw source'),
                                 effective=effective)
        native_state = protocol.assignment_state
        self.source_state_patch = patch.object(protocol, 'assignment_state', side_effect=lambda r, a, key:
            self.source_state if key == identifier else native_state(r, a, key))
        self.source_graph_patch = patch.object(direction_reviews, 'collect_selected_task_graph', return_value=dict(
            epoch={'controller_assignment_id': identifier}, completion={'complete': True},
            packets=[(dict(self.source_assignment, id='source-child'), packet) for packet in self.source_packets],
            frame_packet=self.source_packets[0]))

    def test_paged_source_review_supplies_effective_objection_and_exact_version(self):
        self.prepare_paged_source()
        with self.source_state_patch, self.source_graph_patch:
            assignment = self.case.assign_focused_review()
            packet = protocol._read_json(self.artifacts, assignment['packet'])
        stored = self.case.read_records()['value_review']['review-result-first']
        version = decisions.state_objection(stored, decisions.list_stated_objections(stored)[0])
        for key in ('id', 'review_id', 'recorded_revision', 'material_version', 'claim', 'reason', 'resolution_condition'):
            self.assertEqual(packet['objection'][key], version[key], key)
        self.assertEqual(packet['objection']['evidence'][0]['artifact_descriptor']['sha256'],
                         version['evidence'][0]['sha256'])
        self.assertEqual(packet['version_review']['value']['rubric'], stored['payload']['value']['rubric'])
        self.assertEqual(packet['assessments'][0]['objections'][0]['id'], version['id'])
        self.assertIn(self.case.supporting, packet['original_evidence'])

    def test_version_and_rubric_delivery_reject_changed_source_bindings(self):
        self.prepare_paged_source()
        self.source_state['effective'] = dict(self.source_state['effective'], composition_root=digest('replacement root'))
        records = self.case.read_records()
        review = records['value_review']['review-result-first']
        version = decisions.state_objection(review, decisions.list_stated_objections(review)[0])
        with self.source_state_patch, self.source_graph_patch:
            for build in (lambda: protocol.describe_version_evidence(records, self.artifacts, version),
                          lambda: protocol.build_rubric_objection(records, self.artifacts, [review],
                              {'objection_id': 'unsupported-implication'}, {}, [review]),
                          self.case.assign_focused_review):
                with self.subTest(build=build):
                    with self.assertRaises(ResearchError) as rejected:
                        build()
                    self.assertEqual(rejected.exception.code, 'research_review_unverified')

    def test_incomplete_source_cannot_supply_one_of_the_completed_review_pair(self):
        self.prepare_paged_source()
        self.source_state['effective'] = composition.effective_review_output(self.source_assignment['id'], dict(
            controller_id=self.source_assignment['id'], kind='incomplete', outcome={'id': 'unfinished-result'}))
        with self.source_state_patch, self.source_graph_patch:
            with self.assertRaises(ResearchError) as rejected:
                self.case.assign_focused_review()
        self.assertEqual(rejected.exception.code, 'research_review_unverified')

    def test_digestless_adjudicator_citation_is_refused_before_record_resolution(self):
        assignment = self.case.assign_focused_review()
        records = self.case.read_records()
        packet = protocol._read_json(self.artifacts, assignment['packet'])
        output = dict(objection_id='unsupported-implication', disposition='not_upheld', reason='The claim is resolved.',
                      correction_admissible=None, evidence=[{'kind': 'record', 'record_kind': 'problem_statement',
                                                          'id': packet['subject']['statement_id']}])
        with patch.object(protocol, '_find_frozen_carried_record', side_effect=AssertionError('must check grammar first')):
            with self.assertRaises(ResearchError) as rejected:
                protocol.validate_adjudication_contract(records, self.artifacts, assignment, packet, output)
        self.assertEqual(rejected.exception.code, 'invalid_strategy')


class IncompleteAdjudicationTests(unittest.TestCase):
    def build_incomplete_finding(self, origin='execution'):
        frozen = epoch(role='adjudicator', subject={'kind': 'problem', 'statement_id': 'statement'},
            focused_scope=dict(review_pair_roots=['review-A', 'review-B'],
            objection_id='O7', objection_version='version-7'))
        completion = staged.verify_completion(frozen, {'observations': []})
        outcome = staged.record_incomplete(frozen, completion, origin=origin,
            origin_binding=({'execution_receipts': ['terminated'], 'reason': 'exhausted'} if origin == 'execution'
                            else {'attempt_id': 'terminal', 'raw_output_digest': digest('incomplete authorization')}),
            unresolved_scope=[dict(id='missing', scope={'claim_ids': ['c']}, cause='rereads exhausted',
                affected_decisions=['disposition'], exhausted_tasks=['exhausted'], scope_kind='unresolved',
                next_action=dict(workflow='problem-evidence', scope={'claim_ids': ['c']}, prerequisite='new evidence'))],
            recovery_work=[dict(workflow='problem-evidence', command='record-evidence', payload={},
                                text='Supply evidence for the unresolved focused claim.')], resource_ledger={'attempts': 20})
        assignment = dict(id=frozen['controller_assignment_id'], role='adjudicator', reviewer_id=frozen['reviewer_id'],
            subject=frozen['subject'], slot_id=frozen['slot_id'], context={},
            staged_review={'kind': 'controller', 'epoch_id': frozen['id']})
        effective = composition.effective_review_output(assignment['id'], dict(controller_id=assignment['id'],
                                                                             kind='incomplete', outcome=outcome))
        state = dict(ready=False, role='adjudicator', output=None, effective=effective, obligations=[])
        return frozen, assignment, state, outcome

    def test_incomplete_adjudicator_occupies_its_slot_without_a_disposition(self):
        frozen, assignment, state, outcome = self.build_incomplete_finding()
        records = dict(review_assignment={assignment['id']: assignment}, assessment_incomplete={outcome['id']: outcome},
            review_slot_head={outcome['slot_id']: dict(epoch_id=frozen['id'], state='incomplete', outcome=outcome)})
        with patch.object(protocol, 'assignment_state', return_value=dict(state, role='adjudicator',
                                                                         reviewer_id=assignment['reviewer_id'])):
            result = protocol.adjudication_state(records, object(), outcome['id'])
            slots = decisions.collect_problem_slot_outcomes(records, object(), assignment['subject'], stage='adjudicator')
        self.assertFalse(result['ready'])
        self.assertIsNone(result['adjudication'])
        self.assertIsNone(result['disposition'])
        self.assertEqual(result['incomplete_outcome'], outcome)
        self.assertEqual(result['slot_id'], outcome['slot_id'])
        self.assertEqual(slots['occupied'], 1)
        self.assertEqual(slots['incomplete'], [outcome])
        self.assertEqual(slots['complete'], [])
        self.assertFalse(slots['ready'])
        self.assertIn('assessment_incomplete', [row['code'] for row in result['obligations']])
        self.assertEqual(records['review_slot_head'][outcome['slot_id']]['outcome'], outcome)

    def test_incomplete_outcome_cannot_be_recorded_as_a_scientific_finding(self):
        from unittest.mock import Mock
        _, assignment, state, _ = self.build_incomplete_finding(origin='reviewer')
        store = Mock()
        def prepare(store, operation, payload, callback, **kwargs):
            return callback({'review_assignment': {assignment['id']: assignment}}, payload)
        output = dict(id='fake', assignment_id=assignment['id'], objection_id='O7', disposition='unresolved',
                      reason='Required work is unavailable.', evidence=[], correction_admissible=None)
        with patch.object(protocol, 'prepared_mutation', side_effect=prepare), \
                patch.object(protocol, 'assignment_state', return_value=dict(state, role='adjudicator')):
            with self.assertRaises(ResearchError) as rejected:
                protocol.record_adjudication(store, output, expected_revision=0, request_id='incomplete')
        self.assertEqual(rejected.exception.code, 'assessment_incomplete')

    def test_incomplete_result_uses_the_same_code_in_problem_review_obligations(self):
        _, assignment, state, outcome = self.build_incomplete_finding()
        assignment = dict(assignment, role='result', dossier_id=None, author_id='author')
        records = dict(workspace={'problem': {'authors': ['author']}},
            problem_selection={'current': {'statement_id': 'statement'}},
            problem_statement={'statement': {'id': 'statement'}},
            review_assignment={assignment['id']: assignment})
        with patch.object(protocol, 'assignment_state', return_value=state):
            result = decisions.assess_problem_reviews(records, object())
        item = next(row for row in result['obligations'] if row.get('incomplete'))
        self.assertEqual(item['code'], 'assessment_incomplete')
        self.assertEqual(item['incomplete'], [outcome])
        self.assertFalse(result['ready'])

    def test_incomplete_result_cannot_be_recorded_as_a_value_review(self):
        from unittest.mock import Mock
        _, assignment, state, _ = self.build_incomplete_finding(origin='reviewer')
        assignment = dict(assignment, role='result', dossier_id=None, author_id='author', prompt={'parameters': {}})
        records = dict(workspace={'problem': {'authors': ['author']}},
                       review_assignment={assignment['id']: assignment})
        store = Mock()
        def prepare(store, operation, payload, callback, **kwargs):
            return callback(records, payload)
        output = dict(id='fake', dossier_id=None, assignment_id=assignment['id'], stage='result', support=None,
                      value={}, objections=[], limitations=[])
        with patch.object(decisions, 'prepared_mutation', side_effect=prepare), \
                patch.object(protocol, 'validate_assignment_inventories'), \
                patch.object(protocol, 'assignment_state', return_value=dict(state, stage='result', dossier_id=None)):
            with self.assertRaises(ResearchError) as rejected:
                decisions.record_value_review(store, output, expected_revision=0, request_id='incomplete')
        self.assertEqual(rejected.exception.code, 'assessment_incomplete')


if __name__ == '__main__':
    unittest.main()
