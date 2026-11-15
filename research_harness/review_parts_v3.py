"""Pure v3 read sizing: balanced groups, divisible content and reserved entries.

Only the stored plan supplies rates, reserves and levels. Unit rows state their
delivery characters: the descriptor row and text that they add to a request.
The caller measures them with the real packet builder; a reference-row segment
is a unit of its own. This module counts those sizes, reads no text and builds
no request. Rows have handle/characters and an optional ``late`` flag. A read
cursor is the returned ``next`` value; unselected attempts consume no cursor.
Late units from multiple additions may share a group and a turn. A series takes
at most ceil(initial_characters / level) plus the sum of
ceil(addition_characters / level) over additions, using the lowest read level
when levels change.
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
    """Bound the pinned rate by one card of a whole reachable read level.

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
        rate = min(rate, Fraction(bound - entry, level))
    return rate


def _allocate_content(rate, delivered_before, delivered_through):
    before, through = rate * delivered_before, rate * delivered_through
    return through.numerator // through.denominator - before.numerator // before.denominator


def compute_content_allocation(plan, delivered_before, delivered_through):
    """Allocate an integer interval; adjacent intervals telescope without loss."""
    return _allocate_content(read_content_rate(plan), delivered_before, delivered_through)


def _charge_late_card(entry, content, remaining):
    """Grant full content with its entry, an entry alone, or an unfunded entry."""
    if content + entry <= remaining:
        return content, content + entry, 0
    if entry <= remaining:
        return 0, entry, 0
    return 0, 0, entry


def _describe_card_allowance(entry, rate, groups, late_limit):
    content, normal_content, late_paid, beyond, spent = 0, 0, 0, 0, 0
    for group in groups:
        allocated = _allocate_content(rate, group['delivered_before'], group['delivered_through'])
        if group.get('late', False):
            spent = group.get('late_reserve_before', spent)
            allocated, paid, overflow = _charge_late_card(entry, allocated, late_limit - spent)
            spent += paid
            late_paid += paid
            beyond += overflow
        else:
            normal_content += allocated
        content += allocated
    entries = len(groups) * entry
    normal_entries = sum(not part.get('late', False) for part in groups) * entry
    return dict(content_characters=content, entry_characters=entries, characters=content + entries,
                card_budget=normal_content, card_reserve=normal_entries,
                late_unit_reserve=late_paid, late_characters_beyond_reserve=beyond)


def compute_card_allowance(plan, groups):
    """Return content, full entries and their three separate reserve charges.

    This arithmetic never clamps to the answer cap. Each group states its series
    delivered_before and delivered_through; cached allocations are not trusted.
    Late groups state late_reserve_before, the selected cursor's prior late
    spend. This lets a subset replay its grants without trusting cached content.
    The additional content allocation is zero when it and the entry cannot both
    be paid. late_unit_reserve reports only the amount paid, bounded by the
    stored late reserve. late_characters_beyond_reserve reports unfunded entries.
    Both are separate from card_budget and card_reserve.
    """
    return _describe_card_allowance(plan['allocation']['entry_sizes']['card'], read_content_rate(plan),
                                    groups, plan['allocation']['late_unit_reserve'])


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


def _balance_groups(rate, before, characters, count, bounds, entry, late_room,
                    initial_count, boundary=None):
    """Balance lengths; resolve rounding, ownership and late funding together."""
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
    at, answer, spent = before, 0, 0
    for index, length in enumerate(lengths):
        content = _allocate_content(rate, at, at + length)
        if index >= initial_count:
            content, paid, _ = _charge_late_card(entry, content, late_room - spent)
            spent += paid
        answer += content + entry
        if content + entry > bounds['card_characters'] or answer > bounds['turn_characters']:
            break
        at += length
    else:
        return lengths
    # At (i,j), i groups and j longer groups have consumed i*width+j characters.
    # Equal endpoints and late spend have identical futures: retain the least
    # answer cost at each such state, together with its reconstruction path.
    layers = [{(0, 0): (0, None, None)}]
    for index in range(count):
        following = {}
        for key, (answer, _, _) in layers[-1].items():
            used_long, spent = key
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
                content = _allocate_content(rate, before + start, before + end)
                paid = 0
                if index >= initial_count:
                    content, paid, _ = _charge_late_card(entry, content, late_room - spent)
                total = answer + content + entry
                next_key = next_long, spent + paid
                if content + entry > bounds['card_characters'] or total > bounds['turn_characters']:
                    continue
                if next_key not in following or total < following[next_key][0]:
                    following[next_key] = total, key, width + extra
        if not following:
            return None
        layers.append(following)
    endings = [key for key in layers[-1] if key[0] == long_count]
    if not endings:
        return None
    result, key = [], min(endings, key=lambda key: layers[-1][key][0])
    for layer in reversed(layers[1:]):
        _, key, length = layer[key]
        result.append(length)
    return list(reversed(result))


