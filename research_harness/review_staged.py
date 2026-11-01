"""Pure contracts for staged problem reviews, without store or transport access.

An epoch owns a manifest of sources, distinct occurrences, frame fields, claims,
original context closures and assurance seeds. Text spans use Unicode offsets
and SHA256 of the exact UTF-8 slice. Artifact ownership uses the whole descriptor.

Tasks bind input spans, original claim contexts, dependencies and measured work.
Selections contain ``observations`` with exact task/request/output bindings and
an ``actual_input`` projection (history, intervals, binary_sources, claim_ids,
context_roots, complete_cards). Outputs contain cards, cells, combinations,
selected_groups, original_checks, integrations, findings, answers, retrievals,
new_dependencies and an optional terminal. Callers authenticate observations and
derive actual_input from actual fitted requests before calling this module. A
digest here checks integrity; it does not establish instrumented provenance.

Profiles supply measured additive rendered character contributions, output token
work, margins and duration work. There is no universal character-to-token
conversion or size limit. Planned task request digests are bound with freeze_task
after rendering, before dispatch. Composition verification is supplied as a
bound certificate by its separate versioned verifier.
"""

import copy
import hashlib
import itertools

from .errors import ResearchError
from .evidence import digest
from .operations import fields as check_fields, text as check_text, strings as check_ids, is_finite_number
from .problem_rubric import PROBLEM_CHECKS as ASSURANCES
from .review_objection_evidence import resolve_objection_entry, resolved_objection_entries
from .storage import _canonical

PROTOCOL = 'exactory-review-staged-v2'
MANIFEST_SCHEMA = 'exactory-review-manifest-v1'
TASK_SCHEMA = 'exactory-review-task-v1'
COMPLETION_SCHEMA = 'exactory-review-completion-v1'
INCOMPLETE_SCHEMA = 'exactory-assessment-incomplete-v1'
CONTEXT_GRAPH_SCHEMA = 'exactory-review-context-graph-v1'
SERIALIZATION = 'canonical-json-utf8-v1'
TASK_TYPES = ('frame', 'read', 'combine', 'original_check', 'integrate', 'answer', 'final')
SCOPES = ('joint_original', 'segmented_original', 'joint_card', 'segmented_card',
          'original_segment', 'card_segment', 'unresolved')


def _fail(message, code='invalid_review_staged', **details):
    raise ResearchError(code, message, details or None)


def _check_number(value, name, positive=False, integer=False):
    valid = is_finite_number(value) and (not integer or type(value) is int)
    if not valid or value < 0 or positive and value == 0:
        _fail('Expected a finite nonnegative number.', field=name)
    return value


def _require_array(value, name):
    if not isinstance(value, list):
        _fail('Expected an array.', field=name)
    return value


def _seal(value):
    result = copy.deepcopy(value)
    result['digest'] = digest(value)
    return result


def _verify_seal(value):
    check_fields(value, ('digest',), None, code='invalid_review_staged')
    if value['digest'] != digest({k: v for k, v in value.items() if k != 'digest'}):
        _fail('Record digest does not match its content.')


def _index(rows, name):
    result = {}
    for row in _require_array(rows, name):
        check_fields(row, ('id',), None, code='invalid_review_staged')
        key = check_text(row['id'], name, code='invalid_review_staged')
        if key in result and result[key] != row:
            _fail('Conflicting duplicate identity.', field=name, id=key)
        result[key] = row
    return result


def _merge_ranges(intervals, length=None):
    result = []
    for interval in _require_array(intervals, 'intervals'):
        if not isinstance(interval, (list, tuple)) or len(interval) != 2:
            _fail('Expected a half-open interval.')
        start, end = interval
        _check_number(start, 'start', integer=True)
        _check_number(end, 'end', integer=True)
        if end < start or length is not None and end > length:
            _fail('Interval lies outside the source.')
        if start != end:
            result.append([start, end])
    union = []
    for start, end in sorted(result):
        if union and start <= union[-1][1]:
            union[-1][1] = max(union[-1][1], end)
        else:
            union.append([start, end])
    return union


def interval_gaps(required, delivered):
    """Return the exact uncovered half-open intervals, merging overlaps."""
    pending = _merge_ranges(required)
    for left, right in _merge_ranges(delivered):
        remaining = []
        for start, end in pending:
            if right <= start or left >= end:
                remaining.append([start, end])
            else:
                if start < left:
                    remaining.append([start, left])
                if right < end:
                    remaining.append([right, end])
        pending = remaining
    return pending


def make_source(source_id, kind, text=None, *, identity, descriptor=None, required_intervals=None):
    """Create an exact text source or an opaque native binary obligation."""
    check_text(source_id, 'source_id', code='invalid_review_staged')
    if kind not in ('item', 'record', 'passage', 'source_document', 'artifact', 'frame', 'binary'):
        _fail('Unknown source kind.')
    if not isinstance(identity, dict) or not identity:
        _fail('A source needs its original identity.')
    _canonical(identity, code='invalid_review_staged')
    if kind == 'binary':
        if text is not None or not isinstance(descriptor, dict) or not descriptor:
            _fail('A native binary needs its descriptor and cannot be certified as text.')
        intervals, characters, sha256 = [], None, None
    else:
        if not isinstance(text, str):
            _fail('A text source needs exact original characters.')
        characters = len(text)
        intervals = _merge_ranges(required_intervals if required_intervals is not None else [[0, characters]], characters)
        sha256 = hashlib.sha256(text.encode('utf-8')).hexdigest()
    return dict(id=source_id, kind=kind, identity=copy.deepcopy(identity), text=text,
                descriptor=copy.deepcopy(descriptor), required_intervals=intervals,
                characters=characters, sha256=sha256)


def source_span(source, start, end):
    """Bind a Unicode character interval to the exact original UTF-8 bytes."""
    if source['kind'] == 'binary':
        _fail('Binary originals have no text span.')
    _merge_ranges([[start, end]], len(source['text']))
    return dict(source_id=source['id'], start=start, end=end,
                sha256=hashlib.sha256(source['text'][start:end].encode('utf-8')).hexdigest())


def materialize_quotation(source, span, *, delivered=None, matching_text=None, allow_empty=False):
    """Materialize original characters, optionally checking normalized matching."""
    check_fields(span, ('source_id', 'start', 'end', 'sha256'), code='invalid_review_staged')
    if span['source_id'] != source['id'] or span != source_span(source, span['start'], span['end']):
        _fail('Quotation span does not match the exact source.')
    if delivered is not None:
        portions = [[row['start'], row['end']] for row in delivered if row['source_id'] == source['id']]
        if interval_gaps([[span['start'], span['end']]], portions):
            _fail('Quotation was not within delivered scope.')
    original = source['text'][span['start']:span['end']]
    from .review_excerpts import normalize_with_offsets
    if not allow_empty and not normalize_with_offsets(original)[0]:
        _fail('Scientific quotations require nonempty original text.')
    if matching_text is not None:
        if normalize_with_offsets(original)[0] != normalize_with_offsets(matching_text)[0]:
            _fail('Quotation text does not match the source normalization.')
    return dict(span=copy.deepcopy(span), text=original, kind='quotation')


def bind_quotations(output, *, epoch, sources):
    """Bind reviewer quotations without changing the authenticated raw output.

    Unmatched quotations remain unbound so their containing record fails its
    validator. Other records in the observation retain independent credit.
    Sources are an explicit catalog (a list or an ID mapping) of epoch originals.
    """
    from .review_excerpts import normalize_with_offsets, _locate
    originals = _index(epoch['manifest']['sources'], 'sources')
    supplied = sources.values() if isinstance(sources, dict) else sources
    catalog = {}
    for source in supplied:
        if source['id'] not in originals or source != originals[source['id']]:
            _fail('Quotation catalog differs from its frozen original.', source_id=source['id'])
        if source['kind'] != 'binary':
            catalog[source['id']] = source
    normalized_sources = {}
    unmatched = []

    def bind(value):
        if isinstance(value, dict):
            if set(value) == {'source_id', 'text'}:
                source = catalog.get(value['source_id']) if isinstance(value['source_id'], str) else None
                phrase = normalize_with_offsets(value['text'])[0] if isinstance(value['text'], str) else ''
                location = None
                if source is not None and phrase:
                    if source['id'] not in normalized_sources:
                        normalized, offsets = normalize_with_offsets(source['text'])
                        normalized_sources[source['id']] = dict(normalized=normalized, offsets=offsets)
                    location = _locate(normalized_sources[source['id']], phrase)
                if location is not None:
                    span = source_span(source, *location)
                    try:
                        materialize_quotation(source, span, matching_text=value['text'])
                        return span
                    except ResearchError:
                        # A match inside one expanded NFKC character need not
                        # be an exact quotation of the original character.
                        pass
                unmatched.append(copy.deepcopy(value))
                return copy.deepcopy(value)
            return {key: bind(item) for key, item in value.items()}
        if isinstance(value, list):
            return [bind(item) for item in value]
        return copy.deepcopy(value)

    return bind(output), unmatched


def derive_composition_terminal(epoch, terminal):
    """Derive the composition authorization from the staged terminal contract."""
    from .review_composition import PROTOCOL as COMPOSITION_PROTOCOL
    role = 'incomplete' if isinstance(terminal, dict) and terminal.get('origin') == 'reviewer' else epoch['role']
    role_fields = {'result': ('assurances',), 'adjudicator': ('objection_id', 'disposition', 'correction_admissible'),
                   'incomplete': ('origin',)}[role]
    check_fields(terminal, ('composition_root', 'decisions') + role_fields,
                 ('integration_id', 'visible_conditions', 'record_root'), code='invalid_review_staged')
    if not isinstance(terminal['decisions'], dict):
        _fail('Terminal decisions must be keyed by scientific scalar paths.')
    if role == 'result':
        check_fields(terminal['assurances'], ASSURANCES, code='invalid_review_staged')
    choices = copy.deepcopy(terminal['decisions'])
    for kind, status in terminal.get('assurances', {}).items():
        if choices.get('/value/assurances/' + kind + '/status') != status:
            _fail('Terminal assurance and scalar choice disagree.', assurance=kind)
    for field in ('objection_id', 'disposition', 'correction_admissible', 'origin'):
        if field in terminal and choices.get('/' + field) != terminal[field]:
            _fail('Terminal field and scalar choice disagree.', field=field)
    return dict(protocol=COMPOSITION_PROTOCOL, epoch_id=epoch['id'], role=role,
                composition_root=terminal['composition_root'], choices=choices)


def project_ordinary_final(output):
    """Split the ordinary scientific object from its staged sidecar records."""
    if not isinstance(output, dict):
        _fail('An ordinary final must be an object.')
    if 'stage' in output:
        required = ('stage', 'support', 'value', 'objections', 'limitations')
        optional = ('reassessment', 'excerpts')
    else:
        required = ('objection_id', 'disposition', 'reason', 'evidence', 'correction_admissible')
        optional = ('reassessment',)
    staged = ('terminal', 'assurance_checks', 'decision_checks', 'answers', 'findings', 'new_dependencies',
              'cards', 'cells', 'combinations', 'selected_groups', 'original_checks', 'integrations',
              'retrievals', 'segment_readings', 'pages')
    check_fields(output, required, optional + staged, code='invalid_review_staged')
    return (copy.deepcopy({key: value for key, value in output.items() if key in required + optional}),
            copy.deepcopy({key: value for key, value in output.items() if key in staged}))


def _iter_frame_leaves(value, path=()):
    if isinstance(value, dict):
        for key in sorted(value):
            for leaf in _iter_frame_leaves(value[key], path + (key,)):
                yield leaf
    elif isinstance(value, list):
        for index, item in enumerate(value):
            for leaf in _iter_frame_leaves(item, path + (index,)):
                yield leaf
    elif type(value) in (str, int, float):
        yield list(path), value


def build_contexts(claims, sources):
    """Compute versioned transitive original closures, including compound children."""
    claim_index, source_index = _index(claims, 'claims'), _index(sources, 'sources')
    result = {}
    for claim in claims:
        closure, pending = set(), [claim['id']]
        while pending:
            key = pending.pop()
            if key in closure:
                continue
            if key not in claim_index:
                _fail('Claim dependency is absent.', claim_id=key)
            closure.add(key)
            row = claim_index[key]
            pending.extend(row.get('dependencies', []) + row.get('children', []))
        spans = []
        for key in sorted(closure):
            row = claim_index[key]
            for span in row['spans']:
                if span['source_id'] not in source_index:
                    _fail('Claim span names an absent original.')
                materialize_quotation(source_index[span['source_id']], span, allow_empty=True)
                if span not in spans:
                    spans.append(copy.deepcopy(span))
        body = dict(claim_ids=sorted(closure), spans=spans,
                    claims=[claim_index[key] for key in sorted(closure)])
        root = digest(['claim-context-v1', body])
        result[claim['id']] = dict(version=root, root=root, claim_ids=sorted(closure), spans=spans)
    return result


def build_manifest(frame, occurrences, sources, *, claims=None, assurance_dependencies=None,
                   inherited_findings=()):
    """Inventory original fields, occurrence ownership and complete source obligations."""
    _canonical(frame, code='invalid_review_staged')
    source_rows, source_aliases, source_identities, descriptor_sources = [], {}, {}, {}
    seen_sources = set()
    for raw in sources:
        check_fields(raw, ('id', 'kind', 'identity'), ('text', 'descriptor', 'required_intervals', 'characters', 'sha256'), code='invalid_review_staged')
        source = make_source(raw['id'], raw['kind'], raw.get('text'), identity=raw['identity'],
                             descriptor=raw.get('descriptor'), required_intervals=raw.get('required_intervals'))
        if source['id'] in seen_sources:
            _fail('Duplicate original source identity.')
        seen_sources.add(source['id'])
        source_identities[source['id']] = copy.deepcopy(source['identity'])
        descriptor_key = digest(source['descriptor']) if source['descriptor'] is not None else None
        prior = descriptor_sources.get(descriptor_key) if descriptor_key is not None else None
        if prior is not None:
            if any(source[key] != prior[key] for key in ('kind', 'text', 'required_intervals')):
                _fail('Identical descriptors have inconsistent original scope.')
            source_aliases[source['id']] = prior['id']
        else:
            source_aliases[source['id']] = source['id']
            source_rows.append(source)
            if descriptor_key is not None:
                descriptor_sources[descriptor_key] = source
    source_index = _index(source_rows, 'sources')
    fields = []
    for path, value in _iter_frame_leaves(frame):
        key = 'frame:' + digest(path)
        source = make_source(key, 'frame', value if isinstance(value, str) else _canonical(value, code='invalid_review_staged'),
                             identity={'path': path, 'serialization': SERIALIZATION})
        if key in source_index:
            _fail('Frame source identity collision.')
        source_rows.append(source)
        source_index[key] = source
        fields.append(dict(id=key, path=path, value=copy.deepcopy(value), source_id=key,
                           span=source_span(source, 0, source['characters'])))
    bodies, occurrence_rows, positions = {}, [], set()
    for raw in occurrences:
        check_fields(raw, ('key', 'index', 'item', 'artifacts', 'source_ids'), ('record_reference', 'evidence_class'), code='invalid_review_staged')
        check_text(raw['key'], 'key', code='invalid_review_staged')
        _check_number(raw['index'], 'index', integer=True)
        position = (raw['key'], raw['index'])
        if position in positions:
            _fail('Duplicate occurrence position.')
        positions.add(position)
        descriptors = copy.deepcopy(raw['artifacts'])
        descriptor_keys = [digest(row) for row in descriptors]
        if len(set(descriptor_keys)) != len(descriptor_keys):
            _fail('Duplicate owned artifact descriptor.')
        identity = [raw['key'], raw['index'], digest(raw['item']), descriptors]
        key = 'occurrence:' + digest(identity)
        item_source = make_source('item:' + key, 'item', _canonical(raw['item'], code='invalid_review_staged'),
                                  identity={'occurrence_id': key, 'serialization': SERIALIZATION})
        source_rows.append(item_source)
        source_index[item_source['id']] = item_source
        source_ids = [item_source['id']]
        for source_id in check_ids(raw['source_ids'], 'source_ids', code='invalid_review_staged'):
            canonical = source_aliases.get(source_id, source_id)
            if canonical not in source_ids:
                source_ids.append(canonical)
        for source_id in source_ids:
            if source_id not in source_index:
                _fail('Occurrence names an absent source.', source_id=source_id)
        for descriptor, descriptor_key in zip(descriptors, descriptor_keys):
            candidates = [source for source in source_rows if source['descriptor'] == descriptor]
            if not candidates:
                _fail('Owned artifact has no original source.', descriptor=descriptor)
            signatures = {(source['kind'], source['sha256'], _canonical(source['required_intervals'], code='invalid_review_staged')) for source in candidates}
            if len(signatures) != 1:
                _fail('Identical descriptors have inconsistent original scope.')
            source_id = candidates[0]['id']
            source = candidates[0]
            if source['kind'] != 'binary' and source['required_intervals'] != _merge_ranges([[0, source['characters']]]):
                _fail('An owned artifact requires its complete original body.')
            if source_id not in source_ids:
                source_ids.append(source_id)
            body = bodies.setdefault(descriptor_key, dict(id='body:' + descriptor_key, descriptor=descriptor,
                                       source_id=source_id, occurrence_ids=[]))
            body['occurrence_ids'].append(key)
        occurrence_rows.append(dict(id=key, key=raw['key'], index=raw['index'], item_digest=digest(raw['item']),
                                    item=copy.deepcopy(raw['item']), item_source_id=item_source['id'],
                                    record_reference=copy.deepcopy(raw.get('record_reference')),
                                    artifacts=descriptors, source_ids=source_ids))
        if 'evidence_class' in raw:
            if raw['evidence_class'] not in ('original_context', 'current'):
                _fail('Unknown evidence class.')
            occurrence_rows[-1]['evidence_class'] = raw['evidence_class']
    if claims is None:
        claims = [dict(id='claim:' + field['id'], spans=[field['span']], dependencies=[], children=[])
                  for field in fields]
    bound, _ = bind_quotations({'claims': claims}, epoch={'manifest': {'sources': source_rows}}, sources=source_rows)
    claim_rows = bound['claims']
    for claim in claim_rows:
        check_fields(claim, ('id', 'spans', 'dependencies', 'children'), ('kind',), code='invalid_review_staged')
        check_ids(claim['dependencies'], 'dependencies', code='invalid_review_staged')
        check_ids(claim['children'], 'children', code='invalid_review_staged')
        if claim.get('kind', 'claim') not in ('claim', 'condition', 'definition', 'record'):
            _fail('Unknown frame entry kind.')
        if not claim['spans']:
            _fail('A claim must retain original spans.')
    contexts = build_contexts(claim_rows, source_rows)
    for field in fields:
        covered = [[span['start'], span['end']] for claim in claim_rows for span in claim['spans']
                   if span['source_id'] == field['source_id']]
        if interval_gaps([[0, source_index[field['source_id']]['characters']]], covered):
            _fail('Claim decomposition omits an original frame field.', field=field['path'])
    # Unknown field families seed every assurance; known families seed their
    # required scientific questions. The stop assessment always examines the frame.
    dependencies = {kind: [] for kind in ASSURANCES}
    for field in fields:
        family = str(field['path'][0]).lower() if field['path'] else ''
        if family in ('statement', 'claim', 'resolution_criteria', 'definitions'):
            kinds = ('problem_statement', 'stop')
        elif family in ('openness', 'openness_findings', 'openness_searches'):
            kinds = ('problem_openness', 'stop')
        elif family in ('value', 'motivation', 'assessment', 'grade', 'rubric', 'calibration'):
            kinds = ('problem_value', 'stop')
        elif family == 'citations':
            kinds = ('problem_citations', 'stop')
        elif family in ('attempt', 'results'):
            kinds = ('problem_statement', 'problem_value', 'stop')
        else:
            kinds = ASSURANCES
        field_claims = [claim['id'] for claim in claim_rows
                        if any(span['source_id'] == field['source_id'] for span in claim['spans'])]
        for kind in kinds:
            for key in field_claims:
                for dependency in contexts[key]['claim_ids']:
                    if dependency not in dependencies[kind]:
                        dependencies[kind].append(dependency)
    for kind, extra in (assurance_dependencies or {}).items():
        if kind not in ASSURANCES:
            _fail('Unknown assurance kind.')
        for key in check_ids(extra, 'assurance_dependencies', code='invalid_review_staged'):
            if key not in contexts:
                _fail('Assurance names an unknown claim.')
            if key not in dependencies[kind]:
                dependencies[kind].append(key)
    return _seal(dict(schema=MANIFEST_SCHEMA, serialization=SERIALIZATION, frame=copy.deepcopy(frame),
                      frame_fields=fields, sources=source_rows, occurrences=occurrence_rows,
                      source_aliases=source_aliases, source_identities=source_identities,
                      bodies=list(bodies.values()), claims=claim_rows, contexts=contexts,
                      assurance_dependencies=dependencies, inherited_findings=copy.deepcopy(list(inherited_findings))))


