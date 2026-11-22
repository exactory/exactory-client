"""Prompt readers and transport stubs for the session and series properties."""

import json
import re
from datetime import datetime, timedelta, timezone
from copy import deepcopy
from unittest.mock import patch

from codex_route_fixtures import bind_codex_response, build_codex_response
from research_harness import review_failure_class as failures
from research_harness import review_protocol as protocol, review_route_capacity as capacity
from research_harness.review_continuation import plan_next
from research_harness.review_excerpts import read_sent_packet


def read_prompt(request):
    """Decode only the final user message, the JSON presented to the reviewer."""
    prompt = read_sent_packet(request)
    if prompt is None:
        raise AssertionError('The prompt does not present a JSON packet')
    return prompt


def read_binding(prompt):
    for key in ('session_turn', 'session_final', 'session_combine', 'review_parts'):
        if key in prompt:
            return prompt[key]
    raise AssertionError('No capacity binding in the review prompt')


def read_answer_bound(prompt):
    return read_binding(prompt)['capacity']['answer_bound']


def build_session_requests(prompt, source_kind, characters):
    turn = prompt['session_turn']
    if turn['turn'] == 1:
        return [dict(id='inventory', kind='inventory_page', page=0)]
    for delivery in turn['plan']['now']:
        if delivery['kind'] == 'list_page' and delivery['request_id'] == 'inventory':
            row = next(row for row in delivery['page']['rows'] if row['kind'] == source_kind)
            return [dict(id='passage', kind='passage', unit=row['handle'], anchor=None, characters=min(characters, row['characters']))]
    return []


def build_session_request(prompt, *, source_kind='fulltext', characters=40):
    turn = prompt['session_turn']
    requests = build_session_requests(prompt, source_kind, characters)
    answers = []
    for row, evidence in zip(turn['plan']['now'], prompt['evidence']):
        if row['kind'] in ('list_page', 'request_status'):
            continue
        answers.append(dict(unit=row['unit'], text=evidence['content'][:20],
                            purpose='Retain the delivered passage.', targets=[turn['targets'][0]['handle']]))
    return dict(schema_version=1, stage='request', notes='The delivered context was read.',
                excerpts=answers, requests=requests, settled_requests=[])


def build_bar(prompt, *, source_kind='fulltext'):
    """Use the bar response shape and only evidence handles carried by this final."""
    sources = prompt.get('session_final', {}).get('sources', [])
    evidence = [dict(ref=row['handle'], start=0, end=20) for row in sources if row['kind'] == source_kind]
    return dict(stage='bar', support=None, value=dict(status='unresolved',
        consequence='The consequence cannot yet be determined.', bar_ids=[], objective_adequacy='not_assessed',
        method_adequacy='not_assessed', reason='The delivered evidence does not settle the standard.',
        evidence=evidence, work_items=[], assurances=[], objection_findings=[]),
        objections=[], limitations=['Assessment is limited to the delivered evidence.'])


def size_answer(output, container, field, characters):
    """Fill a text field up to the requested serialized-character size."""
    room = characters - len(json.dumps(output))
    if room < 0:
        raise AssertionError('The minimum answer exceeds the requested size')
    container[field] += ' ' + 'x' * (room - 1) if room else ''
    return output


def build_series_turn(prompt):
    binding = prompt['review_parts']
    kind = binding['kind']
    if kind == 'read':
        return dict(kind=kind, turn=binding['turn'], cards=[
            dict(group=deepcopy(group), summary='The delivered material was read.',
                 bearing=[], statements=[], depends_on='', questions='')
            for group in prompt['review_parts_groups']], unborne=[
                dict(target=row['id'], reason='No material bearing.') for row in binding['targets']])
    if kind == 'combine' and binding.get('mode') == 'condense':
        card = prompt['review_parts_state']['cards'][0]
        handle = card['group']['handle'] if 'group' in card else card.get('id', card.get('block'))
        return dict(block=binding['block']['handle'], summary='The delivered entries retain their evidence.',
                    refs=[handle])
    if kind == 'combine':
        return dict(kind=kind, turn=binding['turn'], combinations=[], rest='No interactions require a check.')
    if kind == 'check':
        return dict(kind=kind, turn=binding['turn'], findings=[
            dict(combination=handle, finding='The originals were reviewed.', effect='unresolved', statements=[])
            for handle in binding['check_combinations']])
    raise AssertionError('No interim answer for ' + kind)


