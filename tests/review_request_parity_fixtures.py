"""Small stored-workspace replays for the ceb050b request parity oracle.

Fixture construction retains the original unversioned request and invocation
policies, and fixes UUIDs, transport runtimes and observation time.
No packet, request, scientific assessment or authentication function is replaced
while collecting the oracle. See review_request_parity.md for the exclusions.
"""

from contextlib import contextmanager, ExitStack
import copy
from datetime import datetime, timezone
import gzip
import hashlib
import itertools
import json
from pathlib import Path
import uuid
from unittest.mock import patch

import codex_route_fixtures as codex_fixtures
import problem_parts_fixtures as legacy_parts
import review_parts_fixtures as current_parts
import test_openness_search_sharing as v5_fixtures
import test_openness_v4_replay as v4_fixtures
import test_problem_openness_native_reviews as native_fixtures
import test_problem_result_history as history_fixtures
import test_research_review_protocol as ordinary_fixtures
from research_harness import cli, gates, openness_reading, review_protocol as protocol
from research_harness import review_request_evaluation, review_transport as transport
from research_harness import review_codex_invocation, review_request_restoration
from research_harness import review_codex_transport as codex
from research_harness.errors import ResearchError
from research_harness.evaluation import Evaluation
from research_harness.evidence import digest
from research_harness.review_bodies import read_history


BASE_COMMIT = "ceb050b26e6bdc372d06e6003836e1e3d8fc0a82"
FROZEN_PATH = Path(__file__).with_name("fixtures") / "review-request-parity-0.72.1.json.gz"


class FixtureDatetime(datetime):
    @classmethod
    def now(cls, tz=None):
        return cls(2026, 11, 3, 12, tzinfo=timezone.utc).astimezone(tz)


def serialize_like_put_json(value):
    """The exact serialization used by review_protocol._put_json, including spaces."""
    return json.dumps(value, sort_keys=True, ensure_ascii=False, allow_nan=False).encode("utf-8")


def compute_fingerprint(data):
    return {"sha256": hashlib.sha256(data).hexdigest(), "bytes": len(data)}


def record_outcome(call):
    try:
        return {"value": call()}
    except ResearchError as error:
        return {"error": error.as_dict()}


def build_legacy_codex_response(route, request, output):
    """Keep the native response shape that the ceb050b oracle recorded."""
    response = {'id': 'provider-response', 'model': 'fixture-model', 'status': 'completed',
                'output': [{'type': 'message', 'role': 'assistant',
                            'content': [{'type': 'output_text', 'text': json.dumps(output)}]}],
                'usage': {'input_tokens': 37, 'output_tokens': 12}}
    events = [{'type': 'session_meta', 'payload': {'id': response['id'], 'source': 'exec',
               'base_instructions': {'text': request['instructions']}}},
              {'type': 'response_item', 'payload': {'type': 'message', 'role': 'user',
               'content': [{'type': 'input_text', 'text': codex.packet_text(request)}]}}]
    evidence = codex.inspect_rollout(events, request, response['id'], '/fixture', [])
    evidence.update(inspected=True, route_digest=digest(route), runtime=codex.runtime(), event_digest=digest(events))
    response['context_evidence'] = evidence
    return response


@contextmanager
def fix_external_inputs():
    """Fix external observations before storing them, rather than scrubbing requests."""
    original_runtime = transport.runtime
    numbers = itertools.count(1)
    emitted = []
    original_observe = transport.observe

    def describe_fixture_runtime(route=None):
        if route and route["adapter"] != transport.ADAPTER:
            return original_runtime(route)
        return {"adapter": transport.ADAPTER, "fixture": "request-parity"}

    def observe(route, request, credential):
        emitted.append(dict(route_id=route["id"], request=compute_fingerprint(serialize_like_put_json(request))))
        return original_observe(route, request, credential)

    with patch.object(transport, "runtime", side_effect=describe_fixture_runtime), \
            patch.object(codex_fixtures, "build_codex_response", new=build_legacy_codex_response), \
            patch.object(review_codex_invocation, "supports_route", return_value=False), \
            patch.object(review_request_restoration, "select_policy", return_value=None), \
            patch.object(uuid, "uuid4", side_effect=lambda: uuid.UUID(int=next(numbers))), \
            patch("time.monotonic", return_value=1000.0), \
            patch("research_harness.problem_claims.datetime", FixtureDatetime), \
            patch("research_harness.integration.datetime", FixtureDatetime), \
            patch.object(transport, "observe", side_effect=observe):
        yield emitted


