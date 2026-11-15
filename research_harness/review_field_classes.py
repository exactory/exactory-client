"""Declared field ownership and bounded frames for prospective role protocols.

Sizes use measure_capacity_value, including native serialized quote escaping.
A nested member inherits its nearest row. Only an explicit nested row changes
its ownership; a whole-value overflow owns all of its descendants.
"""

import copy
import json
from pathlib import Path

from .evidence import digest
from .review_route_capacity import measure_capacity_value

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
            'source_artifacts': {'class': 'collection', 'characters': 256, 'unit_rule': 'each_entry'},
            'source_deliveries': {'class': 'collection', 'characters': 256, 'unit_rule': 'each_entry'},
            'source_inventory': {'class': 'reference', 'characters': 256, 'unit_rule': 'bound_digest'},
            'source_reply_batch': {'class': 'fixed', 'characters': 8000, 'overflow_unit': True},
            'source_rules': {'class': 'fixed', 'characters': 512, 'overflow_unit': True},
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
            'source_artifacts': {'class': 'collection', 'characters': 256, 'unit_rule': 'each_entry'},
            'source_deliveries': {'class': 'collection', 'characters': 256, 'unit_rule': 'each_entry'},
            'source_inventory': {'class': 'reference', 'characters': 256, 'unit_rule': 'bound_digest'},
            'source_reply_batch': {'class': 'fixed', 'characters': 8000, 'overflow_unit': True},
            'source_rules': {'class': 'fixed', 'characters': 512, 'overflow_unit': True},
            'subject': {'class': 'fixed', 'characters': 1000, 'overflow_unit': True},
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
            'protocol': {'class': 'fixed', 'characters': 512, 'overflow_unit': True},
            'readiness': {'class': 'fixed', 'characters': 16000, 'overflow_unit': True},
            'reviewer_id': {'class': 'fixed', 'characters': 512, 'overflow_unit': True},
            'role': {'class': 'fixed', 'characters': 512, 'overflow_unit': True},
            'route_capacity': {'class': 'fixed', 'characters': 4000, 'overflow_unit': True},
            'source_deliveries': {'class': 'collection', 'characters': 256, 'unit_rule': 'each_entry'},
            'source_inventory': {'class': 'reference', 'characters': 256, 'unit_rule': 'bound_digest'},
            'source_reply_batch': {'class': 'fixed', 'characters': 8000, 'overflow_unit': True},
            'source_rules': {'class': 'fixed', 'characters': 512, 'overflow_unit': True},
            'support_contract': {'class': 'fixed', 'characters': 4000, 'overflow_unit': True},
            'visual_context_repair': {'class': 'fixed', 'characters': 512, 'overflow_unit': True},
            'work_item_records': {'class': 'collection', 'characters': 256, 'unit_rule': 'each_entry'},
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
            'problem_evidence_parts': {'class': 'fixed', 'characters': 7000, 'overflow_unit': True},
            'problem_forming_imports': {'class': 'collection', 'characters': 256, 'unit_rule': 'each_entry'},
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
        },
        'readiness': {
            'execution_observations': {'class': 'collection', 'characters': 256, 'unit_rule': 'each_entry'},
            'inputs': {'class': 'fixed', 'characters': 32000, 'overflow_unit': True},
            'kind': {'class': 'fixed', 'characters': 512, 'overflow_unit': True},
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
            'output_refusal': {'class': 'fixed', 'characters': 512, 'overflow_unit': True},
            'own_bars': {'class': 'collection', 'characters': 256, 'unit_rule': 'each_entry'},
            'pending_source_requests': {'class': 'collection', 'characters': 256, 'unit_rule': 'each_entry'},
            'previously_supplied_evidence': {'class': 'collection', 'characters': 256, 'unit_rule': 'each_entry'},
            'problem_areas': {'class': 'collection', 'characters': 256, 'unit_rule': 'each_entry'},
            'problem_forming_imports': {'class': 'collection', 'characters': 256, 'unit_rule': 'each_entry'},
            'problem_refresh': {'class': 'fixed', 'characters': 1000, 'overflow_unit': True},
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
        },
        'exactory-problem-review-v2': {
            'area': {'class': 'fixed', 'characters': 1000, 'overflow_unit': True},
            'bar_ids': {'class': 'collection', 'characters': 256, 'unit_rule': 'each_entry'},
            'bars': {'class': 'collection', 'characters': 256, 'unit_rule': 'each_entry'},
            'candidate_set_digest': {'class': 'reference', 'characters': 256, 'unit_rule': 'bound_digest'},
            'candidates': {'class': 'collection', 'characters': 256, 'unit_rule': 'each_entry'},
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
            'material_objections': {'class': 'collection', 'characters': 256, 'unit_rule': 'each_entry'},
            'own_bars': {'class': 'collection', 'characters': 256, 'unit_rule': 'each_entry'},
            'pending_source_requests': {'class': 'collection', 'characters': 256, 'unit_rule': 'each_entry'},
            'problem_areas': {'class': 'collection', 'characters': 256, 'unit_rule': 'each_entry'},
            'problem_forming_imports': {'class': 'collection', 'characters': 256, 'unit_rule': 'each_entry'},
            'problem_refresh': {'class': 'fixed', 'characters': 1000, 'overflow_unit': True},
            'protocol': {'class': 'fixed', 'characters': 1000, 'overflow_unit': True},
            'reviewer_id': {'class': 'fixed', 'characters': 1000, 'overflow_unit': True},
            'role': {'class': 'fixed', 'characters': 1000, 'overflow_unit': True},
            'source_artifacts': {'class': 'collection', 'characters': 256, 'unit_rule': 'each_entry'},
            'source_deliveries': {'class': 'collection', 'characters': 256, 'unit_rule': 'each_entry'},
            'source_inventory': {'class': 'reference', 'characters': 256, 'unit_rule': 'bound_digest'},
            'source_reply_batch': {'class': 'fixed', 'characters': 8000, 'overflow_unit': True},
            'source_rules': {'class': 'fixed', 'characters': 512, 'overflow_unit': True},
            'subject': {'class': 'fixed', 'characters': 1000, 'overflow_unit': True},
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

# Reserve descriptor space before the planner allocates optional fixed values.
# A lowered allocation can be zero; its descriptor must still fit the frame.
FRAME_DESCRIPTOR_FIELD = '$descriptors'
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
        raise ValueError(UNCLASSIFIED_FIELD_MESSAGE.format(role=role, form=form, field='<packet>'))
    return form, forms[form]


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


def _build_classified_frame(route, role, packet, bounded_fields):
    form, table = _select_table(role, packet)
    frame, units, owners = {}, [], {}
    for name in sorted(packet):
        row = table.get(name)
        if row is None:
            raise ValueError(UNCLASSIFIED_FIELD_MESSAGE.format(role=role, form=form, field=name))
        overhead = _measure_field_overhead(route, name)
        bound = min(row['characters'], max(0, bounded_fields.get(name, row['characters'] + overhead) - overhead))
        frame[name], additions, places = _project_field(route, packet[name], (name,), row, bound, table)
        units.extend(additions)
        owners.update(places)
    return table, frame, units, owners


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


def describe_role_frame(route, role, packet, kinds):
    """Return planner fields in allocation order, with a descriptor reserve first.

    Every packet field reserves its native field envelope and stated value size.
    The separate reserve pays for overflow descriptors even when the plan lowers
    a value allocation to zero. Collections, carried material and bodies use the
    reserve alone. Reference values have a bounded digest representation.
    """
    form, table = _select_table(role, packet)
    fields = {FRAME_DESCRIPTOR_FIELD: measure_capacity_value(route, {})}
    for name in sorted(packet):
        row = table.get(name)
        if row is None:
            raise ValueError(UNCLASSIFIED_FIELD_MESSAGE.format(role=role, form=form, field=name))
        overhead = _measure_field_overhead(route, name)
        fields[FRAME_DESCRIPTOR_FIELD] += DESCRIPTOR_MAXIMUM_CHARACTERS + overhead
        fields[name] = row['characters'] + overhead if row['class'] == 'fixed' else 0
    return {kind: dict(fields) for kind in kinds}


def list_frame_units(route, role, packet, bounded_fields):
    """Read all units, including fixed overflow under a stored kind's bounds."""
    return _build_classified_frame(route, role, packet, bounded_fields)[2]


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
