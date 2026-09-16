import csv
import os
import subprocess
import sys
from pathlib import Path

import pytest

from tools.diagnostics import area_integration_uncertainty_audit as report


def test_path_style_cli_help_preserves_public_script_contract() -> None:
    repo_root = Path(__file__).resolve().parents[1]
    script = (
        repo_root / "tools" / "diagnostics" / "area_integration_uncertainty_audit.py"
    )

    result = subprocess.run(
        [sys.executable, str(script), "--help"],
        cwd=repo_root,
        env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert "--evidence-spine-rows-tsv" in result.stdout
    assert "--alignment-integration-audit-tsv" in result.stdout
    assert "--output-dir" in result.stdout


def test_module_style_cli_help_preserves_public_module_contract() -> None:
    repo_root = Path(__file__).resolve().parents[1]

    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "tools.diagnostics.area_integration_uncertainty_audit",
            "--help",
        ],
        cwd=repo_root,
        env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert "--evidence-spine-rows-tsv" in result.stdout
    assert "--alignment-integration-audit-tsv" in result.stdout
    assert "--output-dir" in result.stdout


def test_area_integration_uncertainty_audit_classifies_all_buckets(
    tmp_path: Path,
) -> None:
    evidence_path = tmp_path / "evidence_spine_consistency_rows.tsv"
    candidates_path = tmp_path / "peak_candidates.tsv"
    boundaries_path = tmp_path / "peak_candidate_boundaries.tsv"
    alignment_path = tmp_path / "alignment_cell_integration_audit.tsv"
    cases = (
        ("missing", "", 1.0, "current_supported", "current_supported"),
        ("incomplete", "FAM_INCOMPLETE", 1.1, "current_supported", "current_supported"),
        ("consistent", "FAM_CONSISTENT", 1.1, "current_supported", "current_supported"),
        ("label", "FAM_LABEL", 1.1, "current_supported", "merge_suggested"),
        ("baseline", "FAM_BASELINE", 2.0, "current_supported", "current_supported"),
        ("boundary", "FAM_BOUNDARY", 2.0, "current_supported", "current_supported"),
        ("uncertain", "FAM_UNCERTAIN", 2.0, "current_supported", "current_supported"),
        (
            "unexplained",
            "FAM_UNEXPLAINED",
            2.0,
            "current_supported",
            "current_supported",
        ),
    )
    _write_tsv(
        evidence_path,
        [
            _evidence_row(
                target,
                family_id=family,
                raw_ratio=raw_ratio,
                targeted_region=targeted_region,
                untargeted_region=untargeted_region,
                boundary_start_delta=0.2 if target == "boundary" else 0.0,
            )
            for target, family, raw_ratio, targeted_region, untargeted_region in cases
        ],
        fields=EVIDENCE_FIELDS,
    )
    _write_tsv(
        candidates_path,
        [
            _candidate_row(target)
            for target, _family, _ratio, _targeted_region, _untargeted_region in cases
        ],
        fields=CANDIDATE_FIELDS,
    )
    _write_tsv(
        boundaries_path,
        [
            _boundary_row(
                target,
                area_ratio="1.5" if target == "boundary" else "1.0",
            )
            for target, _family, _ratio, _targeted_region, _untargeted_region in cases
        ],
        fields=BOUNDARY_FIELDS,
    )
    _write_tsv(
        alignment_path,
        [
            _alignment_row(
                "FAM_CONSISTENT",
                area="110",
                baseline_area="88",
                uncertainty_fraction="0.05",
                baseline_fraction="0.8",
            ),
            _alignment_row(
                "FAM_LABEL",
                area="110",
                baseline_area="88",
                uncertainty_fraction="0.05",
                baseline_fraction="0.8",
            ),
            _alignment_row(
                "FAM_BASELINE",
                area="200",
                baseline_area="88",
                uncertainty_fraction="0.05",
                baseline_fraction="0.44",
            ),
            _alignment_row(
                "FAM_BOUNDARY",
                area="200",
                baseline_area="160",
                uncertainty_fraction="0.05",
                baseline_fraction="0.8",
            ),
            _alignment_row(
                "FAM_UNCERTAIN",
                area="200",
                baseline_area="160",
                uncertainty_fraction="0.30",
                baseline_fraction="0.8",
            ),
            _alignment_row(
                "FAM_UNEXPLAINED",
                area="200",
                baseline_area="160",
                uncertainty_fraction="0.05",
                baseline_fraction="0.8",
            ),
        ],
        fields=ALIGNMENT_FIELDS,
    )

    outputs, result = report.run_area_integration_uncertainty_audit(
        evidence_spine_rows_tsv=evidence_path,
        targeted_peak_candidates_tsv=candidates_path,
        targeted_boundaries_tsv=boundaries_path,
        alignment_integration_audit_tsv=alignment_path,
        output_dir=tmp_path / "out",
    )

    assert outputs.rows_tsv.is_file()
    with outputs.summary_tsv.open(encoding="utf-8", newline="") as handle:
        assert csv.DictReader(handle, delimiter="\t").fieldnames == [
            "rows_checked",
            "bucket_counts",
            "missing_alignment_match_count",
            "integration_context_incomplete_count",
            "unexplained_area_mismatch_count",
        ]
    with outputs.rows_tsv.open(encoding="utf-8", newline="") as handle:
        assert csv.DictReader(handle, delimiter="\t").fieldnames == [
            "sample",
            "target_label",
            "role",
            "targeted_candidate_id",
            "untargeted_family_id",
            "target_mz",
            "untargeted_family_mz",
            "targeted_area",
            "untargeted_area",
            "raw_area_ratio",
            "targeted_baseline_area",
            "untargeted_baseline_area",
            "baseline_area_ratio",
            "baseline_area_method",
            "targeted_uncertainty_fraction",
            "untargeted_uncertainty_fraction",
            "targeted_baseline_fraction",
            "untargeted_baseline_fraction",
            "boundary_delta_start_min",
            "boundary_delta_end_min",
            "boundary_alternative_area_ratio",
            "targeted_region_verdict",
            "untargeted_region_verdict",
            "targeted_local_mixture_verdict",
            "untargeted_local_mixture_verdict",
            "evidence_spine_mismatch_reason",
            "integration_bucket",
            "integration_reason",
        ]
    by_target = {row.target_label: row.integration_bucket for row in result.rows}
    assert by_target == {
        "missing": "missing_alignment_match",
        "incomplete": "integration_context_incomplete",
        "consistent": "area_consistent_low_uncertainty",
        "label": "label_only_mismatch",
        "baseline": "baseline_explains_raw_mismatch",
        "boundary": "boundary_sensitive",
        "uncertain": "high_uncertainty",
        "unexplained": "unexplained_area_mismatch",
    }
    assert result.summary.rows_checked == 8
    assert "unexplained_area_mismatch:1" in result.summary.bucket_counts
    rows = _read_tsv(outputs.rows_tsv)
    assert rows[0]["integration_bucket"] == "missing_alignment_match"
    alignment_rows = _read_tsv(alignment_path)
    assert alignment_rows[0]["area_uncertainty_formula_version"] == (
        "baseline_residual_mad_v1"
    )
    assert alignment_rows[0]["baseline_residual_mad"] == "0.5"
    assert alignment_rows[0]["area_uncertainty_noise_source"] == "asls_residual"