def freeze_epoch(spec):
    """Freeze an epoch and return an independent, canonically sealed record."""
    required = ('id', 'controller_assignment_id', 'subject', 'evidence_currency', 'role', 'slot_id',
                'reviewer_id', 'route_id', 'author_id', 'task_policy_version', 'composition_schema_version',
                'calibration_profile_version', 'budgets', 'manifest', 'material_objections', 'completion_requirements')
    check_fields(spec, required, ('protocol', 'predecessor', 'focused_scope'), code='invalid_review_staged')
    for key in ('id', 'controller_assignment_id', 'slot_id', 'reviewer_id', 'route_id', 'author_id',
                'task_policy_version', 'composition_schema_version', 'calibration_profile_version'):
        check_text(spec[key], key, code='invalid_review_staged')
    if spec['role'] not in ('result', 'adjudicator') or spec.get('protocol', PROTOCOL) != PROTOCOL:
        _fail('Unsupported epoch role or protocol.')
    if spec['role'] == 'adjudicator':
        check_fields(spec.get('focused_scope'), ('review_pair_roots', 'objection_id', 'objection_version'), code='invalid_review_staged')
        if len(spec['focused_scope']['review_pair_roots']) != 2:
            _fail('Focused scope requires two exact completed review roots.')
    _verify_seal(spec['manifest'])
    _check_decision_paths(spec['completion_requirements'], spec['role'])
    check_fields(spec['budgets'], ('max_calls', 'max_retries', 'max_rereads', 'max_subdivisions',
                               'max_epochs', 'max_elapsed_seconds'), code='invalid_review_staged')
    for key, value in spec['budgets'].items():
        _check_number(value, key, positive=key in ('max_calls', 'max_elapsed_seconds'),
                integer=key != 'max_elapsed_seconds')
    _canonical(spec, code='invalid_review_staged')
    entries = []
    for objection in spec['material_objections']:
        check_fields(objection, ('id',), None, code='invalid_review_staged')
        for field in ('version_evidence', 'version_source_mappings'):
            for entry in objection.get(field, []):
                check_fields(entry, (), None, code='review_objection_evidence_invalid')
            for index, full in enumerate(resolved_objection_entries(spec, objection, field)):
                entries.append(dict(objection_id=objection['id'], field=field, index=index,
                                    digest=digest(full), entry=copy.deepcopy(full)))
    return _seal(dict(copy.deepcopy(spec), protocol=PROTOCOL, material_objection_entries=entries))


def _validate_epoch(epoch):
    _verify_seal(epoch)
    _verify_seal(epoch['manifest'])
    if epoch['protocol'] != PROTOCOL:
        _fail('Unsupported staged protocol.')


def _check_decision_paths(constraints, role):
    from .review_composition import list_scalar_fields
    check_fields(constraints, (), None, code='invalid_review_staged')
    allowed = list_scalar_fields(role)
    for key in ('permissible_decisions', 'decision_dependencies'):
        if not isinstance(constraints.get(key, {}), dict) or not set(constraints.get(key, {})) <= set(allowed):
            _fail('Decision constraints must use the composition scalar paths.', field=key)
        for path, values in constraints.get(key, {}).items():
            _require_array(values, path)
            if key == 'decision_dependencies':
                check_ids(values, path, code='invalid_review_staged')
            if key == 'permissible_decisions' and allowed[path] is not None and any(
                    digest(value) not in {digest(choice) for choice in allowed[path]} for value in values):
                _fail('Decision constraint has a value outside its scientific schema.', decision=path)


def make_task(epoch, task_type, *, inputs=None, claim_ids=None, occurrence_ids=None, dependencies=None,
              binary_sources=None, card_ids=None, card_segments=None, group_ids=None, scope='joint_original',
              segment_task_ids=None, measurement=None, planned_request_digest=None,
              rendering_policy=SERIALIZATION, required=True, obligation_ids=None, child_ids=None,
              answer_ranges=None, objection_entries=None, contexts=None, reconciliation_id=None,
              composition_role=None, composition_plan_root=None, supersedes=(), context_graph=None):
    """Create an immutable task envelope; freeze the rendered request before dispatch."""
    _validate_epoch(epoch)
    if task_type not in TASK_TYPES or scope not in SCOPES:
        _fail('Unknown task type or assessment scope.')
    manifest = bind_context_graph(epoch, context_graph) if context_graph is not None else epoch['manifest']
    current_contexts = contexts if contexts is not None else manifest['contexts']
    claims = list(claim_ids or [])
    for key in claims:
        if key not in current_contexts:
            _fail('Task names an unknown claim.')
    spans = copy.deepcopy(inputs or [])
    sources = _index(manifest['sources'], 'sources')
    for span in spans:
        if span['source_id'] not in sources:
            _fail('Task names an unknown source.')
        materialize_quotation(sources[span['source_id']], span, allow_empty=True)
    context_roots = {key: current_contexts[key]['root'] for key in claims}
    body = dict(schema=TASK_SCHEMA, protocol=PROTOCOL, epoch_id=epoch['id'], epoch_digest=epoch['digest'],
                type=task_type, inputs=spans, claim_ids=claims, context_roots=context_roots,
                occurrence_ids=list(occurrence_ids or []), binary_sources=copy.deepcopy(binary_sources or []),
                card_ids=list(card_ids or []), card_segments=copy.deepcopy(card_segments or []),
                group_ids=list(group_ids or []), dependencies=list(dependencies or []),
                segment_task_ids=list(segment_task_ids or []), scope=scope,
                obligation_ids=list(obligation_ids or []), child_ids=list(child_ids or []),
                answer_ranges=copy.deepcopy(answer_ranges or []),
                objection_entries=copy.deepcopy(objection_entries or []),
                resource_policy=copy.deepcopy(epoch['budgets']), measurement=copy.deepcopy(measurement),
                output_schema=task_type + '-output-v1', planned_request_digest=planned_request_digest,
                rendering_policy=rendering_policy, required=required)
    for key, value in (('reconciliation_id', reconciliation_id), ('composition_role', composition_role),
                       ('composition_plan_root', composition_plan_root)):
        if value is not None:
            check_text(value, key, code='invalid_review_staged')
            body[key] = value
    if supersedes:
        body['supersedes'] = check_ids(list(supersedes), 'supersedes', code='invalid_review_staged')
    if composition_role is not None and composition_role not in (epoch['role'], 'incomplete'):
        _fail('Composition task role differs from its epoch.')
    for field in ('claim_ids', 'occurrence_ids', 'card_ids', 'group_ids', 'dependencies', 'segment_task_ids',
                  'obligation_ids', 'child_ids'):
        check_ids(body[field], field, code='invalid_review_staged')
    known = {row['id'] for row in manifest['occurrences']}
    if not set(body['occurrence_ids']) <= known:
        _fail('Task names an unknown occurrence.')
    if any(row not in epoch['material_objection_entries'] for row in body['objection_entries']):
        _fail('Task objection evidence is outside the frozen epoch.')
    body['id'] = 'task:' + digest(body)
    return _seal(body)


def freeze_task(task, planned_request_digest, rendering_policy, *, measurement=None):
    """Bind a measured plan to its exact rendered request before assignment."""
    _verify_seal(task)
    check_text(planned_request_digest, 'planned_request_digest', code='invalid_review_staged')
    check_text(rendering_policy, 'rendering_policy', code='invalid_review_staged')
    body = {key: copy.deepcopy(value) for key, value in task.items() if key != 'digest'}
    if measurement is not None:
        body['measurement'] = copy.deepcopy(measurement)
    validate_measurement(body['measurement'])
    body.update(planned_request_digest=planned_request_digest, rendering_policy=rendering_policy)
    return _seal(body)


def joint_fit(sizes, route_limit, output_tokens, profile, *, claim_count=0, preserved=True):
    """Check actual additive input, measured token work and measured duration."""
    _check_number(route_limit, 'route_limit', positive=True, integer=True)
    _check_number(output_tokens, 'output_tokens', positive=True, integer=True)
    required = ('context_characters', 'envelope_characters', 'output_base_tokens',
                'output_tokens_per_claim', 'output_tokens_per_character', 'seconds_per_character',
                'seconds_per_claim', 'duration_base_seconds', 'duration_limit_seconds', 'output_margin_tokens')
    check_fields(profile, required, ('input_character_target',), code='invalid_review_staged')
    for key, value in profile.items():
        _check_number(value, key)
    for size in sizes:
        _check_number(size, 'rendered_characters', integer=True)
    _check_number(claim_count, 'claim_count', integer=True)
    payload = sum(sizes)
    input_characters = payload + profile['context_characters'] + profile['envelope_characters']
    tokens = (profile['output_base_tokens'] + profile['output_tokens_per_claim'] * claim_count +
              profile['output_tokens_per_character'] * payload)
    seconds = (profile['duration_base_seconds'] + profile['seconds_per_character'] * payload +
               profile['seconds_per_claim'] * claim_count)
    input_fits = input_characters <= route_limit
    output_fits = tokens + profile['output_margin_tokens'] <= output_tokens
    duration_fits = seconds <= profile['duration_limit_seconds']
    return dict(fits=bool(input_fits and output_fits and duration_fits and preserved),
                input_fits=input_fits, output_fits=output_fits, duration_fits=duration_fits,
                mandatory_preserved=bool(preserved), input_characters=input_characters,
                output_tokens=tokens, duration_seconds=seconds, route_limit=route_limit,
                configured_output_tokens=output_tokens, output_margin_tokens=profile['output_margin_tokens'],
                duration_limit_seconds=profile['duration_limit_seconds'])


def validate_measurement(measurement):
    """Require consistent admission of input, output, duration and mandatory content."""
    required = ('fits', 'input_fits', 'output_fits', 'duration_fits', 'mandatory_preserved', 'input_characters',
                'output_tokens', 'duration_seconds', 'route_limit', 'configured_output_tokens',
                'output_margin_tokens', 'duration_limit_seconds')
    check_fields(measurement, required, code='invalid_review_staged')
    for key in ('fits', 'input_fits', 'output_fits', 'duration_fits', 'mandatory_preserved'):
        if measurement[key] is not True:
            _fail('Task work has not been admitted.', code='review_task_pending')
    for key in required[5:]:
        _check_number(measurement[key], key)
    if (measurement['input_characters'] > measurement['route_limit'] or
            measurement['output_tokens'] + measurement['output_margin_tokens'] > measurement['configured_output_tokens'] or
            measurement['duration_seconds'] > measurement['duration_limit_seconds']):
        _fail('Measured work exceeds its frozen resource limits.')
    return copy.deepcopy(measurement)


def _collect_original_inputs(manifest, occurrence_ids, claim_ids):
    occurrences = _index(manifest['occurrences'], 'occurrences')
    sources = _index(manifest['sources'], 'sources')
    source_ids = set()
    for key in occurrence_ids:
        if key not in occurrences:
            _fail('Selected combination names an unknown occurrence.')
        source_ids.update(occurrences[key]['source_ids'])
    spans, binaries = [], []
    for key in sorted(source_ids):
        source = sources[key]
        if source['kind'] == 'binary':
            binaries.append(dict(source_id=key, descriptor=source['descriptor']))
        else:
            spans.extend(source_span(source, start, end) for start, end in source['required_intervals'])
    for key in claim_ids:
        if key not in manifest['contexts']:
            _fail('Selected group names an unknown claim.')
        for span in manifest['contexts'][key]['spans']:
            if span not in spans:
                spans.append(copy.deepcopy(span))
    return spans, binaries


def _derive_reconciliation_id(epoch, kind, occurrence_ids, claim_ids, group_ids, contexts):
    roots = {key: contexts[key]['root'] for key in claim_ids}
    return 'reconciliation:' + digest([epoch['digest'], kind, occurrence_ids, claim_ids, group_ids, roots])


def _partition_original(epoch, kind, occurrence_ids, claim_ids, route_limit, output_tokens, profile,
                        *, group_ids=None, source_sizes=None, contexts=None, dependencies=(), completed_scope=None):
    sources = _index(epoch['manifest']['sources'], 'sources')
    manifest = dict(epoch['manifest'], contexts=contexts or epoch['manifest']['contexts'])
    source_sizes = source_sizes or {}

    def measured(spans, binaries, claims):
        sizes = [span['end'] - span['start'] for span in spans]
        for row in binaries:
            if row['source_id'] not in source_sizes:
                _fail('Native input requires its measured rendered size.', source_id=row['source_id'])
            sizes.append(source_sizes[row['source_id']])
        return joint_fit(sizes, route_limit, output_tokens, profile, claim_count=len(claims))

    tasks, subdivisions = [], [0]
    full_claims = list(manifest['contexts']) if kind == 'read' else claim_ids
    reconciliation_id = _derive_reconciliation_id(epoch, kind, occurrence_ids, full_claims, group_ids, manifest['contexts'])

    def append(spans, binaries, claims, scope, fit):
        if completed_scope is not None and all(
                _covers_inputs(spans, completed_scope.get((occurrence, claim), {}).get('intervals', [])) and
                all(row in completed_scope.get((occurrence, claim), {}).get('binary_sources', []) for row in binaries)
                for occurrence in occurrence_ids for claim in claims):
            return None
        assigned = make_task(epoch, kind, inputs=spans, binary_sources=binaries, claim_ids=claims,
                             occurrence_ids=occurrence_ids, scope=scope, measurement=fit, group_ids=group_ids,
                             contexts=contexts, reconciliation_id=reconciliation_id, dependencies=dependencies)
        tasks.append(assigned)
        return assigned

    def split_sources(spans, binaries, claims):
        fit = measured(spans, binaries, claims)
        if not joint_fit([], route_limit, output_tokens, profile, claim_count=len(claims))['fits']:
            return [append(spans, binaries, claims, 'unresolved', fit)]
        target = profile.get('input_character_target')
        if fit['fits'] and (target is None or sum(s['end'] - s['start'] for s in spans) <= target):
            return [append(spans, binaries, claims, 'original_segment', fit)]
        if subdivisions[0] >= epoch['budgets']['max_subdivisions']:
            return [append(spans, binaries, claims, 'unresolved', fit)]
        subdivisions[0] += 1
        if len(spans) + len(binaries) > 1:
            # Split the largest work dimension without dropping any original.
            pieces = [('text', row) for row in spans] + [('binary', row) for row in binaries]
            middle = len(pieces) // 2
            result = []
            for subset in (pieces[:middle], pieces[middle:]):
                result.extend(split_sources([row for typ, row in subset if typ == 'text'],
                                            [row for typ, row in subset if typ == 'binary'], claims))
            return result
        if spans and spans[0]['end'] - spans[0]['start'] > 1:
            span = spans[0]
            middle = (span['start'] + span['end']) // 2
            return (split_sources([source_span(sources[span['source_id']], span['start'], middle)], [], claims) +
                    split_sources([source_span(sources[span['source_id']], middle, span['end'])], [], claims))
        return [append(spans, binaries, claims, 'unresolved', fit)]

    def split_claims(claims):
        spans, binaries = _collect_original_inputs(manifest, occurrence_ids, claims)
        fit = measured(spans, binaries, claims)
        target = profile.get('input_character_target') if kind == 'read' else None
        if fit['fits'] and (target is None or sum(s['end'] - s['start'] for s in spans) <= target):
            append(spans, binaries, claims, 'joint_original', fit)
        elif len(claims) > 1:
            if subdivisions[0] >= epoch['budgets']['max_subdivisions']:
                append(spans, binaries, claims, 'unresolved', fit)
                return
            subdivisions[0] += 1
            middle = len(claims) // 2
            split_claims(claims[:middle])
            split_claims(claims[middle:])
        else:
            split_sources(spans, binaries, claims)
    split_claims(claim_ids)
    if len(tasks) > 1 or any(row['scope'] != 'joint_original' for row in tasks) or claim_ids != full_claims or dependencies:
        segments = list(dependencies) + [row['id'] for row in tasks]
        tasks.append(make_task(epoch, 'integrate', claim_ids=full_claims, occurrence_ids=occurrence_ids,
                     dependencies=segments, segment_task_ids=segments, scope='segmented_original',
                     group_ids=group_ids, contexts=contexts, reconciliation_id=reconciliation_id))
    return tasks


def _plan_card_tasks(epoch, cards, route_limit, output_tokens, profile, contexts=None, findings=()):
    _index(cards, 'cards')
    for card in cards:
        _check_number(card['characters'], 'card.characters', integer=True)
    blocks, tasks, exceptions, subdivisions = [], [], 0, [0]
    claim_ids = list(contexts if contexts is not None else epoch['manifest']['contexts'])
    current_contexts = contexts or epoch['manifest']['contexts']
    claim_inputs = []
    for key in claim_ids:
        for span in current_contexts[key]['spans']:
            if span not in claim_inputs:
                claim_inputs.append(copy.deepcopy(span))

    def envelope(**kwargs):
        # The measured context contribution includes these exact claims and their
        # required original closure, along with continuity and condition indexes.
        return make_task(epoch, 'combine', inputs=claim_inputs, claim_ids=claim_ids, contexts=contexts, **kwargs)

    def fit(rows):
        return joint_fit([row['characters'] for row in rows], route_limit, output_tokens, profile,
                         claim_count=sum(row.get('claim_count', 0) for row in rows))

    def joint(rows):
        tasks.append(envelope(card_ids=[row['id'] for row in rows], scope='joint_card', measurement=fit(rows)))

    def segmented(rows):
        nonlocal exceptions
        exceptions += 1
        segments = []
        for row in rows:
            pending = [(0, row['characters'])]
            while pending:
                start, end = pending.pop(0)
                measured = joint_fit([end - start], route_limit, output_tokens, profile,
                                     claim_count=row.get('claim_count', 0))
                minimal_fit = joint_fit([], route_limit, output_tokens, profile,
                                        claim_count=row.get('claim_count', 0))['fits']
                if (not measured['fits'] and minimal_fit and end - start > 1 and
                        subdivisions[0] < epoch['budgets']['max_subdivisions']):
                    subdivisions[0] += 1
                    middle = (start + end) // 2
                    pending[0:0] = [(start, middle), (middle, end)]
                    continue
                span = (card_span(row['record'], start, end, manifest=epoch['manifest'], findings=findings)
                        if 'record' in row else dict(card_id=row['id'], start=start, end=end, characters=row['characters']))
                assigned = envelope(card_ids=[row['id']], card_segments=[span],
                                     scope='card_segment' if measured['fits'] else 'unresolved', measurement=measured)
                segments.append(assigned)
                tasks.append(assigned)
        tasks.append(envelope(card_ids=[row['id'] for row in rows],
                     dependencies=[row['id'] for row in segments], segment_task_ids=[row['id'] for row in segments],
                     scope='segmented_card'))

    if not cards:
        return tasks, blocks, exceptions
    if fit(cards)['fits']:
        joint(cards)
        blocks = [[row['id'] for row in cards]]
    else:
        residual = route_limit - profile['context_characters'] - profile['envelope_characters']
        ordinary = [row for row in cards if row['characters'] * 2 <= residual and fit([row])['fits']]
        heavy = [row for row in cards if row not in ordinary]
        packed = []
        for row in ordinary:
            if packed and (sum(x['characters'] for x in packed[-1]) + row['characters']) * 2 <= residual:
                candidate = packed[-1] + [row]
                if fit(candidate)['fits']:
                    packed[-1].append(row)
                    continue
            packed.append([row])
        blocks = [[row['id'] for row in block] for block in packed]
        if len(packed) == 1:
            joint(packed[0])
        for left, right in itertools.combinations(packed, 2):
            if fit(left + right)['fits']:
                joint(left + right)
            else:
                segmented(left + right)
        for left, right in itertools.combinations(cards, 2):
            if left in heavy or right in heavy:
                if fit([left, right])['fits']:
                    joint([left, right])
                else:
                    segmented([left, right])
        if len(cards) == 1:
            segmented(cards)
    return tasks, blocks, exceptions


