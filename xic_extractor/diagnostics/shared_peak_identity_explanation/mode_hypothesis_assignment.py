from __future__ import annotations

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
    QC_MS1_PATTERN_REFERENCE_REQUIRED_COLUMNS,
    RT_MODE_EVIDENCE_REQUIRED_COLUMNS,
)
from .schema import (
    PEAK_HYPOTHESIS_SELECTION_COLUMNS,
    PEAK_HYPOTHESIS_SELECTION_SCHEMA_VERSION,
    validate_row_tokens,
)

EvidenceByKey = Mapping[tuple[str, str], Mapping[str, str]]

_TYPED_RT_MODE_LEVELS = frozenset(
    {
        "irt_selected_apex_modes",
        "mode_assignment_summary",
    }
)
_RAW_MODE_LEVELS = frozenset({"raw_selected_apex_modes"})
_MS1_SUPPORT_STATUSES = frozenset({"supportive", "partial_support"})
_MS1_CONFLICT_STATUSES = frozenset({"conflict"})
_QC_SUPPORT_STATUSES = frozenset({"supportive", "partial_support"})
_QC_CONFLICT_STATUSES = frozenset({"conflict", "mixed_conflict"})
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


def load_rt_mode_rows(path: Path) -> tuple[dict[str, str], ...]:
    return read_tsv_required(path, RT_MODE_EVIDENCE_REQUIRED_COLUMNS)


def load_candidate_ms2_pattern_rows(path: Path | None) -> tuple[dict[str, str], ...]:
    if path is None:
        return ()
    return read_tsv_required(path, CANDIDATE_MS2_PATTERN_REQUIRED_COLUMNS)


def load_ms1_pattern_coherence_rows(path: Path | None) -> tuple[dict[str, str], ...]:
    if path is None:
        return ()
    return read_tsv_required(path, MS1_PATTERN_COHERENCE_REQUIRED_COLUMNS)


def load_qc_ms1_pattern_reference_rows(
    path: Path | None,
) -> tuple[dict[str, str], ...]:
    if path is None:
        return ()
    return read_tsv_required(path, QC_MS1_PATTERN_REFERENCE_REQUIRED_COLUMNS)


def load_matrix_rt_drift_policy_rows(
    path: Path | None,
) -> tuple[dict[str, str], ...]:
    if path is None:
        return ()
    return read_tsv_required(path, MATRIX_RT_DRIFT_POLICY_REQUIRED_COLUMNS)


def build_mode_hypothesis_assignment_rows(
    *,
    rt_mode_rows: Sequence[Mapping[str, str]],
    candidate_ms2_pattern_rows: Sequence[Mapping[str, str]] = (),
    ms1_pattern_coherence_rows: Sequence[Mapping[str, str]] = (),
    qc_ms1_pattern_reference_rows: Sequence[Mapping[str, str]] = (),
    matrix_rt_drift_policy_rows: Sequence[Mapping[str, str]] = (),
    oracle_keys: Iterable[tuple[str, str]] = (),
) -> tuple[dict[str, str], ...]:
    """Build sample-level PeakHypothesis assignments from typed evidence.

    This producer treats RT/iRT mode membership as a candidate assignment only.
    Product candidate status requires independent mode-level required-tag
    support plus sample-level MS1 shape, RT drift, QC context, and MS2 opportunity
    checks. Raw-overlay-only modes stay review-only.
    """

    rt_modes = _rows_by_key(rt_mode_rows)
    candidate_ms2 = _rows_by_key(candidate_ms2_pattern_rows)
    ms1_pattern = _rows_by_key(ms1_pattern_coherence_rows)
    qc_reference = _rows_by_key(qc_ms1_pattern_reference_rows)
    rt_drift = _rows_by_key(matrix_rt_drift_policy_rows)
    tagged_modes = _tagged_modes_by_family(rt_modes, candidate_ms2)
    tagged_families = _tagged_families(candidate_ms2)
    keys = tuple(oracle_keys) or _all_keys(
        rt_modes,
        candidate_ms2,
        ms1_pattern,
        qc_reference,
        rt_drift,
    )
    return tuple(
        _assignment_row(
            family_id=family_id,
            sample_stem=sample_stem,
            rt_mode=rt_modes.get((family_id, sample_stem)),
            candidate_ms2=candidate_ms2.get((family_id, sample_stem)),
            family_required_tag=tagged_families.get(family_id),
            selected_mode_has_required_tag=text_value(
                (rt_modes.get((family_id, sample_stem)) or {}).get(
                    "selected_mode_id"
                )
            )
            in tagged_modes.get(family_id, frozenset()),
            family_has_tagged_mode=bool(tagged_modes.get(family_id)),
            ms1_pattern=ms1_pattern.get((family_id, sample_stem)),
            qc_reference=qc_reference.get((family_id, sample_stem)),
            rt_drift=rt_drift.get((family_id, sample_stem)),
        )
        for family_id, sample_stem in sorted(keys)
    )


