"""The tests whose results a guard test needs, read from the run that executes them.

A guard test names its evidence tests through require_evidence_tests. The call checks, without a run, that each one
exists, loads as one test and carries no skip mark and no expected-failure mark, and it records the identifiers in
this process. ci/unittest_shards.py writes the recorded identifiers into the result of its shard, and its command
verify requires each of them to have run and passed in the same run, on the same Python version. So no test runs
another test again.
"""
import unittest


# The evidence tests that the guard tests of this process required. The runner of the process reads them after its
# last test.
_REQUIRED_TEST_IDS = set()


def inspect_evidence_tests(identifiers):
    """The problem of each evidence test that a check without a run finds: not_one_loadable_test, skip_mark or
    expected_failure_mark. An evidence test without a problem is absent from the result."""
    loader, problems = unittest.TestLoader(), {}
    for identifier in identifiers:
        tests = list(loader.loadTestsFromName(identifier))
        if len(tests) != 1 or tests[0].id() != identifier:
            problems[identifier] = "not_one_loadable_test"
            continue
        test = tests[0]
        method = getattr(test, test._testMethodName)
        if getattr(test, "__unittest_skip__", False) or getattr(method, "__unittest_skip__", False):
            problems[identifier] = "skip_mark"
        elif (getattr(test, "__unittest_expecting_failure__", False)
              or getattr(method, "__unittest_expecting_failure__", False)):
            problems[identifier] = "expected_failure_mark"
    return problems


def require_evidence_tests(identifiers):
    """Record the evidence tests for the verification of this run, and return the problems that
    inspect_evidence_tests finds."""
    _REQUIRED_TEST_IDS.update(identifiers)
    return inspect_evidence_tests(identifiers)


def read_required_tests():
    """The evidence tests that the guard tests of this process required, sorted."""
    return sorted(_REQUIRED_TEST_IDS)
