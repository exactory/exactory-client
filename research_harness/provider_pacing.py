"""Durable provider admission shared by local HTTP processes and workspaces."""

import fcntl
import math
import os
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit

from .errors import ResearchError


# https://info.arxiv.org/help/api/tou.html: one request every three seconds, one connection at a time.
_ARXIV_REQUEST_INTERVAL_SECONDS = 3.0
_PROVIDER_POLICIES = {"arxiv": {"interval": _ARXIV_REQUEST_INTERVAL_SECONDS, "connection_limit": 1}}
_DATABASE = "provider-pacing.sqlite3"


def group_provider(url):
    from .http import safe_url
    parts = urlsplit(safe_url(url))
    host = parts.hostname.lower()
    if host == "arxiv.org" or host.endswith(".arxiv.org"):
        return "arxiv"
    return "https://" + ("[" + host + "]" if ":" in host else host)


def _parse_time(value):
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.utcoffset() is None:
            raise ValueError()
        seconds = parsed.timestamp()
        if not math.isfinite(seconds):
            raise ValueError()
        return seconds
    except (AttributeError, TypeError, ValueError, OverflowError) as error:
        raise ResearchError("invalid_input", "Provider eligibility must be an ISO timestamp with timezone") from error


class ProviderPacing:
    def __init__(self, *, clock, state_dir=None, row_limit=4096, intervals=None):
        if type(row_limit) is not int or row_limit <= 0:
            raise ResearchError("invalid_input", "Provider state row allowance must be a positive integer")
        base = os.environ.get("XDG_STATE_HOME", "").strip()
        self.state_dir = Path(state_dir) if state_dir is not None else (
            Path(base) if base else Path.home() / ".local" / "state") / "exactory"
        self.clock, self.row_limit = clock, row_limit
        self.intervals = {provider: policy["interval"] for provider, policy in _PROVIDER_POLICIES.items()}
        self.connection_limits = {provider: policy["connection_limit"] for provider, policy in _PROVIDER_POLICIES.items()
                                  if policy.get("connection_limit")}
        for origin, interval in (intervals or {}).items():
            if type(interval) not in (int, float) or not math.isfinite(interval) or interval < 0:
                raise ResearchError("invalid_input", "Provider intervals must be nonnegative and finite")
            self.intervals[group_provider(origin)] = float(interval)

    def _open_file(self, name):
        self.state_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
        return os.open(str(self.state_dir / name), os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)

    def _try_lock(self, provider):
        descriptor = self._open_file("provider-" + provider + ".lock")
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return descriptor
        except BlockingIOError:
            os.close(descriptor)
            return None
        except BaseException:
            os.close(descriptor)
            raise

    def _wait(self, seconds, deadline, provider, eligible=None):
        remaining = deadline - self.clock.monotonic()
        details = {"provider_group": provider, "next_eligible_at": eligible,
                   "retry_after_seconds": seconds}
        if remaining <= 0 or (eligible is not None and seconds >= remaining):
            raise ResearchError("rate_limited", "Provider admission exceeds this call's wait allowance", details)
        if seconds > 0:
            self.clock.sleep(min(seconds, remaining))
        if self.clock.monotonic() >= deadline:
            raise ResearchError("rate_limited", "Provider admission exceeded this call's wait allowance", details)

    def _execute_until(self, connection, statement, deadline, provider):
        while True:
            try:
                connection.execute(statement)
                return
            except sqlite3.OperationalError as error:
                # Python 3.9 exposes neither the result constants nor sqlite_errorcode.
                if getattr(error, "sqlite_errorcode", None) not in (5, 6) and str(error) not in (
                        "database is locked", "database table is locked"):
                    raise
                try:
                    self._wait(0.05, deadline, provider)
                except ResearchError as failure:
                    raise ResearchError("provider_state_unavailable", "Provider scheduling state remained busy") from failure

    @contextmanager
    def _edit_state(self, deadline, provider):
        connection = None
        try:
            os.close(self._open_file(_DATABASE))
            connection = sqlite3.connect(str(self.state_dir / _DATABASE), timeout=0, isolation_level=None)
            self._execute_until(connection, "BEGIN IMMEDIATE", deadline, provider)
            connection.execute("""CREATE TABLE IF NOT EXISTS provider_state (
                provider TEXT PRIMARY KEY, next_start REAL NOT NULL, cooldown REAL NOT NULL,
                CHECK(typeof(next_start) IN ('integer', 'real') AND next_start >= 0),
                CHECK(typeof(cooldown) IN ('integer', 'real') AND cooldown >= 0))""")
            now = _parse_time(self.clock.now())
            expired = connection.execute(
                "SELECT provider FROM provider_state WHERE next_start <= ? AND cooldown <= ?", (now, now)).fetchall()
            for (expired_provider,) in expired:
                if not isinstance(expired_provider, str):
                    raise ResearchError("provider_state_unavailable", "Provider scheduling key is invalid")
                self._read_constraints(connection, expired_provider)
                descriptor = None
                if expired_provider in self.connection_limits:
                    descriptor = self._try_lock(expired_provider)
                    if descriptor is None:
                        continue
                try:
                    connection.execute("DELETE FROM provider_state WHERE provider = ?", (expired_provider,))
                finally:
                    if descriptor is not None:
                        os.close(descriptor)
            yield connection
            self._execute_until(connection, "COMMIT", deadline, provider)
        except (OSError, sqlite3.Error, UnicodeError) as error:
            raise ResearchError("provider_state_unavailable", "Provider scheduling state is corrupt or unavailable") from error
        finally:
            if connection is not None:
                connection.close()

    def _read_constraints(self, connection, provider):
        row = connection.execute("SELECT next_start, cooldown FROM provider_state WHERE provider = ?", (provider,)).fetchone()
        if row is None:
            return 0.0, 0.0
        if any(type(value) not in (int, float) or not math.isfinite(value) or value < 0 for value in row):
            raise ResearchError("provider_state_unavailable", "Provider scheduling timestamps are invalid")
        try:
            for value in row:
                datetime.fromtimestamp(value, timezone.utc)
        except (ValueError, OverflowError, OSError) as error:
            raise ResearchError("provider_state_unavailable", "Provider scheduling timestamps are invalid") from error
        return row

    def _write_constraints(self, connection, provider, next_start, cooldown):
        exists = connection.execute("SELECT 1 FROM provider_state WHERE provider = ?", (provider,)).fetchone()
        if exists is None and connection.execute("SELECT count(*) FROM provider_state").fetchone()[0] >= self.row_limit:
            raise ResearchError("provider_state_capacity", "Live provider deadlines occupy the scheduling state allowance",
                                {"provider_group": provider, "row_limit": self.row_limit})
        connection.execute("""INSERT INTO provider_state(provider, next_start, cooldown) VALUES (?, ?, ?)
            ON CONFLICT(provider) DO UPDATE SET next_start = excluded.next_start, cooldown = excluded.cooldown""",
                           (provider, next_start, cooldown))

    def defer_until(self, url, eligible_at, *, deadline, max_wait, pacing=False):
        provider = group_provider(url)
        eligible = _parse_time(eligible_at)
        wait_deadline = min(deadline, self.clock.monotonic() + max_wait)
        with self._edit_state(wait_deadline, provider) as connection:
            next_start, cooldown = self._read_constraints(connection, provider)
            if eligible > _parse_time(self.clock.now()):
                self._write_constraints(connection, provider,
                                        max(next_start, eligible) if pacing else next_start,
                                        cooldown if pacing else max(cooldown, eligible))

    @contextmanager
    def admit(self, url, *, deadline, max_wait):
        """Admit one attempt; a group with a connection policy keeps its lock until the caller's block exits."""
        provider = group_provider(url)
        wait_deadline = min(deadline, self.clock.monotonic() + max_wait)
        descriptor = None
        try:
            try:
                while True:
                    self._wait(0, wait_deadline, provider)
                    with self._edit_state(wait_deadline, provider) as connection:
                        eligible = max(self._read_constraints(connection, provider))
                    delay = max(0.0, eligible - _parse_time(self.clock.now()))
                    if delay:
                        self._wait(delay, wait_deadline, provider,
                                   datetime.fromtimestamp(eligible, timezone.utc).isoformat())
                        continue
                    if provider in self.connection_limits:
                        descriptor = self._try_lock(provider)
                        if descriptor is None:
                            self._wait(0.05, wait_deadline, provider)
                            continue
                    with self._edit_state(wait_deadline, provider) as connection:
                        next_start, cooldown = self._read_constraints(connection, provider)
                        now = _parse_time(self.clock.now())
                        if max(next_start, cooldown) <= now:
                            self._write_constraints(connection, provider, now + self.intervals.get(provider, 0.0),
                                                    cooldown)
                            break
                    if descriptor is not None:
                        os.close(descriptor)
                    descriptor = None
            except OSError as error:
                raise ResearchError("provider_state_unavailable", "Provider connection locks are unavailable") from error
        except BaseException:
            if descriptor is not None:
                os.close(descriptor)
            raise
        # Errors from the admitted request belong to the caller and pass through unchanged.
        try:
            yield provider
        finally:
            if descriptor is not None:
                os.close(descriptor)
