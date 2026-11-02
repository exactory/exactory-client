"""Compact hook reads, deferred Stop views and serialized render publication."""

import contextlib
import fcntl
import io
import json
import os
from pathlib import Path
import tempfile
import threading
import unittest
from unittest import mock

import attack
from search_controller import model, output_views, render, service, strategy_refresh, strategy_refresh_io
from search_controller.errors import SearchError
from search_controller.storage import canonical_bytes
from tests.search_fixtures import contract, digest, proposal, provenance, review


class HookBoundaryTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve() / 'attack'
        self.root.mkdir()
        self.controller = service.Controller(self.root)
        self.controller.store.initialize(contract(), 'objective-000001')

    def read_state(self):
        return model.replay(self.controller.store.read())

    def invoke(self, command, spec=None, request=None, **kwargs):
        revision = self.read_state()['revision']
        return self.controller.command(command, spec or {}, revision, request or 'request-' + str(revision), **kwargs)

    def test_guard_status_is_closed_read_only_and_does_no_advisory_work(self):
        self.invoke('pause', {'reason': 'Fixture pause'})
        state = self.read_state()
        before = {path.relative_to(self.root): (path.read_bytes(), path.stat().st_mtime_ns)
                  for path in self.root.rglob('*') if path.is_file()}
        with contextlib.ExitStack() as stack:
            # Import the consumer before patching its producer, so its imported aliases remain real.
            stack.enter_context(mock.patch.object(strategy_refresh_io, 'describe_context',
                                                  side_effect=AssertionError('Strategy context derivation')))
            for target in ('search_controller.service.Controller.status', 'search_controller.service.audit_state',
                           'search_controller.execution.process_observations',
                           'search_controller.strategy_refresh.assessment_status',
                           'search_controller.output_views.find_stale_views',
                           'search_controller.output_views.render_after_command', 'search_controller.render.render'):
                stack.enter_context(mock.patch(target, side_effect=AssertionError(target)))
            result = self.controller.command('guard-status', {
                'objective_id': state['objective_id'], 'contract_digest': state['contract_digest']}, None, None)
        self.assertEqual(result, {'protocol': 'exactory-search-guard-status-v1',
            'objective_id': state['objective_id'], 'contract_digest': state['contract_digest'],
            'revision': state['revision']})
        after = {path.relative_to(self.root): (path.read_bytes(), path.stat().st_mtime_ns)
                 for path in self.root.rglob('*') if path.is_file()}
        self.assertEqual(after, before)

    def test_guard_status_validates_identity_and_replays_every_event(self):
        with self.assertRaises(SearchError) as caught:
            self.controller.command('guard-status', {'objective_id': 'wrong'}, None, None)
        self.assertEqual(caught.exception.code, 'discovery_conflict')
        self.invoke('pause', {'reason': 'Fixture pause'})
        document = self.controller.store.read()
        document['events'][0]['payload']['operations'][0]['kind'] = 'unknown_operation'
        self.controller.store.tree_path.write_bytes(canonical_bytes(document))
        with self.assertRaises(SearchError) as caught:
            self.controller.command('guard-status', {}, None, None)
        self.assertNotEqual(caught.exception.code, 'invalid_input')

    def test_guard_status_cli_accepts_identity_without_mutation_arguments(self):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = attack.main(['--attack-root', str(self.root), 'search', 'guard-status', '--json',
                                '--objective-id', 'objective-000001', '--contract-digest', digest(contract())])
        self.assertEqual(code, 0, err.getvalue())
        self.assertEqual(json.loads(out.getvalue()), {'protocol': 'exactory-search-guard-status-v1',
            'objective_id': 'objective-000001', 'contract_digest': digest(contract()), 'revision': 0})

    def test_markdown_construction_and_publication_leave_writer_lock_available(self):
        observed = []
        original_status, original_write = strategy_refresh.assessment_status, render.replace_text

        def check_lock():
            descriptor = os.open(str(self.controller.store.root / 'tree.lock'), os.O_RDWR | os.O_CREAT, 0o600)
            try:
                try:
                    fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    observed.append(True)
                except OSError:
                    observed.append(False)
            finally:
                os.close(descriptor)

        def build(*args, **kwargs):
            check_lock()
            return original_status(*args, **kwargs)

        def publish(*args):
            check_lock()
            return original_write(*args)

        with mock.patch.object(strategy_refresh, 'assessment_status', side_effect=build), \
                mock.patch.object(render, 'replace_text', side_effect=publish):
            self.invoke('pause', {'reason': 'Fixture pause'})
        self.assertGreaterEqual(len(observed), 2)
        self.assertTrue(all(observed), 'Markdown was constructed or published under the writer lock')

    def test_old_supplied_snapshot_cannot_overwrite_a_completed_newer_render(self):
        self.invoke('pause', {'reason': 'First pause'})
        document = self.controller.store.read()
        old = (document, model.replay(document))
        self.invoke('render')
        latest = self.read_state()['revision']
        written = []
        original = render.render

        def capture(root, state):
            written.append(state['revision'])
            return original(root, state)

        with mock.patch.object(render, 'render', side_effect=capture):
            output_views.render_views(self.root, self.controller.store, old)
        self.assertEqual(written, [latest], 'Even a current JSON marker must repair Markdown from current state')
        self.assertEqual(output_views.read_marker(self.controller.store)['revision'], latest)

    def test_competing_renderers_select_and_publish_in_render_lock_order(self):
        self.invoke('pause', {'reason': 'First pause'})
        old_document = self.controller.store.read()
        old = (old_document, model.replay(old_document))
        entered, release = threading.Event(), threading.Event()
        revisions, errors = [], []
        original = render.render

        def publish(root, state):
            revisions.append(state['revision'])
            if len(revisions) == 1:
                entered.set()
                if not release.wait(10):
                    raise AssertionError('Test did not release first renderer')
            return original(root, state)

        def run():
            try:
                output_views.render_views(self.root, self.controller.store, old)
            except Exception as error:
                errors.append(error)

        with mock.patch.object(render, 'render', side_effect=publish):
            first = threading.Thread(target=run)
            first.start()
            try:
                self.assertTrue(entered.wait(5), 'Markdown never entered the render-lock boundary')
                self.controller.store.append('control_recorded', {'action': 'pause', 'reason': 'Later pause'},
                                             old[1]['revision'], 'later-pause')
                second = threading.Thread(target=run)
                second.start()
            finally:
                release.set()
                first.join(10)
            if 'second' in locals():
                second.join(10)
                self.assertFalse(second.is_alive())
        self.assertFalse(first.is_alive())
        self.assertEqual(errors, [])
        self.assertEqual(revisions, [old[1]['revision'], old[1]['revision'] + 1])
        self.assertEqual(output_views.read_marker(self.controller.store)['revision'], revisions[-1])

    def test_current_marker_and_reserved_name_do_not_suppress_markdown_repair(self):
        for name in ('attack', output_views.RESERVED_ROOT_NAME):
            with self.subTest(name=name):
                root = self.root.parent / name
                root.mkdir(exist_ok=True)
                controller = self.controller if name == 'attack' else service.Controller(root)
                if name != 'attack':
                    controller.store.initialize(contract(), 'objective-000001')
                controller.command('pause', {'reason': 'Fixture pause'}, 0, 'pause')
                markdown = root / 'SEARCH_TREE.md'
                expected = markdown.read_bytes()
                markdown.write_bytes(b'Interrupted Markdown publication')
                output_views.render_views(root, controller.store)
                self.assertEqual(markdown.read_bytes(), expected)

    def test_current_marker_does_not_suppress_initial_generated_markdown_repair(self):
        for name in ('attack', output_views.RESERVED_ROOT_NAME):
            with self.subTest(name=name):
                root = self.root.parent / 'fresh-repair' / name
                root.mkdir(parents=True)
                controller = service.Controller(root)
                controller.command('init', {'contract': contract()}, 0, 'init')
                markdown = root / 'SEARCH_TREE.md'
                expected = markdown.read_bytes()
                markdown.write_bytes(b'Interrupted initial Markdown publication')
                output_views.render_views(root, controller.store)
                self.assertEqual(markdown.read_bytes(), expected)

    def test_unrecovered_effects_cannot_publish_a_current_marker(self):
        self.invoke('pause', {'reason': 'First pause'})
        snapshot = {'directories': [], 'files': []}
        effect = {'kind': 'initialize_workspace', 'slug': 'attempt',
                  'snapshot_digest': self.controller.store.put_blob(snapshot)}
        self.controller.store.append_operation({'command': 'render', 'target': None, 'spec_digest': digest({})},
            1, 'unrecovered', lambda document, content: {'command': 'render', 'target': None,
                'spec_digest': digest({}), 'operations': [], 'effects': [effect]},
            lambda document, event: model.apply_event(model.replay(document), event))
        before = (self.root / 'SEARCH_TREE.md').read_bytes()
        with contextlib.redirect_stderr(io.StringIO()):
            output_views.render_after_command(self.controller, research=False)
        self.assertLess(output_views.read_marker(self.controller.store)['revision'], self.read_state()['revision'])
        self.assertEqual((self.root / 'SEARCH_TREE.md').read_bytes(), before)
        self.invoke('render')
        self.assertEqual(output_views.read_marker(self.controller.store)['revision'], self.read_state()['revision'])

    def test_early_ownership_claim_survives_interrupted_publication(self):
        original = output_views.replace_bytes

        def fail_view(path, data):
            if 'final' in path.parts:
                raise OSError('Interrupted publication')
            return original(path, data)

        with mock.patch.object(output_views, 'replace_bytes', side_effect=fail_view), \
                contextlib.redirect_stderr(io.StringIO()):
            self.invoke('pause', {'reason': 'Fixture pause'})
        marker = output_views.read_marker(self.controller.store)
        self.assertEqual(marker['revision'], 0)
        self.assertIsNone(marker['files']['final/math/attack/objective.json'])
        self.invoke('render')
        marker = output_views.read_marker(self.controller.store)
        self.assertEqual(marker['revision'], self.read_state()['revision'])
        self.assertIsInstance(marker['files']['final/math/attack/objective.json'], dict)

    def test_retry_after_markdown_failure_clears_failure_at_the_same_revision(self):
        self.invoke('pause', {'reason': 'Fixture pause'})
        with mock.patch.object(render, 'render', side_effect=OSError('Interrupted Markdown repair')), \
                contextlib.redirect_stderr(io.StringIO()):
            output_views.render_after_command(self.controller, research=False)
        self.assertIsNotNone(output_views.read_marker(self.controller.store)['failure'])
        output_views.render_views(self.root, self.controller.store)
        self.assertIsNone(output_views.read_marker(self.controller.store)['failure'])
        self.assertNotIn('advisories', self.controller.command('status', {}, None, None))

    def test_initial_manual_markdown_is_preserved_and_render_failure_is_advisory(self):
        manual = self.root / 'SEARCH_TREE.md'
        manual.write_text('Manual initial tree\n')
        # Initialization uses a fresh controller root, rather than the historical fixture above.
        other = service.Controller(self.root.parent / 'fresh')
        other.root.mkdir()
        (other.root / 'SEARCH_TREE.md').write_bytes(manual.read_bytes())
        other.command('init', {'contract': contract()}, 0, 'init')
        self.assertEqual((other.root / 'SEARCH_TREE.md').read_bytes(), manual.read_bytes())
        with mock.patch.object(render, 'replace_text', side_effect=OSError('Markdown disk full')), \
                contextlib.redirect_stderr(io.StringIO()) as err:
            response = self.invoke('pause', {'reason': 'Fixture pause'})
        self.assertEqual(response['revision'], 1)
        self.assertIn('Markdown disk full', err.getvalue())
        self.assertIn('advisories', self.controller.command('status', {}, None, None))

    def test_internal_stop_defers_all_advisory_rendering_and_render_repairs_it(self):
        candidate = proposal()
        approval = review(candidate)
        for kind, payload in (
            ('proposal_recorded', {'proposal': candidate, 'digest': digest(candidate)}),
            ('review_recorded', {'proposal_id': 'proposal-000001', 'review': approval, 'digest': digest(approval)}),
            ('proposal_admitted', {'proposal_id': 'proposal-000001'}),
        ):
            revision = self.read_state()['revision']
            self.controller.store.append(kind, payload, revision, 'historical-' + str(revision))
        workspace = self.root / candidate['attack_slug']
        unit = workspace / 'units/1'
        unit.mkdir(parents=True)
        (workspace / 'problem.json').write_text(json.dumps(attack.build_problem_skeleton()))
        (workspace / 'journal.jsonl').write_bytes(b'')
        (workspace / 'openings.json').write_text('{"openings":[]}')
        (workspace / 'units/INVENTORY.md').write_text('Fixture inventory')
        (unit / 'unit.json').write_text('{}')
        import hashlib
        (unit / 'check-unit.json').write_text(json.dumps({'unit_sha256': hashlib.sha256(b'{}').hexdigest()}))
        self.invoke('focus', {'focus': 'focused', 'session_id': 'codex:fixture', 'provenance': provenance('operator')},
                    request='focus', workspace_root=self.root.parent)
        marker = output_views.read_marker(self.controller.store)
        validation = {'session_id': 'codex:fixture', 'focus_request_id': 'focus',
                      'objective_id': 'objective-000001', 'contract_digest': digest(contract())}
        spec = {'delivery_id': 'delivery', 'session_id': 'codex:fixture', 'turn_id': None, 'stop_hook_active': False}
        with mock.patch.object(render, 'replace_text', side_effect=AssertionError('Stop Markdown')), \
                mock.patch.object(output_views, 'render_after_command') as advisory:
            first = self.invoke('hook-stop', spec, request='stop-1', hook_session=validation)
            (unit / 'draft.md').write_text('Updated direct draft')
            second = self.invoke('hook-stop', spec, request='stop-2', hook_session=validation)
            (unit / 'evaluation.md').write_text('Updated direct evaluation')
            third = self.invoke('hook-stop', spec, request='stop-3', hook_session=validation)
        advisory.assert_not_called()
        for result, step in ((first, 'draft'), (second, 'evaluation'), (third, 'finish')):
            self.assertEqual(result['decision']['action']['step'], step)
        self.assertEqual(self.read_state()['control']['stop_count'], 1)
        self.assertEqual(output_views.read_marker(self.controller.store), marker)
        [stale] = self.controller.command('status', {}, None, None)['advisories']
        self.assertEqual((stale['reason'], stale['action']), ('behind', 'exactory-math search render'))
        self.invoke('render')
        self.assertNotIn('advisories', self.controller.command('status', {}, None, None))

    def test_fresh_response_reuses_current_replay_but_retry_returns_original_response(self):
        original = service.replay
        sizes = []

        def replay(document):
            sizes.append(len(document['events']))
            return original(document)

        with mock.patch.object(service, 'replay', side_effect=replay), \
                mock.patch.object(output_views, 'render_after_command'):
            first = self.controller.command('pause', {'reason': 'First pause'}, 0, 'first')
        self.assertEqual(sizes.count(1), 1, 'A fresh response replayed its complete history again')
        self.invoke('render', request='later')
        retry = self.controller.command('pause', {'reason': 'First pause'}, 999, 'first')
        self.assertEqual(retry, first)


if __name__ == '__main__':
    unittest.main()
