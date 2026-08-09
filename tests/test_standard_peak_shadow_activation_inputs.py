import csv
import json
from pathlib import Path

import pytest

from tests.matrix_identity_fixtures import legacy_matrix_identity_row
from tools.diagnostics import standard_peak_shadow_activation_inputs as cli
from xic_extractor.alignment.matrix_output_contract import (
    LEGACY_ALIGNMENT_MATRIX_IDENTITY_COLUMNS,
)
from xic_extractor.diagnostics import standard_peak_shadow_activation_inputs
from xic_extractor.diagnostics.standard_peak_shadow_activation_inputs import (
    build_standard_peak_activation_inputs,
)


def test_build_standard_peak_activation_inputs_selects_new_standard_accepts() -> None:
    index = build_standard_peak_activation_inputs(
        [
            _shadow_row("FAM_STD", "S1", "100", standard=True),
            _shadow_row(
                "FAM_ALREADY",
                "S1",
                "200",
                standard=True,
                current_written="TRUE",
            ),
            _shadow_row("FAM_OTHER", "S1", "300", standard=False),
            _shadow_row("FAM_CTX", "S1", "400", standard=True, decision="context"),
        ],
        source_shadow_projection_sha256="a" * 64,
        source_run_id="unit-test",
    )

    assert len(index.decisions) == 1
    decision = index.decisions[0]
    assert decision["feature_family_id"] == "FAM_STD"
    assert decision["sample_id"] == "S1"
    assert decision["activation_status"] == "auto_activate"
    assert decision["activation_unit_scope"] == "peak_hypothesis"
    assert decision["diagnostic_only"] == "FALSE"
    value = index.values[0]
    assert value["projected_matrix_value"] == "100"
    assert value["source_artifact_sha256"] == "a" * 64
    assert value["source_row_sha256"] == "b" * 64
    assert index.summary["selected_activation_row_count"] == "1"
    assert index.summary["skipped_current_written_count"] == "1"
    assert index.summary["skipped_non_standard_reason_count"] == "1"
    assert index.summary["skipped_non_accept_count"] == "1"
    assert index.summary["standard_peak_gate_status"] == "pass"
    assert index.summary["activation_acceptance_status"] == "pass"
    assert index.summary["activation_acceptance_hard_fail_reasons"] == ""
    assert index.summary["activation_decision_scope"] == "manual_oracle_seed_rows"
    assert index.summary["must_not_regress_basis"] == "manual_status_flag"
    assert index.summary["next_action"] == "apply_matrix_only_activation"
    assert index.acceptance["activation_decision_scope"] == "manual_oracle_seed_rows"
    assert index.acceptance["must_not_regress_basis"] == "manual_status_flag"


def test_build_standard_peak_activation_inputs_labels_machine_gate_scope() -> None:
    index = build_standard_peak_activation_inputs(
        [_shadow_row("FAM_STD", "S1", "100", standard=True, machine=True)],
        source_shadow_projection_sha256="a" * 64,
        source_run_id="machine-gate-unit-test",
    )

    assert index.summary["selected_activation_row_count"] == "1"
    assert index.summary["activation_decision_scope"] == (
        "machine_gate_standard_peak_rows"
    )
    assert index.summary["must_not_regress_basis"] == (
        "machine_shift_aware_standard_peak_gate"
    )
    assert index.acceptance["activation_decision_scope"] == (
        "machine_gate_standard_peak_rows"
    )
    assert index.acceptance["must_not_regress_basis"] == (
        "machine_shift_aware_standard_peak_gate"
    )


@pytest.mark.parametrize(
    ("total_n", "detected_count", "expected_status", "expected_floor", "cohort"),
    [
        (19, 1, "not_applicable_small_cohort", "", "FALSE"),
        (20, 1, "blocked_low_seed_support", "2", "FALSE"),
        (20, 2, "eligible_per_cell_only", "2", "FALSE"),
        (79, 3, "eligible_per_cell_only", "3", "FALSE"),
        (80, 3, "blocked_low_seed_support", "4", "FALSE"),
        (85, 4, "eligible_continue_existing_gates", "4", "TRUE"),
        (100, 5, "eligible_continue_existing_gates", "5", "TRUE"),
    ],
)
def test_standard_peak_seed_guard_banded_thresholds(
    tmp_path: Path,
    total_n: int,
    detected_count: int,
    expected_status: str,
    expected_floor: str,
    cohort: str,
) -> None:
    fixture = _write_seed_guard_context_fixture(
        tmp_path,
        total_n=total_n,
        detected_count=detected_count,
    )
    assert hasattr(standard_peak_shadow_activation_inputs, "load_seed_guard_context")
    seed_guard_context = standard_peak_shadow_activation_inputs.load_seed_guard_context(
        pre_backfill_matrix_tsv=fixture["matrix"],
        pre_backfill_review_tsv=fixture["review"],
    )

    index = build_standard_peak_activation_inputs(
        [_shadow_row("FAM_STD", f"S{total_n}", "100", standard=True)],
        source_shadow_projection_sha256="a" * 64,
        source_run_id=f"N{total_n}",
        seed_guard_context=seed_guard_context,
    )

    assert len(index.seed_guard_decisions) == 1
    decision = index.seed_guard_decisions[0]
    assert decision["seed_guard_status"] == expected_status
    assert decision["seed_floor"] == expected_floor
    assert decision["cohort_scale_automatic_backfill"] == cohort
    if expected_status == "blocked_low_seed_support":
        assert index.decisions == ()
        assert decision["expected_no_write_cell_count"] == "1"
        assert decision["expected_no_write_cell_keys"] == f"FAM_STD/S{total_n}"
    else:
        assert len(index.decisions) == 1
    assert decision["candidate_source_row_count"] == "1"
    assert decision["evaluated_row_count"] == "1"
    assert decision["omitted_candidate_count"] == "0"


