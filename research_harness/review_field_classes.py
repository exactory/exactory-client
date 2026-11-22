"""Declared field ownership and bounded frames for prospective role protocols.

Sizes use measure_capacity_value, including native serialized quote escaping.
A nested member inherits its nearest row. Only an explicit nested row changes
its ownership; a whole-value overflow owns all of its descendants.
"""

import copy
import json
from pathlib import Path

from .capture_selection import PACKET_KEY as MAIN_CAPTURE_SELECTIONS
from .errors import ResearchError
from .evidence import digest
from .review_prompt_paragraphs import CONDITIONS as PROBLEM_PROMPT_CONDITIONS
from .review_route_capacity import BELOW_FLOOR_MESSAGE, measure_capacity_value, plan_chain_capacity, read_output_profile

# 787 stored descriptor samples: maximum reference rows 4,956, median 68.
# Rounded to the next 1,000 characters; delivery belongs to parts protocol v3.
REFERENCE_ROWS_MAXIMUM_CHARACTERS = 5000
DESCRIPTOR_MAXIMUM_CHARACTERS = 256

# Data only. Protocol forms share vocabulary through explicit inherited rows.
# A zero fixed-value allowance sends the whole record as a unit at every size.
FIELD_CLASSES = {
    'adjudicator': {
        'exactory-focused-review-evidence-v2': {
            'assessments': {'class': 'collection', 'characters': 256, 'unit_rule': 'each_entry'},
            'assignment_policy': {'class': 'fixed', 'characters': 512, 'overflow_unit': True},
            'bar_calibrations': {'class': 'collection', 'characters': 256, 'unit_rule': 'each_entry'},
            'complete_source_delivery': {'class': 'fixed', 'characters': 512, 'overflow_unit': True},
            'context_repair': {'class': 'fixed', 'characters': 2000, 'overflow_unit': True},
            'correction': {'class': 'fixed', 'characters': 512, 'overflow_unit': True},
            'current_bar_calibrations': {'class': 'collection', 'characters': 256, 'unit_rule': 'each_entry'},
            'current_evidence': {'class': 'collection', 'characters': 256, 'unit_rule': 'each_entry'},
            'current_source_delivery': {'class': 'fixed', 'characters': 8000, 'overflow_unit': True},
            'digest': {'class': 'reference', 'characters': 256, 'unit_rule': 'bound_digest'},
            'dossier_id': {'class': 'fixed', 'characters': 512, 'overflow_unit': True},
            'evidence': {'class': 'collection', 'characters': 256, 'unit_rule': 'each_entry'},
            'evidence.*.content': {'characters': 256, 'unit_rule': 'owning_unit', 'class': 'body'},
            'focused_refresh': {'class': 'fixed', 'characters': 1000, 'overflow_unit': True},
            'held_sources': {'class': 'collection', 'characters': 256, 'unit_rule': 'each_entry'},
            'historical_reviews': {'class': 'collection', 'characters': 256, 'unit_rule': 'each_entry'},
            'history_delivery': {'class': 'fixed', 'characters': 512, 'overflow_unit': True},
            'intent': {'class': 'fixed', 'characters': 16000, 'overflow_unit': True},
            'objection': {'class': 'fixed', 'characters': 8000, 'overflow_unit': True},
            'original_evidence': {'class': 'collection', 'characters': 256, 'unit_rule': 'each_entry'},
            'output_refusal': {'class': 'fixed', 'characters': 512, 'overflow_unit': True},
            'pending_source_requests': {'class': 'collection', 'characters': 256, 'unit_rule': 'each_entry'},
            'previously_supplied_evidence': {'class': 'collection', 'characters': 256, 'unit_rule': 'each_entry'},
            'prior_context': {'class': 'collection', 'characters': 256, 'unit_rule': 'each_entry'},
            'problem_areas': {'class': 'collection', 'characters': 256, 'unit_rule': 'each_entry'},
            'problem_evidence': {'class': 'fixed', 'characters': 16000, 'overflow_unit': True},
            'problem_evidence.account_records': {'class': 'collection', 'characters': 256, 'unit_rule': 'each_entry'},
            'problem_evidence.assessment_evidence': {'class': 'collection', 'characters': 256, 'unit_rule': 'each_entry'},
            'problem_evidence.audit_forecast.notices': {'class': 'collection', 'characters': 256, 'unit_rule': 'each_entry'},
            'problem_evidence.citations': {'class': 'collection', 'characters': 256, 'unit_rule': 'each_entry'},
            'problem_evidence.computations': {'class': 'collection', 'characters': 256, 'unit_rule': 'each_entry'},
            'problem_evidence.formal_checks': {'class': 'collection', 'characters': 256, 'unit_rule': 'each_entry'},
            'problem_evidence.minimum_gaps': {'class': 'collection', 'characters': 256, 'unit_rule': 'each_entry'},
            'problem_evidence.openness.minimum': {'class': 'fixed', 'characters': 0, 'overflow_unit': True, 'unit_rule': 'whole_value'},
            'problem_evidence.openness_minimum': {'class': 'fixed', 'characters': 0, 'overflow_unit': True, 'unit_rule': 'whole_value'},
            'problem_evidence.openness_searches': {'class': 'collection', 'characters': 256, 'unit_rule': 'each_entry'},
            'problem_evidence.passages': {'class': 'collection', 'characters': 256, 'unit_rule': 'each_entry'},
            'problem_evidence.readings': {'class': 'collection', 'characters': 256, 'unit_rule': 'each_entry'},
            'problem_evidence.results': {'class': 'collection', 'characters': 256, 'unit_rule': 'each_entry'},
            'problem_evidence_digest': {'class': 'reference', 'characters': 256, 'unit_rule': 'bound_digest'},
            'protocol': {'class': 'fixed', 'characters': 512, 'overflow_unit': True},
            'review_parts': {'class': 'fixed', 'characters': 8000, 'overflow_unit': True},
            'review_parts.inventory': {'class': 'reference', 'characters': 256, 'unit_rule': 'bound_digest'},
            'review_parts.logical_packet': {'class': 'reference', 'characters': 256, 'unit_rule': 'bound_digest'},
            'review_parts.source_inventory': {'class': 'reference', 'characters': 256, 'unit_rule': 'bound_digest'},
            'review_parts.units.*.aliases': {'class': 'collection', 'characters': 256, 'unit_rule': 'each_entry'},
            'review_parts.units.*.bodies.*.aliases': {'characters': 256, 'unit_rule': 'each_entry', 'class': 'collection'},
            'review_parts.units.*.citations': {'class': 'collection', 'characters': 256, 'unit_rule': 'each_entry'},
            'review_parts.units.*.occurrences': {'class': 'collection', 'characters': 256, 'unit_rule': 'each_entry'},
            'review_parts_state': {'class': 'carried', 'characters': 256, 'unit_rule': 'stored_plan'},
            'reviewer_excerpts': {'class': 'carried', 'characters': 256, 'unit_rule': 'stored_plan'},
            'reviewer_id': {'class': 'fixed', 'characters': 512, 'overflow_unit': True},
            'role': {'class': 'fixed', 'characters': 512, 'overflow_unit': True},
            'rubric_definition': {'class': 'fixed', 'characters': 8000, 'overflow_unit': True},
            'rubric_grades': {'class': 'fixed', 'characters': 8000, 'overflow_unit': True},
            'scientific_history': {'class': 'carried', 'characters': 256, 'unit_rule': 'stored_plan'},
            'source_deliveries': {'class': 'collection', 'characters': 256, 'unit_rule': 'each_entry'},
            'staged_review': {'class': 'fixed', 'characters': 16000, 'overflow_unit': True},
            'subject': {'class': 'fixed', 'characters': 1000, 'overflow_unit': True},
            'supplied_source_mappings': {'class': 'collection', 'characters': 256, 'unit_rule': 'each_entry'},
            'version_evidence': {'class': 'collection', 'characters': 256, 'unit_rule': 'each_entry'},
            'version_review': {'class': 'fixed', 'characters': 16000, 'overflow_unit': True},
            'version_source_mappings': {'class': 'collection', 'characters': 256, 'unit_rule': 'each_entry'},
            'visual_source_delivery': {'class': 'fixed', 'characters': 512, 'overflow_unit': True},
        },
        'exactory-independent-review-v1': {
            'assessments': {'class': 'collection', 'characters': 256, 'unit_rule': 'each_entry'},
            'assignment_policy': {'class': 'fixed', 'characters': 512, 'overflow_unit': True},
            'bar_calibrations': {'class': 'collection', 'characters': 256, 'unit_rule': 'each_entry'},
            'complete_source_delivery': {'class': 'fixed', 'characters': 512, 'overflow_unit': True},
            'context_repair': {'class': 'fixed', 'characters': 2000, 'overflow_unit': True},
            'correction': {'class': 'fixed', 'characters': 512, 'overflow_unit': True},
            'current_bar_calibrations': {'class': 'collection', 'characters': 256, 'unit_rule': 'each_entry'},
            'current_evidence': {'class': 'collection', 'characters': 256, 'unit_rule': 'each_entry'},
            'current_source_delivery': {'class': 'fixed', 'characters': 8000, 'overflow_unit': True},
            'digest': {'class': 'reference', 'characters': 256, 'unit_rule': 'bound_digest'},
            'dossier_id': {'class': 'fixed', 'characters': 512, 'overflow_unit': True},
            'evidence': {'class': 'collection', 'characters': 256, 'unit_rule': 'each_entry'},
            'evidence.*.content': {'characters': 256, 'unit_rule': 'owning_unit', 'class': 'body'},
            'focused_refresh': {'class': 'fixed', 'characters': 1000, 'overflow_unit': True},
            'held_sources': {'class': 'collection', 'characters': 256, 'unit_rule': 'each_entry'},
            'historical_reviews': {'class': 'collection', 'characters': 256, 'unit_rule': 'each_entry'},
            'history_delivery': {'class': 'fixed', 'characters': 512, 'overflow_unit': True},
            'intent': {'class': 'fixed', 'characters': 16000, 'overflow_unit': True},
            'objection': {'class': 'fixed', 'characters': 8000, 'overflow_unit': True},
            'original_evidence': {'class': 'collection', 'characters': 256, 'unit_rule': 'each_entry'},
            'output_refusal': {'class': 'fixed', 'characters': 512, 'overflow_unit': True},
            'pending_source_requests': {'class': 'collection', 'characters': 256, 'unit_rule': 'each_entry'},
            'previously_supplied_evidence': {'class': 'collection', 'characters': 256, 'unit_rule': 'each_entry'},
            'prior_context': {'class': 'collection', 'characters': 256, 'unit_rule': 'each_entry'},
            'problem_areas': {'class': 'collection', 'characters': 256, 'unit_rule': 'each_entry'},
            'problem_evidence': {'class': 'fixed', 'characters': 16000, 'overflow_unit': True},
            'problem_evidence.account_records': {'class': 'collection', 'characters': 256, 'unit_rule': 'each_entry'},
            'problem_evidence.assessment_evidence': {'class': 'collection', 'characters': 256, 'unit_rule': 'each_entry'},
            'problem_evidence.audit_forecast.notices': {'class': 'collection', 'characters': 256, 'unit_rule': 'each_entry'},
            'problem_evidence.citations': {'class': 'collection', 'characters': 256, 'unit_rule': 'each_entry'},
            'problem_evidence.computations': {'class': 'collection', 'characters': 256, 'unit_rule': 'each_entry'},
            'problem_evidence.formal_checks': {'class': 'collection', 'characters': 256, 'unit_rule': 'each_entry'},
            'problem_evidence.minimum_gaps': {'class': 'collection', 'characters': 256, 'unit_rule': 'each_entry'},
            'problem_evidence.openness.minimum': {'class': 'fixed', 'characters': 0, 'overflow_unit': True, 'unit_rule': 'whole_value'},
            'problem_evidence.openness_minimum': {'class': 'fixed', 'characters': 0, 'overflow_unit': True, 'unit_rule': 'whole_value'},
            'problem_evidence.openness_searches': {'class': 'collection', 'characters': 256, 'unit_rule': 'each_entry'},
            'problem_evidence.passages': {'class': 'collection', 'characters': 256, 'unit_rule': 'each_entry'},
            'problem_evidence.readings': {'class': 'collection', 'characters': 256, 'unit_rule': 'each_entry'},
            'problem_evidence.results': {'class': 'collection', 'characters': 256, 'unit_rule': 'each_entry'},
            'problem_evidence_digest': {'class': 'reference', 'characters': 256, 'unit_rule': 'bound_digest'},
            'protocol': {'class': 'fixed', 'characters': 512, 'overflow_unit': True},
            'review_parts': {'class': 'fixed', 'characters': 8000, 'overflow_unit': True},
            'review_parts.inventory': {'class': 'reference', 'characters': 256, 'unit_rule': 'bound_digest'},
            'review_parts.logical_packet': {'class': 'reference', 'characters': 256, 'unit_rule': 'bound_digest'},
            'review_parts.source_inventory': {'class': 'reference', 'characters': 256, 'unit_rule': 'bound_digest'},
            'review_parts.units.*.aliases': {'class': 'collection', 'characters': 256, 'unit_rule': 'each_entry'},
            'review_parts.units.*.bodies.*.aliases': {'characters': 256, 'unit_rule': 'each_entry', 'class': 'collection'},
            'review_parts.units.*.citations': {'class': 'collection', 'characters': 256, 'unit_rule': 'each_entry'},
            'review_parts.units.*.occurrences': {'class': 'collection', 'characters': 256, 'unit_rule': 'each_entry'},
            'review_parts_state': {'class': 'carried', 'characters': 256, 'unit_rule': 'stored_plan'},
            'reviewer_excerpts': {'class': 'carried', 'characters': 256, 'unit_rule': 'stored_plan'},
            'reviewer_id': {'class': 'fixed', 'characters': 512, 'overflow_unit': True},
            'role': {'class': 'fixed', 'characters': 512, 'overflow_unit': True},
            'rubric_definition': {'class': 'fixed', 'characters': 8000, 'overflow_unit': True},
            'rubric_grades': {'class': 'fixed', 'characters': 8000, 'overflow_unit': True},
            'scientific_history': {'class': 'carried', 'characters': 256, 'unit_rule': 'stored_plan'},
            'source_deliveries': {'class': 'collection', 'characters': 256, 'unit_rule': 'each_entry'},
            'staged_review': {'class': 'fixed', 'characters': 16000, 'overflow_unit': True},
            'subject': {'class': 'fixed', 'characters': 1000, 'overflow_unit': True},
            'supplied_source_mappings': {'class': 'collection', 'characters': 256, 'unit_rule': 'each_entry'},
            'version_evidence': {'class': 'collection', 'characters': 256, 'unit_rule': 'each_entry'},
            'version_review': {'class': 'fixed', 'characters': 16000, 'overflow_unit': True},
            'version_source_mappings': {'class': 'collection', 'characters': 256, 'unit_rule': 'each_entry'},
            'visual_source_delivery': {'class': 'fixed', 'characters': 512, 'overflow_unit': True},
        },
        'exactory-problem-review-v2': {
            'assessments': {'class': 'collection', 'characters': 256, 'unit_rule': 'each_entry'},
            'assignment_policy': {'class': 'fixed', 'characters': 1000, 'overflow_unit': True},
            'bar_calibrations': {'class': 'collection', 'characters': 256, 'unit_rule': 'each_entry'},
            'context_repair': {'class': 'fixed', 'characters': 2000, 'overflow_unit': True},
            'correction': {'class': 'fixed', 'characters': 1000, 'overflow_unit': True},
            'current_bar_calibrations': {'class': 'collection', 'characters': 256, 'unit_rule': 'each_entry'},
            'current_evidence': {'class': 'collection', 'characters': 256, 'unit_rule': 'each_entry'},
            'current_source_delivery': {'class': 'fixed', 'characters': 8000, 'overflow_unit': True},
            'digest': {'class': 'reference', 'characters': 256, 'unit_rule': 'bound_digest'},
            'dossier_id': {'class': 'fixed', 'characters': 1000, 'overflow_unit': True},
            'evidence': {'class': 'collection', 'characters': 256, 'unit_rule': 'each_entry'},
            'evidence.*.content': {'characters': 256, 'unit_rule': 'owning_unit', 'class': 'body'},
            'focused_refresh': {'class': 'fixed', 'characters': 1000, 'overflow_unit': True},
            'held_sources': {'class': 'collection', 'characters': 256, 'unit_rule': 'each_entry'},
            'historical_reviews': {'class': 'collection', 'characters': 256, 'unit_rule': 'each_entry'},
            'history_delivery': {'class': 'fixed', 'characters': 1000, 'overflow_unit': True},
            'intent': {'class': 'fixed', 'characters': 16000, 'overflow_unit': True},
            'objection': {'class': 'fixed', 'characters': 8000, 'overflow_unit': True},
            'original_evidence': {'class': 'collection', 'characters': 256, 'unit_rule': 'each_entry'},
            'output_refusal': {'class': 'fixed', 'characters': 512, 'overflow_unit': True},
            'pending_source_requests': {'class': 'collection', 'characters': 256, 'unit_rule': 'each_entry'},
            'previously_supplied_evidence': {'class': 'collection', 'characters': 256, 'unit_rule': 'each_entry'},
            'prior_context': {'class': 'collection', 'characters': 256, 'unit_rule': 'each_entry'},
            'problem_areas': {'class': 'collection', 'characters': 256, 'unit_rule': 'each_entry'},
            'problem_evidence': {'class': 'fixed', 'characters': 16000, 'overflow_unit': True},
            'problem_evidence.account_records': {'class': 'collection', 'characters': 256, 'unit_rule': 'each_entry'},
            'problem_evidence.assessment_evidence': {'class': 'collection', 'characters': 256, 'unit_rule': 'each_entry'},
            'problem_evidence.audit_forecast.notices': {'class': 'collection', 'characters': 256, 'unit_rule': 'each_entry'},
            'problem_evidence.citations': {'class': 'collection', 'characters': 256, 'unit_rule': 'each_entry'},
            'problem_evidence.computations': {'class': 'collection', 'characters': 256, 'unit_rule': 'each_entry'},
            'problem_evidence.formal_checks': {'class': 'collection', 'characters': 256, 'unit_rule': 'each_entry'},
            'problem_evidence.minimum_gaps': {'class': 'collection', 'characters': 256, 'unit_rule': 'each_entry'},
            'problem_evidence.openness.minimum': {'class': 'fixed', 'characters': 0, 'overflow_unit': True, 'unit_rule': 'whole_value'},
            'problem_evidence.openness_minimum': {'class': 'fixed', 'characters': 0, 'overflow_unit': True, 'unit_rule': 'whole_value'},
            'problem_evidence.openness_searches': {'class': 'collection', 'characters': 256, 'unit_rule': 'each_entry'},
            'problem_evidence.passages': {'class': 'collection', 'characters': 256, 'unit_rule': 'each_entry'},
            'problem_evidence.readings': {'class': 'collection', 'characters': 256, 'unit_rule': 'each_entry'},
            'problem_evidence.results': {'class': 'collection', 'characters': 256, 'unit_rule': 'each_entry'},
            'problem_evidence_digest': {'class': 'reference', 'characters': 256, 'unit_rule': 'bound_digest'},
            'protocol': {'class': 'fixed', 'characters': 1000, 'overflow_unit': True},
            'review_parts': {'class': 'fixed', 'characters': 8000, 'overflow_unit': True},
            'review_parts.inventory': {'class': 'reference', 'characters': 256, 'unit_rule': 'bound_digest'},
            'review_parts.logical_packet': {'class': 'reference', 'characters': 256, 'unit_rule': 'bound_digest'},
            'review_parts.source_inventory': {'class': 'reference', 'characters': 256, 'unit_rule': 'bound_digest'},
            'review_parts.units.*.aliases': {'class': 'collection', 'characters': 256, 'unit_rule': 'each_entry'},
            'review_parts.units.*.bodies.*.aliases': {'characters': 256, 'unit_rule': 'each_entry', 'class': 'collection'},
            'review_parts.units.*.citations': {'class': 'collection', 'characters': 256, 'unit_rule': 'each_entry'},
            'review_parts.units.*.occurrences': {'class': 'collection', 'characters': 256, 'unit_rule': 'each_entry'},
            'review_parts_state': {'class': 'carried', 'characters': 256, 'unit_rule': 'stored_plan'},
            'reviewer_excerpts': {'class': 'carried', 'characters': 256, 'unit_rule': 'stored_plan'},
            'reviewer_id': {'class': 'fixed', 'characters': 1000, 'overflow_unit': True},
            'role': {'class': 'fixed', 'characters': 1000, 'overflow_unit': True},
            'rubric_definition': {'class': 'fixed', 'characters': 8000, 'overflow_unit': True},
            'rubric_grades': {'class': 'fixed', 'characters': 8000, 'overflow_unit': True},
            'scientific_history': {'class': 'carried', 'characters': 256, 'unit_rule': 'stored_plan'},
            'source_deliveries': {'class': 'collection', 'characters': 256, 'unit_rule': 'each_entry'},
            'staged_review': {'class': 'fixed', 'characters': 16000, 'overflow_unit': True},
            'subject': {'class': 'fixed', 'characters': 1000, 'overflow_unit': True},
            'supplied_source_mappings': {'class': 'collection', 'characters': 256, 'unit_rule': 'each_entry'},
            'version_evidence': {'class': 'collection', 'characters': 256, 'unit_rule': 'each_entry'},
            'version_review': {'class': 'fixed', 'characters': 16000, 'overflow_unit': True},
            'version_source_mappings': {'class': 'collection', 'characters': 256, 'unit_rule': 'each_entry'},
            'visual_source_delivery': {'class': 'fixed', 'characters': 512, 'overflow_unit': True},
        },
        'exactory-review-staged-v2': {
            'complete_source_delivery': {'class': 'fixed', 'characters': 512, 'overflow_unit': True},
            'context_repair': {'class': 'fixed', 'characters': 2000, 'overflow_unit': True},
            'digest': {'class': 'reference', 'characters': 256, 'unit_rule': 'bound_digest'},
            'evidence': {'class': 'collection', 'characters': 256, 'unit_rule': 'each_entry'},
            'evidence.*.content': {'characters': 256, 'unit_rule': 'owning_unit', 'class': 'body'},
            'focused_refresh': {'class': 'fixed', 'characters': 1000, 'overflow_unit': True},
            'held_sources': {'class': 'collection', 'characters': 256, 'unit_rule': 'each_entry'},
            'intent': {'class': 'fixed', 'characters': 16000, 'overflow_unit': True},
            'output_refusal': {'class': 'fixed', 'characters': 512, 'overflow_unit': True},
            'problem_areas': {'class': 'collection', 'characters': 256, 'unit_rule': 'each_entry'},
            'protocol': {'class': 'fixed', 'characters': 512, 'overflow_unit': True},
            'role': {'class': 'fixed', 'characters': 512, 'overflow_unit': True},
            'rubric_definition': {'class': 'fixed', 'characters': 8000, 'overflow_unit': True},
            'rubric_grades': {'class': 'fixed', 'characters': 8000, 'overflow_unit': True},
            'staged_review': {'class': 'fixed', 'characters': 16000, 'overflow_unit': True},
            'subject': {'class': 'fixed', 'characters': 1000, 'overflow_unit': True},
            'visual_source_delivery': {'class': 'fixed', 'characters': 512, 'overflow_unit': True},
        },
    },
    'bar': {
        'exactory-independent-review-sources-v2': {
            'context_repair': {'class': 'fixed', 'characters': 2000, 'overflow_unit': True},
            'continuation': {'class': 'fixed', 'characters': 512, 'overflow_unit': True},
            'digest': {'class': 'reference', 'characters': 256, 'unit_rule': 'bound_digest'},
            'dossier_id': {'class': 'fixed', 'characters': 512, 'overflow_unit': True},
            'evidence': {'class': 'collection', 'characters': 256, 'unit_rule': 'each_entry'},
            'evidence.*.content': {'characters': 256, 'unit_rule': 'owning_unit', 'class': 'body'},
            'forming_records': {'class': 'collection', 'characters': 256, 'unit_rule': 'each_entry'},
            'historical_reviews': {'class': 'collection', 'characters': 256, 'unit_rule': 'each_entry'},
            'history_delivery': {'class': 'fixed', 'characters': 512, 'overflow_unit': True},
            'intent': {'class': 'fixed', 'characters': 16000, 'overflow_unit': True},
            MAIN_CAPTURE_SELECTIONS: {'class': 'fixed', 'characters': 1000, 'overflow_unit': True},
            'output_refusal': {'class': 'fixed', 'characters': 512, 'overflow_unit': True},
            'pending_source_requests': {'class': 'collection', 'characters': 256, 'unit_rule': 'each_entry'},
            'previously_supplied_evidence': {'class': 'collection', 'characters': 256, 'unit_rule': 'each_entry'},
            'prior_context': {'class': 'collection', 'characters': 256, 'unit_rule': 'each_entry'},
            'problem_areas': {'class': 'collection', 'characters': 256, 'unit_rule': 'each_entry'},
            'problem_forming_imports': {'class': 'collection', 'characters': 256, 'unit_rule': 'each_entry'},
            'problem_refresh': {'class': 'fixed', 'characters': 1000, 'overflow_unit': True},
            'protocol': {'class': 'fixed', 'characters': 512, 'overflow_unit': True},
            'reviewer_excerpts': {'class': 'carried', 'characters': 256, 'unit_rule': 'stored_plan'},
            'reviewer_id': {'class': 'fixed', 'characters': 512, 'overflow_unit': True},
            'role': {'class': 'fixed', 'characters': 512, 'overflow_unit': True},
            'route_capacity': {'class': 'fixed', 'characters': 4000, 'overflow_unit': True},
            'scientific_history': {'class': 'carried', 'characters': 256, 'unit_rule': 'stored_plan'},
            'settled_source_requests': {'class': 'collection', 'characters': 256, 'unit_rule': 'each_entry'},
            'source_artifacts': {'class': 'collection', 'characters': 256, 'unit_rule': 'each_entry'},
            'source_deliveries': {'class': 'collection', 'characters': 256, 'unit_rule': 'each_entry'},
            'source_inventory': {'class': 'reference', 'characters': 256, 'unit_rule': 'bound_digest'},
            'source_reply_batch': {'class': 'fixed', 'characters': 8000, 'overflow_unit': True},
            'source_rules': {'class': 'fixed', 'characters': 512, 'overflow_unit': True},
            'session_turn': {'class': 'carried', 'characters': 256, 'unit_rule': 'stored_plan'},
            'session_final': {'class': 'carried', 'characters': 256, 'unit_rule': 'stored_plan'},
            'session_combine': {'class': 'carried', 'characters': 256, 'unit_rule': 'stored_plan'},
        },
        'exactory-independent-review-v1': {
            'area': {'class': 'fixed', 'characters': 512, 'overflow_unit': True},
            'context_repair': {'class': 'fixed', 'characters': 2000, 'overflow_unit': True},
            'continuation': {'class': 'fixed', 'characters': 512, 'overflow_unit': True},
            'digest': {'class': 'reference', 'characters': 256, 'unit_rule': 'bound_digest'},
            'dossier_id': {'class': 'fixed', 'characters': 512, 'overflow_unit': True},
            'evidence': {'class': 'collection', 'characters': 256, 'unit_rule': 'each_entry'},
            'evidence.*.content': {'characters': 256, 'unit_rule': 'owning_unit', 'class': 'body'},
            'field': {'class': 'fixed', 'characters': 512, 'overflow_unit': True},
            'forming_records': {'class': 'collection', 'characters': 256, 'unit_rule': 'each_entry'},
            'historical_reviews': {'class': 'collection', 'characters': 256, 'unit_rule': 'each_entry'},
            'history_delivery': {'class': 'fixed', 'characters': 512, 'overflow_unit': True},
            'intent': {'class': 'fixed', 'characters': 16000, 'overflow_unit': True},
            'output_refusal': {'class': 'fixed', 'characters': 512, 'overflow_unit': True},
            'pending_source_requests': {'class': 'collection', 'characters': 256, 'unit_rule': 'each_entry'},
            'previously_supplied_evidence': {'class': 'collection', 'characters': 256, 'unit_rule': 'each_entry'},
            'prior_context': {'class': 'collection', 'characters': 256, 'unit_rule': 'each_entry'},
            'problem_areas': {'class': 'collection', 'characters': 256, 'unit_rule': 'each_entry'},
            'problem_forming_imports': {'class': 'collection', 'characters': 256, 'unit_rule': 'each_entry'},
            'problem_refresh': {'class': 'fixed', 'characters': 1000, 'overflow_unit': True},
            'protocol': {'class': 'fixed', 'characters': 512, 'overflow_unit': True},
            'review_parts': {'class': 'fixed', 'characters': 8000, 'overflow_unit': True},
            'review_parts.inventory': {'class': 'reference', 'characters': 256, 'unit_rule': 'bound_digest'},
            'review_parts.logical_packet': {'class': 'reference', 'characters': 256, 'unit_rule': 'bound_digest'},
            'review_parts.source_inventory': {'class': 'reference', 'characters': 256, 'unit_rule': 'bound_digest'},
            'review_parts.units.*.aliases': {'class': 'collection', 'characters': 256, 'unit_rule': 'each_entry'},
            'review_parts.units.*.bodies.*.aliases': {'characters': 256, 'unit_rule': 'each_entry', 'class': 'collection'},
            'review_parts.units.*.citations': {'class': 'collection', 'characters': 256, 'unit_rule': 'each_entry'},
            'review_parts.units.*.occurrences': {'class': 'collection', 'characters': 256, 'unit_rule': 'each_entry'},
            'review_parts_state': {'class': 'carried', 'characters': 256, 'unit_rule': 'stored_plan'},
            'reviewer_excerpts': {'class': 'carried', 'characters': 256, 'unit_rule': 'stored_plan'},
            'reviewer_id': {'class': 'fixed', 'characters': 512, 'overflow_unit': True},
            'role': {'class': 'fixed', 'characters': 512, 'overflow_unit': True},
            'route_capacity': {'class': 'fixed', 'characters': 4000, 'overflow_unit': True},
            'scientific_history': {'class': 'carried', 'characters': 256, 'unit_rule': 'stored_plan'},
            'source_artifacts': {'class': 'collection', 'characters': 256, 'unit_rule': 'each_entry'},
            'source_deliveries': {'class': 'collection', 'characters': 256, 'unit_rule': 'each_entry'},
            'source_inventory': {'class': 'reference', 'characters': 256, 'unit_rule': 'bound_digest'},
            'source_reply_batch': {'class': 'fixed', 'characters': 8000, 'overflow_unit': True},
            'source_rules': {'class': 'fixed', 'characters': 512, 'overflow_unit': True},
            'subject': {'class': 'fixed', 'characters': 1000, 'overflow_unit': True},
            'session_turn': {'class': 'carried', 'characters': 256, 'unit_rule': 'stored_plan'},
            'session_final': {'class': 'carried', 'characters': 256, 'unit_rule': 'stored_plan'},
            'session_combine': {'class': 'carried', 'characters': 256, 'unit_rule': 'stored_plan'},
        },
        'exactory-problem-review-v2': {
            'area': {'class': 'fixed', 'characters': 1000, 'overflow_unit': True},
            'context_repair': {'class': 'fixed', 'characters': 2000, 'overflow_unit': True},
            'continuation': {'class': 'fixed', 'characters': 1000, 'overflow_unit': True},
            'digest': {'class': 'reference', 'characters': 256, 'unit_rule': 'bound_digest'},
            'dossier_id': {'class': 'fixed', 'characters': 1000, 'overflow_unit': True},
            'evidence': {'class': 'collection', 'characters': 256, 'unit_rule': 'each_entry'},
            'evidence.*.content': {'characters': 256, 'unit_rule': 'owning_unit', 'class': 'body'},
            'field': {'class': 'fixed', 'characters': 1000, 'overflow_unit': True},
            'forming_records': {'class': 'collection', 'characters': 256, 'unit_rule': 'each_entry'},
            'historical_reviews': {'class': 'collection', 'characters': 256, 'unit_rule': 'each_entry'},
            'history_delivery': {'class': 'fixed', 'characters': 512, 'overflow_unit': True},
            'intent': {'class': 'fixed', 'characters': 16000, 'overflow_unit': True},
            MAIN_CAPTURE_SELECTIONS: {'class': 'fixed', 'characters': 1000, 'overflow_unit': True},
            'output_refusal': {'class': 'fixed', 'characters': 512, 'overflow_unit': True},
            'pending_source_requests': {'class': 'collection', 'characters': 256, 'unit_rule': 'each_entry'},
            'previously_supplied_evidence': {'class': 'collection', 'characters': 256, 'unit_rule': 'each_entry'},
            'prior_context': {'class': 'collection', 'characters': 256, 'unit_rule': 'each_entry'},
            'problem_areas': {'class': 'collection', 'characters': 256, 'unit_rule': 'each_entry'},
            'problem_forming_imports': {'class': 'collection', 'characters': 256, 'unit_rule': 'each_entry'},
            'problem_refresh': {'class': 'fixed', 'characters': 1000, 'overflow_unit': True},
            'protocol': {'class': 'fixed', 'characters': 1000, 'overflow_unit': True},
            'review_parts': {'class': 'fixed', 'characters': 8000, 'overflow_unit': True},
            'review_parts.inventory': {'class': 'reference', 'characters': 256, 'unit_rule': 'bound_digest'},
            'review_parts.logical_packet': {'class': 'reference', 'characters': 256, 'unit_rule': 'bound_digest'},
            'review_parts.source_inventory': {'class': 'reference', 'characters': 256, 'unit_rule': 'bound_digest'},
            'review_parts.units.*.aliases': {'class': 'collection', 'characters': 256, 'unit_rule': 'each_entry'},
            'review_parts.units.*.bodies.*.aliases': {'characters': 256, 'unit_rule': 'each_entry', 'class': 'collection'},
            'review_parts.units.*.citations': {'class': 'collection', 'characters': 256, 'unit_rule': 'each_entry'},
            'review_parts.units.*.occurrences': {'class': 'collection', 'characters': 256, 'unit_rule': 'each_entry'},
            'review_parts_state': {'class': 'carried', 'characters': 256, 'unit_rule': 'stored_plan'},
            'reviewer_excerpts': {'class': 'carried', 'characters': 256, 'unit_rule': 'stored_plan'},
            'reviewer_id': {'class': 'fixed', 'characters': 1000, 'overflow_unit': True},
            'role': {'class': 'fixed', 'characters': 1000, 'overflow_unit': True},
            'route_capacity': {'class': 'fixed', 'characters': 4000, 'overflow_unit': True},
            'scientific_history': {'class': 'carried', 'characters': 256, 'unit_rule': 'stored_plan'},
            'settled_source_requests': {'class': 'collection', 'characters': 256, 'unit_rule': 'each_entry'},
            'source_artifacts': {'class': 'collection', 'characters': 256, 'unit_rule': 'each_entry'},
            'source_deliveries': {'class': 'collection', 'characters': 256, 'unit_rule': 'each_entry'},
            'source_inventory': {'class': 'reference', 'characters': 256, 'unit_rule': 'bound_digest'},
            'source_reply_batch': {'class': 'fixed', 'characters': 8000, 'overflow_unit': True},
            'source_rules': {'class': 'fixed', 'characters': 512, 'overflow_unit': True},
            'subject': {'class': 'fixed', 'characters': 1000, 'overflow_unit': True},
            'session_turn': {'class': 'carried', 'characters': 256, 'unit_rule': 'stored_plan'},
            'session_final': {'class': 'carried', 'characters': 256, 'unit_rule': 'stored_plan'},
            'session_combine': {'class': 'carried', 'characters': 256, 'unit_rule': 'stored_plan'},
        },
    },
    'correction': {
        'exactory-reassessment-disposition-correction-v1': {
            'diagnostic': {'class': 'fixed', 'characters': 8000, 'overflow_unit': True},
            'historical_reviews': {'class': 'collection', 'characters': 256, 'unit_rule': 'each_entry'},
            'original_output': {'class': 'fixed', 'characters': 16000, 'overflow_unit': True},
            'protocol': {'class': 'fixed', 'characters': 512, 'overflow_unit': True},
        },
    },
    'manuscript': {
        '': {
            'bundle_digest': {'class': 'reference', 'characters': 256, 'unit_rule': 'bound_digest'},
            'claim_evidence': {'class': 'collection', 'characters': 256, 'unit_rule': 'each_entry'},
            'digest': {'class': 'reference', 'characters': 256, 'unit_rule': 'bound_digest'},
            'evidence': {'class': 'collection', 'characters': 256, 'unit_rule': 'each_entry'},
            'evidence.*.content': {'characters': 256, 'unit_rule': 'owning_unit', 'class': 'body'},
            'files': {'class': 'collection', 'characters': 256, 'unit_rule': 'each_entry'},
            'kind': {'class': 'fixed', 'characters': 512, 'overflow_unit': True},
            'output_refusal': {'class': 'fixed', 'characters': 512, 'overflow_unit': True},
            'results': {'class': 'collection', 'characters': 256, 'unit_rule': 'each_entry'},
            'scientific_scope': {'class': 'fixed', 'characters': 512, 'overflow_unit': True},
            'source_gaps': {'class': 'fixed', 'characters': 512, 'overflow_unit': True},
            'standards': {'class': 'fixed', 'characters': 8000, 'overflow_unit': True},
        },
        'exactory-independent-review-v1': {
            'digest': {'class': 'reference', 'characters': 256, 'unit_rule': 'bound_digest'},
            'dossier_id': {'class': 'fixed', 'characters': 512, 'overflow_unit': True},
            'evidence': {'class': 'collection', 'characters': 256, 'unit_rule': 'each_entry'},
            'evidence.*.content': {'characters': 256, 'unit_rule': 'owning_unit', 'class': 'body'},
            'output_refusal': {'class': 'fixed', 'characters': 512, 'overflow_unit': True},
            'paper': {'class': 'fixed', 'characters': 16000, 'overflow_unit': True},
            'prediction_context': {'class': 'fixed', 'characters': 512, 'overflow_unit': True},
            'protocol': {'class': 'fixed', 'characters': 512, 'overflow_unit': True},
            'reviewer_id': {'class': 'fixed', 'characters': 512, 'overflow_unit': True},
            'role': {'class': 'fixed', 'characters': 512, 'overflow_unit': True},
        },
        'round': {
            'analysis_grand_challenge': {'class': 'fixed', 'characters': 16000, 'overflow_unit': True},
            'contribution_analysis': {'class': 'fixed', 'characters': 16000, 'overflow_unit': True},
            'decision': {'class': 'fixed', 'characters': 8000, 'overflow_unit': True},
            'development': {'class': 'collection', 'characters': 256, 'unit_rule': 'each_entry'},
            'digest': {'class': 'reference', 'characters': 256, 'unit_rule': 'bound_digest'},
            'grand_challenge': {'class': 'fixed', 'characters': 16000, 'overflow_unit': True},
            'kind': {'class': 'fixed', 'characters': 512, 'overflow_unit': True},
            'manuscript': {'class': 'fixed', 'characters': 16000, 'overflow_unit': True},
            'measurement': {'class': 'fixed', 'characters': 8000, 'overflow_unit': True},
            'predictions': {'class': 'collection', 'characters': 256, 'unit_rule': 'each_entry'},
            'resources': {'class': 'fixed', 'characters': 8000, 'overflow_unit': True},
            'reviews': {'class': 'collection', 'characters': 256, 'unit_rule': 'each_entry'},
            'rounds': {'class': 'collection', 'characters': 256, 'unit_rule': 'each_entry'},
            'searches': {'class': 'collection', 'characters': 256, 'unit_rule': 'each_entry'},
            'synthesis': {'class': 'collection', 'characters': 256, 'unit_rule': 'each_entry'},
        },
    },
    'native_math': {
        'exactory-independent-review-v1': {
            'digest': {'class': 'reference', 'characters': 256, 'unit_rule': 'bound_digest'},
            'dossier_id': {'class': 'fixed', 'characters': 1000, 'overflow_unit': True},
            'evidence': {'class': 'collection', 'characters': 256, 'unit_rule': 'each_entry'},
            'evidence.*.content': {'characters': 256, 'unit_rule': 'owning_unit', 'class': 'body'},
            'native': {'class': 'fixed', 'characters': 16000, 'overflow_unit': True},
            'native.scientific_context.audit_forecast.notices': {'class': 'collection', 'characters': 256, 'unit_rule': 'each_entry'},
            'native.scientific_context.minimum_gaps': {'class': 'collection', 'characters': 256, 'unit_rule': 'each_entry'},
            'native.scientific_context.openness_minimum': {'class': 'fixed', 'characters': 0, 'overflow_unit': True, 'unit_rule': 'whole_value'},
            'output_refusal': {'class': 'fixed', 'characters': 512, 'overflow_unit': True},
            'protocol': {'class': 'fixed', 'characters': 1000, 'overflow_unit': True},
            'reading_record': {'class': 'carried', 'characters': 256, 'unit_rule': 'stored_plan'},
            'reading_turn': {'class': 'carried', 'characters': 256, 'unit_rule': 'stored_plan'},
            'reading_combine': {'class': 'carried', 'characters': 256, 'unit_rule': 'stored_plan'},
            'reading_final': {'class': 'carried', 'characters': 256, 'unit_rule': 'stored_plan'},
            'finding_index': {'class': 'carried', 'characters': 256, 'unit_rule': 'stored_plan'},
            'reviewer_id': {'class': 'fixed', 'characters': 1000, 'overflow_unit': True},
            'role': {'class': 'fixed', 'characters': 1000, 'overflow_unit': True},
        },
        'native': {
            'claim_digest': {'class': 'fixed', 'characters': 1000, 'overflow_unit': True},
            'contract': {'class': 'fixed', 'characters': 5000, 'overflow_unit': True},
            'evidence': {'class': 'collection', 'characters': 256, 'unit_rule': 'each_entry'},
            'evidence.*.content': {'characters': 256, 'unit_rule': 'owning_unit', 'class': 'body'},
            'execution_provenance': {'class': 'collection', 'characters': 256, 'unit_rule': 'each_entry'},
            'kind': {'class': 'fixed', 'characters': 1000, 'overflow_unit': True},
            'objective_id': {'class': 'fixed', 'characters': 1000, 'overflow_unit': True},
            'protocol': {'class': 'fixed', 'characters': 1000, 'overflow_unit': True},
            'reviewer': {'class': 'fixed', 'characters': 1000, 'overflow_unit': True},
            'root': {'class': 'fixed', 'characters': 1000, 'overflow_unit': True},
            'scientific_context': {'class': 'fixed', 'characters': 32000, 'overflow_unit': True},
            'scientific_context.audit_forecast.notices': {'class': 'collection', 'characters': 256, 'unit_rule': 'each_entry'},
            'scientific_context.minimum_gaps': {'class': 'collection', 'characters': 256, 'unit_rule': 'each_entry'},
            'scientific_context.openness_minimum': {'class': 'fixed', 'characters': 0, 'overflow_unit': True, 'unit_rule': 'whole_value'},
            'subject': {'class': 'fixed', 'characters': 80000, 'overflow_unit': True},
            'subject_digest': {'class': 'fixed', 'characters': 1000, 'overflow_unit': True},
            'unmaterialized_digests': {'class': 'collection', 'characters': 256, 'unit_rule': 'each_entry'},
        },
    },
    'readiness': {
        '': {
            'execution_observations': {'class': 'collection', 'characters': 256, 'unit_rule': 'each_entry'},
            'inputs': {'class': 'fixed', 'characters': 32000, 'overflow_unit': True},
            'kind': {'class': 'fixed', 'characters': 512, 'overflow_unit': True},
        },
    },
    'result': {
        'exactory-independent-review-sources-v2': {
            'bar_ids': {'class': 'collection', 'characters': 256, 'unit_rule': 'each_entry'},
            'bars': {'class': 'collection', 'characters': 256, 'unit_rule': 'each_entry'},
            'complete_source_delivery': {'class': 'fixed', 'characters': 15000, 'overflow_unit': True},
            'context_repair': {'class': 'fixed', 'characters': 2000, 'overflow_unit': True},
            'continuation': {'class': 'fixed', 'characters': 512, 'overflow_unit': True},
            MAIN_CAPTURE_SELECTIONS: {'class': 'fixed', 'characters': 1000, 'overflow_unit': True},
            'own_bars': {'class': 'collection', 'characters': 256, 'unit_rule': 'each_entry'},
            'historical_reviews': {'class': 'collection', 'characters': 256, 'unit_rule': 'each_entry'},
            'prior_context': {'class': 'collection', 'characters': 256, 'unit_rule': 'each_entry'},
            'reviewer_excerpts': {'class': 'carried', 'characters': 256, 'unit_rule': 'stored_plan'},
            'settled_source_requests': {'class': 'collection', 'characters': 256, 'unit_rule': 'each_entry'},
            'source_artifacts': {'class': 'collection', 'characters': 256, 'unit_rule': 'each_entry'},
            'assessor': {'class': 'fixed', 'characters': 512, 'overflow_unit': True},
            'commitments': {'class': 'collection', 'characters': 256, 'unit_rule': 'each_entry'},
            'digest': {'class': 'reference', 'characters': 256, 'unit_rule': 'bound_digest'},
            'direction_evidence': {'class': 'fixed', 'characters': 16000, 'overflow_unit': True},
            'dossier': {'class': 'fixed', 'characters': 16000, 'overflow_unit': True},
            'dossier_id': {'class': 'fixed', 'characters': 512, 'overflow_unit': True},
            'evidence': {'class': 'collection', 'characters': 256, 'unit_rule': 'each_entry'},
            'evidence.*.content': {'characters': 256, 'unit_rule': 'owning_unit', 'class': 'body'},
            'forming_records': {'class': 'collection', 'characters': 256, 'unit_rule': 'each_entry'},
            'historical_bars': {'class': 'collection', 'characters': 256, 'unit_rule': 'each_entry'},
            'intent': {'class': 'fixed', 'characters': 16000, 'overflow_unit': True},
            'lead_records': {'class': 'collection', 'characters': 256, 'unit_rule': 'each_entry'},
            'objection_bodies': {'class': 'collection', 'characters': 256, 'unit_rule': 'each_entry'},
            'output_refusal': {'class': 'fixed', 'characters': 512, 'overflow_unit': True},
            'pending_source_requests': {'class': 'collection', 'characters': 256, 'unit_rule': 'each_entry'},
            'problem_areas': {'class': 'collection', 'characters': 256, 'unit_rule': 'each_entry'},
            'problem_evidence_parts': {'class': 'fixed', 'characters': 512, 'overflow_unit': True},
            'problem_forming_imports': {'class': 'collection', 'characters': 256, 'unit_rule': 'each_entry'},
            'previously_supplied_evidence': {'class': 'collection', 'characters': 256, 'unit_rule': 'each_entry'},
            'protocol': {'class': 'fixed', 'characters': 512, 'overflow_unit': True},
            'readiness': {'class': 'fixed', 'characters': 16000, 'overflow_unit': True},
            'reviewer_id': {'class': 'fixed', 'characters': 512, 'overflow_unit': True},
            'role': {'class': 'fixed', 'characters': 512, 'overflow_unit': True},
            'route_capacity': {'class': 'fixed', 'characters': 4000, 'overflow_unit': True},
            'scientific_history': {'class': 'carried', 'characters': 256, 'unit_rule': 'stored_plan'},
            'source_deliveries': {'class': 'collection', 'characters': 256, 'unit_rule': 'each_entry'},
            'source_inventory': {'class': 'reference', 'characters': 256, 'unit_rule': 'bound_digest'},
            'source_reply_batch': {'class': 'fixed', 'characters': 8000, 'overflow_unit': True},
            'source_rules': {'class': 'fixed', 'characters': 512, 'overflow_unit': True},
            'support_contract': {'class': 'fixed', 'characters': 4000, 'overflow_unit': True},
            'visual_context_repair': {'class': 'fixed', 'characters': 512, 'overflow_unit': True},
            'work_item_records': {'class': 'collection', 'characters': 256, 'unit_rule': 'each_entry'},
            'session_turn': {'class': 'carried', 'characters': 256, 'unit_rule': 'stored_plan'},
            'session_final': {'class': 'carried', 'characters': 256, 'unit_rule': 'stored_plan'},
            'session_combine': {'class': 'carried', 'characters': 256, 'unit_rule': 'stored_plan'},
        },
        'exactory-independent-review-v1': {
            'area': {'class': 'fixed', 'characters': 512, 'overflow_unit': True},
            'assessor': {'class': 'fixed', 'characters': 512, 'overflow_unit': True},
            'bar_ids': {'class': 'collection', 'characters': 256, 'unit_rule': 'each_entry'},
            'bars': {'class': 'collection', 'characters': 256, 'unit_rule': 'each_entry'},
            'commitments': {'class': 'collection', 'characters': 256, 'unit_rule': 'each_entry'},
            'complete_source_delivery': {'class': 'fixed', 'characters': 512, 'overflow_unit': True},
            'context_repair': {'class': 'fixed', 'characters': 2000, 'overflow_unit': True},
            'continuation': {'class': 'fixed', 'characters': 512, 'overflow_unit': True},
            'digest': {'class': 'reference', 'characters': 256, 'unit_rule': 'bound_digest'},
            'direction_evidence': {'class': 'fixed', 'characters': 16000, 'overflow_unit': True},
            'dossier': {'class': 'fixed', 'characters': 16000, 'overflow_unit': True},
            'dossier_id': {'class': 'fixed', 'characters': 512, 'overflow_unit': True},
            'evidence': {'class': 'collection', 'characters': 256, 'unit_rule': 'each_entry'},
            'evidence.*.content': {'characters': 256, 'unit_rule': 'owning_unit', 'class': 'body'},
            'field': {'class': 'fixed', 'characters': 512, 'overflow_unit': True},
            'forming_records': {'class': 'collection', 'characters': 256, 'unit_rule': 'each_entry'},
            'historical_bars': {'class': 'collection', 'characters': 256, 'unit_rule': 'each_entry'},
            'historical_reviews': {'class': 'collection', 'characters': 256, 'unit_rule': 'each_entry'},
            'history_delivery': {'class': 'fixed', 'characters': 512, 'overflow_unit': True},
            'intent': {'class': 'fixed', 'characters': 16000, 'overflow_unit': True},
            'lead_records': {'class': 'collection', 'characters': 256, 'unit_rule': 'each_entry'},
            'material_objections': {'class': 'collection', 'characters': 256, 'unit_rule': 'each_entry'},
            'objection_bodies': {'class': 'collection', 'characters': 256, 'unit_rule': 'each_entry'},
            'output_refusal': {'class': 'fixed', 'characters': 512, 'overflow_unit': True},
            'own_bars': {'class': 'collection', 'characters': 256, 'unit_rule': 'each_entry'},
            'pending_source_requests': {'class': 'collection', 'characters': 256, 'unit_rule': 'each_entry'},
            'previously_supplied_evidence': {'class': 'collection', 'characters': 256, 'unit_rule': 'each_entry'},
            'prior_context': {'class': 'collection', 'characters': 256, 'unit_rule': 'each_entry'},
            'problem_areas': {'class': 'collection', 'characters': 256, 'unit_rule': 'each_entry'},
            'problem_evidence': {'class': 'fixed', 'characters': 16000, 'overflow_unit': True},
            'problem_evidence.account_records': {'class': 'collection', 'characters': 256, 'unit_rule': 'each_entry'},
            'problem_evidence.assessment_evidence': {'class': 'collection', 'characters': 256, 'unit_rule': 'each_entry'},
            'problem_evidence.audit_forecast.notices': {'class': 'collection', 'characters': 256, 'unit_rule': 'each_entry'},
            'problem_evidence.citations': {'class': 'collection', 'characters': 256, 'unit_rule': 'each_entry'},
            'problem_evidence.computations': {'class': 'collection', 'characters': 256, 'unit_rule': 'each_entry'},
            'problem_evidence.formal_checks': {'class': 'collection', 'characters': 256, 'unit_rule': 'each_entry'},
            'problem_evidence.minimum_gaps': {'class': 'collection', 'characters': 256, 'unit_rule': 'each_entry'},
            'problem_evidence.openness.minimum': {'class': 'fixed', 'characters': 0, 'overflow_unit': True, 'unit_rule': 'whole_value'},
            'problem_evidence.openness_minimum': {'class': 'fixed', 'characters': 0, 'overflow_unit': True, 'unit_rule': 'whole_value'},
            'problem_evidence.openness_searches': {'class': 'collection', 'characters': 256, 'unit_rule': 'each_entry'},
            'problem_evidence.passages': {'class': 'collection', 'characters': 256, 'unit_rule': 'each_entry'},
            'problem_evidence.readings': {'class': 'collection', 'characters': 256, 'unit_rule': 'each_entry'},
            'problem_evidence.results': {'class': 'collection', 'characters': 256, 'unit_rule': 'each_entry'},
            'problem_evidence_digest': {'class': 'reference', 'characters': 256, 'unit_rule': 'bound_digest'},
            'problem_evidence_parts': {'class': 'fixed', 'characters': 512, 'overflow_unit': True},
            'problem_forming_imports': {'class': 'collection', 'characters': 256, 'unit_rule': 'each_entry'},
            'problem_refresh': {'class': 'fixed', 'characters': 1000, 'overflow_unit': True},
            'problem_review_currency': {'class': 'reference', 'characters': 256, 'unit_rule': 'bound_digest'},
            'protocol': {'class': 'fixed', 'characters': 512, 'overflow_unit': True},
            'readiness': {'class': 'fixed', 'characters': 16000, 'overflow_unit': True},
            'review_parts': {'class': 'fixed', 'characters': 8000, 'overflow_unit': True},
            'review_parts.inventory': {'class': 'reference', 'characters': 256, 'unit_rule': 'bound_digest'},
            'review_parts.logical_packet': {'class': 'reference', 'characters': 256, 'unit_rule': 'bound_digest'},
            'review_parts.source_inventory': {'class': 'reference', 'characters': 256, 'unit_rule': 'bound_digest'},
            'review_parts.units.*.aliases': {'class': 'collection', 'characters': 256, 'unit_rule': 'each_entry'},
            'review_parts.units.*.bodies.*.aliases': {'characters': 256, 'unit_rule': 'each_entry', 'class': 'collection'},
            'review_parts.units.*.citations': {'class': 'collection', 'characters': 256, 'unit_rule': 'each_entry'},
            'review_parts.units.*.occurrences': {'class': 'collection', 'characters': 256, 'unit_rule': 'each_entry'},
            'review_parts_state': {'class': 'carried', 'characters': 256, 'unit_rule': 'stored_plan'},
            'reviewer_excerpts': {'class': 'carried', 'characters': 256, 'unit_rule': 'stored_plan'},
            'reviewer_id': {'class': 'fixed', 'characters': 512, 'overflow_unit': True},
            'role': {'class': 'fixed', 'characters': 512, 'overflow_unit': True},
            'route_capacity': {'class': 'fixed', 'characters': 4000, 'overflow_unit': True},
            'scientific_history': {'class': 'carried', 'characters': 256, 'unit_rule': 'stored_plan'},
            'source_artifacts': {'class': 'collection', 'characters': 256, 'unit_rule': 'each_entry'},
            'source_deliveries': {'class': 'collection', 'characters': 256, 'unit_rule': 'each_entry'},
            'source_inventory': {'class': 'reference', 'characters': 256, 'unit_rule': 'bound_digest'},
            'source_reply_batch': {'class': 'fixed', 'characters': 8000, 'overflow_unit': True},
            'source_rules': {'class': 'fixed', 'characters': 512, 'overflow_unit': True},
            'staged_review': {'class': 'fixed', 'characters': 16000, 'overflow_unit': True},
            'subject': {'class': 'fixed', 'characters': 1000, 'overflow_unit': True},
            'support_contract': {'class': 'fixed', 'characters': 4000, 'overflow_unit': True},
            'visual_context_repair': {'class': 'fixed', 'characters': 512, 'overflow_unit': True},
            'visual_route_transition': {'class': 'fixed', 'characters': 512, 'overflow_unit': True},
            'visual_source_delivery': {'class': 'fixed', 'characters': 512, 'overflow_unit': True},
            'work_item_records': {'class': 'collection', 'characters': 256, 'unit_rule': 'each_entry'},
            'session_turn': {'class': 'carried', 'characters': 256, 'unit_rule': 'stored_plan'},
            'session_final': {'class': 'carried', 'characters': 256, 'unit_rule': 'stored_plan'},
            'session_combine': {'class': 'carried', 'characters': 256, 'unit_rule': 'stored_plan'},
        },
        'exactory-problem-review-v2': {
            'area': {'class': 'fixed', 'characters': 1000, 'overflow_unit': True},
            'bar_ids': {'class': 'collection', 'characters': 256, 'unit_rule': 'each_entry'},
            'bars': {'class': 'collection', 'characters': 256, 'unit_rule': 'each_entry'},
            'commitments': {'class': 'collection', 'characters': 256, 'unit_rule': 'each_entry'},
            'complete_source_delivery': {'class': 'fixed', 'characters': 15000, 'overflow_unit': True},
            'context_repair': {'class': 'fixed', 'characters': 2000, 'overflow_unit': True},
            'continuation': {'class': 'fixed', 'characters': 1000, 'overflow_unit': True},
            'digest': {'class': 'reference', 'characters': 256, 'unit_rule': 'bound_digest'},
            'dossier_id': {'class': 'fixed', 'characters': 1000, 'overflow_unit': True},
            'evidence': {'class': 'collection', 'characters': 256, 'unit_rule': 'each_entry'},
            'evidence.*.content': {'characters': 256, 'unit_rule': 'owning_unit', 'class': 'body'},
            'field': {'class': 'fixed', 'characters': 1000, 'overflow_unit': True},
            'forming_records': {'class': 'collection', 'characters': 256, 'unit_rule': 'each_entry'},
            'historical_bars': {'class': 'collection', 'characters': 256, 'unit_rule': 'each_entry'},
            'historical_reviews': {'class': 'collection', 'characters': 256, 'unit_rule': 'each_entry'},
            'history_delivery': {'class': 'fixed', 'characters': 1000, 'overflow_unit': True},
            'intent': {'class': 'fixed', 'characters': 16000, 'overflow_unit': True},
            'lead_records': {'class': 'collection', 'characters': 256, 'unit_rule': 'each_entry'},
            MAIN_CAPTURE_SELECTIONS: {'class': 'fixed', 'characters': 1000, 'overflow_unit': True},
            'material_objections': {'class': 'collection', 'characters': 256, 'unit_rule': 'each_entry'},
            'objection_bodies': {'class': 'collection', 'characters': 256, 'unit_rule': 'each_entry'},
            'output_refusal': {'class': 'fixed', 'characters': 512, 'overflow_unit': True},
            'own_bars': {'class': 'collection', 'characters': 256, 'unit_rule': 'each_entry'},
            'pending_source_requests': {'class': 'collection', 'characters': 256, 'unit_rule': 'each_entry'},
            'previously_supplied_evidence': {'class': 'collection', 'characters': 256, 'unit_rule': 'each_entry'},
            'prior_context': {'class': 'collection', 'characters': 256, 'unit_rule': 'each_entry'},
            'problem_areas': {'class': 'collection', 'characters': 256, 'unit_rule': 'each_entry'},
            'problem_evidence': {'class': 'fixed', 'characters': 16000, 'overflow_unit': True},
            PROBLEM_PROMPT_CONDITIONS: {'class': 'fixed', 'characters': 1000, 'overflow_unit': True},
            'problem_evidence.account_records': {'class': 'collection', 'characters': 256, 'unit_rule': 'each_entry'},
            'problem_evidence.assessment_evidence': {'class': 'collection', 'characters': 256, 'unit_rule': 'each_entry'},
            'problem_evidence.audit_forecast.notices': {'class': 'collection', 'characters': 256, 'unit_rule': 'each_entry'},
            'problem_evidence.citations': {'class': 'collection', 'characters': 256, 'unit_rule': 'each_entry'},
            'problem_evidence.computations': {'class': 'collection', 'characters': 256, 'unit_rule': 'each_entry'},
            'problem_evidence.formal_checks': {'class': 'collection', 'characters': 256, 'unit_rule': 'each_entry'},
            'problem_evidence.minimum_gaps': {'class': 'collection', 'characters': 256, 'unit_rule': 'each_entry'},
            'problem_evidence.openness.minimum': {'class': 'fixed', 'characters': 0, 'overflow_unit': True, 'unit_rule': 'whole_value'},
            'problem_evidence.openness_minimum': {'class': 'fixed', 'characters': 0, 'overflow_unit': True, 'unit_rule': 'whole_value'},
            'problem_evidence.openness_searches': {'class': 'collection', 'characters': 256, 'unit_rule': 'each_entry'},
            'problem_evidence.passages': {'class': 'collection', 'characters': 256, 'unit_rule': 'each_entry'},
            'problem_evidence.readings': {'class': 'collection', 'characters': 256, 'unit_rule': 'each_entry'},
            'problem_evidence.results': {'class': 'collection', 'characters': 256, 'unit_rule': 'each_entry'},
            'problem_evidence_digest': {'class': 'reference', 'characters': 256, 'unit_rule': 'bound_digest'},
            'problem_evidence_parts': {'class': 'fixed', 'characters': 7000, 'overflow_unit': True},
            'problem_forming_imports': {'class': 'collection', 'characters': 256, 'unit_rule': 'each_entry'},
            'problem_objection_carry': {'class': 'fixed', 'characters': 512, 'overflow_unit': True},
            'problem_refresh': {'class': 'fixed', 'characters': 1000, 'overflow_unit': True},
            'problem_review_currency': {'class': 'reference', 'characters': 256, 'unit_rule': 'bound_digest'},
            'protocol': {'class': 'fixed', 'characters': 1000, 'overflow_unit': True},
            'review_parts': {'class': 'fixed', 'characters': 8000, 'overflow_unit': True},
            'review_parts.inventory': {'class': 'reference', 'characters': 256, 'unit_rule': 'bound_digest'},
            'review_parts.logical_packet': {'class': 'reference', 'characters': 256, 'unit_rule': 'bound_digest'},
            'review_parts.source_inventory': {'class': 'reference', 'characters': 256, 'unit_rule': 'bound_digest'},
            'review_parts.units.*.aliases': {'class': 'collection', 'characters': 256, 'unit_rule': 'each_entry'},
            'review_parts.units.*.bodies.*.aliases': {'characters': 256, 'unit_rule': 'each_entry', 'class': 'collection'},
            'review_parts.units.*.citations': {'class': 'collection', 'characters': 256, 'unit_rule': 'each_entry'},
            'review_parts.units.*.occurrences': {'class': 'collection', 'characters': 256, 'unit_rule': 'each_entry'},
            'review_parts_state': {'class': 'carried', 'characters': 256, 'unit_rule': 'stored_plan'},
            'reviewer_excerpts': {'class': 'carried', 'characters': 256, 'unit_rule': 'stored_plan'},
            'reviewer_id': {'class': 'fixed', 'characters': 1000, 'overflow_unit': True},
            'role': {'class': 'fixed', 'characters': 1000, 'overflow_unit': True},
            'route_capacity': {'class': 'fixed', 'characters': 4000, 'overflow_unit': True},
            'scientific_history': {'class': 'carried', 'characters': 256, 'unit_rule': 'stored_plan'},
            'settled_source_requests': {'class': 'collection', 'characters': 256, 'unit_rule': 'each_entry'},
            'source_artifacts': {'class': 'collection', 'characters': 256, 'unit_rule': 'each_entry'},
            'source_deliveries': {'class': 'collection', 'characters': 256, 'unit_rule': 'each_entry'},
            'source_inventory': {'class': 'reference', 'characters': 256, 'unit_rule': 'bound_digest'},
            'source_reply_batch': {'class': 'fixed', 'characters': 8000, 'overflow_unit': True},
            'source_rules': {'class': 'fixed', 'characters': 512, 'overflow_unit': True},
            'staged_review': {'class': 'fixed', 'characters': 16000, 'overflow_unit': True},
            'subject': {'class': 'fixed', 'characters': 1000, 'overflow_unit': True},
            'visual_context_repair': {'class': 'fixed', 'characters': 2000, 'overflow_unit': True},
            'visual_route_transition': {'class': 'fixed', 'characters': 1000, 'overflow_unit': True},
            'visual_source_delivery': {'class': 'fixed', 'characters': 13000, 'overflow_unit': True},
            'work_item_records': {'class': 'collection', 'characters': 256, 'unit_rule': 'each_entry'},
            'session_turn': {'class': 'carried', 'characters': 256, 'unit_rule': 'stored_plan'},
            'session_final': {'class': 'carried', 'characters': 256, 'unit_rule': 'stored_plan'},
            'session_combine': {'class': 'carried', 'characters': 256, 'unit_rule': 'stored_plan'},
        },
        'exactory-review-staged-v2': {
            'commitments': {'class': 'collection', 'characters': 256, 'unit_rule': 'each_entry'},
            'context_repair': {'class': 'fixed', 'characters': 2000, 'overflow_unit': True},
            'digest': {'class': 'reference', 'characters': 256, 'unit_rule': 'bound_digest'},
            'evidence': {'class': 'collection', 'characters': 256, 'unit_rule': 'each_entry'},
            'evidence.*.content': {'characters': 256, 'unit_rule': 'owning_unit', 'class': 'body'},
            'forming_records': {'class': 'collection', 'characters': 256, 'unit_rule': 'each_entry'},
            'historical_bars': {'class': 'collection', 'characters': 256, 'unit_rule': 'each_entry'},
            'intent': {'class': 'fixed', 'characters': 16000, 'overflow_unit': True},
            'lead_records': {'class': 'collection', 'characters': 256, 'unit_rule': 'each_entry'},
            'objection_bodies': {'class': 'collection', 'characters': 256, 'unit_rule': 'each_entry'},
            'output_refusal': {'class': 'fixed', 'characters': 512, 'overflow_unit': True},
            'problem_areas': {'class': 'collection', 'characters': 256, 'unit_rule': 'each_entry'},
            'problem_evidence_parts': {'class': 'fixed', 'characters': 512, 'overflow_unit': True},
            'problem_forming_imports': {'class': 'collection', 'characters': 256, 'unit_rule': 'each_entry'},
            'protocol': {'class': 'fixed', 'characters': 512, 'overflow_unit': True},
            'role': {'class': 'fixed', 'characters': 512, 'overflow_unit': True},
            'route_capacity': {'class': 'fixed', 'characters': 4000, 'overflow_unit': True},
            'source_reply_batch': {'class': 'fixed', 'characters': 8000, 'overflow_unit': True},
            'source_rules': {'class': 'fixed', 'characters': 512, 'overflow_unit': True},
            'staged_review': {'class': 'fixed', 'characters': 16000, 'overflow_unit': True},
            'subject': {'class': 'fixed', 'characters': 1000, 'overflow_unit': True},
            'visual_context_repair': {'class': 'fixed', 'characters': 512, 'overflow_unit': True},
            'work_item_records': {'class': 'collection', 'characters': 256, 'unit_rule': 'each_entry'},
            'session_turn': {'class': 'carried', 'characters': 256, 'unit_rule': 'stored_plan'},
            'session_final': {'class': 'carried', 'characters': 256, 'unit_rule': 'stored_plan'},
            'session_combine': {'class': 'carried', 'characters': 256, 'unit_rule': 'stored_plan'},
        },
        'readiness': {
            'execution_observations': {'class': 'collection', 'characters': 256, 'unit_rule': 'each_entry'},
            'inputs': {'class': 'fixed', 'characters': 32000, 'overflow_unit': True},
            'kind': {'class': 'fixed', 'characters': 512, 'overflow_unit': True},
            'session_turn': {'class': 'carried', 'characters': 256, 'unit_rule': 'stored_plan'},
            'session_final': {'class': 'carried', 'characters': 256, 'unit_rule': 'stored_plan'},
            'session_combine': {'class': 'carried', 'characters': 256, 'unit_rule': 'stored_plan'},
        },
    },
    'round': {
        '': {
            'analysis_grand_challenge': {'class': 'fixed', 'characters': 16000, 'overflow_unit': True},
            'contribution_analysis': {'class': 'fixed', 'characters': 16000, 'overflow_unit': True},
            'decision': {'class': 'fixed', 'characters': 8000, 'overflow_unit': True},
            'development': {'class': 'collection', 'characters': 256, 'unit_rule': 'each_entry'},
            'digest': {'class': 'reference', 'characters': 256, 'unit_rule': 'bound_digest'},
            'grand_challenge': {'class': 'fixed', 'characters': 16000, 'overflow_unit': True},
            'kind': {'class': 'fixed', 'characters': 512, 'overflow_unit': True},
            'manuscript': {'class': 'fixed', 'characters': 16000, 'overflow_unit': True},
            'measurement': {'class': 'fixed', 'characters': 8000, 'overflow_unit': True},
            'predictions': {'class': 'collection', 'characters': 256, 'unit_rule': 'each_entry'},
            'resources': {'class': 'fixed', 'characters': 8000, 'overflow_unit': True},
            'reviews': {'class': 'collection', 'characters': 256, 'unit_rule': 'each_entry'},
            'rounds': {'class': 'collection', 'characters': 256, 'unit_rule': 'each_entry'},
            'searches': {'class': 'collection', 'characters': 256, 'unit_rule': 'each_entry'},
            'synthesis': {'class': 'collection', 'characters': 256, 'unit_rule': 'each_entry'},
        },
    },
    'slate': {
        'exactory-independent-review-sources-v2': {
            'area': {'class': 'fixed', 'characters': 512, 'overflow_unit': True},
            'bar_ids': {'class': 'collection', 'characters': 256, 'unit_rule': 'each_entry'},
            'bars': {'class': 'collection', 'characters': 256, 'unit_rule': 'each_entry'},
            'candidate_set_digest': {'class': 'reference', 'characters': 256, 'unit_rule': 'bound_digest'},
            'candidates': {'class': 'collection', 'characters': 256, 'unit_rule': 'each_entry'},
            'context_repair': {'class': 'fixed', 'characters': 2000, 'overflow_unit': True},
            'continuation': {'class': 'fixed', 'characters': 512, 'overflow_unit': True},
            'digest': {'class': 'reference', 'characters': 256, 'unit_rule': 'bound_digest'},
            'direction_evidence': {'class': 'fixed', 'characters': 16000, 'overflow_unit': True},
            'dossier': {'class': 'fixed', 'characters': 16000, 'overflow_unit': True},
            'dossier_id': {'class': 'fixed', 'characters': 512, 'overflow_unit': True},
            'evidence': {'class': 'collection', 'characters': 256, 'unit_rule': 'each_entry'},
            'evidence.*.content': {'characters': 256, 'unit_rule': 'owning_unit', 'class': 'body'},
            'field': {'class': 'fixed', 'characters': 512, 'overflow_unit': True},
            'forming_records': {'class': 'collection', 'characters': 256, 'unit_rule': 'each_entry'},
            'historical_bars': {'class': 'collection', 'characters': 256, 'unit_rule': 'each_entry'},
            'historical_reviews': {'class': 'collection', 'characters': 256, 'unit_rule': 'each_entry'},
            'history_delivery': {'class': 'fixed', 'characters': 512, 'overflow_unit': True},
            'intent': {'class': 'fixed', 'characters': 16000, 'overflow_unit': True},
            'lead_records': {'class': 'collection', 'characters': 256, 'unit_rule': 'each_entry'},
            MAIN_CAPTURE_SELECTIONS: {'class': 'fixed', 'characters': 1000, 'overflow_unit': True},
            'output_refusal': {'class': 'fixed', 'characters': 512, 'overflow_unit': True},
            'own_bars': {'class': 'collection', 'characters': 256, 'unit_rule': 'each_entry'},
            'pending_source_requests': {'class': 'collection', 'characters': 256, 'unit_rule': 'each_entry'},
            'previously_supplied_evidence': {'class': 'collection', 'characters': 256, 'unit_rule': 'each_entry'},
            'problem_areas': {'class': 'collection', 'characters': 256, 'unit_rule': 'each_entry'},
            'problem_forming_imports': {'class': 'collection', 'characters': 256, 'unit_rule': 'each_entry'},
            'problem_refresh': {'class': 'fixed', 'characters': 1000, 'overflow_unit': True},
            'prior_context': {'class': 'collection', 'characters': 256, 'unit_rule': 'each_entry'},
            'protocol': {'class': 'fixed', 'characters': 512, 'overflow_unit': True},
            'reviewer_excerpts': {'class': 'carried', 'characters': 256, 'unit_rule': 'stored_plan'},
            'reviewer_id': {'class': 'fixed', 'characters': 512, 'overflow_unit': True},
            'role': {'class': 'fixed', 'characters': 512, 'overflow_unit': True},
            'route_capacity': {'class': 'fixed', 'characters': 4000, 'overflow_unit': True},
            'scientific_history': {'class': 'carried', 'characters': 256, 'unit_rule': 'stored_plan'},
            'settled_source_requests': {'class': 'collection', 'characters': 256, 'unit_rule': 'each_entry'},
            'source_artifacts': {'class': 'collection', 'characters': 256, 'unit_rule': 'each_entry'},
            'source_deliveries': {'class': 'collection', 'characters': 256, 'unit_rule': 'each_entry'},
            'source_inventory': {'class': 'reference', 'characters': 256, 'unit_rule': 'bound_digest'},
            'source_reply_batch': {'class': 'fixed', 'characters': 8000, 'overflow_unit': True},
            'source_rules': {'class': 'fixed', 'characters': 512, 'overflow_unit': True},
            'subject': {'class': 'fixed', 'characters': 1000, 'overflow_unit': True},
            'work_item_records': {'class': 'collection', 'characters': 256, 'unit_rule': 'each_entry'},
            'session_turn': {'class': 'carried', 'characters': 256, 'unit_rule': 'stored_plan'},
            'session_final': {'class': 'carried', 'characters': 256, 'unit_rule': 'stored_plan'},
            'session_combine': {'class': 'carried', 'characters': 256, 'unit_rule': 'stored_plan'},
        },
        'exactory-independent-review-v1': {
            'area': {'class': 'fixed', 'characters': 512, 'overflow_unit': True},
            'bar_ids': {'class': 'collection', 'characters': 256, 'unit_rule': 'each_entry'},
            'bars': {'class': 'collection', 'characters': 256, 'unit_rule': 'each_entry'},
            'candidate_set_digest': {'class': 'reference', 'characters': 256, 'unit_rule': 'bound_digest'},
            'candidates': {'class': 'collection', 'characters': 256, 'unit_rule': 'each_entry'},
            'context_repair': {'class': 'fixed', 'characters': 2000, 'overflow_unit': True},
            'continuation': {'class': 'fixed', 'characters': 512, 'overflow_unit': True},
            'digest': {'class': 'reference', 'characters': 256, 'unit_rule': 'bound_digest'},
            'direction_evidence': {'class': 'fixed', 'characters': 16000, 'overflow_unit': True},
            'dossier': {'class': 'fixed', 'characters': 16000, 'overflow_unit': True},
            'dossier_id': {'class': 'fixed', 'characters': 512, 'overflow_unit': True},
            'evidence': {'class': 'collection', 'characters': 256, 'unit_rule': 'each_entry'},
            'evidence.*.content': {'characters': 256, 'unit_rule': 'owning_unit', 'class': 'body'},
            'field': {'class': 'fixed', 'characters': 512, 'overflow_unit': True},
            'forming_records': {'class': 'collection', 'characters': 256, 'unit_rule': 'each_entry'},
            'historical_bars': {'class': 'collection', 'characters': 256, 'unit_rule': 'each_entry'},
            'historical_reviews': {'class': 'collection', 'characters': 256, 'unit_rule': 'each_entry'},
            'history_delivery': {'class': 'fixed', 'characters': 512, 'overflow_unit': True},
            'intent': {'class': 'fixed', 'characters': 16000, 'overflow_unit': True},
            'lead_records': {'class': 'collection', 'characters': 256, 'unit_rule': 'each_entry'},
            'material_objections': {'class': 'collection', 'characters': 256, 'unit_rule': 'each_entry'},
            'output_refusal': {'class': 'fixed', 'characters': 512, 'overflow_unit': True},
            'own_bars': {'class': 'collection', 'characters': 256, 'unit_rule': 'each_entry'},
            'pending_source_requests': {'class': 'collection', 'characters': 256, 'unit_rule': 'each_entry'},
            'previously_supplied_evidence': {'class': 'collection', 'characters': 256, 'unit_rule': 'each_entry'},
            'problem_areas': {'class': 'collection', 'characters': 256, 'unit_rule': 'each_entry'},
            'problem_forming_imports': {'class': 'collection', 'characters': 256, 'unit_rule': 'each_entry'},
            'problem_refresh': {'class': 'fixed', 'characters': 1000, 'overflow_unit': True},
            'prior_context': {'class': 'collection', 'characters': 256, 'unit_rule': 'each_entry'},
            'protocol': {'class': 'fixed', 'characters': 512, 'overflow_unit': True},
            'reviewer_excerpts': {'class': 'carried', 'characters': 256, 'unit_rule': 'stored_plan'},
            'reviewer_id': {'class': 'fixed', 'characters': 512, 'overflow_unit': True},
            'role': {'class': 'fixed', 'characters': 512, 'overflow_unit': True},
            'route_capacity': {'class': 'fixed', 'characters': 4000, 'overflow_unit': True},
            'scientific_history': {'class': 'carried', 'characters': 256, 'unit_rule': 'stored_plan'},
            'source_artifacts': {'class': 'collection', 'characters': 256, 'unit_rule': 'each_entry'},
            'source_deliveries': {'class': 'collection', 'characters': 256, 'unit_rule': 'each_entry'},
            'source_inventory': {'class': 'reference', 'characters': 256, 'unit_rule': 'bound_digest'},
            'source_reply_batch': {'class': 'fixed', 'characters': 8000, 'overflow_unit': True},
            'source_rules': {'class': 'fixed', 'characters': 512, 'overflow_unit': True},
            'subject': {'class': 'fixed', 'characters': 1000, 'overflow_unit': True},
            'work_item_records': {'class': 'collection', 'characters': 256, 'unit_rule': 'each_entry'},
            'session_turn': {'class': 'carried', 'characters': 256, 'unit_rule': 'stored_plan'},
            'session_final': {'class': 'carried', 'characters': 256, 'unit_rule': 'stored_plan'},
            'session_combine': {'class': 'carried', 'characters': 256, 'unit_rule': 'stored_plan'},
        },
        'exactory-problem-review-v2': {
            'area': {'class': 'fixed', 'characters': 1000, 'overflow_unit': True},
            'bar_ids': {'class': 'collection', 'characters': 256, 'unit_rule': 'each_entry'},
            'bars': {'class': 'collection', 'characters': 256, 'unit_rule': 'each_entry'},
            'candidate_set_digest': {'class': 'reference', 'characters': 256, 'unit_rule': 'bound_digest'},
            'candidates': {'class': 'collection', 'characters': 256, 'unit_rule': 'each_entry'},
            'context_repair': {'class': 'fixed', 'characters': 2000, 'overflow_unit': True},
            'continuation': {'class': 'fixed', 'characters': 512, 'overflow_unit': True},
            'digest': {'class': 'reference', 'characters': 256, 'unit_rule': 'bound_digest'},
            'dossier_id': {'class': 'fixed', 'characters': 1000, 'overflow_unit': True},
            'evidence': {'class': 'collection', 'characters': 256, 'unit_rule': 'each_entry'},
            'evidence.*.content': {'characters': 256, 'unit_rule': 'owning_unit', 'class': 'body'},
            'field': {'class': 'fixed', 'characters': 1000, 'overflow_unit': True},
            'forming_records': {'class': 'collection', 'characters': 256, 'unit_rule': 'each_entry'},
            'historical_reviews': {'class': 'collection', 'characters': 256, 'unit_rule': 'each_entry'},
            'history_delivery': {'class': 'fixed', 'characters': 512, 'overflow_unit': True},
            'intent': {'class': 'fixed', 'characters': 16000, 'overflow_unit': True},
            MAIN_CAPTURE_SELECTIONS: {'class': 'fixed', 'characters': 1000, 'overflow_unit': True},
            'material_objections': {'class': 'collection', 'characters': 256, 'unit_rule': 'each_entry'},
            'output_refusal': {'class': 'fixed', 'characters': 512, 'overflow_unit': True},
            'own_bars': {'class': 'collection', 'characters': 256, 'unit_rule': 'each_entry'},
            'place_review': {'class': 'fixed', 'characters': 512, 'overflow_unit': True},
            'pending_source_requests': {'class': 'collection', 'characters': 256, 'unit_rule': 'each_entry'},
            'previously_supplied_evidence': {'class': 'collection', 'characters': 256, 'unit_rule': 'each_entry'},
            'problem_areas': {'class': 'collection', 'characters': 256, 'unit_rule': 'each_entry'},
            'problem_forming_imports': {'class': 'collection', 'characters': 256, 'unit_rule': 'each_entry'},
            'problem_objection_carry': {'class': 'fixed', 'characters': 512, 'overflow_unit': True},
            'problem_refresh': {'class': 'fixed', 'characters': 1000, 'overflow_unit': True},
            'prior_context': {'class': 'collection', 'characters': 256, 'unit_rule': 'each_entry'},
            'protocol': {'class': 'fixed', 'characters': 1000, 'overflow_unit': True},
            'reviewer_excerpts': {'class': 'carried', 'characters': 256, 'unit_rule': 'stored_plan'},
            'reviewer_id': {'class': 'fixed', 'characters': 1000, 'overflow_unit': True},
            'role': {'class': 'fixed', 'characters': 1000, 'overflow_unit': True},
            'route_capacity': {'class': 'fixed', 'characters': 4000, 'overflow_unit': True},
            'scientific_history': {'class': 'carried', 'characters': 256, 'unit_rule': 'stored_plan'},
            'settled_source_requests': {'class': 'collection', 'characters': 256, 'unit_rule': 'each_entry'},
            'source_artifacts': {'class': 'collection', 'characters': 256, 'unit_rule': 'each_entry'},
            'source_deliveries': {'class': 'collection', 'characters': 256, 'unit_rule': 'each_entry'},
            'source_inventory': {'class': 'reference', 'characters': 256, 'unit_rule': 'bound_digest'},
            'source_reply_batch': {'class': 'fixed', 'characters': 8000, 'overflow_unit': True},
            'source_rules': {'class': 'fixed', 'characters': 512, 'overflow_unit': True},
            'subject': {'class': 'fixed', 'characters': 1000, 'overflow_unit': True},
            'session_turn': {'class': 'carried', 'characters': 256, 'unit_rule': 'stored_plan'},
            'session_final': {'class': 'carried', 'characters': 256, 'unit_rule': 'stored_plan'},
            'session_combine': {'class': 'carried', 'characters': 256, 'unit_rule': 'stored_plan'},
        },
    },
    'standalone': {
        'exactory-independent-review-v1': {
            'digest': {'class': 'reference', 'characters': 256, 'unit_rule': 'bound_digest'},
            'dossier_id': {'class': 'fixed', 'characters': 512, 'overflow_unit': True},
            'evidence': {'class': 'collection', 'characters': 256, 'unit_rule': 'each_entry'},
            'evidence.*.content': {'characters': 256, 'unit_rule': 'owning_unit', 'class': 'body'},
            'output_refusal': {'class': 'fixed', 'characters': 512, 'overflow_unit': True},
            'paper': {'class': 'fixed', 'characters': 16000, 'overflow_unit': True},
            'protocol': {'class': 'fixed', 'characters': 512, 'overflow_unit': True},
            'reviewer_id': {'class': 'fixed', 'characters': 512, 'overflow_unit': True},
            'role': {'class': 'fixed', 'characters': 512, 'overflow_unit': True},
        },
    },
    'unit': {
        'descriptor': {
            'aliases': {'class': 'collection', 'characters': 256, 'unit_rule': 'each_entry'},
            'bodies': {'class': 'collection', 'characters': 256, 'unit_rule': 'each_entry'},
            'bodies.*.aliases': {'class': 'collection', 'characters': 256, 'unit_rule': 'each_entry'},
            'carded_under': {'class': 'fixed', 'characters': 512, 'overflow_unit': True},
            'characters': {'class': 'fixed', 'characters': 512, 'overflow_unit': True},
            'citations': {'class': 'collection', 'characters': 256, 'unit_rule': 'each_entry'},
            'digest': {'class': 'reference', 'characters': 256, 'unit_rule': 'bound_digest'},
            'handle': {'class': 'fixed', 'characters': 512, 'overflow_unit': True},
            'item_characters': {'class': 'fixed', 'characters': 512, 'overflow_unit': True},
            'occurrences': {'class': 'collection', 'characters': 256, 'unit_rule': 'each_entry'},
            'record_identity': {'class': 'fixed', 'characters': 512, 'overflow_unit': True},
            'sha256': {'class': 'fixed', 'characters': 512, 'overflow_unit': True},
        },
    },
    'verification': {
        'exactory-independent-review-v1': {
            'digest': {'class': 'reference', 'characters': 256, 'unit_rule': 'bound_digest'},
            'dossier_id': {'class': 'fixed', 'characters': 512, 'overflow_unit': True},
            'evidence': {'class': 'collection', 'characters': 256, 'unit_rule': 'each_entry'},
            'evidence.*.content': {'characters': 256, 'unit_rule': 'owning_unit', 'class': 'body'},
            'output_refusal': {'class': 'fixed', 'characters': 512, 'overflow_unit': True},
            'paper': {'class': 'fixed', 'characters': 16000, 'overflow_unit': True},
            'protocol': {'class': 'fixed', 'characters': 512, 'overflow_unit': True},
            'reviewer_id': {'class': 'fixed', 'characters': 512, 'overflow_unit': True},
            'role': {'class': 'fixed', 'characters': 512, 'overflow_unit': True},
        },
    },
}

