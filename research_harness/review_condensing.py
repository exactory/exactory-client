"""Pure, entry-aware condensation; chain families store the returned blocks.

``characters`` is the caller's serialized charge for an entry. ``room`` is the
sum of those charges that a request permits beside its frame. A combination is
measured whole by the route's serializer, including its block handle and refs.
"""
import copy

from .errors import ResearchError
from .evidence import digest
from .review_refusal import contract_check, refuse_unreadable_answer


MINIMUM_CONDENSING_BLOCK_CHARACTERS = 1024
INVALID_COMBINATION_MESSAGE = (
    'This answer is not the combination of the block of this turn. Return exactly {block, summary, refs}. block is '
    'the handle of the block that the turn delivers, summary is text, and refs is a nonempty list of entries of '
    'that block, each once, named as the instructions of this turn state. The whole answer, counted as a request '
    'of this route carries it, has at most the smaller of a quarter of the characters of the block and the answer '
    'bound of the turn; the turn states both. For the agent, details.block names the block and '
    'details.combination_bound gives that number; a notice shows these two members as digests.')
INVALID_SIZE_MESSAGE = (
    'The sizes given to the condensing planner are not valid. Give the room of a block as a positive integer of '
    'characters, and the characters of each entry as an integer of at least zero.'
)
OVERSIZED_ENTRY_MESSAGE = (
    'One entry is larger than the room of a block, and a block holds whole entries, so this list cannot be cut '
    'into blocks. Where the details are given, details.handle names the entry and details.characters its size '
    'against details.room. Cut such an entry into segments that fit the room before the list is condensed. '
    'Where this refusal reaches a command, keep its details and report the stop.'
)

INVALID_FORECAST_ENTRY_MESSAGE = (
    'The forecast of a condensing was asked for a list whose total is above zero and whose largest entry has zero '
    'characters. Give the size of the largest entry of the list, a positive integer of at most the room of a '
    'block.')


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
    """Validate one complete reviewer answer at the protocol's shape boundary. The members of the block, which the
    harness built, and the bound are read before the scope that refuses an unreadable answer."""
    handle, handles, references = block['handle'], set(block['entries']), block.get('references', [])
    bound = describe_combination_bound(block, answer_bound)
    with refuse_unreadable_answer(), contract_check('review.shape'):
        if (not isinstance(output, dict) or set(output) != {'block', 'summary', 'refs'}
                or output['block'] != handle or not isinstance(output['summary'], str)
                or not isinstance(output['refs'], list) or not output['refs']
                or any(not (isinstance(ref, str) and ref in handles or isinstance(ref, dict)
                            and ref in references) for ref in output['refs'])
                or len({digest(ref) for ref in output['refs']}) != len(output['refs']) or measure(output) > bound):
            raise ResearchError('invalid_value_review', INVALID_COMBINATION_MESSAGE,
                                dict(block=handle, combination_bound=bound))
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
            raise ResearchError('invalid_input',
                                'The entries of this list exceed the room, and no block of them reaches '
                                'details.minimum characters, the smallest block that a condensing turn takes, so '
                                'the list cannot be condensed. details.room gives the room.',
                                dict(room=room, minimum=MINIMUM_CONDENSING_BLOCK_CHARACTERS))
        rounds.append(condensed)
        remaining = next_entries
        characters = sum(entry['characters'] for entry in remaining)
        if characters > room:
            blocks = cut_condensing_blocks(remaining, room)
    return dict(blocks=initial, rounds=rounds, entries=remaining,
                minimum_block_characters=MINIMUM_CONDENSING_BLOCK_CHARACTERS,
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
        message = OVERSIZED_ENTRY_MESSAGE if largest_entry > room else INVALID_FORECAST_ENTRY_MESSAGE
        raise ResearchError('invalid_input', message)
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
        raise ResearchError('invalid_input', 'The entries of this block do not stand together and in their order in '
                                             'the carried state, so its combination cannot take their place.')
    archive = copy.deepcopy(state['handles'])
    archive.update((entry['handle'], copy.deepcopy(entry)) for entry in entries)
    combination = dict(handle=block['handle'], characters=measure(output), value=copy.deepcopy(output))
    archive[combination['handle']] = copy.deepcopy(combination)
    blocks = copy.deepcopy(state.get('blocks', {}))
    blocks[block['handle']] = copy.deepcopy(block)
    return dict(entries=copy.deepcopy(entries[:start]) + [combination] +
                copy.deepcopy(entries[start + len(replaced):]), handles=archive, blocks=blocks)
