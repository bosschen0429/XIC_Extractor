"""Batch RT drift: anchor channels, frozen B and M, and two-coefficient groups."""

from __future__ import annotations

from dataclasses import replace
from types import SimpleNamespace

import pytest

from tests.membership_fixtures import cid_nl_owner
from xic_extractor.alignment.cid_nl_grouping import CID_NL_GROUPING_RULES
from xic_extractor.alignment.cid_nl_initial_grouping import rt_anchor_observations
from xic_extractor.alignment.config import AlignmentConfig
from xic_extractor.alignment.feature_membership_inference import (
    infer_feature_memberships,
)
from xic_extractor.alignment.membership_selection import MembershipComparison
from xic_extractor.alignment.peak_trajectories import PeakTrajectories
from xic_extractor.alignment.rt_drift import (
    BatchRtModelAccumulator,
    DriftAnchor,
    DriftCoefficients,
    RtAnchorObservation,
    RtDriftModel,
    candidate_coefficients,
)
from xic_extractor.alignment.rt_frame import SampleRtFrame
from xic_extractor.alignment.sample_rt_calibration import BatchRtCalibration

SAMPLES = tuple(f"s{i:02}" for i in range(12))
ORDERS = {sample: index + 1 for index, sample in enumerate(SAMPLES)}
# Per-sample shift (minutes): not monotone in injection order.
SHIFT = dict(
    zip(
        SAMPLES,
        (0.0, 0.6, -0.4, 0.9, -0.8, 0.3, -0.2, 0.8, -0.6, 0.5, -0.9, 0.2),
        strict=True,
    )
)
FLAT = dict.fromkeys(SAMPLES, 0.0)


def _frame(model: RtDriftModel) -> SampleRtFrame:
    identity = BatchRtCalibration.from_coordinate_knots(
        tuple((sample, ((0.0, 0.0), (60.0, 60.0))) for sample in SAMPLES)
    )
    return SampleRtFrame(
        injection_order=ORDERS,
        sample_cohorts=dict.fromkeys(SAMPLES, ""),
        sample_universe=frozenset(SAMPLES),
    ).with_sample_calibration(identity, model)


def _model(shift=SHIFT) -> RtDriftModel:
    anchor = DriftAnchor("anchor", 30.0, 0, True, FLAT)
    return RtDriftModel((anchor,), dict(shift), "samples")


def _groups(owners, model=None):
    result = infer_feature_memberships(
        owners,
        rules=CID_NL_GROUPING_RULES,
        config=AlignmentConfig(),
        rt_frame=_frame(model or _model()),
    )
    return {
        frozenset(owner_id.split("-")[1] for owner_id in group.owner_ids)
        for group in result.groups
    }, result


def test_compound_following_sample_shift_forms_one_group():
    owners = tuple(
        cid_nl_owner(sample, "drift", apex_rt=10.0 + 1.1 * SHIFT[sample])
        for sample in SAMPLES
    )
    groups, result = _groups(owners)
    assert groups == {frozenset(SAMPLES)}
    (group,) = result.groups
    assert group.rt_drift.sample_shift == pytest.approx(1.1)


@pytest.mark.parametrize("segment_shift", (0.0, 0.8))
def test_complementary_isomers_that_do_not_drift_stay_separate(segment_shift):
    # Each isomer occupies one injection segment. Segment-level shift is the
    # tissue confound: the shift jitter inside each segment still differs.
    shift = {
        sample: value + (segment_shift if index >= 6 else 0.0)
        for index, (sample, value) in enumerate(SHIFT.items())
    }
    owners = tuple(
        cid_nl_owner(sample, "iso", apex_rt=10.0 if index < 6 else 12.0)
        for index, sample in enumerate(SAMPLES)
    )
    groups, _ = _groups(owners, _model(shift))
    assert groups == {frozenset(SAMPLES[:6]), frozenset(SAMPLES[6:])}


def test_unknown_sample_shift_affects_only_that_member():
    shift = {s: v for s, v in SHIFT.items() if s not in ("s03", "s07")}
    owners = tuple(
        cid_nl_owner(sample, "drift", apex_rt=10.0 + 1.1 * SHIFT[sample])
        for sample in SAMPLES
    )
    comparison = MembershipComparison(
        owners,
        rules=CID_NL_GROUPING_RULES,
        config=AlignmentConfig(),
        rt_frame=_frame(_model(shift)),
        trajectories=PeakTrajectories(),
    )
    group = tuple(sorted(owner.owner_id for owner in owners))
    assert comparison.coefficients(group).sample_shift == pytest.approx(1.1)


