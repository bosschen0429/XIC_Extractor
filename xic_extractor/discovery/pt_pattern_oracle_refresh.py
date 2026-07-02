"""Refresh helper for Pt pattern manual oracle rows against current packets."""

from __future__ import annotations

import json
from collections import Counter
from collections.abc import Mapping, Sequence
from pathlib import Path

from xic_extractor.discovery.pt_pattern_blind_overlay import MACHINE_CALL_COLUMNS
from xic_extractor.discovery.pt_pattern_oracle_promotion import (
    ADJUDICATED_REQUIRED_COLUMNS,
)
from xic_extractor.discovery.pt_pattern_review_packet import PACKET_COLUMNS
from xic_extractor.tabular_io import (
    file_sha256,
    read_delimited_rows,
    write_delimited_rows,
)

SCHEMA_VERSION = "pt_pattern_oracle_refresh.v1"
READINESS_TIER = "diagnostic_only"
EVIDENCE_OWNER = "xic_extractor.discovery.pt_pattern_oracle_refresh"
AUTHORITY_SCOPE = "v2_manual_oracle_refresh_worklist_only"

CARRYOVER_STATUS = "carryover_label"
PENDING_STATUS = "pending_human_review"
CONFLICT_STATUS = "provenance_conflict"
STALE_STATUS = "stale_old_oracle_row"

REFRESH_DRAFT_COLUMNS = ADJUDICATED_REQUIRED_COLUMNS + (
    "schema_version",
    "readiness_tier",
    "evidence_owner",
    "authority_scope",
    "refresh_packet_id",
    "refresh_status",
    "provenance_status",
    "machine_call_status",
    "current_packet_label",
    "current_source_label",
    "current_sample_stem",
    "current_review_packet_schema_version",
    "current_review_packet_readiness_tier",
    "current_review_packet_oracle_status",
    "current_combined_pt_pattern_status",
    "current_trace_decision_basis",
    "current_trace_smoothing_method",
    "current_trace_smoothing_window_points",
    "current_non_base_positions_supported",
    "current_max_abs_apex_delta_sec",
    "current_min_pearson_r_to_base",
    "current_min_active_overlap_fraction",
    "source_dataset",
    "source_case_index",
    "source_adjudicated_call",
    "source_user_call",
    "source_machine_call",
    "source_machine_call_reason",
    "source_note_optional",
    "source_adjudication_basis",
)

PENDING_REVIEW_COLUMNS = (
    "dataset",
    "case_index",
    "adjudicated_call",
    "adjudication_basis",
    "user_call",
    "note_optional",
    "base_mz",
    "base_rt_apex_min",
    "candidate_id",
    "packet_row_id",
    "refresh_status",
    "provenance_status",
    "current_packet_label",
    "current_source_label",
    "current_sample_stem",
    "current_combined_pt_pattern_status",
    "current_trace_decision_basis",
    "current_trace_smoothing_method",
    "current_trace_smoothing_window_points",
    "current_non_base_positions_supported",
    "current_max_abs_apex_delta_sec",
    "current_min_pearson_r_to_base",
    "current_min_active_overlap_fraction",
)

STALE_ORACLE_COLUMNS = ADJUDICATED_REQUIRED_COLUMNS + (
    "schema_version",
    "readiness_tier",
    "evidence_owner",
    "authority_scope",
    "refresh_packet_id",
    "refresh_status",
    "provenance_status",
)

ALLOWED_REFRESH_CALLS = ("accept", "reject")


