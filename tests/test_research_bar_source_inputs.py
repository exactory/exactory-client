"""Malformed observed source requests refuse continuation without store changes."""

import test_research_bar_sources as source_fixtures
from strategy_fixtures import StrategyCase


class BarSourceInputTests(StrategyCase):
    assign = source_fixtures.BarSourceTests.assign
    invoke = source_fixtures.BarSourceTests.invoke

    def setUp(self):
        super().setUp()
        self.setup_dossier()
        self.route()
        self.assign()

    def refuse(self, response):
        self.invoke(response)
        self.error('invalid_review_source_request',
                   lambda: self.assign('continuation', prior_assignment_id='a'))

    def test_request_kind_must_be_a_string_before_dispatch_selection(self):
        response = self.review_response('bar', status='unresolved')
        response['source_requests'] = [{'id': 'invalid-kind', 'kind': [], 'page': 0}]
        self.refuse(response)

    def test_request_value_must_be_an_object_before_status_checks(self):
        response = self.review_response('bar', status='unresolved')
        response['source_requests'] = [{'id': 'next-page', 'kind': 'inventory_page', 'page': 0}]
        response['value'] = None
        self.refuse(response)
