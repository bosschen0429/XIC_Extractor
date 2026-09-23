import csv
import os
import subprocess
import sys
from pathlib import Path

import pytest

from tools.diagnostics import evidence_spine_consistency as report
from tools.diagnostics import evidence_spine_consistency_analysis as analysis
from tools.diagnostics.evidence_spine_consistency_models import AlignmentCell


def test_path_style_cli_help_preserves_public_script_contract() -> None:
    repo_root = Path(__file__).resolve().parents[1]
    script = repo_root / "tools" / "diagnostics" / "evidence_spine_consistency.py"

    result = subprocess.run(
        [sys.executable, str(script), "--help"],
        cwd=repo_root,
        env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert "--targeted-dir" in result.stdout
    assert "--alignment-dir" in result.stdout
    assert "--output-dir" in result.stdout


def test_module_style_cli_help_preserves_public_module_contract() -> None:
    repo_root = Path(__file__).resolve().parents[1]

    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "tools.diagnostics.evidence_spine_consistency",
            "--help",
        ],
        cwd=repo_root,
        env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert "--targeted-dir" in result.stdout
    assert "--alignment-dir" in result.stdout
    assert "--output-dir" in result.stdout


def test_evidence_spine_consistency_matches_shared_semantics(tmp_path: Path) -> None:
    targeted_dir = tmp_path / "targeted"
    alignment_dir = tmp_path / "alignment"
    targeted_dir.mkdir()
    alignment_dir.mkdir()
    _write_tsv(
        targeted_dir / "peak_candidates.tsv",
        [
            {
                "sample_name": "sample-a",
                "target_label": "15N5-8-oxodG",
                "role": "ISTD",
                "candidate_id": "sample-a|15N5-8-oxodG|0",
                "selected": "TRUE",
                "rt_apex_min": "25.0",
                "rt_left_min": "24.9",
                "rt_right_min": "25.1",
                "area_raw_counts_seconds": "1000",
                "area_baseline_corrected": "950",
                "region_scan_count": "5",
            },
        ],
        fields=(
            "sample_name",
            "target_label",
            "role",
            "candidate_id",
            "selected",
            "rt_apex_min",
            "rt_left_min",
            "rt_right_min",
            "area_raw_counts_seconds",
            "area_baseline_corrected",
            "region_scan_count",
        ),
    )
    _write_tsv(
        targeted_dir / "peak_candidate_boundaries.tsv",
        [
            {
                "sample_name": "sample-a",
                "target_label": "15N5-8-oxodG",
                "candidate_id": "sample-a|15N5-8-oxodG|0",
                "target_mz": "289.0841",
            },
        ],
        fields=("sample_name", "target_label", "candidate_id", "target_mz"),
    )
    _write_tsv(
        targeted_dir / "peak_region_selection_shadow_summary.tsv",
        [
            {
                "sample_name": "sample-a",
                "target_label": "15N5-8-oxodG",
                "shadow_verdict": "current_supported",
                "local_mixture_diagnostic": "current_single_envelope",
            },
        ],
        fields=(
            "sample_name",
            "target_label",
            "shadow_verdict",
            "local_mixture_diagnostic",
        ),
    )
    _write_tsv(
        alignment_dir / "alignment_cells.tsv",
        [
            {
                "sample_stem": "sample-a",
                "feature_family_id": "FAM000001",
                "status": "detected",
                "area": "1100",
                "apex_rt": "25.02",
                "peak_start_rt": "24.91",
                "peak_end_rt": "25.09",
                "family_center_mz": "289.0842",
                "region_shadow_verdict": "current_supported",
                "region_local_mixture_diagnostic": "current_single_envelope",
            },
        ],
        fields=(
            "sample_stem",
            "feature_family_id",
            "status",
            "area",
            "apex_rt",
            "peak_start_rt",
            "peak_end_rt",
            "family_center_mz",
            "region_shadow_verdict",
            "region_local_mixture_diagnostic",
        ),
    )

    outputs, result = report.run_evidence_spine_consistency(
        targeted_dir=targeted_dir,
        alignment_dir=alignment_dir,
        output_dir=tmp_path / "out",
    )

    assert outputs.rows_tsv.is_file()
    with outputs.summary_tsv.open(encoding="utf-8", newline="") as handle:
        assert csv.DictReader(handle, delimiter="\t").fieldnames == [
            "rows_checked",
            "matched_rows",
            "consistent_rows",
            "mismatch_rows",
            "missing_alignment_rows",
            "focused_target_labels",
            "included_istd_rows",
            "mismatch_reason_counts",
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
            "mz_delta_ppm",
            "trace_scan_count",
            "rt_window_min",
            "targeted_selected_rt",
            "untargeted_selected_rt",
            "rt_delta_min",
            "targeted_boundary_start",
            "targeted_boundary_end",
            "untargeted_boundary_start",
            "untargeted_boundary_end",
            "boundary_delta_start_min",
            "boundary_delta_end_min",
            "targeted_area",
            "untargeted_area",
            "area_ratio_untargeted_to_targeted",
            "baseline_corrected_area_available",
            "targeted_region_verdict",
            "untargeted_region_verdict",
            "targeted_local_mixture_verdict",
            "untargeted_local_mixture_verdict",
            "mismatch_reason",
        ]
    assert result.summary.rows_checked == 1
    assert result.summary.consistent_rows == 1
    row = result.rows[0]
    assert row.untargeted_family_id == "FAM000001"
    assert row.mismatch_reason == "consistent"
    assert row.baseline_corrected_area_available is True


