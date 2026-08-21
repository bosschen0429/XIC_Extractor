import json
from pathlib import Path

from scripts.check_cid_nl_default_activation_remaining_identity_gate import (
    evaluate_remaining_identity_gate,
    main,
)
from tests.test_cid_nl_default_activation_bridge_gate import (
    _identity_row,
    _read_tsv,
    _write_fixture,
)


def test_remaining_identity_gate_passes_existing_write_ready_cell(
    tmp_path: Path,
) -> None:
    paths = _write_fixture(tmp_path)

    payload = evaluate_remaining_identity_gate(
        **paths,
    )

    assert payload["overall_status"] == "pass"
    assert payload["resolution_counts"] == {"write_ready_blank": 1}
    assert payload["candidate_backfill_write_count"] == 1
    assert payload["remaining_cells_resolved_count"] == 0
    assert payload["default_activation_candidate_built"] is False
    assert payload["product_writer_changed"] is False


def test_remaining_identity_gate_removes_missing_identity_from_candidate_scope(
    tmp_path: Path,
) -> None:
    paths = _write_fixture(tmp_path, new_identity_rows=[])

    payload = evaluate_remaining_identity_gate(
        **paths,
    )

    assert payload["overall_status"] == "pass"
    assert payload["resolution_counts"] == {
        "scope_removed_missing_identity_no_write": 1,
    }
    assert payload["scope_removed_no_write_count"] == 1
    assert payload["unresolved_authority_cell_count"] == 0
    assert payload["scope_removal_is_backfill_authority"] is False
    audit_row = payload["audit_rows"][0]
    assert audit_row["authority_action"] == (
        "no_backfill_write_legacy_claim_removed_from_candidate_scope"
    )
    assert audit_row["matrix_effect"] == "no_write_scope_removed"


def test_remaining_identity_gate_records_non_bridge_source_identity_context(
    tmp_path: Path,
) -> None:
    paths = _write_fixture(
        tmp_path,
        new_identity_rows=[_identity_row(1, "FAM_OLD", "900.0", "50.0")],
        matrix_rows=[{"Mz": "900.0", "RT": "50.0", "SampleA": ""}],
    )

    payload = evaluate_remaining_identity_gate(
        **paths,
    )

    assert payload["overall_status"] == "pass"
    audit_row = payload["audit_rows"][0]
    assert audit_row["remaining_identity_resolution_status"] == (
        "scope_removed_missing_identity_no_write"
    )
    assert audit_row["new_identity_exact_peak_hypothesis_ids"] == "FAM_OLD"
    assert audit_row["new_identity_source_feature_family_peak_hypothesis_ids"] == (
        "FAM_OLD"
    )


def test_remaining_identity_gate_removes_all_blank_ambiguity_without_write(
    tmp_path: Path,
) -> None:
    paths = _write_fixture(
        tmp_path,
        new_identity_rows=[
            _identity_row(1, "FAM_NEW_A", "100.001", "5.001"),
            _identity_row(2, "FAM_NEW_B", "100.002", "5.002"),
        ],
        matrix_rows=[
            {"Mz": "100.001", "RT": "5.001", "SampleA": ""},
            {"Mz": "100.002", "RT": "5.002", "SampleA": ""},
        ],
    )

    payload = evaluate_remaining_identity_gate(
        **paths,
    )

    assert payload["overall_status"] == "pass"
    assert payload["resolution_counts"] == {
        "scope_removed_ambiguous_blank_no_write": 1,
    }
    audit_row = payload["audit_rows"][0]
    assert audit_row["candidate_new_peak_hypothesis_ids"] == "FAM_NEW_A;FAM_NEW_B"
    assert audit_row["detected_candidate_peak_hypothesis_ids"] == ""
    assert audit_row["default_activation_scope"] == "removed_from_candidate_scope"


