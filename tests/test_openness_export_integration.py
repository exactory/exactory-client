"""Registry citations retain supplied provenance and override authority together."""

import json
import unittest

from research_harness import acquisition, problem_delivery, problem_export_history
import test_registry_citation_names as registry_fixtures
import test_supplied_eligibility as supplied_fixtures


class ExportIntegrationTests(unittest.TestCase):
    def test_registry_exports_keep_supplied_origin_and_override_authority(self):
        case = registry_fixtures.RegistryCitationExportTests()
        case.setUp()
        self.addCleanup(case.doCleanups)
        version, capture, _ = supplied_fixtures.SuppliedCase.import_text(case)
        case.items = [dict(kind='reading', version_id=version, reading_id='supplied-reading')]
        case.record_value()
        acquisition.import_response(case.store, 'crossref', json.dumps({'message': {
            'DOI': version[4:], 'title': ['Registered survey title'],
            'author': [{'name': 'Registry Author'}]}}).encode(),
            source_url='https://api.crossref.org/works/' + version[4:],
            captured_at='2026-11-13T12:00:00Z', expected_revision=case.store.revision,
            request_id='registry-supplied-survey')
        manifest, directory, citations = case.export_citations()
        self.assertEqual(citations, [dict(citation='Registry Author. Registered survey title', locator=version[4:])])
        self.assertEqual(manifest['schema'], 'exactory-problem-export-v4')
        self.assertTrue(manifest['authority']['override_ids'])
        labels = manifest['supplied_texts']
        self.assertEqual(labels[0]['original_sha256'], capture['original']['sha256'])
        self.assertIn('note', labels[0]['supplied'])
        files = {path.name: path.read_bytes() for path in directory.iterdir()}
        records = case.store.snapshot()['records']
        self.assertEqual(problem_export_history.read_problem_export(directory, records),
                         (json.loads(files['problem-export.json']), files))
        current = problem_export_history.build_current_export(records, case.artifacts, True,
            include_references=True, citation_spec=manifest['citation_spec'],
            authority=manifest['authority'], use_registry=True)
        self.assertEqual(current, files)
        case.put('problem_decision', 'post', dict(id='post', action='post', review_ids=[], recorded_revision=1))
        records = case.store.snapshot()['records']
        ordinary, ordinary_manifest = problem_delivery.build_projection(records, case.artifacts, use_registry=True)
        self.assertEqual(ordinary_manifest['supplied_texts'], labels)
        self.assertEqual(ordinary['citations.json'], files['citations.json'])
