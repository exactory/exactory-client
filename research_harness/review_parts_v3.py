"""Pure card budgets and turn forecasts for the parts protocol v3.

The stored plan owns the rate, entry sizes and task levels. No current route
profile, policy constant or store is consulted. Read-turn card rows have
``handle``, ``unit``, ``unit_characters``, ``entry_characters`` and ``characters``;
the last member is the whole card budget, not just its summary.
"""
from fractions import Fraction

from .errors import ResearchError
from .review_parts import CARD_CHARACTERS
from .review_condensing import cut_condensing_blocks, plan_condensing


CARD_CONDENSING_RATE = Fraction(20, 1000)
IMPOSSIBLE_CARD_MINIMUM_MESSAGE = (
    'The entry sizes of these cards exceed the card allowance ceiling. Read fewer card entries in this turn.'
)
UNFIT_CARD_RATE_MESSAGE = (
    'The stored rate grants more than a condensing block for one character of text. '
    'A positive text segment cannot fit this block room.'
)


def compute_card_allowance(plan, unit_characters, cards):
    """Return an exact rational card budget, clamped to entry sizes and 80000.

    cards is a list of card rows; entry_characters overrides the stored default
    for a measured entry. Integer wire allowances round this result down once.
    Exact additivity holds for proportional budgets and for partitions where
    both sides use their entry minimum. A cap or mixed minimum can prevent it.
    """
    allocation = plan['allocation']
    minimum = sum(card.get('entry_characters', allocation['entry_sizes']['card']) for card in cards)
    if minimum > CARD_CHARACTERS:
        raise ResearchError('invalid_input', IMPOSSIBLE_CARD_MINIMUM_MESSAGE,
                            dict(minimum=minimum, maximum=CARD_CHARACTERS))
    proportional = Fraction(str(allocation['card_rate'])) * unit_characters
    return max(Fraction(minimum), min(Fraction(CARD_CHARACTERS), proportional))


def _find_block_room(plan):
    return min(plan['allocation']['rooms']['combine'], plan['task']['combine']['material_allowance'])


def _plan_card_events(cards, room, last_reads):
    result = plan_condensing(cards, room)
    events = []
    for round_index, blocks in enumerate(result['rounds'], 1):
        for block in blocks:
            after = max(last_reads[entry['handle']] for entry in block['entries'])
            last_reads['c' + block['block']] = after
            events.append(dict(after_read_turn=after, round=round_index, block=block))
    # Stable sorting keeps each round's consecutive blocks in their input order.
    return sorted(events, key=lambda event: (event['after_read_turn'], event['round']))


def plan_card_condensing(plan, read_turns):
    """Return low-rate combine events, indexed by one-based completed read turn.

    read_turns supplies measured or budgeted cards with handle and characters.
    Higher rounds follow as soon as their last input block is available. A final
    partial block is included when needed to bring the entire state within room.
    At or above the threshold there are no scheduled interleaved combines.
    """
    if Fraction(str(plan['allocation']['card_rate'])) >= CARD_CONDENSING_RATE:
        return []
    cards = [card for turn in read_turns for card in turn['cards']]
    last_reads = {card['handle']: index for index, turn in enumerate(read_turns, 1) for card in turn['cards']}
    return _plan_card_events(cards, _find_block_room(plan), last_reads)


def plan_target_condensing(plan, cards, page_room):
    """Return initial whole-card blocks if this target exceeds its page room."""
    if sum(card['characters'] for card in cards) <= page_room:
        return []
    return cut_condensing_blocks(cards, min(page_room, _find_block_room(plan)))


