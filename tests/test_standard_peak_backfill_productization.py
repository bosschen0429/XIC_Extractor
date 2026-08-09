from __future__ import annotations

import csv
import json
from pathlib import Path

import pytest

from tools.diagnostics import standard_peak_backfill_productization as cli
from xic_extractor.alignment.tsv_writer import (
    ALIGNMENT_CELLS_COLUMNS,
    ALIGNMENT_OWNER_BACKFILL_SEED_AUDIT_COLUMNS,
    ALIGNMENT_REVIEW_COLUMNS,
)
from xic_extractor.diagnostics import (
    standard_peak_backfill_productization as productization_module,
)
from xic_extractor.diagnostics.diagnostic_io import file_sha256
from xic_extractor.diagnostics.shadow_production_projection import (
    SHADOW_PRODUCTION_PROJECTION_COLUMNS,
)


def test_standard_peak_productization_applies_matrix_and_synced_gallery(
    tmp_path: Path,
) -> None:
    fixture = _write_fixture(tmp_path)
    output_dir = tmp_path / "out"

    assert (
        cli.main(
            [
                "--shadow-projection-cells-tsv",
                str(fixture["shadow"]),
                "--alignment-matrix-tsv",
                str(fixture["matrix"]),
                "--alignment-matrix-identity-tsv",
                str(fixture["identity"]),
                "--alignment-review-tsv",
                str(fixture["review"]),
                "--output-dir",
                str(output_dir),
                "--source-run-id",
                "unit-standard-peak-productization",
                "--write-gallery",
                "--alignment-cells-tsv",
                str(fixture["cells"]),
                "--backfill-seed-audit-tsv",
                str(fixture["seeds"]),
                "--shift-aware-standard-peak-gate-tsv",
                str(fixture["gate"]),
            ],
        )
        == 0
    )

    summary = json.loads(
        (
            output_dir / "standard_peak_backfill_productization_summary.json"
        ).read_text(encoding="utf-8"),
    )
    assert summary["status"] == "pass"
    assert summary["schema_version"] == productization_module.SCHEMA_VERSION
    assert summary["selected_activation_row_count"] == "1"
    assert summary["skipped_non_standard_reason_count"] == "1"
    assert summary["activation_application_status"] == "applied"
    assert summary["activation_output_mode"] == "matrix-only"
    assert summary["matrix_cells_written"] == "1"
    assert summary["activation_value_delta_written_count"] == "1"
    assert summary["product_behavior_changed"] == "TRUE"

    matrix_rows = _read_tsv(output_dir / "activated_matrix" / "alignment_matrix.tsv")
    identity_rows = _read_tsv(
        output_dir / "activated_matrix" / "alignment_matrix_identity.tsv",
    )
    matrix_index_by_hypothesis = {
        row["peak_hypothesis_id"]: int(row["matrix_row_index"]) - 1
        for row in identity_rows
    }
    assert matrix_rows[matrix_index_by_hypothesis["FAM_STD"]]["S2"] == (
        "1.00000e+02"
    )
    assert matrix_rows[matrix_index_by_hypothesis["FAM_NON"]]["S2"] == ""

    delta = _read_tsv(output_dir / "activated_matrix" / "activation_value_delta.tsv")
    assert len(delta) == 1
    assert delta[0]["feature_family_id"] == "FAM_STD"
    assert delta[0]["matrix_value_effect"] == "written"
    assert delta[0]["activation_reason"] == (
        "standard_peak_shift_aware_ms1_same_peak_product_authorized"
    )

    seed_guard = _read_tsv(
        output_dir
        / "standard_peak_activation_inputs"
        / "seed_guard_decisions.tsv",
    )
    by_seed_guard_family = {row["feature_family_id"]: row for row in seed_guard}
    assert by_seed_guard_family["FAM_STD"]["seed_guard_status"] == (
        "not_applicable_small_cohort"
    )
    assert by_seed_guard_family["FAM_STD"]["actual_written_cell_count"] == "1"
    assert by_seed_guard_family["FAM_STD"]["actual_written_cell_keys"] == "FAM_STD/S2"
    assert by_seed_guard_family["FAM_STD"][
        "actual_cohort_scale_written_cell_count"
    ] == "0"
    assert by_seed_guard_family["FAM_STD"]["actual_per_cell_written_cell_count"] == "1"
    assert by_seed_guard_family["FAM_STD"]["activation_value_delta_path"].endswith(
        "activation_value_delta.tsv"
    )
    assert len(by_seed_guard_family["FAM_STD"]["activation_value_delta_sha256"]) == 64
    assert "FAM_NON" not in by_seed_guard_family

    acceptance = _read_tsv(
        output_dir
        / "standard_peak_activation_inputs"
        / "standard_peak_activation_acceptance.tsv",
    )[0]
    assert acceptance["activation_decision_scope"] == (
        "machine_gate_standard_peak_rows"
    )
    assert acceptance["must_not_regress_basis"] == (
        "machine_shift_aware_standard_peak_gate"
    )

    gallery_groups = _read_tsv(
        output_dir
        / "reconciliation_gallery"
        / "backfill_evidence_reconciliation_groups.tsv",
    )
    by_family = {row["feature_family_id"]: row for row in gallery_groups}
    assert by_family["FAM_STD"]["product_behavior_state"] == (
        "product_primary_backfilled"
    )
    assert by_family["FAM_STD"]["top_product_reason"] == (
        "activation_value_delta_written"
    )
    assert by_family["FAM_NON"]["product_behavior_state"] == (
        "product_rescued_context_only"
    )
    assert by_family["FAM_NON"]["reconciliation_class"] == (
        "product_rejects_and_evidence_blocks"
    )


def test_standard_peak_productization_can_limit_writer_to_high_signal_clean_scope(
    tmp_path: Path,
) -> None:
    fixture = _write_fixture(tmp_path)
    _write_tsv(
        fixture["shadow"],
        [
            _shadow_row(
                "FAM_STD",
                "S2",
                "100",
                standard=True,
                row_sha="a" * 64,
            ),
            _shadow_row(
                "FAM_NON",
                "S2",
                "200",
                standard=True,
                row_sha="c" * 64,
            ),
        ],
        SHADOW_PRODUCTION_PROJECTION_COLUMNS,
    )
    scope_audit = tmp_path / "activation_high_signal_clean_scope_audit.tsv"
    _write_tsv(
        scope_audit,
        [
            _scope_audit_row("FAM_STD", "S2", "a" * 64, "eligible"),
            _scope_audit_row("FAM_NON", "S2", "c" * 64, "ineligible"),
        ],
        (
            *productization_module.ACTIVATION_SCOPE_AUDIT_REQUIRED_COLUMNS,
            "low_scan_clean_status",
        ),
    )
    output_dir = tmp_path / "out"

    assert (
        cli.main(
            [
                "--shadow-projection-cells-tsv",
                str(fixture["shadow"]),
                "--alignment-matrix-tsv",
                str(fixture["matrix"]),
                "--alignment-matrix-identity-tsv",
                str(fixture["identity"]),
                "--alignment-review-tsv",
                str(fixture["review"]),
                "--output-dir",
                str(output_dir),
                "--source-run-id",
                "unit-narrow-high-signal-clean-productization",
                "--high-signal-clean-activation-scope-audit-tsv",
                str(scope_audit),
            ],
        )
        == 0
    )

    summary = json.loads(
        (
            output_dir / "standard_peak_backfill_productization_summary.json"
        ).read_text(encoding="utf-8"),
    )
    assert summary["status"] == "pass"
    assert summary["schema_version"] == "standard_peak_backfill_productization_v1"
    assert summary["activation_scope_filter_status"] == "applied"
    assert summary["activation_scope_contract"] == (
        "high_signal_clean_eligible_activation_rows"
    )
    assert summary["activation_scope_filter_selected_shadow_row_count"] == "1"
    assert summary["activation_scope_filter_eligible_audit_row_count"] == "1"
    assert summary["selected_activation_row_count"] == "1"
    assert summary["matrix_cells_written"] == "1"
    assert summary["narrow_product_writer_expected_diff_acceptance_status"] == "pass"
    assert summary["next_action"] == (
        "narrow_high_signal_clean_backfill_production_ready"
    )

    acceptance = json.loads(
        (
            output_dir / "narrow_product_writer_expected_diff_acceptance.json"
        ).read_text(encoding="utf-8"),
    )
    assert acceptance["acceptance_status"] == "pass"
    assert acceptance["readiness_tier"] == "production_ready"
    assert acceptance["product_surface_changed"] == "TRUE"
    assert acceptance["eligible_audit_row_count"] == "1"
    assert acceptance["product_written_delta_row_count"] == "1"
    assert acceptance["non_eligible_delta_row_count"] == "0"

    matrix_rows = _read_tsv(output_dir / "activated_matrix" / "alignment_matrix.tsv")
    identity_rows = _read_tsv(
        output_dir / "activated_matrix" / "alignment_matrix_identity.tsv",
    )
    matrix_index_by_hypothesis = {
        row["peak_hypothesis_id"]: int(row["matrix_row_index"]) - 1
        for row in identity_rows
    }
    assert matrix_rows[matrix_index_by_hypothesis["FAM_STD"]]["S2"] == (
        "1.00000e+02"
    )
    assert matrix_rows[matrix_index_by_hypothesis["FAM_NON"]]["S2"] == ""


def test_standard_peak_productization_can_limit_writer_to_low_scan_clean_scope(
    tmp_path: Path,
) -> None:
    fixture = _write_fixture(tmp_path)
    _write_tsv(
        fixture["shadow"],
        [
            _shadow_row(
                "FAM_STD",
                "S2",
                "100",
                standard=True,
                row_sha="a" * 64,
            ),
            _shadow_row(
                "FAM_NON",
                "S2",
                "200",
                standard=True,
                row_sha="c" * 64,
            ),
        ],
        SHADOW_PRODUCTION_PROJECTION_COLUMNS,
    )
    scope_audit = tmp_path / "activation_low_scan_clean_scope_audit.tsv"
    _write_tsv(
        scope_audit,
        [
            _scope_audit_row(
                "FAM_STD",
                "S2",
                "a" * 64,
                "ineligible",
                low_scan_clean_status="eligible",
            ),
            _scope_audit_row(
                "FAM_NON",
                "S2",
                "c" * 64,
                "ineligible",
                low_scan_clean_status="ineligible",
            ),
        ],
        (
            *productization_module.ACTIVATION_SCOPE_AUDIT_REQUIRED_COLUMNS,
            "low_scan_clean_status",
        ),
    )
    output_dir = tmp_path / "out"

    assert (
        cli.main(
            [
                "--shadow-projection-cells-tsv",
                str(fixture["shadow"]),
                "--alignment-matrix-tsv",
                str(fixture["matrix"]),
                "--alignment-matrix-identity-tsv",
                str(fixture["identity"]),
                "--alignment-review-tsv",
                str(fixture["review"]),
                "--output-dir",
                str(output_dir),
                "--source-run-id",
                "unit-narrow-low-scan-clean-productization",
                "--low-scan-clean-activation-scope-audit-tsv",
                str(scope_audit),
            ],
        )
        == 0
    )

    summary = json.loads(
        (
            output_dir / "standard_peak_backfill_productization_summary.json"
        ).read_text(encoding="utf-8"),
    )
    assert summary["status"] == "pass"
    assert summary["activation_scope_filter_status"] == "applied"
    assert summary["activation_scope_contract"] == (
        "low_scan_clean_eligible_activation_rows"
    )
    assert summary["activation_scope_filter_selected_shadow_row_count"] == "1"
    assert summary["matrix_cells_written"] == "1"
    assert summary["narrow_product_writer_expected_diff_acceptance_status"] == "pass"
    assert summary["next_action"] == "narrow_low_scan_clean_backfill_production_ready"

    acceptance = json.loads(
        (
            output_dir / "narrow_product_writer_expected_diff_acceptance.json"
        ).read_text(encoding="utf-8"),
    )
    assert acceptance["acceptance_status"] == "pass"
    assert acceptance["readiness_tier"] == "production_ready"
    assert acceptance["expected_scope"] == "low_scan_clean_eligible_activation_rows"
    assert acceptance["product_written_delta_row_count"] == "1"
    assert acceptance["non_eligible_delta_row_count"] == "0"


