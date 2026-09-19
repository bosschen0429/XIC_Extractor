import csv
import json
import subprocess
import sys
from pathlib import Path

from xic_extractor.alignment.quant_matrix_downstream_impact import (
    build_quant_matrix_downstream_impact_smoke,
)
from xic_extractor.alignment.quant_matrix_promotion import (
    PROMOTION_READINESS_SCHEMA,
    evaluate_quant_matrix_promotion_readiness,
)
from xic_extractor.alignment.quant_matrix_report import QUANT_MATRIX_REVIEW_SCHEMA
from xic_extractor.alignment.quant_matrix_validation_packet import (
    REQUIRED_SCIENCE_EVIDENCE,
    TIER_REQUIRED_METADATA,
    VALIDATION_EVIDENCE_ALLOWED_TIERS,
    VALIDATION_EVIDENCE_ROW_COLUMNS,
    VALIDATION_EVIDENCE_SCHEMA,
    VALIDATION_PACKET_SUMMARY_SCHEMA,
    ValidationEvidenceArtifact,
    build_quant_matrix_validation_evidence_packet,
    validate_quant_matrix_validation_evidence_packet,
)
from xic_extractor.alignment.quant_matrix_version import (
    CELL_PROVENANCE_COLUMNS,
    EXPECTED_DIFF_SUMMARY_COLUMNS,
    ROW_SUMMARY_COLUMNS,
)
from xic_extractor.tabular_io import file_sha256

ROOT = Path(__file__).resolve().parents[1]
VALIDATION_SCHEMA = (
    ROOT / "docs/superpowers/schemas/quant_matrix_validation_evidence_schema.json"
)


def test_quant_matrix_validation_evidence_schema_matches_builder_contract() -> None:
    schema = json.loads(VALIDATION_SCHEMA.read_text(encoding="utf-8"))

    assert schema["schema_version"] == "quant_matrix_validation_evidence_schema"
    assert schema["validation_evidence_schema"] == VALIDATION_EVIDENCE_SCHEMA
    assert schema["packet_summary_schema"] == VALIDATION_PACKET_SUMMARY_SCHEMA
    assert schema["evidence_row_columns"] == list(VALIDATION_EVIDENCE_ROW_COLUMNS)
    assert schema["allowed_tiers"] == list(VALIDATION_EVIDENCE_ALLOWED_TIERS)
    assert schema["required_science_evidence"] == {
        key: list(value) for key, value in REQUIRED_SCIENCE_EVIDENCE.items()
    }
    assert schema["required_tier_metadata"] == {
        key: list(value) for key, value in TIER_REQUIRED_METADATA.items()
    }
    assert schema["authority_rules"]["write_authority"] is False
    assert schema["authority_rules"]["does_not_run_scorer"] is True
    assert schema["authority_rules"]["does_not_read_raw_inputs"] is True
    assert schema["binding_rules"][
        "downstream_impact_smoke_must_validate_artifact_content"
    ]


def test_build_packet_copies_artifacts_and_records_source_hashes(
    tmp_path: Path,
) -> None:
    large = _write_json(tmp_path / "source" / "large.json", {"large": True})
    oracle = _write_json(tmp_path / "source" / "oracle.json", {"oracle": True})

    outputs = build_quant_matrix_validation_evidence_packet(
        output_dir=tmp_path / "packet",
        packet_id="test-packet",
        evidence_artifacts=[
            ValidationEvidenceArtifact(
                tier="full_cohort",
                status="pass",
                source_artifact=large,
                cohort_id="synthetic_85raw",
                raw_run_count=85,
            ),
            ValidationEvidenceArtifact(
                tier="heldout_oracle",
                status="pass",
                source_artifact=oracle,
                oracle_packet_id="synthetic_oracle",
            ),
        ],
    )

    evidence = json.loads(
        outputs["validation_evidence_json"].read_text(encoding="utf-8")
    )
    summary = json.loads(outputs["summary_json"].read_text(encoding="utf-8"))
    assert evidence["schema_version"] == VALIDATION_EVIDENCE_SCHEMA
    assert evidence["read_only"] is True
    assert evidence["write_authority"] is False
    assert summary["schema_version"] == VALIDATION_PACKET_SUMMARY_SCHEMA
    assert summary["missing_science_evidence"] == ["downstream_impact_smoke"]
    assert summary["validation_evidence_json_sha256"] == file_sha256(
        outputs["validation_evidence_json"],
    )

    rows = evidence["evidence"]
    assert [row["tier"] for row in rows] == ["full_cohort", "heldout_oracle"]
    for row in rows:
        copied = outputs["validation_evidence_json"].parent / row["artifact_path"]
        source = Path(row["source_artifact_path"])
        assert copied.is_file()
        assert row["artifact_sha256"] == file_sha256(copied)
        assert row["source_artifact_sha256"] == file_sha256(source)

    assert validate_quant_matrix_validation_evidence_packet(
        outputs["validation_evidence_json"],
    ) == []


