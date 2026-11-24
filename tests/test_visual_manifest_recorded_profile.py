"""A recorded page manifest is verified by the renderer that it records, or from the store.

The installed PDF renderer derives the pages of a manifest again only when it has the profile that the manifest
records. Under another renderer, or without one, the recorded page images are verified as stored artifacts, so a
stored review on a visual route keeps its authentication. New reviewer work on a visual route is a new assignment
or a new call. It is admitted only under the renderer that the manifests of its request and of the visual
transitions in its lineage record. The stores are those of test_visual_context_repair, test_visual_result_delivery,
test_review_staged_binary and test_review_staged_repair_upgrade.
"""

import ast
from contextlib import contextmanager
import copy
import json
from pathlib import Path
import unittest
from unittest.mock import patch

import research_harness
from research_harness import cli, research_decisions as decisions, review_pdf_pages, review_visual_sources
from research_harness import review_output_correction as correction, review_protocol as protocol
from research_harness import review_source_complete, review_staged as staged, review_staged_binary
from research_harness import review_staged_protocol as producer, review_transport as transport
from research_harness.errors import ResearchError
from research_harness.evaluation import Evaluation
from research_harness.review_transport import PACKET_LABEL
import test_visual_context_repair as fixtures

READ_INSTALLED_PROFILE, RENDER_WITH_INSTALLED = review_pdf_pages.profile, review_pdf_pages.render_bytes

# The record kinds of new reviewer work, and the functions that write them. A function of COMPARING compares the
# installed renderer itself, before it writes. Each other writer writes a replacement: the preparation of an
# assignment reaches it through the named call, after its comparison of the replaced assignment. That preparation is
# _prepare_assignment, which record_assignment and preview_parts_successor call.
NEW_WORK_KINDS = ('review_assignment', 'review_invocation', 'review_task_dispatch',
                  'review_output_correction_invocation')
ASSIGNMENT_WRITER = ('review_protocol', '_prepare_assignment')
COMPARING = {ASSIGNMENT_WRITER,
             ('review_staged_protocol', 'prepare_child'), ('review_staged_protocol', 'invoke_task'),
             ('review_output_correction', 'invoke_correction'), ('review_session_v4', 'prepare_session_assignment')}
PREPARED_WRITER = ('review_protocol', 'invoke_assignment')
PREPARATION = ('review_protocol', 'prepare_assignment_call')
REPLACEMENT_WRITERS = {
    ('review_refusal_replacement', 'prepare_replacement'): 'prepare_refusal_replacement',
    ('openness_reading_v7', 'prepare_refusal_assignment'): 'prepare_refusal_replacement',
    ('openness_reading_v7', 'prepare_notice_capacity_assignment'): 'prepare_refusal_replacement',
    ('review_parts_v3', 'prepare_refusal_assignment'): 'prepare_refusal_replacement',
    ('review_session_v4', 'prepare_refusal_assignment'): 'prepare_refusal_replacement',
    ('review_single_request', 'prepare_replacement'): 'prepare_capacity_replacement',
    ('review_parts_v3', 'prepare_capacity_assignment'): 'prepare_capacity_assignment',
}
# The one write that no comparison precedes: invoke_task writes again the dispatch of a call that it admitted before.
REPLAYED_WRITES = {('review_staged_protocol', 'invoke_task', 'replay_dispatch')}
# The one function that names the kind of its record in a variable. It writes the receipts of remote executions.
VARIABLE_KIND_WRITERS = {('remote', '_finish_intent_once')}


class Reached(Exception):
    """Raised by a replaced sender or worker start: the admission let the call through."""


def build_other_profile():
    return dict(READ_INSTALLED_PROFILE(), version='pdftoppm version 99.99.9', executable_sha256='0' * 64)


