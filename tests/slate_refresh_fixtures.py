"""The answer of a slate refresh chain in the problem fixtures. Since 0.93.0 the refresh of an ordinary slate review is a
new chain in its slot: its packet delivers the bars as records, the candidates and the cited texts, and replays no turn
of the bar session. A refreshed answer therefore cites a record that the chain delivers, where a fixture answer cites a
text that only the bar session delivered."""

import copy

from research_harness.evidence import digest


def cite_review(case, review_id):
    """The record reference of a recorded review, as the packet of a slate refresh chain delivers it in bars."""
    review = case.store.snapshot()['records']['value_review'][review_id]
    return {'kind': 'record', 'record_kind': 'value_review', 'id': review_id, 'digest': digest(review)}


def cite_in_refresh_chain(output, undelivered, reference):
    """The output with each citation of `undelivered` in its value, its assurances, its findings and its objections
    replaced by `reference`."""
    output = copy.deepcopy(output)
    value = output['value']

    def replace(items):
        return [reference if item == undelivered else item for item in items]
    value['evidence'] = replace(value['evidence'])
    for row in value['assurances'] + value['objection_findings'] + output['objections']:
        row['evidence'] = replace(row['evidence'])
    return output
