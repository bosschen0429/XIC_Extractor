from __future__ import annotations

import csv
import json
from pathlib import Path

import pytest

from tools.diagnostics import changed_row_mode_overlay_review as review


def test_mode_overlay_review_marks_removed_no_split_multimodal_family(
    tmp_path: Path,
) -> None:
    overlay_dir = tmp_path / "overlay"
    overlay_dir.mkdir()
    output_dir = tmp_path / "review"
    trace_json = overlay_dir / "fam001_trace_data.json"
    _write_trace_data(
        trace_json,
        family_id="FAM001",
        traces=[
            _trace("S1", cell_apex_rt=10.00, trace_apex_rt=10.02, status="detected"),
            _trace("S2", cell_apex_rt=10.08, trace_apex_rt=10.09),
            _trace("S3", cell_apex_rt=11.00, trace_apex_rt=11.02),
            _trace("S4", cell_apex_rt=11.08, trace_apex_rt=11.10),
        ],
    )
    overlay_summary = tmp_path / "overlay_summary.tsv"
    _write_tsv(
        overlay_summary,
        [
            {
                "rank": "1",
                "feature_family_id": "FAM001",
                "status": "success",
                "family_verdict": "review_required_neighboring_ms1_interference",
                "png_path": str(overlay_dir / "fam001.png"),
                "pdf_path": str(overlay_dir / "fam001.pdf"),
                "trace_data_json": str(trace_json),
            }
        ],
        review.OVERLAY_SUMMARY_REQUIRED_COLUMNS,
    )
    changed_bundle = tmp_path / "changed.tsv"
    _write_tsv(
        changed_bundle,
        [
            {
                "stable_row_id": "FAM001",
                "reason": "single_dr_gate:blocked_missing_backfill_identity_evidence",
                "presence_impact": "primary_row_removed",
                "evidence_tier": "blocked_missing_backfill_identity_evidence",
                "reviewer_verdict": "pending_manual_review",
            }
        ],
        (
            "stable_row_id",
            "reason",
            "presence_impact",
            "evidence_tier",
            "reviewer_verdict",
        ),
    )
    baseline_identity = tmp_path / "baseline_identity.tsv"
    _write_identity(
        baseline_identity,
        [
            {
                "peak_hypothesis_id": "FAM001",
                "row_identity_basis": "no_split_peak_hypothesis",
                "source_feature_family_ids": "FAM001",
            }
        ],
    )
    active_identity = tmp_path / "active_identity.tsv"
    _write_identity(active_identity, [])

    outputs = review.run_changed_row_mode_overlay_review(
        changed_row_bundle_tsv=changed_bundle,
        overlay_batch_summary_tsv=overlay_summary,
        baseline_alignment_matrix_identity_tsv=baseline_identity,
        active_alignment_matrix_identity_tsv=active_identity,
        output_dir=output_dir,
        render_plots=False,
    )

    family_rows = _read_tsv(outputs.family_summary_tsv)
    assert family_rows[0]["active_identity_status"] == "removed_by_active_gate"
    assert family_rows[0]["family_mode_count"] == "2"
    assert family_rows[0]["mode_review_verdict"] == (
        "review_required_raw_multimodal_family"
    )
    assert "baseline_no_split_but_selected_apex_multimodal" in family_rows[0][
        "mode_review_warning"
    ]
    assert "raw_overlay_mode_is_review_only_not_irt" in family_rows[0][
        "mode_review_warning"
    ]

    sample_rows = _read_tsv(outputs.sample_review_tsv)
    by_sample = {row["sample_stem"]: row for row in sample_rows}
    assert by_sample["S1"]["peak_hypothesis_id"].startswith("FAM001::raw_mode_")
    assert by_sample["S1"]["peak_hypothesis_status"] == "raw_mode_review_only"
    assert by_sample["S1"]["active_identity_status"] == "removed_by_active_gate"

    html = outputs.review_gallery_html.read_text(encoding="utf-8")
    assert "Family ID is provenance here" in html
    assert "removed_by_active_gate" in html
    assert "FAM001::raw_mode_" in html
    assert "Mode-level aligned MS1 overlays" in html
    assert "Original family MS1 overlay" in html
    assert "Legacy family overlay context" not in html


