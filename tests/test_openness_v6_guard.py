"""UB10: execute every bindable material path and qualify stored findings."""

import copy
import json
from pathlib import Path
import unittest
from unittest.mock import patch

from ci.evidence_tests import require_evidence_tests
from openness_reading_fixtures import OpennessReadingCase
from research_harness import openness_reading as reading, problem_openness, record_check
from research_harness import review_protocol as protocol
from research_harness.errors import ResearchError
from research_harness.evidence import digest
from research_harness.literature import record_search
import test_research_problem_openness_minimum as minimum_fixtures


# G3 includes every delivery class of R27, including a named gap for an unreadable proof.
# Each value names an executed journey, not an assertion about a command's name.
MATERIAL_PATHS = (
    ('decisive_reading', ('test_openness_v6_guard.MaterialJourneyTests.test_decisive_reading_is_cited_bound_planned_and_required',)),
    ('family_reading', ('test_openness_v6_guard.MaterialJourneyTests.test_family_reading_is_cited_bound_planned_and_required',)),
    ('supporting_reading', (
        'test_openness_supporting_readings.SupportingReadingTests.test_g3_search_export_plan_and_verification_require_the_supporting_reading',
        'test_openness_supporting_readings.SupportingReadingTests.test_g3_row_disposition_citation_binds_plans_and_requires_complete_supporting_delivery',
        'test_openness_supporting_readings.SupportingReadingTests.test_each_of_the_six_search_text_fields_binds_and_reports_a_reading')),
    ('analytical_result', ('test_openness_result_binding.ResultBindingTests.test_g3_analytical_incident_delivers_cited_claim_and_proof',)),
    ('lean_result', ('test_openness_result_binding.ResultBindingTests.test_g3_lean_result_delivers_verification_and_text_proof',)),
    ('certificate_result', ('test_openness_result_binding.ResultBindingTests.test_g3_certificate_binary_is_described_with_notice',)),
    ('pdf_result', (
        'test_openness_result_binding.ResultBindingTests.test_g3_pdf_proof_uses_real_extraction_and_keeps_both_digests',
        'test_openness_result_binding.ResultBindingTests.test_g3_pdf_without_text_is_a_gap_and_can_record_unresolved',
        'test_openness_result_binding.ResultBindingTests.test_binary_nonproof_pdf_is_a_notice')),
    ('binary_result', ('test_openness_result_binding.ResultBindingTests.test_g3_binary_proof_is_a_gap',)),
    ('executable_or_toolchain', ('test_openness_result_binding.ResultBindingTests.test_executables_and_toolchains_are_described_even_when_text',)),
    ('supplied_reading', ('test_supplied_eligibility.SuppliedEligibilityTests.test_supplied_reading_journey_keeps_origin_in_chain_session_export_and_post',)),
)


