"""Snapshot-local verified access with bounded optional Python-value retention.

The root owns a weighted LRU (64 MiB by default). We charge recursive shallow
Python sizes, including containers, keys and entry overhead, without deduplicating
between entries. Oversized values are returned uncached. Authenticated snapshots,
history indexes, standalone structural indexes and active guards are not
evictable; their accounting is separate from the optional budget. Retained
historical children also charge their indexes, repriced when an index is built. Neither number bounds process RSS
or the temporary memory needed to build one request or parse one JSON value.
"""

from collections import OrderedDict
import sys
import weakref

from .artifacts import ArtifactStore, validate_reference
from .errors import ResearchError
from .evidence import digest
from .source_links import validate_link


def _weigh_value(value):
    seen, pending, total = set(), [value], 0
    while pending:
        item = pending.pop()
        if id(item) in seen:
            continue
        seen.add(id(item))
        total += sys.getsizeof(item)
        if isinstance(item, Evaluation):
            # Optional origin evaluations share this arena. Their copied records
            # and object metadata are charged here; indexes are reported separately.
            pending.extend((item.records, item.counters, item._namespace, item._byte_namespace))
            if item._owner is not None:
                pending.append(item._structural)
            total += sys.getsizeof(vars(item))
        elif isinstance(item, dict):
            pending.extend(item.keys())
            pending.extend(item.values())
        elif isinstance(item, (list, tuple, set, frozenset)):
            pending.extend(item)
    return total


class _HistoricalEvaluationReads:
    """Keep decoded historical values in the same optional budget as their users."""
    def __init__(self, records, evaluation):
        self._history = records._history
        self._revision, self._values = None, {}
        self._decoded_revision = None
        self._current = records if records._current_source is None else records._current_source
        self._verified_current = {}
        self._evaluation = weakref.ref(evaluation)

    def select_revision(self, revision):
        """Preserve the historical reader's single-revision decoded-row lifetime."""
        if self._decoded_revision == revision and self._revision == revision:
            return
        evaluation = self._evaluation()
        if evaluation is not None:
            for key in list(evaluation._optional):
                if (key[:2] == ('memo', evaluation._namespace) and isinstance(key[2], tuple)
                        and key[2][:1] == ('historical_record',)):
                    _, weight = evaluation._optional.pop(key)
                    evaluation._retained_bytes[0] -= weight
        self._decoded_revision = revision
        self._revision = revision

    def read_record(self, kind, key, revision, recorded_digest=None):
        self.select_revision(revision)
        entry = self._history.resolve_record(kind, key, revision, recorded_digest)
        if entry is None:
            return None
        from .storage import _canonical, _digest, _HistoricalSnapshot
        identity = (kind, key, entry[1])
        latest = self._history._versions[kind][key][-1]
        if entry[1] == latest[1] and not isinstance(self._current, _HistoricalSnapshot):
            if identity not in self._verified_current:
                current = self._current.get(kind, {}).get(key)
                self._verified_current[identity] = (current if current is not None
                    and _digest(_canonical(current)) == entry[1] else None)
            current = self._verified_current[identity]
            if current is not None:
                return current
        evaluation = self._evaluation()
        if evaluation is None:
            return self._history.read_record(kind, key, revision, recorded_digest)
        return evaluation.once(('historical_record', kind, key, entry[1]),
                               lambda: self._history.read_record(kind, key, revision, recorded_digest))