def test_mode_overlay_review_keeps_single_mode_as_supported_but_review_only(
    tmp_path: Path,
) -> None:
    overlay_dir = tmp_path / "overlay"
    overlay_dir.mkdir()
    trace_json = overlay_dir / "fam002_trace_data.json"
    _write_trace_data(
        trace_json,
        family_id="FAM002",
        traces=[
            _trace("S1", cell_apex_rt=8.00, trace_apex_rt=8.02, status="detected"),
            _trace("S2", cell_apex_rt=8.08, trace_apex_rt=8.09),
        ],
    )
    overlay_summary = tmp_path / "overlay_summary.tsv"
    _write_tsv(
        overlay_summary,
        [
            {
                "rank": "1",
                "feature_family_id": "FAM002",
                "status": "success",
                "family_verdict": "ms1_shape_supports_family_backfill",
                "png_path": "",
                "pdf_path": "",
                "trace_data_json": str(trace_json),
            }
        ],
        review.OVERLAY_SUMMARY_REQUIRED_COLUMNS,
    )
    changed_bundle = tmp_path / "changed.tsv"
    _write_tsv(
        changed_bundle,
        [{"stable_row_id": "FAM002"}],
        review.CHANGED_ROW_REQUIRED_COLUMNS,
    )

    outputs = review.run_changed_row_mode_overlay_review(
        changed_row_bundle_tsv=changed_bundle,
        overlay_batch_summary_tsv=overlay_summary,
        output_dir=tmp_path / "review",
        render_plots=False,
    )

    family_rows = _read_tsv(outputs.family_summary_tsv)
    assert family_rows[0]["mode_review_verdict"] == (
        "single_mode_supported_raw_review_only"
    )
    assert family_rows[0]["active_identity_status"] == "active_identity_not_supplied"
    assert family_rows[0]["mode_review_warning"] == (
        "raw_overlay_mode_is_review_only_not_irt;alignment_not_supplied_raw_only"
    )
    sample_rows = _read_tsv(outputs.sample_review_tsv)
    assert {row["peak_hypothesis_status"] for row in sample_rows} == {
        "raw_mode_review_only"
    }


def test_mode_overlay_review_splits_clear_subhalf_minute_double_peak(
    tmp_path: Path,
) -> None:
    overlay_dir = tmp_path / "overlay"
    overlay_dir.mkdir()
    trace_json = overlay_dir / "fam_double_trace_data.json"
    _write_trace_data(
        trace_json,
        family_id="FAM_DOUBLE",
        traces=[
            _trace("QC3", cell_apex_rt=8.4299, trace_apex_rt=8.4299),
            _trace("QC5", cell_apex_rt=8.4604, trace_apex_rt=8.4604),
            _trace("TumorA", cell_apex_rt=8.5700, trace_apex_rt=8.5700),
            _trace("BenignA", cell_apex_rt=9.0015, trace_apex_rt=9.0015),
            _trace("BenignB", cell_apex_rt=9.0256, trace_apex_rt=9.0256),
            _trace("NormalA", cell_apex_rt=9.0289, trace_apex_rt=9.0289),
            _trace("NormalB", cell_apex_rt=9.0417, trace_apex_rt=9.0417),
            _trace("TumorB", cell_apex_rt=9.0466, trace_apex_rt=9.0466),
        ],
    )
    overlay_summary = tmp_path / "overlay_summary.tsv"
    _write_tsv(
        overlay_summary,
        [
            {
                "rank": "1",
                "feature_family_id": "FAM_DOUBLE",
                "status": "success",
                "family_verdict": "ms1_shape_supports_family_backfill",
                "png_path": "",
                "pdf_path": "",
                "trace_data_json": str(trace_json),
            }
        ],
        review.OVERLAY_SUMMARY_REQUIRED_COLUMNS,
    )
    changed_bundle = tmp_path / "changed.tsv"
    _write_tsv(
        changed_bundle,
        [{"stable_row_id": "FAM_DOUBLE"}],
        review.CHANGED_ROW_REQUIRED_COLUMNS,
    )

    outputs = review.run_changed_row_mode_overlay_review(
        changed_row_bundle_tsv=changed_bundle,
        overlay_batch_summary_tsv=overlay_summary,
        output_dir=tmp_path / "review",
        render_plots=False,
    )

    family_rows = _read_tsv(outputs.family_summary_tsv)
    assert family_rows[0]["family_mode_count"] == "2"
    assert family_rows[0]["mode_review_verdict"] == (
        "review_required_raw_multimodal_family"
    )
    sample_rows = _read_tsv(outputs.sample_review_tsv)
    modes = {row["selected_mode_id"] for row in sample_rows}
    assert len(modes) == 2


