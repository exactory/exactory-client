"""Portable, locally verified proof of a foreign problem review cut.

The proof is a validated SQLite snapshot plus the closure of its immutable
artifacts. Foreign records are never installed as native destination records.
Only the projections actually delivered to a producing review become citable
problem_carried_evidence records.
"""

from contextlib import contextmanager
import json
from pathlib import Path
import sqlite3
import tempfile

from .artifacts import ArtifactStore, is_sealed_artifact, _OBJECT_DIRECTORY, _SEALED_DIRECTORY, _verify_content
from .errors import ResearchError
from .evidence import digest
from .storage import Store

PROTOCOL = 'exactory-problem-objection-proof-v1'
IMMUTABLE_KINDS = ('value_review', 'review_assignment', 'review_adjudication',
                   'problem_objection_carry', 'problem_carried_evidence')


def source_identity(connection, records):
    from .problem_claims import require_problem
    marker = require_problem(records)
    initialization, immutable = None, {}
    for row in connection.execute('SELECT revision, digest, changes FROM events ORDER BY revision'):
        for change in json.loads(row['changes']):
            kind, key = change['kind'], change['key']
            if kind in IMMUTABLE_KINDS:
                identity = (kind, key)
                original = immutable.setdefault(identity, change['value'])
                if original != change['value'] or records.get(kind, {}).get(key) != original:
                    raise ResearchError('problem_objection_provenance_invalid', 'An immutable review or carry record changed', dict(record_kind=kind, id=key))
                if kind in ('value_review', 'problem_objection_carry') and original.get('recorded_revision') != row['revision']:
                    raise ResearchError('problem_objection_provenance_invalid', 'The immutable record has a false creation revision', dict(record_kind=kind, id=key))
            if change['kind'] == 'workspace' and change['key'] == 'problem':
                if change['value'] != marker:
                    raise ResearchError('problem_objection_provenance_invalid', 'The problem initialization marker changed')
                if initialization is None:
                    initialization = row['digest']
    if initialization is not None:
        return digest(dict(protocol=PROTOCOL, initialization=initialization, marker=marker))
    raise ResearchError('problem_objection_provenance_invalid', 'An authenticated problem initialization is required')


def all_references(value):
    """Include citation descriptors too: the proof retains more than delivery."""
    if isinstance(value, dict):
        def managed(path):
            return isinstance(path, str) and path.startswith((_OBJECT_DIRECTORY + '/', _SEALED_DIRECTORY + '/'))
        if {'sha256', 'path', 'size', 'media_type'} <= value.keys() and managed(value['path']):
            yield value
        elif {'sha256', 'location', 'size', 'media_type'} <= value.keys() and managed(value['location']):
            yield dict(path=value['location'], sha256=value['sha256'], size=value['size'],
                       media_type=value['media_type'], **value.get('extensions', {}))
        else:
            for item in value.values():
                yield from all_references(item)
    elif isinstance(value, list):
        for item in value:
            yield from all_references(item)


def _bind_origin_record(identities, source):
    identity = (source['origin'], source['kind'], source['id'])
    previous = identities.setdefault(identity, source)
    if previous['digest'] != source['digest']:
        raise ResearchError('problem_objection_origin_conflict', 'An immutable record has conflicting origin-qualified bytes',
                            dict(origin=source['origin'], record_kind=source['kind'], id=source['id']))


def origin_inventory(records, origin=None):
    """Keep empty and transitive identities even when a freshness link returns home."""
    identities = {}
    for carry in records.get('problem_objection_carry', {}).values():
        for source in carry['origin_records']:
            _bind_origin_record(identities, source)
    if origin is not None:
        for kind in IMMUTABLE_KINDS:
            for identifier, record in records.get(kind, {}).items():
                _bind_origin_record(identities, dict(origin=origin, kind=kind, id=identifier, digest=digest(record)))
    return [source for _, source in sorted(identities.items())]


def validate_origin_records(records, incoming=(), *, origin=None):
    """One producing origin and record ID cannot acquire different immutable bytes."""
    identities = {}
    for source in origin_inventory(records, origin):
        _bind_origin_record(identities, source)
    for wrapper in list(records.get('problem_carried_evidence', {}).values()) + list(incoming):
        source = wrapper['source_record']
        _bind_origin_record(identities, dict(source, origin=wrapper['origin']))


def validate_native_changes(carries, changes):
    """Reserve known same-origin IDs when later native work writes new records."""
    if not carries:
        return
    origin = next(iter(carries.values()))['destination_origin']
    identities = {}
    for source in origin_inventory(dict(problem_objection_carry=carries)):
        _bind_origin_record(identities, source)
    for kind, identifier, record in changes:
        if kind in IMMUTABLE_KINDS:
            _bind_origin_record(identities, dict(origin=origin, kind=kind, id=identifier, digest=digest(record)))


