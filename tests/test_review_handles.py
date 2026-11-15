"""Packet-local citations preserve native records and part-relative locators."""

import copy
import hashlib
import json
import unittest
from unittest.mock import patch

from literature_fixtures import LiteratureCase
from research_harness import research_decisions as decisions
from research_harness import review_handles as handles
from research_harness import review_protocol as protocol, review_refusal
from research_harness.errors import ResearchError
from research_harness.evidence import digest
from research_harness.source_links import read_locator
import test_research_review_protocol as fixtures
import test_research_problem_reviews as problem_fixtures


class HandleExpansionTests(LiteratureCase):
    def build_packet(self):
        first = 'The first delivered part. ' * 20
        second = '雪 The second delivered part. ' * 20
        reference = self.artifacts.put(second.encode('utf-8'), 'text/plain')
        source = dict(kind='source', link=dict(version_id='arxiv:2601.00001v1', source_id='source',
            artifact=reference, locator=dict(kind='span', start=0, end=len(second), sha256=reference['sha256'])))
        table = {name: dict(reference=reference) for name in ('e41', 'c3-2', 't1', 'h1', 'n3', 'm1')}
        table['u2'] = dict(reference=source, text=second, part=2)
        return dict(handle_table=table, capacity=dict(answer_bound=1000)), first, second

    def test_expands_every_handle_family_recursively_without_mutating_input(self):
        packet, _, _ = self.build_packet()
        packet['handle_table'].update(handles.build_gap_handles([{'code': 'missing_work'}], self.artifacts))
        output = dict(value=dict(evidence=[{'ref': name} for name in packet['handle_table']]),
                      grounds=[dict(refs=[dict(ref='g0')])])
        original = copy.deepcopy([packet, output])
        expanded = handles.expand_handles(packet, output)
        self.assertEqual(expanded['value']['evidence'], [entry['reference'] for entry in packet['handle_table'].values()])
        self.assertEqual(expanded['grounds'][0]['refs'], [packet['handle_table']['g0']['reference']])
        self.assertEqual([packet, output], original)
        self.assertEqual(handles.expand_handles(packet, expanded), expanded)

    def test_two_part_unit_span_addresses_the_second_part_from_zero(self):
        # Real shape of 20261112072628-bar-final-refused-for-part-citation-with-whole-text-offsets.
        packet, first, second = self.build_packet()
        reference = handles.expand_handles(packet, {'ref': 'u2', 'start': 0, 'end': 100})
        self.assertEqual(reference['link']['locator'], dict(kind='span', start=0, end=100,
            sha256=hashlib.sha256(second[:100].encode('utf-8')).hexdigest()))
        self.assertEqual(read_locator(self.artifacts, reference['link']['artifact'], reference['link']['locator']), second[:100])
        self.assertNotEqual(reference['link']['locator']['end'], len(first) + 100)

    def test_explicit_unit_part_selects_its_own_reference_and_relative_offsets(self):
        packet, first, second = self.build_packet()
        first_artifact = self.artifacts.put(first.encode('utf-8'), 'text/plain')
        source = copy.deepcopy(packet['handle_table']['u2']['reference'])
        source['link']['artifact'] = first_artifact
        packet['handle_table']['u2']['parts'] = {'1': dict(reference=source, text=first, part=1)}
        for part, content in ((1, first), (2, second)):
            with self.subTest(part=part):
                reference = handles.expand_handles(packet, dict(ref='u2', part=part, start=0, end=100))
                self.assertEqual(read_locator(self.artifacts, reference['link']['artifact'], reference['link']['locator']), content[:100])
        for part in (True, 0, 3, '2'):
            with self.subTest(part=part), self.assertRaises(ResearchError):
                handles.expand_handles(packet, dict(ref='u2', part=part))

    def test_refuses_unknown_malformed_and_out_of_part_spans_under_review_shape(self):
        packet, first, second = self.build_packet()
        invalid = [dict(ref='unknown'), dict(ref=[]), dict(ref='u2', start=True, end=10),
                   dict(ref='u2', start=-1, end=10), dict(ref='u2', start=10, end=10),
                   dict(ref='u2', start=0, end=len(first) + len(second)), dict(ref='u2', start=0),
                   dict(ref='u2', text='replacement'), dict(ref='m1', start=0, end=1)]
        for reference in invalid:
            with self.subTest(reference=reference), self.assertRaises(ResearchError) as caught:
                handles.expand_handles(packet, {'value': {'evidence': [reference]}})
            self.assertEqual(caught.exception.code, 'invalid_reference_handle')
            self.assertEqual(caught.exception.contract_check, 'review.shape')
            self.assertEqual(caught.exception.details['path'], '/value/evidence/0')
            self.assertEqual(caught.exception.message, handles.INVALID_REFERENCE_HANDLE_MESSAGE)

    def test_minimum_gap_handles_preserve_supplied_positions_and_duplicates(self):
        gaps = [{'code': 'unread_decisive_work', 'work_id': 'work-a'},
                {'code': 'search_missing'}, {'code': 'unread_decisive_work', 'work_id': 'work-a'}]
        table = handles.build_gap_handles(gaps, self.artifacts)
        self.assertEqual(list(table), ['g0', 'g1', 'g2'])
        for index, gap in enumerate(gaps):
            expanded = handles.expand_handles(dict(handle_table=table), dict(ref='g' + str(index)))
            self.assertEqual(json.loads(self.artifacts.read(expanded)), gap)
            self.assertEqual(expanded['sha256'], digest(gap))
            self.assertNotIn('text', expanded)
        with self.assertRaises(ResearchError):
            handles.expand_handles(dict(handle_table=table), dict(ref='g3'))

    def test_legacy_packet_keeps_handle_shaped_content_and_its_serialized_bytes(self):
        output = {'evidence': [{'ref': 'u2', 'start': 0, 'end': 100}], 'reason': 'Original 雪 text.'}
        before = json.dumps(output, ensure_ascii=False).encode('utf-8')
        self.assertIs(handles.expand_handles({}, output), output)
        self.assertEqual(json.dumps(output, ensure_ascii=False).encode('utf-8'), before)

    def test_material_parts_store_one_argument_and_final_expands_to_descriptor_only(self):
        packet = dict(handle_table={}, capacity=dict(answer_bound=100))
        for part, content in enumerate(('First argument. ', 'Second argument.'), 1):
            output = dict(stage='material', title='A retained argument', part=part, text=content, more=part == 1)
            handles.validate_material(packet, output)
            update = handles.advance_material(packet, output, self.artifacts)
            self.assertTrue(handles.authenticate_material(packet, output, update, self.artifacts))
            packet.update(update)
            if part == 1:
                self.assertEqual(update['material_request'], dict(stage='material', title='A retained argument', ref='m1', part=2))
                self.assertNotIn('m1', update['handle_table'])
        self.assertEqual(update['material_request'], dict(stage='final', ref='m1'))
        final = handles.expand_handles(packet, {'grounds': [{'refs': [{'ref': 'm1'}]}]})
        reference = final['grounds'][0]['refs'][0]
        self.assertEqual(self.artifacts.read(reference).decode(), 'First argument. Second argument.')
        self.assertEqual(set(reference), {'path', 'sha256', 'size', 'media_type'})
        self.assertNotIn('First argument', json.dumps(final))
        self.assertNotIn('Second argument', json.dumps(update))
        output = dict(stage='material', title='Another argument', part=1, text='Third.', more=False)
        handles.validate_material(packet, output)
        update = handles.advance_material(packet, output, self.artifacts)
        self.assertIn('m2', update['handle_table'])

    def test_material_accepts_eight_parts_but_refuses_a_ninth_or_more_on_eighth(self):
        packet = dict(handle_table={}, capacity=dict(answer_bound=10))
        for part in range(1, 9):
            output = dict(stage='material', title='Argument', part=part, text='x' * 10, more=part < 8)
            if part == 8:
                with self.assertRaises(ResearchError) as caught:
                    handles.validate_material(packet, dict(output, more=True))
                self.assertEqual(caught.exception.code, 'invalid_review_material')
            handles.validate_material(packet, output)
            packet.update(handles.advance_material(packet, output, self.artifacts))
        self.assertEqual(len(packet['material_state']['arguments']['m1']['parts']), 8)
        with self.assertRaises(ResearchError):
            handles.validate_material(packet, dict(output, part=9))

    def test_material_refuses_bound_shape_order_title_and_type_errors(self):
        packet = dict(handle_table={}, capacity=dict(answer_bound=10))
        output = dict(stage='material', title='Argument', part=1, text='x', more=True)
        invalid = [dict(output, part=True), dict(output, part=0), dict(output, part=2),
                   dict(output, text='x' * 11), dict(output, more=1), dict(output, title=''),
                   dict(output, text=[]), dict(output, extra=True)]
        for value in invalid:
            with self.subTest(value=value), self.assertRaises(ResearchError) as caught:
                handles.validate_material(packet, value)
            self.assertEqual(caught.exception.code, 'invalid_review_material')
            self.assertEqual(caught.exception.contract_check, 'review.shape')
        packet.update(handles.advance_material(packet, output, self.artifacts))
        for value in (dict(output, part=1), dict(output, part=2, title='Changed')):
            with self.assertRaises(ResearchError):
                handles.validate_material(packet, value)
        with self.assertRaises(ResearchError) as caught:
            handles.expand_handles(packet, dict(stage='bar', value={}))
        self.assertEqual(caught.exception.code, 'invalid_review_material')


