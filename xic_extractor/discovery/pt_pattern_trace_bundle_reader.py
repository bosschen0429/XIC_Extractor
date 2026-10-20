"""Read and verify persisted Pt trace bundles using shared manifest contracts.

File integrity and replay against the existing evidence owners live here;
trace producers depend only on the in-memory artifact and manifest contracts.
"""

from __future__ import annotations

import json
import math
from collections.abc import Collection, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from xic_extractor.discovery.pt_pattern_artifact_contract import (
    PT_PATTERN_ANCHOR_LOCAL_METRIC_COLUMNS,
    PT_PATTERN_ANCHOR_LOCAL_SUMMARY_COLUMNS,
    PT_PATTERN_ANCHOR_SEGMENTATION_METRIC_COLUMNS,
    PT_PATTERN_ANCHOR_SEGMENTATION_SUMMARY_COLUMNS,
    PT_PATTERN_RAW_TRACE_BASIS,
    PT_PATTERN_TRACE_ANCHOR_LOCAL_ROLE,
    PT_PATTERN_TRACE_FULL_CONTEXT_ROLE,
    PT_PATTERN_TRACE_METRIC_COLUMNS,
    PT_PATTERN_TRACE_SCHEMA_ROLE,
    PT_PATTERN_TRACE_SUMMARY_COLUMNS,
    pt_pattern_candidate_assignment_fingerprint,
    validate_pt_candidate_trace_summary_identity,
)
from xic_extractor.discovery.pt_pattern_trace_manifest import (
    VerifiedTraceManifestIdentity,
    _optional_finite_row_number,
    _required_finite_row_number,
    _required_int,
    _required_mz_source,
    _required_non_negative_integer_text,
    _required_number,
    _required_projected_number,
    _required_sha256_field,
    _required_text,
    _validate_lineage_rows,
    _validate_metric_coverage,
    _validate_metric_numeric_domains,
    _validate_rows_against_manifest_semantics,
    _validate_summary_numeric_domains,
    _validate_summary_rows_against_manifest_semantics,
    _validate_trace_point_coverage,
    _validate_trace_point_values,
    validate_trace_manifest_identity,
)
from xic_extractor.tabular_io import (
    file_sha256,
    format_diagnostic_value,
    read_delimited_rows,
)

_MANIFEST_FILE_BY_ROLE = {
    PT_PATTERN_TRACE_FULL_CONTEXT_ROLE: "pt_pattern_context_trace_diagnosis.json",
    PT_PATTERN_TRACE_ANCHOR_LOCAL_ROLE: "pt_pattern_trace_diagnosis.json",
}


_METRICS_FILE_BY_ROLE = {
    PT_PATTERN_TRACE_FULL_CONTEXT_ROLE: "pt_pattern_context_trace_metrics.csv",
    PT_PATTERN_TRACE_ANCHOR_LOCAL_ROLE: "pt_pattern_trace_metrics.csv",
}


_SUMMARY_FILE_BY_ROLE = {
    PT_PATTERN_TRACE_FULL_CONTEXT_ROLE: "pt_pattern_context_trace_summary.csv",
    PT_PATTERN_TRACE_ANCHOR_LOCAL_ROLE: "pt_pattern_trace_summary.csv",
}


@dataclass(frozen=True, slots=True)
class VerifiedTraceSummaryBundle:
    summary_csv: Path
    metrics_csv: Path
    manifest_json: Path
    schema_version: str
    trace_artifact_role: str
    trace_extraction_sha256: str
    summary_rows: tuple[dict[str, str], ...]
    metric_rows: tuple[dict[str, str], ...]


def verified_trace_summary_identities(
    bundle: VerifiedTraceSummaryBundle,
) -> dict[str, tuple[str, str]]:
    """Return the complete candidate identity map for a verified trace bundle."""

    identities = _validate_lineage_rows(
        bundle.summary_rows,
        schema_version=bundle.schema_version,
        artifact_role=bundle.trace_artifact_role,
        trace_extraction_sha256=bundle.trace_extraction_sha256,
        label="verified trace summary",
    )
    if len(identities) != len(bundle.summary_rows):
        raise ValueError("verified trace summary candidate_id values must be unique")
    return identities


def default_trace_manifest_path(
    artifact_path: Path,
    *,
    artifact_role: str,
) -> Path:
    """Return the standard sibling manifest path for a trace artifact."""

    try:
        file_name = _MANIFEST_FILE_BY_ROLE[artifact_role]
    except KeyError as exc:
        raise ValueError(f"unsupported trace artifact role: {artifact_role!r}") from exc
    return artifact_path.parent / file_name


