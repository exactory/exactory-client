"""Pure D2 selection of earlier problem or math readings for a new chain.

The caller supplies the new first assignment's identity and recorded revision. The verifier compares
a stored selection of version 2 or 3 with the selection of the store as it stood before that assignment,
and it authenticates each carried item of a stored selection under the records as they stand. Version 3
observes each earlier chain on the content records as they stood at the start of that chain (C16 to C22).
Route equality uses review_context_repair.is_same_route(first, second), with the identity of Ruling 2.
"""

import copy
import hashlib
import json
from types import MappingProxyType

from . import openness_reading as reading, openness_reading_v7 as v7, review_context_repair, storage
from .artifacts import describe_artifact
from .errors import ResearchError
from .evidence import digest
from .evaluation import Evaluation
from .native_review import load_packet
from .operations import normalized_text
from .problem_openness import serialize_record
from .workspace import strict_json

SELECTION_PROTOCOL_V1 = 'exactory-openness-carried-selection-v1'
SELECTION_PROTOCOL_V2 = 'exactory-openness-carried-selection-v2'
SELECTION_PROTOCOL_V3 = 'exactory-openness-carried-selection-v3'
# The protocol of the selection that a new first assignment stores.
SELECTION_PROTOCOL = SELECTION_PROTOCOL_V3
# The protocols whose verification first compares the stored turns with the selection of the cutoff.
CUTOFF_PROTOCOLS = (SELECTION_PROTOCOL_V2, SELECTION_PROTOCOL_V3)


def _verify_math_prefix(records, artifacts, native, chain, rows, observations):
    """Apply math-v1's binding and record checks to a ready reading prefix.

    reading._require_math_continuity requires a native final result at the endpoint.
    This prefix keeps its checks, without that terminal requirement or final coverage.
    Observation, output validation and record rebuilding use the original protocol's code.
    """
    from .review_protocol import canonical_prompt, find_common_prompt_version
    first = chain[0]
    first_packet = json.loads(artifacts.read(first['packet']))
    record = reading._create_record(reading.MATH_PROTOCOL,
                                   excerpt_protocol=reading._find_record(first_packet).get('excerpt_protocol'))
    for index, (assignment, observation) in enumerate(zip(chain, observations)):
        packet, output = json.loads(artifacts.read(assignment['packet'])), observation['output']
        turn = packet[reading.TURN_KEY]
        v7._require_selected(
            reading.find_stage(packet) == 'reading' and reading.find_protocol(packet) == reading.MATH_PROTOCOL
            and assignment['role'] == 'native_math'
            and turn['statement'] == native['subject']
            and all(turn[key] == native[key] for key in ('subject_digest', 'claim_digest', 'reviewer'))
            and assignment['context']['native_packet'] == first['context']['native_packet']
            and assignment['route_id'] == first['route_id']
            and normalized_text(assignment['reviewer_id']) == normalized_text(first['reviewer_id'])
            and normalized_text(assignment['author_id']) == normalized_text(first['author_id'])
            and assignment.get('reading_chain') == dict(first_assignment_id=first['id'], turn=index + 1)
            and assignment['prompt'] == canonical_prompt('native_math', reading_stage='reading',
                reading_protocol=reading.MATH_PROTOCOL, common_version=find_common_prompt_version(assignment['prompt']))
            and reading._project_withheld_notes(record, reading._find_record(packet)) == reading._find_record(packet))
        reading.validate_turn_output(packet, output)
        reading._require(reading.has_deliverable_passage(records, artifacts, packet, output,
            assignment=assignment, turn=observation['turn']), reading.PASSAGE_PROGRESS_ERR_MSG, path='/passage_requests')
        record = reading._extend_record(records, artifacts, packet, {'output': output}, rows,
                                        observations[:index + 1], reading.MATH_PROTOCOL)


