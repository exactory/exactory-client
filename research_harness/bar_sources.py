"""Bounded initial bar context and evidence-bound reviewer source requests.

Inventory metadata never counts as source inspection. Continuations retain one
fixed inventory and satisfy actual observed requests from native source bytes.
"""

import hashlib
import json

from .errors import ResearchError
from .evaluation import Evaluation
from .evidence import digest
from .operations import fields, text
from .reading import current_readings, validate_read_evidence
from .source_links import captured_source, validate_link

PAGE_SIZE = 10
PROTOCOL = 'exactory-independent-review-sources-v2'
ROLES = ('bar', 'slate', 'result')
PROMPT = (
    ' The source_inventory is a complete, immutable, sorted inventory, with a fixed first page. '
    'Its rows describe metadata and recorded or currently verified inspection, not source content or truth. '
    'Only prior_context contains exact previously inspected passages; source_deliveries contains the '
    'actual bytes requested by you. Request any inventory page, literal case-insensitive ID/title query, '
    'or held source by returning optional source_requests: [{id,kind:"inventory_page",page}] or '
    '[{id,kind:"inventory_query",query,page}] or [{id,kind:"source",version_id,depth:"abstract"|"fulltext"}]. '
    'Pages are zero-based. You may combine request kinds. Request IDs must be unique and stable. '
    'Any nonempty source_requests requires value.status="unresolved" and support=null. It is a request turn, '
    'not a final scientific assessment; it may use empty value.evidence. Preserve the exact assigned stage. '
    'For bar only, preserve empty bar_ids, work_items, assurances and objection_findings, and both '
    'adequacies="not_assessed". For slate/result, provide a final complete phase response only after source requests settle. '
    'The harness will continue this same session with exact deterministic deliveries. Unavailable '
    'requests remain pending across turns, even if omitted from a later response. Do not infer that '
    'a requested source has been acquired, inspected, or supports a claim from inventory or delivery alone. '
    'Return a final phase assessment only after necessary requested evidence is supplied; use source_requests=[] or omit '
    'that field. Existing prior_context can be empty; request the sources needed for this phase.')


def descriptor(reference):
    return {'location': reference['path'], **{key: reference[key] for key in ('sha256', 'size', 'media_type')}}


def _reference(value):
    return {'path': value['location'], **{key: value[key] for key in ('sha256', 'size', 'media_type')}}


def _save(artifacts, value):
    return artifacts.put(json.dumps(value, sort_keys=True, ensure_ascii=False, allow_nan=False).encode(), 'application/json')


def _page(rows, inventory_digest, page, query=None, *, page_size=None):
    page_size = PAGE_SIZE if page_size is None else page_size
    if type(page_size) is not int or page_size < 1:
        raise ResearchError('review_inventory_mismatch', 'The original inventory page size must be a positive integer')
    if type(page) is not int or page < 0:
        raise ResearchError('invalid_review_source_request', 'Inventory pages are nonnegative integers, not booleans')
    selected = rows if query is None else [row for row in rows if query.casefold() in
        (row['id'] + '\n' + (row.get('title') or '')).casefold()]
    if page > 0 and page * page_size >= len(selected):
        raise ResearchError('invalid_review_source_request', 'The requested inventory page is out of range')
    return {'page': page, 'page_size': page_size, 'total': len(selected), 'inventory_total': len(rows),
            'inventory_digest': inventory_digest, 'query': query, 'metadata_not_read': True,
            'items': selected[page * page_size:(page + 1) * page_size],
            'next_page': page + 1 if (page + 1) * page_size < len(selected) else None}


def _selected_bundles(records, work):
    from .graph import main_captures, selected_bundle
    selected = {}
    for capture in main_captures(records, work) if work.get('fulltexts') else []:
        bundle = selected_bundle(records, work['id'], {'id': work['id'], 'sha256': capture['original']['sha256']})
        if bundle is not None:
            selected[bundle['id']] = bundle
    current = selected_bundle(records, work['id'])
    if current is not None:
        selected[current['id']] = current
    return [selected[key] for key in sorted(selected)]


