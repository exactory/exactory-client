"""Pure entry-aware final plans, stored separately from reviewer packets.

Rooms exclude the caller's frame. Entries are {handle, characters}; characters
include their serialized fields. The injected policy owns blocks, output-entry
handles and bounds. No exact turn count is inferred from a character total.
"""
import copy

from .errors import ResearchError
from .evidence import digest
from .review_route_capacity import BELOW_FLOOR_MESSAGE, divide_room


FINAL_PAGE_PLAN_PROTOCOL = 'exactory-final-page-plan-v1'
INVALID_CONDENSING_MESSAGE = 'Condensing must provide exact blocks, rounds, output entries and a fitting remaining bound.'
MISSING_CONDENSING_MESSAGE = 'Pass the chain policy condensing function to plan this reduction.'
INVALID_SPLIT_MESSAGE = 'The turn to split must be a nonempty turn in this final plan, with a positive material floor.'
INVALID_PROGRESS_MESSAGE = (
    'Completed and pending must be lists of distinct turn numbers of the plan that is replaced, with no number in '
    'both; only a final, a page or a closing turn may be pending.')
INVALID_REPLACEMENT_MESSAGE = 'A successor needs its predecessor digest and an exact split, level, ratio or inventory cause.'
INVALID_PAGE_PLAN_MESSAGE = (
    'A stored page plan of this final does not verify. The harness builds each page plan again from the plan '
    'before it, its recorded state, its currency and its cause, and the plan that it builds is not the stored one. '
    'details.index, where present, numbers the plan in the chain of page plans, from 0. The harness takes no turn '
    'of the final from such a plan. Check the record with record-check.')
INVALID_FINAL_SIZES_MESSAGE = 'Final rooms and target allowance must be positive integers; sizes are nonnegative and handles distinct.'


