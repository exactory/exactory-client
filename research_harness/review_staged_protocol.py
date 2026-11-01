"""Frozen staged task requests, observations and effective review selections.

The ordinary protocol dispatches here through local imports at its existing
seams. Shared packet, prompt and route operations remain owned by that protocol;
this module never dispatches an ordinary assignment.
"""

import copy

from . import review_protocol as protocol
from . import review_transport as transport
from . import review_excerpts, review_problem_parts
from .errors import ResearchError
from .evidence import digest
from .graph import obligation
from .operations import _read_preparation, fields, immutable_record, prepared_mutation, text
from .storage import _canonical


def collect_output_records(observations, names):
    """Keep one exact record per identity across the accepted dependency closure."""
    records = {}
    for observation in observations:
        for name in names:
            rows = observation['output'].get(name, [])
            if not isinstance(rows, list):
                continue
            for row in rows:
                try:
                    fields(row, ('id',), None, code='invalid_review_staged')
                    text(row['id'], 'record.id', code='invalid_review_staged')
                except ResearchError:
                    continue
                if row['id'] in records and records[row['id']] != row:
                    raise ResearchError('review_task_dependency_changed', 'Dependency records disagree on one identity')
                records[row['id']] = row
    return list(records.values())


def collect_valid_records(epoch, observations, name, validator, *, contexts=None):
    """Preserve independently valid dependency records beside malformed siblings."""
    manifest = epoch['manifest'] if contexts is None else dict(epoch['manifest'], contexts=contexts)
    records = []
    for row in collect_output_records(observations, (name,)):
        try:
            records.append(validator(row, manifest))
        except ResearchError:
            continue
    return records


def collect_observations(records, artifacts, epoch):
    """Select authenticated accepted captures without consulting assignment state."""
    observations, terminal, obligations = [], None, []
    for acceptance in records.get('review_task_acceptance', {}).values():
        receipt = acceptance['receipt']
        observed, attempt = receipt.get('staged_observation'), receipt.get('attempt')
        if observed is None or observed.get('epoch_id') != epoch['id'] or attempt is None:
            continue
        child = records.get('review_assignment', {}).get(attempt['assignment_id'])
        if child is None or not authenticate_observation(records, artifacts, child, attempt):
            obligations.append(obligation('review_invocation_unverified', 'Authenticate every selected task capture.'))
            continue
        if any(row['assignment_id'] == child['id'] and row['kind'] == 'contamination'
               for row in records.get('review_context_event', {}).values()):
            obligations.append(obligation('review_context_contaminated', 'A selected task received foreign input.',
                                          assignment_id=child['id']))
            continue
        observations.append(observed)
        if child['staged_task']['type'] == 'final' and any(
                key in observed['output'] for key in ('terminal', 'assessment_incomplete')):
            if terminal is None or attempt['recorded_revision'] > terminal[0]['recorded_revision']:
                terminal = (attempt, observed)
    return observations, terminal, obligations


def derive_current_contexts(records, artifacts, epoch):
    """Expand original claim closure only from authenticated accepted discoveries."""
    from . import review_staged as staged
    discoveries = [row['receipt'].get('staged_observation')
                   for row in records.get('review_task_acceptance', {}).values()]
    if not any(row is not None and row.get('epoch_id') == epoch['id']
               and row.get('output', {}).get('new_dependencies') for row in discoveries):
        return copy.deepcopy(epoch['manifest']['contexts'])
    observations, _, obligations = collect_observations(records, artifacts, epoch)
    if obligations:
        raise ResearchError(obligations[0]['code'], obligations[0]['explanation'])
    return staged.collect_planning_records(epoch, {'observations':observations},
                                          tasks=list_epoch_tasks(records, epoch))['contexts']


def plan_remaining_tasks(records, artifacts, controller, *, profiles, composition_plan=None):
    """Replan from the exact accepted partial work and caller-supplied sizing profiles."""
    from . import review_staged as staged
    from .evaluation import Evaluation
    evaluation = Evaluation.of(records, artifacts)
    records, artifacts = evaluation.records, evaluation
    epoch = protocol._get(records, 'review_epoch', controller['staged_review']['epoch_id'])
    observations, _, obligations = collect_observations(records, artifacts, epoch)
    if obligations:
        raise ResearchError(obligations[0]['code'], obligations[0]['explanation'])
    tasks = list_epoch_tasks(records, epoch)
    route = protocol._get(records, 'review_route', controller['route_id'])
    selection = {'observations': observations}
    verified = staged.collect_planning_records(epoch, selection, tasks=tasks)
    findings = verified['findings']
    cards = None
    if 'combine' in profiles:
        cards = []
        for card in verified['cards']:
            try:
                characters = len(_canonical(project_card(epoch, card, findings)))
                cards.append(dict(id=card['id'], record=card, claim_count=len(card['judgments']), characters=characters))
            except ResearchError:
                # Missing finding bodies block this card's presentation, not valid sibling cells.
                continue
    planned = staged.plan_tasks(epoch, route_limit=read_input_limit(route),
        output_tokens=route['configuration']['max_output_tokens'], profiles=profiles,
        cards=cards, findings=findings, selected_groups=verified['selected_groups'], contexts=verified['contexts'],
        tasks=tasks, selection=selection, composition_plan=composition_plan,
        source_sizes={row['id']:row['descriptor']['size'] for row in epoch['manifest']['sources'] if row['kind'] == 'binary'})
    remapped, bound = {}, []
    for task in planned['tasks']:
        dependencies = [remapped.get(key, key) for key in task['dependencies']]
        if task['type'] == 'combine':
            dependencies.extend(row['task_id'] for row in observations if any(
                card['id'] in task['card_ids'] for card in row['output'].get('cards', [])))
        if task['type'] == 'original_check':
            dependencies.extend(row['task_id'] for row in observations if any(
                group['id'] in task['group_ids'] for group in row['output'].get('selected_groups', [])))
        changed = remake_task(epoch, task, dependencies=sorted(set(dependencies)),
            segment_task_ids=[remapped.get(key, key) for key in task['segment_task_ids']])
        remapped[task['id']] = changed['id']
        bound.append(changed)
    return staged._seal(dict({key:value for key,value in planned.items() if key != 'digest'}, tasks=bound))


def build_request(artifacts, route, prompt, packet):
    """Admit the exact fresh request, independently of legacy fit-order replay."""
    request = transport.request_body(route, prompt['text'], packet, [])
    request['history'] = []
    sent = review_excerpts.read_sent_packet(request) or {}
    preserved = review_problem_parts.preserves_ordinary_packet(artifacts, packet, request)
    if route['adapter'] == 'codex_cli_visual_v1':
        from .review_staged_binary import preserves_packet
        preserved = preserves_packet(artifacts.records, artifacts, packet, request)
    if (not preserved
            or sent.get('staged_review') != packet.get('staged_review')
            or review_problem_parts._measure(route, request) > read_input_limit(route)):
        raise ResearchError('review_task_pending', 'Partition the task: fitting removed mandatory staged input')
    return request


def project_card(epoch, card, findings):
    """Use the core's versioned compact projection, including observed finding bodies."""
    from . import review_staged as staged
    return staged.compact_card(card, manifest=epoch['manifest'], findings=findings)


def project_terminal_material(epoch, observations, *, integration_id=None, tasks=(), contexts=None):
    """Deliver assessed grounds and answers without replaying full cards or pages."""
    from . import review_staged as staged
    current_contexts = contexts or epoch['manifest']['contexts']
    current_observations = [row for row in observations if all(
        key in current_contexts and root == current_contexts[key]['root']
        for key,root in row['actual_input']['context_roots'].items())]
    outputs = [row['output'] for row in current_observations]
    cards = [row for row in collect_valid_records(epoch, current_observations, 'cards', staged.validate_card,
                                                 contexts=contexts) if row['kind'] == 'full']
    integrations = collect_output_records(current_observations, ('integrations',))
    if tasks:
        verified = staged.collect_planning_records(epoch, {'observations': observations}, tasks=tasks)
        integrations = verified['integrations']
        cards = verified['cards']
    cells = {row['id']: row for card in cards for row in card['judgments']}
    cells.update({row['id']:row for row in collect_valid_records(epoch, current_observations, 'cells',
                                                               staged.validate_cell, contexts=contexts)})
    conclusions = {row['id']: row for card in cards for name in ('conclusions', 'conditions') for row in card[name]}
    for output in outputs:
        for check in output.get('original_checks', []):
            conclusions.update({row['id']: row for row in check['conclusions']})
    children = {key for row in integrations for key in row.get('child_ids', [])}
    roots = [row for row in integrations if row['id'] not in children]
    integrated = next((row for row in roots if row['id'] == integration_id), None)
    if integrated is None and roots:
        if len(roots) != 1:
            raise ResearchError('review_task_pending', 'Integrate the remaining roots before assigning the final')
        integrated = roots[0]
    constraints = integrated.get('decision_constraints', {}) if integrated is not None else {}
    permitted = constraints.get('permissible_decisions', epoch['completion_requirements'].get('permissible_decisions', {}))
    decisions = {}
    for path, values in permitted.items():
        assurance = next((kind for kind in staged.ASSURANCES if path == '/value/assurances/' + kind + '/status'), None)
        required = constraints.get('decision_dependencies', {}).get(path,
            epoch['manifest']['assurance_dependencies'].get(assurance, list(epoch['manifest']['contexts'])))
        related = [row['id'] for row in cells.values() if row['claim_id'] in required]
        decisions[path] = dict(path=path, permitted_values=values, claim_ids=required, cells=related,
            supporting_conclusions=list(conclusions), unresolved=[row['id'] for row in cells.values()
                                                                 if row['id'] in related and row['status'] == 'unresolved'])
    claims = [dict(row, cells=[cell['id'] for cell in cells.values() if cell['claim_id'] == row['id']],
                   statuses=sorted({cell['status'] for cell in cells.values() if cell['claim_id'] == row['id']}))
              for row in epoch['manifest']['claims']]
    finding_records = (verified['findings'] if tasks else
                       [row for observed in observations for row in observed['output'].get('findings', [])])
    findings = {row['id']:row for row in finding_records}
    answers = {row['id']: row for output in outputs for row in output.get('answers', [])}
    material = dict(claims=claims, decisions=decisions, cells=list(cells.values()), conclusions=list(conclusions.values()),
                integrations=integrations, integration_id=integrated['id'] if integrated is not None else None,
                conditions=[row['id'] for card in cards for row in card['conditions']],
                findings=list(findings.values()), answers=list(answers.values()))
    if integrated is not None:
        bound = staged.bind_terminal_conditions(epoch, {}, cards=cards, integration=integrated)
        material.update(record_root=bound['record_root'], visible_conditions=bound['visible_conditions'])
    return material


def build_packet(epoch, task, *, observations=(), repair=None, composition_plan=None, tasks=(), terminal_observations=None,
                 contexts=None):
    """Render exact originals and bounded, explicitly selected dependency records."""
    from . import review_staged as staged
    validate_composition_task(epoch, task, composition_plan)
    sources = {row['id']: row for row in epoch['manifest']['sources']}
    projected = {key: copy.deepcopy(task[key]) for key in
                 ('id', 'type', 'scope', 'claim_ids', 'occurrence_ids', 'card_ids', 'group_ids',
                  'segment_task_ids', 'obligation_ids', 'child_ids', 'context_roots')}
    for key in ('reconciliation_id', 'composition_role', 'composition_plan_root'):
        if key in task:
            projected[key] = copy.deepcopy(task[key])
    originals = [dict(span=span, text=staged.materialize_quotation(sources[span['source_id']], span)['text'])
                 for span in task['inputs']]
    current_contexts = epoch['manifest']['contexts'] if contexts is None else contexts
    claims = [dict(claim, context_root=current_contexts[claim['id']]['root'])
              for claim in epoch['manifest']['claims'] if claim['id'] in task['claim_ids']]
    units = [dict(row, sources=[dict(source_id=original['span']['source_id'], span=original['span'], original_index=index)
                               for index, original in enumerate(originals) if original['span']['source_id'] in row['source_ids']])
             for row in epoch['manifest']['occurrences'] if row['id'] in task['occurrence_ids']]
    # The item is an original too: a segment must not accidentally carry the full item.
    for unit in units:
        unit.pop('item', None)
    continuity = [dict(id=row['id'], task_id=row['task_id'], raw_output_digest=row['raw_output_digest'])
                  for row in observations]
    inherited = []
    assigned_sources = {row['source_id'] for row in task['inputs']}
    for finding in epoch['manifest']['inherited_findings']:
        paths = [(family, index) for family in ('material_objections', 'inherited_review_findings')
                 for index, row in enumerate(epoch['manifest']['frame'].get(family, []))
                 if isinstance(row, dict) and row.get('id') == finding['id']]
        original_fields = [row for row in epoch['manifest']['frame_fields']
                           if any(row['path'][:2] == [family, index] for family, index in paths)]
        if (finding['id'] in task['obligation_ids']
                or any(row['source_id'] in assigned_sources for row in original_fields)):
            entry = dict(id=finding['id'], record_digest=digest(finding),
                         source_ids=[row['source_id'] for row in original_fields])
            if finding.get('provenance'):
                entry.update(provenance=copy.deepcopy(finding['provenance']), accepted=finding['accepted'])
            inherited.append(entry)
    frame_fields = [dict(source_id=original['span']['source_id'], span=original['span'],
                         path=sources[original['span']['source_id']]['identity'].get('path'), text=original['text'])
                    for original in originals if sources[original['span']['source_id']]['kind'] == 'frame']
    body = dict(protocol=staged.PROTOCOL, epoch_id=epoch['id'], task=projected,
                claims=claims, decisions=epoch['completion_requirements'].get('permissible_decisions', {}),
                units=units, originals=originals, continuity=continuity,
                index=[dict(occurrence_id=row['id'], key=row['key'], index=row['index']) for row in
                       epoch['manifest']['occurrences'] if row['id'] in task['occurrence_ids']],
                index_root=epoch['manifest']['digest'], inherited=inherited,
                frame=dict(fields=frame_fields), output_tokens=task['measurement']['configured_output_tokens'])
    project_task_material(epoch, task, body, observations, terminal_observations, tasks,
                          composition_plan['role'] if composition_plan is not None else None,
                          contexts=current_contexts)
    if composition_plan is not None:
        apply_composition_plan(epoch, task, body, composition_plan, terminal_observations or observations, tasks)
    packet = dict(protocol=staged.PROTOCOL, subject=epoch['subject'], role=epoch['role'], staged_review=body,
                  evidence=[])
    if task['objection_entries']:
        ids = {row['objection_id'] for row in task['objection_entries']}
        packet['material_objections'] = [copy.deepcopy(row) for row in epoch['material_objections'] if row['id'] in ids]
        from .review_objection_evidence import resolved_objection_entries
        for objection in packet['material_objections']:
            for name in ('version_evidence', 'version_source_mappings'):
                objection[name] = resolved_objection_entries(epoch, objection, name)
    if repair is not None:
        packet['context_repair'] = copy.deepcopy(repair)
    packet['digest'] = digest(packet)
    return packet


