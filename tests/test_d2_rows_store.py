"""Reviewed store-code rows and their commands over synthetic stored records."""

import copy
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from contextlib import ExitStack
from unittest.mock import patch

from research_harness import cli, lineage, literature, stop_classes
from research_harness.errors import ResearchError
from research_harness.evidence import digest
from research_harness.storage import Store
from research_fixtures import atom, client, entry, xml_response
import test_review_continuation as continuation_fixtures
from test_research_lineage import LineageCase
import test_research_reference_families as reference_fixtures


ROOT = Path(__file__).resolve().parents[1]
GROUP_EVIDENCE = ROOT / 'tests/fixtures/series_code_groups/d2-rows-store.json'
FULLTEXT_CODES = ('xml_depth_limit', 'xml_element_limit', 'xml_external_entity',
                 'xml_internal_entity', 'xml_text_limit')


class StoreRowTests(unittest.TestCase):
    def test_public_handoff_roots_follow_the_cli_dispatch_paths(self):
        from ci import series_code_reach
        self.assertIn('search deliver-attempt', series_code_reach.EXTERNAL_COMMAND_ROOTS)
        sources = series_code_reach.read_sources(ROOT)
        roots = series_code_reach.collect_command_roots(sources)
        self.assertEqual(roots['recover'], ('research_harness/cli.py:run',))
        self.assertEqual(roots['search deliver-attempt'], (
            'skills/math-solver/harness/search_controller/service.py:Controller.command',))
        executable = series_code_reach.read_executable_commands(sources)
        self.assertTrue(executable <= set(roots))
        self.assertTrue({'recover', 'search deliver-attempt'}.isdisjoint(executable))

    def prepare_case(self, case_class):
        case = case_class()
        self.addCleanup(case.doCleanups)
        case.setUp()
        return case

    def take_handoff(self, code, command):
        self.assertIn(code, stop_classes.STOP_REGISTRY)
        plan = stop_classes.describe_terminal(code)
        self.assertEqual((plan['state'], plan['command'], plan['stop_class']),
                         ('handoff', command, 'decision'))
        self.assertNotIn('unlisted', plan['details'])
        from research_harness.review_plan_steps import complete_plan
        plan = complete_plan({}, None, dict(id='row-head'), plan)
        self.assertEqual(plan['details']['step']['payload'], {})
        self.assertEqual(plan['details']['step']['chooses'],
                         [] if command == 'recover' else ['command inputs'])
        return plan

    def execute_handoff(self, case, plan, payload):
        path = case.root / 'row-command.json'
        step = plan['details']['step']
        payload = dict(step['payload'], **payload)
        path.write_text(json.dumps(payload), encoding='utf-8')
        revision = case.store.revision
        args = cli.build_parser().parse_args([plan['command'], '--workspace', str(case.root),
            '--file', str(path), '--expected-revision', str(revision),
            '--request-id', 'row-command-' + str(revision)])
        return cli.run(args)

    def test_every_stored_state_member_ends_the_series_with_the_group_decision(self):
        from research_harness.review_series import run_series
        fixture = self.prepare_case(continuation_fixtures.ContinuationTests)
        case = fixture.case
        before = copy.deepcopy(case.read_records())
        packet = case.artifacts.read(fixture.assignment['packet'])
        members = json.loads(GROUP_EVIDENCE.read_text())['stored_state']['members']
        self.assertEqual(set(members), set(stop_classes._STORED_STATE_CODES_STORE))
        self.assertTrue(members)
        from test_series_code_reach import record_reachable_errors
        for code in stop_classes._STORED_STATE_CODES_STORE:
            with self.subTest(code=code):
                row = stop_classes.STOP_REGISTRY[code]
                self.assertEqual(row['group'], 'stored_state')
                lines = []
                with record_reachable_errors('run_series'), patch.dict(cli.OPERATIONS, {
                        'review-run': lambda *args, **kwargs: self.refuse_operation(code)}):
                    self.assertEqual(run_series(case.store, fixture.assignment['id'], emit=lines.append), 3)
                terminal = lines[-1]
                self.assertEqual((terminal['state'], terminal['code'], terminal['stop_class']),
                                 ('stop', code, 'decision'))
                decision = copy.deepcopy({key: value for key, value in row['next'].items() if key != 'kind'})
                decision['then'][0]['steps'] = [dict(command='record-check',
                    arguments={'--workspace': str(case.store.root)}, payload={}, chooses=[], prerequisites=[])]
                self.assertEqual(terminal['details']['decision'], decision)
                self.assertNotIn('unlisted', terminal['details'])
        # Registry additions never rewrite an assignment, packet, or earlier record.
        after = case.read_records()
        for kind, records in before.items():
            for key, record in records.items():
                self.assertEqual(after[kind][key], record)
        self.assertEqual(case.artifacts.read(fixture.assignment['packet']), packet)
        self.assertEqual(digest(after['review_assignment']), digest(before['review_assignment']))

    def refuse_operation(self, code):
        raise ResearchError(code, 'The retained record does not verify.')

    def test_handoff_payload_keeps_the_row_prerequisites(self):
        from research_harness.review_plan_steps import complete_plan
        terminal = stop_classes.describe_terminal('openness_minimum_gaps')
        rule = copy.deepcopy(terminal['details']['next'])
        plan = complete_plan({}, None, dict(id='row-head'), terminal)
        self.assertEqual(plan['details']['step']['payload'], rule['payload'])
        self.assertEqual(plan['details']['step']['chooses'], rule['chooses'])
        self.assertEqual(plan['details']['step']['prerequisites'], rule['prerequisites'])

    def test_renderer_contention_waits_for_the_render_lock(self):
        from research_harness.review_series import run_series
        fixture = self.prepare_case(continuation_fixtures.ContinuationTests)
        lines = []
        with patch.dict(cli.OPERATIONS, {'review-run': lambda *args, **kwargs:
                self.refuse_operation('output_view_render_deferred')}):
            self.assertEqual(run_series(fixture.case.store, 'bar-first', emit=lines.append), 4)
        self.assertEqual((lines[-1]['state'], lines[-1]['stop_class']), ('wait', 'waiting'))
        self.assertEqual(lines[-1]['details']['next'], dict(kind='wait',
            ends='The output renderer releases the workspace render lock.'))
        self.assertIn('not_before', lines[-1])

    def test_malformed_identifiers_and_urls_are_authenticated_output_refusals(self):
        from research_harness import review_protocol
        for code, identifier in (('invalid_identifier', 'not-an-exact-work'),
                                 ('unsafe_url', 'url:http://example.org/article')):
            with self.subTest(code=code):
                fixture = self.prepare_case(continuation_fixtures.ContinuationTests)
                self.assertEqual(stop_classes.STOP_REGISTRY[code]['next'],
                                 dict(kind='outcome', outcome='scientific_refused'))
                output = fixture.case.build_review_output('bar')
                output['value']['evidence'] = [dict(kind='source', link=dict(version_id=identifier,
                    source_id='saved-source', artifact=fixture.case.supporting,
                    locator=dict(kind='text', start=0, end=1, quote='A')))]
                fixture.invoke(output)
                plan = fixture.plan()
                with self.assertRaises(ResearchError) as caught:
                    fixture.execute(plan)
                self.assertEqual(caught.exception.code, code)
                records = fixture.case.read_records()
                self.assertEqual(records['review_output_refusal']['observed']['code'], code)
                next_plan = fixture.plan()
                self.assertNotEqual(next_plan, plan)
                self.assertEqual(next_plan['command'], 'review-run')
                response = fixture.case.build_provider_response(fixture.case.build_review_output('bar'))
                with patch.object(review_protocol.transport, 'send_request', return_value=response):
                    fixture.execute(next_plan, request='corrected-output')
                self.assertEqual(fixture.plan()['command'], 'value-review')
                fixture.execute(fixture.plan(), request='accepted-output')
                self.assertEqual(fixture.plan()['state'], 'done')
                self.assertEqual(fixture.case.read_records()['review_output_refusal']['observed'],
                                 records['review_output_refusal']['observed'])

    def test_fulltext_rows_acquire_another_capture_and_preserve_the_pending_one(self):
        from literature_fixtures import LiteratureCase
        from research_harness import acquisition
        from research_harness.bar_sources import _source
        for code in FULLTEXT_CODES:
            with self.subTest(code=code):
                case = self.prepare_case(LiteratureCase)
                identifier = case.metadata()
                from research_harness import fulltext
                raw = b'<article><body><p>A complete authored result.</p></body></article>'
                changes = {}
                if code == 'xml_depth_limit':
                    changes['JATS_MAX_DEPTH'] = 1
                elif code == 'xml_element_limit':
                    changes['JATS_MAX_ELEMENTS'] = 1
                elif code == 'xml_text_limit':
                    changes['JATS_MAX_TEXT_CHARACTERS'] = 1
                elif code == 'xml_internal_entity':
                    raw = b'<!DOCTYPE article [<!ENTITY x "saved text">]><article><body><p>&x;</p></body></article>'
                elif code == 'xml_external_entity':
                    raw = b'<article><body><p>&unprovided;</p></body></article>'
                http, _, _ = client([(200, {'Content-Type': 'application/xml'}, raw)])
                with ExitStack() as stack:
                    for name, value in changes.items():
                        stack.enter_context(patch.object(fulltext, name, value))
                    pending = acquisition.acquire_fulltext(case.store, identifier,
                        'https://arxiv.org/abs/2601.00001v1', expected_revision=case.store.revision,
                        request_id='pending', http=http, max_requests=1)
                self.assertEqual(pending['capture']['extraction_status'], code)
                request = dict(id='body', kind='source', version_id=identifier, depth='fulltext')
                before = _source(case.store.snapshot()['records'], case.artifacts, request, {})
                plan = self.take_handoff(code, 'fulltext')
                raw = b'<article><section class="article-body"><p>A complete authored result.</p></section></article>'
                http, wire, _ = client([(200, {'Content-Type': 'text/html'}, raw)])
                with patch.object(acquisition, 'HttpClient', return_value=http):
                    result = self.execute_handoff(case, plan,
                        dict(identifier=identifier, url='https://arxiv.org/html/2601.00001v1'))
                self.assertEqual(result['status'], 'complete')
                self.assertEqual(len(wire.requests), 1)
                captures = case.store.snapshot()['records']['work'][identifier]['fulltexts']
                self.assertEqual(captures[0], pending['capture'])
                after = _source(case.store.snapshot()['records'], case.artifacts, request, {})
                self.assertNotEqual(after, before)
                self.assertNotEqual(after['status'], 'unavailable')

    def test_missing_and_stale_loop_closures_accept_the_documented_command(self):
        for code in ('loop_closure_missing', 'loop_closure_stale'):
            with self.subTest(code=code):
                case = self.prepare_case(LineageCase)
                root = case.metadata()
                case.scope([root])
                for purpose in lineage.SEARCH_PURPOSES:
                    for suffix in ('first', 'second'):
                        case.mutate(literature.record_search, case.search(purpose, [], query=purpose + suffix))
                purposes = {p: dict(status='covered', note='Two saved queries returned no work.')
                            for p in lineage.SEARCH_PURPOSES}
                if code == 'loop_closure_stale':
                    case.mutate(lineage.record_loop_closure, dict(id='old', purposes=purposes))
                    case.mutate(literature.record_search, case.search('direct', [], query='first query'))
                before = case.store.snapshot()['records']
                self.assertIn(code, {row['code'] for row in lineage.loop_obligations(before, 'research')})
                plan = self.take_handoff(code, 'loop-close')
                self.execute_handoff(case, plan, dict(id='current', purposes=purposes))
                after = case.store.snapshot()['records']
                self.assertEqual(lineage.loop_obligations(after, 'research'), [])
                self.assertNotEqual(before, after)
                if code == 'loop_closure_stale':
                    self.assertEqual(after['loop_closure']['old'], before['loop_closure']['old'])

    def test_stale_search_rows_accept_a_current_judgment(self):
        from research_harness.principles import change_policy
        for code in ('search_dispositions_missing', 'search_evidence_stale',
                     'search_frontier_stale', 'search_scope_stale'):
            with self.subTest(code=code):
                case = self.prepare_case(LineageCase)
                root = case.metadata()
                case.scope([root])
                case.mutate(change_policy, dict(previous='lineage-v1', policy='exhaustive-v1',
                                               reason='Require complete captured searches.'))
                payload = case.search('direct', [])
                case.mutate(literature.record_search, payload)
                first = case.store.snapshot()['records']['literature_search'][payload['id']]
                if code == 'search_dispositions_missing':
                    legacy = copy.deepcopy(first)
                    legacy.pop('dispositions')
                    case.store.mutate('fixture.legacy-search', {},
                        lambda tx: tx.put('literature_search', first['id'], legacy),
                        expected_revision=case.store.revision, request_id='legacy-search')
                elif code == 'search_scope_stale':
                    case.scope([root, case.metadata(2)])
                elif code == 'search_evidence_stale':
                    case.capture(root)
                else:
                    case.metadata(2)
                    case.metadata(1, references=[dict(id='arxiv:2601.00002v1')])
                before = literature.foundation_report(case.store, 'research')
                self.assertIn(code, {row['code'] for row in before['obligations']})
                stored = copy.deepcopy(case.store.snapshot()['records']['literature_search'][first['id']])
                plan = self.take_handoff(code, 'search')
                payload = case.search('direct', [])
                self.execute_handoff(case, plan, payload)
                after = literature.foundation_report(case.store, 'research')
                self.assertNotIn(code, {row['code'] for row in after['obligations']})
                self.assertNotEqual(before, after)
                self.assertEqual(case.store.snapshot()['records']['literature_search'][first['id']], stored)

    def test_ambiguous_reference_accepts_a_bundle_resolution_without_rewriting_the_old_one(self):
        case = self.prepare_case(reference_fixtures.ReferenceFamilyTests)
        root, first = case.build_cited_bundle()
        case.mutate(literature.import_bundle, first)
        case.import_bytes('arxiv', case.build_arxiv_bytes(), 'later-identity')
        before = case.store.snapshot()['records']
        graph = literature.citation_graph(before, 'research')
        self.assertIn('reference_identity_ambiguous', {row['code'] for row in graph['obligations']})
        original = copy.deepcopy(before['reference_occurrence'])
        payload = copy.deepcopy(first)
        payload['id'] = 'resolved'
        payload['bibliography']['entries'][0]['target_family'] = reference_fixtures.DOI
        payload['resolutions'] = [dict(payload['bibliography']['entries'][0], reference_id=key)
                                  for key in sorted(original)]
        plan = self.take_handoff('reference_identity_ambiguous', 'bundle')
        self.execute_handoff(case, plan, payload)
        after = case.store.snapshot()['records']
        self.assertNotIn('reference_identity_ambiguous',
                         {row['code'] for row in literature.citation_graph(after, 'research')['obligations']})
        self.assertEqual({key: after['reference_occurrence'][key] for key in original}, original)
        self.assertEqual(after['source_bundle'][first['id']], before['source_bundle'][first['id']])

    def test_hot_journal_recovery_preserves_committed_history(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            store = Store(root, create=True)
            value = dict(title='Committed', text='a' * (4 * 1024 * 1024))
            store.mutate('fixture.commit', {}, lambda tx: tx.put('work', 'paper', value),
                         expected_revision=0, request_id='committed')
            before = store.snapshot()
            script = '''
import json, os, sqlite3, sys
from pathlib import Path
connection = sqlite3.connect(Path(sys.argv[1]) / '.exactory/research.sqlite3')
connection.execute('PRAGMA cache_size = 8')
connection.execute('PRAGMA cache_spill = 8')
connection.execute('BEGIN IMMEDIATE')
connection.execute('UPDATE records SET value = ?', (json.dumps({'text': 'b' * (4 * 1024 * 1024)}),))
os._exit(23)
'''
            crashed = subprocess.run([sys.executable, '-c', script, str(root)],
                                     capture_output=True, timeout=10)
            self.assertEqual(crashed.returncode, 23, crashed.stderr)
            with self.assertRaises(ResearchError) as caught:
                Store(root)
            self.assertEqual(caught.exception.code, 'store_recovery_required')
            plan = self.take_handoff(caught.exception.code, 'recover')
            args = cli.build_parser().parse_args([plan['command'], '--workspace', str(root)])
            self.assertTrue(cli.run(args)['recovered'])
            self.assertEqual(Store(root).snapshot(), before)

    def test_public_write_refusal_accepts_the_named_instruction_command(self):
        from research_harness.automatic_overrides import require_public_write_instruction
        from test_gate_override import StudyOverrideTests
        case = self.prepare_case(StudyOverrideTests)
        case.write_instruction()
        case.put('gate_override', 'automatic', dict(id='automatic', gate='submitted', authority='harness',
            stop_id='retained-stop', stop_code='review_series_no_progress'))
        (case.root / '.exactory/automatic-passes').mkdir(exist_ok=True)
        action = 'POST /api/v1/verifications'
        with self.assertRaises(ResearchError) as caught:
            require_public_write_instruction(case.store, 'submitted', public_action=action)
        self.assertEqual(caught.exception.code, 'gate_override_public_write')
        before = copy.deepcopy(case.store.snapshot()['records'])
        plan = self.take_handoff(caught.exception.code, 'gate-override')
        self.execute_handoff(case, plan, dict(id='authorized', gate='submitted',
            instruction='context/instruction.txt', reason='Continue this exact synthetic write.',
            public_action=action))
        require_public_write_instruction(case.store, 'submitted', public_action=action)
        after = case.store.snapshot()['records']
        self.assertEqual(after['gate_override']['automatic'], before['gate_override']['automatic'])
        self.assertIn('authorized', after['gate_override'])
