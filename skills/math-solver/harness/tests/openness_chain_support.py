"""Native result packets whose openness minimum binds captured search responses and complete source readings, read
through the openness reading chain on a Codex or a Claude route. Only the provider is substituted: export, assignment,
request fitting, observation, refusal and the controller run as they do for a real reviewer."""

import copy
import hashlib
import json
import shutil
from unittest.mock import patch

from research_harness import openness_reading, review_claude_transport as claude, review_codex_transport as codex
from research_harness import review_excerpts, review_protocol as protocol, review_transport as transport
from research_harness.acquisition import import_response
from research_harness.artifacts import ArtifactStore
from research_harness.evidence import digest
from research_harness.literature import import_bundle, record_search
from research_harness.reading import record_reading
from research_harness.storage import Store
from search_controller.service import Controller
from search_controller.storage import canonical_bytes
from tests import test_search_evidence as evidence_tests
from tests.openness_support import open_common_case
from tests.test_search_cli import SearchCLIWorkspace

from codex_route_fixtures import build_codex_response, record_codex_route
from test_research_claude_transport import clean_stream, completed

CLAUDE_ROUTE = {'id': 'route', 'adapter': 'claude_cli_v1', 'model': 'claude-opus-5-5', 'endpoint': 'claude://local',
                'configuration': {'effort': 'high', 'max_output_tokens': 20000, 'timeout_seconds': 1800}}
# The packet of the triangle-free objective's result review (incident 20261024210650): five captured responses and four
# full texts, 2,764,871 bytes in all. The last response size is tuned so that this fixture's packet has that size.
OBJECTIVE_PACKET_BYTES = 2764871
OBJECTIVE_RESPONSE_SIZES = (632621, 512451, 383240, 162040, 152864)
OBJECTIVE_TEXT_SIZES = (124537, 147435, 31819, 73968)
EXCERPT_CHARACTERS = 60
EXCERPT_PURPOSE = 'Keep the exact delivered wording for the final assessment.'


def fail_transport(route, request, credential):
    raise ValueError('The reviewer process was stopped at the route timeout.')


