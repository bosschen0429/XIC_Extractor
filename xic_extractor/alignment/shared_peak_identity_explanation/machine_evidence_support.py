from __future__ import annotations

from collections.abc import Mapping, Sequence

from xic_extractor.alignment.config import AlignmentConfig

from .machine_artifacts import MachineMatch
from .machine_evidence_loaders import (
    CANDIDATE_MS2_PATTERN_REQUIRED_COLUMNS,
    CWT_SHAPE_REQUIRED_COLUMNS,
    MATRIX_RT_DRIFT_POLICY_REQUIRED_COLUMNS,
    MS1_PATTERN_COHERENCE_REQUIRED_COLUMNS,
    PEAK_HYPOTHESIS_SELECTION_REQUIRED_COLUMNS,
    QC_MS1_PATTERN_REFERENCE_REQUIRED_COLUMNS,
    RT_MODE_EVIDENCE_REQUIRED_COLUMNS,
    SAMPLE_NEGATIVE_EVIDENCE_REQUIRED_COLUMNS,
    TIER2_TRACE_REQUIRED_COLUMNS,
    _family_ms2_required_tag_by_family,
    _has_family_ms2_required_tag,
    load_candidate_ms2_pattern_evidence,
    load_cwt_shape_evidence,
    load_matrix_rt_drift_policy_evidence,
    load_ms1_pattern_coherence_evidence,
    load_peak_hypothesis_selection,
    load_qc_ms1_pattern_reference_evidence,
    load_rt_mode_evidence,
    load_sample_negative_evidence,
    load_tier2_trace_evidence,
)
from .schema import (
    MACHINE_EVIDENCE_SUPPORT_SCHEMA_VERSION,
    validate_row_tokens,
)

__all__ = [
    "CANDIDATE_MS2_PATTERN_REQUIRED_COLUMNS",
    "CWT_SHAPE_REQUIRED_COLUMNS",
    "MATRIX_RT_DRIFT_POLICY_REQUIRED_COLUMNS",
    "MS1_PATTERN_COHERENCE_REQUIRED_COLUMNS",
    "PEAK_HYPOTHESIS_SELECTION_REQUIRED_COLUMNS",
    "QC_MS1_PATTERN_REFERENCE_REQUIRED_COLUMNS",
    "RT_MODE_EVIDENCE_REQUIRED_COLUMNS",
    "SAMPLE_NEGATIVE_EVIDENCE_REQUIRED_COLUMNS",
    "TIER2_TRACE_REQUIRED_COLUMNS",
    "_family_ms2_required_tag_by_family",
    "_has_family_ms2_required_tag",
    "build_machine_evidence_support_rows",
    "load_candidate_ms2_pattern_evidence",
    "load_cwt_shape_evidence",
    "load_matrix_rt_drift_policy_evidence",
    "load_ms1_pattern_coherence_evidence",
    "load_peak_hypothesis_selection",
    "load_qc_ms1_pattern_reference_evidence",
    "load_rt_mode_evidence",
    "load_sample_negative_evidence",
    "load_tier2_trace_evidence",
]

_POSITIVE_MACHINE_LABELS = frozenset(
    {"detected", "rescued", "selected", "present", "provisional_discovery"}
)
_ABSENT_MACHINE_LABELS = frozenset(
    {"absent", "missing", "no_match", "not_available", "not_detected", "unchecked"}
)
_RT_TAGS = frozenset({"rt_close", "rt_too_far", "rt_drift_possible"})
_SHAPE_TAGS = frozenset({"shape_complete", "shape_normal", "shape_bad"})
_PATTERN_TAGS = frozenset({"pattern_similar", "pattern_partial", "pattern_mismatch"})
_OPPORTUNITY_TAGS = frozenset({"low_intensity", "dda_stochastic_missing"})
_DEFAULT_ALIGNMENT_CONFIG = AlignmentConfig()

_CANDIDATE_MS2_OBSERVED_LEVELS = frozenset(
    {"sample_candidate_aligned", "sample_boundary_aligned"}
)
_CANDIDATE_MS2_SUPPORT_STATUSES = frozenset({"supportive", "partial_support"})
_CANDIDATE_MS2_CONFLICT_STATUSES = frozenset({"conflict"})
_MS1_PATTERN_OBSERVED_LEVELS = frozenset(
    {"sample_constellation", "sample_boundary_constellation", "trace_constellation"}
)
_MS1_PATTERN_SUPPORT_STATUSES = frozenset({"supportive", "partial_support"})
_MS1_PATTERN_CONFLICT_STATUSES = frozenset({"conflict"})
_QC_MS1_REFERENCE_OBSERVED_LEVELS = frozenset(
    {"qc_consensus_with_local_qc_overlay", "qc_consensus_qc_overlay"}
)
_QC_MS1_REFERENCE_SUPPORT_STATUSES = frozenset({"supportive", "partial_support"})
_QC_MS1_REFERENCE_CONFLICT_STATUSES = frozenset({"conflict"})
_MS1_SHAPE_SUPPORT_MIN = 0.50
_MS1_PEAK_QUALITY_VECTOR_BASIS = "family_ms1_overlay_raw_trace_vector"
_MS1_PEAK_QUALITY_VECTOR_OBSERVED_STATUSES = frozenset(
    {"supportive", "partial_support"}
)
_MS1_PATTERN_INCONCLUSIVE_REASONS = frozenset(
    {"family_ms1_overlay_shape_metric_inconclusive_apex_or_height"}
)
_MATRIX_RT_DRIFT_OBSERVED_LEVELS = frozenset(
    {"matrix_reference_aligned", "sample_istd_aligned", "family_consensus_aligned"}
)
_MATRIX_RT_DRIFT_SUPPORT_STATUSES = frozenset({"drift_supported", "rt_close"})
_MATRIX_RT_DRIFT_CONFLICT_STATUSES = frozenset({"drift_not_supported"})
_SAMPLE_NEGATIVE_EVIDENCE_CLASSES = frozenset(
    {
        "no_candidate_ms1_evidence",
        "pattern_mismatch",
        "rt_not_explained",
        "local_peak_not_decisive",
    }
)
_SAMPLE_NEGATIVE_EVIDENCE_OBSERVED_LEVELS = frozenset({"machine_observed"})
_RT_MODE_OBSERVED_STATUSES = frozenset(
    {
        "mode_supported",
        "mode_conflict",
        "mode_split_required",
        "consolidation_no_go",
        "tailing_confounded",
        "raw_mode_review_only",
    }
)
_RT_MODE_SUPPORT_STATUSES = frozenset({"mode_supported", "tailing_confounded"})
_RT_MODE_CONFLICT_STATUSES = frozenset(
    {"mode_conflict", "mode_split_required", "consolidation_no_go"}
)
_PEAK_HYPOTHESIS_OBSERVED_STATUSES = frozenset(
    {
        "product_candidate_core",
        "cross_mode_rescue_blocked",
        "mode_split_required",
        "consolidation_no_go",
        "tailing_review_only",
        "raw_mode_review_only",
    }
)
_PEAK_HYPOTHESIS_SUPPORT_STATUSES = frozenset(
    {"product_candidate_core", "tailing_review_only"}
)
_PEAK_HYPOTHESIS_CONFLICT_STATUSES = frozenset(
    {"cross_mode_rescue_blocked", "mode_split_required", "consolidation_no_go"}
)
_DDA_NON_DISPOSITIVE_MS1_INTENSITY_MIN = 2.5e4
_DDA_NON_DISPOSITIVE_TRIGGER_SCAN_MIN = 3
_DDA_NON_DISPOSITIVE_TRACE_STRENGTHS = frozenset({"moderate", "strong"})


