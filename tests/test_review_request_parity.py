"""Differential contract for request reuse, frozen by a clean ceb050b export.

Regenerate with tests/generate_review_request_parity.py, never from the harness
under test. Every case compares full reports and states, ordered obligations,
selected attempts, refusal details and fingerprints of exact serialized bytes.
"""

import gzip
import json
import unittest

import review_request_parity_fixtures as fixtures


class ReviewRequestParityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.frozen = json.loads(gzip.decompress(fixtures.FROZEN_PATH.read_bytes()))

    def test_oracle_origin_and_case_inventory(self):
        self.assertEqual(self.frozen["source_commit"], fixtures.BASE_COMMIT)
        self.assertEqual(set(self.frozen["cases"]), set(fixtures.CASE_BUILDERS))
        self.assertEqual(self.frozen["serialization"], "review_protocol._put_json")


def add_case(name, build):
    def test(self):
        actual = build()
        expected = self.frozen["cases"][name]
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
