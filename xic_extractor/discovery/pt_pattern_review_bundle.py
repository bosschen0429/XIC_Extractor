"""Canonical verified loaders for Pt review and blind-overlay bundles."""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

from xic_extractor.discovery.pt_pattern_artifact_contract import (
    PT_PATTERN_TRACE_FULL_CONTEXT_ROLE,
)
from xic_extractor.discovery.pt_pattern_blind_overlay_call import (
    MACHINE_SCHEMA_COLUMNS,
    PACKET_TO_MACHINE_SCHEMA,
    machine_accept_candidate_ids,
    machine_call_row,
    validate_machine_call_rows,
    validate_packet_rows,
)
from xic_extractor.discovery.pt_pattern_review_packet import (
    CANDIDATE_REQUIRED_COLUMNS,
    PACKET_SCHEMA_COLUMNS,
    build_review_packet_rows,
    default_review_packet_manifest_path,
    validate_review_packet_manifest,
)
from xic_extractor.discovery.pt_pattern_trace_evidence import TRACE_POINT_COLUMNS
from xic_extractor.discovery.pt_pattern_trace_manifest import (
    VerifiedTraceSummaryBundle,
    default_trace_summary_path,
    load_trace_bundle_manifest,
    load_verified_trace_summary_bundle,
    validate_cross_role_trace_request_identity,
    validate_trace_points_manifest,
)
from xic_extractor.tabular_io import (
    file_sha256,
    format_diagnostic_value,
    read_delimited_rows,
    validate_delimited_header,
)

_MACHINE_IDENTITY_FIELDS = (
    "packet_row_id",
    "candidate_id",
    "sample_stem",
    "packet_label",
    "source_label",
    "candidate_assignment_sha256",
    "trace_extraction_sha256",
    "trace_artifact_role",
)
BLIND_OVERLAY_SUMMARY_FILE_NAME = "pt_pattern_blind_overlay_summary.json"


@dataclass(frozen=True, slots=True)
class VerifiedReviewPacketBundle:
    review_packet_csv: Path
    review_packet_manifest_json: Path
    trace_manifest_json: Path
    schema_version: str
    trace_artifact_role: str
    trace_extraction_sha256: str
    review_packet_csv_sha256: str
    rows: tuple[dict[str, str], ...]
    trace_summary_bundle: VerifiedTraceSummaryBundle


@dataclass(frozen=True, slots=True)
class VerifiedReviewPacketCollection:
    rows: tuple[dict[str, str], ...]
    sha256_by_packet_row_id: Mapping[str, str]


@dataclass(frozen=True, slots=True)
class VerifiedBlindOverlayBundle:
    summary_json: Path
    summary_json_sha256: str
    review_packet: VerifiedReviewPacketBundle
    machine_calls_csv: Path
    machine_rows_by_packet_row_id: Mapping[str, Mapping[str, str]]


@dataclass(frozen=True, slots=True)
class VerifiedMachineCallCollection:
    rows: tuple[dict[str, str], ...]
    summary_jsons: tuple[Path, ...]
    review_packet_sha256_by_packet_row_id: Mapping[str, str]
    summary_json_sha256_by_packet_row_id: Mapping[str, str]


