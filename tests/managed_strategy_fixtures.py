"""Managed integration fixtures exercise real decisions and observed review receipts.

Only the external reviewer provider is replaced by explicitly authored responses.
These finite examples test software contracts, not scientific reviewer quality.
Bare research preparation and native mathematics do not opt into this workflow.
"""

import copy
import json
from unittest.mock import patch

from research_harness import research_decisions, review_protocol, strategy
from research_harness.evidence import digest
from research_harness.operations import prepared_mutation
from reviewer_fixtures import response as provider_response
from strategy_fixtures import StrategyCase


def managed(case):
    return strategy.managed_research(case.store.snapshot()['records'])


class ManagedStrategy:
    def __init__(self, case):
        self.case, self.store, self.artifacts = case, case.store, case.artifacts
        self.evidence = self.artifacts.put(
            b'Authored fixture: the finite inputs 0, 1, 2, 3 have squares 0, 1, 4, 9. '
            b'The transport examples exercise the admitted command and retain its actual outcome.', 'text/plain')

    def records(self):
        return self.store.snapshot()['records']

    def mutate(self, operation, value):
        return operation(self.store, copy.deepcopy(value), expected_revision=self.store.revision,
                         request_id='managed-fixture-' + str(self.store.revision))['result']

    def ensure_intent(self):
        current = strategy.current_intent(self.records())
        if current is not None:
            return current
        identifier = 'managed-fixture-instruction'
        instruction = self.artifacts.put(
            ('Run the finite software fixture, preserve actual observations, and publish the supported artifact. '
             'Do not claim an untested generalization. Complete objective: ' + self.case.objective['statement']).encode(), 'text/plain')
        record = {'id': identifier, 'path': 'context/managed-fixture.txt', 'artifact': instruction}
        self.mutate(lambda store, value, **identity: prepared_mutation(store, 'test.managed_instruction', value,
            lambda records, payload: ([('local_artifact', identifier, payload)], payload), **identity), record)
        native = self.records().get('research_objective', {}).get(self.case.objective['id'])
        objective = {'kind': 'native', 'id': native['id']} if native else {
            'kind': 'proposed', 'id': 'managed-fixture-proposal', 'statement': self.case.objective['statement'],
            'scope': {}, 'instruction': identifier}
        return self.mutate(strategy.record_intent, {
            'id': 'managed-fixture-intent', 'previous': None, 'task_kind': 'specified_delivery',
            'instruction': identifier, 'recorded_at': '2026-09-30T18:00:00Z',
            'full_objective': self.case.objective['statement'],
            'branch': {'statement': self.case.objective['statement'], 'relation': 'full', 'remaining_obligations': []},
            'objective': objective, 'user_standard': 'Verify the exact finite fixture and disclose its limits.',
            'deliverables': ['A supported finite-result artifact'],
            'publication': {'instruction': identifier, 'endpoint': 'submit', 'required': True, 'quality_condition': None},
            'resources': [{'kind': 'compute', 'limit': None, 'unit': 'execution', 'authorization': None}],
            'scope_changes': [], 'authors': ['fixture-author', 'cycle-author', 'author']})

    def ensure_route(self):
        route_id = 'managed-fixture-route'
        if route_id in self.records().get('review_route', {}):
            return route_id
        self.mutate(review_protocol.record_route, {'id': route_id, 'adapter': 'openai_responses_v1',
            'model': 'fixture-model', 'endpoint': 'https://api.openai.com/v1/responses',
            'configuration': {'max_output_tokens': 10000, 'timeout_seconds': 5}})
        def probe(route, request, credential):
            packet = json.loads(request['input'][0]['content'])
            return provider_response({'allowed_control': packet['allowed_control'],
                'evidence_control': packet['evidence_control'], 'excluded_controls': []})
        with patch('research_harness.review_transport.send_request', side_effect=probe):
            review_protocol.probe_route(self.store, {'id': 'managed-fixture-probe', 'route_id': route_id},
                expected_revision=self.store.revision, request_id='managed-fixture-probe-call', credential='fixture-only')
        return route_id

    def review(self, dossier, reviewer, stage, identifier, bars):
        assignment_id = identifier + '-assignment'
        self.mutate(review_protocol.record_assignment, {'id': assignment_id, 'route_id': self.ensure_route(),
            'dossier_id': dossier['id'], 'role': stage, 'reviewer_id': reviewer,
            'author_id': 'fixture-author', 'context': {}})
        packet = json.loads(self.artifacts.read(self.records()['review_assignment'][assignment_id]['packet']))
        value = StrategyCase.review_response(self, stage)
        value['value'].update(bar_ids=bars, consequence='Establish and preserve the stated finite fixture result.',
            reason='The authored fixture tests the supported finite claim and its actual observed execution.')
        if stage != 'bar':
            value['value']['work_items'] = [{'id': identifier, 'classification': self.records()['research_work_item'][identifier]['payload']['classification'],
                'status': 'accepted', 'reason': 'The retained request is assessed against the fixed finite objective.',
                'evidence': [self.evidence]} for identifier in dossier['payload']['work_items']]
        if stage == 'result':
            support = self.case.review(self.case.execution_payload, identifier='support-' + identifier)
            support['assessor'] = packet['assessor']
            value['support'] = support
        with patch('research_harness.review_transport.send_request', return_value=provider_response(value)):
            review_protocol.invoke_assignment(self.store, {'id': identifier + '-attempt', 'assignment_id': assignment_id},
                expected_revision=self.store.revision, request_id=identifier + '-invoke', credential='fixture-only')
        return self.mutate(research_decisions.record_value_review,
            dict(value, id=identifier, dossier_id=dossier['id'], assignment_id=assignment_id))

    def decide(self, *, phase='prospective', plan=None, bundle=None):
        intent = self.ensure_intent()
        previous = strategy.current_dossier(self.records())
        serial = str(self.store.revision)
        identifier = 'managed-dossier-' + serial
        objective = {'kind': 'native', 'id': self.case.objective['id']} if self.case.objective['id'] in self.records().get('research_objective', {}) else intent['payload']['objective']
        question = (plan or {}).get('question', 'Does exhaustive enumeration attain the proposed finite bound?')
        method = (plan or {}).get('strategy', {}).get('mechanism', 'Exhaustive finite enumeration.')
        limit = (plan or {}).get('resource_limits', {'unit': 'execution', 'max_units': 8})
        candidates = []
        for suffix, approach in (('a', method), ('b', 'Direct finite arithmetic proof.')):
            candidate = StrategyCase.candidate(self, 'managed-candidate-' + suffix, approach)
            candidate.update(question=question, target=self.case.objective['statement'], scope=self.case.objective['statement'], relation='full')
            candidate['next_test'].update(question=question, method=approach)
            candidates.append(candidate)
        tranche = None
        if phase == 'prospective':
            prior_tranches = [record['payload']['tranche'] for record in self.records().get('strategy_dossier', {}).values()
                              if record['payload']['tranche'] is not None]
            predecessor = prior_tranches[-1] if prior_tranches else None
            tranche = {'id': 'managed-tranche-' + serial, 'question': question, 'method': method,
                'limit': {'unit': limit['unit'], 'amount': limit['max_units']}, 'end_condition': 'Assess the exact recorded outputs.',
                'failure_signal': 'A failed process or counterexample prevents the asserted result.',
                'expected_evidence': 'The retained producer output and independent checks.',
                'predecessor': predecessor['id'] if predecessor else None,
                'previous_outcome': 'unresolved' if predecessor else None}
        new_evidence = [self.evidence]
        if phase == 'result':
            candidate = self.case.candidate()
            assessment = self.records()['cycle_assessment'][candidate['assessment_id']]
            new_evidence = [{'kind': 'record', 'record_kind': 'cycle_assessment', 'id': assessment['id'], 'digest': digest(assessment)}]
        elif phase == 'post_measurement':
            from research_harness.contribution import find_analysis
            analysis = find_analysis(self.records(), bundle['digest'])
            new_evidence = [{'kind': 'record', 'record_kind': 'contribution_analysis', 'id': analysis['id'], 'digest': digest(analysis)}]
        elif previous is not None:
            new_evidence = [self.case.source_evidence()] if getattr(self.case, 'links', None) else [self.evidence]
        claims = [] if phase == 'prospective' else [{'id': 'bound', 'statement': self.case.objective['statement'],
            'scope': 'The declared finite input range.', 'evidence': new_evidence}]
        works = [selection['id'] for selection in self.records().get('work_item_selection', {}).values()]
        action = 'investigate' if phase == 'prospective' else 'deliver_requested'
        dossier = self.mutate(strategy.record_strategy, {'id': identifier, 'previous': previous['id'] if previous else None,
            'intent_id': intent['id'], 'phase': phase, 'objective': objective, 'candidates': candidates,
            'selected_candidate': candidates[0]['id'], 'no_branch': 'Preserve the evidence without a further execution.',
            'single_candidate': None, 'claims': claims, 'comparators': [], 'dependencies': [], 'work_items': works,
            'leads': [], 'failures': copy.deepcopy(previous['payload']['failures']) if previous else [],
            'continuity': [{'claim_ids': [claim['id']], 'disposition': 'retained',
                'reason': 'The same supported finite claim remains in scope.', 'evidence': new_evidence}
                for claim in previous['payload']['claims']] if previous else [],
            'material_change': {'kind': 'new_comparison' if phase == 'prospective' else 'new_evidence',
                'reason': 'The actual newly retained sources, result or measurement changes the decision input.',
                'evidence': new_evidence, 'prior_objections': []} if previous else None,
            'reconsideration': None, 'tranche': tranche,
            'recommendation': {'action': action, 'reason': 'Execute or deliver the finite fixture within its actual scope.'},
            'bundle_digest': bundle['digest'] if bundle else None})
        bars = [r['id'] for r in self.records().get('value_review', {}).values() if r['payload']['stage'] == 'bar'
                and self.records()['strategy_dossier'][r['dossier_id']]['payload']['intent_id'] == intent['id']]
        reviewers = ('managed-reviewer-a', 'managed-reviewer-b')
        if not bars:
            for index, reviewer in enumerate(reviewers):
                self.review(dossier, reviewer, 'bar', 'managed-bar-' + str(index), [])
            bars = ['managed-bar-0', 'managed-bar-1']
        reviews = []
        for index, reviewer in enumerate(reviewers):
            review_id = identifier + '-review-' + str(index)
            self.review(dossier, reviewer, 'slate' if phase == 'prospective' else 'result', review_id, bars)
            reviews.append(review_id)
        support = None if phase == 'prospective' else self.records()['value_review'][reviews[0]]['payload']['support']['candidate_digest']
        return self.mutate(research_decisions.record_research_decision, {
            'id': 'managed-decision-' + serial, 'dossier_id': identifier, 'phase': phase, 'action': action,
            'artifact_disposition': 'none' if phase == 'prospective' else 'deliver_under_instruction',
            'goal_status': 'open', 'review_ids': reviews, 'bar_review_ids': bars, 'objections': [],
            'work_items': [{'id': item, 'disposition': 'optional', 'reason': 'Retain the request beyond this explicitly bounded delivery.',
                            'evidence': new_evidence} for item in works],
            'source_impact': {'sources': [], 'aspects': {key: 'unchanged' for key in ('nearest_work', 'scope', 'transfer', 'bar')}},
            'reason': 'The observed fixture findings authorize this exact next boundary.',
            'remaining_objective': ['Record the requested supported artifact and its remaining limits.'],
            'next_action': 'Execute the admitted test.' if phase == 'prospective' else 'Deliver the exact supported artifact.',
            'goal_evidence': [], 'support_candidate_digest': support})


