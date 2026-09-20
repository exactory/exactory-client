"""Explicit component provenance through authored HTTP captures and public APIs."""

import copy
import hashlib
import inspect
import json

from literature_fixtures import LiteratureCase
from research_fixtures import client
from research_harness.acquisition import acquire_fulltext
from research_harness.graph import main_captures, validate_target
from research_harness.literature import foundation_report, import_bundle
from research_harness.reading import current_readings, record_reading, unit_digest
from research_harness.source_links import validate_link


class ComponentTests(LiteratureCase):
    def setUp(self):
        super().setUp()
        self.identifier = self.metadata()
        self.main = self.capture(self.identifier, body=(
            "Title Alpha. Authors Ada and Bob. Supporting Information is required. "
            "Figure 4: sample A at 340 nm. References: none."))
        self.initial = self.bundle(self.identifier, self.main)
        self.initial['units'].append({'id': 'si', 'kind': 'supplement', 'required': True,
                                     'link': None, 'reason': 'Cited Supporting Information.',
                                     'url': 'https://publisher.example/si.pdf'})
        self.mutate(import_bundle, self.initial)
        self.mutate(record_reading, self.full_note(self.initial, 'parent-inspected'))
        self.pdf = b'%PDF-1.4\n% Authored supplement fixture.\n%%EOF'
        self.si_text = ('Title Alpha. Authors Ada and Bob.\f'
                        'Figure 1S extends Figure 4: sample A at 340 nm.\f'
                        'Figure 2S: additional results for sample A.\f')
        parent = self.link(self.identifier, self.main['source_id'], self.main['text'])
        locator = {'kind': 'text', 'start': 0, 'end': len(self.si_text), 'quote': self.si_text}
        pair = {'parent': parent, 'component': {'document': 'text', 'locator': locator},
                'judgment': 'The inspected source passages correspond under the stated scope.'}
        self.assessment = {'status': 'accepted', 'scope': 'required_supplement_only',
                           'conclusion': 'This component supplies the cited supplementary material.',
                           'limitations': ['Historical attachment byte identity is not established.'],
                           'historical_attachment_identity': False, 'main_article_equivalence': False,
                           'evidence': {'requirement': parent,
                                        'identity': {'title': copy.deepcopy(pair), 'authors': copy.deepcopy(pair)},
                                        'references': [copy.deepcopy(pair)], 'conditions': [copy.deepcopy(pair)]}}
        self.component = {'kind': 'supplement', 'unit_id': 'si', 'parent_source_id': self.main['source_id'],
                          'parent_original_sha256': self.main['original']['sha256'],
                          'expected_original_sha256': hashlib.sha256(self.pdf).hexdigest(),
                          'basis': 'component_correspondence'}

    def acquire(self, *, component=True, identifier=None, assessment=None, data=None, text=None,
                request_id=None, url='https://publisher.example/si.pdf', status=200, extraction=None):
        if component:
            self.assertIn('component', inspect.signature(acquire_fulltext).parameters,
                          'The public acquisition API must admit the explicit component contract.')
        spec = copy.deepcopy(self.component)
        spec['assessment'] = self.artifacts.put(json.dumps(assessment or self.assessment).encode(), 'application/json')
        http, _, _ = client([(status, {'Content-Type': 'application/pdf'}, data or self.pdf)], max_retries=0)
        self.sequence += 1
        options = {'component': spec} if component else {}
        return acquire_fulltext(self.store, identifier or self.identifier, url, http=http,
                                extractor=lambda data: extraction or {'status': 'extracted', 'text': text or self.si_text},
                                expected_revision=self.store.revision,
                                request_id=request_id or 'component-' + str(self.sequence), **options)

    def filled_bundle(self, capture, *, pages=3, whole=True):
        bundle = copy.deepcopy(self.initial)
        bundle['id'] = 'filled-' + str(self.sequence)
        bundle['units'][-1]['link'] = self.link(self.identifier, capture['source_id'], capture['text'],
                                               None if whole else 'Title Alpha.')
        for page in range(pages):
            link = {'version_id': self.identifier, 'source_id': capture['source_id'], 'artifact': capture['original'],
                    'locator': {'kind': 'pdf', 'page_index': page, 'printed_page': None, 'region': [0, 0, 1, 1]}}
            bundle['units'].append({'id': 'si-page-' + str(page), 'kind': 'figure', 'required': True, 'link': link})
        return bundle

    def test_main_mode_stays_strict_and_old_failure_replays(self):
        first = self.acquire(component=False, request_id='old-failure')
        self.assertEqual(first['status'], 'pending')
        self.assertEqual(first['pending'][0]['code'], 'invalid_identifier')
        self.assertEqual(self.acquire()['status'], 'complete')
        self.assertEqual(self.acquire(component=False, request_id='old-failure'), first)
        wrong = self.acquire(component=False, url='https://arxiv.org/pdf/2601.00001v2')
        self.assertEqual(wrong['pending'][0]['code'], 'version_mismatch')

    def test_bound_component_has_truthful_http_provenance_and_no_reading_authority(self):
        result = self.acquire()
        self.assertEqual(result['status'], 'complete')
        capture = result['capture']
        self.assertEqual(capture['component']['status'], 'bound')
        self.assertEqual(capture['url'], 'https://publisher.example/si.pdf')
        self.assertIsNone(capture['version'])
        records = self.store.snapshot()['records']
        source = records['source'][capture['source_id']]
        self.assertIsNone(source['observed_identifier'])
        self.assertIsNone(source['exact_version'])
        self.assertEqual(source['capture_method'], 'http')
        self.assertEqual([c['source_id'] for c in main_captures(records, records['work'][self.identifier])],
                         [self.main['source_id']])
        self.assertFalse(current_readings(records, self.artifacts, self.identifier)[0])

    def test_component_requires_exact_parent_and_component_hashes(self):
        for field in ('parent_original_sha256', 'expected_original_sha256'):
            with self.subTest(field=field):
                old = self.component[field]
                self.component[field] = '0' * 64
                result = self.acquire()
                self.assertEqual(result['status'], 'pending')
                self.component[field] = old

    def test_component_rejects_wrong_parent_version_and_source(self):
        other = self.metadata(version=2)
        self.assertEqual(self.acquire(identifier=other)['status'], 'pending')
        self.component['parent_source_id'] = 'acq:missing'
        self.assertEqual(self.acquire()['status'], 'pending')

    def test_title_alone_missing_requirement_or_conditions_cannot_bind(self):
        for field in ('requirement', 'references', 'conditions'):
            with self.subTest(field=field):
                assessment = copy.deepcopy(self.assessment)
                del assessment['evidence'][field]
                self.assertEqual(self.acquire(assessment=assessment)['status'], 'pending')

    def test_pending_assessment_cannot_link(self):
        assessment = dict(self.assessment, status='pending')
        capture = self.acquire(assessment=assessment)['capture']
        self.assertEqual(capture['component']['status'], 'pending')
        self.assert_error('source_pending', lambda: validate_link(self.store.snapshot()['records'], self.artifacts,
                          self.link(self.identifier, capture['source_id'], capture['text'])))

    def test_component_locators_checked_against_fresh_extraction(self):
        self.assertEqual(self.acquire(text='Different acquired text.\f')['status'], 'pending')
        assessment = copy.deepcopy(self.assessment)
        assessment['evidence']['references'][0]['component'] = {
            'document': 'original', 'locator': {'kind': 'pdf', 'page_index': 3,
                                               'printed_page': None, 'region': [0, 0, 1, 1]}}
        self.assertEqual(self.acquire(assessment=assessment)['status'], 'pending')

    def test_component_cannot_be_main_bundle_or_verification_target(self):
        capture = self.acquire()['capture']
        records = self.store.snapshot()['records']
        target = {'kind': 'work', 'id': self.identifier, 'source_id': capture['source_id'],
                  'sha256': capture['original']['sha256']}
        self.assert_error('invalid_target', lambda: validate_target(records, target, [self.identifier]))
        bundle = self.filled_bundle(capture)
        bundle['source_id'] = capture['source_id']
        self.assert_error('invalid_bundle', lambda: self.mutate(import_bundle, bundle))

    def test_component_cannot_migrate_to_another_main_original(self):
        capture = self.acquire()['capture']
        different = self.capture(self.identifier, body='Different main version. References: none.')
        bundle = self.bundle(self.identifier, different, bundle_id='different-main')
        bundle['units'].extend(self.filled_bundle(capture)['units'][3:])
        self.assert_error('component_parent_mismatch', lambda: self.mutate(import_bundle, bundle))

    def test_complete_text_and_all_pages_need_actual_inspections(self):
        capture = self.acquire()['capture']
        for pages, whole in ((2, True), (3, False), (3, True)):
            with self.subTest(pages=pages, whole=whole):
                bundle = self.filled_bundle(capture, pages=pages, whole=whole)
                self.mutate(import_bundle, bundle)
                note = self.full_note(bundle, 'reading-' + str(self.sequence))
                result = self.mutate(record_reading, note)['result']
                self.assertEqual(result['status'], 'complete' if pages == 3 and whole else 'partial')
        bundle = self.filled_bundle(capture)
        self.mutate(import_bundle, bundle)
        result = self.mutate(record_reading, self.full_note(bundle, 'missing-inspection', omit=('si-page-2',)))['result']
        self.assertEqual(result['status'], 'partial')

    def test_binding_evidence_changes_dependency_and_is_revalidated(self):
        capture = self.acquire()['capture']
        bundle = self.filled_bundle(capture)
        self.mutate(import_bundle, bundle)
        self.mutate(record_reading, self.full_note(bundle))
        records = self.store.snapshot()['records']
        before = unit_digest(bundle, records)
        altered = copy.deepcopy(records)
        current = next(c for c in altered['work'][self.identifier]['fulltexts'] if c['source_id'] == capture['source_id'])
        current['component']['status'] = 'pending'
        self.assertNotEqual(before, unit_digest(bundle, altered))
        self.assert_error('component_pending', lambda: validate_link(altered, self.artifacts, bundle['units'][3]['link']))

    def test_uninspected_parent_evidence_cannot_bind(self):
        other = self.capture(self.identifier, body='New uninspected main. References: none.')
        bundle = self.bundle(self.identifier, other, bundle_id='uninspected')
        bundle['units'].append(copy.deepcopy(self.initial['units'][-1]))
        self.mutate(import_bundle, bundle)
        self.component['parent_source_id'] = other['source_id']
        self.component['parent_original_sha256'] = other['original']['sha256']
        assessment = copy.deepcopy(self.assessment)
        parent = self.link(self.identifier, other['source_id'], other['text'])
        assessment['evidence']['requirement'] = parent
        for pair in [*assessment['evidence']['identity'].values(), *assessment['evidence']['references'],
                     *assessment['evidence']['conditions']]:
            pair['parent'] = parent
        self.assertEqual(self.acquire(assessment=assessment)['status'], 'pending')

    def test_cross_work_and_version_links_cannot_reuse_identical_component_bytes(self):
        capture = self.acquire()['capture']
        for identifier in (self.metadata(number=2), self.metadata(version=2)):
            link = self.link(identifier, capture['source_id'], capture['text'])
            self.assert_error('source_mismatch', lambda: validate_link(self.store.snapshot()['records'], self.artifacts, link))

    def test_new_assessment_requires_new_reading_and_preserves_prior_records(self):
        self.scope([self.identifier])
        capture = self.acquire()['capture']
        bundle = self.filled_bundle(capture)
        self.mutate(import_bundle, bundle)
        self.mutate(record_reading, self.full_note(bundle))
        before = self.store.snapshot()['records']
        prior_digest = foundation_report(self.store, 'research')['digest']
        assessment = copy.deepcopy(self.assessment)
        assessment['limitations'].append('The component does not supply a separate density fit.')
        replacement = self.acquire(assessment=assessment)['capture']
        updated = self.filled_bundle(replacement)
        self.mutate(import_bundle, updated)
        records = self.store.snapshot()['records']
        accepted, partial = current_readings(records, self.artifacts, self.identifier)
        self.assertFalse(accepted)
        self.assertTrue(any(p['code'] == 'reading_bundle_stale' for r in partial for p in r['assessment']['pending']))
        self.assertEqual(before['reading']['full'], records['reading']['full'])
        self.assertEqual(capture, next(c for c in records['work'][self.identifier]['fulltexts']
                                      if c['source_id'] == capture['source_id']))
        self.assertNotEqual(prior_digest, foundation_report(self.store, 'research')['digest'])
        self.assertEqual(self.mutate(record_reading, self.full_note(updated, 'reassessed'))['result']['status'], 'complete')

    def test_partial_http_or_missing_extraction_cannot_bind(self):
        for options in ({'status': 206}, {'extraction': {'status': 'extraction_timeout', 'text': None}},
                        {'text': '   '}):
            with self.subTest(options=options):
                result = self.acquire(**options)
                self.assertEqual(result['status'], 'pending')
                self.assertEqual(result['capture']['component']['status'], 'pending')
                self.assertEqual(self.artifacts.read(result['capture']['original']), self.pdf)

    def test_required_unit_must_exist_before_component_acquisition(self):
        self.component['unit_id'] = 'undeclared-si'
        result = self.acquire()
        self.assertEqual(result['pending'][0]['code'], 'component_requirement_missing')

    def test_scientific_scope_cannot_be_promoted_to_main_or_historical_identity(self):
        for field, value in (('main_article_equivalence', True), ('historical_attachment_identity', True),
                             ('scope', 'whole_article'), ('limitations', [])):
            with self.subTest(field=field):
                assessment = dict(self.assessment, **{field: value})
                self.assertEqual(self.acquire(assessment=assessment)['pending'][0]['code'], 'invalid_component')

    def test_missing_assessment_artifact_invalidates_current_reading(self):
        capture = self.acquire()['capture']
        bundle = self.filled_bundle(capture)
        self.mutate(import_bundle, bundle)
        self.mutate(record_reading, self.full_note(bundle))
        artifact = capture['component']['spec']['assessment']
        (self.root / artifact['path']).unlink()
        self.assert_error('artifact_missing', lambda: current_readings(self.store.snapshot()['records'],
                                                                     self.artifacts, self.identifier))

    def test_original_page_evidence_binds_without_promoting_offline_extraction(self):
        assessment = copy.deepcopy(self.assessment)
        for pair in [*assessment['evidence']['identity'].values(), *assessment['evidence']['references'],
                     *assessment['evidence']['conditions']]:
            pair['component'] = {'document': 'original', 'locator': {'kind': 'pdf', 'page_index': 0,
                                                                   'printed_page': None, 'region': [0, 0, 1, 1]}}
        self.assertEqual(self.acquire(assessment=assessment)['status'], 'complete')

    def test_cli_admits_component_payload_without_network_budget(self):
        import subprocess
        import sys
        from pathlib import Path
        spec = dict(self.component, assessment=self.artifacts.put(json.dumps(self.assessment).encode(), 'application/json'))
        payload = {'identifier': self.identifier, 'url': 'https://publisher.example/si.pdf',
                   'component': spec, 'max_requests': 0}
        path = self.root / 'component-request.json'
        path.write_text(json.dumps(payload))
        result = subprocess.run([sys.executable, str(Path(__file__).resolve().parents[1] / 'bin/exactory-research'),
                                 'fulltext', '--file', str(path), '--expected-revision', str(self.store.revision),
                                 '--request-id', 'cli-component'], cwd=self.root, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        response = json.loads(result.stdout)
        self.assertEqual(response['status'], 'pending')
        self.assertEqual(response['attempts_used'], 0)
        self.assertEqual(response['capture']['component']['spec'], spec)

    def test_component_cannot_substitute_for_main_original_coverage(self):
        from research_harness.reading import fulltext_coverage
        capture = self.acquire()['capture']
        records = self.store.snapshot()['records']
        coverage = fulltext_coverage(records, self.artifacts, self.identifier)
        self.assertFalse(coverage['complete'])
        self.assertEqual({c['original_sha256'] for c in coverage['missing']}, {self.main['original']['sha256']})
        self.assertNotIn(capture['original']['sha256'], {c['original_sha256'] for c in coverage['missing']})
