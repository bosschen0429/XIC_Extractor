import csv
from pathlib import Path

import pytest
from openpyxl import Workbook

from tools.diagnostics.apply_shared_peak_identity_activation import main
from xic_extractor.alignment.backfill_evidence_projection import (
    PRODUCT_AUTHORITY_SCOPE_FIELD,
    PRODUCT_AUTHORITY_SOURCE_FIELD,
    PRODUCT_AUTHORITY_STATUS_FIELD,
    PRODUCT_AUTHORIZED_SCOPE,
    PRODUCT_AUTHORIZED_STATUS,
)
from xic_extractor.alignment.promotion_policy import (
    ANCHOR_OWN_MAX_MS1_SUPPORT_REASON,
)
from xic_extractor.alignment.shared_peak_identity_explanation import (
    product_activation,
)
from xic_extractor.alignment.shared_peak_identity_explanation.schema import (
    RT_MODE_EVIDENCE_COLUMNS,
)


def test_activation_application_blanks_wrong_peak_and_writes_auto_activation(
    tmp_path: Path,
) -> None:
    fixture = _write_fixture(tmp_path, acceptance_status="pass")

    outputs = product_activation.apply_activation_to_alignment_outputs(
        activation_decisions_tsv=fixture["decisions"],
        activation_acceptance_tsv=fixture["acceptance"],
        alignment_matrix_tsv=fixture["matrix"],
        alignment_review_tsv=fixture["review"],
        alignment_cells_tsv=fixture["cells"],
        output_dir=tmp_path / "out",
    )

    matrix_rows = {
        row["feature_family_id"]: row for row in _read_tsv(outputs.matrix_tsv)
    }
    assert matrix_rows["FAM_BLOCK"]["S1"] == "100"
    assert matrix_rows["FAM_BLOCK"]["S2"] == ""
    assert matrix_rows["FAM_ADD"]["S2"] == "300"
    assert matrix_rows["FAM_KEEP"]["S2"] == "777"
    assert matrix_rows["FAM_SPLIT"]["S1"] == "111"
    assert matrix_rows["FAM_SPLIT"]["S2"] == "222"

    review_rows = {
        row["feature_family_id"]: row for row in _read_tsv(outputs.review_tsv)
    }
    assert review_rows["FAM_BLOCK"]["include_in_primary_matrix"] == "TRUE"
    assert review_rows["FAM_BLOCK"]["accepted_cell_count"] == "1"
    assert review_rows["FAM_BLOCK"]["accepted_rescue_count"] == "0"
    assert review_rows["FAM_BLOCK"]["activation_blocked_cell_count"] == "1"
    assert review_rows["FAM_BLOCK"]["activation_rules"] == "wrong_peak_conflict"
    assert review_rows["FAM_ADD"]["include_in_primary_matrix"] == "TRUE"
    assert review_rows["FAM_ADD"]["identity_decision"] == "provisional_discovery"
    assert review_rows["FAM_ADD"]["identity_reason"] == (
        "activation_peak_hypothesis_candidate"
    )
    assert review_rows["FAM_ADD"]["activation_written_cell_count"] == "1"

    cell_rows = {
        (row["feature_family_id"], row["sample_stem"]): row
        for row in _read_tsv(outputs.cells_tsv)
    }
    blocked = cell_rows[("FAM_BLOCK", "S2")]
    assert blocked["activation_status"] == "auto_block"
    assert blocked["activation_matrix_value_effect"] == "blanked"
    activated = cell_rows[("FAM_ADD", "S2")]
    assert activated["activation_status"] == "auto_activate"
    assert activated["activation_peak_hypothesis_id"] == "FAM_ADD::mode_1"
    assert activated["activation_unit_scope"] == "peak_hypothesis"
    assert activated["activation_matrix_value_effect"] == "written"
    kept = cell_rows[("FAM_KEEP", "S2")]
    assert kept["activation_status"] == "auto_activate"
    assert kept["activation_matrix_value_effect"] == "unchanged"
    split_blue = cell_rows[("FAM_SPLIT", "S1")]
    assert split_blue["activation_peak_hypothesis_id"] == "FAM_SPLIT::blue"
    split_green = cell_rows[("FAM_SPLIT", "S2")]
    assert split_green["activation_peak_hypothesis_id"] == "FAM_SPLIT::green"

    summary = _read_tsv(outputs.summary_tsv)[0]
    assert summary["application_status"] == "applied"
    assert summary["activation_output_mode"] == "activated-copy"
    assert summary["matrix_row_identity"] == "feature_family_id"
    assert summary["canonical_row_identity_ready"] == "FALSE"
    assert summary["canonical_row_identity_blockers"] == "formal_output_not_requested"
    assert summary["canonical_row_identity_scope"] == "legacy_feature_family_row"
    assert summary["family_projection_semantics"] == "not_applicable"
    assert summary["legacy_rt_row_context_authority"] == "not_applicable"
    assert summary["all_family_split_science_ready"] == "FALSE"
    assert summary["matrix_cells_blanked"] == "1"
    assert summary["matrix_cells_written"] == "1"
    assert summary["families_added_to_matrix"] == "1"

    delta_rows = {
        (row["feature_family_id"], row["sample_id"]): row
        for row in _read_tsv(outputs.value_delta_tsv)
    }
    blocked_delta = delta_rows[("FAM_BLOCK", "S2")]
    assert blocked_delta["original_matrix_value"] == "200"
    assert blocked_delta["activated_matrix_value"] == ""
    assert blocked_delta["source_cell_area"] == "200"
    assert blocked_delta["matrix_value_effect"] == "blanked"
    assert blocked_delta["value_changed"] == "TRUE"
    written_delta = delta_rows[("FAM_ADD", "S2")]
    assert written_delta["original_matrix_value"] == ""
    assert written_delta["activated_matrix_value"] == "300"
    assert written_delta["candidate_container_id"] == "FAM_ADD"
    assert written_delta["peak_hypothesis_id"] == "FAM_ADD::mode_1"
    assert written_delta["activation_unit_scope"] == "peak_hypothesis"
    assert written_delta["matrix_value_effect"] == "written"
    assert written_delta["value_changed"] == "TRUE"
    kept_delta = delta_rows[("FAM_KEEP", "S2")]
    assert kept_delta["original_matrix_value"] == "777"
    assert kept_delta["activated_matrix_value"] == "777"
    assert kept_delta["source_cell_area"] == "999"
    assert kept_delta["matrix_value_effect"] == "unchanged"
    assert kept_delta["value_changed"] == "FALSE"


def test_product_activation_private_write_tsv_preserves_raw_contract(
    tmp_path: Path,
) -> None:
    path = tmp_path / "nested" / "activation.tsv"

    product_activation._write_tsv(
        path,
        ("id", "value", "missing"),
        [{"id": "A", "value": "100", "extra": "ignored"}],
    )

    assert path.read_text(encoding="utf-8") == "id\tvalue\tmissing\nA\t100\t\n"

    empty_path = tmp_path / "empty" / "activation.tsv"
    product_activation._write_tsv(empty_path, ("id", "value"), [])
    assert empty_path.read_text(encoding="utf-8") == "id\tvalue\n"


def test_activation_application_accepts_ms1_morphology_primary_area_source(
    tmp_path: Path,
) -> None:
    fixture = _write_fixture(tmp_path, acceptance_status="pass")
    cell_rows = _read_tsv(fixture["cells"])
    for row in cell_rows:
        if row["feature_family_id"] == "FAM_ADD" and row["sample_stem"] == "S2":
            row["primary_matrix_area_source"] = (
                "gaussian15_positive_asls_residual"
            )
    _write_tsv(fixture["cells"], tuple(cell_rows[0]), cell_rows)

    outputs = product_activation.apply_activation_to_alignment_outputs(
        activation_decisions_tsv=fixture["decisions"],
        activation_acceptance_tsv=fixture["acceptance"],
        alignment_matrix_tsv=fixture["matrix"],
        alignment_review_tsv=fixture["review"],
        alignment_cells_tsv=fixture["cells"],
        output_dir=tmp_path / "out",
    )

    matrix_rows = {
        row["feature_family_id"]: row for row in _read_tsv(outputs.matrix_tsv)
    }
    assert matrix_rows["FAM_ADD"]["S2"] == "300"


def test_activation_application_requires_passing_acceptance(tmp_path: Path) -> None:
    fixture = _write_fixture(tmp_path, acceptance_status="fail")

    with pytest.raises(ValueError, match="activation acceptance must pass"):
        product_activation.apply_activation_to_alignment_outputs(
            activation_decisions_tsv=fixture["decisions"],
            activation_acceptance_tsv=fixture["acceptance"],
            alignment_matrix_tsv=fixture["matrix"],
            alignment_review_tsv=fixture["review"],
            alignment_cells_tsv=fixture["cells"],
            output_dir=tmp_path / "out",
        )


def test_activation_application_does_not_write_raw_area_without_primary_matrix_area(
    tmp_path: Path,
) -> None:
    fixture = _write_fixture(tmp_path, acceptance_status="pass")
    raw_cell_rows = _read_tsv(fixture["cells"])
    downgraded_rows = [
        {
            key: value
            for key, value in row.items()
            if key not in {"primary_matrix_area", "primary_matrix_area_source"}
        }
        for row in raw_cell_rows
    ]
    _write_tsv(
        fixture["cells"],
        ("feature_family_id", "sample_stem", "status", "area"),
        downgraded_rows,
    )

    outputs = product_activation.apply_activation_to_alignment_outputs(
        activation_decisions_tsv=fixture["decisions"],
        activation_acceptance_tsv=fixture["acceptance"],
        alignment_matrix_tsv=fixture["matrix"],
        alignment_review_tsv=fixture["review"],
        alignment_cells_tsv=fixture["cells"],
        output_dir=tmp_path / "out",
    )

    matrix_rows = {
        row["feature_family_id"]: row for row in _read_tsv(outputs.matrix_tsv)
    }
    assert "FAM_ADD" not in matrix_rows

    delta_rows = {
        (row["feature_family_id"], row["sample_id"]): row
        for row in _read_tsv(outputs.value_delta_tsv)
    }
    written_delta = delta_rows[("FAM_ADD", "S2")]
    assert written_delta["source_cell_area"] == "300"
    assert written_delta["activated_matrix_value"] == ""
    assert written_delta["matrix_value_effect"] == "missing_ms1_morphology_area"


