import csv
import json
from pathlib import Path

from xic_extractor.diagnostics import (
    backfill_peakhypothesis_normal_peak_decision as decision,
)

EXPECTED_NORMAL_PEAK_SHAPE_DEFINITION = (
    "gaussian15_asls_residual_selected_shape_context_single_complete_unimodal_peak;"
    "raw_spikes_neighbor_contact_family_multiplet_not_blockers"
)


def test_normal_same_peak_non_primary_candidate_requires_backfill(
    tmp_path: Path,
) -> None:
    index = decision.build_normal_peak_decision_index(
        promotion_rows=[
            _promotion_row(
                peak_hypothesis_id="FAM000808",
                sample="TumorBC2263_DNA",
            ),
        ],
        full_cohort_acceptance_gate_rows=[
            _full_cohort_slice_row(
                peak_hypothesis_id="FAM000808",
                sample="TumorBC2263_DNA",
                matched_peak_hypothesis_id="FAM007718",
                include_primary="FALSE",
                consolidation_state="primary_loser",
                blockers=(
                    "full_cohort_candidate_not_primary_matrix_row;"
                    "full_cohort_candidate_family_consolidation_review_required"
                ),
            ),
        ],
        manual_verdict_rows=[
            _manual_verdict_row(
                source_peak_hypothesis_id="FAM000808",
                sample="TumorBC2263_DNA",
                matched_peak_hypothesis_id="FAM007718",
            ),
        ],
        source_run_id="unit",
    )

    row = index.rows[0]
    assert row["normal_peak_decision"] == "require_backfill"
    assert row["normal_peak_backfill_required"] is True
    assert row["normal_peak_shape_definition"] == (
        EXPECTED_NORMAL_PEAK_SHAPE_DEFINITION
    )
    assert row["normal_peak_decision_reasons"] == (
        "standard_peak_same_peak_supported;"
        "positive_gaussian15_area;"
        "consolidation_not_blocking_normal_peak"
    )
    assert row["normal_peak_decision_blockers"] == ""
    assert row["normal_peak_quantitation_area"] == "298267"
    assert row["normal_peak_quantitation_area_source_field"] == (
        "full_cohort_primary_matrix_area"
    )
    assert row["consolidation_policy_effect"] == (
        "allow_same_peak_peakhypothesis_candidate_despite_non_primary"
    )
    assert index.summary["normal_peak_candidate_count"] == 1
    assert index.summary["required_backfill_count"] == 1
    assert index.summary["consolidation_override_count"] == 1
    assert index.summary["normal_peak_policy_status"] == (
        "normal_peak_backfill_required_all_reviewed_candidates"
    )
    assert index.summary["normal_peak_shape_definition"] == (
        EXPECTED_NORMAL_PEAK_SHAPE_DEFINITION
    )
    assert index.summary["next_action"] == (
        "wire_normal_peak_decisions_into_activation_or_matrix_writer"
    )

    outputs = decision.write_normal_peak_decision_outputs(tmp_path, index)
    rows = _read_tsv(outputs.decisions_tsv)
    summary = json.loads(outputs.summary_json.read_text(encoding="utf-8"))
    assert rows[0]["normal_peak_decision"] == "require_backfill"
    assert rows[0]["normal_peak_backfill_required"] == "TRUE"
    assert summary["required_backfill_count"] == 1


def test_standard_area_policy_uses_gaussian15_selected_segment() -> None:
    index = decision.build_normal_peak_decision_index(
        promotion_rows=[
            _promotion_row(
                peak_hypothesis_id="FAM_MIXED_CONTEXT",
                sample="Sample_A",
                area_uncertainty_reason=(
                    "gaussian15_selected_segment_single_peak;"
                    "raw_xic_spiky;family_window_contains_multiple_peaks;"
                    "neighbor_peak_contact"
                ),
            ),
        ],
        full_cohort_acceptance_gate_rows=[
            _full_cohort_slice_row(
                peak_hypothesis_id="FAM_MIXED_CONTEXT",
                sample="Sample_A",
                matched_peak_hypothesis_id="HYP_SELECTED_SEGMENT",
                blockers="",
            ),
        ],
        manual_verdict_rows=[
            _manual_verdict_row(
                source_peak_hypothesis_id="FAM_MIXED_CONTEXT",
                sample="Sample_A",
                matched_peak_hypothesis_id="HYP_SELECTED_SEGMENT",
            ),
        ],
    )

    row = index.rows[0]
    assert row["normal_peak_decision"] == "require_backfill"
    assert row["normal_peak_backfill_required"] is True
    assert row["normal_peak_decision_blockers"] == ""
    assert row["normal_peak_decision_reasons"] == (
        "standard_peak_same_peak_supported;"
        "positive_gaussian15_area"
    )


