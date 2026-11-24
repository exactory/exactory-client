"""RC10 recovery admission, inspection evidence and staged planner boundaries."""

import copy
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

from ci import series_code_reach as reach
from ci.reviewer_program_guard import use_absent_programs
from ci.series_code_groups import read_group_entries
from research_harness import cli, review_continuation, review_plan_steps, review_series, stop_classes
from research_harness import review_protocol as protocol, review_transport
from research_harness.errors import ResearchError
from codex_route_fixtures import build_codex_response
from review_parts_fixtures import PartsResultCase, make_output
from problem_parts_fixtures import ResultPartsCase
from test_series_code_reach import record_reachable_errors
import test_review_continuation as continuation_fixtures


ROOT = Path(__file__).resolve().parents[1]
GROUPS = ROOT / 'tests/fixtures/series_code_groups/d2-rows-close-recovery.json'
CANDIDATES = (
    'review_evidence_undelivered', 'invalid_reference_handle',
    'review_parts_frame_too_large', 'review_parts_final_too_large',
    'review_parts_inventory_too_large', 'review_parts_no_progress',
    'review_task_pending', 'review_bar_continuity', 'problem_author_unknown',
    'review_epoch_stale', 'review_not_independent', 'review_route_changed',
    'review_source_continuation_mismatch', 'invalid_development',
    'review_assignment_replaced', 'review_assignment_superseded',
    'review_source_continuation_unverified', 'openness_reading_incomplete',
    'review_series_superseded', 'request_budget', 'source_pending', 'xml_timeout',
    'cohort_selection_conflict', 'cohort_selection_stale', 'invalid_cohort_selection',
    'malformed_visual_asset', 'unsupported_visual_asset', 'validity_evidence_missing',
    'component_requirement_missing', 'verification_task_required',
    'fulltext_resume_unsupported', 'gate_override_invalid', 'invalid_import',
    'invalid_reading', 'execution_observation_required', 'query_ceiling',
    'source_deferred', 'readiness_review_stale', 'sample_missing', 'invalid_search',
)


def build_staged_output(body, root):
    """Ground each reading cell in its delivered original without repeating the frame."""
    from test_review_staged_frontier import build_output
    if body['task']['type'] == 'read':
        original = next(row for row in body['originals']
                        if row['span']['source_id'].startswith('artifact:'))
        body = dict(body, originals=[original])
    output = build_output(body, root)
    if body['task']['type'] == 'final':
        for check in output['assurance_checks'] + output['decision_checks']:
            check['grounds'] = check['grounds'][:1]
    return output


class _StagedCase(ResultPartsCase):
    staged_entry = True
    timeout_seconds = 600


