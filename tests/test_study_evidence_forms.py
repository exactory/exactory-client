"""A refusal of evidence.shape names every refused entry of its array and states the accepted evidence forms."""

import copy
import json
from pathlib import Path
import runpy
import unittest
from unittest.mock import patch

from literature_fixtures import LiteratureCase
import test_problem_result_reassessment_binding as binding_fixtures
import test_research_problem_reviews as problem_fixtures
import test_review_continuation as continuation_fixtures
import test_review_parts_study as study_fixtures
from test_reviewer_excerpt_sessions import ExcerptSessionCase, build_body
from research_harness import bar_sources, cli, literature, output_views, reading
from research_harness import review_continuation, review_protocol as protocol, review_refusal as refusal
from research_harness import review_refusal_notice as notices
from research_harness.errors import ResearchError, describe_error_at
from research_harness.evidence import digest


ROOT = Path(__file__).resolve().parents[1]
SHA256 = 'a' * 64
DESCRIPTOR = {'path': 'research/sources/objects/' + SHA256, 'sha256': SHA256, 'size': 10, 'media_type': 'text/plain'}
LINK = {'version_id': 'arxiv:2601.00001v1', 'source_id': 'source-1', 'artifact': DESCRIPTOR,
        'locator': {'kind': 'span', 'start': 0, 'end': 5, 'sha256': SHA256}}
DESCRIPTOR_EXAMPLE = '{"path":"<path>","sha256":"<sha256>","size":0,"media_type":"<media type>"}'
LINK_EXAMPLE = ('{"kind":"source","link":{"version_id":"<version id>","source_id":"<source id>","artifact":'
                + DESCRIPTOR_EXAMPLE + ',"locator":{"kind":"<as delivered>"}}}')
RECORD_EXAMPLE = '{"kind":"record","record_kind":"<record kind>","id":"<record id>","digest":"<digest>"}'
SHAPE_MSG = 'Use an artifact descriptor, source link or exact record reference'
POSITION_DETAILS = ('refused_indexes', 'refused_count', 'notice')


def refuse_shape(values, **options):
    """The error of the shape check, under the path that the caller of an evidence array adds."""
    try:
        with describe_error_at('/value/evidence'):
            refusal.validate_evidence_contract(None, None, values, **options)
    except ResearchError as error:
        return error
    raise AssertionError('The shape check accepted ' + json.dumps(values))


def build_fixture(owner, fixture_type):
    """An instance of a fixture test case, set up outside its own test methods, whose cleanups the owner runs."""
    fixture = fixture_type()
    owner.addCleanup(fixture.doCleanups)
    fixture.setUp()
    return fixture


