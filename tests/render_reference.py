"""Render with the integration base's legacy parts verification and validation."""

from contextlib import contextmanager
import hashlib
from pathlib import Path
from unittest.mock import patch

from research_harness import output_views, review_parts

from test_research_output_views import read_tree


REFERENCE = Path(__file__).with_name('fixtures') / 'review_parts_reference.py.txt'
REFERENCE_DIGEST = '2eb26f85795de8dda9d0c76417d404f63b9e745072aed0579fa254081e5ac2ff'
REFERENCE_FUNCTIONS = (
    '_list_delivered_texts', '_verify_statements', '_validate_read', '_validate_check',
    '_list_series', 'observe_series', 'verify_series',
)


@contextmanager
def use_reference_functions():
    source = REFERENCE.read_bytes()
    if hashlib.sha256(source).hexdigest() != REFERENCE_DIGEST:
        raise AssertionError('The render reference differs from the pinned integration base')
    # Isolate the fixture definitions, then use the real module globals for
    # their calls so both renders see the same dependencies and test patches.
    namespace = dict(vars(review_parts))
    exec(compile(source, str(REFERENCE), 'exec'), namespace)
    from types import FunctionType
    functions = {}
    for name in REFERENCE_FUNCTIONS:
        original = namespace[name]
        function = FunctionType(original.__code__, vars(review_parts), name)
        function.__defaults__ = original.__defaults__
        function.__kwdefaults__ = original.__kwdefaults__
        functions[name] = function
    with patch.multiple(review_parts, **functions):
        yield


def read_render_outcome(root):
    marker = output_views._read_marker(root)
    for entry in marker['files'].values():
        entry.pop('mtime_ns', None)
    return read_tree(root), marker
