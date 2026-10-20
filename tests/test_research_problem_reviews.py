"""Problem bars withhold the claim; observed results cover its exact evidence."""

import copy
import json
import sys
import types
from unittest.mock import patch

from literature_fixtures import LiteratureCase
from research_harness import review_protocol as protocol
from research_harness import problem_rubric, research_decisions as decisions
from research_harness.errors import ResearchError
from research_harness.evidence import digest
from research_harness.problem_rubric import ITEMS, rubric_objection_id
from test_research_problem_claims import ProblemCase
from test_research_problem_rubric import install_rubric_anchors, passing_grades


CHECKS = ("problem_statement", "problem_openness", "problem_value", "problem_citations", "stop")


class ProblemReviewTests(LiteratureCase):
    def setUp(self):
        super().setUp()
        self.put("workspace", "problem", {"version": 1, "orchestration": "problem_creation",
            "slug": "bounded-sequences", "area": "math.CO", "field": "Extremal bounded sequences",
            "authors": ["author", "coauthor"]})
        self.statement = {"id": "statement-1", "title": "PRIVATE_TITLE", "area": "math.CO",
            "statement": "PRIVATE_STATEMENT", "resolution_criteria": "PRIVATE_CRITERIA",
            "neighbours": ["PRIVATE_NEIGHBOUR"], "formal_statement": None}
        self.put("problem_statement", self.statement["id"], self.statement)
        self.put("problem_selection", "current", {"statement_id": self.statement["id"]})
        self.expected = {"claim": {"statement_id": self.statement["id"],
            "statement_digest": digest({key: self.statement[key] for key in ("statement", "resolution_criteria")}),
            **{key: self.statement[key] for key in ("title", "area", "statement", "resolution_criteria", "formal_statement")}},
            "openness": {"id": "open-1", "outcome": "supported_open", "minimum": {}}, "openness_searches": [],
            "value": {"id": "value-1", "motivation": [{"text": "PRIVATE_VALUE", "evidence": []}], "current_state": []},
            "assessment": None, "assessment_evidence": [], "attempt": None, "results": [], "citations": [], "readings": [],
            "passages": [], "computations": [], "formal_checks": []}
        # Isolate the review contract from the independently implemented claim validator.
        claims = types.ModuleType("research_harness.problem_claims")
        claims.find_current_statement = lambda records: records.get("problem_statement", {}).get(
            records.get("problem_selection", {}).get("current", {}).get("statement_id"))
        claims.describe_problem_evidence = lambda records, artifacts: copy.deepcopy(self.expected)
        claims.compute_problem_evidence_digest = lambda records, artifacts: digest(self.expected)
        replacement = patch.dict(sys.modules, {"research_harness.problem_claims": claims})
        replacement.start()
        self.addCleanup(replacement.stop)
        install_rubric_anchors(self)

    def put(self, kind, identifier, value):
        self.sequence += 1
        self.store.mutate("fixture.seed", {"kind": kind, "id": identifier},
            lambda tx: tx.put(kind, identifier, value), expected_revision=self.store.revision,
            request_id="problem-seed-" + str(self.sequence))

    def read_records(self):
        return self.store.snapshot()["records"]

    def build_provider_response(self, output):
        return {"id": "provider-response", "model": "fixture-model", "status": "completed",
            "output": [{"type": "message", "role": "assistant", "content": [
                {"type": "output_text", "text": json.dumps(output)}]}],
            "usage": {"input_tokens": 37, "output_tokens": 12}}

    def prepare_review_route(self):
        self.mutate(protocol.record_route, {"id": "route", "adapter": "openai_responses_v1",
            "model": "fixture-model", "endpoint": "https://api.openai.com/v1/responses",
            "configuration": {"max_output_tokens": 1000, "timeout_seconds": 5}})
        def respond_to_probe(route, request, credential):
            packet = json.loads(request["input"][0]["content"])
            return self.build_provider_response({"allowed_control": packet["allowed_control"],
                "evidence_control": packet["evidence_control"], "excluded_controls": []})
        with patch("research_harness.review_transport.send_request", side_effect=respond_to_probe):
            self.mutate(protocol.probe_route, {"id": "probe", "route_id": "route"}, credential="test-only")

    def assign(self, role, reviewer, author="author", identifier=None, **context):
        return self.mutate(protocol.record_assignment, {"id": identifier or role + "-" + reviewer,
            "route_id": "route", "dossier_id": None, "role": role, "reviewer_id": reviewer,
            "author_id": author, "context": context})["result"]

    def build_review_output(self, stage, checks=CHECKS):
        output = {"stage": stage, "support": None, "value": {"status": "sufficient",
            "consequence": "A new connection between extremal bounds.",
            "bar_ids": [] if stage == "bar" else ["review-bar-first", "review-bar-second"],
            "objective_adequacy": "not_assessed" if stage == "bar" else "adequate",
            "method_adequacy": "not_assessed" if stage == "bar" else "adequate",
            "reason": "The supplied sources support this consequence.", "evidence": [self.supporting],
            "work_items": [], "assurances": [] if stage == "bar" else [
                {"kind": kind, "status": "passed", "reason": "Checked the exact supplied evidence.",
                 "evidence": [self.supporting]} for kind in checks], "objection_findings": []},
            "objections": [], "limitations": ["Assessment is limited to the delivered evidence."]}
        if stage == "result":
            output["value"].update(rubric=passing_grades(), rubric_evidence={
                item: "The delivered evidence supports this grade within its recorded scope." for item in ITEMS})
        return output

    def observe(self, stage, reviewer, output=None, record=True, identifier=None):
        assignment = self.assign(stage, reviewer, identifier=identifier)
        output = output or self.build_review_output(stage)
        with patch("research_harness.review_transport.send_request", return_value=self.build_provider_response(output)):
            self.mutate(protocol.invoke_assignment, {"id": "attempt-" + assignment["id"],
                "assignment_id": assignment["id"]}, credential="test-only")
        payload = dict(output, id="review-" + assignment["id"], dossier_id=None, assignment_id=assignment["id"])
        if record:
            return self.mutate(decisions.record_value_review, payload)["result"]
        return payload

    def observe_bar_reviews(self):
        self.supporting = self.artifacts.put(b"A captured field standard.", "text/plain")
        self.prepare_review_route()
        for reviewer in ("first", "second"):
            self.observe("bar", reviewer)

    def observe_result_reviews(self):
        self.observe_bar_reviews()
        for reviewer in ("first", "second"):
            self.observe("result", reviewer)

    def add_reading_and_computation(self):
        from research_harness import literature, reading
        from research_harness.direction_sources import build_reading_passages
        version = self.metadata(76001)
        bundle = self.bundle(version)
        self.mutate(literature.import_bundle, bundle)
        self.mutate(reading.record_reading, self.full_note(bundle, note_id="decisive-reading"))
        saved = self.read_records()["reading"]["decisive-reading"]
        passages = []
        for passage, data in build_reading_passages(self.read_records(), self.artifacts, [saved]):
            self.assertEqual(self.artifacts.put(data, passage["excerpt"]["media_type"]), passage["excerpt"])
            passages.append(passage)
        self.expected["readings"] = [{"id": saved["id"], "version_id": version,
            "digest": digest(saved), "depth": "fulltext", "reading": saved}]
        self.expected["passages"] = passages
        output = self.artifacts.put(b"{\"verified_inputs\":[1,2,3]}", "application/json")
        validation = self.artifacts.put(b"Validation checked all three inputs.", "text/plain")
        self.expected["computations"] = [{"execution_id": "execution-1", "admission": {"id": "admission-1"},
            "execution": {"id": "execution-1", "outputs": [{"id": "output-1", "artifact": output}]},
            "assessment": {"id": "assessment-1", "validations": [validation]}}]
        self.expected["formal_checks"] = [{"id": "formal-1", "native_closure": {
            "contract": {"original_claim": {"statement": "The requested finite bound", "proof_policy": "lean-kernel"}},
            "closure": {"subject": {"outcome": "proof"}, "review": {"decision": "approve"},
                "audit": {"subject_digest": "checked-subject"}}}}]
        return output, validation

    def test_problem_templates_are_version_two_and_other_prompts_unchanged(self):
        hashes = {"bar": "88b53e388fa267f8aa5f72da297b28ebdab5ac6e020f81b4be6c2b756d30f3ca",
            "slate": "38fed1d12ce4376f4144d3dd6c57e2e9475db73a30b7a86c8471f00c84588312",
            "result": "236703cb1f256d93b60cfa4c82ea7e2eb76fcd7a66c9984542767c675e4e32bc",
            "native_math": "911fd529e31ca9bf8efba3c743ecdd4d9c7e4cfe11ed7d97ed830e7002c81872"}
        for role, expected in hashes.items():
            self.assertEqual(protocol.canonical_prompt(role)["template_hash"], expected)
        for role in ("bar", "result", "adjudicator"):
            prompt = protocol.canonical_prompt(role, orchestration="problem_creation")
            self.assertEqual(prompt["template_id"], "exactory-problem-review-v2/" + role)
        self.assertIn("nearest known problems", protocol.canonical_prompt("bar", orchestration="problem_creation")["text"])
        result = protocol.canonical_prompt("result", orchestration="problem_creation")["text"]
        for kind in CHECKS + ITEMS + ('value.rubric', 'value.rubric_evidence', 'max(B1, B2) at least 2',
                                     'E-A', 'E-N', 'at least 3', 'priority at least 2', 'pass'):
            self.assertIn(kind, result)
        bar = protocol.canonical_prompt("bar", orchestration="problem_creation")["text"]
        for content in ('levels 2 and 3', 'B1', 'B2', 'priority', 'what a resolution of each would give', 'uninteresting'):
            self.assertIn(content, bar)
        for content in ('are not reasons against a question by themselves',
                        'a question without footholds is admissible when its significance case reaches level 3',
                        'the explanation the resolution would give is itself the significance', 'Level 2, supported:'):
            self.assertIn(content, bar)
        # A refreshed bar (the packet carries problem_refresh) gives a finding on every objection its earlier bar stated.
        for content in ('objection_findings is empty for a first bar', 'When the packet carries problem_refresh',
                        'for every objection that earlier bar stated'):
            self.assertIn(content, bar)
        version_one_bar = protocol.canonical_prompt("bar", orchestration="problem_creation", version=1)["text"]
        self.assertNotIn('not reasons against a question by themselves', version_one_bar)
        self.assertNotIn('problem_refresh', version_one_bar)
        # The evidence grammar of every version 2 role permits only records delivered as content (R45, R46).
        for role in ("bar", "slate", "result", "adjudicator"):
            text_of_role = protocol.canonical_prompt(role, orchestration="problem_creation")["text"]
            for content in ("a record whose content the packet delivered to you",
                            "a record delivered under a material objection's version_evidence",
                            "is cited by the link of that passage", "templates (artifact_reference_template, artifact_descriptor)",
                            "reached you only as a template or inside a prior finding is not delivered content",
                            "review_evidence_undelivered"):
                self.assertIn(content, text_of_role)
            self.assertNotIn("a record named in the packet", text_of_role)
        # The slate and result reviewers are told that each material objection arrives with the content it cites.
        for role in ("slate", "result"):
            self.assertIn("under version_evidence and version_source_mappings, which you may cite",
                          protocol.canonical_prompt(role, orchestration="problem_creation")["text"])

    def test_problem_slate_template_is_version_two_only_and_keeps_bar_and_result_bytes(self):
        version_two = {"bar": "eec00e55d2ed36ab837f8cff7cc265c1685971a67c4847b08fc35714bcf96d03",
                       "result": "ca53255ec1becb85708b093056af99c9dbda87ede405d8ad3c1335b4455f6147",
                       "adjudicator": "58905d67ce7e5d37fe6092c778cc11e78967b3af30b7ea41a97205a32fa531c1"}
        for role, expected in version_two.items():
            self.assertEqual(protocol.canonical_prompt(role, orchestration="problem_creation")["template_hash"], expected)
        slate = protocol.canonical_prompt("slate", orchestration="problem_creation")
        self.assertEqual(slate["template_id"], "exactory-problem-review-v2/slate")
        for content in ("value.rubric_potential", "not_assessed", "B1", "B2", "priority", "level 1",
                        "neither openness nor significance", "Continue your own bar assessment"):
            self.assertIn(content, slate["text"])
        # The version keys are promised for a material objection only; a rubric objection's packet carries none.
        adjudicator = protocol.canonical_prompt("adjudicator", orchestration="problem_creation")["text"]
        for content in ("For a material objection, the packet delivers", "one entry for every reference the version cites",
                        "a cited record as the projection its stating reviewer received", "descriptors of its artifacts under artifacts",
                        "an undelivered entry, which means the packet is incomplete",
                        "except an artifact delivered only as passages", "A rubric objection carries none of those three keys"):
            self.assertIn(content, adjudicator)
        with self.assertRaises(ResearchError) as caught:
            protocol.canonical_prompt("slate", orchestration="problem_creation", version=1)
        self.assertEqual(caught.exception.code, "invalid_review_role")
        assignment = {"role": "slate", "subject": {"kind": "problem", "statement_id": None}, "prompt": slate}
        self.assertTrue(protocol._prompt_matches(assignment))
        self.assertFalse(protocol.is_previous_prompt(assignment))

    def test_version_one_problem_assignment_stays_verified_and_gate_requires_version_two_result(self):
        original = protocol.canonical_prompt
        hashes = {'bar': '818b715d1acdfcd10db45e60d70ce6ef5f864dcdcbd82be9312125cf0fe42fca',
                  'result': '31a44bac73484c1aaf62a02199623a147e71a57902ee6d50d61973166a28364b',
                  'adjudicator': 'b355755fc920543576e40a154051c5405f5d7d53f1f92831c934dec316585e37'}
        for role, expected in hashes.items():
            self.assertEqual(original(role, orchestration='problem_creation', version=1)['template_hash'], expected)
        def previous_prompt(role, **options):
            if options.get('orchestration') == 'problem_creation':
                options['version'] = 1
            return original(role, **options)
        # A 0.53.0 store recorded these results before version 1 bars were refused at result assignment.
        with patch.object(protocol, 'canonical_prompt', side_effect=previous_prompt), \
                patch.object(protocol, 'require_calibrated_bars', return_value=None):
            self.observe_bar_reviews()
            for reviewer in ('first', 'second'):
                output = self.build_review_output('result')
                del output['value']['rubric']
                del output['value']['rubric_evidence']
                self.observe('result', reviewer, output)
        records = self.read_records()
        for identifier, assignment in records['review_assignment'].items():
            self.assertTrue(protocol.is_previous_prompt(assignment))
            state = protocol.assignment_state(records, self.artifacts, identifier)
            self.assertTrue(state['ready'], state['obligations'])
        assessment = decisions.assess_problem_reviews(records, self.artifacts)
        self.assertEqual({(row['code'], row['review_id']) for row in assessment['obligations']}, {
            ('problem_rubric_review_required', 'review-result-first'),
            ('problem_rubric_review_required', 'review-result-second')})
        for role in hashes:
            assignment = {'role': role, 'subject': {'kind': 'problem'},
                          'prompt': original(role, orchestration='problem_creation', version=1)}
            self.assertTrue(protocol._prompt_matches(assignment))
            assignment['prompt']['template_hash'] = '0' * 64
            self.assertFalse(protocol._prompt_matches(assignment))

    def test_bar_packet_still_withholds_statement_title_neighbours_value_and_criteria(self):
        packet = protocol.build_packet(self.read_records(), self.artifacts, None, "bar", "first")
        self.assertEqual((packet["area"], packet["field"]), ("math.CO", "Extremal bounded sequences"))
        self.assertEqual(packet["subject"], {"kind": "problem", "statement_id": "statement-1"})
        for forbidden in ("PRIVATE_STATEMENT", "PRIVATE_TITLE", "PRIVATE_NEIGHBOUR", "PRIVATE_VALUE", "PRIVATE_CRITERIA"):
            self.assertNotIn(forbidden, json.dumps(packet))
        self.assertIn("source_inventory", packet)
        self.assertIn("source_deliveries", packet)

    def test_result_review_without_rubric_is_refused(self):
        self.observe_bar_reviews()
        output = self.build_review_output('result')
        del output['value']['rubric']
        del output['value']['rubric_evidence']
        payload = self.observe('result', 'first', output, record=False)
        self.assert_error('invalid_value_review', lambda: self.mutate(decisions.record_value_review, payload))

    def test_result_review_rubric_evidence_must_cover_every_item(self):
        self.observe_bar_reviews()
        output = self.build_review_output('result')
        payload = self.observe('result', 'first', output, record=False)
        # Exercise the validator directly for each malformed response after one
        # observed assignment supplies the real bar records and held evidence.
        for item in ITEMS:
            for bad in ('missing', '', [], None):
                findings = copy.deepcopy(output['value'])
                if bad == 'missing':
                    del findings['rubric_evidence'][item]
                else:
                    findings['rubric_evidence'][item] = bad
                with self.subTest(item=item, bad=bad):
                    self.assert_error('invalid_value_review', lambda: decisions._validate_value(
                        self.read_records(), self.artifacts, findings, 'result', None,
                        expected_checks=CHECKS, problem=self.read_records()['workspace']['problem']))
        del payload['value']['rubric_evidence']['A1']
        # Observe the malformed output itself so output equality cannot mask the
        # schema refusal in the public mutation.
        output['value'] = payload['value']
        second = self.observe('result', 'second', output, record=False)
        self.assert_error('invalid_value_review', lambda: self.mutate(decisions.record_value_review, second))

    def test_rubric_review_templates_follow_the_current_problem_protocol(self):
        self.observe_result_reviews()
        records = self.read_records()
        review = records['value_review']['review-result-first']
        assignment = records['review_assignment'][review['assignment_id']]
        with patch.object(protocol, 'PROBLEM_PROTOCOL', 'exactory-problem-review-next'):
            for suffix in ('/result', '/result/context-repair'):
                with self.subTest(suffix=suffix):
                    assignment['prompt']['template_id'] = protocol.PROBLEM_PROTOCOL + suffix
                    self.assertEqual(decisions.assess_rubric_reviews(
                        records, self.artifacts, [review], digest(self.expected)), [])

    def prepare_below_threshold_reviews(self):
        self.observe_bar_reviews()
        output = self.build_review_output('result')
        output['value']['rubric']['priority'] = 1
        self.observe('result', 'first', output)
        self.observe('result', 'second')

    def observe_rubric_adjudication(self, disposition='not_upheld'):
        identifier = rubric_objection_id('review-result-first', 'priority')
        assignment = self.assign('adjudicator', 'focused-third', identifier='rubric-focused',
            review_ids=['review-result-first', 'review-result-second'], objection_id=identifier)
        output = {'objection_id': identifier, 'disposition': disposition,
                  'reason': 'The delivered mathematical distinction supports the comparative priority.',
                  'evidence': [self.supporting], 'correction_admissible': None}
        with patch('research_harness.review_transport.send_request', return_value=self.build_provider_response(output)):
            self.mutate(protocol.invoke_assignment, {'id': 'rubric-attempt', 'assignment_id': assignment['id']}, credential='test-only')
        saved = self.mutate(protocol.record_adjudication, dict(output, id='rubric-finding', assignment_id=assignment['id']))['result']
        return saved, json.loads(self.artifacts.read(assignment['packet']))

    def test_below_threshold_grade_blocks_until_adjudicated(self):
        self.prepare_below_threshold_reviews()
        pending = decisions.assess_problem_reviews(self.read_records(), self.artifacts)
        below = [row for row in pending['obligations'] if row['code'] == 'problem_rubric_below_threshold']
        self.assertEqual(len(below), 1)
        self.assertEqual((below[0]['source'], below[0]['review_id']), ('review', 'review-result-first'))
        self.assertEqual(below[0]['shortfalls'], [{'item': 'priority', 'required': '>= 2', 'graded': 1}])
        self.observe_rubric_adjudication()
        state = decisions.assess_problem_reviews(self.read_records(), self.artifacts)
        self.assertTrue(state['ready'], state['obligations'])

    def test_rubric_adjudication_under_old_evidence_does_not_resolve(self):
        self.prepare_below_threshold_reviews()
        self.observe_rubric_adjudication()
        self.expected['value']['motivation'][0]['text'] = 'Changed mathematical payoff.'
        state = decisions.assess_problem_reviews(self.read_records(), self.artifacts)
        self.assertIn('problem_rubric_below_threshold', {row['code'] for row in state['obligations']})

    def test_adjudicator_packet_carries_the_rubric_grades_for_a_rubric_objection(self):
        self.prepare_below_threshold_reviews()
        saved, packet = self.observe_rubric_adjudication()
        self.assertEqual(packet['objection']['id'], 'rubric:review-result-first:priority')
        self.assertEqual(packet['objection']['claim'], 'priority graded 1 by first; required >= 2')
        self.assertEqual(packet['rubric_grades'], [self.read_records()['value_review'][identifier]['payload']['value']['rubric']
            for identifier in ('review-result-first', 'review-result-second')])
        self.assertEqual(saved['problem_evidence_digest'], digest(self.expected))

    def test_adjudicator_packet_carries_both_bar_calibrations_and_the_rubric_definition(self):
        self.prepare_below_threshold_reviews()
        _, packet = self.observe_rubric_adjudication()
        records = self.read_records()
        self.assertEqual(packet['bar_calibrations'], [records['value_review'][identifier]['payload']
                                                     for identifier in ('review-bar-first', 'review-bar-second')])
        anchor = problem_rubric.ANCHORS['priority']
        self.assertEqual(packet['rubric_definition'], {'item': 'priority', 'definition': anchor['definition'],
                                                       'threshold': anchor['threshold']})

    def test_rubric_adjudication_without_the_bar_calibrations_does_not_clear_the_shortfall(self):
        self.assert_incomplete_rubric_adjudication_keeps_the_shortfall('bar_calibrations')

    def test_rubric_adjudication_without_the_rubric_definition_does_not_clear_the_shortfall(self):
        self.assert_incomplete_rubric_adjudication_keeps_the_shortfall('rubric_definition')

    def assert_incomplete_rubric_adjudication_keeps_the_shortfall(self, key):
        """A not_upheld finding from a packet without one of the standards leaves the shortfall open."""
        self.prepare_below_threshold_reviews()
        real = protocol.add_focused_adjudication

        def build_without(*arguments, **options):
            real(*arguments, **options)
            arguments[2].pop(key)
        with patch.object(protocol, 'add_focused_adjudication', side_effect=build_without):
            _, packet = self.observe_rubric_adjudication()
        self.assertNotIn(key, packet)
        state = decisions.assess_problem_reviews(self.read_records(), self.artifacts)
        below = [row for row in state['obligations'] if row['code'] == 'problem_rubric_below_threshold']
        self.assertEqual([row['shortfalls'] for row in below], [[{'item': 'priority', 'required': '>= 2', 'graded': 1}]])

    def replace_first_bar(self):
        """A legitimate context repair replaces the first bar B1 by B1' after both results were graded against B1."""
        self.mutate(protocol.record_context_event, {'id': 'exposure', 'assignment_id': 'bar-first',
            'kind': 'contamination', 'source': 'author_history', 'reason': 'Author advocacy entered the bar context.',
            'evidence': [self.supporting]})
        def respond_to_probe(route, request, credential):
            packet = json.loads(request['input'][0]['content'])
            return self.build_provider_response({'allowed_control': packet['allowed_control'],
                'evidence_control': packet['evidence_control'], 'excluded_controls': []})
        with patch('research_harness.review_transport.send_request', side_effect=respond_to_probe):
            self.mutate(protocol.probe_route, {'id': 'repair-probe', 'route_id': 'route'}, credential='test-only')
        self.assign('bar', 'replacement', identifier='bar-replaced', context_repair={
            'assignment_id': 'bar-first', 'event_ids': ['exposure'], 'probe_id': 'repair-probe',
            'reason': 'Reassess the field bar in a verified independent context.', 'evidence': [self.supporting]})
        output = self.build_review_output('bar')
        output['reassessment'] = {'assignment_id': 'bar-first', 'findings': [{'review_id': 'review-bar-first',
            'disposition': 'revised', 'reason': 'The independent context gives the same field standard.',
            'evidence': [self.supporting]}]}
        with patch('research_harness.review_transport.send_request', return_value=self.build_provider_response(output)):
            self.mutate(protocol.invoke_assignment, {'id': 'attempt-bar-replaced', 'assignment_id': 'bar-replaced'},
                        credential='test-only')
        self.mutate(decisions.record_value_review, dict(output, id='review-bar-replaced', dossier_id=None,
                                                        assignment_id='bar-replaced'))

    def test_adjudicator_packet_keeps_the_original_bars_after_a_bar_repair(self):
        self.prepare_below_threshold_reviews()
        self.replace_first_bar()
        _, packet = self.observe_rubric_adjudication()
        records = self.read_records()
        self.assertEqual(packet['bar_calibrations'], [records['value_review'][identifier]['payload']
                                                     for identifier in ('review-bar-first', 'review-bar-second')])
        self.assertEqual(packet['current_bar_calibrations'], [records['value_review']['review-bar-replaced']['payload']])
        # The replaced bar retires the original pair from the current gate, so assess that pair by name.
        state = decisions.assess_problem_reviews(records, self.artifacts, review_ids=['review-result-first', 'review-result-second'])
        self.assertNotIn('problem_rubric_below_threshold', {row['code'] for row in state['obligations']})

    def test_rubric_adjudication_against_a_replaced_bar_does_not_clear_the_shortfall(self):
        self.prepare_below_threshold_reviews()
        self.replace_first_bar()
        real = protocol.add_focused_adjudication

        def build_with_the_current_bars(*arguments, **options):
            real(*arguments, **options)
            packet = arguments[2]
            packet['bar_calibrations'] = sorted(packet['current_bar_calibrations'] + [
                bar for bar in packet['bar_calibrations'] if bar['id'] != 'review-bar-first'], key=lambda bar: bar['id'])
        with patch.object(protocol, 'add_focused_adjudication', side_effect=build_with_the_current_bars):
            _, packet = self.observe_rubric_adjudication()
        self.assertEqual([bar['id'] for bar in packet['bar_calibrations']], ['review-bar-replaced', 'review-bar-second'])
        state = decisions.assess_problem_reviews(self.read_records(), self.artifacts,
                                                 review_ids=['review-result-first', 'review-result-second'])
        below = [row for row in state['obligations'] if row['code'] == 'problem_rubric_below_threshold']
        self.assertEqual([row['shortfalls'] for row in below], [[{'item': 'priority', 'required': '>= 2', 'graded': 1}]])

    def test_rubric_adjudication_cannot_waive_an_assurance_or_another_item(self):
        self.prepare_below_threshold_reviews()
        self.observe_rubric_adjudication()
        records = self.read_records()
        records['value_review']['review-result-first']['payload']['value']['assurances'][0]['status'] = 'failed'
        records['value_review']['review-result-first']['payload']['value']['rubric']['calibration'] = 'fail'
        state = decisions.assess_problem_reviews(records, self.artifacts)
        self.assertIn('problem_assurance_pending', {row['code'] for row in state['obligations']})
        below = next(row for row in state['obligations'] if row['code'] == 'problem_rubric_below_threshold')
        self.assertEqual([row['item'] for row in below['shortfalls']], ['calibration'])

    def test_rubric_significance_objection_claim_uses_sorted_json(self):
        self.observe_bar_reviews()
        output = self.build_review_output('result')
        output['value']['rubric']['significance'] = {'B2': 1, 'B1': 1}
        self.observe('result', 'first', output)
        self.observe('result', 'second')
        assignment = self.assign('adjudicator', 'focused-third', identifier='significance-focused',
            review_ids=['review-result-first', 'review-result-second'],
            objection_id=rubric_objection_id('review-result-first', 'significance'))
        packet = json.loads(self.artifacts.read(assignment['packet']))
        self.assertEqual(packet['objection']['claim'],
            'significance graded {"B1": 1, "B2": 1} by first; required max(B1, B2) >= 2')

    def test_rubric_adjudication_requires_a_ready_not_upheld_finding_for_the_same_subject(self):
        self.prepare_below_threshold_reviews()
        self.observe_rubric_adjudication('upheld')
        records = self.read_records()
        self.assertEqual([row['code'] for row in decisions.assess_rubric_reviews(records, self.artifacts,
            [records['value_review']['review-result-first']], digest(self.expected))],
            ['problem_rubric_below_threshold'])
        # A matching disposition string cannot substitute for the observed response.
        records['review_adjudication']['rubric-finding']['disposition'] = 'not_upheld'
        self.assertEqual([row['code'] for row in decisions.assess_rubric_reviews(records, self.artifacts,
            [records['value_review']['review-result-first']], digest(self.expected))],
            ['problem_rubric_below_threshold'])

    def test_rubric_adjudication_for_another_subject_or_without_held_evidence_does_not_resolve(self):
        self.prepare_below_threshold_reviews()
        self.observe_rubric_adjudication()
        records = self.read_records()
        records['review_adjudication']['rubric-finding']['subject']['statement_id'] = 'another-statement'
        self.assertEqual([row['code'] for row in decisions.assess_rubric_reviews(records, self.artifacts,
            [records['value_review']['review-result-first']], digest(self.expected))],
            ['problem_rubric_below_threshold'])
        (self.root / self.supporting['path']).unlink()
        records = self.read_records()
        self.assertEqual([row['code'] for row in decisions.assess_rubric_reviews(records, self.artifacts,
            [records['value_review']['review-result-first']], digest(self.expected))],
            ['artifact_missing', 'problem_rubric_below_threshold'])

    def test_focused_rubric_objection_must_name_an_original_below_threshold_grade(self):
        self.prepare_below_threshold_reviews()
        for identifier in ('rubric:absent:priority', 'rubric:review-result-first:B1',
                           'rubric:review-result-second:priority'):
            with self.subTest(identifier=identifier):
                self.assert_error('review_objection_missing', lambda: self.assign('adjudicator', 'focused-third',
                    review_ids=['review-result-first', 'review-result-second'], objection_id=identifier))

    def test_both_observed_results_must_cover_the_assessment(self):
        self.observe_result_reviews()
        evidence_digest = digest(self.expected)
        self.expected['assessment'] = {'id': 'new-assessment', 'grades': passing_grades(),
                                       'items': {}, 'primary': {'ground': 'B1', 'criterion': 'V3'}}
        reviews = [row for row in self.read_records()['value_review'].values() if row['payload']['stage'] == 'result']
        pending = decisions._assess_problem_coverage(self.read_records(), self.artifacts, reviews,
                                                     self.expected, evidence_digest)
        self.assertEqual({row['review_id'] for row in pending}, {'review-result-first', 'review-result-second'})
        self.assertTrue(all(row['missing'] == ['assessment'] for row in pending))

    def test_rubric_adjudication_refuses_a_pair_without_both_original_rubrics(self):
        self.observe_bar_reviews()
        output = self.build_review_output('result')
        output['value']['rubric']['priority'] = 1
        self.observe('result', 'first', output)
        original = protocol.canonical_prompt
        def previous_result(role, **options):
            if role == 'result' and options.get('orchestration') == 'problem_creation':
                options['version'] = 1
            return original(role, **options)
        output = self.build_review_output('result')
        del output['value']['rubric']
        del output['value']['rubric_evidence']
        with patch.object(protocol, 'canonical_prompt', side_effect=previous_result):
            self.observe('result', 'second', output)
        self.assert_error('review_objection_missing', lambda: self.assign('adjudicator', 'focused-third',
            review_ids=['review-result-first', 'review-result-second'],
            objection_id='rubric:review-result-first:priority'))

    def test_rubric_id_cannot_replace_an_explicit_material_objection(self):
        self.observe_bar_reviews()
        identifier = 'rubric:review-result-first:priority'
        output = self.build_review_output('result')
        output['value']['rubric']['priority'] = 1
        output['objections'] = [{'id': identifier, 'claim': 'The cited implication has an unsupported hypothesis.',
            'reason': 'The original source does not establish that hypothesis.', 'evidence': [self.supporting],
            'resolution_condition': 'Establish the missing hypothesis or narrow the claim.'}]
        self.observe('result', 'first', output)
        self.observe('result', 'second')
        self.assert_error('review_objection_missing', lambda: self.assign('adjudicator', 'focused-third',
            review_ids=['review-result-first', 'review-result-second'], objection_id=identifier))

    def test_material_bar_objection_can_keep_a_rubric_shaped_id(self):
        self.supporting = self.artifacts.put(b'The field comparison needs an additional hypothesis.', 'text/plain')
        self.prepare_review_route()
        identifier = 'rubric:review-bar-first:priority'
        output = self.build_review_output('bar')
        output['objections'] = [{'id': identifier, 'claim': 'The field comparison needs an additional hypothesis.',
            'reason': 'The supplied source requires it.', 'evidence': [self.supporting],
            'resolution_condition': 'Establish or state the extra hypothesis.'}]
        self.observe('bar', 'first', output)
        self.observe('bar', 'second')
        assignment = self.assign('adjudicator', 'focused-third',
            review_ids=['review-bar-first', 'review-bar-second'], objection_id=identifier)
        packet = json.loads(self.artifacts.read(assignment['packet']))
        self.assertEqual(packet['objection']['claim'], output['objections'][0]['claim'])
        self.assertNotIn('rubric_grades', packet)

    def test_problem_adjudication_keeps_generic_previous_prompt_authentication(self):
        for legacy in (False, True):
            assignment = {'role': 'adjudicator', 'subject': {'kind': 'problem', 'statement_id': 'statement-1'},
                          'prompt': protocol.canonical_prompt('adjudicator', legacy=legacy, version=1)}
            with self.subTest(legacy=legacy):
                self.assertTrue(protocol.is_previous_prompt(assignment))
                self.assertTrue(protocol._prompt_matches(assignment))

    def test_bar_can_precede_a_statement(self):
        records = self.read_records()
        records.pop("problem_selection")
        packet = protocol.build_packet(records, self.artifacts, None, "bar", "first")
        self.assertEqual(packet["subject"], {"kind": "problem", "statement_id": None})

    def test_result_requires_both_observed_bars(self):
        self.assert_error("review_bar_pending", lambda: protocol.build_packet(
            self.read_records(), self.artifacts, None, "result", "first"))
        self.supporting = self.artifacts.put(b"Field standard.", "text/plain")
        self.prepare_review_route()
        self.observe("bar", "first")
        self.assert_error("review_bar_pending", lambda: protocol.build_packet(
            self.read_records(), self.artifacts, None, "result", "first"))

    def test_changed_bar_field_or_area_is_refused_for_result(self):
        self.observe_bar_reviews()
        for key, changed in (("field", "A changed field"), ("area", "math.NT")):
            records = self.read_records()
            records["workspace"]["problem"][key] = changed
            self.assert_error("review_bar_mismatch", lambda: protocol.build_packet(records, self.artifacts, None, "result", "first"))

    def test_problem_result_continues_its_own_bar_session(self):
        self.observe_bar_reviews()
        self.assert_error("review_bar_continuity", lambda: self.assign("result", "third"))
        result = self.assign("result", "first")
        bar = self.read_records()["review_assignment"]["bar-first"]
        self.assertEqual(result["session_id"], bar["session_id"])
        self.assertEqual(result["history_assignment_id"], bar["id"])
        self.assertEqual(result["slot_id"], bar["slot_id"])
        self.assertEqual([turn["role"] for turn in result["history"]], ["user", "assistant"])

    def test_marker_authors_are_the_independence_authority(self):
        self.prepare_review_route()
        self.assert_error("review_not_independent", lambda: self.assign("bar", " CoAuthor "))
        self.assert_error("problem_author_unknown", lambda: self.assign("bar", "first", author="invented-author"))

    def test_result_packet_delivers_readings_outputs_validations_and_native_closure(self):
        output, validation = self.add_reading_and_computation()
        self.observe_bar_reviews()
        assignment = self.assign("result", "first")
        self.assertEqual(assignment["subject"], {"kind": "problem", "statement_id": "statement-1"})
        packet = json.loads(self.artifacts.read(assignment["packet"]))
        self.assertEqual(packet["problem_evidence"], self.expected)
        self.assertEqual(packet["problem_evidence_digest"], digest(self.expected))
        delivered = {entry["artifact"]["sha256"]: entry for entry in packet["evidence"]}
        for reference in (output, validation, self.expected["passages"][0]["excerpt"]):
            self.assertEqual(delivered[reference["sha256"]]["content"].encode(), self.artifacts.read(reference))
        self.assertIn("original_claim", json.dumps(packet["problem_evidence"]["formal_checks"]))

    def test_exact_problem_assurance_set_is_required(self):
        self.observe_bar_reviews()
        payload = self.observe("result", "first", self.build_review_output("result", decisions.ALL_CHECKS), record=False)
        self.assert_error("invalid_value_review", lambda: self.mutate(decisions.record_value_review, payload))
        payload = self.observe("result", "second", self.build_review_output("result", CHECKS[:-1]), record=False)
        self.assert_error("invalid_value_review", lambda: self.mutate(decisions.record_value_review, payload))

    def test_problem_not_assessed_is_recorded_and_blocks_assurance(self):
        self.observe_bar_reviews()
        output = self.build_review_output("result")
        output["value"]["assurances"][0]["status"] = "not_assessed"
        self.observe("result", "first", output)
        self.observe("result", "second")
        state = decisions.assess_problem_reviews(self.read_records(), self.artifacts)
        self.assertIn("problem_assurance_pending", {item["code"] for item in state["obligations"]})

    def test_current_observed_pair_covers_the_problem_evidence(self):
        self.add_reading_and_computation()
        self.observe_result_reviews()
        with patch.object(type(self.artifacts), "put", side_effect=AssertionError("read-only coverage")):
            state = decisions.assess_problem_reviews(self.read_records(), self.artifacts)
        self.assertTrue(state["ready"], state["obligations"])
        self.assertEqual(set(state["review_ids"]), {"review-result-first", "review-result-second"})
        self.assertEqual(set(state["bar_review_ids"]), {"review-bar-first", "review-bar-second"})

    def test_missing_pair_and_duplicate_reviewer_do_not_authorize_post(self):
        self.observe_result_reviews()
        for identifiers in (["review-result-first"], ["review-result-first", "review-result-first"]):
            state = decisions.assess_problem_reviews(self.read_records(), self.artifacts, review_ids=identifiers)
            self.assertIn("problem_review_pair_required", {item["code"] for item in state["obligations"]})

    def test_structural_review_assessment_does_not_describe_unmet_evidence(self):
        self.observe_result_reviews()
        claims = sys.modules["research_harness.problem_claims"]
        with patch.object(claims, "describe_problem_evidence", side_effect=AssertionError("unavailable claim evidence")):
            state = decisions.assess_problem_reviews(self.read_records(), self.artifacts, check_evidence=False)
        self.assertTrue(state["ready"], state["obligations"])
        self.assertIsNone(state["problem_evidence_digest"])

    def test_unobserved_results_cannot_cover_problem_evidence(self):
        self.observe_result_reviews()
        records = self.read_records()
        records["review_attempt"]["attempt-result-first"]["origin"] = "imported"
        state = decisions.assess_problem_reviews(records, self.artifacts)
        self.assertIn("problem_evidence_unassessed", {item["code"] for item in state["obligations"]})

    def test_changed_claim_or_computation_invalidates_coverage(self):
        self.add_reading_and_computation()
        self.observe_result_reviews()
        self.expected["computations"][0]["assessment"]["interpretation"] = "Changed interpretation"
        state = decisions.assess_problem_reviews(self.read_records(), self.artifacts)
        self.assertIn("problem_evidence_unassessed", {item["code"] for item in state["obligations"]})

    def test_records_without_delivered_output_bytes_do_not_count_as_coverage(self):
        output, _ = self.add_reading_and_computation()
        original = protocol._evidence
        def omit_output(artifacts, value, **kwargs):
            return [entry for entry in original(artifacts, value, **kwargs)
                    if entry["artifact"]["sha256"] != output["sha256"]]
        with patch.object(protocol, "_evidence", side_effect=omit_output):
            self.observe_result_reviews()
        state = decisions.assess_problem_reviews(self.read_records(), self.artifacts)
        self.assertIn("problem_evidence_unassessed", {item["code"] for item in state["obligations"]})

    def test_reading_digest_without_delivered_passages_does_not_count_as_coverage(self):
        self.add_reading_and_computation()
        original = protocol._evidence
        omitted = {passage["excerpt"]["sha256"] for passage in self.expected["passages"]}
        def omit_passages(artifacts, value, **kwargs):
            return [entry for entry in original(artifacts, value, **kwargs)
                    if entry["artifact"]["sha256"] not in omitted]
        with patch.object(protocol, "_evidence", side_effect=omit_passages):
            self.observe_result_reviews()
        state = decisions.assess_problem_reviews(self.read_records(), self.artifacts)
        self.assertIn("problem_evidence_unassessed", {item["code"] for item in state["obligations"]})

    def test_source_request_continuation_preserves_the_problem_bar_boundary(self):
        self.add_reading_and_computation()
        version = self.expected["readings"][0]["version_id"]
        self.supporting = self.artifacts.put(b"A field standard.", "text/plain")
        self.prepare_review_route()
        output = self.build_review_output("bar")
        output["value"]["status"] = "unresolved"
        output["source_requests"] = [{"id": "decisive-source", "kind": "source",
            "version_id": version, "depth": "fulltext"}]
        pending = self.observe("bar", "first", output, record=False)
        self.assert_error("review_sources_pending", lambda: self.mutate(decisions.record_value_review, pending))
        assignment = self.assign("bar", "first", identifier="bar-first-source", prior_assignment_id="bar-first")
        packet = json.loads(self.artifacts.read(assignment["packet"]))
        self.assertEqual(packet["subject"], {"kind": "problem", "statement_id": "statement-1"})
        self.assertEqual(packet["pending_source_requests"], [])
        self.assertEqual(packet["historical_reviews"], [])
        self.assertEqual(packet["source_deliveries"][0]["status"], "delivered")
        self.assertNotIn("PRIVATE_STATEMENT", json.dumps(packet))
        final = self.build_review_output("bar")
        with patch("research_harness.review_transport.send_request", return_value=self.build_provider_response(final)):
            self.mutate(protocol.invoke_assignment, {"id": "attempt-source-final", "assignment_id": assignment["id"]},
                credential="test-only")
        saved = self.mutate(decisions.record_value_review, dict(final, id="review-bar-first",
            dossier_id=None, assignment_id=assignment["id"]))["result"]
        self.assertEqual(saved["subject"], assignment["subject"])
        self.assertTrue(protocol.assignment_state(self.read_records(), self.artifacts, assignment["id"])["ready"])

    def test_new_statement_requires_new_result_reviews_and_preserves_field_bars(self):
        self.observe_result_reviews()
        revised = dict(self.statement, id="statement-2", statement="A changed exact statement")
        self.put("problem_statement", revised["id"], revised)
        self.put("problem_selection", "current", {"statement_id": revised["id"]})
        self.expected["claim"].update(statement_id=revised["id"], statement=revised["statement"])
        state = decisions.assess_problem_reviews(self.read_records(), self.artifacts)
        self.assertIn("problem_review_pair_required", {item["code"] for item in state["obligations"]})
        for reviewer in ("first", "second"):
            self.observe("result", reviewer, identifier="result-revised-" + reviewer)
        state = decisions.assess_problem_reviews(self.read_records(), self.artifacts)
        self.assertTrue(state["ready"], state["obligations"])