class HandleObservationTests(LiteratureCase):
    seed = fixtures.ReviewProtocolTests.seed
    records = fixtures.ReviewProtocolTests.records
    route = fixtures.ReviewProtocolTests.route
    protocol = fixtures.ReviewProtocolTests.protocol
    run_probe = fixtures.ReviewProtocolTests.run_probe
    response = fixtures.ReviewProtocolTests.response
    assign = fixtures.ReviewProtocolTests.assign
    invoke = fixtures.ReviewProtocolTests.invoke

    def setUp(self):
        super().setUp()
        self.route()
        self.run_probe()
        self.reference = self.artifacts.put(b'A retained reviewer argument.', 'text/plain')
        self.packet_updates = dict(handle_table={'m1': dict(reference=self.reference)}, capacity=dict(answer_bound=1000))

    def assign_fixture(self, identifier='assignment', role='standalone', updates=None):
        # These fixture packets exercise the inventory/state interface that the
        # v7 builder supplies later. No production builder emits handle_table.
        build = protocol.build_packet
        def build_fixture(*args, **kwargs):
            packet = build(*args, **kwargs)
            packet.update(self.packet_updates if updates is None else updates)
            packet.pop('digest', None)
            packet['digest'] = digest(packet)
            return packet
        with patch.object(protocol, 'build_packet', side_effect=build_fixture):
            self.assign(identifier=identifier)
        assignment = self.records()['review_assignment'][identifier]
        if role != 'standalone':
            assignment = dict(assignment, role=role, prompt=protocol.canonical_prompt(role))
            assignment['prompt_hash'] = digest(assignment['prompt'])
            self.seed('review_assignment', identifier, assignment)
        return assignment

    def test_final_attempt_records_expanded_references_and_exact_answer_certificate(self):
        assignment = self.assign_fixture()
        output = dict(grounds=[dict(refs=[dict(ref='m1'), dict(ref='m1')])])
        result = self.invoke(output)['result']
        expanded = dict(grounds=[dict(refs=[self.reference, self.reference])])
        self.assertEqual(protocol._read_json(self.artifacts, result['output']), expanded)
        answer = self.response(output)['output'][0]['content'][0]['text']
        self.assertEqual(result['handle_expansion'], dict(protocol=handles.EXPANSION_PROTOCOL, references=2,
            answer_sha256=hashlib.sha256(answer.encode('utf-8')).hexdigest(), output_sha256=digest(expanded)))
        self.assertTrue(protocol.authenticate_attempt(self.records(), self.artifacts, assignment, result))
        raw = protocol.read_raw_observation(self.records(), self.artifacts, result['id'])
        self.assertEqual(json.loads(raw['answer_text']), output)
        self.assertEqual(raw['raw_output'], expanded)
        captured = next(row for row in self.records()['review_call_observation'].values()
                        if row['invocation_id'] == result['id'])
        self.assertEqual(captured['observation']['output'], result['output'])
        self.assertEqual(captured['observation']['handle_expansion'], result['handle_expansion'])
        from research_harness.record_check import check_authentication
        report = check_authentication(self.store)
        self.assertTrue(report['captures'][captured['id']]['authenticated'])

    def test_authentication_repeats_expansion_and_refuses_every_certificate_change(self):
        assignment = self.assign_fixture()
        attempt = self.invoke(dict(evidence=[dict(ref='m1')]))['result']
        changes = dict(protocol='other', references=2, answer_sha256='0' * 64, output_sha256='0' * 64,
                       extra=True)
        for name, value in changes.items():
            changed = dict(attempt, handle_expansion=dict(attempt['handle_expansion'], **{name: value}))
            with self.subTest(name=name):
                self.assertFalse(protocol.authenticate_attempt(self.records(), self.artifacts, assignment, changed))
        for value in (True, 1.0):
            changed = dict(attempt, handle_expansion=dict(attempt['handle_expansion'], references=value))
            self.assertFalse(protocol.authenticate_attempt(self.records(), self.artifacts, assignment, changed))
        missing = dict(attempt)
        del missing['handle_expansion']
        self.assertFalse(protocol.authenticate_attempt(self.records(), self.artifacts, assignment, missing))
        changed = dict(attempt, output=protocol._put_json(self.artifacts, dict(evidence=[self.reference, self.reference])))
        self.assertFalse(protocol.authenticate_attempt(self.records(), self.artifacts, assignment, changed))
        with patch.object(handles, '_expand_value', return_value=({'changed': True}, 1)) as expand:
            self.assertFalse(protocol.authenticate_attempt(self.records(), self.artifacts, assignment, attempt))
        self.assertTrue(expand.called)

    def test_captured_expansion_finishes_after_interruption_without_another_provider_call(self):
        from research_harness import review_call_capture
        self.assign_fixture()
        payload = dict(id='captured', assignment_id='assignment')
        revision = self.store.revision
        capture = review_call_capture.capture_call

        def interrupt_after_capture(*args, **kwargs):
            capture(*args, **kwargs)
            raise RuntimeError('Interrupted after capture')

        output = dict(evidence=[dict(ref='m1')])
        with patch.object(protocol.transport, 'send_request', return_value=self.response(output)), \
                patch.object(review_call_capture, 'capture_call', side_effect=interrupt_after_capture):
            with self.assertRaises(RuntimeError):
                protocol.invoke_assignment(self.store, payload, expected_revision=revision, request_id='capture-once')
        self.assertNotIn('captured', self.records().get('review_attempt', {}))
        captured = next(row for row in self.records()['review_call_observation'].values()
                        if row['invocation_id'] == 'captured')
        with patch.object(protocol.transport, 'send_request', side_effect=AssertionError('Unexpected provider call')):
            result = protocol.invoke_assignment(self.store, payload, expected_revision=revision,
                                                request_id='capture-once')['result']
        self.assertEqual(result['handle_expansion'], captured['observation']['handle_expansion'])
        self.assertEqual(protocol._read_json(self.artifacts, result['output']), dict(evidence=[self.reference]))

    def test_expansion_digest_rejects_boolean_for_integer_even_when_python_compares_equal(self):
        assignment = self.assign_fixture()
        attempt = self.invoke(dict(evidence=[dict(ref='m1')], number=1))['result']
        output = protocol._read_json(self.artifacts, attempt['output'])
        output['number'] = True
        changed = dict(attempt, output=protocol._put_json(self.artifacts, output))
        self.assertFalse(protocol.authenticate_attempt(self.records(), self.artifacts, assignment, changed))

    def test_recovered_answer_authenticates_recovery_before_expansion(self):
        assignment = self.assign_fixture()
        output = dict(evidence=[dict(ref='m1')])
        response = self.response(output)
        response['output'][0]['content'][0]['text'] += '}'
        with patch.object(protocol.transport, 'send_request', return_value=response):
            attempt = self.mutate(protocol.invoke_assignment, dict(id='recovered', assignment_id='assignment'))['result']
        self.assertIn('answer_recovery', attempt)
        self.assertIn('handle_expansion', attempt)
        self.assertTrue(protocol.authenticate_attempt(self.records(), self.artifacts, assignment, attempt))
        changed = dict(attempt, answer_recovery=dict(attempt['answer_recovery'], output_sha256='0' * 64))
        self.assertFalse(protocol.authenticate_attempt(self.records(), self.artifacts, assignment, changed))

    def test_unknown_handle_retains_raw_answer_and_records_a_repeatable_shape_refusal(self):
        assignment = self.assign_fixture()
        output = dict(evidence=[dict(ref='unknown')])
        result = self.invoke(output)
        attempt = result['result']
        self.assertEqual(result['output_refusal']['code'], 'invalid_reference_handle')
        self.assertEqual(result['output_refusal']['check'], 'review.shape')
        self.assertEqual(protocol._read_json(self.artifacts, attempt['output']), output)
        self.assertTrue(protocol.authenticate_attempt(self.records(), self.artifacts, assignment, attempt))
        self.assertEqual(review_refusal.classify_observed_output(self.records(), self.artifacts, attempt['id'])['code'], 'invalid_reference_handle')
        self.assertNotIn('handle_expansion', attempt)

    def test_unknown_handles_are_refused_before_every_role_validator(self):
        for role in protocol.ROLES:
            with self.subTest(role=role):
                self.assign_fixture(identifier=role, role=role)
                result = self.invoke(dict(evidence=[dict(ref='unknown')]),
                                     identifier='unknown-' + role, assignment=role)
                self.assertEqual(result['output_refusal']['code'], 'invalid_reference_handle')
                self.assertEqual(result['output_refusal']['check'], 'review.shape')

    def test_material_attempt_retains_part_descriptors_and_next_part_request(self):
        assignment = self.assign_fixture(updates=dict(handle_table={}, capacity=dict(answer_bound=100)))
        output = dict(stage='material', title='Argument', part=1, text='First part.', more=True)
        result = self.invoke(output)['result']
        material = protocol._read_json(self.artifacts, result['material'])
        self.assertEqual(material['material_request'], dict(stage='material', title='Argument', ref='m1', part=2))
        self.assertTrue(protocol.authenticate_attempt(self.records(), self.artifacts, assignment, result))
        changed_material = copy.deepcopy(material)
        changed_material['material_request']['part'] = 3
        changed = dict(result, material=protocol._put_json(self.artifacts, changed_material))
        self.assertFalse(protocol.authenticate_attempt(self.records(), self.artifacts, assignment, changed))
        changed = dict(result)
        del changed['material']
        self.assertFalse(protocol.authenticate_attempt(self.records(), self.artifacts, assignment, changed))
        self.assertIsNone(review_refusal.classify_observed_output(self.records(), self.artifacts, result['id']))
        self.assertEqual(protocol.assignment_state(self.records(), self.artifacts, assignment['id'])['material'], material)
        update = dict(material, capacity=dict(answer_bound=100))
        second = self.assign_fixture(identifier='second', updates=update)
        output = dict(output, part=2, text='Second part.', more=False)
        result = self.invoke(output, identifier='second-part', assignment=second['id'])['result']
        material = protocol._read_json(self.artifacts, result['material'])
        reference = material['handle_table']['m1']['reference']
        self.assertEqual(self.artifacts.read(reference), b'First part.Second part.')
        self.assertTrue(protocol.authenticate_attempt(self.records(), self.artifacts, second, result))
        self.assertEqual(handles.expand_handles(material, dict(ref='m1')), reference)
        final = self.assign_fixture(identifier='final', updates=material)
        output = dict(grounds=[dict(refs=[dict(ref='m1')])])
        result = self.invoke(output, identifier='argument-final', assignment=final['id'])['result']
        self.assertEqual(protocol._read_json(self.artifacts, result['output']),
                         dict(grounds=[dict(refs=[reference])]))
        self.assertTrue(protocol.authenticate_attempt(self.records(), self.artifacts, final, result))

    def test_invalid_material_is_retained_authenticated_and_refused(self):
        assignment = self.assign_fixture(updates=dict(handle_table={}, capacity=dict(answer_bound=10)))
        output = dict(stage='material', title='Argument', part=1, text='x' * 11, more=False)
        result = self.invoke(output)
        self.assertEqual(result['output_refusal']['code'], 'invalid_review_material')
        self.assertEqual(result['output_refusal']['check'], 'review.shape')
        self.assertNotIn('material', result['result'])
        self.assertTrue(protocol.authenticate_attempt(self.records(), self.artifacts, assignment, result['result']))

    def test_no_table_attempt_bytes_and_authentication_remain_unchanged_for_every_role(self):
        for role in protocol.ROLES:
            with self.subTest(role=role):
                assignment = self.assign_fixture(identifier=role, role=role, updates={})
                output = dict(reason='Retained original 雪 text.', evidence=[dict(ref='unknown')])
                attempt = self.invoke(output, identifier='attempt-' + role, assignment=role)['result']
                reference = protocol._put_json(self.artifacts, output)
                self.assertEqual(attempt['output'], reference)
                self.assertNotIn('handle_expansion', attempt)
                self.assertTrue(protocol.authenticate_attempt(self.records(), self.artifacts, assignment, attempt))
                self.assertEqual(handles.expand_handles(protocol._read_json(self.artifacts, assignment['packet']), output), output)


