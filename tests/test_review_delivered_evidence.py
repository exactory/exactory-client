"""Attempt-bound delivery separates citation history from current coverage."""

import copy
import importlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import test_review_staged_protocol as staged_fixtures

# Load the module before fixtures snapshot sys.modules and before state mocks.
from research_harness import direction_reviews, review_protocol as protocol, review_transport as transport
from research_harness.artifacts import ArtifactStore
from research_harness.errors import ResearchError
from research_harness.evidence import digest
from research_harness.review_input_projection import packets_from_request


class DeliveredEvidenceTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.artifacts = ArtifactStore(Path(directory.name))
        self.records = {'review_assignment': {}, 'review_attempt': {}, 'review_route': {}}
        self.route = dict(id='route', adapter='openai_responses_v1', model='fixture-model',
            endpoint='https://api.example.test/v1/responses',
            configuration={'max_output_tokens': 1000, 'timeout_seconds': 5})
        self.route['runtime'] = transport.runtime(self.route)
        self.records['review_route']['route'] = self.route

    def build_packet(self, family='problem', **fields):
        packet = dict(protocol='exactory-problem-review-v2', role='result', reviewer_id='reviewer',
            dossier_id=None, subject={'kind': 'problem', 'statement_id': 'statement'}, evidence=[])
        if family == 'study':
            packet.update(protocol='exactory-independent-review-sources-v2', dossier_id='dossier')
            packet.pop('subject')
        elif family == 'focused':
            packet.update(role='adjudicator', objection={'id': 'objection'},
                assessments=[{'id': 'review-1'}, {'id': 'review-2'}], original_evidence=[],
                supplied_source_mappings=[])
        packet.update(fields)
        packet['digest'] = digest(packet)
        return packet

    def build_message(self, packet):
        return {'role': 'user', 'content': transport.PACKET_LABEL + json.dumps(packet)}

    def record_attempt(self, packet, identifier='assignment', history=(), **fields):
        assignment = dict(id=identifier, route_id='route', route_digest=digest(self.route),
            packet=protocol._put_json(self.artifacts, packet), packet_digest=packet['digest'],
            prompt={'text': 'Assess the supplied evidence.'}, prompt_hash=digest('Assess the supplied evidence.'),
            role=packet['role'], reviewer_id=packet['reviewer_id'], dossier_id=packet['dossier_id'],
            context={}, history=list(history), session_id=identifier)
        if 'subject' in packet:
            assignment['subject'] = packet['subject']
        if packet['role'] == 'adjudicator':
            assignment['context'] = dict(objection_id='objection', review_ids=['review-1', 'review-2'])
        assignment.update(fields)
        request = transport.request_body(self.route, assignment['prompt']['text'], packet, history)
        output = {'stage': packet['role']}
        response = dict(id='response', status='completed', output=[dict(type='message', content=[
            dict(type='output_text', text=json.dumps(output))])])
        attempt = dict(id=identifier + '-attempt', assignment_id=identifier, status='completed',
            origin='instrumented', runtime=self.route['runtime'], route_digest=digest(self.route),
            packet_digest=packet['digest'], prompt_hash=assignment['prompt_hash'],
            request=protocol._put_json(self.artifacts, request), request_digest=digest(request),
            response=protocol._put_json(self.artifacts, response), output=protocol._put_json(self.artifacts, output),
            recorded_revision=len(self.records['review_attempt']) + 1)
        self.records['review_assignment'][identifier] = assignment
        self.records['review_attempt'][attempt['id']] = attempt
        self.assertTrue(protocol.authenticate_attempt(self.records, self.artifacts, assignment, attempt))
        return assignment, attempt

    def read_delivery(self, assignment, attempt):
        module = importlib.import_module('research_harness.review_delivered_evidence')
        return module.read_attempt_delivery(self.records, self.artifacts, assignment, attempt)

    def test_native_study_packet_decodes(self):
        from test_research_review_protocol import ReviewProtocolTests
        case = ReviewProtocolTests()
        self.addCleanup(case.doCleanups)
        case.setUp()
        case.dossier()
        packet = protocol.build_packet(case.records(), case.artifacts, 'dossier', 'bar', 'reviewer')
        self.assertEqual(packets_from_request({'input': [self.build_message(packet)]}), [packet])

    def test_native_focused_adjudication_packet_decodes(self):
        packet = self.build_packet('focused')
        self.assertEqual(packets_from_request({'input': [self.build_message(packet)]}), [packet])

    def test_family_identity_rejects_malformed_and_cross_family_values(self):
        for family, changes in (
                ('study', {'protocol': 'invented'}), ('study', {'dossier_id': None}),
                ('study', {'subject': {'kind': 'problem', 'statement_id': 'statement'}}),
                ('study', {'role': 'standalone'}), ('study', {'reviewer_id': ' '}),
                ('focused', {'assessments': [{'id': 'review-1'}, {'id': 'review-1'}]}),
                ('focused', {'objection': {}}), ('focused', {'digest': 'wrong'})):
            packet = dict(self.build_packet(family), **changes)
            with self.subTest(family=family, changes=changes), self.assertRaises(ResearchError) as error:
                packets_from_request({'input': [self.build_message(packet)]})
            self.assertEqual(error.exception.code, 'review_input_unreadable')

    def test_explicit_attempt_authenticates_without_selecting_current_state(self):
        assignment, attempt = self.record_attempt(self.build_packet('study'))
        before = copy.deepcopy(self.records)
        with patch.object(protocol, 'assignment_state', side_effect=AssertionError('Recursive selection')):
            delivered = self.read_delivery(assignment, attempt)
        self.assertEqual(delivered['assignment_id'], 'assignment')
        self.assertEqual(delivered['attempt_id'], 'assignment-attempt')
        self.assertEqual(delivered['request_digest'], attempt['request_digest'])
        self.assertEqual(delivered['current_input']['packets'][0]['dossier_id'], 'dossier')
        self.assertEqual(delivered['citation_history'], delivered['current_input'])
        self.assertEqual(delivered['coverage_witnesses'], delivered['current_input'])
        self.assertEqual(delivered['packet_identities'][0]['assignment_id'], 'assignment')
        self.assertEqual(self.records, before)

    def test_attempt_and_assignment_must_be_the_exact_committed_pair(self):
        assignment, attempt = self.record_attempt(self.build_packet())
        other, foreign = self.record_attempt(self.build_packet(reviewer_id='other'), 'other')
        for owner, observed in ((assignment, foreign), (other, attempt),
                (assignment, dict(attempt, request_digest='0' * 64)),
                (dict(assignment, reviewer_id='forged'), attempt),
                (assignment, dict(attempt, status='failed'))):
            with self.subTest(owner=owner['id'], attempt=observed['id']), self.assertRaises(ResearchError) as error:
                self.read_delivery(owner, observed)
            self.assertEqual(error.exception.code, 'review_invocation_unverified')

    def test_missing_altered_and_unreadable_requests_never_restore_saved_packet(self):
        assignment, attempt = self.record_attempt(self.build_packet())
        for data in (b'[]', b'{"input": []}', b'not json'):
            changed = dict(attempt, request=self.artifacts.put(data, 'application/json'))
            self.records['review_attempt'][attempt['id']] = changed
            with self.subTest(data=data), self.assertRaises(ResearchError):
                self.read_delivery(assignment, changed)
        self.records['review_attempt'][attempt['id']] = attempt
        path = self.artifacts.root / attempt['request']['path']
        path.unlink()
        with self.assertRaises(ResearchError):
            self.read_delivery(assignment, attempt)

    def test_packet_identity_requires_bound_ancestry_and_exact_subject(self):
        for change in ({'reviewer_id': 'foreign'}, {'dossier_id': 'foreign'},
                       {'subject': {'kind': 'problem', 'statement_id': 'foreign'}},
                       {'role': 'bar'}, {'digest': 'f' * 64}):
            earlier = self.build_packet(**change)
            assignment, attempt = self.record_attempt(self.build_packet(), history=[self.build_message(earlier)])
            with self.subTest(change=change), self.assertRaises(ResearchError) as error:
                self.read_delivery(assignment, attempt)
            self.assertEqual(error.exception.code, 'review_input_unreadable')

    def test_bound_history_counts_only_what_the_deciding_request_keeps(self):
        reference = self.artifacts.put(b'A body that was present in an earlier input.', 'text/plain')
        older = self.build_packet(evidence=[dict(artifact=reference, encoding='utf-8',
                                                content='A body that was present in an earlier input.')])
        prior, _ = self.record_attempt(older, 'prior')
        elided = transport._elide_history_packet(self.build_message(older))
        assignment, attempt = self.record_attempt(self.build_packet(), history=[elided],
            history_assignment_id=prior['id'])
        delivered = self.read_delivery(assignment, attempt)
        self.assertEqual(len(delivered['current_input']['packets']), 1)
        self.assertEqual(delivered['citation_history']['artifacts'], {})
        self.assertEqual(delivered['coverage_witnesses']['artifacts'], {})

    def test_explicit_history_and_repair_ancestry_both_bind_actual_packets(self):
        repaired = self.build_packet(reviewer_id='previous-reviewer')
        prior, _ = self.record_attempt(repaired, 'repair-origin')
        turn = self.build_packet(problem_evidence={'value': {'id': 'new-value'}})
        intermediate, _ = self.record_attempt(turn, 'history-turn')
        assignment, attempt = self.record_attempt(self.build_packet(),
            history=[self.build_message(repaired), self.build_message(turn)],
            history_assignment_id=intermediate['id'], context={'context_repair': {'assignment_id': prior['id']}})
        delivered = self.read_delivery(assignment, attempt)
        self.assertEqual([row['assignment_id'] for row in delivered['packet_identities']],
                         ['repair-origin', 'history-turn', 'assignment'])

    def test_descriptor_omission_and_local_existence_supply_no_body(self):
        reference = self.artifacts.put(b'Locally saved but unsent.', 'text/plain')
        for entry in (dict(artifact=reference), dict(artifact=reference, encoding='omitted', content=''),
                      dict(artifact=reference, encoding='utf-8', content='', delivery='body-elided-route-limit')):
            assignment, attempt = self.record_attempt(self.build_packet(evidence=[entry]))
            with self.subTest(entry=entry):
                delivered = self.read_delivery(assignment, attempt)
                self.assertEqual(delivered['current_input']['artifacts'], {})

    def test_text_native_attachment_and_source_mapping_keep_their_exact_extents(self):
        text = 'Only this passage.'
        excerpt = self.artifacts.put(text.encode(), 'text/plain')
        original = self.artifacts.put(b'Before. Only this passage. After.', 'text/plain')
        pdf = self.artifacts.put(b'%PDF-1.4\nNative original\n%%EOF', 'application/pdf')
        mapping = dict(version_id='version', origin=dict(artifact_descriptor=original,
            locator={'kind': 'text', 'start': 8, 'end': 26}), excerpt=excerpt)
        entries = protocol._evidence(self.artifacts, [excerpt, pdf])
        assignment, attempt = self.record_attempt(self.build_packet(evidence=entries, prior_context=[mapping]))
        delivered = self.read_delivery(assignment, attempt)['current_input']
        self.assertEqual({row['sha256'] for row in delivered['artifacts'].values()},
                         {excerpt['sha256'], pdf['sha256']})
        self.assertEqual(delivered['source_mappings'], [mapping])
        self.assertNotIn(original['sha256'], delivered['artifacts'])
        packet = self.build_packet(prior_context=[mapping])
        assignment, attempt = self.record_attempt(packet)
        self.assertEqual(self.read_delivery(assignment, attempt)['current_input']['source_mappings'], [])

    def test_focused_packet_binds_the_exact_review_pair_and_objection(self):
        for context in (dict(review_ids=['review-1', 'foreign'], objection_id='objection'),
                        dict(review_ids=['review-1', 'review-2'], objection_id='foreign')):
            assignment, attempt = self.record_attempt(self.build_packet('focused'), context=context)
            with self.subTest(context=context), self.assertRaises(ResearchError) as error:
                self.read_delivery(assignment, attempt)
            self.assertEqual(error.exception.code, 'review_input_unreadable')

    def test_digestless_record_resolution_uses_only_the_citation_view(self):
        from research_harness.review_protocol import find_carried_record
        record = {'id': 'value', 'digest': 'a' * 64, 'motivation': ['Delivered claim.']}
        older = self.build_packet(problem_evidence={'value': record})
        prior, _ = self.record_attempt(older, 'prior')
        assignment, attempt = self.record_attempt(self.build_packet(problem_evidence={'value': {'id': 'value'}}),
            history=[self.build_message(older)], history_assignment_id=prior['id'])
        view = self.read_delivery(assignment, attempt)['citation_history']
        reference = dict(kind='record', record_kind='problem_value', id='value')
        self.assertEqual(find_carried_record({}, view['carried_records'], reference, frozen=True), (record, []))
        other = self.build_packet(problem_evidence={'value': dict(record, digest='b' * 64)})
        assignment, attempt = self.record_attempt(other, history=[self.build_message(older)], history_assignment_id=prior['id'])
        rows = self.read_delivery(assignment, attempt)['citation_history']['carried_records']
        self.assertIsNone(find_carried_record({}, rows, reference, frozen=True))

    def test_selected_metadata_correction_uses_its_original_scientific_attempt(self):
        module = importlib.import_module('research_harness.review_delivered_evidence')
        assignment, attempt = self.record_attempt(self.build_packet())
        unrelated, _ = self.record_attempt(self.build_packet('study'), 'unrelated')
        self.records['review_output_correction'] = {'correction': dict(assignment_id=unrelated['id'])}
        with patch.object(protocol, 'assignment_state', return_value=dict(ready=True, attempt_id=attempt['id'],
                output_correction_id='correction')):
            delivered = module.read_selected_delivery(self.records, self.artifacts, assignment['id'])
        self.assertEqual(delivered['attempt_id'], attempt['id'])
        self.assertEqual(delivered['current_input']['packets'][0]['subject']['kind'], 'problem')

    def test_selected_delivery_requires_ready_state_and_uses_selected_attempt(self):
        module = importlib.import_module('research_harness.review_delivered_evidence')
        assignment, attempt = self.record_attempt(self.build_packet())
        with patch.object(protocol, 'assignment_state', return_value=dict(ready=True, attempt_id=attempt['id'])):
            self.assertEqual(module.read_selected_delivery(self.records, self.artifacts, assignment['id'])['attempt_id'],
                             attempt['id'])
        with patch.object(protocol, 'assignment_state', return_value=dict(ready=False, attempt_id=attempt['id'])):
            with self.assertRaises(ResearchError):
                module.read_selected_delivery(self.records, self.artifacts, assignment['id'])

    def test_staged_controller_has_selected_coverage_and_no_invented_current_request(self):
        module = importlib.import_module('research_harness.review_delivered_evidence')
        child, _ = self.record_attempt(self.build_packet(), 'child')
        controller = dict(child, id='controller', staged_review=dict(kind='controller', epoch_id='epoch'))
        self.records['review_assignment']['controller'] = controller
        packet = self.build_packet()
        graph = dict(completion={'complete': True}, packets=[(child, packet)],
                     frame_packet=self.build_packet(problem_evidence={'claim': {'statement_id': 'statement'}}))
        with patch.object(protocol, 'assignment_state', return_value=dict(ready=True)), \
                patch.object(direction_reviews, 'collect_selected_task_graph', return_value=graph):
            delivered = module.read_selected_delivery(self.records, self.artifacts, 'controller')
        self.assertEqual(delivered['current_input']['packets'], [])
        self.assertIsNone(delivered['attempt_id'])
        self.assertEqual(len(delivered['coverage_witnesses']['packets']), 2)
        self.assertEqual(delivered['coverage_witnesses']['packets'][-1]['evidence'], [])
        self.assertEqual(delivered['packet_identities'][0]['assignment_id'], 'child')

    def test_native_page_image_preserves_its_page_mapping_without_delivering_the_original(self):
        from research_harness.review_visual_inputs import control_image
        image = self.artifacts.put(control_image('01234567'), 'image/png')
        pdf = self.artifacts.put(b'%PDF-1.4\nUnsent original\n%%EOF', 'application/pdf')
        manifest = dict(protocol='pdf-page-images-v1', sources=[dict(original=pdf)],
                        pages=[dict(source_index=0, page_index=3, image=image)])
        entries = protocol._evidence(self.artifacts, [image]) + [dict(artifact=pdf, encoding='omitted')]
        assignment, attempt = self.record_attempt(self.build_packet(evidence=entries, visual_source_delivery=manifest))
        view = self.read_delivery(assignment, attempt)['current_input']
        self.assertEqual(view['packets'][0]['visual_source_delivery'], manifest)
        self.assertIn(image['sha256'], view['artifacts'])
        self.assertNotIn(pdf['sha256'], view['artifacts'])

    def test_missing_unsent_body_does_not_remove_other_actual_delivery(self):
        omitted = self.artifacts.put(b'Saved only.', 'text/plain')
        supplied = self.artifacts.put(b'Actually supplied.', 'text/plain')
        assignment, attempt = self.record_attempt(self.build_packet(evidence=[
            dict(artifact=omitted, encoding='omitted')] + protocol._evidence(self.artifacts, [supplied])))
        (self.artifacts.root / omitted['path']).unlink()
        view = self.read_delivery(assignment, attempt)['current_input']
        self.assertEqual(list(view['artifacts']), [supplied['sha256']])

    def test_legacy_series_citations_do_not_supply_refreshed_coverage(self):
        from problem_parts_fixtures import ResultPartsCase
        from test_problem_result_history import ProblemResultHistoryTests
        builder = ProblemResultHistoryTests()
        self.addCleanup(builder.doCleanups)
        ResultPartsCase.setUp(builder)
        prior = builder.record_series((2, 2))
        final = builder.assign('second-final', prior['id'])
        builder.invoke(final, builder.final_output())
        module = importlib.import_module('research_harness.review_delivered_evidence')
        records = builder.case.read_records()
        delivered = module.read_selected_delivery(records, builder.case.artifacts, final['id'])
        searches = lambda view: {row[1] or row[3].get('id') for row in view['carried_records']
                                 if row[0] == 'literature_search'}
        self.assertEqual(searches(delivered['citation_history']), {'s1-0', 's1-1', 's2-0', 's2-1'})
        self.assertEqual(searches(delivered['coverage_witnesses']), {'s2-0', 's2-1'})
        self.assertEqual({row['problem_evidence_parts']['series_assignment_id']
                          for row in delivered['coverage_witnesses']['packets']}, {'series-2'})
        # An otherwise authentic final with a broken receipt earns no part coverage.
        packet = builder.packet(final)
        packet['problem_evidence_parts']['delivered_before'][0]['attempt_id'] = 'foreign-attempt'
        packet['digest'] = digest({key: value for key, value in packet.items() if key != 'digest'})
        changed = dict(final, packet=protocol._put_json(builder.case.artifacts, packet), packet_digest=packet['digest'])
        records['review_assignment'][final['id']] = changed
        attempt = dict(records['review_attempt']['attempt-' + final['id']], packet_digest=packet['digest'])
        request = transport.request_body(records['review_route']['route'], changed['prompt']['text'], packet, changed['history'])
        attempt.update(request=protocol._put_json(builder.case.artifacts, request), request_digest=digest(request))
        from codex_route_fixtures import build_codex_response
        output = protocol._read_json(builder.case.artifacts, attempt['output'])
        attempt['response'] = protocol._put_json(builder.case.artifacts,
            build_codex_response(records['review_route']['route'], request, output))
        records['review_attempt'][attempt['id']] = attempt
        delivered = module.read_attempt_delivery(records, builder.case.artifacts, changed, attempt)
        self.assertEqual(delivered['coverage_witnesses']['packets'], [])

    def test_staged_task_delivery_remains_confined_to_its_own_authenticated_request(self):
        import sys
        from research_harness import review_worker
        builder = staged_fixtures.StagedEntryTests()
        self.addCleanup(builder.doCleanups)
        builder.setUp()
        builder.oversized()
        controller = builder.assign('controller')
        child = builder.case.assign('result', 'first', identifier='child',
            controller_assignment_id=controller['id'], task_id=controller['staged_review']['task_ids'][0])
        with patch.object(review_worker, '_entry_command',
                          return_value=[sys.executable, '-c', staged_fixtures._WORKER_SCRIPT]):
            protocol.invoke_assignment(builder.case.store, dict(id='attempt', assignment_id=child['id']),
                expected_revision=builder.case.store.revision, request_id='run-task')
        records = builder.case.read_records()
        self.assertEqual(records['review_attempt']['attempt']['status'], 'completed',
                         records['review_attempt']['attempt'].get('error'))
        module = importlib.import_module('research_harness.review_delivered_evidence')
        with patch.object(protocol, 'assignment_state', side_effect=AssertionError('Recursive selection')):
            delivered = module.read_attempt_delivery(records, builder.case.artifacts, child, records['review_attempt']['attempt'])
        self.assertEqual(len(delivered['current_input']['packets']), 1)
        self.assertEqual(delivered['current_input']['packets'][0]['staged_review']['task']['id'], child['staged_review']['task_id'])
        self.assertEqual(delivered['citation_history'], delivered['current_input'])


if __name__ == '__main__':
    unittest.main()
