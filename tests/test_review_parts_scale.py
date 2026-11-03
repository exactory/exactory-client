import json
import time
import unittest
from research_harness import review_parts as parts
from review_parts_fixtures import make_packet,make_output,plan,observe


class ScaleTests(unittest.TestCase):
    def test_complete_series_at_record_shape(self):
        packet=make_packet(35,40000)
        packet['problem_evidence']['readings']=[dict(id='r'+str(i)) for i in range(35)]
        packet['problem_evidence']['passages']=[dict(reading_id='r'+str(i),text='p'*28572) for i in range(35)]
        packet['material_objections']=[dict(id='o'+str(i),version_evidence=[dict(id='e'+str(i),reason='v'*35715)]) for i in range(14)]
        packet['frame_text']='f'*450000
        from research_harness import review_protocol as protocol, review_transport as transport
        from research_harness.review_problem_parts import _measure
        route=dict(adapter='codex_cli_v1',endpoint='codex://local',model='fixture',configuration=dict(max_output_tokens=128000,timeout_seconds=5))
        def measure(value):
            prompt=protocol.canonical_prompt('result',orchestration='problem_creation',excerpts=True,review_parts=value['review_parts'])
            return _measure(route,transport.request_body(route,prompt['text'],value,[],order=()))
        observations=[]; sizes=[]; canonical_headers=[]; request_sizes=[]; read_characters=[]; initial_frame=None; worst_turn=0; start=time.monotonic()
        for _ in range(40):
            before=time.monotonic(); turn=plan(packet,observations,limit=1300000,measure=measure)
            elapsed=time.monotonic()-before
            worst_turn=max(worst_turn,elapsed)
            self.assertLess(elapsed,10)
            if initial_frame is None:initial_frame=turn['review_parts']['final_frame_characters']
            clean={k:v for k,v in turn.items() if k!='_test_inventory'}
            self.assertLessEqual(measure(clean),1300000)
            if turn['review_parts']['kind']=='read':read_characters.append(turn['review_parts']['unit_characters'])
            sizes.append(len(json.dumps(turn['review_parts'])))
            canonical_headers.append(len(parts._dump(turn['review_parts'])))
            request_sizes.append(measure(clean))
            if turn['review_parts']['kind']=='final':break
            observations.append(observe(turn))
        else:self.fail('Series did not finish')
        self.assertGreater(len(observations),3)
        inventory=parts.build_inventory(packet,'problem_result')
        self.assertLess(len(json.dumps(inventory)),60000)
        self.assertLess(max(sizes),40000)
        print('parts v2 scale: inventory=%d max_header=%d turns=%d seconds=%.3f' % (len(json.dumps(inventory)),max(sizes),len(observations)+1,time.monotonic()-start))
        print('scale units=%d characters=%d read_characters=%s final_frame_characters=%d' % (len(inventory['units']),inventory['characters'],read_characters,turn['review_parts']['final_frame_characters']))
        print('scale canonical_headers=%s max_request=%d' % (canonical_headers,max(request_sizes)))
        print('scale initial_frame=%d canonical_inventory=%d max_turn_seconds=%.3f' % (initial_frame,len(json.dumps(inventory,ensure_ascii=False,sort_keys=True,separators=(',',':'))),worst_turn))