class HandleRecordingTests(unittest.TestCase):
    def test_bar_final_by_handle_records_only_expanded_native_references(self):
        case = problem_fixtures.ProblemReviewTests()
        self.addCleanup(case.doCleanups)
        case.setUp()
        case.prepare_review_route()
        case.supporting = case.artifacts.put(b'A synthetic delivered field standard.', 'text/plain')
        context = case.deliver_supporting('first')
        build = protocol.build_packet
        def build_fixture(*args, **kwargs):
            packet = build(*args, **kwargs)
            packet['handle_table'] = dict(u1=dict(reference=case.supporting))
            packet.pop('digest', None)
            packet['digest'] = digest(packet)
            return packet
        with patch.object(protocol, 'build_packet', side_effect=build_fixture):
            assignment = case.assign('bar', 'first', **context)
        output = case.build_review_output('bar')
        output['value']['evidence'] = [dict(ref='u1')]
        with patch.object(protocol.transport, 'send_request', return_value=case.build_provider_response(output)):
            attempt = case.mutate(protocol.invoke_assignment, dict(id='final', assignment_id=assignment['id']))['result']
        expanded = protocol._read_json(case.artifacts, attempt['output'])
        self.assertEqual(expanded['value']['evidence'], [case.supporting])
        saved = case.mutate(decisions.record_value_review,
            dict(expanded, id='review', dossier_id=None, assignment_id=assignment['id']))['result']
        self.assertEqual(saved['payload']['value']['evidence'], [case.supporting])
        self.assertEqual(saved['output_digest'], digest(expanded))


if __name__ == '__main__':
    unittest.main()