def build_machine_evidence_support_rows(
    *,
    explanations: Sequence[Mapping[str, str]],
    shadow_rows: Sequence[Mapping[str, str]],
    machine_matches: Mapping[str, Sequence[MachineMatch]],
    cwt_shape_evidence: Mapping[tuple[str, str], Mapping[str, str]] | None = None,
    tier2_trace_evidence: Mapping[str, Mapping[str, str]] | None = None,
    candidate_ms2_pattern_evidence: Mapping[tuple[str, str], Mapping[str, str]]
    | None = None,
    ms1_pattern_coherence_evidence: Mapping[tuple[str, str], Mapping[str, str]]
    | None = None,
    qc_ms1_pattern_reference_evidence: Mapping[tuple[str, str], Mapping[str, str]]
    | None = None,
    matrix_rt_drift_policy_evidence: Mapping[tuple[str, str], Mapping[str, str]]
    | None = None,
    sample_negative_evidence: Mapping[tuple[str, str], Mapping[str, str]] | None = None,
    rt_mode_evidence: Mapping[tuple[str, str], Mapping[str, str]] | None = None,
    peak_hypothesis_selection: Mapping[tuple[str, str], Mapping[str, str]]
    | None = None,
) -> tuple[dict[str, str], ...]:
    shadow_by_id = {row["oracle_row_id"]: row for row in shadow_rows}
    cwt_shape_evidence = cwt_shape_evidence or {}
    tier2_trace_evidence = tier2_trace_evidence or {}
    candidate_ms2_pattern_evidence = candidate_ms2_pattern_evidence or {}
    family_ms2_required_tag = _family_ms2_required_tag_by_family(
        candidate_ms2_pattern_evidence
    )
    ms1_pattern_coherence_evidence = ms1_pattern_coherence_evidence or {}
    qc_ms1_pattern_reference_evidence = qc_ms1_pattern_reference_evidence or {}
    matrix_rt_drift_policy_evidence = matrix_rt_drift_policy_evidence or {}
    sample_negative_evidence = sample_negative_evidence or {}
    rt_mode_evidence = rt_mode_evidence or {}
    peak_hypothesis_selection = peak_hypothesis_selection or {}
    support_rows = [
        _support_row(
            explanation=explanation,
            shadow=shadow_by_id.get(explanation["oracle_row_id"], {}),
            matches=tuple(machine_matches.get(explanation["oracle_row_id"], ())),
            cwt_row=cwt_shape_evidence.get(
                (explanation["feature_family_id"], explanation["sample_id"]),
            ),
            tier2_row=tier2_trace_evidence.get(explanation["feature_family_id"]),
            candidate_ms2_row=candidate_ms2_pattern_evidence.get(
                (explanation["feature_family_id"], explanation["sample_id"]),
            ),
            family_ms2_required_tag_row=family_ms2_required_tag.get(
                explanation["feature_family_id"]
            ),
            ms1_pattern_row=ms1_pattern_coherence_evidence.get(
                (explanation["feature_family_id"], explanation["sample_id"]),
            ),
            qc_ms1_reference_row=qc_ms1_pattern_reference_evidence.get(
                (explanation["feature_family_id"], explanation["sample_id"]),
            ),
            matrix_rt_drift_row=matrix_rt_drift_policy_evidence.get(
                (explanation["feature_family_id"], explanation["sample_id"]),
            ),
            sample_negative_row=sample_negative_evidence.get(
                (explanation["feature_family_id"], explanation["sample_id"]),
            ),
            rt_mode_row=rt_mode_evidence.get(
                (explanation["feature_family_id"], explanation["sample_id"]),
            ),
            peak_hypothesis_row=peak_hypothesis_selection.get(
                (explanation["feature_family_id"], explanation["sample_id"]),
            ),
        )
        for explanation in explanations
    ]
    return tuple(
        sorted(
            support_rows,
            key=lambda row: (row["feature_family_id"], row["sample_id"]),
        )
    )


def _support_row(
    *,
    explanation: Mapping[str, str],
    shadow: Mapping[str, str],
    matches: Sequence[MachineMatch],
    cwt_row: Mapping[str, str] | None,
    tier2_row: Mapping[str, str] | None,
    candidate_ms2_row: Mapping[str, str] | None,
    family_ms2_required_tag_row: Mapping[str, str] | None,
    ms1_pattern_row: Mapping[str, str] | None,
    qc_ms1_reference_row: Mapping[str, str] | None,
    matrix_rt_drift_row: Mapping[str, str] | None,
    sample_negative_row: Mapping[str, str] | None,
    rt_mode_row: Mapping[str, str] | None,
    peak_hypothesis_row: Mapping[str, str] | None,
) -> dict[str, str]:
    tags = _tags(explanation)
    sample_matches = tuple(match for match in matches if match.sample_level)
    context_matches = tuple(match for match in matches if not match.sample_level)
    observed_metrics = _observed_machine_metrics(
        sample_matches,
        context_matches,
        cwt_row=cwt_row,
        tier2_row=tier2_row,
        candidate_ms2_row=candidate_ms2_row,
        family_ms2_required_tag_row=family_ms2_required_tag_row,
        ms1_pattern_row=ms1_pattern_row,
        qc_ms1_reference_row=qc_ms1_reference_row,
        matrix_rt_drift_row=matrix_rt_drift_row,
        sample_negative_row=sample_negative_row,
        rt_mode_row=rt_mode_row,
        peak_hypothesis_row=peak_hypothesis_row,
        tags=tags,
    )
    manual_facts = _manual_derived_facts(tags, explanation)
    missing_evidence = _missing_machine_evidence(
        explanation=explanation,
        tags=tags,
        sample_matches=sample_matches,
        context_matches=context_matches,
        cwt_row=cwt_row,
        tier2_row=tier2_row,
        candidate_ms2_row=candidate_ms2_row,
        family_ms2_required_tag_row=family_ms2_required_tag_row,
        ms1_pattern_row=ms1_pattern_row,
        qc_ms1_reference_row=qc_ms1_reference_row,
        matrix_rt_drift_row=matrix_rt_drift_row,
        sample_negative_row=sample_negative_row,
        rt_mode_row=rt_mode_row,
        peak_hypothesis_row=peak_hypothesis_row,
    )
    literature_refs = _literature_refs(
        tags=tags,
        missing_evidence=missing_evidence,
        explanation=explanation,
    )
    row = {
        "machine_evidence_support_schema_version": (
            MACHINE_EVIDENCE_SUPPORT_SCHEMA_VERSION
        ),
        "oracle_row_id": explanation["oracle_row_id"],
        "feature_family_id": explanation["feature_family_id"],
        "sample_id": explanation["sample_id"],
        "manual_label": explanation["manual_label"],
        "machine_current_label": explanation["machine_current_label"],
        "shadow_label": shadow.get("shadow_label", "unresolved_gap"),
        "shadow_alignment_status": shadow.get(
            "shadow_alignment_status",
            "unresolved",
        ),
        "status_label_alignment_status": _status_label_alignment_status(
            manual_label=explanation["manual_label"],
            machine_label=explanation["machine_current_label"],
        ),
        "rt_basis_status": _rt_basis_status(
            tags,
            sample_matches,
            matrix_rt_drift_row,
            rt_mode_row,
        ),
        "shape_basis_status": _shape_basis_status(
            tags,
            sample_matches,
            cwt_row,
            ms1_pattern_row,
        ),
        "pattern_basis_status": _pattern_basis_status(
            tags,
            context_matches,
            candidate_ms2_row,
            family_ms2_required_tag_row,
            ms1_pattern_row,
            qc_ms1_reference_row,
        ),
        "opportunity_basis_status": _opportunity_basis_status(
            tags,
            sample_matches,
            context_matches,
            tier2_row,
            candidate_ms2_row,
            family_ms2_required_tag_row,
            ms1_pattern_row,
            qc_ms1_reference_row,
        ),
        "scope_basis_status": _scope_basis_status(
            explanation,
            tags,
            sample_negative_row,
        ),
        "negative_evidence_basis_status": _negative_evidence_basis_status(
            explanation,
            tags,
            sample_negative_row,
        ),
        "negative_evidence_class": _negative_evidence_class(
            explanation,
            tags,
            sample_negative_row,
        ),
        "negative_evidence_detail": _negative_evidence_detail(
            explanation,
            tags,
            sample_negative_row,
        ),
        "observed_machine_metrics": observed_metrics,
        "manual_derived_facts": manual_facts,
        "missing_machine_evidence": ";".join(missing_evidence),
        "literature_support_refs": ";".join(literature_refs),
        "evidence_support_status": _evidence_support_status(
            manual_label=explanation["manual_label"],
            sample_id=explanation["sample_id"],
            missing_evidence=missing_evidence,
            observed_metrics=observed_metrics,
            manual_facts=manual_facts,
            has_machine_observed_metric=_has_machine_observed_metric(
                cwt_row=cwt_row,
                tier2_row=tier2_row,
                candidate_ms2_row=candidate_ms2_row,
                ms1_pattern_row=ms1_pattern_row,
                qc_ms1_reference_row=qc_ms1_reference_row,
                matrix_rt_drift_row=matrix_rt_drift_row,
                sample_negative_row=sample_negative_row,
                rt_mode_row=rt_mode_row,
                peak_hypothesis_row=peak_hypothesis_row,
            ),
        ),
        "diagnostic_only": "TRUE",
    }
    validate_row_tokens(row)
    return row


def _status_label_alignment_status(*, manual_label: str, machine_label: str) -> str:
    if manual_label == "not_applicable":
        return "context_only"
    if manual_label == "human_unjudgeable":
        return "not_evaluable"
    if machine_label in _POSITIVE_MACHINE_LABELS:
        if manual_label == "pass":
            return "proxy_agrees"
        if manual_label == "suspect":
            return "proxy_partial"
        if manual_label == "fail":
            return "proxy_contradicts"
    if machine_label in _ABSENT_MACHINE_LABELS:
        if manual_label == "fail":
            return "proxy_agrees"
        if manual_label == "suspect":
            return "proxy_partial"
        if manual_label == "pass":
            return "proxy_contradicts"
    return "not_available"


