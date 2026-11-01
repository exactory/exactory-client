"""Observed problem result sessions on the local Codex route."""

import copy
import json
import unittest
from unittest.mock import patch

from codex_route_fixtures import invoke_codex, limit_codex_input, record_codex_route
from research_harness import research_decisions as decisions, review_protocol as protocol
from research_harness import review_transport as transport
from research_harness.evidence import digest

KEY = 'problem_evidence_parts'
SPLIT_KEYS = ('openness_searches', 'assessment_evidence', 'readings', 'passages', 'computations', 'formal_checks')


def indices(ranges):
    return [index for start, end in ranges for index in range(start, end)]


def note_units(packet):
    evidence, plan = packet['problem_evidence'], packet[KEY]
    units = {key: [{'key': key, 'index': index}
                   for index, item in zip(indices(plan['delivered_now'].get(key, [])), evidence[key])]
             for key in SPLIT_KEYS}
    return [unit for key in SPLIT_KEYS if key != 'passages' for unit in units[key]]


class ResultPartsCase(unittest.TestCase):
    def setUp(self):
        import test_research_problem_reviews as fixtures
        self.case = fixtures.ProblemReviewTests()
        self.addCleanup(self.case.doCleanups)
        self.case.setUp()
        case = self.case
        case.supporting = case.artifacts.put(b'A captured field standard.', 'text/plain')
        record_codex_route(case, output_tokens=getattr(self, 'output_tokens', 1000),
                           timeout_seconds=getattr(self, 'timeout_seconds', 5))
        for reviewer in ('first', 'second'):
            assignment = case.assign('bar', reviewer, **case.deliver_supporting(reviewer))
            output = case.build_review_output('bar')
            invoke_codex(case, assignment['id'], output)
            case.mutate(decisions.record_value_review, dict(output, id='review-' + assignment['id'],
                        dossier_id=None, assignment_id=assignment['id']))
        if not getattr(self, 'staged_entry', False):
            # These fixtures reconstruct assignments recorded by 0.69.x. New
            # prospective oversized assignments use the separate v2 fixtures.
            from research_harness import review_problem_parts
            legacy = patch.object(review_problem_parts, 'allows_staged_entry', return_value=False)
            legacy.start()
            self.addCleanup(legacy.stop)

    def oversized(self, count=6, characters=24000, limit=110000):
        case = self.case
        case.expected['openness_searches'] = []
        for index in range(count):
            search = {'id': 'search-' + str(index), 'queries': ['bounded sequence'], 'reason': 'x' * characters}
            case.put('literature_search', search['id'], search)
            case.expected['openness_searches'].append({'search': search, 'sources': []})
        limit_codex_input(self, limit)

    def assign(self, identifier='part-1', prior=None, reviewer='first'):
        return self.case.assign('result', reviewer, identifier=identifier,
                                **({'prior_assignment_id': prior} if prior else {}))

    def packet(self, assignment):
        return json.loads(self.case.artifacts.read(assignment['packet']))

    def output(self, assignment):
        packet = self.packet(assignment)
        return {'stage': 'result', 'phase': 'evidence_part', 'part': packet[KEY]['turn'],
                'notes': [{'unit': unit, 'note': 'This delivered item supports the bounded assessment.'}
                          for unit in note_units(packet)]}

    def invoke(self, assignment, output=None, attempt=None):
        return invoke_codex(self.case, assignment['id'], output if output is not None else self.output(assignment), attempt)

    def finish_parts(self, first=None, reviewer='first'):
        assignment = first or self.assign(reviewer + '-part-1', reviewer=reviewer)
        parts = []
        while self.packet(assignment)[KEY]['stage'] == 'part':
            parts.append(assignment)
            self.assertLessEqual(len(parts), 30, 'Each part must advance the finite unit queue')
            self.invoke(assignment)
            assignment = self.assign(reviewer + '-turn-' + str(len(parts) + 1), assignment['id'], reviewer)
        return parts, assignment

    def record_final(self, assignment, output=None):
        output = output or self.final_output()
        self.invoke(assignment, output)
        return self.case.mutate(decisions.record_value_review,
            dict(output, id='review-' + assignment['id'], dossier_id=None, assignment_id=assignment['id']))['result']

    def final_output(self):
        from research_harness.review_refusal import evidence_arrays
        output = self.case.build_review_output('result')
        search = self.case.expected['openness_searches'][0]['search']
        reference = {'kind': 'record', 'record_kind': 'literature_search', 'id': search['id'], 'digest': digest(search)}
        for array in evidence_arrays(output):
            array[:] = [copy.deepcopy(reference)]
        return output

    def coverage(self, review, expected=None, records=None):
        expected = self.case.expected if expected is None else expected
        return decisions._assess_problem_coverage(records or self.case.read_records(), self.case.artifacts,
                                                  [review], expected, digest(self.case.expected))

    def assert_code(self, code, call):
        from research_harness.errors import ResearchError
        with self.assertRaises(ResearchError) as raised:
            call()
        self.assertEqual(raised.exception.code, code)
        return raised.exception

    def request(self, assignment):
        return protocol.build_assignment_request(self.case.read_records(), self.case.artifacts, assignment)