def test_mode_overlay_review_uses_alignment_delta_when_raw_modes_split(
    tmp_path: Path,
) -> None:
    overlay_dir = tmp_path / "overlay"
    overlay_dir.mkdir()
    trace_json = overlay_dir / "fam_aligned_trace_data.json"
    _write_trace_data(
        trace_json,
        family_id="FAM_ALIGNED",
        traces=[
            _trace("QC3", cell_apex_rt=8.4299, trace_apex_rt=8.4299),
            _trace("QC5", cell_apex_rt=8.4604, trace_apex_rt=8.4604),
            _trace("TumorA", cell_apex_rt=8.5700, trace_apex_rt=8.5700),
            _trace("BenignA", cell_apex_rt=9.0015, trace_apex_rt=9.0015),
            _trace("BenignB", cell_apex_rt=9.0256, trace_apex_rt=9.0256),
            _trace("NormalA", cell_apex_rt=9.0289, trace_apex_rt=9.0289),
            _trace("NormalB", cell_apex_rt=9.0417, trace_apex_rt=9.0417),
            _trace("TumorB", cell_apex_rt=9.0466, trace_apex_rt=9.0466),
        ],
    )
    overlay_summary = tmp_path / "overlay_summary.tsv"
    _write_tsv(
        overlay_summary,
        [
            {
                "rank": "1",
                "feature_family_id": "FAM_ALIGNED",
                "status": "success",
                "family_verdict": "ms1_shape_supports_family_backfill",
                "png_path": "",
                "pdf_path": "",
                "trace_data_json": str(trace_json),
            }
        ],
        review.OVERLAY_SUMMARY_REQUIRED_COLUMNS,
    )
    changed_bundle = tmp_path / "changed.tsv"
    _write_tsv(
        changed_bundle,
        [{"stable_row_id": "FAM_ALIGNED"}],
        review.CHANGED_ROW_REQUIRED_COLUMNS,
    )
    alignment_cells = tmp_path / "alignment_cells.tsv"
    _write_tsv(
        alignment_cells,
        [
            _alignment_cell("QC3", 8.4299, -23.7),
            _alignment_cell("QC5", 8.4604, -21.9),
            _alignment_cell("TumorA", 8.5700, -15.3),
            _alignment_cell("BenignA", 9.0015, 10.6),
            _alignment_cell("BenignB", 9.0256, 12.0),
            _alignment_cell("NormalA", 9.0289, 12.2),
            _alignment_cell("NormalB", 9.0417, 13.0),
            _alignment_cell("TumorB", 9.0466, 13.3),
        ],
        review.ALIGNMENT_CELL_REQUIRED_COLUMNS,
    )

    outputs = review.run_changed_row_mode_overlay_review(
        changed_row_bundle_tsv=changed_bundle,
        overlay_batch_summary_tsv=overlay_summary,
        alignment_cells_tsv=alignment_cells,
        output_dir=tmp_path / "review",
        render_plots=False,
    )

    family_rows = _read_tsv(outputs.family_summary_tsv)
    assert family_rows[0]["family_mode_count"] == "2"
    assert family_rows[0]["alignment_mode_counts"].startswith("alignment_mode_1:")
    assert family_rows[0]["mode_review_basis"] == "alignment_cell_delta"
    assert family_rows[0]["mode_review_verdict"] == (
        "single_mode_supported_alignment_review_only"
    )
    assert "raw_multimodal_but_alignment_single" in family_rows[0][
        "mode_review_warning"
    ]

    sample_rows = _read_tsv(outputs.sample_review_tsv)
    assert {row["selected_mode_id"] for row in sample_rows} != {
        row["display_mode_id"] for row in sample_rows
    }
    assert {row["display_mode_id"] for row in sample_rows} == {"alignment_mode_1"}
    assert {row["mode_review_basis"] for row in sample_rows} == {
        "alignment_cell_delta"
    }