def load_verified_review_packet_bundle(
    review_packet_csv: Path,
    *,
    review_packet_manifest_json: Path | None = None,
    trace_manifest_json: Path | None = None,
) -> VerifiedReviewPacketBundle:
    rows = tuple(
        read_delimited_rows(
            review_packet_csv,
            required_columns=("schema_version",),
            delimiter=",",
            encoding="utf-8-sig",
        )
    )
    validate_packet_rows(rows)
    validate_delimited_header(
        tuple(rows[0]),
        path=review_packet_csv,
        exact_columns=PACKET_SCHEMA_COLUMNS[str(rows[0]["schema_version"])],
    )
    resolved_packet_manifest = (
        review_packet_manifest_json
        or default_review_packet_manifest_path(review_packet_csv)
    )
    manifest = json.loads(resolved_packet_manifest.read_text(encoding="utf-8"))
    if not isinstance(manifest, dict):
        raise ValueError("review packet manifest must be a JSON object")
    resolved_trace_manifest = trace_manifest_json
    if resolved_trace_manifest is None:
        recorded_trace_manifest = str(manifest.get("trace_manifest_json", "")).strip()
        if recorded_trace_manifest:
            recorded_path = Path(recorded_trace_manifest)
            if recorded_path.is_absolute():
                resolved_trace_manifest = recorded_path
            else:
                sibling_path = (
                    resolved_packet_manifest.parent / recorded_path
                ).resolve()
                cwd_path = (Path.cwd() / recorded_path).resolve()
                resolved_trace_manifest = (
                    sibling_path if sibling_path.is_file() else cwd_path
                )
    extraction_sha256, artifact_role, resolved_trace_manifest = (
        validate_review_packet_manifest(
            manifest=manifest,
            review_packet_csv=review_packet_csv,
            packet_rows=rows,
            trace_manifest_json=resolved_trace_manifest,
        )
    )
    trace_summary_bundle = _validate_review_packet_source_projection(
        manifest=manifest,
        manifest_path=resolved_packet_manifest,
        packet_rows=rows,
        artifact_role=artifact_role,
        trace_manifest_json=resolved_trace_manifest,
    )
    return VerifiedReviewPacketBundle(
        review_packet_csv=review_packet_csv,
        review_packet_manifest_json=resolved_packet_manifest,
        trace_manifest_json=resolved_trace_manifest,
        schema_version=str(rows[0]["schema_version"]),
        trace_artifact_role=artifact_role,
        trace_extraction_sha256=extraction_sha256,
        review_packet_csv_sha256=file_sha256(review_packet_csv),
        rows=rows,
        trace_summary_bundle=trace_summary_bundle,
    )


def _validate_review_packet_source_projection(
    *,
    manifest: Mapping[str, object],
    manifest_path: Path,
    packet_rows: Sequence[Mapping[str, str]],
    artifact_role: str,
    trace_manifest_json: Path,
) -> VerifiedTraceSummaryBundle:
    input_sha256 = manifest.get("input_sha256")
    if not isinstance(input_sha256, Mapping):
        raise ValueError("review packet manifest input_sha256 must be an object")
    candidates_csv = _verified_artifact(
        manifest_path,
        path_value=manifest.get("candidates_csv"),
        sha256_value=input_sha256.get("candidates_csv"),
        label="packet source candidates",
    )
    trace_summary_csv = _verified_artifact(
        manifest_path,
        path_value=manifest.get("trace_summary_csv"),
        sha256_value=input_sha256.get("trace_summary_csv"),
        label="packet source trace summary",
    )
    trace_metrics_csv = _verified_artifact(
        manifest_path,
        path_value=manifest.get("trace_metrics_csv"),
        sha256_value=input_sha256.get("trace_metrics_csv"),
        label="packet source trace metrics",
    )
    candidate_rows = tuple(
        read_delimited_rows(
            candidates_csv,
            required_columns=CANDIDATE_REQUIRED_COLUMNS,
            delimiter=",",
            encoding="utf-8-sig",
        )
    )
    trace_bundle = load_verified_trace_summary_bundle(
        trace_summary_csv,
        expected_artifact_role=artifact_role,
        metrics_csv=trace_metrics_csv,
        manifest_json=trace_manifest_json,
        candidates_csv=candidates_csv,
    )
    max_per_status = manifest.get("max_per_status")
    if (
        isinstance(max_per_status, bool)
        or not isinstance(max_per_status, int)
        or max_per_status <= 0
    ):
        raise ValueError(
            "review packet manifest max_per_status must be a positive integer"
        )
    expected_rows = build_review_packet_rows(
        candidate_rows=candidate_rows,
        summary_rows=trace_bundle.summary_rows,
        metric_rows=trace_bundle.metric_rows,
        packet_label=str(manifest.get("packet_label", "")),
        source_label=str(manifest.get("source_label", "")),
        max_per_status=max_per_status,
    )
    expected_columns = PACKET_SCHEMA_COLUMNS[
        str(packet_rows[0]["schema_version"])
    ]
    serialized_expected = tuple(
        {
            column: format_diagnostic_value(row.get(column, ""))
            for column in expected_columns
        }
        for row in expected_rows
    )
    observed = tuple(
        {column: str(row.get(column, "")) for column in expected_columns}
        for row in packet_rows
    )
    if observed != serialized_expected:
        raise ValueError(
            "review packet rows are not the deterministic projection of their "
            "recorded candidates/trace summary/metrics inputs"
        )
    return trace_bundle


