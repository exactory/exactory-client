"""Qualify cloned synthetic records without admission or external effects."""

from contextlib import ExitStack
import copy
import hashlib
import json
import os
from pathlib import Path
import runpy
import shutil
import socket
import subprocess
import sys
import tempfile
import tarfile
import unittest
from unittest.mock import patch

from research_harness import cli, review_protocol as protocol
from research_harness.errors import ResearchError
from research_harness.storage import Store
import test_research_problem_reviews as fixtures


PLUGIN = Path(__file__).resolve().parents[1]
BASELINE_READERS_COMMIT_ID = '19291e0983103fb62ffc62ccffdcdfbc2105cc8c'
BASELINE_READERS_ARCHIVE_SHA256 = '05e3c70b94cae2d384bc74c5a8b373c1fe29da74e75f584954fa67b23fbab0cd'


def digest_tree(root):
    return {str(path.relative_to(root)): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in root.rglob('*') if path.is_file()}


class RecordCheckTests(unittest.TestCase):
    def setUp(self):
        self.case = fixtures.ProblemReviewTests()
        self.case.setUp()
        self.addCleanup(self.case.doCleanups)
        self.case.prepare_review_route()
        self.case.supporting = self.case.artifacts.put(b'A synthetic field standard.', 'text/plain')
        self.assignment = self.case.assign('bar', 'first', **self.case.deliver_supporting('first'))

    def observe(self, output=None, failure=False):
        response = self.case.build_provider_response(output or self.case.build_review_output('bar'))
        with patch('research_harness.review_transport.send_request',
                   side_effect=OSError('synthetic provider unavailable') if failure else None,
                   return_value=response):
            self.case.mutate(protocol.invoke_assignment,
                             dict(id='observed', assignment_id=self.assignment['id']), credential='fixture')

    def check(self, **kwargs):
        from research_harness.record_check import check_record
        return check_record(self.case.root, isolate=False, **kwargs)

    def step(self, report, name):
        return next(row for row in report['steps'] if row['step'] == name)

    def test_all_six_steps_pass_pending_history_without_changing_live_tree(self):
        before = digest_tree(self.case.root)
        report = self.check()
        self.assertTrue(report['ready'], report)
        self.assertEqual([row['step'] for row in report['steps']],
                         ['snapshot', 'integrity', 'reconstruction', 'authentication', 'continuation', 'report'])
        self.assertTrue(all(row['seconds'] >= 0 for row in report['steps']))
        self.assertEqual(digest_tree(self.case.root), before)
        self.assertFalse(Path(report['clone']).exists())
        self.assertEqual(set(self.step(report, 'report')['result']['commands']), {'status', 'next', 'obligations'})

    def test_failed_history_keeps_its_authentication_and_classification(self):
        self.observe(failure=True)
        report = self.check()
        self.assertTrue(report['ready'], report)
        attempts = self.step(report, 'authentication')['result']['attempts']
        self.assertFalse(attempts['observed']['authenticated'])
        self.assertEqual(attempts['observed']['status'], 'failed')

    def test_completed_observation_authenticates_and_records_final_only_in_clone(self):
        self.observe()
        before = digest_tree(self.case.root)
        report = self.check()
        self.assertTrue(report['ready'], report)
        self.assertTrue(self.step(report, 'authentication')['result']['attempts']['observed']['authenticated'])
        actions = self.step(report, 'continuation')['result']['chains'][0]['actions']
        self.assertEqual([row['state'] for row in actions], ['record', 'done'])
        self.assertEqual(digest_tree(self.case.root), before)

    def test_recorded_scientific_refusal_is_preserved_then_replanned(self):
        output = self.case.build_review_output('bar')
        output['value']['status'] = 'invalid'
        self.observe(output)
        report = self.check()
        self.assertTrue(report['ready'], report)
        chain = self.step(report, 'continuation')['result']['chains'][0]
        self.assertEqual([row['state'] for row in chain['actions']], ['record', 'run'])
        self.assertFalse(self.case.read_records().get('review_output_refusal'))

    def test_mutation_runtime_handoff_is_an_enumerated_external_boundary(self):
        from research_harness import record_check
        action = dict(state='continue', command='review-assignment', payload={},
                      head_id=self.assignment['id'], progress={})

        def refuse(*args, **kwargs):
            raise ResearchError('review_route_runtime_stale', 'Record a new route')

        with patch.object(record_check, 'plan_next', return_value=action), \
                patch.dict(cli.OPERATIONS, {'review-assignment': refuse}):
            report = self.check()
        self.assertTrue(report['ready'], report)

    def test_snapshot_retries_three_times_without_opening_source_sqlite(self):
        from research_harness import record_check
        real_store = record_check.OfflineStore
        opened = []

        def open_store(root, *args, **kwargs):
            opened.append(Path(root))
            self.assertNotEqual(Path(root), self.case.root)
            raise ResearchError('store_corrupt', 'Synthetic torn copy')

        with patch.object(record_check, 'OfflineStore', side_effect=open_store):
            report = self.check()
        self.assertFalse(report['ready'])
        self.assertEqual(len(opened), 3)
        self.assertEqual(self.step(report, 'snapshot')['error']['code'], 'record_snapshot_inconsistent')
        self.assertFalse(Path(report['clone']).exists())
        self.assertTrue(issubclass(real_store, Store))

    def test_integrity_rejects_retained_artifact_damage(self):
        self.observe()
        artifact = self.case.root / self.assignment['packet']['path']
        artifact.chmod(0o600)
        artifact.write_bytes(b'damaged')
        report = self.check()
        self.assertFalse(report['ready'])
        self.assertEqual(self.step(report, 'integrity')['status'], 'failed')

    def test_reconstruction_checks_each_invocation_and_its_own_fit_order(self):
        self.observe()
        from research_harness import record_check
        original = record_check.transport.list_built_requests

        def changed_assignment(route, prompt, packet, history=()):
            if prompt == self.assignment['prompt']['text']:
                return iter([{'changed': True}])
            return original(route, prompt, packet, history)

        with patch.object(record_check.transport, 'list_built_requests', side_effect=changed_assignment):
            report = self.check()
        self.assertEqual(self.step(report, 'reconstruction')['error']['code'], 'record_request_changed')
        self.assertEqual(self.step(report, 'reconstruction')['error']['details']['kind'], 'review_invocation')
        self.assertFalse(report['ready'])

    def test_distinct_current_and_earlier_requests_rebuild_for_one_assignment(self):
        from research_harness.record_check import OfflineStore, check_reconstruction
        from research_harness import review_transport as transport
        from research_harness.evidence import digest
        from codex_route_fixtures import record_codex_route, limit_codex_input
        import test_research_review_protocol as protocol_fixtures
        case = protocol_fixtures.ReviewProtocolTests()
        case.setUp()
        self.addCleanup(case.doCleanups)
        record_codex_route(case)
        assignment = case.assign()['result']
        packet = protocol._read_json(case.artifacts, assignment['packet'])
        body = case.artifacts.put(b'Earlier synthetic primary evidence. ' * 300, 'text/plain')
        entry = dict(artifact=body, encoding='utf-8', content=case.artifacts.read(body).decode())
        historical_packet = dict(evidence=[entry], digest=digest(entry))
        packet['scientific_history'] = [dict(packet=copy.deepcopy(historical_packet))]
        packet.pop('digest')
        packet['digest'] = digest(packet)
        history = [dict(role='user', content=transport.PACKET_LABEL + json.dumps(historical_packet, sort_keys=True)),
                   dict(role='assistant', content='{"assessment":"An independent synthetic assessment."}')]
        assignment = dict(assignment, packet=protocol._put_json(case.artifacts, packet),
                          packet_digest=packet['digest'], history=history)
        assignment.pop('history_ref', None)
        case.seed('review_assignment', assignment['id'], assignment)
        route = case.records()['review_route']['route']
        full = transport.request_body(route, assignment['prompt']['text'], packet, history, order=())
        limit_codex_input(self, len(transport._local_adapter(route).packet_text(full)) - 3000)
        current = transport.request_body(route, assignment['prompt']['text'], packet, history)
        earlier = transport.request_body(route, assignment['prompt']['text'], packet, history,
                                         order=transport.EARLIER_FIT_ORDERS[1])
        self.assertNotEqual(current, earlier)
        for identifier, request in (('current', current), ('earlier', earlier)):
            case.seed('review_invocation', identifier, dict(id=identifier, assignment_id=assignment['id'],
                      request=protocol._put_json(case.artifacts, request)))
        result = check_reconstruction(OfflineStore(case.root))['invocations']
        self.assertNotEqual(result['review_invocation:current']['sha256'],
                            result['review_invocation:earlier']['sha256'])

    def test_imported_observation_is_reported_without_inventing_readiness(self):
        output = protocol._put_json(self.case.artifacts, self.case.build_review_output('bar'))
        self.case.mutate(protocol.record_attempt, dict(id='imported', assignment_id=self.assignment['id'],
                         status='completed', output=output, reason='Imported synthetic observation', usage={}))
        report = self.check()
        self.assertTrue(report['ready'], report)
        self.assertFalse(self.step(report, 'authentication')['result']['attempts']['imported']['authenticated'])
        self.assertFalse(self.step(report, 'authentication')['result']['assignments'][self.assignment['id']]['ready'])

    def test_continuation_refusal_fails_at_exact_planning_step(self):
        from research_harness import record_check
        refusal = dict(state='stop', head_id=self.assignment['id'], progress={},
                       code='review_parts_no_progress', details={'phase': 'continuation'})
        with patch.object(record_check, 'plan_next', return_value=refusal):
            report = self.check()
        self.assertFalse(report['ready'])
        self.assertEqual(self.step(report, 'continuation')['error']['code'], 'review_parts_no_progress')

    def test_run_checks_route_limit_without_admission(self):
        from research_harness import record_check
        with patch.object(record_check.transport, 'local_input_limit', return_value=1):
            report = self.check()
        self.assertFalse(report['ready'])
        self.assertEqual(self.step(report, 'continuation')['error']['code'], 'review_input_too_large')
        self.assertFalse(self.case.read_records().get('review_invocation'))

    def test_unknown_state_never_executes_an_otherwise_allowed_operation(self):
        from research_harness import record_check
        unknown = dict(state='unknown', command='review-assignment', payload={},
                       head_id=self.assignment['id'], progress={})
        run = dict(state='run', command='review-run', payload={'assignment_id': self.assignment['id']},
                   head_id=self.assignment['id'], progress={})
        with patch.object(record_check, 'plan_next', side_effect=[unknown, run]), \
                patch.dict(cli.OPERATIONS, {'review-assignment': unittest.mock.Mock(return_value={})}):
            operation = cli.OPERATIONS['review-assignment']
            report = self.check()
        operation.assert_not_called()
        self.assertFalse(report['ready'])
        self.assertEqual(self.step(report, 'continuation')['error']['code'], 'record_unknown_action')

    def test_offline_executor_refuses_operation_outside_allowlist(self):
        from research_harness import record_check
        action = dict(state='plan', command='review-probe', payload={'route_id': 'route'},
                      head_id=self.assignment['id'], progress={})
        with patch.object(record_check, 'plan_next', return_value=action):
            report = self.check()
        self.assertEqual(self.step(report, 'continuation')['error']['code'], 'qualification_unavailable')
        self.assertIn('review-probe', self.step(report, 'continuation')['error']['details']['reason'])

    def test_effect_denial_beneath_allowed_operation_reports_step_and_reason(self):
        from research_harness import record_check
        action = dict(state='continue', command='review-assignment', payload={},
                      head_id=self.assignment['id'], progress={})

        def external(store, payload, **identity):
            store.build_artifact_store().require_external_effect()

        with patch.object(record_check, 'plan_next', return_value=action), \
                patch.dict(cli.OPERATIONS, {'review-assignment': external}):
            report = self.check()
        row = self.step(report, 'continuation')
        self.assertEqual(row['error']['code'], 'qualification_unavailable')
        self.assertEqual(row['error']['details']['step'], 'continuation')
        self.assertTrue(row['error']['details']['reason'])

    def test_command_over_budget_is_failed_with_patched_clock(self):
        from research_harness import record_check
        with patch.object(record_check.time, 'monotonic', side_effect=[0, 61]):
            result = record_check.run_timed_check('status', lambda: {'ready': False}, budget=60)
        self.assertEqual(result['error']['code'], 'record_command_timeout')
        self.assertEqual(result['seconds'], 61)

    def test_command_deadline_cannot_be_swallowed_as_a_record_refusal(self):
        from research_harness import record_check
        handlers = []

        def install_handler(number, handler):
            handlers.append(handler)
            return 0

        def swallow_record_errors():
            try:
                handlers[0](0, None)
            except Exception:
                return {'swallowed': True}

        with patch.object(record_check.signal, 'signal', side_effect=install_handler), \
                patch.object(record_check.signal, 'setitimer', return_value=(0, 0)):
            result = record_check.run_timed_check('status', swallow_record_errors, budget=60)
        self.assertEqual(result['status'], 'failed')
        self.assertEqual(result['error']['code'], 'record_command_timeout')

    def test_report_step_preserves_all_command_failures_and_times(self):
        from research_harness import record_check
        original = record_check.run_timed_check

        def over_budget(name, check, **kwargs):
            if name in ('status', 'next', 'obligations'):
                return {'step': name, 'seconds': 61, 'status': 'failed',
                        'error': {'code': 'record_command_timeout'}}
            return original(name, check, **kwargs)

        with patch.object(record_check, 'run_timed_check', side_effect=over_budget):
            report = self.check()
        row = self.step(report, 'report')
        self.assertEqual(row['status'], 'failed')
        self.assertEqual(set(row['result']['commands']), {'status', 'next', 'obligations'})
        self.assertTrue(all(command['seconds'] == 61 for command in row['result']['commands'].values()))

    def test_obligations_reader_is_exercised_even_with_no_obligations(self):
        from research_harness.record_check import OfflineStore, check_report
        report = {'revision': 1, 'obligations': []}
        with patch('research_harness.cli.status_report', return_value=report), \
                patch('research_harness.report_views.next_summary', return_value={}), \
                patch('research_harness.report_views.obligations_page', return_value={}) as page, \
                patch('research_harness.record_check.check_math_roots', return_value=[]):
            result = check_report(OfflineStore(self.case.root))
        self.assertTrue(result['ready'])
        page.assert_called_once()

    def test_unexpected_defect_surfaces_and_clone_is_removed(self):
        from research_harness import record_check
        with patch.object(record_check, 'check_integrity', side_effect=RuntimeError('implementation defect')):
            with self.assertRaisesRegex(RuntimeError, 'implementation defect'):
                self.check()

    def test_full_checks_never_reach_process_network_or_reviewer_transports(self):
        self.observe()
        sentinels = []
        with ExitStack() as stack:
            for target in ('subprocess.Popen', 'os.posix_spawn', 'socket.socket.connect',
                           'research_harness.review_transport.send_request',
                           'research_harness.review_claude_transport.send_request',
                           'research_harness.review_codex_transport.send_request',
                           'research_harness.review_codex_framed_transport.send_request',
                           'research_harness.review_codex_visual_transport.send_request'):
                sentinels.append(stack.enter_context(patch(target, side_effect=AssertionError(target))))
            report = self.check()
        for sentinel in sentinels:
            sentinel.assert_not_called()
        self.assertTrue(report['ready'], report)

    def test_keep_clone_retains_only_requested_snapshot(self):
        destination = self.case.root.parent / 'kept-record'
        report = self.check(keep_clone=destination)
        self.addCleanup(shutil.rmtree, destination)
        self.assertTrue(report['ready'], report)
        self.assertEqual(Path(report['clone']), destination.resolve())
        self.assertTrue((destination / '.exactory/research.sqlite3').is_file())

    def test_keep_clone_cannot_resolve_inside_live_workspace(self):
        from research_harness.record_check import check_record
        alias = self.case.root.parent / 'alias-to-record'
        alias.symlink_to(self.case.root, target_is_directory=True)
        for destination in (alias / 'nested-copy', self.case.root.parent / 'unused/../study/nested-copy'):
            with self.subTest(destination=destination), patch(
                    'research_harness.record_check.copy_snapshot', side_effect=AssertionError('source mutation')):
                with self.assertRaises(ResearchError) as caught:
                    check_record(self.case.root, keep_clone=destination, isolate=False)
                self.assertEqual(caught.exception.code, 'unsafe_path')

    def test_clone_destination_created_concurrently_is_never_removed(self):
        from research_harness.record_check import check_record
        destination = self.case.root.parent / 'concurrent-destination'
        self.addCleanup(lambda: shutil.rmtree(destination) if destination.exists() else None)
        marker = destination / 'user-file'

        def appear(*args):
            destination.mkdir()
            marker.write_text('Concurrent user data')
            raise FileExistsError(str(destination))

        with patch('research_harness.record_check.copy_snapshot', side_effect=appear):
            with self.assertRaises(ResearchError) as caught:
                check_record(self.case.root, keep_clone=destination, isolate=False)
        self.assertEqual(caught.exception.code, 'unsafe_path')
        self.assertEqual(marker.read_text(), 'Concurrent user data')

    def test_visual_probe_reconstruction_refuses_renderer_before_process_launch(self):
        from research_harness.record_check import OfflineArtifacts, rebuild_invocation
        from research_harness.artifacts import _CheckEffectRequired
        records = self.case.read_records()
        records['review_route']['route']['adapter'] = 'codex_cli_visual_v1'
        records['review_route']['route']['endpoint'] = 'codex://local'
        from research_harness import review_visual_inputs, review_pdf_pages
        review_visual_inputs.control_image.cache_clear()
        review_pdf_pages._profile.cache_clear()
        invocation = records['review_probe_invocation']['probe']
        with patch('subprocess.Popen', side_effect=AssertionError('renderer launched')):
            with self.assertRaises(_CheckEffectRequired):
                rebuild_invocation(records, OfflineArtifacts(self.case.root, []), invocation,
                                   kind='review_probe_invocation')

    def test_live_workers_are_reported_pending_without_reconciliation(self):
        with patch('research_harness.review_protocol._observed', side_effect=RuntimeError('interrupted')):
            with self.assertRaises(RuntimeError):
                self.observe()
        report = self.check()
        self.assertTrue(report['ready'], report)
        chain = self.step(report, 'continuation')['result']['chains'][0]
        self.assertEqual(chain['boundary'], 'pending')

    def test_captured_unfinished_observation_and_each_probe_are_authenticated(self):
        from research_harness import review_call_capture
        original = review_call_capture.capture_call

        def capture(*args, **kwargs):
            original(*args, **kwargs)
            raise RuntimeError('Interrupted after durable capture')

        with patch.object(review_call_capture, 'capture_call', side_effect=capture):
            with self.assertRaises(RuntimeError):
                self.observe()
        report = self.check()
        self.assertTrue(report['ready'], report)
        result = self.step(report, 'authentication')['result']
        self.assertTrue(result['probes']['probe']['authenticated'])
        captured = next(row for row in result['captures'].values() if 'classification' in row)
        self.assertEqual(captured['status'], 'completed')
        self.assertTrue(captured['authenticated'])

    def test_cli_and_pinned_wrapper_report_running_tree_version(self):
        with tempfile.TemporaryDirectory() as directory:
            tree = Path(directory) / 'pinned'
            shutil.copytree(PLUGIN, tree, ignore=shutil.ignore_patterns('.git', '.lane-scratch', '__pycache__'))
            (tree / '.claude-plugin/plugin.json').write_text('{"version":"0.0.123"}')
            wrapper = Path(directory) / 'xr.py'
            wrapper.write_text('import sys\nsys.path.insert(0, ' + repr(str(tree))
                               + ')\nfrom research_harness.cli import main\nmain()\n')
            result = subprocess.run([sys.executable, str(wrapper), 'record-check',
                                     '--workspace', str(self.case.root)], cwd=PLUGIN,
                                    capture_output=True, text=True,
                                    env=dict(os.environ, PYTHONDONTWRITEBYTECODE='1'), timeout=60)
            self.assertEqual(result.returncode, 0, result.stderr)
            report = json.loads(result.stdout)
            self.assertEqual(report['runtime']['harness_version'], '0.0.123')
            self.assertEqual(report['runtime']['harness_root'], str(tree.resolve()))
            self.assertEqual(report['runtime']['python_executable'], sys.executable)

    def test_module_cli_reports_record_check(self):
        result = subprocess.run([sys.executable, '-m', 'research_harness.cli', 'record-check',
                                 '--workspace', str(self.case.root)], cwd=PLUGIN,
                                capture_output=True, text=True,
                                env=dict(os.environ, PYTHONPATH=str(PLUGIN), PYTHONDONTWRITEBYTECODE='1'), timeout=60)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(json.loads(result.stdout)['ready'])

    def test_fingerprint_script_runs_with_shared_readers(self):
        self.observe()
        from research_harness.record_check import copy_snapshot
        with tempfile.TemporaryDirectory() as directory:
            clone = Path(directory) / 'snapshot'
            copy_snapshot(self.case.root, clone)
            result = subprocess.run([sys.executable, str(PLUGIN / 'ci/record_fingerprint.py'),
                                     '--harness', str(PLUGIN), '--workspace', str(clone)], cwd=PLUGIN,
                                    capture_output=True, text=True, timeout=60)
        self.assertEqual(result.returncode, 0, result.stderr)
        assignment = json.loads(result.stdout)['assignments'][self.assignment['id']]
        self.assertTrue(assignment['assignment_state']['ready'])
        self.assertTrue(assignment['authentication']['observed'])
        self.assertTrue(assignment['rebuilt_requests']['observed']['sha256'])

    def test_fingerprint_matches_0724_on_the_same_snapshot(self):
        self.observe()
        from research_harness.record_check import copy_snapshot
        with tempfile.TemporaryDirectory() as directory:
            clone = Path(directory) / 'snapshot'
            copy_snapshot(self.case.root, clone)
            previous = Path(directory) / 'previous'
            previous.mkdir()
            # Preserve the real baseline readers even in CI's shallow checkout.
            archive = PLUGIN / 'tests/fixtures/record_readers_0_72_4.tar.gz'
            self.assertEqual(hashlib.sha256(archive.read_bytes()).hexdigest(),
                             BASELINE_READERS_ARCHIVE_SHA256)
            with tarfile.open(archive, 'r:gz') as source:
                self.assertEqual(source.pax_headers['comment'], BASELINE_READERS_COMMIT_ID)
                if sys.version_info >= (3, 12):
                    source.extractall(previous, filter='data')
                else:
                    source.extractall(previous)
            results = []
            for harness in (previous, PLUGIN):
                completed = subprocess.run([
                    sys.executable, str(PLUGIN / 'ci/record_fingerprint.py'), '--harness', str(harness),
                    '--workspace', str(clone)], cwd=PLUGIN, capture_output=True, text=True, timeout=60)
                self.assertEqual(completed.returncode, 0, completed.stderr)
                results.append(json.loads(completed.stdout))
            self.assertEqual(results[0], results[1])

    def test_math_roots_ignore_registry_paths_and_keep_command_cwd_outside_clone(self):
        from research_harness.record_check import check_math_roots
        root = self.case.root / 'attack/root'
        (root / '.search').mkdir(parents=True)
        (root / '.search/tree.json').write_text('{}')
        (self.case.root / '.exactory/math-search.json').write_text(
            '{"attack_root":"/a/forbidden/live/root"}')
        with patch('subprocess.run', return_value=subprocess.CompletedProcess([], 0, '{}', '')) as run:
            rows = check_math_roots(self.case.root, PLUGIN)
        self.assertEqual(len(rows), 2)
        for call in run.call_args_list:
            self.assertEqual(Path(call.args[0][call.args[0].index('--attack-root') + 1]), root.resolve())
            self.assertEqual(call.kwargs['cwd'], PLUGIN)
            self.assertNotIn('/a/forbidden/live/root', call.args[0])

    def test_real_math_readers_use_only_controller_inside_clone(self):
        from research_harness.record_check import check_math_roots, copy_snapshot
        from test_research_check_mode_audit import read_command_parsers
        with read_command_parsers():
            from search_controller.service import Controller
            fixtures_path = PLUGIN / 'skills/math-solver/harness/tests/search_fixtures.py'
            fixtures = runpy.run_path(str(fixtures_path))
            root = self.case.root / 'attack/synthetic-root'
            root.mkdir(parents=True)
            Controller(root).command('init', {'contract': fixtures['contract']()}, 0, 'init-controller')
        (self.case.root / '.exactory/math-search.json').write_text(
            '{"attack_root":"/a/forbidden/live/root"}')
        with tempfile.TemporaryDirectory() as directory:
            clone = Path(directory) / 'clone'
            copy_snapshot(self.case.root, clone)
            rows = check_math_roots(clone, PLUGIN)
        self.assertEqual(len(rows), 2)
        self.assertEqual([row['returncode'] for row in rows], [0, 0])
        self.assertTrue(all(str(row['attack_root']).startswith(str(clone.resolve())) for row in rows))

    def test_math_root_discovery_never_silently_skips_an_unreadable_directory(self):
        from research_harness.record_check import check_math_roots

        def unreadable(*args, **kwargs):
            callback = kwargs.get('onerror')
            if callback is not None:
                callback(PermissionError('unreadable controller directory'))
            return iter(())

        with patch('research_harness.record_check.os.walk', side_effect=unreadable):
            with self.assertRaises(PermissionError):
                check_math_roots(self.case.root, PLUGIN)

    def test_sandbox_profile_denies_source_reads_network_and_outside_writes(self):
        from research_harness.record_check import build_sandbox_profile
        profile = build_sandbox_profile(Path('/tmp/clone'), Path('/tmp/report'), Path('/tmp/live'))
        self.assertIn('(deny network-outbound)', profile)
        self.assertIn('(deny file-read* (subpath "/private/tmp/live"))' if sys.platform == 'darwin'
                      else '(deny file-read* (subpath "/tmp/live"))', profile)
        self.assertIn('(deny file-write*', profile)

    def test_sandbox_profile_enforces_outside_write_denial_when_available(self):
        from research_harness.record_check import build_sandbox_profile
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory).resolve()
            clone, reports = base / 'clone', base / 'reports'
            clone.mkdir()
            reports.mkdir()
            if sys.platform != 'darwin':
                self.assertFalse(Path('/usr/bin/sandbox-exec').is_file())
                return
            profile = build_sandbox_profile(clone, reports, self.case.root)
            probe = subprocess.run(['/usr/bin/sandbox-exec', '-p', profile, '/usr/bin/true'],
                                   capture_output=True, text=True)
            if probe.returncode:
                self.assertTrue(probe.stderr.strip())
                return
            forbidden = base / 'outside'
            result = subprocess.run(['/usr/bin/sandbox-exec', '-p', profile, '/bin/sh', '-c',
                                     'echo forbidden > "$1"', 'write-test', str(forbidden)],
                                    capture_output=True, text=True)
            self.assertNotEqual(result.returncode, 0)
            self.assertFalse(forbidden.exists())

    def test_temporary_directory_inside_source_is_never_used(self):
        from research_harness.record_check import check_record
        before = self.case.root.stat().st_mtime_ns
        with patch.object(tempfile, 'tempdir', str(self.case.root)):
            result = check_record(self.case.root, isolate=False)
        self.assertTrue(result['ready'], result)
        self.assertEqual(self.case.root.stat().st_mtime_ns, before)

    def test_uncached_tmpdir_selection_never_probes_inside_source(self):
        from research_harness.record_check import check_record
        before = self.case.root.stat().st_mtime_ns
        with patch.object(tempfile, 'tempdir', None), patch.dict(os.environ, TMPDIR=str(self.case.root)):
            result = check_record(self.case.root, isolate=False)
        self.assertTrue(result['ready'], result)
        self.assertEqual(self.case.root.stat().st_mtime_ns, before)

    def test_sandbox_launch_denial_is_reported_as_unavailable(self):
        from research_harness.record_check import check_isolated_snapshot
        report = {'isolation': 'unavailable', 'local_reads': [], 'steps': []}
        executable_stat = Path(sys.executable).stat()
        with patch('research_harness.record_check.sys.platform', 'darwin'), \
                patch('research_harness.record_check.Path.stat', return_value=executable_stat), \
                patch('subprocess.run', side_effect=PermissionError('sandbox launch denied')), \
                patch('research_harness.record_check.qualify_snapshot', return_value=report):
            result = check_isolated_snapshot(self.case.root, self.case.root.parent / 'source',
                                             report, self.case.root.parent)
        self.assertEqual(result['isolation'], 'unavailable')
        self.assertIn('sandbox launch denied', result['isolation_reason'])

    def test_process_sentinels_also_cover_uncached_provenance(self):
        from research_harness.provenance import runtime_provenance
        runtime_provenance.cache_clear()
        with patch('subprocess.Popen', side_effect=AssertionError('unexpected process')):
            report = self.check()
        self.assertTrue(report['ready'], report)

    def test_cold_platform_runtime_cannot_launch_uname(self):
        from research_harness import record_check
        import platform
        with patch.object(platform, '_platform_cache', {}), patch.object(platform, '_uname_cache', None), \
                patch('subprocess.Popen', side_effect=AssertionError('uname launched')) as popen:
            result = self.check()
        popen.assert_not_called()
        self.assertTrue(result['ready'], result)

    def test_offline_runtime_fingerprint_reads_implementation_without_a_process(self):
        from research_harness.record_check import OfflineArtifacts
        import platform
        artifacts = OfflineArtifacts(self.case.root, [])
        with patch.object(platform, '_platform_cache', {}), \
                patch('subprocess.Popen', side_effect=AssertionError('uname launched')) as popen:
            result = artifacts.read_runtime_fingerprint({'adapter': 'openai_responses_v1'})
        popen.assert_not_called()
        self.assertEqual(result['adapter'], 'openai_responses_v1')
        self.assertEqual(len(result['implementation_sha256']), 64)

    def test_allowed_recording_does_not_launch_uncached_git_provenance(self):
        from research_harness.provenance import runtime_provenance
        self.observe()
        runtime_provenance.cache_clear()
        with patch('subprocess.Popen', side_effect=AssertionError('unexpected process')):
            report = self.check()
        self.assertTrue(report['ready'], report)

    def test_mutation_time_continuation_refusal_fails_qualification(self):
        from research_harness import record_check
        output = self.case.build_review_output('bar')
        output['source_requests'] = [dict(id='page', kind='inventory_page', page=0)]
        output['value']['status'] = 'unresolved'
        self.observe(output)
        with patch.dict(cli.OPERATIONS, {'review-assignment': lambda *args, **kwargs: (
                (_ for _ in ()).throw(ResearchError('review_parts_no_progress', 'Refused continuation')))}):
            report = self.check()
        self.assertEqual(self.step(report, 'continuation')['error']['code'], 'review_parts_no_progress')

    def test_run_boundary_cannot_qualify_an_unlisted_command(self):
        from research_harness import record_check
        action = dict(state='run', command='review-output-correction',
                      payload={'assignment_id': self.assignment['id']},
                      head_id=self.assignment['id'], progress={})
        with patch.object(record_check, 'plan_next', return_value=action):
            report = self.check()
        self.assertFalse(report['ready'])
        self.assertEqual(self.step(report, 'continuation')['error']['code'], 'qualification_unavailable')


if __name__ == '__main__':
    unittest.main()
