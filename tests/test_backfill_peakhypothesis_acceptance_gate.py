import csv
import json
from pathlib import Path

from xic_extractor.diagnostics import (
    backfill_peakhypothesis_acceptance_gate as full_cohort_acceptance_gate,
)
from xic_extractor.diagnostics import (
    backfill_peakhypothesis_promotion as promotion,
)


def test_full_cohort_acceptance_gate_uses_hypothesis_anchor_not_cross_run_fam_id() -> (
    None
):
    index = full_cohort_acceptance_gate.build_full_cohort_acceptance_gate(
        promotion_rows=[
            _promotion_row(
                "FAM000572",
                "Breast_Cancer_Tissue_pooled_QC5",
                projected_value="48462.2",
                seed_group_id=(
                    "seed::FAM000572::mz=289.116::rt=13.0485::"
                    "window=10.0485-16.0485::ppm=20"
                ),
            ),
        ],
        full_cohort_review_rows=[
            _review_row(
                "FAM000572",
                include_primary="FALSE",
                identity_decision="provisional_discovery",
                family_center_mz="250.069",
                family_center_rt="9.12586",
            ),
            _review_row(
                "FAM005540",
                include_primary="FALSE",
                identity_decision="audit_family",
                consolidation_state="primary_loser",
                consolidation_winner="FAM005516",
                family_center_mz="289.116",
                family_center_rt="13.0485",
                row_flags="family_consolidation_loser;duplicate_claim_pressure",
            ),
        ],
        full_cohort_cell_rows=[
            _cell_row(
                "FAM000572",
                "Breast_Cancer_Tissue_pooled_QC5",
                status="absent",
                primary_area="",
                area_source="",
                trace_quality="absent",
            ),
            _cell_row(
                "FAM005540",
                "Breast_Cancer_Tissue_pooled_QC5",
                status="backfilled",
                primary_area="48462.2",
                apex_rt="13.0739",
                peak_start_rt="13.0739",
                peak_end_rt="13.1155",
            ),
        ],
    )

    row = index.rows[0]
    assert row["full_cohort_match_strategy"] == "hypothesis_anchor_mz_rt_sample"
    assert row["full_cohort_matched_feature_family_id"] == "FAM005540"
    assert row["full_cohort_matched_peak_hypothesis_id"] == "FAM005540"
    assert row["full_cohort_cell_status"] == "backfilled"
    assert row["full_cohort_primary_matrix_area"] == "48462.2"
    assert row["full_cohort_acceptance_gate_status"] == "hypothesis_candidate_review"
    assert row["full_cohort_slice_blockers"].split(";") == [
        "full_cohort_candidate_not_primary_matrix_row",
        "full_cohort_candidate_family_consolidation_review_required",
    ]
    assert row["recommended_action"] == (
        "review_hypothesis_anchor_candidate_before_activation"
    )
    assert index.summary["gate_status"] == "partial"
    assert index.summary["hypothesis_candidate_review_count"] == 1
    assert index.summary["blocked_count"] == 0


def test_full_cohort_acceptance_gate_passes_direct_primary_backfill(
    tmp_path: Path,
) -> None:
    index = full_cohort_acceptance_gate.build_full_cohort_acceptance_gate(
        promotion_rows=[
            _promotion_row("FAM001", "Sample_A", projected_value="1234.5"),
        ],
        full_cohort_review_rows=[
            _review_row("FAM001", include_primary="TRUE"),
        ],
        full_cohort_cell_rows=[
            _cell_row(
                "FAM001",
                "Sample_A",
                status="backfilled",
                primary_area="1234.5",
                area_source="gaussian15_positive_asls_residual",
            ),
        ],
        source_run_id="unit-pass",
    )
    outputs = full_cohort_acceptance_gate.write_full_cohort_acceptance_gate_outputs(
        tmp_path, index
    )

    assert index.summary["gate_status"] == "pass"
    assert index.summary["candidate_no_regression_count"] == 1
    row = index.rows[0]
    assert row["full_cohort_acceptance_gate_status"] == "candidate_no_regression"
    assert row["full_cohort_slice_blockers"] == ""
    assert row["full_cohort_match_strategy"] == "feature_family_exact_fallback"
    assert (
        row["recommended_action"] == "eligible_for_direct_full_cohort_activation_trial"
    )

    written_rows = _read_tsv(outputs.cells_tsv)
    assert written_rows[0]["source_run_id"] == "unit-pass"
    summary = json.loads(outputs.summary_json.read_text(encoding="utf-8"))
    assert summary["gate_status"] == "pass"


