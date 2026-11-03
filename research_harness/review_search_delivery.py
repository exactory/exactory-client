"""Exact search-field delivery plans and reconstruction for problem openness v5.

Planning does not publish artifacts. Delivery verification consumes only bodies and receipts that the caller has
derived from authenticated requests of one chain. A manifest or an artifact on disk supplies no delivery credit.
"""

import hashlib
import json

from .artifacts import describe_artifact
from .errors import ResearchError
from .problem_openness import serialize_record

SHARED_FIELDS = ('dispositions', 'queries', 'responses', 'scope')
MEDIA_TYPE = 'application/json'


def plan_search_delivery(bindings):
    """Factor field values shared by at least two distinct bound originals, in first-use order.

    The manifest keeps every original binding. Unaffected records retain their original body; an affected record has
    one residual envelope with disjoint inline fields and explicit shared-field references. Bodies are canonical UTF-8
    JSON bytes keyed by digest, ready to materialize after preflight succeeds.
    """
    searches, values = {}, {}
    for binding in bindings:
        sha256, search = binding['search_digest'], binding['search']
        if sha256 not in searches:
            searches[sha256] = dict(record_id=search['id'], search_digest=sha256,
                                    artifact=describe_artifact(serialize_record(search), MEDIA_TYPE),
                                    formulations=[], search=search)
            for field in SHARED_FIELDS:
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