def load_verified_review_packet_collection(
    paths: Sequence[Path],
    *,
    require_nonempty: bool,
) -> VerifiedReviewPacketCollection:
    """Load one schema-shaped collection of manifest-verified packets."""

    if require_nonempty and not paths:
        raise ValueError("at least one review packet CSV is required")
    all_rows: list[dict[str, str]] = []
    sha256_by_packet_row_id: dict[str, str] = {}
    for path in paths:
        bundle = load_verified_review_packet_bundle(path)
        for row in bundle.rows:
            packet_row_id = str(row.get("packet_row_id", "")).strip()
            if not packet_row_id:
                raise ValueError("review packet packet_row_id must be non-empty")
            if packet_row_id in sha256_by_packet_row_id:
                raise ValueError(f"duplicate review packet_row_id: {packet_row_id}")
            sha256_by_packet_row_id[packet_row_id] = bundle.review_packet_csv_sha256
        all_rows.extend(bundle.rows)
    if all_rows:
        # Two individually valid packet files still cannot mix full and
        # anchor-local roles in one replay evidence surface.
        validate_packet_rows(all_rows)
    return VerifiedReviewPacketCollection(
        rows=tuple(all_rows),
        sha256_by_packet_row_id=sha256_by_packet_row_id,
    )


def validate_machine_rows_against_packet(
    *,
    packet_rows: Sequence[Mapping[str, str]],
    machine_rows: Sequence[Mapping[str, str]],
    require_exact_set: bool,
    allow_identity_mismatch: bool = False,
    require_exact_projection: bool = True,
) -> dict[str, Mapping[str, str]]:
    validate_packet_rows(packet_rows)
    validate_machine_call_rows(machine_rows)
    packet_by_id = _unique_by_packet_row_id(packet_rows, label="review packet")
    machine_by_id = _unique_by_packet_row_id(machine_rows, label="machine-call")
    if require_exact_set:
        missing = sorted(packet_by_id.keys() - machine_by_id.keys())
        extra = sorted(machine_by_id.keys() - packet_by_id.keys())
        if missing or extra:
            raise ValueError(
                "machine-call rows must exactly match review packet; "
                f"missing packet_row_id={missing!r}; extra packet_row_id={extra!r}"
            )
    elif machine_by_id.keys() - packet_by_id.keys():
        extra = sorted(machine_by_id.keys() - packet_by_id.keys())
        raise ValueError(
            f"machine-call rows contain packet_row_id outside review packet: {extra!r}"
        )

    for packet_row_id, machine_row in machine_by_id.items():
        review_row = packet_by_id[packet_row_id]
        mismatches = machine_row_identity_mismatches(
            machine_row=machine_row,
            review_row=review_row,
        )
        if mismatches and not allow_identity_mismatch:
            raise ValueError(
                f"{packet_row_id}: machine {mismatches[0]} does not match exact "
                "review packet"
            )
        projection_mismatches = machine_row_projection_mismatches(
            machine_row=machine_row,
            review_row=review_row,
        )
        if projection_mismatches and require_exact_projection:
            raise ValueError(
                f"{packet_row_id}: sealed machine {projection_mismatches[0]} "
                "is not the deterministic projection of the review packet"
            )
    return machine_by_id


def machine_row_identity_mismatches(
    *,
    machine_row: Mapping[str, str] | None,
    review_row: Mapping[str, str],
) -> tuple[str, ...]:
    """Return canonical schema and identity mismatches for a machine row."""

    if machine_row is None:
        return ()
    mismatches: list[str] = []
    packet_schema = str(review_row.get("schema_version", "")).strip()
    expected_machine_schema = PACKET_TO_MACHINE_SCHEMA.get(packet_schema)
    if str(machine_row.get("schema_version", "")).strip() != expected_machine_schema:
        mismatches.append("schema_version")
    for field in _MACHINE_IDENTITY_FIELDS:
        if (
            str(machine_row.get(field, "")).strip()
            != str(review_row.get(field, "")).strip()
        ):
            mismatches.append(field)
    return tuple(mismatches)