# The aggregate reserves the envelope and non-fixed metadata. Zero-cost tags
# identify fixed fields whose complete descriptors are irreducible frame fields.
# Tags survive canonical JSON ordering when a stored plan is read back.
FRAME_DESCRIPTOR_FIELD = '$descriptors'
FRAME_UNIT_FIELD_PREFIX = '$unit:'
FRAME_BINDING_PATH_PREFIX = '$binding-paths:'
FRAME_BINDING_MEMBERS_PREFIX = '$binding-members:'
REVIEW_BINDING_FIELDS = frozenset(('protocol', 'role', 'reviewer_id', 'dossier_id', 'subject'))
# Values copied into a response or compared with its assignment belong in the frame.
# None keeps a complete value. A set keeps only the listed members if the wrapper overflows.
SHARED_BINDING_PATHS = {
    'context_repair': frozenset(('assignment_id', 'slot_id', 'probe_id', 'exhaustion', 'predecessor_binding')),
    'continuation': frozenset(('assignment_id', 'packet_digest')),
    'problem_refresh': frozenset(('assignment_id', 'previous_candidate_set_digest', 'candidate_set_digest',
        'previous_problem_evidence_digest', 'problem_evidence_digest', 'previous_problem_review_currency',
        'problem_review_currency')),
}
ROLE_BINDING_PATHS = {
    'result': {
        'assessor': None,
        'support_contract': frozenset(('candidate_digest', 'target', 'required_checks')),
        'readiness': frozenset(('review_target',)),
    },
    'adjudicator': {
        'objection': frozenset(('id', 'review_id', 'material_version')),
        'correction': frozenset(('kind', 'prior_adjudication_id')),
        'focused_refresh': frozenset(('assignment_id', 'previous_problem_evidence_digest',
                                      'problem_evidence_digest', 'previous_material_version', 'material_version')),
    },
    'manuscript': {'paper': frozenset(('kind', 'bundle_digest'))},
    'native_math': {'native': frozenset(('protocol', 'kind', 'root', 'objective_id', 'subject', 'subject_digest',
                                        'claim_digest', 'reviewer', 'contract', 'unmaterialized_digests'))},
    'verification': {'paper': frozenset(('kind', 'task_digest', 'target', 'task_identity', 'preparation_digest',
                                       'sampling_context'))},
}
UNCLASSIFIED_FIELD_MESSAGE = 'Packet field {role}/{form}:{field} has no field class.'