def _capture_digest(work, records):
    values = [{'depth': 'abstract', 'source_id': item['source_id'], 'sha256': item['artifact']['sha256'], 'completeness': item.get('completeness', 'unknown')}
              for item in work.get('abstracts', [])]
    values.extend({'depth': 'fulltext', 'source_id': item['source_id'], 'availability': item.get('availability'),
                   'original_sha256': (item.get('original') or {}).get('sha256'),
                   'text_sha256': (item.get('text') or {}).get('sha256')} for item in work.get('fulltexts', []))
    from .reading import unit_digest
    from .visual_assets import current_asset
    values.extend({'bundle_id': bundle['id'], 'unit_digest': unit_digest(bundle, records)}
                  for bundle in _selected_bundles(records, work))
    for identifier in work.get('visual_asset_ids', []):
        asset = records.get('visual_asset', {}).get(identifier)
        if asset is not None and current_asset(records, work['id'], asset['html_sha256'], asset['url']) == asset:
            values.append({'visual_id': identifier, 'html_sha256': asset['html_sha256'], 'url': asset['url'],
                           'artifact_sha256': (asset.get('artifact') or {}).get('sha256'),
                           'availability': asset['availability'], 'validation_status': asset['validation_status']})
    return digest(sorted(values, key=digest))


def inventory(records, artifacts):
    evaluation = Evaluation.of(records, artifacts)
    rows = []
    for identifier, work in sorted(records.get('work', {}).items()):
        recorded = evaluation.readings_for(identifier)
        try:
            accepted, partial = current_readings(records, evaluation, identifier)
            current = sorted({reading['depth'] for reading in accepted})
            pending = sorted({reading['depth'] for reading in partial})
        except (ResearchError, KeyError, TypeError):
            current, pending = [], sorted({reading.get('depth', 'unknown') for reading in recorded})
        rows.append({'id': identifier, 'title': work.get('title'),
            'source_capture_digest': _capture_digest(work, records),
            'recorded_reading_status': sorted({reading.get('depth', 'unknown') for reading in recorded}),
            'current_reading_status': current, 'unverified_or_partial_reading_status': pending,
            'captured_abstract_status': sorted({a.get('completeness', 'unknown') for a in work.get('abstracts', [])}),
            'captured_depths': sorted((['abstract'] if work.get('abstracts') or work.get('abstract') else []) +
                (['fulltext'] if any(c.get('availability') == 'available' for c in work.get('fulltexts', [])) else []))})
    inventory_digest = digest(rows)
    return {'artifact_descriptor': descriptor(_save(artifacts, rows)), 'digest': inventory_digest,
            'total': len(rows), 'page_size': PAGE_SIZE, 'metadata_not_read': True,
            'first_page': _page(rows, inventory_digest, 0)}


def prior_context(records, artifacts, links):
    if not isinstance(links, list):
        raise ResearchError('invalid_review_context', 'Initial source_links must be an array of exact inspected links')
    result = []
    evaluation = Evaluation.of(records, artifacts)
    for link in links:
        inspection = validate_read_evidence(records, evaluation, link, depth='passage')
        context = validate_link(records, evaluation, link)
        kind = link['locator']['kind']
        if kind not in ('text', 'span', 'json'):
            raise ResearchError('invalid_review_context', 'Initial source context uses exact text or JSON passages; request original visuals through source_requests')
        content = context['value']
        if isinstance(content, str):
            data, media = content.encode('utf-8'), 'text/plain'
        else:
            data, media = json.dumps(content, sort_keys=True, ensure_ascii=False, allow_nan=False).encode(), 'application/json'
        location = {'kind': kind}
        if kind == 'json':
            location['pointer'] = link['locator']['pointer']
        else:
            location.update(start=link['locator']['start'], end=link['locator']['end'])
        location['sha256'] = hashlib.sha256(data).hexdigest()
        result.append({'version_id': link['version_id'], 'inspection': inspection,
            'origin': {'source_id': link['source_id'], 'artifact_descriptor': descriptor(link['artifact']), 'locator': location},
            'excerpt': artifacts.put(data, media), 'scientific_truth_certified': False})
    return result


