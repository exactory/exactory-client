"""D2 A7 and K7: refused v7 turns and executable successor admissions."""

import copy
import json
import unittest
from unittest.mock import patch

from openness_reading_fixtures import OpennessReadingCase
import test_openness_reading_v7_turns as turn_fixtures
from research_harness import openness_reading as reading, openness_reading_v7 as v7
from research_harness import review_protocol as protocol, review_route_capacity as capacity
from research_harness import review_refusal_notice as notices
from research_harness.review_continuation import plan_next
from research_harness.errors import ResearchError
from research_harness.evidence import digest


class V7RefusalTests(OpennessReadingCase):
    capacity_probe_tokens = 487900
    reading_protocol = None
    route_model = 'gpt-6-astra'
    route_output_tokens = 128000
    text_sizes = (50000, 50000, 50000)
    build_v7_answer = turn_fixtures.V7CommandTests.build_v7_answer
    build_v7_finding = turn_fixtures.V7CommandTests.build_v7_finding
    finish_reading = turn_fixtures.V7CommandTests.finish_reading

    def setUp(self):
        super().setUp()
        enabled = patch.object(reading, 'NEW_PROBLEM_CHAIN_PROTOCOLS', (v7.PROTOCOL, reading.PROTOCOL_V6))
        enabled.start()
        self.addCleanup(enabled.stop)

    def replace_head(self, head):
        records = self.store.snapshot()['records']
        plan = plan_next(records, self.artifacts, head)
        self.assertEqual((plan['state'], plan.get('command')), ('replace', 'review-assignment'), plan)
        return self.mutate(protocol.record_assignment, plan['payload'])['result']

    def finish_from(self, native, head):
        for number in range(100):
            packet = self.read_packet(head)
            if reading.find_stage(packet) == 'final':
                answer = (self.build_v7_finding(packet, packet['reading_final']['notes_handle'])
                          if 'reading_final' in packet else self.build_native_result(packet, outcome='unresolved'))
                self.invoke(head, answer)
                records = self.store.snapshot()['records']
                assignment = records['review_assignment'][head]
                attempt = records['review_attempt']['attempt-' + head]
                self.assertTrue(protocol.authenticate_attempt(records, self.artifacts, assignment, attempt))
                turns = reading.verify_chain(records, self.artifacts, assignment)
                result = self.mutate(self.load_problem_module().record_openness, dict(assignment_id=head))['result']
                self.assertEqual(result['outcome'], 'unresolved')
                return turns
            answer = (self.build_v7_answer(packet) if reading.find_protocol(packet) == v7.PROTOCOL
                      else self.build_reading_response(packet, excerpts=[]))
            self.invoke(head, answer)
            following = head + '-next'
            assignment = self.store.snapshot()['records']['review_assignment'][head]
            self.mutate(protocol.record_assignment, dict(id=following, route_id=assignment['route_id'],
                dossier_id=None, role='native_math', reviewer_id=assignment['reviewer_id'],
                author_id=assignment['author_id'], context=dict(native_packet=native, prior_assignment_id=head)))
            head = following
        self.fail('The replacement chain must reach its recorded finding.')

    def check_size_recovery(self, stage, outcome, detail=None):
        from codex_route_fixtures import build_codex_response
        from test_review_output_profile import build_row
        from research_harness import review_failure_class as failures
        from research_harness.check_mode import check_mutation
        with patch.object(capacity, 'read_profile_priors', return_value=[
                build_row(level=None, thinking=1000, count=8, over=1),
                build_row(kind='final', level=None, thinking=1000, count=8, over=1)]):
            native = self.export()
            if stage == 'final':
                head = self.finish_reading(native)
            else:
                self.assign('turn-1', native)
                self.invoke('turn-1', self.build_v7_answer(self.read_packet('turn-1')))
                head = self.assign('size-position', native, prior='turn-1')['id']
            before = copy.deepcopy(self.store.snapshot()['records'])
            verified = {key: v7.observe_v7_chain(before, self.artifacts, member)
                for key, member in before['review_assignment'].items()
                if any(row['assignment_id'] == key and row['status'] == 'completed'
                       for row in before.get('review_attempt', {}).values())}
            self.assertTrue(verified)
            first = before['review_assignment'][head]
            levels = []
            def respond(route, request, credential):
                if outcome == 'response_over_limit':
                    raise ResearchError('review_response_too_large', 'Synthetic retained response body limit.')
                if detail is None:
                    raise ResearchError('review_input_too_large', 'Synthetic retained input limit.')
                response = build_codex_response(route, request, {})
                response['status'] = 'incomplete'
                response['context_evidence'] = dict(issues=['uninspected_compaction'])
                return response
            for number in range(8):
                records = self.store.snapshot()['records']
                assignment = records['review_assignment'][head]
                packet = self.read_packet(head)
                member = 'reading_turn' if stage == 'reading' else 'reading_final'
                level = packet[member]['capacity']['material_allowance']
                if not levels or level != levels[-1]:
                    levels.append(level)
                with patch.object(protocol.transport, 'send_request', side_effect=respond):
                    attempt = self.mutate(protocol.invoke_assignment,
                        dict(id='size-%d' % number, assignment_id=head), credential='fixture')['result']
                records = self.store.snapshot()['records']
                classified = failures.classify_attempt(records, self.artifacts, attempt)
                self.assertEqual((classified['outcome'], classified['signal'].get('detail')), (outcome, detail))
                action = plan_next(records, self.artifacts, head)
                position = failures.count_position(records, self.artifacts, assignment)
                self.assertEqual(position['failed_attempts'], number + 1)
                self.assertEqual(position['sequence'], 1)
                if action['state'] == 'run':
                    continue
                if action['state'] == 'replace':
                    payload = action['payload']
                    check_mutation(self.store, protocol.record_assignment, payload,
                        expected_revision=self.store.revision, request_id='size-check-%d' % number,
                        bounded_payload=payload)
                    replacement = self.mutate(protocol.record_assignment, payload)['result']
                    self.assertEqual(replacement['reading_chain']['turn'], first['reading_chain']['turn'])
                    self.assertEqual(replacement['capacity_plan'], first['capacity_plan'])
                    self.assertEqual(replacement['route_digest'], first['route_digest'])
                    self.assertEqual(replacement['reviewer_id'], first['reviewer_id'])
                    self.assertLess(self.read_packet(replacement['id'])[member]['capacity']['material_allowance'], level)
                    head = replacement['id']
                    continue
                self.assertEqual(levels, [120000, 60000, 30000, 15000] if stage == 'reading' else [240000])
                self.assertEqual((action['state'], action['code']), ('stop',
                    'review_response_too_large' if outcome == 'response_over_limit' else 'review_input_too_large'))
                from research_harness.stop_classes import classify_stop
                self.assertEqual(classify_stop(action['code']), 'decision')
                recovery = action['details']['recovery']
                self.assertEqual(recovery['kind'], 'decision')
                self.assertEqual(action['details']['decision'],
                                 {key: recovery[key] for key in ('owner_decides', 'then')})
                self.assertTrue(action['details']['decision']['then'])
                self.assertNotIn('successor', action['details'])
                self.assertEqual(plan_next(records, self.artifacts, head), action)
                after = self.store.snapshot()['records']
                for kind in ('review_assignment', 'review_attempt', 'review_invocation'):
                    for key, row in before.get(kind, {}).items():
                        self.assertEqual(after[kind][key], row)
                for key, observed in verified.items():
                    self.assertEqual(v7.observe_v7_chain(records, self.artifacts,
                        records['review_assignment'][key]), observed)
                return
            self.fail('The size row must reach its floor decision.')

    def test_v7_input_floor_keeps_size_decision_and_verified_turns(self):
        for stage in ('reading', 'final'):
            for detail in (None, 'compacted'):
                with self.subTest(stage=stage, detail=detail):
                    fixture = V7RefusalTests()
                    try:
                        fixture.setUp()
                        fixture.check_size_recovery(stage, 'input_over_limit', detail)
                    finally:
                        fixture.doCleanups()

    def check_response_refusal_history(self, *, capacity_replacement):
        from contextlib import nullcontext
        from test_review_output_profile import build_row
        derive_limits = capacity.derive_plan_task_limits
        def derive_legacy_limits(plan, kind, observations=(), **kwargs):
            return derive_limits(plan, kind, [row for row in observations
                if row.get('outcome') != 'response_over_limit'], **kwargs)
        with patch.object(capacity, 'read_profile_priors', return_value=[
                build_row(level=None, thinking=1000, count=8)]):
            native = self.export()
            head = self.assign('turn-1', native)['id']
            with patch.object(protocol.transport, 'send_request', side_effect=ResearchError(
                    'review_response_too_large', 'Synthetic retained response body limit.')):
                self.mutate(protocol.invoke_assignment, dict(id='response-limit', assignment_id=head),
                            credential='fixture')
            if capacity_replacement:
                head = self.replace_head(head)['id']
            self.invoke(head, dict(stage='reading', notes=42))
            # Freeze the old identical-retry history using its original adaptation rule.
            legacy = (nullcontext() if capacity_replacement else
                      patch.object(capacity, 'derive_plan_task_limits', side_effect=derive_legacy_limits))
            with legacy:
                replacement = self.replace_head(head)
                packet = self.read_packet(replacement['id'])
                self.assertEqual(packet['reading_turn']['capacity']['level_material_allowance'],
                                 60000 if capacity_replacement else 120000)
                turns = self.finish_from(native, replacement['id'])
            records = self.store.snapshot()['records']
            before = copy.deepcopy(records)
            finding = next(iter(records['problem_openness'].values()))
            final = records['review_assignment'][finding['assignment_id']]
            self.assertEqual(reading.verify_chain(records, self.artifacts, final), turns)
            for assignment in records['review_assignment'].values():
                for attempt in records['review_attempt'].values():
                    if attempt['assignment_id'] == assignment['id'] and attempt['status'] == 'completed':
                        self.assertTrue(protocol.authenticate_attempt(records, self.artifacts, assignment, attempt))
            self.assertEqual(records, before)

    def test_a7_refused_old_contract_final_replaces_authenticates_and_records(self):
        native = self.export()
        build = self.build_v7_answer
        def retain_excerpt(packet):
            output = build(packet)
            for row, body in zip(packet['reading_turn']['plan']['now'], packet['evidence']):
                if row['kind'] != 'list_page' and body.get('content'):
                    output['excerpts'] = [dict(unit=row['handle'], text=body['content'][:50],
                        purpose='A retained scientific detail.', targets=['t1'])]
                    break
            return output
        with patch.object(self, 'build_v7_answer', side_effect=retain_excerpt):
            head = self.finish_reading(native)
        malformed = dict(schema_version=3, **{key: self.read_packet(head)['reading_final'][key]
            for key in ('subject_digest', 'claim_digest', 'reviewer')},
            decision='revise', objections=[], reason='An earlier answer contract.')
        response = self.invoke(head, malformed)
        self.assertEqual(response['output_refusal']['check'], 'openness.finding')
        before = copy.deepcopy(self.store.snapshot()['records'])
        replacement = self.replace_head(head)
        prior = before['review_assignment'][head]
        self.assertEqual(replacement['reading_chain'], prior['reading_chain'])
        self.assertEqual(replacement['reviewer_id'], prior['reviewer_id'])
        self.assertEqual(replacement['prompt']['template_id'], prior['prompt']['template_id'])
        self.assertIn('output_refusal', self.read_packet(replacement['id']))
        self.assertTrue(self.read_packet(head)['reading_final']['entries'])
        self.assertEqual(self.read_packet(replacement['id'])['reading_final']['entries'],
                         self.read_packet(head)['reading_final']['entries'])
        turns = self.finish_from(native, replacement['id'])
        self.assertEqual(len(turns), prior['reading_chain']['turn'])
        after = self.store.snapshot()['records']
        for kind in ('review_assignment', 'review_attempt', 'review_output_refusal'):
            for key, row in before.get(kind, {}).items():
                self.assertEqual(digest(after[kind][key]), digest(row))

    def test_a7_refused_reading_uses_notice_room_and_leaves_rest_for_later_turns(self):
        native = self.export()
        self.assign('turn-1', native)
        self.invoke('turn-1', dict(stage='reading', notes=42))
        replacement = self.replace_head('turn-1')
        packet = self.read_packet(replacement['id'])
        prior = self.store.snapshot()['records']['review_assignment']['turn-1']
        self.assertEqual(replacement['reading_chain'], prior['reading_chain'])
        notice_size = len(json.dumps(packet['output_refusal'], ensure_ascii=False, sort_keys=True, allow_nan=False))
        limits = capacity.derive_turn_task_limits(self.store.snapshot()['records'], self.artifacts, prior,
                                                 notice_characters=notice_size)
        self.assertEqual(packet['reading_turn']['capacity']['material_allowance'],
            max(15000, self.read_packet('turn-1')['reading_turn']['capacity']['material_allowance'] - notice_size))
        self.assertEqual(packet['reading_turn']['capacity']['material_allowance'], limits['material_allowance'])
        delivered = capacity.measure_capacity_value(self.store.snapshot()['records']['review_route']['route'], packet['evidence'])
        delivered += capacity.measure_capacity_value(self.store.snapshot()['records']['review_route']['route'], packet['reading_turn']['plan']['now'])
        self.assertLessEqual(delivered, limits['material_allowance'])
        self.assertTrue(self.finish_from(native, replacement['id']))
        attempt = self.store.snapshot()['records']['review_attempt']['attempt-' + replacement['id']]
        self.assertEqual(attempt['task']['notice_characters'], notice_size)
        self.assertEqual(attempt['task']['level_material_allowance'],
                         self.read_packet('turn-1')['reading_turn']['capacity']['material_allowance'])

    def test_k7_dense_failed_final_carries_ratio_through_check_assignment_and_finding(self):
        from test_review_output_profile import build_row
        from research_harness.check_mode import check_mutation
        from codex_route_fixtures import build_codex_response, bind_codex_response
        from test_research_problem_openness_minimum import ProblemOpennessMinimumTests
        self.selected = self.select_statement(identifier='ratio-targets', candidate_id='candidate-1',
                                               neighbours=['Nearby one.', 'Nearby two.'])
        searches = [dict(formulation=self.selected['statement'], search_id=self.search['id'])]
        for formulation in self.selected['neighbours']:
            row = ProblemOpennessMinimumTests.search(self, self.versions, formulation=formulation)
            searches.append(dict(formulation=formulation, search_id=row['id']))
        from research_harness.problem_openness import export_openness
        def export(identifier):
            return export_openness(self.store, dict(assignment_id=identifier, reviewer_id='openness-reviewer',
                minimum=dict(searches=searches, settling_work_id=None, duplicate_resolutions=[])),
                self.root / (identifier + '-packet'))['native_packet']
        priors = [build_row(level=None, thinking=1000, count=8),
                  build_row(kind='final', level=None, thinking=1000, count=8)]
        with patch.object(capacity, 'read_profile_priors', return_value=priors):
            native = export('turn-1')
            head = self.finish_reading(native)
            def respond(route, request, credential):
                response = build_codex_response(route, request, {})
                response['status'] = 'incomplete'
                response['usage']['input_tokens'] = capacity.measure_request(route, request)['text_characters'] * 2
                return bind_codex_response(route, request, response)
            with patch.object(protocol.transport, 'send_request', side_effect=respond):
                self.mutate(protocol.invoke_assignment, dict(id='dense-final', assignment_id=head), credential='fixture')
            records = self.store.snapshot()['records']
            self.assertEqual(records['review_attempt']['dense-final']['usage']['sends'], 1)
            action = plan_next(records, self.artifacts, head)
            self.assertEqual((action['state'], action.get('command')), ('handoff', 'export'), action)
            successor = action['details']['successor']
            self.assertEqual(successor['protocol'], v7.PROTOCOL)
            self.assertTrue(successor['carried_readings']['chains'])
            reference = export('ratio-successor')
            payload = dict(id='ratio-successor', route_id='route', dossier_id=None, role='native_math',
                reviewer_id='openness-reviewer', author_id='problem-author', context=dict(native_packet=reference))
            checked = check_mutation(self.store, protocol.record_assignment, payload,
                expected_revision=self.store.revision, request_id='ratio-check', bounded_payload=payload)
            actual = self.mutate(protocol.record_assignment, payload)['result']
            self.assertEqual(checked['protocol_selection'], actual['protocol_selection'])
            stored_plan = capacity.read_chain_capacity_plan(self.artifacts, actual)
            self.assertEqual(stored_plan['measure'], successor['measure'])
            self.assertEqual(stored_plan['measure']['characters_per_token'], .47)
            self.assertEqual(stored_plan['measure']['source'], dict(chain='turn-1', attempt='dense-final'))
            self.finish_from(reference, 'ratio-successor')

    def test_k7_repeated_dense_ratios_end_after_two_successor_chains(self):
        from test_review_output_profile import build_row
        from research_harness.check_mode import check_mutation
        from codex_route_fixtures import build_codex_response, bind_codex_response
        from test_research_problem_openness_minimum import ProblemOpennessMinimumTests
        self.selected = self.select_statement(identifier='ratio-targets', candidate_id='candidate-1',
                                               neighbours=['Nearby one.', 'Nearby two.'])
        searches = [dict(formulation=self.selected['statement'], search_id=self.search['id'])]
        for formulation in self.selected['neighbours']:
            row = ProblemOpennessMinimumTests.search(self, self.versions, formulation=formulation)
            searches.append(dict(formulation=formulation, search_id=row['id']))
        from research_harness.problem_openness import export_openness
        def export(identifier):
            return export_openness(self.store, dict(assignment_id=identifier, reviewer_id='openness-reviewer',
                minimum=dict(searches=searches, settling_work_id=None, duplicate_resolutions=[])),
                self.root / (identifier + '-packet'))['native_packet']
        priors = [build_row(level=None, thinking=1000, count=8),
                  build_row(kind='final', level=None, thinking=1000, count=8)]
        with patch.object(capacity, 'read_profile_priors', return_value=priors):
            native = export('turn-1')
            head = self.finish_reading(native)
            successors = 0
            for factor in (2, 4):
                def respond(route, request, credential):
                    response = build_codex_response(route, request, {})
                    response['status'] = 'incomplete'
                    response['usage']['input_tokens'] = capacity.measure_request(route, request)['text_characters'] * factor
                    return bind_codex_response(route, request, response)
                with patch.object(protocol.transport, 'send_request', side_effect=respond):
                    self.mutate(protocol.invoke_assignment, dict(id='dense-%d' % factor, assignment_id=head), credential='fixture')
                action = plan_next(self.store.snapshot()['records'], self.artifacts, head)
                self.assertEqual((action['state'], action.get('command')), ('handoff', 'export'), action)
                successors += 1
                predicted = action['details']['successor']
                identifier = 'successor-%d' % successors
                native = export(identifier)
                receipt = self.assign(identifier, native)
                self.assertEqual(receipt['protocol_selection']['protocol'], predicted['protocol'])
                if predicted['protocol'] == v7.PROTOCOL:
                    self.assertEqual(capacity.read_chain_capacity_plan(self.artifacts, receipt)['measure'], predicted['measure'])
                if receipt['protocol_selection']['protocol'] == reading.PROTOCOL_V6:
                    current = identifier
                    for number in range(30):
                        packet = self.read_packet(current)
                        if reading.find_stage(packet) == 'final':
                            # The reviewer returns a valid final even as its measured ratio grows again.
                            output = self.build_native_result(packet, outcome='unresolved')
                            def finish(route, request, credential):
                                response = build_codex_response(route, request, output)
                                response['usage']['input_tokens'] = capacity.measure_request(route, request)['text_characters'] * 8
                                return bind_codex_response(route, request, response)
                            with patch.object(protocol.transport, 'send_request', side_effect=finish):
                                self.mutate(protocol.invoke_assignment, dict(id='dense-complete', assignment_id=current), credential='fixture')
                            finding = self.mutate(self.load_problem_module().record_openness, dict(assignment_id=current))['result']
                            self.assertEqual(finding['outcome'], 'unresolved')
                            self.assertEqual(successors, 2)
                            return
                        self.invoke(current, self.build_reading_response(packet, excerpts=[]))
                        following = identifier + '-%d' % number
                        self.assign(following, native, prior=current)
                        current = following
                    self.fail('The v6 fallback must record its finding.')
                head = identifier
                for number in range(30):
                    packet = self.read_packet(head)
                    if reading.find_stage(packet) == 'final':
                        break
                    self.invoke(head, self.build_v7_answer(packet))
                    following = identifier + '-%d' % number
                    self.assign(following, native, prior=head)
                    head = following
            self.fail('The repeated-ratio fixture must terminate after two successor chains.')

    def test_a7_series_executes_refusal_replacement_and_records_without_losing_forecast(self):
        from research_harness.review_series import run_series
        from codex_route_fixtures import build_codex_response
        native = self.export()
        self.assign('turn-1', native)
        rows = []
        def respond(route, request, credential):
            packet = reading.review_excerpts.read_sent_packet(request)
            if 'reading_turn' in packet:
                output = self.build_v7_answer(packet) if packet.get('output_refusal') or packet['reading_turn']['turn'] > 1 else dict(stage='reading', notes=42)
            else:
                output = self.build_v7_finding(packet, packet['reading_final']['notes_handle'])
            return build_codex_response(route, request, output)
        with patch.object(protocol.transport, 'send_request', side_effect=respond):
            self.assertEqual(run_series(self.store, 'turn-1', emit=rows.append), 0)
        self.assertEqual(rows[-1]['state'], 'done')
        records = self.store.snapshot()['records']
        replaced = {key for key, row in records['review_assignment'].items() if row['context'].get('output_refusal')}
        self.assertTrue(replaced)
        for row in rows:
            if row['head_id'] in replaced and 'capacity_forecast' in row:
                self.assertNotEqual(row['capacity_forecast'].get('status'), 'unavailable', row)
        self.assertTrue(records.get('problem_openness'))

    def test_a7_frozen_origin_tampering_is_refused(self):
        native = self.export()
        head = self.finish_reading(native)
        self.invoke(head, dict(decision='revise', objections=[], reason='Wrong contract.'))
        replacement = self.replace_head(head)
        packet = self.read_packet(replacement['id'])
        self.invoke(replacement['id'], self.build_v7_finding(packet, packet['reading_final']['notes_handle']))
        records = self.store.snapshot()['records']
        changed = copy.deepcopy(records)
        certificate = next(iter(changed['review_output_refusal'].values()))
        certificate['message'] = 'Altered frozen evidence.'
        with self.assertRaises(ResearchError) as error:
            reading.verify_chain(changed, self.artifacts, changed['review_assignment'][replacement['id']])
        self.assertEqual(error.exception.code, 'review_refusal_replacement_origin_invalid')

    def test_a7_refusal_replacement_capacity_failure_keeps_notice_and_records(self):
        from codex_route_fixtures import build_codex_response, bind_codex_response
        native = self.export()
        self.assign('turn-1', native)
        self.invoke('turn-1', dict(stage='reading', notes=42))
        replacement = self.replace_head('turn-1')
        notice = self.read_packet(replacement['id'])['output_refusal']
        def fail(route, request, credential):
            response = build_codex_response(route, request, {})
            response['status'] = 'incomplete'
            response['usage']['output_tokens'] = 128001
            return bind_codex_response(route, request, response)
        with patch.object(protocol.transport, 'send_request', side_effect=fail):
            self.mutate(protocol.invoke_assignment, dict(id='refusal-over-cap', assignment_id=replacement['id']), credential='fixture')
        reduced = self.replace_head(replacement['id'])
        self.assertEqual(self.read_packet(reduced['id'])['output_refusal'], notice)
        self.assertEqual(reduced['reading_chain']['turn'], replacement['reading_chain']['turn'])
        self.assertLess(self.read_packet(reduced['id'])['reading_turn']['capacity']['material_allowance'],
                        self.read_packet(replacement['id'])['reading_turn']['capacity']['material_allowance'])
        self.finish_from(native, reduced['id'])

    def test_a7_third_replacement_refused_and_export_carries_verified_readings(self):
        native = self.export()
        head = self.finish_reading(native)
        for number in range(3):
            self.invoke(head, dict(decision='revise', objections=[], reason='Wrong contract.'))
            if number < 2:
                head = self.replace_head(head)['id']
        records = self.store.snapshot()['records']
        assignment = records['review_assignment'][head]
        value = {key: copy.deepcopy(assignment[key]) for key in
                 ('role', 'route_id', 'dossier_id', 'reviewer_id', 'author_id', 'context')}
        value.update(id='forbidden-third')
        value['context']['output_refusal'] = dict(assignment_id=head)
        with self.assertRaises(ResearchError) as refused:
            self.mutate(protocol.record_assignment, value)
        self.assertEqual(refused.exception.code, 'review_slot_exhausted')
        plan = plan_next(records, self.artifacts, head)
        self.assertEqual((plan['state'], plan.get('command')), ('handoff', 'export'), plan)
        self.assertTrue(plan['details']['successor']['carried_readings']['chains'])
        following = self.export(name='successor-export', assignment_id='successor')
        receipt = self.assign('successor', following)
        selection = json.loads(self.artifacts.read(receipt['carried_selection']['artifact']))
        self.assertTrue(selection['turns'])
        self.finish_from(following, 'successor')