def _read_retained(artifacts, reference):
    from .evaluation import Evaluation
    while isinstance(artifacts, Evaluation):
        artifacts = artifacts.store
    if not is_sealed_artifact(reference):
        return artifacts.read(reference)
    artifacts.verify_sealed(reference)
    # Retaining sealed bytes in a proof does not unseal them for a reviewer.
    with artifacts._open_source(reference, sealed=True) as stream:
        return _verify_content(stream.read(), reference)


def capture_proof(connection, records, source_artifacts, destination_artifacts, revision):
    """Called under the source's one bounded read guard, never a second Store read."""
    pending = []
    for event in connection.execute('SELECT payload, changes, result FROM events ORDER BY revision'):
        for encoded in event:
            pending.extend(all_references(json.loads(encoded)))
    manifest, seen = [], set()
    while pending:
        reference = pending.pop()
        key = digest(reference)
        if key in seen:
            continue
        seen.add(key)
        try:
            data = _read_retained(source_artifacts, reference)
        except ResearchError as error:
            if error.code != 'artifact_missing':
                raise
            # Captured JSON can describe derived probe controls which were
            # never stored as objects. Descriptor shape is not a dependency
            # declaration. Portable authenticated replay below establishes
            # which objects are actually required before a carry is accepted.
            continue
        retained = (destination_artifacts.put_sealed(data, reference['media_type']) if is_sealed_artifact(reference)
                    else destination_artifacts.put(data, reference['media_type']))
        manifest.append(dict(original=reference, retained=retained))
        if not is_sealed_artifact(reference) and reference['media_type'] == 'application/json':
            try:
                pending.extend(all_references(json.loads(data)))
            except (ValueError, UnicodeDecodeError):
                pass
    with tempfile.TemporaryDirectory(prefix='problem-objection-proof-') as directory:
        path = Path(directory) / 'snapshot.sqlite3'
        target = sqlite3.connect(path)
        try:
            connection.backup(target)
        finally:
            target.close()
        database = destination_artifacts.put(path.read_bytes(), 'application/vnd.sqlite3')
    return dict(protocol=PROTOCOL, origin=source_identity(connection, records), revision=revision,
                database=database, artifacts=sorted(manifest, key=lambda item: digest(item['original'])))


@contextmanager
def open_proof(artifacts, proof):
    if proof.get('protocol') != PROTOCOL:
        raise ResearchError('problem_objection_provenance_invalid', 'Unknown carried proof protocol')
    with tempfile.TemporaryDirectory(prefix='problem-objection-verify-') as directory:
        root = Path(directory)
        (root / '.exactory').mkdir()
        (root / '.exactory/research.sqlite3').write_bytes(artifacts.read(proof['database']))
        local = ArtifactStore(root)
        for entry in proof['artifacts']:
            data = _read_retained(artifacts, entry['retained'])
            original = entry['original']
            retained = local.put_sealed(data, original['media_type']) if is_sealed_artifact(original) else local.put(data, original['media_type'])
            if any(retained[key] != original[key] for key in retained):
                raise ResearchError('problem_objection_provenance_invalid', 'A proof artifact manifest changed its descriptor')
        source = Store(root)
        with source._connection() as connection:
            from .storage import _read_snapshot
            snapshot = _read_snapshot(connection)
            if (snapshot['revision'] != proof['revision']
                    or source_identity(connection, snapshot['records']) != proof['origin']):
                raise ResearchError('problem_objection_provenance_invalid', 'The proof does not identify its authenticated source cut')
            yield snapshot['records'], local


