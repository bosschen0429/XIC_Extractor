from __future__ import annotations

from collections import Counter, defaultdict
from collections.abc import Hashable, Iterable, Mapping, Sequence
from pathlib import Path
from statistics import median
from typing import TypeVar

from xic_extractor.tabular_io import (
    optional_float,
    read_delimited_rows,
    read_tsv_required,
    text_value,
    write_tsv,
)

from .overlay_trace_data import load_overlay_trace_data
from .schema import (
    RT_MODE_EVIDENCE_COLUMNS,
    RT_MODE_EVIDENCE_SCHEMA_VERSION,
    validate_row_tokens,
)

_ASSIGNMENT_REQUIRED_COLUMNS = ("sample_stem",)
_CANDIDATE_MS2_REQUIRED_COLUMNS = (
    "feature_family_id",
    "sample_stem",
    "candidate_ms2_pattern_status",
    "candidate_ms2_evidence_level",
)
_SUPPORTIVE_MS2_STATUSES = frozenset({"supportive", "partial_support"})
_OBSERVED_MS2_LEVELS = frozenset(
    {"sample_candidate_aligned", "sample_boundary_aligned"}
)
_UNKNOWN_MODES = frozenset(
    {
        "",
        "unknown",
        "unassigned",
        "irt_unknown",
        "raw_unknown",
        "raw_outlier_mode",
        "outlier_unassigned",
    }
)
_RAW_OVERLAY_MODE_GAP_MIN = 0.5
_RAW_OVERLAY_SOFT_MODE_GAP_MIN = 0.35
_RAW_OVERLAY_MODE_GAP_RATIO_MIN = 3.0
_RAW_OVERLAY_MIN_CLUSTER_SIZE = 2
_ModeKey = TypeVar("_ModeKey", bound=Hashable)


def build_rt_mode_evidence_rows(
    *,
    mode_assignment_tsv: Path,
    oracle_keys: Iterable[tuple[str, str]],
    feature_family_id: str | None = None,
    mode_summary_tsv: Path | None = None,
    candidate_ms2_pattern_evidence_tsv: Path | None = None,
) -> tuple[dict[str, str], ...]:
    """Build diagnostic-only RT-mode evidence from selected-apex mode assignments."""

    assignments = read_delimited_rows(
        mode_assignment_tsv,
        required_columns=_ASSIGNMENT_REQUIRED_COLUMNS,
        delimiter="\t",
        encoding="utf-8-sig",
    )
    if not assignments:
        return tuple(
            _missing_row(family_id, sample_stem)
            for family_id, sample_stem in sorted(oracle_keys)
        )
    family_ids = _producer_family_ids(assignments, feature_family_id)
    if not family_ids:
        raise ValueError(
            "feature_family_id is required when mode assignments do not carry it"
        )
    summary_rows = (
        read_delimited_rows(
            mode_summary_tsv,
            required_columns=(),
            delimiter="\t",
            encoding="utf-8-sig",
        )
        if mode_summary_tsv is not None
        else ()
    )
    candidate_rows = (
        read_tsv_required(
            candidate_ms2_pattern_evidence_tsv,
            _CANDIDATE_MS2_REQUIRED_COLUMNS,
        )
        if candidate_ms2_pattern_evidence_tsv is not None
        else ()
    )
    tag_samples = _tag_supported_samples(candidate_rows)
    assignment_rows_by_family = _rows_by_family(assignments, family_ids)
    summary_rows_by_family = _rows_by_family(summary_rows, family_ids)
    family_contexts = {
        family_id: _build_family_context(
            feature_family_id=family_id,
            assignments=assignment_rows_by_family[family_id],
            summary_rows=summary_rows_by_family[family_id],
            tag_samples=tag_samples,
        )
        for family_id in family_ids
    }
    assignment_by_key = {
        (_family_id(row, family_id), text_value(row.get("sample_stem"))): row
        for family_id in family_ids
        for row in assignment_rows_by_family[family_id]
    }
    rows: list[dict[str, str]] = []
    for family_id, sample_stem in sorted(oracle_keys):
        family_context = family_contexts.get(family_id)
        if family_context is None:
            rows.append(_missing_row(family_id, sample_stem))
            continue
        assignment = assignment_by_key.get((family_id, sample_stem))
        if assignment is None:
            rows.append(_missing_row(family_id, sample_stem))
            continue
        rows.append(
            _row_for_assignment(
                family_id=family_id,
                sample_stem=sample_stem,
                assignment=assignment,
                context=family_context,
                tag_samples=tag_samples,
            )
        )
    return tuple(rows)


