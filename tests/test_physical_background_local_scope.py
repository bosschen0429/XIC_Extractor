"""Observed tail counterexample and NL-linked local-component controls.

The oracle is qualitative: wider background must not manufacture a local peak
on the QC tail or transfer a small NL peak onto the earlier larger component.
It does not establish concentration truth or require old numeric-area parity.
"""

import json
from pathlib import Path

import numpy as np
import pytest

from tests.discovery_ms1_capture_fixtures import BoundedScanRaw
from xic_extractor.acquisition.physical_capture import (
    PhysicalPeakCapture,
    PhysicalPeakSearch,
)
from xic_extractor.configuration.signal_processing_profile import (
    SignalProcessingProfile,
)
from xic_extractor.peak_detection import physical_peak_evaluation as owner
from xic_extractor.peak_detection.physical_peak_models import (
    DISCOVERY_PHYSICAL_PEAK_POINT_POLICY,
)
from xic_extractor.xic_models import XICRequest

CASES = json.loads(
    (Path(__file__).parent / "fixtures/physical_background_local_scope.json").read_text(
        encoding="utf-8"
    )
)["cases"]
EDGE_CASES = json.loads(
    (
        Path(__file__).parent / "fixtures/physical_background_edge_settlement.json"
    ).read_text(encoding="utf-8")
)["cases"]


@pytest.mark.parametrize(
    "case", CASES, ids=lambda c: f"{c['sample']}-{c['source_record_index']}"
)
def test_full_preparation_answers_original_local_nl_question(case, monkeypatch):
    profile = SignalProcessingProfile(**case["profile"])
    full = owner.prepare_physical_signal(
        case["full_rt"], case["full_raw"], profile=profile
    )
    local = owner.scope_prepared_physical_signal(full, case["local_rt"])

    # Scope and evaluation consume preparation; neither may secretly fit again.
    def unexpected_prepare(*args, **kwargs):
        raise AssertionError("scoping/evaluation must not prepare local raw again")

    monkeypatch.setattr(owner, "prepare_physical_signal", unexpected_prepare)
    evaluated = owner.evaluate_physical_peak(
        local,
        query_rt_min=case["query_rt_min"],
        selection_policy_version=DISCOVERY_PHYSICAL_PEAK_POINT_POLICY,
    )
    start = case["full_rt"].index(case["local_rt"][0])
    stop = start + len(case["local_rt"])
    assert evaluated.prepared.rt == tuple(case["local_rt"])
    assert evaluated.prepared.raw == tuple(case["full_raw"][start:stop])
    assert (
        evaluated.retained_prepared_signal.smoothed
        == full.prepared.smoothed[start:stop]
    )
    assert evaluated.preparation_context == full.projection
    if case["source_record_index"] == 529:
        assert evaluated.answer.reason == "no_selection"
        assert evaluated.facts is None
        # NL exists; the missing physical area must not invent chemical absence.
        assert case["nl_receipts"][0]["scan_id"] == 28345
    else:
        facts = evaluated.facts
        assert facts is not None
        assert any(
            facts.left_rt_min <= r["support_rt"] <= facts.right_rt_min
            and facts.left_rt_min <= r["scan_rt"] <= facts.right_rt_min
            for r in case["nl_receipts"]
        )
        if case["source_record_index"] == 230:
            assert facts.apex_rt_min == pytest.approx(14.47968, abs=1e-5)
            assert facts.left_rt_min > 14.2  # Not the larger component at 14.14803.


@pytest.mark.parametrize(
    "case", CASES, ids=lambda c: f"{c['sample']}-{c['source_record_index']}"
)
def test_acquisition_uses_uniform_wider_preparation_without_widening_local_scope(case):
    raw = BoundedScanRaw(
        rt=np.array(case["full_rt"]), intensity=np.array(case["full_raw"])
    )
    capture = PhysicalPeakCapture(
        raw,
        acquisition_source=raw.acquisition_source,
        adapter_namespace="tests.background_local_scope",
        profile=SignalProcessingProfile(**case["profile"]),
    )
    lo, hi = case["local_request"]
    search = PhysicalPeakSearch(
        case["candidate_id"],
        case["query_rt_min"],
        XICRequest(case["xic_mz"], lo, hi, 20.0),
        True,
    )
    (result,) = capture.capture((search,))
    assert result.search.request == search.request
    assert result.evaluation.prepared.rt == tuple(case["local_rt"])
    assert result.preparation_acquired_trace.coordinate.request.rt_min == pytest.approx(
        max(0.0, lo - (hi - lo))
    )
    assert result.preparation_acquired_trace.coordinate.request.rt_max == pytest.approx(
        hi + (hi - lo)
    )
    assert result.evaluation.preparation_context.rt == tuple(case["full_rt"])
    assert (result.evaluation.facts is None) == (case["source_record_index"] == 529)


