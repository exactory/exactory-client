"""A problem openness review whose full texts exceed the route input reads them over several turns."""

import json
from unittest.mock import patch

from openness_reading_fixtures import OpennessReadingCase, build_theorem
from research_harness import openness_reading, review_excerpts, review_protocol as protocol
from research_harness import review_transport as transport
from research_harness import review_codex_transport as codex
from research_harness import review_refusal


class OpennessReadingChainTests(OpennessReadingCase):
    def complete_text(self, entry, version):
        body = self.artifacts.read(self.texts[version]).decode()
        return entry.get('encoding') == 'utf-8' and entry.get('content') == body

    def delivered_versions(self, sent):
        entries = {entry['artifact']['sha256']: entry for entry in sent['evidence']}
        return [version for version in self.versions
                if self.texts[version]['sha256'] in entries
                and self.complete_text(entries[self.texts[version]['sha256']], version)]

    def record_openness(self, identifier):
        return self.mutate(self.load_problem_module().record_openness, {'assignment_id': identifier})['result']

    def test_an_openness_packet_above_the_route_limit_becomes_a_reading_chain(self):
        self.limit_input()
        assignment = self.assign('turn-1', self.export())
        self.assertEqual(assignment['reading_chain'], {'first_assignment_id': 'turn-1', 'turn': 1})
        self.assertEqual((assignment['history'], assignment['session_id']), ([], 'turn-1'))
        self.assertEqual(assignment['prompt']['template_id'], openness_reading.PROTOCOL + '/reading')
        self.assertEqual(assignment['prompt'], protocol.canonical_prompt('native_math', reading_stage='reading'))
        packet = self.read_packet('turn-1')
        self.assertNotIn('native', packet)
        turn = packet['reading_turn']
        native = json.loads(self.artifacts.read(assignment['context']['native_packet']))
        for key in ('subject_digest', 'claim_digest', 'reviewer'):
            self.assertEqual(turn[key], native[key])
        self.assertEqual(turn['statement'], self.selected)
        self.assertEqual(turn['formulations'], {'statement': self.selected['statement'], 'neighbours': []})
        plan = turn['reading_plan']
        self.assertEqual(plan['turn'], 1)
        self.assertEqual([(row['kind'], row['version_id']) for row in plan['delivered_now']],
                         [('fulltext', version) for version in self.versions[:2]])
        self.assertEqual(plan['delivered_before'], [])
        self.assertEqual([(row['kind'], row.get('version_id'), row.get('record_kind'), row.get('record_id'))
                          for row in plan['remaining']],
                         [('fulltext', self.versions[2], None, None), ('record', None, 'literature_search',
                                                                       self.search['id'])]
                         + [('record', None, 'reading', 'read-%d' % number) for number in (1, 2, 3)]
                         + [('record', None, 'problem_statement', self.selected['id'])])
        self.assertEqual(turn['reading_record'], {'protocol': openness_reading.PROTOCOL, 'turns': [], 'sources': [],
                                                  'excerpts': [], 'passages': [],
                                                  'allowances': {'excerpts': {'characters': 200000, 'used': 0},
                                                                 'passages': {'characters': 100000, 'used': 0}}})
        # Only the units of this turn are evidence; the records of the packet follow the full texts as units.
        self.assertEqual(sorted(entry['artifact']['sha256'] for entry in packet['evidence']),
                         sorted(self.texts[version]['sha256'] for version in self.versions[:2]))

    def test_batches_follow_the_text_priority_and_deliver_every_body(self):
        self.limit_input()
        native_packet = self.export(priority=[self.versions[2], self.versions[0]])
        assignment = self.assign('turn-1', native_packet)
        plan = self.read_packet('turn-1')['reading_turn']['reading_plan']
        self.assertEqual([row['version_id'] for row in plan['delivered_now']], [self.versions[2], self.versions[0]])
        self.assertEqual([row['version_id'] for row in plan['remaining'] if row['kind'] == 'fulltext'],
                         [self.versions[1]])
        records = self.store.snapshot()['records']
        route = records['review_route']['route']
        with transport.text_only_delivery(True):
            ordinary = protocol.build_packet(records, self.artifacts, None, 'native_math', 'openness-reviewer',
                                             native_packet=native_packet)
            rebuilt, _ = openness_reading.prepare_packet(records, self.artifacts, assignment, route, ordinary)
        self.assertEqual(json.dumps(rebuilt, sort_keys=True), json.dumps(self.read_packet('turn-1'), sort_keys=True))
        self.invoke('turn-1', self.build_reading_response(self.read_packet('turn-1')))
        request, sent = self.read_sent_packet('turn-1')
        self.assertLessEqual(len(codex.packet_text(request)), transport.CODEX_INPUT_LIMIT_CHARS)
        self.assertEqual(self.delivered_versions(sent), sorted([self.versions[2], self.versions[0]]))
        state = protocol.assignment_state(self.store.snapshot()['records'], self.artifacts, 'turn-1')
        self.assertTrue(state['ready'], state['obligations'])

    def test_a_malformed_reading_response_is_refused_and_the_turn_runs_again(self):
        self.limit_input()
        native_packet = self.export()
        self.assign('turn-1', native_packet)
        packet = self.read_packet('turn-1')
        malformed = dict(self.build_reading_response(packet), notes='')
        self.invoke('turn-1', malformed)
        refusal = self.store.snapshot()['records']['review_output_refusal']['attempt-turn-1']
        self.assertEqual((refusal['code'], refusal['check']), ('invalid_reading_response', 'native.output'))
        short_anchor = {'id': 'p', 'version_id': self.versions[0], 'anchor': 'short', 'characters': 10}
        for change in ({'stage': 'final'}, {'claim_digest': '0' * 64}, {'notes': 'x' * 20001},
                       {'passage_requests': [short_anchor]}):
            with self.subTest(change=change), self.assertRaises(Exception) as raised:
                openness_reading.validate_turn_output(packet, dict(self.build_reading_response(packet), **change))
            self.assertEqual(raised.exception.code, 'invalid_reading_response')
        self.assert_error('review_source_continuation_unverified',
                          lambda: self.assign('turn-2', native_packet, prior='turn-1'))
        self.invoke('turn-1', self.build_reading_response(packet), attempt='attempt-turn-1-retry')
        state = protocol.assignment_state(self.store.snapshot()['records'], self.artifacts, 'turn-1')
        self.assertTrue(state['ready'], state['obligations'])
        self.assertEqual(state['attempt_id'], 'attempt-turn-1-retry')

    def test_short_anchor_refusal_names_request_and_rule(self):
        self.limit_input()
        native = self.export()
        self.assign('turn-1', native)
        packet = self.read_packet('turn-1')
        passage = {'id': 'lemma-35', 'version_id': self.versions[0],
                   'anchor': 'Lemma 35. Let Wℓ,m', 'characters': 100}
        output = self.build_reading_response(packet, passages=[passage])
        receipt = self.invoke('turn-1', output)
        records = self.store.snapshot()['records']
        refusal = records['review_output_refusal']['attempt-turn-1']
        expected = {'message': 'A passage anchor is a verbatim phrase of at least 20 characters',
                    'path': '/passage_requests/0/anchor', 'request_id': 'lemma-35',
                    'normalized_characters': 18, 'minimum_characters': 20}
        self.assertEqual(refusal['details'], expected)
        self.assertEqual(receipt['output_refusal'], {key: refusal[key] for key in ('code', 'check', 'details')})
        attempt = records['review_attempt']['attempt-turn-1']
        saved = self.artifacts.read(attempt['output'])
        self.assertEqual(json.loads(saved), output)
        self.assertTrue(protocol.authenticate_attempt(records, self.artifacts,
                                                      records['review_assignment']['turn-1'], attempt))
        self.assert_error('review_source_continuation_unverified',
                          lambda: self.assign('turn-2', native, prior='turn-1'))
        accepted = self.build_reading_response(packet)
        self.invoke('turn-1', accepted, attempt='attempt-turn-1-retry')
        self.assign('turn-2', native, prior='turn-1')
        turn = self.read_packet('turn-2')['reading_turn']['reading_record']['turns'][0]
        self.assertEqual(turn['attempt_id'], 'attempt-turn-1-retry')
        self.assertEqual(self.artifacts.read(attempt['output']), saved)

    def test_historical_null_refusal_remains_immutable(self):
        self.limit_input()
        self.assign('turn-1', self.export())
        output = dict(self.build_reading_response(self.read_packet('turn-1')), notes='')
        legacy = {'code': 'invalid_reading_response', 'check': 'native.output', 'details': None}
        with patch.object(review_refusal, 'classify_observed_output', return_value=legacy):
            receipt = self.invoke('turn-1', output)
        before = self.store.snapshot()['records']
        fresh = review_refusal.classify_observed_output(before, self.artifacts, 'attempt-turn-1')
        self.assertEqual(fresh['details']['path'], '/notes')
        self.assertIn('message', fresh['details'])
        self.assertEqual(self.invoke('turn-1', output), receipt)
        self.assertEqual(self.store.snapshot()['records'], before)
        retry = self.invoke('turn-1', output, attempt='attempt-turn-1-retry')
        after = self.store.snapshot()['records']
        self.assertEqual(after['review_output_refusal']['attempt-turn-1'],
                         before['review_output_refusal']['attempt-turn-1'])
        self.assertIsNone(receipt['output_refusal']['details'])
        self.assertIsNotNone(retry['output_refusal']['details'])
        first, second = [after['review_attempt'][identifier] for identifier in
                         ('attempt-turn-1', 'attempt-turn-1-retry')]
        self.assertEqual(self.artifacts.read(first['request']), self.artifacts.read(second['request']))
        self.assert_error('review_slot_exhausted',
                          lambda: self.invoke('turn-1', output, attempt='attempt-turn-1-third'))

    def test_the_final_turn_carries_the_reading_record(self):
        self.limit_input()
        native_packet = self.export(priority=self.versions)
        turns = self.run_reading_turns(native_packet, turn_count=8)
        second = self.read_packet('turn-2')['reading_turn']
        self.assertEqual([row['version_id'] for row in second['reading_plan']['delivered_before']], self.versions[:2])
        self.assertEqual([row['version_id'] for row in second['reading_plan']['delivered_now']
                          if row['kind'] == 'fulltext'], self.versions[2:])
        final = turns[-1]
        assignment = self.store.snapshot()['records']['review_assignment'][final]
        self.assertEqual(assignment['reading_chain'], {'first_assignment_id': 'turn-1', 'turn': len(turns)})
        self.assertEqual(assignment['prompt']['template_id'], openness_reading.PROTOCOL + '/final')
        self.assertTrue(assignment['prompt']['text'].startswith(protocol.canonical_prompt('native_math')['text']))
        packet = self.read_packet(final)
        native = json.loads(self.artifacts.read(native_packet))
        self.assertEqual(packet['native'], native)
        record = packet['reading_record']
        self.assertEqual([(row['assignment_id'], row['attempt_id']) for row in record['turns']],
                         [(identifier, 'attempt-' + identifier) for identifier in turns[:-1]])
        self.assertEqual([unit for row in record['turns'] for unit in row['delivered_units']
                          if unit['kind'] == 'fulltext'],
                         [self.unit_identity(version) for version in self.versions])
        self.assertEqual([unit for row in record['turns'][:2] for unit in row['delivered_units']][:3],
                         [self.unit_identity(version) for version in self.versions])
        self.assertTrue(all(row['notes'] for row in record['turns']))
        self.assertEqual([(row['status'], row['version_id']) for row in record['excerpts']],
                         [('verified', version) for version in self.versions])
        self.assertEqual([row['text'] for row in record['excerpts']], [build_theorem(number) for number in (1, 2, 3)])
        self.assertEqual(record['allowances']['excerpts']['used'], sum(len(build_theorem(n)) for n in (1, 2, 3)))
        self.assertEqual(record['passages'], [])
        records = self.store.snapshot()['records']
        route = records['review_route']['route']
        request = transport.request_body(route, assignment['prompt']['text'], packet)
        self.assertLessEqual(len(codex.packet_text(request)), transport.CODEX_INPUT_LIMIT_CHARS)

    def test_a_final_turn_with_passage_requests_leads_to_a_further_final_turn(self):
        self.limit_input()
        native_packet = self.export(priority=self.versions)
        final = self.run_reading_turns(native_packet)[-1]
        anchor = build_theorem(2)[:40]
        passage = {'id': 'theorem-2', 'version_id': self.versions[1], 'anchor': anchor, 'characters': 300}
        self.invoke(final, self.build_reading_response(self.read_packet(final), excerpts=[], passages=[passage]))
        records = self.store.snapshot()['records']
        self.assertNotIn('attempt-' + final, records.get('review_output_refusal', {}))
        further = self.assign('turn-further', native_packet, prior=final)
        self.assertEqual(further['reading_chain'],
                         {'first_assignment_id': 'turn-1', 'turn': int(final.rsplit('-', 1)[1]) + 1})
        self.assertEqual(further['prompt']['template_id'], openness_reading.PROTOCOL + '/final')
        record = self.read_packet('turn-further')['reading_record']
        [answer] = record['passages']
        body = self.artifacts.read(self.texts[self.versions[1]]).decode()
        start = body.index(build_theorem(2))
        self.assertEqual(((answer['assignment_id'], answer['request_id']), answer['status'], answer['text']),
                         ((final, 'theorem-2'), 'delivered', body[start:start + 300]))
        self.assertEqual(record['allowances']['passages']['used'], 300)
        self.assertEqual(record['turns'][-1]['assignment_id'], final)
        # An earlier final turn lists the units whose bodies its actual request carried complete.
        _, sent = self.read_sent_packet(final)
        complete = review_excerpts.find_complete_bodies(sent)
        native = json.loads(self.artifacts.read(native_packet))
        rows = openness_reading.list_reading_rows(records, self.artifacts, native)
        self.assertEqual(record['turns'][-1]['delivered_units'],
                         [{key: row[key] for key in row if key != 'artifact'} for row in rows
                          if row['artifact']['sha256'] in complete])
        self.assertTrue(all(unit['kind'] == 'record' for unit in record['turns'][-1]['delivered_units']))
        self.invoke('turn-further', self.build_native_result(self.read_packet('turn-further')))
        saved = self.record_openness('turn-further')
        chain = ['turn-%d' % number for number in range(1, int(final.rsplit('-', 1)[1]) + 1)]
        self.assertEqual([row['assignment_id'] for row in saved['reading_turns']], chain + ['turn-further'])

    def test_a_final_reading_answer_needs_a_passage_request_and_passage_allowance(self):
        self.limit_input()
        final = self.run_reading_turns(self.export(priority=self.versions))[-1]
        packet = self.read_packet(final)
        passage = {'id': 'theorem-1', 'version_id': self.versions[0], 'anchor': build_theorem(1)[:40], 'characters': 90}
        asking = self.build_reading_response(packet, excerpts=[], passages=[passage])
        openness_reading.validate_turn_output(packet, asking)
        openness_reading.validate_turn_output(packet, self.build_native_result(packet))
        exhausted = json.loads(json.dumps(packet))
        exhausted['reading_record']['allowances']['passages']['used'] = 100000
        reading = self.read_packet('turn-1')
        for checked, output in ((packet, self.build_reading_response(packet, excerpts=[])),
                                (exhausted, asking),
                                (reading, self.build_native_result(packet))):
            with self.assertRaises(Exception) as raised:
                openness_reading.validate_turn_output(checked, output)
            self.assertEqual(raised.exception.code, 'invalid_reading_response')

    def test_problem_openness_records_the_reading_turns_and_rechecks_them(self):
        from research_harness.problem_openness import assess_openness
        self.limit_input()
        turns = self.run_reading_turns(self.export(priority=self.versions), turn_count=8)
        final = turns[-1]
        self.assert_error('openness_reading_incomplete', lambda: self.record_openness('turn-2'))
        self.invoke(final, self.build_native_result(self.read_packet(final)))
        saved = self.record_openness(final)
        names = ('kind', 'version_id', 'text_sha256', 'record_kind', 'record_id')
        expected = [{'assignment_id': identifier, 'attempt_id': 'attempt-' + identifier,
                     'delivered_units': [{key: row[key] for key in names if key in row} for row in
                                         self.read_packet(identifier)['reading_turn']['reading_plan']['delivered_now']]}
                    for identifier in turns[:-1]]
        self.assertEqual(saved['reading_turns'][:-1], expected)
        self.assertEqual(saved['reading_turns'][-1]['assignment_id'], final)
        self.assertEqual(saved['reading_turns'][0]['delivered_units'],
                         [self.unit_identity(version) for version in self.versions[:2]])
        self.assertEqual(saved['outcome'], 'supported_open')
        self.assertEqual(assess_openness(self.store.snapshot()['records'], self.artifacts), [])
        self.mutate(protocol.record_context_event, {'id': 'exposure', 'assignment_id': 'turn-1',
            'kind': 'contamination', 'source': 'author_history', 'reason': 'The first reading turn saw an author note.',
            'evidence': [self.texts[self.versions[0]]]})
        [stale] = assess_openness(self.store.snapshot()['records'], self.artifacts)
        self.assertEqual(stale['code'], 'problem_openness_stale')

    def test_a_chain_that_missed_a_text_is_refused(self):
        self.limit_input()
        native_packet = self.export(priority=self.versions)
        real = openness_reading.list_reading_texts

        def drop_last(records, native):
            return real(records, native)[:-1]
        with patch.object(openness_reading, 'list_reading_texts', side_effect=drop_last), \
                self.omit_final_body(self.texts[self.versions[2]]['sha256']):
            final = self.run_reading_turns(native_packet, turn_count=8)[-1]
        self.invoke(final, self.build_native_result(self.read_packet(final)))
        with self.assertRaises(Exception) as raised:
            self.record_openness(final)
        self.assertEqual((raised.exception.code, raised.exception.details['version_ids']),
                         ('openness_reading_incomplete', [self.versions[2]]))
        self.assertEqual(raised.exception.details['units'], [self.unit_identity(self.versions[2])])

    def test_a_packet_that_fits_stays_single_turn_and_records_as_before(self):
        native_packet = self.export()
        assignment = self.assign('turn-1', native_packet)
        self.assertNotIn('reading_chain', assignment)
        self.assertEqual(assignment['prompt'], protocol.canonical_prompt('native_math'))
        packet = self.read_packet('turn-1')
        self.assertEqual(sorted(packet),
                         ['digest', 'dossier_id', 'evidence', 'native', 'protocol', 'reviewer_id', 'role'])
        self.invoke('turn-1', self.build_native_result(packet))
        saved = self.record_openness('turn-1')
        self.assertNotIn('reading_turns', saved)
        self.assert_error('review_source_continuation_mismatch',
                          lambda: self.assign('turn-2', native_packet, prior='turn-1'))

    def test_a_final_turn_that_does_not_fit_names_its_parts(self):
        self.limit_input()
        native_packet = self.export()
        final = self.run_reading_turns(native_packet, turn_count=8)[-1]
        passage = {'id': 'theorem-1', 'version_id': self.versions[0], 'anchor': build_theorem(1)[:40],
                   'characters': 90}
        self.invoke(final, self.build_reading_response(self.read_packet(final), excerpts=[], passages=[passage]))
        with patch.object(transport, 'CODEX_INPUT_LIMIT_CHARS', 2000), self.assertRaises(Exception) as raised:
            self.assign('turn-further', native_packet, prior=final)
        self.assertEqual(raised.exception.code, 'review_input_too_large')
        parts = raised.exception.details['parts']
        self.assertEqual(sorted(parts), ['base_packet', 'excerpts', 'notes', 'passages'])
        self.assertEqual(sum(parts.values()), raised.exception.details['input_characters'])
        self.assertNotIn('turn-further', self.store.snapshot()['records']['review_assignment'])

    def test_a_text_that_does_not_fit_alone_fails_the_assignment(self):
        self.limit_input(9000)
        with self.assertRaises(Exception) as raised:
            self.assign('turn-1', self.export())
        self.assertEqual(raised.exception.code, 'review_input_too_large')
        self.assertEqual(sum(raised.exception.details['parts'].values()), raised.exception.details['input_characters'])
        self.assertNotIn('turn-1', self.store.snapshot()['records'].get('review_assignment', {}))

    def test_a_chain_turn_continues_the_same_reviewer_route_and_packet_without_forking(self):
        self.limit_input()
        native_packet = self.export()
        self.assign('turn-1', native_packet)
        self.invoke('turn-1', self.build_reading_response(self.read_packet('turn-1')))
        other = self.export(name='other-packet', assignment_id='other-turn')
        self.assert_error('review_source_continuation_mismatch',
                          lambda: self.assign('other-turn', other, prior='turn-1'))
        self.assign('turn-2', native_packet, prior='turn-1')
        self.assert_error('review_source_continuation_fork',
                          lambda: self.assign('turn-2b', native_packet, prior='turn-1'))
        records = self.store.snapshot()['records']
        self.assertEqual(records['review_assignment']['turn-2']['context'],
                         {'native_packet': native_packet, 'prior_assignment_id': 'turn-1'})
        self.assertTrue(protocol._prompt_matches(records['review_assignment']['turn-2']))
        changed = dict(records['review_assignment']['turn-2'])
        changed['prompt'] = dict(changed['prompt'], text=changed['prompt']['text'] + ' ')
        self.assertFalse(protocol._prompt_matches(changed))


