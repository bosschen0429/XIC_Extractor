import csv
import json
from pathlib import Path

from scripts.check_cid_nl_default_activation_bridge_gate import (
    evaluate_bridge_gate,
    main,
)


def test_bridge_gate_passes_unique_blank_replay(tmp_path: Path) -> None:
    paths = _write_fixture(tmp_path)

    payload = evaluate_bridge_gate(
        **paths,
    )

    assert payload["overall_status"] == "pass"
    assert payload["cell_bridge_status_counts"] == {"pass": 1}
    assert payload["activation_replay"]["status"] == "pass"
    assert payload["activation_replay"]["written_backfill_count"] == "1"
    assert payload["product_writer_changed"] is False
    assert payload["default_quant_matrix_changed"] is False


def test_bridge_gate_blocks_missing_new_identity(tmp_path: Path) -> None:
    paths = _write_fixture(tmp_path, new_identity_rows=[])

    payload = evaluate_bridge_gate(
        **paths,
    )

    assert payload["overall_status"] == "blocked"
    assert payload["blocker_counts"] == {"new_identity_missing": 1}
    assert payload["activation_replay"]["status"] == "not_run"


def test_bridge_gate_blocks_ambiguous_new_identity(tmp_path: Path) -> None:
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

    payload = evaluate_bridge_gate(
        **paths,
    )

    assert payload["overall_status"] == "blocked"
    assert payload["blocker_counts"] == {"new_identity_ambiguous": 1}
    assert payload["peak_bridge_status_counts"] == {"blocked": 1}


def test_bridge_gate_blocks_when_new_baseline_already_has_value(
    tmp_path: Path,
) -> None:
    paths = _write_fixture(
        tmp_path,
        matrix_rows=[{"Mz": "100.001", "RT": "5.001", "SampleA": "999"}],
    )

    payload = evaluate_bridge_gate(
        **paths,
    )

    assert payload["overall_status"] == "blocked"
    assert payload["blocker_counts"] == {"new_baseline_already_has_value": 1}
    assert payload["activation_replay"]["status"] == "not_run"


def test_bridge_gate_blocks_expected_diff_content_mismatch(
    tmp_path: Path,
) -> None:
    paths = _write_fixture(
        tmp_path,
        expected_diff_row={
            "activated_value": "999",
        },
    )

    payload = evaluate_bridge_gate(
        **paths,
    )

    assert payload["overall_status"] == "blocked"
    assert any(
        blocker.startswith("expected_diff_content_problem_count:")
        for blocker in payload["blockers"]
    )
    assert payload["activation_replay"]["status"] == "not_run"


def test_bridge_gate_blocks_identity_matrix_coordinate_mismatch(
    tmp_path: Path,
) -> None:
    paths = _write_fixture(
        tmp_path,
        matrix_rows=[{"Mz": "999.0", "RT": "99.0", "SampleA": ""}],
    )

    payload = evaluate_bridge_gate(
        **paths,
    )

    assert payload["overall_status"] == "blocked"
    assert payload["blocker_counts"] == {"new_identity_matrix_coordinate_mismatch": 1}
    assert payload["activation_replay"]["status"] == "not_run"


def test_bridge_gate_cli_writes_summary_and_audit(tmp_path: Path) -> None:
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
        (output_dir / "cid_nl_default_activation_bridge_gate_summary.json").read_text(
            encoding="utf-8",
        )
    )
    audit_rows = _read_tsv(output_dir / "cid_nl_default_activation_bridge_audit.tsv")
    assert summary["overall_status"] == "pass"
    assert audit_rows[0]["old_peak_hypothesis_id"] == "FAM_OLD"
    assert audit_rows[0]["selected_new_peak_hypothesis_id"] == "FAM_NEW"


def _write_fixture(
    tmp_path: Path,
    *,
    new_identity_rows: list[dict[str, str]] | None = None,
    matrix_rows: list[dict[str, str]] | None = None,
    expected_diff_row: dict[str, str] | None = None,
) -> dict[str, Path]:
    old_identity = tmp_path / "old_identity.tsv"
    new_identity = tmp_path / "new_identity.tsv"
    new_matrix = tmp_path / "new_matrix.tsv"
    manifest = tmp_path / "manifest.tsv"
    expected_diff = tmp_path / "expected_diff.tsv"
    target_summary = tmp_path / "target_summary.json"

    _write_tsv(
        old_identity,
        _IDENTITY_COLUMNS,
        [_identity_row(1, "FAM_OLD", "100.0", "5.0")],
    )
    _write_tsv(
        new_identity,
        _IDENTITY_COLUMNS,
        new_identity_rows
        if new_identity_rows is not None
        else [_identity_row(1, "FAM_NEW", "100.001", "5.001")],
    )
    _write_tsv(
        new_matrix,
        ("Mz", "RT", "SampleA"),
        matrix_rows
        if matrix_rows is not None
        else [{"Mz": "100.001", "RT": "5.001", "SampleA": ""}],
    )
    _write_tsv(
        manifest,
        (
            "peak_hypothesis_id",
            "sample_stem",
            "feature_family_id",
            "acceptance_decision",
            "write_authority",
            "matrix_write_allowed",
            "shadow_only",
            "quant_value",
            "source_row_sha256",
        ),
        [
            {
                "peak_hypothesis_id": "FAM_OLD",
                "sample_stem": "SampleA",
                "feature_family_id": "FAM_OLD",
                "acceptance_decision": "accept_basic_backfill",
                "write_authority": "TRUE",
                "matrix_write_allowed": "TRUE",
                "shadow_only": "FALSE",
                "quant_value": "123",
                "source_row_sha256": "A" * 64,
            }
        ],
    )
    expected_row = {
        "schema_version": "quant_matrix_version_expected_diff_v1",
        "peak_hypothesis_id": "FAM_OLD",
        "sample_stem": "SampleA",
        "baseline_value": "",
        "activated_value": "123",
        "expected_matrix_effect": "write_accepted_backfill",
        "expected_reason": "synthetic",
    }
    if expected_diff_row:
        expected_row.update(expected_diff_row)
    _write_tsv(
        expected_diff,
        (
            "schema_version",
            "peak_hypothesis_id",
            "sample_stem",
            "baseline_value",
            "activated_value",
            "expected_matrix_effect",
            "expected_reason",
        ),
        [expected_row],
    )
    target_summary.write_text(
        json.dumps(
            {
                "overall_status": "blocked",
                "target_alignment_evidence_status": "pass",
            },
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    return {
        "old_matrix_identity_tsv": old_identity,
        "new_quant_matrix_tsv": new_matrix,
        "new_matrix_identity_tsv": new_identity,
        "production_acceptance_manifest_tsv": manifest,
        "expected_diff_tsv": expected_diff,
        "target_preflight_summary_json": target_summary,
    }


_IDENTITY_COLUMNS = (
    "matrix_row_index",
    "Mz",
    "RT",
    "peak_hypothesis_id",
    "source_feature_family_ids",
)


def _identity_row(index: int, peak_id: str, mz: str, rt: str) -> dict[str, str]:
    return {
        "matrix_row_index": str(index),
        "Mz": mz,
        "RT": rt,
        "peak_hypothesis_id": peak_id,
        "source_feature_family_ids": peak_id,
    }


def _write_tsv(
    path: Path,
    fieldnames: tuple[str, ...],
    rows: list[dict[str, str]],
) -> None:
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, delimiter="\t")
        writer.writeheader()
        writer.writerows(
            {field: row.get(field, "") for field in fieldnames} for row in rows
        )


def _read_tsv(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle, delimiter="\t"))