def _rt_basis_status(
    tags: frozenset[str],
    sample_matches: Sequence[MachineMatch],
    matrix_rt_drift_row: Mapping[str, str] | None,
    rt_mode_row: Mapping[str, str] | None,
) -> str:
    if _has_matrix_rt_drift_metric(matrix_rt_drift_row) or _has_rt_mode_metric(
        rt_mode_row
    ):
        return "machine_observed"
    if any(_has_value(match.row.get("apex_rt")) for match in sample_matches) and any(
        _has_value(match.row.get("rt_delta_sec")) for match in sample_matches
    ):
        return "machine_observed"
    if tags & _RT_TAGS:
        return "manual_oracle_derived"
    return "not_available"


def _shape_basis_status(
    tags: frozenset[str],
    sample_matches: Sequence[MachineMatch],
    cwt_row: Mapping[str, str] | None,
    ms1_pattern_row: Mapping[str, str] | None,
) -> str:
    if _has_cwt_metric(cwt_row) or _has_ms1_shape_metric(ms1_pattern_row):
        return "machine_observed"
    has_proxy = any(
        _has_value(match.row.get("trace_quality")) for match in sample_matches
    )
    has_manual = bool(tags & _SHAPE_TAGS)
    return _basis_status(has_machine_proxy=has_proxy, has_manual=has_manual)


def _pattern_basis_status(
    tags: frozenset[str],
    context_matches: Sequence[MachineMatch],
    candidate_ms2_row: Mapping[str, str] | None,
    family_ms2_required_tag_row: Mapping[str, str] | None,
    ms1_pattern_row: Mapping[str, str] | None,
    qc_ms1_reference_row: Mapping[str, str] | None,
) -> str:
    if (
        _has_candidate_ms2_pattern_metric(
            candidate_ms2_row,
        )
        or _has_family_ms2_required_tag(
            family_ms2_required_tag_row,
        )
        or _has_ms1_pattern_metric(ms1_pattern_row)
        or _has_qc_ms1_pattern_reference_metric(qc_ms1_reference_row)
    ):
        return "machine_observed"
    has_family_proxy = any(
        _has_value(match.row.get("neutral_loss_tag")) for match in context_matches
    )
    return _basis_status(
        has_machine_proxy=has_family_proxy,
        has_manual=bool(tags & _PATTERN_TAGS),
    )


def _opportunity_basis_status(
    tags: frozenset[str],
    sample_matches: Sequence[MachineMatch],
    context_matches: Sequence[MachineMatch],
    tier2_row: Mapping[str, str] | None,
    candidate_ms2_row: Mapping[str, str] | None,
    family_ms2_required_tag_row: Mapping[str, str] | None,
    ms1_pattern_row: Mapping[str, str] | None,
    qc_ms1_reference_row: Mapping[str, str] | None,
) -> str:
    if (
        _has_tier2_trace_metric(
            tier2_row,
        )
        or _has_ms1_intensity_opportunity_metric(
            ms1_pattern_row,
        )
        or _has_family_ms2_required_tag(
            candidate_ms2_row,
        )
        or _dda_non_dispositive_policy_supports_manual(
            tags,
            context_matches,
            candidate_ms2_row,
            family_ms2_required_tag_row,
            ms1_pattern_row,
            qc_ms1_reference_row,
        )
    ):
        return "machine_observed"
    machine_tokens = _machine_tokens(sample_matches, context_matches)
    has_proxy = any(
        _has_value(match.row.get("scan_support_score")) for match in sample_matches
    ) or bool(
        machine_tokens
        & {
            "low_scan_support",
            "weak_scan_support",
            "no_local_ms1_owner",
            "metric_unavailable",
        }
    )
    return _basis_status(
        has_machine_proxy=has_proxy,
        has_manual=bool(tags & _OPPORTUNITY_TAGS),
    )


def _scope_basis_status(
    explanation: Mapping[str, str],
    tags: frozenset[str],
    sample_negative_row: Mapping[str, str] | None,
) -> str:
    if _sample_negative_evidence_supports_manual(
        explanation,
        tags,
        sample_negative_row,
    ):
        return "machine_observed"
    if (
        explanation.get("manual_scope") == "scope_derived_unmentioned_fail"
        or "scope_derived_unmentioned_fail" in tags
    ):
        return "manual_oracle_derived"
    return "not_applicable"


def _negative_evidence_basis_status(
    explanation: Mapping[str, str],
    tags: frozenset[str],
    sample_negative_row: Mapping[str, str] | None,
) -> str:
    if _sample_negative_evidence_supports_manual(
        explanation,
        tags,
        sample_negative_row,
    ):
        return "machine_observed"
    if explanation.get("manual_label") in {"fail", "human_unjudgeable"}:
        inferred = _manual_negative_evidence_class(explanation, tags)
        if inferred != "not_available":
            return "manual_oracle_derived"
        return "not_available"
    return "not_applicable"


def _negative_evidence_class(
    explanation: Mapping[str, str],
    tags: frozenset[str],
    sample_negative_row: Mapping[str, str] | None,
) -> str:
    if _has_sample_negative_evidence_metric(sample_negative_row):
        if sample_negative_row is None:
            return "not_available"
        negative_class = sample_negative_row.get("negative_evidence_class", "")
        if negative_class in _SAMPLE_NEGATIVE_EVIDENCE_CLASSES:
            return negative_class
    if explanation.get("manual_label") in {"fail", "human_unjudgeable"}:
        return _manual_negative_evidence_class(explanation, tags)
    return "not_applicable"


def _negative_evidence_detail(
    explanation: Mapping[str, str],
    tags: frozenset[str],
    sample_negative_row: Mapping[str, str] | None,
) -> str:
    if _has_sample_negative_evidence_metric(sample_negative_row):
        if sample_negative_row is None:
            return ""
        return (
            sample_negative_row.get("negative_evidence_detail")
            or sample_negative_row.get("reason")
            or ""
        )
    inferred = _manual_negative_evidence_class(explanation, tags)
    if inferred == "not_available":
        return ""
    return "manual_reason_tags:" + ";".join(sorted(tags))


def _manual_negative_evidence_class(
    explanation: Mapping[str, str],
    tags: frozenset[str],
) -> str:
    if "pattern_mismatch" in tags:
        return "pattern_mismatch"
    if "rt_too_far" in tags:
        return "rt_not_explained"
    if tags & {"shape_bad", "boundary_ambiguous", "human_unjudgeable"}:
        return "local_peak_not_decisive"
    if (
        explanation.get("manual_scope") == "scope_derived_unmentioned_fail"
        or "scope_derived_unmentioned_fail" in tags
    ):
        return "not_available"
    return "not_available"


def _basis_status(*, has_machine_proxy: bool, has_manual: bool) -> str:
    if has_machine_proxy and has_manual:
        return "mixed"
    if has_machine_proxy:
        return "machine_proxy"
    if has_manual:
        return "manual_oracle_derived"
    return "not_available"


def _append_row_metrics(
    metrics: list[str],
    row: Mapping[str, str],
    fields: Sequence[tuple[str, str]],
) -> None:
    for metric_name, row_field in fields:
        _append_metric(metrics, metric_name, row.get(row_field))


