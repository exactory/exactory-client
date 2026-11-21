"""Named literature gaps are checked against the exact delivered evidence."""

import copy
import hashlib
import json
import unittest
from pathlib import Path

from openness_named_gap_fixtures import (FIXED_PACKET_NAMES, REQUEST_FORMS, build_fixed_answer,
    build_ground, build_packet, fixture_case, load_fixed_packet)
from research_harness import native_review, review_protocol, review_search_delivery
from research_harness.errors import ResearchError
from research_harness.problem_openness import serialize_record


class FindingVersionTests(unittest.TestCase):
    def test_version_one_keeps_every_frozen_prompt_byte_and_digest(self):
        frozen = json.loads((Path(__file__).parent / 'fixtures' / 'openness-named-gaps-v1-prompts.json').read_text())
        self.assertEqual(native_review.render_finding_prompt(), frozen['finding_prompt'])
        for name, row in frozen['prompts'].items():
            with self.subTest(form=name):
                prompt = review_protocol.canonical_prompt('native_math', **row['options'])
                self.assertEqual(prompt, row['prompt'])
                self.assertNotIn('finding_version', prompt['parameters'])
                self.assertTrue(native_review.uses_finding_contract(dict(prompt=prompt)))

    def test_version_two_has_same_template_and_explicit_finding_parameter(self):
        options = [dict(version=7), dict(reading_stage='final', reading_protocol='exactory-openness-reading-v6'),
                   dict(reading_stage='final', reading_protocol='exactory-openness-reading-v7'),
                   dict(reading_stage='page', reading_protocol='exactory-openness-reading-v7'),
                   dict(reading_stage='closing', reading_protocol='exactory-openness-reading-v7')]
        for option in options:
            with self.subTest(options=option):
                old = review_protocol.canonical_prompt('native_math', **option)
                new = review_protocol.canonical_prompt('native_math', finding_version=2, **option)
                self.assertEqual(new['template_id'], old['template_id'])
                self.assertEqual(new['parameters']['finding_version'], 2)
                self.assertIn(native_review.FINDING_V2_TEXT, new['text'])
                self.assertTrue(native_review.uses_finding_contract(dict(prompt=new)))
                self.assertEqual(native_review.find_finding_version(dict(prompt=new)), 2)
                self.assertEqual(native_review.find_finding_version(dict(prompt=old)), 1)

    def test_version_two_finding_rules_keep_the_pinned_placeholder_text(self):
        pinned = (Path(__file__).parent / 'fixtures' / 'openness-named-gaps-v2-prompt.txt').read_text(encoding='utf-8')
        self.assertEqual(native_review.FINDING_V2_TEXT, pinned)
        self.assertIn('packet_defect ground', pinned)
        self.assertIn('a reviewer_gap still needs every named-gap field and check', pinned)

    def test_version_one_still_accepts_the_original_gap_fields(self):
        packet = build_packet()
        packet.pop('finding_index')
        answer = build_fixed_answer(packet, ground=dict(kind='reviewer_gap', text='An earlier unspecific gap.'))
        native_review.validate_finding(packet, answer)


