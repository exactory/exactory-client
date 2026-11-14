"""Current exports follow their source records; historical bytes and posted identities survive."""

import copy
import contextlib
import hashlib
import io
import json
from pathlib import Path
from unittest.mock import patch

from research_harness import cli, problem_claims, problem_delivery
from research_harness.output_views import _hold_lock, render_all, render_workspace_views
import test_gate_override as override_fixtures
from test_research_problem_claims import ProblemCase
from test_research_problem_delivery import ProblemDeliveryCase


class OverrideExportHistoryTests(override_fixtures.OverrideCommandMixin, ProblemCase):
    prepare_problem = override_fixtures.ProblemOverrideTests.prepare_problem

    def setUp(self):
        super().setUp()
        self.initialize_problem()
        self.write_instruction()
        self.statement = self.prepare_problem()
        self.record_override(self.build_override(gate='post'))

    def export(self, destination=None):
        return Path(problem_delivery.export_problem_override(self.store, destination)['destination'])

    def change_value(self, text=None, identifier='value-after-export'):
        value = copy.deepcopy(problem_claims.find_value(self.store.snapshot()['records']))
        value.pop('recorded_revision')
        value['id'] = identifier
        if text is not None:
            value['current_state'][0]['text'] = text
        response = self.invoke_command('problem-value', value, check=False, request=identifier)
        self.assertEqual(response[0], 0, response[2])

    def test_changed_value_archives_the_first_export_and_post_names_the_second(self):
        first = self.export()
        original = {path.name: path.read_bytes() for path in first.iterdir()}
        (first / '.DS_Store').write_bytes(b'finder metadata')
        self.change_value('The record also excludes the first candidate formulation.')
        historical = self.root / 'final/history/problem/exports' / first.name
        self.assertFalse(first.exists())
        self.assertEqual({name: (historical / name).read_bytes() for name in original}, original)
        self.assertEqual((historical / '.DS_Store').read_bytes(), b'finder metadata')
        second = self.export()
        self.assertNotEqual(first, second)
        self.assertEqual(list(second.parent.iterdir()), [second])
        index = json.loads((self.root / 'final/index.json').read_text())
        self.assertIn({'path': 'history/problem/exports/' + first.name, 'type': 'export'}, index['files'])
        self.assertIn({'path': 'problem/exports/' + second.name, 'type': 'export'}, index['files'])
        self.assertFalse(index['unowned'])
        post = self.mutate(problem_claims.record_post_external, dict(
            id='posted-export', problem_id='656c336e-e892-4c47-80d2-a71d022f4116',
            statement_digest=problem_claims.compute_statement_digest(self.statement),
            export_digest=second.name, reason='The owner ordered this post.', evidence='The server accepted the post.'))['result']
        self.assertEqual(cli.status_report(self.store)['post']['export_digest'], second.name)
        self.assertEqual(post['gate'], 'bypassed')
        render_all(self.store)
        self.assertTrue(second.is_dir())
        self.assertTrue(historical.is_dir())

    def test_new_source_identity_retires_an_export_even_when_its_text_is_unchanged(self):
        first = self.export()
        self.change_value()
        self.assertFalse(first.exists())
        historical = self.root / 'final/history/problem/exports' / first.name
        self.assertTrue(historical.is_dir())
        self.assertEqual(self.export(), first)
        self.assertTrue(historical.is_dir())

    def test_a_recreated_equal_export_can_be_retired_again(self):
        first = self.export()
        self.change_value()
        self.assertEqual(self.export(), first)
        self.change_value(identifier='third-value')
        self.assertFalse(first.exists())
        history = self.root / 'final/history/problem/exports'
        self.assertEqual(len(list(history.iterdir())), 2)
        self.assertTrue(all((path / 'state.md').read_text() == 'Only bounded examples have been established.\n'
                            for path in history.iterdir()))

    def test_source_change_during_export_returns_its_retained_history_destination(self):
        original = problem_delivery.write_problem_export

        def change_before_write(*args):
            self.change_value('A newly recorded current state.')
            return original(*args)

        with patch.object(problem_delivery, 'write_problem_export', side_effect=change_before_write):
            directory = self.export()
        self.assertTrue(directory.is_dir())
        self.assertEqual(directory.parent, self.store.root / 'final/history/problem/exports')
        self.assertEqual((directory / 'state.md').read_text(), 'Only bounded examples have been established.\n')
        self.assertFalse(list((self.root / 'final/problem/exports').iterdir()))

    def test_busy_renderer_refuses_export_before_writing_its_file_set(self):
        with _hold_lock(self.store.root), contextlib.redirect_stderr(io.StringIO()):
            self.assert_error('output_view_render_deferred', self.export)
        self.assertFalse((self.root / 'final/problem/exports').exists())

    def test_old_equal_snapshot_does_not_replace_the_newer_exports_source_receipt(self):
        original = problem_delivery.write_problem_export
        newer = []

        def write_newer_first(*args):
            self.change_value()
            with patch.object(problem_delivery, 'write_problem_export', original):
                newer.append(self.export())
            receipts = list((self.root / '.exactory/problem-export-sources').glob('*.json'))
            self.assertEqual(len(receipts), 1)
            newer.append(receipts[0].read_bytes())
            return original(*args)

        with patch.object(problem_delivery, 'write_problem_export', side_effect=write_newer_first):
            directory = self.export()
        self.assertEqual(directory, newer[0])
        self.assertTrue(directory.is_dir())
        self.assertEqual(directory.parent, self.store.root / 'final/problem/exports')
        self.assertFalse((self.root / 'final/history/problem/exports').exists())
        receipt, = (self.root / '.exactory/problem-export-sources').glob('*.json')
        self.assertEqual(receipt.read_bytes(), newer[1])

    def test_renderer_never_moves_an_export_through_a_symlinked_parent(self):
        first = self.export()
        self.assertEqual(len(list((self.root / '.exactory/problem-export-sources').glob('*.json'))), 1)
        outside = self.root.parent / 'outside-problem'
        (self.root / 'final/problem').rename(outside)
        (self.root / 'final/problem').symlink_to(outside, target_is_directory=True)
        value = copy.deepcopy(problem_claims.find_value(self.store.snapshot()['records']))
        value.pop('recorded_revision')
        value['id'] = 'later-value'
        value['current_state'][0]['text'] = 'The source record has changed.'
        self.mutate(problem_claims.record_value, value)
        with contextlib.redirect_stderr(io.StringIO()):
            render_workspace_views(self.store.root)
        self.assertTrue((outside / 'exports' / first.name).is_dir())
        self.assertFalse((self.root / 'final/history/problem/exports' / first.name).exists())

    def test_default_export_refuses_a_symlinked_exports_folder_before_writing(self):
        first = self.export()
        outside = self.root.parent / 'outside-exports'
        first.parent.rename(outside)
        first.parent.symlink_to(outside, target_is_directory=True)
        self.change_value('A different current state needs a new export.')
        before = {str(path.relative_to(outside)): path.read_bytes()
                  for path in outside.rglob('*') if path.is_file()}
        self.assert_error('unsafe_path', self.export)
        self.assertEqual({str(path.relative_to(outside)): path.read_bytes()
                          for path in outside.rglob('*') if path.is_file()}, before)

    def test_default_export_keeps_its_workspace_anchor_when_a_parent_changes_before_writing(self):
        from research_harness import workspace
        self.export()
        self.change_value('A different current state needs a new export.')
        exports = self.store.root / 'final/problem/exports'
        outside = self.root.parent / 'outside-exports'
        outside.mkdir()
        original = workspace.write_projection
        swapped = []

        def swap_before_write(root, relative, data, **options):
            if not swapped and (Path(root).parent == exports or
                    Path(root) == self.store.root and str(relative).startswith('final/problem/exports/')):
                exports.rename(self.root / 'old-exports')
                exports.symlink_to(outside, target_is_directory=True)
                swapped.append(True)
            return original(root, relative, data, **options)

        with patch.object(workspace, 'write_projection', side_effect=swap_before_write):
            self.assert_error('unsafe_path', self.export)
        self.assertEqual(swapped, [True])
        self.assertEqual(list(outside.iterdir()), [])

    def test_unrelated_records_keep_current_exports_and_foreign_files_in_place(self):
        first = self.export(self.root / 'final/problem/exports/custom/post')
        original = {path.name: (path.read_bytes(), path.stat().st_mtime_ns) for path in first.iterdir()}
        foreign = self.root / 'final/problem/exports/author-notes'
        foreign.mkdir()
        (foreign / 'notes.txt').write_text('Author-owned notes.')
        for name, manifest in [('incomplete', dict(statement_id=self.statement['id'], override_id='override-1',
                                                  gate='bypassed', files={})),
                               ('malformed', dict(statement_id=[], files={}))]:
            directory = first.parent.parent / name
            directory.mkdir()
            (directory / 'problem-export.json').write_text(json.dumps(manifest))
        self.put('unrelated', 'note', {'text': 'No exported source changed.'})
        render_all(self.store)
        self.assertEqual(self.export(first), first)
        self.assertEqual({path.name: (path.read_bytes(), path.stat().st_mtime_ns) for path in first.iterdir()}, original)
        self.assertEqual((foreign / 'notes.txt').read_text(), 'Author-owned notes.')
        self.assertFalse((self.root / 'final/history/problem/exports').exists())
        self.assertEqual(len(list((self.root / '.exactory/problem-export-sources').glob('*.json'))), 1)
        marker = json.loads((self.root / '.exactory/output-views.json').read_text())
        self.assertEqual(marker['errors'], {})

    def test_forming_imports_are_present_in_the_renderers_export_source_snapshot(self):
        import test_problem_forming_import as forming_fixtures
        fixture = forming_fixtures.FormingImportTests()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        fixture.import_history(destination=self)
        directory = self.export()
        self.assertEqual(len(list((self.root / '.exactory/problem-export-sources').glob('*.json'))), 1)
        self.assertTrue(directory.is_dir())
        self.assertEqual(directory.parent, self.store.root / 'final/problem/exports')
        render_all(self.store)
        self.assertTrue(directory.is_dir())

    def test_history_conflict_preserves_both_folders_and_reports_the_view_error(self):
        first = self.export()
        historical = self.root / 'final/history/problem/exports' / first.name
        historical.mkdir(parents=True)
        (historical / 'notes.txt').write_text('Do not replace this folder.')
        self.change_value('A changed source record.')
        self.assertTrue(first.is_dir())
        self.assertEqual((historical / 'notes.txt').read_text(), 'Do not replace this folder.')
        marker = json.loads((self.root / '.exactory/output-views.json').read_text())
        self.assertEqual(marker['errors'].get('final/problem/exports/' + first.name), 'output_view_conflict')

    def test_0853_override_export_keeps_bytes_and_digest_then_retires_on_change(self):
        fixture = Path(__file__).parent / 'fixtures/problem_override_export_0_85_3.py.txt'
        self.assertEqual(hashlib.sha256(fixture.read_bytes()).hexdigest(),
                         '16d9ba45a6139f75ff025b29ea9647718a9651f4f4f45fce6495033892c45551')
        namespace = vars(problem_delivery).copy()
        namespace['write_problem_export'] = lambda store, files, manifest, destination: (files, manifest)
        exec(compile(fixture.read_bytes(), str(fixture), 'exec'), namespace)
        legacy_files, legacy_manifest = namespace['export_problem_override'](self.store)
        directory = self.root / 'final/problem/exports/legacy-export'
        directory.mkdir(parents=True)
        for name, data in legacy_files.items():
            (directory / name).write_bytes(data)
        legacy_digest = problem_delivery.compute_export_digest(legacy_files)
        render_all(self.store)
        self.assertEqual({path.name: path.read_bytes() for path in directory.iterdir()}, legacy_files)
        self.assertEqual(json.loads((directory / 'problem-export.json').read_text()), legacy_manifest)
        self.assertEqual(problem_delivery.compute_export_digest(
            {path.name: path.read_bytes() for path in directory.iterdir()}), legacy_digest)
        self.change_value('The changed value retires this legacy export.')
        self.assertFalse(directory.exists())
        archived = self.root / 'final/history/problem/exports/legacy-export'
        self.assertEqual({path.name: path.read_bytes() for path in archived.iterdir()}, legacy_files)

    def test_stored_legacy_and_new_v2_override_exports_retire_on_source_identity_change(self):
        fixture = Path(__file__).parent / 'fixtures/problem_override_export_0_85_3.py.txt'
        namespace = vars(problem_delivery).copy()
        namespace['write_problem_export'] = lambda store, files, manifest, destination: (files, manifest)
        exec(compile(fixture.read_bytes(), str(fixture), 'exec'), namespace)
        legacy_files, _ = namespace['export_problem_override'](self.store)
        legacy = self.root / 'final/problem/exports/legacy-export'
        legacy.mkdir(parents=True)
        for name, data in legacy_files.items():
            (legacy / name).write_bytes(data)
        current = self.export()
        manifest = json.loads((current / 'problem-export.json').read_bytes())
        self.assertEqual(manifest['schema'], 'exactory-problem-export-v2')
        self.assertEqual(json.loads((current / 'citation-refs.json').read_bytes()), [{
            'referenceString': 'A. Researcher. An authored example',
            'bibliography': {'arxivId': '2601.00001v1', 'title': 'An authored example',
                             'authors': ['A. Researcher']}}])
        current_files = {path.name: path.read_bytes() for path in current.iterdir()}
        render_all(self.store)
        self.assertTrue(legacy.is_dir())
        self.assertEqual({path.name: path.read_bytes() for path in legacy.iterdir()}, legacy_files)
        self.assertEqual({path.name: path.read_bytes() for path in current.iterdir()}, current_files)
        self.assertEqual(problem_delivery.compute_export_digest(current_files), current.name)
        self.change_value()
        for directory, files in [(legacy, legacy_files), (current, current_files)]:
            self.assertFalse(directory.exists())
            historical = self.root / 'final/history/problem/exports' / directory.name
            retained = {path.name: path.read_bytes() for path in historical.iterdir()}
            self.assertEqual(retained, files)
            self.assertEqual(problem_delivery.compute_export_digest(retained),
                             problem_delivery.compute_export_digest(files))

    def test_retirement_leaves_unknown_or_incomplete_v2_exports_in_place(self):
        current = self.export()
        original = {path.name: path.read_bytes() for path in current.iterdir()}
        altered = []
        for kind in ['unknown-schema', 'unversioned-refs', 'missing-refs', 'changed-refs', 'extra-file']:
            files = dict(original)
            manifest = json.loads(files['problem-export.json'])
            if kind == 'unknown-schema':
                manifest['schema'] = 'unsupported-export-schema'
            elif kind == 'unversioned-refs':
                del manifest['schema']
            elif kind == 'missing-refs':
                del manifest['files']['citation-refs.json']
                del files['citation-refs.json']
            elif kind == 'changed-refs':
                files['citation-refs.json'] = b'[]\n'
            else:
                files['notes.txt'] = b'Author-owned notes.\n'
                manifest['files']['notes.txt'] = hashlib.sha256(files['notes.txt']).hexdigest()
            files['problem-export.json'] = (json.dumps(manifest, sort_keys=True, indent=2) + '\n').encode()
            directory = current.parent / kind
            directory.mkdir()
            for name, data in files.items():
                (directory / name).write_bytes(data)
            altered.append((directory, files))
        self.change_value('A changed value retires only the complete supported export.')
        self.assertFalse(current.exists())
        historical = self.root / 'final/history/problem/exports' / current.name
        self.assertEqual({path.name: path.read_bytes() for path in historical.iterdir()}, original)
        for directory, files in altered:
            with self.subTest(export=directory.name):
                self.assertEqual({path.name: path.read_bytes() for path in directory.iterdir()}, files)
                self.assertFalse((historical.parent / directory.name).exists())


