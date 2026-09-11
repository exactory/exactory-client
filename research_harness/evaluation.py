"""Evidence access for one snapshot: verified bytes, indexes and memoized reports.

An Evaluation wraps the artifact store for one records snapshot. Every object
is read and hash-verified once per Evaluation; derived reports are computed
once per key. Nothing here outlives the process or the snapshot it was built
for, and nothing here grants readiness: the same checks run, once.
"""

from .evidence import digest
from .source_links import validate_link


class Evaluation:
    def __init__(self, records, artifacts):
        self.records, self.store, self.root = records, artifacts, artifacts.root
        self._bytes, self._text, self._memo = {}, {}, {}
        self.counters = {"reads": 0, "artifacts_verified": 0, "bytes_verified": 0, "computed": 0,
                         "readings_assessed": 0, "links_validated": 0, "graph_builds": 0, "cohort_reports": 0}

    @classmethod
    def of(cls, records, artifacts):
        """Reuse the caller's Evaluation when it was built for these records; otherwise start one."""
        if isinstance(artifacts, cls) and artifacts.records is records:
            return artifacts
        return cls(records, artifacts)

    def read(self, reference):
        self.counters["reads"] += 1
        if not isinstance(reference, dict):
            return self.store.read(reference)
        key = (reference.get("sha256"), reference.get("size"), reference.get("path"), reference.get("media_type"))
        if key not in self._bytes:
            data = self.store.read(reference)
            self.counters["artifacts_verified"] += 1
            self.counters["bytes_verified"] += len(data)
            self._bytes[key] = data
        return self._bytes[key]

    def text(self, reference):
        key = reference["sha256"]
        if key not in self._text:
            self._text[key] = self.read(reference).decode("utf-8")
        return self._text[key]

    def put(self, data, media_type):
        return self.store.put(data, media_type)

    def once(self, key, compute):
        if key not in self._memo:
            self.counters["computed"] += 1
            self._memo[key] = compute()
        return self._memo[key]

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
