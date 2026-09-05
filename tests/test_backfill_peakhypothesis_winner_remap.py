import csv
import json
from pathlib import Path

from xic_extractor.diagnostics import (
    backfill_peakhypothesis_acceptance_gate as slice_gate,
)
from xic_extractor.diagnostics import (
    backfill_peakhypothesis_winner_remap as winner_remap,
)


def test_winner_remap_marks_primary_winner_cell_as_review_candidate(
    tmp_path: Path,
) -> None:
    index = winner_remap.build_full_cohort_winner_remap(
        full_cohort_acceptance_gate_rows=[
            _slice_gate_row(
                "FAM_SRC",
                "Sample_A",
                winner="FAM_WIN",
                blockers=(
                    "full_cohort_candidate_not_primary_matrix_row;"
                    "full_cohort_candidate_family_consolidation_review_required;"
                    "full_cohort_cell_status_duplicate_assigned"
                ),
            ),
        ],
        full_cohort_review_rows=[
            _review_row("FAM_WIN", include_primary="TRUE"),
        ],
        full_cohort_cell_rows=[
            _cell_row(
                "FAM_WIN",
                "Sample_A",
                status="detected",
                primary_area="456.7",
            ),
        ],
        source_run_id="unit-pass",
    )
    outputs = winner_remap.write_full_cohort_winner_remap_outputs(tmp_path, index)

    row = index.rows[0]
    assert row["remap_status"] == "remap_candidate_review"
    assert row["remap_blockers"] == ""
    assert row["winner_peak_hypothesis_id"] == "FAM_WIN"
    assert row["winner_include_in_primary_matrix"] == "TRUE"
    assert row["winner_cell_status"] == "detected"
    assert row["winner_primary_matrix_area"] == "456.7"
    assert row["matrix_value_source"] == "full_cohort_winner_primary_matrix_area"
    assert row["recommended_action"] == (
        "review_remapped_winner_peak_shape_before_activation"
    )
    assert index.summary["remap_gate_status"] == "pass"
    assert index.summary["remap_candidate_review_count"] == 1

    written = _read_tsv(outputs.rows_tsv)
    assert written[0]["source_run_id"] == "unit-pass"
    summary = json.loads(outputs.summary_json.read_text(encoding="utf-8"))
    assert summary["remap_gate_status"] == "pass"


def test_winner_remap_blocks_missing_winner_id() -> None:
    index = winner_remap.build_full_cohort_winner_remap(
        full_cohort_acceptance_gate_rows=[
            _slice_gate_row(
                "FAM_SRC",
                "Sample_A",
                winner="",
                blockers="full_cohort_cell_status_absent",
            ),
        ],
        full_cohort_review_rows=[],
        full_cohort_cell_rows=[],
    )

    row = index.rows[0]
    assert row["remap_status"] == "blocked"
    assert row["remap_blockers"] == "missing_winner_peak_hypothesis_id"
    assert row["recommended_action"] == "manual_full_cohort_review_required"
    assert index.summary["remap_gate_status"] == "fail"


def test_winner_remap_blocks_non_primary_or_missing_area_winner() -> None:
    index = winner_remap.build_full_cohort_winner_remap(
        full_cohort_acceptance_gate_rows=[
            _slice_gate_row("FAM_SRC", "Sample_B", winner="FAM_WIN"),
        ],
        full_cohort_review_rows=[
            _review_row(
                "FAM_WIN",
                include_primary="FALSE",
                consolidation_state="primary_loser",
            ),
        ],
        full_cohort_cell_rows=[
            _cell_row(
                "FAM_WIN",
                "Sample_B",
                status="duplicate_assigned",
                primary_area="",
                area_source="",
            ),
        ],
    )

    row = index.rows[0]
    assert row["remap_status"] == "blocked"
    assert row["remap_blockers"].split(";") == [
        "winner_not_primary_matrix_row",
        "winner_is_primary_loser",
        "winner_cell_status_duplicate_assigned",
        "winner_primary_matrix_area_missing",
        "winner_primary_matrix_area_source_not_gaussian15",
    ]
    assert index.summary["remap_gate_status"] == "fail"


def test_winner_remap_cli_returns_nonzero_when_any_row_blocked(
    tmp_path: Path,
) -> None:
    slice_tsv = tmp_path / "slice.tsv"
    review_tsv = tmp_path / "review.tsv"
    cells_tsv = tmp_path / "cells.tsv"
    _write_tsv(slice_tsv, [_slice_gate_row("FAM_SRC", "Sample_C", winner="")])
    _write_tsv(review_tsv, [_review_row("FAM_WIN", include_primary="TRUE")])
    _write_tsv(cells_tsv, [_cell_row("FAM_WIN", "Sample_C", status="detected")])

    from tools.diagnostics import backfill_peakhypothesis_winner_remap as cli

    output_dir = tmp_path / "out"
    assert cli.main(
        [
            "--full-cohort-acceptance-gate-tsv",
            str(slice_tsv),
            "--full_cohort-alignment-review-tsv",
            str(review_tsv),
            "--full_cohort-alignment-cells-tsv",
            str(cells_tsv),
            "--output-dir",
            str(output_dir),
            "--source-run-id",
            "unit-cli",
        ],
    ) == 1
    row = _read_tsv(output_dir / "backfill_peakhypothesis_winner_remap.tsv")[0]
    assert row["remap_status"] == "blocked"


def _slice_gate_row(
    family_id: str,
    sample: str,
    *,
    winner: str,
    blockers: str = "full_cohort_candidate_family_consolidation_review_required",
) -> dict[str, str]:
    return {
        "schema_version": slice_gate.SCHEMA_VERSION,
        "source_run_id": "unit-slice",
        "peak_hypothesis_id": family_id,
        "feature_family_id": family_id,
        "seed_group_id": f"seed::{family_id}",
        "sample_stem": sample,
        "promotion_decision": "promote_matrix_write",
        "projected_matrix_value": "123",
        "full_cohort_cell_status": "duplicate_assigned",
        "full_cohort_consolidation_winner_group_hypothesis_id": winner,
        "full_cohort_acceptance_gate_status": "blocked",
        "full_cohort_slice_blockers": blockers,
    }


def _review_row(
    family_id: str,
    *,
    include_primary: str,
    identity_decision: str = "production_family",
    consolidation_state: str = "primary_winner",
) -> dict[str, str]:
    return {
        "feature_family_id": family_id,
        "group_hypothesis_id": family_id,
        "identity_decision": identity_decision,
        "include_in_primary_matrix": include_primary,
        "consolidation_state": consolidation_state,
        "row_flags": "",
    }


def _cell_row(
    family_id: str,
    sample: str,
    *,
    status: str,
    primary_area: str = "100",
    area_source: str = "gaussian15_positive_asls_residual",
) -> dict[str, str]:
    return {
        "feature_family_id": family_id,
        "group_hypothesis_id": family_id,
        "sample_stem": sample,
        "status": status,
        "primary_matrix_area": primary_area,
        "primary_matrix_area_source": area_source,
        "primary_matrix_area_reason": "unit",
        "peak_start_rt": "12.0",
        "peak_end_rt": "12.2",
        "trace_quality": "owner_exact_apex_match",
        "consolidation_state": "primary_winner",
    }


def _write_tsv(path: Path, rows: list[dict[str, str]]) -> None:
    fieldnames = sorted({key for row in rows for key in row})
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            delimiter="\t",
            fieldnames=fieldnames,
            lineterminator="\n",
        )
        writer.writeheader()
        writer.writerows(rows)


def _read_tsv(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle, delimiter="\t"))
