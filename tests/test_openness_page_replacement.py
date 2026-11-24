"""D2 A7/A14: replacements at each position of a composed v7 final."""
import copy
import json
import unittest
from unittest.mock import patch

from test_openness_final_pages import FinalPagesSupport
from test_openness_unread_final import UnreadFinalSupport
from openness_named_gap_fixtures import build_ground
import test_openness_reading_v7_turns as turn_fixtures
from codex_route_fixtures import build_codex_response, bind_codex_response
from research_harness import openness_reading as reading, openness_reading_v7 as v7
from research_harness import review_protocol as protocol, review_route_capacity as capacity
from research_harness import review_failure_class as failures
from research_harness.review_continuation import plan_next
from research_harness.evidence import digest


class PageReplacementTests(FinalPagesSupport):
    def build_v7_answer(self, packet):
        output = turn_fixtures.V7CommandTests.build_v7_answer(self, packet)
        if 'reading_turn' in packet:
            binding = packet['reading_turn']
            for row, body in zip(binding['plan']['now'], packet['evidence']):
                if row['kind'] == 'fulltext':
                    output['excerpts'].append(dict(unit=row['handle'], text=body['content'][:100],
                        purpose='The partial result behind the named search gap.',
                        targets=[target['handle'] for target in binding['allowances']['targets']]))
        return output

    def build_page_gap(self, packet, target, text='A page gap.'):
        quote = next(row for row in packet['reading_final']['entries'] if row.get('status') == 'verified')
        # The final packet abbreviates formulations; use the selected record's literal text.
        supplied = dict(identity=dict(unit=quote['unit']), sentence=quote['text'],
            formulation=([self.selected['statement']] + self.selected['neighbours'])[int(target[1:]) - 1],
            missing_term='finite extremal example')
        ground = build_ground(dict(packet, _fixture=supplied))
        ground.update(text=text, need='Search for finite extremal examples for this formulation.',
            unsettled='The delivered partial result does not settle the formulation.')
        return ground

    def answer(self, packet):
        if reading.find_protocol(packet) != v7.PROTOCOL:
            return self.build_reading_response(packet, excerpts=[]) if reading.find_stage(packet) == 'reading' else self.build_native_result(packet, outcome='unresolved')
        if 'reading_turn' in packet:
            return self.build_v7_answer(packet)
        if 'reading_combine' in packet:
            block = packet['reading_combine']['block']
            return dict(block=block['handle'], summary='Read.', refs=[block['entries'][0].get('handle', block['entries'][0].get('block'))])
        binding = packet['reading_final']
        if 'closing' in binding:
            return dict({key: 'Assessed.' for key in ('statement', 'assumptions', 'scope', 'dependencies', 'policy')},
                        outcome='unresolved', reason='A remaining gap.', grounds=[dict(kind='own_argument', text='The recorded argument needs further checking.')])
        if 'page_plan_digest' not in binding:
            return self.build_v7_finding(packet, binding['notes_handle'])
        return dict(targets=[dict(formulation=target, coverage='Read.', grounds=[], sentence='A gap remains.')
                             for target in binding['page']['targets']])

    build_v7_finding = turn_fixtures.V7CommandTests.build_v7_finding

    def fail_capacity(self, head, outcome='output_over_cap', attempt=None):
        def respond(route, request, credential):
            from research_harness.errors import ResearchError
            if outcome == 'input_over_limit':
                raise ResearchError('review_input_too_large', 'Synthetic provider input limit.')
            response = build_codex_response(route, request, {})
            if outcome == 'timeout':
                response['execution'] = dict(timeout=True)
                response['status'] = 'incomplete'
            else:
                response['usage']['output_tokens'] = 128000
            return bind_codex_response(route, request, response)
        with patch('research_harness.review_transport.send_request', side_effect=respond):
            return self.mutate(protocol.invoke_assignment, dict(id=attempt or 'capacity-' + head, assignment_id=head), credential='fixture')

    def replace(self, head):
        action = plan_next(self.store.snapshot()['records'], self.artifacts, head)
        self.assertEqual((action['state'], action.get('command')), ('replace', 'review-assignment'), action)
        self.assertTrue(action['details'].get('recovery', {}).get('preflight', True))
        result = self.mutate(protocol.record_assignment, action['payload'])['result']
        return result['id']

    def freeze_records(self):
        return {kind: {key: digest(value) for key, value in rows.items()}
                for kind, rows in self.store.snapshot()['records'].items()}

    def assert_frozen(self, saved):
        records = self.store.snapshot()['records']
        for kind, rows in saved.items():
            for key, checksum in rows.items():
                self.assertEqual(digest(records[kind][key]), checksum, (kind, key))

    def finish(self, native, head):
        for number in range(100):
            packet = self.read_packet(head)
            receipt = self.invoke(head, self.answer(packet))
            self.assertIsNone(receipt.get('output_refusal'), receipt.get('output_refusal'))
            if reading.find_stage(packet) == 'closing':
                return head, self.assert_recorded_composite(head)
            if reading.find_stage(packet) == 'final':
                records = self.store.snapshot()['records']
                self.assertTrue(reading.verify_chain(records, self.artifacts, records['review_assignment'][head]))
                self.mutate(self.load_problem_module().record_openness, dict(assignment_id=head))
                return head, None
            following = 'finish-%d' % len(self.store.snapshot()['records']['review_assignment'])
            assigned = self.store.snapshot()['records']['review_assignment'][head]
            self.mutate(protocol.record_assignment, dict(id=following, route_id=assigned['route_id'], dossier_id=None,
                role='native_math', reviewer_id=assigned['reviewer_id'], author_id=assigned['author_id'],
                context=dict(native_packet=native, prior_assignment_id=head)))
            head = following
        self.fail('The replacement must reach a recorded finding.')

    def test_a14_request_capacity_splits_then_level_preserves_four_three_and_authenticates(self):
        native = self.export_targets(7)
        from test_review_output_profile import build_row
        with patch.object(capacity, 'read_profile_priors', return_value=[build_row(kind='final', level=None, thinking=1000, count=8)]):
            head, packet = self.start_final(native)
        saved = self.freeze_records()
        self.assertNotIn('page_plan_digest', packet['reading_final'])
        self.invoke_usage(head, self.answer(packet), tokens=128000)
        head = self.replace(head)
        packet = self.read_packet(head)
        self.assertEqual(len(packet['reading_final']['page']['targets']), 4)
        assignment = self.store.snapshot()['records']['review_assignment'][head]
        reference = assignment['reading_chain']['final_page_plan']
        plans = [json.loads(self.artifacts.read(row['artifact'])) for row in reference.get('predecessors', []) + [reference]]
        self.assertEqual([row.get('cause', {}).get('kind') for row in plans], [None, 'split', 'level'])
        self.assertEqual([len(row['targets']) for row in plans[-1]['turns'] if row['kind'] == 'page'], [4, 3])
        _, composite = self.finish(native, head)
        self.assertEqual(len(composite['pages']), 2)
        self.assert_frozen(saved)

    def test_a7_page_refusal_rebuilds_same_material_and_records(self):
        native = self.export_targets(9)
        head, packet = self.start_final(native)
        answer = self.answer(packet)
        answer['targets'][0]['grounds'] = [dict(kind='own_argument', text='g' * 601)]
        receipt = self.invoke(head, answer)
        self.assertIn('output_refusal', receipt)
        self.assertEqual(receipt['output_refusal']['details']['rule'], 'ground_bound')
        saved = self.freeze_records()
        replacement = self.replace(head)
        after = self.read_packet(replacement)
        self.assertEqual(after['output_refusal']['form'], 'whole')
        for key in v7.FINAL_MATERIAL_FIELDS:
            self.assertEqual(after['reading_final'][key], packet['reading_final'][key])
        self.assertEqual(after['reading_final']['page'], packet['reading_final']['page'])
        final, composite = self.finish(native, replacement)
        self.assertEqual(composite['pages'][0]['assignment_id'], replacement)
        self.assert_frozen(saved)

    def reach_closing(self, native, head):
        for number in range(100):
            packet = self.read_packet(head)
            if reading.find_stage(packet) == 'closing':
                return head, packet
            self.invoke(head, self.answer(packet))
            following = 'closing-%d' % len(self.store.snapshot()['records']['review_assignment'])
            self.assign(following, native, prior=head)
            head = following
        self.fail('Expected closing page.')

    def test_a7_composed_refusal_belongs_to_closing_notice_and_tampering_fails(self):
        from research_harness.errors import ResearchError
        native = self.export_targets(9)
        head, _ = self.start_final(native)
        # Page-owned grounds make a resolved closing outcome inconsistent.
        while reading.find_stage(self.read_packet(head)) != 'closing':
            packet = self.read_packet(head)
            output = self.answer(packet)
            for row in output['targets']:
                row['grounds'] = [self.build_page_gap(packet, row['formulation'])]
            receipt = self.invoke(head, output)
            self.assertNotIn('output_refusal', receipt, receipt)
            following = 'ground-%d' % len(self.store.snapshot()['records']['review_assignment'])
            self.assign(following, native, prior=head)
            head = following
        output = self.answer(self.read_packet(head))
        output.update(outcome='supported_open', grounds=[])
        receipt = self.invoke(head, output)
        self.assertEqual(receipt['output_refusal']['check'], 'openness.finding')
        saved = self.freeze_records()
        old = head
        head = self.replace(head)
        notice = self.read_packet(head)['output_refusal']
        context = notice['composed_finding']
        self.assertEqual(context['path'], '/findings/openness/outcome')
        self.assertEqual(context['check'], 'openness.finding')
        self.assertEqual(context['closing_answer'], output)
        final, composite = self.finish(native, head)
        self.assertEqual(composite['closing']['assignment_id'], head)
        records = self.store.snapshot()['records']
        changed = copy.deepcopy(records)
        certificate = next(row for row in changed['review_output_refusal'].values() if row['assignment_id'] == old)
        certificate['output_digest'] = '0' * 64
        with self.assertRaises(ResearchError):
            reading.verify_chain(changed, self.artifacts, changed['review_assignment'][final])
        changed = copy.deepcopy(records)
        changed['review_assignment'][head]['reading_chain']['final_page_plan']['digest'] = '0' * 64
        with self.assertRaises(ResearchError):
            reading.verify_chain(changed, self.artifacts, changed['review_assignment'][final])
        self.assert_frozen(saved)

    def test_a7_third_page_refusal_exports_admitted_successor(self):
        from test_openness_reading_v7_refusal import LegacySuccessorTests
        native = self.export_targets(9)
        head, _ = self.start_final(native)
        saved = self.freeze_records()
        for number in range(3):
            output = self.answer(self.read_packet(head))
            output['targets'][0]['grounds'] = [dict(kind='own_argument', text='g' * 601)]
            receipt = self.invoke(head, output)
            self.assertEqual(receipt['output_refusal']['details']['rule'], 'ground_bound')
            if number < 2:
                head = self.replace(head)
        action = plan_next(self.store.snapshot()['records'], self.artifacts, head)
        self.assertEqual((action['state'], action.get('command')), ('handoff', 'export'), action)
        self.assertEqual(action['details']['successor']['protocol'], v7.PROTOCOL)
        native, _ = LegacySuccessorTests.execute_export(self, action)
        self.finish(native, 'successor')
        self.assert_frozen(saved)

    def test_a14_closing_capacity_condenses_two_blocks_then_records(self):
        native = self.export_targets(9)
        head, _ = self.start_final(native)
        head, packet = self.reach_closing(native, head)
        self.invoke_usage(head, self.answer(packet), tokens=128000)
        self.assertFalse(self.store.snapshot()['records'].get('review_composite'))
        saved = self.freeze_records()
        head = self.replace(head)
        assignment = self.store.snapshot()['records']['review_assignment'][head]
        plan = json.loads(self.artifacts.read(assignment['reading_chain']['final_page_plan']['artifact']))
        self.assertEqual([row['kind'] for row in plan['turns']][-3:], ['combine', 'combine', 'closing'])
        self.assertEqual([len(row['entry_sizes']) for row in plan['turns'] if row['kind'] == 'combine'], [5, 4])
        self.assertEqual(reading.find_stage(self.read_packet(head)), 'combine')
        final, composite = self.finish(native, head)
        self.assertEqual(len(composite['closing']['condensing']), 2)
        self.assert_frozen(saved)

    def test_capacity_split_of_refused_closing_runs_combine_with_the_frozen_notice(self):
        """A refused closing needs the smallest two-page finding before it can split to combine."""
        from research_harness import review_preflight, review_prompt_paragraphs as paragraphs
        native = self.export_targets(9)
        with patch.object(protocol, 'COMMON_VERSION', 3):
            head, _ = self.start_final(native)
        head, packet = self.reach_closing(native, head)
        output = self.answer(packet)
        output['grounds'] = [dict(kind='own_argument', text='g' * 601)]
        self.assertEqual(self.invoke(head, output)['output_refusal']['details']['rule'], 'ground_bound')
        refused = self.replace(head)
        notice = self.read_packet(refused)['output_refusal']
        self.assertEqual(notice['form'], 'whole')
        self.fail_capacity(refused)
        records = self.store.snapshot()['records']
        with patch.object(review_preflight, 'build_assignment_report',
                          wraps=review_preflight.build_assignment_report) as reports:
            action = plan_next(records, self.artifacts, refused)
        self.assertEqual((action['state'], action.get('command')), ('replace', 'review-assignment'), action)
        proposed = next(call.args[2]['prompt'] for call in reports.call_args_list
                        if call.args[2]['id'] == action['payload']['id'])
        assignment = self.mutate(protocol.record_assignment, action['payload'])['result']
        head = assignment['id']
        packet = self.read_packet(head)
        self.assertEqual(reading.find_stage(packet), 'combine')
        self.assertEqual(packet['output_refusal'], notice)
        self.assertEqual(assignment['prompt']['parameters'][paragraphs.PARAMETER],
                         [paragraphs.REFUSAL_PARAGRAPH_VERSIONS['whole']])
        self.assertEqual(protocol.find_common_prompt_version(assignment['prompt']), 3)
        self.assertTrue(protocol._prompt_matches(assignment, packet))
        self.assertEqual(assignment['prompt'], proposed)
        records = self.store.snapshot()['records']
        actual = review_preflight.build_assignment_report(records, self.artifacts, assignment)
        self.assertEqual(action['details']['recovery']['preflight']['input_size'], actual['input_size'])
        self.finish(native, head)

    def refuse_closing_with_a_long_ground(self):
        """Refuse a closing whose ground takes most of the room that its request leaves."""
        native = self.export_targets(9)
        head, _ = self.start_final(native)
        head, packet = self.reach_closing(native, head)
        records = self.store.snapshot()['records']
        assignment = records['review_assignment'][head]
        request = protocol.build_assignment_request(records, self.artifacts, assignment)
        sizes = capacity.describe_plan_preflight(records, records['review_route']['route'], request,
            capacity.read_chain_capacity_plan(self.artifacts, assignment), 'final')['plan_comparison']['sizes']
        output = self.answer(packet)
        room = sizes['text_characters']['bound'] - sizes['text_characters']['value']
        output['grounds'] = [dict(kind='own_argument', text='g' * (room * 88 // 100))]
        # On the merged release the bound of the closing total (16,000 characters) refuses this answer before the
        # ground bound of the finding validator.
        refusal = self.invoke(head, output)['output_refusal']
        self.assertEqual((refusal['code'], refusal['check']), ('review_composition_conflict', 'review.shape'))
        return native, head

    def test_refused_closing_freezes_a_bounded_notice_that_its_combine_holds(self):
        """The two pages, the closing, its split into combine turns and the recorded finding need the nine targets."""
        native, head = self.refuse_closing_with_a_long_ground()
        refused = self.replace(head)
        notice = self.read_packet(refused)['output_refusal']
        self.assertEqual(notice['form'], 'bounded')
        self.fail_capacity(refused)
        head = self.replace(refused)
        packet = self.read_packet(head)
        self.assertEqual(reading.find_stage(packet), 'combine')
        self.assertEqual(packet['output_refusal'], notice)
        self.finish(native, head)

    def test_frozen_whole_notice_over_the_reserve_ends_in_the_capacity_decision(self):
        """Two pages lead to the closing, and only a split of a closing gives the combine that cannot hold the notice."""
        from research_harness import review_refusal_notice as notices
        native, head = self.refuse_closing_with_a_long_ground()
        select = notices.select_refusal_notice
        # The selection of a release without the reserve rule froze the whole notice.
        with patch.object(notices, 'select_refusal_notice',
                          side_effect=lambda *args, **kwargs: select(*args, **dict(kwargs, forms=('whole', 'bounded')))):
            refused = self.replace(head)
        frozen = self.read_packet(refused)
        self.assertEqual(frozen['output_refusal']['form'], 'whole')
        self.fail_capacity(refused)
        saved, revision = self.freeze_records(), self.store.revision
        for _ in range(2):
            action = plan_next(self.store.snapshot()['records'], self.artifacts, refused)
            # The repeated run of the capacity decision is refused by the store, so the plan hands off to the export.
            self.assertEqual((action['state'], action['command'], action['code']),
                             ('handoff', 'export', 'review_slot_exhausted'), action)
            self.assertEqual(action['details']['outcome'], 'output_over_cap')
            self.assertEqual(action['details']['range_not_served'], v7.FINAL_PAGE_FLOOR_STATEMENT)
            comparison = action['details']['plan_comparison']
            self.assertEqual(comparison['kind'], 'final')
            self.assertFalse(comparison['sizes']['text_characters']['fits'])
        self.assertEqual(self.store.revision, revision)
        self.assert_frozen(saved)
        self.assertEqual(self.read_packet(refused), frozen)
        self.take_export_step(action['details']['step'], refused, 'after-export')
        successor = plan_next(self.store.snapshot()['records'], self.artifacts, 'after-export')
        self.assertEqual((successor['state'], successor['command']), ('run', 'review-run'), successor)

    def test_final_after_its_export_names_the_successor_and_offers_no_second_export(self):
        """The final in one request: a refused final, its whole-notice replacement and an output cap end in the export
        handoff. After the export and the new assignment, the plan of the old head names the successor, twice the
        same, where release 0.92.1 planned the refused run again."""
        released = patch.object(v7, 'V7_FINAL_FORMS', ('request',))
        released.start()
        self.addCleanup(released.stop)
        native = self.export_targets(3)
        head, packet = self.start_final(native)
        bad = self.build_v7_finding(packet, packet['reading_final']['notes_handle'])
        bad['findings']['openness']['grounds'][0]['refs'] = [dict(ref='m999999')]
        self.assertEqual(self.invoke(head, bad)['output_refusal']['code'], 'invalid_reference_handle')
        head = self.replace(head)
        self.fail_capacity(head)
        action = plan_next(self.store.snapshot()['records'], self.artifacts, head)
        self.assertEqual((action['state'], action['command'], action['code']),
                         ('handoff', 'export', 'review_slot_exhausted'), action)
        self.take_export_step(action['details']['step'], head, 'after-export')
        revision = self.store.revision
        plans = [plan_next(self.store.snapshot()['records'], self.artifacts, head) for _ in range(2)]
        self.assertEqual(plans[0], plans[1])
        self.assertEqual((plans[0]['state'], plans[0]['details']['successor_assignment_id']), ('done', 'after-export'))
        self.assertEqual(self.store.revision, revision)

    def take_export_step(self, step, head, identifier):
        """Take step 4 through its commands: the export, the check of the new assignment, then its write."""
        from research_harness.check_mode import check_mutation
        from research_harness.problem_openness import export_openness
        self.assertEqual((step['id'], step['command'], step['payload']['kind']), (4, 'export', 'problem-openness'))
        old = self.store.snapshot()['records']['review_assignment'][head]
        payload = dict(step['payload'], assignment_id=identifier, reviewer_id=old['reviewer_id'],
                       minimum=dict(searches=[dict(formulation=row['question'], search_id=row['id'])
                                              for row in self.searches],
                                    settling_work_id=None, duplicate_resolutions=[]))
        del payload['kind']
        native = export_openness(self.store, payload, self.root / (identifier + '-export'),
                                 accept_gaps=True)['native_packet']
        for child in step['then']:
            value = copy.deepcopy(child['payload'])
            value.update(id=identifier, context=dict(native_packet=native))
            if child.get('check'):
                result = check_mutation(self.store, protocol.record_assignment, value,
                                        expected_revision=self.store.revision, request_id=identifier + '-check',
                                        bounded_payload=value)
                self.assertEqual(result['check'], 'accepted')
            else:
                self.mutate(protocol.record_assignment, value)
        self.assertIn(identifier, self.store.snapshot()['records']['review_assignment'])

    def test_a7_condensing_refusal_keeps_block_and_authenticates(self):
        native = self.export_targets(9)
        head, _ = self.start_final(native)
        head, _ = self.reach_closing(native, head)
        self.fail_capacity(head)
        head = self.replace(head)
        before = self.read_packet(head)
        receipt = self.invoke(head, dict(block=before['reading_combine']['block']['handle'], summary='Too large.' * 1000, refs=[]))
        self.assertIn('output_refusal', receipt)
        saved = self.freeze_records()
        head = self.replace(head)
        packet = self.read_packet(head)
        self.assertEqual(packet['reading_combine']['block'], before['reading_combine']['block'])
        self.assertEqual(packet['output_refusal']['form'], 'whole')
        self.finish(native, head)
        self.assert_frozen(saved)


    def pin_targets(self, allowance, reduced=None):
        original = capacity.plan_chain_capacity
        def build(*args, **kwargs):
            plan = original(*args, **kwargs)
            for row in plan['task']['final']['levels']:
                row['target_allowance'] = (reduced if reduced is not None and row['material_allowance'] <= 120000 else allowance)
            selected = next(row for row in plan['task']['final']['levels'] if row['material_allowance'] == plan['task']['final']['material_allowance'])
            plan['task']['final']['target_allowance'] = selected['target_allowance']
            return plan
        return patch.object(capacity, 'plan_chain_capacity', side_effect=build)

    def invoke_usage(self, head, output, tokens=64000, attempt=None):
        def respond(route, request, credential):
            response = build_codex_response(route, request, output)
            response['usage']['output_tokens'] = tokens
            return response
        with patch('research_harness.review_transport.send_request', side_effect=respond):
            return self.mutate(protocol.invoke_assignment, dict(id=attempt or 'attempt-' + head, assignment_id=head), credential='fixture')

    def test_a14_second_of_three_seven_target_pages_splits_and_keeps_first_output(self):
        native = self.export_targets(21)
        with self.pin_targets(7):
            head, packet = self.start_final(native)
        first = head
        self.invoke(head, self.answer(packet))
        self.assign('second-page', native, prior=head)
        head = 'second-page'
        self.assertEqual(len(self.read_packet(head)['reading_final']['page']['targets']), 7)
        self.invoke_usage(head, self.answer(self.read_packet(head)), tokens=128000)
        saved = self.freeze_records()
        head = self.replace(head)
        self.assertEqual(len(self.read_packet(head)['reading_final']['page']['targets']), 4)
        final, composite = self.finish(native, head)
        self.assertEqual([len(row['targets']) for row in composite['pages']], [7, 4, 3, 7])
        self.assertEqual(composite['pages'][0]['assignment_id'], first)
        self.assert_frozen(saved)

    def test_a14_condensing_capacity_splits_block_and_selects_only_replacements(self):
        # Eleven rows give a first block of six, with three rows in each
        # answerable replacement block.
        native = self.export_targets(11)
        head, _ = self.start_final(native)
        head, _ = self.reach_closing(native, head)
        self.fail_capacity(head)
        head = self.replace(head)
        old = head
        entries = self.read_packet(head)['reading_combine']['block']['entries']
        self.fail_capacity(head)
        head = self.replace(head)
        first = self.read_packet(head)['reading_combine']['block']['entries']
        self.assertEqual(first, entries[:3])
        _, composite = self.finish(native, head)
        self.assertNotIn(old, [row['assignment_id'] for row in composite['closing']['condensing']])
        self.assertEqual(len(composite['closing']['condensing']), 3)

    def test_a14_time_and_provider_input_limit_split_the_page(self):
        for outcome in ('timeout', 'input_over_limit'):
            with self.subTest(outcome=outcome):
                case = PageReplacementTests()
                try:
                    case.setUp()
                    native = case.export_targets(9)
                    head, _ = case.start_final(native)
                    case.fail_capacity(head, outcome, attempt='first-failure')
                    action = plan_next(case.store.snapshot()['records'], case.artifacts, head)
                    if action['state'] == 'run':
                        case.fail_capacity(head, outcome, attempt='second-failure')
                    head = case.replace(head)
                    case.assertEqual(len(case.read_packet(head)['reading_final']['page']['targets']), 4)
                    case.finish(native, head)
                finally:
                    case.doCleanups()

    def test_floor_retries_once_then_executes_export_and_route_answers(self):
        from test_openness_reading_v7_refusal import LegacySuccessorTests
        native = self.export_targets(9)
        head, _ = self.start_final(native)
        while len(self.read_packet(head)['reading_final']['page']['targets']) > 1:
            self.fail_capacity(head)
            head = self.replace(head)
        self.fail_capacity(head, attempt='floor-first')
        action = plan_next(self.store.snapshot()['records'], self.artifacts, head)
        self.assertEqual(action['state'], 'run', action)
        self.fail_capacity(head, attempt='floor-second')
        action = plan_next(self.store.snapshot()['records'], self.artifacts, head)
        self.assertEqual((action['state'], action['code']), ('stop', 'review_output_over_cap'))
        decision = action['details']['decision']
        export = next(step for answer in decision['then'] for step in answer['steps'] if step['command'] == 'export' and 'reviewer_id' in step['payload'])
        native, _ = LegacySuccessorTests.execute_export(self, dict(details=dict(step=export)))
        self.finish(native, 'successor')
        route_answer = next(answer for answer in decision['then'] if answer['answer'] == 'Move to another route')
        from codex_route_fixtures import record_codex_route
        self.assertEqual([step['command'] for step in route_answer['steps']], ['review-route', 'export'])
        record_codex_route(self, 'floor-route', output_tokens=128000, model='gpt-6-astra')
        # Execute the second answer's export/check/assignment on its chosen route.
        route_export = copy.deepcopy(route_answer['steps'][-1])
        for follow in route_export['then']:
            follow['payload'].update(route_id='floor-route', reviewer_id='openness-reviewer', author_id='problem-author')
        route_native, _ = LegacySuccessorTests.execute_export(self, dict(details=dict(step=route_export)), identifier='route-successor')
        self.finish(route_native, 'route-successor')

    def test_request_only_capacity_keeps_the_base_retry_decision(self):
        native = self.export_targets(7)
        head, _ = self.start_final(native)
        with patch.object(v7, 'V7_FINAL_FORMS', ('request',)):
            self.fail_capacity(head, attempt='legacy-first')
            self.assertEqual(plan_next(self.store.snapshot()['records'], self.artifacts, head)['state'], 'run')
            self.fail_capacity(head, attempt='legacy-second')
            action = plan_next(self.store.snapshot()['records'], self.artifacts, head)
            self.assertEqual((action['state'], action['code']), ('stop', 'review_output_over_cap'))
            self.assertFalse(any(step['command'] == 'export' and 'reviewer_id' in step.get('payload', {})
                                 for answer in action['details']['decision']['then'] for step in answer['steps']))
            self.assertFalse(any(row['reading_chain'].get('final_page_plan') for row in self.store.snapshot()['records']['review_assignment'].values()))

        records = self.store.snapshot()['records']
        self.assertEqual(capacity.plan_capacity_recovery(records, self.artifacts, records['review_assignment'][head], 'output_over_cap')['kind'], 'replace')

    def test_pending_split_four_three_then_allowance_one_keeps_passage_argument_and_digests(self):
        from test_review_output_profile import build_row
        native = self.export_targets(7)
        with self.pin_targets(7, reduced=1), patch.object(capacity, 'read_profile_priors', return_value=[build_row(kind='final', level=None, thinking=1000, count=8)]):
            head, _ = self.start_final(native)
        self.fail_capacity(head)
        head = self.replace(head)
        original_groups = [list(range(1, 5)), list(range(5, 8))]
        saved = self.freeze_records()
        output = self.build_v7_answer(self.read_packet(head))
        output['passage_requests'] = [dict(id='pending-passage', unit=self.read_packet(head)['reading_final']['minimum']['gaps_unit'], anchor=None, characters=100)]
        self.invoke_usage(head, output, 100000)
        self.assign('pending-one', native, prior=head)
        packet = self.read_packet('pending-one')
        self.assertEqual(len(packet['reading_final']['page']['targets']), 1)
        self.assertTrue(packet['evidence'])
        self.invoke_usage('pending-one', dict(stage='material', title='Pending argument', part=1, text='First.', more=True))
        self.assign('pending-part-two', native, prior='pending-one')
        packet = self.read_packet('pending-part-two')
        before_plan = packet['reading_final']['page_plan_digest']
        before_bound = packet['reading_final']['capacity']['answer_bound']
        self.fail_capacity('pending-part-two')
        head = self.replace('pending-part-two')
        packet = self.read_packet(head)
        self.assertEqual(packet['reading_final']['page_plan_digest'], before_plan)
        plan = capacity.read_chain_capacity_plan(self.artifacts, self.store.snapshot()['records']['review_assignment'][head])
        self.assertEqual(packet['reading_final']['capacity']['answer_bound'], max(before_bound // 2, plan['task']['final']['smallest_answer_bound']))
        self.assertEqual(packet['reading_final']['material_request']['part'], 2)
        self.invoke_usage(head, dict(stage='material', title='Pending argument', part=2, text='Last.', more=False))
        self.assign('pending-answer', native, prior=head)
        def invoke(head, output, attempt=None):
            packet = self.read_packet(head)
            if 'page' in packet.get('reading_final', {}):
                targets = packet['reading_final']['page']['targets']
                self.assertLessEqual(len(targets), 1)
                self.assertTrue(any({int(t[1:]) for t in targets} <= set(group) for group in original_groups))
            return self.invoke_usage(head, output, attempt=attempt)
        with patch.object(self, 'invoke', side_effect=invoke):
            _, composite = self.finish(native, 'pending-answer')
        targets = [target for row in composite['pages'] for target in row['targets']]
        self.assertEqual(sorted(targets), ['t%d' % n for n in range(1, 8)])
        self.assert_frozen(saved)


    def test_t2_page_filled_to_room_with_exact_45000_wire_notice_fits_stored_bound(self):
        from test_openness_reading_v7_refusal import NoticeRoomTests
        from research_harness import review_refusal_notice as notices, review_prompt_paragraphs as paragraphs
        native = self.export_targets(9)
        head, _ = self.start_final(native)
        bad = self.answer(self.read_packet(head))
        bad['targets'][0]['grounds'] = [dict(kind='own_argument', text='g' * 601)]
        self.invoke(head, bad)
        head = self.replace(head)
        packet = self.read_packet(head)
        assignment = self.store.snapshot()['records']['review_assignment'][head]
        plan = capacity.read_chain_capacity_plan(self.artifacts, assignment)
        route = self.store.snapshot()['records']['review_route']['route']
        binding = packet['reading_final']
        material = {key: binding[key] for key in v7.FINAL_MATERIAL_FIELDS}
        room = binding['capacity']['material_allowance']
        binding['notes'] += 'x' * (room - v7._measure_final_material(route, material))
        binding['notes_cost']['used'] = (capacity.measure_capacity_value(route, binding['notes'])
                                        - capacity.measure_capacity_value(route, ''))
        self.assertEqual(v7._measure_final_material(route, {key: binding[key] for key in v7.FINAL_MATERIAL_FIELDS}), room)
        notice = NoticeRoomTests().build_bounded_notice(turn_fixtures.MemoryArtifacts(), route=route)
        self.assertEqual(capacity.measure_capacity_value(route, notice), 45000)
        packet = notices.attach_refusal_notice(packet, notice)
        prompt = protocol.canonical_prompt('native_math', reading_stage='page', reading_protocol=v7.PROTOCOL,
            capacity=binding['capacity'], finding_version=2 if 'finding_index' in packet else 1,
            **paragraphs.select_prompt_parameters('native_math', packet))
        self.assertTrue(v7.matches_turn_prompt(dict(role='native_math', prompt=prompt), packet))
        request = protocol.transport.request_body(route, prompt['text'], packet)
        report = capacity.describe_plan_preflight(self.store.snapshot()['records'], route, request, plan, 'final')
        self.assertTrue(report['plan_comparison']['fits'], report['plan_comparison'])

    def test_provider_refusal_of_pages_in_every_sequence_ends_in_r7_decision(self):
        from test_openness_unread_final import UnreadFinalSupport
        native = self.export_targets(9)
        head, _ = self.start_final(native)
        positions = set()
        for number in range(40):
            positions.add(head)
            UnreadFinalSupport.refuse_provider(self, head, 'provider-%d' % number)
            action = plan_next(self.store.snapshot()['records'], self.artifacts, head)
            if action['state'] == 'replace':
                head = self.replace(head)
            elif action['state'] == 'stop':
                self.assertGreater(len(positions), 1)
                self.assertEqual(len(self.read_packet(head)['reading_final']['page']['targets']), 1)
                self.assertEqual(action['code'], 'review_turn_failures_exhausted')
                self.assertEqual(action['stop_class'], 'decision')
                self.assertIn('decision', action['details'])
                return
        self.fail('A page refused in every sequence must end in the R7 decision.')


    def test_local_closing_ground_refusal_does_not_claim_a_page_owned_composed_field(self):
        native = self.export_targets(9)
        head, _ = self.start_final(native)
        while reading.find_stage(self.read_packet(head)) != 'closing':
            packet = self.read_packet(head)
            output = self.answer(packet)
            output['targets'][0]['grounds'] = [self.build_page_gap(packet, output['targets'][0]['formulation'], 'A page ground.')]
            receipt = self.invoke(head, output)
            self.assertNotIn('output_refusal', receipt, receipt)
            following = 'local-%d' % len(self.store.snapshot()['records']['review_assignment'])
            self.assign(following, native, prior=head)
            head = following
        output = self.answer(self.read_packet(head))
        output['grounds'] = [dict(kind='own_argument', text='g' * 601)]
        receipt = self.invoke(head, output)
        self.assertEqual(receipt['output_refusal']['details']['rule'], 'ground_bound')
        head = self.replace(head)
        self.assertNotIn('composed_finding', self.read_packet(head)['output_refusal'])
        self.finish(native, head)

    def test_guard_every_final_turn_at_output_limit_reaches_floor_decision(self):
        native = self.export_targets(3)
        head, _ = self.start_final(native)
        saved = self.freeze_records()
        stages = set()
        for number in range(30):
            stages.add(reading.find_stage(self.read_packet(head)))
            self.fail_capacity(head, attempt='guard-%d' % number)
            action = plan_next(self.store.snapshot()['records'], self.artifacts, head)
            self.assertNotEqual(action['code'], 'review_route_capacity_plan_violated')
            if action['state'] == 'replace':
                head = self.replace(head)
            elif action['state'] == 'stop':
                self.assertEqual(action['code'], 'review_output_over_cap')
                self.assertIn('export', [step['command'] for answer in action['details']['decision']['then'] for step in answer['steps']])
                self.assert_frozen(saved)
                return
        self.fail('Every limit down to the floor must end in a decision.')


    def test_capacity_split_after_refusal_keeps_notice_and_starts_new_page_positions(self):
        native = self.export_targets(9)
        head, _ = self.start_final(native)
        output = self.answer(self.read_packet(head))
        output['targets'][0]['grounds'] = [dict(kind='own_argument', text='g' * 601)]
        self.invoke(head, output)
        head = self.replace(head)
        notice = self.read_packet(head)['output_refusal']
        self.fail_capacity(head)
        saved = self.freeze_records()
        previous = head
        head = self.replace(head)
        records = self.store.snapshot()['records']
        member = records['review_assignment'][head]
        self.assertEqual(member['capacity_replacement_of'], previous)
        self.assertEqual(self.read_packet(head)['output_refusal'], notice)
        self.assertEqual(failures.count_position(records, self.artifacts, member)['replacements']['output_refused'], 0)
        output = self.answer(self.read_packet(head))
        output['targets'][0]['grounds'] = [dict(kind='own_argument', text='g' * 601)]
        self.invoke(head, output)
        head = self.replace(head)
        records = self.store.snapshot()['records']
        self.assertNotIn('capacity_replacement_of', records['review_assignment'][head])
        self.assertEqual(failures.count_position(records, self.artifacts, records['review_assignment'][head])['replacements']['output_refused'], 1)
        self.finish(native, head)
        self.assert_frozen(saved)


    def test_guard_closing_and_small_combination_at_every_limit_reach_decision(self):
        native = self.export_targets(9)
        head, _ = self.start_final(native)
        head, _ = self.reach_closing(native, head)
        saved = self.freeze_records()
        route = self.store.snapshot()['records']['review_route']['route']
        for number in range(30):
            binding = self.read_packet(head).get('reading_combine')
            if binding is not None:
                block = binding['block']
                entry = block['entries'][0]
                minimum = dict(block=block['handle'], summary='', refs=[entry.get('handle', entry.get('block'))])
                self.assertGreaterEqual(binding['capacity']['answer_bound'],
                                        capacity.measure_capacity_value(route, minimum))
            self.fail_capacity(head, attempt='closing-guard-%d' % number)
            action = plan_next(self.store.snapshot()['records'], self.artifacts, head)
            self.assertNotEqual(action['code'], 'review_route_capacity_plan_violated')
            if action['state'] == 'replace':
                head = self.replace(head)
            elif action['state'] == 'stop':
                self.assertEqual(action['code'], 'review_output_over_cap')
                self.assert_frozen(saved)
                return
        self.fail('A closing or combination at every output limit must end in a decision.')


    def test_valid_page_answer_at_limit_is_split_instead_of_being_completed(self):
        native = self.export_targets(9)
        head, packet = self.start_final(native)
        self.invoke_usage(head, self.answer(packet), tokens=128000)
        head = self.replace(head)
        self.assertEqual(len(self.read_packet(head)['reading_final']['page']['targets']), 4)
        self.finish(native, head)


    def test_composed_notice_keeps_page_decision_handles_in_both_notice_forms(self):
        from research_harness import openness_final_pages, review_refusal_notice as notices
        native = self.export_targets(9)
        head, _ = self.start_final(native)
        while reading.find_stage(self.read_packet(head)) != 'closing':
            packet = self.read_packet(head)
            output = self.answer(packet)
            ground = self.build_page_gap(packet, output['targets'][0]['formulation'], 'The page reviewer gap.')
            ground['refs'] = [dict(ref=packet['reading_final']['notes_handle'])]
            output['targets'][0]['grounds'] = [ground]
            receipt = self.invoke(head, output)
            self.assertNotIn('output_refusal', receipt, receipt)
            following = 'bounded-%d' % len(self.store.snapshot()['records']['review_assignment'])
            self.assign(following, native, prior=head)
            head = following
        decision_row = self.read_packet(head)['reading_final']['closing']['rows'][0]
        self.assertTrue(decision_row[3])
        output = self.answer(self.read_packet(head))
        output.update(outcome='supported_open', grounds=[])
        receipt = self.invoke(head, output)
        self.assertEqual(receipt['output_refusal']['check'], 'openness.finding')
        head = self.replace(head)
        packet = self.read_packet(head)
        notice = packet['output_refusal']
        route = self.store.snapshot()['records']['review_route']['route']
        self.assertEqual(notice['form'], 'whole')
        records = self.store.snapshot()['records']
        bounded = notices.build_refusal_notice(records, self.artifacts, (notice['refused'][-1]['attempt_id'],),
            form='bounded', enforce_bounded_limit=True, measure=lambda value: capacity.measure_capacity_value(route, value))
        self.assertEqual(bounded['form'], 'bounded')
        self.assertEqual(bounded['composed_finding'], notice['composed_finding'])
        self.assertLessEqual(capacity.measure_capacity_value(route, bounded), notices.BOUNDED_NOTICE_CHARACTERS)
        self.assertLessEqual(capacity.measure_capacity_value(route, notice), notices.BOUNDED_NOTICE_CHARACTERS)
        self.assertLessEqual(capacity.measure_capacity_value(route, notice['composed_finding']), notices.COMPOSED_REFUSAL_CHARACTERS)
        self.assertEqual(notice['composed_finding']['closing_answer'], output)
        records = copy.deepcopy(self.store.snapshot()['records'])
        attempt = records['review_attempt'][notice['refused'][-1]['attempt_id']]
        certificate = records['review_output_refusal'][attempt['id']]
        certificate['details']['path'] = '/findings/openness/grounds/0/text'
        context = openness_final_pages.describe_composed_refusal(records, self.artifacts, attempt,
            certificate['check'], certificate['details']['path'])
        self.assertEqual(context['decision_row'], decision_row)
        self.assertIn('reviewer_gap', context['decision_row'][1])
        self.assertEqual(context['closing_answer'], output)
        self.finish(native, head)



class ClosingDeliveryTests(FinalPagesSupport):
    """A closing over unread rows prices them with its decision rows, its material and a requested passage."""
    # On this route the unread prefix and the whole passage together exceed the token bound at the byte measure.
    capacity_probe_tokens = 373000
    answer = PageReplacementTests.answer
    build_v7_answer = PageReplacementTests.build_v7_answer
    build_v7_finding = PageReplacementTests.build_v7_finding
    invoke_usage = PageReplacementTests.invoke_usage
    refuse_provider = UnreadFinalSupport.refuse_provider
    # With 30 small units the unread rows of a closing that delivers the whole passage exceed that bound.
    small_units = 30
    stock_build_inventory = staticmethod(v7.build_inventory)

    def build_inventory(self, records, artifacts, native, *, review=None):
        """Add the small units, one long unit and one passage of four-byte characters to the stock inventory."""
        result = self.stock_build_inventory(records, artifacts, native, review=review)
        inventory = result['inventory']
        material = {row['sha256']: row for row in inventory['material']}
        bodies = [('%06d' % number + 'q' * 200).encode() for number in range(self.small_units)]
        for number, data in enumerate(bodies + [b'a' * 1200000, ('\U0001f331' * 53000).encode()]):
            reference = v7.describe_artifact(data, 'text/plain')
            unit = v7._append_unit(inventory, dict(kind='record', record_kind='synthetic_required',
                                                   record_id='r%d' % number, artifact=reference), data)
            v7._bind_body(inventory, material, unit, reference, data, 'record')
            result['bodies'][reference['sha256']] = data
        return result

    def test_closing_delivers_a_requested_passage_within_its_material_and_request_bounds(self):
        """A run planned for the closing that delivers the passage passes its dispatch preflight."""
        # The chain compares its units with the records at each step, so the records give the added units throughout.
        with patch.object(v7, 'build_inventory', side_effect=self.build_inventory):
            head, packet = self.request_passage_at_closing()
            binding = packet['reading_final']
            route = self.store.snapshot()['records']['review_route']['route']
            used = (sum(capacity.measure_capacity_value(route, value) for value in
                        (binding['closing'], binding['material'], binding['plan']['now'], packet['evidence']))
                    + v7.compute_unread_rows_charge(route, binding['unread']))
            self.assertLessEqual(used, binding['capacity']['material_allowance'])
            self.assertTrue(binding['unread']['rows'])
            self.assertTrue(packet['evidence'])
            records = self.store.snapshot()['records']
            assignment = records['review_assignment'][head]
            request = protocol.build_assignment_request(records, self.artifacts, assignment)
            plan = capacity.read_chain_capacity_plan(self.artifacts, assignment)
            report = capacity.describe_plan_preflight(records, route, request, plan, 'final')
            self.assertEqual(report['request_measure']['measure'], 'bytes')
            self.assertTrue(report['plan_comparison']['fits'], report['plan_comparison'])
            action = plan_next(records, self.artifacts, head)
            self.assertEqual((action['state'], action.get('command')), ('run', 'review-run'), action)
            self.invoke_usage(head, self.answer(packet), attempt=action['payload']['id'])
            self.assertIn(action['payload']['id'], self.store.snapshot()['records']['review_attempt'])

    def request_passage_at_closing(self):
        """Exhaust the reading, answer the pages, request the whole passage at the closing and record its delivery."""
        native = self.export_targets(9, assignment_id='first')
        self.assign('first', native)
        _, final, packet = UnreadFinalSupport.exhaust_head(self, native, 'first')
        head = final['id']
        while reading.find_stage(packet) != 'closing':
            self.invoke_usage(head, self.answer(packet))
            step = plan_next(self.store.snapshot()['records'], self.artifacts, head)
            head = self.mutate(protocol.record_assignment, step['payload'])['result']['id']
            packet = self.read_packet(head)
        answer = self.build_v7_answer(packet)
        records = self.store.snapshot()['records']
        _, inventory, _ = v7.read_bound_state(self.artifacts, records['review_assignment'][head], packet)
        passage = next(row for row in inventory['material'] if row['characters'] == 53000)
        answer['passage_requests'] = [dict(id='pass', unit=passage['handle'], anchor=None, characters=53000)]
        self.assertNotIn('output_refusal', self.invoke_usage(head, answer))
        step = plan_next(self.store.snapshot()['records'], self.artifacts, head)
        head = self.mutate(protocol.record_assignment, step['payload'])['result']['id']
        return head, self.read_packet(head)

class PageFloorTests(unittest.TestCase):
    def test_single_target_page_after_condensing_keeps_request_refusal_position(self):
        raw = dict(id='request', role='native_math', capacity_plan_kind='final',
                   reading_chain=dict(first_assignment_id='chain', turn=3))
        page = dict(id='page', role='native_math', capacity_plan_kind='final',
                    reading_chain=dict(first_assignment_id='chain', turn=5, final_position=1, final_page_plan={}))
        other = dict(id='other', role='native_math', capacity_plan_kind='final',
                     reading_chain=dict(first_assignment_id='chain', turn=6, final_position=2, final_page_plan={}))
        for index, row in enumerate((raw, page, other)):
            row.update(route_id='route', dossier_id=None, reviewer_id='reviewer', author_id='author',
                       context=dict(prior_assignment_id='parent-' + row['id']), capacity_plan='plan', recorded_revision=index + 1)
        records = dict(review_assignment={row['id']: row for row in (raw, page, other)})
        self.assertEqual(set(failures.list_turn_assignments(records, page)), {'request', 'page'})

    def test_capacity_rows_cover_request_page_closing_and_combine(self):
        rows = [row for row in capacity.list_capacity_recovery_rows() if row['protocol'] == v7.PROTOCOL]
        final = next(row for row in rows if row['kind'] == 'final')
        self.assertEqual(final['forms'], ['request', 'page', 'closing'])
        self.assertEqual(final['handler'], 'final_v7')
        self.assertEqual(next(row for row in rows if row['kind'] == 'combine')['handler'], 'final_v7')
        # An unread final can be the first accepted output of its chain.
        from research_harness import openness_final_pages
        fixture = turn_fixtures.TurnPacketTests()
        fixture.setUp()
        fixture.native = dict(turn_fixtures.build_native(9), kind='result')
        fixture.packet['native'] = fixture.native
        with patch.object(v7, 'V7_FINAL_FORMS', ('request', 'pages')):
            _, chain = fixture.prepare()
            plan = capacity.read_chain_capacity_plan(fixture.artifacts, chain)
            inventory = json.loads(fixture.artifacts.read(chain['inventory']))
            state = json.loads(fixture.artifacts.read(chain['state']))
            self.assertEqual(state['turns'], [])
            prior = dict(fixture.value, reading_chain=chain, capacity_plan=chain['capacity_plan'],
                         capacity_plan_kind='reading', recorded_revision=1)
            packet, reference = openness_final_pages.prepare_final_packet(
                dict(review_assignment={'chain': prior}, review_attempt={}), fixture.artifacts, prior,
                fixture.route, fixture.packet, 1, inventory, state, plan, capacity.derive_plan_task_limits(plan, 'final'))
        self.assertIsNotNone(reference)
        self.assertIn('page', packet['reading_final'])

    def test_one_target_one_block_above_pinned_floor_gets_condensing_then_page(self):
        from research_harness import review_final_pages as pages, review_condensing as condensing
        turns = pages.plan_final_turns(page_room=240000, target_allowance=1, target_minimum=6700, notes=0, pool=[],
            targets=[dict(handle='t1', entries=[dict(handle='e1', characters=60001)], rows=[])],
            closing_room=240000, row_characters=360, condense=condensing.plan_condensing)
        divided = pages.split_final_turn(turns, 1, 60000, condensing.plan_condensing)
        self.assertIsNotNone(divided)
        self.assertEqual([row['kind'] for row in divided], ['combine', 'page', 'closing'])
        self.assertEqual(divided[0]['block']['entries'], ['e1'])
        floor_turns = copy.deepcopy(turns)
        floor_turns[0]['parts'][0]['entries'][0]['characters'] = 60000
        self.assertIsNone(pages.split_final_turn(floor_turns, 1, 60000, condensing.plan_condensing))
        self.assertIsNone(pages.split_final_turn(divided, divided[0]['number'], 60000, condensing.plan_condensing))
        closing = copy.deepcopy(divided)
        closing[-1]['rows'] = [dict(handle='one-combination', characters=360)]
        self.assertIsNone(pages.split_final_turn(closing, closing[-1]['number'], 60000, condensing.plan_condensing))

    def test_closing_cannot_split_two_rows_into_unanswerable_blocks(self):
        from research_harness import review_final_pages as pages, review_condensing as condensing
        turns = pages.plan_final_turns(page_room=60000, target_allowance=1, target_minimum=6700, notes=0, pool=[],
            targets=[dict(handle='t%d' % n, entries=[], rows=[]) for n in (1, 2)],
            closing_room=60000, row_characters=360, condense=condensing.plan_condensing)
        divided = pages.split_final_turn(turns, turns[-1]['number'], 60000, condensing.plan_condensing)
        self.assertIsNone(divided)