def test_uncertainty_audit_uses_reported_baseline_for_current_schema(
    tmp_path: Path,
) -> None:
    evidence_path = tmp_path / "evidence.tsv"
    candidates_path = tmp_path / "peak_candidates.tsv"
    boundaries_path = tmp_path / "peak_candidate_boundaries.tsv"
    alignment_path = tmp_path / "alignment_cell_integration_audit.tsv"
    _write_tsv(
        evidence_path,
        [
            _evidence_row(
                "baseline",
                family_id="FAM_BASELINE",
                raw_ratio=2.0,
                targeted_region="current_single",
                untargeted_region="current_single",
                boundary_start_delta=0.0,
            )
        ],
        fields=EVIDENCE_FIELDS,
    )
    _write_tsv(
        candidates_path,
        [_candidate_row("baseline")],
        fields=CANDIDATE_FIELDS,
    )
    _write_tsv(
        boundaries_path,
        [_boundary_row("baseline", area_ratio="1.0")],
        fields=BOUNDARY_FIELDS,
    )
    _write_tsv(
        alignment_path,
        [
            _alignment_row(
                "FAM_BASELINE",
                area="200",
                baseline_area="80",
                uncertainty_fraction="0.05",
                baseline_fraction="0.4",
            )
        ],
        fields=ALIGNMENT_FIELDS,
    )

    _outputs, result = report.run_area_integration_uncertainty_audit(
        evidence_spine_rows_tsv=evidence_path,
        targeted_peak_candidates_tsv=candidates_path,
        targeted_boundaries_tsv=boundaries_path,
        alignment_integration_audit_tsv=alignment_path,
        output_dir=tmp_path / "out",
    )

    row = result.rows[0]
    assert row.integration_bucket == "baseline_explains_raw_mismatch"
    assert row.untargeted_baseline_area == pytest.approx(80.0)
    assert row.baseline_area_ratio == pytest.approx(1.0)
    assert row.baseline_area_method == "reported_baseline"