def test_machine_shape_evidence_can_classify_standard_peak_without_area_label() -> None:
    index = decision.build_normal_peak_decision_index(
        promotion_rows=[
            _promotion_row(
                peak_hypothesis_id="FAM_MACHINE_STANDARD",
                sample="Sample_A",
                area_policy="",
            ),
        ],
        full_cohort_acceptance_gate_rows=[
            _full_cohort_slice_row(
                peak_hypothesis_id="FAM_MACHINE_STANDARD",
                sample="Sample_A",
                matched_peak_hypothesis_id="HYP_MACHINE_STANDARD",
                blockers="",
            ),
        ],
        manual_verdict_rows=[
            _manual_verdict_row(
                source_peak_hypothesis_id="FAM_MACHINE_STANDARD",
                sample="Sample_A",
                matched_peak_hypothesis_id="HYP_MACHINE_STANDARD",
            ),
        ],
        machine_shape_rows=[
            _machine_shape_row(
                source_peak_hypothesis_id="FAM_MACHINE_STANDARD",
                sample="Sample_A",
                matched_peak_hypothesis_id="HYP_MACHINE_STANDARD",
                decision="standard_peak_shape_supported",
            ),
        ],
    )

    row = index.rows[0]
    assert row["area_policy"] == "standard_assessable_area"
    assert row["normal_peak_decision"] == "require_backfill"
    assert row["normal_peak_quantitation_area"] == "456.7"
    assert row["normal_peak_quantitation_area_source_field"] == (
        "gaussian15_lobe_area"
    )
    assert row["normal_peak_quantitation_boundary_source"] == "baseline_return"
    assert "machine_gaussian15_standard_peak_shape_supported" in (
        row["normal_peak_decision_reasons"]
    )
    assert "machine_gaussian15_lobe_area_selected" in (
        row["normal_peak_decision_reasons"]
    )
    assert row["same_peak_verdict"] == "same_peak_supported"
    assert row["same_peak_verdict_source"] == "manual_review"


def test_machine_same_peak_support_can_replace_manual_verdict() -> None:
    index = decision.build_normal_peak_decision_index(
        promotion_rows=[
            _promotion_row(
                peak_hypothesis_id="FAM_MACHINE_AUTO",
                sample="Sample_A",
                area_policy="",
            ),
        ],
        full_cohort_acceptance_gate_rows=[
            _full_cohort_slice_row(
                peak_hypothesis_id="FAM_MACHINE_AUTO",
                sample="Sample_A",
                matched_peak_hypothesis_id="HYP_MACHINE_AUTO",
                blockers="",
            ),
        ],
        manual_verdict_rows=[],
        machine_shape_rows=[
            _machine_shape_row(
                source_peak_hypothesis_id="FAM_MACHINE_AUTO",
                sample="Sample_A",
                matched_peak_hypothesis_id="HYP_MACHINE_AUTO",
                decision="standard_peak_shape_supported",
                same_peak_verdict="same_peak_supported",
            ),
        ],
    )

    row = index.rows[0]
    assert row["normal_peak_decision"] == "require_backfill"
    assert row["normal_peak_backfill_required"] is True
    assert row["manual_same_peak_verdict"] == ""
    assert row["same_peak_verdict"] == "same_peak_supported"
    assert row["same_peak_verdict_source"] == "machine_gaussian15_trace"
    assert "machine_same_peak_supported" in row["normal_peak_decision_reasons"]