def run_oracle_refresh(
    *,
    adjudicated_oracle_csv: Path,
    review_packet_csvs: Sequence[Path],
    machine_call_csvs: Sequence[Path],
    output_dir: Path,
    refresh_packet_id: str,
) -> dict[str, Path]:
    adjudicated_rows = read_delimited_rows(
        adjudicated_oracle_csv,
        required_columns=ADJUDICATED_REQUIRED_COLUMNS,
        delimiter=",",
        encoding="utf-8-sig",
    )
    review_packet_rows = _read_review_packets(review_packet_csvs)
    machine_call_rows = _read_machine_calls(machine_call_csvs)
    refresh = build_oracle_refresh_rows(
        adjudicated_rows=adjudicated_rows,
        review_packet_rows=review_packet_rows,
        machine_call_rows=machine_call_rows,
        refresh_packet_id=refresh_packet_id,
    )

    output_dir.mkdir(parents=True, exist_ok=True)
    draft_csv = output_dir / "pt_pattern_oracle_refresh_draft.csv"
    pending_csv = output_dir / "pt_pattern_oracle_refresh_pending_review.csv"
    stale_csv = output_dir / "pt_pattern_oracle_refresh_stale_v1_rows.csv"
    summary_json = output_dir / "pt_pattern_oracle_refresh_summary.json"

    write_delimited_rows(draft_csv, refresh["draft_rows"], REFRESH_DRAFT_COLUMNS)
    write_delimited_rows(
        pending_csv,
        refresh["pending_rows"],
        PENDING_REVIEW_COLUMNS,
    )
    write_delimited_rows(stale_csv, refresh["stale_rows"], STALE_ORACLE_COLUMNS)

    status_counts = Counter(
        str(row["refresh_status"]) for row in refresh["draft_rows"]
    )
    machine_call_counts = Counter(
        str(row["machine_call_status"]) for row in refresh["draft_rows"]
    )
    pending_count = int(status_counts.get(PENDING_STATUS, 0))
    conflict_count = int(status_counts.get(CONFLICT_STATUS, 0))
    missing_machine_call_count = int(
        machine_call_counts.get("missing_current_machine_call", 0)
    )
    promotable_now = (
        pending_count == 0
        and conflict_count == 0
        and missing_machine_call_count == 0
        and len(refresh["draft_rows"]) > 0
    )
    summary = {
        "schema_version": SCHEMA_VERSION,
        "readiness_tier": READINESS_TIER,
        "evidence_owner": EVIDENCE_OWNER,
        "authority_scope": AUTHORITY_SCOPE,
        "refresh_packet_id": refresh_packet_id,
        "adjudicated_oracle_csv": str(adjudicated_oracle_csv),
        "adjudicated_oracle_csv_sha256": file_sha256(adjudicated_oracle_csv),
        "review_packet_csvs": [str(path) for path in review_packet_csvs],
        "review_packet_csv_sha256s": {
            str(path): file_sha256(path) for path in review_packet_csvs
        },
        "machine_call_csvs": [str(path) for path in machine_call_csvs],
        "machine_call_csv_sha256s": {
            str(path): file_sha256(path) for path in machine_call_csvs
        },
        "current_review_row_count": len(refresh["draft_rows"]),
        "old_oracle_row_count": len(adjudicated_rows),
        "carryover_label_count": int(status_counts.get(CARRYOVER_STATUS, 0)),
        "pending_human_review_count": pending_count,
        "provenance_conflict_count": conflict_count,
        "stale_old_oracle_row_count": len(refresh["stale_rows"]),
        "machine_call_status_counts": dict(sorted(machine_call_counts.items())),
        "promotable_now": promotable_now,
        "promotion_blocker": (
            ""
            if promotable_now
            else "pending_or_conflicting_rows_must_be_resolved_before_promotion"
        ),
        "changes_matrix": False,
        "changes_counted_detection": False,
        "changes_export": False,
        "outputs": {
            "refresh_draft_csv": str(draft_csv),
            "pending_review_csv": str(pending_csv),
            "stale_v1_rows_csv": str(stale_csv),
        },
    }
    summary_json.write_text(
        json.dumps(summary, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return {
        "refresh_draft_csv": draft_csv,
        "pending_review_csv": pending_csv,
        "stale_v1_rows_csv": stale_csv,
        "summary_json": summary_json,
    }


def build_oracle_refresh_rows(
    *,
    adjudicated_rows: Sequence[Mapping[str, str]],
    review_packet_rows: Sequence[Mapping[str, str]],
    machine_call_rows: Sequence[Mapping[str, str]],
    refresh_packet_id: str,
) -> dict[str, tuple[dict[str, object], ...]]:
    if not refresh_packet_id.strip():
        raise ValueError("refresh_packet_id must be non-empty")
    if not review_packet_rows:
        raise ValueError("review packet rows must be non-empty")

    adjudicated_by_id = _unique_by_packet_row_id(adjudicated_rows, "adjudicated")
    machine_by_id = _unique_by_packet_row_id(machine_call_rows, "machine call")
    seen_current_ids: set[str] = set()
    draft_rows: list[dict[str, object]] = []
    pending_rows: list[dict[str, object]] = []

    for index, review_row in enumerate(review_packet_rows, start=1):
        packet_row_id = _required_text(review_row, "packet_row_id")
        if packet_row_id in seen_current_ids:
            raise ValueError(f"duplicate review packet_row_id: {packet_row_id}")
        seen_current_ids.add(packet_row_id)
        source_row = adjudicated_by_id.get(packet_row_id)
        machine_row = machine_by_id.get(packet_row_id)
        draft_row = _build_draft_row(
            review_row=review_row,
            source_row=source_row,
            machine_row=machine_row,
            refresh_packet_id=refresh_packet_id,
            case_index=f"v2-{index:03d}",
        )
        draft_rows.append(draft_row)
        if draft_row["refresh_status"] != CARRYOVER_STATUS:
            pending_rows.append(
                {key: draft_row.get(key, "") for key in PENDING_REVIEW_COLUMNS}
            )

    stale_rows = [
        _stale_row(
            source_row=source_row,
            refresh_packet_id=refresh_packet_id,
        )
        for packet_row_id, source_row in sorted(adjudicated_by_id.items())
        if packet_row_id not in seen_current_ids
    ]
    return {
        "draft_rows": tuple(draft_rows),
        "pending_rows": tuple(pending_rows),
        "stale_rows": tuple(stale_rows),
    }


def _build_draft_row(
    *,
    review_row: Mapping[str, str],
    source_row: Mapping[str, str] | None,
    machine_row: Mapping[str, str] | None,
    refresh_packet_id: str,
    case_index: str,
) -> dict[str, object]:
    packet_row_id = _required_text(review_row, "packet_row_id")
    candidate_id = _required_text(review_row, "candidate_id")
    source_label = _required_text(review_row, "source_label")
    dataset = source_label
    base_row: dict[str, object] = {
        "dataset": dataset,
        "case_index": case_index,
        "adjudicated_call": "",
        "adjudication_basis": "",
        "user_call": "",
        "machine_call": _machine_text(machine_row, "machine_call"),
        "note_optional": "",
        "machine_call_reason": _machine_text(machine_row, "machine_call_reason"),
        "base_mz": _required_text(review_row, "base_mz"),
        "base_rt_apex_min": _required_text(review_row, "base_rt_apex_min"),
        "candidate_id": candidate_id,
        "packet_row_id": packet_row_id,
        "schema_version": SCHEMA_VERSION,
        "readiness_tier": READINESS_TIER,
        "evidence_owner": EVIDENCE_OWNER,
        "authority_scope": AUTHORITY_SCOPE,
        "refresh_packet_id": refresh_packet_id,
        "refresh_status": PENDING_STATUS,
        "provenance_status": "new_current_packet_row",
        "machine_call_status": (
            "current_machine_call_matched"
            if machine_row is not None
            else "missing_current_machine_call"
        ),
        "current_packet_label": review_row.get("packet_label", ""),
        "current_source_label": source_label,
        "current_sample_stem": review_row.get("sample_stem", ""),
        "current_review_packet_schema_version": review_row.get(
            "schema_version", ""
        ),
        "current_review_packet_readiness_tier": review_row.get(
            "readiness_tier", ""
        ),
        "current_review_packet_oracle_status": review_row.get(
            "oracle_status", ""
        ),
        "current_combined_pt_pattern_status": review_row.get(
            "combined_pt_pattern_status", ""
        ),
        "current_trace_decision_basis": review_row.get(
            "trace_decision_basis", ""
        ),
        "current_trace_smoothing_method": review_row.get(
            "trace_smoothing_method", ""
        ),
        "current_trace_smoothing_window_points": review_row.get(
            "trace_smoothing_window_points", ""
        ),
        "current_non_base_positions_supported": review_row.get(
            "non_base_positions_supported", ""
        ),
        "current_max_abs_apex_delta_sec": review_row.get(
            "max_abs_apex_delta_sec", ""
        ),
        "current_min_pearson_r_to_base": review_row.get(
            "min_pearson_r_to_base", ""
        ),
        "current_min_active_overlap_fraction": review_row.get(
            "min_active_overlap_fraction", ""
        ),
        "source_dataset": "",
        "source_case_index": "",
        "source_adjudicated_call": "",
        "source_user_call": "",
        "source_machine_call": "",
        "source_machine_call_reason": "",
        "source_note_optional": "",
        "source_adjudication_basis": "",
    }
    if source_row is None:
        return base_row

    base_row.update(
        {
            "source_dataset": source_row.get("dataset", ""),
            "source_case_index": source_row.get("case_index", ""),
            "source_adjudicated_call": source_row.get("adjudicated_call", ""),
            "source_user_call": source_row.get("user_call", ""),
            "source_machine_call": source_row.get("machine_call", ""),
            "source_machine_call_reason": source_row.get(
                "machine_call_reason", ""
            ),
            "source_note_optional": source_row.get("note_optional", ""),
            "source_adjudication_basis": source_row.get(
                "adjudication_basis", ""
            ),
        }
    )
    if _required_text(source_row, "candidate_id") != candidate_id:
        base_row.update(
            {
                "refresh_status": CONFLICT_STATUS,
                "provenance_status": "old_oracle_candidate_id_mismatch",
            }
        )
        return base_row

    source_call = _normalize_call(source_row.get("adjudicated_call", ""))
    if source_call not in ALLOWED_REFRESH_CALLS:
        base_row.update(
            {
                "refresh_status": CONFLICT_STATUS,
                "provenance_status": "old_oracle_call_not_promotable",
            }
        )
        return base_row

    base_row.update(
        {
            "adjudicated_call": source_call,
            "adjudication_basis": "v2_refresh_carryover_from_v1_manual_oracle",
            "user_call": source_row.get("user_call", ""),
            "note_optional": source_row.get("note_optional", ""),
            "refresh_status": CARRYOVER_STATUS,
            "provenance_status": "current_packet_row_matched_old_oracle",
        }
    )
    return base_row


def _stale_row(
    *,
    source_row: Mapping[str, str],
    refresh_packet_id: str,
) -> dict[str, object]:
    row: dict[str, object] = {
        key: source_row.get(key, "") for key in ADJUDICATED_REQUIRED_COLUMNS
    }
    row.update(
        {
            "schema_version": SCHEMA_VERSION,
            "readiness_tier": READINESS_TIER,
            "evidence_owner": EVIDENCE_OWNER,
            "authority_scope": AUTHORITY_SCOPE,
            "refresh_packet_id": refresh_packet_id,
            "refresh_status": STALE_STATUS,
            "provenance_status": "old_oracle_row_not_in_current_packet",
        }
    )
    return row


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


def _read_machine_calls(paths: Sequence[Path]) -> list[dict[str, str]]:
    if not paths:
        return []
    rows: list[dict[str, str]] = []
    for path in paths:
        rows.extend(
            read_delimited_rows(
                path,
                required_columns=MACHINE_CALL_COLUMNS,
                delimiter=",",
                encoding="utf-8-sig",
            )
        )
    return rows


def _unique_by_packet_row_id(
    rows: Sequence[Mapping[str, str]],
    label: str,
) -> dict[str, Mapping[str, str]]:
    by_id: dict[str, Mapping[str, str]] = {}
    for row in rows:
        packet_row_id = _required_text(row, "packet_row_id")
        if packet_row_id in by_id:
            raise ValueError(f"duplicate {label} packet_row_id: {packet_row_id}")
        by_id[packet_row_id] = row
    return by_id


def _machine_text(row: Mapping[str, str] | None, key: str) -> str:
    if row is None:
        return ""
    return str(row.get(key, "")).strip()


def _normalize_call(value: object) -> str:
    return str(value or "").strip().lower()


def _required_text(row: Mapping[str, str], key: str) -> str:
    value = str(row.get(key, "")).strip()
    if not value:
        raise ValueError(f"{key} must be non-empty")
    return value