def write_mode_hypothesis_assignment_rows(
    path: Path,
    rows: Sequence[Mapping[str, str]],
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    write_tsv(
        path,
        rows,
        PEAK_HYPOTHESIS_SELECTION_COLUMNS,
        lineterminator="\n",
    )


def _assignment_row(
    *,
    family_id: str,
    sample_stem: str,
    rt_mode: Mapping[str, str] | None,
    candidate_ms2: Mapping[str, str] | None,
    family_required_tag: Mapping[str, str] | None,
    selected_mode_has_required_tag: bool,
    family_has_tagged_mode: bool,
    ms1_pattern: Mapping[str, str] | None,
    qc_reference: Mapping[str, str] | None,
    rt_drift: Mapping[str, str] | None,
) -> dict[str, str]:
    status, scope, action, blocker, reason = _decision(
        rt_mode=rt_mode,
        candidate_ms2=candidate_ms2,
        family_required_tag=family_required_tag,
        selected_mode_has_required_tag=selected_mode_has_required_tag,
        family_has_tagged_mode=family_has_tagged_mode,
        ms1_pattern=ms1_pattern,
        qc_reference=qc_reference,
        rt_drift=rt_drift,
    )
    row = {
        "peak_hypothesis_selection_schema_version": (
            PEAK_HYPOTHESIS_SELECTION_SCHEMA_VERSION
        ),
        "feature_family_id": family_id,
        "sample_stem": sample_stem,
        "peak_hypothesis_id": _peak_hypothesis_id(rt_mode, family_id),
        "peak_hypothesis_status": status,
        "product_unit_scope": scope,
        "selected_mode_id": text_value((rt_mode or {}).get("selected_mode_id")),
        "selected_mode_role": _text_default(
            (rt_mode or {}).get("selected_mode_role"),
            "unknown",
        ),
        "selected_mode_tag_status": _selected_mode_tag_status(
            rt_mode,
            candidate_ms2,
            family_required_tag,
            selected_mode_has_required_tag,
        ),
        "family_mode_class": _text_default(
            (rt_mode or {}).get("family_mode_class"),
            "inconclusive",
        ),
        "family_mode_count": _text_default(
            (rt_mode or {}).get("family_mode_count"),
            "0",
        ),
        "tag_bearing_mode_count": "1" if family_has_tagged_mode else "0",
        "product_selection_action": action,
        "product_selection_blocker": blocker,
        "reason": reason,
        "diagnostic_only": "TRUE",
    }
    validate_row_tokens(row)
    return row


def _decision(
    *,
    rt_mode: Mapping[str, str] | None,
    candidate_ms2: Mapping[str, str] | None,
    family_required_tag: Mapping[str, str] | None,
    selected_mode_has_required_tag: bool,
    family_has_tagged_mode: bool,
    ms1_pattern: Mapping[str, str] | None,
    qc_reference: Mapping[str, str] | None,
    rt_drift: Mapping[str, str] | None,
) -> tuple[str, str, str, str, str]:
    if rt_mode is None:
        return _not_available("rt_mode_evidence_missing")
    rt_status = text_value(rt_mode.get("rt_mode_status"))
    evidence_level = text_value(rt_mode.get("rt_mode_evidence_level"))
    if rt_status == "raw_mode_review_only" or evidence_level in _RAW_MODE_LEVELS:
        return (
            "raw_mode_review_only",
            "review_only",
            "require_raw_mode_review",
            "raw_mode_review_only",
            "raw_mode_requires_typed_irt_mode_hypothesis",
        )
    if rt_status == "tailing_confounded":
        return (
            "tailing_review_only",
            "review_only",
            "require_tailing_review",
            "tailing_confounded",
            "tailing_confounds_mode_split",
        )
    if rt_status == "mode_conflict":
        return _cross_mode_block("selected_cell_belongs_to_non_core_rt_mode")
    if rt_status == "consolidation_no_go":
        return _consolidation_no_go("rt_mode_consolidation_no_go")
    if rt_status == "mode_split_required":
        return _mode_split_required("rt_mode_split_required")
    if rt_status != "mode_supported":
        return _inconclusive("rt_mode_evidence_inconclusive")
    if evidence_level not in _TYPED_RT_MODE_LEVELS:
        return _inconclusive("typed_rt_mode_evidence_missing")
    if not text_value(rt_mode.get("selected_mode_id")):
        return _inconclusive("selected_mode_id_missing")
    if family_required_tag is None:
        return _consolidation_no_go("family_required_tag_not_observed")
    if not selected_mode_has_required_tag:
        if family_has_tagged_mode:
            return _cross_mode_block("selected_mode_lacks_required_tag")
        return _mode_split_required("required_tag_not_mapped_to_typed_mode")

    blockers = _typed_evidence_blockers(
        candidate_ms2=candidate_ms2,
        family_required_tag=family_required_tag,
        ms1_pattern=ms1_pattern,
        qc_reference=qc_reference,
        rt_drift=rt_drift,
    )
    if blockers:
        return _inconclusive(f"typed_assignment_blocked_by_{';'.join(blockers)}")
    ms2_reason = _ms2_reason(candidate_ms2)
    return (
        "product_candidate_core",
        "mode_level",
        "select_mode_peak_hypothesis",
        "none",
        (
            "typed_mode_hypothesis_assignment_supported_by_mode_tag"
            f"_and_{ms2_reason}"
        ),
    )


def _typed_evidence_blockers(
    *,
    candidate_ms2: Mapping[str, str] | None,
    family_required_tag: Mapping[str, str],
    ms1_pattern: Mapping[str, str] | None,
    qc_reference: Mapping[str, str] | None,
    rt_drift: Mapping[str, str] | None,
) -> tuple[str, ...]:
    blockers: list[str] = []
    if _ms1_conflicts(ms1_pattern):
        blockers.append("ms1_pattern_conflict")
    elif not _ms1_supports(ms1_pattern):
        blockers.append("ms1_pattern_missing")

    if _qc_conflicts(qc_reference) and not _ms1_supports(ms1_pattern):
        blockers.append("qc_ms1_reference_conflict")

    if _rt_conflicts(rt_drift):
        blockers.append("matrix_rt_drift_conflict")
    elif not _rt_supports(rt_drift):
        blockers.append("matrix_rt_drift_missing")

    if _candidate_ms2_conflicts(candidate_ms2):
        blockers.append("candidate_ms2_conflict")
    elif not _ms2_opportunity_allows_assignment(
        candidate_ms2,
        family_required_tag=family_required_tag,
        ms1_pattern=ms1_pattern,
        qc_reference=qc_reference,
    ):
        blockers.append(_ms2_blocker(candidate_ms2))
    return tuple(blockers)


def _ms2_opportunity_allows_assignment(
    candidate_ms2: Mapping[str, str] | None,
    *,
    family_required_tag: Mapping[str, str],
    ms1_pattern: Mapping[str, str] | None,
    qc_reference: Mapping[str, str] | None,
) -> bool:
    if _has_required_tag(candidate_ms2):
        return True
    return _dda_missing_nl_not_dispositive(
        candidate_ms2=candidate_ms2,
        family_required_tag=family_required_tag,
        ms1_pattern=ms1_pattern,
        qc_reference=qc_reference,
    )


def _dda_missing_nl_not_dispositive(
    *,
    candidate_ms2: Mapping[str, str] | None,
    family_required_tag: Mapping[str, str],
    ms1_pattern: Mapping[str, str] | None,
    qc_reference: Mapping[str, str] | None,
) -> bool:
    del family_required_tag
    if not candidate_ms2:
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
    if not (_ms1_supports(ms1_pattern) or _qc_supports(qc_reference)):
        return False
    intensity = _ms1_supporting_intensity(ms1_pattern)
    return intensity is not None and intensity >= _DDA_NON_DISPOSITIVE_MS1_INTENSITY_MIN


def _rows_by_key(
    rows: Sequence[Mapping[str, str]],
) -> dict[tuple[str, str], Mapping[str, str]]:
    by_key: dict[tuple[str, str], Mapping[str, str]] = {}
    for row in rows:
        family_id = text_value(row.get("feature_family_id"))
        sample_stem = text_value(row.get("sample_stem") or row.get("sample_id"))
        if family_id and sample_stem:
            by_key[(family_id, sample_stem)] = row
    return by_key


def _tagged_modes_by_family(
    rt_modes: EvidenceByKey,
    candidate_ms2: EvidenceByKey,
) -> dict[str, frozenset[str]]:
    modes: dict[str, set[str]] = {}
    for key, row in candidate_ms2.items():
        if not _has_required_tag(row):
            continue
        rt_mode = rt_modes.get(key)
        mode_id = text_value((rt_mode or {}).get("selected_mode_id"))
        family_id = key[0]
        if family_id and mode_id:
            modes.setdefault(family_id, set()).add(mode_id)
    return {family_id: frozenset(values) for family_id, values in modes.items()}


def _tagged_families(
    candidate_ms2: EvidenceByKey,
) -> dict[str, Mapping[str, str]]:
    tagged: dict[str, Mapping[str, str]] = {}
    for (family_id, _sample_stem), row in candidate_ms2.items():
        if family_id and _has_required_tag(row):
            tagged.setdefault(family_id, row)
    return tagged


def _all_keys(*mappings: EvidenceByKey) -> tuple[tuple[str, str], ...]:
    keys: set[tuple[str, str]] = set()
    for mapping in mappings:
        keys.update(
            (family_id, sample_stem)
            for family_id, sample_stem in mapping
            if family_id and sample_stem
        )
    return tuple(sorted(keys))


def _peak_hypothesis_id(rt_mode: Mapping[str, str] | None, family_id: str) -> str:
    mode_id = text_value((rt_mode or {}).get("selected_mode_id"))
    if not family_id or not mode_id:
        return ""
    return f"{family_id}::{mode_id}"


def _selected_mode_tag_status(
    rt_mode: Mapping[str, str] | None,
    candidate_ms2: Mapping[str, str] | None,
    family_required_tag: Mapping[str, str] | None,
    selected_mode_has_required_tag: bool,
) -> str:
    if _has_required_tag(candidate_ms2):
        return "tag_supported"
    if selected_mode_has_required_tag:
        return "family_tag_supported"
    if family_required_tag is not None:
        return "no_tag_observed"
    if rt_mode is None:
        return "unknown"
    return "family_tag_absent"


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


def _ms1_supports(row: Mapping[str, str] | None) -> bool:
    return bool(row and row.get("ms1_pattern_status") in _MS1_SUPPORT_STATUSES)


def _ms1_conflicts(row: Mapping[str, str] | None) -> bool:
    return bool(row and row.get("ms1_pattern_status") in _MS1_CONFLICT_STATUSES)


def _qc_supports(row: Mapping[str, str] | None) -> bool:
    return bool(row and row.get("qc_reference_status") in _QC_SUPPORT_STATUSES)


def _qc_conflicts(row: Mapping[str, str] | None) -> bool:
    return bool(row and row.get("qc_reference_status") in _QC_CONFLICT_STATUSES)


def _rt_supports(row: Mapping[str, str] | None) -> bool:
    if not row or row.get("matrix_rt_drift_status") not in _RT_SUPPORT_STATUSES:
        return False
    drift_compatible = text_value(row.get("drift_compatible_status"))
    return drift_compatible in {"", "compatible", "not_applicable"}


def _rt_conflicts(row: Mapping[str, str] | None) -> bool:
    return bool(
        row
        and (
            row.get("matrix_rt_drift_status") in _RT_CONFLICT_STATUSES
            or row.get("drift_compatible_status") == "conflict"
        )
    )


def _candidate_ms2_conflicts(row: Mapping[str, str] | None) -> bool:
    return bool(
        row
        and row.get("candidate_ms2_pattern_status")
        in _CANDIDATE_MS2_CONFLICT_STATUSES
    )


def _ms2_blocker(row: Mapping[str, str] | None) -> str:
    if row is None:
        return "candidate_ms2_missing"
    if row.get("candidate_ms2_pattern_status") == "not_observed":
        return "ms2_opportunity_review"
    return "candidate_ms2_missing"


def _ms2_reason(candidate_ms2: Mapping[str, str] | None) -> str:
    if _has_required_tag(candidate_ms2):
        return "sample_required_tag"
    return "dda_opportunity"


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


def _not_available(reason: str) -> tuple[str, str, str, str, str]:
    return (
        "not_available",
        "not_available",
        "no_product_action",
        "not_available",
        reason,
    )


def _inconclusive(reason: str) -> tuple[str, str, str, str, str]:
    return (
        "inconclusive",
        "review_only",
        "require_review",
        "inconclusive_mode_evidence",
        reason,
    )


def _cross_mode_block(reason: str) -> tuple[str, str, str, str, str]:
    return (
        "cross_mode_rescue_blocked",
        "sample_cell",
        "block_cross_mode_rescue",
        "cross_mode_rescue",
        reason,
    )


def _mode_split_required(reason: str) -> tuple[str, str, str, str, str]:
    return (
        "mode_split_required",
        "candidate_container",
        "require_mode_split_before_product",
        "mode_split_required",
        reason,
    )


def _consolidation_no_go(reason: str) -> tuple[str, str, str, str, str]:
    return (
        "consolidation_no_go",
        "candidate_container",
        "block_family_promotion",
        "consolidation_no_go",
        reason,
    )


def _text_default(value: object, default: str) -> str:
    text = text_value(value)
    return text if text else default


def _float_or_none(value: object) -> float | None:
    try:
        text = text_value(value)
        return float(text) if text else None
    except (TypeError, ValueError):
        return None


def _int_or_none(value: object) -> int | None:
    parsed = _float_or_none(value)
    return int(parsed) if parsed is not None else None
