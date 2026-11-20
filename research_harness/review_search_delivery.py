"""Exact search-field delivery plans and reconstruction for problem openness.

Planning does not publish artifacts. Delivery verification consumes only bodies and receipts that the caller has
derived from authenticated requests of one chain. A manifest or an artifact on disk supplies no delivery credit.
"""

import copy
import hashlib
import json

from .artifacts import describe_artifact
from .errors import ResearchError
from .problem_openness import serialize_record

SHARED_FIELDS = ('dispositions', 'queries', 'responses', 'scope')
MEDIA_TYPE = 'application/json'
FINDING_INDEX_FIELDS = ('queries', 'scope', 'dispositions', 'resolved', 'row_dispositions')
FINDING_INDEX_MESSAGE = 'The finding index differs from its bound search or reading originals.'
# The field of the inventory unit that holds an index too long for any form with unit references (G8.5).
FINDING_INDEX_UNIT_FIELD = 'finding_index'


def build_finding_index(records, artifacts, native, *, formulations=None):
    """Freeze bound search fields and the identities of every bound reading's work."""
    from .evidence import digest
    minimum = native['scientific_context']['openness_minimum']
    evidence = {row['artifact']['sha256']: row['artifact'] for row in native.get('evidence', [])}

    def read_original(sha256):
        reference = evidence.get(sha256)
        if reference is None:
            raise ResearchError('openness_reading_incomplete', FINDING_INDEX_MESSAGE)
        content = artifacts.read(reference)
        if hashlib.sha256(content).hexdigest() != sha256:
            raise ResearchError('openness_reading_incomplete', FINDING_INDEX_MESSAGE)
        return json.loads(content)

    searches, values = {}, {}
    for binding in minimum['searches']:
        formulation, sha256 = binding['formulation'], binding['search_digest']
        if formulations is not None and formulation not in formulations:
            continue
        if sha256 not in searches:
            search = read_original(sha256)
            entry = dict(record_id=search['id'], search_digest=sha256, formulations=[])
            for field in FINDING_INDEX_FIELDS:
                if field in search:
                    key = digest(search[field])
                    entry[field] = key
                    values[key] = copy.deepcopy(search[field])
            searches[sha256] = entry
        if formulation not in searches[sha256]['formulations']:
            searches[sha256]['formulations'].append(formulation)
    reading_digests = list(dict.fromkeys(minimum['reading_digests'] + minimum.get('supporting_reading_digests', [])))
    readings = [(read_original(sha256), sha256) for sha256 in reading_digests]
    read_works = []
    for reading, sha256 in sorted(readings, key=lambda row: row[0]['id']):
        version = reading['version_id']
        work = records.get('work', {}).get(version, {})
        identifiers = list(dict.fromkeys([version] + ([work['work_id']] if 'work_id' in work else [])
                                        + work.get('aliases', [])))
        read_works.append(dict(reading_id=reading['id'], reading_digest=sha256,
            version_id=version, identifiers=identifiers, title=work.get('title', '')))
    return dict(searches=list(searches.values()), fields=values, read_works=read_works)


def validate_finding_index(index):
    """Authenticate every field body before any finding rule reads its value."""
    from .evidence import digest
    valid = (isinstance(index, dict) and set(index) == {'searches', 'fields', 'read_works'}
        and isinstance(index['fields'], dict) and isinstance(index['searches'], list)
        and isinstance(index['read_works'], list)
        and all(digest(value) == key for key, value in index['fields'].items())
        and all(isinstance(row, dict) and {'record_id', 'search_digest', 'formulations'} <= set(row)
            and set(row) <= {'record_id', 'search_digest', 'formulations'} | set(FINDING_INDEX_FIELDS)
            and isinstance(row['formulations'], list)
            and all(isinstance(row[field], str) and row[field] in index['fields']
                    for field in FINDING_INDEX_FIELDS if field in row)
            for row in index['searches'])
        and all(isinstance(row, dict) and set(row) == {
            'reading_id', 'reading_digest', 'version_id', 'identifiers', 'title'}
            and all(isinstance(row[key], str) for key in ('reading_id', 'reading_digest', 'version_id', 'title'))
            and isinstance(row['identifiers'], list) and all(isinstance(value, str) for value in row['identifiers'])
            for row in index['read_works']))
    if not valid:
        raise ResearchError('openness_reading_incomplete', FINDING_INDEX_MESSAGE)