def default_trace_metrics_path(
    artifact_path: Path,
    *,
    artifact_role: str,
) -> Path:
    """Return the standard sibling metrics path for a trace summary."""

    try:
        file_name = _METRICS_FILE_BY_ROLE[artifact_role]
    except KeyError as exc:
        raise ValueError(f"unsupported trace artifact role: {artifact_role!r}") from exc
    return artifact_path.parent / file_name


def default_trace_summary_path(
    artifact_path: Path,
    *,
    artifact_role: str,
) -> Path:
    """Return the standard sibling summary path for a trace artifact."""

    try:
        file_name = _SUMMARY_FILE_BY_ROLE[artifact_role]
    except KeyError as exc:
        raise ValueError(f"unsupported trace artifact role: {artifact_role!r}") from exc
    return artifact_path.parent / file_name


def load_verified_trace_summary_bundle(
    summary_csv: Path,
    *,
    expected_artifact_role: str | None = None,
    metrics_csv: Path | None = None,
    manifest_json: Path | None = None,
    candidates_csv: Path | None = None,
) -> VerifiedTraceSummaryBundle:
    """Load a current trace summary only through its sealed bundle contract."""

    resolved_role = expected_artifact_role
    if resolved_role is None:
        discriminator_rows = tuple(
            read_delimited_rows(
                summary_csv,
                required_columns=("schema_version",),
                delimiter=",",
                encoding="utf-8-sig",
            )
        )
        if not discriminator_rows:
            raise ValueError("trace summary must contain at least one candidate row")
        schemas = {
            str(row.get("schema_version", "")).strip() for row in discriminator_rows
        }
        if len(schemas) != 1:
            raise ValueError("trace summary rows must use one schema_version")
        schema_version = next(iter(schemas))
        resolved_role = PT_PATTERN_TRACE_SCHEMA_ROLE.get(schema_version)
        if resolved_role is None:
            raise ValueError(
                f"unsupported trace summary schema_version: {schema_version!r}"
            )

    summary_columns: tuple[str, ...]
    metric_columns: tuple[str, ...]
    if resolved_role == PT_PATTERN_TRACE_FULL_CONTEXT_ROLE:
        summary_columns = PT_PATTERN_TRACE_SUMMARY_COLUMNS
        metric_columns = PT_PATTERN_TRACE_METRIC_COLUMNS
    elif resolved_role == PT_PATTERN_TRACE_ANCHOR_LOCAL_ROLE:
        summary_columns = PT_PATTERN_ANCHOR_LOCAL_SUMMARY_COLUMNS
        metric_columns = PT_PATTERN_ANCHOR_LOCAL_METRIC_COLUMNS
    else:
        raise ValueError(f"unsupported trace artifact role: {resolved_role!r}")
    resolved_metrics = metrics_csv or default_trace_metrics_path(
        summary_csv,
        artifact_role=resolved_role,
    )
    resolved_manifest = manifest_json or default_trace_manifest_path(
        summary_csv,
        artifact_role=resolved_role,
    )
    summary_rows = tuple(
        read_delimited_rows(
            summary_csv,
            required_columns=summary_columns,
            exact_columns=summary_columns,
            delimiter=",",
            encoding="utf-8-sig",
        )
    )
    metric_rows = tuple(
        read_delimited_rows(
            resolved_metrics,
            required_columns=metric_columns,
            exact_columns=metric_columns,
            delimiter=",",
            encoding="utf-8-sig",
        )
    )
    manifest = load_trace_bundle_manifest(resolved_manifest)
    resolved_candidates_csv = candidates_csv or _manifest_candidates_csv_path(
        resolved_manifest,
        manifest,
    )
    trace_extraction_sha256 = validate_trace_bundle_manifest(
        manifest=manifest,
        summary_csv=summary_csv,
        metrics_csv=resolved_metrics,
        summary_rows=summary_rows,
        metric_rows=metric_rows,
        expected_artifact_role=resolved_role,
        candidates_csv=resolved_candidates_csv,
    )
    return VerifiedTraceSummaryBundle(
        summary_csv=summary_csv,
        metrics_csv=resolved_metrics,
        manifest_json=resolved_manifest,
        schema_version=_required_text(
            manifest,
            "schema_version",
            label="trace manifest",
        ),
        trace_artifact_role=resolved_role,
        trace_extraction_sha256=trace_extraction_sha256,
        summary_rows=summary_rows,
        metric_rows=metric_rows,
    )