def authenticate_reviews(records, artifacts, reviews):
    from . import research_decisions as decisions, review_protocol as protocol
    if not reviews:
        return
    # Native identity assignment depends on prior reviews and imports, including
    # reviews that do not themselves contribute an exported material version.
    # Authenticate those predecessors before using their claims or mappings.
    through = max(review['recorded_revision'] for review in reviews)
    earlier = {}
    for review in sorted(records.get('value_review', {}).values(), key=lambda row: (row['recorded_revision'], row['id'])):
        if review['recorded_revision'] > through:
            continue
        assignment = records.get('review_assignment', {}).get(review['assignment_id'])
        if assignment is None:
            raise ResearchError('problem_objection_provenance_invalid', 'The producing assignment is missing')
        expected = dict(assignment_id=review['payload']['assignment_id'], subject=assignment.get('subject'),
                        reviewer_id=assignment['reviewer_id'], packet_digest=assignment['packet_digest'], prompt_hash=assignment['prompt_hash'])
        if any(review.get(key) != value for key, value in expected.items()):
            raise ResearchError('problem_objection_provenance_invalid', 'The review metadata differs from its observed assignment')
        state = protocol.assignment_state(records, artifacts, assignment['id'])
        authenticate_assignment(records, artifacts, assignment, state)
        output, output_digest = decisions.read_effective_result(state)
        if (output != decisions._response(review['payload']) or output_digest != review['output_digest']
                or review['id'] != review['payload']['id'] or review['digest'] != digest(review['payload'])):
            raise ResearchError('problem_objection_provenance_invalid', 'Carry requires the exact observed review output',
                                dict(review_id=review['id']))
        if review.get('effective_binding') is not None:
            if review['effective_binding'] != decisions.bind_effective_result(state.get('effective', {})):
                raise ResearchError('problem_objection_provenance_invalid', 'The effective review binding changed')
        protocol.validate_assignment_inventories(records, artifacts, assignment['id'])
        decisions.validate_review_contract(records, artifacts, assignment, output)
        prior = dict(records, value_review=dict(sorted(earlier.items())),
                     problem_objection_carry={key: carry for key, carry in records.get('problem_objection_carry', {}).items()
                                             if carry['recorded_revision'] < review['recorded_revision']})
        try:
            identities = decisions._assign_objection_identities(prior, artifacts, review['payload'], True)
        except ResearchError as error:
            raise ResearchError('problem_objection_provenance_invalid', 'The observed review cannot produce its canonical identities',
                                dict(review_id=review['id'], cause=error.code)) from error
        if review.get('objection_identities', {}) != identities:
            raise ResearchError('problem_objection_provenance_invalid', 'The canonical identities differ from the observed output and native identity rules',
                                dict(review_id=review['id']))
        earlier[review['id']] = review


def authenticate_assignment(records, artifacts, assignment, state):
    from . import review_protocol as protocol
    if assignment.get('staged_review') is not None:
        from .direction_reviews import collect_selected_task_graph
        graph = collect_selected_task_graph(records, artifacts, assignment['id'])
        valid = graph['completion']['complete'] and state.get('effective') is not None
    else:
        attempt = records.get('review_attempt', {}).get(state.get('attempt_id'))
        valid = attempt is not None and protocol.authenticate_attempt(records, artifacts, assignment, attempt)
    if not valid:
        raise ResearchError('problem_objection_provenance_invalid', 'Carry requires an authenticated observed assignment', dict(assignment_id=assignment['id']))


def observed_source_projection(records, artifacts, assignment_id, reference):
    """Retain actual inline/visual/staged delivery without widening its source bounds."""
    from . import review_protocol as protocol, review_supplied_evidence as supplied
    from .evaluation import Evaluation
    from .research_decisions import read_review_citation_delivery
    if reference.get('kind') == 'record':
        return None
    evaluation = Evaluation.of(records, artifacts)
    delivery = read_review_citation_delivery(records, evaluation, records['review_assignment'][assignment_id])
    packets = delivery['packets']
    if not supplied.is_received(records, reference, delivery['artifacts'], delivery['source_mappings'],
                                artifacts=evaluation, frozen=True, packets=packets):
        return None
    link = reference.get('link') if reference.get('kind') == 'source' else None
    artifact = link['artifact'] if link is not None else reference
    inline, visual, staged, descriptors = [], [], [], {}
    if link is not None:
        for packet in packets:
            for field in ('problem_evidence', 'direction_evidence'):
                evidence = packet.get(field, {})
                for passage in evidence.get('passages', []):
                    trial = {field: dict(evidence, passages=[passage])}
                    if supplied._inline_passage_received(records, evaluation, [trial], link):
                        row = dict(passage=passage, reading_binding=next(
                            {key: reading[key] for key in ('id', 'digest')}
                            for reading in evidence['readings'] if reading['id'] == passage['reading_id']))
                        if row not in inline:
                            inline.append(row)
            if link['locator'].get('kind') == 'pdf':
                from .review_visual_sources import MARKER, has_pdf_page, validate_manifest
                from .direction_sources import has_attachment
                if not has_pdf_page(evaluation, packet, link):
                    continue
                manifest = validate_manifest(evaluation, packet[MARKER])
                for index, page in enumerate(manifest['pages']):
                    source = manifest['sources'][page['source_index']]
                    if (source['original'] != artifact or page['page_index'] != link['locator']['page_index']
                            or any(source[key] != link[key] for key in ('source_id', 'version_id'))
                            or not has_attachment(evaluation, packet, page['image'])):
                        continue
                    row = dict(source=source, page=page, render_profile=manifest['render_profile'],
                        manifest_digest=digest(manifest), reading_bindings=[dict(reading,
                            units=[unit for unit in reading['units'] if unit['page_index'] == index])
                            for reading in manifest['readings'] if any(unit['page_index'] == index for unit in reading['units'])])
                    if row not in visual:
                        visual.append(row)
                    descriptors[digest(page['image'])] = page['image']
    if supplied._is_staged_original_received(evaluation, artifact, reference, packets):
        from .review_staged import make_source, materialize_quotation
        source_id = 'artifact:' + digest(artifact)
        content = evaluation.read(artifact).decode('utf-8')
        source = make_source(source_id, 'artifact', content, identity={'artifact': artifact}, descriptor=artifact)
        locator = link['locator'] if link is not None else dict(start=0, end=len(content))
        for packet in packets:
            for original in packet.get('staged_review', {}).get('originals', []):
                span = original.get('span', {})
                if (span.get('source_id') == source_id and span['start'] < locator['end'] and locator['start'] < span['end']
                        and materialize_quotation(source, span)['text'] == original.get('text') and original not in staged):
                    staged.append(original)
    if not (inline or visual or staged):
        return None
    return dict(kind='observed_source_projection', reference=protocol._reference_templates(reference),
                inline_passages=protocol._reference_templates(inline), visual_pages=protocol._reference_templates(visual),
                staged_originals=protocol._reference_templates(staged), artifacts=list(descriptors.values()))


