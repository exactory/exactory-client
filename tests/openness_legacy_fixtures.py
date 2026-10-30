"""The reading chain of release 0.65.2, so that a test builds a historical chain with the code of that release.

The fixtures hold the exact bytes of openness_reading.py and review_excerpts.py at 448b6da, which 0.66.0 to 0.66.2 also
kept, pinned by SHA-256. CI checks out a single commit, so the bytes are kept here and not read from the history.
"""

import contextlib
import hashlib
import importlib.util
from pathlib import Path
from unittest.mock import patch

from research_harness import review_protocol as protocol

# The modules of release 0.65.2 (448b6da) that built its reading chains, each with the SHA-256 of its exact bytes.
RELEASE_0_65_2_MODULES = (
    ('review_excerpts', 'fixtures/review_excerpts_0_65_2.py.txt',
     '4070b149c027bb248bbc6912492417c6cd44c7f0486073da43ad7bf5f45b80b3'),
    ('openness_reading', 'fixtures/openness_reading_0_65_2.py.txt',
     '4dfe0e00d8f5819c870dcd3c910be6e90750f5d351e0a8bd353342c9f15e6bf8'))


def load_0_65_2_openness_reading():
    """The openness reading module of 0.65.2 with the review_excerpts module of 0.65.2. Both run inside the
    research_harness package, so their other imports resolve to the current modules."""
    modules = {}
    for name, path, sha256 in RELEASE_0_65_2_MODULES:
        location = Path(__file__).resolve().parent / path
        source = location.read_bytes()
        if hashlib.sha256(source).hexdigest() != sha256:
            raise AssertionError('The 0.65.2 source fixture changed: ' + path)
        spec = importlib.util.spec_from_loader('research_harness.%s_0_65_2' % name, loader=None)
        module = importlib.util.module_from_spec(spec)
        exec(compile(source, str(location), 'exec'), module.__dict__)
        modules[name] = module
    modules['openness_reading'].review_excerpts = modules['review_excerpts']
    return modules['openness_reading']


@contextlib.contextmanager
def build_as_0_65_2():
    """Record native_math assignments with the reading chain of 0.65.2: its v2 prompts, plan entries without a kind, its
    verbose reading record and no record units."""
    with patch.object(protocol, 'openness_reading', load_0_65_2_openness_reading()):
        yield
