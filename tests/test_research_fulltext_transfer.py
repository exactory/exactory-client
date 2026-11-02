"""File-backed transfer, representation continuity and crash recovery."""

import hashlib
import importlib
import importlib.util
import io
import json
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from research_harness.artifacts import ArtifactStore
from research_harness.errors import ResearchError
from research_harness.evidence import prepare_sources
from research_harness.http import DEFAULT_MAX_RESPONSE_BYTES, HttpFailure, RawResponse, RequestBudget
from research_fixtures import Clock, make_http_client


URL = "https://example.org/original"
BODY = b"abcdefghij"


class UnitStore:
    def __init__(self, root):
        self.path = Path(root) / "units.json"
        self.commits = 0
        self.fail_commits = 0
        self.fail_code = "storage_io"

    def read_units(self, job_id):
        return json.loads(self.path.read_text()) if self.path.exists() else []

    def commit_unit(self, job_id, unit):
        self.commits += 1
        if self.fail_commits:
            self.fail_commits -= 1
            raise ResearchError(self.fail_code, "Injected commit failure")
        units = self.read_units(job_id)
        if unit not in units:
            units.append(unit)
        self.path.write_text(json.dumps(units))


class GeneratedStream:
    def __init__(self, size):
        self.remaining = size
        self.largest_read = 0
        self.largest_result = 0

    def read(self, amount):
        if not 0 < amount <= 1024 * 1024:
            raise AssertionError("Body reads must be bounded")
        self.largest_read = max(self.largest_read, amount)
        data = b"x" * min(amount, self.remaining)
        self.remaining -= len(data)
        self.largest_result = max(self.largest_result, len(data))
        return data

    def close(self):
        pass


class RangeWire:
    def __init__(self, *, changed=None):
        self.requests = []
        self.changed = changed

    def __call__(self, url, headers, *, address, timeout):
        self.requests.append((url, dict(headers)))
        start, end = map(int, headers["Range"][6:].split("-"))
        end = min(end, len(BODY) - 1)
        response_headers = {"Content-Range": "bytes %d-%d/10" % (start, end),
                            "Content-Length": str(end - start + 1), "ETag": '"one"',
                            "Content-Type": "application/pdf"}
        body = BODY[start:end + 1]
        if self.changed:
            response_headers.update(self.changed)
        return RawResponse(206, response_headers, io.BytesIO(body), address)


def resume_worker(root, maximum):
    """Exercise an injected durable unit store across separate interpreters."""
    from research_harness.fulltext_transfer import FulltextTransfer
    wire = RangeWire()
    client = make_http_client(transport=wire, resolver=lambda host, timeout: ["93.184.216.34"])
    try:
        result = FulltextTransfer(client, ArtifactStore(Path(root)), UnitStore(root)).run(
            "job", URL, max_original_bytes=10, max_retained_bytes=100,
            range_bytes=4, total_timeout=60, budget=RequestBudget(maximum))
        output = {"sha256": result["response"]["sha256"], "method": result["capture_method"]}
    except HttpFailure as error:
        output = {"error": error.code}
    output["requests"] = wire.requests
    print(json.dumps(output))


class FulltextTransferTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(importlib.util.find_spec("research_harness.fulltext_transfer"),
                             "D6-5 streaming transfer engine is missing")
        self.transfer = importlib.import_module("research_harness.fulltext_transfer")
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.artifacts = ArtifactStore(self.root)
        self.units = UnitStore(self.root)

    def make_engine(self, wire, **kwargs):
        self.client = make_http_client(transport=wire, resolver=lambda host, timeout: ["93.184.216.34"],
                                       max_retries=0, **kwargs)
        return self.transfer.FulltextTransfer(self.client, self.artifacts, self.units)

    def run_engine(self, engine, **kwargs):
        options = dict(max_original_bytes=10, max_retained_bytes=100, range_bytes=4, total_timeout=60)
        options.update(kwargs)
        return engine.run("job", URL, **options)

    def retain_first(self):
        wire = RangeWire()
        engine = self.make_engine(wire)
        with self.assertRaises(HttpFailure) as caught:
            self.run_engine(engine, budget=RequestBudget(1))
        self.assertEqual(caught.exception.code, "request_budget")
        self.assertEqual(len(self.units.read_units("job")), 1)
        return wire

    def test_above_128_mib_original_never_uses_whole_body_reads(self):
        size = DEFAULT_MAX_RESPONSE_BYTES + 1
        stream = GeneratedStream(size)
        engine = self.make_engine(lambda *args, **kwargs: RawResponse(
            200, {"Content-Length": str(size)}, stream, kwargs["address"]))
        result = self.run_engine(engine, max_original_bytes=size, max_retained_bytes=size, range_bytes=1024 * 1024)
        expected = hashlib.sha256()
        for _ in range(128):
            expected.update(b"x" * (1024 * 1024))
        expected.update(b"x")
        self.assertEqual(result["response"]["size"], size)
        self.assertEqual(result["response"]["sha256"], expected.hexdigest())
        self.assertLessEqual(stream.largest_read, 65536)
        self.assertLessEqual(stream.largest_result, 65536)
        self.assertNotIn("body", result["attempts"][0])
        self.artifacts.verify(result["response"])

    def test_second_process_reuses_committed_ranges_and_hashes_whole_original(self):
        outputs = []
        for maximum in (1, 2, 0):
            # CI runs discovery without PYTHONPATH: the child puts the tests directory and the root on its path.
            paths = [str(Path(__file__).resolve().parent), str(Path(__file__).resolve().parents[1])]
            command = [sys.executable, "-c", "import sys; sys.path[:0] = %r; " % paths +
                       "from test_research_fulltext_transfer import resume_worker; "
                       "resume_worker(sys.argv[1], int(sys.argv[2]))", str(self.root), str(maximum)]
            process = subprocess.run(command, capture_output=True, text=True, check=True)
            outputs.append(json.loads(process.stdout))
        self.assertEqual(outputs[0]["error"], "request_budget")
        self.assertEqual([r[1]["Range"] for r in outputs[0]["requests"]], ["bytes=0-3"])
        self.assertEqual([r[1]["Range"] for r in outputs[1]["requests"]], ["bytes=4-7", "bytes=8-9"])
        self.assertEqual([r[1]["If-Range"] for r in outputs[1]["requests"]], ['"one"', '"one"'])
        self.assertEqual(outputs[1]["sha256"], hashlib.sha256(BODY).hexdigest())
        self.assertEqual(outputs[1]["method"], "http_range_v1")
        self.assertEqual(outputs[2]["requests"], [])
        self.assertEqual(outputs[2]["sha256"], outputs[1]["sha256"])

    def test_changed_etag_is_refused(self):
        self.retain_first()
        with self.assertRaises(HttpFailure) as caught:
            self.run_engine(self.make_engine(RangeWire(changed={"ETag": '"two"'})))
        self.assertEqual(caught.exception.code, "fulltext_transfer_changed")
        self.assertEqual(len(self.units.read_units("job")), 1)

    def test_changed_total_is_refused(self):
        self.retain_first()
        with self.assertRaises(HttpFailure) as caught:
            self.run_engine(self.make_engine(RangeWire(changed={"Content-Range": "bytes 4-7/11"})))
        self.assertEqual(caught.exception.code, "fulltext_transfer_changed")

    def test_wrong_or_malformed_ranges_and_lengths_are_refused(self):
        for changed in ({"Content-Range": "bytes 1-4/10"}, {"Content-Range": "bytes 0-4/10"},
                        {"Content-Range": "bytes 0-3/*"}, {"Content-Range": "bytes 0-3/3"},
                        {"Content-Range": "bytes 0-3/10, 4-7/10"}, {"Content-Length": "3"}):
            with self.subTest(changed=changed), self.assertRaises(HttpFailure) as caught:
                self.run_engine(self.make_engine(RangeWire(changed=changed)))
            self.assertEqual(caught.exception.code, "fulltext_transfer_changed")
        self.assertEqual(self.units.read_units("job"), [])

    def test_retained_overlap_and_gap_are_refused_before_network(self):
        self.retain_first()
        first = self.units.read_units("job")[0]
        for start in (2, 5):
            unit = dict(first, start=start, end=start + 3,
                        artifact=self.artifacts.put(BODY[start:start + 4], "application/pdf"))
            self.units.path.write_text(json.dumps([first, unit]))
            wire = RangeWire()
            with self.subTest(start=start), self.assertRaises(HttpFailure) as caught:
                self.run_engine(self.make_engine(wire))
            self.assertEqual(caught.exception.code, "fulltext_transfer_changed")
            self.assertEqual(wire.requests, [])

    def test_unexpected_encoding_is_refused(self):
        wire = RangeWire(changed={"Content-Encoding": "gzip"})
        with self.assertRaises(HttpFailure) as caught:
            self.run_engine(self.make_engine(wire))
        self.assertEqual(caught.exception.code, "unsupported_encoding")
        self.assertEqual(self.units.read_units("job"), [])

    def test_ignored_continuation_starts_separate_complete_transfer_only_if_allowed(self):
        self.retain_first()
        old = self.units.read_units("job")[0]
        requests = []
        def wire(url, headers, **kwargs):
            requests.append(dict(headers))
            return RawResponse(200, {"Content-Length": "10", "ETag": '"two"'}, io.BytesIO(BODY), kwargs["address"])
        with self.assertRaises(HttpFailure) as caught:
            self.run_engine(self.make_engine(wire))
        self.assertEqual(caught.exception.code, "fulltext_resume_unsupported")
        self.assertEqual(self.units.read_units("job"), [old])
        result = self.run_engine(self.make_engine(wire), allow_complete_restart=True)
        self.assertEqual(result["response"]["sha256"], hashlib.sha256(BODY).hexdigest())
        self.assertEqual(result["capture_method"], "http")
        self.assertEqual(result["http_status"], 200)
        self.assertEqual(self.units.read_units("job")[0], old)
        self.assertEqual(len(self.units.read_units("job")), 2)
        self.assertEqual(len(requests), 2)

    def test_redirects_keep_attempt_provenance_and_strip_credentials(self):
        requests = []
        def wire(url, headers, **kwargs):
            requests.append((url, dict(headers)))
            if url == URL:
                return RawResponse(302, {"Location": "https://other.org/file"}, io.BytesIO(b"moved"), kwargs["address"])
            return RawResponse(200, {}, io.BytesIO(BODY), kwargs["address"])
        budget = RequestBudget(2)
        result = self.run_engine(self.make_engine(wire), headers={"Authorization": "Bearer private"}, budget=budget)
        self.assertEqual(budget.used, 2)
        self.assertEqual([a["status"] for a in result["attempts"]], [302, 200])
        self.assertEqual(result["url"], "https://other.org/file")
        self.assertNotIn("Authorization", requests[1][1])
        self.assertNotIn("private", repr(result))
        self.assertEqual(self.artifacts.read(result["attempts"][0]["response"]), b"moved")

    def test_redirect_to_changed_range_url_is_refused(self):
        self.retain_first()
        ranges = RangeWire()
        def wire(url, headers, **kwargs):
            if url == URL:
                return RawResponse(307, {"Location": "/changed"}, io.BytesIO(b""), kwargs["address"])
            return ranges(url, headers, **kwargs)
        budget = RequestBudget(2)
        with self.assertRaises(HttpFailure) as caught:
            self.run_engine(self.make_engine(wire), budget=budget)
        self.assertEqual(caught.exception.code, "fulltext_transfer_changed")
        self.assertEqual([a["status"] for a in caught.exception.attempts], [307, 206])
        self.assertEqual(budget.used, 2)

    def test_nonrange_complete_transfer_with_explicit_allowances(self):
        budget = RequestBudget(1)
        engine = self.make_engine(lambda *args, **kwargs: RawResponse(200, {}, io.BytesIO(BODY), kwargs["address"]))
        result = self.run_engine(engine, max_retained_bytes=10, budget=budget)
        self.assertEqual(self.artifacts.read(result["response"]), BODY)
        self.assertEqual(result["capture_method"], "http")
        self.assertEqual(budget.used, 1)

    def test_interrupted_nonrange_reports_restart_and_preserves_prefix(self):
        class Broken(io.BytesIO):
            def read(self, amount):
                if self.tell():
                    raise socket.timeout()
                return super().read(3)
        engine = self.make_engine(lambda *args, **kwargs: RawResponse(200, {}, Broken(BODY), kwargs["address"]))
        with self.assertRaises(HttpFailure) as caught:
            self.run_engine(engine)
        self.assertEqual(caught.exception.code, "fulltext_resume_unsupported")
        self.assertEqual(caught.exception.attempts[0]["error"], "timeout")
        self.assertEqual(self.artifacts.read(caught.exception.attempts[0]["response"]), b"abc")
        self.assertEqual(self.units.read_units("job"), [])

    def test_weak_etag_or_date_never_authorizes_continuation(self):
        for validator in ({"ETag": 'W/"one"'}, {"ETag": "", "Last-Modified": "Wed, 01 Jan 2025 00:00:00 GMT"}):
            wire = RangeWire(changed=validator)
            with self.subTest(validator=validator), self.assertRaises(HttpFailure) as caught:
                self.run_engine(self.make_engine(wire), budget=RequestBudget(1))
            self.assertEqual(caught.exception.code, "fulltext_resume_unsupported")
            self.assertEqual(self.units.read_units("job"), [])

    def test_partial_attempt_sources_are_partial_and_assembly_has_no_fabricated_200(self):
        result = self.run_engine(self.make_engine(RangeWire()))
        sources = prepare_sources(self.artifacts, result["attempts"], "authored", "op")
        self.assertEqual([source["status"] for source in sources], ["partial"] * 3)
        self.assertEqual([source["http_status"] for source in sources], [206] * 3)
        self.assertEqual(result["capture_method"], "http_range_v1")
        self.assertEqual(result["content_scope"], "representation")
        self.assertIsNone(result["http_status"])
        self.assertEqual(result["response"]["sha256"], hashlib.sha256(BODY).hexdigest())

    def test_commit_retry_does_not_repeat_network_and_precedes_next_range(self):
        self.units.fail_commits = 1
        ranges = RangeWire()
        def wire(url, headers, **kwargs):
            self.assertEqual(len(self.units.read_units("job")), len(ranges.requests))
            return ranges(url, headers, **kwargs)
        result = self.run_engine(self.make_engine(wire))
        self.assertEqual(result["response"]["size"], 10)
        self.assertEqual(len(ranges.requests), 3)
        self.assertEqual(self.units.commits, 4)

    def test_storage_limit_preserves_committed_ranges_and_charges_no_local_work(self):
        self.retain_first()
        budget = RequestBudget(10)
        wire = RangeWire()
        with self.assertRaises(HttpFailure) as caught:
            self.run_engine(self.make_engine(wire), max_retained_bytes=4, budget=budget)
        self.assertEqual(caught.exception.code, "fulltext_storage_limit")
        self.assertEqual(budget.used, 0)
        self.assertEqual(wire.requests, [])
        self.assertEqual(len(self.units.read_units("job")), 1)

    def test_complete_body_cannot_exceed_storage_allowance(self):
        engine = self.make_engine(lambda *args, **kwargs: RawResponse(
            200, {}, io.BytesIO(BODY), kwargs["address"]))
        with self.assertRaises(HttpFailure) as caught:
            self.run_engine(engine, max_retained_bytes=5)
        self.assertEqual(caught.exception.code, "fulltext_storage_limit")
        self.assertEqual(caught.exception.attempts[0]["response"]["size"], 5)
        self.assertEqual(self.units.read_units("job"), [])

    def test_streaming_requests_apply_destination_checks_before_body(self):
        for resolver, peer in ((["127.0.0.1"], "93.184.216.34"), (["93.184.216.34"], "1.1.1.1")):
            engine = self.make_engine(lambda *args, **kwargs: RawResponse(200, {}, io.BytesIO(BODY), peer))
            self.client.resolver = lambda host, timeout: resolver
            budget = RequestBudget(1)
            with self.subTest(peer=peer, resolver=resolver), self.assertRaises(HttpFailure) as caught:
                self.run_engine(engine, budget=budget)
            self.assertEqual(caught.exception.code, "unsafe_destination")
            self.assertEqual(budget.used, 1)
            self.assertEqual(self.units.read_units("job"), [])

    def test_excess_range_body_is_bounded_by_requested_coverage(self):
        def wire(url, headers, **kwargs):
            return RawResponse(206, {"Content-Range": "bytes 0-3/10", "ETag": '"one"'},
                               io.BytesIO(BODY), kwargs["address"])
        with self.assertRaises(HttpFailure) as caught:
            self.run_engine(self.make_engine(wire))
        self.assertEqual(caught.exception.code, "fulltext_transfer_changed")
        self.assertEqual(caught.exception.attempts[0]["response"]["size"], 4)
        self.assertEqual(self.units.read_units("job"), [])

    def test_corrupt_committed_range_fails_before_more_network(self):
        self.retain_first()
        reference = self.units.read_units("job")[0]["artifact"]
        path = self.root / reference["path"]
        path.chmod(0o600)
        path.write_bytes(b"xxxx")
        wire = RangeWire()
        with self.assertRaises(HttpFailure) as caught:
            self.run_engine(self.make_engine(wire))
        self.assertEqual(caught.exception.code, "artifact_corrupt")
        self.assertEqual(wire.requests, [])

    def test_nonrange_mode_does_not_synthesize_range_requests(self):
        requests = []
        def wire(url, headers, **kwargs):
            requests.append(dict(headers))
            return RawResponse(200, {}, io.BytesIO(BODY), kwargs["address"])
        result = self.run_engine(self.make_engine(wire), use_ranges=False)
        self.assertEqual(self.artifacts.read(result["response"]), BODY)
        self.assertNotIn("Range", requests[0])
        self.assertNotIn("If-Range", requests[0])

    def test_shared_provider_admission_stops_stream_before_dns_or_charge(self):
        wire = RangeWire()
        engine = self.make_engine(wire)
        resolved = []
        self.client.resolver = lambda host, timeout: resolved.append(host)
        budget = RequestBudget(1)
        from datetime import datetime, timezone
        eligible = datetime.fromtimestamp(self.client.clock.seconds + 120, timezone.utc).isoformat()
        with self.assertRaises(HttpFailure) as caught:
            self.run_engine(engine, not_before=eligible, budget=budget)
        self.assertEqual(caught.exception.code, "rate_limited")
        self.assertEqual(caught.exception.attempts, [])
        self.assertEqual(budget.used, 0)
        self.assertEqual(resolved, [])
        self.assertEqual(wire.requests, [])

    def test_assembly_storage_refusal_preserves_all_units_for_local_recovery(self):
        wire = RangeWire()
        engine = self.make_engine(wire)
        with self.assertRaises(HttpFailure) as caught:
            self.run_engine(engine, max_retained_bytes=23)
        self.assertEqual(caught.exception.code, "fulltext_storage_limit")
        self.assertEqual(len(self.units.read_units("job")), 3)
        budget = RequestBudget(0)
        result = self.run_engine(engine, max_retained_bytes=24, budget=budget)
        self.assertEqual(result["response"]["sha256"], hashlib.sha256(BODY).hexdigest())
        self.assertEqual(len(wire.requests), 3)
        self.assertEqual(budget.used, 0)

    def test_disk_exhaustion_keeps_committed_range(self):
        self.retain_first()
        def fail_stream(*args, **kwargs):
            raise ResearchError("storage_io", "Injected disk exhaustion")
        wire = RangeWire()
        with patch.object(self.artifacts, "put_stream", side_effect=fail_stream):
            with self.assertRaises(HttpFailure) as caught:
                self.run_engine(self.make_engine(wire))
        self.assertEqual(caught.exception.code, "storage_io")
        self.assertEqual(len(self.units.read_units("job")), 1)
        result = self.run_engine(self.make_engine(RangeWire()))
        self.assertEqual(result["response"]["sha256"], hashlib.sha256(BODY).hexdigest())

    def test_assembly_cannot_finish_after_time_allowance(self):
        engine = self.make_engine(RangeWire())
        original = self.artifacts.put_stream
        def delayed_publish(source, *args, **kwargs):
            result = original(source, *args, **kwargs)
            if result["size"] == len(BODY):
                self.client.clock.seconds += 61
            return result
        with patch.object(self.artifacts, "put_stream", side_effect=delayed_publish):
            with self.assertRaises(HttpFailure) as caught:
                self.run_engine(engine)
        self.assertEqual(caught.exception.code, "timeout")
        self.assertEqual(len(self.units.read_units("job")), 3)

    def test_stream_retry_charges_and_retains_each_attempt_without_range_credit(self):
        from http.client import IncompleteRead
        class Broken(io.BytesIO):
            def read(self, amount):
                raise IncompleteRead(b"ab")
        wire = RangeWire()
        def interrupted(url, headers, **kwargs):
            response = wire(url, headers, **kwargs)
            if len(wire.requests) == 1:
                response.stream = Broken()
            return response
        engine = self.make_engine(interrupted)
        self.client.max_retries = 1
        budget = RequestBudget(4)
        result = self.run_engine(engine, budget=budget)
        self.assertEqual(budget.used, 4)
        self.assertEqual(result["response"]["sha256"], hashlib.sha256(BODY).hexdigest())
        self.assertEqual([a["status"] for a in result["attempts"]], [206] * 4)
        self.assertEqual(result["attempts"][0]["error"], "incomplete_response")
        self.assertEqual(self.artifacts.read(result["attempts"][0]["response"]), b"ab")
        self.assertEqual([u["start"] for u in self.units.read_units("job")], [0, 4, 8])

    def test_nonrange_timeout_is_bounded_and_requires_restart(self):
        clock = Clock()
        class Slow(io.BytesIO):
            def read(self, amount):
                clock.seconds += 4
                return super().read(min(1, amount))
        engine = self.make_engine(lambda *args, **kwargs: RawResponse(200, {}, Slow(BODY), kwargs["address"]), clock=clock)
        with self.assertRaises(HttpFailure) as caught:
            self.run_engine(engine, total_timeout=5)
        self.assertEqual(caught.exception.code, "fulltext_resume_unsupported")
        self.assertEqual(caught.exception.attempts[0]["error"], "timeout")
        self.assertLessEqual(clock.seconds, 1788825608)
        self.assertEqual(self.units.read_units("job"), [])

    def test_invalid_request_headers_use_http_input_failure_before_network(self):
        wire = RangeWire()
        with self.assertRaises(HttpFailure) as caught:
            self.run_engine(self.make_engine(wire), headers={1: "invalid"})
        self.assertEqual(caught.exception.code, "invalid_input")
        self.assertEqual(caught.exception.attempts, [])
        self.assertEqual(wire.requests, [])

    def test_busy_commit_retries_captured_unit_without_another_request(self):
        self.units.fail_commits = 1
        self.units.fail_code = "store_busy"
        wire = RangeWire()
        result = self.run_engine(self.make_engine(wire), budget=RequestBudget(3))
        self.assertEqual(result["response"]["sha256"], hashlib.sha256(BODY).hexdigest())
        self.assertEqual(len(wire.requests), 3)
        self.assertEqual(self.units.commits, 4)

    def test_extreme_range_numbers_preserve_a_typed_refusal(self):
        wire = RangeWire(changed={"Content-Range": "bytes 0-3/" + "9" * 5000})
        with self.assertRaises(HttpFailure) as caught:
            self.run_engine(self.make_engine(wire))
        self.assertIn(caught.exception.code, ("fulltext_transfer_changed", "response_too_large"))
        self.assertEqual(caught.exception.attempts[0]["status"], 206)
        self.assertEqual(self.units.read_units("job"), [])

    def test_extreme_content_length_preserves_attempts_in_both_http_paths(self):
        for streaming in (True, False):
            with self.subTest(streaming=streaming):
                wire = RangeWire(changed={"Content-Length": "9" * 5000})
                engine = self.make_engine(wire)
                with self.assertRaises(HttpFailure) as caught:
                    if streaming:
                        self.run_engine(engine)
                    else:
                        self.client.get(URL, headers={"Range": "bytes=0-3"})
                self.assertEqual(caught.exception.code, "response_too_large")
                self.assertEqual(caught.exception.attempts[0]["status"], 206)
                self.assertEqual(self.units.read_units("job"), [])

    def test_identical_retry_artifacts_share_the_retained_byte_allowance(self):
        responses = [(503, b"wait"), (503, b"wait"), (200, BODY)]
        def wire(url, headers, **kwargs):
            status, body = responses.pop(0)
            return RawResponse(status, {"Content-Length": str(len(body))}, io.BytesIO(body), kwargs["address"])
        engine = self.make_engine(wire)
        self.client.max_retries = 2
        budget = RequestBudget(3)
        result = self.run_engine(engine, max_retained_bytes=15, budget=budget)
        self.assertEqual(result["response"]["sha256"], hashlib.sha256(BODY).hexdigest())
        self.assertEqual([a["status"] for a in result["attempts"]], [503, 503, 200])
        self.assertEqual(budget.used, 3)
        self.assertEqual(len({a["response"]["sha256"] for a in result["attempts"]}), 2)


if __name__ == "__main__":
    unittest.main()
