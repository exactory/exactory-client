"""V5 selected-source credit preserves independent roles and reports final capacity."""

import copy
import json
import unittest
from unittest.mock import patch

from codex_route_fixtures import invoke_codex, record_codex_route
from openness_reading_fixtures import OpennessReadingCase
import test_problem_openness_source_delivery as source_fixtures
from research_harness import openness_reading as reading, problem_openness_sources as sources
from research_harness import review_excerpts, review_protocol as protocol, review_transport as transport
from research_harness.errors import ResearchError
from research_harness.evidence import digest


class SelectedSourceTests(unittest.TestCase):
    def setUp(self):
        self.fixture = source_fixtures.SourceDeliveryTests()
        self.addCleanup(self.fixture.doCleanups)
        self.fixture.setUp()
        self.case = self.fixture.case
        record_codex_route(self.case, 'codex-route')

    def test_protection_roles_are_distinct_and_independent_of_text_only_delivery(self):
        f = self.case
        packet = self.fixture.standalone_packet('roles')
        sha256 = self.fixture.pdf['text']['sha256']
        for role in ('selected_text', 'required_proof', 'screening', 'original', 'figure', 'table', 'equation',
                     'supplement', 'inspection_asset'):
            with self.subTest(role=role):
                native = copy.deepcopy(packet)
                records = copy.deepcopy(f.store.snapshot()['records'])
                if role == 'required_proof':
                    native['scientific_context']['native_reviews'][0]['required_evidence_digests'].append(sha256)
                elif role == 'screening':
                    native['scientific_context']['source_evidence'] = [
                        {'source_record': f.proof, 'delivered_artifact': self.fixture.pdf['text']}]
                elif role == 'original':
                    native['scientific_context']['openness_delivery']['sources'][0][
                        'representations'][0]['original_descriptor'] = self.fixture.pdf['text']
                elif role in ('figure', 'table', 'equation', 'supplement'):
                    bundle = next(iter(records['source_bundle'].values()))
                    bundle['units'].append({'id': 'extra', 'kind': role, 'required': True,
                        'link': f.link(self.fixture.work, self.fixture.pdf['source_id'],
                                       self.fixture.pdf['text'], kind='span')})
                elif role == 'inspection_asset':
                    next(iter(records['reading'].values()))['inspections'][0]['link']['locator'][
                        'assets'] = [{'artifact': self.fixture.pdf['text']}]
                native['scientific_context']['openness_minimum']['reading_digests'] = [
                    digest(row) for row in records['reading'].values()]
                rows = reading.list_reading_rows(f.store.snapshot()['records'], f.artifacts, packet)
                classify = getattr(sources, 'classify_selected_texts', None)
                self.assertTrue(callable(classify), 'Selected texts need separate, complete protection reasons')
                results = []
                for text_only in (False, True):
                    with transport.text_only_delivery(text_only):
                        results.append(classify(records, f.artifacts, native, rows)[sha256])
                self.assertEqual(results[0], results[1])
                self.assertIn(role, results[0])
                self.assertIn('selected_text', results[0])
                self.assertIn(sha256, sources.protected_evidence({'native': native}))
                outer = {'native': native, 'evidence': [{'artifact': row['artifact']} for row in rows],
                         'reading_record': reading._create_record(reading.PROTOCOL_V5)}
                eligible = reading._classify_final_texts(records, f.artifacts, native, rows, reading.PROTOCOL_V5)
                candidates = reading._list_withholding_candidates(outer, rows, eligible)
                self.assertEqual(sha256 in {row['artifact']['sha256'] for row in candidates},
                                 role == 'selected_text')

    def finish(self, budget):
        f = self.case
        native = self.fixture.standalone_packet('selected-chain')
        reference = f.pin_json(native)
        prior, delivered, final = None, [], None
        with patch.object(review_excerpts, 'TURN_FULLTEXT_CHARACTERS', budget):
            for number in range(1, 40):
                identifier = 'selected-chain' if number == 1 else 'selected-chain-%d' % number
                context = {'native_packet': reference}
                if prior:
                    context['prior_assignment_id'] = prior
                value = {'id': identifier, 'route_id': 'codex-route', 'dossier_id': None, 'role': 'native_math',
                         'reviewer_id': native['reviewer']['actor_id'], 'author_id': 'problem-author',
                         'context': context}
                records = f.store.snapshot()['records']
                with transport.text_only_delivery(True):
                    packet = protocol.build_packet(records, f.artifacts, None, 'native_math', value['reviewer_id'],
                                                   native_packet=reference)
                    trial, _ = reading.prepare_packet(records, f.artifacts, value,
                                                      records['review_route']['codex-route'], packet)
                if reading.find_stage(trial) == 'final':
                    route = records['review_route']['codex-route']
                    prompt = protocol.canonical_prompt('native_math', reading_stage='final',
                                                       reading_protocol=reading.PROTOCOL_V5)['text']
                    size = reading._measure(route, transport.request_body(route, prompt, trial, order=()))
                    with patch.object(transport, 'CODEX_INPUT_LIMIT_CHARS', size - 1):
                        result = f.mutate(protocol.record_assignment, value)['result']
                    final = json.loads(f.artifacts.read(result['packet']))
                    invoke_codex(f, identifier, f.output(native, 'supported_open'))
                    break
                result = f.mutate(protocol.record_assignment, value)['result']
                packet = json.loads(f.artifacts.read(result['packet']))
                delivered.extend(packet['reading_turn']['reading_plan']['delivered_now'])
                binding = packet['reading_turn']
                output = dict({key: binding[key] for key in ('subject_digest', 'claim_digest', 'reviewer')},
                              schema_version=1, stage='reading', notes='Read the exact source units.',
                              excerpts=[], passage_requests=[])
                invoke_codex(f, identifier, output)
                prior = identifier
            self.assertIsNotNone(final, 'The selected-source chain must reach its final')
            sha256 = self.fixture.pdf['text']['sha256']
            entry = next(row for row in final['evidence'] if row['artifact']['sha256'] == sha256)
            self.assertEqual(entry.get('delivery'), reading.BODY_WITHHELD_DELIVERY)
            self.assertEqual(entry['content'], '')
            self.assertEqual(entry['artifact']['sha256'], sha256)
            pieces = [row for row in delivered if row.get('text_sha256') == sha256]
            self.assertEqual(len(pieces), pieces[0]['parts'])
            if budget < 1000:
                self.assertGreater(len(pieces), 1)
            else:
                self.assertEqual(len(pieces), 1)
            records = f.store.snapshot()['records']
            assignment = records['review_assignment'][identifier]
            state = protocol.assignment_state(records, f.artifacts, identifier)
            self.assertTrue(state['ready'], state['obligations'])
            verified = reading.verify_problem_coverage(records, f.artifacts, assignment, final=True)
            unit = next(row for row in verified['units'] if row.get('text_sha256') == sha256)
            for receipt in unit['deliveries']:
                self.assertIn(receipt['assignment_id'], entry['note'])
            self.assertEqual(sources.validate_delivery(records, f.artifacts, native, state),
                             native['scientific_context']['openness_delivery'])
            supplied = review_excerpts.read_sent_packet(protocol.build_assignment_request(records, f.artifacts,
                                                                                        assignment))
            for mode in ('note', 'descriptor', 'marker', 'coverage'):
                with self.subTest(mode=mode):
                    altered = copy.deepcopy(supplied)
                    target = next(row for row in altered['evidence'] if row['artifact']['sha256'] == sha256)
                    if mode == 'note':
                        target['note'] = 'A fabricated delivering turn.'
                    elif mode == 'descriptor':
                        target['artifact']['size'] += 1
                    elif mode == 'marker':
                        target['delivery'] = 'unverified omission'
                    with self.assertRaises(ResearchError):
                        sources._projection(altered, projected=True, withheld_texts=(
                            {} if mode == 'coverage' else {sha256: unit['deliveries']}))
            saved = f.mutate(f.load_problem_module().record_openness, {'assignment_id': identifier})['result']
            self.assertEqual(saved['openness_delivery'], native['scientific_context']['openness_delivery'])
            self.assertEqual(f.assessment(), [])
            # Saved descriptors and receipt claims supply no credit after an earlier observation loses authentication.
            attempt_id = unit['deliveries'][0]['attempt_id']
            records['review_attempt'][attempt_id]['origin'] = 'imported'
            with self.assertRaises(ResearchError):
                sources.validate_delivery(records, f.artifacts, native, state)
            return final

    def test_whole_selected_text_survives_projection_and_is_verified_again(self):
        self.finish(3000)

    def test_multipart_selected_text_survives_projection_and_is_verified_again(self):
        self.finish(700)