def project_task_material(epoch, task, body, observations, terminal_observations, tasks, composition_role, *, contexts=None):
    """Project only the selected task's scientific dependency material."""
    from . import review_staged as staged
    # Records arrive as explicit task input, never as replayed conversation.
    records = [row['output'] for row in observations]
    cards = collect_valid_records(epoch, observations, 'cards', staged.validate_card, contexts=contexts)
    findings = collect_output_records(observations, ('findings',))
    receipts = collect_output_records(observations, ('integrations', 'original_checks', 'combinations'))
    integration_scope = task['type'] == 'integrate' and not task['occurrence_ids']
    if integration_scope:
        obligations = set(task['obligation_ids'])
        cards = [card for card in cards if not task['child_ids']
                 and (not task['card_ids'] or card['id'] in task['card_ids'])
                 and (not obligations or obligations.intersection(staged.card_obligations([card])))]
        required = {key for card in cards for key in card['dependencies']}
        receipts = [row for row in receipts if row['id'] in task['child_ids'] or row['id'] in required
                    or set(row.get('card_ids', [])).intersection(card['id'] for card in cards)]
        finding_ids = {key for card in cards for key in card['findings']}
        finding_ids.update(key for row in receipts for key in row.get('findings', []))
        if task['card_ids'] or task['child_ids'] or obligations:
            findings = [row for row in findings if row['id'] in finding_ids]
    body['fragments'] = [card for card in cards if card.get('kind') == 'fragment']
    body['combine'] = dict(cards=[project_card(epoch, card, findings) for card in cards if card['id'] in task['card_ids']],
                           presentation='all_cards', blocks=[])
    if task['card_segments']:
        compact = {card['id']: project_card(epoch, card, findings) for card in cards}
        body['combine']['segments'] = [dict(span=span, text=_canonical(compact[span['card_id']])[span['start']:span['end']])
                                       for span in task['card_segments']]
        body['combine']['other_cards'] = task['card_ids']
        body['combine']['cards'] = []
    body['segments'] = dict(segment_task_ids=task['segment_task_ids'])
    if 'reconciliation_id' in task:
        body['segments']['reconciliation_id'] = task['reconciliation_id']
    groups = [group for output in records for group in output.get('selected_groups', [])
              if group['id'] in task['group_ids']]
    body['original_check'] = dict(groups=groups, cards=[project_card(epoch, card, findings) for card in cards])
    body['retrieval'] = [row for output in records for row in output.get('retrievals', [])]
    conclusions = [row for card in cards for name in ('conclusions', 'conditions', 'judgments') for row in card.get(name, [])]
    conclusion_ids = {row['id'] for row in conclusions}
    conclusions.extend(row for row in collect_valid_records(epoch, observations, 'cells', staged.validate_cell,
                                                            contexts=contexts)
                       if row['id'] not in conclusion_ids)
    if integration_scope and (task['obligation_ids'] or task['child_ids']):
        conclusions = [row for row in conclusions if row['id'] in task['obligation_ids']]
    child_union = sorted({key for receipt in receipts if receipt['id'] in task['child_ids']
                          for key in receipt.get('obligation_ids', [])})
    obligation_ids = sorted(set(task['obligation_ids'] or ([] if task['child_ids'] else staged.card_obligations(cards)))
                            | set(child_union))
    copied = dict(epoch_id=epoch['id'], task_id=task['id'], selected_observations=[row['id'] for row in observations],
                  input_roots=[epoch['manifest']['digest']], context_roots=task['context_roots'],
                  child_ids=task['child_ids'], child_union=child_union, obligation_ids=obligation_ids)
    if task['type'] == 'integrate' and not task['occurrence_ids']:
        copied = staged.bind_integration_lists(dict(child_ids=task['child_ids']), epoch=epoch, task=task,
            cards=cards, children=[row for row in receipts if row['id'] in task['child_ids']], observations=observations)
    body['integrate'] = dict(conclusions=conclusions, full_cards=cards, receipts=receipts, findings=findings,
                            constraints=body['decisions'], copy=copied)
    body['final'] = dict(composition_root=None, integration_id=receipts[-1]['id'] if receipts else None,
                         claims=body['claims'], decisions=body['decisions'], cards=cards, findings=findings,
                         conditions=[row['id'] for card in cards for row in card.get('conditions', [])])
    body['answer'] = dict(ownership=task['answer_ranges'], findings=findings, conclusions=conclusions)
    if task['type'] == 'final':
        if composition_role == 'incomplete':
            body['final'] = dict(composition_root=None, origin='reviewer', decisions={'/origin': ['reviewer']})
        else:
            body['final'].update(project_terminal_material(epoch, terminal_observations or observations,
                                  integration_id=body['final']['integration_id'], tasks=tasks, contexts=contexts))
    keep = {'frame': ('frame',), 'read': (), 'combine': ('combine',),
            'original_check': ('original_check', 'segments'),
            'integrate': ('integrate', 'fragments', 'segments', 'original_check', 'retrieval'),
            'answer': ('answer',), 'final': ('final', 'retrieval')}[task['type']]
    for name in ('frame', 'fragments', 'combine', 'original_check', 'integrate', 'answer', 'final', 'retrieval', 'segments'):
        if name not in keep and not (name == 'segments' and
                ('reconciliation_id' in task or task['scope'] in ('original_segment', 'segmented_original'))):
            body.pop(name, None)


def apply_composition_plan(epoch, task, body, composition_plan, observations, tasks):
    """Bind typed page ownership or the verified terminal root to the packet."""
    if task['type'] == 'answer':
        owners = task['answer_ranges']
        page_ids = {row['page_id'] for row in owners}
        if len(page_ids) != 1:
            raise ResearchError('review_composition_conflict', 'An answer task owns exactly one page')
        body['answer'].update(protocol=composition_plan['protocol'], epoch_id=epoch['id'], role=composition_plan['role'],
            task_id=task['id'], page_id=next(iter(page_ids)), revision=0, replaces=None,
            source_root=composition_plan['source_root'], dependency_root=composition_plan['dependency_root'])
        if 'incomplete_source' in composition_plan:
            source = composition_plan['incomplete_source']
            body['answer']['source_fragments'] = [dict(owner, content=copy.deepcopy(
                [] if owner['type'] == 'empty_list' else source[owner['path'][1:]][owner['ordinal']])) for owner in owners]
    if task['type'] == 'final':
        projected = build_composition(epoch, composition_plan, observations, None, tasks=tasks)
        body['final']['composition_root'] = projected['root']['digest']
        body['final'].pop('cards', None)


def validate_composition_task(epoch, task, plan):
    """Bind the composition role, plan digest and exact page ownership before rendering."""
    if plan is None:
        if 'composition_plan_root' in task:
            raise ResearchError('review_composition_conflict', 'Supply the task\'s frozen composition plan')
        return
    from .review_composition import _validate_plan
    _validate_plan(plan)
    if plan['epoch_id'] != epoch['id'] or plan['role'] not in (epoch['role'], 'incomplete'):
        raise ResearchError('review_composition_conflict', 'The composition plan belongs to this epoch and role only')
    if task.get('composition_role', plan['role']) != plan['role'] or task.get('composition_plan_root', digest(plan)) != digest(plan):
        raise ResearchError('review_composition_conflict', 'Retain the exact frozen composition role and plan root')
    if task['type'] == 'answer':
        page_ids = {owner['page_id'] for owner in task['answer_ranges']}
        expected = [owner for owner in plan['ownership'] if owner['page_id'] in page_ids]
        if len(page_ids) != 1 or task['answer_ranges'] != expected:
            raise ResearchError('review_composition_conflict', 'The answer task must retain its exact page ownership')


def validate_answer_pages(task, output, plan):
    """Accept only the typed page owned by this exact frozen answer task."""
    from .review_composition import validate_page
    pages = output.get('pages')
    if not isinstance(pages, list) or len(pages) != 1:
        raise ResearchError('review_composition_conflict', 'An answer task returns exactly its one owned page')
    page = validate_page(pages[0], plan)
    if page['task_id'] != task['id'] or page['ownership'] != task['answer_ranges']:
        raise ResearchError('review_composition_conflict', 'The observed page must retain its frozen task and ownership')


def make_artifact_source(artifacts, reference):
    """Freeze an original body independently of any earlier delivery omission."""
    from . import review_staged as staged
    data = artifacts.read(reference)
    try:
        content = data.decode('utf-8')
    except UnicodeDecodeError:
        content = None
    binary = content is None or transport.is_binary_evidence(data, reference['media_type'])
    return staged.make_source('artifact:' + digest(reference), 'binary' if binary else 'artifact',
                              None if binary else content, identity={'artifact': reference}, descriptor=reference)


def build_manifest(artifacts, packet, role):
    """Freeze occurrence ownership independently of deduplicated original bodies."""
    from . import review_staged as staged
    sources, occurrences = [], []
    evidence = packet.get('problem_evidence', {})
    units = review_problem_parts.list_units(artifacts, evidence) if evidence else []
    owned = set()
    for unit in units:
        for reference in unit['artifacts']:
            key = digest(reference)
            if key in owned:
                continue
            owned.add(key)
            sources.append(make_artifact_source(artifacts, reference))
        occurrences.append(dict(key=unit['key'], index=unit['index'], item=unit['item'],
                                artifacts=unit['artifacts'], source_ids=[]))
    original_references = list(protocol.collect_references(packet.get('original_evidence', []))) if role == 'adjudicator' else []
    original_keys = {digest(reference) for reference in original_references}
    represented_originals = set()
    # Frame bodies and focused evidence are independent original obligations too.
    images = [page['image'] for page in packet.get('visual_source_delivery', {}).get('pages', [])]
    for entry in packet.get('evidence', []):
        reference = entry['artifact']
        if reference in images:
            continue
        if digest(reference) in owned:
            continue
        owned.add(digest(reference))
        sources.append(make_artifact_source(artifacts, reference))
        original = digest(reference) in original_keys
        if original:
            represented_originals.add(digest(reference))
        occurrences.append(dict(key='original_context' if original else 'frame_evidence', index=len(occurrences), item={'artifact': reference},
                                artifacts=[reference], source_ids=[]))
    for reference in original_references:
        key = digest(reference)
        if key not in represented_originals:
            represented_originals.add(key)
            if key not in owned:
                owned.add(key)
                sources.append(make_artifact_source(artifacts, reference))
            occurrences.append(dict(key='original_context', index=len(occurrences), item={'artifact': reference},
                                    artifacts=[reference], source_ids=[]))
    if role == 'adjudicator':
        for key in ('assessments', 'supplied_source_mappings', 'bar_calibrations'):
            for index, item in enumerate(packet.get(key, [])):
                references = list(protocol.collect_references(item))
                for reference in references:
                    identity = digest(reference)
                    if identity not in owned:
                        owned.add(identity)
                        sources.append(make_artifact_source(artifacts, reference))
                occurrences.append(dict(key=key, index=index, item=copy.deepcopy(item),
                                        artifacts=references, source_ids=[]))
    frame = {key: copy.deepcopy(value) for key, value in packet.items()
             if key not in ('evidence', 'digest', 'staged_review', review_excerpts.KEY)}
    if 'problem_evidence' in frame:
        frame['problem_evidence'] = {key: value for key, value in evidence.items()
                                     if key not in review_problem_parts.SPLIT_KEYS}
    for occurrence in occurrences:
        occurrence['evidence_class'] = ('original_context' if occurrence['key'] in
            ('original_context', 'assessments', 'supplied_source_mappings', 'bar_calibrations') else 'current')
    return staged.build_manifest(frame, occurrences, sources,
                                 inherited_findings=packet.get('material_objections', []))


def _find_predecessor_assignment_id(record):
    context = record.get('context', {})
    return (context.get('prior_assignment_id') or context.get('context_repair', {}).get('assignment_id')
            or context.get('staged_upgrade', {}).get('assignment_id'))


def _collect_successor_ancestry(records, epoch):
    """Follow the exact frozen predecessor bindings, including earlier successors."""
    result, visited = [], set()
    while epoch is not None:
        if epoch['id'] in visited:
            raise ResearchError('review_task_dependency_changed', 'A successor ancestry cannot contain a cycle')
        visited.add(epoch['id'])
        result.append(epoch)
        predecessor = epoch.get('predecessor')
        if predecessor is None:
            break
        assignment = protocol._get(records, 'review_assignment', predecessor['assignment_id'])
        if digest(assignment) != predecessor['assignment_digest']:
            raise ResearchError('review_task_dependency_changed', 'Retain the exact predecessor assignment')
        binding = assignment.get('staged_review', {})
        epoch_id = predecessor.get('epoch_id', binding.get('epoch_id'))
        if epoch_id is None:
            break
        epoch = protocol._get(records, 'review_epoch', epoch_id)
        if predecessor.get('epoch_digest', epoch['digest']) != epoch['digest']:
            raise ResearchError('review_task_dependency_changed', 'Retain the exact predecessor epoch')
    return result


