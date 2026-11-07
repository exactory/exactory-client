"""Complete, origin-qualified objection carry and prospective linked-source cuts."""

from contextlib import ExitStack, contextmanager, nullcontext
from pathlib import Path

from .errors import ResearchError
from .evidence import digest
from .operations import fields, immutable_record, prepared_mutation, text
from .storage import Store, _read_snapshot
from . import problem_objection_provenance as proof

PROTOCOL = 'exactory-problem-objection-carry-v1'
PROMPT_PARAMETER = 'problem_objection_carry'
PROMPT = (
    '\n\nCarried problem objections (exactory-problem-objection-carry-v1). '
    'Some material_objections entries come from the bar and slate reviews of another problem record. '
    'Such an entry keeps origin and origin_id, the record that stated the objection and its id there, '
    'and original_objection, the objection as that record stated it. '
    'Its material_members entries are its retained versions. '
    'Keep each delivered id. Do not state an inherited objection again in objections. '
    'Follow the response schema of this turn: an intermediate, source-request or staged task response gives '
    'only the fields that its task owns. '
    'A completed slate or result review gives one value.objection_findings entry {id,status,reason,evidence} '
    'per imported identity. '
    'That finding covers every material_members entry, and it says whether the objection concerns the '
    'supplied candidates or the current formulation. '
    'Cite the problem_carried_evidence references that this packet supplies, with their exact projections, '
    'artifacts and bounded excerpts. '
    'A foreign record reference inside a projection is descriptive, not a local citation. '
    'Give resolved only with evidence that settles the objection or shows that it does not concern this record. '
    'Otherwise give continuing or contested, with your reason and evidence. '
    'A resolution or an adjudication of the source record is context, not a clearance here.'
)
MAX_SOURCES = 32


def local_origin(records):
    carries = records.get('problem_objection_carry', {})
    return next(iter(carries.values()))['destination_origin'] if carries else None


def finding_id(origin, identifier, destination_origin):
    return identifier if origin == destination_origin else identifier + '@origin:' + origin


def member_key(origin, identifier, producer, review_id, material_version):
    return digest([origin, identifier, producer, review_id, material_version])


def retained_members(records):
    members = {}
    for carry in records.get('problem_objection_carry', {}).values():
        for member in carry['members']:
            key = member['key']
            if key in members and members[key] != member:
                raise ResearchError('problem_objection_origin_conflict', 'Immutable origin/review/version bytes conflict', dict(member=key))
            members[key] = member
    return sorted(members.values(), key=lambda member: member['key'])


def import_set_digest(records):
    if not records.get('problem_objection_carry'):
        return None
    return digest(retained_members(records))


def has_imported_objections(records):
    return bool(retained_members(records))


def seed_versions(records):
    destination = local_origin(records)
    return [dict(member['version'], id=finding_id(member['origin'], member['origin_id'], destination),
                 origin=member['origin'], origin_id=member['origin_id'], carried_member=member,
                 material_version=member['key']) for member in retained_members(records)]


def _relevant_reviews(records):
    return [review for review in records.get('value_review', {}).values()
            if review.get('subject', {}).get('kind') == 'problem' and review['payload']['stage'] in ('bar', 'slate')]


def pending_reviews(records, artifacts):
    """Relevant work includes an observed final which has not yet been recorded."""
    from .research_decisions import select_problem_slot_assignments, list_stated_objections
    from .review_protocol import build_problem_subject, _read_json
    from . import review_parts
    from .review_supplied_evidence import list_delivered_objections
    recorded = {review['assignment_id'] for review in records.get('value_review', {}).values()}
    heads = [row for stage in ('bar', 'slate') for row in select_problem_slot_assignments(
        records, stage, build_problem_subject(records, stage)).values()]
    identifiers = {row['id'] for row in seed_versions(records)}
    identifiers.update(row['id'] for review in _relevant_reviews(records) for row in list_stated_objections(review))
    subjects = {digest(row['subject']): row['subject'] for row in records.get('review_assignment', {}).values()
                if row['role'] == 'result' and row.get('subject', {}).get('kind') == 'problem'}
    for subject in subjects.values():
        for row in select_problem_slot_assignments(records, 'result', subject).values():
            if row['id'] in recorded or not identifiers:
                continue
            # Pending work is bound by its frozen scientific packet, including
            # part/controller inventories. Do not reconstruct sent requests or
            # selected task graphs just to discover their objection identities.
            packet = _read_json(artifacts, row['packet'])
            if review_parts.KEY in packet:
                packet = review_parts.read_logical_packet(artifacts, packet[review_parts.KEY])
            if identifiers.intersection(item['id'] for item in list_delivered_objections(packet)):
                heads.append(row)
    return sorted(row['id'] for row in heads if row['id'] not in recorded)


