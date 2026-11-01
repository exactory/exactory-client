"""Replay only authenticated earlier result outputs when ordinary planning refuses."""

import copy
import gzip
import hashlib
import importlib
import json
import shutil
import tempfile
from pathlib import Path
from unittest.mock import patch

from problem_parts_fixtures import KEY, ResultPartsCase
from codex_route_fixtures import install_codex_runtime, limit_codex_input
from research_harness import research_decisions as decisions, review_problem_parts as parts
from research_harness import review_protocol as protocol, review_transport as transport
from research_harness import review_excerpts, review_context_repair, output_views
from research_harness.direction_sources import has_attachment
from research_harness.errors import ResearchError
from research_harness.evaluation import Evaluation
from research_harness.evidence import digest
from research_harness.artifacts import ArtifactStore
from research_harness.storage import Store


def canonical(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, allow_nan=False).encode()


class ProblemResultHistoryTests(ResultPartsCase):
    fixture_fields = ('input_limit', 'closed', 'current', 'series_evidence', 'owned', 'session',
                      'part_sizes', 'final_sizes', 'refusal_size', 'claude_respond')

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        directory = tempfile.TemporaryDirectory()
        cls.addClassCleanup(directory.cleanup)
        # Record every fixture once per class with the existing observed-route helpers; tests copy what they write.
        cls.record_fixtures(directory.name)
        for fixture in cls.fixtures.values():
            for turns in [row[0] for row in fixture['test'].get('closed', [])] + [fixture['test'].get('current', [])]:
                for assignment in turns:
                    packet = json.loads(ArtifactStore(fixture['root']).read(assignment['packet']))
                    if parts.find_stage(packet) != 'part':
                        raise AssertionError('Expected a recorded part: ' + assignment['id'])

    @classmethod
    def record_fixtures(cls, root):
        cls.fixture_root, cls.fixtures = Path(root), {}
        builder = cls()
        try:
            ResultPartsCase.setUp(builder)
            builder.save_fixture('bars')
        finally:
            builder.doCleanups()
        for name in ('two-series', 'three-series', 'short', 'objections', 'owned', 'ledger', 'legacy'):
            builder = cls()
            try:
                builder.setUp()
                if name == 'legacy':
                    builder.record_series((2, 2, 2), 300000, note_size=12000, limit=500000, final_size=14000)
                elif name == 'ledger':
                    builder.record_ledger_series()
                else:
                    if name == 'owned':
                        builder.owned = builder.case.artifacts.put(b'Owned computation body.' * 100, 'text/plain')
                        builder.case.expected['computations'] = [{'execution': {'id': 'owned-computation',
                            'outputs': [{'id': 'owned-output', 'artifact': builder.owned}]},
                            'assessment': {'reason': 'Checked this body.'}}]
                    builder.include_objections = name == 'objections'
                    counts = (2, 2, 2) if name in ('three-series', 'objections') else (2, 2)
                    builder.record_series(counts, note_size=(1, 2000) if name == 'short' else 2000)
                builder.save_fixture(name)
            finally:
                builder.doCleanups()
        builder = cls()
        try:
            builder.record_claude_bars()
            builder.record_series((2, 2, 3, 4), (700000, 600000, 450000, 400000), limit=1300000)
            builder.save_fixture('claude')
        finally:
            builder.doCleanups()

    def setUp(self):
        from test_research_problem_reviews import ProblemReviewTests
        self.case = ProblemReviewTests()
        self.addCleanup(self.case.doCleanups)
        self.case.setUp()
        install_codex_runtime(self)
        self.use_fixture('bars')

    def save_fixture(self, name):
        """Freeze a recorded workspace and its fixture metadata once for this class."""
        root = shutil.copytree(self.case.root, self.fixture_root / name)
        records = Store(root).snapshot()['records']
        self.fixtures[name] = {
            'root': root, 'revision': self.case.store.revision, 'records': records,
            'evaluation': Evaluation(records, ArtifactStore(root)),
            'case': {key: copy.deepcopy(getattr(self.case, key)) for key in
                     ('expected', 'sequence', 'statement', 'supporting', 'supporting_links')},
            'test': {key: copy.deepcopy(getattr(self, key)) for key in self.fixture_fields if hasattr(self, key)}}

    def use_fixture(self, name):
        """Give this test an independent writable copy of a class fixture."""
        fixture = self.fixtures[name]
        shutil.rmtree(self.case.root)
        shutil.copytree(fixture['root'], self.case.root)
        self.case.store, self.case.artifacts = Store(self.case.root), ArtifactStore(self.case.root)
        for key, value in fixture['case'].items():
            setattr(self.case, key, copy.deepcopy(value))
        for key in self.fixture_fields:
            if hasattr(self, key):
                delattr(self, key)
        for key, value in fixture['test'].items():
            setattr(self, key, copy.deepcopy(value))
        self.input_limit = getattr(self, 'input_limit', 105000)
        limit_codex_input(self, self.input_limit)
        self.fixture = fixture
        return self.current[-1] if hasattr(self, 'current') else None

    def record_series(self, counts=(2, 2), characters=50000, note_size=2000, limit=105000, final_size=2000):
        """Use the existing authenticated route and part-output fixtures."""
        self.oversized(count=0, limit=limit)
        self.input_limit = limit
        closed, current = [], []
        self.series_evidence = []
        for series, count in enumerate(counts, 1):
            self.case.expected['openness_searches'] = []
            for index in range(count):
                search = {'id': 's%d-%d' % (series, index), 'queries': ['bounded sequence'],
                          'reason': 'x' * (characters[series - 1] if isinstance(characters, tuple) else characters)}
                self.case.put('literature_search', search['id'], search)
                self.case.expected['openness_searches'].append({'search': search, 'sources': []})
            self.series_evidence.append(copy.deepcopy(self.case.expected))
            assignment = self.assign('series-%d' % series)
            turns = []
            for turn in range(count):
                self.assertEqual(self.packet(assignment)[KEY]['stage'], 'part')
                output = self.output(assignment)
                output['notes'][0]['note'] = 'n' * (note_size[turn] if isinstance(note_size, tuple) else note_size)
                if hasattr(self, 'part_sizes'):
                    output['notes'][0]['note'] = ''
                    output['notes'][0]['note'] = 'n' * (self.part_sizes[series - 1][turn] - len(json.dumps(output, sort_keys=True)))
                self.invoke(assignment, output)
                turns.append(assignment)
                if turn + 1 < count:
                    assignment = self.assign('series-%d-part-%d' % (series, turn + 2), assignment['id'])
            if series == len(counts):
                current = turns
            else:
                final = self.assign('series-%d-final' % series, assignment['id'])
                output = self.final_output()
                output['value']['reason'] = 'r' * final_size
                if getattr(self, 'include_objections', False):
                    reference = output['value']['evidence'][0]
                    if series == 1:
                        output['objections'] = [{'id': 'earlier-limitation', 'claim': 'The comparator needs a narrower hypothesis.',
                            'reason': 'This source leaves the hypothesis open.', 'evidence': [reference],
                            'resolution_condition': 'Check the hypothesis against the delivered source.'}]
                    else:
                        output['value']['objection_findings'] = [{'id': 'earlier-limitation', 'status': 'continuing',
                            'reason': 'The changed evidence still leaves this hypothesis open.', 'evidence': [reference]}]
                if hasattr(self, 'final_sizes'):
                    output['value']['reason'] = ''
                    output['value']['reason'] = 'r' * (self.final_sizes[series - 1] - len(json.dumps(output, sort_keys=True)))
                review = self.record_final(final, output)
                closed.append((turns, final, review))
        self.closed, self.current = closed, current
        return current[-1]

    def inputs(self, prior, records=None, packet=None, history=None, route=None):
        if records is None and self.case.store.revision == self.fixture['revision']:
            records, artifacts = self.fixture['records'], self.fixture['evaluation']
        else:
            records = self.case.read_records() if records is None else records
            artifacts = Evaluation(records, self.case.artifacts)
        state = protocol.assignment_state(records, artifacts, prior['id'])
        history = (transport.logical_messages(protocol.build_assignment_request(records, artifacts, prior)) +
                   [{'role': 'assistant', 'content': json.dumps(state['output'], sort_keys=True)}]
                   if history is None else history)
        packet = (protocol.build_packet(records, artifacts, None, 'result', 'first', prior_assignment_id=prior['id'])
                  if packet is None else packet)
        value = {'id': 'next-final', 'role': 'result', 'reviewer_id': 'first', 'author_id': 'author',
                 'route_id': 'route', 'dossier_id': None, 'subject': packet['subject'],
                 'context': {'prior_assignment_id': prior['id']}}
        route = records['review_route']['route'] if route is None else route
        prompt = protocol.canonical_prompt('result', orchestration='problem_creation', excerpts=True)
        return records, artifacts, value, route, packet, prompt, history, prior['id']

    def bound(self, inputs):
        return importlib.import_module('research_harness.review_problem_history').bound_history(*inputs)

    def pad_frame(self, characters):
        real = protocol.build_packet

        def build(*args, **kwargs):
            packet = real(*args, **kwargs)
            if packet['role'] == 'result':
                packet['fixture_frame'] = 'f' * characters
                packet.pop('digest', None)
                packet['digest'] = digest(packet)
            return packet

        return patch.object(protocol, 'build_packet', side_effect=build)

    def refusal_inputs(self, prior, inputs=None):
        inputs = list(self.inputs(prior) if inputs is None else inputs)
        planned = parts.prepare_packet(*inputs[:7])
        stage_prompt = protocol.canonical_prompt('result', orchestration='problem_creation', excerpts=True,
                                                  evidence_parts=parts.find_stage(planned))
        measured = dict(planned, fixture_frame='')
        minimal = [transport._elide_history_packet(m) for m in inputs[6]]
        size = parts._measure(inputs[3], transport.request_body(inputs[3], stage_prompt['text'], measured, minimal))
        self.frame_size = getattr(self, 'refusal_size', self.input_limit + 2000) - size
        inputs[4] = dict(inputs[4], fixture_frame='f' * self.frame_size)
        inputs[4].pop('digest')
        inputs[4]['digest'] = digest(inputs[4])
        self.assert_code('review_input_too_large', lambda: parts.prepare_packet(*inputs[:7]))
        return inputs

    def replacements(self, history):
        return [json.loads(message['content']) for message in history[1::2]
                if json.loads(message['content']).get('delivery') == 'output-elided-from-replayed-history']

    def test_final_fits_after_history_compaction(self):
        prior = self.use_fixture('two-series')
        self.refusal_inputs(prior)
        with self.pad_frame(self.frame_size):
            final = self.assign('compacted-final', prior['id'])
        self.assertEqual((self.packet(final)[KEY]['stage'], self.packet(final)[KEY]['turn']), ('final', 3))
        self.assertEqual(len(self.replacements(final['history'])), 2)
        self.assertLessEqual(parts._measure(self.case.read_records()['review_route']['route'], self.request(final)), self.input_limit)

    def plan(self, inputs, history=None):
        planned = parts.prepare_packet(*inputs[:6], inputs[6] if history is None else history)
        stage = parts.find_stage(planned)
        prompt = (protocol.canonical_prompt('result', orchestration='problem_creation', excerpts=True,
                                            evidence_parts=stage) if stage else inputs[5])
        return planned, prompt, transport.request_body(inputs[3], prompt['text'], planned,
                                                       inputs[6] if history is None else history)

    def test_recorded_06918_request_rebuilds_byte_for_byte(self):
        prior = self.use_fixture('legacy')
        old = self.closed[-1][1]
        records = self.case.read_records()
        request = self.request(old)
        fixture = Path(__file__).with_name('fixtures') / 'problem_result_history_06918.json.gz'
        # Generated with the unchanged code in an independent git archive of 40690d5.
        frozen = json.loads(gzip.decompress(fixture.read_bytes()))
        self.assertEqual(frozen['source_commit'], '40690d5')
        expected = frozen['canonical_request'].encode()
        self.assertEqual(canonical(request), expected)
        adapter_bytes = frozen['adapter_input'].encode()
        request_digest = frozen['request_digest']
        attempt = copy.deepcopy(records['review_attempt']['attempt-' + old['id']])
        invocation = copy.deepcopy(records['review_invocation'][attempt['invocation_id']])
        self.refusal_inputs(prior)
        with self.pad_frame(self.frame_size):
            child = self.assign('compacted-child', prior['id'])
        self.assertTrue(self.replacements(child['history']))
        fresh = self.case.read_records()
        evaluation = Evaluation(fresh, self.case.artifacts)
        rebuilt = protocol.build_assignment_request(fresh, evaluation, old)
        self.assertEqual(canonical(rebuilt), expected)
        self.assertEqual(transport._local_adapter(fresh['review_route']['route']).packet_text(rebuilt).encode(), adapter_bytes)
        self.assertEqual(digest(rebuilt), request_digest)
        self.assertEqual(fresh['review_attempt'][attempt['id']], attempt)
        self.assertEqual(fresh['review_invocation'][invocation['id']], invocation)
        self.assertTrue(protocol.authenticate_attempt(fresh, evaluation, old, attempt))
        self.assertTrue(protocol.assignment_state(fresh, evaluation, old['id'])['ready'])

    def test_fitting_ordinary_request_is_unchanged(self):
        prior = self.use_fixture('two-series')
        # An evidence change can restart with an ordinary-sized packet after a non-parts parent.
        final = self.assign('ordinary-parent', prior['id'])
        self.record_final(final)
        self.case.expected['value']['motivation'][0]['text'] += ' refreshed'
        inputs = list(self.inputs(final))
        inputs[2]['context'] = {}
        inputs[4] = protocol.build_packet(inputs[0], inputs[1], None, 'result', 'first')
        inputs[4]['historical_reviews'] = [protocol.deliver_review(inputs[0], row['payload'])
                                          for row in inputs[0]['value_review'].values() if row['payload']['stage'] == 'result']
        inputs[4]['problem_evidence']['openness_searches'] = []
        expected = self.plan(inputs)
        self.assertIsNone(parts.find_stage(expected[0]))
        original = copy.deepcopy(inputs[6])
        reduced = self.bound(inputs)
        self.assertIs(reduced, inputs[6])
        self.assertEqual(reduced, original)
        self.assertEqual(tuple(canonical(x) for x in self.plan(inputs, reduced)),
                         tuple(canonical(x) for x in expected))
        self.assertEqual(self.replacements(reduced), [])

    def test_fitting_part_and_final_plans_are_unchanged(self):
        prior = self.use_fixture('two-series')
        for parent, stage in ((self.current[0], 'part'), (prior, 'final')):
            with self.subTest(stage=stage):
                inputs = self.inputs(parent)
                expected = self.plan(inputs)
                self.assertEqual(parts.find_stage(expected[0]), stage)
                reduced = self.bound(inputs)
                self.assertIs(reduced, inputs[6])
                self.assertEqual(tuple(canonical(x) for x in self.plan(inputs, reduced)),
                                 tuple(canonical(x) for x in expected))
                sent = transport.logical_messages(expected[2])[:-1]
                self.assertNotEqual(sent[::2], inputs[6][::2], 'Fixture must exercise existing user reductions')
                self.assertEqual(self.replacements(reduced), [])

    def test_refresh_and_midseries_continuation_use_correct_boundaries(self):
        prior = self.use_fixture('two-series')
        final = self.closed[0][1]
        for same_digest in (False, True):
            with self.subTest(same_digest=same_digest):
                refreshed = list(self.inputs(final))
                self.assertIn('problem_refresh', refreshed[4])
                if same_digest:
                    refreshed[4]['problem_evidence'] = copy.deepcopy(self.series_evidence[0])
                    refreshed[4]['problem_evidence_digest'] = self.packet(final)['problem_evidence_digest']
                refused = self.refusal_inputs(final, refreshed)
                reduced = self.bound(refused)
                self.assertEqual([p['assignment_id'] for p in self.replacements(reduced)],
                                 [p['id'] for p in self.closed[0][0]])
                planned = self.plan(refused, reduced)[0]
                self.assertEqual((planned[KEY]['series_assignment_id'], planned[KEY]['turn']), ('next-final', 1))
        continued = self.refusal_inputs(prior)
        self.assertIn('problem_refresh', continued[4])
        reduced = self.bound(continued)
        planned = self.plan(continued, reduced)[0]
        self.assertEqual((planned[KEY]['series_assignment_id'], planned[KEY]['turn']), ('series-2', 3))
        self.assertEqual(reduced[-4:], continued[6][-4:])

    def test_only_authenticated_closed_series_are_compacted(self):
        prior = self.use_fixture('three-series')
        inputs = self.refusal_inputs(prior)
        valid = self.bound(inputs)
        self.assertTrue(self.replacements(valid))
        old_part, old_final, old_review = self.closed[0]
        second_ids = {row['id'] for row in self.closed[1][0]}
        for defect in ('abandoned', 'unrecorded', 'refused', 'imported', 'other-slot', 'other-subject',
                       'other-reviewer', 'other-author', 'other-route', 'other-session', 'review-output-mismatch'):
            with self.subTest(defect=defect):
                changed = copy.deepcopy(inputs[0])
                if defect in ('abandoned', 'unrecorded'):
                    del changed['value_review'][old_review['id']]
                elif defect == 'refused':
                    attempt = changed['review_attempt']['attempt-' + old_final['id']]
                    attempt['status'] = 'failed'
                elif defect == 'imported':
                    changed['review_attempt']['attempt-' + old_final['id']]['origin'] = 'imported'
                elif defect == 'review-output-mismatch':
                    changed['value_review'][old_review['id']]['output_digest'] = '0' * 64
                else:
                    field = {'other-slot': 'slot_id', 'other-subject': 'subject', 'other-reviewer': 'reviewer_id',
                             'other-author': 'author_id', 'other-route': 'route_id', 'other-session': 'session_id'}[defect]
                    changed['review_assignment'][old_part[0]['id']][field] = (
                        {'kind': 'problem', 'statement_id': 'elsewhere'} if field == 'subject' else 'elsewhere')
                amended = [changed, Evaluation(changed, self.case.artifacts), *inputs[2:]]
                try:
                    reduced = self.bound(amended)
                except ResearchError as error:
                    self.assertEqual(error.code, 'review_input_too_large')
                else:
                    replaced = {p['assignment_id'] for p in self.replacements(reduced)}
                    self.assertTrue(replaced <= second_ids | {self.closed[1][1]['id']})
                    self.assertFalse(replaced & {p['id'] for p in old_part})
        self.assertFalse(any(p['assignment_id'] in {row['id'] for row in self.current}
                             for p in self.replacements(valid)))

    def assert_invalid_history_refuses(self, position, content):
        from research_harness import review_problem_history as history_module
        prior = self.use_fixture('two-series')
        inputs = self.refusal_inputs(prior)
        refusal = self.assert_code('review_input_too_large', lambda: parts.prepare_packet(*inputs[:7]))
        before = self.case.read_records()
        revision = self.case.store.revision
        real = history_module.bound_history

        def supply_invalid_history(*args):
            amended = list(args)
            amended[6] = copy.deepcopy(args[6])
            amended[6][position]['content'] = content
            return real(*amended)

        # The real planner above supplied this refusal; isolate the mapper from transport format checks.
        with patch.object(history_module, 'bound_history', side_effect=supply_invalid_history), \
                patch.object(parts, 'prepare_packet', side_effect=refusal):
            with self.assertRaises(ResearchError) as caught:
                self.assign('invalid-history', prior['id'])
        self.assertIs(caught.exception, refusal)
        self.assertEqual(self.case.store.revision, revision)
        after = self.case.read_records()
        for kind in ('review_assignment', 'review_invocation', 'review_attempt'):
            self.assertEqual(after[kind], before[kind])

    def test_non_string_history_content_refuses_without_records(self):
        self.assert_invalid_history_refuses(0, None)

    def test_assistant_json_list_refuses_without_records(self):
        self.assert_invalid_history_refuses(1, '[]')

    def test_mapper_rejects_invalid_shapes(self):
        from research_harness import review_problem_history as history_module
        prior = self.use_fixture('two-series')
        inputs = self.inputs(prior)
        for defect in ('message-list', 'missing-content', 'assistant-non-string', 'missing-label', 'user-json',
                       'user-list', 'assistant-json', 'assistant-list', 'frozen-json', 'frozen-list',
                       'missing-plan', 'plan-list', 'incomplete-plan'):
            with self.subTest(defect=defect):
                records, history = copy.deepcopy(inputs[0]), copy.deepcopy(inputs[6])
                if defect == 'message-list':
                    history[0] = []
                elif defect == 'missing-content':
                    del history[0]['content']
                elif defect == 'assistant-non-string':
                    history[1]['content'] = 42
                elif defect == 'missing-label':
                    history[0]['content'] = '{}'
                elif defect == 'user-json':
                    history[0]['content'] = transport.PACKET_LABEL + '{'
                elif defect == 'user-list':
                    history[0]['content'] = transport.PACKET_LABEL + '[]'
                elif defect == 'assistant-json':
                    history[1]['content'] = '{'
                elif defect == 'assistant-list':
                    history[1]['content'] = '[]'
                else:
                    assignment = records['review_assignment'][self.closed[0][0][0]['id']]
                    packet = self.packet(assignment)
                    if defect == 'missing-plan':
                        del packet[KEY]
                    elif defect == 'plan-list':
                        packet[KEY] = ['part']
                    elif defect == 'incomplete-plan':
                        packet[KEY] = {'protocol': parts.PROTOCOL, 'stage': 'part'}
                    data = ('{' if defect == 'frozen-json' else '[]' if defect == 'frozen-list'
                            else json.dumps(packet))
                    assignment['packet'] = self.case.artifacts.put(data.encode(), 'application/json')
                chain = history_module._walk_ancestors(records, prior['id'])
                mapped = history_module._map_history(records, Evaluation(records, self.case.artifacts), chain, history)
                self.assertTrue(mapped is None, defect)

    def test_history_mapping_uses_lineage_not_output_text(self):
        prior = self.use_fixture('two-series')
        inputs = self.refusal_inputs(prior)
        # Every part output is identical; positions and the elided packet digests identify them.
        outputs = [protocol.assignment_state(inputs[0], inputs[1], row['id'])['output']
                   for row in (self.closed[0][0][0], self.current[0])]
        self.assertEqual(outputs[0], outputs[1])
        inputs[6] = [transport._elide_history_packet(m) for m in inputs[6]]
        reduced = self.bound(inputs)
        self.assertEqual([p['assignment_id'] for p in self.replacements(reduced)],
                         [row['id'] for row in self.closed[0][0]])
        for defect in ('missing', 'cycle', 'user-digest', 'count', 'role', 'assistant-content'):
            with self.subTest(defect=defect):
                altered = copy.deepcopy(inputs[0])
                history = copy.deepcopy(inputs[6])
                if defect == 'missing':
                    del altered['review_assignment'][self.closed[0][0][0]['id']]
                elif defect == 'cycle':
                    altered['review_assignment']['bar-first']['history_assignment_id'] = prior['id']
                elif defect == 'user-digest':
                    history[0] = dict(history[0], content=transport.PACKET_LABEL + json.dumps({'packet_digest': '0' * 64}))
                elif defect == 'count':
                    altered['review_assignment'][prior['id']]['history'] = []
                elif defect == 'role':
                    history[1]['role'] = 'user'
                else:
                    history[1]['content'] += ' '
                amended = [altered, Evaluation(altered, self.case.artifacts), *inputs[2:6], history, inputs[7]]
                with self.assertRaises(ResearchError) as baseline:
                    parts.prepare_packet(*amended[:7])
                self.assert_code(baseline.exception.code, lambda: self.bound(amended))

    def test_latest_review_and_current_series_remain_verbatim(self):
        prior = self.use_fixture('three-series')
        final = self.assign('source-request-final', prior['id'])
        output = self.final_output()
        output['value']['status'] = 'unresolved'
        output['source_requests'] = [{'id': 'read-source', 'kind': 'source', 'depth': 'fulltext',
                                      'version_id': next(iter(self.case.supporting_links.values()))['version_id']}]
        self.invoke(final, output)
        inputs = self.refusal_inputs(final)
        reduced = self.bound(inputs)
        protected = {'bar-first', self.closed[-1][1]['id'], final['id'], *(row['id'] for row in self.current)}
        chain = []
        current = final
        while current is not None:
            chain.append(current)
            current = inputs[0]['review_assignment'].get(current.get('history_assignment_id'))
        for index, row in enumerate(reversed(chain)):
            self.assertEqual(reduced[2 * index], inputs[6][2 * index])
            if row['id'] in protected:
                self.assertEqual(reduced[2 * index + 1], inputs[6][2 * index + 1])

    def test_old_final_requires_delivered_historical_review(self):
        prior = self.use_fixture('objections')
        inputs = self.refusal_inputs(prior)
        old_review = self.closed[0][2]
        before = copy.deepcopy(inputs[4])
        self.assertTrue(before['historical_reviews'][0]['objections'])
        self.assertTrue(before['historical_reviews'][1]['value']['objection_findings'])
        self.assertEqual(before['historical_reviews'][0]['objections'][0]['id'], 'earlier-limitation')
        exact = self.bound(inputs)
        old_final = self.closed[0][1]['id']
        self.assertIn(old_final, [p['assignment_id'] for p in self.replacements(exact)])
        self.assertEqual(inputs[4], before)
        for defect in ('removed', 'altered'):
            with self.subTest(defect=defect):
                packet = copy.deepcopy(inputs[4])
                if defect == 'removed':
                    packet['historical_reviews'] = [r for r in packet['historical_reviews'] if r['id'] != old_review['id']]
                else:
                    next(r for r in packet['historical_reviews'] if r['id'] == old_review['id'])['limitations'] = []
                reduced = self.bound([*inputs[:4], packet, *inputs[5:]])
                replaced = {p['assignment_id'] for p in self.replacements(reduced)}
                self.assertNotIn(old_final, replaced)
                self.assertFalse(replaced & {row['id'] for row in self.closed[0][0]})
                self.assertEqual(reduced[3], inputs[6][3])
                self.assertEqual(reduced[5], inputs[6][5])
                self.assertEqual(packet['material_objections'], before['material_objections'])

    def record_ledger_series(self):
        self.case.add_reading_and_computation()
        version = self.case.expected['readings'][0]['version_id']
        records = self.case.read_records()
        fulltext = review_excerpts.find_fulltext(records, version)
        phrase = self.case.artifacts.read(fulltext).decode()[:40]
        allowances = review_excerpts.create_allowances()
        allowances['excerpts']['used'] = 199999 - len(phrase)
        allowances['passages']['used'] = 99999
        excerpt = {'version_id': version, 'text': phrase, 'purpose': 'Preserve the exact checked source span.'}
        ledger, allowances = review_excerpts.check_excerpts(records, self.case.artifacts, [excerpt,
            dict(excerpt, text='This sentence does not occur anywhere in the source.'), excerpt], allowances)
        self.assertEqual([entry['status'] for entry in ledger], ['verified', 'unmatched', 'over_allowance'])
        session = {'protocol': review_excerpts.PROTOCOL, 'allowances': allowances, 'excerpts': ledger}
        real = protocol.build_packet

        def seed_session(*args, **kwargs):
            packet = real(*args, **kwargs)
            if packet['role'] == 'result' and 'prior_assignment_id' not in kwargs:
                packet[review_excerpts.KEY] = copy.deepcopy(session)
            return packet

        with patch.object(protocol, 'build_packet', side_effect=seed_session):
            self.record_series(counts=(2, 2, 2), characters=120000, note_size=4000, limit=220000, final_size=4000)
        self.session = session

    def test_excerpt_ledger_and_reading_allowances_are_unchanged(self):
        from research_harness import bar_sources
        prior = self.use_fixture('ledger')
        version = self.case.expected['readings'][0]['version_id']
        records = self.case.read_records()
        fulltext = review_excerpts.find_fulltext(records, version)
        phrase = self.case.artifacts.read(fulltext).decode()[:40]
        session = self.session
        allowances, ledger = session['allowances'], session['excerpts']
        self.assertEqual([entry['status'] for entry in ledger], ['verified', 'unmatched', 'over_allowance'])
        for assignment in [row[1] for row in self.closed] + self.current:
            self.assertEqual(self.packet(assignment)[review_excerpts.KEY], session)
        final = self.assign('ledger-source-final', prior['id'])
        request = {'id': 'read-fulltext', 'kind': 'source', 'depth': 'fulltext', 'version_id': version}
        output = self.final_output()
        output['value']['status'] = 'unresolved'
        output['source_requests'] = [request]
        self.invoke(final, output)
        reply = self.assign('ledger-source-reply', final['id'])
        self.assertEqual(self.packet(reply)['source_deliveries'][0]['status'], 'delivered')
        self.assertEqual(self.packet(reply)['pending_source_requests'], [])
        output['source_requests'] = [dict(request, id='read-again'), {'id': 'passage-after-limit', 'kind': 'passage',
            'version_id': version, 'anchor': phrase, 'characters': 40}]
        self.invoke(reply, output)
        inputs = self.refusal_inputs(reply)
        expected = copy.deepcopy(inputs[4])
        self.assertEqual([entry['status'] for entry in expected['source_deliveries']],
                         ['already_delivered', 'over_allowance'])
        with self.pad_frame(self.frame_size):
            compacted = self.assign('ledger-compacted-reply', reply['id'])
        self.assertTrue(self.replacements(compacted['history']))
        packet = self.packet(compacted)
        for key in (review_excerpts.KEY, 'source_deliveries', 'pending_source_requests'):
            self.assertEqual(packet[key], expected[key])
        self.assertEqual(packet[review_excerpts.KEY], session)
        self.assertEqual(ledger[0]['text'], phrase)
        self.assertEqual(allowances['excerpts'], {'characters': 200000, 'used': 199999})
        self.assertEqual(allowances['passages'], {'characters': 100000, 'used': 99999})
        self.record_final(compacted)
        self.case.expected['value']['motivation'][0]['text'] += ' refreshed'
        refreshed = self.assign('ledger-refreshed')
        self.assertEqual(self.packet(refreshed)[review_excerpts.KEY], session)
        records = self.case.read_records()
        evaluation = Evaluation(records, self.case.artifacts)
        bodies = protocol._find_session_bodies(records, evaluation, refreshed)
        self.assertTrue(bodies[fulltext['sha256']]['is_complete_delivery'])
        self.assertEqual(bodies[fulltext['sha256']]['assignment_id'], reply['id'])
        repeated = bar_sources.continuation(records, evaluation, self.packet(refreshed), output, bodies)
        self.assertEqual([entry['status'] for entry in repeated['source_deliveries']],
                         ['already_delivered', 'over_allowance'])
        self.assertEqual(repeated['pending_source_requests'], [])
        self.assertEqual(repeated[review_excerpts.KEY], session)
        self.assertTrue(all(not entry['reading_credit'] for entry in repeated['source_deliveries']))
        self.assertEqual(review_excerpts.TURN_FULLTEXT_CHARACTERS, 120000)

    def test_placeholder_is_deterministic_shorter_and_idempotent(self):
        prior = self.use_fixture('three-series')
        inputs = self.refusal_inputs(prior)
        reduced = self.bound(inputs)
        module = importlib.import_module('research_harness.review_problem_history')
        for old, new in zip(inputs[6], reduced):
            if old != new:
                self.assertLess(len(new['content']), len(old['content']))
                placeholder = json.loads(new['content'])
                self.assertEqual(placeholder['note'], module.PART_OUTPUT_NOTE if placeholder['evidence_stage'] == 'part'
                                 else module.FINAL_OUTPUT_NOTE)
                self.assertEqual(new['content'], json.dumps(placeholder, sort_keys=True, ensure_ascii=True, allow_nan=False))
        shuffled = {kind: dict(reversed(list(rows.items()))) for kind, rows in reversed(list(inputs[0].items()))}
        self.assertEqual(self.bound([shuffled, Evaluation(shuffled, self.case.artifacts), *inputs[2:]]), reduced)
        # Force another sweep so inherited placeholders must be recognized, rather than bypassed on fit.
        huge_packet = dict(inputs[4], fixture_frame='f' * 700000)
        inherited = copy.deepcopy(reduced)
        self.assert_code('review_input_too_large', lambda: self.bound(
            [*inputs[:4], huge_packet, inputs[5], reduced, inputs[7]]))
        self.assertEqual(reduced, inherited)
        # A genuine short output is never enlarged by a placeholder.
        short = self.build_short_series_inputs()
        kept = self.bound(short)
        for old, new in zip(short[6], kept):
            self.assertLessEqual(len(new['content']), len(old['content']))
        self.assertEqual(kept[3], short[6][3])

    def build_short_series_inputs(self):
        # Use a separate existing fixture so short output is itself authenticated.
        helper = ProblemResultHistoryTests()
        helper.setUp()
        self.addCleanup(helper.doCleanups)
        prior = helper.use_fixture('short')
        inputs = helper.refusal_inputs(prior)
        # One long part can shrink, while the authenticated one-character note stays verbatim.
        return inputs

    def test_retry_rebuild_and_views_preserve_original_outputs(self):
        prior = self.use_fixture('three-series')
        self.refusal_inputs(prior)
        payload = {'id': 'frozen-final', 'role': 'result', 'reviewer_id': 'first', 'author_id': 'author',
                   'route_id': 'route', 'dossier_id': None, 'context': {'prior_assignment_id': prior['id']}}
        revision = self.case.store.revision
        with self.pad_frame(self.frame_size):
            receipt = protocol.record_assignment(self.case.store, payload, expected_revision=revision, request_id='freeze-history')
        final = receipt['result']
        request = canonical(self.request(final))
        bad = self.final_output()
        bad['value']['status'] = 'invalid-status'
        self.invoke(final, bad)
        with self.assertRaises(ResearchError):
            self.case.mutate(decisions.record_value_review, dict(bad, id='refused-review',
                dossier_id=None, assignment_id=final['id']))
        self.assertIn('attempt-' + final['id'], self.case.read_records()['review_output_refusal'])
        review_output = self.final_output()
        self.invoke(final, review_output, attempt='valid-retry')
        review = self.case.mutate(decisions.record_value_review, dict(review_output, id='review-frozen-final',
                                     dossier_id=None, assignment_id=final['id']))['result']
        self.assertEqual(canonical(self.request(final)), request)
        replay = protocol.record_assignment(self.case.store, payload, expected_revision=revision, request_id='freeze-history')
        self.assertEqual(replay, receipt)
        self.assertEqual(self.coverage(review), [])
        self.case.expected['value']['motivation'][0]['text'] += ' refreshed'
        child = self.assign('inherited-final')
        inherited = {json.loads(m['content'])['assignment_id']: m['content'] for m in final['history'][1::2]
                     if json.loads(m['content']).get('delivery') == 'output-elided-from-replayed-history'}
        self.assertEqual({json.loads(m['content'])['assignment_id']: m['content'] for m in child['history'][1::2]
                          if json.loads(m['content']).get('delivery') == 'output-elided-from-replayed-history'}, inherited)
        # A successful later fallback must recognize and freeze the old placeholders while omitting new outputs.
        while True:
            self.invoke(child)
            if not any(self.packet(child)[KEY]['remaining'].values()):
                break
            child = self.assign('later-part-2', child['id'])
        self.refusal_inputs(child)
        with self.pad_frame(self.frame_size):
            compacted_again = self.assign('second-compacted-final', child['id'])
        repeated = {json.loads(m['content'])['assignment_id']: m['content'] for m in compacted_again['history'][1::2]
                    if json.loads(m['content']).get('delivery') == 'output-elided-from-replayed-history'}
        self.assertGreater(len(repeated), len(inherited))
        for identifier, content in inherited.items():
            self.assertEqual(repeated[identifier], content)
        old_final = self.closed[0][1]['id']
        self.assertEqual(json.loads(repeated[old_final])['later_review_id'], self.closed[1][2]['id'])
        self.assertNotEqual(json.loads(repeated[old_final])['later_review_id'], review['id'])
        self.assertEqual(canonical(self.request(final)), request)
        records = self.case.read_records()
        evaluation = Evaluation(records, self.case.artifacts)
        for assignment in (*self.closed[0][0], self.closed[0][1], *self.current, final):
            state = protocol.assignment_state(records, evaluation, assignment['id'])
            self.assertTrue(state['ready'], state['obligations'])
        files = output_views._build_series(records, evaluation, output_views._ViewInputs({}, {}))
        old_attempt = records['review_attempt']['attempt-' + self.closed[0][0][0]['id']]
        original_bytes = self.case.artifacts.read(old_attempt['output'])
        self.assertTrue(any(file.content == original_bytes for file in files.values()))

    def test_compaction_preserves_coverage_and_refusal_rules(self):
        prior = self.use_fixture('owned')
        owned = self.owned
        self.refusal_inputs(prior)
        with self.pad_frame(self.frame_size):
            final = self.assign('coverage-final', prior['id'])
        review = self.record_final(final)
        self.assertEqual(self.coverage(review), [])
        records = self.case.read_records()
        for defect in ('request', 'body', 'receipt', 'part-output'):
            with self.subTest(defect=defect):
                changed = copy.deepcopy(records)
                part = self.current[-1]
                if defect == 'request':
                    changed['review_assignment'][part['id']]['history'][0]['content'] += ' tampered'
                elif defect == 'part-output':
                    output = dict(self.output(part), notes=[])
                    changed['review_attempt']['attempt-' + part['id']]['output'] = protocol._put_json(self.case.artifacts, output)
                else:
                    target = part if defect == 'body' else final
                    request = json.loads(self.case.artifacts.read(changed['review_attempt']['attempt-' + target['id']]['request']))
                    packet = review_excerpts.read_sent_packet(request)
                    if defect == 'body':
                        self.assertTrue(has_attachment(self.case.artifacts, packet, owned))
                        entry = next(e for e in packet['evidence'] if e['artifact'] == owned)
                        entry.update(content='', encoding='omitted')
                    else:
                        packet[KEY]['delivered_before'][0]['attempt_id'] = 'unobserved'
                    request['input'][-1]['content'] = transport.PACKET_LABEL + json.dumps(packet, sort_keys=True)
                    changed['review_attempt']['attempt-' + target['id']]['request'] = protocol._put_json(self.case.artifacts, request)
                evaluation = Evaluation(changed, self.case.artifacts)
                self.assertFalse(protocol.assignment_state(changed, evaluation, final['id'])['ready'])
                self.assertTrue(decisions._assess_problem_coverage(
                    changed, evaluation, [review], self.case.expected, digest(self.case.expected)))

    def test_still_unfittable_history_records_nothing(self):
        prior = self.use_fixture('two-series')
        before = self.case.read_records()
        revision = self.case.store.revision
        with self.pad_frame(700000):
            self.assert_code('review_input_too_large', lambda: self.assign('too-large', prior['id']))
        self.assertEqual(self.case.store.revision, revision)
        after = self.case.read_records()
        for kind in ('review_assignment', 'review_invocation', 'review_attempt'):
            self.assertEqual(after[kind], before[kind])

    def test_other_errors_and_excluded_paths_are_unchanged(self):
        prior = self.use_fixture('two-series')
        inputs = self.refusal_inputs(prior)
        refusal = ResearchError('different_error', 'Retain the original planner error.')
        with patch.object(parts, 'prepare_packet', side_effect=refusal):
            with self.assertRaises(ResearchError) as caught:
                self.bound(inputs)
            self.assertIs(caught.exception, refusal)
        cases = [(2, {'role': 'bar'}), (3, {'adapter': 'codex_cli_visual_v1'}),
                 (3, {'adapter': 'openai_responses_v1'}), (4, {'subject': {'kind': 'dossier'}}),
                 (4, {'context_repair': {}}), (4, {'complete_source_delivery': {}}),
                 (4, {'problem_evidence': {'delivery': 'delivered-in-an-earlier-turn'}})]
        for position, change in cases:
            with self.subTest(change=change):
                altered = list(inputs)
                altered[position] = dict(altered[position], **change)
                with patch.object(parts, 'prepare_packet', side_effect=AssertionError('Excluded path probed')):
                    self.assertIs(self.bound(altered), altered[6])
        explicit = list(inputs)
        explicit[2] = dict(inputs[2], context={'source_delivery': 'complete-visible-v1'})
        self.assertIs(self.bound(explicit), inputs[6])
        missing = list(inputs)
        missing[4] = {k: v for k, v in inputs[4].items() if k != 'problem_evidence'}
        self.assertIs(self.bound(missing), inputs[6])
        self.assertIs(self.bound([*inputs[:7], None]), inputs[6])

    def test_fallback_reuses_the_callers_evaluation(self):
        prior = self.use_fixture('two-series')
        inputs = self.refusal_inputs(prior)
        roots = []
        real = Evaluation.__init__

        def initialize(evaluation, records, artifacts, **kwargs):
            if kwargs.get('_owner') is None:
                roots.append(records)
            real(evaluation, records, artifacts, **kwargs)

        with patch.object(Evaluation, '__init__', initialize):
            reduced = self.bound(inputs)
        self.assertTrue(self.replacements(reduced))
        self.assertEqual(roots, [])

    def test_route_pinned_files_match_06918(self):
        root = Path(__file__).resolve().parents[1]
        # Frozen from git 40690d5, independently of the working tree modules.
        expected = PINNED_SHA256
        for relative, sha in expected.items():
            self.assertEqual(hashlib.sha256((root / relative).read_bytes()).hexdigest(), sha, relative)
        records = self.case.read_records()
        self.assertEqual(protocol.route_state(records, self.case.artifacts, 'route')['context_status'], 'verified_for_route')



    def test_missing_required_body_can_trigger_fallback_below_limit(self):
        prior = self.use_fixture('two-series')
        inputs = list(self.inputs(prior))
        body = self.case.artifacts.put(b'Required final objection body.' * 350, 'text/plain')
        inputs[1] = Evaluation(inputs[0], self.case.artifacts)
        packet = inputs[4]
        packet['material_objections'] = [{'id': 'required-final-body', 'claim': 'Check this material limitation.',
            'material_version': '1' * 64, 'version_evidence': [body], 'version_source_mappings': []}]
        packet['fixture_frame'] = ''
        planned, prompt, _ = self.plan(inputs)
        minimal = [transport._elide_history_packet(m) for m in inputs[6]]
        size = parts._measure(inputs[3], transport.request_body(inputs[3], prompt['text'], planned, minimal, order=()))
        packet['fixture_frame'] = 'f' * (self.input_limit + 1000 - size)
        packet.pop('digest')
        packet['digest'] = digest(packet)
        refusal = self.assert_code('review_input_too_large', lambda: parts.prepare_packet(*inputs[:7]))
        self.assertLessEqual(refusal.details['input_characters'], self.input_limit)
        reduced = self.bound(inputs)
        planned, _, request = self.plan(inputs, reduced)
        self.assertEqual(parts.find_stage(planned), 'final')
        self.assertTrue(has_attachment(inputs[1], review_excerpts.read_sent_packet(request), body))
        self.assertLessEqual(parts._measure(inputs[3], request), self.input_limit)

    def record_claude_bars(self):
        from claude_review_fixtures import build_recorded_response, RECORDED_CONTEXTS
        from research_harness import review_claude_transport as claude
        from test_research_problem_reviews import ProblemReviewTests
        self.case = ProblemReviewTests()
        self.case.setUp()
        self.addCleanup(self.case.doCleanups)
        case = self.case
        case.supporting = case.artifacts.put(b'A captured field standard.', 'text/plain')
        case.mutate(protocol.record_route, {'id': 'route', 'adapter': 'claude_cli_v1', 'model': 'fixture-model',
            'endpoint': 'claude://local', 'configuration': {'effort': 'xhigh', 'max_output_tokens': 20000, 'timeout_seconds': 5}})

        def respond(route, request, output):
            response = build_recorded_response(route, request, output, RECORDED_CONTEXTS[0])
            response['context_evidence']['runtime'] = claude.runtime()
            return response

        self.claude_respond = respond

        def probe(route, request, credential):
            packet = review_excerpts.read_sent_packet(request)
            return respond(route, request, {key: packet[key] for key in ('allowed_control', 'evidence_control')} |
                           {'excluded_controls': []})

        with patch.object(transport, 'send_request', side_effect=probe):
            case.mutate(protocol.probe_route, {'id': 'probe', 'route_id': 'route'}, credential='test-only')
        sizes = (4420, 12010, 44851, 51963)
        prior = None
        for reviewer in ('first', 'second'):
            for turn in range(4 if reviewer == 'first' else 1):
                context = case.deliver_supporting(reviewer) if turn == 0 else {'prior_assignment_id': prior['id']}
                prior = case.assign('bar', reviewer, identifier='bar-' + reviewer + ('' if turn == 0 else '-' + str(turn + 1)), **context)
                output = case.build_review_output('bar')
                if reviewer == 'first':
                    if turn < 3:
                        output['value']['status'] = 'unresolved'
                        version = next(iter(case.supporting_links.values()))['version_id']
                        output['source_requests'] = [{'id': 'source-' + str(turn), 'kind': 'source',
                                                      'depth': 'abstract', 'version_id': version}]
                    output['value']['reason'] = ''
                    output['value']['reason'] = 'b' * (sizes[turn] - len(json.dumps(output, sort_keys=True)))
                self.invoke(prior, output)
            case.mutate(decisions.record_value_review, dict(output, id='review-bar-' + reviewer,
                        dossier_id=None, assignment_id=prior['id']))
        # Exact raw assistant response lengths from the incident's selected outputs.
        self.part_sizes = ((19748, 33352), (31273, 31432), (31816, 32643, 19208),
                           (15694, 36239, 30063, 17604))
        self.final_sizes = (33304, 38702, 39724)
        self.refusal_size = 1472433

    def invoke(self, assignment, output=None, attempt=None):
        if not hasattr(self, 'claude_respond'):
            return super().invoke(assignment, output, attempt)
        output = self.output(assignment) if output is None else output
        with patch.object(transport, 'send_request', side_effect=lambda route, request, credential:
                          self.claude_respond(route, request, output)):
            return self.case.mutate(protocol.invoke_assignment, {'id': attempt or 'attempt-' + assignment['id'],
                                    'assignment_id': assignment['id']}, credential='test-only')

    def test_fourth_series_final_fits_after_history_compaction(self):
        prior = self.use_fixture('claude')
        records = self.fixture['records']
        for index, turns in enumerate([row[0] for row in self.closed] + [self.current]):
            self.assertEqual(len(turns), (2, 2, 3, 4)[index])
            for turn, assignment in enumerate(turns):
                self.assertEqual(self.packet(assignment)[KEY]['stage'], 'part')
                output = json.loads(self.case.artifacts.read(records['review_attempt']['attempt-' + assignment['id']]['output']))
                self.assertEqual(len(json.dumps(output, sort_keys=True)), self.part_sizes[index][turn])
        for index, (_, assignment, _) in enumerate(self.closed):
            output = json.loads(self.case.artifacts.read(records['review_attempt']['attempt-' + assignment['id']]['output']))
            self.assertEqual(len(json.dumps(output, sort_keys=True)), self.final_sizes[index])
        self.assertEqual(self.replacements(prior['history']), [])
        inputs = self.refusal_inputs(prior)
        error = self.assert_code('review_input_too_large', lambda: parts.prepare_packet(*inputs[:7]))
        self.assertEqual(error.details['input_characters'], 1472433)
        original = copy.deepcopy(inputs[6])
        with self.pad_frame(self.frame_size):
            final = self.assign('fourth-series-final', prior['id'])
        plan = self.packet(final)[KEY]
        self.assertEqual((plan['stage'], plan['turn'], plan['series_assignment_id']), ('final', 5, 'series-4'))
        replaced = self.replacements(final['history'])
        self.assertEqual(sum(p['evidence_stage'] == 'part' for p in replaced), 7)
        self.assertEqual(sum(p['evidence_stage'] == 'final' for p in replaced), 2)
        self.assertEqual(sum(self.part_sizes[0] + self.part_sizes[1] + self.part_sizes[2]), 199472)
        self.assertEqual(sum(self.final_sizes[:2]), 72006)
        # History comes directly from the same parent request, so every retained position has exact bytes.
        eligible = {p['assignment_id'] for p in replaced}
        self.assertEqual(len(final['history']), len(original))
        chain = []
        current = prior
        while current is not None:
            chain.append(current)
            current = inputs[0]['review_assignment'].get(current.get('history_assignment_id'))
        for index, row in enumerate(reversed(chain)):
            self.assertEqual(final['history'][2 * index], original[2 * index])
            if row['id'] not in eligible:
                self.assertEqual(final['history'][2 * index + 1], original[2 * index + 1])
        size = parts._measure(inputs[3], self.request(final))
        self.assertLessEqual(size, 1300000)
        print('Claude fourth-series final: baseline=1472433, compacted=%d, limit=1300000' % size)


