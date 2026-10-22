"""Reuse complete review states only for one records view and current runtimes."""

import copy

from .errors import ResearchError
from .evaluation import Evaluation
from .evidence import digest
from . import review_transport as transport
from .review_exact_text_cost_v1 import profile


def _runtime_fingerprint(records):
    """Observe current adapters and emitter; an unavailable observation disables reuse."""
    try:
        representatives = {}
        for route in records.get('review_route', {}).values():
            representatives.setdefault(route['adapter'], route)
        return digest({'adapters': {adapter: transport.runtime(representatives[adapter])
                                   for adapter in sorted(representatives)},
                       'exact_text_cost': profile()})
    except Exception:
        # This observation only decides whether reuse is possible. The native
        # computation still checks its actual dependencies and keeps its errors.
        return None


def evaluate_state(records, artifacts, kind, identifier, compute):
    """Run native checks or return an isolated copy of their exact prior result."""
    evaluation = Evaluation.of(records, artifacts)
    scope = evaluation.once(('review_state_evaluation',), lambda: {'values': {}, 'active': set()})
    identity = (kind, identifier)
    if identity in scope['active']:
        raise ResearchError('review_context_repair_mismatch', 'Reviewer lineage must be acyclic')
    scope['active'].add(identity)
    try:
        before = _runtime_fingerprint(evaluation.records)
        key = (kind, identifier, before)
        if before is not None and key in scope['values']:
            return copy.deepcopy(scope['values'][key])
        result = compute(evaluation.records, evaluation, identifier)
        if before is not None and _runtime_fingerprint(evaluation.records) == before:
            scope['values'][key] = copy.deepcopy(result)
        return copy.deepcopy(result)
    finally:
        scope['active'].remove(identity)
