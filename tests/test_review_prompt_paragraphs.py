"""Ordered optional paragraphs preserve exact historical reviewer envelopes."""

import copy
from dataclasses import replace
import hashlib
import lzma
import itertools
import json
from pathlib import Path
import unittest
from unittest.mock import patch

from research_harness import review_protocol as protocol
from research_harness import review_context_repair as repair
from research_harness import review_excerpts, review_problem_parts, review_parts
from research_harness.evidence import digest
from research_harness.errors import ResearchError
from research_harness import review_prompt_paragraphs as paragraphs
from research_harness import review_staged_protocol as staged
from problem_parts_fixtures import ResultPartsCase


def build_packet(role='result', *, accounts=False, replacement=False, forming=False, areas=False, carry=False, item=None):
    value = dict(accounts={})
    if accounts:
        value['accounts']['answer'] = 'A supported conjectured answer.'
    if replacement:
        value['accounts']['replacement'] = dict(statement='A refuted earlier statement.')
    packet = dict(role=role, subject=dict(kind='problem'), problem_evidence=dict(value=value))
    if forming:
        packet['candidates'] = [dict(id='formed', source=dict(kind='phenomenon'))]
    if areas:
        packet['problem_areas'] = [dict(id='further-area')]
    if carry:
        packet['problem_objection_carry'] = CARRY
    if item is not None:
        packet['rubric_definition'] = dict(item=item, definition='Original anchor.', threshold='pass')
    return packet


def render_packet(packet, **options):
    return protocol.canonical_prompt(packet['role'], orchestration='problem_creation',
        **paragraphs.select_prompt_parameters(packet['role'], packet), **options)


def select_texts(prompt):
    parameters = prompt['parameters']
    versions = parameters.get('problem_paragraphs', [])
    if 'problem_objection_carry' in parameters:
        versions = ['exactory-problem-objection-carry-v1'] + versions
    return [text for paragraph in paragraphs.PARAGRAPHS for version, text in paragraph.versions if version in versions]


FROZEN = Path(__file__).with_name('fixtures') / 'review-prompts-0.82.0.json.xz'
CARRY = dict(protocol='exactory-problem-objection-carry-v1', import_set_digest='f' * 64)


def list_legacy_cases():
    """Enumerate the supported scientific/common, repair, excerpt and parts contracts."""
    cases = []
    for role, common, version in itertools.product(('bar', 'slate', 'result', 'adjudicator'), (1, 2, 3), range(1, 7)):
        if role == 'slate' and version == 1:
            continue
        options = dict(orchestration='problem_creation', version=version, common_version=common)
        variants = [dict(options)]
        if role != 'adjudicator':
            variants += [dict(options, repair=True, repair_prompt=suffix) for suffix in
                         repair.EARLIER_PROMPTS + (repair.PROMPT,)]
            if version == 6:
                variants += [dict(option, excerpts=True, excerpt_rules=rules)
                             for option in list(variants) for rules in review_excerpts.PHASE_PROMPTS]
        else:
            variants.append(dict(options, adjudicator_prompt=protocol._PROBLEM_ADJUDICATOR_PROMPT_BEFORE_NOT_BINDING))
        for option in variants:
            for carried in ((False, True) if role in ('slate', 'result') else (False,)):
                cases.append(dict(role=role, options=dict(option, **(
                    dict(problem_objection_carry=CARRY) if carried else {}))))
    for common, stage, rules in itertools.product((1, 2, 3), review_problem_parts.STAGES, review_excerpts.PHASE_PROMPTS):
        for suffix in ((None, review_problem_parts.PART_PROMPT_0691) if stage == 'part' else (None,)):
            for carried in (False, True):
                cases.append(dict(role='result', options=dict(orchestration='problem_creation', common_version=common,
                    excerpts=True, evidence_parts=stage, excerpt_rules=rules, part_suffix=suffix,
                    **(dict(problem_objection_carry=CARRY) if carried else {}))))
    for role, common, kind, repairing, composed, claim_range in itertools.product(
            ('result', 'adjudicator'), (2, 3),
            ('frame', 'read', 'combine', 'original_check', 'integrate', 'answer', 'final'),
            (False, True), (False, True), (False, True)):
        for carried in ((False, True) if role == 'result' else (False,)):
            cases.append(dict(role=role, options=dict(orchestration='problem_creation', common_version=common,
                staged_task=dict(type=kind, scope='joint_original', claim_ids=['A3'], occurrence_ids=['u1'], group_ids=[]),
                repair=repairing, composed=composed, claim_range=claim_range,
                **(dict(problem_objection_carry=CARRY) if carried else {}))))
    for role, adapter in (('result', 'problem_result'), ('result', 'context_repair'),
                          ('adjudicator', 'problem_adjudicator'), ('adjudicator', 'context_repair')):
        for common, kind in itertools.product((1, 2, 3), review_parts.KINDS):
            for answer in ((False, True) if kind == 'final' else (False,)):
                for carried in ((False, True) if role == 'result' else (False,)):
                    cases.append(dict(role=role, options=dict(orchestration='problem_creation', common_version=common,
                        review_parts=dict(adapter=adapter, kind=kind, answer=answer),
                        **(dict(problem_objection_carry=CARRY) if carried else {}))))
    return cases


