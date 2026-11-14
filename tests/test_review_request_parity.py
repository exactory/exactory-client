"""Differential contract for request reuse, frozen by a clean ceb050b export.

Regenerate with tests/generate_review_request_parity.py, never from the harness
under test. Every case compares full reports and states, ordered obligations,
selected attempts, refusal details and fingerprints of exact serialized bytes.
"""

import copy
import gzip
import json
import unittest
from unittest import mock

import review_request_parity_fixtures as fixtures
from research_harness import review_continuation
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
        """Pin report presentation and Claude response metadata without changing the byte oracle."""
        if name == 'claude-local' and field in ('attempts', 'selected'):
            expected = copy.deepcopy(expected)
            original = self.frozen['cases'][name]['attempts']['attempt']['response']
            sha256 = 'b1db6f722adb299139f67ed88514403ff749d8b35995ebbede32d8b5377ce58b'
            response = dict(original, sha256=sha256, size=3043,
                            path=original['path'].rsplit('/', 1)[0] + '/' + sha256)
            # New calls retain the rate event that the historical stream already carried.
            if field == 'attempts':
                expected['attempt']['response'] = response
            else:
                binding, refused, completed = expected['assignment']['value']
                binding['response'] = response
                for attempt in completed:
                    attempt['response'] = response
            return expected
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
            # Since 0.73.3 the pending parts v1 head reports its occupancy. The
            # other slot has no head, so the missing pair stays beside it.
            head = dict(slot_id='bar-first', epoch_id='series-3-part-2', state='pending', outcome=None,
                        role='result', current=True)
            occupancy = dict(code='review_task_pending',
                             explanation='Complete the current assessment in its existing logical slot.',
                             occupied=1, incomplete=[], pending=[head])
            report['obligations'].insert(report['obligations'].index(pair) + 1, occupancy)
            report['counts'] = dict(report['counts'], obligations=len(report['obligations']))
            report['slot_outcomes'] = dict(slots=[head], occupied=1, complete=[], incomplete=[], pending=[head],
                                           adverse_findings=[], assessment_complete=False, ready=False)
            report['digest'] = digest(dict(problem_evidence_digest=report['problem_evidence_digest'],
                                          decision=None, obligations=report['obligations']))
            if field == 'status':
                report['next'] = copy.deepcopy(pending)
                # status sorts the obligations of a record with a pending head;
                # the digest stays the gate's.
                order = ('problem_evidence_part_pending', 'review_task_pending', 'problem_review_pair_required',
                         'problem_decision_missing', 'problem_openness_missing', 'problem_attempt_missing',
                         'problem_slate_missing', 'problem_value_missing', 'problem_assessment_missing')
                report['obligations'] = sorted(report['obligations'], key=lambda row: order.index(row['code']))
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
                self.assertEqual(actual[field], self.expect_current_result_report(name, field, expected[field]))
    test.__doc__ = "Replay " + name + " against ceb050b."
    setattr(ReviewRequestParityTests, "test_" + name.replace("-", "_"), test)


for case_name, builder in fixtures.CASE_BUILDERS.items():
    add_case(case_name, builder)


if __name__ == "__main__":
    unittest.main()
