"""Integration contracts between staged planning, packets and observed outputs."""

import copy
import unittest
from unittest.mock import patch

from research_harness import review_protocol as protocol, review_staged as staged
from research_harness import review_composition as composition, review_refusal
from research_harness.evidence import digest
from research_harness.errors import ResearchError
from research_harness import review_staged_protocol as staged_protocol
from problem_parts_fixtures import ResultPartsCase
from test_review_staged_fixtures import epoch, profile, task, observation, full_card, selection
from test_review_staged_text_contracts import incomplete_output
import test_review_staged_protocol as protocol_fixtures


class StagedPacketGapTests(unittest.TestCase):
    def test_invalid_card_array_does_not_hide_valid_cells_from_reconciliation(self):
        value = epoch()
        cells = full_card(value)['judgments']
        assigned = task(value, 'integrate', scope='segmented_original')
        invalid_cards = [None] + [[dict(full_card(value), **{key: invalid})]
            for key in ('conclusions', 'conditions', 'judgments') for invalid in (None, {})]
        for index, cards in enumerate(invalid_cards):
            with self.subTest(case=index):
                output = dict(cards=cards, cells=cells)
                observed = dict(id='partial-read', task_id='read', raw_output_digest=digest(output), output=output)
                packet = staged_protocol.build_packet(value, assigned, observations=[observed])
                self.assertEqual(packet['staged_review']['integrate']['full_cards'], [])
                self.assertEqual(packet['staged_review']['integrate']['conclusions'], cells)

    def build_incomplete_page_fixture(self):
        value = epoch()
        bindings = staged_protocol.build_composition_bindings(value, [])
        plan = composition.plan_incomplete_composition(value, outcome=incomplete_output(), known_conclusions=[], findings=[],
            page_characters=1000, **{key:bindings[key] for key in
                ('source_root', 'dependency_root', 'selected_observations_root', 'integration_root')})
        plan['evidence_obligations'] = bindings['evidence_obligations']
        assigned = staged.plan_tasks(value, route_limit=10000, output_tokens=100, profiles={}, composition_plan=plan)['tasks'][0]
        assigned = staged.freeze_task(assigned, digest(assigned), staged.SERIALIZATION,
                                      measurement=staged.joint_fit([], 10000, 100, profile()))
        packet = staged_protocol.build_packet(value, assigned, composition_plan=plan)
        source = packet['staged_review']['answer']
        page = {key:copy.deepcopy(source[key]) for key in ('protocol', 'epoch_id', 'role', 'task_id', 'page_id',
                'revision', 'source_root', 'dependency_root', 'ownership', 'replaces')}
        ground = {'kind':'reviewer_observation', 'reason':'The scientific scope is still unresolved.'}
        page.update(fragments=[dict(fragment, grounds=[ground]) for fragment in source['source_fragments']],
                    grounding=[ground], findings=[], dispositions=[])
        return value, plan, assigned, page

    def test_packet_rejects_a_changed_composition_plan_root(self):
        value, plan, assigned, _ = self.build_incomplete_page_fixture()
        changed = dict(plan, integration_root=digest('a different integration'))
        with self.assertRaises(ResearchError) as raised:
            staged_protocol.build_packet(value, assigned, composition_plan=changed)
        self.assertEqual(raised.exception.code, 'review_composition_conflict')

    def test_composition_rejects_a_page_from_another_frozen_answer_task(self):
        value, plan, assigned, page = self.build_incomplete_page_fixture()
        page['task_id'] = 'another-answer-task'
        observed = observation(value, assigned, {'pages':[page]})
        with self.assertRaises(ResearchError) as raised:
            staged_protocol.build_composition(value, plan, [observed], None, tasks=[assigned])
        self.assertEqual(raised.exception.code, 'review_composition_conflict')

    def test_card_segments_bind_the_compact_text_with_finding_bodies(self):
        value = epoch()
        card = full_card(value)
        finding = dict(id='finding', text='The condition limits the claim.', adverse=True,
                       classification=dict(decisions=['/value/status'], reason='The condition changes the result.'),
                       claim_ids=[card['judgments'][0]['claim_id']], grounds=card['critical_spans'])
        card['findings'] = [finding['id']]
        from research_harness.storage import _canonical
        compact = staged.compact_card(card, manifest=value['manifest'], findings=[finding])
        serialized = _canonical(compact)
        plan = staged.plan_tasks(value, route_limit=len(serialized), output_tokens=100,
            profiles={'combine':profile()}, cards=[dict(id=card['id'], characters=len(serialized), record=card)],
            findings=[finding])
        segments = [row for row in plan['tasks'] if row['scope'] == 'card_segment']
        self.assertTrue(segments)
        for assigned in segments:
            assigned = staged.freeze_task(assigned, digest(assigned), staged.SERIALIZATION)
            packet = staged_protocol.build_packet(value, assigned, observations=[dict(id='reading', task_id='read',
                raw_output_digest=digest(card), output=dict(cards=[card], findings=[finding]))])
            for segment in packet['staged_review']['combine']['segments']:
                span = segment['span']
                self.assertEqual(span.get('card_root'), digest(compact))
                self.assertEqual(span, staged.card_span(card, span['start'], span['end'],
                                                       manifest=value['manifest'], findings=[finding]))
                self.assertEqual(segment['text'], serialized[span['start']:span['end']])

    def test_packets_deliver_the_planned_reconciliation_identity(self):
        value = epoch()
        plan = staged.plan_tasks(value, route_limit=10000, output_tokens=17,
                                 profiles={'read': profile(output_tokens_per_claim=10)})
        identities = set()
        for assigned in plan['tasks']:
            assigned = staged.freeze_task(assigned, digest(assigned), staged.SERIALIZATION,
                measurement=staged.joint_fit([], 10000, 100, profile()))
            packet = staged_protocol.build_packet(value, assigned)
            self.assertIn('segments', packet['staged_review'])
            identities.add(packet['staged_review']['segments']['reconciliation_id'])
        self.assertEqual(identities, {plan['tasks'][0]['reconciliation_id']})

    def test_final_packet_delivers_the_terminal_condition_record_root(self):
        value = epoch()
        card = full_card(value, conditions=[dict(id='condition', text='Prime inputs only.', grounds=[])])
        tasks, chosen = selection(value, [card])
        assigned = task(value, 'final', dependencies=[tasks[-1]['id']])
        packet = staged_protocol.build_packet(value, assigned, observations=chosen['observations'][:1])
        receipt = chosen['observations'][0]['output']['integrations'][0]
        expected = staged.bind_terminal_conditions(value, {}, cards=[card], integration=receipt)
        self.assertEqual(packet['staged_review']['final'].get('record_root'), expected['record_root'])

    def test_terminal_root_uses_the_bound_integration_lists(self):
        value = epoch()
        card = full_card(value)
        tasks, chosen = selection(value, [card])
        output = chosen['observations'][0]['output']
        receipt = output['integrations'][0]
        for key in ('selected_observations', 'input_roots', 'child_union', 'obligation_ids'):
            receipt.pop(key)
        bound = staged.bind_integration_lists(receipt, epoch=value, task=tasks[-1], cards=[card],
                                               observations=chosen['observations'][1:])
        receipt['record_root'] = bound['record_root']
        chosen['observations'][0]['raw_output_digest'] = digest(output)
        assigned = task(value, 'final', dependencies=[tasks[-1]['id']])
        packet = staged_protocol.build_packet(value, assigned, observations=chosen['observations'], tasks=tasks)
        expected = staged.bind_terminal_conditions(value, {}, cards=[card], integration=bound)
        self.assertEqual(packet['staged_review']['final'].get('record_root'), expected['record_root'])

    def test_incomplete_answer_packet_uses_the_composition_role(self):
        value = epoch()
        plan = composition.plan_incomplete_composition(value, outcome=incomplete_output(), known_conclusions=[], findings=[],
            source_root=value['manifest']['digest'], dependency_root=digest('dependencies'),
            selected_observations_root=digest('selection'), integration_root=digest('integration'), page_characters=1000)
        assigned = staged.plan_tasks(value, route_limit=10000, output_tokens=100, profiles={}, composition_plan=plan)['tasks'][0]
        assigned = staged.freeze_task(assigned, digest(assigned), staged.SERIALIZATION,
                                      measurement=staged.joint_fit([], 10000, 100, profile()))
        packet = staged_protocol.build_packet(value, assigned, composition_plan=plan)
        self.assertEqual(packet['staged_review']['answer']['role'], 'incomplete')
        self.assertEqual(packet['staged_review']['answer']['ownership'], assigned['answer_ranges'])
        fragments = packet['staged_review']['answer'].get('source_fragments')
        self.assertIsNotNone(fragments)
        values = dict(known_conclusions=[], findings=[], **incomplete_output())
        self.assertEqual(fragments, [dict(owner, content=[] if owner['type'] == 'empty_list' else
            values[owner['path'][1:]][owner['ordinal']]) for owner in assigned['answer_ranges']])


