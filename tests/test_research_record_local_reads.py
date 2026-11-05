"""Local route qualification permits version reads and retained context only."""

from contextlib import ExitStack
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from codex_route_fixtures import build_codex_response
from research_harness import review_protocol as protocol, review_transport as transport
from research_harness.check_mode import check_mutation
from research_harness.provenance import runtime_provenance
from test_research_record_check import digest_tree
import test_research_review_protocol as protocol_fixtures
import test_research_claude_transport as claude_fixtures


class LocalReadTests(unittest.TestCase):
    def test_preparation_artifact_wrapper_preserves_local_read_audit(self):
        from research_harness.record_check import OfflineArtifacts
        from research_harness.review_staged_protocol import ControllerArtifacts
        with tempfile.TemporaryDirectory() as directory:
            reads = []
            artifacts = ControllerArtifacts(OfflineArtifacts(Path(directory), reads))
            protocol.read_route_runtime(artifacts, dict(adapter='claude_cli_v1'))
            self.assertEqual([row['kind'] for row in reads], ['route_runtime:claude_cli_v1'])

    def test_runtime_read_reached_during_real_parts_continuation_is_listed(self):
        from research_harness.record_check import check_record
        import test_review_parts_views as parts_fixtures
        fixture = parts_fixtures.PartsViewCase()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        fixture.prepare_parts()
        assignment = fixture.assign()
        fixture.invoke(assignment)
        report = check_record(fixture.case.root, isolate=False)
        self.assertTrue(report['ready'], report)
        self.assertTrue(any(row.get('step') == 'continuation' and row['kind'].startswith('route_runtime:')
                            for row in report['local_reads']))

    def build_case(self, adapter):
        case = protocol_fixtures.ReviewProtocolTests()
        case.setUp()
        self.addCleanup(case.doCleanups)
        if adapter == 'claude_cli_v1':
            route = dict(claude_fixtures.ROUTE, id='route')
        else:
            route = dict(id='route', adapter=adapter, model='fixture-model', endpoint='codex://local',
                         configuration=dict(max_output_tokens=1000, timeout_seconds=5))
        case.mutate(protocol.record_route, route)

        def respond(route, request, credential):
            packet = json.loads(request['input'][0]['content'][len(transport.PACKET_LABEL):])
            output = dict(allowed_control=packet['allowed_control'],
                          evidence_control=packet['evidence_control'], excluded_controls=[])
            if adapter == 'claude_cli_v1':
                from research_harness import review_claude_transport as claude
                events = claude_fixtures.clean_stream(request, json.dumps(output), model=route['model'])
                with patch.object(claude, '_cli', return_value=('/fixture/claude', '2.1.306')), \
                        patch.object(claude, '_run_owned', return_value=claude_fixtures.completed(events)):
                    return claude.send_request(route, request)
            return build_codex_response(route, request, output)

        with patch.object(transport, 'send_request', side_effect=respond):
            case.mutate(protocol.probe_route, dict(id='probe', route_id='route'), credential='fixture')
        return case

    def test_claude_and_codex_assignment_checks_plan_without_workspace_changes(self):
        with tempfile.TemporaryDirectory() as directory:
            stub = Path(directory) / 'codex'
            stub.write_text('#!/bin/sh\nprintf "codex-cli 0.0.123\\n"\n')
            stub.chmod(0o755)
            with patch.dict(os.environ, PATH=directory + os.pathsep + os.environ['PATH']):
                for adapter in ('claude_cli_v1', 'codex_cli_v1'):
                    with self.subTest(adapter=adapter):
                        case = self.build_case(adapter)
                        evidence = case.artifacts.put(b'Primary evidence for a synthetic review.', 'text/plain')
                        payload = dict(id='checked', route_id='route', dossier_id=None, role='standalone',
                                       reviewer_id='independent', author_id='author', context={'artifact_refs': [evidence]})
                        before = digest_tree(case.root)
                        result = check_mutation(case.store, protocol.record_assignment, payload,
                                                expected_revision=case.store.revision,
                                                request_id='check-local', bounded_payload=payload)
                        self.assertEqual(result['check'], 'accepted')
                        self.assertEqual(digest_tree(case.root), before)

    def test_full_local_record_checks_launch_only_codex_version_and_report_it(self):
        from research_harness.record_check import check_record
        with tempfile.TemporaryDirectory() as directory:
            stub = Path(directory) / 'codex'
            stub.write_text('#!/bin/sh\nprintf "codex-cli 0.0.123\\n"\n')
            stub.chmod(0o755)
            with patch.dict(os.environ, PATH=directory + os.pathsep + os.environ['PATH']):
                for adapter in ('claude_cli_v1', 'codex_cli_v1'):
                    case = self.build_case(adapter)
                    case.assign()
                    runtime_provenance.cache_clear()
                    real_popen = subprocess.Popen
                    calls = []

                    def version_only(arguments, *args, **kwargs):
                        calls.append(arguments)
                        self.assertEqual(arguments, [str(stub), '--version'])
                        return real_popen(arguments, *args, **kwargs)

                    before = digest_tree(case.root)
                    sentinels = []
                    with ExitStack() as stack:
                        stack.enter_context(patch('subprocess.Popen', side_effect=version_only))
                        for target in ('os.posix_spawn', 'socket.socket.connect',
                                       'research_harness.review_transport.send_request',
                                       'research_harness.review_claude_transport.send_request',
                                       'research_harness.review_codex_transport.send_request',
                                       'research_harness.review_codex_framed_transport.send_request',
                                       'research_harness.review_codex_visual_transport.send_request'):
                            sentinels.append(stack.enter_context(patch(target, side_effect=AssertionError(target))))
                        result = check_record(case.root, isolate=False)
                    for sentinel in sentinels:
                        sentinel.assert_not_called()
                    self.assertTrue(result['ready'], result)
                    self.assertEqual(digest_tree(case.root), before)
                    reads = [row for row in result['local_reads'] if row['kind'] == 'route_runtime:' + adapter]
                    self.assertTrue(reads)
                    if adapter == 'codex_cli_v1':
                        self.assertTrue(calls)
                        self.assertTrue(all(arguments == [str(stub), '--version'] for arguments in calls))
                        self.assertTrue(all(row['executable'] == str(stub) for row in reads))
                    else:
                        self.assertEqual(calls, [])