def target_decision(case):
    if not managed(case):
        return None
    return ManagedStrategy(case).decide()


def bind_plan(case, plan):
    if not managed(case):
        return plan
    workflow = ManagedStrategy(case)
    current = strategy.selected(workflow.records(), 'decision', 'research_decision')
    dossier = strategy.current_dossier(workflow.records())
    reusable = (current is not None and dossier is not None and current['payload']['phase'] == 'prospective'
        and dossier['payload']['tranche']['limit'] == {'unit': plan['resource_limits']['unit'], 'amount': plan['resource_limits']['max_units']}
        and dossier['payload']['candidates'][0]['next_test']['question'] == plan['question']
        and dossier['payload']['candidates'][0]['next_test']['method'] == plan['strategy']['mechanism']
        and not strategy.source_delta(workflow.records(), dossier))
    if not reusable:
        current = workflow.decide(plan=plan)
        dossier = strategy.current_dossier(workflow.records())
    return dict(plan, decision_id=current['id'], candidate_id=dossier['payload']['selected_candidate'])


def result_decision(case):
    if not managed(case):
        return None
    report = research_decisions.decision_state(case.store.snapshot()['records'], case.artifacts, 'write')
    return report['decision'] if report['ready'] else ManagedStrategy(case).decide(phase='result')


