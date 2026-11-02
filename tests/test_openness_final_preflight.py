"""Prospective final-input checks refuse impossible reading work without changing saved inputs."""

import copy
import hashlib
import json
import shutil
import unittest
from unittest.mock import patch

from openness_reading_fixtures import OpennessReadingCase
from research_harness import openness_reading as reading, review_protocol as protocol, review_transport as transport
from research_harness.artifacts import ArtifactStore
from research_harness.errors import ResearchError
from research_harness.evidence import digest

# The three unchanged 0.70.0 builders, frozen before this regression was implemented.
# Other builders are unchanged by D1-2; these preserve the old planning and withholding decisions.
_REFERENCE_BUILDERS = r'''
def _list_withholdable_bodies(packet, rows, spans, observations):
    """The bodies that the final turn may withhold, in the order in which it withholds them, each as its SHA-256 and
    the reading turns that delivered it: the full texts from the end of text_delivery_priority, then the other full
    texts, the captured responses, the reading records and the search records, each kind largest first. A body
    qualifies when the actual requests of reading turns delivered it complete, whole or in all of its parts. The
    statement record and the bodies that proof, source or screening contracts protect stay."""
    from .problem_openness_sources import protected_evidence, required_proof
    native = packet['native']
    protected = set(protected_evidence(packet)) | required_proof(native)
    carried = {entry['artifact']['sha256'] for entry in packet['evidence']}
    priority = native['scientific_context'].get('text_delivery_priority', [])
    whole, parts = _collect_coverage(observations)
    ranked = []
    for row in rows:
        key, sha256 = review_excerpts.find_identity(row), row['artifact']['sha256']
        if (row.get('record_kind') == 'problem_statement' or sha256 in protected or sha256 not in carried
                or not _is_complete(key, spans[key], whole, parts)):
            continue
        if row['kind'] == 'fulltext' and sha256 in priority:
            rank = (0, -priority.index(sha256))
        else:
            rank = (1 + WITHHOLDING_ORDER.index(row.get('record_kind', row['kind'])), -spans[key][-1][1])
        delivering = [observation['assignment_id'] for observation in observations if observation['reading']
                      and (key in observation['whole'] or any(item[0] == key for item in observation['parts']))]
        ranked.append((rank, key, sha256, delivering))
    bodies, seen = [], set()
    for _, _, sha256, delivering in sorted(ranked):
        if sha256 not in seen:
            seen.add(sha256)
            bodies.append((sha256, delivering))
    return bodies


def _build_final_turn(route, packet, record, rows, spans, observations):
    """The native packet with the reading record, withholding only as far as its request needs to fit the route."""
    from .review_protocol import canonical_prompt
    value = {key: item for key, item in packet.items() if key != 'digest'}
    value[RECORD_KEY] = record
    bodies = _list_withholdable_bodies(value, rows, spans, observations)
    prompt = canonical_prompt('native_math', reading_stage='final', reading_protocol=record['protocol'])['text']
    return _fit_turn(route, prompt, value, bodies)[0]


def prepare_packet(records, artifacts, value, route, packet):
    """The packet of a native_math assignment and its place in a reading chain. An openness packet, of a problem or of
    a math result, stays a single turn when one input of the route holds every reading unit complete and its units
    total at most one turn's reading budget. Otherwise the first assignment is reading turn 1; a turn that continues a
    chain is the next reading turn while a required unit has not reached a reading turn complete, and then the final
    turn."""
    native = packet['native']
    prior_id = value['context'].get('prior_assignment_id')
    if prior_id is None:
        if (not (is_openness_packet(native) or is_math_openness_packet(native))
                or transport.local_input_limit(route) is None):
            return packet, None
        from .review_protocol import canonical_prompt
        rows = list_reading_rows(records, artifacts, native)
        characters = sum(len(artifacts.read(row['artifact']).decode('utf-8')) for row in rows)
        if (characters <= review_excerpts.TURN_FULLTEXT_CHARACTERS
                and _fits(route, canonical_prompt('native_math')['text'], packet, [row['artifact'] for row in rows])):
            return packet, None
        record = _create_record(find_chain_protocol(native))
        chain = {'first_assignment_id': value['id'], 'turn': 1}
        return _build_reading_turn(artifacts, route, packet, record, [], list_reading_units(artifacts, rows), 1), chain
    prior, prior_packet, state = _read_prior(records, artifacts, value, prior_id)
    rows = list_reading_rows(records, artifacts, native)
    spans = _compute_spans(artifacts, rows)
    observations = _observe_turns(records, artifacts, _list_chain(records, prior), rows, spans)
    record = _extend_record(records, artifacts, prior_packet, state, rows, observations, find_chain_protocol(native))
    chain = dict(prior['reading_chain'], turn=prior['reading_chain']['turn'] + 1)
    remaining = _list_remaining_units(artifacts, rows, spans, observations)
    if remaining:
        delivered = [dict({key: item for key, item in row.items() if key != 'artifact'},
                          artifact_descriptor=descriptor(row['artifact']))
                     for observation in observations for row in observation['delivered_now']]
        return _build_reading_turn(artifacts, route, packet, record, delivered, remaining, chain['turn']), chain
    return _build_final_turn(route, packet, record, rows, spans, observations), chain

'''


