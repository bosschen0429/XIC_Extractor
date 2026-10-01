from __future__ import annotations

import numpy as np
import pytest

from xic_extractor.configuration.models import Target
from xic_extractor.diagnostics.targeted_ms1_shape_identity import (
    build_targeted_ms1_shape_identity_rows,
)
from xic_extractor.diagnostics.targeted_ms1_shape_identity_support_builder import (
    build_targeted_ms1_shape_identity_candidates_from_traces,
    build_targeted_ms1_shape_identity_support_plan,
    select_smoothed_local_apex_rt,
)
from xic_extractor.xic_models import XICTrace


def test_support_plan_selects_all_policy_blocked_rows_with_pair_support() -> None:
    targets = [_target("5-hmdC", istd_pair="d3-5-hmdC"), _istd("d3-5-hmdC")]
    rows = [
        *_reference_rows("RefA", target_rt="9.10", istd_rt="9.00"),
        *_reference_rows("RefB", target_rt="9.12", istd_rt="9.02"),
        *_reference_rows("RefC", target_rt="9.14", istd_rt="9.04"),
        *_candidate_rows("CandidateA", istd_rt="9.01"),
        *_candidate_rows("CandidateB", istd_rt="9.03"),
        *_candidate_rows("NoPairSupport", istd_rt="9.02", pair_support=False),
        *_candidate_rows("NoPairRtSupport", istd_rt="9.02", pair_rt_support=False),
        *_candidate_rows("Ambiguous", istd_rt="9.02", product_state="ambiguous"),
        *_candidate_rows(
            "ExtraPolicy",
            istd_rt="9.02",
            extra_not_counted_reason="paired_istd_rt_mismatch_policy",
        ),
    ]

    plan = build_targeted_ms1_shape_identity_support_plan(
        rows,
        targets=targets,
    )

    assert [candidate.sample_name for candidate in plan.candidates] == [
        "CandidateA",
        "CandidateB",
    ]
    assert plan.candidates[0].expected_candidate_rt_min == pytest.approx(9.11)
    assert plan.candidates[1].expected_candidate_rt_min == pytest.approx(9.13)
    trace_request_keys = {
        (request.sample_name, request.target_name) for request in plan.trace_requests
    }
    assert trace_request_keys == {
        ("RefB", "5-hmdC"),
        ("CandidateA", "5-hmdC"),
        ("CandidateB", "5-hmdC"),
    }


def test_support_candidates_use_smoothed_apex_near_pair_reference_mode() -> None:
    targets = [_target("5-hmdC", istd_pair="d3-5-hmdC"), _istd("d3-5-hmdC")]
    rows = [
        *_reference_rows("RefA", target_rt="9.10", istd_rt="9.00"),
        *_reference_rows("RefB", target_rt="9.12", istd_rt="9.02"),
        *_reference_rows("RefC", target_rt="9.14", istd_rt="9.04"),
        *_candidate_rows("CandidateA", istd_rt="9.01"),
        *_candidate_rows("CandidateB", istd_rt="9.03"),
    ]
    plan = build_targeted_ms1_shape_identity_support_plan(rows, targets=targets)
    rt = np.linspace(8.7, 9.6, 181)
    traces = {
        ("RefB", "5-hmdC"): _trace(rt, center=9.12, scale=5000.0),
        ("CandidateA", "5-hmdC"): _trace(rt, center=9.11, scale=80.0),
        ("CandidateB", "5-hmdC"): _trace(rt, center=9.13, scale=120.0),
    }

    candidates = build_targeted_ms1_shape_identity_candidates_from_traces(
        plan,
        targets=targets,
        traces=traces,
        candidate_search_half_window_min=0.20,
        smooth_points=5,
    )
    evidence_rows = build_targeted_ms1_shape_identity_rows(
        candidates,
        smooth_points=5,
    )

    assert [candidate.sample_name for candidate in candidates] == [
        "CandidateA",
        "CandidateB",
    ]
    assert candidates[0].candidate_rt_min == pytest.approx(9.11)
    assert candidates[1].candidate_rt_min == pytest.approx(9.13)
    assert [row["own_max_same_peak_status"] for row in evidence_rows] == [
        "own_max_same_peak_supported",
        "own_max_same_peak_supported",
    ]


def test_support_candidates_emit_inconclusive_row_for_missing_trace() -> None:
    targets = [_target("5-hmdC", istd_pair="d3-5-hmdC"), _istd("d3-5-hmdC")]
    rows = [
        *_reference_rows("RefA", target_rt="9.10", istd_rt="9.00"),
        *_reference_rows("RefB", target_rt="9.12", istd_rt="9.02"),
        *_reference_rows("RefC", target_rt="9.14", istd_rt="9.04"),
        *_candidate_rows("CandidateA", istd_rt="9.01"),
    ]
    plan = build_targeted_ms1_shape_identity_support_plan(rows, targets=targets)
    rt = np.linspace(8.7, 9.6, 181)
    candidates = build_targeted_ms1_shape_identity_candidates_from_traces(
        plan,
        targets=targets,
        traces={("RefB", "5-hmdC"): _trace(rt, center=9.12, scale=5000.0)},
        candidate_search_half_window_min=0.20,
        smooth_points=5,
    )
    evidence_rows = build_targeted_ms1_shape_identity_rows(
        candidates,
        smooth_points=5,
    )

    assert len(candidates) == 1
    assert candidates[0].sample_name == "CandidateA"
    assert evidence_rows[0]["own_max_same_peak_status"] == (
        "own_max_same_peak_inconclusive"
    )
    assert evidence_rows[0]["own_max_same_peak_supported"] == "FALSE"
    assert "missing_candidate_or_reference_rt" in evidence_rows[0]["reason"]


