"""In-memory identity and content contracts shared by Pt trace artifacts.

The manifest binds candidate assignments and RAW/settings fingerprints.
Callers supply file digests; this module performs no artifact I/O and
grants no product authority.
"""

from __future__ import annotations

import math
import re
from collections.abc import Collection, Mapping, Sequence
from dataclasses import dataclass
from typing import Literal

from xic_extractor.discovery.pt_pattern_artifact_contract import (
    PT_PATTERN_CANDIDATE_ASSIGNMENT_SHA256_FIELD,
    PT_PATTERN_RAW_TRACE_BASIS,
    PT_PATTERN_TRACE_ANCHOR_LOCAL_ROLE,
    PT_PATTERN_TRACE_ARTIFACT_ROLE_FIELD,
    PT_PATTERN_TRACE_BUNDLE_MANIFEST_VERSION,
    PT_PATTERN_TRACE_EXTRACTION_SHA256_FIELD,
    PT_PATTERN_TRACE_SCHEMA_ROLE,
    pt_pattern_trace_extraction_fingerprint,
)
from xic_extractor.tabular_io import (
    format_diagnostic_value,
    optional_float,
)

_SHA256_PATTERN = re.compile(r"[0-9a-f]{64}")


@dataclass(frozen=True, slots=True)
class VerifiedTraceManifestIdentity:
    schema_version: str
    artifact_role: str
    trace_extraction_sha256: str
    candidates_csv_sha256: str
    raw_sha256: str
    source_sample_stem: str
    candidate_assignments: tuple[tuple[str, str], ...]


def trace_bundle_manifest_fields(
    *,
    schema_version: str,
    artifact_role: str,
    trace_extraction_sha256: str,
    candidates_csv_sha256: str,
    summary_csv_sha256: str,
    metrics_csv_sha256: str,
    trace_points_csv_sha256: str | None,
    candidate_assignments: Sequence[tuple[str, str]],
) -> dict[str, object]:
    """Build the integrity fields added to a trace diagnosis sidecar."""

    _validate_schema_role(schema_version, artifact_role)
    _require_sha256(trace_extraction_sha256, label="trace_extraction_sha256")
    _require_sha256(candidates_csv_sha256, label="candidates_csv_sha256")
    normalized_assignments = _normalized_candidate_assignments(
        candidate_assignments,
        label="trace manifest candidate assignments",
    )
    return {
        "trace_bundle_manifest_version": PT_PATTERN_TRACE_BUNDLE_MANIFEST_VERSION,
        "trace_artifact_role": artifact_role,
        "trace_extraction_sha256": trace_extraction_sha256,
        "candidates_csv_sha256": candidates_csv_sha256,
        "candidate_assignments": [
            {
                "candidate_id": candidate_id,
                "candidate_assignment_sha256": assignment_sha256,
            }
            for candidate_id, assignment_sha256 in normalized_assignments
        ],
        "output_sha256": {
            "summary_csv": summary_csv_sha256.lower(),
            "metrics_csv": metrics_csv_sha256.lower(),
            "trace_points_csv": (
                trace_points_csv_sha256.lower()
                if trace_points_csv_sha256 is not None
                else None
            ),
        },
        "output_schema_versions": {
            "summary_csv": schema_version,
            "metrics_csv": schema_version,
            "trace_points_csv": schema_version
            if trace_points_csv_sha256 is not None
            else None,
        },
    }


