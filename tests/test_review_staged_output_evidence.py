"""Output renders evaluate staged currency against real retained problem evidence."""

import copy
import json
from unittest import mock

from codex_route_fixtures import record_codex_route
from research_harness import output_views, review_protocol, review_staged_protocol
from research_harness.evidence import digest
from research_harness.errors import ResearchError
from test_research_problem_attempt import build_acceptance, build_attempt, build_native_attempt, put_native_attempt
from test_research_problem_assessment import build_assessment
from test_research_problem_citation_metadata import CitationMetadataCase
from test_research_problem_delivery import ProblemDeliveryCase
from test_research_problem_tools import ProblemToolsCase
import test_research_problem_claims as claim_fixtures
import test_research_problem_computations as computation_fixtures


class StagedOutputCase:
    def prepare_staged_controller(self):
        ordinary = self.assign('result', 'first', identifier='ordinary')
        packet = json.loads(self.artifacts.read(ordinary['packet']))
        record_codex_route(self, 'staged-route', output_tokens=128000, timeout_seconds=600)
        records = self.store.snapshot()['records']
        controller = dict(copy.deepcopy(ordinary), id='controller', slot_id='controller', route_id='staged-route')
        changes = review_staged_protocol.prepare_controller(records, self.artifacts, controller, packet,
                                                            records['review_route']['staged-route'])
        def apply(transaction):
            for kind, identifier, record in changes:
                transaction.put(kind, identifier, record)
            transaction.put('review_assignment', controller['id'], controller)
            return {}
        self.store.mutate('fixture.staged', {}, apply, expected_revision=self.store.revision,
                          request_id='staged-controller')
        return controller, packet

    def assert_render_matches_full_state(self, controller, *, currency='current', full=True):
        records = self.store.snapshot()['records']
        expected = review_protocol.assignment_state(records, self.artifacts, controller['id'])
        self.assertEqual(expected['effective']['currency_state'], currency)
        self.assertFalse(expected['completion']['complete'])
        self.assertFalse(records.get('review_observation'))
        evaluated = []
        real_state = review_protocol.assignment_state
        def evaluate(records, artifacts, identifier, **options):
            state = real_state(records, artifacts, identifier, **options)
            if identifier == controller['id']:
                evaluated.append(state)
            return state
        with mock.patch.object(review_protocol, 'assignment_state', side_effect=evaluate):
            rendered = (output_views.render_all if full else output_views.render_pending)(self.store)
        self.assertEqual(rendered['errors'], 0)
        self.assertEqual(evaluated, [expected])
        series = json.loads((self.root / 'intermediate/reviews/result/controller/series.json').read_bytes())
        self.assertEqual(series['turns'][0]['effective'], expected['effective'])