def _material_versions(records):
    """Fold material identities without reading or reconstructing delivered evidence."""
    from . import research_decisions as decisions
    reviews = _relevant_reviews(records)
    seeds = seed_versions(records)
    identifiers = {row['id'] for row in seeds}
    identifiers.update(row['id'] for review in reviews for row in decisions.list_stated_objections(review))
    results = [review for review in records.get('value_review', {}).values()
               if review.get('subject', {}).get('kind') == 'problem' and review['payload']['stage'] == 'result'
               and any(row['id'] in identifiers for row in review['payload']['value']['objection_findings']
                       + decisions.list_stated_objections(review))]
    folded = []
    for review in reviews + results:
        if review in results:
            payload = dict(review['payload'], objections=[row for row in review['payload']['objections']
                if review.get('objection_identities', {}).get(row['id'], row['id']) in identifiers],
                value=dict(review['payload']['value'], objection_findings=[row for row in review['payload']['value']['objection_findings']
                    if row['id'] in identifiers]))
            review = dict(review, payload=payload)
        folded.append(review)
    versions, _ = decisions._fold_objection_versions(folded, set(), seeds=seeds)
    return reviews, results, identifiers, seeds, versions


def material_inventory(records, origin):
    """Only the keys and producing origins used by the freshness comparison."""
    _, _, _, seeds, versions = _material_versions(records)
    members = {row['key']: dict(key=row['key'], producer_origin=row['producer_origin'])
               for row in retained_members(records)}
    inherited = {row['id']: (row['origin'], row['origin_id']) for row in seeds}
    for identifier, rows in versions.items():
        objection_origin, origin_id = inherited.get(identifier, (origin, identifier))
        for version in rows:
            if 'carried_member' not in version:
                key = member_key(objection_origin, origin_id, origin, version['review_id'], version['material_version'])
                members[key] = dict(key=key, producer_origin=origin)
    return sorted(members.values(), key=lambda row: row['key'])


def export_material(records, artifacts, origin, *, authenticate=False):
    """Every bar/slate identity and all retained refinements, without author filters."""
    from . import research_decisions as decisions
    reviews, results, identifiers, seeds, versions = _material_versions(records)
    if authenticate:
        proof.authenticate_reviews(records, artifacts, reviews + results)
    members = {row['key']: row for row in retained_members(records)}
    wrappers = {row['evidence_id']: records['problem_carried_evidence'][row['evidence_id']] for row in members.values()}
    for carry in records.get('problem_objection_carry', {}).values():
        wrappers.update((identifier, records['problem_carried_evidence'][identifier]) for identifier in carry['context_evidence_ids'])
    inherited = {row['id']: (row['origin'], row['origin_id']) for row in seeds}
    for identifier, rows in versions.items():
        objection_origin, origin_id = inherited.get(identifier, (origin, identifier))
        for version in rows:
            if 'carried_member' in version:
                continue
            wrapper = proof.evidence_record(records, artifacts, version, origin)
            wrappers[wrapper['id']] = wrapper
            reference = dict(kind='record', record_kind='problem_carried_evidence', id=wrapper['id'], digest=digest(wrapper))
            key = member_key(objection_origin, origin_id, origin, version['review_id'], version['material_version'])
            member = dict(key=key, origin=objection_origin, origin_id=origin_id, producer_origin=origin,
                          review_id=version['review_id'], source_material_version=version['material_version'],
                          original_objection=version, evidence_id=wrapper['id'],
                          version=dict(version, evidence=[reference]))
            if key in members and members[key] != member:
                raise ResearchError('problem_objection_origin_conflict', 'Immutable origin/review/version bytes conflict', dict(member=key))
            members[key] = member
    # A source resolution travels as authenticated context, never as clearance
    # or an additional material version. Its own evidence wrapper is immutable.
    for review in results:
        for finding in review['payload']['value']['objection_findings']:
            if finding['id'] not in identifiers or finding['status'] != 'resolved':
                continue
            objection_origin, origin_id = inherited.get(finding['id'], (origin, finding['id']))
            version = decisions.state_objection(review, finding)
            wrapper = proof.evidence_record(records, artifacts, version, origin)
            wrapper.update(context_for=dict(origin=objection_origin, origin_id=origin_id), purpose='source_resolution')
            wrappers[wrapper['id']] = wrapper
    for saved in records.get('review_adjudication', {}).values():
        if saved.get('subject', {}).get('kind') == 'problem' and saved['objection_id'] in identifiers:
            objection_origin, origin_id = inherited.get(saved['objection_id'], (origin, saved['objection_id']))
            wrapper = proof.adjudication_context(records, artifacts, saved, origin, objection_origin, origin_id)
            wrappers[wrapper['id']] = wrapper
    return sorted(members.values(), key=lambda member: member['key']), dict(sorted(wrappers.items()))