def _manifest_candidates_csv_path(
    manifest_path: Path,
    manifest: Mapping[str, object],
) -> Path:
    raw_path = _required_text(
        manifest,
        "candidates_csv",
        label="trace manifest",
    )
    candidate_path = Path(raw_path)
    if not candidate_path.is_absolute():
        candidate_path = manifest_path.parent / candidate_path
    candidate_path = candidate_path.resolve()
    if not candidate_path.is_file():
        raise ValueError(
            f"trace manifest candidates_csv does not exist: {candidate_path}"
        )
    return candidate_path


def load_trace_bundle_manifest(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"{path}: trace bundle manifest must be a JSON object")
    return payload


def validate_trace_bundle_manifest(
    *,
    manifest: Mapping[str, object],
    summary_csv: Path,
    metrics_csv: Path,
    summary_rows: Sequence[Mapping[str, str]],
    metric_rows: Sequence[Mapping[str, str]],
    expected_artifact_role: str,
    trace_points_csv: Path | None = None,
    trace_point_rows: Sequence[Mapping[str, str]] | None = None,
    candidates_csv: Path | None = None,
) -> str:
    """Validate bundle hashes, role, row lineage, and extraction fingerprint.

    Returns the verified ``trace_extraction_sha256``.
    """

    identity = validate_trace_manifest_identity(
        manifest,
        expected_artifact_role=expected_artifact_role,
    )
    schema_version = identity.schema_version
    artifact_role = identity.artifact_role
    trace_extraction_sha256 = identity.trace_extraction_sha256
    if (
        candidates_csv is not None
        and file_sha256(candidates_csv).lower() != identity.candidates_csv_sha256
    ):
        raise ValueError("trace manifest candidates_csv_sha256 mismatch")

    if not summary_rows:
        raise ValueError("trace summary must contain at least one candidate row")
    identities = _validate_lineage_rows(
        summary_rows,
        schema_version=schema_version,
        artifact_role=artifact_role,
        trace_extraction_sha256=trace_extraction_sha256,
        label="trace summary",
    )
    _validate_rows_against_manifest_semantics(
        summary_rows,
        manifest=manifest,
        source_sample_stem=identity.source_sample_stem,
        label="trace summary",
    )
    _validate_summary_rows_against_manifest_semantics(
        summary_rows,
        manifest=manifest,
    )
    _validate_summary_numeric_domains(summary_rows)
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
    _validate_rows_against_manifest_semantics(
        metric_rows,
        manifest=manifest,
        source_sample_stem=identity.source_sample_stem,
        label="trace metrics",
    )
    _validate_metric_coverage(
        summary_candidate_ids=set(identities),
        metric_rows=metric_rows,
    )
    if candidates_csv is not None:
        _validate_candidate_trace_projection(
            candidates_csv=candidates_csv,
            summary_rows=summary_rows,
            metric_rows=metric_rows,
            manifest=manifest,
            manifest_identity=identity,
        )
    _validate_metric_numeric_domains(
        metric_rows,
        artifact_role=artifact_role,
    )
    _validate_metric_call_projection(
        metric_rows,
        manifest=manifest,
    )
    _validate_trace_summary_projection(
        summary_rows=summary_rows,
        metric_rows=metric_rows,
        artifact_role=artifact_role,
    )
    if trace_point_rows is not None:
        _validate_lineage_rows(
            trace_point_rows,
            schema_version=schema_version,
            artifact_role=artifact_role,
            trace_extraction_sha256=trace_extraction_sha256,
            expected_identities=identities,
            label="trace points",
        )
        _validate_trace_point_values(trace_point_rows)
        _validate_trace_point_metric_projection(
            trace_point_rows,
            metric_rows=metric_rows,
            artifact_role=artifact_role,
            manifest=manifest,
        )

    if list(identity.candidate_assignments) != sorted(
        (candidate_id, identity[0]) for candidate_id, identity in identities.items()
    ):
        raise ValueError(
            "trace manifest candidate assignments do not match summary rows"
        )

    output_sha256 = manifest.get("output_sha256")
    output_schemas = manifest.get("output_schema_versions")
    if not isinstance(output_sha256, Mapping) or not isinstance(
        output_schemas, Mapping
    ):
        raise ValueError(
            "trace manifest output_sha256/output_schema_versions must be objects"
        )
    _validate_output(
        output_sha256,
        output_schemas,
        key="summary_csv",
        path=summary_csv,
        schema_version=schema_version,
    )
    _validate_output(
        output_sha256,
        output_schemas,
        key="metrics_csv",
        path=metrics_csv,
        schema_version=schema_version,
    )
    if trace_points_csv is not None:
        if manifest.get("trace_points_written") is not True:
            raise ValueError("trace manifest does not declare trace points written")
        _validate_output(
            output_sha256,
            output_schemas,
            key="trace_points_csv",
            path=trace_points_csv,
            schema_version=schema_version,
        )
    return trace_extraction_sha256