def test_membership_does_not_depend_on_owner_ids_or_input_order():
    owners = tuple(
        cid_nl_owner(sample, "drift", apex_rt=10.0 + 1.1 * SHIFT[sample])
        for sample in SAMPLES
    ) + tuple(
        cid_nl_owner(sample, "flat", apex_rt=13.0 + (0.01 if index % 2 else 0.0))
        for index, sample in enumerate(SAMPLES)
    )
    forward, _ = _groups(owners)
    renamed = tuple(
        cid_nl_owner(
            owner.sample_stem,
            "drift" if "drift" in owner.owner_id else "flat",
            apex_rt=owner.peak.integration.rt_apex_min,
        )
        for owner in reversed(owners)
    )
    backward, _ = _groups(renamed)
    assert forward == backward


def test_coefficients_need_enough_members_and_regressor_spread():
    few = [(10.0 + v, 0.0, v) for v in (0.0, 0.5, 1.0)]
    assert candidate_coefficients(few) == ()
    flat = [(10.0, 0.0, 0.0)] * 8
    assert candidate_coefficients(flat) == ()
    fitted = candidate_coefficients([(10.0 + 2 * v, None, v) for v in SHIFT.values()])
    assert fitted == (DriftCoefficients(sample_shift=pytest.approx(2.0)),)


def test_several_supported_peaks_leave_that_channel_sample_unknown():
    accumulator = BatchRtModelAccumulator(
        sample_stems=SAMPLES,
        sample_cohorts=dict.fromkeys(SAMPLES, ""),
        injection_order=ORDERS,
    )
    accumulator.add_observations(
        RtAnchorObservation(f"c{channel}", sample, f"{sample}:{channel}", 8.0 + channel)
        for channel in range(10)
        for sample in SAMPLES
    )
    accumulator.add_observations((RtAnchorObservation("c0", "s00", "s00:second", 8.5),))
    calibration, _ = accumulator.freeze()
    assert calibration.samples["s00"].anchor_count == 9
    assert calibration.samples["s01"].anchor_count == 10


def test_anchor_observations_need_direct_quantifiable_support():
    owners = [cid_nl_owner(s, "x", apex_rt=10.0) for s in ("a", "b", "c")]
    owners[1] = replace(owners[1], quantification_eligible=False)
    scope = SimpleNamespace(groups=(SimpleNamespace(owners=tuple(owners)),))
    direct = SimpleNamespace(
        method_subject=SimpleNamespace(method_subject_id="precursor|500")
    )
    candidates = (
        SimpleNamespace(
            peak_hypothesis_id=owners[0].owner_id, direct_support_assessment=direct
        ),
        SimpleNamespace(
            peak_hypothesis_id=owners[1].owner_id, direct_support_assessment=direct
        ),
        SimpleNamespace(
            peak_hypothesis_id=owners[2].owner_id, direct_support_assessment=None
        ),
    )
    observations = rt_anchor_observations(scope, candidates)
    assert [o.owner_id for o in observations] == [owners[0].owner_id]
    assert observations[0].channel == "precursor|500"


def test_anchor_displacement_lets_a_drifted_main_peak_match_its_main_peak():
    from xic_extractor.alignment.peak_trajectories import _match_patterns
    from xic_extractor.peak_detection.elution_context import ElutionLandmark

    left = [ElutionLandmark(10.0, 100.0, 0.2), ElutionLandmark(10.6, 10.0, 0.2)]
    right = [ElutionLandmark(10.6, 100.0, 0.2), ElutionLandmark(11.2, 10.0, 0.2)]
    # Without drift the main peak can only meet the minor peak: one pair.
    assert _match_patterns(left, right, max_shift=0.0) is None
    match = _match_patterns(left, right, max_shift=0.0, shift_hypotheses=(0.6,))
    assert match is not None and match.pairs == ((0, 0), (1, 1))
    # A complete undrifted match keeps winning over a partial drifted one.
    still = _match_patterns(left, left, max_shift=0.0, shift_hypotheses=(0.6,))
    assert still is not None and still.pairs == ((0, 0), (1, 1))