def validate_trace_manifest_identity(
    manifest: Mapping[str, object],
    *,
    expected_artifact_role: str,
) -> VerifiedTraceManifestIdentity:
    """Validate role, candidate slice, settings, and extraction fingerprint."""

    manifest_version = _required_text(
        manifest,
        "trace_bundle_manifest_version",
        label="trace manifest",
    )
    if manifest_version != PT_PATTERN_TRACE_BUNDLE_MANIFEST_VERSION:
        raise ValueError(
            f"unsupported trace bundle manifest version: {manifest_version!r}"
        )
    schema_version = _required_text(manifest, "schema_version", label="trace manifest")
    artifact_role = _required_text(
        manifest,
        PT_PATTERN_TRACE_ARTIFACT_ROLE_FIELD,
        label="trace manifest",
    )
    if artifact_role != expected_artifact_role:
        raise ValueError(
            "trace manifest artifact role mismatch: "
            f"expected {expected_artifact_role!r}, observed {artifact_role!r}"
        )
    _validate_schema_role(schema_version, artifact_role)
    if manifest.get("diagnostic_only") is not True:
        raise ValueError("trace manifest diagnostic_only must be true")

    trace_extraction_sha256 = _required_sha256_field(
        manifest,
        PT_PATTERN_TRACE_EXTRACTION_SHA256_FIELD,
        label="trace manifest",
    )
    candidates_csv_sha256 = _required_sha256_field(
        manifest,
        "candidates_csv_sha256",
        label="trace manifest",
    )
    raw_sha256 = _required_sha256_field(
        manifest,
        "raw_sha256",
        label="trace manifest",
    )
    manifest_assignments_raw = manifest.get("candidate_assignments")
    if not isinstance(manifest_assignments_raw, list):
        raise ValueError("trace manifest candidate_assignments must be a list")
    manifest_assignments = _normalized_candidate_assignments(
        tuple(
            (
                _required_text(item, "candidate_id", label="trace manifest assignment"),
                _required_sha256_field(
                    item,
                    PT_PATTERN_CANDIDATE_ASSIGNMENT_SHA256_FIELD,
                    label="trace manifest assignment",
                ),
            )
            for item in manifest_assignments_raw
            if isinstance(item, Mapping)
        ),
        label="trace manifest candidate assignments",
    )
    if len(manifest_assignments) != len(manifest_assignments_raw):
        raise ValueError("trace manifest candidate_assignments entries must be objects")
    source_sample_stem = _required_text(
        manifest,
        "source_sample_stem",
        label="trace manifest",
    )
    _validate_trace_setting_domains(manifest)
    expected_fingerprint = pt_pattern_trace_extraction_fingerprint(
        candidates_csv_sha256=candidates_csv_sha256,
        raw_sha256=raw_sha256,
        source_sample_stem=source_sample_stem,
        limit=_required_int(manifest, "limit", minimum=1),
        offset=_required_int(manifest, "offset", minimum=0),
        candidate_assignments=manifest_assignments,
        ppm=_required_number(manifest, "ppm"),
        rt_half_window_sec=_required_number(manifest, "rt_half_window_sec"),
        mz_source=_required_mz_source(manifest),
        active_fraction=_required_number(manifest, "active_fraction"),
        min_pearson_r=_required_number(manifest, "min_pearson_r"),
        max_apex_delta_sec=_required_number(manifest, "max_apex_delta_sec"),
        trace_decision_basis=_required_text(
            manifest,
            "trace_decision_basis",
            label="trace manifest",
        ),
        trace_smoothing_method=_required_text(
            manifest,
            "trace_smoothing_method",
            label="trace manifest",
        ),
        trace_smoothing_window_points=_required_int(
            manifest,
            "trace_smoothing_window_points",
            minimum=1,
        ),
        anchor_segmentation_selector=_required_text(
            manifest,
            "anchor_segmentation_selector",
            label="trace manifest",
        ),
    )
    if expected_fingerprint != trace_extraction_sha256:
        raise ValueError("trace manifest trace_extraction_sha256 is stale")
    return VerifiedTraceManifestIdentity(
        schema_version=schema_version,
        artifact_role=artifact_role,
        trace_extraction_sha256=trace_extraction_sha256,
        candidates_csv_sha256=candidates_csv_sha256,
        raw_sha256=raw_sha256,
        source_sample_stem=source_sample_stem,
        candidate_assignments=tuple(manifest_assignments),
    )


def validate_trace_row_bundle(
    *,
    summary_rows: Sequence[Mapping[str, str]],
    metric_rows: Sequence[Mapping[str, str]],
    schema_version: str,
    artifact_role: str,
) -> str:
    """Validate in-memory summary/metric lineage before file-level manifest I/O."""

    _validate_schema_role(schema_version, artifact_role)
    if not summary_rows:
        raise ValueError("trace summary must contain at least one candidate row")
    extraction_values = {
        str(row.get(PT_PATTERN_TRACE_EXTRACTION_SHA256_FIELD, "")).strip()
        for row in summary_rows
    }
    if len(extraction_values) != 1:
        raise ValueError("trace summary rows must use one trace_extraction_sha256")
    trace_extraction_sha256 = next(iter(extraction_values))
    _require_sha256(
        trace_extraction_sha256,
        label="trace summary trace_extraction_sha256",
    )
    identities = _validate_lineage_rows(
        summary_rows,
        schema_version=schema_version,
        artifact_role=artifact_role,
        trace_extraction_sha256=trace_extraction_sha256,
        label="trace summary",
    )
    if len(identities) != len(summary_rows):
        raise ValueError("trace summary candidate_id values must be unique")
    _validate_lineage_rows(
        metric_rows,
        schema_version=schema_version,
        artifact_role=artifact_role,
        trace_extraction_sha256=trace_extraction_sha256,
        expected_identities=identities,
        label="trace metrics",
    )
    _validate_metric_coverage(
        summary_candidate_ids=set(identities),
        metric_rows=metric_rows,
    )
    return trace_extraction_sha256


