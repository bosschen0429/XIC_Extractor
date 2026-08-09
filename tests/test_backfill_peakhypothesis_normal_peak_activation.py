import csv
import json
from pathlib import Path

from xic_extractor.diagnostics import (
    backfill_peakhypothesis_85raw_activation_trial as trial,
)


def test_normal_peak_activation_cli_runs_end_to_end_matrix_acceptance(
    tmp_path: Path,
) -> None:
    promotion_tsv = tmp_path / "promotion.tsv"
    raw85_tsv = tmp_path / "raw85.tsv"
    shape_tsv = tmp_path / "machine_shape.tsv"
    trial_tsv = tmp_path / "trial.tsv"
    matrix_tsv = tmp_path / "alignment_matrix.tsv"
    identity_tsv = tmp_path / "alignment_matrix_identity.tsv"
    review_tsv = tmp_path / "alignment_review.tsv"
    output_dir = tmp_path / "out"

    _write_tsv(
        promotion_tsv,
        [
            _promotion_row("HYP_SOURCE_NORMAL", "FAM_SOURCE_NORMAL", "S2"),
            _promotion_row(
                "HYP_SOURCE_NONSTANDARD",
                "FAM_SOURCE_NONSTANDARD",
                "S2",
                area_policy="nonstandard_assessable_area",
                matrix_quantitative_use="use_with_uncertainty",
                promotion_decision="blocked",
                promotion_blockers="nonstandard_area_review_only",
            ),
        ],
    )
    _write_tsv(
        raw85_tsv,
        [
            _raw85_row(
                "HYP_SOURCE_NORMAL",
                "FAM_SOURCE_NORMAL",
                "S2",
                matched_peak_hypothesis_id="HYP_RAW85_NORMAL",
                area="300",
            ),
            _raw85_row(
                "HYP_SOURCE_NONSTANDARD",
                "FAM_SOURCE_NONSTANDARD",
                "S2",
                matched_peak_hypothesis_id="HYP_RAW85_NONSTANDARD",
                area="200",
            ),
        ],
    )
    _write_tsv(
        shape_tsv,
        [
            _shape_row(
                "HYP_SOURCE_NORMAL",
                "S2",
                "HYP_RAW85_NORMAL",
                decision="standard_peak_shape_supported",
                lobe_area="450",
            ),
            _shape_row(
                "HYP_SOURCE_NONSTANDARD",
                "S2",
                "HYP_RAW85_NONSTANDARD",
                decision="nonstandard_peak_shape",
                blockers="selected_segment_multiple_gaussian15_peaks",
                peak_count="2",
            ),
        ],
    )
    _write_tsv(
        trial_tsv,
        [
            _trial_row(
                "HYP_SOURCE_NORMAL",
                "S2",
                "HYP_RAW85_NORMAL",
            ),
        ],
    )
    _write_tsv(
        matrix_tsv,
        [
            {"Mz": "301.1", "RT": "16.1", "S1": "111", "S2": ""},
        ],
    )
    _write_tsv(
        identity_tsv,
        [
            {
                "matrix_row_index": "1",
                "Mz": "301.1",
                "RT": "16.1",
                "peak_hypothesis_id": "HYP_RAW85_NORMAL",
                "row_identity_basis": "no_split_peak_hypothesis",
                "source_feature_family_ids": "HYP_RAW85_NORMAL",
                "source_feature_family_count": "1",
            },
        ],
    )
    _write_tsv(
        review_tsv,
        [
            {
                "feature_family_id": "HYP_RAW85_NORMAL",
                "neutral_loss_tag": "8-oxoG",
                "family_center_mz": "301.1",
                "family_center_rt": "16.1",
                "include_in_primary_matrix": "FALSE",
            },
        ],
    )

    from tools.diagnostics import (
        backfill_peakhypothesis_normal_peak_activation as cli,
    )

    assert cli.main(
        [
            "--promotion-cells-tsv",
            str(promotion_tsv),
            "--raw85-slice-gate-tsv",
            str(raw85_tsv),
            "--machine-shape-evidence-tsv",
            str(shape_tsv),
            "--activation-trial-tsv",
            str(trial_tsv),
            "--alignment-matrix-tsv",
            str(matrix_tsv),
            "--alignment-matrix-identity-tsv",
            str(identity_tsv),
            "--alignment-review-tsv",
            str(review_tsv),
            "--output-dir",
            str(output_dir),
            "--source-run-id",
            "unit-e2e",
            "--validation-scope",
            "85raw_current_writer_matrix_diff",
        ],
    ) == 0

    normal_rows = _read_tsv(
        output_dir
        / "normal_peak_decision"
        / "backfill_peakhypothesis_normal_peak_decisions.tsv",
    )
    assert [row["normal_peak_decision"] for row in normal_rows] == [
        "require_backfill",
        "review_only_nonstandard_peak",
    ]

    matrix_rows = _read_tsv(output_dir / "activated_matrix" / "alignment_matrix.tsv")
    assert matrix_rows[0]["S2"] == "4.50000e+02"

    acceptance = _read_tsv(
        output_dir
        / "activation_acceptance"
        / "backfill_peakhypothesis_activation_acceptance.tsv",
    )[0]
    assert acceptance["acceptance_status"] == "pass"
    assert acceptance["changed_matrix_cell_count"] == "1"
    assert acceptance["unexpected_matrix_diff_count"] == "0"
    assert acceptance["missing_matrix_diff_count"] == "0"

    delta = _read_tsv(
        output_dir / "activated_matrix" / "activation_value_delta.tsv",
    )[0]
    assert delta["matrix_value_kind"] == "backfill_activation"
    assert delta["matrix_value_source"] == "activation_values_tsv"
    assert delta["matrix_value_source_artifact_sha256"]
    assert delta["matrix_value_source_row_sha256"]

    summary = json.loads(
        (
            output_dir
            / "backfill_peakhypothesis_normal_peak_activation_summary.json"
        ).read_text(encoding="utf-8"),
    )
    assert summary["normal_peak_activation_status"] == "pass"
    assert summary["required_backfill_count"] == 1
    assert summary["review_only_nonstandard_count"] == 1
    assert summary["activation_acceptance_status"] == "pass"
    assert summary["product_behavior_changed"] is True