def test_activation_application_rejects_asls_legacy_primary_area_source(
    tmp_path: Path,
) -> None:
    fixture = _write_fixture(tmp_path, acceptance_status="pass")
    cell_rows = _read_tsv(fixture["cells"])
    for row in cell_rows:
        if row["feature_family_id"] == "FAM_ADD" and row["sample_stem"] == "S2":
            row["primary_matrix_area_source"] = "asls_baseline_corrected"
    _write_tsv(fixture["cells"], tuple(cell_rows[0]), cell_rows)

    outputs = product_activation.apply_activation_to_alignment_outputs(
        activation_decisions_tsv=fixture["decisions"],
        activation_acceptance_tsv=fixture["acceptance"],
        alignment_matrix_tsv=fixture["matrix"],
        alignment_review_tsv=fixture["review"],
        alignment_cells_tsv=fixture["cells"],
        output_dir=tmp_path / "out",
    )

    matrix_rows = {
        row["feature_family_id"]: row for row in _read_tsv(outputs.matrix_tsv)
    }
    assert "FAM_ADD" not in matrix_rows

    delta_rows = {
        (row["feature_family_id"], row["sample_id"]): row
        for row in _read_tsv(outputs.value_delta_tsv)
    }
    written_delta = delta_rows[("FAM_ADD", "S2")]
    assert written_delta["source_cell_area"] == "300"
    assert written_delta["activated_matrix_value"] == ""
    assert written_delta["matrix_value_effect"] == "missing_ms1_morphology_area"


def test_activation_application_rejects_auto_activate_without_peak_hypothesis(
    tmp_path: Path,
) -> None:
    fixture = _write_fixture(tmp_path, acceptance_status="pass")
    _write_tsv(
        fixture["decisions"],
        (
            "feature_family_id",
            "sample_id",
            "activation_status",
            "activation_action",
            "product_label_candidate",
            "product_effect",
            "contract_rule_id",
            "peak_hypothesis_id",
            "activation_unit_scope",
            "activation_reason",
        ),
        [
            {
                "feature_family_id": "FAM_ADD",
                "sample_id": "S2",
                "activation_status": "auto_activate",
                "activation_action": "activate_pass",
                "product_label_candidate": "pass",
                "product_effect": "accept_label_or_rescue",
                "contract_rule_id": (
                    "machine_observed_sufficient_positive_identity"
                ),
                "activation_reason": "missing product unit",
            }
        ],
    )

    with pytest.raises(ValueError, match="auto_activate decisions require"):
        product_activation.apply_activation_to_alignment_outputs(
            activation_decisions_tsv=fixture["decisions"],
            activation_acceptance_tsv=fixture["acceptance"],
            alignment_matrix_tsv=fixture["matrix"],
            alignment_review_tsv=fixture["review"],
            alignment_cells_tsv=fixture["cells"],
            output_dir=tmp_path / "out",
        )


def test_activation_application_formal_mode_writes_product_contract_names(
    tmp_path: Path,
) -> None:
    fixture = _write_fixture(tmp_path, acceptance_status="pass")
    review_rows = _read_tsv(fixture["review"])
    next(
        row for row in review_rows if row["feature_family_id"] == "FAM_KEEP"
    )["family_center_mz"] = "300.300004"
    _write_tsv(fixture["review"], _header(fixture["review"]), review_rows)

    outputs = product_activation.apply_activation_to_alignment_outputs(
        activation_decisions_tsv=fixture["decisions"],
        activation_acceptance_tsv=fixture["acceptance"],
        alignment_matrix_tsv=fixture["matrix"],
        alignment_review_tsv=fixture["review"],
        alignment_cells_tsv=fixture["cells"],
        output_dir=tmp_path / "formal",
        output_mode="formal",
    )

    assert outputs.matrix_tsv == tmp_path / "formal" / "alignment_matrix.tsv"
    assert outputs.review_tsv == tmp_path / "formal" / "alignment_review.tsv"
    assert outputs.cells_tsv == tmp_path / "formal" / "alignment_cells.tsv"
    assert (
        outputs.hypothesis_identity_tsv
        == tmp_path / "formal" / "activation_hypothesis_identity.tsv"
    )
    assert not (tmp_path / "formal" / "alignment_matrix_activated.tsv").exists()
    assert _header(outputs.matrix_tsv) == ("Mz", "RT", "S1", "S2")
    assert outputs.hypothesis_identity_tsv is not None
    assert _header(outputs.hypothesis_identity_tsv)[:8] == (
        "peak_hypothesis_id",
        "feature_family_id",
        "candidate_container_id",
        "row_identity_basis",
        "legacy_rt_row_context_id",
        "neutral_loss_tag",
        "family_center_mz",
        "family_center_rt",
    )
    assert _header(outputs.review_tsv) == _header(fixture["review"])
    assert _header(outputs.cells_tsv) == _header(fixture["cells"])
    matrix_rows = _read_tsv(outputs.matrix_tsv)
    assert all("peak_hypothesis_id" not in row for row in matrix_rows)
    hypothesis_identity_tsv = outputs.hypothesis_identity_tsv
    assert hypothesis_identity_tsv is not None
    identity_rows = {
        row["peak_hypothesis_id"]: row
        for row in _read_tsv(hypothesis_identity_tsv)
    }
    assert identity_rows["FAM_ADD::mode_1"]["S2"] == "300"
    assert identity_rows["FAM_ADD::mode_1"]["row_identity_basis"] == (
        "split_peak_hypothesis"
    )
    assert identity_rows["FAM_KEEP::mode_1"]["S2"] == "777"
    assert identity_rows["FAM_KEEP::mode_1"]["family_center_mz"] == "300.300004"
    assert identity_rows["FAM_KEEP::mode_1"]["row_identity_basis"] == (
        "split_peak_hypothesis"
    )
    assert identity_rows["FAM_SPLIT::blue"]["S1"] == "111"
    assert identity_rows["FAM_SPLIT::blue"]["S2"] == ""
    assert identity_rows["FAM_SPLIT::blue"]["row_identity_basis"] == (
        "split_peak_hypothesis"
    )
    assert identity_rows["FAM_SPLIT::green"]["S1"] == ""
    assert identity_rows["FAM_SPLIT::green"]["S2"] == "222"
    assert identity_rows["FAM_SPLIT::green"]["row_identity_basis"] == (
        "split_peak_hypothesis"
    )
    assert "FAM_BLOCK::family_projection" not in identity_rows
    assert outputs.matrix_identity_tsv is not None
    matrix_identity_rows = {
        row["peak_hypothesis_id"]: row
        for row in _read_tsv(outputs.matrix_identity_tsv)
    }
    assert matrix_identity_rows["FAM_KEEP::mode_1"]["Mz"] == "300.300004"
    summary = _read_tsv(outputs.summary_tsv)[0]
    assert summary["activation_output_mode"] == "formal"
    assert summary["matrix_row_identity"] == "mz_rt_sample_columns"
    assert summary["canonical_row_identity_ready"] == "FALSE"
    assert summary["canonical_row_identity_blockers"] == (
        "family_projection_excluded_incomplete_scope"
    )
    assert summary["canonical_row_identity_scope"] == (
        "partial_canonical_peak_hypothesis_rows_only"
    )
    assert summary["family_projection_semantics"] == (
        "excluded_from_canonical_output"
    )
    assert summary["legacy_rt_row_context_authority"] == "not_applicable"
    assert summary["all_family_split_science_ready"] == "FALSE"


def test_activation_formal_accepts_public_mz_rt_matrix_with_identity_sidecar(
    tmp_path: Path,
) -> None:
    fixture = _write_fixture(tmp_path, acceptance_status="pass")
    public_matrix = _write_public_mz_rt_matrix_fixture(tmp_path, fixture)
    identity = _write_alignment_matrix_identity_fixture(tmp_path)

    outputs = product_activation.apply_activation_to_alignment_outputs(
        activation_decisions_tsv=fixture["decisions"],
        activation_acceptance_tsv=fixture["acceptance"],
        alignment_matrix_tsv=public_matrix,
        alignment_matrix_identity_tsv=identity,
        alignment_review_tsv=fixture["review"],
        alignment_cells_tsv=fixture["cells"],
        output_dir=tmp_path / "formal",
        output_mode="formal",
    )

    assert _header(outputs.matrix_tsv) == ("Mz", "RT", "S1", "S2")
    matrix_rows = _read_tsv(outputs.matrix_tsv)
    assert all("feature_family_id" not in row for row in matrix_rows)
    assert all("peak_hypothesis_id" not in row for row in matrix_rows)
    assert {
        (row["Mz"], row["RT"], row["S1"], row["S2"])
        for row in matrix_rows
    } == {
        ("100.1000", "7.15", "1.00000e+02", ""),
        ("200.2000", "8.2", "", "3.00000e+02"),
        ("300.3000", "9.35", "", "7.77000e+02"),
        ("400.4000", "10.45", "1.11000e+02", ""),
        ("400.4000", "10.45", "", "2.22000e+02"),
    }

    assert outputs.hypothesis_identity_tsv is not None
    hypothesis_identity_tsv = outputs.hypothesis_identity_tsv
    assert hypothesis_identity_tsv is not None
    identity_rows = {
        row["peak_hypothesis_id"]: row
        for row in _read_tsv(hypothesis_identity_tsv)
    }
    assert identity_rows["FAM_BLOCK"]["row_identity_basis"] == (
        "no_split_peak_hypothesis"
    )
    assert identity_rows["FAM_BLOCK"]["feature_family_id"] == "FAM_BLOCK"
    assert identity_rows["FAM_BLOCK"]["S2"] == ""
    assert identity_rows["FAM_ADD::mode_1"]["S2"] == "300"
    assert identity_rows["FAM_ADD::mode_1"]["row_identity_basis"] == (
        "split_peak_hypothesis"
    )
    assert identity_rows["FAM_KEEP::mode_1"]["S2"] == "777"
    assert identity_rows["FAM_KEEP::mode_1"]["row_identity_basis"] == (
        "split_peak_hypothesis"
    )
    assert identity_rows["FAM_SPLIT::blue"]["S1"] == "111"
    assert identity_rows["FAM_SPLIT::blue"]["row_identity_basis"] == (
        "split_peak_hypothesis"
    )
    assert identity_rows["FAM_SPLIT::green"]["S2"] == "222"
    assert identity_rows["FAM_SPLIT::green"]["row_identity_basis"] == (
        "split_peak_hypothesis"
    )

    assert outputs.matrix_identity_tsv == tmp_path / "formal" / (
        "alignment_matrix_identity.tsv"
    )
    matrix_identity_rows = _read_tsv(outputs.matrix_identity_tsv)
    assert [row["matrix_row_index"] for row in matrix_identity_rows] == [
        "1",
        "2",
        "3",
        "4",
        "5",
    ]
    for index, (matrix_row, identity_row) in enumerate(
        zip(matrix_rows, matrix_identity_rows, strict=True),
        start=1,
    ):
        product_activation._validate_identity_coordinate(
            index,
            matrix_row,
            identity_row,
        )
    matrix_identity_by_peak = {
        row["peak_hypothesis_id"]: row for row in matrix_identity_rows
    }
    assert matrix_identity_by_peak["FAM_BLOCK"]["source_feature_family_ids"] == (
        "FAM_BLOCK"
    )
    assert matrix_identity_by_peak["FAM_BLOCK"]["row_identity_basis"] == (
        "no_split_peak_hypothesis"
    )
    assert matrix_identity_by_peak["FAM_BLOCK"]["split_evaluation_status"] == (
        "complete_no_product_ready_split"
    )
    assert matrix_identity_by_peak["FAM_BLOCK"]["projection_status"] == (
        "not_projection"
    )
    assert matrix_identity_by_peak["FAM_BLOCK"]["parent_peak_hypothesis_id"] == ""
    assert matrix_identity_by_peak["FAM_ADD::mode_1"][
        "source_feature_family_ids"
    ] == "FAM_ADD"
    assert matrix_identity_by_peak["FAM_ADD::mode_1"]["row_identity_basis"] == (
        "split_peak_hypothesis"
    )
    assert matrix_identity_by_peak["FAM_ADD::mode_1"][
        "split_evaluation_status"
    ] == "complete_product_ready_split"
    assert matrix_identity_by_peak["FAM_ADD::mode_1"][
        "parent_peak_hypothesis_id"
    ] == "FAM_ADD"
    assert matrix_identity_by_peak["FAM_SPLIT::blue"][
        "source_feature_family_ids"
    ] == "FAM_SPLIT"
    assert matrix_identity_by_peak["FAM_SPLIT::blue"]["row_identity_basis"] == (
        "split_peak_hypothesis"
    )
    assert matrix_identity_by_peak["FAM_SPLIT::blue"][
        "split_evaluation_status"
    ] == "complete_product_ready_split"
    assert matrix_identity_by_peak["FAM_SPLIT::blue"][
        "parent_peak_hypothesis_id"
    ] == "FAM_SPLIT"

    summary = _read_tsv(outputs.summary_tsv)[0]
    assert summary["matrix_row_identity"] == "mz_rt_sample_columns"
    assert summary["canonical_row_identity_blockers"] == "none"
    assert summary["canonical_row_identity_ready"] == "TRUE"


