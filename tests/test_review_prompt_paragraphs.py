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
from research_harness.bar_sources import PROTOCOL_V3
from research_harness import review_context_repair as repair
from research_harness import review_excerpts, review_problem_parts, review_parts
from research_harness.evidence import digest
from research_harness.errors import ResearchError
from research_harness import review_prompt_paragraphs as paragraphs
from research_harness import review_staged_protocol as staged
from problem_parts_fixtures import ResultPartsCase


def build_packet(role='result', *, accounts=False, replacement=False, forming=False, areas=False, carry=False, item=None,
                 changes=False, places=False):
    value = dict(accounts={})
    if accounts:
        value['accounts']['answer'] = 'A supported conjectured answer.'
    if replacement:
        value['accounts']['replacement'] = dict(statement='A refuted earlier statement.')
    if changes:
        value['changes_if_resolved'] = [dict(text='A proof decides the item that the source states as open.')]
    packet = dict(role=role, subject=dict(kind='problem'), problem_evidence=dict(value=value))
    if forming:
        packet['candidates'] = [dict(id='formed', source=dict(kind='phenomenon'))]
    if places:
        packet[paragraphs.PLACE_REVIEW_KEY] = dict(protocol=paragraphs.SLATE_PLACES_PROTOCOL)
    if areas:
        packet['problem_areas'] = [dict(id='further-area')]
    if carry:
        packet['problem_objection_carry'] = CARRY
    if item is not None:
        packet['rubric_definition'] = dict(item=item, definition='Original anchor.', threshold='pass')
    return packet


def render_packet(packet, **options):
    return protocol.canonical_prompt(packet['role'], orchestration='problem_creation',
        source_rules=packet.get('source_rules'),
        **paragraphs.select_prompt_parameters(packet['role'], packet), **options)


