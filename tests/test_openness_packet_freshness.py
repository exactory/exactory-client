"""G12: current records can retire a forecast without refusing a reviewer's answer."""

import copy
import json
from pathlib import Path
from collections.abc import Mapping
from unittest.mock import patch

from openness_reading_fixtures import OpennessReadingCase
from research_harness import openness_minimum, openness_reading, problem_openness
from research_harness import review_protocol
from research_harness.evidence import digest
from research_harness.review_continuation import plan_next
from test_openness_finding_answer import build_answer
import test_research_problem_openness_minimum as minimum_fixtures


class FactRecords(Mapping):
    """Any read or enumeration outside the R10 facts fails, including dict(mapping)."""
    allowed = frozenset(('problem_selection', 'problem_statement', 'literature_search', 'reading',
                         'work', 'alias', 'work_family', 'source', 'source_bundle', 'bundle_selection',
                         'main_capture_selection', 'visual_asset', 'visual_asset_selection',
                         'source_deferral', 'source_deferral_selection',
                         # Immutable provenance authenticating linked inspection bytes (R10 row 6).
                         'source_assembly', 'fulltext_unit', 'work_assertion'))

    def __init__(self, records):
        self.records = records

    def __getitem__(self, key):
        if key not in self.allowed:
            raise AssertionError('Read outside R10: ' + key)
        return self.records[key]

    def __iter__(self):
        raise AssertionError('Enumerated records outside R10')

    def __len__(self):
        raise AssertionError('Counted records outside R10')


