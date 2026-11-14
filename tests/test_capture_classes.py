"""Each complete-original use retains its existing capture-class boundary."""

import ast
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import literature_fixtures
import test_research_fulltext_jobs as job_fixtures
from test_review_html_delivery import packet as build_html_packet
from research_harness import bar_sources, literature, problem_openness_sources
from research_harness import review_source_complete, review_transport, source_links
from research_harness.errors import ResearchError
from research_harness.evidence import digest
from research_harness.reading import record_reading


ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / 'tests/fixtures/research/capture-classes.json'
CAPTURE_FIELD_ROLES = {
    'acquisition.py': 'Record externally imported responses.',
    'bar_sources.py': 'Deliver session sources and retain their provenance and range sizes.',
    'evidence.py': 'Record fetched source provenance.',
    'fulltext_jobs.py': 'Authenticate retained originals and assembled range provenance.',
    'fulltext_transfer.py': 'Record complete and assembled HTTP representations.',
    'oai_cohort.py': 'Record imported cohort provenance.',
    'problem_citation_metadata.py': 'Retain citation provenance.',
    'problem_evidence.py': 'Authenticate fetched absence evidence.',
    'problem_openness_sources.py': 'Copy source provenance into openness representations.',
    'reading.py': 'Authenticate fetched registry evidence.',
    'review_source_complete.py': 'Measure assembled originals without reading their whole body.',
    'review_transport.py': 'Elide only a fetched HTML original with its complete extraction (C28).',
    'source_candidates.py': 'Describe held candidate provenance.',
    'source_links.py': 'Own capture admission by use and validate assembled source bytes.',
    'visual_assets.py': 'Require a fetched HTML original for visual resources.',
}