class V6GuardTests(unittest.TestCase):
    def setUp(self):
        # Registered first, this cleanup runs after the cleanups of every case that a test sets up, so the
        # released order is restored last. A class decorator would restore it before those cleanups.
        protocols = patch.object(reading, 'NEW_PROBLEM_CHAIN_PROTOCOLS', (reading.PROTOCOL_V6,))
        protocols.start()
        self.addCleanup(protocols.stop)

    def run_material_paths(self, paths):
        self.assertEqual(len({kind for kind, _ in paths}), len(paths))
        self.assertTrue(all(identifiers for _, identifiers in paths), 'Every material kind needs an executed path')
        # Each path exists, loads and carries no skip or expected-failure mark. The run that executes it gives its
        # result: the verification of the run requires it to have run and passed (ci/evidence_tests.py).
        for kind, identifiers in paths:
            with self.subTest(kind=kind):
                self.assertEqual(require_evidence_tests(identifiers), {})

    def test_g3_executes_every_material_path(self):
        self.run_material_paths(MATERIAL_PATHS)

    def test_g3_adding_a_kind_without_a_path_fails(self):
        with self.assertRaises(AssertionError):
            self.run_material_paths((('new_material', ()),))

    def test_ub10_presence_requires_its_executed_evidence_in_the_matrix(self):
        from ci import capacity_guard
        matrix = capacity_guard.read_matrix()
        sources = capacity_guard.read_plugin_sources(Path(__file__).resolve().parents[1])
        obligation = next(row for row in matrix['merge_obligations'] if row['unit'] == 'UB10')
        self.assertEqual(capacity_guard.inspect_merge_obligations(matrix, sources), [])
        removed = obligation['per_entry']['A4']['tests'][0]
        entry = next(row for row in matrix['entries'] if row['id'] == 'A4')
        for tests in (entry['tests'], entry['evidence']['tests']):
            tests[:] = [identifier for identifier in tests if identifier != removed]
        self.assertIn(capacity_guard.MERGE_MESSAGE + ' UB10:A4',
                      capacity_guard.inspect_merge_obligations(matrix, sources))

    def test_problem_first_assignment_requires_a_finite_projection_and_math_v1_keeps_c5(self):
        # C5 explicitly admits math v1; AR18.3 applies to new problem openness chains.
        from test_openness_v6_chain import V6ChainTests
        case = V6ChainTests()
        self.addCleanup(case.doCleanups)
        case.setUp()
        reference = case.export()
        project = reading.project_reading_chain

        def project_without_bound(*args, **kwargs):
            result = project(*args, **kwargs)
            result.update(finite=False, unbounded_kinds=['unmatched_excerpt'])
            return result

        before = case.store.snapshot()
        with patch.object(reading, 'project_reading_chain', side_effect=project_without_bound), \
                patch.object(protocol.transport, 'send_request', side_effect=AssertionError('Admission invoked a reviewer')):
            with self.assertRaises(ResearchError) as caught:
                case.assign('nonfinite', reference)
        self.assertEqual(caught.exception.code, 'review_route_capacity_below_floor')
        refusals = caught.exception.details['admissions']
        self.assertEqual([row['protocol'] for row in refusals], [reading.PROTOCOL_V6])
        self.assertEqual(refusals[0]['code'], reading.PROJECTION_STOP)
        self.assertEqual(refusals[0]['details']['reason'], 'no_enforceable_bound')
        self.assertEqual(case.store.snapshot(), before)
        assigned = case.assign('turn-1', reference)
        self.assertTrue(assigned['reading_chain_admission']['projection']['finite'])
        identifiers = (
            'test_openness_v6_chain.V6ChainTests.test_export_selects_v6_and_keeps_math_on_v1',
            'test_reading_forecast.MathForecastTests.test_estimate_stops_before_call_and_owner_continue_reaches_fitting_final',
        )
        self.run_material_paths((('math_v1_c5', identifiers),))

    def test_every_admitted_v6_request_and_serialized_record_fit_the_first_projection(self):
        from test_openness_v6_chain import V6ChainTests
        for shape in ('short-excerpts-long-purposes', 'uncharged-diagnostics', 'passage-turns', 'long-identifiers'):
            with self.subTest(shape=shape):
                case = V6ChainTests()
                self.addCleanup(case.doCleanups)
                case.setUp()
                try:
                    assign = case.assign

                    def assign_and_measure(*args, **kwargs):
                        assigned = assign(*args, **kwargs)
                        packet = case.read_packet(assigned['id'])
                        retained = reading._find_record(packet)
                        # Codex's native input wraps the JSON packet in a second JSON string.
                        # Count its serialized characters independently of the retention helper.
                        def count_entries(entries):
                            def serialize(items):
                                body = protocol.transport.PACKET_LABEL + json.dumps(
                                    dict(entries=items), sort_keys=True, ensure_ascii=False)
                                return json.dumps(dict(review_messages=[dict(role='user', content=body)]),
                                                  sort_keys=True, ensure_ascii=False)
                            return len(serialize(entries)) - len(serialize([]))
                        extra = [row for row in retained['turns'] if row.get('stage') == 'final']
                        used = sum(count_entries(retained[key]) for key in ('excerpts', 'passages'))
                        used += count_entries(extra) + (2 if extra and len(retained['turns']) > len(extra) else 0)
                        self.assertEqual(retained['allowances']['record']['used'], used)
                        return assigned

                    case.assign = assign_and_measure
                    record, projection, requests, _ = case.drive_stub(shape)
                    self.assertTrue(projection['finite'])
                    self.assertTrue(requests)
                    self.assertLessEqual(max(requests), projection['characters'])
                    self.assertLessEqual(record['allowances']['record']['used'], projection['record_total'])
                finally:
                    case.doCleanups()


