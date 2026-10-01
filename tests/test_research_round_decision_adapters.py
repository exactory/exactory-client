"""Legacy round command names consume the same current scientific decision."""

from strategy_fixtures import StrategyCase
from research_harness.errors import ResearchError


class CanonicalRoundAdapterTests(StrategyCase):
    def ready_decision(self, **kwargs):
        self.reviewed_dossier(**kwargs)
        return self.mutate(self.module('research_decisions').record_research_decision,self.decision())['result']

    def test_round_state_is_ready_before_a_manuscript_when_the_canonical_investigation_is_ready(self):
        self.ready_decision()
        rounds=self.module('rounds')
        report=rounds.round_state(self.store.snapshot()['records'],self.artifacts)
        self.assertTrue(report['ready'],report['obligations'])
        self.assertEqual(report['decision_id'],'decision-1')
        self.assertEqual(report['decision'],'continue')
        self.assertEqual(report['research_decision']['decision']['id'],'decision-1')

    def test_three_round_commands_record_only_adapters_over_one_decision(self):
        self.ready_decision(); rounds=self.module('rounds')
        for name in ('record_round','record_round_review','admit_round'):
            saved=self.mutate(getattr(rounds,name),{'id':name,'research_decision_id':'decision-1',
                'reason':'Use the same independently assessed bounded investigation.'})['result']
            self.assertEqual(saved['research_decision_id'],'decision-1')
        records=self.store.snapshot()['records']
        self.assertEqual(len(records['research_decision']),1)
        self.assertNotIn('round_review',records)
        self.assertNotIn('round_admission',records)
        self.assertEqual(len(records['round_adapter']),3)

    def test_round_alias_cannot_approve_a_failed_canonical_assurance(self):
        response=self.review_response()
        next(a for a in response['value']['assurances'] if a['kind']=='feasibility')['status']='failed'
        self.ready_decision(response_b=response)
        rounds=self.module('rounds')
        before=self.store.revision
        with self.assertRaises(ResearchError) as caught:
            self.mutate(rounds.admit_round,{'id':'admission','research_decision_id':'decision-1','reason':'Try to continue.'})
        self.assertEqual(caught.exception.code,'research_decision_required')
        self.assertEqual(self.store.revision,before)

    def test_old_active_round_does_not_force_unrelated_new_queries_after_managed_migration(self):
        self.ready_decision()
        self.put('round_admission','historical',{'id':'historical','number':2,'decision_id':'historical-decision',
            'opening':{},'admitted_revision':0})
        rounds=self.module('rounds')
        self.assertIsNone(rounds.active_round(self.store.snapshot()['records']))
        self.assertIn('historical',self.store.snapshot()['records']['round_admission'])
        self.assertTrue(rounds.round_state(self.store.snapshot()['records'],self.artifacts)['ready'])

    def test_round_adapter_does_not_create_a_new_tranche_or_resource_budget(self):
        self.ready_decision(); records=self.store.snapshot()['records']
        tranche=records['strategy_dossier']['dossier-1']['payload']['tranche']
        self.mutate(self.module('rounds').admit_round,{'id':'admission','research_decision_id':'decision-1','reason':'Execute the approved test.'})
        records=self.store.snapshot()['records']
        self.assertEqual(records['strategy_dossier']['dossier-1']['payload']['tranche'],tranche)
        self.assertNotIn('resource_account',records)

    def test_round_gate_and_direct_decision_have_identical_current_obligations(self):
        self.ready_decision()
        self.mutate(self.module('review_protocol').record_context_event,{'id':'exposure',
            'assignment_id':'assignment-slate-b','kind':'contamination','source':'startup_memory',
            'reason':'Earlier scores arrived through startup context','evidence':[self.evidence]})
        records=self.store.snapshot()['records']
        direct=self.module('research_decisions').decision_state(records,self.artifacts,'round')
        old=self.module('rounds').round_state(records,self.artifacts)
        self.assertEqual(old['obligations'],direct['obligations'])

    def test_round_assess_requires_actual_outcome_evidence_beyond_prospective_approval(self):
        self.ready_decision()
        with self.assertRaises(ResearchError) as caught:
            self.mutate(self.module('rounds').assess_round,{'id':'premature','research_decision_id':'decision-1',
                'reason':'The planned result is expected.'})
        self.assertEqual(caught.exception.code,'invalid_round')

    def test_failed_tranche_is_recorded_factually_without_a_new_strategic_approval(self):
        self.ready_decision()
        self.put('cycle_plan','cycle',{'id':'cycle','research_decision':{'id':'decision-1'}})
        self.put('cycle','cycle',{'id':'cycle','status':'failed','assessment_id':'assessment'})
        assessment={'id':'assessment','payload':{'cycle_id':'cycle','failures':[{'status':'observed'}]},
                    'artifact':self.evidence}
        self.put('cycle_assessment','assessment',assessment)
        from research_harness.evidence import digest
        evidence={'kind':'record','record_kind':'cycle_assessment','id':'assessment','digest':digest(assessment)}
        result=self.mutate(self.module('rounds').assess_round,{'id':'factual','research_decision_id':'decision-1',
            'reason':'The admitted discriminator failed as recorded.','outcome':'failed','cycle_ids':['cycle'],
            'evidence':[evidence]})['result']
        self.assertEqual(result['outcome'],'failed')
        self.assertIn('factual',self.store.snapshot()['records']['round_factual_assessment'])
        self.assertEqual(len(self.store.snapshot()['records']['research_decision']),1)