def test_manual_same_peak_conflict_overrides_machine_support() -> None:
    index = decision.build_normal_peak_decision_index(
        promotion_rows=[
            _promotion_row(
                peak_hypothesis_id="FAM_MACHINE_MANUAL_CONFLICT",
                sample="Sample_A",
                area_policy="",
            ),
        ],
        full_cohort_acceptance_gate_rows=[
            _full_cohort_slice_row(
                peak_hypothesis_id="FAM_MACHINE_MANUAL_CONFLICT",
                sample="Sample_A",
                matched_peak_hypothesis_id="HYP_MACHINE_MANUAL_CONFLICT",
                blockers="",
            ),
        ],
        manual_verdict_rows=[
            _manual_verdict_row(
                source_peak_hypothesis_id="FAM_MACHINE_MANUAL_CONFLICT",
                sample="Sample_A",
                matched_peak_hypothesis_id="HYP_MACHINE_MANUAL_CONFLICT",
                reviewer_verdict="same_peak_conflict",
            ),
        ],
        machine_shape_rows=[
            _machine_shape_row(
                source_peak_hypothesis_id="FAM_MACHINE_MANUAL_CONFLICT",
                sample="Sample_A",
                matched_peak_hypothesis_id="HYP_MACHINE_MANUAL_CONFLICT",
                decision="standard_peak_shape_supported",
                same_peak_verdict="same_peak_supported",
            ),
        ],
    )

    row = index.rows[0]
    assert row["normal_peak_decision"] == "blocked"
    assert row["same_peak_verdict"] == "same_peak_conflict"
    assert row["same_peak_verdict_source"] == "manual_review"
    assert "same_peak_not_supported" in row["normal_peak_decision_blockers"]


def test_machine_shape_evidence_blocks_multipeak_even_if_area_label_standard() -> None:
    index = decision.build_normal_peak_decision_index(
        promotion_rows=[
            _promotion_row(
                peak_hypothesis_id="FAM_MACHINE_MULTI",
                sample="Sample_A",
                area_policy="standard_assessable_area",
            ),
        ],
        full_cohort_acceptance_gate_rows=[
            _full_cohort_slice_row(
                peak_hypothesis_id="FAM_MACHINE_MULTI",
                sample="Sample_A",
                matched_peak_hypothesis_id="HYP_MACHINE_MULTI",
                blockers="",
            ),
        ],
        manual_verdict_rows=[
            _manual_verdict_row(
                source_peak_hypothesis_id="FAM_MACHINE_MULTI",
                sample="Sample_A",
                matched_peak_hypothesis_id="HYP_MACHINE_MULTI",
            ),
        ],
        machine_shape_rows=[
            _machine_shape_row(
                source_peak_hypothesis_id="FAM_MACHINE_MULTI",
                sample="Sample_A",
                matched_peak_hypothesis_id="HYP_MACHINE_MULTI",
                decision="nonstandard_peak_shape",
                blockers="selected_segment_multiple_gaussian15_peaks",
                peak_count="2",
            ),
        ],
    )

    row = index.rows[0]
    assert row["area_policy"] == "nonstandard_assessable_area"
    assert row["normal_peak_decision"] == "review_only_nonstandard_peak"
    assert row["normal_peak_backfill_required"] is False


def test_machine_shape_standard_peak_blocks_without_lobe_area() -> None:
    index = decision.build_normal_peak_decision_index(
        promotion_rows=[
            _promotion_row(
                peak_hypothesis_id="FAM_MACHINE_NO_AREA",
                sample="Sample_A",
                area_policy="",
            ),
        ],
        full_cohort_acceptance_gate_rows=[
            _full_cohort_slice_row(
                peak_hypothesis_id="FAM_MACHINE_NO_AREA",
                sample="Sample_A",
                matched_peak_hypothesis_id="HYP_MACHINE_NO_AREA",
                blockers="",
            ),
        ],
        manual_verdict_rows=[
            _manual_verdict_row(
                source_peak_hypothesis_id="FAM_MACHINE_NO_AREA",
                sample="Sample_A",
                matched_peak_hypothesis_id="HYP_MACHINE_NO_AREA",
            ),
        ],
        machine_shape_rows=[
            _machine_shape_row(
                source_peak_hypothesis_id="FAM_MACHINE_NO_AREA",
                sample="Sample_A",
                matched_peak_hypothesis_id="HYP_MACHINE_NO_AREA",
                decision="standard_peak_shape_supported",
                lobe_area="",
            ),
        ],
    )

    row = index.rows[0]
    assert row["normal_peak_decision"] == "blocked"
    assert row["normal_peak_decision_blockers"] == (
        "machine_gaussian15_lobe_area_not_positive"
    )


