from __future__ import annotations

import csv
import hashlib
import json
from pathlib import Path

from xic_extractor.diagnostics import (
    backfill_peakhypothesis_activation_transfer as transfer,
)
from xic_extractor.diagnostics import backfill_peakhypothesis_promotion


def test_transfer_emits_full_cohort_peak_hypothesis_keyed_promotion_row() -> None:
    index = transfer.build_activation_transfer_index(
        normal_peak_decision_rows=[
            _normal_decision(
                "SRC001",
                "QC1",
                "FULL_COHORT_LOSER",
                area="42",
                quantitation_area="84",
                quantitation_area_source_field="gaussian15_lobe_area",
            ),
        ],
        activation_trial_rows=[
            _trial_row("SRC001", "QC1", "FULL_COHORT_LOSER"),
        ],
        source_artifact_sha256="c" * 64,
        source_run_id="transfer-test",
    )

    assert index.summary["transfer_status"] == "pass"
    assert index.summary["promotion_row_count"] == 1
    assert index.summary["source_peak_hypothesis_id_authority"] == (
        "audit_only_not_activation_key"
    )
    row = index.promotion_rows[0]
    assert row["schema_version"] == backfill_peakhypothesis_promotion.SCHEMA_VERSION
    assert row["peak_hypothesis_id"] == "FULL_COHORT_LOSER"
    assert row["feature_family_id"] == "FULL_COHORT_LOSER"
    assert row["sample_stem"] == "QC1"
    assert row["projected_matrix_value"] == "84"
    assert row["current_matrix_written"] == "FALSE"
    assert row["promotion_decision"] == "promote_matrix_write"
    assert "normal_peak_same_peak_transfer_activation" in row["promotion_reasons"]
    assert "normal_peak_quantitation_area_source_field:gaussian15_lobe_area" in (
        row["source_provenance_detail"]
    )

    transfer_row = index.transfer_rows[0]
    assert transfer_row["source_peak_hypothesis_id"] == "SRC001"
    assert transfer_row["activation_peak_hypothesis_id"] == "FULL_COHORT_LOSER"
    assert transfer_row["source_id_authority"] == "audit_only_not_activation_key"
    assert transfer_row["transfer_action"] == "emit_promotion_row"


def test_transfer_fails_closed_on_trial_blocker() -> None:
    index = transfer.build_activation_transfer_index(
        normal_peak_decision_rows=[
            _normal_decision("SRC001", "QC1", "FULL_COHORT_LOSER", area="42"),
        ],
        activation_trial_rows=[
            _trial_row(
                "SRC001",
                "QC1",
                "FULL_COHORT_LOSER",
                trial_action="blocked",
                blockers="same_peak_conflict",
            ),
        ],
        source_artifact_sha256="c" * 64,
        source_run_id="transfer-test",
    )

    assert index.summary["transfer_status"] == "fail"
    assert index.summary["promotion_row_count"] == 0
    assert "same_peak_conflict" in index.summary["hard_fail_reasons"]
    assert index.transfer_rows[0]["transfer_action"] == "blocked"
    assert index.transfer_rows[0]["transfer_blockers"] == "same_peak_conflict"


def test_cli_writes_transfer_outputs(tmp_path: Path) -> None:
    normal_tsv = tmp_path / "normal.tsv"
    trial_tsv = tmp_path / "trial.tsv"
    output_dir = tmp_path / "out"
    _write_tsv(normal_tsv, [_normal_decision("SRC001", "QC1", "FULL_COHORT_LOSER")])
    _write_tsv(trial_tsv, [_trial_row("SRC001", "QC1", "FULL_COHORT_LOSER")])

    from tools.diagnostics import (
        backfill_peakhypothesis_activation_transfer as cli,
    )

    assert (
        cli.main(
            [
                "--normal-peak-decisions-tsv",
                str(normal_tsv),
                "--activation-trial-tsv",
                str(trial_tsv),
                "--output-dir",
                str(output_dir),
                "--source-run-id",
                "cli-transfer",
            ],
        )
        == 0
    )

    promotion_tsv = (
        output_dir
        / "backfill_peakhypothesis_full_cohort_transfer_promotion_cells.tsv"
    )
    summary_json = (
        output_dir / "backfill_peakhypothesis_activation_transfer_summary.json"
    )
    assert promotion_tsv.is_file()
    assert (
        output_dir / "backfill_peakhypothesis_activation_transfer.tsv"
    ).is_file()
    summary = json.loads(summary_json.read_text(encoding="utf-8"))
    assert summary["source_run_id"] == "cli-transfer"
    assert summary["promotion_row_count"] == 1
    rows = _read_tsv(promotion_tsv)
    assert rows[0]["peak_hypothesis_id"] == "FULL_COHORT_LOSER"
    assert rows[0]["feature_family_id"] == "FULL_COHORT_LOSER"