def invoke_prompt(test, case, assignment, answer, *, attempt_id=None, incomplete=False, fill_request=False):
    """Measure the actual request at send time and give the stub only visible input."""
    records = case.store.snapshot()['records']
    plan = capacity.read_chain_capacity_plan(case.artifacts, assignment)
    attempt_id = attempt_id or 'attempt-' + assignment['id']

    def send(route, request, credential):
        test.assertEqual(len(protocol.transport.logical_messages(request)), 1)
        prompt = read_prompt(request)
        binding = read_binding(prompt)
        kind = assignment['capacity_plan_kind']
        bound = binding['capacity']
        plan_kind = 'final' if kind == 'combine' and 'combine' not in plan['task'] else kind
        level = bound.get('level_material_allowance', bound['material_allowance'])
        pinned = capacity.derive_plan_task_limits(plan, plan_kind, [dict(material_allowance=level)])
        shown_bound = bound['request_bound']
        if isinstance(shown_bound, dict):
            test.assertEqual(shown_bound, pinned['request_bound'])
        else:
            test.assertEqual(shown_bound, pinned['request_bound']['text_characters'])
        checked = capacity.describe_plan_preflight(records, route, request, plan, kind)
        for name, row in checked['plan_comparison']['sizes'].items():
            test.assertTrue(row['fits'], (name, row))
            test.assertEqual(row['bound'], pinned['request_bound'][name])
        output = answer(prompt)
        response = build_codex_response(route, request, output)
        if fill_request:
            item = response['output'][0]['content'][0]
            text = item['text']
            room = read_answer_bound(prompt) - len(text)
            test.assertGreaterEqual(room, 0)
            # Insignificant interior JSON whitespace is part of the delivered response.
            item['text'] = '{' + ' ' * room + text[1:]
            return bind_codex_response(route, request, response)
        if incomplete:
            response['status'] = 'incomplete'
            return bind_codex_response(route, request, response)
        return response

    with patch.object(protocol.transport, 'send_request', side_effect=send) as sender:
        result = protocol.invoke_assignment(case.store, dict(id=attempt_id, assignment_id=assignment['id']),
            credential='test-only', expected_revision=case.store.revision, request_id='property-' + attempt_id)
    test.assertEqual(sender.call_count, 1, result)
    return result['result']


def exhaust_attempts(test, case, assignment, *, floor=False):
    """Drive the ordinary incomplete-answer ladder at its real retry times."""
    class RetryClock(datetime):
        @classmethod
        def now(cls, tz=None):
            return (datetime.now(timezone.utc) + timedelta(hours=2)).astimezone(tz)

    test.assertEqual(len(assignment['id']), 60)
    outcome = failures.OUTCOME_ROWS['execution_incomplete']
    limit = (failures.IDENTICAL_OVER_CAP_ATTEMPT_LIMIT if floor
             else outcome['attempt_limit'] * failures.SEQUENCE_LIMIT)
    stop = 'review_output_over_cap' if floor else 'review_turn_failures_exhausted'
    with patch.object(failures, 'datetime', RetryClock):
        for number in range(limit):
            invoke_prompt(test, case, assignment, lambda prompt: {}, incomplete=True,
                          attempt_id='attempt-%052d' % number)
            action = plan_next(case.store.snapshot()['records'], case.artifacts, assignment['id'])
            if action['state'] == 'stop':
                break
            test.assertEqual((action['state'], action['command']), ('run', 'review-run'), action)
    test.assertEqual((action['state'], action['code']), ('stop', stop), action)
    test.assertEqual(number + 1, limit)
    test.assertIn('decision', action['details'])
    records = case.store.snapshot()['records']
    attempts = [row for row in records['review_attempt'].values() if row['assignment_id'] == assignment['id']]
    test.assertEqual(len(attempts), limit)
    test.assertEqual(len({row['request']['sha256'] for row in attempts}), 1)
    test.assertTrue(all(len(row['id']) == 60 for row in attempts))


def read_group_material(prompt):
    """Associate each visible text run with its unit descriptor or continued row."""
    context = prompt['review_parts_context']
    handle = context.get('unit')
    material = {}
    for entry in prompt['review_parts_material']:
        if isinstance(entry, dict):
            handle = entry['handle']
        else:
            material[handle] = entry
    return material


def read_group_units(prompt):
    continued = prompt['review_parts_context'].get('unit')
    rows = [row['handle'] for row in prompt['review_parts_material'] if isinstance(row, dict)]
    return ([continued] if continued is not None else []) + rows


def read_original_deliveries(prompt):
    return prompt.get('review_parts_requests', [])


def build_native_final(prompt):
    native = prompt['native']
    binding = prompt['review_parts']
    evidence = [dict(ref=row['unit'], start=row['start'], end=row['end'])
                for row in read_original_deliveries(prompt)]
    findings = {row['id']: 'The delivered examination is limited.' for row in binding['targets']}
    findings['openness'] = dict(outcome='unresolved', statement_digest=native['claim_digest'],
        evidence=evidence, searched_at='2026-11-03', coverage='The delivered originals.',
        reason='The available text supports no stronger openness claim.')
    return dict(schema_version=2, subject_digest=native['subject_digest'], claim_digest=native['claim_digest'],
        reviewer=native['reviewer'], decision='approve', findings=findings)


