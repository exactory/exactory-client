"""Give every test the same answer about the reviewer programs of the machine.

A reviewer program is the codex or the claude command-line program. The code under test finds one with
shutil.which. In a test run, the answer comes from the test in one of three ways:

- The test patches the lookup or a reader above it.
- The test puts a program in a temporary directory and puts that directory on PATH.
- The test calls use_absent_programs.

A lookup that the machine would answer stops the test with MachineReadError. This is a lookup whose searched path
holds a directory of the machine, while no directory of the test on that path holds the program. A child process of
a test finds a stub that records its start and refuses every call. As a result, the child process gets the same
answer on every machine.

ci/unittest_shards.py installs the guard before the first test module loads. Its command run does this for the CI
shards of the plugin tests and for the CI job of the harness tests. Its command run-modules does this for named
test modules, as a lane runs them. The runner also fails a test that passed after a stopped lookup or a stub start.

A plain python3 -m unittest run has no guard, whether it names a module tests.<module> or <module>. The CI step
that measures the coverage of test_codex.py has no guard either. The CI shards run those tests again with the
guard.

The guard has three limits. A stop in a class fixture or a module fixture ends the whole run with a traceback,
because unittest catches only Exception there. A child process that builds its own PATH does not find the stub, so
it can still read the machine. A run that a signal kills leaves its stub directory behind.
"""
import atexit
import os
from pathlib import Path
import shutil
import tempfile
from unittest import mock

REVIEWER_PROGRAMS = ('codex', 'claude')
MACHINE_READ_MSG = (
    'This test looked for the reviewer program {program} on the machine. '
    'A test must get each reviewer program from the test itself, because the result must be the same on every '
    'machine. The traceback shows the function that made the lookup. '
    'If runtime() of a Codex adapter made the lookup, read the Codex route records of the test. '
    'If a record has the member runtime, patch runtime() of its adapter to return that member. '
    'If the test records the route itself, call codex_route_fixtures.install_codex_runtime(self) before the test '
    'records it. '
    'If review_claude_transport._cli made the lookup and the test needs an installed program, patch _cli. '
    "Make that patch return a path and a version, for example ('/fixture/claude', 'fixture-version'). "
    'If the test is about the program itself, make a directory with tempfile.TemporaryDirectory(). '
    'Put an executable stub with the name {program} in that directory. '
    'Put the directory first on PATH. '
    'In all other cases, call ci.reviewer_program_guard.use_absent_programs(self). '
    'The test then gets the answer of a machine without the program.')
CHILD_READ_MSG = (
    'This test or one of its child processes started the reviewer program {program}. '
    'In a test run, the first program with this name on PATH is a stub that refuses every call. '
    'If the process that started the program needs an installed program, make a directory with '
    'tempfile.TemporaryDirectory(). '
    'Put an executable stub with the name {program} in that directory. '
    'Put the directory first on the PATH of that process. '
    'In all other cases, call ci.reviewer_program_guard.use_absent_programs(self) in the test. '
    'The stub then refuses each call as before, and the runner does not count the start against the test.')
CHILD_COMMAND_MSG = 'The command of the process that started the stub: {command}'
# The stub takes its tools from /usr/bin and /bin, whatever PATH its process has. It writes the command of the
# process that started it into a file of its own. It renames the file when the file is complete, so the runner
# never reads a part of it. The quoted here-document prints the message as written.
REFUSING_STUB = '''#!/bin/sh
PATH=/usr/bin:/bin
if record=$(mktemp "$(dirname "$0")/{program}.XXXXXX"); then
  ps -ww -o args= -p "$PPID" > "$record" 2>/dev/null
  mv "$record" "$record.start"
fi
cat >&2 <<'END_OF_MESSAGE'
{message}
END_OF_MESSAGE
exit 127
'''
_stopped_lookups = []
_installed_lookup = None
_stub_directory = None


class MachineReadError(BaseException):
    """A test asked the machine for a reviewer program. It is not an Exception, so a place of the code under test
    that catches Exception passes it on. The code under test also catches BaseException at some places. For those
    places, the runner fails a test that passed after the stop (ci/unittest_shards.py)."""