def test_area_integration_uncertainty_audit_fails_on_missing_columns(
    tmp_path: Path,
) -> None:
    evidence_path = tmp_path / "evidence.tsv"
    evidence_path.write_text("sample\ttarget_label\nS1\tT1\n", encoding="utf-8")
    candidates_path = tmp_path / "peak_candidates.tsv"
    boundaries_path = tmp_path / "peak_candidate_boundaries.tsv"
    alignment_path = tmp_path / "alignment_cell_integration_audit.tsv"
    _write_tsv(candidates_path, [], fields=CANDIDATE_FIELDS)
    _write_tsv(boundaries_path, [], fields=BOUNDARY_FIELDS)
    _write_tsv(alignment_path, [], fields=ALIGNMENT_FIELDS)

    with pytest.raises(ValueError, match="missing required columns"):
        report.run_area_integration_uncertainty_audit(
            evidence_spine_rows_tsv=evidence_path,
            targeted_peak_candidates_tsv=candidates_path,
            targeted_boundaries_tsv=boundaries_path,
            alignment_integration_audit_tsv=alignment_path,
            output_dir=tmp_path / "out",
        )


EVIDENCE_FIELDS = (
    "sample",
    "target_label",
    "role",
    "targeted_candidate_id",
    "untargeted_family_id",
    "target_mz",
    "untargeted_family_mz",
    "targeted_area",
    "untargeted_area",
    "area_ratio_untargeted_to_targeted",
    "boundary_delta_start_min",
    "boundary_delta_end_min",
    "targeted_region_verdict",
    "untargeted_region_verdict",
    "targeted_local_mixture_verdict",
    "untargeted_local_mixture_verdict",
    "mismatch_reason",
)

CANDIDATE_FIELDS = (
    "sample_name",
    "target_label",
    "candidate_id",
    "selected",
    "area_raw_counts_seconds",
    "area_baseline_corrected",
    "area_uncertainty",
)

BOUNDARY_FIELDS = (
    "sample_name",
    "target_label",
    "candidate_id",
    "selected_candidate",
    "boundary_audit_top",
    "area_ratio_vs_candidate_interval",
    "is_candidate_interval",
)

ALIGNMENT_FIELDS = (
    "feature_family_id",
    "sample_stem",
    "area",
    "area_baseline_corrected",
    "area_uncertainty",
    "area_uncertainty_formula_version",
    "baseline_residual_mad",
    "area_uncertainty_noise_source",
    "uncertainty_fraction",
    "baseline_fraction",
)


def _evidence_row(
    target: str,
    *,
    family_id: str,
    raw_ratio: float,
    targeted_region: str,
    untargeted_region: str,
    boundary_start_delta: float,
) -> dict[str, object]:
    return {
        "sample": "S1",
        "target_label": target,
        "role": "Target",
        "targeted_candidate_id": f"S1|{target}|0",
        "untargeted_family_id": family_id,
        "target_mz": "289.1",
        "untargeted_family_mz": "289.1" if family_id else "",
        "targeted_area": "100",
        "untargeted_area": f"{100 * raw_ratio:.6g}" if family_id else "",
        "area_ratio_untargeted_to_targeted": f"{raw_ratio:.6g}" if family_id else "",
        "boundary_delta_start_min": f"{boundary_start_delta:.6g}" if family_id else "",
        "boundary_delta_end_min": "0" if family_id else "",
        "targeted_region_verdict": targeted_region,
        "untargeted_region_verdict": untargeted_region if family_id else "",
        "targeted_local_mixture_verdict": "current_single_envelope",
        "untargeted_local_mixture_verdict": "current_single_envelope"
        if family_id
        else "",
        "mismatch_reason": "consistent" if raw_ratio == 1.1 else "area_ratio",
    }


def _candidate_row(target: str) -> dict[str, str]:
    return {
        "sample_name": "S1",
        "target_label": target,
        "candidate_id": f"S1|{target}|0",
        "selected": "TRUE",
        "area_raw_counts_seconds": "100",
        "area_baseline_corrected": "80",
        "area_uncertainty": "5",
    }


def _boundary_row(target: str, *, area_ratio: str) -> dict[str, str]:
    return {
        "sample_name": "S1",
        "target_label": target,
        "candidate_id": f"S1|{target}|0",
        "selected_candidate": "TRUE",
        "boundary_audit_top": "TRUE",
        "area_ratio_vs_candidate_interval": area_ratio,
        "is_candidate_interval": "FALSE",
    }


def _alignment_row(
    family_id: str,
    *,
    area: str,
    baseline_area: str,
    uncertainty_fraction: str,
    baseline_fraction: str,
) -> dict[str, str]:
    return {
        "feature_family_id": family_id,
        "sample_stem": "S1",
        "area": area,
        "area_baseline_corrected": baseline_area,
        "area_uncertainty": "5",
        "area_uncertainty_formula_version": "baseline_residual_mad_v1",
        "baseline_residual_mad": "0.5",
        "area_uncertainty_noise_source": "asls_residual",
        "uncertainty_fraction": uncertainty_fraction,
        "baseline_fraction": baseline_fraction,
    }


def _write_tsv(path: Path, rows, *, fields) -> None:
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, delimiter="\t")
        writer.writeheader()
        writer.writerows(rows)


def _read_tsv(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle, delimiter="\t"))
