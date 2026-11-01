"""Authenticated output-contract refusals and bounded recovery audit records."""

import json
from contextlib import contextmanager
from functools import partial, wraps

from .artifacts import ArtifactStore, _CheckEffectRequired, validate_reference
from .errors import ResearchError, describe_error_at
from .evidence import digest
from .fulltext import GENERIC_BINARY_MEDIA_TYPES, parse_base_media_type
from .operations import immutable_record, prepared_mutation

PROTOCOL = 'exactory-review-output-refusal-v1'


@contextmanager
def contract_check(name):
    """Identify the pure validator, rather than treating a shared error code as eligibility."""
    try:
        yield
    except ResearchError as error:
        if not getattr(error, 'contract_external', False) and not hasattr(error, 'contract_check'):
            error.contract_check = name
        raise


def _frozen_pdf_capture(link, packets):
    """Find a PDF page map only where a frozen source delivery binds it to the original."""
    if parse_base_media_type(link['artifact']['media_type']) in GENERIC_BINARY_MEDIA_TYPES:
        # Native format-inference qualification is not present in this projection.
        return None
    for packet in packets:
        for delivery in packet.get('source_deliveries', []):
            if delivery.get('request', {}).get('version_id') != link['version_id']:
                continue
            for provenance in delivery.get('source_provenance', []):
                if (provenance['source_id'] != link['source_id']
                        or provenance['original_descriptor']['sha256'] != link['artifact']['sha256']):
                    continue
                # Acquisition stores an extraction as text/plain with its charset parameter.
                texts = [artifact for artifact in delivery['evidence']
                         if artifact['sha256'] in provenance['delivered_sha256']
                         and parse_base_media_type(artifact['media_type']) == 'text/plain']
                if len(texts) == 1:
                    return {'original': link['artifact'], 'text': texts[0]}
    return None


def validate_evidence_contract(records, artifacts, values, *, required=True, packets=(), review_binding=False):
    """Shared evidence grammar and locators over exact bytes; no live source/reading lookup."""
    from . import strategy
    from .source_links import read_locator, validate_link_fields
    with contract_check('evidence.shape'):
        references = strategy.items(values, 'Evidence', nonempty=required)
        for reference in references:
            strategy.evidence_fields(reference, review_binding=review_binding)
            if {'path', 'sha256', 'size', 'media_type'} <= reference.keys():
                validate_reference(reference)
            elif reference.get('kind') == 'source':
                validate_link_fields(reference['link'], locator=False)
                validate_reference(reference['link']['artifact'])
    for reference in references:
        if reference.get('kind') != 'source':
            continue
        link = reference['link']
        media = parse_base_media_type(link['artifact']['media_type'])
        # Missing bytes are evidence-availability failures, before any locator check.
        try:
            artifacts.read(link['artifact'])
            kind = link['locator'].get('kind') if isinstance(link['locator'], dict) else None
            capture = _frozen_pdf_capture(link, packets) if kind == 'pdf' else None
            if (kind == 'pdf' and capture is None
                    and (media == 'application/pdf' or media in GENERIC_BINARY_MEDIA_TYPES)):
                # Page-map-dependent checks cannot classify an output without a delivered page map.
                continue
            if capture is not None:
                artifacts.read(capture['text'])
        except ResearchError as error:
            error.contract_external = True
            raise
        try:
            validate_link_fields(link)
            read_locator(artifacts, link['artifact'], link['locator'], capture=capture)
        except ResearchError as error:
            if error.code == 'invalid_locator':
                error.contract_check = 'evidence.locator'
            else:
                error.contract_external = True
            raise


def evidence_arrays(output, *, with_paths=False):
    """Traverse completed scientific fields, never a raw task or page envelope."""
    if 'objection_id' in output and 'value' not in output:
        arrays = [('/evidence', output['evidence'])]
        return arrays if with_paths else [output['evidence']]
    value = output['value']
    arrays = [('/value/evidence', value['evidence'])]
    for path, rows in (('/value/assurances', value['assurances']), ('/value/work_items', value['work_items']),
                       ('/value/objection_findings', value['objection_findings']), ('/objections', output['objections']),
                       ('/reassessment/findings', output.get('reassessment', {}).get('findings', []))):
        arrays.extend((path + '/' + str(index) + '/evidence', row['evidence']) for index, row in enumerate(rows))
    return arrays if with_paths else [evidence for path, evidence in arrays]


def _validate_staged_objections(records, artifacts, values, evidence_validator):
    """Check each complete ordinary objection before reading its evidence or comparing identities."""
    from . import strategy
    from .operations import fields, text
    with contract_check('review.objections'), describe_error_at('/objections'):
        seen = set()
        for index, objection in enumerate(strategy.items(values, 'Material objections')):
            path = '/objections/' + str(index)
            fields(objection, ('id', 'claim', 'reason', 'evidence', 'resolution_condition'),
                   code='invalid_value_review', path=path)
            for key in ('id', 'claim', 'reason', 'resolution_condition'):
                with describe_error_at(path + '/' + key):
                    text(objection[key], 'Material objection ' + key)
            with describe_error_at(path + '/evidence'):
                evidence_validator(records, artifacts, objection['evidence'], review_binding=True)
            if objection['id'] in seen:
                raise ResearchError('invalid_value_review', 'Material objection identities must be unique',
                                    {'path': path + '/id'})
            seen.add(objection['id'])