def machine_row_projection_mismatches(
    *,
    machine_row: Mapping[str, str] | None,
    review_row: Mapping[str, str],
) -> tuple[str, ...]:
    """Return fields that differ from the deterministic packet projection."""

    if machine_row is None:
        return ()
    expected = {
        key: str(value)
        for key, value in machine_call_row(review_row).items()
    }
    expected_schema = expected["schema_version"]
    return tuple(
        field
        for field in MACHINE_SCHEMA_COLUMNS[expected_schema]
        if str(machine_row.get(field, "")) != expected.get(field, "")
    )


def load_verified_blind_overlay_bundle(
    summary_json: Path,
) -> VerifiedBlindOverlayBundle:
    summary = json.loads(summary_json.read_text(encoding="utf-8-sig"))
    if not isinstance(summary, dict):
        raise ValueError(f"{summary_json}: blind-overlay summary must be a JSON object")
    for key in (
        "diagnostic_only",
        "sealed_machine_calls_written",
        "blind_to_machine_calls_in_html",
    ):
        if summary.get(key) is not True:
            raise ValueError(f"{summary_json}: {key} must be true")

    review_packet_csv = _verified_artifact(
        summary_json,
        path_value=summary.get("review_packet_csv"),
        sha256_value=summary.get("review_packet_csv_sha256"),
        label="review packet",
    )
    packet_manifest_json = _verified_artifact(
        summary_json,
        path_value=summary.get("review_packet_manifest_json"),
        sha256_value=summary.get("review_packet_manifest_json_sha256"),
        label="review packet manifest",
    )
    trace_manifest_json = _verified_artifact(
        summary_json,
        path_value=summary.get("trace_manifest_json"),
        sha256_value=summary.get("trace_manifest_json_sha256"),
        label="primary trace manifest",
    )
    review_bundle = load_verified_review_packet_bundle(
        review_packet_csv,
        review_packet_manifest_json=packet_manifest_json,
        trace_manifest_json=trace_manifest_json,
    )
    expected_summary_schema = PACKET_TO_MACHINE_SCHEMA[review_bundle.schema_version]
    if str(summary.get("schema_version", "")).strip() != expected_summary_schema:
        raise ValueError(f"{summary_json}: summary schema does not match packet role")
    if str(summary.get("trace_artifact_role", "")).strip() != (
        review_bundle.trace_artifact_role
    ):
        raise ValueError(f"{summary_json}: summary trace_artifact_role mismatch")
    if str(summary.get("trace_extraction_sha256", "")).strip() != (
        review_bundle.trace_extraction_sha256
    ):
        raise ValueError(f"{summary_json}: summary trace_extraction_sha256 mismatch")

    packet_identities = {
        str(row["candidate_id"]): (
            str(row["candidate_assignment_sha256"]),
            str(row["sample_stem"]),
        )
        for row in review_bundle.rows
    }
    trace_points_csv = _verified_artifact(
        summary_json,
        path_value=summary.get("trace_points_csv"),
        sha256_value=summary.get("trace_points_csv_sha256"),
        label="primary trace points",
    )
    trace_point_rows = tuple(
        read_delimited_rows(
            trace_points_csv,
            required_columns=TRACE_POINT_COLUMNS,
            exact_columns=TRACE_POINT_COLUMNS,
            delimiter=",",
            encoding="utf-8-sig",
        )
    )
    primary_manifest = load_trace_bundle_manifest(trace_manifest_json)
    primary_extraction = validate_trace_points_manifest(
        manifest=primary_manifest,
        trace_points_csv=trace_points_csv,
        trace_point_rows=trace_point_rows,
        expected_artifact_role=review_bundle.trace_artifact_role,
        expected_packet_identities=packet_identities,
        required_complete_candidate_ids=machine_accept_candidate_ids(
            review_bundle.rows
        ),
        expected_metric_rows=review_bundle.trace_summary_bundle.metric_rows,
    )
    if primary_extraction != review_bundle.trace_extraction_sha256:
        raise ValueError(f"{summary_json}: primary trace extraction mismatch")

    context_path_value = summary.get("context_trace_points_csv")
    context_manifest_value = summary.get("context_trace_manifest_json")
    has_context = bool(str(context_path_value or "").strip())
    if has_context != bool(str(context_manifest_value or "").strip()):
        raise ValueError(
            f"{summary_json}: context points/manifest must appear together"
        )
    if review_bundle.trace_artifact_role == PT_PATTERN_TRACE_FULL_CONTEXT_ROLE:
        if has_context:
            raise ValueError(f"{summary_json}: full packet must not have context")
        expected_gallery_mode = "full_context_only"
    else:
        expected_gallery_mode = (
            "anchor_local_with_full_context" if has_context else "anchor_local_only"
        )
    if str(summary.get("gallery_mode", "")).strip() != expected_gallery_mode:
        raise ValueError(f"{summary_json}: gallery_mode does not match packet/context")

    if has_context:
        context_points_csv = _verified_artifact(
            summary_json,
            path_value=context_path_value,
            sha256_value=summary.get("context_trace_points_csv_sha256"),
            label="context trace points",
        )
        context_manifest_json = _verified_artifact(
            summary_json,
            path_value=context_manifest_value,
            sha256_value=summary.get("context_trace_manifest_json_sha256"),
            label="context trace manifest",
        )
        context_rows = tuple(
            read_delimited_rows(
                context_points_csv,
                required_columns=TRACE_POINT_COLUMNS,
                exact_columns=TRACE_POINT_COLUMNS,
                delimiter=",",
                encoding="utf-8-sig",
            )
        )
        context_manifest = load_trace_bundle_manifest(context_manifest_json)
        context_trace_bundle = load_verified_trace_summary_bundle(
            default_trace_summary_path(
                context_points_csv,
                artifact_role=PT_PATTERN_TRACE_FULL_CONTEXT_ROLE,
            ),
            expected_artifact_role=PT_PATTERN_TRACE_FULL_CONTEXT_ROLE,
            manifest_json=context_manifest_json,
        )
        validate_cross_role_trace_request_identity(
            review_bundle.trace_summary_bundle.metric_rows,
            context_trace_bundle.metric_rows,
        )
        context_extraction = validate_trace_points_manifest(
            manifest=context_manifest,
            trace_points_csv=context_points_csv,
            trace_point_rows=context_rows,
            expected_artifact_role=PT_PATTERN_TRACE_FULL_CONTEXT_ROLE,
            expected_packet_identities=packet_identities,
            expected_metric_rows=context_trace_bundle.metric_rows,
        )
        if context_extraction != review_bundle.trace_extraction_sha256:
            raise ValueError(f"{summary_json}: context trace extraction mismatch")

    outputs = summary.get("outputs")
    if not isinstance(outputs, Mapping):
        raise ValueError(f"{summary_json}: outputs must be a JSON object")
    _verified_artifact(
        summary_json,
        path_value=outputs.get("gallery_html"),
        sha256_value=outputs.get("gallery_html_sha256"),
        label="gallery HTML",
    )
    machine_calls_csv = _verified_artifact(
        summary_json,
        path_value=outputs.get("machine_calls_csv"),
        sha256_value=outputs.get("machine_calls_csv_sha256"),
        label="machine calls",
    )
    machine_rows = tuple(
        read_delimited_rows(
            machine_calls_csv,
            required_columns=MACHINE_SCHEMA_COLUMNS[expected_summary_schema],
            exact_columns=MACHINE_SCHEMA_COLUMNS[expected_summary_schema],
            delimiter=",",
            encoding="utf-8-sig",
        )
    )
    validate_machine_call_rows(machine_rows)
    machine_by_id = validate_machine_rows_against_packet(
        packet_rows=review_bundle.rows,
        machine_rows=machine_rows,
        require_exact_set=True,
    )
    return VerifiedBlindOverlayBundle(
        summary_json=summary_json,
        summary_json_sha256=file_sha256(summary_json),
        review_packet=review_bundle,
        machine_calls_csv=machine_calls_csv,
        machine_rows_by_packet_row_id=machine_by_id,
    )


