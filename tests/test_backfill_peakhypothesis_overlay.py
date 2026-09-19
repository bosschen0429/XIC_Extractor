import csv
import json
from pathlib import Path

import numpy as np

from xic_extractor.diagnostics import (
    backfill_peakhypothesis_overlay as overlay,
)


def test_builds_and_renders_full_cohort_overlay_gallery(tmp_path: Path) -> None:
    requests = overlay.build_overlay_requests(
        review_queue_rows=[
            _review_queue_row(
                review_item_id="HYPREV0001",
                sample="Sample_A",
                matched_family="FAM_MATCH",
                winner_family="FAM_WIN",
                anchor_mz="289.116",
                anchor_rt="13.0485",
                peak_start_rt="13.00",
                peak_end_rt="13.12",
            ),
        ],
        full_cohort_review_rows=[
            _alignment_review_row("FAM_MATCH", mz="289.116", rt="13.0485"),
            _alignment_review_row("FAM_WIN", mz="289.115", rt="13.328"),
        ],
        discovery_batch_rows=[
            {
                "sample_stem": "Sample_A",
                "raw_file": "raw-data/Sample_A.raw",
            },
        ],
        rt_padding_min=0.5,
        ppm_tolerance=20.0,
    )

    assert len(requests) == 1
    request = requests[0]
    assert request.review_item_id == "HYPREV0001"
    assert request.raw_file == Path("raw-data/Sample_A.raw")
    assert request.candidate_mz == 289.116
    assert request.candidate_anchor_rt == 13.0485
    assert request.winner_mz == 289.115
    assert request.winner_rt == 13.328
    assert request.rt_min == 12.5
    assert request.rt_max == 13.828

    outputs = overlay.write_full_cohort_overlay_outputs(
        tmp_path,
        requests,
        trace_provider=_fake_trace_provider,
        source_run_id="unit",
    )

    rows = _read_tsv(outputs.index_tsv)
    assert rows[0]["review_item_id"] == "HYPREV0001"
    assert rows[0]["source_peak_hypothesis_id"] == "FAM_SRC"
    assert rows[0]["candidate_point_count"] == "80"
    assert rows[0]["winner_point_count"] == "80"
    assert rows[0]["smooth_method"] == "gaussian15_asls_residual"
    assert rows[0]["smooth_window_points"] == "15"
    assert rows[0]["machine_shape_decision"] == "standard_peak_shape_supported"
    assert rows[0]["machine_same_peak_verdict"] == "same_peak_supported"
    assert rows[0]["machine_same_peak_blockers"] == ""
    assert (
        rows[0]["machine_shape_reasons"]
        == "gaussian15_selected_shape_context_single_complete_unimodal_peak"
    )
    assert rows[0]["gaussian15_selected_segment_peak_count"] == "1"
    assert float(rows[0]["gaussian15_lobe_area"]) > 0.0
    assert rows[0]["gaussian15_lobe_area_source"] == "gaussian15_positive_asls_residual"
    assert rows[0]["gaussian15_lobe_boundary_source"] == "baseline_return"
    assert rows[0]["png_path"].endswith(".png")
    assert rows[0]["pdf_path"].endswith(".pdf")
    assert (tmp_path / rows[0]["png_path"]).is_file()
    assert (tmp_path / rows[0]["pdf_path"]).is_file()
    assert outputs.gallery_html.is_file()

    summary = json.loads(outputs.summary_json.read_text(encoding="utf-8"))
    assert summary["schema_version"] == overlay.SCHEMA_VERSION
    assert summary["overlay_count"] == 1
    assert summary["smooth_method"] == "gaussian15_asls_residual"
    assert summary["smooth_window_points"] == 15
    assert summary["product_behavior_changed"] is False


def test_machine_shape_uses_local_context_when_peak_bounds_are_too_narrow(
    tmp_path: Path,
) -> None:
    requests = overlay.build_overlay_requests(
        review_queue_rows=[
            _review_queue_row(
                review_item_id="HYPREV_NARROW",
                sample="Sample_A",
                matched_family="FAM_MATCH",
                winner_family="FAM_WIN",
                anchor_mz="289.116",
                anchor_rt="13.0485",
                peak_start_rt="13.073",
                peak_end_rt="13.095",
            ),
        ],
        full_cohort_review_rows=[
            _alignment_review_row("FAM_MATCH", mz="289.116", rt="13.0485"),
            _alignment_review_row("FAM_WIN", mz="289.115", rt="13.328"),
        ],
        discovery_batch_rows=[
            {
                "sample_stem": "Sample_A",
                "raw_file": "raw-data/Sample_A.raw",
            },
        ],
        rt_padding_min=0.5,
        ppm_tolerance=20.0,
    )

    outputs = overlay.write_full_cohort_overlay_outputs(
        tmp_path,
        requests,
        trace_provider=_fake_trace_provider,
        source_run_id="unit",
    )

    rows = _read_tsv(outputs.index_tsv)
    assert rows[0]["machine_shape_decision"] == "standard_peak_shape_supported"
    assert rows[0]["machine_same_peak_verdict"] == "same_peak_supported"
    assert rows[0]["machine_shape_blockers"] == ""
    assert rows[0]["gaussian15_selected_segment_peak_count"] == "1"
    assert float(rows[0]["gaussian15_lobe_start_rt"]) < 13.073
    assert float(rows[0]["gaussian15_lobe_end_rt"]) > 13.095
    assert float(rows[0]["gaussian15_lobe_area"]) > 0.0


