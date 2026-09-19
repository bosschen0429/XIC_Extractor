import csv
import json
import subprocess
import sys
from pathlib import Path

from xic_extractor.alignment.quant_matrix_downstream_impact import (
    DOWNSTREAM_IMPACT_BUNDLE_KINDS,
    DOWNSTREAM_IMPACT_ROW_COLUMNS,
    DOWNSTREAM_IMPACT_SCHEMA,
    build_quant_matrix_downstream_impact_smoke,
    validate_quant_matrix_downstream_impact_smoke,
)
from xic_extractor.tabular_io import file_sha256

ROOT = Path(__file__).resolve().parents[1]
SCHEMA_PATH = (
    ROOT
    / "docs"
    / "superpowers"
    / "schemas"
    / "quant_matrix_downstream_impact_smoke_schema.json"
)


def test_downstream_impact_schema_matches_builder_contract() -> None:
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))

    assert schema["schema_version"] == "quant_matrix_downstream_impact_smoke_schema"
    assert schema["downstream_impact_schema"] == DOWNSTREAM_IMPACT_SCHEMA
    assert schema["bundle_kinds"] == list(DOWNSTREAM_IMPACT_BUNDLE_KINDS)
    assert schema["row_columns"] == list(DOWNSTREAM_IMPACT_ROW_COLUMNS)
    assert schema["authority_rules"]["read_only"] is True
    assert schema["authority_rules"]["write_authority"] is False
    assert schema["authority_rules"]["contract_fixture_cannot_satisfy_promotion"]
    assert schema["promotion_binding_rules"][
        "metrics_must_recompute_from_input_artifacts"
    ]


def test_downstream_impact_smoke_passes_for_real_quant_matrix_version(
    tmp_path: Path,
) -> None:
    inputs = _write_quant_matrix_version_outputs(tmp_path / "inputs")

    outputs = build_quant_matrix_downstream_impact_smoke(
        quant_matrix_tsv=inputs["quant_matrix"],
        cell_provenance_tsv=inputs["cell_provenance"],
        row_summary_tsv=inputs["row_summary"],
        output_dir=tmp_path / "smoke",
        downstream_scope="synthetic_loess_input_contract",
        bundle_kind="real_quant_matrix_version",
    )

    summary = json.loads(outputs["summary_json"].read_text(encoding="utf-8"))
    assert summary["status"] == "pass"
    assert summary["production_promotion_eligible"] is True
    assert summary["write_authority"] is False
    assert summary["product_writer_changed"] is False
    assert summary["default_quant_matrix_changed"] is False
    assert summary["metrics"]["detected_cell_count"] == 1
    assert summary["metrics"]["accepted_backfilled_cell_count"] == 1
    assert summary["metrics"]["missing_cell_reduction_count"] == 1
    assert summary["row_metrics_tsv_sha256"] == file_sha256(outputs["rows_tsv"])
    assert validate_quant_matrix_downstream_impact_smoke(outputs["summary_json"]) == []


def test_downstream_impact_contract_fixture_cannot_satisfy_promotion(
    tmp_path: Path,
) -> None:
    inputs = _write_quant_matrix_version_outputs(tmp_path / "inputs")
    outputs = build_quant_matrix_downstream_impact_smoke(
        quant_matrix_tsv=inputs["quant_matrix"],
        cell_provenance_tsv=inputs["cell_provenance"],
        row_summary_tsv=inputs["row_summary"],
        output_dir=tmp_path / "smoke",
        downstream_scope="synthetic_contract_fixture",
        bundle_kind="contract_fixture",
    )

    problems = validate_quant_matrix_downstream_impact_smoke(outputs["summary_json"])

    assert "downstream impact bundle_kind must be real_quant_matrix_version" in problems
    assert (
        "downstream impact production_promotion_eligible must be true" in problems
    )


def test_downstream_impact_rejects_rows_hash_drift(tmp_path: Path) -> None:
    inputs = _write_quant_matrix_version_outputs(tmp_path / "inputs")
    outputs = build_quant_matrix_downstream_impact_smoke(
        quant_matrix_tsv=inputs["quant_matrix"],
        cell_provenance_tsv=inputs["cell_provenance"],
        row_summary_tsv=inputs["row_summary"],
        output_dir=tmp_path / "smoke",
        downstream_scope="synthetic_loess_input_contract",
        bundle_kind="real_quant_matrix_version",
    )
    outputs["rows_tsv"].write_text(
        outputs["rows_tsv"].read_text(encoding="utf-8").replace("\t1\t", "\t2\t", 1),
        encoding="utf-8",
    )

    problems = validate_quant_matrix_downstream_impact_smoke(outputs["summary_json"])

    assert "downstream impact row_metrics_tsv_sha256 mismatch" in problems


def test_downstream_impact_rejects_missing_rows_tsv(tmp_path: Path) -> None:
    inputs = _write_quant_matrix_version_outputs(tmp_path / "inputs")
    outputs = build_quant_matrix_downstream_impact_smoke(
        quant_matrix_tsv=inputs["quant_matrix"],
        cell_provenance_tsv=inputs["cell_provenance"],
        row_summary_tsv=inputs["row_summary"],
        output_dir=tmp_path / "smoke",
        downstream_scope="synthetic_loess_input_contract",
        bundle_kind="real_quant_matrix_version",
    )
    outputs["rows_tsv"].unlink()

    problems = validate_quant_matrix_downstream_impact_smoke(outputs["summary_json"])

    assert "downstream impact row_metrics_tsv does not exist" in problems