def _validate_staged_final(records, artifacts, assignment, packet, epoch, output):
    """Check ordinary scientific grammar without selecting a staged effective result or its delivery graph."""
    from . import review_context_repair, review_excerpts, review_supplied_evidence, strategy
    from .operations import strings, text
    frame = epoch.get('manifest', {}).get('frame', {})
    evidence = partial(validate_evidence_contract, packets=(packet, frame))
    with contract_check('review.reassessment'):
        review_context_repair.validate_reassessment(records, artifacts, assignment, output, reading_checks=False,
                                                    packet=frame, evidence_validator=evidence)
    if assignment['role'] == 'adjudicator':
        from .review_composition import list_scalar_fields
        with contract_check('adjudication.shape'):
            for path, choices in list_scalar_fields('adjudicator').items():
                value = output[path[1:]]
                if choices is not None and not any(type(value) is type(choice) and value == choice for choice in choices):
                    raise ResearchError('invalid_review_adjudication', 'Use a focused adjudication scalar from its contract.',
                                        {'path': path})
            text(output['objection_id'], 'Adjudicated objection ID')
            text(output['reason'], 'Adjudication reason')
            expected = epoch.get('focused_scope', {}).get('objection_id')
            if expected is None:
                expected = frame.get('objection', {}).get('id')
            if expected is not None and output['objection_id'] != expected:
                raise ResearchError('review_adjudication_mismatch', 'Disposition must concern the assigned objection')
            evidence(records, artifacts, output['evidence'])
        return
    from .research_decisions import _validate_value
    from .problem_rubric import PROBLEM_CHECKS
    with contract_check('review.shape'):
        strategy.choice(output['stage'], (assignment['role'],), 'Review phase', 'invalid_value_review')
        if 'excerpts' in output:
            review_excerpts.validate_excerpts(output['excerpts'], 'invalid_value_review')
    with contract_check('review.value'):
        _validate_value(records, artifacts, output['value'], output['stage'], {'payload': {}},
                        expected_checks=PROBLEM_CHECKS, problem={},
                        problem_objections=review_supplied_evidence.list_delivered_objections(frame),
                        evidence_validator=evidence)
        strings(output['limitations'], 'Independent assessment limitations', nonempty=True)
    _validate_staged_objections(records, artifacts, output['objections'], evidence)
    with contract_check('review.support'):
        if output['support'] is not None:
            raise ResearchError('invalid_value_review', 'A problem review supplies problem assurances, not native proof acceptance')


def _project_staged_final(role, output):
    """Recognize partial scientific objects by their role fields before requiring the complete ordinary shape."""
    from .operations import fields
    from .review_composition import list_page_fields, list_scalar_fields
    from .review_staged import project_ordinary_final
    required, optional = list_page_fields(role)
    required_fields = {path.split('/')[1] for path in list(required) + list(list_scalar_fields(role))}
    scientific_fields = required_fields | {path.split('/')[1] for path in optional}
    if not scientific_fields.intersection(output):
        return None
    with contract_check('review.staged_final'):
        fields(output, required_fields, optional=None, code='invalid_review_staged', path='')
        return project_ordinary_final(output)[0]


def _validate_staged_pages(records, assignment, epoch, task, output):
    """Separate unavailable frozen page scope from output that changes its assigned task or ownership."""
    from .review_staged_protocol import validate_answer_pages, validate_composition_task
    plan = records['review_composition_plan'][assignment['composition_plan_id']]
    validate_composition_task(epoch, task, plan)
    with contract_check('review.staged_page'):
        validate_answer_pages(task, output, plan)


