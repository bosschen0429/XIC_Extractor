import json
from pathlib import Path

from scripts.check_cid_nl_default_activation_cell_local_identity_gate import (
    evaluate_cell_local_identity_gate,
    main,
)
from tests.test_cid_nl_default_activation_bridge_gate import (
    _identity_row,
    _read_tsv,
    _write_fixture,
)


def test_cell_local_gate_resolves_ambiguous_unique_detected_candidate(
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

    payload = evaluate_cell_local_identity_gate(
        **paths,
    )

    assert payload["overall_status"] == "pass"
    assert payload["resolution_counts"] == {
        "cell_local_unique_detected_candidate_supersession": 1,
    }
    assert payload["cell_local_resolved_ambiguous_count"] == 1
    assert payload["unresolved_authority_cell_count"] == 0
    assert payload["cell_local_detected_candidate_is_backfill_authority"] is False


def test_cell_local_gate_keeps_all_blank_ambiguous_blocked(tmp_path: Path) -> None:
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

    payload = evaluate_cell_local_identity_gate(
        **paths,
    )

    assert payload["overall_status"] == "blocked"
    assert payload["resolution_counts"] == {"blocked_ambiguous_all_blank": 1}
    assert payload["blockers"] == ["unresolved_authority_cell_count:1"]


def test_cell_local_gate_keeps_multiple_detected_ambiguous_blocked(
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

    payload = evaluate_cell_local_identity_gate(
        **paths,
    )

    assert payload["overall_status"] == "blocked"
    assert payload["resolution_counts"] == {
        "blocked_ambiguous_multiple_detected_candidates": 1,
    }
    assert payload["unresolved_authority_cell_count"] == 1


def test_cell_local_gate_blocks_stale_ambiguous_identity_coordinate(
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

    payload = evaluate_cell_local_identity_gate(
        **paths,
    )

    assert payload["overall_status"] == "blocked"
    assert payload["resolution_counts"] == {
        "blocked_ambiguous_identity_matrix_coordinate_mismatch": 1,
    }
    assert payload["unresolved_authority_cell_count"] == 1


def test_cell_local_gate_keeps_missing_identity_blocked(tmp_path: Path) -> None:
    paths = _write_fixture(tmp_path, new_identity_rows=[])

    payload = evaluate_cell_local_identity_gate(
        **paths,
    )

    assert payload["overall_status"] == "blocked"
    assert payload["resolution_counts"] == {"blocked_identity_missing": 1}
    assert payload["unresolved_authority_cell_count"] == 1


def test_cell_local_gate_cli_writes_summary_and_audit(tmp_path: Path) -> None:
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
            / "cid_nl_default_activation_cell_local_identity_gate_summary.json"
        ).read_text(encoding="utf-8")
    )
    audit_rows = _read_tsv(
        output_dir / "cid_nl_default_activation_cell_local_identity_audit.tsv"
    )
    assert summary["overall_status"] == "pass"
    assert audit_rows[0]["selected_detected_candidate_peak_hypothesis_id"] == (
        "FAM_NEW_B"
    )
