"""V7 finals in pages, with the release switch enabled only in these tests."""

import copy
import json
import unittest
from unittest.mock import patch

from research_harness import openness_reading_v7 as v7, review_route_capacity as capacity
from test_openness_reading_v7_turns import MemoryArtifacts, build_native
from v7_capacity_fixtures import build_route


class FinalAllocationTests(unittest.TestCase):
    def setUp(self):
        self.artifacts = MemoryArtifacts()
        self.route = build_route('codex_cli_v1', 'gpt-6-astra')
        switch = patch.object(v7, 'V7_FINAL_FORMS', ('request', 'pages'))
        switch.start()
        self.addCleanup(switch.stop)

    def admit(self, count):
        with patch.object(v7, 'build_inventory', return_value=v7.plan_inventory([], {})):
            return v7.check_v7_admission({}, self.artifacts, self.route, build_native(count))

    def test_unmeasured_nine_targets_allow_eight_and_admit_pages(self):
        with patch.object(capacity, 'read_profile_priors', return_value=[]):
            result = self.admit(9)
        self.assertEqual(result['state'], 'record')
        self.assertEqual(result['final_form'], 'pages')
        plan = result['capacity_plan']
        floor = plan['task']['final']['levels'][-1]
        self.assertEqual(floor['target_allowance'], 8)
        self.assertEqual(plan['allocation']['final_form'], 'pages')

    def test_eight_targets_preserve_request_plan_bytes(self):
        with patch.object(v7, 'V7_FINAL_FORMS', ('request',)):
            old = self.admit(8)
        current = self.admit(8)
        self.assertEqual(current['state'], 'record')
        self.assertEqual(current['final_form'], 'request')
        self.assertEqual(current['capacity_plan'], old['capacity_plan'])
        self.assertEqual(current['capacity_members']['capacity_plan'], old['capacity_members']['capacity_plan'])

    def test_five_thousand_targets_are_admitted(self):
        result = self.admit(5000)
        self.assertEqual(result['state'], 'record')
        self.assertEqual(result['final_form'], 'pages')
        self.assertGreater(result['forecast']['by_kind']['final']['pages'], 1)


from openness_reading_fixtures import OpennessReadingCase
import test_openness_reading_v7_turns as turn_fixtures
from research_harness import openness_reading as reading, review_protocol as protocol, review_composite
from research_harness.problem_openness import serialize_record
from research_harness.evidence import digest


class FinalPagesSupport(OpennessReadingCase):
    capacity_probe_tokens = 487900


    reading_protocol = None


    route_model = 'gpt-6-astra'


    route_output_tokens = 128000


    text_sizes = (1000,)


    use_released_chain_default = True


    build_v7_answer = turn_fixtures.V7CommandTests.build_v7_answer


    def setUp(self):
        super().setUp()
        enabled = patch.object(v7, 'V7_FINAL_FORMS', ('request', 'pages'))
        enabled.start()
        self.addCleanup(enabled.stop)


    def export(self, priority=(), name='openness-packet', assignment_id='turn-1'):
        from research_harness.problem_openness import export_openness
        minimum = dict(searches=[dict(formulation=row['question'], search_id=row['id']) for row in self.searches],
                       settling_work_id=None, duplicate_resolutions=[])
        if priority:
            minimum['text_priority'] = list(priority)
        return export_openness(self.store, dict(assignment_id=assignment_id, reviewer_id='openness-reviewer',
            minimum=minimum), self.root / name)['native_packet']

    def export_targets(self, count=9, assignment_id='turn-1'):
        labels = ['Synthetic neighbour %d' % n for n in range(1, count)]
        self.selected = self.select_statement(identifier='statement-' + assignment_id, candidate_id='candidate-1', neighbours=labels)
        from test_research_problem_openness_minimum import ProblemOpennessMinimumTests
        known = {row['question'] for row in self.searches}
        for label in labels:
            if label not in known:
                self.searches.append(ProblemOpennessMinimumTests.search(self, formulation=label))
        reference = self.export(assignment_id=assignment_id, name=assignment_id + '-export')
        native = json.loads(self.artifacts.read(reference))
        return self.artifacts.put(serialize_record(native), 'application/json')


    def finish_pages(self, native, first='turn-1', before_answer=None, assign=True):
        if assign:
            self.assign(first, native)
        current, seen = first, []
        for number in range(1, 100):
            packet = self.read_packet(current)
            seen.append(packet)
            if 'reading_final' in packet and 'closing' not in packet['reading_final']:
                binding = packet['reading_final']
                material = {key: binding[key] for key in v7.FINAL_MATERIAL_FIELDS}
                route = self.store.snapshot()['records']['review_route']['route']
                self.assertLessEqual(v7._measure_final_material(route, material), binding['capacity']['material_allowance'])
            override = before_answer(current, packet) if before_answer is not None else None
            if 'reading_turn' in packet:
                output = self.build_v7_answer(packet)
            elif 'reading_combine' in packet:
                binding = packet['reading_combine']
                output = dict(block=binding['block']['handle'], summary='Condensed supporting entries.',
                              refs=[binding['block']['entries'][0].get('handle', binding['block']['entries'][0].get('block'))])
            elif 'closing' in packet['reading_final']:
                output = dict({key: 'Assessed the supplied subject.' for key in
                    ('statement', 'assumptions', 'scope', 'dependencies', 'policy')},
                    outcome='unresolved', reason='A remaining gap.', grounds=[])
            else:
                output = dict(targets=[dict(formulation=target, coverage='Required sources were read.',
                    grounds=[dict(kind='own_argument', text='The recorded argument requires further checking.')], sentence='The target remains unresolved.')
                    for target in packet['reading_final']['page']['targets']])
            if override is not None:
                output = override
            receipt = self.invoke(current, output)
            self.assertNotIn('output_refusal', receipt, receipt)
            if 'closing' in packet.get('reading_final', {}) and output.get('stage') not in ('reading', 'material'):
                return current, seen
            following = 'next-%d' % len(self.store.snapshot()['records']['review_assignment'])
            self.assign(following, native, prior=current)
            current = following
        self.fail('The chain must reach its closing finding.')


    def start_final(self, native):
        current = 'turn-1'
        self.assign(current, native)
        for number in range(30):
            packet = self.read_packet(current)
            if 'reading_final' in packet:
                return current, packet
            self.invoke(current, self.build_v7_answer(packet))
            following = 'read-%d' % number
            self.assign(following, native, prior=current)
            current = following
        self.fail('The fixture must finish reading.')


    def assert_recorded_composite(self, final):
        records = self.store.snapshot()['records']
        assignment = records['review_assignment'][final]
        self.assertTrue(reading.verify_chain(records, self.artifacts, assignment))
        output, composite = review_composite.read_final_output(records, self.artifacts, assignment)
        self.assertIsNotNone(composite)
        self.assertEqual(output, review_composite.authenticate_composite(records, self.artifacts, composite))
        result = self.mutate(self.load_problem_module().record_openness, dict(assignment_id=final))['result']
        self.assertEqual(result['outcome'], 'unresolved')
        self.assertTrue(result['reading_turns'])
        self.assertEqual(result['reading_turns'][-1]['assignment_id'], final)
        return composite



