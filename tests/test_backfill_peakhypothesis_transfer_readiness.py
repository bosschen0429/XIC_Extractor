import csv
import json
from pathlib import Path

from tests.alignment_pipeline_helpers import peak_config as _peak_config
from xic_extractor.alignment.pipeline_outputs import alignment_metadata
from xic_extractor.diagnostics import (
    backfill_peakhypothesis_transfer_readiness as readiness,
)


def test_readiness_reports_production_candidate_not_ready(tmp_path: Path) -> None:
    index = readiness.build_transfer_readiness(
        promotion_summary=_promotion_summary(promote_count=11),
        activation_acceptance_rows=[_activation_acceptance_row()],
        full_cohort_metadata=_full_cohort_metadata(),
        full_cohort_counts=_full_cohort_counts(),
        source_run_id="unit",
    )

    row = index.readiness_row
    assert row["readiness_label"] == "production_candidate"
    assert row["production_ready"] == "FALSE"
    assert row["eight_raw_gate_status"] == "pass"
    assert row["full_cohort_artifact_status"] == "pass"
    assert row["manual_review_scope"] == "observed_fixture_cohort_top14_standard_cells"
    assert row["full_cohort_peak_shape_review_status"] == "not_assessed"
    assert row["area_generalization_status"] == "not_generalized_to_full_cohort"
    assert row["hard_fail_reasons"] == ""
    assert row["remaining_blockers"] == (
        "explicit_product_transfer_decision_required;"
        "full_cohort_slice_specific_no_regression_not_assessed;"
        "full_cohort_peak_shape_not_manually_confirmed"
    )
    assert row["next_action"] == (
        "request_explicit_product_transfer_decision_or_build_full_cohort_acceptance_gate"
    )


def test_readiness_fails_closed_when_fixture_cohort_acceptance_fails() -> None:
    acceptance_row = {
        **_activation_acceptance_row(),
        "acceptance_status": "fail",
        "hard_fail_reasons": "unexpected_matrix_diff",
    }

    index = readiness.build_transfer_readiness(
        promotion_summary=_promotion_summary(promote_count=11),
        activation_acceptance_rows=[acceptance_row],
        full_cohort_metadata=_full_cohort_metadata(),
        full_cohort_counts=_full_cohort_counts(),
    )

    assert index.readiness_row["readiness_label"] == "blocked"
    assert index.readiness_row["production_ready"] == "FALSE"
    assert index.readiness_row["eight_raw_gate_status"] == "fail"
    assert (
        "fixture_cohort_activation_acceptance_not_passed"
        in (index.readiness_row["hard_fail_reasons"])
    )


def test_readiness_fails_closed_when_full_cohort_contract_is_not_canonical() -> None:
    metadata = {**_full_cohort_metadata(), "output_level": "full"}

    index = readiness.build_transfer_readiness(
        promotion_summary=_promotion_summary(promote_count=11),
        activation_acceptance_rows=[_activation_acceptance_row()],
        full_cohort_metadata=metadata,
        full_cohort_counts=_full_cohort_counts(),
    )

    assert index.readiness_row["readiness_label"] == "blocked"
    assert index.readiness_row["full_cohort_artifact_status"] == "fail"
    assert (
        "full_cohort_metadata_contract_not_canonical"
        in (index.readiness_row["hard_fail_reasons"])
    )


def test_readiness_accepts_targeted_istd_alignment_metadata_v4() -> None:
    metadata = _current_full_cohort_metadata(targeted_istd=True)

    index = readiness.build_transfer_readiness(
        promotion_summary=_promotion_summary(promote_count=11),
        activation_acceptance_rows=[_activation_acceptance_row()],
        full_cohort_metadata=metadata,
        full_cohort_counts=_full_cohort_counts(),
    )

    assert index.readiness_row["full_cohort_metadata_contract_status"] == "pass"
    assert index.readiness_row["full_cohort_artifact_status"] == "pass"


