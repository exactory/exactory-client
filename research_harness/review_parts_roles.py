"""Parts adapter registry; each adapter owns its scientific rules and series key.

An enumerator returns items with occurrences (path plus index or key), owned
body entries, and optionally the digest of the item under whose card it belongs.
The engine supplies handles, sizes, deduplication and append-only inventories.
"""

from . import (review_parts_adjudicator, review_parts_native, review_parts_problem_result,
               review_parts_repair, review_parts_study)

# find_adapter takes the first adapter that applies. The context repair adapter comes first, because it wraps
# the role adapter of a repair packet.
ADAPTERS = {'context_repair': review_parts_repair.ADAPTER,
            'problem_result': review_parts_problem_result.ADAPTER,
            'problem_adjudicator': review_parts_adjudicator.ADAPTER,
            'native_math': review_parts_native.ADAPTER,
            'study_verification': review_parts_study.VERIFICATION_ADAPTER,
            'study_readiness': review_parts_study.READINESS_ADAPTER}


def find_adapter(value, packet):
    return next((name for name, adapter in ADAPTERS.items()
                 if adapter['applies'](value, packet)), None)


def get_adapter(name):
    return ADAPTERS[name]
