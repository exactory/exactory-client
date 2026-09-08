"""Bounded HTTPS GETs with pinned public destinations and evidence of each attempt.

HttpClient(...).get(url, *, headers=None, accept=(), budget=None) returns Fetch
or raises HttpFailure. Both expose attempts: [{url, captured_at, status, headers,
body: bytes, complete: bool, error: code | None}]. No request headers, cookies or
credential URLs enter this evidence. `complete` describes the response transfer,
not article availability. HttpFailure.as_dict() excludes binary attempt bodies.

Injection: transport(url, headers, *, address, timeout) -> RawResponse; it must
connect only to address, validate TLS for the URL hostname, perform no redirects
or retries, and expose its actual peer_ip. resolver(host, timeout) -> [numeric IP]
must finish within timeout. clock supplies now() (ISO UTC), monotonic(), sleep(s).
The default transport also bounds header/body reads by an absolute deadline.
RequestBudget counts EVERY attempted resolution/connection, redirect and retry.
"""

import http.client
import io
import ipaddress
import math
import queue
import re
import socket
import ssl
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from urllib.parse import parse_qsl, urljoin, urlsplit, urlunsplit

from .errors import ResearchError


_SECRET = re.compile(r"(^|[-_])(key|token|secret|password|signature|credential|authorization)($|[-_])", re.I)
_COMPACT_SECRET_NAMES = {"apikey", "accesskey", "accesskeyid", "accesstoken", "authtoken", "authentication",
                         "bearertoken", "clientsecret", "clientkey", "refreshtoken", "sessiontoken", "subscriptionkey"}
_SAFE_HEADERS = {"content-type", "content-length", "content-range", "content-encoding", "retry-after", "etag", "last-modified", "date"}
_RETRY = {408, 429, 500, 502, 503, 504}
# Supplement older Python IANA tables. Protocol, translation and deprecated
# prefixes are conservatively outside the untrusted literature download scope.
_SPECIAL_NETWORKS = tuple(ipaddress.ip_network(value) for value in (
    "192.0.0.0/24", "192.88.99.0/24", "::/96", "64:ff9b::/96", "64:ff9b:1::/48",
    "100::/64", "100:0:0:1::/64", "2001::/23", "3ffe::/16", "3fff::/20", "5f00::/16", "fec0::/10"))


def public_address(value):
    try:
        if not isinstance(value, str) or "%" in value:
            raise ValueError()
        address = ipaddress.ip_address(value)
    except ValueError as error:
        raise ResearchError("unsafe_destination", "Destination is not a numeric public IP address") from error
    if (not address.is_global or address.is_multicast or address.is_unspecified
            or any(address in network for network in _SPECIAL_NETWORKS if address.version == network.version)
            or getattr(address, "ipv4_mapped", None) is not None
            or getattr(address, "sixtofour", None) is not None
            or getattr(address, "teredo", None) is not None):
        raise ResearchError("unsafe_destination", "Destination is not a public unicast address")
    return str(address)


def safe_url(value):
    """Validate an untrusted URL without DNS or retaining credential values."""
    try:
        if (not isinstance(value, str) or not value or len(value) > 16384
                or any(ord(c) <= 32 or ord(c) >= 127 for c in value) or "\\" in value):
            raise ValueError()
        parts = urlsplit(value)
        if (parts.scheme != "https" or not parts.hostname or parts.username is not None
                or parts.password is not None or parts.fragment or parts.port not in (None, 443)):
            raise ValueError()
        host = parts.hostname
        if "%" in host or host.endswith(".") or host.lower() in ("localhost", "localhost.localdomain"):
            raise ValueError()
        for key, _ in parse_qsl(parts.query, keep_blank_values=True):
            compact = re.sub(r"[-_]", "", key).lower()
            if _SECRET.search(key) or compact in _COMPACT_SECRET_NAMES or key.lower().startswith(("x-amz-", "x-goog-")):
                raise ValueError()
        try:
            ipaddress.ip_address(host)
        except ValueError:
            if not re.fullmatch(r"[A-Za-z0-9.-]+", host) or any(not p or len(p) > 63 for p in host.split(".")):
                raise ValueError()
        else:
            public_address(host)
        return urlunsplit(("https", parts.netloc.lower(), parts.path or "/", parts.query, ""))
    except (ValueError, ResearchError) as error:
        raise ResearchError("unsafe_url", "Expected a public HTTPS URL without credentials or fragments") from error


