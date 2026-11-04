"""Differential contract for request reuse, frozen by a clean ceb050b export.

Regenerate with tests/generate_review_request_parity.py, never from the harness
under test. Every case compares full reports and states, ordered obligations,
selected attempts, refusal details and fingerprints of exact serialized bytes.
"""

import gzip
import json
import unittest
from unittest import mock

import review_request_parity_fixtures as fixtures
from research_harness import review_continuation


class ReviewRequestParityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.frozen = json.loads(gzip.decompress(fixtures.FROZEN_PATH.read_bytes()))

    def test_oracle_origin_and_case_inventory(self):
        self.assertEqual(self.frozen["source_commit"], fixtures.BASE_COMMIT)
        self.assertEqual(set(self.frozen["cases"]), set(fixtures.CASE_BUILDERS))
        self.assertEqual(self.frozen["serialization"], "review_protocol._put_json")


def strip_observation_metadata(attempt):
    if attempt is None:
        return None
    # These advisory fields were added after the oracle and do not authenticate attempts.
    return {key: value for key, value in attempt.items()
            if key not in ("observed_at", "provider_diagnostic")}


def strip_status_floor(value):
    # status reports the parts allowance floor from 0.73.0 on; requests and packets never carry it.
    if isinstance(value, dict):
        return {key: strip_status_floor(item) for key, item in value.items()
                if key != "unit_allowance_floor"}
    if isinstance(value, list):
        return [strip_status_floor(item) for item in value]
    return value


def keep_report(records, artifacts, report):
    return report


def keep_actions(records, artifacts, assignment_id, actions=()):
    return [dict(action) for action in actions]


def build_without_actions(build):
    # The executable actions that 0.73.0 attaches to obligations are checked by the
    # continuation tests; without them every report must equal the oracle exactly.
    with mock.patch.object(review_continuation, "annotate_review_obligations", keep_report), \
            mock.patch.object(review_continuation, "describe_next_actions", keep_actions):
        return build()


def add_case(name, build):
    def test(self):
        actual = build_without_actions(build)
        expected = self.frozen["cases"][name]
        actual["attempts"] = {key: strip_observation_metadata(attempt)
                              for key, attempt in actual["attempts"].items()}
        if "status" in actual:
            actual["status"] = strip_status_floor(actual["status"])
        for selected in actual["selected"].values():
            if "value" in selected:
                binding, refused, completed = selected["value"]
                selected["value"] = [strip_observation_metadata(binding), refused,
                                     [strip_observation_metadata(attempt) for attempt in completed]]
        self.assertEqual(set(actual), set(expected))
        # Separate assertions locate a regression without dumping other reports
        # or truncating a comparison of large request bodies.
        for field in expected:
            with self.subTest(case=name, field=field):
                self.assertEqual(actual[field], expected[field])
    test.__doc__ = "Replay " + name + " against ceb050b."
    setattr(ReviewRequestParityTests, "test_" + name.replace("-", "_"), test)


for case_name, builder in fixtures.CASE_BUILDERS.items():
    add_case(case_name, builder)


if __name__ == "__main__":
    unittest.main()
