"""Pure, finite follow-up plans from selected staged-review observations.

The caller authenticates and selects observations before planning, then renders
and freezes every returned core task. Bindings are packet material, not task
envelope extensions. No answer or scientific completion is inferred here.
"""

import copy

from . import review_composition as composition, review_staged as staged
from .errors import ResearchError
from .evidence import digest
from .operations import fields, normalized_text, text
from .storage import _canonical


def _reject(message):
    raise ResearchError('invalid_review_staged', message)


def _list_tasks(values):
    values = values.values() if isinstance(values, dict) else values
    return [row.get('task', row) for row in values]


def _covers_scope(inputs, claim_ids, context_roots, observations):
    """Require the complete original range separately for every assigned claim."""
    for claim in claim_ids or [None]:
        actual = [span for row in observations
                  if claim is None or (claim in row['actual_input']['claim_ids']
                      and row['actual_input']['context_roots'].get(claim) == context_roots[claim])
                  for span in row['actual_input']['intervals']]
        for span in inputs:
            delivered = [[row['start'], row['end']] for row in actual if row['source_id'] == span['source_id']]
            if staged.interval_gaps([[span['start'], span['end']]], delivered):
                return False
    return True


def _collect_retrievals(epoch, observations, *, contexts=None):
    manifest = dict(epoch['manifest'], contexts=contexts or epoch['manifest']['contexts'])
    by_id = {row['id']: row for row in observations}
    by_task = {row['task_id']: row for row in observations}
    receipts = {}
    stable = ('id', 'question', 'inputs', 'claim_ids', 'context_roots', 'requested_by')
    for observed in observations:
        for receipt in staged._require_array(observed['output'].get('retrievals', []), 'retrievals'):
            fields(receipt, ('requested_by',), None, code='invalid_review_staged')
            requester = by_task.get(text(receipt['requested_by'], 'requested_by', code='invalid_review_staged'))
            if requester is None:
                _reject('A retrieval needs its selected original pending request.')
            staged.validate_retrieval(receipt, manifest,
                requested_context_roots=requester['actual_input']['context_roots'])
            if receipt['status'] != 'pending' and any(
                    observed['actual_input']['context_roots'].get(key) != manifest['contexts'][key]['root']
                    for key in receipt['claim_ids']):
                continue
            previous = receipts.get(receipt['id'])
            if previous is not None:
                if any(previous[name] != receipt[name] for name in stable):
                    _reject('Conflicting retrieval lifecycle scope.')
                if previous['status'] != 'pending' and receipt['status'] != 'pending' and previous != receipt:
                    _reject('Conflicting retrieval lifecycle answers.')
                if receipt['status'] == 'pending':
                    continue
            receipts[receipt['id']] = copy.deepcopy(receipt)
    for receipt in receipts.values():
        requester = by_task.get(receipt['requested_by'])
        if requester is None or not any(row['id'] == receipt['id'] and row['status'] == 'pending'
                                       for row in requester['output'].get('retrievals', [])):
            _reject('A retrieval needs its selected original pending request.')
        if receipt['status'] == 'resolved':
            reread = [by_id[key] for key in receipt['observation_ids'] if key in by_id]
            inputs, claims, roots = _collect_scope(epoch, [receipt], contexts=manifest['contexts'])
            if (len(reread) != len(receipt['observation_ids'])
                    or any(row['task_id'] == receipt['requested_by'] for row in reread)
                    or any(not staged.depends_on_task(row, receipt['requested_by'], by_task) for row in reread)
                    or not _covers_scope(inputs, claims, roots, reread)):
                _reject('A retrieval answer needs the exact selected original rereading.')
    return receipts


def _derive_obligation(epoch, receipt, contexts):
    sources = {row['id']: row for row in epoch['manifest']['sources']}
    versions = sorted({digest({key: sources[span['source_id']][key]
                               for key in ('kind', 'identity', 'descriptor', 'sha256')})
                       for span in receipt['inputs']})
    roots = {key:contexts[key]['root'] for key in receipt['claim_ids']}
    return 'retrieval:' + digest([normalized_text(receipt['question']), versions, roots])