def plan_tasks(epoch, *, route_limit, output_tokens, profiles, cards=None, selected_groups=(), source_sizes=None,
               contexts=None, tasks=(), selection=None, composition_plan=None, findings=()):
    """Plan exact two-dimensional original work and measured card presentation."""
    _validate_epoch(epoch)
    previous_tasks = _index(list(tasks), 'tasks')
    accepted_cards, accepted_cells, completed_scope = [], {}, {}
    if selection is not None:
        work = dict(epoch=epoch, selection=selection, contexts=contexts, manifest=epoch['manifest'],
                    task_index=previous_tasks, raw=_index(selection['observations'], 'observations'), issues=[])
        _expand_completion_contexts(work)
        if work['current_contexts'] != epoch['manifest']['contexts']:
            contexts = work['current_contexts']
        _check_completion_observations(work)
        _collect_completion_records(work)
        _check_completion_cards(work)
        accepted_cards = work['cards']
        candidates = [(cell, work['owners'][('cards', card['id'])]) for card in accepted_cards for cell in card['judgments']]
        candidates.extend((cell, work['owners'][('cells', cell['id'])]) for cell in work['grouped'].get('cells', {}).values())
        for cell, owner in candidates:
            try:
                assigned = previous_tasks[owner['task_id']]
                if assigned['type'] != 'read':
                    continue
                if assigned['scope'] == 'original_segment':
                    validate_cell(cell, work['manifest'])
                    if cell.get('reconciliation_id') != assigned.get('reconciliation_id') or not _covers_inputs(cell['grounds'], owner['actual_input']['intervals']):
                        continue
                else:
                    validate_cell(cell, work['manifest'], actual_input=owner['actual_input'])
                pair = (cell['occurrence_id'], cell['claim_id'])
                scope = completed_scope.setdefault(pair, dict(intervals=[], binary_sources=[], tasks=[]))
                scope['intervals'].extend(owner['actual_input']['intervals'])
                scope['binary_sources'].extend(owner['actual_input']['binary_sources'])
                if owner['task_id'] not in scope['tasks']:
                    scope['tasks'].append(owner['task_id'])
                required, binaries = _collect_original_inputs(work['manifest'], [pair[0]], [pair[1]])
                if _covers_inputs(required, scope['intervals']) and all(row in scope['binary_sources'] for row in binaries):
                    accepted_cells[pair] = cell
            except ResearchError:
                pass
    tasks, blocks, exceptional = [], [], 0
    if 'read' in profiles:
        claim_ids = list(contexts if contexts is not None else epoch['manifest']['contexts'])
        for occurrence in epoch['manifest']['occurrences']:
            pending = [key for key in claim_ids if (occurrence['id'], key) not in accepted_cells]
            dependencies = sorted({key for pair, scope in completed_scope.items() if pair[0] == occurrence['id']
                                   for key in scope['tasks']})
            if pending:
                tasks.extend(_partition_original(epoch, 'read', [occurrence['id']], pending,
                             route_limit, output_tokens, profiles['read'], source_sizes=source_sizes,
                             contexts=contexts, dependencies=dependencies, completed_scope=completed_scope))
            elif not any(card['kind'] == 'full' and card['occurrence_id'] == occurrence['id'] for card in accepted_cards):
                reconciliation_id = _derive_reconciliation_id(epoch, 'read', [occurrence['id']], claim_ids, None,
                                                              contexts or epoch['manifest']['contexts'])
                tasks.append(make_task(epoch, 'integrate', claim_ids=claim_ids, occurrence_ids=[occurrence['id']],
                             dependencies=dependencies, segment_task_ids=dependencies, scope='segmented_original',
                             reconciliation_id=reconciliation_id, contexts=contexts))
    if cards is not None:
        if 'combine' not in profiles:
            _fail('Combination planning requires its measured profile.')
        planned, blocks, exceptional = _plan_card_tasks(epoch, cards, route_limit, output_tokens, profiles['combine'], contexts, findings)
        tasks.extend(planned)
    groups = {}
    for group in selected_groups:
        validate_selected_group(group, dict(epoch['manifest'], contexts=contexts or epoch['manifest']['contexts']))
        key = digest([sorted(group['occurrence_ids']), sorted(group['claim_ids']), group['question'],
                      {key: (contexts or epoch['manifest']['contexts'])[key]['root'] for key in group['claim_ids']}])
        if key not in groups:
            groups[key] = copy.deepcopy(group)
            groups[key]['group_ids'] = []
        groups[key]['group_ids'].append(group['id'])
    for group in groups.values():
        if 'original_check' not in profiles:
            _fail('Original-check planning requires its measured profile.')
        tasks.extend(_partition_original(epoch, 'original_check', group['occurrence_ids'], group['claim_ids'],
                     route_limit, output_tokens, profiles['original_check'],
                     group_ids=group['group_ids'], source_sizes=source_sizes, contexts=contexts))
    if composition_plan is not None:
        tasks.extend(_plan_composition_tasks(epoch, composition_plan, work['observed'] if selection is not None else ()))
    # Identical segment work can be shared without dropping pair/group ownership.
    tasks = list(_index(tasks, 'tasks').values())
    supersessions = derive_task_supersessions(list(previous_tasks.values()), tasks,
        contexts=contexts if contexts is not None else epoch['manifest']['contexts'])
    remapped = {}
    bound = []
    for assigned in tasks:
        supersedes = sorted(row['task_id'] for row in supersessions if assigned['id'] in row['replacement_task_ids'])
        dependencies = [remapped.get(key, key) for key in assigned['dependencies']]
        segments = [remapped.get(key, key) for key in assigned['segment_task_ids']]
        if supersedes or dependencies != assigned['dependencies'] or segments != assigned['segment_task_ids']:
            body = {key:copy.deepcopy(value) for key,value in assigned.items() if key not in ('id', 'digest')}
            if supersedes:
                body['supersedes'] = supersedes
            body['dependencies'] = dependencies
            body['segment_task_ids'] = segments
            body['id'] = 'task:' + digest(body)
            changed = _seal(body)
            remapped[assigned['id']] = changed['id']
            assigned = changed
        bound.append(assigned)
    tasks = bound
    for row in supersessions:
        row['replacement_task_ids'] = [remapped.get(key, key) for key in row['replacement_task_ids']]
    pairs = [list(pair) for pair in itertools.combinations([row['id'] for row in cards or []], 2)]
    counts = dict(combination=sum(t['type'] == 'combine' for t in tasks), exceptional_pairs=exceptional,
                  total=len(tasks), original_check=sum(t['type'] == 'original_check' for t in tasks))
    extra = dict(context_graph=work['context_graph']) if selection is not None else {}
    if supersessions:
        extra['task_supersessions'] = supersessions
    return _seal(dict(protocol=PROTOCOL, epoch_id=epoch['id'], tasks=tasks, blocks=blocks, **extra,
                      card_sizes={row['id']: row['characters'] for row in cards or []}, required_pairs=pairs,
                      projected_calls=counts, exceeds_call_budget=len(tasks) > epoch['budgets']['max_calls'],
                      unresolved_task_ids=[task['id'] for task in tasks if task['scope'] == 'unresolved']))


def _plan_composition_tasks(epoch, composition_plan, observations):
    """Bind page ownership to the accepted grounding frontier and terminal dependencies."""
    from .review_composition import _validate_plan
    _validate_plan(composition_plan)
    if composition_plan['epoch_id'] != epoch['id']:
        _fail('Composition plan belongs to another epoch.')
    if composition_plan['role'] not in (epoch['role'], 'incomplete'):
        _fail('Composition plan role differs from its epoch.')
    answers = []
    grounding_tasks = sorted(row['task_id'] for row in observations if not row['output'].get('pages'))
    for page_id in composition_plan['required_pages']:
        ranges = [row for row in composition_plan['ownership'] if row['page_id'] == page_id]
        answers.append(make_task(epoch, 'answer', answer_ranges=ranges, scope='segmented_card',
                       dependencies=grounding_tasks,
                       composition_role=composition_plan['role'], composition_plan_root=digest(composition_plan)))
    answers.append(make_task(epoch, 'final', dependencies=[row['id'] for row in answers], scope='segmented_card',
                 composition_role=composition_plan['role'], composition_plan_root=digest(composition_plan)))
    return answers


def missing_card_pairs(plan, assessments):
    """Return pairs without complete joint presentation or segmented accounting."""
    covered = set()
    by_id = _index(assessments, 'assessments')
    for row in assessments:
        if row.get('scope') == 'joint_card':
            covered.update(tuple(sorted(pair)) for pair in itertools.combinations(row['card_ids'], 2))
        elif row.get('scope') == 'segmented_card':
            segment_ids = row.get('segment_task_ids', [])
            if not segment_ids or not set(segment_ids) <= set(by_id):
                continue
            complete = True
            for card_id in row['card_ids']:
                ranges = [[part['start'], part['end']] for key in segment_ids
                          for part in by_id[key].get('card_segments', []) if part['card_id'] == card_id]
                if interval_gaps([[0, plan['card_sizes'][card_id]]], ranges):
                    complete = False
            if complete:
                covered.update(tuple(sorted(pair)) for pair in itertools.combinations(row['card_ids'], 2))
    return [pair for pair in plan['required_pairs'] if tuple(sorted(pair)) not in covered]


def validate_selected_group(group, manifest):
    """Validate a selected original combination, retaining every requesting claim."""
    check_fields(group, ('id', 'occurrence_ids', 'claim_ids', 'question', 'requested_by'), ('group_ids',), code='invalid_review_staged')
    check_text(group['id'], 'group.id', code='invalid_review_staged')
    check_text(group['question'], 'question', code='invalid_review_staged')
    for key in ('occurrence_ids', 'claim_ids', 'requested_by'):
        check_ids(group[key], key, code='invalid_review_staged')
        if not group[key]:
            _fail('A selected group requires original scope and requesting identities.')
    if not set(group['occurrence_ids']) <= {row['id'] for row in manifest['occurrences']}:
        _fail('Selected group occurrence is absent.')
    if not set(group['claim_ids']) <= set(manifest['contexts']):
        _fail('Selected group claim is absent.')
    return copy.deepcopy(group)


def _covers_inputs(required, actual):
    for span in required:
        ranges = [[row['start'], row['end']] for row in actual if row['source_id'] == span['source_id']]
        if interval_gaps([[span['start'], span['end']]], ranges):
            return False
    return True


def _ground(grounds, manifest, known_ids=()):
    sources = _index(manifest['sources'], 'sources')
    for ground in _require_array(grounds, 'grounds'):
        if isinstance(ground, str):
            if ground not in known_ids:
                _fail('Ground names an absent conclusion.', ground=ground)
        elif isinstance(ground, dict) and 'source_id' in ground:
            if ground['source_id'] not in sources:
                _fail('Ground names an absent original.')
            materialize_quotation(sources[ground['source_id']], ground)
        else:
            _fail('Expected an exact span or a grounded conclusion identity.')


def validate_finding(finding, manifest, *, known_ids=()):
    """Validate a finding's scientific content, decision classification and grounds."""
    from .review_composition import list_scalar_fields
    check_fields(finding, ('id', 'text', 'adverse', 'classification', 'claim_ids', 'grounds'), code='invalid_review_staged')
    for key in ('id', 'text'):
        check_text(finding[key], key, code='invalid_review_staged')
    if type(finding['adverse']) is not bool:
        _fail('Finding adversity must be boolean.')
    classification = finding['classification']
    if classification is not None:
        check_fields(classification, ('decisions', 'reason'), code='invalid_review_staged')
        check_text(classification['reason'], 'classification.reason', code='invalid_review_staged')
        decisions = check_ids(classification['decisions'], 'classification.decisions', code='invalid_review_staged')
        if not decisions or not set(decisions) <= set(list_scalar_fields('result')) | set(list_scalar_fields('adjudicator')):
            _fail('Finding classification must name scientific decision paths.')
    claims = check_ids(finding['claim_ids'], 'claim_ids', code='invalid_review_staged')
    if not set(claims) <= set(manifest['contexts']):
        _fail('Finding names an absent claim.')
    if not finding['grounds']:
        _fail('Findings need grounded support.')
    _ground(finding['grounds'], manifest, known_ids)
    return copy.deepcopy(finding)


def _check_evidence_class(record, manifest):
    occurrence = next(row for row in manifest['occurrences'] if row['id'] == record['occurrence_id'])
    if 'evidence_class' in record and (record['evidence_class'] not in ('original_context', 'current') or
                                      record['evidence_class'] != occurrence.get('evidence_class')):
        _fail('Record evidence class differs from its original occurrence.')


def validate_cell(cell, manifest, *, actual_input=None, checks=()):
    """Validate a claim-occurrence judgment and its exact assessment scope."""
    check_fields(cell, ('id', 'occurrence_id', 'claim_id', 'status', 'reason', 'grounds', 'scope', 'context_root'),
             ('source_subject', 'segment_ids', 'reconciliation_id', 'dependencies', 'evidence_class'), code='invalid_review_staged')
    check_text(cell['id'], 'cell.id', code='invalid_review_staged')
    check_text(cell['reason'], 'reason', code='invalid_review_staged')
    check_ids(cell.get('dependencies', []), 'dependencies', code='invalid_review_staged')
    occurrences = _index(manifest['occurrences'], 'occurrences')
    if cell['occurrence_id'] not in occurrences or cell['claim_id'] not in manifest['contexts']:
        _fail('Claim cell lies outside the frozen obligation space.')
    _check_evidence_class(cell, manifest)
    context = manifest['contexts'][cell['claim_id']]
    if cell['context_root'] != context['root']:
        _fail('Claim context changed.', code='review_task_dependency_changed', claim_id=cell['claim_id'])
    if cell['status'] not in ('supported', 'contradicted', 'no_bearing', 'unresolved'):
        _fail('Unknown claim disposition.')
    if cell['status'] == 'no_bearing':
        check_text(cell.get('source_subject'), 'source_subject', code='invalid_review_staged')
    if cell['status'] != 'unresolved' and not cell['grounds']:
        _fail('A substantive judgment requires grounded support.')
    _ground(cell['grounds'], manifest)
    if actual_input is not None and cell['scope'] != 'segmented_original' and not _covers_inputs(cell['grounds'], actual_input['intervals']):
        _fail('Cell cites an undelivered quotation.')
    if cell['scope'] == 'joint_original':
        if actual_input is not None:
            required, binaries = _collect_original_inputs(manifest, [cell['occurrence_id']], [cell['claim_id']])
            if not _covers_inputs(required, actual_input['intervals']):
                _fail('Joint-original judgment lacks complete original co-presence.')
            if any(row not in actual_input['binary_sources'] for row in binaries):
                _fail('Joint-original judgment lacks its native owned body.')
            if cell['claim_id'] not in actual_input['claim_ids']:
                _fail('Joint-original judgment lacks the exact original claim.')
    elif cell['scope'] == 'segmented_original':
        matches = [row for row in checks if row['id'] == cell.get('reconciliation_id') and
                   row.get('scope') == 'segmented_original' and row.get('status') == 'resolved']
        if actual_input is not None and not matches:
            _fail('Segmented judgment lacks complete traversal and reconciliation.')
    elif cell['scope'] != 'unresolved' or cell['status'] != 'unresolved':
        _fail('Unsupported original assessment scope.')
    return copy.deepcopy(cell)


def validate_card(card, manifest, *, claim_ids=None):
    """Validate a grounded fragment or complete logical card."""
    required = ('id', 'occurrence_id', 'kind', 'establishes', 'evidence_type', 'conclusions', 'conditions',
                'judgments', 'critical_spans', 'findings', 'unresolved', 'dependencies', 'fragment_ids')
    check_fields(card, required, ('premises', 'reasoning', 'negative_observation', 'examined_scope',
                               'no_statement_reason', 'reconciliation_id', 'critical_quotations', 'evidence_class'), code='invalid_review_staged')
    for key in ('conclusions', 'conditions', 'judgments'):
        _require_array(card[key], key)
    check_text(card['id'], 'card.id', code='invalid_review_staged')
    check_text(card['establishes'], 'establishes', code='invalid_review_staged')
    if card['kind'] not in ('fragment', 'full'):
        _fail('Unknown card kind.')
    if card['occurrence_id'] not in {row['id'] for row in manifest['occurrences']}:
        _fail('Card occurrence is absent.')
    _check_evidence_class(card, manifest)
    if card['evidence_type'] not in ('established_result', 'conjecture', 'computation', 'assertion',
                                      'reviewer_deduction', 'reviewer_observation'):
        _fail('Unknown evidence type.')
    if card['evidence_type'] == 'reviewer_deduction':
        if not card.get('premises'):
            _fail('Reviewer deductions need grounded premises.')
        _ground(card['premises'], manifest)
        check_text(card.get('reasoning'), 'reasoning', code='invalid_review_staged')
    if card.get('negative_observation'):
        if card['evidence_type'] != 'reviewer_observation':
            _fail('An absence observation is not a quotation proving absence.')
        check_text(card.get('examined_scope'), 'examined_scope', code='invalid_review_staged')
    if not card['critical_spans']:
        check_text(card.get('no_statement_reason'), 'no_statement_reason', code='invalid_review_staged')
    _ground(card['critical_spans'], manifest)
    if 'critical_quotations' in card:
        sources = _index(manifest['sources'], 'sources')
        exact = [materialize_quotation(sources[span['source_id']], span) for span in card['critical_spans']]
        if card['critical_quotations'] != exact:
            _fail('Retained critical quotations differ from their exact original characters.')
    record_ids = set(_index(card['conclusions'] + card['conditions'] + card['judgments'], 'card records'))
    for row in card['conclusions'] + card['conditions']:
        check_fields(row, ('id', 'text', 'grounds'), ('dependencies', 'satisfied'), code='invalid_review_staged')
        check_text(row['id'], 'conclusion.id', code='invalid_review_staged')
        check_text(row['text'], 'text', code='invalid_review_staged')
        check_ids(row.get('dependencies', []), 'dependencies', code='invalid_review_staged')
        if 'satisfied' in row and type(row['satisfied']) is not bool:
            _fail('Condition satisfaction must be explicit boolean data.')
        _ground(row['grounds'], manifest, record_ids)
    for cell in card['judgments']:
        validate_cell(cell, manifest)
        if cell['occurrence_id'] != card['occurrence_id']:
            _fail('A card cannot assume another occurrence judgment.')
    if claim_ids is not None and (len(card['judgments']) != len(claim_ids) or
                                  {cell['claim_id'] for cell in card['judgments']} != set(claim_ids)):
        _fail('A card must contain one complete judgment per assigned claim.')
    for key in ('findings', 'dependencies', 'fragment_ids'):
        check_ids(card[key], key, code='invalid_review_staged')
    for question in _require_array(card['unresolved'], 'unresolved'):
        check_fields(question, ('text', 'claim_ids', 'needs'), code='invalid_review_staged')
        check_text(question['text'], 'question.text', code='invalid_review_staged')
        check_text(question['needs'], 'question.needs', code='invalid_review_staged')
        claims = check_ids(question['claim_ids'], 'question.claim_ids', code='invalid_review_staged')
        if not claims or not set(claims) <= set(manifest['contexts']):
            _fail('Card question must name its affected claims.')
    return copy.deepcopy(card)


def identify_card_question(card, question):
    """Bind a question obligation to its exact originating card and content."""
    return 'card-unresolved:' + digest([card['id'], question])


def card_obligations(cards):
    """Return every full-card conclusion, condition, judgment and question identity."""
    result = []
    for card in cards:
        if card['kind'] == 'full':
            for row in card['conclusions'] + card['conditions'] + card['judgments']:
                if row['id'] in result:
                    _fail('Duplicate full-card obligation identity.', id=row['id'])
                result.append(row['id'])
            for question in card['unresolved']:
                key = identify_card_question(card, question)
                if key in result:
                    _fail('Duplicate full-card obligation identity.', id=key)
                result.append(key)
    return result