def build_rt_mode_evidence_rows_from_overlay_trace_data(
    *,
    overlay_trace_data_jsons: Sequence[Path],
    oracle_keys: Iterable[tuple[str, str]],
    candidate_ms2_pattern_evidence_tsv: Path | None = None,
) -> tuple[dict[str, str], ...]:
    """Build conservative RT-mode evidence from family MS1 overlay trace JSONs.

    Overlay trace JSONs carry RAW selected-apex positions, not an independent iRT
    model. The resulting rows are therefore useful for mode-hypothesis coverage
    and fail-closed multimodal detection, but do not by themselves prove drift
    correction or product readiness.
    """

    oracle_key_set = frozenset(oracle_keys)
    candidate_rows = (
        read_tsv_required(
            candidate_ms2_pattern_evidence_tsv,
            _CANDIDATE_MS2_REQUIRED_COLUMNS,
        )
        if candidate_ms2_pattern_evidence_tsv is not None
        else ()
    )
    tag_samples = _tag_supported_samples(candidate_rows)
    rows_by_key: dict[tuple[str, str], dict[str, str]] = {}
    for trace_data_json in overlay_trace_data_jsons:
        family_id, assignments = _overlay_assignments(trace_data_json)
        if not family_id:
            raise ValueError(
                f"overlay trace data does not declare family_id: {trace_data_json}"
            )
        family_context = _build_family_context(
            feature_family_id=family_id,
            assignments=assignments,
            summary_rows=(),
            tag_samples=tag_samples,
        )
        for assignment in assignments:
            sample_stem = text_value(assignment.get("sample_stem"))
            key = (family_id, sample_stem)
            if key not in oracle_key_set:
                continue
            rows_by_key[key] = _row_for_assignment(
                family_id=family_id,
                sample_stem=sample_stem,
                assignment=assignment,
                context=family_context,
                tag_samples=tag_samples,
            )
    return tuple(
        rows_by_key.get((family_id, sample_stem), _missing_row(family_id, sample_stem))
        for family_id, sample_stem in sorted(oracle_key_set)
    )


def merge_rt_mode_evidence_rows(
    *row_groups: Sequence[Mapping[str, str]],
) -> tuple[dict[str, str], ...]:
    """Merge producer rows by family/sample, preferring earlier non-missing rows."""

    rows_by_key: dict[tuple[str, str], dict[str, str]] = {}
    for rows in row_groups:
        for row in rows:
            key = (
                text_value(row.get("feature_family_id")),
                text_value(row.get("sample_stem")),
            )
            if not key[0] or not key[1]:
                continue
            existing = rows_by_key.get(key)
            if (
                existing is not None
                and existing.get("rt_mode_status") != "not_available"
            ):
                continue
            rows_by_key[key] = dict(row)
    return tuple(rows_by_key[key] for key in sorted(rows_by_key))


