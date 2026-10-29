"""Reviewer excerpts and bounded passages are checked against the stored source text."""

import hashlib
import json

from literature_fixtures import LiteratureCase
from research_harness import review_excerpts
from research_harness.errors import ResearchError


THEOREM = 'Theorem 2. For every ﬁnite  graph G\twith maximum degree d,\n the gap is at least 1/d.'
REPEATED = 'Repeated phrase for the first-occurrence check.'
CLOSING = 'Closing statement of the source text near its end.'
FILLER = 'Filler sentence. '
SOURCE = ('First line of the paper.\n' + THEOREM + '\n' + REPEATED + ' Middle part. ' + REPEATED + ' '
          + FILLER * 400 + CLOSING + '\nReferences: none.')


class ReviewExcerptTests(LiteratureCase):
    def setUp(self):
        super().setUp()
        self.version = self.metadata(1)
        self.capture(self.version, pdf_text=SOURCE)
        self.text = self.store.snapshot()['records']['work'][self.version]['fulltexts'][0]['text']
        self.assertEqual(self.artifacts.read(self.text).decode(), SOURCE)

    def check(self, values, used=0):
        allowances = review_excerpts.create_allowances()
        allowances['excerpts']['used'] = used
        return review_excerpts.check_excerpts(self.store.snapshot()['records'], self.artifacts, values, allowances)

    def answer(self, requests, used=0):
        allowances = review_excerpts.create_allowances()
        allowances['passages']['used'] = used
        return review_excerpts.answer_passages(self.store.snapshot()['records'], self.artifacts, requests, allowances)

    def excerpt(self, text, version=None):
        return {'version_id': version or self.version, 'text': text, 'purpose': 'The statement that the review uses.'}

    def assert_span(self, entry, start, end):
        original = SOURCE[start:end]
        self.assertEqual(entry['text'], original)
        self.assertEqual(entry['locator'], {'kind': 'span', 'start': start, 'end': end,
                                            'sha256': hashlib.sha256(original.encode('utf-8')).hexdigest()})
        self.assertEqual(entry['artifact_descriptor']['sha256'], self.text['sha256'])
        self.assertEqual(entry['artifact_descriptor']['location'], self.text['path'])

    def test_normalization_keeps_the_original_offset_of_every_character(self):
        normalized, offsets = review_excerpts.normalize_with_offsets('  A  ﬁ\n b ')
        self.assertEqual(normalized, 'A fi b')
        self.assertEqual(offsets, [2, 3, 5, 5, 6, 8])

    def test_an_excerpt_matches_across_white_space_and_compatibility_forms(self):
        copied = 'Theorem 2. For every finite graph G with maximum degree d, the gap is at least 1/d.'
        [entry], allowances = self.check([self.excerpt(copied)])
        self.assertEqual(entry['status'], 'verified')
        self.assertEqual(entry['purpose'], 'The statement that the review uses.')
        start = SOURCE.index('Theorem 2.')
        self.assert_span(entry, start, start + len(THEOREM))
        self.assertEqual(allowances['excerpts'], {'characters': 200000, 'used': len(THEOREM)})

    def test_the_first_occurrence_is_delivered(self):
        [entry], _ = self.check([self.excerpt(REPEATED)])
        start = SOURCE.index(REPEATED)
        self.assertLess(start, SOURCE.rindex(REPEATED))
        self.assert_span(entry, start, start + len(REPEATED))

    def test_an_unmatched_excerpt_keeps_the_reviewers_text_with_its_reason(self):
        cases = ((self.excerpt('A complete sentence copied from elsewhere.', 'arxiv:2601.00999v1'), 'no_stored_text'),
                 (self.excerpt('Theorem 2.'), 'too_short'),
                 (self.excerpt(FILLER * 360), 'too_long'),
                 (self.excerpt('A sentence that the source never contains anywhere.'), 'not_found'))
        entries, allowances = self.check([value for value, _ in cases])
        for entry, (value, reason) in zip(entries, cases):
            with self.subTest(reason=reason):
                self.assertEqual((entry['status'], entry['reason'], entry['text'], entry['checked']),
                                 ('unmatched', reason, value['text'], False))
                self.assertNotIn('locator', entry)
                self.assertTrue(entry['note'])
        self.assertEqual(allowances['excerpts']['used'], 0)

    def test_the_excerpt_allowance_counts_verified_text_in_output_order(self):
        limit = review_excerpts.SESSION_EXCERPT_CHARACTERS
        entries, allowances = self.check([self.excerpt(THEOREM), self.excerpt(REPEATED)], used=limit - 60)
        self.assertEqual([entry['status'] for entry in entries], ['over_allowance', 'verified'])
        self.assertEqual(entries[0]['characters'], len(THEOREM))
        self.assertNotIn('text', entries[0])
        self.assertEqual(allowances['excerpts']['used'], limit - 60 + len(REPEATED))

    def test_an_excerpt_of_a_version_without_a_full_text_matches_its_abstract(self):
        version = self.metadata(2)
        abstract = self.store.snapshot()['records']['work'][version]['abstracts'][0]['artifact']
        text = self.artifacts.read(abstract).decode()
        [entry], _ = self.check([self.excerpt(text[:40], version)])
        self.assertEqual((entry['status'], entry['text']), ('verified', text[:40]))
        self.assertEqual(entry['artifact_descriptor']['sha256'], abstract['sha256'])

    def request(self, identifier, anchor, characters, version=None):
        return {'id': identifier, 'version_id': version or self.version, 'anchor': anchor, 'characters': characters}

    def test_a_passage_starts_at_the_anchor_and_stops_at_the_end_of_the_text(self):
        inner = self.request('inner', 'Theorem 2. For every finite graph', 30)
        closing = self.request('closing', CLOSING[:31], 5000)
        answers, allowances = self.answer([inner, closing])
        start = SOURCE.index('Theorem 2.')
        self.assertEqual((answers[0]['request'], answers[0]['status']), (inner, 'delivered'))
        self.assert_span(answers[0], start, start + 30)
        end_start = SOURCE.index(CLOSING)
        self.assertEqual(answers[1]['status'], 'delivered')
        self.assert_span(answers[1], end_start, len(SOURCE))
        self.assertEqual(allowances['passages'], {'characters': 100000, 'used': 30 + len(SOURCE) - end_start})

    def test_a_passage_is_not_found_or_over_the_session_allowance(self):
        missing = self.request('missing', 'A sentence that the source never contains.', 100)
        unheld = self.request('unheld', 'Theorem 2. For every finite graph', 100, 'arxiv:2601.00999v1')
        over = self.request('over', 'Theorem 2. For every finite graph', 30)
        answers, allowances = self.answer([missing, unheld, over], used=review_excerpts.SESSION_PASSAGE_CHARACTERS - 20)
        self.assertEqual([(answer['status'], answer.get('reason')) for answer in answers],
                         [('not_found', 'anchor_not_found'), ('not_found', 'no_stored_text'), ('over_allowance', None)])
        self.assertEqual(answers[2]['characters'], 30)
        self.assertTrue(all('text' not in answer for answer in answers))
        self.assertEqual(allowances['passages']['used'], review_excerpts.SESSION_PASSAGE_CHARACTERS - 20)

    def test_passage_requests_and_excerpts_need_their_exact_shapes(self):
        valid = self.request('valid', 'Theorem 2. For every finite graph', 30)
        review_excerpts.validate_passage_request(valid, 'invalid_reading_response')
        for change in ({'anchor': 'Theorem 2.'}, {'characters': 0}, {'characters': 20001}, {'characters': True},
                       {'characters': 30.0}, {'version_id': 'arxiv:2601.00001'}):
            with self.subTest(change=change), self.assertRaises(ResearchError) as raised:
                review_excerpts.validate_passage_request(dict(valid, **change), 'invalid_reading_response')
            self.assertEqual(raised.exception.code, 'invalid_reading_response')
        review_excerpts.validate_excerpts([self.excerpt(THEOREM)], 'invalid_value_review')
        for values in ([dict(self.excerpt(THEOREM), purpose='')], [{'version_id': self.version, 'text': THEOREM}],
                       [dict(self.excerpt(THEOREM), extra=1)], {'version_id': self.version}):
            with self.subTest(values=values), self.assertRaises(ResearchError) as raised:
                review_excerpts.validate_excerpts(values, 'invalid_value_review')
            self.assertEqual(raised.exception.code, 'invalid_value_review')


