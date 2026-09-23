from __future__ import annotations

import csv
import json
from pathlib import Path

import pytest

from tests.matrix_identity_fixtures import current_matrix_identity_row
from xic_extractor.diagnostics import (
    backfill_peakhypothesis_activation_trial as trial,
)


def test_trial_counts_primary_loser_override_without_loading_cells(
    tmp_path: Path,
) -> None:
    artifact_dir = _write_artifact_dir(tmp_path)
    index = trial.build_activation_trial_index(
        current_full_cohort_artifact_dir=artifact_dir,
        normal_peak_decision_rows=[
            _normal_decision(
                "SRC001", "QC1", "FULL_COHORT_LOSER", "FALSE", "primary_loser"
            ),
            _normal_decision(
                "SRC002", "QC2", "FULL_COHORT_WINNER", "TRUE", "primary_winner"
            ),
        ],
        manual_verdict_rows=[
            _manual_verdict(
                "SRC001", "QC1", "FULL_COHORT_LOSER", "FALSE", "primary_loser"
            ),
            _manual_verdict(
                "SRC002", "QC2", "FULL_COHORT_WINNER", "TRUE", "primary_winner"
            ),
        ],
        source_run_id="trial",
    )

    assert index.summary["trial_status"] == "pass"
    assert index.summary["candidate_count"] == 30289
    assert index.summary["sample_count"] == 2
    assert index.summary["matrix_row_count"] == 1
    assert index.summary["normal_peak_required_count"] == 2
    assert index.summary["primary_loser_count"] == 1
    assert index.summary["primary_winner_count"] == 1
    assert index.summary["already_primary_matrix_written_count"] == 1
    assert index.summary["expected_matrix_diff_count"] == 1
    assert index.summary["unexpected_diff_count"] == 0
    assert index.summary["owner_backfill_elapsed_sec"] == pytest.approx(408.94)
    assert index.summary["write_outputs_elapsed_sec"] == pytest.approx(219.65)

    rows = {row["source_peak_hypothesis_id"]: row for row in index.trial_rows}
    assert rows["SRC001"]["trial_action"] == "would_write_normal_peak_override"
    assert rows["SRC001"]["matrix_diff_expected"] is True
    assert rows["SRC002"]["trial_action"] == "already_primary_matrix_written"
    assert rows["SRC002"]["matrix_diff_expected"] is False


def test_trial_fails_closed_on_same_peak_conflict(tmp_path: Path) -> None:
    artifact_dir = _write_artifact_dir(tmp_path)
    index = trial.build_activation_trial_index(
        current_full_cohort_artifact_dir=artifact_dir,
        normal_peak_decision_rows=[
            _normal_decision(
                "SRC001", "QC1", "FULL_COHORT_LOSER", "FALSE", "primary_loser"
            ),
        ],
        manual_verdict_rows=[
            _manual_verdict(
                "SRC001",
                "QC1",
                "FULL_COHORT_LOSER",
                "FALSE",
                "primary_loser",
                verdict="same_peak_conflict",
            ),
        ],
        source_run_id="trial",
    )

    assert index.summary["trial_status"] == "fail"
    assert index.summary["same_peak_conflict_count"] == 1
    assert index.summary["expected_matrix_diff_count"] == 0
    assert "same_peak_conflict" in index.summary["hard_fail_reasons"]
    assert index.trial_rows[0]["trial_action"] == "blocked"


def test_trial_uses_machine_same_peak_verdict_when_manual_rows_absent(
    tmp_path: Path,
) -> None:
    artifact_dir = _write_artifact_dir(tmp_path)
    index = trial.build_activation_trial_index(
        current_full_cohort_artifact_dir=artifact_dir,
        normal_peak_decision_rows=[
            _normal_decision(
                "SRC001",
                "QC1",
                "FULL_COHORT_LOSER",
                "FALSE",
                "primary_loser",
                same_peak_verdict="same_peak_supported",
                same_peak_source="machine_gaussian15_trace",
            ),
        ],
        manual_verdict_rows=[],
        source_run_id="trial",
    )

    assert index.summary["trial_status"] == "pass"
    assert index.summary["manual_verdict_row_count"] == 0
    assert index.summary["same_peak_supported_count"] == 1
    assert index.summary["expected_matrix_diff_count"] == 1
    assert index.trial_rows[0]["trial_action"] == "would_write_normal_peak_override"
    assert index.trial_rows[0]["same_peak_verdict"] == "same_peak_supported"
    assert index.trial_rows[0]["same_peak_verdict_source"] == (
        "machine_gaussian15_trace"
    )


