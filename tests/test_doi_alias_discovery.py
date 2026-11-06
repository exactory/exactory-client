"""Synthetic graph-rigidity shapes; only public identifiers come from the copy."""

import copy
import json
from unittest.mock import patch

from literature_fixtures import LiteratureCase
from strategy_fixtures import StrategyCase
from research_fixtures import atom, client, crossref, entry, json_response, openalex
from research_harness import acquisition, bar_sources, review_protocol
from research_harness.reading import record_availability
import test_research_bar_sources as bar_fixtures


PAIRS = (
    ('openalex:W1980120856', 'doi:10.1007/s00454-005-1225-8'),
    ('openalex:W1993907770', 'doi:10.1016/j.aam.2005.03.004'),
    ('openalex:W2148291654', 'doi:10.1016/j.jctb.2004.11.002'),
    ('openalex:W2189909658', 'doi:10.1016/j.jctb.2015.11.003'),
)


class AliasFixtures:
    def import_data(self, provider, data, **options):
        self.sequence += 1
        return acquisition.import_response(
            self.store, provider, data, source_url='https://example.org/captured',
            captured_at='2026-11-01T00:00:00Z', request_id='alias-' + str(self.sequence),
            expected_revision=self.store.revision, **options)

    def pair(self, pair=PAIRS[0], *, oa_abstract=False, doi_abstract=False):
        oa_id, doi_id = pair
        observed = openalex()
        observed.update(id='https://openalex.org/' + oa_id[9:], doi='https://doi.org/' + doi_id[4:])
        observed['ids'] = {'openalex': observed['id'], 'doi': observed['doi']}
        if not oa_abstract:
            observed.pop('abstract_inverted_index', None)
        self.sequence += 1
        http, _, _ = client([json_response(observed)])
        acquisition.acquire_work(self.store, oa_id, http=http, request_id='oa-' + str(self.sequence),
            expected_revision=self.store.revision)
        observed = crossref()
        observed['message']['DOI'] = doi_id[4:]
        if not doi_abstract:
            observed['message'].pop('abstract', None)
        self.sequence += 1
        http, _, _ = client([json_response(observed)])
        acquisition.acquire_work(self.store, doi_id, http=http, request_id='doi-' + str(self.sequence),
            expected_revision=self.store.revision)
        return pair

    def mapped(self, identifier, abstract=None, aliases=()):
        data = {'id': identifier, 'title': 'Synthetic article', 'aliases': list(aliases)}
        mapping = {'id': '/id', 'title': '/title', 'aliases': '/aliases'}
        if abstract is not None:
            data['abstract'] = abstract
            mapping['abstract'] = '/abstract'
        return self.import_data('web', json.dumps(data).encode(), media_type='application/json', mappings=[mapping])

    def source(self, identifier, records=None):
        return bar_sources._source(records or self.store.snapshot()['records'], self.artifacts,
            {'id': 'abstract', 'kind': 'source', 'version_id': identifier, 'depth': 'abstract'}, {})

    def qualify(self, identifier):
        records = self.store.snapshot()['records']
        identities = {identifier} | set(records['work'][identifier]['aliases'])
        sources = list(dict.fromkeys(source for work in records['work'].values()
            if work['id'] in identities
            for source in work['source_ids'] if records['source'][source]['provider'] in ('openalex', 'crossref')))
        value = {'id': 'absence-' + identifier, 'profile': 'research', 'version_id': identifier,
            'depth': 'abstract', 'source_ids': sources, 'reason': 'Synthetic captured registry absence.',
            'policy': {'id': 'registry-abstract-absent', 'minimum_attempts': len(sources),
                       'allowed_statuses': [200], 'rationale': 'Every addressing registry was captured.'}}
        self.mutate(record_availability, value)
        return value


