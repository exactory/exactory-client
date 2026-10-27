"""Content identity shared by the controller writer and its host hooks."""

import hashlib
import json
from functools import lru_cache
from pathlib import Path
import re

WRITER_RUNTIME_FILENAME = "writer-runtime.json"


def read_runtime_identity(launcher=None):
    plugin_root = Path(launcher).resolve().parents[1] if launcher is not None else Path(__file__).resolve().parents[4]
    return dict(_build_runtime_identity(plugin_root))


@lru_cache(maxsize=None)
def _build_runtime_identity(plugin_root):
    # Installed sources are fixed for the lifetime of a process; retain their boot-time identity.
    harness_root = plugin_root / "skills/math-solver/harness"
    source_paths = [plugin_root / "bin/exactory-math", *harness_root.glob("*.py"),
                    *(harness_root / "search_controller").glob("*.py"),
                    *(plugin_root / "research_harness").glob("*.py")]
    summary_digest = hashlib.sha256()
    for source_path in sorted(source_paths):
        summary_digest.update(source_path.relative_to(plugin_root).as_posix().encode("utf-8") + b"\0")
        summary_digest.update(source_path.read_bytes() + b"\0")
    return {"plugin_version": json.loads((plugin_root / ".claude-plugin/plugin.json").read_text())["version"],
            "controller_digest": summary_digest.hexdigest(), "launcher": str(plugin_root / "bin/exactory-math")}


def is_runtime_identity(value):
    return (isinstance(value, dict) and set(value) == {"plugin_version", "controller_digest", "launcher"}
            and isinstance(value["plugin_version"], str) and bool(value["plugin_version"].strip())
            and isinstance(value["controller_digest"], str)
            and re.fullmatch(r"[0-9a-f]{64}", value["controller_digest"]) is not None
            and isinstance(value["launcher"], str) and Path(value["launcher"]).is_absolute())
