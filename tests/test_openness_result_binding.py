"""Accepted solver results cited by searches reach every v6 reading turn."""

import copy
import json
from unittest.mock import patch

from codex_route_fixtures import invoke_codex
from openness_reading_fixtures import OpennessReadingCase
from research_harness import openness_reading as reading, problem_openness, review_protocol as protocol
from research_harness.evidence import digest
import test_problem_openness_native_reviews as native_fixtures
from test_research_problem_attempt import build_acceptance, build_native_attempt, put_native_attempt


class ResultBindingTests(OpennessReadingCase):
    reading_protocol = None
    route_model = 'gpt-6-astra'
    text_sizes = (121000,)
    pin_json = native_fixtures.NativeOpennessReviewTests.pin_json
    evidence = native_fixtures.NativeOpennessReviewTests.evidence
    output = native_fixtures.NativeOpennessReviewTests.output

    def setUp(self):
        super().setUp()
        self.limit_input(1300000)
        self.native = build_native_attempt('A previous formulation admits a finite counterexample.', [], {})
        claim = self.native['contract']['original_claim']
        self.proof = self.artifacts.put(b'Synthetic proof: the finite example violates the previous bound.\n', 'text/plain')
        self.manifest = dict(schema_version=1, kind='analytical', claim_digest=digest(claim),
            conclusion=dict(outcome='counterexample', dependency_ids=[], route_bindings=[]),
            artifacts=[dict(path='proof.md', digest=self.proof['sha256'], role='proof')],
            dependencies=[], external_dependencies=[], verification=None)
        self.manifest_ref = self.pin_json(self.manifest)
        self.checkpoint = dict(schema_version=1, id='checkpoint-1', kind='counterexample', claim=claim,
            origin=dict(kind='journal_move', node_id='node-1', move=1, journal_prefix_digest='a' * 64),
            evidence_digests=[self.manifest_ref['sha256']], verification_status='accepted',
            what_changed='The previous formulation fails.', remaining_obligation_ids=[],
            next_hypothesis='Assess the revised formulation.', milestone_id='finite-counterexample')
        self.native['checkpoints'] = [dict({key: self.checkpoint[key] for key in
            ('id', 'kind', 'claim', 'verification_status')}, origin_kind='journal_move')]
        self.acceptance = build_acceptance(outcome='counterexample', evidence_digests=[self.manifest_ref['sha256']])
        self.native['acceptances'] = [self.acceptance]
        self.native_digest = put_native_attempt(self, self.native)
        self.cited_as = 'Theorem L9'
        self.support = self.observe_result('proof-review')
        self.link_id = self.put_link('proof-review')
        self.search = dict(self.search, impact='Theorem L9 of this record refutes the previous formulation.')
        self.put('literature_search', self.search['id'], self.search)
        self.searches = [self.search]

    def observe_result(self, identifier, change_packet=None):
        packet = dict(protocol='native-math-review-v1', kind='result', root=self.native['attack_root'],
            objective_id=self.native['objective_id'], contract=copy.deepcopy(self.native['contract']),
            subject=copy.deepcopy(self.checkpoint), subject_digest=digest(self.checkpoint),
            claim_digest=digest(self.checkpoint['claim']), reviewer=dict(source='host',
                actor_id='proof-reviewer-' + identifier, attestation_id='review-assignment:' + identifier),
            scientific_context=dict(scope='exact_subject_and_dependencies'),
            evidence=[self.evidence(self.proof), self.evidence(self.manifest_ref, 'blob')],
            execution_provenance=[], unmaterialized_digests=[])
        if change_packet:
            change_packet(packet)
        if self.manifest['kind'] == 'certificate':
            packet['scientific_context']['certificate_outputs'] = [dict(kind='result', execution_id='verification-run',
                output_id='certificate', locator=dict(kind='artifact'), artifact=row['artifact'])
                for row in packet['evidence'] if row['artifact']['media_type'] == 'application/octet-stream']
        reference = self.pin_json(packet)
        self.mutate(protocol.record_assignment, dict(id=identifier, route_id='route', dossier_id=None,
            role='native_math', reviewer_id=packet['reviewer']['actor_id'], author_id='problem-author',
            context=dict(native_packet=reference)))
        output = self.output(packet)
        invoke_codex(self, identifier, output)
        return packet

    def put_link(self, identifier):
        state = protocol.assignment_state(self.store.snapshot()['records'], self.artifacts, identifier)
        self.assertTrue(state['ready'], state['obligations'])
        key = dict(attack_root=self.native['attack_root'], objective_id=self.native['objective_id'],
                   acceptance_digest=digest(dict(self.acceptance, review=identifier)))
        link = dict(key, acceptance_id=self.acceptance['id'], checkpoint_id=self.checkpoint['id'],
            evidence_digests=self.acceptance['evidence_digests'],
            review=dict(assignment_id=identifier, final_assignment_id=identifier,
                attempt_id=state['attempt_id'], output_digest=state['output_digest']),
            provenance=dict(source='operator', actor_id='search-controller', attestation_id='audited-delivery'))
        self.put('native_acceptance_review', digest(key), link)
        return digest(key)

    def export_result(self, **options):
        binding = dict(native_attempt_digest=self.native_digest, acceptance_id=self.acceptance['id'], cited_as=self.cited_as)
        minimum = dict(searches=[dict(formulation=self.selected['statement'], search_id=self.search['id'])],
                       settling_work_id=None, duplicate_resolutions=[], results=[binding])
        return problem_openness.export_openness(self.store, dict(assignment_id='turn-1', reviewer_id='openness-reviewer',
            minimum=minimum), **options)

    def replace_result(self, data, media_type='text/plain', kind='analytical', role='proof', extras=(), verification=None):
        self.proof = self.artifacts.put(data, media_type)
        self.manifest = dict(self.manifest, kind=kind, verification=verification,
            artifacts=[dict(path='proof.pdf' if media_type == 'application/pdf' else 'proof.bin',
                            digest=self.proof['sha256'], role=role)] + [row for row, _ in extras])
        self.manifest_ref = self.pin_json(self.manifest)
        self.publish_result([ref for _, ref in extras])

    def publish_result(self, references=(), change_packet=None):
        self.checkpoint['evidence_digests'] = [self.manifest_ref['sha256']]
        self.acceptance['evidence_digests'] = [self.manifest_ref['sha256']]
        self.native['acceptances'][0] = self.acceptance
        self.native_digest = put_native_attempt(self, self.native)
        identifier = 'replacement-' + str(self.store.revision)
        def alter(packet):
            packet['evidence'].extend(self.evidence(ref) for ref in references)
            if change_packet:
                change_packet(packet)
        self.observe_result(identifier, alter)
        self.link_id = self.put_link(identifier)

    def drive_result(self, *, gapped=False):
        from test_openness_finding_answer import build_answer
        exported = self.export_result(accept_gaps=gapped)
        reference = exported['native_packet']
        native = json.loads(self.artifacts.read(reference))
        minimum = native['scientific_context']['openness_minimum']
        entry = next(row for row in native['evidence'] if row['digest'] == minimum['results'][0]['result_digest'])
        body = json.loads(self.artifacts.read(entry['artifact']))
        prior, deliveries = None, []
        for number in range(1, 22):
            identifier = 'turn-' + str(number)
            self.assign(identifier, reference, prior)
            packet = self.read_packet(identifier)
            self.assertEqual(reading.find_protocol(packet), reading.PROTOCOL_V6)
            if reading.find_stage(packet) == 'final':
                self.invoke(identifier, build_answer(packet, 'unresolved' if gapped else 'supported_open'))
                records = self.store.snapshot()['records']
                verified = reading.verify_problem_coverage(records, self.artifacts, records['review_assignment'][identifier], final=True)
                units = verified['units']
                self.assertTrue(any(row.get('record_kind') == 'accepted_result' for row in units))
                expected = {row['sha256'] for row in body['manifests']}
                expected.update(row.get('text_sha256', row['sha256']) for row in body['artifacts']
                                if row['delivery'] in ('text', 'pdf_text'))
                self.assertEqual({row['text_sha256'] for row in units if row['kind'] == 'result_text'}, expected)
                saved = self.mutate(self.load_problem_module().record_openness, dict(assignment_id=identifier))['result']
                self.assertEqual(saved['minimum']['results'], minimum['results'])
                self.assertEqual(saved['outcome'], 'unresolved' if gapped else 'supported_open')
                return exported, body, deliveries, saved
            deliveries.extend(packet['reading_turn']['reading_plan']['delivered_now'])
            self.invoke(identifier, self.build_reading_response(packet, excerpts=[]))
            prior = identifier
        self.fail('The result journey must reach a recorded finding')

    def test_g3_analytical_incident_delivers_cited_claim_and_proof(self):
        exported = self.export_result()
        native = json.loads(self.artifacts.read(exported['native_packet']))
        [binding] = native['scientific_context']['openness_minimum']['results']
        self.assertEqual(binding['link_digest'], digest(self.store.snapshot()['records']['native_acceptance_review'][self.link_id]))
        rows = reading.list_reading_rows(self.store.snapshot()['records'], self.artifacts, native)
        result = next(row for row in rows if row.get('record_kind') == 'accepted_result')
        body = json.loads(self.artifacts.read(result['artifact']))
        self.assertEqual(body['cited_as'], 'Theorem L9')
        self.assertEqual(body['checkpoint']['claim']['statement'], 'A previous formulation admits a finite counterexample.')
        self.assertEqual(body['review']['assignment_id'], 'proof-review')
        texts = [self.artifacts.read(row['artifact']) for row in rows if row['kind'] == 'result_text']
        self.assertIn(self.artifacts.read(self.proof), texts)
        self.assertIn(self.artifacts.read(self.manifest_ref), texts)
        _, _, _, saved = self.drive_result()
        self.assertIn('Theorem L9', json.dumps(saved['minimum']))

    def test_g3_result_beside_the_current_attempt_reaches_the_finding(self):
        from research_harness.problem_attempt import record_attempt
        from test_research_problem_attempt import build_attempt
        other = build_native_attempt(self.selected['statement'], [], {})
        other['objective_id'] = 'objective-current-statement'
        identifier = put_native_attempt(self, other)
        attempt = self.mutate(record_attempt, build_attempt(native_attempt_digest=identifier,
            statement_id=self.selected['id']))['result']
        self.cited_as = 'Proposition L2'
        self.search = dict(self.search, impact='Proposition L2 of a separate objective excludes this case.',
                          dispositions=[dict(row, reason='Proposition L2 of a separate objective excludes this case.')
                                                    for row in self.search['dispositions']])
        self.put('literature_search', self.search['id'], self.search)
        _, body, _, saved = self.drive_result()
        self.assertNotEqual(body['native_attempt_digest'], attempt['solver']['native_attempt_digest'])
        self.assertEqual(saved['minimum']['results'][0]['cited_as'], 'Proposition L2')
        self.assertEqual(saved['minimum']['searches'][0]['search_digest'], digest(self.search))

    def build_pdf(self, with_text=True):
        content = b'BT /F1 12 Tf 40 720 Td (Synthetic proof: counting gives the finite counterexample.) Tj ET' if with_text else b''
        objects = [b'<< /Type /Catalog /Pages 2 0 R >>', b'<< /Type /Pages /Kids [3 0 R] /Count 1 >>',
            b'<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>',
            b'<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>',
            b'<< /Length ' + str(len(content)).encode() + b' >>\nstream\n' + content + b'\nendstream']
        data, offsets = b'%PDF-1.4\n', []
        for number, body in enumerate(objects, 1):
            offsets.append(len(data))
            data += str(number).encode() + b' 0 obj\n' + body + b'\nendobj\n'
        start = len(data)
        data += b'xref\n0 6\n0000000000 65535 f \n'
        data += b''.join(('%010d 00000 n \n' % offset).encode() for offset in offsets)
        return data + b'trailer\n<< /Size 6 /Root 1 0 R >>\nstartxref\n' + str(start).encode() + b'\n%%EOF\n'

    def test_g3_pdf_proof_uses_real_extraction_and_keeps_both_digests(self):
        self.replace_result(self.build_pdf(), 'application/pdf')
        exported, body, _, _ = self.drive_result()
        [part] = body['artifacts']
        self.assertEqual(part['delivery'], 'pdf_text')
        self.assertEqual(part['sha256'], self.proof['sha256'])
        native = json.loads(self.artifacts.read(exported['native_packet']))
        reference = next(row['artifact'] for row in native['evidence'] if row['digest'] == part['text_sha256'])
        self.assertIn('counting gives the finite counterexample', self.artifacts.read(reference).decode())
        # A recorded PDF extraction remains verifiable without running a later extractor.
        with patch('research_harness.fulltext.extract', side_effect=AssertionError('PDF was extracted again')):
            self.assertEqual(problem_openness.assess_openness(self.store.snapshot()['records'], self.artifacts), [])

    def test_g3_pdf_without_text_is_a_gap_and_can_record_unresolved(self):
        self.replace_result(self.build_pdf(False), 'application/pdf')
        self.assert_error('openness_minimum_gaps', self.export_result)
        exported, body, _, _ = self.drive_result(gapped=True)
        self.assertEqual(body['artifacts'][0]['delivery'], 'described')
        gaps = exported['audit_forecast']['gaps']
        self.assertTrue(any(row['condition'] == 'result_undelivered' and row['code'] == 'openness_result_gap' for row in gaps))

    def test_g3_lean_result_delivers_verification_and_text_proof(self):
        verification = dict(run_id='lean-run', result_digest='a' * 64, policy_review=None,
                            requested_declaration='synthetic_bound', requested_type_digest='b' * 64)
        self.replace_result(b'theorem synthetic_bound : True := True.intro\n', kind='lean', verification=verification)
        _, body, _, _ = self.drive_result()
        self.assertEqual(body['manifests'][0]['kind'], 'lean')
        self.assertEqual(json.loads(self.artifacts.read(self.manifest_ref))['verification'], verification)
        self.assertEqual(body['artifacts'][0]['delivery'], 'text')

    def test_g3_certificate_binary_is_described_with_notice(self):
        certificate = self.artifacts.put(b'\xff\x00synthetic-certificate', 'application/octet-stream')
        extra = (dict(path='certificate.bin', digest=certificate['sha256'], role='certificate'), certificate)
        self.replace_result(b'assert finite_check()\n', kind='certificate', role='checker', extras=[extra],
            verification=dict(run_id='certificate-run', result_digest='a' * 64, policy_review=None,
                              requested_declaration=None, requested_type_digest=None))
        exported, body, _, _ = self.drive_result()
        self.assertEqual([row['delivery'] for row in body['artifacts']], ['text', 'described'])
        notice = next(row for row in exported['audit_forecast']['notices'] if row.get('condition') == 'result_described')
        self.assertEqual(notice['artifact']['sha256'], certificate['sha256'])
        self.assertEqual(exported['audit_forecast']['outcomes']['supported_open'], 'possible')

    def test_g3_binary_proof_is_a_gap(self):
        self.replace_result(b'\x89PNG\r\n\x1a\n\xffsynthetic-proof', 'image/png')
        self.assert_error('openness_minimum_gaps', self.export_result)
        exported, body, _, _ = self.drive_result(gapped=True)
        self.assertEqual(body['artifacts'][0]['delivery'], 'described')
        self.assertIn('result_undelivered', [row['condition'] for row in exported['audit_forecast']['gaps']])

    def test_g3_extended_objective_binds_without_a_value_extension_reason(self):
        self.native['totals']['moves'] = 200
        self.native_digest = put_native_attempt(self, self.native)
        self.drive_result()

    def test_g3_missing_link_hands_off_to_delivery_then_export(self):
        from research_harness.stop_classes import describe_terminal
        records = self.store.snapshot()['records']
        records['native_acceptance_review'] = {}
        with patch.object(self.store, 'snapshot', return_value=dict(records=records)):
            self.assert_error('openness_result_unlinked', self.export_result)
        terminal = describe_terminal('openness_result_unlinked')
        self.assertEqual(terminal['state'], 'handoff')
        self.assertEqual(terminal['command'], 'search deliver-attempt')
        self.drive_result()

    def test_g3_two_approvals_keep_the_review_of_the_acceptance(self):
        self.observe_result('second-approval')
        _, body, _, _ = self.drive_result()
        self.assertEqual(body['review']['assignment_id'], 'proof-review')

    def test_result_text_and_descriptor_larger_than_one_turn_deliver_every_part(self):
        from research_harness import review_excerpts
        allowance = review_excerpts.TURN_FULLTEXT_CHARACTERS
        self.checkpoint['claim'] = copy.deepcopy(self.native['contract']['original_claim'])
        self.checkpoint['claim']['statement'] = 'A finite bound. ' * (3 * allowance // 16)
        self.native['checkpoints'][0]['claim'] = self.checkpoint['claim']
        self.manifest['claim_digest'] = digest(self.checkpoint['claim'])
        proof = ('Synthetic argument.\n\n' * (3 * allowance // 20 + 1)).encode()
        self.replace_result(proof)
        _, _, deliveries, _ = self.drive_result()
        for kind in ('result_text', 'accepted_result'):
            selected = [row for row in deliveries if row.get('record_kind', row['kind']) == kind
                        and (kind != 'result_text' or row['text_sha256'] == self.proof['sha256'])]
            self.assertGreaterEqual(len(selected), 3)
            self.assertEqual([row['part'] for row in selected], list(range(1, selected[0]['parts'] + 1)))

    def test_latest_snapshot_preserves_binding_but_changed_acceptance_stales_finding(self):
        from research_harness.problem_openness_reviews import validate_bound_results
        from research_harness.openness_minimum import list_minimum_gaps
        _, _, _, saved = self.drive_result()
        self.native['totals']['moves'] = 25
        put_native_attempt(self, self.native)
        self.assertEqual(problem_openness.assess_openness(self.store.snapshot()['records'], self.artifacts), [])
        pinned = validate_bound_results(saved['minimum'], self.store.snapshot()['records'], self.artifacts)
        for change in ('status', 'claim', 'evidence', 'missing'):
            changed = copy.deepcopy(self.native)
            if change == 'status':
                changed['acceptances'][0]['status'] = 'invalidated'
            elif change == 'claim':
                changed['checkpoints'][0]['claim']['statement'] = 'A different claim.'
            elif change == 'evidence':
                changed['acceptances'][0]['evidence_digests'] = ['e' * 64]
            else:
                changed['acceptances'] = []
            put_native_attempt(self, changed)
            records = self.store.snapshot()['records']
            with self.subTest(change=change):
                self.assertEqual(validate_bound_results(saved['minimum'], records, self.artifacts), pinned)
                gaps = list_minimum_gaps(saved['minimum'], self.selected['statement'], records, self.artifacts)
                self.assertIn('openness_result_changed', [row['code'] for row in gaps])
                pending = problem_openness.assess_openness(records, self.artifacts)
                self.assertEqual((pending[0]['code'], pending[0]['cause']),
                                 ('problem_openness_stale', 'openness_result_changed'))

    def test_unresolved_finding_also_stales_when_acceptance_changes(self):
        self.replace_result(b'\x89PNG\r\n\x1a\n\xffsynthetic unreadable proof', 'image/png')
        self.drive_result(gapped=True)
        self.native['acceptances'][0]['status'] = 'invalidated'
        put_native_attempt(self, self.native)
        pending = problem_openness.assess_openness(self.store.snapshot()['records'], self.artifacts)
        self.assertEqual((pending[0]['code'], pending[0]['cause']),
                         ('problem_openness_stale', 'openness_result_changed'))

    def test_record_check_four_requires_each_finding_result_link(self):
        from research_harness.record_check import check_acceptance_reviews
        self.drive_result()
        records = self.store.snapshot()['records']
        self.assertIn(self.link_id, check_acceptance_reviews(records, self.artifacts))
        records['native_acceptance_review'] = {}
        self.assert_error('openness_result_unlinked', lambda: check_acceptance_reviews(records, self.artifacts))

    def test_export_refuses_unbound_superseded_and_unaccepted_results(self):
        original = self.native_digest
        self.native_digest = 'f' * 64
        self.assert_error('problem_result_unbound', self.export_result)
        self.native_digest = original
        changed = copy.deepcopy(self.native)
        changed['totals']['moves'] = 26
        put_native_attempt(self, changed)
        self.assert_error('problem_result_superseded', self.export_result)
        changed['acceptances'][0]['status'] = 'invalidated'
        self.native_digest = put_native_attempt(self, changed)
        self.assert_error('problem_result_missing', self.export_result)

    def test_export_refuses_unready_or_mismatched_linked_output(self):
        original = self.store.snapshot()['records']['native_acceptance_review'][self.link_id]
        for changes in (dict(final_assignment_id='missing-final'), dict(output_digest='e' * 64),
                        dict(attempt_id='missing-attempt')):
            with self.subTest(changes=changes):
                self.put('native_acceptance_review', self.link_id, dict(original, review=dict(original['review'], **changes)))
                self.assert_error('openness_native_evidence_missing', self.export_result)

    def test_export_requires_the_whole_linked_manifest_closure(self):
        original = self.support
        for change in ('artifact', 'claim'):
            identifier = 'bad-closure-' + change
            def alter(packet):
                if change == 'artifact':
                    packet['evidence'] = [self.evidence(self.manifest_ref, 'blob')]
                else:
                    packet['subject']['claim']['statement'] = 'A substituted statement.'
                    packet['subject_digest'] = digest(packet['subject'])
                    packet['claim_digest'] = digest(packet['subject']['claim'])
            self.observe_result(identifier, alter)
            state = protocol.assignment_state(self.store.snapshot()['records'], self.artifacts, identifier)
            link = self.store.snapshot()['records']['native_acceptance_review'][self.link_id]
            self.put('native_acceptance_review', self.link_id, dict(link, review=dict(assignment_id=identifier,
                final_assignment_id=identifier, attempt_id=state['attempt_id'], output_digest=state['output_digest'])))
            with self.subTest(change=change):
                self.assert_error('openness_native_evidence_missing', self.export_result)
        self.support = original

    def test_export_rejects_malformed_or_duplicate_result_specs_before_writes(self):
        base = dict(native_attempt_digest=self.native_digest, acceptance_id=self.acceptance['id'], cited_as='Theorem L9')
        candidates = [None, {}, False, [dict(base, extra='field')], [dict(base, cited_as='')],
                      [dict(base, native_attempt_digest='not-a-digest')], [base, base]]
        for results in candidates:
            spec = dict(assignment_id='invalid-result', reviewer_id='openness-reviewer', minimum=dict(
                searches=[dict(formulation=self.selected['statement'], search_id=self.search['id'])],
                settling_work_id=None, duplicate_resolutions=[], results=results))
            before = sorted(self.root.rglob('*'))
            with self.subTest(results=results):
                self.assert_error('invalid_input', lambda: problem_openness.export_openness(self.store, spec))
                self.assertEqual(sorted(self.root.rglob('*')), before)
        self.assertTrue(self.export_result()['native_packet'])

    def test_closure_delivers_child_manifests_and_accepted_dependencies(self):
        lemma = self.artifacts.put(b'A synthetic lemma justifies the finite computation.\n', 'text/plain')
        lean = dict(self.manifest, kind='lean', artifacts=[dict(path='lemma.lean', digest=lemma['sha256'], role='theorem')],
                    verification=dict(run_id='lean-check', result_digest='e' * 64, policy_review=None,
                                      requested_declaration='lemma', requested_type_digest='f' * 64))
        child = self.pin_json(lean)
        dependency = self.pin_json(dict(self.manifest, kind='certificate', verification=dict(run_id='check',
            result_digest='d' * 64, policy_review=None, requested_declaration=None, requested_type_digest=None)))
        self.native['acceptances'].append(build_acceptance('accepted-dependency', evidence_digests=[dependency['sha256']]))
        self.manifest = dict(self.manifest, dependencies=[child['sha256']],
                             conclusion=dict(self.manifest['conclusion'], dependency_ids=['accepted-dependency']))
        self.manifest_ref = self.pin_json(self.manifest)
        self.publish_result([lemma, child, dependency])
        _, body, _, _ = self.drive_result()
        self.assertEqual({row['kind'] for row in body['manifests']}, {'analytical', 'lean', 'certificate'})
        self.assertEqual({row['sha256'] for row in body['artifacts']}, {self.proof['sha256'], lemma['sha256']})

    def test_closure_refuses_missing_children_unaccepted_dependencies_and_excess_depth(self):
        from research_harness.problem_openness_reviews import required_evidence
        packet = copy.deepcopy(self.support)
        cases = [('missing-child', dict(self.manifest, dependencies=['e' * 64])),
                 ('missing-acceptance', dict(self.manifest,
                     conclusion=dict(self.manifest['conclusion'], dependency_ids=['not-accepted']))),
                 ('wrong-claim', dict(self.manifest, claim_digest='f' * 64)),
                 ('missing-verification', dict(self.manifest, kind='lean'))]
        for name, manifest in cases:
            reference = self.pin_json(manifest)
            packet['subject']['evidence_digests'] = [reference['sha256']]
            packet['evidence'] = [self.evidence(reference, 'blob'), self.evidence(self.proof)]
            with self.subTest(case=name):
                self.assert_error('openness_native_evidence_missing', lambda: required_evidence(
                    self.artifacts, packet, self.native, result_kinds=True))
        references = []
        identity = self.manifest_ref['sha256']
        for _ in range(65):
            reference = self.pin_json(dict(self.manifest, dependencies=[identity]))
            references.append(reference)
            identity = reference['sha256']
        packet = copy.deepcopy(self.support)
        packet['subject']['evidence_digests'] = [identity]
        packet['evidence'].extend(self.evidence(ref, 'blob') for ref in references)
        self.assert_error('openness_native_evidence_missing', lambda: required_evidence(
            self.artifacts, packet, self.native, result_kinds=True))

    def test_executables_and_toolchains_are_described_even_when_text(self):
        toolchain = self.artifacts.put(b'Synthetic toolchain version 1.\n', 'text/plain')
        self.manifest = dict(self.manifest, artifacts=self.manifest['artifacts'] +
            [dict(path='toolchain.txt', digest=toolchain['sha256'], role='toolchain')],
            external_dependencies=[dict(path='verifier', digest='e' * 64)])
        self.manifest_ref = self.pin_json(self.manifest)
        self.publish_result([toolchain], lambda packet: packet['execution_provenance'].append(dict(
            path='verifier', digest='e' * 64, size=128, role='native_verifier_executable')))
        exported, body, _, _ = self.drive_result()
        self.assertEqual({row['role'] for row in body['artifacts'] if row['delivery'] == 'described'},
                         {'toolchain', 'executable'})
        notices = [row for row in exported['audit_forecast']['notices'] if row.get('condition') == 'result_described']
        self.assertEqual(len(notices), 2)

    def test_binary_nonproof_pdf_is_a_notice(self):
        self.replace_result(self.build_pdf(False), 'application/pdf', role='support')
        exported, _, _, _ = self.drive_result()
        self.assertEqual(exported['audit_forecast']['outcomes']['supported_open'], 'possible')
        self.assertTrue(any(row.get('condition') == 'result_described' for row in exported['audit_forecast']['notices']))

    def test_replay_refuses_a_rewritten_result_descriptor(self):
        from research_harness.problem_openness_reviews import validate_bound_results
        exported = self.export_result()
        packet = json.loads(self.artifacts.read(exported['native_packet']))
        minimum = packet['scientific_context']['openness_minimum']
        binding = minimum['results'][0]
        reference = next(row['artifact'] for row in packet['evidence'] if row['digest'] == binding['result_digest'])
        body = json.loads(self.artifacts.read(reference))
        for changed in (dict(body, note='An author substituted an openness claim.'), dict(body, artifacts=[{}])):
            binding['result_digest'] = self.pin_json(changed)['sha256']
            self.assert_error('openness_native_evidence_missing', lambda: validate_bound_results(
                minimum, self.store.snapshot()['records'], self.artifacts))

    def test_legacy_v1_packet_and_finding_keep_their_original_bytes(self):
        from openness_legacy_fixtures import export_as_0_85_3
        from research_harness.openness_minimum import audit_minimum
        # Exercise the new path first; the old export then retains exactly its frozen v1 shape.
        self.export_result()
        spec = dict(assignment_id='legacy', reviewer_id='openness-reviewer', minimum=dict(
            searches=[dict(formulation=self.selected['statement'], search_id=self.search['id'])],
            settling_work_id=None, duplicate_resolutions=[]))
        exported = export_as_0_85_3(self.store, spec, self.root / 'legacy-packet')
        original = self.artifacts.read(exported['native_packet'])
        native = json.loads(original)
        minimum = native['scientific_context']['openness_minimum']
        self.assertEqual(minimum['schema_version'], 1)
        self.assertNotIn('results', minimum)
        finding = dict(minimum=minimum, outcome='supported_open', searched_at='2026-11-14',
                       evidence=[row['digest'] for row in native['evidence']])
        before = problem_openness.serialize_record(finding)
        audit_minimum(finding, self.selected['statement'], self.store.snapshot()['records'], self.artifacts,
                      set(finding['evidence']))
        self.assertEqual(problem_openness.serialize_record(finding), before)
        self.assertEqual(self.artifacts.read(exported['native_packet']), original)

    def test_chain_withholds_credit_and_repeats_omitted_result_text(self):
        reference = self.export_result()['native_packet']
        original = reading._build_reading_turn
        omitted = []

        def omit_result(*args):
            packet = original(*args)
            for row in packet['reading_turn']['reading_plan']['delivered_now']:
                if row['kind'] == 'result_text':
                    for entry in packet['evidence']:
                        if entry['artifact']['sha256'] == row['artifact']['sha256']:
                            entry.update(content='', encoding='omitted')
                            omitted.append(row['text_sha256'])
            return packet

        prior = None
        with patch.object(reading, '_build_reading_turn', side_effect=omit_result):
            for number in range(1, 8):
                identifier = 'turn-' + str(number)
                self.assign(identifier, reference, prior)
                packet = self.read_packet(identifier)
                self.invoke(identifier, self.build_reading_response(packet, excerpts=[]))
                state = protocol.assignment_state(self.store.snapshot()['records'], self.artifacts, identifier)
                self.assertTrue(state['ready'], state['obligations'])
                if omitted:
                    records = self.store.snapshot()['records']
                    verified = reading.verify_problem_coverage(records, self.artifacts,
                        records['review_assignment'][identifier])
                    credited = {row['text_sha256'] for row in verified['units'] if row['kind'] == 'result_text'}
                    self.assertFalse(set(omitted) & credited)
                    following = 'turn-' + str(number + 1)
                    self.assign(following, reference, identifier)
                    packet = self.read_packet(following)
                    self.assertEqual(reading.find_stage(packet), 'reading')
                    repeated = {row['text_sha256'] for row in packet['reading_turn']['reading_plan']['delivered_now']
                                if row['kind'] == 'result_text'}
                    self.assertTrue(set(omitted) <= repeated)
                    return
                prior = identifier
        self.fail('The result text must be encountered before the final turn')

    def test_legacy_artifact_extensions_keep_their_reference_meaning(self):
        from research_harness.review_delivery import references
        self.export_result()
        reference = dict(self.proof, role='proof', delivery='text')
        self.assertEqual(references(dict(evidence=[reference])), [reference])
        self.assertEqual(list(protocol.collect_references(dict(evidence=[reference]))), [reference])
        packet = dict(scientific_context=dict(openness_minimum=dict(schema_version=2, results=[dict(
            native_attempt_digest=self.native_digest, acceptance_id=self.acceptance['id'])])), evidence=[reference])
        self.assertEqual(references(packet), [reference])
        self.assertEqual(list(protocol.collect_references(packet)), [reference])
