"""D3 R17 and R18: family texts identify the found work throughout a real v6 chain."""

import copy
import json

from openness_reading_fixtures import OpennessReadingCase
from test_openness_finding_answer import build_answer
import test_research_problem_openness_minimum as minimum_fixtures
from research_harness import native_review, openness_reading as reading, problem_openness
from research_harness import review_protocol
from research_harness.acquisition import import_response
from research_harness.errors import ResearchError
from research_harness.evidence import digest
from research_harness.identities import family_id
from research_fixtures import atom, entry


class FamilyDeliveryTests(OpennessReadingCase):
    reading_protocol = None
    route_model = 'gpt-6-astra'
    text_sizes = (160000,)

    def metadata(self, number=1, **kwargs):
        if number != 1:
            return super().metadata(number, **kwargs)
        # The acceptance identifier, with entirely synthetic metadata and article text.
        identifier = 'arxiv:2404.04364v1'
        self.sequence += 1
        import_response(self.store, 'arxiv', atom([entry(identifier[6:])], total=1),
            source_url='https://export.arxiv.org/api/query?id_list=' + identifier[6:],
            captured_at='2026-09-07T12:00:00Z', expected_revision=self.store.revision,
            request_id='family-metadata-' + str(self.sequence))
        return identifier

    def record_searches(self):
        self.found = family_id(self.versions[0])
        return [minimum_fixtures.ProblemOpennessMinimumTests.search(self, [self.found])]

    def setUp(self):
        super().setUp()
        self.limit_input(1300000)

    def build_ground(self, work=None, turns=()):
        return dict(kind='source_settles', work_id=work or self.found, turns=list(turns),
                    text='The read version proves the exact bound in its first theorem.')

    def test_family_plan_and_delivered_text_support_a_ground_naming_the_found_work(self):
        # Source: 20261113110022-every-open-record-and-review-capacity, D3 R16 to R18.
        from ci.capacity_packet_inventory import read_packet_registry, validate_packet
        registry = read_packet_registry()
        reference = self.export()
        native = json.loads(self.artifacts.read(reference))
        context = native['scientific_context']
        self.assertEqual(context['openness_minimum']['family_readings'], [dict(
            search_digest=digest(self.search), work_id=self.found, version_id=self.versions[0])])
        self.assertEqual(context['decisive_work_ids'], [self.found])
        rows = reading.list_reading_texts(self.store.snapshot()['records'], native)
        self.assertEqual([row['family_of'] for row in rows], [[self.found]])

        identifiers = self.run_reading_turns(reference, turn_count=8)
        for identifier in identifiers[:-1]:
            packet = self.read_packet(identifier)
            plan = packet['reading_turn']['reading_plan']
            self.assertEqual(validate_packet('native_math', packet, registry), [])
            self.assertEqual(reading.find_protocol(packet), reading.PROTOCOL_V6)
            for row in plan['delivered_before'] + plan['delivered_now'] + plan['remaining']:
                if row['kind'] == 'fulltext':
                    self.assertEqual(row['family_of'], [self.found])
            _, sent = self.read_sent_packet(identifier)
            self.assertEqual(sent['reading_turn']['reading_plan'], plan)
            complete = reading.review_excerpts.find_complete_bodies(sent)
            for row in plan['delivered_now']:
                self.assertIn(row['artifact']['sha256'], complete)

        final = identifiers[-1]
        self.assertEqual(validate_packet('native_math', self.read_packet(final), registry), [])
        answer = build_answer(self.read_packet(final), 'unresolved',
                              [self.build_ground(turns=identifiers[:-1])])
        self.invoke(final, answer)
        saved = self.mutate(self.load_problem_module().record_openness, dict(assignment_id=final))['result']
        self.assertEqual(saved['outcome'], 'unresolved')
        self.assertEqual(saved['grounds'], answer['findings']['openness']['grounds'])
        records = self.store.snapshot()['records']
        self.assertEqual(reading.verify_chain(records, self.artifacts, records['review_assignment'][final]),
                         saved['reading_turns'])

    def test_a_decisive_work_without_either_reading_still_fails_the_decisive_work_rule(self):
        unread = self.metadata(2)
        search = minimum_fixtures.ProblemOpennessMinimumTests.search(self, [self.found, unread], resolved=[self.found])
        reference = problem_openness.export_openness(self.store, dict(
            assignment_id='turn-1', reviewer_id='openness-reviewer', minimum=dict(
                searches=[dict(formulation=self.selected['statement'], search_id=row['id'])
                          for row in (self.search, search)],
                settling_work_id=None, duplicate_resolutions=[])), accept_gaps=True)['native_packet']
        native = json.loads(self.artifacts.read(reference))
        self.assertEqual(native['scientific_context']['decisive_work_ids'], [self.found])
        packet = dict(native=native)
        native_review.validate_finding(packet, build_answer(packet, 'unresolved', [self.build_ground()]))
        for work in (unread, self.versions[0]):
            with self.subTest(work=work), self.assertRaises(ResearchError) as caught:
                native_review.validate_finding(packet, build_answer(packet, 'unresolved', [self.build_ground(work)]))
            self.assertEqual(caught.exception.code, 'openness_finding_invalid')
            self.assertEqual(caught.exception.details['rule'], 'decisive_work')

    def test_one_text_names_all_found_records_once_across_searches(self):
        alias = 'doi:10.48550/arxiv.2404.04364'
        search = minimum_fixtures.ProblemOpennessMinimumTests.search(self, [self.found, alias])
        self.searches.append(search)
        native = json.loads(self.artifacts.read(self.export()))
        rows = reading.list_reading_texts(self.store.snapshot()['records'], native)
        self.assertEqual([row['family_of'] for row in rows], [sorted([self.found, alias])])
        self.assertEqual(native['scientific_context']['decisive_work_ids'], sorted([self.found, alias]))

    def test_stored_v6_family_chain_keeps_its_packets_prompts_and_request_digests(self):
        self.assertEqual(digest(reading.PROMPTS[reading.PROTOCOL_V6]),
                         '94918388828714001d4885759224554d4c1935a1ba51c4800c2721431a652701')
        reference = self.export()
        native = json.loads(self.artifacts.read(reference))
        self.assertEqual(native['scientific_context']['family_reading_plan_version'], 1)
        # Frozen shape from before the fix: no plan-metadata version, and no direct reading of the found id.
        native['scientific_context'].pop('family_reading_plan_version', None)
        native['scientific_context']['decisive_work_ids'] = []
        reference = self.artifacts.put(json.dumps(native, sort_keys=True).encode(), 'application/json')
        first = self.assign('turn-1', reference)
        self.invoke('turn-1', self.build_reading_response(self.read_packet('turn-1')))
        before = copy.deepcopy(self.store.snapshot()['records'])
        assignment = before['review_assignment']['turn-1']
        attempt = before['review_attempt']['attempt-turn-1']
        packet_bytes = self.artifacts.read(assignment['packet'])
        request_bytes = self.artifacts.read(attempt['request'])
        prompt = copy.deepcopy(assignment['prompt'])
        self.assertEqual(prompt, review_protocol.canonical_prompt('native_math',
            reading_stage='reading', reading_protocol=reading.PROTOCOL_V6))
        identifiers = ['turn-1']
        for number in range(2, 9):
            identifier = 'turn-' + str(number)
            self.assign(identifier, reference, identifiers[-1])
            identifiers.append(identifier)
            packet = self.read_packet(identifier)
            if reading.find_stage(packet) == 'final':
                self.invoke(identifier, build_answer(packet))
                self.mutate(self.load_problem_module().record_openness, dict(assignment_id=identifier))
                break
            self.assertNotIn('family_of', json.dumps(packet['reading_turn']['reading_plan']))
            self.invoke(identifier, self.build_reading_response(packet))
        else:
            self.fail('The stored family chain must finish')
        records = self.store.snapshot()['records']
        reading.verify_chain(records, self.artifacts, records['review_assignment'][identifiers[-1]])
        self.assertEqual(records['review_assignment']['turn-1'], assignment)
        self.assertEqual(records['review_attempt']['attempt-turn-1'], attempt)
        self.assertEqual(self.artifacts.read(first['packet']), packet_bytes)
        self.assertEqual(self.artifacts.read(attempt['request']), request_bytes)
        self.assertEqual(records['review_assignment']['turn-1']['prompt'], prompt)
        rebuilt = review_protocol.build_assignment_request(records, self.artifacts, assignment)
        self.assertEqual(json.dumps(rebuilt, sort_keys=True, ensure_ascii=False, allow_nan=False).encode(),
                         request_bytes)