def test_activation_formal_preserves_public_split_rows_sharing_source_family(
    tmp_path: Path,
) -> None:
    fixture = _write_fixture(tmp_path, acceptance_status="pass")
    _write_tsv(fixture["decisions"], _header(fixture["decisions"]), [])
    public_matrix = _write_public_split_mz_rt_matrix_fixture(tmp_path, fixture)
    identity = _write_split_alignment_matrix_identity_fixture(tmp_path)

    outputs = product_activation.apply_activation_to_alignment_outputs(
        activation_decisions_tsv=fixture["decisions"],
        activation_acceptance_tsv=fixture["acceptance"],
        alignment_matrix_tsv=public_matrix,
        alignment_matrix_identity_tsv=identity,
        alignment_review_tsv=fixture["review"],
        alignment_cells_tsv=fixture["cells"],
        output_dir=tmp_path / "formal",
        output_mode="formal",
    )

    hypothesis_identity_tsv = outputs.hypothesis_identity_tsv
    assert hypothesis_identity_tsv is not None
    identity_rows = {
        row["peak_hypothesis_id"]: row
        for row in _read_tsv(hypothesis_identity_tsv)
    }
    assert identity_rows["FAM_SPLIT::blue"]["feature_family_id"] == "FAM_SPLIT"
    assert identity_rows["FAM_SPLIT::blue"]["S1"] == "111"
    assert identity_rows["FAM_SPLIT::blue"]["S2"] == ""
    assert identity_rows["FAM_SPLIT::green"]["feature_family_id"] == "FAM_SPLIT"
    assert identity_rows["FAM_SPLIT::green"]["S1"] == ""
    assert identity_rows["FAM_SPLIT::green"]["S2"] == "222"

    matrix_identity_tsv = outputs.matrix_identity_tsv
    assert matrix_identity_tsv is not None
    matrix_identity_by_peak = {
        row["peak_hypothesis_id"]: row
        for row in _read_tsv(matrix_identity_tsv)
    }
    assert matrix_identity_by_peak["FAM_SPLIT::blue"][
        "source_feature_family_ids"
    ] == "FAM_SPLIT"
    assert matrix_identity_by_peak["FAM_SPLIT::green"][
        "source_feature_family_ids"
    ] == "FAM_SPLIT"

    review_rows = {
        row["feature_family_id"]: row for row in _read_tsv(outputs.review_tsv)
    }
    assert review_rows["FAM_SPLIT"]["include_in_primary_matrix"] == "TRUE"
    assert review_rows["FAM_SPLIT"]["accepted_cell_count"] == "2"


def test_activation_formal_rejects_public_identity_sidecar_with_legacy_basis(
    tmp_path: Path,
) -> None:
    fixture = _write_fixture(tmp_path, acceptance_status="pass")
    public_matrix = _write_public_mz_rt_matrix_fixture(tmp_path, fixture)
    identity = _write_alignment_matrix_identity_fixture(tmp_path)
    identity_rows = _read_tsv(identity)
    identity_rows[0]["row_identity_basis"] = "matrix_construction_peak_hypothesis"
    _write_tsv(identity, _header(identity), identity_rows)

    with pytest.raises(
        ValueError,
        match=(
            "public Mz/RT matrix identity row requires product "
            "row_identity_basis"
        ),
    ):
        product_activation.apply_activation_to_alignment_outputs(
            activation_decisions_tsv=fixture["decisions"],
            activation_acceptance_tsv=fixture["acceptance"],
            alignment_matrix_tsv=public_matrix,
            alignment_matrix_identity_tsv=identity,
            alignment_review_tsv=fixture["review"],
            alignment_cells_tsv=fixture["cells"],
            output_dir=tmp_path / "formal",
            output_mode="formal",
        )


def test_activation_formal_rejects_multi_family_hypothesis_collapse(
    tmp_path: Path,
) -> None:
    fixture = _write_fixture(tmp_path, acceptance_status="pass")
    decision_rows = _read_tsv(fixture["decisions"])
    decision_rows.append(
        {
            "feature_family_id": "FAM_OTHER",
            "sample_id": "S1",
            "activation_status": "auto_activate",
            "activation_action": "activate_pass",
            "product_label_candidate": "pass",
            "product_effect": "accept_label_or_rescue",
            "contract_rule_id": "machine_observed_sufficient_positive_identity",
            "peak_hypothesis_id": "FAM_ADD::mode_1",
            "activation_unit_scope": "peak_hypothesis",
            "activation_reason": "bad shared hypothesis id",
        }
    )
    _write_tsv(fixture["decisions"], _header(fixture["decisions"]), decision_rows)
    matrix_rows = _read_tsv(fixture["matrix"])
    matrix_rows.append(
        {
            "feature_family_id": "FAM_OTHER",
            "neutral_loss_tag": "DNA_dR",
            "family_center_mz": "201.2",
            "family_center_rt": "8.25",
            "S1": "444",
            "S2": "",
        }
    )
    _write_tsv(fixture["matrix"], _header(fixture["matrix"]), matrix_rows)
    review_rows = _read_tsv(fixture["review"])
    review_rows.append(
        {
            "feature_family_id": "FAM_OTHER",
            "neutral_loss_tag": "DNA_dR",
            "family_center_mz": "201.2",
            "family_center_rt": "8.25",
            "identity_decision": "provisional_discovery",
            "identity_confidence": "review",
            "identity_reason": "insufficient_detected_identity_support",
            "accepted_cell_count": "0",
            "accepted_rescue_count": "0",
            "include_in_primary_matrix": "FALSE",
        }
    )
    _write_tsv(fixture["review"], _header(fixture["review"]), review_rows)
    cell_rows = _read_tsv(fixture["cells"])
    cell_rows.append(
        {
            "feature_family_id": "FAM_OTHER",
            "sample_stem": "S1",
            "status": "detected",
            "area": "444",
            "primary_matrix_area": "444",
            "primary_matrix_area_source": "gaussian15_positive_asls_residual",
        }
    )
    _write_tsv(fixture["cells"], _header(fixture["cells"]), cell_rows)

    with pytest.raises(ValueError, match="exactly one source_feature_family_id"):
        product_activation.apply_activation_to_alignment_outputs(
            activation_decisions_tsv=fixture["decisions"],
            activation_acceptance_tsv=fixture["acceptance"],
            alignment_matrix_tsv=fixture["matrix"],
            alignment_review_tsv=fixture["review"],
            alignment_cells_tsv=fixture["cells"],
            output_dir=tmp_path / "formal",
            output_mode="formal",
        )


def test_activation_formal_uses_rt_mode_evidence_for_split_hypothesis_rt(
    tmp_path: Path,
) -> None:
    fixture = _write_fixture(tmp_path, acceptance_status="pass")

    outputs = product_activation.apply_activation_to_alignment_outputs(
        activation_decisions_tsv=fixture["decisions"],
        activation_acceptance_tsv=fixture["acceptance"],
        alignment_matrix_tsv=fixture["matrix"],
        alignment_review_tsv=fixture["review"],
        alignment_cells_tsv=fixture["cells"],
        output_dir=tmp_path / "formal",
        output_mode="formal",
        rt_mode_evidence_rows=[
            _rt_mode_row("FAM_SPLIT", "S1", mode_id="blue", raw_rt="10.1"),
            _rt_mode_row("FAM_SPLIT", "S2", mode_id="green", raw_rt="10.9"),
        ],
    )

    matrix_rows = _read_tsv(outputs.matrix_tsv)
    assert {
        (row["RT"], row["S1"], row["S2"])
        for row in matrix_rows
        if row["Mz"] == "400.4000"
    } == {
        ("10.1", "1.11000e+02", ""),
        ("10.9", "", "2.22000e+02"),
    }

    assert outputs.matrix_identity_tsv is not None
    matrix_identity_tsv = outputs.matrix_identity_tsv
    assert matrix_identity_tsv is not None
    matrix_identity_by_peak = {
        row["peak_hypothesis_id"]: row
        for row in _read_tsv(matrix_identity_tsv)
    }
    assert matrix_identity_by_peak["FAM_SPLIT::blue"]["RT"] == "10.1"
    assert matrix_identity_by_peak["FAM_SPLIT::blue"]["center_rt_basis"] == (
        "activation_rt_mode_area_weighted_raw_selected_rt"
    )
    assert matrix_identity_by_peak["FAM_SPLIT::green"]["RT"] == "10.9"
    assert "FAM_BLOCK::family_projection" not in matrix_identity_by_peak


