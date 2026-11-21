"""The prompt of a turn under the session rules v4 holds the instructions of its role.

Rules V1 to V3 and V5 of the design session-v4-role-prompt, rules S1 to S3 of the study forms (revision 2), and the
plan after a recorded problem review of a v4 session. Each test sets NEW_SESSION_RULES for its own length.
"""

import copy
import unittest
from contextlib import nullcontext
from unittest.mock import patch

from codex_route_fixtures import invoke_codex, record_codex_route
from literature_fixtures import LiteratureCase
from strategy_fixtures import StrategyCase
import test_review_session_v4_turns as turn_fixtures
from research_harness import bar_sources, research_decisions as decisions, review_context_repair as repair
from research_harness import review_continuation as continuation, review_excerpts
from research_harness import review_prompt_paragraphs as paragraphs, review_protocol as protocol
from research_harness import review_route_capacity as capacity, review_session_v4 as session
from research_harness import review_transport as transport
from research_harness.errors import ResearchError
from research_harness.evidence import digest

ROLES = ('bar', 'slate', 'result')
KINDS = ('request', 'final', 'page', 'closing', 'combine')


class RolePromptRenderTests(unittest.TestCase):
    """Pure renders of the turn prompts (rule V1 with findings F1 and F2, rule S1, rule S3)."""

    def test_every_turn_starts_with_its_role_base_without_the_texts_of_other_rule_sets(self):
        common = protocol._select_common_prompt(protocol.COMMON_VERSION)
        for orchestration in ('problem_creation', None):
            templates = protocol._PROBLEM_PROMPTS if orchestration else protocol._ROLE_PROMPTS
            for role in ROLES:
                for suffix in (None, repair.PROMPT, repair.EXHAUSTION_PROMPT_V1):
                    _, base = session.build_base_prompt(role, orchestration, repair_suffix=suffix)
                    self.assertIn(templates[role], base['text'])
                    for kind in KINDS:
                        prompt = session.build_turn_prompt(role, kind, {}, orchestration=orchestration, repair_suffix=suffix)
                        text = prompt['text']
                        self.assertTrue(text.startswith(base['text']), (orchestration, role, kind))
                        self.assertEqual(text.count(common), 1)
                        self.assertNotIn(bar_sources.PROMPT, text)
                        self.assertNotIn(bar_sources.SESSION_PROMPT, text)
                        self.assertFalse(any(rules in text for rules in review_excerpts.PHASE_PROMPTS.values()))
                        self.assertEqual(text.count(suffix) if suffix else 0, 1 if suffix else 0)
                        self.assertEqual(session.SESSION_BASE_SHAPE_PROMPT in text, kind in ('request', 'combine'))
                        self.assertEqual(session.SESSION_PART_SHAPE_PROMPT in text, kind in ('page', 'closing'))
                        self.assertEqual(prompt['parameters'].get('context_repair'), True if suffix else None)

    def test_problem_bar_final_holds_its_template_and_response_and_study_bar_final_its_own(self):
        problem = session.build_turn_prompt('bar', 'final', {}, orchestration='problem_creation')['text']
        self.assertIn(protocol._PROBLEM_PROMPTS['bar'], problem)
        self.assertIn(protocol._PROBLEM_BAR_EXAMPLE, problem)
        study = session.build_turn_prompt('bar', 'final', {}, orchestration=None)['text']
        self.assertIn(protocol._ROLE_PROMPTS['bar'], study)
        self.assertIn(protocol._RESPONSE.replace('ROLE', 'bar'), study)

    def test_s1_study_final_page_and_closing_prompts_hold_the_forms_and_problem_prompts_do_not(self):
        for role in ROLES:
            for kind in KINDS:
                study = session.build_turn_prompt(role, kind, {}, orchestration=None)['text']
                problem = session.build_turn_prompt(role, kind, {}, orchestration='problem_creation')['text']
                self.assertEqual(session.STUDY_FORMS_PROMPT in study, kind in ('final', 'page', 'closing'))
                self.assertNotIn(session.STUDY_EVIDENCE_FORMS_PROMPT, problem)
                self.assertNotIn(session.STUDY_HANDLE_PROMPT, problem)

    def test_s3_study_series_final_holds_the_forms_and_a_v2_series_final_keeps_its_bytes(self):
        capacity_binding = dict(output_tokens=128000, answer_bound=20000)
        for role, adapter, options in (('result', 'study_readiness', {}), ('bar', 'context_repair', dict(repair=True))):
            for form in (None, 'page', 'closing'):
                binding = dict(adapter=adapter, kind='final', protocol='exactory-review-parts-v3', capacity=capacity_binding)
                if form:
                    binding['final_form'] = form
                text = protocol.canonical_prompt(role, review_parts=binding, **options)['text']
                self.assertIn(session.STUDY_FORMS_PROMPT.lstrip(), text, (adapter, form))
            read = dict(adapter=adapter, kind='read', protocol='exactory-review-parts-v3', capacity=capacity_binding)
            self.assertNotIn(session.STUDY_EVIDENCE_FORMS_PROMPT, protocol.canonical_prompt(role, review_parts=read, **options)['text'])
        problem = dict(adapter='problem_result', kind='final', protocol='exactory-review-parts-v3', capacity=capacity_binding)
        self.assertNotIn(session.STUDY_EVIDENCE_FORMS_PROMPT, protocol.canonical_prompt(
            'result', orchestration='problem_creation', excerpts=True, review_parts=problem)['text'])
        # The digests of the base commit of this change: a parts v2 series of a live record keeps its prompt.
        self.assertEqual(digest(protocol.canonical_prompt('result', excerpts=True, review_parts=dict(
            adapter='study_readiness', kind='final'))), '38e5dc7609b68d4c4d30df2ad8909654ebd774e195d4277ed98b982d94418573')
        self.assertEqual(digest(protocol.canonical_prompt('bar', repair=True, excerpts=True, review_parts=dict(
            adapter='context_repair', kind='final'))), '909f621fb51105d9b8d67d576486d8fa9d52839e028b44a10b09bd729a668648')


