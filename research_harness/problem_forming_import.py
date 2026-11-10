"""Verified forming history retained in portable source snapshots.

Only the manifest is a destination record. Origin-qualified citations resolve
inside the retained proof, never against the destination's native namespace.
"""

from contextlib import contextmanager, ExitStack
from datetime import datetime, timezone
from pathlib import Path

from . import problem_objection_provenance as proof, strategy
from .errors import ResearchError
from .evidence import digest
from .operations import fields, immutable_record, prepared_mutation, strings, text
from .storage import Store, _read_snapshot

PROTOCOL = 'exactory-problem-forming-import-v1'
HISTORY_REVISION_FIELDS = (('forming_call', 'recorded_revision'), ('form_outcome', 'recorded_revision'),
                           ('probe', 'revision'), ('probe_intent', 'revision'), ('cycle_plan', 'planned_revision'),
                           ('execution', 'recorded_revision'), ('cycle_assessment', 'assessed_revision'))
HISTORY_KINDS = tuple(kind for kind, _ in HISTORY_REVISION_FIELDS)
SOURCE_REVISION_FIELDS = HISTORY_REVISION_FIELDS + (('problem_forming_import', 'recorded_revision'),)
MAX_SOURCES = 32
REFRESH_MESSAGE = ('The forming history of a linked source changed after its import. Run forming-import again with a new '
                   'id, the current revision of that source and the same source_candidate_ids. Then cite the current '
                   'records in the candidates, outcomes and accounts of this record that the change affects.')
UNAVAILABLE_MESSAGE = ('The source workspace of a forming import cannot be opened as the problem record that the import '
                       'names. Restore the workspace at its path. When it moved, run forming-import again with a new '
                       'id and its new path.')
PROVENANCE_MESSAGE = ('A forming import does not reproduce from the source snapshot that it retains, or the history of '
                      'the source does not authenticate its forming records. Restore the damaged store and its '
                      'artifacts from an intact copy, then run the command again.')
SELF_IMPORT_MESSAGE = ('The source is this record, an alias or a copy of it, or a record whose own imports lead back to '
                       'it. Name the workspace of another problem record as source_workspace.')
ORIGIN_CONFLICT_MESSAGE = ('Two copies of one source give different bytes for the same original record. Import only '
                           'from the copy that continues the history of the earlier imports.')


def collect_source_records(records):
    held = {(kind, identifier): row for kind in HISTORY_KINDS for identifier, row in records.get(kind, {}).items()}
    for plan in records.get('cycle_plan', {}).values():
        declaration = plan['payload'].get('test_declaration')
        if declaration is not None:
            identifier = declaration['search_id']
            held[('literature_search', identifier)] = strategy.get_record(records, 'literature_search', identifier)
        objective = plan['payload']['objective']
        if objective['id'] in records.get('problem_statement', {}):
            held[('problem_statement', objective['id'])] = records['problem_statement'][objective['id']]
    for execution in records.get('execution', {}).values():
        identifier = execution['payload']['origin'].get('admission_id')
        for kind in ('execution_admission', 'execution_outcome'):
            if identifier in records.get(kind, {}):
                held[(kind, identifier)] = records[kind][identifier]
    for outcome in records.get('form_outcome', {}).values():
        for item in outcome['evidence']:
            reference = item['reference']
            if reference['kind'] == 'result':
                identifier = reference.get('native_attempt_digest')
                if identifier is None:
                    attempt = records['problem_attempt'][reference['attempt_id']]
                    held[('problem_attempt', attempt['id'])] = attempt
                    identifier = attempt['solver']['native_attempt_digest']
                held[('native_attempt', identifier)] = records['native_attempt'][identifier]
                native = records['native_attempt'][identifier]
                pointer = digest({name: native[name] for name in ('attack_root', 'objective_id')})
                held[('native_attempt_current', pointer)] = records['native_attempt_current'][pointer]
            elif reference['kind'] == 'reading':
                reading = records['reading'][reference['reading_id']]
                held[('reading', reading['id'])] = reading
                held[('work', reading['version_id'])] = records['work'][reading['version_id']]
                bundle = records['source_bundle'][reading['bundle_id']]
                held[('source_bundle', reading['bundle_id'])] = bundle
    for (kind, _), row in list(held.items()):
        if kind == 'literature_search':
            for response in row['responses']:
                identifier = response['source_id']
                held[('source', identifier)] = records['source'][identifier]
    return held