def test_activation_formal_ignores_raw_overlay_rt_mode_for_product_rt(
    tmp_path: Path,
) -> None:
    fixture = _write_fixture(tmp_path, acceptance_status="pass")
    raw_overlay_row = _rt_mode_row(
        "FAM_SPLIT",
        "S1",
        mode_id="blue",
        raw_rt="10.1",
    )
    raw_overlay_row["rt_mode_evidence_level"] = "raw_selected_apex_modes"

    outputs = product_activation.apply_activation_to_alignment_outputs(
        activation_decisions_tsv=fixture["decisions"],
        activation_acceptance_tsv=fixture["acceptance"],
        alignment_matrix_tsv=fixture["matrix"],
        alignment_review_tsv=fixture["review"],
        alignment_cells_tsv=fixture["cells"],
        output_dir=tmp_path / "formal",
        output_mode="formal",
        rt_mode_evidence_rows=[raw_overlay_row],
    )

    matrix_identity_tsv = outputs.matrix_identity_tsv
    assert matrix_identity_tsv is not None
    matrix_identity_by_peak = {
        row["peak_hypothesis_id"]: row
        for row in _read_tsv(matrix_identity_tsv)
    }
    assert matrix_identity_by_peak["FAM_SPLIT::blue"]["RT"] == "10.4"
    assert matrix_identity_by_peak["FAM_SPLIT::blue"]["center_rt_basis"] == (
        "activation_hypothesis_family_center_rt"
    )


def test_activation_application_formal_mode_can_include_projection_legacy_rt_row_oracle(
    tmp_path: Path,
) -> None:
    fixture = _write_fixture(tmp_path, acceptance_status="pass")
    oracle = tmp_path / "legacy_mzmine_oracle.xlsx"
    _write_legacy_rt_row_oracle(oracle, rows=((100.1, 7.1),))

    outputs = product_activation.apply_activation_to_alignment_outputs(
        activation_decisions_tsv=fixture["decisions"],
        activation_acceptance_tsv=fixture["acceptance"],
        alignment_matrix_tsv=fixture["matrix"],
        alignment_review_tsv=fixture["review"],
        alignment_cells_tsv=fixture["cells"],
        output_dir=tmp_path / "formal",
        output_mode="formal",
        legacy_rt_row_oracle_xlsx=oracle,
        exclude_family_projections=False,
    )

    assert outputs.hypothesis_identity_tsv is not None
    identity_rows = {
        row["peak_hypothesis_id"]: row
        for row in _read_tsv(outputs.hypothesis_identity_tsv)
    }
    row = identity_rows["FAM_BLOCK::family_projection"]
    assert row["feature_family_id"] == "FAM_BLOCK"
    assert row["row_identity_basis"] == "family_projection_no_split_evidence"
    assert row["legacy_rt_row_context_id"] == (
        "mzmine_rtrow_2_mz100.1000_rt7.10min"
    )
    assert row["S1"] == "100"
    assert row["S2"] == ""

    summary = _read_tsv(outputs.summary_tsv)[0]
    assert summary["legacy_rt_row_context_rows"] == "1"
    assert summary["family_projection_rows"] == "1"
    assert summary["legacy_rt_row_context_authority"] == (
        "context_only_not_identity_authority"
    )


def test_activation_application_formal_mode_can_require_peak_hypothesis_identity(
    tmp_path: Path,
) -> None:
    fixture = _write_fixture(tmp_path, acceptance_status="pass")

    with pytest.raises(ValueError, match="family_projection_excluded_incomplete_scope"):
        product_activation.apply_activation_to_alignment_outputs(
            activation_decisions_tsv=fixture["decisions"],
            activation_acceptance_tsv=fixture["acceptance"],
            alignment_matrix_tsv=fixture["matrix"],
            alignment_review_tsv=fixture["review"],
            alignment_cells_tsv=fixture["cells"],
            output_dir=tmp_path / "formal",
            output_mode="formal",
            require_complete_peak_hypothesis_identity=True,
        )


def test_activation_application_formal_mode_excludes_projections_by_default(
    tmp_path: Path,
) -> None:
    fixture = _write_fixture(tmp_path, acceptance_status="pass")

    outputs = product_activation.apply_activation_to_alignment_outputs(
        activation_decisions_tsv=fixture["decisions"],
        activation_acceptance_tsv=fixture["acceptance"],
        alignment_matrix_tsv=fixture["matrix"],
        alignment_review_tsv=fixture["review"],
        alignment_cells_tsv=fixture["cells"],
        output_dir=tmp_path / "formal",
        output_mode="formal",
    )

    assert outputs.hypothesis_identity_tsv is not None
    identity_rows = {
        row["peak_hypothesis_id"]: row
        for row in _read_tsv(outputs.hypothesis_identity_tsv)
    }
    assert "FAM_BLOCK::family_projection" not in identity_rows
    assert identity_rows["FAM_ADD::mode_1"]["S2"] == "300"
    assert identity_rows["FAM_KEEP::mode_1"]["S2"] == "777"
    assert identity_rows["FAM_SPLIT::blue"]["S1"] == "111"
    assert identity_rows["FAM_SPLIT::green"]["S2"] == "222"

    summary = _read_tsv(outputs.summary_tsv)[0]
    assert summary["canonical_row_identity_ready"] == "FALSE"
    assert summary["canonical_row_identity_blockers"] == (
        "family_projection_excluded_incomplete_scope"
    )
    assert summary["canonical_row_identity_scope"] == (
        "partial_canonical_peak_hypothesis_rows_only"
    )
    assert summary["family_projection_semantics"] == (
        "excluded_from_canonical_output"
    )
    assert summary["family_projection_rows"] == "0"
    assert summary["family_projection_rows_excluded"] == "1"
    assert summary["family_projection_cells_excluded"] == "1"
    assert summary["all_family_split_science_ready"] == "FALSE"


def test_formal_matrix_helper_excludes_projections_by_default() -> None:
    rows, stats = product_activation._peak_hypothesis_matrix_rows(
        matrix_by_family={
            "FAM_BLOCK": {
                "feature_family_id": "FAM_BLOCK",
                "S1": "100",
            },
        },
        review_by_family={
            "FAM_BLOCK": {
                "feature_family_id": "FAM_BLOCK",
                "neutral_loss_tag": "DNA_dR",
                "family_center_mz": "100.1",
                "family_center_rt": "7.1",
            },
        },
        decisions_by_key={},
        sample_columns=("S1",),
        legacy_rt_row_oracle=(),
        legacy_rt_row_oracle_mz_ppm=20.0,
        legacy_rt_row_oracle_rt_tolerance_min=1.0,
    )

    assert rows == []
    assert stats.family_projection_rows == 0
    assert stats.family_projection_rows_excluded == 1
    assert stats.family_projection_cells_excluded == 1


def test_activation_application_formal_mode_refuses_excluded_projections_as_complete(
    tmp_path: Path,
) -> None:
    fixture = _write_fixture(tmp_path, acceptance_status="pass")

    with pytest.raises(ValueError, match="family_projection_excluded_incomplete_scope"):
        product_activation.apply_activation_to_alignment_outputs(
            activation_decisions_tsv=fixture["decisions"],
            activation_acceptance_tsv=fixture["acceptance"],
            alignment_matrix_tsv=fixture["matrix"],
            alignment_review_tsv=fixture["review"],
            alignment_cells_tsv=fixture["cells"],
            output_dir=tmp_path / "formal",
            output_mode="formal",
            exclude_family_projections=True,
            require_complete_peak_hypothesis_identity=True,
        )


def test_activation_application_formal_mode_rejects_cross_family_value_conflict(
    tmp_path: Path,
) -> None:
    fixture = _write_fixture(tmp_path, acceptance_status="pass")
    decisions = _read_tsv(fixture["decisions"])
    for row in decisions:
        if row["feature_family_id"] == "FAM_ADD":
            row["peak_hypothesis_id"] = "FAM_KEEP::mode_1"
    _write_tsv(fixture["decisions"], tuple(decisions[0]), decisions)

    with pytest.raises(ValueError, match="exactly one source_feature_family_id"):
        product_activation.apply_activation_to_alignment_outputs(
            activation_decisions_tsv=fixture["decisions"],
            activation_acceptance_tsv=fixture["acceptance"],
            alignment_matrix_tsv=fixture["matrix"],
            alignment_review_tsv=fixture["review"],
            alignment_cells_tsv=fixture["cells"],
            output_dir=tmp_path / "formal",
            output_mode="formal",
        )


def test_activation_application_formal_mode_refuses_source_overwrite(
    tmp_path: Path,
) -> None:
    fixture = _write_fixture(tmp_path, acceptance_status="pass")

    with pytest.raises(ValueError, match="overwrite source alignment artifacts"):
        product_activation.apply_activation_to_alignment_outputs(
            activation_decisions_tsv=fixture["decisions"],
            activation_acceptance_tsv=fixture["acceptance"],
            alignment_matrix_tsv=fixture["matrix"],
            alignment_review_tsv=fixture["review"],
            alignment_cells_tsv=fixture["cells"],
            output_dir=tmp_path,
            output_mode="formal",
        )


def test_activation_application_cli_writes_product_copies(tmp_path: Path) -> None:
    fixture = _write_fixture(tmp_path, acceptance_status="pass")

    assert (
        main(
            [
                "--activation-decisions-tsv",
                str(fixture["decisions"]),
                "--activation-acceptance-tsv",
                str(fixture["acceptance"]),
                "--alignment-matrix-tsv",
                str(fixture["matrix"]),
                "--alignment-review-tsv",
                str(fixture["review"]),
                "--alignment-cells-tsv",
                str(fixture["cells"]),
                "--output-dir",
                str(tmp_path / "out"),
            ]
        )
        == 0
    )
    assert (tmp_path / "out" / "alignment_matrix_activated.tsv").exists()
    assert (tmp_path / "out" / "activation_application_summary.tsv").exists()
    assert (tmp_path / "out" / "activation_value_delta.tsv").exists()