def test_standard_peak_seed_guard_output_proves_candidate_coverage(
    tmp_path: Path,
) -> None:
    fixture = _write_seed_guard_context_fixture(
        tmp_path,
        total_n=85,
        detected_count=3,
        extra_counts={"FAM_PASS": 4},
    )
    seed_guard_context = standard_peak_shadow_activation_inputs.load_seed_guard_context(
        pre_backfill_matrix_tsv=fixture["matrix"],
        pre_backfill_review_tsv=fixture["review"],
    )

    index = build_standard_peak_activation_inputs(
        [
            _shadow_row("FAM_STD", "S85", "100", standard=True),
            _shadow_row("FAM_PASS", "S85", "200", standard=True),
            _shadow_row("FAM_NON", "S85", "300", standard=False),
        ],
        source_shadow_projection_sha256="a" * 64,
        source_run_id="coverage",
        seed_guard_context=seed_guard_context,
    )
    write_outputs = (
        standard_peak_shadow_activation_inputs.write_standard_peak_activation_input_outputs
    )
    outputs = write_outputs(tmp_path / "out", index)

    guard_rows = _read_tsv(outputs.seed_guard_decisions_tsv)
    assert {row["feature_family_id"] for row in guard_rows} == {
        "FAM_STD",
        "FAM_PASS",
    }
    assert {row["candidate_source_row_count"] for row in guard_rows} == {"2"}
    assert {row["evaluated_row_count"] for row in guard_rows} == {"2"}
    assert {row["omitted_candidate_count"] for row in guard_rows} == {"0"}
    by_family = {row["feature_family_id"]: row for row in guard_rows}
    assert by_family["FAM_STD"]["seed_guard_status"] == "blocked_low_seed_support"
    assert by_family["FAM_PASS"]["seed_guard_status"] == (
        "eligible_continue_existing_gates"
    )
    assert len(index.decisions) == 1
    assert index.summary["seed_guard_blocked_count"] == "1"
    summary = json.loads(outputs.summary_json.read_text(encoding="utf-8"))
    assert summary["seed_guard_decisions_tsv"] == str(
        outputs.seed_guard_decisions_tsv
    )


def test_standard_peak_heldout_oracle_results_classify_boundary_and_area(
    tmp_path: Path,
) -> None:
    source_artifact = tmp_path / "observed.tsv"
    source_artifact.write_text("oracle_case_id\n", encoding="utf-8")
    assert hasattr(
        standard_peak_shadow_activation_inputs,
        "build_heldout_oracle_results",
    )

    results = standard_peak_shadow_activation_inputs.build_heldout_oracle_results(
        [
            _heldout_manifest_row("pass_case"),
            _heldout_manifest_row("boundary_case"),
            _heldout_manifest_row("area_case"),
            _heldout_manifest_row("invalid_area_case", oracle_area="0"),
        ],
        [
            _heldout_observed_row("pass_case"),
            _heldout_observed_row("boundary_case", observed_start_rt="8.8"),
            _heldout_observed_row("area_case", observed_area="80"),
            _heldout_observed_row("invalid_area_case"),
        ],
        result_source_artifact_path=source_artifact,
    )
    output_path = tmp_path / "heldout_oracle_results.tsv"
    standard_peak_shadow_activation_inputs.write_heldout_oracle_results(
        output_path,
        results,
    )

    by_case = {row["oracle_case_id"]: row for row in _read_tsv(output_path)}
    assert by_case["pass_case"]["oracle_case_status"] == "pass"
    assert by_case["pass_case"]["included_in_product_acceptance"] == "TRUE"
    assert by_case["boundary_case"]["oracle_case_status"] == "fail_boundary"
    assert by_case["boundary_case"]["included_in_product_acceptance"] == "FALSE"
    assert by_case["area_case"]["oracle_case_status"] == "fail_area"
    assert by_case["area_case"]["included_in_product_acceptance"] == "FALSE"
    assert by_case["invalid_area_case"]["oracle_case_status"] == (
        "inconclusive_review_only"
    )
    assert by_case["invalid_area_case"]["inconclusive_reason"] == (
        "invalid_oracle_area"
    )
    assert len(by_case["pass_case"]["result_source_artifact_sha256"]) == 64


