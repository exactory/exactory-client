"""Rejected reviewer copies cannot enlarge retained excerpt state without bounds per entry."""

import hashlib
import json

from literature_fixtures import LiteratureCase
from test_reviewer_excerpt_sessions import ExcerptSessionCase, build_body
from research_harness import review_excerpts


SESSION_PROTOCOL = 'exactory-review-excerpts-v3'


class BoundedExcerptStateTests(LiteratureCase):
    def setUp(self):
        super().setUp()
        self.version = self.metadata(1)
        self.source = 'A verified theorem holds for every finite graph. ' * 150
        self.capture(self.version, pdf_text=self.source)
        self.records = self.store.snapshot()['records']

    def start_session(self):
        return review_excerpts.carry_session(self.records, self.artifacts, {}, {})

    def carry(self, session, values):
        return review_excerpts.carry_session(self.records, self.artifacts,
                                             {review_excerpts.KEY: session}, {'excerpts': values})

    def excerpt(self, text, purpose='Keep the statement.'):
        return {'version_id': self.version, 'text': text, 'purpose': purpose}

    def assert_bounded(self, entry, value):
        self.assertNotIn('text', entry)
        self.assertEqual(entry['sha256'], hashlib.sha256(value.encode('utf-8')).hexdigest())
        self.assertEqual(entry['characters'], len(value))
        self.assertEqual(entry['head'], value[:200])

    def test_an_unmatched_6001_character_copy_is_bounded_without_charging_allowance(self):
        value = '未' * 6001
        session = self.carry(self.start_session(), [self.excerpt(value)])
        [entry] = session['excerpts']
        self.assert_bounded(entry, value)
        self.assertEqual((entry['status'], entry['reason'], entry['checked']), ('unmatched', 'not_found', False))
        self.assertEqual(session['protocol'], SESSION_PROTOCOL)
        self.assertEqual(session['allowances']['excerpts']['used'], 0)

    def test_a_long_purpose_keeps_500_characters_and_the_full_digest_and_length(self):
        purpose = '理' * 5000
        session = self.carry(self.start_session(), [self.excerpt(self.source[:45], purpose)])
        [entry] = session['excerpts']
        self.assertEqual(entry['purpose'], purpose[:500])
        self.assertEqual(entry['purpose_sha256'], hashlib.sha256(purpose.encode('utf-8')).hexdigest())
        self.assertEqual(entry['purpose_characters'], 5000)
        self.assertEqual(entry['text'], self.source[:45])

    def test_three_turns_with_the_same_unmatched_copy_keep_one_entry(self):
        session = self.start_session()
        for turn in range(3):
            session = self.carry(session, [self.excerpt('A statement absent from the source. ' * 200,
                                                       purpose='Purpose of turn %d.' % turn)])
        self.assertEqual(len(session['excerpts']), 1)
        self.assertEqual(session['excerpts'][0]['purpose'], 'Purpose of turn 0.')
        self.assertEqual(session['allowances']['excerpts']['used'], 0)

    def test_verified_duplicates_do_not_spend_the_allowance_twice(self):
        value = self.excerpt(self.source[:45])
        session = self.carry(self.start_session(), [value, value])
        session = self.carry(session, [value])
        self.assertEqual(len(session['excerpts']), 1)
        self.assertEqual(session['allowances']['excerpts']['used'], 45)
        self.assertEqual(session['excerpts'][0]['text'], self.source[:45])

    def test_an_over_allowance_excerpt_keeps_the_source_span_length_and_no_text_diagnostics(self):
        session = self.start_session()
        session['allowances']['excerpts']['used'] = 200000
        original = self.source[:45]
        value = self.excerpt(original.replace(' ', '  '), 'p' * 5000)
        self.assertGreater(len(value['text']), len(original))
        session = self.carry(session, [value, value])
        session = self.carry(session, [value])
        self.assertEqual(len(session['excerpts']), 1)
        [entry] = session['excerpts']
        self.assertEqual(entry, {'version_id': self.version, 'purpose': 'p' * 500,
                                 'purpose_characters': 5000,
                                 'purpose_sha256': hashlib.sha256(('p' * 5000).encode()).hexdigest(),
                                 'status': 'over_allowance', 'characters': len(original),
                                 'note': review_excerpts.EXCERPT_OVER_ALLOWANCE_NOTE})
        self.assertEqual(session['allowances']['excerpts']['used'], 200000)

    def test_fifty_turns_of_distinct_unmatched_copies_stay_below_70000_utf8_bytes(self):
        session = self.start_session()
        for turn in range(50):
            value = 'Missing statement %d: ' % turn + 'x' * 6001
            session = self.carry(session, [self.excerpt(value, 'p' * 5000)])
        size = len(json.dumps(session, sort_keys=True, ensure_ascii=False).encode('utf-8'))
        self.assertLess(size, 70000)
        self.assertEqual(len(session['excerpts']), 50)
        self.assertEqual(session['allowances']['excerpts']['used'], 0)
        for turn, entry in enumerate(session['excerpts']):
            self.assert_bounded(entry, 'Missing statement %d: ' % turn + 'x' * 6001)


