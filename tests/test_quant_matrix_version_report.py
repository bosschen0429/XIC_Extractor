import csv
import hashlib
import json
import subprocess
import sys
from pathlib import Path

import pytest

from scripts.build_quant_matrix_version import run_activation
from scripts.check_production_acceptance_manifest import (
    REQUIRED_COLUMNS as ACCEPTANCE_COLUMNS,
)
from scripts.check_production_acceptance_manifest import (
    production_acceptance_manifest_sha256,
)
from xic_extractor.alignment.quant_matrix_version import (
    EXPECTED_DIFF_COLUMNS,
    SOURCE_SUMMARY_COLUMNS,
)
from xic_extractor.diagnostics.quant_matrix.report import (
    QUANT_MATRIX_REVIEW_SCHEMA,
    build_quant_matrix_review_report,
)

ROOT = Path(__file__).resolve().parents[1]


def test_quant_matrix_report_writes_review_rows_summary_and_html(
    tmp_path: Path,
) -> None:
    outputs, manifest_sha = _write_activated_quant_matrix(tmp_path)

    report_outputs = build_quant_matrix_review_report(
        quant_matrix_tsv=outputs["quant_matrix"],
        cell_provenance_tsv=outputs["cell_provenance"],
        row_summary_tsv=outputs["row_summary"],
        source_summary_tsv=outputs["source_summary"],
        output_dir=tmp_path / "report",
    )

    review_rows = _read_tsv(report_outputs["review_rows"])
    assert [(row["sample_stem"], row["cell_status"]) for row in review_rows] == [
        ("SampleA", "detected"),
        ("SampleB", "accepted_backfill"),
    ]
    accepted = review_rows[1]
    assert accepted["report_authority"] == "review_only"
    assert accepted["prevalence_review_state"] == "report_only_risk"
    assert accepted["prevalence_flags"] == "low_seed_support;high_backfill_dependency"
    assert accepted["trace_basis"] == (
        "gaussian_smoothed_trace_primary_raw_trace_auxiliary"
    )
    assert accepted["boundary_status"] == "gaussian_smoothed_boundary_integration"
    assert accepted["manual_negative_closure"] == "hard_stop_not_triggered"
    assert accepted["doublet_closure"] == "allowed"
    assert accepted["doublet_status"] == "no_doublet_claim"
    assert accepted["reference_side"] == "not_applicable"
    assert accepted["doublet_allowed"] == "TRUE"
    assert accepted["source_artifact_relpath"] == "sources/cell_evidence.tsv"
    assert accepted["doublet_source_relpath"] == "sources/doublet.tsv"
    assert accepted["manifest_sha256"] == manifest_sha

    summary = json.loads(report_outputs["summary_json"].read_text(encoding="utf-8"))
    assert summary["schema_version"] == QUANT_MATRIX_REVIEW_SCHEMA
    assert summary["validation_label"] == "shadow_review"
    assert summary["accepted_backfill_count"] == 1
    assert summary["detected_count"] == 1
    assert summary["report_only_risk_count"] == 1
    assert summary["input_artifacts"]["source_summary_tsv"].endswith(
        "source_summary.tsv"
    )

    html = report_outputs["html"].read_text(encoding="utf-8")
    assert "QuantMatrixVersion Review Report" in html
    assert "accepted_backfill" in html
    assert "detected" in html
    assert "low_seed_support" in html
    assert "high_backfill_dependency" in html
    assert "gaussian smoothed trace is primary" in html
    assert "raw trace is auxiliary" in html
    assert "manual_negative hard stop not triggered" in html
    assert "no_doublet_claim" in html
    assert "sources/cell_evidence.tsv" in html
    assert manifest_sha in html
    assert "does not mutate quant matrix, ProductWriter, workbook, GUI" in html


def test_quant_matrix_report_fails_closed_when_backfill_manifest_row_missing(
    tmp_path: Path,
) -> None:
    outputs, _manifest_sha = _write_activated_quant_matrix(tmp_path)
    manifest_path = tmp_path / "production_acceptance.tsv"
    _write_tsv(manifest_path, ACCEPTANCE_COLUMNS, [])
    _refresh_manifest_hash_in_source_summary(outputs["source_summary"], manifest_path)

    with pytest.raises(ValueError, match="manifest row missing for accepted cell"):
        build_quant_matrix_review_report(
            quant_matrix_tsv=outputs["quant_matrix"],
            cell_provenance_tsv=outputs["cell_provenance"],
            row_summary_tsv=outputs["row_summary"],
            source_summary_tsv=outputs["source_summary"],
            output_dir=tmp_path / "report",
        )