def validate_cross_role_trace_request_identity(
    reference_metric_rows: Sequence[Mapping[str, str]],
    candidate_metric_rows: Sequence[Mapping[str, str]],
) -> None:
    """Bind full-context and anchor-local traces to the same m/z requests."""

    def request_by_pair(
        rows: Sequence[Mapping[str, str]],
    ) -> dict[tuple[str, str], tuple[str, str]]:
        output: dict[tuple[str, str], tuple[str, str]] = {}
        for row in rows:
            pair = (
                str(row.get("candidate_id", "")).strip(),
                str(row.get("position_label", "")).strip(),
            )
            if pair in output:
                raise ValueError("trace metrics contain duplicate candidate/position")
            output[pair] = (
                str(row.get("target_mz", "")).strip(),
                str(row.get("ppm", "")).strip(),
            )
        return output

    expected = request_by_pair(reference_metric_rows)
    observed = request_by_pair(candidate_metric_rows)
    if observed != expected:
        raise ValueError(
            "full-context and anchor-local trace metrics do not use the same "
            "candidate/position target_mz and ppm requests"
        )


_TRACE_POSITION_LABELS = ("base", "pt_plus_1", "pt_plus_2", "pt_plus_4")


_TRACE_METRIC_NUMERIC_COLUMNS = (
    "target_mz",
    "rt_min",
    "rt_max",
    "ppm",
    "trace_smoothing_window_points",
    "point_count",
    "median_rt_step_sec",
    "effective_smoothing_window_sec",
    "raw_max_intensity",
    "raw_apex_rt_min",
    "raw_apex_delta_sec_to_base",
    "raw_smoothed_apex_delta_sec",
    "raw_height_ratio_to_base",
    "raw_pearson_r_to_base",
    "raw_active_overlap_fraction",
    "max_intensity",
    "apex_rt_min",
    "apex_delta_sec_to_base",
    "height_ratio_to_base",
    "pearson_r_to_base",
    "active_overlap_fraction",
    "shifted_pearson_r_to_base",
    "shape_shift_sec_to_base",
)


_TRACE_METRIC_NON_NEGATIVE_COLUMNS = {
    "target_mz",
    "rt_min",
    "rt_max",
    "ppm",
    "trace_smoothing_window_points",
    "point_count",
    "median_rt_step_sec",
    "effective_smoothing_window_sec",
    "raw_max_intensity",
    "raw_apex_rt_min",
    "raw_height_ratio_to_base",
    "raw_active_overlap_fraction",
    "max_intensity",
    "apex_rt_min",
    "height_ratio_to_base",
    "active_overlap_fraction",
}


_TRACE_METRIC_UNIT_INTERVAL_COLUMNS = {
    "raw_active_overlap_fraction",
    "active_overlap_fraction",
}


_TRACE_METRIC_CORRELATION_COLUMNS = {
    "raw_pearson_r_to_base",
    "pearson_r_to_base",
    "shifted_pearson_r_to_base",
}


_ANCHOR_METRIC_NUMERIC_COLUMNS = (
    "anchor_segment_rt_start_min",
    "anchor_segment_rt_end_min",
    "anchor_segment_duration_sec",
    "anchor_segment_left_clip_sec",
    "anchor_segment_right_clip_sec",
    "anchor_segment_common_retained_fraction",
    "anchor_segment_common_left_residual_fraction",
    "anchor_segment_common_right_residual_fraction",
)