def test_cli_writes_summary_and_trial_rows(tmp_path: Path) -> None:
    artifact_dir = _write_artifact_dir(tmp_path)
    normal_tsv = tmp_path / "normal.tsv"
    manual_tsv = tmp_path / "manual.tsv"
    output_dir = tmp_path / "out"
    _write_tsv(
        normal_tsv,
        [
            _normal_decision(
                "SRC001", "QC1", "FULL_COHORT_LOSER", "FALSE", "primary_loser"
            )
        ],
    )
    _write_tsv(
        manual_tsv,
        [
            _manual_verdict(
                "SRC001", "QC1", "FULL_COHORT_LOSER", "FALSE", "primary_loser"
            )
        ],
    )

    from tools.diagnostics import backfill_peakhypothesis_activation_trial as cli

    assert (
        cli.main(
            [
                "--current-full_cohort-artifact-dir",
                str(artifact_dir),
                "--normal-peak-decisions-tsv",
                str(normal_tsv),
                "--full_cohort-manual-verdicts-tsv",
                str(manual_tsv),
                "--output-dir",
                str(output_dir),
                "--source-run-id",
                "cli-trial",
            ],
        )
        == 0
    )
    assert (output_dir / "backfill_peakhypothesis_activation_trial.tsv").is_file()
    summary = json.loads(
        (
            output_dir / "backfill_peakhypothesis_activation_trial_summary.json"
        ).read_text(encoding="utf-8")
    )
    assert summary["source_run_id"] == "cli-trial"
    assert summary["expected_matrix_diff_count"] == 1


def test_cli_accepts_machine_same_peak_without_manual_verdict_tsv(
    tmp_path: Path,
) -> None:
    artifact_dir = _write_artifact_dir(tmp_path)
    normal_tsv = tmp_path / "normal.tsv"
    output_dir = tmp_path / "out"
    _write_tsv(
        normal_tsv,
        [
            _normal_decision(
                "SRC001",
                "QC1",
                "FULL_COHORT_LOSER",
                "FALSE",
                "primary_loser",
                same_peak_verdict="same_peak_supported",
                same_peak_source="machine_gaussian15_trace",
            ),
        ],
    )

    from tools.diagnostics import backfill_peakhypothesis_activation_trial as cli

    assert (
        cli.main(
            [
                "--current-full_cohort-artifact-dir",
                str(artifact_dir),
                "--normal-peak-decisions-tsv",
                str(normal_tsv),
                "--output-dir",
                str(output_dir),
                "--source-run-id",
                "cli-machine-trial",
            ],
        )
        == 0
    )
    summary = json.loads(
        (
            output_dir / "backfill_peakhypothesis_activation_trial_summary.json"
        ).read_text(encoding="utf-8")
    )
    assert summary["manual_verdict_row_count"] == 0
    assert summary["expected_matrix_diff_count"] == 1


def _write_artifact_dir(tmp_path: Path) -> Path:
    artifact_dir = tmp_path / "artifact"
    artifact_dir.mkdir()
    _write_tsv(
        artifact_dir / "alignment_matrix.tsv",
        [{"Mz": "100.0000", "RT": "10.0", "QC1": "", "QC2": "1234"}],
    )
    _write_tsv(
        artifact_dir / "alignment_matrix_identity.tsv",
        [
            current_matrix_identity_row(
                sample_values={"QC1": "", "QC2": "1234"},
                index=1,
                mz="100",
                rt="10.0",
                peak_hypothesis_id="FULL_COHORT_WINNER",
                row_identity_basis="peak_hypothesis",
            ),
        ],
    )
    (artifact_dir / "alignment_run_metadata.json").write_text(
        json.dumps(
            {
                "output_level": "validation-minimal",
                "backfill_scope": "production-equivalent",
                "audit_evidence_mode": "none",
                "performance_profile": "validation-fast",
            },
        ),
        encoding="utf-8",
    )
    (artifact_dir / "timing.json").write_text(
        json.dumps(
            {
                "records": [
                    {
                        "stage": "alignment.read_candidates",
                        "elapsed_sec": 1.98,
                        "metrics": {"candidate_count": 30289},
                    },
                    {"stage": "alignment.owner_backfill", "elapsed_sec": 408.94},
                    {"stage": "alignment.build_matrix", "elapsed_sec": 60.98},
                    {"stage": "alignment.claim_registry", "elapsed_sec": 57.68},
                    {
                        "stage": "alignment.primary_consolidation",
                        "elapsed_sec": 89.63,
                    },
                    {"stage": "alignment.write_outputs", "elapsed_sec": 219.65},
                ],
            },
        ),
        encoding="utf-8",
    )
    return artifact_dir


