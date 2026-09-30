import csv
import json
import subprocess
import sys
from pathlib import Path

from scripts.build_quant_matrix_real_bundle import (
    build_quant_matrix_real_bundle,
    validate_quant_matrix_real_bundle,
)

ROOT = Path(__file__).resolve().parents[1]
RETAINED_SUMMARY = (
    ROOT / "docs/superpowers/validation/quant_matrix_real_bundle/"
    "quant_matrix_real_bundle_summary.json"
)


def test_retained_seed_guard_copy_hash_matches_summary() -> None:
    payload = json.loads(RETAINED_SUMMARY.read_text(encoding="utf-8"))
    entry = payload["source_artifacts"]["seed_guard_decisions"]
    copied = RETAINED_SUMMARY.parent / entry["path"]

    assert copied.is_file()
    assert _sha256(copied) == entry["sha256"]


def test_tracked_default_bundle_and_activation_validate_without_local_artifacts(
    tmp_path: Path,
) -> None:
    checkout = tmp_path / "checkout"
    checkout_result = subprocess.run(
        [
            "git",
            "-c",
            "core.longpaths=true",
            "checkout-index",
            "--all",
            f"--prefix={checkout.as_posix()}/",
        ],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )
    assert checkout_result.returncode == 0, (
        checkout_result.stdout + checkout_result.stderr
    )

    for script in (
        "build_quant_matrix_real_bundle.py",
        "build_quant_matrix_default_product_activation.py",
    ):
        result = subprocess.run(
            [sys.executable, str(checkout / "scripts" / script), "--check-only"],
            cwd=checkout,
            capture_output=True,
            text=True,
            check=False,
        )
        assert result.returncode == 0, result.stdout + result.stderr


def test_real_bundle_builds_self_validating_contract_outputs(tmp_path: Path) -> None:
    fixture = _write_source_run_fixture(tmp_path)

    outputs = build_quant_matrix_real_bundle(
        source_run_dir=fixture["source_run"],
        output_dir=fixture["output_dir"],
        repo_root=tmp_path,
        downstream_scope="synthetic_current_authority_replay",
    )

    assert (
        validate_quant_matrix_real_bundle(
            summary_json=outputs["summary_json"],
            repo_root=tmp_path,
            expected_source_run_id="synthetic-current-511-authority-replay",
            expected_downstream_scope="synthetic_current_authority_replay",
            expected_accepted_backfill_count=1,
        )
        == []
    )
    summary = json.loads(outputs["summary_json"].read_text(encoding="utf-8"))
    cell_contract = outputs["cell_provenance_summary_json"]
    review_contract = outputs["review_rows_summary_json"]
    assert cell_contract.is_file()
    assert outputs["cell_provenance_minimal_fixture"].is_file()
    assert review_contract.is_file()
    assert outputs["review_rows_minimal_fixture"].is_file()
    assert summary["artifacts"]["cell_provenance_summary"]["sha256"] == _sha256(
        cell_contract
    )
    assert summary["artifacts"]["review_rows_summary"]["sha256"] == _sha256(
        review_contract
    )
    assert summary["artifacts"]["cell_provenance"]["retention_decision"] == (
        "externalize"
    )
    assert summary["artifacts"]["review_rows"]["retention_decision"] == ("externalize")
    assert summary["accepted_backfill_count"] == 1
    assert summary["validation_status"] == "contract_ready_science_inconclusive"
    assert summary["product_writer_changed"] is False
    assert summary["default_quant_matrix_changed"] is False
    assert summary["may_promote_default_quant_matrix"] is False

    manifest_rows = _read_tsv(outputs["production_acceptance_manifest"])
    assert len(manifest_rows) == 1
    assert manifest_rows[0]["write_authority"] == "TRUE"
    assert manifest_rows[0]["shadow_only"] == "FALSE"
    assert manifest_rows[0]["truth_status"] == "not_truth_claimed"
    assert manifest_rows[0]["manifest_sha256"]

    expected_diff_rows = _read_tsv(outputs["expected_diff"])
    assert expected_diff_rows == [
        {
            "schema_version": "quant_matrix_version_expected_diff",
            "peak_hypothesis_id": "PH001",
            "sample_stem": "SampleB",
            "baseline_value": "",
            "activated_value": "222.2",
            "expected_matrix_effect": "write_accepted_backfill",
            "expected_reason": "current_511_authority_replay",
        }
    ]


