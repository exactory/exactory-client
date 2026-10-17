"""The Version 6 research constitution that release 0.50.1 distributed.

A store that adopted Version 6 keeps its reports byte-identical under the
current distribution (research_harness.principles.PREVIOUS_CONSTITUTION). Tests
of that rule build the store while Version 6 is the distributed contract.
"""

from pathlib import Path
from unittest.mock import patch

from research_harness import principles

VERSION_SIX_PATH = Path(__file__).resolve().parent / "fixtures" / "research" / "constitution-v6.md"


def distribute_version_six():
    """Make Version 6 the distributed constitution while a store is built under it."""
    return patch.object(principles, "CONSTITUTION_PATH", VERSION_SIX_PATH)