def observe_math_chain(records, artifacts, assignment):
    """Authenticate math-v1 reading turns in the observation form consumed by credit_carried_turn.

    A stopped final needs no output. Only the preceding ready reading prefix supplies
    credit, checked excerpts, passage answers and notes. Searches complete when all
    parts of their original record reached those readings, without problem factoring.
    """
    chain = reading._list_chain(records, assignment)
    while chain and reading.find_stage(json.loads(artifacts.read(chain[-1]['packet']))) != 'reading':
        chain.pop()
    if not chain:
        return []
    endpoint = chain[-1]
    native = load_packet(artifacts, endpoint['context']['native_packet'], endpoint['reviewer_id'])
    v7._require_selected(reading.is_math_openness_packet(native))
    rows = reading.list_reading_rows(records, artifacts, native, review=endpoint)
    spans = reading._compute_spans(artifacts, rows)
    observations = reading._observe_turns(records, artifacts, chain, rows, spans)
    _verify_math_prefix(records, artifacts, native, chain, rows, observations)
    by_identity = {reading.review_excerpts.find_identity(row): row for row in rows}
    result, completed = [], set()
    for index, (saved, observed) in enumerate(zip(chain, observations)):
        packet = json.loads(artifacts.read(saved['packet']))
        earlier = reading._find_record(packet)
        retained = reading._extend_record(records, artifacts, packet, {'output': observed['output']},
                                          rows, observations[:index + 1], reading.MATH_PROTOCOL)
        deliveries = []
        for delivered in observed['turn']['delivered_units'] + observed['turn'].get('delivered_parts', []):
            row = by_identity[reading.review_excerpts.find_identity(delivered)]
            text = artifacts.read(row['artifact']).decode('utf-8')
            start, end = delivered.get('start', 0), delivered.get('end', len(text))
            span = text[start:end]
            deliveries.append(dict(identity=v7._describe_identity(row), sha256=row['artifact']['sha256'],
                part=delivered.get('part', 1), parts=delivered.get('parts', 1), start=start, end=end,
                span_sha256=hashlib.sha256(span.encode('utf-8')).hexdigest(), text=span))
        whole, parts = reading._collect_coverage(observations[:index + 1])
        searches = [row['artifact']['sha256'] for row in rows
                    if row.get('record_kind') == 'literature_search' and row['artifact']['sha256'] not in completed
                    and reading._is_complete(reading.review_excerpts.find_identity(row),
                        spans[reading.review_excerpts.find_identity(row)], whole, parts)]
        completed.update(searches)
        origin = dict(chain=chain[0]['id'], protocol=reading.MATH_PROTOCOL, assignment_id=saved['id'],
                      attempt_id=observed['turn']['attempt_id'])
        result.append(dict(origin=origin, turn=dict(number=index + 1, assignment_id=saved['id'],
            attempt_id=origin['attempt_id'], kind='reading'), deliveries=deliveries, notes=observed['output']['notes'],
            excerpts=v7._describe_checked_entries(records, rows, retained['excerpts'][len(earlier['excerpts']):]),
            passages=v7._describe_checked_entries(records, rows, retained['passages'][len(earlier['passages']):]),
            searches=searches))
    return result


CHAIN_OBSERVERS = MappingProxyType({
    'exactory-openness-reading-v5': v7.observe_carried_chain,
    'exactory-openness-reading-v6': v7.observe_carried_chain,
    'exactory-openness-reading-v7': v7.observe_v7_chain,
    reading.MATH_PROTOCOL: observe_math_chain,
    reading.MATH_PROTOCOL_V2: v7.observe_v7_chain,
})
STORE_REFUSAL_CODES = ('artifact_missing', 'artifact_corrupt', 'storage_io', 'unsafe_path')
# The two refusals with which an observer states that a prefix of an earlier chain is no ready reading prefix.
PREFIX_STATE_CODES = ('review_context_unverified', 'openness_reading_incomplete')
ORIGIN_HISTORY_ERR_MSG_PARTS = (
    'A carried selection reads an earlier reading chain as the records stood when that chain started, and the '
    'harness cannot find that revision for one chain, so it selects and verifies no carried turn. '
    'details.record_id names the first assignment of that chain. {reason} Run record-check, and report this '
    'refusal with its details to the owner.',
    'The records were read without their history, which states the revision at which that assignment was recorded.',
    'The history does not show that assignment, as the records give it now, recorded at the revision that its '
    'recorded_revision states.',
)


def _read_cutoff_revision(records, kind, key, row, protocol):
    """Read creation order for v3, and retain the stored chronology of earlier protocols."""
    if protocol != SELECTION_PROTOCOL_V3:
        return row['recorded_revision']
    read_creation = getattr(records, 'read_creation_revision', None)
    return None if read_creation is None else read_creation(kind, key)


def _require_origin_stamp(records, first, revision, *, authenticate=False):
    """Require the first assignment and its stamp to agree with the retained history."""
    has_history = revision is not None and getattr(records, 'read_snapshot', None) is not None
    stamp = first.get('recorded_revision')
    if has_history and type(stamp) is int and stamp == revision:
        if not authenticate or records.verify_record_version('review_assignment', first['id'], revision, digest(first)):
            return
    message, missing_history, mismatched_stamp = ORIGIN_HISTORY_ERR_MSG_PARTS
    raise ResearchError('claim_reassessment_required',
                        message.format(reason=mismatched_stamp if has_history else missing_history),
                        {'record_id': 'review_assignment:' + first['id']})


def _describe_chain(records, artifacts, first, identity, *, protocol=None, report_only=False):
    """Qualify one named first assignment, independently of every other chain. The caller compares the reviewer.

    An artifact is read only when the result can depend on it, so the comparisons run in four steps, and the
    first that fails ends them. The records come first: the role, the first turn of a reading chain, the author,
    the route identity and the cutoff. Then comes the native packet, which decides the two digests and the
    formulations for every value: an openness_minimum that is no object makes the assignment no candidate. Then
    comes the packet with its reading protocol, which has the math prefix exactly when identity names the
    packet_kind math, and last the envelope with its evidence, where the kind of the packet alone is tested. An
    assignment starts a chain only when its record names itself as the first assignment of a reading chain, so a
    single-turn review and a replacement of a first turn start none.
    """
    if (first['role'] != 'native_math' or first['context'].get('prior_assignment_id') is not None
            or first.get('reading_chain', {}).get('first_assignment_id') != first['id']
            or protocol != SELECTION_PROTOCOL_V3 and first['recorded_revision'] >= identity['recorded_revision']
            or first['author_id'] != identity['author_id']
            or not review_context_repair.is_same_route(records['review_route'][first['route_id']], identity['route'])):
        return None
    revision = _read_cutoff_revision(records, 'review_assignment', first['id'], first, protocol)
    if protocol == SELECTION_PROTOCOL_V3:
        if revision is not None and revision >= identity['recorded_revision']:
            return None
        if not report_only:
            _require_origin_stamp(records, first, revision)
        if revision is None:
            return None
    binding = strict_json(artifacts.read(first['context']['native_packet']))
    context = binding.get('scientific_context') if isinstance(binding, dict) else None
    minimum = context.get('openness_minimum') if isinstance(context, dict) else None
    if (not isinstance(minimum, dict)
            or binding.get('subject_digest') != identity['subject_digest']
            or binding.get('claim_digest') != identity['claim_digest']
            or dict(statement=minimum.get('statement'),
                    neighbours=minimum.get('neighbours')) != identity['formulations']):
        return None
    protocol = reading.find_protocol(json.loads(artifacts.read(first['packet'])))
    is_math = identity.get('packet_kind', 'problem') == 'math'
    if protocol not in CHAIN_OBSERVERS or protocol.startswith('exactory-openness-reading-math-') != is_math:
        return None
    native = load_packet(artifacts, first['context']['native_packet'], first['reviewer_id'])
    if not (reading.is_math_openness_packet(native) if is_math else reading.is_openness_packet(native)):
        return None
    return dict(chain=first['id'], protocol=protocol, recorded_revision=revision)


