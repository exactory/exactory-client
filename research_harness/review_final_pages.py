"""Plan a final over serialized sizes and handles, without reading a chain store.

Rooms exclude the caller's frame. Notes and pool sizes include their serialized
fields, as do each target's entries and rows. Plans are JSON values; canonical
serialization and hashing use evidence.digest, like other harness artifacts.
"""

import copy

from .errors import ResearchError
from .evidence import digest
from .review_route_capacity import BELOW_FLOOR_MESSAGE, divide_room


FINAL_PAGE_PLAN_PROTOCOL = 'exactory-final-page-plan-v1'
INVALID_CONDENSING_MESSAGE = 'Condensing must return a positive turn count and a nonnegative bound within its room.'
MISSING_CONDENSING_MESSAGE = 'Pass the chain policy condensing function to plan this reduction.'
INVALID_SPLIT_MESSAGE = 'The turn to split must be a nonempty final or page in this plan.'
INVALID_ASSIGNED_MESSAGE = 'Assigned turn numbers must be distinct numbers from the predecessor plan.'
INVALID_REPLACEMENT_MESSAGE = 'A successor needs its predecessor digest and an exact split or level cause with an attempt id.'
INVALID_PAGE_PLAN_MESSAGE = 'The final page plan does not replay from its predecessor and authenticated cause.'
INVALID_FINAL_SIZES_MESSAGE = 'Final sizes must be nonnegative integers, rooms positive, and target handles distinct nonempty strings.'


def allocate_final_targets(page_room, notes, targets):
    """Allocate parts shaped like plan_chain_capacity's divide_room inputs.

    The target's ``overhead`` is its serialized gap and source rows, already
    included in minimum/preferred/maximum. The request division is unchanged.
    Paged allocations bound rows at a quarter of a page before granting entries.
    """
    if notes['minimum'] + sum(target['minimum'] for target in targets) <= page_room:
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
            raise ResearchError('review_route_capacity_below_floor', BELOW_FLOOR_MESSAGE,
                                dict(room=maximum, minimum=minimum, target=target['id'], command='review-route'))
        preferred = max(minimum, min(target['preferred'], maximum))
        allocated.append(dict(copy.deepcopy(target), minimum=minimum, preferred=preferred, maximum=maximum,
                              rows=rows, condense_rows=overhead > rows, allowance=preferred))
    largest = max((target['allowance'] for target in allocated), default=0)
    notes_bound = divide_room(page_room, [notes, _build_fixed_part(largest)])['parts'][0]['allowance']
    return dict(form='pages', division=None, notes=notes_bound, targets=allocated)


def _build_fixed_part(characters):
    return dict(minimum=characters, preferred=characters, maximum=characters)


def plan_final_turns(page_room, notes, pool, targets, closing_room, row_characters, condense):
    """Pack ordered ``{handle, entries, rows}`` sizes into a final or pages.

    ``condense(characters, room)`` returns ``(turn_count, remaining_bound)``.
    Combine characters are request bounds: the callback supplies a count and a
    remaining bound, rather than the sizes of its individual blocks. Context and
    input sizes are artifact metadata, never part of a reviewer packet.
    """
    _validate_final_sizes(page_room, notes, pool, targets, closing_room, row_characters)
    context = dict(page_room=page_room, notes=notes, pool=pool,
                   closing_room=closing_room, row_characters=row_characters)
    total = notes + pool + sum(target['entries'] + target['rows'] for target in targets)
    if total <= page_room:
        turns = [_build_page('final', context, targets, targets)]
    else:
        turns = _pack_pages(context, targets, condense)
        turns.extend(_plan_closing(context, [target['handle'] for target in targets], condense))
    return _number_turns(turns, 1)


def _validate_final_sizes(page_room, notes, pool, targets, closing_room, row_characters):
    sizes = [page_room, notes, pool, closing_room, row_characters]
    handles = []
    for target in targets:
        sizes.extend((target['entries'], target['rows']))
        handles.append(target['handle'])
    if (any(type(size) is not int or size < 0 for size in sizes) or page_room == 0 or closing_room == 0
            or any(not isinstance(handle, str) or not handle for handle in handles)
            or len(set(handles)) != len(handles)):
        raise ResearchError('invalid_input', INVALID_FINAL_SIZES_MESSAGE)


def _build_page(kind, context, parts, inputs):
    sizes = [context['notes'], context['pool']]
    sizes.extend(part['entries'] + part['rows'] for part in parts)
    division = divide_room(context['page_room'], [_build_fixed_part(size) for size in sizes])
    return dict(kind=kind, number=0, targets=[part['handle'] for part in parts],
                characters=division['minimum'], context=copy.deepcopy(context),
                parts=copy.deepcopy(parts), inputs=copy.deepcopy(inputs), scope=None)