def test_real_bundle_check_rejects_tampered_source_copy(tmp_path: Path) -> None:
    fixture = _write_source_run_fixture(tmp_path)
    outputs = build_quant_matrix_real_bundle(
        source_run_dir=fixture["source_run"],
        output_dir=fixture["output_dir"],
        repo_root=tmp_path,
        downstream_scope="synthetic_current_authority_replay",
    )
    source_copy = (
        fixture["output_dir"] / "source_artifacts/standard_peak_activation_values.tsv"
    )
    source_copy.write_text(
        source_copy.read_text(encoding="utf-8").replace("222.2", "333.3"),
        encoding="utf-8",
    )

    problems = validate_quant_matrix_real_bundle(
        summary_json=outputs["summary_json"],
        repo_root=tmp_path,
        expected_source_run_id="synthetic-current-511-authority-replay",
        expected_downstream_scope="synthetic_current_authority_replay",
        expected_accepted_backfill_count=1,
    )

    assert any(
        "source artifact copy sha256 mismatch" in problem for problem in problems
    )
    assert any("source_artifact_sha256 mismatch" in problem for problem in problems)


def test_real_bundle_default_check_rejects_non_current_511_bundle(
    tmp_path: Path,
) -> None:
    fixture = _write_source_run_fixture(tmp_path)
    outputs = build_quant_matrix_real_bundle(
        source_run_dir=fixture["source_run"],
        output_dir=fixture["output_dir"],
        repo_root=tmp_path,
        downstream_scope="synthetic_current_authority_replay",
    )

    problems = validate_quant_matrix_real_bundle(
        summary_json=outputs["summary_json"],
        repo_root=tmp_path,
    )

    assert any("source_run_id mismatch" in problem for problem in problems)
    assert any("downstream_scope mismatch" in problem for problem in problems)
    assert any("accepted_backfill_count mismatch" in problem for problem in problems)


def test_real_bundle_check_rejects_missing_downstream_rows(tmp_path: Path) -> None:
    fixture = _write_source_run_fixture(tmp_path)
    outputs = build_quant_matrix_real_bundle(
        source_run_dir=fixture["source_run"],
        output_dir=fixture["output_dir"],
        repo_root=tmp_path,
        downstream_scope="synthetic_current_authority_replay",
    )
    (
        fixture["output_dir"]
        / "downstream_impact/quant_matrix_downstream_impact_rows.tsv"
    ).unlink()

    problems = validate_quant_matrix_real_bundle(
        summary_json=outputs["summary_json"],
        repo_root=tmp_path,
        expected_source_run_id="synthetic-current-511-authority-replay",
        expected_downstream_scope="synthetic_current_authority_replay",
        expected_accepted_backfill_count=1,
    )

    assert "downstream impact row_metrics_tsv does not exist" in problems


def test_real_bundle_check_rejects_stale_fixture_contract(
    tmp_path: Path,
) -> None:
    fixture = _write_source_run_fixture(tmp_path)
    outputs = build_quant_matrix_real_bundle(
        source_run_dir=fixture["source_run"],
        output_dir=fixture["output_dir"],
        repo_root=tmp_path,
        downstream_scope="synthetic_current_authority_replay",
    )
    payload = json.loads(
        outputs["cell_provenance_summary_json"].read_text(encoding="utf-8")
    )
    payload["source_row_count"] = 999
    outputs["cell_provenance_summary_json"].write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    problems = validate_quant_matrix_real_bundle(
        summary_json=outputs["summary_json"],
        repo_root=tmp_path,
        expected_source_run_id="synthetic-current-511-authority-replay",
        expected_downstream_scope="synthetic_current_authority_replay",
        expected_accepted_backfill_count=1,
    )

    assert any("cell_provenance summary" in problem for problem in problems)