class SystemClock:
    def now(self):
        return datetime.now(timezone.utc).isoformat()

    def monotonic(self):
        return time.monotonic()

    def sleep(self, seconds):
        time.sleep(seconds)


class RequestBudget:
    def __init__(self, maximum=None):
        if maximum is not None and (type(maximum) is not int or maximum < 0):
            raise ResearchError("invalid_input", "max_requests must be a nonnegative integer or null")
        self.maximum = maximum
        self.used = 0

    def check(self):
        if self.maximum is not None and self.used >= self.maximum:
            raise ResearchError("request_budget", "The HTTP attempt budget is exhausted; resume with a new operation")

    def consume(self):
        self.check()
        self.used += 1


@dataclass
class RawResponse:
    status: int
    headers: dict
    stream: object
    peer_ip: str
    close_connection: object = None

    def close(self):
        try:
            self.stream.close()
        finally:
            if self.close_connection:
                self.close_connection()


@dataclass
class Fetch:
    url: str
    status: int
    headers: dict
    body: bytes
    attempts: list = field(default_factory=list)


class HttpFailure(ResearchError):
    def __init__(self, code, message, attempts, details=None):
        super().__init__(code, message, details)
        self.attempts = attempts


def _resolve(host, timeout):
    # getaddrinfo has no timeout argument. A daemon confines a stalled resolver's
    # effect to this call's finite wait; it never holds a store transaction.
    result = queue.Queue(maxsize=1)

    def run():
        try:
            result.put((socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM), None))
        except OSError as error:
            result.put((None, error))

    threading.Thread(target=run, daemon=True).start()
    try:
        addresses, error = result.get(timeout=timeout)
    except queue.Empty as error:
        raise socket.timeout() from error
    if error:
        raise error
    return sorted({item[4][0] for item in addresses})


class _DeadlineReader(io.RawIOBase):
    def __init__(self, sock, clock, deadline, timeout):
        self.sock, self.clock, self.deadline, self.timeout = sock, clock, deadline, timeout

    def readable(self):
        return True

    def readinto(self, buffer):
        remaining = self.deadline - self.clock.monotonic()
        if remaining <= 0:
            raise socket.timeout()
        self.sock.settimeout(min(self.timeout, remaining))
        return self.sock.recv_into(buffer)


class _ResponseSocket:
    def __init__(self, sock, clock, deadline, timeout):
        self.sock, self.clock, self.deadline, self.timeout = sock, clock, deadline, timeout

    def makefile(self, mode):
        return io.BufferedReader(_DeadlineReader(self.sock, self.clock, self.deadline, self.timeout))


class _PinnedConnection(http.client.HTTPSConnection):
    def __init__(self, host, address, timeout, clock):
        super().__init__(host, timeout=timeout, context=ssl.create_default_context())
        self.address, self.clock = address, clock
        self.deadline = clock.monotonic() + timeout
        self.response_class = lambda sock, **kw: http.client.HTTPResponse(
            _ResponseSocket(sock, clock, self.deadline, timeout), **kw)

    def connect(self):
        family = socket.AF_INET6 if ":" in self.address else socket.AF_INET
        raw = socket.socket(family, socket.SOCK_STREAM)
        try:
            raw.settimeout(max(0.001, self.deadline - self.clock.monotonic()))
            raw.connect((self.address, self.port))
            if public_address(raw.getpeername()[0]) != self.address:
                raise ResearchError("unsafe_destination", "Connected peer differs from the approved address")
            raw.settimeout(max(0.001, self.deadline - self.clock.monotonic()))
            self.sock = self._context.wrap_socket(raw, server_hostname=self.host)
        except BaseException:
            raw.close()
            raise