def test_readiness_accepts_current_unanchored_alignment_metadata_v3() -> None:
    index = readiness.build_transfer_readiness(
        promotion_summary=_promotion_summary(promote_count=11),
        activation_acceptance_rows=[_activation_acceptance_row()],
        full_cohort_metadata=_current_full_cohort_metadata(targeted_istd=False),
        full_cohort_counts=_full_cohort_counts(),
    )

    assert index.readiness_row["full_cohort_metadata_contract_status"] == "pass"


def test_readiness_accepts_historical_targeted_istd_metadata_v3() -> None:
    metadata = {
        **_current_full_cohort_metadata(targeted_istd=True),
        "schema_version": "alignment-results-v3",
    }

    index = readiness.build_transfer_readiness(
        promotion_summary=_promotion_summary(promote_count=11),
        activation_acceptance_rows=[_activation_acceptance_row()],
        full_cohort_metadata=metadata,
        full_cohort_counts=_full_cohort_counts(),
    )

    assert index.readiness_row["full_cohort_metadata_contract_status"] == "pass"


def test_readiness_rejects_legacy_policy_for_anchored_metadata_v3() -> None:
    metadata = {
        **_full_cohort_metadata(),
        "targeted_istd_anchor_identity": "anchor-id",
        "targeted_istd_target_snapshot_id": "snapshot-id",
    }

    index = readiness.build_transfer_readiness(
        promotion_summary=_promotion_summary(promote_count=11),
        activation_acceptance_rows=[_activation_acceptance_row()],
        full_cohort_metadata=metadata,
        full_cohort_counts=_full_cohort_counts(),
    )

    assert index.readiness_row["full_cohort_metadata_contract_status"] == "fail"


def test_readiness_rejects_legacy_matrix_policy_under_v4() -> None:
    metadata = {
        **_full_cohort_metadata(),
        "schema_version": "alignment-results-v4",
        "targeted_istd_anchor_identity": "anchor-id",
        "targeted_istd_target_snapshot_id": "snapshot-id",
    }

    index = readiness.build_transfer_readiness(
        promotion_summary=_promotion_summary(promote_count=11),
        activation_acceptance_rows=[_activation_acceptance_row()],
        full_cohort_metadata=metadata,
        full_cohort_counts=_full_cohort_counts(),
    )

    assert index.readiness_row["full_cohort_metadata_contract_status"] == "fail"


def test_readiness_rejects_incomplete_targeted_istd_alignment_metadata_v4() -> None:
    metadata = _current_full_cohort_metadata(targeted_istd=True)
    metadata.pop("targeted_istd_target_snapshot_id")

    index = readiness.build_transfer_readiness(
        promotion_summary=_promotion_summary(promote_count=11),
        activation_acceptance_rows=[_activation_acceptance_row()],
        full_cohort_metadata=metadata,
        full_cohort_counts=_full_cohort_counts(),
    )

    assert index.readiness_row["full_cohort_metadata_contract_status"] == "fail"
    assert (
        "full_cohort_metadata_contract_not_canonical"
        in (index.readiness_row["hard_fail_reasons"])
    )


def test_readiness_fails_closed_when_full_cohort_acceptance_gate_fails() -> None:
    index = readiness.build_transfer_readiness(
        promotion_summary=_promotion_summary(promote_count=11),
        activation_acceptance_rows=[_activation_acceptance_row()],
        full_cohort_metadata=_full_cohort_metadata(),
        full_cohort_counts=_full_cohort_counts(),
        full_cohort_acceptance_gate_summary=_full_cohort_acceptance_gate_summary(
            gate_status="fail"
        ),
        full_cohort_winner_remap_summary=_full_cohort_winner_remap_summary(
            remap_gate_status="partial",
        ),
    )

    row = index.readiness_row
    assert row["readiness_label"] == "blocked"
    assert row["full_cohort_acceptance_gate_status"] == "fail"
    assert row["full_cohort_acceptance_gate_blocked_count"] == "11"
    assert row["full_cohort_acceptance_gate_hypothesis_candidate_review_count"] == "0"
    assert row["full_cohort_acceptance_gate_primary_loser_count"] == "10"
    assert row["full_cohort_acceptance_gate_duplicate_assigned_count"] == "10"
    assert row["full_cohort_winner_remap_status"] == "partial"
    assert row["full_cohort_winner_remap_candidate_count"] == "10"
    assert row["full_cohort_winner_remap_blocked_count"] == "1"
    assert row["full_cohort_winner_remap_missing_winner_count"] == "1"
    assert "full_cohort_slice_specific_no_regression_failed" in row["hard_fail_reasons"]
    assert row["remaining_blockers"] == ""
    assert row["next_action"] == "review_full_cohort_winner_remap_candidates"