def test_mode_overlay_review_flags_gaussian15_trace_multipeak_when_alignment_single(
    tmp_path: Path,
) -> None:
    overlay_dir = tmp_path / "overlay"
    overlay_dir.mkdir()
    trace_json = overlay_dir / "fam_gaussian_multi_trace_data.json"
    profile = [
        0.0,
        0.0,
        0.0,
        2.0,
        8.0,
        30.0,
        75.0,
        100.0,
        75.0,
        30.0,
        8.0,
        2.0,
        0.0,
        0.0,
        5.0,
        20.0,
        60.0,
        85.0,
        60.0,
        20.0,
        5.0,
        0.0,
        0.0,
        0.0,
        0.0,
        0.0,
        0.0,
        0.0,
        0.0,
    ]
    _write_trace_data(
        trace_json,
        family_id="FAM_GAUSSIAN_MULTI",
        traces=[
            _trace_with_profile(
                "DetectedA",
                cell_apex_rt=8.3,
                trace_apex_rt=8.3,
                intensities=profile,
                status="detected",
            ),
            _trace_with_profile(
                "DetectedB",
                cell_apex_rt=8.3,
                trace_apex_rt=8.3,
                intensities=profile,
                status="detected",
            ),
            _trace_with_profile(
                "RescueA",
                cell_apex_rt=8.3,
                trace_apex_rt=8.3,
                intensities=profile,
            ),
        ],
    )
    overlay_summary = tmp_path / "overlay_summary.tsv"
    _write_tsv(
        overlay_summary,
        [
            {
                "rank": "1",
                "feature_family_id": "FAM_GAUSSIAN_MULTI",
                "status": "success",
                "family_verdict": "review_required_neighboring_ms1_interference",
                "png_path": "",
                "pdf_path": "",
                "trace_data_json": str(trace_json),
            }
        ],
        review.OVERLAY_SUMMARY_REQUIRED_COLUMNS,
    )
    changed_bundle = tmp_path / "changed.tsv"
    _write_tsv(
        changed_bundle,
        [{"stable_row_id": "FAM_GAUSSIAN_MULTI"}],
        review.CHANGED_ROW_REQUIRED_COLUMNS,
    )
    alignment_cells = tmp_path / "alignment_cells.tsv"
    _write_tsv(
        alignment_cells,
        [
            {
                "feature_family_id": "FAM_GAUSSIAN_MULTI",
                "sample_stem": sample,
                "apex_rt": "8.3",
                "rt_delta_sec": "0",
            }
            for sample in ("DetectedA", "DetectedB", "RescueA")
        ],
        review.ALIGNMENT_CELL_REQUIRED_COLUMNS,
    )

    outputs = review.run_changed_row_mode_overlay_review(
        changed_row_bundle_tsv=changed_bundle,
        overlay_batch_summary_tsv=overlay_summary,
        alignment_cells_tsv=alignment_cells,
        output_dir=tmp_path / "review",
        render_plots=False,
    )

    family_rows = _read_tsv(outputs.family_summary_tsv)
    assert family_rows[0]["mode_review_verdict"] == (
        "review_required_gaussian15_trace_multipeak"
    )
    assert "gaussian15_trace_multipeak" in family_rows[0]["mode_review_warning"]
    sample_rows = _read_tsv(outputs.sample_review_tsv)
    assert {row["display_mode_id"] for row in sample_rows} == {"alignment_mode_1"}


def test_similarity_panel_combines_gaussian_shape_drift_and_apex_badges(
    tmp_path: Path,
) -> None:
    overlay_dir = tmp_path / "overlay"
    overlay_dir.mkdir()
    trace_json = overlay_dir / "fam003_trace_data.json"
    _write_trace_data(
        trace_json,
        family_id="FAM003",
        traces=[
            _trace_with_profile(
                "S1",
                cell_apex_rt=10.00,
                intensities=[0, 5, 25, 75, 100, 75, 25, 5, 0],
                trace_apex_rt=10.00,
                status="detected",
            ),
            _trace_with_profile(
                "S2",
                cell_apex_rt=10.03,
                intensities=[0, 4, 24, 70, 95, 72, 24, 4, 0],
                trace_apex_rt=10.03,
            ),
            _trace_with_profile(
                "S3",
                cell_apex_rt=10.08,
                intensities=[80, 5, 0, 5, 20, 85, 10, 5, 0],
                trace_apex_rt=10.42,
            ),
        ],
    )
    overlay_summary = tmp_path / "overlay_summary.tsv"
    _write_tsv(
        overlay_summary,
        [
            {
                "rank": "1",
                "feature_family_id": "FAM003",
                "status": "success",
                "family_verdict": "review_required_neighboring_ms1_interference",
                "png_path": "",
                "pdf_path": "",
                "trace_data_json": str(trace_json),
            }
        ],
        review.OVERLAY_SUMMARY_REQUIRED_COLUMNS,
    )
    changed_bundle = tmp_path / "changed.tsv"
    _write_tsv(
        changed_bundle,
        [{"stable_row_id": "FAM003"}],
        review.CHANGED_ROW_REQUIRED_COLUMNS,
    )
    drift_tsv = tmp_path / "matrix_rt_drift.tsv"
    _write_tsv(
        drift_tsv,
        [
            _drift_row("S1", status="rt_close", corrected_delta="2"),
            _drift_row("S2", status="drift_supported", corrected_delta="4"),
            _drift_row("S3", status="drift_not_supported", corrected_delta="38"),
        ],
        review.MATRIX_RT_DRIFT_POLICY_REQUIRED_COLUMNS,
    )
    ms1_tsv = tmp_path / "ms1_pattern.tsv"
    _write_tsv(
        ms1_tsv,
        [
            _ms1_row("S1", shape="0.95", interference="0.05"),
            _ms1_row("S2", shape="0.93", interference="0.10"),
            _ms1_row("S3", shape="0.20", interference="0.90"),
        ],
        review.MS1_PATTERN_COHERENCE_REQUIRED_COLUMNS,
    )

    outputs = review.run_changed_row_mode_overlay_review(
        changed_row_bundle_tsv=changed_bundle,
        overlay_batch_summary_tsv=overlay_summary,
        matrix_rt_drift_policy_tsv=drift_tsv,
        ms1_pattern_coherence_tsv=ms1_tsv,
        output_dir=tmp_path / "review",
        render_plots=False,
    )

    rows = _read_tsv(outputs.similarity_review_tsv)
    by_sample = {row["sample_stem"]: row for row in rows}
    assert by_sample["S1"]["quick_review_badge"] == "shape_coherent_review_only"
    assert float(by_sample["S1"]["gaussian15_shape_similarity_to_mode"]) > 0.80
    assert by_sample["S1"]["matrix_rt_drift_status"] == "rt_close"

    assert by_sample["S3"]["quick_review_badge"] == "review_required_wrong_apex_risk"
    assert by_sample["S3"]["global_apex_status"] == "conflict"
    assert by_sample["S3"]["matrix_rt_drift_status"] == "drift_not_supported"
    assert by_sample["S3"]["ms1_pattern_status"] == "conflict"

    family_rows = _read_tsv(outputs.similarity_family_summary_tsv)
    assert "review_required_wrong_apex_risk:1" in family_rows[0][
        "quick_review_badge_counts"
    ]
    html = outputs.review_gallery_html.read_text(encoding="utf-8")
    assert "Similarity TSV" in html
    assert "global median quick score" in html
    assert "median shape similarity" in html
    assert "median quick score" in html
    assert "Mode-level aligned MS1 overlays" in html
    assert "Original family MS1 overlay" in html
    assert "Sample evidence table" in html
    assert "shape similarity" in html
    assert "quick score" in html
    assert "class=\"badge risk-low\">shape_coherent_review_only" in html
    assert "shape_coherent_review_only" in html


