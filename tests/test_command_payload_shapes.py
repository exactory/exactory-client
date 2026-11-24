"""A command answers a payload of a wrong shape with a refusal that has a code (requirement R5.3).

Each test holds a payload that ended with a Python exception in the payload audit of release 0.92.0, or in the reviews
of its repair, on a small store that reaches the check, and the refusal that the command now gives. The accepted
controls show that a member that the harness itself writes, a text id, an empty or a null predecessor, still reaches
the record that it names or the step that it plans.
"""

import copy
import json
from pathlib import Path
import unittest
from unittest.mock import patch

from research_harness import acquisition, cli, native_review, operations, problem_openness, storage
from research_harness import review_continuation
from research_harness.errors import ResearchError
from research_harness.evidence import digest
from test_research_probe import build_probe_outcome
from test_research_problem_attempt import build_attempt
from test_research_problem_claims import ProblemCase
# The fixture classes are imported as modules, so this module does not collect their tests.
import test_research_probe
import test_research_review_protocol
import test_research_verification
import test_review_staged_frontier

EXAMPLES_PATH = Path(cli.__file__).resolve().parents[1] / "docs/research-cli-examples.json"
# A reviewer route whose documented input capacity admits the packet of a native assignment.
ROUTE = {"id": "route-1", "adapter": "openai_responses_v1", "model": "gpt-6-astra",
         "endpoint": "https://example.org/responses", "configuration": {"max_output_tokens": 64000, "timeout_seconds": 5}}