def find_original_date(record):
    for value in (record, record.get('payload', {})):
        for key in ('recorded_at', 'created_at', 'started_at', 'finished_at', 'searched_at', 'captured_at'):
            if value.get(key) is not None:
                return value[key]
    return 'unknown'


def describe_standing(kind, record):
    if kind == 'form_outcome':
        return dict(claim=record['standing'], disposition=record['disposition'],
                    evidence=[item['standing'] for item in record['evidence']])
    if kind == 'execution':
        return dict(status=record['payload']['status'], plan_id=record['payload']['cycle_id'])
    if kind == 'cycle_plan':
        return dict(test_declaration=record['payload'].get('test_declaration'))
    if kind == 'native_attempt':
        return dict(acceptances=record['acceptances'])
    return {'kind': 'exploratory' if kind == 'probe' else kind}


def find_imported_availability(records, reference):
    imported = collect_inherited(records).get(reference['origin'])
    if imported is None:
        return []
    return next((member['availability'] for member in imported['members']
                 if (member['record_kind'], member['id'], member['digest']) ==
                    (reference['record_kind'], reference['id'], reference['digest'])), [])


def describe_availability(records, origin, reference):
    from .problem_accounts import find_evidence_availability, find_result_revision
    if reference['kind'] == 'record':
        return find_imported_availability(records, reference)
    revision = None
    if reference['kind'] == 'result':
        revision = find_result_revision(records, reference)
    elif reference['kind'] == 'computation':
        revision = records['execution'][reference['execution_id']]['recorded_revision']
    elif reference['kind'] == 'reading':
        revision = records['reading'][reference['reading_id']].get('recorded_revision')
    return [dict(origin=origin, revision=revision, date=find_evidence_availability(records, reference))]


def describe_member(records, origin, kind, identifier, record):
    availability, form_ids, call_id = [], [], None
    if kind == 'forming_call':
        call_id = identifier
        form_ids = [form['id'] for form in record['forms'] if form['purpose']['kind'] == 'expected_statement']
    elif kind == 'form_outcome':
        form_ids = [record['form_id']]
        call_id = next(call['id'] for call in records['forming_call'].values() if any(form['id'] == record['form_id'] for form in call['forms']))
        availability = [row for item in record['evidence'] for row in describe_availability(records, origin, item['reference'])]
    elif kind == 'execution':
        availability = [dict(origin=origin, revision=record['recorded_revision'], date=None)]
    elif kind == 'reading':
        availability = describe_availability(records, origin, dict(kind='reading', reading_id=identifier, version_id=record['version_id']))
    elif kind == 'native_attempt':
        for outcome in records.get('form_outcome', {}).values():
            for item in outcome['evidence']:
                reference = item['reference']
                if reference['kind'] != 'result':
                    continue
                native = reference.get('native_attempt_digest')
                if native is None:
                    native = records['problem_attempt'][reference['attempt_id']]['solver']['native_attempt_digest']
                if native == identifier:
                    availability.extend(describe_availability(records, origin, reference))
    return dict(origin=origin, record_kind=kind, id=identifier, digest=digest(record), date=find_original_date(record),
                revision=record.get('recorded_revision', record.get('revision')), standing=describe_standing(kind, record),
                availability=availability, form_ids=form_ids, forming_call_id=call_id)