def relevant_cut(records, members, artifacts):
    return dict(reviews=sorted([dict(id=row['id'], digest=digest(row)) for row in _relevant_reviews(records)], key=lambda row: row['id']),
                pending=pending_reviews(records, artifacts), members=sorted(row['key'] for row in members),
                dependencies=[dict(origin=origin, workspace=link['workspace']) for origin, link in sorted(linked_sources(records).items())])


def _compare_cut(actual, saved, members, destination, observer=None):
    local = {destination, observer}
    ignored = {row['key'] for row in members if row['producer_origin'] in local}
    return (actual['reviews'] == saved['reviews'] and not actual['pending']
            and [row for row in actual['dependencies'] if row['origin'] not in local]
                == [row for row in saved['dependencies'] if row['origin'] not in local]
            and set(actual['members']) - ignored == set(saved['members']) - ignored)


def linked_sources(records):
    """Latest cuts per explicit or transitive source, without timestamp churn."""
    linked = {}
    for carry in sorted(records.get('problem_objection_carry', {}).values(), key=lambda row: (row['recorded_revision'], row['id'])):
        for link in carry['sources']:
            linked[link['origin']] = link
    return linked


def _offline(artifacts):
    from .evaluation import Evaluation
    while isinstance(artifacts, Evaluation):
        artifacts = artifacts.store
    return getattr(artifacts, 'problem_objection_offline', False)


@contextmanager
def source_guards(records, artifacts, *, extra=()):
    """Hold only foreign guards; the destination's normal mutation performs CAS."""
    destination = local_origin(records)
    links = dict(linked_sources(records))
    for link in extra:
        links[link.get('origin', link['workspace'])] = link
    with ExitStack() as stack:
        cuts = {}
        pending = list(extra) + list(links.values())
        paths = set()
        while pending:
            link = pending.pop(0)
            if link.get('origin') in cuts:
                continue
            path = Path(link['workspace']).resolve()
            if path == Path(artifacts.root).resolve() or (destination is not None and link.get('origin') == destination):
                continue
            if path in paths:
                continue
            paths.add(path)
            if len(paths) > MAX_SOURCES:
                raise ResearchError('problem_objection_sources_exceeded', 'Carry links exceed the bounded foreign-store guard set', dict(limit=MAX_SOURCES))
            try:
                source = Store(path)
                connection = stack.enter_context(source._connection())
                snapshot = _read_snapshot(connection)
                origin = proof.source_identity(connection, snapshot['records'])
            except ResearchError as error:
                if error.code in ('store_busy', 'problem_objection_provenance_invalid'):
                    raise
                raise ResearchError('problem_objection_source_unavailable', 'Open the linked problem source before proceeding',
                                    dict(source_workspace=str(path), cause=error.code)) from error
            if origin in cuts or origin == destination:
                continue
            cuts[origin] = dict(store=source, connection=connection, snapshot=snapshot, origin=origin)
            pending.extend(linked_sources(snapshot['records']).values())
        yield cuts


def require_fresh_sources(records, artifacts, *, guards=None):
    if not records.get('problem_objection_carry') or _offline(artifacts):
        return
    destination = local_origin(records)
    expected = linked_sources(records)
    context = source_guards(records, artifacts)
    manager = nullcontext(guards.enter_context(context)) if guards is not None else context
    with manager as sources:
        for origin, link in expected.items():
            if origin == destination:
                continue
            cut = sources.get(origin)
            if cut is None:
                raise ResearchError('problem_objection_source_unavailable', 'The linked source has a different record identity', dict(source_workspace=link['workspace']))
            foreign = cut['snapshot']['records']
            members = material_inventory(foreign, origin)
            if not _compare_cut(relevant_cut(foreign, members, cut['store'].build_artifact_store()), link['cut'], members, destination, link['observer_origin']):
                raise ResearchError('problem_objections_refresh_required', 'Finish and record source reviews, carry its complete current set, then reassess the destination',
                                    dict(source_workspace=link['workspace'], source_revision=cut['snapshot']['revision']))