class BoundedPassageSessionTests(ExcerptSessionCase):
    def test_an_over_allowance_passage_response_keeps_a_bounded_diagnostic(self):
        from unittest.mock import patch
        with patch.object(review_excerpts, 'SESSION_PASSAGE_CHARACTERS', 10):
            self.assign('a')
            self.invoke('a', self.build_request_turn(self.requests[:1]))
            self.assign('b', prior_assignment_id='a')
        passage = {'id': 'over', 'kind': 'passage', 'version_id': self.versions[0],
                   'anchor': build_body(1)[:6001], 'characters': 6001}
        self.invoke('b', self.build_request_turn([passage]))
        self.assign('c', prior_assignment_id='b')
        [entry] = self.packet('c')['source_deliveries']
        self.assertEqual(entry['status'], 'over_allowance')
        self.assertEqual(entry['request']['anchor'], build_body(1)[:200])
        self.assertEqual(entry['request']['anchor_sha256'], hashlib.sha256(passage['anchor'].encode()).hexdigest())
        self.assertEqual(entry['request']['anchor_characters'], len(passage['anchor']))
        self.assertEqual(set(entry), {'request', 'reading_credit', 'status', 'characters', 'note'})
        self.assertEqual(entry['characters'], 6001)
        self.assertLess(len(json.dumps(entry)), 1600)
        self.assertEqual(self.packet('c')[review_excerpts.KEY]['allowances']['passages']['used'], 0)
        from research_harness.review_source_batches import project_replies
        records = self.store.snapshot()['records']
        allowances = self.packet('c')[review_excerpts.KEY]['allowances']
        deferred = project_replies(records, self.artifacts, self.packet('c'), set(), allowances,
                                    passage_requests=[passage])
        self.assertEqual(deferred['pending_source_requests'], [passage])
        # A later fitting selection can deliver the full original anchor, with no diagnostic head used as an anchor.
        allowances['passages']['characters'] = 10000
        fitted = project_replies(records, self.artifacts, self.packet('c'), {0}, allowances,
                                 passage_requests=[passage])
        [delivered] = fitted['source_deliveries']
        self.assertEqual((delivered['status'], delivered['text'], delivered['request']),
                         ('delivered', build_body(1)[:6001], passage))

    def test_an_unsuccessful_passage_does_not_keep_an_unbounded_anchor(self):
        self.assign('a')
        self.invoke('a', self.build_request_turn(self.requests[:1]))
        self.assign('b', prior_assignment_id='a')
        anchor = 'An absent anchor. ' * 1000
        passage = {'id': 'absent', 'kind': 'passage', 'version_id': self.versions[0],
                   'anchor': anchor, 'characters': 100}
        self.invoke('b', self.build_request_turn([passage]))
        self.assign('c', prior_assignment_id='b')
        [entry] = self.packet('c')['source_deliveries']
        self.assertEqual(entry['status'], 'not_found')
        self.assertLess(len(json.dumps(entry)), 1600)
        self.assertEqual(entry['request']['anchor'], anchor[:200])
        self.assertEqual(entry['request']['anchor_sha256'], hashlib.sha256(anchor.encode()).hexdigest())
        self.assertEqual(entry['request']['anchor_characters'], len(anchor))
        self.assertEqual(set(entry), {'request', 'reading_credit', 'status', 'reason'})