def adjudication_context(records, artifacts, saved, origin, objection_origin, origin_id):
    """A source disposition is usable context, never a destination clearance."""
    from . import review_protocol as protocol, review_supplied_evidence as supplied
    from .research_decisions import read_effective_result, bind_effective_result
    assignment = records['review_assignment'][saved['assignment_id']]
    state = protocol.assignment_state(records, artifacts, assignment['id'])
    authenticate_assignment(records, artifacts, assignment, state)
    response = {key: saved[key] for key in ('objection_id', 'disposition', 'reason', 'evidence', 'correction_admissible')}
    if 'reassessment' in saved:
        response['reassessment'] = saved['reassessment']
    output, output_digest = read_effective_result(state)
    if (output != response or (saved.get('effective_binding') is not None
            and saved['effective_binding'] != bind_effective_result(state.get('effective', {})))):
        raise ResearchError('problem_objection_provenance_invalid', 'A source disposition differs from its observed output')
    packet = protocol._read_json(artifacts, assignment['packet'])
    protocol.validate_adjudication_contract(records, artifacts, assignment, packet, output)
    references, mappings = supplied.supplied_context(records, artifacts, [assignment['id']])
    carried = protocol.collect_carried_records(records, artifacts, assignment['id'])
    entries, descriptors, cited = [], {}, set()
    for reference in protocol.collect_adjudication_evidence(output):
        if reference.get('kind') == 'record':
            found = protocol.find_carried_record(records, carried, reference)
            if found is None:
                raise ResearchError('problem_objection_provenance_invalid', 'A source disposition cites an undelivered record')
            projection, delivered = found
            hashes = {row['sha256'] for row in list(protocol.collect_references(projection)) + delivered}
            content = [row for row in references.values() if row['sha256'] in hashes]
            entries.append(dict(reference, record=protocol._reference_templates(projection), artifacts=content))
        else:
            original = reference['link']['artifact'] if reference.get('kind') == 'source' else reference
            cited.add(original['sha256'])
            content = [row for row in references.values() if row['sha256'] == original['sha256']]
            projected = observed_source_projection(records, artifacts, assignment['id'], reference) if not content else None
            if projected is not None:
                entries.append(projected)
                content = projected['artifacts']
            else:
                if not content and not supplied.is_received(records, reference, references, mappings, artifacts=artifacts):
                    raise ResearchError('problem_objection_provenance_invalid', 'A source disposition cites undelivered bytes')
                entries.extend(content)
        descriptors.update((digest(row), row) for row in content)
    selected_mappings = [row for row in mappings if row['origin']['artifact_descriptor']['sha256'] in cited]
    descriptors.update((digest(row['excerpt']), row['excerpt']) for row in selected_mappings)
    for reference in descriptors.values():
        artifacts.read(reference)
    return dict(id='carried-context-' + digest([origin, saved['id'], digest(saved)]), origin=origin,
                source_record=dict(kind='review_adjudication', id=saved['id'], digest=digest(saved)),
                context_for=dict(origin=objection_origin, origin_id=origin_id), purpose='source_adjudication',
                adjudication=protocol._reference_templates(saved),
                review_binding=dict(assignment_id=assignment['id'], output_digest=output_digest,
                    packet_digest=assignment['packet_digest'], prompt_hash=assignment['prompt_hash']),
                projection=protocol._reference_templates(dict(version_evidence=entries, version_source_mappings=selected_mappings)),
                artifacts=[row for _, row in sorted(descriptors.items())])