def _collect_successor_findings(records, artifacts, predecessor):
    """Preserve authenticated historical adverse records as inherited obligations."""
    from . import review_staged as staged
    binding = predecessor.get('staged_review', {})
    if 'epoch_id' not in binding:
        return []
    previous = protocol._get(records, 'review_epoch', binding['epoch_id'])
    inherited = {}
    for epoch in reversed(_collect_successor_ancestry(records, previous)):
        for finding in epoch['manifest']['inherited_findings']:
            if isinstance(finding, dict) and finding.get('accepted') and finding.get('provenance'):
                inherited[finding['id']] = copy.deepcopy(finding)
        observations, origins = [], {}
        for acceptance in records.get('review_task_acceptance', {}).values():
            receipt = acceptance['receipt']
            observed, attempt = receipt.get('staged_observation'), receipt.get('attempt')
            if observed is None or observed.get('epoch_id') != epoch['id'] or attempt is None:
                continue
            child = records.get('review_assignment', {}).get(attempt['assignment_id'])
            if child is None or not authenticate_observation(records, artifacts, child, attempt):
                raise ResearchError('review_invocation_unverified', 'Authenticate inherited accepted task observations')
            raw = protocol.read_raw_observation(records, artifacts, attempt['id'])
            bound, _ = staged.bind_quotations(raw['raw_output'], epoch=epoch, sources=epoch['manifest']['sources'])
            contexts = {key:dict(value, root=child['staged_task']['context_roots'].get(key, value['root']))
                        for key,value in epoch['manifest']['contexts'].items()}
            accepted = staged.validate_task_output(child['staged_task'], bound,
                manifest=dict(epoch['manifest'], contexts=contexts), validate_records=False)
            if accepted != observed['output'] or digest(accepted) != observed['raw_output_digest']:
                raise ResearchError('review_invocation_unverified', 'Inherited science must equal its accepted observation')
            capture = records.get('review_observation', {}).get(attempt.get('reservation_id'))
            origins[observed['id']] = dict(epoch_id=epoch['id'], epoch_digest=epoch['digest'],
                task_id=observed['task_id'], task_digest=observed['task_digest'], assignment_id=child['id'],
                attempt_id=attempt['id'], attempt_digest=digest(attempt),
                observation_id=capture['id'] if capture is not None else None,
                observation_digest=digest(capture) if capture is not None else None,
                raw_output_digest=raw['raw_output_digest'], accepted_output_digest=digest(accepted))
            observations.append(observed)
        verified = staged.collect_planning_records(epoch, {'observations':observations},
                                                   tasks=list_epoch_tasks(records, epoch))
        for finding in verified['findings']:
            if not finding.get('adverse'):
                continue
            owner = next(row for row in observations if finding in row['output'].get('findings', []))
            provenance = origins[owner['id']]
            identifier = 'inherited-finding:' + digest([epoch['id'], owner['id'], finding['id']])
            inherited[identifier] = dict(id=identifier, text=finding['text'], accepted=True,
                family='findings', record=copy.deepcopy(finding), provenance=copy.deepcopy(provenance))
    return list(inherited.values())


def build_epoch(records, record, packet, manifest):
    """Freeze the slot identity, predecessor and scientific decision boundary."""
    from . import review_staged as staged
    from .review_composition import PROTOCOL as COMPOSITION_PROTOCOL, list_scalar_fields
    predecessor_id = _find_predecessor_assignment_id(record)
    predecessor = None
    if predecessor_id:
        parent = records['review_assignment'][predecessor_id]
        prior_epoch_id = parent.get('staged_review', {}).get('epoch_id')
        identifiers = {predecessor_id}
        if prior_epoch_id is not None:
            identifiers.update(row['id'] for row in records.get('review_assignment', {}).values()
                               if row.get('staged_review', {}).get('epoch_id') == prior_epoch_id)
        attempts = [row for row in records.get('review_attempt', {}).values()
                    if row['assignment_id'] in identifiers]
        retained = {row['id']:row for row in attempts}
        for acceptance in records.get('review_task_acceptance', {}).values():
            receipt = acceptance['receipt']
            attempt = receipt.get('attempt')
            if attempt is not None and attempt['assignment_id'] in identifiers:
                retained.setdefault(attempt['id'], attempt)
        attempts = list(retained.values())
        pending = [row for row in records.get('review_invocation', {}).values()
                   if row['assignment_id'] in identifiers and row['id'] not in records.get('review_attempt', {})]
        if pending:
            raise ResearchError('review_worker_reconciliation_required', 'Reconcile the predecessor before staging')
        captures = [row for row in records.get('review_observation', {}).values()
                    if prior_epoch_id is not None and records.get('review_task', {}).get(row['task_id'], {}).get('epoch_id') == prior_epoch_id]
        last = max(attempts, key=lambda row: (row.get('recorded_revision', 0), row['id']), default=None)
        captured = max(captures, key=lambda row: (row.get('recorded_revision', 0), row['id']), default=None)
        if captured is not None and (last is None or captured.get('recorded_revision', 0) > last.get('recorded_revision', 0)):
            last = next((row for row in attempts if row.get('reservation_id') == captured['id']), None)
        elif last is not None:
            captured = records.get('review_observation', {}).get(last.get('reservation_id'))
        predecessor = dict(assignment_id=predecessor_id, assignment_digest=digest(parent),
            attempt_id=last['id'] if last else None, attempt_digest=digest(last) if last else None,
            attempt_assignment_id=last['assignment_id'] if last else None,
            observation_id=captured['id'] if captured else None, observation_digest=digest(captured) if captured else None,
            no_output=not any(row.get('output') for row in attempts)
                      and not any(row['observation'].get('output') for row in captures))
        if prior_epoch_id is not None:
            previous = protocol._get(records, 'review_epoch', prior_epoch_id)
            predecessor.update(epoch_id=previous['id'], epoch_digest=previous['digest'])
    spec = dict(id='epoch:' + record['id'], controller_assignment_id=record['id'], subject=record['subject'],
                evidence_currency=packet.get('problem_review_currency', packet.get('problem_evidence_digest', {})),
                role=record['role'], slot_id=record.get('slot_id', record['id']), reviewer_id=record['reviewer_id'],
                route_id=record['route_id'], author_id=record['author_id'], task_policy_version='tasks-v1',
                composition_schema_version=COMPOSITION_PROTOCOL, calibration_profile_version='unqualified-planning-v1',
                budgets=dict(max_calls=10000, max_retries=1, max_rereads=2, max_subdivisions=10000,
                             max_epochs=2, max_elapsed_seconds=86400), manifest=manifest,
                material_objections=packet.get('material_objections', []), completion_requirements={
                    'permissible_decisions': {path: list(values) if values is not None else [record['context']['objection_id']]
                                             for path, values in list_scalar_fields(record['role']).items()}})
    if record['role'] == 'result':
        for name in ('objective_adequacy', 'method_adequacy'):
            spec['completion_requirements']['permissible_decisions']['/value/' + name] = ['not_assessed']
    previous_epochs = [row for row in records.get('review_epoch', {}).values()
                       if row['slot_id'] == spec['slot_id'] and row['evidence_currency'] == spec['evidence_currency']]
    if len(previous_epochs) >= spec['budgets']['max_epochs']:
        raise ResearchError('assessment_incomplete', 'The unchanged scientific scope exhausted its epoch recovery budget',
                            dict(slot_id=spec['slot_id'], epoch_ids=[row['id'] for row in previous_epochs],
                                 prerequisite='new_evidence_or_resolved_dependency'))
    if predecessor is not None:
        spec['predecessor'] = predecessor
    if record['role'] == 'adjudicator':
        reviews = [protocol._get(records, 'value_review', key) for key in record['context'].get('review_ids', [])]
        spec['focused_scope'] = dict(review_pair_roots=[digest(row) for row in reviews],
             objection_id=record['context']['objection_id'], objection_version=digest(packet.get('objection')))
    epoch = staged.freeze_epoch(spec)
    return epoch


def prepare_frame_tasks(records, artifacts, controller, epoch, route):
    """Render initial frame partitions, splitting until mandatory originals fit."""
    from . import review_staged as staged
    from collections import deque
    sources_by_id = {row['id']: row for row in epoch['manifest']['sources']}
    profile = build_profiles(records, artifacts, route, epoch)['frame']
    route_limit = read_input_limit(route)
    lower, upper = 0, route_limit
    while lower < upper:
        middle = (lower + upper + 1) // 2
        if staged.joint_fit([middle], route_limit, route['configuration']['max_output_tokens'], profile)['fits']:
            lower = middle
        else:
            upper = middle - 1
    target = max(1, lower)
    groups, group, characters = [], [], 0
    for field in epoch['manifest']['frame_fields']:
        source = sources_by_id[field['source_id']]
        for start in range(0, source['characters'], target):
            end = min(source['characters'], start + target)
            if group and characters + end - start > target:
                groups.append(group)
                group, characters = [], 0
            group.append(staged.source_span(source, start, end))
            characters += end - start
    if group:
        groups.append(group)
    tasks, pending = [], deque(groups)
    while pending:
        spans = pending.popleft()
        task = staged.make_task(epoch, 'frame', inputs=spans, measurement=staged.joint_fit(
            [row['end']-row['start'] for row in spans], route_limit, route['configuration']['max_output_tokens'], profile))
        try:
            tasks.append(prepare_task_plan(records, artifacts, controller, epoch, task))
        except ResearchError as error:
            if error.code != 'review_task_pending':
                raise
            if len(spans) > 1:
                middle = len(spans) // 2
                pending.appendleft(spans[middle:])
                pending.appendleft(spans[:middle])
            else:
                span = spans[0]
                if span['end'] - span['start'] <= 1:
                    raise
                source = sources_by_id[span['source_id']]
                middle = (span['start'] + span['end']) // 2
                pending.appendleft([staged.source_span(source, middle, span['end'])])
                pending.appendleft([staged.source_span(source, span['start'], middle)])
    return tasks


def prepare_controller(records, artifacts, record, packet, route):
    """Install a frozen epoch and pending slot before any child can run."""
    from . import review_staged as staged
    import time
    record['staged_started_at'] = time.time()
    predecessor_id = _find_predecessor_assignment_id(record)
    inherited = (_collect_successor_findings(records, artifacts,
        protocol._get(records, 'review_assignment', predecessor_id)) if predecessor_id is not None else [])
    if inherited:
        packet = dict(packet, inherited_review_findings=inherited)
    manifest = build_manifest(artifacts, packet, record['role'])
    if inherited:
        manifest = staged._seal(dict({key:value for key,value in manifest.items() if key != 'digest'},
                                     inherited_findings=manifest['inherited_findings'] + inherited))
    epoch = build_epoch(records, record, packet, manifest)
    frame_failure = None
    try:
        tasks = prepare_frame_tasks(records, artifacts, record, epoch, route)
    except ResearchError as error:
        if error.code != 'review_task_pending':
            raise
        task = staged.make_task(epoch, 'frame', inputs=[row['span'] for row in manifest['frame_fields']], scope='unresolved')
        tasks = [dict(id=task['id'], epoch_id=epoch['id'], generation=1, task=task,
                      controller_assignment_id=record['id'])]
        frame_failure = dict(code=error.code, message=str(error), details=error.details)
    binding = dict(protocol=staged.PROTOCOL, kind='controller', epoch_id=epoch['id'],
                   task_ids=[row['id'] for row in tasks])
    record.update(staged_review=binding, history=[], history_assignment_id=None)
    previous = records.get('review_slot_head', {}).get(epoch['slot_id'])
    slot = (staged.transition_outcome(previous, 'successor', epoch_id=epoch['id']) if previous is not None else
            dict(slot_id=epoch['slot_id'], epoch_id=epoch['id'], state='pending', outcome=None, role=epoch['role']))
    changes = [immutable_record(records, 'review_epoch', epoch['id'], epoch)]
    changes.extend(immutable_record(records, 'review_task', row['id'], row) for row in tasks)
    changes.append(('review_slot_head', slot['slot_id'], slot))
    if frame_failure is not None:
        current = dict(records)
        for kind in ('review_epoch', 'review_task', 'review_slot_head'):
            current[kind] = dict(records.get(kind, {}))
            current[kind].update({key:row for changed_kind,key,row in changes if changed_kind == kind})
        current['review_assignment'] = dict(records.get('review_assignment', {}), **{record['id']:record})
        incomplete_changes, _ = prepare_execution_incomplete(current, artifacts, epoch, tasks[0]['task'],
            'exhausted', [tasks[0]['id']], failure=frame_failure)
        changes.extend(incomplete_changes)
    return changes