def _collect_scope(epoch, receipts, *, contexts=None):
    current_contexts = contexts or epoch['manifest']['contexts']
    sources = {row['id']: row for row in epoch['manifest']['sources']}
    intervals = {}
    for receipt in receipts:
        for span in staged.collect_retrieval_inputs(receipt, current_contexts):
            intervals.setdefault(span['source_id'], []).append([span['start'], span['end']])
    inputs = [staged.source_span(sources[key], start, end) for key in sorted(intervals)
              for start, end in staged._merge_ranges(intervals[key])]
    claims = sorted({key for receipt in receipts for key in receipt['claim_ids']})
    contexts = {key: current_contexts[key]['root'] for key in claims}
    return inputs, claims, contexts


def _read_round_number(task, obligation):
    prefix = obligation + ':reread:'
    numbers = [int(key[len(prefix):]) for key in task['obligation_ids']
               if key.startswith(prefix) and key[len(prefix):].isdigit()]
    return max(numbers) if numbers else 1


def plan_retrieval_tasks(epoch, observations, tasks, *, contexts=None):
    """Plan exact rereads and their explicit resolution, sharing duplicate caps.

    Returns ``tasks`` plus per-task ``bindings`` containing kind, retrievals,
    observation_ids, obligation_id and reread. ``exhausted`` and ``unresolved``
    give the caller explicit scope for an execution-incomplete outcome. A read
    round tag survives task subdivision, so fragments consume one reread round.
    """
    current_contexts = contexts or epoch['manifest']['contexts']
    observations, tasks = list(observations), _list_tasks(tasks)
    task_index = {row['id']: row for row in tasks}
    by_task = {row['task_id']: row for row in observations}
    receipts = _collect_retrievals(epoch, observations, contexts=current_contexts)
    groups = {}
    for receipt in receipts.values():
        groups.setdefault(_derive_obligation(epoch, receipt, current_contexts), []).append(receipt)
    result = dict(tasks=[], bindings={}, exhausted=False, unresolved=[])

    def add(assigned, obligation, requests, reread, observed=()):
        assigned = task_index.get(assigned['id'], assigned)
        result['tasks'].append(copy.deepcopy(assigned))
        result['bindings'][assigned['id']] = dict(kind='resolve' if assigned['type'] == 'final' else 'read',
            retrievals=copy.deepcopy(requests), observation_ids=sorted(row['id'] for row in observed),
            obligation_id=obligation, reread=reread)

    for obligation in sorted(groups):
        group = sorted(groups[obligation], key=lambda row: row['id'])
        pending = [row for row in group if row['status'] == 'pending']
        unresolved = [row for row in group if row['status'] == 'unresolved']
        if not pending and not unresolved:
            continue
        inputs, claims, contexts = _collect_scope(epoch, pending or unresolved, contexts=current_contexts)
        group_tasks = [row for row in tasks if obligation in row['obligation_ids']]
        reads = [row for row in group_tasks if row['type'] in ('read', 'frame')]
        round_number = max((_read_round_number(row, obligation) for row in reads), default=0)
        request_ids = sorted({row['requested_by'] for row in pending})
        cause = 'reviewer_unresolved' if unresolved else None
        current = [row for row in reads if _read_round_number(row, obligation) == round_number
                   and set(request_ids) <= set(row['dependencies'])]
        active = [row for row in current if row['id'] not in by_task]
        observed = [by_task[row['id']] for row in current if row['id'] in by_task]
        if cause is None and active:
            for assigned in active:
                add(assigned, obligation, pending, round_number)
            continue
        if cause is None and observed and _covers_scope(inputs, claims, contexts, observed):
            resolve = staged.make_task(epoch, 'final', claim_ids=claims, scope='segmented_original', contexts=current_contexts,
                dependencies=sorted(set(request_ids) | {row['task_id'] for row in observed}),
                obligation_ids=[obligation, obligation + ':reread:' + str(round_number)])
            if resolve['id'] not in by_task:
                add(resolve, obligation, pending, round_number, observed)
                continue
        if cause is None and round_number < epoch['budgets']['max_rereads']:
            dependencies = sorted(set(request_ids) | {row['id'] for row in group_tasks if row['id'] in by_task})
            occurrences = [row['id'] for row in epoch['manifest']['occurrences']
                           if set(row['source_ids']) & {span['source_id'] for span in inputs}]
            full_context = all(staged._covers_inputs(current_contexts[key]['spans'], inputs)
                               for key in claims)
            reading = staged.make_task(epoch, 'read' if occurrences else 'frame', inputs=inputs, claim_ids=claims, contexts=current_contexts,
                occurrence_ids=occurrences, dependencies=dependencies,
                scope='joint_original' if full_context else 'original_segment',
                obligation_ids=[obligation, obligation + ':reread:' + str(round_number + 1)])
            add(reading, obligation, pending, round_number + 1)
            continue
        result['exhausted'] = True
        result['unresolved'].append(dict(obligation_id=obligation,
            retrieval_ids=[row['id'] for row in group], question=group[0]['question'],
            inputs=inputs, claim_ids=claims, context_roots=contexts,
            cause=cause or 'reread_limit', rereads=round_number))
    return result