def test_evidence_spine_consistency_reports_missing_alignment(tmp_path: Path) -> None:
    targeted_dir = tmp_path / "targeted"
    alignment_dir = tmp_path / "alignment"
    targeted_dir.mkdir()
    alignment_dir.mkdir()
    _write_minimal_targeted(targeted_dir, mz="289.0841")
    _write_tsv(
        alignment_dir / "alignment_cells.tsv",
        [
            {
                "sample_stem": "sample-a",
                "feature_family_id": "FAM000002",
                "status": "detected",
                "area": "1100",
                "apex_rt": "28.0",
                "peak_start_rt": "27.9",
                "peak_end_rt": "28.1",
                "family_center_mz": "300.0",
                "region_shadow_verdict": "current_supported",
                "region_local_mixture_diagnostic": "current_single_envelope",
            },
        ],
        fields=(
            "sample_stem",
            "feature_family_id",
            "status",
            "area",
            "apex_rt",
            "peak_start_rt",
            "peak_end_rt",
            "family_center_mz",
            "region_shadow_verdict",
            "region_local_mixture_diagnostic",
        ),
    )

    _outputs, result = report.run_evidence_spine_consistency(
        targeted_dir=targeted_dir,
        alignment_dir=alignment_dir,
        output_dir=tmp_path / "out",
    )

    assert result.summary.missing_alignment_rows == 1
    assert "no_alignment_mz_rt_match" in result.rows[0].mismatch_reason


