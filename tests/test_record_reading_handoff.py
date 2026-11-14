"""A delivered citation retains its reading prerequisite and a bounded refusal path."""

import copy
import unittest
from unittest.mock import patch

import test_research_problem_reviews as fixtures
from research_harness import cli, literature, reading, review_protocol as protocol, review_refusal
from research_harness.errors import ResearchError
from research_harness.evidence import digest
from research_harness.record_check import check_continuation
from research_harness.review_continuation import plan_next
from research_harness.review_series import run_series


class ReadingHandoffTests(unittest.TestCase):
    def setUp(self):
        self.case = fixtures.ProblemReviewTests()
        self.addCleanup(self.case.doCleanups)
        self.case.setUp()
        self.case.prepare_review_route()
        self.case.supporting = self.case.artifacts.put(b'A synthetic field standard.', 'text/plain')
        self.bundles = []
        self.links = []

    def add_source(self, number=1, *, bundle=True):
        version = self.case.metadata(number)
        capture = self.case.capture(version, body='An authored bounded example. References: none.')
        declared = self.case.bundle(version, capture, bundle_id='bundle-' + str(number))
        if bundle:
            self.case.mutate(literature.import_bundle, declared)
        self.bundles.append(declared)
        self.links.append(declared['units'][0]['link'])
        return version

    def invoke(self, assignment, output, attempt=None):
        with patch.object(protocol.transport, 'send_request', return_value=self.case.build_provider_response(output)):
            return self.case.mutate(protocol.invoke_assignment,
                dict(id=attempt or 'attempt-' + assignment['id'], assignment_id=assignment['id']), credential='fixture')

    def build_final(self, *, count=1, abstract=False, role='bar', invalid=None):
        if role != 'bar':
            for reviewer in ('first', 'second'):
                self.case.observe('bar', reviewer)
        versions = [self.add_source(number) for number in range(1, count + 1)]
        requests = [dict(id='full-' + str(i), kind='source', version_id=version, depth='fulltext')
                    for i, version in enumerate(versions)]
        if abstract:
            version = self.case.metadata(50)
            item = self.case.read_records()['work'][version]['abstracts'][0]
            self.abstract_link = self.case.link(version, item['source_id'], item['artifact'])
            requests.append(dict(id='abstract', kind='source', version_id=version, depth='abstract'))
        first = self.case.assign(role, 'first', identifier='initial')
        output = self.case.build_review_output(role)
        if role == 'slate':
            output['value']['rubric_potential'] = {}
            for check in output['value']['assurances']:
                check['status'] = 'not_assessed'
        output['value'].update(status='unresolved', evidence=[])
        output['source_requests'] = requests
        self.invoke(first, output)
        final = self.case.assign(role, 'first', identifier='final', prior_assignment_id='initial')
        output = self.case.build_review_output(role)
        if role == 'slate':
            output['value']['rubric_potential'] = {}
            for check in output['value']['assurances']:
                check['status'] = 'not_assessed'
        output['value']['evidence'] = [dict(kind='source', link=link) for link in self.links]
        if abstract:
            output['value']['evidence'].append(dict(kind='source', link=self.abstract_link))
        if invalid == 'scalar':
            output['value']['status'] = 'invalid'
        elif invalid == 'support':
            output['support'] = dict(native=True)
        elif invalid == 'locator':
            output['value']['evidence'][0] = copy.deepcopy(output['value']['evidence'][0])
            output['value']['evidence'][0]['link']['locator']['end'] = 999999
        self.invoke(final, output)
        return final, output

    def plan(self):
        return plan_next(self.case.read_records(), self.case.artifacts, 'initial')

    def execute(self, plan, request='execute'):
        return cli.OPERATIONS[plan['command']](self.case.store, plan['payload'],
            expected_revision=self.case.store.revision, request_id=request)

    def read_source(self, index=0):
        self.case.mutate(reading.record_reading, self.case.full_note(self.bundles[index], 'reading-' + str(index)))

    def test_delivered_fulltext_hands_off_reading_then_records_the_same_output_without_a_call(self):
        # Real shape of 20261111235418-bar-final-refused-for-text-delivered-in-reduced-history, M1/M2.
        final, output = self.build_final()
        before = copy.deepcopy(self.case.read_records())
        with patch.object(protocol.transport, 'send_request', side_effect=AssertionError('Unexpected reviewer call')):
            rows = []
            self.assertEqual(run_series(self.case.store, 'initial', emit=rows.append), 3)
            plan = rows[-1]
            self.assertEqual((plan['state'], plan['code'], plan['command']), ('handoff', 'reading_missing', 'read'))
            missing = plan['details']['readings']
            self.assertEqual([row['version_id'] for row in missing], [self.links[0]['version_id']])
            self.assertEqual(missing[0]['citations'], [dict(path='/value/evidence/0', **self.links[0])])
            self.assertEqual(missing[0]['captures'][0]['bundle_id'], 'bundle-1')
            self.assertEqual([row['id'] for row in missing[0]['captures'][0]['required_units']],
                             ['body', 'bibliography', 'abstract'])
            self.assertEqual(plan['details']['step']['id'], 13)
            self.assertEqual(plan['details']['then'], dict(command='review-series', assignment_id=final['id']))
            self.assertEqual(self.case.read_records(), before)
            self.read_source()
            record = self.plan()
            self.assertEqual(record, dict(state='record', chain='session', head_id='final',
                progress=record['progress'], command='value-review',
                payload=dict(output, id='review:final', assignment_id='final', dossier_id=None)))
            saved = self.execute(record)['result']
            self.assertEqual(saved['output_digest'], digest(output))
            self.assertEqual(self.plan()['state'], 'done')
        self.assertEqual(self.case.read_records()['review_attempt'], before['review_attempt'])

    def test_two_versions_shrink_to_one_missing_version(self):
        _, output = self.build_final(count=2)
        plan = self.plan()
        self.assertEqual(plan['state'], 'handoff')
        self.assertEqual(len(plan['details']['readings']), 2)
        self.read_source()
        remaining = self.plan()['details']['readings']
        self.assertEqual([row['version_id'] for row in remaining], [self.links[1]['version_id']])
        self.read_source(1)
        self.assertEqual(self.plan()['state'], 'record')

    def test_record_check_accepts_reading_handoff_with_its_rows(self):
        self.build_final()
        revision = self.case.store.revision
        report = check_continuation(self.case.store)
        chain = next(row for row in report['chains'] if row['head_id'] == 'final')
        self.assertEqual(chain['boundary'], 'handoff')
        self.assertEqual(chain['actions'][-1]['details']['readings'][0]['version_id'], self.links[0]['version_id'])
        self.assertEqual(self.case.store.revision, revision)

    def test_abstract_refusal_precedes_every_missing_reading_and_rechecks_from_frozen_output(self):
        final, output = self.build_final(abstract=True)
        plan = self.plan()
        self.assertEqual(plan['state'], 'record')
        with self.assertRaises(ResearchError) as caught:
            self.execute(plan)
        self.assertEqual(caught.exception.code, 'source_link_not_fulltext')
        self.assertEqual(caught.exception.details['path'], '/value/evidence/1')
        self.assertEqual(caught.exception.details['artifact'], self.abstract_link['artifact'])
        self.assertEqual(caught.exception.details['delivered_depth'], 'abstract')
        self.assertEqual(caught.exception.details['readings'][0]['version_id'], self.links[0]['version_id'])
        records = self.case.read_records()
        certificate = copy.deepcopy(records['review_output_refusal']['attempt-final'])
        self.assertEqual(certificate['code'], 'source_link_not_fulltext')
        self.assertEqual(certificate['check'], 'evidence.fulltext')
        self.assertEqual(certificate['output_digest'], digest(output))
        self.read_source()
        self.assertEqual(self.case.read_records()['review_output_refusal']['attempt-final'], certificate)
        with patch.object(reading, 'current_readings', side_effect=AssertionError('A pure refusal reads no reading')):
            self.assertEqual(review_refusal.classify_observed_output(self.case.read_records(), self.case.artifacts,
                             'attempt-final')['code'], 'source_link_not_fulltext')
        self.assertEqual(self.plan()['state'], 'run')
        output['value']['evidence'][-1] = self.abstract_link['artifact']
        self.invoke(final, output, 'corrected-final')
        self.assertEqual(self.plan()['state'], 'record')
        self.execute(self.plan(), 'record-corrected')
        self.assertEqual(self.plan()['state'], 'done')

    def test_second_output_citing_descriptor_hands_off_only_the_fulltext_reading(self):
        final, output = self.build_final(abstract=True)
        with self.assertRaises(ResearchError) as caught:
            self.execute(self.plan())
        self.assertEqual(caught.exception.code, 'source_link_not_fulltext')
        output['value']['evidence'][-1] = self.abstract_link['artifact']
        self.invoke(final, output, 'corrected-final')
        plan = self.plan()
        self.assertEqual((plan['state'], plan['command']), ('handoff', 'read'))
        self.assertEqual(len(plan['details']['readings']), 1)
        self.read_source()
        self.execute(self.plan(), 'record-corrected')
        self.assertEqual(self.plan()['state'], 'done')

    def test_abstract_alone_refuses_twice_then_new_blind_reviewer_takes_the_slot(self):
        final, output = self.build_final(count=0, abstract=True)
        self.case.assign('bar', 'second', identifier='other-slot')
        with self.assertRaises(ResearchError) as caught:
            self.execute(self.plan())
        self.assertEqual(caught.exception.code, 'source_link_not_fulltext')
        report = check_continuation(self.case.store)
        self.assertEqual(next(row for row in report['chains'] if row['head_id'] == 'final')['boundary'], 'run')
        rows = []
        with patch.object(protocol.transport, 'send_request', return_value=self.case.build_provider_response(output)):
            self.assertEqual(run_series(self.case.store, 'initial', emit=rows.append), 3)
        plan = rows[-1]
        self.assertEqual((plan['state'], plan['code']), ('handoff', 'review_slot_exhausted'))
        self.assertEqual(len(self.case.read_records()['review_output_refusal']), 2)
        step = plan['details']['step']
        self.assertEqual(step['excluded_reviewer_ids'], ['first', 'second'])
        replacement = self.case.mutate(protocol.record_assignment, dict(step['payload'], id='replacement',
            route_id='route', reviewer_id='third'))['result']
        self.assertEqual(replacement['slot_id'], 'initial')
        self.invoke(replacement, self.case.build_review_output('bar'))
        self.assertIn('attempt-replacement', self.case.read_records()['review_attempt'])

    def test_reading_that_becomes_stale_after_the_plan_returns_registry_handoff_without_stop(self):
        self.build_final()
        self.read_source()
        planned = self.plan()
        self.assertEqual(planned['state'], 'record')
        later = copy.deepcopy(self.bundles[0])
        later['id'] = 'updated-bundle'
        later['units'].append(dict(id='proof', kind='text', required=True, link=self.links[0]))
        self.case.mutate(literature.import_bundle, later)
        rows = []
        with patch('research_harness.review_series._read_plan', wraps=__import__(
                'research_harness.review_series', fromlist=['_read_plan'])._read_plan) as read_plan:
            real_read = read_plan._mock_wraps
            def stale_first(*args):
                snapshot, plan = real_read(*args)
                return snapshot, dict(planned, provider_usage=plan['provider_usage']) if not rows else plan
            read_plan.side_effect = stale_first
            self.assertEqual(run_series(self.case.store, 'initial', emit=rows.append), 3)
        self.assertEqual((rows[-1]['state'], rows[-1]['code'], rows[-1]['command']),
                         ('handoff', 'reading_missing', 'read'))
        self.assertEqual(rows[-1]['details']['readings'][0]['captures'][0]['bundle_id'], 'updated-bundle')
        self.assertNotIn('review_series_stop', self.case.read_records())
        self.assertNotIn('review_output_refusal', self.case.read_records())

    def test_slate_and_result_finals_use_the_same_reading_step_and_recording_command(self):
        for role in ('slate', 'result'):
            with self.subTest(role=role):
                fixture = ReadingHandoffTests()
                self.addCleanup(fixture.doCleanups)
                fixture.setUp()
                fixture.build_final(role=role)
                plan = fixture.plan()
                self.assertEqual((plan['state'], plan['command']), ('handoff', 'read'))
                self.assertEqual(plan['details']['step']['id'], 13)
                fixture.read_source()
                fixture.execute(fixture.plan())
                self.assertEqual(fixture.plan()['state'], 'done')

    def test_fulltext_link_without_bundle_names_the_bundle_prerequisite(self):
        self.build_final()
        records = self.case.read_records()
        records['source_bundle'] = {}
        records['bundle_selection'] = {}
        plan = plan_next(records, self.case.artifacts, 'initial')
        self.assertEqual(plan['state'], 'handoff')
        row = plan['details']['readings'][0]['captures'][0]
        self.assertIsNone(row['bundle_id'])
        self.assertEqual([step['command'] for step in row['prerequisites']], ['bundle'])

    def test_every_citation_path_is_kept_and_an_unread_original_needs_acquisition_first(self):
        final, output = self.build_final()
        output['objections'] = [dict(id='gap', claim='A synthetic gap.', reason='The supplied example is limited.',
            evidence=[dict(kind='source', link=self.links[0])], resolution_condition='Inspect the complete source.')]
        records = self.case.read_records()
        from research_harness.review_reading import plan_readings
        plan = plan_readings(records, self.case.artifacts, final, output)
        self.assertEqual([row['path'] for row in plan['details']['readings'][0]['citations']],
                         ['/value/evidence/0', '/objections/0/evidence/0'])
        records['source'][self.links[0]['source_id']]['response_complete'] = False
        plan = plan_next(records, self.case.artifacts, 'initial')
        self.assertEqual(plan['state'], 'handoff')
        prerequisites = plan['details']['readings'][0]['captures'][0]['prerequisites']
        self.assertEqual(prerequisites[0]['command'], 'fulltext')
        self.assertEqual(prerequisites[0]['alternatives'], ['import-fulltext'])

    def test_result_abstract_refusal_exhausts_to_the_preserved_slot_route_decision(self):
        final, output = self.build_final(count=0, abstract=True, role='result')
        with self.assertRaises(ResearchError) as caught:
            self.execute(self.plan())
        self.assertEqual(caught.exception.code, 'source_link_not_fulltext')
        rows = []
        with patch.object(protocol.transport, 'send_request', return_value=self.case.build_provider_response(output)):
            self.assertEqual(run_series(self.case.store, 'initial', emit=rows.append), 3)
        self.assertEqual((rows[-1]['state'], rows[-1]['code']), ('stop', 'review_slot_exhausted'))
        self.assertEqual([step['id'] for step in rows[-1]['details']['steps']], [8, 7, 1])
        self.assertTrue(rows[-1]['details']['decision']['then'])

    def test_stored_reviews_and_refusal_certificates_keep_their_identities_through_the_handoff(self):
        final, output = self.build_final(abstract=True)
        with self.assertRaises(ResearchError) as caught:
            self.execute(self.plan())
        self.assertEqual(caught.exception.code, 'source_link_not_fulltext')
        certificate = copy.deepcopy(self.case.read_records()['review_output_refusal'])
        output['value']['evidence'][-1] = self.abstract_link['artifact']
        self.invoke(final, output, 'corrected-final')
        self.read_source()
        saved = self.execute(self.plan(), 'record-corrected')['result']
        from research_harness import research_decisions as decisions
        records = self.case.read_records()
        before = copy.deepcopy(records)
        state = protocol.assignment_state(records, self.case.artifacts, saved['assignment_id'])
        self.assertEqual(decisions._independence(records, self.case.artifacts, [saved]), [])
        for attempt in records['review_attempt'].values():
            assignment = records['review_assignment'][attempt['assignment_id']]
            self.assertTrue(protocol.authenticate_attempt(records, self.case.artifacts, assignment, attempt))
        self.assertEqual(self.plan()['state'], 'done')
        self.assertEqual(self.case.read_records()['review_output_refusal'], certificate)
        self.assertEqual(protocol.assignment_state(self.case.read_records(), self.case.artifacts, saved['assignment_id']), state)
        self.assertEqual(self.case.read_records(), before)
        self.assertEqual(digest(records['value_review'][saved['id']]), digest(saved))

    def test_malformed_selected_finals_are_refused_before_reading_and_the_retry_can_need_reading(self):
        for invalid, code in (('scalar', 'invalid_value_review'), ('locator', 'invalid_locator')):
            with self.subTest(invalid=invalid):
                fixture = ReadingHandoffTests()
                self.addCleanup(fixture.doCleanups)
                fixture.setUp()
                final, output = fixture.build_final(invalid=invalid)
                plan = fixture.plan()
                self.assertEqual(plan['state'], 'record')
                with self.assertRaises(ResearchError) as caught:
                    fixture.execute(plan)
                self.assertEqual(caught.exception.code, code)
                self.assertEqual(fixture.case.read_records()['review_output_refusal']['attempt-final']['code'], code)
                output['value']['status'] = 'sufficient'
                output['value']['evidence'][0]['link'] = fixture.links[0]
                fixture.invoke(final, output, 'corrected-final')
                self.assertEqual(fixture.plan()['state'], 'handoff')
                self.assertEqual(fixture.plan()['command'], 'read')

    def test_supplement_citation_uses_the_parent_bundle_of_the_recording_predicate(self):
        from test_research_components import ComponentTests
        from research_harness.review_reading import plan_readings
        fixture = ComponentTests()
        self.addCleanup(fixture.doCleanups)
        fixture.setUp()
        capture = fixture.acquire()['capture']
        bundle = fixture.filled_bundle(capture)
        fixture.mutate(literature.import_bundle, bundle)
        link = fixture.link(fixture.identifier, capture['source_id'], capture['text'])
        output = self.case.build_review_output('bar')
        output['value']['evidence'] = [dict(kind='source', link=link)]
        assignment = dict(id='supplement-final', role='bar')
        records = fixture.store.snapshot()['records']
        plan = plan_readings(records, fixture.artifacts, assignment, output)
        row = plan['details']['readings'][0]['captures'][0]
        self.assertEqual(row['bundle_id'], bundle['id'])
        self.assertEqual(row['prerequisites'], [])
        for change in ('partial', 'missing-supplement', 'missing-page', 'partial-supplement'):
            altered = copy.deepcopy(records)
            current = altered['source_bundle'][bundle['id']]
            if change == 'partial':
                current['completeness'] = 'partial'
            elif change == 'missing-supplement':
                current['units'] = [unit for unit in current['units']
                                    if unit['link'] is None or unit['link']['source_id'] != capture['source_id']]
            elif change == 'missing-page':
                current['units'] = [unit for unit in current['units'] if unit['id'] != 'si-page-2']
            else:
                next(unit for unit in current['units'] if unit['id'] == 'si')['link'] = fixture.link(
                    fixture.identifier, capture['source_id'], capture['text'], 'Title Alpha.')
            repair = plan_readings(altered, fixture.artifacts, assignment, output)
            pending = repair['details']['readings'][0]['captures'][0]
            self.assertIsNone(pending['bundle_id'])
            self.assertEqual(pending['prerequisites'][-1]['command'], 'bundle')
            self.assertEqual(pending['prerequisites'][-1]['payload']['source_id'], fixture.main['source_id'])
        altered = copy.deepcopy(records)
        current = altered['source_bundle'][bundle['id']]
        current['units'] = [unit for unit in current['units'] if unit['kind'] != 'supplement']
        page = next(unit['link'] for unit in current['units'] if unit['id'] == 'si-page-0')
        page_output = copy.deepcopy(output)
        page_output['value']['evidence'] = [dict(kind='source', link=page)]
        repair = plan_readings(altered, fixture.artifacts, assignment, page_output)
        pending = repair['details']['readings'][0]['captures'][0]
        self.assertIsNone(pending['bundle_id'])
        self.assertEqual(pending['prerequisites'][-1]['payload']['source_id'], fixture.main['source_id'])
        fixture.mutate(reading.record_reading, fixture.full_note(bundle, 'parent-and-supplement'))
        self.assertIsNone(plan_readings(fixture.store.snapshot()['records'], fixture.artifacts, assignment, output))

    def test_reading_detail_failure_keeps_the_original_output_refusal_and_its_certificate(self):
        self.build_final(abstract=True)
        with patch('research_harness.review_reading.collect_missing_readings',
                   side_effect=ResearchError('store_busy', 'The reading-detail snapshot is temporarily held.')):
            with self.assertRaises(ResearchError) as caught:
                self.execute(self.plan())
        self.assertEqual(caught.exception.code, 'source_link_not_fulltext')
        self.assertEqual(self.case.read_records()['review_output_refusal']['attempt-final']['code'],
                         'source_link_not_fulltext')
        self.assertEqual(self.plan()['state'], 'run')

    def test_catalog_gap_keeps_the_pure_refusal_before_the_corrected_output_needs_reading(self):
        final, output = self.build_final(invalid='support')
        with self.assertRaises(ResearchError) as caught:
            self.execute(self.plan())
        self.assertEqual(caught.exception.code, 'invalid_value_review')
        records = self.case.read_records()
        certificate = copy.deepcopy(records['review_output_refusal']['attempt-final'])
        records['work'] = {}
        failure = review_refusal.classify_observed_output(records, self.case.artifacts, 'attempt-final')
        self.assertIsNotNone(failure)
        self.assertEqual((failure['code'], failure['check']), ('invalid_value_review', 'review.support'))
        self.assertEqual(records['review_output_refusal']['attempt-final'], certificate)
        output['support'] = None
        self.invoke(final, output, 'corrected-final')
        self.assertEqual((self.plan()['state'], self.plan()['command']), ('handoff', 'read'))

    def test_a_reading_completed_during_the_recording_error_records_without_a_reviewer_call(self):
        self.build_final()
        self.read_source()
        planned = self.plan()
        later = copy.deepcopy(self.bundles[0])
        later['id'] = 'later-bundle'
        later['units'].append(dict(id='proof', kind='text', required=True, link=self.links[0]))
        self.case.mutate(literature.import_bundle, later)
        from research_harness import review_series
        read_plan = review_series._read_plan
        record_review = cli.OPERATIONS['value-review']
        rows = []
        def read_cached_plan(*args):
            snapshot, plan = read_plan(*args)
            return snapshot, dict(planned, provider_usage=plan['provider_usage']) if not rows else plan
        def finish_reading_after_refusal(*args, **kwargs):
            try:
                return record_review(*args, **kwargs)
            except ResearchError as error:
                if error.code == 'reading_missing':
                    self.case.mutate(reading.record_reading, self.case.full_note(later, 'later-reading'))
                raise
        with patch.object(review_series, '_read_plan', side_effect=read_cached_plan), \
                patch.dict(cli.OPERATIONS, {'value-review': finish_reading_after_refusal}), \
                patch.object(protocol.transport, 'send_request', side_effect=AssertionError('Unexpected reviewer call')):
            self.assertEqual(run_series(self.case.store, 'initial', emit=rows.append), 0)
        self.assertEqual(rows[-1]['state'], 'done')
        self.assertEqual(self.plan()['state'], 'done')
        self.assertNotIn('review_output_refusal', self.case.read_records())
        self.assertNotIn('review_series_stop', self.case.read_records())