def build_assignment(role, prompt):
    parameters = prompt['parameters']
    assignment = dict(role=role, subject=dict(kind='problem'), prompt=prompt,
                      reassessment_required=parameters.get('context_repair', False),
                      context=dict(prior_assignment_id='prior'))
    if review_parts.KEY in parameters:
        assignment[review_parts.KEY] = parameters[review_parts.KEY]
    return assignment


class HistoricalParagraphTests(unittest.TestCase):
    def test_0820_envelopes_render_and_recognize_exactly(self):
        frozen = json.loads(lzma.decompress(FROZEN.read_bytes()))
        self.assertEqual(frozen['origin'], '645a791 (Exactory 0.82.0)')
        self.assertEqual(len(frozen['cases']), len(list_legacy_cases()))
        self.assertEqual(len(frozen['cases']), 1182)
        self.assertEqual(sum(case['recognized'] for case in frozen['cases']), 1026)
        for case in frozen['cases']:
            with self.subTest(role=case['role'], options=case['options']):
                prompt = protocol.canonical_prompt(case['role'], **case['options'])
                # Compare complete UTF-8 envelope bytes, including text, metadata and both hashes.
                self.assertEqual(json.dumps(prompt, sort_keys=True).encode('utf-8'),
                                 json.dumps(case['prompt'], sort_keys=True).encode('utf-8'))
                assignment = build_assignment(case['role'], prompt)
                stored = build_assignment(case['role'], case['prompt'])
                self.assertEqual(digest(assignment), digest(stored))
                self.assertEqual(json.loads(json.dumps(stored)), assignment)
                self.assertEqual(protocol._prompt_matches(assignment), case['recognized'])
                self.assertEqual(protocol.has_problem_result_rubric(assignment), case['rubric'])
                if case['role'] == 'adjudicator':
                    self.assertEqual(protocol._matches_current_problem_adjudicator_prompt(prompt), case['current_adjudicator'])


