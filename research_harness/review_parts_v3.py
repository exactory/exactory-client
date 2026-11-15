"""Pure v3 read sizing: balanced groups, divisible content and reserved entries.

Only the stored plan supplies rates, reserves and levels. Unit rows are unique
text bodies with handle/characters and an optional ``late`` flag. A read cursor
is the returned ``next`` value; failed/unselected attempts consume no cursor.
"""
from fractions import Fraction

from .errors import ResearchError
from .evidence import digest
from .review_parts import CARD_CHARACTERS
from .review_condensing import cut_condensing_blocks, plan_condensing
from .review_route_capacity import BELOW_FLOOR_MESSAGE


CARD_CONDENSING_RATE = Fraction(20, 1000)
READ_TURN_CANNOT_FIT_MESSAGE = (
    'The next read segment and its full card entry do not fit the read level, the card ceiling or the '
    'remaining reserve. Use a fitting stored level or provide enough reserve before reading this unit.'
)


def _read_floor(plan):
    return min(row['material_allowance'] for row in plan['task']['read']['levels'])


def _read_block_room(plan):
    return min(plan['allocation']['rooms']['combine'], plan['task']['combine']['material_allowance'])


def _read_turn_bound(plan, level):
    task = plan['task']['read']
    row = next((row for row in task['levels'] if row['material_allowance'] == level), None)
    if row is None:
        raise ResearchError('invalid_input', 'Select a read material level from the stored plan.')
    if level == task['material_allowance']:
        answer = task['answer_bound']
    elif 'answer_bound' in row:
        answer = row['answer_bound']
    else:
        answer = (min(row['default_answer_bound'], row['answer_room_characters']) if row['qualifies']
                  else row['smallest_answer_bound'])
    return min(CARD_CHARACTERS, answer)


def describe_card_bounds(plan, level):
    """Return complete turn/card bounds from the pinned read and combine levels."""
    turn = _read_turn_bound(plan, level)
    combine = min(row['material_allowance'] for row in plan['task']['combine']['levels'])
    return dict(turn_characters=turn, card_characters=min(turn, combine))


def read_content_rate(plan):
    """Bound the pinned rate by one full floor card at every reachable level.

    Each level pins an answer bound. Its turn and card bounds must leave room
    beside one entry. Unused content budget is never moved into another reserve.
    """
    allocation = plan['allocation']
    entry = allocation['entry_sizes']['card']
    task = plan['task']['read']
    ceiling = task.get('adaptation', {}).get('ceiling', max(row['material_allowance'] for row in task['levels']))
    rate = Fraction(str(allocation['card_rate']))
    for row in task['levels']:
        level = row['material_allowance']
        if level > ceiling:
            continue
        bound = describe_card_bounds(plan, level)['card_characters']
        if bound <= entry:
            raise ResearchError('review_route_capacity_below_floor', BELOW_FLOOR_MESSAGE,
                                dict(kind='read', level=level, room=bound, minimum=entry + 1,
                                     command='review-route'))
        rate = min(rate, Fraction(bound - entry, _read_floor(plan)))
    return rate


def _allocate_content(rate, delivered_before, delivered_through):
    before, through = rate * delivered_before, rate * delivered_through
    return through.numerator // through.denominator - before.numerator // before.denominator


def compute_content_allocation(plan, delivered_before, delivered_through):
    """Allocate an integer interval; adjacent intervals telescope without loss."""
    return _allocate_content(read_content_rate(plan), delivered_before, delivered_through)


def _describe_card_allowance(entry, rate, groups):
    content, late_content = 0, 0
    for part in groups:
        allocated = _allocate_content(rate, part['delivered_before'], part['delivered_through'])
        content += allocated
        if part.get('late', False):
            late_content += allocated
    entries = len(groups) * entry
    normal_entries = sum(not part.get('late', False) for part in groups) * entry
    return dict(content_characters=content, entry_characters=entries, characters=content + entries,
                card_budget=content - late_content, card_reserve=normal_entries,
                late_unit_reserve=late_content + entries - normal_entries)