def select_texts(prompt, *, include_capacity=True):
    parameters = prompt['parameters']
    versions = parameters.get('problem_paragraphs', [])
    if 'problem_objection_carry' in parameters:
        versions = ['exactory-problem-objection-carry-v1'] + versions
    return [text.format(**parameters['capacity']) if paragraph.name == 'capacity-notice' else text
            for paragraph in paragraphs.PARAGRAPHS if include_capacity or paragraph.name != 'capacity-notice'
            for version, text in paragraph.versions if version in versions]


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
    def test_unread_closing_uses_the_finding_index_and_accepts_the_stored_version(self):
        from research_harness.openness_reading import PROTOCOL_V7
        old = paragraphs.UNREAD_FINAL_PARAGRAPH_VERSION
        new = 'exactory-openness-unread-closing-v1'
        for stage, indexed in itertools.product(('final', 'closing'), (False, True)):
            with self.subTest(stage=stage, indexed=indexed):
                binding = dict(unread={})
                if stage == 'closing':
                    binding['closing'] = {}
                packet = dict(reading_final=binding)
                if indexed:
                    packet['finding_index'] = {}
                options = dict(reading_stage=stage, reading_protocol=PROTOCOL_V7, finding_version=2 if indexed else 1)
                selected = paragraphs.select_prompt_parameters('native_math', packet)
                expected = new if stage == 'closing' and indexed else old
                self.assertEqual(selected[paragraphs.PARAMETER], [expected])
                stored = protocol.canonical_prompt('native_math', problem_paragraphs=[old], **options)
                self.assertTrue(paragraphs.matches_packet_paragraphs(stored, 'native_math', packet))
                if expected == new:
                    rendered = protocol.canonical_prompt('native_math', **options, **selected)
                    base = protocol.canonical_prompt('native_math', **options)
                    self.assertEqual(rendered['template_id'], base['template_id'])
                    self.assertLess(len(rendered['text']) - len(base['text']), 800)
                    self.assertEqual(paragraphs.strip_prompt_paragraphs(rendered, 'native_math'), base)

    def test_replacement_selects_unread_text_from_the_new_turn(self):
        old = paragraphs.UNREAD_FINAL_PARAGRAPH_VERSION
        new = paragraphs.UNREAD_CLOSING_PARAGRAPH_VERSION
        whole = paragraphs.REFUSAL_PARAGRAPH_VERSIONS['whole']
        closing = dict(finding_index={}, reading_final=dict(closing={}, unread={}), output_refusal=dict(form='whole'))
        prior = dict(parameters={paragraphs.PARAMETER: [old, whole]})
        self.assertEqual(paragraphs.select_prompt_parameters('native_math', closing, prior_prompt=prior),
                         {paragraphs.PARAMETER: [new, whole]})

        prior = dict(parameters={paragraphs.PARAMETER: [new, whole]})
        final = dict(finding_index={}, reading_final=dict(unread={}), output_refusal=closing['output_refusal'])
        self.assertEqual(paragraphs.select_prompt_parameters('native_math', final, prior_prompt=prior),
                         {paragraphs.PARAMETER: [old, whole]})
        combine = dict(finding_index={}, reading_combine={}, output_refusal=closing['output_refusal'])
        self.assertEqual(paragraphs.select_prompt_parameters('native_math', combine, prior_prompt=prior),
                         {paragraphs.PARAMETER: [whole]})

    def test_finding_units_version_follows_the_frame_members_of_the_chain(self):
        from research_harness.openness_reading import PROTOCOL_V7
        old = 'exactory-openness-finding-units-v1'
        new = 'exactory-openness-finding-units-v2'
        notes = paragraphs.NOTES_COST_PARAGRAPH_VERSION
        options = dict(reading_stage='reading', reading_protocol=PROTOCOL_V7)
        base = protocol.canonical_prompt('native_math', **options)
        # A reading packet of a chain that release 0.92 recorded holds no notes_cost and keeps version 1.
        stored = dict(reading_turn=dict(finding_units=2))
        self.assertEqual(paragraphs.select_prompt_parameters('native_math', stored), {paragraphs.PARAMETER: [old]})
        prompt = protocol.canonical_prompt('native_math', problem_paragraphs=[old], **options)
        self.assertEqual(prompt['text'], base['text'] + paragraphs.FINDING_UNITS_PROMPT)
        self.assertEqual(prompt['template_id'], base['template_id'])
        # The frame members of 0.93.0 give a reading packet notes_cost, and that chain reads version 2.
        current = dict(reading_turn=dict(finding_units=2, notes_cost={}))
        prior = dict(parameters={paragraphs.PARAMETER: [old, notes]})
        for earlier in (None, prior):
            with self.subTest(prior=earlier is not None):
                self.assertEqual(paragraphs.select_prompt_parameters('native_math', current, prior_prompt=earlier),
                                 {paragraphs.PARAMETER: [new, notes]})
        rendered = protocol.canonical_prompt('native_math', problem_paragraphs=[new, notes], **options)
        self.assertEqual(rendered['text'],
                         base['text'] + paragraphs.FINDING_UNITS_PROMPT_V2 + paragraphs.NOTES_COST_PROMPT)
        self.assertEqual(rendered['template_id'], base['template_id'])
        self.assertEqual(paragraphs.strip_prompt_paragraphs(rendered, 'native_math'), base)

    def test_replacement_prefers_the_prior_version_of_an_applicable_family(self):
        old = 'exactory-problem-slate-forming-v1'
        new = 'exactory-problem-slate-forming-v2'
        prior = dict(parameters={paragraphs.PARAMETER: [old]})
        packet = build_packet('slate', forming=True, places=True)
        packet['source_rules'] = PROTOCOL_V3
        places = 'exactory-problem-slate-places-v1'
        self.assertEqual(paragraphs.select_prompt_parameters('slate', packet),
                         {paragraphs.PARAMETER: [new, places]})
        self.assertEqual(paragraphs.select_prompt_parameters('slate', packet, prior_prompt=prior),
                         {paragraphs.PARAMETER: [old, places]})

    def test_writer_placeholders_have_pinned_bytes_and_one_home(self):
        expected = {
            'exactory-problem-accounts-v1': 'c2e3eb3807d4ff247ad26a6abf716b02a58188968080195afeba16354c3a686d',
            'exactory-problem-account-probes-v1': '81d4dbbf210e62b453a5535425be6553924f69e41fe1929fd1b483eed8373b5f',
            'exactory-problem-changes-if-resolved-v1': '4d688cdffbe965d3533aeecdc89dd6e04f4d28b463ecd49e9e678fc0ccad104a',
            'exactory-problem-slate-places-v1': '234fce931e8378f342c0d6af6de942aa7f560cef00b645ab3efc00accb2e4d70',
            'exactory-problem-slate-delivery-v1': '6cc18ef126fd0ff2155ad1cdbaec42ebe4def3ed36dffb2698e481a138ec0160',
            'exactory-problem-slate-forming-v2': '13c788192070b3fa9d16cf8abf3ab36a81d381f248a7a7333c3f69648aa9849d',
            'exactory-problem-slate-forming-v1': 'e14ff7ce5d6f1a0bd2d03b1f2fd7f45c596e14902eea5083dd7bc66d78f78d2b',
            'exactory-problem-slate-forming-v3': 'e9b8ec9455d5f4fceafd2b0f85fa1395e1871db0f7a11c80adf8f7c178c133fc',
            'exactory-problem-slate-places-v2': '67aaf39a9316ac33b07322d13f7fbcd6f4ae0e5fdd67495ee16b355223835353',
            'exactory-problem-areas-v2': '3eaa9c797360181e76dea95d64770d1c2fda88c98bec2d0692ab9c7970177d0a',
            'exactory-problem-refresh-chain-v1': 'b21d129056cdcfb9f9d3dbea3cab28c63185c88260b9cd209c89bf73bd1280d8',
            'exactory-problem-replacement-v1': '92d8d0ace0a5c7dbd61d340f2fe6e6c5f93760ca1b7764853b7217f1dbbd12c0',
            'exactory-problem-areas-v1': 'e9a1aa5a8fa12579bbaff0cc1902f1f012c6bb7e872c02dff25131afab93da05',
            'exactory-review-delivery-notice-v1': '56dea5b68d33ecb400797772300180198ecafafa1bd1b2b77fb1ba4053284825',
            'exactory-route-capacity-notice-v1': '236975ce56b6cd564d0cb30faa61eaedff10fb2a4570c2b8b460538e4850bf98',
            'exactory-review-output-refusal-whole-v1': 'a88f7b5fbe7e14971371e9e7b6956202ccb49a2a207e74d8946502884e8d0b75',
            'exactory-openness-unread-final-v1': 'e6c89ffba735d8f0b45f6cd274dde987ae0c96fa7a1073a431d08b20aaebf58d',
            'exactory-openness-unread-closing-v1': '9f1cd4a800bc862c3efb76d00c0a25403bc0910757d345c4494d33a40ffcbef5',
            'exactory-openness-finding-units-v1': '86c4abd60011b201e223c82b9f2af6e7c00682311f31c2e7880ea4f6afe56e48',
            'exactory-openness-finding-units-v2': '58802564948837d98b4f5a97311c150e198feebdae605fd8335b7cd56316a4af',
            'exactory-openness-notes-cost-v1': '18e527365a4e9e096195d7f25e69ca0bd0977c397ac0997c99fc5ed08c4fe1f3',
            'exactory-openness-list-rows-v1': 'eb8bcad695a1bb273841e83d6b3594be9bf0b43336f3095fb93b2aa367b729e2',
            'exactory-review-output-refusal-bounded-v1': '61d96ac0b67b91f1890d4c5c8216fc7e995b85859e1afeaf744f2dedafa4db65'}
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
        for role, accounts, replacement, forming, areas, carry, changes, places in itertools.product(
                ('bar', 'slate', 'result'), (False, True), (False, True), (False, True), (False, True), (False, True),
                (False, True), (False, True)):
            packet = build_packet(role, accounts=accounts, replacement=replacement, forming=forming, areas=areas, carry=carry,
                                  changes=changes, places=places)
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
                if paragraph.name == 'capacity-notice':
                    parameters['capacity'] = dict(protocol=paragraphs.CAPACITY_NOTICE_PROTOCOL,
                        kind='request', output_tokens=128000, answer_bound=15000)
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
                 (build_packet(changes=True), build_packet()),
                 (build_packet('slate', places=True), build_packet('slate')),
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
        self.assertEqual(paragraphs.select_prompt_parameters('slate', packet),
                         dict(problem_paragraphs=['exactory-problem-slate-places-v1']))

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

    def test_the_changes_paragraph_follows_a_nonempty_section_of_the_delivered_value(self):
        for section, expected in ((None, False), ([], False), ([dict(text='A paragraph.')], True)):
            packet = build_packet(accounts=True)
            if section is not None:
                packet['problem_evidence']['value']['changes_if_resolved'] = section
            versions = paragraphs.select_prompt_parameters('result', packet)['problem_paragraphs']
            self.assertEqual(versions, ['exactory-problem-accounts-v1'] + (
                ['exactory-problem-changes-if-resolved-v1'] if expected else []))
        prompt = render_packet(build_packet(accounts=True, changes=True))
        self.assertTrue(prompt['template_id'].endswith('/accounts-v1/changes-if-resolved-v1'))
        self.assertTrue(prompt['text'].endswith(paragraphs.CHANGES_IF_RESOLVED_PROMPT))
        self.assertEqual(paragraphs.strip_prompt_paragraphs(prompt, 'result'),
                         protocol.canonical_prompt('result', orchestration='problem_creation'))
        self.assertEqual(render_packet(build_packet(accounts=True)), protocol.canonical_prompt(
            'result', orchestration='problem_creation', problem_paragraphs=['exactory-problem-accounts-v1']))

    def test_the_slate_places_paragraph_follows_the_member_that_the_slate_builder_writes(self):
        row = dict(id='first', previous=None, statement='An exact claim.', source=dict(
            kind='literature', reference='A published question.', evidence=[]), observation='A stated question.',
            neighbours=[], potential={})
        packet = build_packet('slate')
        protocol.add_problem_candidates(dict(problem_candidate={'first': row}), packet)
        self.assertEqual(packet['place_review'], {'protocol': 'exactory-problem-slate-places-v1'})
        prompt = render_packet(packet)
        self.assertEqual(prompt['parameters']['problem_paragraphs'], ['exactory-problem-slate-places-v1'])
        self.assertTrue(prompt['template_id'].endswith('/slate-places-v1'))
        self.assertTrue(prompt['text'].endswith(paragraphs.SLATE_PLACES_PROMPT))
        recorded = {key: value for key, value in packet.items() if key != 'place_review'}
        self.assertEqual(render_packet(recorded), protocol.canonical_prompt('slate', orchestration='problem_creation'))
        self.assertTrue(protocol._prompt_matches(build_assignment('slate', render_packet(recorded)), packet=recorded))
        self.assertFalse(protocol._prompt_matches(build_assignment('slate', render_packet(recorded)), packet=packet))
        for member in (dict(protocol='exactory-problem-slate-places-v0'), {}, dict(other='member')):
            self.assertEqual(paragraphs.select_prompt_parameters('slate', dict(recorded, place_review=member)), {})
        formed = build_packet('slate', forming=True, places=True)
        self.assertEqual(render_packet(formed)['parameters']['problem_paragraphs'],
                         ['exactory-problem-slate-forming-v1', 'exactory-problem-slate-places-v1'])

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
            packet = build_packet('adjudicator', accounts=True, replacement=True, changes=True, item=item)
            prompt = render_packet(packet)
            versions = prompt['parameters'].get('problem_paragraphs', [])
            self.assertEqual('exactory-problem-accounts-v1' in versions,
                             item in ('A3', 'priority', 'calibration', 'significance'))
            self.assertEqual('exactory-problem-replacement-v1' in versions, item == 'A7')
            self.assertEqual('exactory-problem-changes-if-resolved-v1' in versions, item in ('calibration', 'significance'))
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
                self.assertTrue(staged.matches_prompt(build_assignment('result', prompt), protocol.find_common_prompt_version(prompt)))

    def test_compact_context_preserves_applicability_without_repeating_accounts(self):
        packet = build_packet(accounts=True, replacement=True, areas=True, changes=True)
        compact = build_packet()
        compact['problem_evidence'] = dict(delivery='delivered-in-an-earlier-turn')
        paragraphs.preserve_prompt_conditions('result', packet, compact)
        self.assertEqual(compact['problem_prompt_conditions'], ['accounts', 'changes-if-resolved', 'replacement', 'areas'])
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