def _validate_candidate_trace_projection(
    *,
    candidates_csv: Path,
    summary_rows: Sequence[Mapping[str, str]],
    metric_rows: Sequence[Mapping[str, str]],
    manifest: Mapping[str, object],
    manifest_identity: VerifiedTraceManifestIdentity,
) -> None:
    from xic_extractor.discovery.pt_pattern_trace_evidence import (
        build_trace_requests_for_candidate,
        load_candidate_rows,
        validate_candidate_sample_binding,
    )

    candidate_rows = load_candidate_rows(
        candidates_csv,
        limit=_required_int(manifest, "limit", minimum=1),
        offset=_required_int(manifest, "offset", minimum=0),
    )
    validate_candidate_sample_binding(
        candidate_rows,
        expected_sample_stem=manifest_identity.source_sample_stem,
    )
    candidates_by_id = {str(row["candidate_id"]).strip(): row for row in candidate_rows}
    expected_assignments = sorted(
        (
            candidate_id,
            pt_pattern_candidate_assignment_fingerprint(candidate_row),
        )
        for candidate_id, candidate_row in candidates_by_id.items()
    )
    if expected_assignments != list(manifest_identity.candidate_assignments):
        raise ValueError(
            "trace manifest candidate assignments do not match the recorded "
            "candidate slice"
        )

    summaries_by_id = {
        str(row.get("candidate_id", "")).strip(): row for row in summary_rows
    }
    if set(summaries_by_id) != set(candidates_by_id):
        raise ValueError(
            "trace summary candidate_id values do not match the recorded "
            "candidate slice"
        )
    metrics_by_candidate: dict[str, dict[str, Mapping[str, str]]] = {}
    for metric_row in metric_rows:
        metrics_by_candidate.setdefault(
            str(metric_row.get("candidate_id", "")).strip(),
            {},
        )[str(metric_row.get("position_label", "")).strip()] = metric_row

    ppm = _required_number(manifest, "ppm")
    rt_half_window_sec = _required_number(manifest, "rt_half_window_sec")
    mz_source = _required_mz_source(manifest)
    for candidate_id, candidate_row in candidates_by_id.items():
        validate_pt_candidate_trace_summary_identity(
            candidate_row,
            summaries_by_id[candidate_id],
        )
        request_items = build_trace_requests_for_candidate(
            candidate_row,
            ppm=ppm,
            rt_half_window_sec=rt_half_window_sec,
            mz_source=mz_source,
        )
        candidate_metrics = metrics_by_candidate[candidate_id]
        for request_item in request_items:
            metric_row = candidate_metrics[request_item.position_label]
            observed = str(metric_row.get("target_mz", "")).strip()
            expected = format_diagnostic_value(request_item.target_mz)
            if observed != expected:
                raise ValueError(
                    f"trace metrics {candidate_id}:{request_item.position_label} "
                    "target_mz is not the deterministic projection of the "
                    f"recorded candidate and mz_source: {observed!r} != "
                    f"{expected!r}"
                )
            point_count = _required_non_negative_integer_text(
                metric_row.get("point_count"),
                label=(
                    f"trace metrics {candidate_id}:"
                    f"{request_item.position_label} point_count"
                ),
            )
            rt_min = _optional_finite_row_number(
                metric_row,
                "rt_min",
                label="trace metrics",
            )
            rt_max = _optional_finite_row_number(
                metric_row,
                "rt_max",
                label="trace metrics",
            )
            if point_count == 0:
                if rt_min is not None or rt_max is not None:
                    raise ValueError(
                        f"trace metrics {candidate_id}:"
                        f"{request_item.position_label} must have blank RT bounds "
                        "when point_count is zero"
                    )
                continue
            if rt_min is None or rt_max is None:
                raise ValueError(
                    f"trace metrics {candidate_id}:{request_item.position_label} "
                    "must have RT bounds when point_count is positive"
                )