def _select_table(role, packet):
    forms = FIELD_CLASSES.get(role, {})
    if role == 'unit':
        form = 'descriptor'
    elif role == 'native_math' and 'scientific_context' in packet and 'native' not in packet:
        form = 'native'
    elif not packet.get('protocol') and packet.get('kind') in forms:
        form = packet['kind']
    else:
        form = packet.get('protocol', '')
    if form not in forms:
        _refuse_unclassified_field(role, form, '<packet>')
    return form, forms[form]


def _refuse_unclassified_field(role, form, field):
    details = dict(role=role, form=form, field=field)
    raise ResearchError('review_input_unreadable', UNCLASSIFIED_FIELD_MESSAGE.format(**details), details)


def _find_declared_row(table, path):
    if not path:
        return None
    for name, row in table.items():
        components = name.split('.')
        if len(components) == len(path) and all(expected == '*' or expected == str(actual)
                                              for expected, actual in zip(components, path)):
            return row
    return None


def _find_inherited_row(table, path):
    for length in range(len(path), 0, -1):
        row = _find_declared_row(table, path[:length])
        if row is not None:
            return row
    return None


def _list_value_entries(value):
    if isinstance(value, dict):
        return [(key, value[key]) for key in sorted(value)]
    if isinstance(value, list):
        return list(enumerate(value))
    return []