def _list_ordered_assignments(records, protocol):
    """Visit newest assignments first, leaving absent history for qualification to diagnose."""
    assignments = records.get('review_assignment', {}).values()
    if protocol != SELECTION_PROTOCOL_V3:
        return sorted(assignments, key=lambda row: row['recorded_revision'], reverse=True)
    return sorted(assignments,
                  key=lambda row: _read_cutoff_revision(records, 'review_assignment', row['id'], row, protocol) or 0,
                  reverse=True)


def list_qualifying_chains(records, artifacts, identity, *, protocol=None):
    """Return the chains that qualify, newest first. No artifact of an assignment under another reviewer is read.

    identity is {reviewer_id, author_id, route, subject_digest, claim_digest, formulations,
    recorded_revision}. packet_kind is 'math' for a math packet; its default is 'problem'
    for the existing problem callers. The revision is the new first assignment's cutoff, exclusive. A refusal of
    the qualification ends the selection.
    """
    chains = []
    for first in _list_ordered_assignments(records, protocol):
        if normalized_text(first['reviewer_id']) != normalized_text(identity['reviewer_id']):
            continue
        chain = _describe_chain(records, artifacts, first, identity, protocol=protocol)
        if chain is not None:
            chains.append(chain)
    return chains


def list_passed_over_chains(records, artifacts, identity, *, protocol=None):
    """Return {passed_over, unavailable_chains} for the chains under another reviewer, newest first.

    These rows belong to the report alone: no selection holds such a chain. A chain whose artifact the
    store cannot give is a row of unavailable_chains, with the code and details of that refusal. Every
    other refusal ends the report.
    """
    passed_over, unavailable = [], []
    for first in _list_ordered_assignments(records, protocol):
        if normalized_text(first['reviewer_id']) == normalized_text(identity['reviewer_id']):
            continue
        try:
            chain = _describe_chain(records, artifacts, first, identity, protocol=protocol, report_only=True)
        except ResearchError as error:
            if error.code not in STORE_REFUSAL_CODES:
                raise
            unavailable.append(dict(chain=first['id'], code=error.code, details=error.details,
                                    message=v7.UNAVAILABLE_CHAIN_TEXT.format(chain=first['id'])))
            continue
        if chain is not None:
            passed_over.append(dict(chain, reason='reviewer_id'))
    return dict(passed_over=passed_over, unavailable_chains=unavailable)


def _select_recorded_attempts(records, identity, protocol):
    """Exclude outputs first recorded at or after the new first assignment."""
    eligible = {}
    for kind in ('review_attempt', 'review_invocation'):
        eligible[kind] = {}
        for key, row in records.get(kind, {}).items():
            revision = _read_cutoff_revision(records, kind, key, row, protocol)
            if revision is not None and revision < identity['recorded_revision']:
                eligible[kind][key] = row
    return storage.overlay_kinds(records, eligible)


def read_chain_records(records, first, protocol):
    """Return the records on which a selection under protocol observes the chain that first starts.

    Version 3 reads the content kinds as they stood at the start of the chain, the revision before the one at which
    the history shows first recorded, and every other kind as records give it (C17, C18). Every other protocol reads
    records as given. Records without that history, or a stamp of first that the history does not show, end the
    selection with claim_reassessment_required (C21).
    """
    if protocol != SELECTION_PROTOCOL_V3:
        return records
    revision = _read_cutoff_revision(records, 'review_assignment', first['id'], first, protocol)
    _require_origin_stamp(records, first, revision, authenticate=True)
    return storage.pin_kinds(records, reading.READING_CONTENT_KINDS, revision - 1)