def validate_trace_points_manifest(
    *,
    manifest: Mapping[str, object],
    trace_points_csv: Path,
    trace_point_rows: Sequence[Mapping[str, str]],
    expected_artifact_role: str,
    expected_bundle_identities: Mapping[str, tuple[str, str]],
    required_complete_candidate_ids: Collection[str] = (),
    expected_metric_rows: Sequence[Mapping[str, str]] | None = None,
) -> str:
    """Validate a point artifact, including header-only files, against manifest."""

    identity = validate_trace_manifest_identity(
        manifest,
        expected_artifact_role=expected_artifact_role,
    )
    schema_version = identity.schema_version
    artifact_role = identity.artifact_role
    trace_extraction_sha256 = identity.trace_extraction_sha256
    if manifest.get("trace_points_written") is not True:
        raise ValueError("trace manifest does not declare trace points written")

    assignment_by_candidate = dict(identity.candidate_assignments)
    for candidate_id, (
        assignment_sha256,
        sample_stem,
    ) in expected_bundle_identities.items():
        if assignment_by_candidate.get(candidate_id) != assignment_sha256:
            raise ValueError(
                "trace manifest candidate assignments do not cover trace bundle"
            )
        if sample_stem != identity.source_sample_stem:
            raise ValueError("trace manifest sample_stem does not match trace bundle")

    _validate_lineage_rows(
        trace_point_rows,
        schema_version=schema_version,
        artifact_role=artifact_role,
        trace_extraction_sha256=trace_extraction_sha256,
        expected_identities=expected_bundle_identities,
        label="trace points",
    )
    _validate_trace_point_values(trace_point_rows)
    if expected_metric_rows is not None:
        _validate_trace_point_metric_projection(
            trace_point_rows,
            metric_rows=expected_metric_rows,
            artifact_role=artifact_role,
            manifest=manifest,
        )
    _validate_trace_point_coverage(
        trace_point_rows,
        required_complete_candidate_ids=required_complete_candidate_ids,
    )
    output_sha256 = manifest.get("output_sha256")
    output_schemas = manifest.get("output_schema_versions")
    if not isinstance(output_sha256, Mapping) or not isinstance(
        output_schemas,
        Mapping,
    ):
        raise ValueError(
            "trace manifest output_sha256/output_schema_versions must be objects"
        )
    _validate_output(
        output_sha256,
        output_schemas,
        key="trace_points_csv",
        path=trace_points_csv,
        schema_version=schema_version,
    )
    return trace_extraction_sha256


