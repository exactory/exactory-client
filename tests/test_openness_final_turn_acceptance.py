"""Acceptance tests of the bounded final turn of an openness reading chain (exactory-client 0.66.3).

Every test builds a problem record with the fixtures of openness_reading_fixtures: captured full texts, full
readings, bound searches, an exported openness packet, and native_math assignments and attempts on a Codex route whose
process the fixtures simulate. Behavior is observed through public functions: openness_reading.prepare_packet and
verify_chain, the problem-openness export, record and assessment paths, and review_excerpts.check_excerpts and
answer_passages. A chain recorded under 0.65.2 is built by the modules of that release, kept byte for byte in
tests/fixtures, so that no current builder takes part in its history.
"""

import contextlib
import copy
import hashlib
import inspect
import json
import unittest
from unittest.mock import patch

from openness_legacy_fixtures import build_as_0_65_2, load_0_65_2_openness_reading
from openness_reading_fixtures import OpennessReadingCase, build_theorem
from research_harness import native_review, openness_reading, review_excerpts, review_protocol as protocol
from research_harness import review_codex_transport as codex, review_transport as transport
from research_harness.acquisition import import_response
from research_harness.bar_sources import descriptor
from research_harness.errors import ResearchError
from research_harness.evidence import digest
from research_harness.literature import record_search
from research_harness.problem_openness import assess_openness

PROTOCOL_V1 = 'exactory-openness-reading-v1'
PROTOCOL_V2 = 'exactory-openness-reading-v2'
PROTOCOL_V3 = 'exactory-openness-reading-v3'
# The canonical prompt hashes of the reading chains of 0.64.2 to 0.66.0, which their recorded assignments carry.
EARLIER_PROMPT_HASHES = {
    (PROTOCOL_V1, 'reading'): 'c3b851f86bef6f01a1ba4318d95b89cb20ce8f6bb5f6f237d8e555de78beff9f',
    (PROTOCOL_V1, 'final'): '5a9359e8c3b9c27301011cab6a19b1157d9f466e4c5ce2db8d9940a02bde9739',
    (PROTOCOL_V2, 'reading'): '0cd98895abb64e7da1674dc1f9e332c2cba309e6a6bfb8560903ad842f4a4857',
    (PROTOCOL_V2, 'final'): 'e52d3ac0cebb2b5ab2cb79e5412138d74fc98374c4fe53614bcbf019f95ca301'}
# What the v3 prompts of both stages name: the identity fields of the three unit kinds, records as JSON text, and the
# withholding rules.
V3_PROMPT_PHRASES = ('version_id', 'source_id', 'record_kind', 'record_id', 'text_sha256', 'JSON', 'notes_withheld',
                     'earliest turns', 'never withholds an excerpt or a passage')
TURN_BUDGET_CHARACTERS = 120000
CODEX_INPUT_LIMIT_CHARACTERS = 1048576
UNLIMITED_INPUT_CHARACTERS = 10 ** 8
MAX_CHAIN_TURNS = 40
BODY_WITHHELD_DELIVERY = 'body-delivered-in-reading-turns'
BODY_WITHHELD_NOTE_PREFIX = 'Delivered complete in reading turns '
BODY_WITHHELD_NOTE_SUFFIX = '; body withheld to fit the route input limit.'
NOTES_WITHHELD_NOTE = 'The notes of turn {} are withheld from this input so that it fits the route input limit.'
# The fields of a checked excerpt or an answered passage that the compact reading record leaves out.
VERBOSE_ENTRY_FIELDS = ('locator', 'artifact_descriptor', 'note', 'checked', 'reading_credit', 'request')
# A phrase of a reading note whose JSON text differs from its value: the export escapes its quotation marks and its
# backslash. FULLWIDTH LATIN CAPITAL LETTER A and LATIN SMALL LIGATURE FI become A and fi under NFKC, and LATIN SMALL
# LETTER E WITH ACUTE takes two bytes in UTF-8.
RECORD_PHRASE = 'The criterion "\uff21 \ufb01nite caf\u00e9 bound" holds; a backslash \\ separates its two cases.'
RECORD_PHRASE_JSON = json.dumps(RECORD_PHRASE, ensure_ascii=False)[1:-1]
# The phrase as a reviewer copies it from the delivered JSON text, with compatibility characters and spacing changed.
RECORD_PHRASE_COPY = RECORD_PHRASE_JSON.replace('\uff21', 'A').replace('\ufb01', 'fi').replace(' ', '  ')
# The openness packet of statement-4 of the KMP record (2026-10-28): the extracted full texts of its eight readings, and
# its seventeen record artifacts (eight search records, eight reading records and the statement record) of 8,418 to
# 84,505 characters and about 431,000 together. The sizes of single records were not measured; these assume a
# distribution with that range and that total. Its 0.65.2 chain read the texts in 17 turns with notes of about 13,500
# characters, 339 verified excerpts of about 585 characters, 2 unmatched excerpts of about 306 characters and 13
# passages of about 7,565 characters.
KMP_TEXT_SIZES = (291855, 195429, 63236, 304336, 101335, 243326, 185115, 213686)
KMP_SEARCH_SIZES = (18000, 16500, 15000, 13500, 12500, 11500, 10500, 10500)
KMP_READING_SIZES = (84505, 61000, 47000, 38000, 30000, 24000, 18000, 12000)
KMP_STATEMENT_SIZE = 8418
KMP_LEGACY_TURNS = 17
KMP_NOTES_CHARACTERS = 13500
KMP_VERIFIED_EXCERPTS = 339
KMP_EXCERPT_CHARACTERS = 585
KMP_UNMATCHED_EXCERPT_CHARACTERS = 306
KMP_PASSAGES = 13
KMP_PASSAGE_CHARACTERS = 7565
# The characters that recording adds to a payload with these fixtures: a reading's assessment, the derived fields of
# a search that finds eight works, and a statement's revision. The KMP sizes built with them are checked to 1 percent.
READING_RECORD_OVERHEAD = 246
SEARCH_RECORD_OVERHEAD = 314
STATEMENT_RECORD_OVERHEAD = 22