def test_activation_application_cli_projects_backfill_evidence_sidecars(
    tmp_path: Path,
) -> None:
    fixture = _write_fixture(tmp_path, acceptance_status="pass")
    sidecars = _write_backfill_evidence_sidecars(tmp_path)

    assert (
        main(
            [
                "--activation-decisions-tsv",
                str(fixture["decisions"]),
                "--activation-acceptance-tsv",
                str(fixture["acceptance"]),
                "--alignment-matrix-tsv",
                str(fixture["matrix"]),
                "--alignment-review-tsv",
                str(fixture["review"]),
                "--alignment-cells-tsv",
                str(fixture["cells"]),
                "--candidate-ms2-pattern-evidence-tsv",
                str(sidecars["candidate_ms2_pattern"]),
                "--ms1-pattern-coherence-evidence-tsv",
                str(sidecars["ms1_pattern_coherence"]),
                "--qc-ms1-pattern-reference-evidence-tsv",
                str(sidecars["qc_ms1_pattern_reference"]),
                "--matrix-rt-drift-policy-tsv",
                str(sidecars["matrix_rt_drift_policy"]),
                "--output-dir",
                str(tmp_path / "out"),
            ]
        )
        == 0
    )

    rows = {
        (row["feature_family_id"], row["sample_stem"]): row
        for row in _read_tsv(tmp_path / "out" / "alignment_cells_activated.tsv")
    }
    row = rows[("FAM_ADD", "S2")]
    assert row["backfill_ms1_pattern_status"] == "supportive"
    assert row["backfill_ms1_pattern_evidence_level"] == "trace_constellation"
    assert ANCHOR_OWN_MAX_MS1_SUPPORT_REASON in row["backfill_evidence_reason"]
    assert row["backfill_qc_reference_status"] == "supportive"
    assert row["backfill_matrix_rt_drift_status"] == "drift_supported"
    assert row["backfill_candidate_ms2_pattern_status"] == "not_observed"
    assert row["backfill_dda_missing_nl_policy_status"] == "not_dispositive"
    assert row["backfill_family_ms2_required_tag_status"] == ""


def test_activation_application_cli_formal_mode_writes_product_contract_names(
    tmp_path: Path,
) -> None:
    fixture = _write_fixture(tmp_path, acceptance_status="pass")

    assert (
        main(
            [
                "--activation-decisions-tsv",
                str(fixture["decisions"]),
                "--activation-acceptance-tsv",
                str(fixture["acceptance"]),
                "--alignment-matrix-tsv",
                str(fixture["matrix"]),
                "--alignment-review-tsv",
                str(fixture["review"]),
                "--alignment-cells-tsv",
                str(fixture["cells"]),
                "--output-dir",
                str(tmp_path / "formal"),
                "--output-mode",
                "formal",
            ]
        )
        == 0
    )
    assert (tmp_path / "formal" / "alignment_matrix.tsv").exists()
    assert (tmp_path / "formal" / "alignment_review.tsv").exists()
    assert (tmp_path / "formal" / "alignment_cells.tsv").exists()
    assert (tmp_path / "formal" / "activation_value_delta.tsv").exists()


def test_activation_application_cli_formal_mode_accepts_public_mz_rt_matrix(
    tmp_path: Path,
) -> None:
    fixture = _write_fixture(tmp_path, acceptance_status="pass")
    public_matrix = _write_public_mz_rt_matrix_fixture(tmp_path, fixture)
    identity = _write_alignment_matrix_identity_fixture(tmp_path)

    assert (
        main(
            [
                "--activation-decisions-tsv",
                str(fixture["decisions"]),
                "--activation-acceptance-tsv",
                str(fixture["acceptance"]),
                "--alignment-matrix-tsv",
                str(public_matrix),
                "--alignment-matrix-identity-tsv",
                str(identity),
                "--alignment-review-tsv",
                str(fixture["review"]),
                "--alignment-cells-tsv",
                str(fixture["cells"]),
                "--output-dir",
                str(tmp_path / "formal"),
                "--output-mode",
                "formal",
            ]
        )
        == 0
    )

    assert _header(tmp_path / "formal" / "alignment_matrix.tsv") == (
        "Mz",
        "RT",
        "S1",
        "S2",
    )
    assert (
        tmp_path / "formal" / "activation_hypothesis_identity.tsv"
    ).exists()


def test_activation_application_cli_matrix_only_uses_activation_values_without_cells(
    tmp_path: Path,
) -> None:
    fixture = _write_fixture(tmp_path, acceptance_status="pass")
    public_matrix = _write_public_mz_rt_matrix_fixture(tmp_path, fixture)
    identity = _write_alignment_matrix_identity_fixture(tmp_path)
    matrix_rows = _read_tsv(public_matrix)
    matrix_rows[0]["Mz"] = "100.1235"
    _write_tsv(public_matrix, _header(public_matrix), matrix_rows)
    identity_input_rows = _read_tsv(identity)
    identity_input_rows[0]["Mz"] = "100.123456"
    _write_tsv(identity, _header(identity), identity_input_rows)
    _bind_public_matrix_identity_v2(public_matrix, identity)
    review_rows = _read_tsv(fixture["review"])
    next(
        row for row in review_rows if row["feature_family_id"] == "FAM_ADD"
    )["family_center_mz"] = "200.234567"
    _write_tsv(fixture["review"], _header(fixture["review"]), review_rows)
    decisions = tmp_path / "matrix_only_activation_decisions.tsv"
    activation_values = tmp_path / "activation_values.tsv"
    _write_tsv(
        decisions,
        (
            "feature_family_id",
            "candidate_container_id",
            "sample_id",
            "peak_hypothesis_id",
            "activation_unit_scope",
            "activation_status",
            "product_effect",
            "contract_rule_id",
            "activation_reason",
        ),
        [
            {
                "feature_family_id": "FAM_ADD",
                "candidate_container_id": "FAM_ADD",
                "sample_id": "S2",
                "peak_hypothesis_id": "FAM_ADD::mode_1",
                "activation_unit_scope": "peak_hypothesis",
                "activation_status": "auto_activate",
                "product_effect": "accept_label_or_rescue",
                "contract_rule_id": (
                    "machine_observed_sufficient_positive_identity"
                ),
                "activation_reason": "matrix-only normal peak",
            }
        ],
    )
    _write_tsv(
        activation_values,
        (
            "peak_hypothesis_id",
            "feature_family_id",
            "sample_stem",
            "projected_matrix_value",
            "projected_matrix_value_source",
            "current_raw_status",
            "current_production_status",
            "source_artifact_schema_version",
            "source_artifact_sha256",
            "source_row_sha256",
            "source_provenance_detail",
        ),
        [
            {
                "peak_hypothesis_id": "FAM_ADD::mode_1",
                "feature_family_id": "FAM_ADD",
                "sample_stem": "S2",
                "projected_matrix_value": "300",
                "projected_matrix_value_source": (
                    "gaussian15_positive_asls_residual"
                ),
                "current_raw_status": "rescued",
                "current_production_status": "rescued",
                "source_artifact_schema_version": "backfill_test_projection_v1",
                "source_artifact_sha256": "a" * 64,
                "source_row_sha256": "b" * 64,
                "source_provenance_detail": "unit_test_activation_value",
            }
        ],
    )

    assert (
        main(
            [
                "--matrix-only",
                "--activation-values-tsv",
                str(activation_values),
                "--activation-decisions-tsv",
                str(decisions),
                "--activation-acceptance-tsv",
                str(fixture["acceptance"]),
                "--alignment-matrix-tsv",
                str(public_matrix),
                "--alignment-matrix-identity-tsv",
                str(identity),
                "--alignment-review-tsv",
                str(fixture["review"]),
                "--output-dir",
                str(tmp_path / "matrix_only"),
            ]
        )
        == 0
    )

    matrix_rows = _read_tsv(tmp_path / "matrix_only" / "alignment_matrix.tsv")
    identity_rows = _read_tsv(
        tmp_path / "matrix_only" / "alignment_matrix_identity.tsv"
    )
    add_index = next(
        index
        for index, row in enumerate(identity_rows)
        if row["peak_hypothesis_id"] == "FAM_ADD::mode_1"
    )
    block_index = next(
        index
        for index, row in enumerate(identity_rows)
        if row["peak_hypothesis_id"] == "FAM_BLOCK"
    )
    assert matrix_rows[block_index]["Mz"] == "100.1235"
    assert identity_rows[block_index]["Mz"] == "100.123456"
    assert matrix_rows[add_index]["Mz"] == "200.2346"
    assert identity_rows[add_index]["Mz"] == "200.234567"
    assert matrix_rows[add_index]["S2"] == "3.00000e+02"
    assert (tmp_path / "matrix_only" / "activation_value_delta.tsv").exists()
    assert (
        tmp_path / "matrix_only" / "activation_hypothesis_identity.tsv"
    ).exists()
    assert not (tmp_path / "matrix_only" / "alignment_cells.tsv").exists()
    assert not (tmp_path / "matrix_only" / "alignment_review.tsv").exists()
    summary = _read_tsv(
        tmp_path / "matrix_only" / "activation_application_summary.tsv"
    )[0]
    assert summary["activation_output_mode"] == "matrix-only"
    assert summary["matrix_cells_written"] == "1"
    assert summary["families_added_to_matrix"] == "1"
    delta = _read_tsv(tmp_path / "matrix_only" / "activation_value_delta.tsv")[0]
    assert delta["activated_matrix_value"] == "300"
    assert delta["source_cell_status"] == "rescued"
    assert delta["matrix_value_kind"] == "backfill_activation"
    assert delta["matrix_value_source"] == "activation_values_tsv"
    assert delta["matrix_value_source_field"] == "projected_matrix_value"
    assert delta["matrix_value_source_detail"] == "gaussian15_positive_asls_residual"
    assert (
        delta["matrix_value_source_artifact_schema_version"]
        == "backfill_test_projection_v1"
    )
    assert delta["matrix_value_source_artifact_sha256"] == "a" * 64
    assert delta["matrix_value_source_row_sha256"] == "b" * 64