def _observed_machine_metrics(
    sample_matches: Sequence[MachineMatch],
    context_matches: Sequence[MachineMatch],
    *,
    cwt_row: Mapping[str, str] | None,
    tier2_row: Mapping[str, str] | None,
    candidate_ms2_row: Mapping[str, str] | None,
    family_ms2_required_tag_row: Mapping[str, str] | None,
    ms1_pattern_row: Mapping[str, str] | None,
    qc_ms1_reference_row: Mapping[str, str] | None,
    matrix_rt_drift_row: Mapping[str, str] | None,
    sample_negative_row: Mapping[str, str] | None,
    rt_mode_row: Mapping[str, str] | None,
    peak_hypothesis_row: Mapping[str, str] | None,
    tags: frozenset[str],
) -> str:
    metrics: list[str] = []
    if sample_matches:
        first_sample = sample_matches[0].row
        _append_row_metrics(
            metrics,
            first_sample,
            (
                ("status", "status"),
                ("apex_rt", "apex_rt"),
                ("rt_delta_sec", "rt_delta_sec"),
            ),
        )
        if _has_rt_preferred_window_conflict(sample_matches):
            metrics.append("rt_preferred_window_status=outside_preferred_window")
        _append_row_metrics(
            metrics,
            first_sample,
            (
                ("trace_quality", "trace_quality"),
                ("scan_support_score", "scan_support_score"),
            ),
        )
        width_sec = _peak_width_sec(first_sample)
        if width_sec:
            metrics.append(f"peak_width_sec={width_sec}")
    for match in context_matches:
        if match.evidence_source == "alignment_review":
            if _has_cid_nl_pattern_context((match,)):
                metrics.append("cid_nl_pattern_context=family_level_present")
            _append_row_metrics(
                metrics,
                match.row,
                (
                    ("neutral_loss_tag", "neutral_loss_tag"),
                    ("family_product_mz", "family_product_mz"),
                    (
                        "family_observed_neutral_loss_da",
                        "family_observed_neutral_loss_da",
                    ),
                ),
            )
            break
    if cwt_row:
        _append_row_metrics(
            metrics,
            cwt_row,
            (
                ("cwt_status", "cwt_status"),
                ("cwt_shape_status", "cwt_shape_status"),
                ("cwt_apex_delta_sec", "cwt_apex_delta_sec"),
                ("cwt_boundary_width_sec", "cwt_boundary_width_sec"),
                ("cwt_prominence", "cwt_prominence"),
                ("cwt_region_scan_count", "cwt_region_scan_count"),
                ("cwt_quality_flags", "cwt_quality_flags"),
            ),
        )
    if tier2_row:
        _append_row_metrics(
            metrics,
            tier2_row,
            (
                ("tier2_raw_trace_status", "raw_trace_reread_status"),
                ("tier2_scan_support_score", "scan_support_score"),
                ("tier2_trace_scan_count", "trace_scan_count"),
                ("tier2_sn_proxy", "trace_signal_to_noise_proxy"),
                ("tier2_apex_prominence_score", "trace_apex_prominence_score"),
            ),
        )
    if candidate_ms2_row:
        _append_row_metrics(
            metrics,
            candidate_ms2_row,
            (
                ("candidate_ms2_pattern_status", "candidate_ms2_pattern_status"),
                ("candidate_ms2_evidence_level", "candidate_ms2_evidence_level"),
                ("candidate_ms2_similarity_score", "candidate_ms2_similarity_score"),
                ("candidate_ms2_matched_product_count", "matched_product_count"),
                (
                    "candidate_ms2_matched_neutral_loss_count",
                    "matched_neutral_loss_count",
                ),
                ("candidate_ms2_apex_delta_sec", "apex_ms2_delta_sec"),
                ("candidate_ms2_alignment_source", "ms2_alignment_source"),
                (
                    "candidate_ms2_source_neutral_loss_mass_error_ppm",
                    "source_neutral_loss_mass_error_ppm",
                ),
                ("candidate_ms2_raw_best_loss_ppm", "raw_ms2_best_loss_ppm"),
                (
                    "candidate_ms2_raw_trigger_scan_count",
                    "raw_ms2_trigger_scan_count",
                ),
                (
                    "candidate_ms2_raw_strict_nl_scan_count",
                    "raw_ms2_strict_nl_scan_count",
                ),
                ("candidate_ms2_raw_trace_strength", "raw_ms2_trace_strength"),
                (
                    "candidate_ms2_raw_absence_reason",
                    "raw_ms2_diagnostic_product_absence_reason",
                ),
                ("candidate_ms2_nl_ppm_warn", "nl_ppm_warn"),
                ("candidate_ms2_nl_ppm_max", "nl_ppm_max"),
            ),
        )
    if _has_family_ms2_required_tag(family_ms2_required_tag_row):
        assert family_ms2_required_tag_row is not None
        metrics.append("family_ms2_required_tag_status=observed_in_family")
        _append_row_metrics(
            metrics,
            family_ms2_required_tag_row,
            (
                ("family_ms2_required_tag_sample", "sample_stem"),
                (
                    "family_ms2_required_tag_strict_nl_count",
                    "raw_ms2_strict_nl_scan_count",
                ),
            ),
        )
    if ms1_pattern_row:
        _append_row_metrics(
            metrics,
            ms1_pattern_row,
            (
                ("ms1_pattern_status", "ms1_pattern_status"),
                ("ms1_pattern_evidence_level", "ms1_pattern_evidence_level"),
                ("ms1_apex_coherence_sec", "apex_coherence_sec"),
                ("ms1_boundary_overlap_score", "boundary_overlap_score"),
                ("ms1_shape_correlation_score", "shape_correlation_score"),
                ("ms1_local_interference_score", "local_interference_score"),
                ("ms1_drift_compatible_status", "drift_compatible_status"),
                ("ms1_shape_metric_source", "shape_metric_source"),
                ("ms1_overlay_verdict", "family_ms1_overlay_verdict"),
                ("ms1_cell_height", "cell_height"),
                ("ms1_local_window_max_intensity", "local_window_max_intensity"),
                ("ms1_trace_max_intensity", "trace_max_intensity"),
            ),
        )
        if _has_ms1_intensity_opportunity_metric(ms1_pattern_row):
            metrics.append(
                "ms1_intensity_opportunity_status=supported_by_raw_overlay_height"
            )
        _append_row_metrics(
            metrics,
            ms1_pattern_row,
            (
                (
                    "ms1_cell_to_local_window_max_ratio",
                    "cell_to_local_window_max_ratio",
                ),
                (
                    "ms1_local_window_to_global_max_ratio",
                    "local_window_to_global_max_ratio",
                ),
                ("ms1_local_window_apex_delta_sec", "local_window_apex_delta_sec"),
                (
                    "ms1_global_trace_apex_delta_sec",
                    "global_trace_apex_delta_sec",
                ),
                ("ms1_peak_quality_vector_status", "peak_quality_vector_status"),
                ("ms1_peak_quality_vector_basis", "peak_quality_vector_basis"),
                (
                    "ms1_peak_quality_trace_point_count",
                    "peak_quality_trace_point_count",
                ),
                (
                    "ms1_peak_quality_boundary_point_count",
                    "peak_quality_boundary_point_count",
                ),
                (
                    "ms1_peak_quality_signal_to_noise_proxy",
                    "peak_quality_signal_to_noise_proxy",
                ),
                ("ms1_peak_quality_fwhm_sec", "peak_quality_fwhm_sec"),
                (
                    "ms1_peak_quality_sharpness_score",
                    "peak_quality_sharpness_score",
                ),
                ("ms1_peak_quality_zigzag_score", "peak_quality_zigzag_score"),
                ("ms1_peak_quality_tailing_ratio", "peak_quality_tailing_ratio"),
                (
                    "ms1_peak_quality_boundary_margin_ratio",
                    "peak_quality_boundary_margin_ratio",
                ),
                ("ms1_peak_quality_feature_count", "peak_quality_feature_count"),
                ("ms1_peak_quality_vector_reason", "peak_quality_vector_reason"),
                ("ms1_pattern_reason", "reason"),
            ),
        )
    if qc_ms1_reference_row:
        _append_row_metrics(
            metrics,
            qc_ms1_reference_row,
            (
                ("qc_ms1_reference_status", "qc_reference_status"),
                ("qc_ms1_reference_evidence_level", "qc_reference_evidence_level"),
                ("qc_ms1_reference_policy", "qc_reference_policy"),
                ("qc_ms1_reference_local_status", "local_qc_reference_status"),
                ("qc_ms1_reference_consensus_status", "qc_consensus_status"),
                ("qc_ms1_reference_conflict_status", "qc_reference_conflict_status"),
                ("qc_ms1_reference_sample", "nearest_qc_sample_stem"),
                (
                    "qc_ms1_reference_injection_order_delta",
                    "nearest_qc_injection_order_delta",
                ),
                (
                    "qc_ms1_reference_apex_abs_delta_sec",
                    "target_qc_apex_abs_delta_sec",
                ),
                (
                    "qc_ms1_reference_shape_similarity",
                    "target_qc_shape_similarity",
                ),
                (
                    "qc_ms1_reference_target_local_ratio",
                    "target_local_window_to_global_max_ratio",
                ),
                (
                    "qc_ms1_reference_qc_local_ratio",
                    "nearest_qc_local_window_to_global_max_ratio",
                ),
            ),
        )
    if matrix_rt_drift_row:
        _append_row_metrics(
            metrics,
            matrix_rt_drift_row,
            (
                ("matrix_rt_drift_status", "matrix_rt_drift_status"),
                ("drift_evidence_level", "drift_evidence_level"),
                ("raw_rt_delta_sec", "raw_rt_delta_sec"),
                ("drift_corrected_delta_sec", "drift_corrected_delta_sec"),
                ("matrix_shift_sec", "matrix_shift_sec"),
                ("drift_reference_count", "drift_reference_count"),
                ("drift_compatible_status", "drift_compatible_status"),
                ("drift_reference_source", "drift_reference_source"),
                ("drift_reference_artifacts", "drift_reference_artifacts"),
                (
                    "istd_trend_injection_order_span",
                    "istd_trend_injection_order_span",
                ),
                ("istd_phase_summary", "istd_phase_summary"),
            ),
        )
    if sample_negative_row:
        _append_row_metrics(
            metrics,
            sample_negative_row,
            (
                ("sample_negative_evidence_class", "negative_evidence_class"),
                ("sample_negative_evidence_level", "negative_evidence_level"),
                ("sample_negative_evidence_detail", "negative_evidence_detail"),
                ("sample_negative_evidence_reason", "reason"),
            ),
        )
    if rt_mode_row:
        _append_row_metrics(
            metrics,
            rt_mode_row,
            (
                ("rt_mode_status", "rt_mode_status"),
                ("rt_mode_evidence_level", "rt_mode_evidence_level"),
                ("selected_mode_id", "selected_mode_id"),
                ("selected_mode_role", "selected_mode_role"),
                ("selected_mode_tag_status", "selected_mode_tag_status"),
                ("family_mode_class", "family_mode_class"),
                ("family_mode_count", "family_mode_count"),
                ("tag_bearing_mode_count", "tag_bearing_mode_count"),
                ("selected_mode_cell_count", "selected_mode_cell_count"),
                ("selected_mode_raw_rt_range_min", "selected_mode_raw_rt_range_min"),
                (
                    "selected_mode_normalized_rt_range_min",
                    "selected_mode_normalized_rt_range_min",
                ),
                ("family_raw_rt_range_min", "family_raw_rt_range_min"),
                (
                    "family_normalized_rt_range_min",
                    "family_normalized_rt_range_min",
                ),
                ("rt_mode_reason", "reason"),
            ),
        )
    if peak_hypothesis_row:
        _append_metric(
            metrics,
            "peak_hypothesis_authority_source",
            _peak_hypothesis_authority_source(peak_hypothesis_row),
        )
        _append_row_metrics(
            metrics,
            peak_hypothesis_row,
            (
                ("peak_hypothesis_status", "peak_hypothesis_status"),
                ("peak_hypothesis_id", "peak_hypothesis_id"),
                ("product_unit_scope", "product_unit_scope"),
                ("product_selection_action", "product_selection_action"),
                ("product_selection_blocker", "product_selection_blocker"),
                ("peak_hypothesis_reason", "reason"),
            ),
        )
    dda_status = _dda_non_dispositive_policy_status(
        tags,
        context_matches,
        candidate_ms2_row,
        family_ms2_required_tag_row,
        ms1_pattern_row,
        qc_ms1_reference_row,
    )
    if dda_status != "not_applicable":
        _append_metric(metrics, "dda_missing_nl_policy_status", dda_status)
    return ";".join(metrics)