def select_finding_index(index, formulations):
    """Keep the search entries of the formulations judged by one final page."""
    searches = [dict(copy.deepcopy(row), formulations=[value for value in row['formulations'] if value in formulations])
                for row in index['searches'] if any(value in formulations for value in row['formulations'])]
    keys = {row[field] for row in searches for field in FINDING_INDEX_FIELDS if field in row}
    return dict(searches=searches, fields={key: copy.deepcopy(value) for key, value in index['fields'].items()
                                        if key in keys}, read_works=copy.deepcopy(index['read_works']))


def _order_leaving_values(index, inventory):
    """List the values that can leave the index, longest canonical JSON first and then by digest, with references.

    A value can leave when it is no queries value and is longer than its reference {unit, characters}. unit is the
    shared body whose SHA-256 is the digest, or else the residual or whole record of the first search that holds it.
    Each row is (digest, reference, path): path is the sequence of keys of the value in the JSON of its body, empty
    for a shared body, which is the value itself.
    """
    manifest = inventory['search_delivery']
    bodies = {row['sha256']: row['handle'] for row in inventory['material']}
    shared = {row['artifact']['sha256'] for row in manifest['shared_fields']}
    entries = {row['search_digest']: row for row in manifest['searches']}
    queries = {row['queries'] for row in index['searches'] if 'queries' in row}
    leaving = []
    for key, value in index['fields'].items():
        if key in queries:
            continue
        if key in shared:
            handle, path = bodies[key], ()
        else:
            owner = next(row for row in index['searches'] if key in (row.get(field) for field in FINDING_INDEX_FIELDS))
            field = next(field for field in FINDING_INDEX_FIELDS if owner.get(field) == key)
            entry = entries[owner['search_digest']]
            handle = bodies[(entry['residual'] or entry['artifact'])['sha256']]
            path = ('fields', field) if entry['residual'] else (field,)
        reference = dict(unit=handle, characters=len(serialize_record(value).decode('utf-8')))
        if reference['characters'] > len(serialize_record(reference).decode('utf-8')):
            leaving.append((key, reference, path))
    return sorted(leaving, key=lambda row: (-row[1]['characters'], row[0]))


def build_last_finding_form(index, inventory):
    """Return the form of the index from which every value that can leave has left, named in units by its body."""
    form = dict(copy.deepcopy(index), units={})
    for key, reference, _ in _order_leaving_values(index, inventory):
        del form['fields'][key]
        form['units'][key] = reference
    return form


def locate_value_span(record, path):
    """Return the character span of the canonical JSON of the value at path, a sequence of keys, in that of record.

    The span is located by the keys of the value, never by a search for its text, which another field can repeat.
    """
    start, value = 0, record
    for key in path:
        before = {name: value[name] for name in value if name < key}
        head = serialize_record(before).decode('utf-8')[:-1] + (',' if before else '')
        start += len(head) + len(serialize_record(key).decode('utf-8')) + 1
        value = value[key]
    return start, start + len(serialize_record(value).decode('utf-8'))