def test_readiness_surfaces_full_cohort_hypothesis_candidate_review_partial() -> None:
    index = readiness.build_transfer_readiness(
        promotion_summary=_promotion_summary(promote_count=11),
        activation_acceptance_rows=[_activation_acceptance_row()],
        full_cohort_metadata=_full_cohort_metadata(),
        full_cohort_counts=_full_cohort_counts(),
        full_cohort_acceptance_gate_summary=_full_cohort_acceptance_gate_summary(
            gate_status="partial",
            blocked_count=0,
            candidate_no_regression_count=0,
            hypothesis_candidate_review_count=11,
            primary_loser_count=9,
            duplicate_assigned_count=0,
            absent_count=0,
        ),
    )

    row = index.readiness_row
    assert row["readiness_label"] == "blocked"
    assert row["full_cohort_acceptance_gate_status"] == "partial"
    assert row["full_cohort_acceptance_gate_blocked_count"] == "0"
    assert row["full_cohort_acceptance_gate_candidate_no_regression_count"] == "0"
    assert row["full_cohort_acceptance_gate_hypothesis_candidate_review_count"] == "11"
    assert row["full_cohort_acceptance_gate_primary_loser_count"] == "9"
    assert row["full_cohort_acceptance_gate_absent_count"] == "0"
    assert row["full_cohort_winner_remap_status"] == "not_assessed"
    assert row["hard_fail_reasons"] == "full_cohort_slice_specific_no_regression_failed"
    assert row["next_action"] == (
        "review_full_cohort_hypothesis_candidates_before_product_transfer"
    )


def test_readiness_accepts_manual_same_peak_review_for_partial_slice_gate() -> None:
    index = readiness.build_transfer_readiness(
        promotion_summary=_promotion_summary(promote_count=11),
        activation_acceptance_rows=[_activation_acceptance_row()],
        full_cohort_metadata=_full_cohort_metadata(),
        full_cohort_counts=_full_cohort_counts(),
        full_cohort_acceptance_gate_summary=_full_cohort_acceptance_gate_summary(
            gate_status="partial",
            blocked_count=0,
            candidate_no_regression_count=0,
            hypothesis_candidate_review_count=11,
            primary_loser_count=9,
            duplicate_assigned_count=0,
            absent_count=0,
        ),
        full_cohort_hypothesis_review_summary=_full_cohort_hypothesis_review_summary(
            reviewed_candidate_count=11,
            same_peak_supported_count=11,
        ),
    )

    row = index.readiness_row
    assert row["readiness_label"] == "production_candidate"
    assert row["production_ready"] == "FALSE"
    assert row["full_cohort_acceptance_gate_status"] == "partial"
    assert row["full_cohort_peak_shape_review_status"] == (
        "manual_same_peak_supported_all_review_candidates"
    )
    assert row["area_generalization_status"] == (
        "manual_same_peak_reviewed_area_policy_pending"
    )
    assert row["hard_fail_reasons"] == ""
    assert row["remaining_blockers"] == (
        "explicit_product_transfer_decision_required;"
        "full_cohort_consolidation_policy_not_productized"
    )
    assert row["next_action"] == (
        "define_full_cohort_consolidation_policy_for_same_peak_non_primary_candidates"
    )


