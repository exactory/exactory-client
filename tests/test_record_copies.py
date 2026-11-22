"""Record-copy declarations, validator member tables and whole-account delivery."""
import ast
import copy
from pathlib import Path
import unittest

from ci import capacity_packet_inventory as inventory
from literature_fixtures import LiteratureCase
from problem_accounts_fixtures import build_account_value, build_alternative, build_relation
from research_harness import literature, problem_accounts, problem_claims, problem_evidence
from research_harness.evidence import digest

ROOT = Path(__file__).resolve().parents[1]
ACCOUNTS_MODULE = 'research_harness.problem_accounts'
EVIDENCE_MODULE = 'research_harness.problem_evidence'
PROTOCOL = 'exactory-problem-review-v2'
ACCOUNT_PARTS = {'answer', 'answer_evidence', 'boundaries', 'explanatory_use', 'alternatives', 'strength', 'relations',
                 'refuted_conjectures', 'checks', 'gain', 'search_coverage', 'replacement', 'places'}
COPY_SOURCE = 'def write(packet, value):\n    packet["value"]["accounts"] = value["accounts"]\n'
TABLE_SOURCE = '''from research_harness.operations import fields
NAMES = ("id",)
TABLE = dict(accounts=dict(required=("gain",)), gain=dict(required=NAMES))
'''
TABLE_MODULE = 'research_harness.fixture'
COPY_MODULE = 'builder'


def build_record_copy():
    statement = ast.parse(COPY_SOURCE).body[0].body[0]
    row = dict(member='accounts', definition='value_accounts', caller='builder.write',
               ast_sha256=inventory.compute_ast_digest(statement), table=TABLE_MODULE + '.TABLE',
               member_check='research_harness.operations.fields', paths={'accounts': [], 'gain': ['gain']})
    root = {'class': 'fixed', 'members': {'gain': {'class': 'fixed', 'members': {'id': {'class': 'fixed'}}}}}
    value = {'members': {'accounts': {'$ref': 'value_accounts'}}}
    registry = dict(record_copies=[row], roles={'result': {'members': {'value': value}}},
                    definitions={'value_accounts': root})
    return {COPY_MODULE: COPY_SOURCE, TABLE_MODULE: TABLE_SOURCE}, registry, row, root