def test_packet_can_bind_repo_relative_source_artifacts(tmp_path: Path) -> None:
    source = _write_json(tmp_path / "source" / "large.json", {"large": True})

    outputs = build_quant_matrix_validation_evidence_packet(
        output_dir=tmp_path / "packet",
        packet_id="test-packet",
        source_root=tmp_path,
        evidence_artifacts=[
            ValidationEvidenceArtifact(
                tier="full_cohort",
                status="pass",
                source_artifact=source.relative_to(tmp_path),
                cohort_id="synthetic_85raw",
                raw_run_count=85,
            )
        ],
    )

    evidence = json.loads(
        outputs["validation_evidence_json"].read_text(encoding="utf-8")
    )
    assert evidence["evidence"][0]["source_artifact_path"] == "source/large.json"
    assert (
        validate_quant_matrix_validation_evidence_packet(
            outputs["validation_evidence_json"],
            source_root=tmp_path,
        )
        == []
    )


def test_packet_check_rejects_top_level_authority_drift(tmp_path: Path) -> None:
    source = _write_json(tmp_path / "source" / "large.json", {"large": True})
    outputs = build_quant_matrix_validation_evidence_packet(
        output_dir=tmp_path / "packet",
        packet_id="test-packet",
        evidence_artifacts=[
            ValidationEvidenceArtifact(
                tier="full_cohort",
                status="pass",
                source_artifact=source,
                cohort_id="synthetic_85raw",
                raw_run_count=85,
            )
        ],
    )
    evidence = json.loads(
        outputs["validation_evidence_json"].read_text(encoding="utf-8")
    )
    evidence["read_only"] = False
    evidence["write_authority"] = True
    outputs["validation_evidence_json"].write_text(
        json.dumps(evidence, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    problems = validate_quant_matrix_validation_evidence_packet(
        outputs["validation_evidence_json"],
    )

    assert "read_only must be true" in problems
    assert "write_authority must be false" in problems


def test_packet_check_rejects_copied_artifact_hash_drift(tmp_path: Path) -> None:
    source = _write_json(tmp_path / "source" / "large.json", {"large": True})
    outputs = build_quant_matrix_validation_evidence_packet(
        output_dir=tmp_path / "packet",
        packet_id="test-packet",
        evidence_artifacts=[
            ValidationEvidenceArtifact(
                tier="full_cohort",
                status="pass",
                source_artifact=source,
                cohort_id="synthetic_85raw",
                raw_run_count=85,
            )
        ],
    )
    evidence = json.loads(
        outputs["validation_evidence_json"].read_text(encoding="utf-8")
    )
    copied = outputs["validation_evidence_json"].parent / evidence["evidence"][0][
        "artifact_path"
    ]
    copied.write_text('{"large": false}\n', encoding="utf-8")

    problems = validate_quant_matrix_validation_evidence_packet(
        outputs["validation_evidence_json"],
    )

    assert any("artifact_sha256 mismatch" in problem for problem in problems)


def test_packet_check_rejects_rows_tsv_drift(tmp_path: Path) -> None:
    source = _write_json(tmp_path / "source" / "large.json", {"large": True})
    outputs = build_quant_matrix_validation_evidence_packet(
        output_dir=tmp_path / "packet",
        packet_id="test-packet",
        evidence_artifacts=[
            ValidationEvidenceArtifact(
                tier="full_cohort",
                status="pass",
                source_artifact=source,
                cohort_id="synthetic_85raw",
                raw_run_count=85,
            )
        ],
    )
    rows_text = outputs["validation_evidence_rows_tsv"].read_text(encoding="utf-8")
    outputs["validation_evidence_rows_tsv"].write_text(
        rows_text.replace("full_cohort", "large_cohort_validation", 1),
        encoding="utf-8",
    )

    problems = validate_quant_matrix_validation_evidence_packet(
        outputs["validation_evidence_json"],
    )

    assert "quant_matrix_validation_evidence_rows.tsv mismatch" in problems


def test_packet_check_rejects_summary_hash_drift(tmp_path: Path) -> None:
    source = _write_json(tmp_path / "source" / "large.json", {"large": True})
    outputs = build_quant_matrix_validation_evidence_packet(
        output_dir=tmp_path / "packet",
        packet_id="test-packet",
        evidence_artifacts=[
            ValidationEvidenceArtifact(
                tier="full_cohort",
                status="pass",
                source_artifact=source,
                cohort_id="synthetic_85raw",
                raw_run_count=85,
            )
        ],
    )
    summary = json.loads(outputs["summary_json"].read_text(encoding="utf-8"))
    summary["validation_evidence_json_sha256"] = "0" * 64
    outputs["summary_json"].write_text(
        json.dumps(summary, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    problems = validate_quant_matrix_validation_evidence_packet(
        outputs["validation_evidence_json"],
    )

    assert "summary validation_evidence_json_sha256 mismatch" in problems


def test_packet_check_rejects_downstream_contract_fixture(tmp_path: Path) -> None:
    large = _write_json(tmp_path / "source" / "large.json", {"large": True})
    downstream = _write_downstream_impact_artifact(
        tmp_path,
        bundle_kind="contract_fixture",
    )
    outputs = build_quant_matrix_validation_evidence_packet(
        output_dir=tmp_path / "packet",
        packet_id="test-packet",
        evidence_artifacts=[
            ValidationEvidenceArtifact(
                tier="full_cohort",
                status="pass",
                source_artifact=large,
                cohort_id="synthetic_85raw",
                raw_run_count=85,
            ),
            ValidationEvidenceArtifact(
                tier="downstream_impact_smoke",
                status="pass",
                source_artifact=downstream,
                downstream_scope="synthetic_contract_fixture",
            ),
        ],
    )

    problems = validate_quant_matrix_validation_evidence_packet(
        outputs["validation_evidence_json"],
    )

    assert any("bundle_kind must be real_quant_matrix_version" in p for p in problems)


def test_packet_check_accepts_real_downstream_bundle(tmp_path: Path) -> None:
    large = _write_json(tmp_path / "source" / "large.json", {"large": True})
    oracle = _write_json(tmp_path / "source" / "oracle.json", {"oracle": True})
    downstream = _write_downstream_impact_artifact(tmp_path)
    outputs = build_quant_matrix_validation_evidence_packet(
        output_dir=tmp_path / "packet",
        packet_id="test-packet",
        evidence_artifacts=[
            ValidationEvidenceArtifact(
                tier="full_cohort",
                status="pass",
                source_artifact=large,
                cohort_id="synthetic_85raw",
                raw_run_count=85,
            ),
            ValidationEvidenceArtifact(
                tier="heldout_oracle",
                status="pass",
                source_artifact=oracle,
                oracle_packet_id="synthetic_oracle",
            ),
            ValidationEvidenceArtifact(
                tier="downstream_impact_smoke",
                status="pass",
                source_artifact=downstream,
                downstream_scope="synthetic_real_bundle",
            ),
        ],
    )

    evidence = json.loads(
        outputs["validation_evidence_json"].read_text(encoding="utf-8")
    )
    downstream_row = next(
        row for row in evidence["evidence"] if row["tier"] == "downstream_impact_smoke"
    )
    copied_summary = (
        outputs["validation_evidence_json"].parent / downstream_row["artifact_path"]
    )
    copied_payload = json.loads(copied_summary.read_text(encoding="utf-8"))
    copied_rows = copied_summary.parent / copied_payload["row_metrics_tsv"]

    assert copied_rows.is_file()
    assert (
        validate_quant_matrix_validation_evidence_packet(
            outputs["validation_evidence_json"],
        )
        == []
    )


def test_readiness_checker_is_inconclusive_when_downstream_evidence_missing(
    tmp_path: Path,
) -> None:
    inputs = _write_contract_ready_inputs(tmp_path / "inputs")
    large = _write_json(tmp_path / "source" / "large.json", {"large": True})
    oracle = _write_json(tmp_path / "source" / "oracle.json", {"oracle": True})
    packet = build_quant_matrix_validation_evidence_packet(
        output_dir=tmp_path / "packet",
        packet_id="test-packet",
        evidence_artifacts=[
            ValidationEvidenceArtifact(
                tier="full_cohort",
                status="pass",
                source_artifact=large,
                cohort_id="synthetic_85raw",
                raw_run_count=85,
            ),
            ValidationEvidenceArtifact(
                tier="heldout_oracle",
                status="pass",
                source_artifact=oracle,
                oracle_packet_id="synthetic_oracle",
            ),
        ],
    )

    outputs = evaluate_quant_matrix_promotion_readiness(
        expected_diff_summary_tsv=inputs["expected_diff_summary"],
        cell_provenance_tsv=inputs["cell_provenance"],
        row_summary_tsv=inputs["row_summary"],
        review_summary_json=inputs["review_summary"],
        validation_evidence_json=packet["validation_evidence_json"],
        output_dir=tmp_path / "readiness",
    )

    summary = json.loads(outputs["summary_json"].read_text(encoding="utf-8"))
    assert summary["schema_version"] == PROMOTION_READINESS_SCHEMA
    assert summary["contract_correctness_status"] == "pass"
    assert summary["scientific_confidence_status"] == "inconclusive"
    assert summary["readiness_label"] == "contract_ready_science_inconclusive"
    assert summary["production_ready"] is False
    assert summary["may_promote_default_quant_matrix"] is False
    assert summary["validation_tiers"] == {
        "full_cohort": "pass",
        "heldout_oracle": "pass",
    }
    assert summary["missing_science_evidence"] == ["downstream_impact_smoke"]


def test_packet_builder_script_check_only_round_trip(tmp_path: Path) -> None:
    large = _write_json(tmp_path / "source" / "large.json", {"large": True})
    oracle = _write_json(tmp_path / "source" / "oracle.json", {"oracle": True})
    output_dir = tmp_path / "packet"

    build = subprocess.run(
        [
            sys.executable,
            "scripts/build_quant_matrix_promotion_validation_packet.py",
            "--output-dir",
            str(output_dir),
            "--large-cohort-artifact",
            str(large),
            "--heldout-oracle-artifact",
            str(oracle),
            "--cohort-id",
            "synthetic_85raw",
            "--oracle-packet-id",
            "synthetic_oracle",
            "--write-readiness-fixture",
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    assert build.returncode == 0, build.stderr
    assert (output_dir / "readiness_integration_fixture").is_dir()

    check = subprocess.run(
        [
            sys.executable,
            "scripts/build_quant_matrix_promotion_validation_packet.py",
            "--output-dir",
            str(output_dir),
            "--check-only",
        ],
        check=False,
        capture_output=True,
        text=True,
    )

    assert check.returncode == 0, check.stderr
    assert "packet_status: pass" in check.stdout


def test_packet_builder_check_only_rejects_stale_readiness_fixture(
    tmp_path: Path,
) -> None:
    large = _write_json(tmp_path / "source" / "large.json", {"large": True})
    oracle = _write_json(tmp_path / "source" / "oracle.json", {"oracle": True})
    output_dir = tmp_path / "packet"
    build = subprocess.run(
        [
            sys.executable,
            "scripts/build_quant_matrix_promotion_validation_packet.py",
            "--output-dir",
            str(output_dir),
            "--large-cohort-artifact",
            str(large),
            "--heldout-oracle-artifact",
            str(oracle),
            "--cohort-id",
            "synthetic_85raw",
            "--oracle-packet-id",
            "synthetic_oracle",
            "--write-readiness-fixture",
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    assert build.returncode == 0, build.stderr
    readiness_summary = (
        output_dir
        / "readiness_integration_fixture"
        / "readiness"
        / "quant_matrix_promotion_readiness_summary.json"
    )
    summary = json.loads(readiness_summary.read_text(encoding="utf-8"))
    summary["production_ready"] = True
    readiness_summary.write_text(
        json.dumps(summary, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    check = subprocess.run(
        [
            sys.executable,
            "scripts/build_quant_matrix_promotion_validation_packet.py",
            "--output-dir",
            str(output_dir),
            "--check-only",
        ],
        check=False,
        capture_output=True,
        text=True,
    )

    assert check.returncode == 1
    assert "readiness fixture production_ready mismatch" in check.stderr


def test_packet_builder_check_only_rejects_readiness_fixture_input_drift(
    tmp_path: Path,
) -> None:
    large = _write_json(tmp_path / "source" / "large.json", {"large": True})
    oracle = _write_json(tmp_path / "source" / "oracle.json", {"oracle": True})
    output_dir = tmp_path / "packet"
    build = subprocess.run(
        [
            sys.executable,
            "scripts/build_quant_matrix_promotion_validation_packet.py",
            "--output-dir",
            str(output_dir),
            "--large-cohort-artifact",
            str(large),
            "--heldout-oracle-artifact",
            str(oracle),
            "--cohort-id",
            "synthetic_85raw",
            "--oracle-packet-id",
            "synthetic_oracle",
            "--write-readiness-fixture",
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    assert build.returncode == 0, build.stderr
    cell_provenance = (
        output_dir
        / "readiness_integration_fixture"
        / "inputs"
        / "cell_provenance.tsv"
    )
    text = cell_provenance.read_text(encoding="utf-8")
    cell_provenance.write_text(
        text.replace(
            "ProductionAcceptanceManifest\tTRUE",
            "ProductionAcceptanceManifest\tFALSE",
        ),
        encoding="utf-8",
    )

    check = subprocess.run(
        [
            sys.executable,
            "scripts/build_quant_matrix_promotion_validation_packet.py",
            "--output-dir",
            str(output_dir),
            "--check-only",
        ],
        check=False,
        capture_output=True,
        text=True,
    )

    assert check.returncode == 1
    assert "readiness fixture" in check.stderr


def _write_json(path: Path, payload: dict[str, object]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, sort_keys=True) + "\n", encoding="utf-8")
    return path


def _write_contract_ready_inputs(root: Path) -> dict[str, Path]:
    root.mkdir(parents=True, exist_ok=True)
    expected_diff_summary = root / "expected_diff_summary.tsv"
    _write_tsv(
        expected_diff_summary,
        EXPECTED_DIFF_SUMMARY_COLUMNS,
        [
            {
                "schema_version": "quant_matrix_version_expected_diff_summary",
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
                "schema_version": "quant_matrix_cell_provenance",
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
                "schema_version": "quant_matrix_cell_provenance",
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
                "schema_version": "quant_matrix_row_summary",
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


def _write_downstream_impact_artifact(
    root: Path,
    *,
    bundle_kind: str = "real_quant_matrix_version",
) -> Path:
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
                "schema_version": "quant_matrix_cell_provenance",
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
                "schema_version": "quant_matrix_cell_provenance",
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
                "schema_version": "quant_matrix_row_summary",
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
        output_dir=root / "source" / f"downstream_{bundle_kind}",
        downstream_scope=f"synthetic_{bundle_kind}",
        bundle_kind=bundle_kind,
    )
    return outputs["summary_json"]
