"""Reuse complete review states only for one records view and current runtimes."""

import copy
from contextlib import contextmanager
import weakref

from .errors import ResearchError
from .evaluation import Evaluation
from .evidence import digest
from . import review_transport as transport
from .review_exact_text_cost_v1 import profile


def _runtime_fingerprint(records, artifacts):
    """Observe current adapters and emitter; an unavailable observation disables reuse."""
    try:
        representatives = {}
        for route in records.get('review_route', {}).values():
            representatives.setdefault(route['adapter'], route)
        from .review_protocol import read_route_runtime
        return digest({'adapters': {adapter: read_route_runtime(artifacts, representatives[adapter])
                                   for adapter in sorted(representatives)},
                       'exact_text_cost': profile()})
    except Exception:
        # This observation only decides whether reuse is possible. The native
        # computation still checks its actual dependencies and keeps its errors.
        return None


@contextmanager
def runtime_scope(records, artifacts):
    """Own runtime observations for one read or preparation, ending before I/O."""
    evaluation = Evaluation.of(records, artifacts)
    root = evaluation.find_root()
    if root._runtime_scope[0] is not None:
        yield evaluation
        return
    before = _runtime_fingerprint(evaluation.records, evaluation)
    scope = {'fingerprint': before, 'arenas': {
        id(root._optional): (root._optional, root._retained_bytes),
        id(root._requests): (root._requests, root._request_bytes)}}
    root._runtime_scope[0] = scope
    previous = getattr(records, '_review_evaluation', None)
    # Preparation mappings carry ownership to callbacks that still accept the
    # raw store interface (including correction preparation). Plain dict users
    # pass the yielded Evaluation explicitly.
    attached = hasattr(records, '__dict__')
    if attached:
        records._review_evaluation = weakref.ref(evaluation)
    try:
        yield evaluation
        if before is not None and _runtime_fingerprint(evaluation.records, evaluation) != before:
            raise ResearchError('review_runtime_changed', 'The reviewer runtime changed during this operation; retry against the current runtime.')
    except BaseException:
        for entries, retained_bytes in scope['arenas'].values():
            entries.clear()
            retained_bytes[0] = 0
        raise
    finally:
        root._runtime_scope[0] = None
        if attached:
            if previous is None:
                del records._review_evaluation
            else:
                records._review_evaluation = previous


def _fingerprint_selection(records, identifier, kind):
    """Bind reuse to this controller's epoch, task generations and selected roots."""
    if kind == 'adjudication':
        identifier = records.get('review_adjudication', {}).get(identifier, {}).get('assignment_id', identifier)
    assignment = records.get('review_assignment', {}).get(identifier, {})
    binding = assignment.get('staged_review', {})
    epoch_id = binding.get('epoch_id')
    controller_id = binding.get('controller_assignment_id', identifier)
    epoch = records.get('review_epoch', {}).get(epoch_id, {})
    scope = dict(binding=binding, selection=records.get('review_selection', {}).get(controller_id),
                 epoch=epoch.get('digest'), head=records.get('review_slot_head', {}).get(epoch.get('slot_id')))
    if epoch_id is not None:
        scope['composition_plans'] = [(key, digest(row)) for key, row in
                                      records.get('review_composition_plan', {}).items()
                                      if row.get('epoch_id') == epoch_id]
        scope['tasks'] = [(row['id'], row.get('generation'), row.get('task', {}).get('digest'))
                          for row in records.get('review_task', {}).values() if row.get('epoch_id') == epoch_id]
        scope['execution'] = [records.get('review_task_execution', {}).get(row[0]) for row in scope['tasks']]
        scope['reservations'] = [(row['id'], row.get('generation'), row.get('state'), row.get('released'))
                                 for row in records.get('review_reservation', {}).values() if row.get('epoch_id') == epoch_id]
        scope['accepted'] = [(key, digest(row['receipt'])) for key, row in
                             records.get('review_task_acceptance', {}).items()
                             if row['receipt'].get('staged_observation', {}).get('epoch_id') == epoch_id]
        scope['incomplete'] = [row['digest'] for row in records.get('assessment_incomplete', {}).values()
                               if row.get('epoch_id') == epoch_id]
        children = {row['id'] for row in records.get('review_assignment', {}).values()
                    if row.get('staged_review', {}).get('epoch_id') == epoch_id}
        scope['corrections'] = [digest(row) for row in records.get('review_output_correction', {}).values()
                                if row.get('assignment_id') in children]
        scope['context_events'] = [digest(row) for row in records.get('review_context_event', {}).values()
                                  if row.get('assignment_id') in children]
    return digest(scope)


def evaluate_state(records, artifacts, kind, identifier, compute):
    """Run native checks or return an isolated copy of their exact prior result."""
    evaluation = Evaluation.of(records, artifacts)
    active = evaluation._active_states
    scope = evaluation.find_root()._runtime_scope[0]
    identity = (kind, identifier)
    if identity in active:
        raise ResearchError('review_context_repair_mismatch', 'Reviewer lineage must be acyclic')
    active.add(identity)
    try:
        before = scope['fingerprint'] if scope is not None else _runtime_fingerprint(evaluation.records, evaluation)
        key = (kind, identifier, before)
        if kind in ('assignment', 'adjudication'):
            assignment_id = (evaluation.records.get('review_adjudication', {}).get(identifier, {}).get('assignment_id', identifier)
                             if kind == 'adjudication' else identifier)
            assignment = evaluation.records.get('review_assignment', {}).get(assignment_id, {})
            if (assignment.get('staged_review') or {}).get('epoch_id') is not None:
                # Mutable selected roots invalidate staged states within one
                # view; native states retain their original cache key.
                key += (_fingerprint_selection(evaluation.records, identifier, kind),)
        def calculate():
            result = compute(evaluation.records, evaluation, identifier)
            if scope is None and before is not None and _runtime_fingerprint(evaluation.records, evaluation) != before:
                # Unscoped calls retain their historical behavior: return the
                # native result without retaining it after observed drift.
                changed.append(True)
            return result
        changed = []
        if before is None:
            return copy.deepcopy(calculate())
        result = evaluation.reuse_value(('review_state', key), calculate, protected=True)
        if changed:
            cache_key = ('encoded_value', evaluation._namespace, ('review_state', key))
            removed = evaluation._requests.pop(cache_key, None)
            if removed is not None:
                evaluation._retained_bytes[0] -= removed[1]
                evaluation._request_bytes[0] -= removed[1]
        return result
    finally:
        active.remove(identity)
