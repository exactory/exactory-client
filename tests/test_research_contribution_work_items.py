"""Post-measurement requests remain scientific work, not completed contribution."""

import copy

from rounds_fixtures import RoundsCase
from strategy_fixtures import StrategyCase
from research_harness import contribution, predictions, review_protocol, strategy
from research_harness.evidence import digest


class ContributionWorkItemTests(RoundsCase):
    def managed_analysis(self, *, rejected=False):
        bundle=self.pin(measure=False)
        self.measure(bundle,'managed')
        self.write_record('local_artifact',{'id':'instruction','path':'context/instruction.md','media_type':'text/plain',
            'artifact':self.artifacts.put(b'Investigate the full finite problem and retain every reviewer request.','text/plain')})
        intent=StrategyCase.intent(self)
        intent['objective']={'kind':'native','id':self.objective['id']}
        self.mutate(strategy.record_intent,intent)
        self.evidence=self.links[0]['artifact']
        reviews=predictions.select_measurement_reviews(self.store.snapshot()['records'],bundle)
        payload=self.analysis_payload(bundle,'managed')
        template=payload['steps'][0]
        payload['steps']=[]; payload['reviewer_changes']=[]
        for axis,classification in (('soundness','validity'),('presentation','optional'),('contribution','consequence_critical')):
            work=StrategyCase.work_item(self,'work-'+axis)
            work.update(item_id='stable-'+axis,classification=classification,claim_ids=['bound'],requests=[])
            for review in reviews:
                for change in review['core']['changes_for_maximum'][axis]:
                    work['requests'].append({'origin':'manuscript_review','request_id':review['id']+':'+digest(change),
                        'reviewer':review['assessor']['id'],'statement':change})
                    disposition={'review_id':review['id'],'change':change,'disposition':'rejected' if rejected else 'adopted',
                        'reason':'Keep this as an explicitly planned obligation.','work_item_id':work['id']}
                    if not rejected:
                        disposition['step_id']='step-'+axis
                    payload['reviewer_changes'].append(disposition)
            if rejected:
                work.update(disposition='rejected',evidence=[self.source_evidence()],reopen_trigger='New evidence makes this relevant.')
            self.mutate(strategy.record_work_item,work)
            if not rejected:
                step=dict(copy.deepcopy(template),id='step-'+axis,work_item_id=work['id'])
                payload['steps'].append(step)
        payload['refresh']={'status':'current','reason':'The retained verified sources answer the unchanged scientific questions.',
                            'evidence':[self.source_evidence()],'questions':[]}
        payload['investigation']=[]
        return payload

    def test_current_sources_need_no_new_query_and_adoption_does_not_complete_work(self):
        payload=self.managed_analysis()
        before=copy.deepcopy(self.store.snapshot()['records']['research_work_item'])
        result=self.mutate(contribution.record_contribution_analysis,payload)['result']
        self.assertEqual(result['work_item_ids'],['work-contribution','work-presentation','work-soundness'])
        self.assertEqual(self.store.snapshot()['records']['research_work_item'],before)
        self.assertTrue(all(r['payload']['execution_state']=='planned' for r in before.values()))

    def test_missing_soundness_request_is_not_hidden_by_complete_contribution_dispositions(self):
        payload=self.managed_analysis()
        payload['reviewer_changes']=[r for r in payload['reviewer_changes'] if r['work_item_id']!='work-soundness']
        self.assert_error('reviewer_change_missing',lambda:self.mutate(contribution.record_contribution_analysis,payload))

    def test_wrong_work_item_cannot_absorb_an_unrelated_original_request(self):
        payload=self.managed_analysis()
        payload['reviewer_changes'][0]['work_item_id']='work-contribution'
        self.assert_error('contribution_work_item_mismatch',lambda:self.mutate(contribution.record_contribution_analysis,payload))

    def test_justified_closure_does_not_require_manufacturing_a_step(self):
        payload=self.managed_analysis(rejected=True)
        self.assertEqual(payload['steps'],[])
        self.mutate(contribution.record_contribution_analysis,payload)

    def test_failure_based_pivot_does_not_require_a_positive_inherited_claim(self):
        payload=self.managed_analysis()
        payload['steps'][0].update(builds_on=[],inherited_failures=[self.result_evidence(self.execution_payload)])
        self.mutate(contribution.record_contribution_analysis,payload)

    def test_needed_refresh_cannot_be_declared_complete_without_its_deciding_query(self):
        payload=self.managed_analysis()
        payload['refresh'].update(status='refresh_required',questions=['Does the new comparator settle the bound?'])
        self.assert_error('contribution_refresh_pending',lambda:self.mutate(contribution.record_contribution_analysis,payload))

    def test_later_measurement_contamination_prevents_new_managed_analysis(self):
        payload=self.managed_analysis()
        records=self.store.snapshot()['records']
        review=next(r for r in records['manuscript_review'].values() if r['id'].startswith('measure-'))
        self.mutate(review_protocol.record_context_event,{'id':'late-contamination',
            'assignment_id':review['assignment_id'],'kind':'contamination','source':'author_plans',
            'reason':'The assessor context later received the author recommendation.',
            'evidence':[self.artifacts.put(b'Observed author recommendation in the reviewer context.','text/plain')]})
        self.assert_error('manuscript_measurement_unverified',lambda:self.mutate(contribution.record_contribution_analysis,payload))
        current=self.store.snapshot()['records']
        bundle=next(b for b in current['publication_bundle'].values() if b['digest']==review['bundle_digest'])
        self.assertTrue(predictions.measurement_summary(current,bundle)['complete'])

    def test_invalid_refresh_entry_is_rejected_as_invalid_input(self):
        payload=self.managed_analysis()
        payload['refresh'].update(status='refresh_required',questions=['Does the comparator settle the bound?'])
        payload['investigation']=[None]
        self.assert_error('invalid_contribution_analysis',lambda:self.mutate(contribution.record_contribution_analysis,payload))
