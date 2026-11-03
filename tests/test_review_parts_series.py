import copy
import unittest
from research_harness import review_parts as parts
from review_parts_fixtures import PartsResultCase


class SeriesTests(PartsResultCase):
    def test_authenticated_series_and_tampered_receipt_or_packet(self):
        self.oversized()
        turns,final=self.finish_series()
        self.record_final(final)
        result=parts.verify_series(self.case.read_records(),self.case.artifacts,final['id'])
        self.assertTrue(result['complete'],result)
        self.assertEqual(len(result['packets']),len(turns)+1)
        packet=self.packet(final); packet['review_parts']['receipts'][0]['output_digest']='0'*64
        records=copy.deepcopy(self.case.read_records())
        records['review_assignment'][final['id']]['packet']=self.case.artifacts.put(__import__('json').dumps(packet).encode(),'application/json')
        result=parts.verify_series(records,self.case.artifacts,final['id'])
        self.assertFalse(result['complete'])
        self.assertGreater(result['issues']['count'],0)
        self.assertLessEqual(len(result['issues']['examples']),20)

    def test_skipped_unit_missing_card_and_pair_are_incomplete(self):
        self.oversized(); turns,final=self.finish_series(); self.record_final(final)
        for kind in ('read','combine'):
            records=copy.deepcopy(self.case.read_records())
            target=next(t for t in turns if self.packet(t)['review_parts']['kind']==kind)
            records['review_attempt']={k:v for k,v in records['review_attempt'].items() if v['assignment_id']!=target['id']}
            self.assertFalse(parts.verify_series(records,self.case.artifacts,final['id'])['complete'])

    def test_partial_body_is_carried_in_actual_request_with_exact_segment_bytes(self):
        from research_harness.review_input_projection import packets_from_request
        self.oversized(count=0,limit=65000)
        reference=self.case.artifacts.put(('λ\n'*60000).encode(),'text/plain')
        self.case.expected['assessment_evidence']=[reference]
        first=self.assign();self.invoke(first)
        attempt=self.case.read_records()['review_attempt']['attempt-'+first['id']]
        request=__import__('json').loads(self.case.artifacts.read(attempt['request']))
        actual=packets_from_request(request)[-1]
        entry=next(e for e in actual['evidence'] if 'segment' in e)
        self.assertEqual(entry['encoding'],'utf-8')
        self.assertEqual(entry['content'].encode(),self.case.artifacts.read(entry['artifact']))
        second=self.assign('next-segment',first['id'])
        self.assertEqual(self.packet(second)['review_parts']['kind'],'read')

    def test_forged_item_manifest_is_not_credited(self):
        self.oversized();turns,final=self.finish_series();self.record_final(final)
        records=self.case.read_records()
        packet=self.packet(turns[0]); packet['review_parts']['delivered_items'][0]['digest']='0'*64
        inventory=parts.read_inventory(self.case.artifacts,packet['review_parts']['inventory'])
        with self.assertRaises(__import__('research_harness.errors',fromlist=['ResearchError']).ResearchError):
            parts.validate_output(packet,self.output(turns[0]),inventory)

    def test_citation_reader_authenticates_current_attempt_once(self):
        from unittest.mock import patch
        from research_harness import review_protocol as protocol, research_decisions as decisions
        self.oversized();turns,final=self.finish_series();self.record_final(final)
        records=self.case.read_records()
        state = protocol.assignment_state(records, self.case.artifacts, final['id'])
        attempt = records['review_attempt'][state['attempt_id']]
        with patch.object(protocol,'authenticate_attempt',wraps=protocol.authenticate_attempt) as authenticate:
            delivery=decisions.read_review_citation_delivery(records,self.case.artifacts,final, attempt=attempt)
        self.assertTrue(delivery['packets'])
        current=[call for call in authenticate.call_args_list if call.args[2]['id']==final['id']]
        self.assertEqual(len(current),1)

    def test_programming_errors_are_not_reported_as_incomplete_series(self):
        from unittest.mock import patch
        with patch.object(parts, 'observe_series', side_effect=KeyError('programming defect')):
            with self.assertRaises(KeyError):
                parts.verify_series({}, self.case.artifacts, 'missing')

    def test_delivery_without_attempt_keeps_the_selected_attempt(self):
        from unittest.mock import patch
        from research_harness import review_protocol as protocol, review_delivered_evidence as delivery
        records = dict(review_assignment={'assignment': dict(id='assignment')},
                       review_attempt={'later': dict(id='later', assignment_id='assignment', status='completed')})
        with patch.object(protocol, 'assignment_state', return_value=dict(ready=True, attempt_id='later')), \
                patch.object(protocol, 'authenticate_attempt', return_value=True), \
                patch.object(delivery, '_read_authenticated_delivery', return_value='selected') as reader:
            result = delivery.read_attempt_delivery(records, self.case.artifacts, records['review_assignment']['assignment'])
        self.assertEqual(result, 'selected')
        self.assertEqual(reader.call_args.args[3]['id'], 'later')

    def test_staged_controller_delivery_requires_completed_task_graph(self):
        from unittest.mock import patch
        from research_harness import review_protocol as protocol, review_delivered_evidence as delivery
        from research_harness import direction_reviews
        from research_harness.errors import ResearchError
        records = dict(review_assignment={'controller': dict(id='controller', staged_review=dict(kind='controller'))})
        with patch.object(protocol, 'assignment_state', return_value=dict(ready=True)), \
                patch.object(direction_reviews, 'collect_selected_task_graph',
                             return_value=dict(completion=dict(complete=False))):
            with self.assertRaises(ResearchError) as raised:
                delivery.read_selected_delivery(records, self.case.artifacts, 'controller')
        self.assertEqual(raised.exception.code, 'review_invocation_unverified')