class ShapeRefusalTests(unittest.TestCase):
    def test_every_refused_entry_is_named_with_the_forms_of_its_binding(self):
        values = [{'artifact': DESCRIPTOR}, DESCRIPTOR, SHA256, dict(LINK), {'kind': 'source', 'link': LINK}]
        for binding, notice in ((False, refusal.EVIDENCE_FORMS_NOTICE),
                                (True, refusal.REVIEW_BOUND_EVIDENCE_FORMS_NOTICE)):
            with self.subTest(review_binding=binding):
                error = refuse_shape(values, review_binding=binding)
                self.assertEqual((error.code, error.message, error.contract_check),
                                 ('invalid_strategy', SHAPE_MSG, 'evidence.shape'))
                self.assertEqual(error.details, {'path': '/value/evidence', 'refused_indexes': [0, 2, 3],
                                                 'refused_count': 3, 'notice': notice})

    def test_a_refused_array_gets_the_forms_alone(self):
        for values in ([], {'evidence': []}):
            with self.subTest(values=values):
                error = refuse_shape(values)
                self.assertEqual((error.code, error.message),
                                 ('invalid_strategy', 'Evidence must be an array with at least one entry'))
                self.assertEqual(error.details, {'path': '/value/evidence', 'notice': refusal.EVIDENCE_FORMS_NOTICE})

    def test_the_positions_stop_at_their_limit_and_the_count_does_not(self):
        error = refuse_shape([{'artifact': DESCRIPTOR}] * 150)
        self.assertEqual(error.details['refused_indexes'], list(range(refusal.REFUSED_INDEX_LIMIT)))
        self.assertEqual(error.details['refused_count'], 150)

    def test_a_malformed_entry_after_a_refused_entry_keeps_the_first_refusal(self):
        # urlsplit raises ValueError for this version id, and the identifier check refuses it with invalid_identifier.
        # The check of an earlier release stopped at the refused entry before it and never read this one.
        malformed = {'kind': 'source', 'link': dict(LINK, version_id='https://[x')}
        error = refuse_shape([{'artifact': DESCRIPTOR}, malformed])
        self.assertEqual((error.code, error.message, error.contract_check),
                         ('invalid_strategy', SHAPE_MSG, 'evidence.shape'))
        self.assertEqual((error.details['refused_indexes'], error.details['refused_count']), ([0, 1], 2))
        with self.assertRaises(ResearchError) as caught:
            refusal.validate_evidence_contract(None, None, [malformed])
        self.assertEqual((caught.exception.code, caught.exception.contract_check),
                         ('invalid_identifier', 'evidence.shape'))

    def test_a_selection_reports_the_positions_of_its_array_without_the_forms(self):
        error = refuse_shape([{'kind': 'source', 'link': {'version_id': LINK['version_id']}}], positions=[2],
                             forms_notice=False)
        self.assertEqual((error.code, error.contract_check), ('source_mismatch', 'evidence.shape'))
        self.assertEqual(error.details, {'path': '/value/evidence', 'refused_indexes': [2], 'refused_count': 1})

    def test_the_notice_of_a_wrapped_descriptor_names_its_position_and_the_three_forms(self):
        # The acceptance of T2 of 20261115234509-bar-slot-ends-on-evidence-form-and-route-input-limit.
        from test_openness_reading_v7_turns import MemoryArtifacts
        artifacts = MemoryArtifacts()
        output = {'value': {'evidence': [{'artifact': DESCRIPTOR}]}}
        error = refuse_shape(output['value']['evidence'])
        diagnosis = dict(code=error.code, check=error.contract_check,
                         details=dict(error.details, message=error.message))
        records = {'review_attempt': {'attempt': dict(id='attempt', assignment_id='bar', recorded_revision=1,
                                                      output=artifacts.put(json.dumps(output).encode('utf-8')))},
                   'review_output_refusal': {'attempt': dict(diagnosis, id='attempt', assignment_id='bar',
                       attempt_id='attempt', output_digest=digest(output), request_digest='b' * 64,
                       protocol=refusal.PROTOCOL, recorded_revision=2)}}
        for form in ('whole', 'bounded'):
            with self.subTest(form=form):
                notice = notices.build_refusal_notice(records, artifacts, ('attempt',), form=form,
                                                      diagnoses={'attempt': diagnosis})
                entry = notice['refused'][-1]
                self.assertEqual((entry['path'], entry['message']), ('/value/evidence', SHAPE_MSG))
                self.assertEqual(entry['details'], {'refused_indexes': [0], 'refused_count': 1,
                                                    'notice': refusal.EVIDENCE_FORMS_NOTICE})
                for example in (DESCRIPTOR_EXAMPLE, LINK_EXAMPLE, RECORD_EXAMPLE):
                    self.assertIn(example, entry['details']['notice'])
                self.assertLess(len(notices._serialize_value(entry)), notices.REFUSAL_ENTRY_CHARACTERS)
        self.assertEqual(notice['refused_part'], {'path': '/value/evidence', 'value': output['value']['evidence'],
                                                  'complete': True})

    def test_both_notices_state_the_depth_rule_of_the_prompt_unchanged(self):
        for notice in (refusal.EVIDENCE_FORMS_NOTICE, refusal.REVIEW_BOUND_EVIDENCE_FORMS_NOTICE):
            self.assertIn(protocol.EVIDENCE_DEPTH_SENTENCE, notice)