@contextmanager
def install_other_renderer():
    """Install a renderer of another profile. It renders with the installed program, so that a new manifest can be
    built under it; the yielded mock counts the pages that it renders."""
    other = build_other_profile()

    def render_as_other(data, page_index):
        with patch.object(review_pdf_pages, 'profile', READ_INSTALLED_PROFILE):
            rendered, result = RENDER_WITH_INSTALLED(data, page_index)
        return rendered, dict(result, render_profile=dict(other))

    with patch.object(review_pdf_pages, 'profile', side_effect=lambda: dict(other)), \
            patch.object(review_pdf_pages, 'render_bytes', side_effect=render_as_other) as render:
        yield render


class RecordedProfileTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        fixture = fixtures.VisualContextRepairTests()
        cls.addClassCleanup(fixture.doCleanups)
        fixture.setUp()
        case = fixture.case
        cls.assignment = fixture.assign()
        packet = json.loads(fixture.artifacts.read(cls.assignment['packet']))
        response = copy.deepcopy(fixture.output)
        response['reassessment'] = {'assignment_id': fixture.prior['id'], 'findings': [
            {'review_id': row['id'], 'disposition': 'revised',
             'reason': 'The exact fresh supplied source evidence supports this assessment.',
             'evidence': [{'kind': 'source', 'link': fixture.fixture.pdf_link}]} for row in packet['historical_reviews']]}
        with patch.object(transport, 'send_request', side_effect=lambda route, request, credential:
                          fixture.fixture.response(route, request, response)):
            case.mutate(protocol.invoke_assignment, {'id': 'repair-attempt', 'assignment_id': cls.assignment['id']},
                        credential='test-only')
        cls.review = case.mutate(decisions.record_value_review, dict(response, id='repair-final',
            dossier_id=None, assignment_id=cls.assignment['id']))['result']
        cls.records, cls.artifacts = case.read_records(), fixture.artifacts
        cls.packet = packet
        cls.manifest = packet[review_visual_sources.MARKER]
        cls.installed = review_pdf_pages.profile()

    def install(self, profile=None, error=None):
        """Replace the profile of the installed renderer for the rest of the test."""
        patcher = patch.object(review_pdf_pages, 'profile',
                               side_effect=error or (lambda: dict(profile)))
        patcher.start()
        self.addCleanup(patcher.stop)

    def other_profile(self):
        return dict(self.installed, version='pdftoppm version 99.99.9', executable_sha256='0' * 64)

    def assert_review_stands(self):
        attempt = self.records['review_attempt']['repair-attempt']
        self.assertTrue(protocol.authenticate_attempt(self.records, self.artifacts, self.assignment, attempt))
        state = protocol.assignment_state(self.records, self.artifacts, self.assignment['id'])
        self.assertTrue(state['ready'], state['obligations'])
        self.assertEqual(decisions.find_undelivered_review_references(self.records, self.artifacts, self.review), [])

    def test_stored_visual_review_stands_under_another_renderer_without_a_new_derivation(self):
        self.assertEqual(self.manifest['render_profile'], self.installed)
        self.assertTrue(self.manifest['pages'])
        with patch.object(review_pdf_pages, 'render_bytes', wraps=review_pdf_pages.render_bytes) as render:
            review_visual_sources.validate_manifest(Evaluation.of(self.records, self.artifacts), self.manifest)
        self.assertEqual(render.call_count, len(self.manifest['pages']))
        self.install(self.other_profile())
        with patch.object(review_pdf_pages, 'render_bytes', wraps=review_pdf_pages.render_bytes) as render:
            self.assert_review_stands()
        render.assert_not_called()

    def test_stored_visual_review_stands_without_a_renderer(self):
        self.install(error=ResearchError('review_visual_source_mismatch', 'Native PDF image delivery requires pdftoppm'))
        self.assert_review_stands()

    def test_recorded_manifest_with_a_changed_page_is_refused_by_its_stored_image(self):
        self.install(self.other_profile())
        text = self.artifacts.put(b'not an image', 'text/plain')
        for label, change in (('width', lambda page: page.update(width=page['width'] + 1)),
                              ('image of another artifact', lambda page: page.update(image=text)),
                              ('media type', lambda page: page.update(image=dict(page['image'], media_type='image/jpeg'))),
                              ('image that is no descriptor', lambda page: page.update(image='not a descriptor'))):
            changed = copy.deepcopy(self.manifest)
            change(changed['pages'][0])
            with self.subTest(changed=label), self.assertRaises(ResearchError) as refused:
                review_visual_sources.validate_manifest(Evaluation.of(self.records, self.artifacts), changed)
            self.assertEqual(refused.exception.code, 'review_visual_source_mismatch')
            self.assertIsNone(refused.exception.details)

    def test_recorded_profile_keeps_the_fixed_members_of_the_protocol(self):
        recorded = copy.deepcopy(self.manifest)
        recorded['render_profile'] = self.other_profile()
        self.assertEqual(review_visual_sources.validate_manifest(Evaluation.of(self.records, self.artifacts), recorded),
                         recorded)
        for label, change in (('dpi', dict(dpi=300)), ('extra member', dict(crop='none')),
                              ('digest', dict(executable_sha256='x')), ('version', dict(version=''))):
            changed = copy.deepcopy(recorded)
            changed['render_profile'].update(change)
            with self.subTest(changed=label), self.assertRaises(ResearchError) as refused:
                review_visual_sources.validate_manifest(Evaluation.of(self.records, self.artifacts), changed)
            self.assertEqual(refused.exception.code, 'review_visual_source_mismatch')

    def test_manifest_of_another_renderer_is_verified_as_stored_and_enters_no_new_delivery(self):
        recorded = copy.deepcopy(self.manifest)
        recorded['render_profile'] = self.other_profile()
        evaluation = Evaluation.of(self.records, self.artifacts)
        self.assertEqual(review_visual_sources.validate_manifest(evaluation, recorded), recorded)
        fresh = {key: copy.deepcopy(value) for key, value in self.packet.items()
                 if key not in (review_visual_sources.MARKER, 'digest')}
        fresh['evidence'] = [row for row in fresh.get('evidence', []) if row.get('encoding') != 'base64']

        def build_with_history(manifest):
            history = [{'role': 'user', 'content': PACKET_LABEL + json.dumps({review_visual_sources.MARKER: manifest})},
                       {'role': 'assistant', 'content': '{}'}]
            return review_visual_sources.prepare_packet(self.records, Evaluation.of(self.records, self.artifacts),
                                                        copy.deepcopy(fresh), history=history)

        with self.assertRaises(ResearchError) as refused:
            build_with_history(recorded)
        self.assertEqual((refused.exception.code, str(refused.exception)),
                         ('review_visual_source_mismatch', 'Use the exact native PDF render profile'))
        built = build_with_history(self.manifest)
        self.assertEqual(built[review_visual_sources.MARKER]['render_profile'], self.installed)

    def test_fixed_profile_members_are_those_of_the_installed_renderer(self):
        self.assertEqual({name: self.installed[name] for name, _ in review_visual_sources.PROFILE_PARAMETERS},
                         dict(review_visual_sources.PROFILE_PARAMETERS))
        self.assertEqual(set(self.installed),
                         {name for name, _ in review_visual_sources.PROFILE_PARAMETERS} | {'version', 'executable_sha256'})