@contextmanager
def open_fixture(kind, **attributes):
    case = kind()
    for key, value in attributes.items():
        setattr(case, key, value)
    try:
        case.setUp()
        yield case
    finally:
        case.doCleanups()


def capture(case, emitted):
    """Freeze complete reports, selection and authentication, and exact artifact bytes."""
    before = case.store.snapshot()
    records = before["records"]
    evaluation = Evaluation(records, case.artifacts)
    status = record_outcome(lambda: cli.status_report(case.store, counters=True))
    if "value" in status:
        # status.runtime describes the source revision, dirtiness, executable
        # path and package hashes; it varies even between clean exports.
        status["value"].pop("runtime")
        # status.evaluation contains invocation counters, retention and elapsed
        # time. Release 0.72.2 also adds it to the problem branch.
        status["value"].pop("evaluation", None)
    result = dict(status=status, gate=record_outcome(lambda: gates.gate_report(case.store, "post"
                  if "problem" in records.get("workspace", {}) else "readiness")),
                  states={}, selected={}, authentication={}, rebuilt_requests={}, logical_histories={},
                  stored_packets={}, stored_requests={}, emitted_requests=copy.deepcopy(emitted),
                  refusals=records.get("review_output_refusal", {}), attempts=records.get("review_attempt", {}))
    for identifier, assignment in records.get("review_assignment", {}).items():
        result["states"][identifier] = record_outcome(lambda: protocol.assignment_state(records, evaluation, identifier))
        result["selected"][identifier] = record_outcome(lambda: list(protocol._selected_attempt(records, evaluation, identifier)))
        result["rebuilt_requests"][identifier] = record_outcome(lambda: compute_fingerprint(serialize_like_put_json(
            protocol.build_assignment_request(records, evaluation, assignment))))
        result["logical_histories"][identifier] = record_outcome(lambda: compute_fingerprint(serialize_like_put_json(
            read_history(evaluation, assignment))))
    for identifier, attempt in records.get("review_attempt", {}).items():
        assignment = records["review_assignment"][attempt["assignment_id"]]
        result["authentication"][identifier] = dict(
            provenance=protocol.authenticate_provenance(records, evaluation, assignment, attempt),
            attempt=protocol.authenticate_attempt(records, evaluation, assignment, attempt))

    def visit(value, location):
        if isinstance(value, dict):
            for key, child in value.items():
                child_location = location + "/" + key
                if key in ("packet", "native_packet", "request") and isinstance(child, dict) and "sha256" in child:
                    group = "stored_requests" if key == "request" else "stored_packets"
                    # Also pin the physical bytes of corrupted artifacts. A read
                    # through ArtifactStore separately pins the integrity refusal.
                    result[group][child_location] = dict(reference=child,
                        actual=compute_fingerprint((case.root / child["path"]).read_bytes()),
                        read=record_outcome(lambda: compute_fingerprint(case.artifacts.read(child))))
                visit(child, child_location)
        elif isinstance(value, list):
            for index, child in enumerate(value):
                visit(child, location + "/" + str(index))

    visit(records, "records")
    if case.store.snapshot() != before:
        raise AssertionError("Parity evaluation mutated its stored workspace")
    # Match JSON's sequence representation without changing any string or bytes.
    return json.loads(serialize_like_put_json(result))


