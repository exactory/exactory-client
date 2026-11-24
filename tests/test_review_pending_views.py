"""Reviewer calls publish their start without waiting for a busy view builder."""

from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager, redirect_stderr, redirect_stdout
import copy
import io
import json
import shutil
import threading
import unittest
from unittest.mock import patch

from research_harness import cli, output_views as views, review_protocol as protocol
from research_harness.errors import ResearchError
from research_harness.storage import Store
import test_review_continuation as continuation_fixtures


class PendingViewTests(unittest.TestCase):
    def setUp(self):
        from research_harness import bar_sources, review_protocol
        released = patch.object(review_protocol, 'NEW_SESSION_RULES', (bar_sources.PROTOCOL_V3,))
        released.start()
        self.addCleanup(released.stop)
        fixture = continuation_fixtures.ContinuationTests()
        self.addCleanup(fixture.doCleanups)
        fixture.setUp()
        self.case, self.assignment = fixture.case, fixture.assignment
        self.store, self.root = self.case.store, self.case.root
        self.folder = self.root / 'intermediate/reviews/bar/bar-first'
        self.payload = dict(id='pending-run', assignment_id=self.assignment['id'])
        self.request_id = 'pending-command'
        self.response = self.case.build_provider_response(self.case.build_review_output('bar'))
        self.pool = ThreadPoolExecutor(max_workers=2)
        self.addCleanup(self.pool.shutdown)
        # The command-end render must also defer while an event holds the builder.
        guard = patch.object(views, '_RENDER_LOCK_SECONDS', 0)
        guard.start()
        self.addCleanup(guard.stop)
        views.render_workspace_views(self.root)

    def make_event(self):
        event = threading.Event()
        self.addCleanup(event.set)
        return event

    def await_event(self, event):
        self.assertTrue(event.wait(30), 'The controlled renderer or transport did not reach its boundary')

    def read_series(self):
        return json.loads((self.folder / 'series.json').read_bytes())

    def read_runs(self):
        return self.read_series()['turns'][-1]['runs']

    def read_published(self):
        return {str(path.relative_to(self.root)): path.read_bytes()
                for folder in ('final', 'intermediate') for path in (self.root / folder).rglob('*')
                if path.is_file()}

    def assert_pending(self):
        runs = self.read_runs()
        self.assertEqual([(run['run_id'], run['status']) for run in runs], [('pending-run', 'pending')])
        self.assertIn('pending-run', self.case.read_records()['review_invocation'])
        self.assertNotIn('pending-run', self.case.read_records().get('review_attempt', {}))

    def assert_repaired(self):
        self.assertEqual(self.read_runs()[0]['status'], 'completed')
        self.assertEqual(views.read_rendered_revision(self.store), self.store.revision)
        self.assertFalse((self.root / views.DIRTY_PATH).exists())
        self.assertFalse((self.root / views._RENDERING_PATH).exists())
        self.assertEqual(self.read_advisories(), [])
        # Every file of the series, including per-turn and output files, is current.
        workspace = views.find_workspace_root(self.root)
        inputs, _ = views._read_inputs(self.root, workspace)
        model = views.compute_view_model(self.case.read_records(), self.case.artifacts, inputs)
        for path, view in model.items():
            if path.startswith('intermediate/reviews/bar/bar-first/'):
                content = view.content if isinstance(view.content, bytes) else self.case.artifacts.read(view.content)
                self.assertEqual((self.root / path).read_bytes(), content, path)

    def read_advisories(self):
        return [row for row in cli.status_report(self.store)['advisories'] if row['code'] == 'output_views_stale']

    def run_command(self, send=None):
        path = self.root / 'pending-command.json'
        path.write_text(json.dumps(self.payload), encoding='utf-8')
        output, errors = io.StringIO(), io.StringIO()
        with patch.object(protocol.transport, 'send_request', side_effect=send or (lambda *args: self.response)), \
                redirect_stdout(output), redirect_stderr(errors):
            cli.main(['review-run', '--workspace', str(self.root), '--file', str(path),
                      '--expected-revision', str(self.store.revision), '--request-id', self.request_id])
        return json.loads(output.getvalue()), errors.getvalue()

    def copy_before_call(self):
        root = self.root.parent / 'without-callback'
        shutil.copytree(self.root, root)
        return Store(root)

    def assert_same_call_without_callback(self, result, baseline, observation):
        # Reuse the exact observed response and timestamp on a separate copy of the initial records.
        artifacts = baseline.build_artifact_store()
        for key in ('request', 'response', 'output'):
            reference = observation.get(key)
            if reference is not None:
                artifacts.put(self.case.artifacts.read(reference), reference['media_type'])
        with patch.object(protocol, '_observed', return_value=observation):
            expected = protocol.invoke_assignment(baseline, self.payload,
                expected_revision=baseline.revision, request_id=self.request_id)
        self.assertEqual(result, expected)
        self.assertEqual(self.store.committed_request(self.request_id), baseline.committed_request(self.request_id))
        self.assertEqual(self.case.read_records()['review_attempt']['pending-run'],
                         baseline.snapshot()['records']['review_attempt']['pending-run'])

    @contextmanager
    def retain_observation(self):
        observe, observations = protocol._observed, []
        def retain(*args, **kwargs):
            observation = observe(*args, **kwargs)
            observations.append(copy.deepcopy(observation))
            return observation
        with patch.object(protocol, '_observed', side_effect=retain):
            yield observations

    def test_transport_reads_pending_run_from_series_before_answering(self):
        def send(*args):
            self.assert_pending()
            return self.response
        result, errors = self.run_command(send)
        self.assertEqual(errors, '')
        self.assertEqual(result['result']['status'], 'completed')
        self.assert_repaired()

    def test_probe_transport_reads_committed_invocation_from_views(self):
        from research_harness.review_excerpts import read_sent_packet
        path = self.root / 'intermediate/reviews/routes/route/probes/pending-probe.json'
        def send(route, request, credential):
            view = json.loads(path.read_bytes())
            self.assertEqual(view['invocation']['id'], 'pending-probe')
            self.assertIsNone(view['probe'])
            packet = read_sent_packet(request)
            return self.case.build_provider_response(dict(allowed_control=packet['allowed_control'],
                evidence_control=packet['evidence_control'], excluded_controls=[]))
        with patch.object(protocol.transport, 'send_request', side_effect=send):
            result = cli.OPERATIONS['review-probe'](self.store, dict(id='pending-probe', route_id='route'),
                expected_revision=self.store.revision, request_id='pending-probe')
        self.assertEqual(result['result']['status'], 'completed')

    def test_two_turn_series_publishes_each_pending_run_before_transport(self):
        from research_harness.review_series import run_series
        observed = []
        def send(*args):
            turns = self.read_series()['turns']
            observed.append(len(turns))
            self.assertEqual(len(turns[-1]['runs']), 1)
            self.assertEqual(turns[-1]['runs'][0]['status'], 'pending')
            output = self.case.build_review_output('bar')
            if len(observed) == 1:
                output['source_requests'] = [dict(id='page', kind='inventory_page', page=0)]
                output['value']['status'] = 'unresolved'
            return self.case.build_provider_response(output)
        with patch.object(protocol.transport, 'send_request', side_effect=send):
            self.assertEqual(run_series(self.store, 'bar-first', emit=lambda row: None), 0)
        self.assertEqual(observed, [1, 2])
        self.assertTrue(all(turn['runs'][0]['status'] == 'completed' for turn in self.read_series()['turns']))

    def test_resumed_committed_start_does_not_call_after_start(self):
        from research_harness import review_call_capture
        capture = review_call_capture.capture_call
        def stop_after_capture(*args, **kwargs):
            capture(*args, **kwargs)
            raise RuntimeError('Interrupted after committing the capture')
        callbacks = []
        revision = self.store.revision
        with patch.object(protocol.transport, 'send_request', return_value=self.response), \
                patch.object(review_call_capture, 'capture_call', side_effect=stop_after_capture):
            with self.assertRaisesRegex(RuntimeError, 'Interrupted'):
                protocol.invoke_assignment(self.store, self.payload, expected_revision=revision,
                    request_id=self.request_id, after_start=lambda: callbacks.append(self.store.revision))
        self.assertEqual(len(callbacks), 1)
        published = self.read_published()
        with patch.object(protocol.transport, 'send_request', side_effect=AssertionError('Do not send a resumed call')):
            protocol.invoke_assignment(self.store, self.payload, expected_revision=revision,
                request_id=self.request_id, after_start=lambda: self.fail('Do not render a resumed call'))
        self.assertEqual(self.read_published(), published)

    def test_direct_invocation_without_callback_preserves_views_and_stored_request(self):
        published = self.read_published()
        assignment = self.case.read_records()['review_assignment'][self.assignment['id']]
        from research_harness.evidence import digest
        before = digest(assignment)
        with patch.object(protocol.transport, 'send_request', return_value=self.response):
            result = protocol.invoke_assignment(self.store, self.payload, expected_revision=self.store.revision,
                                                request_id=self.request_id)
        self.assertEqual(self.read_published(), published)
        records = self.case.read_records()
        self.assertEqual(digest(records['review_assignment'][assignment['id']]), before)
        self.assertTrue(protocol.authenticate_attempt(records, self.case.artifacts, assignment,
                                                      result['result']))

    def start_stopped_renderer(self):
        entered, release = self.make_event(), self.make_event()
        builder, passes = views.compute_view_model, []
        def build(*args):
            passes.append(args[0])
            if len(passes) == 1:
                entered.set()
                self.await_event(release)
            return builder(*args)
        guard = patch.object(views, 'compute_view_model', side_effect=build)
        guard.start()
        self.addCleanup(guard.stop)
        renderer = self.pool.submit(views.render_all, Store(self.root))
        self.await_event(entered)
        return renderer, release, passes

    def test_busy_builder_allows_transport_then_added_pass_publishes_pending(self):
        renderer, release, passes = self.start_stopped_renderer()
        def send(*args):
            self.assertFalse(release.is_set(), 'Transport must start before the blocked builder is released')
            self.assertEqual(self.read_runs(), [])
            self.assertTrue((self.root / views.DIRTY_PATH).is_file())
            release.set()
            renderer.result(timeout=30)
            self.assertEqual(len(passes), 2)
            self.assert_pending()
            self.assertFalse((self.root / views.DIRTY_PATH).exists())
            return self.response
        self.run_command(send)
        self.assert_repaired()

    def test_call_finishes_before_added_pass_and_never_publishes_pending(self):
        renderer, release, passes = self.start_stopped_renderer()
        result, _ = self.run_command(lambda *args: self.response)
        self.assertFalse(release.is_set())
        self.assertEqual(result['result']['status'], 'completed')
        self.assertEqual(self.read_runs(), [])
        self.assertTrue((self.root / views.DIRTY_PATH).is_file())
        release.set()
        renderer.result(timeout=30)
        self.assertEqual(len(passes), 2)
        self.assertNotIn('pending-run', passes[0].get('review_invocation', {}))
        self.assertIn('pending-run', passes[1]['review_attempt'])
        self.assert_repaired()

    def test_markers_in_both_passes_bound_work_before_transport(self):
        entered = [self.make_event(), self.make_event()]
        release = [self.make_event(), self.make_event()]
        builder, passes = views.compute_view_model, []
        def build(*args):
            passes.append(args[0])
            if len(passes) <= 2:
                index = len(passes) - 1
                entered[index].set()
                self.await_event(release[index])
            return builder(*args)
        def mark_during_passes():
            for index in range(2):
                self.await_event(entered[index])
                other = Store(self.root)
                self.case.put('problem_statement', 'concurrent-' + str(index), dict(id='concurrent-' + str(index)))
                result = views.render_workspace_views(other.root, lock_wait=0)
                self.assertEqual(result, dict(revision=None, rendered=False, deferred=True,
                    reason='render_lock_timeout', dirty_marker=views.DIRTY_PATH))
                release[index].set()
        def send(*args):
            writer.result(timeout=30)
            self.assertEqual(len(passes), 2)
            self.assertTrue((self.root / views.DIRTY_PATH).is_file())
            self.assertEqual(self.read_advisories()[0]['reason'], 'render_deferred')
            self.assert_pending()
            return self.response
        with patch.object(views, 'compute_view_model', side_effect=build):
            writer = self.pool.submit(mark_during_passes)
            self.run_command(send)
        self.assert_repaired()
        self.assertTrue((self.root / 'final/history/problem/statements/concurrent-1.json').is_file())

    def test_marker_after_other_renderers_added_snapshot_survives_both_commands(self):
        entered, release = self.make_event(), self.make_event()
        builder, passes = views.compute_view_model, []
        def build(*args):
            passes.append(args[0])
            if len(passes) == 1:
                views._mark_dirty(self.root)
            elif len(passes) == 2:
                entered.set()
                self.await_event(release)
            return builder(*args)
        with patch.object(views, 'compute_view_model', side_effect=build):
            renderer = self.pool.submit(views.render_all, Store(self.root))
            self.await_event(entered)
            self.run_command()
            self.assertFalse(release.is_set())
            release.set()
            renderer.result(timeout=30)
        self.assertEqual(len(passes), 2)
        self.assertEqual(self.read_runs(), [])
        self.assertTrue((self.root / views.DIRTY_PATH).is_file())
        self.assertEqual(self.read_advisories()[0]['reason'], 'render_deferred')
        views.render_workspace_views(self.root)
        self.assert_repaired()

    def test_second_acquisition_builder_failure_enters_transport_with_two_zero_wait_attempts(self):
        baseline = self.copy_before_call()
        first_entered, first_release, first_done = [self.make_event() for _ in range(3)]
        take_next, next_entered, next_release = [self.make_event() for _ in range(3)]
        hold, mark = views._hold_lock, views._mark_dirty
        waits = []
        path = 'intermediate/reviews/bar/bar-first/series.json'
        def contend():
            with hold(self.root, wait=0):
                first_entered.set()
                self.await_event(first_release)
            first_done.set()
            self.await_event(take_next)
            with hold(self.root, wait=0):
                next_entered.set()
                self.await_event(next_release)
        contender = self.pool.submit(contend)
        self.await_event(first_entered)
        @contextmanager
        def acquire(root, *, wait=None):
            waits.append(wait)
            try:
                with hold(root, wait=wait):
                    yield
            finally:
                if len(waits) == 2:
                    take_next.set()
                    self.await_event(next_entered)
        def mark_and_release(root):
            mark(root)
            first_release.set()
            self.await_event(first_done)
        def fail_builder(*args):
            raise ResearchError('unsafe_path', 'Synthetic builder failure', details=dict(path=path))
        def send(*args):
            self.assertEqual(waits, [0, 0])
            self.assertTrue(next_entered.is_set())
            self.assertFalse(next_release.is_set(), 'A failed render must send while the next renderer holds the lock')
            self.assertEqual(self.read_advisories()[0]['reason'], 'render_failed')
            next_release.set()
            contender.result(timeout=30)
            return self.response
        with patch.object(views, '_hold_lock', side_effect=acquire), \
                patch.object(views, '_mark_dirty', side_effect=mark_and_release), \
                patch.object(views, 'compute_view_model', side_effect=fail_builder), self.retain_observation() as observed:
            result, errors = self.run_command(send)
        self.assertIn('unsafe_path', errors)
        self.assertEqual(result['result'], self.case.read_records()['review_attempt']['pending-run'])
        receipt = self.store.committed_request(self.request_id)
        replay = protocol.invoke_assignment(self.store, self.payload,
            expected_revision=self.store.revision, request_id=self.request_id)
        self.assertEqual(result, replay)
        self.assertEqual(self.store.committed_request(self.request_id), receipt)
        self.assert_same_call_without_callback(result, baseline, observed[0])
        views.render_workspace_views(self.root)
        self.assert_repaired()

    def test_failure_recording_without_a_held_lock_never_waits(self):
        hold = views._hold_lock
        waits = []
        @contextmanager
        def acquire(root, *, wait=None):
            waits.append(wait)
            with hold(root, wait=wait):
                yield
        with hold(self.root), patch.object(views, '_hold_lock', side_effect=acquire):
            views._record_failure(self.root, dict(path='synthetic-view.json', error='OSError'))
        self.assertEqual(waits, [0])
        self.assertTrue((self.root / views.DIRTY_PATH).is_file())
        self.assertEqual(self.read_advisories()[0]['reason'], 'render_deferred')
        views.render_workspace_views(self.root)
        self.assertEqual(self.read_advisories(), [])

    def test_lock_acquisition_failure_keeps_stale_advisory_until_successful_repair(self):
        path = self.root / '.exactory/output-views.lock'
        path.unlink()
        path.mkdir()
        with redirect_stderr(io.StringIO()) as errors:
            self.assertIsNone(views.render_workspace_views(self.root, full=True))
        self.assertIn('IsADirectoryError', errors.getvalue())
        self.assertTrue((self.root / views.DIRTY_PATH).is_file())
        self.assertEqual(self.read_advisories()[0]['reason'], 'render_deferred')
        path.rmdir()
        views.render_workspace_views(self.root)
        self.assertEqual(self.read_advisories(), [])
        self.assertFalse((self.root / views.DIRTY_PATH).exists())

    def test_failure_before_lock_marks_new_work_beside_another_renderers_claimed_marker(self):
        views._mark_dirty(self.root)
        renderer, release, passes = self.start_stopped_renderer()
        self.assertTrue((self.root / views._RENDERING_PATH).is_file())
        self.assertFalse((self.root / views.DIRTY_PATH).exists())
        self.case.put('problem_statement', 'fallback-latest', dict(id='fallback-latest'))
        with patch.object(views, 'Store', side_effect=OSError('Synthetic store initialization failure')), \
                redirect_stderr(io.StringIO()):
            self.assertIsNone(views.render_workspace_views(self.root, lock_wait=0))
        self.assertTrue((self.root / views.DIRTY_PATH).is_file())
        release.set()
        renderer.result(timeout=30)
        self.assertEqual(len(passes), 2)
        self.assertNotIn('fallback-latest', passes[0]['problem_statement'])
        self.assertIn('fallback-latest', passes[1]['problem_statement'])
        self.assertTrue((self.root / 'final/history/problem/statements/fallback-latest.json').is_file())
        self.assertEqual(views.read_rendered_revision(self.store), self.store.revision)
        self.assertEqual(self.read_advisories(), [])
        self.assertFalse((self.root / views.DIRTY_PATH).exists())
        self.assertFalse((self.root / views._RENDERING_PATH).exists())

    def check_failed_pass(self, failure_kind, claimed):
        baseline = self.copy_before_call()
        path = ('intermediate/reviews/bar/bar-first/series.json' if failure_kind != 'other'
                else 'final/problem/statements/statement-1.json')
        if failure_kind == 'other':
            # A writable owned view is republished even when its content is unchanged.
            (self.root / path).chmod(0o600)
        published = self.read_published()
        if claimed:
            views._mark_dirty(self.root)
        write, builder = views.write_projection, views.compute_view_model
        def write_file(root, relative, content):
            if relative == path:
                raise OSError('Synthetic view write failure')
            return write(root, relative, content)
        def fail_builder(*args):
            raise ResearchError('unsafe_path', 'Synthetic builder failure', details=dict(path=path))
        def send(*args):
            marker = json.loads((self.root / views.MARKER_PATH).read_bytes())
            advisory = self.read_advisories()[0]
            self.assertEqual((advisory['reason'], advisory['path']),
                             ('render_failed' if failure_kind == 'builder' else 'file_errors', path))
            if failure_kind == 'builder':
                self.assertEqual(marker['failure'], dict(path=path, error='unsafe_path'))
                self.assertEqual(self.read_published(), published)
            else:
                self.assertEqual(marker['errors'][path], 'OSError')
                self.assertEqual((self.root / path).read_bytes(), published[path])
                self.assertNotEqual(self.read_published(), published)
                inputs, _ = views._read_inputs(self.root, views.find_workspace_root(self.root))
                model = views.compute_view_model(self.case.read_records(), self.case.artifacts, inputs)
                for relative, view in model.items():
                    if relative != path:
                        content = view.content if isinstance(view.content, bytes) else self.case.artifacts.read(view.content)
                        self.assertEqual((self.root / relative).read_bytes(), content, relative)
                if failure_kind == 'other':
                    self.assert_pending()
            if claimed:
                self.assertTrue((self.root / views._RENDERING_PATH).is_file())
            return self.response
        with patch.object(views, 'write_projection', side_effect=write_file), \
                patch.object(views, 'compute_view_model', side_effect=fail_builder if failure_kind == 'builder' else builder), \
                self.retain_observation() as observed:
            result, _ = self.run_command(send)
        advisory = self.read_advisories()[0]
        self.assertEqual((advisory['reason'], advisory['path']),
                         ('render_failed' if failure_kind == 'builder' else 'file_errors', path))
        if claimed:
            self.assertTrue((self.root / views._RENDERING_PATH).is_file())
        attempt = self.case.read_records()['review_attempt']['pending-run']
        receipt = self.store.committed_request(self.request_id)
        with patch.object(protocol.transport, 'send_request', side_effect=AssertionError('No second transport')):
            replay = protocol.invoke_assignment(self.store, self.payload,
                expected_revision=self.store.revision, request_id=self.request_id)
        self.assertEqual(result, replay)
        self.assertEqual(self.case.read_records()['review_attempt']['pending-run'], attempt)
        self.assertEqual(self.store.committed_request(self.request_id), receipt)
        self.assert_same_call_without_callback(result, baseline, observed[0])
        views.render_workspace_views(self.root)
        self.assert_repaired()

    def test_series_write_failure_preserves_call_and_repairs(self):
        self.check_failed_pass('series', claimed=False)

    def test_other_view_write_failure_preserves_pending_series_and_repairs(self):
        self.check_failed_pass('other', claimed=False)

    def test_builder_failure_preserves_published_files_and_repairs(self):
        self.check_failed_pass('builder', claimed=False)

    def test_claimed_marker_series_failure_reports_file_errors_until_repair(self):
        self.check_failed_pass('series', claimed=True)

    def test_claimed_marker_other_view_failure_reports_file_errors_until_repair(self):
        self.check_failed_pass('other', claimed=True)

    def test_claimed_marker_builder_failure_reports_render_failed_until_repair(self):
        self.check_failed_pass('builder', claimed=True)


if __name__ == '__main__':
    unittest.main()
