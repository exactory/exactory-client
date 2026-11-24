"""The refresh of an ordinary slate review is a new chain in the slot of that review (design slate-reviews, decisions D3
to D6, requirements R6 to R9). Its request replays no earlier turn, and its review supersedes the refreshed review."""

import json
from unittest.mock import patch

from research_harness import review_continuation as continuation, review_protocol as protocol
from research_harness import review_route_capacity as capacity, review_transport as transport
from research_harness import research_decisions as decisions
from test_slate_delivery import SlateDeliveryCase

DELIVERY_PROTOCOL = 'exactory-problem-slate-delivery-v1'


class SlateRefreshCase(SlateDeliveryCase):
    def setUp(self):
        from research_harness import bar_sources, review_protocol
        released = patch.object(review_protocol, 'NEW_SESSION_RULES', (bar_sources.PROTOCOL_V3,))
        released.start()
        self.addCleanup(released.stop)
        super().setUp()

    def record_first_slate(self, claims=()):
        """A slate review of the form of 0.92.1, which continues the bar session and states an objection for each
        (id, claim), then a revised candidate."""
        self.record_candidate()
        self.observe_bars()
        output = self.build_slate_output(['candidate-1'])
        output['objections'] = [self.build_objection(identifier, claim) for identifier, claim in claims]
        first = self.observe('slate', 'first', output)
        self.record_candidate('candidate-1-revised', previous='candidate-1',
                              statement='For every odd $n$, does $B(n)\\leq n^2$ hold?')
        return first

    def build_refreshed_output(self, assignment, findings=()):
        """A refreshed answer that cites the reviewer's own bar, which the packet of a refresh chain delivers."""
        _, packet = self.read_assignment(assignment['id'])
        reference = packet['own_bars'][0]['record_reference']
        output = self.cite_everywhere(self.build_slate_output(['candidate-1-revised']), reference)
        output['value']['objection_findings'] = [dict(finding, evidence=[reference]) for finding in findings]
        return output

    def record_refresh(self, identifier, findings=(), **context):
        assignment = self.assign('slate', 'first', identifier=identifier, **context)
        payload = self.answer(assignment, self.build_refreshed_output(assignment, findings))
        return assignment, self.mutate(decisions.record_value_review, payload)['result']

    def build_objection(self, identifier, claim):
        return {'id': identifier, 'claim': claim, 'reason': 'The read survey states it.', 'evidence': [self.supporting],
                'resolution_condition': 'Show what the candidate adds to the known question.',
                'candidate_ids': ['candidate-1']}


