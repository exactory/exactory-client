"""Persistent mathematical search controller primitives."""

from pathlib import Path
import sys

# Direct controller imports, attack.py and the detached launcher all resolve
# their distributed common harness independently of CWD or ambient PYTHONPATH.
plugin_root = str(Path(__file__).resolve().parents[4])
if plugin_root not in sys.path:
    sys.path.insert(0, plugin_root)

from .errors import SearchError
from .storage import Store, canonical_bytes, safe_path

__all__ = ["SearchError", "Store", "canonical_bytes", "safe_path"]
