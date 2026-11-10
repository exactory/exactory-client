"""Cross-record handoffs retain observed objections, evidence and posting obligations."""

import importlib.util
import copy
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
import json
from pathlib import Path
import threading
import time
import unittest
from unittest.mock import patch

from research_harness import research_decisions as decisions, review_protocol as protocol
from research_harness.gates import gate_state
from research_harness.errors import ResearchError
from research_harness.evidence import digest
from test_research_problem_delivery import ProblemDeliveryCase
import test_research_problem_objections as objection_fixtures
import test_research_problem_slate as slate_fixtures


class CarryFixture(ProblemDeliveryCase):
    build_slate_output = slate_fixtures.ProblemSlateTests.build_slate_output
    assert_blocked_delivery = objection_fixtures.ProblemObjectionTests.assert_blocked_delivery
    assert_delivered = objection_fixtures.ProblemObjectionTests.assert_delivered

    area = "math.CO"
    source_reference = None
    route_id = 'route'
    settling_evidence = False

    def prepare_problem_evidence(self):
        objection_fixtures.ProblemObjectionTests.prepare_problem_evidence(self)
        if self.settling_evidence:
            from test_research_problem_assessment import build_assessment
            self.supporting = self.artifacts.put(
                b'Theorem C: for every integer n >= 1, B(n) <= n^2. The proof covers the entire domain of candidate C.', 'text/plain')
            assessment = build_assessment(self.selected, 'settling-theorem')
            assessment['items']['priority']['evidence'].append(self.supporting)
            self.mutate(self.load_problem_module().record_assessment, assessment)

    def assign(self, role, reviewer, author='problem-author', identifier=None, **context):
        return self.mutate(protocol.record_assignment, dict(id=identifier or role + '-' + reviewer,
            route_id=self.route_id, dossier_id=None, role=role, reviewer_id=reviewer, author_id=author, context=context))['result']

    def record_candidate(self, identifier="candidate-1", **changes):
        if self.source_reference is not None:
            changes.setdefault("source", {"kind": "own_record", "reference": self.source_reference, "evidence": []})
        return super().record_candidate(identifier, **changes)

    def initialize_problem(self):
        return self.mutate(self.load_problem_module().initialize_problem, {
            "slug": "bounded-sequences", "area": self.area, "field": "Finite bounds",
            "authors": ["problem-author"]})["result"]

    def build_statement(self, identifier="statement-1", **changes):
        return super().build_statement(identifier, **dict({"area": self.area}, **changes))

    def bars(self):
        for reviewer in ("first", "second"):
            self.observe("bar", reviewer)

    def packet(self, assignment):
        return json.loads(self.artifacts.read(assignment["packet"]))

    def assess(self, stage, reviewer, *, identifier=None, objections=(), status="continuing"):
        assignment = self.assign(stage, reviewer, identifier=identifier)
        packet = self.packet(assignment)
        output = self.build_slate_output(["candidate-1"]) if stage == "slate" else self.build_review_output(stage)
        output["objections"] = list(objections)
        output["value"]["objection_findings"] = [{"id": row["id"], "status": status,
            "reason": "The supplied theorem still settles this formulation.", "evidence": [self.supporting]}
            for row in packet.get("material_objections", [])]
        with patch("research_harness.review_transport.send_request", return_value=self.build_provider_response(output)):
            self.mutate(protocol.invoke_assignment, {"id": "attempt-" + assignment["id"],
                "assignment_id": assignment["id"]}, credential="test-only")
        self.mutate(decisions.record_value_review, dict(output, id="review-" + assignment["id"],
            dossier_id=None, assignment_id=assignment["id"]))
        return packet


def legacy_handoff_or_carry_adapter(destination, source, identifier="carry-001"):
    """Base uses its supported text handoff; candidate uses the explicit carry API.

    The legacy candidate has already been recorded through record_candidate, with
    the same statement and own_record source.reference. Never send a new field or
    unknown command to the base: the shared behavioral assertions must expose loss.
    """
    if importlib.util.find_spec("research_harness.problem_objection_carry") is None:
        return None
    from research_harness.problem_objection_carry import carry_objections
    return destination.mutate(carry_objections, {"id": identifier, "source_workspace": str(source.root),
        "source_revision": source.store.revision})


