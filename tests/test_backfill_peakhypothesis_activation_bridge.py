import csv
import json
from pathlib import Path

from tests.matrix_identity_fixtures import legacy_matrix_identity_row
from xic_extractor.alignment.shared_peak_identity_explanation.schema import (
    ACTIVATION_ACCEPTANCE_SCHEMA_VERSION,
    ACTIVATION_DECISION_SCHEMA_VERSION,
)
from xic_extractor.diagnostics import (
    backfill_peakhypothesis_activation_bridge as bridge,
)


def test_bridge_emits_existing_activation_sidecars_for_promoted_rows(
    tmp_path: Path,
) -> None:
    rows = [
        _promotion_row(
            peak_hypothesis_id="FAM_ADD::mode_1",
            family_id="FAM_ADD",
            sample="S2",
            decision="promote_matrix_write",
            value="321.5",
        ),
        _promotion_row(
            peak_hypothesis_id="FAM_BLOCKED",
            family_id="FAM_BLOCKED",
            sample="S1",
            decision="blocked",
            value="222",
            blockers="nonstandard_area_review_only",
        ),
    ]

    index = bridge.build_activation_bridge(
        rows,
        source_run_id="top14_user_standard_identity_support_20260609",
    )
    outputs = bridge.write_activation_bridge_outputs(tmp_path / "bridge", index)

    decisions = _read_tsv(outputs.activation_decisions_tsv)
    assert len(decisions) == 1
    decision = decisions[0]
    assert decision["activation_schema_version"] == (
        ACTIVATION_DECISION_SCHEMA_VERSION
    )
    assert decision["feature_family_id"] == "FAM_ADD"
    assert decision["candidate_container_id"] == "FAM_ADD"
    assert decision["sample_id"] == "S2"
    assert decision["peak_hypothesis_id"] == "FAM_ADD::mode_1"
    assert decision["activation_unit_scope"] == "peak_hypothesis"
    assert decision["activation_status"] == "auto_activate"
    assert decision["activation_action"] == "activate_pass"
    assert decision["product_effect"] == "accept_label_or_rescue"
    assert decision["contract_rule_id"] == (
        "machine_observed_sufficient_positive_identity"
    )
    assert decision["activation_reason"] == (
        "allowlisted_peakhypothesis_same_peak_backfill"
    )
    assert "shadow_row_sha:row-sha-FAM_ADD" in decision["source_evidence_tokens"]
    assert decision["diagnostic_only"] == "FALSE"

    acceptance = _read_tsv(outputs.activation_acceptance_tsv)[0]
    assert acceptance["activation_acceptance_schema_version"] == (
        ACTIVATION_ACCEPTANCE_SCHEMA_VERSION
    )
    assert acceptance["activation_decision_scope"] == (
        "backfill_peakhypothesis_promotion_rows"
    )
    assert acceptance["blast_radius_current"] == "FALSE"
    assert acceptance["decision_rows_total"] == "1"
    assert acceptance["assessed_rows"] == "2"
    assert acceptance["product_affecting_rows"] == "1"
    assert acceptance["auto_activate_count"] == "1"
    assert acceptance["not_applicable_count"] == "1"
    assert acceptance["acceptance_status"] == "fail"
    assert acceptance["hard_fail_reasons"] == (
        "activation_acceptance_requires_matrix_diff_validation"
    )
    assert acceptance["next_action"] == "run_activation_matrix_diff_smoke"


def test_bridge_uses_normal_peak_decision_as_activation_prerequisite() -> None:
    rows = [
        _promotion_row(
            peak_hypothesis_id="FAM_ADD::mode_1",
            family_id="FAM_ADD",
            sample="S2",
            decision="promote_matrix_write",
            value="321.5",
        ),
    ]

    index = bridge.build_activation_bridge(
        rows,
        normal_peak_decision_rows=[
            _normal_peak_decision_row(
                peak_hypothesis_id="FAM_ADD::mode_1",
                family_id="FAM_ADD",
                sample="S2",
            ),
        ],
    )

    assert len(index.activation_decision_rows) == 1
    assert index.summary["normal_peak_decision_input_count"] == 1
    assert index.summary["normal_peak_required_backfill_count"] == 1
    assert index.summary["normal_peak_decision_blocked_count"] == 0
    assert index.activation_acceptance_row["hard_fail_reasons"] == (
        "activation_acceptance_requires_matrix_diff_validation"
    )