def test_machine_shape_blocks_multiple_peaks_in_local_context(
    tmp_path: Path,
) -> None:
    requests = overlay.build_overlay_requests(
        review_queue_rows=[
            _review_queue_row(
                review_item_id="HYPREV_MULTI",
                sample="Sample_A",
                matched_family="FAM_MATCH",
                winner_family="FAM_WIN",
                anchor_mz="289.116",
                anchor_rt="13.0485",
                peak_start_rt="12.98",
                peak_end_rt="13.48",
            ),
        ],
        full_cohort_review_rows=[
            _alignment_review_row("FAM_MATCH", mz="289.116", rt="13.0485"),
            _alignment_review_row("FAM_WIN", mz="289.115", rt="13.328"),
        ],
        discovery_batch_rows=[
            {
                "sample_stem": "Sample_A",
                "raw_file": "raw-data/Sample_A.raw",
            },
        ],
        rt_padding_min=0.5,
        ppm_tolerance=20.0,
    )

    outputs = overlay.write_full_cohort_overlay_outputs(
        tmp_path,
        requests,
        trace_provider=_two_peak_trace_provider,
        source_run_id="unit",
    )

    rows = _read_tsv(outputs.index_tsv)
    assert rows[0]["machine_shape_decision"] == "nonstandard_peak_shape"
    assert rows[0]["machine_same_peak_verdict"] == "same_peak_not_supported"
    assert (
        rows[0]["machine_same_peak_blockers"]
        == "machine_standard_peak_shape_not_supported;"
        "machine_gaussian15_lobe_area_not_positive"
    )
    assert (
        rows[0]["machine_shape_blockers"]
        == "selected_shape_context_multiple_gaussian15_peaks"
    )
    assert rows[0]["gaussian15_selected_segment_peak_count"] == "2"
    assert rows[0]["gaussian15_lobe_area"] == ""


def _fake_trace_provider(
    _raw_file: Path,
    mz: float,
    rt_min: float,
    rt_max: float,
    _ppm_tolerance: float,
) -> tuple[np.ndarray, np.ndarray]:
    rt = np.linspace(rt_min, rt_max, 80)
    center = 13.05 if mz > 289.1155 else 13.33
    intensity = np.exp(-((rt - center) ** 2) / (2 * 0.035**2)) * 1000
    return rt, intensity


def _two_peak_trace_provider(
    _raw_file: Path,
    _mz: float,
    rt_min: float,
    rt_max: float,
    _ppm_tolerance: float,
) -> tuple[np.ndarray, np.ndarray]:
    rt = np.linspace(rt_min, rt_max, 120)
    intensity = (
        np.exp(-((rt - 13.05) ** 2) / (2 * 0.035**2)) * 1000
        + np.exp(-((rt - 13.38) ** 2) / (2 * 0.035**2)) * 700
    )
    return rt, intensity


def _review_queue_row(
    *,
    review_item_id: str,
    sample: str,
    matched_family: str,
    winner_family: str,
    anchor_mz: str,
    anchor_rt: str,
    peak_start_rt: str,
    peak_end_rt: str,
) -> dict[str, str]:
    return {
        "schema_version": "backfill_peakhypothesis_hypothesis_review",
        "review_item_id": review_item_id,
        "source_feature_family_id": "FAM_SRC",
        "source_peak_hypothesis_id": "FAM_SRC",
        "sample_stem": sample,
        "full_cohort_anchor_mz": anchor_mz,
        "full_cohort_anchor_rt": anchor_rt,
        "full_cohort_matched_feature_family_id": matched_family,
        "full_cohort_matched_peak_hypothesis_id": matched_family,
        "full_cohort_consolidation_winner_group_hypothesis_id": winner_family,
        "full_cohort_peak_start_rt": peak_start_rt,
        "full_cohort_peak_end_rt": peak_end_rt,
        "full_cohort_cell_status": "rescued",
        "full_cohort_primary_matrix_area": "48462.2",
        "full_cohort_include_in_primary_matrix": "FALSE",
        "full_cohort_consolidation_state": "primary_loser",
        "review_focus": "non_primary_candidate_needs_consolidation_policy",
    }


def _alignment_review_row(family_id: str, *, mz: str, rt: str) -> dict[str, str]:
    return {
        "feature_family_id": family_id,
        "group_hypothesis_id": family_id,
        "family_center_mz": mz,
        "family_center_rt": rt,
    }


def _read_tsv(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle, delimiter="\t"))