def test_nonstandard_peak_stays_review_only_even_with_same_peak_support() -> None:
    index = decision.build_normal_peak_decision_index(
        promotion_rows=[
            _promotion_row(
                peak_hypothesis_id="FAM_NONSTANDARD",
                sample="Sample_A",
                area_policy="nonstandard_assessable_area",
                matrix_quantitative_use="use_with_uncertainty",
                promotion_decision="blocked",
                promotion_blockers="nonstandard_area_review_only",
            ),
        ],
        full_cohort_acceptance_gate_rows=[
            _full_cohort_slice_row(
                peak_hypothesis_id="FAM_NONSTANDARD",
                sample="Sample_A",
                matched_peak_hypothesis_id="FAM_FULL_COHORT",
            ),
        ],
        manual_verdict_rows=[
            _manual_verdict_row(
                source_peak_hypothesis_id="FAM_NONSTANDARD",
                sample="Sample_A",
                matched_peak_hypothesis_id="FAM_FULL_COHORT",
            ),
        ],
    )

    row = index.rows[0]
    assert row["normal_peak_decision"] == "review_only_nonstandard_peak"
    assert row["normal_peak_backfill_required"] is False
    assert row["normal_peak_decision_blockers"] == (
        "nonstandard_peak_out_of_goal_scope"
    )
    assert index.summary["review_only_nonstandard_count"] == 1
    assert index.summary["required_backfill_count"] == 0


def test_same_peak_conflict_blocks_normal_peak_backfill() -> None:
    index = decision.build_normal_peak_decision_index(
        promotion_rows=[_promotion_row()],
        full_cohort_acceptance_gate_rows=[_full_cohort_slice_row()],
        manual_verdict_rows=[
            _manual_verdict_row(reviewer_verdict="same_peak_conflict"),
        ],
    )

    row = index.rows[0]
    assert row["normal_peak_decision"] == "blocked"
    assert row["normal_peak_backfill_required"] is False
    assert row["normal_peak_decision_blockers"] == (
        "same_peak_not_supported"
    )
    assert index.summary["blocked_count"] == 1


def test_cli_writes_normal_peak_decision_outputs(tmp_path: Path) -> None:
    promotion_tsv = tmp_path / "promotion.tsv"
    full_cohort_tsv = tmp_path / "full_cohort.tsv"
    manual_tsv = tmp_path / "manual.tsv"
    output_dir = tmp_path / "out"
    _write_tsv(promotion_tsv, [_promotion_row()])
    _write_tsv(full_cohort_tsv, [_full_cohort_slice_row()])
    _write_tsv(manual_tsv, [_manual_verdict_row()])

    from tools.diagnostics import (
        backfill_peakhypothesis_normal_peak_decision as cli,
    )

    assert cli.main(
        [
            "--promotion-cells-tsv",
            str(promotion_tsv),
            "--full-cohort-acceptance-gate-tsv",
            str(full_cohort_tsv),
            "--full_cohort-manual-verdict-tsv",
            str(manual_tsv),
            "--output-dir",
            str(output_dir),
            "--source-run-id",
            "unit-cli",
        ],
    ) == 0

    rows = _read_tsv(
        output_dir / "backfill_peakhypothesis_normal_peak_decisions.tsv",
    )
    summary = json.loads(
        (
            output_dir
            / "backfill_peakhypothesis_normal_peak_decision_summary.json"
        ).read_text(encoding="utf-8"),
    )
    assert rows[0]["source_run_id"] == "unit-cli"
    assert rows[0]["normal_peak_decision"] == "require_backfill"
    assert summary["source_run_id"] == "unit-cli"
    assert summary["required_backfill_count"] == 1


def _promotion_row(
    peak_hypothesis_id: str = "FAM001",
    sample: str = "Sample_A",
    *,
    area_policy: str = "standard_assessable_area",
    area_uncertainty_reason: str = "",
    matrix_quantitative_use: str = "standard_quantitative_use",
    promotion_decision: str = "promote_matrix_write",
    promotion_blockers: str = "",
) -> dict[str, str]:
    return {
        "schema_version": "backfill_peakhypothesis_promotion_v1",
        "peak_hypothesis_id": peak_hypothesis_id,
        "activation_unit_scope": "peak_hypothesis",
        "feature_family_id": peak_hypothesis_id,
        "seed_group_id": f"seed::{peak_hypothesis_id}",
        "sample_stem": sample,
        "promotion_decision": promotion_decision,
        "promotion_reasons": "allowlisted_peakhypothesis_same_peak_backfill",
        "promotion_blockers": promotion_blockers,
        "current_raw_status": "rescued",
        "current_matrix_written": "FALSE",
        "projected_matrix_value": "123.4",
        "area_policy": area_policy,
        "area_uncertainty_reason": area_uncertainty_reason,
        "matrix_quantitative_use": matrix_quantitative_use,
        "shadow_projection_row_sha256": "row-sha",
    }