def _manual_derived_facts(
    tags: frozenset[str],
    explanation: Mapping[str, str],
) -> str:
    facts = sorted(
        tag
        for tag in tags
        if tag
        in (
            _RT_TAGS
            | _SHAPE_TAGS
            | _PATTERN_TAGS
            | _OPPORTUNITY_TAGS
            | {"scope_derived_unmentioned_fail", "human_unjudgeable"}
        )
    )
    if explanation.get("manual_label") == "human_unjudgeable":
        facts.append("manual_label:human_unjudgeable")
    return ";".join(_unique(facts))


def _missing_machine_evidence(
    *,
    explanation: Mapping[str, str],
    tags: frozenset[str],
    sample_matches: Sequence[MachineMatch],
    context_matches: Sequence[MachineMatch],
    cwt_row: Mapping[str, str] | None,
    tier2_row: Mapping[str, str] | None,
    candidate_ms2_row: Mapping[str, str] | None,
    family_ms2_required_tag_row: Mapping[str, str] | None,
    ms1_pattern_row: Mapping[str, str] | None,
    qc_ms1_reference_row: Mapping[str, str] | None,
    matrix_rt_drift_row: Mapping[str, str] | None,
    sample_negative_row: Mapping[str, str] | None,
    rt_mode_row: Mapping[str, str] | None,
    peak_hypothesis_row: Mapping[str, str] | None,
) -> tuple[str, ...]:
    missing: list[str] = []
    gap_class = explanation.get("evidence_gap_class", "")
    if (
        tags & _SHAPE_TAGS
        and not _has_cwt_metric(cwt_row)
        and not _has_ms1_shape_metric(ms1_pattern_row)
    ):
        missing.append("formal_shape_metric")
    if _cwt_conflicts_with_manual(tags, cwt_row):
        missing.append("shape_metric_not_supportive")
    if _ms1_shape_conflicts_with_manual(tags, ms1_pattern_row):
        missing.append("shape_metric_not_supportive")
    if _ms1_shape_inconclusive_with_manual(tags, ms1_pattern_row):
        missing.append("shape_metric_inconclusive_apex_or_height")
    if (
        tags & _PATTERN_TAGS
        and not _has_candidate_ms2_pattern_metric(candidate_ms2_row)
        and not _has_ms1_pattern_metric(ms1_pattern_row)
        and not _has_qc_ms1_pattern_reference_metric(qc_ms1_reference_row)
    ):
        missing.append("formal_pattern_metric")
    if tags & _OPPORTUNITY_TAGS:
        if not (
            _has_tier2_trace_metric(tier2_row)
            or _has_ms1_intensity_opportunity_metric(ms1_pattern_row)
        ):
            missing.append("intensity_opportunity_metric")
        dda_status = _dda_non_dispositive_policy_status(
            tags,
            context_matches,
            candidate_ms2_row,
            family_ms2_required_tag_row,
            ms1_pattern_row,
            qc_ms1_reference_row,
        )
        if dda_status == "family_required_tag_not_observed":
            missing.append("family_required_tag_gate")
        elif dda_status == "policy_evidence_missing":
            missing.append("dda_opportunity_policy")
    if "rt_drift_possible" in tags and not _matrix_rt_drift_supports_manual(
        tags,
        matrix_rt_drift_row,
    ):
        missing.append("matrix_rt_drift_policy")
    if (
        "rt_too_far" in tags
        and not _has_rt_preferred_window_conflict(sample_matches)
        and not _rt_mode_supports_manual(explanation, tags, rt_mode_row)
        and not _ms1_pattern_supports_manual(tags, ms1_pattern_row)
        and not _qc_ms1_pattern_reference_conflicts_with_manual(
            tags,
            qc_ms1_reference_row,
        )
    ):
        missing.append("rt_pattern_conflict_gate")
    if _candidate_ms2_conflicts_with_manual(tags, candidate_ms2_row):
        missing.append("pattern_metric_not_supportive")
    if _ms1_pattern_conflicts_with_manual(tags, ms1_pattern_row):
        missing.append("pattern_metric_not_supportive")
    qc_reference_conflicts = _qc_ms1_pattern_reference_conflicts_with_manual(
        tags,
        qc_ms1_reference_row,
    )
    if qc_reference_conflicts and not _ms1_pattern_supports_manual(
        tags,
        ms1_pattern_row,
    ):
        missing.append("pattern_metric_not_supportive")
    if _ms1_pattern_inconclusive_with_manual(tags, ms1_pattern_row):
        missing.append("pattern_metric_inconclusive_apex_or_height")
    if _qc_ms1_pattern_reference_inconclusive_with_manual(
        tags,
        qc_ms1_reference_row,
    ):
        missing.append("qc_ms1_pattern_reference_inconclusive")
    if _matrix_rt_drift_conflicts_with_manual(tags, matrix_rt_drift_row):
        missing.append("matrix_rt_drift_policy_not_supportive")
    if _rt_mode_conflicts_with_manual(explanation, tags, rt_mode_row):
        missing.append("rt_mode_not_supportive")
    if _peak_hypothesis_conflicts_with_manual(
        explanation,
        tags,
        peak_hypothesis_row,
    ):
        missing.append("peak_hypothesis_not_supportive")
    if (
        "pattern_mismatch" in tags
        and not _candidate_ms2_supports_manual(tags, candidate_ms2_row)
        and not _ms1_pattern_supports_manual(tags, ms1_pattern_row)
        and not _qc_ms1_pattern_reference_supports_manual(
            tags,
            qc_ms1_reference_row,
        )
        and not _has_candidate_ms2_pattern_metric(candidate_ms2_row)
        and not _has_ms1_pattern_metric(ms1_pattern_row)
        and not _has_qc_ms1_pattern_reference_metric(qc_ms1_reference_row)
    ):
        missing.append("candidate_aligned_ms2_pattern")
    if (
        explanation.get("manual_scope") == "scope_derived_unmentioned_fail"
        or "scope_derived_unmentioned_fail" in tags
        or gap_class == "machine_too_permissive_scope_rule_conflict"
    ) and not _sample_negative_evidence_supports_manual(
        explanation,
        tags,
        sample_negative_row,
    ):
        missing.extend(("manual_scope_policy", "sample_level_negative_evidence"))
    if explanation.get("manual_label") == "human_unjudgeable":
        missing.append("human_review_or_retire_from_training")
    if "delta_mass_related" in tags:
        missing.append("delta_mass_family_model")
    if (
        not sample_matches
        and explanation.get("manual_label")
        not in {
            "not_applicable",
            "human_unjudgeable",
        }
        and not _sample_negative_evidence_supports_manual(
            explanation,
            tags,
            sample_negative_row,
        )
    ):
        missing.append("sample_level_machine_observation")
    if (
        not context_matches
        and tags & _PATTERN_TAGS
        and not _has_candidate_ms2_pattern_metric(candidate_ms2_row)
        and not _has_ms1_pattern_metric(ms1_pattern_row)
        and not _has_qc_ms1_pattern_reference_metric(qc_ms1_reference_row)
    ):
        missing.append("family_ms2_pattern_context")
    return _unique(missing)


