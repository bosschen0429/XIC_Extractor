import csv
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from tools.diagnostics import family_ms1_overlay_evidence as evidence
from tools.diagnostics import family_ms1_overlay_plot as report
from tools.diagnostics.family_ms1_overlay_models import TraceOverlayRow
from xic_extractor.alignment.edge_scoring import DriftPriorSource


def test_path_style_cli_help_preserves_public_script_contract() -> None:
    repo_root = Path(__file__).resolve().parents[1]
    script = repo_root / "tools" / "diagnostics" / "family_ms1_overlay_plot.py"

    result = subprocess.run(
        [sys.executable, str(script), "--help"],
        cwd=repo_root,
        env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert "--alignment-cells" in result.stdout
    assert "--family-id" in result.stdout


def test_load_family_cells_and_assign_highlight_groups(tmp_path: Path) -> None:
    cells_path = tmp_path / "alignment_cells.tsv"
    _write_tsv(
        cells_path,
        [
            _cell_row("FAM001", "detected-a", "detected", "1000"),
            _cell_row("FAM001", "rescued-high", "backfilled", "900"),
            _cell_row("FAM001", "rescued-low", "backfilled", "100"),
            _cell_row("FAM001", "pooled_QC1", "backfilled", "50"),
            _cell_row("FAM002", "other", "detected", "999"),
        ],
    )

    cells = report.load_family_cells(cells_path, "FAM001")
    groups = report.assign_highlight_groups(cells)

    assert [cell.sample_stem for cell in cells] == [
        "detected-a",
        "rescued-high",
        "rescued-low",
        "pooled_QC1",
    ]
    assert groups["detected-a"] == "detected_seed"
    assert groups["rescued-high"] == "top_rescued_ms1_area"
    assert groups["pooled_QC1"] == "pooled_qc"


def test_assign_highlight_groups_respects_rescued_highlight_limit() -> None:
    cells = [
        report.FamilyCell(
            sample_stem=f"sample-{index}",
            status="backfilled",
            area=float(100 - index),
            height=10,
            apex_rt=1.1,
            peak_start_rt=1.0,
            peak_end_rt=1.2,
            region_shadow_verdict="current_supported",
            source_candidate_id="",
        )
        for index in range(3)
    ]

    groups = report.assign_highlight_groups(cells, max_highlight_rescued=1)

    assert groups["sample-0"] == "top_rescued_ms1_area"
    assert groups["sample-1"] == "rescued_other"
    assert groups["sample-2"] == "rescued_other"


def test_write_family_ms1_overlay_outputs_from_synthetic_traces(
    tmp_path: Path,
) -> None:
    rows = [
        report.trace_row_from_arrays(
            report.FamilyCell(
                sample_stem="detected-a",
                status="detected",
                area=1000,
                height=100,
                apex_rt=1.1,
                peak_start_rt=1.0,
                peak_end_rt=1.2,
                region_shadow_verdict="current_supported",
                source_candidate_id="detected-a#1",
            ),
            "detected_seed",
            [1.0, 1.1, 1.2],
            [0.0, 100.0, 20.0],
        ),
        report.trace_row_from_arrays(
            report.FamilyCell(
                sample_stem="rescued-a",
                status="backfilled",
                area=800,
                height=80,
                apex_rt=1.11,
                peak_start_rt=1.01,
                peak_end_rt=1.21,
                region_shadow_verdict="split_supported",
                source_candidate_id="",
            ),
            "top_rescued_ms1_area",
            [1.0, 1.1, 1.2],
            [0.0, 80.0, 10.0],
        ),
    ]

    outputs = report.write_family_ms1_overlay_outputs(
        rows=rows,
        output_dir=tmp_path / "out",
        output_prefix="fam001_ms1_overlay",
        family_id="FAM001",
        mz=251.165,
        ppm=10.0,
        rt_min=1.0,
        rt_max=1.2,
        family_center_rt=1.1,
    )

    assert outputs.png_path.is_file()
    assert outputs.pdf_path is not None
    assert outputs.pdf_path.is_file()
    assert (outputs.png_path.parent / "fam001_ms1_overlay_hypothesis.png").is_file()
    assert (outputs.png_path.parent / "fam001_ms1_overlay_hypothesis.pdf").is_file()
    assert outputs.trace_data_json.is_file()
    assert b"\r\n" not in outputs.summary_tsv.read_bytes()
    with outputs.summary_tsv.open(encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle, delimiter="\t")
        assert reader.fieldnames == [
            "sample_stem",
            "status",
            "cell_area",
            "cell_height",
            "cell_apex_rt",
            "cell_start_rt",
            "cell_end_rt",
            "trace_max_intensity",
            "trace_apex_rt",
            "global_trace_apex_delta_min",
            "local_window_max_intensity",
            "local_window_apex_delta_min",
            "local_window_to_global_max_ratio",
            "region_shadow_verdict",
            "source_candidate_id",
            "highlight_group",
            "apex_aligned_shape_similarity",
            "absolute_own_max_shape_similarity",
        ]
        summary_rows = list(reader)
    assert summary_rows[0]["highlight_group"] == "detected_seed"
    assert summary_rows[1]["region_shadow_verdict"] == "split_supported"

    payload = json.loads(outputs.trace_data_json.read_text(encoding="utf-8"))
    assert list(payload) == [
        "family_id",
        "mz",
        "ppm",
        "rt_min",
        "rt_max",
        "family_center_rt",
        "provenance",
        "trace_count",
        "evidence_summary",
        "traces",
    ]
    assert payload["provenance"] == {}
    assert list(payload["traces"][0]) == [
        "sample_stem",
        "status",
        "group",
        "cell_area",
        "cell_height",
        "cell_apex_rt",
        "cell_start_rt",
        "cell_end_rt",
        "trace_max_intensity",
        "trace_apex_rt",
        "global_trace_apex_delta_min",
        "local_window_max_intensity",
        "local_window_apex_delta_min",
        "local_window_to_global_max_ratio",
        "region_shadow_verdict",
        "source_candidate_id",
        "apex_aligned_shape_similarity",
        "absolute_own_max_shape_similarity",
        "rt",
        "intensity",
    ]
    assert payload["trace_count"] == 2
    assert payload["traces"][0]["rt"] == [1.0, 1.1, 1.2]


class _DriftLookup:
    def __init__(self, deltas: dict[str, float]) -> None:
        self._deltas = deltas
        self._source: DriftPriorSource = "targeted_istd_trend"

    @property
    def source(self) -> DriftPriorSource:
        return self._source

    def sample_delta_min(self, sample_stem: str) -> float | None:
        return self._deltas.get(sample_stem)

    def injection_order(self, sample_stem: str) -> int | None:
        return None


def _synthetic_rows() -> list[TraceOverlayRow]:
    return [
        report.trace_row_from_arrays(
            report.FamilyCell(
                sample_stem="detected-a",
                status="detected",
                area=1000,
                height=100,
                apex_rt=1.1,
                peak_start_rt=1.0,
                peak_end_rt=1.2,
                region_shadow_verdict="current_supported",
                source_candidate_id="detected-a#1",
            ),
            "detected_seed",
            [1.0, 1.1, 1.2],
            [0.0, 100.0, 20.0],
        ),
        report.trace_row_from_arrays(
            report.FamilyCell(
                sample_stem="rescued-a",
                status="backfilled",
                area=800,
                height=80,
                apex_rt=1.11,
                peak_start_rt=1.01,
                peak_end_rt=1.21,
                region_shadow_verdict="split_supported",
                source_candidate_id="",
            ),
            "top_rescued_ms1_area",
            [1.0, 1.1, 1.2],
            [0.0, 80.0, 10.0],
        ),
    ]


def test_overlay_keeps_drift_lookup_backcompat_without_irt_panel(
    tmp_path: Path,
) -> None:
    lookup = _DriftLookup(deltas={"detected-a": 0.01, "rescued-a": -0.01})

    outputs = report.write_family_ms1_overlay_outputs(
        rows=_synthetic_rows(),
        output_dir=tmp_path / "out",
        output_prefix="fam_irt_ms1_overlay",
        family_id="FAM_IRT",
        mz=251.165,
        ppm=10.0,
        rt_min=1.0,
        rt_max=1.2,
        family_center_rt=1.1,
        drift_lookup=lookup,
    )

    assert outputs.png_path.is_file()
    assert outputs.pdf_path is not None
    assert outputs.pdf_path.is_file()


def test_overlay_renders_without_drift_lookup_backcompat(tmp_path: Path) -> None:
    outputs = report.write_family_ms1_overlay_outputs(
        rows=_synthetic_rows(),
        output_dir=tmp_path / "out",
        output_prefix="fam_no_drift_ms1_overlay",
        family_id="FAM_NO_DRIFT",
        mz=251.165,
        ppm=10.0,
        rt_min=1.0,
        rt_max=1.2,
        family_center_rt=1.1,
    )

    assert outputs.png_path.is_file()
    assert outputs.pdf_path is not None
    assert outputs.pdf_path.is_file()


def test_cli_help_lists_drift_arguments() -> None:
    repo_root = Path(__file__).resolve().parents[1]
    script = repo_root / "tools" / "diagnostics" / "family_ms1_overlay_plot.py"

    result = subprocess.run(
        [sys.executable, str(script), "--help"],
        cwd=repo_root,
        env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert "--targeted-workbook" in result.stdout
    assert "--sample-info" in result.stdout
    assert "compatibility" in result.stdout
    assert "does not render" in result.stdout
    assert "iRT panel" in result.stdout


def test_cli_accepts_drift_arguments_as_noop_compatibility(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setattr(report, "load_family_cells", lambda *_args, **_kwargs: ())
    monkeypatch.setattr(
        report,
        "extract_family_trace_rows",
        lambda **_kwargs: (),
    )
    monkeypatch.setattr(
        report,
        "build_family_ms1_evidence_summary",
        lambda _rows: {"family_verdict": "not_assessable"},
    )

    def fake_write_family_ms1_overlay_outputs(**_kwargs):
        return report.FamilyMs1OverlayOutputs(
            png_path=tmp_path / "overlay.png",
            pdf_path=tmp_path / "overlay.pdf",
            summary_tsv=tmp_path / "summary.tsv",
            trace_data_json=tmp_path / "trace.json",
        )

    monkeypatch.setattr(
        report,
        "write_family_ms1_overlay_outputs",
        fake_write_family_ms1_overlay_outputs,
    )

    code = report.main(
        [
            "--alignment-cells",
            str(tmp_path / "cells.tsv"),
            "--family-id",
            "FAM_NOOP",
            "--raw-dir",
            str(tmp_path / "raw"),
            "--dll-dir",
            str(tmp_path / "dll"),
            "--mz",
            "251.165",
            "--rt-min",
            "1.0",
            "--rt-max",
            "1.2",
            "--targeted-workbook",
            str(tmp_path / "missing.xlsx"),
            "--sample-info",
            str(tmp_path / "missing_sample_info.tsv"),
            "--output-dir",
            str(tmp_path / "out"),
        ],
    )

    assert code == 0


def test_stable_jitter_is_reproducible() -> None:
    assert report._stable_jitter("sample-a", width=0.18) == report._stable_jitter(
        "sample-a",
        width=0.18,
    )


def test_apex_aligned_shape_similarity_compares_shifted_peak_shapes() -> None:
    similar_a = report.trace_row_from_arrays(
        _family_cell("similar-a", apex_rt=1.1),
        "detected_seed",
        [1.0, 1.05, 1.1, 1.15, 1.2],
        [0.0, 25.0, 100.0, 25.0, 0.0],
    )
    similar_b = report.trace_row_from_arrays(
        _family_cell("similar-b", apex_rt=2.1),
        "rescued_other",
        [2.0, 2.05, 2.1, 2.15, 2.2],
        [0.0, 20.0, 80.0, 20.0, 0.0],
    )
    different = report.trace_row_from_arrays(
        _family_cell("different", apex_rt=3.1),
        "rescued_other",
        [3.0, 3.05, 3.1, 3.15, 3.2],
        [100.0, 20.0, 0.0, 20.0, 100.0],
    )

    similarity = report._apex_aligned_shape_similarity(
        [similar_a, similar_b, different],
    )

    assert similarity["similar-a"] is not None
    assert similarity["similar-b"] is not None
    assert similarity["different"] is not None
    assert similarity["similar-a"] > 0.5
    assert similarity["similar-b"] > 0.5
    assert similarity["different"] < 0.0


def test_apex_aligned_shape_similarity_uses_detected_anchor_reference() -> None:
    detected = report.trace_row_from_arrays(
        _family_cell("detected-anchor", status="detected", apex_rt=1.1),
        "detected_seed",
        [1.0, 1.05, 1.1, 1.15, 1.2],
        [0.0, 25.0, 100.0, 25.0, 0.0],
    )
    rescued_like_detected = report.trace_row_from_arrays(
        _family_cell("rescued-like-detected", apex_rt=2.1),
        "top_rescued_ms1_area",
        [2.0, 2.05, 2.1, 2.15, 2.2],
        [0.0, 20.0, 80.0, 20.0, 0.0],
    )
    rescued_neighbor_majority = [
        report.trace_row_from_arrays(
            _family_cell(f"rescued-neighbor-{index}", apex_rt=3.1 + index),
            "rescued_other",
            [3.0 + index, 3.05 + index, 3.1 + index, 3.15 + index, 3.2 + index],
            [100.0, 20.0, 0.0, 20.0, 100.0],
        )
        for index in range(3)
    ]

    similarity = report._apex_aligned_shape_similarity(
        [detected, rescued_like_detected, *rescued_neighbor_majority],
    )

    assert similarity["detected-anchor"] is not None
    assert similarity["rescued-like-detected"] is not None
    assert similarity["rescued-like-detected"] > 0.5
    assert all(
        (similarity[f"rescued-neighbor-{index}"] or 0.0) < 0.0 for index in range(3)
    )


def test_hypothesis_overlay_layout_keeps_apex_alignment_on_evidence_surface() -> None:
    layout = report._hypothesis_overlay_panel_layout()
    panel_ids = {panel for row in layout for panel in row}

    assert panel_ids == {"aligned", "raw", "legend"}
    assert "aligned" in panel_ids
    assert "area" not in panel_ids
    assert "shape" not in panel_ids


def test_apex_aligned_trace_uses_shared_gaussian15_normalization() -> None:
    row = report.trace_row_from_arrays(
        _family_cell("large-neighbor", apex_rt=1.0),
        "rescued_other",
        [0.2, 0.9, 1.0, 1.1],
        [1000.0, 0.0, 10.0, 0.0],
    )

    _rt, normalized = report._apex_aligned_normalized_trace(row)
    _shared_trace, shared_facts = evidence.gaussian15_trace_window_evidence(
        row.rt,
        row.intensity,
        query_apex_rt_min=row.cell_apex_rt,
        half_window_min=report.APEX_ALIGN_HALF_WINDOW_MIN,
    )

    assert max(normalized) == 1.0
    assert report._local_to_global_max_ratio(row) == pytest.approx(
        shared_facts.local_to_global_max_ratio
    )
    assert report._local_to_global_max_ratio(row) != pytest.approx(0.01)


def test_gaussian_smooth_is_plot_only_and_preserves_trace_shape() -> None:
    values = report.np.asarray([0.0, 0.0, 100.0, 0.0, 0.0], dtype=float)

    smoothed = report._gaussian_smooth(values, points=5)

    assert smoothed.shape == values.shape
    assert smoothed[2] < 100.0
    assert smoothed[2] == max(smoothed)
    assert smoothed[1] > 0.0
    assert smoothed[3] > 0.0


def test_family_evidence_summary_flags_global_apex_interference() -> None:
    rows = [
        report.trace_row_from_arrays(
            _family_cell("detected-a", status="detected", apex_rt=1.0),
            "detected_seed",
            [0.2, 0.9, 1.0, 1.1],
            [1000.0, 0.0, 10.0, 0.0],
        ),
        report.trace_row_from_arrays(
            _family_cell("detected-b", status="detected", apex_rt=1.0),
            "detected_seed",
            [0.2, 0.9, 1.0, 1.1],
            [900.0, 0.0, 10.0, 0.0],
        ),
        report.trace_row_from_arrays(
            _family_cell("rescued-a", apex_rt=1.0),
            "rescued_other",
            [0.2, 0.9, 1.0, 1.1],
            [800.0, 0.0, 10.0, 0.0],
        ),
    ]

    summary = report.build_family_ms1_evidence_summary(rows)

    assert summary["family_verdict"] == "review_required_neighboring_ms1_interference"
    assert summary["global_apex_interference_count"] == 3


def test_scaled_own_max_shape_support_can_override_global_apex_interference() -> None:
    rows = [
        report.trace_row_from_arrays(
            _family_cell("detected-a", status="detected", apex_rt=1.0),
            "detected_seed",
            [0.2, 0.9, 1.0, 1.1],
            [110.0, 0.0, 100.0, 0.0],
        ),
        report.trace_row_from_arrays(
            _family_cell("detected-b", status="detected", apex_rt=1.0),
            "detected_seed",
            [0.2, 0.9, 1.0, 1.1],
            [108.0, 0.0, 100.0, 0.0],
        ),
        report.trace_row_from_arrays(
            _family_cell("rescued-a", apex_rt=1.0),
            "rescued_other",
            [0.2, 0.9, 1.0, 1.1],
            [112.0, 0.0, 100.0, 0.0],
        ),
    ]

    summary = report.build_family_ms1_evidence_summary(rows)

    assert summary["family_verdict"] == "ms1_shape_supports_family_backfill"
    assert summary["global_apex_interference_count"] == 3
    assert summary["shape_supported_fraction"] == 1.0
    assert summary["local_apex_supported_fraction"] == 1.0
    assert summary["low_selected_peak_dominance_fraction"] == 0.0


def test_absolute_own_max_shape_support_handles_misaligned_selected_apex() -> None:
    rows = [
        report.trace_row_from_arrays(
            _family_cell("detected-a", status="detected", apex_rt=0.72),
            "detected_seed",
            [0.60, 0.80, 1.00, 1.20, 1.40],
            [0.0, 25.0, 100.0, 25.0, 0.0],
        ),
        report.trace_row_from_arrays(
            _family_cell("detected-b", status="detected", apex_rt=1.30),
            "detected_seed",
            [0.60, 0.80, 1.00, 1.20, 1.40],
            [0.0, 20.0, 95.0, 20.0, 0.0],
        ),
        report.trace_row_from_arrays(
            _family_cell("rescued-a", apex_rt=1.28),
            "top_rescued_ms1_area",
            [0.60, 0.80, 1.02, 1.20, 1.40],
            [0.0, 30.0, 90.0, 30.0, 0.0],
        ),
        report.trace_row_from_arrays(
            _family_cell("rescued-b", apex_rt=0.75),
            "rescued_other",
            [0.60, 0.82, 1.01, 1.20, 1.40],
            [0.0, 28.0, 88.0, 28.0, 0.0],
        ),
    ]

    summary = report.build_family_ms1_evidence_summary(rows)

    assert summary["family_verdict"] == "ms1_shape_supports_family_backfill"
    assert summary["global_apex_interference_fraction"] == 1.0
    assert summary["local_apex_supported_fraction"] == 0.0
    assert summary["absolute_own_max_shape_supported_fraction"] == 1.0
    assert summary["absolute_trace_apex_cluster_fraction"] == 1.0
    assert summary["low_selected_peak_dominance_fraction"] == 0.0


def test_global_apex_interference_counts_shape_unevaluable_traces() -> None:
    rows = [
        report.trace_row_from_arrays(
            _family_cell(
                "detected-a",
                status="detected",
                apex_rt=1.0,
                height=200.0,
            ),
            "detected_seed",
            [0.9, 1.0, 1.1],
            [0.0, 200.0, 0.0],
        ),
        report.trace_row_from_arrays(
            _family_cell(
                "detected-b",
                status="detected",
                apex_rt=1.0,
                height=180.0,
            ),
            "detected_seed",
            [0.9, 1.0, 1.1],
            [0.0, 180.0, 0.0],
        ),
        report.trace_row_from_arrays(
            _family_cell("far-rescued", apex_rt=1.0, height=100.0),
            "rescued_other",
            [2.0, 2.1, 2.2],
            [0.0, 100.0, 0.0],
        ),
    ]

    summary = report.build_family_ms1_evidence_summary(rows)

    assert summary["family_verdict"] == "review_required_neighboring_ms1_interference"
    assert summary["evaluable_trace_count"] == 2
    assert summary["global_apex_assessable_trace_count"] == 3
    assert summary["global_apex_interference_fraction"] == 1 / 3


def test_family_evidence_summary_marks_dda_trigger_limited_support() -> None:
    rows = [
        report.trace_row_from_arrays(
            _family_cell(
                "detected-a",
                status="detected",
                apex_rt=1.0,
                height=200.0,
            ),
            "detected_seed",
            [0.9, 1.0, 1.1],
            [0.0, 200.0, 0.0],
        ),
        report.trace_row_from_arrays(
            _family_cell(
                "detected-b",
                status="detected",
                apex_rt=1.0,
                height=180.0,
            ),
            "detected_seed",
            [0.9, 1.0, 1.1],
            [0.0, 180.0, 0.0],
        ),
        report.trace_row_from_arrays(
            _family_cell("rescued-a", apex_rt=1.0, height=100.0),
            "rescued_other",
            [0.9, 1.0, 1.1],
            [0.0, 100.0, 0.0],
        ),
        report.trace_row_from_arrays(
            _family_cell("rescued-b", apex_rt=1.0, height=90.0),
            "rescued_other",
            [0.9, 1.0, 1.1],
            [0.0, 90.0, 0.0],
        ),
    ]

    summary = report.build_family_ms1_evidence_summary(rows)

    assert summary["dda_trigger_limited_ms2_support"] is True
    assert summary["detected_to_rescued_height_median_ratio"] == 2.0
    assert summary["detected_to_rescued_local_window_max_median_ratio"] == 2.0


def test_family_evidence_summary_requires_assessable_ms1_coverage() -> None:
    rows = [
        report.trace_row_from_arrays(
            _family_cell(
                "detected-a",
                status="detected",
                apex_rt=1.0,
                height=200.0,
            ),
            "detected_seed",
            [0.9, 1.0, 1.1],
            [0.0, 200.0, 0.0],
        ),
        report.trace_row_from_arrays(
            _family_cell(
                "detected-b",
                status="detected",
                apex_rt=1.0,
                height=180.0,
            ),
            "detected_seed",
            [0.9, 1.0, 1.1],
            [0.0, 180.0, 0.0],
        ),
        report.trace_row_from_arrays(
            _family_cell("rescued-empty-xic", apex_rt=1.0, height=90.0),
            "rescued_other",
            [0.9, 1.0, 1.1],
            [0.0, 0.0, 0.0],
        ),
        report.trace_row_from_arrays(
            _family_cell("rescued-second-empty-xic", apex_rt=1.0, height=80.0),
            "rescued_other",
            [0.9, 1.0, 1.1],
            [0.0, 0.0, 0.0],
        ),
    ]

    summary = report.build_family_ms1_evidence_summary(rows)

    assert summary["family_verdict"] == "review_required_low_ms1_assessable_coverage"
    assert summary["selected_apex_in_trace_window_fraction"] == 1.0
    assert summary["global_apex_assessable_fraction"] == 0.5


def test_missing_required_alignment_columns_fail_clearly(tmp_path: Path) -> None:
    cells_path = tmp_path / "alignment_cells.tsv"
    cells_path.write_text(
        "feature_family_id\tsample_stem\nFAM001\ta\n",
        encoding="utf-8",
    )

    try:
        report.load_family_cells(cells_path, "FAM001")
    except ValueError as exc:
        assert "Missing required columns" in str(exc)
        assert "status" in str(exc)
    else:
        raise AssertionError("Expected missing-column failure")


def _cell_row(
    family_id: str,
    sample_stem: str,
    status: str,
    area: str,
) -> dict[str, str]:
    return {
        "feature_family_id": family_id,
        "sample_stem": sample_stem,
        "status": status,
        "area": area,
        "height": "10",
        "apex_rt": "1.1",
        "peak_start_rt": "1.0",
        "peak_end_rt": "1.2",
        "region_shadow_verdict": "current_supported",
        "source_candidate_id": "",
    }


def _family_cell(
    sample_stem: str,
    *,
    apex_rt: float,
    status: str = "backfilled",
    height: float = 10.0,
) -> report.FamilyCell:
    return report.FamilyCell(
        sample_stem=sample_stem,
        status=status,
        area=100,
        height=height,
        apex_rt=apex_rt,
        peak_start_rt=apex_rt - 0.1,
        peak_end_rt=apex_rt + 0.1,
        region_shadow_verdict="current_supported",
        source_candidate_id="",
    )


def _write_tsv(path: Path, rows: list[dict[str, str]]) -> None:
    fields = (
        "feature_family_id",
        "sample_stem",
        "status",
        "area",
        "height",
        "apex_rt",
        "peak_start_rt",
        "peak_end_rt",
        "region_shadow_verdict",
        "source_candidate_id",
    )
    with path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(
            fh,
            fieldnames=fields,
            delimiter="\t",
            lineterminator="\n",
        )
        writer.writeheader()
        writer.writerows(rows)