def test_bridge_fails_closed_when_normal_peak_decision_is_not_required() -> None:
    rows = [
        _promotion_row(
            peak_hypothesis_id="FAM_ADD::mode_1",
            family_id="FAM_ADD",
            sample="S2",
            decision="promote_matrix_write",
            value="321.5",
        ),
    ]

    index = bridge.build_activation_bridge(
        rows,
        normal_peak_decision_rows=[
            _normal_peak_decision_row(
                peak_hypothesis_id="FAM_ADD::mode_1",
                family_id="FAM_ADD",
                sample="S2",
                normal_peak_decision="blocked",
                normal_peak_backfill_required="FALSE",
                blockers="manual_same_peak_not_supported",
            ),
        ],
    )

    assert index.activation_decision_rows == ()
    assert index.summary["normal_peak_decision_input_count"] == 1
    assert index.summary["normal_peak_required_backfill_count"] == 0
    assert index.summary["normal_peak_decision_blocked_count"] == 1
    assert index.activation_acceptance_row["hard_fail_reasons"] == (
        "normal_peak_decision_missing_or_not_required"
    )
    assert index.activation_acceptance_row["next_action"] == (
        "review_normal_peak_decision_before_activation"
    )


def test_bridge_cli_writes_activation_inputs(tmp_path: Path) -> None:
    promotion_tsv = tmp_path / "promotion.tsv"
    _write_tsv(
        promotion_tsv,
        bridge.PROMOTION_INPUT_REQUIRED_COLUMNS,
        [
            _promotion_row(
                peak_hypothesis_id="FAM_ADD::mode_1",
                family_id="FAM_ADD",
                sample="S2",
                decision="promote_matrix_write",
                value="321.5",
            ),
        ],
    )

    from tools.diagnostics import backfill_peakhypothesis_activation_bridge as cli

    assert cli.main(
        [
            "--promotion-cells-tsv",
            str(promotion_tsv),
            "--output-dir",
            str(tmp_path / "out"),
            "--source-run-id",
            "unit-test",
        ],
    ) == 0

    assert (tmp_path / "out" / "activation_decisions.tsv").is_file()
    assert (tmp_path / "out" / "activation_acceptance.tsv").is_file()
    assert (tmp_path / "out" / "activation_matrix_preflight.tsv").is_file()


def test_bridge_cli_accepts_normal_peak_decision_gate(tmp_path: Path) -> None:
    promotion_tsv = tmp_path / "promotion.tsv"
    normal_peak_tsv = tmp_path / "normal.tsv"
    _write_tsv(
        promotion_tsv,
        bridge.PROMOTION_INPUT_REQUIRED_COLUMNS,
        [
            _promotion_row(
                peak_hypothesis_id="FAM_ADD::mode_1",
                family_id="FAM_ADD",
                sample="S2",
                decision="promote_matrix_write",
                value="321.5",
            ),
        ],
    )
    _write_tsv(
        normal_peak_tsv,
        bridge.NORMAL_PEAK_DECISION_INPUT_REQUIRED_COLUMNS,
        [
            _normal_peak_decision_row(
                peak_hypothesis_id="FAM_ADD::mode_1",
                family_id="FAM_ADD",
                sample="S2",
            ),
        ],
    )

    from tools.diagnostics import backfill_peakhypothesis_activation_bridge as cli

    assert cli.main(
        [
            "--promotion-cells-tsv",
            str(promotion_tsv),
            "--normal-peak-decisions-tsv",
            str(normal_peak_tsv),
            "--output-dir",
            str(tmp_path / "out"),
            "--source-run-id",
            "unit-test",
        ],
    ) == 0

    summary = json.loads(
        (
            tmp_path
            / "out"
            / "backfill_peakhypothesis_activation_bridge_summary.json"
        ).read_text(encoding="utf-8"),
    )
    assert summary["normal_peak_decision_input_count"] == 1
    assert summary["normal_peak_required_backfill_count"] == 1
    assert summary["activation_decision_row_count"] == 1


