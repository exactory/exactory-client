"""Prompt-only allowance and closing regressions for the v7 final in pages."""

import json
import unittest
from unittest.mock import patch

import test_property_drivers_b as drivers
from research_harness import native_review, problem_openness, review_composition, review_prompt_paragraphs
from research_harness.errors import ResearchError


class PendingV7Allowance(drivers.V7PagesChain):
    def test_v7_pages_allowances(self):
        native = self.export_targets()
        self.assign('turn-1', native)
        _, turns = self.drive('turn-1', fill=True)
        self.assertEqual([stage for _, stage, _ in turns][-3:], ['page', 'page', 'closing'])
        self.assertEqual(turns[-1][2]['capacity']['answer_bound'], 16000)

    def test_closing_keeps_a_level_bound_below_the_schema_limit(self):
        self.assign_two_pages()
        final, turns = self.drive('turn-1')
        grant = turns[-1][2]['capacity']
        assignment = self.read_records()['review_assignment'][final]
        plan = drivers.capacity.read_chain_capacity_plan(self.artifacts, assignment)
        limits = drivers.capacity.derive_plan_task_limits(plan, 'final', [
            dict(material_allowance=grant['level_material_allowance'])])
        self.assertLess(limits['answer_bound'], 16000)
        self.assertEqual(grant['answer_bound'], limits['answer_bound'])