def load_verified_machine_call_collection(
    machine_call_csvs: Sequence[Path],
) -> VerifiedMachineCallCollection:
    """Load machine rows only through their sibling sealed overlay summaries."""

    rows: list[dict[str, str]] = []
    summaries: list[Path] = []
    seen_summaries: set[Path] = set()
    review_packet_sha256_by_packet_row_id: dict[str, str] = {}
    summary_json_sha256_by_packet_row_id: dict[str, str] = {}
    for machine_calls_csv in machine_call_csvs:
        summary_json = (
            machine_calls_csv.parent / BLIND_OVERLAY_SUMMARY_FILE_NAME
        ).resolve()
        if summary_json in seen_summaries:
            raise ValueError(f"duplicate blind-overlay summary: {summary_json}")
        verified = load_verified_blind_overlay_bundle(summary_json)
        if verified.machine_calls_csv.resolve() != machine_calls_csv.resolve():
            raise ValueError(
                f"{machine_calls_csv}: sibling blind-overlay summary binds a "
                "different machine-call artifact"
            )
        seen_summaries.add(summary_json)
        summaries.append(summary_json)
        for packet_row_id, row in verified.machine_rows_by_packet_row_id.items():
            if packet_row_id in review_packet_sha256_by_packet_row_id:
                raise ValueError(
                    f"duplicate machine-call packet_row_id: {packet_row_id}"
                )
            review_packet_sha256_by_packet_row_id[packet_row_id] = (
                verified.review_packet.review_packet_csv_sha256
            )
            summary_json_sha256_by_packet_row_id[packet_row_id] = (
                verified.summary_json_sha256
            )
            rows.append(dict(row))
    if rows:
        validate_machine_call_rows(rows)
    return VerifiedMachineCallCollection(
        rows=tuple(rows),
        summary_jsons=tuple(summaries),
        review_packet_sha256_by_packet_row_id=(
            review_packet_sha256_by_packet_row_id
        ),
        summary_json_sha256_by_packet_row_id=(
            summary_json_sha256_by_packet_row_id
        ),
    )


