"""Focused adjudication units retain exact scientific and source requirements."""

import copy
import json
import unittest

from research_harness import review_parts as parts, review_protocol as protocol
from research_harness import review_parts_roles as roles
from research_harness.evidence import digest
from review_parts_fixtures import make_packet


class AdjudicationAdapterTests(unittest.TestCase):
    def test_units_frame_targets_and_series_key(self):
        packet = make_packet(2, 50, body='Exact original evidence.')
        for row in packet['problem_evidence']['openness_searches']:
            row.update(search=dict(id=row['id'], reason=row['reason']), sources=[])
        reference = packet['problem_evidence']['assessment_evidence'][0]
        packet.update(role='adjudicator', objection=dict(id='o', claim='Contested premise', material_version='v1'),
                      assessments=[dict(id='a'), dict(id='b')], bar_calibrations=[dict(id='bar')],
                      current_bar_calibrations=[], correction=None, version_review=dict(id='v'),
                      version_source_mappings=[], supplied_source_mappings=[], assignment_policy='fixed',
                      original_evidence=[reference], version_evidence=[reference],
                      current_source_delivery=dict(protocol='focused-current-sources-v1', readings=[],
                          required_units=[], sources=[dict(request=dict(version_id='source'), evidence=[reference])]))
        packet['problem_evidence']['claim'].update(resolution_criteria='Prove the bound')
        packet['problem_evidence']['value'] = dict(motivation=[dict(text='A useful consequence')], current_state=[])
        self.assertEqual(roles.find_adapter(dict(role='adjudicator'), packet), 'problem_adjudicator')
        inventory = parts.build_inventory(packet, 'problem_adjudicator')
        units = parts.enumerate_units(packet, 'problem_adjudicator')
        frame = parts.build_frame(packet, units)
        for key in ('subject', 'objection', 'assessments', 'bar_calibrations', 'current_bar_calibrations',
                    'correction', 'version_review', 'version_source_mappings', 'supplied_source_mappings', 'assignment_policy'):
            self.assertEqual(frame[key], packet[key])
        for key in ('readings', 'required_units', 'protocol'):
            self.assertEqual(frame['current_source_delivery'][key], packet['current_source_delivery'][key])
        self.assertEqual(frame['evidence'], [])
        paths = {tuple(occurrence['path']) for unit in inventory['units'] for occurrence in unit['occurrences']}
        self.assertTrue({('original_evidence',), ('version_evidence',), ('current_source_delivery', 'sources')} <= paths)
        adapter = roles.get_adapter('problem_adjudicator')
        targets = adapter['targets'](packet)
        self.assertTrue(all(row['id'] and row['label'] for row in targets))
        self.assertTrue({'Contested premise', 'A bounded claim', 'Prove the bound', 'A useful consequence'}
                        <= {row['label'] for row in targets})
        key = adapter['series_key'](packet)
        for name in ('problem_evidence_digest', 'objection', 'current_source_delivery'):
            changed = copy.deepcopy(packet)
            if name == 'objection':
                changed[name]['material_version'] = 'v2'
            elif name == 'current_source_delivery':
                changed[name]['required_units'].append(dict(unit_id='new'))
            else:
                changed[name] = 'changed'
            self.assertNotEqual(adapter['series_key'](changed), key)

    def test_current_prompt_predicate_accepts_only_authentic_current_finals(self):
        for answer in (False, True):
            prompt = protocol.canonical_prompt('adjudicator', orchestration='problem_creation',
                review_parts=dict(adapter='problem_adjudicator', kind='final', answer=answer))
            self.assertTrue(protocol._matches_current_problem_adjudicator_prompt(prompt))
            changed = copy.deepcopy(prompt)
            changed['text'] += ' Forged rule.'
            self.assertFalse(protocol._matches_current_problem_adjudicator_prompt(changed))
        intermediate = protocol.canonical_prompt('adjudicator', orchestration='problem_creation',
            review_parts=dict(adapter='problem_adjudicator', kind='read'))
        self.assertFalse(protocol._matches_current_problem_adjudicator_prompt(intermediate))

    def test_focused_search_unit_owns_its_attached_response_body(self):
        packet = make_packet(1, 50, body='The complete captured search response.')
        reference = packet['problem_evidence']['assessment_evidence'].pop()
        packet['problem_evidence']['openness_searches'][0].update(
            search=dict(id='search', reason='Exact capture'), sources=[dict(id='source', response=reference)])
        packet['objection'] = dict(id='o', material_version='v1')
        units = parts.enumerate_units(packet, 'problem_adjudicator')
        self.assertEqual(parts.build_frame(packet, units)['evidence'], [])
        self.assertEqual(units[0]['bodies'][0]['artifact'], reference)
        self.assertIn(dict(kind='record', record_kind='literature_search', id='search',
                           digest=digest(packet['problem_evidence']['openness_searches'][0]['search'])),
                      units[0]['citations'])

    def test_inventory_rebuilds_after_canonical_json_reorders_object_fields(self):
        packet = make_packet(0, body='First exact body.')
        first = packet['problem_evidence']['assessment_evidence'].pop()
        second = copy.deepcopy(first)
        import hashlib
        text = 'Second exact body.'
        second.update(sha256=hashlib.sha256(text.encode()).hexdigest(), size=len(text), path='artifacts/second')
        packet['evidence'].append(dict(artifact=second, content=text, encoding='utf-8'))
        packet['problem_evidence']['computations'] = [dict(z=first, a=second)]
        inventory = parts.build_inventory(packet, 'problem_adjudicator')
        rebuilt = parts.build_inventory(json.loads(parts._dump(packet)), 'problem_adjudicator')
        self.assertEqual(inventory, rebuilt)

    def test_non_utf8_owned_body_is_never_segmented(self):
        import base64
        import hashlib
        packet = make_packet(0, body='temporary')
        data = b'\xff\x00' * 10000
        reference = packet['problem_evidence']['assessment_evidence'][0]
        reference.update(sha256=hashlib.sha256(data).hexdigest(), size=len(data))
        packet['evidence'] = [dict(artifact=reference, encoding='base64', content=base64.b64encode(data).decode())]
        units = parts.enumerate_units(packet, 'problem_adjudicator')
        selected, _ = parts.select_originals(units, units[0]['characters'] * 2)
        self.assertEqual(selected, [dict(unit=units[0]['handle'])])
        delivered = parts.project_packet(parts.build_frame(packet, units), units, selected,
            dict(kind='read'), dict(cards=[], combinations=[], findings=[]))
        self.assertTrue(parts.has_series_attachment(None, [delivered], reference))
        self.assertEqual(delivered['evidence'][0]['encoding'], 'base64')
        selected, _ = parts.select_originals(units, units[0]['characters'] // 2)
        self.assertFalse(any(row.get('body') == reference['sha256'] for row in selected))

    def test_record_citations_include_the_digests_required_by_adjudications(self):
        from research_harness.review_parts_roles import get_adapter
        citations = get_adapter('problem_adjudicator')['citations']
        for key, kind in (('readings', 'reading'), ('formal_checks', 'problem_formal_check')):
            item = dict(id='record-1', digest='a' * 64)
            if key == 'readings':
                item['cite'] = dict(kind='record', record_kind=kind, id=item['id'])
            source = dict(item=item, occurrences=[dict(path=['problem_evidence', key], index=0)], bodies=[])
            with self.subTest(key=key):
                self.assertEqual(citations(source), [dict(kind='record', record_kind=kind,
                                                        id=item['id'], digest=item['digest'])])