class MaterialJourneyTests(OpennessReadingCase):
    reading_protocol = None
    route_model = 'gpt-6-astra'
    text_sizes = (121000,)

    def record_searches(self):
        return [minimum_fixtures.ProblemOpennessMinimumTests.search(self)]

    def run_reading_material(self, family=False):
        self.limit_input(1300000)
        version = self.versions[0]
        records = self.store.snapshot()['records']
        note = records['reading']['read-1']
        found = version.rsplit('v', 1)[0] if family else version
        payload = self.capture_search(found=[found])
        payload.update(id='citing-reading', question=self.selected['statement'], scope=self.selected['statement'], resolved=[])
        payload['dispositions'][0]['reason'] = 'The complete reading (%s) assesses this source.' % note['id']
        self.mutate(record_search, payload)
        search = self.store.snapshot()['records']['literature_search'][payload['id']]
        self.searches.append(search)
        reference = self.export()
        native = json.loads(self.artifacts.read(reference))
        minimum = native['scientific_context']['openness_minimum']
        self.assertEqual(minimum['reading_digests'], [digest(note)])
        if family:
            self.assertEqual(minimum['family_readings'], [dict(search_digest=digest(search), work_id=found, version_id=version)])
        planned = reading.list_reading_rows(self.store.snapshot()['records'], self.artifacts, native)
        self.assertTrue(any(row.get('record_id') == note['id'] and row.get('role') == 'decisive' for row in planned))
        turns = self.run_reading_turns(reference, turn_count=8)
        deliveries = [row for identifier in turns[:-1]
                      for row in self.read_packet(identifier)['reading_turn']['reading_plan']['delivered_now']]
        self.invoke(turns[-1], self.build_native_result(self.read_packet(turns[-1])))
        records = self.store.snapshot()['records']
        assignment = records['review_assignment'][turns[-1]]
        verified = reading.verify_chain(records, self.artifacts, assignment)
        self.assertTrue(verified)
        # Both a reading record and the text it pins must have authenticated delivery credit.
        identities = [dict(kind='record', record_kind='reading', record_id=note['id']), self.unit_identity(version)]
        observe = reading._observe_turns
        for identity in identities:
            key = reading.review_excerpts.find_identity(identity)

            def omit_material(*args, **kwargs):
                observations = copy.deepcopy(observe(*args, **kwargs))
                for observation in observations:
                    observation['whole'].discard(key)
                    observation['parts'] = {part for part in observation['parts'] if part[0] != key}
                return observations

            with patch.object(reading, '_observe_turns', side_effect=omit_material):
                with self.assertRaises(ResearchError) as caught:
                    reading.verify_chain(records, self.artifacts, assignment)
            self.assertEqual(caught.exception.code, 'openness_reading_incomplete')
        # R17: the found work is decisive. R18: each delivered text of the read version names, in family_of,
        # the sorted found records that it stands for.
        if family:
            with self.subTest(requirement='R17'):
                self.assertIn(found, native['scientific_context']['decisive_work_ids'])
            with self.subTest(requirement='R18'):
                texts = [row for row in deliveries if row['kind'] == 'fulltext' and row['version_id'] == version]
                self.assertTrue(texts)
                self.assertTrue(all(row.get('family_of') == [found] for row in texts))

    def test_decisive_reading_is_cited_bound_planned_and_required(self):
        self.run_reading_material()

    def test_family_reading_is_cited_bound_planned_and_required(self):
        self.run_reading_material(family=True)


