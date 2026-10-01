"""The subscription reviewer uses observed inputs, not a fresh-session assertion."""

import json
import unittest
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
