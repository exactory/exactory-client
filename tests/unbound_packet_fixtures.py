"""Open copies of synthetic stores written by release 0.87.0, without git history."""

import copy
import hashlib
import json
from pathlib import Path
import tarfile
import tempfile
from unittest.mock import patch

from codex_route_fixtures import install_codex_runtime, limit_codex_input
from capacity_evidence_fixtures import build_capacity_prior, build_capacity_runtime
from openness_reading_fixtures import OpennessReadingCase, INPUT_LIMIT_CHARACTERS
from research_harness.artifacts import ArtifactStore
from research_harness.storage import Store
from research_harness import review_capacity_evidence, review_route_capacity, review_codex_transport

FIXTURE_DIRECTORY = Path(__file__).parent / 'fixtures/openness_0870_choice'


def copy_stored_store(owner, name):
    metadata = json.loads((FIXTURE_DIRECTORY / 'manifest.json').read_text())['stores'][name]
    temporary = tempfile.TemporaryDirectory()
    owner.addCleanup(temporary.cleanup)
    root = Path(temporary.name) / 'study'
    root.mkdir()
    with tarfile.open(FIXTURE_DIRECTORY / (name + '.tar.xz')) as archive:
        # All names come from the checked-in per-file manifest, all members are regular files.
        for member in archive.getmembers():
            if member.name not in metadata['files'] or not member.isfile():
                raise AssertionError('Unexpected stored fixture member: ' + member.name)
            target = root / member.name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(archive.extractfile(member).read())
    for name, sha256 in metadata['files'].items():
        if hashlib.sha256((root / name).read_bytes()).hexdigest() != sha256:
            raise AssertionError('The stored 0.87.0 fixture changed: ' + name)
    return root, metadata


class StoredReadingCase(OpennessReadingCase):
    def assign(self, identifier, native_packet, prior=None):
        from research_harness import review_protocol
        context = {'native_packet': native_packet}
        if prior is not None:
            context['prior_assignment_id'] = prior
        return self.mutate(review_protocol.record_assignment, dict(id=identifier, route_id='route', dossier_id=None,
            role='native_math', reviewer_id='openness-reviewer', author_id=self.author_id, context=context))['result']

    def build_native_result(self, packet, outcome='supported_open'):
        if self.fixture_metadata['protocol'] == 'math':
            return copy.deepcopy(self.fixture_metadata['review'])
        return super().build_native_result(packet, outcome)


def install_stored_backend(case, route):
    """Declare the historical synthetic executor without changing its stored runtime or receipts."""
    complete_route = dict(route, runtime=build_capacity_runtime(
        route['adapter'], fixture=route['runtime']['fixture']))
    describe_key = review_capacity_evidence.describe_capacity_key

    def describe_stored_key(candidate):
        if all(candidate.get(key) == route.get(key) for key in ('adapter', 'endpoint', 'model', 'runtime')):
            candidate = dict(candidate, runtime=complete_route['runtime'])
        return describe_key(candidate)

    priors = tuple(review_capacity_evidence.read_capacity_priors()) + (
        build_capacity_prior(complete_route, 2000000, 'stored-synthetic-executor'),)
    for patcher in (
            patch.object(review_codex_transport, 'runtime', return_value=route['runtime']),
            patch.object(review_capacity_evidence, 'describe_capacity_key', describe_stored_key),
            patch.object(review_route_capacity, 'describe_capacity_key', describe_stored_key),
            patch.object(review_capacity_evidence, 'CAPACITY_PRIORS', priors)):
        patcher.start()
        case.addCleanup(patcher.stop)


def open_stored_case(owner, version, scenario):
    prefix = 'v%d' % version if isinstance(version, int) else version
    root, metadata = copy_stored_store(owner, prefix + '-' + scenario)
    case = StoredReadingCase()
    owner.addCleanup(case.doCleanups)
    case.root, case.store, case.artifacts = root, Store(root), ArtifactStore(root)
    case.sequence = 10000
    case.fixture_metadata = metadata
    records = case.store.snapshot()['records']
    case.versions = sorted(records['work'])
    case.texts = {key: work['fulltexts'][0]['text'] for key, work in records['work'].items()}
    case.author_id = records['review_assignment'][metadata['turns'][0]]['author_id']
    case.selected = records.get('problem_statement', {}).get('statement-1')
    case.searches = list(records['literature_search'].values())
    install_codex_runtime(case)
    install_stored_backend(case, records['review_route']['route'])
    if version == 4:
        patcher = patch('research_harness.openness_reading.find_chain_protocol',
                        return_value='exactory-openness-reading-v4')
        patcher.start()
        case.addCleanup(patcher.stop)
        limit_codex_input(case, metadata.get('input_limit') or INPUT_LIMIT_CHARACTERS)
    return case, metadata