RESPONSE_NOTE = 'The captured search response names no work that settles the bounded statement.'
RECORD_NOTE = 'The reading note records that the second paper proves only the bounded case.'
PURPOSE = 'The statement that the review uses.'


class SourceIdentityTests(LiteratureCase):
    """Excerpts and passages name a captured search response by source_id with an optional text hash, and a store
    record by record_kind and record_id, and are checked against the bound bodies of a reading chain; the excerpt rules
    of bar, slate and result sessions keep version_id."""

    def setUp(self):
        super().setUp()
        from research_harness.acquisition import import_response
        data = {'query': 'bounded sequences', 'note': RESPONSE_NOTE, 'results': []}
        captured = import_response(self.store, 'mcp', json.dumps(data).encode(),
                                   source_url='https://example.org/problem-search',
                                   captured_at='2026-10-15T12:00:00Z', media_type='application/json', mappings=[],
                                   expected_revision=self.store.revision, request_id='identity-response')
        self.source_id = captured['source_ids'][0]
        self.response = self.store.snapshot()['records']['source'][self.source_id]['response']
        self.content = self.artifacts.read(self.response).decode('utf-8')
        self.catalog = {('search_response', ('source_id', self.source_id)): {self.response['sha256']: self.response}}

    def check(self, values, unit_sources=None):
        return review_excerpts.check_excerpts(self.store.snapshot()['records'], self.artifacts, values,
                                              review_excerpts.create_allowances(), unit_sources=unit_sources)

    def answer(self, requests, unit_sources=None):
        return review_excerpts.answer_passages(self.store.snapshot()['records'], self.artifacts, requests,
                                               review_excerpts.create_allowances(), unit_sources=unit_sources)

    def test_a_captured_search_response_is_named_by_source_id_with_an_optional_text_hash(self):
        values = [{'source_id': self.source_id, 'text': RESPONSE_NOTE, 'purpose': PURPOSE},
                  {'source_id': self.source_id, 'text_sha256': self.response['sha256'], 'text': RESPONSE_NOTE,
                   'purpose': PURPOSE},
                  {'source_id': self.source_id, 'text_sha256': '0' * 64, 'text': RESPONSE_NOTE, 'purpose': PURPOSE},
                  {'source_id': 'source-missing', 'text': RESPONSE_NOTE, 'purpose': PURPOSE}]
        review_excerpts.validate_excerpts(values, 'invalid_reading_response', kinds=review_excerpts.UNIT_KINDS)
        entries, allowances = self.check(values, unit_sources=self.catalog)
        self.assertEqual([(entry['status'], entry.get('reason')) for entry in entries],
                         [('verified', None), ('verified', None), ('unmatched', 'no_stored_text'),
                          ('unmatched', 'no_stored_text')])
        start = self.content.index(RESPONSE_NOTE)
        end = start + len(RESPONSE_NOTE)
        self.assertEqual({key: entries[0][key] for key in ('source_id', 'text', 'locator')},
                         {'source_id': self.source_id, 'text': RESPONSE_NOTE,
                          'locator': {'kind': 'span', 'start': start, 'end': end,
                                      'sha256': hashlib.sha256(RESPONSE_NOTE.encode('utf-8')).hexdigest()}})
        self.assertNotIn('version_id', entries[0])
        self.assertEqual(entries[0]['artifact_descriptor']['sha256'], self.response['sha256'])
        self.assertEqual(entries[1]['text_sha256'], self.response['sha256'])
        self.assertEqual(allowances['excerpts']['used'], 2 * len(RESPONSE_NOTE))
        request = {'id': 'response-passage', 'source_id': self.source_id, 'anchor': RESPONSE_NOTE[:30],
                   'characters': 40}
        review_excerpts.validate_passage_request(request, 'invalid_reading_response', kinds=review_excerpts.UNIT_KINDS)
        [answer], _ = self.answer([request], unit_sources=self.catalog)
        self.assertEqual((answer['status'], answer['text']), ('delivered', self.content[start:start + 40]))
        [unbound], _ = self.answer([request])
        self.assertEqual((unbound['status'], unbound['reason']), ('not_found', 'no_stored_text'))

    def test_a_record_is_checked_against_the_bytes_that_it_is_given(self):
        data = json.dumps({'id': 'read-1', 'notes': RECORD_NOTE}, sort_keys=True).encode('utf-8')
        reference = self.artifacts.put(data, 'application/json')
        sources = {('record', ('record_kind', 'reading'), ('record_id', 'read-1')):
                   {reference['sha256']: reference}}
        excerpt = {'record_kind': 'reading', 'record_id': 'read-1', 'text': RECORD_NOTE.replace(' ', '\n '),
                   'purpose': PURPOSE}
        review_excerpts.validate_excerpts([excerpt], 'invalid_reading_response', kinds=review_excerpts.UNIT_KINDS)
        [verified], _ = self.check([excerpt], unit_sources=sources)
        start = data.decode('utf-8').index(RECORD_NOTE)
        self.assertEqual((verified['status'], verified['record_kind'], verified['record_id'], verified['text']),
                         ('verified', 'reading', 'read-1', RECORD_NOTE))
        self.assertEqual((verified['locator']['start'], verified['locator']['end']), (start, start + len(RECORD_NOTE)))
        self.assertEqual(verified['artifact_descriptor']['sha256'], reference['sha256'])
        [unchecked], _ = self.check([excerpt])
        self.assertEqual((unchecked['status'], unchecked['reason']), ('unmatched', 'no_stored_text'))
        request = {'id': 'record-passage', 'record_kind': 'reading', 'record_id': 'read-1',
                   'anchor': RECORD_NOTE[:30], 'characters': 25}
        review_excerpts.validate_passage_request(request, 'invalid_reading_response', kinds=review_excerpts.UNIT_KINDS)
        [answer], _ = self.answer([request], unit_sources=sources)
        self.assertEqual((answer['status'], answer['text']), ('delivered', RECORD_NOTE[:25]))

    def test_the_excerpt_rules_of_bar_sessions_keep_the_version_id_shape(self):
        named = [{'source_id': self.source_id, 'text': RESPONSE_NOTE, 'purpose': PURPOSE},
                 {'record_kind': 'reading', 'record_id': 'read-1', 'text': RECORD_NOTE, 'purpose': PURPOSE},
                 {'version_id': 'arxiv:2601.00001v1', 'text_sha256': '0' * 64, 'text': RECORD_NOTE,
                  'purpose': PURPOSE}]
        for value in named:
            with self.subTest(value=value):
                review_excerpts.validate_excerpts([value], 'invalid_reading_response', kinds=review_excerpts.UNIT_KINDS)
                with self.assertRaises(ResearchError) as raised:
                    review_excerpts.validate_excerpts([value], 'invalid_value_review')
                self.assertEqual(raised.exception.code, 'invalid_value_review')
        for value in ({'version_id': 'arxiv:2601.00001v1', 'record_kind': 'reading', 'record_id': 'read-1',
                       'text': RECORD_NOTE, 'purpose': PURPOSE},
                      {'record_kind': 'reading', 'text': RECORD_NOTE, 'purpose': PURPOSE},
                      {'source_id': '', 'text': RESPONSE_NOTE, 'purpose': PURPOSE}):
            with self.subTest(value=value), self.assertRaises(ResearchError) as raised:
                review_excerpts.validate_excerpts([value], 'invalid_reading_response', kinds=review_excerpts.UNIT_KINDS)
            self.assertEqual(raised.exception.code, 'invalid_reading_response')
        with self.assertRaises(ResearchError) as raised:
            review_excerpts.validate_excerpts([named[1]], 'invalid_reading_response', kinds=('fulltext',))
        self.assertEqual(raised.exception.code, 'invalid_reading_response')

    def test_a_bound_catalog_rejects_ambiguous_and_mismatched_whole_hashes(self):
        second = self.artifacts.put((RESPONSE_NOTE + ' A later capture.').encode(), 'text/plain')
        key = ('search_response', ('source_id', self.source_id))
        catalog = {key: {self.response['sha256']: self.response, second['sha256']: second}}
        excerpt = {'source_id': self.source_id, 'text': RESPONSE_NOTE, 'purpose': PURPOSE}
        entries, _ = self.check([excerpt, dict(excerpt, text_sha256=self.response['sha256']),
                                 dict(excerpt, text_sha256='0' * 64)], unit_sources=catalog)
        self.assertEqual([entry['status'] for entry in entries], ['unmatched', 'verified', 'unmatched'])
        [entry], _ = self.check([excerpt], unit_sources={})
        self.assertEqual(entry['reason'], 'no_stored_text')

    def test_a_reading_response_refuses_a_passage_request_with_mixed_identity_fields(self):
        from research_harness import openness_reading
        binding = {'subject_digest': 'a' * 64, 'claim_digest': 'b' * 64, 'reviewer': {'actor_id': 'reviewer'}}
        packet = {'reading_turn': dict(binding, protocol=openness_reading.PROTOCOL)}
        request = {'id': 'mixed', 'version_id': 'arxiv:2601.00001v1', 'source_id': self.source_id,
                   'anchor': RESPONSE_NOTE, 'characters': 30}
        output = dict(binding, schema_version=1, stage='reading', excerpts=[], notes='Notes of the turn.',
                      passage_requests=[request])
        with self.assertRaises(ResearchError) as raised:
            openness_reading.validate_turn_output(packet, output)
        self.assertEqual(raised.exception.code, 'invalid_reading_response')
        del request['version_id']
        openness_reading.validate_turn_output(packet, output)