def build_filler(label, characters):
    """Numbered filler of exactly the given length. No two bodies or parts of the fixture share bytes, so the harness
    cannot credit one delivered part for another identical one."""
    token = len(label) + 9
    return ''.join('%s%08d ' % (label, index) for index in range(characters // token + 1))[:characters]


class MathOpennessChainCase(SearchCLIWorkspace):
    artifact = evidence_tests.EvidenceTests.artifact
    external_checkpoint = evidence_tests.EvidenceTests.external_checkpoint
    setup_external = evidence_tests.EvidenceTests.setup_external
    result_review = evidence_tests.EvidenceTests.result_review

    def setUp(self):
        super().setUp()
        self.start_patch(patch('research_harness.review_codex_transport.shutil.which', return_value='/fixture/codex'))

    def start_patch(self, patcher):
        patcher.start()
        self.addCleanup(patcher.stop)

    def build_fixture(self, response_sizes=(70000, 60000), text_sizes=(20000,), adapter='codex_cli_v1'):
        """Record the captured searches and full readings of an openness minimum for checkpoint-000001, export its
        native result packet and record a verified route; return the single-turn packet that the route would get."""
        self.setup_external()
        self.controller = Controller(self.root)
        self.case = open_common_case(self.controller)
        self.case.sequence = 0
        self.store, self.artifacts = self.case.store, self.case.artifacts
        checkpoint = self.controller.status()['checkpoints']['checkpoint-000001']
        self.review = self.result_review(checkpoint, checkpoint['claim'])
        self.review['reviewer'] = {'source': 'host', 'actor_id': 'openness-reviewer',
                                   'attestation_id': 'review-assignment:turn-1'}
        self.versions, self.texts = [], {}
        for number, size in enumerate(text_sizes, 1):
            version = self.case.metadata(number)
            quote = 'Theorem %d. Every bounded sequence has a finite extremal example.' % number
            ending = '\nReferences: none.'
            body = quote + '\n' + build_filler('t%d-' % number, size - len(quote) - len(ending) - 1) + ending
            capture = self.case.capture(version, pdf_text=body)
            bundle = self.case.bundle(version, capture=capture, bundle_id='bundle-%d' % number)
            for unit in bundle['units']:
                unit['link'] = self.case.link(version, capture['source_id'], capture['text'], quote, kind='span')
            bundle['inventory']['links'] = [bundle['units'][0]['link']]
            self.case.mutate(import_bundle, bundle)
            self.case.mutate(record_reading, self.case.full_note(bundle, 'read-%d' % number))
            self.versions.append(version)
            self.texts[version] = capture['text']
        readings = [digest(row) for row in self.store.snapshot()['records'].get('reading', {}).values()]
        self.searches, self.sources = [], []
        statement = checkpoint['claim']['statement']
        for number, size in enumerate(response_sizes, 1):
            payload = {'query': statement, 'results': [{'id': version, 'title': 'Authored source'}
                                                       for version in self.versions], 'page': number, 'padding': ''}
            payload['padding'] = build_filler('r%d-' % number, size - len(json.dumps(payload).encode()))
            captured = import_response(
                self.store, 'mcp', json.dumps(payload).encode(), source_url='https://example.org/search/%d' % number,
                captured_at='2026-10-15T12:00:00Z', media_type='application/json',
                mappings=[{'id': '/results/%d/id' % index, 'title': '/results/%d/title' % index}
                          for index in range(len(self.versions))], expected_revision=self.store.revision,
                request_id='capture-search-%d' % number)
            source_id = captured['source_ids'][0]
            self.case.mutate(record_search, {'id': 'search-%d' % number, 'profile': 'research',
                'purpose': 'direct', 'question': statement, 'scope': statement, 'queries': [statement],
                'responses': [{'source_id': source_id, 'query': statement,
                               'query_locator': {'kind': 'json', 'pointer': '/query', 'value': statement},
                               'results_pointer': '/results'}], 'captured_at': '2026-10-15T12:00:00Z',
                'found_work_ids': self.versions, 'verdict': 'nothing-new', 'cited_work_ids': [],
                'dispositions': [{'work_id': version, 'disposition': 'relevant', 'reason': 'Compare this full text.'}
                                 for version in self.versions], 'resolved': [], 'impact': 'Compare the exact bound.',
                'gaps': []})
            search = self.store.snapshot()['records']['literature_search']['search-%d' % number]
            self.searches.append({'formulation': statement, 'search_digest': digest(search)})
            self.sources.append(source_id)
        self.review['findings']['openness'].update(outcome='supported_open', minimum={
            'schema_version': 1, 'statement': statement, 'neighbours': [], 'searches': self.searches,
            'reading_digests': readings, 'settling_work_id': None})
        self.review['findings']['openness']['evidence'] += [row['search_digest'] for row in self.searches] + readings
        self.export_fixture(checkpoint)
        self.store = Store(self.exported['workspace'])
        self.artifacts = ArtifactStore(self.store.root)
        self.record_route(adapter)
        return self.read_ordinary_packet()

    def build_objective_fixture(self, adapter):
        """The fixture whose ordinary packet has exactly the incident's 2,764,871 bytes, and that size."""
        response_sizes = list(OBJECTIVE_RESPONSE_SIZES)
        for _ in range(3):
            ordinary = self.build_fixture(response_sizes, OBJECTIVE_TEXT_SIZES, adapter)
            size = len(canonical_bytes(ordinary))
            if size == OBJECTIVE_PACKET_BYTES:
                return ordinary, size
            response_sizes[-1] += OBJECTIVE_PACKET_BYTES - size
            shutil.rmtree(self.root)
            self.root.mkdir()
        self.fail('The objective fixture did not reach its packet size: %d bytes' % size)

    def record_route(self, adapter):
        self.adapter = adapter
        if adapter == 'codex_cli_v1':
            record_codex_route(self)
            return
        with patch.object(transport, 'send_request', side_effect=self.respond_to_probe):
            protocol.record_route(self.store, dict(CLAUDE_ROUTE), expected_revision=self.store.revision,
                                  request_id='claude-route')
            protocol.probe_route(self.store, {'id': 'probe-route', 'route_id': 'route'}, credential='test-only',
                                 expected_revision=self.store.revision, request_id='claude-probe')

    def respond_to_probe(self, route, request, credential):
        packet = json.loads(request['input'][0]['content'][len(transport.PACKET_LABEL):])
        return self.respond(route, request, {'allowed_control': packet['allowed_control'],
                                             'evidence_control': packet['evidence_control'], 'excluded_controls': []})

    def respond(self, route, request, output):
        """The provider's answer: on the Claude route the adapter itself sends the request, with only the CLI process
        replaced by a clean observed stream, so its own input limit applies."""
        if route['adapter'] == 'codex_cli_v1':
            return build_codex_response(route, request, output)
        events = clean_stream(request, json.dumps(output), model=route['model'])
        with patch.object(claude, '_cli', return_value=('/fixture/claude', '2.1.310 (Claude Code)')), \
                patch.object(claude, '_run_owned', return_value=completed(events)):
            return claude.send_request(route, request)

    def measure_request(self, request):
        """The input characters of a recorded request as the route's adapter measures them."""
        return len((codex if self.adapter == 'codex_cli_v1' else claude).packet_text(request))

    def find_input_limit(self):
        """The input limit of the fixture's route."""
        return transport.local_input_limit(self.store.snapshot()['records']['review_route']['route'])

    def invoke(self, identifier, output, attempt_id=None):
        attempt_id = attempt_id or 'attempt-' + identifier
        with patch.object(transport, 'send_request',
                          side_effect=lambda route, request, credential: self.respond(route, request, output)):
            return protocol.invoke_assignment(self.store, {'id': attempt_id, 'assignment_id': identifier},
                                              credential='test-only', expected_revision=self.store.revision,
                                              request_id='invoke-' + attempt_id)

    def fail_invocation(self, identifier):
        with patch.object(transport, 'send_request', side_effect=fail_transport):
            protocol.invoke_assignment(self.store, {'id': 'attempt-' + identifier, 'assignment_id': identifier},
                                       credential='test-only', expected_revision=self.store.revision,
                                       request_id='invoke-attempt-' + identifier)

    def export_fixture(self, subject, inputs=()):
        self.exported = self.controller.command('review-packet', {'kind': 'result', 'subject': subject,
            'claim_digest': self.review['claim_digest'], 'reviewer': self.review['reviewer'], 'inputs': list(inputs),
            'openness': self.review['findings']['openness']['minimum']}, None, None)

    def read_ordinary_packet(self):
        with transport.text_only_delivery(True):
            return protocol.build_packet(self.store.snapshot()['records'], self.artifacts, None,
                                         'native_math', 'openness-reviewer',
                                         native_packet=self.exported['native_packet'])

    def assign_turn(self, identifier, prior=None):
        context = {'native_packet': self.exported['native_packet']}
        if prior:
            context['prior_assignment_id'] = prior
        return protocol.record_assignment(self.store, {'id': identifier, 'route_id': 'route', 'dossier_id': None,
            'role': 'native_math', 'reviewer_id': 'openness-reviewer', 'author_id': 'proof-author',
            'context': context}, expected_revision=self.store.revision, request_id='assign-' + identifier)['result']

    def read_turn(self, identifier):
        assignment = self.store.snapshot()['records']['review_assignment'][identifier]
        return json.loads(self.artifacts.read(assignment['packet']))

    def read_sent(self, identifier):
        """The recorded request of a turn's selected attempt and the packet that it delivered."""
        records = self.store.snapshot()['records']
        state = protocol.assignment_state(records, self.artifacts, identifier)
        request = json.loads(self.artifacts.read(records['review_attempt'][state['attempt_id']]['request']))
        return request, review_excerpts.read_sent_packet(request)

    def build_reading_response(self, packet, requests=()):
        """A reading response that quotes the opening characters of every unit or part that the turn delivered."""
        binding = openness_reading.find_binding(packet)
        excerpts = []
        for row in packet.get('reading_turn', {}).get('reading_plan', {}).get('delivered_now', []):
            identity = {key: row[key] for key in ('version_id', 'source_id', 'record_kind', 'record_id', 'text_sha256')
                        if key in row}
            delivered = self.artifacts.read(row['artifact']).decode('utf-8')
            excerpts.append(dict(identity, text=delivered[:EXCERPT_CHARACTERS], purpose=EXCERPT_PURPOSE))
        return {'schema_version': 1, 'stage': 'reading', 'subject_digest': binding['subject_digest'],
                'claim_digest': binding['claim_digest'], 'reviewer': binding['reviewer'], 'excerpts': excerpts,
                'notes': 'Read the delivered search captures, full texts and records against the exact statement.',
                'passage_requests': list(requests)}

    def run_chain(self, final_response=None, first='turn-1'):
        """Assign and observe reading turns until the chain reaches its final turn, then observe the final answer;
        return the turn ids."""
        prefix = first.rsplit('-', 1)[0]
        identifiers = [first]
        assignment = self.assign_turn(first)
        self.assertIn('reading_chain', assignment, 'An oversized math openness minimum needs reading turns')
        while 'reading_turn' in self.read_turn(identifiers[-1]):
            self.invoke(identifiers[-1], self.build_reading_response(self.read_turn(identifiers[-1])))
            identifiers.append('%s-%d' % (prefix, len(identifiers) + 1))
            self.assign_turn(identifiers[-1], identifiers[-2])
            self.assertLessEqual(len(identifiers), 40, 'The chain must end within a bounded number of turns')
        output = self.review if final_response is None else final_response(self.read_turn(identifiers[-1]))
        self.invoke(identifiers[-1], output)
        return identifiers

    def measure_chain(self, turns):
        """For each turn, the input characters of its actual request and the characters of unit text that it
        delivered complete for the first time; and the characters of all the packet's unit text. A unit counts once for
        each exact body or part, so identical bytes are not counted twice."""
        records = self.store.snapshot()['records']
        native = self.read_turn(turns[-1])['native']
        pieces = {}
        for row in openness_reading.list_reading_rows(records, self.artifacts, native):
            content = self.artifacts.read(row['artifact']).decode('utf-8')
            for start, end in review_excerpts.compute_part_spans(content):
                slice_sha256 = hashlib.sha256(content[start:end].encode('utf-8')).hexdigest()
                pieces[slice_sha256] = ({slice_sha256, row['artifact']['sha256']}, end - start)
        delivered, sizes, fresh = set(), [], []
        for identifier in turns:
            request, sent = self.read_sent(identifier)
            sizes.append(self.measure_request(request))
            complete = review_excerpts.find_complete_bodies(sent)
            new = [key for key, (hashes, _) in pieces.items() if key not in delivered and hashes & complete]
            delivered.update(new)
            fresh.append(sum(pieces[key][1] for key in new))
        return sizes, fresh, sum(characters for _, characters in pieces.values())

    def build_acceptance(self):
        return {'obligation_id': 'obligation-000001', 'outcome': 'proof', 'dependency_ids': [],
                'route_bindings': [], 'review': self.review, 'inputs': []}

    def accept_result(self):
        return self.controller.command('accept', self.build_acceptance(), self.controller.status()['revision'],
                                       'accept-reading-chain', target='checkpoint-000001')

    def build_closure(self, first='closure-1'):
        """The version 2 closure subject that binds acceptance-000001, its required deliverable, and the review whose
        attestation names the first turn of its own reading chain, exported as a native result packet."""
        state = self.controller.status()
        output = self.artifact('final-proof.md', 'The complete requested proof and all required cases.')
        subject = {'schema_version': 2, 'objective_id': state['objective_id'],
                   'contract_digest': state['contract_digest'], 'outcome': 'proof',
                   'acceptance_ids': ['acceptance-000001'],
                   'evidence_digests': state['checkpoints']['checkpoint-000001']['evidence_digests'],
                   'local_deliveries': [], 'deliverables': [
                       {'requirement': requirement, 'digest': output['digest']}
                       for requirement in state['contract']['required_deliverables']]}
        self.review = copy.deepcopy(self.review)
        self.review['subject_digest'] = digest(subject)
        self.review['reviewer']['attestation_id'] = 'review-assignment:' + first
        self.export_fixture(subject, [output])
        return {'subject': subject, 'review': self.review, 'inputs': [output]}

    def complete_objective(self, spec):
        return self.controller.command('complete', spec, self.controller.status()['revision'],
                                       'complete-reading-chain-' + spec['review']['reviewer']['attestation_id'][18:])

    def drop_unit(self, row_filter, part=None):
        """Leave a unit, or one of its parts, out of every reading turn, and out of the final request as an explicit
        stub, so that only an authenticated reading delivery could have covered it."""
        real_units, real_final = openness_reading.list_reading_units, openness_reading._build_final_turn
        dropped = set()

        def drop_from_units(artifacts, rows):
            units = real_units(artifacts, rows)
            for row in rows:
                if row_filter(row):
                    dropped.add(row['artifact']['sha256'])
            return [unit for unit in units if not (row_filter(unit) and part in (None, unit['part']))]

        def stub_final_body(*args):
            packet = real_final(*args)
            for entry in packet['evidence']:
                if entry['artifact']['sha256'] in dropped:
                    entry.update(content='', delivery='body-missing-in-fixture')
            packet.pop('digest')
            packet['digest'] = digest(packet)
            return packet
        return [patch.object(openness_reading, 'list_reading_units', side_effect=drop_from_units),
                patch.object(openness_reading, '_build_final_turn', side_effect=stub_final_body)]
