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


def collect_valid_records(epoch, observations, name, validator):
    """Preserve independently valid dependency records beside malformed siblings."""
    records = []
    for row in collect_output_records(observations, (name,)):
        try:
            records.append(validator(row, epoch['manifest']))
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
    tasks = [row['task'] for row in records.get('review_task', {}).values() if row['epoch_id'] == epoch['id']]
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
    return staged.plan_tasks(epoch, route_limit=transport.local_input_limit(route),
        output_tokens=route['configuration']['max_output_tokens'], profiles=profiles,
        cards=cards, findings=findings, selected_groups=verified['selected_groups'], contexts=verified['contexts'],
        tasks=tasks, selection=selection, composition_plan=composition_plan)


def build_request(artifacts, route, prompt, packet):
    """Admit the exact fresh request, independently of legacy fit-order replay."""
    request = transport.request_body(route, prompt['text'], packet, [])
    request['history'] = []
    sent = review_excerpts.read_sent_packet(request) or {}
    if (not review_problem_parts.preserves_ordinary_packet(artifacts, packet, request)
            or sent.get('staged_review') != packet.get('staged_review')
            or review_problem_parts._measure(route, request) > transport.local_input_limit(route)):
        raise ResearchError('review_task_pending', 'Partition the task: fitting removed mandatory staged input')
    return request


def project_card(epoch, card, findings):
    """Use the core's versioned compact projection, including observed finding bodies."""
    from . import review_staged as staged
    return staged.compact_card(card, manifest=epoch['manifest'], findings=findings)


def project_terminal_material(epoch, observations, *, integration_id=None, tasks=()):
    """Deliver assessed grounds and answers without replaying full cards or pages."""
    from . import review_staged as staged
    outputs = [row['output'] for row in observations]
    cards = [row for row in collect_valid_records(epoch, observations, 'cards', staged.validate_card) if row['kind'] == 'full']
    integrations = collect_output_records(observations, ('integrations',))
    if tasks:
        verified = staged.collect_planning_records(epoch, {'observations': observations}, tasks=tasks)
        integrations = verified['integrations']
        cards = verified['cards']
    cells = {row['id']: row for card in cards for row in card['judgments']}
    cells.update({row['id']: row for output in outputs for row in output.get('cells', [])})
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
    findings = {row['id']: row for output in outputs for row in output.get('findings', [])}
    answers = {row['id']: row for output in outputs for row in output.get('answers', [])}
    material = dict(claims=claims, decisions=decisions, cells=list(cells.values()), conclusions=list(conclusions.values()),
                integrations=integrations, integration_id=integrated['id'] if integrated is not None else None,
                conditions=[row['id'] for card in cards for row in card['conditions']],
                findings=list(findings.values()), answers=list(answers.values()))
    if integrated is not None:
        bound = staged.bind_terminal_conditions(epoch, {}, cards=cards, integration=integrated)
        material.update(record_root=bound['record_root'], visible_conditions=bound['visible_conditions'])
    return material