def test_select_smoothed_local_apex_prefers_expected_window_over_far_peak() -> None:
    rt = np.linspace(8.8, 9.8, 201)
    expected_peak = _gaussian(rt, center=9.12, width=0.035, scale=80.0)
    far_peak = _gaussian(rt, center=9.70, width=0.035, scale=200.0)
    trace = XICTrace.from_arrays(rt, expected_peak + far_peak)

    apex_rt = select_smoothed_local_apex_rt(
        trace,
        center_rt_min=9.12,
        target_window_start_min=8.8,
        target_window_end_min=9.8,
        half_window_min=0.20,
        smooth_points=5,
    )

    assert apex_rt == pytest.approx(9.12)


def _target(label: str, *, istd_pair: str) -> Target:
    return Target(
        label=label,
        mz=258.0,
        rt_min=8.8,
        rt_max=9.8,
        ppm_tol=20.0,
        neutral_loss_da=116.0,
        nl_ppm_warn=10.0,
        nl_ppm_max=20.0,
        is_istd=False,
        istd_pair=istd_pair,
    )


def _istd(label: str) -> Target:
    return Target(
        label=label,
        mz=261.0,
        rt_min=8.8,
        rt_max=9.8,
        ppm_tol=20.0,
        neutral_loss_da=116.0,
        nl_ppm_warn=10.0,
        nl_ppm_max=20.0,
        is_istd=True,
        istd_pair="",
    )


def _reference_rows(
    sample_name: str,
    *,
    target_rt: str,
    istd_rt: str,
) -> list[dict[str, str]]:
    return [
        _long_row(
            sample_name,
            "5-hmdC",
            role="Analyte",
            istd_pair="d3-5-hmdC",
            rt=target_rt,
            nl="OK",
            product_state="detected_clean",
            counted="TRUE",
            reason="decision: detected",
        ),
        _long_row(
            sample_name,
            "d3-5-hmdC",
            role="ISTD",
            istd_pair="",
            rt=istd_rt,
            nl="OK",
            product_state="detected_clean",
            counted="TRUE",
            reason="decision: detected",
        ),
    ]


def _candidate_rows(
    sample_name: str,
    *,
    istd_rt: str,
    pair_support: bool = True,
    pair_rt_support: bool = True,
    product_state: str = "not_counted",
    extra_not_counted_reason: str = "",
) -> list[dict[str, str]]:
    support_values = ["ms1_coherent"]
    if pair_rt_support:
        support_values.append("paired_istd_rt_within_1min_support")
    if pair_support:
        support_values.append("paired_area_ratio_support")
    support = ", ".join(support_values)
    not_counted = "analyte_nl_fail_requires_policy"
    if extra_not_counted_reason:
        not_counted = f"{not_counted}, {extra_not_counted_reason}"
    return [
        _long_row(
            sample_name,
            "5-hmdC",
            role="Analyte",
            istd_pair="d3-5-hmdC",
            rt="ND",
            nl="NL_FAIL",
            product_state=product_state,
            counted="FALSE",
            reason=(
                "decision: not_counted; "
                f"support: {support}; "
                f"not_counted: {not_counted}"
            ),
        ),
        _long_row(
            sample_name,
            "d3-5-hmdC",
            role="ISTD",
            istd_pair="",
            rt=istd_rt,
            nl="OK",
            product_state="detected_clean",
            counted="TRUE",
            reason="decision: detected",
        ),
    ]


def _long_row(
    sample_name: str,
    target_name: str,
    *,
    role: str,
    istd_pair: str,
    rt: str,
    nl: str,
    product_state: str,
    counted: str,
    reason: str,
) -> dict[str, str]:
    return {
        "SampleName": sample_name,
        "Target": target_name,
        "Role": role,
        "ISTD Pair": istd_pair,
        "RT": rt,
        "NL": nl,
        "Product State": product_state,
        "Counted Detection": counted,
        "Reason": reason,
    }


def _trace(rt: np.ndarray, *, center: float, scale: float) -> XICTrace:
    return XICTrace.from_arrays(
        rt,
        _gaussian(rt, center=center, width=0.045, scale=scale),
    )


def _gaussian(
    rt: np.ndarray,
    *,
    center: float,
    width: float,
    scale: float,
) -> np.ndarray:
    return scale * np.exp(-0.5 * ((rt - center) / width) ** 2)
