from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from xic_extractor.diagnostics.targeted_ms1_shape_identity import (
    TargetedMs1ShapeCandidate,
    build_targeted_ms1_shape_identity_rows,
    write_targeted_ms1_shape_identity_tsv,
)
from xic_extractor.extraction.targeted_ms1_shape_identity_projection import (
    DECISION_AUTHORITY,
    SCHEMA_VERSION,
    TARGETED_MS1_SHAPE_IDENTITY_COLUMNS,
    VALIDATION_LABEL,
)
from xic_extractor.output.targeted_product_model import (
    OWN_MAX_SAME_PEAK_SUPPORT_REASON,
)


def test_targeted_ms1_shape_identity_supports_own_max_same_peak() -> None:
    rt = np.linspace(8.7, 9.5, 121)
    candidate_trace = _gaussian(rt, 9.12, 0.055, scale=150.0)
    reference_trace = _gaussian(rt, 9.12, 0.055, scale=2200.0)

    rows = build_targeted_ms1_shape_identity_rows(
        [
            TargetedMs1ShapeCandidate(
                sample_name="TumorBC2294_DNA",
                target_name="5-hmdC",
                target_role="analyte",
                paired_istd="d3-5-hmdC",
                source_row_id="TumorBC2294_DNA|5-hmdC",
                candidate_state="NL_FAIL_MS1_candidate",
                reference_source="group_median_ms1_anchor",
                candidate_rt_min=9.12,
                candidate_rt=rt,
                candidate_intensity=candidate_trace,
                reference_rt_min=9.12,
                reference_rt=rt,
                reference_intensity=reference_trace,
                paired_istd_rt_min=9.03,
                target_window_start_min=8.9,
                target_window_end_min=9.3,
            )
        ],
        smooth_points=5,
    )

    row = rows[0]
    assert row["schema_version"] == SCHEMA_VERSION
    assert row["validation_label"] == VALIDATION_LABEL
    assert row["decision_authority"] == DECISION_AUTHORITY
    assert row["own_max_same_peak_status"] == "own_max_same_peak_supported"
    assert row["own_max_same_peak_supported"] == "TRUE"
    assert row["own_max_same_peak_support_reason"] == OWN_MAX_SAME_PEAK_SUPPORT_REASON
    assert float(row["own_max_same_peak_similarity"]) == pytest.approx(1.0)
    assert row["target_window_status"] == "candidate_inside_target_window"
    assert float(row["candidate_pair_rt_delta_min"]) == pytest.approx(0.09)
    assert row["competing_peak_status"] == "no_competing_peak_observed"
    assert "diagnostic_only" in row["reason"]
    assert "paired_istd_rt_delta_available" in row["reason"]


def test_targeted_ms1_shape_identity_flags_strong_competing_peak_separately() -> None:
    rt = np.linspace(8.7, 9.9, 151)
    candidate_peak = _gaussian(rt, 9.12, 0.055, scale=100.0)
    competing_peak = _gaussian(rt, 9.66, 0.055, scale=90.0)
    candidate_trace = candidate_peak + competing_peak
    reference_trace = _gaussian(rt, 9.12, 0.055, scale=500.0)

    row = build_targeted_ms1_shape_identity_rows(
        [
            TargetedMs1ShapeCandidate(
                sample_name="S1",
                target_name="5-hmdC",
                candidate_rt_min=9.12,
                candidate_rt=rt,
                candidate_intensity=candidate_trace,
                reference_rt_min=9.12,
                reference_rt=rt,
                reference_intensity=reference_trace,
            )
        ],
        smooth_points=5,
        strong_competing_peak_ratio=0.65,
    )[0]

    assert row["own_max_same_peak_status"] == "own_max_same_peak_not_supported"
    assert row["own_max_same_peak_supported"] == "FALSE"
    assert row["own_max_same_peak_support_reason"] == ""
    assert row["competing_peak_status"] == "strong_competing_peak_observed_diagnostic"
    assert float(row["strongest_competing_peak_own_max_ratio"]) == pytest.approx(
        0.9,
        rel=0.2,
    )
    assert "strong_competing_peak_observed_diagnostic" in row["reason"]


def test_targeted_ms1_shape_identity_fails_closed_for_missing_candidate_rt() -> None:
    rt = np.linspace(8.7, 9.5, 121)

    row = build_targeted_ms1_shape_identity_rows(
        [
            TargetedMs1ShapeCandidate(
                sample_name="S1",
                target_name="5-hmdC",
                candidate_rt_min=None,
                candidate_rt=rt,
                candidate_intensity=np.zeros_like(rt),
                reference_rt_min=9.12,
                reference_rt=rt,
                reference_intensity=_gaussian(rt, 9.12, 0.055, scale=500.0),
                target_window_start_min=8.9,
                target_window_end_min=9.3,
            )
        ],
        smooth_points=5,
    )[0]

    assert row["own_max_same_peak_status"] == "own_max_same_peak_inconclusive"
    assert row["own_max_same_peak_supported"] == "FALSE"
    assert row["own_max_same_peak_support_reason"] == ""
    assert row["target_window_status"] == "target_window_inconclusive"
    assert row["competing_peak_status"] == "competing_peak_inconclusive"
    assert "missing_candidate_or_reference_rt" in row["reason"]
    assert "non_positive_signal" in row["reason"]


def test_write_targeted_ms1_shape_identity_tsv_uses_stable_columns(
    tmp_path: Path,
) -> None:
    output_path = tmp_path / "targeted_ms1_shape_identity.tsv"

    write_targeted_ms1_shape_identity_tsv(
        output_path,
        [
            {
                "schema_version": SCHEMA_VERSION,
                "validation_label": VALIDATION_LABEL,
                "decision_authority": DECISION_AUTHORITY,
                "sample_name": "S1",
                "target_name": "5-hmdC",
                "own_max_same_peak_supported": True,
            }
        ],
    )

    header = output_path.read_text(encoding="utf-8").splitlines()[0].split("\t")
    assert tuple(header) == TARGETED_MS1_SHAPE_IDENTITY_COLUMNS


def _gaussian(
    rt: np.ndarray,
    center: float,
    width: float,
    *,
    scale: float,
) -> np.ndarray:
    return scale * np.exp(-0.5 * ((rt - center) / width) ** 2)