def requests(output):
    values = output.get('source_requests', []) if isinstance(output, dict) else []
    if not isinstance(values, list):
        raise ResearchError('invalid_review_source_request', 'source_requests must be an array')
    seen = set()
    for request in values:
        if not isinstance(request, dict):
            raise ResearchError('invalid_review_source_request', 'Each source request is an object')
        kind = request.get('kind')
        text(kind, 'Source request kind', code='invalid_review_source_request')
        required = {'inventory_page': ('id', 'kind', 'page'),
                    'inventory_query': ('id', 'kind', 'query', 'page'),
                    'source': ('id', 'kind', 'version_id', 'depth')}.get(kind)
        if required is None:
            raise ResearchError('invalid_review_source_request', 'Use a native inventory or exact source request')
        fields(request, required, code='invalid_review_source_request')
        text(request['id'], 'Request ID', code='invalid_review_source_request')
        if request['id'] in seen:
            raise ResearchError('invalid_review_source_request', 'Source request IDs must be unique')
        seen.add(request['id'])
        if kind == 'source':
            text(request['version_id'], 'Exact held version ID', code='invalid_review_source_request')
            from .identities import normalize_identifier, version_of
            try:
                canonical = normalize_identifier(request['version_id'])
            except ResearchError as error:
                raise ResearchError('invalid_review_source_request', 'Request a canonical native source identifier') from error
            if canonical != request['version_id'] or canonical.startswith('arxiv:') and version_of(canonical) is None:
                raise ResearchError('invalid_review_source_request', 'Request the canonical exact version, including the arXiv version suffix')
            if request['depth'] not in ('abstract', 'fulltext'):
                raise ResearchError('invalid_review_source_request', 'Source depth is abstract or fulltext')
        else:
            if type(request['page']) is not int or request['page'] < 0:
                raise ResearchError('invalid_review_source_request', 'Inventory pages are nonnegative integers')
            if kind == 'inventory_query':
                text(request['query'], 'Literal ID or title query', code='invalid_review_source_request')
    if values:
        value = output.get('value')
        if not isinstance(value, dict):
            raise ResearchError('invalid_review_source_request', 'A source request turn requires an unresolved value object')
        if output.get('stage') not in ROLES or output.get('support') is not None or value.get('status') != 'unresolved':
            raise ResearchError('invalid_review_source_request', 'A source request is an unresolved strategic phase turn without a final support assessment')
        if output['stage'] == 'bar' and (any(value.get(key) != 'not_assessed' for key in ('objective_adequacy', 'method_adequacy'))
                or any(value.get(key) != [] for key in ('bar_ids', 'work_items', 'assurances', 'objection_findings'))):
            raise ResearchError('invalid_review_source_request', 'A bar source request precedes objective or method assessment')
    return values


def pending(packet, output):
    combined = {request['id']: request for request in packet.get('pending_source_requests', [])}
    for request in requests(output):
        if request['id'] in combined and combined[request['id']] != request:
            raise ResearchError('invalid_review_source_request', 'A pending request ID cannot name different source evidence')
        combined[request['id']] = request
    return list(combined.values())


def _compact_locator(locator):
    kind = locator['kind']
    if kind in ('text', 'span'):
        return {'kind': 'span', 'start': locator['start'], 'end': locator['end'],
                'sha256': locator['sha256'] if kind == 'span' else hashlib.sha256(locator['quote'].encode()).hexdigest()}
    if kind == 'json':
        return {'kind': kind, 'pointer': locator['pointer'], 'value_digest': digest(locator['value'])}
    if kind == 'html':
        return {'kind': kind, 'anchor': _compact_locator(locator['anchor']),
                'assets': [{'url': asset['url'], 'source_id': asset['source_id'],
                            'artifact_descriptor': descriptor(asset['artifact'])} for asset in locator.get('assets', [])]}
    return dict(locator)


def _compact_link(link):
    return {'version_id': link['version_id'], 'source_id': link['source_id'],
            'artifact_descriptor': descriptor(link['artifact']), 'locator': _compact_locator(link['locator'])}


def _html_assets(records, artifacts, work, capture):
    from .html_visuals import _VisualMarkup, resource_inventory, validate_visual
    from .source_links import span_locator
    from .visual_assets import current_asset
    original = capture['original']
    if original['media_type'] not in ('text/html', 'application/xhtml+xml'):
        return [], []
    content = artifacts.read(original).decode('utf-8')
    parser = _VisualMarkup(content)
    parser.feed(content)
    parser.close()
    visual_tags = {'figure', 'table', 'picture', 'svg', 'math', 'img', 'image', 'object', 'embed', 'canvas'}
    visual_classes = {'ltx_figure', 'figure', 'ltx_table', 'ltx_equation'}
    if not any(node['tag'] in visual_tags or set((dict(node['attrs']).get('class') or '').lower().split()) & visual_classes
               for node in parser.nodes):
        return [], []
    source = captured_source(records, artifacts, capture['source_id'])
    anchor = span_locator(content, 0, len(content), excerpt_length=0)
    inventory = resource_inventory(content, source['url'], anchor)
    links = []
    for resource in inventory['resources']:
        asset = current_asset(records, work['id'], original['sha256'], resource['url'])
        if asset is not None and asset['availability'] == 'available':
            links.append({'url': asset['url'], 'source_id': asset['source_id'], 'artifact': asset['artifact']})
    link = {'version_id': work['id'], 'source_id': capture['source_id'], 'artifact': original,
            'locator': {'kind': 'html', 'anchor': anchor, 'assets': links}}
    visual = validate_link(records, artifacts, link)['visual']
    delivered = []
    for asset in links:
        try:
            validate_visual(artifacts.read(asset['artifact']), asset['artifact']['media_type'])
        except ResearchError:
            # visual_context retains the exact validation failure as a pending
            # source obligation; malformed image bytes are not asserted delivered.
            continue
        delivered.append({'source_id': asset['source_id'], 'artifacts': [asset['artifact']],
                          'kind': 'visual_asset', 'completeness': 'complete', 'url': asset['url'],
                          'html_sha256': original['sha256']})
    return delivered, visual['pending']