def _raise_composition_error(message, *, pending=False, **details):
    raise ResearchError('review_composition_pending' if pending else 'review_composition_conflict',
                        message, details or None)


def _select_scientific_output(role, observations):
    candidates = {}
    for observed in observations:
        output = observed['output']
        if (role == 'result' and output.get('stage') == 'result'
                or role == 'adjudicator' and 'objection_id' in output and 'disposition' in output):
            scientific, _ = staged.project_ordinary_final(output)
            candidates[digest(scientific)] = scientific
    if not candidates:
        _raise_composition_error('Page ownership needs a complete observed scientific output.', pending=True)
    if len(candidates) != 1:
        _raise_composition_error('Reconcile conflicting scientific outputs before freezing page ownership.')
    return next(iter(candidates.values()))


def _read_path(output, path):
    value = output
    for key in path.split('/')[1:]:
        if isinstance(value, list):
            entries = [row for row in value if isinstance(row, dict) and row.get('kind') == key]
            if len(entries) != 1:
                raise KeyError(path)
            value = entries[0]
        else:
            value = value[key]
    return copy.deepcopy(value)


def _collect_composition_values(role, output):
    required, optional = composition.list_page_fields(role)
    values, choices = {}, {}
    for path in list(required) + list(optional):
        try:
            value = _read_path(output, path)
        except (KeyError, TypeError):
            if path in optional:
                continue
            _raise_composition_error('A scientific page field is missing.', pending=True, path=path)
        kind = dict(required, **optional)[path]
        if (kind == 'string' and not isinstance(value, str)
                or kind == 'object' and not isinstance(value, dict)
                or kind in ('strings', 'objects') and (not isinstance(value, list)
                    or any(not isinstance(row, str if kind == 'strings' else dict) for row in value))):
            _raise_composition_error('The scientific field does not match its page type.', path=path)
        values[path] = value
    for path in composition.list_scalar_fields(role):
        try:
            choices[path] = _read_path(output, path)
        except (KeyError, TypeError):
            _raise_composition_error('A scientific scalar is missing.', pending=True, path=path)
    # The current composition schema has fixed scalar and page fields. Refuse
    # an output that would lose any additional scientific field in assembly.
    represented = {}
    for path, value in dict(values, **choices).items():
        composition._put(represented, path, value)
    if role == 'result':
        represented['value']['work_items'] = []
        represented['value']['assurances'] = [dict(kind=key, **represented['value']['assurances'][key])
                                               for key in staged.ASSURANCES]
    if represented != output:
        _raise_composition_error('The scientific output has fields the composition schema cannot preserve.')
    return values, choices