class FocusedReadingHandoffTests(unittest.TestCase):
    def prepare(self):
        from test_review_parts_adjudication_journey import AdjudicationPartsCase
        fixture = AdjudicationPartsCase()
        self.addCleanup(fixture.doCleanups)
        fixture.setUp()
        return fixture

    def test_adjudication_final_hands_off_a_stale_reading_then_records_the_same_output(self):
        fixture = self.prepare()
        case = fixture.case
        assignment = fixture.assign()
        old = case.expected['readings'][0]['reading']
        link = old['inspections'][0]['link']
        output = fixture.final_output()
        output['evidence'] = [dict(kind='source', link=link)]
        fixture.invoke(assignment, output)
        records = case.read_records()
        bundle = {key: copy.deepcopy(value) for key, value in records['source_bundle'][old['bundle_id']].items()
                  if key in ('id', 'version_id', 'source_id', 'scope', 'completeness', 'units',
                             'inventory', 'bibliography', 'resolutions')}
        bundle['id'] = 'focused-new-bundle'
        for key in ('recorded_revision', 'original_sha256', 'sequence'):
            bundle.pop(key, None)
        bundle['units'].append(dict(id='proof', kind='text', required=True, link=link))
        case.mutate(literature.import_bundle, bundle)
        plan = plan_next(case.read_records(), case.artifacts, assignment['id'])
        self.assertEqual((plan['state'], plan['command']), ('handoff', 'read'))
        self.assertEqual(plan['details']['step']['id'], 13)
        case.mutate(reading.record_reading, case.full_note(bundle, 'focused-read'))
        plan = plan_next(case.read_records(), case.artifacts, assignment['id'])
        self.assertEqual((plan['state'], plan['command']), ('record', 'review-adjudication'))
        case.mutate(cli.OPERATIONS[plan['command']], plan['payload'])
        self.assertEqual(plan_next(case.read_records(), case.artifacts, assignment['id'])['state'], 'done')
        # Generic focused reviews retain their earlier recursive artifact grammar.
        packet = copy.deepcopy(fixture.packet(assignment))
        packet['subject'] = dict(kind='study')
        generic = dict(assignment, subject=packet['subject'])
        nested = dict(output, disposition='unresolved', evidence=[[case.supporting]])
        protocol.validate_adjudication_contract(case.read_records(), case.artifacts, generic, packet, nested)


    def test_adjudication_abstract_refusal_is_bounded_and_uses_its_role_handoff(self):
        fixture = self.prepare()
        case = fixture.case
        assignment = fixture.assign()
        link = case.supporting_links[case.supporting['sha256']]
        output = fixture.final_output()
        output['evidence'] = [dict(kind='source', link=link)]
        fixture.invoke(assignment, output)
        plan = plan_next(case.read_records(), case.artifacts, assignment['id'])
        self.assertEqual(plan['state'], 'record')
        with self.assertRaises(ResearchError) as caught:
            case.mutate(protocol.record_adjudication, plan['payload'])
        self.assertEqual(caught.exception.code, 'source_link_not_fulltext')
        self.assertEqual(caught.exception.details['path'], '/evidence/0')
        rows = []
        from codex_route_fixtures import build_codex_response
        with patch.object(protocol.transport, 'send_request', side_effect=lambda route, request, credential:
                          build_codex_response(route, request, output)):
            self.assertEqual(run_series(case.store, assignment['id'], emit=rows.append), 3)
        self.assertEqual(rows[-1]['code'], 'review_slot_exhausted')
        self.assertEqual(rows[-1]['details']['step']['id'], 3)
        step = rows[-1]['details']['step']
        replacement = case.mutate(protocol.record_assignment, dict(step['payload'], id='new-focused',
            route_id='route', reviewer_id='fourth'))['result']
        self.assertEqual(replacement['role'], 'adjudicator')
        fixture.invoke(replacement, fixture.final_output())
        self.assertIn('attempt-new-focused', case.read_records()['review_attempt'])