class SlateRefreshChainTests(SlateRefreshCase):
    def assert_refresh_chain(self, assignment, first):
        records = self.read_records()
        original = records['review_assignment']['slate-first']
        self.assertEqual(assignment['context'], {'session_refresh': {'assignment_id': 'slate-first'}})
        self.assertEqual((assignment['session_id'], assignment['history_assignment_id'], assignment['slot_id']),
                         (assignment['id'], None, original['slot_id']))
        _, packet = self.read_assignment(assignment['id'])
        _, previous = self.read_assignment('slate-first')
        self.assertEqual(packet['slate_delivery'], {'protocol': DELIVERY_PROTOCOL, 'cited_works': []})
        self.assertEqual([entry.get('review', entry)['id'] for entry in packet['historical_reviews']], [first['id']])
        self.assertEqual([candidate['id'] for candidate in packet['candidates']], ['candidate-1-revised'])
        self.assertEqual(packet['problem_refresh'], {'assignment_id': 'slate-first',
            'previous_candidate_set_digest': previous['candidate_set_digest'],
            'candidate_set_digest': packet['candidate_set_digest'], 'previous_bar_ids': previous['bar_ids'],
            'bar_ids': packet['bar_ids'], 'review_ids': [first['id']]})
        self.assertEqual([bar['assignment_id'] for bar in packet['own_bars']], ['bar-first'])
        request = protocol.build_assignment_request(records, self.artifacts, assignment)
        self.assertEqual(len(transport.logical_messages(request)), 1)
        self.assertIn(DELIVERY_PROTOCOL, assignment['prompt']['parameters']['problem_paragraphs'])

    def test_B1_an_empty_context_refreshes_the_slate_as_a_new_chain_in_its_slot(self):
        first = self.record_first_slate()
        self.assert_refresh_chain(self.assign('slate', 'first', identifier='slate-first-refreshed'), first)

    def test_B2_a_named_prior_assignment_refreshes_the_slate_as_a_new_chain(self):
        first = self.record_first_slate()
        assignment = self.assign('slate', 'first', identifier='slate-first-refreshed', prior_assignment_id='slate-first')
        self.assert_refresh_chain(assignment, first)

    def test_B3_the_refreshed_review_gives_findings_and_supersedes_the_earlier_one(self):
        first = self.record_first_slate([('restated', 'Candidate 1 restates a known question.'),
                                         ('narrow', 'Candidate 1 covers even lengths only.')])
        assignment = self.assign('slate', 'first', identifier='slate-first-refreshed')
        self.assertEqual(assignment['context'], {'session_refresh': {'assignment_id': 'slate-first'}})
        bare = self.answer(assignment, self.build_refreshed_output(assignment))
        self.assert_error('review_objection_history_incomplete',
                          lambda: self.mutate(decisions.record_value_review, bare))
        findings = [{'id': 'restated', 'status': 'resolved', 'reason': 'The revision adds the odd case.'},
                    {'id': 'narrow', 'status': 'continuing', 'reason': 'The revision still covers one parity.'}]
        output = self.build_refreshed_output(assignment, findings)
        with patch('research_harness.review_transport.send_request', return_value=self.build_provider_response(output)):
            self.mutate(protocol.invoke_assignment, {'id': 'attempt-findings', 'assignment_id': assignment['id']},
                        credential='test-only')
        refreshed = self.mutate(decisions.record_value_review, dict(output, id='review-slate-first-refreshed',
                                dossier_id=None, assignment_id=assignment['id']))['result']
        records = self.read_records()
        superseded = decisions._collect_superseded_review_ids(records, self.artifacts)
        self.assertIn(first['id'], superseded)
        self.assertNotIn(refreshed['id'], superseded)
        reviews = [review for review in records['value_review'].values() if review['payload']['stage'] == 'slate']
        versions, held = decisions._fold_objection_versions(reviews, superseded)
        self.assertFalse(any(version['material_version'] in held for version in versions['restated']))
        current = [version for version in versions['narrow'] if version['material_version'] in held]
        self.assertEqual([version['review_id'] for version in current], [refreshed['id']])

    def test_B4_the_plan_of_the_head_names_the_refresh_that_the_store_takes(self):
        self.record_first_slate()
        plan = continuation.plan_next(self.read_records(), self.artifacts, 'slate-first')
        self.assertEqual((plan['state'], plan.get('command')), ('continue', 'review-assignment'), plan)
        self.assertEqual((plan['payload']['context'], plan['payload']['route_id']),
                         ({'session_refresh': {'assignment_id': 'slate-first'}}, 'route'))
        recorded = self.mutate(protocol.record_assignment, dict(plan['payload'], id='slate-first-planned'))['result']
        self.assertEqual(recorded['context'], plan['payload']['context'])

    def test_B5_a_second_refresh_leaves_one_review_of_the_slot_in_force(self):
        first = self.record_first_slate()
        _, refreshed = self.record_refresh('slate-first-refreshed')
        self.record_candidate('candidate-1-again', previous='candidate-1-revised',
                              statement='For every prime $n$, does $B(n)\\leq n^2$ hold?')
        assignment = self.assign('slate', 'first', identifier='slate-first-again')
        self.assertEqual(assignment['context'], {'session_refresh': {'assignment_id': 'slate-first-refreshed'}})
        _, packet = self.read_assignment(assignment['id'])
        self.assertEqual(sorted(entry.get('review', entry)['id'] for entry in packet['historical_reviews']),
                         sorted([first['id'], refreshed['id']]))
        output = self.build_refreshed_output(assignment)
        output['value']['rubric_potential'] = {'candidate-1-again': output['value']['rubric_potential'].pop(
            'candidate-1-revised')}
        latest = self.mutate(decisions.record_value_review, self.answer(assignment, output))['result']
        superseded = decisions._collect_superseded_review_ids(self.read_records(), self.artifacts)
        self.assertEqual({first['id'], refreshed['id'], latest['id']} - superseded, {latest['id']})

    def test_B6_incident_a_refresh_fits_the_route_on_which_the_first_slate_fitted(self):
        """Run T4, Observation 2: each bar delivers a full text in a source turn, so the first slate replays it in its
        bar session. The route holds that first slate and less than the old refresh, which replays the whole first
        slate turn again. Both bars need their source turn: the first slate continues the bar of its own reviewer, and
        the bars of both reviewers have the same shape. Measured on this fixture: the first slate request has 116,662
        tokens, the refresh of 0.92.1 133,165, the new refresh 24,732; the route holds 125,000 (3 to 5 s)."""
        from codex_route_fixtures import invoke_codex, record_codex_route
        version, _ = self.record_long_reading(5, 8000)
        self.record_candidate()
        self.supporting = self.artifacts.put(b'Field evidence used by the authored review fixture.', 'text/plain')
        self.prepare_review_route()
        for reviewer in ('first', 'second'):
            bar = self.assign('bar', reviewer, **self.deliver_supporting(reviewer))
            request = self.build_review_output('bar')
            request['value']['status'] = 'unresolved'
            request['source_requests'] = [{'id': 'text', 'kind': 'source', 'version_id': version, 'depth': 'fulltext'}]
            self.answer(bar, request)
            final = self.assign('bar', reviewer, identifier='bar-' + reviewer + '-final', prior_assignment_id=bar['id'])
            self.mutate(decisions.record_value_review, self.answer(final, self.build_review_output('bar')))
        self.capacity_probe_tokens = 128000 + 125000
        record_codex_route(self, 'local', output_tokens=128000)
        first = self.mutate(protocol.record_assignment, {'id': 'slate-first', 'route_id': 'local', 'dossier_id': None,
            'role': 'slate', 'reviewer_id': 'first', 'author_id': 'problem-author', 'context': {}})['result']
        self.assertEqual(first['history_assignment_id'], 'bar-first-final')
        output = self.build_slate_output(['candidate-1'])
        output['value']['bar_ids'] = ['review-bar-first-final', 'review-bar-second-final']
        invoke_codex(self, first['id'], output)
        self.mutate(decisions.record_value_review, dict(output, id='review-slate-first', dossier_id=None,
                                                        assignment_id=first['id']))
        records = self.read_records()
        route = records['review_route']['local']
        fitted = capacity.measure_request(route, protocol.build_assignment_request(records, self.artifacts, first),
                                          records=records, artifacts=self.artifacts)
        self.assertEqual((fitted['token_limit'], fitted['fits']), (125000, True))
        self.record_candidate('candidate-1-revised', previous='candidate-1',
                              statement='For every odd $n$, does $B(n)\\leq n^2$ hold?')
        refreshed = self.mutate(protocol.record_assignment, {'id': 'slate-first-refreshed', 'route_id': 'local',
            'dossier_id': None, 'role': 'slate', 'reviewer_id': 'first', 'author_id': 'problem-author',
            'context': {}})['result']
        self.assertEqual(refreshed['context'], {'session_refresh': {'assignment_id': 'slate-first'}})
        records = self.read_records()
        request = protocol.build_assignment_request(records, self.artifacts, refreshed)
        self.assertTrue(capacity.measure_request(route, request, records=records, artifacts=self.artifacts)['fits'])


