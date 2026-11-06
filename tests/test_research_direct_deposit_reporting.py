"""Confirmed external outcomes remain distinct from managed publication gates."""

from research_harness import publication
from research_harness.cli import status_report
from research_harness.gates import gate_report
from research_harness.integration import record_direct_deposit, update_study
from research_harness.report_views import next_summary, status_summary
from rounds_fixtures import RoundsCase
from source_limited_fixtures import SourceLimitedCase


class DirectDepositReportingTests(RoundsCase):
    def record_deposit(self, **changes):
        state = {'environment': 'production', 'deposition_id': 4242,
                 'draft_url': 'https://zenodo.org/deposit/4242',
                 'doi': '10.5281/zenodo.4242', 'record_url': 'https://zenodo.org/records/4242'}
        state.update(changes)
        self.mutate(record_direct_deposit, state)
        return state

    def test_confirmed_deposit_names_manuscript_review_wait_without_gate_credit(self):
        bundle = self.pin(reviews=0)
        self.approve(self.decision_payload(bundle, decision='stop'))
        self.assertTrue(gate_report(self.store, 'round')['ready'])
        self.mutate(publication.record_manuscript_review,
                    self.manuscript_review(bundle, 'unresolved-manuscript', decision='reject'))
        self.set_stage('evaluate')
        gate_before = gate_report(self.store, 'publication')
        self.assertIn('manuscript_reviews_required', [o['code'] for o in gate_before['obligations']])
        state = self.record_deposit()
        before = self.store.snapshot()
        report = status_report(self.store)
        self.assertTrue('external_deposit' in report)
        self.assertEqual(report['external_deposit']['status'], 'published')
        self.assertEqual(report['external_deposit']['doi'], state['doi'])
        self.assertTrue(report['external_deposit']['current_bundle'])
        self.assertFalse(report['external_deposit']['managed_credit'])
        self.assertEqual(report['study']['stage'], 'evaluate')
        self.assertEqual(report['managed_wait']['code'], 'manuscript_reviews_required')
        self.assertEqual(report['next']['code'], 'manuscript_reviews_required')
        self.assertNotIn('deposit', report.get('publication_commands', {}))
        for view in (next_summary(report), status_summary(report)):
            self.assertEqual(view['external_deposit']['status'], 'published')
            self.assertEqual(view['external_deposit']['doi'], state['doi'])
            self.assertEqual(view['managed_wait']['code'], 'manuscript_reviews_required')
            self.assertEqual(view['next_hint']['code'], 'manuscript_reviews_required')
            self.assertFalse(view['ready'])
        self.assertEqual(self.store.snapshot(), before)
        self.assertEqual(gate_report(self.store, 'publication')['obligations'], gate_before['obligations'])
        self.assert_error('readiness_required', lambda: self.mutate(update_study, {'stage': 'deposit'}))

    def test_sandbox_and_open_deposits_do_not_claim_production_publication(self):
        self.pin(reviews=0, measure=False)
        self.set_stage('evaluate')
        previous = status_report(self.store)
        for changes, status in (({'environment': 'sandbox'}, 'sandbox_published'),
                                ({'doi': '', 'record_url': ''}, 'draft')):
            with self.subTest(changes=changes):
                self.record_deposit(**changes)
                report = status_report(self.store)
                self.assertIn('external_deposit', report)
                self.assertEqual(report['external_deposit']['status'], status)
                self.assertFalse(report['external_deposit']['managed_credit'])
                self.assertFalse(report['ready'])
                self.assertEqual(report['next'], previous['next'])
                self.assertEqual(report['obligations'], previous['obligations'])

    def test_lost_or_missing_confirmation_does_not_claim_publication(self):
        from research_harness.remote import begin_intent, remote_step
        bundle = self.pin(reviews=0, measure=False)
        self.set_stage('evaluate')
        self.assertNotIn('external_deposit', status_report(self.store))
        self.record_deposit(doi='', record_url='')
        report = status_report(self.store)
        self.assertIn('external_deposit', report)
        self.assertEqual(report['external_deposit']['status'], 'draft')
        self.assertFalse(report['ready'])
        # A publication intent with no confirmed answer adds no published outcome.
        binding = {'bundle_digest': bundle['digest'], 'prepared_revision': bundle['prepared_revision'],
                   'base_url': 'https://zenodo.org/api', 'environment': 'production',
                   'new_version': False, 'publish': True,
                   'metadata': {'title': 'Fixture', 'description': 'Synthetic fixture manuscript.'},
                   'uploads': [{'name': 'paper.pdf', 'artifact': bundle['files']['pdf']['artifact']}],
                   'prior': None}
        intent = begin_intent(self.store, 'deposit', binding, expected_revision=self.store.revision,
                              request_id='lost-publish')
        calls = []

        def lose_response():
            calls.append('publish')
            raise OSError('Synthetic lost publish response')

        with self.assertRaises(OSError):
            remote_step(self.store, intent['id'], 'publish',
                        {'method': 'POST', 'url': 'https://zenodo.org/api/deposit/depositions/4242/actions/publish'},
                        lose_response)
        report = status_report(self.store)
        self.assertEqual(report['external_deposit']['status'], 'draft')
        self.assertEqual(report['remote_intents'][0]['status'], 'in_flight')
        self.assertEqual(report['remote_intents'][0]['pending']['name'], 'publish')
        self.assertEqual(calls, ['publish'])

    def test_later_bundle_keeps_external_outcome_without_current_bundle_credit(self):
        first = self.pin(reviews=0)
        self.record_deposit()
        self.recandidate('later')
        claims = self.claims('additional')
        second = self.pin(claims, reviews=0, measure=False)
        self.set_stage('evaluate')
        self.assertNotEqual(first['digest'], second['digest'])
        report = status_report(self.store)
        self.assertEqual(report['external_deposit']['status'], 'published')
        self.assertEqual(report['external_deposit']['bundle_digest'], first['digest'])
        self.assertFalse(report['external_deposit']['current_bundle'])
        self.assertFalse(report['ready'])
        self.record_deposit()
        self.assertEqual(status_report(self.store)['external_deposit']['bundle_digest'], first['digest'])

    def test_changed_measured_files_do_not_bind_or_match_the_external_deposit(self):
        self.pin(reviews=0)
        self.set_stage('evaluate')
        self.record_deposit()
        (self.root / 'draft/paper.pdf').write_bytes(b'%PDF-1.4 changed after measurement\n%%EOF')
        report = status_report(self.store)
        self.assertFalse(report['external_deposit']['current_bundle'])
        self.record_deposit(deposition_id=4343, draft_url='https://zenodo.org/deposit/4343')
        report = status_report(self.store)
        self.assertIsNone(report['external_deposit']['bundle_digest'])
        self.assertFalse(report['external_deposit']['current_bundle'])
        self.assertEqual(report['external_deposit']['status'], 'published')

    def test_historical_acceptances_without_assignments_name_the_manuscript_wait(self):
        from research_harness.evidence import digest
        self.pin(reviews=0)
        records = self.store.snapshot()['records']
        for saved in list(records['manuscript_review'].values()):
            payload = {key: value for key, value in saved.items()
                       if key not in ('assignment_id', 'core', 'reviewed_revision', 'digest')}
            payload['id'] = 'historical-' + payload['id']
            historical = dict(payload, core=saved['core'], reviewed_revision=self.store.revision,
                              digest=digest(payload))
            self.write_record('manuscript_review', historical)
        self.set_stage('evaluate')
        gate = gate_report(self.store, 'publication')
        codes = [item['code'] for item in gate['obligations']]
        self.assertIn('review_assignment_missing', codes)
        self.assertNotIn('manuscript_reviews_required', codes)
        self.record_deposit()
        report = status_report(self.store)
        self.assertTrue('managed_wait' in report)
        self.assertEqual(report['managed_wait']['workflow'], 'manuscript-review')
        self.assertEqual(report['managed_wait']['code'], 'review_assignment_missing')
        for view in (next_summary(report), status_summary(report)):
            self.assertEqual(view['managed_wait']['workflow'], 'manuscript-review')
            self.assertEqual(view['next_hint']['code'], 'review_assignment_missing')
        self.assertEqual(gate_report(self.store, 'publication')['obligations'], gate['obligations'])


class ScopedDirectDepositReportingTests(SourceLimitedCase):
    def test_confirmed_scoped_deposit_stops_recommending_the_same_deposit(self):
        self.prepare_delivery()
        bundle = self.scoped_manuscript()
        previous = status_report(self.store)
        self.assertIn('deposit', previous['publication_commands'])
        state = {'environment': 'production', 'deposition_id': 4242,
                 'draft_url': 'https://zenodo.org/deposit/4242',
                 'doi': '10.5281/zenodo.4242', 'record_url': 'https://zenodo.org/records/4242'}
        self.mutate(record_direct_deposit, state)
        report = status_report(self.store)
        self.assertEqual(report['external_deposit']['bundle_digest'], bundle['digest'])
        self.assertTrue(report['external_deposit']['current_bundle'])
        self.assertNotIn('deposit', report['publication_commands'])
        self.assertEqual(report['publication_commands']['submit'], previous['publication_commands']['submit'])
        self.assertEqual(report['obligations'], previous['obligations'])