def carry_objections(store, payload, *, expected_revision, request_id):
    artifacts = store.build_artifact_store()
    with ExitStack() as guards:
        def prepare(records, value):
            from .problem_claims import require_problem
            require_problem(records)
            fields(value, ('id', 'source_workspace', 'source_revision'))
            text(value['id'], 'Carry ID')
            text(value['source_workspace'], 'Explicit absolute source workspace')
            path = Path(value['source_workspace'])
            if not path.is_absolute() or type(value['source_revision']) is not int or value['source_revision'] < 0:
                raise ResearchError('invalid_input', 'Name an absolute source workspace and nonnegative integer revision')
            path = path.resolve()
            if path == store.root.resolve():
                raise ResearchError('problem_objection_self_import', 'A problem cannot carry from itself or an alias')
            # This short destination read is released before acquiring foreign guards.
            with Store(store.root)._connection() as connection:
                revision = connection.execute('SELECT revision FROM metadata WHERE id = 1').fetchone()[0]
                if revision != expected_revision:
                    raise ResearchError('stale_revision', 'Read the current destination revision before carrying',
                                        dict(expected_revision=expected_revision, revision=revision))
                destination = proof.source_identity(connection, records)
            sources = guards.enter_context(source_guards(records, artifacts, extra=[dict(workspace=str(path))]))
            source = next((cut for cut in sources.values() if cut['store'].root == path), None)
            if source is None or source['origin'] == destination:
                raise ResearchError('problem_objection_self_import', 'The source is an alias of the destination record')
            snapshot = source['snapshot']
            if snapshot['revision'] != value['source_revision']:
                raise ResearchError('problem_objection_source_revision_mismatch', 'Read the current source revision and retry the complete carry',
                                    dict(source_revision=snapshot['revision'], expected_source_revision=value['source_revision']))
            foreign = snapshot['records']
            proof.verify_carries(foreign, source['store'].build_artifact_store(), expected_origin=source['origin'])
            members, wrappers = export_material(foreign, source['store'].build_artifact_store(), source['origin'], authenticate=True)
            cut = relevant_cut(foreign, members, source['store'].build_artifact_store())
            if cut['pending']:
                raise ResearchError('problem_objections_refresh_required', 'Finish and record every relevant source review before carrying', dict(assignment_ids=cut['pending']))
            links = {}
            for origin, guarded in sources.items():
                current = guarded['snapshot']['records']
                material = material_inventory(current, origin)
                links[origin] = dict(origin=origin, workspace=str(guarded['store'].root), cut=relevant_cut(current, material, guarded['store'].build_artifact_store()))
            for origin, link in linked_sources(foreign).items():
                if origin == destination:
                    continue
                actual = links[origin]['cut']
                guarded = sources[origin]
                material = material_inventory(guarded['snapshot']['records'], origin)
                if not _compare_cut(actual, link['cut'], material, destination, link['observer_origin']):
                    raise ResearchError('problem_objections_refresh_required', 'Refresh the source record from its linked source first', dict(source_workspace=link['workspace']))
            bundle = proof.capture_proof(source['connection'], foreign, source['store'].build_artifact_store(), artifacts, snapshot['revision'])
            with proof.open_proof(artifacts, bundle) as (portable, local):
                proof.verify_carries(portable, local, expected_origin=source['origin'])
                portable_members, portable_wrappers = export_material(portable, local, source['origin'], authenticate=True)
                if portable_members != members or portable_wrappers != wrappers:
                    raise ResearchError('problem_objection_provenance_invalid', 'The portable proof does not reproduce the complete authenticated source material')
            retained_links = linked_sources(foreign)
            retained_links[source['origin']] = dict(origin=source['origin'], observer_origin=destination, workspace=str(path), cut=cut)
            record = dict(value, source_workspace=str(path), protocol=PROTOCOL, source_origin=source['origin'],
                          destination_origin=destination, source_cut=cut, members=members, proof=bundle,
                          origin_records=proof.origin_inventory(foreign, source['origin']),
                          context_evidence_ids=sorted(set(wrappers) - {member['evidence_id'] for member in members}),
                          sources=[link for origin, link in sorted(retained_links.items()) if origin != destination],
                          recorded_revision=expected_revision + 1)
            existing = records.get('problem_objection_carry', {}).get(value['id'])
            if existing is not None:
                if any(existing[key] != record[key] for key in record if key != 'recorded_revision'):
                    raise ResearchError('record_conflict', 'A carry ID cannot replace its immutable cut')
                return [], existing
            combined = dict(records.get('problem_objection_carry', {}), **{value['id']: record})
            retained_members(dict(problem_objection_carry=combined))
            proof.validate_origin_records(dict(records, problem_objection_carry=combined), wrappers.values(), origin=destination)
            from .research_decisions import list_stated_objections
            claims = {row['id']: row['claim'] for review in records.get('value_review', {}).values()
                      for row in list_stated_objections(review)}
            for member in members:
                identifier = finding_id(member['origin'], member['origin_id'], destination)
                if identifier in claims and member['version'].get('claim', claims[identifier]) != claims[identifier]:
                    raise ResearchError('research_objection_identity_conflict', 'An imported identity collides with a different native claim', dict(objection_id=identifier))
            changes = [immutable_record(records, 'problem_carried_evidence', key, wrapper) for key, wrapper in wrappers.items()]
            return changes + [immutable_record(records, 'problem_objection_carry', value['id'], record)], record
        return prepared_mutation(store, 'problem.objections.carry', payload, prepare,
                                 expected_revision=expected_revision, request_id=request_id)


