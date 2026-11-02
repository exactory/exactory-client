"""Stream complete originals or commit independently validated HTTP ranges.

The injected unit store implements read_units(job_id) and
commit_unit(job_id, unit), in commit order. The job owner serializes run calls
and authenticates those reads. Commits must be idempotent: storage_io/store_busy
is retried using the same captured unit, never another network request. Only a
successful commit advances coverage. Attempts contain artifact references, not
in-memory bodies; the caller retains failed attempts from HttpFailure too.

run returns capture provenance and attempts made during this invocation. Earlier
attempts remain in committed units. Range assemblies have no HTTP status. No
result here grants extraction, availability, reading or reviewer-delivery credit.
retained_bytes accounts for other job artifacts, excluding committed transfer
units, which are counted here. The allowance includes peak assembly scratch.
"""

from contextlib import closing
import math
import re

from .artifacts import validate_reference
from .errors import ResearchError
from .evidence import digest
from .http import HttpFailure, RequestBudget, safe_url


_CONTENT_RANGE = re.compile(r"bytes ([0-9]+)-([0-9]+)/([0-9]+)\Z")
_STRONG_ETAG = re.compile(r'"[\x21\x23-\x7e\x80-\xff]*"\Z')


class _RangeReader:
    def __init__(self, artifacts, units, clock, deadline):
        self.artifacts, self.units = artifacts, iter(units)
        self.clock, self.deadline = clock, deadline
        self.context, self.source = None, None

    def read(self, amount):
        while True:
            if self.clock.monotonic() >= self.deadline:
                raise ResearchError("timeout", "Full-text assembly exceeded its time allowance")
            if self.source is None:
                unit = next(self.units, None)
                if unit is None:
                    return b""
                self.context = self.artifacts.open_verified(unit["artifact"])
                self.source = self.context.__enter__().stream
                continue
            chunk = self.source.read(amount)
            if chunk:
                return chunk
            self.close()

    def close(self):
        if self.context is not None:
            self.context.__exit__(None, None, None)
        self.context, self.source = None, None