def test_bridge_suppresses_rows_already_written_in_public_matrix() -> None:
    rows = [
        _promotion_row(
            peak_hypothesis_id="FAM_ADD::mode_1",
            family_id="FAM_ADD",
            sample="S2",
            decision="promote_matrix_write",
            value="321.5",
            current_matrix_written="TRUE",
        ),
    ]

    index = bridge.build_activation_bridge(
        rows,
        public_matrix_rows=(
            {
                "Mz": "200.2",
                "RT": "8.2",
                "S1": "",
                "S2": "321.5",
            },
        ),
        matrix_identity_rows=(
            legacy_matrix_identity_row(
                index=1,
                mz="200.2",
                rt="8.2",
                peak_hypothesis_id="FAM_ADD::mode_1",
                source_feature_family_ids="FAM_ADD",
            ),
        ),
    )

    assert index.activation_decision_rows == ()
    acceptance = index.activation_acceptance_row
    assert acceptance["decision_rows_total"] == "0"
    assert acceptance["product_affecting_rows"] == "0"
    assert acceptance["not_applicable_count"] == "1"
    assert acceptance["hard_fail_reasons"] == (
        "public_matrix_already_contains_promoted_cells"
    )
    assert acceptance["next_action"] == "investigate_projection_matrix_contract_drift"
    assert index.summary["public_matrix_already_written_count"] == 1
    assert index.summary["public_matrix_projection_conflict_count"] == 0
    assert index.summary["activation_decision_row_count"] == 0


def test_bridge_reports_public_matrix_projection_conflicts(tmp_path: Path) -> None:
    rows = [
        _promotion_row(
            peak_hypothesis_id="FAM_ADD::mode_1",
            family_id="FAM_ADD",
            sample="S2",
            decision="promote_matrix_write",
            value="321.5",
            current_matrix_written="FALSE",
        ),
    ]

    index = bridge.build_activation_bridge(
        rows,
        public_matrix_rows=(
            {
                "Mz": "200.2",
                "RT": "8.2",
                "S1": "",
                "S2": "321.5",
            },
        ),
        matrix_identity_rows=(
            legacy_matrix_identity_row(
                index=1,
                mz="200.2",
                rt="8.2",
                peak_hypothesis_id="FAM_ADD::mode_1",
                source_feature_family_ids="FAM_ADD",
            ),
        ),
    )
    outputs = bridge.write_activation_bridge_outputs(tmp_path / "bridge", index)

    assert index.activation_decision_rows == ()
    assert index.summary["public_matrix_already_written_count"] == 1
    assert index.summary["public_matrix_projection_conflict_count"] == 1
    acceptance = index.activation_acceptance_row
    assert acceptance["hard_fail_reasons"] == (
        "public_matrix_conflicts_with_projection_current_snapshot"
    )
    assert acceptance["next_action"] == (
        "rebuild_alignment_matrix_with_current_writer_before_activation"
    )
    preflight = _read_tsv(outputs.activation_matrix_preflight_tsv)
    assert preflight == [
        {
            "schema_version": "backfill_peakhypothesis_activation_bridge_v1",
            "peak_hypothesis_id": "FAM_ADD::mode_1",
            "feature_family_id": "FAM_ADD",
            "sample_stem": "S2",
            "promotion_decision": "promote_matrix_write",
            "projection_current_matrix_written": "FALSE",
            "public_matrix_written": "TRUE",
            "public_matrix_value": "321.5",
            "preflight_status": "projection_public_matrix_conflict",
            "bridge_action": "suppress_activation",
            "preflight_reason": (
                "public_matrix_value_conflicts_with_projection_current_matrix_written_FALSE"
            ),
        },
    ]


