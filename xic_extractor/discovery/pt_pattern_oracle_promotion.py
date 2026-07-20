"""Promotion packet builder for bounded Pt pattern human-review labels."""

from __future__ import annotations

import json
import os
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from uuid import uuid4

from xic_extractor.artifact_paths import require_disjoint_artifact_paths
from xic_extractor.discovery.pt_pattern_review_bundle import (
    VerifiedReviewPacketCollection,
    load_verified_blind_overlay_bundle,
    load_verified_review_packet_collection,
    machine_row_identity_mismatches,
)
from xic_extractor.tabular_io import (
    file_sha256,
    format_diagnostic_value,
    optional_float,
    read_delimited_rows,
    write_delimited_rows,
)

SCHEMA_VERSION = "pt_pattern_oracle_promotion_packet.v2"
READINESS_TIER = "diagnostic_only"
EVIDENCE_OWNER = "xic_extractor.discovery.pt_pattern_oracle_promotion"
AUTHORITY_SCOPE = "bounded_manual_oracle_only"
BUNDLE_MANIFEST_VERSION = "pt_pattern_oracle_promotion_bundle.v1"
PROMOTION_PACKET_FILE_NAME = "pt_pattern_oracle_promotion_packet.csv"
PROMOTION_SUMMARY_FILE_NAME = "pt_pattern_oracle_promotion_summary.json"

LEGACY_ADJUDICATED_REQUIRED_COLUMNS = (
    "dataset",
    "case_index",
    "adjudicated_call",
    "adjudication_basis",
    "user_call",
    "machine_call",
    "note_optional",
    "machine_call_reason",
    "base_mz",
    "base_rt_apex_min",
    "candidate_id",
    "packet_row_id",
)
ADJUDICATED_REQUIRED_COLUMNS = (
    *LEGACY_ADJUDICATED_REQUIRED_COLUMNS,
    "review_packet_csv_sha256",
    "blind_overlay_summary_json_sha256",
)

PROMOTION_COLUMNS = (
    "schema_version",
    "readiness_tier",
    "evidence_owner",
    "authority_scope",
    "oracle_packet_id",
    "oracle_status",
    "oracle_call",
    "oracle_detection_status",
    "packet_row_id",
    "review_packet_csv_sha256",
    "blind_overlay_summary_json_sha256",
    "dataset",
    "case_index",
    "sample_stem",
    "candidate_id",
    "base_mz",
    "base_rt_apex_min",
    "adjudication_basis",
    "human_review_call",
    "machine_call",
    "human_review_note",
    "machine_call_reason",
    "review_packet_schema_version",
    "review_packet_readiness_tier",
    "review_packet_oracle_status",
    "review_packet_combined_pt_pattern_status",
    "provenance_status",
)

ALLOWED_ORACLE_CALLS = ("accept", "reject")
ORACLE_DETECTION_STATUS = "not_asserted"


@dataclass(frozen=True, slots=True)
class BlindOverlayEvidenceBundle:
    summary_json_sha256: str
    review_packet_csv_sha256: str
    sealed_machine_rows_by_packet_row_id: Mapping[str, Mapping[str, str]]


@dataclass(frozen=True, slots=True)
class VerifiedOraclePromotionBundle:
    packet_csv: Path
    summary_json: Path
    packet_csv_sha256: str
    summary_json_sha256: str
    oracle_packet_id: str
    rows: tuple[dict[str, str], ...]
    review_packets: VerifiedReviewPacketCollection
    review_packet_csvs: tuple[Path, ...]
    blind_overlay_summary_jsons: tuple[Path, ...]


