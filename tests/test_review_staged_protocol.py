"""Staged entry, frozen assignments, raw state and resumable dispatch."""

import copy
import json
from unittest.mock import patch

from problem_parts_fixtures import ResultPartsCase
from research_harness import review_problem_parts as parts, review_protocol as protocol
from research_harness import review_transport as transport, review_excerpts
from research_harness import review_staged, review_worker, review_task_execution
from research_harness import review_staged_protocol as staged_protocol
from research_harness.evidence import digest
from research_harness.errors import ResearchError


_WORKER_SCRIPT = '''
import sys
from pathlib import Path
from research_harness import review_worker, review_protocol, review_transport, review_codex_transport, review_excerpts
from codex_route_fixtures import build_codex_response
review_codex_transport.runtime = lambda: {'adapter':'codex_cli_v1','fixture':'reviewer-excerpt-test'}
review_codex_transport.NATIVE_DECLARATIONS = ()
review_codex_transport.NATIVE_DECLARATION_SETS = {()}
def send(route, request, credential):
    with (Path(sys.argv[1]) / 'calls').open('a') as stream: stream.write('call\\n')
    packet = review_excerpts.read_sent_packet(request)['staged_review']
    claims = [dict(id=packet['task']['id'] + ':' + str(i), kind='record',
                   spans=[{'source_id':row['source_id'], 'text':row['text']}], dependencies=[], children=[])
              for i, row in enumerate(packet['frame']['fields'])]
    return build_codex_response(route, request, {'claims':claims, 'conditions':[], 'dependencies':[], 'unresolved':[]})
review_transport.send_request = send
review_worker.run_worker(Path(sys.argv[1]), sys.argv[2], sys.argv[3], int(sys.argv[4]))
'''