class ProblemObjectionCarryTests(unittest.TestCase):
    @contextmanager
    def source_guard_in_other_thread(self, store):
        entered, release = threading.Event(), threading.Event()

        def hold():
            try:
                with store.guarded_snapshot():
                    entered.set()
                    release.wait()
            finally:
                entered.set()  # Surface an entry failure through the future too.

        with ThreadPoolExecutor(max_workers=1) as writer:
            held = writer.submit(hold)
            try:
                self.assertTrue(entered.wait(5), 'Source guard did not enter')
                if held.done():
                    held.result()
                    self.fail('Source guard exited before the destination mutation')
                yield
            finally:
                release.set()
                held.result(timeout=5)

    def fixture(self, area="math.CO", source=None, bars=True, settling=False):
        case = CarryFixture()
        case.area = area
        case.source_reference = None if source is None else str(source.root)
        case.settling_evidence = settling
        case.setUp()
        self.addCleanup(case.doCleanups)
        case.prepare_problem_evidence()
        if bars:
            case.bars()
        return case

    def source(self):
        source = self.fixture(settling=True)
        objection = {"id": "settled-C", "claim": "The supplied theorem settles candidate C.",
            "reason": "Its quantifiers include every instance of C.", "evidence": [source.supporting],
            "resolution_condition": "Establish an evidenced different statement.", "candidate_ids": ["candidate-1"]}
        source.assess("slate", "first", objections=[objection])
        source.record_slate(review_ids=["review-slate-first"])
        return source

    def test_area_handoff_delivers_source_slate_objection_and_blocks_post(self):
        source = self.source()
        # Same candidate C, now formulated in another class. The record's legacy
        # source.reference is deliberately descriptive, never interpreted as a link.
        destination = self.fixture("math.PR", source=source)
        legacy_handoff_or_carry_adapter(destination, source)
        slate = destination.assess("slate", "first")
        destination.record_slate(review_ids=["review-slate-first"])
        results = [destination.assess("result", reviewer) for reviewer in ("first", "second")]
        for label, packet in [("slate", slate)] + list(zip(("result-first", "result-second"), results)):
            with self.subTest(packet=label):
                carried = packet.get("material_objections", [])
                self.assertTrue(any(row.get("claim") == "The supplied theorem settles candidate C." for row in carried),
                    label + " lost the source objection")
            with self.subTest(evidence=label):
                carried = packet.get("material_objections", [])
                self.assertIn(source.supporting["sha256"], json.dumps(carried), label + " lost its evidence")
        with self.subTest(gate="post"):
            gate = gate_state(destination.store.snapshot()["records"], destination.artifacts, "post")
            self.assertIn("research_objection_unresolved", {row["code"] for row in gate["obligations"]})
        with self.subTest(refusal="post"):
            reviews = decisions.assess_problem_reviews(destination.store.snapshot()['records'], destination.artifacts)
            destination.assert_error('research_objection_unresolved', lambda: destination.mutate(
                destination.load_problem_module().record_decision, dict(id='handoff-post', action='post',
                    review_ids=reviews['review_ids'], bar_review_ids=reviews['bar_review_ids'],
                    reason='The destination otherwise satisfies its posting evidence and reviews.')))

    def test_late_sibling_objection_requires_refresh_and_blocks_post(self):
        source = self.fixture()
        source.record_slate()
        destination = self.fixture("math.PR", source=source)
        legacy_handoff_or_carry_adapter(destination, source)
        destination.assess("slate", "first")
        destination.record_slate(review_ids=["review-slate-first"])
        source.assess("slate", "first", objections=[{"id": "neighbour-settled",
            "claim": "A theorem about the neighbour also settles C.", "reason": "The same reduction applies.",
            "evidence": [source.supporting], "resolution_condition": "Check the reduction.", "candidate_ids": []}])
        destination.assert_error("problem_objections_refresh_required", lambda: destination.assign("result", "first"))
        legacy_handoff_or_carry_adapter(destination, source, "carry-refresh")
        packet = destination.assess("slate", "first", identifier="slate-refreshed")
        self.assertEqual(len(packet["material_objections"]), 1)
        destination.record_slate("slate-refreshed", review_ids=["review-slate-refreshed"])
        for reviewer in ("first", "second"):
            destination.assess("result", reviewer)
        destination.assert_blocked_delivery()

    def test_duplicate_import_and_offline_proof_survive_source_disappearance(self):
        from research_harness.problem_objection_carry import carry_objections, import_set_digest
        from research_harness.record_check import check_integrity
        source, destination = self.source(), self.fixture("math.PR")
        first = legacy_handoff_or_carry_adapter(destination, source)
        records = destination.store.snapshot()["records"]
        before = import_set_digest(records)
        repeated = legacy_handoff_or_carry_adapter(destination, source, "duplicate")
        self.assertEqual(import_set_digest(destination.store.snapshot()["records"]), before)
        self.assertEqual(len(destination.store.snapshot()["records"]["problem_carried_evidence"]), 1)
        source.root.rename(source.root.with_name("moved-source"))
        # Original request replays its receipt without consulting source paths or today's revision.
        self.assertEqual(carry_objections(destination.store, {"id": "carry-001", "source_workspace": str(source.root),
            "source_revision": first["result"]["source_revision"]}, expected_revision=0,
            request_id="task3-" + str(destination.sequence - 1)), first)
        self.assertIn("problem_objection_carries", check_integrity(destination.store))
        destination.assert_error("problem_objection_source_unavailable", lambda: destination.assign("slate", "first"))

    def test_nonempty_carry_requires_current_focused_slate(self):
        from research_harness.problem_candidates import assess_slate
        source, destination = self.source(), self.fixture("math.PR")
        destination.record_slate()
        legacy_handoff_or_carry_adapter(destination, source)
        destination.assert_error("problem_slate_review_unverified", lambda: destination.record_slate("bypass"))
        self.assertIn("problem_slate_stale", {row["code"] for row in assess_slate(destination.store.snapshot()["records"], destination.artifacts)})

    def test_empty_slate_reviews_after_carry_name_the_import_set(self):
        from research_harness.problem_objection_carry import import_set_digest
        source, destination = self.source(), self.fixture('math.PR')
        legacy_handoff_or_carry_adapter(destination, source)
        before = destination.store.revision
        with self.assertRaises(ResearchError) as caught:
            destination.record_slate('missing-focused-review', review_ids=[])
        self.assertEqual(caught.exception.code, 'problem_slate_review_unverified')
        self.assertEqual(caught.exception.details, dict(import_set_digest=import_set_digest(destination.store.snapshot()['records'])))
        self.assertEqual(destination.store.revision, before)

    def test_carry_stale_slate_states_why_it_is_stale(self):
        from research_harness.problem_candidates import assess_slate
        source, destination = self.source(), self.fixture('math.PR')
        destination.record_slate()
        legacy_handoff_or_carry_adapter(destination, source)
        [row] = assess_slate(destination.store.snapshot()['records'], destination.artifacts)
        self.assertEqual(row['code'], 'problem_slate_stale')
        self.assertEqual(row['slate_id'], 'slate-1')
        self.assertEqual(row.get('reason'), 'problem_objection_carry_changed')
        self.assertNotIn('missing', row)
        self.assertNotIn('extra', row)

    def test_reachable_carry_check_leaves_proof_artifacts_and_store_unchanged(self):
        from test_research_check_mode import CheckCommandMixin
        for sealed in (False, True):
            with self.subTest(sealed=sealed):
                source, destination = self.source(), self.fixture('math.PR')
                if sealed:
                    reference = source.artifacts.put_sealed(b'Unobserved retained completion.', 'text/plain')
                    source.put('fixture_completion', 'held', dict(artifact=reference))
                destination.invoke_arguments = lambda arguments: CheckCommandMixin.invoke_arguments(destination, arguments)
                payload = dict(id='checked-carry', source_workspace=str(source.root), source_revision=source.store.revision)
                revision = destination.store.revision
                before = CheckCommandMixin.read_workspace(destination)
                checked = CheckCommandMixin.invoke_command(destination, 'problem-objections-carry', payload)
                self.assertEqual(CheckCommandMixin.read_workspace(destination), before)
                self.assertEqual(destination.store.revision, revision)
                CheckCommandMixin.assert_accepted_check(destination, checked, revision)
                written = CheckCommandMixin.invoke_command(destination, 'problem-objections-carry', payload, check=False)
                self.assertEqual(written[0], 0, written[2])
                carry = json.loads(written[1])['result']
                self.assertTrue(carry['proof']['artifacts'])
                self.assertTrue(destination.artifacts.read(carry['proof']['database']))
                self.assertEqual(destination.store.revision, revision + 1)

    def test_status_lists_carry_origins_and_workspaces_only_when_present(self):
        from research_harness.cli import status_report
        source, destination = self.source(), self.fixture('math.PR')
        self.assertNotIn('problem_objection_carries', status_report(destination.store))
        first = legacy_handoff_or_carry_adapter(destination, source, 'z-first')['result']
        second = legacy_handoff_or_carry_adapter(destination, source, 'a-duplicate')['result']
        expected = [{key: row[key] for key in ('id', 'source_workspace', 'source_origin', 'source_revision')}
                    for row in (second, first)]
        self.assertEqual(status_report(destination.store).get('problem_objection_carries'), expected)
        source.root.rename(source.root.with_name('unavailable-source-with-retained-origin'))
        self.assertEqual(status_report(destination.store).get('problem_objection_carries'), expected)

    def test_two_evidenced_destination_resolutions_clear_but_one_does_not(self):
        for second_status in ('contested', 'resolved'):
            with self.subTest(second_status=second_status):
                source, destination = self.source(), self.fixture("math.PR")
                legacy_handoff_or_carry_adapter(destination, source)
                destination.assess("slate", "first")
                destination.record_slate(review_ids=["review-slate-first"])
                destination.assess("result", "first", status="resolved")
                reviews = decisions.assess_problem_reviews(destination.store.snapshot()['records'], destination.artifacts)
                self.assertIn('research_objection_unresolved', {row['code'] for row in reviews['obligations']})
                destination.assess("result", "second", status=second_status)
                if second_status == 'resolved':
                    destination.assert_delivered()
                else:
                    destination.assert_blocked_delivery()

    def test_unrecorded_source_final_refuses_import_without_mutating_destination(self):
        from research_harness.problem_objection_carry import carry_objections
        source, destination = self.fixture(), self.fixture("math.PR")
        source.observe("slate", "first", source.build_slate_output(["candidate-1"]), record=False)
        before = destination.store.revision
        destination.assert_error("problem_objections_refresh_required", lambda: destination.mutate(carry_objections,
            {"id": "pending", "source_workspace": str(source.root), "source_revision": source.store.revision}))
        self.assertEqual(destination.store.revision, before)

    def test_source_result_pending_and_unrecorded_final_block_ready_destination(self):
        from research_harness.problem_objection_carry import carry_objections, require_fresh_sources
        for observed in (False, True):
            with self.subTest(observed=observed):
                source, destination = self.source(), self.fixture("math.PR")
                legacy_handoff_or_carry_adapter(destination, source)
                self.imported_pair(destination)
                destination.record_post_decision('ready-before-source-work')
                records = destination.store.snapshot()['records']
                self.assertTrue(gate_state(records, destination.artifacts, 'post')['ready'])
                assignment = source.assign('result', 'first')
                output = source.build_review_output('result')
                output['value']['objection_findings'] = [dict(id='settled-C', status='continuing',
                    reason='The supplied evidence adds a continuing refinement.', evidence=[source.supporting])]
                if observed:
                    with patch('research_harness.review_transport.send_request', return_value=source.build_provider_response(output)):
                        source.mutate(protocol.invoke_assignment, dict(id='attempt-result-first',
                            assignment_id=assignment['id']), credential='test-only')
                    self.assertTrue(protocol.assignment_state(source.store.snapshot()['records'], source.artifacts, assignment['id'])['ready'])
                with self.subTest(boundary='gate'):
                    codes = {row['code'] for row in gate_state(records, destination.artifacts, 'post')['obligations']}
                    self.assertIn('problem_objections_refresh_required', codes)
                for boundary, action in (
                    ('decision', lambda: destination.record_post_decision('decision-after-source-work')),
                    ('review', lambda: destination.assign('result', 'third')),
                    ('carry', lambda: legacy_handoff_or_carry_adapter(destination, source, 'pending'))):
                    with self.subTest(boundary=boundary):
                        destination.assert_error('problem_objections_refresh_required', action)
                if not observed:
                    continue
                source.mutate(decisions.record_value_review, dict(output, id='review-result-first',
                    dossier_id=None, assignment_id=assignment['id']))
                destination.assert_error('problem_objections_refresh_required', lambda: require_fresh_sources(
                    destination.store.snapshot()['records'], destination.artifacts))
                legacy_handoff_or_carry_adapter(destination, source, 'recorded-refinement')
                destination.assess('slate', 'first', identifier='slate-refreshed')
                destination.record_slate('slate-refreshed', review_ids=['review-slate-refreshed'])
                for reviewer in ('first', 'second'):
                    destination.assess('result', reviewer, identifier='result-refreshed-' + reviewer, status='resolved')
                destination.assert_delivered()

    def imported_pair(self, destination, *, status="resolved"):
        destination.assess("slate", "first")
        destination.record_slate(review_ids=["review-slate-first"])
        for reviewer in ("first", "second"):
            destination.assess("result", reviewer, status=status)

    def test_two_evidenced_resolutions_and_assessed_irrelevance_allow_post(self):
        source, destination = self.source(), self.fixture("math.PR")
        legacy_handoff_or_carry_adapter(destination, source)
        self.imported_pair(destination)
        destination.assert_delivered()

    def test_diamond_import_reopens_resolution_for_complete_version_set(self):
        from research_harness.problem_objection_carry import import_set_digest
        source = self.source()
        left, right = self.fixture("math.PR"), self.fixture("math.NT")
        for sibling in (left, right):
            legacy_handoff_or_carry_adapter(sibling, source)
            sibling.assess("slate", "first")
            sibling.record_slate(review_ids=["review-slate-first"])
        left.assess("result", "first")
        right.assess("result", "first", status="contested")
        # Distinct local refinements have the same local revision ranges.
        destination, reverse = self.fixture("math.LO"), self.fixture("math.DS")
        legacy_handoff_or_carry_adapter(destination, left, "left")
        self.imported_pair(destination)
        before = protocol.collect_result_objections(destination.store.snapshot()["records"], destination.artifacts,
            protocol.build_problem_subject(destination.store.snapshot()["records"]),
            destination.load_problem_module().compute_problem_evidence_digest(destination.store.snapshot()["records"], destination.artifacts), [])
        legacy_handoff_or_carry_adapter(destination, right, "right")
        for sibling, identifier in ((right, "right"), (left, "left")):
            legacy_handoff_or_carry_adapter(reverse, sibling, identifier)
        current = protocol.collect_result_objections(destination.store.snapshot()["records"], destination.artifacts,
            protocol.build_problem_subject(destination.store.snapshot()["records"]),
            destination.load_problem_module().compute_problem_evidence_digest(destination.store.snapshot()["records"], destination.artifacts), [])
        self.assertEqual(len(current), 1)
        self.assertGreater(len(current[0]["material_members"]), len(before[0]["material_members"]))
        self.assertNotEqual(current[0]["material_version"], before[0]["material_version"])
        self.assertEqual(import_set_digest(destination.store.snapshot()["records"]), import_set_digest(reverse.store.snapshot()["records"]))
        state = gate_state(destination.store.snapshot()['records'], destination.artifacts, 'post')
        self.assertIn('research_objection_unresolved', {row['code'] for row in state['obligations']})
        destination.assert_error('problem_minimum_unmet', lambda: destination.record_post_decision())

    def test_return_to_origin_merges_with_native_identity(self):
        source, sibling = self.source(), self.fixture("math.PR")
        legacy_handoff_or_carry_adapter(sibling, source)
        sibling.assess("slate", "first")
        sibling.record_slate(review_ids=["review-slate-first"])
        sibling.assess("result", "first")
        legacy_handoff_or_carry_adapter(source, sibling)
        packet = protocol.build_packet(source.store.snapshot()["records"], source.artifacts, None, "result", "first")
        self.assertEqual([row["id"] for row in packet["material_objections"]], ["settled-C"])
        self.assertEqual(len({row["key"] for row in packet["material_objections"][0]["material_members"]}),
                         len(packet["material_objections"][0]["material_members"]))

    def test_scope_includes_whole_slate_neighbour_and_bar_premise_but_bars_stay_blind(self):
        source = self.fixture(bars=False)
        bar = source.build_review_output('bar')
        bar['objections'] = [dict(id='premise', claim='The comparator premise is already settled.',
            reason='The theorem includes this premise.', evidence=[source.supporting], resolution_condition='Check its hypotheses.')]
        source.observe('bar', 'first', bar)
        source.observe('bar', 'second')
        # A new slate may object to a neighbour, with no selected-candidate filter.
        source.record_candidate("neighbour", statement="Does the neighbouring reduction hold?")
        output = source.build_slate_output(["candidate-1", "neighbour"])
        output["objections"] = [{"id": identifier, "claim": claim, "candidate_ids": candidate_ids,
            "reason": "The supplied theorem concerns this premise.", "evidence": [source.supporting],
            "resolution_condition": "Establish a distinct premise."} for identifier, claim, candidate_ids in (
                ("whole", "The whole slate uses a settled premise.", []),
                ("neighbour", "The neighbour has already been settled.", ["neighbour"]))]
        source.observe("slate", "first", output)
        source.record_slate(review_ids=["review-slate-first"], deferred=[{"candidate_id": "neighbour", "evidence_needed": "A distinct premise."}])
        destination = self.fixture("math.PR", bars=False)
        legacy_handoff_or_carry_adapter(destination, source)
        destination.bars()
        records = destination.store.snapshot()["records"]
        for reviewer in ("first", "second"):
            packet = destination.packet(records["review_assignment"]["bar-" + reviewer])
            self.assertNotIn("problem_objection_carry", packet)
            self.assertNotIn("material_objections", packet)
        packet = destination.assess("slate", "first")
        self.assertEqual(len(packet["material_objections"]), 3)

    def test_missing_extra_duplicate_findings_and_identity_reuse_are_refused(self):
        source, destination = self.source(), self.fixture("math.PR")
        legacy_handoff_or_carry_adapter(destination, source)
        assignment = destination.assign("slate", "first")
        row = destination.packet(assignment)["material_objections"][0]
        finding = dict(id=row['id'], status='continuing', reason='The theorem applies.', evidence=[destination.supporting])
        for findings in ([], [dict(finding, id='extra')], [finding, finding]):
            with self.subTest(findings=findings):
                output = destination.build_slate_output(['candidate-1'])
                output['value']['objection_findings'] = findings
                with self.assertRaises(ResearchError) as caught:
                    decisions.validate_review_contract(destination.store.snapshot()['records'], destination.artifacts, assignment, output)
                self.assertIn(caught.exception.code, ('review_objection_history_incomplete', 'invalid_strategy', 'invalid_value_review'))
        payload = dict(destination.build_slate_output(['candidate-1']), id='reuse', assignment_id=assignment['id'])
        payload['objections'] = [dict(id=row['id'], claim='An unrelated claim', reason='Different', evidence=[destination.supporting], resolution_condition='Different')]
        destination.assert_error('research_objection_identity_conflict', lambda: decisions._assign_objection_identities(
            destination.store.snapshot()['records'], destination.artifacts, payload, True))

    def test_immutable_proof_rejects_altered_objection_review_identity_and_missing_artifact(self):
        from research_harness.problem_objection_carry import carry_objections
        for change in ('objection', 'review_identity', 'artifact'):
            with self.subTest(change=change):
                source, destination = self.source(), self.fixture('math.PR')
                if change == 'artifact':
                    (source.root / source.supporting['path']).unlink()
                else:
                    review = copy.deepcopy(source.store.snapshot()['records']['value_review']['review-slate-first'])
                    if change == 'objection':
                        review['payload']['objections'][0]['claim'] = 'Author replacement text'
                    else:
                        review['id'] = 'false-review'
                    source.put('value_review', 'review-slate-first', review)
                before = destination.store.revision
                with self.assertRaises(ResearchError) as caught:
                    destination.mutate(carry_objections, dict(id='invalid', source_workspace=str(source.root), source_revision=source.store.revision))
                self.assertIn(caught.exception.code, ('problem_objection_provenance_invalid', 'artifact_missing'))
                self.assertEqual(destination.store.revision, before)

    def test_payload_refusals_and_receipt_first_revision_checks(self):
        from research_harness.problem_objection_carry import carry_objections
        source, destination = self.source(), self.fixture('math.PR')
        payload = dict(id='carry', source_workspace=str(source.root), source_revision=source.store.revision)
        for change, code in ((dict(source_workspace=str(destination.root)), 'problem_objection_self_import'),
            (dict(source_revision=0), 'problem_objection_source_revision_mismatch'),
            (dict(objection_ids=['settled-C']), 'invalid_input'), (dict(objections=[]), 'invalid_input')):
            destination.assert_error(code, lambda change=change: destination.mutate(carry_objections, dict(payload, **change)))
        alias = destination.root.with_name('alias')
        alias.symlink_to(destination.root, target_is_directory=True)
        destination.assert_error('problem_objection_self_import', lambda: destination.mutate(carry_objections, dict(payload, source_workspace=str(alias))))
        destination.assert_error('stale_revision', lambda: carry_objections(destination.store, payload, expected_revision=0, request_id='stale'))
        committed = carry_objections(destination.store, payload, expected_revision=destination.store.revision,
                                     request_id='successful-carry')
        destination.put('local_note', 'after-carry', dict(text='Advance the destination after the committed request.'))
        revision = destination.store.revision
        source.root.rename(source.root.with_name('unavailable-for-replay'))
        from research_harness.storage import Store
        restarted = Store(destination.root)
        replayed = carry_objections(restarted, payload, expected_revision=0, request_id='successful-carry')
        self.assertEqual(replayed, committed)
        self.assertEqual(restarted.revision, revision)
        destination.assert_error('request_id_conflict', lambda: carry_objections(restarted,
            dict(payload, source_revision=0), expected_revision=0, request_id='successful-carry'))

    def test_old_and_new_assignment_replay_after_later_import_restart_and_source_loss(self):
        from research_harness.storage import Store
        source, destination = self.source(), self.fixture('math.PR')
        old = destination.assess('slate', 'first')
        old_assignment = destination.store.snapshot()['records']['review_assignment']['slate-first']
        old_request = protocol.build_assignment_request(destination.store.snapshot()['records'], destination.artifacts, old_assignment)
        legacy_handoff_or_carry_adapter(destination, source)
        destination.assess('slate', 'first', identifier='carried-slate')
        new_assignment = destination.store.snapshot()['records']['review_assignment']['carried-slate']
        new_request = protocol.build_assignment_request(destination.store.snapshot()['records'], destination.artifacts, new_assignment)
        legacy_handoff_or_carry_adapter(destination, source, 'duplicate')
        source.root.rename(source.root.with_name('gone'))
        destination.store = Store(destination.root)
        for assignment, expected in ((old_assignment, old_request), (new_assignment, new_request)):
            self.assertEqual(protocol.build_assignment_request(destination.store.snapshot()['records'], destination.artifacts, assignment), expected)
            self.assertTrue(protocol._prompt_matches(assignment))
            self.assertTrue(protocol.assignment_state(destination.store.snapshot()['records'], destination.artifacts, assignment['id'])['ready'])
        self.assertNotIn('problem_objection_carry', old_assignment['prompt']['parameters'])
        self.assertIn('problem_objection_carry', new_assignment['prompt']['parameters'])

    def test_source_resolution_is_context_and_cannot_clear_destination(self):
        source, destination = self.source(), self.fixture('math.PR')
        # The native source's two result reviewers resolve its objection.
        for reviewer in ('first', 'second'):
            assignment = source.assign('result', reviewer)
            output = source.build_review_output('result')
            output['value']['objection_findings'] = [dict(id='settled-C', status='resolved',
                reason='The supplied narrower domain is an evidenced relevant difference.', evidence=[source.supporting])]
            self.observe_assignment(source, assignment, output)
        legacy_handoff_or_carry_adapter(destination, source)
        packet = destination.assess('slate', 'first')
        self.assertIn('narrower domain', json.dumps(packet['material_objections']))
        self.imported_pair_after_slate(destination)
        destination.assert_blocked_delivery()

    def imported_pair_after_slate(self, destination):
        destination.record_slate(review_ids=['review-slate-first'])
        for reviewer in ('first', 'second'):
            destination.assess('result', reviewer)

    def observe_assignment(self, case, assignment, output):
        with patch('research_harness.review_transport.send_request', return_value=case.build_provider_response(output)):
            case.mutate(protocol.invoke_assignment, dict(id='attempt-' + assignment['id'], assignment_id=assignment['id']), credential='test-only')
        return case.mutate(decisions.record_value_review, dict(output, id='review-' + assignment['id'], dossier_id=None,
            assignment_id=assignment['id']))['result']

    def test_focused_destination_adjudication_can_clear_imported_identity(self):
        source, destination = self.source(), self.fixture('math.PR')
        legacy_handoff_or_carry_adapter(destination, source)
        self.imported_pair(destination, status='contested')
        objections = decisions.collect_problem_post_objections(destination.store.snapshot()['records'], destination.artifacts, [])
        assignment = destination.assign('adjudicator', 'third', identifier='focus',
            review_ids=['review-bar-first', 'review-bar-second'], objection_id=objections[0]['id'])
        wrapper = next(row for row in destination.packet(assignment)['version_evidence'] if row.get('record_kind') == 'problem_carried_evidence')
        output = dict(objection_id=objections[0]['id'], disposition='not_binding',
            reason='The exact local statement has an evidenced different domain.',
            evidence=[{key: wrapper[key] for key in ('kind', 'record_kind', 'id', 'digest')}], correction_admissible=None)
        with patch('research_harness.review_transport.send_request', return_value=destination.build_provider_response(output)):
            destination.mutate(protocol.invoke_assignment, dict(id='focus-attempt', assignment_id='focus'), credential='test-only')
        destination.mutate(protocol.record_adjudication, dict(output, id='adjudication', assignment_id='focus'))
        destination.assert_delivered()

    def test_transitive_freshness_and_reciprocal_links_and_bounded_busy(self):
        from research_harness.problem_objection_carry import require_fresh_sources
        source, sibling, destination = self.source(), self.fixture('math.PR'), self.fixture('math.NT')
        legacy_handoff_or_carry_adapter(sibling, source)
        legacy_handoff_or_carry_adapter(source, sibling, 'reciprocal')
        # Reciprocal imports add neither native reviews nor material versions.
        require_fresh_sources(sibling.store.snapshot()['records'], sibling.artifacts)
        legacy_handoff_or_carry_adapter(destination, sibling)
        with self.source_guard_in_other_thread(source.store):
            started = time.monotonic()
            destination.assert_error('store_busy', lambda: destination.assign('slate', 'first'))
            self.assertLess(time.monotonic() - started, 6)
        # The bounded failure releases every other guard.
        require_fresh_sources(destination.store.snapshot()['records'], destination.artifacts)
        source.assess('slate', 'first', identifier='late-slate')
        destination.assert_error('problem_objections_refresh_required', lambda: destination.assign('slate', 'first'))
        codes = {row['code'] for row in gate_state(destination.store.snapshot()['records'], destination.artifacts, 'post')['obligations']}
        self.assertIn('problem_objections_refresh_required', codes)

    def test_source_reentry_remains_a_wrapped_decision(self):
        from research_harness.problem_objection_carry import carry_objections
        from research_harness.stop_classes import classify_stop
        source, destination = self.fixture(bars=False), self.fixture('math.PR', bars=False)
        payload = dict(id='reentry', source_workspace=str(source.root), source_revision=source.store.revision)
        before = destination.store.snapshot()
        with source.store.guarded_snapshot():
            with self.assertRaises(ResearchError) as caught:
                destination.mutate(carry_objections, payload)
        self.assertEqual(caught.exception.code, 'problem_objection_source_unavailable')
        self.assertEqual(caught.exception.details, dict(source_workspace=str(source.root.resolve()), cause='store_reentrant_access'))
        self.assertEqual(classify_stop(caught.exception.code), 'decision')
        self.assertEqual(destination.store.snapshot(), before)
        self.assertEqual(source.store.revision, payload['source_revision'])

    def assert_source_refusal_without_destination_mutation(self, destination, source, code, details, *, source_revision=None):
        from research_harness.output_views import render_all
        from test_research_check_mode import CheckCommandMixin
        destination.invoke_arguments = lambda arguments: CheckCommandMixin.invoke_arguments(destination, arguments)
        render_all(destination.store)
        before = destination.store.snapshot()
        before_files = CheckCommandMixin.read_workspace(destination)
        for check in (False, True):
            with self.subTest(check=check):
                try:
                    response = CheckCommandMixin.invoke_command(destination, 'problem-objections-carry',
                        dict(id='unavailable-source', source_workspace=str(source.root),
                             source_revision=source.store.revision if source_revision is None else source_revision),
                        check=check, request='unavailable-source-request')
                finally:
                    self.assertEqual(destination.store.snapshot(), before)
                    self.assertEqual(CheckCommandMixin.read_workspace(destination), before_files)
                self.assertEqual(response[0], 1, response[1:])
                error = json.loads(response[2])['error']
                self.assertEqual(error['code'], code)
                self.assertEqual(error['details'], details)

    def test_replaced_transitive_source_refuses_with_live_freshness_details(self):
        from research_harness.problem_objection_carry import require_fresh_sources
        from research_harness.record_check import copy_snapshot
        for replacement_kind in ('new-origin', 'destination-origin', 'guarded-source-origin'):
            with self.subTest(replacement_kind=replacement_kind):
                upstream, source, destination = self.source(), self.fixture('math.PR'), self.fixture('math.NT')
                carry = legacy_handoff_or_carry_adapter(source, upstream)['result']
                upstream.root.rename(upstream.root.with_name('original-upstream'))
                if replacement_kind == 'new-origin':
                    replacement = self.fixture('math.LO')
                    copy_snapshot(replacement.root, upstream.root)
                else:
                    copy_snapshot(destination.root if replacement_kind == 'destination-origin' else source.root, upstream.root)
                with self.assertRaises(ResearchError) as caught:
                    require_fresh_sources(source.store.snapshot()['records'], source.artifacts)
                details = dict(source_workspace=carry['source_workspace'])
                self.assertEqual(caught.exception.code, 'problem_objection_source_unavailable')
                self.assertEqual(caught.exception.details, details)
                self.assert_source_refusal_without_destination_mutation(destination, source,
                    'problem_objection_source_unavailable', details)

    def test_missing_or_damaged_transitive_source_has_typed_open_failure(self):
        import sqlite3
        from research_harness.problem_objection_carry import require_fresh_sources
        for damage, cause in (('moved', 'store_missing'), ('database-header', 'corrupt_state'),
                              ('missing-revision', 'corrupt_state'), ('symlink-loop', 'unsafe_path')):
            with self.subTest(damage=damage):
                upstream, source, destination = self.source(), self.fixture('math.PR'), self.fixture('math.NT')
                carry = legacy_handoff_or_carry_adapter(source, upstream)['result']
                if damage in ('moved', 'symlink-loop'):
                    upstream.root.rename(upstream.root.with_name('original-upstream'))
                    if damage == 'symlink-loop':
                        upstream.root.symlink_to(upstream.root, target_is_directory=True)
                elif damage == 'database-header':
                    (upstream.root / '.exactory/research.sqlite3').write_bytes(b'Not a SQLite store.')
                else:
                    with sqlite3.connect(upstream.root / '.exactory/research.sqlite3') as connection:
                        connection.execute('DELETE FROM metadata')
                details = dict(source_workspace=carry['source_workspace'], cause=cause)
                self.assert_source_refusal_without_destination_mutation(destination, source,
                    'problem_objection_source_unavailable', details)
                with self.assertRaises(ResearchError) as caught:
                    require_fresh_sources(source.store.snapshot()['records'], source.artifacts)
                self.assertEqual(caught.exception.code, 'problem_objection_source_unavailable')
                self.assertEqual(caught.exception.details, details)

    def test_direct_source_symlink_loop_has_typed_open_failure(self):
        source, destination = self.source(), self.fixture('math.PR')
        revision = source.store.revision
        source.root.rename(source.root.with_name('original-source'))
        source.root.symlink_to(source.root, target_is_directory=True)
        self.assert_source_refusal_without_destination_mutation(destination, source,
            'problem_objection_source_unavailable', dict(source_workspace=str(source.root), cause='unsafe_path'),
            source_revision=revision)

    def test_direct_source_revision_mismatch_has_typed_details(self):
        source, destination = self.source(), self.fixture('math.PR')
        self.assert_source_refusal_without_destination_mutation(destination, source,
            'problem_objection_source_revision_mismatch',
            dict(source_revision=source.store.revision, expected_source_revision=0), source_revision=0)

    def test_destination_revision_disappearing_after_open_has_typed_refusal(self):
        import sqlite3
        from research_harness import problem_objection_carry as carry
        from research_harness.storage import Store
        source, destination = self.source(), self.fixture('math.PR')
        revision = destination.store.revision
        database = destination.root / '.exactory/research.sqlite3'
        def committed_rows():
            with sqlite3.connect(database) as connection:
                return {table: connection.execute('SELECT * FROM ' + table + ' ORDER BY 1, 2').fetchall()
                        for table in ('records', 'events', 'receipts')}
        before = committed_rows()
        def opened_store(path):
            store = Store(path)
            if store.root == destination.store.root:
                # A real SQLite change in the gap after constructor validation
                # and before the short destination revision read, not a fake row.
                with sqlite3.connect(database) as connection:
                    connection.execute('DELETE FROM metadata')
            return store
        with patch.object(carry, 'Store', side_effect=opened_store):
            with self.assertRaises(ResearchError) as caught:
                carry.carry_objections(destination.store, dict(id='damaged-destination',
                    source_workspace=str(source.root), source_revision=source.store.revision),
                    expected_revision=revision, request_id='damaged-destination-request')
        self.assertEqual(caught.exception.code, 'corrupt_state')
        self.assertEqual(committed_rows(), before)

    def test_source_continuation_and_context_repair_deliver_imports(self):
        source, destination = self.source(), self.fixture('math.PR')
        legacy_handoff_or_carry_adapter(destination, source)
        request = destination.build_slate_output(['candidate-1'])
        request['value']['status'] = 'unresolved'
        request['source_requests'] = [dict(id='page', kind='inventory_page', page=0)]
        destination.observe('slate', 'first', request, record=False, identifier='request')
        continued = destination.assign('slate', 'first', identifier='continued', prior_assignment_id='request')
        packet = destination.packet(continued)
        self.assertEqual(len(packet['material_objections']), 1)
        self.assertIn('problem_objection_carry', continued['prompt']['parameters'])
        destination.mutate(protocol.record_context_event, dict(id='exposure', assignment_id='continued', kind='contamination',
            source='author_history', reason='Author advocacy entered the reviewer context.', evidence=[destination.supporting]))
        def probe(route, request, credential):
            packet = json.loads(request['input'][0]['content'].removeprefix('The packet below is JSON.\n'))
            return destination.build_provider_response(dict(allowed_control=packet['allowed_control'], evidence_control=packet['evidence_control'], excluded_controls=[]))
        with patch('research_harness.review_transport.send_request', side_effect=probe):
            destination.mutate(protocol.probe_route, dict(id='repair-probe', route_id='route'), credential='test-only')
        repaired = destination.assign('slate', 'replacement', identifier='repaired', context_repair=dict(assignment_id='continued',
            event_ids=['exposure'], probe_id='repair-probe', reason='Reassess the same science in a clean context.', evidence=[destination.supporting]))
        self.assertEqual(len(destination.packet(repaired)['material_objections']), 1)
        self.assertTrue(protocol._prompt_matches(repaired))

    def test_offline_proof_detects_changed_manifest_even_for_duplicate_proof(self):
        from research_harness.problem_objection_provenance import verify_carries
        source, destination = self.source(), self.fixture('math.PR')
        legacy_handoff_or_carry_adapter(destination, source)
        duplicate = legacy_handoff_or_carry_adapter(destination, source, 'duplicate')['result']
        altered = copy.deepcopy(duplicate)
        altered['members'][0]['original_objection']['claim'] = 'Replacement author text'
        destination.put('problem_objection_carry', 'duplicate', altered)
        destination.assert_error('problem_objection_provenance_invalid', lambda: verify_carries(destination.store.snapshot()['records'], destination.artifacts))

    def test_new_empty_transitive_link_and_post_decision_require_refresh(self):
        from research_harness.problem_objection_carry import require_fresh_sources
        source, sibling, destination = self.fixture(), self.fixture('math.PR'), self.fixture('math.NT')
        legacy_handoff_or_carry_adapter(destination, sibling)
        legacy_handoff_or_carry_adapter(sibling, source)
        destination.assert_error('problem_objections_refresh_required', lambda: require_fresh_sources(destination.store.snapshot()['records'], destination.artifacts))
        destination.assert_error('problem_objections_refresh_required', lambda: destination.record_post_decision())

    def test_empty_import_does_not_allow_slate_resampling(self):
        source, destination = self.fixture(), self.fixture('math.PR')
        legacy_handoff_or_carry_adapter(destination, source)
        destination.assess('slate', 'first')
        destination.assert_error('review_resampling_forbidden', lambda: destination.assign('slate', 'first', identifier='another'))

    def test_check_mode_validates_carry_without_publishing_records_or_artifacts(self):
        from research_harness.check_mode import check_mutation
        from research_harness.problem_objection_carry import carry_objections
        source, destination = self.source(), self.fixture('math.PR')
        payload = dict(id='dry', source_workspace=str(source.root), source_revision=source.store.revision)
        before = destination.store.revision
        files = sorted(str(path) for path in destination.root.rglob('*'))
        checked = check_mutation(destination.store, carry_objections, payload, expected_revision=before,
            request_id='dry', bounded_payload=payload)
        self.assertEqual(checked['check'], 'accepted')
        self.assertEqual(destination.store.revision, before)
        self.assertEqual(sorted(str(path) for path in destination.root.rglob('*')), files)

    def test_replaced_failed_bar_does_not_leave_source_permanently_pending(self):
        source, destination = self.fixture(bars=False), self.fixture('math.PR')
        assignment = source.assign('bar', 'first', **source.deliver_supporting('first'))
        with patch('research_harness.review_transport.send_request', side_effect=TimeoutError('fixture timeout')):
            source.mutate(protocol.invoke_assignment, dict(id='failed', assignment_id=assignment['id']), credential='test-only')
        source.observe('bar', 'replacement')
        source.observe('bar', 'second')
        result = legacy_handoff_or_carry_adapter(destination, source)
        self.assertEqual(result['result']['source_cut']['pending'], [])

    def test_returned_complete_set_replaces_historical_bar_delivery(self):
        from research_harness.problem_objection_carry import deliver_imports
        source, sibling = self.fixture(bars=False), self.fixture('math.PR')
        output = source.build_review_output('bar')
        output['objections'] = [dict(id='premise', claim='The premise is settled.', reason='The theorem covers this premise.',
            evidence=[source.supporting], resolution_condition='Establish different premises.')]
        source.observe('bar', 'first', output)
        source.observe('bar', 'second')
        legacy_handoff_or_carry_adapter(sibling, source)
        sibling.assess('slate', 'first')
        legacy_handoff_or_carry_adapter(source, sibling)
        source.mutate(protocol.record_context_event, dict(id='exposure', assignment_id='bar-first', kind='contamination',
            source='author_history', reason='Author advocacy entered the context.', evidence=[source.supporting]))
        records = source.store.snapshot()['records']
        historical = protocol.collect_historical_bar_objections(records, source.artifacts)
        self.assertEqual(len(historical), 1)
        packet = dict(role='slate', subject=protocol.build_problem_subject(records, 'slate'), material_objections=historical)
        deliver_imports(records, source.artifacts, packet)
        self.assertEqual([row['id'] for row in packet['material_objections']], ['premise'])
        self.assertGreater(len(packet['material_objections'][0]['material_members']), 1)

    def test_source_adjudication_is_delivered_as_context_without_clearance(self):
        source, destination = self.source(), self.fixture('math.PR')
        assignment = source.assign('adjudicator', 'third', identifier='source-focus',
            review_ids=['review-bar-first', 'review-bar-second'], objection_id='settled-C')
        output = dict(objection_id='settled-C', disposition='not_binding', reason='The source domain has an evidenced exception.',
            evidence=[source.supporting], correction_admissible=None)
        with patch('research_harness.review_transport.send_request', return_value=source.build_provider_response(output)):
            source.mutate(protocol.invoke_assignment, dict(id='source-focus-attempt', assignment_id=assignment['id']), credential='test-only')
        source.mutate(protocol.record_adjudication, dict(output, id='source-disposition', assignment_id=assignment['id']))
        legacy_handoff_or_carry_adapter(destination, source)
        packet = destination.assess('slate', 'first')
        self.assertIn('source domain has an evidenced exception', json.dumps(packet['material_objections']))
        self.imported_pair_after_slate(destination)
        destination.assert_blocked_delivery()

    def test_unauthenticated_source_attempt_cannot_supply_a_carry(self):
        source, destination = self.source(), self.fixture('math.PR')
        attempt = copy.deepcopy(source.store.snapshot()['records']['review_attempt']['attempt-slate-first'])
        attempt['origin'] = 'imported'
        source.put('review_attempt', attempt['id'], attempt)
        destination.assert_error('problem_objection_provenance_invalid', lambda: legacy_handoff_or_carry_adapter(destination, source))

    def test_distinct_source_ids_with_identical_content_are_both_carried(self):
        source, destination = self.fixture(), self.fixture('math.PR')
        objection = dict(id='first-id', claim='The premise is settled.', reason='The theorem covers every case.',
            evidence=[source.supporting], resolution_condition='Establish a different premise.', candidate_ids=[])
        source.assess('slate', 'first', objections=[objection, dict(objection, id='second-id')])
        source.record_slate(review_ids=['review-slate-first'])
        result = legacy_handoff_or_carry_adapter(destination, source)
        self.assertEqual({row['origin_id'] for row in result['result']['members']}, {'first-id', 'second-id'})
        self.assertEqual(len(destination.assess('slate', 'first')['material_objections']), 2)

    def test_proof_retention_does_not_unseal_unrelated_completion_bytes(self):
        from research_harness.record_check import check_integrity
        source, destination = self.source(), self.fixture('math.PR')
        sealed = source.artifacts.put_sealed(b'Unobserved completion-check output.', 'text/plain')
        source.put('fixture_completion', 'held', dict(artifact=sealed))
        result = legacy_handoff_or_carry_adapter(destination, source)['result']
        retained = next(row['retained'] for row in result['proof']['artifacts'] if row['original'] == sealed)
        destination.assert_error('artifact_sealed', lambda: destination.artifacts.read(retained))
        self.assertEqual(check_integrity(destination.store)['problem_objection_carries'], 1)

    def test_new_import_retains_destination_result_refinement_at_same_evidence_digest(self):
        source, destination = self.source(), self.fixture('math.PR')
        legacy_handoff_or_carry_adapter(destination, source)
        destination.assess('slate', 'first')
        destination.record_slate(review_ids=['review-slate-first'])
        assignment = destination.assign('result', 'first')
        identifier = destination.packet(assignment)['material_objections'][0]['id']
        output = destination.build_review_output('result')
        output['value']['objection_findings'] = [dict(id=identifier, status='continuing',
            reason='The destination proof adds essential condition Q.', evidence=[destination.supporting])]
        self.observe_assignment(destination, assignment, output)
        another = self.source()
        legacy_handoff_or_carry_adapter(destination, another, 'another-origin')
        slate = destination.assess('slate', 'first', identifier='refreshed-slate')
        self.assertIn('essential condition Q', json.dumps(slate['material_objections']))
        destination.record_slate('refreshed-slate', review_ids=['review-refreshed-slate'])
        first = destination.assess('result', 'first', identifier='refreshed-result', status='resolved')
        self.assertIn('essential condition Q', json.dumps(first['material_objections']))
        destination.assess('result', 'second', status='resolved')
        destination.assert_delivered()

    def test_import_rejects_changed_inherited_manifest_and_derived_review_metadata(self):
        from research_harness.problem_objection_carry import carry_objections
        for field in ('subject', 'objection_identities', 'recorded_revision', 'inherited'):
            with self.subTest(field=field):
                source, destination = self.source(), self.fixture('math.PR')
                if field == 'inherited':
                    sibling = self.fixture('math.NT')
                    carry = legacy_handoff_or_carry_adapter(sibling, source)['result']
                    carry = copy.deepcopy(carry)
                    carry['members'][0]['version']['claim'] = 'Forged inherited claim'
                    sibling.put('problem_objection_carry', carry['id'], carry)
                    source = sibling
                else:
                    review = copy.deepcopy(source.store.snapshot()['records']['value_review']['review-slate-first'])
                    review[field] = {'kind': 'other'} if field == 'subject' else {'settled-C': 'renamed'} if field == 'objection_identities' else 9999
                    source.put('value_review', review['id'], review)
                destination.assert_error('problem_objection_provenance_invalid', lambda: destination.mutate(carry_objections,
                    dict(id='invalid', source_workspace=str(source.root), source_revision=source.store.revision)))

    def test_freshness_compares_material_without_rebuilding_evidence(self):
        from research_harness.problem_objection_carry import require_fresh_sources
        from research_harness import problem_objection_provenance as proof
        source, destination = self.source(), self.fixture('math.PR')
        legacy_handoff_or_carry_adapter(destination, source)
        records = destination.store.snapshot()['records']
        with patch.object(proof, 'evidence_record', side_effect=AssertionError('Freshness rebuilt evidence')):
            require_fresh_sources(records, destination.artifacts)
            source.put('local_note', 'unrelated', dict(text='No objection, assignment or source link changed.'))
            require_fresh_sources(records, destination.artifacts)
            assignment = source.assign('result', 'first')
            with patch.object(protocol, 'source_delivery_packet', side_effect=AssertionError('Freshness reconstructed a delivered request')):
                destination.assert_error('problem_objections_refresh_required',
                    lambda: require_fresh_sources(records, destination.artifacts))
            output = source.build_review_output('result')
            output['value']['objection_findings'] = [dict(id='settled-C', status='continuing',
                reason='The new bound continues the objection.', evidence=[source.supporting])]
            self.observe_assignment(source, assignment, output)
            destination.assert_error('problem_objections_refresh_required',
                lambda: require_fresh_sources(records, destination.artifacts))

    def test_staged_frontier_return_reserves_deterministic_child_assignment(self):
        from research_harness import problem_claims as real_claims, review_staged_protocol
        from research_harness.artifacts import ArtifactStore
        from research_harness.storage import Store
        from research_harness.record_check import copy_snapshot
        from problem_parts_fixtures import ResultPartsCase
        import sys
        sibling = self.fixture('math.PR')
        fixture = ResultPartsCase()
        fixture.staged_entry = True
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        sys.modules['research_harness.problem_claims'].require_problem = real_claims.require_problem
        home = fixture.case
        fixture.oversized()
        controller = fixture.assign_staged('controller')
        branch = copy.copy(home)
        branch.root = home.root.with_name('staged-origin-branch')
        copy_snapshot(home.root, branch.root)
        branch.store, branch.artifacts = Store(branch.root), ArtifactStore(branch.root)
        branch.mutate(review_staged_protocol.plan_frontier, dict(controller_assignment_id=controller['id']))
        children = [row for row in branch.store.snapshot()['records']['review_assignment'].values()
                    if row.get('staged_review', {}).get('kind') == 'task']
        self.assertEqual(len(children), 1)
        child = children[0]
        legacy_handoff_or_carry_adapter(sibling, branch)
        legacy_handoff_or_carry_adapter(home, sibling, 'returned-staged-origin')
        records = home.store.snapshot()['records']
        inventory = records['problem_objection_carry']['returned-staged-origin']['origin_records']
        self.assertTrue(any(row['kind'] == 'review_assignment' and row['id'] == child['id']
                            and row['digest'] == digest(child) for row in inventory))
        self.assertNotIn(child['id'], records['review_assignment'])
        revision = home.store.revision
        with self.assertRaises(ResearchError) as caught:
            home.mutate(review_staged_protocol.plan_frontier, dict(controller_assignment_id=controller['id']))
        self.assertEqual(caught.exception.code, 'problem_objection_origin_conflict')
        self.assertEqual(home.store.revision, revision)
        self.assertNotIn(child['id'], home.store.snapshot()['records']['review_assignment'])

    def test_first_insertion_canonical_mapping_is_rederived_from_observed_output(self):
        from research_harness.problem_objection_carry import carry_objections
        # Alter the first insertion and its event/receipt consistently. This is
        # store-write access, not a later immutable-record replacement.
        source, destination = self.source(), self.fixture('math.PR')
        self.rewrite_first_review_mapping(source, 'review-slate-first', {'settled-C': 'agent-authored-canonical-id'})
        before = destination.store.revision
        destination.assert_error('problem_objection_provenance_invalid', lambda: destination.mutate(carry_objections,
            dict(id='forged-first-insertion', source_workspace=str(source.root), source_revision=source.store.revision)))
        self.assertEqual(destination.store.revision, before)

    def test_native_collision_mapping_authenticates_but_missing_mapping_is_refused(self):
        from research_harness.problem_objection_carry import carry_objections
        source, destination = self.fixture(bars=False), self.fixture('math.PR')
        for reviewer, claim in (('first', 'The first comparator settles C.'), ('second', 'A different comparator settles C.')):
            output = source.build_review_output('bar')
            output['objections'] = [dict(id='shared-id', claim=claim, reason='The supplied field standard applies.',
                evidence=[source.supporting], resolution_condition='Establish a different domain.')]
            source.observe('bar', reviewer, output)
        imported = legacy_handoff_or_carry_adapter(destination, source)['result']
        self.assertEqual({row['origin_id'] for row in imported['members']},
                         {'shared-id', 'shared-id@review-bar-second'})
        destination = self.fixture('math.NT')
        self.rewrite_first_review_mapping(source, 'review-bar-second', {})
        destination.assert_error('problem_objection_provenance_invalid', lambda: destination.mutate(carry_objections,
            dict(id='missing-native-mapping', source_workspace=str(source.root), source_revision=source.store.revision)))

    def rewrite_first_review_mapping(self, case, identifier, identities):
        from research_harness.storage import _canonical
        with case.store._connection(writable=True) as connection:
            connection.execute('DROP TRIGGER events_no_update')
            connection.execute('DROP TRIGGER receipts_no_update')
            for row in connection.execute('SELECT * FROM events ORDER BY revision').fetchall():
                changes = json.loads(row['changes'])
                changed = False
                for change in changes:
                    if change['kind'] == 'value_review' and change['key'] == identifier:
                        change['value']['objection_identities'] = identities
                        record = change['value']
                        changed = True
                if not changed:
                    continue
                result = json.loads(row['result'])
                if isinstance(result, dict) and result.get('id') == identifier:
                    result['objection_identities'] = identities
                event = dict(revision=row['revision'], request_id=row['request_id'], operation=row['operation'],
                    payload=json.loads(row['payload']), changes=changes, result=result)
                connection.execute('UPDATE events SET changes=?, result=?, digest=? WHERE revision=?',
                    (_canonical(changes), _canonical(result), digest(event), row['revision']))
                receipt = dict(request_id=row['request_id'], revision=row['revision'], result=result)
                connection.execute('UPDATE receipts SET response=?, digest=? WHERE request_id=?',
                    (_canonical(receipt), digest(receipt), row['request_id']))
                connection.execute('UPDATE records SET value=?, digest=? WHERE kind=? AND key=?',
                    (_canonical(record), digest(record), 'value_review', identifier))
            from research_harness.storage import _SCHEMA
            for name in ('events_no_update', 'receipts_no_update'):
                connection.execute(_SCHEMA[('trigger', name)])
            connection.commit()
        # Reopen and authenticate the real database before carry admission.
        self.assertIn(identifier, case.store.snapshot()['records']['value_review'])

    def test_local_id_collision_preserves_exact_foreign_projection_with_local_citation(self):
        from research_harness.problem_objection_carry import carry_objections
        source = self.fixture()
        candidate = source.store.snapshot()['records']['problem_candidate']['candidate-1']
        reference = dict(kind='record', record_kind='problem_candidate', id='candidate-1', digest=digest(candidate))
        source.assess('slate', 'first', objections=[dict(id='record-objection', claim='This exact candidate is settled.',
            candidate_ids=['candidate-1'], reason='The recorded statement is within the theorem.', evidence=[reference], resolution_condition='A different domain is needed.')])
        source.record_slate(review_ids=['review-slate-first'])
        destination = self.fixture('math.PR', source=source)
        native = destination.store.snapshot()['records']['problem_candidate']['candidate-1']
        self.assertNotEqual(digest(native), reference['digest'])
        legacy_handoff_or_carry_adapter(destination, source)
        assignment = destination.assign('slate', 'first')
        packet = destination.packet(assignment)
        [entry] = packet['material_objections'][0]['version_evidence']
        self.assertEqual(entry['record_kind'], 'problem_carried_evidence')
        [foreign] = entry['record']['projection']['version_evidence']
        self.assertEqual(foreign['record']['statement'], candidate['statement'])
        self.assertNotIn('notes', foreign['record'])
        self.assertNotIn('disposition', foreign['record'])
        output = destination.build_slate_output(['candidate-1'])
        output['value']['objection_findings'] = [dict(id=packet['material_objections'][0]['id'], status='continuing', reason='The exact projection applies.',
            evidence=[{key: entry[key] for key in ('kind', 'record_kind', 'id', 'digest')}])]
        self.observe_assignment(destination, assignment, output)
        output['value']['objection_findings'][0]['evidence'] = [reference]
        destination.assert_error('review_evidence_undelivered', lambda: decisions.validate_review_contract(
            destination.store.snapshot()['records'], destination.artifacts, assignment, output))
        self.assertEqual(destination.store.snapshot()['records']['problem_candidate']['candidate-1'], native)

    def test_moved_source_keeps_origin_and_can_refresh_explicit_link(self):
        from research_harness.problem_objection_carry import import_set_digest, require_fresh_sources
        from research_harness.storage import Store
        source, destination = self.source(), self.fixture('math.PR')
        legacy_handoff_or_carry_adapter(destination, source)
        before = import_set_digest(destination.store.snapshot()['records'])
        moved = source.root.with_name('moved')
        source.root.rename(moved)
        source.root, source.store = moved, Store(moved)
        legacy_handoff_or_carry_adapter(destination, source, 'moved')
        self.assertEqual(import_set_digest(destination.store.snapshot()['records']), before)
        require_fresh_sources(destination.store.snapshot()['records'], destination.artifacts)

    def test_inline_source_refinement_carries_only_observed_bounded_passage(self):
        from research_harness import literature, reading
        from research_harness.record_check import check_integrity
        source, destination = self.source(), self.fixture('math.PR')
        quote, secret = 'The inspected passage fixes the exact finite constant.', 'UNSUPPLIED-OUTSIDE-PASSAGE'
        version = source.metadata(997)
        capture = source.capture(version, body=quote + ' ' + secret + ' References: none.')
        link = source.link(version, capture['source_id'], capture['original'], quote, kind='span')
        bundle = source.bundle(version, capture=capture, bundle_id='bounded-original-bundle')
        bundle['units'].append(dict(id='bounded-original', kind='text', required=True, link=link))
        source.mutate(literature.import_bundle, bundle)
        source.mutate(reading.record_reading, source.full_note(bundle, note_id='bounded-reading'))
        source.mutate(source.load_problem_module().record_value,
            source.build_value([source.item, dict(kind='reading', version_id=version,
                reading_id='bounded-reading')], identifier='bounded-value'))
        assignment = source.assign('result', 'first')
        output = source.build_review_output('result')
        output['value']['objection_findings'] = [dict(id='settled-C', status='continuing',
            reason='The exact inspected bound remains material.', evidence=[dict(kind='source', link=link)])]
        self.observe_assignment(source, assignment, output)
        carry = legacy_handoff_or_carry_adapter(destination, source)['result']
        [member] = [row for row in carry['members'] if row['producer_origin'] == carry['source_origin']
                    and row['review_id'] == 'review-result-first']
        wrapper = destination.store.snapshot()['records']['problem_carried_evidence'][member['evidence_id']]
        self.assertIn(quote, json.dumps(wrapper))
        self.assertNotIn(secret, json.dumps(wrapper))
        self.assertFalse(any(row['sha256'] == capture['original']['sha256'] for row in wrapper['artifacts']))
        for role in ('slate', 'result'):
            packet = destination.packet(destination.assign(role, 'first', identifier='bounded-' + role))
            self.assertIn(quote, json.dumps(packet['material_objections']))
            self.assertNotIn(secret, json.dumps(packet['material_objections']))
        source.root.rename(source.root.with_name('unavailable-inline-source'))
        self.assertEqual(check_integrity(destination.store)['problem_objection_carries'], 1)

    def test_pinned_json_workspace_manifest_remains_descriptive_in_proof(self):
        from research_harness.integration import pin_artifact
        from research_harness.record_check import check_integrity
        source, destination = self.source(), self.fixture('math.PR')
        manifest = source.root / 'inputs.json'
        manifest.write_text(json.dumps(dict(inputs=[dict(path='data/input.csv', sha256='a' * 64,
            size=12, media_type='text/csv')])))
        pinned = source.mutate(pin_artifact, dict(id='inputs', path='inputs.json', media_type='application/json'))['result']['artifact']
        carry = legacy_handoff_or_carry_adapter(destination, source)['result']
        self.assertIn(pinned, [row['original'] for row in carry['proof']['artifacts']])
        self.assertEqual(check_integrity(destination.store)['problem_objection_carries'], 1)

    def test_copied_origin_cannot_replace_one_immutable_producing_review(self):
        from research_harness.artifacts import ArtifactStore
        from research_harness.storage import Store
        from research_harness.record_check import copy_snapshot
        source, destination = self.fixture(), self.fixture('math.PR')
        branch = copy.copy(source)
        branch.root = source.root.with_name('diverged-origin')
        copy_snapshot(source.root, branch.root)
        branch.store, branch.artifacts = Store(branch.root), ArtifactStore(branch.root)
        for case, claim in ((source, 'The first native bound settles C.'), (branch, 'A different native bound settles C.')):
            case.assess('slate', 'first', objections=[dict(id='same-id', claim=claim,
                reason='The supplied comparator applies to this formulation.', evidence=[case.supporting],
                candidate_ids=['candidate-1'], resolution_condition='Supply an evidenced different formulation.')])
            case.record_slate(review_ids=['review-slate-first'])
        legacy_handoff_or_carry_adapter(destination, source)
        destination.assert_error('problem_objection_origin_conflict',
            lambda: legacy_handoff_or_carry_adapter(destination, branch, 'conflicting-review'))

    def test_empty_source_cut_still_binds_immutable_origin_review(self):
        from research_harness.artifacts import ArtifactStore
        from research_harness.storage import Store
        from research_harness.record_check import copy_snapshot
        source, destination = self.fixture(), self.fixture('math.PR')
        branch = copy.copy(source)
        branch.root = source.root.with_name('diverged-empty-origin')
        copy_snapshot(source.root, branch.root)
        branch.store, branch.artifacts = Store(branch.root), ArtifactStore(branch.root)
        source.assess('slate', 'first')
        source.record_slate(review_ids=['review-slate-first'])
        branch.assess('slate', 'first', objections=[dict(id='new-claim', claim='The comparator settles C.',
            reason='Its quantifiers include the entire proposed domain.', evidence=[branch.supporting],
            candidate_ids=['candidate-1'], resolution_condition='Establish an evidenced different statement.')])
        branch.record_slate(review_ids=['review-slate-first'])
        carry = legacy_handoff_or_carry_adapter(destination, source)['result']
        self.assertEqual(carry['members'], [])
        destination.assert_error('problem_objection_origin_conflict',
            lambda: legacy_handoff_or_carry_adapter(destination, branch, 'conflicting-empty-review'))

    def returned_branch(self):
        from research_harness.artifacts import ArtifactStore
        from research_harness.storage import Store
        from research_harness.record_check import copy_snapshot
        home = self.fixture()
        branch = copy.copy(home)
        branch.root = home.root.with_name('returned-origin-branch')
        copy_snapshot(home.root, branch.root)
        branch.store, branch.artifacts = Store(branch.root), ArtifactStore(branch.root)
        branch.assess('slate', 'first', objections=[dict(id='returned-concern', claim='The comparator settles C.',
            reason='The supplied finite bound covers this formulation.', evidence=[branch.supporting],
            candidate_ids=['candidate-1'], resolution_condition='Establish an evidenced different statement.')])
        branch.record_slate(review_ids=['review-slate-first'])
        sibling = self.fixture('math.PR')
        legacy_handoff_or_carry_adapter(sibling, branch)
        legacy_handoff_or_carry_adapter(home, sibling, 'returned-origin')
        return home

    def test_returned_origin_reserves_assignment_ids_during_native_admission(self):
        home = self.returned_branch()
        revision = home.store.revision
        home.assert_error('problem_objection_origin_conflict', lambda: home.assign('slate', 'first'))
        self.assertEqual(home.store.revision, revision)
        fresh = home.assign('slate', 'first', identifier='fresh-slate-after-return')
        self.assertIn('returned-concern', json.dumps(home.packet(fresh)['material_objections']))

    def test_returned_origin_reserves_review_ids_during_native_admission(self):
        home = self.returned_branch()
        assignment = home.assign('slate', 'first', identifier='fresh-slate-after-return')
        output = home.build_slate_output(['candidate-1'])
        output['value']['objection_findings'] = [dict(id=row['id'], status='continuing',
            reason='The supplied comparator remains material.', evidence=[home.supporting])
            for row in home.packet(assignment)['material_objections']]
        with patch('research_harness.review_transport.send_request', return_value=home.build_provider_response(output)):
            home.mutate(protocol.invoke_assignment, dict(id='attempt-fresh-slate', assignment_id=assignment['id']), credential='test-only')
        revision = home.store.revision
        home.assert_error('problem_objection_origin_conflict', lambda: home.mutate(decisions.record_value_review,
            dict(output, id='review-slate-first', dossier_id=None, assignment_id=assignment['id'])))
        self.assertEqual(home.store.revision, revision)
        home.mutate(decisions.record_value_review, dict(output, id='fresh-review-after-return', dossier_id=None, assignment_id=assignment['id']))


