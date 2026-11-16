"""The named-gap index is frozen from bound originals, not from search excerpts."""

import copy
import json
import unittest

from research_harness import review_search_delivery as delivery
from research_harness.evidence import digest
from research_harness.errors import ResearchError
from research_harness.problem_openness import serialize_record
from test_openness_reading_v7_turns import MemoryArtifacts


class FindingIndexTests(unittest.TestCase):
    def setUp(self):
        self.artifacts = MemoryArtifacts()
        self.reading = dict(id='reading', version_id='arxiv:2601.12345v2', depth='fulltext')
        self.searches = [dict(id='search-' + str(i), queries=['spectral radius graph'],
            dispositions=[dict(work_id='other', disposition='out_of_scope', reason='Different dimension.')],
            scope='Finite graphs', resolved=[], row_dispositions=[]) for i in range(2)]
        self.records = dict(reading={'reading': self.reading}, literature_search={s['id']: s for s in self.searches},
            work={self.reading['version_id']: dict(work_id='arxiv:2601.12345',
                aliases=['doi:10.0000/example'], title='A finite graph theorem')})
        self.native = dict(scientific_context=dict(openness_minimum=dict(statement='Statement', neighbours=['Neighbour'],
            searches=[dict(formulation=f, search_digest=digest(s)) for f, s in zip(('Statement', 'Neighbour'), self.searches)],
            reading_digests=[digest(self.reading)], supporting_reading_digests=[], family_readings=[])), evidence=[])
        for row in self.searches + [self.reading]:
            reference = self.artifacts.put(serialize_record(row), 'application/json')
            self.native['evidence'].append(dict(digest=reference['sha256'], artifact=reference))

    def test_shared_fields_and_reading_identity_are_frozen_once(self):
        index = delivery.build_finding_index(self.records, self.artifacts, self.native)
        manifest = delivery.plan_search_delivery([dict(b, search=s) for b, s in zip(
            self.native['scientific_context']['openness_minimum']['searches'], self.searches)])['manifest']
        for field in ('queries', 'dispositions'):
            key = manifest['searches'][0]['shared_fields'][field]
            self.assertEqual(index['searches'][0][field], key)
            self.assertEqual(index['searches'][1][field], key)
            self.assertEqual(index['fields'][key], self.searches[0][field])
        self.assertEqual(index['read_works'], [dict(reading_id='reading', reading_digest=digest(self.reading),
            version_id=self.reading['version_id'], identifiers=[self.reading['version_id'],
                'arxiv:2601.12345', 'doi:10.0000/example'], title='A finite graph theorem')])
        delivery.verify_finding_index(self.records, self.artifacts, self.native, index)

    def test_page_selects_searches_and_fields_for_only_its_formulations(self):
        index = delivery.build_finding_index(self.records, self.artifacts, self.native, formulations=['Neighbour'])
        self.assertEqual([row['record_id'] for row in index['searches']], ['search-1'])
        self.assertEqual(index['searches'][0]['formulations'], ['Neighbour'])
        self.assertEqual(len(index['read_works']), 1)

    def test_changed_field_reference_or_reading_is_refused(self):
        original = delivery.build_finding_index(self.records, self.artifacts, self.native)
        for change in ('value', 'binding', 'reading'):
            index = copy.deepcopy(original)
            if change == 'value':
                index['fields'][index['searches'][0]['queries']] = ['changed']
            elif change == 'binding':
                index['fields'][digest(['changed'])] = ['changed']
                index['searches'][0]['queries'] = digest(['changed'])
            else:
                index['read_works'][0]['reading_digest'] = '0' * 64
            with self.subTest(change=change), self.assertRaises(ResearchError) as caught:
                delivery.verify_finding_index(self.records, self.artifacts, self.native, index)
            self.assertEqual(caught.exception.code, 'openness_reading_incomplete')


    def test_later_work_metadata_does_not_rewrite_assignment_titles(self):
        index = delivery.build_finding_index(self.records, self.artifacts, self.native)
        self.records['work'][self.reading['version_id']]['title'] = 'A later title'
        delivery.verify_finding_index(self.records, self.artifacts, self.native, index)

    def test_invalid_index_rows_are_refused_before_a_finding_reads_them(self):
        original = delivery.build_finding_index(self.records, self.artifacts, self.native)
        for change in ('reference', 'reading'):
            index = copy.deepcopy(original)
            if change == 'reference':
                index['searches'][0]['queries'] = []
            else:
                index['read_works'][0]['title'] = {}
            with self.subTest(change=change), self.assertRaises(ResearchError) as caught:
                delivery.validate_finding_index(index)
            self.assertEqual(caught.exception.code, 'openness_reading_incomplete')