def _full_cohort_slice_row(
    peak_hypothesis_id: str = "FAM001",
    sample: str = "Sample_A",
    *,
    matched_peak_hypothesis_id: str = "FAM_FULL_COHORT",
    include_primary: str = "TRUE",
    consolidation_state: str = "primary_winner",
    blockers: str = "full_cohort_candidate_family_consolidation_review_required",
) -> dict[str, str]:
    return {
        "schema_version": "backfill_peakhypothesis_acceptance_gate_v1",
        "peak_hypothesis_id": peak_hypothesis_id,
        "feature_family_id": peak_hypothesis_id,
        "seed_group_id": f"seed::{peak_hypothesis_id}",
        "sample_stem": sample,
        "full_cohort_anchor_mz": "301.165",
        "full_cohort_anchor_rt": "16.0841",
        "full_cohort_matched_peak_hypothesis_id": matched_peak_hypothesis_id,
        "full_cohort_cell_status": "rescued",
        "full_cohort_primary_matrix_area": "298267",
        "full_cohort_primary_matrix_area_source": "gaussian15_positive_asls_residual",
        "full_cohort_include_in_primary_matrix": include_primary,
        "full_cohort_consolidation_state": consolidation_state,
        "full_cohort_acceptance_gate_status": "hypothesis_candidate_review",
        "full_cohort_slice_blockers": blockers,
    }


def _manual_verdict_row(
    source_peak_hypothesis_id: str = "FAM001",
    sample: str = "Sample_A",
    *,
    matched_peak_hypothesis_id: str = "FAM_FULL_COHORT",
    reviewer_verdict: str = "same_peak_supported",
) -> dict[str, str]:
    return {
        "schema_version": "backfill_peakhypothesis_full_cohort_manual_verdict_v1",
        "source_peak_hypothesis_id": source_peak_hypothesis_id,
        "source_feature_family_id": source_peak_hypothesis_id,
        "sample_stem": sample,
        "full_cohort_matched_peak_hypothesis_id": matched_peak_hypothesis_id,
        "reviewer_verdict": reviewer_verdict,
        "review_basis": "visual_review_raw_plus_gaussian15_overlay_gallery",
        "reviewer": "user",
        "reviewed_at": "2026-06-09",
    }


def _machine_shape_row(
    *,
    source_peak_hypothesis_id: str,
    sample: str,
    matched_peak_hypothesis_id: str,
    decision: str,
    blockers: str = "",
    peak_count: str = "1",
    lobe_area: str = "456.7",
    same_peak_verdict: str = "same_peak_supported",
) -> dict[str, str]:
    return {
        "schema_version": "backfill_peakhypothesis_overlay_v1",
        "source_peak_hypothesis_id": source_peak_hypothesis_id,
        "sample_stem": sample,
        "full_cohort_matched_peak_hypothesis_id": matched_peak_hypothesis_id,
        "machine_shape_decision": decision,
        "machine_shape_reasons": (
            "gaussian15_selected_segment_single_complete_unimodal_peak"
            if decision == "standard_peak_shape_supported"
            else ""
        ),
        "machine_shape_blockers": blockers,
        "gaussian15_selected_segment_peak_count": peak_count,
        "gaussian15_lobe_start_rt": "12.9",
        "gaussian15_lobe_end_rt": "13.3",
        "gaussian15_lobe_area": lobe_area,
        "gaussian15_lobe_area_source": (
            "gaussian15_positive_asls_residual" if lobe_area else ""
        ),
        "gaussian15_lobe_boundary_source": "baseline_return" if lobe_area else "",
        "machine_same_peak_verdict": same_peak_verdict,
        "machine_same_peak_reasons": (
            "slice_gate_hypothesis_anchor_match;"
            "machine_gaussian15_standard_peak_shape_supported"
            if same_peak_verdict == "same_peak_supported"
            else ""
        ),
        "machine_same_peak_blockers": (
            "" if same_peak_verdict == "same_peak_supported" else "unit_blocker"
        ),
    }


def _read_tsv(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle, delimiter="\t"))


def _write_tsv(path: Path, rows: list[dict[str, str]]) -> None:
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, delimiter="\t", fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