class SingleSourceBudgetTests(OpennessReadingCase):
    text_sizes = (120001,)

    def test_a_single_source_above_the_budget_starts_a_chain_even_when_the_route_fits(self):
        assignment = self.assign('turn-1', self.export())
        self.assertIn('reading_chain', assignment)
        now = self.read_packet('turn-1')['reading_turn']['reading_plan']['delivered_now']
        self.assertLessEqual(sum(row['characters'] for row in now), 120000)
        self.assertEqual((now[0]['part'], now[0]['parts']), (1, 2))


class ThreeSourceBudgetTests(OpennessReadingCase):
    text_sizes = (150000, 130000, 117000)

    def test_three_sources_totaling_397000_characters_start_a_chain_within_the_route_limit(self):
        assignment = self.assign('turn-1', self.export())
        self.assertIn('reading_chain', assignment)
        now = self.read_packet('turn-1')['reading_turn']['reading_plan']['delivered_now']
        self.assertLessEqual(sum(row['characters'] for row in now), 120000)


class WithinBudgetTests(OpennessReadingCase):
    text_sizes = (50000,)

    def test_a_packet_within_the_reading_budget_and_route_stays_one_native_turn(self):
        assignment = self.assign('turn-1', self.export())
        self.assertNotIn('reading_chain', assignment)
        self.assertIn('native', self.read_packet('turn-1'))