def test_full_cohort_acceptance_gate_blocks_primary_loser_duplicate_claim() -> None:
    index = full_cohort_acceptance_gate.build_full_cohort_acceptance_gate(
        promotion_rows=[_promotion_row("FAM002", "Sample_B")],
        full_cohort_review_rows=[
            _review_row(
                "FAM002",
                include_primary="FALSE",
                identity_decision="audit_family",
                consolidation_state="primary_loser",
                consolidation_winner="FAM_WIN",
                row_flags="family_consolidation_loser;duplicate_claim_pressure",
            ),
        ],
        full_cohort_cell_rows=[
            _cell_row(
                "FAM002",
                "Sample_B",
                status="duplicate_assigned",
                primary_area="555",
                area_source="gaussian15_positive_asls_residual",
                consolidation_state="primary_loser",
                consolidation_winner="FAM_WIN",
                trace_quality="owner_backfill",
            ),
        ],
    )

    row = index.rows[0]
    assert index.summary["gate_status"] == "fail"
    assert row["full_cohort_acceptance_gate_status"] == "blocked"
    assert row["full_cohort_consolidation_winner_group_hypothesis_id"] == "FAM_WIN"
    assert row["full_cohort_slice_blockers"].split(";") == [
        "full_cohort_candidate_not_primary_matrix_row",
        "full_cohort_candidate_family_consolidation_review_required",
        "full_cohort_cell_status_duplicate_assigned",
    ]
    assert row["recommended_action"] == (
        "review_hypothesis_anchor_candidate_before_activation"
    )


def test_full_cohort_acceptance_gate_blocks_missing_exact_cell() -> None:
    index = full_cohort_acceptance_gate.build_full_cohort_acceptance_gate(
        promotion_rows=[_promotion_row("FAM003", "Sample_C")],
        full_cohort_review_rows=[_review_row("FAM003", include_primary="TRUE")],
        full_cohort_cell_rows=[],
    )

    row = index.rows[0]
    assert index.summary["gate_status"] == "fail"
    assert row["full_cohort_exact_cell_found"] == "FALSE"
    assert (
        row["full_cohort_slice_blockers"]
        == "full_cohort_hypothesis_candidate_cell_missing"
    )
    assert row["recommended_action"] == "manual_full_cohort_review_required"


def test_full_cohort_acceptance_gate_cli_returns_nonzero_on_gate_failure(
    tmp_path: Path,
) -> None:
    promotion_tsv = tmp_path / "promotion.tsv"
    review_tsv = tmp_path / "review.tsv"
    cells_tsv = tmp_path / "cells.tsv"
    _write_tsv(promotion_tsv, [_promotion_row("FAM004", "Sample_D")])
    _write_tsv(review_tsv, [_review_row("FAM004", include_primary="FALSE")])
    _write_tsv(
        cells_tsv,
        [_cell_row("FAM004", "Sample_D", status="duplicate_assigned")],
    )

    from tools.diagnostics import backfill_peakhypothesis_acceptance_gate as cli

    output_dir = tmp_path / "out"
    assert (
        cli.main(
            [
                "--promotion-cells-tsv",
                str(promotion_tsv),
                "--full_cohort-alignment-review-tsv",
                str(review_tsv),
                "--full_cohort-alignment-cells-tsv",
                str(cells_tsv),
                "--output-dir",
                str(output_dir),
                "--source-run-id",
                "unit-cli",
            ],
        )
        == 1
    )
    row = _read_tsv(output_dir / "backfill_peakhypothesis_acceptance_gate.tsv")[0]
    assert row["full_cohort_acceptance_gate_status"] == "blocked"


def _promotion_row(
    family_id: str,
    sample: str,
    *,
    projected_value: str = "100",
    seed_group_id: str | None = None,
) -> dict[str, str]:
    return {
        "schema_version": promotion.SCHEMA_VERSION,
        "peak_hypothesis_id": family_id,
        "activation_unit_scope": "peak_hypothesis",
        "feature_family_id": family_id,
        "seed_group_id": seed_group_id or f"seed::{family_id}",
        "sample_stem": sample,
        "promotion_decision": "promote_matrix_write",
        "projected_matrix_value": projected_value,
    }


def _review_row(
    family_id: str,
    *,
    include_primary: str,
    identity_decision: str = "accept",
    consolidation_state: str = "not_consolidated",
    consolidation_winner: str = "",
    row_flags: str = "",
    family_center_mz: str = "100",
    family_center_rt: str = "12.1",
) -> dict[str, str]:
    return {
        "feature_family_id": family_id,
        "group_hypothesis_id": family_id,
        "identity_decision": identity_decision,
        "include_in_primary_matrix": include_primary,
        "consolidation_state": consolidation_state,
        "consolidation_winner_group_hypothesis_id": consolidation_winner,
        "row_flags": row_flags,
        "family_center_mz": family_center_mz,
        "family_center_rt": family_center_rt,
    }


def _cell_row(
    family_id: str,
    sample: str,
    *,
    status: str,
    primary_area: str = "100",
    area_source: str = "gaussian15_positive_asls_residual",
    area_reason: str = "unit",
    consolidation_state: str = "not_consolidated",
    consolidation_winner: str = "",
    trace_quality: str = "owner_backfill",
    apex_rt: str = "12.1",
    peak_start_rt: str = "12.0",
    peak_end_rt: str = "12.2",
) -> dict[str, str]:
    return {
        "feature_family_id": family_id,
        "group_hypothesis_id": family_id,
        "sample_stem": sample,
        "status": status,
        "primary_matrix_area": primary_area,
        "primary_matrix_area_source": area_source,
        "primary_matrix_area_reason": area_reason,
        "apex_rt": apex_rt,
        "peak_start_rt": peak_start_rt,
        "peak_end_rt": peak_end_rt,
        "trace_quality": trace_quality,
        "consolidation_state": consolidation_state,
        "consolidation_winner_group_hypothesis_id": consolidation_winner,
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