class NamedGapValidatorTests(unittest.TestCase):
    def assert_rule(self, packet, ground, rule):
        with self.assertRaises(ResearchError) as caught:
            native_review.validate_finding(packet, build_fixed_answer(packet, ground=ground), finding_version=2)
        self.assertEqual(caught.exception.code, 'openness_finding_invalid')
        self.assertEqual(caught.exception.details['rule'], rule)

    def test_bound_content_tokens_ignore_order_case_and_punctuation_in_every_request_form(self):
        for form in REQUEST_FORMS:
            packet = build_packet('variant-search', form)
            for term in ('graph spectral radius', 'Graph Spectral-Radius'):
                with self.subTest(form=form, term=term):
                    self.assert_rule(packet, build_ground(packet, term=term), 'gap_term_bound')

    def test_term_not_in_the_basis_fails_before_the_bound_query_check_in_every_request_form(self):
        for form in REQUEST_FORMS:
            packet = build_packet('variant-search', form)
            with self.subTest(form=form):
                self.assert_rule(packet, build_ground(packet, term='chromatic obstruction'), 'gap_term_delivered')

    def test_delivered_term_with_an_unbound_content_token_passes_in_every_request_form(self):
        for form in REQUEST_FORMS:
            packet = build_packet('unsearched-term', form)
            with self.subTest(form=form):
                native_review.validate_finding(packet, build_fixed_answer(packet, ground=build_ground(packet)),
                                               finding_version=2)

    def test_index_limit_with_empty_or_invented_basis_is_refused_and_coverage_limit_is_accepted(self):
        for form in REQUEST_FORMS:
            packet = build_packet('index-only', form)
            ground = build_ground(packet, term='journal index')
            for basis in ([], [dict(packet['_fixture']['identity'], text='No delivered passage contains this invented wording.')]):
                with self.subTest(form=form, basis=basis):
                    self.assert_rule(packet, dict(ground, basis=basis), 'gap_basis')
            answer = build_fixed_answer(packet)
            answer['findings']['openness']['coverage'] = 'The bound search used no journal index; this is a coverage limit.'
            native_review.validate_finding(packet, answer, finding_version=2)

    def test_read_settling_work_matches_title_or_normalized_identifier_even_when_not_decisive(self):
        packet = build_packet('read-settling-work')
        self.assertNotIn('work-read', packet['native']['scientific_context']['decisive_work_ids'])
        for work in (dict(title=load_fixed_packet('read-settling-work')['read_title']),
                     dict(title='A different label', identifiers=['https://doi.org/10.5555/SYNTHETIC.SETTLEMENT'])):
            with self.subTest(work=work):
                self.assert_rule(packet, build_ground(packet, work=work), 'gap_work_read')
        packet = build_packet('related-unread-work')
        descriptor = load_fixed_packet('related-unread-work')
        ground = build_ground(packet, work=descriptor['unread_work'])
        native_review.validate_finding(packet, build_fixed_answer(packet, ground=ground), finding_version=2)
        self.assert_rule(packet, dict(ground, formulation='A formulation no minimum declares.'), 'gap_formulation')

    def test_gap_rules_report_the_first_failure_in_contract_order(self):
        packet = build_packet('related-unread-work')
        ground = build_ground(packet, work=dict(title='Planar boundary case'))
        changed = dict(ground, formulation='Unbound formulation.', basis=[], need='')
        self.assert_rule(packet, changed, 'gap_fields')
        changed['need'] = ground['need']
        self.assert_rule(packet, changed, 'gap_formulation')
        changed['formulation'] = ground['formulation']
        self.assert_rule(packet, changed, 'gap_basis')

    def test_gap_quotation_uses_the_excerpt_matcher_normalization_and_minimum_length(self):
        packet = build_packet('unsearched-term')
        ground = build_ground(packet)
        ground['basis'][0]['text'] = packet['_fixture']['sentence'].replace(' ', '\n  ')
        native_review.validate_finding(packet, build_fixed_answer(packet, ground=ground), finding_version=2)
        ground['basis'][0]['text'] = 'The chromatic'
        self.assert_rule(packet, ground, 'gap_basis')

    def test_malformed_finding_index_cannot_supply_a_bound_query(self):
        packet = build_packet('unsearched-term')
        query_digest = packet['finding_index']['searches'][0]['queries']
        packet['finding_index']['fields'][query_digest] = ['invented query']
        with self.assertRaises(ResearchError) as caught:
            native_review.validate_finding(packet, build_fixed_answer(packet, ground=build_ground(packet)), finding_version=2)
        self.assertEqual(caught.exception.code, 'openness_reading_incomplete')

    def test_supported_open_with_a_ground_still_fails_the_outcome_rule(self):
        packet = build_packet('unsearched-term')
        with self.assertRaises(ResearchError) as caught:
            native_review.validate_finding(packet, build_fixed_answer(packet, outcome='supported_open', ground=build_ground(packet)), finding_version=2)
        self.assertEqual(caught.exception.details['rule'], 2)