def compact_card(card, *, manifest=None, findings=()):
    """Project observed content deterministically, retaining full-card accounting."""
    quotations = card.get('critical_quotations')
    if manifest is not None:
        sources = _index(manifest['sources'], 'sources')
        quotations = [materialize_quotation(sources[span['source_id']], span) for span in card['critical_spans']]
    if quotations is None:
        if card['critical_spans']:
            _fail('Compact cards need materialized quotations or their source manifest.')
        quotations = []
    finding_index = _index(list(findings), 'findings')
    if not set(card['findings']) <= set(finding_index):
        _fail('Compact cards need the bodies of every finding they carry.')
    result = dict(id=card['id'], occurrence_id=card['occurrence_id'], full_card_root=digest(card),
                establishes=card['establishes'], evidence_type=card['evidence_type'],
                conclusions=copy.deepcopy(card['conclusions']), conditions=copy.deepcopy(card['conditions']),
                judgments=[{key: copy.deepcopy(row[key]) for key in ('id', 'claim_id', 'status', 'reason', 'scope', 'grounds', 'source_subject', 'evidence_class') if key in row}
                           for row in card['judgments']], critical_spans=copy.deepcopy(card['critical_spans']),
                critical_quotations=copy.deepcopy(quotations),
                findings=card['findings'][:], unresolved=copy.deepcopy(card['unresolved']),
                dependencies=card['dependencies'][:], obligation_ids=card_obligations([card]))
    for key in ('no_statement_reason', 'examined_scope', 'negative_observation', 'evidence_class'):
        if key in card:
            result[key] = copy.deepcopy(card[key])
    if manifest is not None:
        occurrence = next(row for row in manifest['occurrences'] if row['id'] == card['occurrence_id'])
        if 'evidence_class' in occurrence:
            result['evidence_class'] = occurrence['evidence_class']
    if card['findings']:
        result['finding_bodies'] = [copy.deepcopy(finding_index[key]) for key in card['findings']]
    if card['unresolved']:
        result['question_obligations'] = [dict(id=identify_card_question(card, question),
                                             question=copy.deepcopy(question)) for question in card['unresolved']]
    return result


def bind_integration_lists(receipt, *, epoch, task, cards, children=(), observations=()):
    """Prepare deterministic receipt identity lists and their authorization root.

    The packet builder can deliver only record_root for copying. Verification
    recomputes the lists from the frozen dependency observations and full cards.
    """
    child_index = _index(list(children), 'children')
    child_ids = receipt['child_ids']
    if not set(child_ids) <= set(child_index):
        _fail('Integration child is missing.', code='review_composition_pending')
    selected = sorted(row['id'] for row in observations if row['task_id'] in task['dependencies'])
    child_union = sorted({key for child in child_ids for key in child_index[child]['obligation_ids']})
    obligations = task['obligation_ids'] or ([] if child_ids else card_obligations(cards))
    roots = {epoch['manifest']['digest']}
    roots.update(row['raw_output_digest'] for row in observations if row['id'] in selected)
    roots.update(digest(child_index[key]) for key in child_ids)
    roots.update(digest(card) for card in cards if set(card_obligations([card])) & set(obligations))
    values = dict(epoch_id=epoch['id'], task_id=task['id'], context_roots=task['context_roots'],
                  child_ids=child_ids, selected_observations=selected, input_roots=sorted(roots),
                  child_union=child_union, obligation_ids=sorted(set(obligations) | set(child_union)))
    root = digest(['integration-record-v1', values])
    if 'record_root' in receipt and receipt['record_root'] != root:
        _fail('Integration record root differs from its frozen record.')
    for key, value in values.items():
        if key in receipt and receipt[key] != value:
            _fail('Integration copied list differs from its bound record.', field=key)
    return dict(copy.deepcopy(receipt), **copy.deepcopy(values), record_root=root)


def collect_condition_ids(manifest, cards):
    """Retain frame conditions alongside every full-card condition."""
    return sorted({row['id'] for card in cards if card['kind'] == 'full' for row in card['conditions']} |
                  {row['id'] for row in manifest['claims'] if row.get('kind') == 'condition'})


def bind_terminal_conditions(epoch, terminal, *, cards, integration, context_graph=None):
    """Bind visible conditions to the exact integration authorized by a terminal."""
    manifest = bind_context_graph(epoch, context_graph) if context_graph is not None else epoch['manifest']
    visible = collect_condition_ids(manifest, cards)
    root = digest(['terminal-record-v1', dict(epoch_id=epoch['id'], integration_root=digest(integration),
                                             visible_conditions=visible)])
    if terminal.get('record_root', root) != root or terminal.get('visible_conditions', visible) != visible:
        _fail('Terminal record root or visible conditions differ from the authorized record.')
    return dict(copy.deepcopy(terminal), record_root=root, visible_conditions=visible)


def validate_integration(receipt, *, epoch, obligations, children=(), original_checks=(), known_ids=(),
                         findings=None, answers=None):
    """Check exact full-card and child accounting, including revision grounds."""
    required = ('id', 'epoch_id', 'task_id', 'selected_observations', 'input_roots', 'context_roots',
                'child_ids', 'child_union', 'obligation_ids', 'dispositions', 'findings', 'answers',
                'selected_groups', 'retrievals', 'dependencies', 'unresolved', 'decision_constraints')
    check_fields(receipt, required, ('obligation_index_root', 'record_root'), code='invalid_review_staged')
    if receipt['epoch_id'] != epoch['id']:
        _fail('Integration belongs to another epoch.')
    _check_decision_paths(receipt['decision_constraints'], epoch['role'])
    check_fields(receipt['context_roots'], (), None, code='invalid_review_staged')
    for key in ('id', 'task_id'):
        check_text(receipt[key], key, code='invalid_review_staged')
    for key in ('selected_observations', 'input_roots', 'child_ids', 'child_union', 'obligation_ids', 'dependencies',
                'findings', 'answers', 'selected_groups', 'retrievals'):
        check_ids(receipt[key], key, code='invalid_review_staged')
    if findings is not None and not set(receipt['findings']) <= set(findings):
        _fail('Integration findings must name findings carried by the record.')
    if answers is not None and set(receipt['answers']) != set(answers):
        _fail('Integration answers must name the answers returned beside the receipt.')
    child_index = _index(list(children), 'children')
    pending, visited = list(receipt['child_ids']), set()
    while pending:
        key = pending.pop()
        if key == receipt['id']:
            _fail('Integration dependency graph contains a cycle.')
        if key not in visited and key in child_index:
            visited.add(key)
            pending.extend(child_index[key]['child_ids'])
    if not set(receipt['child_ids']) <= set(child_index):
        _fail('Integration child is missing.', code='review_composition_pending', children=receipt['child_ids'])
    exact_union = sorted({key for child_id in receipt['child_ids'] for key in child_index[child_id]['obligation_ids']})
    if sorted(receipt['child_union']) != exact_union:
        _fail('Integration child union does not match its children.')
    if set(receipt['obligation_ids']) != set(obligations) | set(exact_union):
        _fail('Integration omitted a full-card conclusion or condition.',
              missing=sorted((set(obligations) | set(exact_union)) - set(receipt['obligation_ids'])))
    dispositions = _index(receipt['dispositions'], 'dispositions')
    if set(dispositions) != set(receipt['obligation_ids']):
        _fail('Integration dispositions do not exactly cover their obligations.')
    checks = _index(list(original_checks), 'original_checks')
    grounds_ids = set(known_ids) | set(checks) | set(obligations) | set(exact_union)
    for row in receipt['dispositions']:
        check_fields(row, ('id', 'disposition', 'reason', 'grounds'), ('original_check_ids',), code='invalid_review_staged')
        if row['disposition'] not in ('retained', 'revised', 'conflict', 'irrelevant-to-this-claim'):
            _fail('Unknown integration disposition.')
        check_text(row['reason'], 'reason', code='invalid_review_staged')
        if not row['grounds']:
            _fail('Integration disposition needs supporting references.')
        _ground(row['grounds'], epoch['manifest'], grounds_ids)
        if row['disposition'] == 'revised':
            revisions = row.get('original_check_ids', [])
            if not revisions or any(key not in checks or checks[key]['status'] != 'resolved' for key in revisions):
                _fail('A substantive revision requires resolved original checks.')
    for key, root in receipt['context_roots'].items():
        if key not in epoch['manifest']['contexts'] or root != epoch['manifest']['contexts'][key]['root']:
            _fail('Integration context changed.', code='review_task_dependency_changed', claim_id=key)
    return copy.deepcopy(receipt)


def validate_original_check(check, *, epoch, task, observations, groups):
    """Validate original co-presence, provisional segments or reconciliation."""
    check_fields(check, ('id', 'task_id', 'group_ids', 'scope', 'status', 'conclusions', 'qualifications',
                     'conflicts', 'unresolved', 'segment_ids', 'reconciliation'), ('grounds',), code='invalid_review_staged')
    for field in ('id', 'task_id'):
        check_text(check[field], field, code='invalid_review_staged')
    if check['task_id'] != task['id'] or check['scope'] not in ('joint_original', 'segmented_original', 'original_segment', 'unresolved'):
        _fail('Original check has the wrong task or scope.')
    if check['status'] not in ('resolved', 'unresolved'):
        _fail('Unknown original-check status.')
    group_index = _index(list(groups), 'groups')
    check_ids(check['group_ids'], 'group_ids', code='invalid_review_staged')
    if set(check['group_ids']) != set(task['group_ids']) or not set(check['group_ids']) <= set(group_index):
        _fail('Original check omitted its selected group.')
    if not check['group_ids'] and not (task['type'] == 'integrate' and task['occurrence_ids']):
        _fail('Original check needs selected originals.')
    scopes = [group_index[key] for key in check['group_ids']] or [task]
    observation_index = {row['task_id']: row for row in observations}
    if task['id'] not in observation_index:
        _fail('Original check has no selected observation.')
    actual = observation_index[task['id']]['actual_input']
    delivered = actual['intervals']
    binaries = actual['binary_sources']
    if check['scope'] == 'segmented_original':
        segments = check_ids(check['segment_ids'], 'segment_ids', code='invalid_review_staged')
        if not segments or set(segments) != set(task['segment_task_ids']) or not set(segments) <= set(observation_index):
            _fail('Segmented check is missing a child.')
        if task.get('reconciliation_id', check['id']) != check['id']:
            _fail('Reconciliation must use its planner-assigned identity.')
        check_text(check['reconciliation'], 'reconciliation', code='invalid_review_staged')
        delivered = [span for key in segments for span in observation_index[key]['actual_input']['intervals']]
        binaries = [row for key in segments for row in observation_index[key]['actual_input']['binary_sources']]
    elif check['scope'] == 'joint_original' and task['scope'] != 'joint_original':
        _fail('A segment cannot certify joint-original scope.')
    elif check['scope'] in ('original_segment', 'unresolved'):
        if check['status'] != 'unresolved':
            _fail('Provisional scope cannot certify a resolved check.')
        if check['scope'] == 'original_segment' and task['scope'] != 'original_segment':
            _fail('Original segment receipt has the wrong task scope.')
    if check['scope'] in ('joint_original', 'segmented_original'):
        for group in scopes:
            required, owned = _collect_original_inputs(epoch['manifest'], group['occurrence_ids'], group['claim_ids'])
            if not _covers_inputs(required, delivered) or any(row not in binaries for row in owned):
                _fail('Original-check traversal is incomplete.')
    if check['status'] == 'resolved' and (check['unresolved'] or check['conflicts']):
        _fail('Unresolved dependencies remain in a supposedly resolved check.')
    if not check['conclusions'] and check['status'] == 'resolved':
        _fail('Resolved original checks need substantive conclusions.')
    for collection in ('conclusions', 'qualifications', 'conflicts'):
        for row in _require_array(check[collection], collection):
            required = {'conclusions': ('id', 'text', 'grounds'),
                        'qualifications': ('occurrence_id', 'text', 'grounds'),
                        'conflicts': ('occurrence_ids', 'text', 'grounds', 'resolution')}[collection]
            check_fields(row, required, code='invalid_review_staged')
            check_text(row['text'], 'text', code='invalid_review_staged')
            if collection == 'conclusions':
                check_text(row['id'], 'conclusion.id', code='invalid_review_staged')
            else:
                ids = [row['occurrence_id']] if collection == 'qualifications' else check_ids(row['occurrence_ids'], 'occurrence_ids', code='invalid_review_staged')
                if not ids or not set(ids) <= set(task['occurrence_ids']):
                    _fail('Provisional record names originals outside its task.')
                if collection == 'conflicts':
                    check_text(row['resolution'], 'resolution', code='invalid_review_staged')
            if not row['grounds']:
                _fail('Original-check records require exact grounds.')
            _ground(row['grounds'], epoch['manifest'])
            if not _covers_inputs(row['grounds'], delivered):
                _fail('Original-check record cites an undelivered original span.')
    _require_array(check['unresolved'], 'unresolved')
    return copy.deepcopy(check)


def validate_retrieval(receipt, manifest, *, requested_context_roots=None):
    """Validate an exact original reread question and its explicitly closed scope."""
    check_fields(receipt, ('id', 'question', 'inputs', 'claim_ids', 'context_roots', 'status', 'answer',
                       'observation_ids', 'requested_by'), code='invalid_review_staged')
    check_text(receipt['id'], 'retrieval.id', code='invalid_review_staged')
    check_text(receipt['question'], 'question', code='invalid_review_staged')
    if receipt['status'] not in ('pending', 'resolved', 'unresolved'):
        _fail('Unknown retrieval status.')
    for key in ('claim_ids', 'observation_ids'):
        check_ids(receipt[key], key, code='invalid_review_staged')
    check_text(receipt['requested_by'], 'requested_by', code='invalid_review_staged')
    check_fields(receipt['context_roots'], receipt['claim_ids'], code='invalid_review_staged')
    if not _require_array(receipt['inputs'], 'inputs'):
        _fail('A retrieval needs an explicit original scope.')
    sources = _index(manifest['sources'], 'sources')
    for span in receipt['inputs']:
        if span['source_id'] not in sources:
            _fail('Retrieval names an original outside the epoch.')
        materialize_quotation(sources[span['source_id']], span)
    expected = requested_context_roots if requested_context_roots is not None else {
        key: row['root'] for key, row in manifest['contexts'].items()}
    for key in receipt['claim_ids']:
        if key not in manifest['contexts'] or receipt['context_roots'].get(key) != expected.get(key):
            _fail('Retrieval context changed.', code='review_task_dependency_changed')
    if receipt['status'] == 'resolved':
        check_text(receipt['answer'], 'answer', code='invalid_review_staged')
        if not receipt['observation_ids']:
            _fail('A resolved retrieval requires selected observed rereading.')
    elif receipt['status'] == 'pending' and (receipt['answer'] is not None or receipt['observation_ids']):
        _fail('Pending retrievals cannot claim an observed answer.')
    return copy.deepcopy(receipt)


def collect_retrieval_inputs(receipt, contexts):
    """Keep a pending question's provenance while rereading its expanded closure."""
    inputs = copy.deepcopy(receipt['inputs'])
    for key in receipt['claim_ids']:
        if receipt['context_roots'][key] != contexts[key]['root']:
            inputs.extend(copy.deepcopy(contexts[key]['spans']))
    return inputs


def depends_on_task(observed, task_id, by_task):
    """Require a later delivery's authenticated dependency path to its question."""
    pending, seen = list(observed['dependencies']), set()
    while pending:
        key = pending.pop()
        if key == task_id:
            return True
        if key not in seen:
            seen.add(key)
            pending.extend(by_task.get(key, {}).get('dependencies', {}))
    return False


def card_span(card, start, end, *, manifest=None, findings=()):
    """Bind a segment of the canonical compact card to its complete logical object."""
    compact = compact_card(card, manifest=manifest, findings=findings)
    text = _canonical(compact, code='invalid_review_staged')
    _merge_ranges([[start, end]], len(text))
    return dict(card_id=card['id'], start=start, end=end, card_root=digest(compact),
                sha256=hashlib.sha256(text[start:end].encode('utf-8')).hexdigest())


def validate_task_output(task, output, *, manifest=None, validate_records=True):
    """Validate the task's required output family before assigning any credit."""
    if not isinstance(output, dict):
        _fail('A task output must be an object.')
    if manifest is not None:
        output, unmatched = bind_quotations(output, epoch={'manifest': manifest}, sources=manifest['sources'])
    kind = task['type']
    if kind == 'read' and not (output.get('cards') or output.get('cells')):
        _fail('A reading output requires grounded cards and assigned judgments.')
    if kind == 'frame':
        check_fields(output, ('claims', 'conditions', 'dependencies', 'unresolved'), None, code='invalid_review_staged')
        for key in ('claims', 'conditions', 'dependencies', 'unresolved'):
            _require_array(output[key], key)
        entries = _index(output['claims'] + output['conditions'], 'frame entries')
        if len(entries) != len(output['claims']) + len(output['conditions']):
            _fail('Frame entry identities must be unique.')
        known_ids = set(entries)
        if manifest is not None:
            known_ids.update(manifest['contexts'])
            known_ids.update(row['id'] if isinstance(row, dict) else row for row in manifest['inherited_findings'])
        for collection, allowed in (('claims', ('claim', 'record')), ('conditions', ('condition', 'definition', 'record'))):
            for row in output[collection]:
                check_fields(row, ('id', 'kind', 'spans', 'dependencies', 'children'), code='invalid_review_staged')
                if row['kind'] not in allowed:
                    _fail('Unknown frame entry kind.')
                for field in ('dependencies', 'children'):
                    check_ids(row[field], field, code='invalid_review_staged')
                    if manifest is not None and not set(row[field]) <= known_ids:
                        _fail('Frame entry names an unknown dependency or child.')
                if not _require_array(row['spans'], 'spans'):
                    _fail('A frame entry requires original spans.')
                if manifest is not None:
                    sources = _index(manifest['sources'], 'sources')
                    for span in row['spans']:
                        if not isinstance(span, dict) or span.get('source_id') not in sources:
                            _fail('Frame entry needs exact original spans.')
                        materialize_quotation(sources[span['source_id']], span, allow_empty=True)
                    if not _covers_inputs(row['spans'], task['inputs']):
                        _fail('A frame entry cites an unassigned field.')
        for row in output['dependencies']:
            check_fields(row, ('claim_id', 'depends_on'), code='invalid_review_staged')
            for key in row:
                check_text(row[key], key, code='invalid_review_staged')
            if manifest is not None and not set(row.values()) <= known_ids:
                _fail('Frame dependency names an unknown entry.')
        for row in output['unresolved']:
            check_fields(row, ('text', 'source_ids', 'claim_ids'), code='invalid_review_staged')
            check_text(row['text'], 'text', code='invalid_review_staged')
            for key in ('source_ids', 'claim_ids'):
                check_ids(row[key], key, code='invalid_review_staged')
            if manifest is not None and not set(row['source_ids']) <= {s['id'] for s in manifest['sources']}:
                _fail('Frame unresolved scope names an absent source.')
        if manifest is not None:
            required = [span for span in task['inputs'] if span['source_id'] in {f['source_id'] for f in manifest['frame_fields']}]
            if not _covers_inputs(required, [span for row in entries.values() for span in row['spans']]):
                _fail('Frame decomposition omits assigned original characters.')
    if kind == 'combine':
        if task['scope'] == 'card_segment':
            if not output.get('segment_readings'):
                _fail('A card segment requires an observed reading receipt.')
            readings = _index(output['segment_readings'], 'segment_readings')
            if len(readings) != len(output['segment_readings']):
                _fail('Segment reading identities must be unique.')
            assigned_ranges = {(r['card_id'], r['start'], r['end']) for r in task['card_segments']}
            actual_ranges = []
            for row in readings.values():
                check_fields(row, ('id', 'card_id', 'start', 'end', 'reading'), code='invalid_review_staged')
                check_text(row['card_id'], 'card_id', code='invalid_review_staged')
                check_text(row['reading'], 'reading', code='invalid_review_staged')
                _merge_ranges([[row['start'], row['end']]])
                actual_ranges.append((row['card_id'], row['start'], row['end']))
            if len(actual_ranges) != len(assigned_ranges) or set(actual_ranges) != assigned_ranges:
                _fail('Segment readings must cover exactly the assigned card ranges.')
        elif not output.get('combinations'):
            _fail('A combination task requires substantive synthesis and selections.')
    if kind == 'original_check' and not output.get('original_checks'):
        _fail('An original-check task requires an explicit assessment receipt.')
    if kind == 'integrate' and not (output.get('integrations') or output.get('original_checks')):
        _fail('An integration task requires reconciliation receipts.')
    if kind == 'answer' and not output.get('pages'):
        _fail('An answer task requires typed answer pages for composition verification.')
    if kind == 'final' and not any(key in output for key in ('terminal', 'assessment_incomplete', 'retrievals')):
        _fail('A final task requires a terminal choice, retrieval or incomplete assessment.')
    if (kind == 'final' and 'objection_id' in output and manifest is not None
            and isinstance(manifest['frame'], dict) and manifest['frame'].get('context_repair') is not None
            and 'reassessment' not in output):
        _fail('The final adjudication must reassess the retained historical findings.', code='review_reassessment_missing')
    if kind == 'final' and 'assessment_incomplete' in output:
        check_fields(output['assessment_incomplete'], ('unresolved_scope', 'recovery_work'), code='invalid_review_staged')
        validate_incomplete_scope(**output['assessment_incomplete'])
        if 'terminal' in output or any(key in output for key in ('stage', 'disposition', 'value')):
            _fail('An incomplete assessment cannot also choose a scientific result.')
    if manifest is not None and validate_records:
        for card in _require_array(output.get('cards', []), 'cards'):
            validate_card(card, manifest, claim_ids=task['claim_ids'])
        for cell in _require_array(output.get('cells', []), 'cells'):
            validate_cell(cell, manifest)
        for finding in _require_array(output.get('findings', []), 'findings'):
            validate_finding(finding, manifest)
    return copy.deepcopy(output)