class StagedObservedGapTests(ResultPartsCase):
    staged_entry = True

    def test_incomplete_provenance_uses_the_terminal_attempt_for_every_child(self):
        records, value, _, raw = protocol_fixtures.StagedEntryTests.build_terminal_records(self, incomplete=True)
        terminal_attempt = next(row['receipt']['attempt'] for row in records['review_task_acceptance'].values()
                                if row['receipt']['staged_observation']['output'] == raw)
        child_id = next(row['receipt']['attempt']['assignment_id'] for row in records['review_task_acceptance'].values()
                        if row['receipt']['attempt']['id'] != terminal_attempt['id'])
        with patch.object(staged_protocol, 'authenticate_observation', return_value=True), \
                patch.object(staged_protocol, 'matches_currency', return_value=True):
            state = protocol._assignment_state(records, self.case.artifacts, child_id)
        binding = state['effective']['incomplete_outcome']['origin_binding']
        self.assertEqual(binding, dict(attempt_id=terminal_attempt['id'], raw_output_digest=digest(raw)))

    def test_refusal_classifies_the_ordinary_projection_of_a_final(self):
        from test_review_composition import make_fixture
        records = self.case.read_records()
        assignment = copy.deepcopy(records['review_assignment']['bar-first'])
        assigned = task(epoch(), 'final')
        assignment.update(id='terminal-child', role='result', staged_task=assigned,
                          staged_review=dict(kind='task', epoch_id='epoch-A'))
        packet = dict(staged_review={'task': assigned})
        assignment['packet'] = protocol._put_json(self.case.artifacts, packet)
        output = self.case.build_review_output('result')
        records['review_assignment'][assignment['id']] = assignment
        for status, rejected in (('outside-the-scientific-schema', True), (output['value']['status'], False)):
            with self.subTest(status=status):
                scientific = copy.deepcopy(output)
                scientific['value']['status'] = status
                raw = dict(scientific, terminal={'assurances': {}, 'visible_conditions': []})
                attempt = dict(id='terminal-attempt', assignment_id=assignment['id'], status='completed',
                               output=protocol._put_json(self.case.artifacts, raw))
                records['review_attempt'][attempt['id']] = attempt
                with patch.object(protocol, 'authenticate_attempt', return_value=True):
                    failure = review_refusal.classify_observed_output(records, self.case.artifacts, attempt['id'])
                    observed = protocol.read_raw_observation(records, self.case.artifacts, attempt['id'])
                self.assertEqual(observed['raw_output'], raw)
                self.assertEqual(observed['raw_output_digest'], digest(raw))
                if rejected:
                    self.assertIsNotNone(failure)
                    self.assertEqual((failure['code'], failure['check']), ('invalid_value_review', 'review.value'))
                    self.assertEqual(failure['details']['path'], '/value/status')
                else:
                    self.assertIsNone(failure)
        plan, pages, _, _ = make_fixture()
        page_epoch = epoch(id=plan['epoch_id'])
        ownership = pages[0]['output']['ownership']
        assignment['staged_task'] = task(page_epoch, 'answer', answer_ranges=ownership,
            composition_role=plan['role'], composition_plan_root=digest(plan))
        assignment['staged_review'] = dict(kind='task', epoch_id=page_epoch['id'])
        assignment['packet'] = protocol._put_json(self.case.artifacts,
            dict(staged_review={'task': assignment['staged_task']}))
        assignment['composition_plan_id'] = digest(plan)
        records['review_epoch'] = {page_epoch['id']: page_epoch}
        records['review_composition_plan'] = {digest(plan): plan}
        for altered in (False, True):
            with self.subTest(page_altered=altered):
                page = dict(copy.deepcopy(pages[0]['output']), task_id=assignment['staged_task']['id'])
                if altered:
                    page['fragments'][0]['content'] = None
                raw = dict(pages=[page])
                attempt['output'] = protocol._put_json(self.case.artifacts, raw)
                with patch.object(protocol, 'authenticate_attempt', return_value=True):
                    failure = review_refusal.classify_observed_output(records, self.case.artifacts, attempt['id'])
                if altered:
                    self.assertIsNotNone(failure)
                    self.assertEqual(failure['check'], 'review.staged_page')
                else:
                    self.assertIsNone(failure)

    def test_replanning_passes_the_accepted_task_and_partial_selection(self):
        value = epoch(route_id='route')
        assigned = task(value)
        card = full_card(value)
        card.update(kind='fragment', judgments=card['judgments'][:1])
        captured = observation(value, assigned, dict(cards=[card], cells=card['judgments']))
        records = self.case.read_records()
        controller = dict(records['review_assignment']['bar-first'], id=value['controller_assignment_id'],
                          staged_review=dict(kind='controller', epoch_id=value['id']))
        child = dict(controller, id='partial-reader', staged_task=assigned)
        records['review_assignment'].update({controller['id']:controller, child['id']:child})
        records['review_epoch'] = {value['id']:value}
        records['review_task'] = {assigned['id']:dict(epoch_id=value['id'], task=assigned)}
        records['review_task_acceptance'] = {'partial':dict(task_id=assigned['id'], receipt=dict(
            attempt=dict(id=captured['id'], assignment_id=child['id']), staged_observation=captured))}
        with patch.object(staged_protocol, 'authenticate_observation', return_value=True):
            plan = staged_protocol.plan_remaining_tasks(records, self.case.artifacts, controller,
                                                        profiles={'read':profile()})
        reads = [row for row in plan['tasks'] if row['type'] == 'read']
        self.assertEqual([row['claim_ids'] for row in reads], [[value['manifest']['claims'][1]['id']]])
        self.assertNotIn(assigned['id'], [row['id'] for row in reads])

    def test_replanning_preserves_valid_cells_beside_an_invalid_full_card(self):
        value = epoch(route_id='route')
        assigned = task(value)
        card = full_card(value)
        cells = copy.deepcopy(card['judgments'])
        del card['critical_spans']
        captured = observation(value, assigned, dict(cards=[card], cells=cells))
        records = self.case.read_records()
        controller = dict(records['review_assignment']['bar-first'], id=value['controller_assignment_id'],
                          staged_review=dict(kind='controller', epoch_id=value['id']))
        child = dict(controller, id='partial-reader', staged_task=assigned)
        records['review_assignment'].update({controller['id']:controller, child['id']:child})
        records['review_epoch'] = {value['id']:value}
        records['review_task'] = {assigned['id']:dict(epoch_id=value['id'], task=assigned)}
        records['review_task_acceptance'] = {'partial':dict(task_id=assigned['id'], receipt=dict(
            attempt=dict(id=captured['id'], assignment_id=child['id']), staged_observation=captured))}
        with patch.object(staged_protocol, 'authenticate_observation', return_value=True):
            plan = staged_protocol.plan_remaining_tasks(records, self.case.artifacts, controller,
                                                        profiles={'read':profile(), 'combine':profile()})
        self.assertFalse(any(row['type'] == 'read' for row in plan['tasks']))
        self.assertTrue(any(row['type'] == 'integrate' for row in plan['tasks']))
        reconcile = next(row for row in plan['tasks'] if row['type'] == 'integrate')
        planned = staged_protocol.prepare_task_plan(records, self.case.artifacts, controller, value, reconcile)
        packet = protocol._read_json(self.case.artifacts, planned['packet'])
        self.assertEqual(packet['staged_review']['integrate']['full_cards'], [])
        for cell in cells:
            self.assertIn(cell, packet['staged_review']['integrate']['conclusions'])

    def test_answer_tasks_receive_their_actual_accepted_grounding(self):
        value = epoch(route_id='route')
        assigned = task(value)
        card = full_card(value)
        captured = observation(value, assigned, dict(cards=[card], cells=card['judgments']))
        records = self.case.read_records()
        controller = dict(records['review_assignment']['bar-first'], id=value['controller_assignment_id'],
                          staged_review=dict(kind='controller', epoch_id=value['id']))
        child = dict(controller, id='accepted-reader', staged_task=assigned)
        records['review_assignment'].update({controller['id']:controller, child['id']:child})
        records['review_epoch'] = {value['id']:value}
        records['review_task'] = {assigned['id']:dict(epoch_id=value['id'], task=assigned)}
        records['review_task_acceptance'] = {'reading':dict(task_id=assigned['id'], receipt=dict(
            attempt=dict(id=captured['id'], assignment_id=child['id']), staged_observation=captured))}
        bindings = staged_protocol.build_composition_bindings(value, [captured], tasks=[assigned])
        composition_plan = composition.plan_incomplete_composition(value, outcome=incomplete_output(),
            known_conclusions=card['conclusions'], findings=[], page_characters=1000, **{key:bindings[key] for key in
                ('source_root', 'dependency_root', 'selected_observations_root', 'integration_root')})
        with patch.object(staged_protocol, 'authenticate_observation', return_value=True):
            plan = staged_protocol.plan_remaining_tasks(records, self.case.artifacts, controller,
                profiles={}, composition_plan=composition_plan)
        answer = next(row for row in plan['tasks'] if row['type'] == 'answer')
        self.assertEqual(answer['dependencies'], [assigned['id']])
        planned = staged_protocol.prepare_task_plan(records, self.case.artifacts, controller, value, answer, composition_plan)
        packet = protocol._read_json(self.case.artifacts, planned['packet'])
        self.assertIn(card['conclusions'][0], packet['staged_review']['answer']['conclusions'])
        self.assertTrue(packet['staged_review']['answer']['source_fragments'])

    def test_frozen_producer_units_have_one_explicit_evidence_class(self):
        self.oversized()
        controller = self.assign('classes-controller')
        records = self.case.read_records()
        value = records['review_epoch'][controller['staged_review']['epoch_id']]
        self.assertEqual({row.get('evidence_class') for row in value['manifest']['occurrences']}, {'current'})
        from research_harness.review_composition import list_scalar_fields
        self.assertEqual(set(value['completion_requirements']['permissible_decisions']), set(list_scalar_fields('result')))

    def test_incomplete_answer_pages_are_measured_run_and_recorded_from_the_final_attempt(self):
        import sys
        from research_harness import review_worker
        self.oversized()
        controller = self.assign('page-controller')
        records = self.case.read_records()
        value = records['review_epoch'][controller['staged_review']['epoch_id']]
        bindings = staged_protocol.build_composition_bindings(value, [])
        plan = composition.plan_incomplete_composition(value, outcome=incomplete_output(), known_conclusions=[], findings=[],
            page_characters=1000, **{key:bindings[key] for key in
                ('source_root', 'dependency_root', 'selected_observations_root', 'integration_root')})
        plan['evidence_obligations'] = bindings['evidence_obligations']
        planned = staged_protocol.plan_remaining_tasks(records, self.case.artifacts, controller,
                                                       profiles={}, composition_plan=plan)['tasks']
        script = '''
import copy, sys
from pathlib import Path
from research_harness import review_worker, review_transport, review_codex_transport, review_excerpts
from codex_route_fixtures import build_codex_response
review_codex_transport.runtime = lambda: {'adapter':'codex_cli_v1','fixture':'reviewer-excerpt-test'}
review_codex_transport.NATIVE_DECLARATIONS = ()
review_codex_transport.NATIVE_DECLARATION_SETS = {()}
def send(route, request, credential):
    with (Path(sys.argv[1]) / 'page-calls').open('a') as stream: stream.write('call\\n')
    body = review_excerpts.read_sent_packet(request)['staged_review']
    if body['task']['type'] == 'answer':
        source = body['answer']
        page = {key:copy.deepcopy(source[key]) for key in ('protocol','epoch_id','role','task_id','page_id','revision','source_root','dependency_root','ownership','replaces')}
        ground = {'kind':'reviewer_observation', 'reason':'The named scientific scope remains unresolved.'}
        page.update(fragments=[], grounding=[ground], findings=[], dispositions=[])
        for fragment in source['source_fragments']:
            page['fragments'].append(dict(fragment, grounds=[ground]))
        output = {'pages':[page]}
    else:
        output = {'terminal':dict(origin='reviewer', composition_root=body['final']['composition_root'], decisions={'/origin':'reviewer'})}
    return build_codex_response(route, request, output)
review_transport.send_request = send
review_worker.run_worker(Path(sys.argv[1]), sys.argv[2], sys.argv[3], int(sys.argv[4]))
'''
        for index, assigned in enumerate(planned):
            child = self.case.assign('result', 'first', identifier='page-child-' + str(index),
                controller_assignment_id=controller['id'], task_id=assigned['id'], task=assigned, composition_plan=plan)
            request = self.request(child)
            self.assertEqual(child['staged_task']['measurement']['input_characters'], measure_request(records, request))
            with patch.object(review_worker, '_entry_command', return_value=[sys.executable, '-c', script]):
                protocol.invoke_assignment(self.case.store, dict(id='page-attempt-' + str(index), assignment_id=child['id']),
                    expected_revision=self.case.store.revision, request_id='page-run-' + str(index))
        records = self.case.read_records()
        self.assertEqual(len((self.case.root / 'page-calls').read_text().splitlines()), len(planned))
        outcomes = list(records.get('assessment_incomplete', {}).values())
        self.assertEqual(len(outcomes), 1)
        self.assertEqual(outcomes[0]['origin_binding']['attempt_id'], 'page-attempt-' + str(len(planned)-1))
        state = protocol.assignment_state(records, self.case.artifacts, controller['id'])
        self.assertEqual(state['effective']['kind'], 'incomplete')
        self.assertIsNone(state['output'])
        self.assertEqual(state['effective']['incomplete_outcome'], outcomes[0])
        self.assertEqual(records['review_slot_head'][value['slot_id']]['state'], 'incomplete')


def measure_request(records, request):
    from research_harness.review_problem_parts import _measure
    return _measure(records['review_route']['route'], request)