def build_packet(epoch, task, *, observations=(), repair=None, composition_plan=None, tasks=(), terminal_observations=None):
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
    claims = [dict(claim, context_root=epoch['manifest']['contexts'][claim['id']]['root'])
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
    for index, finding in enumerate(epoch['manifest']['inherited_findings']):
        original_fields = [row for row in epoch['manifest']['frame_fields']
                           if row['path'][:2] == ['material_objections', index]]
        if (finding['id'] in task['obligation_ids']
                or any(row['source_id'] in assigned_sources for row in original_fields)):
            inherited.append(dict(id=finding['id'], record_digest=digest(finding),
                                  source_ids=[row['source_id'] for row in original_fields]))
    if len(_canonical([continuity, inherited])) > 16000:
        raise ResearchError('review_task_pending', 'Partition the dependency frontier to bound task continuity')
    frame_fields = [dict(source_id=original['span']['source_id'], span=original['span'],
                         path=sources[original['span']['source_id']]['identity'].get('path'), text=original['text'])
                    for original in originals if sources[original['span']['source_id']]['kind'] == 'frame']
    body = dict(protocol=staged.PROTOCOL, epoch_id=epoch['id'], task=projected,
                claims=claims, decisions=epoch['completion_requirements'].get('permissible_decisions', {}),
                units=units, originals=originals, continuity=continuity,
                index=[dict(occurrence_id=row['id'], key=row['key'], index=row['index']) for row in
                       epoch['manifest']['occurrences'] if row['id'] in task['occurrence_ids']],
                index_root=epoch['manifest']['digest'], inherited=inherited,
                frame=dict(fields=frame_fields), output_characters=task['measurement']['configured_output_tokens'])
    project_task_material(epoch, task, body, observations, terminal_observations, tasks,
                          composition_plan['role'] if composition_plan is not None else None)
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
    if task['binary_sources']:
        raise ResearchError('review_task_pending', 'Native binary input needs its existing qualified delivery contract')
    if repair is not None:
        packet['context_repair'] = copy.deepcopy(repair)
    packet['digest'] = digest(packet)
    return packet


def project_task_material(epoch, task, body, observations, terminal_observations, tasks, composition_role):
    """Project only the selected task's scientific dependency material."""
    from . import review_staged as staged
    # Records arrive as explicit task input, never as replayed conversation.
    records = [row['output'] for row in observations]
    cards = collect_valid_records(epoch, observations, 'cards', staged.validate_card)
    findings = collect_output_records(observations, ('findings',))
    receipts = collect_output_records(observations, ('integrations', 'original_checks', 'combinations'))
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
    conclusions.extend(row for row in collect_valid_records(epoch, observations, 'cells', staged.validate_cell)
                       if row['id'] not in conclusion_ids)
    child_union = sorted({key for receipt in receipts if receipt['id'] in task['child_ids']
                          for key in receipt.get('obligation_ids', [])})
    obligation_ids = sorted(set(task['obligation_ids'] or ([] if task['child_ids'] else staged.card_obligations(cards)))
                            | set(child_union))
    copied = dict(epoch_id=epoch['id'], task_id=task['id'], selected_observations=[row['id'] for row in observations],
                  input_roots=[epoch['manifest']['digest']], context_roots=task['context_roots'],
                  child_ids=task['child_ids'], child_union=child_union, obligation_ids=obligation_ids)
    if task['type'] == 'integrate' and not task['occurrence_ids']:
        copied = staged.bind_integration_lists(dict(child_ids=task['child_ids']), epoch=epoch, task=task,
            cards=cards, children=[row for output in records for row in output.get('integrations', [])], observations=observations)
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
                                  integration_id=body['final']['integration_id'], tasks=tasks))
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
    for entry in packet.get('evidence', []):
        reference = entry['artifact']
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


def build_epoch(records, record, packet, manifest):
    """Freeze the slot identity, predecessor and scientific decision boundary."""
    from . import review_staged as staged
    from .review_composition import PROTOCOL as COMPOSITION_PROTOCOL, list_scalar_fields
    predecessor_id = (record.get('context', {}).get('prior_assignment_id')
                      or record.get('context', {}).get('context_repair', {}).get('assignment_id')
                      or record.get('context', {}).get('staged_upgrade', {}).get('assignment_id'))
    predecessor = None
    if predecessor_id:
        attempts = [row for row in records.get('review_attempt', {}).values()
                    if row['assignment_id'] == predecessor_id]
        pending = [row for row in records.get('review_invocation', {}).values()
                   if row['assignment_id'] == predecessor_id and row['id'] not in records.get('review_attempt', {})]
        if pending:
            raise ResearchError('review_worker_reconciliation_required', 'Reconcile the predecessor before staging')
        last = max(attempts, key=lambda row: row['recorded_revision'], default=None)
        predecessor = dict(assignment_id=predecessor_id, assignment_digest=digest(records['review_assignment'][predecessor_id]),
                           attempt_id=last['id'] if last else None, attempt_digest=digest(last) if last else None,
                           no_output=not last or not last.get('output'))
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
    target = min(16000, route['configuration']['max_output_tokens'])
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
        task = staged.make_task(epoch, 'frame', inputs=spans)
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
    manifest = build_manifest(artifacts, packet, record['role'])
    epoch = build_epoch(records, record, packet, manifest)
    tasks = prepare_frame_tasks(records, artifacts, record, epoch, route)
    binding = dict(protocol=staged.PROTOCOL, kind='controller', epoch_id=epoch['id'],
                   task_ids=[row['id'] for row in tasks])
    record.update(staged_review=binding, history=[], history_assignment_id=None)
    slot = dict(slot_id=epoch['slot_id'], epoch_id=epoch['id'], state='pending', outcome=None, role=epoch['role'])
    changes = [immutable_record(records, 'review_epoch', epoch['id'], epoch)]
    changes.extend(immutable_record(records, 'review_task', row['id'], row) for row in tasks)
    changes.append(('review_slot_head', slot['slot_id'], slot))
    return changes