def _validate_observation(epoch, task, observation, contexts, *, manifest=None):
    _verify_seal(task)
    check_fields(observation, ('id', 'epoch_id', 'epoch_digest', 'task_id', 'task_digest', 'actual_input',
                           'request_digest', 'output', 'raw_output_digest', 'dependencies'), code='invalid_review_staged')
    check_text(observation['id'], 'observation.id', code='invalid_review_staged')
    if (observation['epoch_id'] != epoch['id'] or observation['epoch_digest'] != epoch['digest'] or
            task['epoch_id'] != epoch['id'] or task['epoch_digest'] != epoch['digest'] or
            observation['task_id'] != task['id'] or observation['task_digest'] != task['digest']):
        _fail('Selected observation does not bind its exact frozen task.')
    if not task['planned_request_digest'] or observation['request_digest'] != task['planned_request_digest']:
        _fail('Selected observation does not bind its planned rendered request.')
    if observation['raw_output_digest'] != digest(observation['output']):
        _fail('Selected output digest is not its exact observed output.')
    actual = observation['actual_input']
    check_fields(actual, ('history', 'intervals', 'binary_sources', 'claim_ids', 'context_roots', 'complete_cards'),
             ('complete_card_roots', 'full_card_roots', 'request_measurement', 'card_segments', 'material_objections'), code='invalid_review_staged')
    if actual['history'] != []:
        _fail('Staged requests require empty history.')
    sources = _index(epoch['manifest']['sources'], 'sources')
    for span in actual['intervals']:
        if span['source_id'] not in sources:
            _fail('Actual input names a source outside the epoch.')
        materialize_quotation(sources[span['source_id']], span, allow_empty=True)
    for row in actual['binary_sources']:
        check_fields(row, ('source_id', 'descriptor'), code='invalid_review_staged')
        source = sources.get(row['source_id'])
        if source is None or source['kind'] != 'binary' or row['descriptor'] != source['descriptor']:
            _fail('Native binary identity does not match the original.')
    if not _covers_inputs(task['inputs'], actual['intervals']):
        _fail('Fitting omitted mandatory task intervals.')
    if any(row not in actual['binary_sources'] for row in task['binary_sources']):
        _fail('Fitting omitted a mandatory native body.')
    if not set(task['claim_ids']) <= set(actual['claim_ids']):
        _fail('Fitting omitted an assigned original claim.')
    for key, root in task['context_roots'].items():
        if key not in contexts or root != contexts[key]['root'] or actual['context_roots'].get(key) != root:
            _fail('Task original closure changed.', code='review_task_dependency_changed', claim_id=key)
        if task['scope'] not in ('original_segment', 'segmented_original', 'unresolved') and \
                not _covers_inputs(contexts[key]['spans'], actual['intervals']):
            _fail('Actual input omitted exact required original claim context.')
    for segment in task['card_segments']:
        received = [row for row in actual.get('card_segments', []) if row['card_id'] == segment['card_id']]
        if not received or interval_gaps([[segment['start'], segment['end']]],
                                         [[row['start'], row['end']] for row in received]):
            _fail('Fitting omitted mandatory card segments.')
    actual_objections = dict(material_objections=actual.get('material_objections', []))
    for objection in actual_objections['material_objections']:
        for field in ('version_evidence', 'version_source_mappings'):
            for entry in objection.get(field, []):
                check_fields(entry, (), None, code='review_objection_evidence_invalid')
            resolved_objection_entries(actual_objections, objection, field)
    for requirement in task['objection_entries']:
        if not _has_objection_entry(actual_objections, requirement):
            _fail('Actual input omitted exact objection evidence.', code='review_objection_evidence_invalid')
    if task['scope'] == 'unresolved':
        _fail('Task work was not admitted against measured resource limits.')
    validate_measurement(task['measurement'])
    manifest = dict(manifest or epoch['manifest'], contexts=contexts)
    output = validate_task_output(task, observation['output'], manifest=manifest, validate_records=False)
    if task['type'] == 'read':
        valid = False
        for family, validator in (('cards', validate_card), ('cells', validate_cell)):
            for record in _require_array(output.get(family, []), family):
                try:
                    if family == 'cards':
                        validator(record, manifest, claim_ids=task['claim_ids'])
                    else:
                        validator(record, manifest)
                    valid = True
                except ResearchError:
                    pass
        if not valid:
            _fail('Reading output contains no independently valid record.')
    return dict(observation, output=output)


def _has_objection_entry(packet, requirement):
    for objection in packet.get('material_objections', []):
        if objection['id'] == requirement['objection_id']:
            entries = objection.get(requirement['field'], [])
            if len(entries) > requirement['index']:
                return digest(resolve_objection_entry(packet, entries[requirement['index']])) == requirement['digest']
    return False


def _record_completion_issue(work, code, **details):
    row = dict(code=code, **details)
    if row not in work['issues']:
        work['issues'].append(row)


def _record_completion_rejection(work, error, **details):
    _record_completion_issue(work, error.code, reason=error.message, **dict(error.details or {}, **details))


def bind_context_graph(epoch, graph):
    """Project a sealed current graph onto frozen originals for planning and packets.

    The caller authenticates the selected observations, as for verify_completion.
    Persist this graph alongside its observations; it never replaces the epoch.
    """
    _validate_epoch(epoch)
    _verify_seal(graph)
    if (graph['schema'] != CONTEXT_GRAPH_SCHEMA or graph['epoch_id'] != epoch['id'] or
            graph['epoch_digest'] != epoch['digest']):
        _fail('Context graph belongs to another frozen epoch.')
    manifest = copy.deepcopy(epoch['manifest'])
    for key in ('claims', 'contexts', 'assurance_dependencies'):
        manifest[key] = copy.deepcopy(graph[key])
    return manifest


def derive_context_graph(epoch, selection, *, tasks=(), contexts=None):
    """Reconstruct the version history from an explicit authenticated selection.

    ``root`` names the current claims and original contexts; ``digest`` also
    binds the parent-linked versions and retained frame questions. A question's
    ID is answered by a later dependent observation's grounded ``answers`` entry.
    ``bind_context_graph`` supplies the matching manifest view for packets.
    """
    _validate_epoch(epoch)
    work = dict(epoch=epoch, selection=selection, contexts=contexts, manifest=epoch['manifest'],
                task_index=_index(list(tasks), 'tasks'),
                raw=_index(selection['observations'], 'observations'), issues=[])
    _expand_completion_contexts(work)
    return copy.deepcopy(work['context_graph'])


def _build_context_version(claims, contexts, *, parent=None, observation_id=None):
    return dict(root=digest(['context-version-v1', claims, contexts]), parent=parent,
                observation_id=observation_id, claims=copy.deepcopy(claims), contexts=copy.deepcopy(contexts))


def _expand_completion_contexts(work):
    epoch, original = work['epoch'], work['manifest']
    manifest = copy.deepcopy(original)
    supplied_contexts = work['contexts']
    versions = [_build_context_version(original['claims'], original['contexts'])]
    pending = sorted(work['raw'].values(), key=lambda row: row['id'])
    verified, unresolved = {}, {}
    while pending:
        remaining = []
        for raw in pending:
            assigned = work['task_index'].get(raw.get('task_id'))
            if assigned is None:
                continue
            expected = {key: verified[key]['raw_output_digest'] for key in assigned['dependencies'] if key in verified}
            if set(expected) != set(assigned['dependencies']):
                remaining.append(raw)
                continue
            snapshot = next((version for version in reversed(versions) if all(
                key in version['contexts'] and version['contexts'][key]['root'] == root
                for key, root in assigned['context_roots'].items())), None)
            if snapshot is None:
                remaining.append(raw)
                continue
            try:
                if raw['dependencies'] != expected:
                    _fail('Discovery does not bind its selected dependency frontier.')
                row = _validate_observation(epoch, assigned, raw, snapshot['contexts'],
                    manifest=dict(original, claims=snapshot['claims'], contexts=snapshot['contexts']))
                output = row['output']
                claims = copy.deepcopy(manifest['claims'])
                index = _index(claims, 'claims')
                entries = output['claims'] + output['conditions'] if assigned['type'] == 'frame' else []
                for entry in entries:
                    prior = index.get(entry['id'])
                    if prior is None:
                        claims.append(copy.deepcopy(entry))
                        index[entry['id']] = claims[-1]
                    else:
                        if prior['spans'] != entry['spans'] or ('kind' in prior and prior['kind'] != entry['kind']):
                            _fail('Frame decomposition changes an existing claim identity.', claim_id=entry['id'])
                        if entry['kind'] != 'claim':
                            prior['kind'] = entry['kind']
                        for field in ('dependencies', 'children'):
                            prior[field] = sorted(set(prior[field]) | set(entry[field]))
                # Original field seeds remain required and acquire the reviewer
                # decomposition that interprets the same original characters.
                for seed in original['claims']:
                    for entry in entries:
                        if entry['id'] != seed['id'] and any(
                            left['source_id'] == right['source_id'] and
                            (max(left['start'], right['start']) < min(left['end'], right['end']) or
                             left['start'] == left['end'] == right['start'] == right['end'])
                            for left in seed['spans'] for right in entry['spans']):
                            index[seed['id']]['children'] = sorted(set(index[seed['id']]['children']) | {entry['id']})
                dependencies = _require_array(output.get('new_dependencies', []), 'new_dependencies')
                if assigned['type'] == 'frame':
                    dependencies = dependencies + output['dependencies']
                for dependency in dependencies:
                    check_fields(dependency, ('claim_id', 'depends_on'), code='invalid_review_staged')
                    key, target = dependency['claim_id'], dependency['depends_on']
                    if key not in index or target not in index:
                        _fail('Discovered dependency names an absent claim.', code='review_task_dependency_changed')
                    index[key]['dependencies'] = sorted(set(index[key]['dependencies']) | {target})
                if claims != manifest['claims']:
                    current = build_contexts(claims, original['sources'])
                    versions.append(_build_context_version(claims, current,
                        parent=versions[-1]['root'], observation_id=row['id']))
                    manifest.update(claims=claims, contexts=current)
                if assigned['type'] == 'frame':
                    for obligation in output['unresolved']:
                        key = 'frame-unresolved:' + digest([assigned['id'], obligation])
                        unresolved[key] = dict(copy.deepcopy(obligation), id=key,
                                              task_id=assigned['id'], observation_id=row['id'])
                verified[assigned['id']] = row
            except ResearchError as error:
                _record_completion_rejection(work, error, observation_id=raw['id'])
        if len(remaining) == len(pending):
            # Retain the existing explicit-context interface. Prefer replaying
            # discoveries so passing the current projection creates no extra version.
            if supplied_contexts is not None and not any(version['contexts'] == supplied_contexts for version in versions):
                versions.append(_build_context_version(manifest['claims'], supplied_contexts,
                    parent=versions[-1]['root']))
                manifest['contexts'] = copy.deepcopy(supplied_contexts)
                pending = remaining
                continue
            break
        pending = remaining
    if supplied_contexts is not None and manifest['contexts'] == original['contexts'] and supplied_contexts != manifest['contexts']:
        versions.append(_build_context_version(manifest['claims'], supplied_contexts, parent=versions[-1]['root']))
        manifest['contexts'] = copy.deepcopy(supplied_contexts)
    for kind in ASSURANCES:
        seeds = original['assurance_dependencies'][kind]
        manifest['assurance_dependencies'][kind] = sorted({key for seed in seeds
            for key in manifest['contexts'][seed]['claim_ids']})
    work['context_graph'] = _seal(dict(schema=CONTEXT_GRAPH_SCHEMA, epoch_id=epoch['id'],
        epoch_digest=epoch['digest'], root=versions[-1]['root'], versions=versions,
        claims=manifest['claims'], contexts=manifest['contexts'],
        assurance_dependencies=manifest['assurance_dependencies'], unresolved=list(unresolved.values())))
    work['manifest'] = manifest
    work['current_contexts'] = manifest['contexts']
    work['current_epoch'] = dict(epoch, manifest=manifest)


def _check_completion_observations(work):
    raw = work['raw']
    task_index = work['task_index']
    epoch = work['epoch']
    current_contexts = work['current_contexts']
    historical = {key for key, task in task_index.items() if any(
        claim_id in current_contexts and root != current_contexts[claim_id]['root']
        for claim_id, root in task['context_roots'].items())}
    accepted, by_task = {}, {}
    for row in raw.values():
        if row.get('task_id') not in task_index:
            _record_completion_issue(work, 'review_task_pending', observation_id=row['id'], reason='No frozen task envelope.')
            continue
        assigned = task_index[row['task_id']]
        try:
            observed_contexts = current_contexts
            if assigned['id'] in historical:
                version = next((version for version in work['context_graph']['versions'] if all(
                    key in version['contexts'] and version['contexts'][key]['root'] == root
                    for key, root in assigned['context_roots'].items())), None)
                if version is None:
                    _fail('Historical task has no recorded context version.', code='review_task_dependency_changed')
                observed_contexts = version['contexts']
            row = _validate_observation(epoch, assigned, row, observed_contexts, manifest=work['manifest'])
            if row['task_id'] in by_task:
                _fail('Selection contains multiple outputs for one task.')
            accepted[row['id']] = row
            by_task[row['task_id']] = row
        except ResearchError as error:
            _record_completion_rejection(work, error, task_id=assigned['id'])
    # Dependency pruning is monotone and terminates after at most |selection| removals.
    changed = True
    while changed:
        changed = False
        for key, row in list(accepted.items()):
            assigned = task_index[row['task_id']]
            expected = {dep: by_task[dep]['raw_output_digest'] for dep in assigned['dependencies'] if dep in by_task}
            if set(expected) != set(assigned['dependencies']) or row['dependencies'] != expected:
                _record_completion_issue(work, 'review_task_dependency_changed', task_id=assigned['id'], dependencies=assigned['dependencies'])
                del accepted[key]
                del by_task[row['task_id']]
                changed = True
    current_by_task = {key:row for key,row in by_task.items() if key not in historical}
    supersessions = derive_task_supersessions(list(task_index.values()),
        [task_index[key] for key in current_by_task], contexts=current_contexts)
    superseded = {row['task_id'] for row in supersessions}
    for assigned in task_index.values():
        if assigned['id'] in historical:
            if assigned['type'] == 'frame' and assigned['id'] in by_task:
                supersessions.append(dict(task_id=assigned['id'], replacement_task_ids=[],
                    context_root=work['context_graph']['root'], reason='frame_decomposition_adopted'))
            elif assigned['id'] not in superseded and assigned['required']:
                _record_completion_issue(work, 'review_task_dependency_changed', task_id=assigned['id'],
                                         reason='Reassess historical work under the expanded original context.')
        elif assigned['required'] and assigned['id'] not in by_task:
            _record_completion_issue(work, 'review_task_pending', task_id=assigned['id'])
    observed = [accepted[key] for key in sorted(accepted) if accepted[key]['task_id'] not in historical]
    observations_root = digest(['selected-observations-v1', sorted(
        [(row['id'], row['task_id'], row['task_digest'], row['request_digest'], row['raw_output_digest'],
          digest(row['actual_input']), row['dependencies']) for row in accepted.values()])])
    work['accepted'] = {key:row for key,row in accepted.items() if row['task_id'] not in historical}
    work['by_task'] = current_by_task
    work['all_by_task'] = by_task
    work['observed'] = observed
    work['historical_observed'] = [accepted[key] for key in sorted(accepted)
                                   if accepted[key]['task_id'] in historical]
    work['historical_tasks'] = historical
    work['task_supersessions'] = supersessions
    work['observations_root'] = observations_root


def derive_task_supersessions(tasks, replacements, *, contexts):
    """Bind obsolete work to current envelopes covering every original obligation."""
    result = []
    for old in tasks:
        if all(key in contexts and contexts[key]['root'] == root for key, root in old['context_roots'].items()):
            continue
        candidates = [row for row in replacements if row['id'] != old['id'] and row['type'] == old['type'] and
            all(row.get(key) == old.get(key) for key in ('composition_role', 'composition_plan_root')) and
            all(key in contexts and contexts[key]['root'] == root for key, root in row['context_roots'].items())]
        pairs = [(occurrence, claim) for occurrence in old['occurrence_ids'] or [None]
                 for claim in old['claim_ids'] or [None]]
        covered = bool(candidates)
        for occurrence, claim in pairs:
            matching = [row for row in candidates if (claim is None or claim in row['claim_ids']) and
                        (occurrence is None or occurrence in row['occurrence_ids'])]
            if not matching or not _covers_inputs(old['inputs'], [span for row in matching for span in row['inputs']]):
                covered = False
        if not covered:
            continue
        if any(not set(old.get(field, [])) <= {key for row in candidates for key in row.get(field, [])}
               for field in ('card_ids', 'group_ids', 'obligation_ids', 'child_ids')):
            continue
        if any(any(item not in [item for row in candidates for item in row[field]] for item in old[field])
               for field in ('binary_sources', 'answer_ranges', 'objection_entries', 'card_segments')):
            continue
        result.append(dict(task_id=old['id'], replacement_task_ids=sorted(row['id'] for row in candidates),
                           context_roots={key:contexts[key]['root'] for key in old['claim_ids'] if key in contexts},
                           reason='context_expanded'))
    return result


def _collect_completion_records(work):
    observed = work['observed'] + work['historical_observed']
    grouped = {}
    owners = {}
    for row in observed:
        for key in ('cards', 'cells', 'combinations', 'selected_groups', 'original_checks', 'integrations',
                    'findings', 'answers', 'retrievals', 'assurance_checks', 'decision_checks', 'segment_readings'):
            if row['task_id'] in work['historical_tasks'] and key not in ('findings', 'retrievals'):
                continue
            try:
                items = _require_array(row['output'].get(key, []), key)
            except ResearchError as error:
                _record_completion_rejection(work, error, task_id=row['task_id'])
                continue
            for item in items:
                if (row['task_id'] in work['historical_tasks'] and key == 'retrievals'
                        and isinstance(item, dict) and item.get('status') != 'pending'):
                    continue
                try:
                    check_fields(item, ('id',), None, code='invalid_review_staged')
                    check_text(item['id'], 'record.id', code='invalid_review_staged')
                    previous = grouped.setdefault(key, {}).get(item['id'])
                    if previous is not None and previous != item:
                        if key != 'retrievals':
                            _fail('Conflicting selected scientific records.', id=item['id'])
                        stable = ('id', 'question', 'inputs', 'claim_ids', 'context_roots', 'requested_by')
                        if any(previous.get(field) != item.get(field) for field in stable) or \
                                {previous.get('status'), item.get('status')} not in ({'pending', 'resolved'}, {'pending', 'unresolved'}):
                            _fail('Conflicting retrieval lifecycle records.', id=item['id'])
                        if item['status'] == 'pending':
                            continue
                    grouped[key][item['id']] = item
                    owners[(key, item['id'])] = row
                except ResearchError as error:
                    _record_completion_rejection(work, error, task_id=row['task_id'])
    work['grouped'] = grouped
    work['owners'] = owners
    _check_completion_record_identities(work)


def identify_scientific_record(family, record):
    """Bind resolution to a record family, stable ID and exact canonical content."""
    return family, record['id'], digest(record)