def test_normal_peak_activation_cli_fails_closed_on_standard_blocker(
    tmp_path: Path,
) -> None:
    promotion_tsv = tmp_path / "promotion.tsv"
    raw85_tsv = tmp_path / "raw85.tsv"
    manual_tsv = tmp_path / "manual.tsv"
    shape_tsv = tmp_path / "machine_shape.tsv"
    trial_tsv = tmp_path / "trial.tsv"
    matrix_tsv = tmp_path / "alignment_matrix.tsv"
    identity_tsv = tmp_path / "alignment_matrix_identity.tsv"
    review_tsv = tmp_path / "alignment_review.tsv"
    output_dir = tmp_path / "out"

    _write_tsv(
        promotion_tsv,
        [_promotion_row("HYP_SOURCE_NORMAL", "FAM_SOURCE_NORMAL", "S2")],
    )
    _write_tsv(
        raw85_tsv,
        [
            _raw85_row(
                "HYP_SOURCE_NORMAL",
                "FAM_SOURCE_NORMAL",
                "S2",
                matched_peak_hypothesis_id="HYP_RAW85_NORMAL",
            ),
        ],
    )
    _write_tsv(
        manual_tsv,
        [
            _manual_row(
                "HYP_SOURCE_NORMAL",
                "S2",
                "HYP_RAW85_NORMAL",
                reviewer_verdict="same_peak_conflict",
            ),
        ],
    )
    _write_tsv(
        shape_tsv,
        [
            _shape_row(
                "HYP_SOURCE_NORMAL",
                "S2",
                "HYP_RAW85_NORMAL",
                decision="standard_peak_shape_supported",
            ),
        ],
    )
    _write_tsv(
        trial_tsv,
        [_trial_row("HYP_SOURCE_NORMAL", "S2", "HYP_RAW85_NORMAL")],
    )
    _write_tsv(matrix_tsv, [{"Mz": "301.1", "RT": "16.1", "S2": ""}])
    _write_tsv(
        identity_tsv,
        [
            {
                "matrix_row_index": "1",
                "Mz": "301.1",
                "RT": "16.1",
                "peak_hypothesis_id": "HYP_RAW85_NORMAL",
                "row_identity_basis": "no_split_peak_hypothesis",
                "source_feature_family_ids": "HYP_RAW85_NORMAL",
                "source_feature_family_count": "1",
            },
        ],
    )
    _write_tsv(
        review_tsv,
        [
            {
                "feature_family_id": "HYP_RAW85_NORMAL",
                "neutral_loss_tag": "8-oxoG",
                "family_center_mz": "301.1",
                "family_center_rt": "16.1",
                "include_in_primary_matrix": "FALSE",
            },
        ],
    )

    from tools.diagnostics import (
        backfill_peakhypothesis_normal_peak_activation as cli,
    )

    assert cli.main(
        [
            "--promotion-cells-tsv",
            str(promotion_tsv),
            "--raw85-slice-gate-tsv",
            str(raw85_tsv),
            "--raw85-manual-verdict-tsv",
            str(manual_tsv),
            "--machine-shape-evidence-tsv",
            str(shape_tsv),
            "--activation-trial-tsv",
            str(trial_tsv),
            "--alignment-matrix-tsv",
            str(matrix_tsv),
            "--alignment-matrix-identity-tsv",
            str(identity_tsv),
            "--alignment-review-tsv",
            str(review_tsv),
            "--output-dir",
            str(output_dir),
        ],
    ) == 1

    summary = json.loads(
        (
            output_dir
            / "backfill_peakhypothesis_normal_peak_activation_summary.json"
        ).read_text(encoding="utf-8"),
    )
    assert summary["normal_peak_activation_status"] == "fail"
    assert summary["standard_blocked_count"] == 1
    assert "standard_normal_peak_blocked" in summary["hard_fail_reasons"]
    assert not (output_dir / "activated_matrix" / "alignment_matrix.tsv").exists()


