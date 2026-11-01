"""Raw observations and composed scientific results have distinct consumers."""

import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from research_harness import direction_reviews, research_decisions as decisions
from research_harness import review_adjudicator_sources as current
from research_harness import review_context_repair as repair
from research_harness import review_protocol as protocol, review_refusal as refusal
from research_harness import review_state_evaluation as states, review_supplied_evidence as supplied
from research_harness.artifacts import ArtifactStore
from research_harness.errors import ResearchError
from research_harness.evaluation import Evaluation
from research_harness.evidence import digest
from test_review_composition import make_fixture, observe, terminal_for


class EffectiveConsumerTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.artifacts = ArtifactStore(Path(directory.name))
        self.records = {'review_assignment': {}, 'review_route': {}, 'review_attempt': {}}

    def save(self, value):
        return self.artifacts.put(json.dumps(value, sort_keys=True).encode(), 'application/json')

    def test_post_decision_preserves_ordinary_error_precedence_and_staged_priorities(self):
        from research_harness import problem_delivery
        payload = dict(id='decision', action='post', review_ids=['result-A', 'result-B'],
                       bar_review_ids=['bar-A', 'bar-B'], reason='Post after the required checks.')
        cases = [
            (['artifact_missing', 'research_objection_unresolved'], ['review_output_unavailable'],
             'research_objection_unresolved'),
            (['problem_evidence_unassessed', 'research_objection_unresolved'], [],
             'research_objection_unresolved'),
            (['problem_evidence_unassessed', 'research_objection_unresolved', 'review_task_pending'], [],
             'review_task_pending'),
        ]
        for staged in (False, True):
            records = dict(self.records, workspace={'problem': {}})
            if staged:
                records['review_epoch'] = {'epoch': {'id': 'epoch'}}
            for review_codes, source_codes, staged_code in cases:
                with self.subTest(staged=staged, review_codes=review_codes):
                    review_pending = [dict(code=code, explanation=code, objection_id='O7') for code in review_codes]
                    source_pending = [dict(code=code, explanation=code) for code in source_codes]
                    with patch.object(problem_delivery, 'assess_minimum', return_value=[]), \
                            patch.object(decisions, 'assess_problem_reviews', return_value={'obligations': review_pending}), \
                            patch.object(problem_delivery, 'assess_source_requests', return_value=source_pending):
                        with self.assertRaises(ResearchError) as rejected:
                            problem_delivery.prepare_decision(records, self.artifacts, payload, 1)
                    self.assertEqual(rejected.exception.code, staged_code if staged else review_codes[0])
                    pending = rejected.exception.details['obligations']
                    self.assertCountEqual([item['code'] for item in pending], review_codes + source_codes)
                    self.assertEqual(pending[0]['code'], rejected.exception.code)
                    if not staged:
                        self.assertEqual(pending, review_pending + source_pending)

    def composed(self):
        from research_harness import review_composition as composition
        plan, pages, choices, output = make_fixture()
        root = composition.build_root(plan, pages)
        terminal = observe(terminal_for(plan, root, choices), 'terminal')
        selection = dict(controller_id='controller', kind='paged', plan=plan, observations=pages,
                         root=root, terminal=terminal, completion_root=digest('completion'))
        return composition.effective_review_output('controller', selection), output

    def test_independence_uses_effective_object_and_composition_binding(self):
        effective, output = self.composed()
        review = dict(id='R', assignment_id='controller', payload=output,
                      output_digest=digest(output), effective_binding=decisions.bind_effective_result(effective))
        state = dict(output={'terminal': 'raw'}, output_digest=digest('raw'), effective=effective, obligations=[])
        with patch.object(protocol, 'assignment_state', return_value=state):
            self.assertEqual(decisions._independence(self.records, self.artifacts, [review]), [])
            state['effective'] = dict(effective, selected_observations_root=digest('changed selection'))
            self.assertEqual(decisions._independence(self.records, self.artifacts, [review])[0]['code'],
                             'research_review_binding_stale')

    def test_composed_result_requires_recorded_binding(self):
        effective, output = self.composed()
        review = dict(id='R', assignment_id='controller', payload=output, output_digest=digest(output))
        state = dict(output=output, output_digest=digest(output), effective=effective, obligations=[])
        with patch.object(protocol, 'assignment_state', return_value=state):
            self.assertTrue(decisions._independence(self.records, self.artifacts, [review]))
            self.records['review_assignment']['controller'] = dict(id='controller', staged_review={'epoch_id': 'epoch'})
            state['effective'] = dict(effective, kind='ordinary', composition_version=None, composition_root=None)
            self.assertTrue(decisions._independence(self.records, self.artifacts, [review]))

    def test_recording_compares_effective_science_and_retains_its_raw_binding(self):
        from research_harness import review_composition as composition
        from test_research_problem_reviews import ProblemReviewTests
        case = ProblemReviewTests(methodName='runTest')
        case.setUp()
        self.addCleanup(case.doCleanups)
        self.addCleanup(case.tearDown)
        case.observe_bar_reviews()
        payload = case.observe('result', 'first', record=False)
        records = case.read_records()
        identifier = payload['assignment_id']
        original_state = protocol.assignment_state(records, case.artifacts, identifier)
        packets = supplied.lineage_packets(records, case.artifacts, [identifier])
        output = decisions._response(payload)
        plan, pages, choices, _ = make_fixture(output)
        root = composition.build_root(plan, pages)
        effective = composition.effective_review_output(identifier, dict(controller_id=identifier, kind='paged',
            plan=plan, observations=pages, root=root, terminal=observe(terminal_for(plan, root, choices), 'terminal'),
            completion_root=digest('completed scientific work')))
        assignment = dict(records['review_assignment'][identifier], staged_review={'kind': 'controller', 'epoch_id': plan['epoch_id']})
        case.put('review_assignment', identifier, assignment)
        state = dict(original_state, output={'terminal': 'raw authorization'}, effective=effective)
        native_state = protocol.assignment_state
        graph = dict(completion={'complete': True}, packets=[(assignment, packet) for packet in packets])
        with patch.object(protocol, 'assignment_state', side_effect=lambda r, a, i:
                          state if i == identifier else native_state(r, a, i)), \
                patch.object(direction_reviews, 'collect_selected_task_graph', return_value=graph):
            substituted = copy.deepcopy(payload)
            substituted['value']['reason'] = 'Author replacement.'
            with self.assertRaises(ResearchError) as rejected:
                case.mutate(decisions.record_value_review, substituted)
            self.assertEqual(rejected.exception.code, 'value_review_output_mismatch')
            recorded = case.mutate(decisions.record_value_review, payload)['result']
        self.assertEqual(recorded['output_digest'], digest(output))
        self.assertEqual(recorded['effective_binding'], decisions.bind_effective_result(effective))

    def test_pending_successor_keeps_its_predecessors_adverse_objection(self):
        from test_review_composition import worked_objection
        subject = {'kind': 'problem', 'statement_id': 'statement'}
        first = dict(id='prior', role='result', subject=subject, context={})
        successor = dict(id='successor', role='result', subject=subject,
                         context={'prior_assignment_id': 'prior'}, staged_review={'kind': 'controller'})
        self.records['review_assignment'] = {'prior': first, 'successor': successor}
        prior = dict(id='prior-result', assignment_id='prior', subject=subject, recorded_revision=1,
                     payload=dict(stage='result', objections=[worked_objection()], value={'objection_findings': []}))
        self.records['value_review'] = {prior['id']: prior}
        with patch.object(direction_reviews, 'collect_observed_packets', return_value=[]), \
                patch('research_harness.problem_candidates.collect_bound_slate_reviews', return_value=[]), \
                patch.object(decisions, '_collect_superseded_review_ids', return_value=set()):
            objections = decisions.collect_problem_post_objections(self.records, self.artifacts, [], subject=subject)
            successor.pop('staged_review')
            legacy = decisions.collect_problem_post_objections(self.records, self.artifacts, [], subject=subject)
        self.assertEqual([row['id'] for row in objections], ['O7'])
        self.assertEqual([row['id'] for row in legacy], ['O7'])

    def test_only_a_bound_effective_reassessment_supersedes_prior_findings(self):
        from research_harness import review_composition as composition
        from test_review_composition import worked_result
        subject = {'kind': 'problem'}
        prior_assignment = dict(id='old', role='result', subject=subject, reviewer_id='reviewer', session_id='session',
                                slot_id='A', context={}, packet=self.save({}))
        successor = dict(prior_assignment, id='controller', context={'prior_assignment_id': 'old'},
                         staged_review={'kind': 'controller', 'epoch_id': 'epoch'})
        self.records['review_assignment'] = {'old': prior_assignment, 'controller': successor}
        prior = dict(id='old-review', assignment_id='old', subject=subject, payload=worked_result(), recorded_revision=1)
        for disposition, changed_root, objection_status, expected in [
                ('revised', False, 'continuing', True), ('unresolved', False, 'continuing', False),
                ('revised', True, 'continuing', False), ('revised', False, 'resolved', False)]:
            with self.subTest(disposition=disposition, changed_root=changed_root, objection_status=objection_status):
                output = worked_result()
                output['value']['objection_findings'][0]['status'] = objection_status
                output['reassessment'] = {'assignment_id': 'old', 'findings': [dict(review_id=prior['id'],
                    disposition=disposition, reason='The broader claim remains unsupported.', evidence=[])]}
                plan, pages, choices, _ = make_fixture(output)
                root = composition.build_root(plan, pages)
                effective = composition.effective_review_output('controller', dict(controller_id='controller', kind='paged',
                    plan=plan, observations=pages, root=root, terminal=observe(terminal_for(plan, root, choices), 'terminal'),
                    completion_root=digest('completion')))
                current = dict(id='new-review', assignment_id='controller', subject=subject, payload=output,
                    recorded_revision=2, output_digest=digest(output), effective_binding=decisions.bind_effective_result(effective))
                self.records['value_review'] = {prior['id']: prior, current['id']: current}
                if changed_root:
                    effective['composition_root'] = digest('unauthorized replacement')
                with patch.object(protocol, 'assignment_state', return_value={'effective': effective, 'obligations': []}):
                    self.assertEqual(prior['id'] in decisions._collect_superseded_review_ids(self.records, self.artifacts), expected)

    def test_refusal_validates_raw_task_without_selecting_effective_result(self):
        assignment = dict(id='task-assignment', role='result', packet=self.save({'staged_review': {
            'task': {'type': 'answer'}, 'epoch': {'manifest': {}}}}))
        attempt = dict(id='page-attempt', assignment_id=assignment['id'], status='completed')
        self.records['review_assignment'][assignment['id']] = assignment
        self.records['review_attempt'][attempt['id']] = attempt
        raw = {'status': 'completed', 'raw_output': {'pages': []}, 'output_digest': digest({'pages': []})}
        with patch.object(protocol, 'read_raw_observation', return_value=raw, create=True) as read, \
                patch.object(protocol, 'authenticate_attempt', return_value=True), \
                patch.object(protocol, 'assignment_state', side_effect=AssertionError('selection recursion')), \
                patch('research_harness.review_staged.validate_task_output',
                      side_effect=ResearchError('invalid_review_staged', 'A typed page is required.')):
            failure = refusal.classify_observed_output(self.records, self.artifacts, attempt['id'])
        self.assertEqual(failure['code'], 'invalid_review_staged')
        read.assert_called_once_with(self.records, self.artifacts, attempt['id'])

    def test_evidence_arrays_accepts_effective_adjudication(self):
        evidence = [{'kind': 'record', 'record_kind': 'reading', 'id': 'current'}]
        self.assertEqual(refusal.evidence_arrays({'objection_id': 'O7', 'evidence': evidence}, with_paths=True),
                         [('/evidence', evidence)])

    def test_cache_changes_when_selected_root_changes_in_same_evaluation(self):
        self.records['review_selection'] = {'controller': {'composition_root': digest('first')}}
        evaluation = Evaluation(self.records, self.artifacts)
        compute = Mock(return_value={'ready': True})
        with patch.object(states, '_runtime_fingerprint', return_value='same-runtime'):
            states.evaluate_state(self.records, evaluation, 'assignment', 'controller', compute)
            self.records['review_selection']['controller']['composition_root'] = digest('second')
            states.evaluate_state(self.records, evaluation, 'assignment', 'controller', compute)
        self.assertEqual(compute.call_count, 2)

    def test_adjudication_cache_tracks_its_assignments_composition_plan(self):
        self.records['review_assignment']['controller'] = {'id': 'controller', 'staged_review': {'epoch_id': 'epoch'}}
        self.records['review_adjudication'] = {'disposition': {'assignment_id': 'controller'}}
        self.records['review_task_acceptance'] = {'capture': {'receipt': {'staged_observation': {'epoch_id': 'epoch'}}}}
        self.records['review_composition_plan'] = {'plan': {'epoch_id': 'epoch', 'root': digest('first')}}
        evaluation = Evaluation(self.records, self.artifacts)
        compute = Mock(return_value={'ready': True})
        with patch.object(states, '_runtime_fingerprint', return_value='same-runtime'):
            states.evaluate_state(self.records, evaluation, 'adjudication', 'disposition', compute)
            self.records['review_composition_plan']['plan']['root'] = digest('changed')
            states.evaluate_state(self.records, evaluation, 'adjudication', 'disposition', compute)
        self.assertEqual(compute.call_count, 2)

    def test_adjudication_scientific_equality_includes_disposition_and_scope(self):
        original = dict(objection_id='O7', disposition='upheld', reason='Only primes.', evidence=[], correction_admissible=None)
        changed = dict(original, disposition='not_upheld')
        self.assertNotEqual(repair._scientific_content(original), repair._scientific_content(changed))

    def test_context_exposure_includes_controller_sibling_tasks(self):
        controller = dict(id='controller', staged_review={'kind': 'controller'}, context={})
        child = dict(id='child', staged_review={'kind': 'task', 'controller_assignment_id': 'controller'}, context={})
        self.records['review_assignment'] = {'controller': controller, 'child': child}
        self.assertEqual([row['id'] for row in repair._exposure_lineage(self.records, controller)],
                         ['controller', 'child'])

    def test_staged_source_requests_preserve_typed_retrievals(self):
        from research_harness import bar_sources
        from research_harness import review_staged as staged
        from test_review_staged_fixtures import epoch
        value = epoch()
        source = value['manifest']['sources'][0]
        row = dict(id='question', question='Check the prime condition.',
                   inputs=[staged.source_span(source, 0, source['characters'])], status='pending',
                   claim_ids=[], context_roots={}, answer=None, observation_ids=[], requested_by='read-task')
        packet = {'staged_review': {'pending_retrievals': []}}
        self.assertEqual(bar_sources.pending(packet, {'retrievals': [row]}, manifest=value['manifest']), [row])

    def test_staged_complete_source_closure_retrieves_originals_again_and_settles_requests(self):
        from research_harness import review_source_complete as complete
        from research_harness import review_staged as staged
        from test_review_staged_fixtures import epoch, selection
        value = epoch()
        prior = dict(id='controller', staged_review={'epoch_id': value['id']})
        source = value['manifest']['sources'][0]
        row = dict(id='question', question='Reread the condition.',
                   inputs=[staged.source_span(source, 0, source['characters'])], claim_ids=[], context_roots={},
                   status='pending', answer=None, observation_ids=[], requested_by='read-task')
        tasks, selected = selection(value)
        observed = selected['observations'][-1]
        observed['output']['retrievals'] = [row]
        observed['raw_output_digest'] = digest(observed['output'])
        selected['observations'][0]['dependencies'][observed['task_id']] = observed['raw_output_digest']
        completion = staged.verify_completion(value, selected, tasks=tasks)
        graph = dict(epoch=value, tasks=tasks, selection=selected, completion=completion)
        packet = {'staged_review': {'epoch_id': value['id']}}
        with patch.object(direction_reviews, 'collect_selected_task_graph', return_value=graph):
            self.assertEqual(complete.requests_for_lineage({}, self.artifacts, prior, {}), [row])
            complete.prepare({}, self.artifacts, prior, {'raw_output': {}}, packet)
            complete.validate({}, self.artifacts, prior, packet)
            self.assertEqual(packet['staged_review']['pending_retrievals'], [row])
            resolved = dict(row, status='resolved', answer='The condition is prime p.', observation_ids=['reread'])
            self.assertEqual(complete.requests_for_lineage({}, self.artifacts, prior, {'retrievals': [resolved]}), [])
            graph['selection']['observations'].append({'id': 'later-sibling', 'output': {}})
            # Later progress cannot change the exact inputs of the frozen task.
            complete.validate({}, self.artifacts, prior, packet)
            packet[complete.MARKER]['selected_observations_root'] = digest('other graph')
            with self.assertRaises(ResearchError):
                complete.validate({}, self.artifacts, prior, packet)

    def test_actual_inputs_include_selected_sibling_tasks_and_exclude_saved_packets(self):
        controller = dict(id='controller', role='result', staged_review={'kind': 'controller', 'epoch_id': 'epoch'})
        self.records['review_assignment']['controller'] = controller
        graph = {'packets': [(dict(id='sibling-a'), {'evidence': ['actual-a']}),
                             (dict(id='sibling-b'), {'evidence': ['actual-b']})]}
        with patch.object(direction_reviews, 'collect_selected_task_graph', return_value=graph, create=True):
            self.assertEqual(direction_reviews.collect_actual_input_packets(self.records, self.artifacts, 'controller'),
                             [row[1] for row in graph['packets']])
            self.assertEqual(supplied.lineage_packets(self.records, self.artifacts, ['controller']),
                             [row[1] for row in graph['packets']])

    def test_staged_current_sources_use_selected_graph_not_terminal_alone(self):
        frozen = {'current_source_delivery': {'protocol': current.PROTOCOL},
                  'staged_review': {'epoch_id': 'epoch'}}
        assignment = dict(id='controller', packet=self.save(frozen), staged_review={'epoch_id': 'epoch'})
        with patch.object(current, 'validate_staged', create=True) as validate:
            current.validate_observed(self.records, self.artifacts, assignment, 'terminal')
        validate.assert_called_once_with(self.records, self.artifacts, assignment, frozen)

    def test_staged_incomplete_occupies_slot_and_excludes_favorable_predecessor(self):
        from research_harness import review_staged as staged
        from test_review_staged_fixtures import epoch
        value = epoch()
        completion = staged.verify_completion(value, {'observations': []}, tasks=[])
        outcome = staged.record_incomplete(value, completion, origin='execution',
            origin_binding={'execution_receipts': ['failed'], 'reason': 'exhausted'},
            unresolved_scope=[dict(id='scope', scope={'claims': ['missing']}, cause='exhausted', affected_decisions=[],
                                  exhausted_tasks=[], scope_kind='unresolved', next_action={'workflow': 'problem-evidence',
                                      'scope': {'claims': ['missing']}, 'prerequisite': 'Acquire missing evidence.'})],
            recovery_work=[dict(workflow='problem-evidence', command='problem-evidence', payload={}, text='Supply evidence.')],
            resource_ledger=dict(attempts=1, retries=0, rereads=0, subdivisions=0, epochs=1,
                                 elapsed_seconds=1, unknown_usage=False))
        assignment = dict(id='controller', role='result', dossier_id=None, subject={'kind': 'problem'},
                          slot_id='A', staged_review={'kind': 'controller', 'epoch_id': value['id']},
                          recorded_revision=2, context={})
        self.records['review_assignment']['controller'] = assignment
        predecessor = dict(assignment, id='predecessor', staged_review=None, recorded_revision=1)
        predecessor.pop('staged_review')
        other = dict(predecessor, id='other', slot_id='B')
        self.records['review_assignment'].update(predecessor=predecessor, other=other)
        payload = dict(stage='result', objections=[], value={})
        self.records['value_review'] = {identifier: dict(id=identifier, assignment_id=owner, subject={'kind': 'problem'},
            payload=payload, recorded_revision=1) for identifier, owner in [('old-favorable', 'predecessor'), ('complete-B', 'other')]}
        state = dict(effective={'kind': 'incomplete', 'incomplete_outcome': outcome}, obligations=[])
        with patch.object(protocol, 'assignment_state', side_effect=lambda r, a, i:
                          {'effective': {'kind': 'ordinary'}, 'obligations': []} if i == 'other' else state):
            result = decisions.collect_problem_slot_outcomes(self.records, self.artifacts, {'kind': 'problem'})
        self.assertEqual(decisions._collect_problem_reviews(self.records, 'result', {'kind': 'problem'}), ['complete-B'])
        self.assertEqual(result['occupied'], 2)
        self.assertEqual([row['id'] for row in result['complete']], ['complete-B'])
        self.assertEqual(result['incomplete'], [outcome])
        self.assertFalse(result['ready'])

    def test_correction_binds_raw_digest_and_preserves_staged_terminal(self):
        from research_harness import review_output_correction as correction
        raw = dict(stage='result', support=None, value={'status': 'insufficient'}, objections=[], limitations=[],
                   terminal={'composition_root': digest('authorized')},
                   reassessment={'findings': [dict(review_id='old', disposition='confirmed', reason='Earlier reason.')]})
        packet = dict(original_output=raw, diagnostic=dict(original_output_digest=digest(raw),
                      findings=[dict(index=0, review_id='old')]))
        change = dict(source_output_digest=digest(raw), findings=[dict(review_id='old', disposition='revised',
                                                                      reason='The wording changed.')])
        with patch.object(decisions, 'validate_review_contract'), \
                patch.object(protocol, 'assignment_state', side_effect=AssertionError('raw correction only')):
            output = correction.validate_output(self.records, self.artifacts, {'staged_review': {'kind': 'task'}},
                                                packet, change)
            self.assertEqual(output['terminal'], raw['terminal'])
            with self.assertRaises(ResearchError):
                correction.validate_output(self.records, self.artifacts, {}, packet,
                                           dict(change, source_output_digest=digest({'effective': 'result'})))

    def test_partial_staged_citations_use_union_without_credit_for_an_entire_body(self):
        from research_harness import review_staged as staged
        reference = self.artifacts.put(b'First condition. Second condition.', 'text/plain')
        text = self.artifacts.read(reference).decode()
        source = staged.make_source('artifact:' + digest(reference), 'artifact', text,
                                    identity={'artifact': reference}, descriptor=reference)
        def packet(start, end):
            return {'staged_review': {'originals': [dict(span=staged.source_span(source, start, end), text=text[start:end])]}}
        link = dict(kind='source', link=dict(version_id='v', source_id='s', artifact=reference,
                                            locator={'kind': 'span', 'start': 3, 'end': 19}))
        packets = [packet(0, 12), packet(12, 20)]
        self.assertTrue(supplied.is_received({}, link, {}, [], artifacts=self.artifacts, frozen=True, packets=packets))
        self.assertFalse(supplied.is_received({}, reference, {}, [], artifacts=self.artifacts, frozen=True, packets=packets))
        packets[1] = packet(13, 20)
        self.assertFalse(supplied.is_received({}, link, {}, [], artifacts=self.artifacts, frozen=True, packets=packets))

    def test_selected_graph_revalidates_raw_receipts_and_sibling_actual_inputs(self):
        from research_harness import review_staged as staged, review_transport as transport
        from test_review_staged_fixtures import epoch, selection
        value = epoch()
        tasks, selected = selection(value)
        controller = dict(id=value['controller_assignment_id'], staged_review={'kind': 'controller', 'epoch_id': value['id']})
        self.records['review_assignment'][controller['id']] = controller
        self.records['review_epoch'] = {value['id']: value}
        self.records['review_task'] = {task['id']: dict(id=task['id'], task=task, epoch_id=value['id']) for task in tasks}
        self.records['review_task_acceptance'] = {}
        raw = {}
        sources = {row['id']: row for row in value['manifest']['sources']}
        for task, observed in zip(tasks, reversed(selected['observations'])):
            child = dict(id=task['id'], staged_task=task, staged_review={'kind': 'task', 'epoch_id': value['id'],
                         'controller_assignment_id': controller['id'], 'task_id': task['id']})
            self.records['review_assignment'][child['id']] = child
            packet = {'staged_review': dict(epoch_id=value['id'], task={'id': task['id']}, originals=[
                dict(span=span, text=staged.materialize_quotation(sources[span['source_id']], span)['text'])
                for span in task['inputs']])}
            request = self.save({'input': [{'role': 'user', 'content': transport.PACKET_LABEL + json.dumps(packet)}]})
            attempt = dict(id=observed['id'], assignment_id=child['id'])
            raw[attempt['id']] = dict(instrumented=True, status='completed', raw_output=observed['output'],
                raw_output_digest=observed['raw_output_digest'], request_digest=observed['request_digest'], request=request)
            self.records['review_task_acceptance'][attempt['id']] = dict(id=attempt['id'], receipt=dict(attempt=attempt, staged_observation=observed))
        with patch.object(protocol, 'authenticate_attempt', return_value=True), \
                patch.object(protocol, 'read_raw_observation', side_effect=lambda r, a, i: raw[i], create=True), \
                patch.object(protocol, 'assignment_state', return_value={'effective': {'kind': 'pending'}}):
            graph = direction_reviews.collect_selected_task_graph(self.records, self.artifacts, controller['id'])
            self.assertEqual(len(graph['packets']), 2)
            self.assertEqual(len(graph['selection']['observations']), 2)
            self.assertTrue(graph['completion']['complete'], graph['completion']['issues'])
            self.records['review_output_correction_invocation'] = {'correction': {'assignment_id': tasks[0]['id']}}
            with patch('research_harness.review_output_correction.selected_correction',
                       return_value={'id': 'corrected-page', 'requires_reauthorization': True}):
                with self.assertRaises(ResearchError) as corrected:
                    direction_reviews.collect_selected_task_graph(self.records, self.artifacts, controller['id'])
                self.assertEqual(corrected.exception.code, 'review_composition_pending')
            self.records.pop('review_output_correction_invocation')
            next(iter(raw.values()))['raw_output_digest'] = digest('altered capture')
            with self.assertRaises(ResearchError):
                direction_reviews.collect_selected_task_graph(self.records, self.artifacts, controller['id'])


if __name__ == '__main__':
    unittest.main()