def test_mode_overlay_plot_intensity_uses_gaussian15_smoothing() -> None:
    values = [0.0, 0.0, 100.0, 0.0, 0.0]

    smoothed = review._smoothed_plot_intensity(values)

    assert len(smoothed) == len(values)
    assert smoothed != values
    assert max(smoothed) < 100.0
    assert sum(smoothed) > 0.0


_MULTIPEAK_PROFILE = [
    0.0, 0.0, 0.0, 2.0, 8.0, 30.0, 75.0, 100.0, 75.0, 30.0, 8.0, 2.0, 0.0, 0.0,
    5.0, 20.0, 60.0, 85.0, 60.0, 20.0, 5.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0,
    0.0,
]
_SHOULDER_PROFILE = [
    0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0,
    2.0, 8.0, 30.0, 70.0, 100.0, 70.0, 30.0, 8.0, 2.0,
    0.0, 0.0,
    4.0, 9.0, 13.0, 14.0, 13.0, 9.0, 4.0,
    0.0, 0.0, 0.0, 0.0,
]


class _DriftLookup:
    def __init__(self, deltas: dict[str, float]) -> None:
        self._deltas = deltas
        self.source = "targeted_istd_trend"

    def sample_delta_min(self, sample_stem: str) -> float | None:
        return self._deltas.get(sample_stem)

    def injection_order(self, sample_stem: str) -> int | None:
        return None


def _multipeak_family(tmp_path: Path) -> tuple[Path, Path]:
    overlay_dir = tmp_path / "overlay"
    overlay_dir.mkdir()
    trace_json = overlay_dir / "fam_drift_trace_data.json"
    _write_trace_data(
        trace_json,
        family_id="FAM_DRIFT",
        traces=[
            _trace_with_profile(
                "DetectedA",
                cell_apex_rt=8.3,
                trace_apex_rt=8.3,
                intensities=_MULTIPEAK_PROFILE,
                status="detected",
            ),
            _trace_with_profile(
                "DetectedB",
                cell_apex_rt=8.3,
                trace_apex_rt=8.3,
                intensities=_MULTIPEAK_PROFILE,
                status="detected",
            ),
            _trace_with_profile(
                "RescueA",
                cell_apex_rt=8.3,
                trace_apex_rt=8.3,
                intensities=_MULTIPEAK_PROFILE,
            ),
        ],
    )
    overlay_summary = tmp_path / "overlay_summary.tsv"
    _write_tsv(
        overlay_summary,
        [
            {
                "rank": "1",
                "feature_family_id": "FAM_DRIFT",
                "status": "success",
                "family_verdict": "review_required_neighboring_ms1_interference",
                "png_path": "",
                "pdf_path": "",
                "trace_data_json": str(trace_json),
            }
        ],
        review.OVERLAY_SUMMARY_REQUIRED_COLUMNS,
    )
    changed_bundle = tmp_path / "changed.tsv"
    _write_tsv(
        changed_bundle,
        [{"stable_row_id": "FAM_DRIFT"}],
        review.CHANGED_ROW_REQUIRED_COLUMNS,
    )
    return overlay_summary, changed_bundle


