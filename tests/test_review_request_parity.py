"""Differential contract for request reuse, frozen by a clean ceb050b export.

Regenerate with tests/generate_review_request_parity.py, never from the harness
under test. Every case compares full reports and states, ordered obligations,
selected attempts, refusal details and fingerprints of exact serialized bytes.
"""

import copy
import gzip
import json
import unittest

import review_request_parity_fixtures as fixtures
from research_harness.evidence import digest


class ReviewRequestParityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.frozen = json.loads(gzip.decompress(fixtures.FROZEN_PATH.read_bytes()))

    def test_oracle_origin_and_case_inventory(self):
        self.assertEqual(self.frozen["source_commit"], fixtures.BASE_COMMIT)
        self.assertEqual(set(self.frozen["cases"]), set(fixtures.CASE_BUILDERS))
        self.assertEqual(self.frozen["serialization"], "review_protocol._put_json")

    def expect_current_result_report(self, name, field, expected):
        """Pin the 0.73.0 presentation changes without changing the byte oracle."""
        if field not in ('gate', 'status') or name not in ('result-history-06918', 'result-parts-v2'):
            return expected
        expected = copy.deepcopy(expected)
        report = expected['value']
        if name == 'result-history-06918':
            # The open third series occupies the slot. The older recorded final
            # no longer supplies a selected result, and continuation has priority.
            pending = next(row for row in report['obligations'] if row['code'] == 'problem_evidence_part_pending')
            pending['next'] = [dict(kind='review', workflow='review-assignment', scope=dict(
                role='result', route_id='route', reviewer_id='first', author_id='author',
                dossier_id=None, prior_assignment_id='series-3-part-2'))]
            pair = next(row for row in report['obligations'] if row['code'] == 'problem_review_pair_required')
            pair['review_ids'] = []
            report['obligations'].remove(pending)
            report['obligations'].insert(0, pending)
            report['digest'] = digest(dict(problem_evidence_digest=report['problem_evidence_digest'],
                                          decision=None, obligations=report['obligations']))
            if field == 'status':
                report['next'] = copy.deepcopy(pending)
        else:
            # Reconstruct the complete outcome solely from the frozen observed
            # final and the fixture's fixed identities/revision, not the report
            # under test. Every prior request/authentication assertion stays exact.
            final = self.frozen['cases'][name]['states']['turn-4']['value']
            payload = dict(final['output'], id='review-turn-4', dossier_id=None, assignment_id='turn-4')
            attempt = self.frozen['cases'][name]['attempts'][final['attempt_id']]
            review = dict(id='review-turn-4', payload=payload, digest=digest(payload),
                recorded_revision=attempt['recorded_revision'] + 1,
                dossier_id=None, dossier_digest=None, assignment_id='turn-4', reviewer_id='first',
                packet_digest=final['packet_digest'], prompt_hash=final['prompt_hash'],
                output_digest=final['output_digest'], support=None,
                independence_at_recording=final['context_status'],
                subject=dict(kind='problem', statement_id='statement-1'))
            head = dict(slot_id='bar-first', epoch_id='turn-4', state='complete', outcome=review,
                        role='result', current=True)
            report['slot_outcomes'] = dict(slots=[head], occupied=1, complete=[review], incomplete=[], pending=[],
                adverse_findings=[], assessment_complete=True, ready=False)
        return expected


def add_case(name, build):
    def test(self):
        actual = build()
        expected = self.frozen["cases"][name]
        self.assertEqual(set(actual), set(expected))
        # Separate assertions locate a regression without dumping other reports
        # or truncating a comparison of large request bodies.
        for field in expected:
            with self.subTest(case=name, field=field):
                self.assertEqual(actual[field], self.expect_current_result_report(name, field, expected[field]))
    test.__doc__ = "Replay " + name + " against ceb050b."
    setattr(ReviewRequestParityTests, "test_" + name.replace("-", "_"), test)


for case_name, builder in fixtures.CASE_BUILDERS.items():
    add_case(case_name, builder)


if __name__ == "__main__":
    unittest.main()