def build_filler(characters, sentence='The recorded work proves a bounded case and leaves the statement open. '):
    """Plain text of exactly the given length."""
    return (sentence * (characters // len(sentence) + 1))[:characters]


def encode_exported_record(record):
    """The JSON bytes of a store record as the openness export carries it as evidence."""
    return json.dumps(record, ensure_ascii=False, sort_keys=True, separators=(',', ':'),
                      allow_nan=False).encode('utf-8')


def hash_text(value):
    return hashlib.sha256(value.encode('utf-8')).hexdigest()


def name_unit(value):
    """The kind and identity of the reading unit that a plan entry or a unit identity names, without its optional text
    hash. An entry without a kind is a full text."""
    kind = value.get('kind', 'fulltext')
    if kind == 'record':
        return kind, value.get('record_kind'), value.get('record_id')
    if kind == 'search_response':
        return kind, value.get('source_id')
    return kind, value.get('version_id')


def find_row_sha256(row):
    """The SHA-256 of the body that a unit row names by its artifact reference or descriptor."""
    return (row.get('artifact') or row.get('artifact_descriptor') or {}).get('sha256')


def build_row_filter(list_rows, sha256):
    """A unit list like list_rows without the rows of one body."""
    def list_kept_rows(*args, **kwargs):
        return [row for row in list_rows(*args, **kwargs) if find_row_sha256(row) != sha256]
    return list_kept_rows


class AcceptanceCase(OpennessReadingCase):
    """A problem record with read works, bound searches and a Codex route, and the chain helpers of these tests."""

    def read_route(self):
        return self.store.snapshot()['records']['review_route']['route']

    def record_sized_search(self, size=None):
        """Record a search of the statement that finds every read work, as the openness minimum binds it. With size,
        its impact text makes the stored record about that many characters long."""
        formulation = self.selected['statement']
        data = {'query': formulation, 'results': [{'id': work, 'title': 'Authored mathematical source'}
                                                  for work in self.versions]}
        self.sequence += 1
        captured = import_response(self.store, 'mcp', json.dumps(data).encode(),
                                   source_url='https://example.org/problem-search', captured_at='2026-10-15T12:00:00Z',
                                   media_type='application/json',
                                   mappings=[{'id': '/results/%d/id' % index, 'title': '/results/%d/title' % index}
                                             for index in range(len(self.versions))],
                                   expected_revision=self.store.revision,
                                   request_id='problem-search-capture-%d' % self.sequence)
        identifier = 'search-%d' % self.sequence
        value = {'id': identifier, 'profile': 'research', 'purpose': 'direct', 'question': formulation,
                 'scope': formulation, 'queries': [formulation],
                 'responses': [{'source_id': captured['source_ids'][0], 'query': formulation,
                                'query_locator': {'kind': 'json', 'pointer': '/query', 'value': formulation},
                                'results_pointer': '/results'}],
                 'captured_at': '2026-10-15T12:00:00Z', 'found_work_ids': list(self.versions),
                 'verdict': 'nothing-new', 'cited_work_ids': [],
                 'dispositions': [{'work_id': work, 'disposition': 'relevant',
                                   'reason': 'Explicit relevance judgment of the captured source.'}
                                  for work in self.versions],
                 'resolved': [], 'impact': 'Evidence for the exact problem formulation.', 'gaps': []}
        if size is not None:
            missing = size - SEARCH_RECORD_OVERHEAD - len(encode_exported_record(dict(value, impact='')))
            value['impact'] = build_filler(missing)
        self.mutate(record_search, value)
        return self.store.snapshot()['records']['literature_search'][identifier]

    def record_openness(self, identifier):
        return self.mutate(self.load_problem_module().record_openness, {'assignment_id': identifier})['result']

    def assign_turn(self, identifier, native_packet, prior=None):
        """Record a turn of the chain; a refusal fails the test with its code and details."""
        try:
            return self.assign(identifier, native_packet, prior)
        except ResearchError as error:
            self.fail('The assignment %s was refused: %s: %s %s' % (identifier, error.code, error, error.details))

    def observe_turn(self, identifier, output):
        """Invoke a recorded turn with a reviewer output. Its actual request fits the Codex input limit, and the turn
        becomes ready."""
        self.invoke(identifier, output)
        request, _ = self.read_sent_packet(identifier)
        self.assertLessEqual(len(codex.packet_text(request)), CODEX_INPUT_LIMIT_CHARACTERS, identifier)
        state = protocol.assignment_state(self.store.snapshot()['records'], self.artifacts, identifier)
        self.assertTrue(state['ready'], (identifier, state['obligations']))

    def run_chain(self, native_packet, respond, identifiers=(), stop_when_read=False):
        """Assign and observe the turns of the chain after identifiers, or from its first turn, and answer each reading
        turn with respond(packet, number). Return every turn id: the last is the assigned final turn, or with
        stop_when_read the observed reading turn whose plan has nothing remaining."""
        identifiers = list(identifiers)
        while len(identifiers) < MAX_CHAIN_TURNS:
            identifier = 'turn-%d' % (len(identifiers) + 1)
            self.assign_turn(identifier, native_packet, identifiers[-1] if identifiers else None)
            identifiers.append(identifier)
            packet = self.read_packet(identifier)
            if 'reading_turn' not in packet:
                return identifiers
            self.observe_turn(identifier, respond(packet, len(identifiers)))
            if stop_when_read and not packet['reading_turn']['reading_plan']['remaining']:
                return identifiers
        self.fail('The reading chain did not reach its final turn within %d turns' % MAX_CHAIN_TURNS)

    def preview_turn(self, native_packet, prior, identifier):
        """The packet that the assignment identifier after prior would get, built without recording it."""
        records = self.store.snapshot()['records']
        value = {'id': identifier, 'route_id': 'route', 'dossier_id': None, 'role': 'native_math',
                 'reviewer_id': 'openness-reviewer', 'author_id': 'problem-author',
                 'context': {'native_packet': native_packet, 'prior_assignment_id': prior}}
        with transport.text_only_delivery(True):
            packet = protocol.build_packet(records, self.artifacts, None, 'native_math', 'openness-reviewer',
                                           native_packet=native_packet)
            return openness_reading.prepare_packet(records, self.artifacts, value, self.read_route(), packet)[0]

    def build_request(self, packet, order=()):
        """The Codex request of a packet: by default rendered without any transport reduction."""
        prompt = protocol.canonical_prompt('native_math', reading_stage=openness_reading.find_stage(packet))['text']
        return transport.request_body(self.read_route(), prompt, packet, order=order)

    def measure(self, packet):
        """The input characters of a packet's request as the Codex route measures them."""
        return len(codex.packet_text(self.build_request(packet)))

    def list_record_names(self):
        """The records of the openness packet in their reading order: the searches in the order of the minimum, the
        readings by record id, then the statement."""
        readings = sorted(self.store.snapshot()['records']['reading'])
        return ([('record', 'literature_search', search['id']) for search in self.searches]
                + [('record', 'reading', identifier) for identifier in readings]
                + [('record', 'problem_statement', self.selected['id'])])

    def list_unit_names(self):
        """Every unit of the openness packet in its reading order, when the text priority lists the works in order."""
        return [('fulltext', version) for version in self.versions] + self.list_record_names()

    def find_record_artifacts(self, native_packet):
        """The evidence artifact of each record of the openness packet by the record's name: the artifact whose bytes
        are the export's JSON encoding of the record."""
        native = json.loads(self.artifacts.read(native_packet))
        carried = {entry['artifact']['sha256']: entry['artifact'] for entry in native['evidence']}
        records = self.store.snapshot()['records']
        references = {}
        for name in self.list_record_names():
            data = encode_exported_record(records[name[1]][name[2]])
            references[name] = carried[hashlib.sha256(data).hexdigest()]
            self.assertEqual(self.artifacts.read(references[name]), data)
        return references

    def name_bodies(self, native_packet):
        """The name of each unit of the openness packet by the SHA-256 of its whole body."""
        names = {self.texts[version]['sha256']: ('fulltext', version) for version in self.versions}
        names.update((reference['sha256'], name)
                     for name, reference in self.find_record_artifacts(native_packet).items())
        return names

    def list_delivered_rows(self, identifiers):
        """The plan entries that the reading turns among identifiers delivered, in order."""
        return [row for identifier in identifiers
                for row in self.read_packet(identifier).get('reading_turn', {}).get('reading_plan', {})
                .get('delivered_now', [])]

    def find_delivering_turns(self, identifiers):
        """The reading turns among identifiers that delivered each unit, whole or in parts, by the unit's name."""
        delivering = {}
        for identifier in identifiers:
            for row in self.list_delivered_rows([identifier]):
                turns = delivering.setdefault(name_unit(row), [])
                if identifier not in turns:
                    turns.append(identifier)
        return delivering

    def list_complete_units(self, identifier, names):
        """The units whose whole body the actual request of a turn delivered complete."""
        _, sent = self.read_sent_packet(identifier)
        return {names[sha256] for sha256 in review_excerpts.find_complete_bodies(sent) if sha256 in names}

    def list_withheld(self, packet, names):
        """What a final packet withholds: the bodies that it replaces with a stub, by the name of their unit, and the
        turns whose notes it replaces with notes_withheld."""
        return ([names.get(entry['artifact']['sha256'], ('unknown', entry['artifact']['sha256']))
                 for entry in packet['evidence'] if entry.get('delivery') == BODY_WITHHELD_DELIVERY]
                + [('notes', turn['assignment_id']) for turn in packet['reading_record']['turns']
                   if 'notes_withheld' in turn])

    def check_withheld_entries(self, packet, baseline, names, delivering):
        """A final packet differs from the one that withholds nothing only by stubs of bodies that reading turns
        delivered, each naming those turns, and by notes_withheld in place of the notes of earlier turns."""
        self.assertEqual(packet['native'], baseline['native'])
        for key in ('protocol', 'sources', 'excerpts', 'passages', 'allowances'):
            self.assertEqual(packet['reading_record'][key], baseline['reading_record'][key], key)
        self.assertEqual(packet['digest'], digest({key: value for key, value in packet.items() if key != 'digest'}))
        self.assertEqual([entry['artifact'] for entry in packet['evidence']],
                         [entry['artifact'] for entry in baseline['evidence']])
        for entry, original in zip(packet['evidence'], baseline['evidence']):
            if entry.get('delivery') != BODY_WITHHELD_DELIVERY:
                self.assertEqual(entry, original)
                continue
            self.assertEqual((entry['content'], entry['encoding']), ('', original['encoding']))
            note = entry['note']
            self.assertTrue(note.startswith(BODY_WITHHELD_NOTE_PREFIX) and note.endswith(BODY_WITHHELD_NOTE_SUFFIX),
                            note)
            named = note[len(BODY_WITHHELD_NOTE_PREFIX):-len(BODY_WITHHELD_NOTE_SUFFIX)]
            positions = [named.find(identifier) for identifier in delivering[names[entry['artifact']['sha256']]]]
            self.assertTrue(min(positions) >= 0 and positions == sorted(positions), note)
        turns = packet['reading_record']['turns']
        self.assertEqual([turn['assignment_id'] for turn in turns],
                         [turn['assignment_id'] for turn in baseline['reading_record']['turns']])
        for turn, original in zip(turns, baseline['reading_record']['turns']):
            if 'notes_withheld' not in turn:
                self.assertEqual(turn, original)
                continue
            self.assertNotIn('notes', turn)
            self.assertEqual(turn['notes_withheld'], NOTES_WITHHELD_NOTE.format(turn['assignment_id']))
            self.assertEqual(dict(turn, notes=original['notes']), dict(original, notes_withheld=turn['notes_withheld']))

    def check_units_delivered(self, reading_turns):
        """Every unit of the openness packet reached the chain complete: whole in one turn, or in all of its parts."""
        whole = {name_unit(unit) for turn in reading_turns for unit in turn.get('delivered_units', [])}
        parts = {}
        for turn in reading_turns:
            for row in turn.get('delivered_parts', []):
                parts.setdefault(name_unit(row), set()).add((row['part'], row['parts']))
        for version in self.versions:
            if ('fulltext', version) not in whole:
                body = self.artifacts.read(self.texts[version]).decode('utf-8')
                count = len(review_excerpts.compute_part_spans(body))
                self.assertEqual(parts.get(('fulltext', version)), {(part, count) for part in range(1, count + 1)},
                                 version)
        for name in self.list_record_names():
            self.assertIn(name, whole)

    def check_budget_starts_a_chain(self, native_packet):
        """The single-turn packet fits the Codex route, but its full texts exceed one turn's budget, so the first
        assignment is a reading turn that delivers at most the budget."""
        self.assertGreater(sum(self.text_sizes), TURN_BUDGET_CHARACTERS)
        with transport.text_only_delivery(True):
            single = protocol.build_packet(self.store.snapshot()['records'], self.artifacts, None, 'native_math',
                                           'openness-reviewer', native_packet=native_packet)
        self.assertLessEqual(self.measure(single), CODEX_INPUT_LIMIT_CHARACTERS)
        assignment = self.assign_turn('turn-1', native_packet)
        self.assertEqual(assignment.get('reading_chain'), {'first_assignment_id': 'turn-1', 'turn': 1})
        packet = self.read_packet('turn-1')
        plan = packet['reading_turn']['reading_plan']
        self.assertLessEqual(sum(row['characters'] for row in plan['delivered_now']), TURN_BUDGET_CHARACTERS)
        first = plan['delivered_now'][0]
        self.assertEqual((name_unit(first), first['part'], first['start']), (('fulltext', self.versions[0]), 1, 0))
        self.assertGreater(first['parts'], 1)
        self.assertTrue(plan['remaining'])
        self.observe_turn('turn-1', self.build_reading_response(packet))


class KmpShapedChainAcceptanceTests(AcceptanceCase):
    """The openness packet of statement-4 of the KMP record and the reading answers of its chain (see the KMP
    constants)."""
    text_sizes = KMP_TEXT_SIZES

    def build_statement(self, identifier='statement-1', **changes):
        statement = super().build_statement(identifier, **changes)
        missing = KMP_STATEMENT_SIZE - STATEMENT_RECORD_OVERHEAD - len(encode_exported_record(statement)) - 1
        return dict(statement, resolution_criteria=statement['resolution_criteria'] + ' ' + build_filler(missing))

    def full_note(self, bundle, note_id='full', omit=()):
        note = super().full_note(bundle, note_id, omit)
        size = KMP_READING_SIZES[int(note_id.rsplit('-', 1)[1]) - 1]
        missing = size - READING_RECORD_OVERHEAD - len(encode_exported_record(note)) - 1
        note['notes']['problem']['text'] += ' ' + build_filler(missing)
        return note

    def record_searches(self):
        return [self.record_sized_search(size) for size in KMP_SEARCH_SIZES]

    def respond(self, packet, number):
        """A reading answer of the KMP shape: notes of 13,500 characters; 20 excerpts of 585 characters in each of the
        turns 1 to 16 and 19 in turn 17, and 2 unmatched excerpts in turn 1; one passage request of 7,565 characters in
        each of the turns 2 to 14."""
        rows = [row for row in packet['reading_turn']['reading_plan']['delivered_now']
                if name_unit(row)[0] == 'fulltext']
        version = rows[0]['version_id'] if rows else self.versions[0]
        body = self.artifacts.read(self.texts[version]).decode('utf-8')
        count = max(0, min(20, KMP_VERIFIED_EXCERPTS - 20 * (number - 1)))
        excerpts = [{'version_id': version, 'purpose': 'A step of the argument.',
                     'text': body[1000 + 600 * index:1000 + 600 * index + KMP_EXCERPT_CHARACTERS]}
                    for index in range(count)]
        if number == 1:
            excerpts += [{'version_id': version, 'purpose': 'An observation that stays unchecked.',
                          'text': build_filler(KMP_UNMATCHED_EXCERPT_CHARACTERS,
                                               'Reviewer copy %d that no stored text holds. ' % index)}
                         for index in (1, 2)]
        passages = []
        if 2 <= number <= KMP_PASSAGES + 1:
            passages = [{'id': 'passage-%d' % number, 'version_id': version, 'anchor': body[1000:1060],
                         'characters': KMP_PASSAGE_CHARACTERS}]
        return dict(self.build_reading_response(packet, excerpts=excerpts, passages=passages),
                    notes=build_filler(KMP_NOTES_CHARACTERS))

    def check_kmp_shape(self, native_packet):
        """The packet carries the eight full texts and the seventeen record artifacts of the KMP sizes."""
        self.assertEqual([len(self.artifacts.read(self.texts[version]).decode('utf-8')) for version in self.versions],
                         list(KMP_TEXT_SIZES))
        sizes = [len(self.artifacts.read(reference).decode('utf-8'))
                 for reference in self.find_record_artifacts(native_packet).values()]
        targets = list(KMP_SEARCH_SIZES + KMP_READING_SIZES + (KMP_STATEMENT_SIZE,))
        self.assertEqual(len(sizes), len(targets))
        for size, target in zip(sizes, targets):
            self.assertAlmostEqual(size, target, delta=target // 100)

    def finish_kmp_chain(self, turns):
        """The final turn of a KMP-shaped chain keeps every excerpt and passage with its text, its actual request fits
        the Codex route, and problem-openness records it with every unit delivered."""
        for identifier in turns[:-1]:
            plan = self.read_packet(identifier)['reading_turn']['reading_plan']
            self.assertLessEqual(sum(row['characters'] for row in plan['delivered_now']), TURN_BUDGET_CHARACTERS)
        final = turns[-1]
        records = self.store.snapshot()['records']
        self.assertEqual(records['review_assignment'][final]['prompt']['template_id'], PROTOCOL_V3 + '/final')
        packet = self.read_packet(final)
        record = packet['reading_record']
        self.assertEqual(record['protocol'], PROTOCOL_V3)
        self.assertEqual([turn['assignment_id'] for turn in record['turns']], turns[:-1])
        verified = [entry for entry in record['excerpts'] if entry['status'] == 'verified']
        unmatched = [entry for entry in record['excerpts'] if entry['status'] == 'unmatched']
        self.assertEqual((len(record['excerpts']), len(verified), len(unmatched)),
                         (KMP_VERIFIED_EXCERPTS + 2, KMP_VERIFIED_EXCERPTS, 2))
        # A first match in the repeated paragraphs can span a paragraph break or not, so a verified span has 582 to
        # 586 characters; each keeps its exact source text.
        bodies = {version: self.artifacts.read(self.texts[version]).decode('utf-8') for version in self.versions}
        for entry in verified:
            self.assertEqual(bodies[entry['version_id']][entry['start']:entry['end']], entry['text'])
        self.assertEqual({len(entry['text']) for entry in unmatched}, {KMP_UNMATCHED_EXCERPT_CHARACTERS})
        self.assertEqual([(entry['status'], len(entry['text'])) for entry in record['passages']],
                         [('delivered', KMP_PASSAGE_CHARACTERS)] * KMP_PASSAGES)
        self.assertEqual(record['allowances']['excerpts']['used'],
                         sum(entry['end'] - entry['start'] for entry in verified))
        self.assertEqual(record['allowances']['passages']['used'], KMP_PASSAGES * KMP_PASSAGE_CHARACTERS)
        self.observe_turn(final, self.build_native_result(packet))
        _, sent = self.read_sent_packet(final)
        self.assertEqual((sent['reading_record']['excerpts'], sent['reading_record']['passages']),
                         (record['excerpts'], record['passages']))
        saved = self.record_openness(final)
        self.assertEqual([turn['assignment_id'] for turn in saved['reading_turns']], turns)
        self.check_units_delivered(saved['reading_turns'])
        self.assertEqual(assess_openness(self.store.snapshot()['records'], self.artifacts), [])

    def test_a_kmp_shaped_chain_reaches_a_final_turn_whose_request_fits_the_codex_limit(self):
        native_packet = self.export(priority=self.versions)
        self.check_kmp_shape(native_packet)
        self.finish_kmp_chain(self.run_chain(native_packet, self.respond))

    def test_a_0_65_2_chain_whose_full_texts_were_read_continues_with_record_turns_and_keeps_its_turns_ready(self):
        native_packet = self.export(priority=self.versions)
        self.check_kmp_shape(native_packet)
        with build_as_0_65_2():
            legacy = self.run_chain(native_packet, self.respond, stop_when_read=True)
        self.assertEqual(len(legacy), KMP_LEGACY_TURNS)
        records = self.store.snapshot()['records']
        for identifier in legacy:
            plan = self.read_packet(identifier)['reading_turn']['reading_plan']
            self.assertEqual(records['review_assignment'][identifier]['prompt']['template_id'],
                             PROTOCOL_V2 + '/reading')
            self.assertEqual([row for row in plan['delivered_now'] + plan['remaining'] if 'kind' in row], [])
        recorded = {identifier: copy.deepcopy((records['review_assignment'][identifier],
                                               records['review_attempt']['attempt-' + identifier]))
                    for identifier in legacy}
        turns = self.run_chain(native_packet, self.respond, identifiers=legacy)
        continued = turns[KMP_LEGACY_TURNS:-1]
        self.assertTrue(continued, 'The turn after the last full text is a reading turn for the records')
        self.assertEqual([name_unit(row) for row in self.list_delivered_rows(continued)], self.list_record_names())
        records = self.store.snapshot()['records']
        self.assertEqual([records['review_assignment'][identifier]['prompt']['template_id'] for identifier in continued],
                         [PROTOCOL_V3 + '/reading'] * len(continued))
        carried = self.read_packet(continued[0])['reading_turn']['reading_record']
        self.assertEqual(carried['protocol'], PROTOCOL_V3)
        self.assertEqual([turn['assignment_id'] for turn in carried['turns']], legacy)
        verified = [entry for entry in carried['excerpts'] if entry['status'] == 'verified']
        self.assertEqual(len(verified), KMP_VERIFIED_EXCERPTS)
        self.assertEqual(carried['allowances']['excerpts']['used'],
                         sum(entry['end'] - entry['start'] for entry in verified))
        self.assertEqual(carried['allowances']['passages']['used'], KMP_PASSAGES * KMP_PASSAGE_CHARACTERS)
        for entry in carried['excerpts'] + carried['passages']:
            self.assertEqual([key for key in VERBOSE_ENTRY_FIELDS if key in entry], [], entry)
        for identifier, (assignment, attempt) in recorded.items():
            self.assertEqual(records['review_assignment'][identifier], assignment)
            self.assertEqual(records['review_attempt']['attempt-' + identifier], attempt)
            state = protocol.assignment_state(records, self.artifacts, identifier)
            self.assertTrue(state['ready'], (identifier, state['obligations']))
        self.finish_kmp_chain(turns)


class RecordUnitAcceptanceTests(AcceptanceCase):
    """Readings whose ids do not follow the order of their works, and two bound searches that the minimum lists against
    the order of their ids. The reading read-a carries RECORD_PHRASE in two notes, after a note with a non-ASCII
    character."""
    reading_ids = {'read-1': 'read-c', 'read-2': 'read-a', 'read-3': 'read-b'}

    def full_note(self, bundle, note_id='full', omit=()):
        note = super().full_note(bundle, self.reading_ids[note_id], omit)
        if note['id'] == 'read-a':
            note['notes']['assumptions']['text'] += ' The na\u00efve bound was known before.'
            note['notes']['claims']['text'] += ' ' + RECORD_PHRASE
            note['notes']['problem']['text'] += ' ' + RECORD_PHRASE
        return note

    def record_searches(self):
        first, second = self.record_sized_search(), self.record_sized_search()
        return [second, first]

    def respond(self, packet, number):
        return self.build_reading_response(packet)

    def test_the_packet_records_arrive_as_reading_units_after_the_full_texts(self):
        self.limit_input()
        first, second, third = self.versions
        native_packet = self.export(priority=[third, first, second])
        turns = self.run_chain(native_packet, self.respond, stop_when_read=True)
        rows = self.list_delivered_rows(turns)
        self.assertEqual([name_unit(row) for row in rows],
                         [('fulltext', third), ('fulltext', first), ('fulltext', second)] + self.list_record_names())
        references = self.find_record_artifacts(native_packet)
        for row in rows:
            name = name_unit(row)
            if name[0] == 'fulltext':
                reference = self.texts[name[1]]
                self.assertEqual(row.get('text_sha256'), reference['sha256'])
            else:
                reference = references[name]
            body = self.artifacts.read(reference).decode('utf-8')
            self.assertEqual((row['artifact'], row['part'], row['parts'], row['start'], row['end'], row['characters']),
                             (reference, 1, 1, 0, len(body), len(body)), name)
        noted = self.artifacts.read(references[('record', 'reading', 'read-a')])
        self.assertGreater(len(noted), len(noted.decode('utf-8')), 'The record read-a has characters of two bytes')
        for identifier in turns:
            now = self.read_packet(identifier)['reading_turn']['reading_plan']['delivered_now']
            self.assertLessEqual(sum(row['characters'] for row in now), TURN_BUDGET_CHARACTERS)
            _, sent = self.read_sent_packet(identifier)
            bodies = {row['artifact']['sha256'] for row in now}
            self.assertEqual({entry['artifact']['sha256'] for entry in sent['evidence']}, bodies, identifier)
            self.assertLessEqual(bodies, review_excerpts.find_complete_bodies(sent), identifier)

    def test_record_excerpts_and_passages_are_checked_against_the_exported_json_text(self):
        self.limit_input()
        native_packet = self.export(priority=self.versions)
        name = ('record', 'reading', 'read-a')
        reference = self.find_record_artifacts(native_packet)[name]
        body = self.artifacts.read(reference).decode('utf-8')
        start = body.index(RECORD_PHRASE_JSON)
        end = start + len(RECORD_PHRASE_JSON)
        self.assertEqual(body.count(RECORD_PHRASE_JSON), 2)
        self.assertGreater(len(body[:start].encode('utf-8')), start, 'Character offsets differ from byte offsets')
        identity = {'record_kind': 'reading', 'record_id': 'read-a'}
        request = dict(identity, id='record-passage', anchor=RECORD_PHRASE_COPY[:48], characters=150)

        def respond(packet, number):
            response = self.build_reading_response(packet)
            if name in [name_unit(row) for row in packet['reading_turn']['reading_plan']['delivered_now']]:
                response['excerpts'] += [
                    dict(identity, text=RECORD_PHRASE_COPY, purpose='The criterion as the record states it.'),
                    dict(identity, text=RECORD_PHRASE, purpose='The decoded value of the note.'),
                    {'record_kind': 'reading', 'record_id': 'read-z', 'text': RECORD_PHRASE_JSON,
                     'purpose': 'A record that the packet does not carry.'}]
                response['passage_requests'] = [request]
            return response
        turns = self.run_chain(native_packet, respond, stop_when_read=True)
        requesting = [identifier for identifier in turns
                      if name in [name_unit(row) for row in self.list_delivered_rows([identifier])]]
        self.assertEqual(len(requesting), 1, 'One reading turn delivers the record read-a')
        # The turn after the last reading turn checks its excerpts and answers its passages; the reading record does not
        # depend on the input limit, except for withheld notes.
        with patch.object(transport, 'CODEX_INPUT_LIMIT_CHARS', UNLIMITED_INPUT_CHARACTERS):
            record = self.preview_turn(native_packet, turns[-1], 'turn-%d' % (len(turns) + 1))['reading_record']
        entries = [entry for entry in record['excerpts'] if entry.get('record_kind') == 'reading']
        self.assertEqual(len(entries), 3, record['excerpts'])
        checked, decoded, missing = entries
        self.assertEqual({key: checked.get(key) for key in ('record_id', 'status', 'text', 'start', 'end', 'sha256',
                                                             'purpose')},
                         {'record_id': 'read-a', 'status': 'verified', 'text': body[start:end], 'start': start,
                          'end': end, 'sha256': hash_text(body[start:end]),
                          'purpose': 'The criterion as the record states it.'})
        self.assertEqual({key: decoded.get(key) for key in ('record_id', 'status', 'reason', 'text')},
                         {'record_id': 'read-a', 'status': 'unmatched', 'reason': 'not_found', 'text': RECORD_PHRASE})
        self.assertEqual({key: missing.get(key) for key in ('record_id', 'status', 'reason', 'text')},
                         {'record_id': 'read-z', 'status': 'unmatched', 'reason': 'no_stored_text',
                          'text': RECORD_PHRASE_JSON})
        [passage] = [entry for entry in record['passages'] if entry.get('record_kind') == 'reading']
        self.assertEqual({key: passage.get(key) for key in ('record_id', 'assignment_id', 'request_id', 'status',
                                                             'text', 'start', 'end', 'sha256')},
                         {'record_id': 'read-a', 'assignment_id': requesting[0], 'request_id': 'record-passage',
                          'status': 'delivered', 'text': body[start:start + 150], 'start': start,
                          'end': start + 150, 'sha256': hash_text(body[start:start + 150])})
        for entry in record['excerpts'] + record['passages']:
            self.assertEqual([key for key in VERBOSE_ENTRY_FIELDS if key in entry], [], entry)
        verified = [entry for entry in record['excerpts'] if entry['status'] == 'verified']
        for entry in verified:
            source = (body if 'record_id' in entry
                      else self.artifacts.read(self.texts[entry['version_id']]).decode('utf-8'))
            self.assertEqual((source[entry['start']:entry['end']], hash_text(entry['text'])),
                             (entry['text'], entry['sha256']))
        self.assertEqual(record['allowances']['excerpts']['used'],
                         sum(entry['end'] - entry['start'] for entry in verified))
        self.assertEqual(record['allowances']['passages']['used'], 150)
        sources = record['sources']
        self.assertEqual(len({source['artifact_descriptor']['sha256'] for source in sources}), len(sources))
        [source] = [source for source in sources if source['artifact_descriptor']['sha256'] == reference['sha256']]
        self.assertEqual(source['artifact_descriptor'], descriptor(reference))
        self.assertTrue(any(unit.get('record_kind') == 'reading' and unit.get('record_id') == 'read-a'
                            for unit in source['identities']), source)

    def test_record_checks_read_the_exported_bytes_when_the_store_record_changed(self):
        native_packet = self.export()
        reference = self.find_record_artifacts(native_packet)[('record', 'reading', 'read-a')]
        body = self.artifacts.read(reference).decode('utf-8')
        start = body.index(RECORD_PHRASE_JSON)
        end = start + len(RECORD_PHRASE_JSON)
        changed = copy.deepcopy(self.store.snapshot()['records']['reading']['read-a'])
        for note in changed['notes'].values():
            note['text'] = 'A later version of the note without the criterion.'
        self.put('reading', 'read-a', changed)
        for function in (review_excerpts.check_excerpts, review_excerpts.answer_passages):
            self.assertIn('unit_sources', inspect.signature(function).parameters, function.__name__)
        identity = {'record_kind': 'reading', 'record_id': 'read-a'}
        catalog = {review_excerpts.find_identity(identity): {reference['sha256']: reference}}
        records = self.store.snapshot()['records']
        excerpts = [dict(identity, text=RECORD_PHRASE_COPY, purpose='The criterion as exported.'),
                    dict(identity, text=RECORD_PHRASE, purpose='The decoded value of the note.')]
        requests = [dict(identity, id='record-passage', anchor=RECORD_PHRASE_COPY[:48], characters=150)]
        checked, allowances = review_excerpts.check_excerpts(records, self.artifacts, excerpts,
                                                             review_excerpts.create_allowances(), unit_sources=catalog)
        answers, allowances = review_excerpts.answer_passages(records, self.artifacts, requests, allowances,
                                                              unit_sources=catalog)
        self.assertEqual([(entry['status'], entry['text']) for entry in checked],
                         [('verified', body[start:end]), ('unmatched', RECORD_PHRASE)])
        self.assertEqual(checked[0]['locator'],
                         {'kind': 'span', 'start': start, 'end': end, 'sha256': hash_text(body[start:end])})
        self.assertEqual(checked[0]['artifact_descriptor'], descriptor(reference))
        self.assertEqual(checked[1]['reason'], 'not_found')
        [answer] = answers
        self.assertEqual((answer['status'], answer['text'], answer['artifact_descriptor']),
                         ('delivered', body[start:start + 150], descriptor(reference)))
        self.assertEqual(answer['locator'], {'kind': 'span', 'start': start, 'end': start + 150,
                                             'sha256': hash_text(body[start:start + 150])})
        self.assertEqual((allowances['excerpts']['used'], allowances['passages']['used']), (end - start, 150))
        # Outside a catalog a record identity names no stored text: the store's current record is never a source.
        uncataloged, _ = review_excerpts.check_excerpts(records, self.artifacts, excerpts[:1],
                                                        review_excerpts.create_allowances())
        self.assertEqual([(entry['status'], entry['reason']) for entry in uncataloged], [('unmatched', 'no_stored_text')])


class FinalTurnAcceptanceTests(AcceptanceCase):
    """Three full texts and five records of distinct sizes, so that each step of the withholding order names one unit.
    The text priority lists only the second work, and the minimum lists the smaller search first."""
    text_sizes = (9000, 7000, 8000)
    reading_paddings = {'read-1': 300, 'read-2': 1500, 'read-3': 900}

    def full_note(self, bundle, note_id='full', omit=()):
        note = super().full_note(bundle, note_id, omit)
        note['notes']['problem']['text'] += ' ' + build_filler(self.reading_paddings[note_id])
        return note

    def record_searches(self):
        return [self.record_sized_search(1600), self.record_sized_search(2400)]

    def respond(self, packet, number):
        passages = []
        if number == 1:
            passages = [{'id': 'theorem-1', 'version_id': self.versions[0], 'anchor': build_theorem(1)[:40],
                         'characters': 200}]
        return dict(self.build_reading_response(packet, passages=passages),
                    notes=build_filler(2000) + ' Turn %d.' % number)

    def read_every_unit(self):
        """Export the packet and observe every reading turn of its chain; return the native packet and the turn ids."""
        self.limit_input(34000)
        native_packet = self.export(priority=[self.versions[1]])
        return native_packet, self.run_chain(native_packet, self.respond, stop_when_read=True)

    def test_the_final_turn_withholds_delivered_bodies_then_the_earliest_notes_only_as_far_as_needed(self):
        native_packet, turns = self.read_every_unit()
        final = 'turn-%d' % (len(turns) + 1)
        names = self.name_bodies(native_packet)
        delivering = self.find_delivering_turns(turns)
        self.assertEqual(set(delivering), set(names.values()), 'The reading turns deliver every unit')
        first, second, third = self.versions
        small_search, large_search = [search['id'] for search in self.searches]
        references = dict(self.find_record_artifacts(native_packet))
        references.update((('fulltext', version), self.texts[version]) for version in self.versions)
        size = {name: len(self.artifacts.read(reference).decode('utf-8')) for name, reference in references.items()}
        readings = [('record', 'reading', identifier) for identifier in ('read-2', 'read-3', 'read-1')]
        searches = [('record', 'literature_search', identifier) for identifier in (large_search, small_search)]
        # The order follows from the sizes: unlisted texts, readings and searches each go largest first.
        self.assertGreater(size[('fulltext', first)], size[('fulltext', third)])
        self.assertGreater(size[readings[0]], size[readings[1]])
        self.assertGreater(size[readings[1]], size[readings[2]])
        self.assertGreater(size[searches[0]], size[searches[1]])
        expected = ([('fulltext', second), ('fulltext', first), ('fulltext', third)] + readings + searches
                    + [('notes', identifier) for identifier in turns])
        with patch.object(transport, 'CODEX_INPUT_LIMIT_CHARS', UNLIMITED_INPUT_CHARACTERS):
            baseline = self.preview_turn(native_packet, turns[-1], final)
        self.assertEqual(openness_reading.find_stage(baseline), 'final')
        record = baseline['reading_record']
        self.assertLessEqual({'protocol', 'turns', 'sources', 'excerpts', 'passages', 'allowances'}, set(record))
        self.assertTrue(record['excerpts'] and record['passages'])
        self.assertEqual(self.list_withheld(baseline, names), [])
        packet, withheld = baseline, []
        for item in expected:
            characters = self.measure(packet)
            with patch.object(transport, 'CODEX_INPUT_LIMIT_CHARS', characters):
                self.assertEqual(self.preview_turn(native_packet, turns[-1], final), packet,
                                 'An input at the limit withholds nothing more')
            with patch.object(transport, 'CODEX_INPUT_LIMIT_CHARS', characters - 1):
                try:
                    packet = self.preview_turn(native_packet, turns[-1], final)
                except ResearchError as error:
                    self.fail('The final turn was refused while %s could be withheld: %s %s'
                              % (item, error.code, error.details))
                self.assertEqual(self.build_request(packet, order=transport.FIT_ORDER), self.build_request(packet))
            withheld.append(item)
            self.assertLessEqual(self.measure(packet), characters - 1)
            self.assertCountEqual(self.list_withheld(packet, names), withheld, item)
            self.check_withheld_entries(packet, baseline, names, delivering)
        with patch.object(transport, 'CODEX_INPUT_LIMIT_CHARS', self.measure(packet) - 1):
            with self.assertRaises(ResearchError) as raised:
                self.preview_turn(native_packet, turns[-1], final)
        self.assertEqual(raised.exception.code, 'review_input_too_large')

    def test_the_final_turn_refuses_with_parts_that_sum_to_its_input_when_nothing_more_can_be_withheld(self):
        native_packet, turns = self.read_every_unit()
        final = 'turn-%d' % (len(turns) + 1)
        limit = 2000
        with patch.object(transport, 'CODEX_INPUT_LIMIT_CHARS', limit):
            with self.assertRaises(ResearchError) as raised:
                self.assign(final, native_packet, prior=turns[-1])
        error = raised.exception
        self.assertEqual(error.code, 'review_input_too_large')
        self.assertNotIn(final, self.store.snapshot()['records']['review_assignment'])
        details = error.details
        self.assertIn('parts', details, details)
        parts = details['parts']
        self.assertEqual(sorted(parts), ['base_packet', 'excerpts', 'notes', 'passages'])
        self.assertTrue(all(type(count) is int and count > 0 for count in parts.values()), parts)
        self.assertEqual(sum(parts.values()), details['input_characters'])
        self.assertEqual(details['input_limit_characters'], limit)
        self.assertGreater(details['input_characters'], limit)
        message = str(error)
        for name, count in sorted(parts.items()):
            self.assertTrue(str(count) in message or '{:,}'.format(count) in message, (name, count, message))
        # At exactly the refused size the same assignment is recorded: the candidate that withholds every body that
        # reading turns delivered, except the statement record, and the notes of every turn.
        names = self.name_bodies(native_packet)
        with patch.object(transport, 'CODEX_INPUT_LIMIT_CHARS', UNLIMITED_INPUT_CHARACTERS):
            baseline = self.preview_turn(native_packet, turns[-1], final)
        with patch.object(transport, 'CODEX_INPUT_LIMIT_CHARS', details['input_characters']):
            self.assign_turn(final, native_packet, turns[-1])
        packet = self.read_packet(final)
        self.assertEqual(self.measure(packet), details['input_characters'])
        self.assertCountEqual(self.list_withheld(packet, names),
                              [name for name in names.values() if name[1] != 'problem_statement']
                              + [('notes', identifier) for identifier in turns])
        self.check_withheld_entries(packet, baseline, names, self.find_delivering_turns(turns))
        # The parts are measured as the design states: removing the notes fields of the turns, then the excerpts,
        # then the passages; what remains is the base packet.
        reduced = copy.deepcopy(packet)
        for turn in reduced['reading_record']['turns']:
            turn.pop('notes', None)
            turn.pop('notes_withheld', None)
        without_notes = self.measure(reduced)
        reduced['reading_record']['excerpts'] = []
        without_excerpts = self.measure(reduced)
        reduced['reading_record']['passages'] = []
        base_packet = self.measure(reduced)
        self.assertEqual(parts, {'base_packet': base_packet, 'notes': details['input_characters'] - without_notes,
                                 'excerpts': without_notes - without_excerpts,
                                 'passages': without_excerpts - base_packet})
        self.observe_turn(final, self.build_native_result(packet))


class ProblemOpennessAcceptanceTests(AcceptanceCase):
    """problem-openness records the final turn of a chain only when every unit reached the chain complete."""

    def setUp(self):
        super().setUp()
        self.limit_input()

    def respond(self, packet, number):
        return self.build_reading_response(packet)

    @contextlib.contextmanager
    def skip_unit_in_reading_turns(self, sha256):
        """Run a reading scheduler that skips one unit: the unit lists of openness_reading leave out the rows of one
        body. Verification enumerates the units of the frozen native packet on its own."""
        with contextlib.ExitStack() as stack:
            for name in ('list_reading_texts', 'list_record_rows', 'list_reading_rows'):
                if hasattr(openness_reading, name):
                    rows = build_row_filter(getattr(openness_reading, name), sha256)
                    stack.enter_context(patch.object(openness_reading, name, new=rows))
            yield

    def check_refusal_of_a_missed_unit(self, name):
        """A chain whose reading turns skipped one unit and whose final request does not carry it either: the unit
        reached no turn complete, and problem-openness refuses the final turn and names the unit."""
        native_packet = self.export(priority=self.versions)
        sha256 = {unit: body for body, unit in self.name_bodies(native_packet).items()}[name]
        build_packet = protocol.build_packet

        def build_packet_without_unit(*args, **kwargs):
            packet = build_packet(*args, **kwargs)
            packet['evidence'] = [entry for entry in packet['evidence'] if entry['artifact']['sha256'] != sha256]
            packet['digest'] = digest({key: value for key, value in packet.items() if key != 'digest'})
            return packet
        with self.skip_unit_in_reading_turns(sha256), patch.object(protocol, 'build_packet',
                                                                   new=build_packet_without_unit):
            turns = self.run_chain(native_packet, self.respond)
        final = turns[-1]
        self.observe_turn(final, self.build_native_result(self.read_packet(final)))
        for identifier in turns:
            _, sent = self.read_sent_packet(identifier)
            self.assertNotIn(sha256, review_excerpts.find_complete_bodies(sent),
                             'This test needs the unit out of every actual request: ' + identifier)
        with self.assertRaises(ResearchError) as raised:
            self.record_openness(final)
        error = raised.exception
        self.assertEqual(error.code, 'openness_reading_incomplete', error.details)
        self.assertIn(name, [name_unit(unit) for unit in error.details.get('units', [])], error.details)
        self.assertEqual(self.store.snapshot()['records'].get('problem_openness', {}), {})
        return error

    def test_problem_openness_accepts_a_chain_that_delivered_every_unit_complete(self):
        native_packet = self.export(priority=self.versions)
        turns = self.run_chain(native_packet, self.respond)
        final = turns[-1]
        self.observe_turn(final, self.build_native_result(self.read_packet(final)))
        saved = self.record_openness(final)
        reading_turns = saved['reading_turns']
        self.assertEqual([(turn['assignment_id'], turn['attempt_id']) for turn in reading_turns],
                         [(identifier, 'attempt-' + identifier) for identifier in turns])
        names = self.name_bodies(native_packet)
        for turn in reading_turns:
            delivered = [name_unit(unit) for unit in turn.get('delivered_units', [])]
            self.assertEqual(len(delivered), len(set(delivered)), turn)
            self.assertEqual(set(delivered), self.list_complete_units(turn['assignment_id'], names), turn)
        self.assertCountEqual([name_unit(unit) for turn in reading_turns[:-1]
                               for unit in turn.get('delivered_units', [])], self.list_unit_names())
        records = self.store.snapshot()['records']
        self.assertEqual(openness_reading.verify_chain(records, self.artifacts, records['review_assignment'][final]),
                         reading_turns)
        self.assertEqual(assess_openness(records, self.artifacts), [])

    def test_problem_openness_accepts_a_unit_that_only_the_final_request_delivered_complete(self):
        native_packet = self.export(priority=self.versions)
        names = self.name_bodies(native_packet)
        name = ('record', 'reading', 'read-2')
        with self.skip_unit_in_reading_turns({unit: body for body, unit in names.items()}[name]):
            turns = self.run_chain(native_packet, self.respond)
        final = turns[-1]
        self.assertNotIn(name, [name_unit(row) for row in self.list_delivered_rows(turns[:-1])])
        self.observe_turn(final, self.build_native_result(self.read_packet(final)))
        self.assertIn(name, self.list_complete_units(final, names))
        saved = self.record_openness(final)
        reading_turns = saved['reading_turns']
        self.assertEqual([turn['assignment_id'] for turn in reading_turns], turns)
        self.assertIn(name, [name_unit(unit) for unit in reading_turns[-1].get('delivered_units', [])])
        self.assertNotIn(name, [name_unit(unit) for turn in reading_turns[:-1]
                                for unit in turn.get('delivered_units', [])])
        self.assertEqual(assess_openness(self.store.snapshot()['records'], self.artifacts), [])

    def test_problem_openness_refuses_a_chain_that_missed_a_full_text(self):
        version = self.versions[-1]
        error = self.check_refusal_of_a_missed_unit(('fulltext', version))
        self.assertIn(version, error.details.get('version_ids', []), error.details)

    def test_problem_openness_refuses_a_chain_that_missed_a_search_record(self):
        self.check_refusal_of_a_missed_unit(('record', 'literature_search', self.search['id']))

    def test_problem_openness_refuses_a_chain_that_missed_a_reading_record(self):
        self.check_refusal_of_a_missed_unit(('record', 'reading', 'read-2'))

    def test_problem_openness_refuses_a_chain_that_missed_the_statement_record(self):
        self.check_refusal_of_a_missed_unit(('record', 'problem_statement', self.selected['id']))


class SingleSourceBudgetAcceptanceTests(AcceptanceCase):
    """One fully read source of 120,001 characters, the smallest reproduction of defect D3."""
    text_sizes = (120001,)

    def test_a_single_source_of_120001_characters_starts_a_reading_chain_though_the_packet_fits(self):
        self.check_budget_starts_a_chain(self.export())


class ThreeSourceBudgetAcceptanceTests(AcceptanceCase):
    """Three fully read sources of 397,000 characters together, the earlier case of defect D3."""
    text_sizes = (133000, 132000, 132000)

    def test_three_sources_of_397000_characters_start_a_reading_chain_though_the_packet_fits(self):
        self.check_budget_starts_a_chain(self.export())


class WithinBudgetAcceptanceTests(AcceptanceCase):
    """Three full texts of 30,000 characters, which with the records of the packet stay within one turn's budget."""
    text_sizes = (30000, 30000, 30000)

    def test_full_texts_and_records_within_the_budget_stay_a_single_turn(self):
        native_packet = self.export()
        records = sum(len(self.artifacts.read(reference).decode('utf-8'))
                      for reference in self.find_record_artifacts(native_packet).values())
        self.assertLessEqual(sum(self.text_sizes) + records, TURN_BUDGET_CHARACTERS)
        assignment = self.assign_turn('turn-1', native_packet)
        self.assertNotIn('reading_chain', assignment)
        packet = self.read_packet('turn-1')
        self.assertIsNone(openness_reading.find_stage(packet))
        self.observe_turn('turn-1', self.build_native_result(packet))
        self.assertNotIn('reading_turns', self.record_openness('turn-1'))


class ReadingPromptAcceptanceTests(unittest.TestCase):
    """The prompts of the reading chain: the earlier versions keep their bytes, and v3 states the new rules."""

    def test_the_v1_and_v2_prompts_keep_their_canonical_hashes(self):
        released = load_0_65_2_openness_reading()
        for (reading_protocol, stage), template_hash in EARLIER_PROMPT_HASHES.items():
            with self.subTest(protocol=reading_protocol, stage=stage):
                prompt = protocol.canonical_prompt('native_math', reading_stage=stage,
                                                   reading_protocol=reading_protocol, common_version=1)
                self.assertEqual((prompt['template_id'], prompt['template_hash'], prompt['rendered_prompt_hash']),
                                 (reading_protocol + '/' + stage, template_hash, template_hash))
                with patch.object(protocol, 'openness_reading', released):
                    self.assertEqual(protocol.canonical_prompt('native_math', reading_stage=stage,
                                                               reading_protocol=reading_protocol, common_version=1),
                                     prompt)

    def test_the_v3_prompts_state_the_unit_identities_and_the_withholding_rules(self):
        self.assertEqual(openness_reading.PROTOCOL, PROTOCOL_V3)
        texts = openness_reading.PROMPTS[PROTOCOL_V3]
        reading = protocol.canonical_prompt('native_math', reading_stage='reading')
        final = protocol.canonical_prompt('native_math', reading_stage='final')
        for stage, prompt in (('reading', reading), ('final', final)):
            with self.subTest(stage=stage):
                self.assertEqual(prompt['template_id'], PROTOCOL_V3 + '/' + stage)
                self.assertNotIn(prompt['template_hash'], EARLIER_PROMPT_HASHES.values())
                for phrase in V3_PROMPT_PHRASES:
                    self.assertIn(phrase, texts[stage])
        self.assertIn('120,000', texts['reading'])
        common = reading['text'][:len(reading['text']) - len(texts['reading'])]
        self.assertEqual(reading['text'], common + texts['reading'])
        self.assertEqual(final['text'], common + native_review.PROMPT + texts['final'])
        self.assertTrue(texts['final'].startswith(' ') and not texts['final'].startswith('  '))


if __name__ == '__main__':
    unittest.main()
