import csv
import json
from pathlib import Path

import pytest

from tests.matrix_identity_fixtures import legacy_matrix_identity_row
from xic_extractor.alignment.shared_peak_identity_explanation.schema import (
    ACTIVATION_APPLICATION_SCHEMA_VERSION,
    ACTIVATION_DECISION_SCHEMA_VERSION,
    ACTIVATION_VALUE_DELTA_SCHEMA_VERSION,
)
from xic_extractor.diagnostics import (
    backfill_peakhypothesis_activation_acceptance as acceptance,
)
from xic_extractor.diagnostics import (
    backfill_peakhypothesis_activation_bridge as bridge,
)


def test_acceptance_passes_exact_activation_matrix_diff(tmp_path: Path) -> None:
    inputs = _write_acceptance_inputs(tmp_path)

    index = acceptance.build_activation_acceptance(
        promotion_rows=_read_tsv(inputs["promotion"]),
        activation_decision_rows=_read_tsv(inputs["decisions"]),
        preflight_rows=_read_tsv(inputs["preflight"]),
        application_summary_rows=_read_tsv(inputs["application_summary"]),
        value_delta_rows=_read_tsv(inputs["value_delta"]),
        input_matrix_rows=_read_tsv(inputs["input_matrix"]),
        input_identity_rows=_read_tsv(inputs["input_identity"]),
        output_matrix_rows=_read_tsv(inputs["output_matrix"]),
        output_identity_rows=_read_tsv(inputs["output_identity"]),
        source_run_id="unit-pass",
    )
    outputs = acceptance.write_activation_acceptance_outputs(tmp_path / "out", index)

    assert index.acceptance_row["acceptance_status"] == "pass"
    assert index.acceptance_row["changed_matrix_cell_count"] == "3"
    assert index.acceptance_row["unexpected_matrix_diff_count"] == "0"
    assert index.acceptance_row["missing_matrix_diff_count"] == "0"
    assert index.acceptance_row["value_mismatch_count"] == "0"
    assert index.acceptance_row["next_action"] == (
        "ready_for_fixture_cohort_reviewed_activation_acceptance"
    )
    diff_rows = _read_tsv(outputs.matrix_diff_tsv)
    assert [(row["peak_hypothesis_id"], row["sample_stem"]) for row in diff_rows] == [
        ("FAM001", "S1"),
        ("FAM002", "S2"),
        ("FAM003", "S1"),
    ]
    summary = json.loads(outputs.summary_json.read_text(encoding="utf-8"))
    assert summary["acceptance_status"] == "pass"


def test_acceptance_fails_unexpected_matrix_diff(tmp_path: Path) -> None:
    inputs = _write_acceptance_inputs(tmp_path, unexpected_output_change=True)

    index = acceptance.build_activation_acceptance(
        promotion_rows=_read_tsv(inputs["promotion"]),
        activation_decision_rows=_read_tsv(inputs["decisions"]),
        preflight_rows=_read_tsv(inputs["preflight"]),
        application_summary_rows=_read_tsv(inputs["application_summary"]),
        value_delta_rows=_read_tsv(inputs["value_delta"]),
        input_matrix_rows=_read_tsv(inputs["input_matrix"]),
        input_identity_rows=_read_tsv(inputs["input_identity"]),
        output_matrix_rows=_read_tsv(inputs["output_matrix"]),
        output_identity_rows=_read_tsv(inputs["output_identity"]),
    )

    assert index.acceptance_row["acceptance_status"] == "fail"
    assert index.acceptance_row["unexpected_matrix_diff_count"] == "1"
    assert "unexpected_matrix_diff" in index.acceptance_row["hard_fail_reasons"]


def test_acceptance_allows_application_summary_expected_added_rows(
    tmp_path: Path,
) -> None:
    inputs = _write_acceptance_inputs(tmp_path, expected_added_rows=True)

    index = acceptance.build_activation_acceptance(
        promotion_rows=_read_tsv(inputs["promotion"]),
        activation_decision_rows=_read_tsv(inputs["decisions"]),
        preflight_rows=_read_tsv(inputs["preflight"]),
        application_summary_rows=_read_tsv(inputs["application_summary"]),
        value_delta_rows=_read_tsv(inputs["value_delta"]),
        input_matrix_rows=_read_tsv(inputs["input_matrix"]),
        input_identity_rows=_read_tsv(inputs["input_identity"]),
        output_matrix_rows=_read_tsv(inputs["output_matrix"]),
        output_identity_rows=_read_tsv(inputs["output_identity"]),
    )

    assert index.acceptance_row["acceptance_status"] == "pass"
    assert index.acceptance_row["application_summary_mismatch_count"] == "0"
    assert index.summary["changed_matrix_cell_count"] == 3