def build_ordinary_case(variant):
    with fix_external_inputs() as emitted, open_fixture(ordinary_fixtures.ReviewProtocolTests) as case:
        case.route()
        case.run_probe()
        case.assign()
        if variant in ("incomplete", "failed"):
            def respond(route, request, credential):
                if variant == "failed":
                    raise OSError("Fixture transport failure")
                return dict(case.response({}), status="incomplete")
            with patch.object(transport, "send_request", side_effect=respond):
                case.mutate(protocol.invoke_assignment, {"id": variant, "assignment_id": "assignment"},
                            credential="fixture")
            if variant == "incomplete":
                # The provider's incomplete response is retained as a failed
                # observation; also retain the public imported-incomplete shape.
                case.mutate(protocol.record_attempt, {"id": "imported-incomplete", "assignment_id": "assignment",
                    "status": "incomplete", "output": None, "reason": "The interrupted reviewer returned no complete output.",
                    "usage": {"input_tokens": None, "output_tokens": None, "cost_usd": None, "wall_seconds": None}})
        elif variant != "pending":
            case.invoke({"assessment": "The exact supplied evidence is sufficient."})
            if variant == "contaminated":
                case.mutate(protocol.record_context_event, {"id": "contamination", "assignment_id": "assignment",
                    "kind": "contamination", "source": "author_history", "reason": "Foreign author history was observed.",
                    "evidence": [case.artifacts.put(b"Observed foreign input", "text/plain")]})
            elif variant == "corrupted-request":
                reference = case.records()["review_attempt"]["attempt"]["request"]
                artifact = case.root / reference["path"]
                artifact.chmod(0o600)
                artifact.write_bytes(b'{"corrupted":true}')
            elif variant == "forged-provenance":
                attempt = case.records()["review_attempt"]["attempt"]
                case.seed("review_attempt", "attempt", dict(attempt, prompt_hash="0" * 64))
            elif variant == "forged-request":
                from research_harness.evidence import digest
                attempt = case.records()["review_attempt"]["attempt"]
                request = json.loads(case.artifacts.read(attempt["request"]))
                request["instructions"] += " Altered recorded instructions."
                case.seed("review_attempt", "attempt", dict(attempt,
                    request=protocol._put_json(case.artifacts, request), request_digest=digest(request)))
        return capture(case, emitted)


def complete_search_metadata(case):
    """Parts builders seed only review fields; full status also reads search scope."""
    for identifier, search in case.store.snapshot()["records"].get("literature_search", {}).items():
        if "profile" not in search:
            case.put("literature_search", identifier,
                     dict(search, profile="research", purpose="direct", cited_work_ids=[], found_work_ids=[], responses=[]))


def build_earlier_fit_case():
    """Record a real earlier-order request, not a mocked candidate iterator."""
    from research_harness.evidence import digest
    with fix_external_inputs() as emitted, open_fixture(ordinary_fixtures.ReviewProtocolTests) as case:
        codex_fixtures.record_codex_route(case)
        assignment = case.assign()["result"]
        packet = json.loads(case.artifacts.read(assignment["packet"]))
        body = case.artifacts.put(b"Earlier supplied evidence. " * 300, "text/plain")
        entry = dict(artifact=body, encoding="utf-8", content=case.artifacts.read(body).decode())
        historical_packet = dict(evidence=[entry], digest=digest(entry))
        packet["scientific_history"] = [dict(packet=copy.deepcopy(historical_packet))]
        packet.pop("digest")
        packet["digest"] = digest(packet)
        history = [dict(role="user", content=transport.PACKET_LABEL + json.dumps(historical_packet, sort_keys=True)),
                   dict(role="assistant", content='{"assessment":"Earlier independent assessment."}')]
        assignment = dict(assignment, packet=protocol._put_json(case.artifacts, packet),
                          packet_digest=packet["digest"], history=history)
        assignment.pop("history_ref", None)
        case.seed("review_assignment", assignment["id"], assignment)
        records = case.records()
        route = records["review_route"]["route"]
        full = transport.request_body(route, assignment["prompt"]["text"], packet, history, order=())
        limit = len(transport._local_adapter(route).packet_text(full)) - 3000
        codex_fixtures.limit_codex_input(case, limit)
        original = transport.request_body
        earlier_order = transport.EARLIER_FIT_ORDERS[1]

        def build_with_earlier_order(route, prompt, packet, history=(), order=transport.FIT_ORDER):
            return original(route, prompt, packet, history,
                            order=earlier_order if order == transport.FIT_ORDER else order)

        with patch.object(review_request_evaluation, "_build_request", side_effect=build_with_earlier_order):
            codex_fixtures.invoke_codex(case, "assignment", {"assessment": "The earlier fit is retained."})
        records = case.records()
        recorded = json.loads(case.artifacts.read(records["review_attempt"]["attempt-assignment"]["request"]))
        candidates = list(transport.list_built_requests(route, assignment["prompt"]["text"], packet, history))
        if recorded == candidates[0] or recorded not in candidates[1:]:
            raise AssertionError("The recorded request must select a distinct earlier fitting order")
        result = capture(case, emitted)
        result["candidate_requests"] = [compute_fingerprint(serialize_like_put_json(request)) for request in candidates]
        if result["rebuilt_requests"]["assignment"] != {"value": compute_fingerprint(serialize_like_put_json(recorded))}:
            raise AssertionError("Selection must retain the actual recorded earlier-fit request")
        if not result["authentication"]["attempt-assignment"]["attempt"]:
            raise AssertionError("The earlier-fit observation must authenticate")
        return result