def _validate_trace_point_metric_projection(
    point_rows: Sequence[Mapping[str, str]],
    *,
    metric_rows: Sequence[Mapping[str, str]],
    artifact_role: str,
    manifest: Mapping[str, object],
) -> None:

    metrics_by_pair = {
        (
            str(row.get("candidate_id", "")).strip(),
            str(row.get("position_label", "")).strip(),
        ): row
        for row in metric_rows
    }
    points_by_pair: dict[tuple[str, str], list[Mapping[str, str]]] = {}
    for row_index, row in enumerate(point_rows, start=2):
        if str(row.get("intensity_basis", "")).strip() != PT_PATTERN_RAW_TRACE_BASIS:
            raise ValueError(
                f"trace points row {row_index}: intensity_basis must be "
                f"{PT_PATTERN_RAW_TRACE_BASIS!r}"
            )
        pair = (
            str(row.get("candidate_id", "")).strip(),
            str(row.get("position_label", "")).strip(),
        )
        if pair not in metrics_by_pair:
            raise ValueError(
                "trace points contain a candidate/position outside metrics"
            )
        points_by_pair.setdefault(pair, []).append(row)

    for pair, metric_row in metrics_by_pair.items():
        rows = points_by_pair.get(pair, [])
        point_count = _required_non_negative_integer_text(
            metric_row.get("point_count"),
            label=f"trace metrics {pair[0]}:{pair[1]} point_count",
        )
        if len(rows) != point_count:
            raise ValueError(
                f"trace points {pair[0]}:{pair[1]} row count does not match "
                "trace metrics point_count"
            )
        metric_fields = (
            "rt_min",
            "rt_max",
            "raw_max_intensity",
            "raw_apex_rt_min",
        )
        if not rows:
            if any(str(metric_row.get(field, "")).strip() for field in metric_fields):
                raise ValueError(
                    f"trace metrics {pair[0]}:{pair[1]} must have blank raw "
                    "statistics when point_count is zero"
                )
            continue
        rt_values = [float(row["rt_min"]) for row in rows]
        if any(
            later <= earlier
            for earlier, later in zip(rt_values, rt_values[1:], strict=False)
        ):
            raise ValueError(
                f"trace points {pair[0]}:{pair[1]} rt_min must be strictly "
                "increasing and unique"
            )
        intensity_values = [float(row["intensity"]) for row in rows]
        max_index = max(
            range(len(intensity_values)),
            key=intensity_values.__getitem__,
        )
        expected_values = {
            "rt_min": rt_values[0],
            "rt_max": rt_values[-1],
            "raw_max_intensity": intensity_values[max_index],
            "raw_apex_rt_min": rt_values[max_index],
        }
        for field, expected in expected_values.items():
            observed = _optional_finite_row_number(
                metric_row,
                field,
                label=f"trace metrics {pair[0]}:{pair[1]}",
            )
            if observed is None or observed != expected:
                raise ValueError(
                    f"trace metrics {pair[0]}:{pair[1]} {field} does not "
                    "match raw trace points"
                )

    if artifact_role == PT_PATTERN_TRACE_ANCHOR_LOCAL_ROLE:
        metrics_by_candidate: dict[str, list[Mapping[str, str]]] = {}
        for metric_row in metric_rows:
            metrics_by_candidate.setdefault(
                str(metric_row.get("candidate_id", "")).strip(),
                [],
            ).append(metric_row)
        for candidate_id, candidate_metrics in metrics_by_candidate.items():
            boundary_values = [
                (
                    _optional_finite_row_number(
                        row,
                        "anchor_segment_rt_start_min",
                        label="trace metrics",
                    ),
                    _optional_finite_row_number(
                        row,
                        "anchor_segment_rt_end_min",
                        label="trace metrics",
                    ),
                )
                for row in candidate_metrics
            ]
            if all(start is None and end is None for start, end in boundary_values):
                continue
            if any(start is None or end is None for start, end in boundary_values):
                raise ValueError("anchor trace metrics contain partial boundaries")
            common_start = max(
                start for start, _end in boundary_values if start is not None
            )
            common_end = min(end for _start, end in boundary_values if end is not None)
            for (row_candidate, _position), rows in points_by_pair.items():
                if row_candidate != candidate_id:
                    continue
                if any(
                    not common_start <= float(row["rt_min"]) <= common_end
                    for row in rows
                ):
                    raise ValueError(
                        f"trace points {candidate_id} lie outside the anchor "
                        "common interval"
                    )

    from xic_extractor.discovery.pt_pattern_trace_evidence import (
        TRACE_POINT_PROJECTED_METRIC_COLUMNS,
        trace_metric_evidence_fields_from_points,
    )

    projected_rows = trace_metric_evidence_fields_from_points(
        metric_rows,
        point_rows,
        active_fraction=_required_number(manifest, "active_fraction"),
        min_pearson_r=_required_number(manifest, "min_pearson_r"),
        max_apex_delta_sec=_required_number(manifest, "max_apex_delta_sec"),
    )
    for observed_row, projected in zip(
        metric_rows,
        projected_rows,
        strict=True,
    ):
        mismatches = [
            field
            for field in TRACE_POINT_PROJECTED_METRIC_COLUMNS
            if str(observed_row.get(field, "")).strip() != projected[field]
        ]
        if mismatches:
            candidate_id = str(observed_row.get("candidate_id", "")).strip()
            position_label = str(observed_row.get("position_label", "")).strip()
            raise ValueError(
                f"trace metrics {candidate_id}:{position_label} are not the "
                "deterministic projection of trace points: " + ",".join(mismatches)
            )


def _validate_metric_call_projection(
    rows: Sequence[Mapping[str, str]],
    *,
    manifest: Mapping[str, object],
) -> None:
    from xic_extractor.discovery.pt_pattern_trace_evidence import (
        trace_metric_call_fields,
    )

    rows_by_candidate: dict[str, list[Mapping[str, str]]] = {}
    for row in rows:
        rows_by_candidate.setdefault(
            str(row.get("candidate_id", "")).strip(),
            [],
        ).append(row)
    min_pearson_r = _required_number(manifest, "min_pearson_r")
    max_apex_delta_sec = _required_number(manifest, "max_apex_delta_sec")
    for candidate_id, candidate_rows in rows_by_candidate.items():
        base_row = next(
            row
            for row in candidate_rows
            if str(row.get("position_label", "")).strip() == "base"
        )
        for row in candidate_rows:
            expected = trace_metric_call_fields(
                row,
                base_metric_row=base_row,
                min_pearson_r=min_pearson_r,
                max_apex_delta_sec=max_apex_delta_sec,
            )
            mismatches = [
                field
                for field, expected_value in expected.items()
                if str(row.get(field, "")).strip() != expected_value
            ]
            if mismatches:
                position_label = str(row.get("position_label", "")).strip()
                raise ValueError(
                    f"trace metrics {candidate_id}:{position_label}: call fields "
                    "are not the deterministic projection of scalar evidence: "
                    + ",".join(mismatches)
                )