def test_cli_source_artifact_hash_tracks_input_tsv_content(tmp_path: Path) -> None:
    normal_tsv = tmp_path / "normal.tsv"
    trial_tsv = tmp_path / "trial.tsv"
    output_dir = tmp_path / "out"
    _write_tsv(normal_tsv, [_normal_decision("SRC001", "QC1", "FULL_COHORT_LOSER")])
    _write_tsv(trial_tsv, [_trial_row("SRC001", "QC1", "FULL_COHORT_LOSER")])

    from tools.diagnostics import (
        backfill_peakhypothesis_activation_transfer as cli,
    )

    assert (
        cli.main(
            [
                "--normal-peak-decisions-tsv",
                str(normal_tsv),
                "--activation-trial-tsv",
                str(trial_tsv),
                "--output-dir",
                str(output_dir),
                "--source-run-id",
                "cli-transfer",
            ],
        )
        == 0
    )

    expected_hash = _input_bundle_sha256(normal_tsv, trial_tsv)
    promotion_rows = _read_tsv(
        output_dir / "backfill_peakhypothesis_full_cohort_transfer_promotion_cells.tsv",
    )
    transfer_rows = _read_tsv(
        output_dir / "backfill_peakhypothesis_activation_transfer.tsv",
    )
    summary = json.loads(
        (
            output_dir
            / "backfill_peakhypothesis_activation_transfer_summary.json"
        ).read_text(encoding="utf-8"),
    )
    assert promotion_rows[0]["source_artifact_sha256"] == expected_hash
    assert promotion_rows[0]["shadow_projection_sha256"] == expected_hash
    assert transfer_rows[0]["source_artifact_sha256"] == expected_hash
    assert summary["source_artifact_sha256"] == expected_hash


def _normal_decision(
    source_peak_hypothesis_id: str,
    sample_stem: str,
    full_cohort_peak_hypothesis_id: str,
    *,
    area: str = "42",
    quantitation_area: str | None = None,
    quantitation_area_source_field: str = "full_cohort_primary_matrix_area",
    decision: str = "require_backfill",
    required: str = "TRUE",
    blockers: str = "",
) -> dict[str, str]:
    quantitation_area = area if quantitation_area is None else quantitation_area
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
        "full_cohort_primary_matrix_area": area,
        "full_cohort_primary_matrix_area_source": "gaussian15_positive_asls_residual",
        "normal_peak_quantitation_area": quantitation_area,
        "normal_peak_quantitation_area_source": "gaussian15_positive_asls_residual",
        "normal_peak_quantitation_area_source_field": (
            quantitation_area_source_field
        ),
        "normal_peak_quantitation_boundary_start_rt": (
            "12.9" if quantitation_area_source_field == "gaussian15_lobe_area" else ""
        ),
        "normal_peak_quantitation_boundary_end_rt": (
            "13.3" if quantitation_area_source_field == "gaussian15_lobe_area" else ""
        ),
        "normal_peak_quantitation_boundary_source": (
            "baseline_return"
            if quantitation_area_source_field == "gaussian15_lobe_area"
            else ""
        ),
        "full_cohort_include_in_primary_matrix": "FALSE",
        "full_cohort_consolidation_state": "primary_loser",
        "manual_same_peak_verdict": "same_peak_supported",
        "same_peak_verdict": "same_peak_supported",
        "same_peak_verdict_source": "manual_review",
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


def _trial_row(
    source_peak_hypothesis_id: str,
    sample_stem: str,
    full_cohort_peak_hypothesis_id: str,
    *,
    trial_action: str = "would_write_normal_peak_override",
    blockers: str = "",
) -> dict[str, str]:
    return {
        "schema_version": "backfill_peakhypothesis_activation_trial",
        "source_run_id": "test",
        "policy_id": "same_peak_normal_peak_override",
        "source_peak_hypothesis_id": source_peak_hypothesis_id,
        "sample_stem": sample_stem,
        "full_cohort_matched_peak_hypothesis_id": full_cohort_peak_hypothesis_id,
        "full_cohort_include_in_primary_matrix": "FALSE",
        "full_cohort_consolidation_state": "primary_loser",
        "manual_same_peak_verdict": "same_peak_supported",
        "same_peak_verdict": "same_peak_supported",
        "same_peak_verdict_source": "manual_review",
        "normal_peak_decision": "require_backfill",
        "normal_peak_backfill_required": "TRUE",
        "current_public_matrix_written": "FALSE",
        "current_public_matrix_value": "",
        "trial_action": trial_action,
        "matrix_diff_expected": "TRUE",
        "trial_blockers": blockers,
    }


def _write_tsv(path: Path, rows: list[dict[str, str]]) -> None:
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            delimiter="\t",
            fieldnames=list(rows[0]),
            lineterminator="\n",
        )
        writer.writeheader()
        writer.writerows(rows)


def _read_tsv(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle, delimiter="\t"))


def _input_bundle_sha256(*paths: Path) -> str:
    digest = hashlib.sha256()
    for path in paths:
        digest.update(path.name.encode("utf-8"))
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()