def _describe_value_unit(route, value, path, kind):
    return dict(handle='f-' + digest(list(path)), kind=kind,
                characters=measure_capacity_value(route, value), sha256=digest(value))


def _describe_value_reference(route, value, path):
    unit = _describe_value_unit(route, value, path, 'fixed')
    return {key: unit[key] for key in ('handle', 'sha256', 'characters')}


def _measure_field_overhead(route, name):
    return measure_capacity_value(route, {name: None}) - measure_capacity_value(route, None)


def _project_children(route, value, path, table):
    entries = _list_value_entries(value)
    if not isinstance(value, (dict, list)):
        return value, [], {}
    projected = {} if isinstance(value, dict) else []
    units, owners = [], {}
    for key, item in entries:
        child = path + (key,)
        row = _find_declared_row(table, child)
        if row is None:
            result, additions, places = _project_children(route, item, child, table)
        else:
            result, additions, places = _project_field(route, item, child, row, row['characters'], table)
        if isinstance(projected, dict):
            projected[key] = result
        else:
            projected.append(result)
        units.extend(additions)
        owners.update(places)
    return projected, units, owners


def _project_field(route, value, path, row, bound, table):
    category = row['class']
    if category == 'collection':
        entries = _list_value_entries(value)
        units = [_describe_value_unit(route, item, path + (key,), category) for key, item in entries]
        summary = dict(entries=len(entries), characters=sum(unit['characters'] for unit in units))
        return summary, units, {path: 'unit'}
    if category == 'reference':
        reference = value if isinstance(value, str) and len(value) == 64 else dict(
            sha256=value.get('sha256', value.get('digest', digest(value))) if isinstance(value, dict) else digest(value),
            characters=measure_capacity_value(route, value))
        return reference, [], {path: 'frame'}
    if category in ('body', 'carried'):
        unit = _describe_value_unit(route, value, path, category)
        return _describe_value_reference(route, value, path), [unit], {path: 'unit'}
    projected, units, owners = _project_children(route, value, path, table)
    if measure_capacity_value(route, projected) <= bound:
        owners[path] = 'frame'
        return projected, units, owners
    unit = _describe_value_unit(route, value, path, category)
    return _describe_value_reference(route, value, path), [unit], {path: 'unit'}