def observe_qualifying_chain(records, artifacts, chain, identity, *, protocol=None):
    """Authenticate the longest ready prefix before the cutoff with its own protocol's observer.

    A stopped/unready last turn cannot erase earlier ready readings. An invalid turn cannot be skipped
    inside a prefix. The dispatch table is the only home for supported observer protocols. Only a refusal
    in PREFIX_STATE_CODES sends the observation to the turn before; every other refusal ends the selection.
    protocol names the selection whose rule reads the records (read_chain_records); without it the chain is
    observed on records as given, the rule of versions 1 and 2.
    """
    eligible = read_chain_records(_select_recorded_attempts(records, identity, protocol),
                                  records['review_assignment'][chain['chain']], protocol)
    artifacts = _SelectionEvaluation.of(eligible, artifacts)
    members = []
    for row in records['review_assignment'].values():
        if row['id'] != chain['chain'] and row.get('reading_chain', {}).get('first_assignment_id') != chain['chain']:
            continue
        revision = _read_cutoff_revision(records, 'review_assignment', row['id'], row, protocol)
        if revision is not None and revision < identity['recorded_revision']:
            members.append((revision, row))
    observer = CHAIN_OBSERVERS[chain['protocol']]
    for _, assignment in sorted(members, key=lambda member: member[0], reverse=True):
        try:
            return observer(eligible, artifacts, assignment)
        except ResearchError as error:
            if error.code not in PREFIX_STATE_CODES:
                raise
    return []


def _index_sources(inventory):
    return {(digest(row['identity']), row['sha256']): row for row in inventory['units']}


def credit_carried_turn(inventory, observation):
    """List unchanged delivered ranges, completed original searches and own-source retained entries.

    This is a candidate in project_carried_turn's selection form. Selection across candidates is R67;
    classify_carried_notes supplies its cumulative source dependencies before it is persisted.
    """
    sources = _index_sources(inventory)
    ranges = []
    for delivered in observation['deliveries']:
        unit = sources.get((digest(delivered['identity']), delivered['sha256']))
        if unit is None or unit['identity'].get('record_kind') == 'literature_search':
            continue
        ranges.append(dict(unit=unit['handle'], part=delivered['part'], parts=delivered['parts'],
                           start=delivered['start'], end=delivered['end'], sha256=delivered['span_sha256']))
    searches = [sha for sha in observation['searches'] if any(
        row['sha256'] == sha and row['identity'].get('record_kind') == 'literature_search'
        for row in inventory['units'])]
    entries = [dict(kind=kind, index=index) for kind in ('excerpt', 'passage')
               for index, entry in enumerate(observation[kind + 's'])
               if (digest(entry['identity']), entry['source_sha256']) in sources
               and entry['status'] == ('verified' if kind == 'excerpt' else 'delivered')]
    return dict(origin=copy.deepcopy(observation['origin']), ranges=ranges, searches=searches, entries=entries,
                notes=dict(classification='current', units=[]))


def _list_search_dependencies(records, artifacts, first, *, packet_kind='problem'):
    """Map legacy search components to the original logical searches on which notes depend.

    Components convey no delivery credit. Even a partially delivered original can affect notes;
    its whole binding is therefore a conservative dependency before its completion receipt exists.
    Math-v1 delivers originals, so its observations already name their dependencies.
    """
    if packet_kind == 'math':
        return {}
    native = load_packet(artifacts, first['context']['native_packet'], first['reviewer_id'])
    manifest = reading._plan_search_delivery(artifacts, native)['manifest']
    originals = {row['search_digest']: dict(
        identity=dict(kind='record', record_kind='literature_search', record_id=row['record_id']),
        sha256=row['search_digest']) for row in manifest['searches']}
    dependencies = {}
    for row in reading.list_delivery_rows([], manifest):
        owners = ([owner['search_digest'] for owner in row['owners']]
                  if row['record_kind'] == 'search_shared_field' else [row['search_digest']])
        key = (digest(v7._describe_identity(row)), row['artifact']['sha256'])
        dependencies[key] = [originals[sha256] for sha256 in dict.fromkeys(owners)]
    return dependencies


def classify_carried_notes(inventory, observations, *, search_dependencies=None):
    """Classify notes from cumulative deliveries and passage answers received by that turn (R70).

    Dependency rows are {identity, sha256, current_sha256}; null means the unit left the inventory.
    Added units have no effect. A changed or removed dependency makes notes affected.
    V5/v6 observations expose answers to the current output's passage requests. The successor
    receives those answers, so their sources first affect the successor's notes.
    """
    current = {digest(row['identity']): row['sha256'] for row in inventory['units']}
    dependencies, result, received_passages = {}, [], []
    for observation in observations:
        sources = [dict(identity=row['identity'], sha256=row['sha256']) for row in observation['deliveries']]
        sources += observation.get('dependencies', [])
        sources += received_passages
        for source in sources:
            originals = (search_dependencies or {}).get((digest(source['identity']), source['sha256']), [source])
            for original in originals:
                dependencies.setdefault((digest(original['identity']), original['sha256']), original)
        units = [dict(copy.deepcopy(source), current_sha256=current.get(key[0]))
                 for key, source in dependencies.items()]
        result.append(dict(classification='current' if all(row['sha256'] == row['current_sha256']
                                                          for row in units) else 'affected', units=units))
        received_passages = [dict(identity=row['identity'], sha256=row['source_sha256'])
                             for row in observation['passages']]
    return result


def _merge_ranges(ranges):
    merged = []
    for start, end in sorted(ranges):
        if merged and start <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
        else:
            merged.append((start, end))
    return merged


def _count_characters(ranges):
    return sum(end - start for start, end in _merge_ranges(ranges))