def allocate_final_targets(page_room, target_allowance, notes, targets):
    """Allocate current divide_room parts, including each target's row overhead."""
    if type(target_allowance) is not int or target_allowance <= 0:
        raise ResearchError('invalid_input', INVALID_FINAL_SIZES_MESSAGE)
    if (len(targets) <= target_allowance
            and notes['minimum'] + sum(target['minimum'] for target in targets) <= page_room):
        division = divide_room(page_room, [notes] + targets)
        return dict(form='request', division=division,
                    notes=division['parts'][0]['allowance'], targets=division['parts'][1:])
    allocated = []
    for target in targets:
        overhead = target.get('overhead', 0)
        rows = min(overhead, page_room // 4)
        minimum = target['minimum'] - overhead + rows
        maximum = min(target['maximum'], page_room // 2)
        if minimum > maximum:
            _raise_below_floor(maximum, minimum)
        preferred = max(minimum, min(target['preferred'], maximum))
        allocated.append(dict(copy.deepcopy(target), minimum=minimum, preferred=preferred, maximum=maximum,
                              rows=rows, condense_rows=overhead > rows, allowance=preferred))
    largest = max((target['allowance'] for target in allocated), default=0)
    notes_bound = divide_room(page_room, [notes, _build_fixed_part(largest)])['parts'][0]['allowance']
    return dict(form='pages', division=None, notes=notes_bound, targets=allocated)


def _build_fixed_part(characters):
    return dict(minimum=characters, preferred=characters, maximum=characters)


def _sum_entries(entries):
    return sum(entry['characters'] for entry in entries)


def _raise_below_floor(room, minimum):
    raise ResearchError('review_route_capacity_below_floor', BELOW_FLOOR_MESSAGE,
                        dict(room=room, minimum=minimum, command='review-route'))


def _validate_entries(entries):
    if not isinstance(entries, list) or any(
            not isinstance(entry, dict) or set(entry) != {'handle', 'characters'}
            or not isinstance(entry['handle'], str) or not entry['handle']
            or type(entry['characters']) is not int or entry['characters'] < 0 for entry in entries):
        raise ResearchError('invalid_input', INVALID_FINAL_SIZES_MESSAGE)
    if len({entry['handle'] for entry in entries}) != len(entries):
        raise ResearchError('invalid_input', INVALID_FINAL_SIZES_MESSAGE)


def _validate_final_inputs(page_room, target_allowance, target_minimum, notes, pool, targets, closing_room, row_characters):
    sizes = [page_room, target_allowance, target_minimum, notes, closing_room, row_characters]
    if (any(type(size) is not int or size < 0 for size in sizes)
            or not page_room or not target_allowance or not closing_room):
        raise ResearchError('invalid_input', INVALID_FINAL_SIZES_MESSAGE)
    _validate_entries(pool)
    handles = []
    for target in targets:
        if (not isinstance(target, dict) or set(target) != {'handle', 'entries', 'rows'}
                or not isinstance(target['handle'], str) or not target['handle']):
            raise ResearchError('invalid_input', INVALID_FINAL_SIZES_MESSAGE)
        handles.append(target['handle'])
        _validate_entries(target['entries'])
        _validate_entries(target['rows'])
    if len(set(handles)) != len(handles):
        raise ResearchError('invalid_input', INVALID_FINAL_SIZES_MESSAGE)


def _build_context(page_room, target_allowance, target_minimum, notes, pool, closing_room, row_characters):
    return dict(page_room=page_room, target_allowance=target_allowance, target_minimum=target_minimum,
                notes=notes, pool=_sum_entries(pool), closing_room=closing_room, row_characters=row_characters)


def _build_turn(kind, context, targets, characters, *, parts=None, inputs=None, pool=None,
                pool_inputs=None, rows=None, rows_inputs=None, scope=None, component=None, block=None,
                output=None, entry_sizes=None):
    return dict(kind=kind, number=0, targets=list(targets), characters=characters,
                context=copy.deepcopy(context), parts=copy.deepcopy(parts or []), inputs=copy.deepcopy(inputs or []),
                pool=copy.deepcopy(pool or []), pool_inputs=copy.deepcopy(pool_inputs or []),
                rows=copy.deepcopy(rows or []), rows_inputs=copy.deepcopy(rows_inputs or []),
                scope=scope, component=component, block=copy.deepcopy(block),
                output=copy.deepcopy(output), entry_sizes=copy.deepcopy(entry_sizes or []),
                split_of=None, pending_of=None)


def _build_page(kind, context, pool, pool_inputs, parts, inputs):
    sizes = [context['notes'], _sum_entries(pool)] + [_sum_entries(part['entries'] + part['rows']) for part in parts]
    division = divide_room(context['page_room'], [_build_fixed_part(size) for size in sizes])
    if len(parts) > context['target_allowance']:
        raise ResearchError('invalid_input', INVALID_FINAL_SIZES_MESSAGE)
    return _build_turn(kind, dict(context, pool=_sum_entries(pool)), [part['handle'] for part in parts],
                       division['minimum'], parts=parts, inputs=inputs, pool=pool, pool_inputs=pool_inputs)


def _replace_blocks(entries, blocks, strict=False):
    """Apply whole-entry replacements; completed older stages may already be applied."""
    by_first = {block['entries'][0]: (block, output) for block, output in blocks}
    result, found, index = [], set(), 0
    while index < len(entries):
        entry = entries[index]
        replacement = by_first.get(entry['handle'])
        if replacement is not None:
            block, output = replacement
            count = len(block['entries'])
            if [value['handle'] for value in entries[index:index + count]] == block['entries']:
                result.append(copy.deepcopy(output))
                found.add(block['handle'])
                index += count
                continue
        result.append(copy.deepcopy(entry))
        index += 1
    if strict and len(found) != len(blocks):
        raise ResearchError('invalid_input', INVALID_CONDENSING_MESSAGE)
    return result


def _plan_condensing(entries, room, context, pool, scope, targets, component, condense, force=False):
    """Consume the policy's entry plan; force uses its already-fitting blocks."""
    if room <= 0:
        _raise_below_floor(room, max(1, _sum_entries(entries)))
    if condense is None:
        raise ResearchError('invalid_input', MISSING_CONDENSING_MESSAGE)
    try:
        plan = condense(copy.deepcopy(entries), room)
        rounds = plan['rounds']
        count = plan['turns']
        remaining = plan['entries']
        bound = plan['remaining_characters']
        if not isinstance(rounds, list) or type(count) is not int or count < 0 or count != sum(len(r) for r in rounds):
            raise ValueError
        if force and not rounds:
            minimum = plan.get('minimum_block_characters')
            if minimum is None:
                total = _sum_entries(entries)
                largest = max((entry['characters'] for entry in entries), default=0)
                if total <= largest:
                    return [], copy.deepcopy(entries)
                # Ask the same pure policy about a smaller whole-entry layout.
                # A permitted subblock proves that the containing block meets
                # its minimum; a policy-floor refusal states that minimum.
                try:
                    probe = condense(copy.deepcopy(entries), total - 1)
                    eligible = [block['characters'] for round_ in probe['rounds'] for block in round_]
                    if not eligible:
                        return [], copy.deepcopy(entries)
                    minimum = min(eligible)
                except ResearchError as error:
                    minimum = (error.details or {}).get('minimum')
                    if error.code != 'invalid_input' or minimum is None:
                        raise
            if type(minimum) is not int or minimum <= 0:
                raise ValueError
            blocks = [block for block in plan['blocks'] if block['characters'] >= minimum]
            if not blocks:
                return [], copy.deepcopy(entries)
            rounds = [blocks]
        computed = copy.deepcopy(entries)
        turns = []
        final_entries = {entry['handle']: entry for entry in remaining}
        for round_ in rounds:
            by_handle = {entry['handle']: entry for entry in computed}
            replacements = []
            for block in round_:
                source = [by_handle[handle] for handle in block['entries']]
                # The policy uses the block handle for its complete combination;
                # intermediate bounds are its documented whole-block quarters.
                output = final_entries.get(block['handle'], dict(
                    handle=block['handle'], characters=block['characters'] // 4))
                _validate_entries([output])
                if (not block['entries'] or not isinstance(block['handle'], str)
                        or block['characters'] != _sum_entries(source)
                        or block['characters'] > room
                        or output['characters'] >= block['characters']):
                    raise ValueError
                base = 0 if scope == 'rows' else context['notes'] + (0 if scope == 'pool' else _sum_entries(pool))
                turns.append(_build_turn('combine', context, targets, base + block['characters'],
                                         pool=pool, scope=scope, component=component, block=block,
                                         output=output, entry_sizes=source))
                replacements.append((block, output))
            computed = _replace_blocks(computed, replacements, strict=True)
        if force and count == 0:
            remaining, bound = computed, _sum_entries(computed)
        _validate_entries(remaining)
        if (not turns or type(bound) is not int or bound != _sum_entries(remaining) or bound > room
                or digest(computed) != digest(remaining)):
            raise ValueError
        return turns, copy.deepcopy(remaining)
    except (KeyError, TypeError, ValueError, IndexError) as error:
        raise ResearchError('invalid_input', INVALID_CONDENSING_MESSAGE) from error


def _prepare_pool(context, pool, condense):
    room = context['page_room'] - context['notes'] - context['target_minimum']
    if room < 0:
        _raise_below_floor(context['page_room'], context['notes'] + context['target_minimum'])
    if _sum_entries(pool) > room:
        return _plan_condensing(pool, room, context, [], 'pool', [], None, condense)
    return [], copy.deepcopy(pool)


def _pack_pages(context, pool, pool_inputs, targets, condense):
    available = context['page_room'] - context['notes'] - _sum_entries(pool)
    turns, pending, inputs = [], [], []
    size = 0
    for target in targets:
        characters = _sum_entries(target['entries'] + target['rows'])
        needs_rows = _sum_entries(target['rows']) > context['page_room'] // 4
        if pending and (size + characters > available or len(pending) == context['target_allowance'] or needs_rows):
            turns.append(_build_page('page', context, pool, pool_inputs, pending, inputs))
            pending, inputs, size = [], [], 0
        part, reductions = copy.deepcopy(target), []
        if needs_rows:
            reductions, part['rows'] = _plan_condensing(part['rows'], min(context['page_room'] // 4, available),
                context, pool, 'target', [part['handle']], 'rows', condense)
        if _sum_entries(part['entries'] + part['rows']) > available:
            extra, entries = _plan_condensing(part['entries'] + part['rows'], available, context, pool,
                                              'target', [part['handle']], 'entries', condense)
            reductions.extend(extra)
            part['entries'], part['rows'] = entries, []
        if reductions:
            turns.extend(reductions)
            turns.append(_build_page('page', context, pool, pool_inputs, [part], [target]))
        else:
            pending.append(part)
            inputs.append(target)
            size += characters
    if pending:
        turns.append(_build_page('page', context, pool, pool_inputs, pending, inputs))
    return turns


def _build_decision_rows(targets, row_characters):
    return [dict(handle='decision:' + handle, characters=row_characters) for handle in targets]


def _plan_closing(context, targets, condense, rows=None, rows_inputs=None):
    rows = _build_decision_rows(targets, context['row_characters']) if rows is None else rows
    original = rows if rows_inputs is None else rows_inputs
    turns = []
    if _sum_entries(rows) > context['closing_room']:
        turns, rows = _plan_condensing(rows, context['closing_room'], context, [], 'rows', [], None, condense)
    turns.append(_build_turn('closing', context, targets, _sum_entries(rows), rows=rows, rows_inputs=original))
    return turns


def _number_turns(turns, start):
    return [dict(copy.deepcopy(turn), number=number) for number, turn in enumerate(turns, start)]


def plan_final_turns(page_room, target_allowance, target_minimum, notes, pool, targets,
                     closing_room, row_characters, condense):
    """Plan exact entry blocks, then bounded ordered pages and a closing if needed."""
    _validate_final_inputs(page_room, target_allowance, target_minimum, notes, pool, targets, closing_room, row_characters)
    context = _build_context(page_room, target_allowance, target_minimum, notes, pool, closing_room, row_characters)
    turns, carried_pool = _prepare_pool(context, pool, condense)
    context['pool'] = _sum_entries(carried_pool)
    total = notes + context['pool'] + sum(_sum_entries(t['entries'] + t['rows']) for t in targets)
    if total <= page_room and len(targets) <= target_allowance:
        turns.append(_build_page('page' if turns else 'final', context, carried_pool, pool, targets, targets))
        if len(turns) > 1:
            turns.extend(_plan_closing(context, [target['handle'] for target in targets], condense))
    else:
        turns.extend(_pack_pages(context, carried_pool, pool, targets, condense))
        turns.extend(_plan_closing(context, [target['handle'] for target in targets], condense))
    return _number_turns(turns, 1)


def _rewrite_entries(entries, replacements):
    return [copy.deepcopy(value) for entry in entries for value in replacements.get(entry['handle'], [entry])]


def _rewrite_consumers(turns, replacements, condense):
    """Propagate the outputs of a split block into its later dependent blocks."""
    result = []
    for turn in turns:
        if turn['kind'] == 'combine':
            entries = _rewrite_entries(turn['entry_sizes'], replacements)
            pool = _rewrite_entries(turn['pool'], replacements)
            if entries != turn['entry_sizes']:
                context = dict(turn['context'], pool=_sum_entries(pool))
                rebuilt, remaining = _plan_condensing(entries, max(1, _sum_entries(entries)), context, pool,
                    turn['scope'], turn['targets'], turn['component'], condense, force=True)
                replacements = dict(replacements, **{turn['output']['handle']: remaining})
                result.extend(rebuilt)
                continue
            if pool != turn['pool']:
                base = 0 if turn['scope'] == 'rows' else turn['context']['notes'] + (
                    0 if turn['scope'] == 'pool' else _sum_entries(pool))
                result.append(dict(copy.deepcopy(turn), number=0, pool=pool,
                                   context=dict(turn['context'], pool=_sum_entries(pool)),
                                   characters=base + turn['block']['characters']))
                continue
        if turn['kind'] in ('page', 'final'):
            parts = [dict(part, entries=_rewrite_entries(part['entries'], replacements),
                          rows=_rewrite_entries(part['rows'], replacements)) for part in turn['parts']]
            pool = _rewrite_entries(turn['pool'], replacements)
            if parts != turn['parts'] or pool != turn['pool']:
                result.append(_build_page(turn['kind'], turn['context'], pool, turn['pool_inputs'], parts, turn['inputs']))
                continue
        if turn['kind'] == 'closing':
            rows = _rewrite_entries(turn['rows'], replacements)
            if rows != turn['rows']:
                result.append(_build_turn('closing', turn['context'], turn['targets'], _sum_entries(rows),
                                          rows=rows, rows_inputs=turn['rows_inputs']))
                continue
        result.append(copy.deepcopy(turn))
    return result


def split_final_turn(turns, number, floor, condense, *, pending=None):
    """Return a smaller task for five final forms, or None at the pinned floor."""
    selected = next((turn for turn in turns if turn['number'] == number), None)
    if selected is None or type(floor) is not int or floor <= 0:
        raise ResearchError('invalid_input', INVALID_SPLIT_MESSAGE)
    context, replacement, rewrites = selected['context'], [], {}
    if selected['kind'] in ('page', 'final'):
        parts, inputs = selected['parts'], selected['inputs']
        if not parts:
            raise ResearchError('invalid_input', INVALID_SPLIT_MESSAGE)
        if len(parts) > 1:
            half = (len(parts) + 1) // 2
            replacement = [_build_page('page', context, selected['pool'], selected['pool_inputs'], p, i)
                           for p, i in ((parts[:half], inputs[:half]), (parts[half:], inputs[half:]))]
        else:
            entries = parts[0]['entries'] + parts[0]['rows']
            characters = _sum_entries(entries)
            if characters <= floor:
                return None
            room = max(floor, characters // 2, max(entry['characters'] for entry in entries))
            replacement, remaining = _plan_condensing(entries, room, context, selected['pool'], 'target',
                                                       selected['targets'], 'entries', condense, force=True)
            if not replacement:
                return None
            part = dict(parts[0], entries=remaining, rows=[])
            replacement.append(_build_page('page', context, selected['pool'], selected['pool_inputs'], [part], inputs))
        if selected['kind'] == 'final':
            replacement.extend(_plan_closing(context, selected['targets'], condense))
    elif selected['kind'] in ('combine', 'closing'):
        entries = selected['entry_sizes'] if selected['kind'] == 'combine' else selected['rows']
        if len(entries) <= 1:
            return None
        half = (len(entries) + 1) // 2
        halves = (entries[:half], entries[half:])
        scope = selected['scope'] if selected['kind'] == 'combine' else 'rows'
        room = max(_sum_entries(block) for block in halves)
        if room <= 0 or room >= _sum_entries(entries):
            return None
        try:
            planned, outputs = _plan_condensing(entries, room, context, selected['pool'], scope,
                selected['targets'] if scope != 'rows' else [], selected['component'], condense)
        except ResearchError as error:
            if error.code == 'invalid_input' and (error.details or {}).get('minimum') is not None:
                planned, outputs = [], []
            else:
                raise
        expected = [[entry['handle'] for entry in block] for block in halves]
        if len(planned) == 2 and [turn['block']['entries'] for turn in planned] == expected:
            replacement = planned
        else:
            outputs = []
            for block in halves:
                extra, remaining = _plan_condensing(block, max(1, _sum_entries(block)), context, selected['pool'],
                    scope, selected['targets'] if scope != 'rows' else [], selected['component'], condense, force=True)
                if not extra:
                    return None
                replacement.extend(extra)
                outputs.extend(remaining)
        if selected['kind'] == 'closing':
            replacement.append(_build_turn('closing', context, selected['targets'], _sum_entries(outputs),
                                           rows=outputs, rows_inputs=selected['rows_inputs']))
        else:
            rewrites[selected['output']['handle']] = outputs
    else:
        raise ResearchError('invalid_input', INVALID_SPLIT_MESSAGE)
    index = turns.index(selected)
    later = _rewrite_consumers(turns[index + 1:], rewrites, condense) if rewrites else copy.deepcopy(turns[index + 1:])
    result = copy.deepcopy(turns[:index]) + replacement + later
    next_number = max(turn['number'] for turn in turns) + 1
    for turn in result:
        if turn['number'] == 0:
            turn['split_of'] = number
            if number in (pending or []) or selected.get('pending_of') is not None:
                turn['pending_of'] = selected.get('pending_of') or number
            turn['number'] = next_number
            next_number += 1
    return result


def _apply_completed(entries, turns, scope, handle=None, component=None):
    result = copy.deepcopy(entries)
    for turn in turns:
        if (turn['kind'] == 'combine' and turn['scope'] == scope
                and (handle is None or turn['targets'] == [handle])
                and (component is None or turn['component'] == component)):
            result = _replace_blocks(result, [(turn['block'], turn['output'])])
    return result


def _validate_completed(turns, completed):
    if (not isinstance(completed, list) or any(type(number) is not int for number in completed)
            or len(set(completed)) != len(completed) or not set(completed) <= {turn['number'] for turn in turns}):
        raise ResearchError('invalid_input', INVALID_PROGRESS_MESSAGE)


def _validate_progress(turns, completed, pending):
    _validate_completed(turns, completed)
    _validate_completed(turns, pending)
    if (set(completed) & set(pending) or any(turn['number'] in pending
            and turn['kind'] not in ('final', 'page', 'closing') for turn in turns)):
        raise ResearchError('invalid_input', INVALID_PROGRESS_MESSAGE)


def _collect_preserved(turns, completed, pending):
    preserved = set(completed) | set(pending) | {turn['number'] for turn in turns if turn.get('split_of') is not None}
    by_number = {turn['number']: turn for turn in turns}
    producers = {turn['output']['handle']: turn['number'] for turn in turns if turn['kind'] == 'combine'}
    remaining = list(preserved)
    while remaining:
        turn = by_number[remaining.pop()]
        entries = turn['pool'] + turn['rows'] + turn['entry_sizes']
        entries += [entry for part in turn['parts'] for entry in part['entries'] + part['rows']]
        for entry in entries:
            producer = producers.get(entry['handle'])
            if producer is not None and producer not in preserved:
                preserved.add(producer)
                remaining.append(producer)
    return preserved


def repack_final_turns(turns, completed, pending, page_room, target_allowance, *, target_minimum=None, notes=None,
                       pool=None, targets=None, closing_room=None, row_characters=None, condense=None):
    """Keep terminal, pending and split-held work; replan other assigned pages. A room that cannot hold the carried
    material raises review_route_capacity_below_floor."""
    _validate_progress(turns, completed, pending)
    selected = _collect_preserved(turns, completed, pending)
    if len(selected) == len(turns):
        return copy.deepcopy(turns)
    pages = [turn for turn in turns if turn['kind'] in ('final', 'page')]
    previous = turns[-1]['context']
    values = dict(previous, page_room=page_room, target_allowance=target_allowance)
    for key, value in (('target_minimum', target_minimum), ('notes', notes),
                       ('closing_room', closing_room), ('row_characters', row_characters)):
        if value is not None:
            values[key] = value
    original_pool = pages[0]['pool_inputs'] if pool is None else pool
    original_targets = [target for turn in pages for target in turn['inputs']] if targets is None else targets
    kept = [turn for turn in turns if turn['number'] in completed]
    carried_pool = _apply_completed(original_pool, kept, 'pool')
    context = dict(values, pool=_sum_entries(carried_pool))
    try:
        pool_turns, carried_pool = _prepare_pool(context, carried_pool, condense)
        context['pool'] = _sum_entries(carried_pool)
        by_handle = {}
        for target in original_targets:
            rows = _apply_completed(target['rows'], kept, 'target', target['handle'], 'rows')
            entries = _apply_completed(target['entries'] + rows, kept, 'target', target['handle'], 'entries')
            if entries != target['entries'] + rows:
                by_handle[target['handle']] = dict(target, entries=entries, rows=[])
            else:
                by_handle[target['handle']] = dict(target, rows=rows)
        result, pending = [], []
        for turn in turns:
            if turn['number'] in selected:
                if pending:
                    result.extend(pool_turns)
                    pool_turns = []
                    result.extend(_pack_pages(context, carried_pool, original_pool, pending, condense))
                    pending = []
                result.append(copy.deepcopy(turn))
            elif turn['kind'] in ('final', 'page'):
                pending.extend(by_handle[handle] for handle in turn['targets'])
        if pending:
            result.extend(pool_turns)
            result.extend(_pack_pages(context, carried_pool, original_pool, pending, condense))
        if not any(turn['kind'] in ('closing', 'final') for turn in result):
            handles = [target['handle'] for target in original_targets]
            rows = _build_decision_rows(handles, context['row_characters'])
            result.extend(_plan_closing(context, handles, condense, _apply_completed(rows, kept, 'rows'), rows))
    except ResearchError as error:
        # A condensing refusal with a minimum block: the measured room cannot hold the carried material at all, so
        # the room of this chain is below its floor.
        if error.code != 'invalid_input' or 'minimum' not in (error.details or {}):
            raise
        _raise_below_floor(error.details['room'], error.details['minimum'])
    next_number = max(turn['number'] for turn in turns) + 1
    for turn in result:
        if turn['number'] == 0:
            turn['number'] = next_number
            next_number += 1
    return result


def prepare_final_turn(turns, number, page_room, target_allowance, floor, condense, *, pending=None, completed=(), inputs=None):
    """Admit a held/pending page at current limits without joining split groups.

    The family resumes pending work through pending_of, which names the original
    page identity. This function stores no passage or argument body.
    """
    selected = next((turn for turn in turns if turn['number'] == number), None)
    if selected is None or selected['kind'] not in ('page', 'final', 'closing', 'combine') or type(floor) is not int or floor <= 0:
        raise ResearchError('invalid_input', INVALID_SPLIT_MESSAGE)
    if type(page_room) is not int or page_room <= 0 or type(target_allowance) is not int or target_allowance <= 0:
        raise ResearchError('invalid_input', INVALID_FINAL_SIZES_MESSAGE)
    if inputs is not None and selected['kind'] in ('page', 'final'):
        kept = [turn for turn in turns if turn['number'] in completed]
        pool = _apply_completed(inputs['pool'], kept, 'pool')
        parts, source = [], {target['handle']: target for target in inputs['targets']}
        for handle in selected['targets']:
            target = source[handle]
            rows = _apply_completed(target['rows'], kept, 'target', handle, 'rows')
            entries = _apply_completed(target['entries'] + rows, kept, 'target', handle, 'entries')
            parts.append(dict(target, entries=entries, rows=[]) if entries != target['entries'] + rows else dict(target, rows=rows))
        selected = dict(selected, pool=pool, pool_inputs=inputs['pool'], parts=parts,
            inputs=[source[handle] for handle in selected['targets']],
            characters=inputs['notes'] + _sum_entries(pool) + sum(_sum_entries(part['entries'] + part['rows']) for part in parts),
            context=dict(selected['context'], notes=inputs['notes']))
    fits = (selected['block']['characters'] <= page_room if selected['kind'] == 'combine' else
            selected['characters'] <= (inputs.get('closing_room', page_room) if inputs is not None and selected['kind'] == 'closing' else page_room))
    if fits and (selected['kind'] not in ('page', 'final') or len(selected['targets']) <= target_allowance):
        return copy.deepcopy(turns)
    context = dict(selected['context'], page_room=page_room, target_allowance=target_allowance,
                   closing_room=min(selected['context']['closing_room'], page_room,
                                    inputs.get('closing_room', page_room) if inputs is not None else page_room))
    selected = dict(selected, context=context)
    if selected['kind'] in ('closing', 'combine'):
        updated = [selected if turn['number'] == number else turn for turn in turns]
        return split_final_turn(updated, number, floor, condense, pending=pending)
    pool_turns, pool = _prepare_pool(context, selected['pool'], condense)
    context['pool'] = _sum_entries(pool)
    available = page_room - context['notes'] - context['pool']
    if len(selected['parts']) == 1:
        size = _sum_entries(selected['parts'][0]['entries'] + selected['parts'][0]['rows'])
        if available < size <= floor:
            return None
    replacement = pool_turns + _pack_pages(context, pool, selected['pool_inputs'], selected['parts'], condense)
    if selected['kind'] == 'final':
        replacement.extend(_plan_closing(context, selected['targets'], condense))
    origin = selected.get('pending_of') or (number if number in (pending or []) else None)
    next_number = max(turn['number'] for turn in turns) + 1
    for turn in replacement:
        turn.update(number=next_number, split_of=number, pending_of=origin)
        next_number += 1
    result = []
    for turn in turns:
        result.extend(replacement if turn['number'] == number else [copy.deepcopy(turn)])
    return result


def count_final_turns(page_room, target_allowance, target_minimum, notes, pool, targets,
                      closing_room, row_characters, condense=None, *, forecast=None, largest_entry=None):
    """Count exact entries, or explicitly mark a total-only admission forecast."""
    if forecast is not None:
        return _forecast_final_turns(page_room, target_allowance, target_minimum, notes, pool, targets,
                                     closing_room, row_characters, forecast, largest_entry)
    turns = plan_final_turns(page_room, target_allowance, target_minimum, notes, pool, targets,
                             closing_room, row_characters, condense)
    return dict(pages=sum(turn['kind'] in ('final', 'page') for turn in turns),
                condensing_turns=sum(turn['kind'] == 'combine' for turn in turns),
                closing_pages=sum(turn['kind'] == 'closing' for turn in turns), forecast=False)


def _forecast_final_turns(page_room, target_allowance, target_minimum, notes, pool, targets,
                          closing_room, row_characters, forecast, largest_entry):
    sizes = [page_room, target_allowance, target_minimum, notes, pool, closing_room, row_characters, largest_entry]
    sizes.extend(size for target in targets for size in (target['entries'], target['rows']))
    if (any(type(size) is not int or size < 0 for size in sizes)
            or not page_room or not target_allowance or not closing_room):
        raise ResearchError('invalid_input', INVALID_FINAL_SIZES_MESSAGE)
    condensing = 0
    pool_room = page_room - notes - target_minimum
    if pool_room < 0:
        _raise_below_floor(page_room, notes + target_minimum)
    if pool > pool_room:
        count, pool = _read_forecast(forecast, pool, pool_room, largest_entry)
        condensing += count
    available = page_room - notes - pool
    if len(targets) <= target_allowance and sum(t['entries'] + t['rows'] for t in targets) <= available:
        return dict(pages=1, condensing_turns=condensing, closing_pages=0, forecast=True)
    pages, size, target_count = 0, 0, 0
    for target in targets:
        entries, rows = target['entries'], target['rows']
        reduced = False
        if rows > page_room // 4:
            count, rows = _read_forecast(forecast, rows, min(page_room // 4, available), largest_entry)
            condensing += count
            reduced = True
        if entries + rows > available:
            count, entries = _read_forecast(forecast, entries + rows, available, largest_entry)
            condensing += count
            rows = 0
            reduced = True
        characters = entries + rows
        if reduced:
            pages += bool(target_count) + 1
            size, target_count = 0, 0
            continue
        if target_count and (size + characters > available or target_count == target_allowance):
            pages += 1
            size, target_count = 0, 0
        size, target_count = size + characters, target_count + 1
    pages += bool(target_count)
    if len(targets) * row_characters > closing_room:
        count, _ = _read_forecast(forecast, len(targets) * row_characters, closing_room,
                                  min(largest_entry, row_characters))
        condensing += count
    return dict(pages=pages, condensing_turns=condensing, closing_pages=1, forecast=True)


def _read_forecast(forecast, characters, room, largest_entry):
    if room <= 0:
        _raise_below_floor(room, characters)
    result = forecast(characters, room, largest_entry)
    if isinstance(result, dict):
        count, bound = result.get('turns'), result.get('remaining_characters')
        marked = result.get('forecast') is True
    else:
        count, bound, marked = result, room, True
    if (not marked or type(count) is not int or count <= 0
            or type(bound) is not int or not 0 <= bound <= room):
        raise ResearchError('invalid_input', INVALID_CONDENSING_MESSAGE)
    return count, bound


def _validate_replacement_cause(replaces, cause):
    if not isinstance(replaces, str) or not replaces or not isinstance(cause, dict):
        raise ResearchError('invalid_input', INVALID_REPLACEMENT_MESSAGE)
    kind = cause.get('kind')
    members = {'split': {'kind', 'turn', 'attempt_id', 'completed', 'pending'},
               'level': {'kind', 'attempt_id', 'page_room', 'target_allowance', 'completed', 'pending'},
               'ratio': {'kind', 'attempt_id', 'page_room', 'completed', 'pending'},
               'inventory': {'kind', 'replaced_inventory_digest', 'inventory_digest'}}
    valid = isinstance(kind, str) and kind in members and set(cause) == members[kind]
    if valid and kind == 'inventory':
        valid = all(isinstance(cause[key], str) and cause[key] for key in members[kind])
    elif valid:
        valid = isinstance(cause['attempt_id'], str) and bool(cause['attempt_id'])
        for key in ('completed', 'pending'):
            numbers = cause[key]
            valid = valid and isinstance(numbers, list) and all(type(n) is int and n > 0 for n in numbers)
            valid = valid and len(set(numbers)) == len(numbers)
        valid = valid and not set(cause['completed']) & set(cause['pending'])
        for key in ('turn', 'page_room', 'target_allowance'):
            if key in cause:
                valid = valid and type(cause[key]) is int and cause[key] > 0
    if not valid:
        raise ResearchError('invalid_input', INVALID_REPLACEMENT_MESSAGE)


def build_page_plan(chain, plan_digest, state_digest, currency, turns, replaces=None, cause=None):
    """Build a canonical JSON value; absent provenance is omitted on first plans."""
    if replaces is not None or cause is not None:
        _validate_replacement_cause(replaces, cause)
    result = dict(protocol=FINAL_PAGE_PLAN_PROTOCOL, chain=copy.deepcopy(chain), plan_digest=plan_digest,
                  state_digest=state_digest, currency=copy.deepcopy(currency), turns=copy.deepcopy(turns))
    if replaces is not None:
        result.update(replaces=replaces, cause=copy.deepcopy(cause))
    return result


def read_plan_room(plans):
    """Return the page room of the last of a chain of page plans: the room of its latest level or ratio cause, else the
    room of the latest plan without a cause or with the cause inventory, which its turns hold. A split keeps the room.
    A ratio cause records a room below this one; the live side and the replay both compare with it."""
    for plan in reversed(plans):
        cause = plan.get('cause', {})
        if cause.get('kind') in ('level', 'ratio'):
            return cause['page_room']
        if cause.get('kind') in (None, 'inventory'):
            return plan['turns'][0]['context']['page_room']
    raise ResearchError('artifact_corrupt', INVALID_PAGE_PLAN_MESSAGE)


def verify_page_plans(plans, read_inputs):
    """Replay frozen state and cause.completed; never consult later live progress.

    read_inputs(plan) returns the plan_final_turns keyword inputs and, for split
    replay, the pinned material ``floor``. It must resolve the plan's state and
    currency, not the latest mutable state of the family.
    """
    if not isinstance(plans, list) or not plans:
        raise ResearchError('artifact_corrupt', INVALID_PAGE_PLAN_MESSAGE)
    index = 0
    try:
        for index, plan in enumerate(plans):
            inputs = dict(read_inputs(plan))
            floor = inputs.pop('floor', None)
            if index == 0:
                room, allowance = inputs['page_room'], inputs['target_allowance']
                turns = plan_final_turns(**inputs)
                expected = build_page_plan(plan['chain'], plan['plan_digest'], plan['state_digest'], plan['currency'], turns)
            else:
                previous = plans[index - 1]
                cause = plan['cause']
                _validate_replacement_cause(plan['replaces'], cause)
                kind = cause['kind']
                if kind == 'inventory':
                    if (cause['replaced_inventory_digest'] != previous['currency']['inventory_digest']
                            or cause['inventory_digest'] != plan['currency']['inventory_digest']
                            or cause['inventory_digest'] == cause['replaced_inventory_digest']):
                        raise ValueError
                    turns = plan_final_turns(**inputs)
                    room, allowance = inputs['page_room'], inputs['target_allowance']
                else:
                    if plan['currency'] != previous['currency']:
                        raise ValueError
                    _validate_progress(previous['turns'], cause['completed'], cause['pending'])
                    if kind == 'split':
                        if cause['turn'] in cause['completed']:
                            raise ValueError
                        selected = next((turn for turn in previous['turns'] if turn['number'] == cause['turn']), None)
                        if selected is None:
                            raise ValueError
                        turns = prepare_final_turn(previous['turns'], cause['turn'], room, allowance, floor,
                            inputs['condense'], pending=cause['pending'], completed=cause['completed'], inputs=inputs)
                        if turns == previous['turns']:
                            turns = split_final_turn(previous['turns'], cause['turn'], floor, inputs['condense'],
                                                     pending=cause['pending'])
                        if turns is None:
                            raise ValueError
                    else:
                        if kind == 'ratio' and cause['page_room'] >= read_plan_room(plans[:index]):
                            raise ValueError
                        room = cause['page_room']
                        if kind == 'level':
                            allowance = cause['target_allowance']
                        state = {key: value for key, value in inputs.items() if key not in ('page_room', 'target_allowance')}
                        turns = repack_final_turns(previous['turns'], cause['completed'], cause['pending'], room, allowance, **state)
                expected = build_page_plan(previous['chain'], previous['plan_digest'], plan['state_digest'],
                                           plan['currency'], turns, digest(previous), cause)
            if digest(plan) != digest(expected):
                raise ValueError
    except (KeyError, TypeError, ValueError, IndexError, ResearchError) as error:
        raise ResearchError('artifact_corrupt', INVALID_PAGE_PLAN_MESSAGE, dict(index=index)) from error
    return True