def test_standard_peak_productization_can_limit_writer_to_low_height_clean_scope(
    tmp_path: Path,
) -> None:
    fixture = _write_fixture(tmp_path)
    _write_tsv(
        fixture["shadow"],
        [
            _shadow_row(
                "FAM_STD",
                "S2",
                "100",
                standard=True,
                row_sha="a" * 64,
            ),
            _shadow_row(
                "FAM_NON",
                "S2",
                "200",
                standard=True,
                row_sha="c" * 64,
            ),
        ],
        SHADOW_PRODUCTION_PROJECTION_COLUMNS,
    )
    scope_audit = tmp_path / "activation_low_height_clean_scope_audit.tsv"
    _write_tsv(
        scope_audit,
        [
            _scope_audit_row(
                "FAM_STD",
                "S2",
                "a" * 64,
                "ineligible",
                low_height_clean_status="eligible",
            ),
            _scope_audit_row(
                "FAM_NON",
                "S2",
                "c" * 64,
                "ineligible",
                low_height_clean_status="ineligible",
            ),
        ],
        (
            *productization_module.ACTIVATION_SCOPE_AUDIT_REQUIRED_COLUMNS,
            "low_scan_clean_status",
            "low_height_clean_status",
        ),
    )
    output_dir = tmp_path / "out"

    assert (
        cli.main(
            [
                "--shadow-projection-cells-tsv",
                str(fixture["shadow"]),
                "--alignment-matrix-tsv",
                str(fixture["matrix"]),
                "--alignment-matrix-identity-tsv",
                str(fixture["identity"]),
                "--alignment-review-tsv",
                str(fixture["review"]),
                "--output-dir",
                str(output_dir),
                "--source-run-id",
                "unit-narrow-low-height-clean-productization",
                "--low-height-clean-activation-scope-audit-tsv",
                str(scope_audit),
            ],
        )
        == 0
    )

    summary = json.loads(
        (
            output_dir / "standard_peak_backfill_productization_summary.json"
        ).read_text(encoding="utf-8"),
    )
    assert summary["status"] == "pass"
    assert summary["activation_scope_filter_status"] == "applied"
    assert summary["activation_scope_contract"] == (
        "low_height_clean_eligible_activation_rows"
    )
    assert summary["activation_scope_filter_selected_shadow_row_count"] == "1"
    assert summary["matrix_cells_written"] == "1"
    assert summary["narrow_product_writer_expected_diff_acceptance_status"] == "pass"
    assert summary["next_action"] == (
        "narrow_low_height_clean_backfill_production_ready"
    )

    acceptance = json.loads(
        (
            output_dir / "narrow_product_writer_expected_diff_acceptance.json"
        ).read_text(encoding="utf-8"),
    )
    assert acceptance["acceptance_status"] == "pass"
    assert acceptance["readiness_tier"] == "production_ready"
    assert acceptance["expected_scope"] == "low_height_clean_eligible_activation_rows"
    assert acceptance["product_surface_changed"] == "TRUE"
    assert acceptance["product_written_delta_row_count"] == "1"
    assert acceptance["unexpected_delta_row_count"] == "0"
    assert acceptance["non_eligible_delta_row_count"] == "0"
    assert acceptance["not_written_delta_row_count"] == "0"


def test_standard_peak_productization_can_limit_writer_to_low_height_low_scan_scope(
    tmp_path: Path,
) -> None:
    fixture = _write_fixture(tmp_path)
    _write_tsv(
        fixture["shadow"],
        [
            _shadow_row(
                "FAM_STD",
                "S2",
                "100",
                standard=True,
                row_sha="a" * 64,
            ),
            _shadow_row(
                "FAM_NON",
                "S2",
                "200",
                standard=True,
                row_sha="c" * 64,
            ),
        ],
        SHADOW_PRODUCTION_PROJECTION_COLUMNS,
    )
    scope_audit = tmp_path / "activation_low_height_low_scan_clean_scope_audit.tsv"
    _write_tsv(
        scope_audit,
        [
            _scope_audit_row(
                "FAM_STD",
                "S2",
                "a" * 64,
                "ineligible",
                low_height_low_scan_clean_status="eligible",
            ),
            _scope_audit_row(
                "FAM_NON",
                "S2",
                "c" * 64,
                "ineligible",
                low_height_low_scan_clean_status="ineligible",
            ),
        ],
        (
            *productization_module.ACTIVATION_SCOPE_AUDIT_REQUIRED_COLUMNS,
            "low_scan_clean_status",
            "low_height_clean_status",
            "low_height_low_scan_clean_status",
        ),
    )
    output_dir = tmp_path / "out"

    assert (
        cli.main(
            [
                "--shadow-projection-cells-tsv",
                str(fixture["shadow"]),
                "--alignment-matrix-tsv",
                str(fixture["matrix"]),
                "--alignment-matrix-identity-tsv",
                str(fixture["identity"]),
                "--alignment-review-tsv",
                str(fixture["review"]),
                "--output-dir",
                str(output_dir),
                "--source-run-id",
                "unit-narrow-low-height-low-scan-clean-productization",
                "--low-height-low-scan-clean-activation-scope-audit-tsv",
                str(scope_audit),
            ],
        )
        == 0
    )

    summary = json.loads(
        (
            output_dir / "standard_peak_backfill_productization_summary.json"
        ).read_text(encoding="utf-8"),
    )
    assert summary["status"] == "pass"
    assert summary["activation_scope_filter_status"] == "applied"
    assert summary["activation_scope_contract"] == (
        "low_height_low_scan_clean_eligible_activation_rows"
    )
    assert summary["activation_scope_filter_selected_shadow_row_count"] == "1"
    assert summary["matrix_cells_written"] == "1"
    assert summary["narrow_product_writer_expected_diff_acceptance_status"] == "pass"
    assert summary["next_action"] == (
        "narrow_low_height_low_scan_clean_backfill_production_ready"
    )

    acceptance = json.loads(
        (
            output_dir / "narrow_product_writer_expected_diff_acceptance.json"
        ).read_text(encoding="utf-8"),
    )
    assert acceptance["acceptance_status"] == "pass"
    assert acceptance["readiness_tier"] == "production_ready"
    assert acceptance["expected_scope"] == (
        "low_height_low_scan_clean_eligible_activation_rows"
    )
    assert acceptance["product_surface_changed"] == "TRUE"
    assert acceptance["product_written_delta_row_count"] == "1"
    assert acceptance["unexpected_delta_row_count"] == "0"
    assert acceptance["non_eligible_delta_row_count"] == "0"
    assert acceptance["not_written_delta_row_count"] == "0"


def test_standard_peak_productization_can_limit_writer_to_low_height_stability_scope(
    tmp_path: Path,
) -> None:
    fixture = _write_fixture(tmp_path)
    _write_tsv(
        fixture["shadow"],
        [
            _shadow_row(
                "FAM_STD",
                "S2",
                "100",
                standard=True,
                row_sha="a" * 64,
            ),
            _shadow_row(
                "FAM_NON",
                "S2",
                "200",
                standard=True,
                row_sha="c" * 64,
            ),
        ],
        SHADOW_PRODUCTION_PROJECTION_COLUMNS,
    )
    scope_audit = tmp_path / "activation_low_height_stability_scope_audit.tsv"
    _write_tsv(
        scope_audit,
        [
            _scope_audit_row(
                "FAM_STD",
                "S2",
                "a" * 64,
                "ineligible",
                cell_height="500000",
            ),
            _scope_audit_row(
                "FAM_NON",
                "S2",
                "c" * 64,
                "ineligible",
                cell_height="3000000",
            ),
        ],
        (
            *productization_module.ACTIVATION_SCOPE_AUDIT_REQUIRED_COLUMNS,
            "low_scan_clean_status",
            "cell_height",
        ),
    )
    stability_audit = tmp_path / "reintegration_stability_audit.tsv"
    _write_tsv(
        stability_audit,
        [
            _stability_audit_row("FAM_STD", "S2", "a" * 64, "eligible"),
            _stability_audit_row("FAM_NON", "S2", "c" * 64, "eligible"),
        ],
        productization_module.REINTEGRATION_STABILITY_AUDIT_REQUIRED_COLUMNS,
    )
    output_dir = tmp_path / "out"

    assert (
        cli.main(
            [
                "--shadow-projection-cells-tsv",
                str(fixture["shadow"]),
                "--alignment-matrix-tsv",
                str(fixture["matrix"]),
                "--alignment-matrix-identity-tsv",
                str(fixture["identity"]),
                "--alignment-review-tsv",
                str(fixture["review"]),
                "--output-dir",
                str(output_dir),
                "--source-run-id",
                "unit-low-height-stability-productization",
                "--low-height-reintegration-stable-activation-scope-audit-tsv",
                str(scope_audit),
                "--reintegration-stability-audit-tsv",
                str(stability_audit),
            ],
        )
        == 0
    )

    summary = json.loads(
        (
            output_dir / "standard_peak_backfill_productization_summary.json"
        ).read_text(encoding="utf-8"),
    )
    assert summary["status"] == "pass"
    assert summary["activation_scope_filter_status"] == "applied"
    assert summary["activation_scope_contract"] == (
        "low_height_reintegration_stable_eligible_activation_rows"
    )
    assert summary["activation_scope_filter_selected_shadow_row_count"] == "1"
    assert summary["activation_scope_filter_eligible_audit_row_count"] == "1"
    assert summary["reintegration_stability_audit_tsv"] == str(stability_audit)
    assert len(summary["reintegration_stability_audit_sha256"]) == 64
    assert summary["matrix_cells_written"] == "1"
    assert summary["narrow_product_writer_expected_diff_acceptance_status"] == "pass"
    assert summary["next_action"] == (
        "narrow_low_height_reintegration_stable_backfill_production_ready"
    )

    acceptance = json.loads(
        (
            output_dir / "narrow_product_writer_expected_diff_acceptance.json"
        ).read_text(encoding="utf-8"),
    )
    assert acceptance["acceptance_status"] == "pass"
    assert acceptance["readiness_tier"] == "production_ready"
    assert acceptance["expected_scope"] == (
        "low_height_reintegration_stable_eligible_activation_rows"
    )
    assert acceptance["reintegration_stability_audit_tsv"] == str(stability_audit)
    assert len(acceptance["reintegration_stability_audit_sha256"]) == 64
    assert acceptance["product_written_delta_row_count"] == "1"
    assert acceptance["non_eligible_delta_row_count"] == "0"

    matrix_rows = _read_tsv(output_dir / "activated_matrix" / "alignment_matrix.tsv")
    identity_rows = _read_tsv(
        output_dir / "activated_matrix" / "alignment_matrix_identity.tsv",
    )
    matrix_index_by_hypothesis = {
        row["peak_hypothesis_id"]: int(row["matrix_row_index"]) - 1
        for row in identity_rows
    }
    assert matrix_rows[matrix_index_by_hypothesis["FAM_STD"]]["S2"] == (
        "1.00000e+02"
    )
    assert matrix_rows[matrix_index_by_hypothesis["FAM_NON"]]["S2"] == ""