def _choose_groups(plan, rate, cursor, maximum, boundary, late, bounds):
    """Maximize material first and card count second with separate funding."""
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
    if late:
        # At reachable levels the first material candidate fits in one card.
        # Try all card counts at that material before considering a shorter read.
        for material in range(limit, min(floor, limit) - 1, -1):
            upper = min(max(1, material // floor), turn_bound // entry)
            for count in range(upper, 0, -1):
                lengths = _balance_groups(rate, before, material, count, bounds, entry, late_room, 0)
                if lengths is not None:
                    return material, count, lengths
        return None
    upper = min(max(1, limit // floor), turn_bound // entry, normal_slots)
    best = None
    for count in range(upper, 0, -1):
        budget = min(turn_bound - count * entry, count * (card_bound - entry), normal_content)
        material = _limit_content_characters(rate, before, limit, budget)
        minimum = count * floor if count > 1 else min(floor, limit)
        while material >= minimum:
            lengths = _balance_groups(rate, before, material, count, bounds, entry, late_room,
                                      count)
            if lengths is not None:
                if best is None or (material, count) > best[:2]:
                    best = (material, count, lengths)
                break
            material -= 1
        if best is not None and best[0] == limit:
            break
    if boundary is None or boundary == 0:
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
                    lengths = _balance_groups(rate, before, material, count, bounds, entry, late_room,
                                               initial_count, boundary)
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
    Only initial cards count in next.card_count. next.late_unit_reserve accumulates
    the paid late charge within the plan's reserve. Unfunded late entries
    accumulate in next.late_characters_beyond_reserve; they never stop a read turn.
    """
    if type(level) is not int or level <= 0:
        raise ResearchError('invalid_input', 'The read material allowance must be a positive integer.')
    bounds, rate = describe_card_bounds(plan, level), read_content_rate(plan)
    cursor = dict(unit_index=0, unit_offset=0, delivered=0, card_count=0,
                  card_budget=0, card_reserve=0, late_unit_reserve=0, late_characters_beyond_reserve=0)
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
    late_limit, late_spent = plan['allocation']['late_unit_reserve'], cursor['late_unit_reserve']
    for length in chosen[2]:
        metadata, following = _advance_group(inventory, cursor, length)
        start, through = cursor['delivered'], cursor['delivered'] + length
        content = _allocate_content(rate, start, through)
        identity = dict(metadata, delivered_before=start, delivered_through=through)
        if metadata['late']:
            identity['late_reserve_before'] = late_spent
            content, paid, _ = _charge_late_card(entry, content, late_limit - late_spent)
            late_spent += paid
        cards.append(dict(identity, handle='c' + digest(identity), unit_characters=length,
                          content_characters=content, entry_characters=entry, characters=content + entry))
        cursor.update(following)
        cursor['delivered'] = through
    allowance = _describe_card_allowance(entry, rate, cards, late_limit)
    cursor['card_count'] += sum(not group['late'] for group in cards)
    for reserve in ('card_budget', 'card_reserve', 'late_unit_reserve', 'late_characters_beyond_reserve'):
        cursor[reserve] += allowance[reserve]
    return dict(delivered_before=before, delivered_through=cursor['delivered'], unit_characters=chosen[0],
                cards=cards, allowance=allowance, next=cursor)


def plan_read_turns(plan, inventory, *, material_allowance=None):
    """Size every read once, deduplicating unit identities before traversal."""
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
    Low pinned rates carry all cards; otherwise only unfunded late entries do.
    """
    low_rate = Fraction(str(plan['allocation']['card_rate'])) < CARD_CONDENSING_RATE
    cards = [card for turn in read_turns for card in turn['cards']
             if low_rate or card.get('late', False) and
             card['late_reserve_before'] + card['entry_characters'] > plan['allocation']['late_unit_reserve']]
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
