"""Bar, slate and result continuations deliver at most one budget of new full text in each turn."""

from unittest.mock import patch

from codex_route_fixtures import record_codex_route
from strategy_fixtures import StrategyCase
from test_reviewer_excerpt_sessions import ExcerptSessionCase, build_body, build_lemma
from research_harness import review_excerpts, review_protocol as protocol, review_supplied_evidence
from research_harness import review_transport as transport
from research_harness.source_links import span_locator

# The new full text that one turn delivers at most (review_excerpts.TURN_FULLTEXT_CHARACTERS).
BUDGET = 120000
# Four requested texts of 600,000 characters together, as in the bar session of the KMP record.
SESSION_SIZES = (291855, 195429, 63236, 49480)


def build_sized_body(index, characters):
    """A full text of exactly the given length, in paragraphs separated by blank lines."""
    head = build_lemma(index) + '\n\n'
    tail = 'References: none.'
    paragraph = ('Exact complete mathematical source %d with its proof. ' % index) * 18 + '\n\n'
    filler = characters - len(head) - len(tail)
    return head + (paragraph * (filler // len(paragraph) + 1))[:filler] + tail


def fail_transport(route, request, credential):
    raise ValueError('The reviewer process was stopped at the route timeout.')


class SessionBudgetHelpers:
    def run_failing(self, identifier, attempt_id):
        with patch.object(transport, 'send_request', side_effect=fail_transport):
            return protocol.invoke_assignment(self.store, {'id': attempt_id, 'assignment_id': identifier},
                                              credential='test-only', expected_revision=self.store.revision,
                                              request_id='invoke-' + attempt_id)

    def count_new_text(self, identifier):
        """The characters of new full text that a continuation delivers: whole texts and parts."""
        total = 0
        for delivery in self.packet(identifier)['source_deliveries']:
            if delivery['request']['kind'] != 'source' or delivery['status'] != 'delivered':
                continue
            if 'part' in delivery:
                total += delivery['part']['characters']
            else:
                total += len(self.artifacts.read(review_excerpts.find_fulltext(
                    self.store.snapshot()['records'], delivery['request']['version_id'])).decode())
        return total


class LargeSessionBudgetTests(SessionBudgetHelpers, ExcerptSessionCase):
    def setUp(self):
        StrategyCase.setUp(self)
        self.sequence = 0
        self.setup_dossier()
        record_codex_route(self)
        self.versions = []
        for index, characters in enumerate(SESSION_SIZES, 1):
            version = self.metadata(index)
            self.capture(version, pdf_text=build_sized_body(index, characters))
            self.versions.append(version)
        self.requests = [{'id': 'source-%d' % index, 'kind': 'source', 'version_id': version, 'depth': 'fulltext'}
                         for index, version in enumerate(self.versions, 1)]

    def test_four_requested_texts_of_600000_characters_settle_under_the_budget(self):
        self.assertEqual(sum(SESSION_SIZES), 600000)
        self.assign('a')
        self.invoke('a', self.build_request_turn(self.requests))
        parent, statuses = 'a', set()
        for turn in range(1, 12):
            child = 'turn-%d' % turn
            self.assign(child, prior_assignment_id=parent)
            packet = self.packet(child)
            self.assertLessEqual(self.count_new_text(child), BUDGET)
            pending = {request['id'] for request in packet['pending_source_requests']}
            for delivery in packet['source_deliveries']:
                statuses.add(delivery['status'])
                if delivery['status'] == 'deferred_reading_budget':
                    self.assertIn(delivery['request']['id'], pending)
                    self.assertTrue(delivery['reason'])
                if 'part' in delivery:
                    part = delivery['part']
                    self.assertEqual(delivery['request']['id'] in pending, part['part'] < part['parts'])
            settled = {delivery['status'] for delivery in packet['source_deliveries']} == {'already_delivered'}
            if not pending and settled:
                break
            # The reviewer asks for every text again, as the reviewer of the incident did.
            self.invoke(child, self.build_request_turn(self.requests))
            parent = child
        else:
            self.fail('The session did not settle within a bounded number of turns.')
        self.assertEqual(turn, 7)
        self.assertLessEqual({'delivered', 'deferred_reading_budget', 'already_delivered'}, statuses)
        final = self.review_response('bar')
        self.invoke(child, final)
        self.assertEqual(self.record_final(child, final)['assignment_id'], child)


class SessionBudgetTests(SessionBudgetHelpers, ExcerptSessionCase):
    """Three texts of about 60,500 characters with a budget of 25,000 characters: each arrives in three parts."""

    def limit_budget(self, characters=25000):
        budget = patch.object(review_excerpts, 'TURN_FULLTEXT_CHARACTERS', characters)
        budget.start()
        self.addCleanup(budget.stop)

    def test_a_text_delivered_in_parts_is_already_delivered_after_its_last_part(self):
        self.limit_budget()
        body = build_body(1)
        self.assign('a')
        self.invoke('a', self.build_request_turn(self.requests[:1]))
        parent = 'a'
        for identifier, (start, end) in zip(('b', 'c', 'd'), ((0, 25000), (25000, 50000), (50000, len(body)))):
            self.assign(identifier, prior_assignment_id=parent)
            [delivery] = self.packet(identifier)['source_deliveries']
            last = end == len(body)
            self.assertEqual((delivery['status'], delivery['part']),
                             ('delivered', {'version_id': self.versions[0], 'part': 1 + start // 25000, 'parts': 3,
                                            'start': start, 'end': end, 'characters': end - start}))
            [evidence] = delivery['evidence']
            self.assertEqual(self.artifacts.read(evidence).decode(), body[start:end])
            self.assertEqual(delivery['origin']['locator']['start'], start)
            self.assertEqual(self.packet(identifier)['pending_source_requests'], [] if last else self.requests[:1])
            self.invoke(identifier, self.build_request_turn([dict(self.requests[0], id='again')] if last
                                                            else self.requests[:1]))
            parent = identifier
        self.assign('e', prior_assignment_id='d')
        [delivery] = self.packet('e')['source_deliveries']
        self.assertEqual((delivery['status'], delivery['turn']['assignment_id']), ('already_delivered', 'd'))
        self.assertEqual(self.packet('e')['pending_source_requests'], [])

    def test_a_part_supplies_its_span_of_the_stored_text_for_citations(self):
        self.limit_budget()
        self.assign('a')
        self.invoke('a', self.build_request_turn(self.requests[:1]))
        self.assign('b', prior_assignment_id='a')
        records = self.store.snapshot()['records']
        stored = review_excerpts.find_fulltext(records, self.versions[0])
        source_id = next(capture['source_id'] for capture in records['work'][self.versions[0]]['fulltexts']
                         if capture.get('text') == stored)
        references, mappings = review_supplied_evidence.supplied_context(records, self.artifacts, ['b'], frozen=True)
        self.assertNotIn(stored['sha256'], references)
        for (start, end), received in (((100, 400), True), ((24000, 26000), False), ((30000, 30300), False)):
            with self.subTest(start=start, end=end):
                link = {'version_id': self.versions[0], 'source_id': source_id, 'artifact': stored,
                        'locator': span_locator(build_body(1), start, end)}
                self.assertEqual(review_supplied_evidence.is_received(
                    records, {'kind': 'source', 'link': link}, references, mappings, artifacts=self.artifacts,
                    frozen=True), received)

    def test_a_failed_continuation_is_replaced_and_can_no_longer_run_or_continue(self):
        self.assign('a')
        self.invoke('a', self.build_request_turn(self.requests[:1]))
        failed = self.assign('b', prior_assignment_id='a')
        self.run_failing('b', 'attempt-b')
        self.assertEqual(self.store.snapshot()['records']['review_attempt']['attempt-b']['status'], 'failed')
        replacement = self.assign('b2', prior_assignment_id='a')
        self.assertEqual((replacement['session_id'], replacement['slot_id'], replacement['history_assignment_id']),
                         (failed['session_id'], failed['slot_id'], 'a'))
        self.error('review_assignment_replaced', lambda: self.run_failing('b', 'attempt-b-again'))
        self.error('review_assignment_replaced', lambda: self.assign('c', prior_assignment_id='b'))
        final = self.review_response('bar')
        self.invoke('b2', final)
        self.record_final('b2', final)
        self.assertIn('b', self.store.snapshot()['records']['review_assignment'])

    def test_a_continuation_that_ran_or_never_ran_still_blocks_a_fork(self):
        self.assign('a')
        self.invoke('a', self.build_request_turn(self.requests[:1]))
        self.assign('b', prior_assignment_id='a')
        self.error('review_source_continuation_fork', lambda: self.assign('b2', prior_assignment_id='a'))
        self.invoke('b', self.build_request_turn([dict(self.requests[0], id='again')]))
        self.error('review_source_continuation_fork', lambda: self.assign('b2', prior_assignment_id='a'))

    def test_a_session_of_0_64_2_continues_under_the_budget(self):
        self.assign('a')
        self.invoke('a', self.build_request_turn(self.requests[:2]))
        unbounded = patch.object(review_excerpts, 'TURN_FULLTEXT_CHARACTERS', 10 ** 7)
        with patch.object(review_excerpts, 'PROTOCOL', review_excerpts.PROTOCOL_V1), unbounded:
            earlier = self.assign('b', prior_assignment_id='a')
        self.assertEqual(earlier['prompt'], protocol.canonical_prompt('bar', excerpts=True,
                                                                      excerpt_rules=review_excerpts.PROTOCOL_V1))
        self.assertEqual(self.packet('b')[review_excerpts.KEY]['protocol'], review_excerpts.PROTOCOL_V1)
        self.assertEqual([delivery['status'] for delivery in self.packet('b')['source_deliveries']],
                         ['delivered', 'delivered'])
        self.invoke('b', self.build_request_turn([dict(self.requests[0], id='again'), self.requests[2]]))
        records = self.store.snapshot()['records']
        state = protocol.assignment_state(records, self.artifacts, 'b')
        self.assertTrue(state['ready'], state['obligations'])
        self.assertTrue(protocol.authenticate_attempt(records, self.artifacts, records['review_assignment']['b'],
                                                      records['review_attempt']['attempt-b']))
        current = self.assign('c', prior_assignment_id='b')
        self.assertEqual(current['prompt'], protocol.canonical_prompt('bar', excerpts=True))
        deliveries = self.deliveries('c')
        self.assertEqual((deliveries['again']['status'], deliveries['again']['turn']['assignment_id']),
                         ('already_delivered', 'b'))
        self.assertEqual(deliveries['source-3']['status'], 'delivered')
        self.assertLessEqual(self.count_new_text('c'), BUDGET)
