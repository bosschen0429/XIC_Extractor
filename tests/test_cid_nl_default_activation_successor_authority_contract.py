import csv
import json
from pathlib import Path

from scripts.build_cid_nl_default_activation_successor_authority_contract import (
    build_successor_authority_contract,
    main,
)
from scripts.check_production_acceptance_manifest import (
    REQUIRED_COLUMNS as ACCEPTANCE_COLUMNS,
)
from scripts.check_production_acceptance_manifest import (
    production_acceptance_manifest_sha256,
)
from xic_extractor.alignment.quant_matrix_version import EXPECTED_DIFF_COLUMNS


def test_successor_authority_contract_builds_write_allowlist_and_replay(
    tmp_path: Path,
) -> None:
    paths = _write_fixture(tmp_path, scenario="write_ready")

    payload = build_successor_authority_contract(
        output_dir=tmp_path / "out",
        source_root=tmp_path,
        **paths,
    )

    assert payload["overall_status"] == "pass"
    assert payload["successor_authority_write_count"] == 1
    assert payload["successor_expected_diff_count"] == 1
    assert payload["detected_baseline_no_write_count"] == 0
    assert payload["scope_removed_no_write_count"] == 0
    assert payload["default_product_activation_changed"] is False
    assert payload["candidate_quant_matrix_sidecar_written"] is True
    assert payload["replay_summary"]["written_backfill_count"] == "1"
    assert payload["matrix_delta_summary"]["changed_cell_count"] == 1

    manifest = _read_tsv(tmp_path / "out" / "successor_authority_manifest.tsv")
    assert manifest[0]["peak_hypothesis_id"] == "FAM_NEW"
    assert manifest[0]["feature_family_id"] == "FAM_NEW"
    assert manifest[0]["write_authority"] == "TRUE"
    expected = _read_tsv(tmp_path / "out" / "successor_expected_diff.tsv")
    assert expected == [
        {
            "schema_version": "quant_matrix_version_expected_diff",
            "peak_hypothesis_id": "FAM_NEW",
            "sample_stem": "SampleA",
            "baseline_value": "",
            "activated_value": "222.2",
            "expected_matrix_effect": "write_accepted_backfill",
            "expected_reason": "cid_nl_successor_authority_contract:old_peak=FAM_OLD",
        }
    ]
    matrix = _read_tsv(
        tmp_path / "out" / "candidate_quant_matrix_version" / "quant_matrix.tsv"
    )
    assert matrix[0]["SampleA"] == "222.2"


def test_successor_authority_contract_preserves_detected_baseline_without_write(
    tmp_path: Path,
) -> None:
    paths = _write_fixture(tmp_path, scenario="detected_baseline")

    payload = build_successor_authority_contract(
        output_dir=tmp_path / "out",
        source_root=tmp_path,
        **paths,
    )

    assert payload["overall_status"] == "pass"
    assert payload["successor_authority_write_count"] == 0
    assert payload["detected_baseline_no_write_count"] == 1
    assert payload["matrix_delta_summary"]["changed_cell_count"] == 0
    assert _read_tsv(tmp_path / "out" / "successor_authority_manifest.tsv") == []
    decision = _read_tsv(tmp_path / "out" / "successor_authority_decisions.tsv")[0]
    assert decision["successor_decision"] == "no_write_detected_baseline_preserved"
    assert decision["write_authority"] == "FALSE"
    matrix = _read_tsv(
        tmp_path / "out" / "candidate_quant_matrix_version" / "quant_matrix.tsv"
    )
    assert matrix[0]["SampleA"] == "999"


def test_successor_authority_contract_omits_scope_removed_cells(
    tmp_path: Path,
) -> None:
    paths = _write_fixture(tmp_path, scenario="missing_identity")

    payload = build_successor_authority_contract(
        output_dir=tmp_path / "out",
        source_root=tmp_path,
        **paths,
    )

    assert payload["overall_status"] == "pass"
    assert payload["successor_authority_write_count"] == 0
    assert payload["scope_removed_no_write_count"] == 1
    decision = _read_tsv(tmp_path / "out" / "successor_authority_decisions.tsv")[0]
    assert decision["successor_decision"] == "no_write_omitted"
    assert decision["successor_peak_hypothesis_id"] == ""
    assert "omitted" in decision["human_explanation"]


def test_successor_authority_contract_fails_closed_on_stale_coordinate(
    tmp_path: Path,
) -> None:
    paths = _write_fixture(tmp_path, scenario="stale_coordinate")

    payload = build_successor_authority_contract(
        output_dir=tmp_path / "out",
        source_root=tmp_path,
        **paths,
    )

    assert payload["overall_status"] == "blocked"
    assert payload["successor_authority_contract_built"] is False
    assert payload["replay_summary"]["status"] == "not_run"
    assert payload["blockers"] == ["unresolved_authority_cell_count:1"]