def _project_binding_field(route, value, name, members, table):
    path = (name,)
    if members is None:
        return copy.deepcopy(value), [], {path: 'frame'}
    projected, units, owners = {}, [], {path: 'frame'}
    for key, item in _list_value_entries(value):
        child = path + (key,)
        if key in members or not item:
            projected[key] = copy.deepcopy(item)
            continue
        result, additions, places = _project_children(route, item, child, table)
        if places and measure_capacity_value(route, result) <= table[name]['characters']:
            projected[key] = result
            units.extend(additions)
            owners.update(places)
        else:
            projected[key] = _describe_value_reference(route, item, child)
            units.append(_describe_value_unit(route, item, child, 'fixed'))
            owners[child] = 'unit'
    return projected, units, owners


def _build_classified_frame(route, role, packet, bounded_fields, unit_fields=()):
    form, table = _select_table(role, packet)
    frame, units, owners = {}, [], {}
    moved = set(unit_fields) | {name[len(FRAME_UNIT_FIELD_PREFIX):] for name in bounded_fields
                               if name.startswith(FRAME_UNIT_FIELD_PREFIX)}
    binding_members = {}
    for tag in bounded_fields:
        if tag.startswith(FRAME_BINDING_MEMBERS_PREFIX):
            name, encoded = tag[len(FRAME_BINDING_MEMBERS_PREFIX):].split(':', 1)
            binding_members[name] = json.loads(encoded)
    for name in sorted(packet):
        tag = FRAME_BINDING_PATH_PREFIX + name
        if tag in bounded_fields:
            members, rows = binding_members[name]
            frame[name], additions, places = _project_binding_field(route, packet[name], name, members, dict(rows))
            size = measure_capacity_value(route, {name: frame[name]})
            if size > bounded_fields[tag]:
                raise ResearchError('review_route_capacity_below_floor', BELOW_FLOOR_MESSAGE,
                                    dict(room=bounded_fields[tag], minimum=size, field=name, command='review-route'))
        else:
            row = table.get(name)
            if row is None:
                _refuse_unclassified_field(role, form, name)
            overhead = _measure_field_overhead(route, name)
            bound = (0 if name in moved else
                     min(row['characters'], max(0, bounded_fields.get(name, row['characters'] + overhead) - overhead)))
            frame[name], additions, places = _project_field(route, packet[name], (name,), row, bound, table)
        units.extend(additions)
        owners.update(places)
    return table, frame, units, owners