def classify_observed_output(records, artifacts, attempt_id):
    """Return the first pure failure of an authenticated attempt's own saved output."""
    from . import review_protocol as protocol
    from .review_protocol import authenticate_attempt, validate_adjudication_contract
    from .research_decisions import validate_review_contract
    attempt = records.get('review_attempt', {}).get(attempt_id)
    if attempt is None or attempt.get('status') != 'completed':
        return None
    assignment = records.get('review_assignment', {}).get(attempt['assignment_id'])
    if assignment is None or not authenticate_attempt(records, artifacts, assignment, attempt):
        return None
    try:
        output = protocol.read_raw_observation(records, artifacts, attempt_id)['raw_output']
        packet = json.loads(artifacts.read(assignment['packet']))
        staged = packet.get('staged_review')
        if staged is not None:
            from .review_staged import validate_task_output
            binding = assignment.get('staged_review', {})
            task = assignment.get('staged_task', staged.get('task'))
            epoch = records.get('review_epoch', {}).get(binding.get('epoch_id'), staged.get('epoch', {}))
            with contract_check('review.staged_task'):
                validate_task_output(task, output, manifest=epoch.get('manifest'))
            if task['type'] == 'answer':
                _validate_staged_pages(records, assignment, epoch, task, output)
            if task['type'] == 'final':
                scientific = _project_staged_final(assignment['role'], output)
                if scientific is not None:
                    _validate_staged_final(records, artifacts, assignment, packet, epoch, scientific)
        elif assignment['role'] in ('bar', 'slate', 'result'):
            validate_review_contract(records, artifacts, assignment, output)
        elif assignment['role'] == 'adjudicator':
            packet = json.loads(artifacts.read(assignment['packet']))
            validate_adjudication_contract(records, artifacts, assignment, packet, output)
        elif assignment['role'] == 'native_math':
            from .native_review import validate_output
            from .openness_reading import (INVALID_RESPONSE, PASSAGE_PROGRESS_ERR_MSG, find_stage,
                                           has_deliverable_passage, validate_turn_output)
            with contract_check('native.output'):
                packet = json.loads(artifacts.read(assignment['packet']))
                if find_stage(packet) is None:
                    validate_output(packet['native'], output)
                else:
                    validate_turn_output(packet, output)
            # Reading the bound bodies stays outside the contract check: an unavailable body is not an output failure.
            if not has_deliverable_passage(records, artifacts, packet, output):
                with contract_check('native.output'):
                    raise ResearchError(INVALID_RESPONSE, PASSAGE_PROGRESS_ERR_MSG, {'path': '/passage_requests'})
    except ResearchError as error:
        check = getattr(error, 'contract_check', None)
        if check is not None:
            return {'code': error.code, 'details': dict(error.details or {}, message=error.message), 'check': check}
    except (ValueError, TypeError, KeyError):
        # Unavailable/malformed frozen inputs are not an output-contract finding.
        return None
    return None


def refused_attempts(records, artifacts, completed):
    """Recheck certificates against each exact observation, never effective assignment state."""
    result = []
    for attempt in completed:
        if attempt['id'] in records.get('review_output_refusal', {}):
            failure = classify_observed_output(records, artifacts, attempt['id'])
            if failure is not None:
                result.append({'attempt_id': attempt['id'], 'code': failure['code']})
    return result


def record_refusal_on_error(record_function):
    """Keep command tables unchanged and preserve the recording call's original exception."""
    @wraps(record_function)
    def wrapped(store, payload, *, expected_revision, request_id):
        try:
            return record_function(store, payload, expected_revision=expected_revision, request_id=request_id)
        except ResearchError:
            try:
                _record_refusal(store, payload, expected_revision=expected_revision, request_id=request_id + ':refusal')
            except (_CheckEffectRequired, ResearchError, ValueError, TypeError, KeyError):
                # A stale revision or unavailable provenance cannot change the original refusal.
                pass
            raise
    return wrapped


def record_native_refusal(store, assignment_id, *, request_id):
    """Record the refusal of a native review output when the run completes: the native controller records the review
    in another program, so the run is the harness's only chance to refuse it and let the assignment run again."""
    try:
        _record_refusal(store, {'assignment_id': assignment_id}, expected_revision=store.revision,
                        request_id=request_id + ':refusal')
    except (ResearchError, ValueError, TypeError, KeyError):
        # A stale revision or unavailable provenance leaves the completed run as it was.
        pass


def _record_refusal(store, payload, *, expected_revision, request_id):
    from .review_protocol import assignment_state
    artifacts = store.build_artifact_store()
    snapshot = store.snapshot()
    records = snapshot['records']
    assignment_id = payload.get('assignment_id') if isinstance(payload, dict) else None
    if not isinstance(assignment_id, str) or assignment_id not in records.get('review_assignment', {}):
        return
    state = assignment_state(records, artifacts, assignment_id)
    attempt_id = state['attempt_id']
    if attempt_id is None or attempt_id in records.get('review_output_refusal', {}):
        return
    failure = classify_observed_output(records, artifacts, attempt_id)
    if failure is None:
        return
    attempt = records['review_attempt'][attempt_id]
    value = dict(id=attempt_id, assignment_id=assignment_id, attempt_id=attempt_id,
                 output_digest=digest(json.loads(artifacts.read(attempt['output']))),
                 request_digest=attempt['request_digest'], protocol=PROTOCOL, **failure)

    def prepare(current, submitted):
        existing = current.get('review_output_refusal', {}).get(attempt_id)
        if existing is not None:
            return [], existing
        record = dict(submitted, recorded_revision=expected_revision + 1)
        return [immutable_record(current, 'review_output_refusal', attempt_id, record)], record
    return prepared_mutation(store, 'review.refusal', value, prepare,
                             expected_revision=expected_revision, request_id=request_id)