def _validate_trace_summary_projection(
    *,
    summary_rows: Sequence[Mapping[str, str]],
    metric_rows: Sequence[Mapping[str, str]],
    artifact_role: str,
) -> None:
    from xic_extractor.discovery.pt_pattern_trace_evidence import (
        TRACE_SUMMARY_AGGREGATE_COLUMNS,
        trace_summary_aggregate_fields,
    )

    metrics_by_candidate: dict[str, list[Mapping[str, str]]] = {}
    for row in metric_rows:
        candidate_id = str(row.get("candidate_id", "")).strip()
        metrics_by_candidate.setdefault(candidate_id, []).append(row)
    for row_index, summary_row in enumerate(summary_rows, start=2):
        candidate_id = str(summary_row.get("candidate_id", "")).strip()
        expected = trace_summary_aggregate_fields(
            metrics_by_candidate.get(candidate_id, ())
        )
        fields_to_compare = TRACE_SUMMARY_AGGREGATE_COLUMNS
        if artifact_role == PT_PATTERN_TRACE_ANCHOR_LOCAL_ROLE:
            segmentation_status = str(
                summary_row.get("anchor_segmentation_status", "")
            ).strip()
            if segmentation_status not in {
                "clear",
                "missing_anchor_segment",
                "ambiguous_anchor_segment",
                "empty_common_interval",
            }:
                raise ValueError(
                    f"trace summary row {row_index}: unsupported "
                    "anchor_segmentation_status"
                )
            if segmentation_status != "clear":
                segmentation_reason = str(
                    summary_row.get("anchor_segmentation_reason", "")
                ).strip()
                if not segmentation_reason:
                    raise ValueError(
                        f"trace summary row {row_index}: "
                        "anchor_segmentation_reason must be non-empty"
                    )
                expected.update(
                    {
                        "trace_support_status": "review",
                        "combined_pt_pattern_status": ("pt_pattern_candidate_review"),
                        "reason": (
                            f"anchor_segmentation_{segmentation_status}:"
                            f"{segmentation_reason}"
                        ),
                    }
                )
        mismatches = [
            field
            for field in fields_to_compare
            if str(summary_row.get(field, ""))
            != format_diagnostic_value(expected.get(field))
        ]
        if mismatches:
            raise ValueError(
                f"trace summary row {row_index}: aggregate fields are not the "
                "deterministic projection of trace metrics: " + ",".join(mismatches)
            )
        if artifact_role == PT_PATTERN_TRACE_ANCHOR_LOCAL_ROLE:
            _validate_anchor_segmentation_projection(
                summary_row=summary_row,
                metric_rows=metrics_by_candidate.get(candidate_id, ()),
                row_index=row_index,
            )