class IncidentSessionTests(ExcerptSessionCase):
    def setUp(self):
        from research_harness import bar_sources, review_protocol
        released = patch.object(review_protocol, 'NEW_SESSION_RULES', (bar_sources.PROTOCOL_V3,))
        released.start()
        self.addCleanup(released.stop)
        super().setUp()

    bundle = LiteratureCase.bundle
    full_note = LiteratureCase.full_note
    link = LiteratureCase.link
    span = LiteratureCase.span

    def test_a_bar_refused_for_the_incident_forms_is_replaced_with_their_positions_and_recorded(self):
        # Real shape of 20261115234509-bar-slot-ends-on-evidence-form-and-route-input-limit, slot A: the reviewer
        # wrote the artifact member of a packet evidence entry and the citation of a prior_context passage as
        # entries, in value.evidence and in two objections.
        version = self.versions[0]
        fulltext = self.store.snapshot()['records']['work'][version]['fulltexts'][0]
        bundle = self.bundle(version, capture=fulltext)
        self.mutate(literature.import_bundle, bundle)
        self.mutate(reading.record_reading, self.full_note(bundle))
        # A passage of 4,000 characters puts the first request above the smallest task level, which the
        # replacement keeps, so the replacement with its notice fits it.
        passage = self.link(version, fulltext['source_id'], fulltext['text'], build_body(1)[:4000], kind='span')
        self.assign('bar-a', source_links=[passage])
        packet = self.packet('bar-a')
        self.assertEqual(packet['source_rules'], bar_sources.SESSION_PROTOCOL)
        citation = packet['prior_context'][0]['citation']
        descriptor = packet['evidence'][0]['artifact']
        refused = self.review_response('bar')
        refused['value']['evidence'] = [{'artifact': descriptor}, copy.deepcopy(citation)]
        refused['objections'] = [dict(id='objection-' + str(number), claim='The comparator needs a stated bound.',
            reason='The delivered passage states the bound only for finite inputs.',
            evidence=[copy.deepcopy(citation), {'artifact': descriptor}],
            resolution_condition='State the bound for every input of the study.') for number in (1, 2)]
        self.invoke('bar-a', refused)
        with self.assertRaises(ResearchError) as caught:
            self.record_final('bar-a', refused)
        self.assertEqual(caught.exception.code, 'invalid_strategy')
        records = self.store.snapshot()['records']
        stored = records['review_output_refusal']['attempt-bar-a']
        self.assertEqual((stored['code'], stored['check']), ('invalid_strategy', 'evidence.shape'))
        self.assertEqual(stored['details'], {'path': '/value/evidence', 'message': SHAPE_MSG, 'refused_indexes': [0, 1],
                                             'refused_count': 2, 'notice': refusal.EVIDENCE_FORMS_NOTICE})
        output_views.render_workspace_views(self.store.root)
        series = json.loads((self.store.root / 'intermediate/reviews/bar/bar-a/series.json').read_text())
        shown = series['turns'][0]['runs'][0]['refusal']['details']
        self.assertEqual((shown['refused_indexes'], shown['refused_count']), ([0, 1], 2))
        plan = review_continuation.plan_next(records, self.artifacts, 'bar-a')
        self.assertEqual((plan['state'], plan['command']), ('replace', 'review-assignment'))
        replacement = self.mutate(cli.OPERATIONS[plan['command']], plan['payload'])['result']
        [entry] = self.packet(replacement['id'])['output_refusal']['refused']
        self.assertEqual((entry['path'], entry['details']), ('/value/evidence', {
            'refused_indexes': [0, 1], 'refused_count': 2, 'notice': refusal.EVIDENCE_FORMS_NOTICE}))
        corrected = copy.deepcopy(refused)
        corrected['value']['evidence'] = [descriptor, {'kind': 'source', 'link': copy.deepcopy(citation)}]
        for objection in corrected['objections']:
            objection['evidence'] = [{'kind': 'source', 'link': copy.deepcopy(citation)}, descriptor]
        self.invoke(replacement['id'], corrected)
        self.assertEqual(self.record_final(replacement['id'], corrected)['assignment_id'], replacement['id'])


