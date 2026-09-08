"""Persistent mathematical search controller primitives."""

from pathlib import Path
import sys

# Direct controller imports, attack.py and the detached launcher all resolve
# their distributed common harness independently of CWD or ambient PYTHONPATH.
sys.path.insert(0, str(Path(__file__).resolve().parents[4]))

from .errors import SearchError
from .storage import Store, canonical_bytes, safe_path

__all__ = ["SearchError", "Store", "canonical_bytes", "safe_path"]
