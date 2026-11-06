"""Keep deliberately emulated transports in both planning and reconstruction."""

from unittest.mock import patch

from research_harness import review_request_evaluation, review_transport as transport


def use_emulated_request_builders(case):
    """Use native fitting normally and honor this fixture's transport substitutions."""
    native = transport.request_body
    reconstruct = review_request_evaluation._build_request

    def build(route, prompt, packet, history, order):
        if transport.request_body is native:
            return reconstruct(route, prompt, packet, history, order)
        if order is transport.FIT_ORDER:
            return transport.request_body(route, prompt, packet, history)
        return transport.request_body(route, prompt, packet, history, order)

    patcher = patch.object(review_request_evaluation, '_build_request', new=build)
    patcher.start()
    case.addCleanup(patcher.stop)