def _verified_artifact(
    summary_path: Path,
    *,
    path_value: object,
    sha256_value: object,
    label: str,
) -> Path:
    recorded = str(path_value or "").strip()
    if not recorded:
        raise ValueError(f"{summary_path}: missing {label} path")
    expected_sha256 = _normalized_sha256(
        sha256_value,
        label=f"{summary_path}: {label} sha256",
    )
    return _resolve_artifact_path(
        summary_path,
        recorded,
        expected_sha256=expected_sha256,
        label=label,
    )


def _resolve_artifact_path(
    summary_path: Path,
    value: str,
    *,
    expected_sha256: str,
    label: str,
) -> Path:
    recorded = Path(value)
    candidates = (
        (recorded,)
        if recorded.is_absolute()
        else (summary_path.parent / recorded, Path.cwd() / recorded)
    )
    existing: list[Path] = []
    seen: set[Path] = set()
    for candidate in candidates:
        resolved = candidate.resolve()
        if resolved in seen:
            continue
        seen.add(resolved)
        if resolved.is_file() and file_sha256(resolved).upper() == expected_sha256:
            return resolved
        if resolved.is_file():
            existing.append(resolved)
    if existing:
        raise ValueError(f"{summary_path}: {label} sha256 does not match")
    raise ValueError(f"{summary_path}: recorded artifact does not exist: {value}")


def _normalized_sha256(value: object, *, label: str) -> str:
    normalized = str(value or "").strip().upper()
    if len(normalized) != 64 or any(
        character not in "0123456789ABCDEF" for character in normalized
    ):
        raise ValueError(f"{label} must be a 64-character SHA-256 digest")
    return normalized


def _unique_by_packet_row_id(
    rows: Sequence[Mapping[str, str]],
    *,
    label: str,
) -> dict[str, Mapping[str, str]]:
    output: dict[str, Mapping[str, str]] = {}
    for row in rows:
        packet_row_id = str(row.get("packet_row_id", "")).strip()
        if not packet_row_id:
            raise ValueError(f"{label} packet_row_id must be non-empty")
        if packet_row_id in output:
            raise ValueError(f"duplicate {label} packet_row_id: {packet_row_id}")
        output[packet_row_id] = row
    return output