def _check_completion_record_identities(work):
    identities, conflicts = {}, set()

    def register(family, record):
        try:
            check_fields(record, ('id',), None, code='invalid_review_staged')
            key = check_text(record['id'], 'record.id', code='invalid_review_staged')
            identity = identify_scientific_record(family, record)
            if key in identities and identities[key] != identity:
                conflicts.add(key)
            else:
                identities[key] = identity
        except ResearchError as error:
            _record_completion_rejection(work, error)

    # Inspect every definition, including nested records hidden by a duplicate
    # parent. Dispositions and answer targets are references, not definitions.
    for observed in work['observed'] + work['historical_observed']:
        for family in work['grouped']:
            if observed['task_id'] in work['historical_tasks'] and family not in ('findings', 'retrievals'):
                continue
            rows = observed['output'].get(family, [])
            if not isinstance(rows, list):
                continue
            for record in rows:
                if family == 'retrievals' and isinstance(record, dict) and isinstance(record.get('id'), str):
                    # A verified pending request and its answer share one lifecycle.
                    selected = work['grouped'][family].get(record.get('id'), record)
                    stable = ('id', 'question', 'inputs', 'claim_ids', 'context_roots', 'requested_by')
                    if (record.get('status') == 'pending' and selected.get('status') in ('resolved', 'unresolved')
                            and all(record.get(key) == selected.get(key) for key in stable)):
                        record = selected
                register(family, record)
                if not isinstance(record, dict):
                    continue
                nested = {'cards': (('conclusions', 'card_conclusions'), ('conditions', 'card_conditions'),
                                    ('judgments', 'cells')),
                          'original_checks': (('conclusions', 'original_check_conclusions'),)}.get(family, ())
                for field, kind in nested:
                    if isinstance(record.get(field), list):
                        for child in record[field]:
                            register(kind, child)
                if family == 'cards' and isinstance(record.get('unresolved'), list):
                    for question in record['unresolved']:
                        if isinstance(question, dict):
                            register('card_questions', dict(question, id=identify_card_question(record, question)))
    for key in sorted(conflicts):
        _record_completion_issue(work, 'invalid_review_staged', id=key,
                                 reason='Conflicting scientific record definitions across the selection.')
    work['record_identities'] = {key: value for key, value in identities.items() if key not in conflicts}


def _check_completion_coverage(work):
    manifest = work['manifest']
    observed = work['observed']
    _index(manifest['sources'], 'sources')
    coverage, source_characters, binary_count = [], 0, 0
    for source in manifest['sources']:
        if source['kind'] == 'binary':
            complete = any(dict(source_id=source['id'], descriptor=source['descriptor']) in row['actual_input']['binary_sources']
                           for row in observed)
            binary_count += int(complete)
            if not complete:
                _record_completion_issue(work, 'review_source_coverage_pending', source_id=source['id'], native_binary=True)
            coverage.append(dict(source_id=source['id'], complete=complete))
        else:
            intervals = [[span['start'], span['end']] for row in observed for span in row['actual_input']['intervals']
                         if span['source_id'] == source['id']]
            gaps = interval_gaps(source['required_intervals'], intervals)
            source_characters += (sum(end - start for start, end in source['required_intervals']) -
                                  sum(end - start for start, end in gaps))
            if gaps:
                _record_completion_issue(work, 'review_source_coverage_pending', source_id=source['id'], intervals=gaps)
            coverage.append(dict(source_id=source['id'], missing_intervals=gaps, complete=not gaps))
    work['coverage'] = coverage
    work['source_characters'] = source_characters
    work['binary_count'] = binary_count


def _check_completion_groups(work):
    grouped = work['grouped']
    manifest = work['manifest']
    groups = []
    for group in grouped.get('selected_groups', {}).values():
        try:
            groups.append(validate_selected_group(group, manifest))
        except ResearchError as error:
            _record_completion_rejection(work, error, group_id=group['id'])
    work['groups'] = groups


def _check_completion_original_checks(work):
    grouped = work['grouped']
    by_task = work['by_task']
    current_epoch = work['current_epoch']
    task_index = work['task_index']
    observed = work['observed']
    groups = work['groups']
    checks = []
    for check in grouped.get('original_checks', {}).values():
        try:
            if check.get('task_id') not in by_task:
                _fail('Original check has no selected task.')
            checks.append(validate_original_check(check, epoch=current_epoch, task=task_index[check['task_id']],
                          observations=observed, groups=groups))
        except ResearchError as error:
            _record_completion_rejection(work, error, check_id=check['id'])
    closed_groups = {key for check in checks if check['status'] == 'resolved' for key in check['group_ids']}
    for group in groups:
        if group['id'] not in closed_groups:
            _record_completion_issue(work, 'review_original_check_pending', group_id=group['id'])
    work['checks'] = checks
    work['closed_groups'] = closed_groups


def _check_completion_cards(work):
    grouped = work['grouped']
    manifest = work['manifest']
    cards = []
    for card in grouped.get('cards', {}).values():
        try:
            owner = work['owners'][('cards', card['id'])]
            assigned = work['task_index'][owner['task_id']]
            if work['record_identities'].get(card['id']) != identify_scientific_record('cards', card):
                _fail('A conflicting card definition cannot supply scientific records.', id=card['id'])
            validate_card(card, manifest, claim_ids=assigned['claim_ids'])
            delivered = owner['actual_input']['intervals'][:]
            if assigned['type'] == 'integrate' and assigned['scope'] == 'segmented_original':
                for fragment in grouped.get('cards', {}).values():
                    fragment_owner = work['owners'][('cards', fragment['id'])]
                    if fragment['id'] in card['fragment_ids'] and fragment_owner['task_id'] in assigned['dependencies']:
                        validate_card(fragment, manifest)
                        spans = _find_quotation_spans(fragment)
                        if not _covers_inputs(spans, fragment_owner['actual_input']['intervals']):
                            _fail('Fragment quotations were not delivered to their reading task.')
                        delivered.extend(spans)
            if not _covers_inputs(_find_quotation_spans(card), delivered):
                _fail('Card cites quotations outside its delivered originals or verified fragments.')
            cards.append(card)
        except ResearchError as error:
            _record_completion_rejection(work, error, card_id=card['id'])
    full_cards = [row for row in cards if row['kind'] == 'full']
    card_index = _index(cards, 'cards')
    work['cards'] = cards
    work['full_cards'] = full_cards
    work['card_index'] = card_index


def _find_quotation_spans(value):
    if isinstance(value, dict):
        if set(value) == {'source_id', 'start', 'end', 'sha256'}:
            return [value]
        return [span for item in value.values() for span in _find_quotation_spans(item)]
    if isinstance(value, list):
        return [span for item in value for span in _find_quotation_spans(item)]
    return []


def _check_completion_cells(work):
    grouped = work['grouped']
    cards = work['cards']
    owners = work['owners']
    manifest = work['manifest']
    checks = work['checks']
    cells = {}
    cell_rows = list(grouped.get('cells', {}).values())
    for card in cards:
        row_owner = owners[('cards', card['id'])]
        for cell in card['judgments']:
            if cell not in cell_rows:
                cell_rows.append(cell)
            owners.setdefault(('cells', cell['id']), row_owner)
    for cell in cell_rows:
        try:
            owner = owners[('cells', cell['id'])]
            assigned = work['task_index'][owner['task_id']]
            provisional = assigned['scope'] == 'original_segment'
            if provisional and cell['scope'] == 'segmented_original':
                if cell.get('reconciliation_id') != assigned.get('reconciliation_id'):
                    _fail('Fragment cell does not use its planned reconciliation identity.')
                validate_cell(cell, manifest)
                if not _covers_inputs(cell['grounds'], owner['actual_input']['intervals']):
                    _fail('Fragment cell cites an undelivered quotation.')
                continue
            validate_cell(cell, manifest, actual_input=owner['actual_input'], checks=checks)
            pair = (cell['claim_id'], cell['occurrence_id'])
            if pair in cells and cells[pair] != cell:
                if cell['scope'] == 'segmented_original' and cell.get('reconciliation_id'):
                    pass
                elif cells[pair]['scope'] == 'segmented_original' and cells[pair].get('reconciliation_id'):
                    continue
                else:
                    _fail('Claim cell has conflicting selected conclusions.')
            cells[pair] = cell
        except ResearchError as error:
            _record_completion_rejection(work, error, cell_id=cell.get('id'))
    work['cells'] = cells


def _check_completion_card_reconciliation(work):
    manifest = work['manifest']
    full_cards = work['full_cards']
    cards = work['cards']
    checks = work['checks']
    cells = work['cells']
    for occurrence in manifest['occurrences']:
        logical = [card for card in full_cards if card['occurrence_id'] == occurrence['id']]
        if len(logical) != 1:
            _record_completion_issue(work, 'review_card_pending', occurrence_id=occurrence['id'])
        else:
            card = logical[0]
            fragments = [fragment['id'] for fragment in cards
                         if fragment['kind'] == 'fragment' and fragment['occurrence_id'] == occurrence['id']]
            if sorted(card['fragment_ids']) != sorted(fragments):
                _record_completion_issue(work, 'review_card_pending', card_id=card['id'], reason='Fragment reconciliation is incomplete.')
            if fragments and not any(check['id'] == card.get('reconciliation_id') and check['status'] == 'resolved'
                                     for check in checks):
                _record_completion_issue(work, 'review_card_pending', card_id=card['id'], reason='Fragment reconciliation has no original check.')
            if {cell['claim_id'] for cell in card['judgments']} != set(manifest['contexts']):
                _record_completion_issue(work, 'review_card_pending', card_id=card['id'], reason='Full card omits a claim judgment.')
        for claim in manifest['claims']:
            pair = (claim['id'], occurrence['id'])
            if pair not in cells:
                _record_completion_issue(work, 'review_claim_cell_pending', claim_id=pair[0], occurrence_id=pair[1])
            elif cells[pair]['status'] == 'unresolved':
                _record_completion_issue(work, 'review_claim_unresolved', claim_id=pair[0], occurrence_id=pair[1])


def _check_completion_combinations(work):
    grouped = work['grouped']
    card_index = work['card_index']
    owners = work['owners']
    task_index = work['task_index']
    manifest = work['manifest']
    by_task = work['by_task']
    groups = work['groups']
    full_cards = work['full_cards']
    findings = list(grouped.get('findings', {}).values())
    covered_pairs, presented_cards = set(), set()
    valid_combinations = []
    for combination in grouped.get('combinations', {}).values():
        try:
            check_fields(combination, ('id', 'task_id', 'card_ids', 'scope', 'synthesis', 'selected_groups',
                                    'segment_task_ids', 'reconciliation'), code='invalid_review_staged')
            check_text(combination['synthesis'], 'synthesis', code='invalid_review_staged')
            ids = check_ids(combination['card_ids'], 'card_ids', code='invalid_review_staged')
            if not ids or not set(ids) <= set(card_index):
                _fail('Combination omitted a complete logical card.')
            owner = owners[('combinations', combination['id'])]
            if owner['task_id'] != combination['task_id']:
                _fail('Combination receipt belongs to another observation.')
            assigned = task_index[owner['task_id']]
            if combination['scope'] != assigned['scope']:
                _fail('Combination receipt changes its declared scope.')
            if combination['scope'] == 'joint_card':
                if not set(ids) <= set(owner['actual_input']['complete_cards']):
                    _fail('Joint-card receipt lacks actual complete card co-presence.')
                roots = owner['actual_input'].get('complete_card_roots', {})
                if any(roots.get(key) != digest(compact_card(card_index[key], manifest=manifest, findings=findings)) for key in ids):
                    _fail('Actual compact-card roots do not bind the complete logical cards.')
            elif combination['scope'] == 'segmented_card':
                check_text(combination['reconciliation'], 'reconciliation', code='invalid_review_staged')
                segments = combination['segment_task_ids']
                if not segments or not set(segments) <= set(by_task):
                    _fail('Segmented-card receipt is missing a child.')
                for key in ids:
                    size = len(_canonical(compact_card(card_index[key], manifest=manifest, findings=findings), code='invalid_review_staged'))
                    portions = []
                    for task_id in segments:
                        for span in by_task[task_id]['actual_input'].get('card_segments', []):
                            if span['card_id'] == key:
                                if span != card_span(card_index[key], span['start'], span['end'], manifest=manifest, findings=findings):
                                    _fail('Actual card segment does not bind its exact complete logical card.')
                                portions.append([span['start'], span['end']])
                    if interval_gaps([[0, size]], portions):
                        _fail('Segmented-card traversal is incomplete.', card_id=key)
            else:
                _fail('A card segment is not a completed combination assessment.')
            if not set(combination['selected_groups']) <= {row['id'] for row in groups}:
                _fail('Combination selection is missing its original-check obligation.')
            covered_pairs.update(tuple(sorted(pair)) for pair in itertools.combinations(ids, 2))
            presented_cards.update(ids)
            valid_combinations.append(combination)
        except ResearchError as error:
            _record_completion_rejection(work, error, combination_id=combination['id'])
    full_ids = [row['id'] for row in full_cards]
    for pair in itertools.combinations(full_ids, 2):
        if tuple(sorted(pair)) not in covered_pairs:
            _record_completion_issue(work, 'review_combination_pending', card_ids=list(pair))
    for key in full_ids:
        if key not in presented_cards:
            _record_completion_issue(work, 'review_combination_pending', card_ids=[key])
    work['covered_pairs'] = covered_pairs
    work['valid_combinations'] = valid_combinations


def _check_completion_retrievals(work):
    grouped = work['grouped']
    manifest = work['manifest']
    accepted = work['accepted']
    checks = work['checks']
    cells = work['cells']
    retrievals = []
    for retrieval in grouped.get('retrievals', {}).values():
        try:
            requester_id = check_text(retrieval.get('requested_by'), 'requested_by', code='invalid_review_staged')
            requesting = work['all_by_task'].get(requester_id)
            if requesting is None or not any(row.get('id') == retrieval['id'] and row.get('status') == 'pending'
                    for row in requesting['output'].get('retrievals', [])):
                _fail('A retrieval needs its selected pending request.')
            validate_retrieval(retrieval, manifest,
                               requested_context_roots=requesting['actual_input']['context_roots'])
            if retrieval['status'] == 'resolved':
                if not set(retrieval['observation_ids']) <= set(accepted):
                    _fail('Retrieval has no selected observed rereading.')
                if any(accepted[key]['task_id'] == retrieval['requested_by'] for key in retrieval['observation_ids']):
                    _fail('A retrieval answer requires a later delivery than its requesting task.')
                if any(not depends_on_task(accepted[key], retrieval['requested_by'], work['all_by_task'])
                       for key in retrieval['observation_ids']):
                    _fail('A retrieval answer requires a delivery depending on its original question.')
                spans = [span for key in retrieval['observation_ids'] for span in accepted[key]['actual_input']['intervals']]
                inputs = collect_retrieval_inputs(retrieval, work['current_contexts'])
                if not _covers_inputs(inputs, spans):
                    _fail('Retrieval did not deliver the requested original scope again.')
                for claim_id in retrieval['claim_ids']:
                    current_spans = [span for key in retrieval['observation_ids']
                        if accepted[key]['actual_input']['context_roots'].get(claim_id) ==
                           work['current_contexts'][claim_id]['root']
                        for span in accepted[key]['actual_input']['intervals']]
                    if not _covers_inputs(inputs, current_spans):
                        _fail('Retrieval did not reassess its current claim context.')
            else:
                _record_completion_issue(work, 'review_retrieval_pending', retrieval_id=retrieval['id'])
            retrievals.append(retrieval)
        except ResearchError as error:
            _record_completion_rejection(work, error, retrieval_id=retrieval['id'])
    resolved_records, resolved = {}, set()

    def resolve(family, row):
        identity = identify_scientific_record(family, row)
        if work['record_identities'].get(row['id']) == identity:
            resolved_records[identity] = row
            resolved.add(row['id'])

    for row in checks:
        if row['status'] == 'resolved':
            resolve('original_checks', row)
    for row in retrievals:
        if row['status'] == 'resolved':
            resolve('retrievals', row)
    for check in checks:
        if identify_scientific_record('original_checks', check) in resolved_records:
            for row in check['conclusions']:
                resolve('original_check_conclusions', row)
    pending = [(family, row, card['dependencies']) for card in work['cards']
               for family, field in (('card_conclusions', 'conclusions'), ('card_conditions', 'conditions'))
               for row in card[field]]
    pending.extend(('cells', row, []) for row in cells.values() if row['status'] != 'unresolved')
    while pending:
        remaining = []
        for family, row, parent_dependencies in pending:
            dependencies = set(parent_dependencies) | set(row.get('dependencies', []))
            premises = {ground for ground in row['grounds'] if isinstance(ground, str)}
            if row['grounds'] and dependencies | premises <= resolved:
                resolve(family, row)
            else:
                remaining.append((family, row, parent_dependencies))
        if len(remaining) == len(pending):
            break
        pending = remaining
    work['resolved_records'] = resolved_records
    # String references remain compatible, but only unique, exactly bound typed
    # records can supply those names to downstream validators.
    work['resolved'] = resolved


def _check_completion_integration_accounting(work):
    full_cards = work['full_cards']
    grouped = work['grouped']
    owners = work['owners']
    task_index = work['task_index']
    current_epoch = work['current_epoch']
    checks = work['checks']
    resolved = work['resolved']
    accepted = work['accepted']
    by_task = work['by_task']
    manifest = work['manifest']
    current_contexts = work['current_contexts']
    closed_groups = work['closed_groups']
    try:
        obligations = card_obligations(full_cards)
    except ResearchError as error:
        _record_completion_rejection(work, error)
        obligations = []
    integrations = list(grouped.get('integrations', {}).values())
    integration_index = _index(integrations, 'integrations')
    binding = set()

    def bind(receipt):
        if 'record_root' not in receipt:
            return receipt
        if receipt['id'] in binding:
            _fail('Integration dependency graph contains a cycle.')
        binding.add(receipt['id'])
        children = [bind(integration_index[key]) for key in receipt['child_ids'] if key in integration_index]
        assigned = task_index[owners[('integrations', receipt['id'])]['task_id']]
        result = bind_integration_lists(receipt, epoch=current_epoch, task=assigned, cards=full_cards,
                                        children=children, observations=list(accepted.values()))
        binding.remove(receipt['id'])
        return result

    bound_integrations = []
    for receipt in integrations:
        try:
            bound_integrations.append(bind(receipt))
        except ResearchError as error:
            _record_completion_rejection(work, error, integration_id=receipt['id'])
    integrations = bound_integrations
    integration_index = _index(integrations, 'integrations')
    valid_integrations, accounted = [], set()
    for receipt in integrations:
        try:
            owner = owners[('integrations', receipt['id'])]
            if receipt['task_id'] != owner['task_id']:
                _fail('Integration has the wrong selected observation.')
            assigned = task_index[receipt['task_id']]
            expected = assigned['obligation_ids'] or ([] if receipt['child_ids'] else obligations)
            if not set(expected) <= set(obligations):
                _fail('Integration task names an absent full-card obligation.')
            validate_integration(receipt, epoch=current_epoch, obligations=expected, children=integrations,
                                 original_checks=checks, known_ids=list(resolved),
                                 findings=set(grouped.get('findings', {})) |
                                 {row['id'] if isinstance(row, dict) else row for row in manifest['inherited_findings']} |
                                 {row['id'] for row in work['epoch']['material_objections']},
                                 answers=[row['id'] for row in owner['output'].get('answers', [])])
            if not set(receipt['selected_observations']) <= set(accepted):
                _fail('Integration names an unselected observation.')
            dependency_observations = {by_task[key]['id'] for key in assigned['dependencies'] if key in by_task}
            if not set(receipt['selected_observations']) <= dependency_observations:
                _fail('Integration observation is absent from the frozen dependency closure.')
            allowed_roots = {manifest['digest']} | {digest(card) for card in full_cards}
            allowed_roots.update(accepted[key]['raw_output_digest'] for key in receipt['selected_observations'])
            allowed_roots.update(digest(integration_index[key]) for key in receipt['child_ids'])
            if not receipt['input_roots'] or not set(receipt['input_roots']) <= allowed_roots:
                _fail('Integration input roots do not bind its selected inputs.')
            if not receipt['child_ids']:
                for card in full_cards:
                    if set(card_obligations([card])) & set(expected) and \
                            owner['actual_input'].get('full_card_roots', {}).get(card['id']) != digest(card):
                        _fail('Integration leaf did not receive its assigned full-card conclusions and conditions.')
            for key, root in receipt['context_roots'].items():
                if root != current_contexts[key]['root']:
                    _fail('Integration original closure changed.', code='review_task_dependency_changed')
            if receipt['unresolved'] or any(row['disposition'] == 'conflict' for row in receipt['dispositions']):
                _record_completion_issue(work, 'review_integration_unresolved', integration_id=receipt['id'])
            for dependency in receipt['dependencies']:
                if dependency not in resolved and dependency not in integration_index:
                    _fail('Integration omitted an unresolved dependency.', dependency=dependency)
            if not set(receipt['selected_groups']) <= closed_groups:
                _fail('Integration has unchecked selected originals.')
            accounted.update(receipt['obligation_ids'])
            valid_integrations.append(receipt)
        except ResearchError as error:
            _record_completion_rejection(work, error, integration_id=receipt['id'])
    if set(obligations) != accounted:
        _record_completion_issue(work, 'review_integration_pending', obligation_ids=sorted(set(obligations) - accounted))
    if not valid_integrations:
        _record_completion_issue(work, 'review_integration_pending', reason='No selected integration receipt.')
    for card in full_cards:
        for row in card['conclusions'] + card['conditions']:
            if row['id'] not in resolved:
                _record_completion_issue(work, 'review_integration_unresolved', card_id=card['id'], record_id=row['id'])
        for dependency in card['dependencies']:
            if dependency not in resolved:
                _record_completion_issue(work, 'review_integration_unresolved', card_id=card['id'], dependency=dependency)
    work['obligations'] = obligations
    work['valid_integrations'] = valid_integrations
    work['accounted'] = accounted