class NewWorkTests(unittest.TestCase):
    """After a renderer update the stored records verify and a judgement on a stored answer is recorded; no new
    reviewer work starts on them."""
    def setUp(self):
        from research_harness import bar_sources, review_protocol
        released = patch.object(review_protocol, 'NEW_SESSION_RULES', (bar_sources.PROTOCOL_V3,))
        released.start()
        self.addCleanup(released.stop)
        super().setUp()

    REFUSAL = ('review_visual_source_mismatch', 'Use the exact native PDF render profile')

    def build_repair(self):
        fixture = fixtures.VisualContextRepairTests()
        self.addCleanup(fixture.doCleanups)
        fixture.setUp()
        return fixture

    def assert_refused(self, call):
        with self.assertRaises(ResearchError) as refused:
            call()
        self.assertEqual((refused.exception.code, str(refused.exception)), self.REFUSAL)

    def test_new_assignment_after_a_renderer_update_is_refused_before_any_page_is_rendered(self):
        fixture = self.build_repair()
        recorded = json.loads(fixture.artifacts.read(fixture.prior['packet']))[review_visual_sources.MARKER]
        with install_other_renderer() as render:
            evaluation = Evaluation.of(fixture.case.read_records(), fixture.artifacts)
            self.assertEqual(review_visual_sources.validate_manifest(evaluation, recorded), recorded)
            self.assert_refused(fixture.assign)
            render.assert_not_called()
        self.assertNotIn(fixture.value['id'], fixture.case.read_records()['review_assignment'])
        self.assertEqual(fixture.assign()['id'], fixture.value['id'])

    def test_run_after_a_renderer_update_is_refused_before_any_call(self):
        fixture = self.build_repair()
        assignment = fixture.assign()
        packet = json.loads(fixture.artifacts.read(assignment['packet']))
        response = copy.deepcopy(fixture.output)
        response['reassessment'] = {'assignment_id': fixture.prior['id'], 'findings': [
            {'review_id': row['id'], 'disposition': 'revised', 'reason': 'The fresh source evidence supports it.',
             'evidence': [{'kind': 'source', 'link': fixture.fixture.pdf_link}]} for row in packet['historical_reviews']]}

        def run():
            return fixture.case.mutate(protocol.invoke_assignment,
                                       {'id': 'run', 'assignment_id': assignment['id']}, credential='test-only')

        with patch.object(transport, 'send_request', side_effect=lambda route, request, credential:
                          fixture.fixture.response(route, request, response)) as send:
            with install_other_renderer():
                # The stored lineage of the assignment, with its visual transition, verifies under the other renderer.
                review_source_complete.validate(fixture.case.read_records(), fixture.artifacts, assignment, packet)
                self.assert_refused(run)
            send.assert_not_called()
            self.assertEqual(run()['result']['status'], 'completed')
            self.assertEqual(send.call_count, 1)

    def test_run_of_a_transition_child_after_a_renderer_update_is_refused_before_any_call(self):
        from test_visual_result_delivery import VisualResultDeliveryTests
        fixture = VisualResultDeliveryTests()
        self.addCleanup(fixture.doCleanups)
        fixture.setUp()
        output = fixture.case.build_review_output('result')

        def run():
            return fixture.case.mutate(protocol.invoke_assignment,
                                       {'id': 'run', 'assignment_id': fixture.assignment['id']}, credential='test-only')

        with patch.object(transport, 'send_request', side_effect=lambda route, request, credential:
                          fixture.response(route, request, output)) as send:
            with install_other_renderer():
                self.assert_refused(run)
            send.assert_not_called()
            self.assertEqual(run()['result']['status'], 'completed')

    def test_staged_packet_with_a_native_page_verifies_as_stored_under_another_renderer(self):
        from test_review_staged_binary import StagedBinaryTests
        fixture = StagedBinaryTests('test_native_pages_supply_the_exact_assigned_binary_and_actual_images')
        self.addCleanup(fixture.doCleanups)
        fixture.setUp()
        packet = fixture.prepared()
        request = fixture.request(packet)
        with install_other_renderer() as render:
            self.assertTrue(review_staged_binary.preserves_packet(fixture.records, fixture.artifacts, packet, request))
            render.assert_not_called()

    def build_staged(self):
        """A staged controller on a visual route, as test_review_staged_repair_upgrade records it."""
        from test_visual_result_delivery import VisualResultDeliveryTests
        fixture = VisualResultDeliveryTests()
        self.addCleanup(fixture.doCleanups)
        fixture.setUp()
        prior = fixture.assignment
        value = {key: prior[key] for key in ('role', 'route_id', 'reviewer_id', 'author_id', 'dossier_id')}
        manifest = staged.build_manifest(dict(statement='The bounded visual claim.'), [], [])
        with patch.object(producer, 'build_manifest', return_value=manifest):
            controller = fixture.case.mutate(protocol.record_assignment, dict(value, id='visual-controller',
                context=dict(staged_upgrade=dict(assignment_id=prior['id']))))['result']

        def command(name, payload):
            return cli.OPERATIONS[name](fixture.case.store, payload, expected_revision=fixture.case.store.revision,
                                       request_id='operator-' + str(fixture.case.store.revision))['result']

        return fixture.case.store, command, dict(controller_assignment_id=controller['id'])

    def test_frontier_after_a_renderer_update_is_refused_and_no_child_is_recorded(self):
        store, command, frontier = self.build_staged()
        revision = store.revision
        with install_other_renderer():
            self.assert_refused(lambda: command('review-assignment', frontier))
        self.assertEqual(store.revision, revision)
        self.assertEqual([row['command'] for row in command('review-assignment', frontier)['next']], ['review-run'])

    def test_staged_dispatch_after_a_renderer_update_is_refused_before_any_worker_starts(self):
        store, command, frontier = self.build_staged()
        run = command('review-assignment', frontier)['next'][0]
        revision = store.revision
        with patch.object(producer, 'wait_for_capture', side_effect=Reached) as start:
            with install_other_renderer():
                self.assert_refused(lambda: command('review-run', run['payload']))
            start.assert_not_called()
            self.assertEqual(store.revision, revision)
            with self.assertRaises(Reached):
                command('review-run', run['payload'])

    def answer_refused(self, fixture, assignment, output, identifier):
        """Record one answer of the reviewer and its refusal by the value review."""
        with patch.object(transport, 'send_request', side_effect=lambda route, request, credential:
                          fixture.fixture.response(route, request, output)):
            fixture.case.mutate(protocol.invoke_assignment, {'id': identifier, 'assignment_id': assignment['id']},
                                credential='test-only')
        with self.assertRaises(ResearchError):
            fixture.case.mutate(decisions.record_value_review,
                                dict(output, id='final', dossier_id=None, assignment_id=assignment['id']))

    def build_output(self, fixture, assignment, disposition):
        packet = json.loads(fixture.artifacts.read(assignment['packet']))
        output = copy.deepcopy(fixture.output)
        output['reassessment'] = {'assignment_id': fixture.prior['id'], 'findings': [
            {'review_id': row['id'], 'disposition': disposition, 'reason': 'The fresh source evidence supports it.',
             'evidence': [{'kind': 'source', 'link': fixture.fixture.pdf_link}]} for row in packet['historical_reviews']]}
        return output

    def test_refusal_replacement_after_a_renderer_update_is_refused_and_not_recorded(self):
        from research_harness.review_failure_class import plan_refusal_recovery
        fixture = self.build_repair()
        assignment = fixture.assign()
        output = self.build_output(fixture, assignment, 'revised')
        output['value']['evidence'] = list(output['value'].get('evidence', [])) + [
            fixture.case.artifacts.put(b'An artifact that the packet did not deliver.', 'text/plain')]
        self.answer_refused(fixture, assignment, output, 'answer')
        records = fixture.case.read_records()
        plan = plan_refusal_recovery(records, fixture.artifacts, records['review_assignment'][assignment['id']])
        self.assertEqual(plan['state'], 'replace')
        revision = fixture.case.store.revision
        with install_other_renderer():
            self.assert_refused(lambda: fixture.case.mutate(protocol.record_assignment, dict(plan['payload'])))
        self.assertEqual(fixture.case.store.revision, revision)
        replacement = fixture.case.mutate(protocol.record_assignment, dict(plan['payload']))['result']
        self.assertEqual(replacement['context']['output_refusal'], {'assignment_id': assignment['id']})

    def test_output_correction_after_a_renderer_update_is_refused_before_any_record(self):
        fixture = self.build_repair()
        assignment = fixture.assign()
        output = self.build_output(fixture, assignment, 'confirmed')
        for identifier in ('answer-one', 'answer-two'):
            self.answer_refused(fixture, assignment, output, identifier)
        payload = {'id': 'delta', 'assignment_id': assignment['id'], 'route_id': 'route'}
        revision = fixture.case.store.revision
        with patch.object(transport, 'send_request', side_effect=Reached) as send:
            with install_other_renderer():
                self.assert_refused(lambda: fixture.case.mutate(correction.invoke_correction, dict(payload),
                                                                credential='test-only'))
            send.assert_not_called()
            self.assertEqual(fixture.case.store.revision, revision)
            with self.assertRaises(Reached):
                fixture.case.mutate(correction.invoke_correction, dict(payload), credential='test-only')


    def test_value_review_of_a_stored_answer_is_recorded_under_another_renderer(self):
        fixture = self.build_repair()
        assignment = fixture.assign()
        output = self.build_output(fixture, assignment, 'revised')
        with patch.object(transport, 'send_request', side_effect=lambda route, request, credential:
                          fixture.fixture.response(route, request, output)):
            fixture.case.mutate(protocol.invoke_assignment, {'id': 'answer', 'assignment_id': assignment['id']},
                                credential='test-only')
        with install_other_renderer() as render:
            review = fixture.case.mutate(decisions.record_value_review,
                dict(output, id='final', dossier_id=None, assignment_id=assignment['id']))['result']
            render.assert_not_called()
        self.assertEqual(review['assignment_id'], assignment['id'])