def derive_carried_turns(inventory, chains, credits):
    """Return own-source entries for every ready turn of each carried chain, newest chain first.

    Credits retain the unit walk's ranges and completed searches. Only credits of later chains
    supersede an eligible entry, and only when they cover its whole span or its original search.
    """
    sources = _index_sources(inventory)
    searches = {row['sha256']: row['handle'] for row in inventory['units']
                if row['identity'].get('record_kind') == 'literature_search'}
    later_ranges, later_searches, result = {}, set(), {}
    for chain_id, observations in chains:
        entries, credited = {}, False
        for observation in observations:
            key = observation['origin']['assignment_id']
            credit = credits.get(key, dict(ranges=[], searches=[]))
            credited = credited or bool(credit['ranges'] or credit['searches'])
            entries[key] = []
            for item in credit_carried_turn(inventory, observation)['entries']:
                entry = observation[item['kind'] + 's'][item['index']]
                unit = sources[(digest(entry['identity']), entry['source_sha256'])]['handle']
                covered = any(start <= entry['start'] and entry['end'] <= end
                              for start, end in _merge_ranges(later_ranges.get(unit, [])))
                if unit not in later_searches and not covered:
                    entries[key].append(item)
        if credited or any(entries.values()):
            result.update(entries)
        for observation in observations:
            credit = credits.get(observation['origin']['assignment_id'], dict(ranges=[], searches=[]))
            for span in credit['ranges']:
                later_ranges.setdefault(span['unit'], []).append((span['start'], span['end']))
            later_searches.update(searches[sha256] for sha256 in credit['searches'])
    return result


class _SelectionArtifacts:
    """Remember store refusals even when a historical observer catches them."""

    def __init__(self, source):
        self.source = source
        self.root = source.root
        self.refusals = []

    def read(self, reference):
        try:
            return self.source.read(reference)
        except ResearchError as error:
            if error.code in STORE_REFUSAL_CODES:
                self.refusals.append(error)
            raise

    def put(self, content, media_type):
        return self.source.put(content, media_type)

    def require_local_read(self, purpose):
        return self.source.require_local_read(purpose)

    def require_external_effect(self):
        return self.source.require_external_effect()


class _SelectionEvaluation(Evaluation):
    """Include descriptor refusals raised before Evaluation reaches the artifact store."""

    def read(self, reference):
        try:
            return super().read(reference)
        except ResearchError as error:
            if (isinstance(self.store, _SelectionArtifacts) and error.code == 'unsafe_path'
                    and error not in self.store.refusals):
                self.store.refusals.append(error)
            raise

    def read_json(self, reference, *, strict=False):
        try:
            return super().read_json(reference, strict=strict)
        except ResearchError as error:
            if (isinstance(self.store, _SelectionArtifacts) and error.code == 'unsafe_path'
                    and error not in self.store.refusals):
                self.store.refusals.append(error)
            raise


def select_carried_readings(records, artifacts, inventory, identity, *, protocol):
    """Select R67 candidates latest chain first, then turn number, walking inventory units in order.

    Take an overlapping range whole only when it contributes a character not already taken.
    Emit carried turns in recorded order, using only the form consumed by project_carried_turn.
    protocol names the selection whose rule observes each earlier chain (read_chain_records).
    """
    while isinstance(artifacts, Evaluation):
        artifacts = artifacts.store
    noted = _SelectionArtifacts(artifacts)
    artifacts = _SelectionEvaluation(records, noted)
    try:
        candidates, chains = [], []
        for chain in list_qualifying_chains(records, artifacts, identity, protocol=protocol):
            observations = observe_qualifying_chain(records, artifacts, chain, identity, protocol=protocol)
            first = records['review_assignment'][chain['chain']]
            classes = classify_carried_notes(inventory, observations,
                search_dependencies=_list_search_dependencies(records, artifacts, first,
                    packet_kind=identity.get('packet_kind', 'problem')))
            own = []
            for observation, notes in zip(observations, classes):
                # The v7 observer must likewise expose only turns that this chain ran itself.
                if observation['origin']['chain'] != chain['chain'] or observation['turn']['kind'] != 'reading':
                    continue
                credit = credit_carried_turn(inventory, observation)
                credit['notes'] = notes
                candidates.append((observation, credit, dict(credit, ranges=[], searches=[])))
                own.append(observation)
            chains.append((chain['chain'], own))
        for unit in inventory['units']:
            covered = []
            for observation, credit, selected in candidates:
                if unit['identity'].get('record_kind') == 'literature_search':
                    if unit['sha256'] in credit['searches']:
                        selected['searches'].append(unit['sha256'])
                        break
                else:
                    for span in credit['ranges']:
                        if span['unit'] != unit['handle']:
                            continue
                        extended = _merge_ranges(covered + [(span['start'], span['end'])])
                        if _count_characters(extended) > _count_characters(covered):
                            selected['ranges'].append(span)
                            covered = extended
                    if _count_characters(covered) >= unit['characters']:
                        break
        derived = derive_carried_turns(inventory, chains,
            {row['origin']['assignment_id']: row for observation, credit, row in candidates})
        selected = [dict(row, entries=derived[row['origin']['assignment_id']])
                    for observation, credit, row in candidates if row['origin']['assignment_id'] in derived]
        def read_attempt_revision(row):
            key = row['origin']['attempt_id']
            return _read_cutoff_revision(records, 'review_attempt', key, records['review_attempt'][key], protocol)
        return sorted(selected, key=read_attempt_revision)
    finally:
        if noted.refusals:
            raise noted.refusals[0]