class OtherGrammarTests(unittest.TestCase):
    """The arrays of another grammar get the positions in their array and no text of the three forms."""
    def setUp(self):
        from research_harness import review_parts
        for released in (patch.object(review_parts, 'NEW_SERIES_PROTOCOLS', (review_parts.PROTOCOL,)),
                         patch.object(protocol, 'NEW_SESSION_RULES', (bar_sources.PROTOCOL_V3,))):
            released.start()
            self.addCleanup(released.stop)
        super().setUp()

    def prepare(self, fixture_type):
        fixture = build_fixture(self, fixture_type)
        return fixture

    def classify(self, fixture, attempt_id):
        finding = refusal.classify_observed_output(fixture.records(), fixture.case.artifacts, attempt_id)
        self.assertEqual(finding['check'], 'evidence.shape')
        return finding['details']

    def test_a_verification_final_names_the_refused_link_of_its_check(self):
        fixture = self.prepare(study_fixtures.VerificationPartsTests)
        fixture.enlarge_verification()
        _, final = fixture.finish()
        output = fixture.final_output(final)
        valid = output['checks'][1]['evidence']
        output['checks'][1]['evidence'] = valid + [{'version_id': LINK['version_id']}]
        fixture.invoke(final, output, 'malformed-final')
        details = self.classify(fixture, 'malformed-final')
        self.assertEqual((details['path'], details['refused_indexes'], details['refused_count']),
                         ('/checks/1/evidence', [len(valid)], 1))
        self.assertNotIn('notice', details)

    def test_a_v3_study_result_names_the_position_of_a_source_link_after_typed_references(self):
        fixture = self.prepare(study_fixtures.ReadinessPartsTests)
        from codex_route_fixtures import limit_codex_input
        from research_harness import review_route_capacity as capacity
        from test_review_output_profile import build_row
        row = dict(build_row(level=None, kind='single', thinking=1), largest_output_tokens=12)
        profile = patch.object(capacity, 'read_profile_priors', return_value=[row])
        profile.start()
        self.addCleanup(profile.stop)
        limit_codex_input(fixture, fixture.measure(fixture.logical) * 2)
        fixture.logical['source_rules'] = bar_sources.SESSION_PROTOCOL
        assignment = fixture.assign('v3-result')
        self.assertNotIn('review_parts', fixture.packet(assignment))
        output = fixture.final_output(assignment)
        typed = [reference for reference in fixture.references if reference['kind'] == 'result'][:2]
        self.assertEqual(len(typed), 2)
        malformed = {'kind': 'source', 'link': {'version_id': LINK['version_id'], 'source_id': 'source-1'}}
        output['support']['checks'][1]['evidence'] = typed + [malformed]
        fixture.invoke(assignment, output, 'malformed-result')
        details = self.classify(fixture, 'malformed-result')
        self.assertEqual((details['path'], details['refused_indexes'], details['refused_count']),
                         ('/support/checks/1/evidence', [2], 1))
        self.assertNotIn('notice', details)

    def test_a_readiness_series_names_the_refused_reference_of_its_check(self):
        fixture = self.prepare(study_fixtures.ReadinessPartsTests)
        fixture.enlarge_readiness()
        _, final = fixture.finish()
        output = fixture.final_output(final, all_citations=True)
        evidence = output['support']['checks'][1]['evidence']
        output['support']['checks'][1]['evidence'] = [evidence[0], {'artifact': DESCRIPTOR}] + evidence[1:]
        fixture.invoke(final, output, 'malformed-final')
        details = self.classify(fixture, 'malformed-final')
        self.assertEqual((details['path'], details['refused_indexes'], details['refused_count']),
                         ('/support/checks/1/evidence', [1], 1))
        self.assertNotIn('notice', details)


class ArrayPathTests(unittest.TestCase):
    """Each caller of the three forms names the evidence array that the positions count in."""
    def setUp(self):
        from research_harness import bar_sources, review_protocol
        released = patch.object(review_protocol, 'NEW_SESSION_RULES', (bar_sources.PROTOCOL_V3,))
        released.start()
        self.addCleanup(released.stop)
        super().setUp()

    def assert_refused_entry(self, call, path, notice):
        with self.assertRaises(ResearchError) as caught:
            call()
        error = caught.exception
        self.assertEqual((error.code, error.contract_check), ('invalid_strategy', 'evidence.shape'))
        self.assertEqual(error.details, {'path': path, 'refused_indexes': [1], 'refused_count': 1, 'notice': notice})

    def test_a_reassessment_finding_of_a_review_names_its_evidence_array(self):
        fixture = build_fixture(self, binding_fixtures.ResultReassessmentBindingTests)
        fixture.prepare()
        output = copy.deepcopy(fixture.output)
        finding = output['reassessment']['findings'][0]
        finding.update(disposition='revised', evidence=[copy.deepcopy(fixture.reference), {'artifact': DESCRIPTOR}])
        self.assert_refused_entry(lambda: fixture.contract(output), '/reassessment/findings/0/evidence',
                                  refusal.REVIEW_BOUND_EVIDENCE_FORMS_NOTICE)

    def test_a_staged_final_names_the_array_of_a_reassessment_finding_and_of_an_adjudication(self):
        frame = {'context_repair': {'assignment_id': 'prior'},
                 'historical_reviews': [{'id': 'review-1', 'payload': {}}, {'id': 'review-2', 'payload': {}}]}
        output = {'reassessment': {'assignment_id': 'prior', 'findings': [
            dict(review_id='review-1', disposition='revised', reason='The bound holds.', evidence=[DESCRIPTOR]),
            dict(review_id='review-2', disposition='revised', reason='The bound needs a scope.',
                 evidence=[DESCRIPTOR, {'artifact': DESCRIPTOR}])]}}
        subject = {'kind': 'problem'}
        self.assert_refused_entry(lambda: refusal._validate_staged_final(
            {}, None, dict(role='bar', subject=subject), {}, {'manifest': {'frame': frame}}, output),
            '/reassessment/findings/1/evidence', refusal.REVIEW_BOUND_EVIDENCE_FORMS_NOTICE)
        adjudication = dict(objection_id='objection-1', disposition='upheld', correction_admissible=None,
                            reason='The objection names a gap.', evidence=[DESCRIPTOR, {'artifact': DESCRIPTOR}])
        self.assert_refused_entry(lambda: refusal._validate_staged_final(
            {}, None, dict(role='adjudicator', subject=subject), {},
            {'manifest': {'frame': {'objection': {'id': 'objection-1'}}}}, adjudication),
            '/evidence', refusal.EVIDENCE_FORMS_NOTICE)

    def test_a_problem_adjudication_names_its_evidence_array(self):
        case = build_fixture(self, problem_fixtures.ProblemReviewTests)
        case.supporting = case.artifacts.put(b'An exact synthetic field standard.', 'text/plain')
        case.prepare_review_route()
        case.observe('bar', 'first')
        records = case.read_records()
        packet = {'protocol': protocol.PROTOCOL, 'role': 'adjudicator', 'subject': {'kind': 'problem'},
                  'objection': {'id': 'objection-1'}, 'evidence': [], 'source_deliveries': []}
        packet['digest'] = digest(packet)
        focused = dict(records['review_assignment']['bar-first'], id='focused', role='adjudicator',
                       packet=protocol._put_json(case.artifacts, packet), packet_digest=packet['digest'])
        records['review_assignment'][focused['id']] = focused
        output = dict(objection_id='objection-1', disposition='upheld', correction_admissible=None,
                      reason='The objection names a gap.', evidence=[case.supporting, {'artifact': case.supporting}])
        self.assert_refused_entry(lambda: protocol.validate_adjudication_contract(
            records, case.artifacts, focused, packet, output), '/evidence', refusal.EVIDENCE_FORMS_NOTICE)