class PacketFreshnessTests(OpennessReadingCase):
    reading_protocol = None

    def build_native_packet(self):
        return json.loads(self.artifacts.read(self.export()))

    def assess_packet(self, packet, records=None):
        return problem_openness.assess_packet_freshness(
            FactRecords(self.store.snapshot()['records'] if records is None else records), self.artifacts, packet)

    def assert_changed(self, packet, records, row):
        result = self.assess_packet(packet, records)
        self.assertFalse(result['current'], result)
        self.assertIn(row, [entry['row'] for entry in result['added'] + result['removed']])
        if row in ('decisive_captures', 'required_bundle', 'inspection_bytes', 'source_deferrals'):
            gaps = openness_minimum.list_minimum_gaps(packet['scientific_context']['openness_minimum'],
                self.selected['statement'], FactRecords(records), self.artifacts)
            self.assertNotEqual(openness_minimum.forecast_outcomes(packet['scientific_context']['minimum_gaps']),
                                openness_minimum.forecast_outcomes(gaps))
        return result

    def test_g12_canonical_comparison_and_immutable_bound_records(self):
        packet = self.build_native_packet()
        before = copy.deepcopy(packet)
        records = self.store.snapshot()['records']
        self.assertTrue(self.assess_packet(packet, records)['current'])
        changed = copy.deepcopy(packet['scientific_context']['minimum_gaps'][0])
        changed['details']['synthetic_gap'] = 'Previously reported barrier.'
        packet['scientific_context']['minimum_gaps'].append(changed)
        result = self.assert_changed(packet, records, 'bound_records')
        self.assertEqual(result['removed'][0]['entry'], changed)
        self.assertEqual(result['added'], [])
        packet = before
        gaps = packet['scientific_context']['minimum_gaps']
        with patch.object(openness_minimum, 'list_minimum_gaps', return_value=list(reversed(gaps))):
            self.assertTrue(self.assess_packet(packet, records)['current'])
        self.assertEqual(packet, before)

    def test_g12_statement_and_neighbours_are_binding_facts(self):
        packet = self.build_native_packet()
        for field, value in (('statement', 'A changed exact statement.'),
                             ('neighbours', ['A newly declared neighbouring formulation.']),
                             ('id', 'another-statement-with-the-same-claim')):
            records = self.store.snapshot()['records']
            records['problem_statement'][self.selected['id']][field] = value
            result = self.assert_changed(packet, records, 'current_statement')
            self.assertEqual(result['added'][0]['entry']['code'], 'openness_statement_mismatch')

    def test_g12_removed_selected_bundle_is_reported_in_its_r10_row(self):
        packet = self.build_native_packet()
        records = self.store.snapshot()['records']
        records['source_bundle'].pop('bundle-1')
        self.assert_changed(packet, records, 'required_bundle')

    def test_g12_component_checks_keep_unpinned_parent_readings_out_of_v1_audit(self):
        import test_research_components as components
        from research_harness.literature import import_bundle
        from research_harness.reading import record_reading
        from research_harness.errors import ResearchError
        case = components.ComponentTests()
        self.addCleanup(case.doCleanups)
        case.setUp()
        capture = case.acquire()['capture']
        bundle = case.filled_bundle(capture)
        case.mutate(import_bundle, bundle)
        note = case.full_note(bundle, 'component-only')
        note['inspections'] = [row for row in note['inspections'] if row['link']['source_id'] == capture['source_id']]
        for value in note['notes'].values():
            value['inspections'] = [0]
        case.mutate(record_reading, note)
        records = case.store.snapshot()['records']
        search = copy.deepcopy(self.search)
        search['dispositions'] = [dict(search['dispositions'][0], work_id=case.identifier)]
        records['literature_search'] = {search['id']: search}
        pinned = records['reading']['component-only']
        minimum = dict(schema_version=1, statement=self.selected['statement'], neighbours=[],
            searches=[dict(formulation=self.selected['statement'], search_digest=digest(search))],
            reading_digests=[digest(pinned)], settling_work_id=None, duplicate_resolutions=[])
        finding = dict(minimum=minimum, outcome='supported_open', searched_at='2026-10-17',
                       evidence=[digest(search), digest(pinned)])
        legacy = {'__name__': 'research_harness.legacy_minimum', '__package__': 'research_harness'}
        source = Path(__file__).with_name('fixtures') / 'openness_minimum_0_85_3.py.txt'
        exec(compile(source.read_text(), str(source), 'exec'), legacy)
        errors = []
        for audit in (legacy['audit_minimum'], openness_minimum.audit_minimum):
            with self.assertRaises(ResearchError) as caught:
                audit(finding, self.selected['statement'], records, case.artifacts, set(finding['evidence']))
            errors.append(caught.exception.as_dict())
        self.assertEqual(errors[0]['code'], 'component_evidence_uninspected')
        self.assertEqual(errors[0], errors[1])

    def test_g12_new_material_search_changes_the_binding_forecast(self):
        packet = self.build_native_packet()
        search = minimum_fixtures.ProblemOpennessMinimumTests.search(self, self.versions, query='A later search')
        result = self.assert_changed(packet, self.store.snapshot()['records'], 'material_searches')
        self.assertIn(search['id'], result['added'][0]['entry']['details']['search_ids'])

    def test_g12_bound_records_are_read_by_their_immutable_digests(self):
        packet = self.build_native_packet()
        for kind in ('literature_search', 'reading'):
            records = self.store.snapshot()['records']
            records[kind].pop(next(iter(records[kind])))
            with self.subTest(kind=kind):
                self.assert_changed(packet, records, 'bound_records')

    def test_g12_decisive_capture_availability_main_body_and_original(self):
        packet = self.build_native_packet()
        original = self.store.snapshot()['records']
        for change in ('availability', 'component', 'original'):
            records = copy.deepcopy(original)
            capture = records['work'][self.versions[0]]['fulltexts'][0]
            if change == 'availability':
                capture['availability'] = 'unavailable'
            elif change == 'component':
                capture['component'] = {'kind': 'supplement'}
            else:
                capture['original'] = self.artifacts.put(b'A distinct main original.', 'application/pdf')
            with self.subTest(change=change):
                self.assert_changed(packet, records, 'decisive_captures')

    def test_g12_current_required_bundle_changes_the_reading_forecast(self):
        packet = self.build_native_packet()
        records = self.store.snapshot()['records']
        bundle = copy.deepcopy(records['source_bundle']['bundle-1'])
        bundle.update(id='new-bundle', sequence=bundle['sequence'] + 1)
        bundle['units'].append(dict(id='new-unit', kind='text', required=True, link=None,
                                    reason='A new required argument.'))
        records['source_bundle'][bundle['id']] = bundle
        records['bundle_selection'][self.versions[0]] = {'bundle_id': bundle['id']}
        self.assert_changed(packet, records, 'required_bundle')

    def test_g12_pinned_inspection_bytes_change_the_reading_forecast(self):
        packet = self.build_native_packet()
        reference = self.texts[self.versions[0]]
        (self.root / reference['path']).chmod(0o600)
        (self.root / reference['path']).write_bytes(b'Damaged inspected bytes.')
        result = self.assert_changed(packet, self.store.snapshot()['records'], 'inspection_bytes')
        self.assertIn('artifact', result['added'][0]['entry']['details']['assessment_error']['code'])

    def test_g12_range_source_authenticates_inspection_bytes_with_only_r10_reads(self):
        import test_capture_classes as captures
        from literature_fixtures import LiteratureCase
        case, version, capture = captures.CaptureClassTests.create_capture_case(self, 'http_range_v1')
        bundle = captures.CaptureClassTests.build_bundle(self, case, version, capture)
        # A stored-shaped reading isolates authentication from today's stricter
        # article-bundle import admission, which this lane does not change.
        bundle.update(original_sha256=capture['original']['sha256'], sequence=1)
        note = LiteratureCase.full_note(case, bundle, 'range-reading')
        records = case.store.snapshot()['records']
        records['source_bundle'] = {bundle['id']: bundle}
        records['bundle_selection'] = {version: dict(bundle_id=bundle['id'])}
        records['reading'] = {note['id']: note}
        search = copy.deepcopy(self.search)
        search['dispositions'] = [dict(search['dispositions'][0], work_id=version)]
        records['literature_search'] = {search['id']: search}
        records['problem_selection'] = {'current': {'statement_id': self.selected['id']}}
        records['problem_statement'] = {self.selected['id']: self.selected}
        minimum = dict(schema_version=2, statement=self.selected['statement'], neighbours=[],
            searches=[dict(formulation=self.selected['statement'], search_digest=digest(search))],
            reading_digests=[digest(records['reading']['range-reading'])], settling_work_id=None,
            duplicate_resolutions=[], family_readings=[], supporting_reading_digests=[], results=[])
        gaps = openness_minimum.list_minimum_gaps(minimum, self.selected['statement'], FactRecords(records), case.artifacts)
        packet = dict(subject=self.selected, claim_digest=problem_openness.compute_statement_digest(self.selected),
            evidence=[dict(digest=capture['original']['sha256'])],
            scientific_context=dict(openness_minimum=minimum, minimum_gaps=gaps))
        self.assertTrue(problem_openness.assess_packet_freshness(FactRecords(records), case.artifacts, packet)['current'])
        source = records['source'][capture['source_id']]
        records['source_assembly'][source['assembly_id']]['original']['sha256'] = '0' * 64
        result = problem_openness.assess_packet_freshness(FactRecords(records), case.artifacts, packet)
        self.assertFalse(result['current'])
        self.assertEqual({row['row'] for row in result['added']}, {'inspection_bytes'})

    def test_g12_only_decisive_source_deferrals_are_assessed(self):
        packet = self.build_native_packet()
        records = self.store.snapshot()['records']
        evidence = self.artifacts.put(b'Synthetic source gap authorization and acquisition log.', 'text/plain')
        deferred = dict(id='deferred', version_id=self.versions[0], profile='research', sequence=1,
                        authorization=evidence, acquisition_evidence=[evidence], dependency_digest='a' * 64)
        records.setdefault('source_deferral', {})['deferred'] = deferred
        records.setdefault('source_deferral_selection', {})[self.versions[0]] = {'deferral_id': 'deferred'}
        self.assert_changed(packet, records, 'source_deferrals')
        packet['scientific_context']['minimum_gaps'] = openness_minimum.list_minimum_gaps(
            packet['scientific_context']['openness_minimum'], self.selected['statement'], FactRecords(records), self.artifacts)
        self.assertTrue(self.assess_packet(packet, records)['current'])
        records['source_deferral_selection'][self.versions[0]]['resume_id'] = 'resumed'
        self.assert_changed(packet, records, 'source_deferrals')
        records = self.store.snapshot()['records']
        records.setdefault('source_deferral', {})['unrelated'] = dict(deferred, id='unrelated', version_id='arxiv:2601.99999v1')
        self.assertTrue(self.assess_packet(self.build_native_packet(), records)['current'])

    def test_g12_v5_packet_bypasses_record_reads_and_keeps_its_bytes(self):
        self.historical_v5 = True
        packet = self.build_native_packet()
        self.assertNotIn('minimum_gaps', packet['scientific_context'])
        before = problem_openness.serialize_record(packet)
        self.assertEqual(self.assess_packet(packet, {}), {'current': True, 'added': [], 'removed': []})
        self.assertEqual(problem_openness.serialize_record(packet), before)

    def assert_handoff(self, plan):
        self.assertEqual((plan['state'], plan['command'], plan['code']),
                         ('handoff', 'export', 'openness_packet_outdated'), plan)
        self.assertEqual(plan['details']['message'], problem_openness.OUTDATED_PACKET_MESSAGE)

    def test_g12_resolved_final_is_exported_without_an_answer_refusal(self):
        native = self.export()
        self.assign('turn-1', native)
        packet = self.read_packet('turn-1')
        self.invoke('turn-1', build_answer(packet))
        search = minimum_fixtures.ProblemOpennessMinimumTests.search(self, self.versions, query='A search after the answer')
        records = self.store.snapshot()['records']
        plan = plan_next(records, self.artifacts, 'turn-1')
        self.assert_handoff(plan)
        self.assertIn(search['id'], json.dumps(plan['details']))
        self.assertFalse(records.get('review_output_refusal'))
        self.assertTrue(review_protocol.assignment_state(records, self.artifacts, 'turn-1')['ready'])

    def test_g12_unresolved_answer_is_recordable_over_changed_records(self):
        native = self.export()
        self.assign('turn-1', native)
        answer = build_answer(self.read_packet('turn-1'), 'unresolved',
                              [dict(kind='reviewer_gap', text='The reviewer identifies an additional gap.')])
        self.invoke('turn-1', answer)
        minimum_fixtures.ProblemOpennessMinimumTests.search(self, self.versions, query='A search after the answer')
        records = self.store.snapshot()['records']
        self.assertEqual(plan_next(records, self.artifacts, 'turn-1')['state'], 'record')

    def test_g12_invalid_answer_keeps_its_contract_refusal_over_changed_records(self):
        native = self.export()
        self.assign('turn-1', native)
        minimum_fixtures.ProblemOpennessMinimumTests.search(self, self.versions, query='A search before invalid output')
        answer = build_answer(self.read_packet('turn-1'), 'already_solved')
        self.invoke('turn-1', answer)
        records = self.store.snapshot()['records']
        self.assertEqual(records['review_output_refusal']['attempt-turn-1']['code'], 'openness_finding_invalid')
        self.assertEqual(plan_next(records, self.artifacts, 'turn-1')['state'], 'run')


