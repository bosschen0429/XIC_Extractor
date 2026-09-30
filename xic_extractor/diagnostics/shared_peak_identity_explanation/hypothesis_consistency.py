from __future__ import annotations

from collections import Counter
from collections.abc import Iterable, Mapping, Sequence
from pathlib import Path

from xic_extractor.tabular_io import (
    read_tsv_required,
    text_value,
    write_tsv,
)

from .machine_evidence_support import (
    CANDIDATE_MS2_PATTERN_REQUIRED_COLUMNS,
    MATRIX_RT_DRIFT_POLICY_REQUIRED_COLUMNS,
    MS1_PATTERN_COHERENCE_REQUIRED_COLUMNS,
    PEAK_HYPOTHESIS_SELECTION_REQUIRED_COLUMNS,
    QC_MS1_PATTERN_REFERENCE_REQUIRED_COLUMNS,
)
from .schema import (
    HYPOTHESIS_CONSISTENCY_COLUMNS,
    HYPOTHESIS_CONSISTENCY_SCHEMA_VERSION,
    HYPOTHESIS_CONSISTENCY_SUMMARY_COLUMNS,
    HYPOTHESIS_CONSISTENCY_SUMMARY_SCHEMA_VERSION,
    validate_row_tokens,
    validate_semicolon_tokens,
)

_MS1_SUPPORT_STATUSES = frozenset({"supportive", "partial_support"})
_MS1_CONFLICT_STATUSES = frozenset({"conflict"})
_QC_MS1_SUPPORT_STATUSES = frozenset({"supportive", "partial_support"})
_QC_MS1_CONFLICT_STATUSES = frozenset({"conflict", "mixed_conflict"})
_RT_SUPPORT_STATUSES = frozenset({"rt_close", "drift_supported"})
_RT_CONFLICT_STATUSES = frozenset({"drift_not_supported"})
_CANDIDATE_MS2_SUPPORT_STATUSES = frozenset({"supportive", "partial_support"})
_CANDIDATE_MS2_CONFLICT_STATUSES = frozenset({"conflict"})
_CANDIDATE_MS2_OBSERVED_LEVELS = frozenset(
    {"sample_candidate_aligned", "sample_boundary_aligned"}
)
_DDA_NON_DISPOSITIVE_MS1_INTENSITY_MIN = 2.5e4
_DDA_NON_DISPOSITIVE_TRIGGER_SCAN_MIN = 3
_DDA_NON_DISPOSITIVE_TRACE_STRENGTHS = frozenset({"moderate", "strong"})
_BLOCKER_TOKENS = frozenset(
    {
        "peak_hypothesis_missing",
        "ms1_pattern_missing",
        "ms1_pattern_conflict",
        "qc_ms1_reference_conflict",
        "matrix_rt_drift_missing",
        "matrix_rt_drift_conflict",
        "candidate_ms2_missing",
        "candidate_ms2_conflict",
        "family_required_tag_not_observed",
        "ms2_opportunity_review",
        "cross_mode_rescue",
        "mode_split_required",
        "consolidation_no_go",
        "tailing_confounded",
        "raw_mode_review_only",
        "inconclusive_mode_evidence",
    }
)


EvidenceByKey = Mapping[tuple[str, str], Mapping[str, str]]


def load_peak_hypothesis_selection(
    path: Path | None,
) -> dict[tuple[str, str], Mapping[str, str]]:
    if path is None:
        return {}
    rows = read_tsv_required(path, PEAK_HYPOTHESIS_SELECTION_REQUIRED_COLUMNS)
    return {(row["feature_family_id"], row["sample_stem"]): row for row in rows}


def load_ms1_pattern_coherence_evidence(
    path: Path | None,
) -> dict[tuple[str, str], Mapping[str, str]]:
    if path is None:
        return {}
    rows = read_tsv_required(path, MS1_PATTERN_COHERENCE_REQUIRED_COLUMNS)
    return {(row["feature_family_id"], row["sample_stem"]): row for row in rows}


