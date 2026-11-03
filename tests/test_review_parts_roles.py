"""Every supported packet has one scientific adapter and a valid source projection."""

import copy
import unittest

from research_harness import bar_sources, review_parts as parts
from research_harness import review_parts_roles as roles


class AdapterSelectionTests(unittest.TestCase):
    def assert_selected(self, role, packet, expected):
        value = dict(role=role, context={})
        applicable = [name for name, adapter in roles.ADAPTERS.items()
                      if adapter['applies'](value, packet)]
        self.assertEqual(applicable, [expected])
        self.assertEqual(roles.find_adapter(value, packet), expected)

    def test_each_ordinary_packet_selects_its_role_adapter(self):
        packets = (
            ('result', dict(subject=dict(kind='problem')), 'problem_result'),
            ('adjudicator', dict(subject=dict(kind='problem')), 'problem_adjudicator'),
            ('native_math', dict(native=dict(scientific_context={})), 'native_math'),
            ('verification', dict(paper=dict(kind='verification')), 'study_verification'),
            ('result', dict(dossier={}, readiness=dict(kind='readiness')), 'study_readiness'),
        )
        for role, packet, expected in packets:
            with self.subTest(adapter=expected):
                self.assert_selected(role, packet, expected)

    def test_each_repair_packet_selects_only_the_repair_adapter(self):
        packets = (
            ('bar', dict(dossier={})),
            ('slate', dict(dossier={})),
            ('result', dict(subject=dict(kind='problem'))),
            ('adjudicator', dict(subject=dict(kind='problem'))),
            ('result', dict(dossier={}, readiness=dict(kind='readiness'))),
        )
        for role, packet in packets:
            with self.subTest(role=role, packet=packet):
                self.assert_selected(role, dict(packet, context_repair={}), 'context_repair')


class SourceRequestProjectionTests(unittest.TestCase):
    def test_every_source_request_role_projects_a_valid_ordinary_request(self):
        request = dict(kind='requests', unit_requests=[],
                       source_requests=[dict(id='page', kind='inventory_page', page=0)])
        for role in bar_sources.ROLES:
            with self.subTest(role=role):
                projected = parts.project_source_output(request, role)
                self.assertEqual(projected['stage'], role)
                self.assertIsNone(projected['support'])
                self.assertEqual(projected['value']['status'], 'unresolved')
                self.assertEqual(bar_sources.requests(projected), request['source_requests'])
                self.assertEqual(request, dict(kind='requests', unit_requests=[],
                    source_requests=[dict(id='page', kind='inventory_page', page=0)]))
                projected['source_requests'][0]['id'] = 'changed'
                self.assertEqual(request['source_requests'][0]['id'], 'page')

    def test_ordinary_source_output_is_preserved(self):
        output = dict(stage='slate', support=None, value=dict(status='unresolved'),
                      source_requests=[dict(id='page', kind='inventory_page', page=0)])
        self.assertEqual(parts.project_source_output(copy.deepcopy(output), 'slate'), output)
