import csv
import hashlib
from pathlib import Path

import pytest

import scripts.build_quant_matrix_version as activation_module
from scripts.build_quant_matrix_version import run_activation
from scripts.check_production_acceptance_manifest import (
    REQUIRED_COLUMNS as ACCEPTANCE_COLUMNS,
)
from scripts.check_production_acceptance_manifest import (
    production_acceptance_manifest_sha256,
)
from xic_extractor.alignment.quant_matrix_version import (
    EXPECTED_DIFF_COLUMNS,
    detected_only_matrix_rows_from_quant_version,
)


def test_quant_matrix_version_writes_accepted_backfill_with_provenance(
    tmp_path: Path,
) -> None:
    inputs = _write_activation_inputs(tmp_path)

    outputs = run_activation(
        input_quant_matrix_tsv=inputs["matrix"],
        input_matrix_identity_tsv=inputs["identity"],
        production_acceptance_manifest_tsv=inputs["manifest"],
        expected_diff_tsv=inputs["expected_diff"],
        output_dir=tmp_path / "out",
    )

    matrix_rows = _read_tsv(outputs["quant_matrix"])
    assert matrix_rows == [
        {"Mz": "101.1", "RT": "5.5", "SampleA": "100", "SampleB": "222.2"},
    ]

    provenance_rows = _read_tsv(outputs["cell_provenance"])
    assert len(provenance_rows) == 2
    detected = _row_by_sample(provenance_rows, "SampleA")
    backfilled = _row_by_sample(provenance_rows, "SampleB")
    assert detected["cell_status"] == "detected"
    assert detected["write_authority"] == "FALSE"
    assert detected["value_source"] == "input_quant_matrix"
    assert backfilled["cell_status"] == "accepted_backfill"
    assert backfilled["write_authority"] == "TRUE"
    assert backfilled["acceptance_decision"] == "accept_basic_backfill"
    assert backfilled["source_artifact_relpath"] == "sources/cell_evidence.tsv"
    assert backfilled["manifest_sha256"] == _read_tsv(inputs["manifest"])[0][
        "manifest_sha256"
    ]

    summary_rows = _read_tsv(outputs["row_summary"])
    assert summary_rows == [
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
    ]

    detected_only = detected_only_matrix_rows_from_quant_version(
        quant_matrix_rows=matrix_rows,
        cell_provenance_rows=provenance_rows,
    )
    assert detected_only == [{"Mz": "101.1", "RT": "5.5", "SampleA": "100"}]

    expected_summary = _read_tsv(outputs["expected_diff_summary"])
    assert expected_summary[0]["acceptance_status"] == "pass"
    assert expected_summary[0]["written_backfill_count"] == "1"


def test_detected_only_matrix_uses_identity_rows_across_blank_matrix_rows() -> None:
    detected_only = detected_only_matrix_rows_from_quant_version(
        quant_matrix_rows=(
            {"Mz": "100", "RT": "1", "SampleA": ""},
            {"Mz": "200", "RT": "2", "SampleA": "20"},
        ),
        cell_provenance_rows=(
            {
                "peak_hypothesis_id": "PH002",
                "sample_stem": "SampleA",
                "cell_status": "detected",
            },
        ),
        matrix_identity_rows=(
            {"matrix_row_index": "1", "peak_hypothesis_id": "PH001"},
            {"matrix_row_index": "2", "peak_hypothesis_id": "PH002"},
        ),
    )

    assert detected_only == [{"Mz": "200", "RT": "2", "SampleA": "20"}]


