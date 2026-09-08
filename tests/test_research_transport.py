"""Safety and budget tests at the real research HTTP boundary."""

import io
import socket
import tempfile
import unittest
from datetime import datetime, timezone
from email.utils import format_datetime
from http.client import HTTPResponse, IncompleteRead
from pathlib import Path

from research_harness.artifacts import ArtifactStore
from research_harness.evidence import prepare_sources
from research_harness.errors import ResearchError
from research_harness.http import HttpClient, HttpFailure, RequestBudget, RawResponse, safe_url
from research_fixtures import Clock, client


class TransportTests(unittest.TestCase):
    def test_rejects_private_urls_credentials_and_redirects(self):
        for url in ("http://example.org/x", "https://user:pass@example.org/x",
                    "https://example.org/?api_key=secret", "https://127.0.0.1/x",
                    "https://[::1]/x", "https://169.254.169.254/x"):
            with self.subTest(url=url), self.assertRaises(ResearchError):
                safe_url(url)
        http, wire, _ = client([(302, {"Location": "https://127.0.0.1/admin"}, b"")])
        with self.assertRaises(HttpFailure) as error:
            http.get("https://example.org/x")
        self.assertEqual(error.exception.code, "unsafe_url")
        self.assertEqual(len(wire.requests), 1)

    def test_resolution_and_actual_peer_must_both_be_public(self):
        http, wire, _ = client([(200, {}, b"private", "127.0.0.1")])
        with self.assertRaises(HttpFailure) as error:
            http.get("https://example.org/x")
        self.assertEqual(error.exception.code, "unsafe_destination")
        self.assertEqual(error.exception.attempts[0]["body"], b"")
        http.resolver = lambda host, timeout: ["127.0.0.1"]
        with self.assertRaises(HttpFailure):
            http.get("https://example.org/x")
        self.assertEqual(len(wire.requests), 1)

    def test_public_peer_different_from_pinned_address_is_rejected(self):
        http, _, _ = client([(200, {}, b"changed", "1.1.1.1")])
        with self.assertRaises(HttpFailure) as error:
            http.get("https://example.org/x")
        self.assertEqual(error.exception.code, "unsafe_destination")

    def test_redirect_strips_credentials_and_counts_every_attempt(self):
        http, wire, _ = client([(302, {"Location": "https://other.org/article"}, b"moved"),
                                (200, {"Content-Type": "text/plain", "Set-Cookie": "secret"}, b"body")])
        budget = RequestBudget(2)
        result = http.get("https://example.org/x", headers={"Authorization": "Bearer secret"}, budget=budget)
        self.assertEqual(result.body, b"body")
        self.assertEqual(budget.used, 2)
        self.assertNotIn("Authorization", wire.requests[1][1])
        self.assertNotIn("secret", repr(result.attempts))

    def test_retry_after_and_attempt_limit_leave_retriable_failure(self):
        http, wire, clock = client([(429, {"Retry-After": "5"}, b"slow"), (200, {}, b"ok")])
        result = http.get("https://example.org/x", budget=RequestBudget(2))
        self.assertEqual(result.body, b"ok")
        self.assertIn(5.0, clock.sleeps)
        self.assertEqual(result.attempts[0]["status"], 429)
        http, wire, clock = client([(429, {"Retry-After": "5"}, b"slow")])
        with self.assertRaises(HttpFailure) as error:
            http.get("https://example.org/x", budget=RequestBudget(1))
        self.assertEqual(error.exception.code, "request_budget")
        self.assertEqual(len(error.exception.attempts), 1)
        self.assertEqual(len(wire.requests), 1)
        self.assertEqual(clock.sleeps, [])

    def test_retry_after_deadline_uses_response_receipt_before_budget_exhaustion(self):
        for form in ("seconds", "date"):
            with self.subTest(form=form):
                http, wire, clock = client([])
                started = clock.seconds
                eligible = datetime.fromtimestamp(started + (11 if form == "seconds" else 9), timezone.utc)
                value = "7" if form == "seconds" else format_datetime(eligible, usegmt=True)
                wire.responses.append((429, {"Retry-After": value}, b"Wait."))
                def delayed_headers(*args, **kwargs):
                    clock.seconds += 4
                    return wire(*args, **kwargs)
                http.transport = delayed_headers
                with self.assertRaises(HttpFailure) as error:
                    http.get("https://example.org/source", budget=RequestBudget(1))
                self.assertEqual(error.exception.code, "request_budget")
                self.assertEqual(error.exception.attempts[0].get("next_eligible_at"), eligible.isoformat())
                self.assertEqual(error.exception.attempts[0].get("response_received_at"), clock.now())
                self.assertEqual(clock.sleeps, [])

    def test_deferred_request_obeys_wait_and_total_limits_before_dns(self):
        for bounds, delay in (({"max_retry_after": 20}, 30), ({"max_retry_after": 60, "total_timeout": 10}, 20)):
            with self.subTest(bounds=bounds):
                http, wire, clock = client([(200, {}, b"Unexpected request.")], **bounds)
                resolved = []
                def resolve(host, timeout):
                    resolved.append(host)
                    return ["93.184.216.34"]
                http.resolver = resolve
                budget = RequestBudget(1)
                eligible = datetime.fromtimestamp(clock.seconds + delay, timezone.utc).isoformat()
                with self.assertRaises(HttpFailure) as error:
                    http.get("https://example.org/source", budget=budget, not_before=eligible)
                self.assertEqual(error.exception.code, "rate_limited")
                self.assertEqual(error.exception.details["next_eligible_at"], eligible)
                self.assertEqual(error.exception.attempts, [])
                self.assertEqual(budget.used, 0)
                self.assertEqual(resolved, [])
                self.assertEqual(wire.requests, [])
                self.assertEqual(clock.sleeps, [])

    def test_retry_after_also_defers_redirect_and_incomplete_response_attempts(self):
        class IncompleteStream(io.BytesIO):
            def read(self, amount):
                raise IncompleteRead(b"An incomplete response.")
        for status, headers, stream in (
            (302, {"Location": "/next", "Retry-After": "120"}, io.BytesIO(b"Moved.")),
            (429, {"Retry-After": "120"}, IncompleteStream()),
        ):
            with self.subTest(status=status):
                requests = []
                responses = [RawResponse(status, headers, stream, "93.184.216.34"),
                             RawResponse(200, {}, io.BytesIO(b"Unexpected retry."), "93.184.216.34")]
                def transport(url, headers, **kwargs):
                    requests.append(url)
                    return responses.pop(0)
                http = HttpClient(clock=Clock(), transport=transport,
                                  resolver=lambda host, timeout: ["93.184.216.34"])
                with self.assertRaises(HttpFailure) as error:
                    http.get("https://example.org/source", budget=RequestBudget(2))
                self.assertEqual(error.exception.code, "rate_limited")
                self.assertEqual(requests, ["https://example.org/source"])
                self.assertEqual(len(error.exception.attempts), 1)

    def test_pacing_uses_injected_clock(self):
        http, _, clock = client([(200, {}, b"a"), (200, {}, b"b")])
        http.get("https://export.arxiv.org/api/query?a=1")
        http.get("https://export.arxiv.org/api/query?a=2")
        self.assertIn(3.0, clock.sleeps)

    def test_size_mime_encoding_and_truncation_are_failures(self):
        cases = [({"Content-Length": "100"}, b"x", "response_too_large"),
                 ({}, b"123456", "response_too_large"),
                 ({"Content-Length": "4"}, b"x", "incomplete_response"),
                 ({"Content-Type": "text/html"}, b"x", "unexpected_mime"),
                 ({"Content-Encoding": "gzip"}, b"x", "unsupported_encoding")]
        for headers, body, code in cases:
            with self.subTest(code=code):
                http, _, _ = client([(200, headers, body)], max_bytes=5, max_retries=0)
                with self.assertRaises(HttpFailure) as error:
                    http.get("https://example.org/x", accept=("application/json",))
                self.assertEqual(error.exception.code, code)

    def test_timeouts_and_retries_are_finite_and_sanitized(self):
        http, wire, _ = client([socket.timeout("secret token"), socket.timeout("secret token")], max_retries=1)
        with self.assertRaises(HttpFailure) as error:
            http.get("https://example.org/x")
        self.assertEqual(error.exception.code, "timeout")
        self.assertEqual(len(wire.requests), 2)
        self.assertNotIn("secret", str(error.exception.as_dict()))

    def test_total_deadline_limits_streaming_response(self):
        clock = Clock()

        class SlowStream(io.BytesIO):
            def read(self, amount):
                clock.seconds += 4
                return super().read(1)

        http = HttpClient(clock=clock, timeout=3, total_timeout=5, max_retries=0,
                          resolver=lambda host, timeout: ["93.184.216.34"],
                          transport=lambda *args, **kwargs: RawResponse(200, {}, SlowStream(b"abcdef"), "93.184.216.34"))
        with self.assertRaises(HttpFailure) as error:
            http.get("https://example.org/x")
        self.assertEqual(error.exception.code, "timeout")
        self.assertFalse(error.exception.attempts[0]["complete"])

    def test_credential_parameter_spellings_never_enter_attempt_evidence(self):
        for key in ("apiKey", "apikey", "accessToken", "authToken", "client_secret", "password", "X-Amz-Signature",
                    "sig", "SIG", "%73ig"):
            with self.subTest(key=key):
                http, wire, _ = client([(200, {}, b"Unexpected request")])
                with self.assertRaises(HttpFailure) as error:
                    http.get("https://example.org/?" + key + "=authored-secret")
                self.assertEqual(error.exception.attempts, [])
                self.assertEqual(wire.requests, [])
                self.assertNotIn("authored-secret", str(error.exception.as_dict()))

    def test_sas_signed_redirect_never_resolves_or_records_the_credential_target(self):
        signed = ("https://authored.blob.core.windows.net/articles/example.pdf"
                  "?sv=2025-01-05&sp=r&se=2026-09-09T00%3A00%3A00Z&sig=authored-secret")
        for direct in (True, False):
            with self.subTest(direct=direct):
                http, wire, _ = client([(302, {"Location": signed}, b"Moved."), (200, {}, b"Unexpected request.")]
                                      if not direct else [(200, {}, b"Unexpected request.")])
                resolved = []
                def resolve(host, timeout):
                    resolved.append(host)
                    return ["93.184.216.34"]
                http.resolver = resolve
                with self.assertRaises(HttpFailure) as error:
                    http.get(signed if direct else "https://example.org/article")
                self.assertEqual(error.exception.code, "unsafe_url")
                self.assertEqual(resolved, [] if direct else ["example.org"])
                self.assertEqual(len(wire.requests), 0 if direct else 1)
                self.assertNotIn("authored-secret", repr(error.exception.attempts))
                self.assertNotIn("authored-secret", repr(error.exception.as_dict()))

    def test_partial_http_success_and_mime_errors_remain_failed_attempts(self):
        http, _, _ = client([(206, {"Content-Type": "application/pdf", "Content-Range": "bytes 0-4/20"}, b"%PDF-")])
        with self.assertRaises(HttpFailure) as error:
            http.get("https://example.org/article", accept=("application/pdf",))
        self.assertEqual(error.exception.code, "partial_response")
        self.assertEqual(error.exception.attempts[-1]["error"], "partial_response")
        http, _, _ = client([(200, {"Content-Type": "text/html"}, b"challenge")])
        with self.assertRaises(HttpFailure) as error:
            http.get("https://example.org/article", accept=("application/pdf",))
        self.assertEqual(error.exception.attempts[-1]["error"], "unexpected_mime")

    def test_chunked_incomplete_read_preserves_partial_bytes_and_source_status(self):
        for prefix in (b"", b"a" * 65536):
            with self.subTest(prefix_bytes=len(prefix)), tempfile.TemporaryDirectory() as directory:
                prefix_chunk = b"10000\r\n" + prefix + b"\r\n" if prefix else b""
                raw = (b"HTTP/1.1 200 OK\r\nContent-Type: text/plain\r\nTransfer-Encoding: chunked\r\n\r\n"
                       + prefix_chunk + b"5\r\nHello\r\n5\r\nW")
                class BufferSocket:
                    def makefile(self, mode):
                        return io.BytesIO(raw)
                response = HTTPResponse(BufferSocket())
                response.begin()
                http = HttpClient(clock=Clock(), max_retries=0,
                    resolver=lambda host, timeout: ["93.184.216.34"],
                    transport=lambda *args, **kwargs: RawResponse(response.status, dict(response.getheaders()), response, "93.184.216.34"))
                with self.assertRaises(HttpFailure) as error:
                    http.get("https://example.org/incomplete")
                attempt = error.exception.attempts[0]
                self.assertEqual(attempt["body"], prefix + b"Hello")
                self.assertFalse(attempt["complete"])
                self.assertEqual(error.exception.code, "incomplete_response")
                artifacts = ArtifactStore(Path(directory))
                source = prepare_sources(artifacts, error.exception.attempts, "authored", "partial")[0]
                self.assertEqual(source["status"], "partial")
                self.assertFalse(source["response_complete"])
                self.assertEqual(artifacts.read(source["response"]), prefix + b"Hello")

    def test_incomplete_read_partial_cannot_exceed_the_capture_byte_bound(self):
        class PartialStream(io.BytesIO):
            def read(self, amount):
                if self.tell() == 0:
                    return super().read(2)
                raise IncompleteRead(b"c" * 100)
        http = HttpClient(clock=Clock(), max_retries=0, max_bytes=5,
            resolver=lambda host, timeout: ["93.184.216.34"],
            transport=lambda *args, **kwargs: RawResponse(200, {}, PartialStream(b"ab"), "93.184.216.34"))
        with self.assertRaises(HttpFailure) as error:
            http.get("https://example.org/incomplete")
        self.assertEqual(error.exception.attempts[0]["body"], b"abccc")
        self.assertFalse(error.exception.attempts[0]["complete"])
        self.assertEqual(error.exception.code, "response_too_large")

    def test_transition_and_special_use_addresses_cannot_reach_private_peers(self):
        from research_harness.http import public_address
        for address in ("64:ff9b::7f00:1", "64:ff9b:1::a00:1", "fec0::1", "192.0.0.8", "3fff::1"):
            with self.subTest(address=address), self.assertRaises(ResearchError):
                public_address(address)

    def test_default_connection_pins_numeric_address_and_validates_tls_hostname(self):
        import ssl
        from unittest.mock import patch
        from research_harness.http import _PinnedConnection
        events = []
        class Socket:
            def settimeout(self, timeout):
                events.append(("timeout", timeout))
            def connect(self, address):
                events.append(("connect", address))
            def getpeername(self):
                return "93.184.216.34", 443
            def close(self):
                events.append(("close",))
        sock = Socket()
        context = ssl.create_default_context()
        def wrap_socket(raw, *, server_hostname):
            self.assertTrue(context.check_hostname)
            self.assertEqual(context.verify_mode, ssl.CERT_REQUIRED)
            events.append(("tls", server_hostname))
            return raw
        with patch("research_harness.http.ssl.create_default_context", return_value=context), \
             patch.object(context, "wrap_socket", side_effect=wrap_socket), \
             patch("research_harness.http.socket.socket", return_value=sock):
            connection = _PinnedConnection("example.org", "93.184.216.34", 5, Clock())
            connection.connect()
            connection.close()
        self.assertIn(("connect", ("93.184.216.34", 443)), events)
        self.assertIn(("tls", "example.org"), events)

    def test_redirect_limit_and_retry_after_date_are_bounded(self):
        http, wire, _ = client([(302, {"Location": "/again"}, b"")] * 3, max_redirects=2)
        with self.assertRaises(HttpFailure) as error:
            http.get("https://example.org/start")
        self.assertEqual(error.exception.code, "redirect_limit")
        self.assertEqual(len(wire.requests), 3)
        from email.utils import format_datetime
        from datetime import datetime, timezone
        retry_date = format_datetime(datetime.fromtimestamp(1788825607, timezone.utc), usegmt=True)
        http, _, clock = client([(429, {"Retry-After": retry_date}, b"wait"), (200, {}, b"ok")])
        self.assertEqual(http.get("https://example.org/x").body, b"ok")
        self.assertIn(7.0, clock.sleeps)
        http, wire, clock = client([(429, {"Retry-After": "3600"}, b"wait")])
        with self.assertRaises(HttpFailure) as error:
            http.get("https://example.org/x")
        self.assertEqual(error.exception.code, "rate_limited")
        self.assertEqual(len(wire.requests), 1)
        self.assertEqual(clock.sleeps, [])


if __name__ == "__main__":
    unittest.main()