def build_before_preflight(*args):
    namespace = dict(vars(reading))
    exec(_REFERENCE_BUILDERS, namespace)
    return namespace['prepare_packet'](*args)


class FinalPreflightCase(OpennessReadingCase):
    text_sizes = (125000, 9000, 9000)
    input_limit = 160000

    def pin_packet(self, native):
        return self.artifacts.put(json.dumps(native, sort_keys=True).encode(), 'application/json')

    def build_native(self, *, proof_characters=0, protected=(), screening=()):
        native = json.loads(self.artifacts.read(self.export()))
        context = native['scientific_context']
        references = []
        for number, characters in enumerate(([proof_characters] if proof_characters else [])
                                             + list(protected) + list(screening)):
            content = ('Mandatory body %d. λ\"\n' % number) * (characters // 20 + 1)
            reference = self.artifacts.put(content[:characters].encode(), 'text/plain')
            native['evidence'].append({'digest': reference['sha256'], 'kind': 'artifact', 'artifact': reference})
            references.append(reference)
        if proof_characters:
            context['native_reviews'] = [{'required_evidence_digests': [references[0]['sha256']]}]
        offset = int(bool(proof_characters))
        if protected:
            context['openness_delivery'] = {'schema_version': 1, 'sources': [], 'contained_sources': [],
                'alternate_body_digests': [], 'protected_source_digests': [
                    row['sha256'] for row in references[offset:offset + len(protected)]]}
        if screening:
            context['source_evidence'] = [{'source_record': row, 'delivered_artifact': row}
                for row in references[offset + len(protected):]]
        return self.pin_packet(native), references

    def build_inputs(self, native_packet, prior=None):
        records = self.store.snapshot()['records']
        value = {'id': 'turn-1' if prior is None else 'turn-2', 'route_id': 'route', 'dossier_id': None,
                 'role': 'native_math', 'reviewer_id': 'openness-reviewer', 'author_id': 'problem-author',
                 'context': {'native_packet': native_packet}}
        if prior is not None:
            value['context']['prior_assignment_id'] = prior
        with transport.text_only_delivery(True):
            packet = protocol.build_packet(records, self.artifacts, None, 'native_math', 'openness-reviewer',
                                           native_packet=native_packet)
        return records, self.artifacts, value, records['review_route']['route'], packet

    def read_artifacts(self):
        return {str(path.relative_to(self.root)): hashlib.sha256(path.read_bytes()).hexdigest()
                for path in (self.root / 'research' / 'sources').rglob('*') if path.is_file()}

    def assert_refused_without_writes(self, native_packet, references=(), prior=None):
        before = self.store.snapshot()
        objects = self.read_artifacts()
        with self.assertRaises(ResearchError) as caught:
            self.assign('turn-1' if prior is None else 'turn-2', native_packet, prior)
        self.assertEqual(caught.exception.code, 'review_input_too_large')
        details = caught.exception.details
        self.assertEqual(details.get('phase'), 'final_preflight')
        self.assertEqual(details.get('measurement_kind'), 'mandatory_input_lower_bound')
        self.assertEqual(details['input_limit_characters'], self.input_limit)
        self.assertGreater(details['input_characters'], self.input_limit)
        components = details['mandatory_components']
        for reference in references:
            self.assertTrue(any(row.get('sha256') == reference['sha256']
                                and row.get('size_bytes') == reference['size'] for row in components), components)
        self.assertTrue(any(row['kind'] == 'final_prompt' for row in components))
        self.assertEqual(self.store.snapshot(), before)
        self.assertEqual(self.read_artifacts(), objects)
        return details


class EarlyRefusalTests(FinalPreflightCase):
    def test_oversized_mandatory_proof_refuses_before_part_artifacts_or_assignment(self):
        self.limit_input(self.input_limit)
        native, references = self.build_native(proof_characters=200000)
        # Establish the original defect: the first reading call itself fits and writes split artifacts.
        args = list(self.build_inputs(native))
        baseline = self.root / 'unchanged-builder'
        shutil.copytree(self.root / 'research' / 'sources', baseline / 'research' / 'sources')
        args[1] = ArtifactStore(baseline)
        first, chain = build_before_preflight(*args)
        self.assertEqual(chain['turn'], 1)
        prompt = protocol.canonical_prompt('native_math', reading_stage='reading')['text']
        self.assertLess(reading._measure(self.build_inputs(native)[3],
                                       transport.request_body(self.build_inputs(native)[3], prompt, first)),
                        self.input_limit)
        self.assert_refused_without_writes(native, references)

    def test_protected_source_union_refuses_before_part_artifacts_or_assignment(self):
        self.limit_input(self.input_limit)
        native, references = self.build_native(protected=(90000, 90000))
        self.assertTrue(all(row['size'] < self.input_limit for row in references))
        self.assert_refused_without_writes(native, references)

    def test_screening_union_refuses_before_part_artifacts_or_assignment(self):
        self.limit_input(self.input_limit)
        native, references = self.build_native(screening=(90000, 90000))
        self.assert_refused_without_writes(native, references)

    def test_accumulated_excerpts_refuse_before_another_reading_plan(self):
        self.limit_input(self.input_limit)
        native, _ = self.build_native(proof_characters=70000)
        self.assign('turn-1', native)
        packet = self.read_packet('turn-1')
        content = self.artifacts.read(self.texts[self.versions[0]]).decode()
        excerpts = [{'version_id': self.versions[0], 'text': content[1000:7000],
                     'purpose': 'Mandatory excerpt %d.' % number} for number in range(25)]
        self.invoke('turn-1', self.build_reading_response(packet, excerpts=excerpts))
        details = self.assert_refused_without_writes(native, prior='turn-1')
        self.assertTrue(any(row['kind'] == 'excerpt' for row in details['mandatory_components']))

    def test_accumulated_passages_refuse_before_another_reading_plan(self):
        self.limit_input(self.input_limit)
        native, _ = self.build_native(proof_characters=70000)
        self.assign('turn-1', native)
        packet = self.read_packet('turn-1')
        content = self.artifacts.read(self.texts[self.versions[0]]).decode()
        passages = [{'id': 'p-%d' % number, 'version_id': self.versions[0], 'anchor': content[1000:1100],
                     'characters': 20000} for number in range(5)]
        self.invoke('turn-1', self.build_reading_response(packet, passages=passages))
        details = self.assert_refused_without_writes(native, prior='turn-1')
        self.assertTrue(any(row['kind'] == 'passage' for row in details['mandatory_components']))


class CompatibilityTests(FinalPreflightCase):
    def test_fitting_chain_preserves_prompts_packets_and_requests(self):
        self.limit_input(self.input_limit)
        native, _ = self.build_native(proof_characters=1000)
        prior = None
        for number in range(1, 10):
            args = self.build_inputs(native, prior)
            expected, expected_chain = build_before_preflight(*args)
            packet_before = copy.deepcopy(args[-1])
            actual, chain = reading.prepare_packet(*args)
            self.assertEqual(args[-1], packet_before)
            self.assertEqual(json.dumps(actual, sort_keys=True), json.dumps(expected, sort_keys=True))
            self.assertEqual(chain, expected_chain)
            prompt = protocol.canonical_prompt('native_math', reading_stage=reading.find_stage(actual),
                                               reading_protocol=reading.find_protocol(actual))
            identifier = 'turn-%d' % number
            assignment = self.assign(identifier, native, prior)
            self.assertEqual(assignment['prompt'], prompt)
            self.assertEqual(self.read_packet(identifier), expected)
            self.assertEqual(transport.request_body(args[3], prompt['text'], actual),
                             transport.request_body(args[3], prompt['text'], expected))
            if reading.find_stage(actual) == 'final':
                break
            self.invoke(identifier, self.build_reading_response(actual))
            prior = identifier
        else:
            self.fail('The fitting chain did not finish')


class LowerBoundTests(unittest.TestCase):
    def setUp(self):
        from research_harness.artifacts import describe_artifact
        self.route = {'adapter': 'codex_cli_v1', 'endpoint': 'codex://local', 'model': 'fixture',
                      'configuration': {'max_output_tokens': 1000, 'timeout_seconds': 60}}
        body = 'Mandatory λ proof with JSON escaping: "quoted"\n' * 30
        reference = describe_artifact(body.encode(), 'text/plain')
        self.packet = {'protocol': protocol.PROTOCOL, 'role': 'native_math', 'reviewer_id': 'reader',
                       'dossier_id': None, 'native': {'subject': {'statement': 'Exact statement.'},
                       'scientific_context': {'native_reviews': [
                           {'required_evidence_digests': [reference['sha256']]}]}},
                       'evidence': [{'artifact': reference, 'encoding': 'utf-8', 'content': body}]}
        self.rows = []
        self.record = reading._create_record(reading.PROTOCOL)

    def preflight(self, limit=10 ** 8, route=None, packet=None, record=None):
        function = getattr(reading, 'preflight_final_input', None)
        self.assertTrue(callable(function), 'The pure mandatory-final-input preflight is missing')
        return function(route or self.route, limit, packet or self.packet, self.rows,
                        self.record if record is None else record)

    def test_projection_is_pure_and_bounds_real_final_input_on_text_routes(self):
        for adapter, endpoint in [('codex_cli_v1', 'codex://local'),
                                  ('codex_cli_framed_v1', 'codex://local'),
                                  ('claude_cli_v1', 'claude://local')]:
            with self.subTest(adapter=adapter):
                route = dict(self.route, adapter=adapter, endpoint=endpoint)
                if adapter == 'claude_cli_v1':
                    route['configuration'] = dict(route['configuration'], effort='high')
                before = copy.deepcopy((self.packet, self.rows, self.record))
                report = self.preflight(route=route)
                self.assertEqual(report['status'], 'measured')
                self.assertEqual(report['measurement_kind'], 'mandatory_input_lower_bound')
                self.assertEqual((self.packet, self.rows, self.record), before)
                final = reading._build_final_turn(route, self.packet, self.record, self.rows, {}, [])
                prompt = protocol.canonical_prompt('native_math', reading_stage='final')['text']
                actual_size = reading._measure(route, transport.request_body(route, prompt, final, order=()))
                self.assertLessEqual(report['input_characters'], actual_size)
                self.assertGreater(report['input_characters'], len(self.packet['evidence'][0]['content']))
                self.assertNotIn('packet', report)
                self.assertNotIn('request', report)
                self.preflight(report['input_characters'], route=route)
                with self.assertRaises(ResearchError) as caught:
                    self.preflight(report['input_characters'] - 1, route=route)
                self.assertEqual(caught.exception.details['phase'], 'final_preflight')

    def test_encoded_or_unmeasured_representations_report_unknown(self):
        from research_harness.review_source_complete import MARKER
        encoded = dict(self.packet, **{MARKER: {}})
        for route, packet, limit in [(self.route, encoded, 1),
                                     (dict(self.route, adapter='codex_cli_visual_v1'), self.packet, 1),
                                     (self.route, self.packet, None)]:
            with self.subTest(adapter=route['adapter'], limit=limit):
                report = self.preflight(limit, route=route, packet=packet)
                self.assertEqual(report['status'], 'unknown')
                self.assertIsNone(report['input_characters'])
                self.assertTrue(report['reason'])

    def test_lower_bound_never_authorizes_undelivered_body_withholding(self):
        reference = self.packet['evidence'][0]['artifact']
        self.packet['native']['scientific_context']['native_reviews'] = []
        self.rows = [{'kind': 'fulltext', 'version_id': 'v', 'text_sha256': reference['sha256'],
                      'artifact': reference}]
        key = reading.review_excerpts.find_identity(self.rows[0])
        spans = {key: [(0, len(self.packet['evidence'][0]['content']), reference['sha256'])]}
        report = self.preflight()
        self.assertLess(report['input_characters'], len(self.packet['evidence'][0]['content']) + 1000)
        self.assertEqual(reading._list_withholdable_bodies(self.packet, self.rows, spans, []), [])
        final = reading._build_final_turn(self.route, self.packet, self.record, self.rows, spans, [])
        self.assertEqual(final['evidence'], self.packet['evidence'])


class RepresentationCompatibilityTests(unittest.TestCase):
    def setUp(self):
        import test_problem_openness_source_delivery as fixtures
        from codex_route_fixtures import record_codex_route
        self.fixture = fixtures.SourceDeliveryTests()
        self.addCleanup(self.fixture.doCleanups)
        self.fixture.setUp()
        self.case = self.fixture.case
        record_codex_route(self.case, 'codex-route')

    def check_chain(self, native):
        from codex_route_fixtures import invoke_codex
        from research_harness.native_review import ATTESTATION_PREFIX
        f = self.case
        reference = f.pin_json(native)
        first = native['reviewer']['attestation_id'][len(ATTESTATION_PREFIX):]
        prior = None
        for number in range(1, 20):
            records = f.store.snapshot()['records']
            value = {'id': first if number == 1 else first + '-%d' % number,
                     'route_id': 'codex-route', 'dossier_id': None,
                     'role': 'native_math', 'reviewer_id': native['reviewer']['actor_id'],
                     'author_id': 'problem-author', 'context': {'native_packet': reference}}
            if prior:
                value['context']['prior_assignment_id'] = prior
            with transport.text_only_delivery(True):
                packet = protocol.build_packet(records, f.artifacts, None, 'native_math', value['reviewer_id'],
                                               native_packet=reference)
            route = records['review_route']['codex-route']
            args = (records, f.artifacts, value, route, packet)
            expected, old_chain = build_before_preflight(*args)
            actual, chain = reading.prepare_packet(*args)
            self.assertEqual(json.dumps(actual, sort_keys=True), json.dumps(expected, sort_keys=True))
            self.assertEqual(chain, old_chain)
            prompt = protocol.canonical_prompt('native_math', reading_stage=reading.find_stage(actual),
                                               reading_protocol=reading.find_protocol(actual))
            assignment = f.mutate(protocol.record_assignment, value)['result']
            self.assertEqual(assignment['prompt'], prompt)
            self.assertEqual(json.loads(f.artifacts.read(assignment['packet'])), expected)
            request = transport.request_body(route, prompt['text'], actual)
            self.assertEqual(request, transport.request_body(route, prompt['text'], expected))
            if reading.find_stage(actual) == 'final':
                sent = reading.review_excerpts.read_sent_packet(request)
                entries = {row['artifact']['sha256']: row for row in sent['evidence']}
                delivery = native['scientific_context']['openness_delivery']
                report = reading.preflight_final_input(route, reading._measure(route, request), packet,
                    reading.list_reading_rows(records, f.artifacts, native), actual['reading_record'])
                self.assertLessEqual(report['input_characters'], reading._measure(route, request))
                components = {row['sha256']: row for row in report['mandatory_components']}
                for identity in delivery['alternate_body_digests']:
                    self.assertEqual(entries[identity]['content'], '')
                    self.assertEqual(components[identity]['characters'], 0)
                for span in delivery['contained_sources']:
                    self.assertEqual(entries[span['sha256']]['content'], '')
                    self.assertEqual(components[span['sha256']]['characters'], 0)
                    self.assertEqual(entries[span['container_sha256']]['content'].encode(),
                                     f.artifacts.read(entries[span['container_sha256']]['artifact']))
                return
            binding = actual['reading_turn']
            output = dict({key: binding[key] for key in ('subject_digest', 'claim_digest', 'reviewer')},
                          schema_version=1, stage='reading', notes='Read the delivered exact units.',
                          excerpts=[], passage_requests=[])
            invoke_codex(f, value['id'], output)
            prior = value['id']
        self.fail('The representation chain did not finish')

    def test_authorized_alternative_and_contained_chain_preserves_input_bytes(self):
        native = self.fixture.export('contained', priority=True)
        delivery = native['scientific_context']['openness_delivery']
        self.assertTrue(delivery['alternate_body_digests'])
        self.assertTrue(delivery['contained_sources'])
        with patch.object(reading.review_excerpts, 'TURN_FULLTEXT_CHARACTERS', 3000):
            self.check_chain(native)

    def test_authorized_standalone_source_chain_preserves_input_bytes(self):
        native = self.fixture.standalone_packet('standalone')
        self.assertFalse(native['scientific_context']['openness_delivery']['contained_sources'])
        with patch.object(reading.review_excerpts, 'TURN_FULLTEXT_CHARACTERS', 3000):
            self.check_chain(native)