def prepare_task_plan(records, artifacts, controller, epoch, task, composition_plan=None):
    """Freeze a sealed core envelope and its selected dependency frontier."""
    from . import review_staged as staged
    from .evaluation import Evaluation
    artifacts = Evaluation.of(records, artifacts)
    staged._verify_seal(task)
    fields(task, ('id', 'schema', 'protocol', 'epoch_id', 'epoch_digest', 'type', 'scope'), None, code='invalid_review_staged')
    if task['schema'] != staged.TASK_SCHEMA or task['protocol'] != staged.PROTOCOL or task['type'] not in staged.TASK_TYPES or task['scope'] not in staged.SCOPES:
        raise ResearchError('invalid_review_staged', 'Use the frozen core task schema, type and scope')
    if task['epoch_id'] != epoch['id'] or task['epoch_digest'] != epoch['digest']:
        raise ResearchError('review_task_dependency_changed', 'The task must bind this frozen epoch')
    current_contexts = derive_current_contexts(records, artifacts, epoch)
    if task['scope'] == 'unresolved' or task['measurement'] is not None and not task['measurement']['fits']:
        raise ResearchError('review_task_pending', 'Subdivide work that has not passed joint admission')
    for claim_id in task['claim_ids']:
        context = current_contexts.get(claim_id)
        if context is None or task['context_roots'].get(claim_id) != context['root']:
            raise ResearchError('review_task_dependency_changed', 'The task must retain the exact original claim context')
        if task['scope'] not in ('original_segment', 'segmented_original', 'unresolved') and not staged._covers_inputs(context['spans'], task['inputs']):
            raise ResearchError('review_task_pending', 'The task input must hold every assigned original claim context')
    if task['resource_policy'] != epoch['budgets'] or task['required'] is not True:
        raise ResearchError('review_task_dependency_changed', 'The child retains its frozen epoch resource policy')
    dependencies = []
    for task_id in task['dependencies']:
        accepted = [row['receipt'].get('staged_observation') for row in records.get('review_task_acceptance', {}).values()
                    if row['task_id'] == task_id]
        accepted = [row for row in accepted if row is not None and row['epoch_id'] == epoch['id']]
        if len(accepted) != 1:
            raise ResearchError('review_task_pending', 'The exact dependency frontier must be accepted first')
        dependencies.extend(accepted)
    terminal_observations = None
    if task['type'] == 'final':
        terminal_observations = {row['task_id']: row for row in dependencies}
        pending = [key for row in dependencies for key in row['dependencies']]
        while pending:
            key = pending.pop()
            if key in terminal_observations:
                continue
            candidates = [row['receipt'].get('staged_observation') for row in records.get('review_task_acceptance', {}).values() if row['task_id'] == key]
            if len(candidates) != 1 or candidates[0] is None or candidates[0]['epoch_id'] != epoch['id']:
                raise ResearchError('review_task_pending', 'The terminal needs its exact accepted grounding closure')
            terminal_observations[key] = candidates[0]
            pending.extend(candidates[0]['dependencies'])
        terminal_observations = list(terminal_observations.values())
        # Final science must explicitly receive every retained finding, including
        # historical adverse records outside the narrowed integration inputs.
        observed, _, obligations = collect_observations(records, artifacts, epoch)
        if obligations:
            raise ResearchError(obligations[0]['code'], obligations[0]['explanation'])
        terminal_observations = observed
    route = protocol._get(records, 'review_route', controller['route_id'])
    repair = protocol._read_json(artifacts, controller['packet']).get('context_repair')
    claim_range = set(task['claim_ids']) != {row['id'] for row in epoch['manifest']['claims']}
    prompt = protocol.canonical_prompt(epoch['role'], orchestration='problem_creation', staged_task=task,
                              repair=repair is not None, claim_range=claim_range,
                              composed=composition_plan is not None and task['type'] == 'final')
    config = route['configuration']
    profile = build_profiles(records, artifacts, route, epoch)[task['type']]
    payload_size = sum(row['end'] - row['start'] for row in task['inputs'])
    payload_size += sum(row['end'] - row['start'] for row in task['card_segments'])
    measurement = task['measurement'] or staged.joint_fit([payload_size], read_input_limit(route),
        config['max_output_tokens'], profile, claim_count=len(task['claim_ids']))
    measurement = dict(measurement, route_limit=read_input_limit(route),
                       configured_output_tokens=config['max_output_tokens'], duration_limit_seconds=min(measurement['duration_limit_seconds'], config['timeout_seconds']))
    task = staged._seal(dict({key: value for key, value in task.items() if key != 'digest'}, measurement=measurement))
    packet = build_packet(epoch, task, observations=dependencies, repair=repair, composition_plan=composition_plan,
                                  tasks=list_epoch_tasks(records, epoch),
                                  terminal_observations=terminal_observations, contexts=current_contexts)
    if composition_plan is not None and composition_plan['role'] != 'incomplete' and task['type'] == 'answer':
        from .review_staged_followups import read_composition_source_fragments
        sources = read_composition_sources(records, artifacts, composition_plan)
        packet['staged_review']['answer']['source_fragments'] = read_composition_source_fragments(
            composition_plan, sources, owners=task['answer_ranges'])
    from .review_staged_followups import plan_retrieval_tasks
    observed, _, _ = collect_observations(records, artifacts, epoch)
    retrieval_plan = plan_retrieval_tasks(epoch, observed, list_epoch_tasks(records, epoch), contexts=current_contexts)
    retrieval = retrieval_plan['bindings'].get(task['id'])
    if retrieval is not None:
        packet['staged_review']['retrieval'] = retrieval['retrievals']
        packet['staged_review']['retrieval_observations'] = retrieval['observation_ids']
        packet['staged_review']['reread'] = retrieval['reread']
    packet['staged_review']['refused_findings'] = collect_refused_findings(records, artifacts, epoch)
    from .review_staged_binary import prepare_packet
    packet = prepare_packet(records, artifacts, route, epoch, task, packet,
                            source_packet=protocol._read_json(artifacts, controller['packet']))
    packet.pop('digest', None)
    packet['digest'] = digest(packet)
    request = build_request(artifacts, route, prompt, packet)
    measurement = staged.joint_fit([measure_task_payload(task, packet)], read_input_limit(route),
        config['max_output_tokens'], profile, claim_count=len(task['claim_ids']))
    measurement['duration_limit_seconds'] = min(measurement['duration_limit_seconds'], config['timeout_seconds'])
    measurement['duration_fits'] = measurement['duration_seconds'] <= measurement['duration_limit_seconds']
    measurement['input_characters'] = review_problem_parts._measure(route, request)
    measurement['input_fits'] = measurement['input_characters'] <= measurement['route_limit']
    measurement['fits'] = all(measurement[key] for key in
        ('input_fits', 'output_fits', 'duration_fits', 'mandatory_preserved'))
    task = staged.freeze_task(task, digest(request), staged.SERIALIZATION, measurement=measurement)
    result = dict(id=task['id'], epoch_id=epoch['id'], generation=1, task=task, packet=protocol._put_json(artifacts, packet),
                request=protocol._put_json(artifacts, request), prompt=prompt, controller_assignment_id=controller['id'])
    if composition_plan is not None:
        result['composition_plan_id'] = digest(composition_plan)
    return result


def prepare_upgrade(records, value, route, problem):
    """Retain the exact predecessor boundary before entering a staged epoch."""
    upgrade = value['context']['staged_upgrade']
    fields(value['context'], ('staged_upgrade',), code='invalid_review_context')
    fields(upgrade, ('assignment_id',), code='invalid_review_context')
    upgrade_parent = protocol._get(records, 'review_assignment', upgrade['assignment_id'])
    if not problem or value['role'] not in ('result', 'adjudicator') or route['adapter'] not in ('claude_cli_v1', 'codex_cli_v1', 'codex_cli_framed_v1', 'codex_cli_visual_v1'):
        raise ResearchError('invalid_review_context', 'A staged upgrade requires a supported problem review route')
    for key in ('role', 'reviewer_id', 'author_id', 'route_id', 'subject', 'dossier_id'):
        if upgrade_parent.get(key) != value.get(key):
            raise ResearchError('review_task_dependency_changed', 'An upgrade retains the exact predecessor boundary')
    if (any(row['assignment_id'] == upgrade_parent['id'] and row['id'] not in records.get('review_attempt', {})
            for row in records.get('review_invocation', {}).values())
            or any(not row['released'] and row.get('epoch_id') == upgrade_parent.get('staged_review', {}).get('epoch_id')
                   for row in records.get('review_reservation', {}).values())):
        raise ResearchError('review_worker_reconciliation_required', 'Reconcile the predecessor invocation before upgrading')
    focused_context = ({key: copy.deepcopy(upgrade_parent['context'][key])
                        for key in ('objection_id', 'review_ids', 'correction') if key in upgrade_parent['context']}
                       if value['role'] == 'adjudicator' else {})
    value = dict(value, context=focused_context)
    return value, upgrade_parent


def prepare_child(records, artifacts, value, revision):
    """Bind a child to its controller's exact identities and immutable task request."""
    fields(value['context'], ('controller_assignment_id', 'task_id'), ('task', 'composition_plan'), code='invalid_review_context')
    controller = protocol._get(records, 'review_assignment', value['context']['controller_assignment_id'])
    binding = controller.get('staged_review', {})
    if binding.get('kind') != 'controller':
        raise ResearchError('invalid_review_context', 'A staged child names its controller assignment')
    for key in ('role', 'reviewer_id', 'author_id', 'route_id', 'dossier_id'):
        if value[key] != controller[key]:
            raise ResearchError('review_task_dependency_changed', 'A child retains the frozen controller identities')
    epoch = protocol._get(records, 'review_epoch', binding['epoch_id'])
    planned = records.get('review_task', {}).get(value['context']['task_id'])
    changes = []
    composition_plan = value['context'].get('composition_plan')
    if composition_plan is not None:
        from .review_composition import _validate_plan
        _validate_plan(composition_plan)
        if composition_plan['epoch_id'] != epoch['id']:
            raise ResearchError('review_composition_conflict', 'The composition plan belongs to this epoch only')
    if planned is None and 'task' in value['context']:
        planned = prepare_task_plan(records, artifacts, controller, epoch, value['context']['task'], composition_plan)
        if planned['id'] != value['context']['task_id']:
            raise ResearchError('review_task_dependency_changed', 'The requested id differs from the core task')
        changes.append(immutable_record(records, 'review_task', planned['id'], planned))
    if planned is None:
        raise ResearchError('review_task_pending', 'Name an existing task or supply its sealed core envelope')
    if planned['epoch_id'] != binding['epoch_id']:
        raise ResearchError('review_task_dependency_changed', 'The task belongs to another epoch')
    if any(row.get('staged_review', {}).get('task_id') == planned['id']
           for row in records.get('review_assignment', {}).values()):
        raise ResearchError('review_resampling_forbidden', 'The immutable task already has its child assignment')
    epoch = protocol._get(records, 'review_epoch', binding['epoch_id'])
    if records.get('review_slot_head', {}).get(epoch['slot_id'], {}).get('epoch_id') != epoch['id']:
        raise ResearchError('review_epoch_stale', 'Assign the current slot epoch')
    record = dict(value, subject=controller['subject'], slot_id=controller['slot_id'],
        staged_review=dict(protocol=binding['protocol'], kind='task', controller_assignment_id=controller['id'],
                           epoch_id=epoch['id'], task_id=planned['id']), staged_task=planned['task'],
        packet=planned['packet'], packet_digest=protocol._read_json(artifacts, planned['packet'])['digest'],
        planned_request_digest=planned['task']['planned_request_digest'], request=planned['request'],
        prompt=planned['prompt'], prompt_hash=planned['prompt']['rendered_prompt_hash'],
        route_digest=controller['route_digest'], context_state=controller['context_state'], assignment_policy=protocol.POLICY,
        history=[], history_assignment_id=None, session_id=value['id'],
        reassessment_required=controller.get('reassessment_required', False), recorded_revision=revision)
    if composition_plan is not None:
        record['composition_plan_id'] = digest(composition_plan)
        changes.append(immutable_record(records, 'review_composition_plan', record['composition_plan_id'], composition_plan))
    protocol.build_assignment_request(records, artifacts, record)
    return changes + [immutable_record(records, 'review_assignment', value['id'], record)], record


def read_actual_input(epoch, task, sent):
    """Bind coverage and card certificates to exact content in the fitted packet."""
    from . import review_staged as staged
    body = sent['staged_review']
    sources = {row['id']: row for row in epoch['manifest']['sources']}
    for original in body['originals']:
        span = original['span']
        if staged.materialize_quotation(sources[span['source_id']], span)['text'] != original['text']:
            raise ResearchError('review_task_dependency_changed', 'Actual original characters differ from their frozen span')
    compact = body.get('combine', {}).get('cards', [])
    full = body.get('integrate', {}).get('full_cards', body.get('final', {}).get('cards', []))
    actual = dict(history=[], intervals=[row['span'] for row in body['originals']], binary_sources=[],
                  claim_ids=[row['id'] for row in body['claims']], context_roots=body['task']['context_roots'],
                  complete_cards=[row['id'] for row in compact],
                  complete_card_roots={row['id']: digest(row) for row in compact},
                  full_card_roots={row['id']: digest(row) for row in full},
                  card_segments=[row['span'] for row in body.get('combine', {}).get('segments', [])],
                  request_measurement=task['measurement'], material_objections=sent.get('material_objections', []))
    return actual


def accept_capture(records, artifacts, assignment, captured, attempt_id):
    """Build a task receipt from the captured actual request and exact raw output."""
    from . import review_staged as staged
    task = assignment['staged_task']
    observation = captured['observation']
    attempt = dict(observation, id=attempt_id, assignment_id=assignment['id'], origin='instrumented',
                   reservation_id=captured['reservation_id'], invocation_id=captured['reservation_id'],
                   packet_digest=assignment['packet_digest'], prompt_hash=assignment['prompt_hash'],
                   recorded_revision=captured['recorded_revision'])
    if not authenticate_observation(records, artifacts, assignment, attempt):
        raise ResearchError('review_invocation_unverified', 'Authenticate the exact owned-worker capture before acceptance')
    raw = protocol._read_json(artifacts, observation['output'])
    epoch = protocol._get(records, 'review_epoch', assignment['staged_review']['epoch_id'])
    if not matches_currency(records, artifacts, epoch):
        raise ResearchError('review_task_dependency_changed', 'Capture remains historical after the scientific evidence changes')
    current_contexts = derive_current_contexts(records, artifacts, epoch)
    sent = review_excerpts.read_sent_packet(protocol._read_json(artifacts, observation['request']))
    actual = read_actual_input(epoch, task, sent)
    if task['binary_sources']:
        from .review_staged_binary import read_actual_sources
        actual['binary_sources'] = read_actual_sources(records, artifacts, epoch, task,
                                                  protocol._read_json(artifacts, observation['request']))
    bound, unmatched = staged.bind_quotations(raw, epoch=epoch, sources=epoch['manifest']['sources'])
    output = staged.validate_task_output(task, bound, manifest=dict(epoch['manifest'], contexts=current_contexts),
                                         validate_records=False)
    if task['type'] == 'final' and 'terminal' in output and 'composition_plan_id' not in assignment:
        from .review_refusal import _project_staged_final, _validate_staged_final
        scientific = _project_staged_final(assignment['role'], output)
        _validate_staged_final(records, artifacts, assignment, sent, epoch, scientific)
    if task['type'] == 'answer':
        validate_answer_pages(task, output, protocol._get(records, 'review_composition_plan', assignment['composition_plan_id']))
    dependencies = {}
    for dependency in task['dependencies']:
        candidates = [row['receipt'].get('staged_observation')
                      for row in records.get('review_task_acceptance', {}).values() if row['task_id'] == dependency]
        candidates = [row for row in candidates if row is not None]
        if len(candidates) != 1:
            raise ResearchError('review_task_pending', 'Every exact task dependency needs an accepted observation')
        dependencies[dependency] = candidates[0]['raw_output_digest']
    selected = dict(id=attempt_id, epoch_id=epoch['id'], epoch_digest=epoch['digest'], task_id=task['id'],
                    task_digest=task['digest'], actual_input=actual, request_digest=digest(protocol._read_json(artifacts, observation['request'])),
                    output=output, raw_output_digest=digest(output), dependencies=dependencies)
    staged._validate_observation(epoch, task, selected, current_contexts)
    if 'retrievals' in output:
        from .review_staged_followups import plan_retrieval_tasks
        observations, _, _ = collect_observations(records, artifacts, epoch)
        try:
            plan_retrieval_tasks(epoch, observations + [selected], list_epoch_tasks(records, epoch),
                                 contexts=current_contexts)
        except ResearchError as error:
            if error.code != 'review_task_dependency_changed':
                raise
            raise ResearchError('invalid_review_staged', 'The output retrieval changed its requesting scope',
                                dict(cause=error.code, details=error.details)) from error
    if task['type'] == 'final' and 'terminal' in output and 'composition_plan_id' not in assignment:
        observations, _, _ = collect_observations(records, artifacts, epoch)
        completion = staged.verify_completion(epoch, {'observations':observations + [selected]}, tasks=list_epoch_tasks(records, epoch))
        if not completion['complete']:
            raise ResearchError('invalid_review_staged', 'The terminal leaves required assessment unfinished',
                                {'issues':completion['issues']})
    return dict(attempt=attempt, staged_observation=selected, raw_output_digest=digest(raw), unmatched=unmatched)