def test_writes_readiness_tsv_and_summary(tmp_path: Path) -> None:
    index = readiness.build_transfer_readiness(
        promotion_summary=_promotion_summary(promote_count=11),
        activation_acceptance_rows=[_activation_acceptance_row()],
        full_cohort_metadata=_full_cohort_metadata(),
        full_cohort_counts=_full_cohort_counts(),
        source_run_id="unit",
    )

    outputs = readiness.write_transfer_readiness_outputs(tmp_path, index)

    rows = _read_tsv(outputs.readiness_tsv)
    summary = json.loads(outputs.summary_json.read_text(encoding="utf-8"))
    assert rows[0]["readiness_label"] == "production_candidate"
    assert summary["readiness_label"] == "production_candidate"


def test_cli_builds_readiness_from_artifact_paths(tmp_path: Path) -> None:
    promotion_summary = tmp_path / "promotion_summary.json"
    activation_acceptance = tmp_path / "activation_acceptance.tsv"
    full_cohort_dir = tmp_path / "full_cohort"
    output_dir = tmp_path / "out"
    full_cohort_dir.mkdir()
    promotion_summary.write_text(
        json.dumps(_promotion_summary(promote_count=2)),
        encoding="utf-8",
    )
    _write_tsv(activation_acceptance, [_activation_acceptance_row(count="2")])
    (full_cohort_dir / "alignment_run_metadata.json").write_text(
        json.dumps(_full_cohort_metadata()),
        encoding="utf-8",
    )
    _write_tsv(
        full_cohort_dir / "alignment_matrix.tsv",
        [{"Mz": "100", "RT": "5", "S1": "1", "S2": ""}],
    )
    _write_tsv(
        full_cohort_dir / "alignment_review.tsv", [{"feature_family_id": "FAM001"}]
    )
    _write_tsv(
        full_cohort_dir / "alignment_cells.tsv",
        [
            {"feature_family_id": "FAM001", "sample_stem": "S1"},
            {"feature_family_id": "FAM001", "sample_stem": "S2"},
        ],
    )
    _write_tsv(full_cohort_dir / "skipped_evidence_ledger.tsv", [{"reason": "none"}])
    full_cohort_acceptance_gate_summary = (
        tmp_path / "full_cohort_acceptance_gate_summary.json"
    )
    full_cohort_acceptance_gate_summary.write_text(
        json.dumps(
            _full_cohort_acceptance_gate_summary(
                gate_status="pass",
                promotion_count=2,
                blocked_count=0,
            ),
        ),
        encoding="utf-8",
    )
    full_cohort_winner_remap_summary = (
        tmp_path / "full_cohort_winner_remap_summary.json"
    )
    full_cohort_winner_remap_summary.write_text(
        json.dumps(
            _full_cohort_winner_remap_summary(
                remap_gate_status="pass",
                candidate_count=2,
                blocked_count=0,
            ),
        ),
        encoding="utf-8",
    )

    from tools.diagnostics import backfill_peakhypothesis_transfer_readiness as cli

    assert (
        cli.main(
            [
                "--promotion-summary-json",
                str(promotion_summary),
                "--activation-acceptance-tsv",
                str(activation_acceptance),
                "--full_cohort-alignment-dir",
                str(full_cohort_dir),
                "--output-dir",
                str(output_dir),
                "--source-run-id",
                "unit-cli",
                "--expected-full_cohort-sample-columns",
                "2",
                "--full-cohort-acceptance-gate-summary-json",
                str(full_cohort_acceptance_gate_summary),
                "--full_cohort-winner-remap-summary-json",
                str(full_cohort_winner_remap_summary),
            ],
        )
        == 0
    )

    rows = _read_tsv(output_dir / "backfill_peakhypothesis_transfer_readiness.tsv")
    assert rows[0]["full_cohort_sample_column_count"] == "2"
    assert rows[0]["full_cohort_acceptance_gate_status"] == "pass"
    assert rows[0]["full_cohort_winner_remap_status"] == "pass"
    assert rows[0]["readiness_label"] == "production_candidate"