def request_first_original(prompt):
    card = prompt['review_parts_state']['cards'][0]
    handle = card['group']['first']['unit']
    return dict(kind='requests', source_requests=[], unit_requests=[dict(unit=handle, start=0, end=20)])


def build_cross_group_answer(prompt):
    answer = build_series_turn(prompt)
    other = answer['cards'][-1]['group']['last']['unit']
    material = read_group_material(prompt)[other]
    # Earlier and later groups can meet inside one unit. Use the last text
    # of the turn, which the final group owns.
    quotation = material[-40:]
    answer['cards'][0]['statements'] = [dict(unit=other, text=quotation,
                                             why='This text belongs to a different group.')]
    return answer


def build_broken_cards(prompt, variant):
    answer = build_series_turn(prompt)
    cards = answer['cards']
    if variant == 'missing':
        cards.pop()
    elif variant == 'duplicate':
        cards[-1] = deepcopy(cards[0])
    elif variant in ('first', 'last'):
        cards[0]['group'][variant]['offset'] += 1
    else:
        cards[0]['group']['unit_count'] += 1
    return answer


def read_delivered_span_references(prompt):
    return [dict(ref=row['unit'], start=row['start'], end=row['end'])
            for row in read_original_deliveries(prompt)]


def build_adjudication_final(prompt):
    return dict(objection_id=prompt['objection']['id'], disposition='unresolved',
                reason='The delivered passage does not settle the objection.',
                evidence=read_delivered_span_references(prompt), correction_admissible=None)


def carry_source_citations(prompt):
    """Copy permitted typed citations from the visible inventory rows into cards."""
    output = build_series_turn(prompt)
    if output.get('kind') != 'read':
        return output
    handles = read_group_units(prompt)
    for card in output['cards']:
        group = card['group']
        if group['first']['unit'] not in handles or group['last']['unit'] not in handles:
            continue
        first, last = handles.index(group['first']['unit']), handles.index(group['last']['unit'])
        owners = handles[first:last + 1]
        citations = []
        for value in prompt['review_parts_material']:
            if not isinstance(value, dict) or value.get('handle') not in owners:
                continue
            for reference in value.get('references', {}).get('rows', []):
                link = reference['row']
                if reference['field'] != 'citations':
                    continue
                if ({'version_id', 'source_id', 'locator'} <= link.keys()
                        or link.get('kind') in ('source', 'result')):
                    citations.append(link)
        if citations:
            card['summary'] = json.dumps(dict(citations=citations))
    return output


def read_carried_citations(prompt):
    evidence = {}
    for row in prompt['review_parts_state']['cards']:
        if row['summary'].startswith('{'):
            for citation in json.loads(row['summary'])['citations']:
                evidence[json.dumps(citation, sort_keys=True)] = citation
    return list(evidence.values())


def build_verification_final(prompt):
    evidence = read_carried_citations(prompt)
    binding = prompt['review_parts']
    return dict(verdict=dict(stance='sound', summary='The scoped evidence was reviewed.',
        prediction=dict(percentile=50, band=dict(best=35, worst=65))),
        checks=[dict(dimension=row['id'], reason='The delivered text grounds this check.', evidence=evidence)
                for row in binding['targets']])


def build_problem_final(prompt):
    stage = prompt['role']
    output = build_bar(prompt)
    output['stage'] = stage
    value = output['value']
    evidence = read_delivered_span_references(prompt)
    target_ids = [row['id'] for row in prompt['review_parts']['targets']]
    bar_ids = prompt.get('bar_ids') or [row['id'] for row in prompt.get('bars', [])]
    value.update(bar_ids=bar_ids, objective_adequacy='unresolved', method_adequacy='unresolved',
                 evidence=evidence, assurances=[
                     dict(kind=kind, status='not_assessed', reason='The consulted evidence has limited scope.',
                          evidence=evidence) for kind in target_ids
                     if kind.startswith('problem_') or kind == 'stop'])
    if stage == 'slate':
        value['rubric_potential'] = {row['id']: dict(significance={'B1': 1, 'B2': 1}, priority=1)
                                     for row in prompt.get('candidates', [])}
    else:
        admissions = {name: 'pass' for name in target_ids if name.startswith('A') and name[1:].isdigit()}
        grades = dict(admission=admissions, significance={'B1': 2, 'B2': 1}, priority=2,
                      attempt='pass', accessibility='E-F', calibration='pass')
        value.update(rubric=grades, rubric_evidence={name: 'The shown evidence grounds this grade.'
            for name in list(admissions) + ['B1', 'B2', 'priority', 'attempt', 'accessibility', 'calibration']})
    if 'context_repair' in prompt:
        output['reassessment'] = dict(assignment_id=prompt['context_repair']['assignment_id'], findings=[])
    return output