def _normal_decision(
    source_peak_hypothesis_id: str,
    sample_stem: str,
    full_cohort_peak_hypothesis_id: str,
    include_primary: str,
    consolidation_state: str,
    *,
    decision: str = "require_backfill",
    required: str = "TRUE",
    blockers: str = "",
    same_peak_verdict: str = "same_peak_supported",
    same_peak_source: str = "manual_review",
) -> dict[str, str]:
    return {
        "schema_version": "backfill_peakhypothesis_normal_peak_decision",
        "source_run_id": "test",
        "peak_hypothesis_id": source_peak_hypothesis_id,
        "activation_unit_scope": "peak_hypothesis",
        "feature_family_id": source_peak_hypothesis_id,
        "seed_group_id": "seed",
        "sample_stem": sample_stem,
        "area_policy": "standard_assessable_area",
        "matrix_quantitative_use": "standard_quantitative_use",
        "promotion_decision": "promote_matrix_write",
        "full_cohort_matched_peak_hypothesis_id": full_cohort_peak_hypothesis_id,
        "full_cohort_cell_status": "backfilled",
        "full_cohort_primary_matrix_area": "42",
        "full_cohort_primary_matrix_area_source": "gaussian15_positive_asls_residual",
        "full_cohort_include_in_primary_matrix": include_primary,
        "full_cohort_consolidation_state": consolidation_state,
        "manual_same_peak_verdict": (
            same_peak_verdict if same_peak_source == "manual_review" else ""
        ),
        "same_peak_verdict": same_peak_verdict,
        "same_peak_verdict_source": same_peak_source,
        "normal_peak_shape_definition": (
            "gaussian15_asls_residual_selected_shape_context_single_complete_unimodal_peak;"
            "raw_spikes_neighbor_contact_family_multiplet_not_blockers"
        ),
        "normal_peak_decision": decision,
        "normal_peak_backfill_required": required,
        "normal_peak_decision_reasons": "standard_peak_same_peak_supported",
        "normal_peak_decision_blockers": blockers,
        "consolidation_policy_effect": (
            "allow_same_peak_peakhypothesis_candidate_despite_non_primary"
        ),
    }


def _manual_verdict(
    source_peak_hypothesis_id: str,
    sample_stem: str,
    full_cohort_peak_hypothesis_id: str,
    include_primary: str,
    consolidation_state: str,
    *,
    verdict: str = "same_peak_supported",
) -> dict[str, str]:
    return {
        "schema_version": "backfill_peakhypothesis_full_cohort_manual_verdict",
        "source_run_id": "test",
        "review_item_id": "HYPREV0001",
        "sample_stem": sample_stem,
        "source_peak_hypothesis_id": source_peak_hypothesis_id,
        "source_feature_family_id": source_peak_hypothesis_id,
        "full_cohort_anchor_mz": "100.0",
        "full_cohort_anchor_rt": "10.0",
        "full_cohort_matched_peak_hypothesis_id": full_cohort_peak_hypothesis_id,
        "full_cohort_consolidation_winner_group_hypothesis_id": "WINNER",
        "full_cohort_include_in_primary_matrix": include_primary,
        "full_cohort_consolidation_state": consolidation_state,
        "reviewer_verdict": verdict,
        "reviewer_note": "",
        "review_basis": "test",
        "reviewer": "tester",
        "reviewed_at": "2026-06-09",
        "overlay_png_path": "",
        "overlay_pdf_path": "",
        "overlay_smooth_method": "gaussian15_asls_residual",
        "overlay_smooth_window_points": "15",
        "product_transfer_implication": (
            "peak_shape_closed_consolidation_policy_required"
        ),
    }


def _write_tsv(path: Path, rows: list[dict[str, str]]) -> None:
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]), delimiter="\t")
        writer.writeheader()
        writer.writerows(rows)