class FinalPagesCommandTests(FinalPagesSupport):
    def test_a5_q7_nine_targets_record_one_authenticated_composite(self):
        native = self.export_targets()
        final, packets = self.finish_pages(native)
        from research_harness import openness_final_pages, review_prompt_paragraphs as paragraphs
        self.assertEqual(sum('page_plan_digest' in p.get('reading_final', {}) and 'closing' not in p['reading_final'] for p in packets), 2)
        records = self.store.snapshot()['records']
        members = reading._list_chain(records, records['review_assignment'][final])
        plan = capacity.read_chain_capacity_plan(self.artifacts, members[0])
        route = records['review_route']['route']
        self.assertEqual(len(members), len(packets))
        self.assertEqual([reading.find_stage(packet) for packet in packets],
                         ['reading', 'reading', 'page', 'page', 'closing'])
        for member, packet in zip(members, packets):
            with self.subTest(assignment=member['id']):
                request, sent = self.read_sent_packet(member['id'])
                self.assertEqual(request['instructions'], member['prompt']['text'])
                self.assertIn(paragraphs.NOTES_COST_PROMPT, request['instructions'])
                self.assertIn(paragraphs.LIST_ROWS_PROMPT, request['instructions'])
                self.assertTrue(v7.matches_turn_prompt(member, packet))
                binding = openness_final_pages.read_binding(packet)
                for field in ('notes_cost', 'lists'):
                    self.assertIn(field, binding)
                    self.assertEqual(openness_final_pages.read_binding(sent)[field], binding[field])
                kind = member['capacity_plan_kind']
                report = capacity.describe_plan_preflight({}, route, request, plan, kind)
                self.assertTrue(report['plan_comparison']['fits'], report['plan_comparison'])
        composite = next(iter(records['review_composite'].values()))
        self.assertEqual(len(review_composite.authenticate_composite(records, self.artifacts, composite)['findings']['openness']['coverage']), 9)
        self.assertTrue(reading.verify_chain(records, self.artifacts, records['review_assignment'][final]))
        result = self.mutate(self.load_problem_module().record_openness, dict(assignment_id=final))['result']
        self.assertEqual(result['outcome'], 'unresolved')
        self.assertEqual(result['composite'],dict(id=composite['id'],digest=digest(composite)))
        self.assertEqual(result['reading_turns'][-1]['assignment_id'],final)
        from ci.reviewer_program_guard import use_absent_programs
        from research_harness.record_check import check_record
        # The record check lists the path of the Codex program beside each runtime read. The test states that
        # the machine has none.
        use_absent_programs(self)
        checked = check_record(self.root, isolate=False)
        self.assertTrue(checked['ready'], checked)


    def test_a14_forty_targets_reach_recorded_finding(self):
        native = self.export_targets(40)
        final, packets = self.finish_pages(native)
        composite = self.assert_recorded_composite(final)
        self.assertEqual(sum(len(row['targets']) for row in composite['pages']), 40)
        self.assertLessEqual(len(composite['pages'][0]['targets']), 8)


    def test_closing_material_two_parts_expand_m_handle_and_authenticate(self):
        native = self.export_targets()
        parts = []
        def answer(current, packet):
            if 'closing' not in packet.get('reading_final', {}):
                return None
            if len(parts) < 2:
                parts.append(current)
                return dict(stage='material', title='Independent argument', part=len(parts), text='Argument part %d.' % len(parts), more=len(parts) == 1)
            self.assertEqual(packet['reading_final']['material'][0]['handle'], 'm1')
            return dict({key: 'Assessed.' for key in ('statement', 'assumptions', 'scope', 'dependencies', 'policy')},
                outcome='unresolved', reason='The independent argument has a gap.',
                grounds=[dict(kind='own_argument', text='The stored argument explains the gap.', refs=[dict(ref='m1')])])
        final, _ = self.finish_pages(native, before_answer=answer)
        composite = self.assert_recorded_composite(final)
        output = review_composite.authenticate_composite(self.store.snapshot()['records'], self.artifacts, composite)
        reference = output['findings']['openness']['grounds'][-1]['refs'][0]
        self.assertIn(b'Argument part 1.', self.artifacts.read(reference))
        self.assertIn(b'Argument part 2.', self.artifacts.read(reference))


    def test_page_material_resumes_the_same_targets_and_authenticates(self):
        native = self.export_targets()
        requested = []
        def answer(current, packet):
            binding = packet.get('reading_final', {})
            if 'page' in binding and not requested:
                requested.append(list(binding['page']['targets']))
                return dict(stage='material', title='Target argument', part=1, text='An argument on this target.', more=False)
            if 'page' in binding and len(requested) == 1:
                self.assertEqual(binding['page']['targets'], requested[0])
                requested.append(current)
                return dict(targets=[dict(formulation=target, coverage='Read.', sentence='A gap remains.',
                    grounds=[dict(kind='own_argument', text='The argument holds the detail.', refs=[dict(ref='m1')])])
                    for target in binding['page']['targets']])
            return None
        final, _ = self.finish_pages(native, before_answer=answer)
        self.assert_recorded_composite(final)


    def test_page_and_cross_page_contradictions_are_refused_at_their_owner(self):
        native = self.export_targets()
        head, packet = self.start_final(native)
        target = packet['reading_final']['page']['targets'][0]
        bad = dict(targets=[dict(formulation=handle, coverage='c' * 801, sentence='Too long.', grounds=[])
                            for handle in packet['reading_final']['page']['targets']])
        receipt = self.invoke(head, bad)
        self.assertEqual(receipt['output_refusal']['check'], 'openness.finding')
        # The ordinary refused-page replacement belongs to the next lane. Use
        # a fresh fixture chain to test the composed scientific refusal here.
        def answer(current, supplied):
            if 'closing' in supplied.get('reading_final', {}):
                return dict({key: 'Assessed.' for key in ('statement', 'assumptions', 'scope', 'dependencies', 'policy')},
                    outcome='supported_open', reason='Claimed resolved despite a page ground.', grounds=[])
            return None
        with self.assertRaises(AssertionError):
            self.finish_pages(self.export_targets(assignment_id='other'), first='other', before_answer=answer)
        records = self.store.snapshot()['records']
        refusal = max(records['review_output_refusal'].values(), key=lambda row: row['recorded_revision'])
        self.assertEqual(refusal['check'], 'openness.finding')
        closing = records['review_assignment'][records['review_attempt'][refusal['id']]['assignment_id']]
        self.assertIn('closing', self.read_packet(closing['id'])['reading_final'])


    def test_page_passage_request_keeps_excerpt_and_resumes(self):
        native = self.export_targets()
        requested = []
        def answer(current, packet):
            binding = packet.get('reading_final', {})
            if 'page' not in binding:
                return None
            if not requested:
                source = binding['minimum']['gaps_unit']
                requested.append((binding['final_turn'], binding['page']['targets']))
                response = self.build_v7_answer(packet)
                response['passage_requests'] = [dict(id='gaps', unit=source, anchor=None, characters=1000)]
                return response
            if len(requested) == 1:
                self.assertEqual(binding['final_turn'], requested[0][0])
                self.assertEqual(binding['page']['targets'], requested[0][1])
                self.assertTrue(packet['evidence'])
                response = self.build_v7_answer(packet)
                source = packet['evidence'][0]
                response['excerpts'] = [dict(unit=source['handle'], text=source['content'][:10], purpose='Relevant gap record.',
                                             targets=[binding['page']['targets'][0]])]
                response['passage_requests'] = [dict(id='again', unit=source['handle'], anchor=None, characters=1000)]
                requested.append(True)
                return response
            if len(requested) == 2:
                assignment = self.store.snapshot()['records']['review_assignment'][current]
                state = json.loads(self.artifacts.read(assignment['reading_chain']['state']))
                self.assertEqual(state['entries'][-1]['status'], 'verified')
                requested.append(True)
            return None
        final, _ = self.finish_pages(native, before_answer=answer)
        self.assert_recorded_composite(final)


    def test_verify_refuses_changed_plan_target_output_missing_page_and_currency(self):
        from research_harness.errors import ResearchError
        native = self.export_targets()
        final, _ = self.finish_pages(native)
        original = self.store.snapshot()['records']
        composite = next(iter(original['review_composite'].values()))
        page_id = composite['pages'][0]['assignment_id']
        for change in ('plan', 'target', 'output', 'missing', 'currency'):
            with self.subTest(change=change):
                records = copy.deepcopy(original)
                member = records['review_assignment'][page_id]
                if change == 'plan':
                    reference = member['reading_chain']['final_page_plan']
                    value = json.loads(self.artifacts.read(reference['artifact']))
                    value['turns'][0]['targets'].append('t999')
                    reference['artifact'] = self.artifacts.put(serialize_record(value), 'application/json')
                elif change == 'target':
                    value = json.loads(self.artifacts.read(member['packet']))
                    value['reading_final']['page']['targets'] = ['t9']
                    member['packet'] = self.artifacts.put(serialize_record(value), 'application/json')
                elif change == 'output':
                    records['review_attempt'][composite['pages'][0]['attempt_id']]['output'] = self.artifacts.put(b'{}', 'application/json')
                elif change == 'missing':
                    del records['review_attempt'][composite['pages'][0]['attempt_id']]
                else:
                    records['review_composite'][composite['id']]['currency']['packet_digest'] = '0' * 64
                with self.assertRaises(ResearchError):
                    reading.verify_chain(records, self.artifacts, records['review_assignment'][final])


    def test_new_templates_match_packets_and_no_transmitted_handle_table(self):
        native = self.export_targets()
        final, packets = self.finish_pages(native)
        records = self.store.snapshot()['records']
        for member in reading._list_chain(records, records['review_assignment'][final]):
            packet = self.read_packet(member['id'])
            self.assertTrue(v7.matches_turn_prompt(member, packet))
            self.assertNotIn('handle_table', packet)
            self.assertNotIn('handle_table', self.read_sent_packet(member['id']))
            from ci.capacity_packet_inventory import read_packet_registry, validate_packet
            self.assertEqual(validate_packet('native_math', packet, read_packet_registry()), [])