class Evaluation:
    def __init__(self, records, artifacts, *, retention_budget_bytes=None, _owner=None):
        inherited = artifacts if isinstance(artifacts, Evaluation) else _owner
        if retention_budget_bytes is None:
            retention_budget_bytes = inherited.retention_budget_bytes if inherited is not None else 64 * 1024 * 1024
        if type(retention_budget_bytes) is not int or retention_budget_bytes < 0:
            raise ResearchError('invalid_input', 'The evaluation retention budget must be a nonnegative integer')
        while isinstance(artifacts, Evaluation):
            artifacts = artifacts.store
        from .storage import _HistoricalSnapshot, _RecordedSnapshot, _HistoricalKind
        historical = _owner is None and isinstance(records, _HistoricalSnapshot)
        overlay = _owner is None and isinstance(records, _RecordedSnapshot) and any(
            isinstance(kind, _HistoricalKind) for kind in records.values())
        self._source_records = records
        self.records, self.store, self.root = records, artifacts, artifacts.root
        self.retention_budget_bytes = retention_budget_bytes
        self._historical_cache = None if _owner is None else _owner._historical_cache
        self._owner = None if _owner is None else weakref.ref(_owner)
        self._revisions = weakref.WeakValueDictionary()
        arena = inherited if inherited is not None and inherited.retention_budget_bytes == retention_budget_bytes else None
        self._optional = OrderedDict() if arena is None else arena._optional
        self._retained_bytes = [0] if arena is None else arena._retained_bytes
        self._views = weakref.WeakSet() if arena is None else arena._views
        self._views.add(self)
        self._byte_namespace = object() if _owner is None else _owner._byte_namespace
        self._namespace = object()
        self._revision_cache_key = None
        self._structural = {}
        self._active_states = set()
        self._runtime_scope = [None] if inherited is None else inherited._runtime_scope
        if historical or overlay:
            self._historical_cache = _HistoricalEvaluationReads(records, self)
            historical_records = records.read_snapshot(records.read_revision(), cache=self._historical_cache)
            if historical:
                self.records = historical_records
            else:
                self.records = _RecordedSnapshot(
                    {kind: _HistoricalKind(historical_records, kind) if isinstance(rows, _HistoricalKind) else rows
                     for kind, rows in records.items()}, history=records._history, revision=records.read_revision(),
                    current_source=records._current_source)
        self.counters = {"reads": 0, "artifacts_verified": 0, "bytes_verified": 0, "computed": 0,
                         "readings_assessed": 0, "links_validated": 0, "graph_builds": 0, "cohort_reports": 0,
                         "working_bytes_hashed": 0}

    @classmethod
    def reuse_evaluation(cls, records, artifacts, *, retention_budget_bytes=None):
        """Reuse only this exact view, carrying the caller's retention policy."""
        if retention_budget_bytes is not None and (type(retention_budget_bytes) is not int or retention_budget_bytes < 0):
            raise ResearchError('invalid_input', 'The evaluation retention budget must be a nonnegative integer')
        scoped = getattr(records, '_review_evaluation', None)
        scoped = None if scoped is None else scoped()
        if scoped is not None and (scoped.records is records or scoped._source_records is records) and (scoped.store is artifacts or
                type(scoped.store) is ArtifactStore and type(artifacts) is ArtifactStore and scoped.root == artifacts.root):
            artifacts = scoped
        if isinstance(artifacts, cls):
            budget = artifacts.retention_budget_bytes if retention_budget_bytes is None else retention_budget_bytes
            if budget == artifacts.retention_budget_bytes:
                if artifacts.records is records or artifacts._source_records is records:
                    return artifacts
                for child in list(artifacts.find_root()._revisions.values()):
                    if child.records is records:
                        return child
        if retention_budget_bytes is None:
            return cls(records, artifacts)
        return cls(records, artifacts, retention_budget_bytes=retention_budget_bytes)

    of = reuse_evaluation

    def find_root(self):
        owner = None if self._owner is None else self._owner()
        return self if owner is None else owner

    def compute_once_in_root(self, key, compute):
        return self.find_root().once(('revision', self._namespace, key), compute)

    def _retain_once(self, key, compute, *, refresh=False):
        scope = self._runtime_scope[0]
        if scope is not None and self.retention_budget_bytes:
            scope['arenas'][id(self._optional)] = (self._optional, self._retained_bytes)
        if key in self._optional:
            value, weight = self._optional.pop(key)
            if not refresh:
                self._optional[key] = value, weight
                return value
            self._retained_bytes[0] -= weight
        if compute is None:
            return None
        value = compute()
        budget = self.retention_budget_bytes
        if budget == 0:
            return value
        # OrderedDict linkage and the stored (value, weight) pair are charged
        # in addition to recursively sized keys and values.
        weight = _weigh_value((key, value)) + sys.getsizeof((None, 0)) + 256
        if weight <= budget:
            while self._optional and self._retained_bytes[0] + weight > budget:
                _, (_, removed) = self._optional.popitem(last=False)
                self._retained_bytes[0] -= removed
            self._optional[key] = value, weight
            self._retained_bytes[0] += weight
        return value

    def retention_report(self):
        root = self.find_root()
        views = list(self._views)
        return {'budget_bytes': self.retention_budget_bytes,
                'optional_bytes': self._retained_bytes[0],
                'optional_entries': len(self._optional),
                'structural_bytes': _weigh_value([(view._structural, view._active_states) for view in views] +
                    [root._historical_cache._verified_current if root._historical_cache is not None else {}]),
                'snapshot_bytes': _weigh_value([view.records for view in views]),
                'history_index_bytes': _weigh_value(vars(root.records._history)) if hasattr(root.records, '_history') else 0}

    def read(self, reference):
        self.counters['reads'] += 1
        validate_reference(reference)
        key = tuple(reference[name] for name in ('sha256', 'size', 'path', 'media_type'))
        def verify():
            data = self.store.read(reference)
            self.counters['artifacts_verified'] += 1
            self.counters['bytes_verified'] += len(data)
            return data
        return self._retain_once(('bytes', self._byte_namespace, key), verify)

    def text(self, reference):
        data = self.read(reference)
        return self._retain_once(('text', self._byte_namespace, reference['sha256']), lambda: data.decode('utf-8'))

    def put(self, data, media_type):
        return self.store.put(data, media_type)

    def require_external_effect(self):
        self.store.require_external_effect()

    def once(self, key, compute):
        def calculate():
            self.counters['computed'] += 1
            return compute()
        return self._retain_once(('memo', self._namespace, key), calculate)

    def index_records(self, kind, field, *, order=None):
        """Index immutable rows once, preserving insertion order for equal sort keys."""
        key = ('index', kind, field, order)
        if key not in self._structural:
            rows = self.records.get(kind, {}).values()
            if order is not None:
                rows = sorted(rows, key=lambda row: row[order])
            index = {}
            for row in rows:
                index.setdefault(row[field], []).append(row)
            self._structural[key] = index
            child_key = self._revision_cache_key
            if child_key is not None and child_key in self._optional and self._optional[child_key][0] is self:
                self._retain_once(child_key, lambda: self, refresh=True)
        return self._structural[key]

    def read_snapshot(self, revision):
        """Share authenticated history and budget; inactive child retention is optional."""
        if (not hasattr(self.records, 'read_snapshot') or type(revision) is not int
                or not 0 <= revision <= self.records.read_revision()):
            raise ResearchError('claim_reassessment_required', 'The recorded input snapshot is unavailable; reassess the bound record.',
                                {'record_id': 'snapshot:' + str(revision)})
        root = self.find_root()
        if root._historical_cache is None:
            root._historical_cache = _HistoricalEvaluationReads(root.records, root)
        root._historical_cache.select_revision(revision)
        records = self.records.read_snapshot(revision, cache=root._historical_cache)
        child = root._revisions.get(revision)
        if child is None:
            child = Evaluation(records, root, _owner=root)
            root._revisions[revision] = child
        # Retain inactive views only while their metadata and indexes fit the
        # shared budget; the weak registry also finds externally active views.
        child._revision_cache_key = ('child', root._namespace, revision)
        return root._retain_once(child._revision_cache_key, lambda: child)

    def readings_for(self, version_id):
        index = self.index_records('reading', 'version_id', order='id')
        return index.get(version_id, [])

    def link(self, link):
        def compute():
            self.counters['links_validated'] += 1
            return validate_link(self.records, self, link)
        return self.once(('link', digest(link)), compute)