class CaptureClassTests(unittest.TestCase):
    def find_capture_field_modules(self, directory):
        modules = {}
        for path in directory.rglob('*.py'):
            tree = ast.parse(path.read_text())
            names = []
            for node in ast.walk(tree):
                if isinstance(node, ast.Constant) and isinstance(node.value, str):
                    names.append(node.value)
                elif isinstance(node, ast.keyword):
                    names.append(node.arg)
                elif isinstance(node, ast.Attribute):
                    names.append(node.attr)
                elif isinstance(node, ast.Name):
                    names.append(node.id)
            if any(name in ('capture_method', 'origin_verified') for name in names):
                modules[str(path.relative_to(directory))] = CAPTURE_FIELD_ROLES.get(path.name)
        return modules

    def create_capture_case(self, method):
        if method == 'http_range_v1':
            case = job_fixtures.JobCase()
            self.addCleanup(case.doCleanups)
            case.setUp()
            job = case.start(allowances={'pages_per_resume': 5})
            self.assertEqual(case.resume(job, http=case.wire()[0])['status'], 'complete')
            version = job_fixtures.VERSION
        else:
            case = literature_fixtures.LiteratureCase()
            self.addCleanup(case.doCleanups)
            case.setUp()
            version = case.metadata()
            case.capture(version, pdf_text='A complete synthetic theorem. References: none.\f')
        records = case.store.snapshot()['records']
        capture = records['work'][version]['fulltexts'][-1]
        if method == 'external_import':
            source = records['source'][capture['source_id']]
            source.update(capture_method=method, origin_verified=False, imported=True, http_status=None)
            case.store.mutate('fixture.import', {},
                lambda tx: tx.put('source', source['id'], source),
                expected_revision=case.store.revision, request_id='external-source')
        return case, version, capture

    def build_bundle(self, case, version, capture):
        link = {'version_id': version, 'source_id': capture['source_id'], 'artifact': capture['text'],
                'locator': source_links.span_locator(case.artifacts.read(capture['text']).decode(),
                                                    0, capture['text']['size'])}
        return {'id': 'class-bundle', 'version_id': version, 'source_id': capture['source_id'],
                'scope': 'article', 'completeness': 'complete',
                'units': [{'id': 'body', 'kind': 'text', 'required': True, 'link': link},
                          {'id': 'references', 'kind': 'bibliography', 'required': True, 'link': link}],
                'inventory': {'text': 'This synthetic article contains its text and references.', 'links': [link]},
                'bibliography': {'complete': True, 'unit_id': 'references', 'entries': []}, 'resolutions': []}

    def build_openness_input(self, version, capture, source, *, alternate=False):
        representation = {'source_id': source['id'], 'version_id': version, 'main_representation': True,
            'source_scope_pending': [], 'original_descriptor': bar_sources.descriptor(capture['original']),
            'extraction_descriptor': bar_sources.descriptor(capture['text']),
            'capture_method': source['capture_method'], 'origin_verified': source['origin_verified'],
            'observed_identifier': version}
        choice = dict(representation, capture_method='http', origin_verified=True) if alternate else representation
        representations = [choice]
        if alternate:
            representations.append(dict(representation, source_id='alternate', main_representation=False))
        delivery = {'schema_version': 1, 'sources': [{'version_id': version,
            'selected_source_id': source['id'], 'selected_text_sha256': capture['text']['sha256'],
            'selected_original_sha256': capture['original']['sha256'], 'representations': representations}],
            'contained_sources': [], 'alternate_body_digests': [], 'protected_source_digests': []}
        return {'native': {'scientific_context': {'openness_delivery': delivery}},
                'evidence': [{'artifact': capture['text'], 'encoding': 'utf-8',
                              'content': 'A complete synthetic theorem. References: none.\f'}]}

    def collect_site_results(self, case, version, capture, source):
        records = case.store.snapshot()['records']
        records['source'][source['id']] = source
        work = records['work'][version]
        bundle = self.build_bundle(case, version, capture)
        # The command sees the same source as the pure delivery checks.
        case.store.mutate('fixture.class', {}, lambda tx: tx.put('source', source['id'], source),
            expected_revision=case.store.revision, request_id='source-class-' + str(case.store.revision))
        row = {'source_id': source['id'], 'version_id': version, 'main_representation': True,
               'original_descriptor': bar_sources.descriptor(capture['original']),
               'extraction_descriptor': bar_sources.descriptor(capture['text'])}
        supplied = self.build_openness_input(version, capture, source)
        supplied['evidence'][0]['content'] = case.artifacts.read(capture['text']).decode()
        alternate = self.build_openness_input(version, capture, source, alternate=True)
        alternate['evidence'][0]['content'] = supplied['evidence'][0]['content']
        html = build_html_packet(size=256)
        html['source_deliveries'][0]['source_provenance'][0].update(
            capture_method=source['capture_method'], origin_verified=source['origin_verified'])
        actions = {
            'article_bundle': lambda: literature.import_bundle(case.store, bundle,
                expected_revision=case.store.revision, request_id='bundle-' + str(case.store.revision)),
            'complete_original': lambda: source_links.complete_original({'source': source, 'capture': capture}),
            'session_source_delivery': lambda: bar_sources._fulltext_source(records, case.artifacts, work, capture),
            'openness_packet_source': lambda: problem_openness_sources.apply_delivery(supplied),
            'openness_packet_alternative': lambda: problem_openness_sources.apply_delivery(alternate),
            'complete_source_route': lambda: review_source_complete._native_alternative(records, case.artifacts, version, row),
            'html_original_elision': lambda: bool(list(review_transport._elide_html_originals(html))),
        }
        results = {}
        for use, action in actions.items():
            try:
                result = action()
                results[use] = result if type(result) is bool else True
            except ResearchError as error:
                results[use] = error.code
        return results

    def test_table_preserves_base_results_at_every_site_for_existing_classes(self):
        admits = getattr(source_links, 'admits_capture_class', None)
        self.assertTrue(callable(admits), 'Capture admission needs one table of uses in source_links')
        fixture = json.loads(FIXTURE.read_text())
        for method, variants in fixture['results'].items():
            case, version, capture = self.create_capture_case(method)
            source = case.store.snapshot()['records']['source'][capture['source_id']]
            for variant, expected in variants.items():
                source = dict(source, origin_verified=variant == 'verified')
                with self.subTest(method=method, origin=variant):
                    self.assertEqual(self.collect_site_results(case, version, capture, source), expected)
                    for use in source_links.CAPTURE_CLASSES_BY_USE:
                        self.assertEqual(admits(source, use), expected[use] is True)
        # Missing, truthy and future origins must not gain verified credit.
        for origin in (None, False, 1, 'true'):
            for use in source_links.CAPTURE_CLASSES_BY_USE:
                with self.subTest(origin=origin, use=use):
                    self.assertFalse(admits({'capture_method': 'http', 'origin_verified': origin}, use))
        for method in ('external_import', 'supplied_file', 'unknown', None):
            for use in source_links.CAPTURE_CLASSES_BY_USE:
                self.assertFalse(admits({'capture_method': method, 'origin_verified': True}, use))

    def test_each_nonruntime_site_reads_its_row_and_keeps_stored_identities(self):
        table = getattr(source_links, 'CAPTURE_CLASSES_BY_USE', None)
        self.assertIsNotNone(table, 'The existing sites must read their own capture-class row')
        case, version, capture = self.create_capture_case('http')
        source = case.store.snapshot()['records']['source'][capture['source_id']]
        expected = json.loads(FIXTURE.read_text())
        for use, refusal in expected['row_refusals'].items():
            with self.subTest(use=use), patch.object(source_links, 'CAPTURE_CLASSES_BY_USE', dict(table, **{use: ()})):
                results = self.collect_site_results(case, version, capture, source)
                self.assertEqual(results[use], refusal)
                if use == 'openness_packet_source':
                    self.assertEqual(results['openness_packet_alternative'], refusal)
        self.collect_site_results(case, version, capture, source)
        records = case.store.snapshot()['records']
        bundle = records['source_bundle']['class-bundle']
        note = literature_fixtures.LiteratureCase.full_note(case, bundle)
        record_reading(case.store, note, expected_revision=case.store.revision, request_id='full-reading')
        records = case.store.snapshot()['records']
        with review_transport.text_only_delivery(True):
            delivery = bar_sources._source(records, case.artifacts,
                {'id': 'held', 'kind': 'source', 'version_id': version, 'depth': 'fulltext'}, {})
        pins = {'source': digest(source), 'capture': digest(capture), 'bundle': digest(bundle),
                'reading': digest(records['reading']['full']), 'delivery': digest(delivery)}
        self.assertEqual(pins, expected['stored_digests'])
        # Admitting range here must affect both the selected and alternative checks.
        case, version, capture = self.create_capture_case('http_range_v1')
        source = case.store.snapshot()['records']['source'][capture['source_id']]
        with patch.object(source_links, 'CAPTURE_CLASSES_BY_USE',
                          dict(table, openness_packet_source=(('http', True), ('http_range_v1', True)))):
            results = self.collect_site_results(case, version, capture, source)
            self.assertIs(results['openness_packet_source'], True)
            self.assertIs(results['openness_packet_alternative'], True)

    def test_capture_fields_stay_in_modules_with_reviewed_roles(self):
        modules = self.find_capture_field_modules(ROOT / 'research_harness')
        self.assertEqual(set(modules), set(CAPTURE_FIELD_ROLES))
        self.assertTrue(all(modules.values()), 'Each module naming capture fields needs its reviewed role')
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            module = root / 'unreviewed.py'
            for code in ("source['capture_method']", "dict(capture_method='http')",
                         'source.update(origin_verified=True)', 'source.origin_verified', 'capture_method'):
                with self.subTest(code=code):
                    module.write_text(code)
                    self.assertEqual(self.find_capture_field_modules(root), {'unreviewed.py': None})


if __name__ == '__main__':
    unittest.main()
