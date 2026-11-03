"""Observed focused series complete delivery before a disposition can bind."""

import copy
import json
from unittest.mock import patch

from codex_route_fixtures import invoke_codex, limit_codex_input
from research_harness import research_decisions as decisions, review_protocol as protocol
from research_harness import review_parts as parts, review_adjudicator_sources as current
from research_harness import review_transport as transport
from research_harness.evidence import digest
from research_harness.review_problem_parts import _measure
from review_parts_fixtures import PartsResultCase


class AdjudicationPartsCase(PartsResultCase):
    def setUp(self):
        super().setUp()
        self.case.add_reading_and_computation()
        self.formal_record = copy.deepcopy(self.case.expected['formal_checks'][0])
        self.case.expected['formal_checks'] = []
        for row in self.case.expected['readings']:
            for inspection in row['reading']['inspections']:
                inspection['version_id'] = inspection['link']['version_id']
        for reviewer in ('first', 'second'):
            assignment = self.case.assign('result', reviewer)
            output = self.case.build_review_output('result')
            if reviewer == 'first':
                output['objections'] = [dict(id='premise', claim='An unsupported premise.',
                    reason='The exact implication is disputed.', evidence=[self.case.supporting],
                    resolution_condition='Prove the implication.')]
            invoke_codex(self.case, assignment['id'], output)
            self.case.mutate(decisions.record_value_review, dict(output, id='review-' + assignment['id'],
                dossier_id=None, assignment_id=assignment['id']))
        self.case.put('problem_value', self.case.expected['value']['id'], self.case.expected['value'])
        self.context = dict(review_ids=['review-result-first', 'review-result-second'], objection_id='premise')

    def assign(self, identifier='focused', prior=None, reviewer='third', **context):
        return self.case.assign('adjudicator', reviewer, identifier=identifier,
            **dict(self.context, **({'prior_assignment_id': prior} if prior else {}), **context))

    def oversized(self, count=5, paragraphs=4000):
        bodies = [self.case.artifacts.put((str(index) + ' Exact evidence.\n' * paragraphs).encode(), 'text/plain')
                  for index in range(count)]
        self.case.expected['assessment_evidence'] = bodies
        original_source = protocol.bar_sources._source

        def attach_required_source_bodies(*args, **kwargs):
            source = original_source(*args, **kwargs)
            source['evidence'].extend(bodies)
            return source

        patcher = patch.object(protocol.bar_sources, '_source', side_effect=attach_required_source_bodies)
        patcher.start()
        self.addCleanup(patcher.stop)
        records = self.case.read_records()
        packet = protocol.build_packet(records, self.case.artifacts, None, 'adjudicator', 'third', **self.context)
        route = records['review_route']['route']
        prompt = protocol.canonical_prompt('adjudicator', orchestration='problem_creation')
        request = transport.request_body(route, prompt['text'], packet, [], order=())
        limit_codex_input(self, _measure(route, request) - sum(ref['size'] for ref in bodies) // 2)
        return packet

    def final_output(self, disposition='not_binding'):
        evidence = self.case.expected
        return dict(objection_id=self.context['objection_id'], disposition=disposition,
            reason='The statement, resolution criteria and value paragraphs do not rely on the contested premise.',
            evidence=[dict(kind='record', record_kind='problem_statement', id=evidence['claim']['statement_id'],
                           digest=digest(self.case.read_records()['problem_statement'][evidence['claim']['statement_id']])),
                      dict(kind='record', record_kind='problem_value', id=evidence['value']['id'],
                           digest=digest(self.case.read_records()['problem_value'][evidence['value']['id']]))],
            correction_admissible=None)

    def record_final(self, assignment, output=None):
        output = output or self.final_output()
        self.invoke(assignment, output)
        return self.case.mutate(protocol.record_adjudication,
            dict(output, id='finding-' + assignment['id'], assignment_id=assignment['id']))['result']


class AdjudicationJourneyTests(AdjudicationPartsCase):
    def test_final_validates_its_parts_contract_once(self):
        self.oversized()
        _, final = self.finish_series()
        output = self.final_output()
        self.invoke(final, output)
        records = self.case.read_records()
        attempt = records['review_attempt']['attempt-' + final['id']]
        with patch.object(parts, 'validate_turn_output', wraps=parts.validate_turn_output) as validate:
            protocol.validate_adjudication_contract(records, self.case.artifacts, final,
                self.packet(final), output, attempt=attempt)
        calls = [call for call in validate.call_args_list if call.args[2]['id'] == final['id']]
        self.assertEqual(len(calls), 1)

    def test_oversized_enters_parts_and_fitting_packet_keeps_single_contract(self):
        assignment = self.assign()
        self.assertNotIn(parts.KEY, self.packet(assignment))
        self.assertTrue(protocol._prompt_matches(assignment))
        self.record_final(assignment)
        self.assertTrue(protocol.adjudication_state(self.case.read_records(), self.case.artifacts,
                                                   'finding-focused')['ready'])

    def test_read_frame_dispatch_and_earlier_citations_reach_ready_final(self):
        for row in self.case.expected['readings']:
            row['cite'] = dict(kind='record', record_kind='reading', id=row['id'])
        self.case.put('problem_formal_check', self.formal_record['id'], self.formal_record)
        self.case.expected['formal_checks'] = [dict(self.formal_record, digest=digest(self.formal_record))]
        logical = self.oversized()
        first = self.assign()
        packet = self.packet(first)
        self.assertEqual((packet[parts.KEY]['adapter'], packet[parts.KEY]['kind']), ('problem_adjudicator', 'read'))
        self.assertEqual(first['history'], [])
        turns, final = self.finish_series(first)
        scientific = self.final_output()
        inventory = parts.read_inventory(self.case.artifacts, self.packet(final)[parts.KEY]['inventory'])
        citations = {digest(ref): ref for row in inventory['units'] for ref in row['citations']}
        scientific['evidence'].extend(citations.values())
        review = self.record_final(final, scientific)
        verified = parts.verify_series(self.case.read_records(), self.case.artifacts, final['id'])
        self.assertTrue(verified['complete'], verified['issues'])
        state = protocol.adjudication_state(self.case.read_records(), self.case.artifacts, review['id'])
        self.assertTrue(state['ready'], state['obligations'])
        self.assertEqual(decisions.find_undelivered_adjudication_keys(self.case.read_records(), self.case.artifacts,
            final['id'], version_evidence={key: logical[key] for key in protocol.VERSION_EVIDENCE_KEYS}), [])
        self.assertTrue(any(ref not in [entry['artifact'] for entry in self.packet(final)['evidence']]
                            for ref in self.case.expected['assessment_evidence']))
        self.assertGreater(len(turns), 1)

    def test_final_refuses_an_undelivered_record(self):
        self.oversized()
        _, final = self.finish_series()
        output = self.final_output()
        output['evidence'].append(dict(kind='record', record_kind='literature_search', id='never-delivered', digest='a' * 64))
        result = self.invoke(final, output)
        self.assertEqual(result['output_refusal']['code'], 'review_evidence_undelivered')
        self.assertFalse(protocol.assignment_state(self.case.read_records(), self.case.artifacts, final['id'])['ready'])

    def test_current_sources_need_complete_series_bytes_and_exact_manifest(self):
        self.oversized()
        _, final = self.finish_series()
        self.record_final(final)
        verified = parts.verify_series(self.case.read_records(), self.case.artifacts, final['id'])
        logical = parts.read_logical_packet(self.case.artifacts, self.packet(final)[parts.KEY])
        packets = copy.deepcopy(verified['packets'])
        reference = logical['current_source_delivery']['sources'][0]['evidence'][0]
        for packet in packets:
            packet['evidence'] = [entry for entry in packet['evidence']
                if entry['artifact']['sha256'] != reference['sha256']
                and entry.get('original_artifact', {}).get('sha256') != reference['sha256']]
        self.assert_code('review_current_source_incomplete', lambda: current.validate(self.case.artifacts, logical,
            has_delivery=lambda ref: parts.has_series_attachment(self.case.artifacts, packets, ref)))
        changed = copy.deepcopy(logical)
        changed['current_source_delivery']['required_units'].pop()
        self.assert_code('review_current_source_incomplete', lambda: current.validate(self.case.artifacts, changed,
            has_delivery=lambda ref: True))

    def test_intermediate_cannot_record_a_disposition_or_clear_objection(self):
        self.oversized()
        first = self.assign()
        self.invoke(first)
        output = self.final_output()
        self.assert_code('review_parts_pending', lambda: self.case.mutate(protocol.record_adjudication,
            dict(output, id='premature', assignment_id=first['id'])))
        self.assertEqual(self.case.read_records().get('review_adjudication', {}), {})

    def test_receipt_or_evidence_digest_tampering_never_readies_a_final(self):
        self.oversized()
        turns, final = self.finish_series()
        review = self.record_final(final)
        for label in ('receipt', 'evidence', 'missing read'):
            records = copy.deepcopy(self.case.read_records())
            if label == 'missing read':
                records['review_attempt'] = {key: value for key, value in records['review_attempt'].items()
                    if value['assignment_id'] != turns[0]['id']}
            else:
                packet = self.packet(final)
                if label == 'receipt':
                    packet[parts.KEY]['receipts'][0]['output_digest'] = 'forged'
                else:
                    packet['problem_evidence_digest'] = 'different'
                    packet[parts.KEY]['series_key']['evidence'] = 'different'
                records['review_assignment'][final['id']]['packet'] = self.case.artifacts.put(
                    json.dumps(packet).encode(), 'application/json')
            with self.subTest(label=label):
                self.assertFalse(protocol.adjudication_state(records, self.case.artifacts, review['id'])['ready'])

    def test_ready_parts_final_cannot_be_sampled_again(self):
        self.oversized()
        _, final = self.finish_series()
        self.record_final(final)
        self.assert_code('review_adjudicator_fixed', lambda: self.assign('resampled'))

    def test_evidence_refresh_starts_new_series_with_same_reviewer(self):
        self.oversized()
        _, final = self.finish_series()
        self.record_final(final)
        self.case.expected['value']['motivation'][0]['text'] += ' Revised payoff.'
        self.assert_code('review_adjudicator_fixed', lambda: self.assign('different-reviewer', reviewer='fourth'))
        refreshed = self.assign('refreshed')
        packet = self.packet(refreshed)
        self.assertEqual(packet[parts.KEY]['series_assignment_id'], refreshed['id'])
        self.assertEqual(packet['focused_refresh']['reason'], 'evidence_changed')
        self.assertEqual(refreshed['session_id'], final['session_id'])
        self.assertEqual(refreshed['slot_id'], final['slot_id'])

    def test_legacy_oversized_assignment_without_attempt_can_be_replaced(self):
        self.oversized()
        with patch.object(parts, 'prepare_packet', side_effect=lambda r, a, v, route, packet, p, h, **kw: packet):
            old = self.assign('legacy')
        replacement = self.assign('replacement')
        self.assertEqual(self.packet(replacement)[parts.KEY]['kind'], 'read')
        self.assertEqual(replacement['slot_id'], old.get('slot_id', old['id']))
        self.assertEqual(self.case.read_records()['review_assignment']['legacy'], old)
        self.assert_code('review_assignment_replaced', lambda: self.invoke(old, self.final_output()))

    def test_legacy_replacement_refuses_live_invocation_or_an_attempt(self):
        self.oversized()
        with patch.object(parts, 'prepare_packet', side_effect=lambda r, a, v, route, packet, p, h, **kw: packet):
            old = self.assign('legacy')
        self.case.put('review_invocation', 'live', dict(id='live', assignment_id=old['id']))
        self.assert_code('review_adjudicator_fixed', lambda: self.assign('blocked'))

    def test_premature_final_with_missing_current_body_is_refused_as_incomplete(self):
        self.oversized()
        with patch.object(parts, 'select_kind', return_value='final'):
            final = self.assign('premature-final')
        output = self.final_output()
        self.invoke(final, output)
        self.assert_code('review_current_source_incomplete', lambda: self.case.mutate(protocol.record_adjudication,
            dict(output, id='missing-source-finding', assignment_id=final['id'])))
        self.assertEqual(self.case.read_records().get('review_adjudication', {}), {})

    def assert_kept_objection(self, disposition):
        self.oversized()
        _, final = self.finish_series()
        saved = self.record_final(final, self.final_output(disposition))
        records = self.case.read_records()
        state = protocol.adjudication_state(records, self.case.artifacts, saved['id'])
        self.assertTrue(state['ready'], state['obligations'])
        obligations = decisions.assess_problem_objections(records, self.case.artifacts,
            final['subject'], [], [records['value_review']['review-result-first'],
                                  records['value_review']['review-result-second']],
            digest(self.case.expected))
        self.assertIn('research_objection_unresolved', {row['code'] for row in obligations})

    def test_upheld_never_resolves_a_material_objection(self):
        self.assert_kept_objection('upheld')

    def test_unresolved_never_resolves_a_material_objection(self):
        self.assert_kept_objection('unresolved')

    def test_a_legacy_completed_attempt_blocks_the_unrun_replacement_path(self):
        self.oversized()
        with patch.object(parts, 'prepare_packet', side_effect=lambda r, a, v, route, packet, p, h, **kw: packet):
            old = self.assign('legacy')
        attempt = copy.deepcopy(next(row for row in self.case.read_records()['review_attempt'].values()
                                     if row['assignment_id'] == 'result-first'))
        attempt.update(id='old-attempt', assignment_id=old['id'], origin='imported')
        self.case.put('review_attempt', attempt['id'], attempt)
        self.assert_code('review_adjudicator_fixed', lambda: self.assign('blocked'))

    def test_rubric_not_binding_stays_invalid_in_a_parts_final(self):
        from research_harness.problem_rubric import rubric_objection_id
        self.oversized()
        identifier = rubric_objection_id('review-result-first', 'priority')
        self.context['objection_id'] = identifier
        original = protocol.build_packet

        def rubric_packet(*args, **kwargs):
            packet = original(*args, **dict(kwargs, objection_id='premise'))
            if packet.get('role') == 'adjudicator':
                packet['rubric_definition'] = dict(item='priority', definition='Comparative priority.', threshold='>=2')
                packet['objection']['id'] = identifier
                packet['digest'] = digest({key: value for key, value in packet.items() if key != 'digest'})
            return packet

        with patch.object(protocol, 'build_packet', side_effect=rubric_packet):
            _, final = self.finish_series()
        output = dict(self.final_output(), objection_id=identifier)
        result = self.invoke(final, output)
        self.assertEqual(result['output_refusal']['code'], 'invalid_review_adjudication')

    def test_version_refresh_starts_new_series_without_mixing_cards(self):
        self.oversized()
        _, final = self.finish_series()
        self.record_final(final)
        # A new material version is supplied by the packet builder; prior observed review bytes remain immutable.
        original = protocol.build_packet

        def changed_version(*args, **kwargs):
            packet = original(*args, **kwargs)
            packet['objection']['material_version'] = 'new-version'
            packet['objection']['reason'] += ' A changed material finding.'
            packet['digest'] = digest({key: value for key, value in packet.items() if key != 'digest'})
            return packet

        with patch.object(protocol, 'build_packet', side_effect=changed_version):
            refreshed = self.assign('new-version')
        packet = self.packet(refreshed)
        self.assertEqual(packet['focused_refresh']['reason'], 'version_changed')
        self.assertEqual(packet[parts.KEY]['turn'], 1)
        self.assertEqual(packet[parts.STATE_KEY]['cards'], [])

    def test_over_cap_read_replacement_keeps_identity_slot_and_series(self):
        from codex_route_fixtures import build_codex_response
        self.oversized()
        first = self.assign()
        self.invoke(first)
        failed = self.assign('failed-read', first['id'])

        def over_cap(route, request, credential):
            response = build_codex_response(route, request, {})
            response['status'] = 'incomplete'
            response['usage']['output_tokens'] = route['configuration']['max_output_tokens']
            return response

        with patch.object(protocol.transport, 'send_request', side_effect=over_cap):
            protocol.invoke_assignment(self.case.store, dict(id='over-cap', assignment_id=failed['id']),
                credential='fixture', expected_revision=self.case.store.revision, request_id='over-cap')
        replacement = self.assign('replacement-read', first['id'])
        packet = self.packet(replacement)
        self.assertEqual(packet[parts.KEY]['series_assignment_id'], first['id'])
        self.assertEqual(replacement['slot_id'], first['slot_id'])
        self.assertEqual(packet[parts.KEY]['plan']['unit_allowance'],
                         self.packet(failed)[parts.KEY]['unit_characters'] // 2)

    def test_replaced_first_read_is_refused_before_transport(self):
        from codex_route_fixtures import build_codex_response
        self.oversized()
        failed = self.assign('failed-first')

        def over_cap(route, request, credential):
            response = build_codex_response(route, request, {})
            response['status'] = 'incomplete'
            response['usage']['output_tokens'] = route['configuration']['max_output_tokens']
            return response

        with patch.object(protocol.transport, 'send_request', side_effect=over_cap):
            protocol.invoke_assignment(self.case.store, dict(id='over-cap-first', assignment_id=failed['id']),
                credential='fixture', expected_revision=self.case.store.revision, request_id='over-cap-first')
        replacement = self.assign('replacement-first')
        self.assertEqual(replacement['slot_id'], failed['slot_id'])
        self.assertEqual(self.packet(replacement)[parts.KEY]['series_assignment_id'], replacement['id'])
        with patch.object(protocol.transport, 'send_request', side_effect=AssertionError('Obsolete dispatch')):
            self.assert_code('review_assignment_replaced', lambda: protocol.invoke_assignment(self.case.store,
                dict(id='retry-old-first', assignment_id=failed['id']), credential='fixture',
                expected_revision=self.case.store.revision, request_id='retry-old-first'))
        self.invoke(replacement)
        self.assertTrue(protocol.assignment_state(self.case.read_records(), self.case.artifacts,
                                                 replacement['id'])['ready'])

    def test_unrun_oversized_correction_replacement_retains_its_exact_scope(self):
        self.oversized()
        correction = dict(kind='omission', claim='A supplied passage was overlooked.', reason='Inspect the original.',
            evidence=[self.case.supporting], prior_adjudication_id=None, new_error_explanation=None)
        self.context['correction'] = correction
        with patch.object(parts, 'prepare_packet', side_effect=lambda r, a, v, route, packet, p, h, **kw: packet):
            old = self.assign('legacy-correction')
        replacement = self.assign('replacement-correction')
        self.assertEqual(replacement['context']['correction'], correction)
        self.assertEqual(replacement['slot_id'], old.get('slot_id', old['id']))
        self.assertEqual(self.packet(replacement)[parts.KEY]['kind'], 'read')
        self.invoke(replacement)
        continued = self.assign('continued-correction', replacement['id'])
        self.assertEqual(self.packet(continued)[parts.KEY]['series_assignment_id'], replacement['id'])

    def test_current_source_requirement_change_refreshes_the_same_adjudicator(self):
        self.oversized()
        _, final = self.finish_series()
        self.record_final(final)
        additional = self.case.artifacts.put(b'A newly required exact current source unit.', 'text/plain')
        original = protocol.bar_sources._source

        def changed_source(*args, **kwargs):
            source = original(*args, **kwargs)
            source['evidence'].append(additional)
            return source

        with patch.object(protocol.bar_sources, '_source', side_effect=changed_source):
            self.assert_code('review_adjudicator_fixed', lambda: self.assign('other-source-reviewer', reviewer='fourth'))
            refreshed = self.assign('source-refreshed')
        packet = self.packet(refreshed)
        self.assertEqual(packet['problem_evidence_digest'], self.packet(final)['problem_evidence_digest'])
        self.assertEqual(packet[parts.KEY]['series_assignment_id'], refreshed['id'])
        self.assertEqual(packet[parts.KEY]['turn'], 1)
        self.assertEqual(packet[parts.STATE_KEY]['cards'], [])
        self.assertEqual(refreshed['slot_id'], final['slot_id'])

    def test_segmented_required_source_is_credited_only_after_complete_delivery(self):
        self.oversized(count=1, paragraphs=20000)
        turns, final = self.finish_series()
        reference = self.case.expected['assessment_evidence'][0]
        entries = [entry for turn in turns for entry in self.packet(turn)['evidence']
                   if entry.get('original_artifact') == reference]
        self.assertGreater(len(entries), 1)
        self.assertTrue(all(entry['encoding'] == 'utf-8' and entry.get('segment') for entry in entries))
        output = self.final_output()
        output['evidence'].append(reference)
        saved = self.record_final(final, output)
        self.assertTrue(protocol.adjudication_state(self.case.read_records(), self.case.artifacts, saved['id'])['ready'])

    def test_continuation_inherits_focused_scope_from_its_prior_turn(self):
        self.oversized()
        first = self.assign()
        self.invoke(first)
        continued = self.case.assign('adjudicator', 'third', identifier='minimal-continuation',
                                     prior_assignment_id=first['id'])
        self.assertEqual(continued['context']['review_ids'], self.context['review_ids'])
        self.assertEqual(continued['context']['objection_id'], self.context['objection_id'])
        self.assertEqual(self.packet(continued)[parts.KEY]['series_assignment_id'], first['id'])

    def test_packet_refresh_starts_new_series_even_when_scientific_key_is_unchanged(self):
        self.oversized()
        original = protocol.build_packet

        def missing_calibrations(*args, **kwargs):
            packet = original(*args, **kwargs)
            packet['bar_calibrations'] = []
            packet['digest'] = digest({key: value for key, value in packet.items() if key != 'digest'})
            return packet

        with patch.object(protocol, 'build_packet', side_effect=missing_calibrations):
            _, final = self.finish_series()
            self.record_final(final)
        refreshed = self.assign('packet-refreshed')
        packet = self.packet(refreshed)
        self.assertEqual(packet['focused_refresh']['reason'], 'packet_incomplete')
        self.assertEqual(packet[parts.KEY]['series_key'], self.packet(final)[parts.KEY]['series_key'])
        self.assertEqual(packet[parts.KEY]['series_assignment_id'], refreshed['id'])
        self.assertEqual(packet[parts.KEY]['turn'], 1)
        self.assertEqual(packet[parts.STATE_KEY]['cards'], [])