def test_mode_overlay_review_drift_badge_and_columns_in_gallery(
    tmp_path: Path,
) -> None:
    overlay_summary, changed_bundle = _multipeak_family(tmp_path)
    lookup = _DriftLookup(
        deltas={"DetectedA": 0.10, "DetectedB": 0.12, "RescueA": 0.08},
    )

    outputs = review.run_changed_row_mode_overlay_review(
        changed_row_bundle_tsv=changed_bundle,
        overlay_batch_summary_tsv=overlay_summary,
        drift_lookup=lookup,
        output_dir=tmp_path / "review",
        render_plots=False,
    )

    family_rows = _read_tsv(outputs.family_summary_tsv)
    assert "likely_false_split" in family_rows[0]["drift_diagnostic_badge"]
    assert family_rows[0]["raw_mode_span_min"] != ""
    assert family_rows[0]["corrected_mode_span_min"] != ""

    sample_rows = _read_tsv(outputs.sample_review_tsv)
    by_sample = {row["sample_stem"]: row for row in sample_rows}
    assert by_sample["DetectedA"]["sample_drift_shift_sec"] != ""
    assert by_sample["DetectedA"]["corrected_apex_rt"] != ""

    html = outputs.review_gallery_html.read_text(encoding="utf-8")
    assert "likely_false_split" in html
    assert "drift shift (s)" in html
    assert "raw apex" in html
    assert "corrected apex" in html
    assert "drift diagnostic distribution" in html


def test_mode_overlay_review_renders_drift_aware_mode_plot(tmp_path: Path) -> None:
    overlay_summary, changed_bundle = _multipeak_family(tmp_path)
    lookup = _DriftLookup(
        deltas={"DetectedA": 0.10, "DetectedB": 0.12, "RescueA": 0.08},
    )

    outputs = review.run_changed_row_mode_overlay_review(
        changed_row_bundle_tsv=changed_bundle,
        overlay_batch_summary_tsv=overlay_summary,
        drift_lookup=lookup,
        output_dir=tmp_path / "review",
        render_plots=True,
    )

    family_rows = _read_tsv(outputs.family_summary_tsv)
    mode_plot = Path(family_rows[0]["mode_plot_png_path"])
    aligned_plot = Path(family_rows[0]["mode_aligned_plot_png_path"])
    assert mode_plot.is_file()
    assert aligned_plot.is_file()


