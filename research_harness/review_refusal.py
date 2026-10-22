"""Authenticated output-contract refusals and bounded recovery audit records."""

import json
from contextlib import contextmanager
from functools import wraps

from .artifacts import ArtifactStore, validate_reference
from .errors import ResearchError
from .evidence import digest
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
    if link['artifact']['media_type'] == 'application/octet-stream':
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
                         and artifact['media_type'].split(';', 1)[0].strip() == 'text/plain']
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
        # Missing bytes are evidence-availability failures, before any locator check.
        try:
            artifacts.read(link['artifact'])
            kind = link['locator'].get('kind') if isinstance(link['locator'], dict) else None
            capture = _frozen_pdf_capture(link, packets) if kind == 'pdf' else None
            if (kind == 'pdf' and capture is None
                    and link['artifact']['media_type'] in ('application/pdf', 'application/octet-stream')):
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


def evidence_arrays(output):
    value = output['value']
    return [value['evidence']] + [row['evidence'] for row in (
        value['assurances'] + value['work_items'] + value['objection_findings'] + output['objections']
        + output.get('reassessment', {}).get('findings', []))]


def classify_observed_output(records, artifacts, attempt_id):
    """Return the first pure failure of an authenticated attempt's own saved output."""
    from .review_protocol import authenticate_attempt, validate_adjudication_contract
    from .research_decisions import validate_review_contract
    attempt = records.get('review_attempt', {}).get(attempt_id)
    if attempt is None or attempt.get('status') != 'completed':
        return None
    assignment = records.get('review_assignment', {}).get(attempt['assignment_id'])
    if assignment is None or not authenticate_attempt(records, artifacts, assignment, attempt):
        return None
    try:
        output = json.loads(artifacts.read(attempt['output']))
        if assignment['role'] in ('bar', 'slate', 'result'):
            validate_review_contract(records, artifacts, assignment, output)
        elif assignment['role'] == 'adjudicator':
            packet = json.loads(artifacts.read(assignment['packet']))
            validate_adjudication_contract(records, artifacts, assignment, packet, output)
        elif assignment['role'] == 'native_math':
            from .native_review import validate_output
            with contract_check('native.output'):
                validate_output(json.loads(artifacts.read(assignment['packet']))['native'], output)
    except ResearchError as error:
        check = getattr(error, 'contract_check', None)
        if check is not None:
            return {'code': error.code, 'details': error.details, 'check': check}
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
            except (ResearchError, ValueError, TypeError, KeyError):
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
    artifacts = ArtifactStore(store.root)
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