class OptionalParagraphTests(unittest.TestCase):
    def test_writer_placeholders_have_pinned_bytes_and_one_home(self):
        expected = {
            'exactory-problem-accounts-v1': 'c2e3eb3807d4ff247ad26a6abf716b02a58188968080195afeba16354c3a686d',
            'exactory-problem-slate-forming-v1': 'e14ff7ce5d6f1a0bd2d03b1f2fd7f45c596e14902eea5083dd7bc66d78f78d2b',
            'exactory-problem-replacement-v1': '92d8d0ace0a5c7dbd61d340f2fe6e6c5f93760ca1b7764853b7217f1dbbd12c0',
            'exactory-problem-areas-v1': 'e9a1aa5a8fa12579bbaff0cc1902f1f012c6bb7e872c02dff25131afab93da05'}
        actual = {version: hashlib.sha256(text.encode()).hexdigest()
                  for paragraph in paragraphs.PARAGRAPHS[1:] for version, text in paragraph.versions}
        self.assertEqual(actual, expected)

    def test_accounts_render_when_delivered_value_holds_accounts(self):
        from research_harness import review_prompt_paragraphs as paragraphs
        packet = dict(role='result', subject=dict(kind='problem'), problem_evidence=dict(value=dict(accounts=dict(answer='open'))))
        parameters = paragraphs.select_prompt_parameters('result', packet)
        prompt = protocol.canonical_prompt('result', orchestration='problem_creation', **parameters)
        self.assertIn('exactory-problem-accounts-v1', prompt['parameters']['problem_paragraphs'])
        self.assertTrue(protocol._prompt_matches(build_assignment('result', prompt), packet=packet))

    def test_every_admitted_combination_renders_once_in_order_and_recognizes(self):
        for role, accounts, replacement, forming, areas, carry in itertools.product(
                ('bar', 'slate', 'result'), (False, True), (False, True), (False, True), (False, True), (False, True)):
            packet = build_packet(role, accounts=accounts, replacement=replacement, forming=forming, areas=areas, carry=carry)
            prompt = render_packet(packet)
            expected = protocol.canonical_prompt(role, orchestration='problem_creation')
            self.assertEqual(prompt['text'].encode(), (expected['text'] + ''.join(select_texts(prompt))).encode())
            positions = [prompt['text'].index(text) for text in select_texts(prompt)]
            self.assertEqual(positions, sorted(positions))
            self.assertTrue(all(prompt['text'].count(text) == 1 for text in select_texts(prompt)))
            assignment = build_assignment(role, prompt)
            self.assertTrue(protocol._prompt_matches(assignment, packet=packet), prompt['template_id'])
            self.assertEqual(protocol.has_problem_result_rubric(assignment), role == 'result')

    def test_each_paragraph_alone_is_an_exact_registered_prompt_addition(self):
        for paragraph in paragraphs.PARAGRAPHS[1:]:
            role = 'adjudicator' if paragraph.name == 'replacement' else paragraph.roles[0]
            for carry in ((False, True) if role in ('result', 'slate') else (False,)):
                parameters = dict(problem_paragraphs=[paragraph.versions[0][0]])
                if carry:
                    parameters['problem_objection_carry'] = CARRY
                prompt = protocol.canonical_prompt(role, orchestration='problem_creation', **parameters)
                self.assertTrue(protocol._prompt_matches(build_assignment(role, prompt)))
                base = paragraphs.strip_prompt_paragraphs(prompt, role)
                self.assertEqual(base, protocol.canonical_prompt(role, orchestration='problem_creation'))

    def test_packet_conditions_reject_extra_and_missing_paragraphs(self):
        cases = [(build_packet(accounts=True), build_packet()),
                 (build_packet(replacement=True), build_packet(accounts=True)),
                 (build_packet('slate', forming=True), build_packet('slate')),
                 (build_packet(areas=True), build_packet()),
                 (build_packet(carry=True), build_packet())]
        for required, absent in cases:
            for first, second in ((required, absent), (absent, required)):
                assignment = build_assignment(first['role'], render_packet(first))
                self.assertFalse(protocol._prompt_matches(assignment, packet=second))

    def test_forming_conditions_accept_both_record_kinds_for_any_source_kind(self):
        for kind, expected in (('forming_call', True), ('form_outcome', True), ('literature_search', False)):
            packet = build_packet('slate')
            packet['candidates'] = [dict(source=dict(kind='own_record', evidence=[dict(kind='record', record_kind=kind, id='f1')]))]
            selected = paragraphs.select_prompt_parameters('slate', packet)
            self.assertEqual(bool(selected), expected)
        packet['candidates'] = []
        self.assertEqual(paragraphs.select_prompt_parameters('slate', packet), {})

    def test_slate_projection_retains_forming_references_and_ignores_superseded_candidates(self):
        row = dict(id='first', previous=None, statement='An exact claim.', source=dict(
            kind='own_record', reference='A recorded form.', evidence=[]), observation='A pattern.',
            neighbours=[], potential={})
        for record_kind in ('forming_call', 'form_outcome'):
            reference = dict(kind='record', record_kind=record_kind, id='forming-1')
            row['source']['evidence'] = [reference]
            records = dict(problem_candidate={'first': row})
            packet = build_packet('slate')
            protocol.add_problem_candidates(records, packet)
            self.assertEqual(packet['candidates'][0]['source']['evidence'], [reference])
            self.assertIn('exactory-problem-slate-forming-v1', render_packet(packet)['parameters']['problem_paragraphs'])
        later = dict(row, id='later', previous='first', source=dict(kind='literature', reference='A published question.', evidence=[]))
        records['problem_candidate']['later'] = later
        protocol.add_problem_candidates(records, packet)
        self.assertEqual(paragraphs.select_prompt_parameters('slate', packet), {})

    def test_0820_candidate_projection_and_record_digest_stay_unchanged(self):
        row = dict(id='ordinary', previous=None, statement='An existing question.', source=dict(
            kind='literature', reference='A published question.', evidence=[dict(kind='record', record_kind='literature_search', id='s')]),
            observation='An existing bound.', neighbours=[], potential={})
        before = copy.deepcopy(row)
        packet = build_packet('slate')
        protocol.add_problem_candidates(dict(problem_candidate={'ordinary': row}), packet)
        self.assertEqual(row, before)
        expected = dict(id=row['id'], digest=digest(before), statement=row['statement'],
            source={key: row['source'][key] for key in ('kind', 'reference')},
            observation=row['observation'], neighbours=[], potential={})
        self.assertEqual(packet['candidates'], [expected])

    def test_empty_accounts_and_replacement_call_for_no_paragraph(self):
        for accounts in (None, {}, []):
            packet = build_packet()
            packet['problem_evidence']['value']['accounts'] = accounts
            self.assertEqual(paragraphs.select_prompt_parameters('result', packet), {})
        packet['problem_evidence']['value'] = None
        self.assertEqual(paragraphs.select_prompt_parameters('result', packet), {})
        packet = build_packet(accounts=True)
        packet['problem_evidence']['value']['accounts']['replacement'] = {}
        self.assertEqual(paragraphs.select_prompt_parameters('result', packet)['problem_paragraphs'],
                         ['exactory-problem-accounts-v1'])

    def test_adjudicator_receives_only_the_paragraph_for_its_named_definition(self):
        for item in ('A1', 'A2', 'A3', 'A7', 'priority', 'calibration', 'significance', 'attempt'):
            packet = build_packet('adjudicator', accounts=True, replacement=True, item=item)
            prompt = render_packet(packet)
            versions = prompt['parameters'].get('problem_paragraphs', [])
            self.assertEqual('exactory-problem-accounts-v1' in versions,
                             item in ('A3', 'priority', 'calibration', 'significance'))
            self.assertEqual('exactory-problem-replacement-v1' in versions, item == 'A7')
            self.assertTrue(protocol._matches_current_problem_adjudicator_prompt(prompt))
            self.assertTrue(protocol._prompt_matches(build_assignment('adjudicator', prompt), packet=packet))
            paragraphs.append_rubric_definition(packet)
            self.assertEqual(packet['rubric_definition']['definition'], 'Original anchor.' + ''.join(select_texts(prompt)))
            for adapter in ('problem_adjudicator', 'context_repair'):
                final = render_packet(packet, review_parts=dict(adapter=adapter, kind='final', answer=True))
                self.assertTrue(protocol._matches_current_problem_adjudicator_prompt(final))
                self.assertFalse(protocol._matches_current_problem_adjudicator_prompt(
                    render_packet(packet, review_parts=dict(adapter=adapter, kind='read'))))

    def test_refuses_unknown_duplicate_reordered_and_wrong_role_versions(self):
        accounts, replacement = 'exactory-problem-accounts-v1', 'exactory-problem-replacement-v1'
        for role, versions in (('result', ['unknown-v1']), ('result', [accounts, accounts]),
                               ('result', [replacement, accounts]), ('bar', [accounts]), ('result', 'accounts')):
            with self.assertRaises(ResearchError) as raised:
                protocol.canonical_prompt(role, orchestration='problem_creation', problem_paragraphs=versions)
            self.assertEqual(raised.exception.code, 'invalid_review_context')
        with self.assertRaises(ResearchError):
            protocol.canonical_prompt('result', problem_paragraphs=[accounts])

    def test_rejects_altered_bytes_metadata_order_and_hashes(self):
        packet = build_packet(accounts=True, replacement=True, carry=True, areas=True)
        prompt = render_packet(packet)
        changes = []
        for key in ('text', 'template_id', 'template_hash', 'rendered_prompt_hash'):
            changed = copy.deepcopy(prompt)
            changed[key] += 'altered'
            changes.append(changed)
        changed = copy.deepcopy(prompt)
        changed['parameters']['problem_paragraphs'].reverse()
        changes.append(changed)
        changed = copy.deepcopy(prompt)
        texts = select_texts(prompt)
        changed['text'] = paragraphs.strip_prompt_paragraphs(prompt, 'result')['text'] + ''.join(reversed(texts))
        changed['template_hash'] = changed['rendered_prompt_hash'] = digest(changed['text'])
        changes.append(changed)
        for changed in changes:
            self.assertFalse(protocol._prompt_matches(build_assignment('result', changed), packet=packet))
        for value in ([], ['unknown-v1'], None):
            changed = copy.deepcopy(prompt)
            changed['parameters']['problem_paragraphs'] = value
            self.assertFalse(protocol._prompt_matches(build_assignment('result', changed)))
            self.assertFalse(protocol._prompt_matches(build_assignment('result', changed), packet=packet))

    def test_recorded_version_replays_after_a_later_version_is_registered(self):
        packet = build_packet(accounts=True, carry=True)
        old_prompt = render_packet(packet)
        old_bytes = json.dumps(old_prompt, sort_keys=True).encode()
        old = paragraphs.PARAGRAPHS[1]
        later = replace(old, versions=old.versions + (('exactory-problem-accounts-v2', '\n\nA later policy version.'),))
        registry = (paragraphs.PARAGRAPHS[0], later) + paragraphs.PARAGRAPHS[2:]
        with patch.object(paragraphs, 'PARAGRAPHS', registry):
            self.assertIn('exactory-problem-accounts-v2', render_packet(packet)['parameters']['problem_paragraphs'])
            replay = protocol.canonical_prompt('result', orchestration='problem_creation',
                **{key: old_prompt['parameters'][key] for key in ('problem_paragraphs', 'problem_objection_carry')})
            self.assertEqual(json.dumps(replay, sort_keys=True).encode(), old_bytes)
            self.assertTrue(protocol._prompt_matches(build_assignment('result', old_prompt), packet=packet))
            self.assertTrue(protocol.has_problem_result_rubric(build_assignment('result', old_prompt)))
            for versions in (['exactory-problem-accounts-v1', 'exactory-problem-accounts-v2'],
                             ['exactory-problem-accounts-v2', 'exactory-problem-accounts-v1']):
                with self.assertRaises(ResearchError):
                    protocol.canonical_prompt('result', orchestration='problem_creation', problem_paragraphs=versions)

    def test_carry_can_gain_a_version_without_replacing_its_recorded_v1_bytes(self):
        packet = build_packet(accounts=True, carry=True)
        recorded = render_packet(packet)
        carry = paragraphs.PARAGRAPHS[0]
        later = replace(carry, versions=carry.versions + (('exactory-problem-objection-carry-v2', '\n\nLater carry text.'),))
        with patch.object(paragraphs, 'PARAGRAPHS', (later,) + paragraphs.PARAGRAPHS[1:]):
            current = render_packet(packet)
            self.assertIn('exactory-problem-objection-carry-v2', current['parameters']['problem_paragraphs'])
            self.assertTrue(current['text'].endswith(later.versions[-1][1] + paragraphs.PARAGRAPHS[1].versions[0][1]))
            self.assertTrue(protocol._prompt_matches(build_assignment('result', current), packet=packet))
            self.assertTrue(protocol._prompt_matches(build_assignment('result', recorded), packet=packet))
            self.assertTrue(protocol.has_problem_result_rubric(build_assignment('result', current)))
            replay = protocol.canonical_prompt('result', orchestration='problem_creation',
                **paragraphs.read_paragraph_parameters(recorded))
            self.assertEqual(replay, recorded)

    def test_new_adjudication_refreshes_a_definition_after_a_paragraph_version_changes(self):
        packet = build_packet('adjudicator', accounts=True, item='A3')
        paragraphs.append_rubric_definition(packet)
        original = copy.deepcopy(packet)
        old = paragraphs.PARAGRAPHS[1]
        later = replace(old, versions=old.versions + (('exactory-problem-accounts-v2', '\n\nA later policy version.'),))
        with patch.object(paragraphs, 'PARAGRAPHS', (paragraphs.PARAGRAPHS[0], later) + paragraphs.PARAGRAPHS[2:]):
            paragraphs.append_rubric_definition(packet)
            self.assertEqual(packet['rubric_definition']['definition'], 'Original anchor.' + later.versions[-1][1])
            self.assertEqual(original['rubric_definition']['definition'], 'Original anchor.' + old.versions[0][1])

    def test_repair_legacy_parts_current_parts_and_staged_keep_the_same_paragraph_bytes(self):
        packet = build_packet(accounts=True, replacement=True, areas=True, carry=True)
        expected = select_texts(render_packet(packet))
        options = [dict(repair=True), dict(repair=True, excerpts=True)]
        options += [dict(excerpts=True, evidence_parts=stage, part_suffix=suffix)
                    for stage, suffix in (('part', None), ('part', review_problem_parts.PART_PROMPT_0691), ('final', None))]
        options += [dict(review_parts=dict(adapter=adapter, kind=kind, answer=kind == 'final'))
                    for adapter in ('problem_result', 'context_repair') for kind in review_parts.KINDS]
        options += [dict(staged_task=dict(type=kind), repair=repairing)
                    for kind in ('frame', 'read', 'combine', 'original_check', 'integrate', 'answer', 'final')
                    for repairing in (False, True)]
        for option in options:
            prompt = render_packet(packet, **option)
            self.assertEqual(select_texts(prompt), expected)
            self.assertTrue(protocol._prompt_matches(build_assignment('result', prompt), packet=packet))
            if 'staged_task' in option:
                self.assertTrue(staged.matches_prompt(build_assignment('result', prompt), 3))

    def test_compact_context_preserves_applicability_without_repeating_accounts(self):
        packet = build_packet(accounts=True, replacement=True, areas=True)
        compact = build_packet()
        compact['problem_evidence'] = dict(delivery='delivered-in-an-earlier-turn')
        paragraphs.preserve_prompt_conditions('result', packet, compact)
        self.assertEqual(compact['problem_prompt_conditions'], ['accounts', 'replacement', 'areas'])
        self.assertEqual(paragraphs.select_prompt_parameters('result', compact),
                         paragraphs.select_prompt_parameters('result', packet))
        self.assertNotEqual(compact['problem_evidence'], packet['problem_evidence'])
        self.assertTrue(protocol._prompt_matches(build_assignment('result', render_packet(packet)), packet=compact))

    def test_new_value_overrides_the_conditions_of_an_earlier_compact_turn(self):
        earlier = build_packet(accounts=True, replacement=True)
        for current in (build_packet(), build_packet(accounts=True), build_packet(replacement=True)):
            packet = copy.deepcopy(current)
            paragraphs.preserve_prompt_conditions('result', earlier, packet)
            self.assertEqual(paragraphs.select_prompt_parameters('result', packet),
                             paragraphs.select_prompt_parameters('result', current))
        packet = build_packet(replacement=True)
        paragraphs.preserve_prompt_conditions('result', build_packet(accounts=True), packet)
        self.assertIn('exactory-problem-replacement-v1',
                      paragraphs.select_prompt_parameters('result', packet)['problem_paragraphs'])

    def test_staged_bootstrap_profiles_need_no_epoch(self):
        route = dict(configuration=dict(timeout_seconds=5, max_output_tokens=1000))
        profiles = staged.build_profiles({}, None, route)
        for kind, profile in profiles.items():
            prompt = protocol.canonical_prompt('result', orchestration='problem_creation', staged_task=dict(
                type=kind, scope='joint_original', claim_ids=[], occurrence_ids=[], group_ids=[]))
            self.assertEqual(profile['context_characters'], len(prompt['text']))

    def test_staged_profiles_include_the_applicable_paragraph_bytes(self):
        packet = build_packet(accounts=True, replacement=True, carry=True)
        route = dict(configuration=dict(timeout_seconds=5, max_output_tokens=1000))
        profiles = staged.build_profiles({}, None, route, dict(role='result', manifest=dict(frame=packet)))
        for kind, profile in profiles.items():
            prompt = render_packet(packet, staged_task=dict(type=kind, scope='joint_original',
                claim_ids=[], occurrence_ids=[], group_ids=[]))
            self.assertEqual(profile['context_characters'], len(prompt['text']))


