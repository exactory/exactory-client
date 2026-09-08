"""Portable old writers for historical accounting fixtures, never fresh authority.

Only admission, admission validation and binding are copied from commit
f5b3c46bb22424f3a0d5e836ba9c21e7fbcff51c. Their real preparation and storage
helpers are unchanged. The isolated namespace leaves every current permission
guard intact. The fixture needs no Git history, old installation or worktree.
"""

import hashlib
from pathlib import Path
from types import SimpleNamespace


SOURCE_SHA256 = "63aabbda35ed0dcda4f4c5d32c9970325f23b3884946c21edbd6a4074c893099"
FUNCTION_SOURCES = {
    "admit_execution": ("research_harness/development.py", "ad7c8e9f7d4c7af5932b961a37e9ad204ab97641b876af128e2fb65ed7601a3d"),
    "validate_admitted_execution": ("research_harness/development.py", "4f576f7277caa921d2e91f07600efbf921a56ac82041e4fb8b34edcc7f3514d2"),
    "bind_execution": ("research_harness/execution.py", "ad667b18dbb3f166f507a877dea5b3b95ab3e513484988c00a4fd2a539ec3465"),
}


def legacy_execution_writers():
    from research_harness import development, execution
    path = Path(__file__).resolve().parent / "fixtures/legacy_execution_admission.py.txt"
    source = path.read_bytes()
    if hashlib.sha256(source).hexdigest() != SOURCE_SHA256:
        raise AssertionError("The identified historical writer fixture changed")
    namespace = dict(vars(development))
    namespace.update(vars(execution))
    namespace["__name__"] = __name__ + ".f5b3c46"
    exec(compile(source, str(path), "exec"), namespace)
    return SimpleNamespace(admit_execution=namespace["admit_execution"], bind_execution=namespace["bind_execution"])