def load_qc_ms1_pattern_reference_evidence(
    path: Path | None,
) -> dict[tuple[str, str], Mapping[str, str]]:
    if path is None:
        return {}
    rows = read_tsv_required(path, QC_MS1_PATTERN_REFERENCE_REQUIRED_COLUMNS)
    return {(row["feature_family_id"], row["sample_stem"]): row for row in rows}


def load_matrix_rt_drift_policy_evidence(
    path: Path | None,
) -> dict[tuple[str, str], Mapping[str, str]]:
    if path is None:
        return {}
    rows = read_tsv_required(path, MATRIX_RT_DRIFT_POLICY_REQUIRED_COLUMNS)
    return {(row["feature_family_id"], row["sample_stem"]): row for row in rows}


def load_candidate_ms2_pattern_evidence(
    path: Path | None,
) -> dict[tuple[str, str], Mapping[str, str]]:
    if path is None:
        return {}
    rows = read_tsv_required(path, CANDIDATE_MS2_PATTERN_REQUIRED_COLUMNS)
    return {(row["feature_family_id"], row["sample_stem"]): row for row in rows}


def build_hypothesis_consistency_rows(
    *,
    peak_hypothesis_selection: EvidenceByKey,
    ms1_pattern_coherence_evidence: EvidenceByKey | None = None,
    qc_ms1_pattern_reference_evidence: EvidenceByKey | None = None,
    matrix_rt_drift_policy_evidence: EvidenceByKey | None = None,
    candidate_ms2_pattern_evidence: EvidenceByKey | None = None,
) -> tuple[dict[str, str], ...]:
    """Cross-check PeakHypothesis rows against independent evidence sidecars."""

    ms1_pattern_coherence_evidence = ms1_pattern_coherence_evidence or {}
    qc_ms1_pattern_reference_evidence = qc_ms1_pattern_reference_evidence or {}
    matrix_rt_drift_policy_evidence = matrix_rt_drift_policy_evidence or {}
    candidate_ms2_pattern_evidence = candidate_ms2_pattern_evidence or {}
    family_required_tags = _family_required_tags(candidate_ms2_pattern_evidence)
    keys = _all_keys(
        peak_hypothesis_selection,
        ms1_pattern_coherence_evidence,
        qc_ms1_pattern_reference_evidence,
        matrix_rt_drift_policy_evidence,
        candidate_ms2_pattern_evidence,
    )
    return tuple(
        _row_for_key(
            key,
            peak_hypothesis=peak_hypothesis_selection.get(key),
            ms1_pattern=ms1_pattern_coherence_evidence.get(key),
            qc_ms1_reference=qc_ms1_pattern_reference_evidence.get(key),
            matrix_rt_drift=matrix_rt_drift_policy_evidence.get(key),
            candidate_ms2=candidate_ms2_pattern_evidence.get(key),
            family_required_tag=family_required_tags.get(key[0]),
        )
        for key in keys
    )