class FixedPacketShapeTests(unittest.TestCase):
    def test_six_packets_have_shared_search_fields_and_only_fulltext_reading_excerpts(self):
        for name in FIXED_PACKET_NAMES:
            packet = build_packet(name)
            index = packet['finding_index']
            with self.subTest(packet=name):
                self.assertEqual(len(index['searches']), 2)
                self.assertEqual(len(index['read_works']), 1)
                self.assertEqual(index['searches'][0]['queries'], index['searches'][1]['queries'])
                self.assertEqual(index['searches'][0]['dispositions'], index['searches'][1]['dispositions'])
                self.assertTrue(all('record_kind' not in row for row in packet['reading_record']['excerpts']))
                for field_digest, value in index['fields'].items():
                    self.assertEqual(hashlib.sha256(serialize_record(value)).hexdigest(), field_digest)

    def test_persisted_fixed_packets_and_answers_cover_every_named_acceptance_case(self):
        for name in FIXED_PACKET_NAMES:
            descriptor = load_fixed_packet(name)
            packet = descriptor['packet']
            generated = build_packet(name)
            generated.pop('_fixture')
            self.assertEqual(packet, generated)
            with self.subTest(packet=name, answer='accepted'):
                native_review.validate_finding(packet, descriptor['answers']['accepted'], finding_version=2)
                self.assertEqual(descriptor['answers']['accepted']['findings']['openness']['outcome'],
                                 descriptor['expected_outcome'])
            for answer_name, answer in descriptor['answers'].items():
                if answer_name == 'accepted':
                    continue
                if answer_name.startswith('gap_work_read'):
                    rule = 'gap_work_read'
                elif answer_name.startswith('gap_basis'):
                    rule = 'gap_basis'
                elif answer_name.startswith('gap_term_bound'):
                    rule = 'gap_term_bound'
                elif answer_name == 'outcome_with_ground':
                    rule = 2
                else:
                    rule = answer_name
                with self.subTest(packet=name, answer=answer_name), self.assertRaises(ResearchError) as caught:
                    native_review.validate_finding(packet, answer, finding_version=2)
                self.assertEqual(caught.exception.code, 'openness_finding_invalid')
                self.assertEqual(caught.exception.details['rule'], rule)


class FindingIndexOriginalsTests(unittest.TestCase):
    def test_index_rebuild_checks_original_fields_and_bound_readings(self):
        case = fixture_case('variant-search', 'single')
        self.addCleanup(case.doCleanups)
        native = json.loads(case.artifacts.read(case.export()))
        records = case.store.snapshot()['records']
        index = review_search_delivery.build_finding_index(records, case.artifacts, native)
        review_search_delivery.verify_finding_index(records, case.artifacts, native, index)
        plan = review_search_delivery.plan_search_delivery([dict(binding, search=next(
            row for row in case.searches if hashlib.sha256(serialize_record(row)).hexdigest() == binding['search_digest']))
            for binding in native['scientific_context']['openness_minimum']['searches']])
        for field in ('queries', 'dispositions'):
            shared = next(row for row in plan['manifest']['shared_fields'] if row['field'] == field)
            self.assertEqual(index['searches'][0][field], shared['artifact']['sha256'])
            self.assertEqual(index['searches'][1][field], shared['artifact']['sha256'])
            self.assertEqual(sum(value == case.searches[0][field] for value in index['fields'].values()), 1)
        for mutation in ('field-key', 'bound-field', 'reading-list'):
            changed = copy.deepcopy(index)
            if mutation == 'field-key':
                changed['fields'][changed['searches'][0]['queries']] = ['different bytes']
            elif mutation == 'bound-field':
                changed_field = ['a different query with its own valid digest']
                key = hashlib.sha256(serialize_record(changed_field)).hexdigest()
                changed['fields'][key] = changed_field
                changed['searches'][0]['queries'] = key
            else:
                changed['read_works'] = []
            with self.subTest(mutation=mutation), self.assertRaises(ResearchError) as caught:
                review_search_delivery.verify_finding_index(records, case.artifacts, native, changed)
            self.assertEqual(caught.exception.code, 'openness_reading_incomplete')

    def test_pages_select_only_their_formulations_and_keep_all_bound_readings(self):
        case = fixture_case('variant-search', 'single')
        self.addCleanup(case.doCleanups)
        native = json.loads(case.artifacts.read(case.export()))
        statement = native['scientific_context']['openness_minimum']['statement']
        records = case.store.snapshot()['records']
        all_index = review_search_delivery.build_finding_index(records, case.artifacts, native)
        page_index = review_search_delivery.build_finding_index(records, case.artifacts, native, formulations=[statement])
        self.assertEqual([row['formulations'] for row in page_index['searches']], [[statement]])
        self.assertEqual(page_index['read_works'], all_index['read_works'])
        review_search_delivery.verify_finding_index(records, case.artifacts, native, page_index, formulations=[statement])


