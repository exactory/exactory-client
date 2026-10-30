"""The final turn of an openness reading chain stays within the route input. The records that the packet carries
arrive as reading units after the full texts, excerpts and passages of a record are checked against its exact bytes,
and the final turn withholds, only as far as its input needs, the bodies that reading turns delivered and then the
notes of the earliest turns, never an excerpt or a passage."""

import hashlib
import json
from pathlib import Path
import unittest
from unittest.mock import patch

from openness_legacy_fixtures import build_as_0_65_2
from openness_reading_fixtures import OpennessReadingCase, build_theorem
from research_harness import openness_reading, review_excerpts, review_protocol as protocol
from research_harness import review_codex_transport as codex, review_transport as transport
from research_harness.acquisition import import_response
from research_harness.errors import ResearchError
from research_harness.literature import record_search
from research_harness.problem_openness import serialize_record

BODY_WITHHELD = 'body-delivered-in-reading-turns'
UNLIMITED_INPUT_CHARACTERS = 10 ** 8
# The KMP record's openness packet of statement-4 (2026-10-28): the extracted full texts of its eight readings, and
# its seventeen record artifacts, eight search records, eight reading records and the statement record, of 8,418 to
# 84,505 characters and about 431,000 together.
KMP_TEXT_SIZES = (291855, 195429, 63236, 304336, 101335, 243326, 185115, 213686)
KMP_SEARCH_SIZES = (18000, 16500, 15000, 13500, 12500, 11500, 10500, 10500)
KMP_READING_SIZES = (84505, 61000, 47000, 38000, 30000, 24000, 18000, 12000)
KMP_STATEMENT_SIZE = 8418
KMP_NOTES_CHARACTERS = 13500
KMP_EXCERPTS = 339
KMP_EXCERPT_CHARACTERS = 585
KMP_PASSAGES = 13
KMP_PASSAGE_CHARACTERS = 7565