def test_successor_authority_contract_cli_writes_summary(
    tmp_path: Path,
) -> None:
    paths = _write_fixture(tmp_path, scenario="write_ready")
    output_dir = tmp_path / "out"

    status = main(
        [
            "--output-dir",
            str(output_dir),
            "--source-root",
            str(tmp_path),
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
            "--require-pass",
        ]
    )

    assert status == 0
    summary = json.loads(
        (
            output_dir
            / "cid_nl_default_activation_successor_authority_summary.json"
        ).read_text(encoding="utf-8")
    )
    assert summary["overall_status"] == "pass"
    assert summary["successor_authority_write_count"] == 1


def _write_fixture(tmp_path: Path, *, scenario: str) -> dict[str, Path | None]:
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
    if scenario == "write_ready":
        new_rows = [_identity_row(1, "FAM_NEW", "100.001", "5.001")]
        matrix_rows = [{"Mz": "100.001", "RT": "5.001", "SampleA": ""}]
    elif scenario == "detected_baseline":
        new_rows = [_identity_row(1, "FAM_NEW", "100.001", "5.001")]
        matrix_rows = [{"Mz": "100.001", "RT": "5.001", "SampleA": "999"}]
    elif scenario == "missing_identity":
        new_rows = [_identity_row(1, "FAM_OTHER", "200.0", "8.0")]
        matrix_rows = [{"Mz": "200.0", "RT": "8.0", "SampleA": ""}]
    elif scenario == "stale_coordinate":
        new_rows = [
            _identity_row(1, "FAM_NEW_A", "100.001", "5.001"),
            _identity_row(2, "FAM_NEW_B", "100.002", "5.002"),
        ]
        matrix_rows = [
            {"Mz": "100.001", "RT": "5.001", "SampleA": ""},
            {"Mz": "999.0", "RT": "99.0", "SampleA": "999"},
        ]
    else:
        raise AssertionError(f"unknown scenario: {scenario}")
    _write_tsv(new_identity, _IDENTITY_COLUMNS, new_rows)
    _write_tsv(new_matrix, ("Mz", "RT", "SampleA"), matrix_rows)

    manifest_rows = [_acceptance_row(tmp_path)]
    manifest_sha = production_acceptance_manifest_sha256(manifest_rows)
    for row in manifest_rows:
        row["manifest_sha256"] = manifest_sha
    _write_tsv(manifest, ACCEPTANCE_COLUMNS, manifest_rows)
    _write_tsv(
        expected_diff,
        EXPECTED_DIFF_COLUMNS,
        [
            {
                "schema_version": "quant_matrix_version_expected_diff",
                "peak_hypothesis_id": "FAM_OLD",
                "sample_stem": "SampleA",
                "baseline_value": "",
                "activated_value": "222.2",
                "expected_matrix_effect": "write_accepted_backfill",
                "expected_reason": "fixture",
            }
        ],
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


def _acceptance_row(tmp_path: Path) -> dict[str, str]:
    source = _write_source(tmp_path, "sources/evidence.tsv", "cell\tFAM_OLD\n")
    doublet = _write_source(tmp_path, "sources/doublet.tsv", "doublet\tFAM_OLD\n")
    return {
        "schema_version": "production_acceptance_manifest",
        "peak_hypothesis_id": "FAM_OLD",
        "sample_stem": "SampleA",
        "feature_family_id": "FAM_OLD",
        "acceptance_decision": "accept_basic_backfill",
        "acceptance_basis": "machine_basic",
        "truth_status": "not_truth_claimed",
        "shadow_only": "FALSE",
        "write_authority": "TRUE",
        "matrix_write_allowed": "TRUE",
        "quant_value": "222.2",
        "quant_value_source": "gaussian_smoothed_integration",
        "matrix_area_source": "gaussian_smoothed_boundary_integration",
        "detected_count": "1",
        "backfilled_count": "1",
        "quant_available_count": "2",
        "missing_count": "0",
        "backfill_fraction": "0.500000",
        "prevalence_flags": "low_seed_support",
        "hard_blocker_rule_ids": "",
        "triggered_risk_rule_ids": "low_seed_support",
        "closure_rule_ids": "",
        "decision_reason": "fixture",
        "next_evidence_needed": "",
        "doublet_status": "no_doublet_claim",
        "reference_side": "not_applicable",
        "doublet_allowed": "TRUE",
        "doublet_source_relpath": _relpath(doublet, tmp_path),
        "doublet_source_sha256": _sha256(doublet),
        "source_artifact_relpath": _relpath(source, tmp_path),
        "source_artifact_sha256": _sha256(source),
        "source_row_sha256": "A" * 64,
        "manifest_sha256": "",
        "acceptance_contract_version": "production_acceptance_manifest_contract",
    }


def _write_source(root: Path, relative: str, content: str) -> Path:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return path


def _relpath(path: Path, root: Path) -> str:
    return path.relative_to(root).as_posix()


def _sha256(path: Path) -> str:
    import hashlib

    return hashlib.sha256(path.read_bytes()).hexdigest().upper()


def _write_tsv(
    path: Path,
    fieldnames: tuple[str, ...],
    rows: list[dict[str, str]],
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, delimiter="\t")
        writer.writeheader()
        writer.writerows(
            {field: row.get(field, "") for field in fieldnames} for row in rows
        )


def _read_tsv(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle, delimiter="\t"))