def install_guard():
    """Put the refusing stubs first on PATH and replace shutil.which, once for this process."""
    global _installed_lookup, _stub_directory
    if _installed_lookup is not None:
        return
    _stub_directory = Path(tempfile.mkdtemp(prefix='reviewer-program-stubs-'))
    atexit.register(_remove_stub_directory, os.getpid())
    for program in REVIEWER_PROGRAMS:
        stub = _stub_directory / program
        stub.write_text(REFUSING_STUB.format(program=program, message=CHILD_READ_MSG.format(program=program)))
        stub.chmod(0o755)
    os.environ['PATH'] = str(_stub_directory) + os.pathsep + os.environ.get('PATH', os.defpath)
    machine_directories = frozenset(os.get_exec_path())
    temporary_root = os.path.join(os.path.realpath(tempfile.gettempdir()), '')
    find_on_machine = shutil.which

    def is_test_directory(directory):
        # A directory of the test lies under the temporary directory of the run (tempfile.gettempdir()), and it
        # was not on PATH when the guard was installed. Every other directory of a searched path is a directory
        # of the machine. This includes a directory outside the temporary directory that a test put on PATH.
        return directory not in machine_directories and os.path.realpath(directory).startswith(temporary_root)

    def find_program(cmd, mode=os.F_OK | os.X_OK, path=None):
        if os.fsdecode(cmd) not in REVIEWER_PROGRAMS:
            return find_on_machine(cmd, mode=mode, path=path)
        searched = (os.environ.get('PATH', os.defpath) if path is None else os.fsdecode(path)).split(os.pathsep)
        test_directories = [directory for directory in searched if is_test_directory(directory)]
        found = find_on_machine(cmd, mode=mode, path=os.pathsep.join(test_directories)) if test_directories else None
        if found is None and len(test_directories) < len(searched):
            error = MachineReadError(MACHINE_READ_MSG.format(program=os.fsdecode(cmd)))
            _stopped_lookups.append(error)
            raise error
        return found

    _installed_lookup = shutil.which = find_program


def _remove_stub_directory(owner):
    # A child that os.fork made runs the exit handlers of its parent. The stubs stay for the parent.
    if os.getpid() == owner:
        shutil.rmtree(_stub_directory, ignore_errors=True)


def is_guard_installed():
    return _installed_lookup is not None


def _take_stub_starts():
    """Return the program and the command of the starting process for each stub start since the last call."""
    if _stub_directory is None:
        return []
    starts = []
    for record in sorted(_stub_directory.glob('*.start'), key=lambda path: path.stat().st_mtime_ns):
        starts.append((record.name.partition('.')[0], record.read_text(errors='replace').strip()))
        record.unlink()
    return starts


def take_machine_reads():
    """Return one MachineReadError for each lookup that the guard stopped and for each stub start since the last
    call."""
    stopped = _stopped_lookups[:]
    del _stopped_lookups[:len(stopped)]
    return stopped + [MachineReadError(CHILD_READ_MSG.format(program=program) + ' '
                                       + CHILD_COMMAND_MSG.format(command=command or 'not readable'))
                      for program, command in _take_stub_starts()]


def use_absent_programs(case):
    """State for one test that the machine has no reviewer program.

    Until the test ends, every lookup of a reviewer program in the test process returns None, as on a machine
    without the programs. In a run with the guard, a child process of the test still finds the refusing stub. Its
    lookup returns the path of the stub, and its runtime readers report review_runtime_missing, as they do without
    the program. The runner does not count the stub starts of the test against it. In a run without the guard, a
    child process finds what the machine has.

    Call it in a test or in setUp, with the test as the argument. A class fixture or a module fixture cannot call
    it, because it registers its cleanup on the test.
    """
    find_program = shutil.which

    def find_absent(cmd, mode=os.F_OK | os.X_OK, path=None):
        return None if os.fsdecode(cmd) in REVIEWER_PROGRAMS else find_program(cmd, mode=mode, path=path)

    patcher = mock.patch.object(shutil, 'which', find_absent)
    patcher.start()
    case.addCleanup(patcher.stop)
    case.addCleanup(_take_stub_starts)
