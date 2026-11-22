"""Drive late records past the stored reserve in an adjudicator series."""

import math

import property_stub_a as stub
import test_property_drivers_a_parts_roles as fixtures
from research_harness import review_parts, review_parts_v3 as parts, review_protocol as protocol
from research_harness import review_route_capacity as capacity
from research_harness.evidence import digest
from research_harness.operations import prepared_mutation


class LateAdjudicatorProperties(fixtures.RoleProperties):
    def append_searches(self, case, first_index, count, width):
        searches = [dict(id='late-search-%d' % index, queries=['What decides the implication?'],
            reason='The reported counterexample falsifies the implication. ' + str(index % 10) * width)
            for index in range(first_index, first_index + count)]

        def seed(store, payload, **identity):
            def prepare(records, rows):
                return [('literature_search', row['id'], row) for row in rows['searches']], {}
            return prepared_mutation(store, 'fixture.late_searches', payload, prepare, **identity)

        case.mutate(seed, {'searches': searches})
        case.expected['openness_searches'].extend(dict(search=row, sources=[]) for row in searches)

    def test_d31_late_reserve(self):
        """The late inventory must exceed one block of unfunded cards before archived passage retrieval."""
        fixture = fixtures.adjudication_fixtures.AdjudicationPartsCase()
        fixture.output_tokens = 128000
        self.use_fixture(fixture)
        fixture.oversized(count=1, paragraphs=2400)
        self.pin_read_profile()
        case = fixture.case
        first = fixture.assign()
        plan = capacity.read_chain_capacity_plan(case.artifacts, first)
        descriptor = first['capacity_plan']
        artifact_bytes = case.artifacts.read(descriptor['artifact'])
        floor = min(row['material_allowance'] for row in plan['task']['read']['levels'])
        reserve = plan['allocation']['late_unit_reserve']
        entry = plan['allocation']['entry_sizes']['card']
        block = min(plan['allocation']['rooms']['combine'], plan['task']['combine']['material_allowance'])
        count = math.ceil((reserve + block) / entry) + 2
        stub.invoke_prompt(self, case, first, stub.build_series_turn)
        self.append_searches(case, 0, count, floor)
        assignment = self.continue_series(case, first)
        for _ in range(65):
            self.assertEqual(assignment['capacity_plan'], descriptor)
            self.assertEqual(case.artifacts.read(descriptor['artifact']), artifact_bytes)
            packet = protocol._read_json(case.artifacts, assignment['packet'])
            if packet['review_parts']['kind'] == 'final':
                break
            stub.invoke_prompt(self, case, assignment, stub.build_late_card_answer)
            assignment = self.continue_series(case, assignment)
        else:
            self.fail('The late reserve chain did not reach its final')
        records = case.read_records()
        observations = parts.observe_series(records, case.artifacts, assignment['id'])
        state = parts.build_series_state(observations)
        read_groups = [turn['packet']['review_parts_groups'] for turn in observations
                       if turn['packet']['review_parts']['kind'] == 'read']
        charges = [parts.compute_card_allowance(plan, groups) for groups in read_groups]
        for key in ('late_unit_reserve', 'late_characters_beyond_reserve', 'card_reserve', 'card_budget'):
            self.assertEqual(state['cursor'][key], sum(charge[key] for charge in charges), key)
        self.assertEqual(state['late_unit_reserve'], state['cursor']['late_unit_reserve'])
        self.assertLessEqual(state['late_unit_reserve'], reserve)
        self.assertEqual(state['late_characters_beyond_reserve'], state['cursor']['late_characters_beyond_reserve'])
        self.assertGreater(state['late_characters_beyond_reserve'], block)
        for key in ('card_reserve', 'card_budget'):
            self.assertLessEqual(state['cursor'][key], plan['allocation'][key])
        self.assertEqual(first['capacity_plan']['digest'], digest(plan))

        read = [row for row in state['cards'] if row['statements']]
        self.assertTrue(read, 'No unfunded card retained a verified refutation')
        conditioned = [row for row in state['combinations'] if row.get('mode') == 'condense']
        self.assertTrue(any(row['output'].get('summary', '').startswith('[') for row in conditioned),
                        'No condensing answer retained the citation scope')
        stub.invoke_prompt(self, case, assignment, stub.request_archived_quote)
        final = self.continue_series(case, assignment)
        packet = protocol._read_json(case.artifacts, final['packet'])
        card_row, quote = packet['review_parts_requests']
        card = next(row for row in state['cards'] if row['id'] == card_row['unit'])
        self.assertEqual(card['output']['bearing'][0]['relation'], 'contradicts')
        self.assertEqual(quote['text'], card['statements'][0]['text'])
        self.assertNotIn(card['id'], state['entries'])
        stub.invoke_prompt(self, case, final, stub.build_late_adjudication_final)
        records = case.read_records()
        _, output = protocol.read_assignment_output(records, case.artifacts, final['id'])
        self.assertIsNotNone(output, records.get('review_output_refusal'))
        saved = case.mutate(protocol.record_adjudication, dict(
            output, id='late-disposition', assignment_id=final['id']))['result']
        self.assertEqual(saved['assignment_id'], final['id'])
        result = review_parts.verify_series(case.read_records(), case.artifacts, final['id'])
        self.assertTrue(result['complete'], result['issues'])
