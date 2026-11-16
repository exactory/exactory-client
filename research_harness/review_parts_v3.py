"""Parts v3 sizing and authenticated series with a pinned inventory and plan.

Only the stored plan supplies rates, reserves and levels. Unit rows state their
delivery characters: the descriptor row and text that they add to a request.
The series builder measures them with the real route; a reference-row segment
is a unit of its own. The pure sizing functions consume handle/characters rows
and an optional ``late`` flag, independently of artifact I/O. A read
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


# Series bindings and request fields belong to v3; v2's stored shapes stay intact.
import bisect
import copy
import hashlib
import json

from . import review_parts as legacy, review_route_capacity as capacity
from . import review_field_classes as field_classes
from .artifacts import describe_artifact

PROTOCOL = 'exactory-review-parts-v3'
SERIES_MARKER = 'review_parts_protocol'
MATERIAL_KEY = 'review_parts_material'
CONTEXT_KEY = 'review_parts_context'
GROUPS_KEY = 'review_parts_groups'
UNIT_ROW_CHARACTERS = field_classes.REFERENCE_ROWS_MAXIMUM_CHARACTERS
CARD_GROUP_FIELDS_CHARACTERS = 900
GROUP_TABLE_CHARACTERS = 36 * CARD_GROUP_FIELDS_CHARACTERS + 2
PROTOCOL_FRAME_CHARACTERS = 18000
GROUP_INVALID_MESSAGE = 'Return exactly one card for each assigned group, with its unchanged boundaries and unit count.'
READ_ANSWER_LIMIT_MESSAGE = 'The complete cards of a read answer must fit its answer bound and the 80,000-character card ceiling.'
DELIVERY_INVALID_MESSAGE = 'The material of this turn differs from its inventory interval or its stored plan.'


def _put_value(artifacts, value):
    return artifacts.put(legacy.render_json(value).encode('utf-8'), 'application/json')


def _read_value(artifacts, reference, expected=None):
    from .review_protocol import _read_json
    value = _read_json(artifacts, reference)
    def check_digest():
        legacy._require(expected is None or digest(value) == expected, DELIVERY_INVALID_MESSAGE,
                        'review_parts_delivery_invalid')
        return True
    if hasattr(artifacts, 'once'):
        artifacts.once(('parts_v3_value_digest', reference['sha256'], expected), check_digest)
    else:
        check_digest()
    return value


def _find_path(value, path):
    for component in path:
        value = value[component]
    return value


def _describe_text_charge(route, text, widths=None, empty=None):
    empty = capacity.measure_capacity_value(route, '') if empty is None else empty
    widths = {} if widths is None else widths
    for character in set(text):
        if character not in widths:
            widths[character] = capacity.measure_capacity_value(route, character) - empty
    # Persist run endpoints rather than a row for every character. Plain text is
    # one run; JSON quotes and backslashes introduce only their actual runs.
    runs, at, last = [], 0, None
    for index, character in enumerate(text):
        width = widths[character]
        if width != last:
            runs.append([index, at, width])
            last = width
        at += width
    return dict(runs=runs, characters=at, envelope=empty + 1)


def _describe_reference_units(route, owner, remaining):
    result, selected = [], []

    def describe(rows):
        return dict(handle=owner + '-r' + str(len(result) + 1), kind='reference', owner=owner,
                    digest=digest(rows), references=dict(count=len(rows), rows=rows))

    def append(rows, text=''):
        descriptor = describe(rows)
        result.append(dict(handle=descriptor['handle'], kind='reference', digest=descriptor['digest'],
                           descriptor=descriptor, text=text, required=True, occurrences=[], citations=[],
                           bodies=[], components=[]))

    for row in remaining:
        if capacity.measure_capacity_value(route, describe(selected + [row])) <= UNIT_ROW_CHARACTERS:
            selected.append(row)
            continue
        if selected:
            append(selected)
            selected = []
        if capacity.measure_capacity_value(route, describe([row])) > UNIT_ROW_CHARACTERS:
            # A single unbounded reference is text, so ordinary interval slicing applies.
            text = legacy.render_json(row)
            append([], text)
            result[-1]['descriptor']['reference_count'] = 1
            result[-1]['digest'] = result[-1]['descriptor']['digest'] = digest(row)
        else:
            selected = [row]
    if selected:
        append(selected)
    return result


def _describe_inventory_unit(route, source, kind='record', required=True):
    row = legacy._describe_unit(source)
    handle = row['handle']
    core = dict(handle=handle, digest=row['digest'], kind=kind)
    if row.get('carded_under'):
        core['carded_under'] = row['carded_under']
    if row.get('record_identity'):
        core['identity'] = row['record_identity']
    reference_room = UNIT_ROW_CHARACTERS - capacity.measure_capacity_value(route, core) - 32
    references = field_classes.describe_unit_references(row, reference_room, route=route)
    for reference in references['rows'] + references['remaining']:
        reference['path'] = list(reference['path'])
    descriptor = dict(core, references={key: references[key] for key in ('count', 'rows')})
    legacy._require(capacity.measure_capacity_value(route, descriptor) <= UNIT_ROW_CHARACTERS,
                    DELIVERY_INVALID_MESSAGE, 'review_parts_inventory_too_large')
    text = source['text']
    components = [dict(kind='item', start=0, end=len(text))]
    for body in source['bodies']:
        start = len(text) + 1
        text += '\n' + body['content']
        components.append(dict(kind='body', start=start, end=len(text), artifact=copy.deepcopy(body['artifact']),
                               encoding=body['encoding']))
    row.update(kind=kind, required=required, text=text, descriptor=descriptor, components=components)
    return [row] + _describe_reference_units(route, handle, references['remaining'])


def build_series_inventory(route, packet, adapter, *, records=None, artifacts=None, previous=None,
                           bounded_fields=None):
    """Freeze whole reference lists, bounded rows and their actual delivery cost."""
    units = legacy.enumerate_units(packet, adapter, protocol=PROTOCOL)
    base_frame = legacy.build_frame(packet, units)
    bounded_fields = bounded_fields or field_classes.describe_role_frame(route, packet['role'], base_frame, ['read'])['read']
    classified = field_classes.classify_packet(route, packet['role'], base_frame, bounded_fields=bounded_fields)
    paths = {'f-' + digest(list(row['path'])): row['path'] for row in classified['fields']}
    identities = {unit['digest'] for unit in units}
    for entry in field_classes.list_frame_units(route, packet['role'], base_frame, bounded_fields):
        if entry['sha256'] in identities:
            continue
        path = paths[entry['handle']]
        item = copy.deepcopy(_find_path(base_frame, path))
        units.append(dict(handle='u' + str(len(units) + 1), digest=digest(item), item=item,
                          text=legacy.render_json(item), occurrences=[dict(path=list(path[:-1]),
                          **({'index': path[-1]} if isinstance(path[-1], int) else {'key': path[-1]}))],
                          citations=[], bodies=[], item_characters=len(legacy.render_json(item)),
                          characters=entry['characters'], frame_path=list(path)))
        identities.add(entry['sha256'])
    rows = []
    for unit in units:
        additions = _describe_inventory_unit(route, unit, 'frame' if 'frame_path' in unit else 'record')
        if 'frame_path' in unit:
            additions[0]['frame_path'] = unit['frame_path']
        rows.extend(additions)
    if artifacts is not None and records is not None:
        from .review_parts_roles import get_adapter
        if get_adapter(adapter).get('source_requests'):
            from .review_session_v4 import build_session_inventory
            optional = build_session_inventory(records, artifacts, {})
            for item in optional['inventory']['units']:
                content = optional['bodies'][item['sha256']].decode('utf-8')
                handle = 'u' + str(len(units) + 1)
                units.append(item)
                descriptor = dict(handle=handle, digest=item['sha256'], kind='source',
                                  identity=item['identity'], references=dict(count=0, rows=[]))
                rows.append(dict(handle=handle, digest=item['sha256'], kind='source', required=False,
                    text=content, descriptor=descriptor, occurrences=[], citations=[], bodies=[],
                    components=[dict(kind='source', start=0, end=len(content), artifact=item['artifact'])],
                    source=copy.deepcopy(item)))
    if previous is not None:
        old_records = [row for row in previous['units'] if row['required'] and row['kind'] != 'reference']
        new_records = [row for row in rows if row['required'] and row['kind'] != 'reference']
        by_identity = {legacy._find_unit_identity(row): row for row in new_records}
        retained = copy.deepcopy(previous['units'])
        retained_by_handle = {row['handle']: row for row in retained}
        for old in old_records:
            current = by_identity.get(legacy._find_unit_identity(old))
            legacy._require(current is not None and current['text'] == old['text'],
                            'An earlier unit disappeared or changed', 'review_parts_inventory_changed')
            old_refs = field_classes.describe_unit_references(old, 0, route=route)['remaining']
            new_refs = field_classes.describe_unit_references(current, 0, route=route)['remaining']
            for reference in old_refs + new_refs:
                reference['path'] = list(reference['path'])
            known = {digest(row) for row in old_refs}
            legacy._require(known <= {digest(row) for row in new_refs},
                            'An earlier reference row disappeared', 'review_parts_inventory_changed')
            additions = [row for row in new_refs if digest(row) not in known]
            target = retained_by_handle[old['handle']]
            for name in ('occurrences', 'citations', 'bodies'):
                target[name] = copy.deepcopy(current[name])
            if additions:
                target['reference_count'] = len(new_refs)
                offset = sum(row['kind'] == 'reference' and row['descriptor']['owner'] == old['handle'] for row in retained)
                extra = _describe_reference_units(route, old['handle'], additions)
                for index, row in enumerate(extra, offset + 1):
                    row['handle'] = old['handle'] + '-r' + str(index)
                    row['descriptor']['handle'] = row['handle']
                    row['late'] = True
                retained.extend(extra)
        known = {legacy._find_unit_identity(row) for row in old_records}
        for source in units:
            if 'item' not in source or legacy._find_unit_identity(source) in known:
                continue
            source = dict(source, handle='u' + str(len([r for r in retained if r['kind'] != 'reference']) + 1))
            additions = _describe_inventory_unit(route, source, 'frame' if 'frame_path' in source else 'record')
            if 'frame_path' in source:
                additions[0]['frame_path'] = source['frame_path']
            for row in additions:
                row['late'] = True
            retained.extend(additions)
        rows = retained
    text_widths = {}
    text_envelope = capacity.measure_capacity_value(route, '')
    for row in rows:
        row.setdefault('item_characters', 0)
        if 'text_charge' not in row:
            row['text_charge'] = _describe_text_charge(route, row['text'], text_widths, text_envelope)
            row['row_characters'] = capacity.measure_capacity_value(route, row['descriptor']) + 1
            row['characters'] = row['row_characters'] + (
                row['text_charge']['characters'] + row['text_charge']['envelope'] if row['text'] else 0)
    inventory = dict(protocol=PROTOCOL, adapter=adapter, units=rows,
                     characters=sum(row['characters'] for row in rows if row['required']),
                     frame=classified['frame'], frame_source=base_frame)
    rule = legacy._find_unit_rule(adapter, None)
    if rule:
        inventory['unit_rule'] = rule
    return inventory


def _describe_series_frames(route, role, frame_source, profile, moved=()):
    frames = field_classes.describe_role_frame(route, role, frame_source, legacy.KINDS, unit_fields=moved)
    selected = capacity.select_task_limits(route, profile, 'read')
    group_bound = min(CARD_CHARACTERS, selected['answer_bound']) // capacity.CARD_ENTRY_CHARACTERS
    result = {}
    for kind, fields in frames.items():
        reserved = ({'$groups': max(1, group_bound) * CARD_GROUP_FIELDS_CHARACTERS + 2, '$continued_unit': UNIT_ROW_CHARACTERS,
                     '$seam': UNIT_ROW_CHARACTERS} if kind == 'read' else {})
        result[kind] = dict(reserved, **{'$protocol': PROTOCOL_FRAME_CHARACTERS}, **fields)
    return result


def admit_packet(records, artifacts, route, packet, adapter):
    """Admit a series with the final form allocated by its pinned plan."""
    from .review_parts_roles import get_adapter
    profile = capacity.read_output_profile(records, route)
    inventory = build_series_inventory(route, packet, adapter, records=records, artifacts=artifacts)
    targets = get_adapter(adapter)['targets'](packet)
    moved = set()
    while True:
        frames = _describe_series_frames(route, packet['role'], inventory['frame_source'], profile, moved)
        try:
            plan = capacity.plan_chain_capacity(route, profile, frames=frames,
                inventory=[dict(handle=row['handle'], kind=row['kind'], characters=row['characters'])
                           for row in inventory['units'] if row['required']],
                targets=['t' + str(index + 1) for index in range(len(targets))], records=records, artifacts=artifacts,
                allowance_rows_bound=0, final_pages=True)
        except ResearchError as error:
            if error.code != 'review_route_capacity_below_floor':
                raise
            return dict(state='refuse', reason=error.code, message=error.message, details=error.details)
        additions = {name for frame in plan['frame'].values() for name in frame['unit_fields']
                     if not name.startswith('$')} - moved
        if not additions:
            break
        moved.update(additions)
        bounded = field_classes.describe_role_frame(route, packet['role'], inventory['frame_source'],
                                                    ['read'], unit_fields=moved)['read']
        inventory = build_series_inventory(route, packet, adapter, records=records, artifacts=artifacts,
                                           bounded_fields=bounded)
    for name, size in frames['read'].items():
        if name in ('$groups', '$continued_unit', '$seam') and plan['frame']['read']['bounded_fields'][name] != size:
            return dict(state='refuse', reason='review_route_capacity_below_floor', message=BELOW_FLOOR_MESSAGE)
    # Validate the reachable card bounds before any assignment is published.
    read_content_rate(plan)
    for row in inventory['units']:
        for component in row['components']:
            if component.get('encoding', 'utf-8') != 'utf-8':
                content = row['text'][component['start']:component['end']]
                legacy._require(legacy._read_entry_bytes(dict(artifact=component['artifact'],
                    encoding=component['encoding'], content=content)) is not None and
                    capacity.measure_capacity_value(route, content) <= plan['task']['read']['material_allowance'],
                    'A non-text body cannot fit whole', 'review_parts_inventory_too_large',
                    unit=row['handle'], body=component['artifact']['sha256'],
                    body_characters=capacity.measure_capacity_value(route, content))
    return dict(state='accept', plan=plan, inventory=inventory, targets=targets)


def _find_text_offset(row, position):
    """Return the first character whose rendered start is at/after position."""
    charge = row['text_charge']
    position -= row['row_characters']
    if position <= 0:
        return 0
    # Opening quote travels with the row. The last character crossing an
    # interval endpoint travels whole with the interval containing its start.
    position -= (charge['envelope'] - 1) // 2
    if position <= 0:
        return 0
    runs = charge['runs']
    if not runs or position >= charge['characters']:
        return len(row['text'])
    index = bisect.bisect_right([run[1] for run in runs], position) - 1
    start, before, width = runs[index]
    return min(len(row['text']), start + (position - before + width - 1) // width)


def _list_interval_units(inventory, first, last):
    indices = {row['handle']: index for index, row in enumerate(inventory['units'])}
    start, end = indices[first['unit']], indices[last['unit']]
    for index in range(start, end + 1):
        row = inventory['units'][index]
        yield row, first['offset'] if index == start else 0, last['offset'] if index == end else row['characters']


def deliver_read_interval(route, inventory, turn):
    """Rows and characters travel with their first rendered character, once."""
    groups = turn['cards']
    material, context = [], {}
    for row, start, end in _list_interval_units(inventory, groups[0]['first'], groups[-1]['last']):
        if start == 0:
            material.append(copy.deepcopy(row['descriptor']))
        elif not context:
            continued = {key: copy.deepcopy(row['descriptor'][key]) for key in
                         ('handle', 'digest', 'kind') if key in row['descriptor']}
            continued['reference_count'] = row['descriptor']['references']['count']
            context = dict(unit=row['handle'], row=continued, offset=start)
        left, right = _find_text_offset(row, start), _find_text_offset(row, end)
        if right > left:
            material.append(row['text'][left:right])
    return material, context


def read_material_texts(inventory, turn, material, context):
    """Bind every supplied character, including middle units, to its frozen text."""
    expected, expected_context = deliver_read_interval(None, inventory, turn)
    legacy._require(material == expected and context == expected_context, DELIVERY_INVALID_MESSAGE,
                    'review_parts_delivery_invalid')
    result = []
    for row, start, end in _list_interval_units(inventory, turn['cards'][0]['first'], turn['cards'][-1]['last']):
        left, right = _find_text_offset(row, start), _find_text_offset(row, end)
        if right > left:
            result.append(dict(unit=row['handle'], start=left, end=right, text=row['text'][left:right]))
    return result


def build_series_state(observations):
    """Rebuild receipts, raw cards, checked findings and retained entries."""
    state = dict(protocol=PROTOCOL, receipts=[], turns=[], cards=[], combinations=[], findings=[], entries=[],
                 cursor=None, scientific_rounds=[], scientific_covered={}, material_state=dict(arguments={}, pending=None),
                 late_unit_reserve=0, late_characters_beyond_reserve=0, pending_requests=[], delivered_parts={})
    for observation in observations:
        advance_series_state(state, observation)
    return state


def advance_series_state(state, observation):
    """Fold one authenticated observation into an evaluation-local state."""
    packet, delta = observation['packet'], observation['delta']
    header = packet[legacy.KEY]
    state['receipts'].append(copy.deepcopy(observation['receipt']))
    state['turns'].append(dict(kind=header['kind'], turn=header['turn']))
    for fragment in _list_packet_fragments(packet, observation['inventory']):
        state['delivered_parts'].setdefault(fragment['unit'], []).append(
            dict(start=fragment['start'], end=fragment['end']))
    _advance_requested_ranges(state, packet, observation['inventory'])
    if 'page_plan_digest' in header:
        _advance_final_progress(state, observation)
    state['cards'].extend(copy.deepcopy(delta.get('cards', [])))
    state['combinations'].extend(copy.deepcopy(delta.get('combinations', [])))
    findings = copy.deepcopy(delta.get('findings', []))
    for index, finding in enumerate(findings, 1):
        finding['id'] = 'f%d-%d' % (header['turn'], index)
        combination = next(row for row in state['combinations'] if row['id'] == finding['combination'])
        finding['carried'] = dict(finding['output'], id=finding['id'],
            combination_view=dict(combination['output'], id=combination['id']),
            verified_statements=finding['statements'], unverified_statements=finding['unverified_statements'])
        state['entries'].append(finding['id'])
    state['findings'].extend(findings)
    if header['kind'] == 'read':
        state['cursor'] = copy.deepcopy(header['interval']['next'])
        state['entries'].extend(card['id'] for card in delta['cards'])
        for name in ('late_unit_reserve', 'late_characters_beyond_reserve'):
            state[name] += header['interval']['allowance'][name]
    elif header['kind'] == 'combine':
        if header.get('mode') == 'condense' and 'page_plan_digest' not in header:
            block = set(delta['condensation']['block']['entries'])
            at = min(state['entries'].index(handle) for handle in block)
            state['entries'] = [handle for handle in state['entries'] if handle not in block]
            state['entries'].insert(at, delta['condensation']['id'])
            state['combinations'].append(delta['condensation'])
        elif 'page_plan_digest' not in header:
            state['scientific_rounds'].append(header['round'])
            state['scientific_covered'].setdefault(header['round'], []).extend(delta['entry_handles'])
    if observation['output'].get('kind') == 'requests':
        state['pending_requests'].extend(_resolve_requests(observation['inventory'], observation['output'], state))
    if observation.get('material'):
        state['material_state'] = copy.deepcopy(observation['material']['material_state'])
    checked = {finding['combination'] for finding in state['findings']}
    for combination in state['combinations']:
        if combination.get('mode') != 'condense':
            combination['checked'] = combination['id'] in checked
    return state


def _list_state_entries(state):
    by_handle = {row['id']: row for row in state['cards'] + state['combinations'] + state['findings']}
    return [by_handle[handle] for handle in state['entries']]


def _describe_entry_rows(route, entries):
    return [dict(handle=row['id'], characters=capacity.measure_capacity_value(route, row.get('carried', row['output'])))
            for row in entries]


def _derive_observations(records, artifacts, chain, kind):
    """Include failed attempts for adaptation without granting them delivery credit."""
    from .review_failure_class import classify_attempt
    observations = []
    members = {assignment['id']: (assignment, packet) for assignment, packet in chain}
    from .review_protocol import read_filtered_records
    attempts = {attempt['id']: attempt for identifier in members for attempt in
                read_filtered_records(records, 'review_attempt', assignment_id=identifier).values()}
    for attempt in sorted(attempts.values(), key=lambda row: (row.get('recorded_revision', 0), row['id'])):
        member = members.get(attempt['assignment_id'])
        if member is None or member[1][legacy.KEY]['kind'] != kind:
            continue
        classification = classify_attempt(records, artifacts, attempt)
        task = member[1][legacy.KEY]['capacity']
        observations.append(dict(output_tokens=(attempt.get('usage') or {}).get('output_tokens'),
            material_allowance=task.get('level_material_allowance', task['material_allowance']),
            outcome=classification['outcome'] if classification else None))
    return observations


def derive_series_task_limits(records, artifacts, assignment, *, kind=None, notice_characters=0):
    plan = capacity.read_chain_capacity_plan(artifacts, assignment)
    kind = kind or assignment['capacity_plan_kind']
    chain = list_series(records, artifacts, assignment['id'])
    return capacity.derive_plan_task_limits(plan, kind, _derive_observations(records, artifacts, chain, kind),
                                             notice_characters=notice_characters)


def _describe_capacity(limits, plan):
    names = ('material_allowance', 'level_material_allowance', 'answer_bound', 'request_bound',
             'target_allowance', 'notice_characters')
    return dict({name: copy.deepcopy(limits[name]) for name in names if name in limits},
                output_tokens=plan['limits']['output_tokens']['value'])


def _build_frame(route, inventory, plan, kind):
    frame = field_classes.classify_packet(route, inventory['frame_source'].get('role', 'result'),
        inventory['frame_source'], bounded_fields=plan['frame'][kind]['bounded_fields'])['frame']
    frame.pop('digest', None)
    frame = _restore_collection_shapes(frame, inventory['frame_source'])
    frame['evidence'] = []
    for name in ('source_inventory', 'source_deliveries', 'pending_source_requests', 'reviewer_excerpts'):
        frame.pop(name, None)
    return frame


def _select_condensing_block(route, plan, limits, state, *, reading, final_limits, paged_final=False):
    entries = _list_state_entries(state)
    rows = _describe_entry_rows(route, entries)
    room = min(limits['material_allowance'], plan['allocation']['rooms']['combine'])
    total = sum(row['characters'] for row in rows)
    final_room = min(plan['allocation']['page_room'], final_limits['material_allowance'])
    if state['pending_requests']:
        final_room = max(1, final_room - UNIT_ROW_CHARACTERS - 1000)
    from .review_condensing import MINIMUM_CONDENSING_BLOCK_CHARACTERS
    if total > final_room and (reading or not paged_final):
        for block in cut_condensing_blocks(rows, room):
            if block['characters'] >= MINIMUM_CONDENSING_BLOCK_CHARACTERS:
                return block
    # The forecast counts unfunded late entries separately. Even after earlier
    # blocks have contracted, its remaining complete cards still require their
    # final condensation; an archive never substitutes for their review text.
    scheduled = plan_card_condensing(plan, [dict(cards=[card['group'] for card in state['cards']])])
    candidates = [entry for entry in entries if entry in state['cards'] and (
        entry['over_allowance'] or scheduled and entry['group'].get('late') and
        entry['group']['late_reserve_before'] + entry['group']['entry_characters'] > plan['allocation']['late_unit_reserve'])]
    selected = {entry['id'] for entry in candidates}
    runs, current = [], []
    for entry in entries:
        if entry['id'] in selected:
            current.append(entry)
        elif current:
            runs.append(current)
            current = []
    if current:
        runs.append(current)
    for run in runs:
        for block in cut_condensing_blocks(_describe_entry_rows(route, run), room):
            if block['characters'] >= MINIMUM_CONDENSING_BLOCK_CHARACTERS and (
                    not reading or block['characters'] >= room // 2):
                return block
    return None


def plan_series_turn(route, artifacts, logical, inventory, plan, state, *, binding, limits_by_kind,
                     refusal_notice=None, replacement=None, paged_final=False):
    """Build one fresh request from pinned limits and authenticated state only."""
    required = [row for row in inventory['units'] if row['required']]
    read_inventory = dict(inventory, units=required)
    position = state['cursor']
    reading = position is None and bool(required) or position is not None and position['unit_index'] < len(required)
    combine_limits = copy.deepcopy(limits_by_kind['combine'])
    if replacement and replacement['kind'] == 'combine':
        combine_limits['material_allowance'] = min(combine_limits['material_allowance'], replacement['room'])
    block = _select_condensing_block(route, plan, combine_limits, state, reading=reading,
                                    final_limits=limits_by_kind['final'], paged_final=paged_final)
    pending_checks = [row for row in state['combinations'] if row.get('mode') != 'condense' and not row['checked']]
    entries = _list_state_entries(state)
    round_id = digest([row['id'] for row in state['cards']])
    covered = set(state['scientific_covered'].get(round_id, []))
    uncombined = [row for row in entries if row['id'] not in covered and row not in state['findings']]
    kind = ('combine' if block else 'read' if reading else 'check' if pending_checks else
            'combine' if len(entries) > 1 and uncombined else 'final')
    if replacement and replacement['kind'] == 'check':
        kind = 'check'
    limits = limits_by_kind[kind]
    capacity_value = _describe_capacity(limits, plan)
    binding = dict(binding)
    targets = binding.pop('_targets', [])
    header = dict(binding, protocol=PROTOCOL, turn=len(state['turns']) + 1, kind=kind,
                  counts=dict(turns=len(state['turns']), receipts=len(state['receipts']), cards=len(state['cards']),
                              combinations=len(state['combinations']), findings=len(state['findings'])),
                  capacity=capacity_value)
    packet = _build_frame(route, inventory, plan, kind)
    if logical.get('role') == 'adjudicator':
        packet['assessments'] = [dict(id=row['id']) for row in logical.get('assessments', [])[:2]]
        if 'objection' in logical:
            packet.setdefault('objection', {})['id'] = logical['objection']['id']
    packet[legacy.KEY] = header
    packet[SERIES_MARKER] = PROTOCOL
    packet[legacy.STATE_KEY] = dict(cards=[], combinations=[], findings=[])
    packet[CONTEXT_KEY] = {}
    packet[MATERIAL_KEY] = []
    packet[GROUPS_KEY] = []
    pending_material = state['material_state']['pending']
    if pending_material:
        packet['review_parts_material_request'] = dict(stage='material', **pending_material,
            title=state['material_state']['arguments'][pending_material['ref']]['title'])
    if kind == 'read':
        level = limits.get('level_material_allowance', limits['material_allowance'])
        sizing_plan = plan
        if limits['material_allowance'] != level:
            # The refusal consumes delivery room, while the card rate and the
            # answer bound remain pinned to the unchanged level.
            sizing_plan = copy.deepcopy(plan)
            level = limits['material_allowance']
            pinned = copy.deepcopy(next(row for row in plan['task']['read']['levels']
                if row['material_allowance'] == limits['level_material_allowance']))
            pinned.update(material_allowance=level, answer_bound=limits['answer_bound'])
            sizing_plan['task']['read']['levels'].append(pinned)
        turn = size_read_turn(sizing_plan, level, required, position=position)
        header['interval'] = {key: value for key, value in turn.items() if key != 'cards'}
        header['unit_characters'] = turn['unit_characters']
        header['card_allowance'] = turn['allowance']
        packet[GROUPS_KEY] = copy.deepcopy(turn['cards'])
        packet[MATERIAL_KEY], packet[CONTEXT_KEY] = deliver_read_interval(route, read_inventory, turn)
    elif kind == 'combine' and block:
        header.update(mode='condense', block=dict(handle=block['handle'], characters=block['characters'],
                                                entry_count=len(block['entries'])))
        block_ref = _put_value(artifacts, block)
        header['block_artifact'] = block_ref
        selected = {handle for handle in block['entries']}
        packet[legacy.STATE_KEY]['cards'] = [row.get('carried', row['output']) for row in entries if row['id'] in selected]
    elif kind == 'combine':
        room = limits['material_allowance']
        if replacement and replacement['kind'] == 'combine':
            room = min(room, replacement['room'])
        selected, characters = [], 0
        for entry, row in zip(uncombined, _describe_entry_rows(route, uncombined)):
            if characters + row['characters'] > room:
                break
            selected.append(entry)
            characters += row['characters']
        legacy._require(bool(selected), 'The scientific block has no fitting entry.', 'review_parts_no_progress')
        header.update(mode='scientific', round=round_id,
                      entries_artifact=_put_value(artifacts, [row['id'] for row in selected]),
                      entry_count=len(selected), block_characters=characters)
        packet[legacy.STATE_KEY]['cards'] = [row.get('carried', row['output']) for row in selected]
    elif kind == 'check':
        room = limits['material_allowance']
        if replacement and replacement['kind'] == 'check':
            room = min(room, replacement['room'])
        selected, deliveries = _select_check_work(route, inventory, pending_checks, room)
        header['check_combinations'] = [row['id'] for row in selected]
        header['block_characters'] = capacity.measure_capacity_value(route, deliveries)
        packet[legacy.STATE_KEY]['combinations'] = [dict(row['output'], id=row['id']) for row in selected]
        packet['review_parts_requests'] = deliveries
    else:
        packet[legacy.STATE_KEY] = dict(cards=[row['output'] for row in entries if row in state['cards']],
            combinations=[row['output'] for row in entries if row in state['combinations']],
            findings=[row['carried'] for row in entries if row in state['findings']])
        requests = state.get('pending_requests', [])
        if requests and not state.get('final_progress'):
            room = limits['material_allowance'] - capacity.measure_capacity_value(route, packet[legacy.STATE_KEY])
            packet['review_parts_requests'] = _deliver_requested_units(route, inventory, requests, room, state)
            legacy._require(bool(packet['review_parts_requests']), 'The requested material has no room beside the final state.',
                            'review_parts_no_progress')
    header['targets'] = copy.deepcopy(targets)
    if refusal_notice is not None:
        from .review_refusal_notice import attach_refusal_notice
        packet = attach_refusal_notice(packet, refusal_notice)
    packet.pop('digest', None)
    packet['digest'] = digest(packet)
    return packet


def _deliver_requested_units(route, inventory, scopes, room, state=None):
    delivered, used = [], 0
    rows = {row['handle']: row for row in inventory['units']}
    for entry in (state or {}).get('cards', []) + (state or {}).get('combinations', []) + (state or {}).get('findings', []):
        rows[entry['id']] = dict(handle=entry['id'], text=legacy.render_json(entry['output']))
        if entry.get('mode') == 'condense':
            handle = entry['id'] + '/entries'
            rows[handle] = dict(handle=handle, text=legacy.render_json(entry['block']))
    for scope in scopes:
        row = rows[scope['unit']]
        start, end = scope.get('start', 0), scope.get('end', len(row['text']))
        text = row['text'][start:end]
        if scope.get('row_only'):
            value = dict(unit=row['handle'], start=0, end=0, text='', descriptor=row['descriptor'])
            if capacity.measure_capacity_value(route, delivered + [value]) > room:
                break
            delivered.append(value)
            continue
        # The request's own row, offsets and escapes are part of material.
        low, high = 0, len(text)
        while low < high:
            middle = (low + high + 1) // 2
            value = dict(unit=row['handle'], start=start, end=start + middle, text=text[:middle],
                         descriptor=row.get('descriptor', dict(handle=row['handle'], digest=digest(row['text']))))
            if capacity.measure_capacity_value(route, delivered + [value]) <= room:
                low = middle
            else:
                high = middle - 1
        if not low:
            break
        delivered.append(dict(unit=row['handle'], start=start, end=start + low, text=text[:low],
                              descriptor=row.get('descriptor', dict(handle=row['handle'], digest=digest(row['text'])))))
        if low < len(text):
            break
    return delivered


def prepare_packet(records, artifacts, value, route, packet, prompt, history, **options):
    """Return None for v3 admission fallback; recorded v3 never changes protocol."""
    from .review_parts_roles import find_adapter, get_adapter
    from .review_protocol import _read_json
    from . import review_transport as transport
    adapter = find_adapter(value, packet)
    if adapter is None or route['adapter'] not in ('claude_cli_v1', 'codex_cli_v1', 'codex_cli_framed_v1'):
        return None
    prior_id = value.get('context', {}).get('prior_assignment_id')
    prior = records.get('review_assignment', {}).get(prior_id)
    previous = _read_json(artifacts, prior['packet']) if prior and prior.get('packet') else {}
    header = previous.get(legacy.KEY, {})
    continuing = header.get('protocol') == PROTOCOL
    base_parameters = options.get('base_prompt_parameters')
    if base_parameters is None:
        parameters = prompt.get('parameters', {})
        base_parameters = dict(orchestration=parameters.get('orchestration'),
            repair=bool(parameters.get('context_repair')), excerpts='reviewer_excerpts' in parameters,
            common_version=parameters.get('common_prompt_version'))
    logical = copy.deepcopy(packet)
    logical.pop('digest', None)
    # The frozen source inventory supplies final requests, independently of the
    # session projection that prepared the prospective ordinary role packet.
    logical.pop('source_rules', None)
    logical.pop('source_artifacts', None)
    if not continuing:
        ordinary = transport.request_body(route, prompt['text'], packet, history)
        from .review_single_request import describe_task_entry
        measured = capacity.measure_request(route, ordinary)['text_characters']
        entry = options.get('entry')
        if entry is None and measured <= transport.local_input_limit(route):
            entry = describe_task_entry(records, route, value['role'], packet, ordinary)
            if entry is None and options.get('capacity_replacement') is None:
                return packet
        admitted = admit_packet(records, artifacts, route, logical, adapter)
        if admitted['state'] != 'accept':
            return None
        plan, inventory = admitted['plan'], admitted['inventory']
        plan_reference = dict(protocol=capacity.CAPACITY_PLAN_PROTOCOL, digest=digest(plan), artifact=_put_value(artifacts, plan))
        observations, chain = [], []
        series_id = value['id']
        targets = admitted['targets']
    else:
        plan_reference = prior['capacity_plan']
        plan = capacity.read_chain_capacity_plan(artifacts, prior)
        old_inventory = _read_series_inventory(artifacts, header['inventory'], header['inventory_digest'])
        frozen_logical = legacy.read_logical_packet(artifacts, header)
        for name in ('source_inventory', 'source_deliveries', 'pending_source_requests',
                     'reviewer_excerpts', 'continuation', 'prior_context'):
            if name in frozen_logical:
                logical[name] = copy.deepcopy(frozen_logical[name])
            else:
                logical.pop(name, None)
        optional_bodies = {part.get('artifact', {}).get('sha256') for row in old_inventory['units']
                           if not row['required'] for part in row['components']}
        originally_bound = {row['artifact']['sha256'] for row in frozen_logical.get('evidence', [])}
        logical['evidence'] = [row for row in logical.get('evidence', []) if
                               row['artifact']['sha256'] not in optional_bodies or row['artifact']['sha256'] in originally_bound]
        inventory = build_series_inventory(route, logical, adapter, records=records, artifacts=artifacts,
            previous=old_inventory, bounded_fields=plan['frame']['read']['bounded_fields'])
        if inventory['units'] == old_inventory['units']:
            inventory = old_inventory
            logical = legacy.read_logical_packet(artifacts, header)
        observations = observe_series(records, artifacts, prior_id)
        chain = list_series(records, artifacts, prior_id)
        series_id, entry = header['series_assignment_id'], header.get('entry')
        targets = header['targets']
    state = build_series_state(observations)
    for stored_entry in state['cards'] + state['combinations'] + state['findings']:
        _put_value(artifacts, stored_entry['output'])
    for handle, output in state.get('final_catalog', {}).items():
        _put_value(artifacts, output)
        _put_value(artifacts, state['final_blocks'][handle])
    inventory_reference = _put_value(artifacts, inventory)
    logical.pop('digest', None)
    logical['digest'] = digest(logical)
    logical_reference = _put_value(artifacts, logical)
    binding = dict(adapter=adapter, series_assignment_id=series_id,
        series_key=get_adapter(adapter)['series_key'](logical), plan_digest=plan_reference['digest'],
        inventory=inventory_reference, inventory_digest=digest(inventory),
        logical_packet=logical_reference, logical_packet_digest=digest(logical),
        state=_put_value(artifacts, state), state_digest=digest(state), _targets=targets)
    if entry is not None:
        binding['entry'] = entry
    origin = header.get('replaces_assignment_id') if continuing else options.get('replaces_assignment_id')
    if origin is not None:
        binding['replaces_assignment_id'] = origin
    notice = options.get('refusal_notice')
    notice_characters = capacity.measure_capacity_value(route, notice) if notice else 0
    limits = {kind: capacity.derive_plan_task_limits(plan, kind,
        _derive_observations(records, artifacts, chain, kind)) for kind in legacy.KINDS}
    if notice_characters:
        read = limits['read']
        read = capacity.derive_plan_task_limits(plan, 'read',
            _derive_observations(records, artifacts, chain, 'read'), notice_characters=notice_characters)
        limits['read'] = read
    planned = plan_series_turn(route, artifacts, logical, inventory, plan, state, binding=binding,
                               limits_by_kind=limits, refusal_notice=notice,
                               paged_final=plan['allocation'].get('final_form') == 'pages' or bool(state.get('final_progress')))
    planned = prepare_series_final(records, artifacts, prior if continuing else None, route, logical,
        inventory, plan, state, limits, planned)
    _store_delivery_fragments(artifacts, planned, inventory)
    _store_context_entries(artifacts, planned)
    planned[capacity.CAPACITY_REQUEST_KEY] = dict(kind=planned[legacy.KEY]['kind'], plan=plan_reference)
    return planned


def _resolve_requests(inventory, output, state=None):
    requests = copy.deepcopy(output.get('unit_requests', []))
    for request in output.get('source_requests', []):
        from .operations import fields
        fields(request, ('id', 'kind', 'version_id', 'depth'), ('why',), code='invalid_value_review')
        if request['kind'] != 'source':
            raise ResearchError('invalid_value_review', 'Request a source or a unit from the frozen inventory.')
        matches = [row for row in inventory['units'] if not row['required'] and
                   row['source']['identity'].get('version_id') == request['version_id'] and
                   row['source']['kind'] == ('fulltext' if request['depth'] == 'fulltext' else 'abstract')]
        legacy._require(bool(matches), 'The source request names a captured source of this inventory.')
        requests.extend(dict(unit=row['handle'], why=request.get('why', 'Inspect the source.')) for row in matches)
    rows = {row['handle']: row for row in inventory['units']}
    for entry in (state or {}).get('cards', []) + (state or {}).get('combinations', []) + (state or {}).get('findings', []):
        rows[entry['id']] = dict(text=legacy.render_json(entry['output']))
        if entry.get('mode') == 'condense':
            rows[entry['id'] + '/entries'] = dict(text=legacy.render_json(entry['block']))
    for handle, value in (state or {}).get('final_catalog', {}).items():
        rows[handle] = dict(text=legacy.render_json(value))
        rows[handle + '/entries'] = dict(text=legacy.render_json(state['final_blocks'][handle]))
    for handle, value in (state or {}).get('final_entries', {}).items():
        rows[handle] = dict(text=legacy.render_json(value))
    normalized = []
    for request in requests:
        handle = request['unit'] + ('/entries' if request.get('entries') else '')
        legacy._require(handle in rows, 'A request names a stored unit, entry or block.')
        row = rows[handle]
        component = None
        if request.get('item') is True:
            component = next((part for part in row.get('components', []) if part['kind'] == 'item'), None)
            legacy._require(component is not None and not request.get('body'), 'An item request names only its item.')
        if request.get('body'):
            component = next((part for part in row.get('components', []) if
                              part.get('artifact', {}).get('sha256') == request['body']), None)
            legacy._require(component is not None, 'The requested body belongs to this unit.')
        length = component['end'] - component['start'] if component else len(row['text'])
        start, end = request.get('start', 0), request.get('end', length)
        if length == 0 and 'start' not in request and 'end' not in request:
            normalized.append(dict(unit=handle, start=0, end=0, row_only=True))
            continue
        legacy._require(type(start) is int and type(end) is int and 0 <= start < end <= length,
                        'Requested offsets lie in the unit or its body.')
        if component:
            start, end = start + component['start'], end + component['start']
        normalized.append(dict(unit=handle, start=start, end=end))
    return normalized


def _advance_requested_ranges(state, packet, inventory):
    pending = state['pending_requests']
    for delivery in packet.get('review_parts_requests', []):
        scope = next((scope for scope in pending if scope['unit'] == delivery['unit']
                      and scope['start'] == delivery['start'] and delivery['end'] <= scope['end']), None)
        if scope is None and packet[legacy.KEY]['kind'] == 'final':
            raise ResearchError('review_parts_delivery_invalid', DELIVERY_INVALID_MESSAGE)
        if scope is not None:
            scope['start'] = delivery['end']
            scope.pop('row_only', None)
    state['pending_requests'] = [scope for scope in pending if scope.get('row_only') or scope['start'] < scope['end']]


def _read_interval(packet):
    return dict(packet[legacy.KEY]['interval'], cards=packet[GROUPS_KEY])


def _verify_group_statements(packet, group, statements, inventory, used):
    """Reuse v2 normalization and offsets after restricting each unit to this group."""
    verified, unmatched = [], 0
    if not statements:
        return verified, unmatched, used
    indices = {row['handle']: row for row in inventory['units']}
    for statement in statements:
        from .operations import fields
        fields(statement, ('unit', 'text', 'why'), code='invalid_value_review')
        handle = statement['unit']
        legacy._require(isinstance(handle, str) and handle in indices, 'A statement names an inventory unit.')
        fragments = []
        required = dict(inventory, units=[row for row in inventory['units'] if row['required']])
        for row, start, end in _list_interval_units(required, group['first'], group['last']):
            if row['handle'] != handle:
                continue
            left, right = _find_text_offset(row, start), _find_text_offset(row, end)
            for component in row['components']:
                if component.get('encoding', 'utf-8') != 'utf-8':
                    continue
                begin, stop = max(left, component['start']), min(right, component['end'])
                if stop <= begin:
                    continue
                content = row['text'][begin:stop]
                location = dict(content=content, at=dict(unit=handle), offset=begin - component['start'])
                if component['kind'] == 'item':
                    location['at']['item'] = True
                else:
                    location['at']['body'] = component['artifact']['sha256']
                fragments.append(location)
        # v2's matching itself is shared; its unit-wide source collector is
        # deliberately replaced by a group-limited transient projection here.
        sources = []
        for fragment in fragments:
            normalized, offsets = legacy.review_excerpts.normalize_with_offsets(fragment['content'])
            sources.append((fragment, normalized, offsets))
        text, why = statement['text'], statement['why']
        legacy._require_text(text, '/statements/text')
        legacy._require_text(why, '/statements/why')
        used += len(text)
        legacy._require(len(text) <= 3000 and len(why) <= 400 and used <= 24000,
                        'Statements exceed their field bounds.')
        phrase = legacy.review_excerpts.normalize_with_offsets(text)[0]
        for fragment, normalized, offsets in sources:
            found = normalized.find(phrase)
            if found >= 0 and phrase:
                start = offsets[found]
                end = offsets[found + len(phrase) - 1] + 1
                verified.append(dict(statement, text=fragment['content'][start:end],
                    at=dict(fragment['at'], start=start + fragment['offset'], end=end + fragment['offset'])))
                break
        else:
            unmatched += 1
    return verified, unmatched, used


def _validate_read(packet, output, inventory, route, plan):
    from .operations import fields
    fields(output, ('kind', 'turn', 'cards', 'unborne'), code='invalid_value_review')
    groups = packet[GROUPS_KEY]
    legacy._require(isinstance(output['cards'], list) and len(output['cards']) == len(groups), GROUP_INVALID_MESSAGE)
    expected = {group['handle']: group for group in groups}
    seen, cards, borne, used = set(), [], set(), 0
    targets = {row['id'] for row in packet[legacy.KEY]['targets']}
    for card in output['cards']:
        fields(card, ('group', 'summary', 'bearing', 'statements', 'depends_on', 'questions'), code='invalid_value_review')
        group = card['group']
        legacy._require(isinstance(group, dict) and group.get('handle') in expected and
                        group == expected[group['handle']] and group['handle'] not in seen, GROUP_INVALID_MESSAGE)
        seen.add(group['handle'])
        legacy._require(capacity.measure_capacity_value(route, group) <= CARD_GROUP_FIELDS_CHARACTERS,
                        GROUP_INVALID_MESSAGE)
        for name, maximum in (('summary', 12000), ('depends_on', 400), ('questions', 400)):
            legacy._require_text(card[name], '/cards/' + name, blank=name != 'summary')
            legacy._require(len(card[name]) <= maximum, 'A card field exceeds its bound.')
        legacy._require(isinstance(card['bearing'], list) and len(card['bearing']) <= 10 and
                        isinstance(card['statements'], list) and len(card['statements']) <= 8,
                        'A card exceeds its bearing or statement count.')
        for bearing in card['bearing']:
            fields(bearing, ('target', 'relation', 'reason'), code='invalid_value_review')
            legacy._require(bearing['target'] in targets and bearing['relation'] in
                            ('supports', 'qualifies', 'contradicts', 'context'), 'A bearing names an assigned target and relation.')
            legacy._require_text(bearing['reason'], '/bearing/reason')
            legacy._require(len(bearing['reason']) <= 400, 'A bearing reason exceeds its field bound.')
            borne.add(bearing['target'])
            legacy._require(bearing['relation'] == 'context' or bool(card['statements']),
                            'A material bearing needs a statement.')
        statements, unmatched, used = _verify_group_statements(packet, group, card['statements'], inventory, used)
        card_size = capacity.measure_capacity_value(route, card)
        legacy._require(card_size <= min(row['material_allowance'] for row in plan['task']['combine']['levels']),
                        'A complete card exceeds the smallest combine block.')
        cards.append(dict(id=group['handle'], output=copy.deepcopy(card), group=copy.deepcopy(group),
                          statements=statements, unverified_statements=unmatched, characters=card_size,
                          over_allowance=card_size > group['characters']))
    legacy._require(isinstance(output['unborne'], list), 'Unborne is an array.')
    unborne = set()
    for row in output['unborne']:
        fields(row, ('target', 'reason'), code='invalid_value_review')
        legacy._require(row['target'] in targets and row['target'] not in unborne, 'Unborne names distinct targets.')
        legacy._require_text(row['reason'], '/unborne/reason')
        legacy._require(len(row['reason']) <= 400, 'An unborne reason exceeds its field bound.')
        unborne.add(row['target'])
    legacy._require(unborne == targets - borne, 'Unborne names exactly targets without a bearing.')
    legacy._require(capacity.measure_capacity_value(route, output['cards']) <= CARD_CHARACTERS and
                    capacity.measure_capacity_value(route, output) <= packet[legacy.KEY]['capacity']['answer_bound'],
                    READ_ANSWER_LIMIT_MESSAGE)
    return dict(cards=cards, combinations=[], findings=[])


def validate_turn_output(records, artifacts, assignment, packet, output, *, attempt=None):
    """Validate v3 at the ordinary output-contract edge, retaining whole answers."""
    from .evaluation import Evaluation
    artifacts = Evaluation.of(records, artifacts)
    header = packet[legacy.KEY]
    route = records['review_route'][assignment['route_id']]
    plan = capacity.read_chain_capacity_plan(artifacts, assignment)
    inventory = _read_series_inventory(artifacts, header['inventory'], header['inventory_digest'])
    state = _read_value(artifacts, header['state'], header['state_digest'])
    legacy._require(header['plan_digest'] == assignment['capacity_plan']['digest'], DELIVERY_INVALID_MESSAGE,
                    'review_parts_delivery_invalid')
    return validate_planned_output(packet, output, inventory, plan, state, route,
                                   artifacts=artifacts, assignment=assignment)


def validate_planned_output(packet, output, inventory, plan, state, route, *, artifacts, assignment):
    """Validate against already authenticated plan, inventory and prior state.

    Keeping artifact resolution at the public command edge also permits bounded
    streaming simulations to reuse their immutable inventory without parsing it
    again for every read. Both paths execute the same checks.
    """
    from .operations import fields
    from .review_handles import is_material_output, validate_material
    header = packet[legacy.KEY]
    if 'page_plan_digest' in header:
        state = dict(state, final_entries={**state.get('final_entries', {}), **read_final_entry_fragments(packet)})
    kind = header['kind']
    if kind == 'read':
        required = dict(inventory, units=[row for row in inventory['units'] if row['required']])
        interval = _read_interval(packet)
        expected = size_read_turn(plan, header['capacity'].get('level_material_allowance',
            header['capacity']['material_allowance']), required['units'], position=state['cursor'])
        if header['capacity'].get('level_material_allowance'):
            expected = interval  # The replay below derives refusal-specific sizing as well.
        legacy._require(expected == interval, DELIVERY_INVALID_MESSAGE, 'review_parts_delivery_invalid')
        read_material_texts(required, interval, packet[MATERIAL_KEY], packet[CONTEXT_KEY])
    for delivery in packet.get('review_parts_requests', []):
        row = next((row for row in inventory['units'] if row['handle'] == delivery['unit']), None)
        if row is None:
            row = _find_stored_entry_unit(state, delivery['unit'])
        legacy._require(row is not None, DELIVERY_INVALID_MESSAGE, 'review_parts_delivery_invalid')
        legacy._require(row['text'][delivery['start']:delivery['end']] == delivery['text'],
                        DELIVERY_INVALID_MESSAGE, 'review_parts_delivery_invalid')
    legacy._require(isinstance(output, dict), 'A parts output is an object.')
    if is_material_output(packet, output):
        legacy._require(kind == 'final', 'Material belongs to a final turn.')
        validate_material(packet, output, artifacts=artifacts, assignment=assignment)
        return dict(cards=[], combinations=[], findings=[])
    if kind == 'final':
        if output.get('kind') == 'requests':
            fields(output, ('kind', 'unit_requests', 'source_requests'), code='invalid_value_review')
            legacy._require(isinstance(output['unit_requests'], list) and isinstance(output['source_requests'], list)
                            and bool(output['unit_requests'] or output['source_requests']), 'Request originals or sources.')
            for scope in output['unit_requests']:
                fields(scope, ('unit',), ('why', 'start', 'end', 'body', 'item', 'entries'), code='invalid_value_review')
                legacy._require(isinstance(scope['unit'], str), 'A request names a unit handle.')
                if scope.get('unit') in ({row['id'] for row in state['cards'] + state['combinations'] + state['findings']}
                                         | set(state.get('final_catalog', {})) | set(state.get('final_entries', {}))):
                    continue
                row = next((row for row in inventory['units'] if row['handle'] == scope.get('unit')), None)
                legacy._require(row is not None, 'A request names an inventory unit.')
                if 'start' in scope:
                    legacy._require(type(scope['start']) is int and type(scope.get('end')) is int and
                        0 <= scope['start'] < scope['end'] <= len(row['text']), 'Requested offsets lie in the unit.')
            _resolve_requests(inventory, output, state)
        else:
            remaining = copy.deepcopy(state)
            _advance_requested_ranges(remaining, packet, inventory)
            legacy._require(not remaining['pending_requests'], 'Requested originals are still pending.')
            if 'page_plan_digest' in header:
                from . import review_composition as composition
                envelope = build_validation_envelope(artifacts, packet, inventory)
                rule = composition.find_rule(envelope['role'], envelope)
                if header['final_form'] in ('page', 'final'):
                    composition.validate_page_output(rule, envelope, header['page']['targets'], output)
                else:
                    composition.validate_closing_output(rule, envelope, header['closing']['rows'], output)
        return dict(cards=[], combinations=[], findings=[])
    if kind == 'combine' and header.get('mode') == 'condense':
        from .review_condensing import validate_combination
        block = _read_value(artifacts, header['block_artifact'])
        combined = validate_combination(block, output,
            lambda value: capacity.measure_capacity_value(route, value), header['capacity']['answer_bound'])
        return dict(cards=[], combinations=[], findings=[], condensation=dict(id=header['block']['handle'],
                    output=combined, mode='condense', block=block, characters=capacity.measure_capacity_value(route, combined)))
    legacy._require(output.get('kind') == kind and type(output.get('turn')) is int and
                    output['turn'] == header['turn'], 'The answer names this kind and turn.')
    if kind == 'read':
        return _validate_read(packet, output, inventory, route, plan)
    if kind == 'combine':
        fields(output, ('kind', 'turn', 'combinations', 'rest'), code='invalid_value_review')
        legacy._require_text(output['rest'], '/rest')
        legacy._require(isinstance(output['combinations'], list) and len(output['combinations']) <= 8,
                        'Combinations are an array of at most eight entries.')
        combinations = []
        units = {row['handle'] for row in inventory['units']}
        for index, combination in enumerate(output['combinations'], 1):
            fields(combination, ('units', 'question', 'view', 'read'), ('bearing',), code='invalid_value_review')
            legacy._require(isinstance(combination['units'], list) and combination['units'] and
                            set(combination['units']) <= units, 'A combination names known units.')
            for name in ('question', 'view'):
                legacy._require_text(combination[name], '/' + name)
                legacy._require(len(combination[name]) <= 4000, 'A scientific combination field exceeds its bound.')
            legacy._require(isinstance(combination['read'], list) and combination['read'], 'A combination asks for originals.')
            check_floor = min(row['material_allowance'] for row in plan['task']['check']['levels'])
            scopes = _resolve_requests(inventory, dict(unit_requests=combination['read'], source_requests=[]), state)
            check_room = check_floor - capacity.measure_capacity_value(route,
                [dict(combination, id='c%d-%d' % (header['turn'], index))])
            checks = _deliver_requested_units(route, inventory, scopes, check_room)
            legacy._require(all(any(row['unit'] == scope['unit'] and row['end'] >= scope.get('end',
                len(next(unit for unit in inventory['units'] if unit['handle'] == scope['unit'])['text']))
                for row in checks) for scope in scopes), 'The originals exceed one check turn.')
            combinations.append(dict(id='c%d-%d' % (header['turn'], index), output=copy.deepcopy(combination),
                                     checked=False, mode='scientific'))
        return dict(cards=[], combinations=combinations, findings=[], entry_handles=_read_value(artifacts, header['entries_artifact']))
    fields(output, ('kind', 'turn', 'findings'), code='invalid_value_review')
    expected = set(header['check_combinations'])
    legacy._require(isinstance(output['findings'], list) and
                    {row['combination'] for row in output['findings']} == expected and
                    len(output['findings']) == len(expected), 'Return one finding for each assigned combination.')
    findings, used = [], 0
    for finding in output['findings']:
        fields(finding, ('combination', 'finding', 'effect', 'statements'), code='invalid_value_review')
        legacy._require_text(finding['finding'], '/finding')
        legacy._require(finding['effect'] in ('supports', 'qualifies', 'contradicts', 'unresolved'), 'A finding has a known effect.')
        legacy._require(isinstance(finding['statements'], list), 'Finding statements are an array.')
        statements, unmatched, used = _verify_requested_statements(packet, finding['statements'], inventory, used)
        combination = next(row for row in state['combinations'] if row['id'] == finding['combination'])
        carried = dict(finding, combination_view=dict(combination['output'], id=combination['id']),
                       verified_statements=statements, unverified_statements=unmatched, id='f%d-%d' % (header['turn'], len(findings) + 1))
        legacy._require(capacity.measure_capacity_value(route, carried) <= min(
            row['material_allowance'] for row in plan['task']['combine']['levels']),
            'A checked finding exceeds one complete combine entry.')
        findings.append(dict(output=copy.deepcopy(finding), combination=finding['combination'],
                             statements=statements, unverified_statements=unmatched))
    return dict(cards=[], combinations=[], findings=findings)


def list_series(records, artifacts, assignment_id):
    """Follow one frozen series including replacements, with no live-store replan."""
    current = records.get('review_assignment', {}).get(assignment_id)
    chain, seen, root = [], set(), None
    while current is not None and current['id'] not in seen:
        seen.add(current['id'])
        packet = _read_value(artifacts, current['packet'])
        header = packet.get(legacy.KEY, {})
        if header.get('protocol') != PROTOCOL:
            break
        if root is None:
            root = header
        legacy._require(all(header[key] == root[key] for key in ('series_assignment_id', 'adapter', 'plan_digest')),
                        DELIVERY_INVALID_MESSAGE, 'review_parts_delivery_invalid')
        chain.append((current, packet))
        if current['id'] == header['series_assignment_id']:
            break
        parent = (current.get('capacity_replacement_of') or current.get('context', {}).get('output_refusal', {}).get('assignment_id')
                  or current.get('context', {}).get('prior_assignment_id'))
        current = records.get('review_assignment', {}).get(parent)
    return list(reversed(chain))


def observe_series(records, artifacts, assignment_id):
    from .evaluation import Evaluation
    from .review_protocol import assignment_state
    from .review_input_projection import packets_from_request
    artifacts = Evaluation.of(records, artifacts)
    observations = []
    chain = list_series(records, artifacts, assignment_id)
    state = build_series_state([])
    for chain_index, (assignment, packet) in enumerate(chain):
        header = packet[legacy.KEY]
        legacy._require(digest(state) == header['state_digest'], DELIVERY_INVALID_MESSAGE, 'review_parts_delivery_invalid')
        def read_turn():
            observed = assignment_state(records, artifacts, assignment['id'])
            attempt = records.get('review_attempt', {}).get(observed.get('attempt_id'))
            if not observed['ready'] or attempt is None or attempt.get('origin') != 'instrumented':
                return None
            from .review_failure_class import classify_attempt
            if classify_attempt(records, artifacts, attempt) is not None:
                return None
            _validate_replayed_turn(records, artifacts, assignment, packet, state, chain[:chain_index])
            request = _read_value(artifacts, attempt['request'])
            supplied = packets_from_request(request)
            legacy._require(supplied and supplied[-1] == packet, DELIVERY_INVALID_MESSAGE, 'review_parts_delivery_invalid')
            inventory = _read_series_inventory(artifacts, header['inventory'], header['inventory_digest'])
            delta = validate_turn_output(records, artifacts, assignment, packet, observed['output'], attempt=attempt)
            observation = dict(packet=packet, output=observed['output'], inventory=inventory, delta=delta,
                receipt=dict(turn=header['turn'], kind=header['kind'], assignment_id=assignment['id'],
                             attempt_id=attempt['id'], output_digest=observed['output_digest']),
                output_tokens=(attempt.get('usage') or {}).get('output_tokens'))
            if attempt.get('material'):
                observation['material'] = _read_value(artifacts, attempt['material'])
            return observation
        observation = artifacts.once(('review_parts_v3_turn', assignment['id'], packet['digest']), read_turn)
        if observation is not None:
            observations.append(observation)
            advance_series_state(state, observation)
    return observations


def _list_packet_fragments(packet, inventory):
    if packet.get(legacy.KEY, {}).get('delivery_projection') or legacy.KEY not in packet:
        return []
    if packet[legacy.KEY]['kind'] == 'read':
        required = dict(inventory, units=[row for row in inventory['units'] if row['required']])
        return read_material_texts(required, _read_interval(packet), packet[MATERIAL_KEY], packet[CONTEXT_KEY])
    return copy.deepcopy(packet.get('review_parts_requests', []))


def _find_stored_entry_unit(state, handle):
    entries = handle.endswith('/entries')
    identifier = handle[:-8] if entries else handle
    entry = next((entry for entry in state['cards'] + state['combinations'] + state['findings']
                  if entry['id'] == identifier), None)
    if entry is None and identifier in state.get('final_catalog', {}):
        entry = dict(mode='condense', block=state['final_blocks'][identifier], output=state['final_catalog'][identifier])
    if entry is None and identifier in state.get('final_entries', {}):
        entry = dict(output=state['final_entries'][identifier])
    if entry is None or entries and entry.get('mode') != 'condense':
        return None
    text = legacy.render_json(entry['block'] if entries else entry['output'])
    return dict(handle=handle, text=text, components=[dict(kind='source', start=0, end=len(text),
        artifact=describe_artifact(text.encode('utf-8'), 'application/json'))])


def read_series_delivery(records, artifacts, assignment_id=None, *, packets=None, inventory=None):
    """Project only actually delivered text; inventory bytes never grant credit."""
    from .review_delivered_evidence import project_delivery_view
    if packets is None:
        packets = [row['packet'] for row in observe_series(records, artifacts, assignment_id)]
    if not packets:
        return project_delivery_view(records, artifacts, [])
    header = packets[-1][legacy.KEY]
    inventory = inventory or _read_series_inventory(artifacts, header['inventory'], header['inventory_digest'])
    logical = legacy.read_logical_packet(artifacts, header)
    if all(packet.get(legacy.KEY, {}).get('delivery_projection') for packet in packets):
        delivery = project_delivery_view(records, artifacts, packets)
        delivery.update(actual_packets=packets, wire_packets=[], read_packets=packets, inventory=inventory,
                        segments=[entry for packet in packets for entry in packet.get('evidence', [])])
        return delivery
    units = {row['handle']: row for row in inventory['units']}
    fragments, projected = {}, []
    for packet in packets:
        if packet.get(legacy.KEY, {}).get('delivery_projection') or legacy.KEY not in packet:
            projected.append(copy.deepcopy(packet))
            continue
        projection = copy.deepcopy(packet)
        projection[legacy.KEY]['delivery_projection'] = True
        projection = _restore_collection_shapes(projection, logical)
        projection['evidence'] = []
        prior_state = _read_value(artifacts, packet[legacy.KEY]['state'], packet[legacy.KEY]['state_digest'])
        for fragment in _list_packet_fragments(packet, inventory):
            row = units.get(fragment['unit']) or _find_stored_entry_unit(prior_state, fragment['unit'])
            if row is None:
                continue
            legacy._require(row['text'][fragment['start']:fragment['end']] == fragment['text'],
                            DELIVERY_INVALID_MESSAGE, 'review_parts_delivery_invalid')
            if row['handle'] in units:
                fragments.setdefault(row['handle'], []).append(fragment)
            for component in row['components']:
                if component.get('encoding', 'utf-8') != 'utf-8':
                    continue
                begin, end = max(fragment['start'], component['start']), min(fragment['end'], component['end'])
                if begin >= end:
                    continue
                content = row['text'][component['start']:component['end']]
                if component['kind'] == 'item':
                    entry = legacy._make_segment(row, content, begin - component['start'], end - component['start'])
                else:
                    entry = legacy._make_segment(row, content, begin - component['start'], end - component['start'],
                        body=dict(artifact=component['artifact']))
                projection['evidence'].append(entry)
        for rows in packet.get(legacy.STATE_KEY, {}).values():
            for entry in rows:
                text = legacy.render_json(entry)
                projection['evidence'].append(dict(artifact=describe_artifact(text.encode('utf-8'), 'application/json'),
                                                   encoding='utf-8', content=text))
        for argument in prior_state['material_state']['arguments'].values():
            if argument['complete']:
                data = b''.join(artifacts.read(part) for part in argument['parts'])
                projection['evidence'].append(dict(artifact=describe_artifact(data, 'text/plain; charset=utf-8'),
                    encoding='utf-8', content=data.decode('utf-8')))
        projected.append(projection)
    items = {}
    for handle, entries in fragments.items():
        row = units[handle]
        if not row['required'] or row['kind'] == 'reference':
            continue
        component = row['components'][0]
        spans = [[entry['start'], entry['end']] for entry in entries]
        if legacy._find_gaps(component['end'], spans):
            continue
        # Compare rebuilt bytes, not just the first/last/count of a group.
        text = row['text'][:component['end']]
        item = json.loads(text)
        legacy._require(digest(item) == row['digest'], DELIVERY_INVALID_MESSAGE, 'review_parts_delivery_invalid')
        items[handle] = item
    restored = legacy.build_frame(logical, legacy.enumerate_units(logical, inventory['adapter'], protocol=PROTOCOL))
    restored['evidence'] = list(_list_binary_entries(inventory, fragments))
    collections = {}
    for occurrence, item in legacy._rebuild_occurrences(items, inventory):
        path = tuple(occurrence['path'])
        address = legacy._find_address(occurrence)
        collections.setdefault(path, {})[address] = copy.deepcopy(item)
    for path, entries in sorted(collections.items(), key=lambda pair: len(pair[0])):
        collection = legacy._find_collection(restored, list(path))
        if isinstance(collection, list):
            collection[:] = [entries[index] for index in sorted(entries)]
        else:
            collection.update(entries)
    restored[legacy.KEY] = dict(header, delivery_projection=True, units=inventory['units'], delivered_items=[])
    handles_by_digest = {digest(item): handle for handle, item in items.items()}
    for path, entries in collections.items():
        for position, address in enumerate(sorted(entries)):
            handle = handles_by_digest.get(digest(entries[address]))
            if handle:
                restored[legacy.KEY]['delivered_items'].append(dict(unit=handle, path=list(path), digest=units[handle]['digest'],
                    **(dict(index=address, position=position) if isinstance(address, int) else dict(key=address))))
    restored['digest'] = digest({key: value for key, value in restored.items() if key != 'digest'})
    projected.insert(0, restored)
    delivery = project_delivery_view(records, artifacts, projected)
    delivery.update(actual_packets=projected, wire_packets=packets, read_packets=projected,
                    segments=[entry for packet in projected for entry in packet['evidence']], inventory=inventory)
    return delivery


def verify_series(records, artifacts, final_assignment_id):
    issues, observations = [], []
    try:
        observations = observe_series(records, artifacts, final_assignment_id)
        chain = list_series(records, artifacts, final_assignment_id)
        verify_final_page_plans(records, artifacts, chain)
        selected = {row['receipt']['assignment_id'] for row in observations}
        if any(assignment['id'] not in selected and not any(
                (later.get('capacity_replacement_of') or later.get('context', {}).get('output_refusal', {}).get('assignment_id'))
                == assignment['id'] for later, _ in chain)
                for assignment, _ in chain):
            issues.append(dict(code='unobserved_turn'))
        if not observations or observations[-1]['packet'][legacy.KEY]['kind'] != 'final' or (
                observations[-1]['output'].get('kind') == 'requests' or observations[-1]['output'].get('stage') == 'material'):
            issues.append(dict(code='final_pending'))
        if observations:
            inventory = observations[-1]['inventory']
            state = build_series_state(observations)
            if state['pending_requests']:
                issues.append(dict(code='requested_range_not_delivered'))
            cursor = state['cursor']
            required = [row for row in inventory['units'] if row['required']]
            if required and (not cursor or cursor['unit_index'] != len(required) or cursor['unit_offset'] != 0):
                issues.append(dict(code='unit_not_delivered'))
            packets = [row['packet'] for row in observations]
            delivery = read_series_delivery(records, artifacts, packets=packets, inventory=inventory)
            if 'page_plan_digest' in packets[-1][legacy.KEY]:
                from .review_composite import read_final_output
                output, composite = read_final_output(records, artifacts, final_assignment_id)
                if composite is None:
                    issues.append(dict(code='composite_missing'))
            return dict(complete=not issues, packets=packets, delivery=delivery, state=state,
                        issues=dict(count=len(issues), examples=issues[:20]))
    except (ResearchError, ValueError, KeyError) as error:
        issues.append(dict(code='series_invalid', reason=getattr(error, 'code', str(error))))
    return dict(complete=False, packets=[row['packet'] for row in observations], delivery=None,
                issues=dict(count=len(issues), examples=issues[:20]))


def _restore_collection_shapes(value, original):
    if isinstance(original, list) and not isinstance(value, list):
        return []
    if isinstance(value, dict) and isinstance(original, dict):
        return {name: _restore_collection_shapes(item, original[name]) if name in original else item
                for name, item in value.items()}
    return value


def build_handle_packet(packet, artifacts, assignment):
    """Build a transient handle table from the bound inventory and state artifacts."""
    from .source_links import span_locator
    header = packet[legacy.KEY]
    inventory = _read_series_inventory(artifacts, header['inventory'], header['inventory_digest'])
    state = _read_value(artifacts, header['state'], header['state_digest'])
    table = {}
    current_parts = {}
    for fragment in _list_packet_fragments(packet, inventory):
        current_parts.setdefault(fragment['unit'], []).append(fragment)
    for row in inventory['units']:
        if row['kind'] == 'reference':
            continue
        text = row['text']
        component = next((part for part in row['components'] if part['kind'] in ('body', 'source')), None)
        span_components = []
        bare_source = header['adapter'] == 'study_verification'
        if component is not None:
            if component.get('encoding', 'utf-8') != 'utf-8':
                table[row['handle']] = dict(reference=component['artifact'])
                continue
            content = text[component['start']:component['end']]
            artifact = component['artifact']
            link = dict(artifact=artifact, locator=span_locator(content, 0, len(content), excerpt_length=0))
            identity = row.get('source', {}).get('identity', {})
            link.update({key: identity[key] for key in ('version_id', 'source_id') if key in identity})
            citations = [citation.get('link', citation) for citation in row['citations'] if isinstance(citation, dict)]
            native = next((citation for citation in citations if
                citation.get('artifact', {}).get('sha256') == artifact['sha256']), {})
            link.update({key: native[key] for key in ('version_id', 'source_id') if key in native})
            reference = dict(kind='source', link=link) if {'version_id', 'source_id'} <= link.keys() else artifact
            if reference.get('kind') == 'source':
                span_components.append(dict(start=component['start'], end=component['end'],
                    text=content, reference=copy.deepcopy(reference)))
                if bare_source:
                    reference = reference['link']
        elif row['citations']:
            reference = row['citations'][0]
        else:
            artifact = describe_artifact(text.encode('utf-8'), 'application/json')
            reference = artifact
        handle_entry = dict(reference=copy.deepcopy(reference), text=text, store_text=component is None,
                            artifact_spans=True, span_components=span_components, bare_source_link=bare_source)
        parts = {}
        delivered_parts = list(state['delivered_parts'].get(row['handle'], []))
        delivered_parts.extend(current_parts.get(row['handle'], []))
        for delivered in delivered_parts:
            start, end = delivered['start'], delivered['end']
            if start >= end:
                continue
            part = len(parts) + 1
            parts[str(part)] = dict(handle_entry, text=text[start:end], unit_offset=start, part=part, cite_part_span=True,
                                   store_text=False)
        if parts:
            handle_entry['parts'] = parts
        table[row['handle']] = handle_entry
    for row in state['cards'] + state['combinations'] + state['findings']:
        table[row['id']] = dict(reference=describe_artifact(legacy.render_json(row['output']).encode('utf-8'), 'application/json'))
        if row.get('mode') == 'condense':
            table[row['id'] + '/entries'] = dict(reference=describe_artifact(
                legacy.render_json(row['block']).encode('utf-8'), 'application/json'))
    for handle, value in state.get('final_catalog', {}).items():
        table[handle] = dict(reference=describe_artifact(legacy.render_json(value).encode('utf-8'), 'application/json'))
        table[handle + '/entries'] = dict(reference=describe_artifact(
            legacy.render_json(state['final_blocks'][handle]).encode('utf-8'), 'application/json'))
    for handle, value in {**state.get('final_entries', {}), **read_final_entry_fragments(packet)}.items():
        table[handle] = dict(reference=describe_artifact(legacy.render_json(value).encode('utf-8'), 'application/json'))
    for index, target in enumerate(header['targets'], 1):
        table['t' + str(index)] = dict(reference=target['id'])
    for handle, argument in state['material_state']['arguments'].items():
        if argument['complete']:
            text = b''.join(artifacts.read(part) for part in argument['parts'])
            table[handle] = dict(reference=describe_artifact(text, 'text/plain; charset=utf-8'))
    if header['adapter'] == 'native_math':
        pending = list(table.values())
        while pending:
            entry = pending.pop()
            reference = entry['reference']
            if isinstance(reference, dict):
                artifact = reference.get('link', {}).get('artifact', reference)
                entry['reference'] = artifact['sha256']
            entry['native_digest'] = True
            entry['artifact_spans'] = True
            pending.extend(entry.get('parts', {}).values())
    return dict(packet, handle_table=table, capacity=header['capacity'],
                material_state=copy.deepcopy(state['material_state']))


def covers_items(packets, expected, key, inventory, *, path=None):
    """Use authenticated interval bytes and the complete occurrence inventory."""
    path = ['problem_evidence', key] if path is None else path
    target = expected[key]
    fragments = {}
    for packet in packets:
        if packet.get(legacy.KEY, {}).get('protocol') != PROTOCOL:
            continue
        for fragment in _list_packet_fragments(packet, inventory):
            fragments.setdefault(fragment['unit'], []).append(fragment)
    items = {}
    projected = [packet for packet in packets if packet.get(legacy.KEY, {}).get('delivery_projection')]
    if projected:
        primary = dict(inventory, units=[row for row in inventory['units'] if row['required'] and
                       row['components'] and row['components'][0]['kind'] == 'item'])
        items.update(legacy._reconstruct_items(projected, primary))
    for row in inventory['units']:
        if row['handle'] not in fragments or not row['components'] or row['components'][0]['kind'] != 'item':
            continue
        end = row['components'][0]['end']
        if not legacy._find_gaps(end, [[part['start'], part['end']] for part in fragments[row['handle']]]):
            item = json.loads(row['text'][:end])
            if digest(item) == row['digest']:
                items[row['handle']] = item
    delivered = {legacy._find_address(occurrence): item for occurrence, item in
                 legacy._rebuild_occurrences(items, inventory) if occurrence['path'] == path}
    return all(index in delivered and digest(delivered[index]) == digest(item)
               for index, item in (enumerate(target) if isinstance(target, list) else target.items()))


def list_native_citation_digests(records, artifacts, assignment, current):
    """Native acceptance cites authenticated originals and recorded reviewer material."""
    from .review_handles import expand_handles
    from .review_answer_recovery import recover_answer
    from .review_transport import response_output
    packet = _read_value(artifacts, assignment['packet'])
    resolved = build_handle_packet(packet, artifacts, assignment)
    allowed = {entry['reference'] for entry in resolved['handle_table'].values()
               if isinstance(entry['reference'], str) and len(entry['reference']) == 64}
    attempt = records['review_attempt'][current['attempt_id']]
    response = _read_value(artifacts, attempt['response'])
    try:
        raw = response_output(response)
    except json.JSONDecodeError:
        raw = recover_answer(response)['output']
    pending = [raw]
    while pending:
        value = pending.pop()
        if isinstance(value, dict):
            if 'ref' in value:
                reference = expand_handles(resolved, value, artifacts=artifacts, assignment=assignment)
                if isinstance(reference, str) and len(reference) == 64:
                    allowed.add(reference)
            else:
                pending.extend(value.values())
        elif isinstance(value, list):
            pending.extend(value)
    return sorted(allowed)


def build_replacement(records, artifacts, assignment, *, outcome=None, identifier=None, notice=None):
    """Keep the route and currency; reduce read levels or whole-entry blocks."""
    from .review_preflight import build_report, check_request
    from .review_transport import request_body
    packet = _read_value(artifacts, assignment['packet'])
    header = packet[legacy.KEY]
    plan = capacity.read_chain_capacity_plan(artifacts, assignment)
    route = records['review_route'][assignment['route_id']]
    inventory = _read_series_inventory(artifacts, header['inventory'], header['inventory_digest'])
    state = _read_value(artifacts, header['state'], header['state_digest'])
    logical = legacy.read_logical_packet(artifacts, header)
    chain = list_series(records, artifacts, assignment['id'])
    limits = {kind: capacity.derive_plan_task_limits(plan, kind,
        _derive_observations(records, artifacts, chain, kind)) for kind in legacy.KINDS}
    kind = header['kind']
    carried_notice = notice if notice is not None else packet.get('output_refusal')
    reduction = None
    if notice is None:
        if kind == 'read' and carried_notice is not None:
            limits[kind] = capacity.derive_plan_task_limits(plan, kind,
                _derive_observations(records, artifacts, chain, kind),
                notice_characters=capacity.measure_capacity_value(route, carried_notice))
        if kind == 'final' and state['material_state']['pending']:
            limits['final'].update(copy.deepcopy(header['capacity']))
            limits['final']['answer_bound'] = max(1, header['capacity']['answer_bound'] // 2)
        if kind == 'read' and limits[kind]['material_allowance'] >= header['capacity']['material_allowance']:
            return None
        if kind in ('combine', 'check') and 'page_plan_digest' not in header:
            # The old state remains intact; only this turn's block is divided.
            old_room = header.get('block', {}).get('characters', header.get('block_characters', header['capacity']['material_allowance']))
            reduction = dict(kind=kind, room=max(1, old_room // 2))
    else:
        if kind == 'read':
            limits[kind] = capacity.derive_plan_task_limits(plan, kind,
                _derive_observations(records, artifacts, chain, kind),
                notice_characters=capacity.measure_capacity_value(route, notice))
        else:
            limits[kind] = copy.deepcopy(plan['task'][kind])
            limits[kind].update(copy.deepcopy(header['capacity']))
    binding = {name: copy.deepcopy(header[name]) for name in ('adapter', 'series_assignment_id', 'series_key',
        'plan_digest', 'inventory', 'inventory_digest', 'logical_packet', 'logical_packet_digest', 'state', 'state_digest')}
    binding['_targets'] = header['targets']
    if 'entry' in header:
        binding['entry'] = header['entry']
    if 'replaces_assignment_id' in header:
        binding['replaces_assignment_id'] = header['replaces_assignment_id']
    replacement = plan_series_turn(route, artifacts, logical, inventory, plan, state, binding=binding,
                                   limits_by_kind=limits, refusal_notice=carried_notice, replacement=reduction,
                                   paged_final=kind == 'final' or 'page_plan_digest' in header or
                                       plan['allocation'].get('final_form') == 'pages' or bool(state.get('final_progress')))
    replacement = prepare_series_final(records, artifacts, assignment, route, logical, inventory, plan, state,
        limits, replacement, split=notice is None and 'page_plan_digest' in header and not state['material_state']['pending']
        or notice is None and kind == 'final' and not state['material_state']['pending'], keep_level=notice is not None)
    if replacement is None:
        return None
    from .review_prompt_paragraphs import read_paragraph_parameters
    _store_delivery_fragments(artifacts, replacement, inventory)
    _store_context_entries(artifacts, replacement)
    base = dict(assignment['prompt']['parameters']['base_prompt']['parameters'],
                **read_paragraph_parameters(assignment['prompt']))
    prompt = legacy.build_turn_prompt(assignment['role'], base, replacement[legacy.KEY])
    if carried_notice is not None:
        from . import review_prompt_paragraphs as paragraphs
        parameters = paragraphs.read_paragraph_parameters(prompt)
        stripped = paragraphs.strip_prompt_paragraphs(prompt, assignment['role'])
        versions = [version for version in parameters.get(paragraphs.PARAMETER, [])
                    if version not in paragraphs.REFUSAL_PARAGRAPH_VERSIONS.values()]
        prompt = paragraphs.append_prompt_paragraphs(stripped, assignment['role'],
            parameters.get(paragraphs.CARRY_PARAMETER), versions + [paragraphs.REFUSAL_PARAGRAPH_VERSIONS[carried_notice['form']]],
            parameters.get(paragraphs.CAPACITY_PARAMETER))
    request = request_body(route, prompt['text'], replacement, [], order=())
    preflight = check_request(build_report(records, artifacts, route, assignment['role'], prompt, replacement,
        request, capacity_plan=plan, turn_kind=replacement[legacy.KEY]['kind']), request, packet=replacement, artifacts=artifacts)
    return dict(packet=replacement, prompt=prompt, preflight=preflight,
                payload=dict({key: copy.deepcopy(assignment[key]) for key in
                    ('role', 'route_id', 'dossier_id', 'reviewer_id', 'author_id')},
                    id=identifier or assignment['id'] + '-replacement',
                    context=dict(capacity_replacement=assignment['id'])))


def plan_turn_recovery(records, artifacts, assignment, outcome):
    from .review_staged_protocol import ControllerArtifacts
    try:
        replacement = build_replacement(records, ControllerArtifacts(artifacts), assignment, outcome=outcome)
    except ResearchError:
        return None
    if replacement is None:
        return None
    return dict(kind='replace', reason='capacity', payload=replacement['payload'],
                preflight=replacement['preflight'], details=dict(handler='parts_v3', outcome=outcome))


def prepare_capacity_assignment(records, artifacts, value, revision):
    from .operations import immutable_record, normalized_text
    from .review_protocol import find_replacement
    identifier = value['context']['capacity_replacement']
    prior = records['review_assignment'][identifier]
    legacy._require(all(value[key] == prior[key] for key in ('role', 'route_id', 'dossier_id')) and
        all(normalized_text(value[key]) == normalized_text(prior[key]) for key in ('reviewer_id', 'author_id')),
        'The capacity replacement keeps its route, role, reviewer and author.', 'review_source_continuation_mismatch')
    legacy._require(find_replacement(records, prior) is None, 'Continue the selected replacement.',
                    'review_source_continuation_fork')
    from .review_failure_class import classify_attempt
    from .review_protocol import read_filtered_records
    attempts = list(read_filtered_records(records, 'review_attempt', assignment_id=identifier).values())
    outcomes = [classify_attempt(records, artifacts, attempt) for attempt in attempts]
    legacy._require(outcomes and all(row is not None for row in outcomes), 'The turn has no capacity failure.', 'invalid_review_context')
    replacement = build_replacement(records, artifacts, prior, outcome=outcomes[-1]['outcome'], identifier=value['id'])
    legacy._require(replacement is not None, 'This turn has no smaller task.', 'invalid_review_context')
    packet, prompt = replacement['packet'], replacement['prompt']
    record = dict(copy.deepcopy(prior), id=value['id'], packet=_put_value(artifacts, packet),
        packet_digest=packet['digest'], prompt=prompt, prompt_hash=prompt['rendered_prompt_hash'],
        history=[], recorded_revision=revision, capacity_replacement_of=identifier)
    record[legacy.KEY] = {key: packet[legacy.KEY][key] for key in
        ('protocol', 'adapter', 'kind', 'series_assignment_id', 'turn', 'inventory_digest')}
    record['capacity_plan_kind'] = packet[legacy.KEY]['kind']
    record['review_parts_plan'] = legacy.describe_plan(packet)
    return [immutable_record(records, 'review_assignment', value['id'], record)], record


def prepare_refusal_assignment(records, artifacts, value, revision, prior, packet, refused_ids, diagnoses):
    from .review_refusal_notice import build_refusal_notice
    from .operations import immutable_record
    route = records['review_route'][prior['route_id']]
    for form in ('whole', 'bounded'):
        notice = build_refusal_notice(records, artifacts, refused_ids, form=form, diagnoses=diagnoses,
            measure=lambda value: capacity.measure_capacity_value(route, value), enforce_bounded_limit=True)
        try:
            replacement = build_replacement(records, artifacts, prior, identifier=value['id'], notice=notice)
            break
        except ResearchError as error:
            if form == 'bounded' or error.code not in ('review_input_too_large', 'review_route_capacity_plan_violated'):
                raise
    packet, prompt = replacement['packet'], replacement['prompt']
    record = dict(copy.deepcopy(prior), id=value['id'], context=copy.deepcopy(value['context']),
        packet=_put_value(artifacts, packet), packet_digest=packet['digest'], prompt=prompt,
        prompt_hash=prompt['rendered_prompt_hash'], history=[], recorded_revision=revision)
    record.pop('capacity_replacement_of', None)
    record[legacy.KEY] = {key: packet[legacy.KEY][key] for key in
        ('protocol', 'adapter', 'kind', 'series_assignment_id', 'turn', 'inventory_digest')}
    record['review_parts_plan'] = legacy.describe_plan(packet)
    return [immutable_record(records, 'review_assignment', value['id'], record)], record


def _validate_replayed_turn(records, artifacts, assignment, packet, state, earlier):
    header = packet[legacy.KEY]
    plan = capacity.read_chain_capacity_plan(artifacts, assignment)
    inventory = _read_series_inventory(artifacts, header['inventory'], header['inventory_digest'])
    route = records['review_route'][assignment['route_id']]
    limits = {kind: capacity.derive_plan_task_limits(plan, kind,
        _derive_observations(records, artifacts, earlier, kind)) for kind in legacy.KINDS}
    origin = records.get('review_assignment', {}).get(assignment.get('capacity_replacement_of'))
    if packet.get('output_refusal') and (origin is None or header['kind'] == 'read'):
        if header['kind'] == 'read':
            limits['read'] = capacity.derive_plan_task_limits(plan, 'read',
                _derive_observations(records, artifacts, earlier, 'read'),
                notice_characters=capacity.measure_capacity_value(route, packet['output_refusal']))
        else:
            limits[header['kind']] = dict(limits[header['kind']], **header['capacity'])
    binding = {name: copy.deepcopy(header[name]) for name in ('adapter', 'series_assignment_id', 'series_key',
        'plan_digest', 'inventory', 'inventory_digest', 'logical_packet', 'logical_packet_digest', 'state', 'state_digest')}
    binding['_targets'] = header['targets']
    if 'entry' in header:
        binding['entry'] = header['entry']
    if 'replaces_assignment_id' in header:
        binding['replaces_assignment_id'] = header['replaces_assignment_id']
    logical = legacy.read_logical_packet(artifacts, header)
    reduction = None
    if origin is not None:
        old = _read_value(artifacts, origin['packet']).get(legacy.KEY, {})
        if old.get('kind') in ('combine', 'check') and 'page_plan_digest' not in old:
            old_room = old.get('block', {}).get('characters', old.get('block_characters', old['capacity']['material_allowance']))
            reduction = dict(kind=old['kind'], room=max(1, old_room // 2))
        elif old.get('kind') == 'final' and state['material_state']['pending']:
            limits['final'].update(old['capacity'])
            limits['final']['answer_bound'] = max(1, old['capacity']['answer_bound'] // 2)
    expected = plan_series_turn(route, artifacts, logical, inventory, plan, state, binding=binding,
        limits_by_kind=limits, refusal_notice=packet.get('output_refusal'), replacement=reduction,
        paged_final='page_plan_digest' in header or plan['allocation'].get('final_form') == 'pages' or
            bool(state.get('final_progress')) or origin is not None and old.get('kind') == 'final')
    historical = dict(records, review_attempt={key: row for key, row in records.get('review_attempt', {}).items()
        if row['recorded_revision'] < assignment['recorded_revision']})
    prior = earlier[-1][0] if earlier else None
    expected = prepare_series_final(historical, artifacts, prior, route, logical, inventory, plan, state,
        limits, expected, split=origin is not None and
        (old.get('kind') == 'final' or 'page_plan_digest' in old) and not state['material_state']['pending'],
        keep_level=bool(packet.get('output_refusal')) and origin is None)
    for key in (set(packet) | set(expected)) - {'digest'}:
        legacy._require(packet.get(key) == expected.get(key), DELIVERY_INVALID_MESSAGE,
                        'review_parts_delivery_invalid', member=key)
    if hasattr(artifacts, 'once'):
        artifacts.once(('parts_v3_inventory', header['inventory_digest']),
                       lambda: _validate_frozen_inventory(logical, inventory))
    else:
        _validate_frozen_inventory(logical, inventory)


def _validate_frozen_inventory(logical, inventory):
    enumerated = legacy.enumerate_units(logical, inventory['adapter'], protocol=PROTOCOL)
    rows = [row for row in inventory['units'] if row['required'] and row['kind'] == 'record']
    legacy._require(len(rows) == len(enumerated), DELIVERY_INVALID_MESSAGE, 'review_parts_inventory_changed')
    by_digest = {row['digest']: row for row in rows}
    for unit in enumerated:
        row = by_digest.get(unit['digest'])
        legacy._require(row is not None and all(row[key] == legacy._describe_unit(unit)[key]
                        for key in ('digest', 'occurrences', 'citations', 'bodies')),
                        DELIVERY_INVALID_MESSAGE, 'review_parts_inventory_changed')
    # Rebuild every wrapper from the complete, digest-bound reference lists.
    items = {row['handle']: json.loads(row['text'][:row['components'][0]['end']]) for row in rows}
    rebuilt = list(legacy._rebuild_occurrences(items, dict(inventory, units=rows)))
    legacy._require(len(rebuilt) == sum(len(row['occurrences']) for row in rows),
                    DELIVERY_INVALID_MESSAGE, 'review_parts_inventory_changed')
    for occurrence, item in rebuilt:
        original = legacy._find_collection(logical, occurrence['path'])[legacy._find_address(occurrence)]
        legacy._require(digest(item) == digest(original), DELIVERY_INVALID_MESSAGE, 'review_parts_inventory_changed')
    frame = legacy.build_frame(logical, enumerated)
    for row in inventory['units']:
        if row['kind'] == 'frame':
            item = json.loads(row['text'][:row['components'][0]['end']])
            legacy._require(digest(item) == row['digest'] == digest(_find_path(frame, row['frame_path'])),
                            DELIVERY_INVALID_MESSAGE, 'review_parts_inventory_changed')
    return True


def has_packet_binding(packet, adapter):
    from .artifacts import validate_reference
    header = packet.get(legacy.KEY, {})
    if not (header.get('protocol') == PROTOCOL and header.get('adapter') == adapter and
            header.get('kind') in legacy.KINDS and type(header.get('turn')) is int and header['turn'] >= 1 and
            isinstance(header.get('series_assignment_id'), str) and header['series_assignment_id'] and
            packet.get('digest') == digest({key: value for key, value in packet.items() if key != 'digest'})):
        return False
    try:
        for name in ('inventory', 'logical_packet', 'state'):
            validate_reference(header[name])
            if header[name]['sha256'] != header[name + '_digest']:
                return False
    except (ResearchError, KeyError):
        return False
    return True


def list_bearing_references(series, references):
    from .development import _REVIEW_CHECKS
    candidates = {digest(reference): reference for reference in references}
    credited = set()
    inventory = series['delivery']['inventory']
    for card in series['state']['cards']:
        if not any(row['target'] in _REVIEW_CHECKS for row in card['output']['bearing']):
            continue
        group = card['group']
        for row, start, end in _list_interval_units(inventory, group['first'], group['last']):
            credited.update(digest(reference) for reference in row['citations'] if digest(reference) in candidates)
    return [candidates[key] for key in sorted(credited)]


def validate_deliveries(packet, inventory):
    if packet[legacy.KEY]['kind'] == 'read':
        required = dict(inventory, units=[row for row in inventory['units'] if row['required']])
        read_material_texts(required, _read_interval(packet), packet[MATERIAL_KEY], packet[CONTEXT_KEY])
    else:
        rows = {row['handle']: row for row in inventory['units']}
        for entry in packet.get('review_parts_requests', []):
            row = rows.get(entry['unit'])
            if row is not None:
                legacy._require(row['text'][entry['start']:entry['end']] == entry['text'],
                                DELIVERY_INVALID_MESSAGE, 'review_parts_delivery_invalid')


def _select_check_work(route, inventory, pending, room):
    selected, scopes, deliveries = [], [], []
    for combination in pending[:8]:
        candidate = scopes + _resolve_requests(inventory,
            dict(unit_requests=combination['output']['read'], source_requests=[]))
        combined_room = capacity.measure_capacity_value(route,
            [dict(row['output'], id=row['id']) for row in selected + [combination]])
        supplied = _deliver_requested_units(route, inventory, candidate, room - combined_room)
        if len(supplied) != len(candidate) or any(
                delivery['unit'] != scope['unit'] or delivery['start'] != scope['start'] or delivery['end'] != scope['end']
                for delivery, scope in zip(supplied, candidate)):
            break
        selected.append(combination)
        scopes, deliveries = candidate, supplied
    legacy._require(bool(selected), 'The pending scientific check does not fit its pinned level.',
                    'review_parts_no_progress')
    return selected, deliveries


def _verify_requested_statements(packet, statements, inventory, used):
    entries = []
    units = {row['handle']: row for row in inventory['units']}
    for delivery in packet.get('review_parts_requests', []):
        row = units[delivery['unit']]
        for component in row['components']:
            start, end = max(delivery['start'], component['start']), min(delivery['end'], component['end'])
            if start >= end:
                continue
            text = row['text'][component['start']:component['end']]
            if component['kind'] == 'item':
                entries.append(legacy._make_segment(row, text, start - component['start'], end - component['start']))
            else:
                entries.append(legacy._make_segment(row, text, start - component['start'], end - component['start'],
                                                    body=dict(artifact=component['artifact'])))
    projection = dict(evidence=entries, review_parts=dict(delivered_items=[]))
    verified, unmatched = [], 0
    from .operations import fields
    for statement in statements:
        fields(statement, ('unit', 'text', 'why'), code='invalid_value_review')
        row = units.get(statement['unit'])
        legacy._require(row is not None, 'A statement names a known unit.')
        matched, count, used = legacy._verify_statements(projection, row['handle'],
            [dict(text=statement['text'], why=statement['why'])], dict(units=[row]), used)
        verified.extend(dict(entry, unit=row['handle']) for entry in matched)
        unmatched += count
    return verified, unmatched, used


def enumerate_packet_units(packet, adapter, previous=None):
    """Keep v2 unit rules and ordering with linear reference-list membership."""
    from .review_parts_roles import get_adapter
    definition = get_adapter(adapter)
    rule = legacy._find_unit_rule(adapter, previous)
    enumerate_sources = definition['enumerate_record_units'] if rule else definition['enumerate_units']
    units, by_identity, occurrences, citations = [], {}, {}, {}
    for source in enumerate_sources(packet):
        identity = legacy._find_unit_identity(source)
        unit = by_identity.get(identity)
        if unit is None:
            unit = dict(item=copy.deepcopy(source['item']), digest=digest(source['item']),
                        occurrences=[], citations=[], bodies=[])
            for key in ('record_identity', 'carded_under'):
                if key in source:
                    unit[key] = copy.deepcopy(source[key])
            units.append(unit)
            by_identity[identity] = unit
            occurrences[identity], citations[identity] = set(), set()
        refs = definition['citations'](source) if 'citations' in definition else source.get('citations', [])
        for row, seen, destination in ((row, citations[identity], unit['citations']) for row in refs):
            key = legacy.render_json(row)
            if key not in seen:
                seen.add(key)
                destination.append(copy.deepcopy(row))
        for row in source['occurrences']:
            key = legacy.render_json(row)
            if key not in occurrences[identity]:
                occurrences[identity].add(key)
                unit['occurrences'].append(copy.deepcopy(row))
        for body in source.get('bodies', []):
            existing = next((row for row in unit['bodies'] if row['artifact']['sha256'] == body['artifact']['sha256']), None)
            if existing is None:
                unit['bodies'].append(copy.deepcopy(body))
            elif existing['artifact'] != body['artifact']:
                existing.setdefault('aliases', []).append(copy.deepcopy(body['artifact']))
    earlier = (previous or {}).get('units', [])
    old = {legacy._find_unit_identity(row) for row in earlier}
    legacy._require(old <= by_identity.keys(), 'An earlier unit disappeared', 'review_parts_inventory_changed')
    units = [by_identity[legacy._find_unit_identity(row)] for row in earlier] + [
        unit for unit in units if legacy._find_unit_identity(unit) not in old]
    if rule:
        owners = {}
        for unit in sorted(units, key=lambda row: row.get('record_identity', {}).get('record_kind') != 'source'):
            bodies = []
            for body in unit['bodies']:
                identity = body['artifact']['sha256']
                if identity not in owners:
                    owners[identity] = body
                    bodies.append(body)
                else:
                    owner = owners[identity]
                    for reference in [body['artifact']] + body.get('aliases', []):
                        if reference != owner['artifact'] and reference not in owner.get('aliases', []):
                            owner.setdefault('aliases', []).append(copy.deepcopy(reference))
            unit['bodies'] = bodies
    handles = {legacy._find_unit_identity(unit): 'u' + str(index + 1) for index, unit in enumerate(units)}
    for unit in units:
        unit['handle'] = handles[legacy._find_unit_identity(unit)]
        unit['text'] = legacy.render_json(unit['item'])
        unit['item_characters'] = len(unit['text'])
        if unit.get('carded_under'):
            unit['carded_under'] = handles[unit['carded_under']]
        unit['characters'] = unit['item_characters'] + sum(len(legacy.render_json(body['content'])) for body in unit['bodies'])
    return units


def _read_series_inventory(artifacts, reference, expected_digest=None):
    value = _read_value(artifacts, reference, expected_digest)
    legacy._require(value.get('protocol') == PROTOCOL and isinstance(value.get('units'), list),
                    DELIVERY_INVALID_MESSAGE, 'review_parts_inventory_changed')
    return value


def has_series_attachment(artifacts, packets, reference):
    entries = []
    span = is_span_reference(reference)
    try:
        sought = artifacts.read(reference).decode('utf-8') if span else None
    except UnicodeDecodeError:
        return False
    if span and not sought:
        return False
    if span:
        for packet in packets:
            texts = [value for value in packet.get(MATERIAL_KEY, []) if isinstance(value, str)]
            texts.extend(row['text'] for row in packet.get('review_parts_requests', []))
            if any(sought in content for content in texts):
                return True
    received = {}
    for entry in _list_actual_segments(artifacts, packets):
        if legacy._read_entry_bytes(entry) is None:
            continue
        if span and entry.get('encoding') == 'utf-8':
            if sought in entry['content']:
                return True
            if entry.get('segment'):
                received.setdefault(entry['original_artifact']['sha256'], []).append(entry)
        if entry['artifact']['sha256'] == reference['sha256']:
            return entry['artifact']['size'] == reference['size']
        if entry.get('segment') and entry.get('original_artifact', {}).get('sha256') == reference['sha256']:
            entries.append(entry)
    if span:
        for fragments in received.values():
            text, through = '', 0
            for entry in sorted(fragments, key=lambda value: value['segment']['start']):
                start, end = entry['segment']['start'], entry['segment']['end']
                if start > through:
                    if sought in text:
                        return True
                    text = ''
                    through = start
                if end > through:
                    text += entry['content'][through - start:]
                    through = end
            if sought in text:
                return True
        return False
    if not entries:
        return False
    text = legacy._join_segments(entries, entries[0]['segment']['length'])
    return (text is not None and len(text.encode('utf-8')) == reference['size'] and
            hashlib.sha256(text.encode('utf-8')).hexdigest() == reference['sha256'])


def is_span_reference(reference):
    """A native artifact of exact delivered unit text, without invented source provenance."""
    return reference.get('media_type') == 'text/plain; profile=exactory-review-parts-span-v3'


def has_series_reference(artifacts, packets, reference):
    artifact = reference['link']['artifact'] if reference.get('kind') == 'source' else reference
    if has_series_attachment(artifacts, packets, artifact):
        return True
    locator = reference.get('link', {}).get('locator', {})
    if locator.get('kind') not in ('text', 'span'):
        return False
    position, end = locator['start'], locator['end']
    entries = [entry for entry in _list_actual_segments(artifacts, packets) if entry.get('segment')
               and entry.get('original_artifact', {}).get('sha256') == artifact['sha256']
               and legacy._read_entry_bytes(entry) is not None]
    fragments = []
    for entry in sorted(entries, key=lambda row: row['segment']['start']):
        span = entry['segment']
        if span['end'] <= position:
            continue
        if span['start'] > position:
            break
        stop = min(end, span['end'])
        fragments.append(entry['content'][position - span['start']:stop - span['start']])
        position = stop
        if position == end:
            text = ''.join(fragments)
            return (text == locator['quote'] if locator['kind'] == 'text' else
                    hashlib.sha256(text.encode('utf-8')).hexdigest() == locator['sha256'])
    return False


def _store_delivery_fragments(artifacts, packet, inventory):
    units = {row['handle']: row for row in inventory['units']}
    for fragment in _list_packet_fragments(packet, inventory):
        row = units.get(fragment['unit'])
        if row is None:
            state = _read_value(artifacts, packet[legacy.KEY]['state'], packet[legacy.KEY]['state_digest'])
            row = _find_stored_entry_unit(state, fragment['unit'])
        if row is None:
            continue
        for component in row['components']:
            if component.get('encoding', 'utf-8') != 'utf-8':
                continue
            start, end = max(fragment['start'], component['start']), min(fragment['end'], component['end'])
            if start < end:
                media = 'application/json' if component['kind'] == 'item' else component['artifact']['media_type']
                artifacts.put(row['text'][start:end].encode('utf-8'), media)


def _list_actual_segments(artifacts, packets):
    inventories = {}
    binary_units, binary_fragments = {}, {}
    for packet in packets:
        yield from packet.get('evidence', [])
        header = packet.get(legacy.KEY, {})
        if header.get('protocol') != PROTOCOL or header.get('delivery_projection') or MATERIAL_KEY not in packet:
            continue
        key = header['inventory_digest']
        if key not in inventories:
            inventories[key] = _read_series_inventory(artifacts, header['inventory'], key)
        inventory = inventories[key]
        units = {row['handle']: row for row in inventory['units']}
        state = None
        for fragment in _list_packet_fragments(packet, inventory):
            row = units.get(fragment['unit'])
            if row is None:
                if state is None:
                    state = _read_value(artifacts, header['state'], header['state_digest'])
                row = _find_stored_entry_unit(state, fragment['unit'])
            if row is None:
                continue
            for component in row['components']:
                if component.get('encoding', 'utf-8') != 'utf-8':
                    binary_units[row['handle']] = row
                    binary_fragments.setdefault(row['handle'], []).append(fragment)
                    continue
                start, end = max(fragment['start'], component['start']), min(fragment['end'], component['end'])
                if start >= end:
                    continue
                text = row['text'][component['start']:component['end']]
                body = None if component['kind'] == 'item' else dict(artifact=component['artifact'])
                yield legacy._make_segment(row, text, start - component['start'], end - component['start'], body=body)
    yield from _list_binary_entries(dict(units=list(binary_units.values())), binary_fragments)


def _list_binary_entries(inventory, fragments):
    for row in inventory['units']:
        for component in row['components']:
            if component.get('encoding', 'utf-8') == 'utf-8':
                continue
            spans = [[fragment['start'], fragment['end']] for fragment in fragments.get(row['handle'], [])]
            if legacy._find_gaps(component['end'], spans, start=component['start'], end=component['end']):
                continue
            entry = dict(artifact=component['artifact'], encoding=component['encoding'],
                         content=row['text'][component['start']:component['end']])
            legacy._require(legacy._read_entry_bytes(entry) is not None, DELIVERY_INVALID_MESSAGE,
                            'review_parts_delivery_invalid')
            yield entry


def _store_context_entries(artifacts, packet):
    for entries in packet[legacy.STATE_KEY].values():
        for entry in entries:
            _put_value(artifacts, entry)


# Final pages share the entry planner, condensing policy and composite protocol
# with v7. Only the series inventory, scientific findings and currency differ.
def read_final_currency(header):
    return dict(packet_digest=header['logical_packet_digest'], inventory_digest=header['inventory_digest'],
                series_key=copy.deepcopy(header['series_key']))


def read_final_entry_fragments(packet):
    return {row['id']: copy.deepcopy(row) for entries in packet.get(legacy.STATE_KEY, {}).values()
            for row in entries if isinstance(row, dict) and 'entry' in row}


def _advance_final_progress(state, observation):
    header = observation['packet'][legacy.KEY]
    currency = read_final_currency(header)
    if state.get('final_currency') != currency:
        state.update(final_currency=currency, final_progress={}, final_catalog={}, final_blocks={}, final_entries={})
    output = observation['output']
    terminal = output.get('kind') != 'requests' and output.get('stage') != 'material'
    state['final_progress'][str(header['final_turn'])] = dict(
        observation['receipt'], kind=header['final_form'], targets=header.get('page', {}).get('targets', []),
        terminal=terminal, output=copy.deepcopy(output))
    state['final_entries'].update(read_final_entry_fragments(observation['packet']))
    if header['final_form'] == 'combine' and terminal:
        state['final_catalog'][header['block']['handle']] = copy.deepcopy(output)
        state['final_blocks'][header['block']['handle']] = copy.deepcopy(header['block'])


def build_final_inputs(route, artifacts, packet, inventory, plan, state, limits, room):
    """Map cards and checked scientific findings to every target they bear on."""
    from . import review_condensing, review_final_pages
    header = packet[legacy.KEY]
    handles = plan['targets']['handles']
    target_handles = {row['id']: handle for row, handle in zip(header['targets'], handles)}
    catalog, pool = {}, []
    minimum = capacity.EXCERPT_TEXT_CHARACTERS + capacity.EXCERPT_FIELDS_CHARACTERS
    notes = UNIT_ROW_CHARACTERS + 1000 if state['pending_requests'] else 0
    # Keep fragment identities through level changes and pending requests. A
    # completed condensation must still apply when its page asks for originals.
    fragment_room = min(room, min(row['material_allowance'] for row in plan['task']['final']['levels']))
    request_reserve = UNIT_ROW_CHARACTERS + 1000
    pool_room = max(1, fragment_room - minimum - request_reserve)
    targets = [dict(handle=handle, entries=[], rows=[]) for handle in handles]
    retained = _list_state_entries(state)
    retained += [row for row in state['findings'] if row['id'] not in state['entries']]
    for entry in retained:
        value = copy.deepcopy(entry.get('carried', entry['output']))
        value['id'] = entry['id']
        catalog[entry['id']] = value
        bearing = value.get('bearing', value.get('combination_view', {}).get('bearing', []))
        if not bearing and value.get('combination_view', {}).get('units'):
            units = set(value['combination_view']['units'])
            bearing = []
            for card in state['cards']:
                interval = _list_interval_units(inventory, card['group']['first'], card['group']['last'])
                if any(unit['handle'] in units for unit, start, end in interval):
                    bearing.extend(card['output']['bearing'])
        selected = {target_handles.get(row['target'], row['target']) for row in bearing}
        # A checked finding inherits its scientific combination's bearing. Older
        # combinations and condensation summaries with no mapping remain common.
        common = not selected or set(handles) <= selected
        if common:
            destination = [pool]
        else:
            destination = [target['entries'] for target in targets if target['handle'] in selected]
        sizes, values = (split_final_entry(route, entry['id'], value, pool_room)
                        if common else
                        ([dict(handle=entry['id'], characters=capacity.measure_capacity_value(route, value) + 1)], {}))
        catalog.update(values)
        for entries in destination:
            entries.extend(copy.deepcopy(sizes))
    target_room = max(1, min(fragment_room - reserve - review_condensing.plan_condensing(
        pool, max(1, fragment_room - minimum - reserve))['remaining_characters']
        for reserve in (0, request_reserve)))
    for target in targets:
        original, target['entries'] = target['entries'], []
        for entry in original:
            sizes, values = split_final_entry(route, entry['handle'], catalog[entry['handle']], target_room)
            catalog.update(values)
            target['entries'].extend(sizes)
    inputs = dict(page_room=room, target_allowance=limits['target_allowance'], target_minimum=minimum,
        notes=notes,
        pool=pool, targets=targets, closing_room=room,
        row_characters=capacity.DECISION_ROW_CHARACTERS, condense=review_condensing.plan_condensing)
    return inputs, catalog


def split_final_entry(route, handle, value, room):
    """Segment an intact stored entry when it cannot fit a final condensing block."""
    size = capacity.measure_capacity_value(route, value) + 1
    if size <= room:
        return [dict(handle=handle, characters=size)], {handle: value}
    text = legacy.render_json(value)
    entries, catalog, start = [], {}, 0
    while start < len(text):
        def build(end):
            return dict(id=handle + '@' + str(start) + '-' + str(end), entry=handle,
                        start=start, end=end, text=text[start:end])
        low, high = start, len(text)
        while low < high:
            end = (low + high + 1) // 2
            if capacity.measure_capacity_value(route, build(end)) + 1 <= room:
                low = end
            else:
                high = end - 1
        legacy._require(low > start, BELOW_FLOOR_MESSAGE, 'review_route_capacity_below_floor')
        fragment = build(low)
        identifier = fragment['id']
        entries.append(dict(handle=identifier, characters=capacity.measure_capacity_value(route, fragment) + 1))
        catalog[identifier] = fragment
        start = low
    return entries, catalog


def _measure_final_room(records, artifacts, prior, plan, limits):
    from .openness_final_pages import measure_page_room
    if prior is None or plan['measure']['characters_per_token'] is None:
        return min(plan['allocation']['page_room'], limits['material_allowance'])
    return measure_page_room(records, artifacts, prior, plan, limits)


def _read_final_progress(state, page_plan):
    from .openness_final_pages import list_final_progress
    if state.get('final_currency') != page_plan['currency']:
        return [], []
    return list_final_progress(state, page_plan)


def prepare_series_final(records, artifacts, prior, route, logical, inventory, plan, state, limits_by_kind,
                         packet, *, split=False, keep_level=False):
    """Freeze or replay a series final plan, retaining pending and split pages."""
    from . import review_final_pages as pages, review_condensing
    from .openness_final_pages import read_page_plan, publish_page_plan
    header = packet[legacy.KEY]
    old_packet = _read_value(artifacts, prior['packet']) if prior else {}
    old = old_packet.get(legacy.KEY, {})
    reference = _read_value(artifacts, old['final_page_plan']) if 'final_page_plan' in old else None
    if reference is not None:
        header['final_page_plan'] = _put_value(artifacts, reference)
        value = read_page_plan(artifacts, reference)
        repeated = old.get('final_forecast', {}).get('repeated_pages', 0)
        if value['currency'] != read_final_currency(header):
            repeated = sum(row['kind'] in ('page', 'final') and row['terminal']
                           for row in state.get('final_progress', {}).values())
        header['final_forecast'] = dict(pages=sum(row['kind'] in ('page', 'final') for row in value['turns']),
            condensing_turns=sum(row['kind'] == 'combine' for row in value['turns']), closing_pages=1,
            repeated_pages=repeated, forecast=value['currency'] != read_final_currency(header))
    if header['kind'] != 'final':
        packet['digest'] = digest({key: value for key, value in packet.items() if key != 'digest'})
        return packet
    limits = limits_by_kind['final']
    room = _measure_final_room(records, artifacts, prior, plan, limits)
    if reference is None and not split and len(header['targets']) <= limits['target_allowance'] and not (
            state['pending_requests']) and sum(row['characters'] for row in _describe_entry_rows(route,
                _list_state_entries(state))) <= room:
        return packet
    inputs, catalog = build_final_inputs(route, artifacts, packet, inventory, plan, state, limits, room)
    currency = read_final_currency(header)
    floor = min(row['material_allowance'] for row in plan['task']['final']['levels'])
    attempt = max((row for row in records.get('review_attempt', {}).values()
                   if prior and row['assignment_id'] == prior['id']),
                  key=lambda row: row['recorded_revision'], default=None)
    attempt_id = attempt['id'] if attempt else (state['receipts'][-1]['attempt_id'] if state['receipts'] else header['series_assignment_id'])

    def publish(turns, previous, cause=None):
        value = pages.build_page_plan(header['series_assignment_id'], digest(plan), digest(state), currency,
            turns, digest(previous) if previous is not None else None, cause)
        return value, publish_page_plan(artifacts, value, previous=reference)

    if reference is None:
        initial_inputs = inputs
        if split:
            historical = dict(records, review_attempt={key: row for key, row in records.get('review_attempt', {}).items()
                if row['recorded_revision'] < prior['recorded_revision']})
            old_limits = derive_series_task_limits(historical, artifacts, prior, kind='final')
            old_room = _measure_final_room(historical, artifacts, prior, plan, old_limits)
            initial_inputs, _ = build_final_inputs(route, artifacts, packet, inventory, plan, state, old_limits, old_room)
        turns = pages.plan_final_turns(**initial_inputs)
        if not split and len(turns) == 1 and turns[0]['kind'] == 'final':
            return packet
        page_plan, reference = publish(turns, None)
    else:
        page_plan = read_page_plan(artifacts, reference)
    if page_plan['currency'] != currency:
        cause = dict(kind='inventory', replaced_inventory_digest=page_plan['currency']['inventory_digest'],
                     inventory_digest=currency['inventory_digest'])
        page_plan, reference = publish(pages.plan_final_turns(**inputs), page_plan, cause)
    completed, pending = _read_final_progress(state, page_plan)
    if split:
        number = old.get('final_turn', page_plan['turns'][0]['number'])
        turns = pages.split_final_turn(page_plan['turns'], number, floor, review_condensing.plan_condensing, pending=pending)
        if turns is None:
            return None
        page_plan, reference = publish(turns, page_plan, dict(kind='split', turn=number,
            attempt_id=attempt_id, completed=completed, pending=pending))
    previous = page_plan['turns'][-1]['context']
    current_level = limits.get('level_material_allowance', limits['material_allowance'])
    old_level = (old['capacity'].get('level_material_allowance', old['capacity']['material_allowance'])
                 if old.get('kind') == 'final' else current_level)
    cause_kind = None if keep_level else (
        'level' if previous['target_allowance'] != limits['target_allowance'] or
        old_level is not None and old_level != current_level else
        'ratio' if room < previous['page_room'] else None)
    if cause_kind:
        completed, pending = _read_final_progress(state, page_plan)
        cause = dict(kind=cause_kind, attempt_id=attempt_id, completed=completed, pending=pending, page_room=room)
        if cause_kind == 'level':
            cause['target_allowance'] = limits['target_allowance']
        turns = pages.repack_final_turns(page_plan['turns'], completed, pending, room, limits['target_allowance'],
            **{key: value for key, value in inputs.items() if key not in ('page_room', 'target_allowance')})
        page_plan, reference = publish(turns, page_plan, cause)
    while True:
        completed, pending = _read_final_progress(state, page_plan)
        selected = next((row for row in page_plan['turns'] if row['number'] not in completed), None)
        legacy._require(selected is not None, 'This final already has its closing answer.', 'review_resampling_forbidden')
        turns = pages.prepare_final_turn(page_plan['turns'], selected['number'], room, limits['target_allowance'],
            floor, review_condensing.plan_condensing, pending=pending, completed=completed, inputs=inputs)
        legacy._require(turns is not None, BELOW_FLOOR_MESSAGE, 'review_route_capacity_below_floor')
        if turns == page_plan['turns']:
            break
        page_plan, reference = publish(turns, page_plan, dict(kind='split', turn=selected['number'],
            attempt_id=attempt_id, completed=completed, pending=pending))
    return build_final_packet(route, artifacts, logical, inventory, plan, state, limits_by_kind, packet,
                              page_plan, reference, selected, catalog, room=room)


def _list_page_outputs(state, page_plan):
    progress = state.get('final_progress', {}) if state.get('final_currency') == page_plan['currency'] else {}
    return [dict(targets=turn['targets'], output=progress[str(turn['number'])]['output'])
            for turn in page_plan['turns'] if turn['kind'] in ('page', 'final')
            and progress.get(str(turn['number']), {}).get('terminal')]


def build_final_packet(route, artifacts, logical, inventory, plan, state, limits_by_kind, packet,
                       page_plan, reference, turn, catalog, *, room):
    """Carry exactly one block, target page or closing decision-row list."""
    from . import review_composition as composition, review_condensing
    header = packet[legacy.KEY]
    header.update(final_page_plan=_put_value(artifacts, reference), page_plan_digest=digest(page_plan),
                  final_turn=turn['number'], final_form=turn['kind'])
    header['final_forecast'] = dict(pages=sum(row['kind'] in ('page', 'final') for row in page_plan['turns']),
        condensing_turns=sum(row['kind'] == 'combine' for row in page_plan['turns']), closing_pages=1,
        repeated_pages=header.get('final_forecast', {}).get('repeated_pages', 0), forecast=False)
    envelope = dict(logical, review_parts=header)
    rule = composition.find_rule(logical['role'], envelope)
    outputs = _list_page_outputs(state, page_plan)
    rows = composition.build_decision_rows(rule, envelope, outputs,
        measure=lambda row: capacity.measure_capacity_value(route, row) + 1)
    for output, row in zip([item for page in outputs for item in page['output']['targets']], rows):
        key = output.get('target', output.get('formulation'))
        handle = next((handle for handle, target in zip(plan['targets']['handles'], header['targets'])
                       if key in (handle, target['id'])), key)
        catalog['decision:' + handle] = row
    catalog.update(copy.deepcopy(state.get('final_catalog', {})))
    catalog.update(copy.deepcopy(state.get('final_entries', {})))
    packet[legacy.STATE_KEY] = dict(cards=[], combinations=[], findings=[])
    packet.pop('review_parts_requests', None)
    limits = limits_by_kind['combine' if turn['kind'] == 'combine' else 'final']
    header['capacity'] = dict(_describe_capacity(limits, plan), material_allowance=min(room, limits['material_allowance']),
        level_material_allowance=limits.get('level_material_allowance', limits['material_allowance']))
    def read_entries(entries):
        return [copy.deepcopy(catalog[row['handle']]) for row in entries]
    if turn['kind'] == 'combine':
        header.update(kind='combine', mode='condense', block=copy.deepcopy(turn['block']),
                      block_artifact=_put_value(artifacts, turn['block']))
        header['capacity']['answer_bound'] = review_condensing.describe_combination_bound(
            turn['block'], header['capacity']['answer_bound'])
        packet[legacy.STATE_KEY]['cards'] = read_entries(turn['entry_sizes'])
    elif turn['kind'] in ('page', 'final'):
        header['page'] = dict(number=1 + sum(row['kind'] in ('page', 'final') for row in page_plan['turns'][:page_plan['turns'].index(turn)]),
            pages=sum(row['kind'] in ('page', 'final') for row in page_plan['turns']), targets=copy.deepcopy(turn['targets']))
        entries = turn['pool'] + [row for part in turn['parts'] for row in part['entries'] + part['rows']]
        for value in read_entries(entries):
            destination = 'findings' if 'verified_statements' in value else 'combinations' if 'block' in value else 'cards'
            packet[legacy.STATE_KEY][destination].append(value)
    else:
        header['closing'] = dict(rows=read_entries(turn['rows']))
    if state['pending_requests'] and turn['kind'] != 'combine':
        used = capacity.measure_capacity_value(route, packet[legacy.STATE_KEY]) + capacity.measure_capacity_value(route, header.get('closing', {}))
        packet['review_parts_requests'] = _deliver_requested_units(route, inventory, state['pending_requests'],
            header['capacity']['material_allowance'] - used, state)
        legacy._require(bool(packet['review_parts_requests']), 'The requested material has no room beside the page.', 'review_parts_no_progress')
    packet['digest'] = digest({key: value for key, value in packet.items() if key != 'digest'})
    return packet


def list_composite_turns(records, artifacts, assignment):
    from .openness_final_pages import read_page_plan
    header = _read_value(artifacts, assignment['packet']).get(legacy.KEY, {})
    if header.get('protocol') != PROTOCOL or 'page_plan_digest' not in header:
        return None
    members = sorted((row for row in records['review_assignment'].values()
        if row.get(legacy.KEY, {}).get('series_assignment_id') == header['series_assignment_id']),
        key=lambda row: row['recorded_revision'])
    latest = members[-1]
    latest_packet = _read_value(artifacts, latest['packet'])
    latest_header = latest_packet[legacy.KEY]
    page_plan = read_page_plan(artifacts, _read_value(artifacts, latest_header['final_page_plan']))
    from .review_protocol import read_assignment_output
    selected = {}
    for member in members:
        binding = _read_value(artifacts, member['packet'])[legacy.KEY]
        if binding.get('page_plan_digest') and read_final_currency(binding) == page_plan['currency']:
            attempt, output = read_assignment_output(records, artifacts, member['id'])
            if output is not None and output.get('kind') != 'requests' and output.get('stage') != 'material':
                selected[binding['final_turn']] = member['id']
    result = dict(chain=header['series_assignment_id'], pages=[], condensing=[], closing=None,
                  packet=dict(legacy.read_logical_packet(artifacts, latest_header), review_parts=latest_header))
    for turn in page_plan['turns']:
        row = dict(assignment_id=selected.get(turn['number'], latest['id']), targets=turn['targets'])
        if turn['kind'] in ('page', 'final'):
            result['pages'].append(row)
        elif turn['kind'] == 'combine':
            result['condensing'].append(row)
        else:
            result['closing'] = row
    return result


def build_validation_envelope(artifacts, packet, inventory):
    """Supply validation bytes without granting unrequested sources delivery credit."""
    header = packet[legacy.KEY]
    envelope = dict(legacy.read_logical_packet(artifacts, header), review_parts=header)
    envelope['evidence'] = list(envelope.get('evidence', [])) + list(_list_actual_segments(artifacts, [packet]))
    for row in inventory['units']:
        if not row['required']:
            component = row['components'][0]
            envelope['evidence'].append(dict(artifact=component['artifact'], encoding='utf-8', content=row['text']))
    return envelope


def read_composite_currency(records, artifacts, assignment):
    from .openness_final_pages import read_page_plan
    header = _read_value(artifacts, assignment['packet'])[legacy.KEY]
    page_plan = read_page_plan(artifacts, _read_value(artifacts, header['final_page_plan']))
    currency = read_final_currency(header)
    legacy._require(page_plan['currency'] == currency and digest(page_plan) == header['page_plan_digest'],
                    DELIVERY_INVALID_MESSAGE, 'review_parts_delivery_invalid')
    return currency


def validate_composite_output(records, artifacts, assignment, packet, output):
    from .review_parts_roles import get_adapter
    try:
        get_adapter(packet[legacy.KEY]['adapter'])['validate_final'](records, artifacts, assignment, output)
    except ResearchError as error:
        error.details['composed_output'] = True
        raise


def prepare_closing_composite(records, artifacts, assignment, attempt, output):
    from .review_composite import prepare_composite
    from .operations import immutable_record
    from .review_failure_class import classify_attempt
    packet = _read_value(artifacts, assignment['packet'])
    header = packet.get(legacy.KEY, {})
    if header.get('final_form') != 'closing' or classify_attempt(records, artifacts, attempt) is not None:
        return []
    validate_turn_output(records, artifacts, assignment, packet, output, attempt=attempt)
    if output.get('kind') == 'requests' or output.get('stage') == 'material':
        return []
    composite, (reference, content) = prepare_composite(records, artifacts, assignment, attempt, output)
    artifacts.put(content, reference['media_type'])
    return [immutable_record(records, 'review_composite', composite['id'], composite)]


def verify_final_page_plans(records, artifacts, chain):
    """Replay every linked page-plan artifact at its recorded state and currency."""
    from . import review_final_pages as pages
    from .openness_final_pages import read_page_plan
    values, owners = [], {}
    for member, packet in chain:
        header = packet[legacy.KEY]
        if 'final_page_plan' not in header:
            continue
        reference = _read_value(artifacts, header['final_page_plan'])
        for ancestor in reference.get('predecessors', []) + [reference]:
            if ancestor['digest'] not in owners:
                values.append(read_page_plan(artifacts, ancestor))
                owners[ancestor['digest']] = (member, packet)
    if not values:
        return
    def read_inputs(value):
        member, packet = owners[digest(value)]
        header = packet[legacy.KEY]
        state = _read_value(artifacts, header['state'], header['state_digest'])
        plan = capacity.read_chain_capacity_plan(artifacts, member)
        legacy._require(value['state_digest'] == digest(state) and value['plan_digest'] == digest(plan)
            and value['chain'] == header['series_assignment_id'] and value['currency'] == read_final_currency(header),
            DELIVERY_INVALID_MESSAGE, 'review_parts_delivery_invalid')
        earlier = [(row, prior) for row, prior in chain if row['recorded_revision'] < member['recorded_revision']]
        historical = dict(records, review_attempt={key: row for key, row in records.get('review_attempt', {}).items()
            if row['recorded_revision'] < member['recorded_revision']})
        if 'cause' not in value and member.get('capacity_replacement_of'):
            origin = records['review_assignment'][member['capacity_replacement_of']]
            historical = dict(historical, review_attempt={key: row for key, row in historical['review_attempt'].items()
                if row['recorded_revision'] < origin['recorded_revision']})
        limits = capacity.derive_plan_task_limits(plan, 'final', _derive_observations(historical, artifacts, earlier, 'final'))
        room = _measure_final_room(historical, artifacts, earlier[-1][0] if earlier else None, plan, limits)
        inventory = _read_series_inventory(artifacts, header['inventory'], header['inventory_digest'])
        inputs, _ = build_final_inputs(records['review_route'][member['route_id']], artifacts, packet, inventory, plan, state, limits, room)
        if value.get('cause', {}).get('kind') in ('level', 'ratio', 'split'):
            previous = values[values.index(value) - 1]
            completed, pending = _read_final_progress(state, previous)
            cause = value['cause']
            legacy._require(cause['completed'] == completed and cause['pending'] == pending and
                cause['attempt_id'] in historical['review_attempt'], DELIVERY_INVALID_MESSAGE, 'review_parts_delivery_invalid')
        return dict(inputs, floor=min(row['material_allowance'] for row in plan['task']['final']['levels']))
    pages.verify_page_plans(values, read_inputs)


COMPOSED_REFUSAL_MESSAGE = 'The composed review was refused; correct the closing answer using this check and composed path.'


def describe_composed_refusal(records, artifacts, attempt, check, path):
    certificate = records.get('review_output_refusal', {}).get(attempt['id'], {})
    if not certificate.get('details', {}).get('composed_output'):
        return None
    assignment = records['review_assignment'][attempt['assignment_id']]
    packet = _read_value(artifacts, assignment['packet'])
    header = packet.get(legacy.KEY, {})
    if header.get('final_form') != 'closing':
        return None
    result = dict(check=check, path=path, closing_answer=_read_value(artifacts, attempt['output']),
                  message=COMPOSED_REFUSAL_MESSAGE)
    state = _read_value(artifacts, header['state'], header['state_digest'])
    from .openness_final_pages import read_page_plan
    from . import review_composition as composition
    plan = read_page_plan(artifacts, _read_value(artifacts, header['final_page_plan']))
    outputs = _list_page_outputs(state, plan)
    envelope = dict(legacy.read_logical_packet(artifacts, header), review_parts=header)
    rule = composition.find_rule(assignment['role'], envelope)
    route = records['review_route'][assignment['route_id']]
    rows = composition.build_decision_rows(rule, envelope, outputs,
        measure=lambda row: capacity.measure_capacity_value(route, row) + 1)
    for output, row in zip([item for page in outputs for item in page['output']['targets']], rows):
        if any(path == field or path.startswith(field + '/') for field in output.get('fields', {})):
            result['decision_row'] = row
            break
    return result