class NamedGapRecordingTests(unittest.TestCase):
    def make_case(self, name, form):
        case = fixture_case(name, form)
        self.addCleanup(case.doCleanups)
        return case

    def assert_real_index(self, case, packet, assignment):
        self.assertTrue(native_review.uses_finding_contract(assignment))
        self.assertEqual(assignment['prompt']['parameters']['finding_version'], 2)
        self.assertIn(native_review.FINDING_V2_TEXT, assignment['prompt']['text'])
        index = packet['finding_index']
        self.assertEqual(len(index['read_works']), 1)
        for field in ('queries', 'dispositions'):
            digests = {row[field] for row in index['searches']}
            self.assertEqual(len(digests), 1)
            self.assertEqual(sum(value == case.searches[0][field] for value in index['fields'].values()), 1)
        self.assertTrue(all('record_kind' not in row for row in packet.get('reading_record', {}).get('excerpts', [])))
        query = case.descriptor['queries'][0]
        if case.request_form != 'single':
            self.assertFalse(any(query in row.get('content', '') for row in packet.get('evidence', [])))

    def finish_answer(self, case, current, packet, work=None, term=None):
        from openness_named_gap_fixtures import bind_fixture_basis
        native_reference = case.store.snapshot()['records']['review_assignment'][current]['context']['native_packet']
        saved_ground = None
        for count in range(10):
            assignment = case.store.snapshot()['records']['review_assignment'][current]
            self.assert_real_index(case, packet, assignment)
            if 'closing' in packet.get('reading_final', {}):
                output = dict({field: 'Assessed the exact supplied subject.' for field in native_review.RESULT_FINDINGS},
                              outcome='unresolved', reason='The named gap requires further evidence.', grounds=[])
            else:
                bound_packet = bind_fixture_basis(case, packet)
                ground = build_ground(bound_packet, work=work, term=term)
                if 'page_plan_digest' in packet.get('reading_final', {}):
                    formulations = [case.descriptor['statement'], case.descriptor['neighbour']]
                    rows = []
                    for target in packet['reading_final']['page']['targets']:
                        page_ground = dict(ground, formulation=formulations[int(target[1:]) - 1])
                        rows.append(dict(formulation=target, coverage='The shared searches cover this formulation.',
                                         sentence='The named gap requires further evidence.', grounds=[page_ground]))
                    output = dict(targets=rows)
                else:
                    validation_packet = dict(bound_packet, native=json.loads(case.artifacts.read(native_reference)))
                    native_review.validate_finding(validation_packet, build_fixed_answer(bound_packet, ground=ground),
                                                   finding_version=2, artifacts=case.artifacts)
                    output = build_fixed_answer(bound_packet, ground=ground)
                saved_ground = saved_ground or ground
            result = case.invoke(current, output)
            self.assertNotIn('output_refusal', result, result)
            if ('page_plan_digest' not in packet.get('reading_final', {})
                    or 'closing' in packet['reading_final']):
                record = case.mutate(case.load_problem_module().record_openness, dict(assignment_id=current))['result']
                self.assertEqual(record['outcome'], 'unresolved')
                self.assertIn(saved_ground, record['grounds'])
                for field in ('formulation', 'basis', 'need', 'unsettled', 'work' if work is not None else 'search'):
                    self.assertEqual(record['grounds'][0][field], saved_ground[field])
                return record
            following = 'page-next-%d' % count
            case.assign(following, native_reference, prior=current)
            current, packet = following, case.read_packet(following)
        self.fail('Two target pages and their closing must finish.')

    def test_unread_work_is_validated_and_preserved_through_every_real_recording_path(self):
        for form in ('single', 'v6', 'v7', 'page'):
            with self.subTest(form=form):
                case = self.make_case('related-unread-work', form)
                current, packet = case.finish_reading(case.export())
                if form == 'page':
                    self.assertIn('page_plan_digest', packet['reading_final'])
                assignment = case.store.snapshot()['records']['review_assignment'][current]
                self.assertTrue(review_protocol._prompt_matches(assignment, packet))
                self.finish_answer(case, current, packet, case.descriptor['unread_work'])
                case.doCleanups()

    def build_invoked_answer(self, case, packet, ground):
        if 'page_plan_digest' in packet.get('reading_final', {}):
            targets = packet['reading_final']['page']['targets']
            formulations = [case.descriptor['statement'], case.descriptor['neighbour']]
            return dict(targets=[dict(formulation=target, coverage='Both shared queries cover this formulation.',
                sentence='The proposed search gap needs checking.',
                grounds=[dict(ground, formulation=formulations[int(target[1:]) - 1])]) for target in targets])
        return build_fixed_answer(packet, ground=ground)

    def test_bound_variant_and_undelivered_terms_are_refused_by_every_real_request_form(self):
        from openness_named_gap_fixtures import bind_fixture_basis
        groups = ((('graph spectral radius', 'gap_term_bound'),
                   ('Graph Spectral-Radius', 'gap_term_bound')),
                  (('chromatic obstruction', 'gap_term_delivered'),))
        for form in ('single', 'v6', 'v7', 'page'):
            for examples in groups:
                case = self.make_case('variant-search', form)
                current, packet = case.finish_reading(case.export())
                bound = bind_fixture_basis(case, packet)
                assignment = case.store.snapshot()['records']['review_assignment'][current]
                self.assert_real_index(case, packet, assignment)
                for index, (term, rule) in enumerate(examples):
                    with self.subTest(form=form, term=term):
                        answer = self.build_invoked_answer(case, packet, build_ground(bound, term=term))
                        attempt = 'invalid-term-%d' % index
                        response = case.invoke(current, answer, attempt=attempt)
                        self.assertEqual(response['output_refusal']['code'], 'openness_finding_invalid')
                        refusal = case.store.snapshot()['records']['review_output_refusal'][attempt]
                        self.assertEqual(refusal['details']['rule'], rule)
                case.doCleanups()

    def test_unsearched_delivered_term_is_accepted_and_recorded_by_every_real_request_form(self):
        for form in ('single', 'v6', 'v7', 'page'):
            with self.subTest(form=form):
                case = self.make_case('unsearched-term', form)
                current, packet = case.finish_reading(case.export())
                self.finish_answer(case, current, packet, term=case.descriptor['missing_term'])
                case.doCleanups()

    def test_reading_turn_does_not_select_the_finding_version(self):
        for form in ('v6', 'v7'):
            case = self.make_case('variant-search', form)
            reference = case.export()
            case.assign('turn-1', reference)
            assignment = case.store.snapshot()['records']['review_assignment']['turn-1']
            self.assertEqual(assignment['prompt']['parameters']['reading_stage'], 'reading')
            self.assertNotIn('finding_version', assignment['prompt']['parameters'])
            self.assertNotIn(native_review.FINDING_V2_TEXT, assignment['prompt']['text'])
            case.doCleanups()

    def test_refused_variant_search_is_replaced_with_its_notice_and_recorded(self):
        from openness_named_gap_fixtures import bind_fixture_basis
        from research_harness.review_continuation import plan_next
        case = self.make_case('variant-search', 'v7')
        reference = case.export()
        current, packet = case.finish_reading(reference)
        bound = bind_fixture_basis(case, packet)
        response = case.invoke(current, build_fixed_answer(bound, ground=build_ground(bound)))
        self.assertEqual(response['output_refusal']['code'], 'openness_finding_invalid')
        records = case.store.snapshot()['records']
        refusal = records['review_output_refusal']['attempt-' + current]
        self.assertEqual(refusal['details']['rule'], 'gap_term_bound')
        action = plan_next(records, case.artifacts, current)
        self.assertEqual(action['state'], 'replace')
        payload = copy.deepcopy(action['payload'])
        payload['id'] = 'replacement'
        case.mutate(review_protocol.record_assignment, payload)
        replacement = case.read_packet('replacement')
        self.assertIn('output_refusal', replacement)
        self.assertIn('gap_term_bound', json.dumps(replacement['output_refusal']))
        accepted = case.invoke('replacement', build_fixed_answer(replacement))
        self.assertNotIn('output_refusal', accepted, accepted)
        record = case.mutate(case.load_problem_module().record_openness, dict(assignment_id='replacement'))['result']
        self.assertEqual(record['outcome'], 'supported_open')


