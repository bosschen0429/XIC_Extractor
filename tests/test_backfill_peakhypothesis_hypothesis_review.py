import csv
import json
from pathlib import Path

from xic_extractor.diagnostics import (
    backfill_peakhypothesis_acceptance_gate as slice_gate,
)
from xic_extractor.diagnostics import (
    backfill_peakhypothesis_hypothesis_review as review,
)


def test_builds_hypothesis_candidate_review_queue(tmp_path: Path) -> None:
    index = review.build_full_cohort_hypothesis_review_queue(
        full_cohort_acceptance_gate_rows=[
            _slice_row(
                source_family="FAM000572",
                sample="Breast_Cancer_Tissue_pooled_QC5",
                matched_family="FAM005540",
                anchor_mz="289.116",
                anchor_rt="13.0485",
                primary_area="48462.2",
                include_primary="FALSE",
                consolidation_state="primary_loser",
                consolidation_winner="FAM005516",
                blockers=(
                    "full_cohort_candidate_not_primary_matrix_row;"
                    "full_cohort_candidate_family_consolidation_review_required"
                ),
            ),
            _slice_row(
                source_family="FAM001850",
                sample="Breast_Cancer_Tissue_pooled_QC5",
                matched_family="FAM017068",
                anchor_mz="479.158",
                anchor_rt="8.01617",
                primary_area="4574490",
                include_primary="TRUE",
                consolidation_state="primary_winner",
                consolidation_winner="FAM017068",
                blockers="full_cohort_candidate_family_consolidation_review_required",
            ),
            {
                **_slice_row(
                    source_family="FAM_DIRECT",
                    sample="Sample_A",
                    matched_family="FAM_DIRECT",
                    include_primary="TRUE",
                    consolidation_state="not_consolidated",
                    consolidation_winner="",
                    blockers="",
                ),
                "full_cohort_acceptance_gate_status": "candidate_no_regression",
            },
        ],
        source_run_id="unit-review",
    )
    outputs = review.write_full_cohort_hypothesis_review_outputs(tmp_path, index)

    assert len(index.rows) == 2
    fam572 = index.rows[0]
    assert fam572["schema_version"] == review.SCHEMA_VERSION
    assert fam572["source_run_id"] == "unit-review"
    assert fam572["review_item_id"] == "HYPREV0001"
    assert fam572["source_feature_family_id"] == "FAM000572"
    assert fam572["full_cohort_matched_peak_hypothesis_id"] == "FAM005540"
    assert fam572["full_cohort_anchor_mz"] == "289.116"
    assert fam572["full_cohort_anchor_rt"] == "13.0485"
    assert fam572["full_cohort_primary_matrix_area"] == "48462.2"
    assert fam572["review_focus"] == (
        "non_primary_candidate_needs_consolidation_policy"
    )
    assert fam572["proposed_product_transfer_status"] == (
        "review_only_pending_same_peak_and_consolidation_policy"
    )
    assert fam572["reviewer_verdict"] == ""

    fam1850 = index.rows[1]
    assert fam1850["review_focus"] == (
        "primary_candidate_with_family_consolidation_context"
    )

    assert index.summary["schema_version"] == review.SCHEMA_VERSION
    assert index.summary["review_queue_status"] == "manual_review_required"
    assert index.summary["input_row_count"] == 3
    assert index.summary["candidate_queue_count"] == 2
    assert index.summary["direct_candidate_count"] == 1
    assert index.summary["non_primary_candidate_count"] == 1
    assert index.summary["primary_row_consolidation_context_count"] == 1
    assert index.summary["product_behavior_changed"] is False

    written = _read_tsv(outputs.review_queue_tsv)
    assert written[0]["review_item_id"] == "HYPREV0001"
    summary = json.loads(outputs.summary_json.read_text(encoding="utf-8"))
    assert summary["candidate_queue_count"] == 2


def test_full_cohort_hypothesis_review_cli_writes_review_queue(tmp_path: Path) -> None:
    slice_tsv = tmp_path / "slice.tsv"
    output_dir = tmp_path / "out"
    _write_tsv(
        slice_tsv,
        [
            _slice_row(
                source_family="FAM000572",
                sample="Breast_Cancer_Tissue_pooled_QC5",
                matched_family="FAM005540",
                blockers=(
                    "full_cohort_candidate_not_primary_matrix_row;"
                    "full_cohort_candidate_family_consolidation_review_required"
                ),
            ),
        ],
    )

    from tools.diagnostics import (
        backfill_peakhypothesis_hypothesis_review as cli,
    )

    assert (
        cli.main(
            [
                "--full-cohort-acceptance-gate-tsv",
                str(slice_tsv),
                "--output-dir",
                str(output_dir),
                "--source-run-id",
                "unit-cli",
            ],
        )
        == 1
    )
    rows = _read_tsv(
        output_dir / "backfill_peakhypothesis_hypothesis_review_queue.tsv",
    )
    assert rows[0]["source_run_id"] == "unit-cli"


def _slice_row(
    *,
    source_family: str,
    sample: str,
    matched_family: str,
    anchor_mz: str = "100",
    anchor_rt: str = "12.1",
    primary_area: str = "100",
    include_primary: str = "FALSE",
    consolidation_state: str = "primary_loser",
    consolidation_winner: str = "FAM_WIN",
    blockers: str = "full_cohort_candidate_family_consolidation_review_required",
) -> dict[str, str]:
    return {
        "schema_version": slice_gate.SCHEMA_VERSION,
        "source_run_id": "unit-slice",
        "peak_hypothesis_id": source_family,
        "feature_family_id": source_family,
        "seed_group_id": f"seed::{source_family}::mz={anchor_mz}::rt={anchor_rt}",
        "sample_stem": sample,
        "promotion_decision": "promote_matrix_write",
        "projected_matrix_value": primary_area,
        "full_cohort_match_strategy": "hypothesis_anchor_mz_rt_sample",
        "full_cohort_anchor_mz": anchor_mz,
        "full_cohort_anchor_rt": anchor_rt,
        "full_cohort_matched_feature_family_id": matched_family,
        "full_cohort_matched_peak_hypothesis_id": matched_family,
        "full_cohort_match_mz_delta_ppm": "0",
        "full_cohort_match_rt_delta_min": "0",
        "full_cohort_candidate_count": "1",
        "full_cohort_exact_cell_found": "TRUE",
        "full_cohort_cell_status": "rescued",
        "full_cohort_primary_matrix_area": primary_area,
        "full_cohort_primary_matrix_area_source": "gaussian15_positive_asls_residual",
        "full_cohort_primary_matrix_area_reason": "unit",
        "full_cohort_peak_start_rt": anchor_rt,
        "full_cohort_peak_end_rt": "12.2",
        "full_cohort_trace_quality": "owner_backfill",
        "full_cohort_review_identity_decision": "audit_family",
        "full_cohort_include_in_primary_matrix": include_primary,
        "full_cohort_consolidation_state": consolidation_state,
        "full_cohort_consolidation_winner_group_hypothesis_id": consolidation_winner,
        "full_cohort_row_flags": "family_consolidation",
        "full_cohort_acceptance_gate_status": "hypothesis_candidate_review",
        "full_cohort_slice_blockers": blockers,
        "recommended_action": "review_hypothesis_anchor_candidate_before_activation",
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