class StagedOutputEvidenceTests(StagedOutputCase, CitationMetadataCase, ProblemDeliveryCase):
    def test_staged_context_events_refuse_record_reference_dependencies(self):
        self.record_metadata()
        self.observe_bars()
        controller, _ = self.prepare_staged_controller()
        evidence = dict(id='repair-evidence', description='Retained evidence of foreign input.')
        self.put('research_work_item', evidence['id'], evidence)
        reference = dict(kind='record', record_kind='research_work_item', id=evidence['id'], digest=digest(evidence))
        with self.assertRaises(ResearchError) as rejected:
            self.mutate(review_protocol.record_context_event, dict(id='exposure', assignment_id=controller['id'],
                kind='contamination', source='foreign_input', reason='Foreign input reached this review.',
                evidence=[reference]))
        self.assertEqual(rejected.exception.code, 'invalid_review_context')
        self.assertNotIn('exposure', self.store.snapshot()['records'].get('review_context_event', {}))

    def test_render_with_retained_problem_evidence_matches_full_store_state(self):
        self.record_metadata()
        self.observe_bars()
        # Assessments can cite kinds outside the usual problem evidence set.
        self.put('collection', 'assessment-collection', {'id': 'assessment-collection'})
        records = self.store.snapshot()['records']
        assessment = build_assessment(records['problem_statement']['statement-1'])
        # A full-text capture is embedded in work; its provenance, bundle,
        # reading, selection and citation metadata are separate records.
        references = []
        for kind in ('reading', 'source', 'work', 'source_bundle', 'bundle_selection',
                     'problem_citation_metadata', 'collection'):
            identifier, record = next(iter(records[kind].items()))
            references.append(dict(kind='record', record_kind=kind, id=identifier, digest=digest(record)))
        assessment['items']['A2']['evidence'] = references
        self.mutate(self.load_problem_module().record_assessment, assessment)
        controller, packet = self.prepare_staged_controller()
        self.assertTrue(packet['problem_evidence']['readings'])
        self.assertTrue(records['work'][self.version]['fulltexts'])
        self.assert_render_matches_full_state(controller)
        # A newly selected citation changes currency even though it has no view
        # of its own and no staged task has run.
        payload = self.payload()
        payload['id'] = 'metadata-2'
        payload['year'] = '2026'
        payload['evidence']['year'] = self.evidence_span('2026')
        self.record_metadata(payload)
        self.assert_render_matches_full_state(controller, currency='stale', full=False)
        # A change to a dynamically read reference must also invalidate the
        # render: it must report the same defect as a full-store evaluation.
        self.put('collection', 'assessment-collection', {'id': 'assessment-collection', 'changed': True})
        with self.assertRaises(ResearchError) as full:
            review_protocol.assignment_state(self.store.snapshot()['records'], self.artifacts, controller['id'])
        with self.assertRaises(ResearchError) as rendered:
            output_views.render_pending(self.store)
        self.assertEqual(full.exception.code, 'problem_assessment_evidence_missing')
        self.assertEqual(rendered.exception.code, full.exception.code)
        self.assertEqual(rendered.exception.details, full.exception.details)


class StagedComputationOutputTests(StagedOutputCase, ProblemToolsCase, ProblemDeliveryCase):
    def test_render_with_search_computation_formal_check_and_native_result_matches_full_store(self):
        from research_harness import problem_attempt
        plan, observed = self.run_problem()
        computation_fixtures.ProblemComputationTests.assess_support(self, plan, observed)
        self.selected = self.store.snapshot()['records']['problem_statement']['statement-1']
        self.claim = dict(statement='The first four values satisfy the bound.', quantifiers='For n in [0,3]',
                          assumption_ids=[], proof_policy='lean-kernel', scope=dict(kind='named', name='finite range'))
        item = dict(kind='computation', execution_id=observed['id'], output_id='result',
                    formal_check=dict(claim=self.claim, required_deliverables=['range check']))
        native = build_native_attempt(self.selected['statement'], [build_acceptance()], {})
        attempt = build_attempt('result-attempt', put_native_attempt(self, native))
        self.mutate(problem_attempt.record_attempt, attempt)
        result = dict(kind='result', attempt_id=attempt['id'], acceptance_id='acceptance-1')
        reading = self.record_full_reading()
        self.mutate(self.load_problem_module().record_value, self.build_value([item, result, reading]))
        closure = claim_fixtures.ProblemFormalBindingTests.build_closure_record(self)
        identity = digest(closure['closure'])
        self.put('native_closure', identity, closure)
        self.mutate(self.load_problem_module().record_formal_check,
                    dict(evidence=dict(execution_id=observed['id'], output_id='result'), native_closure_digest=identity))
        self.observe_bars()
        self.observe_openness()
        self.mutate(self.load_problem_module().record_openness, dict(assignment_id='openness-assignment'))
        self.mutate(self.load_problem_module().record_assessment, build_assessment(self.selected))
        controller, packet = self.prepare_staged_controller()
        for key in ('openness_searches', 'readings', 'computations', 'formal_checks', 'attempt', 'results'):
            self.assertTrue(packet['problem_evidence'][key], key)
        self.assert_render_matches_full_state(controller)
