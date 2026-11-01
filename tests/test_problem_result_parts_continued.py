"""Owned bodies can arrive across authenticated part turns without losing item coverage."""

import copy
from unittest.mock import patch

from problem_parts_fixtures import KEY, ResultPartsCase
from research_harness import review_problem_parts as parts, review_protocol as protocol
from research_harness import review_excerpts, review_transport as transport
from research_harness.direction_sources import has_attachment


class ProblemResultPartsContinuedTests(ResultPartsCase):
    def continued_unit(self, duplicates=1, aliases=False, body_characters=4000, limit=42000):
        self.oversized(count=0, limit=limit)
        if aliases:
            body = self.case.artifacts.put(b'a' * body_characters, 'text/plain')
            references = [body, dict(body, media_type='text/plain; charset=utf-8'),
                          dict(body, media_type='text/plain; charset=ascii')]
        else:
            references = [self.case.artifacts.put(bytes([97 + i]) * body_characters, 'text/plain')
                          for i in range(3)]
        item = {'execution': {'id': 'large-execution',
                             'outputs': [{'id': str(i), 'artifact': ref} for i, ref in enumerate(references)]},
                'assessment': {'reason': 'c' * 22000}}
        self.case.expected['computations'] = [copy.deepcopy(item) for _ in range(duplicates)]
        return references

    def final_output(self):
        from research_harness.review_refusal import evidence_arrays
        output = self.case.build_review_output('result')
        reference = self.case.expected['computations'][0]['execution']['outputs'][0]['artifact']
        for array in evidence_arrays(output):
            array[:] = [copy.deepcopy(reference)]
        return output

    def sent(self, assignment):
        return review_excerpts.read_sent_packet(self.request(assignment))

    def test_unit_continues_with_pending_and_earlier_markers_then_passes_post_coverage(self):
        references = self.continued_unit()
        # The fallback must bypass the whole-unit cap as well as the request's room.
        with patch.object(parts, 'PART_UNIT_CHARACTERS', 1000):
            first = self.assign()
            first_packet = self.sent(first)
            attached = [ref for ref in references if has_attachment(self.case.artifacts, first_packet, ref)]
            self.assertGreater(len(attached), 0)
            self.assertLess(len(attached), len(references))
            self.assertEqual(first_packet[KEY]['delivered_now']['computations'], [[0, 1]])
            self.assertEqual(first_packet[KEY]['remaining']['computations'], [[0, 1]])
            for entry in first_packet['evidence']:
                if entry['artifact'] in references and entry['artifact'] not in attached:
                    self.assertEqual((entry['content'], entry['encoding'], entry['delivery']),
                                     ('', 'omitted', 'delivered-in-a-later-part-turn'))
                    self.assertEqual(entry['note'],
                                     'This artifact body is not delivered in this turn. It arrives in a later part '
                                     'turn of this review. That turn carries its unit again.')
                    self.assertNotIn('turn', entry)
            self.invoke(first)
            observed = parts.observe_series(self.case.read_records(), self.case.artifacts, first['id'])
            self.assertEqual(len(observed), 1)
            self.assertEqual(observed[0]['artifacts'],
                             [ref for ref in parts._owned_artifacts('computations', self.case.expected['computations'][0])
                              if ref in attached])
            second = self.assign('part-2', first['id'])
            second_packet = self.sent(second)
            self.assertEqual(second_packet['problem_evidence']['computations'], self.case.expected['computations'])
            self.assertEqual(second_packet[KEY]['delivered_now']['computations'], [[0, 1]])
            self.assertEqual(second_packet[KEY]['remaining']['computations'], [])
            self.assertEqual(second_packet[KEY]['delivered_before'][0]['units']['computations'], [[0, 1]])
            for entry in second_packet['evidence']:
                if entry['artifact'] in attached:
                    self.assertEqual((entry['content'], entry['encoding'], entry['delivery'], entry['turn']),
                                     ('', 'omitted', 'delivered-in-an-earlier-part-turn', 1))
                    self.assertEqual(entry['note'],
                                     'This artifact body was delivered in the earlier part turn of this review '
                                     'that the turn field of this entry names. It is not repeated in this turn. '
                                     'The notes of that turn hold what the final assessment needs from it.')
                elif entry['artifact'] in references:
                    self.assertTrue(has_attachment(self.case.artifacts, second_packet, entry['artifact']))
            self.invoke(second)
            final = self.assign('final', second['id'])
        self.assertEqual(self.packet(final)[KEY]['stage'], 'final')
        self.assertEqual(self.coverage(self.record_final(final)), [])

    def test_duplicate_units_deliver_their_items_with_only_earlier_body_entries(self):
        references = self.continued_unit(duplicates=3)
        first = self.assign()
        self.invoke(first)
        second = self.assign('part-2', first['id'])
        self.invoke(second)
        assignment = self.assign('part-3', second['id'])
        indices = []
        while self.packet(assignment)[KEY]['stage'] == 'part':
            packet = self.sent(assignment)
            indices.extend(unit['index'] for unit in parts._delivered_units(packet))
            self.assertTrue(packet['problem_evidence']['computations'])
            for entry in packet['evidence']:
                if entry['artifact'] in references:
                    self.assertEqual(entry['delivery'], 'delivered-in-an-earlier-part-turn')
                    self.assertEqual(entry['encoding'], 'omitted')
                    self.assertEqual(entry['content'], '')
                    self.assertIn(entry['turn'], (1, 2))
            self.invoke(assignment)
            assignment = self.assign('part-' + str(packet[KEY]['turn'] + 1), assignment['id'])
            self.assertLessEqual(packet[KEY]['turn'], 4)
        self.assertEqual(indices, [1, 2])
        self.assertEqual(self.coverage(self.record_final(assignment)), [])

    def test_body_identity_is_the_full_descriptor_even_for_equal_hashes(self):
        references = self.continued_unit(aliases=True)
        first = self.assign()
        packet = self.sent(first)
        attached = [ref for ref in references if has_attachment(self.case.artifacts, packet, ref)]
        self.assertGreater(len(attached), 0)
        self.assertLess(len(attached), 3)
        self.invoke(first)
        second = self.assign('part-2', first['id'])
        packet = self.sent(second)
        for reference in references:
            entry = next(row for row in packet['evidence'] if row['artifact'] == reference)
            if reference in attached:
                self.assertEqual(entry['delivery'], 'delivered-in-an-earlier-part-turn')
            else:
                self.assertTrue(has_attachment(self.case.artifacts, packet, reference))
        self.invoke(second)
        self.assertEqual(self.packet(self.assign('final', second['id']))[KEY]['stage'], 'final')

    def test_body_attached_in_an_earlier_valid_frame_is_not_delivered_again(self):
        references = self.continued_unit()
        search = {'id': 'frame-search', 'queries': ['bounded sequence'], 'reason': 's' * 24000}
        self.case.put('literature_search', search['id'], search)
        self.case.expected['openness_searches'] = [{'search': search, 'sources': []}]
        self.case.expected['citations'] = [{'artifact': references[0]}]
        first = self.assign()
        self.assertEqual(self.packet(first)['problem_evidence']['computations'], [])
        self.assertTrue(has_attachment(self.case.artifacts, self.sent(first), references[0]))
        self.invoke(first)
        observations = parts.observe_series(self.case.read_records(), self.case.artifacts, first['id'])
        self.assertIn(references[0], observations[0]['artifacts'])
        second = self.assign('part-2', first['id'])
        entry = next(row for row in self.sent(second)['evidence'] if row['artifact'] == references[0])
        self.assertEqual((entry['delivery'], entry['turn'], entry['encoding'], entry['content']),
                         ('delivered-in-an-earlier-part-turn', 1, 'omitted', ''))

    def test_part_objections_omit_only_the_two_evidence_fields_and_final_keeps_them(self):
        self.continued_unit()
        objection_body = self.case.artifacts.put(b'objection evidence only', 'text/plain')
        objection = {'id': 'inherited-objection', 'claim': 'A material limitation.', 'reason': 'Retain its identity.',
                     'material_version': '1' * 64, 'version_evidence': [objection_body],
                     'version_source_mappings': [], 'history': [{'id': 'earlier-version'}]}
        real = protocol.add_problem_objections

        def add_objection(*args):
            real(*args)
            args[2]['material_objections'] = [copy.deepcopy(objection)]

        with patch.object(protocol, 'add_problem_objections', side_effect=add_objection):
            first = self.assign()
            packet = self.packet(first)
            self.assertEqual(packet['material_objections'],
                             [{key: value for key, value in objection.items()
                               if key not in ('version_evidence', 'version_source_mappings')}])
            self.assertFalse(any(entry['artifact'] == objection_body for entry in packet['evidence']))
            _, final = self.finish_parts(first)
        final_packet = self.packet(final)
        self.assertEqual(final_packet['material_objections'], [objection])
        self.assertTrue(has_attachment(self.case.artifacts, final_packet, objection_body))

    def test_recorded_0691_prompt_authenticates_and_continuation_uses_new_suffix(self):
        self.oversized()
        canonical = protocol.canonical_prompt

        def earlier(*args, **kwargs):
            if kwargs.get('evidence_parts') == 'part':
                kwargs['part_suffix'] = parts.PART_PROMPT_0691
            return canonical(*args, **kwargs)

        with patch.object(protocol, 'canonical_prompt', side_effect=earlier):
            first = self.assign()
        self.assertNotIn('delivered-in-a-later-part-turn', first['prompt']['text'])
        self.invoke(first)
        records = self.case.read_records()
        attempt = records['review_attempt']['attempt-' + first['id']]
        self.assertTrue(protocol.authenticate_attempt(records, self.case.artifacts, first, attempt))
        self.assertTrue(protocol.assignment_state(records, self.case.artifacts, first['id'])['ready'])
        second = self.assign('new-prompt-part', first['id'])
        self.assertIn('delivered-in-a-later-part-turn', second['prompt']['text'])
        self.assertIn('In a part turn, each entry of material_objections keeps every field except version_evidence '
                      'and version_source_mappings; a part response gives no finding on an objection; the final '
                      'turn delivers every material objection with its evidence.', second['prompt']['text'])
        self.assertEqual(self.packet(second)[KEY]['turn'], 2)
        self.assertTrue(protocol._prompt_matches(second))
        tampered = copy.deepcopy(first)
        tampered['prompt']['text'] += ' '
        self.assertFalse(protocol._prompt_matches(tampered))

    def test_both_prompt_envelopes_authenticate_under_all_common_and_excerpt_versions(self):
        for rules in review_excerpts.PHASE_PROMPTS:
            for common in (1, protocol.COMMON_VERSION):
                for suffix in (parts.PART_PROMPT_0691, parts.PROMPTS['part']):
                    with self.subTest(rules=rules, common=common, earlier=suffix == parts.PART_PROMPT_0691):
                        prompt = protocol.canonical_prompt('result', orchestration='problem_creation', excerpts=True,
                            evidence_parts='part', excerpt_rules=rules, common_version=common, part_suffix=suffix)
                        assignment = {'role': 'result', 'subject': {'kind': 'problem'}, 'context': {}, 'prompt': prompt}
                        self.assertTrue(protocol._prompt_matches(assignment))
                        changed = copy.deepcopy(assignment)
                        changed['prompt']['template_hash'] = '0' * 64
                        self.assertFalse(protocol._prompt_matches(changed))

    def test_frame_and_item_alone_must_fit_and_record_nothing_on_refusal(self):
        self.continued_unit()
        self.case.expected['computations'][0]['assessment']['reason'] = 'c' * 50000
        revision = self.case.store.revision
        error = self.assert_code('review_input_too_large', self.assign)
        self.assertEqual(error.details['unit']['key'], 'computations')
        self.assertEqual(self.case.store.revision, revision)

    def test_a_fitted_item_with_no_new_body_or_completed_unit_is_refused(self):
        self.continued_unit(duplicates=2, body_characters=60000)
        # A fitted request can elide these large bodies, but that delivers no progress.
        revision = self.case.store.revision
        error = self.assert_code('review_input_too_large', self.assign)
        self.assertEqual(error.details['stage'], 'part')
        self.assertEqual(self.case.store.revision, revision)

    def test_invalid_part_response_does_not_supply_attachment_credit(self):
        self.continued_unit()
        first = self.assign()
        self.invoke(first, dict(self.output(first), notes=[]))
        self.assertEqual(parts.observe_series(self.case.read_records(), self.case.artifacts, first['id']), [])
        self.assert_code('review_source_continuation_unverified', lambda: self.assign('next', first['id']))

    def test_valid_response_without_delivery_progress_is_not_an_observed_turn(self):
        self.continued_unit(duplicates=2, body_characters=60000)
        # Construct a fitted, authenticated assignment whose item arrived without any owned body.
        with patch.object(parts, '_makes_progress', return_value=True):
            first = self.assign()
        self.invoke(first)
        records = self.case.read_records()
        self.assertTrue(protocol.assignment_state(records, self.case.artifacts, first['id'])['ready'])
        self.assertEqual(parts.observe_series(records, self.case.artifacts, first['id']), [])

    def test_tiny_bodies_can_fit_when_their_pending_markers_do_not(self):
        self.oversized()
        first = self.assign()
        self.case.expected['openness_searches'] = []
        references = [self.case.artifacts.put(str(i).encode(), 'text/plain') for i in range(24)]
        self.case.expected['computations'] = [{'execution': {'id': 'tiny-outputs', 'outputs': [
            {'id': str(i), 'artifact': reference} for i, reference in enumerate(references)]},
            'assessment': {'reason': 'c' * 30000}}]
        records, artifacts = self.case.read_records(), self.case.artifacts
        packet = protocol.build_packet(records, artifacts, None, 'result', 'first')
        units = parts.list_units(artifacts, self.case.expected)
        route = records['review_route']['route']
        prompt = protocol.canonical_prompt('result', orchestration='problem_creation', excerpts=True,
                                           evidence_parts='part')
        full = parts._project(records, artifacts, packet, units, units, [], units, 'tiny-part', 'part', 1)
        pending = parts._project(records, artifacts, packet, units, units, [], units, 'tiny-part', 'part', 1, [])
        full_size = parts._measure(route, transport.request_body(route, prompt['text'], full))
        pending_size = parts._measure(route, transport.request_body(route, prompt['text'], pending))
        self.assertLess(full_size, pending_size)
        limit = (full_size + pending_size) // 2
        value = {'id': 'tiny-part', 'role': 'result', 'context': {'prior_assignment_id': first['id']}}
        with patch.object(transport, 'CODEX_INPUT_LIMIT_CHARS', limit), patch.object(parts, 'PART_UNIT_CHARACTERS', 1000):
            planned = parts.prepare_packet(records, artifacts, value, route, packet, prompt, [])
            request = transport.request_body(route, prompt['text'], planned)
        sent = review_excerpts.read_sent_packet(request)
        self.assertLessEqual(parts._measure(route, request), limit)
        self.assertEqual(sent[KEY]['remaining']['computations'], [])
        self.assertTrue(all(has_attachment(artifacts, sent, reference) for reference in references))
