"""Refused outputs identify the failed constraint and its output location."""

import copy
import json
import unittest

from research_harness import bar_sources, native_review, openness_reading, review_excerpts, review_protocol
from research_harness.errors import ResearchError
import test_research_claude_result_text as native_fixtures
import test_reviewer_excerpt_sessions as phase_fixtures


class ReadingDiagnosticTests(unittest.TestCase):
    def setUp(self):
        self.binding = {'subject_digest': 'subject', 'claim_digest': 'claim', 'reviewer': {'actor_id': 'reader'}}
        self.packet = {'reading_turn': dict(self.binding, protocol=openness_reading.PROTOCOL)}
        self.output = dict(self.binding, schema_version=1, stage='reading', notes='Read the supplied units.',
                           excerpts=[], passage_requests=[])
        self.passage = {'id': 'p', 'version_id': 'arxiv:2601.00001v1',
                        'anchor': 'A sufficiently long verbatim anchor.', 'characters': 100}

    def assert_failure(self, output, path, *, packet=None, **details):
        with self.assertRaises(ResearchError) as caught:
            openness_reading.validate_turn_output(packet or self.packet, output)
        self.assertEqual(caught.exception.code, 'invalid_reading_response')
        actual = caught.exception.details or {}
        self.assertEqual(actual.get('path'), path)
        for key, value in details.items():
            self.assertEqual(actual.get(key), value, key)
        return actual

    def test_reading_refusal_identifies_first_failed_field(self):
        cases = [({'schema_version': 2, 'stage': 'wrong'}, '/schema_version', {}),
                 ({'stage': 'wrong'}, '/stage', {}),
                 ({'subject_digest': 'wrong', 'claim_digest': 'wrong'}, '/subject_digest', {}),
                 ({'claim_digest': 'wrong'}, '/claim_digest', {}),
                 ({'reviewer': {}}, '/reviewer', {}),
                 ({'notes': ''}, '/notes', {'characters': 0, 'minimum_characters': 1}),
                 ({'notes': ' '}, '/notes', {'characters': 1, 'minimum_characters': 1}),
                 ({'notes': 'x' * 20001}, '/notes', {'characters': 20001, 'maximum_characters': 20000}),
                 ({'notes': None}, '/notes', {}),
                 ({'excerpts': {}}, '/excerpts', {}),
                 ({'passage_requests': {}}, '/passage_requests', {})]
        for change, path, details in cases:
            with self.subTest(change=tuple(change), path=path):
                self.assert_failure(dict(self.output, **change), path, **details)
        self.assert_failure([], '')
        final = {'native': self.binding, 'reading_record': {'protocol': openness_reading.PROTOCOL,
                 'allowances': review_excerpts.create_allowances()}}
        self.assert_failure(self.output, '/passage_requests', packet=final, items=0, minimum_items=1)
        final['reading_record']['allowances']['passages']['used'] = 100000
        self.assert_failure(dict(self.output, passage_requests=[self.passage]), '/passage_requests', packet=final,
                            used_characters=100000, maximum_characters=100000)

    def test_reading_item_refusal_uses_index_when_id_is_unavailable(self):
        missing = dict(self.passage)
        del missing['id']
        cases = [([None], '/passage_requests/0', None),
                 ([missing], '/passage_requests/0', None),
                 ([dict(self.passage, id=3)], '/passage_requests/0/id', None),
                 ([dict(self.passage, id=' ')], '/passage_requests/0/id', None),
                 ([self.passage, self.passage], '/passage_requests/1/id', 'p'),
                 ([dict(self.passage, source_id='mixed')], '/passage_requests/0', 'p'),
                 ([dict(self.passage, version_id='')], '/passage_requests/0/version_id', 'p'),
                 ([dict(self.passage, anchor='')], '/passage_requests/0/anchor', 'p'),
                 ([dict(self.passage, characters=20001)], '/passage_requests/0/characters', 'p')]
        for passages, path, identifier in cases:
            with self.subTest(path=path, passages=passages):
                details = self.assert_failure(dict(self.output, passage_requests=passages), path)
                self.assertEqual(details.get('request_id'), identifier)
        for excerpt, suffix in ((None, ''), ({'version_id': 'v', 'text': '', 'purpose': 'why'}, '/text'),
                                ({'version_id': 'v', 'text': 'copied', 'purpose': ''}, '/purpose'),
                                ({'version_id': '', 'text': 'copied', 'purpose': 'why'}, '/version_id')):
            with self.subTest(excerpt=excerpt):
                details = self.assert_failure(dict(self.output, excerpts=[excerpt]), '/excerpts/0' + suffix)
                self.assertNotIn('request_id', details)

    def test_passage_diagnostics_use_normalized_character_count(self):
        anchor = '  Lemma 35.   Let Wℓ,m  '
        self.assertGreater(len(anchor), 20)
        self.assert_failure(dict(self.output, passage_requests=[dict(self.passage, anchor=anchor)]),
                            '/passage_requests/0/anchor', request_id='p', normalized_characters=18,
                            minimum_characters=20)
        for characters in (0, 20001, True):
            with self.subTest(characters=characters):
                self.assert_failure(dict(self.output, passage_requests=[dict(self.passage, characters=characters)]),
                                    '/passage_requests/0/characters', characters=characters,
                                    minimum_characters=1, maximum_characters=20000)
        openness_reading.validate_turn_output(self.packet, dict(self.output,
            passage_requests=[dict(self.passage, anchor='  ' + 'ﬁ' * 10 + '  ', characters=1)]))

    def test_invalid_string_request_ids_use_only_the_item_index(self):
        for identifier in ('p\x00', 'p\ud800'):
            request = dict(self.passage, id=identifier)
            with self.subTest(identifier=repr(identifier)):
                details = self.assert_failure(dict(self.output, passage_requests=[request]), '/passage_requests/0/id')
                self.assertNotIn('request_id', details)
                with self.assertRaises(ResearchError) as caught:
                    bar_sources.requests({'source_requests': [dict(request, kind='passage')]}, passages=True)
                self.assertEqual(caught.exception.details['path'], '/source_requests/0/id')
                self.assertNotIn('request_id', caught.exception.details)
        self.assert_failure(dict(self.output, passage_requests=[dict(self.passage, id='lemma-α', anchor='short')]),
                            '/passage_requests/0/anchor', request_id='lemma-α')


