"""Small measured staged-review records, independent of stores and transports."""

import copy

from research_harness import review_staged as staged
from research_harness.evidence import digest


def profile(**changes):
    value = dict(context_characters=10, envelope_characters=10, output_base_tokens=5,
                 output_tokens_per_claim=5, output_tokens_per_character=0,
                 seconds_per_character=0, seconds_per_claim=1, duration_base_seconds=1,
                 duration_limit_seconds=1000, output_margin_tokens=2)
    value.update(changes)
    return value


def epoch(count=1, claims=None, **changes):
    manifest = staged.build_manifest(
        {'statement': 'For primes p the conclusion holds.', 'grade': 2},
        [{'key': 'readings', 'index': i, 'item': {'text': 'Evidence %d.' % i},
          'artifacts': [], 'source_ids': []} for i in range(count)], [], claims=claims)
    spec = dict(id='epoch-A', controller_assignment_id='assignment-A', subject={'id': 'problem'},
                evidence_currency={'revision': 1}, role='result', slot_id='A', reviewer_id='reviewer-A',
                route_id='route-A', author_id='author', task_policy_version='tasks-v1',
                composition_schema_version='composition-v1', calibration_profile_version='measured-v1',
                budgets={'max_calls': 20, 'max_retries': 1, 'max_rereads': 2,
                         'max_subdivisions': 10, 'max_epochs': 2, 'max_elapsed_seconds': 1000},
                manifest=manifest, material_objections=[], completion_requirements={})
    spec.update(changes)
    return staged.freeze_epoch(spec)


def task(value, kind='read', **changes):
    manifest = value['manifest']
    inputs = [staged.source_span(source, start, end)
              for source in manifest['sources'] if source['kind'] != 'binary'
              for start, end in source['required_intervals']]
    changes.setdefault('occurrence_ids', [row['id'] for row in manifest['occurrences']])
    changes.setdefault('measurement', staged.joint_fit([span['end'] - span['start'] for span in inputs],
                       100000, 10000, profile(), claim_count=len(manifest['claims'])))
    assigned = staged.make_task(value, kind, inputs=inputs,
                               claim_ids=[c['id'] for c in manifest['claims']], **changes)
    return staged.freeze_task(assigned, digest({'rendered_inputs': inputs, 'task_id': assigned['id']}),
                              staged.SERIALIZATION)


def observation(value, assigned, output, **changes):
    actual = dict(history=[], intervals=copy.deepcopy(assigned['inputs']),
                  binary_sources=copy.deepcopy(assigned['binary_sources']),
                  claim_ids=assigned['claim_ids'][:], context_roots=assigned['context_roots'].copy(),
                  complete_cards=assigned['card_ids'][:])
    actual['complete_card_roots'] = {card['id']: digest(staged.compact_card(card))
                                     for card in output.get('cards', []) if card['id'] in assigned['card_ids']}
    actual['full_card_roots'] = {card['id']: digest(card) for card in output.get('cards', [])
                                 if card['id'] in assigned['card_ids']}
    result = dict(id='observation-' + assigned['id'], epoch_id=value['id'], epoch_digest=value['digest'],
                  task_id=assigned['id'], task_digest=assigned['digest'], actual_input=actual,
                  request_digest=assigned['planned_request_digest'], output=copy.deepcopy(output),
                  raw_output_digest=digest(output), dependencies={})
    result.update(changes)
    return result


def full_card(value, index=0, conditions=None):
    manifest = value['manifest']
    occurrence = manifest['occurrences'][index]
    grounds = [staged.source_span(next(s for s in manifest['sources'] if s['id'] == source_id), start, end)
               for source_id in occurrence['source_ids']
               for start, end in next(s for s in manifest['sources'] if s['id'] == source_id)['required_intervals']]
    judgments = [dict(id='judgment-%d-%s' % (index, claim['id']), occurrence_id=occurrence['id'],
                      claim_id=claim['id'], status='supported', reason='The supplied item addresses this claim.',
                      grounds=grounds, scope='joint_original', context_root=manifest['contexts'][claim['id']]['root'])
                 for claim in manifest['claims']]
    sources = {row['id']: row for row in manifest['sources']}
    return dict(id='card-%d' % index, occurrence_id=occurrence['id'], kind='full',
                establishes='The result holds for prime inputs.', evidence_type='assertion',
                conclusions=[dict(id='conclusion-%d' % index, text='It holds for primes.', grounds=grounds)],
                conditions=conditions or [], judgments=judgments,
                critical_spans=grounds,
                critical_quotations=[staged.materialize_quotation(sources[span['source_id']], span) for span in grounds],
                findings=[], unresolved=[], dependencies=[], fragment_ids=[])


def selection(value, cards=None, terminal=None, integration_changes=None, contexts=None):
    cards = cards or [full_card(value)]
    reading = task(value, contexts=contexts)
    read_output = dict(cards=cards, cells=[j for c in cards for j in c['judgments']])
    read_observation = observation(value, reading, read_output)
    assigned = task(value, 'combine', card_ids=[card['id'] for card in cards], scope='joint_card',
                    dependencies=[reading['id']], contexts=contexts)
    obligations = staged.card_obligations(cards)
    receipt = dict(id='integration', epoch_id=value['id'], task_id=assigned['id'],
                   selected_observations=[read_observation['id']], input_roots=[value['manifest']['digest']],
                   context_roots=assigned['context_roots'],
                   child_ids=[], child_union=[], obligation_ids=obligations,
                   dispositions=[dict(id=key, disposition='retained', reason='Retained with its full scope.',
                                      grounds=[cards[0]['conclusions'][0]['id']]) for key in obligations],
                   findings=[], answers=[], selected_groups=[], retrievals=[], dependencies=[],
                   unresolved=[], decision_constraints={'visible_conditions': [x['id'] for c in cards for x in c['conditions']]})
    receipt.update(integration_changes or {})
    output = dict(cards=cards, cells=[j for c in cards for j in c['judgments']], integrations=[receipt],
                  terminal=terminal or {'assurances': {kind: 'passed' for kind in staged.ASSURANCES},
                                        'visible_conditions': [x['id'] for c in cards for x in c['conditions']]})
    output['combinations'] = [dict(id='combination', task_id=assigned['id'], card_ids=assigned['card_ids'],
                                   scope='joint_card', synthesis='The cards jointly support the prime-domain claim.',
                                   selected_groups=[], segment_task_ids=[], reconciliation=None)]
    output['assurance_checks'] = [dict(id='assurance-' + kind, assurance=kind,
                     status=output['terminal']['assurances'][kind], claim_ids=assigned['claim_ids'],
                     reason='The original claims and their supported scope were assessed.',
                     grounds=[cell['id'] for card in cards for cell in card['judgments']],
                     conditions_satisfied=True, dependencies=[]) for kind in staged.ASSURANCES
                     if 'assurances' in output['terminal']]
    if value['role'] == 'adjudicator':
        output['decision_checks'] = [dict(id='disposition-check', decision='disposition',
                  value=output['terminal']['disposition'], claim_ids=assigned['claim_ids'],
                  reason='The exact objection version is upheld on its original scope.',
                  grounds=[cell['id'] for card in cards for cell in card['judgments']], dependencies=[])]
    combined = observation(value, assigned, output,
                           dependencies={reading['id']: read_observation['raw_output_digest']})
    return [reading, assigned], dict(observations=[combined, read_observation])