def _promotion_row(
    peak_hypothesis_id: str,
    family_id: str,
    sample: str,
    *,
    area_policy: str = "standard_assessable_area",
    matrix_quantitative_use: str = "standard_quantitative_use",
    promotion_decision: str = "promote_matrix_write",
    promotion_blockers: str = "",
) -> dict[str, str]:
    return {
        "schema_version": "backfill_peakhypothesis_promotion_v1",
        "peak_hypothesis_id": peak_hypothesis_id,
        "activation_unit_scope": "peak_hypothesis",
        "feature_family_id": family_id,
        "seed_group_id": f"seed::{family_id}",
        "sample_stem": sample,
        "promotion_decision": promotion_decision,
        "promotion_reasons": "allowlisted_peakhypothesis_same_peak_backfill",
        "promotion_blockers": promotion_blockers,
        "current_raw_status": "rescued",
        "current_matrix_written": "FALSE",
        "projected_matrix_value": "123.4",
        "area_policy": area_policy,
        "matrix_quantitative_use": matrix_quantitative_use,
    }


def _raw85_row(
    peak_hypothesis_id: str,
    family_id: str,
    sample: str,
    *,
    matched_peak_hypothesis_id: str,
    area: str = "300",
) -> dict[str, str]:
    return {
        "schema_version": "backfill_peakhypothesis_raw85_slice_gate_v1",
        "peak_hypothesis_id": peak_hypothesis_id,
        "feature_family_id": family_id,
        "seed_group_id": f"seed::{family_id}",
        "sample_stem": sample,
        "raw85_matched_peak_hypothesis_id": matched_peak_hypothesis_id,
        "raw85_cell_status": "rescued",
        "raw85_primary_matrix_area": area,
        "raw85_primary_matrix_area_source": "gaussian15_positive_asls_residual",
        "raw85_include_in_primary_matrix": "FALSE",
        "raw85_consolidation_state": "primary_loser",
        "raw85_slice_gate_status": "hypothesis_candidate_review",
        "raw85_slice_blockers": (
            "raw85_candidate_not_primary_matrix_row;"
            "raw85_candidate_family_consolidation_review_required"
        ),
    }


