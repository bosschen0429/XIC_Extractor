from __future__ import annotations

import csv
from pathlib import Path

import numpy as np

from tools.diagnostics.targeted_ms1_shape_identity_from_grid import (
    build_candidates_from_own_max_trace_grid,
    run_targeted_ms1_shape_identity_from_grid,
)
from xic_extractor.extraction.targeted_projection_reasons import (
    OWN_MAX_SAME_PEAK_SUPPORT_REASON,
)


def test_build_candidates_from_own_max_trace_grid_groups_by_sample() -> None:
    rt = np.linspace(8.8, 9.4, 31)
    grid_rows = [
        {
            "sample": "S1",
            "rt_min": f"{value:.6f}",
            "analyte_gaussian15_own_max": "1.0",
            "group_median_analyte_own_max": "1.0",
        }
        for value in rt
    ]

    candidates = build_candidates_from_own_max_trace_grid(
        [
            {
                "sample": "S1",
                "target": "5-hmdC",
                "paired_istd": "d3-5-hmdC",
                "candidate_rt_min": "9.12",
                "group_median_candidate_rt_min": "9.12",
                "paired_istd_gaussian15_apex_rt_min": "9.03",
                "source_summary_analyte_current_nl": "NL_FAIL",
            }
        ],
        grid_rows,
        target_window_start_min=8.0,
        target_window_end_min=10.0,
    )

    assert len(candidates) == 1
    assert candidates[0].sample_name == "S1"
    assert candidates[0].target_name == "5-hmdC"
    assert candidates[0].candidate_rt_min == 9.12
    assert len(candidates[0].candidate_rt) == len(rt)


def test_run_targeted_ms1_shape_identity_from_grid_writes_support_token(
    tmp_path: Path,
) -> None:
    summary_tsv = tmp_path / "own_max_similarity_summary.tsv"
    trace_grid_tsv = tmp_path / "own_max_similarity_trace_grid.tsv"
    output_tsv = tmp_path / "targeted_ms1_shape_identity.tsv"
    rt = np.linspace(8.8, 9.4, 61)
    shape = np.exp(-0.5 * ((rt - 9.12) / 0.06) ** 2)

    _write_tsv(
        summary_tsv,
        (
            "sample",
            "target",
            "paired_istd",
            "candidate_rt_min",
            "group_median_candidate_rt_min",
            "paired_istd_gaussian15_apex_rt_min",
            "source_summary_analyte_current_nl",
        ),
        [
            {
                "sample": "S1",
                "target": "5-hmdC",
                "paired_istd": "d3-5-hmdC",
                "candidate_rt_min": "9.12",
                "group_median_candidate_rt_min": "9.12",
                "paired_istd_gaussian15_apex_rt_min": "9.03",
                "source_summary_analyte_current_nl": "NL_FAIL",
            }
        ],
    )
    _write_tsv(
        trace_grid_tsv,
        (
            "sample",
            "rt_min",
            "analyte_gaussian15_own_max",
            "group_median_analyte_own_max",
        ),
        [
            {
                "sample": "S1",
                "rt_min": f"{rt_value:.6f}",
                "analyte_gaussian15_own_max": f"{shape_value:.6f}",
                "group_median_analyte_own_max": f"{shape_value:.6f}",
            }
            for rt_value, shape_value in zip(rt, shape, strict=True)
        ],
    )

    outputs = run_targeted_ms1_shape_identity_from_grid(
        summary_tsv=summary_tsv,
        trace_grid_tsv=trace_grid_tsv,
        output_tsv=output_tsv,
        target_window_start_min=8.0,
        target_window_end_min=10.0,
    )

    rows = _read_tsv(outputs.evidence_tsv)
    assert len(rows) == 1
    assert rows[0]["schema_version"] == "targeted_ms1_shape_identity"
    assert rows[0]["validation_label"] == "diagnostic_only"
    assert rows[0]["decision_authority"] == "diagnostic_only_no_product_write"
    assert rows[0]["own_max_same_peak_status"] == "own_max_same_peak_supported"
    assert rows[0]["own_max_same_peak_support_reason"] == (
        OWN_MAX_SAME_PEAK_SUPPORT_REASON
    )
    assert rows[0]["target_window_status"] == "candidate_inside_target_window"


def _write_tsv(
    path: Path,
    fieldnames: tuple[str, ...],
    rows: list[dict[str, str]],
) -> None:
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, delimiter="\t")
        writer.writeheader()
        writer.writerows(rows)


def _read_tsv(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle, delimiter="\t"))