class NewWorkWriterTests(unittest.TestCase):
    def test_every_writer_of_new_reviewer_work_stands_behind_a_renderer_comparison(self):
        writes, comparisons, calls, variable = {}, {}, {}, set()

        def read_calls(node, owner, function):
            for child in ast.iter_child_nodes(node):
                inner = child.name if isinstance(child, (ast.FunctionDef, ast.ClassDef)) else function
                if isinstance(child, ast.Call):
                    name = getattr(child.func, 'attr', getattr(child.func, 'id', None))
                    if name == 'immutable_record' and len(child.args) > 1:
                        if not isinstance(child.args[1], ast.Constant):
                            variable.add(owner)
                        elif child.args[1].value in NEW_WORK_KINDS:
                            writes.setdefault(owner, []).append((child.lineno, inner))
                    elif name == 'require_installed_renderer':
                        comparisons.setdefault(owner, []).append(child.lineno)
                    else:
                        calls.setdefault(owner, {}).setdefault(name, []).append((child.lineno, inner))
                read_calls(child, owner, inner)

        for path in sorted(Path(research_harness.__file__).parent.glob('*.py')):
            for node in ast.parse(path.read_text(encoding='utf-8')).body:
                # A function or a class of the module owns every call below it, also in a nested function.
                owner = (path.stem, getattr(node, 'name', None))
                read_calls(node, owner, owner[1])
        self.assertEqual(set(writes), COMPARING | set(REPLACEMENT_WRITERS) | {PREPARED_WRITER})
        self.assertEqual(variable, VARIABLE_KIND_WRITERS)
        self.assertEqual(set(comparisons), COMPARING | {PREPARATION})
        for owner in COMPARING:
            # The first comparison of a function stands before each of its writes.
            first = min(comparisons[owner])
            for line, function in writes[owner]:
                if owner + (function,) not in REPLAYED_WRITES:
                    self.assertLess(first, line, owner)
        for writer, call in REPLACEMENT_WRITERS.items():
            # The comparison of the replaced assignment stands before the call that leads to the writer.
            self.assertLess(min(comparisons[ASSIGNMENT_WRITER]), min(line for line, _ in calls[ASSIGNMENT_WRITER][call]),
                            writer)
        for line, function in writes[PREPARED_WRITER]:
            preceding = [call_line for call_line, inner in calls[PREPARED_WRITER][PREPARATION[1]]
                         if inner == function and call_line < line]
            self.assertTrue(preceding, (PREPARED_WRITER, function, line))


if __name__ == '__main__':
    unittest.main()
