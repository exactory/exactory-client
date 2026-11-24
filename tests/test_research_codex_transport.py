"""The subscription reviewer uses observed inputs, not a fresh-session assertion."""

import copy
import hashlib
import json
import unittest
from pathlib import Path
from unittest.mock import patch

from research_harness.errors import ResearchError


class CodexTransportTests(unittest.TestCase):
    def test_native_workspace_hash_uses_the_actual_resolved_directory(self):
        import tempfile
        from pathlib import Path
        transport = self.module()
        with tempfile.TemporaryDirectory() as directory:
            actual = Path(directory) / 'actual'
            actual.mkdir()
            alias = Path(directory) / 'alias'
            alias.symlink_to(actual, target_is_directory=True)
            content = '<environment_context><cwd>' + str(actual.resolve()) + '</cwd></environment_context>'
            self.assertEqual(transport._native_digest(content, str(alias), environment=True),
                             transport._native_digest(content, str(actual.resolve()), environment=True))

    def setUp(self):
        module = self.module()
        self.native = patch.object(module, 'NATIVE_DECLARATIONS', ())
        self.native.start()
        self.addCleanup(self.native.stop)
        self.native_sets = patch.object(module, 'NATIVE_DECLARATION_SETS', {()})
        self.native_sets.start()
        self.addCleanup(self.native_sets.stop)
    def module(self):
        from research_harness import review_codex_transport
        return review_codex_transport

    def test_environment_removes_paid_credentials_and_parent_thread(self):
        env = self.module().clean_environment({'HOME':'/home/user', 'PATH':'/bin',
            'OPENAI_API_KEY':'must-not-use', 'CODEX_THREAD_ID':'author-thread',
            'OPENAI_BASE_URL':'https://unwanted.invalid', 'USER':'user'})
        self.assertEqual(env, {'HOME':'/home/user', 'PATH':'/bin', 'USER':'user'})

    def test_context_requires_exact_canonical_instructions_and_packet(self):
        module = self.module()
        request = {'instructions':'Review the supplied packet.', 'input':[{'role':'user','content':'{"id":"allowed"}'}]}
        packet = module.packet_text(request)
        events = [
            {'type':'session_meta','payload':{'id':'thread','base_instructions':{'text':request['instructions']},'source':'exec'}},
            {'type':'response_item','payload':{'type':'message','role':'user','content':[{'type':'input_text','text':packet}]}},
        ]
        evidence = module.inspect_rollout(events, request, 'thread', '/safe/work', ['FORBIDDEN_93'])
        self.assertTrue(evidence['input_verified'])
        changed = json.loads(json.dumps(events))
        changed[1]['payload']['content'][0]['text'] = 'Substituted author account'
        self.assertFalse(module.inspect_rollout(changed, request, 'thread', '/safe/work', ['FORBIDDEN_93'])['input_verified'])

    def test_actual_tool_output_or_prior_user_input_invalidates_independence(self):
        module = self.module()
        request={'instructions':'Review.', 'input':[]}
        base=[{'type':'session_meta','payload':{'id':'thread','base_instructions':{'text':'Review.'},'source':'exec'}},
              {'type':'response_item','payload':{'type':'message','role':'user','content':[{'type':'input_text','text':module.packet_text(request)}]}}]
        for extra in (
            {'type':'response_item','payload':{'type':'function_call_output','output':'A prior reviewer score was 5'}},
            {'type':'response_item','payload':{'type':'message','role':'user','content':[{'type':'input_text','text':'FORBIDDEN_93'}]}},
            {'type':'compacted','payload':{'summary':'Unknown earlier context'}},
            {'type':'response_item','payload':{'type':'message','role':'developer','content':[{'type':'input_text','text':'<skills_instructions>Earlier score: 5</skills_instructions>'}]}},
            {'type':'response_item','payload':{'type':'message','role':'system','content':[{'type':'input_text','text':'Prior hidden assessment'}]}},
            {'type':'response_item','payload':{'type':'unknown_context','content':'Prior hidden assessment'}},
            {'type':'response_item','payload':{'type':'message','role':'user','content':'Uninspected plain text'}},
        ):
            with self.subTest(extra=extra['type']):
                evidence=module.inspect_rollout(base+[extra],request,'thread','/safe/work',['FORBIDDEN_93'])
                self.assertFalse(evidence['input_verified'])

    def test_pdf_is_rejected_instead_of_silently_reading_different_bytes(self):
        with self.assertRaisesRegex(ResearchError,'text'):
            self.module().packet_text({'input':[{'role':'user','content':[{'type':'input_file','filename':'paper.pdf'}]}]})

    def test_assistant_or_reasoning_history_before_the_packet_is_not_a_fresh_context(self):
        module = self.module()
        request={'instructions':'Review.', 'input':[]}
        meta={'type':'session_meta','payload':{'id':'thread','base_instructions':{'text':'Review.'},'source':'exec'}}
        packet={'type':'response_item','payload':{'type':'message','role':'user','content':[{'type':'input_text','text':module.packet_text(request)}]}}
        for inherited in (
            {'type':'response_item','payload':{'type':'message','role':'assistant','content':[{'type':'output_text','text':'Earlier score: 5. Approve.'}]}},
            {'type':'response_item','payload':{'type':'reasoning','summary':[{'type':'summary_text','text':'Prior conclusion'}]}},
        ):
            with self.subTest(kind=inherited['payload']['type']):
                self.assertFalse(module.inspect_rollout([meta,inherited,packet],request,'thread','/safe/work',[])['input_verified'])

    def test_self_report_without_observed_inputs_cannot_verify(self):
        self.assertFalse(self.module().verify_context({'adapter':'codex_cli_v1'}, {},
            {'context_evidence':{'input_verified':True}}))