def build_filler(characters, sentence='The recorded work proves a bounded case and leaves the statement open. '):
    """Plain text of exactly the given length."""
    return (sentence * (characters // len(sentence) + 1))[:characters]


def hash_text(value):
    return hashlib.sha256(value.encode('utf-8')).hexdigest()


class FinalTurnCase(OpennessReadingCase):
    """A reading chain whose turns a test drives one by one, and rebuilds without recording them."""

    def read_route(self):
        return self.store.snapshot()['records']['review_route']['route']

    def peek(self, native_packet, prior):
        """The packet that the assignment after `prior` would get, built without recording it."""
        records = self.store.snapshot()['records']
        value = {'id': 'peek', 'route_id': 'route', 'dossier_id': None, 'role': 'native_math',
                 'reviewer_id': 'openness-reviewer', 'author_id': 'problem-author',
                 'context': {'native_packet': native_packet, 'prior_assignment_id': prior}}
        with transport.text_only_delivery(True):
            packet = protocol.build_packet(records, self.artifacts, None, 'native_math', 'openness-reviewer',
                                           native_packet=native_packet)
            return openness_reading.prepare_packet(records, self.artifacts, value, self.read_route(), packet)[0]

    def measure(self, packet):
        """The input characters of a packet's request as the Codex route measures them."""
        prompt = protocol.canonical_prompt('native_math', reading_stage=openness_reading.find_stage(packet))['text']
        return len(codex.packet_text(transport.request_body(self.read_route(), prompt, packet)))

    def run_chain(self, native_packet, respond, turn_count=30, stop_before_final=False):
        """Assign and observe the turns of a chain, answering each with respond(packet, number). Return the turn ids:
        the last is the final turn, or with stop_before_final the reading turn after which nothing remains to read."""
        identifiers = ['turn-1']
        self.assign('turn-1', native_packet)
        while True:
            packet = self.read_packet(identifiers[-1])
            self.invoke(identifiers[-1], respond(packet, len(identifiers)))
            if stop_before_final and not packet['reading_turn']['reading_plan']['remaining']:
                return identifiers
            identifiers.append('turn-%d' % (len(identifiers) + 1))
            self.assign(identifiers[-1], native_packet, prior=identifiers[-2])
            self.assertLessEqual(len(identifiers), turn_count, 'The chain must end within its bounded number of turns')
            if 'reading_turn' not in self.read_packet(identifiers[-1]):
                return identifiers

    def continue_chain(self, native_packet, identifiers, respond, turn_count=30):
        """Continue a chain from its last observed turn to its final turn; return all turn ids."""
        identifiers = list(identifiers)
        while True:
            identifiers.append('turn-%d' % (len(identifiers) + 1))
            self.assign(identifiers[-1], native_packet, prior=identifiers[-2])
            self.assertLessEqual(len(identifiers), turn_count, 'The chain must end within its bounded number of turns')
            if 'reading_turn' not in self.read_packet(identifiers[-1]):
                return identifiers
            self.invoke(identifiers[-1], respond(self.read_packet(identifiers[-1]), len(identifiers)))

    def list_delivered(self, identifiers):
        """The plan entries that the reading turns among identifiers delivered, in order."""
        packets = [self.read_packet(identifier) for identifier in identifiers]
        return [row for packet in packets if 'reading_turn' in packet
                for row in packet['reading_turn']['reading_plan']['delivered_now']]

    def list_record_names(self):
        """The records of the openness packet in their delivery order: the searches of the minimum, the readings by
        record id and the statement."""
        return ([('literature_search', search['id']) for search in self.searches]
                + [('reading', 'read-%d' % number) for number in range(1, len(self.versions) + 1)]
                + [('problem_statement', self.selected['id'])])

    def read_record_text(self, record_kind, record_id):
        return serialize_record(self.store.snapshot()['records'][record_kind][record_id]).decode('utf-8')

    def record_openness(self, identifier):
        return self.mutate(self.load_problem_module().record_openness, {'assignment_id': identifier})['result']


class RecordUnitTests(FinalTurnCase):
    def test_records_arrive_as_reading_units_after_the_full_texts(self):
        self.limit_input()
        native_packet = self.export(priority=self.versions)
        turns = self.run_chain(native_packet, lambda packet, number: self.build_reading_response(packet))
        rows = self.list_delivered(turns)
        self.assertEqual([(row['kind'], row.get('version_id')) for row in rows[:3]],
                         [('fulltext', version) for version in self.versions])
        self.assertEqual([(row['kind'], row['record_kind'], row['record_id']) for row in rows[3:]],
                         [('record',) + name for name in self.list_record_names()])
        native = json.loads(self.artifacts.read(native_packet))
        carried = {entry['artifact']['sha256'] for entry in native['evidence']}
        for identifier in turns[:-1]:
            _, sent = self.read_sent_packet(identifier)
            complete = review_excerpts.find_complete_bodies(sent)
            for row in self.read_packet(identifier)['reading_turn']['reading_plan']['delivered_now']:
                self.assertIn(row['artifact']['sha256'], complete)
                if row['kind'] == 'record':
                    content = self.read_record_text(row['record_kind'], row['record_id'])
                    self.assertEqual(self.artifacts.read(row['artifact']).decode('utf-8'), content)
                    self.assertIn(row['artifact']['sha256'], carried)
                    self.assertEqual((row['part'], row['parts'], row['start'], row['end'], row['characters']),
                                     (1, 1, 0, len(content), len(content)))
        final = turns[-1]
        record = self.read_packet(final)['reading_record']
        self.assertEqual(record['protocol'], openness_reading.PROTOCOL)
        names = ([self.unit_identity(version) for version in self.versions]
                 + [{'kind': 'record', 'record_kind': kind, 'record_id': identifier}
                    for kind, identifier in self.list_record_names()])
        self.assertEqual([unit for turn in record['turns'] for unit in turn['delivered_units']], names)
        self.invoke(final, self.build_native_result(self.read_packet(final)))
        saved = self.record_openness(final)
        self.assertEqual([turn['assignment_id'] for turn in saved['reading_turns']], turns)
        self.assertEqual([unit for turn in saved['reading_turns'][:-1] for unit in turn['delivered_units']], names)
        self.assertEqual(sorted(saved['reading_turns'][0]), ['assignment_id', 'attempt_id', 'delivered_units'])

    def test_excerpts_and_passages_of_a_record_are_checked_against_its_bytes(self):
        self.limit_input()
        native_packet = self.export(priority=self.versions)
        content = self.read_record_text('reading', 'read-2')
        start = content.index('The bounded example provides')
        phrase = content[start:start + 80]
        request = {'id': 'record-passage', 'record_kind': 'reading', 'record_id': 'read-2', 'anchor': phrase[:40],
                   'characters': 150}

        def respond(packet, number):
            response = self.build_reading_response(packet)
            delivered = [(row.get('record_kind'), row.get('record_id'))
                         for row in packet['reading_turn']['reading_plan']['delivered_now']]
            if ('reading', 'read-2') in delivered:
                response['excerpts'] += [
                    {'record_kind': 'reading', 'record_id': 'read-2', 'text': phrase.replace(' ', '  '),
                     'purpose': 'The reading note of the second work.'},
                    {'record_kind': 'reading', 'record_id': 'read-9', 'text': phrase,
                     'purpose': 'A record that the packet does not carry.'}]
                response['passage_requests'] = [request]
            return response
        turns = self.run_chain(native_packet, respond)
        record = self.read_packet(turns[-1])['reading_record']
        [verified, unmatched] = [row for row in record['excerpts'] if 'record_kind' in row]
        self.assertEqual(verified, {'record_kind': 'reading', 'record_id': 'read-2',
                                    'purpose': 'The reading note of the second work.', 'status': 'verified',
                                    'text': phrase, 'start': start, 'end': start + 80, 'sha256': hash_text(phrase)})
        self.assertEqual((unmatched['status'], unmatched['reason'], unmatched['text']),
                         ('unmatched', 'no_stored_text', phrase))
        [unit] = [row for row in self.list_delivered(turns) if row.get('record_id') == 'read-2']
        self.assertIn({'identities': [{'kind': 'record', 'record_kind': 'reading', 'record_id': 'read-2'}],
                       'artifact_descriptor': openness_reading.descriptor(unit['artifact'])}, record['sources'])
        [passage] = record['passages']
        [requesting] = [identifier for identifier in turns[:-1] if any(
            row.get('record_id') == 'read-2'
            for row in self.read_packet(identifier)['reading_turn']['reading_plan']['delivered_now'])]
        self.assertEqual(passage, {'assignment_id': requesting, 'request_id': 'record-passage',
                                   'record_kind': 'reading', 'record_id': 'read-2', 'status': 'delivered',
                                   'text': content[start:start + 150],
                                   'start': start, 'end': start + 150, 'sha256': hash_text(content[start:start + 150])})
        [theorem] = [row for row in record['excerpts'] if row.get('version_id') == self.versions[0]]
        body = self.artifacts.read(self.texts[self.versions[0]]).decode('utf-8')
        offset = body.index(build_theorem(1))
        self.assertEqual(theorem, {'version_id': self.versions[0], 'purpose': 'The result that bears on the exact '
                                   'statement.', 'status': 'verified', 'text': build_theorem(1), 'start': offset,
                                   'end': offset + len(build_theorem(1)), 'sha256': hash_text(build_theorem(1))})
        self.assertIn({'identities': [{'kind': 'fulltext', 'version_id': self.versions[0]}],
                       'artifact_descriptor': openness_reading.descriptor(self.texts[self.versions[0]])},
                      record['sources'])


class FinalTurnWithholdingTests(FinalTurnCase):
    """Three full texts of different sizes and three reading records of different sizes, read without a text
    priority, so that the harness itself withholds every body that the final turn gives up."""
    text_sizes = (9000, 7000, 8000)
    reading_padding = {'read-1': 300, 'read-2': 1500, 'read-3': 900}

    def full_note(self, bundle, note_id='full', omit=()):
        note = super().full_note(bundle, note_id, omit)
        note['notes']['problem']['text'] += ' ' + build_filler(self.reading_padding.get(note_id, 0))
        return note

    def name_bodies(self, native):
        """The name of each unit body of the openness packet by its SHA-256: (kind, identifier)."""
        names = {self.texts[version]['sha256']: ('fulltext', version) for version in self.versions}
        for row in openness_reading.list_record_rows(self.artifacts, native):
            names[row['artifact']['sha256']] = (row['record_kind'], row['record_id'])
        return names

    def list_withheld(self, packet):
        """What a final packet withholds: the evidence bodies by the name of their unit, then the turns whose notes
        it withholds."""
        names = self.name_bodies(packet['native'])
        return ([names[entry['artifact']['sha256']] for entry in packet['evidence']
                 if entry.get('delivery') == BODY_WITHHELD]
                + [('notes', turn['assignment_id']) for turn in packet['reading_record']['turns']
                   if 'notes' not in turn])

    def test_the_final_turn_withholds_bodies_then_the_earliest_notes_only_as_far_as_needed(self):
        self.limit_input(30000)
        native_packet = self.export()
        passage = {'id': 'theorem-1', 'version_id': self.versions[0], 'anchor': build_theorem(1)[:40],
                   'characters': 200}

        def respond(packet, number):
            response = dict(self.build_reading_response(packet), notes=build_filler(2000) + str(number))
            if number == 1:
                response['passage_requests'] = [passage]
            return response
        turns = self.run_chain(native_packet, respond, stop_before_final=True)
        delivering = {}
        for identifier in turns:
            for row in self.read_packet(identifier)['reading_turn']['reading_plan']['delivered_now']:
                delivering.setdefault(row['artifact']['sha256'], []).append(identifier)
        with patch.object(transport, 'CODEX_INPUT_LIMIT_CHARS', UNLIMITED_INPUT_CHARACTERS):
            packet = self.peek(native_packet, turns[-1])
        baseline = packet['reading_record']
        self.assertEqual(self.list_withheld(packet), [])
        self.assertEqual([(row['assignment_id'], row['request_id']) for row in baseline['passages']],
                         [('turn-1', 'theorem-1')])
        names = self.name_bodies(packet['native'])
        [statement] = [sha256 for sha256, name in names.items() if name[0] == 'problem_statement']
        steps, refusal = [], None
        while refusal is None:
            limit = self.measure(packet) - 1
            try:
                with patch.object(transport, 'CODEX_INPUT_LIMIT_CHARS', limit):
                    packet = self.peek(native_packet, turns[-1])
            except ResearchError as error:
                refusal = error
                break
            self.assertLessEqual(self.measure(packet), limit)
            withheld = self.list_withheld(packet)
            [new] = [item for item in withheld if item not in steps]
            self.assertEqual(sorted(withheld), sorted(steps + [new]), 'Each step withholds one more item')
            steps.append(new)
            for key in ('excerpts', 'passages', 'sources', 'allowances'):
                self.assertEqual(packet['reading_record'][key], baseline[key])
            for entry in packet['evidence']:
                if entry['artifact']['sha256'] == statement:
                    self.assertEqual(entry['content'].encode('utf-8'), self.artifacts.read(entry['artifact']))
                if entry.get('delivery') == BODY_WITHHELD:
                    self.assertEqual(entry['content'], '')
                    for identifier in delivering[entry['artifact']['sha256']]:
                        self.assertIn(identifier, entry['note'])
            for turn in packet['reading_record']['turns']:
                if 'notes' not in turn:
                    self.assertIn(turn['assignment_id'], turn['notes_withheld'])
        expected = ([('fulltext', self.versions[0]), ('fulltext', self.versions[2]), ('fulltext', self.versions[1]),
                     ('reading', 'read-2'), ('reading', 'read-3'), ('reading', 'read-1'),
                     ('literature_search', self.search['id'])] + [('notes', identifier) for identifier in turns])
        self.assertEqual(steps, expected)
        self.assertEqual(refusal.code, 'review_input_too_large')
        parts = refusal.details['parts']
        self.assertEqual(sorted(parts), ['base_packet', 'excerpts', 'notes', 'passages'])
        self.assertTrue(all(value > 0 for value in parts.values()), parts)
        self.assertEqual(sum(parts.values()), refusal.details['input_characters'])
        self.assertEqual(refusal.details['input_characters'], self.measure(packet))
        for name in ('base packet', 'notes', 'excerpts', 'passages'):
            self.assertIn(name, str(refusal))


class ReadingTurnNotesTests(FinalTurnCase):
    text_sizes = (6000, 4000, 5000)

    def test_a_reading_turn_withholds_the_earliest_notes_when_its_request_would_not_fit(self):
        self.limit_input()
        native_packet = self.export(priority=self.versions)
        notes = build_filler(6000)

        def respond(packet, number):
            return dict(self.build_reading_response(packet), notes=notes + str(number))
        budget = patch.object(review_excerpts, 'TURN_FULLTEXT_CHARACTERS', 6500)
        budget.start()
        self.addCleanup(budget.stop)
        self.assign('turn-1', native_packet)
        self.invoke('turn-1', respond(self.read_packet('turn-1'), 1))
        open_packet = self.peek(native_packet, 'turn-1')
        delivered = open_packet['reading_turn']['reading_plan']['delivered_now']
        self.assertEqual([row.get('version_id') for row in delivered], [self.versions[1]])
        with patch.object(transport, 'CODEX_INPUT_LIMIT_CHARS', self.measure(open_packet) - 1):
            self.assign('turn-2', native_packet, prior='turn-1')
        packet = self.read_packet('turn-2')
        self.assertEqual(packet['reading_turn']['reading_plan']['delivered_now'], delivered)
        [turn] = packet['reading_turn']['reading_record']['turns']
        self.assertNotIn('notes', turn)
        self.assertIn('turn-1', turn['notes_withheld'])
        self.invoke('turn-2', respond(packet, 2))
        state = protocol.assignment_state(self.store.snapshot()['records'], self.artifacts, 'turn-2')
        self.assertTrue(state['ready'], state['obligations'])
        with patch.object(transport, 'CODEX_INPUT_LIMIT_CHARS', UNLIMITED_INPUT_CHARACTERS):
            following = self.peek(native_packet, 'turn-2')['reading_turn']['reading_record']['turns']
        self.assertEqual([row['notes'] for row in following], [notes + '1', notes + '2'])


class ContinuedChainTests(FinalTurnCase):
    """Chains that 0.65.2 started, with v2 prompts, the verbose reading record and no record units."""

    def test_a_0_65_2_chain_whose_texts_are_read_continues_with_reading_turns_for_its_records(self):
        from research_harness.problem_openness import assess_openness
        self.limit_input()
        native_packet = self.export(priority=self.versions)
        with build_as_0_65_2():
            turns = self.run_chain(native_packet, lambda packet, number: self.build_reading_response(packet),
                                   stop_before_final=True)
        self.assertEqual(turns, ['turn-1', 'turn-2'])
        for identifier in turns:
            assignment = self.store.snapshot()['records']['review_assignment'][identifier]
            self.assertEqual(assignment['prompt']['template_id'], openness_reading.PROTOCOL_V2 + '/reading')
            self.assertNotIn('kind', self.read_packet(identifier)['reading_turn']['reading_plan']['delivered_now'][0])
        turns = self.continue_chain(native_packet, turns, lambda packet, number: self.build_reading_response(packet))
        records = self.store.snapshot()['records']
        next_turn = records['review_assignment'][turns[2]]
        self.assertEqual(next_turn['prompt']['template_id'], openness_reading.PROTOCOL + '/reading')
        self.assertEqual([(row['kind'], row['record_kind'], row['record_id'])
                          for row in self.list_delivered(turns[2:])],
                         [('record',) + name for name in self.list_record_names()])
        record = self.read_packet(turns[2])['reading_turn']['reading_record']
        self.assertEqual(record['protocol'], openness_reading.PROTOCOL)
        self.assertEqual([turn['delivered_units'] for turn in record['turns']],
                         [[self.unit_identity(version) for version in self.versions[:2]],
                          [self.unit_identity(self.versions[2])]])
        for excerpt in record['excerpts']:
            self.assertEqual(sorted(excerpt), ['end', 'purpose', 'sha256', 'start', 'status', 'text', 'version_id'])
        self.assertEqual(sorted(identity['version_id'] for source in record['sources']
                                for identity in source['identities']), sorted(self.versions))
        for identifier in turns[:2]:
            state = protocol.assignment_state(records, self.artifacts, identifier)
            self.assertTrue(state['ready'], state['obligations'])
        final = turns[-1]
        self.assertEqual(records['review_assignment'][final]['prompt']['template_id'],
                         openness_reading.PROTOCOL + '/final')
        self.invoke(final, self.build_native_result(self.read_packet(final)))
        saved = self.record_openness(final)
        self.assertEqual(saved['reading_turns'][:2], [
            {'assignment_id': 'turn-1', 'attempt_id': 'attempt-turn-1',
             'delivered_units': [self.unit_identity(version) for version in self.versions[:2]]},
            {'assignment_id': 'turn-2', 'attempt_id': 'attempt-turn-2',
             'delivered_units': [self.unit_identity(self.versions[2])]}])
        self.assertEqual(assess_openness(self.store.snapshot()['records'], self.artifacts), [])

    def test_a_finding_from_a_0_65_2_final_turn_keeps_its_reading_turns_and_stays_current(self):
        from research_harness.problem_openness import assess_openness
        self.limit_input()
        native_packet = self.export(priority=self.versions)
        with build_as_0_65_2():
            turns = self.run_reading_turns(native_packet)
        self.assertEqual(turns, ['turn-1', 'turn-2', 'turn-3'])
        final = turns[-1]
        packet = self.read_packet(final)
        self.assertEqual(packet['reading_record']['protocol'], openness_reading.PROTOCOL_V2)
        self.assertEqual(self.store.snapshot()['records']['review_assignment'][final]['prompt']['template_id'],
                         openness_reading.PROTOCOL_V2 + '/final')
        self.invoke(final, self.build_native_result(packet))
        saved = self.record_openness(final)
        self.assertEqual(saved['reading_turns'], [
            {'assignment_id': 'turn-1', 'attempt_id': 'attempt-turn-1', 'delivered': self.versions[:2]},
            {'assignment_id': 'turn-2', 'attempt_id': 'attempt-turn-2', 'delivered': self.versions[2:]}])
        self.assertEqual(assess_openness(self.store.snapshot()['records'], self.artifacts), [])


class KmpShapedChainTests(FinalTurnCase):
    """The KMP record's openness chain: 0.65.2 read the eight full texts in seventeen turns, with notes of 13,500
    characters, 339 verified excerpts and 13 passages; the chain then continues under the current rules."""
    text_sizes = KMP_TEXT_SIZES

    def build_statement(self, identifier='statement-1', **changes):
        statement = super().build_statement(identifier, **changes)
        missing = KMP_STATEMENT_SIZE - len(serialize_record(statement)) - 60
        return dict(statement, resolution_criteria=statement['resolution_criteria'] + ' ' + build_filler(missing))

    def full_note(self, bundle, note_id='full', omit=()):
        note = super().full_note(bundle, note_id, omit)
        target = KMP_READING_SIZES[int(note_id.rsplit('-', 1)[1]) - 1]
        note['notes']['problem']['text'] += ' ' + build_filler(target - len(serialize_record(note)) - 120)
        return note

    def record_searches(self):
        return [self.record_search(size) for size in KMP_SEARCH_SIZES]

    def record_search(self, size):
        """A search that finds every read work, with its impact text sized for a record of about `size` characters."""
        formulation = self.selected['statement']
        data = {'query': formulation, 'results': [{'id': work, 'title': 'Authored mathematical source'}
                                                  for work in self.versions]}
        self.sequence += 1
        captured = import_response(self.store, 'mcp', json.dumps(data).encode(),
                                   source_url='https://example.org/problem-search',
                                   captured_at='2026-10-15T12:00:00Z', media_type='application/json',
                                   mappings=[{'id': '/results/%d/id' % index, 'title': '/results/%d/title' % index}
                                             for index in range(len(self.versions))],
                                   expected_revision=self.store.revision,
                                   request_id='problem-search-capture-' + str(self.sequence))
        identifier = 'search-' + str(self.sequence)
        value = {'id': identifier, 'profile': 'research', 'purpose': 'direct', 'question': formulation,
                 'scope': formulation, 'queries': [formulation],
                 'responses': [{'source_id': captured['source_ids'][0], 'query': formulation,
                                'query_locator': {'kind': 'json', 'pointer': '/query', 'value': formulation},
                                'results_pointer': '/results'}], 'captured_at': '2026-10-15T12:00:00Z',
                 'found_work_ids': list(self.versions), 'verdict': 'nothing-new', 'cited_work_ids': [],
                 'dispositions': [{'work_id': work, 'disposition': 'relevant',
                                   'reason': 'Explicit relevance judgment of the captured source.'}
                                  for work in self.versions],
                 'resolved': [], 'impact': '', 'gaps': []}
        value['impact'] = build_filler(size - len(json.dumps(value)) - 150)
        self.mutate(record_search, value)
        return self.store.snapshot()['records']['literature_search'][identifier]

    def respond(self, packet, number):
        """A reading answer of the KMP shape: notes of 13,500 characters, twenty excerpts of 585 characters of a full
        text delivered now (339 in all, copied from its stored text), and one passage request in each of the turns 2
        to 14."""
        rows = [row for row in packet['reading_turn']['reading_plan']['delivered_now']
                if row.get('kind', 'fulltext') == 'fulltext']
        excerpts, passages = [], []
        if rows:
            content = self.artifacts.read(self.texts[rows[0]['version_id']]).decode('utf-8')
            count = min(20, KMP_EXCERPTS - 20 * (number - 1))
            excerpts = [{'version_id': rows[0]['version_id'], 'purpose': 'A step of the argument.',
                         'text': content[1000 + 600 * index:1000 + 600 * index + KMP_EXCERPT_CHARACTERS]}
                        for index in range(count)]
            if 2 <= number <= KMP_PASSAGES + 1:
                anchor = content[1000:1060]
                passages = [{'id': 'passage-%d' % number, 'version_id': rows[0]['version_id'], 'anchor': anchor,
                             'characters': KMP_PASSAGE_CHARACTERS}]
        if number == 1:
            excerpts.extend({'version_id': rows[0]['version_id'], 'purpose': 'An unchecked quotation.',
                             'text': 'Unmatched retained reviewer copy ' + str(index)} for index in range(2))
        return dict(self.build_reading_response(packet, excerpts=excerpts, passages=passages),
                    notes=build_filler(KMP_NOTES_CHARACTERS))

    def test_a_kmp_shaped_chain_of_0_65_2_reaches_a_final_turn_within_the_codex_limit(self):
        from research_harness.problem_openness import assess_openness
        native_packet = self.export(priority=self.versions)
        records = self.store.snapshot()['records']
        native = json.loads(self.artifacts.read(native_packet))
        sizes = [len(self.artifacts.read(row['artifact']))
                 for row in openness_reading.list_record_rows(self.artifacts, native)]
        self.assertEqual(len(sizes), 17)
        self.assertTrue(8000 <= min(sizes) and max(sizes) <= 85000, sizes)
        self.assertAlmostEqual(sum(sizes), 431000, delta=8000)
        with build_as_0_65_2():
            turns = self.run_chain(native_packet, self.respond, stop_before_final=True)
        self.assertEqual(len(turns), 17)
        for identifier in turns:
            record = self.read_packet(identifier)['reading_turn']['reading_record']
            self.assertTrue(all('notes' in turn for turn in record['turns']))
            request, _ = self.read_sent_packet(identifier)
            self.assertLessEqual(len(codex.packet_text(request)), transport.CODEX_INPUT_LIMIT_CHARS)
        turns = self.continue_chain(native_packet, turns, self.respond)
        self.assertGreater(len(turns), 18)
        for identifier in turns[17:-1]:
            request, _ = self.read_sent_packet(identifier)
            self.assertLessEqual(len(codex.packet_text(request)), transport.CODEX_INPUT_LIMIT_CHARS)
            for row in self.read_packet(identifier)['reading_turn']['reading_plan']['delivered_now']:
                self.assertEqual(row['kind'], 'record')
        final = turns[-1]
        packet = self.read_packet(final)
        self.assertLessEqual(self.measure(packet), transport.CODEX_INPUT_LIMIT_CHARS)
        record = packet['reading_record']
        verified = [row for row in record['excerpts'] if row['status'] == 'verified']
        self.assertEqual(len(verified), KMP_EXCERPTS)
        self.assertEqual(len([row for row in record['excerpts'] if row['status'] == 'unmatched']), 2)
        for identifier in turns[:17]:
            state = protocol.assignment_state(self.store.snapshot()['records'], self.artifacts, identifier)
            self.assertTrue(state['ready'], state['obligations'])
        measured = openness_reading._measure_parts(
            self.read_route(), protocol.canonical_prompt('native_math', reading_stage='final')['text'], packet)
        self.assertEqual(sum(measured['parts'].values()), measured['input_characters'])
        self.assertLessEqual(measured['input_characters'], transport.CODEX_INPUT_LIMIT_CHARS)
        self.assertTrue(all(len(row['text']) >= KMP_EXCERPT_CHARACTERS - 5 for row in verified))
        self.assertEqual([row['status'] for row in record['passages']], ['delivered'] * KMP_PASSAGES)
        self.assertEqual(record['allowances']['passages']['used'], KMP_PASSAGES * KMP_PASSAGE_CHARACTERS)
        self.assertGreaterEqual(len(record['turns']), 18)
        withheld = [entry for entry in packet['evidence'] if entry.get('delivery') == BODY_WITHHELD]
        self.assertTrue(withheld)
        self.invoke(final, self.build_native_result(packet))
        saved = self.record_openness(final)
        self.assertEqual([turn['assignment_id'] for turn in saved['reading_turns']], turns)
        self.assertEqual(assess_openness(self.store.snapshot()['records'], self.artifacts), [])


class AlternateRepresentationTests(unittest.TestCase):
    """An openness packet with native reviews selects one main representation of each read version. The alternate
    that its openness_delivery contract omits is not a reading unit, so a small packet stays a single turn."""

    def setUp(self):
        import test_problem_openness_native_reviews as native_fixtures
        import test_research_problem_openness_minimum as minimum_fixtures
        from codex_route_fixtures import record_codex_route
        from research_harness.literature import import_bundle
        from research_harness.reading import record_reading
        self.case = case = native_fixtures.NativeOpennessReviewTests()
        self.addCleanup(case.doCleanups)
        case.setUp()
        self.work = case.metadata()
        abstract = case.artifacts.read(case.store.snapshot()['records']['work'][self.work]['abstract']).decode()
        self.pdf = case.capture(self.work, pdf_text=abstract + '\nComplete authored source l. ' * 24
                                + '\nReferences: none.\f')
        self.html = case.capture(self.work, body='Alternative complete authored source. ' * 35 + 'References: none.')
        for name, capture in (('pdf', self.pdf), ('html', self.html)):
            bundle = case.bundle(self.work, capture, bundle_id=name + '-bundle')
            self.use_span_locators(bundle)
            case.mutate(import_bundle, bundle)
            case.mutate(record_reading, case.full_note(bundle, name + '-reading'))
        case.search = minimum_fixtures.ProblemOpennessMinimumTests.search(case, [self.work])
        record_codex_route(case, 'codex-route')

    def use_span_locators(self, value):
        """Locate the quotations of a bundle by span, so that each reading links the extracted text it read."""
        if isinstance(value, dict):
            if value.get('locator', {}).get('kind') == 'text':
                value['locator'] = self.case.span(value['artifact'], value['locator']['quote'], 'span')
            for item in value.values():
                self.use_span_locators(item)
        elif isinstance(value, list):
            for item in value:
                self.use_span_locators(item)

    def test_an_omitted_alternate_representation_is_not_a_reading_unit(self):
        case = self.case
        native = case.export(assignment='openness-codex')
        delivery = native['scientific_context']['openness_delivery']
        self.assertEqual(delivery['sources'][0]['selected_text_sha256'], self.pdf['text']['sha256'])
        self.assertIn(self.html['text']['sha256'], delivery['alternate_body_digests'])
        records = case.store.snapshot()['records']
        self.assertEqual([row['text_sha256'] for row in openness_reading.list_reading_texts(records, native)],
                         [self.pdf['text']['sha256']])
        assignment = case.mutate(protocol.record_assignment, {
            'id': 'openness-codex', 'route_id': 'codex-route', 'dossier_id': None, 'role': 'native_math',
            'reviewer_id': native['reviewer']['actor_id'], 'author_id': 'problem-author',
            'context': {'native_packet': case.pin_json(native)}})['result']
        self.assertNotIn('reading_chain', assignment)


class ReadingPromptTests(unittest.TestCase):
    def test_the_v1_and_v2_prompts_keep_their_bytes_and_the_v3_prompts_state_the_new_rules(self):
        v1, v2 = openness_reading.PROTOCOL_V1, openness_reading.PROTOCOL_V2
        earlier = {(v1, 'reading'): 'c3b851f86bef6f01a1ba4318d95b89cb20ce8f6bb5f6f237d8e555de78beff9f',
                   (v1, 'final'): '5a9359e8c3b9c27301011cab6a19b1157d9f466e4c5ce2db8d9940a02bde9739',
                   (v2, 'reading'): '0cd98895abb64e7da1674dc1f9e332c2cba309e6a6bfb8560903ad842f4a4857',
                   (v2, 'final'): 'e52d3ac0cebb2b5ab2cb79e5412138d74fc98374c4fe53614bcbf019f95ca301'}
        for (reading_protocol, stage), template_hash in earlier.items():
            with self.subTest(protocol=reading_protocol, stage=stage):
                prompt = protocol.canonical_prompt('native_math', reading_stage=stage,
                                                   reading_protocol=reading_protocol, common_version=1)
                self.assertEqual((prompt['template_id'], prompt['template_hash']),
                                 (reading_protocol + '/' + stage, template_hash))
                self.assertTrue(protocol._prompt_matches({'role': 'native_math', 'prompt': prompt, 'context': {}}))
        self.assertEqual(openness_reading.PROTOCOL, 'exactory-openness-reading-v3')
        self.assertEqual(sorted(openness_reading.PROMPTS),
                         [openness_reading.PROTOCOL_V1, openness_reading.PROTOCOL_V2, openness_reading.PROTOCOL])
        for stage in openness_reading.STAGES:
            prompt = protocol.canonical_prompt('native_math', reading_stage=stage)
            self.assertEqual(prompt['template_id'], openness_reading.PROTOCOL + '/' + stage)
            self.assertNotIn(prompt['template_hash'], earlier.values())
            self.assertTrue(protocol._prompt_matches({'role': 'native_math', 'prompt': prompt, 'context': {}}))
            expected = (Path(__file__).parent / 'fixtures' / ('openness-' + stage + '-v3.txt')).read_text().rstrip('\n')
            actual = openness_reading.PROMPTS[openness_reading.PROTOCOL][stage]
            self.assertEqual(actual, (' ' if stage == 'final' else '') + expected)
            for phrase in ('record_kind', 'record_id', 'version_id', 'source_id', 'text_sha256', 'JSON',
                           'notes_withheld', 'never withholds an excerpt or a passage'):
                self.assertIn(phrase, prompt['text'])
        self.assertIn('120,000', openness_reading.READING_PROMPT)
        self.assertIn('latest notes', openness_reading.READING_PROMPT)
