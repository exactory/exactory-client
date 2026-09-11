"""Identity of the running build. Recorded next to receipts; never a gate input."""

import hashlib
import json
import shutil
import subprocess
from functools import lru_cache
from pathlib import Path

from .storage import _SCHEMA_VERSION


_ROOT = Path(__file__).resolve().parents[1]


def plugin_version(root=None):
    root = Path(root) if root else _ROOT
    return json.loads((root / ".claude-plugin/plugin.json").read_text(encoding="utf-8"))["version"]


def _git(root):
    if not (root / ".git").exists() or shutil.which("git") is None:
        return None, None
    try:
        commit = subprocess.run(["git", "-C", str(root), "rev-parse", "HEAD"], capture_output=True, text=True, timeout=5)
        status = subprocess.run(["git", "-C", str(root), "status", "--porcelain"], capture_output=True, text=True, timeout=5)
    except (OSError, subprocess.SubprocessError):
        return None, None
    if commit.returncode or status.returncode:
        return None, None
    return commit.stdout.strip(), bool(status.stdout.strip())


def _package_digest(root):
    paths = list((root / "research_harness").glob("*.py")) + list((root / "bin").iterdir()) + [root / "RESEARCH_CONSTITUTION.md"]
    summary = hashlib.sha256()
    for path in sorted(p for p in paths if p.is_file()):
        summary.update(path.relative_to(root).as_posix().encode("utf-8") + b"\0" + path.read_bytes() + b"\0")
    return summary.hexdigest()


@lru_cache(maxsize=None)
def runtime_provenance(root=None):
    """Describe the code that is running: version, commit, dirtiness, digest and policy.

The build cannot change while this process runs, so the value is computed once per process.
"""
    root = Path(root) if root else _ROOT
    commit, dirty = _git(root)
    constitution = (root / "RESEARCH_CONSTITUTION.md").read_bytes()
    version = next((line[len("Version: "):].strip() for line in constitution.decode("utf-8").splitlines()
                    if line.startswith("Version: ")), None)
    return {"plugin_version": plugin_version(root), "source_commit": commit, "dirty": dirty,
            "executable": str(_ROOT / "bin" / "exactory-research"), "package_digest": _package_digest(root),
            "schema_version": _SCHEMA_VERSION,
            "constitution": {"version": version, "sha256": hashlib.sha256(constitution).hexdigest()}}
