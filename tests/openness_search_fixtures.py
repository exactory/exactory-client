"""Search delivery fixtures, including the incident's three generations of five formulations."""

import copy

from research_harness.evidence import digest


def build_fifteen_searches():
    """Ten searches share 339 judgments, five share 271, with scope shared within each generation."""
    judgments = [[{'work_id': 'arxiv:2601.%05dv1' % number, 'disposition': 'out_of_scope',
                   'reason': ('The captured source establishes a different finite bound. ' * 14)
                             + 'Judgment %d.' % number}
                  for number in range(count)] for count in (339, 271)]
    bindings = []
    for generation in range(3):
        for formulation, purpose in enumerate(('direct', 'originals', 'theory', 'adjacent', 'recent')):
            question = 'Exact formulation %d of the finite bound problem.' % formulation
            search = {'id': 'search-%d-%d' % (generation, formulation), 'profile': 'research',
                      'purpose': purpose, 'question': question,
                      'scope': 'Scope of generation %d, with its exact dated enumeration.' % generation,
                      'queries': [question], 'responses': [{'source_id': 'response-%d-%d' %
                                                           (generation, formulation), 'query': question}],
                      'dispositions': copy.deepcopy(judgments[generation == 2]),
                      'impact': 'Impact on formulation %d at generation %d.' % (formulation, generation)}
            bindings.append({'formulation': question, 'search_digest': digest(search), 'search': search})
    return bindings