class ReviewedExportHistoryTests(ProblemDeliveryCase):
    def test_stored_legacy_and_new_v2_reviewed_exports_retire_without_changing_bytes(self):
        self.prepare_ready()
        self.record_post_decision()
        records = self.store.snapshot()['records']
        legacy_files, _ = problem_delivery.build_projection(records, self.artifacts)
        legacy = self.root / 'final/problem/exports/legacy-export'
        legacy.mkdir(parents=True)
        for name, data in legacy_files.items():
            (legacy / name).write_bytes(data)
        current = Path(problem_delivery.export_problem(self.store)['destination'])
        current_files = {path.name: path.read_bytes() for path in current.iterdir()}
        self.assertEqual(json.loads(current_files['problem-export.json'])['schema'], 'exactory-problem-export-v2')
        self.assertIn('citation-refs.json', current_files)
        render_all(self.store)
        self.assertEqual({path.name: path.read_bytes() for path in legacy.iterdir()}, legacy_files)
        self.assertEqual({path.name: path.read_bytes() for path in current.iterdir()}, current_files)
        value = copy.deepcopy(problem_claims.find_value(records))
        value.pop('recorded_revision')
        value['id'] = 'later-value'
        value['current_state'][0]['text'] = 'A later source changes the current state.'
        self.mutate(problem_claims.record_value, value)
        render_all(self.store)
        for directory, files in [(legacy, legacy_files), (current, current_files)]:
            self.assertFalse(directory.exists())
            historical = self.root / 'final/history/problem/exports' / directory.name
            self.assertEqual({path.name: path.read_bytes() for path in historical.iterdir()}, files)

    def test_changed_evidence_retires_an_approved_export_without_changing_its_bytes(self):
        self.prepare_ready()
        self.record_post_decision()
        first = Path(problem_delivery.export_problem(self.store)['destination'])
        original = {path.name: path.read_bytes() for path in first.iterdir()}
        value = copy.deepcopy(problem_claims.find_value(self.store.snapshot()['records']))
        value.pop('recorded_revision')
        value['id'] = 'revised-value'
        value['current_state'][0]['text'] = 'A later source changes the current state.'
        self.mutate(problem_claims.record_value, value)
        render_all(self.store)
        self.assertFalse(first.exists())
        historical = self.root / 'final/history/problem/exports' / first.name
        self.assertEqual({path.name: path.read_bytes() for path in historical.iterdir()}, original)
