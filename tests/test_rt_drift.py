"""Batch RT drift: anchor channels, frozen B and M, and two-coefficient groups."""

from __future__ import annotations

import math
from itertools import combinations
from statistics import median

import pytest

from tests.membership_fixtures import cid_nl_owner
from xic_extractor.alignment.cid_nl_grouping import CID_NL_GROUPING_RULES
from xic_extractor.alignment.config import AlignmentConfig
from xic_extractor.alignment.feature_membership_inference import (
    infer_feature_memberships,
)
from xic_extractor.alignment.membership_selection import (
    MembershipComparison,
    _GroupInputs,
)
from xic_extractor.alignment.peak_trajectories import PeakTrajectories
from xic_extractor.alignment.rt_anchors import RtAnchorObservation
from xic_extractor.alignment.rt_drift import (
    BatchRtModelAccumulator,
    DriftAnchor,
    DriftCoefficients,
    RtDriftModel,
    _drift_model,
    _row_voted_shift,
    candidate_coefficients,
)
from xic_extractor.alignment.rt_frame import SampleRtFrame
from xic_extractor.alignment.sample_rt_calibration import BatchRtCalibration

SAMPLES = tuple(f"s{i:02}" for i in range(12))
ORDERS = {sample: index + 1 for index, sample in enumerate(SAMPLES)}
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
    anchor = DriftAnchor("anchor", 30.0, 0, FLAT)
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


@pytest.mark.parametrize(("displaced", "feasible"), ((0.0, True), (2.5, False)))
def test_neighbor_jump_left_after_removing_sample_shift_breaks_the_group(
    displaced, feasible
):
    """Raw neighbours s09 -> s10 differ by 2.8 min, all explained by b*M (b=2).

    Displacing s05 leaves a 2.5 min jump that no shift response explains.
    """
    owners = tuple(
        cid_nl_owner(
            sample,
            "drift",
            apex_rt=10.0 + 2.0 * SHIFT[sample] + (displaced if sample == "s05" else 0),
        )
        for sample in SAMPLES
    )
    comparison = MembershipComparison(
        owners,
        rules=CID_NL_GROUPING_RULES,
        config=AlignmentConfig(),
        rt_frame=_frame(_model()),
        trajectories=PeakTrajectories(),
    )
    group = tuple(sorted(owner.owner_id for owner in owners))
    assert math.isfinite(comparison.group_cost(group)) is feasible


@pytest.mark.parametrize(("s05_rt", "feasible"), ((11.8, True), (13.8, False)))
def test_backfill_extension_obeys_the_neighbor_jump_limit(s05_rt, feasible):
    """s05 joins a group rising 0.3 min per injection as a Backfill extension.

    At 13.8 min it stays inside the group's RT range, so no RT-population gap
    rejects it, but it jumps 2.3 min from its injection-order neighbours.
    """
    owners = tuple(
        cid_nl_owner(
            sample,
            "trend",
            apex_rt=s05_rt if sample == "s05" else round(10.3 + 0.3 * index, 2),
        )
        for index, sample in enumerate(SAMPLES)
    )
    comparison = MembershipComparison(
        owners,
        rules=CID_NL_GROUPING_RULES,
        config=AlignmentConfig(),
        rt_frame=_frame(_model(FLAT)),
        trajectories=PeakTrajectories(),
    )
    group = tuple(sorted(owner.owner_id for owner in owners))
    original = tuple(p for p in group if p != "OWN-s05-trend")
    assert math.isfinite(comparison.fixed_group_cost(group, original)) is feasible


def test_settled_model_judges_a_moved_owner_instead_of_its_refit():
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
        assert actual.pair(owners[2].owner_id, owners[2].owner_id)[0] == math.inf


def test_anchor_without_shift_response_keeps_samples_whose_shift_is_unknown():
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

    def anchor(channel, sample, owner_id, rt_min):
        return RtAnchorObservation(channel, 500.0, None, sample, owner_id, rt_min, None)

    accumulator.add_observations(
        anchor(
            f"c{channel}", sample, f"{sample}:{channel}", 10.0 * (channel + 1) + shift
        )
        for channel in range(2)
        for sample, shift in SHIFT.items()
    )
    accumulator.add_observations((anchor("c0", "s00", "s00:second", 10.5),))
    identity = BatchRtCalibration.from_coordinate_knots(
        tuple((sample, ((0.0, 0.0), (60.0, 60.0))) for sample in SAMPLES)
    )
    owners = {
        anchor.channel: anchor.owner_ids
        for anchor in accumulator.freeze(identity).anchors
    }
    assert "s00:0" not in owners["c0"] and "s01:0" in owners["c0"]
    assert "s00:1" in owners["c1"]


