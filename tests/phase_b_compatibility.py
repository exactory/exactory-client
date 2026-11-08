"""Run the released reader/writer, isolated from candidate imports."""

from functools import lru_cache
from pathlib import Path
import hashlib
import os
import subprocess
import sys
import tarfile
import tempfile


ROOT = Path(__file__).resolve().parents[1]
RELEASE = "a04617fc2f6d5ca760ad770179149824e1570580"
RELEASE_ARCHIVE_SHA256 = "da54601176c868942546b5a22630d9fe6c190f2a0e7c8e837d92a9ed28ce1b6a"


@lru_cache(maxsize=1)
def released_tree():
    directory = tempfile.TemporaryDirectory(prefix="exactory-080-reader-")
    root = Path(directory.name)
    # Preserve the released readers even in CI's shallow checkout.
    archive = ROOT / "tests/fixtures/record_readers_0_80_0.tar.gz"
    if hashlib.sha256(archive.read_bytes()).hexdigest() != RELEASE_ARCHIVE_SHA256:
        raise AssertionError("Released 0.80.0 reader archive checksum mismatch")
    with tarfile.open(archive, "r:gz") as source:
        if source.pax_headers.get("comment") != RELEASE:
            raise AssertionError("Released 0.80.0 reader archive commit mismatch")
        if sys.version_info >= (3, 12):
            source.extractall(root, filter="data")
        else:
            source.extractall(root)
    return directory, root


def run_released(script, *arguments):
    _, root = released_tree()
    return subprocess.run([sys.executable, "-c", script, *map(str, arguments)], cwd=root,
                          env=dict(os.environ, PYTHONPATH=str(root)), capture_output=True, text=True)
