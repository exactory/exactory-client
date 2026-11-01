"""Native complete-source input for new, explicitly opted-in result continuations."""

import copy
import hashlib
import json

from . import bar_sources
from .errors import ResearchError
from .evidence import digest
from .source_links import captured_source
from .bar_sources import descriptor

MODE = 'complete-visible-v1'
PROTOCOL = 'exactory-review-complete-source-v1'
MARKER = 'complete_source_delivery'
ALTERNATE = 'alternate-main-representation'
ALTERNATE_NOTE = ('Alternate main body omitted from this derived input. One authenticated complete main extraction of '
                  'the same exact version is supplied in full. Alternative provenance and required units remain; '
                  'original layout inspection is not asserted. The reviewer may request further source evidence.')


def _require(condition, message):
    if not condition:
        raise ResearchError('review_complete_source_mismatch', message)


def _json(artifacts, reference):
    return json.loads(artifacts.read(reference))


def _native_alternative(records, artifacts, version, row):
    from .components import validate_binding
    from .fulltext import extraction_measures
    from .graph import main_captures
    source = captured_source(records, artifacts, row['source_id'])
    _require(source.get('origin_verified') is True and source.get('capture_method') == 'http'
             and source.get('observed_identifier') == version and row['version_id'] == version,
             'Every alternative must retain an authenticated exact observed version')
    work = records['work'][version]
    captures = [capture for capture in work.get('fulltexts', []) if capture.get('source_id') == row['source_id']
                and descriptor(capture.get('original', {})) == row['original_descriptor']
                and descriptor(capture.get('text', {})) == row['extraction_descriptor']]
    _require(len(captures) == 1 and source['response'] == captures[0]['original'],
             'Source representations must bind native original and extraction artifacts')
    capture = captures[0]
    _require(capture.get('availability') == 'available' and capture.get('extraction_status') == 'extracted',
             'Select only available native complete extractions')
    validate_binding(records, artifacts, version, capture)
    original = artifacts.read(capture['original'])
    extracted = artifacts.read(capture['text']).decode('utf-8')
    _require(all(capture.get('extraction', {}).get(key) == value
                 for key, value in extraction_measures(extracted, len(original)).items()),
             'Native extraction measurements must match the complete exact bytes')
    main = {(item['source_id'], item['original']['sha256']) for item in main_captures(records, work)}
    _require(row['main_representation'] == ((row['source_id'], row['original_descriptor']['sha256']) in main),
             'Main representation status must come from native source components')


def selection(records, artifacts, deliveries):
    """Select one exact complete main, retaining required units and alternative provenance."""
    sources, alternate, protected = [], set(), set()
    for delivery in deliveries:
        request = delivery.get('request', {})
        if request.get('kind') != 'source' or request.get('depth') != 'fulltext' or delivery['status'] != 'delivered':
            continue
        _require(not delivery.get('source_scope_pending'), 'Unresolved required units cannot count as complete delivery')
        choices = delivery['source_alternatives']
        for row in choices:
            _native_alternative(records, artifacts, request['version_id'], row)
        eligible = [row for row in choices if row['main_representation'] and not row['source_scope_pending']]
        _require(bool(eligible), 'A requested complete source needs a complete authenticated main representation')
        choice = min(eligible, key=lambda row: (
            row['original_descriptor']['media_type'] != 'application/pdf', row['source_id']))
        selected = choice['extraction_descriptor']['sha256']
        original = choice['original_descriptor']['sha256']
        protected.update((selected, original))
        for row in choices:
            if row['main_representation'] and row['source_id'] != choice['source_id']:
                alternate.update((row['original_descriptor']['sha256'], row['extraction_descriptor']['sha256']))
        for bundle in delivery.get('declared_bundles', []):
            for unit in bundle['units']:
                link = unit.get('link')
                if link is not None and unit['kind'] in {'figure', 'table', 'equation', 'supplement'}:
                    protected.add(link['artifact_descriptor']['sha256'])
                    protected.update(identity for row in delivery.get('source_provenance', [])
                        if row.get('bundle_id') == bundle['id'] and row.get('unit_id') == unit['id']
                        for identity in row['delivered_sha256'])
        sources.append({'version_id': request['version_id'], 'selected_source_id': choice['source_id'],
                        'selected_text_sha256': selected, 'selected_original_sha256': original})
    return {'sources': sources, 'alternate_body_sha256': sorted(alternate - protected), 'protected_sha256': sorted(protected)}