def merge_versions(records, artifacts, versions):
    """Imported identities bind every statement and hash every retained member."""
    imported = {}
    for seed in seed_versions(records):
        imported.setdefault(seed['id'], []).append(seed)
    for identifier, rows in imported.items():
        native = versions.get(identifier, [])
        all_rows = {row['material_version']: row for row in rows + native}
        members = {}
        for row in all_rows.values():
            member = row.get('carried_member')
            member = member if member is not None else dict(key=member_key(rows[0]['origin'], rows[0]['origin_id'], local_origin(records), row['review_id'], row['material_version']),
                producer_origin=local_origin(records), review_id=row['review_id'], original_objection=row, version=row)
            members.setdefault(member['key'], member)
        members = [member for _, member in sorted(members.items())]
        chosen = {key: value for key, value in rows[0].items() if key != 'carried_member'}
        material = [dict(row['version'], origin=row.get('producer_origin'), original_objection=row['original_objection']) for row in members]
        versions[identifier] = [dict(chosen, material_members=members, history=material,
            original_objection=members[0]['original_objection'],
            evidence=[reference for row in material for reference in row['evidence']],
            material_version=digest([row['key'] for row in members]))]
    return set(imported)


def describe_imported_evidence(records, artifacts, version):
    from .review_protocol import describe_version_evidence
    entries, mappings = {}, []
    for member in version['material_members']:
        native = []
        for reference in member['version']['evidence']:
            if reference.get('record_kind') == 'problem_carried_evidence':
                wrapper = records['problem_carried_evidence'][reference['id']]
                entries[reference['id']] = dict(reference, record=wrapper, artifacts=wrapper['artifacts'])
            else:
                native.append(reference)
        if native:
            delivery = describe_version_evidence(records, artifacts, dict(member['version'], evidence=native))
            entries.update((digest(entry), entry) for entry in delivery['version_evidence'])
            mappings.extend(row for row in delivery['version_source_mappings'] if row not in mappings)
    for wrapper in records.get('problem_carried_evidence', {}).values():
        if wrapper.get('context_for') == dict(origin=version['origin'], origin_id=version['origin_id']):
            reference = dict(kind='record', record_kind='problem_carried_evidence', id=wrapper['id'], digest=digest(wrapper))
            entries[wrapper['id']] = dict(reference, record=wrapper, artifacts=wrapper['artifacts'])
    return dict(version_evidence=list(entries.values()), version_source_mappings=mappings)


def deliver_imports(records, artifacts, packet):
    """Slate imports use the same version set as results, without statement filtering."""
    if packet.get('subject', {}).get('kind') != 'problem' or packet.get('role') not in ('slate', 'result') or not has_imported_objections(records):
        return
    packet[PROMPT_PARAMETER] = dict(protocol=PROTOCOL, import_set_digest=import_set_digest(records))
    if packet['role'] == 'slate':
        from .problem_claims import compute_problem_evidence_digest
        from .review_protocol import _reference_templates, describe_objection_delivery, collect_result_objections, build_problem_subject
        current = packet.setdefault('material_objections', [])
        positions = {row['id']: index for index, row in enumerate(current)}
        for row in collect_result_objections(records, artifacts, build_problem_subject(records), compute_problem_evidence_digest(records, artifacts), []):
            if 'material_members' in row:
                delivered = dict(_reference_templates(row), **describe_objection_delivery(records, artifacts, row))
                if row['id'] in positions:
                    current[positions[row['id']]] = delivered
                else:
                    current.append(delivered)
