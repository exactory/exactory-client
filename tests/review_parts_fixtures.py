"""Fresh-input result series and pure planner fixtures."""
import copy
import json
from problem_parts_fixtures import ResultPartsCase
from research_harness.evidence import digest


def make_packet(count=4, characters=24000, body=None):
    evidence = {key: [] for key in ('openness_searches', 'assessment_evidence', 'readings', 'passages', 'computations', 'formal_checks')}
    evidence.update(claim={'statement': 'A bounded claim'}, value={}, assessment={}, openness={}, attempt={}, results=[], citations=[])
    evidence['openness_searches'] = [{'id': 's' + str(i), 'reason': 'x' * characters} for i in range(count)]
    packet = dict(protocol='fixture', role='result', subject={'kind': 'problem'}, problem_evidence=evidence,
                  problem_evidence_digest=digest(evidence), evidence=[], material_objections=[], historical_reviews=[])
    if body is not None:
        import hashlib
        ref = dict(path='artifacts/body', sha256=hashlib.sha256(body.encode()).hexdigest(), size=len(body.encode()), media_type='text/plain')
        evidence['assessment_evidence'] = [ref]
        packet['evidence'] = [dict(artifact=ref, content=body, encoding='utf-8')]
    return packet


def make_output(packet, combinations=None):
    header = packet['review_parts']
    kind, turn = header['kind'], header['turn']
    if kind == 'read':
        return dict(kind=kind, turn=turn, unborne=[dict(target=row['id'], reason='No bearing in these originals.') for row in header['targets']], cards=[dict(unit=unit, summary='A supported record.', bearing=[], statements=[], depends_on='', questions='') for unit in header['delivered_units'] if not next(row for row in header['units'] if row['handle'] == unit).get('carded_under')])
    if kind == 'combine':
        return dict(kind=kind, turn=turn, combinations=copy.deepcopy(combinations or []), rest='All other cards are independent.')
    if kind == 'check':
        return dict(kind=kind, turn=turn, findings=[dict(combination=row['id'], finding='The originals agree.', effect='supports', statements=[]) for row in packet['review_parts_state']['combinations'] if row['id'] in header['check_combinations']])
    return dict(stage='result')


def observe(packet, output=None, output_tokens=50000):
    from research_harness import review_parts as parts
    output = output or make_output(packet)
    delta = parts.validate_output(packet, output, packet['_test_inventory'])
    clean = {k: v for k, v in packet.items() if k != '_test_inventory'}
    header = packet['review_parts']
    receipt = dict(turn=header['turn'], kind=header['kind'], assignment_id='a' + str(header['turn']), attempt_id='t' + str(header['turn']), output_digest=digest(output))
    return dict(packet=clean, inventory=packet['_test_inventory'], output=output, delta=delta, receipt=receipt, output_tokens=output_tokens)


def plan(packet, observations=(), limit=110000, **kwargs):
    from research_harness import review_parts as parts
    inventory = parts.build_inventory(packet, 'problem_result', observations[-1].get('inventory') if observations else None)
    result = parts.plan_turn(packet, inventory, observations, series_id='a1', input_limit=limit,
                             measure=kwargs.pop('measure',lambda value: len(json.dumps(value, sort_keys=True))), output_tokens=128000, **kwargs)
    result['_test_inventory'] = inventory
    return result


class PartsResultCase(ResultPartsCase):
    """Use the real prospective path; the parent only supplies observed bar fixtures."""
    use_parts_v2 = True

    def output(self, assignment):
        return make_output(self.packet(assignment))

    def finish_series(self, first=None):
        assignment = first or self.assign()
        turns = []
        while self.packet(assignment)['review_parts']['kind'] != 'final':
            turns.append(assignment)
            self.assertLess(len(turns), 40)
            self.invoke(assignment)
            assignment = self.assign('turn-' + str(len(turns) + 1), assignment['id'])
        return turns, assignment