def test_activation_application_matrix_only_rejects_unprovenanced_values(
    tmp_path: Path,
) -> None:
    fixture = _write_fixture(tmp_path, acceptance_status="pass")
    public_matrix = _write_public_mz_rt_matrix_fixture(tmp_path, fixture)
    identity = _write_alignment_matrix_identity_fixture(tmp_path)
    decisions = tmp_path / "matrix_only_activation_decisions.tsv"
    activation_values = tmp_path / "activation_values.tsv"
    _write_tsv(
        decisions,
        (
            "feature_family_id",
            "candidate_container_id",
            "sample_id",
            "peak_hypothesis_id",
            "activation_unit_scope",
            "activation_status",
            "product_effect",
            "contract_rule_id",
            "activation_reason",
        ),
        [
            {
                "feature_family_id": "FAM_ADD",
                "candidate_container_id": "FAM_ADD",
                "sample_id": "S2",
                "peak_hypothesis_id": "FAM_ADD::mode_1",
                "activation_unit_scope": "peak_hypothesis",
                "activation_status": "auto_activate",
                "product_effect": "accept_label_or_rescue",
                "contract_rule_id": (
                    "machine_observed_sufficient_positive_identity"
                ),
                "activation_reason": "matrix-only normal peak",
            }
        ],
    )
    _write_tsv(
        activation_values,
        (
            "peak_hypothesis_id",
            "feature_family_id",
            "sample_stem",
            "projected_matrix_value",
        ),
        [
            {
                "peak_hypothesis_id": "FAM_ADD::mode_1",
                "feature_family_id": "FAM_ADD",
                "sample_stem": "S2",
                "projected_matrix_value": "300",
            }
        ],
    )

    with pytest.raises(ValueError, match="activation_values.tsv missing columns"):
        product_activation.apply_activation_to_alignment_matrix_outputs(
            activation_decisions_tsv=decisions,
            activation_acceptance_tsv=fixture["acceptance"],
            activation_values_tsv=activation_values,
            alignment_matrix_tsv=public_matrix,
            alignment_matrix_identity_tsv=identity,
            alignment_review_tsv=fixture["review"],
            output_dir=tmp_path / "matrix_only",
        )


def test_activation_application_cli_formal_mode_uses_rt_mode_evidence(
    tmp_path: Path,
) -> None:
    fixture = _write_fixture(tmp_path, acceptance_status="pass")
    rt_mode = _write_rt_mode_evidence_sidecar(tmp_path)

    assert (
        main(
            [
                "--activation-decisions-tsv",
                str(fixture["decisions"]),
                "--activation-acceptance-tsv",
                str(fixture["acceptance"]),
                "--alignment-matrix-tsv",
                str(fixture["matrix"]),
                "--alignment-review-tsv",
                str(fixture["review"]),
                "--alignment-cells-tsv",
                str(fixture["cells"]),
                "--rt-mode-evidence-tsv",
                str(rt_mode),
                "--output-dir",
                str(tmp_path / "formal"),
                "--output-mode",
                "formal",
            ]
        )
        == 0
    )

    rows = _read_tsv(tmp_path / "formal" / "alignment_matrix.tsv")
    assert {
        (row["RT"], row["S1"], row["S2"])
        for row in rows
        if row["Mz"] == "400.4000"
    } == {
        ("10.1", "1.11000e+02", ""),
        ("10.9", "", "2.22000e+02"),
    }


def test_activation_application_cli_formal_mode_rejects_legacy_context_as_complete(
    tmp_path: Path,
) -> None:
    fixture = _write_fixture(tmp_path, acceptance_status="pass")
    oracle = tmp_path / "legacy_mzmine_oracle.xlsx"
    _write_legacy_rt_row_oracle(oracle, rows=((100.1, 7.1),))

    assert (
        main(
            [
                "--activation-decisions-tsv",
                str(fixture["decisions"]),
                "--activation-acceptance-tsv",
                str(fixture["acceptance"]),
                "--alignment-matrix-tsv",
                str(fixture["matrix"]),
                "--alignment-review-tsv",
                str(fixture["review"]),
                "--alignment-cells-tsv",
                str(fixture["cells"]),
                "--output-dir",
                str(tmp_path / "formal"),
                "--output-mode",
                "formal",
                "--require-complete-peak-hypothesis-identity",
                "--legacy-rt-row-oracle-xlsx",
                str(oracle),
            ]
        )
        == 2
    )


def test_activation_application_cli_excludes_projections_by_default(
    tmp_path: Path,
) -> None:
    fixture = _write_fixture(tmp_path, acceptance_status="pass")

    assert (
        main(
            [
                "--activation-decisions-tsv",
                str(fixture["decisions"]),
                "--activation-acceptance-tsv",
                str(fixture["acceptance"]),
                "--alignment-matrix-tsv",
                str(fixture["matrix"]),
                "--alignment-review-tsv",
                str(fixture["review"]),
                "--alignment-cells-tsv",
                str(fixture["cells"]),
                "--output-dir",
                str(tmp_path / "formal"),
                "--output-mode",
                "formal",
            ]
        )
        == 0
    )

    identity_rows = {
        row["peak_hypothesis_id"]: row
        for row in _read_tsv(
            tmp_path / "formal" / "activation_hypothesis_identity.tsv"
        )
    }
    assert "FAM_BLOCK::family_projection" not in identity_rows
    summary = _read_tsv(
        tmp_path / "formal" / "activation_application_summary.tsv"
    )[0]
    assert summary["canonical_row_identity_ready"] == "FALSE"
    assert summary["family_projection_rows_excluded"] == "1"


def test_activation_application_cli_can_include_projections_for_diagnostics(
    tmp_path: Path,
) -> None:
    fixture = _write_fixture(tmp_path, acceptance_status="pass")

    assert (
        main(
            [
                "--activation-decisions-tsv",
                str(fixture["decisions"]),
                "--activation-acceptance-tsv",
                str(fixture["acceptance"]),
                "--alignment-matrix-tsv",
                str(fixture["matrix"]),
                "--alignment-review-tsv",
                str(fixture["review"]),
                "--alignment-cells-tsv",
                str(fixture["cells"]),
                "--output-dir",
                str(tmp_path / "formal"),
                "--output-mode",
                "formal",
                "--include-family-projections",
            ]
        )
        == 0
    )

    identity_rows = {
        row["peak_hypothesis_id"]: row
        for row in _read_tsv(
            tmp_path / "formal" / "activation_hypothesis_identity.tsv"
        )
    }
    assert identity_rows["FAM_BLOCK::family_projection"][
        "row_identity_basis"
    ] == "family_projection_no_split_evidence"
    summary = _read_tsv(
        tmp_path / "formal" / "activation_application_summary.tsv"
    )[0]
    assert summary["canonical_row_identity_blockers"] == "family_projection_present"
    assert summary["family_projection_rows"] == "1"


def test_activation_application_cli_refuses_excluded_projections_as_complete(
    tmp_path: Path,
) -> None:
    fixture = _write_fixture(tmp_path, acceptance_status="pass")

    assert (
        main(
            [
                "--activation-decisions-tsv",
                str(fixture["decisions"]),
                "--activation-acceptance-tsv",
                str(fixture["acceptance"]),
                "--alignment-matrix-tsv",
                str(fixture["matrix"]),
                "--alignment-review-tsv",
                str(fixture["review"]),
                "--alignment-cells-tsv",
                str(fixture["cells"]),
                "--output-dir",
                str(tmp_path / "formal"),
                "--output-mode",
                "formal",
                "--exclude-family-projections",
                "--require-complete-peak-hypothesis-identity",
            ]
        )
        == 2
    )