def publication_decision(case, bundle):
    return ManagedStrategy(case).decide(phase='post_measurement', bundle=bundle)


def measure_publication(case, bundle, suffix):
    """The publication transport fixture has no authored validity deficiency."""
    from integration_fixtures import build_manuscript_review, build_prediction, build_review_core
    from research_harness import predictions, publication
    if predictions.select_measurement_reviews(case.store.snapshot()['records'], bundle) is not None:
        return
    for index in range(3):
        assessor = 'managed-measure-' + suffix + '-' + str(index)
        core = build_review_core()
        core.update(soundness=4, presentation=4)
        core['changes_for_maximum'].update(soundness=[], presentation=[])
        case.mutate(publication.record_manuscript_review, build_manuscript_review(case, bundle, assessor, core=core))
        case.mutate(predictions.record_prediction, build_prediction(case, bundle, assessor))


def record_managed_analysis(case, bundle, suffix):
    from integration_fixtures import build_contribution_analysis
    from research_harness import contribution, predictions
    workflow = ManagedStrategy(case)
    intent = workflow.ensure_intent()
    value = build_contribution_analysis(case, bundle, suffix)
    value.update(steps=[], reviewer_changes=[], investigation=[],
        refresh={'status': 'current', 'reason': 'The finite fixture sources and scientific questions are unchanged.',
                 'evidence': [case.source_evidence()], 'questions': []})
    reviews = predictions.select_measurement_reviews(workflow.records(), bundle)
    for axis in ('soundness', 'presentation', 'contribution'):
        requests = [(review, change) for review in reviews for change in review['core']['changes_for_maximum'][axis]]
        if not requests:
            continue
        stable = 'managed-demand-' + axis
        selected = workflow.records().get('work_item_selection', {}).get(stable)
        previous = None if selected is None else workflow.records()['research_work_item'][selected['id']]
        retained = [] if previous is None else copy.deepcopy(previous['payload']['requests'])
        for review, change in requests:
            retained.append({'origin': 'manuscript_review', 'request_id': contribution.review_request_id(review['id'], change),
                             'reviewer': review['assessor']['id'], 'statement': change})
        identifier = stable + '-' + bundle['id']
        workflow.mutate(strategy.record_work_item, {'id': identifier, 'previous': previous['id'] if previous else None,
            'item_id': stable, 'intent_id': intent['id'], 'requests': retained,
            'classification': 'validity' if axis == 'soundness' else 'optional', 'execution_state': 'planned',
            'claim_ids': ['bound'] if axis == 'soundness' else [], 'disposition': 'deferred',
            'reason': 'Retain this actual reviewer request for a separately admitted follow-up.',
            'evidence': [case.source_evidence()], 'resource_implications': 'No additional execution is claimed.',
            'reopen_trigger': 'A subsequent authorized study takes up this retained request.', 'cycle_ids': []})
        value['reviewer_changes'].extend({'review_id': review['id'], 'change': change, 'disposition': 'rejected',
            'reason': 'The request remains retained outside the current finite delivery.', 'work_item_id': identifier}
            for review, change in requests)
    return workflow.mutate(contribution.record_contribution_analysis, value)