class HttpClient:
    def __init__(self, *, transport=None, resolver=None, clock=None, timeout=30,
                 total_timeout=120, max_bytes=32 * 1024 * 1024, max_redirects=5,
                 max_retries=2, max_retry_after=60, user_agent="exactory-research/1"):
        for number in (timeout, total_timeout, max_retry_after):
            if not isinstance(number, (float, int)) or not math.isfinite(number) or number <= 0:
                raise ResearchError("invalid_input", "HTTP time limits must be positive and finite")
        for number in (max_bytes, max_redirects, max_retries):
            if type(number) is not int or number < 0:
                raise ResearchError("invalid_input", "HTTP bounds must be nonnegative integers")
        self.clock = clock or SystemClock()
        self.transport = transport or self._open
        self.resolver = resolver or _resolve
        self.timeout, self.total_timeout, self.max_bytes = timeout, total_timeout, max_bytes
        self.max_redirects, self.max_retries = max_redirects, max_retries
        self.max_retry_after, self.user_agent = max_retry_after, user_agent
        self._last_request = {}

    def _open(self, url, headers, *, address, timeout):
        parts = urlsplit(url)
        connection = _PinnedConnection(parts.hostname, address, timeout, self.clock)
        try:
            connection.connect()
            peer = connection.sock.getpeername()[0]
            connection.request("GET", urlunsplit(("", "", parts.path, parts.query, "")), headers=headers)
            response = connection.getresponse()
            return RawResponse(response.status, dict(response.getheaders()), response, peer, connection.close)
        except BaseException:
            connection.close()
            raise

    def pace_from(self, host, captured_at):
        """Apply persisted pacing across separate collection invocations."""
        try:
            age = (datetime.fromisoformat(self.clock.now()) - datetime.fromisoformat(captured_at)).total_seconds()
            self._last_request[host] = self.clock.monotonic() - max(0, age)
        except (TypeError, ValueError):
            raise ResearchError("invalid_input", "Pacing time must be an ISO timestamp")

    def _delay(self, seconds, deadline):
        if self.clock.monotonic() + seconds >= deadline:
            raise ResearchError("timeout", "HTTP operation exceeded its total time limit")
        if seconds > 0:
            self.clock.sleep(seconds)

    def _retry_delay(self, headers, retry):
        value = headers.get("retry-after")
        if value:
            try:
                delay = float(value)
            except ValueError:
                try:
                    delay = (parsedate_to_datetime(value) - datetime.fromisoformat(self.clock.now())).total_seconds()
                except (ValueError, TypeError, OverflowError):
                    delay = 2 ** retry
            if not math.isfinite(delay):
                delay = self.max_retry_after + 1
            if delay > self.max_retry_after:
                raise ResearchError("rate_limited", "Provider Retry-After exceeds this call's wait limit",
                                    {"retry_after_seconds": delay})
            return max(0.0, delay)
        return float(2 ** retry)

    def get(self, url, *, headers=None, accept=(), budget=None):
        budget = budget if budget is not None else RequestBudget()
        attempts, redirects, retries = [], 0, 0
        deadline = self.clock.monotonic() + self.total_timeout
        request_headers = {"User-Agent": self.user_agent, "Accept-Encoding": "identity"}
        request_headers.update(headers or {})
        if any(not isinstance(k, str) or not isinstance(v, str) or re.search(r"[\x00-\x1f\x7f]", k + v)
               for k, v in request_headers.items()):
            raise HttpFailure("invalid_input", "HTTP headers cannot contain control characters", [])
        try:
            url = safe_url(url)
            while True:
                budget.check()
                host = urlsplit(url).hostname
                interval = 3 if host in ("arxiv.org", "export.arxiv.org") else 0
                self._delay(max(0, self._last_request.get(host, -float("inf")) + interval - self.clock.monotonic()), deadline)
                budget.consume()
                attempt = {"url": url, "captured_at": self.clock.now(), "status": None,
                           "headers": {}, "body": b"", "complete": False, "error": None}
                attempts.append(attempt)
                self._last_request[host] = self.clock.monotonic()
                response = None
                try:
                    remaining = min(self.timeout, deadline - self.clock.monotonic())
                    if remaining <= 0:
                        raise socket.timeout()
                    addresses = [public_address(item) for item in self.resolver(host, remaining)]
                    if not addresses:
                        raise socket.gaierror()
                    address = addresses[retries % len(addresses)]
                    response = self.transport(url, request_headers, address=address,
                                              timeout=min(self.timeout, deadline - self.clock.monotonic()))
                    if public_address(response.peer_ip) != address:
                        raise ResearchError("unsafe_destination", "Connected peer differs from the approved address")
                    all_headers = {k.lower(): str(v) for k, v in response.headers.items()}
                    attempt["headers"] = {k: v for k, v in all_headers.items() if k in _SAFE_HEADERS}
                    attempt["status"] = response.status
                    length = all_headers.get("content-length")
                    if length is not None and (not length.isdigit() or int(length) > self.max_bytes):
                        raise ResearchError("response_too_large", "Response length exceeds the configured bound or is invalid")
                    if all_headers.get("content-encoding", "identity").lower() != "identity":
                        raise ResearchError("unsupported_encoding", "Response ignored the identity encoding request")
                    body = bytearray()
                    while True:
                        if self.clock.monotonic() >= deadline:
                            raise socket.timeout()
                        chunk = response.stream.read(min(65536, self.max_bytes + 1 - len(body)))
                        if not chunk:
                            break
                        body.extend(chunk)
                        attempt["body"] = bytes(body)
                        if len(body) > self.max_bytes:
                            raise ResearchError("response_too_large", "Response exceeds the configured byte limit")
                    if self.clock.monotonic() >= deadline:
                        raise socket.timeout()
                    if length is not None and len(body) != int(length):
                        raise ResearchError("incomplete_response", "Response ended before its declared length")
                    attempt["complete"] = True
                except (socket.timeout, TimeoutError) as error:
                    attempt["error"] = "timeout"
                    if retries >= self.max_retries:
                        raise ResearchError("timeout", "HTTP operation timed out") from error
                except (OSError, http.client.HTTPException) as error:
                    attempt["error"] = "network_error"
                    if retries >= self.max_retries:
                        raise ResearchError("network_error", "HTTP connection or response failed") from error
                except ResearchError as error:
                    attempt["error"] = error.code
                    raise
                finally:
                    if response is not None:
                        response.close()
                if attempt["error"]:
                    budget.check()
                    self._delay(float(2 ** retries), deadline)
                    retries += 1
                    continue
                status = attempt["status"]
                if status == 206 or "content-range" in attempt["headers"]:
                    raise ResearchError("partial_response", "A partial HTTP response cannot satisfy full source capture")
                if status in (301, 302, 303, 307, 308):
                    if redirects >= self.max_redirects:
                        raise ResearchError("redirect_limit", "HTTP redirect limit reached")
                    location = all_headers.get("location")
                    if not location:
                        raise ResearchError("invalid_redirect", "Redirect omitted its destination")
                    target = safe_url(urljoin(url, location))
                    if urlsplit(target).netloc != urlsplit(url).netloc:
                        request_headers = {k: v for k, v in request_headers.items()
                                           if k.lower() in ("user-agent", "accept", "accept-encoding")}
                    url, redirects = target, redirects + 1
                    continue
                if status in _RETRY and retries < self.max_retries:
                    budget.check()
                    self._delay(self._retry_delay(attempt["headers"], retries), deadline)
                    retries += 1
                    continue
                if status < 200 or status >= 300:
                    code = "rate_limited" if status == 429 else "http_status"
                    raise ResearchError(code, "Provider returned an unsuccessful HTTP status", {"status": status})
                media_type = attempt["headers"].get("content-type", "application/octet-stream").split(";")[0].lower().strip()
                if accept and media_type not in accept:
                    raise ResearchError("unexpected_mime", "Provider returned an unexpected media type", {"media_type": media_type})
                return Fetch(url, status, attempt["headers"], attempt["body"], attempts)
        except ResearchError as error:
            if attempts and attempts[-1]["error"] is None:
                attempts[-1]["error"] = error.code
            raise HttpFailure(error.code, error.message, attempts, error.details) from error