def _validate_anchor_segmentation_projection(
    *,
    summary_row: Mapping[str, str],
    metric_rows: Sequence[Mapping[str, str]],
    row_index: int,
) -> None:
    from xic_extractor.discovery.pt_pattern_trace_batch import (
        project_anchor_local_metric_rows,
        project_anchor_segmentation_summary_fields,
    )

    status = str(summary_row.get("anchor_segmentation_status", "")).strip()
    reason = str(summary_row.get("anchor_segmentation_reason", "")).strip()
    if status != "clear":
        empty_trace_numeric_fields = (
            "rt_min",
            "rt_max",
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
        for metric_row in metric_rows:
            if any(
                str(metric_row.get(field, "")).strip()
                for field in PT_PATTERN_ANCHOR_SEGMENTATION_METRIC_COLUMNS
            ):
                raise ValueError(
                    f"trace summary row {row_index}: failed anchor segmentation "
                    "requires blank per-position boundary evidence"
                )
            if str(metric_row.get("point_count", "")).strip() != "0" or any(
                str(metric_row.get(field, "")).strip()
                for field in empty_trace_numeric_fields
            ):
                raise ValueError(
                    f"trace summary row {row_index}: failed anchor segmentation "
                    "requires the producer empty-trace metric envelope"
                )
            position_label = str(metric_row.get("position_label", "")).strip()
            expected_status = (
                "missing_signal" if position_label == "base" else "missing_points"
            )
            expected_reason = (
                "missing_signal" if position_label == "base" else "no_trace_points"
            )
            if (
                str(metric_row.get("trace_quality_flags", "")).strip() != "none"
                or str(metric_row.get("trace_support_status", "")).strip()
                != expected_status
                or str(metric_row.get("reason", "")).strip() != expected_reason
            ):
                raise ValueError(
                    f"trace summary row {row_index}: failed anchor segmentation "
                    "has non-canonical empty-trace call fields"
                )
        projected_summary = project_anchor_segmentation_summary_fields(
            segmentation_status=status,
            segmentation_reason=reason,
            metric_rows=metric_rows,
        )
        for field in PT_PATTERN_ANCHOR_SEGMENTATION_SUMMARY_COLUMNS:
            if str(summary_row.get(field, "")).strip() != format_diagnostic_value(
                projected_summary.get(field)
            ):
                raise ValueError(
                    f"trace summary row {row_index}: {field} is not the exact "
                    "failed-anchor projection"
                )
        if status == "missing_anchor_segment" and not reason.startswith(
            "missing_anchor_segment:"
        ):
            raise ValueError(
                f"trace summary row {row_index}: invalid missing-anchor reason"
            )
        if status == "ambiguous_anchor_segment" and not reason.startswith(
            "ambiguous_anchor_segment:"
        ):
            raise ValueError(
                f"trace summary row {row_index}: invalid ambiguous-anchor reason"
            )
        if status == "empty_common_interval" and reason not in {
            "selected_segments_have_no_common_rt_interval",
            "selected_common_rt_interval_has_fewer_than_three_points",
        }:
            raise ValueError(
                f"trace summary row {row_index}: invalid empty-interval reason"
            )
        return

    if reason != "one_anchor_segment_per_position":
        raise ValueError(
            f"trace summary row {row_index}: clear anchor segmentation reason mismatch"
        )
    projected_metric_rows = project_anchor_local_metric_rows(
        metric_rows,
        segmentation_status=status,
    )
    for observed_row, expected_row in zip(
        metric_rows,
        projected_metric_rows,
        strict=True,
    ):
        position_label = str(observed_row.get("position_label", "")).strip()
        for field in PT_PATTERN_ANCHOR_SEGMENTATION_METRIC_COLUMNS:
            if str(observed_row.get(field, "")).strip() != format_diagnostic_value(
                expected_row.get(field)
            ):
                raise ValueError(
                    f"trace metrics {position_label}: {field} is not the exact "
                    "CSV-precision anchor projection"
                )
        for trend_field in (
            "anchor_segment_common_left_trend",
            "anchor_segment_common_right_trend",
        ):
            if str(observed_row.get(trend_field, "")).strip() not in {
                "ascending",
                "descending",
                "flat",
                "unavailable",
            }:
                raise ValueError(f"trace metrics contains unsupported {trend_field}")
        for text_field in (
            "anchor_segment_class",
            "anchor_segment_boundary_stop_reason",
        ):
            if not str(observed_row.get(text_field, "")).strip():
                raise ValueError(f"trace metrics {text_field} must be non-empty")

    projected_summary = project_anchor_segmentation_summary_fields(
        segmentation_status=status,
        segmentation_reason=reason,
        metric_rows=projected_metric_rows,
    )
    for field in PT_PATTERN_ANCHOR_SEGMENTATION_SUMMARY_COLUMNS:
        if str(summary_row.get(field, "")).strip() != format_diagnostic_value(
            projected_summary.get(field)
        ):
            raise ValueError(
                f"trace summary row {row_index}: {field} is not the exact "
                "CSV-precision anchor projection"
            )

    anchor_rt_min = _required_finite_row_number(
        summary_row,
        "base_rt_apex_min",
        label="trace summary",
    )
    common_start = _required_projected_number(
        projected_summary,
        "anchor_segmentation_common_rt_start_min",
    )
    common_end = _required_projected_number(
        projected_summary,
        "anchor_segmentation_common_rt_end_min",
    )
    if (
        anchor_rt_min < common_start
        and not math.isclose(
            anchor_rt_min,
            common_start,
            rel_tol=1e-5,
            abs_tol=1e-5,
        )
    ) or (
        anchor_rt_min > common_end
        and not math.isclose(
            anchor_rt_min,
            common_end,
            rel_tol=1e-5,
            abs_tol=1e-5,
        )
    ):
        raise ValueError(
            f"trace summary row {row_index}: clear anchor interval must contain "
            "base_rt_apex_min"
        )


def _validate_output(
    output_sha256: Mapping[str, object],
    output_schemas: Mapping[str, object],
    *,
    key: str,
    path: Path,
    schema_version: str,
) -> None:
    expected_sha256 = _required_sha256_field(
        output_sha256,
        key,
        label="trace manifest output_sha256",
    )
    if file_sha256(path).lower() != expected_sha256:
        raise ValueError(f"trace manifest {key} sha256 mismatch")
    if str(output_schemas.get(key, "")).strip() != schema_version:
        raise ValueError(f"trace manifest {key} schema_version mismatch")
