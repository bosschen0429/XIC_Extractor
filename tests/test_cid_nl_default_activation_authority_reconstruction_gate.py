import json
from pathlib import Path

from scripts.check_cid_nl_default_activation_authority_reconstruction_gate import (
    evaluate_authority_reconstruction_gate,
    main,
)
from tests.test_cid_nl_default_activation_bridge_gate import (
    _identity_row,
    _read_tsv,
    _write_fixture,
)


def test_reconstruction_gate_passes_blank_write_ready_replay(
    tmp_path: Path,
) -> None:
    paths = _write_fixture(tmp_path)

    payload = evaluate_authority_reconstruction_gate(
        **paths,
    )

    assert payload["overall_status"] == "pass"
    assert payload["resolution_counts"] == {"write_ready_blank": 1}
    assert payload["candidate_backfill_write_count"] == 1
    assert payload["candidate_replay"]["status"] == "pass"
    assert payload["candidate_replay"]["written_backfill_count"] == "1"
    assert payload["detected_baseline_supersession_is_backfill_authority"] is False
    assert payload["default_quant_matrix_changed"] is False


def test_reconstruction_gate_treats_detected_baseline_as_resolved_no_write(
    tmp_path: Path,
) -> None:
    paths = _write_fixture(
        tmp_path,
        matrix_rows=[{"Mz": "100.001", "RT": "5.001", "SampleA": "999"}],
    )

    payload = evaluate_authority_reconstruction_gate(
        **paths,
    )

    assert payload["overall_status"] == "pass"
    assert payload["resolution_counts"] == {
        "superseded_by_detected_baseline": 1,
    }
    assert payload["candidate_backfill_write_count"] == 0
    assert payload["detected_baseline_superseded_count"] == 1
    assert payload["candidate_replay"]["status"] == "pass"
    assert payload["candidate_replay"]["written_backfill_count"] == "0"
    assert payload["detected_baseline_supersession_is_backfill_authority"] is False


def test_reconstruction_gate_blocks_missing_identity(tmp_path: Path) -> None:
    paths = _write_fixture(tmp_path, new_identity_rows=[])

    payload = evaluate_authority_reconstruction_gate(
        **paths,
    )

    assert payload["overall_status"] == "blocked"
    assert payload["resolution_counts"] == {"blocked_identity_missing": 1}
    assert payload["unresolved_authority_cell_count"] == 1
    assert payload["blockers"] == ["unresolved_authority_cell_count:1"]


def test_reconstruction_gate_blocks_ambiguous_identity(tmp_path: Path) -> None:
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

    payload = evaluate_authority_reconstruction_gate(
        **paths,
    )

    assert payload["overall_status"] == "blocked"
    assert payload["resolution_counts"] == {"blocked_identity_ambiguous": 1}
    assert payload["unresolved_authority_cell_count"] == 1


def test_reconstruction_gate_blocks_expected_diff_content_mismatch(
    tmp_path: Path,
) -> None:
    paths = _write_fixture(
        tmp_path,
        expected_diff_row={"activated_value": "999"},
    )

    payload = evaluate_authority_reconstruction_gate(
        **paths,
    )

    assert payload["overall_status"] == "blocked"
    assert payload["candidate_replay"]["status"] == "not_run"
    assert payload["blockers"] == [
        "expected_diff_content_problem_count:1",
        "candidate_replay_not_pass:source_blockers_present",
    ]


def test_reconstruction_gate_cli_writes_summary_and_audit(tmp_path: Path) -> None:
    paths = _write_fixture(tmp_path)
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
            / "cid_nl_default_activation_authority_reconstruction_gate_summary.json"
        ).read_text(encoding="utf-8")
    )
    audit_rows = _read_tsv(
        output_dir / "cid_nl_default_activation_authority_reconstruction_audit.tsv"
    )
    assert summary["overall_status"] == "pass"
    assert audit_rows[0]["resolution_status"] == "write_ready_blank"
    assert audit_rows[0]["authority_action"] == "candidate_backfill_write"