def _literature_refs(
    *,
    tags: frozenset[str],
    missing_evidence: Sequence[str],
    explanation: Mapping[str, str],
) -> tuple[str, ...]:
    refs: list[str] = ["sumner_2007_msi"]
    if tags & _SHAPE_TAGS or "formal_shape_metric" in missing_evidence:
        refs.extend(
            (
                "scipy_signal_find_peaks_cwt_docs",
                "tautenhahn_2008_centwave",
                "zhang_2014_eic_quality",
                "kumler_2023_peak_quality",
            )
        )
    if tags & _PATTERN_TAGS or "candidate_aligned_ms2_pattern" in missing_evidence:
        refs.extend(
            (
                "neutral_loss_product_ion_annotation",
                "watrous_2012_gnps_molecular_networking",
                "huber_2021_spec2vec",
                "biesinger_2022_spectral_alignment",
            )
        )
    if tags & _OPPORTUNITY_TAGS or "dda_opportunity_policy" in missing_evidence:
        refs.extend(("koelmel_2017_iterative_exclusion", "tsugawa_2017_ts_dda"))
    if tags & _RT_TAGS or "matrix_rt_drift_policy" in missing_evidence:
        refs.extend(("prince_2006_obiwarp", "gika_2010_nonlinear_rt_alignment"))
    if (
        explanation.get("manual_scope") == "scope_derived_unmentioned_fail"
        or "sample_level_negative_evidence" in missing_evidence
    ):
        refs.append("sumner_2007_msi")
    return _unique(refs)


def _evidence_support_status(
    *,
    manual_label: str,
    sample_id: str,
    missing_evidence: Sequence[str],
    observed_metrics: str,
    manual_facts: str,
    has_machine_observed_metric: bool,
) -> str:
    if manual_label == "not_applicable" or sample_id == "__family_context__":
        return "context_only"
    if manual_label == "human_unjudgeable":
        return "not_evaluable"
    if "shape_metric_not_supportive" in missing_evidence:
        return "machine_observed_conflict"
    if "pattern_metric_not_supportive" in missing_evidence:
        return "machine_observed_conflict"
    if "matrix_rt_drift_policy_not_supportive" in missing_evidence:
        return "machine_observed_conflict"
    if "family_required_tag_gate" in missing_evidence:
        return "machine_observed_conflict"
    if "rt_mode_not_supportive" in missing_evidence:
        return "machine_observed_conflict"
    if "peak_hypothesis_not_supportive" in missing_evidence:
        return "machine_observed_conflict"
    if "manual_scope_policy" in missing_evidence:
        return "blocked_missing_metric"
    if missing_evidence:
        if has_machine_observed_metric:
            return "machine_observed_partial"
        return "machine_proxy_only" if observed_metrics else "manual_derived_only"
    if observed_metrics and has_machine_observed_metric:
        return "machine_observed_sufficient"
    if observed_metrics:
        return (
            "machine_observed_partial"
            if has_machine_observed_metric
            else "machine_proxy_only"
        )
    return "manual_derived_only"


def _machine_tokens(
    sample_matches: Sequence[MachineMatch],
    context_matches: Sequence[MachineMatch],
) -> frozenset[str]:
    tokens: set[str] = set()
    for match in (*sample_matches, *context_matches):
        tokens.update(_split_semicolon(match.machine_reason.replace(" ", "_")))
        tokens.update(match.machine_blockers)
        for field in ("reason", "challenge_blockers", "dependent_context", "row_flags"):
            tokens.update(_split_semicolon(match.row.get(field, "")))
    return frozenset(token.lower() for token in tokens)


def _tags(row: Mapping[str, str]) -> frozenset[str]:
    return frozenset(_split_semicolon(row.get("manual_reason_tags", "")))


def _split_semicolon(value: object) -> tuple[str, ...]:
    return tuple(part.strip() for part in str(value or "").split(";") if part.strip())


def _unique(values: Sequence[str]) -> tuple[str, ...]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        if value and value not in seen:
            result.append(value)
            seen.add(value)
    return tuple(result)


def _append_metric(metrics: list[str], name: str, value: object) -> None:
    if _has_value(value):
        metrics.append(f"{name}={value}")


def _peak_hypothesis_authority_source(row: Mapping[str, str]) -> str:
    reason = str(row.get("reason") or "")
    status = row.get("peak_hypothesis_status")
    action = row.get("product_selection_action")
    if reason.startswith("typed_mode_hypothesis_assignment_"):
        return "typed_mode_hypothesis_assignment"
    if status == "raw_mode_review_only" or action == "require_raw_mode_review":
        return "raw_or_overlay_review_only"
    return "legacy_rt_mode_selection"


def _has_value(value: object) -> bool:
    return str(value or "").strip() not in {"", "nan", "None"}


def _float_or_none(value: object) -> float | None:
    text = str(value or "").strip().strip("'")
    if not text:
        return None
    try:
        return float(text)
    except ValueError:
        return None


def _int_or_none(value: object) -> int | None:
    number = _float_or_none(value)
    if number is None:
        return None
    return int(number)


def _peak_width_sec(row: Mapping[str, str]) -> str:
    try:
        start = float(str(row.get("peak_start_rt", "")).strip("'"))
        end = float(str(row.get("peak_end_rt", "")).strip("'"))
    except ValueError:
        return ""
    width = max(0.0, (end - start) * 60.0)
    return f"{width:.3f}"


def _has_rt_preferred_window_conflict(
    sample_matches: Sequence[MachineMatch],
) -> bool:
    return any(
        (rt_delta_sec := _float_or_none(match.row.get("rt_delta_sec"))) is not None
        and abs(rt_delta_sec) > _DEFAULT_ALIGNMENT_CONFIG.preferred_rt_sec
        for match in sample_matches
    )


def _has_cwt_metric(cwt_row: Mapping[str, str] | None) -> bool:
    if not cwt_row:
        return False
    return _has_value(cwt_row.get("cwt_status")) and cwt_row.get("cwt_status") not in {
        "not_assessed",
        "unavailable",
    }


def _has_tier2_trace_metric(tier2_row: Mapping[str, str] | None) -> bool:
    if not tier2_row:
        return False
    return any(
        _has_value(tier2_row.get(field))
        for field in (
            "scan_support_score",
            "trace_scan_count",
            "scan_availability_score",
            "trace_signal_to_noise_proxy",
            "trace_apex_prominence_score",
        )
    )


def _has_ms1_intensity_opportunity_metric(
    ms1_pattern_row: Mapping[str, str] | None,
) -> bool:
    intensity = _ms1_supporting_intensity(ms1_pattern_row)
    if intensity is None or intensity < _DDA_NON_DISPOSITIVE_MS1_INTENSITY_MIN:
        return False
    if not ms1_pattern_row:
        return False
    return (
        ms1_pattern_row.get("shape_metric_source") == "family_ms1_overlay_raw_trace"
        or ms1_pattern_row.get("peak_quality_vector_basis")
        == _MS1_PEAK_QUALITY_VECTOR_BASIS
        or ms1_pattern_row.get("ms1_pattern_evidence_level")
        in _MS1_PATTERN_OBSERVED_LEVELS
    )


def _has_cid_nl_pattern_context(context_matches: Sequence[MachineMatch]) -> bool:
    return any(
        match.evidence_source == "alignment_review"
        and _has_value(match.row.get("neutral_loss_tag"))
        and _has_value(match.row.get("family_product_mz"))
        and _has_value(match.row.get("family_observed_neutral_loss_da"))
        for match in context_matches
    )


def _has_machine_observed_metric(
    *,
    cwt_row: Mapping[str, str] | None,
    tier2_row: Mapping[str, str] | None,
    candidate_ms2_row: Mapping[str, str] | None,
    ms1_pattern_row: Mapping[str, str] | None,
    qc_ms1_reference_row: Mapping[str, str] | None,
    matrix_rt_drift_row: Mapping[str, str] | None,
    sample_negative_row: Mapping[str, str] | None,
    rt_mode_row: Mapping[str, str] | None,
    peak_hypothesis_row: Mapping[str, str] | None,
) -> bool:
    return (
        _has_cwt_metric(cwt_row)
        or _has_tier2_trace_metric(tier2_row)
        or _has_candidate_ms2_pattern_metric(candidate_ms2_row)
        or _has_ms1_shape_metric(ms1_pattern_row)
        or _has_ms1_pattern_metric(ms1_pattern_row)
        or _has_qc_ms1_pattern_reference_metric(qc_ms1_reference_row)
        or _has_matrix_rt_drift_metric(matrix_rt_drift_row)
        or _has_sample_negative_evidence_metric(sample_negative_row)
        or _has_rt_mode_metric(rt_mode_row)
        or _has_peak_hypothesis_metric(peak_hypothesis_row)
    )


def _cwt_conflicts_with_manual(
    tags: frozenset[str],
    cwt_row: Mapping[str, str] | None,
) -> bool:
    if not _has_cwt_metric(cwt_row):
        return False
    if cwt_row is None:
        return False
    cwt_supports_shape = cwt_row.get("cwt_status") == "OK"
    if tags & {"shape_complete", "shape_normal"}:
        return not cwt_supports_shape
    if "shape_bad" in tags:
        return cwt_supports_shape
    return False