def test_acceptance_uses_requested_validation_scope(tmp_path: Path) -> None:
    inputs = _write_acceptance_inputs(tmp_path)

    index = acceptance.build_activation_acceptance(
        promotion_rows=_read_tsv(inputs["promotion"]),
        activation_decision_rows=_read_tsv(inputs["decisions"]),
        preflight_rows=_read_tsv(inputs["preflight"]),
        application_summary_rows=_read_tsv(inputs["application_summary"]),
        value_delta_rows=_read_tsv(inputs["value_delta"]),
        input_matrix_rows=_read_tsv(inputs["input_matrix"]),
        input_identity_rows=_read_tsv(inputs["input_identity"]),
        output_matrix_rows=_read_tsv(inputs["output_matrix"]),
        output_identity_rows=_read_tsv(inputs["output_identity"]),
        validation_scope="full_cohort_current_writer_matrix_diff",
    )

    assert index.acceptance_row["acceptance_status"] == "pass"
    assert (
        index.acceptance_row["validation_scope"]
        == "full_cohort_current_writer_matrix_diff"
    )
    assert index.acceptance_row["next_action"] == (
        "ready_for_full_cohort_reviewed_activation_acceptance"
    )


def test_acceptance_rejects_duplicate_activation_keys(tmp_path: Path) -> None:
    inputs = _write_acceptance_inputs(tmp_path)
    decisions = _read_tsv(inputs["decisions"])

    with pytest.raises(ValueError, match="duplicate activation_decision key"):
        acceptance.build_activation_acceptance(
            promotion_rows=_read_tsv(inputs["promotion"]),
            activation_decision_rows=[*decisions, decisions[0]],
            preflight_rows=_read_tsv(inputs["preflight"]),
            application_summary_rows=_read_tsv(inputs["application_summary"]),
            value_delta_rows=_read_tsv(inputs["value_delta"]),
            input_matrix_rows=_read_tsv(inputs["input_matrix"]),
            input_identity_rows=_read_tsv(inputs["input_identity"]),
            output_matrix_rows=_read_tsv(inputs["output_matrix"]),
            output_identity_rows=_read_tsv(inputs["output_identity"]),
        )


def test_acceptance_rejects_stale_input_schema(tmp_path: Path) -> None:
    inputs = _write_acceptance_inputs(tmp_path)
    promotion_rows = _read_tsv(inputs["promotion"])
    promotion_rows[0]["schema_version"] = "old_schema"

    with pytest.raises(ValueError, match="promotion row schema_version mismatch"):
        acceptance.build_activation_acceptance(
            promotion_rows=promotion_rows,
            activation_decision_rows=_read_tsv(inputs["decisions"]),
            preflight_rows=_read_tsv(inputs["preflight"]),
            application_summary_rows=_read_tsv(inputs["application_summary"]),
            value_delta_rows=_read_tsv(inputs["value_delta"]),
            input_matrix_rows=_read_tsv(inputs["input_matrix"]),
            input_identity_rows=_read_tsv(inputs["input_identity"]),
            output_matrix_rows=_read_tsv(inputs["output_matrix"]),
            output_identity_rows=_read_tsv(inputs["output_identity"]),
        )


def test_acceptance_cli_returns_nonzero_on_gate_failure(tmp_path: Path) -> None:
    inputs = _write_acceptance_inputs(tmp_path, unexpected_output_change=True)

    from tools.diagnostics import backfill_peakhypothesis_activation_acceptance as cli

    output_dir = tmp_path / "cli_fail"
    assert cli.main(_cli_args(inputs, output_dir)) == 1
    row = _read_tsv(
        output_dir / "backfill_peakhypothesis_activation_acceptance.tsv",
    )[0]
    assert row["acceptance_status"] == "fail"
    assert "unexpected_matrix_diff" in row["hard_fail_reasons"]


def test_acceptance_cli_writes_outputs(tmp_path: Path) -> None:
    inputs = _write_acceptance_inputs(tmp_path)

    from tools.diagnostics import backfill_peakhypothesis_activation_acceptance as cli

    output_dir = tmp_path / "cli_out"
    assert cli.main(_cli_args(inputs, output_dir)) == 0

    assert (output_dir / "backfill_peakhypothesis_activation_acceptance.tsv").is_file()
    assert (output_dir / "backfill_peakhypothesis_activation_matrix_diff.tsv").is_file()
    assert (
        output_dir / "backfill_peakhypothesis_activation_acceptance_summary.json"
    ).is_file()