def build_carried_selection(records, artifacts, inventory, identity):
    """Return {selection, artifact, content}, ready for the caller's atomic publication; write nothing."""
    turns = select_carried_readings(records, artifacts, inventory, identity, protocol=SELECTION_PROTOCOL)
    selection = dict(protocol=SELECTION_PROTOCOL, inventory_digest=digest(inventory), turns=turns)
    content = serialize_record(selection)
    return dict(selection=selection, artifact=describe_artifact(content, 'application/json'), content=content)


def _require_shape(value, keys):
    v7._require_selected(isinstance(value, dict) and set(value) == set(keys))


def _verify_entry_spans(inventory, artifacts, observation, selected):
    sources = _index_sources(inventory)
    for item in selected['entries']:
        entry = observation[item['kind'] + 's'][item['index']]
        unit = sources[(digest(entry['identity']), entry['source_sha256'])]
        text = artifacts.read(unit['artifact']).decode('utf-8')[entry['start']:entry['end']]
        v7._require_selected(text == entry['text'] and hashlib.sha256(text.encode('utf-8')).hexdigest() == entry['sha256'])


def _list_selection_endpoints(records, selection, cutoff):
    """For each chain that gives a stored turn of a selection, its last stored turn, which was recorded before the
    cutoff revision. A stored turn of another form ends with the selection refusal."""
    endpoints, revisions = {}, {}
    protocol = selection.get('protocol')
    for selected in selection['turns']:
        _require_shape(selected, ('origin', 'ranges', 'searches', 'entries', 'notes'))
        origin = selected['origin']
        _require_shape(origin, ('chain', 'protocol', 'assignment_id', 'attempt_id'))
        key, chain = origin['assignment_id'], origin['chain']
        assignment = records['review_assignment'][key]
        revision = _read_cutoff_revision(records, 'review_assignment', key, assignment, protocol)
        v7._require_selected(revision is not None and revision < cutoff)
        if chain not in revisions or revision > revisions[chain]:
            endpoints[chain] = assignment
            revisions[chain] = revision
    return endpoints


def list_unverified_carried_turns(records, artifacts, first):
    """Rule C28: one row for each earlier chain with a turn on which the stored selection of first rests and whose
    context the review records of the present do not verify, or no row.

    The selection rests on each chain that gives a stored turn, from its first turn to its last stored turn, and on
    the selection that such a chain stores, at any depth. The observer of each such chain requires a verified context
    at every position, and two kinds of record that a command writes later decide that context: a probe of the route
    record of the turn (route_state) and a contamination event that names the turn. This function reads these two
    and nothing else; it authenticates no attempt and rebuilds no packet.

    A row is {chain, assignment_id, events, route: {id, context_status}, count, recovery}, merged over every path to
    the chain. count is the number of such turns of the chain. recovery is export when a contamination event names
    the turn, or when its stale route record is retired or its latest answered probe failed; retire for the other
    stale routes; and probe otherwise. assignment_id names the first such turn with recovery export, or else the
    first such turn. A stored selection of another form ends with the selection refusal, and a refusal of the
    store passes through."""
    from .review_protocol import find_answered_route_status, route_state
    from .review_route_plan import read_route_standing
    contaminated = {}
    for key, event in storage.read_filtered_records(records, 'review_context_event', kind='contamination').items():
        if event['kind'] == 'contamination':
            contaminated.setdefault(event['assignment_id'], []).append(key)
    states, recoveries, found, pending, seen = {}, {}, {}, [first], set()
    while pending:
        current = pending.pop(0)
        if current['id'] in seen or current.get('carried_selection') is None:
            continue
        seen.add(current['id'])
        try:
            selection = json.loads(artifacts.read(current['carried_selection']['artifact']))
            endpoints = _list_selection_endpoints(records, selection, current['recorded_revision'])
            chains = [(records['review_assignment'][chain_id], reading._list_chain(records, endpoint))
                      for chain_id, endpoint in endpoints.items()]
        except (KeyError, TypeError, ValueError, IndexError):
            v7._require_selected(False)
        for chain_first, members in chains:
            pending.append(chain_first)
            for member in members:
                route_id, events = member['route_id'], sorted(contaminated.get(member['id'], []))
                if route_id not in states:
                    states[route_id] = route_state(records, artifacts, route_id)['context_status']
                if not events and states[route_id] == 'verified_for_route':
                    continue
                if not events and route_id not in recoveries:
                    standing = read_route_standing(records, artifacts, route_id)
                    recovery = 'probe'
                    if standing['stale']:
                        answered = (None if standing['retired']
                                    else find_answered_route_status(records, artifacts, route_id))
                        recovery = ('export' if standing['retired'] or answered not in (None, 'verified_for_route')
                                    else 'retire')
                    recoveries[route_id] = recovery
                found.setdefault(chain_first['id'], {})[member['id']] = dict(
                    chain=chain_first['id'], assignment_id=member['id'], events=events,
                    route=dict(id=route_id, context_status=states[route_id]),
                    recovery='export' if events else recoveries[route_id])
    rows = []
    for turns in found.values():
        listed = list(turns.values())
        decisive = next((row for row in listed if row['recovery'] == 'export'), listed[0])
        rows.append(dict(decisive, count=len(listed)))
    return rows