class StoredShapeRefusalTests(unittest.TestCase):
    """A store whose evidence.shape refusal and replacement were recorded before the positions existed."""
    def setUp(self):
        from research_harness import bar_sources, review_protocol
        released = patch.object(review_protocol, 'NEW_SESSION_RULES', (bar_sources.PROTOCOL_V3,))
        released.start()
        self.addCleanup(released.stop)
        super().setUp()

    def test_a_stored_replacement_without_shape_details_stays_verified_with_the_same_fingerprint(self):
        fixture = build_fixture(self, continuation_fixtures.ContinuationTests)
        installed = refusal.classify_observed_output

        def classify_without_positions(records, artifacts, attempt_id):
            """The diagnosis of 0.91.5: the same code, message and path, and no other detail."""
            finding = installed(records, artifacts, attempt_id)
            if finding is not None and finding['check'] == 'evidence.shape':
                finding['details'] = {key: value for key, value in finding['details'].items()
                                      if key not in POSITION_DETAILS}
            return finding

        invalid = fixture.case.build_review_output('bar')
        invalid['value']['evidence'] = [{'unexpected': 'evidence'}]
        fixture.invoke(invalid)
        with patch.object(refusal, 'classify_observed_output', side_effect=classify_without_positions):
            with self.assertRaises(ResearchError):
                fixture.execute(fixture.plan(), request='refuse-evidence')
            replace = fixture.plan()
            self.assertEqual((replace['state'], replace['command']), ('replace', 'review-assignment'))
            replacement = fixture.execute(replace, request='replace-refused-output')['result']
        readers = runpy.run_path(str(ROOT / 'ci/record_fingerprint.py'))
        with patch.object(refusal, 'classify_observed_output', side_effect=classify_without_positions):
            before = readers['fingerprint_snapshot'](fixture.case.store.root)
        records = fixture.case.read_records()
        stored = records['review_output_refusal']['observed']
        self.assertEqual((stored['code'], stored['check'], stored['details']),
                         ('invalid_strategy', 'evidence.shape', {'path': '/value/evidence', 'message': SHAPE_MSG}))
        [entry] = protocol._read_json(fixture.case.artifacts, replacement['packet'])['output_refusal']['refused']
        self.assertEqual((entry['check'], entry['details']), ('evidence.shape', {}))
        from research_harness.review_refusal_replacement import validate_replacement_origin
        validate_replacement_origin(records, fixture.case.artifacts, records['review_assignment'][replacement['id']])
        self.assertEqual(readers['fingerprint_snapshot'](fixture.case.store.root), before)
        head = before['assignments'][fixture.assignment['id']]
        self.assertEqual(head['assignment_state']['refusal_codes'],
                         ['invalid_strategy', 'review_assignment_replaced', 'review_output_refused'])
        self.assertEqual(fixture.plan(head=replacement['id'])['state'], 'run')


if __name__ == '__main__':
    unittest.main()