class AliasDiscoveryTests(AliasFixtures, LiteratureCase):
    def test_four_pairs_and_springer_1115_byte_excerpt_remain_qualified(self):
        for pair in PAIRS:
            self.pair(pair)
        self.mapped(PAIRS[0][1], 'Synthetic partial abstract. ' + 'x' * (1115 - len('Synthetic partial abstract. ')))
        for oa_id, doi_id in PAIRS:
            with self.subTest(pair=oa_id):
                value = self.qualify(oa_id)
                delivery = self.source(oa_id)
                with patch.object(bar_sources, 'find_source_candidates', return_value={'candidates': [], 'ambiguities': []}):
                    legacy = self.source(oa_id)
                without_candidates = {key: value for key, value in delivery.items()
                                      if key not in ('abstract_candidates', 'identity_ambiguities', 'candidate_source_deltas')}
                self.assertEqual(without_candidates, legacy)
                self.assertEqual(delivery['status'], 'unavailable_qualified')
                self.assertEqual(delivery['request']['version_id'], oa_id)
                self.assertFalse(delivery['reading_credit'])
                self.assertFalse(delivery['scientific_truth_certified'])
                self.assertEqual(delivery['availability']['id'], value['id'])
                candidates = delivery.get('abstract_candidates', [])
                self.assertEqual([row['version_id'] for row in candidates], [doi_id])
                candidate = candidates[0]
                self.assertFalse(candidate['reusable'])
                self.assertEqual(candidate['identity_status'], 'family_only')
                self.assertTrue(candidate['identity_provenance'])
                if doi_id == PAIRS[0][1]:
                    self.assertEqual(candidate['abstracts'][0]['artifact_descriptor']['size'], 1115)
                    self.assertEqual(candidate['abstracts'][0]['completeness'], 'partial')
                    self.assertEqual(delivery['evidence'], [self.store.snapshot()['records']['source'][s]['response']
                        for s in value['source_ids']])

    def test_forward_and_reverse_discovery_keeps_complete_donors_as_alternatives(self):
        for index, pair in enumerate(PAIRS[1:3]):
            self.pair(pair, oa_abstract=index == 0, doi_abstract=index == 1)
            requested, donor = pair[::-1] if index == 0 else pair
            with self.subTest(requested=requested):
                delivery = self.source(requested)
                self.assertEqual(delivery['status'], 'unavailable')
                self.assertEqual(delivery['request']['version_id'], requested)
                candidate = delivery['abstract_candidates'][0]
                self.assertEqual(candidate['version_id'], donor)
                self.assertFalse(candidate['reusable'])
                self.assertEqual(candidate['identity_status'], 'family_only')
                held = self.store.snapshot()['records']['work'][donor]['abstracts'][0]
                self.assertNotIn(held['artifact'], delivery['evidence'])
                self.assertEqual(candidate['abstracts'][0]['completeness'], 'complete')
                self.assertFalse(delivery['reading_credit'])

    def test_discovery_does_not_require_a_separate_requested_work_row(self):
        oa_id, doi_id = self.pair(oa_abstract=True)
        records = copy.deepcopy(self.store.snapshot()['records'])
        del records['work'][doi_id]
        delivery = self.source(doi_id, records)
        self.assertEqual(delivery['status'], 'unavailable')
        self.assertEqual(delivery['abstract_candidates'][0]['version_id'], oa_id)
        self.assertEqual(delivery['request']['version_id'], doi_id)

    def test_competing_doi_families_never_choose_the_first(self):
        oa_id, doi_id = self.pair(doi_abstract=True)
        other = 'doi:10.1007/synthetic-competitor'
        self.mapped(oa_id, aliases=[other])
        self.mapped(other, 'Alternative abstract')
        delivery = self.source(oa_id)
        self.assertEqual(delivery['status'], 'unavailable')
        candidates = delivery.get('abstract_candidates', [])
        self.assertEqual({row['version_id'] for row in candidates}, {doi_id, other})
        self.assertTrue(delivery['identity_ambiguities'])
        self.assertTrue(all(not row['reusable'] for row in candidates))
        self.assertEqual(delivery['evidence'], [])

    def test_competing_unacquired_doi_identities_still_report_ambiguity(self):
        identifier = 'openalex:W123'
        self.mapped(identifier, aliases=['doi:10.1234/one', 'doi:10.1234/two'])
        delivery = self.source(identifier)
        self.assertEqual(delivery['status'], 'unavailable')
        self.assertTrue(delivery.get('identity_ambiguities'))
        self.assertEqual(delivery['abstract_candidates'], [])

    def test_reverse_competing_openalex_assertions_remain_alternatives(self):
        oa_id, doi_id = self.pair(oa_abstract=True)
        other = 'openalex:W999999'
        self.pair((other, doi_id), oa_abstract=True)
        delivery = self.source(doi_id)
        self.assertEqual(delivery['status'], 'unavailable')
        self.assertEqual({row['version_id'] for row in delivery.get('abstract_candidates', [])}, {oa_id, other})
        self.assertTrue(delivery['identity_ambiguities'])
        self.assertEqual(delivery['evidence'], [])

    def test_preprint_and_journal_versions_are_only_family_alternatives(self):
        oa_id, doi_id = self.pair()
        preprint = 'arxiv:2601.00099v1'
        self.import_data('arxiv', atom([entry(preprint[6:], doi=doi_id[4:])], total=1))
        delivery = self.source(doi_id)
        self.assertEqual(delivery['status'], 'unavailable')
        candidates = delivery.get('abstract_candidates', [])
        self.assertIn(preprint, [row['version_id'] for row in candidates])
        self.assertTrue(all(not row['reusable'] for row in candidates))
        self.assertEqual(delivery['evidence'], [])
        self.assertEqual(self.source(preprint)['status'], 'delivered')

    def test_family_only_mapping_is_an_alternative_without_identity_credit(self):
        requested = 'doi:10.1234/requested'
        donor = 'doi:10.1234/donor'
        self.mapped(requested, aliases=[donor])
        self.mapped(donor, 'Partial donor')
        delivery = self.source(requested)
        self.assertEqual(delivery['status'], 'unavailable')
        self.assertEqual(delivery['abstract_candidates'][0]['identity_status'], 'family_only')
        self.assertEqual(delivery['evidence'], [])

    def test_corrupt_or_misbound_donor_cannot_supply_exact_identity(self):
        oa_id, doi_id = self.pair(oa_abstract=True)
        records = copy.deepcopy(self.store.snapshot()['records'])
        held = records['work'][oa_id]['abstracts'][0]
        held['artifact'] = self.artifacts.put(b'Substituted abstract', 'text/plain; charset=utf-8')
        delivery = self.source(doi_id, records)
        self.assertEqual(delivery['status'], 'unavailable')
        self.assertFalse(delivery['abstract_candidates'][0]['reusable'])


    def test_corrupt_optional_donor_does_not_block_exact_requested_source(self):
        oa_id, doi_id = self.pair(oa_abstract=True, doi_abstract=True)
        records = copy.deepcopy(self.store.snapshot()['records'])
        records['work'][oa_id]['abstracts'][0]['artifact']['size'] += 1
        delivery = self.source(doi_id, records)
        self.assertEqual(delivery['status'], 'delivered')
        candidate = delivery['abstract_candidates'][0]
        self.assertFalse(candidate['reusable'])
        self.assertEqual(candidate['abstracts'][0]['validation_error']['code'], 'artifact_corrupt')
        self.assertEqual(delivery['evidence'], [records['work'][doi_id]['abstract']])

    def test_corrupt_partial_candidate_keeps_qualified_absence(self):
        oa_id, doi_id = self.pair()
        self.mapped(doi_id, 'Synthetic partial donor')
        self.qualify(oa_id)
        records = copy.deepcopy(self.store.snapshot()['records'])
        records['work'][doi_id]['abstracts'][0]['artifact']['size'] += 1
        delivery = self.source(oa_id, records)
        self.assertEqual(delivery['status'], 'unavailable_qualified')
        self.assertEqual(delivery['abstract_candidates'][0]['abstracts'][0]['validation_error']['code'], 'artifact_corrupt')
        self.assertFalse(delivery['reading_credit'])
        self.assertFalse(delivery['scientific_truth_certified'])

    def test_openalex_multiple_locations_do_not_establish_abstract_version(self):
        oa_id, doi_id = self.pair(oa_abstract=True)
        observed = openalex()
        observed.update(id='https://openalex.org/' + oa_id[9:], doi='https://doi.org/' + doi_id[4:])
        observed['locations'] = [
            {'doi': doi_id[4:], 'version': 'publishedVersion'},
            {'doi': '10.48550/arxiv.2601.00099', 'version': 'submittedVersion'}]
        self.import_data('openalex', json.dumps(observed).encode())
        delivery = self.source(doi_id)
        self.assertEqual(delivery['status'], 'unavailable')
        self.assertFalse(delivery['abstract_candidates'][0]['reusable'])
        self.assertEqual(delivery['abstract_candidates'][0]['identity_status'], 'family_only')

    def test_candidate_delta_reports_donor_acquired_after_inventory(self):
        oa_id, doi_id = self.pair()
        records = self.store.snapshot()['records']
        original_rows = {identifier: {'source_capture_digest': bar_sources._capture_digest(work, records)}
                         for identifier, work in records['work'].items()}
        self.pair(doi_abstract=True)
        delivery = bar_sources._source(self.store.snapshot()['records'], self.artifacts,
            {'id': 'abstract', 'kind': 'source', 'version_id': oa_id, 'depth': 'abstract'}, original_rows)
        self.assertFalse(delivery['native_source_delta']['changed'])
        self.assertEqual(delivery['candidate_source_deltas'][0]['version_id'], doi_id)
        self.assertTrue(delivery['candidate_source_deltas'][0]['changed'])

