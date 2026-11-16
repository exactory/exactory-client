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

    def test_a7_refused_final_capacity_replacement_keeps_notice_in_the_reserve(self):
        from codex_route_fixtures import build_codex_response, bind_codex_response
        from test_review_output_profile import build_row
        priors = [build_row(level=None, thinking=1000, count=8),
                  build_row(kind='final', level=None, thinking=1000, count=8)]
        with patch.object(capacity, 'read_profile_priors', return_value=priors):
            native = self.export()
            head = self.finish_reading(native)
        self.invoke(head, dict(stage='material', title='A reviewer argument', part=1, text='Part one.', more=True))
        self.assign('material-next', native, prior=head)
        head = 'material-next'
        self.invoke(head, dict(decision='revise', objections=[], reason='Wrong contract.'))
        replacement = self.replace_head(head)
        notice = self.read_packet(replacement['id'])['output_refusal']
        def fail(route, request, credential):
            response = build_codex_response(route, request, {})
            response['status'] = 'incomplete'
            response['usage']['output_tokens'] = 128001
            response['usage']['input_tokens'] = capacity.measure_request(route, request)['text_characters']
            return bind_codex_response(route, request, response)
        with patch.object(protocol.transport, 'send_request', side_effect=fail):
            self.mutate(protocol.invoke_assignment, dict(id='final-over-cap',
                assignment_id=replacement['id']), credential='fixture')
        before = copy.deepcopy(self.store.snapshot()['records'])
        record_bytes = {kind: {key: v7.serialize_record(row) for key, row in rows.items()}
                        for kind, rows in before.items()}
        old_artifacts = {}
        def retain_artifacts(value):
            if isinstance(value, dict):
                if set(value) == {'path', 'sha256', 'size', 'media_type'}:
                    old_artifacts[value['sha256']] = (copy.deepcopy(value), self.artifacts.read(value))
                for item in value.values():
                    retain_artifacts(item)
            elif isinstance(value, list):
                for item in value:
                    retain_artifacts(item)
        retain_artifacts(before)
        frozen = self.read_packet(replacement['id'])
        self.assertEqual(frozen['reading_final']['capacity']['material_allowance'], 480000)
        route = before['review_route']['route']
        self.assertLessEqual(v7._measure_final_material(route,
            {key: frozen['reading_final'][key] for key in v7.FINAL_MATERIAL_FIELDS}), 240000)
        reduced = self.replace_head(replacement['id'])
        packet = self.read_packet(reduced['id'])
        self.assertEqual(packet['output_refusal'], notice)
        for key in v7.FINAL_MATERIAL_FIELDS:
            self.assertEqual(packet['reading_final'][key], frozen['reading_final'][key], key)
        self.assertEqual(packet['reading_final']['capacity']['material_allowance'], 240000)
        self.assertNotIn('level_material_allowance', packet['reading_final']['capacity'])
        self.assertLess(packet['reading_final']['capacity']['answer_bound'],
                        self.read_packet(replacement['id'])['reading_final']['capacity']['answer_bound'])
        self.invoke(reduced['id'], dict(stage='material', title='A reviewer argument', part=2, text='Part two.', more=False))
        self.assign('corrected-final', native, prior=reduced['id'])
        self.finish_from(native, 'corrected-final')
        attempt = self.store.snapshot()['records']['review_attempt']['attempt-' + reduced['id']]
        self.assertEqual(attempt['task']['material_allowance'], 240000)
        self.assertEqual(attempt['task']['level_material_allowance'], 240000)
        self.assertEqual(attempt['task']['notice_characters'], len(json.dumps(
            notice, ensure_ascii=False, sort_keys=True, allow_nan=False)))
        after = self.store.snapshot()['records']
        self.assertTrue(protocol.authenticate_attempt(after, self.artifacts, after['review_assignment'][reduced['id']], attempt))
        for kind, rows in before.items():
            for key, row in rows.items():
                self.assertEqual(v7.serialize_record(after[kind][key]), record_bytes[kind][key], (kind, key))
                self.assertEqual(digest(after[kind][key]), digest(row), (kind, key))
        for reference, content in old_artifacts.values():
            self.assertEqual(self.artifacts.read(reference), content)

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


