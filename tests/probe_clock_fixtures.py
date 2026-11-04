"""Share a controlled probe clock with real workers and their watchdogs."""

from contextlib import ExitStack
from pathlib import Path
import runpy
import subprocess
import sys
from unittest.mock import patch


class ProbeClock:
    def __init__(self, path):
        self.path = Path(path).resolve()
        self.set_time(0)

    def read_time(self):
        return float(self.path.read_text())

    def set_time(self, seconds):
        pending = self.path.with_suffix(".pending")
        pending.write_text(str(seconds))
        pending.replace(self.path)

    def build_command(self, command):
        return [command[0], str(Path(__file__).resolve()), str(self.path), "cli",
                *command[1:]]


def run_clocked_probe(clock_path, role, arguments):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from research_harness import probe, probe_supervision, probe_worker

    # Opening an existing clock must not reset the time in another process.
    def read_time():
        return float(Path(clock_path).read_text())

    start_process = subprocess.Popen

    def start_clocked_process(command, *args, **kwargs):
        if command[1:3] == ["-m", "research_harness.probe_worker"]:
            command = [command[0], __file__, clock_path, "worker", *command[3:]]
        elif len(command) > 1 and command[1] == probe_supervision.__file__:
            command = [command[0], __file__, clock_path, "supervisor", *command[2:]]
        return start_process(command, *args, **kwargs)

    with ExitStack() as stack:
        for module in (probe, probe_worker, probe_supervision):
            stack.enter_context(patch.object(module, "read_probe_clock", read_time))
        stack.enter_context(patch.object(subprocess, "Popen", start_clocked_process))
        if role == "cli":
            sys.argv = arguments
            runpy.run_path(arguments[0], run_name="__main__")
        elif role == "worker":
            root, directory, digest, owner = arguments
            probe_worker.run_probe_worker(Path(root), directory, digest, int(owner))
        elif role == "supervisor":
            descriptor, ready, owner, root, directory, digest, *argv = arguments
            probe_supervision.run_probe_program(
                int(descriptor), int(ready), int(owner), root, directory, digest, argv)
        else:
            raise ValueError("Unknown probe process role: " + role)


if __name__ == "__main__":
    run_clocked_probe(sys.argv[1], sys.argv[2], sys.argv[3:])
