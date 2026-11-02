"""Durable full-text transfer and PDF extraction jobs on the research store."""

import copy
import hashlib
import errno
import time

from . import acquisition, resources
from .artifacts import ArtifactStore
from .errors import ResearchError
from .evidence import digest, derived_id, prepare_sources, put_sources
from .extraction_units import ExtractionEngine, PROTOCOL, _SupervisedTools, _hold_owner
from .finishing import retry_store_write
from .fulltext import PdfExtractor, GENERIC_BINARY_MEDIA_TYPES, parse_base_media_type
from .fulltext_transfer import FulltextTransfer
from .http import HttpClient, HttpFailure, RequestBudget, safe_url
from .identities import normalize_identifier, version_of
from .ocr import OcrPdfExtractor, run_bounded
from .operations import fields, is_finite_number, text
from .source_links import exact_work, captured_source


DEFAULT_ALLOWANCES = {
    'original_bytes': 512 * 1024 * 1024, 'retained_bytes': 4 * 1024 * 1024 * 1024,
    'text_bytes': 256 * 1024 * 1024, 'network_attempts': 100,
    'invocation_seconds': 120, 'unit_seconds': 30, 'unit_output_bytes': 32 * 1024 * 1024,
    'unit_image_bytes': 32 * 1024 * 1024, 'pages_per_resume': 20, 'range_bytes': 8 * 1024 * 1024,
}


def validate_allowances(value, previous=None):
    fields(value, (), tuple(DEFAULT_ALLOWANCES))
    result = dict(DEFAULT_ALLOWANCES if previous is None else previous)
    for key, amount in value.items():
        valid = is_finite_number(amount) if key.endswith('seconds') else type(amount) is int
        if not valid or amount <= 0 or previous is not None and amount < previous[key]:
            raise ResearchError('invalid_input', 'Allowances must be positive, finite and cannot decrease', {'allowance': key})
        result[key] = amount
    return result


def read_job(store, job_id):
    text(job_id, 'Job ID')
    scope = store._read_scoped_preparation()
    job = scope.records.get('fulltext_job', {}).get(job_id)
    if job is None:
        raise ResearchError('unknown_fulltext_job', 'No full-text job has this identifier', {'job_id': job_id})
    return scope, copy.deepcopy(job)


def commit_job(store, job_id, operation, payload, request_id, apply):
    def write():
        scope, job = read_job(store, job_id)
        def commit(transaction, revision):
            return apply(transaction, job, revision)
        return store.mutate_scoped(operation, payload, commit, request_id=request_id,
                                   _preparation=scope.release_proof())['result']
    # A transfer or extraction already spent its network requests and work; a refused write of its record is tried
    # again instead of fetching or extracting again.
    return retry_store_write(write, codes=('store_busy', 'stale_revision', 'storage_io'))


def build_unit_key(job_id, stage, ordinal):
    return derived_id(job_id, stage, ordinal)


def read_unit(records, job_id, stage, ordinal):
    key = build_unit_key(job_id, stage, ordinal)
    record = records.get('fulltext_unit', {}).get(key)
    if (record is None or record.get('id') != key or record.get('job_id') != job_id
            or record.get('stage') != stage or record.get('ordinal') != ordinal):
        raise ResearchError('record_conflict', 'Committed full-text unit is missing or has changed identity', {'unit_id': key})
    return copy.deepcopy(record['unit'])


