import pickle

from xic_extractor.alignment.matrix import AlignedCell
from xic_extractor.peak_detection.hypotheses import IntegrationResult


def test_aligned_cell_selected_integration_is_pickleable_for_process_mode():
    cell = AlignedCell(
        sample_stem="sample-a",
        cluster_id="ALN000001",
        status="detected",
        area=123.4,
        apex_rt=5.1,
        height=99.0,
        peak_start_rt=5.0,
        peak_end_rt=5.2,
        rt_delta_sec=6.0,
        trace_quality="clean",
        scan_support_score=0.75,
        source_candidate_id="sample-a#42",
        reason="detected candidate",
        selected_integration=IntegrationResult(
            rt_left_min=5.0,
            rt_apex_min=5.1,
            rt_right_min=5.2,
            raw_apex_rt_min=5.1,
            rt_width_min=0.2,
            height_raw=99.0,
            height_smoothed=99.0,
            area_raw_counts_seconds=234.5,
            area_baseline_corrected=200.0,
            baseline_type="asls",
            boundary_sources=("test",),
        ),
    )

    restored = pickle.loads(pickle.dumps(cell))

    assert restored.selected_integration == cell.selected_integration
    assert restored.matrix_area is None
    assert restored.matrix_area_source == "missing_ms1_morphology_area"


def test_aligned_cell_matrix_area_source_names_missing_asls():
    cell = AlignedCell(
        sample_stem="sample-a",
        cluster_id="ALN000001",
        status="detected",
        area=123.4,
        apex_rt=5.1,
        height=None,
        peak_start_rt=None,
        peak_end_rt=None,
        rt_delta_sec=6.0,
        trace_quality="legacy-area-only",
        scan_support_score=None,
        source_candidate_id="sample-a#42",
        reason="detected candidate",
    )

    assert cell.matrix_area is None
    assert cell.matrix_area_source == "missing_ms1_morphology_area"