def build_local_adapter_case(name):
    from research_harness import review_claude_transport as claude, review_codex_framed_transport as framed
    from research_harness.evidence import digest
    import test_research_claude_transport as claude_fixtures
    import test_research_codex_controls as controls
    with fix_external_inputs() as emitted, open_fixture(ordinary_fixtures.ReviewProtocolTests) as case, ExitStack() as stack:
        if name == "claude":
            specification = dict(claude_fixtures.ROUTE, id="route")
        else:
            stack.enter_context(patch.object(framed, "runtime", return_value={
                "adapter": framed.ADAPTER, "fixture": "request-parity"}))
            specification = dict(controls.CodexControlTests().build_route(framed), id="route")
        case.mutate(protocol.record_route, specification)

        def respond(route, request, credential):
            supplied = json.loads(request["input"][-1]["content"][len(transport.PACKET_LABEL):])
            output = ({key: supplied[key] for key in ("allowed_control", "evidence_control")}
                      if "allowed_control" in supplied else {"assessment": "Inspected exact local input."})
            if "allowed_control" in supplied:
                output["excluded_controls"] = []
            if name == "claude":
                events = claude_fixtures.clean_stream(request, json.dumps(output), model=route["model"])
                with patch.object(claude, "_cli", return_value=("/fixture/claude", "2.1.306")), \
                        patch.object(claude, "_run_owned", return_value=claude_fixtures.completed(events)):
                    return claude.send_request(route, request)
            evidence = controls.CodexControlTests().inspect_request(framed, request)
            evidence.update(inspected=True, route_digest=digest(route), runtime=framed.runtime(),
                            event_digest=evidence["rollout_digest"])
            return dict(case.response(output), id=evidence["thread_id"], context_evidence=evidence)

        with patch.object(transport, "send_request", side_effect=respond):
            case.mutate(protocol.probe_route, {"id": "probe", "route_id": "route"}, credential="fixture")
            case.assign()
            case.mutate(protocol.invoke_assignment, {"id": "attempt", "assignment_id": "assignment"}, credential="fixture")
        result = capture(case, emitted)
        if not result["authentication"]["attempt"]["attempt"]:
            raise AssertionError("The local adapter fixture must authenticate")
        return result


def build_result_case(version, corrupted_history=False):
    kind = legacy_parts.ResultPartsCase if version == 1 else current_parts.PartsResultCase
    with fix_external_inputs() as emitted, open_fixture(kind) as parts:
        if version == 1:
            parts.oversized(count=2, characters=50000, limit=105000)
            turns, final = parts.finish_parts()
        else:
            parts.oversized(count=2, characters=50000, room=60000)
            turns, final = parts.finish_series()
        if not turns:
            raise AssertionError("The result fixture must contain a multipart series")
        parts.record_final(final)
        complete_search_metadata(parts.case)
        if corrupted_history:
            reference = final["history_ref"]["pages"][-1]
            artifact = parts.case.root / reference["path"]
            artifact.chmod(0o600)
            artifact.write_bytes(b'{"corrupted":true}')
        return capture(parts.case, emitted)


def build_native_case(variant):
    with fix_external_inputs() as emitted, open_fixture(native_fixtures.NativeOpennessReviewTests) as case:
        if variant in ("refused", "refused-retry"):
            case.observe_support("refused", change_output=lambda output: output["findings"].update(statement={"bad": "shape"}))
            if variant == "refused-retry":
                assignment = case.store.snapshot()["records"]["review_assignment"]["refused"]
                packet = json.loads(case.artifacts.read(assignment["packet"]))["native"]
                with patch.object(transport, "send_request", return_value=case.build_provider_response(case.output(packet))):
                    case.mutate(protocol.invoke_assignment, {"id": "accepted-retry", "assignment_id": "refused"}, credential="fixture")
        elif variant == "corrupted-packet":
            reference = case.support["assignment"]["packet"]
            artifact = case.root / reference["path"]
            artifact.chmod(0o600)
            artifact.write_bytes(b'{"corrupted":true}')
        return capture(case, emitted)