def list_finding_units(index, inventory, bodies, form):
    """Return the rows {unit, start, end} of the values that the packet form of a complete index names (G8.11).

    A row gives the body of a value that left and the character span of its canonical JSON there. A descriptor form
    names every value that can leave, in the order of G8.4, and then the whole body of the unit finding_index. bodies
    maps the SHA-256 of each body of the inventory to its bytes. A complete form names no value: the result is None.
    """
    if 'units' not in form and 'fields' in form:
        return None
    handles = {row['handle']: row['sha256'] for row in inventory['material']}
    rows = []
    for key, reference, path in _order_leaving_values(index, inventory):
        if 'units' in form and key not in form['units']:
            continue
        start, end = locate_value_span(json.loads(bodies[handles[reference['unit']]]), path)
        rows.append(dict(unit=reference['unit'], start=start, end=end))
    if 'units' not in form:
        rows.append(dict(unit=form['unit'], start=0, end=form['characters']))
    return rows


def fit_finding_index(index, inventory, stated_characters, measure, *, formulations=None):
    """Return the packet form of a complete index, or of its selection for formulations, within a stated size.

    The complete form when it fits; otherwise the first form in which values have left, the longest first, that
    fits; otherwise the descriptor of the inventory unit whose body is the last form of the complete index. measure
    gives the size of a value in the measure of the route.
    """
    selected = index if formulations is None else select_finding_index(index, formulations)
    if measure(selected) <= stated_characters:
        return copy.deepcopy(selected)
    if measure(build_last_finding_form(selected, inventory)) > stated_characters:
        unit = next(row for row in inventory['units'] if row['identity'].get('field') == FINDING_INDEX_UNIT_FIELD)
        return dict(unit=unit['bodies'][0], characters=unit['characters'], searches=len(index['searches']),
                    read_works=len(index['read_works']))
    form = dict(copy.deepcopy(selected), units={})
    for key, reference, _ in _order_leaving_values(selected, inventory):
        del form['fields'][key]
        form['units'][key] = reference
        if measure(form) <= stated_characters:
            break
    return form


def verify_finding_index(records, artifacts, native, index, *, formulations=None):
    """Rebuild scientific bindings; work titles and aliases remain fixed at assignment."""
    validate_finding_index(index)
    expected = build_finding_index(records, artifacts, native, formulations=formulations)
    reading_keys = ('reading_id', 'reading_digest', 'version_id')
    if (index['searches'] != expected['searches'] or index['fields'] != expected['fields']
            or [{key: row.get(key) for key in reading_keys} for row in index['read_works']]
            != [{key: row[key] for key in reading_keys} for row in expected['read_works']]):
        raise ResearchError('openness_reading_incomplete', FINDING_INDEX_MESSAGE)


def plan_search_delivery(bindings, *, share_all_fields=False):
    """Factor field values shared by at least two distinct bound originals, in first-use order.

    The manifest keeps every original binding. Unaffected records retain their original body; an affected record has
    one residual envelope with disjoint inline fields and explicit shared-field references. Bodies are canonical UTF-8
    JSON bytes keyed by digest, ready to materialize after preflight succeeds. V7 opts into all fields; the default
    keeps the field order and bytes of the v5/v6 manifest and residuals.
    """
    searches, values = {}, {}
    for binding in bindings:
        sha256, search = binding['search_digest'], binding['search']
        if sha256 not in searches:
            searches[sha256] = dict(record_id=search['id'], search_digest=sha256,
                                    artifact=describe_artifact(serialize_record(search), MEDIA_TYPE),
                                    formulations=[], search=search)
            for field in sorted(search) if share_all_fields else SHARED_FIELDS:
                if field in search:
                    content = serialize_record(search[field])
                    values.setdefault((field, content), []).append(sha256)
        if binding['formulation'] not in searches[sha256]['formulations']:
            searches[sha256]['formulations'].append(binding['formulation'])
    bodies, shared, references = {}, [], {sha256: {} for sha256 in searches}
    for (field, content), owners in values.items():
        if len(owners) < 2:
            continue
        artifact = describe_artifact(content, MEDIA_TYPE)
        bodies[artifact['sha256']] = content
        shared.append({'field': field, 'artifact': artifact,
                       'owners': [{key: searches[sha256][key] for key in
                                   ('record_id', 'search_digest', 'formulations')} for sha256 in owners]})
        for sha256 in owners:
            references[sha256][field] = artifact['sha256']
    entries = []
    for sha256, row in searches.items():
        fields = references[sha256]
        residual = None
        if fields:
            content = serialize_record({'record_id': row['record_id'], 'search_digest': sha256,
                'fields': {key: value for key, value in row['search'].items() if key not in fields},
                'shared_fields': fields})
            residual = describe_artifact(content, MEDIA_TYPE)
            bodies[residual['sha256']] = content
        entries.append(dict({key: value for key, value in row.items() if key != 'search'},
                            residual=residual, shared_fields=fields))
    return {'manifest': {'schema_version': 1, 'searches': entries, 'shared_fields': shared}, 'bodies': bodies}