def _check_completion_finding_answers(work):
    grouped = work['grouped']
    manifest = work['manifest']
    epoch = work['epoch']
    cards = work['cards']
    resolved = work['resolved']
    findings = []
    for finding in grouped.get('findings', {}).values():
        try:
            findings.append(validate_finding(finding, manifest, known_ids=resolved))
        except ResearchError as error:
            _record_completion_rejection(work, error, finding_id=finding['id'])
    inherited = manifest['inherited_findings']
    finding_ids = {row['id'] if isinstance(row, dict) else row for row in inherited}
    finding_ids.update(row['id'] for row in findings)
    finding_ids.update(row['id'] for row in epoch['material_objections'])
    finding_ids.update(key for card in cards for key in card['findings'])
    frame_pending = {row['id']: row for row in work['context_graph']['unresolved']}
    finding_ids.update(frame_pending)
    card_pending = {identify_card_question(card, question): (card, question)
                    for card in cards for question in card['unresolved']}
    finding_ids.update(card_pending)
    answered = set()
    for answer in grouped.get('answers', {}).values():
        try:
            check_fields(answer, ('id', 'finding_id', 'disposition', 'reason', 'grounds'), code='invalid_review_staged')
            check_text(answer['id'], 'answer.id', code='invalid_review_staged')
            check_text(answer['reason'], 'reason', code='invalid_review_staged')
            if answer['finding_id'] not in finding_ids or answer['disposition'] not in ('retained', 'answered', 'unresolved'):
                _fail('Finding answer has no exact finding or disposition.')
            _ground(answer['grounds'], manifest, resolved)
            if answer['disposition'] == 'answered' and not answer['grounds']:
                _fail('An answered finding requires grounded support.')
            if answer['finding_id'] in frame_pending:
                pending = frame_pending[answer['finding_id']]
                owner = work['owners'][('answers', answer['id'])]
                if answer['disposition'] != 'answered' or not depends_on_task(
                        owner, pending['task_id'], work['all_by_task']):
                    _fail('Frame work requires a grounded answer depending on its discovery.')
            if answer['finding_id'] in card_pending:
                card, question = card_pending[answer['finding_id']]
                owner = work['owners'][('answers', answer['id'])]
                discoveries = [row for row in work['observed'] if card in row['output'].get('cards', [])]
                if (not answer['grounds'] or not any(depends_on_task(owner, row['task_id'], work['all_by_task'])
                                                     for row in discoveries)
                        or not _covers_inputs(_find_quotation_spans(answer), owner['actual_input']['intervals'])):
                    _fail('Card questions require grounded answers delivered after their discovery.')
                if answer['disposition'] == 'retained':
                    terminal = next((row['output']['terminal'] for row in work['observed']
                                     if 'terminal' in row['output']), {})
                    claims = set(question['claim_ids'])
                    relevant = [kind for kind in ASSURANCES
                                if claims & set(manifest['assurance_dependencies'][kind])] or list(ASSURANCES)
                    if epoch['role'] == 'result':
                        adverse = all(terminal.get('assurances', {}).get(kind) in ('failed', 'not_assessed')
                                      for kind in relevant)
                        if terminal.get('decisions', {}).get('/value/status', 'insufficient') == 'sufficient':
                            adverse = False
                    else:
                        adverse = terminal.get('disposition') in ('upheld', 'unresolved')
                    if not adverse:
                        _fail('Retained card questions require an explicit incomplete or adverse assessment.')
            if answer['disposition'] != 'unresolved':
                answered.add(answer['finding_id'])
        except ResearchError as error:
            _record_completion_rejection(work, error, answer_id=answer['id'])
    for key in sorted(finding_ids - answered):
        code = ('review_frame_unresolved' if key in frame_pending else
                'review_card_question_pending' if key in card_pending else 'review_finding_answer_pending')
        details = (dict(card_id=card_pending[key][0]['id'], question=copy.deepcopy(card_pending[key][1]))
                   if key in card_pending else {})
        _record_completion_issue(work, code, finding_id=key, **details)
    work['findings'] = findings
    work['inherited'] = inherited
    work['answered'] = answered


def _check_completion_objection_evidence(work):
    epoch = work['epoch']
    observed = work['observed']
    for requirement in epoch['material_objection_entries']:
        if not any(_has_objection_entry(dict(material_objections=row['actual_input'].get('material_objections', [])), requirement)
                   for row in observed):
            _record_completion_issue(work, 'review_objection_evidence_invalid', objection_id=requirement['objection_id'],
                  field=requirement['field'], index=requirement['index'])


def _check_completion_terminal_identity(work):
    epoch = work['epoch']
    terminal = work['terminal']
    grouped = work['grouped']
    manifest = work['manifest']
    resolved = work['resolved']
    if terminal.get('origin') == 'reviewer':
        check_fields(terminal, ('origin', 'composition_root', 'decisions'), ('integration_id', 'record_root', 'visible_conditions'), code='invalid_review_staged')
        if terminal['decisions'] != {'/origin': 'reviewer'}:
            _fail('An incomplete composition authorizes reviewer origin only.')
        return
    if epoch['role'] == 'result':
        check_fields(terminal, ('assurances', 'visible_conditions'), ('composition_root', 'integration_id', 'decisions', 'record_root'), code='invalid_review_staged')
        check_fields(terminal['assurances'], ASSURANCES, code='invalid_review_staged')
    else:
        check_fields(terminal, ('objection_id', 'disposition', 'correction_admissible', 'visible_conditions'),
                 ('composition_root', 'integration_id', 'decisions', 'record_root'), code='invalid_review_staged')
        if terminal['objection_id'] != epoch['focused_scope']['objection_id'] or \
                terminal['disposition'] not in ('upheld', 'not_upheld', 'not_binding', 'unresolved'):
            _fail('Adjudication terminal does not bind its exact focused objection.')
        if terminal['correction_admissible'] is not None and type(terminal['correction_admissible']) is not bool:
            _fail('Correction admissibility must be null or boolean.')
        if terminal['correction_admissible'] is not None:
            corrections = [row for row in grouped.get('decision_checks', {}).values()
                           if row.get('decision') == '/correction_admissible']
            if len(corrections) != 1:
                _fail('Correction admissibility needs its own original-context decision check.')
            correction = corrections[0]
            check_fields(correction, ('id', 'decision', 'value', 'claim_ids', 'reason', 'grounds', 'dependencies'), code='invalid_review_staged')
            check_text(correction['reason'], 'reason', code='invalid_review_staged')
            if correction['value'] is not terminal['correction_admissible'] or correction['dependencies'] or not correction['grounds']:
                _fail('Correction admissibility lacks a settled decision check.')
            _ground(correction['grounds'], manifest, resolved)
            if not _uses_original_context(correction['grounds'], work):
                _fail('Correction admissibility may rest only on original-context grounds.')
        decisions = [row for row in grouped.get('decision_checks', {}).values() if row.get('decision') in ('disposition', '/disposition')]
        if len(decisions) != 1:
            _record_completion_issue(work, 'review_assurance_dependency_pending', decision='disposition')
        else:
            decision = decisions[0]
            check_fields(decision, ('id', 'decision', 'value', 'claim_ids', 'reason', 'grounds', 'dependencies'), code='invalid_review_staged')
            check_text(decision['reason'], 'decision.reason', code='invalid_review_staged')
            _ground(decision['grounds'], manifest, resolved)
            if decision['value'] != terminal['disposition'] or decision['dependencies'] or \
                    not decision['grounds'] or not set(manifest['contexts']) <= set(decision['claim_ids']):
                _record_completion_issue(work, 'review_assurance_dependency_pending', decision='disposition')


def _uses_original_context(grounds, work):
    from .review_adjudicator_sources import has_original_context
    manifest = work['manifest']
    classes = {row['id']: row.get('evidence_class') for row in manifest['occurrences']}
    records = {}
    for card in work['cards']:
        for record in [card] + card['judgments'] + card['conclusions'] + card['conditions']:
            records[record['id']] = (record, classes[card['occurrence_id']])
    for check in work['checks']:
        records[check['id']] = (check, None)
        for record in check['conclusions']:
            records[record['id']] = (record, None)

    def uses_original(ground, visiting):
        if isinstance(ground, dict):
            return has_original_context(manifest, ground['source_id'])
        if ground in visiting or ground not in records or ground not in work['resolved']:
            return False
        record, evidence_class = records[ground]
        if evidence_class == 'current':
            return False
        premises = record.get('grounds') or [item for row in record.get('conclusions', []) for item in row['grounds']]
        return bool(premises) and all(uses_original(item, visiting | {ground}) for item in premises)

    return bool(grounds) and all(uses_original(ground, set()) for ground in grounds)


def _check_completion_terminal_integration(work):
    terminal = work['terminal']
    valid_integrations = work['valid_integrations']
    obligations = work['obligations']
    selected_integration = terminal.get('integration_id')
    valid_index = _index(valid_integrations, 'integrations')
    roots = set(valid_index) - {key for receipt in valid_integrations for key in receipt['child_ids']}
    if selected_integration is None and len(roots) == 1:
        selected_integration = next(iter(roots))
    if selected_integration not in roots:
        _record_completion_issue(work, 'review_integration_pending', reason='Terminal does not select a unique complete integration root.')
    else:
        reachable, pending = set(), [selected_integration]
        while pending:
            key = pending.pop()
            if key not in valid_index:
                _fail('Integration root has an invalid child.')
            if key not in reachable:
                reachable.add(key)
                pending.extend(valid_index[key]['child_ids'])
        if reachable != set(valid_index) or set(valid_index[selected_integration]['obligation_ids']) != set(obligations):
            _record_completion_issue(work, 'review_integration_pending', reason='Terminal root omits required integration work.')


def _check_completion_visible_conditions(work):
    full_cards = work['full_cards']
    terminal = work['terminal']
    check_ids(terminal['visible_conditions'], 'visible_conditions', code='invalid_review_staged')
    visible = set(collect_condition_ids(work['manifest'], full_cards))
    if not visible <= set(terminal['visible_conditions']):
        _fail('Terminal lost a required condition.', missing=sorted(visible - set(terminal['visible_conditions'])))


def _check_completion_assurances(work):
    grouped = work['grouped']
    epoch = work['epoch']
    terminal = work['terminal']
    manifest = work['manifest']
    cells = work['cells']
    resolved = work['resolved']
    findings = work['findings']
    valid_integrations = work['valid_integrations']
    assurance_checks = {}
    for check in grouped.get('assurance_checks', {}).values():
        try:
            check_fields(check, ('id', 'assurance', 'status', 'claim_ids', 'reason', 'grounds',
                              'conditions_satisfied', 'dependencies'), code='invalid_review_staged')
            check_text(check['assurance'], 'assurance', code='invalid_review_staged')
            check_ids(check['claim_ids'], 'claim_ids', code='invalid_review_staged')
            check_ids(check['dependencies'], 'dependencies', code='invalid_review_staged')
            if check['assurance'] not in ASSURANCES or check['assurance'] in assurance_checks:
                _fail('Assurance checks must have unique assurance kinds.')
            assurance_checks[check['assurance']] = check
        except ResearchError as error:
            _record_completion_rejection(work, error, assurance_check_id=check['id'])
    for kind in (ASSURANCES if epoch['role'] == 'result' else ()):
        status = terminal['assurances'][kind]
        seeds = set(manifest['assurance_dependencies'][kind])
        relevant = [cell for cell in cells.values() if cell['claim_id'] in seeds]
        check = assurance_checks.get(kind)
        if status not in ('passed', 'failed', 'not_assessed'):
            _fail('Unknown terminal assurance choice.')
        if check is None or check.get('status') != status or not seeds <= set(check.get('claim_ids', [])):
            _record_completion_issue(work, 'review_assurance_dependency_pending', assurance=kind)
            continue
        check_fields(check, ('id', 'assurance', 'status', 'claim_ids', 'reason', 'grounds',
                          'conditions_satisfied', 'dependencies'), code='invalid_review_staged')
        if type(check['conditions_satisfied']) is not bool:
            _fail('Assurance condition satisfaction must be explicit boolean data.')
        check_ids(check['claim_ids'], 'claim_ids', code='invalid_review_staged')
        check_ids(check['dependencies'], 'dependencies', code='invalid_review_staged')
        check_text(check.get('reason'), 'assurance.reason', code='invalid_review_staged')
        _ground(check.get('grounds'), manifest, resolved | {cell['id'] for cell in cells.values()})
        if not check['grounds']:
            _record_completion_issue(work, 'review_assurance_dependency_pending', assurance=kind)
        if not seeds <= {cell['claim_id'] for cell in relevant}:
            _record_completion_issue(work, 'review_assurance_dependency_pending', assurance=kind)
        if status == 'passed':
            conditions = [row for card in work['cards']
                          if any(cell['claim_id'] in seeds for cell in card['judgments'])
                          for row in card['conditions']]
            if (any(cell['status'] in ('unresolved', 'contradicted') or cell['id'] not in resolved for cell in relevant) or
                    any(row.get('satisfied', True) is not True or row['id'] not in resolved for row in conditions) or
                    not check.get('conditions_satisfied') or check.get('dependencies') or
                    any(row.get('adverse') and not row.get('classification') for row in findings)):
                _record_completion_issue(work, 'review_assurance_dependency_pending', assurance=kind)
        for receipt in valid_integrations:
            constraints = receipt['decision_constraints']
            allowed = constraints.get('permissible_assurances', {}).get(kind)
            if allowed is not None and status not in allowed:
                _record_completion_issue(work, 'review_assurance_dependency_pending', assurance=kind, permissible=allowed)
            if status == 'passed' and (receipt['unresolved'] or
                    any(row['disposition'] == 'conflict' for row in receipt['dispositions'])):
                _record_completion_issue(work, 'review_assurance_dependency_pending', assurance=kind)


def _check_completion_decisions(work):
    valid_integrations = work['valid_integrations']
    epoch = work['epoch']
    terminal = work['terminal']
    manifest = work['manifest']
    cells = work['cells']
    decisions = terminal.get('decisions')
    if decisions is not None:
        from .review_composition import list_scalar_fields
        scalar_fields = list_scalar_fields(epoch['role'])
        check_fields(decisions, scalar_fields, code='invalid_review_staged')
        for key, choices in scalar_fields.items():
            if choices is not None and digest(decisions[key]) not in {digest(choice) for choice in choices}:
                _fail('Terminal decision is outside its scientific schema.', decision=key)
        if epoch['role'] == 'result':
            if any(decisions['/value/' + key] != 'not_assessed' for key in ('objective_adequacy', 'method_adequacy')):
                _fail('Problem review adequacies are fixed at not_assessed.')
            for kind in ASSURANCES:
                if decisions['/value/assurances/' + kind + '/status'] != terminal['assurances'][kind]:
                    _fail('Terminal assurance and decision disagree.')
        else:
            for key in ('objection_id', 'disposition', 'correction_admissible'):
                if digest(decisions['/' + key]) != digest(terminal[key]):
                    _fail('Adjudicator terminal and decision disagree.')
        checks = {}
        for check in work['grouped'].get('decision_checks', {}).values():
            check_fields(check, ('id', 'decision', 'value', 'claim_ids', 'reason', 'grounds', 'dependencies'), code='invalid_review_staged')
            key = check['decision']
            check_text(key, 'decision', code='invalid_review_staged')
            if key not in decisions or key in checks:
                _fail('Decision checks must have unique scientific scalar paths.')
            checks[key] = check
            check_text(check['reason'], 'decision.reason', code='invalid_review_staged')
            check_ids(check['claim_ids'], 'claim_ids', code='invalid_review_staged')
            check_ids(check['dependencies'], 'dependencies', code='invalid_review_staged')
            _ground(check['grounds'], manifest, work['resolved'])
            if digest(check['value']) != digest(decisions[key]) or not check['grounds'] or check['dependencies']:
                _record_completion_issue(work, 'review_assurance_dependency_pending', decision=key)
        required = set(decisions) - {'/stage', '/support', '/value/objective_adequacy', '/value/method_adequacy', '/objection_id'}
        required -= {'/value/assurances/' + kind + '/status' for kind in ASSURANCES}
        for key in sorted(required - set(checks)):
            _record_completion_issue(work, 'review_assurance_dependency_pending', decision=key)
    constraints = [receipt['decision_constraints'] for receipt in valid_integrations]
    constraints.append(epoch['completion_requirements'])
    for constraint in constraints:
        for key, allowed in constraint.get('permissible_decisions', {}).items():
            choice = terminal.get('decisions', {}).get(key, terminal.get(key.lstrip('/')))
            if choice not in allowed:
                _record_completion_issue(work, 'review_assurance_dependency_pending', decision=key, permissible=allowed)
        for key, required_claims in constraint.get('decision_dependencies', {}).items():
            for claim_id in required_claims:
                if claim_id not in manifest['contexts'] or any(
                        cell['status'] == 'unresolved' for cell in cells.values() if cell['claim_id'] == claim_id):
                    _record_completion_issue(work, 'review_assurance_dependency_pending', decision=key, claim_id=claim_id)


def _check_completion_composition(work):
    selection = work['selection']
    terminal = work['terminal']
    epoch = work['epoch']
    observations_root = work['observations_root']
    certificate = selection.get('composition_certificate')
    if terminal.get('composition_root') is not None:
        if not certificate or not certificate.get('complete') or certificate.get('epoch_digest') != epoch['digest'] or \
                certificate.get('composition_root') != terminal['composition_root'] or \
                certificate.get('selected_observations_root') != observations_root:
            _record_completion_issue(work, 'review_composition_pending', composition_root=terminal['composition_root'])


def _check_completion_terminal(work):
    terminals = [row['output']['terminal'] for row in work['observed'] if 'terminal' in row['output']]
    terminal = terminals[0] if len(terminals) == 1 else None
    if terminal is None:
        _record_completion_issue(work, 'review_terminal_pending', count=len(terminals))
        return
    work['terminal'] = terminal
    try:
        if 'record_root' in terminal:
            integrations = work['valid_integrations']
            roots = {row['id'] for row in integrations} - {key for row in integrations for key in row['child_ids']}
            selected = terminal.get('integration_id')
            if selected is None and len(roots) == 1:
                selected = next(iter(roots))
            integration = next((row for row in integrations if row['id'] == selected), None)
            if integration is None:
                _fail('Terminal record root has no complete integration.')
            terminal = bind_terminal_conditions(work['current_epoch'], terminal, cards=work['full_cards'], integration=integration)
            work['terminal'] = terminal
        _check_completion_terminal_identity(work)
        _check_completion_terminal_integration(work)
        if terminal.get('origin') == 'reviewer':
            _check_completion_composition(work)
            _record_completion_issue(work, 'assessment_incomplete', reason='Reviewer authorized an incomplete composition.')
            return
        _check_completion_visible_conditions(work)
        _check_completion_assurances(work)
        _check_completion_decisions(work)
        for row in work['observed']:
            output = row['output']
            if 'terminal' in output and ('stage' in output or 'objection_id' in output):
                scientific, records = project_ordinary_final(output)
                if work['epoch']['role'] == 'result':
                    statuses = {entry['kind']: entry['status'] for entry in scientific['value']['assurances']}
                    if statuses != terminal['assurances']:
                        _fail('Ordinary scientific assurances differ from terminal authorization.')
                elif any(scientific[key] != terminal[key] for key in ('objection_id', 'disposition', 'correction_admissible')):
                    _fail('Ordinary adjudication differs from terminal authorization.')
                if 'decisions' in terminal:
                    if work['epoch']['role'] == 'result':
                        scientific['value']['assurances'] = {entry['kind']: entry for entry in scientific['value']['assurances']}
                    for path, choice in terminal['decisions'].items():
                        value = scientific
                        for key in path.split('/')[1:]:
                            if not isinstance(value, dict) or key not in value:
                                _fail('Ordinary final omits a terminal-owned scientific field.', path=path)
                            value = value[key]
                        if digest(value) != digest(choice):
                            _fail('Ordinary final differs from its terminal-owned choice.', path=path)
        _check_completion_composition(work)
    except ResearchError as error:
        _record_completion_rejection(work, error)