def test_downstream_impact_rejects_input_artifact_hash_drift(
    tmp_path: Path,
) -> None:
    inputs = _write_quant_matrix_version_outputs(tmp_path / "inputs")
    outputs = build_quant_matrix_downstream_impact_smoke(
        quant_matrix_tsv=inputs["quant_matrix"],
        cell_provenance_tsv=inputs["cell_provenance"],
        row_summary_tsv=inputs["row_summary"],
        output_dir=tmp_path / "smoke",
        downstream_scope="synthetic_loess_input_contract",
        bundle_kind="real_quant_matrix_version",
    )
    inputs["quant_matrix"].write_text(
        inputs["quant_matrix"].read_text(encoding="utf-8").replace("222.2", "333.3"),
        encoding="utf-8",
    )

    problems = validate_quant_matrix_downstream_impact_smoke(outputs["summary_json"])

    assert (
        "downstream impact input_artifacts.quant_matrix_sha256 mismatch" in problems
    )
    assert any(
        "downstream impact recompute problems" in problem for problem in problems
    )


def test_downstream_impact_rejects_semantic_rows_drift_with_matching_hash(
    tmp_path: Path,
) -> None:
    inputs = _write_quant_matrix_version_outputs(tmp_path / "inputs")
    outputs = build_quant_matrix_downstream_impact_smoke(
        quant_matrix_tsv=inputs["quant_matrix"],
        cell_provenance_tsv=inputs["cell_provenance"],
        row_summary_tsv=inputs["row_summary"],
        output_dir=tmp_path / "smoke",
        downstream_scope="synthetic_loess_input_contract",
        bundle_kind="real_quant_matrix_version",
    )
    rows_text = outputs["rows_tsv"].read_text(encoding="utf-8")
    outputs["rows_tsv"].write_text(
        rows_text.replace("\t1\t1\t2\t", "\t1\t0\t1\t", 1),
        encoding="utf-8",
    )
    summary = json.loads(outputs["summary_json"].read_text(encoding="utf-8"))
    summary["row_metrics_tsv_sha256"] = file_sha256(outputs["rows_tsv"])
    outputs["summary_json"].write_text(
        json.dumps(summary, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    problems = validate_quant_matrix_downstream_impact_smoke(outputs["summary_json"])

    assert "downstream impact row_metrics_tsv does not match inputs" in problems


def test_downstream_impact_rejects_metrics_drift_with_matching_rows(
    tmp_path: Path,
) -> None:
    inputs = _write_quant_matrix_version_outputs(tmp_path / "inputs")
    outputs = build_quant_matrix_downstream_impact_smoke(
        quant_matrix_tsv=inputs["quant_matrix"],
        cell_provenance_tsv=inputs["cell_provenance"],
        row_summary_tsv=inputs["row_summary"],
        output_dir=tmp_path / "smoke",
        downstream_scope="synthetic_loess_input_contract",
        bundle_kind="real_quant_matrix_version",
    )
    summary = json.loads(outputs["summary_json"].read_text(encoding="utf-8"))
    summary["metrics"]["detected_cell_count"] = 999
    outputs["summary_json"].write_text(
        json.dumps(summary, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    problems = validate_quant_matrix_downstream_impact_smoke(outputs["summary_json"])

    assert "downstream impact metrics do not match inputs" in problems


def test_downstream_impact_script_check_only_round_trip(tmp_path: Path) -> None:
    inputs = _write_quant_matrix_version_outputs(tmp_path / "inputs")
    output_dir = tmp_path / "smoke"

    build = subprocess.run(
        [
            sys.executable,
            "scripts/build_quant_matrix_downstream_impact_smoke.py",
            "--quant-matrix-tsv",
            str(inputs["quant_matrix"]),
            "--cell-provenance-tsv",
            str(inputs["cell_provenance"]),
            "--row-summary-tsv",
            str(inputs["row_summary"]),
            "--output-dir",
            str(output_dir),
            "--downstream-scope",
            "synthetic_loess_input_contract",
            "--bundle-kind",
            "real_quant_matrix_version",
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    assert build.returncode == 0, build.stderr

    check = subprocess.run(
        [
            sys.executable,
            "scripts/build_quant_matrix_downstream_impact_smoke.py",
            "--quant-matrix-tsv",
            str(inputs["quant_matrix"]),
            "--cell-provenance-tsv",
            str(inputs["cell_provenance"]),
            "--row-summary-tsv",
            str(inputs["row_summary"]),
            "--output-dir",
            str(output_dir),
            "--check-only",
        ],
        check=False,
        capture_output=True,
        text=True,
    )

    assert check.returncode == 0, check.stderr
    assert "downstream_impact_status: pass" in check.stdout


def _write_quant_matrix_version_outputs(root: Path) -> dict[str, Path]:
    root.mkdir(parents=True, exist_ok=True)
    quant_matrix = root / "quant_matrix.tsv"
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
    cell_provenance = root / "cell_provenance.tsv"
    _write_tsv(
        cell_provenance,
        (
            "schema_version",
            "peak_hypothesis_id",
            "sample_stem",
            "source_feature_family_ids",
            "matrix_value",
            "cell_status",
            "value_source",
            "write_authority",
            "acceptance_decision",
            "acceptance_basis",
            "truth_status",
            "quant_value_source",
            "matrix_area_source",
            "source_artifact_relpath",
            "source_artifact_sha256",
            "source_row_sha256",
            "manifest_sha256",
        ),
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
        (
            "schema_version",
            "peak_hypothesis_id",
            "source_feature_family_ids",
            "detected_count",
            "accepted_backfilled_count",
            "quant_available_count",
            "missing_count",
            "backfill_fraction",
            "prevalence_flags",
        ),
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
    return {
        "quant_matrix": quant_matrix,
        "cell_provenance": cell_provenance,
        "row_summary": row_summary,
    }


def _write_tsv(
    path: Path,
    fieldnames: tuple[str, ...],
    rows: list[dict[str, str]],
) -> None:
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, delimiter="\t", fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)
