import csv
import json
import subprocess
import sys
from pathlib import Path

from xic_extractor.alignment.quant_matrix_downstream_impact import (
    build_quant_matrix_downstream_impact_smoke,
)
from xic_extractor.alignment.quant_matrix_promotion import (
    PROMOTION_CHECK_COLUMNS,
    PROMOTION_READINESS_SCHEMA,
    evaluate_quant_matrix_promotion_readiness,
)
from xic_extractor.alignment.quant_matrix_report import QUANT_MATRIX_REVIEW_SCHEMA
from xic_extractor.alignment.quant_matrix_version import (
    CELL_PROVENANCE_COLUMNS,
    EXPECTED_DIFF_SUMMARY_COLUMNS,
    ROW_SUMMARY_COLUMNS,
)
from xic_extractor.tabular_io import file_sha256

ROOT = Path(__file__).resolve().parents[1]
PROMOTION_SCHEMA = (
    ROOT / "docs/superpowers/schemas/quant_matrix_promotion_readiness_schema.v1.json"
)


def test_quant_matrix_promotion_schema_matches_public_outputs() -> None:
    schema = json.loads(PROMOTION_SCHEMA.read_text(encoding="utf-8"))

    assert schema["schema_version"] == "quant_matrix_promotion_readiness_schema_v1"
    assert schema["readiness_schema_version"] == PROMOTION_READINESS_SCHEMA
    assert schema["promotion_check_columns"] == list(PROMOTION_CHECK_COLUMNS)
    assert schema["authority_rules"]["contract_correctness_is_not_science"] is True
    assert schema["authority_rules"]["focused_tests_cannot_claim_production_ready"]
    assert (
        schema["authority_rules"]["required_science_pass_rows_must_be_artifact_bound"]
    )
    assert schema["authority_rules"]["duplicate_validation_tiers_fail_closed"]
    assert schema["required_science_tier_provenance"]["full_cohort"] == [
        "artifact_path",
        "artifact_sha256",
        "cohort_id",
        "raw_run_count",
    ]


def test_promotion_readiness_is_inconclusive_without_science_evidence(
    tmp_path: Path,
) -> None:
    inputs = _write_contract_ready_inputs(tmp_path)

    outputs = evaluate_quant_matrix_promotion_readiness(
        expected_diff_summary_tsv=inputs["expected_diff_summary"],
        cell_provenance_tsv=inputs["cell_provenance"],
        row_summary_tsv=inputs["row_summary"],
        review_summary_json=inputs["review_summary"],
        output_dir=tmp_path / "out",
    )

    summary = json.loads(outputs["summary_json"].read_text(encoding="utf-8"))
    assert summary["schema_version"] == PROMOTION_READINESS_SCHEMA
    assert summary["contract_correctness_status"] == "pass"
    assert summary["scientific_confidence_status"] == "inconclusive"
    assert summary["readiness_label"] == "contract_ready_science_inconclusive"
    assert summary["production_ready"] is False
    assert summary["may_promote_default_quant_matrix"] is False
    assert "large_cohort_validation" in summary["missing_science_evidence"]
    assert "heldout_oracle_or_manual_review" in summary["missing_science_evidence"]
    assert "downstream_impact_smoke" in summary["missing_science_evidence"]

    checks = _read_tsv(outputs["checks_tsv"])
    assert any(
        row["check_id"] == "contract_expected_diff_pass"
        and row["status"] == "pass"
        for row in checks
    )
    assert any(
        row["check_id"] == "science_large_cohort"
        and row["status"] == "missing"
        for row in checks
    )