def test_evidence_spine_consistency_prefers_primary_consolidation_match(
    tmp_path: Path,
) -> None:
    targeted_dir = tmp_path / "targeted"
    alignment_dir = tmp_path / "alignment"
    targeted_dir.mkdir()
    alignment_dir.mkdir()
    _write_minimal_targeted(targeted_dir, mz="289.0841")
    _write_tsv(
        alignment_dir / "alignment_cells.tsv",
        [
            {
                "sample_stem": "sample-a",
                "feature_family_id": "FAM000001",
                "status": "detected",
                "area": "1100",
                "apex_rt": "25.0",
                "peak_start_rt": "24.9",
                "peak_end_rt": "25.1",
                "family_center_mz": "289.0841",
                "region_shadow_verdict": "current_supported",
                "region_local_mixture_diagnostic": "current_single_envelope",
                "reason": "sample-local MS1 owner with original MS2 evidence",
            },
            {
                "sample_stem": "sample-a",
                "feature_family_id": "FAM000002",
                "status": "detected",
                "area": "1100",
                "apex_rt": "25.0",
                "peak_start_rt": "24.9",
                "peak_end_rt": "25.1",
                "family_center_mz": "289.0841",
                "region_shadow_verdict": "current_supported",
                "region_local_mixture_diagnostic": "current_single_envelope",
                "reason": "primary family consolidation; source_family=FAM000001",
            },
        ],
        fields=(
            "sample_stem",
            "feature_family_id",
            "status",
            "area",
            "apex_rt",
            "peak_start_rt",
            "peak_end_rt",
            "family_center_mz",
            "region_shadow_verdict",
            "region_local_mixture_diagnostic",
            "reason",
        ),
    )

    _outputs, result = report.run_evidence_spine_consistency(
        targeted_dir=targeted_dir,
        alignment_dir=alignment_dir,
        output_dir=tmp_path / "out",
    )

    assert result.rows[0].untargeted_family_id == "FAM000002"


def test_evidence_spine_consistency_prefers_primary_match_over_audit_loser_distance(
    tmp_path: Path,
) -> None:
    targeted_dir = tmp_path / "targeted"
    alignment_dir = tmp_path / "alignment"
    targeted_dir.mkdir()
    alignment_dir.mkdir()
    _write_minimal_targeted(targeted_dir, mz="289.0841")
    _write_tsv(
        alignment_dir / "alignment_cells.tsv",
        [
            {
                "sample_stem": "sample-a",
                "feature_family_id": "FAM000001",
                "status": "detected",
                "area": "10",
                "apex_rt": "25.74",
                "peak_start_rt": "25.70",
                "peak_end_rt": "25.78",
                "family_center_mz": "289.0841",
                "region_shadow_verdict": "",
                "region_local_mixture_diagnostic": "",
                "reason": "sample-local MS1 owner with original MS2 evidence",
            },
            {
                "sample_stem": "sample-a",
                "feature_family_id": "FAM000002",
                "status": "backfilled",
                "area": "1100",
                "apex_rt": "25.0",
                "peak_start_rt": "24.9",
                "peak_end_rt": "25.1",
                "family_center_mz": "289.0850",
                "region_shadow_verdict": "",
                "region_local_mixture_diagnostic": "",
                "reason": "primary family consolidation; source_family=FAM000001",
            },
        ],
        fields=(
            "sample_stem",
            "feature_family_id",
            "status",
            "area",
            "apex_rt",
            "peak_start_rt",
            "peak_end_rt",
            "family_center_mz",
            "region_shadow_verdict",
            "region_local_mixture_diagnostic",
            "reason",
        ),
    )

    _outputs, result = report.run_evidence_spine_consistency(
        targeted_dir=targeted_dir,
        alignment_dir=alignment_dir,
        output_dir=tmp_path / "out",
    )

    assert result.rows[0].untargeted_family_id == "FAM000002"
    assert result.rows[0].mismatch_reason == "consistent"


def test_evidence_spine_rt_index_limits_candidate_cells_by_sample_and_window() -> None:
    cells = (
        _alignment_cell("sample-a", "FAR_LOW", 23.9),
        _alignment_cell("sample-a", "LEFT_EDGE", 24.25),
        _alignment_cell("sample-a", "CENTER", 25.0),
        _alignment_cell("sample-a", "RIGHT_EDGE", 25.75),
        _alignment_cell("sample-a", "FAR_HIGH", 25.8),
        _alignment_cell("sample-b", "OTHER_SAMPLE", 25.0),
        _alignment_cell("sample-a", "ABSENT", 25.0, status="absent"),
        _alignment_cell("sample-a", "NO_MZ", 25.0, mz=None),
    )

    index = analysis._sample_rt_cell_index(cells)

    window_cells = analysis._candidate_cells_for_rt_window(
        index,
        sample="sample-a",
        rt=25.0,
        match_rt_min=0.75,
    )

    assert [cell.family_id for cell in window_cells] == [
        "LEFT_EDGE",
        "CENTER",
        "RIGHT_EDGE",
    ]
    assert (
        analysis._candidate_cells_for_rt_window(
            index,
            sample="sample-a",
            rt=None,
            match_rt_min=0.75,
        )
        == ()
    )