def _lineage(records, artifacts, prior, *, include_bar=False):
    seen, chain = set(), []
    current, child, route_id, bridged = prior, None, prior['route_id'], False
    while current is not None and current['role'] in ({'bar', 'result'} if include_bar else {'result'}):
        _require(current['id'] not in seen, 'A result source lineage must be acyclic')
        seen.add(current['id'])
        _require(all(current[key] == prior[key] for key in ('reviewer_id', 'author_id', 'dossier_id')),
                 'Complete source delivery continues the same result reviewer and author')
        if current['route_id'] != route_id:
            _require(child is not None and not bridged, 'A source lineage permits only its one native visual transition')
            from .review_visual_route import validate_lineage_bridge
            validate_lineage_bridge(records, artifacts, child, current)
            route_id, bridged = current['route_id'], True
        chain.append(_json(artifacts, current['packet']))
        parent = (current.get('history_assignment_id') or current.get('context', {}).get('prior_assignment_id')
                  or current.get('context', {}).get('context_repair', {}).get('assignment_id'))
        child, current = current, records.get('review_assignment', {}).get(parent)
    return list(reversed(chain))


def _historical_sources(records, artifacts, prior):
    sources = {}
    for packet in _lineage(records, artifacts, prior, include_bar=True):
        for delivery in packet.get('source_deliveries', []):
            request = delivery.get('request', {})
            if request.get('kind') != 'source' or request.get('depth') != 'fulltext':
                continue
            for row in delivery.get('source_alternatives', []):
                value = {key: row[key] for key in ('source_id', 'version_id', 'original_descriptor', 'extraction_descriptor')}
                key = (value['source_id'], value['original_descriptor']['sha256'])
                _require(key not in sources or sources[key] == value, 'Historical exact source identities cannot change')
                sources[key] = value
    return [sources[key] for key in sorted(sources)]


def requests_for_lineage(records, artifacts, prior, output, *, graph=None):
    if prior.get('staged_review') is not None:
        from .direction_reviews import collect_selected_task_graph
        graph = graph or collect_selected_task_graph(records, artifacts, prior['id'])
        manifest = graph['epoch']['manifest']
        rows = {}
        for observed in graph['selection']['observations']:
            for row in bar_sources.requests(observed['output'], staged=True, manifest=manifest):
                previous = rows.get(row['id'])
                if previous is not None:
                    _require(all(previous[key] == row[key] for key in ('question', 'inputs', 'claim_ids', 'context_roots', 'requested_by')),
                             'A staged retrieval keeps its exact question and source scope')
                    if previous['status'] in ('resolved', 'unresolved'):
                        continue
                rows[row['id']] = row
        for row in bar_sources.requests(output, staged=True, manifest=manifest):
            previous = rows.get(row['id'])
            _require(previous is None or all(previous[key] == row[key] for key in
                     ('question', 'inputs', 'claim_ids', 'context_roots', 'requested_by')), 'Retrieval identifiers cannot change meaning')
            rows[row['id']] = row
        return [rows[key] for key in sorted(rows) if rows[key]['status'] in ('pending', 'delivered')]
    requests = {}
    for packet in _lineage(records, artifacts, prior):
        rows = [row['request'] for row in packet.get('source_deliveries', [])] + packet.get('pending_source_requests', [])
        for row in rows:
            if row.get('kind') == 'source' and row.get('depth') == 'fulltext':
                _require(row['id'] not in requests or requests[row['id']] == row, 'Source request identifiers cannot change meaning')
                requests[row['id']] = row
    from .review_excerpts import KEY
    for row in bar_sources.requests(output, passages=KEY in _json(artifacts, prior['packet'])):
        if row.get('kind') == 'source' and row.get('depth') == 'fulltext':
            _require(row['id'] not in requests or requests[row['id']] == row, 'Source request identifiers cannot change meaning')
            requests[row['id']] = row
    return list(requests.values())


def _history(records, artifacts, prior, state):
    from . import review_transport as transport
    attempt = records['review_attempt'][state['attempt_id']]
    return transport.logical_messages(_json(artifacts, attempt['request'])) + [
        {'role': 'assistant', 'content': json.dumps(state['output'], sort_keys=True)}]