def test_real_bundle_check_rejects_missing_or_tampered_minimal_fixture(
    tmp_path: Path,
) -> None:
    fixture = _write_source_run_fixture(tmp_path)
    outputs = build_quant_matrix_real_bundle(
        source_run_dir=fixture["source_run"],
        output_dir=fixture["output_dir"],
        repo_root=tmp_path,
        downstream_scope="synthetic_current_authority_replay",
    )
    outputs["cell_provenance_minimal_fixture"].unlink()

    missing = validate_quant_matrix_real_bundle(
        summary_json=outputs["summary_json"],
        repo_root=tmp_path,
        expected_source_run_id="synthetic-current-511-authority-replay",
        expected_downstream_scope="synthetic_current_authority_replay",
        expected_accepted_backfill_count=1,
    )

    assert any("cell_provenance minimal fixture" in problem for problem in missing)

    outputs = build_quant_matrix_real_bundle(
        source_run_dir=fixture["source_run"],
        output_dir=fixture["output_dir"],
        repo_root=tmp_path,
        downstream_scope="synthetic_current_authority_replay",
    )
    fixture_text = outputs["review_rows_minimal_fixture"].read_text(encoding="utf-8")
    outputs["review_rows_minimal_fixture"].write_text(
        fixture_text.replace("review_only", "authority_changed", 1),
        encoding="utf-8",
    )

    tampered = validate_quant_matrix_real_bundle(
        summary_json=outputs["summary_json"],
        repo_root=tmp_path,
        expected_source_run_id="synthetic-current-511-authority-replay",
        expected_downstream_scope="synthetic_current_authority_replay",
        expected_accepted_backfill_count=1,
    )

    assert any("review rows minimal fixture" in problem for problem in tampered)


def test_real_bundle_check_accepts_externalized_cell_provenance(
    tmp_path: Path,
) -> None:
    fixture = _write_source_run_fixture(tmp_path)
    outputs = build_quant_matrix_real_bundle(
        source_run_dir=fixture["source_run"],
        output_dir=fixture["output_dir"],
        repo_root=tmp_path,
        downstream_scope="synthetic_current_authority_replay",
    )
    outputs["cell_provenance"].unlink()

    assert (
        validate_quant_matrix_real_bundle(
            summary_json=outputs["summary_json"],
            repo_root=tmp_path,
            expected_source_run_id="synthetic-current-511-authority-replay",
            expected_downstream_scope="synthetic_current_authority_replay",
            expected_accepted_backfill_count=1,
        )
        == []
    )


def test_real_bundle_can_externalize_review_html(tmp_path: Path) -> None:
    fixture = _write_source_run_fixture(tmp_path)
    rendered_review_dir = tmp_path / "rendered_review"
    outputs = build_quant_matrix_real_bundle(
        source_run_dir=fixture["source_run"],
        output_dir=fixture["output_dir"],
        repo_root=tmp_path,
        downstream_scope="synthetic_current_authority_replay",
        rendered_review_dir=rendered_review_dir,
    )

    assert (
        validate_quant_matrix_real_bundle(
            summary_json=outputs["summary_json"],
            repo_root=tmp_path,
            expected_source_run_id="synthetic-current-511-authority-replay",
            expected_downstream_scope="synthetic_current_authority_replay",
            expected_accepted_backfill_count=1,
        )
        == []
    )

    contract_html = fixture["output_dir"] / "review/quant_matrix_review_report.html"
    rendered_html = rendered_review_dir / "quant_matrix_review_report.html"
    summary = json.loads(outputs["summary_json"].read_text(encoding="utf-8"))
    review_html = summary["artifacts"]["review_html"]
    replacement = fixture["output_dir"] / review_html["replacement_or_summary"]
    replacement_payload = json.loads(replacement.read_text(encoding="utf-8"))

    assert not contract_html.exists()
    assert rendered_html.is_file()
    assert outputs["review_html_summary_json"] == replacement
    assert review_html["externalized"] is True
    assert review_html["retention_decision"] == "externalize"
    assert replacement_payload["may_grant_product_authority"] is False
    assert replacement_payload["sha256"] == review_html["sha256"]