def test_focused_or_fixture_cohort_only_validation_cannot_claim_production_ready(
    tmp_path: Path,
) -> None:
    inputs = _write_contract_ready_inputs(tmp_path)
    evidence = tmp_path / "validation_evidence.json"
    evidence.write_text(
        json.dumps(
            {
                "schema_version": "quant_matrix_validation_evidence_v1",
                "requested_readiness_label": "production_ready",
                "evidence": [
                    {"tier": "focused_tests", "status": "pass"},
                    {"tier": "fixture_cohort_smoke", "status": "pass"},
                ],
            }
        ),
        encoding="utf-8",
    )

    outputs = evaluate_quant_matrix_promotion_readiness(
        expected_diff_summary_tsv=inputs["expected_diff_summary"],
        cell_provenance_tsv=inputs["cell_provenance"],
        row_summary_tsv=inputs["row_summary"],
        review_summary_json=inputs["review_summary"],
        validation_evidence_json=evidence,
        output_dir=tmp_path / "out",
    )

    summary = json.loads(outputs["summary_json"].read_text(encoding="utf-8"))
    assert summary["production_ready"] is False
    assert summary["readiness_label"] == "contract_ready_science_inconclusive"
    assert "insufficient_validation_tier_for_production_ready" in summary["blockers"]


def test_required_science_strings_without_artifact_binding_cannot_promote(
    tmp_path: Path,
) -> None:
    inputs = _write_contract_ready_inputs(tmp_path)
    evidence = tmp_path / "validation_evidence.json"
    evidence.write_text(
        json.dumps(
            {
                "schema_version": "quant_matrix_validation_evidence_v1",
                "requested_readiness_label": "production_ready",
                "evidence": [
                    {"tier": "full_cohort", "status": "pass"},
                    {"tier": "heldout_oracle", "status": "pass"},
                    {"tier": "downstream_impact_smoke", "status": "pass"},
                ],
            }
        ),
        encoding="utf-8",
    )

    outputs = evaluate_quant_matrix_promotion_readiness(
        expected_diff_summary_tsv=inputs["expected_diff_summary"],
        cell_provenance_tsv=inputs["cell_provenance"],
        row_summary_tsv=inputs["row_summary"],
        review_summary_json=inputs["review_summary"],
        validation_evidence_json=evidence,
        output_dir=tmp_path / "out",
    )

    summary = json.loads(outputs["summary_json"].read_text(encoding="utf-8"))
    assert summary["production_ready"] is False
    assert summary["readiness_label"] == "contract_ready_science_failed"
    assert "validation_evidence_artifact_unbound" in summary["blockers"]


def test_duplicate_validation_tiers_fail_closed(tmp_path: Path) -> None:
    inputs = _write_contract_ready_inputs(tmp_path)
    evidence = tmp_path / "validation_evidence.json"
    evidence_rows = _science_ready_evidence_rows(tmp_path)
    evidence_rows.append({**evidence_rows[0], "status": "fail"})
    evidence.write_text(
        json.dumps(
            {
                "schema_version": "quant_matrix_validation_evidence_v1",
                "requested_readiness_label": "production_ready",
                "evidence": evidence_rows,
            }
        ),
        encoding="utf-8",
    )

    outputs = evaluate_quant_matrix_promotion_readiness(
        expected_diff_summary_tsv=inputs["expected_diff_summary"],
        cell_provenance_tsv=inputs["cell_provenance"],
        row_summary_tsv=inputs["row_summary"],
        review_summary_json=inputs["review_summary"],
        validation_evidence_json=evidence,
        output_dir=tmp_path / "out",
    )

    summary = json.loads(outputs["summary_json"].read_text(encoding="utf-8"))
    assert summary["production_ready"] is False
    assert "validation_evidence_duplicate_tier" in summary["blockers"]


def test_large_cohort_oracle_and_downstream_evidence_can_mark_science_ready(
    tmp_path: Path,
) -> None:
    inputs = _write_contract_ready_inputs(tmp_path)
    evidence = tmp_path / "validation_evidence.json"
    evidence.write_text(
        json.dumps(
            {
                "schema_version": "quant_matrix_validation_evidence_v1",
                "requested_readiness_label": "production_ready",
                "evidence": _science_ready_evidence_rows(tmp_path),
            }
        ),
        encoding="utf-8",
    )

    outputs = evaluate_quant_matrix_promotion_readiness(
        expected_diff_summary_tsv=inputs["expected_diff_summary"],
        cell_provenance_tsv=inputs["cell_provenance"],
        row_summary_tsv=inputs["row_summary"],
        review_summary_json=inputs["review_summary"],
        validation_evidence_json=evidence,
        output_dir=tmp_path / "out",
    )

    summary = json.loads(outputs["summary_json"].read_text(encoding="utf-8"))
    assert summary["contract_correctness_status"] == "pass"
    assert summary["scientific_confidence_status"] == "pass"
    assert summary["readiness_label"] == "production_ready_candidate_packet"
    assert summary["production_ready"] is True
    assert summary["may_promote_default_quant_matrix"] is True