def _matches(content, artifact):
    return len(content) == artifact['size'] and hashlib.sha256(content).hexdigest() == artifact['sha256']


def rebuild_search_record(entry, residual, shared, original):
    """Rebuild and require the original canonical bytes, identity and digest from a residual and field bodies."""
    envelope = json.loads(residual)
    fields = envelope['fields']
    if (not _matches(residual, entry['residual']) or envelope['record_id'] != entry['record_id']
            or envelope['search_digest'] != entry['search_digest']
            or envelope['shared_fields'] != entry['shared_fields']
            or set(fields).intersection(entry['shared_fields']) or set(shared) != set(entry['shared_fields'])
            or any(hashlib.sha256(shared[field]).hexdigest() != sha256
                   for field, sha256 in entry['shared_fields'].items())):
        raise ResearchError('openness_reading_incomplete', 'The search residual and shared fields do not match the '
                            'frozen manifest', {'search_digest': entry['search_digest']})
    rebuilt = serialize_record(dict(fields, **{field: json.loads(content) for field, content in shared.items()}))
    if (rebuilt != original or not _matches(rebuilt, entry['artifact'])
            or hashlib.sha256(rebuilt).hexdigest() != entry['search_digest']
            or json.loads(rebuilt)['id'] != entry['record_id']):
        raise ResearchError('openness_reading_incomplete', 'The delivered search components do not rebuild the exact '
                            'bound original', {'search_digest': entry['search_digest']})
    return rebuilt


def verify_search_deliveries(manifest, delivered, originals):
    """Return original-search receipts only for complete delivered components and exact reconstruction.

    delivered maps a component digest to its complete bytes and the authenticated assignment/attempt/part receipts.
    Missing, altered or unreceipted bodies cannot complete a search. Chain authentication belongs to the caller.
    """
    shared = {row['artifact']['sha256']: row['artifact'] for row in manifest['shared_fields']}
    receipts = []
    for entry in manifest['searches']:
        required = ([entry['residual']] + [shared[entry['shared_fields'][field]]
                                          for field in sorted(entry['shared_fields'])]
                    if entry['residual'] is not None else [entry['artifact']])
        if any(reference['sha256'] not in delivered
               or not delivered[reference['sha256']]['deliveries']
               or not _matches(delivered[reference['sha256']]['content'], reference) for reference in required):
            continue
        if entry['residual'] is not None:
            rebuild_search_record(entry, delivered[entry['residual']['sha256']]['content'],
                                  {field: delivered[sha256]['content']
                                   for field, sha256 in entry['shared_fields'].items()},
                                  originals[entry['search_digest']])
        elif delivered[entry['search_digest']]['content'] != originals[entry['search_digest']]:
            raise ResearchError('openness_reading_incomplete', 'The delivered search differs from its bound original')
        receipts.append({'record_id': entry['record_id'], 'search_digest': entry['search_digest'],
                         'components': [{'sha256': reference['sha256'],
                                         'deliveries': delivered[reference['sha256']]['deliveries']}
                                        for reference in required]})
    return receipts