def _has_candidate_ms2_pattern_metric(row: Mapping[str, str] | None) -> bool:
    if not row:
        return False
    return row.get(
        "candidate_ms2_evidence_level"
    ) in _CANDIDATE_MS2_OBSERVED_LEVELS and row.get("candidate_ms2_pattern_status") in (
        _CANDIDATE_MS2_SUPPORT_STATUSES | _CANDIDATE_MS2_CONFLICT_STATUSES
    )


def _has_ms1_pattern_metric(row: Mapping[str, str] | None) -> bool:
    if not row:
        return False
    return row.get(
        "ms1_pattern_evidence_level"
    ) in _MS1_PATTERN_OBSERVED_LEVELS and row.get("ms1_pattern_status") in (
        _MS1_PATTERN_SUPPORT_STATUSES | _MS1_PATTERN_CONFLICT_STATUSES
    )


def _has_qc_ms1_pattern_reference_metric(row: Mapping[str, str] | None) -> bool:
    if not row:
        return False
    return row.get(
        "qc_reference_evidence_level"
    ) in _QC_MS1_REFERENCE_OBSERVED_LEVELS and row.get("qc_reference_status") in (
        _QC_MS1_REFERENCE_SUPPORT_STATUSES | _QC_MS1_REFERENCE_CONFLICT_STATUSES
    )


def _has_ms1_shape_metric(row: Mapping[str, str] | None) -> bool:
    if not row:
        return False
    return (
        _has_ms1_pattern_metric(row)
        and row.get("ms1_pattern_evidence_level") == "trace_constellation"
        and row.get("shape_metric_source") == "family_ms1_overlay_raw_trace"
        and _has_value(row.get("family_ms1_overlay_trace_data_json"))
        and _has_value(row.get("shape_correlation_score"))
        and _has_ms1_peak_quality_vector_metric(row)
    )


def _has_ms1_peak_quality_vector_metric(row: Mapping[str, str] | None) -> bool:
    if not row:
        return False
    return (
        row.get("peak_quality_vector_basis") == _MS1_PEAK_QUALITY_VECTOR_BASIS
        and row.get("peak_quality_vector_status")
        in _MS1_PEAK_QUALITY_VECTOR_OBSERVED_STATUSES
    )


def _has_matrix_rt_drift_metric(row: Mapping[str, str] | None) -> bool:
    if not row:
        return False
    return row.get(
        "drift_evidence_level"
    ) in _MATRIX_RT_DRIFT_OBSERVED_LEVELS and row.get("matrix_rt_drift_status") in (
        _MATRIX_RT_DRIFT_SUPPORT_STATUSES | _MATRIX_RT_DRIFT_CONFLICT_STATUSES
    )


def _has_sample_negative_evidence_metric(row: Mapping[str, str] | None) -> bool:
    if not row:
        return False
    return (
        row.get("negative_evidence_level") in _SAMPLE_NEGATIVE_EVIDENCE_OBSERVED_LEVELS
        and row.get("negative_evidence_class") in _SAMPLE_NEGATIVE_EVIDENCE_CLASSES
    )


def _has_rt_mode_metric(row: Mapping[str, str] | None) -> bool:
    if not row:
        return False
    return row.get("rt_mode_status") in _RT_MODE_OBSERVED_STATUSES


def _has_peak_hypothesis_metric(row: Mapping[str, str] | None) -> bool:
    if not row:
        return False
    return row.get("peak_hypothesis_status") in _PEAK_HYPOTHESIS_OBSERVED_STATUSES


def _rt_mode_supports_manual(
    explanation: Mapping[str, str],
    tags: frozenset[str],
    row: Mapping[str, str] | None,
) -> bool:
    if not _has_rt_mode_metric(row):
        return False
    if row is None:
        return False
    status = row.get("rt_mode_status")
    if explanation.get("manual_label") == "fail":
        return status in _RT_MODE_CONFLICT_STATUSES
    if tags & {"rt_drift_possible", "rt_close", "pattern_similar", "shape_complete"}:
        return status in _RT_MODE_SUPPORT_STATUSES
    return False


def _rt_mode_conflicts_with_manual(
    explanation: Mapping[str, str],
    tags: frozenset[str],
    row: Mapping[str, str] | None,
) -> bool:
    if not _has_rt_mode_metric(row):
        return False
    if row is None:
        return False
    status = row.get("rt_mode_status")
    if explanation.get("manual_label") in {"pass", "suspect"}:
        return status in _RT_MODE_CONFLICT_STATUSES
    if explanation.get("manual_label") == "fail":
        return status in _RT_MODE_SUPPORT_STATUSES and "rt_too_far" in tags
    return False


def _peak_hypothesis_conflicts_with_manual(
    explanation: Mapping[str, str],
    tags: frozenset[str],
    row: Mapping[str, str] | None,
) -> bool:
    if not _has_peak_hypothesis_metric(row):
        return False
    if row is None:
        return False
    status = row.get("peak_hypothesis_status")
    if explanation.get("manual_label") in {"pass", "suspect"}:
        return status in _PEAK_HYPOTHESIS_CONFLICT_STATUSES
    if explanation.get("manual_label") == "fail":
        return status in _PEAK_HYPOTHESIS_SUPPORT_STATUSES and "rt_too_far" in tags
    return False


def _sample_negative_evidence_supports_manual(
    explanation: Mapping[str, str],
    tags: frozenset[str],
    row: Mapping[str, str] | None,
) -> bool:
    if explanation.get("manual_label") != "fail":
        return False
    if not _has_sample_negative_evidence_metric(row):
        return False
    if row is None:
        return False
    negative_class = row["negative_evidence_class"]
    if (
        explanation.get("manual_scope") == "scope_derived_unmentioned_fail"
        or "scope_derived_unmentioned_fail" in tags
        or explanation.get("evidence_gap_class")
        == "machine_too_permissive_scope_rule_conflict"
    ):
        return True
    if "pattern_mismatch" in tags:
        return negative_class == "pattern_mismatch"
    if "rt_too_far" in tags:
        return negative_class == "rt_not_explained"
    if tags & {"shape_bad", "boundary_ambiguous"}:
        return negative_class == "local_peak_not_decisive"
    return negative_class == "no_candidate_ms1_evidence"


def _dda_non_dispositive_policy_status(
    tags: frozenset[str],
    context_matches: Sequence[MachineMatch],
    candidate_ms2_row: Mapping[str, str] | None,
    family_ms2_required_tag_row: Mapping[str, str] | None,
    ms1_pattern_row: Mapping[str, str] | None,
    qc_ms1_reference_row: Mapping[str, str] | None,
) -> str:
    if "dda_stochastic_missing" not in tags:
        return "not_applicable"
    if _has_family_ms2_required_tag(candidate_ms2_row):
        return "sample_required_tag_observed"
    if _dda_non_dispositive_policy_supports_manual(
        tags,
        context_matches,
        candidate_ms2_row,
        family_ms2_required_tag_row,
        ms1_pattern_row,
        qc_ms1_reference_row,
    ):
        return "not_dispositive"
    if (
        candidate_ms2_row
        and not _has_family_required_tag_or_context(
            family_ms2_required_tag_row,
            context_matches,
        )
        and candidate_ms2_row.get("candidate_ms2_evidence_level") != "not_available"
    ):
        return "family_required_tag_not_observed"
    return "policy_evidence_missing"


def _dda_non_dispositive_policy_supports_manual(
    tags: frozenset[str],
    context_matches: Sequence[MachineMatch],
    candidate_ms2_row: Mapping[str, str] | None,
    family_ms2_required_tag_row: Mapping[str, str] | None,
    ms1_pattern_row: Mapping[str, str] | None,
    qc_ms1_reference_row: Mapping[str, str] | None,
) -> bool:
    if "dda_stochastic_missing" not in tags:
        return False
    if not candidate_ms2_row:
        return False
    if not _has_family_required_tag_or_context(
        family_ms2_required_tag_row,
        context_matches,
    ):
        return False
    if candidate_ms2_row.get("candidate_ms2_pattern_status") != "not_observed":
        return False
    if (
        candidate_ms2_row.get("candidate_ms2_evidence_level")
        != "sample_boundary_no_observed_pattern"
    ):
        return False
    if _int_or_none(candidate_ms2_row.get("raw_ms2_trigger_scan_count")) is None:
        return False
    trigger_count = _int_or_none(candidate_ms2_row.get("raw_ms2_trigger_scan_count"))
    if trigger_count is None or trigger_count < _DDA_NON_DISPOSITIVE_TRIGGER_SCAN_MIN:
        return False
    strict_nl_count = _int_or_none(
        candidate_ms2_row.get("raw_ms2_strict_nl_scan_count")
    )
    if strict_nl_count not in {0, None}:
        return False
    if (
        candidate_ms2_row.get("raw_ms2_trace_strength")
        not in _DDA_NON_DISPOSITIVE_TRACE_STRENGTHS
        and candidate_ms2_row.get("raw_ms2_diagnostic_product_absence_reason")
        != "product_outside_diagnostic_window"
    ):
        return False
    if not (
        _ms1_pattern_supports_manual(tags, ms1_pattern_row)
        or _qc_ms1_pattern_reference_supports_manual(tags, qc_ms1_reference_row)
    ):
        return False
    intensity = _ms1_supporting_intensity(ms1_pattern_row)
    return intensity is not None and intensity >= _DDA_NON_DISPOSITIVE_MS1_INTENSITY_MIN