class ChainFreshnessTests(OpennessReadingCase):
    reading_protocol = None
    route_model = "gpt-6-astra"
    text_sizes = (80000, 80000, 80000)
    assert_handoff = PacketFreshnessTests.assert_handoff

    def test_g12_failed_turn_is_exported_before_retrying_an_outdated_packet(self):
        self.limit_input(1300000)
        native = self.export()
        self.assign('turn-1', native)
        self.mutate(review_protocol.record_attempt, dict(id='failed', assignment_id='turn-1', status='failed',
            output=None, usage=None, reason='A synthetic provider failure.'))
        minimum_fixtures.ProblemOpennessMinimumTests.search(self, self.versions, query='A search before retry')
        records = self.store.snapshot()['records']
        self.assert_handoff(plan_next(records, self.artifacts, 'turn-1'))

    def test_g12_stale_final_chain_keeps_the_answer_and_reading_turns_verified(self):
        self.limit_input(1300000)
        native = self.export()
        identifiers = self.run_reading_turns(native, turn_count=12)
        final = identifiers[-1]
        packet = self.read_packet(final)
        minimum_fixtures.ProblemOpennessMinimumTests.search(self, self.versions, query='A search before final observation')
        self.invoke(final, build_answer(packet))
        records = self.store.snapshot()['records']
        self.assert_handoff(plan_next(records, self.artifacts, final))
        self.assertFalse(records.get('review_output_refusal'))
        self.assertTrue(review_protocol.assignment_state(records, self.artifacts, final)['ready'])
        verified = openness_reading.verify_chain(records, self.artifacts, records['review_assignment'][final])
        self.assertEqual([row['assignment_id'] for row in verified], identifiers)

    def test_g12_stored_v5_chain_continues_after_a_new_search_with_original_packets(self):
        self.historical_v5 = True
        self.limit_input(1300000)
        native = self.export()
        assignment = self.assign('turn-1', native)
        packet_bytes = self.artifacts.read(assignment['packet'])
        packet = self.read_packet('turn-1')
        self.invoke('turn-1', self.build_reading_response(packet))
        before = copy.deepcopy(self.store.snapshot()['records']['review_assignment']['turn-1'])
        minimum_fixtures.ProblemOpennessMinimumTests.search(self, self.versions, query='Later search for a v5 chain')
        records = self.store.snapshot()['records']
        plan = plan_next(records, self.artifacts, 'turn-1')
        self.assertEqual(plan['state'], 'continue', plan)
        self.assertEqual(self.artifacts.read(assignment['packet']), packet_bytes)
        self.assertEqual(records['review_assignment']['turn-1'], before)
        next_assignment = self.assign('turn-2', native, 'turn-1')
        self.assertTrue(next_assignment['prompt']['template_id'].startswith(openness_reading.PROTOCOL_V5 + '/'))

    def test_g12_incident_new_search_after_two_turns_exports_and_keeps_verified_readings(self):
        # Source: 20261112135015-openness-reading-turn-stopped-by-provider-safeguards T5.
        self.limit_input(1300000)
        native = self.export()
        self.assign('turn-1', native)
        self.invoke('turn-1', self.build_reading_response(self.read_packet('turn-1')))
        self.assign('turn-2', native, 'turn-1')
        self.invoke('turn-2', self.build_reading_response(self.read_packet('turn-2')))
        before = self.store.snapshot()['records']
        planned = plan_next(before, self.artifacts, 'turn-2')
        self.assertEqual(planned['state'], 'continue')
        new_search = minimum_fixtures.ProblemOpennessMinimumTests.search(self, self.versions, query='A new search after turn two')
        records = self.store.snapshot()['records']
        plan = plan_next(records, self.artifacts, 'turn-2')
        self.assert_handoff(plan)
        self.assertIn(new_search['id'], json.dumps(plan['details']))
        turns = openness_reading.verify_problem_coverage(
            records, self.artifacts, records['review_assignment']['turn-2'])['turns']
        self.assertEqual([row['assignment_id'] for row in turns], ['turn-1', 'turn-2'])
        for kind in ('review_assignment', 'review_attempt', 'review_output_refusal'):
            self.assertEqual(records.get(kind), before.get(kind))