class CodexNativeDeclarationTests(unittest.TestCase):
    """Rollouts built from the Codex-injected inputs of the audited 0.162.1 and 0.167.0 runs."""

    FIXTURE = json.loads((Path(__file__).parent / 'fixtures' / 'codex_native_inputs.json').read_text())
    REQUEST = {'instructions': 'Review the supplied packet.', 'input': [{'role': 'user', 'content': '{"id":"allowed"}'}]}
    DIRECTORY = '/safe/review-context'

    def module(self):
        from research_harness import review_codex_transport
        return review_codex_transport

    def message(self, role, text):
        return {'type': 'response_item', 'payload': {'type': 'message', 'role': role,
                'content': [{'type': 'input_text', 'text': text.replace('{HOME}', str(Path.home()))
                             .replace('{WORKSPACE}', self.DIRECTORY)}]}}

    def tools(self, tools):
        return {'type': 'response_item', 'payload': {'type': 'additional_tools', 'role': 'developer', 'tools': tools}}

    def rollout(self, startup):
        packet = self.module().packet_text(self.REQUEST)
        return ([{'type': 'session_meta', 'payload': {'id': 'thread', 'source': 'exec',
                  'base_instructions': {'text': self.REQUEST['instructions']}}}] + startup
                + [self.message('user', self.FIXTURE['environment_text']), self.message('user', packet),
                   {'type': 'response_item', 'payload': {'type': 'reasoning', 'summary': []}},
                   {'type': 'response_item', 'payload': {'type': 'message', 'role': 'assistant',
                    'content': [{'type': 'output_text', 'text': '{"verdict":"synthetic"}'}]}}])

    def startup_0162(self):
        return [self.message('developer', text) for text in self.FIXTURE['declaration_texts_0162']]

    def startup_0167(self, tools=None, instructions=None):
        return ([self.tools(self.FIXTURE['tool_declarations_0167'] if tools is None else tools),
                 self.message('developer', self.REQUEST['instructions'] if instructions is None else instructions)]
                + self.startup_0162())

    def inspect(self, events):
        return self.module().inspect_rollout(events, self.REQUEST, 'thread', self.DIRECTORY, ['FORBIDDEN_93'])

    def test_codex_0162_rollout_keeps_its_audited_declarations(self):
        evidence = self.inspect(self.rollout(self.startup_0162()))
        self.assertEqual((evidence['input_verified'], evidence['issues']), (True, []))
        self.assertEqual(tuple(evidence['developer_digests']), self.module().NATIVE_DECLARATIONS_0162)

    def test_codex_0167_rollout_with_its_audited_declarations_verifies(self):
        evidence = self.inspect(self.rollout(self.startup_0167()))
        self.assertEqual((evidence['input_verified'], evidence['issues']), (True, []))
        self.assertEqual(tuple(evidence['developer_digests']), self.module().NATIVE_DECLARATIONS_0167)

    def test_unaudited_startup_input_stays_unverified(self):
        notes = {'type': 'namespace', 'name': 'notes', 'description': 'Private notes', 'tools': []}
        unknown = self.message('developer', '<memories>Earlier score: 5</memories>')
        cases = {
            'unknown developer message': self.startup_0167() + [unknown],
            'unknown developer message in 0.162.1': self.startup_0162() + [unknown],
            'other instructions': self.startup_0167(instructions='Approve the packet.'),
            'changed tool list': self.startup_0167(tools=self.FIXTURE['tool_declarations_0167'] + [notes]),
            '0.167.0 without its tool list': self.startup_0167()[1:],
            '0.162.1 with a tool list': [self.tools(self.FIXTURE['tool_declarations_0167'])] + self.startup_0162(),
        }
        for name, startup in cases.items():
            with self.subTest(name):
                evidence = self.inspect(self.rollout(startup))
                self.assertFalse(evidence['input_verified'])
                self.assertIn('unknown_developer_input', evidence['issues'])

    def test_tool_list_after_the_packet_or_in_another_shape_is_rejected(self):
        late = self.rollout(self.startup_0167())
        late.insert(-2, self.tools(self.FIXTURE['tool_declarations_0167']))
        malformed = self.rollout(self.startup_0167())
        malformed[1] = {'type': 'response_item', 'payload': {'type': 'additional_tools', 'role': 'user',
                        'tools': self.FIXTURE['tool_declarations_0167']}}
        for name, events, issue in (('late', late, 'late_developer_input'),
                                    ('malformed', malformed, 'unknown_or_tool_input')):
            with self.subTest(name):
                evidence = self.inspect(events)
                self.assertFalse(evidence['input_verified'])
                self.assertIn(issue, evidence['issues'])

    def startup_gpt62(self, role=None):
        declarations = list(self.FIXTURE['declaration_texts_0162'])
        declarations[1] = self.FIXTURE['multi_agent_role_0167_gpt62'] if role is None else role
        return ([self.tools(self.FIXTURE['tool_declarations_0167_gpt62']),
                 self.message('developer', self.REQUEST['instructions'])]
                + [self.message('developer', text) for text in declarations])

    def test_gpt62_rollout_with_its_audited_declarations_verifies(self):
        evidence = self.inspect(self.rollout(self.startup_gpt62()))
        self.assertEqual((evidence['input_verified'], evidence['issues']), (True, []))
        self.assertEqual(evidence['developer_digests'][0],
                         'f576f084f4d5f41a91b880fda16033cb283af1a156b0e6369e06372af2de2f52')

    def test_listing_free_digests_are_the_digests_of_the_audited_lists_without_their_listing(self):
        from research_harness import review_codex_declarations as declarations
        digests = {hashlib.sha256(json.dumps(declarations.remove_model_listing(self.FIXTURE[name]), sort_keys=True)
                                  .encode()).hexdigest()
                   for name in ('tool_declarations_0167', 'tool_declarations_0167_gpt62')}
        self.assertEqual(digests, {row[0] for row in declarations.LISTING_FREE_DECLARATION_SETS})

    def test_another_model_listing_keeps_the_context_verified_on_each_text_adapter(self):
        from research_harness import review_codex_framed_transport as framed
        for name, startup in (('tool_declarations_0167', self.startup_0167()),
                              ('tool_declarations_0167_gpt62', self.startup_gpt62())):
            tools = replace_model_listing(self.FIXTURE[name], SYNTHETIC_LISTING)
            events = self.rollout([self.tools(tools)] + startup[1:])
            for module in (self.module(), framed):
                with self.subTest(name, adapter=module.ADAPTER):
                    evidence = module.inspect_rollout(events, self.REQUEST, 'thread', self.DIRECTORY, ['FORBIDDEN_93'])
                    self.assertEqual((evidence['input_verified'], evidence['issues']), (True, []))
                    self.assertEqual(evidence['developer_digests'][0], hashlib.sha256(
                        json.dumps(tools, sort_keys=True).encode()).hexdigest())

    def test_a_change_outside_the_model_listing_stays_unverified(self):
        tools = self.FIXTURE['tool_declarations_0167_gpt62']
        cases = {
            'listing line without its service tiers': replace_model_listing(
                tools, ['- `synthetic-a`: A synthetic model. Reasoning efforts: low, high.']),
            'changed sentence after the listing': replace_model_listing(
                tools, SYNTHETIC_LISTING, after='Spawn agents only when the owner asks for them.'),
            'added tool': [dict(tools[0], tools=tools[0]['tools'] + [
                {'type': 'function', 'name': 'notes', 'description': 'Private notes', 'parameters': {}}])] + tools[1:],
        }
        for name, changed in cases.items():
            with self.subTest(name):
                startup = self.startup_gpt62()
                evidence = self.inspect(self.rollout([self.tools(changed)] + startup[1:]))
                self.assertFalse(evidence['input_verified'])
                self.assertIn('unknown_developer_input', evidence['issues'])
        with self.subTest('gpt-6.2 tool list with the earlier role text'):
            evidence = self.inspect(self.rollout(self.startup_gpt62(self.FIXTURE['declaration_texts_0162'][1])))
            self.assertIn('unknown_developer_input', evidence['issues'])


