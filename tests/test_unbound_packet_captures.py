"""C8: a legacy packet keeps the capture that its own authenticated review delivered.

Source: 20261113110022-every-open-record-and-review-capacity, closure r4.1.
"""

from contextlib import ExitStack
import json
import unittest
from unittest.mock import patch

import test_openness_capture_binding as binding_fixtures
from test_openness_capture_binding import build_before_capture_binding, COMPACT_TEXT
from unbound_packet_fixtures import open_stored_case
from research_harness import openness_reading as reading, problem_openness as openness
from research_harness import review_protocol as protocol, review_transport as transport
from research_harness.capture_selection import bind_packet_selections, select_main_capture
from research_harness.errors import ResearchError
from research_harness.literature import import_bundle
from research_harness.reading import record_reading


class UnboundPacketCaptureTests(unittest.TestCase):
    open_case = binding_fixtures.OpennessCaptureBindingTests.open_case
    choose_capture = binding_fixtures.OpennessCaptureBindingTests.choose_capture
    complete_chain = binding_fixtures.OpennessCaptureBindingTests.complete_chain
    def assert_chain_replays(self, case, identifiers, saved, before):
        records = case.store.snapshot()['records']
        final = records['review_assignment'][identifiers[-1]]
        self.assertEqual(reading.verify_chain(records, case.artifacts, final), saved['reading_turns'])
        self.assertEqual(openness.assess_openness(records, case.artifacts), [])
        native = json.loads(case.artifacts.read(final['context']['native_packet']))
        bound = bind_packet_selections(records, native['scientific_context'], artifacts=case.artifacts, review=final)
        namespace = dict(vars(reading))
        exec(compile(binding_fixtures.BUILDERS_PATH.read_bytes(), str(binding_fixtures.BUILDERS_PATH), 'exec'), namespace)
        # Rebuild with the frozen planner over the captures derived from this authenticated review.
        # The assignment API's prefix intentionally omits the current assignment to avoid a continuation fork.
        for index, identifier in enumerate(identifiers):
            assignment = records['review_assignment'][identifier]
            self.assertEqual(assignment, before['review_assignment'][identifier])
            prefix = dict(bound, review_assignment={key: row for key, row in records['review_assignment'].items()
                                                   if key in identifiers[:index]})
            route = records['review_route'][assignment['route_id']]
            with transport.text_only_delivery(True):
                ordinary = protocol.build_packet(prefix, case.artifacts, None, 'native_math', assignment['reviewer_id'],
                                                 native_packet=assignment['context']['native_packet'])
                rebuilt, _ = namespace['prepare_packet'](prefix, case.artifacts, assignment, route, ordinary)
            packet_bytes = case.artifacts.read(assignment['packet'])
            self.assertEqual(json.dumps(rebuilt, sort_keys=True, ensure_ascii=False).encode(), packet_bytes)
            self.assertEqual(assignment['prompt'], protocol.canonical_prompt(
                'native_math', reading_stage=reading.find_stage(rebuilt), reading_protocol=reading.find_protocol(rebuilt)))
            attempt = records['review_attempt']['attempt-' + identifier]
            self.assertEqual(attempt, before['review_attempt'][attempt['id']])
            self.assertTrue(protocol.authenticate_attempt(records, case.artifacts, assignment, attempt))
            request = transport.request_body(route, assignment['prompt']['text'], rebuilt)
            self.assertEqual(json.dumps(request, sort_keys=True, ensure_ascii=False).encode(),
                             case.artifacts.read(attempt['request']))


    def record_compact_reading(self, case, compact):
        bundle = case.bundle(case.versions[0], capture=compact, bundle_id='bundle-compact')
        for unit in bundle['units']:
            quote = unit['link']['locator']['quote']
            unit['link'] = case.link(case.versions[0], compact['source_id'], compact['text'], quote, kind='span')
        bundle['inventory']['links'] = [bundle['units'][0]['link']]
        case.mutate(import_bundle, bundle)
        case.mutate(record_reading, case.full_note(bundle, 'read-compact'))

    def test_stored_0870_findings_replay_with_their_delivered_capture_and_unchanged_bytes(self):
        for version in (4, 5):
            for scenario in ('control', 'choice', 'workaround'):
                with self.subTest(protocol=version, scenario=scenario), ExitStack() as stack:
                    case, metadata = open_stored_case(self, version, scenario)
                    stack.callback(case.doCleanups)
                    before = case.store.snapshot()
                    obligations = openness.assess_openness(before['records'], case.artifacts)
                    if scenario == 'choice':
                        self.assertEqual([(row['code'], row['cause']) for row in obligations],
                                         [('problem_openness_stale', 'openness_reading_incomplete')])
                    else:
                        saved = before['records']['problem_openness'][metadata['turns'][-1]]
                        self.assert_chain_replays(case, metadata['turns'], saved, before['records'])
                        final = before['records']['review_assignment'][metadata['turns'][-1]]
                        native = json.loads(case.artifacts.read(metadata['native']))
                        rows = reading.list_reading_texts(before['records'], native, artifacts=case.artifacts,
                                                          review=final)
                        expected = metadata['compact'] if scenario == 'workaround' else metadata['first']
                        self.assertEqual([row['artifact'] for row in rows if row['version_id'] == metadata['work']],
                                         [expected['text']])
                    self.assertEqual(case.store.snapshot(), before)

    def test_later_choice_leaves_the_stored_chosen_capture_finding_current(self):
        for version in (4, 5):
            with self.subTest(protocol=version), ExitStack() as stack:
                case, metadata = open_stored_case(self, version, 'workaround')
                stack.callback(case.doCleanups)
                before = case.store.snapshot()['records']
                self.choose_capture(case, metadata['first'])
                saved = before['problem_openness'][metadata['turns'][-1]]
                self.assert_chain_replays(case, metadata['turns'], saved, before)

    def test_new_chain_refuses_an_unbound_chosen_body_before_a_call_then_a_new_export_reads_it(self):
        for version in (4, 5):
            with self.subTest(protocol=version), ExitStack() as stack:
                case = self.open_case(version)
                stack.callback(case.doCleanups)
                compact = case.capture(case.versions[0], pdf_text=COMPACT_TEXT)
                self.choose_capture(case, compact)
                self.record_compact_reading(case, compact)
                with build_before_capture_binding():
                    native = case.export(priority=case.versions)
                before = case.store.snapshot()
                with patch.object(transport, 'send_request') as sender:
                    with self.assertRaises(ResearchError) as caught:
                        case.assign('turn-1', native)
                    self.assertEqual(caught.exception.code, 'openness_reading_incomplete')
                    self.assertEqual(caught.exception.details['handoff'], 'export')
                    sender.assert_not_called()
                self.assertEqual(case.store.snapshot(), before)
                fresh = case.export(priority=case.versions, name='bound-packet')
                identifiers, saved = self.complete_chain(case, fresh)
                self.assertEqual(saved['outcome'], 'supported_open')
                self.assertIn(compact['text']['sha256'], [row['text_sha256'] for identifier in identifiers[:-1]
                    for row in case.read_packet(identifier)['reading_turn']['reading_plan']['delivered_now']
                    if row['kind'] == 'fulltext'])

    def test_frozen_0870_builders_replay_the_three_cases_and_continue_an_observed_prefix(self):
        for version in (4, 5):
            for scenario in ('control', 'choice', 'workaround'):
                with self.subTest(protocol=version, scenario=scenario), ExitStack() as stack:
                    case = self.open_case(version)
                    stack.callback(case.doCleanups)
                    with build_before_capture_binding():
                        if scenario != 'control':
                            compact = case.capture(case.versions[0], pdf_text=COMPACT_TEXT)
                            self.choose_capture(case, compact)
                        if scenario == 'workaround':
                            self.record_compact_reading(case, compact)
                        native = case.export(priority=case.versions if scenario != 'choice' else ())
                        identifiers, saved = self.complete_chain(case, native)
                    records = case.store.snapshot()['records']
                    if scenario == 'choice':
                        self.assertEqual(openness.assess_openness(records, case.artifacts)[0]['cause'],
                                         'openness_reading_incomplete')
                    else:
                        self.assert_chain_replays(case, identifiers, saved, records)

    def test_observed_prefix_continues_with_the_chosen_capture_after_a_later_choice(self):
        from openness_reading_fixtures import build_sized_body
        for version in (4, 5):
            with self.subTest(protocol=version), ExitStack() as stack:
                case = self.open_case(version)
                stack.callback(case.doCleanups)
                if version == 4:
                    case.limit_input(200000)
                first = case.store.snapshot()['records']['work'][case.versions[0]]['fulltexts'][0]
                compact = case.capture(case.versions[0], pdf_text=build_sized_body(1, 160000))
                with build_before_capture_binding():
                    self.choose_capture(case, compact)
                    self.record_compact_reading(case, compact)
                    native = case.export(priority=case.versions)
                    case.assign('turn-1', native)
                    case.invoke('turn-1', case.build_reading_response(case.read_packet('turn-1'), excerpts=[]))
                self.choose_capture(case, first)
                identifiers = ['turn-1']
                while True:
                    identifier = 'turn-%d' % (len(identifiers) + 1)
                    case.assign(identifier, native, prior=identifiers[-1])
                    identifiers.append(identifier)
                    packet = case.read_packet(identifier)
                    if reading.find_stage(packet) == 'final':
                        case.invoke(identifier, case.build_native_result(packet))
                        saved = case.mutate(case.load_problem_module().record_openness,
                                            {'assignment_id': identifier})['result']
                        break
                    case.invoke(identifier, case.build_reading_response(packet, excerpts=[]))
                    self.assertLess(len(identifiers), 16)
                delivered = [row for identifier in identifiers[:-1]
                             for row in case.read_packet(identifier)['reading_turn']['reading_plan']['delivered_now']
                             if row['kind'] == 'fulltext' and row['version_id'] == case.versions[0]]
                self.assertEqual([(row['text_sha256'], row['part']) for row in delivered],
                                 [(compact['text']['sha256'], 1), (compact['text']['sha256'], 2)])
                self.assert_chain_replays(case, identifiers, saved, case.store.snapshot()['records'])

    def test_stored_plans_keep_inventory_without_crediting_unready_requests(self):
        import copy
        case, metadata = open_stored_case(self, 4, 'workaround')
        records = case.store.snapshot()['records']
        final = records['review_assignment'][metadata['turns'][-1]]
        native = json.loads(case.artifacts.read(metadata['native']))
        def list_texts(current, review):
            return [row['artifact'] for row in reading.list_reading_texts(
                current, native, artifacts=case.artifacts, review=review) if row['version_id'] == metadata['work']]
        self.assertEqual(list_texts(records, final), [metadata['compact']['text']])
        self.assertEqual(list_texts(records, None), [])
        for change in ('unauthenticated', 'unready'):
            with self.subTest(change=change):
                altered = copy.deepcopy(records)
                if change == 'unauthenticated':
                    for attempt in altered['review_attempt'].values():
                        attempt['origin'] = 'imported'
                else:
                    altered['review_attempt'] = {}
                self.assertEqual(list_texts(altered, final), [metadata['compact']['text']])
                with self.assertRaises(ResearchError) as caught:
                    reading.verify_chain(altered, case.artifacts, final)
                self.assertEqual(caught.exception.code, 'review_context_unverified')
        other = dict(final, id='another-review', context=dict(final['context'], prior_assignment_id=None))
        self.assertEqual(list_texts(records, other), [])
        altered = copy.deepcopy(records)
        for assignment in altered['review_assignment'].values():
            assignment['packet_digest'] = '0' * 64
        self.assertEqual(list_texts(altered, altered['review_assignment'][final['id']]), [])

    def test_single_request_recording_uses_the_authenticated_chosen_extraction(self):
        from openness_reading_fixtures import OpennessReadingCase
        class SmallCase(OpennessReadingCase):
            text_sizes = (1000,)
        case = SmallCase()
        self.addCleanup(case.doCleanups)
        case.setUp()
        with build_before_capture_binding():
            compact = case.capture(case.versions[0], pdf_text=COMPACT_TEXT)
            self.choose_capture(case, compact)
            self.record_compact_reading(case, compact)
            native = case.export(priority=case.versions)
            case.assign('turn-1', native)
            self.assertIsNone(reading.find_stage(case.read_packet('turn-1')))
            case.invoke('turn-1', case.build_native_result(case.read_packet('turn-1')))
        # This is a new finding over the stored request, so prepare_openness must bind the same extraction.
        saved = case.mutate(case.load_problem_module().record_openness, {'assignment_id': 'turn-1'})['result']
        records = case.store.snapshot()['records']
        rows = reading.list_reading_texts(records, json.loads(case.artifacts.read(native)), artifacts=case.artifacts,
                                          review=records['review_assignment']['turn-1'])
        self.assertEqual([row['artifact'] for row in rows], [compact['text']])
        self.assertEqual(saved['outcome'], 'supported_open')
        self.assertEqual(openness.assess_openness(records, case.artifacts), [])

    def test_delivery_contract_precedes_requests_and_replays_the_chosen_representation(self):
        import test_problem_openness_source_delivery as source_fixtures
        fixture = source_fixtures.SourceDeliveryTests()
        self.addCleanup(fixture.doCleanups)
        fixture.setUp()
        case = fixture.case
        compact = case.capture(fixture.work, pdf_text=fixture.pdf_text + '\nAdditional synthetic extraction line.')
        case.versions = [fixture.work]
        with build_before_capture_binding():
            self.choose_capture(case, compact)
            self.record_compact_reading(case, compact)
            packet = fixture.export()
            saved = case.record_openness(packet, 'source-openness')
        before = case.store.snapshot()
        self.assertEqual(case.assessment(), [])
        records = before['records']
        assignment = records['review_assignment']['source-openness']
        from research_harness.problem_openness_sources import build_delivery
        self.assertEqual(build_delivery(records, case.artifacts, packet, review=assignment), saved['openness_delivery'])
        rows = reading.list_reading_texts(records, packet, artifacts=case.artifacts, review=assignment)
        self.assertIn(compact['text'], [row['artifact'] for row in rows])
        self.assertEqual(case.store.snapshot(), before)

    def test_ambiguous_delivery_falls_back_per_original_and_explicit_empty_bindings_win(self):
        import copy
        from research_harness.capture_selection import PACKET_KEY, PROTOCOL, SELECTION_KIND
        case, metadata = open_stored_case(self, 4, 'workaround')
        records = case.store.snapshot()['records']
        review = records['review_assignment'][metadata['turns'][-1]]
        context = json.loads(case.artifacts.read(metadata['native']))['scientific_context']
        chosen = bind_packet_selections(records, context, artifacts=case.artifacts, review=review)
        self.assertEqual([row['source_id'] for row in chosen[SELECTION_KIND].values()
                          if row['version_id'] == metadata['work']], [metadata['compact']['source_id']])
        context = copy.deepcopy(context)
        context['openness_delivery'] = {'sources': [{'version_id': metadata['work'], 'representations': [
            {'source_id': capture['source_id'], 'main_representation': True,
             'original_descriptor': capture['original'], 'extraction_descriptor': capture['text']}
            for capture in (metadata['first'], metadata['compact'])]}]}
        ambiguous = bind_packet_selections(records, context, artifacts=case.artifacts, review=review)
        from research_harness.graph import main_captures
        work = records['work'][metadata['work']]
        self.assertEqual(main_captures(ambiguous, work)[0], metadata['first'])
        self.assertFalse(any(row['version_id'] == metadata['work'] for row in ambiguous[SELECTION_KIND].values()))
        for binding in ({'protocol': PROTOCOL, 'choices': []}, {}):
            context[PACKET_KEY] = binding
            self.assertEqual(bind_packet_selections(records, context, artifacts=case.artifacts, review=review)[SELECTION_KIND], {})

    def test_preflight_uses_the_capture_already_delivered_by_the_review(self):
        from research_harness.review_preflight import build_assignment_report
        for version in (4, 5):
            with self.subTest(protocol=version), ExitStack() as stack:
                case, metadata = open_stored_case(self, version, 'workaround')
                stack.callback(case.doCleanups)
                records = case.store.snapshot()['records']
                assignment = records['review_assignment'][metadata['turns'][-1]]
                report = build_assignment_report(records, case.artifacts, assignment)
                mandatory = {row['sha256'] for row in report['mandatory_final_input']['mandatory_components']
                             if row['kind'] == 'evidence'}
                self.assertNotIn(metadata['compact']['text']['sha256'], mandatory)

    def test_new_chain_refusal_matches_original_bytes_across_reported_media_types(self):
        from research_harness.acquisition import acquire_fulltext
        from research_fixtures import client
        case = self.open_case(4)
        work = case.versions[0]
        first = case.store.snapshot()['records']['work'][work]['fulltexts'][0]
        data = case.artifacts.read(first['original'])
        http, _, _ = client([(200, {'Content-Type': 'application/octet-stream'}, data)], max_retries=0)
        compact = acquire_fulltext(case.store, work, 'https://arxiv.org/pdf/' + work[6:], http=http,
            extractor=lambda body: {'status': 'extracted', 'text': COMPACT_TEXT},
            expected_revision=case.store.revision, request_id='capture-generic-pdf')['capture']
        self.assertEqual(first['original']['sha256'], compact['original']['sha256'])
        self.assertNotEqual(first['original'], compact['original'])
        with build_before_capture_binding():
            self.choose_capture(case, compact)
            self.record_compact_reading(case, compact)
            native = case.export(priority=case.versions)
        with self.assertRaises(ResearchError) as caught:
            case.assign('turn-1', native)
        self.assertEqual(caught.exception.code, 'openness_reading_incomplete')
        self.assertEqual(caught.exception.details['handoff'], 'export')

    def test_normalized_author_identity_keeps_the_earlier_chosen_capture_delivery(self):
        import copy
        case, metadata = open_stored_case(self, 4, 'workaround')
        records = copy.deepcopy(case.store.snapshot()['records'])
        assignment = records['review_assignment'][metadata['turns'][-1]]
        assignment['author_id'] = assignment['author_id'].upper()
        saved = records['problem_openness'][metadata['turns'][-1]]
        self.assertTrue(protocol.assignment_state(records, case.artifacts, assignment['id'])['ready'])
        self.assertEqual(reading.verify_chain(records, case.artifacts, assignment), saved['reading_turns'])

    def test_first_assignment_ignores_a_carried_supplement_as_a_main_extraction(self):
        import copy
        case, metadata = open_stored_case(self, 4, 'workaround')
        records = copy.deepcopy(case.store.snapshot()['records'])
        native = json.loads(case.artifacts.read(metadata['native']))
        bound = bind_packet_selections(records, native['scientific_context'])
        with self.assertRaises(ResearchError) as caught:
            reading._require_initial_captures(bound, native)
        self.assertEqual(caught.exception.code, 'openness_reading_incomplete')
        supplement = copy.deepcopy(records['source_bundle']['bundle-compact']['units'][0])
        supplement.update(id='same-original-supplement', kind='supplement')
        records['source_bundle']['bundle-1']['units'].append(supplement)
        bound = bind_packet_selections(records, native['scientific_context'])
        reading._require_initial_captures(bound, native)

    def test_plan_identity_uses_every_inventory_section_and_requires_carried_text(self):
        import copy
        from research_harness.capture_selection import find_plan_texts
        case, metadata = open_stored_case(self, 4, 'stopped-chosen-uncalled')
        native = json.loads(case.artifacts.read(metadata['native']))
        carried = {row['artifact']['sha256'] for row in native['evidence']}
        identity = (metadata['work'], metadata['compact']['text']['sha256'])
        row = next(row for row in metadata['planned_remaining'] if row.get('version_id') == metadata['work'])
        for field in ('delivered_before', 'delivered_now', 'remaining'):
            with self.subTest(field=field):
                self.assertEqual(find_plan_texts([{field: [row]}], carried), {identity})
                self.assertEqual(find_plan_texts([{field: [row]}], set()), set())
        for change in ('version', 'digest', 'kind'):
            with self.subTest(change=change):
                altered = copy.deepcopy(row)
                altered[{'version': 'version_id', 'digest': 'text_sha256', 'kind': 'kind'}[change]] = {
                    'version': metadata['first_work'], 'digest': '0' * 64, 'kind': 'record'}[change]
                self.assertNotIn(identity, find_plan_texts([{'remaining': [altered]}], carried))
        # Part bounds govern delivery checks, never the capture inventory of a stored plan.
        altered = dict(row, part=99, start=1, end=2)
        self.assertEqual(find_plan_texts([{'remaining': [altered]}], carried), {identity})

    def test_planless_reviews_identify_only_complete_whole_bodies_or_exact_parts(self):
        from openness_reading_fixtures import build_sized_body
        from research_harness.capture_selection import has_delivered_capture
        from research_harness import review_excerpts
        case = self.open_case(4)
        body = build_sized_body(1, 160000)
        capture = case.capture(case.versions[0], pdf_text=body)
        sent = {'reading_record': {}, 'evidence': [
            {'artifact': capture['text'], 'encoding': 'utf-8', 'content': body}]}
        self.assertTrue(has_delivered_capture(case.artifacts, sent, capture))
        start, end = review_excerpts.compute_part_spans(body)[0]
        reference = case.artifacts.put(body[start:end].encode(), reading.PART_MEDIA_TYPE)
        sent['evidence'] = [{'artifact': reference, 'encoding': 'utf-8', 'content': body[start:end]}]
        self.assertTrue(has_delivered_capture(case.artifacts, sent, capture))
        sent['evidence'][0]['content'] = 'A partial quotation cannot name an extraction.'
        self.assertFalse(has_delivered_capture(case.artifacts, sent, capture))