def _write_acceptance_inputs(
    tmp_path: Path,
    *,
    unexpected_output_change: bool = False,
    expected_added_rows: bool = False,
) -> dict[str, Path]:
    promotion = tmp_path / "promotion.tsv"
    decisions = tmp_path / "decisions.tsv"
    preflight = tmp_path / "preflight.tsv"
    application_summary = tmp_path / "application_summary.tsv"
    value_delta = tmp_path / "value_delta.tsv"
    input_matrix = tmp_path / "input_matrix.tsv"
    input_identity = tmp_path / "input_identity.tsv"
    output_matrix = tmp_path / "output_matrix.tsv"
    output_identity = tmp_path / "output_identity.tsv"

    promoted = [
        ("FAM001", "S1", "101"),
        ("FAM002", "S2", "202"),
        ("FAM003", "S1", "303"),
    ]
    _write_tsv(
        promotion,
        [
            _promotion_row(family_id=family, sample=sample, value=value)
            for family, sample, value in promoted
        ],
    )
    _write_tsv(
        decisions,
        [
            {
                "feature_family_id": family,
                "candidate_container_id": family,
                "sample_id": sample,
                "peak_hypothesis_id": family,
                "activation_schema_version": ACTIVATION_DECISION_SCHEMA_VERSION,
                "activation_unit_scope": "peak_hypothesis",
                "activation_status": "auto_activate",
                "contract_rule_id": "machine_observed_sufficient_positive_identity",
                "product_effect": "accept_label_or_rescue",
                "activation_reason": "allowlisted_peakhypothesis_same_peak_backfill",
            }
            for family, sample, _value in promoted
        ],
    )
    _write_tsv(
        preflight,
        [
            {
                "peak_hypothesis_id": family,
                "feature_family_id": family,
                "sample_stem": sample,
                "schema_version": bridge.SCHEMA_VERSION,
                "promotion_decision": "promote_matrix_write",
                "preflight_status": "needs_activation",
                "bridge_action": "emit_activation_decision",
            }
            for family, sample, _value in promoted
        ],
    )
    _write_tsv(
        application_summary,
        [
            {
                "activation_application_schema_version": (
                    ACTIVATION_APPLICATION_SCHEMA_VERSION
                ),
                "application_status": "applied",
                "canonical_row_identity_ready": "TRUE",
                "decision_rows_total": "3",
                "input_matrix_rows": "3" if expected_added_rows else "4",
                "output_matrix_rows": "4",
                "matrix_cells_written": "3",
                "matrix_cells_blanked": "0",
                "matrix_value_conflict_cells": "0",
                "families_added_to_matrix": "1" if expected_added_rows else "0",
            },
        ],
    )
    _write_tsv(
        value_delta,
        [
            {
                "feature_family_id": family,
                "sample_id": sample,
                "peak_hypothesis_id": family,
                "activation_value_delta_schema_version": (
                    ACTIVATION_VALUE_DELTA_SCHEMA_VERSION
                ),
                "activation_unit_scope": "peak_hypothesis",
                "activation_status": "auto_activate",
                "contract_rule_id": "machine_observed_sufficient_positive_identity",
                "original_matrix_value": "",
                "activated_matrix_value": value,
                "matrix_value_kind": "backfill_activation",
                "matrix_value_source": "activation_values_tsv",
                "matrix_value_source_field": "projected_matrix_value",
                "matrix_value_source_detail": "matrix_only_activation_value",
                "matrix_value_source_artifact_schema_version": (
                    "backfill_test_projection_v1"
                ),
                "matrix_value_source_artifact_sha256": "a" * 64,
                "matrix_value_source_row_sha256": "b" * 64,
                "matrix_value_effect": "written",
                "value_changed": "TRUE",
            }
            for family, sample, value in promoted
        ],
    )
    if expected_added_rows:
        _write_tsv(
            input_identity,
            [
                _identity_row(1, "FAM001"),
                _identity_row(2, "FAM002"),
                _identity_row(3, "FAM004"),
            ],
        )
        _write_tsv(input_matrix, _matrix_rows(("101", "202", ""), blank=True))
    else:
        _write_tsv(
            input_identity,
            [
                _identity_row(1, "FAM001"),
                _identity_row(2, "FAM002"),
                _identity_row(3, "FAM003"),
                _identity_row(4, "FAM004"),
            ],
        )
        _write_tsv(input_matrix, _matrix_rows(("101", "202", "303", ""), blank=True))
    output_rows = _matrix_rows(("101", "202", "303", "404"), blank=False)
    if not unexpected_output_change:
        output_rows[-1]["S2"] = ""
    _write_tsv(output_matrix, output_rows)
    _write_tsv(
        output_identity,
        [
            _identity_row(1, "FAM001"),
            _identity_row(2, "FAM002"),
            _identity_row(3, "FAM003"),
            _identity_row(4, "FAM004"),
        ],
    )
    return {
        "promotion": promotion,
        "decisions": decisions,
        "preflight": preflight,
        "application_summary": application_summary,
        "value_delta": value_delta,
        "input_matrix": input_matrix,
        "input_identity": input_identity,
        "output_matrix": output_matrix,
        "output_identity": output_identity,
    }