def restore_reference_template(reference):
    result = dict(reference)
    if 'artifact_reference_template' in result:
        result['artifact'] = restore_reference_template(result.pop('artifact_reference_template'))
    if 'artifact_descriptor' in result:
        descriptor = result['artifact_descriptor']
        return dict(path=descriptor['location'], sha256=descriptor['sha256'], size=descriptor['size'],
                    media_type=descriptor['media_type'], **descriptor.get('extensions', {}))
    for key in ('link', 'provenance'):
        if key in result:
            result[key] = restore_reference_template(result[key])
    return result


def build_readiness_final(prompt):
    """Copy the readiness binding and use the declared check targets and delivered units."""
    contract = prompt['support_contract']
    evidence = read_delivered_span_references(prompt)
    native = [restore_reference_template(reference) for reference in contract.get('evidence', [])]
    if not native:
        native = read_carried_citations(prompt)
    output = build_bar(prompt)
    output['stage'] = 'result'
    output['value'].update(evidence=evidence, objective_adequacy='unresolved', method_adequacy='unresolved',
        assurances=[dict(kind=kind, status='unresolved', reason='The shown evidence has limited scope.',
                         evidence=evidence) for kind in ('impact', 'demand', 'novelty_risk', 'feasibility',
                             'distinctness', 'continuity', 'grand_challenge', 'q1_venue', 'stop')])
    support = dict(id='readiness-observation', candidate_digest=contract['candidate_digest'],
        assessor=restore_reference_template(prompt['assessor']), verdict='unresolved', limitations=['Only the supplied evidence was checked.'],
        checks=[dict(kind=row['id'], status='unresolved', reason='The evidence has limited scope.',
                     evidence=native) for row in prompt['review_parts']['targets']])
    if 'target' in contract:
        support['target'] = contract['target']
    output['support'] = support
    return output


def build_late_card_answer(prompt):
    """Retain a visible refuting sentence and its unit-relative range."""
    answer = build_series_turn(prompt)
    binding = read_binding(prompt)
    if binding.get('mode') == 'condense':
        for card in prompt['review_parts_state']['cards']:
            if card.get('statements'):
                statement = card['statements'][0]
                card_handle = card['group']['handle']
                start, end = json.loads(statement['why'])
                return dict(block=binding['block']['handle'], refs=[card_handle],
                            summary=json.dumps([card_handle, statement['unit'], start, end],
                                               separators=(',', ':')))
    if binding['kind'] != 'read':
        return answer
    originals = read_group_material(prompt)
    target = binding['targets'][0]['id']
    handles = read_group_units(prompt)
    for card in answer['cards']:
        group = card['group']
        if not group['late'] or group['content_characters']:
            continue
        first = handles.index(group['first']['unit'])
        last = handles.index(group['last']['unit'])
        for handle in handles[first + bool(group['first']['offset']):last + 1]:
            match = re.search(r'"reason"\s*:\s*"([^".]+\.)', originals.get(handle, ''))
            if match is None or 'counterexample' not in match[1].lower():
                continue
            card['statements'] = [dict(unit=handle, text=match[1], why=json.dumps(match.span(1)))]
            card['bearing'] = [dict(target=target, relation='contradicts', reason='The delivered sentence refutes it.')]
            answer['unborne'] = [row for row in answer['unborne'] if row['target'] != target]
            return fill_late_cards(prompt, answer)
    return fill_late_cards(prompt, answer)


def fill_late_cards(prompt, answer):
    available = read_answer_bound(prompt) - len(json.dumps(answer))
    for card in answer['cards']:
        group = card['group']
        if not group['late']:
            continue
        room = min(available, group['characters'] - len(json.dumps(card)), 12000 - len(card['summary']))
        if room > 0:
            card['summary'] += 'x' * room
            available -= room
    return answer


def request_archived_quote(prompt):
    """Use the index left in the visible condensing summary."""
    for row in prompt['review_parts_state']['combinations']:
        summary = row.get('summary', '')
        if summary.startswith('['):
            card, unit, start, end = json.loads(summary)
            return dict(kind='requests', source_requests=[],
                        unit_requests=[dict(unit=card), dict(unit=unit, start=start, end=end)])
    raise AssertionError('The final has no condensing summary that identifies the quote')


def build_late_adjudication_final(prompt):
    answer = build_adjudication_final(prompt)
    answer['evidence'] = [row for row in answer['evidence'] if row['ref'].startswith('u')]
    return answer
