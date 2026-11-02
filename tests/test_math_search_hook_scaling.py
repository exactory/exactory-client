"""A pre-tool patch is checked as one math invocation with one deadline."""

import contextlib
import io
import json
from pathlib import Path
import runpy
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'hooks'))
import math_search


class MathGuardScalingTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.workspace = Path(temporary.name).resolve()
        self.roots = [self.workspace / name for name in ('first', 'second')]
        for root in self.roots:
            (root / '.search').mkdir(parents=True)
            (root / '.search/tree.json').write_text('{}')
            (root / 'attempt').mkdir()
            (root / 'attempt/problem.json').write_text('{}')

    def invoke_patch(self, targets, script='guard_attack_files.py'):
        command = '*** Begin Patch\n' + '\n'.join(targets) + '\n*** End Patch'
        payload = {'hook_event_name': 'PreToolUse', 'session_id': 'fixture', 'cwd': str(self.workspace),
                   'tool_name': 'apply_patch', 'tool_input': {'command': command}}
        out = io.StringIO()
        with mock.patch.object(sys, 'argv', ['hook.py', script]), \
                mock.patch.object(sys, 'stdin', io.StringIO(json.dumps(payload))), contextlib.redirect_stdout(out):
            runpy.run_path(str(ROOT / 'codex/hook.py'), run_name='__main__')
        return json.loads(out.getvalue()) if out.getvalue().strip() else None

    def test_32_file_patch_queries_one_root_once_using_guard_status(self):
        targets = ['*** Add File: ' + str(self.roots[0] / ('note-{}.md'.format(i))) + '\n+note' for i in range(32)]
        with mock.patch.object(math_search, 'controller_call', return_value={}) as query:
            self.assertIsNone(self.invoke_patch(targets))
        self.assertEqual(query.call_count, 1)
        self.assertEqual(query.call_args.args[:2], (self.roots[0], 'guard-status'))

    def test_multiple_roots_each_receive_one_query(self):
        targets = ['*** Add File: ' + str(root / ('note-{}.md'.format(i))) + '\n+note'
                   for root in self.roots for i in range(4)]
        with mock.patch.object(math_search, 'controller_call', return_value={}) as query:
            self.assertIsNone(self.invoke_patch(targets))
        self.assertEqual(query.call_count, 2)
        self.assertEqual({call.args[:2] for call in query.call_args_list}, {(root, 'guard-status') for root in self.roots})

    def test_every_patch_target_and_both_ends_of_moves_keep_protection(self):
        protected = ['.search/tree.json', 'SEARCH_TREE.md', 'attempt/LINEAGE.md',
                     'attempt/journal.jsonl', 'attempt/activity.jsonl', 'attempt/tasks.json',
                     'attempt/openings.json', 'attempt/parent.json', 'attempt/units/FINISHED.json',
                     'attempt/units/1/check-unit.json', 'attempt/deterministic/job/result.json']
        for relative in protected:
            for move_from in (False, True):
                with self.subTest(relative=relative, move_from=move_from):
                    protected_path = str(self.roots[1] / relative)
                    safe = str(self.roots[1] / 'safe.md')
                    move = ['*** Update File: ' + (protected_path if move_from else safe),
                            '*** Move to: ' + (safe if move_from else protected_path), '@@', '-old', '+new']
                    with mock.patch.object(math_search, 'controller_call', return_value={}):
                        result = self.invoke_patch(['*** Add File: ' + str(self.roots[0] / 'note.md'), '+note', *move])
                    self.assertEqual(result['hookSpecificOutput']['permissionDecision'], 'deny')

    def test_all_roots_and_fallback_share_remaining_deadline(self):
        now = [100.0]
        calls = []
        (self.workspace / '.exactory').mkdir()
        (self.workspace / '.exactory/math-search.json').write_text(json.dumps({'schema_version': 1,
            'roots': [{'path': str(root), 'objective_id': 'objective-000001', 'contract_digest': 'a' * 64}
                      for root in self.roots], 'sessions': {}}))

        def run(argv, **kwargs):
            calls.append((argv, kwargs['timeout']))
            now[0] += 6
            # First root succeeds; qualified second-root call fails operationally.
            return subprocess.CompletedProcess(argv, 1 if len(calls) == 2 else 0, '{}', 'operation failed')

        targets = ['*** Add File: ' + str(root / 'note.md') + '\n+note' for root in self.roots]
        with mock.patch.object(math_search.time, 'monotonic', side_effect=lambda: now[0]), \
                mock.patch.object(math_search.subprocess, 'run', side_effect=run):
            result = self.invoke_patch(targets)
        self.assertEqual(result['hookSpecificOutput']['permissionDecision'], 'deny')
        self.assertEqual([timeout for _, timeout in calls], [20, 14, 8])
        self.assertTrue(all(argv[5:7] == ['guard-status', '--json'] for argv, _ in calls))
        self.assertNotIn('--objective-id', calls[-1][0])

    def test_expired_invocation_does_not_start_another_root_or_fallback(self):
        now = [100.0]
        calls = []

        def run(argv, **kwargs):
            calls.append(argv)
            now[0] += 21
            return subprocess.CompletedProcess(argv, 0, '{}', '')

        with mock.patch.object(math_search.time, 'monotonic', side_effect=lambda: now[0]), \
                mock.patch.object(math_search.subprocess, 'run', side_effect=run):
            result = self.invoke_patch(['*** Add File: ' + str(root / 'note.md') for root in self.roots])
        self.assertIsNotNone(result, 'An expired invocation must deny the remaining operation')
        self.assertEqual(result['hookSpecificOutput']['permissionDecision'], 'deny')
        self.assertEqual(len(calls), 1)

    def test_native_read_exemption_still_validates_discovery_first(self):
        (self.workspace / '.exactory').mkdir()
        (self.workspace / '.exactory/math-search.json').write_text('{invalid')
        payload = {'cwd': str(self.workspace), 'tool_name': 'Read', 'tool_input': {'file_path': 'notes.md'}}
        with self.assertRaises(ValueError):
            math_search.guard(payload)

    def test_unexpected_pre_tool_guard_exception_explicitly_denies(self):
        with mock.patch.object(math_search, 'discover', side_effect=RuntimeError('Unexpected discovery failure')):
            result = self.invoke_patch(['*** Add File: ' + str(self.roots[0] / 'note.md')])
        self.assertIsNotNone(result, 'Unexpected guard exceptions must not allow the call')
        self.assertEqual(result['hookSpecificOutput']['permissionDecision'], 'deny')
        self.assertIn('Unexpected discovery failure', result['hookSpecificOutput']['permissionDecisionReason'])

    def test_other_hooks_keep_per_file_payloads(self):
        adapter = runpy.run_path(str(ROOT / 'codex/hook.py'))
        payload = {'hook_event_name': 'PreToolUse', 'cwd': str(self.workspace), 'tool_name': 'apply_patch',
            'tool_input': {'command': '*** Begin Patch\n*** Add File: first/a.md\n+a\n'
                                     '*** Delete File: second/b.md\n*** End Patch'}}
        self.assertEqual(len(adapter['build_file_payloads'](payload, 'enforce_unit_flow.py')), 2)
        payload['hook_event_name'] = 'PostToolUse'
        self.assertEqual(len(adapter['build_file_payloads'](payload, 'record_attack_activity.py')), 2)


if __name__ == '__main__':
    unittest.main()