class V7UnreadRecoveryTests(drivers.V7PagesChain):
    text_sizes = (50000, 50000, 50000)

    def advance_from(self, head, state):
        action = self.plan(head)
        self.assertEqual(action['state'], state, action)
        return self.assign_next(action)

    def refuse_provider(self, identifier, attempt):
        build = drivers.codex_route_fixtures.build_codex_response

        def refuse(route, request, output):
            response = build(route, request, output)
            response['output'][0]['content'][0]['text'] = 'API Error: safeguards flagged this message'
            return response

        with patch.object(drivers.codex_route_fixtures, 'build_codex_response', side_effect=refuse):
            self.send_turn(identifier, failed=True, attempt_id=attempt)

    def send_named_turn(self, head):
        build = drivers.stub.build_answer

        def answer(instructions, message, **options):
            output = build(instructions, message, **options)
            packet = drivers.stub.read_packet(message)
            stage, binding = drivers.stub.read_binding(packet)
            if stage == 'reading':
                for source, text in drivers.stub.read_deliveries(packet, binding):
                    if source['kind'] == 'fulltext' and 'Theorem ' in text:
                        start = text.index('Theorem ')
                        excerpt = text[start:start + 100].splitlines()[0]
                        output['excerpts'] = [dict(unit=source['handle'], text=excerpt,
                            purpose='Check the extremal example.', targets=['t1'])]
                        break
            if stage == 'page':
                self.assertIn('finding_index', packet)
                self.assertIn('unread_summary', binding)
                for row in output['targets']:
                    if row['formulation'] == 't1':
                        entry = next(e for e in binding['entries']
                                     if e.get('status') == 'verified' and 'extremal example' in e['text'])
                        row['grounds'] = [dict(kind='reviewer_gap', text='The finite extremal example needs a search.',
                            formulation=dict(ref=row['formulation']), basis=[dict(unit=entry['unit'], text=entry['text'])],
                            need='Does the example settle this target?', unsettled='The passage gives a partial example.',
                            search=dict(terms='finite extremal example', missing_term='extremal example'))]
            return output

        with patch.object(drivers.stub, 'build_answer', side_effect=answer):
            return self.send_turn(head)

    def reach_unread_closing(self):
        from test_openness_unread_final import UnreadFinalSupport

        native = self.export_targets()
        self.assign('turn-1', native)
        (_, stage, _), _, receipt = self.send_named_turn('turn-1')
        self.assertEqual(stage, 'reading')
        self.assertNotIn('output_refusal', receipt)
        head = self.advance_from('turn-1', 'continue')
        _, assignment, packet = UnreadFinalSupport.exhaust_head(self, native, head)
        self.assertIn('unread_summary', packet['reading_final'])

        with patch.object(drivers.stub, 'build_answer', return_value={'targets': []}):
            _, _, refused = self.send_turn(assignment['id'])
        self.assertIn('output_refusal', refused)
        head = self.advance_from(assignment['id'], 'replace')
        for index in range(2):
            (packet, stage, _), _, receipt = self.send_named_turn(head)
            self.assertEqual(stage, 'page')
            if index == 0:
                self.assertEqual(packet['output_refusal']['form'], 'whole')
            self.assertNotIn('output_refusal', receipt)
            head = self.advance_from(head, 'continue')
        self.assertIn('closing', self.read_packet(head)['reading_final'])
        return head

    def finish_unread_split(self, head):
        for _ in range(2):
            (_, stage, _), _, receipt = self.send_turn(head)
            self.assertEqual(stage, 'combine')
            self.assertNotIn('output_refusal', receipt)
            head = self.advance_from(head, 'continue')
        (packet, stage, binding), attempt, receipt = self.send_turn(head)
        self.assertEqual(stage, 'closing')
        self.assertIn('finding_index', packet)
        self.assertIn('unread', binding)
        self.assertNotIn('output_refusal', receipt)
        assignment = self.read_records()['review_assignment'][head]
        self.assertIn(review_prompt_paragraphs.UNREAD_CLOSING_PARAGRAPH_VERSION,
                      assignment['prompt']['parameters']['problem_paragraphs'])
        output = json.loads(self.artifacts.read(attempt['output']))
        self.assertTrue(any(ground['kind'] == 'packet_defect' and binding['unread']['artifact'] in ground['refs']
                            for ground in output['grounds']))
        self.record_final(head, self.plan(head))
        finding = self.read_records()['problem_openness'][head]
        self.assertTrue(any(ground['kind'] == 'reviewer_gap' and ground['formulation'] == self.selected['statement']
                            for ground in finding['grounds']))
        self.assertTrue(any(ground['kind'] == 'packet_defect' for ground in finding['grounds']))

    def test_prompt_only_unread_finding_records_after_refused_page_and_two_combinations(self):
        """Nine targets permit two answerable splits and the unread provider sequence needs repeated attempts."""
        head = self.reach_unread_closing()
        self.send_turn(head, capped=True)
        self.finish_unread_split(self.advance_from(head, 'replace'))

    def test_unread_closing_notice_replans_to_a_runnable_combination(self):
        """The provider sequence and six decision rows or more are needed for a split after refusal."""
        paragraphs = review_prompt_paragraphs
        head = self.reach_unread_closing()
        answer = drivers.stub.build_answer

        def resolve_unread(instructions, message, **options):
            return dict(answer(instructions, message, **options), outcome='supported_open', grounds=[])

        with patch.object(drivers.stub, 'build_answer', side_effect=resolve_unread):
            _, attempt, receipt = self.send_turn(head)
        self.assertEqual(receipt['output_refusal']['details']['rule'], 'unread_outcome')
        head = self.advance_from(head, 'replace')
        records = self.read_records()
        retry = records['review_assignment'][head]
        retry_packet = self.read_packet(head)
        self.assertIn(paragraphs.UNREAD_CLOSING_PARAGRAPH_VERSION,
                      retry['prompt']['parameters']['problem_paragraphs'])
        notice = retry_packet['output_refusal']
        self.assertEqual(notice['form'], 'whole')
        self.assertEqual(notice['refused'][-1]['attempt_id'], attempt['id'])

        self.send_turn(head, capped=True)
        head = self.advance_from(head, 'replace')
        combine = self.read_records()['review_assignment'][head]
        packet = self.read_packet(head)
        self.assertIn('reading_combine', packet)
        self.assertEqual(packet['output_refusal'], notice)
        self.finish_unread_split(head)

        versions = combine['prompt']['parameters']['problem_paragraphs']
        self.assertIn(paragraphs.REFUSAL_PARAGRAPH_VERSIONS['whole'], versions)
        for version in (paragraphs.UNREAD_FINAL_PARAGRAPH_VERSION, paragraphs.UNREAD_CLOSING_PARAGRAPH_VERSION,
                        paragraphs.NOTES_COST_PARAGRAPH_VERSION, paragraphs.LIST_ROWS_PARAGRAPH_VERSION):
            self.assertNotIn(version, versions)
        self.assertEqual(drivers.protocol.find_common_prompt_version(combine['prompt']),
                         drivers.protocol.find_common_prompt_version(retry['prompt']))
        self.assertTrue(drivers.v7.matches_turn_prompt(combine, packet))