def charge_attempts(transaction, job, attempts, artifacts):
    sources = prepare_sources(artifacts, attempts, 'fulltext', job['id'],
                              start=job['retained_attempts'], requested_identifier=job['identifier'])
    # Streamed attempts already contain descriptors; prepare_sources performs no artifact I/O.
    put_sources(transaction, sources)
    retained = 0
    for attempt, source in zip(attempts, sources):
        ordinal = job['retained_attempts']
        key = build_unit_key(job['id'], 'attempt', ordinal)
        transaction.put('fulltext_unit', key, {'id': key, 'job_id': job['id'], 'stage': 'attempt', 'ordinal': ordinal,
                                              'unit': {'kind': 'attempt', 'attempt': attempt, 'source_id': source['id'],
                                                       'request_ordinal': job['next_response_request']}})
        job['retained_attempts'] += 1
        job['next_response_request'] += 1
        retained += (attempt.get('response') or {}).get('size', 0)
    job['retained_bytes'] += retained
    change = resources.charge(transaction, 'literature', {'source_bytes': retained}, refuse=False)
    if change is not None:
        transaction.put(*change)


class JobUnits:
    def __init__(self, store):
        self.store = store

    def read_units(self, job_id):
        scope, job = read_job(self.store, job_id)
        return [read_unit(scope.records, job_id, 'transfer', i) for i in range(job['transfer_units'])]

    def read_committed_units(self, job_id):
        scope, job = read_job(self.store, job_id)
        count = job['completed_pages'] + 1 if job['page_count'] is not None else 0
        return [read_unit(scope.records, job_id, 'extraction', i) for i in range(count)]

    def commit_unit(self, job_id, unit):
        stage = 'extraction' if unit['kind'] in ('manifest', 'page') else 'transfer'
        scope, job = read_job(self.store, job_id)
        ordinal = (0 if unit['kind'] == 'manifest' else unit.get('page')) if stage == 'extraction' else job['transfer_units']
        if stage == 'transfer':
            # An acknowledged commit may have lost its response. Authenticate and reuse it.
            for i in range(job['transfer_units']):
                if read_unit(scope.records, job_id, stage, i).get('id') == unit['id']:
                    if read_unit(scope.records, job_id, stage, i) != unit:
                        raise ResearchError('record_conflict', 'A committed transfer unit changed')
                    return
        key = build_unit_key(job_id, stage, ordinal)
        record = {'id': key, 'job_id': job_id, 'stage': stage, 'ordinal': ordinal, 'unit': copy.deepcopy(unit)}
        if stage == 'transfer':
            record['attempt_start'] = job['retained_attempts']
        def apply(transaction, current, revision):
            old = transaction.get('fulltext_unit', key)
            if old is not None:
                if old != record:
                    raise ResearchError('record_conflict', 'An immutable full-text unit changed')
                return {'unit_id': key}
            if stage == 'transfer':
                before = current['retained_bytes']
                charge_attempts(transaction, current, unit['attempts'], ArtifactStore(self.store.root))
                current['transfer_retained_bytes'] += current['retained_bytes'] - before
                current['transfer_units'] += 1
                current['transferred_bytes'] += unit['artifact']['size']
            elif unit['kind'] == 'manifest':
                current['page_count'] = unit['page_count']
            else:
                if ordinal != current['completed_pages'] + 1:
                    raise ResearchError('record_conflict', 'Extraction pages must commit in order')
                current['completed_pages'] += 1
                current['text_bytes'] += unit['text']['size'] + 1
                current['retained_bytes'] += unit['text']['size']
            transaction.put('fulltext_unit', key, record)
            current['next_unit'] = ('page:' + str(current['completed_pages'] + 1) if current['original'] else
                                    'byte:' + str(current['transferred_bytes']))
            transaction.put('fulltext_job', job_id, current)
            return {'unit_id': key}
        return commit_job(self.store, job_id, 'fulltext.unit', {'record': record}, key, apply)