def test_bridge_reports_public_matrix_value_mismatch_for_already_written_row(
    tmp_path: Path,
) -> None:
    rows = [
        _promotion_row(
            peak_hypothesis_id="FAM_ADD::mode_1",
            family_id="FAM_ADD",
            sample="S2",
            decision="promote_matrix_write",
            value="321.5",
            current_matrix_written="TRUE",
        ),
    ]

    index = bridge.build_activation_bridge(
        rows,
        public_matrix_rows=(
            {
                "Mz": "200.2",
                "RT": "8.2",
                "S1": "",
                "S2": "999.5",
            },
        ),
        matrix_identity_rows=(
            legacy_matrix_identity_row(
                index=1,
                mz="200.2",
                rt="8.2",
                peak_hypothesis_id="FAM_ADD::mode_1",
                source_feature_family_ids="FAM_ADD",
            ),
        ),
    )
    outputs = bridge.write_activation_bridge_outputs(tmp_path / "bridge", index)

    assert index.activation_decision_rows == ()
    assert index.summary["public_matrix_projection_conflict_count"] == 1
    assert index.activation_acceptance_row["hard_fail_reasons"] == (
        "public_matrix_conflicts_with_projection_current_snapshot"
    )
    preflight = _read_tsv(outputs.activation_matrix_preflight_tsv)
    assert preflight[0]["preflight_status"] == "projection_public_matrix_conflict"
    assert preflight[0]["preflight_reason"] == (
        "public_matrix_value_conflicts_with_projected_matrix_value"
    )


def _promotion_row(
    *,
    peak_hypothesis_id: str,
    family_id: str,
    sample: str,
    decision: str,
    value: str,
    blockers: str = "",
    current_matrix_written: str = "FALSE",
) -> dict[str, str]:
    return {
        "schema_version": "backfill_peakhypothesis_promotion_v1",
        "peak_hypothesis_id": peak_hypothesis_id,
        "activation_unit_scope": "peak_hypothesis",
        "feature_family_id": family_id,
        "seed_group_id": f"seed::{family_id}",
        "sample_stem": sample,
        "promotion_decision": decision,
        "promotion_reasons": (
            "allowlisted_peakhypothesis_same_peak_backfill"
            if decision == "promote_matrix_write"
            else ""
        ),
        "promotion_blockers": blockers,
        "current_production_status": "review_rescue",
        "current_raw_status": "rescued",
        "current_matrix_written": current_matrix_written,
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
        "authority_source": "unit_test_review",
        "shadow_projection_sha256": "shadow-sha",
        "shadow_projection_row_sha256": f"row-sha-{family_id}",
    }


def _normal_peak_decision_row(
    *,
    peak_hypothesis_id: str,
    family_id: str,
    sample: str,
    normal_peak_decision: str = "require_backfill",
    normal_peak_backfill_required: str = "TRUE",
    blockers: str = "",
) -> dict[str, str]:
    return {
        "schema_version": "backfill_peakhypothesis_normal_peak_decision_v1",
        "source_run_id": "unit",
        "peak_hypothesis_id": peak_hypothesis_id,
        "activation_unit_scope": "peak_hypothesis",
        "feature_family_id": family_id,
        "seed_group_id": f"seed::{family_id}",
        "sample_stem": sample,
        "area_policy": "standard_assessable_area",
        "matrix_quantitative_use": "standard_quantitative_use",
        "promotion_decision": "promote_matrix_write",
        "full_cohort_matched_peak_hypothesis_id": "FAM_FULL_COHORT",
        "full_cohort_cell_status": "rescued",
        "full_cohort_primary_matrix_area": "321.5",
        "full_cohort_primary_matrix_area_source": "gaussian15_positive_asls_residual",
        "full_cohort_include_in_primary_matrix": "FALSE",
        "full_cohort_consolidation_state": "primary_loser",
        "manual_same_peak_verdict": "same_peak_supported",
        "normal_peak_shape_definition": (
            "gaussian15_asls_residual_selected_segment_single_complete_unimodal_peak;"
            "raw_spikes_neighbor_contact_family_multiplet_not_blockers"
        ),
        "normal_peak_decision": normal_peak_decision,
        "normal_peak_backfill_required": normal_peak_backfill_required,
        "normal_peak_decision_reasons": (
            "standard_peak_same_peak_supported;positive_gaussian15_area"
        ),
        "normal_peak_decision_blockers": blockers,
        "consolidation_policy_effect": (
            "allow_same_peak_peakhypothesis_candidate_despite_non_primary"
        ),
    }


def _write_tsv(
    path: Path,
    fieldnames: tuple[str, ...],
    rows: list[dict[str, str]],
) -> None:
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=fieldnames,
            delimiter="\t",
            lineterminator="\n",
        )
        writer.writeheader()
        writer.writerows(rows)


def _read_tsv(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle, delimiter="\t"))