class ParagraphPathTests(ResultPartsCase):
    def test_assignment_selects_accounts_and_replacement_from_its_delivered_value(self):
        self.case.expected['value']['accounts'] = dict(answer='A supported answer.', replacement=dict(statement='Earlier form.'))
        assignment = self.assign('ordinary-with-accounts')
        packet = self.packet(assignment)
        self.assertEqual(assignment['prompt'], render_packet(packet))
        self.assertTrue(protocol._prompt_matches(assignment, packet=packet))

    def test_parts_final_keeps_the_same_paragraphs_as_single_input(self):
        self.case.expected['value']['accounts'] = dict(answer='A supported answer.', replacement=dict(statement='Earlier form.'))
        self.oversized(count=6)
        assignment = self.assign('parts-with-accounts')
        expected = select_texts(render_packet(self.packet(assignment)))
        self.assertEqual(len(expected), 2)
        turns = 0
        while self.packet(assignment).get('review_parts', {}).get('kind',
                self.packet(assignment).get('problem_evidence_parts', {}).get('stage')) != 'final':
            self.assertEqual(select_texts(assignment['prompt']), expected)
            self.assertTrue(protocol._prompt_matches(assignment, packet=self.packet(assignment)))
            self.invoke(assignment)
            turns += 1
            self.assertLess(turns, 20)
            assignment = self.assign('turn-' + str(turns), assignment['id'])
        self.assertEqual(select_texts(assignment['prompt']), expected)
        self.assertTrue(protocol._prompt_matches(assignment, packet=self.packet(assignment)))

    def test_stage_b_bar_has_no_areas_paragraph(self):
        packet = protocol.build_packet(self.case.read_records(), self.case.artifacts, None, 'bar', 'third')
        self.assertNotIn('problem_area', self.case.read_records())
        self.assertEqual(paragraphs.select_prompt_parameters('bar', packet), {})

    def test_adjudication_assignment_delivers_the_same_registered_text_with_its_anchor(self):
        from codex_route_fixtures import invoke_codex
        from research_harness import research_decisions as decisions
        from research_harness.problem_rubric import rubric_objection_id
        self.case.expected['value']['accounts'] = dict(answer='A supported answer.', replacement=dict(statement='Earlier form.'))
        for reviewer in ('first', 'second'):
            assignment = self.assign('result-' + reviewer, reviewer=reviewer)
            output = self.case.build_review_output('result')
            if reviewer == 'first':
                output['value']['rubric']['admission'].update(A3='fail', A7='fail')
                output['value']['rubric'].update(priority=1, calibration='fail', significance=dict(B1=1, B2=1))
            invoke_codex(self.case, assignment['id'], output)
            self.case.mutate(decisions.record_value_review, dict(output, id='review-' + assignment['id'],
                dossier_id=None, assignment_id=assignment['id']))
        for item in ('A3', 'A7', 'priority', 'calibration', 'significance'):
            assignment = self.case.assign('adjudicator', 'third', identifier='adjudicate-' + item,
                review_ids=['review-result-first', 'review-result-second'],
                objection_id=rubric_objection_id('review-result-first', item))
            packet = self.packet(assignment)
            self.assertTrue(protocol._prompt_matches(assignment, packet=packet))
            self.assertTrue(protocol._matches_current_problem_adjudicator_prompt(assignment['prompt']))
            texts = select_texts(assignment['prompt'])
            self.assertEqual(len(texts), 1)
            self.assertTrue(packet['rubric_definition']['definition'].endswith(texts[0]))


