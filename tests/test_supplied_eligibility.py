"""G8: supplied full readings keep their credit and reported origin through delivery."""

import copy
import hashlib
import json
import unittest
from unittest.mock import patch

from codex_route_fixtures import record_codex_route
from openness_reading_fixtures import OpennessReadingCase
from test_research_problem_claims import ProblemCase
import test_research_problem_openness_minimum as minimum_fixtures
from test_openness_finding_answer import build_answer
from research_fixtures import client
from research_harness import acquisition, bar_sources, cli, graph, openness_reading
from research_harness import problem_claims, problem_delivery, problem_export_history
from research_harness import problem_openness_sources
from research_harness import review_transport
from research_harness.errors import ResearchError
from research_harness.evidence import digest
from research_harness.http import HttpClient, safe_url
from research_harness.literature import import_bundle
from research_harness.reading import fulltext_coverage, record_reading
from research_harness.record_check import check_integrity


ORIGIN = dict(saved_by='A reader', obtained_from='https://publisher.example/survey.pdf',
              obtained_at='2026-11-12T16:30:00-08:00', statement='Saved the complete text in a browser.')
PDF = b'%PDF-1.4\n% Synthetic supplied survey.\n%%EOF\n'
BODY = ('A complete synthetic survey of bounded sequences.\n\n'
        + 'The finite construction has the stated bounded parameters.\n\n' * 2200 + 'References: none.\f')


class SuppliedCase(OpennessReadingCase):
    reading_protocol = None
    historical_v5 = False

    def setUp(self):
        ProblemCase.setUp(self)
        self.initialize_problem()
        self.put_selecting_slate()
        self.selected = self.select_statement(candidate_id='candidate-1')

    def import_text(self, shape='pdf'):
        version = ('doi:10.1234/survey' if shape == 'pdf' else
                   'url:https://sources.example/' + shape)
        data = json.dumps(dict(id=version, title='A synthetic ' + shape + ' source',
                               authors=['A. Reader'], year=2026)).encode()
        acquisition.import_response(self.store, 'mcp', data, source_url='https://sources.example/catalog',
            captured_at='2026-11-12T12:00:00Z', media_type='application/json',
            mappings=[dict(id='/id', title='/title')],
            expected_revision=self.store.revision, request_id='source-metadata')
        filename = {'pdf': 'survey.pdf', 'markdown': 'repository.md', 'question': 'question.txt'}[shape]
        body = {'pdf': PDF,
                'markdown': ('# Repository document\n\n' + BODY).encode(),
                'question': ('Question: Is every bounded sequence finite?\nAnswer: No.\n' + BODY).encode()}[shape]
        (self.root / filename).write_bytes(body)
        origin = dict(ORIGIN, obtained_from=('https://publisher.example/survey.pdf' if shape == 'pdf'
                                           else 'http://sources.example/' + filename))
        payload = dict(identifier=version, file=filename, origin=origin)
        path = self.root / 'import.json'
        path.write_text(json.dumps(payload))
        args = cli.build_parser().parse_args(['import-fulltext', '--workspace', str(self.root),
            '--file', str(path), '--expected-revision', str(self.store.revision), '--request-id', 'supplied'])
        # All reported addresses stay descriptive; only the local file is read.
        with patch.object(HttpClient, '_get', side_effect=AssertionError('A reported origin was fetched')), \
             patch.object(acquisition, 'extract', return_value={
                 'status': 'extracted', 'text': BODY, 'includes_abstract': None,
                 'visual_inspection_required': False, 'extractor': 'fixture', 'version': None,
                 'media_type': 'application/pdf', 'format_detection': 'reported_media_type', 'options': {}}):
            result = cli.run(args)
        capture = result['capture']
        bundle = self.bundle(version, capture, bundle_id='supplied-bundle')
        if version.startswith('url:'):
            bundle['units'][0]['url'] = version[4:]
        for unit in bundle['units']:
            unit['link'] = self.link(version, capture['source_id'], capture['text'],
                                     unit['link']['locator']['quote'], kind='span')
        bundle['inventory']['links'] = [bundle['units'][0]['link']]
        self.mutate(import_bundle, bundle)
        self.mutate(record_reading, self.full_note(bundle, 'supplied-reading'))
        self.versions, self.texts = [version], {version: capture['text']}
        return version, capture, body


