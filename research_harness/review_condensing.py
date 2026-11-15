"""Pure, entry-aware condensation; chain families store the returned blocks.

``characters`` is the caller's serialized charge for an entry. ``room`` is the
sum of those charges that a request permits beside its frame. A combination is
measured whole by the route's serializer, including its block handle and refs.
"""
import copy

from .errors import ResearchError
from .evidence import digest
from .review_refusal import contract_check


MINIMUM_CONDENSING_BLOCK_CHARACTERS = 1024
CONDENSING_TURN_TEXT = (
    'A block is an ordered group of carried entries retained under a block handle. '
    'Read its entries and return {block, summary, refs}. block is the supplied block handle. '
    'The complete serialized answer, including escapes and refs, fits the smaller of one quarter of the '
    'block characters and this turn\'s answer bound. Cite a nonempty array of distinct entry handles from '
    'this block. The block remains available by handle; request a page of its entry handles or an entry '
    'by its handle when you need the original material.'
)
INVALID_COMBINATION_MESSAGE = (
    'Return {block, summary, refs} for this block, with text in summary and a nonempty array of distinct '
    'local entry handles in refs. The whole serialized combination must fit its stated bound.'
)
INVALID_SIZE_MESSAGE = 'A room is a positive integer and entry characters are nonnegative integers.'
OVERSIZED_ENTRY_MESSAGE = 'An entry exceeds the block room; deliver that entry in segments before condensing it.'


def _validate_size(characters, room):
    if type(room) is not int or room <= 0 or type(characters) is not int or characters < 0:
        raise ResearchError('invalid_input', INVALID_SIZE_MESSAGE)


def _build_block(entries, characters):
    handles = [entry['handle'] for entry in entries]
    identity = [dict(handle=entry['handle'], characters=entry['characters']) for entry in entries]
    return dict(handle='b' + digest(identity), entries=handles, characters=characters)


def cut_condensing_blocks(entries, room):
    """Return consecutive whole-entry blocks as artifact values with stable handles."""
    _validate_size(0, room)
    blocks, current, characters = [], [], 0
    for entry in entries:
        size = entry['characters']
        _validate_size(size, room)
        if size > room:
            raise ResearchError('invalid_input', OVERSIZED_ENTRY_MESSAGE,
                                dict(handle=entry['handle'], characters=size, room=room))
        if current and characters + size > room:
            blocks.append(_build_block(current, characters))
            current, characters = [], 0
        current.append(entry)
        characters += size
    if current:
        blocks.append(_build_block(current, characters))
    return blocks


def describe_combination_bound(block, answer_bound):
    """Bound the complete serialized combination, not just its summary string."""
    return min(block['characters'] // 4, answer_bound)


def validate_combination(block, output, measure, answer_bound):
    """Validate one complete reviewer answer at the protocol's shape boundary."""
    with contract_check('review.shape'):
        handles = set(block['entries'])
        bound = describe_combination_bound(block, answer_bound)
        if (not isinstance(output, dict) or set(output) != {'block', 'summary', 'refs'}
                or output['block'] != block['handle'] or not isinstance(output['summary'], str)
                or not isinstance(output['refs'], list) or not output['refs']
                or any(not isinstance(ref, str) or ref not in handles for ref in output['refs'])
                or len(set(output['refs'])) != len(output['refs']) or measure(output) > bound):
            raise ResearchError('invalid_value_review', INVALID_COMBINATION_MESSAGE,
                                dict(block=block['handle'], combination_bound=bound))
    return copy.deepcopy(output)


def plan_condensing(entries, room):
    """Return exact blocks/rounds and the remaining bound at whole-output quarters.

    A block below MINIMUM_CONDENSING_BLOCK_CHARACTERS keeps its entries,
    unchanged, in the next round, including a small block before a large entry.
    Later blocks refer to combinations by their block handle. Actual answers
    may be smaller, including a lower answer cap.
    """
    remaining = [dict(handle=entry['handle'], characters=entry['characters']) for entry in entries]
    blocks = cut_condensing_blocks(remaining, room)
    initial = blocks
    rounds = []
    characters = sum(entry['characters'] for entry in remaining)
    while characters > room:
        by_handle = {entry['handle']: entry for entry in remaining}
        condensed, next_entries = [], []
        for block in blocks:
            if block['characters'] < MINIMUM_CONDENSING_BLOCK_CHARACTERS:
                next_entries.extend(by_handle[handle] for handle in block['entries'])
            else:
                condensed.append(block)
                next_entries.append(dict(handle=block['handle'], characters=block['characters'] // 4))
        if not condensed:
            raise ResearchError('invalid_input', 'The overflowing list has no block large enough to condense.',
                                dict(room=room, minimum=MINIMUM_CONDENSING_BLOCK_CHARACTERS))
        rounds.append(condensed)
        remaining = next_entries
        characters = sum(entry['characters'] for entry in remaining)
        if characters > room:
            blocks = cut_condensing_blocks(remaining, room)
    return dict(blocks=initial, rounds=rounds, entries=remaining,
                turns=sum(len(round_) for round_ in rounds), remaining_characters=characters)


def forecast_condensing(characters, room, largest_entry):
    """Return a marked upper forecast from a total, never an execution plan.

    A nonfinal greedy block contains more than room-largest_entry characters;
    adjacent blocks together exceed room. Both bounds limit the block count.
    Carried small blocks add at most three quarters of their combined size to
    contraction; their count is no greater than the block-count upper bound.
    No plan builder or verification calls this aggregate estimate.
    """
    _validate_size(characters, room)
    _validate_size(largest_entry, room)
    if largest_entry > room or characters > 0 and largest_entry == 0:
        raise ResearchError('invalid_input', OVERSIZED_ENTRY_MESSAGE)
    turns, rounds = 0, 0
    while characters > room:
        fill = room - largest_entry + 1
        blocks = min((characters + fill - 1) // fill, 2 * ((characters + room - 1) // room) - 1)
        turns += blocks
        rounds += 1
        small = min(MINIMUM_CONDENSING_BLOCK_CHARACTERS - 1, room)
        carried = min(characters, blocks * small)
        characters = min(characters - 1, (characters + 3 * carried) // 4)
        largest_entry = max(room // 4, min(largest_entry, small))
    return dict(forecast=True, turns=turns, rounds=rounds, remaining_characters=characters)


def apply_combination(state, block, output, measure):
    """Replace a block with a validated whole combination and retain its archive.

    ``entries`` carries handle/characters/value rows. ``handles`` retains every
    entry; ``blocks`` retains block artifact values. Neither archive belongs in
    a request. A combination's entry handle is the handle of its original block.
    """
    entries = state['entries']
    handles = [entry['handle'] for entry in entries]
    replaced = block['entries']
    start = next((index for index in range(len(entries))
                  if handles[index:index + len(replaced)] == replaced), None)
    if start is None:
        raise ResearchError('invalid_input', 'The block is not consecutive in the current carried state.')
    archive = copy.deepcopy(state['handles'])
    archive.update((entry['handle'], copy.deepcopy(entry)) for entry in entries)
    combination = dict(handle=block['handle'], characters=measure(output), value=copy.deepcopy(output))
    archive[combination['handle']] = copy.deepcopy(combination)
    blocks = copy.deepcopy(state.get('blocks', {}))
    blocks[block['handle']] = copy.deepcopy(block)
    return dict(entries=copy.deepcopy(entries[:start]) + [combination] +
                copy.deepcopy(entries[start + len(replaced):]), handles=archive, blocks=blocks)
