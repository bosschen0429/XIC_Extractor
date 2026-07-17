"""Promotion packet builder for bounded Pt pattern human-review labels."""

from __future__ import annotations

import json
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

from xic_extractor.discovery.pt_pattern_blind_overlay_call import (
    ANCHOR_LOCAL_SCHEMA_VERSION as OVERLAY_ANCHOR_SCHEMA_VERSION,
)
from xic_extractor.discovery.pt_pattern_blind_overlay_call import (
    LEGACY_MACHINE_CALL_REQUIRED_COLUMNS,
    LEGACY_PACKET_REQUIRED_COLUMNS,
    validate_machine_call_rows,
    validate_packet_rows,
)
from xic_extractor.discovery.pt_pattern_blind_overlay_call import (
    SCHEMA_VERSION as OVERLAY_SCHEMA_VERSION,
)
from xic_extractor.discovery.pt_pattern_review_packet import (
    EVIDENCE_OWNER as REVIEW_PACKET_EVIDENCE_OWNER,
)
from xic_extractor.tabular_io import (
    file_sha256,
    optional_float,
    read_delimited_rows,
    write_delimited_rows,
)

SCHEMA_VERSION = "pt_pattern_oracle_promotion_packet.v2"
READINESS_TIER = "diagnostic_only"
EVIDENCE_OWNER = "xic_extractor.discovery.pt_pattern_oracle_promotion"
AUTHORITY_SCOPE = "bounded_manual_oracle_only"

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