_ANCHOR_METRIC_NON_NEGATIVE_COLUMNS = {
    "anchor_segment_rt_start_min",
    "anchor_segment_rt_end_min",
    "anchor_segment_duration_sec",
    "anchor_segment_left_clip_sec",
    "anchor_segment_right_clip_sec",
    "anchor_segment_common_retained_fraction",
}


def _validate_trace_point_values(
    rows: Sequence[Mapping[str, str]],
) -> None:
    rt_by_pair: dict[tuple[str, str], list[float]] = {}
    for row_index, row in enumerate(rows, start=2):
        if str(row.get("intensity_basis", "")).strip() != PT_PATTERN_RAW_TRACE_BASIS:
            raise ValueError(
                f"trace points row {row_index}: intensity_basis must be "
                f"{PT_PATTERN_RAW_TRACE_BASIS!r}"
            )
        rt_min = optional_float(row.get("rt_min"))
        intensity = optional_float(row.get("intensity"))
        if (
            rt_min is None
            or intensity is None
            or not math.isfinite(rt_min)
            or not math.isfinite(intensity)
            or rt_min < 0.0
            or intensity < 0.0
        ):
            raise ValueError(
                f"trace points row {row_index}: rt_min/intensity must be finite "
                "non-negative numbers"
            )
        pair = (
            str(row.get("candidate_id", "")).strip(),
            str(row.get("position_label", "")).strip(),
        )
        rt_by_pair.setdefault(pair, []).append(rt_min)
    for pair, rt_values in rt_by_pair.items():
        if any(
            later <= earlier
            for earlier, later in zip(rt_values, rt_values[1:], strict=False)
        ):
            raise ValueError(
                f"trace points {pair[0]}:{pair[1]} rt_min must be strictly "
                "increasing and unique"
            )


def _required_non_negative_integer_text(value: object, *, label: str) -> int:
    text = str(value or "").strip()
    try:
        parsed = float(text)
    except ValueError as exc:
        raise ValueError(f"{label} must be a non-negative integer") from exc
    if not math.isfinite(parsed) or parsed < 0.0 or not parsed.is_integer():
        raise ValueError(f"{label} must be a non-negative integer")
    return int(parsed)


def _validate_metric_numeric_domains(
    rows: Sequence[Mapping[str, str]],
    *,
    artifact_role: str,
) -> None:
    numeric_columns: tuple[str, ...] = _TRACE_METRIC_NUMERIC_COLUMNS
    non_negative_columns = _TRACE_METRIC_NON_NEGATIVE_COLUMNS
    if artifact_role == PT_PATTERN_TRACE_ANCHOR_LOCAL_ROLE:
        numeric_columns += _ANCHOR_METRIC_NUMERIC_COLUMNS
        non_negative_columns = (
            non_negative_columns | _ANCHOR_METRIC_NON_NEGATIVE_COLUMNS
        )
    for row_index, row in enumerate(rows, start=2):
        parsed: dict[str, float] = {}
        for field in numeric_columns:
            raw_value = str(row.get(field, "")).strip()
            if not raw_value:
                continue
            try:
                value = float(raw_value)
            except ValueError as exc:
                raise ValueError(
                    f"trace metrics row {row_index}: {field} must be a finite "
                    "numeric value when supplied"
                ) from exc
            if not math.isfinite(value):
                raise ValueError(
                    f"trace metrics row {row_index}: {field} must be a finite "
                    "numeric value when supplied"
                )
            parsed[field] = value
            if field in non_negative_columns and value < 0.0:
                raise ValueError(
                    f"trace metrics row {row_index}: {field} must be non-negative"
                )
            if field in _TRACE_METRIC_UNIT_INTERVAL_COLUMNS and value > 1.0:
                raise ValueError(
                    f"trace metrics row {row_index}: {field} must be between 0 and 1"
                )
            if field in _TRACE_METRIC_CORRELATION_COLUMNS and not (
                -1.0 <= value <= 1.0
            ):
                raise ValueError(
                    f"trace metrics row {row_index}: {field} must be between -1 and 1"
                )
        rt_min = parsed.get("rt_min")
        rt_max = parsed.get("rt_max")
        if rt_min is not None and rt_max is not None and rt_min > rt_max:
            raise ValueError(
                f"trace metrics row {row_index}: rt_min must not exceed rt_max"
            )
        segment_start = parsed.get("anchor_segment_rt_start_min")
        segment_end = parsed.get("anchor_segment_rt_end_min")
        if (
            segment_start is not None
            and segment_end is not None
            and segment_start >= segment_end
        ):
            raise ValueError(
                f"trace metrics row {row_index}: anchor segment interval must "
                "have positive duration"
            )


