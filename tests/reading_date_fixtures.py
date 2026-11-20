"""Deterministic synthetic producers for the base-frozen reading-date controls."""

from contextlib import ExitStack, contextmanager
from datetime import datetime, timezone
import os
import time
from itertools import count
from types import SimpleNamespace
from uuid import UUID
from unittest.mock import patch

from research_harness import problem_forming, problem_forming_import
from research_harness.evidence import digest
from test_problem_accounts import ReadingDateCase
from test_problem_forming_evidence import build_evidence
from test_research_problem_claims import ProblemCase
from test_research_problem_forming import build_forming, build_outcome
from test_research_problem_candidates import build_candidate
from test_research_problem_delivery import ProblemDeliveryCase


class FrozenClock(datetime):
    @classmethod
    def now(cls, tz=None):
        value = cls(2026, 11, 14, 14, tzinfo=timezone.utc)
        return value if tz is None else value.astimezone(tz)


class FixtureProcessOS:
    def getpid(self):
        return 1001

    def __getattr__(self, name):
        return getattr(os, name)


@contextmanager
def freeze_recording_clocks():
    with ExitStack() as stack:
        for module in ("problem_claims", "problem_forming", "problem_forming_import", "review_protocol"):
            stack.enter_context(patch("research_harness." + module + ".datetime", FrozenClock))
        stack.enter_context(patch("research_harness.storage.Store.describe_runtime", return_value={"fixture": "reading-date"}))
        stack.enter_context(patch("research_harness.acquisition.runtime_provenance", return_value={"fixture": "reading-date"}))
        stack.enter_context(patch("research_harness.review_task_execution.runtime_provenance", return_value={"fixture": "reading-date"}))
        stack.enter_context(patch("research_harness.review_worker.os", FixtureProcessOS()))
        stack.enter_context(patch("research_harness.review_worker._identity", return_value=dict(pid=1001, pgid=1001, started="synthetic")))
        stack.enter_context(patch("research_harness.review_worker.uuid", SimpleNamespace(uuid4=lambda: UUID(int=1))))
        timer = SimpleNamespace(monotonic=lambda: 1.0, sleep=time.sleep)
        stack.enter_context(patch("research_harness.review_protocol.time", timer))
        stack.enter_context(patch("research_harness.review_transport.time", timer))
        controls = count(100)
        stack.enter_context(patch("research_harness.review_protocol.uuid", SimpleNamespace(uuid4=lambda: UUID(int=next(controls)))))
        yield


def build_synthetic_performer(case):
    return dict(kind="model_lane", lane_id="synthetic-lane", model="fixture-model", effort="high",
                prompt=case.artifacts.put(b"Form a finite bound from the synthetic examples.", "text/plain"),
                output=case.artifacts.put(b"For every n, B(n) <= n^2.", "text/plain"))


def record_stored_gain(case):
    """Record the M7 shape; the base accepts its typed dates without dating a reading."""
    ReadingDateCase.setUp(case)
    # Use the public forming writer with retained synthetic performer bytes.
    reading = case.store.snapshot()["records"]["reading"][case.reading["reading_id"]]
    payload = build_forming(performer=build_synthetic_performer(case))
    payload["question"]["kind"] = "thought_experiment"
    payload["forms"][0]["grounds"][0]["evidence"] = [dict(kind="record", record_kind="reading", id=reading["id"], digest=digest(reading))]
    # The fixture was captured before 0.91.3, which accepted the kind in every record; record the call as it did.
    with patch.object(problem_forming, "RETIRED_KINDS", ()):
        call = case.mutate(problem_forming.record_forming, payload)["result"]
    candidate = build_candidate()
    candidate["source"]["evidence"] = [dict(kind="record", record_kind="forming_call", id=call["id"], digest=digest(call))]
    from research_harness import problem_candidates
    case.mutate(problem_candidates.record_candidate, candidate)
    case.statement = case.select_statement(identifier="formed-statement", candidate_id=candidate["id"])
    case.value["statement_id"] = case.statement["id"]
    from test_research_problem_attempt import build_native_attempt, build_acceptance, put_native_attempt
    from test_research_problem_objective_results import build_objective_result
    native = build_native_attempt(case.statement["statement"], [build_acceptance()], {})
    native["attack_root"] = "synthetic-attack"
    result = build_objective_result(put_native_attempt(case, native))
    case.value["accounts"]["explanatory_use"][0].update(evidence=[case.reading, result], date="2026-11-15")
    ReadingDateCase.add_gain(case)
    saved = ReadingDateCase.record(case)
    return saved


class StoredGainCase(ReadingDateCase):
    prepare_review_route = ProblemDeliveryCase.prepare_review_route
    build_provider_response = ProblemDeliveryCase.build_provider_response
    build_review_output = ProblemDeliveryCase.build_review_output
    observe = ProblemDeliveryCase.observe
    deliver_supporting = ProblemDeliveryCase.deliver_supporting
    assign = ProblemDeliveryCase.assign
    observe_bars = ProblemDeliveryCase.observe_bars


def record_reading_import(owner):
    """Return an import and its unchanged source; SOURCE is its portable path slot."""
    source, destination = ProblemCase(), ProblemCase()
    for case in (source, destination):
        case.setUp()
        owner.addCleanup(case.doCleanups)
    source.initialize_problem(slug="synthetic-source")
    destination.initialize_problem(slug="synthetic-destination")
    reading = source.record_full_reading()
    payload = build_forming(performer=build_synthetic_performer(source))
    payload["question"]["kind"] = "thought_experiment"
    # The fixture was captured before 0.91.3, which accepted the kind in every record; record the call as it did.
    with patch.object(problem_forming, "RETIRED_KINDS", ()):
        call = source.mutate(problem_forming.record_forming, payload)["result"]
    candidate = build_candidate()
    candidate["source"]["evidence"] = [dict(kind="record", record_kind="forming_call", id=call["id"], digest=digest(call))]
    from research_harness import problem_candidates
    source.mutate(problem_candidates.record_candidate, candidate)
    source.mutate(problem_forming.record_form_outcome, build_outcome(standing="found_in_literature", evidence=[build_evidence(reading)]))
    imported = destination.mutate(problem_forming_import.import_forming, dict(id="import-1", source_workspace=str(source.root),
        source_revision=source.store.revision, source_candidate_ids=[candidate["id"]]))["result"]
    return source, destination, imported
