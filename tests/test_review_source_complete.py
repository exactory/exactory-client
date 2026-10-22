"""Complete main source selection retains native provenance and protected units."""

import copy
import json
from unittest.mock import patch

from literature_fixtures import LiteratureCase
from research_harness import bar_sources, review_protocol, review_transport
from research_harness import review_supplied_evidence
from research_harness import review_source_complete as complete
from research_harness.errors import ResearchError
from research_harness.evidence import digest
from test_review_html_delivery import ROUTE


class CompleteSourceTests(LiteratureCase):
    def setUp(self):
        super().setUp()
        self.version = self.metadata()
        self.html = self.capture(self.version, body='Complete HTML alternative, including its proof. ' * 200)
        self.pdf = self.capture(self.version, pdf_text='Complete PDF mathematical text with its proof. ' * 200 + '\f')
        self.records = self.store.snapshot()['records']
        self.request = {'id': 'decisive', 'kind': 'source', 'version_id': self.version, 'depth': 'fulltext'}
        with review_transport.text_only_delivery(True):
            self.delivery = bar_sources._source(self.records, self.artifacts, self.request, {})

    def test_one_complete_pdf_is_selected_with_exact_alternative_identity(self):
        value = complete.selection(self.records, self.artifacts, [self.delivery])
        self.assertEqual(value['sources'][0]['selected_text_sha256'], self.pdf['text']['sha256'])
        self.assertIn(self.html['text']['sha256'], value['alternate_body_sha256'])
        self.assertIn(self.pdf['text']['sha256'], value['protected_sha256'])

    def test_unverified_or_wrong_exact_version_cannot_be_selected(self):
        for field, value in (('origin_verified', False), ('observed_identifier', 'arxiv:2601.99999v1')):
            records = copy.deepcopy(self.records)
            records['source'][self.pdf['source_id']][field] = value
            with self.subTest(field=field):
                with self.assertRaises(ResearchError):
                    complete.selection(records, self.artifacts, [self.delivery])

    def test_required_unit_cannot_be_omitted_as_an_alternative_body(self):
        delivery = copy.deepcopy(self.delivery)
        delivery['declared_bundles'].append({'id': 'required', 'units': [{'id': 'figure', 'kind': 'figure',
            'link': {'artifact_descriptor': {'sha256': self.html['text']['sha256']}}}]})
        value = complete.selection(self.records, self.artifacts, [delivery])
        self.assertNotIn(self.html['text']['sha256'], value['alternate_body_sha256'])

    def test_partial_and_unavailable_sources_do_not_gain_complete_selection(self):
        for status in ('partial', 'unavailable'):
            delivery = dict(self.delivery, status=status)
            self.assertEqual(complete.selection(self.records, self.artifacts, [delivery])['sources'], [])

    def test_projection_restores_proof_and_preserves_an_existing_search_elision(self):
        proof = self.artifacts.put(b'# Exact independent proof\n' * 250, 'text/plain')
        search = self.artifacts.put(b'Raw earlier search body\n' * 250, 'text/plain')
        old = {'evidence': [{'artifact': ref, 'content': '', 'encoding': 'utf-8',
                            'delivery': 'body-elided-from-replayed-history', 'note': 'Existing omission.'}
                           for ref in (proof, search)], 'source_deliveries': [self.delivery]}
        history = [{'role': 'user', 'content': review_transport.PACKET_LABEL + json.dumps(old, sort_keys=True, ensure_ascii=False)},
                   {'role': 'assistant', 'content': 'The exact observed response must remain unchanged.'}]
        manifest = dict(complete.selection(self.records, self.artifacts, [self.delivery]),
            protocol=complete.PROTOCOL, history_digest=digest(history), restored_evidence=[proof],
            historical_search_elisions=[search['sha256']], source_requests=[self.request])
        packet = {complete.MARKER: manifest, 'source_deliveries': [self.delivery], 'pending_source_requests': []}
        packet['evidence'] = review_protocol._evidence(self.artifacts, packet)
        messages, sources = complete.project(packet, history)
        replayed = json.loads(messages[0]['content'][len(review_transport.PACKET_LABEL):])
        self.assertEqual(replayed['evidence'][0]['content'], self.artifacts.read(proof).decode())
        self.assertEqual(replayed['evidence'][1], old['evidence'][1])
        self.assertEqual(messages[1], history[1])
        self.assertEqual(sources[self.pdf['text']['sha256']], self.artifacts.read(self.pdf['text']).decode())
        self.assertEqual(old['evidence'][0]['content'], '')

    def test_restoration_preserves_only_frozen_source_elisions_and_restores_unknown_proofs(self):
        proof = self.artifacts.put(b'Exact current proof body\n' * 250, 'text/plain')
        background = self.artifacts.put(b'Previously reviewed background fulltext\n' * 250, 'text/plain')
        search = self.artifacts.put(b'Exact frozen raw search body\n' * 250, 'text/plain')
        packet = {'evidence': [{'artifact': ref, 'content': '', 'encoding': 'utf-8',
                               'delivery': 'body-elided-from-replayed-history'}
                              for ref in (proof, background, search)],
                  'problem_evidence': {'search': search,
                      'openness_searches': [{'search': {'responses': [{'source_id': 'frozen-search'}]}}]}}
        records = copy.deepcopy(self.records)
        records['source']['frozen-search'] = {'response': search}
        history = [{'role': 'user', 'content': review_transport.PACKET_LABEL + json.dumps(packet)}]
        historical = [{'original_descriptor': background, 'extraction_descriptor': background}]
        restored, searches, existing = complete._restoration(records, history, [self.delivery], historical)
        self.assertEqual(restored, [proof])
        self.assertEqual(searches, [search['sha256']])
        self.assertEqual(existing, sorted([background['sha256'], search['sha256']]))
        records.setdefault('literature_search', {})['new-search'] = {'responses': [{'source_id': 'later'}]}
        records['source']['later'] = {'response': proof}
        self.assertEqual(complete._restoration(records, history, [self.delivery], historical), (restored, searches, existing))
        self.assertEqual(complete._restoration(records, history, [self.delivery], [])[0],
                         sorted([proof, background], key=lambda ref: ref['sha256']))

    def test_missing_complete_source_fails_projection(self):
        manifest = dict(complete.selection(self.records, self.artifacts, [self.delivery]), protocol=complete.PROTOCOL,
                        history_digest=digest([]), restored_evidence=[], historical_search_elisions=[], source_requests=[self.request])
        packet = {complete.MARKER: manifest, 'source_deliveries': [self.delivery], 'evidence': [], 'pending_source_requests': []}
        with self.assertRaises(ResearchError):
            complete.project(packet, [])

    def test_transport_exposes_decoded_sources_without_capacity_deferral(self):
        manifest = dict(complete.selection(self.records, self.artifacts, [self.delivery]), protocol=complete.PROTOCOL,
                        history_digest=digest([]), restored_evidence=[], historical_search_elisions=[], source_requests=[self.request])
        packet = {complete.MARKER: manifest, 'source_deliveries': [self.delivery], 'pending_source_requests': [],
                  'continuation': {'assignment_id': 'prior'}, 'role': 'result'}
        packet['evidence'] = review_protocol._evidence(self.artifacts, packet)
        request = review_transport.request_body(ROUTE, 'Review exact JSON sources.', packet)
        logical = review_transport.logical_messages(request)
        supplied = json.loads(logical[-1]['content'][len(review_transport.PACKET_LABEL):])
        self.assertNotIn('source_capacity_delivery', supplied)
        entries = {entry['artifact']['sha256']: entry for entry in supplied['evidence']}
        self.assertEqual(entries[self.pdf['text']['sha256']]['content'], self.artifacts.read(self.pdf['text']).decode())
        self.assertEqual(entries[self.html['text']['sha256']]['delivery'], complete.ALTERNATE)
        self.assertEqual(supplied['pending_source_requests'], [])

    def test_legacy_messages_are_returned_unchanged(self):
        messages = [{'role': 'user', 'content': 'An ordinary exact input.'}]
        self.assertEqual(review_transport.logical_messages({'input': messages}), messages)

    def test_new_omitted_alternative_gains_no_credit_but_actual_prior_delivery_keeps_credit(self):
        original = {'evidence': review_protocol._evidence(self.artifacts, {'source_deliveries': [self.delivery]})}
        alternate = self.html['text']['sha256']
        omitted = copy.deepcopy(original)
        for entry in omitted['evidence']:
            if entry['artifact']['sha256'] == alternate:
                entry.update(content='', encoding='omitted', delivery=complete.ALTERNATE)
        with patch.object(review_supplied_evidence, 'lineage_packets', return_value=[omitted]):
            references, _ = review_supplied_evidence.supplied_context(self.records, self.artifacts, ['new'], frozen=True)
            self.assertNotIn(alternate, references)
        with patch.object(review_supplied_evidence, 'lineage_packets', return_value=[omitted, original]):
            references, _ = review_supplied_evidence.supplied_context(self.records, self.artifacts, ['new'], frozen=True)
            self.assertIn(alternate, references)


if __name__ == '__main__':
    import unittest
    unittest.main()
