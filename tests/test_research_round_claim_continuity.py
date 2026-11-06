"""New round boundaries bind revision markers without rewriting historical progress."""

import copy

from research_harness import rounds
from research_harness.evaluation import Evaluation
from rounds_fixtures import RoundsCase


class RoundClaimContinuityTests(RoundsCase):
    def test_invented_previous_refuses_closing_decision_and_current_gate(self):
        _, _, admission = self.open_round()
        self.run_round_work('r2')
        claims = self.claims('wider', revised=('bound',))
        claims[0]['claim'] = 'The maximum over the extended range is 25.'
        claims[0]['revised']['previous'] = 'An invented earlier claim.'
        bundle = self.pin(claims)
        self.write_round_assessment(admission['id'], True, bundle['digest'])
        before = self.store.snapshot()
        payload = self.decision_payload(bundle, closes=2, decision='stop')
        with self.subTest(boundary='admission'):
            self.assert_error('round_claim_revision_mismatch',
                              lambda: self.mutate(rounds.record_round, payload))
            self.assertEqual(self.store.snapshot(), before)
        with self.subTest(boundary='current-gate'):
            report = rounds.round_state(self.store.snapshot()['records'], self.artifacts)
            self.assertIn('round_claim_revision_mismatch', [o['code'] for o in report['obligations']])
            self.assertFalse(report['ready'])
        # Stored progress derivations retain the historical marker interpretation.
        progress = rounds.derive_progress(before['records'], Evaluation(before['records'], self.artifacts),
                                          admission, bundle)
        self.assertEqual(progress['revised_claim_ids'], ['bound'])
        self.assertEqual(progress['changed_claim_ids'], [])

    def test_revision_markers_bind_the_immediate_opening_bundle_across_rounds(self):
        _, _, admission = self.open_round()
        self.run_round_work('r2')
        opening_text = 'The maximum is 9.'
        for label, edits in (
                ('unchanged', {}),
                ('valid', {'claim': 'The maximum is 25.', 'revised': {'previous': opening_text, 'reason': 'A wider range.'}}),
                ('superseded', {'claim': 'An obsolete finite bound.', 'superseded': {'reason': 'Replaced by wider.'}}),
                ('scoped', {'claim': 'The maximum is 9.', 'revised': {'previous': opening_text, 'reason': 'Scope clarified.'}})):
            with self.subTest(label=label):
                claims = self.claims('wider')
                claims[0].update(copy.deepcopy(edits))
                bundle = self.pin(claims)
                report = rounds.round_state(self.store.snapshot()['records'], self.artifacts)
                self.assertNotIn('round_claim_revision_mismatch', [o['code'] for o in report['obligations']])
        # Open round 3 from a valid revised claim; its previous text must be round 2's text.
        claims = self.claims('wider')
        claims[0].update(claim='The maximum is 25.',
                         revised={'previous': opening_text, 'reason': 'A wider range.'})
        bundle = self.pin(claims)
        self.write_round_assessment(admission['id'], True, bundle['digest'])
        payload = self.decision_payload(bundle, closes=2, statement='Extend the finite bound to every integer in [0, 7].')
        _, _, third = self.open_round(bundle, closes=2, payload=payload)
        self.run_round_work('r3')
        claims[0].update(claim='The maximum is 49.',
                         revised={'previous': 'The maximum is 25.', 'reason': 'Another wider range.'})
        third_bundle = self.pin(claims)
        report = rounds.round_state(self.store.snapshot()['records'], self.artifacts)
        self.assertNotIn('round_claim_revision_mismatch', [o['code'] for o in report['obligations']])
        claims[0]['revised']['previous'] = opening_text
        stale = self.pin(claims)
        self.write_round_assessment(third['id'], True, stale['digest'])
        self.assert_error('round_claim_revision_mismatch', lambda: self.mutate(
            rounds.record_round, self.decision_payload(stale, closes=3, decision='stop')))
