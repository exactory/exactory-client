"""Replay runs 1, 2, 4, 5 and 6 of the writer's capture-choice measurements with synthetic sources.

Source: 20261111223126-layout-extraction-inflates-fulltext-delivery.
"""

from contextlib import ExitStack, contextmanager
import hashlib
import json
from pathlib import Path
import unittest
from unittest.mock import patch

from openness_reading_fixtures import OpennessReadingCase
from test_openness_search_sharing import SearchSharingCase
from research_harness import openness_reading as reading, problem_openness as openness, problem_openness_sources as sources
from research_harness import review_protocol as protocol, review_transport as transport
from research_harness.capture_selection import PACKET_KEY, describe_capture_selections, select_main_capture
from research_harness.evidence import digest
from research_harness.literature import import_bundle
from research_harness.reading import record_reading

BUILDERS_PATH = Path(__file__).parent / 'fixtures/openness_capture_builders_3e8325b.py.txt'
BUILDERS_SHA256 = '7fa7bc60117ef91c630c945fa883c70fa6685e7c859e59d04936d99a6bb00bf7'
COMPACT_TEXT = ('Paper 1 opening line.\nThe compact extraction gives the complete theorem and proof.\n'
                'References: none.')


@contextmanager
def build_before_capture_binding():
    """Use the unchanged builders that produced unbound packets, including v4 and v5 plans."""
    source = BUILDERS_PATH.read_bytes()
    if hashlib.sha256(source).hexdigest() != BUILDERS_SHA256:
        raise AssertionError('The pre-binding openness builders changed')
    with ExitStack() as stack:
        for module, names in ((reading, ('list_reading_texts', 'list_math_text_rows', '_list_legacy_texts')),
                              (openness, ('collect_reading_artifacts', 'export_openness')),
                              (sources, ('build_delivery',))):
            namespace = dict(vars(module))
            exec(compile(source, str(BUILDERS_PATH), 'exec'), namespace)
            for name in names:
                stack.enter_context(patch.object(module, name, namespace[name]))
        yield