def test_standard_peak_heldout_oracle_results_cli_writes_contract_tsv(
    tmp_path: Path,
) -> None:
    from tools.diagnostics import standard_peak_heldout_oracle_results as heldout_cli

    manifest_tsv = tmp_path / "heldout_oracle_manifest.tsv"
    observed_tsv = tmp_path / "heldout_observed_results.tsv"
    source_artifact = tmp_path / "observed_source.tsv"
    output_tsv = tmp_path / "heldout_oracle_results.tsv"
    source_artifact.write_text("oracle_case_id\n", encoding="utf-8")
    _write_tsv(
        manifest_tsv,
        [_heldout_manifest_row("pass_case")],
        tuple(_heldout_manifest_row("pass_case")),
    )
    _write_tsv(
        observed_tsv,
        [_heldout_observed_row("pass_case")],
        tuple(_heldout_observed_row("pass_case")),
    )

    assert heldout_cli.main(
        [
            "--heldout-oracle-manifest-tsv",
            str(manifest_tsv),
            "--observed-results-tsv",
            str(observed_tsv),
            "--result-source-artifact",
            str(source_artifact),
            "--output-tsv",
            str(output_tsv),
        ],
    ) == 0

    rows = _read_tsv(output_tsv)
    assert rows[0]["schema_version"] == (
        "standard_peak_seed_guard_heldout_oracle_results_v1"
    )
    assert rows[0]["oracle_case_status"] == "pass"
    assert rows[0]["included_in_product_acceptance"] == "TRUE"
    assert rows[0]["observed_result_source"] == "unit_product_writer"
    assert rows[0]["observed_boundary_source"] == "masked_product_writer_boundary"
    assert rows[0]["observed_area_source"] == "masked_product_writer_area"
    assert rows[0]["observed_independence_basis"] == (
        "product_writer_observed_result"
    )
    assert len(rows[0]["result_source_artifact_sha256"]) == 64


def test_standard_peak_heldout_oracle_results_cli_requires_full_manifest_schema(
    tmp_path: Path,
) -> None:
    from tools.diagnostics import standard_peak_heldout_oracle_results as heldout_cli

    manifest_tsv = tmp_path / "heldout_oracle_manifest.tsv"
    observed_tsv = tmp_path / "heldout_observed_results.tsv"
    source_artifact = tmp_path / "observed_source.tsv"
    output_tsv = tmp_path / "heldout_oracle_results.tsv"
    source_artifact.write_text("oracle_case_id\n", encoding="utf-8")
    incomplete_manifest = _heldout_manifest_row("pass_case")
    del incomplete_manifest["baseline_model_set"]
    _write_tsv(
        manifest_tsv,
        [incomplete_manifest],
        tuple(incomplete_manifest),
    )
    _write_tsv(
        observed_tsv,
        [_heldout_observed_row("pass_case")],
        tuple(_heldout_observed_row("pass_case")),
    )

    with pytest.raises(ValueError, match="baseline_model_set"):
        heldout_cli.main(
            [
                "--heldout-oracle-manifest-tsv",
                str(manifest_tsv),
                "--observed-results-tsv",
                str(observed_tsv),
                "--result-source-artifact",
                str(source_artifact),
                "--output-tsv",
                str(output_tsv),
            ],
        )

    assert not output_tsv.exists()


def test_standard_peak_heldout_oracle_results_cli_requires_observed_provenance_schema(
    tmp_path: Path,
) -> None:
    from tools.diagnostics import standard_peak_heldout_oracle_results as heldout_cli

    manifest_tsv = tmp_path / "heldout_oracle_manifest.tsv"
    observed_tsv = tmp_path / "heldout_observed_results.tsv"
    source_artifact = tmp_path / "observed_source.tsv"
    output_tsv = tmp_path / "heldout_oracle_results.tsv"
    source_artifact.write_text("oracle_case_id\n", encoding="utf-8")
    observed = _heldout_observed_row("pass_case")
    del observed["observed_boundary_source"]
    _write_tsv(
        manifest_tsv,
        [_heldout_manifest_row("pass_case")],
        tuple(_heldout_manifest_row("pass_case")),
    )
    _write_tsv(
        observed_tsv,
        [observed],
        tuple(observed),
    )

    with pytest.raises(ValueError, match="observed_boundary_source"):
        heldout_cli.main(
            [
                "--heldout-oracle-manifest-tsv",
                str(manifest_tsv),
                "--observed-results-tsv",
                str(observed_tsv),
                "--result-source-artifact",
                str(source_artifact),
                "--output-tsv",
                str(output_tsv),
            ],
        )

    assert not output_tsv.exists()


