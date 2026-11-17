"""Batch RT drift: anchor channels, frozen B and M, and two-coefficient groups."""

from __future__ import annotations

import math
from dataclasses import replace
from itertools import combinations
from statistics import median
from types import SimpleNamespace

import pytest

from tests.membership_fixtures import cid_nl_owner
from xic_extractor.alignment.cid_nl_grouping import CID_NL_GROUPING_RULES
from xic_extractor.alignment.cid_nl_initial_grouping import rt_anchor_observations
from xic_extractor.alignment.config import AlignmentConfig
from xic_extractor.alignment.feature_membership_inference import (
    infer_feature_memberships,
)
from xic_extractor.alignment.membership_selection import (
    MembershipComparison,
    _GroupInputs,
)
from xic_extractor.alignment.peak_trajectories import PeakTrajectories
from xic_extractor.alignment.rt_drift import (
    BatchRtModelAccumulator,
    DriftAnchor,
    DriftCoefficients,
    RtAnchorObservation,
    RtDriftModel,
    _drift_model,
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


def test_settled_model_judges_a_moved_owner_instead_of_its_refit():
    # Each refit after one owner leaves perturbs the coefficients; with this
    # jitter, removing an interior owner raised the group's own refit cost.
    jitter = (0.1, 0.08, -0.02, -0.07, 0.0, -0.03, 0.09, -0.06, -0.01, 0.03, 0.12, 0.0)
    owners = tuple(
        cid_nl_owner(sample, "drift", apex_rt=round(10.0 + 1.1 * SHIFT[sample] + j, 3))
        for sample, j in zip(SAMPLES, jitter, strict=True)
    )
    comparison = MembershipComparison(
        owners,
        rules=CID_NL_GROUPING_RULES,
        config=AlignmentConfig(),
        rt_frame=_frame(_model()),
        trajectories=PeakTrajectories(),
    )
    group = tuple(sorted(owner.owner_id for owner in owners))
    comparison.settle((group,))
    full = comparison.group_cost(group)
    for owner_id in group:
        remainder = tuple(p for p in group if p != owner_id)
        assert comparison.group_cost(remainder) <= full


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


@pytest.mark.parametrize("missing", (False, True))
def test_residual_fits_preserve_the_pairwise_median_slope(missing):
    """Independent scalar Theil-Sen oracle, including tied and missing regressors."""
    points = [
        (
            10.0 + 0.8 * (index // 2) + 1.1 * shift + (index % 3) * 0.01,
            None if missing and index == 3 else float(index // 2),
            None if missing and index == 7 else shift,
        )
        for index, shift in enumerate(SHIFT.values())
    ]

    def slope(xs, ys):
        return median(
            (ys[j] - ys[i]) / (xs[j] - xs[i])
            for i, j in combinations(range(len(xs)), 2)
            if xs[i] != xs[j]
        )

    with_b = [(y, b) for y, b, _ in points if b is not None]
    with_m = [(y, m) for y, _, m in points if m is not None]
    both = [(y, b, m) for y, b, m in points if b is not None and m is not None]
    ys, bs, ms = zip(*both, strict=True)
    a, c = slope(bs, ys), 0.0
    for _ in range(20):
        next_c = slope(ms, [y - a * b for y, b in zip(ys, bs, strict=True)])
        next_a = slope(bs, [y - next_c * m for y, m in zip(ys, ms, strict=True)])
        converged = abs(next_a - a) < 1e-9 and abs(next_c - c) < 1e-9
        a, c = next_a, next_c
        if converged:
            break
    expected = (
        DriftCoefficients(time=slope([b for _, b in with_b], [y for y, _ in with_b])),
        DriftCoefficients(
            sample_shift=slope([m for _, m in with_m], [y for y, _ in with_m])
        ),
        DriftCoefficients(time=a, sample_shift=c),
    )
    assert candidate_coefficients(points) == expected


def test_proposed_groups_reuse_exact_pair_costs_when_the_comparison_grows():
    """Vectorized proposals agree with scalar pair evaluation, even after caching.

    The first proposal prepares numeric pair evidence before the later owners
    have been compared. Subsequent proposals exercise newly filled cache rows,
    overlapping subsets, and a member with an unknown drift regressor.
    """
    owners = tuple(
        cid_nl_owner(sample, "drift", apex_rt=10.0 + 1.1 * SHIFT[sample])
        for sample in SAMPLES
    )
    frame = _frame(_model({s: v for s, v in SHIFT.items() if s != "s09"}))

    def comparison():
        return MembershipComparison(
            owners,
            rules=CID_NL_GROUPING_RULES,
            config=AlignmentConfig(),
            rt_frame=frame,
            trajectories=PeakTrajectories(),
        )

    actual, scalar = comparison(), comparison()
    for subset in (owners[:6], owners[6:], owners[::2], owners[1::2], owners):
        key = tuple(sorted(o.owner_id for o in subset))
        models = [scalar._evaluate(key, DriftCoefficients())]
        models.extend(
            scalar._evaluate(key, coefficients)
            for coefficients in scalar._candidate_coefficients(key)
        )
        feasible = [model for model in models if not model.population_gap]
        expected = min(
            feasible,
            key=lambda model: (model.evidence, model.coefficients.nonzero_count),
        )
        assert math.isfinite(expected.evidence)
        assert actual.group_cost(key) == expected.evidence
        assert actual.coefficients(key) == expected.coefficients
        # A self-comparison remains a hard conflict, not a packed off-diagonal
        # entry that could poison a later valid group's cost.
        assert actual.pair(owners[2].owner_id, owners[2].owner_id)[0] == math.inf


def test_anchor_without_shift_response_keeps_samples_whose_shift_is_unknown():
    # M comes from two synchronized families observed in s00-s10; the flat
    # channel also has s11, where M is unknown and its zero response needs none.
    measured = SAMPLES[:-1]
    calibrated = {
        "a": {s: 10.0 + SHIFT[s] for s in measured},
        "b": {s: 20.0 + SHIFT[s] for s in measured},
        "flat": dict.fromkeys(SAMPLES, 30.0),
    }
    model = _drift_model(
        calibrated,
        injection_order=ORDERS,
        qc_samples=frozenset(),
        cohorts={},
        families={"a": 0, "b": 1, "flat": 2},
        owner_ids={},
    )
    assert "s11" not in model.sample_shift_min
    flat = next(anchor for anchor in model.anchors if anchor.channel == "flat")
    assert set(flat.shape) == set(SAMPLES)


def test_several_supported_peaks_leave_that_channel_sample_unknown():
    accumulator = BatchRtModelAccumulator(
        sample_cohorts=dict.fromkeys(SAMPLES, ""), injection_order=ORDERS
    )
    # Two drifting channels in different elution families follow SHIFT.
    accumulator.add_observations(
        RtAnchorObservation(
            f"c{channel}", sample, f"{sample}:{channel}", 10.0 * (channel + 1) + shift
        )
        for channel in range(2)
        for sample, shift in SHIFT.items()
    )
    accumulator.add_observations(
        (RtAnchorObservation("c0", "s00", "s00:second", 10.5),)
    )
    identity = BatchRtCalibration.from_coordinate_knots(
        tuple((sample, ((0.0, 0.0), (60.0, 60.0))) for sample in SAMPLES)
    )
    owners = {
        anchor.channel: anchor.owner_ids
        for anchor in accumulator.freeze(identity).anchors
    }
    assert "s00:0" not in owners["c0"] and "s01:0" in owners["c0"]
    assert "s00:1" in owners["c1"]


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


@pytest.mark.parametrize("query_rt", (0.0, 2.0, 4.0, 6.0, 25.0))
def test_anchor_lookup_preserves_the_closed_displacement_window(query_rt):
    references = [
        reference
        for edge in (query_rt - 2.0, query_rt + 2.0)
        for reference in (
            math.nextafter(edge, -math.inf),
            edge,
            math.nextafter(edge, math.inf),
        )
        if reference >= 0.0
    ]
    anchors = tuple(
        DriftAnchor(
            f"boundary-{index}",
            reference,
            index,
            True,
            {"left": 0.0, "right": 1.0},
            DriftCoefficients(time=(index + 1) / 10.0),
        )
        for index, reference in enumerate(references)
    )
    # The scientific window is evaluated by the original subtraction, not by
    # separately rounded query +/- window bounds.
    expected = tuple(
        sorted(
            anchor.coefficients.time
            for anchor in anchors
            if abs(anchor.reference_rt_min - query_rt) <= 2.0
        )
    )
    lookup = RtDriftModel(anchors).displacement_lookup()
    assert lookup(query_rt, "left", "right") == expected


def test_backfill_tie_break_scores_extensions_in_the_original_model():
    from itertools import combinations

    from xic_extractor.alignment.rt_drift import DriftCoefficients

    owners = tuple(
        cid_nl_owner(sample, "drift", apex_rt=10.0 + 1.1 * SHIFT[sample])
        for sample in SAMPLES[:10]
    )
    extra = cid_nl_owner("s10", "late", apex_rt=10.0 + 1.1 * SHIFT["s10"] + 0.4)
    original = tuple(sorted(owner.owner_id for owner in owners))
    fixed = DriftCoefficients(sample_shift=1.1)
    comparison = MembershipComparison(
        (*owners, extra),
        rules=CID_NL_GROUPING_RULES,
        config=AlignmentConfig(),
        rt_frame=_frame(_model()),
        trajectories=PeakTrajectories(),
        fixed_coefficients={original: fixed},
    )
    extended = tuple(sorted((*original, extra.owner_id)))
    in_original_model = [
        comparison.fixed_pair(a, b, original)[0] for a, b in combinations(extended, 2)
    ]
    expected = (len(extended) * sum(in_original_model) / len(in_original_model)) / len(
        comparison.owners
    )
    assert comparison.fixed_peer_fit(((extended, original),)) == pytest.approx(expected)
    # The same extension scored as a free group would refit its own model.
    assert comparison.coefficients(extended) != fixed
    assert comparison.fixed_group_model(extended, original).coefficients == fixed


def test_borrowed_batch_state_is_shared_only_by_queries_without_an_own_anchor():
    rising = {sample: index / 11 for index, sample in enumerate(SAMPLES)}
    falling = {sample: 1 - value for sample, value in rising.items()}
    model = RtDriftModel(
        (
            DriftAnchor(
                "early", 10.0, 0, True, rising, owner_ids=frozenset({"own-early"})
            ),
            DriftAnchor(
                "late", 13.0, 1, True, falling, owner_ids=frozenset({"own-late"})
            ),
        )
    )
    lookup = model.batch_state_lookup()
    borrowed = dict(lookup(("a",), 11.0))
    # Both anchors contribute; the blend is neither anchor's shape.
    assert borrowed not in (rising, falling)
    # A group sharing an anchor's owner keeps that anchor's shape at an RT
    # already answered by a borrowed shape, in either query order.
    assert lookup(("own-late", "b"), 11.0) == falling
    assert lookup(("b", "c"), 11.0) == borrowed
    reversed_lookup = model.batch_state_lookup()
    assert reversed_lookup(("own-early",), 11.0) == rising
    assert reversed_lookup(("d",), 11.0) == borrowed
    # Another frozen model at the same RT borrows its own anchors' shapes.
    late_only = RtDriftModel((DriftAnchor("late", 13.0, 1, True, falling),))
    other = late_only.batch_state_lookup()(("a",), 11.0)
    assert other != borrowed
    assert other == late_only.batch_state(("e",), 11.0)
    assert lookup(("a",), 11.0) == borrowed


@pytest.mark.parametrize("missing", (False, True))
def test_drift_models_rejected_by_one_pair_match_full_scoring(missing):
    """Early rejection keeps every model's value, before and after settling.

    The reference scores each model without the zero model's worst pair. Some
    models are rejected by that pair alone, some only by another pair, and
    some survive; a population-gap group has no finite bound to reject by.
    """
    jitter = (0.1, 0.08, -0.02, -0.07, 0.0, -0.03, 0.09, -0.06, -0.01, 0.03, 0.12, 0.0)
    noise = (
        0.05,
        -0.04,
        0.03,
        0.08,
        -0.07,
        0.02,
        -0.01,
        0.06,
        -0.05,
        0.04,
        -0.08,
        0.01,
    )
    owners = (
        *(
            cid_nl_owner(s, "drift", apex_rt=round(10.0 + 1.1 * SHIFT[s] + j, 3))
            for s, j in zip(SAMPLES, jitter, strict=True)
        ),
        *(
            cid_nl_owner(s, "flat", apex_rt=10.2 + (0.01 if i % 2 else 0.0))
            for i, s in enumerate(SAMPLES)
        ),
        *(
            cid_nl_owner(s, "noisy", apex_rt=round(12.0 + n + 0.1 * SHIFT[s], 3))
            for s, n in zip(SAMPLES, noise, strict=True)
        ),
        *(
            cid_nl_owner(s, "pop", apex_rt=13.0 if i % 2 else 13.8)
            for i, s in enumerate(SAMPLES)
        ),
    )
    shift = {s: v for s, v in SHIFT.items() if not (missing and s in ("s03", "s07"))}
    frame = _frame(_model(shift))

    def comparison():
        return MembershipComparison(
            owners,
            rules=CID_NL_GROUPING_RULES,
            config=AlignmentConfig(),
            rt_frame=frame,
            trajectories=PeakTrajectories(),
        )

    actual, reference = comparison(), comparison()
    by_name: dict[str, list[str]] = {}
    for owner in owners:
        by_name.setdefault(owner.owner_id.split("-")[2], []).append(owner.owner_id)
    drift, flat, noisy, pop = (
        tuple(sorted(by_name[n])) for n in ("drift", "flat", "noisy", "pop")
    )
    groups = [
        tuple(sorted(group))
        for group in (
            drift,
            flat,
            noisy,
            pop,
            drift[:6],
            drift[::2],
            noisy[:6],
            noisy[2:],
            noisy[1::2] + noisy[:1],
            drift[:7] + flat[7:],
            drift[:9] + flat[9:],
            drift[:11] + flat[11:],
        )
    ]
    outcomes = set()

    def expected_models(key):
        zero = reference._evaluate(key, DriftCoefficients())
        models = [zero]
        if not math.isfinite(zero.evidence):
            return tuple(models)
        bound = math.inf if zero.population_gap else zero.evidence
        worst = max(combinations(key, 2), key=lambda pair: reference.pair(*pair)[0])
        for coefficients in reference._candidate_coefficients(key):
            model = reference._evaluate(key, coefficients, bound=bound)
            models.append(model)
            if math.isinf(bound):
                outcomes.add("no_bound")
                continue
            offsets, unknown = reference._frame(
                reference._sample_offsets(coefficients, key), key
            )
            witness = reference._compose(*worst, offsets, unknown)[0]
            penalty = reference._coefficient_cost * coefficients.nonzero_count
            if 1.0 + witness + penalty >= bound:
                outcomes.add("rejected_by_worst_zero_pair")
            elif math.isinf(model.evidence):
                outcomes.add("rejected_by_another_pair")
            else:
                outcomes.add("kept")
        return tuple(models)

    for settled in ((), (drift, flat, noisy)):
        actual.settle(settled)
        reference.settle(settled)
        for key in groups:
            assert actual._group_models(key) == expected_models(key)
    expected_outcomes = {"rejected_by_worst_zero_pair", "kept", "no_bound"}
    if not missing:
        expected_outcomes.add("rejected_by_another_pair")
    assert outcomes >= expected_outcomes


def test_one_pair_rejection_bound_is_inclusive_like_full_scoring():
    owners = tuple(
        cid_nl_owner(s, "drift", apex_rt=10.0 + 1.1 * SHIFT[s]) for s in SAMPLES
    )
    comparison = MembershipComparison(
        owners,
        rules=CID_NL_GROUPING_RULES,
        config=AlignmentConfig(),
        rt_frame=_frame(_model()),
        trajectories=PeakTrajectories(),
    )
    key = tuple(sorted(owner.owner_id for owner in owners))
    zero = comparison._evaluate(key, DriftCoefficients())
    (coefficients,) = comparison._candidate_coefficients(key)
    offsets, unknown = comparison._frame(
        comparison._sample_offsets(coefficients, key), key
    )
    worst_zero_pair = max(
        combinations(key, 2), key=lambda pair: comparison.pair(*pair)[0]
    )
    witness = comparison._compose(*worst_zero_pair, offsets, unknown)[0]
    at_witness = 1.0 + witness + comparison._coefficient_cost
    assert at_witness < zero.evidence
    for bound in (at_witness, math.nextafter(at_witness, math.inf)):
        full = comparison._evaluate(key, coefficients, bound=bound)
        inputs = _GroupInputs(comparison, key)
        assert comparison._evaluate(key, DriftCoefficients(), inputs=inputs) == zero
        assert inputs.witness == worst_zero_pair
        early = comparison._evaluate(key, coefficients, bound=bound, inputs=inputs)
        assert early == full
    assert math.isinf(
        comparison._evaluate(key, coefficients, bound=at_witness).evidence
    )
