#!/usr/bin/env python3
"""Installed stable launcher; invoked by an absolute interpreter with -I -B."""
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
from hook_runtime.dispatch import main

if __name__ == "__main__":
    sys.exit(main(Path(__file__).resolve().parents[1]))