def test_standard_peak_heldout_oracle_results_cli_requires_result_source_artifact(
    tmp_path: Path,
) -> None:
    from tools.diagnostics import standard_peak_heldout_oracle_results as heldout_cli

    manifest_tsv = tmp_path / "heldout_oracle_manifest.tsv"
    observed_tsv = tmp_path / "heldout_observed_results.tsv"
    source_artifact = tmp_path / "missing_observed_source.tsv"
    output_tsv = tmp_path / "heldout_oracle_results.tsv"
    _write_tsv(
        manifest_tsv,
        [_heldout_manifest_row("pass_case")],
        tuple(_heldout_manifest_row("pass_case")),
    )
    _write_tsv(
        observed_tsv,
        [_heldout_observed_row("pass_case")],
        tuple(_heldout_observed_row("pass_case")),
    )

    with pytest.raises(ValueError, match="result source artifact"):
        heldout_cli.main(
            [
                "--heldout-oracle-manifest-tsv",
                str(manifest_tsv),
                "--observed-results-tsv",
                str(observed_tsv),
                "--result-source-artifact",
                str(source_artifact),
                "--output-tsv",
                str(output_tsv),
            ],
        )

    assert not output_tsv.exists()


def test_standard_peak_heldout_oracle_results_rejects_observed_rows_without_provenance(
    tmp_path: Path,
) -> None:
    source_artifact = tmp_path / "observed.tsv"
    source_artifact.write_text("oracle_case_id\n", encoding="utf-8")
    observed = _heldout_observed_row("pass_case")
    del observed["observed_independence_basis"]

    with pytest.raises(ValueError, match="observed_independence_basis"):
        standard_peak_shadow_activation_inputs.build_heldout_oracle_results(
            [_heldout_manifest_row("pass_case")],
            [observed],
            result_source_artifact_path=source_artifact,
        )


@pytest.mark.parametrize(
    "source_value",
    [
        "oracle_source_row",
        "oracle-source row",
        "manual review worksheet",
        "manual-review worksheet",
        "manual verdict source",
        "review queue row",
    ],
)
def test_standard_peak_heldout_oracle_results_rejects_oracle_copied_observed_rows(
    tmp_path: Path,
    source_value: str,
) -> None:
    source_artifact = tmp_path / "observed.tsv"
    source_artifact.write_text("oracle_case_id\n", encoding="utf-8")
    observed = _heldout_observed_row(
        "pass_case",
        observed_boundary_source=source_value,
        observed_area_source=source_value,
        observed_independence_basis="product_writer_observed_result",
    )

    with pytest.raises(ValueError, match="source is not independent"):
        standard_peak_shadow_activation_inputs.build_heldout_oracle_results(
            [_heldout_manifest_row("pass_case")],
            [observed],
            result_source_artifact_path=source_artifact,
        )


def test_standard_peak_heldout_oracle_results_rejects_missing_result_source_artifact(
    tmp_path: Path,
) -> None:
    source_artifact = tmp_path / "missing_observed_source.tsv"

    with pytest.raises(ValueError, match="result source artifact"):
        standard_peak_shadow_activation_inputs.build_heldout_oracle_results(
            [_heldout_manifest_row("pass_case")],
            [_heldout_observed_row("pass_case")],
            result_source_artifact_path=source_artifact,
        )


def test_standard_peak_heldout_oracle_results_cli_rejects_wrong_manifest_version(
    tmp_path: Path,
) -> None:
    from tools.diagnostics import standard_peak_heldout_oracle_results as heldout_cli

    manifest_tsv = tmp_path / "heldout_oracle_manifest.tsv"
    observed_tsv = tmp_path / "heldout_observed_results.tsv"
    source_artifact = tmp_path / "observed_source.tsv"
    output_tsv = tmp_path / "heldout_oracle_results.tsv"
    source_artifact.write_text("oracle_case_id\n", encoding="utf-8")
    wrong_version = _heldout_manifest_row("pass_case")
    wrong_version["schema_version"] = (
        "standard_peak_seed_guard_heldout_oracle_manifest_v0"
    )
    _write_tsv(
        manifest_tsv,
        [wrong_version],
        tuple(wrong_version),
    )
    _write_tsv(
        observed_tsv,
        [_heldout_observed_row("pass_case")],
        tuple(_heldout_observed_row("pass_case")),
    )

    with pytest.raises(ValueError, match="unsupported heldout oracle manifest"):
        heldout_cli.main(
            [
                "--heldout-oracle-manifest-tsv",
                str(manifest_tsv),
                "--observed-results-tsv",
                str(observed_tsv),
                "--result-source-artifact",
                str(source_artifact),
                "--output-tsv",
                str(output_tsv),
            ],
        )

    assert not output_tsv.exists()


