"""Versioned sessions settle partial requests and retrieve referenced artifacts."""

import copy
import hashlib
import json
from unittest.mock import patch
from codex_route_fixtures import invoke_codex, limit_codex_input, probe_codex_route
import review_parts_fixtures
import test_review_parts_repair_journey as repair_fixtures
import test_review_parts_study as study_fixtures
import test_problem_result_contract as result_contract_fixtures

from research_harness import bar_sources, review_excerpts, review_protocol as protocol
from research_harness import research_decisions, review_continuation, review_delivered_evidence
from research_harness import review_source_complete
from research_harness import review_refusal
from research_harness.evidence import digest
from research_harness.errors import ResearchError
from test_reviewer_excerpt_sessions import ExcerptSessionCase


RULES = 'exactory-independent-review-sources-v3'


class SessionRulesTests(ExcerptSessionCase):
    def setUp(self):
        from research_harness import bar_sources, review_protocol
        released = patch.object(review_protocol, 'NEW_SESSION_RULES', (bar_sources.PROTOCOL_V3,))
        released.start()
        self.addCleanup(released.stop)
        super().setUp()

    def test_a_paragraph_wrapped_v3_prompt_requires_the_packets_source_rules(self):
        prompt = protocol.canonical_prompt('slate', orchestration='problem_creation', source_rules=RULES,
            problem_paragraphs=['exactory-problem-slate-forming-v2'])
        assignment = dict(role='slate', subject=dict(kind='problem'), prompt=prompt)
        packet = dict(subject=dict(kind='problem'), source_rules=RULES,
                      problem_prompt_conditions=['slate-forming'])
        self.assertTrue(protocol._prompt_matches(assignment, packet=packet))
        for rules in (None, bar_sources.PROTOCOL, 'unregistered'):
            changed = dict(packet)
            if rules is None:
                changed.pop('source_rules')
            else:
                changed['source_rules'] = rules
            with self.subTest(rules=rules):
                self.assertFalse(protocol._prompt_matches(assignment, packet=changed))

    def test_edited_v3_prompt_keeps_its_result_rubric_without_authentication(self):
        prompt = protocol.canonical_prompt('result', orchestration='problem_creation', source_rules=RULES)
        assignment = dict(role='result', subject=dict(kind='problem'), prompt=prompt)
        self.assertTrue(protocol.has_problem_result_rubric(assignment))
        for label, change in (('appended-text', dict(text=prompt['text'] + ' An edited scientific sentence.')),
                              ('replaced-text', dict(text='An edited scientific prompt.')),
                              ('template-hash', dict(template_hash='0' * 64)),
                              ('bare-template', dict(template_id=prompt['template_id'][
                                  :-len(bar_sources.SESSION_TEMPLATE_SUFFIX)]))):
            edited = dict(assignment, prompt=dict(prompt, **change))
            with self.subTest(change=label):
                self.assertTrue(protocol.has_problem_result_rubric(edited))
                self.assertFalse(protocol._prompt_matches(edited))

    def test_a_v3_result_refresh_names_the_wrapped_prompt_and_its_digest(self):
        case = result_contract_fixtures.ProblemResultContractTests()
        case.source_rules_protocol = RULES
        self.addCleanup(case.doCleanups)
        case.setUp()
        prior, _ = case.old_result()
        self.assertEqual(json.loads(case.case.artifacts.read(prior['packet']))['source_rules'], RULES)
        successor = case.case.assign('result', 'first', identifier='result-source-rules-refresh')
        packet = json.loads(case.case.artifacts.read(successor['packet']))
        refresh = packet['problem_refresh']
        self.assertEqual(refresh['template_id'], successor['prompt']['template_id'])
        self.assertEqual(refresh['prompt_hash'], successor['prompt']['rendered_prompt_hash'])
        self.assertEqual(packet['source_rules'], RULES)
        self.assertTrue(protocol._prompt_matches(successor, packet=packet))

    def test_a_source_free_final_does_not_rebuild_source_delivery_history(self):
        self.assign('source-free')
        self.assertEqual(self.packet('source-free')['source_rules'], RULES)
        output = self.review_response('bar')
        self.invoke('source-free', output)
        with patch.object(research_decisions, 'read_review_citation_delivery',
                          side_effect=AssertionError('A source-free final has no source delivery to audit.')):
            self.record_final('source-free', output)
            self.assertTrue(self.state('source-free')['ready'])

    def test_source_prompt_refuses_an_unknown_protocol_or_another_role(self):
        for role, rules in (('bar', 'unregistered'), ('native_math', RULES)):
            with self.subTest(role=role, rules=rules):
                with self.assertRaises(ResearchError) as caught:
                    protocol.canonical_prompt(role, source_rules=rules)
                self.assertEqual(caught.exception.code, 'invalid_review_context')

    def test_result_support_source_citations_require_v3_delivery_without_changing_v2(self):
        from research_harness import review_route_capacity
        from test_review_output_profile import build_row
        profile = patch.object(review_route_capacity, 'read_profile_priors', return_value=[
            build_row(level=None, kind='single', thinking=1000)])
        profile.start()
        self.addCleanup(profile.stop)
        case = study_fixtures.ReadinessPartsTests()
        self.addCleanup(case.doCleanups)
        case.setUp()
        from research_harness import review_route_capacity as capacity
        from test_review_output_profile import build_row
        # This test compares source citation rules in ordinary single reviews.
        row = dict(build_row(level=None, kind='single', thinking=1), largest_output_tokens=12)
        profile = patch.object(capacity, 'read_profile_priors', return_value=[row])
        profile.start()
        self.addCleanup(profile.stop)
        version = case.case.metadata(91)
        case.case.capture(version, pdf_text='A held source that neither result reviewer received.')
        capture = case.records()['work'][version]['fulltexts'][0]
        content = case.case.artifacts.read(capture['text']).decode()
        source = dict(kind='source', link=dict(version_id=version, source_id=capture['source_id'],
            artifact=capture['text'], locator=dict(kind='span', start=0, end=20,
                sha256=hashlib.sha256(content[:20].encode()).hexdigest())))
        limit_codex_input(case, case.measure(case.logical) * 2)
        old = case.assign('old-support')
        self.assertNotIn('review_parts', case.packet(old))
        output = case.final_output(old)
        output['support']['checks'][0]['evidence'] = [source]
        case.invoke(old, output)
        self.assertIsNone(review_refusal.classify_observed_output(case.records(), case.case.artifacts,
                                                                  'attempt-old-support'))
        packet_bytes = case.case.artifacts.read(old['packet'])
        prompt = copy.deepcopy(old['prompt'])
        request = case.records()['review_attempt']['attempt-old-support']['request']
        request_bytes = case.case.artifacts.read(request)
        case.logical['source_rules'] = RULES
        case.logical['assessor']['id'] = 'another-verifier'
        new = case.assign('new-support', reviewer_id='another-verifier')
        self.assertNotIn('review_parts', case.packet(new))
        self.assertEqual(case.packet(new)['source_rules'], RULES)
        output = case.final_output(new)
        output['support']['checks'][0]['evidence'] = [source]
        case.invoke(new, output)
        failure = review_refusal.classify_observed_output(case.records(), case.case.artifacts,
                                                          'attempt-new-support')
        self.assertIsNotNone(failure)
        self.assertEqual(failure['code'], 'review_evidence_undelivered')
        self.assertEqual(failure['details']['path'], '/support/checks/0/evidence')
        self.assertEqual(case.case.artifacts.read(old['packet']), packet_bytes)
        self.assertEqual(case.records()['review_assignment'][old['id']]['prompt'], prompt)
        self.assertEqual(case.case.artifacts.read(request), request_bytes)

    def test_v2_source_citation_contract_keeps_its_packet_prompt_and_request_bytes(self):
        capture = self.store.snapshot()['records']['work'][self.versions[0]]['fulltexts'][0]
        content = self.artifacts.read(capture['text']).decode()
        output = self.review_response('bar')
        output['value']['evidence'] = [dict(kind='source', link=dict(version_id=self.versions[0],
            source_id=capture['source_id'], artifact=capture['text'], locator=dict(kind='span', start=0, end=20,
                sha256=hashlib.sha256(content[:20].encode()).hexdigest())))]
        with patch.object(bar_sources, 'SESSION_PROTOCOL', bar_sources.PROTOCOL):
            old = self.assign('old')
        self.invoke('old', output)
        self.assertTrue(self.state('old')['ready'])
        records = self.store.snapshot()['records']
        attempt = records['review_attempt']['attempt-old']
        packet_bytes = self.artifacts.read(old['packet'])
        request_bytes = self.artifacts.read(attempt['request'])
        prompt = copy.deepcopy(old['prompt'])
        self.assign('new', reviewer='another-reviewer')
        self.invoke('new', output)
        failure = review_refusal.classify_observed_output(self.store.snapshot()['records'],
                                                          self.artifacts, 'attempt-new')
        self.assertEqual(failure['code'], 'review_evidence_undelivered')
        records = self.store.snapshot()['records']
        self.assertEqual(self.artifacts.read(old['packet']), packet_bytes)
        self.assertEqual(records['review_assignment']['old']['prompt'], prompt)
        self.assertEqual(protocol.build_assignment_request(records, self.artifacts, old), json.loads(request_bytes))
        self.assertTrue(protocol.authenticate_attempt(records, self.artifacts, old, attempt))

    def test_a_fresh_repair_series_uses_v2_source_rules_before_planning(self):
        from research_harness import review_parts_repair
        case = repair_fixtures.BarRepairJourneyTests()
        case.source_rules_protocol = RULES
        case.setUp()
        self.addCleanup(case.doCleanups)
        original = case.store.snapshot()['records']['review_assignment']['assignment-bar-a']
        self.assertEqual(case.packet(original).get('source_rules'), RULES)
        with patch.object(review_parts_repair, 'requires_parts', return_value=True):
            assignment = case.assign_repair()
        self.assertIn('review_parts', case.packet(assignment))
        self.assertNotIn('source_rules', case.packet(assignment))
        self.assertNotIn('source_rules', assignment['prompt']['parameters']['base_prompt']['parameters'])
        self.assertTrue(protocol._prompt_matches(assignment, packet=case.packet(assignment)))

    def test_readiness_series_keep_their_key_and_v2_rules_after_a_v3_entry(self):
        case = study_fixtures.ReadinessPartsTests()
        case.setUp()
        self.addCleanup(case.doCleanups)
        case.logical.update(source_rules=RULES, source_artifacts=[])
        case.enlarge_readiness()
        first = case.assign()
        self.assertNotIn('source_rules', case.packet(first))
        case.invoke(first)
        second = case.assign('second', first['id'])
        self.assertNotIn('source_rules', case.packet(second))
        self.assertNotIn('source_artifacts', case.packet(second))
        self.assertEqual(case.packet(first)['review_parts']['series_key'],
                         case.packet(second)['review_parts']['series_key'])
        case.invoke(second)

    def test_a_new_parts_series_keeps_its_existing_source_contract(self):
        case = review_parts_fixtures.PartsResultCase()
        self.addCleanup(case.doCleanups)
        case.setUp()
        case.oversized()
        ordinary = protocol.build_packet(case.case.read_records(), case.case.artifacts, None, 'result', 'first')
        self.assertEqual(ordinary.get('source_rules'), RULES)
        assignment = case.assign()
        self.assertIn('review_parts', case.packet(assignment))
        self.assertNotIn('source_rules', case.packet(assignment))
        self.assertNotIn('source_rules', assignment['prompt']['parameters']['base_prompt']['parameters'])

    def test_an_active_v3_session_is_not_downgraded_to_parts(self):
        case = review_parts_fixtures.PartsResultCase()
        self.addCleanup(case.doCleanups)
        case.setUp()
        first = case.assign()
        self.assertEqual(case.packet(first).get('source_rules'), RULES)
        output = case.case.build_review_output('result')
        output['value']['status'] = 'unresolved'
        output['source_requests'] = [dict(id='page', kind='inventory_page', page=0)]
        case.invoke(first, output)
        case.oversized()
        with self.assertRaises(ResearchError) as caught:
            case.assign('too-large', first['id'])
        self.assertEqual(caught.exception.code, 'review_input_too_large')
        self.assertEqual(case.packet(first).get('source_rules'), RULES)

    def test_the_v3_placeholder_is_versioned_and_pins_its_digest(self):
        prompt = protocol.canonical_prompt('bar', source_rules=RULES)
        self.assertEqual(prompt['parameters']['source_rules'], RULES)
        self.assertTrue(prompt['template_id'].endswith('/sources-v3'))
        self.assertEqual(prompt['rendered_prompt_hash'], digest(prompt['text']))
        self.assertEqual(hashlib.sha256(bar_sources.SESSION_PROMPT.encode()).hexdigest(),
                         'eb08845b29d86ed9ad223b733e99af82e3276ccca0a17b936c1ced5fd47d5344')

    def test_settling_after_three_of_twenty_two_parts_reaches_the_next_final(self):
        # Incident 20261111235418-bar-final-refused-for-text-delivered-in-reduced-history:
        # a source continuation can stop delivering the rest without claiming it was read.
        content = 'x' * 2200
        version = self.metadata(4)
        self.capture(version, pdf_text=content)
        self.requests = [dict(id='long', kind='source', version_id=version, depth='fulltext')]
        replacement = self.store.snapshot()['records']['work'][version]['fulltexts'][0]['text']
        with patch.object(review_excerpts, 'TURN_FULLTEXT_CHARACTERS', 100):
            self.assign('start')
            self.assertEqual(self.packet('start').get('source_rules'), RULES)
            self.invoke('start', self.build_request_turn(self.requests[:1]))
            parent = 'start'
            for index in range(1, 4):
                child = 'part-' + str(index)
                self.assign(child, prior_assignment_id=parent)
                self.assertEqual(self.packet(child)['source_deliveries'][0]['part']['parts'], 22)
                output = self.build_request_turn([])
                if index == 3:
                    output['settled_requests'] = [self.requests[0]['id']]
                self.invoke(child, output)
                self.assertTrue(self.state(child)['ready'])
                parent = child
            records = self.store.snapshot()['records']
            self.assertEqual(review_continuation.plan_next(records, self.artifacts, parent)['state'], 'continue')
            with self.assertRaises(ResearchError) as caught:
                self.record_final(parent, output)
            self.assertEqual(caught.exception.code, 'review_sources_pending')
            self.assign('final', prior_assignment_id=parent)
            packet = self.packet('final')
            self.assertEqual(packet['source_deliveries'], [])
            self.assertEqual(packet['pending_source_requests'], [])
            settlement = packet['settled_source_requests'][0]
            self.assertEqual(settlement['undelivered_spans'], [dict(
                artifact_descriptor=bar_sources.descriptor(replacement), start=300, end=2200)])
            final = self.review_response('bar')
            final['limitations'].extend(settlement['limitations'])
            source = self.store.snapshot()['records']['work'][version]['fulltexts'][0]['source_id']
            def cite_span(start, end):
                return dict(kind='source', link=dict(version_id=version, source_id=source, artifact=replacement,
                    locator=dict(kind='span', start=start, end=end,
                                 sha256=hashlib.sha256(content[start:end].encode()).hexdigest())))
            refused = copy.deepcopy(final)
            refused['value']['evidence'] = [cite_span(300, 400)]
            invoke_codex(self, 'final', refused, 'undelivered-final')
            failure = review_refusal.classify_observed_output(self.store.snapshot()['records'],
                                                              self.artifacts, 'undelivered-final')
            self.assertEqual(failure['code'], 'review_evidence_undelivered')
            with self.assertRaises(ResearchError) as caught:
                self.record_final('final', refused)
            self.assertEqual(caught.exception.code, 'review_evidence_undelivered')
            received = copy.deepcopy(final)
            received['value']['evidence'] = [cite_span(0, 100)]
            records = self.store.snapshot()['records']
            research_decisions.validate_review_contract(records, self.artifacts,
                records['review_assignment']['final'], received,
                attempt=records['review_attempt']['undelivered-final'])
            self.invoke('final', final)
            self.assertTrue(self.state('final')['ready'])
            self.record_final('final', final)
            self.assertNotIn('reading', self.store.snapshot()['records'])

    def test_artifact_requests_require_a_reference_and_keep_undelivered_spans_uncredited(self):
        artifact = self.artifacts.put(b'abcdefghijklmnopqrstuv', 'text/plain')
        request = dict(id='artifact-1', kind='artifact', sha256=artifact['sha256'])
        packet = dict(source_rules=RULES, pending_source_requests=[],
                      dossier=dict(output=protocol._reference_templates(artifact)))
        output = self.build_request_turn([request])
        self.assertEqual(bar_sources.pending(packet, output), [request])
        for changed in (dict(request, sha256='0' * 64), dict(request, sha256='invalid')):
            with self.subTest(request=changed), self.assertRaises(ResearchError) as caught:
                bar_sources.pending(packet, self.build_request_turn([changed]))
            self.assertEqual(caught.exception.code, 'invalid_review_source_request')
        self.assign('start')
        initial = self.packet('start')
        initial['dossier'] = packet['dossier']
        with patch.object(review_excerpts, 'TURN_FULLTEXT_CHARACTERS', 10):
            delivery = bar_sources.continuation(self.store.snapshot()['records'], self.artifacts, initial, output)
        part = delivery['source_deliveries'][0]
        self.assertEqual(part['part']['start'], 0)
        self.assertEqual(part['part']['end'], 10)
        self.assertEqual(self.artifacts.read(part['excerpt']), b'abcdefghij')
        self.assertEqual(part['origin']['artifact_descriptor'], bar_sources.descriptor(artifact))
        self.assertFalse(part['reading_credit'])
        sent = dict(delivery, evidence=protocol._evidence(self.artifacts, delivery))
        view = review_delivered_evidence.project_delivery_view(self.store.snapshot()['records'], self.artifacts, [sent])
        def cite_span(start, end):
            quote = b'abcdefghijklmnopqrstuv'[start:end]
            return dict(kind='source', link=dict(version_id=self.versions[0], source_id='held-source',
                artifact=artifact, locator=dict(kind='span', start=start, end=end,
                                               sha256=hashlib.sha256(quote).hexdigest())))
        records = self.store.snapshot()['records']
        review_refusal.validate_evidence_contract(records, self.artifacts, [cite_span(0, 10)], packets=[sent])
        self.assertEqual(research_decisions.find_review_undelivered_references(
            records, self.artifacts, [cite_span(0, 10)], view), [])
        self.assertEqual(len(research_decisions.find_review_undelivered_references(
            records, self.artifacts, [cite_span(10, 20)], view)), 1)
        self.assertNotIn(artifact['sha256'], view['artifacts'])

    def test_artifact_requests_refuse_binary_and_non_utf8_records_at_admission(self):
        self.assign('start')
        for content, media in ((b'%PDF-1.4\n', 'application/pdf'), (b'\xff\xfe', 'application/octet-stream')):
            artifact = self.artifacts.put(content, media)
            packet = dict(source_rules=RULES, dossier=dict(output=protocol._reference_templates(artifact)))
            output = self.build_request_turn([dict(id='artifact', kind='artifact', sha256=artifact['sha256'])])
            with self.subTest(media=media):
                with self.assertRaises(ResearchError) as caught:
                    bar_sources.validate_requested_artifacts(self.artifacts, packet, output)
                self.assertEqual(caught.exception.code, 'invalid_review_source_request')

    def test_a_final_must_name_the_undelivered_settled_ranges(self):
        packet = dict(source_rules=RULES, settled_source_requests=[dict(limitations=[
            'Request source: artifact digest characters [300, 2200) were not delivered.'])])
        with self.assertRaises(ResearchError) as caught:
            bar_sources.validate_settlement_limitations(packet, self.review_response('bar'))
        self.assertEqual(caught.exception.code, 'invalid_value_review')

    def test_settling_without_a_capture_keeps_an_explicit_final_limitation(self):
        version = self.metadata(4)
        request = dict(id='uncaptured', kind='source', version_id=version, depth='fulltext')
        self.assign('start')
        self.invoke('start', self.build_request_turn([request]))
        self.assign('unavailable', prior_assignment_id='start')
        self.assertEqual(self.packet('unavailable')['pending_source_requests'], [request])
        self.invoke('unavailable', dict(self.build_request_turn([]), settled_requests=['uncaptured']))
        self.assign('final', prior_assignment_id='unavailable')
        limitation = bar_sources.MISSING_FULLTEXT_LIMITATION.format(id='uncaptured', version_id=version)
        self.assertEqual(self.packet('final')['settled_source_requests'], [dict(
            request=request, undelivered_spans=[], limitations=[limitation])])
        output = self.review_response('bar')
        output['limitations'].append(limitation)
        self.invoke('final', output)
        self.record_final('final', output)
        self.assertFalse(self.store.snapshot()['records'].get('reading'))

    def test_settlement_counts_offsets_even_when_source_parts_have_equal_content(self):
        version = self.metadata(4)
        self.capture(version, pdf_text='x' * 300)
        request = dict(id='repeated', kind='source', version_id=version, depth='fulltext')
        with patch.object(review_excerpts, 'TURN_FULLTEXT_CHARACTERS', 100):
            self.assign('start')
            self.invoke('start', self.build_request_turn([request]))
            self.assign('first', prior_assignment_id='start')
            self.invoke('first', dict(self.build_request_turn([]), settled_requests=['repeated']))
            self.assign('next', prior_assignment_id='first')
        spans = self.packet('next')['settled_source_requests'][0]['undelivered_spans']
        self.assertEqual([(span['start'], span['end']) for span in spans], [(100, 300)])

    def test_a_context_repair_keeps_the_predecessors_new_settlement_ranges(self):
        version = self.metadata(4)
        self.capture(version, pdf_text='a' * 100 + 'b' * 100 + 'c' * 100)
        request = dict(id='settled', kind='source', version_id=version, depth='fulltext')
        with patch.object(review_excerpts, 'TURN_FULLTEXT_CHARACTERS', 100):
            self.assign('start')
            self.invoke('start', self.build_request_turn([request]))
            self.assign('first', prior_assignment_id='start')
            self.invoke('first', dict(self.build_request_turn([]), settled_requests=['settled']))
            self.mutate(protocol.record_context_event, dict(id='exposure', assignment_id='first',
                kind='contamination', source='author_history', reason='An author note entered the context.',
                evidence=[self.evidence]))
            probe_codex_route(self, 'repair-probe')
            self.assign('repair', reviewer='repair-reviewer', context_repair=dict(
                assignment_id='first', event_ids=['exposure'], probe_id='repair-probe',
                reason='A fresh reviewer reassesses the same evidence.', evidence=[self.evidence]))
        spans = self.packet('repair').get('settled_source_requests', [{}])[0].get('undelivered_spans', [])
        self.assertEqual([(span['start'], span['end']) for span in spans], [(100, 300)])

    def test_complete_source_lineage_accepts_v3_artifacts_and_excludes_settled_fulltexts(self):
        self.assign('start')
        records = self.store.snapshot()['records']
        prior = dict(records['review_assignment']['start'], role='result')
        artifact_request = dict(id='artifact', kind='artifact', sha256='0' * 64)
        output = self.build_request_turn([self.requests[0], artifact_request])
        requested = review_source_complete.requests_for_lineage(records, self.artifacts, prior, output)
        self.assertEqual(requested, self.requests[:1])
        self.invoke('start', self.build_request_turn(self.requests[:1]))
        self.assign('first', prior_assignment_id='start')
        records = self.store.snapshot()['records']
        prior = dict(records['review_assignment']['first'], role='result')
        output = dict(self.build_request_turn([]), settled_requests=[self.requests[0]['id']])
        self.assertEqual(review_source_complete.requests_for_lineage(records, self.artifacts, prior, output), [])

    def test_complete_source_mode_retains_a_partially_delivered_artifact_request(self):
        self.assign('start')
        self.invoke('start', self.build_request_turn(self.requests[:1]))
        records = self.store.snapshot()['records']
        prior = dict(records['review_assignment']['start'], role='result')
        artifact = self.artifacts.put(b'a' * 10 + b'b' * 10 + b'c' * 10, 'text/plain')
        previous = self.packet('start')
        previous['dossier'] = dict(output=protocol._reference_templates(artifact))
        request = dict(id='artifact', kind='artifact', sha256=artifact['sha256'])
        output = self.build_request_turn([request] + self.requests[:1])
        with patch.object(review_excerpts, 'TURN_FULLTEXT_CHARACTERS', 10):
            packet = bar_sources.continuation(records, self.artifacts, previous, output)
        self.assertEqual(packet['source_deliveries'][0]['part']['end'], 10)
        review_source_complete.prepare(records, self.artifacts, prior, self.state('start'), packet)
        self.assertIn(request, packet['pending_source_requests'])

    def test_v2_rejects_new_vocabulary_and_continues_with_its_exact_prompt(self):
        # Explicit old protocol, no v3 marker: byte equality covers prompt, packet and request replay.
        with patch.object(bar_sources, 'SESSION_PROTOCOL', bar_sources.PROTOCOL):
            self.assign('old')
        old = self.store.snapshot()['records']['review_assignment']['old']
        before = copy.deepcopy(old['prompt'])
        self.invoke('old', self.build_request_turn(self.requests[:1]))
        attempt = self.store.snapshot()['records']['review_attempt']['attempt-old']
        request = self.artifacts.read(attempt['request'])
        self.assign('old-next', prior_assignment_id='old')
        self.assertNotEqual(self.packet('old-next').get('source_rules'), RULES)
        records = self.store.snapshot()['records']
        self.assertEqual(records['review_assignment']['old']['prompt'], before)
        self.assertEqual(json.loads(request), protocol.build_assignment_request(records, self.artifacts, old))
        self.assertTrue(protocol.authenticate_attempt(records, self.artifacts, old, attempt))
        for output in (self.build_request_turn([dict(id='a', kind='artifact', sha256='0' * 64)]),
                       dict(self.build_request_turn([]), settled_requests=[self.requests[0]['id']])):
            with self.subTest(output=output), self.assertRaises(ResearchError) as caught:
                bar_sources.pending(self.packet('old-next'), output)
            self.assertEqual(caught.exception.code, 'invalid_review_source_request')

    def test_settlement_refuses_unknown_duplicate_and_non_fulltext_requests(self):
        packet = dict(source_rules=RULES, pending_source_requests=[self.requests[0],
                      dict(id='page', kind='inventory_page', page=0)])
        for ids in (['absent'], ['page'], [self.requests[0]['id']] * 2, 'not-an-array'):
            with self.subTest(ids=ids), self.assertRaises(ResearchError) as caught:
                bar_sources.pending(packet, dict(self.build_request_turn([]), settled_requests=ids))
            self.assertEqual(caught.exception.code, 'invalid_review_source_request')
        with self.assertRaises(ResearchError) as caught:
            bar_sources.pending(packet, dict(self.build_request_turn(self.requests[:1]),
                                            settled_requests=[self.requests[0]['id']]))
        self.assertEqual(caught.exception.code, 'invalid_review_source_request')