@pytest.mark.parametrize("case", EDGE_CASES, ids=lambda c: c["sample"])
def test_edge_settlement_uses_final_background_view_once(case):
    raw = BoundedScanRaw(
        rt=np.array(case["full_rt"]), intensity=np.array(case["full_raw"])
    )
    capture = PhysicalPeakCapture(
        raw,
        acquisition_source=raw.acquisition_source,
        adapter_namespace="tests.final_background_edge",
        profile=SignalProcessingProfile(**case["profile"]),
    )
    lo, hi = case["local_request"]
    (result,) = capture.capture(
        (
            PhysicalPeakSearch(
                case["candidate_id"],
                case["query_rt_min"],
                XICRequest(case["xic_mz"], lo, hi, 20.0),
                True,
            ),
        )
    )
    facts = result.evaluation.facts
    assert facts is not None
    assert facts.apex_rt_min == pytest.approx(
        case["local_raw_max_rt"], abs=float(np.max(np.diff(case["local_rt"])))
    )
    assert capture.reacquisition_request_count == 1
    assert capture.preparation_request_count == 1
    assert result.search.request.rt_min < lo
    assert result.search.request.rt_max > hi
    assert result.evaluation.preparation_context.rt == tuple(case["full_rt"])
    assert facts.left_rt_min < case["query_rt_min"] < facts.right_rt_min


def test_one_initial_path_repair_reaches_observed_same_component_closure():
    case = json.loads(
        (
            Path(__file__).parent / "fixtures/physical_background_initial_edge.json"
        ).read_text(encoding="utf-8")
    )
    raw = BoundedScanRaw(
        rt=np.array(case["full_rt"]), intensity=np.array(case["full_raw"])
    )
    capture = PhysicalPeakCapture(
        raw,
        acquisition_source=raw.acquisition_source,
        adapter_namespace="tests.component_extent",
        profile=SignalProcessingProfile(**case["profile"]),
    )
    lo, hi = case["initial_request"]
    (result,) = capture.capture(
        (
            PhysicalPeakSearch(
                case["candidate_id"],
                case["query_rt_min"],
                XICRequest(case["xic_mz"], lo, hi, 20.0),
                True,
            ),
        )
    )
    facts = result.evaluation.facts
    assert facts is not None
    assert facts.apex_rt_min == case["component_apex_rt"]
    assert facts.left_rt_min < lo - 0.6  # The observed closure needs more than .6.
    assert facts.left_rt_min < case["parent_rt"] < case["nl_rt"] < facts.right_rt_min
    assert capture.reacquisition_request_count == capture.preparation_request_count == 1


def test_edge_extent_cannot_borrow_a_different_full_context_apex():
    case = next(c for c in CASES if c["source_record_index"] == 230)
    full = owner.prepare_physical_signal(
        case["full_rt"],
        case["full_raw"],
        profile=SignalProcessingProfile(**case["profile"]),
    )
    rt = np.array(case["full_rt"])
    local = owner.scope_prepared_physical_signal(
        full, rt[(rt >= 14.35) & (rt <= 14.65)]
    )
    evaluation = owner.evaluate_physical_peak(
        local,
        query_rt_min=case["query_rt_min"],
        selection_policy_version=DISCOVERY_PHYSICAL_PEAK_POINT_POLICY,
    )
    assert evaluation.reason == "incomplete_peak_context"
    assert evaluation.question.admissible_segments[0].apex_rt_min == pytest.approx(
        14.47968, abs=1e-5
    )
    universe = owner.enumerate_physical_segment_universe(
        full,
        selection_policy_version=DISCOVERY_PHYSICAL_PEAK_POINT_POLICY,
    )
    assert universe.segments[0].apex_rt_min == pytest.approx(14.14803, abs=1e-5)
    assert owner.physical_component_repair_bounds(evaluation, full, universe) is None