class LegacySuccessorTests(OpennessReadingCase):
    capacity_probe_tokens = 487900
    historical_v5 = True
    reading_protocol = None
    route_model = 'gpt-6-astra'
    route_output_tokens = 128000
    text_sizes = (70000, 70000, 70000, 70000)
    build_v7_answer = turn_fixtures.V7CommandTests.build_v7_answer
    build_v7_finding = turn_fixtures.V7CommandTests.build_v7_finding
    finish_from = V7RefusalTests.finish_from

    def setUp(self):
        super().setUp()
        self.limit_input(1300000)

    def export(self, priority=(), name='openness-packet', assignment_id='turn-1'):
        from research_harness.problem_openness import export_openness
        if self.historical_v5:
            from openness_legacy_fixtures import export_as_0_85_3
            export_openness = export_as_0_85_3
        minimum = dict(searches=[dict(formulation=row['question'], search_id=row['id']) for row in self.searches],
                       settling_work_id=None, duplicate_resolutions=[])
        return export_openness(self.store, dict(assignment_id=assignment_id,
            reviewer_id='openness-reviewer', minimum=minimum), self.root / name)['native_packet']

    def fail_legacy_final(self, fail=True, expected_protocol=reading.PROTOCOL_V5):
        native = self.export()
        self.assign('turn-1', native)
        current = 'turn-1'
        for number in range(30):
            packet = self.read_packet(current)
            if 'reading_final' in packet or reading.find_stage(packet) == 'final':
                break
            self.invoke(current, self.build_reading_response(packet, excerpts=[]))
            following = 'old-%d' % number
            self.assign(following, native, prior=current)
            current = following
        else:
            self.fail('The old chain must reach its final.')
        self.assertEqual(reading.find_protocol(self.read_packet(current)), expected_protocol)
        if fail:
            with patch.object(protocol.transport, 'send_request', side_effect=ResearchError(
                    'review_input_too_large', 'The provider refused the final input.')):
                self.mutate(protocol.invoke_assignment, dict(id='old-input-limit', assignment_id=current), credential='fixture')
        self.historical_v5 = False
        return current

    def select_targets(self, count, width=20):
        self.selected = self.select_statement(identifier='many-targets', candidate_id='candidate-1',
            neighbours=['Neighbour %d. ' % n + 'x' * width for n in range(count - 1)])
        from test_research_problem_openness_minimum import ProblemOpennessMinimumTests
        self.searches += [ProblemOpennessMinimumTests.search(self, self.versions, formulation=formulation)
                          for formulation in self.selected['neighbours']]

    def execute_export(self, plan, identifier='successor'):
        from research_harness.problem_openness import export_openness
        step = plan['details']['step']
        self.assertEqual(step['command'], 'export')
        self.assertTrue(step['prerequisites'])
        minimum = dict(searches=[dict(formulation=row['question'], search_id=row['id']) for row in self.searches],
                       settling_work_id=None, duplicate_resolutions=[])
        reference = export_openness(self.store, dict(assignment_id=identifier,
            reviewer_id='openness-reviewer', minimum=minimum), self.root / (identifier + '-packet'),
            accept_gaps=True)['native_packet']
        from research_harness.check_mode import check_mutation
        payload = dict(step['then'][0]['payload'], id=identifier, context=dict(native_packet=reference))
        checked = check_mutation(self.store, protocol.record_assignment, payload,
            expected_revision=self.store.revision, request_id=identifier + '-check', bounded_payload=payload)
        assigned = self.mutate(protocol.record_assignment, payload)['result']
        self.assertEqual(checked['protocol_selection'], assigned['protocol_selection'])
        return reference, assigned

    def test_k7_measured_uncalled_final_exports_before_any_provider_call(self):
        head = self.fail_legacy_final(fail=False)
        with patch.object(reading, 'NEW_PROBLEM_CHAIN_PROTOCOLS', (v7.PROTOCOL, reading.PROTOCOL_V6)), patch.object(
                protocol.transport, 'CODEX_INPUT_LIMIT_CHARS', 230000):
            records = self.store.snapshot()['records']
            self.assertFalse(any(row['assignment_id'] == head for row in records.get('review_attempt', {}).values()))
            action = plan_next(records, self.artifacts, head)
            self.assertEqual((action['state'], action.get('command')), ('handoff', 'export'), action)
            reference, _ = self.execute_export(action)
            self.finish_from(reference, 'successor')

    def test_k7_v5_input_limit_exports_to_v7_carries_and_records(self):
        head = self.fail_legacy_final()
        with patch.object(reading, 'NEW_PROBLEM_CHAIN_PROTOCOLS', (v7.PROTOCOL, reading.PROTOCOL_V6)):
            plan = plan_next(self.store.snapshot()['records'], self.artifacts, head)
            self.assertEqual((plan['state'], plan.get('command')), ('handoff', 'export'), plan)
            self.assertEqual(plan['details']['successor']['protocol'], v7.PROTOCOL)
            reference, receipt = self.execute_export(plan)
            self.assertEqual(receipt['protocol_selection']['protocol'], v7.PROTOCOL)
            self.assertTrue(receipt['carried_readings']['chains'])
            self.assertEqual(receipt['carried_readings'], plan['details']['successor']['carried_readings'])
            self.assertNotEqual(plan_next(self.store.snapshot()['records'], self.artifacts, 'successor')['state'], 'stop')
            self.finish_from(reference, 'successor')

    def test_k7_v6_input_limit_exports_to_v7_carries_and_records(self):
        self.historical_v5 = False
        with patch.object(reading, 'NEW_PROBLEM_CHAIN_PROTOCOLS', (reading.PROTOCOL_V6,)):
            head = self.fail_legacy_final(expected_protocol=reading.PROTOCOL_V6)
        with patch.object(reading, 'NEW_PROBLEM_CHAIN_PROTOCOLS', (v7.PROTOCOL, reading.PROTOCOL_V6)):
            action = plan_next(self.store.snapshot()['records'], self.artifacts, head)
            self.assertEqual((action['state'], action.get('command')), ('handoff', 'export'), action)
            self.assertEqual(action['details']['successor']['protocol'], v7.PROTOCOL)
            reference, receipt = self.execute_export(action)
            self.assertTrue(receipt['carried_readings']['chains'])
            self.finish_from(reference, 'successor')

    def test_k7_nine_targets_v7_refuses_v6_exports_reads_again_and_records(self):
        self.select_targets(9)
        with patch.object(reading, 'NEW_PROBLEM_CHAIN_PROTOCOLS', (v7.PROTOCOL, reading.PROTOCOL_V6)):
            self.check_v6_successor()

    def test_k7_no_successor_stops_with_two_answers_and_an_executable_route(self):
        from research_harness import review_claude_transport as claude
        from research_harness.check_mode import check_mutation
        from test_research_claude_transport import clean_stream, completed
        from v7_capacity_fixtures import build_route
        head = self.fail_legacy_final()
        records = self.store.snapshot()['records']
        old = copy.deepcopy(records)
        for protocols in ((v7.PROTOCOL, reading.PROTOCOL_V6), (reading.PROTOCOL_V6,)):
            with patch.object(reading, 'NEW_PROBLEM_CHAIN_PROTOCOLS', protocols), patch.object(
                    protocol.transport, 'CODEX_INPUT_LIMIT_CHARS', 60000):
                action = plan_next(records, self.artifacts, head)
                self.assertEqual(action['state'], 'stop', action)
                self.assertIsNone(action['details']['successor']['protocol'])
                self.assertEqual(len(action['details']['decision']['then']), 2)
                self.assertTrue(action['details']['unserved_range'])
                self.assertTrue(all(row['state'] == 'stop' for row in action['details']['successor']['admissions']))
        for kind in ('review_assignment', 'review_attempt'):
            self.assertEqual(records[kind], old[kind])
        # One target is already the smallest packet: the first answer reports its unserved range.
        # The second answer's route/probe prerequisites are executed before its export and check.
        route_step = action['details']['decision']['then'][1]['steps'][0]
        self.assertEqual(route_step['command'], 'review-route')
        export_step = action['details']['decision']['then'][1]['steps'][1]
        self.assertEqual(export_step['command'], 'export')
        route = dict(build_route(effort='xhigh'), id='serving-route')
        def probe(route, request, credential):
            supplied = reading.review_excerpts.read_sent_packet(request)
            output = dict(allowed_control=supplied['allowed_control'], evidence_control=supplied['evidence_control'],
                          excluded_controls=[])
            with patch.object(claude, '_run_owned', return_value=completed(clean_stream(request, output=json.dumps(output), model=route['model']))):
                return claude.send_request(route, request, credential)
        # Keep the old adapter's physical limit in force; the new adapter supplies its own serving capacity.
        with patch.object(reading, 'NEW_PROBLEM_CHAIN_PROTOCOLS', (v7.PROTOCOL, reading.PROTOCOL_V6)), patch.object(
                protocol.transport, 'CODEX_INPUT_LIMIT_CHARS', 60000), patch.object(
                claude, '_cli', return_value=('fixture-claude', 'fixture-version')), patch.object(
                protocol.transport, 'send_request', side_effect=probe):
            self.mutate(protocol.record_route, route)
            self.mutate(protocol.probe_route, dict(id='serving-probe', route_id='serving-route'), credential='fixture')
            reference = self.export(name='recovered-export', assignment_id='recovered')
            payload = dict(id='recovered', route_id='serving-route', dossier_id=None, role='native_math',
                reviewer_id='openness-reviewer', author_id='problem-author', context=dict(native_packet=reference))
            checked = check_mutation(self.store, protocol.record_assignment, payload,
                expected_revision=self.store.revision, request_id='recovered-check', bounded_payload=payload)
            receipt = self.mutate(protocol.record_assignment, payload)['result']
            self.assertEqual(checked['protocol_selection'], receipt['protocol_selection'])
            self.assertEqual(receipt['protocol_selection']['protocol'], v7.PROTOCOL)
            self.assertNotEqual(plan_next(self.store.snapshot()['records'], self.artifacts, 'recovered')['state'], 'stop')

    def test_math_solver_input_stop_keeps_its_answers_in_both_protocol_states(self):
        from research_harness.review_plan_steps import complete_plan
        head = self.fail_legacy_final()
        records = self.store.snapshot()['records']
        assignment = copy.deepcopy(records['review_assignment'][head])
        native = json.loads(self.artifacts.read(assignment['context']['native_packet']))
        native['contract'].pop('orchestration')
        assignment['context']['native_packet'] = protocol._put_json(self.artifacts, native)
        packet = copy.deepcopy(self.read_packet(head))
        packet['native'] = native
        packet[reading.RECORD_KEY]['protocol'] = reading.MATH_PROTOCOL
        assignment['packet'] = protocol._put_json(self.artifacts, packet)
        for protocols in ((v7.PROTOCOL, reading.PROTOCOL_V6), (reading.PROTOCOL_V6,)):
            with patch.object(reading, 'NEW_PROBLEM_CHAIN_PROTOCOLS', protocols):
                action = complete_plan(records, self.artifacts, assignment,
                    dict(state='stop', code=reading.PROJECTION_STOP, details={}))
                self.assertEqual(action['state'], 'stop')
                self.assertIn(reading.WAIT_MATH_ANSWER,
                              [answer['answer'] for answer in action['details']['decision']['then']])

    def test_k7_output_cap_decision_names_admitted_export(self):
        self.check_output_cap_export(False)

    def test_k7_reading_output_cap_decision_names_admitted_export(self):
        self.check_output_cap_export(True)

    def check_output_cap_export(self, reading_turn):
        from codex_route_fixtures import build_codex_response, bind_codex_response
        if reading_turn:
            native = self.export()
            self.assign('turn-1', native)
            head = 'turn-1'
            self.historical_v5 = False
        else:
            head = self.fail_legacy_final(fail=False)
        def respond(route, request, credential):
            response = build_codex_response(route, request, {})
            response['status'] = 'incomplete'
            response['usage']['output_tokens'] = 128001
            return bind_codex_response(route, request, response)
        with patch.object(protocol.transport, 'send_request', side_effect=respond):
            for number in (1, 2):
                self.mutate(protocol.invoke_assignment, dict(id='old-over-%d' % number, assignment_id=head), credential='fixture')
        with patch.object(reading, 'NEW_PROBLEM_CHAIN_PROTOCOLS', (v7.PROTOCOL, reading.PROTOCOL_V6)):
            action = plan_next(self.store.snapshot()['records'], self.artifacts, head)
            self.assertEqual(action['state'], 'stop', action)
            self.assertEqual(action['code'], 'review_output_over_cap')
            decision = action['details']['decision']
            export = next(step for answer in decision['then'] for step in answer['steps']
                          if step['command'] == 'export' and 'reviewer_id' in step.get('payload', {}))
            reference, _ = self.execute_export(dict(details=dict(step=export)))
            self.finish_from(reference, 'successor')

    def test_k7_nine_targets_with_v6_only_reads_again_and_records(self):
        self.select_targets(9)
        with patch.object(reading, 'NEW_PROBLEM_CHAIN_PROTOCOLS', (reading.PROTOCOL_V6,)):
            self.check_v6_successor()

    def test_k7_v6_only_input_limit_exports_reads_again_and_records(self):
        with patch.object(reading, 'NEW_PROBLEM_CHAIN_PROTOCOLS', (reading.PROTOCOL_V6,)):
            self.check_v6_successor()

    def check_v6_successor(self):
        head = self.fail_legacy_final()
        with patch.object(reading, 'NEW_PROBLEM_CHAIN_PROTOCOLS', reading.NEW_PROBLEM_CHAIN_PROTOCOLS):
            plan = plan_next(self.store.snapshot()['records'], self.artifacts, head)
            self.assertEqual((plan['state'], plan.get('command')), ('handoff', 'export'), plan)
            successor = plan['details']['successor']
            self.assertEqual(successor['protocol'], reading.PROTOCOL_V6)
            self.assertTrue(successor['reads_every_unit_again'])
            self.assertGreater(successor['planned_reading_turns'], 0)
            reference, receipt = self.execute_export(plan)
            self.assertEqual(receipt['protocol_selection']['protocol'], reading.PROTOCOL_V6)
            self.assertEqual(reading._find_record(self.read_packet('successor'))['planned_reading_turns'],
                             successor['planned_reading_turns'])
            current = 'successor'
            for number in range(30):
                packet = self.read_packet(current)
                if reading.find_stage(packet) == 'final':
                    self.invoke(current, self.build_native_result(packet))
                    result = self.mutate(self.load_problem_module().record_openness, dict(assignment_id=current))['result']
                    self.assertTrue(result['reading_turns'])
                    return
                self.invoke(current, self.build_reading_response(packet, excerpts=[]))
                following = 'v6-%d' % number
                self.assign(following, reference, prior=current)
                current = following
            self.fail('The v6 successor must finish.')


