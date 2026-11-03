"""Parts adapter registry; each adapter owns its scientific rules and series key.

An enumerator returns items with occurrences (path plus index or key), owned
body entries, and optionally the digest of the item under whose card it belongs.
The engine supplies handles, sizes, deduplication and append-only inventories.
"""

from . import review_parts_problem_result

ADAPTERS = {'problem_result': review_parts_problem_result.ADAPTER}


def find_adapter(value, packet):
    return next((name for name, adapter in ADAPTERS.items()
                 if adapter['applies'](value, packet)), None)


def get_adapter(name):
    return ADAPTERS[name]
