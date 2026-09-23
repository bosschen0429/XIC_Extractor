"""Contracts and constants for low MS1 coverage review diagnostics."""

from __future__ import annotations

REVIEW_REQUIRED_COLUMNS = (
    "feature_family_id",
    "family_center_mz",
    "family_center_rt",
    "suggested_rt_min",
    "suggested_rt_max",
    "suggested_output_prefix",
    "detected_count",
    "accepted_backfill_count",
    "detected_backfilled_count",
    "global_apex_assessable_fraction",
    "selected_apex_in_trace_window_fraction",
    "review_classification",
)

ALIGNMENT_REVIEW_REQUIRED_COLUMNS = (
    "feature_family_id",
    "event_cluster_count",
    "event_member_count",
    "identity_decision",
    "primary_evidence",
    "row_flags",
    "reason",
)

TRACE_REQUIRED_COLUMNS = (
    "sample_stem",
    "status",
    "cell_area",
    "cell_height",
    "cell_apex_rt",
    "trace_max_intensity",
    "trace_apex_rt",
    "global_trace_apex_delta_min",
    "local_window_to_global_max_ratio",
    "region_shadow_verdict",
)

TRACE_DISCOVERY_JOIN_COLUMNS = ("source_candidate_id",)

BACKFILL_SEED_AUDIT_REQUIRED_COLUMNS = (
    "feature_family_id",
    "sample_stem",
    "backfill_seed_mz",
    "backfill_seed_rt",
    "backfill_request_rt_min",
    "backfill_request_rt_max",
    "backfill_request_ppm",
    "backfill_apex_delta_sec",
)

DISCOVERY_REQUIRED_COLUMNS = (
    "candidate_id",
    "precursor_mz",
    "product_mz",
    "observed_neutral_loss_da",
    "evidence_score",
    "seed_event_count",
    "neutral_loss_mass_error_ppm",
)

LOW_COVERAGE_CLASSIFICATION = "low_ms1_assessable_coverage_review"
ASSESSABLE_FRACTION_MIN = 0.70
SELECTED_APEX_IN_WINDOW_MIN = 0.70
ZERO_TRACE_INSIDE_WINDOW_FRACTION_MIN = 0.30
SELECTED_APEX_OVERLAY_PADDING_MIN = 0.35
SEED_RT_SPAN_CONCERN_MIN = 0.20
SEED_APEX_DELTA_CONCERN_SEC = 60.0
SEED_OVERLAY_QUEUE_BUCKETS = frozenset(
    {
        "multi_seed_family_center_overlay_incomplete",
        "seed_apex_delta_concern",
    }
)
APEX_AWARE_QUEUE_BUCKETS = frozenset(
    {
        "rt_window_or_multiseed_shift",
        "rt_window_mismatch",
        "multi_seed_overlay_limitation",
    }
)
