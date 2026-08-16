"""Validate the historical source-sealed clean-target replay packet.

T5D removed the Standard Peak authority-bundle producer and its ownerless
writer. This checker validates the retained 84/112 experimental result only;
it cannot reconstruct current owner authority, run RAW, or authorize a write.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.validation_artifact_contracts import (  # noqa: E402
    check_summary_artifact_hashes,
    is_declared_externalized_artifact_path,
)
from xic_extractor.tabular_io import read_tsv_required, text_value  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
SCHEMA_VERSION = "backfill_expansion_clean_target_full_chain_replay_v1"
PACKET_SCOPE = "backfill_expansion_clean_target_split_decision_112_cells"

DEFAULT_DOCS_DIR = (
    ROOT
    / "docs/superpowers/validation/"
    "backfill_expansion_clean_target_full_chain_replay_v1"
)
DEFAULT_OUTPUT_DIR = (
    ROOT / "output/validation/backfill_expansion_clean_target_full_chain_replay_v1"
)
DEFAULT_SUMMARY_JSON = (
    DEFAULT_DOCS_DIR / "backfill_expansion_clean_target_full_chain_replay_summary.json"
)
DEFAULT_CHECKS_TSV = (
    DEFAULT_DOCS_DIR / "backfill_expansion_clean_target_full_chain_replay_checks.tsv"
)
DEFAULT_ROW_MANIFEST_TSV = (
    DEFAULT_DOCS_DIR
    / "backfill_expansion_clean_target_full_chain_replay_row_manifest.tsv"
)
DEFAULT_CELLS_TSV = (
    DEFAULT_OUTPUT_DIR / "backfill_expansion_clean_target_full_chain_replay_cells.tsv"
)

EXPECTED_COUNTS = {
    "split_decision_group_count": 18,
    "split_decision_family_count": 11,
    "clean_target_candidate_cell_count": 112,
    "boundary_review_excluded_cell_count": 37,
    "off_target_hold_or_remap_excluded_cell_count": 29,
    "missing_or_unclassified_excluded_cell_count": 0,
}

CHECK_COLUMNS = (
    "schema_version",
    "check_id",
    "status",
    "observed",
    "expected",
    "notes",
)
ROW_MANIFEST_COLUMNS = (
    "schema_version",
    "packet_scope",
    "peak_hypothesis_id",
    "sample_subtype",
    "split_decision_status",
    "clean_target_candidate_cell_count",
    "full_chain_pass_cell_count",
    "held_cell_count",
    "primary_blocker_counts",
    "projected_selective_full_chain_pass_cell_count",
    "projected_selective_held_cell_count",
    "projected_selective_primary_blocker_counts",
    "product_authority_effect",
    "next_gate",
)
CELL_COLUMNS = (
    "schema_version",
    "packet_scope",
    "peak_hypothesis_id",
    "sample_stem",
    "sample_subtype",
    "split_decision_status",
    "peak_mode_route",
    "expected_diff_status",
    "source_evidence_status",
    "raw_trace_status",
    "own_max_metric_status",
    "own_max_metric_value",
    "shift_aware_gate_status",
    "shift_aware_gate_call",
    "selective_source_family",
    "selective_source_family_shift_status",
    "selective_source_family_shape_r",
    "selective_source_family_shift_sec",
    "selective_evidence_status",
    "selective_primary_blocker",
    "selective_secondary_blockers",
    "ms1_product_authority_status",
    "ms1_product_authority_source",
    "selective_ms1_product_authority_status",
    "selective_ms1_product_authority_source",
    "full_chain_status",
    "primary_blocker",
    "secondary_blockers",
    "projected_selective_full_chain_status",
    "projected_selective_primary_blocker",
    "projected_selective_secondary_blockers",
    "product_authority_effect",
    "next_gate",
)


def validate_backfill_expansion_clean_target_full_chain_replay(
    *,
    summary_json: Path = DEFAULT_SUMMARY_JSON,
    checks_tsv: Path = DEFAULT_CHECKS_TSV,
    row_manifest_tsv: Path = DEFAULT_ROW_MANIFEST_TSV,
    cells_tsv: Path = DEFAULT_CELLS_TSV,
) -> list[str]:
    problems: list[str] = []
    try:
        payload = json.loads(summary_json.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return [f"could not read summary JSON: {exc}"]
    if not isinstance(payload, Mapping):
        return ["summary JSON must contain an object"]
    _check_summary(payload, problems)
    _check_checks_tsv(checks_tsv, payload, problems)
    _check_manifest_tsv(row_manifest_tsv, payload, problems)
    if cells_tsv.exists():
        _check_cells_tsv(cells_tsv, payload, problems)
    elif not _is_declared_externalized_cells_artifact(payload, cells_tsv):
        problems.append(f"cells TSV missing: {cells_tsv}")
    check_summary_artifact_hashes(payload, root=ROOT, problems=problems)
    return problems


def _check_summary(payload: Mapping[str, Any], problems: list[str]) -> None:
    if payload.get("schema_version") != SCHEMA_VERSION:
        problems.append("summary schema_version mismatch")
    if payload.get("packet_scope") != PACKET_SCOPE:
        problems.append("summary packet_scope mismatch")
    for field, expected in EXPECTED_COUNTS.items():
        if payload.get(field) != expected:
            problems.append(f"summary {field} mismatch")
    for field in (
        "write_authority",
        "product_writer_changed",
        "default_quant_matrix_changed",
        "workbook_or_gui_changed",
        "selected_peak_area_or_counting_changed",
        "raw_or_85raw_ran_by_checker",
    ):
        if payload.get(field) is not False:
            problems.append(f"summary {field} must be false")


def _check_checks_tsv(
    checks_tsv: Path,
    payload: Mapping[str, Any],
    problems: list[str],
) -> None:
    try:
        rows = read_tsv_required(checks_tsv, CHECK_COLUMNS)
    except (OSError, ValueError) as exc:
        problems.append(f"could not read checks TSV: {exc}")
        return
    failing = [row["check_id"] for row in rows if row.get("status") != "pass"]
    if failing:
        problems.append("checks must all pass: " + ";".join(failing))
    summary_checks = payload.get("checks")
    if isinstance(summary_checks, Mapping):
        for row in rows:
            check_id = text_value(row.get("check_id"))
            if summary_checks.get(check_id) != "pass":
                problems.append(f"summary check {check_id} must pass")


def _check_manifest_tsv(
    row_manifest_tsv: Path,
    payload: Mapping[str, Any],
    problems: list[str],
) -> None:
    try:
        rows = read_tsv_required(row_manifest_tsv, ROW_MANIFEST_COLUMNS)
    except (OSError, ValueError) as exc:
        problems.append(f"could not read row manifest TSV: {exc}")
        return
    if len(rows) != payload.get("row_manifest_group_count"):
        problems.append("row manifest group count mismatch")
    checks = (
        ("full_chain_pass_cell_count", "full_chain_pass_cell_count"),
        ("held_cell_count", "held_cell_count"),
        (
            "projected_selective_full_chain_pass_cell_count",
            "projected_selective_full_chain_pass_cell_count",
        ),
        (
            "projected_selective_held_cell_count",
            "projected_selective_held_cell_count",
        ),
    )
    for column, summary_field in checks:
        if _sum_int(rows, column) != payload.get(summary_field):
            problems.append(f"row manifest {summary_field} mismatch")
    if {row.get("product_authority_effect") for row in rows} != {
        "diagnostic_only_no_write_authority"
    }:
        problems.append("row manifest product_authority_effect mismatch")


def _check_cells_tsv(
    cells_tsv: Path,
    payload: Mapping[str, Any],
    problems: list[str],
) -> None:
    try:
        rows = read_tsv_required(cells_tsv, CELL_COLUMNS)
    except (OSError, ValueError) as exc:
        problems.append(f"could not read cells TSV: {exc}")
        return
    if len(rows) != payload.get("clean_target_candidate_cell_count"):
        problems.append("cells TSV clean target count mismatch")
    for field, expected_field in (
        ("full_chain_status", "full_chain_pass_cell_count"),
        (
            "projected_selective_full_chain_status",
            "projected_selective_full_chain_pass_cell_count",
        ),
    ):
        if sum(1 for row in rows if row.get(field) == "pass") != payload.get(
            expected_field
        ):
            problems.append(f"cells TSV {expected_field} mismatch")
    if {row.get("product_authority_effect") for row in rows} != {
        "diagnostic_only_no_write_authority"
    }:
        problems.append("cells TSV product_authority_effect mismatch")


def _is_declared_externalized_cells_artifact(
    payload: Mapping[str, Any],
    cells_tsv: Path,
) -> bool:
    return is_declared_externalized_artifact_path(
        payload,
        "cells_tsv",
        cells_tsv,
        root=ROOT,
    )


def _sum_int(rows: Sequence[Mapping[str, str]], field: str) -> int:
    return sum(int(row.get(field) or 0) for row in rows)


def _parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--summary-json", type=Path, default=DEFAULT_SUMMARY_JSON)
    parser.add_argument("--checks-tsv", type=Path, default=DEFAULT_CHECKS_TSV)
    parser.add_argument(
        "--row-manifest-tsv",
        type=Path,
        default=DEFAULT_ROW_MANIFEST_TSV,
    )
    parser.add_argument("--cells-tsv", type=Path, default=DEFAULT_CELLS_TSV)
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = _parse_args(argv)
    problems = validate_backfill_expansion_clean_target_full_chain_replay(
        summary_json=args.summary_json,
        checks_tsv=args.checks_tsv,
        row_manifest_tsv=args.row_manifest_tsv,
        cells_tsv=args.cells_tsv,
    )
    if problems:
        for problem in problems:
            print(problem, file=sys.stderr)
        return 2
    print(f"Clean-target full-chain replay summary: {args.summary_json}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