def verify_carried_selection(records, artifacts, inventory, identity, selection, *, plan=None):
    """Verify a stored JSON selection item by item and return its turn projections, without selecting.

    selection is its stored JSON value or artifact reference. Only named origin prefixes are authenticated.
    Later candidates cannot change a stored selection.
    Any invalid selected item uses project_carried_turn's existing refusal and message, and so do a refusal in
    PREFIX_STATE_CODES or STORE_REFUSAL_CODES and a value of the wrong shape. Every other refusal passes through.
    """
    try:
        if isinstance(selection, dict) and 'path' in selection:
            content = artifacts.read(selection)
            selection = json.loads(content)
            v7._require_selected(serialize_record(selection) == content)
        keys = ('protocol', 'inventory_digest', 'turns')
        _require_shape(selection, keys + ('affected_notes',) if 'affected_notes' in selection else keys)
        v7._require_selected(selection['protocol'] in (SELECTION_PROTOCOL_V1,) + CUTOFF_PROTOCOLS
                             and selection['inventory_digest'] == digest(inventory)
                             and isinstance(selection['turns'], list))
        protocol = selection['protocol']
        eligible = _select_recorded_attempts(records, identity, protocol)
        artifacts = Evaluation.of(eligible, artifacts)
        observations, notes, projections, seen = {}, {}, [], set()
        endpoints = _list_selection_endpoints(records, selection, identity['recorded_revision'])
        for chain_id, endpoint in endpoints.items():
            first = records['review_assignment'][chain_id]
            same_reviewer = normalized_text(first['reviewer_id']) == normalized_text(identity['reviewer_id'])
            chain = _describe_chain(records, artifacts, first, identity, protocol=protocol, report_only=not same_reviewer)
            v7._require_selected(chain is not None and same_reviewer)
            view = read_chain_records(eligible, first, protocol)
            observed = CHAIN_OBSERVERS[chain['protocol']](view, Evaluation.of(view, artifacts), endpoint)
            classes = classify_carried_notes(inventory, observed,
                search_dependencies=_list_search_dependencies(records, artifacts, first,
                    packet_kind=identity.get('packet_kind', 'problem')))
            for value, classification in zip(observed, classes):
                observations[value['origin']['assignment_id']] = value
                notes[value['origin']['assignment_id']] = classification
        previous_revision = -1
        for selected in selection['turns']:
            origin = selected['origin']
            v7._require_selected(digest(origin) not in seen)
            seen.add(digest(origin))
            observation = observations[origin['assignment_id']]
            v7._require_selected(observation['origin'] == origin and observation['turn']['kind'] == 'reading'
                                 and selected['notes'] == notes[origin['assignment_id']]
                                 and (selection['protocol'] != SELECTION_PROTOCOL_V1
                                      or bool(selected['ranges'] or selected['searches'])))
            key = origin['attempt_id']
            revision = _read_cutoff_revision(eligible, 'review_attempt', key, eligible['review_attempt'][key], protocol)
            v7._require_selected(previous_revision < revision < identity['recorded_revision'])
            previous_revision = revision
            for span in selected['ranges']:
                _require_shape(span, ('unit', 'part', 'parts', 'start', 'end', 'sha256'))
            for entry in selected['entries']:
                _require_shape(entry, ('kind', 'index'))
            projection = v7.project_carried_turn(inventory, observation, selected)
            _verify_entry_spans(inventory, artifacts, observation, selected)
            projections.append(projection)
        if 'affected_notes' in selection:
            v7._require_selected(isinstance(selection['affected_notes'], list))
            if selection['affected_notes']:
                v7._require_selected(plan is not None)
                rebuilt = v7.build_chain_state(inventory, projections, route=identity['route'], plan=plan)
                v7._require_selected(selection['affected_notes'] == rebuilt['state']['affected_notes'])
            else:
                v7._require_selected(not any(row['notes']['classification'] == 'affected' for row in projections))
        return projections
    except ResearchError as error:
        if error.code not in PREFIX_STATE_CODES + STORE_REFUSAL_CODES:
            raise
        v7._require_selected(False)
    except (KeyError, TypeError, ValueError, IndexError):
        v7._require_selected(False)


def verify_stored_selection(records, artifacts, inventory, identity, selection, *, cutoff=None, plan=None):
    """Require a stored selection of CUTOFF_PROTOCOLS to equal the selection of the cutoff under its own protocol,
    then authenticate its items today.

    Version 1 retains its item-only verification. Artifact refusals from selecting the cutoff
    pass through unchanged, before any comparison with the stored turns.
    """
    try:
        if isinstance(selection, dict) and 'path' in selection:
            content = artifacts.read(selection)
            selection = json.loads(content)
            v7._require_selected(serialize_record(selection) == content)
        protocol = selection['protocol']
    except (ResearchError, KeyError, TypeError, ValueError):
        v7._require_selected(False)
    if protocol in CUTOFF_PROTOCOLS:
        v7._require_selected(cutoff is not None)
        expected = select_carried_readings(cutoff.records, cutoff, inventory, identity, protocol=protocol)
        v7._require_selected(selection.get('turns') == expected)
    return verify_carried_selection(records, artifacts, inventory, identity, selection, plan=plan)