class StoredSlateFormTests(SlateRefreshCase):
    def assert_stored_turn_verifies(self, assignment, output):
        _, packet = self.read_assignment(assignment['id'])
        self.assertTrue(protocol._prompt_matches(assignment, packet=packet))
        review = self.mutate(decisions.record_value_review, self.answer(assignment, output))['result']
        records = self.read_records()
        attempt, _ = protocol.read_assignment_output(records, self.artifacts, assignment['id'])
        self.assertTrue(protocol.authenticate_attempt(records, self.artifacts, assignment, attempt))
        return review

    def test_B7_stored_forms_of_0921_rebuild_and_record(self):
        self.record_first_slate()
        original = protocol.is_session_refresh

        def find_v4_refresh(records, artifacts, prior):
            # The refresh check of 0.92.1: only a chain of the session rules v4 is refreshed as a new chain.
            return bool(prior.get('session_chain')) and original(records, artifacts, prior)
        with patch.object(protocol, 'is_session_refresh', side_effect=find_v4_refresh):
            stored = self.assign('slate', 'first', identifier='slate-first-refreshed')
        self.assertEqual((stored['context'], stored['history_assignment_id']),
                         ({'prior_assignment_id': 'slate-first'}, 'slate-first'))
        self.assert_stored_turn_verifies(stored, self.build_slate_output(['candidate-1-revised']))
        reading = self.record_full_reading(note_id='read-cited')
        self.record_cited_candidate('candidate-2', [self.cite_reading(reading['reading_id'])])
        with patch.object(protocol, 'add_slate_delivery'):
            cited = self.assign('slate', 'second')
        _, packet = self.read_assignment(cited['id'])
        self.assertNotIn('slate_delivery', packet)
        self.assertEqual(cited['history_assignment_id'], 'bar-second')
        output = self.build_slate_output(['candidate-1-revised', 'candidate-2'])
        self.assert_stored_turn_verifies(cited, output)