def build_hypothesis_consistency_summary(
    rows: Sequence[Mapping[str, str]],
    *,
    scope: str = "sidecar_key_union",
) -> dict[str, str]:
    statuses = Counter(row["evidence_consistency_status"] for row in rows)
    blockers = Counter(
        blocker
        for row in rows
        for blocker in _split_semicolon(row.get("consistency_blockers", ""))
    )
    hard_blocker_count = statuses["conflict"] + statuses["split_required"]
    if hard_blocker_count:
        gate_status = "blocked"
        next_action = "inspect_conflict_or_split_peak_hypothesis"
    elif statuses["incomplete"] or statuses["review_only"] or statuses["not_available"]:
        gate_status = "review_required"
        next_action = "add_missing_sidecar_or_keep_review_only"
    else:
        gate_status = "pass"
        next_action = "no_action"
    row = {
        "hypothesis_consistency_summary_schema_version": (
            HYPOTHESIS_CONSISTENCY_SUMMARY_SCHEMA_VERSION
        ),
        "scope": scope,
        "row_count": str(len(rows)),
        "consistent_count": str(statuses["consistent"]),
        "conflict_count": str(statuses["conflict"]),
        "incomplete_count": str(statuses["incomplete"]),
        "split_required_count": str(statuses["split_required"]),
        "review_only_count": str(statuses["review_only"]),
        "not_available_count": str(statuses["not_available"]),
        "product_candidate_ready_count": str(
            sum(
                1
                for consistency_row in rows
                if consistency_row["split_readiness_status"]
                == "peak_hypothesis_ready"
            )
        ),
        "hard_blocker_count": str(hard_blocker_count),
        "consistency_gate_status": gate_status,
        "dominant_blockers": _format_tokens(
            token
            for token, _count in sorted(
                blockers.items(),
                key=lambda item: (-item[1], item[0]),
            )[:5]
        ),
        "next_action": next_action,
        "diagnostic_only": "TRUE",
    }
    validate_row_tokens(row)
    return row