def prepare_task_plan(records, artifacts, controller, epoch, task, composition_plan=None):
    """Freeze a sealed core envelope and its selected dependency frontier."""
    from . import review_staged as staged
    staged._verify_seal(task)
    fields(task, ('id', 'schema', 'protocol', 'epoch_id', 'epoch_digest', 'type', 'scope'), None, code='invalid_review_staged')
    if task['schema'] != staged.TASK_SCHEMA or task['protocol'] != staged.PROTOCOL or task['type'] not in staged.TASK_TYPES or task['scope'] not in staged.SCOPES:
        raise ResearchError('invalid_review_staged', 'Use the frozen core task schema, type and scope')
    if task['epoch_id'] != epoch['id'] or task['epoch_digest'] != epoch['digest']:
        raise ResearchError('review_task_dependency_changed', 'The task must bind this frozen epoch')
    for claim_id in task['claim_ids']:
        context = epoch['manifest']['contexts'].get(claim_id)
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
    route = protocol._get(records, 'review_route', controller['route_id'])
    repair = protocol._read_json(artifacts, controller['packet']).get('context_repair')
    claim_range = set(task['claim_ids']) != {row['id'] for row in epoch['manifest']['claims']}
    prompt = protocol.canonical_prompt(epoch['role'], orchestration='problem_creation', staged_task=task,
                              repair=repair is not None, claim_range=claim_range,
                              composed=composition_plan is not None and task['type'] == 'final')
    config = route['configuration']
    measurement = task['measurement'] or dict(fits=True, input_fits=True, output_fits=True, duration_fits=True,
        mandatory_preserved=True, input_characters=0, output_tokens=config['max_output_tokens'],
        duration_seconds=config['timeout_seconds'], route_limit=transport.local_input_limit(route),
        configured_output_tokens=config['max_output_tokens'], output_margin_tokens=0,
        duration_limit_seconds=config['timeout_seconds'])
    measurement = dict(measurement, route_limit=transport.local_input_limit(route),
                       configured_output_tokens=config['max_output_tokens'], duration_limit_seconds=config['timeout_seconds'])
    task = staged._seal(dict({key: value for key, value in task.items() if key != 'digest'}, measurement=measurement))
    packet = build_packet(epoch, task, observations=dependencies, repair=repair, composition_plan=composition_plan,
                                  tasks=[row['task'] for row in records.get('review_task', {}).values() if row['epoch_id'] == epoch['id']],
                                  terminal_observations=terminal_observations)
    request = build_request(artifacts, route, prompt, packet)
    measurement = dict(measurement, input_characters=review_problem_parts._measure(route, request))
    task = staged.freeze_task(task, digest(request), staged.SERIALIZATION, measurement=measurement)
    return dict(id=task['id'], epoch_id=epoch['id'], generation=1, task=task, packet=protocol._put_json(artifacts, packet),
                request=protocol._put_json(artifacts, request), prompt=prompt, controller_assignment_id=controller['id'])