def test_quant_matrix_activation_consumes_the_checked_manifest_snapshot(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    inputs = _write_activation_inputs(tmp_path)
    original_manifest_sha = _sha256(inputs["manifest"])
    checked_snapshots = []
    original_loader = activation_module.load_checked_production_acceptance_manifest

    def load_then_replace(**kwargs):
        checked = original_loader(**kwargs)
        checked_snapshots.append(checked)
        rows = _read_tsv(inputs["manifest"])
        rows[0]["source_row_sha256"] = "B" * 64
        replacement_sha = production_acceptance_manifest_sha256(rows)
        rows[0]["manifest_sha256"] = replacement_sha
        _write_tsv(inputs["manifest"], ACCEPTANCE_COLUMNS, rows)
        return checked

    monkeypatch.setattr(
        activation_module,
        "load_checked_production_acceptance_manifest",
        load_then_replace,
    )
    outputs = run_activation(
        input_quant_matrix_tsv=inputs["matrix"],
        input_matrix_identity_tsv=inputs["identity"],
        production_acceptance_manifest_tsv=inputs["manifest"],
        expected_diff_tsv=inputs["expected_diff"],
        output_dir=tmp_path / "out",
    )

    provenance = _row_by_sample(_read_tsv(outputs["cell_provenance"]), "SampleB")
    assert provenance["source_row_sha256"] == "A" * 64
    source_summary = _read_tsv(outputs["source_summary"])[0]
    assert source_summary["production_acceptance_manifest_sha256"] == (
        original_manifest_sha
    )
    with pytest.raises(TypeError):
        checked_snapshots[0].rows[0]["source_row_sha256"] = "C" * 64


def test_quant_matrix_version_requires_expected_diff_for_backfill_write(
    tmp_path: Path,
) -> None:
    inputs = _write_activation_inputs(tmp_path, expected_diff_rows=[])

    with pytest.raises(ValueError, match="missing expected-diff row"):
        run_activation(
            input_quant_matrix_tsv=inputs["matrix"],
            input_matrix_identity_tsv=inputs["identity"],
            production_acceptance_manifest_tsv=inputs["manifest"],
            expected_diff_tsv=inputs["expected_diff"],
            output_dir=tmp_path / "out",
        )


def test_quant_matrix_version_rejects_overwriting_detected_values(
    tmp_path: Path,
) -> None:
    inputs = _write_activation_inputs(tmp_path, sample_b_value="111")

    with pytest.raises(ValueError, match="cannot overwrite existing quant value"):
        run_activation(
            input_quant_matrix_tsv=inputs["matrix"],
            input_matrix_identity_tsv=inputs["identity"],
            production_acceptance_manifest_tsv=inputs["manifest"],
            expected_diff_tsv=inputs["expected_diff"],
            output_dir=tmp_path / "out",
        )


def test_quant_matrix_version_rejects_duplicate_matrix_row_identity(
    tmp_path: Path,
) -> None:
    inputs = _write_activation_inputs(tmp_path)
    _write_tsv(
        inputs["identity"],
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
            },
            {
                "matrix_row_index": "1",
                "Mz": "101.1",
                "RT": "5.5",
                "peak_hypothesis_id": "PH002",
                "row_identity_basis": "no_split_peak_hypothesis",
                "source_feature_family_ids": "FAM002",
            },
        ],
    )

    with pytest.raises(ValueError, match="duplicate matrix_row_index"):
        run_activation(
            input_quant_matrix_tsv=inputs["matrix"],
            input_matrix_identity_tsv=inputs["identity"],
            production_acceptance_manifest_tsv=inputs["manifest"],
            expected_diff_tsv=inputs["expected_diff"],
            output_dir=tmp_path / "out",
        )


def _write_activation_inputs(
    root: Path,
    *,
    sample_b_value: str = "",
    expected_diff_rows: list[dict[str, str]] | None = None,
) -> dict[str, Path]:
    matrix = root / "alignment_matrix.tsv"
    _write_tsv(
        matrix,
        ("Mz", "RT", "SampleA", "SampleB"),
        [{"Mz": "101.1", "RT": "5.5", "SampleA": "100", "SampleB": sample_b_value}],
    )
    identity = root / "alignment_matrix_identity.tsv"
    _write_tsv(
        identity,
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
    source = _write_source(root, "sources/cell_evidence.tsv", "cell\tPH001\n")
    doublet = _write_source(root, "sources/doublet.tsv", "doublet\tPH001\n")
    manifest = root / "production_acceptance.tsv"
    manifest_rows = [_acceptance_row(source, doublet)]
    manifest_sha = production_acceptance_manifest_sha256(manifest_rows)
    for row in manifest_rows:
        row["manifest_sha256"] = manifest_sha
    _write_tsv(manifest, ACCEPTANCE_COLUMNS, manifest_rows)
    expected_diff = root / "expected_diff.tsv"
    if expected_diff_rows is None:
        expected_diff_rows = [
            {
                "schema_version": "quant_matrix_version_expected_diff",
                "peak_hypothesis_id": "PH001",
                "sample_stem": "SampleB",
                "baseline_value": "",
                "activated_value": "222.2",
                "expected_matrix_effect": "write_accepted_backfill",
                "expected_reason": "quant_matrix_version_fixture",
            }
        ]
    _write_tsv(expected_diff, EXPECTED_DIFF_COLUMNS, expected_diff_rows)
    return {
        "matrix": matrix,
        "identity": identity,
        "manifest": manifest,
        "expected_diff": expected_diff,
    }


def _acceptance_row(source: Path, doublet: Path) -> dict[str, str]:
    return {
        "schema_version": "production_acceptance_manifest",
        "peak_hypothesis_id": "PH001",
        "sample_stem": "SampleB",
        "feature_family_id": "FAM001",
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
        "decision_reason": "quant_matrix_version_fixture",
        "next_evidence_needed": "",
        "doublet_status": "no_doublet_claim",
        "reference_side": "not_applicable",
        "doublet_allowed": "TRUE",
        "doublet_source_relpath": "sources/doublet.tsv",
        "doublet_source_sha256": _sha256(doublet),
        "source_artifact_relpath": "sources/cell_evidence.tsv",
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


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest().upper()


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


def _row_by_sample(rows: list[dict[str, str]], sample_stem: str) -> dict[str, str]:
    return next(row for row in rows if row["sample_stem"] == sample_stem)
