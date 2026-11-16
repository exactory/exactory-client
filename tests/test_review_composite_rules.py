"""Exercise the real final-rule boundary with authenticated expanded outputs."""

import copy
import unittest
from unittest.mock import patch

from literature_fixtures import LiteratureCase
import test_research_review_protocol as fixtures
from test_openness_finding_answer import build_packet, build_answer
from research_harness import native_review, review_composite, review_composition, review_protocol
from research_harness.evidence import digest
from research_harness.operations import immutable_record
from research_harness.review_route_capacity import CAPACITY_PLAN_PROTOCOL


class CompositeRuleTests(LiteratureCase):
    seed = fixtures.ReviewProtocolTests.seed
    records = fixtures.ReviewProtocolTests.records
    route = fixtures.ReviewProtocolTests.route
    protocol = fixtures.ReviewProtocolTests.protocol
    run_probe = fixtures.ReviewProtocolTests.run_probe
    response = fixtures.ReviewProtocolTests.response
    assign = fixtures.ReviewProtocolTests.assign
    invoke = fixtures.ReviewProtocolTests.invoke

    def test_real_openness_rule_records_and_authenticates_expanded_page_references(self):
        self.route()
        self.run_probe()
        packet = dict(build_packet('blocked'), handle_table={})
        packet['reading_final'] = dict(protocol='exactory-openness-reading-v7', page=dict(targets=['t0', 't1']))
        packet['native']['scientific_context']['openness_minimum']['neighbours'] = ['A neighbour']
        reference = self.artifacts.put(b'A complete independent argument.', 'text/plain')
        answer = build_answer(packet, 'unresolved', [dict(kind='own_argument', text='The argument leaves a gap.', refs=[reference])])
        answer['findings']['openness']['coverage'] = ['Statement searched.', 'Neighbour searched.']
        rule = review_composition.find_rule('native_math', packet)
        pages, closing = review_composition.split_output(rule, packet, answer, [['t0'], ['t1']])
        pages[0]['output']['targets'][0]['grounds'] = closing['grounds']
        closing['grounds'] = []
        turns = dict(chain='page-1', packet=packet,
            pages=[dict(assignment_id='page-%d' % n, targets=['t%d' % (n - 1)]) for n in (1, 2)],
            closing=dict(assignment_id='closing', targets=[]), condensing=[])
        currency = dict(packet_digest=digest(packet['native']), inventory_digest=digest([]))
        plan = dict(targets=dict(handles=['t0', 't1']))
        plan_ref = review_protocol._put_json(self.artifacts, plan)
        for identifier, output in [('page-1', pages[0]['output']), ('page-2', pages[1]['output']), ('closing', closing)]:
            # Ordinary instrumented observations; the family supplies the scientific envelope.
            self.assign(identifier=identifier)
            self.invoke(output, identifier='attempt-' + identifier, assignment=identifier)
        records = self.records()
        for assignment in records['review_assignment'].values():
            assignment.update(role='native_math', capacity_plan=dict(protocol=CAPACITY_PLAN_PROTOCOL,
                artifact=plan_ref, digest=digest(plan)))
        family = dict(list_turns=lambda records, artifacts, assignment: copy.deepcopy(turns),
            read_currency=lambda records, artifacts, assignment: currency,
            validate_output=lambda records, artifacts, assignment, envelope, output: native_review.validate_finding(envelope, output))
        with patch.dict(review_composite.FINAL_FAMILIES, {'rule-integration': family}):
            composite, (descriptor, content) = review_composite.build_composite(records, self.artifacts,
                records['review_assignment']['closing'])
            self.artifacts.put(content, descriptor['media_type'])
            change = immutable_record(self.records(), 'review_composite', composite['id'], composite)
            self.store.mutate('fixture.composite', {}, lambda tx: tx.put(*change),
                expected_revision=self.store.revision, request_id='composite')
            records['review_composite'] = self.records()['review_composite']
            actual = review_composite.authenticate_composite(records, self.artifacts, composite)
        self.assertEqual(actual, answer)
        self.assertEqual(actual['findings']['openness']['grounds'][0]['refs'], [reference])


if __name__ == '__main__':
    unittest.main()