def test_sample_review_rows_reuses_family_alignment_mode_count(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    trace_data = [
        review.TraceData(
            family_id="FAM_COUNT",
            path=tmp_path / "fam_count_trace_data.json",
            overlay_row={
                "rank": "1",
                "family_verdict": "review_required_neighboring_ms1_interference",
            },
            payload={},
            traces=tuple(
                _trace(
                    f"S{index}",
                    cell_apex_rt=8.0 + index * 0.01,
                    trace_apex_rt=8.0 + index * 0.01,
                    status="detected",
                )
                for index in range(4)
            ),
        )
    ]
    alignment_modes = {
        f"S{index}": {
            "alignment_mode_id": "alignment_mode_1",
            "alignment_mode_status": "alignment_cell_supported",
            "alignment_mode_source": "alignment_cell_delta",
        }
        for index in range(4)
    }
    original_count = review._alignment_mode_count
    calls = 0

    def counting_alignment_mode_count(
        rows: dict[str, dict[str, str]],
    ) -> int:
        nonlocal calls
        calls += 1
        return original_count(rows)

    monkeypatch.setattr(
        review,
        "_alignment_mode_count",
        counting_alignment_mode_count,
    )

    rows = review._sample_review_rows(
        trace_data=trace_data,
        rt_rows_by_key={},
        hypothesis_rows_by_key={},
        baseline_identity={},
        active_identity=None,
        global_mode_ids_by_family={
            "FAM_COUNT": {f"S{index}": "raw_mode_1" for index in range(4)}
        },
        alignment_modes_by_family={"FAM_COUNT": alignment_modes},
        gaussian15_modes_by_family={},
        output_dir=tmp_path,
    )

    assert len(rows) == 4
    assert calls == 1
    assert {row["mode_review_basis"] for row in rows} == {"alignment_cell_delta"}


def test_mode_trace_entries_by_mode_preserves_trace_order_and_membership(
    tmp_path: Path,
) -> None:
    trace_data = review.TraceData(
        family_id="FAM_MODE_BUCKET",
        path=tmp_path / "fam_mode_bucket_trace_data.json",
        overlay_row={},
        payload={},
        traces=(
            _trace("S3", cell_apex_rt=8.3, trace_apex_rt=8.3),
            _trace("S1", cell_apex_rt=8.1, trace_apex_rt=8.1),
            _trace("S2", cell_apex_rt=8.2, trace_apex_rt=8.2),
            _trace("not-in-sample-rows", cell_apex_rt=8.4, trace_apex_rt=8.4),
        ),
    )
    sample_rows = [
        {
            "sample_stem": "S1",
            "display_mode_id": "display-a",
            "gaussian15_trace_mode_ids": "mode-a;mode-b",
        },
        {
            "sample_stem": "S2",
            "display_mode_id": "display-b",
            "gaussian15_trace_mode_ids": "mode-b",
        },
        {
            "sample_stem": "S3",
            "display_mode_id": "display-a",
            "gaussian15_trace_mode_ids": "mode-a",
        },
    ]
    gaussian_modes = (
        review.ms1_peak_modes.Gaussian15PeakModeWindow(
            mode_id="mode-a",
            start_rt=7.8,
            end_rt=8.4,
            apex_rt=8.1,
            trace_peak_count=2,
            detected_seed_count=1,
        ),
    )

    entries = review._mode_trace_entries_by_mode(
        trace_data=trace_data,
        sample_rows=sample_rows,
        gaussian15_modes=gaussian_modes,
    )
    fallback_entries = review._mode_trace_entries_by_mode(
        trace_data=trace_data,
        sample_rows=sample_rows,
        gaussian15_modes=(),
    )

    assert [
        trace["sample_stem"] for trace, _row in entries["mode-a"]
    ] == ["S3", "S1"]
    assert [
        trace["sample_stem"] for trace, _row in entries["mode-b"]
    ] == ["S1", "S2"]
    assert "not-in-sample-rows" not in {
        trace["sample_stem"]
        for mode_entries in entries.values()
        for trace, _row in mode_entries
    }
    assert [
        trace["sample_stem"] for trace, _row in fallback_entries["display-a"]
    ] == ["S3", "S1"]


def test_gallery_has_triage_bar_lazy_images_and_meter_levels(tmp_path: Path) -> None:
    overlay_summary, changed_bundle = _multipeak_family(tmp_path)
    lookup = _DriftLookup(
        deltas={"DetectedA": 0.10, "DetectedB": 0.12, "RescueA": 0.08},
    )

    outputs = review.run_changed_row_mode_overlay_review(
        changed_row_bundle_tsv=changed_bundle,
        overlay_batch_summary_tsv=overlay_summary,
        drift_lookup=lookup,
        output_dir=tmp_path / "review",
        render_plots=True,
    )

    html = outputs.review_gallery_html.read_text(encoding="utf-8")
    # sticky triage filter bar + client-side filter script
    assert 'class="triage"' in html
    assert 'data-filter="all"' in html
    assert "querySelector('.triage')" in html
    # every family card carries its diagnostic category for filtering
    assert "data-diag=" in html
    # rendered plots are lazy-loaded
    assert 'loading="lazy"' in html
    # threshold-coloured meter (rendered, not just the CSS rule)
    assert "meter meter-" in html


def test_mode_overlay_review_flags_subthreshold_missed_peak(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    overlay_dir = tmp_path / "overlay"
    overlay_dir.mkdir()
    trace_json = overlay_dir / "fam_shoulder_trace_data.json"
    _write_trace_data(
        trace_json,
        family_id="FAM_SHOULDER",
        traces=[
            _trace_with_profile(
                "S1",
                cell_apex_rt=8.3,
                trace_apex_rt=8.3,
                intensities=_SHOULDER_PROFILE,
                status="detected",
            ),
            _trace_with_profile(
                "S2",
                cell_apex_rt=8.3,
                trace_apex_rt=8.3,
                intensities=_SHOULDER_PROFILE,
                status="detected",
            ),
        ],
    )
    overlay_summary = tmp_path / "overlay_summary.tsv"
    _write_tsv(
        overlay_summary,
        [
            {
                "rank": "1",
                "feature_family_id": "FAM_SHOULDER",
                "status": "success",
                "family_verdict": "ms1_shape_supports_family_backfill",
                "png_path": "",
                "pdf_path": "",
                "trace_data_json": str(trace_json),
            }
        ],
        review.OVERLAY_SUMMARY_REQUIRED_COLUMNS,
    )
    changed_bundle = tmp_path / "changed.tsv"
    _write_tsv(
        changed_bundle,
        [{"stable_row_id": "FAM_SHOULDER"}],
        review.CHANGED_ROW_REQUIRED_COLUMNS,
    )

    original_report = review.subthreshold_candidate_report
    report_calls = 0

    def counting_report(
        trace_row,
        *,
        window_points=review.DEFAULT_GAUSSIAN15_WINDOW_POINTS,
    ):
        nonlocal report_calls
        report_calls += 1
        return original_report(trace_row, window_points=window_points)

    monkeypatch.setattr(review, "subthreshold_candidate_report", counting_report)

    outputs = review.run_changed_row_mode_overlay_review(
        changed_row_bundle_tsv=changed_bundle,
        overlay_batch_summary_tsv=overlay_summary,
        output_dir=tmp_path / "review",
        render_plots=True,
    )

    family_rows = _read_tsv(outputs.family_summary_tsv)
    assert family_rows[0]["subthreshold_present"] == "TRUE"
    assert int(family_rows[0]["subthreshold_candidate_count"]) >= 1
    assert report_calls == 2

    html = outputs.review_gallery_html.read_text(encoding="utf-8")
    assert "sub_threshold_candidates_present" in html


def _trace(
    sample: str,
    *,
    cell_apex_rt: float,
    trace_apex_rt: float,
    status: str = "backfilled",
) -> dict[str, object]:
    return {
        "sample_stem": sample,
        "status": status,
        "group": "detected_seed" if status == "detected" else "top_rescued_ms1_area",
        "cell_area": 1000.0,
        "cell_height": 100.0,
        "cell_apex_rt": cell_apex_rt,
        "trace_apex_rt": trace_apex_rt,
        "global_trace_apex_delta_min": trace_apex_rt - cell_apex_rt,
        "apex_aligned_shape_similarity": 0.9,
        "rt": [cell_apex_rt - 0.05, cell_apex_rt, cell_apex_rt + 0.05],
        "intensity": [10.0, 100.0, 12.0],
    }


def _trace_with_profile(
    sample: str,
    *,
    cell_apex_rt: float,
    intensities: list[float],
    trace_apex_rt: float,
    status: str = "backfilled",
) -> dict[str, object]:
    center_index = len(intensities) // 2
    rt = [
        cell_apex_rt + (index - center_index) * 0.04
        for index in range(len(intensities))
    ]
    return {
        "sample_stem": sample,
        "status": status,
        "group": "detected_seed" if status == "detected" else "top_rescued_ms1_area",
        "cell_area": float(sum(intensities)),
        "cell_height": float(max(intensities)),
        "cell_apex_rt": cell_apex_rt,
        "trace_apex_rt": trace_apex_rt,
        "global_trace_apex_delta_min": trace_apex_rt - cell_apex_rt,
        "apex_aligned_shape_similarity": 0.0,
        "rt": rt,
        "intensity": [float(value) for value in intensities],
    }


def _drift_row(
    sample: str,
    *,
    status: str,
    corrected_delta: str,
) -> dict[str, str]:
    return {
        "feature_family_id": "FAM003",
        "sample_stem": sample,
        "matrix_rt_drift_status": status,
        "drift_evidence_level": "sample_istd_aligned",
        "raw_rt_delta_sec": corrected_delta,
        "drift_corrected_delta_sec": corrected_delta,
        "matrix_shift_sec": "0",
        "drift_reference_count": "3",
        "drift_reference_source": "unit_test",
        "drift_compatible_status": (
            "compatible" if status != "drift_not_supported" else "conflict"
        ),
        "reason": "unit_test",
        "diagnostic_only": "TRUE",
    }


def _alignment_cell(sample: str, apex_rt: float, rt_delta_sec: float) -> dict[str, str]:
    return {
        "feature_family_id": "FAM_ALIGNED",
        "sample_stem": sample,
        "apex_rt": str(apex_rt),
        "rt_delta_sec": str(rt_delta_sec),
    }


def _ms1_row(
    sample: str,
    *,
    shape: str,
    interference: str,
) -> dict[str, str]:
    status = "supportive" if float(shape) >= 0.5 else "conflict"
    return {
        "feature_family_id": "FAM003",
        "sample_stem": sample,
        "ms1_pattern_status": status,
        "ms1_pattern_evidence_level": "trace_constellation",
        "apex_coherence_sec": "1",
        "boundary_overlap_score": "0.9",
        "shape_correlation_score": shape,
        "relative_pattern_stability_score": "0.8",
        "local_interference_score": interference,
        "constellation_peak_count": "1",
        "reference_peak_count": "2",
        "drift_compatible_status": "compatible",
        "reason": "unit_test",
        "diagnostic_only": "TRUE",
    }


def _write_trace_data(
    path: Path,
    *,
    family_id: str,
    traces: list[dict[str, object]],
) -> None:
    path.write_text(
        json.dumps(
            {
                "family_id": family_id,
                "mz": 250.0,
                "ppm": 10.0,
                "rt_min": 7.0,
                "rt_max": 12.0,
                "family_center_rt": 8.0,
                "traces": traces,
            }
        ),
        encoding="utf-8",
    )


def _write_identity(path: Path, rows: list[dict[str, str]]) -> None:
    _write_tsv(
        path,
        rows,
        (
            "identity_schema_version",
            "matrix_row_index",
            "Mz",
            "RT",
            "peak_hypothesis_id",
            "row_identity_basis",
            "split_evaluation_status",
            "projection_status",
            "source_feature_family_ids",
        ),
    )


def _write_tsv(
    path: Path,
    rows: list[dict[str, object]],
    fieldnames: tuple[str, ...],
) -> None:
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, delimiter="\t")
        writer.writeheader()
        for row in rows:
            writer.writerow({field: row.get(field, "") for field in fieldnames})


def _read_tsv(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle, delimiter="\t"))