def describe_source(records, origin, candidate_ids):
    from .problem_account_derivations import (collect_form_history, collect_statement_lineage, compute_history_counts,
                                              describe_run_checks, describe_probe_computation)
    from .problem_forming import count_forming
    for identifier in candidate_ids:
        strategy.get_record(records, 'problem_candidate', identifier, code='problem_candidate_missing')
    held = collect_source_records(records)
    members = [describe_member(records, origin, kind, identifier, row)
               for (kind, identifier), row in sorted(held.items())]
    history = []
    for identifier in candidate_ids:
        for row in collect_form_history(records, dict(candidate_id=identifier)):
            if row not in history:
                history.append(row)
    from .problem_claims import find_value
    accounts = (find_value(records) or {}).get('accounts', dict(answer=dict(id='none'), relations=[]))
    statements = sorted((row for row in records.get('problem_statement', {}).values() if row.get('candidate_id') in candidate_ids),
                        key=lambda row: row['recorded_revision'], reverse=True)
    lineage = {}
    for statement in statements:
        for ancestor in collect_statement_lineage(records, statement):
            lineage.setdefault(ancestor['id'], ancestor)
    checks = describe_run_checks(records, accounts, list(lineage.values()))
    from .problem_accounts import collect_entries, collect_entry_evidence
    entries = {entry['id']: entry for entry in collect_entries(accounts)}
    for member in members:
        if member['record_kind'] != 'execution':
            continue
        member['standing']['checks'] = [dict(id=entry['id'], standing=entry['standing'], passed=entry['passed'])
            for entry in accounts.get('checks', []) if any(reference['kind'] == 'computation' and reference['execution_id'] == member['id']
                for reference in collect_entry_evidence(entries, entry['id']))]
        checked = next((row for row in checks['current'] + checks['earlier'] if row['execution_id'] == member['id']), None)
        if checked is not None:
            member['standing']['statement_digest'] = checked['statement_digest']
            member['standing']['failure_signals'] = checked['failure_signals']
    counts = compute_history_counts(records, history, checks)
    computations = [describe_probe_computation(records, row)
                    for row in records.get('probe', {}).values()]
    computations.extend(dict(kind='execution', id=row['id'], revision=row['recorded_revision'],
                             question=records['cycle_plan'][row['payload']['cycle_id']]['payload']['question'])
                        for row in records.get('execution', {}).values())
    inherited = [dict(source_origin=source_origin, source_revision=row['source_revision'], members=row['members'],
                      history=row['history'], counts=row['counts'], checks=row['checks'], computations=row['computations'],
                      recorded_revision=row['recorded_revision']) for source_origin, row in sorted(collect_inherited(records).items())]
    return dict(members=members, history=history, counts=counts, forming=count_forming(records), inherited=inherited,
                checks=checks, computations=sorted(computations, key=lambda row: (row['revision'], row['kind'], row['id'])),
                candidate_digests={identifier: digest(records['problem_candidate'][identifier]) for identifier in candidate_ids})


def collect_links(records):
    links = {}
    for imported in sorted(records.get('problem_forming_import', {}).values(), key=lambda row: (row['recorded_revision'], row['id'])):
        for link in imported['sources']:
            previous = links.get(link['origin'])
            if previous is None or previous['source_revision'] <= link['source_revision']:
                links[link['origin']] = link
    return links


def resolve_source(workspace):
    from .problem_objection_carry import _source_path
    try:
        return _source_path(workspace)
    except ResearchError as error:
        raise ResearchError('problem_forming_source_unavailable', UNAVAILABLE_MESSAGE, error.details) from error


@contextmanager
def guard_sources(artifacts, links):
    """Use the carry's authenticated store read and hold it through destination CAS."""
    with ExitStack() as stack:
        cuts = {}
        for link in sorted(links, key=lambda row: row['workspace']):
            path = resolve_source(link['workspace'])
            try:
                source = Store(path)
                connection = stack.enter_context(source._connection())
                snapshot = _read_snapshot(connection)
                origin = proof.source_identity(connection, snapshot['records'], additional_revision_fields=SOURCE_REVISION_FIELDS)
            except (OSError, RuntimeError, ResearchError) as error:
                if isinstance(error, ResearchError) and error.code == 'store_busy':
                    raise
                if isinstance(error, ResearchError) and error.code == 'problem_objection_provenance_invalid':
                    raise ResearchError('problem_forming_provenance_invalid', PROVENANCE_MESSAGE, error.details) from error
                raise ResearchError('problem_forming_source_unavailable', UNAVAILABLE_MESSAGE,
                                    dict(source_workspace=link['workspace'], cause=getattr(error, 'code', type(error).__name__))) from error
            if link.get('origin', origin) != origin:
                raise ResearchError('problem_forming_source_unavailable', UNAVAILABLE_MESSAGE,
                                    dict(source_workspace=link['workspace'], cause='source_identity_changed'))
            cuts[origin] = dict(store=source, connection=connection, snapshot=snapshot, origin=origin)
        yield cuts


def require_fresh_sources(records, artifacts, *, guards=None):
    links = collect_links(records)
    if not links:
        return
    from .problem_objection_carry import _offline
    if _offline(artifacts):
        return
    with ExitStack() as local_guards:
        sources = (guards or local_guards).enter_context(guard_sources(artifacts, list(links.values())))
        for origin, link in links.items():
            cut = sources[origin]
            foreign = cut['snapshot']['records']
            try:
                actual = digest(describe_source(foreign, origin, link['candidate_ids']))
            except ResearchError:
                actual = None
            if actual != link['cut']:
                raise ResearchError('problem_forming_refresh_required', REFRESH_MESSAGE,
                                    dict(source_workspace=link['workspace'], source_revision=cut['snapshot']['revision']))