def _check_completion_requirements(work):
    task_index = work['task_index']
    epoch = work['epoch']
    by_task = work['by_task']
    grouped = work['grouped']
    closed_groups = work['closed_groups']
    selection = work['selection']
    if not task_index:
        _record_completion_issue(work, 'review_task_pending', reason='No frozen task envelopes.')
    requirements = epoch['completion_requirements']
    for kind in requirements.get('required_task_types', []):
        if not any(task_index[key]['type'] == kind for key in by_task) and not (
                kind == 'frame' and any(task_index[key]['type'] == kind for key in work['all_by_task'])):
            _record_completion_issue(work, 'review_task_pending', task_type=kind)
    for key in requirements.get('required_answer_ids', []):
        if key not in grouped.get('answers', {}):
            _record_completion_issue(work, 'review_finding_answer_pending', answer_id=key)
    for key in requirements.get('required_group_ids', []):
        if key not in closed_groups:
            _record_completion_issue(work, 'review_original_check_pending', group_id=key)
    if requirements.get('composition_required') and not selection.get('composition_certificate'):
        _record_completion_issue(work, 'review_composition_pending', reason='Frozen completion requires a composition certificate.')


def _build_completion_report(work):
    source_characters = work['source_characters']
    binary_count = work['binary_count']
    cells = work['cells']
    full_cards = work['full_cards']
    valid_combinations = work['valid_combinations']
    covered_pairs = work['covered_pairs']
    closed_groups = work['closed_groups']
    accounted = work['accounted']
    answered = work['answered']
    resolved = work['resolved']
    epoch = work['epoch']
    current_contexts = work['current_contexts']
    observations_root = work['observations_root']
    issues = work['issues']
    coverage = work['coverage']
    cards = work['cards']
    checks = work['checks']
    valid_integrations = work['valid_integrations']
    findings = work['findings']
    inherited = work['inherited']
    progress = dict(source_characters=source_characters, binary_sources=binary_count,
                    claim_cells=len(cells), unresolved_cells=sum(row['status'] == 'unresolved' for row in cells.values()),
                    full_cards=len(full_cards), combination_assessments=len(valid_combinations),
                    card_pairs=len(covered_pairs), original_groups=len(closed_groups),
                    integrated_obligations=len(accounted), answered_findings=len(answered),
                    resolved_dependencies=len(resolved))
    report = dict(schema=COMPLETION_SCHEMA, epoch_id=epoch['id'], epoch_digest=epoch['digest'],
                  context_roots={key: value['root'] for key, value in current_contexts.items()},
                  selected_observations_root=observations_root, complete=not issues, issues=issues,
                  progress=progress, coverage=coverage,
                  known_conclusions_root=digest(dict(cards=cards, cells=list(cells.values()), original_checks=checks,
                                                     integrations=valid_integrations, findings=findings)),
                  unresolved_scope_root=digest(issues), inherited_adverse_findings_root=digest(inherited))
    if len(work['context_graph']['versions']) > 1 or work['context_graph']['unresolved']:
        report['context_graph_root'] = work['context_graph']['digest']
    if work['task_supersessions']:
        report['task_supersessions'] = work['task_supersessions']
    return _seal(report)


def verify_completion(epoch, selection, *, tasks=None, contexts=None):
    """Verify an explicit authenticated selection; never select from store state.

    Invalid or missing scientific work becomes structured issues and receives no
    completion credit. Malformed frozen identities raise ResearchError. Returned
    roots bind the exact selected work, retained conclusions and unfinished scope.
    Historical authenticity, current currency and reviewer independence are
    separate caller checks.
    """
    return _build_completion_report(_verify_selection(epoch, selection, tasks=tasks, contexts=contexts))


def collect_planning_records(epoch, selection, *, tasks=()):
    """Expose independently verified records while retaining partial reading progress."""
    work = _verify_selection(epoch, selection, tasks=tasks)
    return copy.deepcopy(dict(cards=work['full_cards'], findings=work['findings'],
                              selected_groups=work['groups'], contexts=work['current_contexts'],
                              integrations=work['valid_integrations'], context_graph=work['context_graph'],
                              task_supersessions=work['task_supersessions']))


def _verify_selection(epoch, selection, *, tasks=None, contexts=None):
    _validate_epoch(epoch)
    check_fields(selection, ('observations',), ('composition_certificate',), code='invalid_review_staged')
    work = dict(epoch=epoch, selection=selection, contexts=contexts, manifest=epoch['manifest'],
                task_index=_index(list(tasks or []), 'tasks'),
                raw=_index(selection['observations'], 'observations'), issues=[])
    _expand_completion_contexts(work)
    _check_completion_observations(work)
    _collect_completion_records(work)
    _check_completion_coverage(work)
    _check_completion_groups(work)
    _check_completion_original_checks(work)
    _check_completion_cards(work)
    _check_completion_cells(work)
    _check_completion_card_reconciliation(work)
    _check_completion_combinations(work)
    _check_completion_retrievals(work)
    _check_completion_integration_accounting(work)
    _check_completion_finding_answers(work)
    _check_completion_objection_evidence(work)
    _check_completion_terminal(work)
    _check_completion_requirements(work)
    return work


def _build_ledger(value):
    source = value or {}
    check_fields(source, (), ('attempts', 'retries', 'rereads', 'subdivisions', 'epochs',
                          'elapsed_seconds', 'unknown_usage'), code='invalid_review_staged')
    result = {key: source.get(key, 0) for key in ('attempts', 'retries', 'rereads', 'subdivisions', 'epochs',
                                                'elapsed_seconds')}
    for key, amount in result.items():
        _check_number(amount, key, integer=key != 'elapsed_seconds')
    result['unknown_usage'] = source.get('unknown_usage', False)
    if type(result['unknown_usage']) is not bool:
        _fail('Unknown usage must be explicit boolean data.')
    return result


def task_state(epoch, task, *, ledger=None, failures=0, captured=False, accepted=False,
               worker_unreconciled=False, dependencies_ready=True, current=True, incomplete=None):
    """Return finite task transitions and exact next-command payloads as data."""
    _validate_epoch(epoch)
    _verify_seal(task)
    if task['epoch_digest'] != epoch['digest']:
        _fail('Task belongs to another epoch.')
    _check_number(failures, 'failures', integer=True)
    usage = _build_ledger(ledger)
    policy = epoch['budgets']
    payload = dict(assignment_id=epoch['controller_assignment_id'], epoch_id=epoch['id'], task_id=task['id'])
    result = dict(epoch_id=epoch['id'], task_id=task['id'], resource_ledger=usage)

    def state(name, code, next_work):
        return copy.deepcopy(dict(result, state=name, code=code, next=next_work))

    def command(action=None):
        return dict(kind='command', command='review-run', payload=dict(payload, **({'action': action} if action else {})))

    if worker_unreconciled:
        return state('reconciling', 'review_worker_reconciliation_required', [command('reconcile')])
    if not current:
        return state('historical', 'review_epoch_stale', [dict(kind='recovery', workflow='review-assignment',
                     predecessor_epoch_id=epoch['id'], prerequisite='authorized_successor')])
    if accepted:
        return state('accepted', None, [])
    if captured:
        return state('captured', 'review_observation_acceptance_pending', [command('accept')])
    if incomplete is not None:
        validate_incomplete(incomplete, epoch=epoch)
        return state('incomplete', 'assessment_incomplete', copy.deepcopy(incomplete['recovery_work']))
    exhausted = (usage['attempts'] >= policy['max_calls'] or failures > policy['max_retries'] or
                 usage['retries'] > policy['max_retries'] or
                 usage['rereads'] > 0 and usage['rereads'] >= policy['max_rereads'] or
                 usage['subdivisions'] > 0 and usage['subdivisions'] >= policy['max_subdivisions'] or
                 usage['epochs'] > 0 and usage['epochs'] >= policy['max_epochs'] or
                 usage['elapsed_seconds'] >= policy['max_elapsed_seconds'])
    if exhausted:
        return state('exhausted', 'assessment_incomplete', [dict(kind='research', workflow='review-assignment',
                     scope=dict(task_id=task['id'], claim_ids=task['claim_ids'], inputs=task['inputs']),
                     prerequisite='new_evidence_or_eligible_recovery')])
    if task['planned_request_digest'] is None or task['measurement'] is None:
        return state('blocked', 'review_task_pending', [dict(kind='recovery', workflow='review-assignment',
                     scope=dict(task_id=task['id']), prerequisite='measured_rendering_and_admission')])
    if task['scope'] == 'unresolved' or not task['measurement']['fits']:
        return state('blocked', 'review_task_pending', [dict(kind='recovery', workflow='review-assignment',
                     scope=dict(task_id=task['id']), prerequisite='smaller_admissible_task')])
    if not dependencies_ready:
        return state('blocked', 'review_task_pending', [])
    return state('ready', 'review_task_pending', [command()])


def record_incomplete(epoch, completion, *, origin, origin_binding, unresolved_scope, recovery_work,
                      resource_ledger, recorded_revision=None, predecessor_outcome_id=None):
    """Construct and validate an incomplete record; perform no recording or writes."""
    _validate_epoch(epoch)
    _verify_seal(completion)
    if completion['epoch_digest'] != epoch['digest'] or completion['complete']:
        _fail('An incomplete outcome requires the exact unfinished completion assessment.')
    body = dict(schema=INCOMPLETE_SCHEMA, kind='assessment_incomplete',
                controller_assignment_id=epoch['controller_assignment_id'], epoch_id=epoch['id'],
                epoch_digest=epoch['digest'], slot_id=epoch['slot_id'], role=epoch['role'],
                subject=copy.deepcopy(epoch['subject']), evidence_currency=copy.deepcopy(epoch['evidence_currency']),
                reviewer_id=epoch['reviewer_id'], route_id=epoch['route_id'], author_id=epoch['author_id'],
                origin=origin, origin_binding=copy.deepcopy(origin_binding),
                selected_observations_root=completion['selected_observations_root'], completion_root=completion['digest'],
                known_conclusions_root=completion['known_conclusions_root'],
                unresolved_scope_root=digest(dict(completion_issues=completion['issues'], entries=unresolved_scope)),
                inherited_adverse_findings_root=completion['inherited_adverse_findings_root'],
                resource_ledger=_build_ledger(resource_ledger), recovery_work_root=digest(recovery_work),
                predecessor_outcome_id=predecessor_outcome_id, recorded_revision=recorded_revision,
                completion_issues=copy.deepcopy(completion['issues']), unresolved_scope=copy.deepcopy(unresolved_scope),
                recovery_work=copy.deepcopy(recovery_work))
    body['id'] = 'assessment-incomplete:' + digest(body)
    result = _seal(body)
    validate_incomplete(result, epoch=epoch)
    return result


def validate_incomplete_scope(unresolved_scope, recovery_work):
    """Validate reviewer and recorded incomplete scope with one shared contract."""
    entries = _require_array(unresolved_scope, 'unresolved_scope')
    if not entries:
        _fail('An incomplete outcome must name unfinished scientific scope.')
    exhausted = set()
    for row in entries:
        check_fields(row, ('id', 'scope', 'cause', 'affected_decisions', 'exhausted_tasks', 'scope_kind', 'next_action'), code='invalid_review_staged')
        for key in ('id', 'cause'):
            check_text(row[key], key, code='invalid_review_staged')
        if not isinstance(row['scope'], dict) or not row['scope']:
            _fail('Unresolved scope must identify its scientific obligations.')
        check_ids(row['affected_decisions'], 'affected_decisions', code='invalid_review_staged')
        check_ids(row['exhausted_tasks'], 'exhausted_tasks', code='invalid_review_staged')
        exhausted.update(row['exhausted_tasks'])
        if row['scope_kind'] not in SCOPES:
            _fail('Unknown unresolved assessment scope.')
        check_fields(row['next_action'], ('workflow', 'scope', 'prerequisite'), code='invalid_review_staged')
        for key in ('workflow', 'prerequisite'):
            check_text(row['next_action'][key], key, code='invalid_review_staged')
        if not isinstance(row['next_action']['scope'], dict) or not row['next_action']['scope']:
            _fail('Next action must name concrete work.')
        if row['next_action']['scope'].get('task_id') in row['exhausted_tasks']:
            _fail('Next action cannot repeat an exhausted task.')
    _index(entries, 'unresolved_scope')
    for action in _require_array(recovery_work, 'recovery_work'):
        check_fields(action, ('workflow', 'command', 'payload', 'text'), code='invalid_review_staged')
        for key in ('workflow', 'command', 'text'):
            check_text(action[key], key, code='invalid_review_staged')
        if not isinstance(action['payload'], dict):
            _fail('Recovery payload must be an object.')
        if action.get('command') == 'review-run' and action.get('payload', {}).get('task_id') in exhausted:
            _fail('Recovery cannot repeat an exhausted invocation.')
    return copy.deepcopy(dict(unresolved_scope=unresolved_scope, recovery_work=recovery_work))


def validate_incomplete(record, *, epoch=None):
    """Validate incomplete attribution, immutable scope roots and recovery actions."""
    required = ('schema', 'kind', 'id', 'digest', 'controller_assignment_id', 'epoch_id', 'epoch_digest',
                'slot_id', 'role', 'subject', 'evidence_currency', 'reviewer_id', 'route_id', 'author_id',
                'origin', 'origin_binding', 'selected_observations_root', 'completion_root',
                'known_conclusions_root', 'unresolved_scope_root', 'inherited_adverse_findings_root',
                'resource_ledger', 'recovery_work_root', 'predecessor_outcome_id', 'recorded_revision',
                'completion_issues', 'unresolved_scope', 'recovery_work')
    check_fields(record, required, code='invalid_review_staged')
    _verify_seal(record)
    if record['schema'] != INCOMPLETE_SCHEMA or record['kind'] != 'assessment_incomplete':
        _fail('Unknown incomplete-outcome contract.')
    identity = {key: value for key, value in record.items() if key not in ('id', 'digest')}
    if record['id'] != 'assessment-incomplete:' + digest(identity):
        _fail('Incomplete outcome identity does not match its immutable content.')
    for key in ('controller_assignment_id', 'epoch_id', 'epoch_digest', 'slot_id', 'reviewer_id', 'route_id',
                'author_id', 'selected_observations_root', 'completion_root', 'known_conclusions_root',
                'unresolved_scope_root', 'inherited_adverse_findings_root', 'recovery_work_root'):
        check_text(record[key], key, code='invalid_review_staged')
    if record['role'] not in ('result', 'adjudicator'):
        _fail('Unknown incomplete slot role.')
    if record['origin'] == 'reviewer':
        check_fields(record['origin_binding'], ('attempt_id', 'raw_output_digest'), code='invalid_review_staged')
        for key in record['origin_binding']:
            check_text(record['origin_binding'][key], key, code='invalid_review_staged')
    elif record['origin'] == 'execution':
        check_fields(record['origin_binding'], ('execution_receipts', 'reason'), code='invalid_review_staged')
        check_ids(record['origin_binding']['execution_receipts'], 'execution_receipts', code='invalid_review_staged')
        if not record['origin_binding']['execution_receipts'] or record['origin_binding']['reason'] not in ('exhausted', 'unknown_outcome'):
            _fail('Execution-origin incompleteness needs exhaustion or unknown-outcome receipts.')
    else:
        _fail('Unknown incomplete-outcome origin.')
    entries = record['unresolved_scope']
    validate_incomplete_scope(entries, record['recovery_work'])
    if record['unresolved_scope_root'] != digest(dict(completion_issues=record['completion_issues'], entries=entries)) or \
            record['recovery_work_root'] != digest(record['recovery_work']):
        _fail('Incomplete scope or recovery root does not match.')
    _build_ledger(record['resource_ledger'])
    if record['recorded_revision'] is not None:
        _check_number(record['recorded_revision'], 'recorded_revision', integer=True)
    if epoch is not None:
        _validate_epoch(epoch)
        for key in ('controller_assignment_id', 'epoch_id', 'epoch_digest', 'slot_id', 'role', 'subject',
                    'evidence_currency', 'reviewer_id', 'route_id', 'author_id'):
            expected = epoch['id'] if key == 'epoch_id' else epoch['digest'] if key == 'epoch_digest' else epoch[key]
            if record[key] != expected:
                _fail('Incomplete outcome does not bind its exact epoch.', field=key)
        if record['inherited_adverse_findings_root'] != digest(epoch['manifest']['inherited_findings']):
            _fail('Incomplete outcome lost inherited adverse findings.')
    return copy.deepcopy(record)


def transition_outcome(head, event, *, epoch_id=None, outcome=None):
    """Apply a slot transition as data, retaining history and adverse findings."""
    check_fields(head, ('slot_id', 'epoch_id', 'state', 'outcome'),
                 ('role', 'history', 'inherited_adverse_findings', 'current'), code='invalid_review_staged')
    if head['state'] not in ('pending', 'complete', 'incomplete'):
        _fail('Unknown slot state.')
    result = copy.deepcopy(head)
    if event in ('complete', 'incomplete'):
        if head['state'] != 'pending' or outcome is None:
            _fail('Only a pending epoch can select an outcome.')
        if event == 'incomplete':
            validate_incomplete(outcome)
            if outcome['slot_id'] != head['slot_id'] or outcome['epoch_id'] != head['epoch_id']:
                _fail('Incomplete outcome has the wrong slot.')
        result.update(state=event, outcome=copy.deepcopy(outcome))
    elif event == 'successor':
        if not epoch_id or epoch_id == head['epoch_id']:
            _fail('Successor requires a distinct authorized epoch.')
        history = list(result.get('history', []))
        history.append(dict(epoch_id=head['epoch_id'], state=head['state'], outcome=copy.deepcopy(head['outcome'])))
        result.update(epoch_id=epoch_id, state='pending', outcome=None, history=history, current=True)
    elif event == 'evidence_changed':
        result['current'] = False
    elif event == 'late_observation':
        # Historical capture is preserved by execution; frozen membership is unchanged.
        pass
    else:
        _fail('Unknown slot transition.')
    return result


def collect_slot_outcomes(heads, *, qualified=False, scientific_ready=False):
    """Collect current slot occupancy without fabricating reviews or readiness."""
    slots, complete, incomplete, pending, adverse = {}, [], [], [], []
    current = True
    for head in heads:
        check_fields(head, ('slot_id', 'epoch_id', 'state', 'outcome'),
                 ('role', 'history', 'inherited_adverse_findings', 'current'), code='invalid_review_staged')
        key = head['slot_id']
        if key in slots:
            _fail('Multiple current heads for one logical slot.', slot_id=key)
        slots[key] = copy.deepcopy(head)
        current = current and head.get('current', True)
        for finding in head.get('inherited_adverse_findings', []):
            if finding not in adverse:
                adverse.append(copy.deepcopy(finding))
        if head['state'] == 'pending':
            if head['outcome'] is not None:
                _fail('Pending successor cannot retain an effective predecessor outcome.')
            pending.append(copy.deepcopy(head))
        elif head['state'] == 'incomplete':
            validate_incomplete(head['outcome'])
            if head['outcome']['slot_id'] != key or head['outcome']['epoch_id'] != head['epoch_id']:
                _fail('Current slot points to a different incomplete epoch.')
            incomplete.append(copy.deepcopy(head['outcome']))
        elif head['state'] == 'complete' and isinstance(head['outcome'], dict):
            if head['outcome'].get('kind') == 'assessment_incomplete':
                _fail('Incomplete outcome cannot masquerade as a completed review.')
            complete.append(copy.deepcopy(head['outcome']))
        else:
            _fail('Unknown slot state or missing completed outcome.')
    assessment_complete = bool(slots and not pending and not incomplete and current)
    return dict(slots=list(slots.values()), occupied=len(slots), complete=complete, incomplete=incomplete,
                pending=pending, adverse_findings=adverse, assessment_complete=assessment_complete,
                ready=bool(assessment_complete and qualified and scientific_ready and not adverse))
