"""Read-only access to the shipped math controller for v2 review handoffs."""

import copy
import importlib
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
import sys

from .errors import ResearchError

STALE_NATIVE_EVIDENCE = frozenset({'review_packet_mismatch', 'openness_search_stale'})
# The refusals of a native objective that this workspace cannot read and of a predecessor link that the
# admission of a first turn does not take.
OBJECTIVE_UNAVAILABLE_ERR_MSG = (
    'The harness cannot read the math objective of this packet in this workspace, so it checks no command of the '
    'math controller and plans none. The member cause of this refusal states why: the root that the packet names '
    'lies outside this workspace or is a link, the math controller does not open the objective at that root, or '
    'the objective and its common workspace differ from the packet. A copy of a workspace is in this state, because '
    'its packets name the root of the original. Run review-series in the workspace that holds the objective at '
    'the root of the packet.')
APPROVAL_RECORDED_ERR_MSG = (
    'The review that context.native_review_predecessor names approved its result, and the math controller records '
    'that approval as it stands, so no new review takes its place. Run review-series on that assignment and take '
    'the step that it plans.')
NO_NATIVE_RECOVERY_ERR_MSG = (
    'The assignment that context.native_review_predecessor names hands no review on: its packet holds no closure '
    'subject of schema_version 1, and its final did not stop at the output limit, did not return revise or reject, '
    'and does not rest on evidence that changed since. Run review-series on that assignment and take the step that '
    'it plans.')
SUCCESSOR_PACKET_ERR_MSG = (
    'This assignment does not take the handoff of the review that context.native_review_predecessor names. Its '
    'packet keeps the root, the objective, the contract and the kind of the packet of that review. It keeps the '
    'subject and the claim digest too, except after a revise or reject, and after changed evidence it differs from '
    'that packet. After a final that stopped at its output limit, its route record is another one. Run '
    'review-series on the named assignment and take its step as the plan prints it.')
SUCCESSOR_ROUTE_ERR_MSG = (
    'The route record of this assignment is not verified, so it takes no handoff of an earlier review. Name a '
    'verified route record, or probe this one, and send the assignment again.')
SUCCESSOR_EVIDENCE_ERR_MSG = (
    'The packet of this assignment is not the packet that the math controller exports from the present evidence; '
    'details.fields names the members that differ. Run review-series on the assignment that '
    'context.native_review_predecessor names and take its export step again.')


def load_controller():
    """Import the math controller of this plugin tree by its file location and return its service and reviewer."""
    name = 'search_controller'
    source = Path(__file__).resolve().parents[1] / 'skills' / 'math-solver' / 'harness' / name / '__init__.py'
    if name not in sys.modules:
        spec = spec_from_file_location(name, source)
        sys.modules[name] = module_from_spec(spec)
        try:
            spec.loader.exec_module(sys.modules[name])
        except BaseException:
            del sys.modules[name]
            raise
    if Path(sys.modules[name].__file__).resolve() != source:
        raise ImportError('The loaded math controller belongs to a different plugin tree.')
    return importlib.import_module(name + '.service'), importlib.import_module(name + '.reviewer')


def read_objective(artifacts, native):
    """Open the bound objective in this workspace, never in the original of a copy."""
    try:
        root = Path(native['root'])
        root.relative_to(artifacts.root)
        if root.resolve() != root:
            raise ValueError('The packet root no longer resolves to its original location.')
        service, reviewer = load_controller()
        try:
            controller = service.Controller(root)
            state = service.replay(controller.store.read())
            common = reviewer.common_root(root, state, native['subject'])
        except service.SearchError as error:
            raise ValueError(error.code) from error
        if (state['objective_id'] != native['objective_id'] or state['contract'] != native['contract']
                or common != artifacts.root):
            raise ValueError('The native objective or its common workspace differs from the packet.')
        return reviewer, controller, state
    except (ImportError, ValueError, OSError) as error:
        raise ResearchError('native_review_objective_unavailable', OBJECTIVE_UNAVAILABLE_ERR_MSG,
                            {'cause': str(error)}) from error


def has_receipt(state, output):
    """Look for the exact recorded approval in the validated objective."""
    closure = state['control']['closure']
    return ((closure is not None and closure['review'] == output)
            or any(row['review'] == output for row in state['acceptances'].values()))


def check_recording(artifacts, native, output):
    """Return the native command that records output, as the controller checks it now, or None once it is recorded."""
    reviewer, controller, state = read_objective(artifacts, native)
    if has_receipt(state, output):
        return None
    try:
        name, payload = reviewer.check_result_command(controller, state, native, output)
    except reviewer.SearchError as error:
        raise ResearchError(error.code, error.message, error.details) from error
    return dict(command='search ' + name, root=str(controller.root), payload=payload, chooses=[])


