"""Pure block and summary budgets shared by planned review protocols.

An entry has a stable ``handle`` and its measured ``characters``. A block binds
the ordered entries by digest. Round forecasts charge the summary body; the
protocol builder must reserve its serialization envelope and reference fields
beside that body. This module does not build or measure transport requests.
"""
import copy

from .errors import ResearchError
from .evidence import digest
from .review_refusal import contract_check


CONDENSING_TURN_TEXT = (
    'Read this block of carried entries. Return {block, summary, refs}, using the block handle supplied. '
    'The summary has at most one quarter of the characters of the block, rounded down. '
    'Cite the entries the summary rests on in refs, a nonempty array of distinct handles from this block.'
)
INVALID_COMBINATION_MESSAGE = (
    'Return {block, summary, refs} for this block. summary is text within the stated quarter-block bound; '
    'refs is a nonempty array of distinct entry handles from this block.'
)
INVALID_SIZE_MESSAGE = 'A room is a positive integer and entry characters are nonnegative integers.'
OVERSIZED_ENTRY_MESSAGE = 'An entry exceeds the block room; deliver that entry in segments before condensing it.'


def _validate_size(characters, room):
    if type(room) is not int or room <= 0 or type(characters) is not int or characters < 0:
        raise ResearchError('invalid_input', INVALID_SIZE_MESSAGE)


def _build_block(entries, characters):
    return dict(block=digest(entries), entries=copy.deepcopy(entries), characters=characters,
                summary_bound=characters // 4)


def cut_condensing_blocks(entries, room):
    """Return consecutive whole-entry blocks, each with a quarter-summary bound.

    Entries larger than room need segmentation by their owning protocol first.
    All entry sizes already include the serialization the caller charges to them.
    """
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


def validate_combination(block, output):
    """Validate one reviewer output once, at its protocol's shape boundary."""
    with contract_check('review.shape'):
        handles = {entry['handle'] for entry in block['entries']}
        if (not isinstance(output, dict) or set(output) != {'block', 'summary', 'refs'}
                or output['block'] != block['block']
                or not isinstance(output['summary'], str)
                or len(output['summary']) > block['summary_bound']
                or not isinstance(output['refs'], list) or not output['refs']
                or any(not isinstance(ref, str) or ref not in handles for ref in output['refs'])
                or len(set(output['refs'])) != len(output['refs'])):
            raise ResearchError('invalid_value_review', INVALID_COMBINATION_MESSAGE,
                                dict(block=block['block'], summary_bound=block['summary_bound']))
    return copy.deepcopy(output)


def _build_summary_entry(block, characters):
    return dict(handle='c' + block['block'], characters=characters)


def plan_condensing(entries, room):
    """Return rounds of blocks, turn count and remaining summary-body bound.

    Only overflowing lists are condensed. Later rounds use combinations at
    their bounds. Whole-entry packing, including its unused room, is retained.
    """
    _validate_size(0, room)
    remaining = list(entries)
    rounds = []
    characters = sum(entry['characters'] for entry in remaining)
    # Check even a list that already fits, without adding an unnecessary round.
    blocks = cut_condensing_blocks(remaining, room)
    while characters > room:
        rounds.append(blocks)
        remaining = [_build_summary_entry(block, block['summary_bound']) for block in blocks]
        characters = sum(entry['characters'] for entry in remaining)
        if characters > room:
            blocks = cut_condensing_blocks(remaining, room)
    return dict(rounds=rounds, turns=sum(len(round_) for round_ in rounds),
                remaining_characters=characters)


def condense(characters, room):
    """Return (turns, remaining bound) for densely packed total characters.

    A total does not describe unused room between indivisible entries. Call
    plan_condensing when those entries are available. Quarter bounds round down
    for each block, exactly as the entry-aware plan does.
    """
    _validate_size(characters, room)
    turns = 0
    while characters > room:
        full, tail = divmod(characters, room)
        turns += full + bool(tail)
        characters = full * (room // 4) + tail // 4
    return turns, characters


def apply_combination(state, block, output):
    """Replace a consecutive block using an already validated combination.

    State is {entries: [...], handles: {handle: entry}}. handles is the retained
    archive, never a request member. It keeps originals and earlier combinations
    available to later requests. characters charges only the new summary body.
    """
    entries = state['entries']
    handles = [entry['handle'] for entry in entries]
    replaced = [entry['handle'] for entry in block['entries']]
    start = next((index for index in range(len(entries))
                  if handles[index:index + len(replaced)] == replaced), None)
    if start is None:
        raise ResearchError('invalid_input', 'The block is not consecutive in the current carried state.')
    archive = copy.deepcopy(state['handles'])
    archive.update((entry['handle'], copy.deepcopy(entry)) for entry in entries)
    combination = dict(_build_summary_entry(block, len(output['summary'])), **copy.deepcopy(output))
    archive[combination['handle']] = copy.deepcopy(combination)
    return dict(entries=copy.deepcopy(entries[:start]) + [combination] +
                copy.deepcopy(entries[start + len(replaced):]), handles=archive)
