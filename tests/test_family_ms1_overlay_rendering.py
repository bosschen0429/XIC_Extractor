from __future__ import annotations

import pytest

from tools.diagnostics.family_ms1_overlay_models import TraceOverlayRow
from tools.diagnostics.family_ms1_overlay_rendering import (
    _family_overlay_panel_layout,
    _selected_peak_focus_rows,
    _selected_peak_segment_label,
    _selected_peak_window_bounds,
    _single_anchor_review_note,
)
from tools.diagnostics.family_ms1_overlay_rendering_styles import _plot_unified_legend


def test_family_overlay_panel_layout_keeps_only_family_context_graphs() -> None:
    layout = _family_overlay_panel_layout()
    panel_ids = {panel for row in layout for panel in row}

    assert panel_ids == {"norm", "raw", "legend"}
    assert "aligned" not in panel_ids
    assert "area" not in panel_ids
    assert "rt" not in panel_ids
    assert "shape" not in panel_ids
    assert "irt" not in panel_ids


def test_selected_peak_focus_rows_keep_all_detected_and_rescued_peak_traces() -> None:
    high = _trace_row("high", selected_peak_height=80.0, global_peak_height=100.0)
    low = _trace_row("low", selected_peak_height=30.0, global_peak_height=100.0)
    non_product = _trace_row(
        "qc",
        selected_peak_height=100.0,
        global_peak_height=100.0,
        status="unchecked",
        group="pooled_qc",
    )

    focus = _selected_peak_focus_rows((high, low, non_product))

    assert focus == (high, low)


def test_selected_peak_window_bounds_use_cell_peak_segment_with_padding() -> None:
    left = _trace_row(
        "left",
        selected_peak_height=80.0,
        global_peak_height=100.0,
        start=9.92,
        end=10.08,
    )
    right = _trace_row(
        "right",
        selected_peak_height=90.0,
        global_peak_height=100.0,
        start=10.10,
        end=10.32,
    )

    low, high = _selected_peak_window_bounds((left, right), rt_min=8.0, rt_max=12.0)

    assert low == pytest.approx(9.67)
    assert high == pytest.approx(10.57)


def test_selected_peak_segment_label_names_absolute_rt_segment() -> None:
    rows = (
        _trace_row(
            "left",
            selected_peak_height=80.0,
            global_peak_height=100.0,
            start=9.92,
            end=10.08,
        ),
        _trace_row(
            "right",
            selected_peak_height=90.0,
            global_peak_height=100.0,
            start=10.10,
            end=10.32,
        ),
    )

    assert _selected_peak_segment_label(rows) == "9.92-10.32 min"


def test_single_detected_anchor_note_marks_review_only_consensus() -> None:
    anchor = _trace_row("anchor", selected_peak_height=90.0, global_peak_height=100.0)

    assert _single_anchor_review_note((anchor,)) == "; single-anchor review only"
    assert _single_anchor_review_note((anchor, anchor)) == ""


def test_unified_legend_does_not_draw_detected_median() -> None:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    fig, ax = plt.subplots()
    try:
        _plot_unified_legend(ax)
        legend = ax.get_legend()
        assert legend is not None
        labels = [text.get_text() for text in legend.get_texts()]
        assert "detected NL seed" in labels
        assert "detected median" not in labels
        assert "rescued median" in labels
    finally:
        plt.close(fig)


def _trace_row(
    sample: str,
    *,
    selected_peak_height: float,
    global_peak_height: float,
    status: str = "backfilled",
    group: str = "top_rescued_ms1_area",
    start: float = 9.9,
    apex: float = 10.0,
    end: float = 10.1,
) -> TraceOverlayRow:
    return TraceOverlayRow(
        sample_stem=sample,
        status=status,
        group=group,
        cell_area=1000.0,
        cell_height=selected_peak_height,
        cell_apex_rt=apex,
        cell_start_rt=start,
        cell_end_rt=end,
        trace_max_intensity=global_peak_height,
        trace_apex_rt=12.0 if global_peak_height > selected_peak_height else apex,
        region_shadow_verdict="",
        source_candidate_id=sample,
        rt=(9.8, apex, 10.2, 12.0),
        intensity=(0.0, selected_peak_height, 0.0, global_peak_height),
    )