class HeadroomTests(OpennessReadingCase):
    reading_protocol = None
    text_sizes = (125000,)

    def test_every_assignment_exposes_low_headroom_before_invocation(self):
        native = json.loads(self.artifacts.read(self.export()))
        proof = self.artifacts.put(b'M' * 1070000, 'text/plain')
        native['evidence'].append({'digest': proof['sha256'], 'kind': 'artifact', 'artifact': proof})
        native['scientific_context']['native_reviews'] = [{'required_evidence_digests': [proof['sha256']]}]
        reference = self.artifacts.put(json.dumps(native).encode(), 'application/json')
        with patch.object(transport, 'CODEX_INPUT_LIMIT_CHARS', 1300000):
            prior = None
            for number in range(1, 4):
                identifier = 'turn-%d' % number
                result = self.assign(identifier, reference, prior)
                self.assertIn('diagnostics', result, 'Successful assignments must expose final-input headroom')
                report = result['diagnostics']['final_input']
                self.assertEqual(report['status'], 'measured')
                self.assertEqual(report['input_limit_characters'], 1300000)
                self.assertAlmostEqual(report['input_characters'], 1080000, delta=25000)
                self.assertEqual(report['headroom_upper_bound_characters'], 1300000 - report['input_characters'])
                self.assertEqual(report['remaining_allowances'], {'excerpts': 200000, 'passages': 100000})
                self.assertTrue(report['capacity_warning'])
                self.assertTrue(report['mandatory_components'])
                self.assertNotIn('diagnostics', self.read_packet(identifier))
                self.assertNotIn('diagnostics', self.store.snapshot()['records']['review_assignment'][identifier])
                packet = self.read_packet(identifier)
                self.invoke(identifier, self.build_reading_response(packet, excerpts=[])
                            if reading.find_stage(packet) == 'reading' else self.build_native_result(packet))
                prior = identifier