def _declared_units(records, artifacts, work):
    from .reading import required_unit_obligations, unit_digest
    scopes, captures, pending = [], [], []
    evaluation = Evaluation.of(records, artifacts)
    for bundle in _selected_bundles(records, work):
        scopes.append({'id': bundle['id'], 'scope': bundle['scope'], 'completeness': bundle['completeness'],
            'original_sha256': bundle['original_sha256'], 'unit_digest': unit_digest(bundle, records),
            'units': [{'id': unit['id'], 'kind': unit['kind'], 'required': unit['required'],
                       'link': _compact_link(unit['link']) if unit['link'] is not None else None,
                       'url': unit.get('url')} for unit in bundle['units']]})
        pending.extend(required_unit_obligations(records, evaluation, bundle))
        if bundle['scope'] != 'article' or bundle['completeness'] != 'complete':
            pending.append({'code': 'source_bundle_incomplete', 'bundle_id': bundle['id'], 'version_id': work['id']})
        for unit in bundle['units']:
            if unit['link'] is None:
                continue
            context = evaluation.link(unit['link'])
            capture = context['capture']
            evidence = [unit['link']['artifact']]
            if capture is not None:
                evidence.extend(a for a in (capture.get('original'), capture.get('text')) if a is not None and a not in evidence)
            captures.append({'source_id': unit['link']['source_id'], 'artifacts': evidence, 'kind': 'source_unit',
                             'completeness': 'complete' if capture is not None and capture.get('availability') == 'available' else 'unknown',
                             'unit_id': unit['id'], 'bundle_id': bundle['id']})
            for asset in unit['link']['locator'].get('assets', []):
                captures.append({'source_id': asset['source_id'], 'artifacts': [asset['artifact']], 'kind': 'visual_asset',
                                 'completeness': 'complete', 'unit_id': unit['id'], 'bundle_id': bundle['id'], 'url': asset['url']})
    return scopes, captures, pending


