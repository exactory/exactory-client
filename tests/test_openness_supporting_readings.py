"""UB3: citations in searches deliver supporting records through a complete chain."""

import copy
import json
from unittest.mock import patch

from openness_reading_fixtures import OpennessReadingCase
from research_harness import openness_reading as reading, problem_openness
from research_harness.evidence import digest
from research_harness.errors import ResearchError
from research_harness.literature import import_bundle, record_search
from research_harness.reading import record_reading


class SupportingReadingTests(OpennessReadingCase):
    reading_protocol = None
    route_model = 'gpt-6-astra'
    text_sizes = (121000,)

    def setUp(self):
        super().setUp()
        self.limit_input(1300000)
        self.support_version = self.metadata(2)
        self.support = self.abstract_note(self.support_version)
        self.support['id'] = 'support-note'
        self.mutate(record_reading, self.support)
        self.support = self.store.snapshot()['records']['reading']['support-note']

    def record_citing_search(self, field='dispositions.reason', citation='Read (support-note).'):
        payload = self.capture_search(found=[self.support_version])
        payload.update(id='citing-' + str(self.sequence), question=self.selected['statement'],
                       scope=self.selected['statement'], resolved=[])
        payload['dispositions'][0]['disposition'] = 'out_of_scope'
        if field in ('dispositions.reason', 'resolved.reason'):
            key = field.split('.')[0]
            if key == 'resolved':
                payload[key] = [dict(work_id=self.support_version, reason=citation)]
            else:
                payload[key][0]['reason'] = citation
        elif field == 'gaps':
            payload[field] = [citation]
        else:
            payload[field] = citation
        self.mutate(record_search, payload)
        return self.store.snapshot()['records']['literature_search'][payload['id']]

    def build_spec(self, searches, supporting=()):
        return dict(assignment_id='turn-1', reviewer_id='openness-reviewer', minimum=dict(
            searches=[dict(formulation=self.selected['statement'], search_id=row['id']) for row in searches],
            settling_work_id=None, duplicate_resolutions=[], supporting_reading_ids=list(supporting)))

    def export_supporting(self, searches, supporting=()):
        return problem_openness.export_openness(self.store, self.build_spec(searches, supporting),
                                               accept_gaps=True)

    def test_g3_search_export_plan_and_verification_require_the_supporting_reading(self):
        # Source: 20261113074000-openness-packet-cannot-deliver-what-its-searches-cite T1.
        search = self.record_citing_search()
        before = self.store.snapshot()['records']
        result = self.export_supporting([self.search, search])
        native = json.loads(self.artifacts.read(result['native_packet']))
        minimum = native['scientific_context']['openness_minimum']
        self.assertEqual(minimum['supporting_reading_digests'], [digest(self.support)])
        citation = dict(search_id=search['id'], search_digest=digest(search), fields=['dispositions.reason'])
        self.assertEqual(result['supporting_readings'], [dict(reading_id='support-note',
            reading_digest=digest(self.support), cited_by=[citation])])
        rows = reading.list_reading_rows(before, self.artifacts, native)
        supporting = next(row for row in rows if row.get('record_id') == 'support-note')
        self.assertEqual(supporting['role'], 'supporting')
        self.assertEqual(supporting['cited_by'], [citation])
        self.assertEqual(supporting['record_kind'], 'reading')
        self.assertEqual(json.loads(self.artifacts.read(supporting['artifact'])), self.support)
        self.assertFalse(any(row.get('version_id') == self.support_version for row in rows))
        self.assertEqual(self.store.snapshot()['records'], before)
        missing_search = copy.deepcopy(native)
        missing_search['evidence'] = [row for row in native['evidence'] if row['digest'] != digest(search)]
        with self.assertRaises(ResearchError) as caught:
            reading.list_record_rows(self.artifacts, missing_search)
        self.assertEqual(caught.exception.code, 'openness_reading_incomplete')
        turns = self.run_reading_turns(result['native_packet'], turn_count=8)
        planned = [row for identifier in turns[:-1]
                   for row in self.read_packet(identifier)['reading_turn']['reading_plan']['delivered_now']
                   if row.get('record_id') == 'support-note']
        self.assertEqual(len(planned), 1)
        self.assertEqual((planned[0]['role'], planned[0]['cited_by']), ('supporting', [citation]))
        assignment = self.store.snapshot()['records']['review_assignment'][turns[-1]]
        final = self.read_packet(turns[-1])
        self.invoke(turns[-1], self.build_native_result(final))
        records = self.store.snapshot()['records']
        verified = reading.verify_chain(records, self.artifacts, assignment)
        identity = dict(kind='record', record_kind='reading', record_id='support-note')
        self.assertIn(identity, [unit for turn in verified for unit in turn['delivered_units']])
        # Remove authenticated delivery credit for this unit, keeping every other observation intact.
        observe = reading._observe_turns
        def omit_support(*args, **kwargs):
            observations = copy.deepcopy(observe(*args, **kwargs))
            key = ('record', ('record_kind', 'reading'), ('record_id', 'support-note'))
            for observation in observations:
                observation['whole'].discard(key)
                observation['parts'] = {part for part in observation['parts'] if part[0] != key}
            return observations
        with patch.object(reading, '_observe_turns', side_effect=omit_support):
            with self.assertRaises(ResearchError) as caught:
                reading.verify_chain(records, self.artifacts, assignment)
        self.assertEqual(caught.exception.code, 'openness_reading_incomplete')

    def test_each_of_the_five_search_text_fields_binds_and_reports_a_reading(self):
        for field in ('dispositions.reason', 'resolved.reason', 'impact', 'scope', 'gaps'):
            with self.subTest(field=field):
                search = self.record_citing_search(field)
                result = self.export_supporting([self.search, search])
                native = json.loads(self.artifacts.read(result['native_packet']))
                self.assertEqual(native['scientific_context']['openness_minimum']['supporting_reading_digests'],
                                 [digest(self.support)])
                self.assertEqual(result['supporting_readings'][0]['cited_by'], [dict(
                    search_id=search['id'], search_digest=digest(search), fields=[field])])

    def test_citations_require_non_alphanumeric_boundaries_and_ignore_other_fields(self):
        for text, expected in (('support-note', True), ('_support-note_', True), ('(support-note).', True),
                               ('xsupport-note', False), ('support-note9', False),
                               ('ésupport-note', False), ('support-note中', False)):
            with self.subTest(text=text):
                search = self.record_citing_search('impact', text)
                result = self.export_supporting([self.search, search])
                self.assertEqual(bool(result['supporting_readings']), expected)
        search = self.record_citing_search('impact', 'No citation here.')
        altered = dict(search, id='support-note', queries=search['queries'] + ['support-note'])
        self.put('literature_search', altered['id'], altered)
        self.assertEqual(self.export_supporting([self.search, altered])['supporting_readings'], [])

    def test_explicit_selection_binds_unmentioned_reading_and_does_not_duplicate_decisive_reading(self):
        result = self.export_supporting([self.search], ['support-note', 'read-1'])
        native = json.loads(self.artifacts.read(result['native_packet']))
        minimum = native['scientific_context']['openness_minimum']
        self.assertEqual(minimum['supporting_reading_digests'], [digest(self.support)])
        self.assertEqual(result['supporting_readings'], [dict(reading_id='support-note',
            reading_digest=digest(self.support), cited_by=[])])
        rows = reading.list_record_rows(self.artifacts, native)
        self.assertEqual(next(row['role'] for row in rows if row['record_id'] == 'read-1'), 'decisive')

    def test_invalid_boundary_cannot_hide_an_overlapping_valid_citation(self):
        self.mutate(record_reading, self.abstract_note(self.support_version, note_id='a-a'))
        search = self.record_citing_search('impact', 'xa-a-a')
        result = self.export_supporting([self.search, search])
        self.assertEqual([row['reading_id'] for row in result['supporting_readings']], ['a-a'])
        self.assertEqual(result['supporting_readings'][0]['cited_by'][0]['fields'], ['impact'])

    def test_cited_nondecisive_full_reading_delivers_its_record_without_its_full_text(self):
        capture = self.capture(self.support_version, pdf_text='A synthetic background proof. ' * 30 + 'References: none.')
        bundle = self.bundle(self.support_version, capture=capture, bundle_id='background-bundle')
        self.mutate(import_bundle, bundle)
        self.mutate(record_reading, self.full_note(bundle, 'background-full'))
        search = self.record_citing_search(citation='Read in full (background-full).')
        result = self.export_supporting([self.search, search])
        native = json.loads(self.artifacts.read(result['native_packet']))
        self.assertEqual([row['reading_id'] for row in result['supporting_readings']], ['background-full'])
        self.assertNotIn(capture['text']['sha256'], {row['digest'] for row in native['evidence']})
        rows = reading.list_reading_rows(self.store.snapshot()['records'], self.artifacts, native)
        self.assertTrue(any(row.get('record_id') == 'background-full' and row['role'] == 'supporting' for row in rows))
        self.assertFalse(any(row.get('version_id') == self.support_version for row in rows))

    def test_multiple_bound_searches_and_fields_are_reported_once_from_frozen_evidence(self):
        first = self.record_citing_search('impact')
        second = self.record_citing_search('scope')
        second = dict(second, gaps=['Read support-note again.'])
        self.put('literature_search', second['id'], second)
        result = self.export_supporting([self.search, first, second, first], ['support-note'])
        citations = [dict(search_id=search['id'], search_digest=digest(search), fields=fields)
                     for search, fields in ((first, ['impact']), (second, ['scope', 'gaps']))]
        self.assertEqual(result['supporting_readings'][0]['cited_by'], citations)
        self.put('literature_search', first['id'], dict(first, impact='Changed after export.'))
        native = json.loads(self.artifacts.read(result['native_packet']))
        rows = reading.list_record_rows(self.artifacts, native)
        self.assertEqual(next(row['cited_by'] for row in rows if row['record_id'] == 'support-note'), citations)

    def test_explicit_selection_refuses_unknown_ids_and_malformed_lists_before_writing(self):
        for value in (['unknown-reading'], None, {}, 'support-note', False, [3], [''],
                      ['support-note', 'support-note']):
            spec = self.build_spec([self.search])
            spec['minimum']['supporting_reading_ids'] = value
            before = sorted(self.root.rglob('*'))
            with self.subTest(value=value), self.assertRaises(ResearchError) as caught:
                problem_openness.export_openness(self.store, spec)
            self.assertEqual(caught.exception.code, 'openness_reading_gap' if value == ['unknown-reading']
                             else 'invalid_input')
            self.assertEqual(sorted(self.root.rglob('*')), before)

    def test_stored_v1_search_reading_rows_and_prompts_keep_the_released_projection(self):
        from openness_legacy_fixtures import export_as_0_85_3, load_0_87_0_openness_reading
        search = self.record_citing_search()
        spec = self.build_spec([self.search, search])
        spec['minimum'].pop('supporting_reading_ids')
        before = self.store.snapshot()['records']
        record_bytes = {kind: problem_openness.serialize_record(before[kind])
                        for kind in ('reading', 'literature_search')}
        released = load_0_87_0_openness_reading()
        legacy = export_as_0_85_3(self.store, spec, self.root / 'legacy')
        native = json.loads(self.artifacts.read(legacy['native_packet']))
        packet_bytes = self.artifacts.read(legacy['native_packet'])
        self.assertEqual(reading.list_record_rows(self.artifacts, native),
                         released.list_record_rows(self.artifacts, native))
        self.assertFalse(any(row.get('record_id') == 'support-note'
                             for row in reading.list_record_rows(self.artifacts, native)))
        self.assertEqual(self.export_supporting([self.search, search])['supporting_readings'][0]['reading_id'],
                         'support-note')
        for protocol, prompts in released.PROMPTS.items():
            self.assertEqual(reading.PROMPTS[protocol], prompts)
        self.assertEqual(self.artifacts.read(legacy['native_packet']), packet_bytes)
        after = self.store.snapshot()['records']
        for kind, body in record_bytes.items():
            self.assertEqual(problem_openness.serialize_record(after[kind]), body)