def _has_family_required_tag_or_context(
    family_ms2_required_tag_row: Mapping[str, str] | None,
    context_matches: Sequence[MachineMatch],
) -> bool:
    return _has_family_ms2_required_tag(
        family_ms2_required_tag_row,
    ) or _has_cid_nl_pattern_context(context_matches)


def _ms1_supporting_intensity(row: Mapping[str, str] | None) -> float | None:
    if not row:
        return None
    values = [
        _float_or_none(row.get(field))
        for field in (
            "cell_height",
            "local_window_max_intensity",
            "trace_max_intensity",
        )
    ]
    finite_values = [value for value in values if value is not None]
    return max(finite_values) if finite_values else None


def _candidate_ms2_supports_manual(
    tags: frozenset[str],
    row: Mapping[str, str] | None,
) -> bool:
    if not _has_candidate_ms2_pattern_metric(row):
        return False
    if row is None:
        return False
    status = row["candidate_ms2_pattern_status"]
    if "pattern_mismatch" in tags:
        return status in _CANDIDATE_MS2_CONFLICT_STATUSES
    if tags & {"pattern_similar", "pattern_partial"}:
        return status in _CANDIDATE_MS2_SUPPORT_STATUSES
    return False


def _ms1_pattern_supports_manual(
    tags: frozenset[str],
    row: Mapping[str, str] | None,
) -> bool:
    if not _has_ms1_pattern_metric(row):
        return False
    if row is None:
        return False
    if _ms1_pattern_metric_inconclusive(row):
        return False
    status = row["ms1_pattern_status"]
    if "pattern_mismatch" in tags:
        return status in _MS1_PATTERN_CONFLICT_STATUSES
    if tags & {"pattern_similar", "pattern_partial"}:
        return status in _MS1_PATTERN_SUPPORT_STATUSES
    return False


def _qc_ms1_pattern_reference_supports_manual(
    tags: frozenset[str],
    row: Mapping[str, str] | None,
) -> bool:
    if not _has_qc_ms1_pattern_reference_metric(row):
        return False
    if row is None:
        return False
    status = row["qc_reference_status"]
    if "pattern_mismatch" in tags:
        return status in _QC_MS1_REFERENCE_CONFLICT_STATUSES
    if tags & {"pattern_similar", "pattern_partial"}:
        return status in _QC_MS1_REFERENCE_SUPPORT_STATUSES
    return False


def _ms1_shape_supports_manual(
    tags: frozenset[str],
    row: Mapping[str, str] | None,
) -> bool:
    if not _has_ms1_shape_metric(row):
        return False
    if row is None:
        return False
    status = row["ms1_pattern_status"]
    shape_score = _float_or_none(row.get("shape_correlation_score"))
    if tags & {"shape_complete", "shape_normal"}:
        if _ms1_conflict_preserves_selected_peak_shape(row):
            return True
        return (
            status in _MS1_PATTERN_SUPPORT_STATUSES
            and shape_score is not None
            and shape_score >= _MS1_SHAPE_SUPPORT_MIN
        )
    if "shape_bad" in tags:
        return status in _MS1_PATTERN_CONFLICT_STATUSES or (
            shape_score is not None and shape_score < _MS1_SHAPE_SUPPORT_MIN
        )
    return False


def _matrix_rt_drift_supports_manual(
    tags: frozenset[str],
    row: Mapping[str, str] | None,
) -> bool:
    if not _has_matrix_rt_drift_metric(row):
        return False
    if row is None:
        return False
    status = row["matrix_rt_drift_status"]
    if "rt_drift_possible" in tags:
        return (
            status in {"drift_supported", "rt_close"}
            and row.get("drift_compatible_status") == "compatible"
        )
    if "rt_close" in tags:
        return status == "rt_close"
    if "rt_too_far" in tags:
        return status in _MATRIX_RT_DRIFT_CONFLICT_STATUSES
    return False


def _candidate_ms2_conflicts_with_manual(
    tags: frozenset[str],
    row: Mapping[str, str] | None,
) -> bool:
    if not _has_candidate_ms2_pattern_metric(row):
        return False
    if row is None:
        return False
    status = row["candidate_ms2_pattern_status"]
    if "pattern_mismatch" in tags:
        return status in _CANDIDATE_MS2_SUPPORT_STATUSES
    if tags & {"pattern_similar", "pattern_partial"}:
        return status in _CANDIDATE_MS2_CONFLICT_STATUSES
    return False


def _ms1_pattern_conflicts_with_manual(
    tags: frozenset[str],
    row: Mapping[str, str] | None,
) -> bool:
    if not _has_ms1_pattern_metric(row):
        return False
    if row is None:
        return False
    if _ms1_pattern_metric_inconclusive(row):
        return False
    status = row["ms1_pattern_status"]
    if "pattern_mismatch" in tags:
        return status in _MS1_PATTERN_SUPPORT_STATUSES
    if tags & {"pattern_similar", "pattern_partial"}:
        return status in _MS1_PATTERN_CONFLICT_STATUSES
    return False


def _qc_ms1_pattern_reference_conflicts_with_manual(
    tags: frozenset[str],
    row: Mapping[str, str] | None,
) -> bool:
    if not _has_qc_ms1_pattern_reference_metric(row):
        return False
    if row is None:
        return False
    status = row["qc_reference_status"]
    if "pattern_mismatch" in tags:
        return status in _QC_MS1_REFERENCE_SUPPORT_STATUSES
    if tags & {"pattern_similar", "pattern_partial"}:
        return status in _QC_MS1_REFERENCE_CONFLICT_STATUSES
    return False


def _ms1_shape_conflicts_with_manual(
    tags: frozenset[str],
    row: Mapping[str, str] | None,
) -> bool:
    if not _has_ms1_shape_metric(row):
        return False
    if row is None:
        return False
    status = row["ms1_pattern_status"]
    shape_score = _float_or_none(row.get("shape_correlation_score"))
    if tags & {"shape_complete", "shape_normal"}:
        if _ms1_conflict_preserves_selected_peak_shape(row):
            return False
        return status in _MS1_PATTERN_CONFLICT_STATUSES
    if "shape_bad" in tags:
        return (
            status in _MS1_PATTERN_SUPPORT_STATUSES
            and shape_score is not None
            and shape_score >= _MS1_SHAPE_SUPPORT_MIN
        )
    return False


def _ms1_conflict_preserves_selected_peak_shape(row: Mapping[str, str]) -> bool:
    return (
        row.get("ms1_pattern_status") in _MS1_PATTERN_CONFLICT_STATUSES
        and row.get("reason")
        == "family_ms1_overlay_competing_peak_matches_family_consensus"
        and (score := _float_or_none(row.get("shape_correlation_score"))) is not None
        and score >= _MS1_SHAPE_SUPPORT_MIN
    )


def _ms1_shape_inconclusive_with_manual(
    tags: frozenset[str],
    row: Mapping[str, str] | None,
) -> bool:
    return (
        bool(tags & _SHAPE_TAGS)
        and _has_ms1_shape_metric(row)
        and not _ms1_shape_supports_manual(tags, row)
        and not _ms1_shape_conflicts_with_manual(tags, row)
    )


def _ms1_pattern_inconclusive_with_manual(
    tags: frozenset[str],
    row: Mapping[str, str] | None,
) -> bool:
    return (
        bool(tags & _PATTERN_TAGS)
        and _has_ms1_pattern_metric(row)
        and not _ms1_pattern_supports_manual(tags, row)
        and not _ms1_pattern_conflicts_with_manual(tags, row)
    )


def _qc_ms1_pattern_reference_inconclusive_with_manual(
    tags: frozenset[str],
    row: Mapping[str, str] | None,
) -> bool:
    return (
        bool(tags & _PATTERN_TAGS)
        and _has_qc_ms1_pattern_reference_metric(row)
        and not _qc_ms1_pattern_reference_supports_manual(tags, row)
        and not _qc_ms1_pattern_reference_conflicts_with_manual(tags, row)
    )


def _ms1_pattern_metric_inconclusive(row: Mapping[str, str] | None) -> bool:
    if row is None:
        return False
    return bool(
        _MS1_PATTERN_INCONCLUSIVE_REASONS & set(_split_semicolon(row.get("reason", "")))
    )


def _matrix_rt_drift_conflicts_with_manual(
    tags: frozenset[str],
    row: Mapping[str, str] | None,
) -> bool:
    if not _has_matrix_rt_drift_metric(row):
        return False
    if row is None:
        return False
    status = row["matrix_rt_drift_status"]
    if "rt_drift_possible" in tags:
        return status in _MATRIX_RT_DRIFT_CONFLICT_STATUSES
    if "rt_close" in tags:
        return status in _MATRIX_RT_DRIFT_CONFLICT_STATUSES
    if "rt_too_far" in tags:
        return status == "drift_supported"
    return False
