"""Bind pure v7 planning fixtures to the released capacity evidence runtime."""

import copy

from research_harness.review_capacity_evidence import read_capacity_priors
from test_review_route_capacity import build_route as build_unbound_route


def build_route(*args, **kwargs):
    route = build_unbound_route(*args, **kwargs)
    if route['adapter'].startswith('codex_cli_'):
        prior = next(row for row in read_capacity_priors()
                     if row['key']['adapter'] == route['adapter']
                     and row['key']['model'] == route['model'])
        route['runtime'] = copy.deepcopy(prior['key']['runtime'])
    return route