def test_quant_matrix_report_rejects_stale_manifest_join(tmp_path: Path) -> None:
    outputs, _manifest_sha = _write_activated_quant_matrix(tmp_path)
    manifest_path = tmp_path / "production_acceptance.tsv"
    rows = _read_tsv(manifest_path)
    rows[0]["manifest_sha256"] = "0" * 64
    _write_tsv(manifest_path, ACCEPTANCE_COLUMNS, rows)
    _refresh_manifest_hash_in_source_summary(outputs["source_summary"], manifest_path)

    with pytest.raises(ValueError, match="manifest_sha256 mismatch"):
        build_quant_matrix_review_report(
            quant_matrix_tsv=outputs["quant_matrix"],
            cell_provenance_tsv=outputs["cell_provenance"],
            row_summary_tsv=outputs["row_summary"],
            source_summary_tsv=outputs["source_summary"],
            output_dir=tmp_path / "report",
        )


def test_quant_matrix_report_rejects_manifest_file_hash_drift(
    tmp_path: Path,
) -> None:
    outputs, _manifest_sha = _write_activated_quant_matrix(tmp_path)
    manifest_path = tmp_path / "production_acceptance.tsv"
    rows = _read_tsv(manifest_path)
    rows[0]["decision_reason"] = "tampered_after_activation"
    _write_tsv(manifest_path, ACCEPTANCE_COLUMNS, rows)

    with pytest.raises(ValueError, match="production_acceptance_manifest_sha256"):
        build_quant_matrix_review_report(
            quant_matrix_tsv=outputs["quant_matrix"],
            cell_provenance_tsv=outputs["cell_provenance"],
            row_summary_tsv=outputs["row_summary"],
            source_summary_tsv=outputs["source_summary"],
            output_dir=tmp_path / "report",
        )


def test_quant_matrix_report_rejects_non_authoritative_manifest_join(
    tmp_path: Path,
) -> None:
    outputs, _manifest_sha = _write_activated_quant_matrix(tmp_path)
    manifest_path = tmp_path / "production_acceptance.tsv"
    rows = _read_tsv(manifest_path)
    rows[0]["truth_status"] = "manual_negative"
    rows[0]["write_authority"] = "FALSE"
    rows[0]["matrix_write_allowed"] = "FALSE"
    rows[0]["shadow_only"] = "TRUE"
    _write_tsv(manifest_path, ACCEPTANCE_COLUMNS, rows)
    _refresh_manifest_hash_in_source_summary(outputs["source_summary"], manifest_path)

    with pytest.raises(ValueError, match="manifest row is not authoritative"):
        build_quant_matrix_review_report(
            quant_matrix_tsv=outputs["quant_matrix"],
            cell_provenance_tsv=outputs["cell_provenance"],
            row_summary_tsv=outputs["row_summary"],
            source_summary_tsv=outputs["source_summary"],
            output_dir=tmp_path / "report",
        )


def test_quant_matrix_report_rejects_source_row_hash_mismatch(
    tmp_path: Path,
) -> None:
    outputs, _manifest_sha = _write_activated_quant_matrix(tmp_path)
    manifest_path = tmp_path / "production_acceptance.tsv"
    rows = _read_tsv(manifest_path)
    rows[0]["source_row_sha256"] = "B" * 64
    _write_tsv(manifest_path, ACCEPTANCE_COLUMNS, rows)
    _refresh_manifest_hash_in_source_summary(outputs["source_summary"], manifest_path)

    with pytest.raises(ValueError, match="source_row_sha256 mismatch"):
        build_quant_matrix_review_report(
            quant_matrix_tsv=outputs["quant_matrix"],
            cell_provenance_tsv=outputs["cell_provenance"],
            row_summary_tsv=outputs["row_summary"],
            source_summary_tsv=outputs["source_summary"],
            output_dir=tmp_path / "report",
        )