def compute_card_allowance(plan, groups):
    """Return content, full entries and their three separate reserve charges.

    This arithmetic never clamps to the answer cap. Each group states its series
    delivered_before and delivered_through; cached allocations are not trusted.
    Late groups charge their content and full entry to the late reserve.
    """
    return _describe_card_allowance(plan['allocation']['entry_sizes']['card'], read_content_rate(plan),
                                    groups)


def _describe_read_window(inventory, cursor, level):
    """Measure the available prefix and its initial/late ownership boundary."""
    index, offset = cursor['unit_index'], cursor['unit_offset']
    late = inventory[index].get('late', False)
    characters, boundary = 0, None
    while index < len(inventory):
        unit = inventory[index]
        if unit.get('late', False) != late and boundary is None:
            boundary = characters
        characters += min(unit['characters'] - offset, level - characters)
        index, offset = index + 1, 0
        if characters == level:
            break
    return characters, boundary, late


def _limit_content_characters(rate, before, maximum, content_bound):
    """Invert the integer interval allocation, including its rounding phase."""
    if content_bound < 0:
        return -1
    if rate == 0:
        return maximum
    start = _allocate_content(rate, 0, before)
    threshold = Fraction(start + content_bound + 1) / rate
    last = (threshold.numerator + threshold.denominator - 1) // threshold.denominator - 1
    return min(maximum, last - before)


def _balance_groups(rate, before, characters, count, card_bound, entry, boundary=None, initial_count=None):
    """Balance lengths exactly; a small DP resolves rounding and ownership."""
    width, long_count = divmod(characters, count)
    if boundary is None:
        lengths = [width + 1] * long_count + [width] * (count - long_count)
    else:
        initial_long = boundary - initial_count * width
        later_long = long_count - initial_long
        if not 0 <= initial_long <= initial_count or not 0 <= later_long <= count - initial_count:
            return None
        lengths = ([width + 1] * initial_long + [width] * (initial_count - initial_long) +
                   [width + 1] * later_long + [width] * (count - initial_count - later_long))
    at = before
    for length in lengths:
        if _allocate_content(rate, at, at + length) + entry > card_bound:
            break
        at += length
    else:
        return lengths
    # At (i,j), i groups and j longer groups have consumed i*width+j characters.
    layers = [{0: None}]
    for index in range(count):
        following = {}
        for used_long in layers[-1]:
            start = index * width + used_long
            for extra in (0, 1):
                next_long = used_long + extra
                if next_long > long_count or long_count - next_long > count - index - 1:
                    continue
                end = start + width + extra
                if boundary is not None:
                    if index < initial_count and end > boundary or index >= initial_count and start < boundary:
                        continue
                    if index + 1 == initial_count and end != boundary:
                        continue
                if _allocate_content(rate, before + start, before + end) + entry <= card_bound:
                    following.setdefault(next_long, (used_long, width + extra))
        if not following:
            return None
        layers.append(following)
    if long_count not in layers[-1]:
        return None
    result, used_long = [], long_count
    for layer in reversed(layers[1:]):
        used_long, length = layer[used_long]
        result.append(length)
    return list(reversed(result))