def _manual_row(
    source_peak_hypothesis_id: str,
    sample: str,
    matched_peak_hypothesis_id: str,
    *,
    reviewer_verdict: str = "same_peak_supported",
) -> dict[str, str]:
    return {
        "schema_version": "backfill_peakhypothesis_raw85_manual_verdict_v1",
        "source_peak_hypothesis_id": source_peak_hypothesis_id,
        "sample_stem": sample,
        "raw85_matched_peak_hypothesis_id": matched_peak_hypothesis_id,
        "reviewer_verdict": reviewer_verdict,
    }


def _shape_row(
    source_peak_hypothesis_id: str,
    sample: str,
    matched_peak_hypothesis_id: str,
    *,
    decision: str,
    blockers: str = "",
    peak_count: str = "1",
    lobe_area: str = "450",
) -> dict[str, str]:
    return {
        "schema_version": "backfill_peakhypothesis_raw85_overlay_v1",
        "source_peak_hypothesis_id": source_peak_hypothesis_id,
        "sample_stem": sample,
        "raw85_matched_peak_hypothesis_id": matched_peak_hypothesis_id,
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
        "gaussian15_lobe_area": (
            lobe_area if decision == "standard_peak_shape_supported" else ""
        ),
        "gaussian15_lobe_area_source": (
            "gaussian15_positive_asls_residual"
            if decision == "standard_peak_shape_supported"
            else ""
        ),
        "gaussian15_lobe_boundary_source": (
            "baseline_return" if decision == "standard_peak_shape_supported" else ""
        ),
        "machine_same_peak_verdict": (
            "same_peak_supported"
            if decision == "standard_peak_shape_supported"
            else "same_peak_not_supported"
        ),
        "machine_same_peak_reasons": (
            "slice_gate_hypothesis_anchor_match;"
            "machine_gaussian15_standard_peak_shape_supported"
            if decision == "standard_peak_shape_supported"
            else ""
        ),
        "machine_same_peak_blockers": (
            "" if decision == "standard_peak_shape_supported" else "unit_blocker"
        ),
    }


def _trial_row(
    source_peak_hypothesis_id: str,
    sample: str,
    matched_peak_hypothesis_id: str,
) -> dict[str, str]:
    return {
        "schema_version": trial.SCHEMA_VERSION,
        "source_run_id": "unit",
        "policy_id": trial.POLICY_ID,
        "source_peak_hypothesis_id": source_peak_hypothesis_id,
        "sample_stem": sample,
        "raw85_matched_peak_hypothesis_id": matched_peak_hypothesis_id,
        "raw85_include_in_primary_matrix": "FALSE",
        "raw85_consolidation_state": "primary_loser",
        "manual_same_peak_verdict": "same_peak_supported",
        "same_peak_verdict": "same_peak_supported",
        "same_peak_verdict_source": "manual_review",
        "normal_peak_decision": "require_backfill",
        "normal_peak_backfill_required": "TRUE",
        "current_public_matrix_written": "FALSE",
        "current_public_matrix_value": "",
        "trial_action": "would_write_normal_peak_override",
        "matrix_diff_expected": "TRUE",
        "trial_blockers": "",
    }


def _write_tsv(path: Path, rows: list[dict[str, str]]) -> None:
    fieldnames = tuple(dict.fromkeys(key for row in rows for key in row))
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