def _write_fixture(tmp_path: Path, *, acceptance_status: str) -> dict[str, Path]:
    decisions = tmp_path / "activation_decisions.tsv"
    acceptance = tmp_path / "activation_acceptance.tsv"
    matrix = tmp_path / "alignment_matrix.tsv"
    review = tmp_path / "alignment_review.tsv"
    cells = tmp_path / "alignment_cells.tsv"
    _write_tsv(
        decisions,
        (
            "feature_family_id",
            "sample_id",
            "activation_status",
            "activation_action",
            "product_label_candidate",
            "product_effect",
            "contract_rule_id",
            "peak_hypothesis_id",
            "activation_unit_scope",
            "activation_reason",
        ),
        [
            {
                "feature_family_id": "FAM_BLOCK",
                "sample_id": "S2",
                "activation_status": "auto_block",
                "activation_action": "block_rescue",
                "product_label_candidate": "fail",
                "product_effect": "block_rescue_cell",
                "contract_rule_id": "wrong_peak_conflict",
                "activation_reason": "wrong peak",
            },
            {
                "feature_family_id": "FAM_ADD",
                "sample_id": "S2",
                "activation_status": "auto_activate",
                "activation_action": "activate_pass",
                "product_label_candidate": "pass",
                "product_effect": "accept_label_or_rescue",
                "contract_rule_id": (
                    "machine_observed_sufficient_positive_identity"
                ),
                "peak_hypothesis_id": "FAM_ADD::mode_1",
                "activation_unit_scope": "peak_hypothesis",
                "activation_reason": "machine observed",
            },
            {
                "feature_family_id": "FAM_KEEP",
                "sample_id": "S2",
                "activation_status": "auto_activate",
                "activation_action": "activate_pass",
                "product_label_candidate": "pass",
                "product_effect": "accept_label_or_rescue",
                "contract_rule_id": (
                    "machine_observed_sufficient_positive_identity"
                ),
                "peak_hypothesis_id": "FAM_KEEP::mode_1",
                "activation_unit_scope": "peak_hypothesis",
                "activation_reason": "already accepted",
            },
            {
                "feature_family_id": "FAM_SPLIT",
                "sample_id": "S1",
                "activation_status": "auto_activate",
                "activation_action": "activate_pass",
                "product_label_candidate": "pass",
                "product_effect": "accept_label_or_rescue",
                "contract_rule_id": (
                    "machine_observed_sufficient_positive_identity"
                ),
                "peak_hypothesis_id": "FAM_SPLIT::blue",
                "activation_unit_scope": "peak_hypothesis",
                "activation_reason": "mode split blue",
            },
            {
                "feature_family_id": "FAM_SPLIT",
                "sample_id": "S2",
                "activation_status": "auto_activate",
                "activation_action": "activate_pass",
                "product_label_candidate": "pass",
                "product_effect": "accept_label_or_rescue",
                "contract_rule_id": (
                    "machine_observed_sufficient_positive_identity"
                ),
                "peak_hypothesis_id": "FAM_SPLIT::green",
                "activation_unit_scope": "peak_hypothesis",
                "activation_reason": "mode split green",
            },
        ],
    )
    _write_tsv(
        acceptance,
        ("acceptance_status", "blast_radius_current", "decision_rows_total"),
        [
            {
                "acceptance_status": acceptance_status,
                "blast_radius_current": "TRUE",
                "decision_rows_total": "5",
            }
        ],
    )
    _write_tsv(
        matrix,
        (
            "feature_family_id",
            "neutral_loss_tag",
            "family_center_mz",
            "family_center_rt",
            "S1",
            "S2",
        ),
        [
            {
                "feature_family_id": "FAM_BLOCK",
                "neutral_loss_tag": "DNA_dR",
                "family_center_mz": "100.1",
                "family_center_rt": "7.1",
                "S1": "100",
                "S2": "200",
            },
            {
                "feature_family_id": "FAM_KEEP",
                "neutral_loss_tag": "DNA_dR",
                "family_center_mz": "300.3",
                "family_center_rt": "9.3",
                "S1": "",
                "S2": "777",
            },
            {
                "feature_family_id": "FAM_SPLIT",
                "neutral_loss_tag": "DNA_dR",
                "family_center_mz": "400.4",
                "family_center_rt": "10.4",
                "S1": "111",
                "S2": "222",
            }
        ],
    )
    _write_tsv(
        review,
        (
            "feature_family_id",
            "neutral_loss_tag",
            "family_center_mz",
            "family_center_rt",
            "identity_decision",
            "identity_confidence",
            "identity_reason",
            "accepted_cell_count",
            "accepted_rescue_count",
            "include_in_primary_matrix",
        ),
        [
            {
                "feature_family_id": "FAM_BLOCK",
                "neutral_loss_tag": "DNA_dR",
                "family_center_mz": "100.1",
                "family_center_rt": "7.1",
                "identity_decision": "production_family",
                "identity_confidence": "high",
                "identity_reason": "owner_complete_link",
                "accepted_cell_count": "2",
                "accepted_rescue_count": "1",
                "include_in_primary_matrix": "TRUE",
            },
            {
                "feature_family_id": "FAM_ADD",
                "neutral_loss_tag": "DNA_dR",
                "family_center_mz": "200.2",
                "family_center_rt": "8.2",
                "identity_decision": "provisional_discovery",
                "identity_confidence": "review",
                "identity_reason": "insufficient_detected_identity_support",
                "accepted_cell_count": "0",
                "accepted_rescue_count": "0",
                "include_in_primary_matrix": "FALSE",
            },
            {
                "feature_family_id": "FAM_KEEP",
                "neutral_loss_tag": "DNA_dR",
                "family_center_mz": "300.3",
                "family_center_rt": "9.3",
                "identity_decision": "production_family",
                "identity_confidence": "high",
                "identity_reason": "owner_complete_link",
                "accepted_cell_count": "1",
                "accepted_rescue_count": "1",
                "include_in_primary_matrix": "TRUE",
            },
            {
                "feature_family_id": "FAM_SPLIT",
                "neutral_loss_tag": "DNA_dR",
                "family_center_mz": "400.4",
                "family_center_rt": "10.4",
                "identity_decision": "production_family",
                "identity_confidence": "high",
                "identity_reason": "owner_complete_link",
                "accepted_cell_count": "2",
                "accepted_rescue_count": "0",
                "include_in_primary_matrix": "TRUE",
            },
        ],
    )
    _write_tsv(
        cells,
        (
            "feature_family_id",
            "sample_stem",
            "status",
            "area",
            "primary_matrix_area",
            "primary_matrix_area_source",
        ),
        [
            {
                "feature_family_id": "FAM_BLOCK",
                "sample_stem": "S1",
                "status": "detected",
                "area": "100",
                "primary_matrix_area": "100",
                "primary_matrix_area_source": "gaussian15_positive_asls_residual",
            },
            {
                "feature_family_id": "FAM_BLOCK",
                "sample_stem": "S2",
                "status": "rescued",
                "area": "200",
                "primary_matrix_area": "200",
                "primary_matrix_area_source": "gaussian15_positive_asls_residual",
            },
            {
                "feature_family_id": "FAM_ADD",
                "sample_stem": "S2",
                "status": "rescued",
                "area": "300",
                "primary_matrix_area": "300",
                "primary_matrix_area_source": "gaussian15_positive_asls_residual",
            },
            {
                "feature_family_id": "FAM_KEEP",
                "sample_stem": "S2",
                "status": "rescued",
                "area": "999",
                "primary_matrix_area": "999",
                "primary_matrix_area_source": "gaussian15_positive_asls_residual",
            },
            {
                "feature_family_id": "FAM_SPLIT",
                "sample_stem": "S1",
                "status": "detected",
                "area": "111",
                "primary_matrix_area": "111",
                "primary_matrix_area_source": "gaussian15_positive_asls_residual",
            },
            {
                "feature_family_id": "FAM_SPLIT",
                "sample_stem": "S2",
                "status": "detected",
                "area": "222",
                "primary_matrix_area": "222",
                "primary_matrix_area_source": "gaussian15_positive_asls_residual",
            },
        ],
    )
    return {
        "decisions": decisions,
        "acceptance": acceptance,
        "matrix": matrix,
        "review": review,
        "cells": cells,
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


def _rt_mode_row(
    family_id: str,
    sample_stem: str,
    *,
    mode_id: str,
    raw_rt: str,
) -> dict[str, str]:
    return {
        "feature_family_id": family_id,
        "sample_stem": sample_stem,
        "rt_mode_status": "mode_supported",
        "rt_mode_evidence_level": "irt_selected_apex_modes",
        "selected_mode_id": mode_id,
        "raw_selected_rt": raw_rt,
        "normalized_selected_rt": "",
    }


def _write_rt_mode_evidence_sidecar(tmp_path: Path) -> Path:
    path = tmp_path / "rt_mode_evidence.tsv"
    columns = tuple(
        column
        for column in RT_MODE_EVIDENCE_COLUMNS
        if column != "rt_mode_evidence_schema_version"
    )
    rows = [{column: "" for column in columns} for _ in range(2)]
    rows[0].update(
        _rt_mode_row("FAM_SPLIT", "S1", mode_id="blue", raw_rt="10.1")
    )
    rows[1].update(
        _rt_mode_row("FAM_SPLIT", "S2", mode_id="green", raw_rt="10.9")
    )
    _write_tsv(path, columns, rows)
    return path


def _read_tsv(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle, delimiter="\t"))


def _header(path: Path) -> tuple[str, ...]:
    with path.open("r", encoding="utf-8", newline="") as handle:
        return tuple(next(csv.reader(handle, delimiter="\t")))


def _write_public_mz_rt_matrix_fixture(
    tmp_path: Path,
    fixture: dict[str, Path],
) -> Path:
    matrix = tmp_path / "public_alignment_matrix.tsv"
    source_rows = _read_tsv(fixture["matrix"])
    _write_tsv(
        matrix,
        ("Mz", "RT", "S1", "S2"),
        [
            {
                "Mz": row["family_center_mz"],
                "RT": f"{float(row['family_center_rt']) + 0.05:.2f}".rstrip(
                    "0"
                ).rstrip("."),
                "S1": row["S1"],
                "S2": row["S2"],
            }
            for row in source_rows
        ],
    )
    return matrix


def _write_public_split_mz_rt_matrix_fixture(
    tmp_path: Path,
    fixture: dict[str, Path],
) -> Path:
    matrix = tmp_path / "public_split_alignment_matrix.tsv"
    source_rows = {
        row["feature_family_id"]: row for row in _read_tsv(fixture["matrix"])
    }
    _write_tsv(
        matrix,
        ("Mz", "RT", "S1", "S2"),
        [
            {
                "Mz": source_rows["FAM_BLOCK"]["family_center_mz"],
                "RT": "7.15",
                "S1": source_rows["FAM_BLOCK"]["S1"],
                "S2": source_rows["FAM_BLOCK"]["S2"],
            },
            {
                "Mz": source_rows["FAM_KEEP"]["family_center_mz"],
                "RT": "9.35",
                "S1": source_rows["FAM_KEEP"]["S1"],
                "S2": source_rows["FAM_KEEP"]["S2"],
            },
            {
                "Mz": source_rows["FAM_SPLIT"]["family_center_mz"],
                "RT": "10.45",
                "S1": source_rows["FAM_SPLIT"]["S1"],
                "S2": "",
            },
            {
                "Mz": source_rows["FAM_SPLIT"]["family_center_mz"],
                "RT": "10.95",
                "S1": "",
                "S2": source_rows["FAM_SPLIT"]["S2"],
            },
        ],
    )
    return matrix


def _write_alignment_matrix_identity_fixture(tmp_path: Path) -> Path:
    identity = tmp_path / "alignment_matrix_identity.tsv"
    _write_tsv(
        identity,
        (
            "identity_schema_version",
            "matrix_row_index",
            "Mz",
            "RT",
            "peak_hypothesis_id",
            "row_identity_basis",
            "source_feature_family_ids",
            "source_feature_family_count",
        ),
        [
            {
                "identity_schema_version": (
                    "untargeted_peak_hypothesis_matrix_identity_v1"
                ),
                "matrix_row_index": "1",
                "Mz": "100.1",
                "RT": "7.15",
                "peak_hypothesis_id": "FAM_BLOCK",
                "row_identity_basis": "no_split_peak_hypothesis",
                "source_feature_family_ids": "FAM_BLOCK",
                "source_feature_family_count": "1",
            },
            {
                "identity_schema_version": (
                    "untargeted_peak_hypothesis_matrix_identity_v1"
                ),
                "matrix_row_index": "2",
                "Mz": "300.3",
                "RT": "9.35",
                "peak_hypothesis_id": "FAM_KEEP",
                "row_identity_basis": "no_split_peak_hypothesis",
                "source_feature_family_ids": "FAM_KEEP",
                "source_feature_family_count": "1",
            },
            {
                "identity_schema_version": (
                    "untargeted_peak_hypothesis_matrix_identity_v1"
                ),
                "matrix_row_index": "3",
                "Mz": "400.4",
                "RT": "10.45",
                "peak_hypothesis_id": "FAM_SPLIT",
                "row_identity_basis": "no_split_peak_hypothesis",
                "source_feature_family_ids": "FAM_SPLIT",
                "source_feature_family_count": "1",
            },
        ],
    )
    return identity


