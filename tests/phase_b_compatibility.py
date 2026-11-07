"""Run the released reader/writer, isolated from candidate imports."""

from functools import lru_cache
from pathlib import Path
import os
import subprocess
import sys
import tempfile


ROOT = Path(__file__).resolve().parents[1]
RELEASE = "a04617f"


@lru_cache(maxsize=1)
def released_tree():
    directory = tempfile.TemporaryDirectory(prefix="exactory-080-reader-")
    root = Path(directory.name)
    paths = subprocess.check_output(
        ["git", "ls-tree", "-r", "--name-only", RELEASE, "--", "research_harness", "bin",
         "RESEARCH_CONSTITUTION.md", ".claude-plugin/plugin.json"], cwd=ROOT, text=True)
    for relative in paths.splitlines():
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(subprocess.check_output(["git", "show", RELEASE + ":" + relative], cwd=ROOT))
    return directory, root


def run_released(script, *arguments):
    _, root = released_tree()
    return subprocess.run([sys.executable, "-c", script, *map(str, arguments)], cwd=root,
                          env=dict(os.environ, PYTHONPATH=str(root)), capture_output=True, text=True)