def _validate_rows_against_manifest_semantics(
    rows: Sequence[Mapping[str, str]],
    *,
    manifest: Mapping[str, object],
    source_sample_stem: str,
    label: str,
) -> None:
    expected_values = {
        "ppm": format_diagnostic_value(
            _required_number(manifest, "ppm")
        ),
        "trace_decision_basis": _required_text(
            manifest,
            "trace_decision_basis",
            label="trace manifest",
        ),
        "trace_smoothing_method": _required_text(
            manifest,
            "trace_smoothing_method",
            label="trace manifest",
        ),
        "trace_smoothing_window_points": str(
            _required_int(
                manifest,
                "trace_smoothing_window_points",
                minimum=1,
            )
        ),
    }
    for row_index, row in enumerate(rows, start=2):
        if str(row.get("sample_stem", "")).strip() != source_sample_stem:
            raise ValueError(
                f"{label} row {row_index}: sample_stem does not match trace manifest"
            )
        for field, expected in expected_values.items():
            if str(row.get(field, "")).strip() != expected:
                raise ValueError(
                    f"{label} row {row_index}: {field} does not match trace manifest"
                )


def _validate_summary_rows_against_manifest_semantics(
    rows: Sequence[Mapping[str, str]],
    *,
    manifest: Mapping[str, object],
) -> None:
    expected_values = {
        "scan_pattern_seed_status": "full_pattern_seeded",
        "rt_half_window_sec": format_diagnostic_value(
            _required_number(manifest, "rt_half_window_sec")
        ),
        "mz_source": _required_mz_source(manifest),
    }
    for row_index, row in enumerate(rows, start=2):
        for field, expected in expected_values.items():
            if str(row.get(field, "")).strip() != expected:
                raise ValueError(
                    f"trace summary row {row_index}: {field} does not match "
                    "trace manifest semantics"
                )


def _validate_summary_numeric_domains(
    rows: Sequence[Mapping[str, str]],
) -> None:
    for row_index, row in enumerate(rows, start=2):
        try:
            base_mz = _optional_finite_row_number(
                row,
                "base_mz",
                label=f"trace summary row {row_index}",
            )
        except ValueError as exc:
            raise ValueError(
                f"trace summary row {row_index}: base_mz must be finite and "
                "positive"
            ) from exc
        try:
            base_rt = _optional_finite_row_number(
                row,
                "base_rt_apex_min",
                label=f"trace summary row {row_index}",
            )
        except ValueError as exc:
            raise ValueError(
                f"trace summary row {row_index}: base_rt_apex_min must be finite "
                "and non-negative"
            ) from exc
        if base_mz is None or base_mz <= 0.0:
            raise ValueError(
                f"trace summary row {row_index}: base_mz must be finite and positive"
            )
        if base_rt is None or base_rt < 0.0:
            raise ValueError(
                f"trace summary row {row_index}: base_rt_apex_min must be finite "
                "and non-negative"
            )


def _validate_metric_coverage(
    *,
    summary_candidate_ids: set[str],
    metric_rows: Sequence[Mapping[str, str]],
) -> None:
    observed_pairs: set[tuple[str, str]] = set()
    duplicates: list[str] = []
    for row_index, row in enumerate(metric_rows, start=2):
        candidate_id = str(row.get("candidate_id", "")).strip()
        position_label = str(row.get("position_label", "")).strip()
        if position_label not in _TRACE_POSITION_LABELS:
            raise ValueError(
                f"trace metrics row {row_index}: unsupported position_label "
                f"{position_label!r}"
            )
        pair = (candidate_id, position_label)
        if pair in observed_pairs:
            duplicates.append(f"{candidate_id}:{position_label}")
        observed_pairs.add(pair)
    if duplicates:
        raise ValueError(
            "duplicate trace metric candidate/position values: "
            + ", ".join(sorted(set(duplicates)))
        )
    expected_pairs = {
        (candidate_id, position_label)
        for candidate_id in summary_candidate_ids
        for position_label in _TRACE_POSITION_LABELS
    }
    if observed_pairs != expected_pairs:
        missing = sorted(expected_pairs - observed_pairs)
        unexpected = sorted(observed_pairs - expected_pairs)
        raise ValueError(
            "trace metric candidate/position coverage mismatch; "
            f"missing={missing!r}; unexpected={unexpected!r}"
        )