def write_hypothesis_consistency_rows(
    path: Path,
    rows: Sequence[Mapping[str, str]],
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    write_tsv(path, rows, HYPOTHESIS_CONSISTENCY_COLUMNS, lineterminator="\n")


def write_hypothesis_consistency_summary(
    path: Path,
    row: Mapping[str, str],
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    write_tsv(path, [row], HYPOTHESIS_CONSISTENCY_SUMMARY_COLUMNS, lineterminator="\n")


def _row_for_key(
    key: tuple[str, str],
    *,
    peak_hypothesis: Mapping[str, str] | None,
    ms1_pattern: Mapping[str, str] | None,
    qc_ms1_reference: Mapping[str, str] | None,
    matrix_rt_drift: Mapping[str, str] | None,
    candidate_ms2: Mapping[str, str] | None,
    family_required_tag: Mapping[str, str] | None,
) -> dict[str, str]:
    family_id, sample_stem = key
    ms2_opportunity = _ms2_opportunity_status(
        candidate_ms2=candidate_ms2,
        family_required_tag=family_required_tag,
        ms1_pattern=ms1_pattern,
        qc_ms1_reference=qc_ms1_reference,
    )
    family_required_tag_status = _family_required_tag_status(
        candidate_ms2,
        family_required_tag,
    )
    blockers = _consistency_blockers(
        peak_hypothesis=peak_hypothesis,
        ms1_pattern=ms1_pattern,
        qc_ms1_reference=qc_ms1_reference,
        matrix_rt_drift=matrix_rt_drift,
        candidate_ms2=candidate_ms2,
        ms2_opportunity=ms2_opportunity,
    )
    consistency_status = _evidence_consistency_status(
        peak_hypothesis,
        blockers,
    )
    split_readiness = _split_readiness_status(
        peak_hypothesis,
        consistency_status,
    )
    missing_evidence = _missing_evidence(
        peak_hypothesis=peak_hypothesis,
        ms1_pattern=ms1_pattern,
        matrix_rt_drift=matrix_rt_drift,
        candidate_ms2=candidate_ms2,
    )
    row = {
        "hypothesis_consistency_schema_version": (
            HYPOTHESIS_CONSISTENCY_SCHEMA_VERSION
        ),
        "feature_family_id": family_id,
        "sample_stem": sample_stem,
        "peak_hypothesis_id": text_value(
            (peak_hypothesis or {}).get("peak_hypothesis_id")
        ),
        "peak_hypothesis_status": _text_default(
            (peak_hypothesis or {}).get("peak_hypothesis_status"),
            "not_available",
        ),
        "product_unit_scope": _text_default(
            (peak_hypothesis or {}).get("product_unit_scope"),
            "not_available",
        ),
        "product_selection_action": _text_default(
            (peak_hypothesis or {}).get("product_selection_action"),
            "no_product_action",
        ),
        "product_selection_blocker": _text_default(
            (peak_hypothesis or {}).get("product_selection_blocker"),
            "not_available",
        ),
        "ms1_pattern_status": _text_default(
            (ms1_pattern or {}).get("ms1_pattern_status"),
            "not_available",
        ),
        "ms1_pattern_evidence_level": _text_default(
            (ms1_pattern or {}).get("ms1_pattern_evidence_level"),
            "not_available",
        ),
        "qc_reference_status": _text_default(
            (qc_ms1_reference or {}).get("qc_reference_status"),
            "not_available",
        ),
        "qc_reference_evidence_level": _text_default(
            (qc_ms1_reference or {}).get("qc_reference_evidence_level"),
            "not_available",
        ),
        "matrix_rt_drift_status": _text_default(
            (matrix_rt_drift or {}).get("matrix_rt_drift_status"),
            "not_available",
        ),
        "drift_evidence_level": _text_default(
            (matrix_rt_drift or {}).get("drift_evidence_level"),
            "not_available",
        ),
        "drift_compatible_status": _text_default(
            (matrix_rt_drift or {}).get("drift_compatible_status"),
            "not_available",
        ),
        "candidate_ms2_pattern_status": _text_default(
            (candidate_ms2 or {}).get("candidate_ms2_pattern_status"),
            "not_available",
        ),
        "candidate_ms2_evidence_level": _text_default(
            (candidate_ms2 or {}).get("candidate_ms2_evidence_level"),
            "not_available",
        ),
        "family_required_tag_status": family_required_tag_status,
        "ms2_opportunity_status": ms2_opportunity,
        "evidence_consistency_status": consistency_status,
        "split_readiness_status": split_readiness,
        "consistency_blockers": _format_tokens(blockers),
        "missing_evidence": _format_tokens(missing_evidence),
        "evidence_sources_seen": _format_tokens(
            _evidence_sources_seen(
                peak_hypothesis=peak_hypothesis,
                ms1_pattern=ms1_pattern,
                qc_ms1_reference=qc_ms1_reference,
                matrix_rt_drift=matrix_rt_drift,
                candidate_ms2=candidate_ms2,
            )
        ),
        "hypothesis_next_action": _next_action(
            consistency_status,
            blockers,
        ),
        "diagnostic_only": "TRUE",
    }
    validate_semicolon_tokens(
        row["consistency_blockers"],
        field="consistency_blockers",
        allowed_tokens=_BLOCKER_TOKENS,
    )
    validate_row_tokens(row)
    return row


def _all_keys(*mappings: EvidenceByKey) -> tuple[tuple[str, str], ...]:
    keys: set[tuple[str, str]] = set()
    for mapping in mappings:
        keys.update(
            (family_id, sample_stem)
            for family_id, sample_stem in mapping
            if family_id and sample_stem
        )
    return tuple(sorted(keys))


def _family_required_tags(
    candidate_ms2: EvidenceByKey,
) -> dict[str, Mapping[str, str]]:
    observed: dict[str, Mapping[str, str]] = {}
    for (family_id, _sample_stem), row in candidate_ms2.items():
        if not _has_required_tag(row):
            continue
        observed.setdefault(family_id, row)
    return observed


def _family_required_tag_status(
    candidate_ms2: Mapping[str, str] | None,
    family_required_tag: Mapping[str, str] | None,
) -> str:
    if _has_required_tag(candidate_ms2):
        return "sample_required_tag_observed"
    if family_required_tag is not None:
        return "family_required_tag_observed"
    if not candidate_ms2 or _status(candidate_ms2, "candidate_ms2") == "not_available":
        return "not_available"
    if (
        candidate_ms2.get("candidate_ms2_pattern_status") == "not_observed"
        or candidate_ms2.get("candidate_ms2_evidence_level")
        != "not_available"
    ):
        return "family_required_tag_not_observed"
    return "not_observed"


def _ms2_opportunity_status(
    *,
    candidate_ms2: Mapping[str, str] | None,
    family_required_tag: Mapping[str, str] | None,
    ms1_pattern: Mapping[str, str] | None,
    qc_ms1_reference: Mapping[str, str] | None,
) -> str:
    if _has_required_tag(candidate_ms2):
        return "required_tag_observed"
    if candidate_ms2 and candidate_ms2.get("candidate_ms2_pattern_status") in (
        _CANDIDATE_MS2_CONFLICT_STATUSES
    ):
        return "conflict"
    if not candidate_ms2 or _status(candidate_ms2, "candidate_ms2") == "not_available":
        return "not_available"
    if _dda_missing_nl_not_dispositive(
        candidate_ms2=candidate_ms2,
        family_required_tag=family_required_tag,
        ms1_pattern=ms1_pattern,
        qc_ms1_reference=qc_ms1_reference,
    ):
        return "dda_missing_nl_not_dispositive"
    if family_required_tag is not None:
        return "family_required_tag_observed"
    if candidate_ms2.get("candidate_ms2_pattern_status") == "not_observed":
        return "expected_but_missing"
    return "not_observed_review"


def _consistency_blockers(
    *,
    peak_hypothesis: Mapping[str, str] | None,
    ms1_pattern: Mapping[str, str] | None,
    qc_ms1_reference: Mapping[str, str] | None,
    matrix_rt_drift: Mapping[str, str] | None,
    candidate_ms2: Mapping[str, str] | None,
    ms2_opportunity: str,
) -> tuple[str, ...]:
    blockers: list[str] = []
    peak_status = _status(peak_hypothesis, "peak_hypothesis")
    if peak_status == "not_available":
        blockers.append("peak_hypothesis_missing")
    elif peak_status == "cross_mode_rescue_blocked":
        blockers.append("cross_mode_rescue")
    elif peak_status == "mode_split_required":
        blockers.append("mode_split_required")
    elif peak_status == "consolidation_no_go":
        blockers.append("consolidation_no_go")
    elif peak_status == "tailing_review_only":
        blockers.append("tailing_confounded")
    elif peak_status == "raw_mode_review_only":
        blockers.append("raw_mode_review_only")
    elif peak_status == "inconclusive":
        blockers.append("inconclusive_mode_evidence")

    ms1_status = _status(ms1_pattern, "ms1_pattern")
    if ms1_status in _MS1_CONFLICT_STATUSES:
        blockers.append("ms1_pattern_conflict")
    elif ms1_status not in _MS1_SUPPORT_STATUSES:
        blockers.append("ms1_pattern_missing")

    qc_status = text_value((qc_ms1_reference or {}).get("qc_reference_status"))
    if qc_status in _QC_MS1_CONFLICT_STATUSES:
        blockers.append("qc_ms1_reference_conflict")

    rt_status = _status(matrix_rt_drift, "matrix_rt_drift")
    drift_compatible = text_value(
        (matrix_rt_drift or {}).get("drift_compatible_status")
    )
    if rt_status in _RT_CONFLICT_STATUSES or drift_compatible == "conflict":
        blockers.append("matrix_rt_drift_conflict")
    elif rt_status not in _RT_SUPPORT_STATUSES:
        blockers.append("matrix_rt_drift_missing")

    candidate_status = _status(candidate_ms2, "candidate_ms2")
    if candidate_status in _CANDIDATE_MS2_CONFLICT_STATUSES:
        blockers.append("candidate_ms2_conflict")
    elif ms2_opportunity == "expected_but_missing":
        blockers.append("family_required_tag_not_observed")
    elif ms2_opportunity == "not_available":
        blockers.append("candidate_ms2_missing")
    elif ms2_opportunity == "not_observed_review":
        blockers.append("ms2_opportunity_review")

    return tuple(dict.fromkeys(blockers))


def _evidence_consistency_status(
    peak_hypothesis: Mapping[str, str] | None,
    blockers: Sequence[str],
) -> str:
    peak_status = _status(peak_hypothesis, "peak_hypothesis")
    if peak_status == "not_available":
        return "not_available"
    if any(
        blocker
        in {"cross_mode_rescue", "mode_split_required", "consolidation_no_go"}
        for blocker in blockers
    ):
        return "split_required"
    if any(
        blocker
        in {
            "ms1_pattern_conflict",
            "qc_ms1_reference_conflict",
            "matrix_rt_drift_conflict",
            "candidate_ms2_conflict",
            "family_required_tag_not_observed",
        }
        for blocker in blockers
    ):
        return "conflict"
    if any(
        blocker
        in {
            "tailing_confounded",
            "raw_mode_review_only",
            "inconclusive_mode_evidence",
            "ms2_opportunity_review",
        }
        for blocker in blockers
    ):
        return "review_only"
    if blockers:
        return "incomplete"
    return "consistent"


def _split_readiness_status(
    peak_hypothesis: Mapping[str, str] | None,
    consistency_status: str,
) -> str:
    peak_status = _status(peak_hypothesis, "peak_hypothesis")
    if consistency_status == "consistent" and peak_status == "product_candidate_core":
        return "peak_hypothesis_ready"
    if peak_status == "cross_mode_rescue_blocked":
        return "cross_mode_rescue_blocked"
    if peak_status == "mode_split_required":
        return "mode_split_required"
    if peak_status == "consolidation_no_go":
        return "consolidation_no_go"
    if consistency_status == "incomplete":
        return "incomplete_evidence"
    if consistency_status == "not_available":
        return "not_available"
    return "review_required"


def _missing_evidence(
    *,
    peak_hypothesis: Mapping[str, str] | None,
    ms1_pattern: Mapping[str, str] | None,
    matrix_rt_drift: Mapping[str, str] | None,
    candidate_ms2: Mapping[str, str] | None,
) -> tuple[str, ...]:
    missing: list[str] = []
    if _status(peak_hypothesis, "peak_hypothesis") == "not_available":
        missing.append("peak_hypothesis_selection")
    if _status(ms1_pattern, "ms1_pattern") not in (
        _MS1_SUPPORT_STATUSES | _MS1_CONFLICT_STATUSES
    ):
        missing.append("ms1_pattern_coherence")
    if _status(matrix_rt_drift, "matrix_rt_drift") not in (
        _RT_SUPPORT_STATUSES | _RT_CONFLICT_STATUSES
    ):
        missing.append("matrix_rt_drift_policy")
    if _status(candidate_ms2, "candidate_ms2") == "not_available":
        missing.append("candidate_ms2_pattern")
    return tuple(missing)


def _evidence_sources_seen(
    *,
    peak_hypothesis: Mapping[str, str] | None,
    ms1_pattern: Mapping[str, str] | None,
    qc_ms1_reference: Mapping[str, str] | None,
    matrix_rt_drift: Mapping[str, str] | None,
    candidate_ms2: Mapping[str, str] | None,
) -> tuple[str, ...]:
    sources: list[str] = []
    if peak_hypothesis:
        sources.append("peak_hypothesis_selection")
    if ms1_pattern:
        sources.append("ms1_pattern_coherence")
    if qc_ms1_reference:
        sources.append("qc_ms1_pattern_reference")
    if matrix_rt_drift:
        sources.append("matrix_rt_drift_policy")
    if candidate_ms2:
        sources.append("candidate_ms2_pattern")
    return tuple(sources)


def _next_action(consistency_status: str, blockers: Sequence[str]) -> str:
    if consistency_status == "consistent":
        return "no_action"
    if consistency_status == "split_required":
        return "split_peak_hypothesis"
    if consistency_status == "incomplete" or consistency_status == "not_available":
        return "add_missing_sidecar"
    if "family_required_tag_not_observed" in blockers:
        return "block_product_activation"
    if "ms2_opportunity_review" in blockers:
        return "inspect_ms2_opportunity"
    if consistency_status == "review_only":
        return "keep_review_only"
    return "inspect_conflict"


def _has_required_tag(row: Mapping[str, str] | None) -> bool:
    if not row:
        return False
    if row.get("candidate_ms2_pattern_status") not in _CANDIDATE_MS2_SUPPORT_STATUSES:
        return False
    if row.get("candidate_ms2_evidence_level") not in _CANDIDATE_MS2_OBSERVED_LEVELS:
        return False
    return any(
        (count := _int_or_none(row.get(field))) is not None and count >= 1
        for field in (
            "raw_ms2_strict_nl_scan_count",
            "matched_neutral_loss_count",
            "source_matched_tag_count",
        )
    )


def _dda_missing_nl_not_dispositive(
    *,
    candidate_ms2: Mapping[str, str] | None,
    family_required_tag: Mapping[str, str] | None,
    ms1_pattern: Mapping[str, str] | None,
    qc_ms1_reference: Mapping[str, str] | None,
) -> bool:
    if not candidate_ms2 or family_required_tag is None:
        return False
    if candidate_ms2.get("candidate_ms2_pattern_status") != "not_observed":
        return False
    if (
        candidate_ms2.get("candidate_ms2_evidence_level")
        != "sample_boundary_no_observed_pattern"
    ):
        return False
    trigger_count = _int_or_none(candidate_ms2.get("raw_ms2_trigger_scan_count"))
    if trigger_count is None or trigger_count < _DDA_NON_DISPOSITIVE_TRIGGER_SCAN_MIN:
        return False
    strict_nl_count = _int_or_none(candidate_ms2.get("raw_ms2_strict_nl_scan_count"))
    if strict_nl_count not in {0, None}:
        return False
    if (
        candidate_ms2.get("raw_ms2_trace_strength")
        not in _DDA_NON_DISPOSITIVE_TRACE_STRENGTHS
        and candidate_ms2.get("raw_ms2_diagnostic_product_absence_reason")
        != "product_outside_diagnostic_window"
    ):
        return False
    if not (_ms1_supports(ms1_pattern) or _qc_ms1_supports(qc_ms1_reference)):
        return False
    intensity = _ms1_supporting_intensity(ms1_pattern)
    return intensity is not None and intensity >= _DDA_NON_DISPOSITIVE_MS1_INTENSITY_MIN


def _ms1_supports(row: Mapping[str, str] | None) -> bool:
    if not row:
        return False
    return row.get("ms1_pattern_status") in _MS1_SUPPORT_STATUSES


def _qc_ms1_supports(row: Mapping[str, str] | None) -> bool:
    if not row:
        return False
    return row.get("qc_reference_status") in _QC_MS1_SUPPORT_STATUSES


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


def _status(row: Mapping[str, str] | None, kind: str) -> str:
    if not row:
        return "not_available"
    if kind == "peak_hypothesis":
        return _text_default(row.get("peak_hypothesis_status"), "not_available")
    if kind == "ms1_pattern":
        return _text_default(row.get("ms1_pattern_status"), "not_available")
    if kind == "matrix_rt_drift":
        return _text_default(row.get("matrix_rt_drift_status"), "not_available")
    if kind == "candidate_ms2":
        return _text_default(row.get("candidate_ms2_pattern_status"), "not_available")
    return "not_available"


def _text_default(value: object, default: str) -> str:
    text = text_value(value)
    return text if text else default


def _format_tokens(tokens: Iterable[str]) -> str:
    return ";".join(token for token in tokens if token)


def _split_semicolon(value: str) -> tuple[str, ...]:
    return tuple(part for part in str(value or "").split(";") if part)


def _float_or_none(value: object) -> float | None:
    try:
        text = str(value or "").strip()
        if not text:
            return None
        return float(text)
    except (TypeError, ValueError):
        return None


def _int_or_none(value: object) -> int | None:
    try:
        text = str(value or "").strip()
        if not text:
            return None
        return int(float(text))
    except (TypeError, ValueError):
        return None
