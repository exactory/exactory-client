"""HTTP attempts retain exact prefixes with one terminal body materialization."""

import builtins
import io
import socket
import unittest
from http.client import IncompleteRead
from unittest.mock import patch

from research_harness import http
from research_fixtures import Clock


class ChunkStream(io.BytesIO):
    def __init__(self, body, terminal=None, on_close=None):
        super().__init__(body)
        self.terminal, self.on_close = terminal, on_close

    def read(self, amount):
        if self.tell() == len(self.getvalue()) and self.terminal is not None:
            raise self.terminal
        return super().read(min(amount, 4))

    def close(self):
        if self.on_close is not None:
            self.on_close()
        super().close()


class HttpLinearTests(unittest.TestCase):
    def run_capture(self, responses, expected, *, code=None, **bounds):
        copied, frozen_at_close = [], []
        raw = []
        for status, headers, body, terminal in responses:
            stream = ChunkStream(body, terminal, lambda: frozen_at_close.append(len(copied)))
            raw.append(http.RawResponse(status, headers, stream, "93.184.216.34"))

        def materialize(value):
            if isinstance(value, bytearray):
                copied.append(len(value))
            return builtins.bytes(value)

        client = http.HttpClient(clock=Clock(), resolver=lambda host, timeout: ["93.184.216.34"],
                                 transport=lambda *args, **kwargs: raw.pop(0), **bounds)
        with patch.object(http, "bytes", materialize, create=True):
            if code is None:
                result = client.get("https://example.org/source")
                attempts = result.attempts
                self.assertEqual(result.body, expected[-1][0])
            else:
                with self.assertRaises(http.HttpFailure) as caught:
                    client.get("https://example.org/source")
                self.assertEqual(caught.exception.code, code)
                attempts = caught.exception.attempts
        self.assertEqual([(a["body"], a["complete"], a["error"]) for a in attempts], expected)
        self.assertEqual(copied, [len(body) for body, _, _ in expected])
        self.assertEqual(frozen_at_close, list(range(1, len(expected) + 1)))
        return sum(copied)

    def test_terminal_freeze_preserves_each_attempt_prefix(self):
        prefix = b"abcdefghijkl"
        cases = [
            ([(200, {}, prefix, None)], [(prefix, True, None)], {}, None),
            ([(302, {"Location": "/next"}, prefix, None), (200, {}, b"done", None)],
             [(prefix, True, None), (b"done", True, None)], {}, None),
            ([(503, {}, prefix, None), (200, {}, b"done", None)],
             [(prefix, True, None), (b"done", True, None)], {}, None),
            ([(200, {}, prefix, socket.timeout()), (200, {}, b"done", None)],
             [(prefix, False, "timeout"), (b"done", True, None)], {}, None),
            ([(200, {}, prefix, socket.timeout())], [(prefix, False, "timeout")],
             {"max_retries": 0}, "timeout"),
            ([(200, {}, prefix, IncompleteRead(b"tail")), (200, {}, b"done", None)],
             [(prefix + b"tail", False, "incomplete_response"), (b"done", True, None)], {}, None),
            ([(200, {}, prefix, IncompleteRead(b"tail"))],
             [(prefix + b"tail", False, "incomplete_response")], {"max_retries": 0}, "incomplete_response"),
        ]
        for responses, expected, bounds, code in cases:
            with self.subTest(code=code, responses=len(responses), terminal=repr(responses[0][-1])):
                self.run_capture(responses, expected, code=code, **bounds)

    def test_boundary_and_oversize_prefixes_include_zero_allowance(self):
        cases = [
            (12, {}, b"abcdefghijkl", None, b"abcdefghijkl", None),
            (12, {}, b"abcdefghijklm", None, b"abcdefghijkl", "response_too_large"),
            (12, {"Content-Length": "13"}, b"abcdefghijklm", None, b"", "response_too_large"),
            (5, {}, b"abcd", IncompleteRead(b"efgh"), b"abcde", "response_too_large"),
            (0, {}, b"", None, b"", None),
            (0, {}, b"a", None, b"", "response_too_large"),
            (0, {}, b"", IncompleteRead(b"a"), b"", "response_too_large"),
        ]
        for maximum, headers, body, terminal, retained, code in cases:
            with self.subTest(maximum=maximum, body=body, terminal=repr(terminal)):
                self.run_capture([(200, headers, body, terminal)], [(retained, code is None, code)],
                                 code=code, max_bytes=maximum, max_retries=0)

    def test_doubling_chunks_doubles_materialized_bytes(self):
        copied = []
        for size in (256, 512):
            body = b"a" * size
            copied.append(self.run_capture([(200, {}, body, None)], [(body, True, None)]))
        self.assertEqual(copied, [256, 512])


if __name__ == "__main__":
    unittest.main()
