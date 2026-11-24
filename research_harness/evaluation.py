"""Snapshot-local verified access with bounded optional Python-value retention.

The root owns a shared optional budget (64 MiB by default). Compressed requests
and review states have a protected weighted LRU capped at half the budget. Ordinary
entries borrow unused space but never evict requests. We charge recursive shallow
Python sizes, including containers, keys and entry overhead, without deduplicating
between entries. Oversized values are returned uncached. Authenticated snapshots,
history indexes, standalone structural indexes and active guards are not
evictable; their accounting is separate from the optional budget. Retained
historical children also charge their indexes, repriced when an index is built. Neither number bounds process RSS
or the temporary memory needed to build one request or parse one JSON value.
"""

from collections import OrderedDict
from contextlib import contextmanager
import json
import marshal
import sys
import weakref
import zlib

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
        if inherited is None:
            # A plan's view, and an overlay nested in it, carry the evaluation of that plan: its arena, runtime scope
            # and prospective entries (planning_view).
            from .openness_reading_v7 import _PlanningArtifacts
            if isinstance(artifacts, _PlanningArtifacts):
                inherited = artifacts.planning_owner
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
        self.records, self.store, self.root = records, artifacts, getattr(artifacts, 'root', None)
        self.retention_budget_bytes = retention_budget_bytes
        self._historical_cache = None if _owner is None else _owner._historical_cache
        self._owner = None if _owner is None else weakref.ref(_owner)
        self._revisions = weakref.WeakValueDictionary()
        arena = inherited if inherited is not None and inherited.retention_budget_bytes == retention_budget_bytes else None
        self._optional = OrderedDict() if arena is None else arena._optional
        self._requests = OrderedDict() if arena is None else arena._requests
        self._retained_bytes = [0] if arena is None else arena._retained_bytes
        self._request_bytes = [0] if arena is None else arena._request_bytes
        self._views = weakref.WeakSet() if arena is None else arena._views
        self._views.add(self)
        self._byte_namespace = object() if _owner is None else _owner._byte_namespace
        self._namespace = object()
        self._revision_cache_key = None
        self._structural = {}
        self._active_states = set()
        self._runtime_scope = [None] if inherited is None else inherited._runtime_scope
        self._planning_entries = None if inherited is None else inherited._planning_entries
        self._planning_accounting = ({'overlays': weakref.WeakSet(), 'peak_bytes': 0, 'entries': None}
                                     if inherited is None else inherited._planning_accounting)
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
            evaluation = cls(records, artifacts)
        else:
            evaluation = cls(records, artifacts, retention_budget_bytes=retention_budget_bytes)
        if (isinstance(artifacts, cls) and artifacts._runtime_scope[0] is not None
                and evaluation._optional is artifacts._optional):
            evaluation._byte_namespace = artifacts._byte_namespace
        return evaluation

    of = reuse_evaluation

    @contextmanager
    def planning_view(self):
        """Share the command arena and keep each plan's prospective bytes in its own view."""
        from .openness_reading_v7 import _PlanningArtifacts
        if isinstance(self.store, _PlanningArtifacts):
            yield self
            return
        accounting = self._planning_accounting
        overlay = _PlanningArtifacts(self, accounting=accounting, checks_destinations=True)
        preview = Evaluation(self.records, overlay)
        preview._structural = self._structural
        preview.counters = self.counters
        enclosing, earlier_views = accounting['entries'], set(accounting['overlays']) - {overlay}
        preview._planning_entries = accounting['entries'] = set()
        try:
            yield preview
        finally:
            for view in list(accounting['overlays']):
                if view not in earlier_views:
                    view.clear()
            accounting['entries'] = enclosing
            for request, key in preview._planning_entries:
                entries = self._requests if request else self._optional
                value = entries.pop(key, None)
                if value is not None:
                    weight = value[1]
                    self._retained_bytes[0] -= weight
                    if request:
                        self._request_bytes[0] -= weight

    def find_root(self):
        owner = None if self._owner is None else self._owner()
        return self if owner is None else owner

    def compute_once_in_root(self, key, compute):
        return self.find_root().once(('revision', self._namespace, key), compute)

    def _retain_once(self, key, compute, *, refresh=False, request=False):
        scope = self._runtime_scope[0]
        if scope is not None and self.retention_budget_bytes:
            scope['arenas'][id(self._optional)] = (self._optional, self._retained_bytes)
            scope['arenas'][id(self._requests)] = (self._requests, self._request_bytes)
        entries = self._requests if request else self._optional
        if key in entries:
            value, weight = entries.pop(key)
            if not refresh:
                entries[key] = value, weight
                return value
            self._retained_bytes[0] -= weight
            if request:
                self._request_bytes[0] -= weight
        if compute is None:
            return None
        value = compute()
        budget = self.retention_budget_bytes
        if budget == 0:
            return value
        # OrderedDict linkage and the stored (value, weight) pair are charged
        # in addition to recursively sized keys and values.
        weight = _weigh_value((key, value)) + sys.getsizeof((None, 0)) + (512 if request else 256)
        capacity = budget // 2 if request else budget - self._request_bytes[0]
        if weight <= capacity and not (request and value is None):
            # The open plan forgets an evicted entry, so its list stays within the arena.
            planned = self._planning_accounting['entries'] or set()
            if request:
                while self._requests and self._request_bytes[0] + weight > capacity:
                    evicted, (_, removed) = self._requests.popitem(last=False)
                    planned.discard((True, evicted))
                    self._request_bytes[0] -= removed
                    self._retained_bytes[0] -= removed
            while self._optional and self._retained_bytes[0] + weight > budget:
                evicted, (_, removed) = self._optional.popitem(last=False)
                planned.discard((False, evicted))
                self._retained_bytes[0] -= removed
            entries[key] = value, weight
            if self._planning_entries is not None:
                self._planning_entries.add((request, key))
            self._retained_bytes[0] += weight
            if request:
                self._request_bytes[0] += weight
        return value

    def request_once(self, key, compute):
        """Share compressed pure derivations across views of this command's arena."""
        return self._retain_once(key, compute, request=True)

    def retention_report(self):
        root = self.find_root()
        views = list(self._views)
        return {'budget_bytes': self.retention_budget_bytes,
                'planning_live_bytes': sum(view._planning_bytes for view in self._planning_accounting['overlays']),
                'planning_peak_bytes': self._planning_accounting['peak_bytes'],
                'optional_bytes': self._retained_bytes[0],
                'optional_entries': len(self._optional) + len(self._requests),
                'request_bytes': self._request_bytes[0],
                'request_entries': len(self._requests),
                'structural_bytes': _weigh_value([(view._structural, view._active_states) for view in views] +
                    [root._historical_cache._verified_current if root._historical_cache is not None else {}]),
                'snapshot_bytes': _weigh_value([view.records for view in views]),
                'history_index_bytes': _weigh_value(vars(root.records._history)) if hasattr(root.records, '_history') else 0}

    def read(self, reference):
        self.counters['reads'] += 1
        validate_reference(reference)
        key = tuple(reference[name] for name in ('sha256', 'size', 'path', 'media_type'))
        for decoder in ('json', 'strict_json'):
            parsed = self.reuse_value((decoder, self._byte_namespace, key),
                                      namespace=self._byte_namespace)
            if parsed is not None:
                return parsed[0]
        def verify():
            data = self.store.read(reference)
            self.counters['artifacts_verified'] += 1
            self.counters['bytes_verified'] += len(data)
            return data
        return self._retain_once(('bytes', self._byte_namespace, key), verify)

    def text(self, reference):
        data = self.read(reference)
        return self._retain_once(('text', self._byte_namespace, reference['sha256']), lambda: data.decode('utf-8'))

    def read_json(self, reference, *, strict=False):
        """Reuse a verified parse, retaining descriptor checks and caller isolation."""
        validate_reference(reference)
        identity = tuple(reference[name] for name in ('sha256', 'size', 'path', 'media_type'))
        key = ('strict_json' if strict else 'json', self._byte_namespace, identity)

        def parse():
            data = self.read(reference)
            if strict:
                from .workspace import strict_json
                return data, strict_json(data)
            return data, json.loads(data)

        _, value = self.reuse_value(key, parse, namespace=self._byte_namespace)
        retained_key = ('memo', self._byte_namespace, ('encoded_value', key))
        if retained_key in self._optional:
            removed = self._optional.pop(('bytes', self._byte_namespace, identity), None)
            if removed is not None:
                self._retained_bytes[0] -= removed[1]
        return value

    def put(self, data, media_type):
        return self.store.put(data, media_type)

    def require_external_effect(self):
        self.store.require_external_effect()

    def once(self, key, compute):
        def calculate():
            self.counters['computed'] += 1
            return compute()
        return self._retain_once(('memo', self._namespace, key), None if compute is None else calculate)

    def reuse_value(self, key, compute=None, *, protected=False, namespace=None):
        """Retain compact native values in this arena and return an isolated value.

        The binary representation is produced here, never read from artifacts or
        persisted. Encoding avoids retaining copies of large overlapping packets;
        decoding avoids repeating JSON parsing and Python-level deep copies.
        """
        if namespace is None:
            namespace = self._namespace

        def encode():
            self.counters['computed'] += 1
            return zlib.compress(marshal.dumps(compute(), 4), 1)

        if protected:
            key = ('encoded_value', namespace, key)
            encoded = self.request_once(key, None if compute is None else encode)
        else:
            encoded = self._retain_once(('memo', namespace, ('encoded_value', key)),
                                        None if compute is None else encode)
        return None if encoded is None else marshal.loads(zlib.decompress(encoded))

    def index_records(self, kind, field, *, order=None, where=None):
        """Group immutable rows once by a field or key function, with an optional scoped read."""
        key = ('index', kind, field, order)
        if where:
            key += (tuple(sorted(where.items())),)
        if key not in self._structural:
            if where:
                from .storage import read_filtered_records
                rows = read_filtered_records(self.records, kind, **where).values()
            else:
                rows = self.records.get(kind, {}).values()
            if order is not None:
                rows = sorted(rows, key=lambda row: row[order])
            index = {}
            computed = callable(field)
            for row in rows:
                group = field(row) if computed else row[field]
                if group is not None or not computed:
                    index.setdefault(group, []).append(row)
            self._structural[key] = index
            child_key = self._revision_cache_key
            if child_key is not None and child_key in self._optional and self._optional[child_key][0] is self:
                self._retain_once(child_key, lambda: self, refresh=True)
        return self._structural[key]

    @staticmethod
    def assignment_rows(records, **where):
        """Index immutable assignment filters during a command, keeping scoped reads authoritative."""
        from .storage import _matches_record_filter, read_filtered_records
        attached = getattr(records, '_review_evaluation', None)
        evaluation = None if attached is None else attached()
        if (getattr(records, 'read_filtered_records', None) is not None or evaluation is None
                or evaluation._source_records is not records and evaluation.records is not records):
            return read_filtered_records(records, 'review_assignment', **where).values()
        fields = tuple(where)
        values = tuple(where.values())
        key = ('assignment_filters', fields)
        if key not in evaluation._structural:
            groups = {}
            for row in records.get('review_assignment', {}).values():
                identity = []
                for field in fields:
                    value = row
                    for part in field.split('.'):
                        value = value.get(part) if isinstance(value, dict) else None
                    identity.append(value)
                try:
                    groups.setdefault(tuple(identity), []).append(row)
                except TypeError:
                    # A damaged binding cannot equal one of these scalar filter keys.
                    continue
            evaluation._structural[key] = groups
        try:
            return evaluation._structural[key].get(values, ())
        except TypeError:
            return (row for row in records.get('review_assignment', {}).values() if _matches_record_filter(row, where))

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
