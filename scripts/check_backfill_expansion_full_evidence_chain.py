"""Validate the historical 666-cell Backfill full evidence-chain packet.

The retained packet is a source-sealed diagnostic/falsification artifact. This
checker validates its internal counts, hashes, and no-write boundary only. It
does not regenerate classifications, rehydrate product authority, read RAW
files, mutate the default matrix, or grant ProductWriter authority.
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
from xic_extractor.tabular_io import read_tsv_required  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
SCHEMA_VERSION = "backfill_expansion_full_evidence_chain_v1"
PACKET_SCOPE = "backfill_expansion_candidate_replay_666_cells"

DEFAULT_DOCS_DIR = (
    ROOT / "docs/superpowers/validation/backfill_expansion_full_evidence_chain_v1"
)
DEFAULT_OUTPUT_DIR = (
    ROOT / "output/validation/backfill_expansion_full_evidence_chain_v1"
)
DEFAULT_CELLS_TSV = (
    DEFAULT_OUTPUT_DIR / "backfill_expansion_full_evidence_chain_cells.tsv"
)
DEFAULT_SUMMARY_JSON = (
    DEFAULT_DOCS_DIR / "backfill_expansion_full_evidence_chain_summary.json"
)
DEFAULT_CHECKS_TSV = (
    DEFAULT_DOCS_DIR / "backfill_expansion_full_evidence_chain_checks.tsv"
)
DEFAULT_ROW_MANIFEST_TSV = (
    DEFAULT_DOCS_DIR / "backfill_expansion_full_evidence_chain_row_manifest.tsv"
)

EXPECTED_COUNTS = {
    "candidate_cell_count": 666,
    "candidate_peak_count": 20,
    "expected_diff_present_cell_count": 666,
    "source_evidence_present_cell_count": 666,
    "raw_trace_observed_cell_count": 666,
    "shift_aware_gate_family_count": 20,
    "shift_aware_gate_supported_family_count": 14,
    "shift_aware_gate_supported_cell_count": 492,
    "own_max_metric_supported_cell_count": 496,
    "ms1_product_authorized_cell_count": 374,
    "full_chain_pass_cell_count": 374,
    "held_cell_count": 292,
}

EXPECTED_CHECK_IDS = (
    "upstream_candidate_packet_pass",
    "upstream_raw_trace_identity_pass",
    "candidate_scope_count",
    "candidate_keyset_unique",
    "expected_diff_all_present",
    "source_evidence_all_present",
    "raw_trace_observed_all_present",
    "shift_aware_batch_success",
    "shift_aware_gate_family_count",
    "shift_aware_gate_supported_family_count",
    "shift_aware_gate_supported_cell_count",
    "own_max_metric_supported_cell_count",
    "ms1_product_authorized_cell_count",
    "full_chain_pass_cell_count",
    "held_cell_count",
    "full_chain_complete_is_false",
    "no_product_writer_authority",
    "no_default_matrix_or_workbook_change",
)

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
    "row_scope",
    "peak_hypothesis_id",
    "candidate_cell_count",
    "expected_diff_present_cell_count",
    "source_evidence_present_cell_count",
    "raw_trace_observed_cell_count",
    "shift_aware_gate_supported_cell_count",
    "own_max_metric_supported_cell_count",
    "ms1_product_authorized_cell_count",
    "full_chain_pass_cell_count",
    "held_cell_count",
    "primary_blocker",
    "product_authority_effect",
    "next_gate",
)
CELL_CHAIN_COLUMNS = (
    "schema_version",
    "peak_hypothesis_id",
    "sample_stem",
    "expected_diff_status",
    "source_evidence_status",
    "raw_trace_status",
    "own_max_metric_status",
    "own_max_metric_value",
    "shift_aware_gate_status",
    "shift_aware_gate_call",
    "ms1_product_authority_status",
    "ms1_product_authority_source",
    "full_chain_status",
    "primary_blocker",
    "secondary_blockers",
    "product_authority_effect",
    "next_gate",
)


def validate_backfill_expansion_full_evidence_chain(
    *,
    summary_json: Path = DEFAULT_SUMMARY_JSON,
    checks_tsv: Path = DEFAULT_CHECKS_TSV,
    row_manifest_tsv: Path = DEFAULT_ROW_MANIFEST_TSV,
    cells_tsv: Path = DEFAULT_CELLS_TSV,
    require_full_chain: bool = False,
) -> list[str]:
    """Validate the retained packet without reconstructing scientific claims."""
    problems: list[str] = []
    try:
        payload = json.loads(summary_json.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return [f"could not read summary JSON: {exc}"]
    if not isinstance(payload, Mapping):
        return ["summary JSON must contain an object"]

    _check_summary_counts(payload, problems)
    _check_checks_tsv(checks_tsv, payload, problems)
    _check_manifest_tsv(row_manifest_tsv, payload, problems)
    if cells_tsv.exists():
        _check_cells_tsv(cells_tsv, payload, problems)
    elif not is_declared_externalized_artifact_path(
        payload,
        "cells_tsv",
        cells_tsv,
        root=ROOT,
    ):
        problems.append(f"cells TSV missing: {cells_tsv}")
    _check_artifact_hashes(payload, problems)
    if require_full_chain and payload.get("full_chain_complete") is not True:
        problems.append(
            "full evidence chain incomplete: "
            f"{payload.get('full_chain_pass_cell_count')}/"
            f"{payload.get('candidate_cell_count')} cells pass; "
            f"held={payload.get('held_cell_count')}",
        )
    return problems


def _check_summary_counts(payload: Mapping[str, Any], problems: list[str]) -> None:
    if payload.get("schema_version") != SCHEMA_VERSION:
        problems.append("summary schema_version mismatch")
    if payload.get("packet_scope") != PACKET_SCOPE:
        problems.append("summary packet_scope mismatch")
    for field, expected in EXPECTED_COUNTS.items():
        if payload.get(field) != expected:
            problems.append(f"summary {field} mismatch")
    if payload.get("full_chain_complete") is not False:
        problems.append("summary full_chain_complete must be false for this packet")
    if payload.get("write_authority") is not False:
        problems.append("summary write_authority must be false")
    if payload.get("product_writer_changed") is not False:
        problems.append("summary product_writer_changed must be false")
    if payload.get("default_quant_matrix_changed") is not False:
        problems.append("summary default_quant_matrix_changed must be false")


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
    observed_ids = {row.get("check_id", "") for row in rows}
    missing = [
        check_id for check_id in EXPECTED_CHECK_IDS if check_id not in observed_ids
    ]
    if missing:
        problems.append("checks missing required ids: " + ";".join(missing))
    failing = [row["check_id"] for row in rows if row.get("status") != "pass"]
    if failing:
        problems.append("checks must all pass: " + ";".join(failing))
    summary_checks = payload.get("checks")
    if isinstance(summary_checks, Mapping):
        for check_id in EXPECTED_CHECK_IDS:
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
    if len(rows) != payload.get("candidate_peak_count"):
        problems.append("row manifest candidate peak count mismatch")
    full_chain_sum = sum(
        int(row.get("full_chain_pass_cell_count") or 0) for row in rows
    )
    if full_chain_sum != payload.get("full_chain_pass_cell_count"):
        problems.append("row manifest full-chain pass cell count mismatch")
    held_sum = sum(int(row.get("held_cell_count") or 0) for row in rows)
    if held_sum != payload.get("held_cell_count"):
        problems.append("row manifest held cell count mismatch")
    bad_effect = [
        row
        for row in rows
        if row.get("product_authority_effect") != "candidate_only_no_write_authority"
    ]
    if bad_effect:
        problems.append("row manifest product_authority_effect mismatch")


def _check_cells_tsv(
    cells_tsv: Path,
    payload: Mapping[str, Any],
    problems: list[str],
) -> None:
    try:
        rows = read_tsv_required(cells_tsv, CELL_CHAIN_COLUMNS)
    except (OSError, ValueError) as exc:
        problems.append(f"could not read cells TSV: {exc}")
        return
    if len(rows) != payload.get("candidate_cell_count"):
        problems.append("cells TSV candidate count mismatch")
    full_chain_count = sum(1 for row in rows if row.get("full_chain_status") == "pass")
    if full_chain_count != payload.get("full_chain_pass_cell_count"):
        problems.append("cells TSV full-chain pass count mismatch")
    writer_effects = {row.get("product_authority_effect") for row in rows}
    if writer_effects != {"candidate_only_no_write_authority"}:
        problems.append("cells TSV product_authority_effect mismatch")


def _check_artifact_hashes(payload: Mapping[str, Any], problems: list[str]) -> None:
    check_summary_artifact_hashes(payload, root=ROOT, problems=problems)


def _parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--require-full-chain", action="store_true")
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
    problems = validate_backfill_expansion_full_evidence_chain(
        summary_json=args.summary_json,
        checks_tsv=args.checks_tsv,
        row_manifest_tsv=args.row_manifest_tsv,
        cells_tsv=args.cells_tsv,
        require_full_chain=args.require_full_chain,
    )
    if problems:
        for problem in problems:
            print(problem, file=sys.stderr)
        return 1 if args.require_full_chain else 2
    print(f"Historical Backfill full-chain packet: {args.summary_json}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