def test_standard_peak_heldout_oracle_results_rejects_ambiguous_observed_rows(
    tmp_path: Path,
) -> None:
    source_artifact = tmp_path / "observed.tsv"
    source_artifact.write_text("oracle_case_id\n", encoding="utf-8")

    with pytest.raises(ValueError, match="duplicate observed oracle_case_id"):
        standard_peak_shadow_activation_inputs.build_heldout_oracle_results(
            [_heldout_manifest_row("pass_case")],
            [
                _heldout_observed_row("pass_case"),
                _heldout_observed_row("pass_case", observed_area="90"),
            ],
            result_source_artifact_path=source_artifact,
        )

    with pytest.raises(ValueError, match="observed oracle_case_id not in manifest"):
        standard_peak_shadow_activation_inputs.build_heldout_oracle_results(
            [_heldout_manifest_row("pass_case")],
            [
                _heldout_observed_row("pass_case"),
                _heldout_observed_row("stale_case"),
            ],
            result_source_artifact_path=source_artifact,
        )


def test_standard_peak_heldout_oracle_results_rejects_incomplete_manifest_mapping(
    tmp_path: Path,
) -> None:
    source_artifact = tmp_path / "observed.tsv"
    source_artifact.write_text("oracle_case_id\n", encoding="utf-8")
    incomplete_manifest = _heldout_manifest_row("pass_case")
    del incomplete_manifest["baseline_model_set"]

    with pytest.raises(ValueError, match="baseline_model_set"):
        standard_peak_shadow_activation_inputs.build_heldout_oracle_results(
            [incomplete_manifest],
            [_heldout_observed_row("pass_case")],
            result_source_artifact_path=source_artifact,
        )


def test_standard_peak_heldout_oracle_results_requires_original_detected_cell_status(
    tmp_path: Path,
) -> None:
    source_artifact = tmp_path / "observed.tsv"
    source_artifact.write_text("oracle_case_id\n", encoding="utf-8")
    missing_status = _heldout_manifest_row("missing_status")
    del missing_status["heldout_original_cell_status"]

    with pytest.raises(ValueError, match="heldout_original_cell_status"):
        standard_peak_shadow_activation_inputs.build_heldout_oracle_results(
            [missing_status],
            [_heldout_observed_row("missing_status")],
            result_source_artifact_path=source_artifact,
        )

    for case_id, status in (
        ("rescued_status", "rescued"),
        ("blank_status", ""),
        ("unknown_status", "backfilled"),
    ):
        with pytest.raises(ValueError, match="heldout_original_cell_status"):
            standard_peak_shadow_activation_inputs.build_heldout_oracle_results(
                [
                    _heldout_manifest_row(
                        case_id,
                        heldout_original_cell_status=status,
                    )
                ],
                [_heldout_observed_row(case_id)],
                result_source_artifact_path=source_artifact,
            )


def test_standard_peak_heldout_oracle_results_accepts_original_detected_statuses(
    tmp_path: Path,
) -> None:
    source_artifact = tmp_path / "observed.tsv"
    source_artifact.write_text("oracle_case_id\n", encoding="utf-8")
    allowed_statuses = (
        "detected",
        "detected_seed",
        "quantifiable_detected",
        "accepted_detected",
    )

    results = standard_peak_shadow_activation_inputs.build_heldout_oracle_results(
        [
            _heldout_manifest_row(
                f"case_{index}",
                heldout_original_cell_status=status,
            )
            for index, status in enumerate(allowed_statuses)
        ],
        [
            _heldout_observed_row(f"case_{index}")
            for index, _status in enumerate(allowed_statuses)
        ],
        result_source_artifact_path=source_artifact,
    )

    assert [row["oracle_case_status"] for row in results] == ["pass"] * 4


@pytest.mark.parametrize(
    "observed_source_column",
    (
        "observed_result_source",
        "observed_boundary_source",
        "observed_area_source",
    ),
)
def test_standard_peak_heldout_oracle_results_rejects_neutral_oracle_source_copy(
    tmp_path: Path,
    observed_source_column: str,
) -> None:
    source_artifact = tmp_path / "observed.tsv"
    source_artifact.write_text("oracle_case_id\n", encoding="utf-8")
    manifest = _heldout_manifest_row(
        "neutral_copy",
        oracle_source="neutral reviewed artifact",
    )
    observed = _heldout_observed_row("neutral_copy")
    observed[observed_source_column] = "neutral-reviewed artifact"

    with pytest.raises(ValueError, match="source is not independent"):
        standard_peak_shadow_activation_inputs.build_heldout_oracle_results(
            [manifest],
            [observed],
            result_source_artifact_path=source_artifact,
        )