class UnknownHeadroomTests(unittest.TestCase):
    def test_unsupported_representations_keep_capacity_unknown(self):
        from research_harness.review_source_complete import MARKER
        record = reading._create_record(reading.PROTOCOL_V5, {'searches': [], 'shared_fields': []})
        packet = {'native': {'scientific_context': {}}}
        cases = [({'adapter': 'codex_cli_visual_v1'}, packet, 1300000),
                 ({'adapter': 'codex_cli_framed_v1'},
                  {'native': {'scientific_context': {'review_text_encoding': 'encoded'}}}, 1300000),
                 ({'adapter': 'codex_cli_v1'}, dict(packet, **{MARKER: {}}), 1300000),
                 ({'adapter': 'codex_cli_v1'}, packet, None)]
        for route, supplied, limit in cases:
            with self.subTest(adapter=route['adapter'], limit=limit):
                report = reading.preflight_final_input(route, limit, supplied, [], record)
                self.assertEqual(report['status'], 'unknown')
                self.assertIsNone(report['input_characters'])
                self.assertIsNone(report['headroom_upper_bound_characters'])
                self.assertIsNone(report['capacity_warning'])
                self.assertEqual(report['remaining_allowances'], {'excerpts': 200000, 'passages': 100000})


class IncidentFinalTests(OpennessReadingCase):
    reading_protocol = None
    text_sizes = (70000,) * 12

    def record_searches(self):
        from test_research_problem_openness_minimum import ProblemOpennessMinimumTests
        return [ProblemOpennessMinimumTests.search(self, self.versions[:6]),
                ProblemOpennessMinimumTests.search(self, self.versions[6:])]

    def test_twelve_selected_texts_and_224_retained_excerpts_fit_the_final(self):
        native = json.loads(self.artifacts.read(self.export(priority=self.versions)))
        proof = self.artifacts.put(b'M' * 200000, 'text/plain')
        native['evidence'].append({'digest': proof['sha256'], 'kind': 'artifact', 'artifact': proof})
        native['scientific_context']['native_reviews'] = [{'required_evidence_digests': [proof['sha256']]}]
        records = self.store.snapshot()['records']
        native['scientific_context']['openness_delivery'] = sources.build_delivery(records, self.artifacts, native)
        reference = self.artifacts.put(json.dumps(native).encode(), 'application/json')
        prior, retained, reports = None, 0, []
        with patch.object(transport, 'CODEX_INPUT_LIMIT_CHARS', 1300000):
            for number in range(1, 30):
                identifier = 'turn-%d' % number
                result = self.assign(identifier, reference, prior)
                packet = self.read_packet(identifier)
                self.assertIn('diagnostics', result)
                reports.append(result['diagnostics']['final_input'])
                if reading.find_stage(packet) == 'final':
                    break
                texts = [row for row in packet['reading_turn']['reading_plan']['delivered_now']
                         if row['kind'] == 'fulltext']
                excerpts = []
                if texts:
                    version = texts[0]['version_id']
                    content = self.artifacts.read(self.texts[version]).decode()
                    count = min(19, 224 - retained)
                    excerpts = [{'version_id': version, 'text': content[1000:1650],
                                 'purpose': 'Retained scientific passage %d.' % (retained + index)}
                                for index in range(count)]
                    retained += count
                self.invoke(identifier, self.build_reading_response(packet, excerpts=excerpts))
                prior = identifier
            else:
                self.fail('The incident-shaped chain must finish')
            self.assertEqual(retained, 224)
            self.assertEqual(len(packet['reading_record']['excerpts']), 224)
            self.assertTrue(all(row['status'] == 'verified' for row in packet['reading_record']['excerpts']))
            selected = {row['selected_text_sha256'] for row in native['scientific_context']['openness_delivery']['sources']}
            self.assertEqual(len(selected), 12)
            # With the old selected-text rule the first floor was already near the measured incident's 1.08M.
            old_floor = reports[0]['input_characters'] + sum(
                len(self.artifacts.read(self.texts[version]).decode()) for version in self.versions)
            self.assertAlmostEqual(old_floor, 1080000, delta=25000)
            withheld = {row['artifact']['sha256'] for row in packet['evidence']
                        if row.get('delivery') == reading.BODY_WITHHELD_DELIVERY}
            self.assertTrue(selected & withheld)
            prompt = protocol.canonical_prompt('native_math', reading_stage='final',
                                               reading_protocol=reading.PROTOCOL_V5)['text']
            route = self.store.snapshot()['records']['review_route']['route']
            request = transport.request_body(route, prompt, packet, order=())
            measured = reading._measure(route, request)
            self.assertLessEqual(measured, 1300000)
            self.assertLessEqual(reports[-1]['input_characters'], measured)
            unreduced = copy.deepcopy(packet)
            for entry in unreduced['evidence']:
                if entry.get('delivery') == reading.BODY_WITHHELD_DELIVERY:
                    entry['content'] = self.artifacts.read(entry['artifact']).decode()
                    del entry['delivery']
                    del entry['note']
            self.assertGreater(reading._measure(route, transport.request_body(route, prompt, unreduced, order=())),
                               1300000)
            self.assertEqual(reports[-1]['remaining_allowances'], {
                kind: value['characters'] - value['used']
                for kind, value in packet['reading_record']['allowances'].items()})
            supplied = review_excerpts.read_sent_packet(request)
            proof_entry = next(row for row in supplied['evidence'] if row['artifact']['sha256'] == proof['sha256'])
            self.assertEqual(proof_entry['content'].encode(), self.artifacts.read(proof))
            self.assertEqual(len(reports[0]['planned_removable_texts']), 12)
            self.assertTrue(all(not row['deliveries'] for row in reports[0]['planned_removable_texts']))
            self.assertTrue(all(row['deliveries'] for row in reports[-1]['planned_removable_texts']))
            self.invoke(identifier, self.build_native_result(packet))
            records = self.store.snapshot()['records']
            state = protocol.assignment_state(records, self.artifacts, identifier)
            sources.validate_delivery(records, self.artifacts, native, state)
