"""T5: deliver exploratory probe provenance without changing stored reviews."""

import copy
import json
import lzma
from pathlib import Path
import unittest

from research_harness import cli, problem_claims, problem_evidence, review_parts, review_protocol
from research_harness import review_prompt_paragraphs as paragraphs
from research_harness.evidence import digest
from research_harness.review_refusal import validate_evidence_contract
from research_harness.review_supplied_evidence import undelivered_references
from probe_delivery_fixtures import (assert_account_binding_delivery, build_probe_check,
                                     build_route_records, identify_account_record)
import test_problem_accounts as accounts_fixtures
import test_problem_accounts_tests as credit_fixtures
import test_problem_forming_import as import_fixtures
from test_research_probe_lifecycle import build_probe_intent, build_probe_outcome
from test_research_problem_forming import build_outcome
from test_review_prompt_paragraphs import build_assignment, render_packet


PROBE_PARAGRAPH = 'exactory-problem-account-probes-v1'
BASE_FIXTURE = Path(__file__).parent / 'fixtures' / 'problem_account_probes_base.json.xz'


class ProbeDeliveryTests(unittest.TestCase):
    def setUp(self):
        self.case = credit_fixtures.AccountTestCreditTests()
        self.case.setUp()
        self.addCleanup(self.case.doCleanups)

    def describe(self, records):
        binding = problem_evidence.describe_binding(records, self.case.artifacts)
        delivered = problem_evidence.describe_evidence(records, self.case.artifacts)
        assert_account_binding_delivery(self, binding, delivered)
        return binding, delivered

    def test_eight_independent_routes_deliver_both_records_once_with_original_digests(self):
        # Source: 20261114165500-math-conjecturer-phenomenon-run-observations, observation 6 / T5.
        records = build_route_records(self.case)
        before = copy.deepcopy(records)
        binding, delivered = self.describe(records)
        held = {identify_account_record(entry): entry for entry in delivered['account_records']}
        for route in ('check', 'call', 'place', 'input', 'ground', 'outcome', 'refuted', 'earlier'):
            for kind in ('probe', 'probe_intent'):
                identifier = 'probe-' + route
                self.assertEqual(held.get((None, kind, identifier)), dict(record=records[kind][identifier],
                    cite=dict(kind='record', record_kind=kind, id=identifier, digest=digest(records[kind][identifier]))))
        identities = [identify_account_record(entry)[1:] for entry in delivered['account_records']]
        self.assertEqual(identities, sorted(identities))
        self.assertEqual(delivered['value']['accounts_version'], 2)
        self.assertIn('probe_intent', problem_evidence.collect_binding_record_kinds(records))
        self.assertEqual(records, before)
        # Reaching a probe again through another route adds no duplicate unit.
        records['forming_call']['forming-1']['probes'].append(
            dict(probe_id='probe-check', form_id='form-1', role='observation'))
        call = records['forming_call']['forming-1']
        records['problem_candidate']['candidate-history']['source']['evidence'][0] = (
            problem_evidence.cite_record('forming_call', call['id'], call))
        _, repeated = self.describe(records)
        self.assertEqual([identify_account_record(entry) for entry in repeated['account_records']], list(held))

    def test_result_packet_lists_probe_pairs_as_units_and_accepts_their_citations(self):
        records = build_route_records(self.case)
        _, delivered = self.describe(records)
        packet = dict(role='result', subject=dict(kind='problem'), problem_evidence=delivered, evidence=[])
        units = review_parts.enumerate_units(packet, 'problem_result')
        unit_ids = {(unit['record_identity']['record_kind'], unit['record_identity']['id'])
                    for unit in units if 'record_identity' in unit}
        for kind in ('probe', 'probe_intent'):
            self.assertIn((kind, 'probe-check'), unit_ids)
            cite = next(row['cite'] for row in delivered['account_records']
                        if row['cite']['record_kind'] == kind and row['cite']['id'] == 'probe-check')
            output = dict(value=dict(evidence=[cite]))
            validate_evidence_contract(records, self.case.artifacts, output['value']['evidence'],
                                       packets=[packet], review_binding=True)
            carried = review_protocol.collect_carried_records(records, self.case.artifacts, None, packets=[packet])
            self.assertIsNotNone(review_protocol.find_carried_record(records, carried, cite))
            self.assertEqual(undelivered_references(records, self.case.artifacts, output['value']['evidence'],
                                                   carried, {}, [], frozen=True), [])

    def test_old_probe_without_intent_is_delivered_and_sealed_outputs_are_projected(self):
        records = build_route_records(self.case)
        records['probe']['probe-check']['question'] = 'An older self-contained question?'
        del records['probe_intent']['probe-check']
        sealed = self.case.artifacts.put_sealed(b'Unread finite output.\n', 'text/plain')
        records['probe']['probe-ground']['stdout'] = sealed
        _, delivered = self.describe(records)
        rows = {identify_account_record(row): row for row in delivered['account_records']}
        self.assertIn((None, 'probe', 'probe-check'), rows)
        self.assertNotIn((None, 'probe_intent', 'probe-check'), rows)
        projected = rows[(None, 'probe', 'probe-ground')]
        self.assertNotIn('path', projected['record']['stdout'])
        self.assertEqual(projected['record']['stdout']['sha256'], sealed['sha256'])
        self.assertEqual(projected['cite']['digest'], digest(records['probe']['probe-ground']))
        self.assertNotEqual(digest(projected['record']), projected['cite']['digest'])

    def test_paragraph_requires_version_two_native_probe_and_the_accounts_item(self):
        records = build_route_records(self.case)
        _, delivered = self.describe(records)
        for role, item, expected in (('result', None, True), ('adjudicator', 'A3', True),
                ('adjudicator', 'priority', True), ('adjudicator', 'calibration', True),
                ('adjudicator', 'significance', True), ('adjudicator', 'A7', False),
                ('adjudicator', 'B1', False), ('slate', None, False)):
            with self.subTest(role=role, item=item):
                packet = dict(role=role, subject=dict(kind='problem'), problem_evidence=delivered)
                if item:
                    packet['rubric_definition'] = dict(item=item)
                selected = paragraphs.select_prompt_parameters(role, packet).get(paragraphs.PARAMETER, [])
                self.assertEqual(PROBE_PARAGRAPH in selected, expected)
                if expected:
                    self.assertEqual(selected.index(PROBE_PARAGRAPH), selected.index('exactory-problem-accounts-v1') + 1)
                compact = dict(role=role, subject=packet['subject'])
                paragraphs.preserve_prompt_conditions(role, packet, compact)
                self.assertEqual(paragraphs.select_prompt_parameters(role, compact).get(paragraphs.PARAMETER, []), selected)
        for variant in ('no-probe', 'imported-only', 'version-one'):
            packet = dict(role='result', subject=dict(kind='problem'), problem_evidence=copy.deepcopy(delivered))
            evidence = packet['problem_evidence']
            if variant == 'version-one':
                evidence['value'].pop('accounts_version', None)
            elif variant == 'no-probe':
                evidence['account_records'] = []
            else:
                for row in evidence['account_records']:
                    row['cite']['origin'] = 'foreign-origin'
            self.assertNotIn(PROBE_PARAGRAPH, paragraphs.select_prompt_parameters('result', packet).get(paragraphs.PARAMETER, []))
        prompt = render_packet(dict(role='result', subject=dict(kind='problem'), problem_evidence=delivered))
        self.assertTrue(review_protocol._prompt_matches(build_assignment('result', prompt), packet=dict(
            subject=dict(kind='problem'), problem_evidence=delivered)))

    def test_native_and_imported_probe_of_one_id_keep_four_distinct_entries(self):
        fixture = import_fixtures.FormingImportTests()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        for case in (fixture.source, fixture.destination):
            case.mutate(cli.OPERATIONS['probe-intent'], build_probe_intent(case.artifacts, 'p1'))
            case.mutate(cli.OPERATIONS['probe'], build_probe_outcome(case.artifacts, 'p1'))
        fixture.source.mutate(cli.OPERATIONS['form-outcome'], build_outcome('with-probe', evidence=[dict(
            kind='observation', description='A bounded pattern.', standing='exploratory', date='2026-11-01',
            reference=dict(kind='probe', id='p1'), counterexample=None)]))
        fixture.import_history()
        destination = fixture.destination
        destination.select_statement()
        value = destination.build_value([destination.record_full_reading()])
        value['accounts']['checks'] = [build_probe_check(probe_id='p1')]
        destination.mutate(problem_claims.record_value, value)
        records = destination.store.snapshot()['records']
        for version in (2, 1):
            saved = records['problem_value']['value-1']
            if version == 1:
                saved.pop('accounts_version')
            binding = problem_evidence.describe_binding(records, destination.artifacts)
            delivered = problem_evidence.describe_evidence(records, destination.artifacts)
            assert_account_binding_delivery(self, binding, delivered)
            rows = [row for row in delivered['account_records'] if row['cite']['id'] == 'p1']
            self.assertEqual(len(rows), 4 if version == 2 else 2)
            if version == 2:
                self.assertEqual({(row['cite']['record_kind'], 'origin' in row['cite']) for row in rows},
                                 {(kind, foreign) for kind in ('probe', 'probe_intent') for foreign in (False, True)})

    def test_base_assignment_with_native_probes_keeps_records_digest_prompt_and_state(self):
        frozen = json.loads(lzma.decompress(BASE_FIXTURE.read_bytes()))
        records = frozen['records']
        build_probe_intent(self.case.artifacts)
        build_probe_outcome(self.case.artifacts)
        binding, delivered = self.describe(records)
        self.assertEqual(delivered['account_records'], frozen['account_records'])
        self.assertEqual([(row['cite']['record_kind'], row['cite']['id']) for row in delivered['account_records']
                          if row['cite']['record_kind'].startswith('probe')],
                         [('probe', 'probe-earlier'), ('probe', 'probe-refuted')])
        self.assertNotIn('accounts_version', delivered['value'])
        self.assertNotIn('probe_intent', problem_evidence.collect_binding_record_kinds(records))
        self.assertEqual(problem_claims.compute_problem_evidence_digest(records, self.case.artifacts), frozen['binding_digest'])
        self.assertEqual(problem_claims.describe_value_size(records, self.case.artifacts, records['problem_value']['value-1']), frozen['size_report'])
        assignment, packet = frozen['assignment'], frozen['packet']
        packet['problem_evidence'] = delivered
        self.assertEqual(render_packet(packet), assignment['prompt'])
        self.assertTrue(review_protocol._prompt_matches(assignment))
        self.assertTrue(review_protocol._prompt_matches(assignment, packet=packet))
        for row in frozen['artifacts']:
            self.assertEqual(self.case.artifacts.put(row['content'].encode(), row['reference']['media_type']), row['reference'])
        self.assertEqual(self.case.artifacts.put(json.dumps(frozen['packet'], sort_keys=True, ensure_ascii=False).encode(),
            'application/json'), assignment['packet'])
        records.update(frozen['assignment_records'])
        self.assertEqual(review_protocol.assignment_state(records, self.case.artifacts, assignment['id']), frozen['state'])

    def test_public_value_check_and_record_count_both_probe_records(self):
        case = accounts_fixtures.ProblemAccountsTests()
        case.setUp()
        self.addCleanup(case.doCleanups)
        case.mutate(cli.OPERATIONS['probe-intent'], build_probe_intent(case.artifacts, 'probe-check'))
        case.mutate(cli.OPERATIONS['probe'], build_probe_outcome(case.artifacts, 'probe-check'))
        case.value['accounts']['checks'] = [build_probe_check()]
        spec = case.root / 'value.json'
        spec.write_text(json.dumps(case.value))
        before = case.store.snapshot()
        checked = cli.run(cli.build_parser().parse_args(['problem-value', '--workspace', str(case.root),
            '--file', str(spec), '--expected-revision', str(case.store.revision), '--request-id', 'check-probe', '--check']))
        self.assertEqual(checked['check'], 'accepted')
        self.assertEqual(case.store.snapshot(), before)
        receipt = case.mutate(problem_claims.record_value, case.value)
        records = case.store.snapshot()['records']
        value = receipt['result']
        self.assertEqual(value['accounts_version'], 2)
        binding = problem_evidence.describe_binding(records, case.artifacts)
        delivered = problem_evidence.describe_evidence(records, case.artifacts)
        assert_account_binding_delivery(self, binding, delivered)
        self.assertEqual([(row['cite']['record_kind'], row['cite']['id']) for row in delivered['account_records']],
                         [('probe', 'probe-check'), ('probe_intent', 'probe-check')])
        self.assertEqual(receipt['size_report']['account_records_characters'],
                         len(json.dumps(delivered['account_records'], sort_keys=True, ensure_ascii=False)))
        value.pop('accounts_version')
        legacy = problem_claims.describe_value_size(records, case.artifacts, value)
        self.assertEqual(legacy['account_records_characters'], 2)  # The base's empty JSON list.
        case.value['accounts']['explanatory_use'][0]['evidence'] = [dict(kind='probe', id='probe-check')]
        case.assert_error('problem_explanatory_evidence_missing', lambda: case.record(dict(case.value, id='probe-fact')))
