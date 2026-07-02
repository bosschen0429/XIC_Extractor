"""Promotion packet builder for adjudicated Pt pattern manual oracles."""

from __future__ import annotations

import json
from collections import Counter
from collections.abc import Mapping, Sequence
from pathlib import Path

from xic_extractor.discovery.pt_pattern_review_packet import PACKET_COLUMNS
from xic_extractor.tabular_io import (
    file_sha256,
    read_delimited_rows,
    write_delimited_rows,
)

SCHEMA_VERSION = "pt_pattern_oracle_promotion_packet.v1"
READINESS_TIER = "production_candidate"
EVIDENCE_OWNER = "xic_extractor.discovery.pt_pattern_oracle_promotion"
AUTHORITY_SCOPE = "bounded_manual_oracle_only"

ADJUDICATED_REQUIRED_COLUMNS = (
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


def run_oracle_promotion(
    *,
    adjudicated_oracle_csv: Path,
    review_packet_csvs: Sequence[Path],
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
    review_packet_rows = _read_review_packets(review_packet_csvs)
    _validate_adjudication_summary(
        adjudication_summary_json,
        adjudicated_rows,
    )
    promotion_rows = build_oracle_promotion_rows(
        adjudicated_rows=adjudicated_rows,
        review_packet_rows=review_packet_rows,
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
    oracle_packet_id: str,
) -> tuple[dict[str, object], ...]:
    if not oracle_packet_id.strip():
        raise ValueError("oracle_packet_id must be non-empty")
    if not adjudicated_rows:
        raise ValueError("adjudicated rows must be non-empty")
    review_by_packet_row_id = _unique_by_packet_row_id(review_packet_rows)
    seen_packet_row_ids: set[str] = set()
    output_rows: list[dict[str, object]] = []

    for row in adjudicated_rows:
        packet_row_id = _required_text(row, "packet_row_id")
        if packet_row_id in seen_packet_row_ids:
            raise ValueError(f"duplicate adjudicated packet_row_id: {packet_row_id}")
        seen_packet_row_ids.add(packet_row_id)
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

        output_rows.append(
            {
                "schema_version": SCHEMA_VERSION,
                "readiness_tier": READINESS_TIER,
                "evidence_owner": EVIDENCE_OWNER,
                "authority_scope": AUTHORITY_SCOPE,
                "oracle_packet_id": oracle_packet_id,
                "oracle_status": "manual_oracle_promoted",
                "oracle_call": oracle_call,
                "oracle_detection_status": (
                    "detected" if oracle_call == "accept" else "non_detected"
                ),
                "packet_row_id": packet_row_id,
                "dataset": _required_text(row, "dataset"),
                "case_index": _required_text(row, "case_index"),
                "sample_stem": _required_text(review_row, "sample_stem"),
                "candidate_id": candidate_id,
                "base_mz": _preferred_value(row, review_row, "base_mz"),
                "base_rt_apex_min": _preferred_value(
                    row,
                    review_row,
                    "base_rt_apex_min",
                ),
                "adjudication_basis": _required_text(row, "adjudication_basis"),
                "human_review_call": _required_text(row, "user_call"),
                "machine_call": _required_text(row, "machine_call"),
                "human_review_note": row.get("note_optional", ""),
                "machine_call_reason": row.get("machine_call_reason", ""),
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
                "provenance_status": "review_packet_row_matched",
            }
        )
    return tuple(output_rows)


def _read_review_packets(paths: Sequence[Path]) -> list[dict[str, str]]:
    if not paths:
        raise ValueError("at least one review packet CSV is required")
    rows: list[dict[str, str]] = []
    for path in paths:
        rows.extend(
            read_delimited_rows(
                path,
                required_columns=PACKET_COLUMNS,
                delimiter=",",
                encoding="utf-8-sig",
            )
        )
    return rows


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


def _oracle_call(row: Mapping[str, str]) -> str:
    call = _normalize_call(row.get("adjudicated_call", ""))
    if call not in ALLOWED_ORACLE_CALLS:
        raise ValueError(
            "adjudicated_call must be accept or reject for promotion; "
            f"got {call!r}",
        )
    if not _required_text(row, "adjudication_basis"):
        raise ValueError("adjudication_basis must be non-empty")
    return call


def _normalize_call(value: object) -> str:
    return str(value or "").strip().lower()


def _required_text(row: Mapping[str, str], key: str) -> str:
    value = str(row.get(key, "")).strip()
    if not value:
        raise ValueError(f"{key} must be non-empty")
    return value


def _preferred_value(
    primary: Mapping[str, str],
    secondary: Mapping[str, str],
    key: str,
) -> str:
    value = str(primary.get(key, "")).strip()
    if value:
        return value
    return _required_text(secondary, key)