def prepare_upgrade(records, value, route, problem):
    """Retain the exact predecessor boundary before entering a staged epoch."""
    upgrade = value['context']['staged_upgrade']
    fields(value['context'], ('staged_upgrade',), code='invalid_review_context')
    fields(upgrade, ('assignment_id',), code='invalid_review_context')
    upgrade_parent = protocol._get(records, 'review_assignment', upgrade['assignment_id'])
    if not problem or value['role'] not in ('result', 'adjudicator') or route['adapter'] not in ('claude_cli_v1', 'codex_cli_v1', 'codex_cli_framed_v1'):
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
    sent = review_excerpts.read_sent_packet(protocol._read_json(artifacts, observation['request']))
    actual = read_actual_input(epoch, task, sent)
    bound, unmatched = staged.bind_quotations(raw, epoch=epoch, sources=epoch['manifest']['sources'])
    output = staged.validate_task_output(task, bound)
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
    protocol._require_route_runtime(route)
    request = protocol.build_assignment_request(records, store.build_artifact_store(), assignment)
    epoch = protocol._get(records, 'review_epoch', binding['epoch_id'])
    head = protocol._get(records, 'review_slot_head', epoch['slot_id'])
    if not matches_currency(records, store.build_artifact_store(), epoch):
        raise ResearchError('review_epoch_stale', 'The frozen scientific evidence changed before dispatch')
    if head['epoch_id'] != epoch['id']:
        raise ResearchError('review_epoch_stale', 'The current slot has a successor epoch')
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
                 and (previous_capture is None or previous_capture['observation']['status'] != 'completed'))
    if reservation is None or retryable:
        reserved = execution.reserve_task(store, plan, request_id=request_id + ':reserve')
        reservation = reserved['result']
    captured, process, current = wait_for_capture(store, route, reservation, request_id)
    if captured['observation']['status'] != 'completed':
        finish_worker(store, current['id'], process, request_id)
        def prepare_failure(current_records, value):
            attempt = dict(captured['observation'], **value, origin='instrumented',
                           reservation_id=captured['reservation_id'], invocation_id=captured['reservation_id'],
                           packet_digest=assignment['packet_digest'], prompt_hash=assignment['prompt_hash'],
                           recorded_revision=captured['recorded_revision'])
            return [immutable_record(current_records, 'review_attempt', value['id'], attempt)], attempt
        return prepared_task_mutation(store, 'review.invoke', payload, prepare_failure, request_id=request_id)
    try:
        receipt = execution.accept_observation(store, {'reservation_id': current['id']},
             lambda current_records, capture: accept_capture(current_records, store.build_artifact_store(),
                                                                    assignment, capture, payload['id']),
             request_id=request_id + ':accept')['result']
        if receipt['receipt']['attempt']['id'] != payload['id']:
            raise ResearchError('review_resampling_forbidden', 'Resume the originally named task attempt')
    finally:
        finish_worker(store, current['id'], process, request_id)
    def prepare(current_records, value):
        return prepare_attempt(current_records, store.build_artifact_store(), assignment, receipt['receipt'])
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
    tasks = [row['task'] for row in records.get('review_task', {}).values() if row['epoch_id'] == epoch['id']]
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
    observation = captured['observation'] if captured is not None else attempt
    result = dict(attempt_id=attempt_id, status=observation['status'],
                  instrumented=attempt.get('origin') == 'instrumented',
                  provenance={key: copy.deepcopy(attempt.get(key)) for key in
                              ('origin', 'runtime', 'route_digest', 'invocation_id', 'recorded_revision')})
    for name in ('request', 'response', 'output'):
        reference = observation.get(name)
        result[name] = copy.deepcopy(reference)
        value = protocol._read_json(artifacts, reference) if reference is not None else None
        result[name + '_digest'] = digest(value) if reference is not None else None
        if name == 'output':
            result['raw_output'] = value
            result['raw_output_digest'] = result['output_digest']
    if captured is not None:
        result['provenance'].update(reservation_id=captured['reservation_id'], worker=captured['worker'],
                                    capture_digest=digest(captured), capture_revision=captured['recorded_revision'])
    return result


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
    required_tasks = [row['task'] for row in records.get('review_task', {}).values()
                      if row.get('epoch_id') == epoch['id']]
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
    if terminal is not None:
        attempt, observed = terminal
        incomplete = (paged.get('incomplete_output') if paged is not None else None)
        if incomplete is None:
            incomplete = observed['output'].get('assessment_incomplete')
        if incomplete is not None:
            outcome = build_incomplete_outcome(records, artifacts, epoch, completion, attempt, incomplete)
            outcomes.append(outcome)
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
        state['effective']['terminal_raw_output_digest'] = read_raw_observation(records, artifacts,
            selection['terminal_attempt_id'])['raw_output_digest']
        state['effective']['raw_page_bindings'] = [dict(attempt_id=row['attempt_id'],
            raw_output_digest=read_raw_observation(records, artifacts, row['attempt_id'])['raw_output_digest'])
            for row in paged['raw_page_bindings']]
    state['effective']['currency_state'] = ('current' if matches_currency(records, artifacts, epoch) else 'stale')
    if state['effective']['currency_state'] == 'stale':
        state['obligations'].append(obligation('review_epoch_stale', 'The frozen scientific evidence changed.'))
    state['completion'] = completion
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