def _find_binding_paths(role):
    return dict.fromkeys(REVIEW_BINDING_FIELDS) | SHARED_BINDING_PATHS | ROLE_BINDING_PATHS.get(role, {})


def classify_packet(route, role, packet, *, bounded_fields=None):
    """Return a frame, stable units and a measured row for every field and entry.

    Paths are tuples, including integer list indices. A unit owns its complete
    original value, so nested fields report that same travel without counting
    their characters twice in the inventory. The optional bounds read a stored
    plan without changing the frozen packet or the table.
    """
    table, frame, units, owners = _build_classified_frame(route, role, packet, bounded_fields or {})
    rows = []
    def visit(value, path):
        if path:
            owner = next(prefix for prefix in (path[:length] for length in range(len(path), 0, -1))
                         if prefix in owners)
            rows.append(dict(path=path, row=copy.deepcopy(_find_inherited_row(table, path)),
                             characters=measure_capacity_value(route, value), travel=owners[owner]))
        for key, item in _list_value_entries(value):
            visit(item, path + (key,))
    visit(packet, ())
    return dict(fields=rows, frame=frame, units=units,
                unit_characters=sum(unit['characters'] for unit in units))


def describe_role_frame(route, role, packet, kinds, *, unit_fields=()):
    """Declare fixed value maxima or complete, irreducible unit descriptors.

    unit_fields is a sequence of fixed field names, or a mapping from turn kind
    to such a sequence. Each moved field retains its own measured descriptor
    size, including handle, digest, characters and native field envelope. A
    zero-cost tag preserves that ownership through a stored plan's JSON order.
    The aggregate reserve holds the envelope and non-fixed frame metadata.
    If the fixed allowances would move part of the identity, reserve the
    exact binding outside the movable fields. A zero-cost tag preserves its
    kept members and nested field classes for replay. Frames whose identity
    already fits keep their original declarations.
    """
    moved = unit_fields if isinstance(unit_fields, dict) else tuple(unit_fields)
    binding = _find_binding_paths(role)
    frames = {}
    for kind in kinds:
        table, frame, _, owners = _build_classified_frame(
            route, role, packet, {}, moved.get(kind, ()) if isinstance(moved, dict) else moved)
        moved_binding = {
            name for name in binding.keys() & packet.keys() if owners[(name,)] == 'unit' and (
                binding[name] is None or isinstance(packet[name], dict) and binding[name] & packet[name].keys())
        }
        # The common identity shares an exact floor when any of its members spills.
        common = REVIEW_BINDING_FIELDS & packet.keys()
        if moved_binding & common:
            moved_binding = moved_binding | common
        fields = {FRAME_DESCRIPTOR_FIELD: measure_capacity_value(route, {})}
        for name in sorted(packet):
            if name in moved_binding:
                members = binding[name]
                rows = [] if members is None else [(key, row) for key, row in table.items()
                                                   if key == name or key.startswith(name + '.')]
                projected, _, _ = _project_binding_field(route, packet[name], name, members, dict(rows))
                fields[FRAME_BINDING_PATH_PREFIX + name] = measure_capacity_value(route, {name: projected})
                kept = None if members is None else sorted(members)
                encoded = json.dumps([kept, rows], sort_keys=True, separators=(',', ':'))
                fields[FRAME_BINDING_MEMBERS_PREFIX + name + ':' + encoded] = 0
            elif table[name]['class'] != 'fixed':
                fields[FRAME_DESCRIPTOR_FIELD] += measure_capacity_value(route, {name: frame[name]})
                fields[name] = 0
            elif owners[(name,)] == 'unit':
                fields[name] = measure_capacity_value(route, {name: frame[name]})
                fields[FRAME_UNIT_FIELD_PREFIX + name] = 0
            else:
                fields[name] = table[name]['characters'] + _measure_field_overhead(route, name)
        frames[kind] = fields
    return frames