class CurrentParagraphPathTests(ParagraphPathTests):
    use_parts_v2 = True

    def output(self, assignment):
        from review_parts_fixtures import make_output
        return make_output(self.packet(assignment))

    def test_context_repair_uses_the_same_paragraphs(self):
        from test_review_parts_repair_journey import ResultRepairJourneyTests
        self.case.expected['value']['accounts'] = dict(answer='A supported answer.', replacement=dict(statement='Earlier form.'))
        first = self.assign('original')
        self.record_final(first, self.case.build_review_output('result'))
        binding = ResultRepairJourneyTests.contaminate(self, first)
        repaired = ResultRepairJourneyTests.repair_assignment(self, first, binding)
        packet = self.packet(repaired)
        self.assertTrue(protocol._prompt_matches(repaired, packet=packet))
        self.assertEqual(select_texts(repaired['prompt']), select_texts(first['prompt']))

    def test_replacement_preflight_keeps_the_recorded_paragraph_versions(self):
        from research_harness import review_failure_class as failures
        self.case.expected['value']['accounts'] = dict(answer='A supported answer.', replacement=dict(statement='Earlier form.'))
        self.oversized(count=12, characters=10000, limit=110000)
        assignment = self.assign('oversized')
        self.assertEqual(self.packet(assignment)['review_parts']['kind'], 'read')
        expected = select_texts(assignment['prompt'])
        original = protocol.transport.request_body
        requests = []
        def build_request(route, prompt, packet, *args, **kwargs):
            request = original(route, prompt, packet, *args, **kwargs)
            requests.append((prompt, packet))
            return request
        with patch.object(protocol.transport, 'request_body', side_effect=build_request):
            failures.plan_output_replacement(self.case.read_records(), self.case.artifacts, assignment)
        read_prompts = [text for text, packet in requests if packet.get('review_parts', {}).get('kind') == 'read']
        self.assertTrue(read_prompts)
        for prompt in read_prompts:
            self.assertTrue(all(text in prompt for text in expected))