def run_oracle_promotion(
    *,
    adjudicated_oracle_csv: Path,
    review_packet_csvs: Sequence[Path],
    blind_overlay_summary_jsons: Sequence[Path],
    output_dir: Path,
    oracle_packet_id: str,
    adjudication_summary_json: Path | None = None,
) -> dict[str, Path]:
    adjudicated_rows = read_delimited_rows(
        adjudicated_oracle_csv,
        required_columns=ADJUDICATED_REQUIRED_COLUMNS,
        delimiter=",",
        encoding="utf-8-sig",
    )
    review_packet_rows, review_hash_by_packet_row_id = _read_review_packets(
        review_packet_csvs
    )
    overlay_bundles = _read_blind_overlay_summaries(blind_overlay_summary_jsons)
    _validate_adjudication_summary(
        adjudication_summary_json,
        adjudicated_rows,
    )
    promotion_rows = build_oracle_promotion_rows(
        adjudicated_rows=adjudicated_rows,
        review_packet_rows=review_packet_rows,
        review_hash_by_packet_row_id=review_hash_by_packet_row_id,
        overlay_bundles=overlay_bundles,
        oracle_packet_id=oracle_packet_id,
    )

    output_dir.mkdir(parents=True, exist_ok=True)
    packet_csv = output_dir / "pt_pattern_oracle_promotion_packet.csv"
    summary_json = output_dir / "pt_pattern_oracle_promotion_summary.json"
    write_delimited_rows(packet_csv, promotion_rows, PROMOTION_COLUMNS)

    call_counts = Counter(str(row["oracle_call"]) for row in promotion_rows)
    status_counts = Counter(
        str(row["oracle_detection_status"]) for row in promotion_rows
    )
    summary = {
        "schema_version": SCHEMA_VERSION,
        "readiness_tier": READINESS_TIER,
        "evidence_owner": EVIDENCE_OWNER,
        "authority_scope": AUTHORITY_SCOPE,
        "oracle_packet_id": oracle_packet_id,
        "adjudicated_oracle_csv": str(adjudicated_oracle_csv),
        "adjudicated_oracle_csv_sha256": file_sha256(adjudicated_oracle_csv),
        "adjudication_summary_json": (
            str(adjudication_summary_json) if adjudication_summary_json else ""
        ),
        "adjudication_summary_json_sha256": (
            file_sha256(adjudication_summary_json)
            if adjudication_summary_json
            else ""
        ),
        "review_packet_csvs": [str(path) for path in review_packet_csvs],
        "review_packet_csv_sha256s": {
            str(path): file_sha256(path) for path in review_packet_csvs
        },
        "blind_overlay_summary_jsons": [
            str(path) for path in blind_overlay_summary_jsons
        ],
        "blind_overlay_summary_json_sha256s": {
            str(path): file_sha256(path) for path in blind_overlay_summary_jsons
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
            "promotion_packet_csv": str(packet_csv),
        },
    }
    summary_json.write_text(
        json.dumps(summary, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return {"promotion_packet_csv": packet_csv, "summary_json": summary_json}


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
                "human_review_call": _required_text(row, "user_call"),
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
    return tuple(output_rows)


def _read_review_packets(
    paths: Sequence[Path],
) -> tuple[list[dict[str, str]], dict[str, str]]:
    if not paths:
        raise ValueError("at least one review packet CSV is required")
    rows: list[dict[str, str]] = []
    review_hash_by_packet_row_id: dict[str, str] = {}
    for path in paths:
        packet_rows = _read_review_packet(path)
        packet_sha256 = file_sha256(path)
        for row in packet_rows:
            packet_row_id = _required_text(row, "packet_row_id")
            if packet_row_id in review_hash_by_packet_row_id:
                raise ValueError(
                    f"duplicate review packet_row_id: {packet_row_id}"
                )
            review_hash_by_packet_row_id[packet_row_id] = packet_sha256
        rows.extend(packet_rows)
    return rows, review_hash_by_packet_row_id


def _read_review_packet(path: Path) -> list[dict[str, str]]:
    rows = read_delimited_rows(
        path,
        required_columns=LEGACY_PACKET_REQUIRED_COLUMNS,
        delimiter=",",
        encoding="utf-8-sig",
    )
    validate_packet_rows(rows)
    return rows


def _read_blind_overlay_summaries(
    paths: Sequence[Path],
) -> dict[str, BlindOverlayEvidenceBundle]:
    if not paths:
        raise ValueError("at least one blind-overlay summary JSON is required")
    bundles: dict[str, BlindOverlayEvidenceBundle] = {}
    for path in paths:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
        if not isinstance(payload, dict):
            raise ValueError(f"{path}: blind-overlay summary must be a JSON object")
        summary_sha256 = file_sha256(path)
        bundle = _validate_blind_overlay_summary(
            path,
            payload,
            summary_json_sha256=summary_sha256,
        )
        if summary_sha256 in bundles:
            raise ValueError(
                f"duplicate blind-overlay summary artifact: {summary_sha256}"
            )
        bundles[summary_sha256] = bundle
    return bundles


def _validate_blind_overlay_summary(
    summary_path: Path,
    summary: Mapping[str, object],
    *,
    summary_json_sha256: str,
) -> BlindOverlayEvidenceBundle:
    schema_version = str(summary.get("schema_version", "")).strip()
    if schema_version not in {OVERLAY_SCHEMA_VERSION, OVERLAY_ANCHOR_SCHEMA_VERSION}:
        raise ValueError(
            f"{summary_path}: unsupported blind-overlay schema_version "
            f"{schema_version!r}"
        )
    for key, expected in (
        ("readiness_tier", READINESS_TIER),
        ("evidence_owner", REVIEW_PACKET_EVIDENCE_OWNER),
        ("oracle_status", "pending_human_review"),
    ):
        if str(summary.get(key, "")).strip() != expected:
            raise ValueError(f"{summary_path}: {key} must be {expected!r}")
    for key in (
        "diagnostic_only",
        "sealed_machine_calls_written",
        "blind_to_machine_calls_in_html",
    ):
        if summary.get(key) is not True:
            raise ValueError(f"{summary_path}: {key} must be true")

    review_packet_path, review_packet_sha256 = _verify_summary_artifact(
        summary_path=summary_path,
        path_value=summary.get("review_packet_csv"),
        sha256_value=summary.get("review_packet_csv_sha256"),
        label="review packet",
    )
    _verify_summary_artifact(
        summary_path=summary_path,
        path_value=summary.get("trace_points_csv"),
        sha256_value=summary.get("trace_points_csv_sha256"),
        label="trace points",
    )

    context_path = summary.get("context_trace_points_csv")
    context_hash = str(summary.get("context_trace_points_csv_sha256", "")).strip()
    if context_path is not None and str(context_path).strip():
        _verify_summary_artifact(
            summary_path=summary_path,
            path_value=context_path,
            sha256_value=context_hash,
            label="context trace points",
        )
    elif context_hash:
        raise ValueError(
            f"{summary_path}: context trace hash exists without an artifact path"
        )

    outputs = summary.get("outputs")
    if not isinstance(outputs, Mapping):
        raise ValueError(f"{summary_path}: outputs must be a JSON object")
    _verify_summary_artifact(
        summary_path=summary_path,
        path_value=outputs.get("gallery_html"),
        sha256_value=outputs.get("gallery_html_sha256"),
        label="gallery HTML",
    )
    machine_calls_path, _ = _verify_summary_artifact(
        summary_path=summary_path,
        path_value=outputs.get("machine_calls_csv"),
        sha256_value=outputs.get("machine_calls_csv_sha256"),
        label="sealed machine calls",
    )

    review_packet_rows = _read_review_packet(review_packet_path)
    sealed_machine_rows = read_delimited_rows(
        machine_calls_path,
        required_columns=LEGACY_MACHINE_CALL_REQUIRED_COLUMNS,
        delimiter=",",
        encoding="utf-8-sig",
    )
    validate_machine_call_rows(sealed_machine_rows)
    review_by_packet_row_id = _unique_by_packet_row_id(review_packet_rows)
    machine_by_packet_row_id = _unique_machine_by_packet_row_id(
        sealed_machine_rows
    )
    _require_exact_packet_row_id_set(
        expected=set(review_by_packet_row_id),
        observed=set(machine_by_packet_row_id),
        label=f"{summary_path}: sealed machine-call rows",
    )
    for packet_row_id, review_row in review_by_packet_row_id.items():
        _validate_machine_row_identity(
            machine_row=machine_by_packet_row_id[packet_row_id],
            review_row=review_row,
            packet_row_id=packet_row_id,
            label=f"{summary_path}: sealed machine-call artifact",
        )

    return BlindOverlayEvidenceBundle(
        summary_json_sha256=summary_json_sha256,
        review_packet_csv_sha256=review_packet_sha256,
        sealed_machine_rows_by_packet_row_id=machine_by_packet_row_id,
    )


def _verify_summary_artifact(
    *,
    summary_path: Path,
    path_value: object,
    sha256_value: object,
    label: str,
) -> tuple[Path, str]:
    recorded_path = str(path_value or "").strip()
    if not recorded_path:
        raise ValueError(f"{summary_path}: missing {label} path")
    expected_sha256 = _normalized_sha256(
        sha256_value,
        label=f"{summary_path}: {label} sha256",
    )
    artifact_path = _resolve_summary_artifact_path(summary_path, recorded_path)
    observed_sha256 = file_sha256(artifact_path)
    if observed_sha256 != expected_sha256:
        raise ValueError(
            f"{summary_path}: {label} sha256 does not match {artifact_path}"
        )
    return artifact_path, observed_sha256


def _resolve_summary_artifact_path(summary_path: Path, value: str) -> Path:
    recorded = Path(value)
    candidates = (
        (recorded if recorded.is_absolute() else Path.cwd() / recorded),
        summary_path.parent / recorded,
    )
    for candidate in candidates:
        resolved = candidate.resolve()
        if resolved.is_file():
            return resolved
    raise ValueError(f"{summary_path}: recorded artifact does not exist: {value}")


def _validate_adjudication_summary(
    path: Path | None,
    rows: Sequence[Mapping[str, str]],
) -> None:
    if path is None:
        return
    summary = json.loads(path.read_text(encoding="utf-8-sig"))
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
        if int(summary.get(key, -1)) != value:
            raise ValueError(
                f"{path}: {key}={summary.get(key)!r} does not match {value}",
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
            raise ValueError(
                f"duplicate adjudicated packet_row_id: {packet_row_id}"
            )
        by_id[packet_row_id] = row
    return by_id


def _unique_machine_by_packet_row_id(
    rows: Sequence[Mapping[str, str]],
) -> dict[str, Mapping[str, str]]:
    by_id: dict[str, Mapping[str, str]] = {}
    for row in rows:
        packet_row_id = _required_text(row, "packet_row_id")
        if packet_row_id in by_id:
            raise ValueError(
                f"duplicate sealed machine-call packet_row_id: {packet_row_id}"
            )
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


def _validate_machine_row_identity(
    *,
    machine_row: Mapping[str, str],
    review_row: Mapping[str, str],
    packet_row_id: str,
    label: str,
) -> None:
    for field in ("packet_row_id", "candidate_id", "packet_label", "source_label"):
        machine_value = _required_text(machine_row, field)
        review_value = _required_text(review_row, field)
        if machine_value != review_value:
            raise ValueError(
                f"{packet_row_id}: {label} {field} does not match the exact "
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
        observed_packet_row_ids = set(
            bundle.sealed_machine_rows_by_packet_row_id
        )
        _require_exact_packet_row_id_set(
            expected=expected_packet_row_ids,
            observed=observed_packet_row_ids,
            label=f"blind-overlay bundle {summary_sha256}",
        )
        for packet_row_id in expected_packet_row_ids:
            review_row = review_by_packet_row_id.get(packet_row_id)
            if review_row is None:
                raise ValueError(
                    f"{packet_row_id}: review packet row is unavailable"
                )
            _validate_machine_row_identity(
                machine_row=bundle.sealed_machine_rows_by_packet_row_id[
                    packet_row_id
                ],
                review_row=review_row,
                packet_row_id=packet_row_id,
                label="sealed machine-call artifact",
            )


def _oracle_call(row: Mapping[str, str]) -> str:
    call = _normalize_call(row.get("adjudicated_call", ""))
    if call not in ALLOWED_ORACLE_CALLS:
        raise ValueError(
            "adjudicated_call must be accept or reject for promotion; "
            f"got {call!r}",
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
    normalized = str(value or "").strip().upper()
    if len(normalized) != 64 or any(
        character not in "0123456789ABCDEF" for character in normalized
    ):
        raise ValueError(f"{label} must be a 64-character SHA-256 digest")
    return normalized


def _required_text(row: Mapping[str, str], key: str) -> str:
    value = str(row.get(key, "")).strip()
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