def matches_currency(records, artifacts, epoch):
    """Keep frozen authenticity separate from current subject and evidence currency."""
    controller = protocol._get(records, 'review_assignment', epoch['controller_assignment_id'])
    packet = protocol._read_json(artifacts, controller['packet'])
    if protocol.build_problem_subject(records, epoch['role']) != epoch['subject']:
        return False
    if packet.get('problem_evidence_digest') is not None:
        from .problem_claims import compute_problem_evidence_digest
        return packet['problem_evidence_digest'] == compute_problem_evidence_digest(records, artifacts)
    return True


def invoke_task(store, payload, *, expected_revision, request_id):
    """Reserve, launch an owned worker once, then resume capture acceptance on replay."""
    from . import review_task_execution as execution
    from .operations import prepared_task_mutation
    fields(payload, ('id', 'assignment_id'))
    snapshot = _read_preparation(store)
    if snapshot.has_request(request_id):
        return prepared_mutation(store, 'review.invoke', payload, lambda r, v: None,
                                 expected_revision=expected_revision, request_id=request_id, _snapshot=snapshot)
    records = snapshot.records
    assignment = protocol._get(records, 'review_assignment', payload['assignment_id'])
    binding = assignment['staged_review']
    if binding['kind'] != 'task':
        raise ResearchError('review_task_pending', 'Assign and run a child of this staged controller')
    route = protocol._get(records, 'review_route', assignment['route_id'])
    controller_id = binding['controller_assignment_id']
    if any(row['assignment_id'] in (assignment['id'], controller_id) and row['kind'] == 'contamination'
           for row in records.get('review_context_event', {}).values()):
        raise ResearchError('review_context_contaminated', 'Repair the contaminated epoch before dispatch')
    if assignment['route_id'] in protocol.retired_routes(records):
        raise ResearchError('review_route_retired', 'Repair the slot on an eligible route')
    if not protocol._prompt_matches(assignment) or digest(route) != assignment['route_digest']:
        raise ResearchError('review_task_dependency_changed', 'The frozen route or staged prompt changed')
    epoch = protocol._get(records, 'review_epoch', binding['epoch_id'])
    contexts = derive_current_contexts(records, store.build_artifact_store(), epoch)
    if not match_task_contexts(assignment['staged_task'], contexts):
        raise ResearchError('review_task_dependency_changed', 'Replan this task under the current discovered claim closure')
    protocol._require_route_runtime(route)
    request = protocol.build_assignment_request(records, store.build_artifact_store(), assignment)
    epoch = protocol._get(records, 'review_epoch', binding['epoch_id'])
    head = protocol._get(records, 'review_slot_head', epoch['slot_id'])
    if not matches_currency(records, store.build_artifact_store(), epoch):
        raise ResearchError('review_epoch_stale', 'The frozen scientific evidence changed before dispatch')
    if head['epoch_id'] != epoch['id']:
        raise ResearchError('review_epoch_stale', 'The current slot has a successor epoch')
    if head['state'] != 'pending':
        raise ResearchError('assessment_incomplete' if head['state'] == 'incomplete' else 'review_resampling_forbidden',
                            'The epoch already selected its immutable slot outcome')
    plan = dict(task_id=binding['task_id'], task_generation=1, epoch_id=epoch['id'], route_id=assignment['route_id'],
                request=protocol._put_json(store.build_artifact_store(), request), max_attempts=epoch['budgets']['max_retries'] + 1,
                dependencies=[dict(kind='review_assignment', key=assignment['id'], digest=digest(assignment))],
                selectors=[dict(kind='review_slot_head', key=epoch['slot_id'], digest=digest(head))])
    def prepare_dispatch(current_records, value):
        current = protocol._get(current_records, 'review_assignment', value['assignment_id'])
        if digest(current) != digest(assignment):
            raise ResearchError('review_task_dependency_changed', 'The task assignment changed before dispatch')
        dispatch = dict(value, task_id=binding['task_id'], request_digest=digest(request))
        return [immutable_record(current_records, 'review_task_dispatch', request_id, dispatch)], dispatch
    prepared_task_mutation(store, 'review.task_dispatch', payload, prepare_dispatch,
                           request_id=request_id + ':dispatch', expected_revision=expected_revision)
    state = records.get('review_task_execution', {}).get(binding['task_id'])
    reservation = records.get('review_reservation', {}).get(state['reservation_id']) if state else None
    previous_capture = records.get('review_observation', {}).get(reservation['observation_id']) if reservation else None
    retryable = (reservation is not None and reservation['released']
                 and (reservation['state'] == 'refused' or previous_capture is None or previous_capture['observation']['status'] != 'completed'))
    if reservation is None or retryable:
        try:
            reserved = execution.reserve_task(store, plan, request_id=request_id + ':reserve')
        except ResearchError as error:
            if error.code != 'assessment_incomplete':
                raise
            def prepare_exhaustion(current_records, value):
                return prepare_execution_incomplete(current_records, store.build_artifact_store(), epoch,
                    assignment['staged_task'], 'exhausted', [assignment['staged_task']['id']],
                    failure=dict(code=error.code, message=str(error), details=error.details))
            return prepared_task_mutation(store, 'review.invoke', payload, prepare_exhaustion, request_id=request_id)
        reservation = reserved['result']
    captured, process, current = wait_for_capture(store, route, reservation, request_id)
    if captured['observation']['status'] != 'completed':
        finish_worker(store, current['id'], process, request_id)
        def prepare_failure(current_records, value):
            attempt = dict(captured['observation'], **value, origin='instrumented',
                           reservation_id=captured['reservation_id'], invocation_id=captured['reservation_id'],
                           packet_digest=assignment['packet_digest'], prompt_hash=assignment['prompt_hash'],
                           recorded_revision=captured['recorded_revision'])
            return [immutable_record(current_records, 'review_attempt', value['id'], attempt)], dict(
                attempt, next=[describe_planning_command(controller_id)])
        return prepared_task_mutation(store, 'review.invoke', payload, prepare_failure, request_id=request_id)
    try:
        receipt = execution.accept_observation(store, {'reservation_id': current['id']},
             lambda current_records, capture: accept_capture(current_records, store.build_artifact_store(),
                                                                    assignment, capture, payload['id']),
             request_id=request_id + ':accept')['result']
        if 'refusal' not in receipt and receipt['receipt']['attempt']['id'] != payload['id']:
            raise ResearchError('review_resampling_forbidden', 'Resume the originally named task attempt')
    finally:
        finish_worker(store, current['id'], process, request_id)
    def prepare(current_records, value):
        if 'refusal' in receipt:
            attempt = dict(captured['observation'], **value, origin='instrumented',
                reservation_id=captured['reservation_id'], invocation_id=captured['reservation_id'],
                packet_digest=assignment['packet_digest'], prompt_hash=assignment['prompt_hash'],
                recorded_revision=captured['recorded_revision'])
            return [immutable_record(current_records, 'review_attempt', value['id'], attempt)], dict(attempt,
                refusal=receipt['refusal'], next=[describe_planning_command(controller_id)])
        changes, attempt = prepare_attempt(current_records, store.build_artifact_store(), assignment, receipt['receipt'])
        return changes, dict(attempt, next=[describe_planning_command(controller_id)])
    return prepared_task_mutation(store, 'review.invoke', payload, prepare, request_id=request_id)


def finish_worker(store, reservation_id, process, request_id):
    """Join an exited worker and release its reconciled reservation."""
    import subprocess
    from . import review_task_execution as execution
    if process is not None:
        try:
            process.wait(timeout=2)
        except subprocess.TimeoutExpired:
            pass
    execution.reconcile_worker(store, {'reservation_id': reservation_id},
                               request_id=request_id + ':reconcile:' + str(store.revision))


def wait_for_capture(store, route, reservation, request_id):
    """Launch once or reconcile the owned worker, then await its durable capture."""
    import time
    from . import review_task_execution as execution, review_worker
    process = None
    if reservation['launch'] is not None and reservation['observation_id'] is None:
        execution.reconcile_worker(store, {'reservation_id': reservation['id']},
                                   request_id=request_id + ':resume:' + str(reservation['generation']))
    if reservation['launch'] is None and reservation['state'] == 'reserved' and not reservation['released']:
        process = review_worker.launch_worker(store, reservation['id'])
    deadline = time.monotonic() + route['configuration']['timeout_seconds'] + 15
    while True:
        records = store.snapshot()['records']
        current = records['review_reservation'][reservation['id']]
        if current['observation_id'] is not None:
            break
        if current['released'] or process is not None and process.poll() is not None:
            execution.reconcile_worker(store, {'reservation_id': current['id']}, request_id=request_id + ':reconcile')
            raise ResearchError('review_worker_reconciliation_required', 'The worker ended without a committed observation')
        if time.monotonic() >= deadline:
            raise ResearchError('review_worker_reconciliation_required', 'Resume the owned reservation; its outcome is still pending')
        time.sleep(.05)
    captured = records['review_observation'][current['observation_id']]
    return captured, process, current


def prepare_attempt(records, artifacts, assignment, receipt):
    """Record a captured attempt and its distinct incomplete slot outcome atomically."""
    from . import review_staged as staged
    attempt = receipt['attempt']
    changes = [immutable_record(records, 'review_attempt', attempt['id'], attempt)]
    if assignment['staged_task']['type'] != 'final':
        return changes, attempt
    observed = receipt['staged_observation']
    incomplete = observed['output'].get('assessment_incomplete')
    plan_id = assignment.get('composition_plan_id')
    plan = records.get('review_composition_plan', {}).get(plan_id) if plan_id is not None else None
    if incomplete is None and (plan is None or plan['role'] != 'incomplete'):
        return changes, attempt
    epoch = protocol._get(records, 'review_epoch', assignment['staged_review']['epoch_id'])
    observations, _, obligations = collect_observations(records, artifacts, epoch)
    if obligations:
        raise ResearchError(obligations[0]['code'], obligations[0]['explanation'])
    tasks = list_epoch_tasks(records, epoch)
    completion = staged.verify_completion(epoch, {'observations': observations}, tasks=tasks)
    if incomplete is None:
        projected = build_composition(epoch, plan, observations, observed, tasks=tasks)
        incomplete = projected['incomplete_output']
        selection = dict(observations=observations, composition_certificate=dict(complete=True,
            epoch_digest=epoch['digest'], composition_root=projected['root']['digest'],
            selected_observations_root=completion['selected_observations_root']))
        completion = staged.verify_completion(epoch, selection, tasks=tasks)
    outcome = build_incomplete_outcome(records, artifacts, epoch, completion, attempt, incomplete)
    changes.append(immutable_record(records, 'assessment_incomplete', outcome['id'], outcome))
    head = staged.transition_outcome(records['review_slot_head'][epoch['slot_id']],
                                     'incomplete', epoch_id=epoch['id'], outcome=outcome)
    changes.append(('review_slot_head', epoch['slot_id'], head))
    return changes, attempt


def read_raw_observation(records, artifacts, attempt_id):
    """Read exact committed observation bytes; never assemble a scientific result."""
    attempt = records.get('review_attempt', {}).get(attempt_id)
    captured = records.get('review_observation', {}).get(attempt_id)
    if attempt is None and captured is None:
        dispatch = next((row for row in records.get('review_task_dispatch', {}).values() if row['id'] == attempt_id), None)
        if dispatch is not None:
            execution = records.get('review_task_execution', {}).get(dispatch['task_id'], {})
            captured = records.get('review_observation', {}).get(execution.get('reservation_id'))
            if captured is not None:
                attempt = dict(captured['observation'], id=attempt_id, origin=captured['origin'],
                               assignment_id=dispatch['assignment_id'], reservation_id=captured['reservation_id'],
                               recorded_revision=captured['recorded_revision'])
    if attempt is None and captured is None:
        for acceptance in records.get('review_task_acceptance', {}).values():
            candidate = acceptance['receipt'].get('attempt')
            if candidate is not None and candidate['id'] == attempt_id:
                attempt = candidate
                captured = records.get('review_observation', {}).get(acceptance['reservation_id'])
                break
    if attempt is not None and attempt.get('reservation_id'):
        captured = protocol._get(records, 'review_observation', attempt['reservation_id'])
    if attempt is None:
        if captured is None:
            raise ResearchError('review_output_unavailable', 'The exact committed observation is absent')
        attempt = dict(captured['observation'], id=attempt_id, origin=captured['origin'])
    return protocol._read_raw_observation(artifacts, attempt_id, attempt, captured)


def authenticate_observation(records, artifacts, assignment, attempt):
    """Authenticate fresh request, capture and runtime without scientific selection."""
    try:
        captured = protocol._get(records, 'review_observation', attempt['reservation_id'])
        observation = captured['observation']
        route = protocol._get(records, 'review_route', assignment['route_id'])
        request = protocol.build_assignment_request(records, artifacts, assignment)
        response = protocol._read_json(artifacts, observation['response'])
        return (captured['origin'] == 'instrumented' and attempt['origin'] == 'instrumented'
                and captured['task_id'] == assignment['staged_review']['task_id']
                and all(attempt.get(name) == observation.get(name) for name in ('request', 'response', 'output'))
                and observation['status'] == 'completed'
                and digest(route) == assignment['route_digest'] == observation['route_digest']
                and observation['runtime'] == route['runtime']
                and protocol._read_json(artifacts, observation['request']) == request
                and request.get('history') == [] and len(transport.logical_messages(request)) == 1
                and digest(request) == assignment['planned_request_digest']
                and transport.response_output(response) == protocol._read_json(artifacts, observation['output'])
                and protocol.verify_route_context(artifacts, route, request, response))
    except (ResearchError, KeyError, TypeError, ValueError):
        return False