class StagedParagraphPathTests(ResultPartsCase):
    staged_entry = True

    def test_staged_controller_and_child_keep_accounts_without_repeating_value(self):
        self.case.expected['value']['accounts'] = dict(answer='A supported answer.', replacement=dict(statement='Earlier form.'))
        self.oversized()
        controller = self.assign_staged('controller-with-accounts')
        records = self.case.read_records()
        epoch = records['review_epoch'][controller['staged_review']['epoch_id']]
        expected = select_texts(render_packet(epoch['manifest']['frame']))
        self.assertEqual(len(expected), 2)
        self.assertEqual(select_texts(controller['prompt']), expected)
        task_id = controller['staged_review']['task_ids'][0]
        child = self.case.assign('result', 'first', identifier='child-with-accounts',
            controller_assignment_id=controller['id'], task_id=task_id)
        packet = self.packet(child)
        self.assertEqual(select_texts(child['prompt']), expected)
        self.assertNotIn('problem_evidence', packet)
        self.assertTrue(protocol._prompt_matches(child, packet=packet))
        self.assertTrue(staged.matches_prompt(child, 3))
        self.assertEqual(digest(protocol.build_assignment_request(self.case.read_records(), self.case.artifacts, child)),
                         child['planned_request_digest'])


if __name__ == '__main__':
    unittest.main()