def check_export(records, artifacts, native, spec, *, refresh=False):
    """Check a native export of spec without a write. With refresh, bind the material searches that it lacks."""
    reviewer, controller, state = read_objective(artifacts, native)
    # The native evidence walker expects the complete dictionary used by the
    # exporter. Materializing a scoped record view also binds that revision.
    if not isinstance(records, dict) or any(not isinstance(rows, dict) for rows in records.values()):
        records = {kind: dict(rows) for kind, rows in records.items()}
    spec = copy.deepcopy(spec)
    try:
        if 'inputs' not in spec:
            binding = native['scientific_context'].get('supplementary_inputs', {})
            spec['inputs'] = reviewer.handoff_inputs(binding.get('inputs', []))
        search_ids = []
        if refresh:
            spec['openness'], search_ids = reviewer.refresh_export_minimum(spec, state, records)
        exported = reviewer.check_export(controller, spec, state, artifacts, records)
    except reviewer.SearchError as error:
        raise ResearchError(error.code, error.message, error.details) from error
    return spec, exported, search_ids


def matches_successor(original, candidate, mode):
    """Whether candidate is the native packet that the handoff of mode prescribes after original."""
    if any(candidate[key] != original[key] for key in ('root', 'objective_id', 'contract', 'kind')):
        return False
    if mode == 'nonapproval':
        return True
    subject = dict(original['subject'], schema_version=2) if mode == 'legacy' else original['subject']
    if candidate['subject'] != subject or candidate['claim_digest'] != original['claim_digest']:
        return False
    return mode != 'stale' or any(candidate[key] != original[key]
                                 for key in ('scientific_context', 'evidence', 'unmaterialized_digests'))


def find_successor(records, assignment):
    """Return the first turn that the assignment admission took as the native successor of assignment, or None."""
    from .storage import read_filtered_records
    for candidate in read_filtered_records(records, 'review_assignment', role='native_math').values():
        context = candidate['context']
        if (context.get('native_review_predecessor') == assignment['id']
                and not context.get('prior_assignment_id') and not context.get('output_refusal')):
            return candidate
    return None


def find_recovery_mode(records, artifacts, predecessor, original):
    """Return the native recovery that a successor of predecessor takes (legacy, cap, nonapproval or stale), or None
    when predecessor has none. original is the native packet of predecessor. An approval that the controller records
    as it stands needs no successor: that refusal is raised."""
    from .openness_reading import verify_chain
    from .review_failure_class import count_position, is_math_request_final, math_final_cap_exhausted
    from .review_composite import read_final_output

    if 'acceptance_ids' in original['subject'] and original['subject'].get('schema_version') != 2:
        return 'legacy'
    if not is_math_request_final(predecessor):
        return None
    if math_final_cap_exhausted(count_position(records, artifacts, predecessor)):
        return 'cap'
    output, _ = read_final_output(records, artifacts, predecessor)
    if output is not None:
        verify_chain(records, artifacts, predecessor)
    if output is not None and output['decision'] != 'approve':
        return 'nonapproval'
    if output is not None:
        try:
            check_recording(artifacts, original, output)
        except ResearchError as error:
            if error.code in STALE_NATIVE_EVIDENCE:
                return 'stale'
            raise
        raise ResearchError('invalid_review_context', APPROVAL_RECORDED_ERR_MSG)
    if any(row['assignment_id'] == predecessor['id'] and row.get('details', {}).get('code') == 'openness_search_stale'
           for row in records.get('review_output_refusal', {}).values()):
        return 'stale'
    return None


def raise_controller_refusal(error, member):
    """Answer a refusal of the math controller as the plan answers it for the same cause: the stop
    native_review_recording_blocked, with the controller's message and, in member, its code and details. Any other
    error passes unchanged."""
    if not isinstance(error.__cause__, load_controller()[1].SearchError):
        raise error
    raise ResearchError('native_review_recording_blocked', error.message,
                        {member: dict(code=error.code, details=error.details)}) from error


def require_successor(records, artifacts, value, native):
    """Admit a first turn only when it consumes the stated native recovery."""
    from . import review_protocol as protocol
    from .native_review import load_packet

    predecessor = records['review_assignment'][value['context']['native_review_predecessor']]
    original = load_packet(artifacts, predecessor['context']['native_packet'], value['reviewer_id'])
    try:
        mode = find_recovery_mode(records, artifacts, predecessor, original)
    except ResearchError as error:
        raise_controller_refusal(error, 'refusal')
    if mode is None:
        raise ResearchError('invalid_review_context', NO_NATIVE_RECOVERY_ERR_MSG)
    if (not matches_successor(original, native, mode)
            or mode == 'cap' and value['route_id'] == predecessor['route_id']):
        raise ResearchError('invalid_review_context', SUCCESSOR_PACKET_ERR_MSG)
    if protocol.route_state(records, artifacts, value['route_id'])['context_status'] != 'verified_for_route':
        raise ResearchError('invalid_review_context', SUCCESSOR_ROUTE_ERR_MSG)
    spec = {key: native[key] for key in ('kind', 'subject', 'claim_digest', 'reviewer')}
    spec['openness'] = native['scientific_context']['openness_minimum']
    try:
        _, expected, _ = check_export(records, artifacts, native, spec)
    except ResearchError as error:
        raise_controller_refusal(error, 'export_refusal')
    if expected != native:
        changed = sorted(key for key in expected if expected[key] != native.get(key))
        raise ResearchError('invalid_review_context', SUCCESSOR_EVIDENCE_ERR_MSG,
                            {'fields': changed})