class FindingQualificationTests(OpennessReadingCase):
    reading_protocol = None
    route_model = 'gpt-6-astra'
    text_sizes = (121000,)

    def record_finding(self, legacy=False):
        self.historical_v5 = legacy
        self.limit_input(1300000)
        reference = self.export()
        turns = self.run_reading_turns(reference, turn_count=8)
        self.invoke(turns[-1], self.build_native_result(self.read_packet(turns[-1])))
        return self.mutate(self.load_problem_module().record_openness, dict(assignment_id=turns[-1]))['result']

    def test_check_one_reports_every_finding_including_an_older_stale_one(self):
        finding = self.record_finding()
        records = self.store.snapshot()['records']
        earlier = copy.deepcopy(finding)
        earlier.update(id='earlier', statement_digest='0' * 64, recorded_revision=0)
        records['problem_openness']['earlier'] = earlier
        before = copy.deepcopy(records)
        assess = problem_openness.assess_openness
        def assess_with_chronology(selected, artifacts):
            self.assertIs(type(selected), type(records))
            self.assertEqual(selected.read_creation_revision('problem_statement', self.selected['id']),
                             records.read_creation_revision('problem_statement', self.selected['id']))
            return assess(selected, artifacts)
        with patch.object(problem_openness, 'assess_openness', side_effect=assess_with_chronology):
            findings = record_check.check_openness_findings(records, self.artifacts)
        self.assertEqual(set(findings), {finding['id'], 'earlier'})
        self.assertEqual(findings[finding['id']], dict(minimum_version=2, obligations=[]))
        self.assertEqual(findings['earlier']['obligations'][0]['code'], 'problem_openness_stale')
        self.assertEqual(records, before)
        self.assertEqual(record_check.check_authentication(self.store)['openness_findings'][finding['id']],
                         findings[finding['id']])

    def test_check_one_validates_even_a_stale_or_unresolved_minimum_under_its_version(self):
        finding = self.record_finding()
        for version in (1, 2):
            for outcome in ('supported_open', 'unresolved'):
                with self.subTest(version=version, outcome=outcome):
                    malformed = copy.deepcopy(finding)
                    malformed.update(statement_digest='0' * 64, outcome=outcome)
                    malformed['minimum']['schema_version'] = version
                    if version == 1:
                        for key in ('family_readings', 'supporting_reading_digests', 'results'):
                            malformed['minimum'].pop(key)
                    malformed['minimum']['reading_digests'] = ['not-a-digest']
                    records = self.store.snapshot()['records']
                    records['problem_openness'] = {finding['id']: malformed}
                    with self.assertRaises(ResearchError) as caught:
                        record_check.check_openness_findings(records, self.artifacts)
                    self.assertEqual(caught.exception.code, 'invalid_record')

    def test_check_one_keeps_legacy_v1_finding_and_every_stored_byte(self):
        finding = self.record_finding(legacy=True)
        records = self.store.snapshot()['records']
        self.assertEqual(finding['minimum']['schema_version'], 1)
        before = copy.deepcopy(records)
        references = protocol.collect_references(records)
        retained = [(reference, self.artifacts.read(reference)) for reference in references]
        qualified = record_check.check_openness_findings(records, self.artifacts)
        self.assertEqual(qualified[finding['id']], dict(minimum_version=1, obligations=[]))
        self.assertEqual(records, before)
        for reference, body in retained:
            self.assertEqual(self.artifacts.read(reference), body)

    def test_check_one_keeps_a_legacy_finding_without_a_minimum(self):
        finding = self.record_finding(legacy=True)
        records = self.store.snapshot()['records']
        records['problem_openness'][finding['id']].pop('minimum')
        before = copy.deepcopy(records)
        qualified = record_check.check_openness_findings(records, self.artifacts)
        self.assertIsNone(qualified[finding['id']]['minimum_version'])
        self.assertEqual(qualified[finding['id']]['obligations'], problem_openness.assess_openness(records, self.artifacts))
        self.assertEqual(records, before)
