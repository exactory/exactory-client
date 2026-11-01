"""Synthetic staged entry, exact bounded diagnostics and evaluation work bounds."""

import copy
import json
from pathlib import Path
import signal
import tempfile
import time
import unittest
from unittest.mock import patch

from research_harness import output_views, review_protocol as protocol
from research_harness import review_staged as staged, review_staged_protocol as producer
from research_harness.artifacts import ArtifactStore
from research_harness.errors import ResearchError
from research_harness.evidence import digest
from research_harness.storage import _canonical
from test_review_staged_fixtures import epoch, full_card, selection, profile


def tagged_text(tag, length):
    return (tag + ' ' + 'synthetic original prose ' * length)[:length]


def scale_packet(artifacts, large=False):
    """The review's exact length construction; the small case uses 256/32/8."""
    lengths = [80] * 600 + [146] * 338 + [145] * 318 + [64] * 284 + [76]
    pool = [tagged_text('frame-%04d' % i, size) for i, size in enumerate(lengths)]
    frame = pool + [pool[i % 600] for i in range(13828)] + pool[1256:1276]
    if not large:
        frame = frame[:256]
    count, bodies = (106, 32) if large else (32, 8)
    references = []
    for i in range(bodies):
        size = [16384, 32768, 65536, 262144][i % 4] + 614 + int(i < 18)
        references.append(artifacts.put(tagged_text('artifact-%d' % i, size).encode(), 'text/plain'))
    items = []
    for i in range(count):
        size = [1024, 4096, 8192, 32768][i % 4] - 230 - int(i < 68)
        item = dict(tag='item-%d' % i, prose='', artifacts=[references[i]] if i < bodies else [])
        item['prose'] = 'x' * (size - len(_canonical(item)))
        assert len(_canonical(item)) == size
        items.append(item)
    return dict(research=dict(sections=[dict(paragraph=text) for text in frame]),
                problem_evidence=dict(computations=items), evidence=[])


class StagedScaleTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.artifacts = ArtifactStore(Path(temporary.name))
        self.route = dict(id='route', adapter='claude_cli_v1', model='synthetic', endpoint='claude://local',
                          configuration=dict(effort='max', max_output_tokens=128000, timeout_seconds=3600))
        self.controller = dict(id='controller', role='result', context={}, slot_id='slot',
            reviewer_id='reviewer', route_id='route', author_id='author', dossier_id=None,
            subject=dict(kind='problem', id='synthetic'), recorded_revision=1,
            prompt_hash='synthetic', packet_digest='synthetic')
        self.records = dict(review_route={'route': self.route}, review_assignment={})

    def enter(self, packet):
        changes = producer.prepare_controller(self.records, self.artifacts, self.controller, packet, self.route)
        for kind, key, row in changes:
            self.records.setdefault(kind, {})[key] = row
        self.records['review_assignment'][self.controller['id']] = self.controller
        return self.records['review_epoch'][self.controller['staged_review']['epoch_id']]

    def test_large_entry_refuses_before_freezing_or_planning_and_writes_nothing(self):
        packet = scale_packet(self.artifacts, large=True)
        records, controller = copy.deepcopy(self.records), copy.deepcopy(self.controller)
        def timed_out(signum, frame):
            raise AssertionError('scale preflight exceeded 30 seconds')
        previous = signal.signal(signal.SIGALRM, timed_out)
        signal.alarm(30)
        started = time.monotonic()
        preflight = producer.check_scale
        def checked(records, artifacts, route, manifest, role):
            self.assertEqual(len(manifest['sources']), 15527)
            self.assertEqual(len({row['sha256'] for row in manifest['sources']}), 1679)
            self.assertEqual(sum(row['characters'] for row in manifest['sources']), 5482304)
            return preflight(records, artifacts, route, manifest, role)
        try:
            with patch.object(staged, 'freeze_epoch', wraps=staged.freeze_epoch) as freeze, \
                    patch.object(producer, 'prepare_frame_tasks', wraps=producer.prepare_frame_tasks) as plan, \
                    patch.object(self.artifacts, 'put', wraps=self.artifacts.put) as put, \
                    patch.object(producer, 'check_scale', side_effect=checked):
                with self.assertRaises(ResearchError) as caught:
                    self.enter(packet)
                self.assertEqual(caught.exception.code, 'review_staged_scale_unsupported')
                self.assertEqual(caught.exception.details['claims'], 15389)
                self.assertEqual(caught.exception.details['occurrences'], 106)
                self.assertGreater(caught.exception.details['envelope_characters'], caught.exception.details['input_limit'])
                self.assertGreater(caught.exception.details['card_output_tokens'], caught.exception.details['output_allowance'])
                self.assertEqual(freeze.call_count, 0)
                self.assertEqual(plan.call_count, 0)
                self.assertEqual(put.call_count, 0)
        finally:
            signal.alarm(0)
            signal.signal(signal.SIGALRM, previous)
        self.assertLess(time.monotonic() - started, 30)
        self.assertEqual(self.records, records)
        self.assertEqual(self.controller, controller)

    def test_passing_entry_empty_state_and_view_have_bounded_exact_work(self):
        value = self.enter(scale_packet(self.artifacts))
        self.assertEqual((len(value['manifest']['claims']), len(value['manifest']['occurrences'])), (256, 32))
        selected = dict(observations=[])
        started = time.monotonic()
        with patch.object(protocol, 'route_state', return_value=dict(context_status='verified_for_route')), \
                patch.object(producer, 'matches_currency', return_value=True), \
                patch.object(staged, '_record_completion_issue', wraps=staged._record_completion_issue) as insert:
            state = producer.build_state(self.records, self.artifacts, self.controller)
        self.assertLess(insert.call_count, 256 * 32 // 4)
        self.assertLess(time.monotonic() - started, 2)
        report = state['completion']
        self.assertFalse(report['complete'])
        pending = [row for row in report['issues'] if row['code'] == 'review_claim_cell_pending']
        self.assertEqual(len(pending), 1)
        self.assertEqual(pending[0]['count'], 256 * 32)
        exact = list(staged.iter_completion_issues(value, selected, tasks=producer.list_epoch_tasks(self.records, value),
                                                  code='review_claim_cell_pending'))
        self.assertEqual(len(exact), 256 * 32)
        self.assertEqual(pending[0]['digest'], digest(sorted(exact, key=_canonical)))
        self.assertEqual(list(staged.iter_completion_issues(value, selected,
            tasks=producer.list_epoch_tasks(self.records, value), code='review_claim_cell_pending', offset=500, limit=25)),
            exact[500:525])
        self.assertLessEqual(len(pending[0]['sample']), 20)
        self.assertLessEqual(len(_canonical(report).encode()), 65536)
        with patch.object(protocol, 'route_state', return_value=dict(context_status='verified_for_route')), \
                patch.object(producer, 'matches_currency', return_value=True), \
                patch.object(staged, 'verify_completion', wraps=staged.verify_completion) as verify:
            for i in range(64):
                child = dict(self.controller, id='child-%d' % i, recorded_revision=i + 2,
                    staged_review=dict(kind='task', controller_assignment_id='controller',
                        epoch_id=value['id'], task_id='pending-%d' % i))
                self.records['review_assignment'][child['id']] = child
            started = time.monotonic()
            files = output_views.compute_view_model(self.records, self.artifacts, output_views._ViewInputs({}, {}))
            self.assertTrue(files)
            workspace = self.artifacts.root / 'rendered'
            store_root = workspace / '.exactory'
            store_root.mkdir(parents=True)
            written, errors = output_views._write_views(workspace, store_root, dict(files={}), files, self.artifacts)
            self.assertEqual(errors, {})
            self.assertTrue(all((workspace / path).is_file() for path in written))
            self.assertEqual(verify.call_count, 1)
            self.assertLess(time.monotonic() - started, 5)

    def test_completion_catalog_is_built_once_and_nested_cells_use_identity_sets(self):
        value = epoch()
        card = full_card(value)
        tasks, selected = selection(value, cards=[card])
        original = staged._index
        counts = dict(sources=0)
        def indexed(rows, name):
            if name == 'sources':
                counts['sources'] += 1
            return original(rows, name)
        for evaluate in (staged.verify_completion, staged.derive_context_graph):
            with self.subTest(evaluate=evaluate.__name__):
                counts['sources'] = 0
                with patch.object(staged, '_index', side_effect=indexed):
                    result = evaluate(value, selected, tasks=tasks)
                if evaluate is staged.verify_completion:
                    self.assertTrue(result['complete'], result['issues'])
                self.assertEqual(counts['sources'], 1)

    def test_upgrade_refuses_output_overflow_without_changing_the_existing_slot(self):
        self.enter(scale_packet(self.artifacts))
        previous_records = copy.deepcopy(self.records)
        successor = dict(self.controller, id='successor', context=dict(staged_upgrade=dict(assignment_id='controller')))
        successor.pop('staged_review')
        before = copy.deepcopy(successor)
        route = dict(self.route, configuration=dict(self.route['configuration'], max_output_tokens=1000))
        with patch.object(staged, 'freeze_epoch', wraps=staged.freeze_epoch) as freeze:
            with self.assertRaises(ResearchError) as caught:
                producer.prepare_controller(self.records, self.artifacts, successor, scale_packet(self.artifacts), route)
        self.assertEqual(caught.exception.code, 'review_staged_scale_unsupported')
        details = caught.exception.details
        self.assertLess(details['envelope_characters'], details['input_limit'])
        self.assertGreater(details['card_output_tokens'], details['output_allowance'])
        self.assertEqual(freeze.call_count, 0)
        self.assertEqual(self.records, previous_records)
        self.assertEqual(successor, before)

    def test_issue_dedup_does_not_compare_the_accumulated_issue_list(self):
        class Issues(list):
            comparisons = 0
            def __contains__(self, row):
                self.comparisons += len(self)
                return super().__contains__(row)
        original = staged._record_completion_issue
        captured = []
        def record(work, code, **details):
            if not captured:
                work['issues'] = Issues(work['issues'])
                captured.append(work['issues'])
            return original(work, code, **details)
        with patch.object(staged, '_record_completion_issue', side_effect=record):
            report = staged.verify_completion(epoch(count=16), dict(observations=[]))
        self.assertFalse(report['complete'])
        self.assertEqual(captured[0].comparisons, 0)

    def test_source_unresolved_and_combination_summaries_bind_every_exact_row(self):
        value = epoch(count=8)
        cards = [full_card(value, index=i) for i in range(8)]
        cards[-1]['judgments'][-1]['status'] = 'unresolved'
        tasks, selected = selection(value, cards=cards)
        # The reading is selected without any combination assessment.
        selected = dict(observations=[selected['observations'][1]])
        tasks = tasks[:1]
        cases = [(selected, tasks, dict(review_combination_pending=36, review_claim_unresolved=1)),
                 (dict(observations=[]), [], dict(review_source_coverage_pending=len(value['manifest']['sources'])))]
        for selected, tasks, expected in cases:
            report = staged.verify_completion(value, selected, tasks=tasks)
            self.assertFalse(report['complete'])
            for code, count in expected.items():
                summary = next(row for row in report['issues'] if row['code'] == code)
                exact = list(staged.iter_completion_issues(value, selected, tasks=tasks, code=code))
                self.assertEqual(len(exact), count)
                self.assertEqual(summary['count'], count)
                self.assertEqual(summary['digest'], digest(sorted(exact, key=_canonical)))
                self.assertEqual(summary['sample'], sorted(exact, key=_canonical)[:20])

    def test_planner_validates_the_epoch_once_for_many_tasks(self):
        value = epoch(count=16)
        with patch.object(staged, '_validate_epoch', wraps=staged._validate_epoch) as validate:
            planned = staged.plan_tasks(value, route_limit=100000, output_tokens=10000, profiles={'read': profile()})
        self.assertEqual(len(planned['tasks']), 16)
        self.assertEqual(validate.call_count, 1)

    def test_indexing_and_verified_digest_reuse_preserve_frozen_identities(self):
        # These identities were measured on the unchanged lane base.
        value = epoch()
        self.assertEqual(value['manifest']['digest'], '9952d702bffb0516feb54df5c833d34d9d6d68768a703128937665fb67673c6d')
        self.assertEqual(value['digest'], 'bad48a33c6b3c5569bbc49454e74f0d18585b1c556918c3e5a306a6b5820bab1')
        verified = staged.make_task(value, 'read', claim_ids=list(value['manifest']['contexts']),
                                   verified_epoch_digest=value['digest'])
        self.assertEqual(verified['digest'], 'f11cd457f24b4217baf496d31cb926e8b9bd6a5a4b9ff85e1a5ee95228f391c5')
        self.assertEqual(verified, staged.make_task(value, 'read', claim_ids=list(value['manifest']['contexts'])))
        with self.assertRaises(ResearchError):
            staged.make_task(value, 'read', verified_epoch_digest='different-epoch')

    def test_protocol_planning_does_not_rehash_the_epoch_when_remapping_tasks(self):
        self.enter(scale_packet(self.artifacts))
        with patch.object(staged, '_validate_epoch', wraps=staged._validate_epoch) as validate:
            planned = producer.plan_remaining_tasks(self.records, self.artifacts, self.controller,
                                                     profiles={'read': profile()})
        self.assertEqual(len(planned['tasks']), 32)
        self.assertEqual(validate.call_count, 2)

    def test_manifest_joins_visit_independent_claims_linearly(self):
        visits = [0]
        class Claims(list):
            def __iter__(self):
                for row in super().__iter__():
                    visits[0] += 1
                    yield row
        bind = staged.bind_quotations
        def counted(*args, **kwargs):
            bound, unmatched = bind(*args, **kwargs)
            if 'claims' in bound:
                bound['claims'] = Claims(bound['claims'])
            return bound, unmatched
        with patch.object(staged, 'bind_quotations', side_effect=counted):
            manifest = staged.build_manifest(dict(fields=['field-%d' % i for i in range(240)]), [], [])
        self.assertEqual(len(manifest['claims']), 240)
        self.assertLess(visits[0], 40 * 240)

    def test_nested_cell_collection_and_obligation_ids_use_sets(self):
        manifest = staged.build_manifest(dict(statement=['claim-%d' % i for i in range(80)]),
            [dict(key='readings', index=0, item='evidence', artifacts=[], source_ids=[])], [])
        value = epoch(manifest=manifest)
        tasks, selected = selection(value, cards=[full_card(value)])
        comparisons = dict(cells=0, identities=0)
        class Cell(dict):
            def __eq__(self, other):
                comparisons['cells'] += 1
                return super().__eq__(other)
        class Identity(str):
            __hash__ = str.__hash__
            def __eq__(self, other):
                comparisons['identities'] += 1
                return super().__eq__(other)
        collect = staged._check_completion_cells
        account = staged._check_completion_integration_accounting
        def counted_cells(work):
            for card in work['cards']:
                card['judgments'] = [Cell(row) for row in card['judgments']]
            work['grouped']['cells'] = {key: Cell(row) for key, row in work['grouped'].get('cells', {}).items()}
            return collect(work)
        def counted_obligations(work):
            for card in work['full_cards']:
                for row in card['conclusions'] + card['conditions'] + card['judgments']:
                    row['id'] = Identity(row['id'])
            return account(work)
        with patch.object(staged, '_check_completion_cells', side_effect=counted_cells), \
                patch.object(staged, '_check_completion_integration_accounting', side_effect=counted_obligations):
            report = staged.verify_completion(value, selected, tasks=tasks)
        self.assertTrue(report['complete'], report['issues'])
        for family, bound in (('cells', 80 * 4), ('identities', 80 * 20)):
            with self.subTest(family=family):
                self.assertLess(comparisons[family], bound)

    def test_missing_integration_obligations_are_individual_exact_set_members(self):
        manifest = staged.build_manifest(dict(statement=['claim-%d' % i for i in range(80)]),
            [dict(key='readings', index=0, item='evidence', artifacts=[], source_ids=[])], [])
        value = epoch(manifest=manifest)
        card = full_card(value)
        tasks, selected = selection(value, cards=[card])
        selected = dict(observations=[selected['observations'][1]])
        tasks = tasks[:1]
        report = staged.verify_completion(value, selected, tasks=tasks)
        summary = next(row for row in report['issues'] if row['code'] == 'review_integration_pending')
        self.assertEqual(summary['count'], len(staged.card_obligations([card])) + 1)
        exact = list(staged.iter_completion_issues(value, selected, tasks=tasks, code='review_integration_pending'))
        self.assertEqual(summary['digest'], digest(sorted(exact, key=_canonical)))
        self.assertEqual({row['obligation_id'] for row in exact if 'obligation_id' in row},
                         set(staged.card_obligations([card])))
        self.assertLessEqual(len(summary['sample']), 20)
        self.assertFalse(any('obligation_ids' in row for row in summary['sample']))


if __name__ == '__main__':
    unittest.main()