def test_evidence_spine_consistency_fails_on_missing_columns(tmp_path: Path) -> None:
    targeted_dir = tmp_path / "targeted"
    alignment_dir = tmp_path / "alignment"
    targeted_dir.mkdir()
    alignment_dir.mkdir()
    (targeted_dir / "peak_candidates.tsv").write_text(
        "sample_name\ttarget_label\nsample-a\t15N5-8-oxodG\n",
        encoding="utf-8",
    )
    _write_tsv(
        alignment_dir / "alignment_cells.tsv",
        [],
        fields=(
            "sample_stem",
            "feature_family_id",
            "status",
            "area",
            "apex_rt",
            "peak_start_rt",
            "peak_end_rt",
            "family_center_mz",
            "region_shadow_verdict",
            "region_local_mixture_diagnostic",
        ),
    )

    with pytest.raises(ValueError, match="missing required columns"):
        report.run_evidence_spine_consistency(
            targeted_dir=targeted_dir,
            alignment_dir=alignment_dir,
            output_dir=tmp_path / "out",
        )


def _alignment_cell(
    sample: str,
    family_id: str,
    rt: float,
    *,
    status: str = "detected",
    mz: float | None = 289.0841,
) -> AlignmentCell:
    return AlignmentCell(
        sample=sample,
        family_id=family_id,
        status=status,
        mz=mz,
        rt=rt,
        area=1000.0,
        left=rt - 0.1,
        right=rt + 0.1,
        region_verdict="current_supported",
        local_mixture_diagnostic="current_single_envelope",
    )


def _write_minimal_targeted(targeted_dir: Path, *, mz: str) -> None:
    _write_tsv(
        targeted_dir / "peak_candidates.tsv",
        [
            {
                "sample_name": "sample-a",
                "target_label": "15N5-8-oxodG",
                "role": "ISTD",
                "candidate_id": "sample-a|15N5-8-oxodG|0",
                "selected": "TRUE",
                "rt_apex_min": "25.0",
                "rt_left_min": "24.9",
                "rt_right_min": "25.1",
                "area_raw_counts_seconds": "1000",
                "area_baseline_corrected": "",
                "region_scan_count": "5",
            },
        ],
        fields=(
            "sample_name",
            "target_label",
            "role",
            "candidate_id",
            "selected",
            "rt_apex_min",
            "rt_left_min",
            "rt_right_min",
            "area_raw_counts_seconds",
            "area_baseline_corrected",
            "region_scan_count",
        ),
    )
    _write_tsv(
        targeted_dir / "peak_candidate_boundaries.tsv",
        [
            {
                "sample_name": "sample-a",
                "target_label": "15N5-8-oxodG",
                "candidate_id": "sample-a|15N5-8-oxodG|0",
                "target_mz": mz,
            },
        ],
        fields=("sample_name", "target_label", "candidate_id", "target_mz"),
    )
    _write_tsv(
        targeted_dir / "peak_region_selection_shadow_summary.tsv",
        [],
        fields=(
            "sample_name",
            "target_label",
            "shadow_verdict",
            "local_mixture_diagnostic",
        ),
    )


def _write_tsv(
    path: Path,
    rows: list[dict[str, str]],
    *,
    fields: tuple[str, ...],
) -> None:
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=fields,
            delimiter="\t",
            lineterminator="\n",
        )
        writer.writeheader()
        writer.writerows(rows)
