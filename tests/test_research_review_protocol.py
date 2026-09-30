"""Reviewer input boundaries, observed invocation binding, and retained failures."""

import copy
import importlib.util
import json
import unittest
from unittest.mock import patch

from literature_fixtures import LiteratureCase
from research_harness.errors import ResearchError


class ReviewProtocolTests(LiteratureCase):
    def protocol(self):
        self.assertIsNotNone(importlib.util.find_spec('research_harness.review_protocol'),
                             'The independent reviewer protocol is not implemented')
        from research_harness import review_protocol
        return review_protocol

    def seed(self, kind, key, value):
        self.sequence += 1
        self.store.mutate('fixture.seed', {'kind':kind, 'key':key, 'value':value},
            lambda tx: tx.put(kind, key, value), expected_revision=self.store.revision,
            request_id='seed-' + str(self.sequence))

    def records(self):
        return self.store.snapshot()['records']

    def dossier(self):
        evidence = self.artifacts.put(b'The allowed primary source proves a finite bound.', 'text/plain')
        self.seed('research_intent', 'intent', {'id':'intent','payload':{
            'id':'intent','objective':'Determine the optimal bound','field_question':'Which bound is sharp?',
            'resources':{'calls':5},'author_id':'author','prior_scores':'EXCLUDED_SCORE'}})
        self.seed('strategy_dossier', 'dossier', {'id':'dossier','digest':'dossier-digest','payload':{
            'id':'dossier','intent_id':'intent','phase':'prospective',
            'objective':{'statement':'EXCLUDED_PROPOSAL'},
            'candidates':[{'id':'candidate','statement':'EXCLUDED_CANDIDATE','evidence':[evidence]}],
            'selected_candidate':'candidate','claims':[], 'comparators':[evidence],
            'dependencies':[], 'work_items':[], 'leads':[], 'failures':[], 'continuity':[],
            'recommendation':{'action':'EXCLUDED_ACTION','reason':'EXCLUDED_ADVOCACY'}}})
        self.seed('work','source',{'id':'source','title':'Held primary source','abstract':evidence})
        self.seed('reading','reading',{'id':'reading','version_id':'source','depth':'fulltext'})
        return evidence

    def route(self):
        p = self.protocol()
        return self.mutate(p.record_route, {'id':'route','adapter':'openai_responses_v1',
            'model':'fixture-model','endpoint':'https://api.openai.com/v1/responses',
            'configuration':{'max_output_tokens':1000,'timeout_seconds':5}})

    def response(self, output):
        return {'id':'provider-response','model':'fixture-model','status':'completed',
            'output':[{'type':'message','role':'assistant','content':[{'type':'output_text','text':json.dumps(output)}]}],
            'usage':{'input_tokens':37,'output_tokens':12}}

    def run_probe(self, positive=True):
        p = self.protocol()
        def provider(route, request, credential):
            packet = json.loads(request['input'][0]['content'])
            return self.response({'allowed_control':packet['allowed_control'] if positive else 'missing',
                                  'evidence_control':packet['evidence_control'] if positive else 'missing',
                                  'excluded_controls':[]})
        with patch('research_harness.review_transport.send_request', side_effect=provider):
            self.mutate(p.probe_route, {'id':'probe','route_id':'route'}, credential='test-only')

    def assign(self, role='standalone', identifier='assignment', reviewer='reviewer'):
        p = self.protocol()
        payload = {'id':identifier,'route_id':'route','dossier_id':None if role=='standalone' else 'dossier',
            'role':role,'reviewer_id':reviewer,'author_id':'author', 'context':{}}
        if role=='standalone':
            evidence = self.artifacts.put(b'Allowed evidence for an ordinary paper review.', 'text/plain')
            payload['context']={'artifact_refs':[evidence]}
        return self.mutate(p.record_assignment,payload)

    def invoke(self, output, identifier='attempt', assignment='assignment'):
        p = self.protocol()
        with patch('research_harness.review_transport.send_request',return_value=self.response(output)):
            return self.mutate(p.invoke_assignment, {'id':identifier,'assignment_id':assignment},credential='test-only')

    def test_bar_packet_excludes_author_slate_history_and_includes_held_sources(self):
        p = self.protocol(); self.dossier()
        packet = p.build_packet(self.records(),self.artifacts,'dossier','bar','reviewer')
        encoded = json.dumps(packet)
        for secret in ('EXCLUDED_SCORE','EXCLUDED_PROPOSAL','EXCLUDED_CANDIDATE','EXCLUDED_ACTION','EXCLUDED_ADVOCACY'):
            self.assertNotIn(secret,encoded)
        self.assertIn('Determine the optimal bound',encoded)
        self.assertIn('Held primary source',encoded)
        self.assertIn('fulltext',encoded)

    def test_slate_cannot_be_released_before_both_initial_bar_responses(self):
        p = self.protocol(); self.dossier()
        with self.assertRaisesRegex(ResearchError,'bar'):
            p.build_packet(self.records(),self.artifacts,'dossier','slate','reviewer')

    def test_standalone_review_needs_no_strategic_dossier(self):
        p = self.protocol(); self.route(); self.assign()
        state = p.assignment_state(self.records(),self.artifacts,'assignment')
        self.assertFalse(state['ready'])
        self.assertEqual(state['role'],'standalone')
        self.assertIn('review_context_unverified',[o['code'] for o in state['obligations']])

    def test_imported_claim_of_verified_isolation_is_rejected(self):
        p = self.protocol()
        with self.assertRaises(ResearchError):
            self.mutate(p.record_route, {'id':'route','adapter':'external','model':'fixture-model',
                'endpoint':'https://api.openai.com/v1/responses','configuration':{},
                'context_status':'verified_for_route'})

    def test_allowed_positive_controls_are_required_even_when_canary_absent(self):
        p = self.protocol(); self.route(); self.run_probe(False); self.assign(); self.invoke({'verdict':'sound'})
        state = p.assignment_state(self.records(),self.artifacts,'assignment')
        self.assertFalse(state['ready']); self.assertEqual(state['context_status'],'unverified')

    def test_observed_request_binds_prompt_packet_and_output(self):
        p = self.protocol(); self.route(); self.run_probe(); self.assign(); self.invoke({'verdict':'sound'})
        state = p.assignment_state(self.records(),self.artifacts,'assignment')
        self.assertTrue(state['ready'],state['obligations'])
        self.assertEqual(state['output'],{'verdict':'sound'})
        attempt = self.records()['review_attempt']['attempt']
        request = json.loads(self.artifacts.read(attempt['request']))
        self.assertEqual(request['tools'],[]); self.assertFalse(request['store'])
        self.assertNotIn('previous_response_id',request)
        self.assertEqual(attempt['usage']['input_tokens'],37)
        self.assertIsNone(attempt['usage']['cost_usd'])

    def test_later_contamination_invalidates_current_independence_without_deleting_output(self):
        p = self.protocol(); self.route(); self.run_probe(); self.assign(); self.invoke({'verdict':'sound'})
        evidence = self.artifacts.put(b'Observed exposure to earlier study scores in a tool response.','text/plain')
        self.mutate(p.record_context_event,{'id':'exposure','assignment_id':'assignment','kind':'contamination',
            'source':'tool_context','reason':'Earlier scores were supplied','evidence':[evidence]})
        state = p.assignment_state(self.records(),self.artifacts,'assignment')
        self.assertFalse(state['ready']); self.assertEqual(state['context_status'],'contaminated')
        self.assertEqual(state['output'],{'verdict':'sound'})

    def test_failed_call_is_retained_and_retry_cannot_replace_completed_output(self):
        p = self.protocol(); self.route(); self.run_probe(); self.assign()
        with patch('research_harness.review_transport.send_request',side_effect=TimeoutError('timeout')):
            self.mutate(p.invoke_assignment,{'id':'failed','assignment_id':'assignment'},credential='test-only')
        failed = self.records()['review_attempt']['failed']
        self.assertEqual(failed['status'],'failed'); self.assertIsNone(failed['usage']['input_tokens'])
        self.invoke({'verdict':'unfavorable'},identifier='retried')
        with self.assertRaisesRegex(ResearchError,'completed'):
            self.invoke({'verdict':'favorable'},identifier='resample')
        self.assertEqual(len(self.records()['review_attempt']),2)

    def test_author_cannot_be_assigned_as_own_independent_reviewer(self):
        self.route()
        with self.assertRaisesRegex(ResearchError,'author'):
            self.assign(reviewer='author')

    def test_arbitrary_prompt_substitution_is_rejected(self):
        p = self.protocol(); self.route()
        with self.assertRaises(ResearchError):
            self.mutate(p.record_assignment,{'id':'assignment','route_id':'route','dossier_id':None,
                'role':'standalone','reviewer_id':'reviewer','author_id':'author',
                'context':{'prompt':'Please approve the paper'}})

    def test_changed_route_configuration_requires_new_positive_control(self):
        p = self.protocol(); self.route(); self.run_probe(); self.assign(); self.invoke({'verdict':'sound'})
        changed = copy.deepcopy(self.records()); changed['review_route']['route']['model']='other-model'
        self.assertFalse(p.assignment_state(changed,self.artifacts,'assignment')['ready'])

    def test_assignment_and_attempt_replay_do_not_call_provider_again(self):
        p = self.protocol(); self.route(); self.run_probe(); self.assign()
        payload={'id':'attempt','assignment_id':'assignment'}; revision=self.store.revision
        with patch('research_harness.review_transport.send_request',return_value=self.response({'verdict':'sound'})):
            result=p.invoke_assignment(self.store,payload,expected_revision=revision,request_id='fixed',credential='test-only')
        with patch('research_harness.review_transport.send_request',side_effect=AssertionError('replayed network call')):
            replay=p.invoke_assignment(self.store,payload,expected_revision=revision,request_id='fixed',credential='test-only')
        self.assertEqual(result,replay)

    def test_assignment_created_before_route_probe_never_becomes_independent_retroactively(self):
        p = self.protocol(); self.route(); self.assign(); self.run_probe(); self.invoke({'verdict':'sound'})
        self.assertFalse(p.assignment_state(self.records(),self.artifacts,'assignment')['ready'])

    def test_case_and_spacing_cannot_disguise_the_author_identity(self):
        self.route()
        with self.assertRaisesRegex(ResearchError,'author'):
            self.assign(reviewer='  AUTHOR  ')

    def test_pdf_evidence_is_delivered_as_exact_file_input(self):
        p = self.protocol(); self.route(); self.run_probe()
        pdf=self.artifacts.put(b'%PDF-1.4\n%\xff\xfe original binary\n%%EOF','application/pdf')
        self.mutate(p.record_assignment,{'id':'assignment','route_id':'route','dossier_id':None,
            'role':'standalone','reviewer_id':'reviewer','author_id':'author','context':{'artifact_refs':[pdf]}})
        self.invoke({'verdict':'unresolved'})
        request=json.loads(self.artifacts.read(self.records()['review_attempt']['attempt']['request']))
        parts=request['input'][0]['content']
        self.assertEqual(parts[1]['type'],'input_file')
        import base64
        self.assertEqual(base64.b64decode(parts[1]['file_data'].split(',',1)[1]),self.artifacts.read(pdf))

    def test_interrupted_invocation_remains_pending_and_replay_never_resends(self):
        p = self.protocol(); self.route(); self.run_probe(); self.assign()
        payload={'id':'interrupted','assignment_id':'assignment'}; revision=self.store.revision
        class Interrupted(BaseException):
            pass
        with patch('research_harness.review_transport.send_request',side_effect=Interrupted()):
            with self.assertRaises(Interrupted):
                p.invoke_assignment(self.store,payload,expected_revision=revision,request_id='interrupted-request',credential='test-only')
        self.assertIn('interrupted',self.records()['review_invocation'])
        with patch('research_harness.review_transport.send_request',side_effect=AssertionError('must not resend')):
            with self.assertRaisesRegex(ResearchError,'pending'):
                p.invoke_assignment(self.store,payload,expected_revision=revision,request_id='interrupted-request',credential='test-only')

    def test_cas_race_after_network_preserves_observation_without_second_call(self):
        p = self.protocol(); self.route(); self.run_probe(); self.assign()
        def provider(route,request,credential):
            self.seed('fixture_concurrency','writer',{'id':'writer'})
            return self.response({'verdict':'sound'})
        with patch('research_harness.review_transport.send_request',side_effect=provider):
            self.mutate(p.invoke_assignment,{'id':'attempt','assignment_id':'assignment'},credential='test-only')
        self.assertTrue(p.assignment_state(self.records(),self.artifacts,'assignment')['ready'])

    def test_changed_output_artifact_cannot_replace_actual_provider_response(self):
        p = self.protocol(); self.route(); self.run_probe(); self.assign(); self.invoke({'verdict':'unfavorable'})
        changed=copy.deepcopy(self.records())
        changed['review_attempt']['attempt']['output']=self.artifacts.put(b'{"verdict":"favorable"}','application/json')
        self.assertFalse(p.assignment_state(changed,self.artifacts,'assignment')['ready'])

    def test_probe_process_interruption_has_durable_invocation_and_no_silent_retry(self):
        p=self.protocol(); self.route()
        payload={'id':'interrupted-probe','route_id':'route'}; revision=self.store.revision
        class Interrupted(BaseException):
            pass
        with patch('research_harness.review_transport.send_request',side_effect=Interrupted()):
            with self.assertRaises(Interrupted):
                p.probe_route(self.store,payload,expected_revision=revision,request_id='probe-request',credential='test-only')
        self.assertIn('interrupted-probe',self.records().get('review_probe_invocation',{}))
        with patch('research_harness.review_transport.send_request',side_effect=AssertionError('must not resend')):
            with self.assertRaisesRegex(ResearchError,'pending'):
                p.probe_route(self.store,payload,expected_revision=revision,request_id='probe-request',credential='test-only')

    def bar_reviews(self):
        self.dossier(); self.route(); self.run_probe()
        evidence=self.records()['work']['source']['abstract']
        for reviewer in ('first','second'):
            self.assign('bar','bar-'+reviewer,reviewer)
            response={'stage':'bar','support':None,'value':{'consequence':reviewer+' PRIVATE_BAR'},
                      'objections':[{'id':'objection-'+reviewer,'claim':'A supplied inference is unsupported',
                          'reason':'The exact cited bound is weaker','evidence':[evidence],
                          'resolution_condition':'Check the stated quantifier'}], 'limitations':[]}
            self.invoke(response,'bar-attempt-'+reviewer,'bar-'+reviewer)
            self.seed('value_review','review-'+reviewer,{'id':'review-'+reviewer,'payload':dict(response,
                id='review-'+reviewer,dossier_id='dossier',assignment_id='bar-'+reviewer)})
        return evidence

    def test_slate_replays_only_own_canonical_bar_exchange_after_both_are_saved(self):
        p=self.protocol(); self.bar_reviews(); self.assign('slate','slate-first','first')
        self.invoke({'stage':'slate'},'slate-attempt','slate-first')
        request=json.loads(self.artifacts.read(self.records()['review_attempt']['slate-attempt']['request']))
        self.assertEqual([m['role'] for m in request['input']],['user','assistant','user'])
        encoded=json.dumps(request)
        self.assertIn('first PRIVATE_BAR',encoded)
        self.assertNotIn('second PRIVATE_BAR',json.dumps(request['input'][:2]))
        self.assertIn('second PRIVATE_BAR',request['input'][-1]['content'])
        self.assertNotIn('EXCLUDED_ADVOCACY',encoded)
        self.assertIn('EXCLUDED_CANDIDATE',encoded)

    def test_adjudication_cannot_be_reassigned_after_unfavorable_completed_output(self):
        p=self.protocol(); self.bar_reviews()
        payload={'id':'adjudicator','route_id':'route','dossier_id':'dossier','role':'adjudicator',
            'reviewer_id':'third','author_id':'author','context':{'review_ids':['review-first','review-second'],
                'objection_id':'objection-first'}}
        self.mutate(p.record_assignment,payload)
        self.invoke({'objection_id':'objection-first','disposition':'upheld'},'third-attempt','adjudicator')
        changed=dict(payload,id='replacement',reviewer_id='fourth')
        with self.assertRaisesRegex(ResearchError,'adjudicat'):
            self.mutate(p.record_assignment,changed)

    def test_adjudication_requires_fresh_identity_and_preserves_full_initial_assessments(self):
        p=self.protocol(); self.bar_reviews()
        context={'review_ids':['review-first','review-second'],'objection_id':'objection-first'}
        with self.assertRaisesRegex(ResearchError,'assessor'):
            p.build_packet(self.records(),self.artifacts,'dossier','adjudicator','first',**context)
        packet=p.build_packet(self.records(),self.artifacts,'dossier','adjudicator','third',**context)
        encoded=json.dumps(packet)
        self.assertIn('first PRIVATE_BAR',encoded); self.assertIn('second PRIVATE_BAR',encoded)
        self.assertEqual(packet['objection']['id'],'objection-first')
        self.assertNotIn('EXCLUDED_ADVOCACY',encoded)

    def test_new_favorable_source_is_not_a_correction_of_the_old_packet(self):
        p=self.protocol(); self.bar_reviews()
        new=self.artifacts.put(b'A newly acquired source argues the result is important.','text/plain')
        context={'review_ids':['review-first','review-second'],'objection_id':'objection-first',
            'correction':{'kind':'misreading','claim':'New source implies importance','reason':'Revise the assessment',
                'evidence':[new],'prior_adjudication_id':None,'new_error_explanation':None}}
        with self.assertRaisesRegex(ResearchError,'original'):
            p.build_packet(self.records(),self.artifacts,'dossier','adjudicator','third',**context)

    def test_second_distinct_documented_correction_is_permitted_without_quota(self):
        p=self.protocol(); evidence=self.bar_reviews()
        context={'review_ids':['review-first','review-second'],'objection_id':'objection-first',
            'correction':{'kind':'misreading','claim':'The supplied bound concerns all n','reason':'A quantifier was missed',
                'evidence':[evidence],'prior_adjudication_id':None,'new_error_explanation':None}}
        payload={'id':'first-correction','route_id':'route','dossier_id':'dossier','role':'adjudicator',
            'reviewer_id':'third','author_id':'author','context':context}
        self.mutate(p.record_assignment,payload)
        response={'objection_id':'objection-first','disposition':'not_upheld','reason':'The universal quantifier is explicit',
                  'evidence':[evidence],'correction_admissible':True}
        self.invoke(response,'first-correction-attempt','first-correction')
        self.mutate(p.record_adjudication,dict(response,id='correction-result',assignment_id='first-correction'))
        second=copy.deepcopy(payload); second.update(id='second-correction',reviewer_id='fourth')
        second['context']['correction'].update(claim='A supplied number was also misread',reason='The number is 7, not 1',
            prior_adjudication_id='correction-result',new_error_explanation='The earlier correction addressed the quantifier only')
        self.mutate(p.record_assignment,second)
        self.assertIn('second-correction',self.records()['review_assignment'])
        third=copy.deepcopy(second); third.update(id='repeated-correction',reviewer_id='fifth')
        with self.assertRaisesRegex(ResearchError,'correction'):
            self.mutate(p.record_assignment,third)

    def test_slate_delivers_referenced_work_items_and_lead_content(self):
        p=self.protocol(); evidence=self.bar_reviews()
        self.seed('research_work_item','item',{'id':'item','payload':{'id':'item','statement':'Check target transfer',
            'execution_state':'planned','evidence':[evidence]}})
        self.seed('research_lead','lead',{'id':'lead','payload':{'id':'lead','statement':'Earlier discrepancy',
            'evidence':[evidence]}})
        saved=copy.deepcopy(self.records()['strategy_dossier']['dossier'])
        saved['payload'].update(work_items=['item'],leads=[{'id':'lead','candidate_id':'candidate'}])
        self.seed('strategy_dossier','dossier',saved)
        packet=p.build_packet(self.records(),self.artifacts,'dossier','slate','first')
        self.assertIn('Check target transfer',json.dumps(packet))
        self.assertIn('Earlier discrepancy',json.dumps(packet))

    def test_result_without_assessed_candidate_is_operationally_incomplete(self):
        p=self.protocol(); self.bar_reviews()
        with self.assertRaisesRegex(ResearchError,'candidate'):
            p.build_packet(self.records(),self.artifacts,'dossier','result','first')

    def test_prior_assessment_exposure_updates_the_separate_isolation_dimension(self):
        p=self.protocol(); self.route(); self.run_probe(); self.assign(); self.invoke({'verdict':'sound'})
        evidence=self.artifacts.put(b'The startup input included prior assessment scores.','text/plain')
        self.mutate(p.record_context_event,{'id':'exposure','assignment_id':'assignment','kind':'contamination',
            'source':'prior_assessments','reason':'Prior score supplied','evidence':[evidence]})
        state=p.assignment_state(self.records(),self.artifacts,'assignment')
        self.assertEqual(state['prior_assessment_isolation'],'contaminated')

    def test_latest_failed_positive_control_invalidates_current_route_verification(self):
        p=self.protocol(); self.route(); self.run_probe(); self.assign(); self.invoke({'verdict':'sound'})
        with patch('research_harness.review_transport.send_request',return_value=self.response({
            'allowed_control':'not-received','evidence_control':'not-received','excluded_controls':[]})):
            self.mutate(p.probe_route,{'id':'later-probe','route_id':'route'},credential='test-only')
        self.assertFalse(p.assignment_state(self.records(),self.artifacts,'assignment')['ready'])

    def test_contamination_event_requires_actual_evidence_not_only_a_sentence(self):
        p=self.protocol(); self.route(); self.assign()
        with self.assertRaises(ResearchError):
            self.mutate(p.record_context_event,{'id':'event','assignment_id':'assignment','kind':'contamination',
                'source':'startup_memory','reason':'A reported issue','evidence':['No evidence artifact supplied']})

    def test_adjudication_current_state_rechecks_later_context_exposure(self):
        p=self.protocol(); evidence=self.bar_reviews()
        self.mutate(p.record_assignment,{'id':'adjudicator','route_id':'route','dossier_id':'dossier','role':'adjudicator',
            'reviewer_id':'third','author_id':'author','context':{'review_ids':['review-first','review-second'],
                'objection_id':'objection-first'}})
        response={'objection_id':'objection-first','disposition':'not_upheld','reason':'The original evidence settles it',
                  'evidence':[evidence],'correction_admissible':None}
        self.invoke(response,'adjudicator-attempt','adjudicator')
        self.mutate(p.record_adjudication,dict(response,id='adjudication',assignment_id='adjudicator'))
        self.assertTrue(hasattr(p,'adjudication_state'),'Current adjudication independence needs a derived state')
        self.assertTrue(p.adjudication_state(self.records(),self.artifacts,'adjudication')['ready'])
        self.mutate(p.record_context_event,{'id':'event','assignment_id':'adjudicator','kind':'contamination',
            'source':'startup_memory','reason':'Forbidden study history was exposed','evidence':[evidence]})
        self.assertFalse(p.adjudication_state(self.records(),self.artifacts,'adjudication')['ready'])

    def test_new_assignment_id_cannot_resample_a_completed_initial_reviewer(self):
        self.bar_reviews()
        with self.assertRaisesRegex(ResearchError,'completed'):
            self.assign('bar','replacement-initial','first')

    def test_third_initial_reviewer_cannot_replace_either_fixed_decision_slot(self):
        self.bar_reviews()
        with self.assertRaisesRegex(ResearchError,'two'):
            self.assign('bar','third-bar','third')

    def test_unfinished_prior_invocation_keeps_unknown_usage_in_accounting(self):
        p=self.protocol(); self.route(); self.run_probe(); self.assign()
        class Interrupted(BaseException):
            pass
        with patch('research_harness.review_transport.send_request',side_effect=Interrupted()):
            with self.assertRaises(Interrupted):
                self.mutate(p.invoke_assignment,{'id':'pending','assignment_id':'assignment'},credential='test-only')
        self.assertTrue(hasattr(p,'usage_report'),'Pending, failed, and completed calls need one observable envelope report')
        report=p.usage_report(self.records())
        self.assertEqual(report['calls'],2)
        self.assertEqual(report['pending_calls'],1)
        self.assertEqual(report['unknown']['cost_usd'],2)

    def test_inadmissible_correction_is_preserved_without_clearing_the_finding(self):
        p=self.protocol(); evidence=self.bar_reviews()
        correction={'kind':'misreading','claim':'The supplied quantifier was allegedly missed','reason':'Check the wording',
            'evidence':[evidence],'prior_adjudication_id':None,'new_error_explanation':None}
        self.mutate(p.record_assignment,{'id':'adjudicator','route_id':'route','dossier_id':'dossier','role':'adjudicator',
            'reviewer_id':'third','author_id':'author','context':{'review_ids':['review-first','review-second'],
                'objection_id':'objection-first','correction':correction}})
        response={'objection_id':'objection-first','disposition':'not_upheld','reason':'The requested correction is not demonstrated',
                  'evidence':[evidence],'correction_admissible':False}
        self.invoke(response,'adjudicator-attempt','adjudicator')
        self.mutate(p.record_adjudication,dict(response,id='adjudication',assignment_id='adjudicator'))
        self.assertFalse(p.adjudication_state(self.records(),self.artifacts,'adjudication')['ready'])
        self.assertIn('adjudication',self.records()['review_adjudication'])

    def test_manuscript_packet_supplies_frozen_cohort_without_prior_scores_or_author_plans(self):
        p=self.protocol()
        self.seed('collection','cohort',{'id':'cohort','definition':{'corpus':'arxiv','primaryCategory':'cs.LG',
            'windowStart':'2026-01-01','windowEnd':'2026-01-31'},'prior_scores':'FORBIDDEN_COHORT_SCORE'})
        self.seed('literature_scope','research',{'collection_ids':['cohort']})
        paper=self.artifacts.put(b'A bounded scientific manuscript.','text/plain')
        bundle={'id':'paper','digest':'exact-paper','files':{'pdf':{'artifact':paper}},'claim_evidence':[],
            'execution_observations':{},'review_inputs':{'synthesis':{'sections':{'standards':{'payload':{
                'scientific_requirement':'State exact assumptions'}}},'foundation':{},'history':'FORBIDDEN_HISTORY'}},
            'author_plans':'FORBIDDEN_PLAN'}
        self.seed('publication_bundle','paper',bundle)
        packet=p.build_packet(self.records(),self.artifacts,None,'manuscript','reviewer',bundle=bundle)
        self.assertEqual(packet.get('prediction_context'),{'corpus':'arxiv','category':'cs.LG',
            'windowStart':'2026-01-01','windowEnd':'2026-01-31'})
        self.assertIn('State exact assumptions',json.dumps(packet))
        for secret in ('FORBIDDEN_COHORT_SCORE','FORBIDDEN_HISTORY','FORBIDDEN_PLAN'):
            self.assertNotIn(secret,json.dumps(packet))
