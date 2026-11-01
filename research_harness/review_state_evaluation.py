"""Reuse complete review states only for one records view and current runtimes."""

import copy

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
    scope = evaluation.once(('review_state_evaluation',), lambda: {'values': {}, 'active': set()})
    identity = (kind, identifier)
    if identity in scope['active']:
        raise ResearchError('review_context_repair_mismatch', 'Reviewer lineage must be acyclic')
    scope['active'].add(identity)
    try:
        before = _runtime_fingerprint(evaluation.records, evaluation)
        # These mutable selectors can change while an Evaluation holds immutable
        # task bodies. A root or task generation change must invalidate its state.
        dependencies = _fingerprint_selection(evaluation.records, identifier, kind)
        key = (kind, identifier, before, dependencies)
        if before is not None and key in scope['values']:
            return copy.deepcopy(scope['values'][key])
        result = compute(evaluation.records, evaluation, identifier)
        if before is not None and _runtime_fingerprint(evaluation.records, evaluation) == before:
            scope['values'][key] = copy.deepcopy(result)
        return copy.deepcopy(result)
    finally:
        scope['active'].remove(identity)