class PayloadShapeCase(ProblemCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.examples = json.loads(EXAMPLES_PATH.read_bytes())

    def build_example(self, command, **changes):
        return dict(self.examples[command], **changes)

    def assert_refused(self, code, function, *args, **kwargs):
        revision = self.store.revision
        with self.assertRaises(ResearchError) as caught:
            function(*args, **kwargs)
        self.assertEqual(caught.exception.code, code, caught.exception.as_dict())
        self.assertEqual(self.store.revision, revision)
        return caught.exception

    def refuse_command(self, code, command, payload):
        return self.assert_refused(code, self.mutate, cli.OPERATIONS[command], payload)

    def count_history_replays(self, command, payload):
        """Run a command that is refused, and count the replays of the whole history of the store that it made.

        Every authoritative read, receipt lookup and write replays the history once (storage._validate). The revision
        is read before the count, so the count holds the command alone."""
        revision = self.store.revision
        with patch.object(storage, "_validate", wraps=storage._validate) as validate, \
                self.assertRaises(ResearchError) as caught:
            cli.OPERATIONS[command](self.store, payload, expected_revision=revision, request_id="replay-count")
        return caught.exception, sum(call.kwargs.get("replay", True) for call in validate.call_args_list)


def start_fixture(test, case_class):
    """A fixture case of another module, cleaned up with the test also when its own set-up fails."""
    case = case_class()
    test.addCleanup(case.doCleanups)
    case.setUp()
    return case


class StudyCommandShapeTests(PayloadShapeCase):
    def test_bind_verdict_refuses_a_task_digest_that_is_not_a_text(self):
        self.refuse_command("invalid_input", "bind-verdict",
                            {"id": "verdict-1", "task_digest": [], "body": {}, "assessment": {}})

    def test_read_batch_refuses_an_item_whose_version_is_not_a_text(self):
        error = self.refuse_command("invalid_batch", "read-batch", {"id": "batch-1", "depth": "abstract", "items": [
            {"version_id": [], "note": "A note.", "notes": {}}]})
        self.assertEqual([item["code"] for item in error.details["items"]], ["invalid_batch"])

    def test_read_batch_refuses_a_repeated_version_before_it_assesses_the_item(self):
        item = {"version_id": "absent", "note": "A note.", "notes": {}}
        error = self.refuse_command("invalid_batch", "read-batch", {"id": "batch-1", "depth": "abstract",
                                                                    "items": [item, item]})
        self.assertEqual([row["code"] for row in error.details["items"]], ["invalid_identifier", "invalid_batch"])

    def test_sample_refuses_a_collection_that_is_not_a_text(self):
        self.put("configuration", "research", {"profile": "verification", "preparation_policy": {"id": "sampled-v1"}})
        self.refuse_command("invalid_input", "sample", {"id": "sample-1", "collection_id": [], "size": 1, "seed": "seed-1"})

    def test_an_import_and_an_acquisition_refuse_a_provider_that_is_not_a_text(self):
        with self.subTest(command="import-response"):
            self.assert_refused("unsupported_provider", acquisition.import_response, self.store, [], b"{}",
                                source_url="https://example.org/search", captured_at="2026-09-07T12:00:00Z",
                                expected_revision=self.store.revision, request_id="import-1")
        with self.subTest(command="acquire"):
            self.assert_refused("unsupported_provider", acquisition.acquire_work, self.store, "arxiv:2601.00001v1",
                                provider=["arxiv"], expected_revision=self.store.revision, request_id="acquire-1")


class ReviewCommandShapeTests(PayloadShapeCase):
    def test_a_record_is_found_by_its_text_id_only(self):
        self.put("review_epoch", "epoch-1", {"id": "epoch-1", "controller_assignment_id": "controller-1"})
        for records in (self.store.snapshot()["records"], self.store._read_scoped_preparation().records):
            self.assertEqual(operations.find_record(records, "review_epoch", "epoch-1")["controller_assignment_id"],
                             "controller-1")
            for identifier in ("missing", None, 1, True, [], {}, ["epoch-1"]):
                with self.subTest(identifier=identifier):
                    self.assertIsNone(operations.find_record(records, "review_epoch", identifier))

    def test_review_commands_refuse_an_assignment_or_route_that_is_not_a_text(self):
        for command, member in (("review-attempt", "assignment_id"), ("review-context", "assignment_id"),
                                ("review-adjudication", "assignment_id"), ("review-run", "assignment_id"),
                                ("review-output-correction", "assignment_id"), ("review-probe", "route_id")):
            with self.subTest(command=command):
                self.refuse_command("review_record_missing", command, self.build_example(command, **{member: []}))

    def test_a_review_command_goes_on_with_a_stored_assignment(self):
        self.put("review_assignment", "assignment-1", {"id": "assignment-1"})
        self.refuse_command("invalid_review_context", "review-context",
                            self.build_example("review-context", assignment_id="assignment-1", kind="unknown"))

    def test_review_context_refuses_an_evidence_reference_of_another_shape(self):
        self.put("review_assignment", "assignment-1", {"id": "assignment-1"})
        payload = self.build_example("review-context", assignment_id="assignment-1")
        payload["evidence"] = [dict(payload["evidence"][0], sha256=[])]
        self.refuse_command("invalid_input", "review-context", payload)

    def test_review_route_refuses_a_configuration_that_is_not_an_object(self):
        self.refuse_command("invalid_review_route", "review-route",
                            self.build_example("review-route", configuration=None))

    def test_a_staged_review_run_refuses_an_epoch_or_task_that_is_not_a_text(self):
        self.put("review_epoch", "epoch-1", {"id": "epoch-1", "controller_assignment_id": "controller-1"})
        self.put("review_task", "task-1", {"id": "task-1", "epoch_id": "epoch-1"})
        run = {"id": "attempt-1", "assignment_id": "controller-1", "epoch_id": "epoch-1", "task_id": "task-1",
               "action": "accept"}
        with self.subTest(case="a stored epoch and task"):
            self.refuse_command("review_observation_acceptance_pending", "review-run", run)
        for member in ("epoch_id", "task_id"):
            with self.subTest(member=member):
                error, replays = self.count_history_replays("review-run", dict(run, **{member: []}))
                self.assertEqual(error.code, "invalid_input")
                # A malformed epoch is refused before the store is read; a malformed task after the one read of the epoch.
                self.assertEqual(replays, int(member == "task_id"))

    def store_native_packet(self):
        packet = {"protocol": native_review.PROTOCOL, "kind": "result", "root": str(self.root), "objective_id": "o",
                  "contract": {"proof_policy": "analytical"},
                  "subject": {"statement": "Every finite example satisfies the bound."},
                  "reviewer": {"source": "host", "actor_id": "reader", "attestation_id": "review-assignment:a"},
                  "scientific_context": {}, "evidence": [], "execution_provenance": [], "unmaterialized_digests": []}
        packet.update(subject_digest=digest(packet["subject"]), claim_digest=digest(packet["subject"]))
        return self.artifacts.put(json.dumps(packet).encode(), "application/json")

    def test_review_assignment_refuses_a_context_or_an_id_of_another_type(self):
        self.mutate(cli.OPERATIONS["review-route"], ROUTE)
        base = {"id": "a", "route_id": ROUTE["id"], "dossier_id": None, "reviewer_id": "reader", "author_id": "author"}
        native = dict(base, role="native_math", context={"native_packet": self.store_native_packet()})
        for name, payload in (
                ("adjudicator predecessor", dict(base, role="adjudicator", context={"prior_assignment_id": []})),
                ("standalone context", dict(base, role="standalone", context=None)),
                ("verification task", dict(base, role="verification", context={"verification_task_id": []})),
                ("native predecessor", dict(native, context=dict(native["context"], prior_assignment_id=[])))):
            with self.subTest(case=name):
                self.refuse_command("invalid_review_context", "review-assignment", payload)
        # A predecessor that names no assignment is no form error, as in 0.92.0: the adjudicator goes on to its dossier.
        for prior in ("", False, 0):
            with self.subTest(case="adjudicator predecessor", prior=prior):
                self.refuse_command("review_record_missing", "review-assignment",
                                    dict(base, role="adjudicator", context={"prior_assignment_id": prior}))
        with self.subTest(case="native assignment with a null predecessor"):
            self.mutate(cli.OPERATIONS["review-assignment"], dict(native, context=dict(native["context"],
                                                                                        prior_assignment_id=None)))
            self.assertIn("a", self.store.snapshot()["records"]["review_assignment"])

    def test_paper_assignment_selectors_and_evidence_of_another_shape_are_refused(self):
        self.mutate(cli.OPERATIONS["review-route"], ROUTE)
        reference = self.artifacts.put(b"Primary material.", "text/plain")
        paper = {"id": "paper", "route_id": ROUTE["id"], "dossier_id": None, "role": "standalone", "reviewer_id": "reader",
                 "author_id": "author"}
        for name, context, code in (
                ("bundle id", {"bundle": {"id": []}}, "review_bundle_mismatch"),
                ("artifact digest", {"artifact_refs": [dict(reference, sha256=[])]}, "invalid_input"),
                ("reading replacement", {"output_refusal": {"assignment_id": "a"}, "reading_replacement": []},
                 "review_record_missing")):
            with self.subTest(member=name):
                self.refuse_command(code, "review-assignment", dict(paper, context=context))
        self.mutate(cli.OPERATIONS["review-assignment"], dict(paper, context={"artifact_refs": [reference]}))
        # Metadata beside a delivered descriptor reads as before; only a text digest names a result artifact.
        result = {"kind": "result", "execution_id": "e", "output_id": "o", "locator": "l", "artifact": dict(reference, sha256=[])}
        event = {"id": "event", "assignment_id": "paper", "kind": "observation", "source": "test", "reason": "Observed.",
                 "evidence": [dict(reference, extra=result)]}
        revision = self.store.revision
        self.mutate(cli.OPERATIONS["review-context"], event)
        self.assertEqual(self.store.revision, revision + 1)


# The focused adjudicator of the two recorded bar reviews of tests/test_research_review_protocol.py.
FOCUSED = {"route_id": "route", "dossier_id": "dossier", "role": "adjudicator", "reviewer_id": "third",
           "author_id": "author", "context": {"review_ids": ["review-first", "review-second"],
                                              "objection_id": "objection-first"}}
# An adjudication output that names another objection: its review-adjudication is refused.
REFUSED_ADJUDICATION = {"objection_id": "wrong-objection", "disposition": "unresolved",
                        "reason": "The finding remains open.", "evidence": [], "correction_admissible": None}


def refuse_adjudication(case, attempt_id, assignment_id):
    """Let the fixture transport return an adjudication that is refused, as the planner of a replacement needs."""
    case.invoke(REFUSED_ADJUDICATION, attempt_id, assignment_id)
    with case.assertRaises(ResearchError):
        case.mutate(cli.OPERATIONS["review-adjudication"],
                    dict(REFUSED_ADJUDICATION, id="finding", assignment_id=assignment_id))


class FixtureStateShapeTests(unittest.TestCase):
    """Payloads on the stored states that the fixtures of other test modules build."""
    def setUp(self):
        from research_harness import bar_sources, review_protocol
        released = patch.object(review_protocol, 'NEW_SESSION_RULES', (bar_sources.PROTOCOL_V3,))
        released.start()
        self.addCleanup(released.stop)
        super().setUp()

    def test_bind_verdict_refuses_an_assignment_that_is_not_a_text_on_a_bound_task(self):
        case = start_fixture(self, test_research_verification.ResearchVerificationTests)
        api = case.verifier()
        task = case.mutate(api.record_task, {"task": case.task})["result"]
        payload = case.build_bind_payload(task["digest"], case.verdict(), observed=False)
        for identifier, code in ((None, "review_assignment_missing"), ([], "invalid_input")):
            with self.subTest(assignment_id=identifier):
                payload["assessment"]["assignment_id"] = identifier
                revision = case.store.revision
                with self.assertRaises(ResearchError) as caught:
                    case.mutate(cli.OPERATIONS["bind-verdict"], payload)
                self.assertEqual(caught.exception.code, code, caught.exception.as_dict())
                self.assertEqual(case.store.revision, revision)

    def test_a_staged_child_refuses_a_task_that_is_not_a_text(self):
        frontier = start_fixture(self, test_review_staged_frontier.FrontierTests)
        controller = frontier.create_controller()
        with self.assertRaises(ResearchError) as caught:
            frontier.case.assign("result", "first", identifier="child", controller_assignment_id=controller["id"],
                                 task_id=[])
        self.assertEqual(caught.exception.code, "review_task_pending")
        child = frontier.case.assign("result", "first", identifier="child", controller_assignment_id=controller["id"],
                                     task_id=controller["staged_review"]["task_ids"][0])
        self.assertEqual(child["id"], "child")

    def test_a_terminal_probe_refuses_a_resubmission_with_another_member(self):
        probe = start_fixture(self, test_research_probe.ProbeTests)
        probe.record_intent()
        outcome = build_probe_outcome(probe.artifacts)
        probe.mutate(cli.OPERATIONS["probe"], outcome)
        probe.mutate(cli.OPERATIONS["probe"], outcome)
        for name, member in (("program_run", None), ("capture_failure", {"code": "capture", "program_exit_status": 0})):
            with self.subTest(member=name):
                with self.assertRaises(ResearchError) as caught:
                    probe.mutate(cli.OPERATIONS["probe"], dict(outcome, **{name: member}))
                self.assertEqual(caught.exception.code, "record_conflict")

    def test_an_empty_predecessor_is_admitted_and_its_planned_replacement_advances(self):
        case = start_fixture(self, test_research_review_protocol.ReviewProtocolTests)
        case.bar_reviews()
        focused = dict(FOCUSED, id="focused", context=dict(FOCUSED["context"], prior_assignment_id=""))
        with self.subTest(case="a review id that is an array"):
            malformed = dict(FOCUSED, id="malformed", context=dict(FOCUSED["context"], review_ids=["review-first", []]))
            with self.assertRaises(ResearchError) as caught:
                case.mutate(cli.OPERATIONS["review-assignment"], malformed)
            self.assertEqual(caught.exception.code, "review_packet_incomplete")
        with self.subTest(case="an empty predecessor"):
            case.mutate(cli.OPERATIONS["review-assignment"], focused)
            refuse_adjudication(case, "refused", "focused")
            plan = review_continuation.plan_next(case.store.snapshot()["records"], case.artifacts, "focused")
            self.assertEqual((plan["state"], plan["command"]), ("replace", "review-assignment"))
            self.assertEqual(plan["payload"]["context"]["prior_assignment_id"], "")
            revision = case.store.revision
            case.mutate(cli.OPERATIONS[plan["command"]], plan["payload"])
            self.assertEqual(case.store.revision, revision + 1)
            stored = case.store.snapshot()["records"]["review_assignment"][plan["payload"]["id"]]
            self.assertEqual(stored["context"], plan["payload"]["context"])

    def test_a_focused_adjudication_takes_the_review_id_containers_of_0_92_0(self):
        """0.92.0 admits the two review ids as the keys of an object or the characters of a text, and keeps the
        container. A fresh assignment takes both, and the assessor of step 3 of a slot whose two refusal replacements
        are spent rebuilds the focused packet from the copied object and advances the record."""
        case = start_fixture(self, test_research_review_protocol.ReviewProtocolTests)
        case.bar_reviews()
        reviews = case.records()["value_review"]
        for identifier, source in (("a", "review-first"), ("b", "review-second")):
            case.seed("value_review", identifier, dict(reviews[source], id=identifier,
                                                       payload=dict(reviews[source]["payload"], id=identifier)))
        by_keys = {"review-first": 1, "review-second": 1}
        # One focused slot per objection: the text form takes the objection of the second review.
        for name, ids, objection in (("object", by_keys, "objection-first"), ("text", "ab", "objection-second")):
            with self.subTest(review_ids=name):
                payload = dict(FOCUSED, id="focused-" + name, context={"review_ids": ids, "objection_id": objection})
                case.mutate(cli.OPERATIONS["review-assignment"], payload)
                self.assertEqual(case.records()["review_assignment"][payload["id"]]["context"], payload["context"])
        for ids in (None, False, 2, "abc", ["review-first", "review-first"], ["review-first", []],
                    ["review-first", {}]):
            with self.subTest(review_ids=ids):
                with self.assertRaises(ResearchError) as caught:
                    case.mutate(cli.OPERATIONS["review-assignment"], dict(
                        FOCUSED, id="malformed", context=dict(FOCUSED["context"], review_ids=ids)))
                self.assertEqual(caught.exception.code, "review_packet_incomplete")
        head = "focused-object"
        for number in (1, 2, 3):
            if number > 1:
                replacement = review_continuation.plan_next(case.records(), case.artifacts, "focused-object")
                self.assertEqual((replacement["state"], replacement["command"]), ("replace", "review-assignment"))
                head = case.mutate(cli.OPERATIONS["review-assignment"], replacement["payload"])["result"]["id"]
            refuse_adjudication(case, "refused-" + str(number), head)
        plan = review_continuation.plan_next(case.records(), case.artifacts, "focused-object")
        self.assertEqual((plan["state"], plan["code"], plan["command"]),
                         ("handoff", "review_slot_exhausted", "review-assignment"))
        step = plan["details"]["step"]
        self.assertEqual((step["id"], step["payload"]["context"]),
                         (3, {"review_ids": by_keys, "objection_id": "objection-first"}))
        revision = case.store.revision
        case.mutate(cli.OPERATIONS["review-assignment"], dict(step["payload"], id="new-focus", route_id="route",
                                                              reviewer_id="fourth"))
        self.assertEqual(case.store.revision, revision + 1)
        self.assertEqual(case.records()["review_assignment"]["new-focus"]["context"]["review_ids"], by_keys)
        self.assertNotEqual(review_continuation.plan_next(case.records(), case.artifacts, "focused-object"), plan)


class AttemptAndSearchShapeTests(PayloadShapeCase):
    def test_a_literature_attempt_refuses_a_reading_id_that_is_not_a_text(self):
        self.initialize_problem()
        self.select_statement()
        attempt = build_attempt(reading=self.record_full_reading())
        malformed = copy.deepcopy(attempt)
        malformed["literature"]["readings"][0]["reading_id"] = []
        self.refuse_command("problem_value_reading_missing", "problem-attempt", malformed)
        self.mutate(cli.OPERATIONS["problem-attempt"], attempt)

    def test_a_captured_search_refuses_a_disposition_work_that_is_not_a_text(self):
        version = self.metadata()
        search = dict(self.capture_search(found=[version], verdict="nothing-new"), question="Which works bound it?")
        malformed = copy.deepcopy(search)
        malformed["dispositions"][0]["work_id"] = []
        self.refuse_command("invalid_search", "search", malformed)
        self.mutate(cli.OPERATIONS["search"], search)


class ProblemCommandShapeTests(PayloadShapeCase):
    def setUp(self):
        super().setUp()
        self.initialize_problem()

    def test_problem_decision_refuses_an_action_that_is_not_a_text(self):
        self.refuse_command("invalid_input", "problem-decision", {"id": "decision-1", "action": [], "review_ids": [],
                                                                  "bar_review_ids": [], "reason": "A reason."})

    def test_problem_formal_check_refuses_a_closure_digest_that_is_not_a_text(self):
        self.refuse_command("invalid_input", "problem-formal-check", {
            "evidence": {"execution_id": "execution-1", "output_id": "output-1"}, "native_closure_digest": []})

    def test_value_review_refuses_an_assignment_that_is_not_a_text_without_another_store_read(self):
        error, replays = self.count_history_replays("value-review", self.build_example("value-review", assignment_id=[]))
        self.assertEqual(error.code, "invalid_value_review")
        # The one preparation of the command; the refusal records no output for an id that names none.
        self.assertEqual(replays, 1)

    def test_problem_openness_refuses_an_assignment_that_is_not_a_text(self):
        self.refuse_command("invalid_input", "problem-openness", {"assignment_id": []})

    def test_the_openness_export_refuses_searches_and_ids_that_have_another_shape(self):
        self.select_statement()
        minimum = {"searches": [{"formulation": "The exact statement.", "search_id": "search-1"}],
                   "settling_work_id": None, "duplicate_resolutions": []}
        for name, changed in (("searches", dict(minimum, searches=None)),
                              ("search_id", dict(minimum, searches=[dict(minimum["searches"][0], search_id=[])])),
                              ("settling_work_id", dict(minimum, searches=[], settling_work_id=[]))):
            with self.subTest(member=name):
                self.assert_refused("invalid_input", problem_openness.export_openness, self.store, {
                    "assignment_id": "openness-1", "reviewer_id": "reviewer-1", "minimum": changed})