def test_real_bundle_script_check_only_round_trip(tmp_path: Path) -> None:
    fixture = _write_source_run_fixture(tmp_path)

    build = subprocess.run(
        [
            sys.executable,
            "scripts/build_quant_matrix_real_bundle.py",
            "--source-run-dir",
            str(fixture["source_run"]),
            "--output-dir",
            str(fixture["output_dir"]),
            "--repo-root",
            str(tmp_path),
            "--downstream-scope",
            "synthetic_current_authority_replay",
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    assert build.returncode == 0, build.stderr

    check = subprocess.run(
        [
            sys.executable,
            "scripts/build_quant_matrix_real_bundle.py",
            "--output-dir",
            str(fixture["output_dir"]),
            "--repo-root",
            str(tmp_path),
            "--check-only",
            "--expected-source-run-id",
            "synthetic-current-511-authority-replay",
            "--expected-downstream-scope",
            "synthetic_current_authority_replay",
            "--expected-accepted-backfill-count",
            "1",
        ],
        check=False,
        capture_output=True,
        text=True,
    )

    assert check.returncode == 0, check.stderr
    assert "real_bundle_status: pass" in check.stdout


def _write_source_run_fixture(root: Path) -> dict[str, Path]:
    source_run = root / "source_run"
    activation_dir = source_run / "standard_peak_activation_inputs"
    activated_dir = source_run / "activated_matrix"
    pre_dir = root / "pre"
    output_dir = root / "bundle"
    activation_dir.mkdir(parents=True)
    activated_dir.mkdir(parents=True)
    pre_dir.mkdir(parents=True)

    pre_matrix = pre_dir / "alignment_matrix.pre_standard_peak_backfill.tsv"
    _write_tsv(
        pre_matrix,
        ("Mz", "RT", "SampleA", "SampleB", "SampleC"),
        [
            {
                "Mz": "101.1",
                "RT": "5.5",
                "SampleA": "100",
                "SampleB": "",
                "SampleC": "",
            }
        ],
    )
    activated_matrix = activated_dir / "alignment_matrix.tsv"
    _write_tsv(
        activated_matrix,
        ("Mz", "RT", "SampleA", "SampleB", "SampleC"),
        [
            {
                "Mz": "101.1",
                "RT": "5.5",
                "SampleA": "100",
                "SampleB": "222.2",
                "SampleC": "",
            }
        ],
    )
    _write_tsv(
        activated_dir / "alignment_matrix_identity.tsv",
        (
            "matrix_row_index",
            "Mz",
            "RT",
            "peak_hypothesis_id",
            "row_identity_basis",
            "source_feature_family_ids",
        ),
        [
            {
                "matrix_row_index": "1",
                "Mz": "101.1",
                "RT": "5.5",
                "peak_hypothesis_id": "PH001",
                "row_identity_basis": "no_split_peak_hypothesis",
                "source_feature_family_ids": "FAM001",
            }
        ],
    )
    _write_tsv(
        activation_dir / "standard_peak_activation_inputs.tsv",
        (
            "schema_version",
            "source_run_id",
            "selected_activation_row_count",
            "next_action",
        ),
        [
            {
                "schema_version": "standard_peak_shadow_activation_inputs",
                "source_run_id": "synthetic-current-511-authority-replay",
                "selected_activation_row_count": "1",
                "next_action": "apply_matrix_only_activation",
            }
        ],
    )
    _write_tsv(
        activation_dir / "standard_peak_activation_values.tsv",
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
                "peak_hypothesis_id": "PH001",
                "feature_family_id": "FAM001",
                "sample_stem": "SampleB",
                "projected_matrix_value": "222.2",
                "projected_matrix_value_source": "standard_peak_shadow_projection",
                "current_raw_status": "backfilled",
                "current_production_status": "accepted_backfill",
                "source_artifact_schema_version": "shadow_production_projection",
                "source_artifact_sha256": "A" * 64,
                "source_row_sha256": "B" * 64,
                "source_provenance_detail": (
                    "family_overlay_gaussian_smoothed_standard_peak_supported"
                ),
            }
        ],
    )
    _write_tsv(
        activation_dir / "seed_guard_decisions.tsv",
        (
            "schema_version",
            "source_run_id",
            "feature_family_id",
            "peak_hypothesis_id",
            "sample_scope",
            "pre_backfill_matrix_path",
            "detected_count",
            "seed_floor",
            "seed_guard_status",
            "decision_reason",
        ),
        [
            {
                "schema_version": "standard_peak_seed_guard_decision",
                "source_run_id": "synthetic-current-511-authority-replay",
                "feature_family_id": "FAM001",
                "peak_hypothesis_id": "PH001",
                "sample_scope": "SampleB",
                "pre_backfill_matrix_path": (
                    "pre/alignment_matrix.pre_standard_peak_backfill.tsv"
                ),
                "detected_count": "1",
                "seed_floor": "1",
                "seed_guard_status": "eligible_continue_existing_gates",
                "decision_reason": "large_cohort_seed_floor_met",
            }
        ],
    )
    _write_tsv(
        activated_dir / "activation_value_delta.tsv",
        (
            "activation_value_delta_schema_version",
            "feature_family_id",
            "candidate_container_id",
            "sample_id",
            "peak_hypothesis_id",
            "activation_unit_scope",
            "activation_status",
            "product_effect",
            "contract_rule_id",
            "original_matrix_value",
            "activated_matrix_value",
            "matrix_value_kind",
            "matrix_value_source",
            "matrix_value_source_field",
            "matrix_value_source_detail",
            "matrix_value_source_artifact_schema_version",
            "matrix_value_source_artifact_sha256",
            "matrix_value_source_row_sha256",
            "source_cell_status",
            "source_cell_area",
            "matrix_value_effect",
            "value_changed",
            "activation_reason",
        ),
        [
            {
                "activation_value_delta_schema_version": (
                    "shared_peak_identity_activation_value_delta"
                ),
                "feature_family_id": "FAM001",
                "candidate_container_id": "PH001",
                "sample_id": "SampleB",
                "peak_hypothesis_id": "PH001",
                "activation_unit_scope": "peak_hypothesis",
                "activation_status": "auto_activate",
                "product_effect": "accept_label_or_rescue",
                "contract_rule_id": "machine_observed_sufficient_positive_identity",
                "original_matrix_value": "",
                "activated_matrix_value": "222.2",
                "matrix_value_kind": "backfill_activation",
                "matrix_value_source": "activation_values_tsv",
                "matrix_value_source_field": "projected_matrix_value",
                "matrix_value_source_detail": "standard_peak_shadow_projection",
                "matrix_value_source_artifact_schema_version": (
                    "shadow_production_projection"
                ),
                "matrix_value_source_artifact_sha256": "A" * 64,
                "matrix_value_source_row_sha256": "B" * 64,
                "source_cell_status": "backfilled",
                "source_cell_area": "222.2",
                "matrix_value_effect": "written",
                "value_changed": "TRUE",
                "activation_reason": (
                    "standard_peak_shift_aware_ms1_same_peak_product_authorized"
                ),
            }
        ],
    )
    return {"source_run": source_run, "output_dir": output_dir}


def _write_tsv(
    path: Path,
    fieldnames: tuple[str, ...],
    rows: list[dict[str, str]],
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, delimiter="\t", fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def _read_tsv(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle, delimiter="\t"))


def _sha256(path: Path) -> str:
    import hashlib

    return hashlib.sha256(path.read_bytes()).hexdigest().upper()