def _restoration(records, history, deliveries, historical_sources):
    from . import review_transport as transport
    packets = [json.loads(message['content'][len(transport.PACKET_LABEL):]) for message in history
               if message.get('role') == 'user' and isinstance(message.get('content'), str)
               and message['content'].startswith(transport.PACKET_LABEL)]
    search_sources = {response['source_id'] for packet in packets
                      for search in packet.get('problem_evidence', {}).get('openness_searches', [])
                      for response in search['search'].get('responses', [])}
    searches = {records['source'][identifier]['response']['sha256'] for identifier in search_sources}
    source_bodies = {reference['sha256'] for delivery in deliveries for reference in delivery.get('evidence', [])}
    historical_bodies = {row[key]['sha256'] for row in historical_sources
                         for key in ('original_descriptor', 'extraction_descriptor')}
    restore, search_elisions, existing_elisions = {}, set(), set()
    for packet in packets:
        for entry in packet.get('evidence', []):
            if entry.get('delivery') != 'body-elided-from-replayed-history' or entry.get('content') != '':
                continue
            identity = entry['artifact']['sha256']
            if identity in searches:
                search_elisions.add(identity)
            if identity not in source_bodies and identity not in searches and identity not in historical_bodies:
                restore[identity] = entry['artifact']
            elif identity not in source_bodies:
                existing_elisions.add(identity)
    return [restore[key] for key in sorted(restore)], sorted(search_elisions), sorted(existing_elisions)


def prepare(records, artifacts, prior, state, packet):
    """Derive the prospective contract solely from native source requests and records."""
    if prior.get('staged_review') is not None:
        from .direction_reviews import collect_selected_task_graph
        from .review_staged import PROTOCOL as STAGED_PROTOCOL
        graph = collect_selected_task_graph(records, artifacts, prior['id'])
        requested = requests_for_lineage(records, artifacts, prior, {}, graph=graph)
        _require('staged_review' in packet, 'Staged retrievals continue through fresh task inputs')
        packet[MARKER] = dict(protocol=STAGED_PROTOCOL, epoch_id=graph['epoch']['id'], source_requests=requested,
                             selected_observations_root=graph['completion']['selected_observations_root'],
                             selected_observation_ids=[row['id'] for row in graph['selection']['observations']])
        packet['staged_review']['pending_retrievals'] = requested
        return packet[MARKER]
    requested = requests_for_lineage(records, artifacts, prior, state['output'])
    _require(bool(requested), 'Complete-source mode requires an exact full-text request in this result lineage')
    rows = bar_sources.validate_inventory(artifacts, packet['source_inventory'])
    originals = {row['id']: row for row in rows}
    deliveries = [bar_sources._source(records, artifacts, request, originals) for request in requested]
    deliveries.extend(row for row in packet['source_deliveries'] if row['request'] not in requested)
    packet['source_deliveries'] = deliveries
    # A passage answer never stays pending.
    packet['pending_source_requests'] = [row['request'] for row in deliveries
                                         if row['status'] != 'delivered' and row['request']['kind'] != 'passage']
    history = _history(records, artifacts, prior, state)
    historical_sources = _historical_sources(records, artifacts, prior)
    restored, searches, existing = _restoration(records, history, deliveries, historical_sources)
    packet[MARKER] = dict(selection(records, artifacts, deliveries), protocol=PROTOCOL, source_requests=requested,
                         history_digest=digest(history), restored_evidence=restored, historical_search_elisions=searches,
                         historical_existing_elisions=existing, historical_source_bodies=historical_sources)
    # Reading credit and original capture provenance remain the source protocol's responsibility.
    return packet[MARKER]