class NoticeRoomTests(unittest.TestCase):
    def test_no_notice_reading_and_final_packet_and_prompt_digests_stay_frozen(self):
        fixture = turn_fixtures.TurnPacketTests()
        fixture.setUp()
        packet, chain = fixture.prepare()
        plan = capacity.read_chain_capacity_plan(fixture.artifacts, chain)
        inventory = json.loads(fixture.artifacts.read(chain['inventory']))
        before = json.loads(fixture.artifacts.read(chain['state']))
        projection = v7.project_own_turn(fixture.artifacts, fixture.route, packet, fixture.answer(packet),
            'chain', 'attempt', state=before, inventory=inventory)
        state = v7.build_chain_state(inventory, [projection], route=fixture.route, plan=plan,
                                     initial_state=before)['state']
        final = v7.build_turn_packet(fixture.artifacts, fixture.route, fixture.packet, 'chain', 2,
            inventory, state, plan, capacity.derive_plan_task_limits(plan, 'final'), 'final')
        for kind, value, expected_packet, expected_prompt in (
            ('reading', packet, '6c993fba2a14b639dae4dc5b87f20ff25f47910019744660bd5e1877a345a8d9',
             '1ee257be7dc40a135dce006f8d2911744a22f925488252d31992c788a4dce50c'),
            ('final', final, '2c0de138ceeaa91adcacc32d8f4c1d162c9f0768c33f36498be0920a75ca8184',
             'aa25b66defa740bc21346b3a8c6dabe2d7a305b1468f39a70707a149fbecc675')):
            with self.subTest(kind=kind):
                self.assertNotIn('output_refusal', value)
                self.assertEqual(value['digest'], expected_packet)
                prompt = protocol.canonical_prompt('native_math', reading_stage=kind, reading_protocol=v7.PROTOCOL)
                self.assertEqual(prompt['rendered_prompt_hash'], expected_prompt)

    def test_a7_bounded_45000_character_notice_fits_lowest_reading_level(self):
        fixture = turn_fixtures.TurnPacketTests()
        fixture.setUp()
        packet, chain = fixture.prepare()
        plan = capacity.read_chain_capacity_plan(fixture.artifacts, chain)
        state = json.loads(fixture.artifacts.read(chain['state']))
        inventory = json.loads(fixture.artifacts.read(chain['inventory']))
        limits = capacity.derive_plan_task_limits(plan, 'reading', [dict(material_allowance=15000)],
                                                 notice_characters=45000)
        self.assertEqual(limits['material_allowance'], 15000)
        supplied = v7.build_turn_packet(fixture.artifacts, fixture.route, fixture.packet, 'chain', 1,
                                        inventory, state, plan, limits, 'reading')
        # Exercise the complete reserved wire cost, including JSON escaping, at the bound.
        entries = []
        for number in range(4):
            entry = dict(assignment_id='old-%d' % number, attempt_id='attempt-%d' % number,
                output_digest='a' * 64, refusal_digest='b' * 64, status='refused',
                code='openness_finding_invalid', check='openness.finding', path=[], message='', details={})
            entry['message'] = 'x' * (6000 - len(json.dumps(entry, ensure_ascii=False, sort_keys=True)))
            entries.append(entry)
        part = dict(path=[], value='', complete=False)
        part['value'] = 'x' * (20000 - len(json.dumps(part, ensure_ascii=False, sort_keys=True)))
        notice = dict(protocol=notices.PROTOCOL, form='bounded', refused=entries,
                      output_ref=dict(artifact=fixture.artifacts.put(b'{}'), characters=2), refused_part=part)
        # Fill the remaining fixed-descriptor metadata reserve; its body is not read by request sizing.
        notice['output_ref']['artifact']['path'] += 'x' * (45000 - len(json.dumps(notice, ensure_ascii=False, sort_keys=True)))
        self.assertEqual(len(json.dumps(notice, ensure_ascii=False, sort_keys=True)), 45000)
        self.assertTrue(all(len(json.dumps(entry, sort_keys=True)) <= 6000 for entry in entries))
        self.assertLessEqual(len(json.dumps(part, sort_keys=True)), 20000)
        self.assertEqual(supplied['reading_turn']['capacity']['level_material_allowance'], 15000)
        supplied = notices.attach_refusal_notice(supplied, notice)
        from research_harness import review_prompt_paragraphs as paragraphs, review_preflight
        prompt = protocol.canonical_prompt('native_math', reading_stage='reading', reading_protocol=v7.PROTOCOL)
        prompt = paragraphs.append_prompt_paragraphs(prompt, 'native_math',
            versions=[paragraphs.REFUSAL_PARAGRAPH_VERSIONS['bounded']])
        prompt['parameters']['capacity'] = supplied['reading_turn']['capacity']
        assignment = dict(id='chain', role='native_math', route_id='route', prompt=prompt,
            reading_chain=chain, capacity_plan=chain['capacity_plan'], capacity_plan_kind='reading',
            packet=fixture.artifacts.put_json(supplied))
        records = dict(review_route=dict(route=fixture.route))
        request = protocol.transport.request_body(fixture.route, prompt['text'], supplied)
        report = review_preflight.build_assignment_report(records, fixture.artifacts, assignment, request)
        self.assertTrue(report['plan_comparison']['fits'], report)
        review_preflight.check_request(report, request, packet=supplied, artifacts=fixture.artifacts)


if __name__ == '__main__':
    unittest.main()