def test_remaining_identity_gate_removes_multiple_detected_ambiguity_without_choice(
    tmp_path: Path,
) -> None:
    paths = _write_fixture(
        tmp_path,
        new_identity_rows=[
            _identity_row(1, "FAM_NEW_A", "100.001", "5.001"),
            _identity_row(2, "FAM_NEW_B", "100.002", "5.002"),
        ],
        matrix_rows=[
            {"Mz": "100.001", "RT": "5.001", "SampleA": "111"},
            {"Mz": "100.002", "RT": "5.002", "SampleA": "999"},
        ],
    )

    payload = evaluate_remaining_identity_gate(
        **paths,
    )

    assert payload["overall_status"] == "pass"
    assert payload["resolution_counts"] == {
        "scope_removed_ambiguous_multiple_detected_no_write": 1,
    }
    audit_row = payload["audit_rows"][0]
    assert audit_row["detected_candidate_peak_hypothesis_ids"] == (
        "FAM_NEW_A;FAM_NEW_B"
    )
    assert audit_row["selected_detected_candidate_peak_hypothesis_id"] == ""


def test_remaining_identity_gate_keeps_unique_detected_candidate_as_no_write(
    tmp_path: Path,
) -> None:
    paths = _write_fixture(
        tmp_path,
        new_identity_rows=[
            _identity_row(1, "FAM_NEW_A", "100.001", "5.001"),
            _identity_row(2, "FAM_NEW_B", "100.002", "5.002"),
        ],
        matrix_rows=[
            {"Mz": "100.001", "RT": "5.001", "SampleA": ""},
            {"Mz": "100.002", "RT": "5.002", "SampleA": "999"},
        ],
    )

    payload = evaluate_remaining_identity_gate(
        **paths,
    )

    assert payload["overall_status"] == "pass"
    assert payload["resolution_counts"] == {
        "cell_local_unique_detected_candidate_supersession": 1,
    }
    assert payload["detected_baseline_no_write_count"] == 1
    assert payload["scope_removed_no_write_count"] == 0


def test_remaining_identity_gate_fails_closed_on_stale_candidate_coordinate(
    tmp_path: Path,
) -> None:
    paths = _write_fixture(
        tmp_path,
        new_identity_rows=[
            _identity_row(1, "FAM_NEW_A", "100.001", "5.001"),
            _identity_row(2, "FAM_NEW_B", "100.002", "5.002"),
        ],
        matrix_rows=[
            {"Mz": "100.001", "RT": "5.001", "SampleA": ""},
            {"Mz": "999.0", "RT": "99.0", "SampleA": "999"},
        ],
    )

    payload = evaluate_remaining_identity_gate(
        **paths,
    )

    assert payload["overall_status"] == "blocked"
    assert payload["resolution_counts"] == {
        "blocked_unresolved_cell_local_status": 1,
    }
    assert payload["unresolved_authority_cell_count"] == 1
    assert payload["blockers"] == ["unresolved_authority_cell_count:1"]


def test_remaining_identity_gate_cli_writes_summary_and_audit(
    tmp_path: Path,
) -> None:
    paths = _write_fixture(tmp_path, new_identity_rows=[])
    output_dir = tmp_path / "out"

    status = main(
        [
            "--old-matrix-identity-tsv",
            str(paths["old_matrix_identity_tsv"]),
            "--new-quant-matrix-tsv",
            str(paths["new_quant_matrix_tsv"]),
            "--new-matrix-identity-tsv",
            str(paths["new_matrix_identity_tsv"]),
            "--production-acceptance-manifest-tsv",
            str(paths["production_acceptance_manifest_tsv"]),
            "--expected-diff-tsv",
            str(paths["expected_diff_tsv"]),
            "--target-preflight-summary-json",
            str(paths["target_preflight_summary_json"]),
            "--output-dir",
            str(output_dir),
        ]
    )

    assert status == 0
    summary = json.loads(
        (
            output_dir
            / "cid_nl_default_activation_remaining_identity_gate_summary.json"
        ).read_text(encoding="utf-8")
    )
    audit_rows = _read_tsv(
        output_dir / "cid_nl_default_activation_remaining_identity_audit.tsv"
    )
    assert summary["overall_status"] == "pass"
    assert summary["scope_removed_no_write_count"] == 1
    assert audit_rows[0]["remaining_identity_resolution_status"] == (
        "scope_removed_missing_identity_no_write"
    )
