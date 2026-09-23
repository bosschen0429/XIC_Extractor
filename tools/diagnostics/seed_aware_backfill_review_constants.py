"""Constants for seed-aware backfill review diagnostics."""

from __future__ import annotations

import re

SUPPORT_VERDICT = "ms1_shape_supports_family_backfill"
NEIGHBOR_VERDICT = "review_required_neighboring_ms1_interference"

NEIGHBOR_INTERFERENCE_FRACTION_MAX = 0.25
MIN_RESCUE_COUNT = 40
MIN_ACCEPTED_COUNT = 60
SEED_OVERLAY_PATTERN = re.compile(r"_seed\d+_")

CLASS_SEED_SUPPORTED = "seed_shape_supported_review_candidate"
CLASS_NEIGHBOR = "neighbor_interference_review"
CLASS_SHAPE = "shape_insufficient_review"
CLASS_SEED_MISSING = "seed_context_missing"
CLASS_NOT_ASSESSABLE = "not_assessable"
CLASS_NOT_RESCUED_HEAVY = "not_rescued_heavy"

WITHHOLD_CLASSES = frozenset({CLASS_NEIGHBOR, CLASS_SHAPE})

REVIEW_REQUIRED_COLUMNS = (
    "feature_family_id",
    "family_center_mz",
    "family_center_rt",
    "detected_count",
    "accepted_backfill_count",
    "accepted_cell_count",
    "review_classification",
)

OVERLAY_REQUIRED_COLUMNS = (
    "feature_family_id",
    "status",
    "family_verdict",
    "global_apex_interference_fraction",
    "selected_apex_in_trace_window_fraction",
    "global_apex_assessable_fraction",
    "shape_supported_fraction",
    "png_path",
    "pdf_path",
)

LOW_COVERAGE_REQUIRED_COLUMNS = (
    "feature_family_id",
    "sample_stem",
    "status",
)

SEED_AUDIT_REQUIRED_COLUMNS = (
    "feature_family_id",
    "sample_stem",
    "backfill_seed_mz",
    "backfill_seed_rt",
    "backfill_request_rt_min",
    "backfill_request_rt_max",
    "backfill_request_ppm",
)
