"""Safety and budget tests at the real research HTTP boundary."""

import io
import socket
import unittest

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
        for key in ("apiKey", "apikey", "accessToken", "authToken", "client_secret", "password", "X-Amz-Signature"):
            with self.subTest(key=key):
                http, wire, _ = client([(200, {}, b"Unexpected request")])
                with self.assertRaises(HttpFailure) as error:
                    http.get("https://example.org/?" + key + "=authored-secret")
                self.assertEqual(error.exception.attempts, [])
                self.assertEqual(wire.requests, [])
                self.assertNotIn("authored-secret", str(error.exception.as_dict()))

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