def _optional_finite_row_number(
    row: Mapping[str, str],
    field: str,
    *,
    label: str,
) -> float | None:
    raw_value = str(row.get(field, "")).strip()
    if not raw_value:
        return None
    try:
        value = float(raw_value)
    except ValueError as exc:
        raise ValueError(f"{label} {field} must be finite") from exc
    if not math.isfinite(value):
        raise ValueError(f"{label} {field} must be finite")
    return value


def _required_finite_row_number(
    row: Mapping[str, str],
    field: str,
    *,
    label: str,
) -> float:
    value = _optional_finite_row_number(row, field, label=label)
    if value is None:
        raise ValueError(f"{label} {field} must be supplied")
    return value


def _required_projected_number(
    row: Mapping[str, object],
    field: str,
) -> float:
    value = optional_float(row.get(field))
    if value is None:
        raise ValueError(f"anchor projection {field} must be finite")
    return value


def _validate_trace_point_coverage(
    rows: Sequence[Mapping[str, str]],
    *,
    required_complete_candidate_ids: Collection[str],
) -> None:
    observed: dict[str, set[str]] = {}
    for row_index, row in enumerate(rows, start=2):
        candidate_id = str(row.get("candidate_id", "")).strip()
        position_label = str(row.get("position_label", "")).strip()
        if position_label not in _TRACE_POSITION_LABELS:
            raise ValueError(
                f"trace points row {row_index}: unsupported position_label "
                f"{position_label!r}"
            )
        observed.setdefault(candidate_id, set()).add(position_label)
    expected_positions = set(_TRACE_POSITION_LABELS)
    for candidate_id in sorted(required_complete_candidate_ids):
        positions = observed.get(candidate_id, set())
        if positions != expected_positions:
            raise ValueError(
                f"{candidate_id}: accept-capable review packet requires trace "
                f"points for every Pt position; missing="
                f"{sorted(expected_positions - positions)!r}"
            )


def _validate_lineage_rows(
    rows: Sequence[Mapping[str, str]],
    *,
    schema_version: str,
    artifact_role: str,
    trace_extraction_sha256: str,
    label: str,
    expected_identities: Mapping[str, tuple[str, str]] | None = None,
) -> dict[str, tuple[str, str]]:
    identities: dict[str, tuple[str, str]] = {}
    for row_index, row in enumerate(rows, start=2):
        if str(row.get("schema_version", "")).strip() != schema_version:
            raise ValueError(f"{label} row {row_index}: schema_version mismatch")
        if (
            str(row.get(PT_PATTERN_TRACE_ARTIFACT_ROLE_FIELD, "")).strip()
            != artifact_role
        ):
            raise ValueError(f"{label} row {row_index}: trace_artifact_role mismatch")
        if (
            str(row.get(PT_PATTERN_TRACE_EXTRACTION_SHA256_FIELD, "")).strip()
            != trace_extraction_sha256
        ):
            raise ValueError(
                f"{label} row {row_index}: trace_extraction_sha256 mismatch"
            )
        candidate_id = str(row.get("candidate_id", "")).strip()
        sample_stem = str(row.get("sample_stem", "")).strip()
        if not candidate_id or not sample_stem:
            raise ValueError(
                f"{label} row {row_index}: candidate_id/sample_stem must be non-empty"
            )
        assignment_sha256 = str(
            row.get(PT_PATTERN_CANDIDATE_ASSIGNMENT_SHA256_FIELD, "")
        ).strip()
        _require_sha256(
            assignment_sha256,
            label=f"{label} row {row_index} candidate_assignment_sha256",
        )
        identity = (assignment_sha256, sample_stem)
        if expected_identities is not None:
            expected_identity = expected_identities.get(candidate_id)
            if expected_identity is None:
                raise ValueError(
                    f"{label} row {row_index}: candidate_id is outside trace summary"
                )
            if assignment_sha256 != expected_identity[0]:
                raise ValueError(
                    f"{label} row {row_index}: candidate_assignment_sha256 mismatch"
                )
            if sample_stem != expected_identity[1]:
                raise ValueError(f"{label} row {row_index}: sample_stem mismatch")
        prior_identity = identities.setdefault(candidate_id, identity)
        if prior_identity != identity:
            if prior_identity[0] != assignment_sha256:
                raise ValueError(
                    f"{label} row {row_index}: mixed candidate_assignment_sha256"
                )
            raise ValueError(f"{label} row {row_index}: mixed sample_stem")
    return identities