def validate(records, artifacts, assignment, packet):
    """Reject an invented selection even when its caller-authored flags look complete."""
    value = packet.get(MARKER)
    if value is None:
        _require(assignment.get('context', {}).get('source_delivery') is None, 'A complete-source opt-in needs its native manifest')
        return
    if assignment.get('staged_review') is not None:
        from .review_staged import PROTOCOL as STAGED_PROTOCOL
        from .review_staged import verify_completion
        from .direction_reviews import collect_selected_task_graph
        graph = collect_selected_task_graph(records, artifacts, assignment['id'])
        _require(value.get('protocol') == STAGED_PROTOCOL and value.get('epoch_id') == graph['epoch']['id'],
                 'The staged source closure binds its exact epoch')
        identifiers = value.get('selected_observation_ids', [])
        observed = [row for row in graph['selection']['observations'] if row['id'] in identifiers]
        _require(len(identifiers) == len(set(identifiers)) and {row['id'] for row in observed} == set(identifiers),
                 'Retain every exact observation that supplied this frozen source request')
        selected = {'observations': observed}
        completion = verify_completion(graph['epoch'], selected, tasks=graph['tasks'])
        _require(value.get('selected_observations_root') == completion['selected_observations_root'],
                 'The staged source closure binds its exact frozen selection')
        frozen_graph = dict(graph, selection=selected)
        _require(value.get('source_requests') == requests_for_lineage(records, artifacts, assignment, {}, graph=frozen_graph),
                 'Retain every unfinished task retrieval and its exact source spans')
        return
    from . import review_visual_context_repair as visual_repair
    repair = visual_repair.MARKER in packet
    _require(value.get('protocol') == PROTOCOL and assignment['role'] == 'result'
             and (repair or (assignment.get('context', {}).get('source_delivery') == MODE
             and assignment.get('context', {}).get('prior_assignment_id'))), 'Use a prospective native result source continuation')
    expected = selection(records, artifacts, packet['source_deliveries'])
    _require(all(value[key] == item for key, item in expected.items()), 'Complete main selection must match native exact source records')
    if repair:
        prior, history, output = visual_repair.validate(records, artifacts, assignment, packet)
    else:
        prior = records['review_assignment'][assignment['context']['prior_assignment_id']]
        history = assignment['history']
        output = json.loads(history[-1]['content'])
    historical_sources = _historical_sources(records, artifacts, prior)
    _require(value['historical_source_bodies'] == historical_sources, 'Preserve only exact source identities from frozen prior packets')
    restored, searches, existing = _restoration(records, history, packet['source_deliveries'], historical_sources)
    _require(value['history_digest'] == digest(assignment['history']) and value['restored_evidence'] == restored
             and value['historical_search_elisions'] == searches and value['historical_existing_elisions'] == existing,
             'Restore required scientific evidence from the exact bound history')
    _require(value['source_requests'] == requests_for_lineage(records, artifacts, prior, output),
             'Every requested exact full-text source must remain in the complete input')
    delivered = [row['request'] for row in packet['source_deliveries']]
    _require(all(request in delivered for request in value['source_requests']), 'The native source closure must be delivered or explicitly pending')


def _complete(entry):
    from .review_transport import is_binary_evidence
    _require(entry is not None and entry.get('encoding') == 'utf-8' and isinstance(entry.get('content'), str),
             'Supply every selected complete source and required scientific artifact')
    data = entry['content'].encode('utf-8')
    reference = entry['artifact']
    _require(not is_binary_evidence(data, reference['media_type']), 'Binary originals require native delivery')
    _require(len(data) == reference['size'] and hashlib.sha256(data).hexdigest() == reference['sha256'],
             'Complete supplied bytes must match their exact artifact size and digest')
    return entry['content']


def project(packet, history):
    """Derive logical input without removing any non-alternative scientific body."""
    from . import review_transport as transport
    value = packet[MARKER]
    _require(value['protocol'] == PROTOCOL and value['history_digest'] == digest(history), 'Use the exact frozen history binding')
    entries = {entry['artifact']['sha256']: entry for entry in packet.get('evidence', [])}
    selected = {source['selected_text_sha256'] for source in value['sources']}
    alternate = set(value['alternate_body_sha256'])
    _require(not alternate.intersection(selected | set(value['protected_sha256'])), 'A required or selected source cannot be omitted')
    sources = {identity: _complete(entries.get(identity)) for identity in sorted(selected)}
    restored = {ref['sha256']: _complete(entries.get(ref['sha256'])) for ref in value['restored_evidence']}

    def projected(original):
        result = copy.deepcopy(original)
        for entry in result.get('evidence', []):
            identity = entry['artifact']['sha256']
            if identity in alternate:
                entry.update(content='', encoding='omitted', delivery=ALTERNATE, note=ALTERNATE_NOTE)
            elif identity in restored and entry.get('delivery') == 'body-elided-from-replayed-history':
                entry.update(content=restored[identity], encoding='utf-8', delivery='body-restored-for-current-review',
                             note='The original exact artifact bytes are supplied again for this review.')
        for delivery in result.get('source_deliveries', []):
            if 'evidence' in delivery:
                delivery['evidence'] = [reference for reference in delivery['evidence'] if reference['sha256'] not in alternate]
            for row in delivery.get('source_provenance', []):
                old = row.get('delivered_sha256', [])
                if alternate.intersection(old):
                    row.setdefault('captured_delivery_sha256', list(old))
                    row['delivered_sha256'] = [identity for identity in old if identity not in alternate]
        return result

    messages = []
    for message in history:
        content = message.get('content')
        if message.get('role') == 'user' and isinstance(content, str) and content.startswith(transport.PACKET_LABEL):
            supplied = projected(json.loads(content[len(transport.PACKET_LABEL):]))
            content = transport.PACKET_LABEL + json.dumps(supplied, sort_keys=True, ensure_ascii=False)
        messages.append(dict(message, content=content))
    supplied = projected(packet)
    messages.append({'role': 'user', 'content': transport.PACKET_LABEL + json.dumps(supplied, sort_keys=True, ensure_ascii=False)})
    return messages, sources