def run_oracle_promotion(
    *,
    adjudicated_oracle_csv: Path,
    review_packet_csvs: Sequence[Path],
    blind_overlay_summary_jsons: Sequence[Path],
    output_dir: Path,
    oracle_packet_id: str,
    adjudication_summary_json: Path | None = None,
) -> dict[str, Path]:
    resolved_adjudicated = adjudicated_oracle_csv.resolve()
    resolved_adjudication_summary = (
        adjudication_summary_json.resolve()
        if adjudication_summary_json is not None
        else None
    )
    resolved_review_packets = _canonical_source_paths(
        review_packet_csvs,
        label="review packet CSV",
    )
    resolved_overlay_summaries = _canonical_source_paths(
        blind_overlay_summary_jsons,
        label="blind-overlay summary JSON",
    )
    packet_csv = output_dir / PROMOTION_PACKET_FILE_NAME
    summary_json = output_dir / PROMOTION_SUMMARY_FILE_NAME
    temp_token = uuid4().hex
    packet_tmp = packet_csv.with_name(f".{packet_csv.name}.{temp_token}.tmp")
    summary_tmp = summary_json.with_name(f".{summary_json.name}.{temp_token}.tmp")
    require_disjoint_artifact_paths(
        input_paths=(
            resolved_adjudicated,
            *(
                (resolved_adjudication_summary,)
                if resolved_adjudication_summary
                else ()
            ),
            *resolved_review_packets,
            *resolved_overlay_summaries,
        ),
        output_paths=(packet_csv, summary_json, packet_tmp, summary_tmp),
        label="oracle promotion",
    )
    adjudicated_rows = read_delimited_rows(
        resolved_adjudicated,
        required_columns=ADJUDICATED_REQUIRED_COLUMNS,
        delimiter=",",
        encoding="utf-8-sig",
    )
    review_packets = _read_review_packets(resolved_review_packets)
    overlay_bundles = _read_blind_overlay_summaries(resolved_overlay_summaries)
    _validate_adjudication_summary(
        resolved_adjudication_summary,
        adjudicated_rows,
    )
    promotion_rows = build_oracle_promotion_rows(
        adjudicated_rows=adjudicated_rows,
        review_packet_rows=review_packets.rows,
        review_hash_by_packet_row_id=review_packets.sha256_by_packet_row_id,
        overlay_bundles=overlay_bundles,
        oracle_packet_id=oracle_packet_id,
    )

    output_dir.mkdir(parents=True, exist_ok=True)
    packet_tmp.unlink(missing_ok=True)
    summary_tmp.unlink(missing_ok=True)
    try:
        write_delimited_rows(
            packet_tmp,
            promotion_rows,
            PROMOTION_COLUMNS,
            extrasaction="raise",
        )
        packet_sha256 = file_sha256(packet_tmp)

        call_counts = Counter(str(row["oracle_call"]) for row in promotion_rows)
        status_counts = Counter(
            str(row["oracle_detection_status"]) for row in promotion_rows
        )
        summary_parent = summary_json.resolve().parent
        summary = {
            "schema_version": SCHEMA_VERSION,
            "bundle_manifest_version": BUNDLE_MANIFEST_VERSION,
            "readiness_tier": READINESS_TIER,
            "evidence_owner": EVIDENCE_OWNER,
            "authority_scope": AUTHORITY_SCOPE,
            "oracle_packet_id": oracle_packet_id,
            "inputs": {
                "adjudicated_oracle_csv": _artifact_descriptor(
                    resolved_adjudicated,
                    relative_to=summary_parent,
                ),
                "adjudication_summary_json": (
                    _artifact_descriptor(
                        resolved_adjudication_summary,
                        relative_to=summary_parent,
                    )
                    if resolved_adjudication_summary is not None
                    else None
                ),
                "review_packet_csvs": [
                    _artifact_descriptor(path, relative_to=summary_parent)
                    for path in resolved_review_packets
                ],
                "blind_overlay_summary_jsons": [
                    _artifact_descriptor(path, relative_to=summary_parent)
                    for path in resolved_overlay_summaries
                ],
            },
            "packet_row_count": len(promotion_rows),
            "oracle_call_counts": dict(sorted(call_counts.items())),
            "oracle_detection_status_counts": dict(sorted(status_counts.items())),
            "manual_oracle_only": True,
            "changes_matrix": False,
            "changes_counted_detection": False,
            "changes_export": False,
            "promotion_status": "promotion_packet_ready",
            "outputs": {
                "promotion_packet_csv": PROMOTION_PACKET_FILE_NAME,
                "promotion_packet_csv_sha256": packet_sha256,
            },
        }
        summary_tmp.write_text(
            json.dumps(summary, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        # The summary is the bundle commit marker. Invalidate any old marker
        # before publishing a new packet so an interrupted run fails closed.
        summary_json.unlink(missing_ok=True)
        packet_tmp.replace(packet_csv)
        summary_tmp.replace(summary_json)
        try:
            load_verified_oracle_promotion_bundle(packet_csv)
        except BaseException:
            summary_json.unlink(missing_ok=True)
            raise
    finally:
        packet_tmp.unlink(missing_ok=True)
        summary_tmp.unlink(missing_ok=True)
    return {"promotion_packet_csv": packet_csv, "summary_json": summary_json}


def default_oracle_promotion_summary_path(packet_csv: Path) -> Path:
    return packet_csv.parent / PROMOTION_SUMMARY_FILE_NAME


def load_verified_oracle_promotion_bundle(
    packet_csv: Path,
) -> VerifiedOraclePromotionBundle:
    """Load a promotion packet only after rebuilding it from sealed sources."""

    summary_json = default_oracle_promotion_summary_path(packet_csv)
    packet_sha256 = file_sha256(packet_csv)
    summary_sha256 = file_sha256(summary_json)
    rows = tuple(
        read_delimited_rows(
            packet_csv,
            required_columns=PROMOTION_COLUMNS,
            exact_columns=PROMOTION_COLUMNS,
            delimiter=",",
            encoding="utf-8-sig",
        )
    )
    if not rows:
        raise ValueError("oracle promotion packet must contain at least one row")
    payload = json.loads(summary_json.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("oracle promotion summary must be a JSON object")
    _require_exact_keys(
        payload,
        expected={
            "schema_version",
            "bundle_manifest_version",
            "readiness_tier",
            "evidence_owner",
            "authority_scope",
            "oracle_packet_id",
            "inputs",
            "packet_row_count",
            "oracle_call_counts",
            "oracle_detection_status_counts",
            "manual_oracle_only",
            "changes_matrix",
            "changes_counted_detection",
            "changes_export",
            "promotion_status",
            "outputs",
        },
        label="oracle promotion summary",
    )
    expected_constants = {
        "schema_version": SCHEMA_VERSION,
        "bundle_manifest_version": BUNDLE_MANIFEST_VERSION,
        "readiness_tier": READINESS_TIER,
        "evidence_owner": EVIDENCE_OWNER,
        "authority_scope": AUTHORITY_SCOPE,
        "promotion_status": "promotion_packet_ready",
    }
    for field, expected in expected_constants.items():
        if payload.get(field) != expected:
            raise ValueError(
                f"oracle promotion summary {field} must be {expected!r}"
            )
    for field, expected in {
        "manual_oracle_only": True,
        "changes_matrix": False,
        "changes_counted_detection": False,
        "changes_export": False,
    }.items():
        if payload.get(field) is not expected:
            raise ValueError(
                f"oracle promotion summary {field} must be "
                f"{str(expected).lower()}"
            )

    oracle_packet_id = _required_text(payload, "oracle_packet_id")
    observed_packet_ids = {
        _required_text(row, "oracle_packet_id") for row in rows
    }
    if observed_packet_ids != {oracle_packet_id}:
        raise ValueError(
            "oracle promotion rows must use the summary oracle_packet_id"
        )
    outputs = _required_mapping(payload, "outputs", label="oracle promotion summary")
    _require_exact_keys(
        outputs,
        expected={"promotion_packet_csv", "promotion_packet_csv_sha256"},
        label="oracle promotion summary outputs",
    )
    if outputs.get("promotion_packet_csv") != PROMOTION_PACKET_FILE_NAME:
        raise ValueError(
            "oracle promotion summary must name the fixed sibling packet CSV"
        )
    expected_packet_path = summary_json.parent / PROMOTION_PACKET_FILE_NAME
    if expected_packet_path.resolve() != packet_csv.resolve():
        raise ValueError("oracle promotion packet is not the fixed summary sibling")
    if _normalized_sha256(
        outputs.get("promotion_packet_csv_sha256"),
        label="oracle promotion summary packet SHA-256",
    ) != packet_sha256:
        raise ValueError("oracle promotion summary packet SHA-256 mismatch")
    if _required_manifest_int(payload, "packet_row_count") != len(rows):
        raise ValueError("oracle promotion summary packet_row_count mismatch")
    observed_call_counts = dict(
        sorted(Counter(row["oracle_call"] for row in rows).items())
    )
    observed_detection_counts = dict(
        sorted(Counter(row["oracle_detection_status"] for row in rows).items())
    )
    if (
        _required_count_mapping(payload, "oracle_call_counts")
        != observed_call_counts
    ):
        raise ValueError("oracle promotion summary oracle_call_counts mismatch")
    if (
        _required_count_mapping(payload, "oracle_detection_status_counts")
        != observed_detection_counts
    ):
        raise ValueError(
            "oracle promotion summary oracle_detection_status_counts mismatch"
        )

    inputs = _required_mapping(payload, "inputs", label="oracle promotion summary")
    _require_exact_keys(
        inputs,
        expected={
            "adjudicated_oracle_csv",
            "adjudication_summary_json",
            "review_packet_csvs",
            "blind_overlay_summary_jsons",
        },
        label="oracle promotion summary inputs",
    )
    adjudicated_oracle_csv = _load_artifact_descriptor(
        inputs.get("adjudicated_oracle_csv"),
        manifest_path=summary_json,
        label="adjudicated oracle CSV",
    )
    adjudication_summary_raw = inputs.get("adjudication_summary_json")
    adjudication_summary_json = (
        None
        if adjudication_summary_raw is None
        else _load_artifact_descriptor(
            adjudication_summary_raw,
            manifest_path=summary_json,
            label="adjudication summary JSON",
        )
    )
    review_packet_csvs = _load_artifact_descriptor_list(
        inputs.get("review_packet_csvs"),
        manifest_path=summary_json,
        label="review packet CSV",
    )
    blind_overlay_summary_jsons = _load_artifact_descriptor_list(
        inputs.get("blind_overlay_summary_jsons"),
        manifest_path=summary_json,
        label="blind-overlay summary JSON",
    )
    require_disjoint_artifact_paths(
        input_paths=(
            adjudicated_oracle_csv,
            *((adjudication_summary_json,) if adjudication_summary_json else ()),
            *review_packet_csvs,
            *blind_overlay_summary_jsons,
        ),
        output_paths=(packet_csv, summary_json),
        label="oracle promotion",
    )

    adjudicated_rows = read_delimited_rows(
        adjudicated_oracle_csv,
        required_columns=ADJUDICATED_REQUIRED_COLUMNS,
        delimiter=",",
        encoding="utf-8-sig",
    )
    _validate_adjudication_summary(adjudication_summary_json, adjudicated_rows)
    review_packets = _read_review_packets(review_packet_csvs)
    overlay_bundles = _read_blind_overlay_summaries(
        blind_overlay_summary_jsons
    )
    rebuilt_rows = build_oracle_promotion_rows(
        adjudicated_rows=adjudicated_rows,
        review_packet_rows=review_packets.rows,
        review_hash_by_packet_row_id=review_packets.sha256_by_packet_row_id,
        overlay_bundles=overlay_bundles,
        oracle_packet_id=oracle_packet_id,
    )
    expected_rows = tuple(
        {
            column: format_diagnostic_value(row.get(column, ""))
            for column in PROMOTION_COLUMNS
        }
        for row in rebuilt_rows
    )
    if rows != expected_rows:
        raise ValueError(
            "oracle promotion rows are not the deterministic projection of "
            "their recorded adjudication/review/overlay sources"
        )
    _revalidate_promotion_sources(
        inputs=inputs,
        summary_json=summary_json,
        adjudicated_oracle_csv=adjudicated_oracle_csv,
        adjudication_summary_json=adjudication_summary_json,
        review_packet_csvs=review_packet_csvs,
        blind_overlay_summary_jsons=blind_overlay_summary_jsons,
    )
    if file_sha256(packet_csv) != packet_sha256:
        raise ValueError("oracle promotion packet changed during verification")
    if file_sha256(summary_json) != summary_sha256:
        raise ValueError("oracle promotion summary changed during verification")
    return VerifiedOraclePromotionBundle(
        packet_csv=packet_csv.resolve(),
        summary_json=summary_json.resolve(),
        packet_csv_sha256=packet_sha256,
        summary_json_sha256=summary_sha256,
        oracle_packet_id=oracle_packet_id,
        rows=rows,
        review_packets=review_packets,
        review_packet_csvs=review_packet_csvs,
        blind_overlay_summary_jsons=blind_overlay_summary_jsons,
    )


def build_oracle_promotion_rows(
    *,
    adjudicated_rows: Sequence[Mapping[str, str]],
    review_packet_rows: Sequence[Mapping[str, str]],
    review_hash_by_packet_row_id: Mapping[str, str],
    overlay_bundles: Mapping[str, BlindOverlayEvidenceBundle],
    oracle_packet_id: str,
) -> tuple[dict[str, object], ...]:
    if not oracle_packet_id.strip():
        raise ValueError("oracle_packet_id must be non-empty")
    if not adjudicated_rows:
        raise ValueError("adjudicated rows must be non-empty")
    review_by_packet_row_id = _unique_by_packet_row_id(review_packet_rows)
    adjudicated_by_packet_row_id = _unique_adjudicated_by_packet_row_id(
        adjudicated_rows
    )
    unexpected_adjudicated_ids = sorted(
        set(adjudicated_by_packet_row_id) - set(review_by_packet_row_id)
    )
    if unexpected_adjudicated_ids:
        raise ValueError(
            "adjudicated rows contain packet_row_id values outside the review "
            f"packet: {unexpected_adjudicated_ids!r}"
        )
    _validate_overlay_bundles_against_review_packets(
        overlay_bundles=overlay_bundles,
        review_by_packet_row_id=review_by_packet_row_id,
        review_hash_by_packet_row_id=review_hash_by_packet_row_id,
    )
    output_rows: list[dict[str, object]] = []
    referenced_review_hashes: set[str] = set()
    referenced_overlay_hashes: set[str] = set()

    for row in adjudicated_rows:
        packet_row_id = _required_text(row, "packet_row_id")
        review_row = review_by_packet_row_id.get(packet_row_id)
        if review_row is None:
            raise ValueError(f"adjudicated row missing review packet: {packet_row_id}")
        oracle_call = _oracle_call(row)
        candidate_id = _required_text(row, "candidate_id")
        if candidate_id != _required_text(review_row, "candidate_id"):
            raise ValueError(
                f"{packet_row_id}: candidate_id does not match review packet"
            )
        if _required_text(review_row, "oracle_status") != "pending_human_review":
            raise ValueError(
                f"{packet_row_id}: review packet oracle_status is not pending"
            )
        review_packet_sha256 = _normalized_sha256(
            row.get("review_packet_csv_sha256", ""),
            label=f"{packet_row_id}: review_packet_csv_sha256",
        )
        expected_review_sha256 = review_hash_by_packet_row_id.get(packet_row_id)
        if expected_review_sha256 is None:
            raise ValueError(
                f"{packet_row_id}: review packet source hash is unavailable"
            )
        if review_packet_sha256 != expected_review_sha256:
            raise ValueError(
                f"{packet_row_id}: adjudicated review_packet_csv_sha256 does not "
                "match the supplied review packet"
            )
        referenced_review_hashes.add(review_packet_sha256)
        overlay_summary_sha256 = _normalized_sha256(
            row.get("blind_overlay_summary_json_sha256", ""),
            label=f"{packet_row_id}: blind_overlay_summary_json_sha256",
        )
        overlay_bundle = overlay_bundles.get(overlay_summary_sha256)
        if overlay_bundle is None:
            raise ValueError(
                f"{packet_row_id}: adjudicated blind-overlay summary hash was not "
                "supplied"
            )
        referenced_overlay_hashes.add(overlay_summary_sha256)
        if overlay_bundle.review_packet_csv_sha256 != review_packet_sha256:
            raise ValueError(
                f"{packet_row_id}: blind-overlay summary does not bind the "
                "supplied review packet"
            )
        sealed_machine_row = overlay_bundle.sealed_machine_rows_by_packet_row_id.get(
            packet_row_id
        )
        if sealed_machine_row is None:
            raise ValueError(
                f"{packet_row_id}: sealed machine-call artifact is missing the "
                "review packet row"
            )
        _validate_machine_row_identity(
            machine_row=sealed_machine_row,
            review_row=review_row,
            packet_row_id=packet_row_id,
            label="sealed machine-call artifact",
        )
        sealed_machine_call = _required_text(sealed_machine_row, "machine_call")
        sealed_machine_reason = _required_text(
            sealed_machine_row,
            "machine_call_reason",
        )
        for field, sealed_value in (
            ("machine_call", sealed_machine_call),
            ("machine_call_reason", sealed_machine_reason),
        ):
            adjudicated_value = str(row.get(field, ""))
            if adjudicated_value != sealed_value:
                raise ValueError(
                    f"{packet_row_id}: adjudicated {field} does not exactly match "
                    "the sealed machine-call artifact"
                )

        output_rows.append(
            {
                "schema_version": SCHEMA_VERSION,
                "readiness_tier": READINESS_TIER,
                "evidence_owner": EVIDENCE_OWNER,
                "authority_scope": AUTHORITY_SCOPE,
                "oracle_packet_id": oracle_packet_id,
                "oracle_status": "manual_oracle_promoted",
                "oracle_call": oracle_call,
                "oracle_detection_status": ORACLE_DETECTION_STATUS,
                "packet_row_id": packet_row_id,
                "review_packet_csv_sha256": review_packet_sha256,
                "blind_overlay_summary_json_sha256": overlay_summary_sha256,
                "dataset": _required_text(row, "dataset"),
                "case_index": _required_text(row, "case_index"),
                "sample_stem": _required_text(review_row, "sample_stem"),
                "candidate_id": candidate_id,
                "base_mz": _review_coordinate_value(
                    adjudicated_row=row,
                    review_row=review_row,
                    field="base_mz",
                    packet_row_id=packet_row_id,
                ),
                "base_rt_apex_min": _review_coordinate_value(
                    adjudicated_row=row,
                    review_row=review_row,
                    field="base_rt_apex_min",
                    packet_row_id=packet_row_id,
                ),
                "adjudication_basis": _required_text(row, "adjudication_basis"),
                "human_review_call": oracle_call,
                "machine_call": sealed_machine_call,
                "human_review_note": row.get("note_optional", ""),
                "machine_call_reason": sealed_machine_reason,
                "review_packet_schema_version": _required_text(
                    review_row,
                    "schema_version",
                ),
                "review_packet_readiness_tier": _required_text(
                    review_row,
                    "readiness_tier",
                ),
                "review_packet_oracle_status": _required_text(
                    review_row,
                    "oracle_status",
                ),
                "review_packet_combined_pt_pattern_status": _required_text(
                    review_row,
                    "combined_pt_pattern_status",
                ),
                "provenance_status": "exact_review_evidence_bundle_matched",
            }
        )
    _require_exact_source_hash_set(
        referenced=referenced_review_hashes,
        supplied={
            _normalized_sha256(
                digest,
                label="supplied review packet SHA-256",
            )
            for digest in review_hash_by_packet_row_id.values()
        },
        label="review packet source set",
    )
    _require_exact_source_hash_set(
        referenced=referenced_overlay_hashes,
        supplied=set(overlay_bundles),
        label="blind-overlay source set",
    )
    return tuple(
        sorted(
            output_rows,
            key=lambda row: str(row["packet_row_id"]),
        )
    )


def _canonical_source_paths(
    paths: Sequence[Path],
    *,
    label: str,
) -> tuple[Path, ...]:
    if not paths:
        raise ValueError(f"at least one {label} is required")
    resolved = tuple(path.resolve() for path in paths)
    if len(set(resolved)) != len(resolved):
        raise ValueError(f"duplicate {label} path")
    return tuple(sorted(resolved, key=_canonical_path_sort_key))


def _artifact_descriptor(path: Path, *, relative_to: Path) -> dict[str, str]:
    resolved = path.resolve()
    try:
        recorded_path = Path(os.path.relpath(resolved, relative_to)).as_posix()
    except ValueError:
        recorded_path = str(resolved)
    return {
        "path": recorded_path,
        "sha256": file_sha256(resolved),
    }


def _load_artifact_descriptor(
    value: object,
    *,
    manifest_path: Path,
    label: str,
) -> Path:
    if not isinstance(value, Mapping):
        raise ValueError(f"oracle promotion {label} descriptor must be an object")
    _require_exact_keys(
        value,
        expected={"path", "sha256"},
        label=f"oracle promotion {label} descriptor",
    )
    raw_path_value = value.get("path")
    if not isinstance(raw_path_value, str):
        raise ValueError(f"oracle promotion {label} path must be a string")
    raw_path = raw_path_value.strip()
    if not raw_path:
        raise ValueError(f"oracle promotion {label} path must be non-empty")
    resolved = Path(raw_path)
    if not resolved.is_absolute():
        resolved = manifest_path.parent / resolved
    resolved = resolved.resolve()
    if not resolved.is_file():
        raise ValueError(f"oracle promotion {label} does not exist: {resolved}")
    expected_sha256 = _normalized_sha256(
        value.get("sha256"),
        label=f"oracle promotion {label} SHA-256",
    )
    if file_sha256(resolved) != expected_sha256:
        raise ValueError(f"oracle promotion {label} SHA-256 mismatch")
    return resolved


def _load_artifact_descriptor_list(
    value: object,
    *,
    manifest_path: Path,
    label: str,
) -> tuple[Path, ...]:
    if not isinstance(value, list) or not value:
        raise ValueError(
            f"oracle promotion {label} descriptors must be a non-empty list"
        )
    paths = tuple(
        _load_artifact_descriptor(
            item,
            manifest_path=manifest_path,
            label=f"{label} {index}",
        )
        for index, item in enumerate(value, start=1)
    )
    if len(set(paths)) != len(paths):
        raise ValueError(f"oracle promotion contains duplicate {label} paths")
    if paths != tuple(sorted(paths, key=_canonical_path_sort_key)):
        raise ValueError(f"oracle promotion {label} paths are not canonical")
    return paths


def _canonical_path_sort_key(path: Path) -> tuple[str, str]:
    text = str(path)
    return text.casefold(), text


def _revalidate_promotion_sources(
    *,
    inputs: Mapping[str, object],
    summary_json: Path,
    adjudicated_oracle_csv: Path,
    adjudication_summary_json: Path | None,
    review_packet_csvs: tuple[Path, ...],
    blind_overlay_summary_jsons: tuple[Path, ...],
) -> None:
    if _load_artifact_descriptor(
        inputs.get("adjudicated_oracle_csv"),
        manifest_path=summary_json,
        label="adjudicated oracle CSV",
    ) != adjudicated_oracle_csv:
        raise ValueError("oracle promotion adjudicated source changed")
    if adjudication_summary_json is not None:
        if _load_artifact_descriptor(
            inputs.get("adjudication_summary_json"),
            manifest_path=summary_json,
            label="adjudication summary JSON",
        ) != adjudication_summary_json:
            raise ValueError("oracle promotion adjudication summary changed")
    if _load_artifact_descriptor_list(
        inputs.get("review_packet_csvs"),
        manifest_path=summary_json,
        label="review packet CSV",
    ) != review_packet_csvs:
        raise ValueError("oracle promotion review packet sources changed")
    if _load_artifact_descriptor_list(
        inputs.get("blind_overlay_summary_jsons"),
        manifest_path=summary_json,
        label="blind-overlay summary JSON",
    ) != blind_overlay_summary_jsons:
        raise ValueError("oracle promotion blind-overlay sources changed")


def _require_exact_keys(
    value: Mapping[object, object],
    *,
    expected: set[str],
    label: str,
) -> None:
    observed = {str(key) for key in value}
    if observed != expected:
        missing = sorted(expected - observed)
        extra = sorted(observed - expected)
        raise ValueError(
            f"{label} keys mismatch; missing={missing!r}; extra={extra!r}"
        )


def _required_mapping(
    row: Mapping[str, object],
    field: str,
    *,
    label: str,
) -> Mapping[str, object]:
    value = row.get(field)
    if not isinstance(value, Mapping):
        raise ValueError(f"{label} {field} must be an object")
    return value


def _required_manifest_int(row: Mapping[str, object], field: str) -> int:
    value = row.get(field)
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"oracle promotion summary {field} must be an integer >= 0")
    return value


def _required_count_mapping(
    row: Mapping[str, object],
    field: str,
) -> dict[str, int]:
    value = row.get(field)
    if not isinstance(value, Mapping):
        raise ValueError(
            f"oracle promotion summary {field} must be an object"
        )
    counts: dict[str, int] = {}
    for key, count in value.items():
        if not isinstance(key, str) or not key:
            raise ValueError(
                f"oracle promotion summary {field} keys must be non-empty strings"
            )
        if isinstance(count, bool) or not isinstance(count, int) or count < 0:
            raise ValueError(
                f"oracle promotion summary {field} values must be integers >= 0"
            )
        counts[key] = count
    return counts


def _read_review_packets(
    paths: Sequence[Path],
) -> VerifiedReviewPacketCollection:
    return load_verified_review_packet_collection(
        paths,
        require_nonempty=True,
    )


def _read_blind_overlay_summaries(
    paths: Sequence[Path],
) -> dict[str, BlindOverlayEvidenceBundle]:
    if not paths:
        raise ValueError("at least one blind-overlay summary JSON is required")
    bundles: dict[str, BlindOverlayEvidenceBundle] = {}
    for path in paths:
        verified = load_verified_blind_overlay_bundle(path)
        summary_sha256 = verified.summary_json_sha256
        bundle = BlindOverlayEvidenceBundle(
            summary_json_sha256=summary_sha256,
            review_packet_csv_sha256=(verified.review_packet.review_packet_csv_sha256),
            sealed_machine_rows_by_packet_row_id=(
                verified.machine_rows_by_packet_row_id
            ),
        )
        if summary_sha256 in bundles:
            raise ValueError(
                f"duplicate blind-overlay summary artifact: {summary_sha256}"
            )
        bundles[summary_sha256] = bundle
    return bundles


def _validate_adjudication_summary(
    path: Path | None,
    rows: Sequence[Mapping[str, str]],
) -> None:
    if path is None:
        return
    summary = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(summary, Mapping):
        raise ValueError(f"{path}: adjudication summary must be a JSON object")
    call_counts = Counter(
        _normalize_call(row.get("adjudicated_call", "")) for row in rows
    )
    expected = {
        "total_rows": len(rows),
        "accept_count": call_counts.get("accept", 0),
        "reject_count": call_counts.get("reject", 0),
        "uncertain_count": call_counts.get("uncertain", 0),
    }
    for key, value in expected.items():
        observed = summary.get(key)
        if (
            isinstance(observed, bool)
            or not isinstance(observed, int)
            or observed < 0
        ):
            raise ValueError(f"{path}: {key} must be an integer >= 0")
        if observed != value:
            raise ValueError(
                f"{path}: {key}={observed!r} does not match {value}",
            )
    if expected["uncertain_count"] != 0:
        raise ValueError(f"{path}: promotion requires zero uncertain rows")


def _unique_by_packet_row_id(
    rows: Sequence[Mapping[str, str]],
) -> dict[str, Mapping[str, str]]:
    by_id: dict[str, Mapping[str, str]] = {}
    for row in rows:
        packet_row_id = _required_text(row, "packet_row_id")
        if packet_row_id in by_id:
            raise ValueError(f"duplicate review packet_row_id: {packet_row_id}")
        by_id[packet_row_id] = row
    return by_id


def _unique_adjudicated_by_packet_row_id(
    rows: Sequence[Mapping[str, str]],
) -> dict[str, Mapping[str, str]]:
    by_id: dict[str, Mapping[str, str]] = {}
    for row in rows:
        packet_row_id = _required_text(row, "packet_row_id")
        if packet_row_id in by_id:
            raise ValueError(f"duplicate adjudicated packet_row_id: {packet_row_id}")
        by_id[packet_row_id] = row
    return by_id


def _require_exact_packet_row_id_set(
    *,
    expected: set[str],
    observed: set[str],
    label: str,
) -> None:
    missing = sorted(expected - observed)
    extra = sorted(observed - expected)
    if not missing and not extra:
        return
    raise ValueError(
        f"{label} must exactly match the review packet candidate set; "
        f"missing packet_row_id={missing!r}; extra packet_row_id={extra!r}"
    )


def _require_exact_source_hash_set(
    *,
    referenced: set[str],
    supplied: set[str],
    label: str,
) -> None:
    missing = sorted(referenced - supplied)
    extra = sorted(supplied - referenced)
    if not missing and not extra:
        return
    raise ValueError(
        f"{label} must exactly match adjudicated dependencies; "
        f"missing SHA-256={missing!r}; extra SHA-256={extra!r}"
    )


def _validate_machine_row_identity(
    *,
    machine_row: Mapping[str, str],
    review_row: Mapping[str, str],
    packet_row_id: str,
    label: str,
) -> None:
    mismatches = machine_row_identity_mismatches(
        machine_row=machine_row,
        review_row=review_row,
    )
    if mismatches:
        raise ValueError(
            f"{packet_row_id}: {label} {mismatches[0]} does not match the exact "
            "review packet row"
        )


def _validate_overlay_bundles_against_review_packets(
    *,
    overlay_bundles: Mapping[str, BlindOverlayEvidenceBundle],
    review_by_packet_row_id: Mapping[str, Mapping[str, str]],
    review_hash_by_packet_row_id: Mapping[str, str],
) -> None:
    for summary_sha256, bundle in overlay_bundles.items():
        if summary_sha256 != bundle.summary_json_sha256:
            raise ValueError(
                "blind-overlay bundle key does not match summary_json_sha256"
            )
        expected_packet_row_ids = {
            packet_row_id
            for packet_row_id, review_sha256 in review_hash_by_packet_row_id.items()
            if review_sha256 == bundle.review_packet_csv_sha256
        }
        observed_packet_row_ids = set(bundle.sealed_machine_rows_by_packet_row_id)
        _require_exact_packet_row_id_set(
            expected=expected_packet_row_ids,
            observed=observed_packet_row_ids,
            label=f"blind-overlay bundle {summary_sha256}",
        )
        for packet_row_id in expected_packet_row_ids:
            review_row = review_by_packet_row_id.get(packet_row_id)
            if review_row is None:
                raise ValueError(f"{packet_row_id}: review packet row is unavailable")
            _validate_machine_row_identity(
                machine_row=bundle.sealed_machine_rows_by_packet_row_id[packet_row_id],
                review_row=review_row,
                packet_row_id=packet_row_id,
                label="sealed machine-call artifact",
            )


def _oracle_call(row: Mapping[str, str]) -> str:
    call = _normalize_call(row.get("adjudicated_call", ""))
    if call not in ALLOWED_ORACLE_CALLS:
        raise ValueError(
            f"adjudicated_call must be accept or reject for promotion; got {call!r}",
        )
    user_call = _normalize_call(row.get("user_call", ""))
    if user_call not in ALLOWED_ORACLE_CALLS:
        raise ValueError(
            "user_call must be a fresh human accept or reject for promotion; "
            f"got {user_call!r}",
        )
    if call != user_call:
        raise ValueError(
            "adjudicated_call must equal the fresh human user_call; "
            f"got adjudicated_call={call!r}, user_call={user_call!r}",
        )
    if not _required_text(row, "adjudication_basis"):
        raise ValueError("adjudication_basis must be non-empty")
    return call


def _normalize_call(value: object) -> str:
    return str(value or "").strip().lower()


def _normalized_sha256(value: object, *, label: str) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{label} must be a string SHA-256 digest")
    normalized = value.strip().upper()
    if len(normalized) != 64 or any(
        character not in "0123456789ABCDEF" for character in normalized
    ):
        raise ValueError(f"{label} must be a 64-character SHA-256 digest")
    return normalized


def _required_text(row: Mapping[str, object], key: str) -> str:
    raw_value = row.get(key)
    if not isinstance(raw_value, str):
        raise ValueError(f"{key} must be a string")
    value = raw_value.strip()
    if not value:
        raise ValueError(f"{key} must be non-empty")
    return value


def _review_coordinate_value(
    *,
    adjudicated_row: Mapping[str, str],
    review_row: Mapping[str, str],
    field: str,
    packet_row_id: str,
) -> str:
    review_text = _required_text(review_row, field)
    review_value = optional_float(review_text)
    if review_value is None:
        raise ValueError(
            f"{packet_row_id}: exact review packet row has invalid {field}"
        )
    adjudicated_text = str(adjudicated_row.get(field, "")).strip()
    if not adjudicated_text:
        return review_text
    adjudicated_value = optional_float(adjudicated_text)
    if adjudicated_value is None or adjudicated_value != review_value:
        raise ValueError(
            f"{packet_row_id}: adjudicated {field} does not match the exact "
            "review packet row"
        )
    return review_text