def _finish_read_turn(plan, cards):
    characters = sum(card['unit_characters'] for card in cards)
    allowance = int(compute_card_allowance(plan, characters, cards))
    minimum = sum(card['entry_characters'] for card in cards)
    extra = allowance - minimum
    # A size-only maximum realization: apportion the content by text read.
    # Remainders go in order, so the integer card sizes use the full allowance.
    weights = [card['unit_characters'] for card in cards] if characters else [1] * len(cards)
    total = sum(weights)
    shares = [extra * weight // total for weight in weights]
    for index in range(extra - sum(shares)):
        shares[index] += 1
    for card, share in zip(cards, shares):
        card['characters'] = card['entry_characters'] + share
    return dict(unit_characters=characters, cards=cards)


def _find_segment_characters(plan, level):
    rate = Fraction(str(plan['allocation']['card_rate']))
    if rate > 0 and _find_block_room(plan) < CARD_CHARACTERS:
        return min(level, int(Fraction(_find_block_room(plan)) / rate))
    return level


def plan_read_turns(plan, inventory, *, material_allowance=None):
    """Pack unique text bodies, segmenting at a read level and the card floor cap.

    A zero-character unit still produces one card. Segments are fixed per unit
    before packing, so unused tail room cannot create extra structural cards.
    A turn stops before its entries exceed the card cap or a condensing block.
    The text segment ceiling may be below the stored read floor when needed to
    keep a single granted card inside a condensing block. The forecast exposes
    that ceiling and any cards beyond the stored structural reserve. The maximum
    realization apportions the integer allowance among its cards; actual
    reviewer cards can distribute that allowance differently.
    """
    level = plan['task']['read']['material_allowance'] if material_allowance is None else material_allowance
    if type(level) is not int or level <= 0:
        raise ResearchError('invalid_input', 'The read material allowance must be a positive integer.')
    level = _find_segment_characters(plan, level)
    entry_size = plan['allocation']['entry_sizes']['card']
    card_count = min(CARD_CHARACTERS, _find_block_room(plan)) // entry_size
    if card_count < 1:
        raise ResearchError('invalid_input', IMPOSSIBLE_CARD_MINIMUM_MESSAGE)
    unique = {}
    for unit in inventory:
        unique.setdefault(unit.get('sha256', unit['handle']), unit)
    if level == 0 and any(unit['characters'] > 0 for unit in unique.values()):
        raise ResearchError('invalid_input', UNFIT_CARD_RATE_MESSAGE)
    turns, cards, characters = [], [], 0
    for unit in unique.values():
        remaining, part = unit['characters'], 0
        while True:
            size = min(remaining, level)
            if cards and (characters + size > level or len(cards) == card_count):
                turns.append(_finish_read_turn(plan, cards))
                cards, characters = [], 0
            cards.append(dict(handle='c/' + unit['handle'] + '/' + str(part), unit=unit['handle'],
                              unit_characters=size, entry_characters=entry_size))
            characters += size
            remaining -= size
            if not remaining:
                break
            part += 1
    if cards:
        turns.append(_finish_read_turn(plan, cards))
    return turns


def _count_card_condensing(plan, turns):
    cards = [card for turn in turns for card in turn['cards']]
    room = _find_block_room(plan)
    total = sum(card['characters'] for card in cards)
    bounds = []
    discretionary = False
    for turn in turns:
        extra = sum(card['characters'] - card['entry_characters'] for card in turn['cards'])
        discretionary = discretionary or extra > 0
        # Any one card can receive the whole discretionary turn budget. These
        # simultaneous upper bounds need not themselves be a possible answer.
        bounds.extend(dict(handle=card['handle'], characters=card['entry_characters'] + extra)
                      for card in turn['cards'])
    if not discretionary or total <= room:
        condensed = plan_condensing(cards, room)
        return dict(condensing_turns=condensed['turns'], condensing_rounds=len(condensed['rounds']),
                    remaining_characters=condensed['remaining_characters'], count_basis='entry_sizes')
    blocks = len(cut_condensing_blocks(bounds, room))
    count, rounds = blocks, 1
    remaining = min(total // 4, blocks * (room // 4))
    while remaining > room:
        blocks = (blocks + 3) // 4
        count += blocks
        rounds += 1
        remaining = min(remaining // 4, blocks * (room // 4))
    return dict(condensing_turns=count, condensing_rounds=rounds,
                remaining_characters=remaining, count_basis='entry_upper_bounds')


def count_series_turns(plan, inventory):
    """Bound read and combine counts at the start and lowest stored read level.

    Counts cover every distribution of the granted card budgets, with summaries
    at every quarter bound, through a state that fits a combine request. They are
    exact when all cards use their entry minimum; otherwise conservative
    independent entry bounds protect against an uneven answer. Finals/checks
    themselves are outside these counts. High-rate chains defer any necessary
    condensing until after the reads rather than scheduling it between reads.
    """
    result = {}
    start = plan['task']['read']['material_allowance']
    lowest = min(row['material_allowance'] for row in plan['task']['read']['levels'])
    for name, level in (('start', start), ('lowest', lowest)):
        turns = plan_read_turns(plan, inventory, material_allowance=level)
        cards = [card for turn in turns for card in turn['cards']]
        condensed = _count_card_condensing(plan, turns)
        count = len(turns)
        result[name] = dict(material_allowance=level, read_turns=count,
                            segment_characters=_find_segment_characters(plan, level),
                            planned_card_count=len(cards),
                            card_count_over_reserve=max(0, len(cards) -
                                plan['allocation'].get('largest_card_count', len(cards))),
                            card_characters=sum(card['characters'] for card in cards),
                            turns=count + condensed['condensing_turns'], **condensed)
    return result