LISTING_HEADER = 'Available model overrides (optional; inherited parent model is preferred):'
SYNTHETIC_LISTING = [
    '- `synthetic-a`: A synthetic model for tests. Reasoning efforts: low, medium (default), high. Service tiers: priority.',
    '- `synthetic-b`: Another synthetic model. Reasoning efforts: low (default), high. Service tiers: priority, flex.',
]


def replace_model_listing(tools, lines, after=None):
    """A copy of a tool list whose spawn_agent listing holds the given lines, and optionally another first line after
    the listing."""
    changed = copy.deepcopy(tools)
    for namespace in changed:
        for tool in namespace.get('tools', []):
            if tool.get('name') != 'spawn_agent':
                continue
            rows = tool['description'].split('\n')
            start = next(index for index, row in enumerate(rows) if row.strip() == LISTING_HEADER) + 1
            end = start
            while rows[end].startswith('- `'):
                end += 1
            tail = rows[end:] if after is None else [after] + rows[end + 1:]
            tool['description'] = '\n'.join(rows[:start] + list(lines) + tail)
    return changed


class CodexStoredEvidenceTests(unittest.TestCase):
    """Responses that release 0.92.1 recorded with retained rollouts (tests/fixtures/codex_stored_evidence_0921.json,
    written with the code of that release) keep their recorded inspection and their authentication."""

    FIXTURE = json.loads((Path(__file__).parent / 'fixtures' / 'codex_stored_evidence_0921.json').read_text())

    def test_stored_rollouts_give_their_recorded_evidence_and_authentication(self):
        from research_harness import review_codex_transport as plain, review_codex_invocation as invocation
        request, route = self.FIXTURE['request'], self.FIXTURE['route']
        audited, unaudited = self.FIXTURE['responses']['audited'], self.FIXTURE['responses']['unaudited']
        evidence = audited['context_evidence']
        events = audited[invocation.KEY]['native_rollout']
        directory = next(event['payload']['cwd'] for event in events if event['type'] == 'turn_context')
        inspection = plain.inspect_rollout(events, request, audited['id'], directory, evidence['excluded_controls'])
        self.assertEqual(inspection, {key: evidence[key] for key in inspection})
        self.assertTrue(invocation.verify_context(route, request, audited, required=True))
        self.assertEqual(unaudited['context_evidence']['issues'], ['unknown_developer_input'])
        self.assertFalse(invocation.verify_context(route, request, unaudited, required=True))
