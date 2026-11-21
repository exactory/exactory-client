"""Every test gets the same answer about the reviewer programs of the machine, and a test that asks the machine stops."""
import importlib.util
import io
import os
import shutil
import subprocess
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import patch

from ci.reviewer_program_guard import (CHILD_READ_MSG, MACHINE_READ_MSG, MachineReadError, REFUSING_STUB,
                                       REVIEWER_PROGRAMS, is_guard_installed, take_machine_reads,
                                       use_absent_programs)
from research_harness import review_claude_transport as claude
from research_harness import review_codex_framed_transport as framed
from research_harness import review_codex_transport as codex
from research_harness import review_codex_visual_transport as visual
from research_harness import review_state_evaluation as states
from research_harness import review_transport as transport
from research_harness.errors import ResearchError

ROOT = Path(__file__).resolve().parents[1]
STUB = '#!/bin/sh\nprintf "{name}-cli 0.0.123\\n"\n'


class ReviewerProgramGuardTests(unittest.TestCase):
    def setUp(self):
        # These tests reach the guard on purpose. The runner must not count their lookups against them.
        self.addCleanup(take_machine_reads)

    def add_program_directory(self, *names):
        directory = tempfile.TemporaryDirectory(prefix='reviewer-programs-')
        self.addCleanup(directory.cleanup)
        for name in names:
            program = Path(directory.name) / name
            program.write_text(STUB.format(name=name))
            program.chmod(0o755)
        return directory.name

    def load_runner(self):
        spec = importlib.util.spec_from_file_location('ci_shards_with_guard', ROOT / 'ci' / 'unittest_shards.py')
        runner = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(runner)
        return runner

    def test_this_run_has_the_guard(self):
        self.assertTrue(is_guard_installed(),
                        'Run test modules with python3 ci/unittest_shards.py run-modules, which installs the guard')

    def test_a_lookup_that_the_machine_would_answer_stops_the_test(self):
        for program in REVIEWER_PROGRAMS:
            with self.subTest(program=program), self.assertRaises(MachineReadError) as stopped:
                shutil.which(program)
            self.assertEqual(str(stopped.exception), MACHINE_READ_MSG.format(program=program))
        self.assertEqual(len(take_machine_reads()), 2)

    def test_each_runtime_reader_of_the_code_under_test_stops_at_the_guard(self):
        route = dict(adapter='codex_cli_v1')
        readers = dict(codex=codex.runtime, framed=framed.runtime, visual=visual.runtime, claude=claude._cli,
                       transport=lambda: transport.runtime(route))
        for name, reader in readers.items():
            with self.subTest(reader=name), self.assertRaises(MachineReadError):
                reader()

    def test_the_code_under_test_cannot_turn_the_stop_into_an_ordinary_refusal(self):
        # _runtime_fingerprint catches Exception and returns None. The stop of the guard passes through it.
        store = types.SimpleNamespace(read_runtime_fingerprint=transport.runtime)
        records = dict(review_route=dict(route=dict(adapter='codex_cli_v1')))
        with self.assertRaises(MachineReadError):
            states._runtime_fingerprint(records, types.SimpleNamespace(store=store))

    def test_other_programs_are_found_as_on_the_machine(self):
        self.assertIsNotNone(shutil.which('sh'))
        self.assertIsNone(shutil.which('no-such-program-of-this-test'))
        self.assertEqual(take_machine_reads(), [])

    def test_a_stub_in_a_directory_of_the_test_is_the_installed_program(self):
        directory = self.add_program_directory('codex', 'claude')
        with patch.dict(os.environ, PATH=directory + os.pathsep + os.environ['PATH']):
            self.assertEqual(shutil.which('codex'), str(Path(directory) / 'codex'))
            self.assertEqual(codex.runtime()['version'], 'codex-cli 0.0.123')
            self.assertEqual(claude._cli(), (str(Path(directory) / 'claude'), 'claude-cli 0.0.123'))
        self.assertEqual(take_machine_reads(), [])

    def test_a_path_of_test_directories_alone_gives_its_own_answer(self):
        directory = self.add_program_directory()
        with patch.dict(os.environ, PATH=directory):
            self.assertIsNone(shutil.which('codex'))
        self.assertIsNone(shutil.which('claude', path=directory))
        self.assertEqual(take_machine_reads(), [])

    def test_a_directory_outside_the_temporary_directory_is_a_directory_of_the_machine(self):
        # This directory was not on PATH when the guard was installed, and the test did not make it.
        directory = os.path.dirname(os.__file__)
        with self.assertRaises(MachineReadError):
            shutil.which('codex', path=directory)
        with patch.dict(os.environ, PATH=directory), self.assertRaises(MachineReadError):
            shutil.which('claude')

    def test_a_test_states_that_the_machine_has_no_reviewer_program(self):
        use_absent_programs(self)
        for reader in (codex.runtime, framed.runtime, visual.runtime, claude._cli):
            with self.assertRaises(ResearchError) as refused:
                reader()
            self.assertEqual(refused.exception.code, 'review_runtime_missing')
        self.assertIsNotNone(shutil.which('sh'))
        self.assertEqual(take_machine_reads(), [])

    def test_a_child_process_finds_a_stub_that_refuses(self):
        for program in REVIEWER_PROGRAMS:
            child = subprocess.run([program, '--version'], capture_output=True, text=True)
            self.assertEqual(child.returncode, 127)
            self.assertEqual(child.stderr, CHILD_READ_MSG.format(program=program) + '\n')
        # The readers of a child process take the refusal as a program without a readable version.
        script = Path(self.add_program_directory()) / 'read_codex_runtime.py'
        script.write_text('from research_harness import review_codex_transport as codex\n'
                          'from research_harness.errors import ResearchError\n'
                          'try:\n    codex.runtime()\nexcept ResearchError as error:\n    print(error.code)\n')
        child = subprocess.run([sys.executable, str(script)], capture_output=True, text=True,
                               env=dict(os.environ, PYTHONPATH=str(ROOT)))
        self.assertEqual(child.stdout.strip(), 'review_runtime_missing', child.stderr)
        reads = take_machine_reads()
        self.assertEqual(len(reads), 3)
        self.assertTrue(all(str(read).startswith('A child process of this test started') for read in reads))
        # The report of a start names the command of the process that started the stub.
        self.assertIn(str(script), str(reads[2]))

    def test_the_stub_prints_its_message_as_written(self):
        message = 'A text with "double quotes", \'single quotes\', $HOME, `date` and a backslash \\n stays as written.'
        directory = self.add_program_directory()
        stub = Path(directory) / 'codex'
        stub.write_text(REFUSING_STUB.format(program='codex', message=message))
        stub.chmod(0o755)
        child = subprocess.run([str(stub)], capture_output=True, text=True)
        self.assertEqual(child.returncode, 127)
        self.assertEqual(child.stderr, message + '\n')
        self.assertEqual(len(list(Path(directory).glob('codex.*.start'))), 1)

    def test_a_forked_child_leaves_the_stubs_of_its_parent(self):
        # A child that os.fork made and that ends with sys.exit runs the exit handlers of its parent.
        script = ('import os, sys\n'
                  'from ci import reviewer_program_guard\n'
                  'reviewer_program_guard.install_guard()\n'
                  'stubs = os.environ["PATH"].split(os.pathsep)[0]\n'
                  'child = os.fork()\n'
                  'if child == 0:\n'
                  '    sys.exit(0)\n'
                  'os.waitpid(child, 0)\n'
                  'print(os.path.isdir(stubs))\n')
        child = subprocess.run([sys.executable, '-c', script], capture_output=True, text=True, cwd=ROOT)
        self.assertEqual(child.stdout.strip(), 'True', child.stderr)

    def test_a_test_that_states_absent_programs_can_start_child_processes(self):
        class Sample(unittest.TestCase):
            def test_child(self):
                use_absent_programs(self)
                self.assertEqual(subprocess.run(['codex', '--version'], capture_output=True).returncode, 127)

        result = unittest.TestResult()
        Sample('test_child').run(result)
        self.assertTrue(result.wasSuccessful(), result.errors + result.failures)
        self.assertEqual(take_machine_reads(), [])

    def test_the_runner_fails_a_test_that_passes_after_a_stopped_lookup(self):
        class Sample(unittest.TestCase):
            def test_catches_the_stop(self):
                try:
                    shutil.which('codex')
                except BaseException:
                    pass

            def test_reads_nothing(self):
                pass

            def test_starts_a_child_that_reads(self):
                subprocess.run(['claude', '--version'], capture_output=True)

        suite = unittest.TestSuite([Sample('test_catches_the_stop'), Sample('test_reads_nothing'),
                                    Sample('test_starts_a_child_that_reads')])
        report = self.load_runner().run_shard(suite, 0, 1, {}, stream=io.StringIO(),
                                              take_machine_reads=take_machine_reads)
        self.assertFalse(report['success'])
        self.assertEqual([name.rpartition('.')[2] for name in report['errors']],
                         ['test_catches_the_stop', 'test_starts_a_child_that_reads'])
        self.assertEqual(report['tests_run'], 3)

    def test_the_runner_charges_a_read_of_a_class_fixture_to_the_next_test(self):
        class Sample(unittest.TestCase):
            @classmethod
            def setUpClass(cls):
                subprocess.run(['codex', '--version'], capture_output=True)

            def test_after_the_class_fixture(self):
                pass

        stream = io.StringIO()
        report = self.load_runner().run_shard(unittest.TestSuite([Sample('test_after_the_class_fixture')]), 0, 1, {},
                                              stream=stream, take_machine_reads=take_machine_reads)
        self.assertEqual([name.rpartition('.')[2] for name in report['errors']], ['test_after_the_class_fixture'])
        self.assertIn('started the reviewer program codex', stream.getvalue())


if __name__ == '__main__':
    unittest.main()