class SlateRefreshPlanTests(SlateRefreshCase):
    def revise_citing(self, words=None, number=1):
        """A revised candidate-1 that cites a full reading: of the default fixture work, or of a work of `words`."""
        if words is None:
            reading = self.record_full_reading(note_id='read-cited')
            version, reading_id = reading['version_id'], reading['reading_id']
        else:
            version, reading_id = self.record_long_reading(number, words)
        self.record_candidate('candidate-1-revised', previous='candidate-1',
                              statement='For every odd $n$, does $B(n)\\leq n^2$ hold?',
                              source={'kind': 'literature', 'evidence': [self.cite_reading(reading_id)],
                                      'reference': 'The bound is stated as open in the cited work.'})
        return version

    def test_B8_a_refresh_chain_delivers_a_cited_work_and_its_review_cites_it(self):
        """Run T4 with run T6: the refresh of a slate review whose revised candidate cites a full reading."""
        self.record_candidate()
        self.observe_bars()
        output = self.build_slate_output(['candidate-1'])
        output['objections'] = [self.build_objection('restated', 'Candidate 1 restates a known question.')]
        first = self.observe('slate', 'first', output)
        version = self.revise_citing()
        assignment = self.assign('slate', 'first', identifier='slate-first-refreshed')
        self.assertEqual(assignment['context'], {'session_refresh': {'assignment_id': 'slate-first'}})
        _, packet = self.read_assignment(assignment['id'])
        [work] = packet['slate_delivery']['cited_works']
        self.assertEqual((work['version_id'], work['delivery']['status']), (version, 'delivered'))
        [text] = work['delivery']['evidence']
        self.assertIn(text['sha256'], {entry['artifact']['sha256'] for entry in packet['evidence']})
        request = protocol.build_assignment_request(self.read_records(), self.artifacts, assignment)
        self.assertEqual(len(transport.logical_messages(request)), 1)
        answer = self.cite_everywhere(self.build_slate_output(['candidate-1-revised']), text)
        answer['value']['objection_findings'] = [{'id': 'restated', 'status': 'resolved',
                                                  'reason': 'The cited work leaves the odd case open.', 'evidence': [text]}]
        review = self.mutate(decisions.record_value_review, self.answer(assignment, answer))['result']
        superseded = decisions._collect_superseded_review_ids(self.read_records(), self.artifacts)
        self.assertEqual({first['id'], review['id']} - superseded, {review['id']})

    def test_B9_the_plan_of_a_refresh_that_does_not_fit_is_a_stop_whose_step_the_store_takes(self):
        """P1-1: the refresh carries a cited text of 60,000 words, more than the route of the first slate holds. Each
        round plans the head, then takes what the plan names. The plan checks the refresh with the capacity admission
        of review-assignment and stops with the steps of rule F5: no retirement of a bar route and no bar repair."""
        from codex_route_fixtures import build_codex_response, invoke_codex, record_codex_route
        self.record_candidate()
        self.observe_bars()
        self.capacity_probe_tokens = 128000 + 125000
        record_codex_route(self, 'local', output_tokens=128000)
        first = self.mutate(protocol.record_assignment, {'id': 'slate-first', 'route_id': 'local', 'dossier_id': None,
            'role': 'slate', 'reviewer_id': 'first', 'author_id': 'problem-author', 'context': {}})['result']
        output = self.build_slate_output(['candidate-1'])
        invoke_codex(self, first['id'], output)
        self.mutate(decisions.record_value_review, dict(output, id='review-slate-first', dossier_id=None,
                                                        assignment_id=first['id']))
        self.revise_citing(words=60000, number=9)
        plans = [continuation.plan_next(self.read_records(), self.artifacts, 'slate-first') for _ in range(2)]
        for plan in plans:
            self.assertEqual((plan['state'], plan['code']), ('stop', 'review_route_capacity_below_floor'), plan)
            steps = [step for answer in plan['details']['decision']['then'] for step in answer['steps']]
            self.assertTrue(steps)
            self.assertTrue(all(step['id'] in (14, 16) for step in steps), steps)
            self.assertNotIn('payload', plan)
        self.assertEqual(plans[0], plans[1])
        step = plans[1]['details']['step']
        self.assertEqual((plans[1]['command'], step['id'], step['payload']['route_id']), ('review-probe', 16, 'local'))

        def answer_probe(route, request, credential):
            packet = json.loads(request['input'][0]['content'][len(transport.PACKET_LABEL):])
            return build_codex_response(route, request, {'allowed_control': packet['allowed_control'],
                'evidence_control': packet['evidence_control'], 'excluded_controls': []})
        with patch.object(transport, 'send_request', side_effect=answer_probe):
            taken = self.mutate(protocol.probe_route, dict(step['payload'], id='capacity-probe'), credential='test-only')
        self.assertEqual(taken['result']['id'], 'capacity-probe')
        after = continuation.plan_next(self.read_records(), self.artifacts, 'slate-first')
        self.assertEqual((after['state'], after.get('command'), after['payload']['context']),
                         ('continue', 'review-assignment', {'session_refresh': {'assignment_id': 'slate-first'}}))