def _bind_public_matrix_identity_v2(matrix: Path, identity: Path) -> None:
    from xic_extractor.alignment.matrix_output_contract import (
        bind_matrix_identity_rows,
        project_public_matrix_rows,
    )
    from xic_extractor.alignment.product_matrix import (
        ALIGNMENT_MATRIX_IDENTITY_COLUMNS,
    )

    matrix_header = _header(matrix)
    matrix_rows = tuple(
        project_public_matrix_rows(
            _read_tsv(matrix),
            sample_columns=matrix_header[2:],
        )
    )
    _write_tsv(matrix, matrix_header, matrix_rows)
    bound_rows = bind_matrix_identity_rows(
        matrix_header=matrix_header,
        matrix_rows=matrix_rows,
        identity_columns=ALIGNMENT_MATRIX_IDENTITY_COLUMNS,
        identity_rows=_read_tsv(identity),
    )
    _write_tsv(identity, ALIGNMENT_MATRIX_IDENTITY_COLUMNS, bound_rows)


def _write_split_alignment_matrix_identity_fixture(tmp_path: Path) -> Path:
    identity = tmp_path / "split_alignment_matrix_identity.tsv"
    _write_tsv(
        identity,
        (
            "identity_schema_version",
            "matrix_row_index",
            "Mz",
            "RT",
            "peak_hypothesis_id",
            "row_identity_basis",
            "source_feature_family_ids",
            "source_feature_family_count",
        ),
        [
            {
                "identity_schema_version": (
                    "untargeted_peak_hypothesis_matrix_identity_v1"
                ),
                "matrix_row_index": "1",
                "Mz": "100.1",
                "RT": "7.15",
                "peak_hypothesis_id": "FAM_BLOCK",
                "row_identity_basis": "no_split_peak_hypothesis",
                "source_feature_family_ids": "FAM_BLOCK",
                "source_feature_family_count": "1",
            },
            {
                "identity_schema_version": (
                    "untargeted_peak_hypothesis_matrix_identity_v1"
                ),
                "matrix_row_index": "2",
                "Mz": "300.3",
                "RT": "9.35",
                "peak_hypothesis_id": "FAM_KEEP",
                "row_identity_basis": "no_split_peak_hypothesis",
                "source_feature_family_ids": "FAM_KEEP",
                "source_feature_family_count": "1",
            },
            {
                "identity_schema_version": (
                    "untargeted_peak_hypothesis_matrix_identity_v1"
                ),
                "matrix_row_index": "3",
                "Mz": "400.4",
                "RT": "10.45",
                "peak_hypothesis_id": "FAM_SPLIT::blue",
                "row_identity_basis": "split_peak_hypothesis",
                "source_feature_family_ids": "FAM_SPLIT",
                "source_feature_family_count": "1",
            },
            {
                "identity_schema_version": (
                    "untargeted_peak_hypothesis_matrix_identity_v1"
                ),
                "matrix_row_index": "4",
                "Mz": "400.4",
                "RT": "10.95",
                "peak_hypothesis_id": "FAM_SPLIT::green",
                "row_identity_basis": "split_peak_hypothesis",
                "source_feature_family_ids": "FAM_SPLIT",
                "source_feature_family_count": "1",
            },
        ],
    )
    return identity


def _write_legacy_rt_row_oracle(
    path: Path,
    *,
    rows: tuple[tuple[float, float], ...],
) -> None:
    workbook = Workbook()
    sheet = workbook.active
    sheet.append(("Mz", "RT"))
    for mz, rt in rows:
        sheet.append((mz, rt))
    workbook.save(path)


def _write_backfill_evidence_sidecars(tmp_path: Path) -> dict[str, Path]:
    candidate_ms2 = tmp_path / "candidate_ms2_pattern.tsv"
    ms1_pattern = tmp_path / "ms1_pattern_coherence.tsv"
    qc_reference = tmp_path / "qc_ms1_pattern_reference.tsv"
    rt_drift = tmp_path / "matrix_rt_drift_policy.tsv"
    _write_tsv(
        candidate_ms2,
        (
            "feature_family_id",
            "sample_stem",
            "candidate_ms2_pattern_status",
            "candidate_ms2_evidence_level",
            "raw_ms2_trigger_scan_count",
            "raw_ms2_strict_nl_scan_count",
            "raw_ms2_trace_strength",
            "diagnostic_only",
            PRODUCT_AUTHORITY_STATUS_FIELD,
            PRODUCT_AUTHORITY_SCOPE_FIELD,
            PRODUCT_AUTHORITY_SOURCE_FIELD,
        ),
        [
            {
                "feature_family_id": "FAM_ADD",
                "sample_stem": "S1",
                "candidate_ms2_pattern_status": "supportive",
                "candidate_ms2_evidence_level": "sample_candidate_aligned",
                "raw_ms2_trigger_scan_count": "4",
                "raw_ms2_strict_nl_scan_count": "1",
                "raw_ms2_trace_strength": "strong",
                "diagnostic_only": "FALSE",
                **_product_authority(),
            },
            {
                "feature_family_id": "FAM_ADD",
                "sample_stem": "S2",
                "candidate_ms2_pattern_status": "not_observed",
                "candidate_ms2_evidence_level": "sample_boundary_no_observed_pattern",
                "raw_ms2_trigger_scan_count": "3",
                "raw_ms2_strict_nl_scan_count": "0",
                "raw_ms2_trace_strength": "moderate",
                "diagnostic_only": "FALSE",
                **_product_authority(),
            },
        ],
    )
    _write_tsv(
        ms1_pattern,
        (
            "feature_family_id",
            "sample_stem",
            "ms1_pattern_status",
            "ms1_pattern_evidence_level",
            "apex_coherence_sec",
            "boundary_overlap_score",
            "shape_correlation_score",
            "relative_pattern_stability_score",
            "local_interference_score",
            "constellation_peak_count",
            "reference_peak_count",
            "drift_compatible_status",
            "reason",
            "diagnostic_only",
            PRODUCT_AUTHORITY_STATUS_FIELD,
            PRODUCT_AUTHORITY_SCOPE_FIELD,
            PRODUCT_AUTHORITY_SOURCE_FIELD,
        ),
        [
            {
                "feature_family_id": "FAM_ADD",
                "sample_stem": "S2",
                "ms1_pattern_status": "supportive",
                "ms1_pattern_evidence_level": "trace_constellation",
                "apex_coherence_sec": "8",
                "boundary_overlap_score": "0.9",
                "shape_correlation_score": "0.82",
                "relative_pattern_stability_score": "0.8",
                "local_interference_score": "0.1",
                "constellation_peak_count": "4",
                "reference_peak_count": "5",
                "drift_compatible_status": "compatible",
                "reason": ANCHOR_OWN_MAX_MS1_SUPPORT_REASON,
                "diagnostic_only": "FALSE",
                **_product_authority(),
            }
        ],
    )
    _write_tsv(
        qc_reference,
        (
            "feature_family_id",
            "sample_stem",
            "qc_reference_status",
            "qc_reference_evidence_level",
            "target_injection_order",
            "nearest_qc_sample_stem",
            "nearest_qc_injection_order",
            "nearest_qc_injection_order_delta",
            "target_apex_rt",
            "nearest_qc_apex_rt",
            "target_minus_qc_apex_delta_sec",
            "target_qc_apex_abs_delta_sec",
            "target_qc_shape_similarity",
            "target_local_window_to_global_max_ratio",
            "nearest_qc_local_window_to_global_max_ratio",
            "reason",
            "diagnostic_only",
            PRODUCT_AUTHORITY_STATUS_FIELD,
            PRODUCT_AUTHORITY_SCOPE_FIELD,
            PRODUCT_AUTHORITY_SOURCE_FIELD,
        ),
        [
            {
                "feature_family_id": "FAM_ADD",
                "sample_stem": "S2",
                "qc_reference_status": "supportive",
                "qc_reference_evidence_level": "qc_consensus_with_local_qc_overlay",
                "target_injection_order": "10",
                "nearest_qc_sample_stem": "QC1",
                "nearest_qc_injection_order": "9",
                "nearest_qc_injection_order_delta": "1",
                "target_apex_rt": "8.20",
                "nearest_qc_apex_rt": "8.18",
                "target_minus_qc_apex_delta_sec": "1.2",
                "target_qc_apex_abs_delta_sec": "1.2",
                "target_qc_shape_similarity": "0.86",
                "target_local_window_to_global_max_ratio": "0.6",
                "nearest_qc_local_window_to_global_max_ratio": "0.7",
                "reason": "local_qc_overlay_supports_peak",
                "diagnostic_only": "FALSE",
                **_product_authority(),
            }
        ],
    )
    _write_tsv(
        rt_drift,
        (
            "feature_family_id",
            "sample_stem",
            "matrix_rt_drift_status",
            "drift_evidence_level",
            "raw_rt_delta_sec",
            "drift_corrected_delta_sec",
            "matrix_shift_sec",
            "drift_reference_count",
            "drift_reference_source",
            "drift_compatible_status",
            "reason",
            "diagnostic_only",
            PRODUCT_AUTHORITY_STATUS_FIELD,
            PRODUCT_AUTHORITY_SCOPE_FIELD,
            PRODUCT_AUTHORITY_SOURCE_FIELD,
        ),
        [
            {
                "feature_family_id": "FAM_ADD",
                "sample_stem": "S2",
                "matrix_rt_drift_status": "drift_supported",
                "drift_evidence_level": "matrix_reference_aligned",
                "raw_rt_delta_sec": "75",
                "drift_corrected_delta_sec": "15",
                "matrix_shift_sec": "60",
                "drift_reference_count": "5",
                "drift_reference_source": "paired_istd",
                "drift_compatible_status": "compatible",
                "reason": "paired_istd_drift_explains_delta",
                "diagnostic_only": "FALSE",
                **_product_authority(),
            }
        ],
    )
    return {
        "candidate_ms2_pattern": candidate_ms2,
        "ms1_pattern_coherence": ms1_pattern,
        "qc_ms1_pattern_reference": qc_reference,
        "matrix_rt_drift_policy": rt_drift,
    }


def _product_authority() -> dict[str, str]:
    return {
        PRODUCT_AUTHORITY_STATUS_FIELD: PRODUCT_AUTHORIZED_STATUS,
        PRODUCT_AUTHORITY_SCOPE_FIELD: PRODUCT_AUTHORIZED_SCOPE,
        PRODUCT_AUTHORITY_SOURCE_FIELD: "unit_test_reviewed_allowlist",
    }
