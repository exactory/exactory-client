"""Pure v3 read sizing: divisible content and separately charged card entries.

Only the stored plan supplies rates, reserves and levels. Unit rows are unique
text bodies with handle/characters and an optional ``late`` flag. A read cursor
is the returned ``next`` value; failed/unselected attempts consume no cursor.
"""
from fractions import Fraction

from .errors import ResearchError
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


def _describe_card_allowance(entry, rate, parts):
    content, late_content = 0, 0
    for part in parts:
        allocated = _allocate_content(rate, part['delivered_before'], part['delivered_through'])
        content += allocated
        if part.get('late', False):
            late_content += allocated
    entries = len(parts) * entry
    normal_entries = sum(not part.get('late', False) for part in parts) * entry
    return dict(content_characters=content, entry_characters=entries, characters=content + entries,
                card_budget=content - late_content, card_reserve=normal_entries,
                late_unit_reserve=late_content + entries - normal_entries)


def compute_card_allowance(plan, parts):
    """Return content, full entries and their three separate reserve charges.

    This arithmetic never clamps to the answer cap. size_read_turn chooses a
    fitting parts and card count. Each part states its series delivered_before
    and delivered_through; cached content allocations are not trusted. Parts
    marked late charge their content and full entry to the late reserve.
    """
    return _describe_card_allowance(plan['allocation']['entry_sizes']['card'], read_content_rate(plan),
                                    parts)


def _can_pay_card(plan, cursor, content, late):
    allocation = plan['allocation']
    entry = allocation['entry_sizes']['card']
    if late:
        return cursor['late_unit_reserve'] + content + entry <= allocation['late_unit_reserve']
    return (cursor['card_count'] < allocation['largest_card_count']
            and cursor['card_reserve'] + entry <= allocation['card_reserve']
            and cursor['card_budget'] + content <= allocation['card_budget'])


def _size_card(plan, rate, cursor, unit, material_left, answer_left, card_bound):
    """Use available room without making a nonfinal part smaller than the floor."""
    remaining = unit['characters'] - cursor['unit_offset']
    floor = _read_floor(plan)
    entry = plan['allocation']['entry_sizes']['card']
    ceiling = min(answer_left, card_bound)
    if remaining == 0:
        return 0 if entry <= ceiling and _can_pay_card(plan, cursor, 0, unit.get('late', False)) else None
    minimum = min(remaining, floor)
    if material_left < minimum:
        return None
    low, high = minimum - 1, min(remaining, material_left)
    while low < high:
        size = (low + high + 1) // 2
        content = _allocate_content(rate, cursor['delivered'], cursor['delivered'] + size)
        fits = (content + entry <= ceiling
                and _can_pay_card(plan, cursor, content, unit.get('late', False)))
        if fits:
            low = size
        else:
            high = size - 1
    return None if low < minimum else low


def size_read_turn(plan, level, inventory, *, position=None):
    """Size one read under its pinned material/answer bounds and reserved entries.

    inventory is the ordered unique inventory. position is the previous next
    cursor, or None at the start. Return None at its end. Every part has at least
    the lowest read level in characters except its unit's last part. Thus the
    number of cards cannot exceed the stored sum of ceil(unit size / floor),
    even after repeated division.
    Late units are appended and charged entirely to late_unit_reserve.
    """
    if type(level) is not int or level <= 0:
        raise ResearchError('invalid_input', 'The read material allowance must be a positive integer.')
    bounds = describe_card_bounds(plan, level)
    turn_bound = bounds['turn_characters']
    rate = read_content_rate(plan)
    cursor = dict(unit_index=0, unit_offset=0, delivered=0, card_count=0,
                  card_budget=0, card_reserve=0, late_unit_reserve=0)
    cursor.update(position or {})
    if cursor['unit_index'] == len(inventory):
        return None
    before = cursor['delivered']
    cards, material, answer = [], 0, 0
    entry = plan['allocation']['entry_sizes']['card']
    while cursor['unit_index'] < len(inventory):
        unit = inventory[cursor['unit_index']]
        size = _size_card(plan, rate, cursor, unit, level - material, turn_bound - answer,
                          bounds['card_characters'])
        if size is None:
            if not cards:
                raise ResearchError('invalid_input', READ_TURN_CANNOT_FIT_MESSAGE,
                                    dict(unit=unit['handle'], level=level, position=cursor))
            break
        start = cursor['unit_offset']
        through = cursor['delivered'] + size
        content = _allocate_content(rate, cursor['delivered'], through)
        late = unit.get('late', False)
        cards.append(dict(handle='c/' + unit['handle'] + '/' + str(start), unit=unit['handle'],
                          start=start, end=start + size, unit_characters=size, late=late,
                          delivered_before=cursor['delivered'], delivered_through=through,
                          content_characters=content, entry_characters=entry, characters=content + entry))
        if late:
            cursor['late_unit_reserve'] += content + entry
        else:
            cursor['card_count'] += 1
            cursor['card_budget'] += content
            cursor['card_reserve'] += entry
        material += size
        answer += content + entry
        cursor['delivered'] = through
        cursor['unit_offset'] += size
        if cursor['unit_offset'] == unit['characters']:
            cursor['unit_index'] += 1
            cursor['unit_offset'] = 0
    return dict(delivered_before=before, delivered_through=cursor['delivered'], unit_characters=material,
                cards=cards, allowance=_describe_card_allowance(entry, rate, cards),
                next=cursor)


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