def _cli_args(inputs: dict[str, Path], output_dir: Path) -> list[str]:
    return [
        "--promotion-cells-tsv",
        str(inputs["promotion"]),
        "--activation-decisions-tsv",
        str(inputs["decisions"]),
        "--activation-matrix-preflight-tsv",
        str(inputs["preflight"]),
        "--activation-application-summary-tsv",
        str(inputs["application_summary"]),
        "--activation-value-delta-tsv",
        str(inputs["value_delta"]),
        "--input-alignment-matrix-tsv",
        str(inputs["input_matrix"]),
        "--input-alignment-matrix-identity-tsv",
        str(inputs["input_identity"]),
        "--output-alignment-matrix-tsv",
        str(inputs["output_matrix"]),
        "--output-alignment-matrix-identity-tsv",
        str(inputs["output_identity"]),
        "--output-dir",
        str(output_dir),
        "--source-run-id",
        "unit-cli",
    ]


def _promotion_row(*, family_id: str, sample: str, value: str) -> dict[str, str]:
    return {
        "schema_version": "backfill_peakhypothesis_promotion_v1",
        "peak_hypothesis_id": family_id,
        "activation_unit_scope": "peak_hypothesis",
        "feature_family_id": family_id,
        "seed_group_id": f"seed::{family_id}",
        "sample_stem": sample,
        "promotion_decision": "promote_matrix_write",
        "promotion_reasons": "allowlisted_peakhypothesis_same_peak_backfill",
        "promotion_blockers": "",
        "current_production_status": "review_rescue",
        "current_raw_status": "rescued",
        "current_matrix_written": "FALSE",
        "shadow_reasons": "identity_supported_review",
        "projected_matrix_written": "FALSE",
        "projected_matrix_value": value,
        "area_policy": "standard_assessable_area",
        "area_uncertainty_state": "standard_assessable",
        "area_uncertainty_reason": "",
        "area_uncertainty_fraction": "",
        "area_uncertainty_fraction_status": "",
        "matrix_quantitative_use": "standard_quantitative_use",
        "product_authority_chain": "",
        "authority_source": "unit_test",
        "shadow_projection_sha256": "sha",
        "shadow_projection_row_sha256": f"row-sha-{family_id}",
    }


def _identity_row(index: int, family_id: str) -> dict[str, str]:
    family_number = int(family_id.removeprefix("FAM"))
    return legacy_matrix_identity_row(
        index=index,
        mz=str(family_number * 101),
        rt=str(family_number),
        peak_hypothesis_id=family_id,
    )


def _matrix_rows(
    values: tuple[str, ...],
    *,
    blank: bool,
) -> list[dict[str, str]]:
    if len(values) == 3:
        fam1, fam2, fam4 = values
        return [
            {"Mz": "101", "RT": "1", "S1": "" if blank else fam1, "S2": ""},
            {"Mz": "202", "RT": "2", "S1": "", "S2": "" if blank else fam2},
            {"Mz": "404", "RT": "4", "S1": "", "S2": fam4 if not blank else ""},
        ]
    fam1, fam2, fam3, fam4 = values
    return [
        {"Mz": "101", "RT": "1", "S1": "" if blank else fam1, "S2": ""},
        {"Mz": "202", "RT": "2", "S1": "", "S2": "" if blank else fam2},
        {"Mz": "303", "RT": "3", "S1": "" if blank else fam3, "S2": ""},
        {"Mz": "404", "RT": "4", "S1": "", "S2": fam4 if not blank else ""},
    ]


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
