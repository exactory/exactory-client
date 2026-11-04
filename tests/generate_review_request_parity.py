"""Generate the oracle using only harness code exported from ceb050b.

Run from the worktree root with Python 3.12:
    PYTHONDONTWRITEBYTECODE=1 python3.12 tests/generate_review_request_parity.py

The parent imports no harness code. Only the replay helper is overlaid onto a
clean git archive; existing fixture builders and stored sources come from that
archive. The child runs with the archive as both cwd and its Python import root.
"""

import argparse
import gzip
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile


BASE_COMMIT = "ceb050b26e6bdc372d06e6003836e1e3d8fc0a82"
REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
HELPER_FILENAME = "review_request_parity_fixtures.py"
DEFAULT_OUTPUT_PATH = REPOSITORY_ROOT / "tests/fixtures/review-request-parity-0.72.1.json.gz"


def collect(output):
    import review_request_parity_fixtures as fixtures
    if (REPOSITORY_ROOT / ".git").exists() or Path(fixtures.protocol.__file__).resolve().parents[1] != REPOSITORY_ROOT:
        raise AssertionError("Collect only inside the clean archive, with its own harness imports")
    if fixtures.BASE_COMMIT != BASE_COMMIT:
        raise AssertionError("The generator and replay helper must pin the same base")
    document = dict(source_commit=BASE_COMMIT, serialization="review_protocol._put_json", cases={})
    for name, build in fixtures.CASE_BUILDERS.items():
        print("Freezing " + name, flush=True)
        document["cases"][name] = build()
    data = json.dumps(document, sort_keys=True, ensure_ascii=False, allow_nan=False, indent=1).encode() + b"\n"
    output.write_bytes(gzip.compress(data, mtime=0))
    print("Wrote %d bytes (%d uncompressed)" % (output.stat().st_size, len(data)), flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT_PATH)
    parser.add_argument("--archive-child", action="store_true", help=argparse.SUPPRESS)
    options = parser.parse_args()
    output = options.output.resolve()
    if options.archive_child:
        collect(output)
        return
    with tempfile.TemporaryDirectory(prefix="review-request-parity-") as temporary:
        directory = Path(temporary)
        archive = directory / "base.tar"
        with archive.open("wb") as stream:
            subprocess.run(["git", "archive", BASE_COMMIT], cwd=REPOSITORY_ROOT, stdout=stream, check=True)
        export = directory / "base"
        export.mkdir()
        subprocess.run(["tar", "-xf", str(archive), "-C", str(export)], check=True)
        for source in (Path(__file__), REPOSITORY_ROOT / "tests" / HELPER_FILENAME):
            shutil.copyfile(source, export / "tests" / source.name)
        environment = dict(os.environ, PYTHONDONTWRITEBYTECODE="1", PYTHONPATH="tests")
        subprocess.run([sys.executable, "-m", Path(__file__).stem, "--archive-child",
                        "--output", str(output)], cwd=export, env=environment, check=True)


if __name__ == "__main__":
    main()
