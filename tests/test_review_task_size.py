"""D1 task levels and D2 recovery use retained capacity events at the same effort."""

import copy
import json
import re
import unittest
from unittest.mock import patch

from research_harness import openness_reading as reading, review_excerpts as excerpts
from research_harness import review_failure_class as failures, review_parts as parts
from research_harness import review_protocol as protocol, review_route_capacity as capacity
from research_harness.evidence import digest
from test_review_capacity_plan import build_inputs
from test_review_output_profile import build_row
from test_review_route_capacity import build_route
import test_review_failure_class as failure_fixtures
from review_parts_fixtures import PartsResultCase


def build_max_plan():
    route = build_route()
    profile = capacity.read_output_profile({}, route, priors=[
        build_row(level=None, count=8, over=1)])
    return capacity.plan_chain_capacity(route, profile, **build_inputs(targets=1))


class TaskAllowanceTests(unittest.TestCase):
    def test_all_pinned_reading_levels_step_down_without_lowering_effort(self):
        plan = build_max_plan()
        observations = []
        for material, expected in ((120000, 60000), (60000, 30000), (30000, 15000), (15000, 15000)):
            observations.append(dict(material_allowance=material, output_tokens=None, outcome='output_over_cap'))
            self.assertEqual(capacity.derive_plan_task_limits(plan, 'reading', observations)['material_allowance'], expected)
            self.assertEqual(plan['route_class']['effort'], 'max')

    def test_completed_thresholds_ignore_failed_small_outputs_and_preserve_the_plan(self):
        plan = build_max_plan()
        before = copy.deepcopy(plan)
        for tokens, expected in ((96000, 30000), (95999, 60000), (44800, 120000), (44801, 60000)):
            observations = [dict(material_allowance=60000, output_tokens=tokens, outcome=None),
                            dict(material_allowance=60000, output_tokens=0, outcome='interrupted')]
            result = capacity.derive_plan_task_limits(plan, 'reading', observations)
            self.assertEqual(result['material_allowance'], expected)
        self.assertEqual(plan, before)

    def test_capacity_events_step_down_even_without_usage_and_notices_use_the_level(self):
        plan = build_max_plan()
        for outcome in ('output_over_cap', 'input_over_limit', 'timeout', 'provider_refused'):
            with self.subTest(outcome=outcome):
                result = capacity.derive_plan_task_limits(plan, 'reading', [
                    dict(material_allowance=120000, output_tokens=None, outcome=outcome)],
                    notice_characters=12000)
                self.assertEqual(result['level_material_allowance'], 60000)
                self.assertEqual(result['material_allowance'], 48000)
                self.assertEqual(result['notice_characters'], 12000)
                self.assertLessEqual(result['answer_bound'], result['default_answer_bound'])
        floor = capacity.derive_plan_task_limits(plan, 'reading', [
            dict(material_allowance=15000, output_tokens=138371, outcome='output_over_cap')],
            notice_characters=45000)
        self.assertEqual(floor['material_allowance'], 15000)

    def test_retained_chain_attempts_choose_the_next_allowance_without_new_profile_rows(self):
        case = failure_fixtures.FailureClassTests()
        self.addCleanup(case.doCleanups)
        case.setUp()
        plan = build_max_plan()
        case.assignment.update(task=dict(kind='reading', material_characters=120000),
            capacity_plan=dict(protocol=capacity.CAPACITY_PLAN_PROTOCOL, artifact=case.put(plan), digest=digest(plan)))
        case.records['review_route']['route'] = build_route()
        case.attempt(tokens=138371)
        # Source: 20261112043216-bar-turn-over-output-cap-at-effort-max.
        before = copy.deepcopy(case.records)
        with patch.object(capacity, 'read_output_profile', side_effect=AssertionError('Use the pinned profile')):
            result = capacity.derive_turn_task_limits(case.records, case.artifacts, case.assignment)
        self.assertEqual(result['material_allowance'], 60000)
        self.assertEqual(case.records, before)
        self.assertEqual(case.records['review_route']['route']['configuration']['effort'], 'max')

    def test_capacity_plan_kind_selects_allowance_without_new_task_metadata(self):
        case = failure_fixtures.FailureClassTests()
        self.addCleanup(case.doCleanups)
        case.setUp()
        plan = build_max_plan()
        case.assignment.update(capacity_plan_kind='reading',
            capacity_plan=dict(protocol=capacity.CAPACITY_PLAN_PROTOCOL, artifact=case.put(plan), digest=digest(plan)))
        self.assertEqual(capacity.derive_turn_task_limits(case.records, case.artifacts,
                                                        case.assignment)['material_allowance'], 120000)