class AliasReplayTests(AliasFixtures, StrategyCase):
    assign = bar_fixtures.BarSourceTests.assign
    packet = bar_fixtures.BarSourceTests.packet
    invoke = bar_fixtures.BarSourceTests.invoke
    request = bar_fixtures.BarSourceTests.request

    def setUp(self):
        super().setUp()
        self.sequence = 0
        self.setup_dossier()
        self.route()

    def test_saved_assignment_request_and_packet_replay_without_new_discovery(self):
        oa_id, doi_id = self.pair()
        self.qualify(oa_id)
        self.assign()
        self.request([{'id': 'abstract', 'kind': 'source', 'version_id': oa_id, 'depth': 'abstract'}])
        # An assignment saved before candidate reporting retains its original packet.
        with patch.object(bar_sources, 'find_source_candidates', return_value={'candidates': [], 'ambiguities': []}, create=True):
            self.assign('continued', prior_assignment_id='a')
        records = self.store.snapshot()['records']
        assignment = records['review_assignment']['continued']
        packet_bytes = self.artifacts.read(assignment['packet'])
        request = review_protocol.build_assignment_request(records, self.artifacts, assignment)
        response = self.review_response('bar', status='unresolved')
        response['value']['evidence'] = []
        self.invoke(response, 'continued')
        recorded_state = review_protocol.assignment_state(self.store.snapshot()['records'], self.artifacts, 'continued')
        self.assertTrue(recorded_state['ready'])
        self.pair(doi_abstract=True)
        records = self.store.snapshot()['records']
        with patch.object(bar_sources, 'find_source_candidates', side_effect=AssertionError('Replay rediscovered aliases')):
            self.assertEqual(review_protocol.build_assignment_request(records, self.artifacts, assignment), request)
            self.assertEqual(review_protocol.assignment_state(records, self.artifacts, 'continued')['output'], response)
        self.assertEqual(self.artifacts.read(assignment['packet']), packet_bytes)
        self.assertEqual(self.source(oa_id)['status'], 'unavailable')
