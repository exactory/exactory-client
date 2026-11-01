"""Authenticated discoveries expand task contexts without replacing frozen originals."""

import copy
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from research_harness.artifacts import ArtifactStore
from research_harness import review_protocol as protocol
from research_harness import review_staged as staged
from research_harness import review_staged_protocol as producer
from research_harness.errors import ResearchError
from research_harness.evidence import digest
from test_review_staged_fixtures import epoch, full_card, observation, selection, task


class StagedContextTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.artifacts = ArtifactStore(Path(temporary.name))
        self.epoch = epoch(route_id='route')
        self.before = copy.deepcopy(self.epoch)
        claims = copy.deepcopy(self.epoch['manifest']['claims'])
        claims[0]['dependencies'] = [claims[1]['id']]
        self.contexts = staged.build_contexts(claims, self.epoch['manifest']['sources'])
        assigned = task(self.epoch)
        output = dict(cards=[full_card(self.epoch)], new_dependencies=[
            dict(claim_id=claims[0]['id'], depends_on=claims[1]['id'])])
        captured = observation(self.epoch, assigned, output)
        self.discovery = captured
        self.route = dict(id='route', adapter='codex_cli_v1', model='fixture', endpoint='codex://local',
                          configuration=dict(max_output_tokens=10000, timeout_seconds=600))
        self.controller = dict(id=self.epoch['controller_assignment_id'], route_id='route',
                               packet=protocol._put_json(self.artifacts, {}))
        child = dict(self.controller, id='discoverer', staged_task=assigned,
                     staged_review=dict(kind='task', epoch_id=self.epoch['id']))
        self.records = dict(review_route={'route':self.route}, review_assignment={'discoverer':child},
            review_epoch={self.epoch['id']:self.epoch},
            review_task={assigned['id']:dict(epoch_id=self.epoch['id'], task=assigned)},
            review_task_acceptance={'discovery':dict(task_id=assigned['id'], receipt=dict(
                attempt=dict(id=captured['id'], assignment_id=child['id'], recorded_revision=1),
                staged_observation=captured))})
        active = patch.object(producer, 'authenticate_observation', return_value=True)
        active.start()
        self.addCleanup(active.stop)

    def expanded_task(self, kind='read', **changes):
        return task(self.epoch, kind, contexts=self.contexts,
                    dependencies=[self.discovery['task_id']], **changes)

    def plan(self, assigned):
        return producer.prepare_task_plan(self.records, self.artifacts, self.controller,
                                          self.epoch, assigned)

    def test_planned_expanded_read_uses_current_roots_and_keeps_exact_epoch(self):
        assigned = self.expanded_task()
        planned = self.plan(assigned)
        packet = protocol._read_json(self.artifacts, planned['packet'])
        roots = {row['id']:row['context_root'] for row in packet['staged_review']['claims']}
        self.assertEqual(roots, assigned['context_roots'])
        self.assertNotEqual(roots, {key:row['root'] for key,row in self.epoch['manifest']['contexts'].items()})
        self.assertEqual(self.epoch, self.before)
        self.assertEqual(planned['task']['epoch_digest'], self.epoch['digest'])

    def accept_expanded(self, siblings=()):
        planned = self.plan(self.expanded_task())
        card = full_card(self.epoch)
        for cell in card['judgments']:
            cell['context_root'] = self.contexts[cell['claim_id']]['root']
        captured = dict(reservation_id='expanded-reservation', recorded_revision=2,
            observation=dict(status='completed', request=planned['request'],
                             output=protocol._put_json(self.artifacts, dict(cards=[card] + list(siblings)))))
        assignment = dict(id='expanded-reader', staged_task=planned['task'],
                          packet_digest='packet', prompt_hash='prompt',
                          staged_review=dict(kind='task', epoch_id=self.epoch['id']))
        with patch.object(producer, 'matches_currency', return_value=True):
            receipt = producer.accept_capture(self.records, self.artifacts, assignment,
                                              captured, 'expanded-attempt')
        return receipt, assignment, card

    def test_expanded_read_acceptance_uses_current_contexts(self):
        receipt, _, card = self.accept_expanded()
        self.assertEqual(receipt['staged_observation']['output']['cards'], [card])
        self.assertEqual(receipt['staged_observation']['actual_input']['context_roots'],
                         {key:row['root'] for key,row in self.contexts.items()})
        self.assertEqual(self.epoch, self.before)

    def test_accepted_read_with_malformed_sibling_card_returns_completion_rejection(self):
        for identity in ({}, {'id': ''}, {'id': None}, {'id': []}):
            with self.subTest(identity=identity):
                sibling = dict(identity, unresolved=[dict(text='Was a hypothesis omitted?',
                    claim_ids=list(self.contexts), needs='The original hypothesis.')])
                receipt, assignment, card = self.accept_expanded([sibling])
                observed = receipt['staged_observation']
                self.assertEqual(observed['output']['cards'], [card, sibling])
                tasks = [self.records['review_task'][self.discovery['task_id']]['task'],
                         assignment['staged_task']]
                report = staged.verify_completion(self.epoch,
                    {'observations': [self.discovery, observed]}, tasks=tasks)
                self.assertFalse(report['complete'])
                self.assertTrue(any(row['code'] == 'invalid_review_staged'
                    and row.get('task_id') == observed['task_id'] for row in staged.iter_completion_issues(
                        self.epoch, {'observations': [self.discovery, observed]}, tasks=tasks)), report['issues'])
                self.assertEqual(report['progress']['full_cards'], 1)

    def test_malformed_retrieval_cannot_poison_an_accepted_read_frontier(self):
        from test_review_staged_followups import request
        for invalid in ('owner', 'array', 'root'):
            with self.subTest(invalid=invalid):
                planned = self.plan(self.expanded_task())
                card = full_card(self.epoch)
                for cell in card['judgments']:
                    cell['context_root'] = self.contexts[cell['claim_id']]['root']
                retrieval = request(planned['task'])
                if invalid == 'owner':
                    retrieval['requested_by'] = 'missing-requester'
                elif invalid == 'root':
                    retrieval['context_roots'] = {key:'forged-root' for key in retrieval['claim_ids']}
                raw = dict(cards=[card], retrievals='invalid' if invalid == 'array' else [retrieval])
                captured = dict(reservation_id='expanded-reservation', recorded_revision=2,
                    observation=dict(status='completed', request=planned['request'],
                                     output=protocol._put_json(self.artifacts, raw)))
                assignment = dict(id='expanded-reader', staged_task=planned['task'],
                    packet_digest='packet', prompt_hash='prompt',
                    staged_review=dict(kind='task', epoch_id=self.epoch['id']))
                with patch.object(producer, 'matches_currency', return_value=True):
                    with self.assertRaises(ResearchError) as failure:
                        producer.accept_capture(self.records, self.artifacts, assignment, captured, 'expanded-attempt')
                self.assertEqual(failure.exception.code, 'invalid_review_staged')

    def test_expanded_card_survives_read_to_combine_projection(self):
        receipt, assignment, card = self.accept_expanded()
        observed = receipt['staged_observation']
        self.records['review_assignment'][assignment['id']] = assignment
        self.records['review_task'][observed['task_id']] = dict(epoch_id=self.epoch['id'], task=assignment['staged_task'])
        self.records['review_task_acceptance']['expanded'] = dict(task_id=observed['task_id'], receipt=receipt)
        verified = staged.collect_planning_records(self.epoch,
            {'observations':[self.discovery, observed]},
            tasks=[row['task'] for row in self.records['review_task'].values()])
        self.assertEqual([row['id'] for row in verified['cards']], [card['id']])
        assigned = task(self.epoch, 'combine', contexts=self.contexts,
                        dependencies=[observed['task_id']], card_ids=[card['id']], scope='joint_card')
        planned = self.plan(assigned)
        packet = protocol._read_json(self.artifacts, planned['packet'])
        self.assertEqual([row['id'] for row in packet['staged_review']['combine']['cards']], [card['id']])
        self.assertEqual(packet['staged_review']['combine']['cards'][0]['full_card_root'], digest(card))
        self.assertEqual(self.epoch, self.before)

    def test_original_context_task_is_stale_after_authenticated_discovery(self):
        with self.assertRaises(ResearchError) as failure:
            self.plan(task(self.epoch))
        self.assertEqual(failure.exception.code, 'review_task_dependency_changed')

    def test_unverified_discovery_cannot_authorize_expanded_context(self):
        with patch.object(producer, 'authenticate_observation', return_value=False):
            with self.assertRaises(ResearchError) as failure:
                self.plan(self.expanded_task())
        self.assertEqual(failure.exception.code, 'review_invocation_unverified')

    def recovered_selection(self, unresolved=()):
        card = full_card(self.epoch)
        card['unresolved'] = list(unresolved)
        for cell in card['judgments']:
            cell['context_root'] = self.contexts[cell['claim_id']]['root']
        tasks, selected = selection(self.epoch, [card], contexts=self.contexts)
        tasks.append(self.records['review_task'][self.discovery['task_id']]['task'])
        selected['observations'].append(self.discovery)
        return tasks, selected

    def test_recovered_context_completes_with_original_discovery_retained(self):
        tasks, selected = self.recovered_selection()
        report = staged.verify_completion(self.epoch, selected, tasks=tasks)
        self.assertTrue(report['complete'], report['issues'])
        self.assertEqual(report['context_roots'], {key:row['root'] for key,row in self.contexts.items()})
        self.assertEqual(self.epoch, self.before)

    def test_missing_historical_card_context_version_returns_completion_rejection(self):
        tasks, selected = self.recovered_selection()
        check = staged._check_completion_observations

        def lose_historical_version(work):
            check(work)
            self.assertEqual(len(work['historical_observed']), 1)
            # Exercise the card collection boundary if its version disappears
            # after the earlier observation validation has consumed it.
            work['context_graph']['versions'] = work['context_graph']['versions'][1:]

        with patch.object(staged, '_check_completion_observations', side_effect=lose_historical_version):
            report = staged.verify_completion(self.epoch, selected, tasks=tasks)
            rows = list(staged.iter_completion_issues(self.epoch, selected, tasks=tasks))
        self.assertFalse(report['complete'])
        self.assertTrue(any(row['code'] == 'review_task_dependency_changed'
            and row.get('task_id') == self.discovery['task_id']
            and row.get('reason') == 'Historical task has no recorded context version.'
            for row in rows), report['issues'])

    def test_stale_scientific_work_alone_stays_incomplete(self):
        assigned = self.records['review_task'][self.discovery['task_id']]['task']
        report = staged.verify_completion(self.epoch, {'observations':[self.discovery]}, tasks=[assigned])
        self.assertFalse(report['complete'])
        self.assertEqual(report['progress']['full_cards'], 0)
        self.assertEqual(report['progress']['claim_cells'], 0)

    def test_historical_discovery_still_requires_exact_observed_output_identity(self):
        tasks, selected = self.recovered_selection()
        selected['observations'][-1] = dict(self.discovery, raw_output_digest='forged-output-digest')
        report = staged.verify_completion(self.epoch, selected, tasks=tasks)
        self.assertFalse(report['complete'])
        self.assertIn('invalid_review_staged', [row['code'] for row in report['issues']])

    def test_context_recovery_keeps_historical_adverse_finding_as_an_obligation(self):
        finding = dict(id='historical-adverse-finding', text='The newly required premise may fail.', adverse=True,
            classification=dict(decisions=['/value/status'], reason='The premise constrains the conclusion.'),
            claim_ids=[self.epoch['manifest']['claims'][0]['id']], grounds=full_card(self.epoch)['critical_spans'])
        self.discovery['output']['findings'] = [finding]
        self.discovery['raw_output_digest'] = digest(self.discovery['output'])
        tasks, selected = self.recovered_selection()
        report = staged.verify_completion(self.epoch, selected, tasks=tasks)
        self.assertFalse(report['complete'])
        self.assertTrue(any(row['code'] == 'review_finding_answer_pending' and row.get('finding_id') == finding['id']
                            for row in staged.iter_completion_issues(self.epoch, selected, tasks=tasks)), report['issues'])
        verified = staged.collect_planning_records(self.epoch, selected, tasks=tasks)
        self.assertEqual(verified['findings'], [finding])

    def recover_card_question(self, disposition=None, grounded=True, dependent=True,
                              assurance='passed', historical_answer=False, kind='full', repeat_question=False):
        card = self.discovery['output']['cards'][0]
        card['kind'] = kind
        question = dict(text='Is the newly discovered premise necessary?',
                        claim_ids=list(self.contexts), needs='Original evidence for the premise.')
        card['unresolved'] = [question]
        identifier = 'card-unresolved:' + digest([card['id'], question])
        answer = dict(id='premise-answer', finding_id=identifier, disposition=disposition,
                      reason='The original evidence accounts for the premise.',
                      grounds=card['critical_spans'] if grounded else [])
        self.discovery['output'].pop('answers', None)
        if historical_answer:
            self.discovery['output']['answers'] = [answer]
        self.discovery['raw_output_digest'] = digest(self.discovery['output'])
        tasks, selected = self.recovered_selection([question] if repeat_question else ())
        reading = task(self.epoch, contexts=self.contexts,
                       dependencies=[self.discovery['task_id']] if dependent else [])
        reread = observation(self.epoch, reading, selected['observations'][1]['output'],
            dependencies={self.discovery['task_id']: self.discovery['raw_output_digest']} if dependent else {})
        combined = task(self.epoch, 'combine', contexts=self.contexts, card_ids=tasks[1]['card_ids'],
                        scope='joint_card', dependencies=[reading['id']])
        output = copy.deepcopy(selected['observations'][0]['output'])
        output['integrations'][0].update(task_id=combined['id'], selected_observations=[reread['id']])
        output['combinations'][0]['task_id'] = combined['id']
        output['terminal']['assurances'] = {key: assurance for key in staged.ASSURANCES}
        for check in output['assurance_checks']:
            check['status'] = assurance
        if disposition is not None and not historical_answer:
            output['answers'] = [answer]
            output['integrations'][0]['answers'] = [answer['id']]
        result = observation(self.epoch, combined, output,
                             dependencies={reading['id']: reread['raw_output_digest']})
        return [reading, combined, tasks[-1]], dict(observations=[result, reread, self.discovery]), identifier

    def test_context_expansion_keeps_unanswered_historical_card_questions(self):
        for kind in ('full', 'fragment'):
            with self.subTest(kind=kind):
                tasks, selected, identifier = self.recover_card_question(kind=kind)
                report = staged.verify_completion(self.epoch, selected, tasks=tasks)
                self.assertFalse(report['complete'], report['issues'])
                pending = [row for row in staged.iter_completion_issues(self.epoch, selected, tasks=tasks,
                    code='review_card_question_pending')]
                self.assertEqual([row['finding_id'] for row in pending], [identifier])
                self.assertEqual(pending[0]['question'], self.discovery['output']['cards'][0]['unresolved'][0])
                self.assertEqual(report['progress']['full_cards'], 1)
                self.assertEqual(self.epoch, self.before)

    def test_current_grounded_dependent_answer_closes_a_historical_card_question(self):
        tasks, selected, _ = self.recover_card_question(disposition='answered')
        report = staged.verify_completion(self.epoch, selected, tasks=tasks)
        self.assertTrue(report['complete'], report['issues'])
        self.assertEqual(report['progress']['answered_findings'], 1)

    def test_current_rediscovery_can_supply_the_same_question_identity(self):
        tasks, selected, _ = self.recover_card_question(disposition='answered', dependent=False,
                                                       repeat_question=True)
        report = staged.verify_completion(self.epoch, selected, tasks=tasks)
        self.assertTrue(report['complete'], report['issues'])

    def test_malformed_historical_card_array_returns_a_completion_rejection(self):
        tasks, selected, _ = self.recover_card_question(disposition='answered', dependent=False,
                                                       repeat_question=True)
        assigned = task(self.epoch, 'final', required=False)
        historical = observation(self.epoch, assigned, {'terminal': {}})
        historical['output']['cards'] = None
        historical['raw_output_digest'] = digest(historical['output'])
        tasks.append(assigned)
        selected['observations'].append(historical)
        report = staged.verify_completion(self.epoch, selected, tasks=tasks)
        self.assertFalse(report['complete'])
        self.assertTrue(any(row['code'] == 'invalid_review_staged'
            and row.get('task_id') == assigned['id'] for row in staged.iter_completion_issues(self.epoch, selected, tasks=tasks)), report['issues'])

    def test_historical_card_questions_reject_ungrounded_unrelated_or_historical_answers(self):
        for changes in (dict(grounded=False), dict(dependent=False), dict(historical_answer=True),
                        dict(disposition='unresolved')):
            with self.subTest(changes=changes):
                options = dict(disposition='answered')
                options.update(changes)
                tasks, selected, identifier = self.recover_card_question(**options)
                report = staged.verify_completion(self.epoch, selected, tasks=tasks)
                self.assertFalse(report['complete'], report['issues'])
                self.assertTrue(any(row['code'] == 'review_card_question_pending'
                    and row.get('finding_id') == identifier for row in staged.iter_completion_issues(self.epoch, selected, tasks=tasks)), report['issues'])

    def test_retained_historical_card_question_requires_adverse_or_incomplete_assessment(self):
        for assurance in ('passed', 'failed', 'not_assessed'):
            with self.subTest(assurance=assurance):
                tasks, selected, _ = self.recover_card_question(disposition='retained', assurance=assurance)
                report = staged.verify_completion(self.epoch, selected, tasks=tasks)
                self.assertEqual(report['complete'], assurance != 'passed', report['issues'])

    def test_final_packet_keeps_historical_findings_outside_its_integration_dependencies(self):
        finding = dict(id='historical-adverse-finding', text='The newly required premise may fail.', adverse=True,
            classification=dict(decisions=['/value/status'], reason='The premise constrains the conclusion.'),
            claim_ids=[self.epoch['manifest']['claims'][0]['id']], grounds=full_card(self.epoch)['critical_spans'])
        self.discovery['output']['findings'] = [finding]
        self.discovery['raw_output_digest'] = digest(self.discovery['output'])
        reading = task(self.epoch, contexts=self.contexts)
        card = full_card(self.epoch)
        for cell in card['judgments']:
            cell['context_root'] = self.contexts[cell['claim_id']]['root']
        current = observation(self.epoch, reading, dict(cards=[card]))
        self.records['review_task'][reading['id']] = dict(epoch_id=self.epoch['id'], task=reading)
        self.records['review_assignment']['current'] = dict(id='current', staged_task=reading,
            staged_review=dict(kind='task', epoch_id=self.epoch['id']))
        self.records['review_task_acceptance']['current'] = dict(task_id=reading['id'], receipt=dict(
            attempt=dict(id=current['id'], assignment_id='current', recorded_revision=2), staged_observation=current))
        final = task(self.epoch, 'final', contexts=self.contexts, dependencies=[reading['id']])
        planned = self.plan(final)
        packet = protocol._read_json(self.artifacts, planned['packet'])
        self.assertIn(finding, packet['staged_review']['final']['findings'])
        self.assertNotIn(self.discovery['task_id'], planned['task']['dependencies'])

    def test_automatic_replanning_and_subdivision_keep_expanded_context_roots(self):
        from test_review_staged_fixtures import profile
        self.controller['staged_review'] = dict(epoch_id=self.epoch['id'])
        planned = producer.plan_remaining_tasks(self.records, self.artifacts, self.controller,
                                                 profiles={'read':profile()})
        reads = [row for row in planned['tasks'] if row['type'] == 'read']
        self.assertTrue(reads)
        for assigned in reads + producer.split_task(self.epoch, self.expanded_task()):
            self.assertEqual(assigned['context_roots'],
                             {key:self.contexts[key]['root'] for key in assigned['claim_ids']})

    def test_retrieval_after_discovery_uses_the_current_frozen_source_closure(self):
        from research_harness import review_staged_followups as followups
        from test_review_staged_followups import request
        assigned = self.expanded_task('final')
        observed = observation(self.epoch, assigned, dict(retrievals=[request(assigned)]))
        result = followups.plan_retrieval_tasks(self.epoch, [self.discovery, observed],
            [self.records['review_task'][self.discovery['task_id']]['task'], assigned], contexts=self.contexts)
        self.assertEqual(result['tasks'][0]['context_roots'], assigned['context_roots'])

    def test_pending_historical_retrieval_is_reissued_under_expanded_context(self):
        from research_harness import review_staged_followups as followups
        from test_review_staged_followups import request
        original = self.records['review_task'][self.discovery['task_id']]['task']
        pending = request(original)
        self.discovery['output']['retrievals'] = [pending]
        self.discovery['raw_output_digest'] = digest(self.discovery['output'])
        result = followups.plan_retrieval_tasks(self.epoch, [self.discovery], [original], contexts=self.contexts)
        reread = result['tasks'][0]
        self.assertEqual(reread['context_roots'], {key:self.contexts[key]['root'] for key in reread['claim_ids']})
        self.assertEqual(result['bindings'][reread['id']]['retrievals'], [pending])

    def recover_retrieval(self, resolved=False, depends_on_request=True):
        from test_review_staged_followups import request
        original = self.records['review_task'][self.discovery['task_id']]['task']
        pending = request(original)
        self.discovery['output']['retrievals'] = [pending]
        self.discovery['raw_output_digest'] = digest(self.discovery['output'])
        tasks, selected = self.recovered_selection()
        old_read = next(row for row in selected['observations'] if row['task_id'] == tasks[0]['id'])
        reading = task(self.epoch, contexts=self.contexts,
                       dependencies=[original['id']] if depends_on_request else [])
        reread = observation(self.epoch, reading, old_read['output'], dependencies={
            original['id']: self.discovery['raw_output_digest']} if depends_on_request else {})
        old_combined = next(row for row in selected['observations'] if row['task_id'] == tasks[1]['id'])
        combined = task(self.epoch, 'combine', contexts=self.contexts, card_ids=tasks[1]['card_ids'],
                        scope='joint_card', dependencies=[reading['id']])
        output = copy.deepcopy(old_combined['output'])
        output['integrations'][0].update(task_id=combined['id'], selected_observations=[reread['id']])
        output['combinations'][0]['task_id'] = combined['id']
        if resolved:
            output['retrievals'] = [dict(pending, status='resolved', answer='Rechecked the expanded original context.',
                                        observation_ids=[reread['id']])]
        result = observation(self.epoch, combined, output, dependencies={reading['id']:reread['raw_output_digest']})
        return [reading, combined, original], dict(observations=[result, reread, self.discovery])

    def test_historical_pending_retrieval_still_blocks_current_completion(self):
        tasks, selected = self.recover_retrieval()
        report = staged.verify_completion(self.epoch, selected, tasks=tasks)
        self.assertFalse(report['complete'])
        self.assertTrue(any(row['code'] == 'review_retrieval_pending' for row in report['issues']), report['issues'])

    def test_current_reread_can_close_a_historical_request_without_rewriting_its_scope(self):
        tasks, selected = self.recover_retrieval(resolved=True)
        report = staged.verify_completion(self.epoch, selected, tasks=tasks)
        self.assertTrue(report['complete'], report['issues'])

    def test_unrelated_read_cannot_close_a_historical_question(self):
        tasks, selected = self.recover_retrieval(resolved=True, depends_on_request=False)
        report = staged.verify_completion(self.epoch, selected, tasks=tasks)
        self.assertFalse(report['complete'])
        self.assertTrue(any(row['code'] == 'invalid_review_staged' and row.get('retrieval_id') == 'retrieve'
                            for row in staged.iter_completion_issues(self.epoch, selected, tasks=tasks)), report['issues'])


if __name__ == '__main__':
    unittest.main()