class RecoveryRowsTests(unittest.TestCase):
    def prepare(self, cls):
        case = cls()
        self.addCleanup(case.doCleanups)
        case.setUp()
        return case

    def invoke_event(self, case, assignment, answer, *, timeout=False, number=0, tokens=1):
        from codex_route_fixtures import build_codex_response, bind_codex_response
        def respond(route, request, credential):
            response = build_codex_response(route, request, {})
            response['output'][0]['content'][0]['text'] = answer
            response['usage']['output_tokens'] = tokens
            if timeout:
                response['status'] = 'failed'
                response['execution'] = dict(timeout=True)
            return bind_codex_response(route, request, response)
        with patch.object(protocol.transport, 'send_request', side_effect=respond):
            return case.case.mutate(protocol.invoke_assignment,
                dict(id='capacity-' + str(number), assignment_id=assignment['id']), credential='fixture')

    def test_k2_every_installed_kind_protocol_pair_has_an_explicit_row(self):
        from research_harness import review_problem_parts, review_staged, review_output_correction
        from research_harness import review_transport, bar_sources
        expected = set()
        versions = {value for value in vars(reading).values() if isinstance(value, str)
                    and re.fullmatch(r'exactory-openness-reading-(?:v[0-9]+|math-v[0-9]+)', value)}
        for version in versions:
            expected.update((kind, version) for kind in ('reading', 'final'))
        expected.update((kind, parts.PROTOCOL) for kind in parts.KINDS)
        expected.update((kind, review_problem_parts.PROTOCOL) for kind in ('read', 'request'))
        expected.update(('request', value) for value in
                        (excerpts.PROTOCOL_V1, excerpts.PROTOCOL, excerpts.SESSION_PROTOCOL))
        versions = {value for value in vars(protocol).values() if isinstance(value, str)
                    and re.fullmatch(r'exactory-(?:independent-review|verification-review|problem-review)-v[0-9]+', value)}
        expected.update(('single', value) for value in versions | {bar_sources.PROTOCOL})
        expected.update((('staged', review_staged.PROTOCOL), ('correction', review_output_correction.PROTOCOL)))
        expected.update(('probe', adapter) for adapter in tuple(review_transport.LOCAL_ADAPTERS) + (review_transport.ADAPTER,))
        rows = capacity.list_capacity_recovery_rows()
        actual = {(row['kind'], row['protocol']) for row in rows}
        self.assertEqual(actual, expected)
        self.assertEqual(len(actual), len(rows))
        self.assertEqual({kind for kind, _ in actual}, set(capacity.TURN_KINDS))
        for row in rows:
            self.assertIn(row['handler'], ('nothing', 'parts_v2_read', 'context_repair',
                                          'single_request_series', 'staged_controller', 'reading_v7', 'final_v7'))
        self.assertIs(failures.plan_capacity_recovery, capacity.plan_capacity_recovery)

    def test_legacy_reading_and_final_decisions_include_profile_attempts_and_migration(self):
        case = self.prepare(failure_fixtures.FailureClassTests)
        case.records['review_route']['route'] = build_route()
        for version in (reading.PROTOCOL_V5, reading.PROTOCOL_V6, reading.MATH_PROTOCOL):
            for kind in ('reading', 'final'):
                with self.subTest(version=version, kind=kind):
                    case.records['review_attempt'].clear()
                    key = reading.TURN_KEY if kind == 'reading' else reading.RECORD_KEY
                    case.assignment['packet'] = case.put({key: dict(protocol=version)})
                    case.assignment['task'] = dict(kind=kind, material_characters=120000)
                    case.attempt(tokens=138371)
                    before = copy.deepcopy(case.records)
                    recovery = capacity.plan_capacity_recovery(case.records, case.artifacts,
                                                               case.assignment, 'output_over_cap')
                    self.assertEqual(recovery['kind'], 'decision')
                    self.assertEqual(recovery['details']['handler'], 'nothing')
                    self.assertIn('at_or_over_limit_share', recovery['details'])
                    self.assertEqual(recovery['details']['attempts'][0]['output_tokens'], 138371)
                    self.assertEqual(recovery['details']['effort'], 'max')
                    self.assertTrue(recovery['details']['effort_message'])
                    self.assertEqual(recovery['details']['required_unit'],
                                     'reading-math-v2' if version == reading.MATH_PROTOCOL else 'reading-v7-turns')
                    self.assertEqual(case.records, before)

    def test_a19_reduced_context_cap_replaces_a_read_and_real_assignment_halves_it(self):
        case = self.prepare(PartsResultCase)
        case.oversized(count=12, characters=10000, room=80000)
        first = case.assign()
        before = protocol.build_assignment_request(case.case.read_records(), case.case.artifacts, first)
        old = case.packet(first)[parts.KEY]['unit_characters']
        self.invoke_event(case, first, "API Error: Claude's response exceeded the 200 output token maximum.")
        plan = failures.plan_after_failure(case.case.read_records(), case.case.artifacts, first['id'])
        self.assertEqual(plan['details']['signal']['detail'], 'reduced_by_context')
        self.assertEqual(plan['state'], 'replace')
        accepted = case.case.mutate(protocol.record_assignment, plan['details']['recovery']['payload'])['result']
        self.assertLessEqual(case.packet(accepted)[parts.KEY]['unit_characters'], old // 2)
        self.assertEqual(accepted['route_id'], first['route_id'])
        self.assertEqual(protocol.build_assignment_request(case.case.read_records(), case.case.artifacts, first), before)

    def test_a21_timeout_and_provider_refusal_use_the_lower_read_after_their_retries(self):
        for outcome, count in (('timeout', 2), ('provider_refused', 3)):
            with self.subTest(outcome=outcome):
                case = self.prepare(PartsResultCase)
                case.oversized(count=12, characters=10000, room=80000)
                first = case.assign()
                old = case.packet(first)[parts.KEY]['unit_characters']
                for number in range(count):
                    if outcome == 'timeout':
                        self.invoke_event(case, first, '', timeout=True, number=number)
                    else:
                        self.invoke_event(case, first, 'API Error: safeguards flagged this message', number=number)
                    plan = failures.plan_after_failure(case.case.read_records(), case.case.artifacts, first['id'])
                    self.assertEqual(plan['state'], 'replace' if number == count - 1 else 'run')
                replacement = case.case.mutate(protocol.record_assignment, plan['details']['recovery']['payload'])['result']
                self.assertLessEqual(case.packet(replacement)[parts.KEY]['unit_characters'], old // 2)
                self.assertEqual(replacement['route_id'], first['route_id'])

    def test_input_over_limit_replaces_a_read_on_the_same_route(self):
        case = self.prepare(PartsResultCase)
        case.oversized(count=12, characters=10000, room=80000)
        assignment = case.assign()
        original = case.packet(assignment)[parts.KEY]['unit_characters']
        self.invoke_event(case, assignment, 'Prompt is too long')
        result = failures.plan_after_failure(case.case.read_records(), case.case.artifacts, assignment['id'])
        self.assertEqual((result['state'], result['details']['outcome']), ('replace', 'input_over_limit'))
        replacement = case.case.mutate(protocol.record_assignment, result['details']['recovery']['payload'])['result']
        self.assertLessEqual(case.packet(replacement)[parts.KEY]['unit_characters'], original // 2)
        self.assertEqual(replacement['route_id'], assignment['route_id'])
        records = case.case.read_records()
        self.assertEqual(replacement['capacity_replacement_of'], assignment['id'])
        self.assertEqual(protocol.context_repair.slot_id(records, replacement),
                         protocol.context_repair.slot_id(records, assignment))

    def test_verification_input_replacement_preserves_its_slot_and_frozen_parent(self):
        from research_harness.errors import ResearchError
        from research_harness.review_continuation import plan_next
        from review_parts_fixtures import make_output
        from test_review_parts_study import VerificationPartsTests

        fixture = self.prepare(VerificationPartsTests)
        fixture.enlarge_verification()
        assignment = fixture.assign()
        case = fixture.case
        records = fixture.records()
        parent = copy.deepcopy(assignment)
        packet_bytes = case.artifacts.read(assignment['packet'])
        request = protocol.build_assignment_request(records, case.artifacts, assignment)
        premature = {key: assignment[key] for key in
                     ('route_id', 'dossier_id', 'role', 'reviewer_id', 'author_id')}
        premature.update(id='premature', context=dict(capacity_replacement=assignment['id']))
        before = case.store.snapshot()
        with self.assertRaises(ResearchError) as caught:
            case.mutate(protocol.record_assignment, premature)
        self.assertEqual(caught.exception.code, 'invalid_review_context')
        self.assertEqual(case.store.snapshot(), before)

        self.invoke_event(fixture, assignment, 'Prompt is too long')
        plan = plan_next(fixture.records(), case.artifacts, assignment['id'])
        self.assertEqual((plan['state'], plan['command']), ('replace', 'review-assignment'))
        self.assertEqual(plan['payload']['context'], dict(capacity_replacement=assignment['id']))
        following = case.mutate(protocol.record_assignment, plan['payload'])['result']
        records = fixture.records()
        self.assertEqual(following['capacity_replacement_of'], assignment['id'])
        self.assertEqual(protocol.context_repair.slot_id(records, following), assignment['id'])
        self.assertEqual(set(failures.list_turn_assignments(records, following)),
                         {assignment['id'], following['id']})
        self.assertEqual(following['context'], parent['context'])
        self.assertLessEqual(fixture.packet(following)[parts.KEY]['unit_characters'],
                             fixture.packet(assignment)[parts.KEY]['unit_characters'] // 2)
        self.assertNotIn('replaces_assignment_id', following[parts.KEY])
        self.assertEqual(records['review_assignment'][assignment['id']], parent)
        self.assertEqual(case.artifacts.read(assignment['packet']), packet_bytes)
        self.assertEqual(protocol.build_assignment_request(records, case.artifacts, assignment), request)
        selected = plan_next(records, case.artifacts, assignment['id'])
        self.assertEqual((selected['head_id'], selected['state'], selected['command']),
                         (following['id'], 'run', 'review-run'))
        fixture.invoke(following, make_output(fixture.packet(following)))
        self.assertTrue(protocol.assignment_state(fixture.records(), case.artifacts, following['id'])['ready'])

    def test_c_exhaustion_returns_a_decision_and_names_the_untried_level(self):
        case = self.prepare(PartsResultCase)
        case.oversized(count=12, characters=10000, room=80000)
        assignment = case.assign()
        for number in range(2):
            self.invoke_event(case, assignment, '{', number=number, tokens=138371)
            records = case.case.read_records()
            # No usable read row: c=2, even when smaller levels remain.
            with patch.object(capacity, 'read_output_profile', return_value=dict(rows=[])):
                plan = failures.plan_after_failure(records, case.case.artifacts, assignment['id'])
            if number == 0:
                self.assertEqual(plan['state'], 'replace')
                assignment = case.case.mutate(protocol.record_assignment, plan['details']['recovery']['payload'])['result']
        self.assertEqual(plan['state'], 'stop')
        self.assertEqual(plan['details']['recovery']['kind'], 'decision')
        self.assertEqual(plan['details']['over_cap_allowance'], 2)
        self.assertGreater(plan['details']['untried_material_allowance'], 0)
        self.assertTrue(plan['details']['decision']['then'])

    def test_floor_has_one_identical_retry_and_then_full_decision_evidence(self):
        case = self.prepare(failure_fixtures.FailureClassTests)
        case.records['review_route']['route'] = build_route()
        plan = build_max_plan()
        case.assignment.update(task=dict(kind='reading', material_characters=15000),
            capacity_plan=dict(protocol=capacity.CAPACITY_PLAN_PROTOCOL, artifact=case.put(plan), digest=digest(plan)),
            packet=case.put({reading.TURN_KEY: dict(protocol=reading.PROTOCOL_V6)}))
        for expected in ('run', 'stop'):
            case.attempt(tokens=138371)
            result = case.plan()
            self.assertEqual(result['state'], expected)
        evidence = result['details']['recovery']
        self.assertEqual(evidence['kind'], 'decision')
        self.assertEqual(evidence['details']['at_or_over_limit_share'], 0.125)
        self.assertEqual([row['level'] for row in evidence['details']['attempts']], [3, 3])
        self.assertEqual([row['output_tokens'] for row in evidence['details']['attempts']], [138371, 138371])
        self.assertEqual(evidence['details']['effort'], 'max')
        self.assertIn('review-route', [step['command'] for step in evidence['then'][0]['steps']])

    def test_timeout_and_safeguard_floors_keep_their_outcome_codes(self):
        case = self.prepare(failure_fixtures.FailureClassTests)
        for outcome, code in (('timeout', 'review_timeout'), ('provider_refused', 'review_provider_refused')):
            with self.subTest(outcome=outcome), patch.object(failures, 'plan_output_replacement', return_value=dict(
                    state='stop', code='review_parts_budget_exhausted', reason='floor')):
                recovery = capacity.plan_capacity_recovery(case.records, case.artifacts, case.assignment, outcome)
                self.assertEqual((recovery['kind'], recovery['code']), ('decision', code))

    def test_c_uses_profile_share_and_counts_distinct_levels_after_duplicate_attempts(self):
        case = self.prepare(PartsResultCase)
        case.oversized(count=12, characters=10000, room=80000)
        first = case.assign()
        for number in range(2):
            self.invoke_event(case, first, '{', number=number, tokens=138371)
        profile = dict(rows=[build_row(kind='read', count=20, over=1)])
        with patch.object(capacity, 'read_output_profile', return_value=profile):
            plan = failures.plan_after_failure(case.case.read_records(), case.case.artifacts, first['id'])
        self.assertEqual(plan['state'], 'replace')
        assignment = case.case.mutate(protocol.record_assignment, plan['details']['recovery']['payload'])['result']
        self.invoke_event(case, assignment, '{', number=2, tokens=138371)
        with patch.object(capacity, 'read_output_profile', return_value=profile):
            plan = failures.plan_after_failure(case.case.read_records(), case.case.artifacts, assignment['id'])
        self.assertEqual(plan['state'], 'replace')
        self.assertEqual(plan['details']['at_or_over_limit_share'], 0.05)

    def test_legacy_problem_parts_final_has_an_explicit_no_smaller_task_decision(self):
        from problem_parts_fixtures import ResultPartsCase
        case = self.prepare(ResultPartsCase)
        case.oversized(count=12, characters=10000, limit=80000)
        _, assignment = case.finish_parts()
        packet = case.packet(assignment)
        self.assertEqual(packet['problem_evidence_parts']['stage'], 'final')
        before = case.request(assignment)
        recovery = capacity.plan_capacity_recovery(case.case.read_records(), case.case.artifacts,
                                                   assignment, 'output_over_cap')
        self.assertEqual((recovery['kind'], recovery['details']['handler']), ('decision', 'nothing'))
        self.assertEqual(recovery['details']['kind'], capacity.describe_task_kind(assignment['role'], packet))
        self.assertEqual(case.request(assignment), before)

    def test_legacy_native_size_stop_keeps_migration_prerequisite_in_final_plan(self):
        from openness_reading_fixtures import OpennessReadingCase
        from codex_route_fixtures import build_codex_response, bind_codex_response
        from research_harness.review_continuation import plan_next
        case = self.prepare(OpennessReadingCase)
        case.limit_input(24000)
        reference = case.export(assignment_id='reading')
        case.assign('reading', reference)
        def respond(route, request, credential):
            response = build_codex_response(route, request, {})
            response['output'][0]['content'][0]['text'] = "API Error: Claude's response exceeded the 200 output token maximum."
            return bind_codex_response(route, request, response)
        with patch.object(protocol.transport, 'send_request', side_effect=respond):
            for number in range(2):
                case.mutate(protocol.invoke_assignment, dict(id='failed-' + str(number), assignment_id='reading'))
        result = plan_next(case.store.snapshot()['records'], case.artifacts, 'reading')
        self.assertEqual((result['state'], result['code']), ('stop', 'review_output_over_cap'))
        steps = result['details']['decision']['then'][0]['steps']
        self.assertEqual([step['command'] for step in steps], ['review-route', 'export'])
        self.assertIn('reading-v7-turns', json.dumps(steps[-1]['prerequisites']))
        self.assertEqual(result['details']['decision']['then'], result['details']['recovery']['then'])

    def test_math_timeout_decision_names_route_and_new_attestation(self):
        from test_review_single_request import FrozenSingleTests
        case = self.prepare(FrozenSingleTests)
        recovery = capacity.plan_capacity_recovery(case.case.store.snapshot()['records'],
                                                   case.case.artifacts, case.first, 'timeout')
        self.assertEqual(recovery['kind'], 'decision')
        self.assertEqual([step['command'] for step in recovery['then'][0]['steps']],
                         ['review-route', 'search review-packet'])


if __name__ == '__main__':
    unittest.main()