def _validate_schema_role(schema_version: str, artifact_role: str) -> None:
    expected_role = PT_PATTERN_TRACE_SCHEMA_ROLE.get(schema_version)
    if expected_role is None:
        raise ValueError(f"unsupported trace schema_version: {schema_version!r}")
    if expected_role != artifact_role:
        raise ValueError(
            f"trace schema {schema_version!r} requires role {expected_role!r}, "
            f"not {artifact_role!r}"
        )


def _normalized_candidate_assignments(
    assignments: Sequence[tuple[str, str]],
    *,
    label: str,
) -> list[tuple[str, str]]:
    normalized: list[tuple[str, str]] = []
    seen: set[str] = set()
    for candidate_id, assignment_sha256 in assignments:
        normalized_id = str(candidate_id).strip()
        if not normalized_id:
            raise ValueError(f"{label}: candidate_id must be non-empty")
        if normalized_id in seen:
            raise ValueError(f"{label}: duplicate candidate_id {normalized_id}")
        seen.add(normalized_id)
        normalized_sha256 = str(assignment_sha256).strip()
        _require_sha256(
            normalized_sha256,
            label=f"{label} candidate_assignment_sha256",
        )
        normalized.append((normalized_id, normalized_sha256))
    return sorted(normalized)


def _required_text(
    row: Mapping[str, object],
    field: str,
    *,
    label: str,
) -> str:
    raw_value = row.get(field)
    if not isinstance(raw_value, str) or not raw_value.strip():
        raise ValueError(f"{label} {field} must be a non-empty string")
    return raw_value.strip()


def _required_sha256_field(
    row: Mapping[str, object],
    field: str,
    *,
    label: str,
) -> str:
    value = _required_text(row, field, label=label)
    _require_sha256(value, label=f"{label} {field}")
    return value


def _require_sha256(value: str, *, label: str) -> None:
    if _SHA256_PATTERN.fullmatch(value) is None:
        raise ValueError(f"{label} must be a lowercase SHA-256 digest")


def _required_int(
    row: Mapping[str, object],
    field: str,
    *,
    minimum: int,
) -> int:
    value = row.get(field)
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise ValueError(f"trace manifest {field} must be an integer >= {minimum}")
    return value


def _required_number(row: Mapping[str, object], field: str) -> float:
    value = row.get(field)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"trace manifest {field} must be a finite number")
    parsed = float(value)
    if not (float("-inf") < parsed < float("inf")):
        raise ValueError(f"trace manifest {field} must be a finite number")
    return parsed


def _required_mz_source(
    manifest: Mapping[str, object],
) -> Literal["expected", "observed"]:
    value = _required_text(manifest, "mz_source", label="trace manifest")
    if value == "expected":
        return "expected"
    if value == "observed":
        return "observed"
    raise ValueError(
        "trace manifest mz_source must be 'expected' or 'observed'"
    )


def _validate_trace_setting_domains(manifest: Mapping[str, object]) -> None:
    ppm = _required_number(manifest, "ppm")
    rt_half_window_sec = _required_number(manifest, "rt_half_window_sec")
    active_fraction = _required_number(manifest, "active_fraction")
    min_pearson_r = _required_number(manifest, "min_pearson_r")
    max_apex_delta_sec = _required_number(manifest, "max_apex_delta_sec")
    if ppm <= 0.0:
        raise ValueError("trace manifest ppm must be > 0")
    if rt_half_window_sec <= 0.0:
        raise ValueError("trace manifest rt_half_window_sec must be > 0")
    if not 0.0 < active_fraction <= 1.0:
        raise ValueError("trace manifest active_fraction must be in (0, 1]")
    if not -1.0 <= min_pearson_r <= 1.0:
        raise ValueError("trace manifest min_pearson_r must be in [-1, 1]")
    if max_apex_delta_sec < 0.0:
        raise ValueError("trace manifest max_apex_delta_sec must be >= 0")