def test_low_height_stability_scope_filters_rows_not_whole_family(
    tmp_path: Path,
) -> None:
    fixture = _write_fixture(tmp_path)
    _write_tsv(
        fixture["matrix"],
        [
            {"Mz": "300.3", "RT": "9.3", "S1": "10", "S2": "", "S3": ""},
            {"Mz": "301.3", "RT": "9.4", "S1": "20", "S2": "", "S3": ""},
        ],
        ("Mz", "RT", "S1", "S2", "S3"),
    )
    _write_tsv(
        fixture["shadow"],
        [
            _shadow_row(
                "FAM_STD",
                "S2",
                "100",
                standard=True,
                row_sha="a" * 64,
            ),
            _shadow_row(
                "FAM_STD",
                "S3",
                "300",
                standard=True,
                row_sha="d" * 64,
            ),
        ],
        SHADOW_PRODUCTION_PROJECTION_COLUMNS,
    )
    scope_audit = tmp_path / "activation_low_height_stability_scope_audit.tsv"
    _write_tsv(
        scope_audit,
        [
            _scope_audit_row(
                "FAM_STD",
                "S2",
                "a" * 64,
                "ineligible",
                cell_height="500000",
            ),
            _scope_audit_row(
                "FAM_STD",
                "S3",
                "d" * 64,
                "ineligible",
                cell_height="3000000",
            ),
        ],
        (
            *productization_module.ACTIVATION_SCOPE_AUDIT_REQUIRED_COLUMNS,
            "low_scan_clean_status",
            "cell_height",
        ),
    )
    stability_audit = tmp_path / "reintegration_stability_audit.tsv"
    _write_tsv(
        stability_audit,
        [
            _stability_audit_row("FAM_STD", "S2", "a" * 64, "eligible"),
            _stability_audit_row("FAM_STD", "S3", "d" * 64, "eligible"),
        ],
        productization_module.REINTEGRATION_STABILITY_AUDIT_REQUIRED_COLUMNS,
    )
    output_dir = tmp_path / "out"

    assert (
        cli.main(
            [
                "--shadow-projection-cells-tsv",
                str(fixture["shadow"]),
                "--alignment-matrix-tsv",
                str(fixture["matrix"]),
                "--alignment-matrix-identity-tsv",
                str(fixture["identity"]),
                "--alignment-review-tsv",
                str(fixture["review"]),
                "--output-dir",
                str(output_dir),
                "--source-run-id",
                "unit-low-height-stability-family-exact-filter",
                "--low-height-reintegration-stable-activation-scope-audit-tsv",
                str(scope_audit),
                "--reintegration-stability-audit-tsv",
                str(stability_audit),
            ],
        )
        == 0
    )

    acceptance = json.loads(
        (
            output_dir / "narrow_product_writer_expected_diff_acceptance.json"
        ).read_text(encoding="utf-8"),
    )
    assert acceptance["acceptance_status"] == "pass"
    assert acceptance["eligible_audit_row_count"] == "1"
    assert acceptance["product_written_delta_row_count"] == "1"

    matrix_rows = _read_tsv(output_dir / "activated_matrix" / "alignment_matrix.tsv")
    identity_rows = _read_tsv(
        output_dir / "activated_matrix" / "alignment_matrix_identity.tsv",
    )
    matrix_index_by_hypothesis = {
        row["peak_hypothesis_id"]: int(row["matrix_row_index"]) - 1
        for row in identity_rows
    }
    assert matrix_rows[matrix_index_by_hypothesis["FAM_STD"]]["S2"] == (
        "1.00000e+02"
    )
    assert matrix_rows[matrix_index_by_hypothesis["FAM_STD"]]["S3"] == ""


def test_standard_peak_productization_generates_broad_policy_and_writes_ready_rows(
    tmp_path: Path,
) -> None:
    fixture = _write_fixture(tmp_path)
    families = (
        ("FAM_READY", "300.3", "9.3", "100"),
        ("FAM_STABLE", "301.3", "9.4", "150"),
        ("FAM_FLAG", "302.3", "9.5", "175"),
        ("FAM_BLOCK", "303.3", "9.6", "200"),
    )
    _write_tsv(
        fixture["matrix"],
        [{"Mz": mz, "RT": rt, "S1": "10", "S2": ""} for _, mz, rt, _ in families],
        ("Mz", "RT", "S1", "S2"),
    )
    _write_tsv(
        fixture["identity"],
        [
            _identity_row(str(index), mz, rt, family)
            for index, (family, mz, rt, _) in enumerate(families, start=1)
        ],
        (
            "matrix_row_index",
            "Mz",
            "RT",
            "peak_hypothesis_id",
            "row_identity_basis",
            "source_feature_family_ids",
        ),
    )
    _write_tsv(
        fixture["review"],
        [_review_row(family, mz, rt) for family, mz, rt, _ in families],
        ALIGNMENT_REVIEW_COLUMNS,
    )
    _write_tsv(
        fixture["shadow"],
        [
            _shadow_row(family, "S2", value, standard=True, row_sha=sha * 64)
            for (family, _, _, value), sha in zip(
                families,
                ("a", "b", "c", "d"),
                strict=True,
            )
        ],
        SHADOW_PRODUCTION_PROJECTION_COLUMNS,
    )
    source_audit = tmp_path / "broad_activation_scope_audit.tsv"
    _write_tsv(
        source_audit,
        [
            _scope_audit_row(
                "FAM_READY",
                "S2",
                "a" * 64,
                "eligible",
                low_scan_clean_status="ineligible",
                low_height_clean_status="ineligible",
                low_height_low_scan_clean_status="ineligible",
                cell_height="3000000",
                trace_match_status="matched",
            ),
            _scope_audit_row(
                "FAM_STABLE",
                "S2",
                "b" * 64,
                "ineligible",
                low_scan_clean_status="ineligible",
                low_height_clean_status="ineligible",
                low_height_low_scan_clean_status="ineligible",
                cell_height="500000",
                trace_match_status="matched",
            ),
            _scope_audit_row(
                "FAM_FLAG",
                "S2",
                "c" * 64,
                "ineligible",
                low_scan_clean_status="ineligible",
                low_height_clean_status="ineligible",
                low_height_low_scan_clean_status="ineligible",
                cell_height="3000000",
                apex_aligned_shape_similarity="0.96",
                trace_match_status="matched",
            ),
            _scope_audit_row(
                "FAM_BLOCK",
                "S2",
                "d" * 64,
                "ineligible",
                low_scan_clean_status="ineligible",
                low_height_clean_status="ineligible",
                low_height_low_scan_clean_status="ineligible",
                cell_height="3000000",
                trace_match_status="matched",
            ),
        ],
        productization_module.BACKFILL_POLICY_SOURCE_AUDIT_REQUIRED_COLUMNS,
    )
    stability_audit = tmp_path / "reintegration_stability_audit.tsv"
    _write_tsv(
        stability_audit,
        [
            _stability_audit_row("FAM_STABLE", "S2", "b" * 64, "eligible"),
            _stability_audit_row("FAM_FLAG", "S2", "c" * 64, "eligible"),
        ],
        productization_module.REINTEGRATION_STABILITY_AUDIT_REQUIRED_COLUMNS,
    )
    output_dir = tmp_path / "out"

    assert (
        cli.main(
            [
                "--shadow-projection-cells-tsv",
                str(fixture["shadow"]),
                "--alignment-matrix-tsv",
                str(fixture["matrix"]),
                "--alignment-matrix-identity-tsv",
                str(fixture["identity"]),
                "--alignment-review-tsv",
                str(fixture["review"]),
                "--output-dir",
                str(output_dir),
                "--source-run-id",
                "unit-generated-backfill-policy",
                "--backfill-policy-source-audit-tsv",
                str(source_audit),
                "--reintegration-stability-audit-tsv",
                str(stability_audit),
            ],
        )
        == 0
    )

    policy_rows = _read_tsv(output_dir / "standard_peak_backfill_policy.tsv")
    policy_by_family = {row["feature_family_id"]: row for row in policy_rows}
    assert policy_by_family["FAM_READY"]["backfill_policy_decision"] == "write_ready"
    assert policy_by_family["FAM_READY"]["backfill_policy_evidence_class"] == (
        "high_signal_clean"
    )
    assert policy_by_family["FAM_READY"]["backfill_policy_decision_basis"] == (
        "approved_writer_scope"
    )
    assert policy_by_family["FAM_READY"]["backfill_policy_next_evidence"] == (
        "none_current_scope_writer_approved"
    )
    assert policy_by_family["FAM_STABLE"]["backfill_policy_decision"] == "write_ready"
    assert policy_by_family["FAM_STABLE"]["backfill_policy_evidence_class"] == (
        "low_height_reintegration_stable"
    )
    assert (
        policy_by_family["FAM_STABLE"]["backfill_policy_candidate_evidence_class"]
        == "reintegration_stable"
    )
    assert policy_by_family["FAM_FLAG"]["backfill_policy_decision"] == (
        "detected_flagged"
    )
    assert policy_by_family["FAM_FLAG"]["backfill_policy_decision_basis"] == (
        "candidate_signal_without_writer_oracle"
    )
    assert policy_by_family["FAM_FLAG"]["backfill_policy_next_evidence"] == (
        "masked_or_product_writer_oracle_required"
    )
    assert (
        policy_by_family["FAM_FLAG"]["backfill_policy_candidate_evidence_class"]
        == "shape_clean_reintegration_stable,reintegration_stable"
    )
    assert policy_by_family["FAM_FLAG"]["backfill_policy_blockers"] == (
        "missing_writer_approved_oracle"
    )
    assert policy_by_family["FAM_BLOCK"]["backfill_policy_decision"] == "blocked"
    assert policy_by_family["FAM_BLOCK"]["backfill_policy_decision_basis"] == (
        "no_writer_approved_evidence_class"
    )
    assert policy_by_family["FAM_BLOCK"]["backfill_policy_next_evidence"] == (
        "approved_evidence_class_or_passing_oracle_required"
    )
    assert policy_by_family["FAM_BLOCK"]["backfill_policy_blockers"] == (
        "no_writer_approved_evidence_class;"
        "high_signal_clean_status:ineligible;"
        "low_scan_clean_status:ineligible;"
        "low_height_clean_status:ineligible;"
        "low_height_low_scan_clean_status:ineligible;"
        "reintegration_stability_status:missing_or_ineligible"
    )
    explanation_rows = _read_tsv(
        output_dir / "standard_peak_backfill_policy_quality_explanations.tsv",
    )
    assert len(explanation_rows) == len(policy_rows)
    assert {row["feature_family_id"] for row in explanation_rows} == set(
        policy_by_family,
    )
    assert {row["explanation_only"] for row in explanation_rows} == {"TRUE"}

    policy_summary = json.loads(
        (output_dir / "standard_peak_backfill_policy_summary.json").read_text(
            encoding="utf-8",
        ),
    )
    assert policy_summary["policy_row_count"] == "4"
    assert policy_summary["backfill_policy_quality_explanation_row_count"] == "4"
    assert policy_summary["write_ready_row_count"] == "2"
    assert policy_summary["detected_flagged_row_count"] == "1"
    assert policy_summary["blocked_row_count"] == "1"
    assert json.loads(policy_summary["policy_next_evidence_counts_json"]) == {
        "approved_evidence_class_or_passing_oracle_required": 1,
        "masked_or_product_writer_oracle_required": 1,
        "none_current_scope_writer_approved": 2,
    }

    summary = json.loads(
        (
            output_dir / "standard_peak_backfill_productization_summary.json"
        ).read_text(encoding="utf-8"),
    )
    assert summary["status"] == "pass"
    assert summary["activation_scope_contract"] == "backfill_policy_write_ready_rows"
    assert summary["activation_scope_filter_selected_shadow_row_count"] == "2"
    assert summary["matrix_cells_written"] == "2"
    assert summary["next_action"] == "backfill_policy_writer_production_ready"

    acceptance = json.loads(
        (
            output_dir / "narrow_product_writer_expected_diff_acceptance.json"
        ).read_text(encoding="utf-8"),
    )
    assert acceptance["acceptance_status"] == "pass"
    assert acceptance["expected_scope"] == "backfill_policy_write_ready_rows"
    assert acceptance["eligible_audit_row_count"] == "2"
    assert acceptance["product_written_delta_row_count"] == "2"
    assert acceptance["non_eligible_delta_row_count"] == "0"

    matrix_rows = _read_tsv(output_dir / "activated_matrix" / "alignment_matrix.tsv")
    identity_rows = _read_tsv(
        output_dir / "activated_matrix" / "alignment_matrix_identity.tsv",
    )
    matrix_index_by_hypothesis = {
        row["peak_hypothesis_id"]: int(row["matrix_row_index"]) - 1
        for row in identity_rows
    }
    assert matrix_rows[matrix_index_by_hypothesis["FAM_READY"]]["S2"] == (
        "1.00000e+02"
    )
    assert matrix_rows[matrix_index_by_hypothesis["FAM_STABLE"]]["S2"] == (
        "1.50000e+02"
    )
    assert matrix_rows[matrix_index_by_hypothesis["FAM_FLAG"]]["S2"] == ""
    assert matrix_rows[matrix_index_by_hypothesis["FAM_BLOCK"]]["S2"] == ""