def import_forming(store, payload, *, expected_revision, request_id):
    from .problem_claims import require_problem
    artifacts = store.build_artifact_store()
    with ExitStack() as guards:
        def prepare(records, value):
            require_problem(records)
            fields(value, ('id', 'source_workspace', 'source_revision', 'source_candidate_ids'))
            text(value['id'], 'Forming import ID')
            text(value['source_workspace'], 'Absolute source workspace')
            strings(value['source_candidate_ids'], 'Source candidate IDs', nonempty=True)
            path = Path(value['source_workspace'])
            if not path.is_absolute() or type(value['source_revision']) is not int or value['source_revision'] < 0:
                raise ResearchError('invalid_input', 'Give source_workspace as an absolute path and source_revision as a '
                                    'nonnegative integer')
            path = resolve_source(path)
            if path == store.root.resolve():
                raise ResearchError('problem_forming_self_import', SELF_IMPORT_MESSAGE)
            with Store(store.root)._connection() as connection:
                destination = proof.source_identity(connection, records)
            sources = guards.enter_context(guard_sources(artifacts, [dict(workspace=str(path))]))
            source = next(iter(sources.values()))
            if source['origin'] == destination:
                raise ResearchError('problem_forming_self_import', SELF_IMPORT_MESSAGE)
            snapshot = source['snapshot']
            if snapshot['revision'] != value['source_revision']:
                raise ResearchError('problem_forming_source_revision_mismatch',
                                    'source_revision is not the current revision of the source. Read that revision with '
                                    'exactory-research status in the source workspace, and run the import again with it.',
                                    dict(source_revision=snapshot['revision'], expected_source_revision=value['source_revision']))
            foreign = snapshot['records']
            source_artifacts = source['store'].build_artifact_store()
            verify_imports(foreign, source_artifacts, expected_origin=source['origin'])
            require_fresh_sources(foreign, source_artifacts, guards=guards)
            proof.verify_carries(foreign, source_artifacts, expected_origin=source['origin'])
            summary = describe_source(foreign, source['origin'], value['source_candidate_ids'])
            bundle = proof.capture_proof(source['connection'], foreign, source_artifacts, artifacts, snapshot['revision'])
            links = collect_links(foreign)
            links[source['origin']] = dict(origin=source['origin'], workspace=str(path), source_revision=snapshot['revision'],
                                           candidate_ids=value['source_candidate_ids'], cut=digest(summary))
            if destination in links:
                raise ResearchError('problem_forming_self_import', SELF_IMPORT_MESSAGE)
            if len(set(collect_links(records)) | set(links)) > MAX_SOURCES:
                raise ResearchError('problem_forming_sources_exceeded',
                                    'This import would link the record to more source records than the limit, counting '
                                    'the records that each source imported from. The harness records no part of the '
                                    'import. Stop, and report the limit and the sources.', dict(limit=MAX_SOURCES))
            saved = records.get('problem_forming_import', {}).get(value['id'])
            record = dict(value, source_workspace=str(path), protocol=PROTOCOL, source_origin=source['origin'],
                          destination_origin=destination, proof=bundle, sources=[link for _, link in sorted(links.items())],
                          recorded_revision=expected_revision + 1, recorded_at=datetime.now(timezone.utc).isoformat(), **summary)
            if saved is not None:
                raise ResearchError('record_conflict', 'This forming import id is in use, and an import record keeps its '
                                    'source cut. Run the import under a new id.')
            combined = dict(records, problem_forming_import=dict(records.get('problem_forming_import', {}), **{value['id']: record}))
            verify_imports(combined, artifacts, expected_origin=destination)
            return [immutable_record(records, 'problem_forming_import', value['id'], record)], record
        return prepared_mutation(store, 'problem.forming_import', payload, prepare,
                                 expected_revision=expected_revision, request_id=request_id)