def test_standard_peak_heldout_oracle_results_rejects_loose_tolerances(
    tmp_path: Path,
) -> None:
    source_artifact = tmp_path / "observed.tsv"
    source_artifact.write_text("oracle_case_id\n", encoding="utf-8")
    loose_boundary = _heldout_manifest_row("loose_boundary")
    loose_boundary["acceptable_boundary_delta_min"] = "0.1001"
    loose_area = _heldout_manifest_row("loose_area")
    loose_area["acceptable_area_relative_error"] = "0.1001"

    with pytest.raises(ValueError, match="acceptable_boundary_delta_min"):
        standard_peak_shadow_activation_inputs.build_heldout_oracle_results(
            [loose_boundary],
            [_heldout_observed_row("loose_boundary")],
            result_source_artifact_path=source_artifact,
        )

    with pytest.raises(ValueError, match="acceptable_area_relative_error"):
        standard_peak_shadow_activation_inputs.build_heldout_oracle_results(
            [loose_area],
            [_heldout_observed_row("loose_area")],
            result_source_artifact_path=source_artifact,
        )


def test_standard_peak_heldout_oracle_results_accepts_strict_tolerances(
    tmp_path: Path,
) -> None:
    source_artifact = tmp_path / "observed.tsv"
    source_artifact.write_text("oracle_case_id\n", encoding="utf-8")
    strict = _heldout_manifest_row("strict_case")
    strict["acceptable_boundary_delta_min"] = "0.05"
    strict["acceptable_area_relative_error"] = "0.05"

    results = standard_peak_shadow_activation_inputs.build_heldout_oracle_results(
        [strict],
        [_heldout_observed_row("strict_case")],
        result_source_artifact_path=source_artifact,
    )

    assert results[0]["oracle_case_status"] == "pass"
    assert results[0]["included_in_product_acceptance"] == "TRUE"


def test_standard_peak_heldout_oracle_results_accepts_exact_boundary_tolerance(
    tmp_path: Path,
) -> None:
    source_artifact = tmp_path / "observed.tsv"
    source_artifact.write_text("oracle_case_id\n", encoding="utf-8")

    results = standard_peak_shadow_activation_inputs.build_heldout_oracle_results(
        [_heldout_manifest_row("exact_boundary")],
        [_heldout_observed_row("exact_boundary", observed_end_rt="9.3")],
        result_source_artifact_path=source_artifact,
    )

    assert float(results[0]["boundary_error_min"]) == pytest.approx(0.1)
    assert results[0]["oracle_case_status"] == "pass"
    assert results[0]["included_in_product_acceptance"] == "TRUE"


def test_standard_peak_heldout_oracle_results_accepts_exact_area_tolerance(
    tmp_path: Path,
) -> None:
    source_artifact = tmp_path / "observed.tsv"
    source_artifact.write_text("oracle_case_id\n", encoding="utf-8")

    results = standard_peak_shadow_activation_inputs.build_heldout_oracle_results(
        [_heldout_manifest_row("exact_area", oracle_area="0.3")],
        [_heldout_observed_row("exact_area", observed_area="0.33")],
        result_source_artifact_path=source_artifact,
    )

    assert float(results[0]["area_relative_error"]) == pytest.approx(0.1)
    assert results[0]["oracle_case_status"] == "pass"
    assert results[0]["included_in_product_acceptance"] == "TRUE"


def test_build_standard_peak_activation_inputs_rejects_malformed_row_hash() -> None:
    malformed = _shadow_row("FAM_STD", "S1", "100", standard=True)
    malformed["shadow_projection_row_sha256"] = "g" * 64

    index = build_standard_peak_activation_inputs(
        [malformed],
        source_shadow_projection_sha256="a" * 64,
        source_run_id="unit-test",
    )

    assert index.summary["standard_peak_gate_status"] == "fail"
    assert "FAM_STD/S1:invalid_source_row_sha256" in index.summary[
        "standard_peak_gate_failure_reasons"
    ]
    assert index.summary["activation_acceptance_status"] == "fail"
    assert index.summary["next_action"] == (
        "review_standard_peak_activation_gate_failures"
    )


