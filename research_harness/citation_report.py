"""The offline citation check that submit and a production deposit run before a remote write."""

import subprocess
import sys


def report_citation_gate(workspace, check_command):
    """Run `exactory-check gate` for the workspace and print a failing report.

    The report informs the user; the calling command continues either way."""
    completed = subprocess.run(
        [sys.executable, str(check_command), "gate", "--workspace", str(workspace)],
        capture_output=True, text=True)
    if completed.returncode != 0:
        print("Citation report: " + completed.stderr.strip(), file=sys.stderr)