def write_rt_mode_evidence_rows(
    path: Path,
    rows: Sequence[Mapping[str, str]],
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    write_tsv(path, rows, RT_MODE_EVIDENCE_COLUMNS, lineterminator="\n")


def _infer_family_id(assignments: Sequence[Mapping[str, str]]) -> str:
    family_ids = {
        text_value(row.get("feature_family_id"))
        for row in assignments
        if text_value(row.get("feature_family_id"))
    }
    if len(family_ids) == 1:
        return next(iter(family_ids))
    return ""


def _producer_family_ids(
    assignments: Sequence[Mapping[str, str]],
    explicit_family_id: str | None,
) -> tuple[str, ...]:
    if explicit_family_id:
        return (explicit_family_id,)
    family_ids = sorted(
        {
            text_value(row.get("feature_family_id"))
            for row in assignments
            if text_value(row.get("feature_family_id"))
        }
    )
    if family_ids:
        return tuple(family_ids)
    inferred = _infer_family_id(assignments)
    return (inferred,) if inferred else ()


def _family_rows(
    rows: Sequence[Mapping[str, str]],
    family_id: str,
) -> tuple[Mapping[str, str], ...]:
    return tuple(
        row
        for row in rows
        if not text_value(row.get("feature_family_id"))
        or text_value(row.get("feature_family_id")) == family_id
    )


def _rows_by_family(
    rows: Sequence[Mapping[str, str]],
    family_ids: Sequence[str],
) -> dict[str, tuple[Mapping[str, str], ...]]:
    grouped: dict[str, list[Mapping[str, str]]] = {
        family_id: [] for family_id in family_ids
    }
    family_id_set = frozenset(family_ids)
    for row in rows:
        row_family_id = text_value(row.get("feature_family_id"))
        if row_family_id:
            if row_family_id in family_id_set:
                grouped[row_family_id].append(row)
            continue
        for family_rows in grouped.values():
            family_rows.append(row)
    return {family_id: tuple(values) for family_id, values in grouped.items()}


def _overlay_assignments(path: Path) -> tuple[str, tuple[dict[str, str], ...]]:
    bundle = load_overlay_trace_data(path)
    family_id = bundle.family_id
    trace_rows = bundle.trace_mappings
    mode_ids = _raw_overlay_mode_ids(trace_rows)
    assignments: list[dict[str, str]] = []
    for index, trace in enumerate(trace_rows):
        sample_stem = text_value(trace.get("sample_stem"))
        if not sample_stem:
            continue
        raw_rt = _first_text(trace, ("cell_apex_rt", "trace_apex_rt"))
        assignments.append(
            {
                "feature_family_id": family_id,
                "sample_stem": sample_stem,
                "sample_type": _infer_sample_type(sample_stem),
                "status": text_value(trace.get("status")),
                "mode_id": mode_ids.get(index, "raw_unknown"),
                "cell_apex_rt": raw_rt,
                "raw_selected_rt": raw_rt,
            }
        )
    return family_id, tuple(assignments)


def _raw_overlay_mode_ids(rows: Sequence[Mapping[str, object]]) -> dict[int, str]:
    indexed_values: list[tuple[int, float]] = []
    for index, row in enumerate(rows):
        value = optional_float(_first_text(row, ("cell_apex_rt", "trace_apex_rt")))
        if value is not None:
            indexed_values.append((index, value))
    mode_ids = cluster_raw_overlay_rt_modes(
        indexed_values,
        prefix="raw_mode",
        outlier_mode_id="raw_outlier_mode",
        min_cluster_size=_RAW_OVERLAY_MIN_CLUSTER_SIZE,
    )
    return mode_ids


def cluster_raw_overlay_rt_modes(
    indexed_values: Sequence[tuple[_ModeKey, float]],
    *,
    prefix: str,
    outlier_mode_id: str,
    min_cluster_size: int = _RAW_OVERLAY_MIN_CLUSTER_SIZE,
) -> dict[_ModeKey, str]:
    """Cluster raw-overlay apex RTs into review-only chromatographic modes."""

    if not indexed_values:
        return {}
    ordered = sorted(indexed_values, key=lambda item: item[1])
    clusters: list[list[tuple[_ModeKey, float]]] = []
    current: list[tuple[_ModeKey, float]] = []
    for index, item in enumerate(ordered):
        if current and _is_raw_overlay_mode_gap(
            ordered,
            split_index=index,
            current_cluster=current,
            min_cluster_size=min_cluster_size,
        ):
            clusters.append(current)
            current = []
        current.append(item)
    if current:
        clusters.append(current)
    if len(clusters) == 1:
        return {index: f"{prefix}_1" for index, _value in ordered}
    mode_ids: dict[_ModeKey, str] = {}
    named_cluster_number = 0
    for cluster in clusters:
        if len(cluster) < min_cluster_size:
            mode_id = outlier_mode_id
        else:
            named_cluster_number += 1
            cluster_median = cluster[len(cluster) // 2][1]
            mode_id = f"{prefix}_{named_cluster_number}_{cluster_median:.2f}min"
        for key, _value in cluster:
            mode_ids[key] = mode_id
    return mode_ids


def _is_raw_overlay_mode_gap(
    ordered: Sequence[tuple[Hashable, float]],
    *,
    split_index: int,
    current_cluster: Sequence[tuple[Hashable, float]],
    min_cluster_size: int,
) -> bool:
    gap = ordered[split_index][1] - ordered[split_index - 1][1]
    if gap > _RAW_OVERLAY_MODE_GAP_MIN:
        return True
    if gap < _RAW_OVERLAY_SOFT_MODE_GAP_MIN:
        return False
    remaining_count = len(ordered) - split_index
    if len(current_cluster) < min_cluster_size or remaining_count < min_cluster_size:
        return False
    typical_gap = _typical_adjacent_gap(ordered, excluded_gap_index=split_index)
    return typical_gap > 0 and gap >= typical_gap * _RAW_OVERLAY_MODE_GAP_RATIO_MIN


def _typical_adjacent_gap(
    ordered: Sequence[tuple[Hashable, float]],
    *,
    excluded_gap_index: int,
) -> float:
    gaps = [
        ordered[index][1] - ordered[index - 1][1]
        for index in range(1, len(ordered))
        if index != excluded_gap_index
        and ordered[index][1] - ordered[index - 1][1] > 1e-9
    ]
    if not gaps:
        return 0.0
    return float(median(gaps))


def _infer_sample_type(sample_stem: str) -> str:
    lowered = sample_stem.lower()
    if "qc" in lowered:
        return "QC"
    if "benign" in lowered:
        return "Benign"
    if "normal" in lowered:
        return "Normal"
    if "tumor" in lowered:
        return "Tumor"
    return "unknown"


def _build_family_context(
    *,
    feature_family_id: str,
    assignments: Sequence[Mapping[str, str]],
    summary_rows: Sequence[Mapping[str, str]],
    tag_samples: frozenset[tuple[str, str]],
) -> dict[str, object]:
    mode_rows: dict[str, list[Mapping[str, str]]] = defaultdict(list)
    for row in assignments:
        if _family_id(row, feature_family_id) != feature_family_id:
            continue
        mode_rows[_mode_id(row)].append(row)
    mode_rows = {
        mode_id: rows
        for mode_id, rows in mode_rows.items()
        if mode_id not in _UNKNOWN_MODES and rows
    }
    mode_summary = _summary_by_mode(summary_rows)
    tag_modes = frozenset(
        mode_id
        for mode_id, rows in mode_rows.items()
        if any(
            (feature_family_id, text_value(row.get("sample_stem"))) in tag_samples
            for row in rows
        )
    )
    family_class = _family_mode_class(
        mode_rows=mode_rows,
        mode_summary=mode_summary,
        tag_modes=tag_modes,
    )
    return {
        "mode_rows": mode_rows,
        "mode_summary": mode_summary,
        "tag_modes": tag_modes,
        "family_class": family_class,
        "family_raw_range": _range_text(
            _optional_values(assignments, ("cell_apex_rt", "raw_selected_rt"))
        ),
        "family_normalized_range": _range_text(
            _optional_values(
                assignments,
                ("normalized_cell_apex_rt", "norm_apex_rt", "normalized_selected_rt"),
            )
        ),
    }


def _summary_by_mode(
    rows: Sequence[Mapping[str, str]],
) -> dict[str, Mapping[str, str]]:
    result: dict[str, Mapping[str, str]] = {}
    for row in rows:
        mode_id = _mode_id(row)
        if mode_id not in _UNKNOWN_MODES:
            result[mode_id] = row
    return result


def _family_mode_class(
    *,
    mode_rows: Mapping[str, Sequence[Mapping[str, str]]],
    mode_summary: Mapping[str, Mapping[str, str]],
    tag_modes: frozenset[str],
) -> str:
    override = _family_class_override(mode_summary.values())
    if override:
        return override
    mode_count = len(mode_rows)
    if mode_count == 0:
        return "inconclusive"
    if _tailing_confounded(mode_summary.values()):
        return "tailing_confounded"
    if mode_count == 1:
        return "rt_mode_pure"
    if len(tag_modes) == 1:
        return "tag_backed_core_with_outlier_modes"
    if mode_count >= 3 and not tag_modes:
        return "consolidation_no_go"
    return "irt_refined_mode_split"


def _family_class_override(rows: Iterable[Mapping[str, str]]) -> str:
    overrides = {
        text_value(row.get("family_mode_class"))
        for row in rows
        if text_value(row.get("family_mode_class"))
    }
    overrides.discard("inconclusive")
    if len(overrides) == 1:
        return next(iter(overrides))
    return ""


def _tailing_confounded(rows: Iterable[Mapping[str, str]]) -> bool:
    return any(
        text_value(row.get("tailing_confounded")).upper() == "TRUE" for row in rows
    )


def _row_for_assignment(
    *,
    family_id: str,
    sample_stem: str,
    assignment: Mapping[str, str],
    context: Mapping[str, object],
    tag_samples: frozenset[tuple[str, str]],
) -> dict[str, str]:
    mode_id = _mode_id(assignment)
    mode_rows = context["mode_rows"]
    if not isinstance(mode_rows, Mapping):
        raise AssertionError("mode_rows must be mapping")
    selected_rows = tuple(mode_rows.get(mode_id, ()))
    tag_modes = context["tag_modes"]
    if not isinstance(tag_modes, frozenset):
        raise AssertionError("tag_modes must be frozenset")
    family_class = str(context["family_class"])
    selected_mode_has_tag = mode_id in tag_modes
    sample_has_tag = (family_id, sample_stem) in tag_samples
    evidence_level = _evidence_level(assignment)
    status, role, tag_status, reason = _mode_decision(
        mode_id=mode_id,
        family_class=family_class,
        evidence_level=evidence_level,
        selected_mode_has_tag=selected_mode_has_tag,
        sample_has_tag=sample_has_tag,
        tag_modes=tag_modes,
    )
    row = {
        "rt_mode_evidence_schema_version": RT_MODE_EVIDENCE_SCHEMA_VERSION,
        "feature_family_id": family_id,
        "sample_stem": sample_stem,
        "rt_mode_status": status,
        "rt_mode_evidence_level": evidence_level,
        "selected_mode_id": mode_id,
        "selected_mode_role": role,
        "selected_mode_tag_status": tag_status,
        "family_mode_class": family_class,
        "family_mode_count": str(len(mode_rows)),
        "tag_bearing_mode_count": str(len(tag_modes)),
        "selected_mode_cell_count": str(len(selected_rows)),
        "selected_mode_sample_type_counts": _counts_text(
            row.get("sample_type") for row in selected_rows
        ),
        "selected_mode_status_counts": _counts_text(
            row.get("status") for row in selected_rows
        ),
        "raw_selected_rt": _first_text(
            assignment,
            ("cell_apex_rt", "raw_selected_rt"),
        ),
        "normalized_selected_rt": _first_text(
            assignment,
            ("normalized_cell_apex_rt", "norm_apex_rt", "normalized_selected_rt"),
        ),
        "selected_mode_raw_rt_range_min": _range_text(
            _optional_values(selected_rows, ("cell_apex_rt", "raw_selected_rt"))
        ),
        "selected_mode_normalized_rt_range_min": _range_text(
            _optional_values(
                selected_rows,
                ("normalized_cell_apex_rt", "norm_apex_rt", "normalized_selected_rt"),
            )
        ),
        "family_raw_rt_range_min": str(context["family_raw_range"]),
        "family_normalized_rt_range_min": str(context["family_normalized_range"]),
        "reason": reason,
        "diagnostic_only": "TRUE",
    }
    validate_row_tokens(row)
    return row


def _mode_decision(
    *,
    mode_id: str,
    family_class: str,
    evidence_level: str,
    selected_mode_has_tag: bool,
    sample_has_tag: bool,
    tag_modes: frozenset[str],
) -> tuple[str, str, str, str]:
    if mode_id in _UNKNOWN_MODES:
        return "inconclusive", "unknown", "unknown", "selected_mode_unknown"
    if family_class == "rt_mode_pure":
        tag_status = "tag_supported" if sample_has_tag else "family_tag_absent"
        return "mode_supported", "single_mode", tag_status, "single_rt_mode_family"
    if family_class == "tailing_confounded":
        return (
            "tailing_confounded",
            "tailing_confounded",
            "unknown",
            "tailing_confounds_mode_split",
        )
    if family_class == "tag_backed_core_with_outlier_modes":
        if selected_mode_has_tag:
            tag_status = "tag_supported" if sample_has_tag else "family_tag_supported"
            return (
                "mode_supported",
                "tag_bearing_core",
                tag_status,
                "selected_mode_is_tag_bearing_core",
            )
        if evidence_level == "raw_selected_apex_modes":
            return (
                "raw_mode_review_only",
                "raw_non_tag_outlier",
                "no_tag_observed" if tag_modes else "family_tag_absent",
                "raw_overlay_non_core_mode_requires_irt_confirmation",
            )
        return (
            "mode_conflict",
            "non_tag_outlier",
            "no_tag_observed" if tag_modes else "family_tag_absent",
            "selected_mode_not_tag_bearing_core",
        )
    if evidence_level == "raw_selected_apex_modes" and family_class in {
        "consolidation_no_go",
        "irt_refined_mode_split",
    }:
        return (
            "raw_mode_review_only",
            "raw_split_review",
            "family_tag_absent" if not tag_modes else "unknown",
            "raw_overlay_mode_split_requires_irt_or_tag_review",
        )
    if family_class == "consolidation_no_go":
        return (
            "consolidation_no_go",
            "mixed_mode",
            "family_tag_absent" if not tag_modes else "unknown",
            "multimodal_family_without_tag_bearing_core",
        )
    return (
        "mode_split_required",
        "split_mode",
        "family_tag_supported" if selected_mode_has_tag else "unknown",
        "multimodal_family_requires_split_before_product_label",
    )


def _missing_row(family_id: str, sample_stem: str) -> dict[str, str]:
    row = {
        "rt_mode_evidence_schema_version": RT_MODE_EVIDENCE_SCHEMA_VERSION,
        "feature_family_id": family_id,
        "sample_stem": sample_stem,
        "rt_mode_status": "not_available",
        "rt_mode_evidence_level": "not_available",
        "selected_mode_id": "",
        "selected_mode_role": "unknown",
        "selected_mode_tag_status": "unknown",
        "family_mode_class": "inconclusive",
        "family_mode_count": "0",
        "tag_bearing_mode_count": "0",
        "selected_mode_cell_count": "0",
        "selected_mode_sample_type_counts": "",
        "selected_mode_status_counts": "",
        "raw_selected_rt": "",
        "normalized_selected_rt": "",
        "selected_mode_raw_rt_range_min": "",
        "selected_mode_normalized_rt_range_min": "",
        "family_raw_rt_range_min": "",
        "family_normalized_rt_range_min": "",
        "reason": "rt_mode_assignment_missing",
        "diagnostic_only": "TRUE",
    }
    validate_row_tokens(row)
    return row


def _tag_supported_samples(
    rows: Sequence[Mapping[str, str]],
) -> frozenset[tuple[str, str]]:
    return frozenset(
        (row["feature_family_id"], row["sample_stem"])
        for row in rows
        if _candidate_ms2_has_required_tag(row)
    )


def _candidate_ms2_has_required_tag(row: Mapping[str, str]) -> bool:
    if row.get("candidate_ms2_pattern_status") not in _SUPPORTIVE_MS2_STATUSES:
        return False
    if row.get("candidate_ms2_evidence_level") not in _OBSERVED_MS2_LEVELS:
        return False
    for field in (
        "raw_ms2_strict_nl_scan_count",
        "matched_neutral_loss_count",
        "source_matched_tag_count",
    ):
        count = optional_float(row.get(field))
        if count is not None and count >= 1:
            return True
    return False


def _mode_id(row: Mapping[str, str]) -> str:
    return _first_text(
        row,
        ("irt_refined_cluster", "irt_cluster", "mode_id", "raw_selected_cluster"),
    )


def _family_id(row: Mapping[str, str], fallback: str) -> str:
    return text_value(row.get("feature_family_id")) or fallback


def _evidence_level(row: Mapping[str, str]) -> str:
    if text_value(row.get("normalized_cell_apex_rt")) or text_value(
        row.get("norm_apex_rt")
    ):
        return "irt_selected_apex_modes"
    return "raw_selected_apex_modes"


def _first_text(row: Mapping[str, object], fields: Sequence[str]) -> str:
    for field in fields:
        value = text_value(row.get(field))
        if value:
            return value
    return ""


def _optional_values(
    rows: Sequence[Mapping[str, str]],
    fields: Sequence[str],
) -> tuple[float, ...]:
    values: list[float] = []
    for row in rows:
        value = optional_float(_first_text(row, fields))
        if value is not None:
            values.append(value)
    return tuple(values)


def _range_text(values: Sequence[float]) -> str:
    if len(values) < 2:
        return "0" if values else ""
    return f"{max(values) - min(values):.6g}"


def _counts_text(values: Iterable[object]) -> str:
    counts = Counter(text_value(value) for value in values if text_value(value))
    return ";".join(f"{label}:{counts[label]}" for label in sorted(counts))