class FamilyFreshnessTests(OpennessReadingCase):
    reading_protocol = None

    def test_g12_family_authors_and_third_family_alias_change_currency(self):
        import test_research_work_families as families
        # Link a registry record to the already read exact version by a shared DOI.
        found = families.WorkFamilyTests.import_work(self, 'openalex:W123',
            aliases=['doi:10.1234/shared'], authors=['A. Author', 'B. Author'])
        third = families.WorkFamilyTests.import_work(self, 'openalex:W999')
        records = self.store.snapshot()['records']
        version = self.versions[0]
        records['work'][version]['aliases'].append('doi:10.1234/shared')
        records['work'][version]['authors'] = ['A. Author', 'B. Author']
        records.setdefault('alias', {})['doi:10.1234/shared'] = {
            'assertions': [{'work_id': found}, {'work_id': 'arxiv:2601.00001'}]}
        search = copy.deepcopy(self.search)
        search['dispositions'][0]['work_id'] = found
        records['literature_search'][search['id']] = search
        minimum = problem_openness.build_minimum(self.selected, [(self.selected['statement'], search)], {},
            {'settling_work_id': None, 'duplicate_resolutions': []}, records=records, artifacts=self.artifacts)
        self.assertEqual(minimum['family_readings'][0]['work_id'], found)
        gaps = openness_minimum.list_minimum_gaps(minimum, self.selected['statement'], FactRecords(records), self.artifacts)
        packet = {'subject': self.selected, 'claim_digest': problem_openness.compute_statement_digest(self.selected),
                  'scientific_context': {'openness_minimum': minimum, 'minimum_gaps': gaps}}
        assess = lambda snapshot: problem_openness.assess_packet_freshness(FactRecords(snapshot), self.artifacts, packet)
        self.assertTrue(assess(records)['current'])
        for change in ('authors', 'third_family'):
            changed = copy.deepcopy(records)
            if change == 'authors':
                changed['work'][found]['authors'] = ['A different author']
            else:
                changed['work'][third]['aliases'].append('doi:10.1234/shared')
                changed['alias']['doi:10.1234/shared']['assertions'].append({'work_id': third})
            with self.subTest(change=change):
                result = assess(changed)
                self.assertFalse(result['current'])
                self.assertEqual({row['row'] for row in result['added'] + result['removed']}, {'family_reading'})
                current = openness_minimum.list_minimum_gaps(minimum, self.selected['statement'], FactRecords(changed), self.artifacts)
                self.assertNotEqual(openness_minimum.forecast_outcomes(gaps), openness_minimum.forecast_outcomes(current))

    def test_g12_canonical_lists_preserve_multiplicity_and_ignore_entry_order(self):
        packet = json.loads(self.artifacts.read(self.export()))
        original = packet['scientific_context']['minimum_gaps'][0]
        other = dict(original, condition='settling_not_decisive')
        packet['scientific_context']['minimum_gaps'] = [original, other]
        records = FactRecords(self.store.snapshot()['records'])
        with patch.object(openness_minimum, 'list_minimum_gaps', return_value=[other, original]):
            self.assertTrue(problem_openness.assess_packet_freshness(records, self.artifacts, packet)['current'])
        with patch.object(openness_minimum, 'list_minimum_gaps', return_value=[original, other, original]):
            result = problem_openness.assess_packet_freshness(records, self.artifacts, packet)
            self.assertFalse(result['current'])
            self.assertEqual([row['entry'] for row in result['added']], [original])