def build_composition_bindings(epoch, observations, *, tasks=()):
    """Bind a page plan to the verified selection and its complete frozen scope."""
    from . import review_staged as staged
    # A plan freezes the grounding selection before its answer pages exist.
    # Including those pages would require their outputs to bind their own plan.
    grounding = [row for row in observations if not row['output'].get('pages')]
    completion = staged.verify_completion(epoch, {'observations': grounding}, tasks=tasks)
    integrations = [row for observation in observations for row in observation['output'].get('integrations', [])]
    findings = sorted({row['id'] for observation in observations for row in observation['output'].get('findings', [])}
                      | {key for observation in observations for page in observation['output'].get('pages', []) for key in page['findings']}
                      | {row['id'] for row in epoch['manifest']['inherited_findings']})
    obligations = [dict(source_id=row['id'], intervals=row['required_intervals'], descriptor=row['descriptor'])
                   for row in epoch['manifest']['sources']]
    obligations.extend(epoch['material_objection_entries'])
    currency = epoch['evidence_currency']
    return dict(evidence_currency=currency if isinstance(currency, str) and len(currency) == 64 else digest(currency),
                source_root=epoch['manifest']['digest'],
                dependency_root=digest(dict(epoch_digest=epoch['digest'],
                    context_roots={key: row['root'] for key, row in epoch['manifest']['contexts'].items()})),
                selected_observations_root=completion['selected_observations_root'],
                integration_root=digest(integrations), findings=findings, evidence_obligations=obligations)


def build_composition(epoch, plan, observations, terminal_observation, *, tasks=()):
    """Project authenticated page envelopes and derive the authorized terminal."""
    from . import review_staged as staged, review_composition as composition
    for name in ('epoch_id', 'slot_id', 'subject'):
        expected = epoch['id'] if name == 'epoch_id' else epoch[name]
        if plan[name] != expected:
            raise ResearchError('review_composition_conflict', 'The composition belongs to another frozen epoch')
    if plan['role'] not in (epoch['role'], 'incomplete'):
        raise ResearchError('review_composition_conflict', 'The composition role differs from its frozen epoch')
    prior = [row for row in observations if terminal_observation is None or row['id'] != terminal_observation['id']]
    expected = build_composition_bindings(epoch, prior, tasks=tasks)
    if any(plan[name] != value for name, value in expected.items()):
        raise ResearchError('review_composition_conflict', 'The plan must bind the exact source, selection, integration and finding scope')
    pages = []
    raw_bindings = []
    task_index = {task['id']: task for task in tasks}
    for observation in observations:
        if observation['output'].get('pages') and observation.get('task_id') is not None:
            assigned = task_index.get(observation['task_id'])
            if assigned is None or assigned['type'] != 'answer':
                raise ResearchError('review_composition_conflict', 'Every page must bind its frozen answer task')
            validate_composition_task(epoch, assigned, plan)
            validate_answer_pages(assigned, observation['output'], plan)
        for page in observation['output'].get('pages', []):
            pages.append(dict(attempt_id=observation['id'], raw_output_digest=digest(page), output=page))
            raw_bindings.append(dict(attempt_id=observation['id'], raw_output_digest=digest(observation['output'])))
    root = composition.build_root(plan, pages)
    result = dict(plan=plan, observations=pages, root=root, raw_page_bindings=raw_bindings)
    if terminal_observation is not None:
        raw = terminal_observation['output']
        derived = staged.derive_composition_terminal(epoch, raw['terminal'])
        output = composition.assemble_result(plan, pages, root, derived)
        result.update(terminal=dict(attempt_id=terminal_observation['id'], raw_output_digest=digest(derived), output=derived),
                      raw_terminal_digest=digest(raw))
        if plan['role'] == 'incomplete':
            result['incomplete_output'] = output
    return result


def build_incomplete_outcome(records, artifacts, epoch, completion, attempt, incomplete):
    """Bind an incomplete outcome to its actual terminal capture, for every caller."""
    from . import review_staged as staged
    reservations = [row for row in records.get('review_reservation', {}).values() if row['epoch_id'] == epoch['id']]
    return staged.record_incomplete(epoch, completion, origin='reviewer',
        origin_binding=dict(attempt_id=attempt['id'],
            raw_output_digest=read_raw_observation(records, artifacts, attempt['id'])['raw_output_digest']),
        unresolved_scope=incomplete['unresolved_scope'], recovery_work=incomplete['recovery_work'],
        resource_ledger=dict(attempts=len(reservations), unknown_usage=True),
        recorded_revision=attempt['recorded_revision'])


def build_selection(records, artifacts, epoch, controller_id, binding, observations, terminal, state):
    """Verify the frozen science selection and preserve distinct incomplete outcomes."""
    from . import review_staged as staged
    # No assignment_state call occurs inside task selection or completion.
    required_tasks = list_epoch_tasks(records, epoch)
    completion_selection = {'observations': observations}
    completion = staged.verify_completion(epoch, completion_selection, tasks=required_tasks)
    paged = None
    if terminal is not None:
        terminal_attempt, terminal_observed = terminal
        terminal_child = records['review_assignment'][terminal_attempt['assignment_id']]
        plan_id = terminal_child.get('composition_plan_id')
        if plan_id is not None:
            try:
                paged = build_composition(epoch, protocol._get(records, 'review_composition_plan', plan_id),
                                                   observations, terminal_observed, tasks=required_tasks)
                completion_selection['composition_certificate'] = dict(complete=True, epoch_digest=epoch['digest'],
                    composition_root=paged['root']['digest'], selected_observations_root=completion['selected_observations_root'])
                completion = staged.verify_completion(epoch, completion_selection, tasks=required_tasks)
            except ResearchError as error:
                state['obligations'].append(obligation(error.code, str(error)))
    selection = dict(controller_id=controller_id, kind='pending',
                     selected_observations_root=completion['selected_observations_root'],
                     completion_root=completion['digest'], context_state=state['context_status'])
    if terminal is not None:
        attempt, observed = terminal
        raw = read_raw_observation(records, artifacts, attempt['id'])
        if binding['kind'] == 'controller':
            state.update(raw_output=raw['raw_output'], raw_output_digest=raw['raw_output_digest'], attempt_id=attempt['id'])
        selection.update(terminal_attempt_id=attempt['id'], terminal_raw_output_digest=raw['raw_output_digest'])
        if completion['complete'] and 'assessment_incomplete' not in observed['output']:
            if paged is not None:
                selection.update(paged, kind='paged')
            else:
                output, _ = staged.project_ordinary_final(observed['output'])
                selection.update(kind='ordinary', output=output, output_digest=digest(output))
    outcomes = [row for row in records.get('assessment_incomplete', {}).values() if row['epoch_id'] == epoch['id']]
    if outcomes:
        selection.update(kind='incomplete', outcome=outcomes[-1])
        selection.pop('output', None)
        selection.pop('output_digest', None)
    return selection, completion, paged


def build_state(records, artifacts, assignment):
    """Select authenticated task receipts before invoking the pure completion boundary."""
    from . import review_staged as staged, review_composition as composition
    binding = assignment['staged_review']
    controller_id = assignment['id'] if binding['kind'] == 'controller' else binding['controller_assignment_id']
    epoch = protocol._get(records, 'review_epoch', binding['epoch_id'])
    state = dict(protocol.route_state(records, artifacts, assignment['route_id']), ready=False, obligations=[],
                 reviewer_id=assignment['reviewer_id'], dossier_id=assignment['dossier_id'], role=assignment['role'],
                 stage=assignment['role'], packet_digest=assignment['packet_digest'], prompt_hash=assignment['prompt_hash'],
                 raw_output=None, raw_output_digest=None, output=None, output_digest=None, attempt_id=None,
                 refused_attempts=[], staged_review=copy.deepcopy(binding))
    observations, terminal, selection_obligations = collect_observations(records, artifacts, epoch)
    state['obligations'].extend(selection_obligations)
    if any(row['code'] == 'review_context_contaminated' for row in selection_obligations):
        state['context_status'] = 'contaminated'
    if binding['kind'] == 'task':
        execution = records.get('review_task_execution', {}).get(binding['task_id'], {})
        capture = records.get('review_observation', {}).get(execution.get('reservation_id'))
        if capture is not None:
            raw = read_raw_observation(records, artifacts, capture['id'])
            state.update(raw_output=raw['raw_output'], raw_output_digest=raw['raw_output_digest'])
        selected = next((row for row in observations if row['task_id'] == binding['task_id']), None)
        if selected is not None:
            raw = read_raw_observation(records, artifacts, selected['id'])
            state.update(raw_output=raw['raw_output'], raw_output_digest=raw['raw_output_digest'],
                         attempt_id=selected['id'])
    selection, completion, paged = build_selection(records, artifacts, epoch, controller_id,
                                                   binding, observations, terminal, state)
    if any(row['assignment_id'] in (assignment['id'], controller_id) and row['kind'] == 'contamination'
           for row in records.get('review_context_event', {}).values()):
        state['context_status'] = 'contaminated'
        selection['context_state'] = 'contaminated'
    state['effective'] = composition.effective_review_output(controller_id, selection)
    if paged is not None and state['effective']['kind'] == 'paged':
        # The page plan binds the grounding selection before pages exist. The
        # effective review binds the complete selected graph, including pages.
        state['effective']['selected_observations_root'] = completion['selected_observations_root']
        state['effective']['terminal_raw_output_digest'] = read_raw_observation(records, artifacts,
            selection['terminal_attempt_id'])['raw_output_digest']
        state['effective']['raw_page_bindings'] = [dict(attempt_id=row['attempt_id'],
            raw_output_digest=read_raw_observation(records, artifacts, row['attempt_id'])['raw_output_digest'])
            for row in paged['raw_page_bindings']]
    state['effective']['currency_state'] = ('current' if matches_currency(records, artifacts, epoch) else 'stale')
    if state['effective']['currency_state'] == 'stale':
        state['obligations'].append(obligation('review_epoch_stale', 'The frozen scientific evidence changed.'))
    state['completion'] = completion
    state['next'] = [describe_planning_command(controller_id)]
    if binding['kind'] == 'controller':
        state.update(output=state['effective'].get('effective_output'),
                     output_digest=state['effective'].get('effective_output_digest'))
    else:
        # A task can be accepted without being a scientific review result.
        state['accepted'] = any(row['task_id'] == binding['task_id'] for row in observations)
    head = records.get('review_slot_head', {}).get(epoch['slot_id'], {})
    if head.get('epoch_id') != epoch['id']:
        state['obligations'].append(obligation('review_epoch_stale', 'Use the current slot epoch.'))
    if state['context_status'] != 'verified_for_route':
        state['obligations'].append(obligation('review_context_' + state['context_status'], 'Verify the assigned route context.'))
    if state['output'] is None:
        state['obligations'].append(obligation('review_task_pending', 'Complete the frozen epoch tasks.',
                                              controller_assignment_id=controller_id, epoch_id=epoch['id']))
    state['ready'] = not state['obligations']
    return state


def build_prompt(role, staged_task, *, repair, composed, claim_range, orchestration,
                 common_version, evidence_parts=None, reading_stage=None):
    """Render the canonical staged suffix without altering the ordinary prompt."""
    from . import review_staged_prompts as staged_prompts
    if (type(composed) is not bool or type(claim_range) is not bool
            or orchestration != 'problem_creation' or role not in ('result', 'adjudicator')
            or common_version != 2 or evidence_parts is not None or reading_stage is not None):
        raise ResearchError('invalid_review_context', 'Staged prompts require the current problem review contract')
    base = protocol.canonical_prompt(role, orchestration=orchestration, common_version=2)
    descriptor = {key: copy.deepcopy(staged_task.get(key)) for key in
                  ('type', 'scope', 'claim_ids', 'occurrence_ids', 'group_ids')}
    parameters = dict(role=role, orchestration=orchestration, staged_task=descriptor,
                      staged_review=staged_prompts.PROTOCOL, composed=composed, claim_range=claim_range)
    if repair:
        parameters['context_repair'] = True
    suffix = staged_prompts.compose_task_prompt(role, descriptor, repair=repair,
                                                composed=composed, claim_range=claim_range)
    return protocol._build_prompt(staged_prompts.PROTOCOL + '/' + role + '/' + descriptor['type'],
                         base['text'] + suffix, parameters, 2)


def build_assigned_request(artifacts, route, assignment, packet):
    """Reconstruct and verify the exact request frozen for a child assignment."""
    if assignment['staged_review']['kind'] != 'task':
        raise ResearchError('review_task_pending', 'Assign a controller child task before dispatch')
    if assignment.get('history') != []:
        raise ResearchError('review_task_dependency_changed', 'Staged task history must stay empty')
    request = build_request(artifacts, route, assignment['prompt'], packet)
    if digest(request) != assignment['planned_request_digest']:
        raise ResearchError('review_task_dependency_changed', 'The exact planned task request changed')
    return request


def matches_prompt(assignment, common_version):
    """Match a child prompt independently of mutable task execution state."""
    parameters = assignment['prompt']['parameters']
    from .review_staged import PROTOCOL as STAGED_PROTOCOL
    try:
        task = assignment.get('staged_task', parameters['staged_task'])
        return (parameters['staged_review'] == STAGED_PROTOCOL
                and assignment.get('subject', {}).get('kind') == 'problem'
                and assignment['prompt'] == protocol.canonical_prompt(assignment['role'],
                    orchestration='problem_creation', staged_task=task, common_version=common_version,
                    repair=parameters.get('context_repair', False),
                    composed=parameters.get('composed', False), claim_range=parameters.get('claim_range', False)))
    except (ResearchError, KeyError, TypeError):
        return False


def measure_task_payload(task, packet=None):
    """Measure original or selected scientific work independently of prompt overhead."""
    characters = sum(row['end'] - row['start'] for row in task['inputs'])
    characters += sum(row['end'] - row['start'] for row in task['card_segments'])
    section = dict(combine='combine', original_check='original_check', integrate='integrate',
                   answer='answer', final='final').get(task['type'])
    if packet is not None and section is not None:
        material = packet.get('staged_review', {}).get(section, {})
        characters = max(characters, len(_canonical(material)))
    return characters


