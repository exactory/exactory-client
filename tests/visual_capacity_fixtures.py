"""Bounded synthetic image and file backends for protocol tests, independent of real provider rules."""

from unittest.mock import patch

from research_harness import review_route_capacity as capacity, review_capacity_evidence as evidence
from capacity_evidence_fixtures import build_capacity_prior


def install_bounded_visual_backend(case, routes):
    """Declare this stub's image rule and an earlier verified input under its exact runtime key."""
    keys = [capacity.describe_capacity_key(route) for route in routes]
    original = capacity.describe_binary_rules

    def describe_fixture_rules(route):
        result = original(route)
        if route['adapter'] in ('codex_cli_visual_v1', capacity.transport.ADAPTER) and capacity.describe_capacity_key(route) in keys:
            result['image']['token_rule'] = dict(source='documented',
                url='https://provider.example/fixture-image-rule', rule='patch_budget', patch_pixels=32,
                multiplier=1.2, detail_budgets=dict(low=256, high=2500, original=30000, auto=30000),
                maximum_dimension=65535, rounding_margin_tokens=1)
        return result

    priors = tuple(evidence.read_capacity_priors()) + tuple(
        build_capacity_prior(route, 2000000) for route in routes)
    for active in (patch.object(capacity, 'describe_binary_rules', side_effect=describe_fixture_rules),
                   patch.object(evidence, 'CAPACITY_PRIORS', priors)):
        active.start()
        case.addCleanup(active.stop)


def install_bounded_binary_backend(case, routes):
    """Declare a fixed input budget for this synthetic byte-reading provider, including its PDF converter."""
    keys = [capacity.describe_capacity_key(route) for route in routes]
    original = capacity.describe_binary_rules

    def describe_fixture_rules(route):
        result = original(route)
        if route['adapter'] == capacity.transport.ADAPTER and capacity.describe_capacity_key(route) in keys:
            for rule in result.values():
                rule['token_rule'] = dict(source='documented', rule='fixed_budget', tokens_upper=50000,
                    url='https://provider.example/fixture-binary-rule')
        return result

    active = patch.object(capacity, 'describe_binary_rules', side_effect=describe_fixture_rules)
    active.start()
    case.addCleanup(active.stop)