def test_quant_matrix_report_escapes_html_and_keeps_tsv_literal_values(
    tmp_path: Path,
) -> None:
    outputs, _manifest_sha = _write_activated_quant_matrix(
        tmp_path,
        sample_a="<b>SampleA</b>",
        sample_b="<script>SampleB</script>",
        family_id="<i>FAM001</i>",
    )

    report_outputs = build_quant_matrix_review_report(
        quant_matrix_tsv=outputs["quant_matrix"],
        cell_provenance_tsv=outputs["cell_provenance"],
        row_summary_tsv=outputs["row_summary"],
        source_summary_tsv=outputs["source_summary"],
        output_dir=tmp_path / "report",
    )

    review_rows = _read_tsv(report_outputs["review_rows"])
    assert review_rows[0]["sample_stem"] == "<b>SampleA</b>"
    assert review_rows[1]["sample_stem"] == "<script>SampleB</script>"

    html = report_outputs["html"].read_text(encoding="utf-8")
    assert "<script>SampleB</script>" not in html
    assert "<b>SampleA</b>" not in html
    assert "&lt;script&gt;SampleB&lt;/script&gt;" in html
    assert "&lt;b&gt;SampleA&lt;/b&gt;" in html
    assert "&lt;i&gt;FAM001&lt;/i&gt;" in html


def test_quant_matrix_report_script_entrypoint_works(tmp_path: Path) -> None:
    outputs, _manifest_sha = _write_activated_quant_matrix(tmp_path)
    script = Path("scripts/build_quant_matrix_version_report.py")
    output_dir = tmp_path / "script_report"

    completed = subprocess.run(
        [
            sys.executable,
            str(script),
            "--quant-matrix-tsv",
            str(outputs["quant_matrix"]),
            "--cell-provenance-tsv",
            str(outputs["cell_provenance"]),
            "--row-summary-tsv",
            str(outputs["row_summary"]),
            "--source-summary-tsv",
            str(outputs["source_summary"]),
            "--output-dir",
            str(output_dir),
        ],
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 0, completed.stderr
    assert "html:" in completed.stdout
    assert (output_dir / "quant_matrix_review_report.html").is_file()


def _write_activated_quant_matrix(
    root: Path,
    *,
    sample_a: str = "SampleA",
    sample_b: str = "SampleB",
    family_id: str = "FAM001",
) -> tuple[dict[str, Path], str]:
    matrix = root / "alignment_matrix.tsv"
    _write_tsv(
        matrix,
        ("Mz", "RT", sample_a, sample_b),
        [{"Mz": "101.1", "RT": "5.5", sample_a: "100", sample_b: ""}],
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
                "source_feature_family_ids": family_id,
            }
        ],
    )
    source = _write_source(root, "sources/cell_evidence.tsv", "cell\tPH001\n")
    doublet = _write_source(root, "sources/doublet.tsv", "doublet\tPH001\n")
    manifest = root / "production_acceptance.tsv"
    manifest_rows = [_acceptance_row(source, doublet, sample_b, family_id)]
    manifest_sha = production_acceptance_manifest_sha256(manifest_rows)
    for row in manifest_rows:
        row["manifest_sha256"] = manifest_sha
    _write_tsv(manifest, ACCEPTANCE_COLUMNS, manifest_rows)
    expected_diff = root / "expected_diff.tsv"
    _write_tsv(
        expected_diff,
        EXPECTED_DIFF_COLUMNS,
        [
            {
                "schema_version": "quant_matrix_version_expected_diff",
                "peak_hypothesis_id": "PH001",
                "sample_stem": sample_b,
                "baseline_value": "",
                "activated_value": "222.2",
                "expected_matrix_effect": "write_accepted_backfill",
                "expected_reason": "review_report_fixture",
            }
        ],
    )
    outputs = run_activation(
        input_quant_matrix_tsv=matrix,
        input_matrix_identity_tsv=identity,
        production_acceptance_manifest_tsv=manifest,
        expected_diff_tsv=expected_diff,
        output_dir=root / "activated",
    )
    return dict(outputs), manifest_sha


def _acceptance_row(
    source: Path,
    doublet: Path,
    sample_stem: str,
    family_id: str,
) -> dict[str, str]:
    return {
        "schema_version": "production_acceptance_manifest",
        "peak_hypothesis_id": "PH001",
        "sample_stem": sample_stem,
        "feature_family_id": family_id,
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
        "prevalence_flags": "low_seed_support;high_backfill_dependency",
        "hard_blocker_rule_ids": "",
        "triggered_risk_rule_ids": "low_seed_support;high_backfill_dependency",
        "closure_rule_ids": "",
        "decision_reason": "review_report_fixture",
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


def _refresh_manifest_hash_in_source_summary(
    source_summary: Path,
    manifest: Path,
) -> None:
    rows = _read_tsv(source_summary)
    rows[0]["production_acceptance_manifest_sha256"] = _sha256(manifest)
    _write_tsv(source_summary, SOURCE_SUMMARY_COLUMNS, rows)