def _source(records, artifacts, request, original_rows):
    work = records.get('work', {}).get(request['version_id'])
    result = {'request': request, 'status': 'unavailable', 'evidence': [], 'source_provenance': [],
              'reading_credit': False, 'scientific_truth_certified': False,
              'source_scope_pending': [], 'declared_bundles': [], 'scope_status': 'not_inventoried',
              'acquired_after_inventory': request['version_id'] not in original_rows}
    prior_digest = original_rows.get(request['version_id'], {}).get('source_capture_digest')
    current_digest = _capture_digest(work, records) if work is not None else None
    result['native_source_delta'] = {'inventory_capture_digest': prior_digest, 'current_capture_digest': current_digest,
                                     'changed': prior_digest != current_digest}
    if work is None:
        return dict(result, reason='The exact requested version is not held in the current native source records.')
    if request['depth'] == 'abstract':
        captures = [{'source_id': a['source_id'], 'artifacts': [a['artifact']], 'completeness': a.get('completeness', 'unknown')}
                    for a in work.get('abstracts', [])]
    else:
        captures = [{'source_id': c['source_id'], 'completeness': 'complete',
                     'artifacts': [a for a in (c.get('original'), c.get('text')) if a is not None]}
                    for c in work.get('fulltexts', []) if c.get('availability') == 'available']
    if request['depth'] == 'fulltext':
        from .components import validate_binding
        for capture in work.get('fulltexts', []):
            if capture.get('availability') == 'available':
                source = captured_source(records, artifacts, capture['source_id'])
                if (capture.get('requested_version_id') != work['id'] or source.get('requested_identifier') != work['id']
                        or capture.get('original') != source['response'] or source.get('capture_method') != 'http'
                        or source.get('origin_verified') is not True or capture.get('extraction_status') != 'extracted'
                        or capture.get('text') is None):
                    raise ResearchError('review_source_binding_mismatch', 'Deliver the exact acquired version, original response and recorded extraction')
                if source.get('observed_identifier') is not None and source['observed_identifier'] != work['id']:
                    raise ResearchError('review_source_binding_mismatch', 'The observed source version differs from the requested exact version')
                original = artifacts.read(capture['original'])
                extracted = artifacts.read(capture['text']).decode('utf-8')
                from .fulltext import extraction_measures
                if any(capture.get('extraction', {}).get(key) != value for key, value in extraction_measures(extracted, len(original)).items()):
                    raise ResearchError('review_source_binding_mismatch', 'The extraction bytes no longer match their recorded native measurements')
                validate_binding(records, artifacts, work['id'], capture)
                visual_captures, visual_pending = _html_assets(records, artifacts, work, capture)
                captures.extend(visual_captures)
                result['source_scope_pending'].extend(visual_pending)
        scopes, unit_captures, unit_pending = _declared_units(records, artifacts, work)
        captures.extend(unit_captures)
        result['declared_bundles'] = scopes
        result['source_scope_pending'].extend(unit_pending)
        if scopes:
            result['scope_status'] = 'declared_with_pending_units' if unit_pending else 'declared_units_available'
    for capture in captures:
        source = captured_source(records, artifacts, capture['source_id'])
        for reference in capture['artifacts']:
            artifacts.read(reference)
            if reference not in result['evidence']:
                result['evidence'].append(reference)
        result['source_provenance'].append({'source_id': source['id'], 'captured_at': source.get('captured_at'),
            'original_descriptor': descriptor(source['response']), 'completeness': capture['completeness'],
            'capture_method': source.get('capture_method'), 'origin_verified': source.get('origin_verified'),
            'content_scope': source.get('content_scope'),
            **{key: capture[key] for key in ('kind', 'url', 'html_sha256', 'unit_id', 'bundle_id') if key in capture},
            'delivered_sha256': [a['sha256'] for a in capture['artifacts']]})
    if result['evidence'] and not result['source_scope_pending'] and any(capture['completeness'] == 'complete' for capture in captures):
        result.update(status='delivered', reason='All available native captures at the requested depth are supplied. Delivery does not certify complete reading or scientific support.')
    elif result['evidence']:
        result.update(status='partial', reason='The supplied captures are partial, of unknown completeness, or have pending required source units; the requested source depth remains pending.')
    else:
        result['reason'] = 'No complete captured source bytes at the requested depth are available in the current native records.'
    return result


def validate_inventory(artifacts, inventory_value):
    try:
        rows = json.loads(artifacts.read(_reference(inventory_value['artifact_descriptor'])))
        valid = (isinstance(rows, list) and type(inventory_value['total']) is int
                 and len(rows) == inventory_value['total'] and digest(rows) == inventory_value['digest']
                 and type(inventory_value['page_size']) is int and inventory_value['page_size'] > 0)
        identifiers = [row['id'] for row in rows] if valid else []
        if not valid or identifiers != sorted(set(identifiers)):
            raise ResearchError('review_inventory_mismatch', 'Retain the complete exact sorted inventory snapshot')
        if inventory_value.get('first_page') != _page(rows, inventory_value['digest'], 0, page_size=inventory_value['page_size']):
            raise ResearchError('review_inventory_mismatch', 'The advertised first page must match the immutable inventory')
        return rows
    except (ValueError, TypeError, KeyError) as error:
        raise ResearchError('review_inventory_mismatch', 'The complete inventory descriptor and saved rows must remain readable and exact') from error


def continuation(records, artifacts, previous_packet, output):
    inventory_value = previous_packet['source_inventory']
    rows = validate_inventory(artifacts, inventory_value)
    deliveries, outstanding = [], []
    original_rows = {row['id']: row for row in rows}
    requested = pending(previous_packet, output)
    for request in requested:
        if request['kind'] == 'source':
            delivery = _source(records, artifacts, request, original_rows)
        else:
            delivery = {'request': request, 'status': 'delivered', 'page': _page(rows, inventory_value['digest'],
                request['page'], request.get('query'), page_size=inventory_value['page_size']), 'reading_credit': False}
        deliveries.append(delivery)
        if delivery['status'] != 'delivered':
            outstanding.append(request)
    return {'source_inventory': inventory_value, 'prior_context': [], 'source_deliveries': deliveries,
            'pending_source_requests': outstanding}