class NativeDiagnosticTests(unittest.TestCase):
    def test_native_refusals_preserve_message_and_field(self):
        fixture = native_fixtures.ClaudeResultTextTests()
        self.addCleanup(fixture.doCleanups)
        fixture.setUp()
        result = fixture.build_output()
        proposal = dict(result, schema_version=1, unresolved_objections=[],
                        findings={key: '' for key in native_review.PROPOSAL_FINDINGS})
        recovery = dict(result, schema_version=1, unresolved_objections=[],
                        findings={key: 'Checked.' for key in native_review.RECOVERY_FINDINGS})
        del recovery['claim_digest']
        openness = dict(result, schema_version=2, findings=dict(result['findings'], openness={
            'outcome': 'unresolved', 'statement_digest': 'claim', 'evidence': ['a' * 64],
            'searched_at': '2026-02-30', 'coverage': 'Searched.', 'reason': 'A gap remains.'}))
        cases = [('proposal', dict(proposal, claim_digest='wrong'), '/claim_digest'),
                 ('proposal', dict(proposal, unresolved_objections=[{'blocking': 'yes', 'description': ''}]),
                  '/unresolved_objections/0/blocking'),
                 ('result', dict(result, findings=dict(result['findings'], policy='')), '/findings/policy'),
                 ('strategy', dict(result, findings=dict(result['findings'], dependencies=[])), '/findings/dependencies'),
                 ('recovery', dict(recovery, findings=dict(recovery['findings'], delta='')), '/findings/delta'),
                 ('recovery', dict(recovery, unresolved_objections=[{}]), '/unresolved_objections'),
                 ('openness', openness, '/findings/openness/searched_at'),
                 ('openness', dict(openness, findings=dict(openness['findings'], openness=dict(
                     openness['findings']['openness'], searched_at='2026-10-29', minimum={}))),
                  '/findings/openness/minimum')]
        for kind, output, path in cases:
            with self.subTest(kind=kind, path=path):
                fixture.native.update(kind=kind if kind in ('proposal', 'recovery') else 'result', contract={})
                fixture.native['subject'] = {'context_digest': 'strategy'} if kind == 'strategy' else {}
                packet = {'protocol': review_protocol.PROTOCOL, 'role': 'native_math', 'native': fixture.native}
                packet['digest'] = review_protocol.digest(packet)
                fixture.assignment.update(packet=review_protocol._put_json(fixture.artifacts, packet),
                                          packet_digest=packet['digest'])
                fixture.request = native_fixtures.transport.request_body(fixture.route,
                    fixture.assignment['prompt']['text'], packet)
                attempt = fixture.observe_text(json.dumps(output))
                refusal = fixture.classify_attempt(attempt)
                with self.assertRaises(ResearchError) as caught:
                    native_review.validate_output(fixture.native, output)
                self.assertEqual(refusal['details']['message'], caught.exception.message)
                self.assertEqual(refusal['details']['path'], path)
                if kind == 'openness':
                    with self.assertRaises(ResearchError) as final:
                        openness_reading.validate_turn_output(dict(packet, reading_record={}), output)
                    self.assertEqual(final.exception.details['path'], path)
        for kind in ('result', 'recovery'):
            native_review.validate_output({'kind': kind}, {'decision': 'reject', 'reason': 'Cannot approve.'})