def list_frame_units(route, role, packet, bounded_fields):
    """Read all units, including fixed overflow under a stored kind's bounds."""
    return _build_classified_frame(route, role, packet, bounded_fields)[2]


def plan_role_capacity(route, role, packet, kinds, *, profile=None, targets=(), carried_budget=None,
                       records=None, artifacts=None, measure=None, allowance_rows_bound=None):
    """Return {plan, units} at the fixed point of the role's frame declarations.

    Each iteration permanently moves newly lowered fixed fields for their turn
    kind. There are at most len(packet) such moves per kind. The inventory is
    the stable union of each kind's units; list_frame_units reads a particular
    kind from its stored bounded_fields, including the zero-cost unit tags.
    A descriptor or task floor that cannot fit uses the planner's existing stop.
    Canonical kind order also preserves the inventory digest on stored replay.
    """
    kinds = tuple(sorted(kinds))
    targets = tuple(targets)
    moved = {kind: set() for kind in kinds}
    if profile is None:
        profile = read_output_profile({} if records is None else records, route)
    while True:
        frames = describe_role_frame(route, role, packet, kinds, unit_fields=moved)
        inventory = {}
        for kind in kinds:
            for unit in list_frame_units(route, role, packet, frames[kind]):
                inventory.setdefault(unit['handle'], unit)
        units = list(inventory.values())
        plan = plan_chain_capacity(route, profile, frames=frames, inventory=units, targets=targets,
            carried_budget=carried_budget, records=records, artifacts=artifacts, measure=measure,
            allowance_rows_bound=allowance_rows_bound)
        if not any(frame['unit_fields'] for frame in plan['frame'].values()):
            return dict(plan=plan, units=units)
        for kind in kinds:
            moved[kind].update(plan['frame'][kind]['unit_fields'])