class BoundedReadingRecordTests(LiteratureCase):
    def test_text_free_over_allowance_diagnostics_deduplicate_after_compaction(self):
        from research_harness import openness_reading as reading
        source = 'A source statement for an exhausted excerpt allowance.'
        reference = self.artifacts.put(source.encode(), 'text/plain')
        rows = [{'kind': 'search_response', 'source_id': 'held', 'artifact': reference}]
        record = {'protocol': reading.PROTOCOL_V4, 'excerpt_protocol': SESSION_PROTOCOL, 'turns': [],
                  'sources': [], 'excerpts': [], 'passages': [], 'allowances': review_excerpts.create_allowances()}
        record['allowances']['excerpts']['used'] = 200000
        excerpt = {'source_id': 'held', 'text': source, 'purpose': 'Keep the statement.'}
        output = {'excerpts': [excerpt], 'passage_requests': [], 'notes': 'Allowance exhausted.'}
        observations = []
        for turn in (1, 2):
            identifier = 'turn-%d' % turn
            observations.append({'assignment_id': identifier, 'output': output,
                                 'turn': {'assignment_id': identifier, 'attempt_id': 'attempt-%d' % turn}})
            packet = {'reading_turn': {'protocol': reading.PROTOCOL_V4, 'reading_record': record}}
            record = reading._extend_record({}, self.artifacts, packet, {'output': output}, rows, observations,
                                             reading.PROTOCOL_V4)
        self.assertEqual(record['excerpts'], [{'source_id': 'held', 'purpose': 'Keep the statement.',
                                               'status': 'over_allowance', 'characters': len(source)}])

    def test_a_reading_record_carries_bounded_excerpts_and_passages_under_its_session_version(self):
        from research_harness import openness_reading as reading
        earlier = {'protocol': reading.PROTOCOL_V4, 'excerpt_protocol': SESSION_PROTOCOL, 'turns': [],
                   'sources': [], 'excerpts': [], 'passages': [], 'allowances': review_excerpts.create_allowances()}
        excerpt = {'source_id': 'missing', 'text': 'x' * 6001, 'purpose': 'p' * 5000}
        request = {'id': 'missing', 'source_id': 'missing', 'anchor': 'a' * 6001, 'characters': 100}
        output = {'excerpts': [excerpt, excerpt], 'passage_requests': [request], 'notes': 'No matching source.'}
        observation = {'assignment_id': 'turn-1', 'output': output,
                       'turn': {'assignment_id': 'turn-1', 'attempt_id': 'attempt-1'}}
        packet = {'reading_turn': {'protocol': reading.PROTOCOL_V4, 'reading_record': earlier}}
        record = reading._extend_record({}, self.artifacts, packet, {'output': output}, [], [observation],
                                         reading.PROTOCOL_V4)
        self.assertEqual(len(record['excerpts']), 1)
        self.assertEqual(record['excerpt_protocol'], SESSION_PROTOCOL)
        self.assertEqual(record['excerpts'][0]['head'], 'x' * 200)
        self.assertEqual(len(record['excerpts'][0]['purpose']), 500)
        self.assertEqual(record['passages'][0], {'source_id': 'missing', 'request_id': 'missing',
                                                'assignment_id': 'turn-1', 'status': 'not_found',
                                                'reason': 'no_stored_text'})
        self.assertLess(len(json.dumps(record)), 2500)