def test_standard_peak_shadow_activation_inputs_cli_writes_activation_inputs(
    tmp_path: Path,
) -> None:
    source = tmp_path / "shadow.tsv"
    _write_tsv(
        source,
        [_shadow_row("FAM_STD", "S1", "100", standard=True)],
        (
            "schema_version",
            "peak_hypothesis_id",
            "feature_family_id",
            "sample_stem",
            "current_raw_status",
            "current_production_status",
            "current_matrix_written",
            "shadow_decision",
            "projected_matrix_written",
            "projected_matrix_value",
            "product_authority_chain",
            "shadow_projection_row_sha256",
        ),
    )

    assert (
        cli.main(
            [
                "--shadow-projection-cells-tsv",
                str(source),
                "--output-dir",
                str(tmp_path / "out"),
                "--source-run-id",
                "unit-test",
            ],
        )
        == 0
    )

    out = tmp_path / "out"
    decisions = _read_tsv(out / "standard_peak_activation_decisions.tsv")
    values = _read_tsv(out / "standard_peak_activation_values.tsv")
    acceptance = _read_tsv(out / "standard_peak_activation_acceptance.tsv")
    summary = json.loads(
        (out / "standard_peak_activation_inputs_summary.json").read_text(
            encoding="utf-8",
        ),
    )
    assert decisions[0]["activation_status"] == "auto_activate"
    assert values[0]["projected_matrix_value_source"] == (
        "standard_peak_shadow_projection"
    )
    assert len(values[0]["source_artifact_sha256"]) == 64
    assert acceptance[0]["decision_rows_total"] == "1"
    assert acceptance[0]["acceptance_status"] == "pass"
    assert acceptance[0]["hard_fail_reasons"] == ""
    assert acceptance[0]["must_not_regress_status"] == "pass"
    assert summary["selected_activation_row_count"] == "1"
    assert summary["standard_peak_gate_status"] == "pass"
    assert summary["activation_acceptance_status"] == "pass"
    assert summary["activation_acceptance_max_allowed_product_affecting_rows"] == "1"
    assert summary["next_action"] == "apply_matrix_only_activation"
    assert summary["source_run_id"] == "unit-test"


def test_standard_peak_shadow_activation_inputs_cli_can_apply_matrix_only(
    tmp_path: Path,
) -> None:
    source = tmp_path / "shadow.tsv"
    fixture = _write_matrix_only_fixture(tmp_path)
    _write_tsv(
        source,
        [_shadow_row("FAM_STD", "S2", "100", standard=True)],
        (
            "schema_version",
            "peak_hypothesis_id",
            "feature_family_id",
            "sample_stem",
            "current_raw_status",
            "current_production_status",
            "current_matrix_written",
            "shadow_decision",
            "projected_matrix_written",
            "projected_matrix_value",
            "product_authority_chain",
            "shadow_projection_row_sha256",
        ),
    )

    assert (
        cli.main(
            [
                "--shadow-projection-cells-tsv",
                str(source),
                "--output-dir",
                str(tmp_path / "out"),
                "--source-run-id",
                "unit-test",
                "--apply-matrix-only",
                "--alignment-matrix-tsv",
                str(fixture["matrix"]),
                "--alignment-matrix-identity-tsv",
                str(fixture["identity"]),
                "--alignment-review-tsv",
                str(fixture["review"]),
            ],
        )
        == 0
    )

    activated_dir = tmp_path / "out" / "activated_matrix"
    matrix_rows = _read_tsv(activated_dir / "alignment_matrix.tsv")
    identity_rows = _read_tsv(activated_dir / "alignment_matrix_identity.tsv")
    delta = _read_tsv(activated_dir / "activation_value_delta.tsv")
    row_index = next(
        index
        for index, row in enumerate(identity_rows)
        if row["peak_hypothesis_id"] == "FAM_STD"
    )
    assert matrix_rows[row_index]["S2"] == "1.00000e+02"
    assert delta[0]["matrix_value_effect"] == "written"
    assert delta[0]["matrix_value_source"] == "activation_values_tsv"


def _shadow_row(
    family: str,
    sample: str,
    value: str,
    *,
    standard: bool,
    decision: str = "accept",
    current_written: str = "FALSE",
    machine: bool = False,
) -> dict[str, str]:
    authority_token = (
        "machine_standard_peak_gate_authorized"
        if machine
        else "manual_standard_peak_gate_authorized"
    )
    reason = (
        "MS1:product_authorized:supportive:trace_constellation:"
        f"feature_family_sample:{authority_token} | "
        "same_peak_reason:shift_aware_standard_peak_gate_supported"
        if standard
        else "MS1:product_authorized:supportive:trace_constellation:"
        "feature_family_sample:other"
    )
    return {
        "schema_version": "shadow_production_projection_v1",
        "peak_hypothesis_id": family,
        "feature_family_id": family,
        "sample_stem": sample,
        "current_raw_status": "rescued",
        "current_production_status": "review_rescue",
        "current_matrix_written": current_written,
        "shadow_decision": decision,
        "projected_matrix_written": "TRUE",
        "projected_matrix_value": value,
        "product_authority_chain": reason,
        "shadow_projection_row_sha256": "b" * 64,
    }