class SessionRolePromptTests(unittest.TestCase):
    """Recorded v4 sessions of the problem fixture of the turn tests."""
    setUp = turn_fixtures.SessionTurnsTests.setUp
    read_packet = turn_fixtures.SessionTurnsTests.read_packet
    add_sources = turn_fixtures.SessionTurnsTests.add_sources
    respond = turn_fixtures.SessionTurnsTests.respond
    finish = turn_fixtures.SessionTurnsTests.finish
    build_final = turn_fixtures.SessionTurnsTests.build_final
    record_legacy_bars = turn_fixtures.SessionTurnsTests.record_legacy_bars

    def test_v1_v2_slate_turn_holds_places_paragraph_and_verifies_only_with_the_selected_paragraphs(self):
        self.record_legacy_bars()
        current = self.case.assign('slate', 'first', identifier='slate-start')
        records, artifacts = self.case.read_records(), self.case.artifacts
        assignment = records['review_assignment'][current['id']]
        packet = self.read_packet(assignment)
        logical = protocol._read_json(artifacts, assignment['session_chain']['logical'])
        self.assertIn(paragraphs.PLACE_REVIEW_KEY, logical)
        self.assertIn(paragraphs.SLATE_PLACES_PROTOCOL, assignment['prompt']['parameters'][paragraphs.PARAMETER])
        self.assertIn(protocol._PROBLEM_PROMPTS['slate'], assignment['prompt']['text'])
        self.assertTrue(session.matches_turn_prompt(assignment, packet, logical))
        self.assertTrue(session.authenticate_session_binding(records, artifacts, assignment, packet))
        selected = paragraphs.select_prompt_parameters('slate', logical)
        forming = next(paragraph for paragraph in paragraphs.PARAGRAPHS if paragraph.name == 'slate-forming')
        for versions in ([version for version in selected[paragraphs.PARAMETER] if version != paragraphs.SLATE_PLACES_PROTOCOL],
                         [forming.versions[0][0]] + selected[paragraphs.PARAMETER]):
            # A self-consistent prompt with another paragraph choice rebuilds from its own record, and the
            # authentication of the turn refuses it against the logical packet of the chain.
            forged = dict(assignment, prompt=session.build_turn_prompt('slate', 'request',
                assignment['prompt']['parameters']['capacity'], orchestration='problem_creation',
                paragraphs=dict(selected, **{paragraphs.PARAMETER: versions})))
            self.assertTrue(session.matches_turn_prompt(forged, packet))
            self.assertFalse(session.matches_turn_prompt(forged, packet, logical))
            with self.assertRaises(ResearchError) as caught:
                session._authenticate_session_binding(records, artifacts, forged, packet)
            self.assertEqual(caught.exception.code, 'review_invocation_unverified')
        altered = copy.deepcopy(assignment)
        altered['prompt']['text'] = altered['prompt']['text'].replace(protocol._PROBLEM_PROMPTS['slate'], '')
        self.assertFalse(session.matches_turn_prompt(altered, packet))

    def test_v3_largest_base_keeps_request_and_final_within_their_bounds_at_effort_max(self):
        """The size of the base prompt is the subject: a problem result whose logical packet selects every result
        paragraph, about 49,000 characters of base, planned on the Claude Opus 5.5 route at effort max, the
        tightest route class (finding F4). The request turn delivers a requested full text to its material level."""
        from test_review_route_capacity import build_route
        self.record_legacy_bars()
        self.add_sources([200000])
        records, artifacts = self.case.read_records(), self.case.artifacts
        logical = protocol.build_packet(records, artifacts, None, 'result', 'first')
        every = {paragraphs.PARAMETER: [paragraph.versions[-1][0] for paragraph in paragraphs.PARAGRAPHS[1:]
                                        if 'result' in paragraph.roles and paragraph.name not in paragraphs.SHARED_PARAGRAPH_NAMES
                                        and paragraph.name != 'capacity-notice'],
                 paragraphs.CARRY_PARAMETER: dict(protocol=paragraphs.CARRY_PROTOCOL, import_set_digest='0' * 64)}
        route = build_route('claude_cli_v1', 'claude-opus-5-5', 'max', 128000)
        with patch.object(paragraphs, 'select_prompt_parameters', return_value=every):
            base = session.measure_base_prompt('result', logical)
            admission = session.check_v4_admission(records, artifacts, route, 'result', logical)
        self.assertGreater(base, 45000)
        self.assertEqual(admission['state'], 'record', admission.get('details'))
        plan = admission['capacity_plan']
        for content in admission['bodies'].values():
            artifacts.put(content, 'application/json')
        inventory = admission['inventory']['inventory']
        for unit in inventory['units']:
            artifacts.put(admission['inventory']['bodies'][unit['sha256']], unit['artifact']['media_type'])
        unit = next(row for row in inventory['units'] if row['kind'] == 'fulltext')
        notes = 'n' * plan['allocation']['notes']
        observation = dict(number=1, assignment_id='turn-1', attempt_id='attempt-1', deliveries=[], output=dict(
            schema_version=1, stage='request', notes=notes, excerpts=[], settled_requests=[],
            requests=[dict(id='full', kind='unit', unit=unit['handle'])]))
        state = session.build_session_state(inventory, [observation], route, plan)['state']
        for kind in ('request', 'final'):
            limits = capacity.derive_plan_task_limits(plan, kind)
            packet = session.build_session_packet(artifacts, route, logical, 'chain', 2, inventory, state, plan, limits, kind)
            if kind == 'request':
                self.assertTrue(packet['evidence'])
            prompt = session.build_turn_prompt('result', kind, session.read_session_binding(packet)['capacity'],
                                               orchestration='problem_creation', paragraphs=every)
            request = transport.request_body(route, prompt['text'], packet, [], order=())
            self.assertLessEqual(capacity.measure_request(route, request)['text_characters'],
                                 limits['request_bound']['text_characters'], kind)
        # The page, closing and combine turns differ from the final by fixed turn texts inside the envelope, which also
        # holds the binding, the delivery descriptors and the bounded source rows.
        for kind in KINDS:
            text = session.build_turn_prompt('result', kind, {}, orchestration='problem_creation', paragraphs=every)['text']
            self.assertLessEqual(len(text) - base, session.FRAME_ENVELOPE_CHARACTERS // 2, kind)

    def test_v3_route_admitted_without_the_base_is_below_the_floor_with_it(self):
        from research_harness import review_capacity_evidence as evidence
        records, artifacts = self.case.read_records(), self.case.artifacts
        route = records['review_route']['route']
        logical = protocol.build_packet(records, artifacts, None, 'bar', 'first')
        read = evidence.read_capacity_evidence
        def admit(tokens, base=True):
            """Admission on the route as a probe that observed this many input tokens would record it; without the
            base, the frames reserve the envelope alone, as before rule V3."""
            reader = patch.object(evidence, 'read_capacity_evidence',
                                  side_effect=lambda *args, **kwargs: dict(read(*args, **kwargs), input_tokens=tokens))
            reserve = patch.object(session, 'measure_base_prompt', return_value=0) if not base else nullcontext()
            with reader, reserve:
                return session.check_v4_admission(records, artifacts, route, 'bar', logical)
        low, high = 1, capacity.describe_route_capacity(route, records=records, artifacts=artifacts)['input_tokens']['value']
        self.assertEqual(admit(high)['state'], 'record')
        while high - low > 1:
            middle = (low + high) // 2
            low, high = (low, middle) if admit(middle)['state'] == 'record' else (middle, high)
        self.assertEqual(admit(low)['reason'], 'below_floor')
        self.assertEqual(admit(low, base=False)['state'], 'record')

    def test_v3_admission_of_a_packet_with_a_capacity_notice_counts_the_notice_in_the_base(self):
        """The slate series check of the base path admits a packet that prepare_capacity_notice gave a notice."""
        records, artifacts = self.case.read_records(), self.case.artifacts
        route = records['review_route']['route']
        logical = protocol.build_packet(records, artifacts, None, 'bar', 'first')
        noticed = dict(logical, **{paragraphs.CAPACITY_NOTICE_KEY: dict(protocol=paragraphs.CAPACITY_NOTICE_PROTOCOL,
                                                                         kind='request', output_tokens=128000, answer_bound=20000)})
        self.assertGreater(session.measure_base_prompt('bar', noticed), session.measure_base_prompt('bar', logical))
        self.assertEqual(session.check_v4_admission(records, artifacts, route, 'bar', noticed)['state'], 'record')

    def test_v5_new_bar_below_the_v4_floor_stops_before_its_call_and_names_a_serving_route(self):
        record_codex_route(self.case, 'small', model='gpt-6-astra', output_tokens=4000)
        payload = dict(id='small-bar', route_id='small', dossier_id=None, role='bar', reviewer_id='first',
                       author_id='author', context={})
        with self.assertRaises(ResearchError) as caught:
            self.case.mutate(protocol.record_assignment, payload)
        details = caught.exception.details
        self.assertEqual(caught.exception.code, 'review_route_capacity_below_floor')
        self.assertEqual(details['reason'], session.SESSION_BELOW_FLOOR_REASON)
        self.assertEqual(details['admission']['reason'], 'below_floor')
        self.assertEqual([row['route_id'] for row in details['serving_routes']], ['route'])
        step = details['then'][0]['steps'][0]
        self.assertEqual((step['command'], step['payload']['route_id']), ('review-assignment', 'route'))
        self.assertNotIn('small-bar', self.case.read_records().get('review_assignment', {}))

    def test_v5_new_slate_below_the_v4_floor_starts_on_the_serving_route_without_repairing_its_bar(self):
        self.record_legacy_bars()
        record_codex_route(self.case, 'small', model='gpt-6-astra', output_tokens=4000)
        with self.assertRaises(ResearchError) as caught:
            self.case.mutate(protocol.record_assignment, dict(id='small-slate', route_id='small', dossier_id=None,
                role='slate', reviewer_id='first', author_id='author', context={}))
        answer = caught.exception.details['then'][0]
        self.assertEqual([(step['id'], step['payload']['route_id']) for step in answer['steps']], [(14, 'route')])

    def test_v5_admission_stops_for_another_reason_take_the_next_rule_set(self):
        for number, reason in enumerate(('unsupported_role_or_route', 'binary_required_evidence')):
            stop = dict(state='stop', protocol=session.PROTOCOL, reason=reason)
            with patch.object(session, 'check_v4_admission', return_value=stop):
                assignment = self.case.assign('bar', 'first' if number == 0 else 'second', identifier='next-rules-%d' % number)
            self.assertNotIn('session_chain', assignment)
            self.assertEqual(self.read_packet(assignment)['source_rules'], bar_sources.PROTOCOL_V3)

    def test_recorded_v4_bar_ends_its_series_when_an_area_preceded_it(self):
        self.case.put('problem_area', 'aux', dict(id='aux', area='math.AG', reason='A further area.'))
        final, unit = self.build_final()
        self.finish(final, [dict(ref=unit['handle'])])
        plan = continuation.plan_next(self.case.read_records(), self.case.artifacts, final['id'])
        self.assertEqual(plan['state'], 'done', plan)


class StudyBarFormsTests(StrategyCase):
    """A study bar under the session rules v4 (rules S1 and S2 of the study forms)."""
    metadata = LiteratureCase.metadata
    capture = LiteratureCase.capture
    bundle = LiteratureCase.bundle
    full_note = LiteratureCase.full_note
    link = LiteratureCase.link
    span = LiteratureCase.span

    def setUp(self):
        super().setUp()
        self.sequence = 0
        self.setup_dossier()
        record_codex_route(self, 'route-v4', model='gpt-6-astra', output_tokens=128000)
        self.version = self.metadata(1)
        self.capture(self.version, pdf_text=('Source 1. ' + 'x' * 2000)[:1982] + ' References: none.')
        preferred = patch.object(protocol, 'NEW_SESSION_RULES', (session.PROTOCOL, bar_sources.PROTOCOL_V3))
        preferred.start()
        self.addCleanup(preferred.stop)

    def records(self):
        return self.store.snapshot()['records']

    def assign(self, identifier, **context):
        return self.mutate(protocol.record_assignment, dict(id=identifier, route_id='route-v4', dossier_id='dossier-1',
            role='bar', reviewer_id='reviewer-a', author_id='author', context=context))['result']

    def test_s2_refused_final_with_written_out_entries_is_replaced_and_recorded_after_a_handle(self):
        """Kinds of entry of the incident at one entry each: a descriptor under a member name, and the members of a
        source link written out with and without the locator."""
        current = self.assign('turn-0')
        _, inventory, _ = session.read_bound_session(self.artifacts, current)
        unit = next(row for row in inventory['units'] if row['kind'] == 'fulltext')
        invoke_codex(self, current['id'], dict(schema_version=1, stage='request', notes='Notes.', excerpts=[],
            settled_requests=[], requests=[dict(id='source', kind='source', version_id=self.version, depth='fulltext')]))
        for number in range(1, 10):
            current = self.assign('turn-%d' % number, prior_assignment_id=current['id'])
            if 'session_final' in protocol._read_json(self.artifacts, current['packet']):
                break
            invoke_codex(self, current['id'], dict(schema_version=1, stage='request', notes='Notes.', excerpts=[],
                                                   requests=[], settled_requests=[]))
        self.assertIn(session.STUDY_FORMS_PROMPT, current['prompt']['text'])
        capture = self.records()['work'][self.version]['fulltexts'][0]
        link = dict(artifact=capture['text'], source_id=capture['source_id'], version_id=self.version,
                    locator=dict(kind='span', start=0, end=20, sha256=capture['text']['sha256']))
        refused = self.review_response('bar')
        refused['value'].update(status='unresolved', evidence=[dict(artifact=capture['text']), dict(link),
            {key: link[key] for key in ('artifact', 'source_id', 'version_id')}])
        invoke_codex(self, current['id'], refused)
        stored = self.records()['review_output_refusal']['attempt-' + current['id']]
        self.assertEqual((stored['code'], stored['check'], stored['details']['path'], stored['details']['refused_count']),
                         ('invalid_strategy', 'evidence.shape', '/value/evidence', 3))
        plan = continuation.plan_next(self.records(), self.artifacts, current['id'])
        self.assertEqual((plan['state'], plan['command']), ('replace', 'review-assignment'), plan)
        replacement = self.mutate(protocol.record_assignment, plan['payload'])['result']
        self.assertIn('output_refusal', protocol._read_json(self.artifacts, replacement['packet']))
        self.assertIn(session.STUDY_FORMS_PROMPT, replacement['prompt']['text'])
        bundle = self.bundle(self.version, capture=capture)
        from research_harness import literature, reading
        self.mutate(literature.import_bundle, bundle)
        self.mutate(reading.record_reading, self.full_note(bundle))
        answer = self.review_response('bar')
        answer['value'].update(status='unresolved', evidence=[dict(ref=unit['handle'])])
        invoke_codex(self, replacement['id'], answer)
        from research_harness.review_composite import read_final_output
        expanded, _ = read_final_output(self.records(), self.artifacts, replacement)
        saved = self.mutate(decisions.record_value_review, dict(expanded, id='final-bar', dossier_id='dossier-1',
                                                                assignment_id=replacement['id']))['result']
        self.assertEqual(saved['assignment_id'], replacement['id'])


if __name__ == '__main__':
    unittest.main()