class FulltextTransfer:
    def __init__(self, client, artifacts, units):
        self.client, self.artifacts, self.units = client, artifacts, units

    def _validate_units(self, units, requested_url):
        ranges, complete = [], None
        cursor, binding = 0, None
        for unit in units:
            if complete is not None or unit.get("requested_url") != requested_url:
                raise ResearchError("fulltext_transfer_changed", "Committed transfer units changed their requested URL or order")
            reference = unit["artifact"]
            validate_reference(reference)
            total = unit.get("total")
            if (type(total) is not int or total < 0 or safe_url(unit["url"]) != unit["url"]
                    or unit.get("encoding") != "identity"):
                raise ResearchError("fulltext_transfer_changed", "Committed transfer identity is invalid")
            if unit.get("kind") == "complete":
                if reference["size"] != total or unit.get("http_status") != 200:
                    raise ResearchError("fulltext_transfer_changed", "A complete transfer must cover its entire response")
                complete = unit
                continue
            start, end = unit.get("start"), unit.get("end")
            etag = unit.get("etag")
            identity = (unit["url"], etag, total)
            if (unit.get("kind") != "range" or type(start) is not int or type(end) is not int
                    or start != cursor or not start <= end < total or reference["size"] != end - start + 1
                    or not isinstance(etag, str) or not _STRONG_ETAG.fullmatch(etag)
                    or (binding is not None and binding != identity)):
                raise ResearchError("fulltext_transfer_changed", "Committed ranges have gaps, overlaps or changed identity")
            binding, cursor = identity, end + 1
            ranges.append(unit)
        return ranges, complete

    def _commit_unit(self, job_id, unit):
        for retry in range(3):
            try:
                self.units.commit_unit(job_id, unit)
                return
            except ResearchError as error:
                if error.code not in ("storage_io", "store_busy") or retry == 2:
                    raise

    def _build_result(self, unit, response, attempts, unit_count, deadline, *, assembled=False):
        if self.client.clock.monotonic() >= deadline:
            raise ResearchError("timeout", "Full-text transfer exceeded its time allowance")
        return {"url": unit["url"], "response": response, "attempts": attempts,
                "capture_method": "http_range_v1" if assembled else "http",
                "content_scope": "representation" if assembled else "response",
                "http_status": None if assembled else 200,
                "headers": {} if assembled else unit["attempts"][-1]["headers"],
                "response_complete": True, "origin_verified": True,
                "etag": unit.get("etag"), "encoding": "identity", "total": response["size"],
                "unit_count": unit_count, "requested_url": unit["requested_url"]}

    def run(self, job_id, url, *, max_original_bytes, max_retained_bytes, range_bytes,
            total_timeout, budget=None, retained_bytes=0, allow_complete_restart=False,
            use_ranges=True, headers=None, accept=(), not_before=None):
        """Acquire under explicit allowances; reuse only committed complete units.

        A range-ignoring 200 may replace prior coverage only when
        allow_complete_restart is recorded by the caller. use_ranges=False
        requests the complete-response alternative when continuation is unsafe.
        A failed non-range transfer must restart; its prefix earns no coverage.
        """
        attempts = []
        try:
            for number in (max_original_bytes, max_retained_bytes, retained_bytes):
                if type(number) is not int or number < 0:
                    raise ResearchError("invalid_input", "Transfer byte allowances must be nonnegative integers")
            if type(range_bytes) is not int or range_bytes <= 0:
                raise ResearchError("invalid_input", "Range size must be a positive integer")
            if type(total_timeout) not in (int, float) or not math.isfinite(total_timeout) or total_timeout <= 0:
                raise ResearchError("invalid_input", "Transfer time allowance must be positive and finite")
            if type(use_ranges) is not bool or type(allow_complete_restart) is not bool:
                raise ResearchError("invalid_input", "Transfer mode flags must be boolean")
            url = safe_url(url)
            deadline = self.client.clock.monotonic() + total_timeout
            budget = budget if budget is not None else RequestBudget()
            units = list(self.units.read_units(job_id))
            ranges, complete = self._validate_units(units, url)
            retained_refs = {}
            for unit in units:
                retained_refs[unit["artifact"]["sha256"]] = unit["artifact"]
                for attempt in unit["attempts"]:
                    if attempt.get("response") is not None:
                        retained_refs[attempt["response"]["sha256"]] = attempt["response"]
            used = retained_bytes + sum(ref["size"] for ref in retained_refs.values())
            if used > max_retained_bytes:
                raise ResearchError("fulltext_storage_limit", "Committed transfers exhaust the retained-byte allowance")
            if complete is not None:
                if complete["total"] > max_original_bytes:
                    raise ResearchError("response_too_large", "Committed original exceeds the original-byte allowance")
                self.artifacts.verify(complete["artifact"])
                return self._build_result(complete, complete["artifact"], attempts, len(units), deadline)
            if ranges and ranges[0]["total"] > max_original_bytes:
                raise ResearchError("response_too_large", "Committed original exceeds the original-byte allowance")
            for unit in ranges:
                self.artifacts.verify(unit["artifact"])
            if ranges and not use_ranges and not allow_complete_restart:
                raise ResearchError("fulltext_resume_unsupported", "A separate complete transfer requires a restart allowance")
            while not ranges or ranges[-1]["end"] + 1 < ranges[0]["total"]:
                remaining = deadline - self.client.clock.monotonic()
                if remaining <= 0:
                    raise ResearchError("timeout", "Full-text transfer exceeded its time allowance")
                if used >= max_retained_bytes and max_original_bytes:
                    raise ResearchError("fulltext_storage_limit", "Retained bytes exhaust the transfer allowance")
                start = ranges[-1]["end"] + 1 if ranges else 0
                total = ranges[0]["total"] if ranges else None
                end = min(start + range_bytes, total if total is not None else max_original_bytes) - 1
                request_headers = {key: value for key, value in (headers or {}).items()
                                   if not isinstance(key, str) or key.lower() not in ("range", "if-range", "accept-encoding")}
                if use_ranges and end >= start:
                    request_headers["Range"] = "bytes=%d-%d" % (start, end)
                    if ranges:
                        request_headers["If-Range"] = ranges[0]["etag"]
                identity = {}

                def validate_response(final_url, status, response_headers):
                    if status == 200:
                        if "content-range" in response_headers:
                            raise ResearchError("fulltext_transfer_changed", "A complete response included a partial range")
                        if ranges and not allow_complete_restart:
                            raise ResearchError("fulltext_resume_unsupported", "Server ignored continuation; a separate complete transfer requires a restart allowance")
                        identity.update(kind="complete", url=final_url, etag=response_headers.get("etag"))
                        return int(response_headers["content-length"]) if "content-length" in response_headers else None
                    if status != 206 or not use_ranges:
                        raise ResearchError("partial_response", "Expected a complete response or the requested byte range")
                    match = _CONTENT_RANGE.fullmatch(response_headers.get("content-range", ""))
                    if match is None:
                        raise ResearchError("fulltext_transfer_changed", "Malformed or missing Content-Range")
                    try:
                        actual_start, actual_end, actual_total = map(int, match.groups())
                    except ValueError as error:
                        raise ResearchError("fulltext_transfer_changed", "Content-Range numbers exceed the parser bound") from error
                    if (actual_start != start or actual_end != min(end, actual_total - 1)
                            or not actual_start <= actual_end < actual_total
                            or (total is not None and actual_total != total)):
                        raise ResearchError("fulltext_transfer_changed", "Response range or total differs from the requested coverage")
                    etag = response_headers.get("etag", "")
                    if ranges and (etag != ranges[0]["etag"] or final_url != ranges[0]["url"]):
                        raise ResearchError("fulltext_transfer_changed", "Continuation changed its final URL or validator")
                    if not _STRONG_ETAG.fullmatch(etag):
                        raise ResearchError("fulltext_resume_unsupported", "Range continuation requires a strong ETag; request a separate complete transfer")
                    if actual_total > max_original_bytes:
                        raise ResearchError("response_too_large", "Original exceeds the original-byte allowance")
                    expected = actual_end - actual_start + 1
                    if "content-length" in response_headers and int(response_headers["content-length"]) != expected:
                        raise ResearchError("fulltext_transfer_changed", "Range Content-Length differs from its exact coverage")
                    identity.update(kind="range", url=final_url, etag=etag,
                                    start=actual_start, end=actual_end, total=actual_total)
                    return expected

                try:
                    fetch = self.client.get_stream(url, self.artifacts, max_bytes=max_original_bytes,
                        max_retained_bytes=max_retained_bytes - used, total_timeout=remaining,
                        headers=request_headers, accept=accept, budget=budget, not_before=not_before,
                        validate_response=validate_response)
                except HttpFailure as error:
                    attempts.extend(error.attempts)
                    if (error.code in ("timeout", "incomplete_response", "network_error") and error.attempts
                            and error.attempts[-1]["status"] == 200):
                        raise ResearchError("fulltext_resume_unsupported", "Interrupted complete transfer must restart",
                                            {"cause": error.code}) from error
                    raise ResearchError(error.code, error.message, error.details) from error
                attempts.extend(fetch.attempts)
                for attempt in fetch.attempts:
                    reference = attempt.get("response")
                    if reference is not None and reference["sha256"] not in retained_refs:
                        retained_refs[reference["sha256"]] = reference
                        used += reference["size"]
                unit = dict(identity, requested_url=url, encoding="identity", artifact=fetch.response,
                            attempts=fetch.attempts)
                if unit["kind"] == "complete":
                    unit.update(total=fetch.response["size"], http_status=200)
                    unit["id"] = digest([job_id, unit])
                    self._commit_unit(job_id, unit)
                    return self._build_result(unit, fetch.response, attempts, len(units) + 1, deadline)
                unit["id"] = digest([job_id, unit])
                self._commit_unit(job_id, unit)
                units.append(unit)
                ranges.append(unit)
            ranges, _ = self._validate_units(units, url)
            # put_stream writes one assembly temporary; open_verified freezes one
            # range at a time. Both are charged in addition to retained evidence.
            total = ranges[0]["total"]
            scratch = max(unit["artifact"]["size"] for unit in ranges)
            if used + total + scratch > max_retained_bytes:
                raise ResearchError("fulltext_storage_limit", "Assembly and verified range scratch exceed the retained-byte allowance")
            with closing(_RangeReader(self.artifacts, ranges, self.client.clock, deadline)) as source:
                response = self.artifacts.put_stream(source, ranges[0]["artifact"]["media_type"])
            if response["size"] != total:
                raise ResearchError("fulltext_transfer_changed", "Assembly does not cover the whole original")
            return self._build_result(ranges[0], response, attempts, len(units), deadline, assembled=True)
        except ResearchError as error:
            raise HttpFailure(error.code, error.message, attempts, error.details) from error