class V7ClosingStubTests(unittest.TestCase):
    def test_indexed_unread_closing_stub_cites_the_unread_set(self):
        packet = dict(finding_index={}, reading_final=dict(closing=dict(rows=[]), unread=dict(count=1)))
        instructions = 'Include a packet_defect ground that cites x1.'
        answer = drivers.stub.build_answer(instructions, 'Review packet\n' + json.dumps(packet))
        self.assertEqual(answer['outcome'], 'unresolved')
        [ground] = answer['grounds']
        self.assertEqual((ground['kind'], ground['refs']), ('packet_defect', [dict(ref='x1')]))


class V7ClosingTests(drivers.V7PagesChain):
    def test_decision_rows_offer_citable_handles_when_an_excerpt_matches_notes(self):
        native = self.export_targets(2)
        self.assign_two_pages(native=native)
        note_handles = []

        def answer(current, packet):
            stage = drivers.reading.find_stage(packet)
            if stage == 'reading' and not note_handles:
                deliveries = packet['reading_turn']['plan']['now']
                body = next((body for row, body in zip(deliveries, packet['evidence'])
                             if row['kind'] == 'fulltext'), None)
                if body is not None:
                    result = self.build_v7_answer(packet)
                    result['notes'] = body['content'][:40]
                    result['excerpts'] = [dict(unit=body['handle'], text=result['notes'],
                                               purpose='The same text as the notes.', targets=['t1'])]
                    note_handles.append('n%d' % packet['reading_turn']['turn'])
                    return result
            elif stage == 'page' and packet['reading_final']['page']['targets'] == ['t1']:
                [note] = note_handles
                assignment = self.read_records()['review_assignment'][current]
                table = drivers.v7.build_handle_packet(packet, self.artifacts, assignment)['handle_table']
                self.assertTrue(any(handle.startswith('e') and entry['reference'] == table[note]['reference']
                                    for handle, entry in table.items()))
                return dict(targets=[dict(formulation='t1', coverage='Read.', sentence='A gap remains.',
                    grounds=[dict(kind='own_argument', text='The notes identify the remaining gap.', refs=[dict(ref=note)])])])
            elif stage == 'closing':
                [note] = note_handles
                row = next(row for row in packet['reading_final']['closing']['rows'] if row[0] == 't1')
                [listed] = row[3]
                self.assertTrue(listed.startswith('n'), row)
                assignment = self.read_records()['review_assignment'][current]
                table = drivers.v7.build_handle_packet(packet, self.artifacts, assignment)['handle_table']
                self.assertEqual(table[listed]['reference'], table[note]['reference'])
                return dict({name: 'Assessed.' for name in native_review.RESULT_FINDINGS}, outcome='unresolved',
                    reason='The remaining gap is in the notes.',
                    grounds=[dict(kind='own_argument', text='The notes hold the grounds.', refs=[dict(ref=listed)])])
            return None

        final, _ = self.finish_pages(native, assign=False, before_answer=answer)
        self.assert_recorded_composite(final)

    def test_refused_recording_after_a_closing_answer_gives_an_export_handoff(self):
        native = self.export_targets(2)
        self.assign_two_pages(native=native)
        final, _ = self.finish_pages(native, assign=False)
        records = self.read_records()
        attempt = records['review_attempt']['attempt-' + final]
        answer = json.loads(self.artifacts.read(attempt['output']))
        self.assertEqual(answer['outcome'], 'unresolved')
        self.assertNotIn('findings', answer)
        refusal = ResearchError('openness_packet_outdated', 'The recorded packet needs a fresh export.')
        with patch.object(problem_openness, 'prepare_openness', side_effect=refusal):
            action = self.plan(final)
        self.assertEqual((action['state'], action['code'], action['command']),
                         ('handoff', refusal.code, 'export'))


class V7DecisionRowsTests(unittest.TestCase):
    def test_decision_row_prefers_a_notes_handle_over_an_identical_excerpt(self):
        from openness_named_gap_fixtures import build_packet
        packet = build_packet(form='page')
        reference = {'sha256': 'a' * 64}
        packet['handle_table'] = {handle: dict(reference=reference) for handle in ('n1', 'e1')}
        pages = [dict(output=dict(targets=[dict(formulation='t1', coverage='Read.', sentence='A gap remains.',
            grounds=[dict(kind='own_argument', text='The notes establish the gap.', refs=[reference])])]))]
        rule = review_composition.find_rule('native_math', packet)
        [row] = review_composition.build_decision_rows(rule, packet, pages)
        self.assertEqual(row[3], ['n1'])