def _write_matrix_only_fixture(tmp_path: Path) -> dict[str, Path]:
    matrix = tmp_path / "alignment_matrix.tsv"
    identity = tmp_path / "alignment_matrix_identity.tsv"
    review = tmp_path / "alignment_review.tsv"
    _write_tsv(
        matrix,
        [
            {
                "Mz": "300.3",
                "RT": "9.3",
                "S1": "10",
                "S2": "",
            },
        ],
        ("Mz", "RT", "S1", "S2"),
    )
    _write_tsv(
        identity,
        [
            legacy_matrix_identity_row(
                index=1,
                mz="300.3",
                rt="9.3",
                peak_hypothesis_id="FAM_STD",
            ),
        ],
        LEGACY_ALIGNMENT_MATRIX_IDENTITY_COLUMNS,
    )
    _write_tsv(
        review,
        [
            {
                "feature_family_id": "FAM_STD",
                "neutral_loss_tag": "DNA_dR",
                "family_center_mz": "300.3",
                "family_center_rt": "9.3",
                "include_in_primary_matrix": "TRUE",
            },
        ],
        (
            "feature_family_id",
            "neutral_loss_tag",
            "family_center_mz",
            "family_center_rt",
            "include_in_primary_matrix",
        ),
    )
    return {"matrix": matrix, "identity": identity, "review": review}


def _write_seed_guard_context_fixture(
    tmp_path: Path,
    *,
    total_n: int,
    detected_count: int,
    extra_counts: dict[str, int] | None = None,
) -> dict[str, Path]:
    matrix = tmp_path / f"alignment_matrix_N{total_n}.tsv"
    review = tmp_path / f"alignment_review_N{total_n}.tsv"
    sample_columns = tuple(f"S{index}" for index in range(1, total_n + 1))
    rows = [
        {
            "Mz": "300.3",
            "RT": "9.3",
            **{sample: ("10" if sample == "S1" else "") for sample in sample_columns},
        }
    ]
    _write_tsv(matrix, rows, ("Mz", "RT", *sample_columns))
    review_rows = [
        {
            "feature_family_id": "FAM_STD",
            "quantifiable_detected_count": str(detected_count),
        }
    ]
    for family, count in (extra_counts or {}).items():
        review_rows.append(
            {
                "feature_family_id": family,
                "quantifiable_detected_count": str(count),
            }
        )
    _write_tsv(
        review,
        review_rows,
        ("feature_family_id", "quantifiable_detected_count"),
    )
    return {"matrix": matrix, "review": review}


def _heldout_manifest_row(
    oracle_case_id: str,
    *,
    oracle_area: str = "100",
    heldout_original_cell_status: str = "detected",
    oracle_source: str = "unit_fixture",
) -> dict[str, str]:
    return {
        "schema_version": "standard_peak_seed_guard_heldout_oracle_manifest_v1",
        "oracle_case_id": oracle_case_id,
        "source_run_id": "unit-heldout",
        "mask_strategy": "unit_mask",
        "masked_sample": "S1",
        "heldout_original_cell_status": heldout_original_cell_status,
        "feature_family_id": "FAM_STD",
        "peak_hypothesis_id": "FAM_STD",
        "target_shape_class": "standard_assessable",
        "oracle_source": oracle_source,
        "oracle_start_rt": "9.0",
        "oracle_end_rt": "9.2",
        "oracle_area": oracle_area,
        "baseline_model_set": "unit",
        "baseline_epsilon": "1e-9",
        "baseline_residual_threshold": "0.0",
        "acceptable_boundary_delta_min": "0.1",
        "acceptable_area_relative_error": "0.1",
        "expected_seed_guard_status": "eligible_continue_existing_gates",
        "expected_integration_pathology": "standard_assessable",
        "expected_matrix_write_allowed": "TRUE",
    }


def _heldout_observed_row(
    oracle_case_id: str,
    *,
    observed_start_rt: str = "9.0",
    observed_end_rt: str = "9.2",
    observed_area: str = "100",
    observed_result_source: str = "unit_product_writer",
    observed_boundary_source: str = "masked_product_writer_boundary",
    observed_area_source: str = "masked_product_writer_area",
    observed_independence_basis: str = "product_writer_observed_result",
) -> dict[str, str]:
    return {
        "oracle_case_id": oracle_case_id,
        "observed_start_rt": observed_start_rt,
        "observed_end_rt": observed_end_rt,
        "observed_area": observed_area,
        "observed_result_source": observed_result_source,
        "observed_boundary_source": observed_boundary_source,
        "observed_area_source": observed_area_source,
        "observed_independence_basis": observed_independence_basis,
    }


def _write_tsv(
    path: Path,
    rows: list[dict[str, str]],
    fieldnames: tuple[str, ...],
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, delimiter="\t")
        writer.writeheader()
        writer.writerows(rows)


def _read_tsv(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8-sig") as handle:
        return list(csv.DictReader(handle, delimiter="\t"))