def _choose_groups(plan, rate, cursor, maximum, boundary, late, bounds):
    """Maximize material first and card count second, using only reserved room."""
    allocation = plan['allocation']
    entry, floor = allocation['entry_sizes']['card'], _read_floor(plan)
    turn_bound, card_bound = bounds['turn_characters'], bounds['card_characters']
    normal_slots = min(allocation['largest_card_count'] - cursor['card_count'],
                       (allocation['card_reserve'] - cursor['card_reserve']) // entry)
    normal_content = allocation['card_budget'] - cursor['card_budget']
    late_room = allocation['late_unit_reserve'] - cursor['late_unit_reserve']
    before = cursor['delivered']
    # A homogeneous prefix also covers the short final tail of an ownership epoch.
    limit = maximum if boundary is None else boundary
    slots = late_room // entry if late else normal_slots
    upper = min(max(1, limit // floor), turn_bound // entry, slots)
    best = None
    for count in range(upper, 0, -1):
        budget = min(turn_bound - count * entry, count * (card_bound - entry),
                     late_room - count * entry if late else normal_content)
        material = _limit_content_characters(rate, before, limit, budget)
        minimum = count * floor if count > 1 else min(floor, limit)
        while material >= minimum:
            lengths = _balance_groups(rate, before, material, count, card_bound, entry)
            if lengths is not None:
                if best is None or (material, count) > best[:2]:
                    best = (material, count, lengths)
                break
            material -= 1
        if best is not None and best[0] == limit:
            break
    if boundary is None or late or boundary == 0:
        return best
    # A crossing layout has h initial groups. Its balanced base width is one of
    # floor(boundary/h) and floor((boundary-1)/h); only <=k late extras remain.
    initial_content = _allocate_content(rate, before, before + boundary)
    upper = min(maximum // floor, turn_bound // entry)
    for count in range(upper, 1, -1):
        for initial_count in range(1, min(count - 1, normal_slots) + 1):
            if initial_content > normal_content:
                continue
            for width in sorted({boundary // initial_count, (boundary - 1) // initial_count}, reverse=True):
                if width < floor:
                    continue
                initial_long = boundary - initial_count * width
                if not 0 <= initial_long <= initial_count:
                    continue
                for later_long in range(count - initial_count, -1, -1):
                    long_count = initial_long + later_long
                    if long_count >= count:
                        continue
                    material = count * width + long_count
                    if material > maximum or best is not None and (material, count) <= best[:2]:
                        continue
                    content = _allocate_content(rate, before, before + material)
                    if content + count * entry > turn_bound:
                        continue
                    if content - initial_content + (count - initial_count) * entry > late_room:
                        continue
                    lengths = _balance_groups(rate, before, material, count, card_bound, entry,
                                               boundary, initial_count)
                    if lengths is not None:
                        best = (material, count, lengths)
        if best is not None and best[:2] == (maximum, count):
            break
    return best


def _advance_group(inventory, cursor, characters):
    """Return compact endpoints and the next cursor, retaining zero-size units."""
    index, offset = cursor['unit_index'], cursor['unit_offset']
    first_index = index
    first = dict(unit=inventory[index]['handle'], offset=offset)
    late = inventory[index].get('late', False)
    remaining = characters
    last = first
    last_index = index
    while index < len(inventory) and inventory[index].get('late', False) == late:
        unit = inventory[index]
        available = unit['characters'] - offset
        if remaining == 0 and available > 0:
            break
        take = min(available, remaining)
        last, last_index = dict(unit=unit['handle'], offset=offset + take), index
        remaining -= take
        if take == available:
            index, offset = index + 1, 0
        else:
            offset += take
            break
    return dict(first=first, last=last, unit_count=last_index - first_index + 1, late=late), \
        dict(unit_index=index, unit_offset=offset)


def size_read_turn(plan, level, inventory, *, position=None):
    """Return maximal material in balanced, separately funded groups.

    cards are group records, with constant-size first/last endpoints instead of
    a list of unit handles. Last offsets are exclusive. position is the previous
    next cursor. Initial units precede late units; a final ownership-epoch tail
    may be shorter than the floor when it cannot share a balanced crossing turn.
    """
    if type(level) is not int or level <= 0:
        raise ResearchError('invalid_input', 'The read material allowance must be a positive integer.')
    bounds, rate = describe_card_bounds(plan, level), read_content_rate(plan)
    cursor = dict(unit_index=0, unit_offset=0, delivered=0, card_count=0,
                  card_budget=0, card_reserve=0, late_unit_reserve=0)
    cursor.update(position or {})
    if cursor['unit_index'] == len(inventory):
        return None
    maximum, boundary, late = _describe_read_window(inventory, cursor, level)
    chosen = _choose_groups(plan, rate, cursor, maximum, boundary, late, bounds)
    if chosen is None:
        raise ResearchError('invalid_input', READ_TURN_CANNOT_FIT_MESSAGE,
                            dict(unit=inventory[cursor['unit_index']]['handle'], level=level, position=cursor))
    before, cards = cursor['delivered'], []
    entry = plan['allocation']['entry_sizes']['card']
    for length in chosen[2]:
        metadata, following = _advance_group(inventory, cursor, length)
        start, through = cursor['delivered'], cursor['delivered'] + length
        content = _allocate_content(rate, start, through)
        identity = dict(metadata, delivered_before=start, delivered_through=through)
        cards.append(dict(identity, handle='c' + digest(identity), unit_characters=length,
                          content_characters=content, entry_characters=entry, characters=content + entry))
        cursor.update(following)
        cursor['delivered'] = through
    allowance = _describe_card_allowance(entry, rate, cards)
    cursor['card_count'] += sum(not group['late'] for group in cards)
    for reserve in ('card_budget', 'card_reserve', 'late_unit_reserve'):
        cursor[reserve] += allowance[reserve]
    return dict(delivered_before=before, delivered_through=cursor['delivered'], unit_characters=chosen[0],
                cards=cards, allowance=allowance, next=cursor)


def plan_read_turns(plan, inventory, *, material_allowance=None):
    """Size every read once, deduplicating body identities before cursor traversal."""
    unique = {}
    for unit in inventory:
        unique.setdefault(unit.get('sha256', unit['handle']), unit)
    units = list(unique.values())
    level = plan['task']['read']['material_allowance'] if material_allowance is None else material_allowance
    turns, position = [], None
    while True:
        turn = size_read_turn(plan, level, units, position=position)
        if turn is None:
            return turns
        turns.append(turn)
        position = turn['next']


def plan_card_condensing(plan, read_turns):
    """Schedule entry-aware combines after their last one-based read turn.

    Later rounds describe answers at their quarter bounds. A chain remeasures
    actual answers and rebuilds those blocks before assignment if an earlier
    answer used less, including a lower turn answer cap. It stores the resulting
    measured block artifacts, not an unmeasured future block.
    """
    if read_content_rate(plan) >= CARD_CONDENSING_RATE:
        return []
    cards = [card for turn in read_turns for card in turn['cards']]
    last_reads = {card['handle']: index for index, turn in enumerate(read_turns, 1) for card in turn['cards']}
    result = plan_condensing(cards, _read_block_room(plan))
    events = []
    for round_index, blocks in enumerate(result['rounds'], 1):
        for block in blocks:
            after = max(last_reads[handle] for handle in block['entries'])
            last_reads[block['handle']] = after
            events.append(dict(after_read_turn=after, round=round_index, block=block))
    return sorted(events, key=lambda event: (event['after_read_turn'], event['round']))


def plan_target_condensing(plan, cards, page_room):
    """Return initial whole-card blocks if this target exceeds its page room."""
    if sum(card['characters'] for card in cards) <= page_room:
        return []
    return cut_condensing_blocks(cards, min(page_room, _read_block_room(plan)))


def count_series_turns(plan, inventory):
    """Return sized reads and a marked forecast at every granted card bound.

    The forecast uses entry sizes, not the total-only forecast_condensing helper.
    It is advisory: actual whole answers may use less than their allocations.
    Final/check turns themselves are outside these counts.
    """
    result = {}
    for name, level in (('start', plan['task']['read']['material_allowance']), ('lowest', _read_floor(plan))):
        turns = plan_read_turns(plan, inventory, material_allowance=level)
        cards = [card for turn in turns for card in turn['cards']]
        condensed = plan_condensing(cards, _read_block_room(plan))
        result[name] = dict(material_allowance=level, read_turns=len(turns), reads=turns,
                            condensing=dict(forecast=True, turns=condensed['turns'],
                                remaining_characters=condensed['remaining_characters']),
                            turns_upper_bound=len(turns) + condensed['turns'])
    return result