class ProblemObjectionCarryDeliveryTests(unittest.TestCase):
    fixture = ProblemObjectionCarryTests.fixture
    source = ProblemObjectionCarryTests.source

    def assert_portable_result_proof(self, source):
        from research_harness.record_check import check_integrity
        destination = self.fixture('math.NT')
        imported = legacy_handoff_or_carry_adapter(destination, source)['result']
        self.assertTrue(any(row['producer_origin'] == imported['source_origin']
            and row['review_id'] in ('review-final', 'legacy-final') for row in imported['members']))
        source.root.rename(source.root.with_name('unavailable-parts-source'))
        self.assertEqual(check_integrity(destination.store)['problem_objection_carries'], 1)

    def local_destination(self):
        from codex_route_fixtures import record_codex_route, invoke_codex
        from review_request_builder_fixtures import use_emulated_request_builders
        use_emulated_request_builders(self)
        source, case = self.source(), self.fixture('math.PR', bars=False)
        legacy_handoff_or_carry_adapter(case, source)
        record_codex_route(case, identifier='local', output_tokens=128000, timeout_seconds=600)
        case.route_id = 'local'
        for reviewer in ('first', 'second'):
            assignment = case.assign('bar', reviewer, **case.deliver_supporting(reviewer))
            output = case.build_review_output('bar')
            invoke_codex(case, assignment['id'], output)
            case.mutate(decisions.record_value_review, dict(output, id='review-' + assignment['id'], dossier_id=None, assignment_id=assignment['id']))
        assignment = case.assign('slate', 'first')
        packet = case.packet(assignment)
        output = case.build_slate_output(['candidate-1'])
        output['value']['objection_findings'] = [dict(id=row['id'], status='continuing', reason='The supplied theorem still applies.',
            evidence=[case.supporting]) for row in packet['material_objections']]
        invoke_codex(case, assignment['id'], output)
        case.mutate(decisions.record_value_review, dict(output, id='review-' + assignment['id'], dossier_id=None, assignment_id=assignment['id']))
        case.record_slate(review_ids=['review-slate-first'])
        from test_research_problem_assessment import build_assessment
        assessment = build_assessment(case.selected, 'large-assessment')
        assessment['items']['priority']['evidence'].append(case.supporting)
        for index in range(6):
            assessment['items']['priority']['evidence'].append(case.artifacts.put(
                ('Theorem %d establishes the exact finite bound. ' % index + 'evidence ' * 2500).encode(), 'text/plain'))
        case.mutate(case.load_problem_module().record_assessment, assessment)
        return source, case

    def test_parts_final_carries_all_imported_versions_and_frozen_prompt(self):
        from codex_route_fixtures import invoke_codex, limit_codex_input
        from review_parts_fixtures import make_output
        from research_harness import review_parts
        source, case = self.local_destination()
        limit_codex_input(self, 135000)
        assignment = case.assign('result', 'first', identifier='part-1')
        turns = 0
        while case.packet(assignment).get('review_parts', {}).get('kind') != 'final':
            packet = case.packet(assignment)
            self.assertIn('review_parts', packet)
            self.assertIn('problem_objection_carry', assignment['prompt']['parameters'])
            invoke_codex(case, assignment['id'], make_output(packet))
            turns += 1
            self.assertLess(turns, 25)
            assignment = case.assign('result', 'first', identifier='part-' + str(turns + 1), prior_assignment_id=assignment['id'])
        self.assertGreater(turns, 0)
        packet = case.packet(assignment)
        self.assertTrue(packet['material_objections'])
        self.assertTrue(protocol._prompt_matches(assignment))
        output = case.build_review_output('result')
        output['value']['objection_findings'] = [dict(id=row['id'], status='continuing', reason='The complete imported versions still apply.',
            evidence=[case.supporting]) for row in packet['material_objections']]
        invoke_codex(case, assignment['id'], output)
        review = case.mutate(decisions.record_value_review, dict(output, id='review-final', dossier_id=None, assignment_id=assignment['id']))['result']
        self.assertTrue(review_parts.verify_series(case.store.snapshot()['records'], case.artifacts, assignment['id'])['complete'])
        self.assertFalse(decisions.find_undelivered_review_references(case.store.snapshot()['records'], case.artifacts, review))
        self.assert_portable_result_proof(case)

    def test_import_change_stales_staged_epoch_and_refuses_new_children(self):
        from codex_route_fixtures import limit_codex_input
        from research_harness import review_staged_protocol
        source, case = self.local_destination()
        limit_codex_input(self, 2000000)
        prior = case.assign('result', 'first', identifier='prior')
        controller = case.assign('result', 'first', identifier='controller', staged_upgrade=dict(assignment_id=prior['id']))
        another = self.source()
        legacy_handoff_or_carry_adapter(case, another, 'another-origin')
        records = case.store.snapshot()['records']
        epoch = records['review_epoch'][controller['staged_review']['epoch_id']]
        self.assertFalse(review_staged_protocol.matches_currency(records, case.artifacts, epoch))
        case.assert_error('review_epoch_stale', lambda: case.assign('result', 'first', identifier='stale-child',
            controller_assignment_id=controller['id'], task_id=controller['staged_review']['task_ids'][0]))

    def test_visual_source_refinement_carries_selected_page_and_attaches_destination_images(self):
        from codex_route_fixtures import invoke_codex, record_codex_route
        from review_request_builder_fixtures import use_emulated_request_builders
        from research_fixtures import client
        from test_research_binary_pdf import authored_pdf
        from test_visual_result_delivery import VisualResultDeliveryTests
        from research_harness import literature, reading, review_source_complete as complete
        from research_harness import review_codex_visual_transport as visual, review_transport as transport
        from research_harness.acquisition import acquire_fulltext
        from research_harness.record_check import check_integrity
        source = self.fixture(bars=False, settling=True)
        use_emulated_request_builders(self)
        record_codex_route(source, identifier='local', output_tokens=128000, timeout_seconds=600)
        source.route_id = 'local'
        for reviewer in ('first', 'second'):
            bar = source.assign('bar', reviewer, **source.deliver_supporting(reviewer))
            bar_output = source.build_review_output('bar')
            invoke_codex(source, bar['id'], bar_output)
            source.mutate(decisions.record_value_review, dict(bar_output, id='review-' + bar['id'], dossier_id=None, assignment_id=bar['id']))
        slate = source.assign('slate', 'first')
        slate_output = source.build_slate_output(['candidate-1'])
        slate_output['objections'] = [dict(id='settled-C', claim='The supplied theorem settles C.',
            reason='Its bound covers the whole proposed domain.', evidence=[source.supporting],
            candidate_ids=['candidate-1'], resolution_condition='Establish an evidenced different formulation.')]
        invoke_codex(source, slate['id'], slate_output)
        source.mutate(decisions.record_value_review, dict(slate_output, id='review-slate-first', dossier_id=None, assignment_id=slate['id']))
        source.record_slate(review_ids=['review-slate-first'])
        destination = self.fixture('math.NT')
        version = source.metadata(996)
        http, _, _ = client([(200, {'Content-Type': 'application/pdf'}, authored_pdf())])
        capture = acquire_fulltext(source.store, version, 'https://arxiv.org/pdf/' + version[6:], http=http,
            extractor=lambda _: dict(status='extracted', text='Authored page. References: none.\f'),
            expected_revision=source.store.revision, request_id='native-page')['capture']
        link = dict(version_id=version, source_id=capture['source_id'], artifact=capture['original'],
            locator=dict(kind='pdf', page_index=0, printed_page='1', region=[0, 0, 1, 1]))
        bundle = source.bundle(version, capture=capture, bundle_id='page-bundle')
        bundle['units'].append(dict(id='page', kind='figure', required=True, link=link))
        source.mutate(literature.import_bundle, bundle)
        source.mutate(reading.record_reading, source.full_note(bundle, note_id='page-reading'))
        page_evidence = [source.item, dict(kind='reading', version_id=version, reading_id='page-reading')]
        page_value = source.build_value(page_evidence, identifier='page-value')
        page_value.pop('accounts')
        for section in ('motivation', 'current_state'):
            page_value[section][0]['evidence'] = page_evidence
        # This fixture exercises a historical v2-to-v3 prompt transition.
        source.put('problem_value', page_value['id'], dict(page_value, recorded_revision=source.store.revision + 1))
        def output(requests=(), evidence=None):
            value = source.build_review_output('result')
            value['value']['objection_findings'] = [dict(id=identifier, status='continuing',
                reason='The supplied native page keeps the exact bound material.', evidence=evidence or [source.supporting])]
            if requests:
                value['value']['status'] = 'unresolved'
                value['source_requests'] = list(requests)
            return value

        current_prompt = protocol.canonical_prompt

        def legacy_prompt(role, **options):
            if role == 'result' and options.get('orchestration') == 'problem_creation' and 'version' not in options:
                options['version'] = 2
            return current_prompt(role, **options)

        # The native visual transition needs a current scientific frame. Use
        # the established v2-to-v3 rubric upgrade to refresh that frame.
        with patch.object(protocol, 'canonical_prompt', side_effect=legacy_prompt):
            first = source.assign('result', 'first', identifier='image-request')
            identifier = source.packet(first)['material_objections'][0]['id']
            invoke_codex(source, first['id'], output([dict(id='pdf-source', kind='source', version_id=version, depth='fulltext')]))
            second = source.assign('result', 'first', identifier='complete-request', prior_assignment_id=first['id'], source_delivery=complete.MODE)
            invoke_codex(source, second['id'], output([dict(id='inventory', kind='inventory_page', page=0)]))
            prior = source.assign('result', 'first', identifier='legacy-visual-source', prior_assignment_id=second['id'])
            legacy = output()
            legacy['value']['assurances'][-1]['status'] = 'not_assessed'
            invoke_codex(source, prior['id'], legacy)
            source.mutate(decisions.record_value_review, dict(legacy, id='review-legacy-visual-source', dossier_id=None, assignment_id=prior['id']))
        sender = VisualResultDeliveryTests()
        sender.case = source
        with patch.object(visual, 'runtime', return_value=dict(adapter=visual.ADAPTER, fixture='carry-native-visual')):
            source.mutate(protocol.record_route, dict(id='visual-route', adapter=visual.ADAPTER,
                model='fixture-model', endpoint='codex://local', configuration=dict(max_output_tokens=128000, timeout_seconds=600)))

            def respond_probe(route, request, credential):
                positive = source.store.snapshot()['records']['review_probe_invocation']['visual-probe']['positive_controls']
                return sender.response(route, request, dict(allowed_control=positive['allowed_control'],
                    evidence_control=positive['evidence_control'], excluded_controls=[]))

            with patch.object(transport, 'send_request', side_effect=respond_probe):
                source.mutate(protocol.probe_route, dict(id='visual-probe', route_id='visual-route'), credential='test-only')
            source.route_id = 'visual-route'
            third = source.assign('result', 'first', identifier='visual-refinement', prior_assignment_id=prior['id'])
            final = output(evidence=[dict(kind='source', link=link)])
            with patch.object(transport, 'send_request', side_effect=lambda route, request, credential: sender.response(route, request, final)):
                source.mutate(protocol.invoke_assignment, dict(id='attempt-visual-refinement', assignment_id=third['id']), credential='test-only')
            source.mutate(decisions.record_value_review, dict(final, id='review-visual-refinement', dossier_id=None, assignment_id=third['id']))
            carry = legacy_handoff_or_carry_adapter(destination, source)['result']
            [member] = [row for row in carry['members'] if row['producer_origin'] == carry['source_origin']
                        and row['review_id'] == 'review-visual-refinement']
            wrapper = destination.store.snapshot()['records']['problem_carried_evidence'][member['evidence_id']]
            self.assertTrue(any(row['media_type'] == 'image/png' for row in wrapper['artifacts']))
            self.assertFalse(any(row['sha256'] == capture['original']['sha256'] for row in wrapper['artifacts']))
            for role in ('slate', 'result'):
                destination.assess(role, 'first', identifier='image-' + role)
                attempt = destination.store.snapshot()['records']['review_attempt']['attempt-image-' + role]
                request = json.loads(destination.artifacts.read(attempt['request']))
                self.assertTrue(any(row.get('type') == 'input_image' and row['image_url'].startswith('data:image/png;base64,')
                    for row in request['input'][-1]['content']))
                if role == 'slate':
                    destination.record_slate('image-slate', review_ids=['review-image-slate'])
            source.root.rename(source.root.with_name('unavailable-visual-source'))
            self.assertEqual(check_integrity(destination.store)['problem_objection_carries'], 1)

    def test_staged_final_receives_imported_material_and_prompt_variant(self):
        from research_harness import review_parts, review_problem_parts, review_worker, cli
        from problem_parts_fixtures import ResultPartsCase
        import test_review_staged_frontier as frontier
        import sys
        source = self.source()
        from research_harness import problem_claims as real_claims
        fixture = ResultPartsCase()
        fixture.staged_entry = True
        fixture.timeout_seconds = 600
        fixture.output_tokens = 1000000
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        # The established staged fixture isolates its claim description, but
        # carry still uses the real problem-store discriminator.
        sys.modules['research_harness.problem_claims'].require_problem = real_claims.require_problem
        case = fixture.case
        case.packet = fixture.packet
        legacy_handoff_or_carry_adapter(case, source)
        fixture.oversized(count=3, characters=5000, limit=10000000)
        with patch.object(review_parts, 'prepare_packet', side_effect=lambda records, artifacts, value, route, packet, prompt, history, **kwargs: packet), \
             patch.object(review_problem_parts, 'prepare_packet', review_problem_parts._prepare_legacy_packet):
            prior = case.assign('result', 'first', identifier='predecessor')
            try:
                controller = case.assign('result', 'first', identifier='controller', staged_upgrade=dict(assignment_id=prior['id']))
            except ResearchError as error:
                self.fail(str(error.as_dict()))
        output = case.build_review_output('result')
        output['value'].update(objective_adequacy='not_assessed', method_adequacy='not_assessed')
        objection = case.packet(controller)['material_objections'][0]
        output['value']['objection_findings'] = [dict(id=objection['id'], status='continuing',
            reason='The complete imported material still applies.', evidence=[case.supporting])]
        from research_harness.review_refusal import evidence_arrays
        wrapper = next(row for row in objection['version_evidence'] if row.get('record_kind') == 'problem_carried_evidence')
        citation = {key: wrapper[key] for key in ('kind', 'record_kind', 'id', 'digest')}
        for evidence in evidence_arrays(output):
            evidence[:] = [copy.deepcopy(citation)]
        (case.root / 'scientific.json').write_text(json.dumps(output))
        next_command = controller['next'][0]
        # Keep this fixture's authored grounding bounded: one actual supplied
        # original per judgment, rather than repeating every context span in
        # every cell of the all-claim matrix.
        worker = frontier.WORKER.replace('from test_review_staged_frontier import build_output', '''from test_review_staged_frontier import build_output as original_output
def build_output(body, root):
    if body['task']['type'] == 'frame':
        return dict(claims=[dict(id='claim:' + row['source_id'], kind='record', spans=[row['span']],
            dependencies=[], children=[]) for row in body['frame']['fields']],
            conditions=[], dependencies=[], unresolved=[])
    if body['task']['type'] == 'combine' and body['task']['scope'] == 'card_segment':
        return {'segment_readings': [dict(id=body['task']['id'] + ':' + str(index),
            card_id=row['span']['card_id'], start=row['span']['start'], end=row['span']['end'],
            reading='Read the complete supplied bounded card segment.')
            for index, row in enumerate(body['combine']['segments'])]}
    output = original_output(body, root)
    if body['task']['type'] == 'combine':
        segments = body.get('segments', {}).get('segment_task_ids', [])
        for combination in output.get('combinations', []):
            combination['segment_task_ids'] = segments
            combination['reconciliation'] = ('All observed segments and their conditions were reconciled.'
                if body['task']['scope'] != 'joint_card' else None)
    if body['task']['type'] == 'read':
        units = {row['id']: row for row in body['units']}
        for card in output['cards']:
            unit = units[card['occurrence_id']]
            grounds = [row['span'] for row in unit['sources'][:1]]
            if not grounds:
                grounds = [row['span'] for row in body['originals'][:1]]
            for judgment in card['judgments']:
                judgment['grounds'] = grounds
            card['critical_spans'] = grounds
            for conclusion in card['conclusions']:
                conclusion['grounds'] = grounds
        if body.get('inherited') and body.get('originals'):
            output['answers'] = [dict(id=body['task']['id'] + ':answer:' + str(index), finding_id=row['id'],
                disposition='retained', reason='The supplied original retains this inherited concern.',
                grounds=[body['originals'][0]['span']]) for index, row in enumerate(body['inherited'])]
    return output''')
        with patch.object(review_worker, '_entry_command', return_value=[sys.executable, '-c', worker]):
            for index in range(70):
                payload = next_command['payload']
                try:
                    result = cli.OPERATIONS[next_command['command']](case.store, payload,
                        expected_revision=case.store.revision, request_id='staged-' + str(index))['result']
                except ResearchError as error:
                    records = case.store.snapshot()['records']
                    epoch = records['review_epoch'][controller['staged_review']['epoch_id']]
                    current_claims = sys.modules['research_harness.problem_claims']
                    self.fail(str(dict(error=error.as_dict(), command=next_command['command'], turn=index,
                        head_epoch=records['review_slot_head'][controller['slot_id']]['epoch_id'], epoch_id=epoch['id'],
                        subject=protocol.build_problem_subject(records, 'result'), expected_subject=epoch['subject'],
                        expected_evidence=case.packet(controller).get('problem_evidence_digest'),
                        actual_evidence=current_claims.compute_problem_evidence_digest(records, case.artifacts),
                        fixture_evidence=digest(case.expected))))
                if next_command['command'] == 'value-review':
                    break
                next_command = result['next'][0]
            else:
                self.fail('Staged carried-objection assessment did not reach recording')
        records = case.store.snapshot()['records']
        finals = [row for row in records['review_assignment'].values() if row.get('staged_task', {}).get('type') == 'final']
        self.assertTrue(finals)
        for assignment in finals:
            self.assertIn('problem_objection_carry', assignment['prompt']['parameters'])
            self.assertIn(objection['id'], json.dumps(case.packet(assignment)))
            self.assertTrue(protocol._prompt_matches(assignment))

    def test_legacy_parts_final_retains_imported_objection_evidence(self):
        from codex_route_fixtures import invoke_codex, limit_codex_input
        from problem_parts_fixtures import note_units
        from research_harness import review_parts, review_problem_parts
        source, case = self.local_destination()
        limit_codex_input(self, 170000)
        with patch.object(review_parts, 'prepare_packet', side_effect=lambda records, artifacts, value, route, packet, prompt, history, **kwargs: packet), \
             patch.object(review_problem_parts, 'prepare_packet', review_problem_parts._prepare_legacy_packet):
            assignment = case.assign('result', 'first', identifier='legacy-1')
            turns = 0
            while case.packet(assignment)['problem_evidence_parts']['stage'] == 'part':
                packet = case.packet(assignment)
                output = dict(stage='result', phase='evidence_part', part=packet['problem_evidence_parts']['turn'],
                    notes=[dict(unit=unit, note='The supplied original bears on the exact bound.') for unit in note_units(packet)])
                invoke_codex(case, assignment['id'], output)
                turns += 1
                self.assertLess(turns, 25)
                try:
                    assignment = case.assign('result', 'first', identifier='legacy-' + str(turns + 1), prior_assignment_id=assignment['id'])
                except ResearchError as error:
                    self.fail(str(error.as_dict()))
        packet = case.packet(assignment)
        self.assertGreater(turns, 0)
        self.assertTrue(packet['material_objections'])
        self.assertIn('problem_objection_carry', assignment['prompt']['parameters'])
        self.assertTrue(protocol._prompt_matches(assignment))
        output = case.build_review_output('result')
        output['value']['objection_findings'] = [dict(id=row['id'], status='continuing', reason='The inherited theorem still applies.',
            evidence=[case.supporting]) for row in packet['material_objections']]
        invoke_codex(case, assignment['id'], output)
        case.mutate(decisions.record_value_review, dict(output, id='legacy-final', dossier_id=None, assignment_id=assignment['id']))
        self.assert_portable_result_proof(case)