def verify_imports(records, artifacts, *, expected_origin=None, seen=None, identities=None):
    """Reproduce every manifest offline, using the carry's proof verifier."""
    seen = {} if seen is None else seen
    identities = {} if identities is None else identities
    for imported in records.get('problem_forming_import', {}).values():
        if (imported.get('protocol') != PROTOCOL or expected_origin is not None
                and imported['destination_origin'] != expected_origin):
            raise ResearchError('problem_forming_provenance_invalid', PROVENANCE_MESSAGE)
        key = digest([imported['proof'], imported['source_candidate_ids']])
        if key not in seen:
            with open_snapshot(artifacts, imported['proof']) as (foreign, local):
                origin = imported['proof']['origin']
                verify_imports(foreign, local, expected_origin=origin, seen=seen, identities=identities)
                summary = describe_source(foreign, origin, imported['source_candidate_ids'])
                for record in collect_source_records(foreign).values():
                    for reference in proof.all_references(record):
                        proof._read_retained(local, reference)
                seen[key] = (summary, collect_links(foreign))
        summary, foreign_links = seen[key]
        origin = imported['proof']['origin']
        links = dict(foreign_links)
        links[origin] = dict(origin=origin, workspace=imported['source_workspace'], source_revision=imported['source_revision'],
                            candidate_ids=imported['source_candidate_ids'], cut=digest(summary))
        if (origin != imported['source_origin'] or imported['proof']['revision'] != imported['source_revision']
                or any(imported.get(key) != value for key, value in summary.items())
                or imported['sources'] != [link for _, link in sorted(links.items())]):
            raise ResearchError('problem_forming_provenance_invalid', PROVENANCE_MESSAGE)
        for member in imported['members']:
            if member['record_kind'] not in HISTORY_KINDS + ('literature_search',):
                continue
            try:
                proof._bind_origin_record(identities, dict(origin=origin, kind=member['record_kind'], id=member['id'], digest=member['digest']))
            except ResearchError as error:
                raise ResearchError('problem_forming_origin_conflict', ORIGIN_CONFLICT_MESSAGE,
                                    dict(error.details or {}, source_workspace=imported['source_workspace'])) from error
    return len(records.get('problem_forming_import', {}))


@contextmanager
def open_snapshot(artifacts, bundle):
    try:
        with proof.open_proof(artifacts, bundle, additional_revision_fields=SOURCE_REVISION_FIELDS) as snapshot:
            yield snapshot
    except ResearchError as error:
        if error.code == 'problem_objection_provenance_invalid':
            raise ResearchError('problem_forming_provenance_invalid', PROVENANCE_MESSAGE) from error
        raise


def collect_inherited(records):
    """Latest summary per origin; repeated imports never double-count a source."""
    inherited = {}
    for imported in sorted(records.get('problem_forming_import', {}).values(), key=lambda row: (row['recorded_revision'], row['id'])):
        for row in imported['inherited'] + [imported]:
            origin = row['source_origin']
            previous = inherited.get(origin)
            if previous is not None and previous['source_revision'] > row['source_revision']:
                continue
            available = {} if previous is None else {(item['kind'], item['id']): item['imported_revision'] for item in previous['computations']}
            computations = [dict(item, imported_revision=available.get((item['kind'], item['id']), imported['recorded_revision']))
                            for item in row['computations']]
            inherited[origin] = dict(row, computations=computations,
                                     recorded_revision=min(imported['recorded_revision'], (previous or imported)['recorded_revision']))
    return inherited


@contextmanager
def open_imported_record(records, artifacts, reference):
    fields(reference, ('kind', 'origin', 'record_kind', 'id', 'digest'), code='invalid_strategy')
    for key in ('origin', 'record_kind', 'id', 'digest'):
        text(reference[key], 'Imported record ' + key, code='invalid_strategy')
    routes = [(source['source_revision'], imported)
              for imported in records.get('problem_forming_import', {}).values()
              for source in imported['inherited'] + [imported] if source['source_origin'] == reference['origin']]
    for _, imported in sorted(routes, key=lambda row: (row[0], row[1]['recorded_revision'], row[1]['id']), reverse=True):
        with open_snapshot(artifacts, imported['proof']) as (foreign, local):
            if imported['source_origin'] == reference['origin']:
                if imported['proof']['origin'] != reference['origin']:
                    raise ResearchError('problem_forming_provenance_invalid', PROVENANCE_MESSAGE)
                member = next((row for row in imported['members'] if row['record_kind'] == reference['record_kind'] and row['id'] == reference['id']), None)
                if member is not None:
                    row = foreign[reference['record_kind']][reference['id']]
                    if member['digest'] != reference['digest'] or digest(row) != reference['digest']:
                        raise ResearchError('strategy_evidence_stale', strategy.STALE_EVIDENCE_MESSAGE, dict(id=reference['id']))
                    yield foreign, local, row
                    return
            elif reference['origin'] in collect_inherited(foreign):
                with open_imported_record(foreign, local, reference) as found:
                    yield found
                    return
    raise ResearchError('problem_forming_reference_missing',
                        'No forming import of this record lists a record with this origin, record_kind and id. Copy '
                        'origin, record_kind, id and digest from an entry of members in the receipt of forming-import.',
                        dict(origin=reference['origin'], record_kind=reference['record_kind'], id=reference['id']))


