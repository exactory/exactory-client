"""Native mathematical packets use fresh parts unless openness owns delivery."""

import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from codex_route_fixtures import build_codex_response, invoke_codex, limit_codex_input, record_codex_route
from openness_reading_fixtures import OpennessReadingCase
from research_harness import native_review, review_parts as parts, review_protocol as protocol
from research_harness import review_transport as transport
from research_harness.artifacts import ArtifactStore
from research_harness.errors import ResearchError
from research_harness.evidence import digest
from research_harness.review_input_projection import packets_from_request
from research_harness.review_parts_roles import find_adapter
from research_harness.storage import Store
from review_parts_fixtures import make_output


class NativePartsTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.store = Store(self.root, create=True)
        self.artifacts = ArtifactStore(self.root)
        record_codex_route(self, output_tokens=20000)
        subject = dict(statement='Every finite example satisfies the bound.')
        closure = []
        for index in range(3):
            reference = self.artifacts.put(
                (('Exact argument %d. λ\n' % index) * 3000).encode(), 'text/plain')
            closure.append(dict(digest=reference['sha256'], kind='artifact', artifact=reference))
        self.native = dict(protocol=native_review.PROTOCOL, kind='result', root=str(self.root),
            objective_id='objective', contract=dict(proof_policy='analytical'), subject=subject,
            subject_digest=digest(subject), claim_digest=digest(subject),
            reviewer=dict(source='host', actor_id='reviewer', attestation_id='review-assignment:first'),
            scientific_context=dict(scope='objective', previous_assessments=[
                dict(id='assessment', evidence_digest=closure[0]['digest'])],
                current=dict(strategies=[dict(method='finite', study_digest=closure[1]['digest'])],
                             remaining_obligation_ids=['obligation'])),
            evidence=closure, execution_provenance=[], unmaterialized_digests=[])
        self.reference = self.artifacts.put(json.dumps(self.native).encode(), 'application/json')

    def ordinary_packet(self):
        with transport.text_only_delivery(True):
            return protocol.build_packet(self.store.snapshot()['records'], self.artifacts, None,
                'native_math', 'reviewer', native_packet=self.reference)

    def limit_input(self):
        route = self.store.snapshot()['records']['review_route']['route']
        request = transport.request_body(route, protocol.canonical_prompt('native_math')['text'],
                                         self.ordinary_packet())
        from research_harness.review_problem_parts import _measure
        limit_codex_input(self, _measure(route, request) // 2)

    def assign(self, identifier='first', prior=None):
        context = dict(native_packet=self.reference)
        if prior is not None:
            context['prior_assignment_id'] = prior
        return protocol.record_assignment(self.store, dict(id=identifier, role='native_math',
            route_id='route', dossier_id=None, reviewer_id='reviewer', author_id='author', context=context),
            expected_revision=self.store.revision, request_id='assign-' + identifier)['result']

    def read_packet(self, assignment):
        return json.loads(self.artifacts.read(assignment['packet']))

    def finish_series(self):
        assignment = self.assign()
        turns = []
        for number in range(40):
            packet = self.read_packet(assignment)
            self.assertTrue('review_parts' in packet, 'An oversized native packet needs a parts series')
            if packet['review_parts']['kind'] == 'final':
                return turns, assignment
            turns.append(assignment)
            invoke_codex(self, assignment['id'], make_output(packet))
            state = protocol.assignment_state(self.store.snapshot()['records'], self.artifacts, assignment['id'])
            self.assertTrue(state['ready'], state['obligations'])
            assignment = self.assign('turn-' + str(number + 2), assignment['id'])
        self.fail('Native parts did not reach a final turn')

    def final_output(self, citations):
        return dict(schema_version=2, subject_digest=self.native['subject_digest'],
            claim_digest=self.native['claim_digest'], reviewer=copy.deepcopy(self.native['reviewer']),
            decision='approve', findings=dict(
                {key: 'Checked the exact evidence.' for key in native_review.RESULT_FINDINGS},
                openness=dict(outcome='unresolved', statement_digest=self.native['claim_digest'],
                              evidence=citations, searched_at='2026-11-03',
                              coverage='The supplied originals.', reason='No openness assertion.')))

    def test_native_adapter_enumerates_closure_and_nested_context_lists(self):
        packet = self.ordinary_packet()
        self.assertEqual(find_adapter(dict(role='native_math'), packet), 'native_math')
        units = parts.enumerate_units(packet, 'native_math')
        paths = {tuple(occurrence['path']) for unit in units for occurrence in unit['occurrences']}
        self.assertEqual(paths, {('native', 'evidence'),
            ('native', 'scientific_context', 'previous_assessments'),
            ('native', 'scientific_context', 'current', 'strategies'),
            ('native', 'scientific_context', 'current', 'remaining_obligation_ids')})
        frame = parts.build_frame(packet, units)
        self.assertEqual(frame['native']['evidence'], [])
        self.assertEqual(frame['evidence'], [])
        for key in ('subject', 'contract', 'reviewer', 'kind', 'subject_digest', 'claim_digest'):
            self.assertEqual(frame['native'][key], self.native[key])

    def test_fitting_native_packet_stays_single(self):
        assignment = self.assign()
        self.assertNotIn('review_parts', assignment)
        self.assertNotIn('reading_chain', assignment)
        self.assertEqual(self.read_packet(assignment)['native'], self.native)

    def test_series_key_binds_the_native_frame_and_leaves_lists_splittable(self):
        from research_harness.review_parts_roles import get_adapter
        adapter = get_adapter('native_math')
        packet = self.ordinary_packet()
        expected = adapter['series_key'](packet)
        added = copy.deepcopy(packet)
        added['native']['scientific_context']['previous_assessments'].append(dict(id='late-assessment'))
        self.assertEqual(adapter['series_key'](added), expected)
        for key in ('subject_digest', 'claim_digest', 'kind', 'contract', 'reviewer'):
            changed = copy.deepcopy(packet)
            changed['native'][key] = 'changed'
            with self.subTest(key=key):
                self.assertNotEqual(adapter['series_key'](changed), expected)

    def test_native_final_refuses_source_requests(self):
        packet = self.ordinary_packet()
        inventory = parts.build_inventory(packet, 'native_math')
        route = self.store.snapshot()['records']['review_route']['route']
        from research_harness.review_problem_parts import _measure
        ordinary = transport.request_body(route, protocol.canonical_prompt('native_math')['text'], packet)
        limit = _measure(route, ordinary) * 2

        def measure(value):
            prompt = parts.build_turn_prompt('native_math', {}, value['review_parts'])
            return _measure(route, transport.request_body(route, prompt['text'], value, [], order=()))

        turn = parts.plan_turn(packet, inventory, [], series_id='first', input_limit=limit,
                               measure=measure, output_tokens=20000)
        from review_parts_fixtures import observe
        observations = []
        for number in range(10):
            if parts.find_kind(turn) == 'final':
                break
            turn['_test_inventory'] = inventory
            observations.append(observe(turn))
            turn = parts.plan_turn(packet, inventory, observations, series_id='first', input_limit=limit,
                                   measure=measure, output_tokens=20000)
        self.assertEqual(parts.find_kind(turn), 'final')
        with self.assertRaises(ResearchError) as caught:
            parts.validate_output(turn, dict(kind='requests', unit_requests=[], source_requests=[
                dict(id='source', kind='inventory_page', page=0)]), inventory)
        self.assertEqual(caught.exception.code, 'invalid_value_review')

    def test_oversized_native_series_delivers_all_units_and_copies_citations(self):
        self.limit_input()
        turns, final = self.finish_series()
        records = self.store.snapshot()['records']
        final_packet = self.read_packet(final)
        inventory = parts.read_inventory(self.artifacts, final_packet['review_parts']['inventory'])
        citations = list(dict.fromkeys(citation for unit in inventory['units'] for citation in unit['citations']))
        self.assertEqual(set(citations), {entry['digest'] for entry in self.native['evidence']})
        output = self.final_output(citations)
        native_review.validate_output(self.native, output)
        invoke_codex(self, final['id'], output)
        records = self.store.snapshot()['records']
        verified = parts.verify_series(records, self.artifacts, final['id'])
        self.assertTrue(verified['complete'], verified['issues'])
        with self.assertRaises(ResearchError) as caught:
            self.assign('after-final', final['id'])
        self.assertEqual(caught.exception.code, 'review_resampling_forbidden')
        from research_harness.review_parts_native import find_final_assignment
        self.assertEqual(find_final_assignment(records, self.artifacts, 'first')['id'], final['id'])
        self.assertEqual(turns[0]['review_parts']['series_assignment_id'], 'first')
        self.assertEqual({self.read_packet(turn)['review_parts']['kind'] for turn in turns}, {'read', 'combine'})
        for turn in turns + [final]:
            self.assertEqual(turn['history'], [])
            self.assertEqual(turn['session_id'], 'first')
            native_review.require_assignment_identity(self.native, turn)
            state = protocol.assignment_state(records, self.artifacts, turn['id'])
            request = json.loads(self.artifacts.read(records['review_attempt'][state['attempt_id']]['request']))
            self.assertEqual(packets_from_request(request)[-1], self.read_packet(turn))
            from research_harness.review_problem_parts import _measure
            self.assertLessEqual(_measure(records['review_route']['route'], request),
                                 transport.local_input_limit(records['review_route']['route']))

    def test_intermediate_output_is_checked_and_final_uses_native_validator(self):
        self.limit_input()
        first = self.assign()
        packet = self.read_packet(first)
        self.assertTrue('review_parts' in packet, 'The native read turn must use parts')
        invalid = make_output(packet)
        invalid['cards'].pop()
        result = invoke_codex(self, first['id'], invalid, 'missing-card')
        self.assertEqual(result['output_refusal']['code'], 'invalid_value_review')
        self.assertEqual(result['output_refusal']['check'], 'review.shape')
        invoke_codex(self, first['id'], make_output(packet), 'valid-card')
        prior = first
        for number in range(40):
            following = self.assign('validated-' + str(number), prior['id'])
            packet = self.read_packet(following)
            if packet['review_parts']['kind'] == 'final':
                result = invoke_codex(self, following['id'], dict(decision='approve'), 'invalid-final')
                self.assertEqual(result['output_refusal']['code'], 'invalid_native_review')
                break
            invoke_codex(self, following['id'], make_output(packet))
            prior = following
        else:
            self.fail('Native parts did not reach a final')

    def test_parts_output_refusal_is_independent_of_the_assignment_role(self):
        from research_harness.review_refusal import classify_observed_output
        self.limit_input()
        first = self.assign()
        output = make_output(self.read_packet(first))
        output['cards'].pop()
        invoke_codex(self, first['id'], output, 'shared-refusal')
        records = self.store.snapshot()['records']
        for role in ('native_math', 'verification', 'adjudicator', 'manuscript'):
            changed = copy.deepcopy(records)
            changed['review_assignment'][first['id']]['role'] = role
            # Authentication is a separate boundary; use the real saved packet and output to test dispatch.
            with self.subTest(role=role), patch.object(protocol, 'authenticate_attempt', return_value=True):
                refusal = classify_observed_output(changed, self.artifacts, 'shared-refusal')
                self.assertIsNotNone(refusal)
                self.assertEqual((refusal['code'], refusal['check']), ('invalid_value_review', 'review.shape'))

    def test_native_continuation_preserves_binding_and_refuses_forks(self):
        self.limit_input()
        first = self.assign()
        invoke_codex(self, first['id'], make_output(self.read_packet(first)))
        following = self.assign('following', first['id'])
        self.assertEqual(following['history_assignment_id'], first['id'])
        self.assertEqual(following['session_id'], first['session_id'])
        self.assertEqual(following['history'], [])
        self.assertNotIn('reading_chain', following)
        self.assertEqual(following['review_parts']['series_assignment_id'], first['id'])
        with self.assertRaises(ResearchError) as caught:
            self.assign('fork', first['id'])
        self.assertEqual(caught.exception.code, 'review_source_continuation_fork')

    def test_actual_native_parts_packet_can_be_read_from_its_request(self):
        self.limit_input()
        first = self.assign()
        invoke_codex(self, first['id'], make_output(self.read_packet(first)))
        records = self.store.snapshot()['records']
        request = json.loads(self.artifacts.read(records['review_attempt']['attempt-first']['request']))
        try:
            actual = packets_from_request(request)
        except ResearchError as error:
            self.fail('The actual native parts packet must be readable: ' + error.code)
        self.assertEqual(actual, [self.read_packet(first)])
        self.assertEqual(packets_from_request(request, assignments=[first]), actual)

    def test_native_request_reader_requires_its_parts_binding(self):
        self.limit_input()
        packet = self.read_packet(self.assign())
        for field in ('header', 'minimal_header', 'protocol', 'adapter', 'reviewer', 'missing_reviewer',
                      'subject_digest', 'state_digest'):
            changed = copy.deepcopy(packet)
            if field == 'header':
                changed.pop('review_parts')
            elif field == 'minimal_header':
                changed['review_parts'] = {key: changed['review_parts'][key] for key in
                    ('protocol', 'adapter', 'kind', 'series_assignment_id', 'turn')}
            elif field in ('protocol', 'adapter'):
                changed['review_parts'][field] = 'different'
            elif field == 'reviewer':
                changed['native']['reviewer']['attestation_id'] = 'review-assignment:different'
            elif field == 'missing_reviewer':
                changed['native'].pop('reviewer')
            elif field == 'state_digest':
                changed['review_parts'][field] = '0' * 64
            else:
                changed['native']['subject_digest'] = '0' * 64
            changed.pop('digest')
            changed['digest'] = digest(changed)
            request = dict(input=[dict(role='user', content=transport.PACKET_LABEL + json.dumps(changed))])
            with self.subTest(field=field):
                with self.assertRaises(ResearchError) as caught:
                    packets_from_request(request)
                self.assertEqual(caught.exception.code, 'review_input_unreadable')

    def test_native_request_reader_refuses_another_series_or_turn(self):
        self.limit_input()
        first = self.assign()
        for field in ('series_assignment_id', 'turn', 'inventory_digest', 'kind'):
            changed = self.read_packet(first)
            changed['review_parts'][field] = {'series_assignment_id': 'another-series', 'turn': 2,
                'inventory_digest': '0' * 64, 'kind': 'check'}[field]
            if field == 'series_assignment_id':
                changed['native']['reviewer']['attestation_id'] = 'review-assignment:another-series'
            changed.pop('digest')
            changed['digest'] = digest(changed)
            bound = dict(first, packet_digest=changed['digest'])
            request = dict(input=[dict(role='user', content=transport.PACKET_LABEL + json.dumps(changed))])
            with self.subTest(field=field), self.assertRaises(ResearchError) as caught:
                packets_from_request(request, assignments=[bound])
            self.assertEqual(caught.exception.code, 'review_input_unreadable')

    def test_native_continuation_requires_the_same_packet_and_boundaries(self):
        from research_harness.review_parts_native import read_prior_assignment
        self.limit_input()
        first = self.assign()
        invoke_codex(self, first['id'], make_output(self.read_packet(first)))
        records = self.store.snapshot()['records']
        candidate = dict(first, id='continuation', context=dict(first['context'], prior_assignment_id=first['id']))
        for field in ('route_id', 'reviewer_id', 'author_id', 'dossier_id', 'native_packet'):
            changed = copy.deepcopy(candidate)
            if field == 'native_packet':
                changed['context'][field] = dict(self.reference, sha256='0' * 64)
            else:
                changed[field] = 'different'
            with self.subTest(field=field), self.assertRaises(ResearchError) as caught:
                read_prior_assignment(records, self.artifacts, changed)
            self.assertEqual(caught.exception.code, 'review_source_continuation_mismatch')
        records['review_attempt'].clear()
        with self.assertRaises(ResearchError) as caught:
            read_prior_assignment(records, self.artifacts, candidate)
        self.assertEqual(caught.exception.code, 'review_source_continuation_unverified')

    def test_first_over_cap_read_halves_allowance_after_reexport(self):
        self.limit_input()
        first = self.assign()

        def incomplete(route, request, credential):
            response = build_codex_response(route, request, {})
            response['status'] = 'incomplete'
            response['usage']['output_tokens'] = route['configuration']['max_output_tokens']
            return response

        with patch.object(transport, 'send_request', side_effect=incomplete):
            protocol.invoke_assignment(self.store, dict(id='over-cap', assignment_id=first['id']),
                credential='fixture', expected_revision=self.store.revision, request_id='over-cap')
        self.native['reviewer']['attestation_id'] = 'review-assignment:replacement'
        self.reference = self.artifacts.put(json.dumps(self.native).encode(), 'application/json')
        replacement = self.assign('replacement')
        header = self.read_packet(replacement)['review_parts']
        self.assertEqual(header['series_assignment_id'], 'replacement')
        self.assertEqual(header['plan']['unit_allowance'],
                         self.read_packet(first)['review_parts']['unit_characters'] // 2)
        self.assertNotEqual(header['series_key'], self.read_packet(first)['review_parts']['series_key'])
        native_review.require_assignment_identity(self.native, replacement)
        self.native['subject']['statement'] += ' Changed scientific inputs.'
        self.native['subject_digest'] = digest(self.native['subject'])
        self.native['reviewer']['attestation_id'] = 'review-assignment:changed-inputs'
        self.reference = self.artifacts.put(json.dumps(self.native).encode(), 'application/json')
        changed = self.read_packet(self.assign('changed-inputs'))['review_parts']
        self.assertGreater(changed['plan']['unit_allowance'], header['plan']['unit_allowance'])

    def test_native_final_request_continues_as_a_fresh_final(self):
        self.limit_input()
        turns, final = self.finish_series()
        packet = self.read_packet(final)
        header = packet['review_parts']
        inventory = parts.read_inventory(self.artifacts, header['inventory'])
        original = next(dict(unit=row['handle']) for row in inventory['units']
                        if not parts.covers_original(header['originals'], dict(unit=row['handle']), inventory))
        requested = dict(original, why='Read this original alongside the carried findings.')
        result = invoke_codex(self, final['id'], dict(kind='requests', unit_requests=[requested], source_requests=[]))
        self.assertNotIn('output_refusal', result)
        following = self.assign('answer', final['id'])
        answered = self.read_packet(following)
        self.assertEqual(answered['review_parts']['kind'], 'final')
        self.assertEqual(following['history'], [])
        self.assertTrue(parts.covers_original(answered['review_parts']['originals'], original, inventory))
        citations = [entry['digest'] for entry in self.native['evidence']]
        invoke_codex(self, following['id'], self.final_output(citations))
        verified = parts.verify_series(self.store.snapshot()['records'], self.artifacts, following['id'])
        self.assertTrue(verified['complete'], verified['issues'])

    def test_identity_rejects_another_first_turn(self):
        assignment = dict(id='later', reviewer_id='reviewer', dossier_id=None,
                          review_parts=dict(series_assignment_id='first'))
        try:
            native_review.require_assignment_identity(self.native, assignment)
        except ResearchError as error:
            self.fail('The first-turn attestation must bind later parts turns: ' + error.code)
        changed = copy.deepcopy(self.native)
        changed['reviewer']['attestation_id'] = 'review-assignment:later'
        with self.assertRaises(ResearchError) as caught:
            native_review.require_assignment_identity(changed, assignment)
        self.assertEqual(caught.exception.code, 'invalid_native_review_packet')


class NativeOpennessRoutingTests(OpennessReadingCase):
    def test_oversized_openness_minimum_keeps_the_reading_chain(self):
        reference = self.export()
        records = self.store.snapshot()['records']
        route = records['review_route']['route']
        with transport.text_only_delivery(True):
            ordinary = protocol.build_packet(records, self.artifacts, None, 'native_math',
                'openness-reviewer', native_packet=reference)
        request = transport.request_body(route, protocol.canonical_prompt('native_math')['text'], ordinary)
        from research_harness.review_problem_parts import _measure
        limit_codex_input(self, _measure(route, request) * 3 // 4)
        assignment = self.assign('turn-1', reference)
        self.assertIn('reading_chain', assignment)
        self.assertNotIn('review_parts', assignment)