def test_contract_failure_blocks_even_with_science_evidence(tmp_path: Path) -> None:
    inputs = _write_contract_ready_inputs(tmp_path)
    _write_tsv(
        inputs["expected_diff_summary"],
        EXPECTED_DIFF_SUMMARY_COLUMNS,
        [
            {
                "schema_version": "quant_matrix_version_expected_diff_summary_v1",
                "acceptance_status": "fail",
                "expected_diff_count": "1",
                "written_backfill_count": "1",
                "unused_expected_diff_count": "0",
                "blocking_reasons": "unexpected_delta",
            }
        ],
    )
    evidence = tmp_path / "validation_evidence.json"
    evidence.write_text(
        json.dumps(
            {
                "schema_version": "quant_matrix_validation_evidence_v1",
                "evidence": _science_ready_evidence_rows(tmp_path),
            }
        ),
        encoding="utf-8",
    )

    outputs = evaluate_quant_matrix_promotion_readiness(
        expected_diff_summary_tsv=inputs["expected_diff_summary"],
        cell_provenance_tsv=inputs["cell_provenance"],
        row_summary_tsv=inputs["row_summary"],
        review_summary_json=inputs["review_summary"],
        validation_evidence_json=evidence,
        output_dir=tmp_path / "out",
    )

    summary = json.loads(outputs["summary_json"].read_text(encoding="utf-8"))
    assert summary["contract_correctness_status"] == "fail"
    assert summary["production_ready"] is False
    assert "contract_expected_diff_not_pass" in summary["blockers"]


def test_invalid_cell_provenance_hash_format_fails_contract(tmp_path: Path) -> None:
    inputs = _write_contract_ready_inputs(tmp_path)
    rows = _read_tsv(inputs["cell_provenance"])
    rows[1]["source_artifact_sha256"] = "not-a-sha"
    _write_tsv(inputs["cell_provenance"], CELL_PROVENANCE_COLUMNS, rows)

    outputs = evaluate_quant_matrix_promotion_readiness(
        expected_diff_summary_tsv=inputs["expected_diff_summary"],
        cell_provenance_tsv=inputs["cell_provenance"],
        row_summary_tsv=inputs["row_summary"],
        review_summary_json=inputs["review_summary"],
        output_dir=tmp_path / "out",
    )

    summary = json.loads(outputs["summary_json"].read_text(encoding="utf-8"))
    assert summary["contract_correctness_status"] == "fail"
    assert "cell_provenance_authority_invalid" in summary["blockers"]