class PhaseDiagnosticTests(phase_fixtures.ExcerptSessionCase):
    def test_inventory_page_refusal_reports_range(self):
        self.assign('a')
        output = self.build_request_turn([{'id': 'too-far', 'kind': 'inventory_page', 'page': 100000}])
        self.invoke('a', output)
        refusal = self.assert_refusal('invalid_review_source_request', lambda: self.record_final('a', output),
                                      'attempt-a')
        details = refusal['details']
        self.assertEqual(details['path'], '/source_requests/0/page')
        self.assertEqual(details['request_id'], 'too-far')
        self.assertEqual(details['page'], 100000)
        self.assertEqual(details['minimum_page'], 0)
        self.assertGreaterEqual(details['maximum_page'], 0)
        self.assertEqual(details['maximum_page'], max(0, (details['total'] - 1) // details['page_size']))

    def test_phase_refusal_preserves_message_and_existing_details(self):
        self.assign('a')
        self.invoke('a', self.build_request_turn(self.requests[:1]))
        self.assign('b', prior_assignment_id='a')
        passage = {'id': 'lemma-35', 'kind': 'passage', 'version_id': self.versions[0],
                   'anchor': 'Lemma 35. Let Wℓ,m', 'characters': 100}
        output = self.build_request_turn([passage])
        self.invoke('b', output)
        refusal = self.assert_refusal('invalid_review_source_request', lambda: self.record_final('b', output),
                                      'attempt-b')
        self.assertEqual(refusal['details'], {'message': 'A passage anchor is a verbatim phrase of at least 20 characters',
            'path': '/source_requests/0/anchor', 'request_id': 'lemma-35',
            'normalized_characters': 18, 'minimum_characters': 20})
        self.assign('c', reviewer='reviewer-c')
        malformed = copy.deepcopy(self.review_response('bar'))
        del malformed['value']['status']
        self.invoke('c', malformed)
        refusal = self.assert_refusal('invalid_value_review', lambda: self.record_final('c', malformed), 'attempt-c')
        self.assertEqual(refusal['details']['path'], '/value')
        self.assertEqual(refusal['details']['missing_fields'], ['status'])
        self.assertIn('status', refusal['details']['message'])
        structured = self.review_response('bar')
        structured['value']['assurances'] = self.review_response('slate')['value']['assurances'][:1]
        phase_fixtures.invoke_codex(self, 'c', structured, 'attempt-c-retry')
        refusal = self.assert_refusal('invalid_value_review', lambda: self.record_final('c', structured), 'attempt-c-retry')
        self.assertEqual(refusal['details'], {'message': 'Address all phase-required independent assurances',
                                             'path': '/value/assurances', 'required': []})