def _describe_missing_ranges(unit, spans):
    missing, start = [], 0
    for left, right in _merge_ranges(spans):
        if start < left:
            missing.append(dict(start=start, end=left, characters=left - start))
        start = max(start, right)
    if start < unit['characters']:
        missing.append(dict(start=start, end=unit['characters'], characters=unit['characters'] - start))
    return dict(unit=unit['handle'], ranges=missing, characters=sum(row['characters'] for row in missing))


def _describe_outputs(records, artifacts, inventory, identity, selection, qualifying):
    """Account for every verified excerpt and delivered passage of each qualifying chain."""
    chains = [(chain['chain'], [row for row in observe_qualifying_chain(records, artifacts, chain, identity,
                                                                         protocol=selection['protocol'])
               if row['origin']['chain'] == chain['chain'] and row['turn']['kind'] == 'reading'])
              for chain in qualifying]
    credits = {row['origin']['assignment_id']: row for row in selection['turns']}
    derived = derive_carried_turns(inventory, chains, credits)
    sources, outputs = _index_sources(inventory), []
    for chain, (chain_id, observations) in zip(qualifying, chains):
        row = dict(chain=chain_id, protocol=chain['protocol'], reading_turns=len(observations),
                   carried_turns=sum(value['origin']['assignment_id'] in derived for value in observations),
                   carried=dict(excerpts=0, passages=0, characters=0),
                   superseded=dict(excerpts=0, passages=0, characters=0),
                   source_changed=dict(excerpts=0, passages=0, characters=0))
        superseded_units = set()
        for observation in observations:
            carried = derived.get(observation['origin']['assignment_id'], [])
            for kind in ('excerpt', 'passage'):
                for index, entry in enumerate(observation[kind + 's']):
                    if entry['status'] != ('verified' if kind == 'excerpt' else 'delivered'):
                        continue
                    unit = sources.get((digest(entry['identity']), entry['source_sha256']))
                    state = ('source_changed' if unit is None else 'carried'
                             if dict(kind=kind, index=index) in carried else 'superseded')
                    row[state][kind + 's'] += 1
                    row[state]['characters'] += len(entry['text'])
                    if state == 'superseded':
                        superseded_units.add(unit['handle'])
        if superseded_units:
            row['message'] = v7.SUPERSEDED_TEXT.format(chain=chain_id, units=len(superseded_units),
                entries=row['superseded']['excerpts'] + row['superseded']['passages'])
        outputs.append(row)
    return outputs


def describe_carried_selection(records, artifacts, inventory, identity, selection, *, plan=None):
    """Describe stored selection JSON, with logical credit and the physical work the new chain still reads.

    Counts use unions of ranges. Completed original searches cover their current physical components;
    components give no credit independently. Equal material bodies are counted once by the inventory. A span of
    inventory.finding_units stays remaining work, as in the reading plan of the chain (G8.12).
    No scientific text enters the report.
    """
    projections = verify_carried_selection(records, artifacts, inventory, identity, selection, plan=plan)
    ranges, material_ranges, chains, searches, entries = {}, {}, {}, [], []
    units = {row['handle']: row for row in inventory['units']}
    material = {row['handle']: row for row in inventory['material']}
    notes = dict(current=0, affected=0)
    for selected, projected in zip(selection['turns'], projections):
        origin = selected['origin']
        chains.setdefault(origin['chain'], []).append(copy.deepcopy(selected))
        notes[selected['notes']['classification']] += 1
        searches.extend(selected['searches'])
        entries.extend(dict(origin=origin, **item) for item in selected['entries'])
        for span in projected['deliveries']:
            ranges.setdefault(span['unit'], []).append((span['start'], span['end']))
            unit = units[span['unit']]
            for body in unit['bodies']:
                interval = ((0, material[body]['characters'])
                            if unit['identity'].get('record_kind') == 'literature_search'
                            else (span['start'], span['end']))
                material_ranges.setdefault(body, []).extend(v7.remove_finding_spans(inventory, body, [interval]))
    remaining = [_describe_missing_ranges(unit, ranges.get(unit['handle'], [])) for unit in inventory['units']]
    remaining_material = [_describe_missing_ranges(body, material_ranges.get(body['handle'], []))
                          for body in inventory['material']]
    remaining = [row for row in remaining if row['ranges']]
    remaining_material = [row for row in remaining_material if row['ranges']]
    uncarried = list_passed_over_chains(records, artifacts, identity, protocol=selection['protocol'])
    report = dict(chains=[dict(chain=chain, turns=turns) for chain, turns in chains.items()],
                  ranges=[dict(unit=unit, start=start, end=end, characters=end - start)
                          for unit, spans in ranges.items() for start, end in _merge_ranges(spans)],
                  credited_characters=sum(_count_characters(spans) for spans in ranges.values()),
                  searches=searches, entries=entries, notes=notes,
                  remaining=remaining, remaining_characters=sum(row['characters'] for row in remaining),
                  remaining_material=remaining_material,
                  remaining_material_characters=sum(row['characters'] for row in remaining_material),
                  credited_material_characters=sum(_count_characters(spans) for spans in material_ranges.values()),
                  passed_over=uncarried['passed_over'],
                  outputs=_describe_outputs(records, artifacts, inventory, identity, selection,
                                            list_qualifying_chains(records, artifacts, identity,
                                                                  protocol=selection['protocol'])))
    if uncarried['unavailable_chains']:
        report['unavailable_chains'] = uncarried['unavailable_chains']
    return report