def test_promotion_readiness_script_entrypoint_works(tmp_path: Path) -> None:
    inputs = _write_contract_ready_inputs(tmp_path)
    output_dir = tmp_path / "script_out"

    completed = subprocess.run(
        [
            sys.executable,
            "scripts/check_quant_matrix_promotion_readiness.py",
            "--expected-diff-summary-tsv",
            str(inputs["expected_diff_summary"]),
            "--cell-provenance-tsv",
            str(inputs["cell_provenance"]),
            "--row-summary-tsv",
            str(inputs["row_summary"]),
            "--review-summary-json",
            str(inputs["review_summary"]),
            "--output-dir",
            str(output_dir),
        ],
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 0, completed.stderr
    assert "readiness_label: contract_ready_science_inconclusive" in completed.stdout
    assert (output_dir / "quant_matrix_promotion_readiness_summary.json").is_file()


def test_require_production_ready_exits_nonzero_when_science_missing(
    tmp_path: Path,
) -> None:
    inputs = _write_contract_ready_inputs(tmp_path)

    completed = subprocess.run(
        [
            sys.executable,
            "scripts/check_quant_matrix_promotion_readiness.py",
            "--expected-diff-summary-tsv",
            str(inputs["expected_diff_summary"]),
            "--cell-provenance-tsv",
            str(inputs["cell_provenance"]),
            "--row-summary-tsv",
            str(inputs["row_summary"]),
            "--review-summary-json",
            str(inputs["review_summary"]),
            "--output-dir",
            str(tmp_path / "script_out"),
            "--require-production-ready",
        ],
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 1
    assert "production_ready: False" in completed.stdout


def _write_contract_ready_inputs(root: Path) -> dict[str, Path]:
    expected_diff_summary = root / "expected_diff_summary.tsv"
    _write_tsv(
        expected_diff_summary,
        EXPECTED_DIFF_SUMMARY_COLUMNS,
        [
            {
                "schema_version": "quant_matrix_version_expected_diff_summary_v1",
                "acceptance_status": "pass",
                "expected_diff_count": "1",
                "written_backfill_count": "1",
                "unused_expected_diff_count": "0",
                "blocking_reasons": "",
            }
        ],
    )
    cell_provenance = root / "cell_provenance.tsv"
    _write_tsv(
        cell_provenance,
        CELL_PROVENANCE_COLUMNS,
        [
            {
                "schema_version": "quant_matrix_cell_provenance_v1",
                "peak_hypothesis_id": "PH001",
                "sample_stem": "SampleA",
                "source_feature_family_ids": "FAM001",
                "matrix_value": "100",
                "cell_status": "detected",
                "value_source": "input_quant_matrix",
                "write_authority": "FALSE",
                "acceptance_decision": "",
                "acceptance_basis": "",
                "truth_status": "",
                "quant_value_source": "",
                "matrix_area_source": "",
                "source_artifact_relpath": "",
                "source_artifact_sha256": "",
                "source_row_sha256": "",
                "manifest_sha256": "",
            },
            {
                "schema_version": "quant_matrix_cell_provenance_v1",
                "peak_hypothesis_id": "PH001",
                "sample_stem": "SampleB",
                "source_feature_family_ids": "FAM001",
                "matrix_value": "222.2",
                "cell_status": "accepted_backfill",
                "value_source": "ProductionAcceptanceManifest",
                "write_authority": "TRUE",
                "acceptance_decision": "accept_basic_backfill",
                "acceptance_basis": "machine_basic",
                "truth_status": "not_truth_claimed",
                "quant_value_source": "gaussian_smoothed_integration",
                "matrix_area_source": "gaussian_smoothed_boundary_integration",
                "source_artifact_relpath": "sources/cell_evidence.tsv",
                "source_artifact_sha256": "A" * 64,
                "source_row_sha256": "B" * 64,
                "manifest_sha256": "C" * 64,
            },
        ],
    )
    row_summary = root / "row_summary.tsv"
    _write_tsv(
        row_summary,
        ROW_SUMMARY_COLUMNS,
        [
            {
                "schema_version": "quant_matrix_row_summary_v1",
                "peak_hypothesis_id": "PH001",
                "source_feature_family_ids": "FAM001",
                "detected_count": "1",
                "accepted_backfilled_count": "1",
                "quant_available_count": "2",
                "missing_count": "0",
                "backfill_fraction": "0.500000",
                "prevalence_flags": "low_seed_support",
            }
        ],
    )
    review_summary = root / "quant_matrix_review_summary.json"
    review_summary.write_text(
        json.dumps(
            {
                "schema_version": QUANT_MATRIX_REVIEW_SCHEMA,
                "validation_label": "shadow_review",
                "accepted_backfill_count": 1,
                "detected_count": 1,
                "report_only_risk_count": 1,
            }
        ),
        encoding="utf-8",
    )
    return {
        "expected_diff_summary": expected_diff_summary,
        "cell_provenance": cell_provenance,
        "row_summary": row_summary,
        "review_summary": review_summary,
    }


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


def _science_ready_evidence_rows(root: Path) -> list[dict[str, str]]:
    cohort = _write_evidence_artifact(root, "full_cohort.json")
    oracle = _write_evidence_artifact(root, "heldout_oracle.json")
    downstream = _write_downstream_impact_artifact(root)
    return [
        {
            "tier": "full_cohort",
            "status": "pass",
            "artifact_path": cohort.relative_to(root).as_posix(),
            "artifact_sha256": file_sha256(cohort),
            "cohort_id": "synthetic_full_cohort_gate_fixture",
            "raw_run_count": "85",
        },
        {
            "tier": "heldout_oracle",
            "status": "pass",
            "artifact_path": oracle.relative_to(root).as_posix(),
            "artifact_sha256": file_sha256(oracle),
            "oracle_packet_id": "synthetic_heldout_oracle_fixture",
        },
        {
            "tier": "downstream_impact_smoke",
            "status": "pass",
            "artifact_path": downstream.relative_to(root).as_posix(),
            "artifact_sha256": file_sha256(downstream),
            "downstream_scope": "synthetic_loess_gate_fixture",
        },
    ]


def _write_evidence_artifact(root: Path, filename: str) -> Path:
    path = root / "validation_artifacts" / filename
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"artifact": filename}) + "\n", encoding="utf-8")
    return path