def build_profiles(records, artifacts, route, epoch=None):
    """Estimate each work dimension separately, refining bootstrap bounds from captures."""
    from . import review_staged as staged
    config = route['configuration']
    profiles = {}
    for kind in staged.TASK_TYPES:
        # Bootstrap estimates are planning bounds, never empirical qualification.
        prompt = protocol.canonical_prompt('result' if epoch is None else epoch['role'],
            orchestration='problem_creation', staged_task=dict(type=kind, scope='joint_original',
                claim_ids=[], occurrence_ids=[], group_ids=[]))
        profiles[kind] = dict(context_characters=len(prompt['text']), envelope_characters=4000,
            output_base_tokens=128, output_tokens_per_claim=100, output_tokens_per_character=0.5 if kind == 'frame' else 0.01,
            seconds_per_character=0.0001, seconds_per_claim=0.1, duration_base_seconds=1,
            duration_limit_seconds=config['timeout_seconds'], output_margin_tokens=max(1, config['max_output_tokens'] // 10))
    for captured in records.get('review_observation', {}).values():
        if epoch is not None and captured.get('task_id') not in records.get('review_task', {}):
            continue
        planned = records.get('review_task', {}).get(captured.get('task_id'), {})
        task = planned.get('task')
        if task is None or planned.get('controller_assignment_id') is None:
            continue
        owner = records.get('review_assignment', {}).get(planned['controller_assignment_id'], {})
        if owner.get('route_id') != route['id']:
            continue
        observed = captured['observation']
        profile = profiles[task['type']]
        packet = protocol._read_json(artifacts, planned['packet']) if planned.get('packet') is not None else None
        work = max(1, measure_task_payload(task, packet))
        usage = observed.get('usage') or {}
        tokens = usage.get('output_tokens')
        if type(tokens) in (int, float) and tokens > 0:
            profile['output_tokens_per_character'] = max(profile['output_tokens_per_character'], tokens / work)
        seconds = usage.get('wall_seconds')
        if type(seconds) in (int, float) and seconds > 0:
            profile['seconds_per_character'] = max(profile['seconds_per_character'], seconds / work)
    return profiles


def list_epoch_tasks(records, epoch):
    """Select immutable active envelopes while retaining superseded work in history."""
    replaced = {row['task_id'] for row in records.get('review_task_repair', {}).values()
                if row['epoch_id'] == epoch['id']}
    return [row['task'] for key, row in records.get('review_task', {}).items()
            if row['epoch_id'] == epoch['id'] and key not in replaced]


def describe_planning_command(controller_id):
    return dict(kind='command', command='review-assignment', payload=dict(controller_assignment_id=controller_id))


def split_task(epoch, task, *, cards=(), observations=()):
    """Partition one failed work dimension without losing its original obligation union."""
    from . import review_staged as staged
    options = {key: copy.deepcopy(task[key]) for key in ('inputs', 'claim_ids', 'occurrence_ids', 'dependencies',
        'binary_sources', 'card_ids', 'card_segments', 'group_ids', 'segment_task_ids', 'obligation_ids', 'child_ids',
        'answer_ranges', 'objection_entries')}
    for key in ('reconciliation_id', 'composition_role', 'composition_plan_root'):
        if key in task:
            options[key] = task[key]
    scope = task['scope']
    options['contexts'] = {key:dict(context, root=task['context_roots'].get(key, context['root']))
                           for key,context in epoch['manifest']['contexts'].items()}
    integration_scope = task['type'] == 'integrate' and not task['occurrence_ids']
    catalog = {card['id']:card for card in cards if card['kind'] == 'full'}
    if integration_scope and not options['child_ids']:
        if not options['card_ids']:
            options['card_ids'] = [key for key, card in catalog.items()
                                  if not options['obligation_ids'] or
                                  set(staged.card_obligations([card])).intersection(options['obligation_ids'])]
        if not options['obligation_ids'] and options['card_ids']:
            options['obligation_ids'] = staged.card_obligations([catalog[key] for key in options['card_ids'] if key in catalog])
    preferred = ('child_ids', 'card_ids', 'obligation_ids') if integration_scope else ()
    dimension = next((key for key in preferred + ('claim_ids', 'answer_ranges', 'inputs', 'card_segments', 'obligation_ids')
                      if len(options[key]) > 1), None)
    if dimension is not None:
        values = options[dimension]
        middle = len(values) // 2
        subsets = [values[:middle], values[middle:]]
    elif task['inputs'] and task['inputs'][0]['end'] - task['inputs'][0]['start'] > 1:
        dimension = 'inputs'
        span = task['inputs'][0]
        source = next(row for row in epoch['manifest']['sources'] if row['id'] == span['source_id'])
        middle = (span['start'] + span['end']) // 2
        subsets = [[staged.source_span(source, span['start'], middle)], [staged.source_span(source, middle, span['end'])]]
    else:
        return []
    if dimension == 'inputs' and task['type'] != 'frame':
        scope = 'original_segment'
        options['reconciliation_id'] = options.get('reconciliation_id') or 'reconciliation:' + digest(task['id'])
    children = []
    for values in subsets:
        selected = dict(options, **{dimension:values})
        if integration_scope:
            if dimension == 'card_ids':
                owned = set(staged.card_obligations([catalog[key] for key in values if key in catalog]))
                selected['obligation_ids'] = [key for key in options['obligation_ids'] if key in owned]
            elif dimension == 'obligation_ids':
                selected['card_ids'] = [key for key in options['card_ids'] if key not in catalog or
                                       set(staged.card_obligations([catalog[key]])).intersection(values)]
            elif dimension == 'child_ids':
                selected.update(card_ids=[], obligation_ids=[])
            if observations:
                selected_cards = [catalog[key] for key in selected['card_ids'] if key in catalog]
                dependencies = []
                for observed in observations:
                    if any(key in task['context_roots'] and root != task['context_roots'][key]
                           for key,root in observed['actual_input']['context_roots'].items()):
                        continue
                    output = observed['output']
                    if (any(card in output.get('cards', []) for card in selected_cards)
                            or any(row['id'] in selected['child_ids'] for row in output.get('integrations', []))
                            or any(set(row.get('card_ids', [])).intersection(selected['card_ids'])
                                   for row in output.get('combinations', []))
                            or any(row['id'] in selected['obligation_ids'] for row in output.get('cells', []))):
                        dependencies.append(observed['task_id'])
                selected['dependencies'] = sorted(set(dependencies))
        child = staged.make_task(epoch, task['type'], scope=scope, **selected)
        children.append(child)
    return children


def prepare_execution_incomplete(records, artifacts, epoch, task, reason, receipts, *, failure=None):
    """Bind an execution failure to a persisted distinct outcome and pending slot."""
    from . import review_staged as staged
    receipt_changes, receipt_ids = [], []
    for identifier in receipts:
        if (identifier in records.get('review_reservation', {}) or identifier in records.get('review_task_refusal', {})
                or identifier in records.get('review_task_repair', {})):
            receipt_ids.append(identifier)
        else:
            receipt_id = 'planning-failure:' + digest([epoch['digest'], task['digest'], reason, failure])
            receipt = dict(id=receipt_id, epoch_id=epoch['id'], task_id=task['id'], task=task,
                           route_id=epoch['route_id'], reason=reason, failure=failure)
            receipt_changes.append(immutable_record(records, 'review_task_refusal', receipt_id, receipt))
            receipt_ids.append(receipt_id)
    observations, _, _ = collect_observations(records, artifacts, epoch)
    completion = staged.verify_completion(epoch, {'observations':observations}, tasks=list_epoch_tasks(records, epoch))
    scope = dict(task_ids=[task['id']], claim_ids=task['claim_ids'], occurrence_ids=task['occurrence_ids'],
                 inputs=task['inputs'], binary_sources=task['binary_sources'], remaining_issues=completion['issues'],
                 refused_findings=collect_refused_findings(records, artifacts, epoch))
    action = dict(workflow='review-assignment', scope=dict(controller_assignment_id=epoch['controller_assignment_id']),
                  prerequisite='new_evidence_or_eligible_recovery')
    controller = records['review_assignment'][epoch['controller_assignment_id']]
    successor = {key:controller[key] for key in ('role', 'reviewer_id', 'author_id', 'route_id', 'dossier_id')}
    successor.update(id='recovery:' + epoch['id'], context=dict(staged_upgrade=dict(assignment_id=controller['id'])))
    recovery = [dict(workflow='review-assignment', command='review-assignment',
        payload=successor,
        text='Resolve the named scope with new evidence or an eligible route/context recovery before creating a successor.')]
    ledger = dict(attempts=sum(row.get('attempts', 0) for key, row in records.get('review_task_execution', {}).items()
                              if key in records.get('review_task', {}) and records['review_task'][key]['epoch_id'] == epoch['id']),
                  subdivisions=sum(row['epoch_id'] == epoch['id'] for row in records.get('review_task_repair', {}).values()),
                  unknown_usage=True)
    import time
    ledger['elapsed_seconds'] = max(0, time.time() - controller.get('staged_started_at', time.time()))
    ledger['retries'] = sum(max(0, row.get('attempts', 0) - 1) for key,row in records.get('review_task_execution', {}).items()
                            if key in records.get('review_task', {}) and records['review_task'][key]['epoch_id'] == epoch['id'])
    ledger['epochs'] = sum(row['slot_id'] == epoch['slot_id'] and row['evidence_currency'] == epoch['evidence_currency']
                           for row in records.get('review_epoch', {}).values())
    outcome = staged.record_incomplete(epoch, completion, origin='execution',
        origin_binding=dict(execution_receipts=receipt_ids, reason=reason),
        unresolved_scope=[dict(id='unresolved:' + task['id'], scope=scope,
            cause='Required work exhausted its bounded execution or native delivery policy.',
            affected_decisions=list(epoch['completion_requirements'].get('permissible_decisions', {})),
            exhausted_tasks=[task['id']], scope_kind='unresolved', next_action=action)],
        recovery_work=recovery, resource_ledger=ledger)
    head = records['review_slot_head'][epoch['slot_id']]
    changes = receipt_changes + [immutable_record(records, 'assessment_incomplete', outcome['id'], outcome),
               ('review_slot_head', epoch['slot_id'], staged.transition_outcome(head, 'incomplete', outcome=outcome))]
    return changes, dict(controller_assignment_id=epoch['controller_assignment_id'], outcome=outcome, next=recovery)


def plan_frontier(store, payload, *, expected_revision, request_id):
    """Plan and assign the next ready frontier through the operator's controller handle."""
    from .operations import prepared_task_mutation
    from .evaluation import Evaluation
    from . import review_staged as staged
    fields(payload, ('controller_assignment_id',))
    def prepare(records, value):
        artifacts = Evaluation.of(records, store.build_artifact_store())
        controller = protocol._get(records, 'review_assignment', value['controller_assignment_id'])
        binding = controller.get('staged_review', {})
        if binding.get('kind') != 'controller':
            raise ResearchError('invalid_review_context', 'Plan through the frozen staged controller')
        epoch = protocol._get(records, 'review_epoch', binding['epoch_id'])
        head = protocol._get(records, 'review_slot_head', epoch['slot_id'])
        if head['epoch_id'] != epoch['id'] or not matches_currency(records, artifacts, epoch):
            raise ResearchError('review_epoch_stale', 'Plan the current scientific epoch')
        outstanding = next((row for row in records.get('review_reservation', {}).values()
                            if row['epoch_id'] == epoch['id'] and not row['released']), None)
        if outstanding is not None:
            return [], dict(controller_assignment_id=controller['id'], next=[dict(kind='command',
                command='review-run', payload=dict(assignment_id=controller['id'], epoch_id=epoch['id'],
                    task_id=outstanding['task_id'], action='reconcile'))])
        if head['state'] == 'incomplete':
            outcome = head['outcome']
            return [], dict(controller_assignment_id=controller['id'], outcome=outcome, next=outcome['recovery_work'])
        observations, terminal, obligations = collect_observations(records, artifacts, epoch)
        if obligations:
            raise ResearchError(obligations[0]['code'], obligations[0]['explanation'])
        if terminal is not None:
            from .review_composition import effective_review_output
            state = dict(obligations=[], context_status=controller['context_state']['context_status'])
            selection, _, _ = build_selection(records, artifacts, epoch, controller['id'], binding,
                                                observations, terminal, state)
            effective = effective_review_output(controller['id'], selection)
            if effective['kind'] in ('ordinary', 'paged'):
                scientific = effective['effective_output']
                command = 'value-review' if epoch['role'] == 'result' else 'review-adjudication'
                recorded = records.get('value_review' if epoch['role'] == 'result' else 'review_adjudication', {})
                if any(row['assignment_id'] == controller['id'] for row in recorded.values()):
                    return [], dict(controller_assignment_id=controller['id'], next=[], complete=True)
                record = dict(scientific, id='review:' + controller['id'], assignment_id=controller['id'])
                if epoch['role'] == 'result':
                    record['dossier_id'] = controller['dossier_id']
                return [], dict(controller_assignment_id=controller['id'], next=[dict(kind='command', command=command, payload=record)])
        accepted = {row['task_id'] for row in observations}
        active = list_epoch_tasks(records, epoch)
        contexts = derive_current_contexts(records, artifacts, epoch)
        pending = [task for task in active if task['id'] not in accepted and match_task_contexts(task, contexts)]
        route = protocol._get(records, 'review_route', controller['route_id'])
        composition_plan = next((plan for plan in records.get('review_composition_plan', {}).values()
                                 if plan['epoch_id'] == epoch['id']), None)
        changes, candidates = [], []
        total = sum(row.get('attempts', 0) for key, row in records.get('review_task_execution', {}).items()
                    if key in records.get('review_task', {}) and records['review_task'][key]['epoch_id'] == epoch['id'])
        import time
        if (total >= epoch['budgets']['max_calls'] or
                time.time() - controller.get('staged_started_at', time.time()) >= epoch['budgets']['max_elapsed_seconds']):
            unfinished = pending[0] if pending else active[-1]
            return prepare_execution_incomplete(records, artifacts, epoch, unfinished, 'exhausted', [unfinished['id']])
        for task in pending:
            execution = records.get('review_task_execution', {}).get(task['id'], {})
            reservation_id = execution.get('reservation_id')
            reservation = records.get('review_reservation', {}).get(reservation_id, {}) if reservation_id else {}
            if reservation and not reservation['released']:
                return [], dict(controller_assignment_id=controller['id'], next=[dict(kind='command', command='review-run',
                    payload=dict(assignment_id=controller['id'], epoch_id=epoch['id'], task_id=task['id'], action='reconcile'))])
            if execution.get('attempts', 0) >= epoch['budgets']['max_retries'] + 1 or total >= epoch['budgets']['max_calls']:
                paged = prepare_paged_recovery(records, artifacts, controller, epoch, task, observations, active)
                if paged is not None:
                    page_changes, composition_plan, page_tasks = paged
                    changes.extend(page_changes)
                    candidates.extend(row for row in page_tasks if all(key in accepted for key in row['dependencies']))
                    break
                children = split_task(epoch, task, observations=observations,
                    cards=staged.collect_planning_records(epoch, {'observations':observations}, tasks=active)['cards'])
                repairs = records.get('review_task_repair', {})
                if not children or total >= epoch['budgets']['max_calls'] or sum(row['epoch_id'] == epoch['id'] for row in repairs.values()) >= epoch['budgets']['max_subdivisions']:
                    return prepare_execution_incomplete(records, artifacts, epoch, task,
                        'unknown_outcome' if execution.get('state') == 'unknown' else 'exhausted',
                        [reservation.get('id', task['id'])])
                repair = dict(task_id=task['id'], epoch_id=epoch['id'], child_ids=[row['id'] for row in children])
                changes.append(immutable_record(records, 'review_task_repair', task['id'], repair))
                for child_task in children:
                    partition_changes, child_plans, blocked = prepare_partitions(records, artifacts, controller, epoch, child_task)
                    if blocked is not None:
                        return prepare_execution_incomplete(records, artifacts, epoch, blocked['task'], 'exhausted', [reservation['id']], failure=blocked)
                    changes.extend(partition_changes)
                    changes.extend(immutable_record(records, 'review_task', row['id'], row) for row in child_plans)
                    candidates.extend(row['task'] for row in child_plans)
            elif all(key in accepted for key in task['dependencies']):
                candidates.append(task)
        if not candidates:
            from .review_staged_followups import plan_retrieval_tasks
            retrievals = plan_retrieval_tasks(epoch, observations, active, contexts=contexts)
            if retrievals['exhausted']:
                requester = next((task for task in active if task['id'] in accepted), active[0])
                return prepare_execution_incomplete(records, artifacts, epoch, requester, 'exhausted', [requester['id']],
                                                    failure=dict(retrievals=retrievals['unresolved']))
            candidates = [task for task in retrievals['tasks'] if task['id'] not in accepted
                          and all(key in accepted for key in task['dependencies'])]
        if not candidates and composition_plan is not None:
            page_tasks = staged.plan_tasks(epoch, route_limit=read_input_limit(route),
                output_tokens=route['configuration']['max_output_tokens'], profiles={},
                tasks=active, selection=dict(observations=observations), composition_plan=composition_plan)['tasks']
            candidates = [task for task in page_tasks if not any(compare_task_scope(task, prior) and prior['id'] in accepted
                for prior in active) and all(key in accepted for key in task['dependencies'])]
        if not candidates:
            profiles = build_profiles(records, artifacts, route, epoch)
            planned = plan_remaining_tasks(records, artifacts, controller, profiles=profiles)
            candidates = [task for task in planned['tasks'] if match_task_contexts(task, contexts) and not any(
                compare_task_scope(task, previous) and previous['id'] in accepted for previous in active)
                and all(key in accepted for key in task['dependencies'])]
            # Only advance after every occurrence has a complete logical card.
            if not candidates:
                verified = staged.collect_planning_records(epoch, {'observations':observations}, tasks=active)
                task_index = {row['id']:row for row in active}
                current_observations = [row for row in observations if row['task_id'] in task_index
                                        and match_task_contexts(task_index[row['task_id']], verified['contexts'])]
                integrations = verified['integrations']
                roots = {row['id'] for row in integrations} - {key for row in integrations for key in row['child_ids']}
                if len(roots) > 1:
                    selected_roots = sorted(roots)
                    producers = [row['task_id'] for observed in current_observations for row in observed['output'].get('integrations', [])
                                 if row['id'] in selected_roots]
                    candidates = [staged.make_task(epoch, 'integrate', dependencies=sorted(set(producers)),
                        child_ids=selected_roots, scope='segmented_original')]
                elif not roots:
                    cards = verified['cards']
                    obligations = staged.card_obligations(cards)
                    dependencies = sorted({observed['task_id'] for observed in current_observations
                        if any(card in observed['output'].get('cards', []) for card in cards)
                        or any(row['id'] in obligations for row in observed['output'].get('cells', []))
                        or any(set(row.get('card_ids', [])).intersection(card['id'] for card in cards)
                               for row in observed['output'].get('combinations', []))})
                    if not cards:
                        dependencies = sorted(row['task_id'] for row in current_observations)
                    candidates = [staged.make_task(epoch, 'integrate', dependencies=dependencies,
                        card_ids=[card['id'] for card in cards], obligation_ids=obligations,
                        inputs=[span for context in verified['contexts'].values() for span in context['spans']],
                        claim_ids=list(verified['contexts']), contexts=verified['contexts'])]
                else:
                    candidates = [staged.make_task(epoch, 'final', dependencies=sorted(
                        row['task_id'] for observed in current_observations for row in observed['output'].get('integrations', []) if row['id'] in roots) + sorted(observed['task_id'] for observed in current_observations
                        if any(row.get('status') == 'resolved' for row in observed['output'].get('retrievals', []))),
                        inputs=[span for context in verified['contexts'].values() for span in context['spans']],
                        claim_ids=list(verified['contexts']), contexts=verified['contexts'])]
        # Render against this preparation's immutable plan and source binding.
        for kind in ('review_composition_plan', 'review_task_repair'):
            added = {key:row for changed_kind,key,row in changes if changed_kind == kind}
            if added:
                records = dict(records, **{kind:dict(records.get(kind, {}), **added)})
        # Freeze one ready task, keeping future siblings unrendered until their exact dependencies exist.
        task = candidates[0]
        planned = records.get('review_task', {}).get(task['id'])
        if planned is None:
            planned = next((row for kind, key, row in changes if kind == 'review_task' and key == task['id']), None)
        if planned is None:
            partition_changes, plans, blocked = prepare_partitions(records, artifacts, controller, epoch, task, composition_plan=composition_plan)
            if blocked is not None:
                return prepare_execution_incomplete(records, artifacts, epoch, blocked['task'], 'exhausted', [task['id']], failure=blocked)
            changes.extend(partition_changes)
            changes.extend(immutable_record(records, 'review_task', row['id'], row) for row in plans)
            planned, task = plans[0], plans[0]['task']
        child = next((row for row in records.get('review_assignment', {}).values()
                      if row.get('staged_review', {}).get('task_id') == task['id']), None)
        if child is None:
            current = dict(records, review_task=dict(records.get('review_task', {}), **{planned['id']:planned}))
            for kind in ('review_composition_plan', 'review_task_repair'):
                current[kind] = dict(records.get(kind, {}))
                current[kind].update({key:row for changed_kind, key, row in changes if changed_kind == kind})
            child_value = {key:controller[key] for key in ('role', 'reviewer_id', 'author_id', 'route_id', 'dossier_id')}
            child_value.update(id='task-assignment:' + digest([epoch['id'], task['id']]),
                context=dict(controller_assignment_id=controller['id'], task_id=task['id']))
            if composition_plan is not None:
                child_value['context']['composition_plan'] = composition_plan
            child_changes, child = prepare_child(current, artifacts, child_value, store.revision + 1)
            changes.extend(child_changes)
        return changes, dict(controller_assignment_id=controller['id'], task_ids=[task['id']], next=[dict(kind='command',
            command='review-run', payload=dict(assignment_id=controller['id'], epoch_id=epoch['id'], task_id=task['id']))])
    return prepared_task_mutation(store, 'review.frontier', payload, prepare,
                                  expected_revision=expected_revision, request_id=request_id)



def read_input_limit(route):
    """Use the native visual route's existing text limit without changing its adapter."""
    if route['adapter'] == 'codex_cli_visual_v1':
        from .review_codex_visual_transport import MAX_USER_TEXT
        return MAX_USER_TEXT
    return transport.local_input_limit(route)


def remake_task(epoch, task, **changes):
    """Rebind an unrendered envelope through its single core constructor."""
    from . import review_staged as staged
    keys = ('inputs', 'claim_ids', 'occurrence_ids', 'dependencies', 'binary_sources', 'card_ids', 'card_segments',
            'group_ids', 'scope', 'segment_task_ids', 'measurement', 'required', 'obligation_ids', 'child_ids',
            'answer_ranges', 'objection_entries', 'reconciliation_id', 'composition_role', 'composition_plan_root')
    options = {key:copy.deepcopy(task[key]) for key in keys if key in task}
    options['contexts'] = {key:dict(context, root=task['context_roots'].get(key, context['root']))
                           for key,context in epoch['manifest']['contexts'].items()}
    options.update(changes)
    return staged.make_task(epoch, task['type'], **options)


def compare_task_scope(left, right):
    """Credit accepted work by exact scientific scope despite later profile refinements."""
    return all(left.get(key) == right.get(key) for key in ('type', 'inputs', 'claim_ids', 'context_roots',
        'occurrence_ids', 'binary_sources', 'card_ids', 'card_segments', 'group_ids', 'scope',
        'obligation_ids', 'child_ids', 'answer_ranges', 'objection_entries', 'composition_plan_root'))



def prepare_partitions(records, artifacts, controller, epoch, task, *, composition_plan=None):
    """Admit rendered children before declaring a splittable scope exhausted."""
    pending, changes, plans = [task], [], []
    existing = sum(row['epoch_id'] == epoch['id'] for row in records.get('review_task_repair', {}).values())
    observations, cards = [], []
    if task['type'] == 'integrate' and not task['occurrence_ids']:
        observations, _, obligations = collect_observations(records, artifacts, epoch)
        if obligations:
            raise ResearchError(obligations[0]['code'], obligations[0]['explanation'])
        from . import review_staged as staged
        cards = staged.collect_planning_records(epoch, {'observations':observations},
                                                tasks=list_epoch_tasks(records, epoch))['cards']
    while pending:
        assigned = pending.pop(0)
        try:
            plans.append(prepare_task_plan(records, artifacts, controller, epoch, assigned, composition_plan))
        except ResearchError as error:
            if error.code not in ('review_task_pending', 'review_staged_binary_unsupported'):
                raise
            children = split_task(epoch, assigned, cards=cards, observations=observations) if error.code == 'review_task_pending' else []
            if not children or existing >= epoch['budgets']['max_subdivisions']:
                return changes, plans, dict(task=assigned, code=error.code, message=str(error), details=error.details)
            existing += 1
            repair = dict(task_id=assigned['id'], epoch_id=epoch['id'], child_ids=[row['id'] for row in children],
                          task=assigned, cause=error.code)
            changes.append(immutable_record(records, 'review_task_repair', assigned['id'], repair))
            pending[0:0] = children
    return changes, plans, None



def collect_refused_findings(records, artifacts, epoch):
    """Carry recognizable adverse text with its exact refused capture, without credit."""
    ancestry = {row['id']:row for row in _collect_successor_ancestry(records, epoch)}
    findings, seen = [], set()
    for refusal in records.get('review_task_refusal', {}).values():
        reservation_id = refusal.get('reservation_id')
        capture = records.get('review_observation', {}).get(reservation_id) if reservation_id else None
        if capture is None or capture.get('task_id') not in records.get('review_task', {}):
            continue
        epoch_id = records['review_task'][capture['task_id']]['epoch_id']
        if epoch_id not in ancestry:
            continue
        reference = capture['observation'].get('output')
        raw = protocol._read_json(artifacts, reference) if reference is not None else None
        if not isinstance(raw, dict):
            continue
        for family in ('findings', 'objections', 'conditions', 'unresolved'):
            rows = raw.get(family, [])
            if not isinstance(rows, list):
                continue
            for row in rows:
                recognizable = ((isinstance(row, str) and bool(row.strip())) or
                    isinstance(row, dict) and (family != 'findings' or row.get('adverse')))
                if recognizable:
                    identity = (reservation_id, family, digest(row))
                    if identity in seen:
                        continue
                    seen.add(identity)
                    findings.append(dict(epoch_id=epoch_id, epoch_digest=ancestry[epoch_id]['digest'],
                        task_id=capture['task_id'], reservation_id=reservation_id, observation_digest=digest(capture),
                        refusal_id=refusal.get('id'), raw_output_digest=digest(raw),
                        family=family, record=copy.deepcopy(row), accepted=False))
    return findings



def read_composition_sources(records, artifacts, plan):
    """Read the exact instrumented outputs selected for automatic page recovery."""
    plan_id = digest(plan)
    ids = [key for row in records.get('review_task_repair', {}).values() if row.get('composition_plan_id') == plan_id
           for key in row['composition_observation_ids']]
    return [dict(id=key, output=protocol._read_json(artifacts, records['review_observation'][key]['observation']['output']))
            for key in ids]


def prepare_paged_recovery(records, artifacts, controller, epoch, task, observations, tasks):
    """Freeze smaller scientific answer ranges from a refused complete observed object."""
    if task['type'] != 'final' or task.get('composition_plan_root'):
        return None
    from . import review_staged as staged
    from .review_staged_followups import build_composition_plan
    captures = [row for row in records.get('review_observation', {}).values() if row['task_id'] == task['id']
                and row['observation'].get('output') is not None]
    captures.sort(key=lambda row:row['recorded_revision'], reverse=True)
    if not captures:
        return None
    captured = captures[0]
    raw = protocol._read_json(artifacts, captured['observation']['output'])
    if not isinstance(raw, dict) or not ('stage' in raw or 'disposition' in raw):
        return None
    route = records['review_route'][epoch['route_id']]
    bindings = build_composition_bindings(epoch, observations, tasks=tasks)
    source = dict(id=captured['id'], output=raw)
    # An observed sample calibrates characters separately from output tokens.
    tokens = (captured['observation'].get('usage') or {}).get('output_tokens')
    allowance = max(2, read_input_limit(route) // 8)
    if type(tokens) in (int, float) and tokens > 0:
        allowance = min(allowance, max(2, int(len(_canonical(raw)) * route['configuration']['max_output_tokens'] / tokens / 4)))
    try:
        plan = build_composition_plan(epoch, bindings, observations + [source], page_characters=allowance)
    except ResearchError as error:
        if error.code not in ('review_composition_pending', 'review_composition_conflict'):
            raise
        return None
    page_tasks = staged.plan_tasks(epoch, route_limit=read_input_limit(route),
        output_tokens=route['configuration']['max_output_tokens'], profiles={}, tasks=tasks,
        selection=dict(observations=observations), composition_plan=plan)['tasks']
    plan_id = digest(plan)
    repair = dict(task_id=task['id'], epoch_id=epoch['id'], child_ids=[row['id'] for row in page_tasks],
                  composition_plan_id=plan_id, composition_observation_ids=[captured['id']])
    return [immutable_record(records, 'review_composition_plan', plan_id, plan),
            immutable_record(records, 'review_task_repair', task['id'], repair)], plan, page_tasks



def match_task_contexts(task, contexts):
    """Keep undispatched old-context work out of the current ready frontier."""
    return all(key in contexts and contexts[key]['root'] == root for key,root in task['context_roots'].items())