class OpennessCaptureBindingTests(unittest.TestCase):
    def open_case(self, version):
        case = (OpennessReadingCase if version == 4 else SearchSharingCase)()
        self.addCleanup(case.doCleanups)
        case.setUp()
        if version == 4:
            case.limit_input()
        return case

    def choose_capture(self, case, capture):
        case.mutate(select_main_capture, dict(version_id=case.versions[0], source_id=capture['source_id'],
                                             reason='Deliver the complete compact extraction of the same original.'))

    def complete_chain(self, case, native, first='turn-1'):
        identifiers = [first]
        case.assign(first, native)
        while reading.find_stage(case.read_packet(identifiers[-1])) == 'reading':
            packet = case.read_packet(identifiers[-1])
            case.invoke(identifiers[-1], case.build_reading_response(packet, excerpts=[]))
            identifiers.append(first + '-next-%d' % len(identifiers))
            case.assign(identifiers[-1], native, prior=identifiers[-2])
            self.assertLess(len(identifiers), 16)
        self.assertGreater(len(identifiers), 1)
        final = identifiers[-1]
        case.invoke(final, case.build_native_result(case.read_packet(final)))
        saved = case.mutate(case.load_problem_module().record_openness, {'assignment_id': final})['result']
        return identifiers, saved

    def assert_chain_replays(self, case, identifiers, saved, before):
        records = case.store.snapshot()['records']
        final = records['review_assignment'][identifiers[-1]]
        self.assertEqual(reading.verify_chain(records, case.artifacts, final), saved['reading_turns'])
        self.assertEqual(openness.assess_openness(records, case.artifacts), [])
        route = records['review_route']['route']
        for index, identifier in enumerate(identifiers):
            assignment = records['review_assignment'][identifier]
            self.assertEqual(assignment, before['review_assignment'][identifier])
            packet_bytes = case.artifacts.read(assignment['packet'])
            packet = json.loads(packet_bytes)
            prefix = dict(records, review_assignment={key: row for key, row in records['review_assignment'].items()
                                                      if key in identifiers[:index]})
            with transport.text_only_delivery(True):
                ordinary = protocol.build_packet(prefix, case.artifacts, None, 'native_math', 'openness-reviewer',
                                                 native_packet=assignment['context']['native_packet'])
                rebuilt, _ = reading.prepare_packet(prefix, case.artifacts, assignment, route, ordinary)
            self.assertEqual(json.dumps(rebuilt, sort_keys=True, ensure_ascii=False).encode(), packet_bytes)
            self.assertEqual(assignment['prompt'], protocol.canonical_prompt(
                'native_math', reading_stage=reading.find_stage(packet), reading_protocol=reading.find_protocol(packet)))
            attempt = records['review_attempt']['attempt-' + identifier]
            self.assertEqual(attempt, before['review_attempt'][attempt['id']])
            self.assertTrue(protocol.authenticate_attempt(records, case.artifacts, assignment, attempt))
            request_bytes = case.artifacts.read(attempt['request'])
            self.assertEqual(hashlib.sha256(request_bytes).hexdigest(), attempt['request']['sha256'])
            request = transport.request_body(route, assignment['prompt']['text'], rebuilt)
            self.assertEqual(json.dumps(request, sort_keys=True, ensure_ascii=False).encode(), request_bytes)

    def check_stored_finding(self, version):
        case = self.open_case(version)
        with build_before_capture_binding():
            native = case.export(priority=case.versions)
            identifiers, saved = self.complete_chain(case, native)
        packet_bytes = case.artifacts.read(native)
        self.assertNotIn(PACKET_KEY, json.loads(packet_bytes)['scientific_context'])
        rebuilt_export = case.export(priority=case.versions, name='unbound-export-rebuilt')
        self.assertEqual(case.artifacts.read(rebuilt_export), packet_bytes)
        before = case.store.snapshot()['records']
        compact = case.capture(case.versions[0], pdf_text=COMPACT_TEXT)
        self.assertEqual(openness.assess_openness(case.store.snapshot()['records'], case.artifacts), [])
        self.choose_capture(case, compact)
        self.assert_chain_replays(case, identifiers, saved, before)
        self.assertEqual(case.artifacts.read(native), packet_bytes)
        self.assertEqual(case.store.snapshot()['records']['problem_openness'], before['problem_openness'])
        # A later export freezes the choice without another bundle or reading of this original.
        selected = json.loads(case.artifacts.read(case.export(priority=case.versions, name='selected-packet')))
        records = case.store.snapshot()['records']
        self.assertEqual(selected['scientific_context'][PACKET_KEY], describe_capture_selections(records)[PACKET_KEY])
        rows = reading.list_reading_rows(records, case.artifacts, selected)
        self.assertIn(compact['text']['sha256'], [row.get('text_sha256') for row in rows])

    def test_stored_v4_packets_plans_prompts_requests_and_finding_survive_a_later_choice(self):
        self.check_stored_finding(4)

    def test_stored_v5_packets_plans_prompts_requests_and_finding_survive_a_later_choice(self):
        self.check_stored_finding(5)

    def check_choice_before_export(self, priority):
        for version in (4, 5):
            with self.subTest(protocol=version), ExitStack() as stack:
                case = self.open_case(version)
                stack.callback(case.doCleanups)
                first = case.store.snapshot()['records']['work'][case.versions[0]]['fulltexts'][0]
                compact = case.capture(case.versions[0], pdf_text=COMPACT_TEXT)
                self.assertEqual(first['original'], compact['original'])
                self.choose_capture(case, compact)
                native = case.export(priority=case.versions if priority else ())
                packet = json.loads(case.artifacts.read(native))
                carried = {row['digest'] for row in packet['evidence']}
                self.assertIn(compact['text']['sha256'], carried)
                self.assertNotIn(first['text']['sha256'], carried)
                self.assertIn(first['original']['sha256'], carried)
                records = case.store.snapshot()['records']
                self.assertEqual(packet['scientific_context'][PACKET_KEY], describe_capture_selections(records)[PACKET_KEY])
                self.assertEqual(packet['scientific_context']['openness_minimum']['reading_digests'],
                                 sorted(digest(row) for row in records['reading'].values()))
                # Another choice before assignment must not change the exported selection or its units.
                self.choose_capture(case, first)
                identifiers, saved = self.complete_chain(case, native)
                delivered = [row for identifier in identifiers[:-1]
                             for row in case.read_packet(identifier)['reading_turn']['reading_plan']['delivered_now']
                             if row['kind'] == 'fulltext' and row['version_id'] == case.versions[0]]
                self.assertEqual([row['text_sha256'] for row in delivered], [compact['text']['sha256']])
                final = case.read_packet(identifiers[-1])
                self.assertEqual(final['native'], packet)
                self.assertEqual(saved['outcome'], 'supported_open')
                self.assert_chain_replays(case, identifiers, saved, case.store.snapshot()['records'])
                fresh = json.loads(case.artifacts.read(case.export(priority=case.versions, name='first-again')))
                texts = reading.list_reading_texts(case.store.snapshot()['records'], fresh)
                self.assertIn(first['text']['sha256'], [row['text_sha256'] for row in texts])

    def test_choice_before_prioritized_export_delivers_the_chosen_fulltext_unit(self):
        self.check_choice_before_export(True)

    def test_choice_before_export_without_priority_delivers_the_chosen_fulltext_unit(self):
        self.check_choice_before_export(False)

    def test_a_new_reading_on_the_chosen_capture_stays_bound_after_another_choice(self):
        for version in (4, 5):
            with self.subTest(protocol=version), ExitStack() as stack:
                case = self.open_case(version)
                stack.callback(case.doCleanups)
                first = case.store.snapshot()['records']['work'][case.versions[0]]['fulltexts'][0]
                compact = case.capture(case.versions[0], pdf_text=COMPACT_TEXT)
                self.choose_capture(case, compact)
                bundle = case.bundle(case.versions[0], capture=compact, bundle_id='bundle-compact')
                for unit in bundle['units']:
                    quote = unit['link']['locator']['quote']
                    unit['link'] = case.link(case.versions[0], compact['source_id'], compact['text'], quote, kind='span')
                bundle['inventory']['links'] = [bundle['units'][0]['link']]
                case.mutate(import_bundle, bundle)
                case.mutate(record_reading, case.full_note(bundle, 'read-compact'))
                native = json.loads(case.artifacts.read(case.export(priority=case.versions)))
                self.choose_capture(case, first)
                texts = reading.list_reading_texts(case.store.snapshot()['records'], native)
                self.assertEqual([row['text_sha256'] for row in texts if row['version_id'] == case.versions[0]],
                                 [compact['text']['sha256']])

    def test_math_and_legacy_text_rows_use_the_packet_binding_and_unbound_packets_keep_the_first(self):
        case = self.open_case(4)
        native = json.loads(case.artifacts.read(case.export()))
        compact = case.capture(case.versions[0], pdf_text=COMPACT_TEXT)
        self.choose_capture(case, compact)
        records = case.store.snapshot()['records']
        self.assertEqual([row['artifact'] for row in reading.list_math_text_rows(records, native)],
                         [case.texts[version] for version in case.versions])
        self.assertEqual([row['artifact'] for row in reading._list_legacy_texts(records, native)],
                         [case.texts[version] for version in case.versions])
        selected = json.loads(case.artifacts.read(case.export(name='selected-math-packet')))
        first = records['work'][case.versions[0]]['fulltexts'][0]
        self.choose_capture(case, first)
        records = case.store.snapshot()['records']
        rows = reading.list_math_text_rows(records, selected)
        self.assertIn(compact['text'], [row['artifact'] for row in rows])
        self.assertIn(compact['text'], [row['artifact'] for row in reading._list_legacy_texts(records, selected)])

    def test_stored_source_delivery_contract_keeps_its_bound_capture_under_v4_and_v5(self):
        from codex_route_fixtures import invoke_codex
        from test_openness_v4_replay import V4ReplayTests
        from research_harness import review_excerpts
        for version in (reading.PROTOCOL_V4, reading.PROTOCOL_V5):
            with self.subTest(protocol=version), ExitStack() as stack:
                owner = V4ReplayTests()
                self.addCleanup(owner.doCleanups)
                stack.callback(owner.doCleanups)
                owner.setUp()
                case = owner.case
                compact = case.capture(owner.fixture.work, pdf_text=COMPACT_TEXT)
                stack.enter_context(patch.object(review_excerpts, 'TURN_FULLTEXT_CHARACTERS', 3000))
                stack.enter_context(patch.object(reading, 'find_chain_protocol', return_value=version))
                with build_before_capture_binding():
                    native = owner.fixture.standalone_packet('v4-first')
                    reference = case.pin_json(native)
                    owner.assign('v4-first', reference)
                    invoke_codex(case, 'v4-first', owner.response(owner.packet('v4-first')))
                    identifiers = owner.finish(reference)
                    saved = case.mutate(case.load_problem_module().record_openness,
                                        {'assignment_id': identifiers[-1]})['result']
                before = case.store.snapshot()['records']
                case.mutate(select_main_capture, dict(version_id=owner.fixture.work, source_id=compact['source_id'],
                    reason='Read the compact extraction in future exports.'))
                records = case.store.snapshot()['records']
                self.assertEqual(sources.build_delivery(records, case.artifacts, native),
                                 native['scientific_context']['openness_delivery'])
                self.assertEqual(openness.assess_openness(records, case.artifacts), [])
                self.assertEqual(reading.verify_chain(records, case.artifacts,
                    records['review_assignment'][identifiers[-1]]), saved['reading_turns'])
                self.assertEqual(records['problem_openness'], before['problem_openness'])
                selected = owner.fixture.export('selected', support='standalone-proof', priority=True)
                self.assertEqual(selected['scientific_context']['openness_delivery']['sources'][0]['selected_source_id'],
                                 compact['source_id'])

    def test_a_legacy_reading_continuation_checks_its_excerpts_against_the_first_capture(self):
        from openness_legacy_fixtures import build_as_0_65_2
        case = self.open_case(4)
        native = case.export(priority=case.versions)
        with build_as_0_65_2():
            first = case.assign('turn-1', native)
        packet_bytes = case.artifacts.read(first['packet'])
        packet = case.read_packet('turn-1')
        self.assertEqual(reading.find_protocol(packet), reading.PROTOCOL_V2)
        case.invoke('turn-1', case.build_reading_response(packet))
        compact = case.capture(case.versions[0], pdf_text=COMPACT_TEXT)
        self.choose_capture(case, compact)
        case.assign('turn-2', native, prior='turn-1')
        record = case.read_packet('turn-2')['reading_turn']['reading_record']
        self.assertTrue(record['excerpts'])
        self.assertTrue(all(row['status'] == 'verified' for row in record['excerpts']))
        self.assertEqual(case.artifacts.read(first['packet']), packet_bytes)
        records = case.store.snapshot()['records']
        self.assertTrue(protocol.authenticate_attempt(records, case.artifacts, first,
                                                      records['review_attempt']['attempt-turn-1']))

    def test_export_refuses_missing_or_corrupt_chosen_text_without_changing_the_record(self):
        case = self.open_case(4)
        compact = case.capture(case.versions[0], pdf_text=COMPACT_TEXT)
        self.choose_capture(case, compact)
        path = case.root / compact['text']['path']
        data = path.read_bytes()
        before = case.store.snapshot()
        for code in ('artifact_missing', 'artifact_corrupt'):
            with self.subTest(code=code):
                path.chmod(0o600)
                if code == 'artifact_missing':
                    path.unlink()
                else:
                    path.write_bytes(b'Changed extraction bytes.')
                try:
                    case.assert_error(code, lambda: case.export(name=code))
                    self.assertEqual(case.store.snapshot(), before)
                    self.assertFalse((case.root / code).exists())
                finally:
                    path.write_bytes(data)
        # An extraction can have exactly the original's bytes; replacing its text must retain the original.
        original = case.artifacts.read(compact['original']).decode()
        alias = case.capture(case.versions[0], pdf_text=original)
        self.assertEqual(alias['text']['sha256'], alias['original']['sha256'])
        link = case.link(case.versions[0], alias['source_id'], alias['text'], kind='span')
        note = case.full_note(dict(id='passage', version_id=case.versions[0], units=[dict(id='body', link=link)]),
                              'read-original-text')
        note.update(depth='passage')
        note.pop('bundle_id')
        note['inspections'][0]['unit_id'] = None
        case.mutate(record_reading, note)
        records = case.store.snapshot()['records']
        references = openness.collect_reading_artifacts(records, case.artifacts, records['reading'][note['id']])
        self.assertEqual({ref['sha256'] for ref in references},
                         {alias['original']['sha256'], compact['text']['sha256']})