PINNED_SHA256 = {'research_harness/review_claude_transport.py': 'ee183e027eaad81de13cfab1149000c027b4504842070537a1068c93dfb1a7bf',
 'research_harness/review_codex_framed_transport.py': '1f8824a96003c0885df028533c0df676f2957b33beb986851a191486c534053d',
 'research_harness/review_codex_transport.py': 'a53baa420784a146340ae595aadbd370b1917b33725a1abf805d3bab6126d078',
 'research_harness/review_codex_visual_transport.py': '5ee79b4655535023cd22b41233dc08d9dc69b231715c39e74dd6ac43ec264dd6',
 'research_harness/review_exact_text.py': 'bdaa6f03311ea602fe4579d9fdb1db0baf5db2a571dba419650acff40fbb931d',
 'research_harness/review_exact_text_cost_v1.py': 'f017125ec32bf89c8aa71c2ccba96ba8e0fcdf1abb53c050d8bd16b90adf7cf5',
 'research_harness/review_exact_text_v2.py': 'fcae2720e7f584936e026427203c945dc18be0794d003113c800e0de898e2bf8',
 'research_harness/review_pdf_pages.py': '5b884cdf50483225f24868b211b188b6d62879d01098d89710ec92c62f573147',
 'research_harness/review_transport.py': 'eb08185b87656093ea3895582c01eb57758afc385718909cec0f1ccf60e7a3d3',
 'research_harness/review_verbatim_text.py': '97df5c8b8d48e7e763546ba8e42e7fba65651e9e683dcc853367aca3a5db9c56',
 'research_harness/review_visual_inputs.py': '976b95d2a24346db337acdfaa66708df6ee73eb6a15c891159c7ad2ce9aeb96d'}