class RecoveryRowTests(unittest.TestCase):
    def setUp(self):
        from research_harness import bar_sources, review_protocol
        released = patch.object(review_protocol, 'NEW_SESSION_RULES', (bar_sources.PROTOCOL_V3,))
        released.start()
        self.addCleanup(released.stop)
        super().setUp()

    def prepare_fixture(self, cls=continuation_fixtures.ContinuationTests):
        fixture = cls()
        self.addCleanup(fixture.doCleanups)
        fixture.setUp()
        return fixture

    def run_series(self, case, head):
        rows = []
        result = review_series.run_series(case.store, head, emit=rows.append)
        return result, rows

    def execute_step(self, case, step, choices=None):
        """Fill only declared choices, keeping the printed payload intact."""
        payload = copy.deepcopy(step.get('payload', {}))
        for path, value in (choices or {}).items():
            self.assertIn(path, step['chooses'])
            target = payload
            keys = path.split('.')
            for key in keys[:-1]:
                target = target.setdefault(key, {})
            target[keys[-1]] = value
        return cli.OPERATIONS[step['command']](case.store, payload,
            expected_revision=case.store.revision, request_id='step-' + str(case.store.revision))['result']

    def assert_inspections(self, case, line, group='named_cause'):
        expected = stop_classes.STOP_GROUPS[group]['next']
        decision = line['details']['decision']
        self.assertEqual(decision['owner_decides'], expected['owner_decides'])
        self.assertEqual([row['answer'] for row in decision['then']],
                         [row['answer'] for row in expected['then']])
        for answer in decision['then']:
            for step in answer['steps']:
                self.assertEqual(step.get('chooses', []), [])
                arguments = step['arguments']
                self.assertEqual(arguments, {'--workspace': str(case.store.root)})
                if step['command'] == 'record-check':
                    from research_harness.record_check import check_record
                    # The record check lists the path of the Codex program beside each runtime read. The test
                    # states that the machine has none.
                    use_absent_programs(self)
                    report = check_record(case.store.root)
                    self.assertIn('series_stops', report)
                else:
                    self.assertEqual(step['command'], 'status')
                    report = cli.status_report(case.store)
                    self.assertIn('obligations', report)
                    self.assertIn('next', report)

    def test_all_candidates_have_exactly_one_reviewed_row(self):
        self.assertEqual(len(CANDIDATES), 40)
        for code in CANDIDATES:
            with self.subTest(code=code):
                self.assertTrue(code in stop_classes.STOP_REGISTRY, code)
        entries = json.loads(GROUPS.read_text())
        members = {code for row in entries.values() for code in row['members']}
        own = {code for code in CANDIDATES if 'group' not in stop_classes.STOP_REGISTRY[code]}
        self.assertEqual(set(CANDIDATES), members | own)
        self.assertEqual(members & own, set())
        self.assertEqual(own, set())
        report = reach.analyze_repository(ROOT)
        checked = read_group_entries(ROOT, report, stop_classes.STOP_REGISTRY, stop_classes.STOP_GROUPS)
        for group, row in entries.items():
            self.assertEqual(set(row['members']), set(getattr(stop_classes,
                '_NAMED_CAUSE_CODES_RECOVERY' if group == 'named_cause' else '_STORED_STATE_CODES_RECOVERY')))
            for code, sites in row['members'].items():
                self.assertEqual(checked[group]['members'][code], sites)

    def check_group_members(self, codes, group):
        fixture = self.prepare_fixture()
        for code in codes:
            with self.subTest(code=code):
                def refuse(store, payload, **identity):
                    raise ResearchError(code, 'The candidate retains its inspected cause.', {'candidate': code})
                with record_reachable_errors('run_series') as observed:
                    with patch.dict(cli.OPERATIONS, {'review-run': refuse}):
                        result, rows = self.run_series(fixture.case, fixture.assignment['id'])
                self.assertIn(code, observed)
                self.assertEqual(result, 3)
                last = rows[-1]
                self.assertEqual((last['state'], last['code']), ('stop', code))
                self.assertNotIn('unlisted', last['details'])
                self.assertEqual(last['error']['details'], {'candidate': code})
                self.assertEqual(last['details']['decision']['owner_decides'],
                                 stop_classes.STOP_GROUPS[group]['next']['owner_decides'])

    def test_named_cause_members_end_at_the_group_decision_inside_the_recorder(self):
        self.assertTrue(stop_classes._NAMED_CAUSE_CODES_RECOVERY)
        self.check_group_members(stop_classes._NAMED_CAUSE_CODES_RECOVERY, 'named_cause')

    def test_stored_state_members_end_at_the_group_decision_inside_the_recorder(self):
        self.assertTrue(stop_classes._STORED_STATE_CODES_RECOVERY)
        self.check_group_members(stop_classes._STORED_STATE_CODES_RECOVERY, 'stored_state')

    def prepare_parts(self):
        from codex_route_fixtures import limit_codex_input
        from review_parts_fixtures import measure_input_limit
        fixture = self.prepare_fixture(PartsResultCase)
        fixture.case.expected['openness_searches'] = []
        for index in range(6):
            value = fixture.case.capture_search()
            value.update(id='search-' + str(index), question='A bounded sequence comparison.', impact='x' * 24000)
            self.execute_step(fixture.case, dict(command='search', payload=value, chooses=[]))
            saved = fixture.case.read_records()['literature_search'][value['id']]
            fixture.case.expected['openness_searches'].append(dict(search=saved, sources=[]))
        limit_codex_input(fixture, measure_input_limit(85000))
        return fixture

    def test_frame_overflow_inspects_before_a_partial_result_route_recovery(self):
        fixture = self.prepare_parts()
        first = fixture.assign('parts-head')
        fixture.invoke(first)
        with patch.object(review_transport, 'CODEX_INPUT_LIMIT_CHARS', 1000):
            result, rows = self.run_series(fixture.case, first['id'])
        self.assertEqual(result, 3)
        line = rows[-1]
        self.assertEqual(line['code'], 'review_parts_frame_too_large')
        self.assertEqual(stop_classes.STOP_REGISTRY[line['code']]['group'], 'named_cause')
        self.assert_inspections(fixture.case, line)
        # Explore this result-role recovery without admitting the shared code.
        # Native and verification routes have different untested prerequisites.
        records = fixture.case.read_records()
        steps = [review_plan_steps.describe_step(records, fixture.case.artifacts, first, number)
                 for number in review_plan_steps.select_route_steps(records, fixture.case.artifacts, first)]
        self.assertEqual([step['command'] for step in steps],
                         ['review-context', 'review-route', 'review-assignment'])
        evidence = fixture.case.artifacts.put(b'The owner chooses a route that serves this review.', 'text/plain')
        self.execute_step(fixture.case, steps[0], dict(id='retirement', reason='Use a serving route.', evidence=[evidence]))
        self.execute_step(fixture.case, steps[1], dict(id='serving-route'))
        probe = steps[1]['then'][0]
        def respond(route, request, credential):
            from research_harness.review_excerpts import read_sent_packet
            packet = read_sent_packet(request)
            return build_codex_response(route, request,
                dict(allowed_control=packet['allowed_control'], evidence_control=packet['evidence_control'],
                     excluded_controls=[]))
        with patch.object(review_transport, 'send_request', side_effect=respond):
            self.execute_step(fixture.case, probe, dict(id='serving-probe', route_id='serving-route'))
        self.execute_step(fixture.case, steps[2], {
            'id': 'repaired-head', 'route_id': 'serving-route', 'reviewer_id': 'first',
            'context.context_repair.probe_id': 'serving-probe',
            'context.context_repair.reason': 'Assess the retained work on the serving route.',
            'context.context_repair.event_ids': ['retirement'], 'context.context_repair.evidence': [evidence]})
        upcoming = review_continuation.plan_next(fixture.case.read_records(), fixture.case.artifacts, first['id'])
        self.assertEqual(upcoming['head_id'], 'repaired-head')
        self.assertNotEqual(upcoming.get('code'), line['code'])
        def answer_review(route, request, credential):
            from research_harness.review_excerpts import read_sent_packet
            packet = read_sent_packet(request)
            output = (make_output(packet) if 'review_parts' in packet and packet['review_parts']['kind'] != 'final'
                      else fixture.final_output())
            return build_codex_response(route, request, output)
        with patch.object(review_transport, 'send_request', side_effect=answer_review):
            _, following = self.run_series(fixture.case, first['id'])
        self.assertNotEqual(following[-1].get('code'), line['code'])
        self.assertTrue(any(row.get('state') == 'run' and row.get('head_id') == 'repaired-head'
                            for row in following))
        self.assertTrue(any(attempt['assignment_id'] == 'repaired-head'
                            and attempt['status'] == 'completed'
                            for attempt in fixture.case.read_records()['review_attempt'].values()))

    def test_group_obligations_replace_both_planner_boundaries_but_own_rows_keep_their_plan(self):
        fixture = self.prepare_fixture()
        records = fixture.case.read_records()
        assignment = records['review_assignment'][fixture.assignment['id']]
        for code in ('review_invocation_unverified', 'review_epoch_stale'):
            for state in ('handoff', 'stop'):
                with self.subTest(code=code, state=state):
                    original = dict(state=state, code=code, details=dict(message='Inspect this obligation.'))
                    if state == 'handoff':
                        original['command'] = 'review-assignment'
                    completed = review_plan_steps.complete_plan(records, fixture.case.artifacts, assignment, original)
                    self.assertEqual(completed['state'], 'stop')
                    self.assertNotIn('command', completed)
                    self.assertEqual(completed['details']['decision'],
                                     stop_classes.describe_terminal(code)['details']['decision'])
                    self.assertEqual(original['state'], state)
        original = dict(state='handoff', code='review_context_unverified', command='review-assignment', details={})
        completed = review_plan_steps.complete_plan(records, fixture.case.artifacts, assignment, original)
        self.assertEqual((completed['state'], completed['code'], completed['command']),
                         ('handoff', 'review_context_unverified', 'review-probe'))

    def prepare_staged(self):
        fixture = self.prepare_fixture(_StagedCase)
        controller = fixture.assign_staged('controller')
        return fixture, controller

    def execute_plan(self, case, plan):
        return cli.OPERATIONS[plan['command']](case.store, plan['payload'],
            expected_revision=case.store.revision,
            request_id=plan.get('request_id', 'plan-' + str(case.store.revision)))['result']

    def test_staged_unverified_capture_uses_the_named_cause_decision(self):
        from research_harness import review_worker
        from test_review_staged_frontier import WORKER
        fixture, controller = self.prepare_staged()
        case = fixture.case
        self.execute_plan(case, review_continuation.plan_next(case.read_records(), case.artifacts, controller['id']))
        with patch.object(review_worker, '_entry_command', return_value=[sys.executable, '-c', WORKER]):
            self.execute_plan(case, review_continuation.plan_next(case.read_records(), case.artifacts, controller['id']))
        key, accepted = next(iter(case.read_records()['review_task_acceptance'].items()))
        damaged = copy.deepcopy(accepted)
        damaged['receipt']['attempt']['output'] = case.artifacts.put(b'{}', 'application/json')
        case.put('review_task_acceptance', key, damaged)
        before = case.read_records()
        state = protocol.assignment_state(before, case.artifacts, controller['id'])
        self.assertIn('review_invocation_unverified', [row['code'] for row in state['obligations']])
        plan = review_continuation.plan_next(before, case.artifacts, controller['id'])
        self.assertEqual((plan['state'], plan['code']), ('stop', 'review_invocation_unverified'))
        self.assertNotIn('command', plan)
        self.assertEqual(plan['details']['decision'],
                         stop_classes.describe_terminal('review_invocation_unverified')['details']['decision'])
        _, rows = self.run_series(case, controller['id'])
        self.assertEqual((rows[-1]['state'], rows[-1]['code']), ('stop', 'review_invocation_unverified'))
        self.assert_inspections(case, rows[-1], 'named_cause')

    def test_both_stale_epoch_states_keep_inspection_until_each_cause_has_a_recovery(self):
        from research_harness import review_staged_protocol
        for cause in ('evidence_changed', 'successor_epoch'):
            with self.subTest(cause=cause):
                fixture, controller = self.prepare_staged()
                case = fixture.case
                if cause == 'evidence_changed':
                    case.expected['value']['motivation'][0]['text'] = 'New scientific evidence.'
                else:
                    successor = case.assign('result', 'first', identifier='successor',
                        staged_upgrade={'assignment_id': controller['id']})
                    # The stale controller is still a real retained state even
                    # though normal navigation has already selected its successor.
                    old = protocol.assignment_state(case.read_records(), case.artifacts, controller['id'])
                    self.assertIn('review_epoch_stale', [row['code'] for row in old['obligations']])
                    plan = review_continuation.plan_next(case.read_records(), case.artifacts, controller['id'])
                    self.assertEqual(plan['head_id'], successor['id'])
                    self.assertNotEqual(plan.get('code'), 'review_epoch_stale')
                    with self.assertRaises(ResearchError) as raised:
                        self.execute_step(case, review_staged_protocol.describe_planning_command(controller['id']))
                    self.assertEqual(raised.exception.code, 'review_epoch_stale')
                    self.execute_plan(case, plan)
                    self.assertEqual(review_continuation.plan_next(case.read_records(), case.artifacts,
                        controller['id'])['state'], 'run')
                    continue
                plan = review_continuation.plan_next(case.read_records(), case.artifacts, controller['id'])
                self.assertEqual((plan['state'], plan['code']), ('stop', 'review_epoch_stale'))
                _, rows = self.run_series(case, controller['id'])
                self.assert_inspections(case, rows[-1])
                successor = case.assign('result', 'first', identifier='successor',
                    staged_upgrade={'assignment_id': controller['id']})
                upcoming = review_continuation.plan_next(case.read_records(), case.artifacts, controller['id'])
                self.assertEqual(upcoming['head_id'], successor['id'])
                self.assertNotEqual(upcoming.get('code'), 'review_epoch_stale')
                self.execute_plan(case, upcoming)
                self.assertEqual(review_continuation.plan_next(case.read_records(), case.artifacts,
                    controller['id'])['state'], 'run')

    def test_staged_completion_findings_remain_diagnostics_until_the_result_is_recorded(self):
        from research_harness import review_worker
        from test_review_staged_frontier import WORKER
        fixture = self.prepare_fixture(_StagedCase)
        case = fixture.case
        # Select one captured original in the fixture's scientific input. The
        # public assignment builds the complete manifest and every frozen task.
        case.expected['assessment_evidence'] = [dict(evidence=[case.supporting])]
        from codex_route_fixtures import limit_codex_input
        limit_codex_input(fixture, 8 * 1048576)
        controller = fixture.assign_staged('controller')
        from research_harness.review_budget import record_budget
        case.mutate(record_budget, dict(max_calls=12, reason='Bound the synthetic staged-review journey.'))
        output = case.build_review_output('result')
        output['value'].update(objective_adequacy='not_assessed', method_adequacy='not_assessed')
        (case.root / 'scientific.json').write_text(json.dumps(output))
        before = case.read_records()
        frozen = copy.deepcopy(before['review_assignment'])
        state = protocol.assignment_state(before, case.artifacts, controller['id'])
        finding = 'review_integration_pending'
        self.assertIn(finding, [row['code'] for row in state['completion']['issues']])
        self.assertEqual(stop_classes.STOP_REGISTRY.get('review_task_pending', {}).get('group'), 'named_cause')
        plan = review_continuation.plan_next(before, case.artifacts, controller['id'])
        self.assertIn(plan['state'], review_series.EXECUTABLE_STATES)
        self.assertIsNone(plan.get('code'))
        self.assertEqual(case.read_records(), before)
        worker = WORKER.replace('from test_review_staged_frontier import build_output',
                                'from test_series_rows_close_recovery import build_staged_output as build_output')
        with patch.object(review_worker, '_entry_command', return_value=[sys.executable, '-c', worker]):
            result, rows = self.run_series(case, controller['id'])
        refusals = case.read_records().get('review_task_refusal', {})
        self.assertEqual(result, 0, (rows[-1].get('code'), [
            refusal.get('message', refusal.get('failure', {}).get('message'))
            for refusal in refusals.values()]))
        self.assertEqual(rows[-1]['state'], 'done')
        self.assertEqual((case.root / 'calls').read_text().splitlines(),
                         ['frame', 'read', 'combine', 'integrate', 'final'])
        findings = {'review_assurance_dependency_pending', 'review_card_pending',
            'review_card_question_pending', 'review_claim_cell_pending', 'review_claim_unresolved',
            'review_combination_pending', 'review_finding_answer_pending', 'review_frame_unresolved',
            'review_integration_pending', 'review_integration_unresolved', 'review_objection_evidence_pending',
            'review_original_check_pending', 'review_retrieval_pending', 'review_source_coverage_pending',
            'review_terminal_pending'}
        self.assertTrue(all(row.get('code') not in findings for row in rows))
        self.assertEqual([row['command'] for row in rows if row['state'] != 'done'][-1], 'value-review')
        state = protocol.assignment_state(case.read_records(), case.artifacts, controller['id'])
        self.assertTrue(state['completion']['complete'])
        self.assertNotIn(finding, [row['code'] for row in state['completion']['issues']])
        for key, assignment in frozen.items():
            self.assertEqual(case.read_records()['review_assignment'][key], assignment)
        self.assertEqual(state['output'], output)

    def test_actual_incomplete_outcome_prints_an_inspection_instead_of_an_invalid_assignment(self):
        fixture, controller = self.prepare_staged()
        case = fixture.case
        # Exercise the actual clock-based epoch exhaustion through its public
        # planning command, without substituting the incomplete-state check.
        with patch('time.time', return_value=controller['staged_started_at'] + 86401):
            self.execute_step(case, controller['next'][0])
        result, rows = self.run_series(case, controller['id'])
        self.assertEqual(result, 3)
        self.assertEqual((rows[-1]['state'], rows[-1]['code']), ('stop', 'assessment_incomplete'))
        records = case.read_records()
        self.assertTrue(records['assessment_incomplete'])
        plan = review_continuation.plan_next(records, case.artifacts, controller['id'])
        self.assertEqual(plan['state'], 'stop')
        self.assertNotIn('command', plan)
        decision = rows[-1]['details']['decision']
        self.assertEqual(decision['owner_decides'], 'How should the incomplete assessment be answered?')
        for answer in decision['then']:
            for step in answer['steps']:
                self.assertIn(step['command'], ('record-check', 'status'))
                self.assertNotIn('assignment_id', step.get('payload', {}))
                self.assertEqual(step.get('chooses', []), [])
                if step['command'] == 'record-check':
                    from research_harness.record_check import check_record
                    # The record check lists the path of the Codex program beside each runtime read. The test
                    # states that the machine has none.
                    use_absent_programs(self)
                    self.assertIn('series_stops', check_record(case.store.root))
                else:
                    self.assertIn('obligations', cli.status_report(case.store))

    def test_a_recoverable_output_cause_does_not_admit_the_whole_mixed_code(self):
        from research_harness import research_decisions
        fixture = self.prepare_fixture()
        output = fixture.case.build_review_output('bar')
        output['value']['evidence'] = [fixture.case.artifacts.put(b'An undelivered original.', 'text/plain')]
        fixture.invoke(output)
        # Repeat the retained-output check at the driver's read boundary, as
        # the study group tests do. The refusing check itself remains real.
        def read_refused_output(store, head):
            snapshot = store.snapshot()
            assignment = snapshot['records']['review_assignment'][head]
            research_decisions.validate_review_contract(snapshot['records'], fixture.case.artifacts,
                                                        assignment, output)
            return snapshot, review_continuation.plan_next(snapshot['records'], fixture.case.artifacts, head)
        with patch.object(review_series, '_read_plan', side_effect=read_refused_output):
            result, rows = self.run_series(fixture.case, fixture.assignment['id'])
        self.assertEqual(result, 3)
        self.assertEqual(rows[-1]['code'], 'review_evidence_undelivered')
        self.assert_inspections(fixture.case, rows[-1])
        valid = fixture.case.build_provider_response(fixture.case.build_review_output('bar'))
        with patch.object(review_transport, 'send_request', return_value=valid):
            result, rows = self.run_series(fixture.case, fixture.assignment['id'])
        self.assertEqual(result, 0)
        records = fixture.case.read_records()
        self.assertEqual(next(iter(records['review_output_refusal'].values()))['code'],
                         'review_evidence_undelivered')
        self.assertEqual(rows[-1]['state'], 'done')
        # This proves the ordinary bar cause only. Reassessment, adjudication,
        # study and verification finals retain separate delivery preconditions.
        self.assertEqual(stop_classes.STOP_REGISTRY['review_evidence_undelivered']['group'], 'named_cause')
        sites = json.loads(GROUPS.read_text())['named_cause']['members']['review_evidence_undelivered']
        # The sixth site is the evidence check of a page answer. It raises under the same contract check
        # (review.evidence_delivery), so the answer is refused by that check and not admitted by the shared code.
        self.assertEqual(len(sites), 6)
        self.assertTrue(any('validate_reassessment#' in site for site in sites))
        self.assertIn('research_harness/review_composition.py:_validate_packet_evidence#1', sites)

    def test_inventory_overflow_keeps_its_two_inspections_from_the_actual_parts_state(self):
        fixture = self.prepare_parts()
        first = fixture.assign('parts-head')
        fixture.invoke(first)
        with patch.object(review_transport, 'CODEX_INPUT_LIMIT_CHARS', 20000):
            result, rows = self.run_series(fixture.case, first['id'])
        self.assertEqual(result, 3)
        self.assertEqual(rows[-1]['code'], 'review_parts_inventory_too_large')
        self.assertEqual(stop_classes.STOP_REGISTRY[rows[-1]['code']]['group'], 'named_cause')
        self.assert_inspections(fixture.case, rows[-1])

    def test_staged_obligation_with_its_own_row_keeps_the_planners_handoff(self):
        fixture, controller = self.prepare_staged()
        case = fixture.case
        case.mutate(protocol.record_context_event, dict(id='exposure', assignment_id=controller['id'],
            kind='contamination', source='foreign_input', reason='The review received foreign input.',
            evidence=[case.supporting]))
        records = case.read_records()
        state = protocol.assignment_state(records, case.artifacts, controller['id'])
        self.assertIn('review_context_contaminated', [row['code'] for row in state['obligations']])
        self.assertEqual(stop_classes.STOP_REGISTRY.get('review_task_pending', {}).get('group'), 'named_cause')
        before = review_continuation._plan_head(records, case.artifacts, controller)
        plan = review_continuation.plan_next(records, case.artifacts, controller['id'])
        self.assertEqual((plan['state'], plan['code'], plan['command']),
                         (before['state'], before['code'], before['command']))
        self.assertEqual((plan['state'], plan['code'], plan['command']),
                         ('handoff', 'review_context_contaminated', 'review-assignment'))
        self.assertEqual(plan['details']['step']['payload']['context']['context_repair']['event_ids'], ['exposure'])
        _, rows = self.run_series(case, controller['id'])
        self.assertEqual((rows[-1]['state'], rows[-1]['code'], rows[-1]['command']),
                         ('handoff', 'review_context_contaminated', 'review-assignment'))