class CurrentStoppedCase(OpennessReadingCase):
    text_sizes = (120000, 60000)
    reading_protocol = None


def open_current_stopped_case(owner, version, *, observed=True, both=False):
    """Build the C8 stopped-prefix shape with a current route and its real keyed probe."""
    from openness_reading_fixtures import build_sized_body
    from research_harness import openness_reading, review_route_capacity
    from research_harness.capture_selection import select_main_capture
    from research_harness.literature import import_bundle
    from research_harness.reading import record_reading

    case = CurrentStoppedCase()
    if version == 'math':
        # Leave the first turn room for the captured search response before its
        # first complete full text, as in the native stopped-prefix fixture.
        case.text_sizes = (119000, 60000)
    owner.addCleanup(case.doCleanups)
    case.setUp()
    if version != 'math':
        selected_protocol = openness_reading.PROTOCOL_V4 if version == 4 else openness_reading.PROTOCOL_V5
        patcher = patch.object(openness_reading, 'find_chain_protocol', return_value=selected_protocol)
        patcher.start()
        case.addCleanup(patcher.stop)
    if version == 4:
        case.limit_input(400000)
    work = case.versions[1]
    first = case.store.snapshot()['records']['work'][work]['fulltexts'][0]
    compact = case.capture(work, pdf_text=build_sized_body(2, 50000))
    case.mutate(select_main_capture, dict(version_id=work, source_id=compact['source_id'],
                                        reason='Choose the complete second extraction.'))
    bundle = case.bundle(work, capture=compact, bundle_id='bundle-compact')
    if both:
        bundle['units'].append(dict(id='earlier-extraction', kind='text', required=True,
            link=case.link(work, first['source_id'], first['text'], 'Paper 2 opening line.', kind='span')))
    for unit in bundle['units']:
        link = unit['link']
        if link['locator']['kind'] == 'text':
            unit['link'] = case.link(work, link['source_id'], link['artifact'], link['locator']['quote'], kind='span')
    bundle['inventory']['links'] = [bundle['units'][0]['link']]
    case.mutate(import_bundle, bundle)
    case.mutate(record_reading, case.full_note(bundle, 'read-compact'))
    reference = case.export(priority=case.versions)
    if version == 'math':
        # Keep the current exporter's chosen-capture binding and evidence. The
        # mathematical result contract names the schema-1 openness minimum.
        native = json.loads(case.artifacts.read(reference))
        native['contract'] = dict(proof_policy='analytical')
        native['objective_id'] = 'synthetic-math-objective'
        minimum = native['scientific_context']['openness_minimum']
        native['scientific_context']['openness_minimum'] = dict(schema_version=1,
            **{key: minimum[key] for key in ('statement', 'neighbours', 'searches',
                                             'reading_digests', 'settling_work_id')})
        # Native math delivers the captured response itself; the problem
        # exporter also carries normalized search material under other hashes.
        records = case.store.snapshot()['records']
        carried = {entry['digest']: entry['artifact'] for entry in native['evidence']}
        for binding in minimum['searches']:
            search = json.loads(case.artifacts.read(carried[binding['search_digest']]))
            for response in search['responses']:
                body = records['source'][response['source_id']]['response']
                if body['sha256'] not in carried:
                    native['evidence'].append(dict(digest=body['sha256'], kind='artifact', artifact=body))
                    carried[body['sha256']] = body
        reference = case.artifacts.put(json.dumps(native, sort_keys=True, ensure_ascii=False).encode(),
                                       'application/json')
    assignment = case.assign('turn-1', reference)
    packet = case.read_packet('turn-1')
    owner.assertIn('reading_chain', assignment)
    plan = packet['reading_turn']['reading_plan']
    owner.assertFalse(any(row.get('version_id') == work for row in plan['delivered_now']))
    owner.assertTrue(any(row.get('version_id') == work for row in plan['remaining']))
    records = case.store.snapshot()['records']
    route = records['review_route']['route']
    capacity = review_route_capacity.describe_route_capacity(route, records=records, artifacts=case.artifacts)
    owner.assertIsNotNone(capacity['input_tokens']['key'])
    owner.assertGreater(capacity['input_tokens']['value'], 0)
    owner.assertEqual(records['review_route_probe']['probe-route']['capacity_evidence']['key'],
                      capacity['input_tokens']['key'])
    if observed:
        case.invoke('turn-1', case.build_reading_response(packet, excerpts=[]))
    return case, dict(protocol=version, work=work, first=first, compact=compact, native=reference)