class SuppliedEligibilityTests(unittest.TestCase):
    def create_case(self, shape='pdf', *, route=False):
        case = SuppliedCase()
        case.setUp()
        self.addCleanup(case.doCleanups)
        version, capture, body = case.import_text(shape)
        if route:
            record_codex_route(case, model='gpt-6-astra')
            case.limit_input(1300000)
        return case, version, capture, body

    def bind_search(self, case, version):
        case.search = minimum_fixtures.ProblemOpennessMinimumTests.search(case, [version])
        case.searches = [case.search]

    def test_supplied_reading_journey_keeps_origin_in_chain_session_export_and_post(self):
        # G3's single journey function covers a saved PDF and both shapes of record 18, T3.
        for shape in ('pdf', 'markdown', 'question'):
            with self.subTest(shape=shape):
                case, version, capture, original = self.create_case(shape, route=True)
                records = case.store.snapshot()['records']
                source = records['source'][capture['source_id']]
                origin = source['supplied']
                self.assertEqual(case.artifacts.read(capture['original']), original)
                self.assertEqual(capture['original']['sha256'], hashlib.sha256(original).hexdigest())
                self.assertEqual(origin['note'], acquisition.SUPPLIED_TEXT_NOTE)
                self.assertEqual(fulltext_coverage(records, case.artifacts, version)['readings'].keys(),
                                 {'supplied-reading'})
                self.bind_search(case, version)
                native_ref = case.export()
                native = json.loads(case.artifacts.read(native_ref))
                context = native['scientific_context']
                reading = case.store.snapshot()['records']['reading']['supplied-reading']
                self.assertEqual(context['openness_minimum']['reading_digests'], [digest(reading)])
                representation = problem_openness_sources.build_delivery(
                    case.store.snapshot()['records'], case.artifacts, native)['sources'][0]['representations'][0]
                self.assertEqual(representation['supplied'], origin)
                notice = next(row for row in context['audit_forecast']['notices'] if row.get('kind') == 'supplied_text')
                self.assertEqual((notice['version_id'], notice['supplied']), (version, origin))
                prior, delivered = None, False
                for number in range(1, 8):
                    identifier = 'turn-' + str(number)
                    assigned = case.assign(identifier, native_ref, prior)
                    self.assertIn(openness_reading.SUPPLIED_ORIGIN_SENTENCE, assigned['prompt']['text'])
                    packet = case.read_packet(identifier)
                    if openness_reading.find_stage(packet) == 'final':
                        case.invoke(identifier, build_answer(packet))
                        case.mutate(problem_claims.record_openness, dict(assignment_id=identifier))
                        break
                    units = packet['reading_turn']['reading_plan']['delivered_now']
                    for unit in units:
                        if unit['kind'] == 'fulltext':
                            self.assertEqual(unit['origin'], origin)
                            delivered = True
                    case.invoke(identifier, case.build_reading_response(packet, excerpts=[]))
                    _, sent = case.read_sent_packet(identifier)
                    for unit in sent['reading_turn']['reading_plan']['delivered_now']:
                        if unit['kind'] == 'fulltext':
                            self.assertEqual(unit['origin'], origin)
                    prior = identifier
                else:
                    self.fail('The supplied-source chain must reach its finding')
                self.assertTrue(delivered)
                records = case.store.snapshot()['records']
                with review_transport.text_only_delivery(True):
                    delivery = bar_sources._source(records, case.artifacts,
                        dict(id='requested', kind='source', version_id=version, depth='fulltext'), {})
                self.assertEqual(delivery['status'], 'delivered')
                self.assertEqual(delivery['source_provenance'][0]['supplied'], origin)
                self.assertEqual(bar_sources.prior_context(records, case.artifacts,
                    [reading['inspections'][0]['link']])[0]['supplied'], origin)
                self.assertEqual(openness_reading.list_math_text_rows(records, native)[0]['origin'], origin)
                request = dict(id='requested', kind='source', version_id=version, depth='fulltext')
                request_value = dict(status='unresolved', objective_adequacy='not_assessed',
                    method_adequacy='not_assessed', bar_ids=[], work_items=[], assurances=[], objection_findings=[])
                previous = dict(source_inventory=bar_sources.inventory(records, case.artifacts))
                with review_transport.text_only_delivery(True):
                    session = bar_sources.continuation(records, case.artifacts, previous,
                        dict(stage='bar', source_requests=[request], value=request_value))
                part = session['source_deliveries'][0]
                self.assertEqual(part['source_provenance'][0]['supplied'], origin)
                self.assertEqual(part['source_provenance'][0]['delivered_sha256'], [part['excerpt']['sha256']])
                passage = dict(id='passage', kind='passage', version_id=version,
                    anchor=case.artifacts.read(capture['text']).decode()[:40], characters=80)
                with review_transport.text_only_delivery(True):
                    session = bar_sources.continuation(records, case.artifacts, session,
                        dict(stage='bar', source_requests=[passage], value=request_value))
                answer = next(row for row in session['source_deliveries'] if row['request']['id'] == 'passage')
                self.assertEqual(answer['supplied'], origin)
                item = dict(kind='reading', version_id=version, reading_id='supplied-reading')
                case.mutate(problem_claims.record_value, case.build_value([item]))
                case.put('problem_decision', 'decision', dict(id='decision', action='post', review_ids=[],
                                                            recorded_revision=case.store.revision + 1))
                records = case.store.snapshot()['records']
                files, manifest = problem_delivery.build_projection(records, case.artifacts, include_references=True)
                labels = manifest['supplied_texts']
                self.assertEqual(labels, [dict(version_id=version, source_id=source['id'],
                                               original_sha256=capture['original']['sha256'], supplied=origin)])
                citations = json.loads(files['citations.json'])
                self.assertEqual(set(citations[0]), {'citation', 'locator'})
                self.assertNotIn(origin['note'], citations[0]['citation'])
                for spec in (None, {}):
                    override_files, override_manifest = problem_delivery.build_override_projection(
                        records, case.artifacts, dict(id='override'), include_references=True, citation_spec=spec)
                    self.assertEqual(override_manifest['supplied_texts'], labels)
                    self.assertEqual(json.loads(override_files['citations.json']), citations)
                directory = case.root / 'final/problem/exports' / problem_delivery.compute_export_digest(files)
                directory.mkdir(parents=True)
                for name, data in files.items():
                    (directory / name).write_bytes(data)
                self.assertEqual(problem_export_history.read_problem_export(directory, records), (manifest, files))
                with patch.object(problem_delivery, 'assess_post', return_value=dict(ready=True, obligations=[])):
                    _, post = problem_delivery.prepare_posted(records, case.artifacts,
                        dict(problem_id='synthetic-post', export_directory=str(directory)), case.store.revision + 1)
                self.assertEqual(post['supplied_texts'], labels)
                historical = case.root / 'final/history/problem/exports' / directory.name
                historical.parent.mkdir(parents=True)
                directory.rename(historical)
                self.assertEqual(problem_export_history.find_export_supplied_texts(
                    case.root, records, directory.name, manifest['statement_digest']), labels)
                external = case.mutate(problem_claims.record_post_external, dict(id='external-post',
                    problem_id='656c336e-e892-4c47-80d2-a71d022f4116',
                    statement_digest=problem_claims.compute_statement_digest(case.selected),
                    export_digest=directory.name, reason='An external post was observed.',
                    evidence='The server accepted the post.'))['result']
                self.assertEqual(external['supplied_texts'], labels)
                check_integrity(case.store)

    def test_post_without_an_export_labels_used_readings_and_leaves_unused_captures_out(self):
        case, version, capture, _ = self.create_case()
        self.assertEqual(problem_delivery.describe_cited_supplied_texts(case.store.snapshot()['records']), [])
        case.mutate(problem_claims.record_value, case.build_value([
            dict(kind='reading', version_id=version, reading_id='supplied-reading')]))
        expected = problem_delivery.describe_cited_supplied_texts(case.store.snapshot()['records'])
        self.assertEqual(expected[0]['source_id'], capture['source_id'])
        result = case.mutate(problem_claims.record_post_external, dict(id='external-post',
            problem_id='656c336e-e892-4c47-80d2-a71d022f4116',
            statement_digest=problem_claims.compute_statement_digest(case.selected),
            reason='An external post without an export was observed.', evidence='The server accepted the post.'))['result']
        self.assertEqual(result['supplied_texts'], expected)

    def test_fetched_main_replaces_supplied_and_identical_original_keeps_reading_credit(self):
        for same in (True, False):
            with self.subTest(same_original=same):
                case, version, supplied, original = self.create_case()
                before = copy.deepcopy(case.store.snapshot()['records']['reading']['supplied-reading'])
                body = original if same else PDF + b'% A distinct fetched edition.\n'
                http, _, _ = client([(200, {'Content-Type': 'application/pdf'}, body)])
                result = acquisition.acquire_fulltext(case.store, version, 'https://publisher.example/fetched.pdf',
                    http=http, extractor=lambda data: dict(status='extracted', text=BODY),
                    expected_revision=case.store.revision, request_id='fetched')
                records = case.store.snapshot()['records']
                self.assertEqual(graph.main_captures(records, records['work'][version]), [result['capture']])
                records['main_capture_selection'] = {'chosen': dict(version_id=version,
                    original_sha256=supplied['original']['sha256'], source_id=supplied['source_id'])}
                records['work'][version]['fulltexts'].reverse()
                self.assertEqual(graph.main_captures(records, records['work'][version]), [result['capture']])
                self.assertEqual(graph.main_captures(records, records['work'][version],
                    dict(id=version, sha256=supplied['original']['sha256'])), [result['capture']] if same else [])
                self.assertEqual(fulltext_coverage(records, case.artifacts, version)['complete'], same)
                self.assertEqual(records['reading']['supplied-reading'], before)

    def test_pending_fetch_and_supplement_do_not_displace_supplied_main(self):
        case, version, supplied, _ = self.create_case()
        records = case.store.snapshot()['records']
        work = copy.deepcopy(records['work'][version])
        pending = dict(supplied, source_id='pending', availability='pending')
        supplement = dict(supplied, source_id='supplement')
        work['fulltexts'].extend([pending, supplement])
        records['source']['supplement'] = dict(records['source'][supplied['source_id']],
            id='supplement', capture_method='http', origin_verified=True)
        records['source']['supplement'].pop('supplied')
        records['source_bundle']['supplied-bundle']['units'].append(dict(kind='supplement',
            link=dict(source_id='supplement')))
        self.assertEqual(graph.main_captures(records, work), [supplied])

    def test_supplied_text_is_not_fetched_reextraction_absence_or_citation_locator_evidence(self):
        from research_harness import fulltext_jobs, problem_evidence
        from research_harness.reading import qualify_availability
        case, version, capture, _ = self.create_case()
        records = case.store.snapshot()['records']
        source = records['source'][capture['source_id']]
        before = case.store.snapshot()
        with self.assertRaises(ResearchError) as caught:
            fulltext_jobs.start_job(case.store, version, source_id=source['id'],
                expected_revision=case.store.revision, request_id='reextract-supplied')
        self.assertEqual(caught.exception.code, 'source_pending')
        with self.assertRaises(ResearchError) as caught:
            fulltext_jobs.validate_assembly(records, case.artifacts, source)
        self.assertEqual(caught.exception.code, 'source_pending')
        availability = dict(id='absence', profile='research', version_id=version, depth='fulltext',
            source_ids=[source['id']], reason='A supplied text is not an origin failure response.',
            policy=dict(id='terminal', minimum_attempts=1, allowed_statuses=[403], rationale='Require an HTTP failure.'))
        with self.assertRaises(ResearchError) as caught:
            qualify_availability(records, case.artifacts, availability)
        self.assertEqual(caught.exception.code, 'invalid_availability')
        with self.assertRaises(ResearchError) as caught:
            problem_evidence.validate_citation_locator(records, case.artifacts, dict(id='locator',
                version_id=version, reading_id='supplied-reading', original_sha256=capture['original']['sha256'],
                url=source['url'], source_id=source['id']))
        self.assertEqual(caught.exception.code, 'problem_citation_locator_unbound')
        self.assertEqual(case.store.snapshot(), before)

    def test_http_reported_origins_keep_url_guards_and_never_relax_fetch_urls(self):
        for url in ('http://publisher.example/file', 'http://publisher.example:80/file',
                    'https://publisher.example:443/file'):
            with self.subTest(url=url):
                self.assertEqual(acquisition.validate_supplied_origin(dict(ORIGIN, obtained_from=url))['obtained_from'], url)
        for url in ('http://localhost/file', 'http://127.0.0.1/file', 'http://10.0.0.1/file',
                    'http://user:password@publisher.example/file', 'http://publisher.example/file#fragment',
                    'http://publisher.example:81/file', 'http://publisher.example/file?api_key=secret',
                    'http://publisher.example./file', 'ftp://publisher.example/file', 'file:///input.txt'):
            with self.subTest(url=url), self.assertRaises(ResearchError) as caught:
                acquisition.validate_supplied_origin(dict(ORIGIN, obtained_from=url))
            self.assertEqual(caught.exception.code, 'unsafe_url')
        with self.assertRaises(ResearchError) as caught:
            safe_url('http://publisher.example/file')
        self.assertEqual(caught.exception.code, 'unsafe_url')

    def test_v6_prompts_explain_supplied_origin_without_changing_prior_prompts(self):
        from openness_legacy_fixtures import load_0_87_0_openness_reading
        old = load_0_87_0_openness_reading()
        for protocol in (openness_reading.PROTOCOL_V4, openness_reading.PROTOCOL_V5, openness_reading.MATH_PROTOCOL):
            self.assertEqual(openness_reading.PROMPTS[protocol], old.PROMPTS[protocol])
        for stage in ('reading', 'final'):
            self.assertIn(openness_reading.SUPPLIED_ORIGIN_SENTENCE, openness_reading.PROMPTS[openness_reading.PROTOCOL_V6][stage])


if __name__ == '__main__':
    unittest.main()