class JobBudget(RequestBudget):
    def __init__(self, store, job_id, request_id, maximum):
        super().__init__(maximum)
        self.store, self.job_id, self.request_id = store, job_id, request_id

    def consume(self):
        self.check()
        def charge(transaction, job, revision):
            ordinal = job['attempts_used']
            key = build_unit_key(self.job_id, 'request', ordinal)
            transaction.put('fulltext_unit', key, {'id': key, 'job_id': self.job_id, 'stage': 'request', 'ordinal': ordinal,
                                                  'unit': {'kind': 'attempt_started', 'operation_id': self.request_id}})
            job['attempts_used'] += 1
            transaction.put('fulltext_job', self.job_id, job)
            operation = transaction.get('acquisition_operation', self.request_id)
            operation['payload']['max_requests'] = max(0, operation['payload']['max_requests'] - 1)
            transaction.put('acquisition_operation', self.request_id, operation)
            change = resources.charge(transaction, 'literature', {'network_requests': 1}, refuse=False)
            if change is not None:
                transaction.put(*change)
            return {}
        commit_job(self.store, self.job_id, 'fulltext.request', {'operation_id': self.request_id, 'ordinal': self.used},
                   derived_id(self.request_id, 'network', self.used), charge)
        self.used += 1


class JobExtractor:
    def __init__(self, store, job_id, extractor):
        self.store, self.job_id, self.extractor = store, job_id, extractor
        self.name, self.timeout = extractor.name, extractor.timeout

    def get_page_options(self):
        return self.extractor.get_page_options()

    def build_runner(self, run):
        def bounded(argv, limit, deadline, *, include_stderr=False):
            _, job = read_job(self.store, self.job_id)
            worker = self.store.root / '.exactory/fulltext-workers' / digest(self.job_id)
            # Committed page scratch is removed by ExtractionEngine. Account for
            # unfinished outputs too before a replacement worker launches a tool.
            scratch = sum(path.stat().st_size for path in worker.rglob('tool-output')) if worker.exists() else 0
            available = job['allowances']['retained_bytes'] - job['retained_bytes'] - 2 * job['original']['size'] - 2 * scratch
            cap = min(limit, max(0, available // 4))
            if isinstance(run, _SupervisedTools):
                operation = run.directory + '/' + digest(run.scope) + '/' + str(run.cursor)
                previous = run._read_optional(operation + '/config.json')
                if previous is not None:
                    # A recovered invocation consumes its persisted output, whose
                    # original cap was admitted before these scratch bytes existed.
                    cap = previous['limit']
                    if cap > limit:
                        raise ResearchError('record_conflict', 'Retained extraction tool allowance changed')
            if cap == 0:
                raise ResearchError('fulltext_storage_limit', 'No storage remains for this extraction tool unit')
            try:
                return (run or run_bounded)(argv, cap, deadline, include_stderr=include_stderr)
            except ResearchError as error:
                if cap < limit and error.code == 'extraction_too_large':
                    error.details = dict(error.details or {})
                    error.details['limit'] = dict(error.details.get('limit', {}), retained_bytes=job['allowances']['retained_bytes'])
                raise
        return bounded

    def read_page_tool_versions(self, *, run=None, deadline=None):
        return self.extractor.read_page_tool_versions(run=self.build_runner(run), deadline=deadline)

    def count_pages(self, original, *, run=None, deadline=None):
        return self.extractor.count_pages(original, run=self.build_runner(run), deadline=deadline)

    def extract_page(self, original, page, *, run=None, deadline=None):
        return self.extractor.extract_page(original, page, run=self.build_runner(run), deadline=deadline)


class JobArtifacts:
    """Enforce job output and scratch bounds before the extraction engine writes."""
    def __init__(self, store, job_id):
        self.store, self.job_id = store, job_id
        self.artifacts = ArtifactStore(store.root)

    def check_space(self, additional, *, text_bytes=None):
        _, job = read_job(self.store, self.job_id)
        limits = job['allowances']
        if text_bytes is not None and text_bytes > limits['text_bytes']:
            raise ResearchError('fulltext_unit_limit', 'Extraction exceeds the job text allowance',
                                {'limit': limits['text_bytes'], 'observed_bytes': text_bytes})
        if job['retained_bytes'] + additional > limits['retained_bytes']:
            raise ResearchError('fulltext_storage_limit', 'Extraction scratch and retained artifacts exceed the job allowance')

    def put(self, data, media_type):
        _, job = read_job(self.store, self.job_id)
        self.check_space(len(data) * 2 + job['original']['size'] * 2,
                         text_bytes=job['text_bytes'] + len(data) + 1)
        return self.artifacts.put(data, media_type)

    def put_file(self, path, media_type):
        size = path.stat().st_size
        self.check_space(size * 2, text_bytes=size)
        return self.artifacts.put_file(path, media_type)

    def open_verified(self, reference):
        _, job = read_job(self.store, self.job_id)
        assembly = 2 * job['text_bytes'] if job['page_count'] == job['completed_pages'] else 0
        self.check_space(reference['size'] + assembly)
        return self.artifacts.open_verified(reference)

    def materialize_verified(self, reference, directory):
        self.check_space(reference['size'] * 2)
        return self.artifacts.materialize_verified(reference, directory)


def start_job(store, identifier, *, request_id, expected_revision, url=None, source_id=None,
              extraction_options=None, allowances=None, use_ranges=True):
    identifier = normalize_identifier(identifier)
    options = acquisition._extraction_options(extraction_options)
    limits = validate_allowances({} if allowances is None else allowances)
    if (url is None) == (source_id is None) or type(use_ranges) is not bool:
        raise ResearchError('invalid_input', 'Supply exactly one URL or retained source ID and a boolean transfer mode')
    url = safe_url(url) if url is not None else None
    payload = dict(identifier=identifier, url=url, source_id=source_id, extraction_options=options,
                   allowances=limits, use_ranges=use_ranges)
    job_id = 'fulltext:' + digest([request_id, payload])
    if store.committed_request(request_id) is not None:
        return store.mutate('fulltext.start', payload, lambda tx: None, request_id=request_id,
                            expected_revision=expected_revision)['result']
    scope = store._read_scoped_preparation()
    work = exact_work(scope.records, identifier)
    original = None
    if source_id is not None:
        source = captured_source(scope.records, ArtifactStore(store.root), source_id)
        captures = [c for c in work['fulltexts'] if c.get('source_id') == source_id]
        prior_job = scope.records.get('fulltext_job', {}).get(source.get('job_id')) if source.get('job_id') else None
        if (source.get('origin_verified') is not True or source.get('capture_method') not in ('http', 'http_range_v1')
                or source.get('observed_identifier') != identifier or source.get('requested_identifier') != identifier
                or not (any(c.get('original') == source['response'] for c in captures)
                        or prior_job is not None and prior_job['identifier'] == identifier
                        and prior_job['original'] == source['response'] and prior_job['source_id'] == source_id)):
            raise ResearchError('source_pending', 'Retained extraction requires a complete original of the exact version')
        original = source['response']
        if original['size'] > limits['retained_bytes']:
            raise ResearchError('fulltext_storage_limit', 'Retained original exceeds the job storage allowance')
        if original['size'] > limits['original_bytes']:
            raise ResearchError('response_too_large', 'Retained original exceeds the job original-byte allowance')
    job = dict(id=job_id, protocol=PROTOCOL, identifier=identifier, version=version_of(identifier),
               url=url, source_id=source_id, original=original, transfer=None, assembly_id=None,
               options=options, use_ranges=use_ranges, tool_versions=None, allowances=limits,
               transfer_units=0, transferred_bytes=0, transfer_retained_bytes=0, completed_pages=0, page_count=None,
               text_bytes=0, retained_bytes=original['size'] if original else 0, attempts_used=0, retained_attempts=0,
               status='pending', pending=[{'code': 'fulltext_job_pending'}],
               next_unit='page:1' if original else 'byte:0', capture=None)
    def admission(transaction, revision):
        transaction.put('fulltext_job', job_id, job)
        return dict(job_id=job_id, status='pending', pending=job['pending'], revision=revision)
    return store.mutate_scoped('fulltext.start', payload, admission, request_id=request_id,
                               _preparation=scope.release_proof(), expected_revision=expected_revision)['result']


def read_job_status(store, job_id, *, cursor=0, limit=30):
    if type(cursor) is not int or cursor < 0 or type(limit) is not int or not 1 <= limit <= 100:
        raise ResearchError('invalid_input', 'Unit cursor must be nonnegative and limit must be 1 to 100')
    scope, job = read_job(store, job_id)
    counts = [('transfer', job['transfer_units']), ('extraction', job['completed_pages'] + 1 if job['page_count'] else 0),
              ('attempt', job['retained_attempts']), ('request', job['attempts_used'])]
    total, details = sum(n for _, n in counts), []
    offset = 0
    for stage, count in counts:
        for i in range(max(0, cursor - offset), min(count, cursor + limit - offset)):
            details.append(dict(stage=stage, ordinal=i, unit=read_unit(scope.records, job_id, stage, i)))
        offset += count
    return dict(job, job_id=job_id, revision=scope.revision, units=details, total_units=total,
                next_cursor=cursor + len(details) if cursor + len(details) < total else None,
                next_action='complete' if job['status'] == 'complete' else 'fulltext-resume')


def resume_job(store, job_id, *, request_id, expected_revision, max_requests=None, max_pages=None,
               allowances=None, reason=None, http=None):
    payload = dict(job_id=job_id, max_requests=max_requests, max_pages=max_pages, allowances=allowances, reason=reason)
    if store.committed_request(request_id) is not None:
        return acquisition._begin(store, 'fulltext.resume', payload, request_id, expected_revision, target=job_id)
    with acquisition.hold_operation(store, job_id, request_id):
        _, initial = read_job(store, job_id)
        with acquisition.hold_operation(store, initial['identifier'], request_id):
            limits = validate_allowances({} if allowances is None else allowances, initial['allowances'])
            if allowances:
                text(reason, 'Allowance change reason')
            if max_requests is not None and (type(max_requests) is not int or max_requests < 0):
                raise ResearchError('invalid_input', 'max_requests must be a nonnegative integer')
            maximum = min(limits['network_attempts'] - initial['attempts_used'],
                          limits['network_attempts'] if max_requests is None else max_requests)
            if initial['original'] is not None:
                maximum = 0
            pages = limits['pages_per_resume'] if max_pages is None else max_pages
            if type(pages) is not int or not 1 <= pages <= limits['pages_per_resume']:
                raise ResearchError('invalid_input', 'max_pages must fit the recorded invocation allowance')
            def admission(transaction):
                current = transaction.get('fulltext_job', job_id)
                current['allowances'] = limits
                current['active_operation'] = request_id
                current['next_response_request'] = current['attempts_used']
                transaction.put('fulltext_job', job_id, current)
            # Reserve only the bounded invocation, while every committed attempt charges independently.
            # Keep the caller's payload stable for replay, including its optional invocation bound.
            replay = acquisition._begin(store, 'fulltext.resume', payload, request_id, expected_revision,
                                        target=job_id, apply=admission, reserved_requests=maximum)
            if replay is not None:
                return replay
            units, artifacts = JobUnits(store), ArtifactStore(store.root)
            _, initial = read_job(store, job_id)
            limits, pending, capture = initial['allowances'], [], initial['capture']
            started = time.monotonic()
            try:
                if initial['status'] != 'complete' and initial['original'] is None:
                    client = http or HttpClient()
                    try:
                        transfer = FulltextTransfer(client, artifacts, units).run(job_id, initial['url'],
                            max_original_bytes=limits['original_bytes'], max_retained_bytes=limits['retained_bytes'],
                            range_bytes=limits['range_bytes'], total_timeout=limits['invocation_seconds'],
                            budget=JobBudget(store, job_id, request_id, maximum), retained_bytes=max(0, initial['retained_bytes'] - initial['transfer_retained_bytes']),
                            use_ranges=initial['use_ranges'])
                    except HttpFailure as error:
                        _, current = read_job(store, job_id)
                        tail = error.attempts[current['retained_attempts'] - initial['retained_attempts']:]
                        if tail:
                            def retain(transaction, job, revision):
                                charge_attempts(transaction, job, tail, artifacts)
                                transaction.put('fulltext_job', job_id, job)
                                return {}
                            commit_job(store, job_id, 'fulltext.attempts', {'attempts': tail},
                                       derived_id(request_id, 'attempts'), retain)
                        raise
                    # A requested identifier never substitutes for an observed exact version.
                    observed = normalize_identifier(transfer['url'])
                    if observed != initial['identifier'] or observed.startswith('arxiv:') and version_of(observed) is None:
                        raise ResearchError('version_mismatch', 'Full-text destination differs from the exact job version')
                    def retain_original(transaction, job, revision):
                        source_id = derived_id(job_id, 'representation')
                        source = dict(transfer, id=source_id, provider='fulltext', operation_id=request_id, job_id=job_id,
                                      status='captured', requested_identifier=job['identifier'], observed_identifier=observed,
                                      exact_version=job['version'], captured_at=client.clock.now(), error=None)
                        source.pop('attempts', None)
                        job.update(original=transfer['response'], transfer=source, source_id=source_id)
                        if transfer['capture_method'] == 'http_range_v1':
                            assembly_id = derived_id(job_id, 'assembly')
                            assembly = dict(id=assembly_id, job_id=job_id, original=transfer['response'],
                                            requested_url=job['url'], url=transfer['url'], etag=transfer['etag'],
                                            encoding='identity', total=transfer['total'], unit_count=job['transfer_units'],
                                            identifier=observed)
                            transaction.put('source_assembly', assembly_id, assembly)
                            source['assembly_id'] = assembly_id
                            job['assembly_id'] = assembly_id
                            job['retained_bytes'] += transfer['response']['size']
                        transaction.put('source', source_id, source)
                        transaction.put('fulltext_job', job_id, job)
                        return {}
                    commit_job(store, job_id, 'fulltext.original', {'original': transfer['response']},
                               derived_id(job_id, 'original'), retain_original)
                _, current = read_job(store, job_id)
                if current['status'] != 'complete':
                    with JobArtifacts(store, job_id).open_verified(current['original']) as frozen:
                        if not frozen.stream.read(5).startswith(b'%PDF-'):
                            raise ResearchError('unsupported_media_type', 'Page extraction requires a complete PDF original')
                    remaining = limits['invocation_seconds'] - (time.monotonic() - started)
                    if remaining <= 0:
                        raise ResearchError('fulltext_job_pending', 'Invocation allowance ended before extraction')
                    extractor = (OcrPdfExtractor(timeout=limits['unit_seconds'], max_text_bytes=limits['unit_output_bytes'],
                                                max_image_bytes=limits['unit_image_bytes']) if current['options'].get('ocr') else
                                 PdfExtractor(timeout=limits['unit_seconds'], max_text_bytes=limits['unit_output_bytes'],
                                              layout=current['options'].get('layout', True)))
                    extractor = JobExtractor(store, job_id, extractor)
                    if current['tool_versions'] is None:
                        version_root = store.root / '.exactory/fulltext-version-workers'
                        relative = digest(job_id)
                        with _hold_owner(version_root, relative) as owner:
                            if owner is None:
                                raise ResearchError('fulltext_job_pending', 'The prior tool-version worker has not terminated')
                            runner = _SupervisedTools(version_root, relative + '/runs', owner,
                                                      {'job_id': job_id, 'original': current['original']}, limits['unit_seconds'])
                            runner.begin_scope('versions')
                            try:
                                versions = extractor.read_page_tool_versions(run=runner, deadline=time.monotonic() + remaining)
                            except ResearchError as error:
                                if error.code in ('extraction_too_large', 'extraction_timeout', 'extraction_failed', 'extractor_missing'):
                                    runner.discard_scope()
                                raise
                            runner.discard_scope()
                        def pin_tools(transaction, job, revision):
                            job['tool_versions'] = versions
                            transaction.put('fulltext_job', job_id, job)
                            return {}
                        commit_job(store, job_id, 'fulltext.tools', {'versions': versions}, derived_id(job_id, 'tools'), pin_tools)
                        current['tool_versions'] = versions
                    capture = ExtractionEngine(JobArtifacts(store, job_id), units, store.root / '.exactory/fulltext-workers', extractor).resume(
                        job_id, original=current['original'], work_id=current['identifier'], version=current['version'],
                        tool_versions=current['tool_versions'], options=extractor.get_page_options(), max_pages=pages,
                        max_seconds=max(0.001, limits['invocation_seconds'] - (time.monotonic() - started)))
                    if capture['availability'] == 'available':
                        if parse_base_media_type(current['original']['media_type']) in GENERIC_BINARY_MEDIA_TYPES:
                            capture['extraction']['format_detection'] = 'pdf_signature_from_generic_binary'
                        capture.update(source_id=current['source_id'], url=(current['transfer'] or {}).get('url') or
                                       store._read_scoped_preparation().records['source'][current['source_id']]['url'])
                    else:
                        pending = [capture['pending']]
                        if 'retained_bytes' in pending[0].get('limit', {}):
                            pending[0]['code'] = 'fulltext_storage_limit'
                        capture = None
            except ResearchError as error:
                code = error.code
                if ('retained_bytes' in (error.details or {}).get('limit', {})
                        or code == 'storage_io' and (error.details or {}).get('errno') in (errno.ENOSPC, errno.EDQUOT)):
                    code = 'fulltext_storage_limit'
                pending = [dict(error.details or {}, code=code)]
            def finish(transaction, job, revision):
                if capture is not None and job['status'] != 'complete':
                    work = transaction.get('work', job['identifier'])
                    if job['transfer'] is None:
                        # A fresh source identity lets links select the appended extraction without
                        # changing the retained legacy capture or its historical readings.
                        source = transaction.get('source', job['source_id'])
                        source = dict(source, id=derived_id(job_id, 'retained-extraction'))
                        transaction.put('source', source['id'], source)
                        capture['source_id'] = source['id']
                    work['fulltexts'].append(capture)
                    transaction.put('work', job['identifier'], work)
                    job.update(status='complete', capture=capture, next_unit=None)
                    job['retained_bytes'] += capture['text']['size']
                job['pending'] = pending
                transaction.put('fulltext_job', job_id, job)
                result = dict(job_id=job_id, status=job['status'], pending=pending, completed_pages=job['completed_pages'],
                              page_count=job['page_count'], attempts_used=job['attempts_used'], next_unit=job['next_unit'], revision=revision)
                operation = transaction.get('acquisition_operation', request_id)
                operation.update(state='finished', result=result)
                transaction.put('acquisition_operation', request_id, operation)
                return result
            return commit_job(store, job_id, 'fulltext.finish', {'request_id': request_id, 'pending': pending},
                               derived_id(request_id, 'finish'), finish)


def validate_assembly(records, artifacts, source):
    """Verify the exact assembled representation and all committed range provenance."""
    from .evaluation import Evaluation
    while isinstance(artifacts, Evaluation):
        artifacts = artifacts.store
    assembly = records.get('source_assembly', {}).get(source.get('assembly_id'))
    def require(condition):
        if not condition:
            raise ResearchError('source_pending', 'Full-text assembly provenance is incomplete or inconsistent')
    require(assembly is not None)
    fields(assembly, ('id', 'job_id', 'original', 'requested_url', 'url', 'etag', 'encoding',
                      'total', 'unit_count', 'identifier'), code='source_pending')
    job_id = assembly['job_id']
    require(assembly.get('id') == source.get('assembly_id') and assembly.get('original') == source.get('response')
            and assembly.get('identifier') == source.get('observed_identifier') == source.get('requested_identifier')
            and source.get('response_complete') is True and source.get('origin_verified') is True
            and source.get('http_status') is None and source.get('content_scope') == 'representation'
            and assembly.get('encoding') == 'identity' and assembly.get('total') == source['response']['size']
            and assembly.get('url') == source.get('url') and type(assembly.get('unit_count')) is int
            and assembly['unit_count'] > 0)
    require(normalize_identifier(assembly['url']) == assembly['identifier']
            and source.get('exact_version') == version_of(assembly['identifier']))
    from .fulltext_transfer import _STRONG_ETAG, _CONTENT_RANGE
    require(isinstance(assembly.get('etag'), str) and _STRONG_ETAG.fullmatch(assembly['etag']) is not None)
    cursor, hasher = 0, hashlib.sha256()
    for i in range(assembly['unit_count']):
        unit = read_unit(records, job_id, 'transfer', i)
        require(unit.get('id') == digest([job_id, {key: value for key, value in unit.items() if key != 'id'}]))
        require(unit.get('kind') == 'range' and type(unit.get('start')) is int and type(unit.get('end')) is int
                and unit.get('start') == cursor and unit.get('end', -1) >= cursor
                and unit.get('end', -1) < assembly['total'] and unit.get('total') == assembly['total']
                and unit.get('url') == assembly['url'] and unit.get('requested_url') == assembly['requested_url']
                and unit.get('etag') == assembly['etag'] and unit.get('encoding') == 'identity'
                and unit['artifact']['size'] == unit['end'] - cursor + 1 and bool(unit.get('attempts')))
        record = records['fulltext_unit'][build_unit_key(job_id, 'transfer', i)]
        require(type(record.get('attempt_start')) is int)
        for index, embedded in enumerate(unit['attempts'], record['attempt_start']):
            retained = read_unit(records, job_id, 'attempt', index)
            require(retained['attempt'] == embedded and retained['source_id'] == derived_id(job_id, 'source', index))
            started = read_unit(records, job_id, 'request', retained['request_ordinal'])
            require(started['kind'] == 'attempt_started')
            native = records.get('source', {}).get(retained['source_id'])
            require(native is not None and native.get('response') == embedded.get('response')
                    and native.get('headers') == embedded.get('headers') and native.get('url') == embedded.get('url')
                    and native.get('requested_identifier') == assembly['identifier']
                    and native.get('http_status') == embedded.get('status')
                    and native.get('response_complete') == embedded.get('complete')
                    and native.get('captured_at') == embedded.get('captured_at'))
        attempt = unit['attempts'][-1]
        headers = attempt.get('headers', {})
        matched = _CONTENT_RANGE.fullmatch(headers.get('content-range', ''))
        require(matched is not None)
        try:
            coverage = tuple(map(int, matched.groups()))
            length = int(headers.get('content-length', str(unit['artifact']['size'])))
        except (TypeError, ValueError) as error:
            raise ResearchError('source_pending', 'Full-text range headers are invalid') from error
        require(coverage == (cursor, unit['end'], assembly['total']) and length == unit['artifact']['size'])
        require(attempt.get('response') == unit['artifact'] and attempt.get('status') == 206
                and attempt.get('complete') is True and attempt.get('error') is None
                and attempt.get('url') == assembly['url']
                and attempt.get('headers', {}).get('etag') == assembly['etag']
                and attempt.get('headers', {}).get('content-encoding', 'identity').lower() == 'identity')
        with artifacts.open_verified(unit['artifact']) as frozen:
            for block in iter(lambda: frozen.stream.read(65536), b''):
                hasher.update(block)
        cursor = unit['end'] + 1
    require(cursor == assembly['total'] and hasher.hexdigest() == source['response']['sha256'])
    artifacts.verify(source['response'])
    return assembly