def validate_imported_evidence(records, artifacts, reference, *, exploratory=False):
    from .problem_forming import validate_outcome_evidence
    from .problem_evidence import validate_computation, validate_result, validate_reading
    with open_imported_record(records, artifacts, reference) as (foreign, local, record):
        if reference['record_kind'] == 'probe' and exploratory:
            return 'exploratory'
        if reference['record_kind'] == 'form_outcome' and record['evidence']:
            standing = 'admissible' if any(item['standing'] == 'admissible' for item in record['evidence']) else 'exploratory'
            if standing == 'admissible' or exploratory:
                validate_outcome_evidence(foreign, local, record['evidence'], record['recorded_revision'])
                return standing
        if reference['record_kind'] == 'execution':
            for assessment in foreign.get('cycle_assessment', {}).values():
                for result in assessment['payload']['result']['evidence']:
                    if result.get('execution_id') == record['id']:
                        validate_computation(foreign, local, dict(kind='computation', execution_id=record['id'], output_id=result['output_id'], formal_check=None))
                        return 'admissible'
        if reference['record_kind'] == 'reading':
            validate_reading(foreign, local, dict(kind='reading', reading_id=record['id'], version_id=record['version_id']))
            return 'admissible'
        if reference['record_kind'] == 'native_attempt':
            for outcome in foreign.get('form_outcome', {}).values():
                for item in outcome['evidence']:
                    cited = item['reference']
                    if cited['kind'] == 'result':
                        identifier = cited.get('native_attempt_digest')
                        if identifier is None:
                            identifier = foreign['problem_attempt'][cited['attempt_id']]['solver']['native_attempt_digest']
                        if identifier == reference['id']:
                            validate_result(foreign, cited)
                            return 'admissible'
    raise ResearchError('problem_forming_evidence_invalid',
                        'This imported record is no admissible evidence here. Cite an imported full reading, an imported '
                        'run that was admitted and assessed, an imported solver snapshot whose accepted result an '
                        'outcome of the source cites, or an imported outcome with admissible evidence. An imported '
                        'probe, and an imported outcome whose evidence is exploratory, support only an exploratory '
                        'item: a check that is not a test, or an exploratory evidence item of an outcome.')


def project_imported_record(value):
    from .artifacts import is_sealed_artifact
    from .program_probes import _describe_inventory_artifact
    if isinstance(value, dict):
        if is_sealed_artifact(value):
            return _describe_inventory_artifact(value)
        return {key: project_imported_record(item) for key, item in value.items()}
    if isinstance(value, list):
        return [project_imported_record(item) for item in value]
    return value


def collect_import_projections(records, artifacts):
    delivered = {}
    for imported in sorted(records.get('problem_forming_import', {}).values(), key=lambda row: (row['recorded_revision'], row['id'])):
        origin = imported['source_origin']
        with open_snapshot(artifacts, imported['proof']) as (foreign, local):
            inherited = collect_import_projections(foreign, local)
            rows = [dict(record=project_imported_record(foreign[member['record_kind']][member['id']]),
                         cite=dict(kind='record', origin=origin, record_kind=member['record_kind'], id=member['id'], digest=member['digest']),
                         date=member['date'], standing=member['standing']) for member in imported['members']]
            inherited[origin] = dict(source_origin=origin, source_revision=imported['source_revision'], records=rows)
            for source_origin, row in inherited.items():
                if source_origin not in delivered or delivered[source_origin]['source_revision'] <= row['source_revision']:
                    delivered[source_origin] = row
    return delivered


def describe_imports(records, artifacts):
    verify_imports(records, artifacts)
    return [row for _, row in sorted(collect_import_projections(records, artifacts).items())]


def deliver_slate_imports(records, artifacts, packet):
    if packet.get('subject', {}).get('kind') == 'problem' and packet.get('role') == 'slate' and records.get('problem_forming_import'):
        packet['problem_forming_imports'] = describe_imports(records, artifacts)