def build_openness_case(version):
    with fix_external_inputs() as emitted:
        if version == 4:
            with open_fixture(v4_fixtures.V4ReplayTests) as replay:
                # The existing replay builds the actual c408fea v4 protocol,
                # then continues it under current code with selected proof bytes.
                export = replay.fixture.export
                # The workspace's temporary pathname is an input to the native
                # packet. Give that fixture input a stable synthetic root before
                # any assignment or observed request is recorded.
                def export_with_fixed_root(*args, **kwargs):
                    return dict(export(*args, **kwargs), root="/fixture/request-parity")
                with patch.object(replay.fixture, "export", side_effect=export_with_fixed_root), \
                        patch.object(replay.released.review_excerpts, "TURN_FULLTEXT_CHARACTERS", 3000), \
                        patch.object(protocol, "openness_reading", replay.released):
                    reference = replay.start()
                turns = replay.finish(reference)
                case = replay.case
                case.mutate(case.load_problem_module().record_openness, {"assignment_id": turns[-1]})
                return capture(case, emitted)
        with open_fixture(v5_fixtures.SearchSharingCase) as case:
            native = json.loads(case.artifacts.read(case.export(priority=case.versions)))
            native["root"] = "/fixture/request-parity"
            reference = protocol._put_json(case.artifacts, native)
            case.assign("turn-1", reference)
            turns = case.complete(reference)
            packet = case.read_packet(turns[-1])
            if openness_reading.find_protocol(packet) != openness_reading.PROTOCOL_V5:
                raise AssertionError("Expected a v5 reading chain")
            case.invoke(turns[-1], case.build_native_result(packet))
            case.record_openness(turns[-1])
            return capture(case, emitted)


def build_history_case():
    with fix_external_inputs() as emitted, ExitStack() as stack:
        builder = history_fixtures.ProblemResultHistoryTests()
        stack.callback(builder.doCleanups)
        # Reuse the legacy branch of record_fixtures without recording its ten
        # unrelated siblings. 0.69.18 observed calls before durable captures.
        import importlib
        captures = importlib.import_module("research_harness.review_call_capture")
        held = {}

        def keep(store, operation, payload, invocation, route, observation, **kwargs):
            held[operation, payload["id"]] = invocation, observation

        def read(records, artifacts, operation, payload, request_id):
            return held[operation, payload["id"]]

        with patch.object(captures, "capture_call", side_effect=keep), \
                patch.object(captures, "read_call_observation", side_effect=read), \
                patch.object(protocol, "COMMON_VERSION", 2):
            legacy_parts.ResultPartsCase.setUp(builder)
            codex_fixtures.install_codex_runtime(builder)
            builder.record_series((2, 2, 2), 300000, note_size=12000, limit=500000, final_size=14000)
        old = builder.closed[-1][1]
        frozen = json.loads(gzip.decompress(FROZEN_PATH.with_name("problem_result_history_06918.json.gz").read_bytes()))
        if serialize_like_put_json(builder.request(old)) != frozen["canonical_request"].encode():
            raise AssertionError("The 0.69.18 request must still match its independently stored fixture")
        complete_search_metadata(builder.case)
        return capture(builder.case, emitted)


CASE_BUILDERS = {
    **{name: (lambda name=name: build_ordinary_case(name)) for name in (
        "pending", "completed", "incomplete", "failed", "contaminated", "corrupted-request", "forged-provenance",
        "forged-request")},
    "result-parts-v1": lambda: build_result_case(1),
    "result-parts-v2": lambda: build_result_case(2),
    "corrupted-history": lambda: build_result_case(1, corrupted_history=True),
    "native-math": lambda: build_native_case("completed"),
    "native-refused": lambda: build_native_case("refused"),
    "native-refused-retry": lambda: build_native_case("refused-retry"),
    "corrupted-packet": lambda: build_native_case("corrupted-packet"),
    "openness-v4": lambda: build_openness_case(4),
    "openness-v5": lambda: build_openness_case(5),
    "result-history-06918": build_history_case,
    "recorded-earlier-fit": build_earlier_fit_case,
    "claude-local": lambda: build_local_adapter_case("claude"),
    "codex-framed-local": lambda: build_local_adapter_case("framed"),
}