class LocalRenderReadTests(unittest.TestCase):
    def test_full_visual_probe_check_only_runs_version_and_confined_renderer(self):
        from research_harness import record_check, review_pdf_pages, review_visual_inputs
        case = protocol_fixtures.ReviewProtocolTests()
        case.setUp()
        self.addCleanup(case.doCleanups)
        with tempfile.TemporaryDirectory() as directory:
            stub = Path(directory) / 'codex'
            stub.write_text('#!/bin/sh\nprintf "codex-cli 0.0.123\\n"\n')
            stub.chmod(0o755)
            with patch.dict(os.environ, PATH=directory + os.pathsep + os.environ['PATH']):
                case.mutate(protocol.record_route, dict(id='visual', adapter='codex_cli_visual_v1',
                    model='gpt-6-astra', endpoint='codex://local',
                    configuration=dict(max_output_tokens=1000, timeout_seconds=5)))
                with patch.object(protocol, '_observed', side_effect=RuntimeError('pending probe')):
                    with self.assertRaisesRegex(RuntimeError, 'pending probe'):
                        case.mutate(protocol.probe_route, dict(id='probe', route_id='visual'), credential='fixture')
                review_pdf_pages._profile.cache_clear()
                review_visual_inputs.control_image.cache_clear()
                before = digest_tree(case.root)
                real_popen, real_spawn, calls, sentinels = subprocess.Popen, os.posix_spawn, [], []

                def local_only(arguments, *args, **kwargs):
                    calls.append(arguments)
                    if arguments[0] == str(stub):
                        self.assertEqual(arguments, [str(stub), '--version'])
                    else:
                        self.assertEqual(Path(arguments[0]).name, 'pdftoppm')
                        # Exercise the permitted renderer through posix_spawn too.
                        kwargs['close_fds'] = False
                    return real_popen(arguments, *args, **kwargs)

                def local_spawn(executable, arguments, *args, **kwargs):
                    if os.fsdecode(executable) == str(stub):
                        self.assertEqual(arguments, [str(stub), '--version'])
                    else:
                        self.assertEqual(Path(os.fsdecode(executable)).name, 'pdftoppm')
                    calls.append(arguments)
                    return real_spawn(executable, arguments, *args, **kwargs)

                with ExitStack() as stack:
                    stack.enter_context(patch('subprocess.Popen', side_effect=local_only))
                    stack.enter_context(patch('os.posix_spawn', side_effect=local_spawn))
                    for target in ('socket.socket.connect',
                                   'research_harness.review_transport.send_request',
                                   'research_harness.review_claude_transport.send_request',
                                   'research_harness.review_codex_transport.send_request',
                                   'research_harness.review_codex_framed_transport.send_request',
                                   'research_harness.review_codex_visual_transport.send_request'):
                        sentinels.append(stack.enter_context(patch(target, side_effect=AssertionError(target))))
                    result = record_check.check_record(case.root, isolate=False)
                self.assertTrue(result['ready'], result)
                for sentinel in sentinels:
                    sentinel.assert_not_called()
                clone = Path(result['clone'])
                render_calls = [args for args in calls if Path(args[0]).name == 'pdftoppm' and args[-1] != '-v']
                self.assertTrue(render_calls)
                for args in render_calls:
                    self.assertTrue(all(clone in Path(path).resolve().parents for path in args[-2:]))
                self.assertTrue(any(row['kind'] == 'pdf_render' for row in result['local_reads']))
                self.assertEqual(digest_tree(case.root), before)

    def test_visual_manifest_and_preparation_request_local_read_permission(self):
        from research_harness import review_visual_sources
        from research_harness.record_check import OfflineArtifacts
        with tempfile.TemporaryDirectory() as directory:
            reads = []
            artifacts = OfflineArtifacts(Path(directory), reads)
            with patch('research_harness.review_pdf_pages.profile', return_value={}):
                packet = review_visual_sources.prepare_packet({}, artifacts, {})
            self.assertEqual(packet[review_visual_sources.MARKER]['pages'], [])
            self.assertGreaterEqual(sum(row['kind'] == 'pdf_render' for row in reads), 2)

    def test_render_directory_is_confined_and_restored_after_a_defect(self):
        from research_harness.record_check import confine_local_reads
        with tempfile.TemporaryDirectory() as directory:
            previous = tempfile.tempdir
            with self.assertRaisesRegex(RuntimeError, 'renderer defect'):
                with confine_local_reads(Path(directory)) as render_directory:
                    self.assertEqual(os.environ['TMPDIR'], render_directory)
                    self.assertIn(Path(directory).resolve(), Path(render_directory).resolve().parents)
                    with tempfile.TemporaryDirectory() as rendered:
                        self.assertIn(Path(directory).resolve(), Path(rendered).resolve().parents)
                    raise RuntimeError('renderer defect')
            self.assertEqual(tempfile.tempdir, previous)
            self.assertFalse(Path(render_directory).exists())


if __name__ == '__main__':
    unittest.main()