class FullGrantFinalRefusalTests(OpennessReadingCase):
    """F14/R59: the final's notice uses the stored reserve, preserving its grants."""
    capacity_probe_tokens = 487900
    reading_protocol = None
    route_model = 'gpt-6-astra'
    route_output_tokens = 128000
    text_sizes = (1500000,)
    replace_head = V7RefusalTests.replace_head
    build_v7_answer = V7RefusalTests.build_v7_answer
    build_v7_finding = V7RefusalTests.build_v7_finding

    def setUp(self):
        super().setUp()
        enabled = patch.object(reading, 'NEW_PROBLEM_CHAIN_PROTOCOLS', (v7.PROTOCOL, reading.PROTOCOL_V6))
        enabled.start()
        self.addCleanup(enabled.stop)

    def prepare_full_final(self, *, stop_when_full=False, stop_after_assignment=False):
        from test_review_output_profile import build_row
        from test_research_problem_openness_minimum import ProblemOpennessMinimumTests
        from research_harness.problem_openness import export_openness
        self.selected = self.select_statement(identifier='eight-targets', candidate_id='candidate-1',
            neighbours=['Neighbour %d.' % number for number in range(1, 8)])
        searches = [dict(formulation=self.selected['statement'], search_id=self.search['id'])]
        for formulation in self.selected['neighbours']:
            row = ProblemOpennessMinimumTests.search(self, self.versions, formulation=formulation)
            searches.append(dict(formulation=formulation, search_id=row['id']))
        priors = [build_row(level=None, thinking=1000, count=8),
                  build_row(kind='final', level=None, thinking=1000, count=8)]
        with patch.object(capacity, 'read_profile_priors', return_value=priors):
            native = export_openness(self.store, dict(assignment_id='turn-1', reviewer_id='openness-reviewer',
                minimum=dict(searches=searches, settling_work_id=None, duplicate_resolutions=[])),
                self.root / 'eight-target-packet')['native_packet']
            self.assign('turn-1', native)
        if stop_after_assignment:
            return 'turn-1'
        current = 'turn-1'
        route = self.store.snapshot()['records']['review_route']['route']
        for number in range(1, 40):
            packet = self.read_packet(current)
            if stop_when_full and 'reading_turn' in packet:
                assigned = self.store.snapshot()['records']['review_assignment'][current]
                _, _, retained = v7.read_bound_state(self.artifacts, assigned, packet)
                if (retained['latest_notes'] is not None
                        and len(retained['latest_notes']) == retained['allowances']['notes']
                        and all(row['used'] == row['characters'] for row in retained['allowances']['targets'])):
                    return current
            if 'reading_final' in packet:
                assignment = self.store.snapshot()['records']['review_assignment'][current]
                plan, _, state = v7.read_bound_state(self.artifacts, assignment, packet)
                self.assertEqual(plan['task']['final']['material_allowance'], 480000)
                self.assertEqual(len(state['allowances']['targets']), 8)
                self.assertEqual(len(state['latest_notes']), state['allowances']['notes'])
                self.assertTrue(all(row['used'] == row['characters'] for row in state['allowances']['targets']))
                # Conservative serialized handles leave a little space, but less than either notice.
                material = {key: packet['reading_final'][key] for key in v7.FINAL_MATERIAL_FIELDS}
                self.assertGreater(v7._measure_final_material(route, material), 476000)
                return current
            binding = packet['reading_turn']
            answer = self.build_v7_answer(packet)
            answer['notes'] = 'n' * binding['allowances']['notes']
            room = binding['allowances']['excerpts']
            for target in binding['allowances']['targets']:
                remaining = target['remaining']
                for row, body in zip(binding['plan']['now'], packet['evidence']):
                    if row['kind'] != 'fulltext' or min(room, remaining) <= 1000:
                        continue
                    def measure_width(width):
                        entry = dict(kind='excerpt', handle='e999999999', unit=row['handle'],
                            text=body['content'][:width], purpose='Retained detail.', targets=[target['handle']],
                            start=row['start'], end=row['start'] + width, sha256='x' * 64, status='verified')
                        return capacity.measure_capacity_value(route, entry) + capacity.measure_capacity_value(
                            route, {key: row[key] for key in ('handle', 'kind', 'label')})
                    low, high = 1, min(len(body['content']), room, remaining)
                    while low < high:
                        middle = (low + high + 1) // 2
                        if measure_width(middle) <= min(room, remaining):
                            low = middle
                        else:
                            high = middle - 1
                    charge = measure_width(low)
                    answer['excerpts'].append(dict(unit=row['handle'], text=body['content'][:low],
                        purpose='Retained detail.', targets=[target['handle']]))
                    room -= charge
                    remaining -= charge
            self.invoke(current, answer)
            following = 'turn-%d' % (number + 1)
            self.assign(following, native, prior=current)
            current = following
        self.fail('The full-grant fixture must reach its final with every target filled.')

    def refuse_final(self, head, characters=4000, *, characters_per_token=None):
        output = dict(decision='revise', objections=[], reason='')
        if characters == 100000:
            pattern = '{"finding": ["quoted", "\\\\", null]}\n'
            output['reason'] = pattern * ((characters - len(json.dumps(output))) // (len(json.dumps(pattern)) - 2))
        output['reason'] += 'r' * (characters - len(json.dumps(output)))
        self.assertEqual(len(json.dumps(output)), characters)
        if characters_per_token is None:
            response = self.invoke(head, output)
        else:
            from codex_route_fixtures import build_codex_response, bind_codex_response
            def respond(route, request, credential):
                response = build_codex_response(route, request, output)
                response['usage']['input_tokens'] = int(capacity.measure_request(route, request)['text_characters']
                                                       / characters_per_token)
                return bind_codex_response(route, request, response)
            with patch.object(protocol.transport, 'send_request', side_effect=respond):
                response = self.mutate(protocol.invoke_assignment, dict(id='attempt-' + head,
                    assignment_id=head), credential='fixture')
        self.assertEqual(response['output_refusal']['check'], 'openness.finding')

    def check_full_final_replacement(self, characters, form):
        from research_harness import review_preflight
        head = self.prepare_full_final()
        original = self.read_packet(head)['reading_final']
        self.refuse_final(head, characters)
        before = copy.deepcopy(self.store.snapshot()['records'])
        prior = before['review_assignment'][head]
        stored_plan = self.artifacts.read(prior['capacity_plan']['artifact'])
        replacement = self.replace_head(head)
        packet = self.read_packet(replacement['id'])
        self.assertEqual(replacement['reading_chain'], prior['reading_chain'])
        self.assertEqual(packet['output_refusal']['form'], form)
        if form == 'bounded':
            route = before['review_route']['route']
            self.assertLessEqual(capacity.measure_capacity_value(route, packet['output_refusal']), 45000)
            self.assertLessEqual(capacity.measure_capacity_value(route, packet['output_refusal']['refused_part']), 20000)
            self.assertTrue(all(capacity.measure_capacity_value(route, row) <= 6000
                                for row in packet['output_refusal']['refused']))
        self.assertEqual(packet['reading_final']['capacity']['material_allowance'], 480000)
        self.assertNotIn('level_material_allowance', packet['reading_final']['capacity'])
        for key in v7.FINAL_MATERIAL_FIELDS:
            self.assertEqual(packet['reading_final'][key], original[key], key)
        self.assertEqual(replacement['prompt']['template_id'], prior['prompt']['template_id'])
        route = before['review_route']['route']
        request = protocol.transport.request_body(route, replacement['prompt']['text'], packet)
        report = review_preflight.build_assignment_report(before, self.artifacts, replacement, request)
        self.assertTrue(report['plan_comparison']['fits'], report)
        plan = capacity.read_chain_capacity_plan(self.artifacts, replacement)
        self.assertEqual(plan['task']['final']['request_bound']['text_characters'],
                         plan['frame']['final']['bound'] + 45000 + 480000)
        self.invoke(replacement['id'], self.build_v7_finding(packet, packet['reading_final']['notes_handle']))
        records = self.store.snapshot()['records']
        reading.verify_chain(records, self.artifacts, records['review_assignment'][replacement['id']])
        finding = self.mutate(self.load_problem_module().record_openness,
                              dict(assignment_id=replacement['id']))['result']
        self.assertEqual(finding['outcome'], 'unresolved')
        after = self.store.snapshot()['records']
        attempt = after['review_attempt']['attempt-' + replacement['id']]
        self.assertEqual(attempt['task']['material_allowance'], 480000)
        self.assertEqual(attempt['task']['level_material_allowance'], 480000)
        self.assertEqual(attempt['task']['notice_characters'], len(json.dumps(
            packet['output_refusal'], ensure_ascii=False, sort_keys=True, allow_nan=False)))
        self.assertGreater(attempt['task']['characters'], 480000)
        profile = capacity.read_output_profile(after, route, priors=[])
        row = next(row for row in profile['rows'] if row['kind'] == 'final' and row['task_level'] is None)
        self.assertEqual(row['attempts'], 2)
        self.assertEqual(self.artifacts.read(prior['capacity_plan']['artifact']), stored_plan)
        for kind in ('review_assignment', 'review_attempt', 'review_output_refusal'):
            for key, value in before.get(kind, {}).items():
                self.assertEqual(digest(after[kind][key]), digest(value))

    def test_t1_short_refused_full_final_replaces_with_whole_notice_and_records(self):
        self.check_full_final_replacement(4000, 'whole')

    def test_t1_large_refused_full_final_replaces_with_bounded_notice_and_records(self):
        self.check_full_final_replacement(100000, 'bounded')

    def test_t3_refused_full_final_exports_after_a_denser_ratio_lowers_its_level(self):
        from codex_route_fixtures import build_codex_response, bind_codex_response
        head = self.prepare_full_final()
        self.refuse_final(head, characters_per_token=4)
        records = self.store.snapshot()['records']
        prior = records['review_assignment'][head]
        plan = capacity.read_chain_capacity_plan(self.artifacts, prior)
        self.assertGreater(capacity.measure_attempt_ratio(records['review_attempt']['attempt-' + head],
            margin=plan['measure']['ratio_margin']), plan['measure']['characters_per_token'])
        self.assertEqual(capacity.derive_turn_task_limits(records, self.artifacts, prior, kind='final')['material_allowance'], 480000)
        action = plan_next(records, self.artifacts, head)
        self.assertEqual((action['state'], action.get('command')), ('replace', 'review-assignment'), action)
        replacement = self.replace_head(head)
        original = self.read_packet(replacement['id'])
        def respond(route, request, credential):
            response = build_codex_response(route, request, dict(decision='revise', objections=[], reason='r' * 100000))
            response['usage']['input_tokens'] = capacity.measure_request(route, request)['text_characters'] * 2
            return bind_codex_response(route, request, response)
        with patch.object(protocol.transport, 'send_request', side_effect=respond):
            response = self.mutate(protocol.invoke_assignment,
                dict(id='dense-refused-final', assignment_id=replacement['id']), credential='fixture')
        self.assertEqual(response['output_refusal']['check'], 'openness.finding')
        records = self.store.snapshot()['records']
        route = records['review_route']['route']
        limits = capacity.derive_turn_task_limits(records, self.artifacts, replacement, kind='final')
        material = {key: original['reading_final'][key] for key in v7.FINAL_MATERIAL_FIELDS}
        self.assertLess(limits['material_allowance'], v7._measure_final_material(route, material))
        action = plan_next(records, self.artifacts, replacement['id'])
        self.assertEqual((action['state'], action.get('command')), ('handoff', 'export'), action)
        successor = action['details']['successor']
        self.assertEqual(successor['protocol'], v7.PROTOCOL)
        self.assertEqual(successor['measure']['characters_per_token'], .47)
        self.assertEqual(successor['measure']['source'], dict(chain='turn-1', attempt='dense-refused-final'))
        self.assertTrue(successor['carried_readings']['chains'])


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

    def prepare_bound_readings(self, *, family=False):
        """Synthetic stopped-chain shape: a search cites two nondecisive readings."""
        from research_harness.reading import record_reading
        from test_research_problem_openness_minimum import ProblemOpennessMinimumTests
        supporting = []
        for number in (5, 6):
            note = self.abstract_note(self.metadata(number))
            note['id'] = 'support-%d' % number
            self.mutate(record_reading, note)
            supporting.append(self.store.snapshot()['records']['reading'][note['id']])
        search = copy.deepcopy(self.search)
        search['dispositions'][0]['reason'] = 'Compared (support-5) and (support-6).'
        if family:
            # The legacy minimum cannot use the complete reading of the linked v1.
            search['dispositions'][0]['disposition'] = 'out_of_scope'
        self.put('literature_search', search['id'], search)
        self.searches = [search]
        if family:
            found = 'doi:10.48550/arxiv.' + self.versions[0][6:].rsplit('v', 1)[0]
            self.searches.append(ProblemOpennessMinimumTests.search(self, [found]))
        return supporting

    def check_bound_reading_successor(self, *, family=False, targets=1):
        from research_harness.review_series import run_series
        self.prepare_bound_readings(family=family)
        if targets > 1:
            self.select_targets(targets)
        head = self.fail_legacy_final()
        before = copy.deepcopy(self.store.snapshot()['records'])
        files = {path.relative_to(self.root): path.read_bytes() for path in self.root.rglob('*') if path.is_file()}
        expected = reading.PROTOCOL_V6 if targets == 9 else v7.PROTOCOL
        with patch.object(reading, 'NEW_PROBLEM_CHAIN_PROTOCOLS', (v7.PROTOCOL, reading.PROTOCOL_V6)):
            plan = plan_next(before, self.artifacts, head)
            self.assertEqual((plan['state'], plan.get('command'),
                plan['details'].get('successor', {}).get('protocol')), ('handoff', 'export', expected), plan)
            self.assertEqual(self.store.snapshot()['records'], before)
            self.assertEqual({path.relative_to(self.root): path.read_bytes()
                for path in self.root.rglob('*') if path.is_file()}, files)
            rows = []
            self.assertEqual(run_series(self.store, head, emit=rows.append), 3)
            self.assertEqual(rows[-1]['state'], 'handoff', rows[-1])
            self.assertEqual(rows[-1]['details']['successor'], plan['details']['successor'])
            self.assertEqual(rows[-1]['command'], 'export')
            reference, receipt = self.execute_export(plan)
            self.assertEqual(receipt['protocol_selection']['protocol'], expected)
            if expected == v7.PROTOCOL:
                self.assertTrue(receipt['carried_readings']['chains'])
                self.assertEqual(receipt['carried_readings'], plan['details']['successor']['carried_readings'])
            else:
                self.assertTrue(plan['details']['successor']['reads_every_unit_again'])
                self.assertEqual(receipt['carried_readings']['chains'], [])
                self.assertEqual(reading._find_record(self.read_packet('successor'))['planned_reading_turns'],
                                 plan['details']['successor']['planned_reading_turns'])
            turns = self.finish_from(reference, 'successor')
            if expected == reading.PROTOCOL_V6:
                self.assertGreater(len(turns), 1)
                delivered = [unit for turn in turns for unit in turn['delivered_units']]
                self.assertTrue(all(self.unit_identity(version) in delivered for version in self.versions))
            after = self.store.snapshot()['records']
            self.assertTrue(after['problem_openness'])
            self.assertFalse(any(row.get('code') == 'openness_reading_incomplete'
                for records in after.values() for row in records.values()))
            for kind, records in before.items():
                for key, record in records.items():
                    self.assertEqual(digest(after[kind][key]), digest(record), (kind, key))
            for path, content in files.items():
                if str(path).startswith('research/sources/objects/'):
                    self.assertEqual((self.root / path).read_bytes(), content)

    def test_k7_v5_head_whose_search_names_a_reading_exports_to_v7_and_records(self):
        self.check_bound_reading_successor()

    def test_k7_v5_head_with_a_family_reading_exports_to_v7_and_records(self):
        self.check_bound_reading_successor(family=True)

    def test_k7_named_reading_with_nine_targets_exports_to_v6_and_records(self):
        self.check_bound_reading_successor(targets=9)

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


class SuccessorProjectionTests(OpennessReadingCase):
    reading_protocol = None
    historical_v5 = True
    prepare_bound_readings = LegacySuccessorTests.prepare_bound_readings
    export = LegacySuccessorTests.export

    def test_projected_packet_carries_every_record_that_its_minimum_binds(self):
        from research_harness.native_review import load_packet
        from research_harness.openness_successor import _project_successor_native
        for family in (False, True):
            with self.subTest(family=family):
                case = SuccessorProjectionTests()
                case.setUp()
                try:
                    supporting = case.prepare_bound_readings(family=family)
                    reference = case.export()
                    native = json.loads(case.artifacts.read(reference))
                    records = case.store.snapshot()['records']
                    files = {path.relative_to(case.root): path.read_bytes()
                             for path in case.root.rglob('*') if path.is_file()}
                    overlay = v7._PlanningArtifacts(case.artifacts)
                    projected = _project_successor_native(records, overlay, native)
                    rows = reading.list_record_rows(overlay, projected)
                    self.assertEqual({row['record_id'] for row in rows if row.get('role') == 'supporting'},
                                     {row['id'] for row in supporting})
                    texts = reading.list_reading_texts(records, projected, artifacts=overlay)
                    self.assertEqual({row['version_id'] for row in texts}, set(case.versions))
                    minimum = projected['scientific_context']['openness_minimum']
                    self.assertEqual(minimum['schema_version'], 2)
                    self.assertEqual(len(minimum['family_readings']), int(family))
                    if family:
                        self.assertEqual(projected['scientific_context']['family_reading_plan_version'],
                                         reading.FAMILY_READING_PLAN_VERSION)
                    stored = overlay.put(v7.serialize_record(projected), 'application/json')
                    self.assertEqual(load_packet(overlay, stored, 'openness-reviewer'), projected)
                    self.assertEqual(_project_successor_native(records, overlay, projected), projected)
                    self.assertEqual(json.loads(case.artifacts.read(reference)), native)
                    self.assertEqual(case.store.snapshot()['records'], records)
                    self.assertEqual({path.relative_to(case.root): path.read_bytes()
                        for path in case.root.rglob('*') if path.is_file()}, files)
                finally:
                    case.doCleanups()

    def test_projection_equals_the_export_of_the_same_declarations(self):
        from research_harness.openness_successor import _project_successor_native
        from research_harness.problem_openness import export_openness
        self.prepare_bound_readings()
        native = json.loads(self.artifacts.read(self.export()))
        minimum = native['scientific_context']['openness_minimum']
        records = self.store.snapshot()['records']
        searches = {digest(row): row for row in records['literature_search'].values()}
        spec = dict(assignment_id='turn-1', reviewer_id='openness-reviewer', minimum=dict(
            searches=[dict(formulation=row['formulation'], search_id=searches[row['search_digest']]['id'])
                      for row in minimum['searches']], settling_work_id=minimum['settling_work_id'],
            duplicate_resolutions=minimum['duplicate_resolutions']))
        projected = _project_successor_native(records, v7._PlanningArtifacts(self.artifacts), native)
        reference = export_openness(self.store, spec, self.root / 'current-export')['native_packet']
        exported = json.loads(self.artifacts.read(reference))
        self.assertEqual([row['digest'] for row in projected['evidence']],
                         [row['digest'] for row in exported['evidence']])
        for key in ('openness_minimum', 'minimum_gaps', 'audit_forecast', 'decisive_work_ids'):
            self.assertEqual(projected['scientific_context'][key], exported['scientific_context'][key], key)


class NoticeRoomTests(unittest.TestCase):
    def build_label_native(self, route, count, label_set):
        native = turn_fixtures.build_native(count)
        labels = ['s'] + ['n'] * (count - 1)
        if label_set in ('just_under', 'exact', 'above'):
            native['scientific_context']['openness_minimum'].update(statement=labels[0], neighbours=labels[1:])
            base = capacity.measure_capacity_value(route, v7.list_target_labels(native))
            boundary = {'just_under': 99999, 'exact': 100000, 'above': 100001}[label_set]
            width, remainder = divmod(boundary - base, count)
            labels = ['x' * (1 + width + int(index < remainder)) for index in range(count)]
        elif label_set == 'mixed':
            labels = ['s'] + ['n' if number <= 16 else 'n' * 1000 for number in range(1, count)]
        native['scientific_context']['openness_minimum'].update(statement=labels[0], neighbours=labels[1:])
        size = capacity.measure_capacity_value(route, v7.list_target_labels(native))
        if label_set in ('just_under', 'exact', 'above'):
            self.assertEqual(size, {'just_under': 99999, 'exact': 100000, 'above': 100001}[label_set])
        return native

    def measure_remaining_frame(self, route, packet, native, fields, level):
        # Consume only actual wire cost of each declared component. Unused component
        # budget, including the absent label vector, never reduces the envelope.
        skeleton = copy.deepcopy(packet)
        binding = skeleton['reading_final']
        def measure(prompt=''):
            request = protocol.transport.request_body(route, prompt, skeleton)
            return capacity.measure_request(route, request)['text_characters']
        cost = measure()
        prompt = protocol.canonical_prompt('native_math', reading_stage='final', reading_protocol=v7.PROTOCOL)
        excess = max(0, measure(prompt['text']) - cost - fields['instructions'])
        def reserve_values(container, keys, budget, *, outer_keys=()):
            nonlocal cost, excess
            for key in keys:
                container.pop(key, None)
            for key in outer_keys:
                skeleton.pop(key)
            remaining = measure()
            excess += max(0, cost - remaining - budget)
            cost = remaining
        reserve_values(binding, ('protocol', 'chain', 'turn', 'plan_digest', 'inventory_digest', 'state_digest',
            'subject_digest', 'claim_digest', 'reviewer'), fields['bindings'], outer_keys=('reviewer_id',))
        reserve_values(binding, v7.FINAL_MATERIAL_FIELDS, level + fields['material_envelope'])
        reserve_values(binding, ('allowances',), fields['allowances'])
        reserve_values(binding, ('page',), fields['page'])
        reserve_values(skeleton['native'], tuple(v7._describe_native_fields(native)) + ('context_unit',), fields['native_frame'])
        reserve_values(skeleton['native'], ('subject',), fields['statement'])
        reserve_values(skeleton['native']['scientific_context'], ('decisive_work_ids',), fields['decisive_work_ids'])
        reserve_values(binding['minimum'], ('gaps_unit',), fields['minimum.gaps_unit'])
        reserve_values(skeleton['native'], ('scientific_context',), fields['minimum'])
        return cost + excess

    def check_final_wire_case(self, fixture, records, native, admission, *, pending=False, carried=False, unread=False):
        from research_harness import review_prompt_paragraphs as paragraphs
        from research_harness.operations import text
        plan = copy.deepcopy(admission['capacity_plan'])
        inventory = admission['inventory']['inventory']
        logical = dict(fixture.packet, native=native,
                       reviewer_id=(native['reviewer']['actor_id'] if isinstance(native['reviewer'], dict)
                                    else native['reviewer']))
        if carried:
            origin = dict(chain='origin', protocol=reading.PROTOCOL_V6, assignment_id='old', attempt_id='old-attempt')
            projection = dict(turn=dict(origin=origin, assignment_id='old', attempt_id='old-attempt', kind='reading'),
                deliveries=[], entries=[], searches=[], gaps=[], arguments=[], allowance_use=dict(targets={}, passages=0),
                notes=dict(classification='affected', units=[], text='An earlier retained note.'))
            _, chain = v7.prepare_first_assignment(records, fixture.artifacts, fixture.value, fixture.route,
                logical, [projection], admission=copy.deepcopy(admission))
            plan = capacity.read_chain_capacity_plan(fixture.artifacts, chain)
            inventory = json.loads(fixture.artifacts.read(chain['inventory']))
            state = json.loads(fixture.artifacts.read(chain['state']))
            self.assertTrue(state['turns'][0]['origin'])
            self.assertTrue(state['carried_readings'])
        else:
            state = v7.build_chain_state(inventory, [], route=fixture.route, plan=plan)['state']
        level = plan['task']['final']['material_allowance']
        state['latest_notes'] = 'n' * level
        state['notes'].append(dict(classification='current', turn=999999999, text=state['latest_notes']))
        # All three target counters have their largest serialized width, consistently.
        for row in state['allowances']['targets']:
            row.update(characters=999999999, used=499999999)
        state['allowances']['notes'] = 20000
        state['allowances']['passages'].update(characters=100000, used=100000)
        if pending:
            from research_harness.review_handles import MATERIAL_PART_LIMIT
            state['material_state'] = dict(arguments={}, pending=dict(ref='m999999999', part=MATERIAL_PART_LIMIT))
            state['material_request'] = dict(stage='material', title='\x01' * 120, ref='m999999999', part=MATERIAL_PART_LIMIT)
            text(state['material_request']['title'], 'Maximum material title')
        limits = capacity.derive_plan_task_limits(plan, 'final')
        logical['protocol'] = protocol.PROTOCOL
        identifier = '\x01' * reading.RESERVED_IDENTIFIER_CHARACTERS
        text(identifier, 'Maximum chain identifier')
        reading.require_reserved_identifier(identifier, v7.PROTOCOL)
        packet = v7.build_turn_packet(fixture.artifacts, fixture.route, logical, identifier,
            999999999, inventory, state, plan, limits, 'final')
        packet['reading_final']['allowances']['excerpts'] = 40000
        context = packet['native']['scientific_context']
        context['audit_forecast'].update(gaps=999999999, notices=999999999)
        if isinstance(context['decisive_work_ids'], dict):
            context['decisive_work_ids']['count'] = 999999999
        for key in ('evidence', 'execution_provenance', 'unmaterialized_digests'):
            packet['native'][key] = dict(count=999999999)
        packet['digest'] = digest({key: value for key, value in packet.items() if key != 'digest'})
        fields = plan['frame']['final']['fields']
        handles = plan['targets']['handles']
        allowance_bound = v7.compute_allowance_rows_bound(fixture.route, handles)
        self.assertEqual(fields['allowances'], allowance_bound)
        self.assertLessEqual(capacity.measure_capacity_value(fixture.route, packet['reading_final']['allowances']), allowance_bound)
        self.assertEqual(packet['reading_final']['page']['targets'], handles)
        self.assertLessEqual(capacity.measure_capacity_value(fixture.route, packet['reading_final']['page']), fields['page'])
        self.assertEqual(fields['envelope'], 3000)
        envelope = self.measure_remaining_frame(fixture.route, packet, native, fields, level)
        self.assertLessEqual(envelope, 3000)
        self.assertEqual(v7._measure_final_material(fixture.route,
            {key: packet['reading_final'][key] for key in v7.FINAL_MATERIAL_FIELDS}), level)
        prompt = protocol.canonical_prompt('native_math', reading_stage='final', reading_protocol=v7.PROTOCOL)
        request = protocol.transport.request_body(fixture.route, prompt['text'], packet)
        before = capacity.measure_request(fixture.route, request)['text_characters']
        bound = plan['frame']['final']['bound'] + 45000 + level
        self.assertEqual(plan['task']['final']['request_bound']['text_characters'], bound)
        self.assertLessEqual(before, bound - 47000)
        notice = self.build_bounded_notice(fixture.artifacts, route=fixture.route)
        self.assertEqual(capacity.measure_capacity_value(fixture.route, notice), 45000)
        packet = notices.attach_refusal_notice(packet, notice)
        prompt = paragraphs.append_prompt_paragraphs(prompt, 'native_math',
            versions=[paragraphs.REFUSAL_PARAGRAPH_VERSIONS['bounded']])
        request = protocol.transport.request_body(fixture.route, prompt['text'], packet)
        after = capacity.measure_request(fixture.route, request)['text_characters']
        self.assertLessEqual(after, bound)
        comparison = capacity.describe_plan_preflight(records, fixture.route, request, plan, 'final')['plan_comparison']
        self.assertTrue(comparison['fits'], comparison)
        if unread:
            from test_openness_unread_final import UnreadFrameTests
            UnreadFrameTests().check_unread_wire_case(fixture, logical, identifier, inventory, state, plan, limits)
        return dict(level=level, envelope=envelope, without_notice_margin=bound - 47000 - before, margin=bound - after)

    def test_t2_every_admitted_count_at_floor_and_highest_level_has_reserved_wire_room(self):
        self.run_final_wire_matrix()

    def run_final_wire_matrix(self, *, unread=False):
        from test_review_capacity_closure import build_capacity_records
        from test_review_output_profile import build_row
        fixture = turn_fixtures.TurnPacketTests()
        fixture.setUp()
        self.assertEqual(capacity.describe_route_class(fixture.route),
                         dict(adapter='codex_cli_v1', model='gpt-6-astra', effort='high'))
        self.assertEqual(fixture.route['configuration']['max_output_tokens'], 128000)
        records = build_capacity_records(fixture.route, tokens=487900, capacity_probe=True)
        summaries = []
        for level in (60000, 480000):
            priors = [] if level == 60000 else [build_row(level=None, thinking=1000, count=8, over=0),
                build_row(kind='final', level=None, thinking=1000, count=8, over=0)]
            if priors:
                self.assertEqual([(row['kind'], row['task_level'], row['attempts'],
                    row['largest_thinking_tokens'], row['largest_output_tokens']) for row in priors],
                    [('reading', None, 8, 1000, 65000), ('final', None, 8, 1000, 65000)])
            for label_set in ('short', 'just_under', 'exact', 'above', 'mixed'):
                margins, endpoints = [], []
                with patch.object(v7, 'build_inventory', return_value=fixture.built), patch.object(
                        capacity, 'read_profile_priors', return_value=priors):
                    for count in range(1, 77):
                        native = self.build_label_native(fixture.route, count, label_set)
                        admission = v7.check_v7_admission(records, fixture.artifacts, fixture.route, native)
                        if admission['state'] != 'record':
                            self.assertEqual(admission['largest_one_request_targets'], count - 1)
                            break
                        self.assertEqual(admission['capacity_plan']['task']['final']['material_allowance'], level)
                        self.assertEqual(admission['capacity_plan']['measure']['characters_per_token'], 2.1)
                        moved_labels = any(row['identity'].get('field') == 'formulations'
                                           for row in admission['inventory']['inventory']['units'])
                        if label_set in ('just_under', 'exact', 'above'):
                            self.assertEqual(moved_labels, label_set == 'above')
                        if level == 60000 and label_set == 'short' and count == 2:
                            parts = admission['capacity_plan']['allocation']['carried']['parts']
                            self.assertEqual([row['minimum'] for row in parts], [6000, 6700, 6700])
                            with self.assertRaises(ResearchError) as caught:
                                capacity.divide_room(15000, parts)
                            self.assertEqual(caught.exception.code, 'review_route_capacity_below_floor')
                        endpoints = [endpoints[0], (count, native, admission)] if endpoints else [(count, native, admission)]
                        with self.subTest(level=level, labels=label_set, targets=count):
                            result = self.check_final_wire_case(fixture, records, native, admission, unread=unread)
                            margins.append(result)
                    else:
                        self.fail('The fixture must reach its first unadmitted target count.')
                    self.assertEqual(count - 1, 8 if level == 60000 else 70)
                    for targets, native, admission in endpoints:
                        for pending, carried in ((True, False), (False, True), (True, True)):
                            with self.subTest(level=level, labels=label_set, targets=targets, pending=pending, carried=carried):
                                margins.append(self.check_final_wire_case(fixture, records, native, admission,
                                    pending=pending, carried=carried, unread=unread))
                if margins:
                    summaries.append(dict(level=level, labels=label_set, maximum=count - 1, cases=len(margins),
                        maximum_envelope=max(row['envelope'] for row in margins),
                        minimum_margin=min(row['margin'] for row in margins),
                        minimum_without_notice_margin=min(row['without_notice_margin'] for row in margins)))
        print(json.dumps(dict(f14_wire_matrix=summaries), sort_keys=True))

    def test_t2_review_regression_17_mixed_targets_at_level_240000_has_reserved_wire_room(self):
        from test_review_output_profile import build_row
        fixture = turn_fixtures.TurnPacketTests()
        fixture.setUp()
        native = self.build_label_native(fixture.route, 17, 'mixed')
        with patch.object(v7, 'build_inventory', return_value=fixture.built), patch.object(capacity,
                'read_profile_priors', return_value=[build_row(level=None, thinking=1000, count=8),
                    build_row(kind='final', level=None, thinking=1000, count=8)]):
            admission = v7.check_v7_admission({}, fixture.artifacts, fixture.route, native)
        self.assertEqual(admission['state'], 'record')
        self.assertEqual(admission['capacity_plan']['task']['final']['material_allowance'], 240000)
        result = self.check_final_wire_case(fixture, {}, native, admission)
        print(json.dumps(dict(f14_review_17_targets=result), sort_keys=True))

    def test_t2_long_reviewer_identity_fits_the_frame_inline_and_after_native_frame_moves(self):
        from research_harness.operations import text
        from test_review_capacity_closure import build_capacity_records
        fixture = turn_fixtures.TurnPacketTests()
        fixture.setUp()
        records = build_capacity_records(fixture.route, tokens=487900, capacity_probe=True)
        for characters, compact in ((120, False), (3000, True), (20000, True)):
            with self.subTest(characters=characters, compact=compact):
                native = self.build_label_native(fixture.route, 1, 'short')
                native['reviewer'] = dict(actor_id='\x01' * characters, source='host',
                                          attestation_id='review-assignment:synthetic')
                text(native['reviewer']['actor_id'], 'Reviewer identifier')
                if compact:
                    native['contract'] = dict(question='x' * 100001)
                with patch.object(v7, 'build_inventory', return_value=fixture.built), patch.object(
                        capacity, 'read_profile_priors', return_value=[]):
                    admission = v7.check_v7_admission(records, fixture.artifacts, fixture.route, native)
                if characters == 20000:
                    self.assertEqual(admission['state'], 'stop')
                    self.assertEqual(admission['details']['reason'], 'fixed_frame')
                    continue
                self.assertEqual(admission['state'], 'record')
                moved = any(row['identity'].get('field') == 'native_frame'
                            for row in admission['inventory']['inventory']['units'])
                self.assertEqual(moved, compact)
                for pending, carried in ((False, False), (True, False), (False, True), (True, True)):
                    with self.subTest(pending=pending, carried=carried):
                        self.check_final_wire_case(fixture, records, native, admission,
                                                   pending=pending, carried=carried)

    def build_bounded_notice(self, artifacts, *, route=None):
        def measure(value):
            return (capacity.measure_capacity_value(route, value) if route else
                    len(json.dumps(value, ensure_ascii=False, sort_keys=True)))
        # Exercise the complete reserved wire cost, including JSON escaping, at the bound.
        entries = []
        for number in range(4):
            entry = dict(assignment_id='old-%d' % number, attempt_id='attempt-%d' % number,
                output_digest='a' * 64, refusal_digest='b' * 64, status='refused',
                code='openness_finding_invalid', check='openness.finding', path=[], message='', details={})
            entry['message'] = 'x' * (6000 - measure(entry))
            entries.append(entry)
        part = dict(path=[], value='', complete=False)
        part['value'] = 'x' * (20000 - measure(part))
        notice = dict(protocol=notices.PROTOCOL, form='bounded', refused=entries,
                      output_ref=dict(artifact=artifacts.put(b'{}'), characters=2), refused_part=part)
        # Fill fixed descriptor metadata; request sizing does not dereference its synthetic path.
        notice['output_ref']['artifact']['path'] += 'x' * (45000 - measure(notice))
        return notice

    def test_t7_no_notice_packets_change_only_plan_digest_and_keep_prompt_hashes(self):
        import hashlib
        fixture = turn_fixtures.TurnPacketTests()
        fixture.setUp()
        self.assertEqual(digest(v7.measure_turn_frames(fixture.route, fixture.native)['reading']),
                         '5d2b6f8d1bf5363349635ebc453371fddbd97c88a142ef9484894aae0756b00e')
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
        for kind, value, expected_packet, expected_prompt, expected_without_plan in (
            ('reading', packet, '009cfbd13dfc38e448b577aea7aab531234a1ff8ffaab4509fae689fd414f49f',
             '45a806cdb6b91eed5ed0776364abec5063cdbf94c5821f9677e999cc0cc05d72',
             '7c92fc4865879fa862e2457584a85ed254d566b92046941468d99ecc9082746e'),
            ('final', final, '8b8d6484d26bb8505becb7122d3c6db1ec19a7fda252f8f1a8b8c54b0590bc1a',
             '914e559bee9bffe4897dec8d7349f9931ec29f47397ad79d2299b832298c21c0',
             'dcd8258c3094e36e171c94bcfb046b494e3936b624695a99db364585f76eeb75')):
            with self.subTest(kind=kind):
                self.assertNotIn('output_refusal', value)
                self.assertEqual(value['digest'], expected_packet)
                normalized = copy.deepcopy(value)
                normalized.pop('digest')
                normalized.get('reading_turn', normalized.get('reading_final')).pop('plan_digest')
                self.assertEqual(hashlib.sha256(v7.serialize_record(normalized)).hexdigest(), expected_without_plan)
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
        notice = self.build_bounded_notice(fixture.artifacts)
        self.assertEqual(len(json.dumps(notice, ensure_ascii=False, sort_keys=True)), 45000)
        self.assertTrue(all(len(json.dumps(entry, sort_keys=True)) <= 6000 for entry in notice['refused']))
        self.assertLessEqual(len(json.dumps(notice['refused_part'], sort_keys=True)), 20000)
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