def test_generated_backfill_policy_explains_blocked_quality_reasons(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    fixture = _write_fixture(tmp_path)
    source_audit = tmp_path / "broad_activation_scope_audit.tsv"
    blocked_row = _scope_audit_row(
        "FAM_BLOCK",
        "S2",
        "d" * 64,
        "ineligible",
        low_scan_clean_status="ineligible",
        low_height_clean_status="ineligible",
        low_height_low_scan_clean_status="ineligible",
        cell_height="300000",
        apex_aligned_shape_similarity="0.91",
        trace_match_status="matched",
    )
    blocked_row.update(
        {
            "high_signal_clean_blockers": (
                "shape_lt_0.95;height_lt_2000000;"
                "width_outside_0.30_0.65;scan_count_lt_10"
            ),
            "low_scan_clean_blockers": (
                "shape_lt_0.95;height_lt_2000000;"
                "width_outside_0.30_0.65;scan_count_lt_7"
            ),
            "low_height_clean_blockers": (
                "shape_lt_0.95;width_outside_0.30_0.65;scan_count_lt_10"
            ),
            "low_height_low_scan_clean_blockers": (
                "shape_lt_0.95;width_outside_0.30_0.65;scan_count_lt_7"
            ),
        },
    )
    _write_tsv(
        source_audit,
        [blocked_row],
        (
            *productization_module.BACKFILL_POLICY_SOURCE_AUDIT_REQUIRED_COLUMNS,
            *productization_module.BACKFILL_POLICY_SOURCE_QUALITY_BLOCKER_COLUMNS,
        ),
    )
    output_dir = tmp_path / "out"

    assert (
        cli.main(
            [
                "--shadow-projection-cells-tsv",
                str(fixture["shadow"]),
                "--alignment-matrix-tsv",
                str(fixture["matrix"]),
                "--alignment-matrix-identity-tsv",
                str(fixture["identity"]),
                "--alignment-review-tsv",
                str(fixture["review"]),
                "--output-dir",
                str(output_dir),
                "--source-run-id",
                "unit-generated-policy-quality-blockers",
                "--backfill-policy-source-audit-tsv",
                str(source_audit),
            ],
        )
        == 2
    )
    assert "has no eligible written rows" in capsys.readouterr().err

    policy_rows = _read_tsv(output_dir / "standard_peak_backfill_policy.tsv")
    assert policy_rows[0]["backfill_policy_decision"] == "blocked"
    assert policy_rows[0]["backfill_policy_reason"] == (
        "no_writer_approved_evidence_class"
    )
    assert policy_rows[0]["backfill_policy_next_evidence"] == (
        "approved_evidence_class_or_passing_oracle_required"
    )
    assert policy_rows[0]["backfill_policy_blockers"] == (
        "no_writer_approved_evidence_class;"
        "high_signal_clean_status:ineligible;"
        "low_scan_clean_status:ineligible;"
        "low_height_clean_status:ineligible;"
        "low_height_low_scan_clean_status:ineligible;"
        "reintegration_stability_status:missing_or_ineligible"
    )
    explanation_rows = _read_tsv(
        output_dir / "standard_peak_backfill_policy_quality_explanations.tsv",
    )
    assert explanation_rows[0]["explanation_only"] == "TRUE"
    assert explanation_rows[0]["quality_blocker_count"] == "5"
    assert explanation_rows[0]["quality_blockers"] == (
        "shape_lt_0.95;height_lt_2000000;width_outside_0.30_0.65;"
        "scan_count_lt_10;scan_count_lt_7"
    )
    policy_summary = json.loads(
        (output_dir / "standard_peak_backfill_policy_summary.json").read_text(
            encoding="utf-8",
        ),
    )
    assert policy_summary["backfill_policy_quality_explanations_tsv"].endswith(
        "standard_peak_backfill_policy_quality_explanations.tsv",
    )
    assert policy_summary["backfill_policy_quality_explanation_row_count"] == "1"
    assert not (output_dir / "activated_matrix" / "alignment_matrix.tsv").exists()


def test_generated_backfill_policy_blocks_trace_mismatch_clean_status(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    fixture = _write_fixture(tmp_path)
    source_audit = tmp_path / "broad_activation_scope_audit.tsv"
    _write_tsv(
        source_audit,
        [
            _scope_audit_row(
                "FAM_STD",
                "S2",
                "b" * 64,
                "eligible",
                low_scan_clean_status="ineligible",
                low_height_clean_status="ineligible",
                low_height_low_scan_clean_status="ineligible",
                cell_height="3000000",
                trace_match_status="not_matched",
            ),
        ],
        productization_module.BACKFILL_POLICY_SOURCE_AUDIT_REQUIRED_COLUMNS,
    )
    output_dir = tmp_path / "out"

    assert (
        cli.main(
            [
                "--shadow-projection-cells-tsv",
                str(fixture["shadow"]),
                "--alignment-matrix-tsv",
                str(fixture["matrix"]),
                "--alignment-matrix-identity-tsv",
                str(fixture["identity"]),
                "--alignment-review-tsv",
                str(fixture["review"]),
                "--output-dir",
                str(output_dir),
                "--source-run-id",
                "unit-generated-policy-trace-mismatch",
                "--backfill-policy-source-audit-tsv",
                str(source_audit),
            ],
        )
        == 2
    )
    assert "has no eligible written rows" in capsys.readouterr().err

    policy_rows = _read_tsv(output_dir / "standard_peak_backfill_policy.tsv")
    assert policy_rows[0]["backfill_policy_decision"] == "blocked"
    assert policy_rows[0]["backfill_policy_reason"] == "missing_trace_evidence"
    assert policy_rows[0]["backfill_policy_decision_basis"] == (
        "missing_trace_evidence"
    )
    assert policy_rows[0]["backfill_policy_candidate_evidence_class"] == (
        "high_signal_clean"
    )
    assert policy_rows[0]["backfill_policy_next_evidence"] == (
        "trace_overlay_or_reintegration_evidence_required"
    )
    assert policy_rows[0]["backfill_policy_blockers"] == (
        "missing_trace_evidence;"
        "trace_match_status:not_matched;"
        "approved_evidence_classes_require_matched_trace"
    )
    assert not (output_dir / "activated_matrix" / "alignment_matrix.tsv").exists()


def test_generated_backfill_policy_shape_clean_requires_trace_match(
    tmp_path: Path,
) -> None:
    fixture = _write_fixture(tmp_path)
    source_audit = tmp_path / "broad_activation_scope_audit.tsv"
    _write_tsv(
        source_audit,
        [
            _scope_audit_row(
                "FAM_STD",
                "S2",
                "b" * 64,
                "eligible",
                low_scan_clean_status="ineligible",
                low_height_clean_status="ineligible",
                low_height_low_scan_clean_status="ineligible",
                cell_height="3000000",
                apex_aligned_shape_similarity="0.96",
                trace_match_status="not_matched",
            ),
        ],
        productization_module.BACKFILL_POLICY_SOURCE_AUDIT_REQUIRED_COLUMNS,
    )
    stability_audit = tmp_path / "reintegration_stability_audit.tsv"
    _write_tsv(
        stability_audit,
        [_stability_audit_row("FAM_STD", "S2", "b" * 64, "eligible")],
        productization_module.REINTEGRATION_STABILITY_AUDIT_REQUIRED_COLUMNS,
    )
    output_dir = tmp_path / "out"

    assert (
        cli.main(
            [
                "--shadow-projection-cells-tsv",
                str(fixture["shadow"]),
                "--alignment-matrix-tsv",
                str(fixture["matrix"]),
                "--alignment-matrix-identity-tsv",
                str(fixture["identity"]),
                "--alignment-review-tsv",
                str(fixture["review"]),
                "--output-dir",
                str(output_dir),
                "--source-run-id",
                "unit-generated-policy-shape-clean-trace-guard",
                "--backfill-policy-source-audit-tsv",
                str(source_audit),
                "--reintegration-stability-audit-tsv",
                str(stability_audit),
            ],
        )
        == 2
    )

    policy_rows = _read_tsv(output_dir / "standard_peak_backfill_policy.tsv")
    assert policy_rows[0]["backfill_policy_decision"] == "detected_flagged"
    assert policy_rows[0]["backfill_policy_evidence_class"] == ""
    assert policy_rows[0]["ready_evidence_classes"] == ""
    assert policy_rows[0]["backfill_policy_candidate_evidence_class"] == (
        "high_signal_clean,reintegration_stable"
    )
    assert not (output_dir / "activated_matrix" / "alignment_matrix.tsv").exists()


def test_generated_backfill_policy_shape_clean_requires_similarity_threshold(
    tmp_path: Path,
) -> None:
    fixture = _write_fixture(tmp_path)
    source_audit = tmp_path / "broad_activation_scope_audit.tsv"
    _write_tsv(
        source_audit,
        [
            _scope_audit_row(
                "FAM_STD",
                "S2",
                "b" * 64,
                "ineligible",
                low_scan_clean_status="ineligible",
                low_height_clean_status="ineligible",
                low_height_low_scan_clean_status="ineligible",
                cell_height="3000000",
                apex_aligned_shape_similarity="0.94",
                trace_match_status="matched",
            ),
        ],
        productization_module.BACKFILL_POLICY_SOURCE_AUDIT_REQUIRED_COLUMNS,
    )
    stability_audit = tmp_path / "reintegration_stability_audit.tsv"
    _write_tsv(
        stability_audit,
        [_stability_audit_row("FAM_STD", "S2", "b" * 64, "eligible")],
        productization_module.REINTEGRATION_STABILITY_AUDIT_REQUIRED_COLUMNS,
    )
    output_dir = tmp_path / "out"

    assert (
        cli.main(
            [
                "--shadow-projection-cells-tsv",
                str(fixture["shadow"]),
                "--alignment-matrix-tsv",
                str(fixture["matrix"]),
                "--alignment-matrix-identity-tsv",
                str(fixture["identity"]),
                "--alignment-review-tsv",
                str(fixture["review"]),
                "--output-dir",
                str(output_dir),
                "--source-run-id",
                "unit-generated-policy-shape-clean-threshold",
                "--backfill-policy-source-audit-tsv",
                str(source_audit),
                "--reintegration-stability-audit-tsv",
                str(stability_audit),
            ],
        )
        == 2
    )

    policy_rows = _read_tsv(output_dir / "standard_peak_backfill_policy.tsv")
    assert policy_rows[0]["backfill_policy_decision"] == "detected_flagged"
    assert policy_rows[0]["backfill_policy_candidate_evidence_class"] == (
        "reintegration_stable"
    )
    assert not (output_dir / "activated_matrix" / "alignment_matrix.tsv").exists()


def test_generated_backfill_policy_keeps_shape_clean_stability_review_only(
    tmp_path: Path,
) -> None:
    fixture = _write_fixture(tmp_path)
    source_audit = tmp_path / "broad_activation_scope_audit.tsv"
    _write_tsv(
        source_audit,
        [
            _scope_audit_row(
                "FAM_STD",
                "S2",
                "b" * 64,
                "ineligible",
                low_scan_clean_status="ineligible",
                low_height_clean_status="ineligible",
                low_height_low_scan_clean_status="ineligible",
                cell_height="3000000",
                apex_aligned_shape_similarity="0.96",
                trace_match_status="matched",
            ),
        ],
        productization_module.BACKFILL_POLICY_SOURCE_AUDIT_REQUIRED_COLUMNS,
    )
    stability_audit = tmp_path / "reintegration_stability_audit.tsv"
    _write_tsv(
        stability_audit,
        [_stability_audit_row("FAM_STD", "S2", "b" * 64, "eligible")],
        productization_module.REINTEGRATION_STABILITY_AUDIT_REQUIRED_COLUMNS,
    )
    output_dir = tmp_path / "out"

    assert (
        cli.main(
            [
                "--shadow-projection-cells-tsv",
                str(fixture["shadow"]),
                "--alignment-matrix-tsv",
                str(fixture["matrix"]),
                "--alignment-matrix-identity-tsv",
                str(fixture["identity"]),
                "--alignment-review-tsv",
                str(fixture["review"]),
                "--output-dir",
                str(output_dir),
                "--source-run-id",
                "unit-generated-policy-shape-clean-review-only",
                "--backfill-policy-source-audit-tsv",
                str(source_audit),
                "--reintegration-stability-audit-tsv",
                str(stability_audit),
            ],
        )
        == 2
    )

    policy_rows = _read_tsv(output_dir / "standard_peak_backfill_policy.tsv")
    assert policy_rows[0]["backfill_policy_decision"] == "detected_flagged"
    assert policy_rows[0]["backfill_policy_authority_status"] == "review_only"
    assert policy_rows[0]["backfill_policy_evidence_class"] == ""
    assert policy_rows[0]["ready_evidence_classes"] == ""
    assert policy_rows[0]["backfill_policy_candidate_evidence_class"] == (
        "shape_clean_reintegration_stable,reintegration_stable"
    )
    assert not (output_dir / "activated_matrix" / "alignment_matrix.tsv").exists()


def test_generated_backfill_policy_uses_policy_observed_oracle_for_write_ready(
    tmp_path: Path,
) -> None:
    fixture = _write_fixture(tmp_path)
    _write_tsv(
        fixture["shadow"],
        [
            _shadow_row("FAM_STD", "S2", "100", standard=True, row_sha="b" * 64),
            _shadow_row("FAM_NON", "S2", "200", standard=True, row_sha="c" * 64),
        ],
        SHADOW_PRODUCTION_PROJECTION_COLUMNS,
    )
    source_audit = tmp_path / "broad_activation_scope_audit.tsv"
    source_sha = "b" * 64
    _write_tsv(
        source_audit,
        [
            _scope_audit_row(
                "FAM_STD",
                "S2",
                source_sha,
                "ineligible",
                low_scan_clean_status="ineligible",
                low_height_clean_status="ineligible",
                low_height_low_scan_clean_status="ineligible",
                cell_height="3000000",
                apex_aligned_shape_similarity="0.96",
                trace_match_status="matched",
            ),
        ],
        productization_module.BACKFILL_POLICY_SOURCE_AUDIT_REQUIRED_COLUMNS,
    )
    stability_audit = tmp_path / "reintegration_stability_audit.tsv"
    _write_tsv(
        stability_audit,
        [_stability_audit_row("FAM_STD", "S2", source_sha, "eligible")],
        productization_module.REINTEGRATION_STABILITY_AUDIT_REQUIRED_COLUMNS,
    )
    policy_oracle = tmp_path / "standard_peak_policy_observed_oracle.tsv"
    _write_tsv(
        policy_oracle,
        [_policy_observed_oracle_row("FAM_STD", "S2", source_sha)],
        productization_module.POLICY_OBSERVED_ORACLE_REQUIRED_COLUMNS,
    )
    base_policy = _write_generated_policy_base(
        fixture=fixture,
        tmp_path=tmp_path,
        source_audit=source_audit,
        stability_audit=stability_audit,
    )
    policy_oracle_summary = tmp_path / "policy_observed_oracle_summary.json"
    _write_policy_observed_oracle_summary(
        policy_oracle_summary,
        policy_oracle_tsv=policy_oracle,
        source_audit_tsv=source_audit,
        source_policy_tsv=base_policy,
    )
    output_dir = tmp_path / "out"

    assert (
        cli.main(
            [
                "--shadow-projection-cells-tsv",
                str(fixture["shadow"]),
                "--alignment-matrix-tsv",
                str(fixture["matrix"]),
                "--alignment-matrix-identity-tsv",
                str(fixture["identity"]),
                "--alignment-review-tsv",
                str(fixture["review"]),
                "--output-dir",
                str(output_dir),
                "--source-run-id",
                "unit-generated-policy-observed-oracle-write-ready",
                "--backfill-policy-source-audit-tsv",
                str(source_audit),
                "--reintegration-stability-audit-tsv",
                str(stability_audit),
                "--policy-observed-oracle-tsv",
                str(policy_oracle),
                "--policy-observed-oracle-summary-json",
                str(policy_oracle_summary),
            ],
        )
        == 0
    )

    policy_rows = _read_tsv(output_dir / "standard_peak_backfill_policy.tsv")
    assert policy_rows[0]["backfill_policy_decision"] == "write_ready"
    assert policy_rows[0]["backfill_policy_evidence_class"] == (
        "policy_observed_full_trace_reintegration"
    )
    assert policy_rows[0]["backfill_policy_authority_status"] == "writer_approved"
    assert policy_rows[0]["backfill_policy_decision_basis"] == (
        "approved_writer_scope"
    )
    assert policy_rows[0]["backfill_policy_next_evidence"] == (
        "none_current_scope_writer_approved"
    )
    assert policy_rows[0]["backfill_policy_candidate_evidence_class"] == (
        "shape_clean_reintegration_stable,reintegration_stable"
    )

    summary = json.loads(
        (
            output_dir / "standard_peak_backfill_productization_summary.json"
        ).read_text(encoding="utf-8"),
    )
    assert summary["status"] == "pass"
    assert summary["activation_scope_contract"] == "backfill_policy_write_ready_rows"
    assert summary["matrix_cells_written"] == "1"
    assert summary["next_action"] == "backfill_policy_writer_production_ready"

    acceptance = json.loads(
        (
            output_dir / "narrow_product_writer_expected_diff_acceptance.json"
        ).read_text(encoding="utf-8"),
    )
    assert acceptance["acceptance_status"] == "pass"
    assert acceptance["eligible_audit_row_count"] == "1"
    assert acceptance["product_written_delta_row_count"] == "1"

    matrix_rows = _read_tsv(output_dir / "activated_matrix" / "alignment_matrix.tsv")
    identity_rows = _read_tsv(
        output_dir / "activated_matrix" / "alignment_matrix_identity.tsv",
    )
    matrix_index_by_hypothesis = {
        row["peak_hypothesis_id"]: int(row["matrix_row_index"]) - 1
        for row in identity_rows
    }
    assert matrix_rows[matrix_index_by_hypothesis["FAM_STD"]]["S2"] == (
        "1.00000e+02"
    )


def test_generated_backfill_policy_rejects_failed_policy_observed_oracle(
    tmp_path: Path,
) -> None:
    fixture = _write_fixture(tmp_path)
    _write_tsv(
        fixture["shadow"],
        [
            _shadow_row("FAM_STD", "S2", "100", standard=True, row_sha="b" * 64),
            _shadow_row("FAM_NON", "S2", "200", standard=True, row_sha="c" * 64),
        ],
        SHADOW_PRODUCTION_PROJECTION_COLUMNS,
    )
    source_audit = tmp_path / "broad_activation_scope_audit.tsv"
    source_sha = "b" * 64
    _write_tsv(
        source_audit,
        [
            _scope_audit_row(
                "FAM_STD",
                "S2",
                source_sha,
                "ineligible",
                low_scan_clean_status="ineligible",
                low_height_clean_status="ineligible",
                low_height_low_scan_clean_status="ineligible",
                cell_height="3000000",
                apex_aligned_shape_similarity="0.96",
                trace_match_status="matched",
            ),
        ],
        productization_module.BACKFILL_POLICY_SOURCE_AUDIT_REQUIRED_COLUMNS,
    )
    stability_audit = tmp_path / "reintegration_stability_audit.tsv"
    _write_tsv(
        stability_audit,
        [_stability_audit_row("FAM_STD", "S2", source_sha, "eligible")],
        productization_module.REINTEGRATION_STABILITY_AUDIT_REQUIRED_COLUMNS,
    )
    policy_oracle = tmp_path / "standard_peak_policy_observed_oracle.tsv"
    _write_tsv(
        policy_oracle,
        [
            _policy_observed_oracle_row(
                "FAM_STD",
                "S2",
                source_sha,
                oracle_case_status="fail_area",
                included_in_product_acceptance="FALSE",
            ),
        ],
        productization_module.POLICY_OBSERVED_ORACLE_REQUIRED_COLUMNS,
    )
    base_policy = _write_generated_policy_base(
        fixture=fixture,
        tmp_path=tmp_path,
        source_audit=source_audit,
        stability_audit=stability_audit,
    )
    policy_oracle_summary = tmp_path / "policy_observed_oracle_summary.json"
    _write_policy_observed_oracle_summary(
        policy_oracle_summary,
        policy_oracle_tsv=policy_oracle,
        source_audit_tsv=source_audit,
        source_policy_tsv=base_policy,
        status="fail",
        accepted_count=0,
    )
    output_dir = tmp_path / "out"

    assert (
        cli.main(
            [
                "--shadow-projection-cells-tsv",
                str(fixture["shadow"]),
                "--alignment-matrix-tsv",
                str(fixture["matrix"]),
                "--alignment-matrix-identity-tsv",
                str(fixture["identity"]),
                "--alignment-review-tsv",
                str(fixture["review"]),
                "--output-dir",
                str(output_dir),
                "--source-run-id",
                "unit-generated-policy-observed-oracle-fail-closed",
                "--backfill-policy-source-audit-tsv",
                str(source_audit),
                "--reintegration-stability-audit-tsv",
                str(stability_audit),
                "--policy-observed-oracle-tsv",
                str(policy_oracle),
                "--policy-observed-oracle-summary-json",
                str(policy_oracle_summary),
            ],
        )
        == 2
    )

    assert not (output_dir / "standard_peak_backfill_policy.tsv").exists()
    assert not (output_dir / "activated_matrix" / "alignment_matrix.tsv").exists()


def test_generated_backfill_policy_rejects_observed_oracle_without_summary(
    tmp_path: Path,
) -> None:
    fixture = _write_fixture(tmp_path)
    source_audit = tmp_path / "broad_activation_scope_audit.tsv"
    source_sha = "b" * 64
    _write_tsv(
        source_audit,
        [
            _scope_audit_row(
                "FAM_STD",
                "S2",
                source_sha,
                "ineligible",
                low_scan_clean_status="ineligible",
                low_height_clean_status="ineligible",
                low_height_low_scan_clean_status="ineligible",
                cell_height="3000000",
                apex_aligned_shape_similarity="0.96",
                trace_match_status="matched",
            ),
        ],
        productization_module.BACKFILL_POLICY_SOURCE_AUDIT_REQUIRED_COLUMNS,
    )
    stability_audit = tmp_path / "reintegration_stability_audit.tsv"
    _write_tsv(
        stability_audit,
        [_stability_audit_row("FAM_STD", "S2", source_sha, "eligible")],
        productization_module.REINTEGRATION_STABILITY_AUDIT_REQUIRED_COLUMNS,
    )
    policy_oracle = tmp_path / "standard_peak_policy_observed_oracle.tsv"
    _write_tsv(
        policy_oracle,
        [_policy_observed_oracle_row("FAM_STD", "S2", source_sha)],
        productization_module.POLICY_OBSERVED_ORACLE_REQUIRED_COLUMNS,
    )
    output_dir = tmp_path / "out"

    assert (
        cli.main(
            [
                "--shadow-projection-cells-tsv",
                str(fixture["shadow"]),
                "--alignment-matrix-tsv",
                str(fixture["matrix"]),
                "--alignment-matrix-identity-tsv",
                str(fixture["identity"]),
                "--alignment-review-tsv",
                str(fixture["review"]),
                "--output-dir",
                str(output_dir),
                "--source-run-id",
                "unit-generated-policy-observed-oracle-missing-summary",
                "--backfill-policy-source-audit-tsv",
                str(source_audit),
                "--reintegration-stability-audit-tsv",
                str(stability_audit),
                "--policy-observed-oracle-tsv",
                str(policy_oracle),
            ],
        )
        == 2
    )

    assert not (output_dir / "standard_peak_backfill_policy.tsv").exists()
    assert not (output_dir / "activated_matrix" / "alignment_matrix.tsv").exists()


def test_generated_backfill_policy_rejects_partial_policy_observed_oracle(
    tmp_path: Path,
) -> None:
    fixture = _write_fixture(tmp_path)
    _write_tsv(
        fixture["shadow"],
        [
            _shadow_row("FAM_STD", "S2", "100", standard=True, row_sha="b" * 64),
            _shadow_row("FAM_NON", "S2", "200", standard=True, row_sha="c" * 64),
        ],
        SHADOW_PRODUCTION_PROJECTION_COLUMNS,
    )
    source_audit = tmp_path / "broad_activation_scope_audit.tsv"
    _write_tsv(
        source_audit,
        [
            _scope_audit_row(
                "FAM_STD",
                "S2",
                "b" * 64,
                "ineligible",
                low_scan_clean_status="ineligible",
                low_height_clean_status="ineligible",
                low_height_low_scan_clean_status="ineligible",
                cell_height="3000000",
                apex_aligned_shape_similarity="0.96",
                trace_match_status="matched",
            ),
            _scope_audit_row(
                "FAM_NON",
                "S2",
                "c" * 64,
                "ineligible",
                low_scan_clean_status="ineligible",
                low_height_clean_status="ineligible",
                low_height_low_scan_clean_status="ineligible",
                cell_height="3000000",
                apex_aligned_shape_similarity="0.96",
                trace_match_status="matched",
            ),
        ],
        productization_module.BACKFILL_POLICY_SOURCE_AUDIT_REQUIRED_COLUMNS,
    )
    stability_audit = tmp_path / "reintegration_stability_audit.tsv"
    _write_tsv(
        stability_audit,
        [
            _stability_audit_row("FAM_STD", "S2", "b" * 64, "eligible"),
            _stability_audit_row("FAM_NON", "S2", "c" * 64, "eligible"),
        ],
        productization_module.REINTEGRATION_STABILITY_AUDIT_REQUIRED_COLUMNS,
    )
    base_policy = _write_generated_policy_base(
        fixture=fixture,
        tmp_path=tmp_path,
        source_audit=source_audit,
        stability_audit=stability_audit,
    )
    policy_oracle = tmp_path / "standard_peak_policy_observed_oracle.tsv"
    _write_tsv(
        policy_oracle,
        [_policy_observed_oracle_row("FAM_STD", "S2", "b" * 64)],
        productization_module.POLICY_OBSERVED_ORACLE_REQUIRED_COLUMNS,
    )
    policy_oracle_summary = tmp_path / "policy_observed_oracle_summary.json"
    _write_policy_observed_oracle_summary(
        policy_oracle_summary,
        policy_oracle_tsv=policy_oracle,
        source_audit_tsv=source_audit,
        source_policy_tsv=base_policy,
    )
    output_dir = tmp_path / "out"

    assert (
        cli.main(
            [
                "--shadow-projection-cells-tsv",
                str(fixture["shadow"]),
                "--alignment-matrix-tsv",
                str(fixture["matrix"]),
                "--alignment-matrix-identity-tsv",
                str(fixture["identity"]),
                "--alignment-review-tsv",
                str(fixture["review"]),
                "--output-dir",
                str(output_dir),
                "--source-run-id",
                "unit-generated-policy-observed-oracle-partial",
                "--backfill-policy-source-audit-tsv",
                str(source_audit),
                "--reintegration-stability-audit-tsv",
                str(stability_audit),
                "--policy-observed-oracle-tsv",
                str(policy_oracle),
                "--policy-observed-oracle-summary-json",
                str(policy_oracle_summary),
            ],
        )
        == 2
    )

    assert not (output_dir / "standard_peak_backfill_policy.tsv").exists()
    assert not (output_dir / "activated_matrix" / "alignment_matrix.tsv").exists()


def test_generated_backfill_policy_rejects_mismatched_policy_observed_oracle(
    tmp_path: Path,
) -> None:
    fixture = _write_fixture(tmp_path)
    _write_tsv(
        fixture["shadow"],
        [
            _shadow_row("FAM_STD", "S2", "100", standard=True, row_sha="b" * 64),
            _shadow_row("FAM_NON", "S2", "200", standard=True, row_sha="c" * 64),
        ],
        SHADOW_PRODUCTION_PROJECTION_COLUMNS,
    )
    source_audit = tmp_path / "broad_activation_scope_audit.tsv"
    source_sha = "b" * 64
    _write_tsv(
        source_audit,
        [
            _scope_audit_row(
                "FAM_STD",
                "S2",
                source_sha,
                "ineligible",
                low_scan_clean_status="ineligible",
                low_height_clean_status="ineligible",
                low_height_low_scan_clean_status="ineligible",
                cell_height="3000000",
                apex_aligned_shape_similarity="0.96",
                trace_match_status="matched",
            ),
        ],
        productization_module.BACKFILL_POLICY_SOURCE_AUDIT_REQUIRED_COLUMNS,
    )
    stability_audit = tmp_path / "reintegration_stability_audit.tsv"
    _write_tsv(
        stability_audit,
        [_stability_audit_row("FAM_STD", "S2", source_sha, "eligible")],
        productization_module.REINTEGRATION_STABILITY_AUDIT_REQUIRED_COLUMNS,
    )
    policy_oracle = tmp_path / "standard_peak_policy_observed_oracle.tsv"
    _write_tsv(
        policy_oracle,
        [_policy_observed_oracle_row("FAM_OTHER", "S2", source_sha)],
        productization_module.POLICY_OBSERVED_ORACLE_REQUIRED_COLUMNS,
    )
    base_policy = _write_generated_policy_base(
        fixture=fixture,
        tmp_path=tmp_path,
        source_audit=source_audit,
        stability_audit=stability_audit,
    )
    policy_oracle_summary = tmp_path / "policy_observed_oracle_summary.json"
    _write_policy_observed_oracle_summary(
        policy_oracle_summary,
        policy_oracle_tsv=policy_oracle,
        source_audit_tsv=source_audit,
        source_policy_tsv=base_policy,
    )
    output_dir = tmp_path / "out"

    assert (
        cli.main(
            [
                "--shadow-projection-cells-tsv",
                str(fixture["shadow"]),
                "--alignment-matrix-tsv",
                str(fixture["matrix"]),
                "--alignment-matrix-identity-tsv",
                str(fixture["identity"]),
                "--alignment-review-tsv",
                str(fixture["review"]),
                "--output-dir",
                str(output_dir),
                "--source-run-id",
                "unit-generated-policy-observed-oracle-mismatch",
                "--backfill-policy-source-audit-tsv",
                str(source_audit),
                "--reintegration-stability-audit-tsv",
                str(stability_audit),
                "--policy-observed-oracle-tsv",
                str(policy_oracle),
                "--policy-observed-oracle-summary-json",
                str(policy_oracle_summary),
            ],
        )
        == 2
    )

    assert not (output_dir / "standard_peak_backfill_policy.tsv").exists()
    assert not (output_dir / "activated_matrix" / "alignment_matrix.tsv").exists()


def test_standard_peak_productization_public_api_rejects_manual_policy_tsv(
    tmp_path: Path,
) -> None:
    fixture = _write_fixture(tmp_path)
    run_productization = productization_module.run_standard_peak_backfill_productization

    with pytest.raises(TypeError, match="activation_policy_tsv"):
        run_productization(
            shadow_projection_cells_tsv=fixture["shadow"],
            alignment_matrix_tsv=fixture["matrix"],
            alignment_matrix_identity_tsv=fixture["identity"],
            alignment_review_tsv=fixture["review"],
            output_dir=tmp_path / "out",
            source_run_id="unit-manual-policy-rejected",
            activation_policy_tsv=tmp_path / "manual_policy.tsv",
        )


def test_standard_peak_productization_rejects_wrong_activation_scope_schema(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    fixture = _write_fixture(tmp_path)
    scope_audit = tmp_path / "activation_low_height_stability_scope_audit.tsv"
    wrong_schema_row = _scope_audit_row(
        "FAM_STD",
        "S2",
        "b" * 64,
        "ineligible",
        cell_height="500000",
    )
    wrong_schema_row["schema_version"] = "wrong_activation_scope_schema"
    _write_tsv(
        scope_audit,
        [wrong_schema_row],
        (
            *productization_module.ACTIVATION_SCOPE_AUDIT_REQUIRED_COLUMNS,
            "low_scan_clean_status",
            "cell_height",
        ),
    )
    stability_audit = tmp_path / "reintegration_stability_audit.tsv"
    _write_tsv(
        stability_audit,
        [_stability_audit_row("FAM_STD", "S2", "b" * 64, "eligible")],
        productization_module.REINTEGRATION_STABILITY_AUDIT_REQUIRED_COLUMNS,
    )

    assert (
        cli.main(
            [
                "--shadow-projection-cells-tsv",
                str(fixture["shadow"]),
                "--alignment-matrix-tsv",
                str(fixture["matrix"]),
                "--alignment-matrix-identity-tsv",
                str(fixture["identity"]),
                "--alignment-review-tsv",
                str(fixture["review"]),
                "--output-dir",
                str(tmp_path / "out"),
                "--source-run-id",
                "unit-low-height-stability-wrong-activation-schema",
                "--low-height-reintegration-stable-activation-scope-audit-tsv",
                str(scope_audit),
                "--reintegration-stability-audit-tsv",
                str(stability_audit),
            ],
        )
        == 2
    )

    assert "expected schema_version standard_peak_activation_scope_audit_v1" in (
        capsys.readouterr().err
    )


def test_standard_peak_productization_rejects_wrong_stability_schema(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    fixture = _write_fixture(tmp_path)
    scope_audit = tmp_path / "activation_low_height_stability_scope_audit.tsv"
    _write_tsv(
        scope_audit,
        [
            _scope_audit_row(
                "FAM_STD",
                "S2",
                "b" * 64,
                "ineligible",
                cell_height="500000",
            ),
        ],
        (
            *productization_module.ACTIVATION_SCOPE_AUDIT_REQUIRED_COLUMNS,
            "low_scan_clean_status",
            "cell_height",
        ),
    )
    stability_row = _stability_audit_row(
        "FAM_STD",
        "S2",
        "b" * 64,
        "eligible",
    )
    stability_row["schema_version"] = "wrong_stability_schema"
    stability_audit = tmp_path / "reintegration_stability_audit.tsv"
    _write_tsv(
        stability_audit,
        [stability_row],
        productization_module.REINTEGRATION_STABILITY_AUDIT_REQUIRED_COLUMNS,
    )

    assert (
        cli.main(
            [
                "--shadow-projection-cells-tsv",
                str(fixture["shadow"]),
                "--alignment-matrix-tsv",
                str(fixture["matrix"]),
                "--alignment-matrix-identity-tsv",
                str(fixture["identity"]),
                "--alignment-review-tsv",
                str(fixture["review"]),
                "--output-dir",
                str(tmp_path / "out"),
                "--source-run-id",
                "unit-low-height-stability-wrong-stability-schema",
                "--low-height-reintegration-stable-activation-scope-audit-tsv",
                str(scope_audit),
                "--reintegration-stability-audit-tsv",
                str(stability_audit),
            ],
        )
        == 2
    )

    assert (
        "expected schema_version standard_peak_reintegration_stability_audit_v1"
        in capsys.readouterr().err
    )


@pytest.mark.parametrize(
    "second_scope_flag",
    (
        "--low-scan-clean-activation-scope-audit-tsv",
        "--low-height-clean-activation-scope-audit-tsv",
        "--low-height-low-scan-clean-activation-scope-audit-tsv",
    ),
)
def test_standard_peak_productization_rejects_multiple_activation_scope_audits(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    second_scope_flag: str,
) -> None:
    fixture = _write_fixture(tmp_path)
    scope_audit = tmp_path / "activation_scope_audit.tsv"
    _write_tsv(
        scope_audit,
        [_scope_audit_row("FAM_STD", "S2", "b" * 64, "eligible")],
        (
            *productization_module.ACTIVATION_SCOPE_AUDIT_REQUIRED_COLUMNS,
            "low_scan_clean_status",
            "low_height_clean_status",
            "low_height_low_scan_clean_status",
        ),
    )

    assert (
        cli.main(
            [
                "--shadow-projection-cells-tsv",
                str(fixture["shadow"]),
                "--alignment-matrix-tsv",
                str(fixture["matrix"]),
                "--alignment-matrix-identity-tsv",
                str(fixture["identity"]),
                "--alignment-review-tsv",
                str(fixture["review"]),
                "--output-dir",
                str(tmp_path / "out"),
                "--source-run-id",
                "unit-multiple-scope-audits",
                "--high-signal-clean-activation-scope-audit-tsv",
                str(scope_audit),
                second_scope_flag,
                str(scope_audit),
            ],
        )
        == 2
    )

    assert "only one activation scope audit may be provided" in capsys.readouterr().err


def test_standard_peak_productization_rejects_empty_low_scan_scope(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    fixture = _write_fixture(tmp_path)
    scope_audit = tmp_path / "activation_low_scan_clean_scope_audit.tsv"
    _write_tsv(
        scope_audit,
        [
            _scope_audit_row(
                "FAM_STD",
                "S2",
                "b" * 64,
                "ineligible",
                low_scan_clean_status="ineligible",
            ),
        ],
        (
            *productization_module.ACTIVATION_SCOPE_AUDIT_REQUIRED_COLUMNS,
            "low_scan_clean_status",
        ),
    )

    assert (
        cli.main(
            [
                "--shadow-projection-cells-tsv",
                str(fixture["shadow"]),
                "--alignment-matrix-tsv",
                str(fixture["matrix"]),
                "--alignment-matrix-identity-tsv",
                str(fixture["identity"]),
                "--alignment-review-tsv",
                str(fixture["review"]),
                "--output-dir",
                str(tmp_path / "out"),
                "--source-run-id",
                "unit-empty-low-scan-scope",
                "--low-scan-clean-activation-scope-audit-tsv",
                str(scope_audit),
            ],
        )
        == 2
    )

    assert "low-scan-clean activation scope audit has no eligible written rows" in (
        capsys.readouterr().err
    )


def test_standard_peak_productization_rejects_duplicate_low_scan_scope_sha(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    fixture = _write_fixture(tmp_path)
    scope_audit = tmp_path / "activation_low_scan_clean_scope_audit.tsv"
    _write_tsv(
        scope_audit,
        [
            _scope_audit_row(
                "FAM_STD",
                "S2",
                "b" * 64,
                "ineligible",
                low_scan_clean_status="eligible",
            ),
            _scope_audit_row(
                "FAM_STD_DUP",
                "S2",
                "b" * 64,
                "ineligible",
                low_scan_clean_status="eligible",
            ),
        ],
        (
            *productization_module.ACTIVATION_SCOPE_AUDIT_REQUIRED_COLUMNS,
            "low_scan_clean_status",
        ),
    )

    assert (
        cli.main(
            [
                "--shadow-projection-cells-tsv",
                str(fixture["shadow"]),
                "--alignment-matrix-tsv",
                str(fixture["matrix"]),
                "--alignment-matrix-identity-tsv",
                str(fixture["identity"]),
                "--alignment-review-tsv",
                str(fixture["review"]),
                "--output-dir",
                str(tmp_path / "out"),
                "--source-run-id",
                "unit-duplicate-low-scan-scope-sha",
                "--low-scan-clean-activation-scope-audit-tsv",
                str(scope_audit),
            ],
        )
        == 2
    )

    assert "duplicate eligible matrix_value_source_row_sha256 values" in (
        capsys.readouterr().err
    )


def test_standard_peak_productization_rejects_low_scan_scope_missing_shadow_sha(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    fixture = _write_fixture(tmp_path)
    scope_audit = tmp_path / "activation_low_scan_clean_scope_audit.tsv"
    _write_tsv(
        scope_audit,
        [
            _scope_audit_row(
                "FAM_STD",
                "S2",
                "d" * 64,
                "ineligible",
                low_scan_clean_status="eligible",
            ),
        ],
        (
            *productization_module.ACTIVATION_SCOPE_AUDIT_REQUIRED_COLUMNS,
            "low_scan_clean_status",
        ),
    )

    assert (
        cli.main(
            [
                "--shadow-projection-cells-tsv",
                str(fixture["shadow"]),
                "--alignment-matrix-tsv",
                str(fixture["matrix"]),
                "--alignment-matrix-identity-tsv",
                str(fixture["identity"]),
                "--alignment-review-tsv",
                str(fixture["review"]),
                "--output-dir",
                str(tmp_path / "out"),
                "--source-run-id",
                "unit-low-scan-scope-missing-shadow-sha",
                "--low-scan-clean-activation-scope-audit-tsv",
                str(scope_audit),
            ],
        )
        == 2
    )

    assert "references missing shadow_projection_row_sha256 values" in (
        capsys.readouterr().err
    )


def test_standard_peak_productization_rejects_duplicate_shadow_scope_sha(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    fixture = _write_fixture(tmp_path)
    _write_tsv(
        fixture["shadow"],
        [
            _shadow_row(
                "FAM_STD",
                "S2",
                "100",
                standard=True,
                row_sha="b" * 64,
            ),
            _shadow_row(
                "FAM_NON",
                "S2",
                "200",
                standard=True,
                row_sha="b" * 64,
            ),
        ],
        SHADOW_PRODUCTION_PROJECTION_COLUMNS,
    )
    scope_audit = tmp_path / "activation_low_scan_clean_scope_audit.tsv"
    _write_tsv(
        scope_audit,
        [
            _scope_audit_row(
                "FAM_STD",
                "S2",
                "b" * 64,
                "ineligible",
                low_scan_clean_status="eligible",
            ),
        ],
        (
            *productization_module.ACTIVATION_SCOPE_AUDIT_REQUIRED_COLUMNS,
            "low_scan_clean_status",
        ),
    )

    assert (
        cli.main(
            [
                "--shadow-projection-cells-tsv",
                str(fixture["shadow"]),
                "--alignment-matrix-tsv",
                str(fixture["matrix"]),
                "--alignment-matrix-identity-tsv",
                str(fixture["identity"]),
                "--alignment-review-tsv",
                str(fixture["review"]),
                "--output-dir",
                str(tmp_path / "out"),
                "--source-run-id",
                "unit-duplicate-shadow-scope-sha",
                "--low-scan-clean-activation-scope-audit-tsv",
                str(scope_audit),
            ],
        )
        == 2
    )

    assert "shadow projection contains duplicate shadow_projection_row_sha256" in (
        capsys.readouterr().err
    )


def test_standard_peak_productization_rejects_stale_current_projection(
    tmp_path: Path,
) -> None:
    fixture = _write_fixture(tmp_path)
    shadow_rows = _read_tsv(fixture["shadow"])
    stale = shadow_rows[0]
    stale.update(
        {
            "current_production_status": "accepted_rescue",
            "current_matrix_written": "TRUE",
            "current_matrix_value": "100",
            "current_matrix_source": "production_decision_snapshot",
            "shadow_decision": "context",
            "shadow_reasons": "already_written_current_matrix",
            "projected_matrix_written": "TRUE",
            "projected_matrix_value": "100",
        },
    )
    _write_tsv(fixture["shadow"], shadow_rows, SHADOW_PRODUCTION_PROJECTION_COLUMNS)

    assert (
        cli.main(
            [
                "--shadow-projection-cells-tsv",
                str(fixture["shadow"]),
                "--alignment-matrix-tsv",
                str(fixture["matrix"]),
                "--alignment-matrix-identity-tsv",
                str(fixture["identity"]),
                "--alignment-review-tsv",
                str(fixture["review"]),
                "--output-dir",
                str(tmp_path / "out"),
                "--source-run-id",
                "unit-standard-peak-productization",
            ],
        )
        == 2
    )


def test_standard_peak_productization_writes_duplicate_warning_rescue(
    tmp_path: Path,
) -> None:
    fixture = _write_fixture(tmp_path)
    shadow_rows = _read_tsv(fixture["shadow"])
    standard_row = shadow_rows[0]
    standard_row.update(
        {
            "current_production_status": "accepted_rescue",
            "gap_fill_state": "not_filled",
            "gap_fill_reason": "not_requested_duplicate_loser",
            "shadow_warnings": "same_peak_multi_claim",
        },
    )
    _write_tsv(fixture["shadow"], shadow_rows, SHADOW_PRODUCTION_PROJECTION_COLUMNS)
    output_dir = tmp_path / "out"

    assert (
        cli.main(
            [
                "--shadow-projection-cells-tsv",
                str(fixture["shadow"]),
                "--alignment-matrix-tsv",
                str(fixture["matrix"]),
                "--alignment-matrix-identity-tsv",
                str(fixture["identity"]),
                "--alignment-review-tsv",
                str(fixture["review"]),
                "--output-dir",
                str(output_dir),
                "--source-run-id",
                "unit-standard-peak-productization",
            ],
        )
        == 0
    )

    summary = json.loads(
        (
            output_dir / "standard_peak_backfill_productization_summary.json"
        ).read_text(encoding="utf-8"),
    )
    assert summary["selected_activation_row_count"] == "1"
    assert summary["matrix_cells_written"] == "1"

    matrix_rows = _read_tsv(output_dir / "activated_matrix" / "alignment_matrix.tsv")
    identity_rows = _read_tsv(
        output_dir / "activated_matrix" / "alignment_matrix_identity.tsv",
    )
    matrix_index_by_hypothesis = {
        row["peak_hypothesis_id"]: int(row["matrix_row_index"]) - 1
        for row in identity_rows
    }
    assert matrix_rows[matrix_index_by_hypothesis["FAM_STD"]]["S2"] == (
        "1.00000e+02"
    )

    decisions = _read_tsv(
        output_dir
        / "standard_peak_activation_inputs"
        / "standard_peak_activation_decisions.tsv",
    )
    values = _read_tsv(
        output_dir
        / "standard_peak_activation_inputs"
        / "standard_peak_activation_values.tsv",
    )
    assert "audit_warning:same_peak_multi_claim" in decisions[0][
        "source_evidence_tokens"
    ]
    assert "audit_warning:same_peak_multi_claim" in values[0][
        "source_provenance_detail"
    ]


def test_standard_peak_productization_fails_on_unattributed_seed_guard_write(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fixture = _write_fixture(tmp_path)
    output_dir = tmp_path / "out"

    def _blocked_write(
        rows: list[dict[str, str]] | tuple[dict[str, str], ...],
        **_: object,
    ) -> tuple[dict[str, str], ...]:
        finalized = [dict(row) for row in rows]
        finalized[0]["write_authority_status"] = "blocked_unattributed_write"
        finalized[0]["blocking_reason"] = "blocked_seed_guard_row_was_written"
        return tuple(finalized)

    monkeypatch.setattr(
        productization_module,
        "seed_guard_decisions_with_actual_writes",
        _blocked_write,
    )

    assert (
        cli.main(
            [
                "--shadow-projection-cells-tsv",
                str(fixture["shadow"]),
                "--alignment-matrix-tsv",
                str(fixture["matrix"]),
                "--alignment-matrix-identity-tsv",
                str(fixture["identity"]),
                "--alignment-review-tsv",
                str(fixture["review"]),
                "--output-dir",
                str(output_dir),
                "--source-run-id",
                "unit-standard-peak-productization",
            ],
        )
        == 1
    )

    summary = json.loads(
        (
            output_dir / "standard_peak_backfill_productization_summary.json"
        ).read_text(encoding="utf-8"),
    )
    assert summary["status"] == "fail"
    assert summary["next_action"] == "review_seed_guard_write_attribution_failure"


def _write_fixture(tmp_path: Path) -> dict[str, Path]:
    matrix = tmp_path / "alignment_matrix.tsv"
    identity = tmp_path / "alignment_matrix_identity.tsv"
    review = tmp_path / "alignment_review.tsv"
    cells = tmp_path / "alignment_backfill_cell_evidence.tsv"
    seeds = tmp_path / "alignment_owner_backfill_seed_audit.tsv"
    gate = tmp_path / "shift_aware_standard_peak_gate_calibration.tsv"
    shadow = tmp_path / "shadow_production_projection_cells.tsv"

    _write_tsv(
        matrix,
        [
            {"Mz": "300.3", "RT": "9.3", "S1": "10", "S2": ""},
            {"Mz": "301.3", "RT": "9.4", "S1": "20", "S2": ""},
        ],
        ("Mz", "RT", "S1", "S2"),
    )
    _write_tsv(
        identity,
        [
            _identity_row("1", "300.3", "9.3", "FAM_STD"),
            _identity_row("2", "301.3", "9.4", "FAM_NON"),
        ],
        (
            "matrix_row_index",
            "Mz",
            "RT",
            "peak_hypothesis_id",
            "row_identity_basis",
            "source_feature_family_ids",
        ),
    )
    _write_tsv(
        review,
        [
            _review_row("FAM_STD", "300.3", "9.3"),
            _review_row("FAM_NON", "301.3", "9.4"),
        ],
        ALIGNMENT_REVIEW_COLUMNS,
    )
    _write_tsv(
        cells,
        [
            _cell_row("FAM_STD", "S1", "detected"),
            _cell_row("FAM_STD", "S2", "rescued"),
            _cell_row("FAM_NON", "S1", "detected"),
            _cell_row("FAM_NON", "S2", "rescued"),
        ],
        ALIGNMENT_CELLS_COLUMNS,
    )
    _write_tsv(
        seeds,
        [_seed_row("FAM_STD", "S2"), _seed_row("FAM_NON", "S2")],
        ALIGNMENT_OWNER_BACKFILL_SEED_AUDIT_COLUMNS,
    )
    _write_tsv(
        gate,
        [_gate_row("FAM_STD", supported=True), _gate_row("FAM_NON", supported=False)],
        (
            "feature_family_id",
            "standard_peak_gate_call",
            "standard_peak_gate_reasons",
            "standard_peak_gate_blockers",
            "calibration_outcome",
            "min_shape_r_after_best_shift",
            "max_abs_shift_sec",
        ),
    )
    _write_tsv(
        shadow,
        [
            _shadow_row("FAM_STD", "S2", "100", standard=True),
            _shadow_row("FAM_NON", "S2", "200", standard=False),
        ],
        SHADOW_PRODUCTION_PROJECTION_COLUMNS,
    )
    return {
        "matrix": matrix,
        "identity": identity,
        "review": review,
        "cells": cells,
        "seeds": seeds,
        "gate": gate,
        "shadow": shadow,
    }


def _identity_row(
    index: str,
    mz: str,
    rt: str,
    family: str,
) -> dict[str, str]:
    return {
        "matrix_row_index": index,
        "Mz": mz,
        "RT": rt,
        "peak_hypothesis_id": family,
        "row_identity_basis": "no_split_peak_hypothesis",
        "source_feature_family_ids": family,
    }


def _review_row(family: str, mz: str, rt: str) -> dict[str, str]:
    row = _blank_row(ALIGNMENT_REVIEW_COLUMNS)
    row.update(
        {
            "feature_family_id": family,
            "group_hypothesis_id": f"{family}::group",
            "public_family_id": family,
            "group_construction_role": "single_detected_seed",
            "group_delivery_role": "review",
            "group_membership_source": "owner_family",
            "family_center_mz": mz,
            "family_center_rt": rt,
            "detected_count": "1",
            "accepted_cell_count": "2",
            "accepted_rescue_count": "1",
            "quantifiable_detected_count": "1",
            "quantifiable_rescue_count": "1",
            "identity_decision": "provisional_discovery",
            "identity_confidence": "review_only",
            "primary_evidence": "owner_backfill_context",
            "identity_reason": "owner_backfill_context",
            "include_in_primary_matrix": "FALSE",
            "row_flags": "single_detected_seed;provisional_retention_candidate",
            "reason": "fixture",
        },
    )
    return row


def _cell_row(family: str, sample: str, status: str) -> dict[str, str]:
    row = _blank_row(ALIGNMENT_CELLS_COLUMNS)
    row.update(
        {
            "feature_family_id": family,
            "group_hypothesis_id": f"{family}::group",
            "public_family_id": family,
            "group_construction_role": "single_detected_seed",
            "group_delivery_role": "review",
            "group_membership_source": "owner_family",
            "sample_stem": sample,
            "status": status,
            "area": "1000.0",
            "primary_matrix_area": "1000.0" if status == "detected" else "",
            "primary_matrix_area_source": "detected" if status == "detected" else "",
            "apex_rt": "10.0000",
            "peak_start_rt": "9.9000",
            "peak_end_rt": "10.1000",
            "rt_delta_sec": "0",
            "trace_quality": "ok",
            "scan_support_score": "0.9",
            "gap_fill_state": "owner_backfill" if status == "rescued" else "observed",
            "gap_fill_reason": "owner_backfill" if status == "rescued" else "",
            "reason": "fixture",
        },
    )
    return row


def _seed_row(family: str, sample: str) -> dict[str, str]:
    row = _blank_row(ALIGNMENT_OWNER_BACKFILL_SEED_AUDIT_COLUMNS)
    row.update(
        {
            "feature_family_id": family,
            "group_hypothesis_id": f"{family}::group",
            "public_family_id": family,
            "group_construction_role": "single_detected_seed",
            "group_delivery_role": "review",
            "group_membership_source": "owner_family",
            "sample_stem": sample,
            "status": "rescued",
            "area": "1000.0",
            "apex_rt": "10.0000",
            "family_center_mz": "300.3",
            "family_center_rt": "10.0000",
            "backfill_seed_mz": "300.3",
            "backfill_seed_rt": "10.0000",
            "backfill_request_rt_min": "9.0000",
            "backfill_request_rt_max": "11.0000",
            "backfill_request_ppm": "20",
            "reason": "fixture",
        },
    )
    return row


def _gate_row(family: str, *, supported: bool) -> dict[str, str]:
    return {
        "feature_family_id": family,
        "standard_peak_gate_call": "standard_peak_gate_supported"
        if supported
        else "standard_peak_gate_blocked",
        "standard_peak_gate_reasons": "shift_aware_same_pattern_supported",
        "standard_peak_gate_blockers": ""
        if supported
        else "family_overlay_gaussian_smoothed_peak_not_standard",
        "calibration_outcome": "true_positive" if supported else "true_negative",
        "min_shape_r_after_best_shift": "0.99",
        "max_abs_shift_sec": "1.0",
    }


def _shadow_row(
    family: str,
    sample: str,
    value: str,
    *,
    standard: bool,
    row_sha: str | None = None,
) -> dict[str, str]:
    row = _blank_row(SHADOW_PRODUCTION_PROJECTION_COLUMNS)
    reason = (
        "MS1:product_authorized:supportive:trace_constellation:"
        "feature_family_sample:machine_standard_peak_gate_authorized | "
        "same_peak_reason:shift_aware_standard_peak_gate_supported"
        if standard
        else "MS1:product_authorized:supportive:trace_constellation:"
        "feature_family_sample:other"
    )
    row.update(
        {
            "schema_version": "shadow_production_projection_v1",
            "peak_hypothesis_id": family,
            "feature_family_id": family,
            "seed_group_id": (
                f"seed::{family}::mz=300.3::rt=10.0000::"
                "window=9.0000-11.0000::ppm=20"
            ),
            "sample_stem": sample,
            "current_raw_status": "rescued",
            "current_production_status": "review_rescue",
            "current_matrix_written": "FALSE",
            "shadow_decision": "accept",
            "shadow_reasons": (
                "same_peak_reason:shift_aware_standard_peak_gate_supported"
                if standard
                else "same_peak_reason:other"
            ),
            "projected_matrix_written": "TRUE",
            "projected_matrix_value": value,
            "product_authority_chain": reason,
            "shadow_projection_row_sha256": row_sha or "b" * 64,
            "evidence_gate_status": "visual_support",
            "support_components": "seed_request_provenance",
            "hard_blockers": "",
            "overlay_verdict": "ms1_shape_supports_family_backfill"
            if standard
            else "review_required_neighboring_ms1_interference",
        },
    )
    return row


def _scope_audit_row(
    family: str,
    sample: str,
    row_sha: str,
    status: str,
    *,
    low_scan_clean_status: str = "missing_evidence",
    low_height_clean_status: str | None = None,
    low_height_low_scan_clean_status: str | None = None,
    cell_height: str | None = None,
    apex_aligned_shape_similarity: str | None = None,
    trace_match_status: str | None = None,
) -> dict[str, str]:
    row = {
        "schema_version": "standard_peak_activation_scope_audit_v1",
        "feature_family_id": family,
        "peak_hypothesis_id": family,
        "sample_id": sample,
        "matrix_value_effect": "written",
        "matrix_value_source_row_sha256": row_sha,
        "high_signal_clean_status": status,
        "low_scan_clean_status": low_scan_clean_status,
    }
    if low_height_clean_status is not None:
        row["low_height_clean_status"] = low_height_clean_status
    if low_height_low_scan_clean_status is not None:
        row["low_height_low_scan_clean_status"] = low_height_low_scan_clean_status
    if cell_height is not None:
        row["cell_height"] = cell_height
    if apex_aligned_shape_similarity is not None:
        row["apex_aligned_shape_similarity"] = apex_aligned_shape_similarity
    if trace_match_status is not None:
        row["trace_match_status"] = trace_match_status
    return row


def _stability_audit_row(
    family: str,
    sample: str,
    row_sha: str,
    status: str,
) -> dict[str, str]:
    return {
        "schema_version": "standard_peak_reintegration_stability_audit_v1",
        "feature_family_id": family,
        "sample_id": sample,
        "matrix_value_effect": "written",
        "matrix_value_source_row_sha256": row_sha,
        "stability_status": status,
    }


def _policy_observed_oracle_row(
    family: str,
    sample: str,
    row_sha: str,
    *,
    oracle_case_status: str = "pass",
    included_in_product_acceptance: str = "TRUE",
) -> dict[str, str]:
    return {
        "schema_version": "standard_peak_policy_observed_oracle_v1",
        "feature_family_id": family,
        "peak_hypothesis_id": family,
        "sample_id": sample,
        "matrix_value_source_row_sha256": row_sha,
        "policy_decision": "detected_flagged",
        "policy_candidate_evidence_class": "reintegration_stable",
        "source_cell_status": "rescued",
        "trace_match_status": "matched",
        "trace_status": "rescued",
        "observed_result_source": "policy_observed_full_trace_reintegration_v1",
        "observed_independence_basis": "independent_boundary_reintegration_result",
        "oracle_case_status": oracle_case_status,
        "included_in_product_acceptance": included_in_product_acceptance,
    }


def _write_generated_policy_base(
    *,
    fixture: dict[str, Path],
    tmp_path: Path,
    source_audit: Path,
    stability_audit: Path,
) -> Path:
    output_dir = tmp_path / f"base_policy_{source_audit.stem}"
    assert (
        cli.main(
            [
                "--shadow-projection-cells-tsv",
                str(fixture["shadow"]),
                "--alignment-matrix-tsv",
                str(fixture["matrix"]),
                "--alignment-matrix-identity-tsv",
                str(fixture["identity"]),
                "--alignment-review-tsv",
                str(fixture["review"]),
                "--output-dir",
                str(output_dir),
                "--source-run-id",
                "unit-generated-policy-observed-oracle-base",
                "--backfill-policy-source-audit-tsv",
                str(source_audit),
                "--reintegration-stability-audit-tsv",
                str(stability_audit),
            ],
        )
        == 2
    )
    return output_dir / "standard_peak_backfill_policy.tsv"


def _write_policy_observed_oracle_summary(
    path: Path,
    *,
    policy_oracle_tsv: Path,
    source_audit_tsv: Path,
    source_policy_tsv: Path,
    status: str = "pass",
    candidate_count: int = 1,
    accepted_count: int = 1,
) -> None:
    summary = {
        "schema_version": "standard_peak_policy_observed_oracle_summary_v1",
        "source_run_id": "unit-policy-observed-oracle",
        "status": status,
        "observed_reintegration_mode": "full_trace",
        "source_backfill_policy_tsv": str(source_policy_tsv),
        "source_backfill_policy_sha256": file_sha256(source_policy_tsv),
        "source_activation_scope_audit_tsv": str(source_audit_tsv),
        "source_activation_scope_audit_sha256": file_sha256(source_audit_tsv),
        "candidate_policy_decision": "detected_flagged",
        "candidate_policy_row_count": str(candidate_count),
        "oracle_case_status_pass_count": str(accepted_count),
        "oracle_case_status_fail_boundary_count": "0",
        "oracle_case_status_fail_area_count": "0"
        if accepted_count == candidate_count
        else str(candidate_count - accepted_count),
        "oracle_case_status_inconclusive_count": "0",
        "included_in_product_acceptance_count": str(accepted_count),
        "max_boundary_error_min": "0",
        "max_area_relative_error": "0",
        "boundary_error_min_max_allowed": "0.1",
        "area_relative_error_max_allowed": "0.1",
        "policy_observed_oracle_tsv": str(policy_oracle_tsv),
        "policy_observed_oracle_sha256": file_sha256(policy_oracle_tsv),
        "next_action": (
            "eligible_for_policy_evidence_class_expected_diff_gate"
            if status == "pass"
            else "review_policy_observed_oracle_failures"
        ),
    }
    path.write_text(json.dumps(summary, indent=2, sort_keys=True), encoding="utf-8")


def _blank_row(columns: tuple[str, ...]) -> dict[str, str]:
    return {column: "" for column in columns}


def _write_tsv(
    path: Path,
    rows: list[dict[str, str]],
    fieldnames: tuple[str, ...],
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, delimiter="\t", fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def _read_tsv(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle, delimiter="\t"))