class UnreadFindingVersionTests(unittest.TestCase):
    def test_version_two_validator_fields_have_composition_owners(self):
        import inspect
        from openness_named_gap_fixtures import build_packet
        from research_harness import native_review, review_composition
        rule = review_composition.find_rule('native_math', build_packet('unsearched-term', 'v7-final'))
        sources = {row['validator']: inspect.getsource(getattr(native_review, row['validator'].split('.')[1]))
                   for row in rule['validator_contracts']}
        review_composition.validate_validator_ownership(rule, sources)

    def test_closing_gap_can_quote_a_verified_excerpt_of_the_reading_record(self):
        from openness_named_gap_fixtures import build_packet, build_ground, build_fixed_answer
        from research_harness.openness_final_pages import build_validation_envelope
        from research_harness.native_review import validate_finding
        packet = build_packet('unsearched-term', 'v7-final')
        packet.pop('reading_record', None)
        entries = packet['reading_final'].pop('entries')
        packet['reading_final']['closing'] = dict(rows=[])
        artifacts = MemoryArtifacts()
        assignment = dict(context=dict(native_packet=artifacts.put(serialize_record(packet['native']), 'application/json')),
            reading_chain=dict(state=artifacts.put(serialize_record(dict(entries=entries)), 'application/json')))
        envelope = build_validation_envelope(artifacts, assignment, packet)
        validate_finding(envelope, build_fixed_answer(envelope, ground=build_ground(packet)), finding_version=2)

    def test_operational_unread_set_uses_packet_defect_without_exempting_named_gaps(self):
        from research_harness.native_review import validate_finding
        from test_openness_finding_answer import build_packet, build_answer
        artifacts = MemoryArtifacts()
        reference = artifacts.put(serialize_record(dict(rows=[])), 'application/json')
        packet = build_packet()
        packet['finding_index'] = dict(searches=[], fields={}, read_works=[])
        packet['reading_final'] = dict(protocol='exactory-openness-reading-v7', page=dict(targets=['t1']),
                                       unread=dict(artifact=reference))
        ground = dict(kind='packet_defect', text='The provider left ranges unread.', refs=[reference])
        validate_finding(packet, build_answer(packet, 'unresolved', [ground]), finding_version=2, artifacts=artifacts)
        ground['kind'] = 'reviewer_gap'
        with self.assertRaises(ResearchError) as caught:
            validate_finding(packet, build_answer(packet, 'unresolved', [ground]), finding_version=2, artifacts=artifacts)
        self.assertEqual(caught.exception.details['rule'], 'gap_fields')
        validate_finding(packet, build_answer(packet, 'unresolved', [ground]), artifacts=artifacts)


class FindingIndexChainTests(unittest.TestCase):
    def test_changed_index_values_bindings_and_readings_refuse_chain_verification(self):
        from openness_named_gap_fixtures import fixture_case, build_fixed_answer
        from research_harness import openness_reading as reading
        for form in ('v6', 'v7'):
            case = fixture_case('variant-search', form)
            try:
                native = case.export()
                identifier, packet = case.finish_reading(native)
                case.invoke(identifier, build_fixed_answer(packet))
                originals = case.store.snapshot()['records']
                reading.verify_chain(originals, case.artifacts, originals['review_assignment'][identifier])
                for change in ('value', 'binding', 'reading'):
                    records = copy.deepcopy(originals)
                    changed = copy.deepcopy(packet)
                    index = changed['finding_index']
                    if change == 'value':
                        index['fields'][index['searches'][0]['queries']] = ['invented query']
                    elif change == 'binding':
                        key = digest(['invented query'])
                        index['fields'][key] = ['invented query']
                        index['searches'][0]['queries'] = key
                    else:
                        index['read_works'][0]['reading_digest'] = '0' * 64
                    changed.pop('digest', None)
                    changed['digest'] = digest(changed)
                    assignment = records['review_assignment'][identifier]
                    assignment.update(packet=case.artifacts.put(serialize_record(changed), 'application/json'),
                                      packet_digest=changed['digest'])
                    with self.subTest(form=form, change=change), self.assertRaises(ResearchError) as caught:
                        reading.verify_chain(records, case.artifacts, assignment)
                    self.assertEqual(caught.exception.code, 'openness_reading_incomplete')
                    from research_harness.problem_openness import prepare_openness
                    with self.subTest(boundary='record', form=form, change=change), self.assertRaises(ResearchError) as caught:
                        prepare_openness(records, case.artifacts, dict(assignment_id=identifier), case.store.revision + 1)
                    self.assertEqual(caught.exception.code, 'openness_reading_incomplete')
            finally:
                case.doCleanups()



if __name__ == '__main__':
    unittest.main()