class ProblemOpennessReviewTests(ProblemCase):
    prepare_review_route = ProblemReviewTests.prepare_review_route
    build_provider_response = ProblemReviewTests.build_provider_response
    read_records = ProblemReviewTests.read_records
    build_review_output = ProblemReviewTests.build_review_output
    observe = ProblemReviewTests.observe

    def assign(self, role, reviewer, author="problem-author", identifier=None, **context):
        return ProblemReviewTests.assign(self, role, reviewer, author, identifier, **context)

    def setUp(self):
        super().setUp()
        self.initialize_problem()
        self.put_selecting_slate()
        self.selected = self.select_statement(candidate_id="candidate-1")

    def observe_openness(self, outcome="supported_open", change_output=None):
        from research_harness.literature import record_search
        from research_harness.problem_openness import export_openness
        search = self.capture_search()
        search["question"] = self.selected["statement"]
        self.mutate(record_search, search)
        exported = export_openness(self.store, {"assignment_id": "openness-assignment", "reviewer_id": "native-reviewer",
            "minimum": {"searches": [{"formulation": self.selected["statement"], "search_id": search["id"]}],
                "settling_work_id": None, "duplicate_resolutions": []}}, self.root / "openness-packet")
        native = json.loads(self.artifacts.read(exported["native_packet"]))
        if "route" not in self.store.snapshot()["records"].get("review_route", {}):
            self.prepare_review_route()
        self.mutate(protocol.record_assignment, {"id": "openness-assignment", "route_id": "route", "dossier_id": None,
            "role": "native_math", "reviewer_id": "native-reviewer", "author_id": "problem-author",
            "context": {"native_packet": exported["native_packet"]}})
        openness = {"outcome": outcome, "statement_digest": native["claim_digest"],
            "evidence": [entry["digest"] for entry in native["evidence"]], "searched_at": "2026-09-07",
            "coverage": "The exact formulation was searched in the captured provider response.",
            "reason": "No settling result was exposed in the bounded captured search."}
        if outcome != "unresolved":
            openness["minimum"] = native["scientific_context"]["openness_minimum"]
        output = {"schema_version": 2, "subject_digest": native["subject_digest"], "claim_digest": native["claim_digest"],
            "reviewer": native["reviewer"], "decision": "approve", "findings": {
                **{key: "Assessed the exact supplied subject." for key in ("statement", "assumptions", "scope", "dependencies", "policy")},
                "openness": openness}}
        if change_output is not None:
            change_output(output)
        with patch("research_harness.review_transport.send_request", return_value=self.build_provider_response(output)):
            self.mutate(protocol.invoke_assignment, {"id": "openness-attempt", "assignment_id": "openness-assignment"},
                credential="test-only")
        return output

    def test_exported_native_problem_packet_accepts_an_observed_supported_open_finding(self):
        output = self.observe_openness()
        saved = self.mutate(self.load_problem_module().record_openness, {"assignment_id": "openness-assignment"})["result"]
        self.assertEqual(saved["outcome"], "supported_open")
        self.assertEqual(saved["minimum"], output["findings"]["openness"]["minimum"])
        self.assertEqual(saved["statement_id"], self.selected["id"])

    def test_observed_unresolved_finding_records_without_minimum_audit(self):
        self.observe_openness("unresolved")
        with patch("research_harness.openness_minimum.audit_minimum", side_effect=AssertionError("unresolved audit")):
            saved = self.mutate(self.load_problem_module().record_openness, {"assignment_id": "openness-assignment"})["result"]
        self.assertEqual(saved["outcome"], "unresolved")

    def test_observed_finding_for_a_different_statement_is_refused(self):
        self.observe_openness(change_output=lambda output: output["findings"]["openness"].update(statement_digest="0" * 64))
        self.assert_error("openness_statement_mismatch", lambda: self.mutate(self.load_problem_module().record_openness,
            {"assignment_id": "openness-assignment"}))

    def test_unresolved_output_cannot_rebind_an_old_packet_to_a_new_statement(self):
        def change(output):
            revised = self.select_statement(identifier="statement-2", statement="A different exact proposition.")
            output["findings"]["openness"]["statement_digest"] = self.load_problem_module().compute_statement_digest(revised)
        self.observe_openness("unresolved", change_output=change)
        self.assert_error("openness_statement_mismatch", lambda: self.mutate(self.load_problem_module().record_openness,
            {"assignment_id": "openness-assignment"}))

    def test_real_problem_evidence_is_observed_and_assessed_without_artifact_writes(self):
        self.observe_openness()
        self.mutate(self.load_problem_module().record_openness, {"assignment_id": "openness-assignment"})
        reading = self.record_full_reading()
        self.mutate(self.load_problem_module().record_value, self.build_value([reading]))
        self.supporting = self.artifacts.put(b"The original comparison establishes the field standard.", "text/plain")
        for stage in ("bar", "result"):
            for reviewer in ("first", "second"):
                self.observe(stage, reviewer)
        with patch.object(type(self.artifacts), "put", side_effect=AssertionError("read-only review assessment")):
            state = decisions.assess_problem_reviews(self.read_records(), self.artifacts)
        self.assertTrue(state["ready"], state["obligations"])
        from research_harness.reading import record_reading
        self.mutate(record_reading, self.abstract_note(reading["version_id"], note_id="unused-abstract"))
        after = decisions.assess_problem_reviews(self.read_records(), self.artifacts)
        self.assertTrue(after["ready"], after["obligations"])
        self.assertEqual(after["problem_evidence_digest"], state["problem_evidence_digest"])

    def prepare_changed_value_reviews(self):
        self.observe_openness()
        self.mutate(self.load_problem_module().record_openness, {"assignment_id": "openness-assignment"})
        item = self.record_full_reading()
        from research_harness.problem_attempt import record_attempt
        from test_research_problem_attempt import build_attempt
        self.mutate(record_attempt, build_attempt(reading=item))
        self.mutate(self.load_problem_module().record_value, self.build_value([item]))
        from test_research_problem_assessment import build_assessment
        self.mutate(self.load_problem_module().record_assessment, build_assessment(self.selected))
        self.supporting = self.artifacts.put(b"The exact earlier result comparison.", "text/plain")
        for reviewer in ("first", "second"):
            self.observe("bar", reviewer)
        old = self.build_review_output("result")
        old["objections"] = [{"id": "prior-result-objection", "claim": "PRIVATE_PRIOR_RESULT_OBJECTION",
            "reason": "The earlier value paragraph needs a narrower interpretation.", "evidence": [self.supporting],
            "resolution_condition": "Bind the narrower interpretation to the read comparator."}]
        self.observe("result", "first", old)
        self.observe("result", "second")
        before = decisions.assess_problem_reviews(self.read_records(), self.artifacts)
        self.assertFalse(before["ready"], before["obligations"])
        self.assertIn("research_objection_unresolved", {item["code"] for item in before["obligations"]})
        changed = self.build_value([item], identifier="value-2")
        changed["motivation"][0]["text"] = "The narrowed consequence connects these exact finite constructions."
        self.mutate(self.load_problem_module().record_value, changed)
        return before

    def build_refreshed_result(self):
        output = self.build_review_output("result")
        output["value"]["objection_findings"] = [{"id": "prior-result-objection", "status": "resolved",
            "reason": "The new bound value paragraph makes the narrower supported claim.", "evidence": [self.supporting]}]
        return output

    def test_changed_value_can_refresh_observed_results_in_their_original_slots(self):
        before = self.prepare_changed_value_reviews()
        pending = decisions.assess_problem_reviews(self.read_records(), self.artifacts)
        self.assertIn("problem_evidence_unassessed", {item["code"] for item in pending["obligations"]})
        self.assertIn("research_objection_unresolved", {item["code"] for item in pending["obligations"]})
        for reviewer in ("first", "second"):
            output = self.build_refreshed_result()
            self.observe("result", reviewer, output, identifier="refreshed-" + reviewer)
            records = self.read_records()
            original = records["review_assignment"]["result-" + reviewer]
            refreshed = records["review_assignment"]["refreshed-" + reviewer]
            packet = json.loads(self.artifacts.read(refreshed["packet"]))
            self.assertEqual(refreshed["subject"], {"kind": "problem", "statement_id": self.selected["id"]})
            self.assertEqual(refreshed["slot_id"], original["slot_id"])
            self.assertEqual(refreshed["session_id"], original["session_id"])
            self.assertEqual(refreshed["history_assignment_id"], original["id"])
            self.assertEqual(packet["problem_refresh"]["assignment_id"], original["id"])
            self.assertNotEqual(packet["problem_evidence_digest"], before["problem_evidence_digest"])
            if reviewer == "first":
                self.assertIn("PRIVATE_PRIOR_RESULT_OBJECTION", json.dumps(refreshed["history"]))
            self.assert_error("review_resampling_forbidden", lambda: self.assign("result", reviewer,
                identifier="same-evidence-" + reviewer))
            self.assert_error("review_resampling_forbidden", lambda: self.assign("result", reviewer,
                identifier="same-evidence-explicit-" + reviewer, prior_assignment_id=refreshed["id"]))
            if reviewer == "first":
                partial = decisions.assess_problem_reviews(self.read_records(), self.artifacts)
                self.assertFalse(partial["ready"])
                self.assertIn("problem_evidence_unassessed", {item["code"] for item in partial["obligations"]})
        state = decisions.assess_problem_reviews(self.read_records(), self.artifacts)
        self.assertTrue(state["ready"], state["obligations"])
        self.assertEqual(set(state["review_ids"]), {"review-refreshed-first", "review-refreshed-second"})
        self.assertIn("review-result-first", self.read_records()["value_review"])
        decision = self.mutate(self.load_problem_module().record_decision, {"id": "refreshed-decision", "action": "post",
            "review_ids": state["review_ids"], "bar_review_ids": state["bar_review_ids"],
            "reason": "Both original reviewers assessed the changed bound value evidence."})["result"]
        self.assertEqual(decision["problem_evidence_digest"], state["problem_evidence_digest"])

    def test_changed_value_refresh_requires_the_prior_result_objections(self):
        self.prepare_changed_value_reviews()
        pending = decisions.assess_problem_reviews(self.read_records(), self.artifacts)
        self.assertIn("research_objection_unresolved", {item["code"] for item in pending["obligations"]})
        payload = self.observe("result", "first", record=False, identifier="refreshed-first")
        self.assert_error("review_objection_history_incomplete", lambda: self.mutate(decisions.record_value_review, payload))


    def test_settled_source_request_repair_does_not_leave_its_old_parent_pending(self):
        from research_harness.problem_delivery import assess_source_requests
        version = self.metadata(76002)
        self.supporting = self.artifacts.put(b"The observed context exposure.", "text/plain")
        self.prepare_review_route()
        output = self.build_review_output("bar")
        output["value"]["status"] = "unresolved"
        output["source_requests"] = [{"id": "abstract", "kind": "source", "version_id": version, "depth": "abstract"}]
        self.observe("bar", "first", output, record=False)
        self.mutate(protocol.record_context_event, {"id": "exposure", "assignment_id": "bar-first",
            "kind": "contamination", "source": "author_history", "reason": "Author advocacy entered the context.",
            "evidence": [self.supporting]})
        def respond_to_probe(route, request, credential):
            packet = json.loads(request["input"][0]["content"])
            return self.build_provider_response({"allowed_control": packet["allowed_control"],
                "evidence_control": packet["evidence_control"], "excluded_controls": []})
        with patch("research_harness.review_transport.send_request", side_effect=respond_to_probe):
            self.mutate(protocol.probe_route, {"id": "repair-probe", "route_id": "route"}, credential="test-only")
        self.assign("bar", "repair-reviewer", identifier="repair", context_repair={
            "assignment_id": "bar-first", "event_ids": ["exposure"], "probe_id": "repair-probe",
            "reason": "Reassess the exact source request in a verified fresh context.", "evidence": [self.supporting]})
        repair_output = self.build_review_output("bar")
        repair_output["value"]["status"] = "unresolved"
        repair_output["source_requests"] = [{"id": "page", "kind": "inventory_page", "page": 0}]
        with patch("research_harness.review_transport.send_request", return_value=self.build_provider_response(repair_output)):
            self.mutate(protocol.invoke_assignment, {"id": "repair-attempt", "assignment_id": "repair"}, credential="test-only")
        fulfilled = self.assign("bar", "repair-reviewer", identifier="fulfilled", prior_assignment_id="repair")
        packet = json.loads(self.artifacts.read(fulfilled["packet"]))
        self.assertEqual(packet["pending_source_requests"], [])
        final = self.build_review_output("bar")
        final["reassessment"] = {"assignment_id": "bar-first", "findings": []}
        with patch("research_harness.review_transport.send_request", return_value=self.build_provider_response(final)):
            self.mutate(protocol.invoke_assignment, {"id": "fulfilled-attempt", "assignment_id": "fulfilled"}, credential="test-only")
        self.mutate(decisions.record_value_review, dict(final, id="review-bar-repaired", dossier_id=None, assignment_id="fulfilled"))
        self.assertEqual(assess_source_requests(self.read_records(), self.artifacts), [])

