"""Evidence access for one snapshot: verified bytes, indexes and memoized reports.

An Evaluation wraps the artifact store for one records snapshot. Every object
is read and hash-verified once per Evaluation; derived reports are computed
once per key. Nothing here outlives the process or the snapshot it was built
for, and nothing here grants readiness: the same checks run, once.
"""

import weakref

from .artifacts import validate_reference
from .errors import ResearchError
from .evidence import digest
from .source_links import validate_link


class Evaluation:
    def __init__(self, records, artifacts, *, _owner=None):
        # A new evaluation owns fresh caches even when its caller supplies an
        # older evaluation as the artifact interface. Only of() may reuse one.
        while isinstance(artifacts, Evaluation):
            artifacts = artifacts.store
        from .storage import _HistoricalReadCache, _HistoricalSnapshot
        if _owner is None and isinstance(records, _HistoricalSnapshot):
            cache = _HistoricalReadCache(records)
            records = records.read_snapshot(records.read_revision(), cache=cache)
        self.records, self.store, self.root = records, artifacts, artifacts.root
        self._historical_cache = getattr(records, "_cache", None)
        self._owner = None if _owner is None else weakref.ref(_owner)
        self._revisions = {}
        # Artifact references are content-addressed, so a child Evaluation
        # shares the verified bytes and text of its root instead of copying them.
        self._bytes, self._text = ({}, {}) if _owner is None else (_owner._bytes, _owner._text)
        self._memo = {}
        self.counters = {"reads": 0, "artifacts_verified": 0, "bytes_verified": 0, "computed": 0,
                         "readings_assessed": 0, "links_validated": 0, "graph_builds": 0, "cohort_reports": 0,
                         "working_bytes_hashed": 0}

    @classmethod
    def reuse_evaluation(cls, records, artifacts):
        """Reuse the caller's Evaluation when it was built for these records; otherwise start one."""
        if isinstance(artifacts, cls) and artifacts.records is records:
            return artifacts
        if isinstance(artifacts, cls):
            for child in artifacts.find_root()._revisions.values():
                if child.records is records:
                    return child
        return cls(records, artifacts)

    of = reuse_evaluation

    def find_root(self):
        owner = None if self._owner is None else self._owner()
        return self if owner is None else owner

    def compute_once_in_root(self, key, compute):
        """Namespace command-level derived results by this exact snapshot view."""
        return self.find_root().once(("revision", id(self), key), compute)

    def read(self, reference):
        self.counters["reads"] += 1
        validate_reference(reference)
        key = (reference["sha256"], reference["size"], reference["path"], reference["media_type"])
        if key not in self._bytes:
            data = self.store.read(reference)
            self.counters["artifacts_verified"] += 1
            self.counters["bytes_verified"] += len(data)
            self._bytes[key] = data
        return self._bytes[key]

    def text(self, reference):
        data = self.read(reference)
        key = reference["sha256"]
        if key not in self._text:
            self._text[key] = data.decode("utf-8")
        return self._text[key]

    def put(self, data, media_type):
        return self.store.put(data, media_type)

    def once(self, key, compute):
        if key not in self._memo:
            self.counters["computed"] += 1
            self._memo[key] = compute()
        return self._memo[key]

    def read_snapshot(self, revision):
        """Reuse one child Evaluation per authenticated historical revision."""
        from .storage import _HistoricalReadCache
        if (not hasattr(self.records, "read_snapshot") or type(revision) is not int
                or not 0 <= revision <= self.records.read_revision()):
            raise ResearchError("claim_reassessment_required", "The recorded input snapshot is unavailable; reassess the bound record.",
                                {"record_id": "snapshot:" + str(revision)})
        root = self.find_root()
        if root._historical_cache is None:
            root._historical_cache = _HistoricalReadCache(root.records)
        records = self.records.read_snapshot(revision, cache=root._historical_cache)
        if revision not in root._revisions:
            child = Evaluation(records, root.store, _owner=root)
            root._revisions[revision] = child
        return root._revisions[revision]

    def readings_for(self, version_id):
        index = self.once(("readings",), lambda: _index(self.records.get("reading", {}).values()))
        return index.get(version_id, [])

    def link(self, link):
        def compute():
            self.counters["links_validated"] += 1
            return validate_link(self.records, self, link)
        return self.once(("link", digest(link)), compute)


def _index(readings):
    index = {}
    for reading in sorted(readings, key=lambda r: r["id"]):
        index.setdefault(reading["version_id"], []).append(reading)
    return index