def _write_downstream_impact_artifact(root: Path) -> Path:
    input_dir = root / "downstream_inputs"
    input_dir.mkdir(parents=True, exist_ok=True)
    quant_matrix = input_dir / "quant_matrix.tsv"
    _write_tsv(
        quant_matrix,
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
    cell_provenance = input_dir / "cell_provenance.tsv"
    _write_tsv(
        cell_provenance,
        CELL_PROVENANCE_COLUMNS,
        [
            {
                "schema_version": "quant_matrix_cell_provenance_v1",
                "peak_hypothesis_id": "PH001",
                "sample_stem": "SampleA",
                "source_feature_family_ids": "FAM001",
                "matrix_value": "100",
                "cell_status": "detected",
                "value_source": "input_quant_matrix",
                "write_authority": "FALSE",
                "acceptance_decision": "",
                "acceptance_basis": "",
                "truth_status": "",
                "quant_value_source": "",
                "matrix_area_source": "",
                "source_artifact_relpath": "",
                "source_artifact_sha256": "",
                "source_row_sha256": "",
                "manifest_sha256": "",
            },
            {
                "schema_version": "quant_matrix_cell_provenance_v1",
                "peak_hypothesis_id": "PH001",
                "sample_stem": "SampleB",
                "source_feature_family_ids": "FAM001",
                "matrix_value": "222.2",
                "cell_status": "accepted_backfill",
                "value_source": "ProductionAcceptanceManifest",
                "write_authority": "TRUE",
                "acceptance_decision": "accept_basic_backfill",
                "acceptance_basis": "machine_basic",
                "truth_status": "not_truth_claimed",
                "quant_value_source": "gaussian_smoothed_integration",
                "matrix_area_source": "gaussian_smoothed_boundary_integration",
                "source_artifact_relpath": "sources/cell_evidence.tsv",
                "source_artifact_sha256": "A" * 64,
                "source_row_sha256": "B" * 64,
                "manifest_sha256": "C" * 64,
            },
        ],
    )
    row_summary = input_dir / "row_summary.tsv"
    _write_tsv(
        row_summary,
        ROW_SUMMARY_COLUMNS,
        [
            {
                "schema_version": "quant_matrix_row_summary_v1",
                "peak_hypothesis_id": "PH001",
                "source_feature_family_ids": "FAM001",
                "detected_count": "1",
                "accepted_backfilled_count": "1",
                "quant_available_count": "2",
                "missing_count": "1",
                "backfill_fraction": "0.500000",
                "prevalence_flags": "low_seed_support",
            }
        ],
    )
    outputs = build_quant_matrix_downstream_impact_smoke(
        quant_matrix_tsv=quant_matrix,
        cell_provenance_tsv=cell_provenance,
        row_summary_tsv=row_summary,
        output_dir=root / "validation_artifacts" / "downstream_impact_smoke",
        downstream_scope="synthetic_loess_gate_fixture",
        bundle_kind="real_quant_matrix_version",
    )
    return outputs["summary_json"]