class WholeAccountsTests(LiteratureCase):
    def test_whole_accounts_of_a_recorded_value_have_a_declaration_at_every_member(self):
        self.mutate(problem_claims.initialize_problem, dict(slug='finite-bounds', area='math.CO',
                    field='Finite bounds', authors=['fixture']))
        claim = 'For every positive integer, does the construction have a quadratic bound?'
        form = dict(id='form', text=claim, purpose={'kind': 'expected_statement'}, previous=None,
                    grounds=[], correction_checks=[])
        forming = dict(id='forming', forms=[form], recorded_revision=self.store.revision + 1,
                       recorded_at='2025-11-09T00:00:00+00:00', probes=[], question={'places': []},
                       inputs=[], comparison_count=None)
        candidate = dict(id='candidate', statement=claim, source={'evidence': [
            dict(kind='record', record_kind='forming_call', id='forming', digest=digest(forming))]})
        statement = dict(id='statement-1', statement=claim, title='Finite bound', area='math.CO',
                         resolution_criteria='Prove the bound or exhibit a counterexample.',
                         candidate_id='candidate', neighbours=['A weaker bound.'], formal_statement=None)

        def store_history(tx):
            tx.put('forming_call', forming['id'], forming)
            tx.put('problem_candidate', candidate['id'], candidate)
            tx.put('problem_statement', statement['id'], statement)
            tx.put('problem_selection', 'current', {'statement_id': statement['id']})

        self.store.mutate('test.history', {}, store_history, expected_revision=self.store.revision,
                          request_id='history')
        version = self.metadata()
        bundle = self.bundle(version)
        self.mutate(literature.import_bundle, bundle)
        from research_harness.reading import record_reading
        self.mutate(record_reading, self.full_note(bundle, 'reading'))
        reference = dict(kind='reading', version_id=version, reading_id='reading')
        value = build_account_value([reference], neighbours=statement['neighbours'])
        search = self.capture_search()
        search['question'] = 'Which earlier statements and alternatives have the gathered cases?'
        self.mutate(literature.record_search, search)
        accounts = value['accounts']
        accounts.update(
            search_coverage=[dict(id='coverage', purpose='earlier_statements', search_ids=[search['id']],
                                  unreached='Private notes.')],
            gain=dict(id='gain', form='common_structure', claim='A construction handles both families.',
                      relation_ids=['gathered'], establishing_ids=['use'], new_object_justification=None,
                      separate_grounds=[dict(id='grounds', relation_id='gathered', evidence_ids=['use'])],
                      conjunction=dict(status='not_known', description='No converse is known.', evidence_ids=[])))
        accounts['relations'] = [
            build_relation('gathered', standing='established', evidence_ids=['use'],
                           consequence=dict(assertion='A common construction.', instances_or_translation='A finite case.',
                                            credited_to='statement', weaker_derivations=[
                                                dict(id='derivation', alternative_id='weaker', status='not_attempted',
                                                     reason='No attempt.', evidence_ids=[])])),
            build_relation(alternative_id='weaker')]
        accounts['alternatives'] = [build_alternative(search_ids=[search['id']])]
        accounts['answer'] = dict(id='answer', status='conjectured', text='Yes.', evidence_ids=['support'])
        accounts['answer_evidence'] = [dict(id='support', kind=dict(kind='analogy', description='A subclass.'),
            fact='The bound holds for a subclass.', scope='The subclass.', direction='for',
            alternative_id='weaker', evidence=[], evidence_ids=['use'])]
        accounts['boundaries'] = [dict(id='boundary', fact='The relaxation fails.', scope='One instance.',
                                       evidence=[], evidence_ids=['use'], argument=None)]
        accounts['checks'] = [dict(id='observation', target=dict(kind='instance', description='One instance.'),
                                  contradiction='The bound fails.', no_contradiction_reason=None,
                                  result='The bounded case holds.', standing='observation', passed=False,
                                  alternative_id='weaker', date='2026', evidence=[], evidence_ids=['use'],
                                  independence=None)]
        accounts['refuted_conjectures'] = [dict(id='refuted', statement='The unrestricted bound.',
                                               identified_by='author', sources=[], evidence_ids=['use'])]
        accounts['replacement'] = [dict(id='replacement', refuted_id='refuted', form_id=None,
                                        evidence_ids=['use'], account='The question describes the failure.')]

        saved = self.mutate(problem_claims.record_value, value)['result']
        projected = problem_evidence.describe_evidence(self.store.snapshot()['records'], self.artifacts)['value']
        self.assertEqual(projected['accounts'], saved['accounts'])
        self.assertEqual(set(projected['accounts']), ACCOUNT_PARTS)
        self.assertEqual(projected['accounts_version'], 2)
        registry = inventory.read_packet_registry()
        current = registry['roles']['result']['protocols'][PROTOCOL]['members']['problem_evidence']
        declaration = current['members']['value']['members']['accounts']
        local = dict(registry, roles={'accounts': declaration})
        self.assertEqual(inventory.validate_packet('accounts', projected['accounts'], local), [])
        changed = copy.deepcopy(projected['accounts'])
        changed['gain']['conjunction']['extra'] = 'x'
        self.assertEqual(inventory.validate_packet('accounts', changed, local),
                         [inventory.UNDECLARED_MEMBER.format(path='accounts.gain.conjunction.extra')])