class TurnFormVersionTests(unittest.TestCase):
    """Rows S22 and P11 of the second writer pass: a paragraph version can serve one form of turn, and a turn selects
    the last version that serves its form. An installed form selects what it selected before."""

    def select(self, packet, **options):
        return paragraphs.select_prompt_parameters(packet['role'], packet, **options).get(paragraphs.PARAMETER, [])

    def test_a_session_of_the_rules_v4_selects_the_new_slate_forming_and_areas_versions(self):
        from research_harness import review_session_v4 as session
        slate = dict(build_packet('slate', forming=True), source_rules=PROTOCOL_V3)
        result = build_packet('result', areas=True)
        self.assertEqual(self.select(slate), ['exactory-problem-slate-forming-v2'])
        self.assertEqual(self.select(dict(slate, source_rules=None)), ['exactory-problem-slate-forming-v1'])
        self.assertEqual(self.select(slate, session_rules=session.PROTOCOL), ['exactory-problem-slate-forming-v3'])
        self.assertEqual(self.select(result), ['exactory-problem-areas-v1'])
        self.assertEqual(self.select(result, session_rules=session.PROTOCOL), ['exactory-problem-areas-v2'])

    def test_a_refresh_outside_a_session_of_the_rules_v4_selects_the_refresh_chain_paragraph(self):
        from research_harness import review_session_v4 as session
        refresh = dict(build_packet('bar'), problem_refresh=dict(assignment_id='earlier'))
        self.assertEqual(self.select(refresh), ['exactory-problem-refresh-chain-v1'])
        prompt = render_packet(refresh)
        self.assertTrue(prompt['text'].endswith(paragraphs.REFRESH_CHAIN_PROMPT))
        self.assertTrue(prompt['template_id'].endswith('/refresh-chain-v1'))
        self.assertEqual(self.select(dict(refresh, continuation={})), [])
        self.assertEqual(self.select(refresh, session_rules=session.PROTOCOL), [])

    def test_a_series_of_the_parts_protocol_v3_selects_slate_places_by_the_kind_of_its_turn(self):
        from research_harness.review_parts_v3 import PROTOCOL as V3
        slate = build_packet('slate', places=True)
        self.assertEqual(self.select(slate), [paragraphs.SLATE_PLACES_PROTOCOL])
        for kind, form, expected in (('final', None, ['exactory-problem-slate-places-v2']),
                                     ('final', 'page', ['exactory-problem-slate-places-v2']),
                                     ('final', 'closing', ['exactory-problem-slate-places-v2']),
                                     ('final', 'combine', []), ('read', None, []), ('combine', None, []),
                                     ('check', None, [])):
            header = dict(protocol=V3, kind=kind, **(dict(final_form=form) if form else {}))
            with self.subTest(kind=kind, form=form):
                self.assertEqual(self.select(dict(slate, review_parts=header)), expected)


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
        # The scientific paragraphs persist; capacity belongs to the current turn.
        expected = select_texts(render_packet(self.packet(assignment)), include_capacity=False)
        self.assertEqual(len(expected), 2)
        turns = 0
        while self.packet(assignment).get('review_parts', {}).get('kind',
                self.packet(assignment).get('problem_evidence_parts', {}).get('stage')) != 'final':
            self.assertEqual(select_texts(assignment['prompt'], include_capacity=False), expected)
            self.assertTrue(protocol._prompt_matches(assignment, packet=self.packet(assignment)))
            self.invoke(assignment)
            turns += 1
            self.assertLess(turns, 20)
            assignment = self.assign('turn-' + str(turns), assignment['id'])
        self.assertEqual(select_texts(assignment['prompt'], include_capacity=False), expected)
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

    def setUp(self):
        super().setUp()
        from research_harness import review_route_capacity
        from test_review_output_profile import build_row
        # Ordinary paragraph paths fit their start; oversized cases still force input parts.
        profile = patch.object(review_route_capacity, 'read_profile_priors', return_value=[
            dict(build_row(level=None, kind='single', thinking=1), largest_output_tokens=12)])
        profile.start()
        self.addCleanup(profile.stop)

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
        self.assertEqual(select_texts(repaired['prompt'], include_capacity=False),
                         select_texts(first['prompt'], include_capacity=False))

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
        self.assertTrue(staged.matches_prompt(child, protocol.find_common_prompt_version(child['prompt'])))
        self.assertEqual(digest(protocol.build_assignment_request(self.case.read_records(), self.case.artifacts, child)),
                         child['planned_request_digest'])

    def test_stored_controller_keeps_its_common_version_when_a_child_is_assigned(self):
        self.assertEqual(protocol.COMMON_VERSION, 4)
        self.oversized()
        with patch.object(protocol, 'COMMON_VERSION', 3):
            controller = self.assign_staged('stored-controller')
        self.assertEqual(controller['prompt']['parameters']['common_version'], 3)
        task_id = controller['staged_review']['task_ids'][0]
        child = self.case.assign('result', 'first', identifier='stored-child',
            controller_assignment_id=controller['id'], task_id=task_id)
        self.assertEqual(child['prompt']['parameters']['common_version'], 3)
        self.assertTrue(staged.matches_prompt(child, 3))


if __name__ == '__main__':
    unittest.main()