def describe_unit_references(descriptor, size=REFERENCE_ROWS_MAXIMUM_CHARACTERS, *, route=None):
    """Select a serialized prefix of reference rows, retaining all remaining rows.

    The descriptor's insertion order is the frozen packet's order. Entries keep
    their field and index, so identical rows and cross-list occurrences remain
    distinct. The count, wrapper and selected rows all consume the size.
    """
    rows = []
    def collect(value, path):
        if isinstance(value, dict):
            for field, items in value.items():
                child = path + (field,)
                if field in ('occurrences', 'citations', 'aliases'):
                    rows.extend(dict(field=field, path=child, index=index, row=copy.deepcopy(row))
                                for index, row in enumerate(items))
                elif field == 'bodies':
                    collect(items, child)
        elif isinstance(value, list):
            for index, item in enumerate(value):
                collect(item, path + (index,))
    collect(descriptor, ())
    # A caller can supply its route. The default measures the ordinary local
    # text envelope, without launching a model or depending on a model prior.
    if route is None:
        route = dict(adapter='claude_cli_v1', model='field-measurement', endpoint='claude://local',
                     configuration=dict(max_output_tokens=1, timeout_seconds=1))
    selected = []
    envelope = dict(count=len(rows), rows=selected)
    used = measure_capacity_value(route, envelope)
    # Native routes can wrap JSON differently. Measure the complete candidate
    # prefix instead of adding independently serialized row costs.
    for row in rows:
        candidate = dict(count=len(rows), rows=selected + [row])
        characters = measure_capacity_value(route, candidate)
        if characters > size:
            break
        selected.append(row)
        used = characters
    return dict(count=len(rows), rows=selected, remaining=rows[len(selected):], characters=used)


def describe_registry_classes(registry=None):
    """Describe the three registry gaps using the nearest inherited table row.

    Accept an explicit registry for release tooling. The repository's reviewed
    registry is the default; its declarations never become runtime class data.
    """
    if registry is None:
        path = Path(__file__).resolve().parents[1] / 'tests/fixtures/capacity_packet_schema.json'
        registry = json.loads(path.read_text(encoding='utf-8'))
    result = {gap: {} for gap in ('A1_frame_metadata', 'A6_parts_frame_metadata', 'Q10_frame_metadata')}
    for member, declaration in sorted(registry['quantity_contracts'].items()):
        gap = declaration.get('gap')
        if gap not in result:
            continue
        identity, field = member.split(':', 1)
        role, protocol = identity.split('/', 1)
        table = FIELD_CLASSES.get(role, {}).get(protocol, {})
        row = _find_inherited_row(table, tuple(field.split('.')))
        if row is not None:
            result[gap][member] = copy.deepcopy(row)
    return result