class StagedEntryTests(ResultPartsCase):
    staged_entry = True

    def test_fitting_ordinary_request_retains_packet_prompt_and_history(self):
        records = self.case.read_records()
        with transport.text_only_delivery(True):
            expected = protocol.build_packet(records, self.case.artifacts, None, 'result', 'first')
        assignment = self.assign('ordinary')
        self.assertEqual(self.packet(assignment), expected)
        self.assertEqual(assignment['prompt'], protocol.canonical_prompt('result', orchestration='problem_creation'))
        self.assertTrue(assignment['history'])

    def test_new_oversized_assignment_is_a_controller_with_immutable_frame_children(self):
        self.oversized()
        controller = self.assign('controller')
        self.assertEqual(controller['staged_review']['kind'], 'controller')
        self.assertEqual(controller['history'], [])
        records = self.case.read_records()
        epoch = records['review_epoch'][controller['staged_review']['epoch_id']]
        self.assertEqual(epoch['controller_assignment_id'], controller['id'])
        self.assertEqual(epoch['slot_id'], controller['slot_id'])
        self.assertTrue(controller['staged_review']['task_ids'])
        self.assertLess(len(controller['staged_review']['task_ids']), len(epoch['manifest']['frame_fields']))
        task_id = controller['staged_review']['task_ids'][0]
        child = self.case.assign('result', 'first', identifier='child',
                                 controller_assignment_id=controller['id'], task_id=task_id)
        self.assertEqual(child['history'], [])
        self.assertIsNone(child['history_assignment_id'])
        self.assertTrue(protocol._prompt_matches(child))
        request = protocol.build_assignment_request(records | {'review_assignment': dict(records['review_assignment'], child=child)},
                                                     self.case.artifacts, child)
        self.assertEqual(request['history'], [])
        self.assertEqual(digest(request), child['planned_request_digest'])
        with self.assertRaises(ResearchError):
            self.case.assign('result', 'first', identifier='duplicate',
                             controller_assignment_id=controller['id'], task_id=task_id)

    def test_first_turn_fitted_body_omission_enters_v2_even_below_limit(self):
        self.case.add_reading_and_computation()
        original = transport.request_body
        def omit(route, prompt, packet, history=(), **kwargs):
            result = original(route, prompt, packet, history, **kwargs)
            if packet.get('role') == 'result' and 'staged_review' not in packet:
                sent = review_excerpts.read_sent_packet(result)
                if sent.get('evidence'):
                    old = json.dumps(sent, sort_keys=True, ensure_ascii=False, allow_nan=False)
                    sent['evidence'][0].update(content='', encoding='omitted')
                    # Use the adapter's exact existing serialized packet boundary.
                    for item in result['input']:
                        item['content'] = item['content'].replace(old, json.dumps(sent, sort_keys=True, ensure_ascii=False, allow_nan=False))
            return result
        with patch.object(transport, 'request_body', side_effect=omit):
            assignment = self.assign('body-omitted')
        self.assertEqual(assignment['staged_review']['kind'], 'controller')

    def test_raw_accessor_preserves_exact_legacy_observation_and_native_output(self):
        records = self.case.read_records()
        assignment = records['review_assignment']['bar-first']
        attempt = records['review_attempt']['attempt-bar-first']
        with patch.object(staged_protocol, 'read_raw_observation',
                          side_effect=AssertionError('Legacy observation must not enter staged resolution.')):
            raw = protocol.read_raw_observation(records, self.case.artifacts, attempt['id'])
        self.assertEqual(raw['raw_output'], json.loads(self.case.artifacts.read(attempt['output'])))
        self.assertEqual(raw['raw_output_digest'], digest(raw['raw_output']))
        self.assertEqual(raw['request'], attempt['request'])
        self.assertEqual(raw['status'], 'completed')
        self.assertTrue(raw['instrumented'])
        state = protocol.assignment_state(records, self.case.artifacts, assignment['id'])
        self.assertEqual(state['raw_output'], raw['raw_output'])
        self.assertEqual(state['output'], raw['raw_output'])
        self.assertEqual(state['output_digest'], raw['raw_output_digest'])
        self.assertNotIn('effective', state)

    def test_ordinary_assignment_state_never_calls_composition_identity(self):
        from research_harness import review_composition
        records = self.case.read_records()
        assignment = records['review_assignment']['bar-first']
        attempt = records['review_attempt']['attempt-bar-first']
        with patch.object(review_composition, 'effective_review_output',
                          side_effect=AssertionError('Ordinary state must use its native scientific output.')):
            state = protocol._assignment_state(records, self.case.artifacts, assignment['id'])
        self.assertTrue(state['ready'], state['obligations'])
        self.assertEqual(state['output'], protocol._read_json(self.case.artifacts, attempt['output']))
        self.assertEqual(state['raw_output'], state['output'])
        self.assertNotIn('effective', state)

    def test_ordinary_state_and_outcome_projection_do_not_import_composition(self):
        from research_harness import problem_delivery
        records = self.case.read_records()
        native_import = __import__
        def import_native(name, globals=None, locals=None, fromlist=(), level=0):
            if name.rsplit('.', 1)[-1] == 'review_composition' or 'review_composition' in (fromlist or ()):
                raise AssertionError('Ordinary reads must not load composition code.')
            return native_import(name, globals, locals, fromlist, level)
        with patch('builtins.__import__', side_effect=import_native):
            state = protocol._assignment_state(records, self.case.artifacts, 'bar-first')
            outcomes = problem_delivery.collect_review_outcomes(records, self.case.artifacts)
        self.assertTrue(state['ready'], state['obligations'])
        self.assertTrue(outcomes)
        self.assertTrue(all(row['effective'] is None for row in outcomes))

    def test_controller_pending_and_child_output_never_become_scientific_output(self):
        self.oversized()
        controller = self.assign('pending-controller')
        records = self.case.read_records()
        state = protocol.assignment_state(records, self.case.artifacts, controller['id'])
        self.assertFalse(state['ready'])
        self.assertIsNone(state['output'])
        self.assertIsNone(state['raw_output'])
        self.assertEqual(state['effective']['kind'], 'pending')

    def test_child_request_rejects_history_and_fitting_tampering(self):
        self.oversized()
        controller = self.assign('frozen-controller')
        child = self.case.assign('result', 'first', identifier='frozen-child',
            controller_assignment_id=controller['id'], task_id=controller['staged_review']['task_ids'][0])
        records = self.case.read_records()
        changed = dict(child, history=[{'role': 'assistant', 'content': 'Undelivered continuity'}])
        with self.assertRaises(ResearchError):
            protocol.build_assignment_request(records, self.case.artifacts, changed)
        original = transport.request_body
        def altered(*args, **kwargs):
            result = original(*args, **kwargs)
            result['instructions'] += 'Changed'
            return result
        with patch.object(transport, 'request_body', side_effect=altered):
            with self.assertRaises(ResearchError):
                protocol.build_assignment_request(copy.deepcopy(records), self.case.artifacts, child)

    def test_owned_dispatch_capture_acceptance_and_replay_make_one_call(self):
        import sys
        from research_harness import review_worker
        self.oversized()
        controller = self.assign('run-controller')
        child = self.case.assign('result', 'first', identifier='run-child',
            controller_assignment_id=controller['id'], task_id=controller['staged_review']['task_ids'][0])
        script = _WORKER_SCRIPT
        payload = dict(id='staged-attempt', assignment_id=child['id'])
        revision = self.case.store.revision
        with patch.object(review_worker, '_entry_command', return_value=[sys.executable, '-c', script]):
            result = protocol.invoke_assignment(self.case.store, payload, expected_revision=revision, request_id='staged-run')
            replay = protocol.invoke_assignment(self.case.store, payload, expected_revision=revision, request_id='staged-run')
        self.assertEqual(result, replay)
        self.assertEqual((self.case.root / 'calls').read_text().splitlines(), ['call'])
        records = self.case.read_records()
        attempt = records['review_attempt'][payload['id']]
        self.assertTrue(records['review_task_acceptance'])
        self.assertTrue(records['review_reservation'][attempt['reservation_id']]['released'])
        raw = protocol.read_raw_observation(records, self.case.artifacts, attempt['id'])
        self.assertTrue(raw['instrumented'])
        self.assertTrue(raw['raw_output']['claims'])
        state = protocol.assignment_state(records, self.case.artifacts, child['id'])
        self.assertIsNone(state['output'])
        self.assertEqual(state['raw_output'], raw['raw_output'])
        self.assertTrue(state['accepted'])
    def test_core_task_envelope_can_be_assigned_after_the_initial_frontier(self):
        from research_harness import review_staged as staged
        self.oversized()
        controller = self.assign('frontier-controller')
        records = self.case.read_records()
        epoch = records['review_epoch'][controller['staged_review']['epoch_id']]
        source = next(row for row in epoch['manifest']['sources'] if row['kind'] == 'item')
        task = staged.make_task(epoch, 'read', inputs=[staged.source_span(source, 0, len(source['text']))],
                                occurrence_ids=[epoch['manifest']['occurrences'][0]['id']])
        child = self.case.assign('result', 'first', identifier='next-child',
            controller_assignment_id=controller['id'], task_id=task['id'], task=task)
        self.assertEqual(child['staged_task']['id'], task['id'])
        self.assertEqual(child['history'], [])
        self.assertIn(task['id'], self.case.read_records()['review_task'])

    def test_new_focused_and_repair_packet_entry_and_unsupported_routes(self):
        records = self.case.read_records()
        route = records['review_route']['route']
        packet = dict(subject={'kind':'problem'}, role='adjudicator', evidence=[],
                      objection={'reason':'x' * 200000})
        value = dict(id='focused', role='adjudicator', context={})
        with patch.object(transport, 'local_input_limit', return_value=50000):
            entered = parts.prepare_packet(records, self.case.artifacts, value, route, packet,
                protocol.canonical_prompt('adjudicator', orchestration='problem_creation'), [])
        self.assertEqual(entered['staged_review']['kind'], 'controller')
        repair = dict(packet, role='result', context_repair={'assignment_id':'prior'})
        value = dict(value, role='result')
        with patch.object(transport, 'local_input_limit', return_value=50000):
            entered = parts.prepare_packet(records, self.case.artifacts, value, route, repair,
                protocol.canonical_prompt('result', repair=True, orchestration='problem_creation'), [])
        self.assertEqual(entered['staged_review']['kind'], 'controller')
        self.assertFalse(parts.allows_staged_entry(value, dict(route, adapter='openai_responses_v1'), repair))

    def test_explicit_upgrade_binds_a_no_output_predecessor_and_installs_pending_slot(self):
        prior = self.assign('no-output')
        successor = self.case.assign('result', 'first', identifier='upgrade',
                                     staged_upgrade={'assignment_id': prior['id']})
        records = self.case.read_records()
        epoch = records['review_epoch'][successor['staged_review']['epoch_id']]
        self.assertEqual(epoch['predecessor']['assignment_id'], prior['id'])
        self.assertTrue(epoch['predecessor']['no_output'])
        self.assertEqual(successor['slot_id'], prior['slot_id'])
        self.assertEqual(records['review_slot_head'][prior['slot_id']]['state'], 'pending')

    def test_record_assignment_can_record_an_explicit_capacity(self):
        self.oversized()
        payload = dict(id='capacity-controller', role='result', route_id='route', dossier_id=None,
                       reviewer_id='first', author_id='author', context={},
                       scheduler={'store_limit':2, 'route_limits':{'route':2}})
        protocol.record_assignment(self.case.store, payload, expected_revision=self.case.store.revision,
                                   request_id='capacity-assignment')
        policy = self.case.read_records()['review_capacity_policy']['store']
        self.assertEqual(policy['store_limit'], 2)

    def test_raw_capture_survives_acceptance_interruption_without_second_worker(self):
        # Exercise the same owned process path, then resume only acceptance.
        from research_harness import review_task_execution as execution
        original = execution.accept_observation
        interrupted = {'done':False}
        def accept(*args, **kwargs):
            if not interrupted['done']:
                interrupted['done'] = True
                raise ResearchError('store_busy', 'Interrupted after capture')
            return original(*args, **kwargs)
        with patch.object(execution, 'accept_observation', side_effect=accept):
            with self.assertRaises(ResearchError) as raised:
                self.test_owned_dispatch_capture_acceptance_and_replay_make_one_call()
        self.assertEqual(raised.exception.code, 'store_busy')
        before = (self.case.root / 'calls').read_text()
        records = self.case.read_records()
        self.assertEqual(len(records['review_observation']), 1)
        raw = protocol.read_raw_observation(records, self.case.artifacts, 'staged-attempt')
        self.assertTrue(raw['raw_output']['claims'])
        pending = protocol.assignment_state(records, self.case.artifacts, 'run-child')
        self.assertEqual(pending['raw_output'], raw['raw_output'])
        self.assertFalse(pending['accepted'])
        with patch('research_harness.review_worker.launch_worker', side_effect=AssertionError('No second worker')):
            protocol.invoke_assignment(self.case.store, dict(id='staged-attempt', assignment_id='run-child'),
                                       expected_revision=self.case.store.revision, request_id='staged-run')
        self.assertEqual((self.case.root / 'calls').read_text(), before)

    def test_paged_selection_assembles_science_and_retains_raw_terminal_digest(self):
        from research_harness import review_staged as staged, review_composition as composition
        from test_review_composition import make_fixture, worked_result
        from test_review_staged_fixtures import epoch
        plan, pages, choices, _ = make_fixture()
        value = epoch()
        plan.update(epoch_id=value['id'], slot_id=value['slot_id'], subject=value['subject'])
        for page in pages:
            page['output']['epoch_id'] = value['id']
            page['raw_output_digest'] = digest(page['output'])
        observations = [dict(id=page['attempt_id'], output={'pages':[page['output']]}) for page in pages]
        plan.update(staged_protocol.build_composition_bindings(value, observations))
        for page in pages:
            page['output'].update(source_root=plan['source_root'], dependency_root=plan['dependency_root'])
            page['raw_output_digest'] = digest(page['output'])
        observations = [dict(id=page['attempt_id'], output={'pages':[page['output']]}) for page in pages]
        plan.update(staged_protocol.build_composition_bindings(value, observations))
        root = composition.build_root(plan, pages)
        terminal = dict(protocol=composition.PROTOCOL, epoch_id=value['id'], role='result',
                        composition_root=root['digest'], choices=choices)
        raw = dict(terminal=dict(assurances={kind: choices['/value/assurances/' + kind + '/status'] for kind in staged.ASSURANCES}, visible_conditions=[], composition_root=root['digest'],
                                 integration_id='integrated', decisions=terminal['choices']))
        observations = [dict(id=page['attempt_id'], output={'pages':[page['output']]}) for page in pages]
        observations.append(dict(id='terminal-attempt', output=raw))
        selection = staged_protocol.build_composition(value, plan, observations, observations[-1])
        selection.update(controller_id='controller', kind='paged', completion_root=digest('complete'))
        effective = composition.effective_review_output('controller', selection)
        self.assertEqual(effective['effective_output'], worked_result())
        self.assertEqual(selection['raw_terminal_digest'], digest(raw))
        self.assertNotEqual(selection['raw_terminal_digest'], effective['terminal_raw_output_digest'])
        for name in ('evidence_currency', 'source_root', 'selected_observations_root', 'integration_root'):
            with self.subTest(name=name), self.assertRaises(ResearchError):
                staged_protocol.build_composition(value, dict(plan, **{name:digest('unrelated')}), observations, observations[-1])

    def test_actual_input_binds_the_cards_that_reached_the_task(self):
        from test_review_staged_fixtures import epoch, task, full_card
        value = epoch()
        card = full_card(value)
        selected = dict(id='read-observation', task_id='read-task', raw_output_digest=digest({'cards':[card]}),
                        output={'cards':[card]})
        assigned = task(value, 'combine', card_ids=[card['id']], scope='joint_card')
        packet = staged_protocol.build_packet(value, assigned, observations=[selected])
        actual = staged_protocol.read_actual_input(value, assigned, packet)
        self.assertEqual(actual['complete_card_roots'][card['id']],
                         digest(review_staged.compact_card(card, manifest=value['manifest'])))
        assigned = task(value, 'integrate')
        packet = staged_protocol.build_packet(value, assigned, observations=[selected])
        self.assertEqual(staged_protocol.read_actual_input(value, assigned, packet)['full_card_roots'][card['id']], digest(card))

    def test_failed_capture_is_recorded_and_an_explicit_retry_is_bounded(self):
        import sys
        from research_harness import review_worker
        self.oversized()
        controller = self.assign('retry-controller')
        child = self.case.assign('result', 'first', identifier='retry-child',
            controller_assignment_id=controller['id'], task_id=controller['staged_review']['task_ids'][0])
        failed_script = _WORKER_SCRIPT.replace(
            "return build_codex_response(route, request, {'claims':claims, 'conditions':[], 'dependencies':[], 'unresolved':[]})",
            "raise OSError('Transient failure')")
        with patch.object(review_worker, '_entry_command', return_value=[sys.executable, '-c', failed_script]):
            result = protocol.invoke_assignment(self.case.store, dict(id='failed-capture', assignment_id=child['id']),
                expected_revision=self.case.store.revision, request_id='failed-call')
        self.assertEqual(result['result']['status'], 'failed')
        raw = protocol.read_raw_observation(self.case.read_records(), self.case.artifacts, 'failed-capture')
        self.assertIsNone(raw['raw_output'])
        with patch.object(review_worker, '_entry_command', return_value=[sys.executable, '-c', _WORKER_SCRIPT]):
            protocol.invoke_assignment(self.case.store, dict(id='retry-capture', assignment_id=child['id']),
                expected_revision=self.case.store.revision, request_id='explicit-retry')
        self.assertEqual((self.case.root / 'calls').read_text().splitlines(), ['call', 'call'],
                         self.case.read_records()['review_attempt'])
        with self.assertRaises(ResearchError) as raised:
            protocol.invoke_assignment(self.case.store, dict(id='third', assignment_id=child['id']),
                expected_revision=self.case.store.revision, request_id='third-call')
        self.assertEqual(raised.exception.code, 'review_resampling_forbidden')

    def test_card_segment_packet_contains_the_segment_and_its_bounded_context(self):
        from test_review_staged_fixtures import epoch, task, full_card
        value = epoch()
        card = full_card(value)
        card['establishes'] = 'x' * 200000
        span = review_staged.card_span(card, 0, 200, manifest=value['manifest'])
        assigned = task(value, 'combine', card_ids=[card['id']], card_segments=[span], scope='card_segment')
        packet = staged_protocol.build_packet(value, assigned, observations=[dict(
            id='read', task_id='reader', raw_output_digest=digest(card), output={'cards':[card]})])
        self.assertLess(len(json.dumps(packet)), 20000)
        self.assertNotIn(card['establishes'], json.dumps(packet))
        self.assertEqual(staged_protocol.read_actual_input(value, assigned, packet)['card_segments'], [span])

    def test_selected_child_contamination_invalidates_controller_credit(self):
        self.test_owned_dispatch_capture_acceptance_and_replay_make_one_call()
        self.case.mutate(protocol.record_context_event, dict(id='child-contamination', assignment_id='run-child',
            kind='contamination', source='foreign_input', reason='Foreign input reached this task.', evidence=[self.case.supporting]))
        records = self.case.read_records()
        state = protocol.assignment_state(records, self.case.artifacts, 'run-controller')
        self.assertEqual(state['context_status'], 'contaminated')
        self.assertFalse(state['ready'])
        self.assertEqual(state['completion']['progress']['source_characters'], 0)

    def test_evidence_change_keeps_the_frozen_epoch_but_blocks_its_dispatch(self):
        self.oversized()
        controller = self.assign('currency-controller')
        child = self.case.assign('result', 'first', identifier='currency-child',
            controller_assignment_id=controller['id'], task_id=controller['staged_review']['task_ids'][0])
        self.case.expected['value']['motivation'][0]['text'] = 'The scientific claim changed.'
        records = self.case.read_records()
        state = protocol.assignment_state(records, self.case.artifacts, controller['id'])
        self.assertEqual(state['effective']['currency_state'], 'stale')
        self.assertFalse(state['ready'])
        with self.assertRaises(ResearchError) as raised:
            protocol.invoke_assignment(self.case.store, dict(id='stale-call', assignment_id=child['id']),
                expected_revision=self.case.store.revision, request_id='stale-call')
        self.assertEqual(raised.exception.code, 'review_epoch_stale')
        self.assertNotIn('review_reservation', self.case.read_records())

    def build_terminal_records(self, incomplete=False):
        from test_review_staged_fixtures import epoch, selection
        from test_review_composition import worked_result
        value = epoch(route_id='route')
        tasks, chosen = selection(value)
        final = tasks[-1]
        final['type'] = 'final'
        final['digest'] = digest({key:item for key,item in final.items() if key != 'digest'})
        terminal_observation = chosen['observations'][0]
        terminal_observation['task_digest'] = final['digest']
        role = worked_result()
        for assurance in role['value']['assurances']:
            assurance['status'] = 'passed'
        terminal_observation['output'].update(role)
        if incomplete:
            terminal_observation['output'] = {'assessment_incomplete': dict(
                unresolved_scope=[dict(id='missing', scope={'claim_ids':['c']}, cause='exhausted',
                    affected_decisions=['problem_value'], exhausted_tasks=['read'], scope_kind='unresolved',
                    next_action={'workflow':'problem-evidence', 'scope':{'claim_ids':['c']}, 'prerequisite':'new evidence'})],
                recovery_work=[dict(workflow='problem-evidence', command='record-evidence', payload={}, text='Supply a new source.')])}
        for observed in chosen['observations']:
            observed['raw_output_digest'] = digest(observed['output'])
        records = self.case.read_records()
        template = records['review_assignment']['bar-first']
        controller = dict(template, id=value['controller_assignment_id'], role='result', subject=value['subject'], slot_id=value['slot_id'],
            staged_review=dict(kind='controller', protocol=review_staged.PROTOCOL, epoch_id=value['id']))
        records['review_assignment'][controller['id']] = controller
        records['review_epoch'] = {value['id']:value}
        records['review_slot_head'] = {value['slot_id']:dict(slot_id=value['slot_id'], epoch_id=value['id'],
                                                           state='pending', outcome=None)}
        records['review_task'] = {row['id']:dict(task=row, epoch_id=value['id']) for row in tasks}
        records['review_task_acceptance'] = {}
        for index, observed in enumerate(chosen['observations']):
            assigned = next(row for row in tasks if row['id'] == observed['task_id'])
            child = dict(template, id='selected-child-' + str(index), role='result', subject=value['subject'], staged_task=assigned,
                staged_review=dict(kind='task', protocol=review_staged.PROTOCOL, epoch_id=value['id'],
                                   controller_assignment_id=controller['id'], task_id=assigned['id']))
            attempt = dict(id=observed['id'], assignment_id=child['id'], origin='instrumented', status='completed',
                           output=protocol._put_json(self.case.artifacts, observed['output']), recorded_revision=index+1)
            records['review_assignment'][child['id']] = child
            records['review_attempt'][attempt['id']] = attempt
            records['review_task_acceptance'][str(index)] = dict(receipt=dict(attempt=attempt, staged_observation=observed))
        if incomplete:
            receipt = next(row['receipt'] for row in records['review_task_acceptance'].values()
                           if 'assessment_incomplete' in row['receipt']['staged_observation']['output'])
            child = records['review_assignment'][receipt['attempt']['assignment_id']]
            with patch.object(staged_protocol, 'authenticate_observation', return_value=True):
                changes, _ = staged_protocol.prepare_attempt(records, self.case.artifacts, child, receipt)
            for kind, identifier, record in changes:
                records.setdefault(kind, {})[identifier] = record
        return records, value, role, terminal_observation['output']

    def test_completed_controller_exposes_science_and_raw_terminal_separately(self):
        records, value, role, raw = self.build_terminal_records()
        with patch.object(staged_protocol, 'authenticate_observation', return_value=True), \
                patch.object(staged_protocol, 'matches_currency', return_value=True):
            state = protocol._assignment_state(records, self.case.artifacts, value['controller_assignment_id'])
        self.assertTrue(state['ready'], state['obligations'])
        self.assertEqual(state['output'], role)
        self.assertEqual(state['raw_output'], raw)
        self.assertEqual(state['effective']['effective_output_digest'], digest(role))
        self.assertEqual(state['effective']['terminal_raw_output_digest'], digest(raw))
        self.assertNotEqual(state['output_digest'], state['raw_output_digest'])

    def test_reviewer_incomplete_authorization_has_no_scientific_output(self):
        records, value, _, raw = self.build_terminal_records(incomplete=True)
        with patch.object(staged_protocol, 'authenticate_observation', return_value=True), \
                patch.object(staged_protocol, 'matches_currency', return_value=True):
            state = protocol._assignment_state(records, self.case.artifacts, value['controller_assignment_id'])
        self.assertFalse(state['ready'])
        self.assertIsNone(state['output'])
        self.assertEqual(state['raw_output'], raw)
        self.assertEqual(state['effective']['kind'], 'incomplete')
        self.assertEqual(state['effective']['incomplete_outcome']['origin'], 'reviewer')

    def test_frame_continuity_indexes_large_inherited_findings_instead_of_replaying_them(self):
        from test_review_staged_fixtures import epoch, task
        manifest = review_staged.build_manifest({'material_objections':[{'id':'old', 'text':'x' * 200000}]}, [], [],
                                                inherited_findings=[{'id':'old', 'text':'x' * 200000}])
        value = epoch(manifest=manifest)
        source = next(row for row in manifest['sources'] if row['kind'] == 'frame' and row['characters'] > 100)
        assigned = review_staged.make_task(value, 'frame', inputs=[review_staged.source_span(source, 0, 100)],
                                           measurement=task(epoch())['measurement'])
        packet = staged_protocol.build_packet(value, assigned)
        self.assertLess(len(json.dumps(packet)), 20000)
        self.assertEqual(packet['staged_review']['inherited'][0]['id'], 'old')
        self.assertEqual(packet['staged_review']['inherited'][0]['record_digest'], digest(manifest['inherited_findings'][0]))

    def test_composed_terminal_receives_integrated_grounds_and_answers(self):
        from test_review_staged_fixtures import epoch, task, full_card, observation
        value = epoch()
        card = full_card(value)
        card['establishes'] = 'x' * 200000
        answer = dict(id='answered', finding_id='finding', disposition='answered', reason='The original settles it.',
                      grounds=[card['conclusions'][0]['id']])
        observed = observation(value, task(value), dict(cards=[card], answers=[answer], findings=[dict(
            id='finding', text='The claim needs a condition.', adverse=True, classification={'decisions':[], 'reason':'scope'},
            claim_ids=[], grounds=[card['conclusions'][0]['id']])]), id='read')
        material = staged_protocol.project_terminal_material(value, [observed])
        self.assertLess(len(json.dumps(material)), 20000)
        self.assertEqual(material['cells'][0]['id'], card['judgments'][0]['id'])
        self.assertEqual(material['conclusions'][0]['id'], card['conclusions'][0]['id'])
        self.assertEqual(material['answers'], [answer])
        self.assertTrue(material['claims'][0]['cells'])

    def test_final_packet_uses_ancestor_conditions_and_the_integration_root(self):
        from test_review_staged_fixtures import epoch, task, full_card, observation
        value = epoch()
        condition = dict(id='condition', text='The original holds under this condition.', grounds=[])
        card = full_card(value, conditions=[condition])
        ancestor = observation(value, task(value), {'cards':[card]}, id='reader')
        integration = dict(id='integrated', child_ids=[], decision_constraints={})
        dependency = observation(value, task(value, 'integrate'),
            dict(integrations=[integration], original_checks=[dict(id='original-check', conclusions=[])]),
            id='integration-observation')
        packet = staged_protocol.build_packet(value, task(value, 'final'), observations=[dependency],
                                               terminal_observations=[ancestor, dependency])
        final = packet['staged_review']['final']
        self.assertEqual(final['integration_id'], 'integrated')
        self.assertEqual(final['conditions'], ['condition'])

    def test_focused_epoch_labels_original_and_current_ownership_of_a_shared_body(self):
        records = self.case.read_records()
        body = self.case.artifacts.put(b'Primary evidence shared by both contexts.', 'text/plain')
        evidence = copy.deepcopy(self.case.expected)
        evidence['computations'] = [dict(execution_id='shared', execution=dict(outputs=[dict(id='out',artifact=body)]))]
        packet = dict(role='adjudicator', subject=protocol.build_problem_subject(records, 'adjudicator'),
            problem_evidence=evidence, original_evidence=[body], objection=dict(id='objection'),
            assessments=[dict(id='original-assessment', reason='The original assessment.')],
            supplied_source_mappings=[dict(id='original-mapping', text='The originally supplied mapping.')],
            evidence=[dict(artifact=body,content='Primary evidence shared by both contexts.',encoding='utf-8')],
            staged_review=dict(kind='controller'))
        packet['digest'] = digest(packet)
        controller = dict(id='focused-controller', role='adjudicator', subject=packet['subject'], reviewer_id='judge',
            route_id='route', slot_id='focused-slot', author_id='author', context=dict(objection_id='objection',
                review_ids=['review-bar-first','review-bar-second']), packet=protocol._put_json(self.case.artifacts,packet))
        changes = staged_protocol.prepare_controller(records, self.case.artifacts, controller, packet,
                                                       records['review_route']['route'])
        value = next(row for kind,_,row in changes if kind == 'review_epoch')
        occurrences = value['manifest']['occurrences']
        self.assertEqual({row['evidence_class'] for row in occurrences}, {'original_context','current'})
        for key in ('assessments', 'supplied_source_mappings'):
            unit = next((row for row in occurrences if row['key'] == key), None)
            self.assertIsNotNone(unit, key)
            self.assertEqual(unit['evidence_class'], 'original_context')
            self.assertEqual(unit['item'], packet[key][0])
        self.assertEqual(len(value['manifest']['bodies']), 1)