def test_cli_accepts_manual_same_peak_review_summary(tmp_path: Path) -> None:
    promotion_summary = tmp_path / "promotion_summary.json"
    activation_acceptance = tmp_path / "activation_acceptance.tsv"
    full_cohort_dir = tmp_path / "full_cohort"
    output_dir = tmp_path / "out"
    full_cohort_dir.mkdir()
    promotion_summary.write_text(
        json.dumps(_promotion_summary(promote_count=2)),
        encoding="utf-8",
    )
    _write_tsv(activation_acceptance, [_activation_acceptance_row(count="2")])
    (full_cohort_dir / "alignment_run_metadata.json").write_text(
        json.dumps(_full_cohort_metadata()),
        encoding="utf-8",
    )
    _write_tsv(
        full_cohort_dir / "alignment_matrix.tsv",
        [{"Mz": "100", "RT": "5", "S1": "1", "S2": ""}],
    )
    _write_tsv(
        full_cohort_dir / "alignment_review.tsv", [{"feature_family_id": "FAM001"}]
    )
    _write_tsv(
        full_cohort_dir / "alignment_cells.tsv",
        [
            {"feature_family_id": "FAM001", "sample_stem": "S1"},
            {"feature_family_id": "FAM001", "sample_stem": "S2"},
        ],
    )
    full_cohort_acceptance_gate_summary = (
        tmp_path / "full_cohort_acceptance_gate_summary.json"
    )
    full_cohort_acceptance_gate_summary.write_text(
        json.dumps(
            _full_cohort_acceptance_gate_summary(
                gate_status="partial",
                promotion_count=2,
                blocked_count=0,
                candidate_no_regression_count=0,
                hypothesis_candidate_review_count=2,
                primary_loser_count=2,
                duplicate_assigned_count=0,
                absent_count=0,
            ),
        ),
        encoding="utf-8",
    )
    full_cohort_hypothesis_review_summary = (
        tmp_path / "full_cohort_manual_review_summary.json"
    )
    full_cohort_hypothesis_review_summary.write_text(
        json.dumps(
            _full_cohort_hypothesis_review_summary(
                reviewed_candidate_count=2,
                same_peak_supported_count=2,
            ),
        ),
        encoding="utf-8",
    )

    from tools.diagnostics import backfill_peakhypothesis_transfer_readiness as cli

    assert (
        cli.main(
            [
                "--promotion-summary-json",
                str(promotion_summary),
                "--activation-acceptance-tsv",
                str(activation_acceptance),
                "--full_cohort-alignment-dir",
                str(full_cohort_dir),
                "--output-dir",
                str(output_dir),
                "--expected-full_cohort-sample-columns",
                "2",
                "--full-cohort-acceptance-gate-summary-json",
                str(full_cohort_acceptance_gate_summary),
                "--full_cohort-hypothesis-review-summary-json",
                str(full_cohort_hypothesis_review_summary),
            ],
        )
        == 0
    )

    rows = _read_tsv(output_dir / "backfill_peakhypothesis_transfer_readiness.tsv")
    assert rows[0]["full_cohort_peak_shape_review_status"] == (
        "manual_same_peak_supported_all_review_candidates"
    )
    assert rows[0]["hard_fail_reasons"] == ""
    assert rows[0]["next_action"] == (
        "define_full_cohort_consolidation_policy_for_same_peak_non_primary_candidates"
    )


def _promotion_summary(*, promote_count: int) -> dict[str, object]:
    return {
        "schema_version": "backfill_peakhypothesis_promotion_v1",
        "readiness_label": "shadow_ready",
        "decision_counts": {"promote_matrix_write": promote_count},
        "allowlist_row_count": promote_count,
    }


def _activation_acceptance_row(count: str = "11") -> dict[str, str]:
    return {
        "schema_version": "backfill_peakhypothesis_activation_acceptance_v1",
        "validation_scope": "fixture_cohort_current_writer_matrix_diff",
        "promotion_row_count": count,
        "activation_decision_row_count": count,
        "changed_matrix_cell_count": count,
        "unexpected_matrix_diff_count": "0",
        "missing_matrix_diff_count": "0",
        "value_mismatch_count": "0",
        "decision_mismatch_count": "0",
        "preflight_mismatch_count": "0",
        "value_delta_mismatch_count": "0",
        "application_summary_mismatch_count": "0",
        "canonical_row_identity_ready": "TRUE",
        "acceptance_status": "pass",
        "hard_fail_reasons": "",
    }