class RecordCopyInventoryTests(unittest.TestCase):
    def setUp(self):
        self.sources, self.registry, self.row, self.root = build_record_copy()

    def inspect(self):
        from ci.capacity_packet_inventory import inspect_record_copies
        return inspect_record_copies(self.sources, self.registry)

    def assert_messages(self, template, **fields):
        self.assertEqual(self.inspect(), [template.format(**fields)])

    def test_production_declaration_equals_the_member_table(self):
        from ci.capacity_packet_inventory import inspect_record_copies
        registry = inventory.read_packet_registry()
        row, = registry['record_copies']
        self.assertEqual(row['table'], ACCOUNTS_MODULE + '.ACCOUNT_MEMBERS')
        self.assertEqual(set(row['paths']), set(problem_accounts.ACCOUNT_MEMBERS))
        sources = {name: (ROOT / (name.replace('.', '/') + '.py')).read_text()
                   for name in (ACCOUNTS_MODULE, EVIDENCE_MODULE)}
        self.assertEqual(inspect_record_copies(sources, registry), [])

    def test_every_position_of_the_member_refers_to_its_one_definition(self):
        registry = inventory.read_packet_registry()
        positions = {}
        for role, protocol, schema in inventory.iter_packet_schemas(registry):
            if role not in ('result', 'adjudicator') or protocol != PROTOCOL:
                continue
            members = schema['members']
            history = members['scientific_history']['items']['members']['packet']
            for location, packet in (('current', schema), ('history', history)):
                node = packet['members']['problem_evidence']
                if '$ref' in node:
                    node = registry['definitions'][node['$ref']]
                value = node['members']['value']
                if '$ref' in value:
                    value = registry['definitions'][value['$ref']]
                self.assertEqual(set(value['members']), {
                    'accounts', 'accounts_version', 'changes_if_resolved', 'cite', 'current_state',
                    'derived_accounts', 'digest', 'id', 'motivation'})
                positions[role, location] = value['members']['accounts']
        self.assertEqual(positions, {(role, location): {'$ref': 'value_accounts'}
                                    for role in ('result', 'adjudicator') for location in ('current', 'history')})

    def test_packet_inventory_reports_redeclaration_at_a_value(self):
        members = self.registry['roles']['result']['members']
        members['unrelated'] = {'members': {'accounts': {'class': 'fixed'}}}
        self.assertEqual(inventory.inspect_packet_sources(self.sources, self.registry), [])
        members['value']['members']['accounts'] = {'class': 'fixed'}
        self.assertEqual(inventory.inspect_packet_sources(self.sources, self.registry),
                         [inventory.RECORD_COPY_REDECLARED.format(
                             member='accounts', definition='value_accounts', path='result/:value.accounts')])

    def test_a_new_member_of_an_entry_fails_until_the_definition_declares_it(self):
        self.sources[TABLE_MODULE] = TABLE_SOURCE.replace('required=NAMES', 'required=NAMES + ("weight",)')
        expected = dict(member='accounts', table=self.row['table'], definition='value_accounts', entry='gain', path='gain')
        self.assert_messages(inventory.RECORD_MEMBERS_DIFFER, **expected, undeclared='weight', unknown='none')
        self.root['members']['gain']['members']['weight'] = {'class': 'fixed'}
        self.assertEqual(self.inspect(), [])
        self.sources[TABLE_MODULE] = TABLE_SOURCE
        self.assert_messages(inventory.RECORD_MEMBERS_DIFFER, **expected, undeclared='none', unknown='weight')

    def test_a_new_entry_requires_a_path_and_declaration(self):
        self.sources[TABLE_MODULE] = TABLE_SOURCE.replace('gain=dict(required=NAMES))',
                                                          'gain=dict(required=NAMES), forecast=dict(required=NAMES))')
        self.assert_messages(inventory.RECORD_ENTRY_UNMAPPED, table=self.row['table'], member='accounts',
                             entry='forecast', definition='value_accounts')
        self.row['paths']['forecast'] = ['forecast']
        self.root['members']['forecast'] = copy.deepcopy(self.root['members']['gain'])
        self.assert_messages(inventory.RECORD_MEMBERS_DIFFER, member='accounts', table=self.row['table'],
                             definition='value_accounts', entry='accounts', path='(root)',
                             undeclared='none', unknown='forecast')

    def test_an_object_requires_its_own_table_entry(self):
        self.row['paths'].pop('gain')
        self.sources[TABLE_MODULE] = TABLE_SOURCE.replace(', gain=dict(required=NAMES)', '')
        self.assert_messages(inventory.RECORD_OBJECT_UNMAPPED, definition='value_accounts', member='accounts',
                             path='gain', table=self.row['table'])

    def test_the_table_is_read_in_keyword_form_and_in_literal_form(self):
        from ci.capacity_packet_inventory import _read_member_tables
        keyword = 'NAMES = ("id", "fact")\nTABLE = dict(gain=dict(required=NAMES + ("claim",), optional=("date",), code="x"))\n'
        literal = 'NAMES = ("id", "fact")\nTABLE = {"gain": {"required": NAMES + ("claim",), "optional": ("date",)}}\n'
        expected = {'TABLE': {'gain': frozenset(('id', 'fact', 'claim', 'date'))}}
        self.assertEqual(_read_member_tables(ast.parse(keyword)), expected)
        self.assertEqual(_read_member_tables(ast.parse(literal)), expected)
        self.assertEqual(_read_member_tables(ast.parse('TABLE = dict(gain=dict(required=read_names()))\n')), {})

    def assert_unreadable(self, addition):
        self.sources[TABLE_MODULE] += addition
        self.assert_messages(inventory.RECORD_TABLE_UNREADABLE, table=self.row['table'], member='accounts')

    def test_table_reassignment_is_unreadable(self):
        self.assert_unreadable('TABLE = dict(TABLE, gain=dict(required=("id", "weight")))\n')

    def test_table_item_assignment_is_unreadable_inside_a_function(self):
        self.assert_unreadable('def change():\n    TABLE["gain"] = dict(required=("id", "weight"))\n')

    def test_table_update_is_unreadable(self):
        self.assert_unreadable('TABLE.update(gain=dict(required=("id", "weight")))\n')

    def test_member_checks_read_the_table_even_through_aliases(self):
        for imports, call in (('', 'fields(value, ("id",))'),
                              ('from research_harness.operations import fields as accept\n', 'accept(value, ("id",))'),
                              ('from research_harness import operations\n', 'operations.fields(value, ("id",))')):
            with self.subTest(call=call):
                self.sources[TABLE_MODULE] = TABLE_SOURCE + imports + call + '\n'
                node = ast.parse(self.sources[TABLE_MODULE]).body[-1]
                self.assert_messages(inventory.RECORD_MEMBER_CHECK_UNLISTED, module=TABLE_MODULE, name='TABLE',
                                     line=node.lineno, digest=inventory.compute_ast_digest(node.value)[:12])

    def test_an_extra_call_of_a_listed_member_check_fails(self):
        call = 'fields(value, ("id",))'
        digest = inventory.compute_ast_digest(ast.parse(call, mode='eval').body)
        self.row['other_member_checks'] = [dict(ast_sha256=digest, call=call, count=1, reason='Different member.')]
        self.sources[TABLE_MODULE] += (call + '\n') * 2
        self.assert_messages(inventory.RECORD_MEMBER_CHECK_COUNT, member='accounts', call=call, module=TABLE_MODULE,
                             digest=digest[:12], listed=1, held=2)

    def test_the_copy_statement_is_pinned(self):
        self.sources[COPY_MODULE] = COPY_SOURCE.replace('value["accounts"]', 'dict(value["accounts"])')
        self.assert_messages(inventory.RECORD_COPY_STALE, member='accounts', site='builder.write',
                             digest=self.row['ast_sha256'][:12])

    def test_each_member_has_a_class_and_each_list_a_bound_and_a_ground(self):
        for declaration in ({}, {'class': 'fixed', 'items': {'class': 'fixed'}},
                            {'class': 'fixed', 'items': {'class': 'fixed'}, 'bound': {'characters': 40}},
                            {'class': 'fixed', 'items': {'class': 'fixed'}, 'bound': 'open', 'ground': 'Frame.'}):
            with self.subTest(declaration=declaration):
                self.root['members']['gain']['members']['id'] = declaration
                self.assert_messages(inventory.RECORD_DECLARATION_INCOMPLETE, definition='value_accounts',
                                     member='accounts', path='gain.id')

    def test_a_cyclic_reference_gets_a_declaration_message(self):
        self.root['members']['gain']['members']['id'] = {'$ref': 'loop'}
        self.registry['definitions']['loop'] = {'$ref': 'loop'}
        self.assert_messages(inventory.RECORD_DECLARATION_INCOMPLETE, definition='value_accounts',
                             member='accounts', path='gain.id')

    def test_each_reference_form_has_a_declaration(self):
        registry = inventory.read_packet_registry()
        local = dict(registry, roles=dict(accounts=registry['definitions']['value_accounts']))
        formal = {'claim': {'statement': 'x', 'quantifiers': 'x', 'assumption_ids': ['a'], 'proof_policy': 'certificate',
                            'scope': {'kind': 'integer_interval', 'lower': 1, 'upper': 2, 'lower_inclusive': True, 'upper_inclusive': True}},
                  'required_deliverables': ['x']}
        references = [
            {'kind': 'reading', 'reading_id': 'r', 'version_id': 'v'},
            {'kind': 'computation', 'execution_id': 'e', 'output_id': 'o', 'formal_check': None},
            {'kind': 'computation', 'execution_id': 'e', 'output_id': 'o', 'formal_check': formal},
            {'kind': 'result', 'attempt_id': 'a', 'acceptance_id': 'c'},
            {'kind': 'result', 'native_attempt_digest': 'd', 'acceptance_id': 'c', 'purpose': 'p', 'extension_reason': None},
            {'kind': 'probe', 'id': 'p'},
            {'kind': 'record', 'origin': 'o', 'record_kind': 'reading', 'id': 'r', 'digest': 'd'}]
        sources = [{'kind': 'record', 'record_kind': 'reading', 'id': 'r', 'digest': 'd'},
                   {'path': 'research/sources/objects/' + 'a' * 64, 'sha256': 'a' * 64, 'size': 1, 'media_type': 'text/plain'},
                   {'kind': 'source', 'link': {'version_id': 'v', 'source_id': 's', 'locator': {'kind': 'text', 'start': 0, 'end': 1, 'quote': 'x'}}}]
        for reference in references:
            accounts = {'boundaries': [{'evidence': [reference]}], 'places': [{'evidence': [reference]}]}
            with self.subTest(reference=reference):
                self.assertEqual(inventory.validate_packet('accounts', accounts, local), [])
                changed = {'boundaries': [{'evidence': [dict(reference, never_declared='x')]}]}
                self.assertTrue(inventory.validate_packet('accounts', changed, local))
        for reference in sources:
            accounts = {'refuted_conjectures': [{'sources': [reference]}]}
            with self.subTest(source=reference):
                self.assertEqual(inventory.validate_packet('accounts', accounts, local, request_limit_characters=1000), [])
                changed = {'refuted_conjectures': [{'sources': [dict(reference, never_declared='x')]}]}
                self.assertTrue(inventory.validate_packet('accounts', changed, local, request_limit_characters=1000))


if __name__ == '__main__':
    unittest.main()