def _plan_condensing(characters, room, context, targets, scope, condense):
    if room <= 0:
        raise ResearchError('review_route_capacity_below_floor', BELOW_FLOOR_MESSAGE,
                            dict(room=room, minimum=characters, command='review-route'))
    if condense is None:
        raise ResearchError('invalid_input', MISSING_CONDENSING_MESSAGE)
    result = condense(characters, room)
    if not isinstance(result, (tuple, list)) or len(result) != 2:
        raise ResearchError('invalid_input', INVALID_CONDENSING_MESSAGE)
    count, remaining = result
    if type(count) is not int or count <= 0 or type(remaining) is not int or not 0 <= remaining <= room:
        raise ResearchError('invalid_input', INVALID_CONDENSING_MESSAGE)
    base = 0 if scope == 'decisions' else context['notes'] + context['pool']
    turns = [dict(kind='combine', number=0, targets=list(targets), characters=base + room,
                  context=copy.deepcopy(context), parts=[], inputs=[], scope=scope, remaining=None)
             for _ in range(count)]
    turns[-1]['remaining'] = remaining
    return turns, remaining


def _pack_pages(context, targets, condense):
    room = context['page_room']
    base = context['notes'] + context['pool']
    available = room - base
    divide_room(room, [_build_fixed_part(base)])
    turns, pending = [], []
    pending_characters = base
    for target in targets:
        size = target['entries'] + target['rows']
        needs_rows = target['rows'] > room // 4
        if pending and (pending_characters + size > room or needs_rows):
            turns.append(_build_page('page', context, pending, pending))
            pending, pending_characters = [], base
        part = copy.deepcopy(target)
        reductions = []
        if needs_rows:
            rows, bound = _plan_condensing(part['rows'], min(room // 4, available), context,
                                           [part['handle']], 'rows', condense)
            reductions.extend(rows)
            part['rows'] = bound
        if part['entries'] + part['rows'] > available:
            entries, bound = _plan_condensing(part['entries'] + part['rows'], available, context,
                                              [part['handle']], 'entries', condense)
            reductions.extend(entries)
            part['entries'], part['rows'] = bound, 0
        if reductions:
            turns.extend(reductions)
            turns.append(_build_page('page', context, [part], [target]))
        else:
            pending.append(part)
            pending_characters += size
    if pending:
        turns.append(_build_page('page', context, pending, pending))
    return turns


def _plan_closing(context, targets, condense):
    characters = len(targets) * context['row_characters']
    turns = []
    if characters > context['closing_room']:
        turns, characters = _plan_condensing(characters, context['closing_room'], context, [], 'decisions', condense)
    turns.append(dict(kind='closing', number=0, targets=list(targets), characters=characters,
                      context=copy.deepcopy(context), parts=[], inputs=[], scope=None))
    return turns


def _number_turns(turns, start):
    return [dict(copy.deepcopy(turn), number=number) for number, turn in enumerate(turns, start)]


def split_final_turn(turns, number, *, condense=None):
    """Replace a page by ordered halves, or condense a singleton before retrying.

    The policy callback is needed only for singleton or closing-row condensation.
    Turn numbers are stable identities, not positions: new turns receive numbers
    above the predecessor maximum; all unaffected turns keep their bytes.
    """
    selected = next((turn for turn in turns if turn['number'] == number), None)
    if selected is None or selected['kind'] not in ('final', 'page') or not selected['targets']:
        raise ResearchError('invalid_input', INVALID_SPLIT_MESSAGE)
    context, parts, inputs = selected['context'], selected['parts'], selected['inputs']
    if len(parts) == 1:
        part = parts[0]
        size = part['entries'] + part['rows']
        room = min((context['page_room'] - context['notes'] - context['pool']) // 2, size // 2)
        replacement, bound = _plan_condensing(size, room, context, selected['targets'], 'entries', condense)
        replacement.append(_build_page('page', context, [dict(part, entries=bound, rows=0)], inputs))
    else:
        half = (len(parts) + 1) // 2
        replacement = [_build_page('page', context, parts[:half], inputs[:half]),
                       _build_page('page', context, parts[half:], inputs[half:])]
    if selected['kind'] == 'final':
        replacement.extend(_plan_closing(context, selected['targets'], condense))
    replacement = _number_turns(replacement, max(turn['number'] for turn in turns) + 1)
    result = []
    for turn in turns:
        result.extend(replacement if turn['number'] == number else [copy.deepcopy(turn)])
    return result


def repack_final_turns(turns, assigned, page_room, *, condense=None):
    """Repack unassigned targets at a new level; preserve assigned turn values.

    Assigned values are predecessor turn numbers, authenticated by the caller.
    An assigned page is a packing boundary, including when it is not a prefix.
    Each completed combine batch contributes its bound, including when a later
    split or another scope added a batch that has not yet been assigned.
    """
    assigned = list(assigned)
    selected = set(assigned)
    if len(selected) != len(assigned) or not selected <= {turn['number'] for turn in turns}:
        raise ResearchError('invalid_input', INVALID_ASSIGNED_MESSAGE)
    if len(selected) == len(turns):
        return copy.deepcopy(turns)
    context = dict(turns[0]['context'], page_room=page_room)
    completed = _collect_completed_bounds(turns, selected)
    targets = []
    for turn in turns:
        if turn['kind'] in ('final', 'page'):
            targets.extend(turn['targets'])
    result, pending = [], []
    for turn in turns:
        if turn['number'] in selected:
            result.extend(_pack_pages(context, pending, condense))
            pending = []
            result.append(copy.deepcopy(turn))
        elif turn['kind'] in ('final', 'page'):
            for original in turn['inputs']:
                pending.append(dict(copy.deepcopy(original), **completed.get(original['handle'], {})))
        elif turn['kind'] == 'closing' or turn['kind'] == 'combine' and turn['scope'] == 'decisions':
            # A level changes page room, never the pinned closing room or rows.
            # Keep that schedule and bound rather than repeating completed work.
            result.extend(_pack_pages(context, pending, condense))
            pending = []
            result.append(dict(copy.deepcopy(turn), number=0, context=copy.deepcopy(context)))
    result.extend(_pack_pages(context, pending, condense))
    if not any(turn['kind'] == 'closing' for turn in result):
        result.extend(_plan_closing(context, targets, condense))
    next_number = max(turn['number'] for turn in turns) + 1
    for turn in result:
        if turn['number'] == 0:
            turn['number'] = next_number
            next_number += 1
    return result


def _collect_completed_bounds(turns, assigned):
    completed = {}
    complete = True
    for turn in turns:
        if turn['kind'] != 'combine' or turn['scope'] == 'decisions':
            continue
        complete = complete and turn['number'] in assigned
        if turn['remaining'] is not None:
            if complete:
                for handle in turn['targets']:
                    sizes = completed.setdefault(handle, {})
                    if turn['scope'] == 'rows':
                        sizes['rows'] = turn['remaining']
                    else:
                        sizes.update(entries=turn['remaining'], rows=0)
            complete = True
    return completed


def count_final_turns(turns):
    """Count requests by work type; a one-request final counts as one page."""
    return dict(pages=sum(turn['kind'] in ('final', 'page') for turn in turns),
                condensing_turns=sum(turn['kind'] == 'combine' for turn in turns),
                closing_pages=sum(turn['kind'] == 'closing' for turn in turns))


def build_page_plan(chain, plan_digest, state_digest, turns, replaces=None, cause=None):
    """Build the JSON artifact hashed by evidence.digest; omit absent provenance."""
    if replaces is not None or cause is not None:
        _validate_replacement_cause(replaces, cause)
    result = dict(protocol=FINAL_PAGE_PLAN_PROTOCOL, chain=copy.deepcopy(chain), plan_digest=plan_digest,
                  state_digest=state_digest, turns=copy.deepcopy(turns))
    if replaces is not None:
        result.update(replaces=replaces, cause=copy.deepcopy(cause))
    return result


def _validate_replacement_cause(replaces, cause):
    valid = isinstance(replaces, str) and bool(replaces) and isinstance(cause, dict)
    if valid:
        valid = isinstance(cause.get('attempt_id'), str) and bool(cause['attempt_id'])
        kind = cause.get('kind')
        if kind == 'split':
            valid = valid and set(cause) == {'kind', 'turn', 'attempt_id'}
            valid = valid and type(cause.get('turn')) is int and cause['turn'] > 0
        elif kind == 'level':
            valid = valid and set(cause) == {'kind', 'attempt_id', 'page_room'}
            valid = valid and type(cause.get('page_room')) is int and cause['page_room'] > 0
        else:
            valid = False
    if not valid:
        raise ResearchError('invalid_input', INVALID_REPLACEMENT_MESSAGE)


def verify_page_plans(plans, first_turns, *, condense=None, assigned=None):
    """Authenticate canonical successors against size plans and external witnesses.

    ``first_turns`` is rebuilt by the caller from its pinned capacity plan and
    state. ``assigned`` maps each level cause's attempt_id to authenticated turn
    numbers. This cannot be inferred from sizes or trusted from a successor.
    The same pinned condensing callback used to plan is needed for reductions.
    """
    if not isinstance(plans, list) or not plans:
        raise ResearchError('artifact_corrupt', INVALID_PAGE_PLAN_MESSAGE)
    index = 0
    try:
        first = plans[0]
        expected = build_page_plan(first['chain'], first['plan_digest'], first['state_digest'], first_turns)
        if digest(first) != digest(expected):
            raise ValueError
        for index, successor in enumerate(plans[1:], 1):
            previous = plans[index - 1]
            cause = successor['cause']
            _validate_replacement_cause(successor['replaces'], cause)
            if cause['kind'] == 'split':
                turns = split_final_turn(previous['turns'], cause['turn'], condense=condense)
            else:
                if assigned is None or cause['attempt_id'] not in assigned:
                    raise ValueError
                turns = repack_final_turns(previous['turns'], assigned[cause['attempt_id']],
                                          cause['page_room'], condense=condense)
            expected = build_page_plan(first['chain'], first['plan_digest'], first['state_digest'], turns,
                                       replaces=digest(previous), cause=cause)
            if digest(successor) != digest(expected):
                raise ValueError
    except (KeyError, TypeError, ValueError, ResearchError) as error:
        raise ResearchError('artifact_corrupt', INVALID_PAGE_PLAN_MESSAGE, dict(index=index)) from error
    return True