def _full_cohort_metadata() -> dict[str, str]:
    return {
        "output_level": "validation-minimal",
        "backfill_scope": "production-equivalent",
        "audit_evidence_mode": "none",
        "matrix_value_policy": "gaussian15_positive_asls_residual_primary",
        "owner_backfill_xic_backend": "raw",
        "schema_version": "alignment-results-v3",
    }


def _current_full_cohort_metadata(*, targeted_istd: bool) -> dict[str, str]:
    return alignment_metadata(
        discovery_batch_index=Path("batch.csv"),
        raw_dir=Path("raw"),
        dll_dir=Path("dll"),
        owner_backfill_xic_backend="raw",
        output_level="validation-minimal",
        peak_config=_peak_config(),
        backfill_scope="production-equivalent",
        audit_evidence_mode="none",
        targeted_istd_anchor_identity="anchor-id" if targeted_istd else "",
        targeted_istd_target_snapshot_id=("snapshot-id" if targeted_istd else ""),
    )


def _full_cohort_counts() -> dict[str, int]:
    return {
        "matrix_row_count": 685,
        "sample_column_count": 85,
        "review_row_count": 21151,
        "cell_row_count": 1797835,
        "skipped_evidence_row_count": 38976,
    }


def _full_cohort_acceptance_gate_summary(
    *,
    gate_status: str,
    promotion_count: int = 11,
    blocked_count: int = 11,
    candidate_no_regression_count: int | None = None,
    hypothesis_candidate_review_count: int = 0,
    primary_loser_count: int | None = None,
    duplicate_assigned_count: int | None = None,
    absent_count: int | None = None,
) -> dict[str, object]:
    if candidate_no_regression_count is None:
        candidate_no_regression_count = 0 if blocked_count else promotion_count
    if primary_loser_count is None:
        primary_loser_count = 10 if blocked_count else 0
    if duplicate_assigned_count is None:
        duplicate_assigned_count = 10 if blocked_count else 0
    if absent_count is None:
        absent_count = 1 if blocked_count else 0
    return {
        "schema_version": "backfill_peakhypothesis_acceptance_gate_v1",
        "gate_status": gate_status,
        "promotion_row_count": promotion_count,
        "candidate_no_regression_count": candidate_no_regression_count,
        "hypothesis_candidate_review_count": hypothesis_candidate_review_count,
        "blocked_count": blocked_count,
        "primary_loser_count": primary_loser_count,
        "duplicate_assigned_count": duplicate_assigned_count,
        "absent_count": absent_count,
    }


def _full_cohort_winner_remap_summary(
    *,
    remap_gate_status: str,
    candidate_count: int = 10,
    blocked_count: int = 1,
) -> dict[str, object]:
    return {
        "schema_version": "backfill_peakhypothesis_winner_remap_v1",
        "remap_gate_status": remap_gate_status,
        "row_count": candidate_count + blocked_count,
        "remap_candidate_review_count": candidate_count,
        "blocked_count": blocked_count,
        "winner_detected_count": 7,
        "winner_rescued_count": 3,
        "missing_winner_count": blocked_count,
    }


def _full_cohort_hypothesis_review_summary(
    *,
    reviewed_candidate_count: int,
    same_peak_supported_count: int,
) -> dict[str, object]:
    return {
        "schema_version": "backfill_peakhypothesis_full_cohort_manual_verdict_v1",
        "reviewed_candidate_count": reviewed_candidate_count,
        "same_peak_supported_count": same_peak_supported_count,
        "same_peak_conflict_count": 0,
        "unreviewed_candidate_count": reviewed_candidate_count
        - same_peak_supported_count,
    }


def _read_tsv(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle, delimiter="\t"))


def _write_tsv(path: Path, rows: list[dict[str, str]]) -> None:
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, delimiter="\t", fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