def evidence_record(records, artifacts, version, origin):
    """Wrap exact delivered projections, retaining foreign citations descriptively."""
    from .review_protocol import describe_version_evidence, _reference_templates, collect_references
    delivery = describe_version_evidence(records, artifacts, version)
    review = records['value_review'][version['review_id']]
    for index, entry in enumerate(delivery['version_evidence']):
        if entry.get('kind') != 'undelivered':
            continue
        reference = next(row for row in version['evidence'] if _reference_templates(row) == entry['reference'])
        projected = observed_source_projection(records, artifacts, review['assignment_id'], reference)
        if projected is None:
            raise ResearchError('problem_objection_provenance_invalid', 'An objection cites evidence its reviewer never received')
        delivery['version_evidence'][index] = projected
    descriptors = list(collect_references(delivery['version_evidence']))
    # Passages supply only their excerpts. The original descriptors remain templates.
    descriptors.extend(mapping['excerpt'] for mapping in delivery['version_source_mappings'])
    descriptors = list({digest(item): item for item in descriptors}.values())
    for reference in descriptors:
        artifacts.read(reference)
    identifier = 'carried-' + digest([origin, version['id'], version['review_id'], version['material_version']])
    projection = {key: value for key, value in delivery.items() if key != 'version_review'}
    binding = {key: review[key] for key in ('assignment_id', 'packet_digest', 'prompt_hash', 'output_digest', 'digest')}
    return dict(id=identifier, origin=origin, review_id=version['review_id'], review_binding=binding,
                source_record=dict(kind='value_review', id=review['id'], digest=digest(review)),
                original_material_version=version['material_version'],
                objection=_reference_templates(version), projection=_reference_templates(projection),
                artifacts=sorted(descriptors, key=digest))


def verify_carries(records, artifacts, *, seen=None, expected_origin=None):
    """Offline authentication never opens a recorded source workspace path."""
    from .problem_objection_carry import export_material, relevant_cut, linked_sources
    seen = {} if seen is None else seen
    for carry in records.get('problem_objection_carry', {}).values():
        if expected_origin is not None and carry['destination_origin'] != expected_origin:
            raise ResearchError('problem_objection_provenance_invalid', 'A carry names a different destination initialization')
        key = digest(carry['proof'])
        if key not in seen:
            with open_proof(artifacts, carry['proof']) as (foreign, local):
                verify_carries(foreign, local, seen=seen, expected_origin=carry['proof']['origin'])
                members, wrappers = export_material(foreign, local, carry['source_origin'], authenticate=True)
                seen[key] = (members, wrappers, relevant_cut(foreign, members, local), linked_sources(foreign),
                             origin_inventory(foreign, carry['source_origin']))
        members, wrappers, cut, foreign_links, inventory = seen[key]
        if (members != carry['members'] or cut != carry['source_cut']
                or inventory != carry['origin_records']
                or carry['proof']['origin'] != carry['source_origin']
                or carry['proof']['revision'] != carry['source_revision']):
            raise ResearchError('problem_objection_provenance_invalid', 'The carry manifest differs from its authenticated proof')
        links = dict(foreign_links)
        links[carry['source_origin']] = dict(origin=carry['source_origin'], observer_origin=carry['destination_origin'], workspace=carry['source_workspace'], cut=carry['source_cut'])
        expected = [link for origin, link in sorted(links.items()) if origin != carry['destination_origin']]
        if expected != carry['sources']:
            raise ResearchError('problem_objection_provenance_invalid', 'The linked-source cut differs from the retained proof')
        material_ids = {member['evidence_id'] for member in members}
        if carry['context_evidence_ids'] != sorted(set(wrappers) - material_ids):
            raise ResearchError('problem_objection_provenance_invalid', 'The source resolution context differs from its proof')
        for identifier, wrapper in wrappers.items():
            if records.get('problem_carried_evidence', {}).get(identifier) != wrapper:
                raise ResearchError('problem_objection_provenance_invalid', 'Carried evidence differs from its authenticated projection',
                                    dict(evidence_id=identifier))
    validate_origin_records(records, origin=expected_origin)
    return len(records.get('problem_objection_carry', {}))