def test_anchor_displacement_lets_a_drifted_main_peak_match_its_main_peak():
    from xic_extractor.alignment.peak_trajectories import _match_patterns
    from xic_extractor.peak_detection.elution_context import ElutionLandmark

    left = [ElutionLandmark(10.0, 100.0, 0.2), ElutionLandmark(10.6, 10.0, 0.2)]
    right = [ElutionLandmark(10.6, 100.0, 0.2), ElutionLandmark(11.2, 10.0, 0.2)]
    assert _match_patterns(left, right, max_shift=0.0) is None
    match = _match_patterns(left, right, max_shift=0.0, shift_hypotheses=(0.6,))
    assert match is not None and match.pairs == ((0, 0), (1, 1))
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
            {"left": 0.0, "right": 1.0},
            DriftCoefficients(time=(index + 1) / 10.0),
        )
        for index, reference in enumerate(references)
    )
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
    assert comparison.coefficients(extended) != fixed
    assert comparison.fixed_group_model(extended, original).coefficients == fixed


def test_borrowed_batch_state_is_shared_only_by_queries_without_an_own_anchor():
    rising = {sample: index / 11 for index, sample in enumerate(SAMPLES)}
    falling = {sample: 1 - value for sample, value in rising.items()}
    model = RtDriftModel(
        (
            DriftAnchor("early", 10.0, 0, rising, owner_ids=frozenset({"own-early"})),
            DriftAnchor("late", 13.0, 1, falling, owner_ids=frozenset({"own-late"})),
        )
    )
    lookup = model.batch_state_lookup()
    borrowed = dict(lookup(("a",), 11.0))
    assert borrowed not in (rising, falling)
    assert lookup(("own-late", "b"), 11.0) == falling
    assert lookup(("b", "c"), 11.0) == borrowed
    reversed_lookup = model.batch_state_lookup()
    assert reversed_lookup(("own-early",), 11.0) == rising
    assert reversed_lookup(("d",), 11.0) == borrowed
    late_only = RtDriftModel((DriftAnchor("late", 13.0, 1, falling),))
    other = late_only.batch_state_lookup()(("a",), 11.0)
    assert other != borrowed
    assert other == late_only.batch_state(("e",), 11.0)
    assert lookup(("a",), 11.0) == borrowed


@pytest.mark.parametrize("missing", (False, True))
def test_group_decisions_match_full_drift_scoring(missing):
    """Compare both group objectives and the selected fit with all models scored."""
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
    for settled in ((), (drift, flat, noisy)):
        actual.settle(settled)
        reference.settle(settled)
        for key in groups:
            models = [
                reference._evaluate(key, coefficients)
                for coefficients in (
                    DriftCoefficients(),
                    *reference._candidate_coefficients(key),
                )
                if not reference._breaks_sequence(key, coefficients.sample_shift)
            ]
            assert actual._group_evidence_cost(key) == min(
                (model.evidence for model in models), default=math.inf
            )
            feasible = [
                m for m in models if not m.population_gap and math.isfinite(m.evidence)
            ]
            best = min(
                feasible,
                key=lambda m: (m.evidence, m.coefficients.nonzero_count),
                default=None,
            )
            assert actual.group_cost(key) == (best.evidence if best else math.inf)
            assert actual.coefficients(key) == (
                best.coefficients if best else DriftCoefficients()
            )


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


_ROW_SAMPLES = [f"S{index:02d}" for index in range(30)]
_ROW_ORDER = {sample: index for index, sample in enumerate(_ROW_SAMPLES)}
_TRUE_SHIFT = {
    sample: math.sin(1.7 * index) + 0.4 * math.cos(0.9 * index)
    for index, sample in enumerate(_ROW_SAMPLES)
}


def _shift_rows(responses):
    return [
        {
            sample: 10.0 + index + response * _TRUE_SHIFT[sample]
            for sample in _ROW_SAMPLES
        }
        for index, response in enumerate(responses)
    ]


def test_rows_correct_a_sample_shift_the_anchors_got_wrong():
    anchors = {**_TRUE_SHIFT, "S12": _TRUE_SHIFT["S12"] + 1.5}
    rows = _shift_rows((0.5, 0.8, 1.0, 1.2, 1.5))

    voted = _row_voted_shift(anchors, rows, _ROW_ORDER)

    assert voted == pytest.approx(_TRUE_SHIFT, abs=0.02)
    assert _row_voted_shift(anchors, rows[::-1], _ROW_ORDER) == voted


@pytest.mark.parametrize(
    "rows",
    [
        pytest.param(_shift_rows((0.8, 1.2)), id="fewer rows than votes needed"),
        pytest.param(
            [
                {s: rt for s, rt in row.items() if _ROW_ORDER[s] < 15}
                for row in _shift_rows((0.5, 0.8, 1.0, 1.2, 1.5))
            ],
            id="rows observed in too few samples",
        ),
        pytest.param(
            [
                {
                    s: 10.0 + 0.05 * math.sin(3.1 * i + k)
                    for i, s in enumerate(_ROW_SAMPLES)
                }
                for k in range(5)
            ],
            id="rows that do not follow the shift",
        ),
    ],
)
def test_sample_shift_stays_with_the_anchors_without_enough_following_rows(rows):
    wrong = {**_TRUE_SHIFT, "S12": _TRUE_SHIFT["S12"] + 1.5}

    assert _row_voted_shift(wrong, rows, _ROW_ORDER) == wrong