class NamedGapCapacitySchemaTests(unittest.TestCase):
    def validate_definition(self, registry, definition, value):
        from ci.capacity_packet_inventory import validate_packet
        focused = dict(registry, roles=dict(annotation={'$ref': definition}))
        return validate_packet('annotation', value, focused, request_limit_characters=1000000)

    def test_fixed_packets_and_version_two_answers_have_closed_declarations(self):
        from ci.capacity_packet_inventory import read_packet_registry, validate_packet, validate_open_members
        registry = read_packet_registry()
        self.assertEqual(validate_open_members(registry), [])
        for name in FIXED_PACKET_NAMES:
            descriptor = load_fixed_packet(name)
            with self.subTest(packet=name):
                packet = dict(descriptor['packet'], protocol='exactory-independent-review-v1')
                self.assertEqual(validate_packet('native_math', packet, registry), [])
                for answer in descriptor['answers'].values():
                    self.assertEqual(self.validate_definition(registry, 'finding_v2_answer', answer), [])
                changed = copy.deepcopy(descriptor['answers']['accepted'])
                if changed['findings']['openness']['grounds']:
                    changed['findings']['openness']['grounds'][0]['unregistered_gap_field'] = 'Unexpected.'
                    self.assertTrue(self.validate_definition(registry, 'finding_v2_answer', changed))
        ground = build_ground(build_packet('unsearched-term'))
        page = dict(targets=[dict(formulation='t1', coverage='Read.', grounds=[ground], sentence='A search remains.')])
        closing = dict({key: 'Assessed.' for key in native_review.RESULT_FINDINGS},
                       outcome='unresolved', reason='A search remains.', grounds=[ground])
        for definition, value in (('finding_v2_page_answer', page), ('finding_v2_closing_answer', closing)):
            self.assertEqual(self.validate_definition(registry, definition, value), [])
            changed = dict(value, unregistered_answer_field='Unexpected.')
            self.assertTrue(self.validate_definition(registry, definition, changed))

    def test_real_packet_forms_have_closed_named_gap_members(self):
        from ci.capacity_packet_inventory import read_packet_registry, validate_packet
        registry = read_packet_registry()
        for form in ('single', 'v6', 'v7', 'page'):
            with self.subTest(form=form):
                case = fixture_case('related-unread-work', form)
                self.addCleanup(case.doCleanups)
                current, packet = case.finish_reading(case.export())
                self.assertEqual(validate_packet('native_math', packet, registry), [])
                for member in ('searches', 'read_works'):
                    changed = copy.deepcopy(packet)
                    changed['finding_index'][member][0]['unregistered_index_field'] = 'Unexpected.'
                    self.assertTrue(validate_packet('native_math', changed, registry))
                changed = copy.deepcopy(packet)
                key = changed['finding_index']['searches'][0]['dispositions']
                changed['finding_index']['fields'][key][0]['unregistered_disposition_field'] = 'Unexpected.'
                self.assertTrue(validate_packet('native_math', changed, registry))
                if form == 'single':
                    changed = copy.deepcopy(packet)
                    entry = next(row for row in changed['evidence'] if 'identity' in row)
                    entry['identity']['unregistered_identity_field'] = 'Unexpected.'
                    self.assertTrue(validate_packet('native_math', changed, registry))
                if form == 'v6':
                    changed = copy.deepcopy(packet)
                    changed['reading_record']['finding_index']['unregistered_reference_field'] = 'Unexpected.'
                    self.assertTrue(validate_packet('native_math', changed, registry))
                case.doCleanups()