def build_composition_plan(epoch, bindings, observations, *, page_characters):
    """Freeze lossless ownership of an already observed ordinary scientific output.

    Strings may span pages; object members remain atomic. The allowance covers
    serialized content, while the caller still measures complete rendered page
    requests and required output before dispatch. No missing output is invented.
    """
    if type(page_characters) is not int or page_characters < 2:
        _raise_composition_error('Scientific pages need a positive serialized content allowance.')
    observations = list(observations)
    output = _select_scientific_output(epoch['role'], observations)
    values, choices = _collect_composition_values(epoch['role'], output)
    permitted = copy.deepcopy(epoch['completion_requirements'].get('permissible_decisions', {}))
    integrations = [row for observed in observations for row in observed['output'].get('integrations', [])]
    children = {key for row in integrations for key in row.get('child_ids', [])}
    roots = {row['id']: row for row in integrations if row['id'] not in children}
    if len(roots) > 1:
        _raise_composition_error('Reconcile integration roots before freezing scientific pages.', pending=True)
    if roots:
        integrated = next(iter(roots.values()))
        permitted.update(integrated.get('decision_constraints', {}).get('permissible_decisions', {}))
    for path, value in choices.items():
        if path in permitted and digest(value) not in {digest(row) for row in permitted[path]}:
            _raise_composition_error('The observed choice is outside its integrated permissions.', path=path)
    if epoch['role'] == 'adjudicator' and output['objection_id'] != epoch['focused_scope']['objection_id']:
        _raise_composition_error('The scientific output names another focused objection.')
    plan = dict(protocol=composition.PROTOCOL, schema=composition.SCHEMA,
        epoch_id=epoch['id'], role=epoch['role'], slot_id=epoch['slot_id'], subject=copy.deepcopy(epoch['subject']),
        required_pages=[], ownership=[], revision_authorizations=[],
        terminal_choices={path: [copy.deepcopy(value)] for path, value in choices.items()})
    for key in ('evidence_currency', 'source_root', 'dependency_root', 'selected_observations_root',
                'integration_root', 'findings', 'evidence_obligations'):
        plan[key] = copy.deepcopy(bindings[key])
    used = page_characters

    def append(path, value_type, member_key, ordinal, span, content):
        nonlocal used
        size = len(_canonical(content, code='review_composition_conflict'))
        if size > page_characters:
            _raise_composition_error('An atomic page member needs a smaller scientific scope.', pending=True,
                               path=path, ordinal=ordinal)
        if used + size > page_characters:
            plan['required_pages'].append('scientific-page:' + str(len(plan['required_pages']) + 1))
            used = 0
        used += size
        plan['ownership'].append(dict(page_id=plan['required_pages'][-1], path=path, type=value_type,
                                     member_key=member_key, ordinal=ordinal, range=span))

    required, optional = composition.list_page_fields(epoch['role'])
    for path, value in values.items():
        kind = dict(required, **optional)[path]
        if isinstance(value, list):
            if not value:
                append(path, 'empty_list', None, None, None, [])
                continue
            members = [(str(row.get('id', path + ':' + str(index))) if isinstance(row, dict)
                        else path + ':' + str(index), index, row) for index, row in enumerate(value)]
        else:
            members = [(None, None, value)]
        for member_key, ordinal, member in members:
            if kind in ('objects', 'object'):
                append(path, 'object', member_key, ordinal, None, member)
                continue
            if not member:
                append(path, 'string', member_key, ordinal, [0, 0], '')
                continue
            start = 0
            while start < len(member):
                room = page_characters - used
                low, high = start, min(len(member), start + max(0, room - 2))
                while low < high:
                    middle = (low + high + 1) // 2
                    if len(_canonical(member[start:middle], code='review_composition_conflict')) <= room:
                        low = middle
                    else:
                        high = middle - 1
                if low == start:
                    if used:
                        used = page_characters
                        # append opens the next page when its first character
                        # does not fit the current page's remaining allowance.
                        single = member[start:start + 1]
                        append(path, 'string', member_key, ordinal, [start, start + 1], single)
                        start += 1
                        continue
                    _raise_composition_error('A single scientific character exceeds the page allowance.', pending=True, path=path)
                append(path, 'string', member_key, ordinal, [start, low], member[start:low])
                start = low
    composition._validate_plan(plan)
    return copy.deepcopy(plan)


def read_composition_source_fragments(plan, observations, *, owners=None):
    """Project only requested frozen ownership from the selected original output."""
    output = _select_scientific_output(plan['role'], observations)
    values, choices = _collect_composition_values(plan['role'], output)
    if any(plan['terminal_choices'].get(path) != [value] for path, value in choices.items()):
        _raise_composition_error('Source scalar decisions differ from the frozen page plan.')
    fragments = []
    for owner in plan['ownership'] if owners is None else owners:
        if owner not in plan['ownership']:
            _raise_composition_error('Only frozen page ownership can select a source fragment.')
        content = values[owner['path']]
        if owner['type'] == 'empty_list':
            content = []
        else:
            if owner['ordinal'] is not None:
                content = content[owner['ordinal']]
            if owner['type'] == 'string':
                content = content[slice(*owner['range'])]
        fragments.append(dict(copy.deepcopy(owner), content=copy.deepcopy(content)))
    return fragments