class CondensingCommandTests(FinalPagesSupport):
    # Only the new condensing scenarios live here; inherited command scenarios
    # use the same real transport capture and exercise the high starting level.
    text_sizes = (80000, 80000)

    def setUp(self):
        super().setUp()
        original = capacity.read_output_profile
        def high_start(records, route, **kwargs):
            profile = original(records, route, **kwargs)
            row = capacity._create_profile_row('final', None)
            row.update(attempts=10, completed_attempts=10, largest_thinking_tokens=100,
                       largest_output_tokens=100)
            profile['rows'] = [item for item in profile['rows'] if item['kind'] != 'final'] + [row]
            return profile
        prior = patch.object(capacity, 'read_output_profile', side_effect=high_start)
        prior.start()
        self.addCleanup(prior.stop)

    def invoke_with_ratio(self, identifier, output, ratio):
        import math
        from codex_route_fixtures import build_codex_response
        def respond(route, request, credential):
            response = build_codex_response(route, request, output)
            size = capacity.measure_request(route, request)['text_characters']
            response['usage']['input_tokens'] = int(math.ceil(size * (1 - capacity.TOKEN_RATIO_MARGIN) / ratio))
            return response
        with patch('research_harness.review_transport.send_request', side_effect=respond):
            return self.mutate(protocol.invoke_assignment, dict(id='attempt-' + identifier, assignment_id=identifier), credential='test-only')

    def test_stored_request_plan_condenses_at_measured_ratio_and_ignores_new_priors(self):
        native = self.export_targets(1)
        with patch.object(v7, 'V7_FINAL_FORMS', ('request',)):
            self.assign('turn-1', native)
        original = self.store.snapshot()['records']['review_assignment']['turn-1']
        plan_ref = copy.deepcopy(original['capacity_plan'])
        plan = capacity.read_chain_capacity_plan(self.artifacts, original)
        self.assertNotIn('combine', plan['task'])
        self.assertEqual(plan['task']['final']['material_allowance'], 480000)
        current = 'turn-1'
        for index in range(20):
            packet = self.read_packet(current)
            binding = packet['reading_turn']
            output = self.build_v7_answer(packet)
            allowance = binding['allowances']['targets'][0]['remaining']
            for source in packet['evidence']:
                if any(row['handle'] == source['handle'] and row['kind'] == 'fulltext' for row in binding['plan']['now']):
                    for offset in range(0, min(len(source['content']), 30000), 6000):
                        if allowance < 7200:
                            break
                        output['excerpts'].append(dict(unit=source['handle'], text=source['content'][offset:offset + 6000],
                            purpose='Relevant supporting material.', targets=['t1']))
                        allowance -= 7200
            delivered = sum(row['characters'] for row in binding['plan']['now'])
            last = delivered >= binding['plan']['remaining']['characters']
            if last:
                # Leave 18,000 material characters beside the pinned frame and notice.
                fixed = plan['frame']['final']['bound'] + plan['task']['final']['notice_characters']
                ratio = (fixed + 18000) / plan['limits']['input_tokens']['value']
                self.invoke_with_ratio(current, output, ratio)
            else:
                self.invoke(current, output)
            following = 'dense-%d' % index
            self.assign(following, native, prior=current)
            current = following
            if last:
                break
        first_combine = self.read_packet(current)
        self.assertIn('reading_combine', first_combine)
        from ci.capacity_packet_inventory import read_packet_registry, validate_packet
        self.assertEqual(validate_packet('native_math', first_combine, read_packet_registry()), [])
        records = self.store.snapshot()['records']
        assigned = records['review_assignment'][current]
        self.assertEqual(assigned['capacity_plan_kind'], 'final')
        self.assertEqual(assigned['capacity_plan'], plan_ref)
        binding = first_combine['reading_combine']
        self.assertLessEqual(binding['block']['characters'], binding['capacity']['material_allowance'])
        self.assertLessEqual(binding['capacity']['answer_bound'], binding['block']['characters'] // 4)
        old_digests = {kind: {key: digest(value) for key, value in records.get(kind, {}).items()}
                       for kind in ('review_assignment', 'review_attempt')}
        # Use an actual citation in a combination through capture and authentication.
        cited, requested = False, []
        def cite(current, packet):
            nonlocal cited
            if 'reading_combine' in packet:
                block = packet['reading_combine']['block']
                cited = True
                return dict(block=block['handle'], summary='Condensed material.', refs=[dict(ref=block['entries'][0].get('handle', block['entries'][0].get('block')))])
            if 'page' in packet.get('reading_final', {}) and not requested:
                requested.append(True)
                response = self.build_v7_answer(packet)
                response['passage_requests'] = [dict(id='block-entries', kind='list_page',
                    list=first_combine['reading_combine']['block']['handle'], after=None),
                    dict(id='original-entry', unit=first_combine['reading_combine']['block']['entries'][0]['handle'],
                         anchor=None, characters=1000)]
                return response
            if len(requested) == 1:
                kinds = {row['kind'] for row in packet['reading_final']['plan']['now']}
                self.assertIn('list_page', kinds)
                self.assertIn('final_entry', kinds)
                requested.append(True)
            return None
        final, packets = self.finish_pages(native, first=current, before_answer=cite, assign=False)
        self.assertTrue(cited)
        self.assertGreater(sum('reading_combine' in packet for packet in packets), 1)
        page = next(packet['reading_final'] for packet in packets if 'page' in packet.get('reading_final', {}))
        self.assertEqual(page['notes'], 'The delivered units have been read.')
        self.assert_recorded_composite(final)
        after = self.store.snapshot()['records']
        for kind, rows in old_digests.items():
            for key, expected in rows.items():
                self.assertEqual(digest(after[kind][key]), expected)
        with patch.object(capacity, 'read_output_profile', side_effect=AssertionError('current prior entered replay')):
            self.assertTrue(reading.verify_chain(after, self.artifacts, after['review_assignment'][final]))


class DensePendingCommandTests(FinalPagesSupport):
    def test_pending_request_ratio_successor_condenses_closing_rows(self):
        native = self.export_targets(70)
        head, packet = self.start_final(native)
        assigned = self.store.snapshot()['records']['review_assignment'][head]
        plan = capacity.read_chain_capacity_plan(self.artifacts, assigned)
        response = self.build_v7_answer(packet)
        response['passage_requests'] = [dict(id='pending', unit=packet['reading_final']['minimum']['gaps_unit'], anchor=None, characters=100)]
        fixed = plan['frame']['final']['bound'] + plan['task']['final']['notice_characters']
        ratio = (fixed + 20000) / plan['limits']['input_tokens']['value']
        CondensingCommandTests.invoke_with_ratio(self, head, response, ratio)
        self.assign('dense-pending', native, prior=head)
        resumed = self.read_packet('dense-pending')
        self.assertEqual(resumed['reading_final']['page']['targets'], packet['reading_final']['page']['targets'])
        self.assertTrue(resumed['evidence'])
        record = self.store.snapshot()['records']['review_assignment']['dense-pending']
        page_plan = json.loads(self.artifacts.read(record['reading_chain']['final_page_plan']['artifact']))
        self.assertIn(page_plan['cause']['kind'], ('ratio', 'level'))
        self.assertIn(packet['reading_final']['final_turn'], page_plan['cause']['pending'])
        final, packets = self.finish_pages(native, first='dense-pending', assign=False)
        composite = self.assert_recorded_composite(final)
        self.assertEqual(sum(len(row['targets']) for row in composite['pages']), 70)
        self.assertTrue(any('reading_combine' in item for item in packets))

    def test_pending_multipart_argument_survives_ratio_successor(self):
        native = self.export_targets()
        head, packet = self.start_final(native)
        assigned = self.store.snapshot()['records']['review_assignment'][head]
        plan = capacity.read_chain_capacity_plan(self.artifacts, assigned)
        fixed = plan['frame']['final']['bound'] + plan['task']['final']['notice_characters']
        ratio = (fixed + 45000) / plan['limits']['input_tokens']['value']
        CondensingCommandTests.invoke_with_ratio(self, head, dict(stage='material', title='Pending argument', part=1,
            text='First part of the argument.', more=True), ratio)
        self.assign('argument-pending', native, prior=head)
        resumed = self.read_packet('argument-pending')
        self.assertEqual(resumed['reading_final']['page']['targets'], packet['reading_final']['page']['targets'])
        self.assertEqual(resumed['reading_final']['material_request']['part'], 2)
        self.invoke('argument-pending', dict(stage='material', title='Pending argument', part=2,
            text='Last part of the argument.', more=False))
        self.assign('argument-complete', native, prior='argument-pending')
        final, _ = self.finish_pages(native, first='argument-complete', assign=False)
        self.assert_recorded_composite(final)

    def test_q8_two_thousand_forecast_gaps_are_condensed_before_target_page(self):
        native_ref = self.export_targets(1)
        native = json.loads(self.artifacts.read(native_ref))
        context = native['scientific_context']
        context['minimum_gaps'] = [dict(formulation=context['openness_minimum']['statement'],
            condition='reading_missing', outcomes=['supported_open', 'already_solved'], details=dict(index=n)) for n in range(2000)]
        native_ref = self.artifacts.put(serialize_record(native), 'application/json')
        final, packets = self.finish_pages(native_ref)
        self.assertTrue(any('reading_combine' in packet for packet in packets))
        self.assert_recorded_composite(final)


class UnreadPagesTests(FinalPagesSupport):
    text_sizes = (50000, 50000, 50000)

    def test_unread_pages_close_with_x1_and_record_unresolved(self):
        import test_openness_unread_final as unread_fixtures
        native = self.export_targets()
        self.assign('turn-1', native)
        self.invoke('turn-1', self.build_v7_answer(self.read_packet('turn-1')))
        self.assign('refused', native, prior='turn-1')
        self.refuse_provider = unread_fixtures.UnreadFinalSupport.refuse_provider.__get__(self)
        native, assigned, packet = unread_fixtures.UnreadFinalSupport.exhaust_head(self, native, 'refused')
        self.assertIn('unread_summary', packet['reading_final'])
        self.assertNotIn('unread', packet['reading_final'])
        def answer(current, supplied):
            if 'closing' in supplied.get('reading_final', {}):
                self.assertIn('unread', supplied['reading_final'])
                bad = dict({key: 'Assessed.' for key in ('statement', 'assumptions', 'scope', 'dependencies', 'policy')},
                    outcome='supported_open', reason='Claimed resolved over unread ranges.', grounds=[])
                refused = self.invoke(current, bad, attempt='refused-' + current)
                self.assertEqual(refused['output_refusal']['check'], 'openness.finding')
                self.assertEqual(refused['output_refusal']['details']['rule'], 'unread_outcome')
                return dict({key: 'Assessed.' for key in ('statement', 'assumptions', 'scope', 'dependencies', 'policy')},
                    outcome='unresolved', reason='Required ranges remain unread.', grounds=[dict(kind='packet_defect',
                        text='The unread set prevents a resolved finding.', refs=[dict(ref='x1')])])
            return None
        final, _ = self.finish_pages(native, first=assigned['id'], before_answer=answer, assign=False)
        self.assert_recorded_composite(final)
        stored = self.store.snapshot()['records']['problem_openness'][final]
        self.assertTrue(stored['unread']['rows'])


class IncidentFinalPagesTests(FinalPagesSupport):
    text_sizes = (150000,) * 29

    def record_searches(self):
        from test_research_problem_openness_minimum import ProblemOpennessMinimumTests
        return [ProblemOpennessMinimumTests.search(self, self.versions[start:start + 10])
                for start in range(0, len(self.versions), 10)]

    def test_t4_fifty_seven_carried_turns_reach_a_recorded_paged_finding(self):
        # Synthetic shape: 57 prior readings, retained entries, and unchanged
        # large full texts. No live scientific text or record names are copied.
        from codex_route_fixtures import build_codex_response
        native = self.export_targets()
        from test_research_problem_openness_minimum import ProblemOpennessMinimumTests
        for index in range(20 - len(self.searches)):
            self.searches.append(ProblemOpennessMinimumTests.search(self, self.versions[:10]))
        native = self.export(assignment_id='turn-1', name='incident-export')
        current = 'turn-1'
        self.assign(current, native)
        for number in range(57):
            packet = self.read_packet(current)
            self.assertTrue('reading_turn' in packet, 'Reading ended at turn %d' % number)
            output = self.build_v7_answer(packet)
            binding = packet['reading_turn']
            row = next(row for row in packet['evidence'] if len(row['content']) >= 500)
            for index in range(19 if number < 37 else 18):
                target = 't%d' % ((number + index) % 9 + 1)
                output['excerpts'].append(dict(unit=row['handle'], text=row['content'][:500],
                    purpose='A retained scientific observation.', targets=[target]))
            def respond(route, request, credential):
                response = build_codex_response(route, request, output)
                response['usage']['output_tokens'] = 64000
                return response
            with patch('research_harness.review_transport.send_request', side_effect=respond):
                self.mutate(protocol.invoke_assignment, dict(id='attempt-' + current, assignment_id=current), credential='test-only')
            if number != 56:
                following = 'old-%d' % (number + 2)
                self.assign(following, native, prior=current)
                current = following
        before = self.store.snapshot()['records']
        earlier = {key: digest(row) for key, row in before['review_assignment'].items()}
        successor = self.export(assignment_id='carried', name='carried-export')
        first = self.assign('carried', successor)
        selected = json.loads(self.artifacts.read(first['carried_selection']['artifact']))
        self.assertEqual(len(selected['turns']), 57)
        final, packets = self.finish_pages(successor, first='carried', assign=False)
        composite = self.assert_recorded_composite(final)
        records = self.store.snapshot()['records']
        for key, expected in earlier.items():
            self.assertEqual(digest(records['review_assignment'][key]), expected)
        inventory = json.loads(self.artifacts.read(first['reading_chain']['inventory']))
        carried_units = sum(row['kind'] == 'carried_reading' for row in inventory['units'])
        self.assertGreater(carried_units, 0)
        self.assertGreater(len(composite['pages']), 1)
        from ci.reviewer_program_guard import use_absent_programs
        from research_harness.record_check import check_record
        # The record check lists the path of the Codex program beside each runtime read. The test states that
        # the machine has none.
        use_absent_programs(self)
        checked = check_record(self.root, isolate=False)
        self.assertTrue(checked['ready'], checked)
        print(json.dumps(dict(incident_final_pages=dict(pages=len(composite['pages']),
            condensing_turns=len(composite['closing']['condensing']), carried_reading=carried_units,
            carried_turns=len(selected['turns'])))))


class TargetAllowanceTests(unittest.TestCase):
    def build_plan(self, count, answer_room=None):
        route = build_route(effort='xhigh')
        profile = capacity.read_output_profile({}, route, priors=[])
        if answer_room is not None:
            row = capacity._create_profile_row('final', None)
            thinking = int((128000 - answer_room / 1.8) / 1.15) + 1
            row.update(attempts=10, completed_attempts=10, largest_thinking_tokens=thinking, largest_output_tokens=128000)
            profile['rows'] = [row]
        plan = capacity.plan_chain_capacity(route, profile, frames=dict(reading=dict(frame=3000),final=dict(frame=3000)),
            inventory=[], targets=['t%d' % n for n in range(1, count + 1)], allowance_rows_bound=3000, final_pages=True)
        return route, profile, plan

    def test_usable_row_below_one_target_minimum_pins_one_and_three_pages(self):
        route, profile, plan = self.build_plan(3, 5000)
        self.assertEqual(plan['task']['final']['target_allowance'], 1)
        self.assertTrue(all(row['target_allowance'] == 1 for row in plan['task']['final']['levels']))
        self.assertEqual(plan['task']['final']['smallest_answer_bound'], 7500)
        self.assertEqual(plan['allocation']['final_form'], 'pages')
        forecast = capacity.describe_capacity_forecast(plan, route, profile)['by_kind']['final']
        self.assertEqual((forecast['pages'], forecast['closing_pages']), (3, 1))

    def test_measured_answer_room_holds_twenty_of_forty_targets(self):
        route, profile, plan = self.build_plan(40, 36002)
        self.assertEqual(plan['task']['final']['target_allowance'], 20)
        self.assertEqual([row['target_allowance'] for row in plan['task']['final']['levels']], [20, 20, 17, 8])
        self.assertEqual(plan['allocation']['final_form'], 'pages')

    def test_output_limit_below_one_target_answer_refuses_first_plan(self):
        from research_harness.errors import ResearchError
        route = build_route('codex_cli_v1', 'gpt-6-astra', output=1000)
        with self.assertRaises(ResearchError) as refused:
            capacity.plan_chain_capacity(route, capacity.read_output_profile({}, route, priors=[]),
                frames=dict(final=dict(frame=1000)), inventory=[], targets=['t1'], allowance_rows_bound=1000, final_pages=True)
        self.assertEqual(refused.exception.code, 'review_route_capacity_below_floor')

    def test_exact_answer_count_and_input_count_without_a_usable_row(self):
        route, profile, plan = self.build_plan(9)
        self.assertEqual([row['target_allowance'] for row in plan['task']['final']['levels']], [9, 9, 9, 8])
        self.assertEqual(plan['task']['final']['target_allowance'], 8)

    def test_request_packets_and_prompts_preserve_one_eight_and_seventy_target_bytes(self):
        from research_harness import review_final_pages
        artifacts = MemoryArtifacts()
        for count in (1, 8, 70):
            route = (build_route(effort='xhigh') if count == 70 else build_route('codex_cli_v1', 'gpt-6-astra'))
            profile = capacity.read_output_profile({}, route, priors=[])
            if count == 70:
                row = capacity._create_profile_row('final', None)
                row.update(attempts=10, completed_attempts=10, largest_thinking_tokens=0, largest_output_tokens=100)
                profile['rows'] = [row]
            kwargs = dict(frames=dict(reading=dict(frame=3000), final=dict(frame=3000)), inventory=[],
                          targets=['t%d' % n for n in range(1, count + 1)], allowance_rows_bound=3000)
            before = capacity.plan_chain_capacity(route, profile, **kwargs)
            after = capacity.plan_chain_capacity(route, profile, final_pages=True, **kwargs)
            self.assertEqual(digest(before), digest(after))
            self.assertEqual(serialize_record(before), serialize_record(after))
            native = build_native(count)
            native['scientific_context']['minimum_gaps'] = []
            gap_reference = artifacts.put(b'[]')
            base = v7.plan_inventory([dict(kind='record', record_kind='minimum_gaps', record_id='minimum_gaps', artifact=gap_reference)],
                {gap_reference['sha256']: b'[]'})
            built = v7.prepare_inventory(base, native, route)
            inventory = built['inventory']
            for content in built['bodies'].values():
                artifacts.put(content)
            state = v7.build_chain_state(inventory, [], route=route, plan=after)['state']
            logical = dict(protocol=protocol.PROTOCOL, role='native_math', reviewer_id='fixture', dossier_id=None, native=native)
            old_packet = v7.build_turn_packet(artifacts, route, logical, 'chain', 2, inventory, state, before,
                                            capacity.derive_plan_task_limits(before,'final'), 'final')
            new_packet = v7.build_turn_packet(artifacts, route, logical, 'chain', 2, inventory, state, after,
                                            capacity.derive_plan_task_limits(after,'final'), 'final')
            self.assertEqual(serialize_record(old_packet), serialize_record(new_packet))
            self.assertEqual(protocol.canonical_prompt('native_math', reading_protocol=v7.PROTOCOL, reading_stage='final'),
                             protocol.canonical_prompt('native_math', reading_protocol=v7.PROTOCOL, reading_stage=reading.find_stage(new_packet)))


class PagePlannerIntegrationTests(unittest.TestCase):
    def test_condensed_pool_has_a_page_and_a_recordable_closing(self):
        from research_harness import review_final_pages as pages, review_condensing
        inputs = dict(page_room=60000, target_allowance=1, target_minimum=6700, notes=1000,
            pool=[dict(handle='p%d' % n, characters=20000) for n in range(4)],
            targets=[dict(handle='t1', entries=[], rows=[])], closing_room=60000, row_characters=360,
            condense=review_condensing.plan_condensing)
        turns = pages.plan_final_turns(**inputs)
        self.assertEqual([row['kind'] for row in turns][-2:], ['page', 'closing'])

    def test_preserved_pending_page_with_new_entries_is_condensed_at_current_room(self):
        from research_harness import review_final_pages as pages, review_condensing
        target = dict(handle='t1', entries=[dict(handle='e1', characters=1000)], rows=[])
        inputs = dict(page_room=60000, target_allowance=1, target_minimum=6700, notes=1000,
            pool=[], targets=[target, dict(handle='t2', entries=[],rows=[])], closing_room=60000,
            row_characters=360,condense=review_condensing.plan_condensing)
        initial = pages.plan_final_turns(**inputs)
        fresh = copy.deepcopy(inputs)
        fresh['targets'][0]['entries'] += [dict(handle='e%d' % n, characters=6000) for n in range(2, 9)]
        prepared = pages.prepare_final_turn(initial, 1, 20000, 1, 6000, review_condensing.plan_condensing,
            pending=[1], completed=[], inputs=fresh)
        self.assertTrue(any(row['kind'] == 'combine' for row in prepared))
        self.assertTrue(all(row['characters'] <= 20000 for row in prepared if row['number'] != initial[-1]['number']))


class PendingClosingTests(FinalPagesSupport):
    def test_pending_closing_shrink_condenses_rows_and_preserves_passage(self):
        native = self.export_targets(70)
        requested = []
        original_invoke = self.invoke
        def invoke(current, output, attempt=None):
            if output.get('stage') == 'reading' and requested and current == requested[0]:
                assigned = self.store.snapshot()['records']['review_assignment'][current]
                plan = capacity.read_chain_capacity_plan(self.artifacts, assigned)
                fixed = plan['frame']['final']['bound'] + plan['task']['final']['notice_characters']
                return CondensingCommandTests.invoke_with_ratio(self, current, output,
                    (fixed + 20000) / plan['limits']['input_tokens']['value'])
            return original_invoke(current, output, attempt)
        def answer(current, packet):
            binding = packet.get('reading_final', {})
            if 'closing' in binding and not requested:
                requested.append(current)
                response = self.build_v7_answer(packet)
                assignment = self.store.snapshot()['records']['review_assignment'][current]
                inventory = json.loads(self.artifacts.read(assignment['reading_chain']['inventory']))
                response['passage_requests'] = [dict(id='closing-pass', unit=inventory['material'][0]['handle'],
                                                   anchor=None, characters=100)]
                return response
            if 'closing' in binding and len(requested) == 1:
                self.assertTrue(packet['evidence'])
                self.assertLessEqual(capacity.measure_capacity_value(self.store.snapshot()['records']['review_route']['route'],
                    binding['closing']), binding['capacity']['material_allowance'])
                requested.append(current)
            return None
        with patch.object(self, 'invoke', side_effect=invoke):
            final, packets = self.finish_pages(native, before_answer=answer)
        self.assertGreater(sum('reading_combine' in packet for packet in packets), 0)
        self.assert_recorded_composite(final)


class AllowanceForecastCommandTests(FinalPagesSupport):
    text_sizes = (200000, 200000, 200000)

    def test_check_forecasts_the_assigned_pages_with_retained_entries_at_allowances(self):
        from research_harness.check_mode import check_mutation
        native = self.export_targets()
        payload = dict(id='turn-1', route_id='route', dossier_id=None, role='native_math', reviewer_id='openness-reviewer',
                       author_id='problem-author', context=dict(native_packet=native))
        revision = self.store.revision
        checked = check_mutation(self.store, protocol.record_assignment, payload, expected_revision=revision,
                                 request_id='final-page-check', bounded_payload=payload)
        self.assertEqual(self.store.revision, revision)
        forecast = checked['capacity_forecast']['by_kind']['final']
        self.assertEqual(forecast['pages'], 9)
        remaining = ['t%d' % n for repetition in range(4) for n in range(1,10)]
        def answer(current, packet):
            if 'reading_turn' in packet:
                response = self.build_v7_answer(packet)
                response['notes'] = 'n' * 20000
                source = next((row for row in packet['evidence'] if len(row['content']) >= 6000), None)
                if source is not None:
                    for index in range(min(5, len(remaining))):
                        response['excerpts'].append(dict(unit=source['handle'], text=source['content'][:6000],
                            purpose='A retained supporting range.', targets=[remaining.pop(0)]))
                return response
            self.assertFalse(remaining)
            return None
        final, packets = self.finish_pages(native, before_answer=answer)
        composite = self.assert_recorded_composite(final)
        self.assertEqual(len(composite['pages']), forecast['pages'])
        self.assertEqual(len(composite['closing']['condensing']), forecast['condensing_turns'])


class PageExcerptChargeTests(unittest.TestCase):
    def test_other_page_exhausted_target_and_page_overflow_get_over_allowance(self):
        artifacts = MemoryArtifacts()
        route = build_route('codex_cli_v1', 'gpt-6-astra')
        body = 'Synthetic passage.'
        reference = artifacts.put(body.encode(), 'text/plain')
        binding = dict(turn=2, protocol=v7.PROTOCOL, chain='chain', page_plan_digest='a'*64, final_turn=1,
            page=dict(targets=['t1', 't2']), notes='Notes.', entries=[], gaps=[],material=[],sources=[],fill=[],
            capacity=dict(material_allowance=1200), plan=dict(now=[dict(handle='u1', kind='fulltext', part=1,
            start=0,end=len(body),characters=len(body),artifact=reference)]))
        packet = dict(reading_final=binding, evidence=[dict(handle='u1', content=body)])
        state = dict(turns=[],gaps=[],latest_notes='Notes.', allowances=dict(targets=[dict(handle=target, characters=30000, used=0)
            for target in ('t1','t2','t3')]))
        def project(targets, count=1, budget=None):
            current = copy.deepcopy(state)
            if budget is not None:
                current['allowances']['targets'][1]['characters'] = budget
            output = dict(notes='Notes.', excerpts=[dict(unit='u1', text=body, purpose='Relevant.', targets=targets) for _ in range(count)])
            return v7.project_own_turn(artifacts,route,packet,output,'page','attempt',state=current)
        self.assertEqual(project(['t3'])['entries'][0]['status'], 'over_allowance')
        self.assertEqual(project(['t1','t2'],budget=0)['entries'][0]['status'], 'over_allowance')
        result = project(['t1'], 5)
        self.assertEqual(result['entries'][0]['status'], 'verified')
        self.assertEqual(result['entries'][-1]['status'], 'over_allowance')
        self.assertEqual(state['allowances']['targets'][0]['used'], 0)


class HeldPageAllowanceTests(unittest.TestCase):
    def test_split_four_and_three_then_allow_one_preserves_pending_work_and_unique_targets(self):
        from research_harness import review_final_pages as pages, review_condensing, review_composition
        from test_openness_finding_answer import build_packet, build_answer
        targets = [dict(handle='t%d' % n, entries=[dict(handle='e%d'%n,characters=1000)],rows=[]) for n in range(1,8)]
        inputs = dict(page_room=60000,target_allowance=7,target_minimum=6700,notes=1000,pool=[],targets=targets,
                      closing_room=60000,row_characters=360,condense=review_condensing.plan_condensing)
        first = pages.plan_final_turns(**inputs)
        initial_digest = digest(first)
        divided = pages.split_final_turn(first, 1, 6000, review_condensing.plan_condensing)
        groups = [row['targets'] for row in divided if row['kind']=='page']
        self.assertEqual([len(group) for group in groups], [4,3])
        pending = [divided[0]['number']]
        repacked = pages.repack_final_turns(divided,[],pending,60000,1,condense=review_condensing.plan_condensing)
        for selected in list(repacked):
            if selected['kind']=='page':
                repacked = pages.prepare_final_turn(repacked, selected['number'],60000,1,6000,
                    review_condensing.plan_condensing,pending=pending)
        assigned = [row for row in repacked if row['kind']=='page']
        self.assertTrue(all(len(row['targets'])==1 for row in assigned))
        self.assertEqual([target for row in assigned for target in row['targets']], [row['handle'] for row in targets])
        self.assertTrue(all(any(set(row['targets'])<=set(group) for group in groups) for row in assigned))
        self.assertTrue(all(row['pending_of']==pending[0] for row in assigned[:4]))
        self.assertEqual(digest(first),initial_digest)
        packet=build_packet('blocked')
        packet['native']['scientific_context']['openness_minimum']['neighbours']=['Neighbour %d'%n for n in range(1,7)]
        packet['reading_final']=dict(protocol=v7.PROTOCOL,page_plan_digest='p',page=dict(targets=[row['handle'] for row in targets]))
        rule=review_composition.find_rule('native_math',packet)
        outputs=[dict(targets=row['targets'],output=dict(targets=[dict(formulation=row['targets'][0],coverage='Read.',grounds=[],sentence='Assessed.')])) for row in assigned]
        closing=dict({key:'Assessed.' for key in ('statement','assumptions','scope','dependencies','policy')},
                     outcome='unresolved',reason='A remaining gap.',grounds=[])
        composed=review_composition.compose_output(rule,packet,outputs,closing)
        from research_harness.native_review import validate_finding
        validate_finding(packet,composed)
        self.assertEqual(len(composed['findings']['openness']['coverage']),7)


class AdmissionCheckTests(FinalPagesSupport):
    def test_five_thousand_targets_check_admits_without_a_write(self):
        from research_harness.check_mode import check_mutation
        native = json.loads(self.artifacts.read(self.export_targets(1)))
        native['scientific_context']['openness_minimum']['neighbours'] = ['Check-only neighbour %d' % n for n in range(4999)]
        reference = self.artifacts.put(serialize_record(native), 'application/json')
        payload = dict(id='turn-1', route_id='route', dossier_id=None, role='native_math', reviewer_id='openness-reviewer',
                       author_id='problem-author', context=dict(native_packet=reference))
        revision = self.store.revision
        checked = check_mutation(self.store, protocol.record_assignment, payload, expected_revision=revision,
                                 request_id='large-final-check', bounded_payload=payload)
        self.assertEqual(self.store.revision,revision)
        self.assertEqual(checked['reading_chain_admission']['final_form'],'pages')
        self.assertGreater(checked['reading_chain_admission']['pages'],1)
        self.assertEqual(checked['protocol_selection']['protocol'], v7.PROTOCOL)

    def test_ratio_below_the_smallest_page_plans_export(self):
        from research_harness.review_continuation import plan_next
        native=self.export_targets(1)
        self.assign('turn-1',native)
        output=self.build_v7_answer(self.read_packet('turn-1'))
        CondensingCommandTests.invoke_with_ratio(self,'turn-1',output,.01)
        action=plan_next(self.store.snapshot()['records'],self.artifacts,'turn-1')
        self.assertEqual(action['command'],'export')


class FinalAdmissionFloorTests(FinalPagesSupport):
    def test_first_assignment_stops_when_route_cannot_answer_one_target(self):
        from codex_route_fixtures import record_codex_route
        from research_harness.errors import ResearchError
        native=self.export_targets(1)
        record_codex_route(self,'low-output',output_tokens=1000,model='gpt-6-astra')
        with self.assertRaises(ResearchError) as refused:
            self.mutate(protocol.record_assignment,dict(id='turn-1',route_id='low-output',dossier_id=None,
                role='native_math',reviewer_id='openness-reviewer',author_id='problem-author',context=dict(native_packet=native)))
        self.assertEqual(refused.exception.code,'review_route_capacity_below_floor')
        self.assertNotIn('turn-1',self.store.snapshot()['records'].get('review_assignment',{}))

    def test_malformed_closing_is_kept_and_refused_at_closing(self):
        native=self.export_targets()
        tested=[]
        def answer(current,packet):
            if 'closing' in packet.get('reading_final',{}) and not tested:
                refused=self.invoke(current,dict(unexpected='value'),attempt='malformed-closing')
                self.assertEqual(refused['output_refusal']['check'],'native.output')
                tested.append(True)
            return None
        final,_=self.finish_pages(native,before_answer=answer)
        self.assert_recorded_composite(final)


class OversizedNotesTests(unittest.TestCase):
    def test_notes_larger_than_a_page_are_segmented_then_condensed_before_page_and_closing(self):
        from research_harness import openness_final_pages,review_final_pages
        artifacts=MemoryArtifacts()
        route,_,plan=TargetAllowanceTests().build_plan(1)
        native=build_native(1)
        gap_ref=artifacts.put(b'[]')
        base=v7.plan_inventory([dict(kind='record',record_kind='minimum_gaps',record_id='minimum_gaps',artifact=gap_ref)],
                              {gap_ref['sha256']:b'[]'})
        prepared=v7.prepare_inventory(base,native,route)
        for content in prepared['bodies'].values():artifacts.put(content)
        inventory=prepared['inventory']
        state=v7.build_chain_state(inventory,[],route=route,plan=plan)['state']
        state.update(latest_notes='n'*20000,notes=[dict(turn=1,classification='current',text='n'*20000)])
        logical=dict(protocol=protocol.PROTOCOL,role='native_math',reviewer_id='fixture',dossier_id=None,native=native)
        inputs,_,_=openness_final_pages.build_final_inputs(artifacts,route,logical,'chain',2,inventory,state,plan,
            capacity.derive_plan_task_limits(plan,'final'),15000)
        turns=review_final_pages.plan_final_turns(**inputs)
        self.assertEqual([turn['kind'] for turn in turns][-2:],['page','closing'])
        self.assertTrue(all(entry['characters'] <= 8300 for entry in inputs['pool']))
        self.assertTrue(any(turn['kind']=='combine' for turn in turns))


class DecisionRowHandleTests(FinalPagesSupport):
    def test_expanded_argument_reference_reaches_closing_decision_row(self):
        native=self.export_targets()
        material=[]
        def answer(current,packet):
            binding=packet.get('reading_final',{})
            if 'page' in binding and not material:
                material.append(True)
                return dict(stage='material',title='A target argument',part=1,text='Argument detail.',more=False)
            if 'page' in binding and len(material)==1:
                material.append(True)
                return dict(targets=[dict(formulation=target,coverage='Read.',sentence='An argument remains.',
                    grounds=[dict(kind='own_argument',text='The stored argument has the detail.',refs=[dict(ref='m1')])])
                    for target in binding['page']['targets']])
            if 'closing' in binding:
                self.assertTrue(any('m1' in row[3] for row in binding['closing']['rows']))
            return None
        final,_=self.finish_pages(native,before_answer=answer)
        self.assert_recorded_composite(final)


class ByteAnswerCountTests(unittest.TestCase):
    def test_byte_measured_plan_uses_its_pinned_answer_room_for_target_allowance(self):
        route,profile,_=TargetAllowanceTests().build_plan(40,36002)
        plan=capacity.plan_chain_capacity(route,profile,frames=dict(reading=dict(frame=3000),final=dict(frame=3000)),
            inventory=[],targets=['t%d'%n for n in range(1,41)],allowance_rows_bound=3000,final_pages=True,
            measure=dict(rule='bytes',characters_per_token=1))
        self.assertEqual(plan['allocation']['final_form'],'pages')
        self.assertEqual(plan['task']['final']['target_allowance'],5)


class PageRoomMeasureTests(unittest.TestCase):
    def build_inputs(self, ratio):
        prior = dict(id='turn', context={}, role='bar', route_id='route', dossier_id=None,
                     author_id='author', reviewer_id='reviewer')
        records = dict(review_assignment=dict(turn=prior), review_attempt=dict(attempt=dict(
            assignment_id='turn', usage=dict(sends=1, input_tokens=100000, request_characters=80000))))
        plan = dict(measure=dict(characters_per_token=ratio, ratio_margin=0),
            allocation=dict(page_room=60000), limits=dict(input_tokens=dict(value=100000)),
            frame=dict(final=dict(bound=10000)), task=dict(final=dict(notice_characters=45000)))
        return records, prior, plan

    def test_byte_measured_room_keeps_the_pinned_room_and_current_level(self):
        from research_harness.openness_final_pages import measure_page_room
        records, prior, plan = self.build_inputs(None)
        for level, expected in ((30000, 30000), (120000, 60000)):
            with self.subTest(level=level):
                self.assertEqual(measure_page_room(records, None, prior, plan,
                    dict(material_allowance=level)), expected)

    def test_calibrated_room_still_shrinks_from_recorded_input_ratio(self):
        from research_harness.openness_final_pages import measure_page_room
        records, prior, plan = self.build_inputs(2)
        self.assertEqual(measure_page_room(records, None, prior, plan,
            dict(material_allowance=60000)), 25000)


class FinalLevelSuccessorTests(FinalPagesSupport):
    def test_completed_pages_replay_high_low_and_ratio_successors(self):
        from codex_route_fixtures import build_codex_response
        original = capacity.read_output_profile
        def profile(records, route, **kwargs):
            value = original(records, route, **kwargs)
            row = capacity._create_profile_row('final', None)
            row.update(attempts=10, completed_attempts=10, largest_output_tokens=100,
                       largest_thinking_tokens=109000)
            value['rows'] = [item for item in value['rows'] if item['kind'] != 'final'] + [row]
            return value
        native = self.export_targets()
        with patch.object(capacity, 'read_output_profile', side_effect=profile):
            current, packet = self.start_final(native)
        saved = {}
        for index, output_tokens in enumerate((100000, 10000, 64000)):
            assigned = self.store.snapshot()['records']['review_assignment'][current]
            saved[current] = digest(assigned)
            plan = capacity.read_chain_capacity_plan(self.artifacts, assigned)
            before = json.loads(self.artifacts.read(assigned['reading_chain']['final_page_plan']['artifact']))
            output = dict(targets=[dict(formulation=target, coverage='Read.', grounds=[], sentence='Unresolved.')
                                  for target in packet['reading_final']['page']['targets']])
            def respond(route, request, credential):
                response = build_codex_response(route, request, output)
                response['usage']['output_tokens'] = output_tokens
                if index == 2:
                    import math
                    fixed = plan['frame']['final']['bound'] + plan['task']['final']['notice_characters']
                    ratio = (fixed + 30000) / plan['limits']['input_tokens']['value']
                    size = capacity.measure_request(route, request)['text_characters']
                    response['usage']['input_tokens'] = math.ceil(size * (1 - capacity.TOKEN_RATIO_MARGIN) / ratio)
                return response
            with patch('research_harness.review_transport.send_request', side_effect=respond):
                self.mutate(protocol.invoke_assignment, dict(id='attempt-' + current, assignment_id=current), credential='test-only')
            following = 'level-%d' % index
            self.assign(following, native, prior=current)
            current, packet = following, self.read_packet(following)
            reference = self.store.snapshot()['records']['review_assignment'][current]['reading_chain']['final_page_plan']
            after = json.loads(self.artifacts.read(reference['artifact']))
            self.assertEqual(after['cause']['kind'], 'ratio' if index == 2 else 'level')
            self.assertIn(before['turns'][index]['number'], after['cause']['completed'])
            self.assertLessEqual(after['turns'][-1]['context']['page_room'], (240000,480000,30000)[index])
        final, _ = self.finish_pages(native, first=current, assign=False)
        self.assert_recorded_composite(final)
        for identifier, expected in saved.items():
            self.assertEqual(digest(self.store.snapshot()['records']['review_assignment'][identifier]), expected)


class CompositeEnvelopeBoundTests(unittest.TestCase):
    def test_complete_native_envelope_enforces_v7_ground_and_reason_bounds(self):
        from research_harness import openness_final_pages
        from research_harness.native_review import validate_finding
        from research_harness.errors import ResearchError
        from test_openness_finding_answer import build_packet, build_answer
        packet = build_packet('blocked')
        artifacts = MemoryArtifacts()
        assignment = dict(context=dict(native_packet=artifacts.put(serialize_record(packet['native']))))
        envelope = openness_final_pages.build_validation_envelope(artifacts, assignment,
            dict(reading_final=dict(protocol=v7.PROTOCOL, page_plan_digest='p')), ['t1'])
        self.assertEqual(envelope['native'], packet['native'])
        output = build_answer(packet)
        for field, text, rule in (('reason', 'r' * 4001, 'reason_bound'), ('grounds', 'g' * 601, 'ground_bound')):
            changed = copy.deepcopy(output)
            if field == 'reason':
                changed['findings']['openness']['reason'] = text
            else:
                changed['findings']['openness']['grounds'] = [dict(kind='reviewer_gap', text=text)]
            with self.assertRaises(ResearchError) as refused:
                validate_finding(envelope, changed)
            self.assertEqual(refused.exception.details['rule'], rule)


class HeldPendingCommandTests(FinalPagesSupport):
    text_sizes = (100000, 100000)

    def test_split_seven_four_three_then_one_resumes_passage_and_argument_and_records(self):
        from codex_route_fixtures import build_codex_response
        original = capacity.read_output_profile
        def profile(records, route, **kwargs):
            value = original(records, route, **kwargs)
            rows = []
            for level, thinking in ((None, 99000), (480000, 109000)):
                row = capacity._create_profile_row('final', level)
                row.update(attempts=10, completed_attempts=10, largest_output_tokens=100,
                           largest_thinking_tokens=thinking)
                rows.append(row)
            value['rows'] = [item for item in value['rows'] if item['kind'] != 'final'] + rows
            return value
        native = self.export_targets(14)
        with patch.object(capacity, 'read_output_profile', side_effect=profile):
            self.assign('turn-1', native)
        current = 'turn-1'
        targets = ['t%d' % n for repeat in range(4) for n in range(1,15)]
        for index in range(30):
            packet = self.read_packet(current)
            if 'reading_final' in packet:
                break
            output = self.build_v7_answer(packet)
            source = next((row for row in packet['evidence'] if len(row['content']) >= 1500), None)
            if source is not None:
                for _ in range(min(15,len(targets))):
                    output['excerpts'].append(dict(unit=source['handle'],text=source['content'][:1500],
                        purpose='A supporting entry.',targets=[targets.pop(0)]))
            self.invoke(current,output)
            following = 'held-reading-%d' % index
            self.assign(following,native,prior=current)
            current = following
        self.assertFalse(targets)
        records = self.store.snapshot()['records']
        assigned = records['review_assignment'][current]
        plan = capacity.read_chain_capacity_plan(self.artifacts,assigned)
        first = json.loads(self.artifacts.read(assigned['reading_chain']['final_page_plan']['artifact']))
        self.assertEqual(len(packet['reading_final']['page']['targets']),7)
        turn = first['turns'][0]
        sizes = [sum(row['characters'] for row in part['entries']+part['rows']) for part in turn['parts']]
        room = turn['context']['notes'] + turn['context']['pool'] + sum(sizes[:4]) + 7000
        self.assertLess(room,60000)
        fixed = plan['frame']['final']['bound'] + plan['task']['final']['notice_characters']
        response = self.build_v7_answer(packet)
        response['passage_requests'] = [dict(id='held-passage',unit=packet['reading_final']['minimum']['gaps_unit'],
                                            anchor=None,characters=100)]
        CondensingCommandTests.invoke_with_ratio(self,current,response,(fixed+room)/plan['limits']['input_tokens']['value'])
        self.assign('held-four',native,prior=current)
        packet = self.read_packet('held-four')
        assigned = self.store.snapshot()['records']['review_assignment']['held-four']
        reference = assigned['reading_chain']['final_page_plan']
        divided = json.loads(self.artifacts.read(reference['artifact']))
        groups = [row['targets'] for row in divided['turns'] if row.get('pending_of')==turn['number'] and row['kind']=='page']
        self.assertEqual([len(group) for group in groups],[4,3],dict(room=room,before=turn['context'],
            after=divided['turns'][-1]['context'],cause=divided['cause'],capacity=packet['reading_final']['capacity']))
        self.assertGreaterEqual(len(reference['predecessors']),2)
        self.assertTrue(packet['evidence'],json.loads(self.artifacts.read(assigned['reading_chain']['state']))['pending_requests'])
        earlier = {key:digest(row) for key,row in self.store.snapshot()['records']['review_assignment'].items()}
        output = self.build_v7_answer(packet)
        output['passage_requests'] = [dict(id='held-again',unit=packet['reading_final']['minimum']['gaps_unit'],
                                          anchor=None,characters=100)]
        def respond(route,request,credential):
            response=build_codex_response(route,request,output)
            response['usage']['output_tokens']=100000
            return response
        with patch('research_harness.review_transport.send_request',side_effect=respond):
            self.mutate(protocol.invoke_assignment,dict(id='attempt-held-four',assignment_id='held-four'),credential='test-only')
        self.assign('held-one',native,prior='held-four')
        packet=self.read_packet('held-one')
        self.assertEqual(len(packet['reading_final']['page']['targets']),1)
        self.assertTrue(packet['evidence'])
        CondensingCommandTests.invoke_with_ratio(self,'held-one',dict(stage='material',title='The pending split argument',
            part=1,text='First part.',more=True),(fixed+20000)/plan['limits']['input_tokens']['value'])
        self.assign('held-argument',native,prior='held-one')
        packet=self.read_packet('held-argument')
        self.assertEqual(len(packet['reading_final']['page']['targets']),1)
        self.assertEqual(packet['reading_final']['material_request']['part'],2)
        self.invoke('held-argument',dict(stage='material',title='The pending split argument',part=2,text='Last part.',more=False))
        self.assign('held-complete',native,prior='held-argument')
        def answer(current,packet):
            binding=packet.get('reading_final',{})
            if 'page' in binding:
                self.assertLessEqual(len(binding['page']['targets']),1)
                if set(binding['page']['targets']) <= set(turn['targets']):
                    self.assertTrue(any(set(binding['page']['targets'])<=set(group) for group in groups))
                return dict(targets=[dict(formulation=target,coverage='Read.',sentence='A gap remains.',
                    grounds=[dict(kind='own_argument',text='The recorded argument explains the gap.',refs=[dict(ref='m1')])])
                    for target in binding['page']['targets']])
            return None
        def invoke(current,output,attempt=None):
            def respond(route,request,credential):
                response=build_codex_response(route,request,output)
                response['usage']['output_tokens']=64000
                return response
            with patch('research_harness.review_transport.send_request',side_effect=respond):
                return self.mutate(protocol.invoke_assignment,dict(id=attempt or 'attempt-'+current,
                    assignment_id=current),credential='test-only')
        with patch.object(self,'invoke',side_effect=invoke):
            final,_=self.finish_pages(native,first='held-complete',assign=False,before_answer=answer)
        composite=self.assert_recorded_composite(final)
        self.assertEqual(sum(len(row['targets']) for row in composite['pages']),14)
        self.assertEqual(len({target for row in composite['pages'] for target in row['targets']}),14)
        for identifier,expected in earlier.items():
            self.assertEqual(digest(self.store.snapshot()['records']['review_assignment'][identifier]),expected)
