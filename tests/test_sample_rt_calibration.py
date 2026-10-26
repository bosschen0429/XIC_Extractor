import csv
import json
from pathlib import Path

import numpy as np
import pytest

from tools.diagnostics.build_sample_rt_calibration import main
from xic_extractor.alignment.rt_frame import SampleRtFrame
from xic_extractor.alignment.sample_rt_calibration import (
    PriorInjection,
    RtCalibrationPolicy,
    build_batch_rt_calibration,
)

SAMPLES = tuple(f"S{i:02d}" for i in range(1, 11))
REFERENCE_RTS = (8.0, 10.0, 12.0, 14.0, 16.0, 18.0, 20.0, 22.0, 24.0, 28.0, 30.0, 32.0)


def _batch(
    shifts: dict[str, tuple[float, ...]] | None = None,
) -> dict[str, dict[str, float]]:
    """Features at REFERENCE_RTS; each sample shifts every knot independently."""
    shifts = shifts or {}
    detected: dict[str, dict[str, float]] = {}
    for index, reference in enumerate(REFERENCE_RTS):
        detected[f"F{index}"] = {
            sample: reference
            + shifts.get(sample, (0.0,) * len(REFERENCE_RTS))[index]
            + 0.001 * n
            for n, sample in enumerate(SAMPLES)
        }
    return detected


def _calibrate(detected, priors=None, **policy):
    return build_batch_rt_calibration(
        detected,
        sample_stems=SAMPLES,
        prior_injections=priors or {},
        policy=RtCalibrationPolicy(**policy),
    )


@pytest.mark.parametrize("positions", (2, len(REFERENCE_RTS)))
def test_repeated_measured_patterns_do_not_add_anchor_votes(positions) -> None:
    detected = dict(list(_batch().items())[:positions])
    expected = _calibrate(detected)
    for i in range(20):
        detected[f"F1_alias_{i}"] = dict(detected["F1"])
    calibration = _calibrate(detected)
    assert calibration.samples == expected.samples
    assert len(calibration.knots) == positions


def test_calibrated_rt_corrects_local_shift_inside_span_only() -> None:
    shifted = {"S01": tuple(-0.5 * (32 - rt) / 24 for rt in REFERENCE_RTS)}
    calibration = _calibrate(_batch(shifted))
    sample = calibration.samples["S01"]

    observed_ten = 10 - 0.5 * 22 / 24
    assert sample.calibrated_rt(observed_ten) == pytest.approx(10.0, abs=0.01)
    assert sample.calibrated_rt(19.75) == pytest.approx(20.0, abs=0.01)
    assert sample.calibrated_rt(6.0) is None
    assert sample.calibrated_rt(33.0) is None
    frame = SampleRtFrame().with_sample_calibration(calibration)
    assert frame.has_support("S01")
    assert not frame.has_support("missing")
    common_rt = frame.common_rt_min("S01", observed_ten)
    assert common_rt == pytest.approx(sample.calibrated_rt(observed_ten))
    assert common_rt + frame.offset_min("S01", common_rt) == pytest.approx(observed_ten)
    assert frame.common_rt_min("S01", 6.0) == 6.0
    assert frame.offset_min("S01", 6.0) == 0.0
    assert frame.common_rt_min("missing", 9.65) == 9.65


def test_local_correspondences_do_not_require_near_all_batch_prevalence() -> None:
    detected = _batch()
    detected["rare"] = {s: 20.0 for s in SAMPLES[:5]}

    calibration = _calibrate(detected)

    assert any("rare" in k.feature_ids for k in calibration.knots)
    assert calibration.samples["S01"].calibrated_rt(20) == pytest.approx(20, abs=0.01)


def test_too_few_knots_leave_sample_uncalibrated() -> None:
    detected = {k: v for k, v in _batch().items() if k == "F0"}

    calibration = _calibrate(detected)

    sample = calibration.samples["S01"]
    assert sample.status == "insufficient_anchors"
    assert sample.calibrated_rt(8.0) is None


@pytest.mark.parametrize("positions", (0, 1, 2, 7, 8))
def test_graded_estimate_uses_sparse_positions_without_claiming_loess(positions):
    detected = {
        f"F{i}": {s: 8.0 + i + (0.2 if s == "S01" else 0) for s in SAMPLES}
        for i in range(positions)
    }
    sample = _calibrate(detected).samples["S01"]
    estimate = sample.estimate_rt(8.7)
    if positions < 2:
        assert estimate is None
        return
    assert estimate.rt_min == pytest.approx(8.5)
    assert estimate.method == ("loess" if positions >= 8 else "median_shift")
    assert estimate.anchor_count == positions
    assert estimate.residual_spread_min == pytest.approx(0)
    assert sample.estimate_observed_rt(estimate.rt_min).rt_min == pytest.approx(8.7)
    if positions < 8:
        assert sample.calibrated_rt(8.7) is None


def test_boundary_shift_preserves_order_and_inverse_of_supported_curve():
    samples = ("a", "b", "c")
    detected = {
        f"F{i}": {"a": 10.0 + i, "b": 10.0 + 1.1 * i, "c": 10.0 + 1.1 * i}
        for i in range(8)
    }
    calibration = build_batch_rt_calibration(
        detected,
        sample_stems=samples,
        prior_injections={},
        policy=RtCalibrationPolicy(),
    )
    sample = calibration.samples["a"]
    queries = (9.99, 10.0, 13.2, 17.0, 17.01)
    estimates = [sample.estimate_rt(rt) for rt in queries]
    assert [e.rt_min for e in estimates] == pytest.approx(
        (9.99, 10, 13.52, 17.7, 17.71)
    )
    for rt, estimate in zip(queries, estimates, strict=True):
        assert sample.estimate_observed_rt(estimate.rt_min).rt_min == pytest.approx(rt)
    assert estimates[-1].method == "boundary_shift"
    assert estimates[-1].outside_anchor_range_min == pytest.approx(0.01)
    assert sample.estimate_observed_rt(17.5).rt_min == pytest.approx(16.818181818)
    assert sample.calibrated_rt(17.01) is None  # Strong-support API stays bounded.


def test_failed_strong_fit_retains_explicit_weak_estimate():
    detected = {
        f"F{i}": {
            s: 8.0 + 4 * i + (2.0 if s == "S01" and i == 3 else 0.0) for s in SAMPLES
        }
        for i in range(8)
    }
    sample = _calibrate(detected).samples["S01"]
    assert sample.calibrated_rt(10.0) is None
    estimate = sample.estimate_rt(10.0)
    assert estimate.method == "median_shift"
    assert estimate.rt_min == pytest.approx(10.0)
    assert estimate.anchor_count == 8  # Qualified positions, not final LOESS support.
    assert estimate.fit_status == sample.status != "calibrated"


def test_weak_estimate_snapshot_round_trip_does_not_upgrade_legacy_maps(tmp_path):
    from xic_extractor.diagnostics.grouping_experiment_io import (
        read_rt_calibration,
        write_rt_calibration,
    )

    calibration = _calibrate(dict(list(_batch().items())[:2]))
    path = tmp_path / "calibration.json"
    write_rt_calibration(path, calibration)
    restored = read_rt_calibration(path)
    assert restored.samples["S01"].estimate_rt(12) == calibration.samples[
        "S01"
    ].estimate_rt(12)
    assert restored.samples["S01"].estimate_rt(12).method == "median_shift"

    strong = _calibrate(_batch())
    write_rt_calibration(path, strong)
    payload = json.loads(path.read_text())
    payload["schema_version"] = 2
    for sample in payload["samples"]:
        sample.pop("estimate_support", None)
    path.write_text(json.dumps(payload))
    legacy = read_rt_calibration(path).samples["S01"]
    assert legacy.calibrated_rt(20) == strong.samples["S01"].calibrated_rt(20)
    assert legacy.estimate_rt(20).method == "retained_map"
    assert legacy.estimate_rt(20).anchor_count is None
    assert legacy.estimate_rt(40) is None


@pytest.mark.parametrize(
    "field,value",
    (
        ("positions_min", [8, 8]),
        ("positions_min", [True, 9]),
        ("median_shift_min", "0.1"),
        ("shift_spread_min", -1),
    ),
)
def test_persisted_estimate_support_rejects_malformed_values(tmp_path, field, value):
    from xic_extractor.diagnostics.grouping_experiment_io import (
        read_rt_calibration,
        write_rt_calibration,
    )

    path = tmp_path / "calibration.json"
    write_rt_calibration(path, _calibrate(dict(list(_batch().items())[:2])))
    payload = json.loads(path.read_text())
    payload["samples"][0]["estimate_support"][field] = value
    path.write_text(json.dumps(payload))
    with pytest.raises(ValueError, match="RT estimate"):
        read_rt_calibration(path)


@pytest.mark.parametrize("positions", (2, 7, 8))
def test_loess_requires_eight_distinct_measured_positions(positions: int) -> None:
    detected = {
        f"F{i}": {s: 8.0 + i + (0.2 if s == "S01" else 0) for s in SAMPLES}
        for i in range(positions)
    }
    # Repeated ion views cannot turn seven acquired positions into eight.
    detected.update({f"alias{i}": dict(detected["F0"]) for i in range(20)})
    sample = _calibrate(detected).samples["S01"]
    assert sample.anchor_count == positions
    if positions < 8:
        assert sample.status == "insufficient_anchors"
        assert sample.calibrated_rt(9.2) is None
    else:
        assert sample.calibrated_rt(9.2) == pytest.approx(9.0)


def test_ordinary_anchor_needs_half_of_its_cohort_not_just_three_members() -> None:
    detected = _batch()
    detected["sparse"] = dict.fromkeys(SAMPLES[:3], 20.0)
    calibration = _calibrate(detected)
    assert not any("sparse" in knot.feature_ids for knot in calibration.knots)
    assert "sparse" in detected  # Anchor rejection does not delete a feature.


@pytest.mark.parametrize("positions", (8, 9))
def test_rejected_position_does_not_count_toward_final_loess_support(positions):
    detected = {
        f"F{i}": {
            s: 8.0 + 4 * i + (2.0 if s == "S01" and i == 3 else 0.0) for s in SAMPLES
        }
        for i in range(positions)
    }
    calibration = _calibrate(detected)
    # Every other position independently specifies the identity RT map.
    expected = None if positions == 8 else pytest.approx(10.0)
    assert calibration.samples["S01"].calibrated_rt(10.0) == expected
    assert calibration.samples["S02"].calibrated_rt(10.0) == pytest.approx(10.0)


def test_unshared_standard_cannot_extend_calibration_support() -> None:
    calibration = build_batch_rt_calibration(
        _batch(),
        sample_stems=SAMPLES,
        prior_injections={},
        policy=RtCalibrationPolicy(),
        measured_istds=(("unshared", "S01", 50.0),),
    )
    assert not any("unshared" in knot.feature_ids for knot in calibration.knots)
    assert calibration.samples["S01"].calibrated_rt(40.0) is None


def test_unresolved_standard_order_is_not_forced_into_a_map() -> None:
    calibration = build_batch_rt_calibration(
        {},
        sample_stems=SAMPLES,
        prior_injections={},
        policy=RtCalibrationPolicy(),
        measured_istds=tuple(
            (f"standard{i}", s, observed)
            for s in SAMPLES
            for i, observed in enumerate(
                (12, 8, 16, 20, 24, 28, 32, 36)
                if s == "S01"
                else (8, 12, 16, 20, 24, 28, 32, 36)
            )
        ),
    )
    assert calibration.samples["S01"].calibrated_rt(10) is None
    assert calibration.samples["S02"].calibrated_rt(10) == pytest.approx(10)


@pytest.mark.parametrize("sample_count", (7, 12))
def test_shared_anchors_correct_systematic_and_rt_dependent_injection_shift(
    sample_count,
):
    samples = tuple(f"S{i}" for i in range(sample_count))

    def observed(rt, i):
        return rt + 0.01 * i - (0.8 * (38 - rt) / 31 if i == 0 else 0)

    groups = {
        f"F{j}": {s: observed(float(rt), i) for i, s in enumerate(samples)}
        for j, rt in enumerate(np.linspace(7, 38, 12))
    }
    standards = tuple(("early", s, observed(6, i)) for i, s in enumerate(samples))
    kwargs = dict(
        sample_stems=samples, prior_injections={}, policy=RtCalibrationPolicy()
    )
    ordinary = build_batch_rt_calibration(groups, **kwargs)
    known = build_batch_rt_calibration({}, measured_istds=standards, **kwargs)
    joint = build_batch_rt_calibration(groups, measured_istds=standards, **kwargs)
    centre = 0.01 * (sample_count - 1) / 2
    assert not known.has_support("S0")  # One measured position cannot define a map.
    assert ordinary.samples["S0"].calibrated_rt(observed(6.5, 0)) is None
    for i, sample in enumerate(samples):
        for rt in (6.5, 12, 20, 37):
            value = joint.samples[sample].calibrated_rt(observed(rt, i))
            assert value == pytest.approx(rt + centre)
            assert value + joint.offset_min(sample, value) == pytest.approx(
                observed(rt, i)
            )


def test_conflicting_standard_does_not_drag_a_supported_ordinary_map():
    samples = ("a", "b", "c", "d")
    detected = {
        f"F{i}": dict.fromkeys(samples, float(rt))
        for i, rt in enumerate(np.linspace(7, 38, 24))
    }
    calibration = build_batch_rt_calibration(
        detected,
        sample_stems=samples,
        prior_injections={},
        policy=RtCalibrationPolicy(),
        measured_istds=tuple(("late", s, 35 if s == "a" else 42) for s in samples),
    )
    assert calibration.samples["a"].calibrated_rt(35) == pytest.approx(35)
    (residual,) = calibration.samples["a"].standard_residuals
    assert residual[0] == "late"
    assert residual[1:] == pytest.approx((35, 42, -7))


@pytest.mark.parametrize("standard_reference", (16.65, 30.0))
def test_identity_landmark_corrects_locally_without_unbounded_influence(
    standard_reference,
):
    samples = ("a", "b", "c", "d", "e")
    detected = {
        f"F{i}": {
            s: float(rt) - 1 + (0.05 if i % 2 else -0.05)
            if s == "a"
            else float(rt) + (0.2 if s == "b" else 0)
            for s in samples
        }
        for i, rt in enumerate(range(8, 40))
    }
    measured = {
        s: 15.3 if s == "a" else standard_reference + (0.2 if s == "b" else 0)
        for s in samples
    }
    # The same acquired position already has an ordinary correspondence.
    # Naming its known identity must not add a vote or move other fitted knots.
    detected["ordinary_view"] = measured
    kwargs = dict(
        sample_stems=samples, prior_injections={}, policy=RtCalibrationPolicy()
    )
    ordinary = build_batch_rt_calibration(detected, **kwargs)
    known = build_batch_rt_calibration(
        detected,
        measured_istds=tuple(("standard", s, rt) for s, rt in measured.items()),
        **kwargs,
    )
    before, after = ordinary.samples["a"], known.samples["a"]
    assert after.anchor_count == before.anchor_count
    assert after.reliable_observed_range == before.reliable_observed_range
    assert [(x, y) for x, y in after.knots if x != 15.3] == [
        (x, y) for x, y in before.knots if x != 15.3
    ]
    calibrated = after.calibrated_rt(15.3)
    if standard_reference == 16.65:
        assert calibrated == pytest.approx(standard_reference, abs=0.1)
    else:
        # An identity/coordinate contradiction cannot pull a whole RT region.
        assert abs(calibrated - before.calibrated_rt(15.3)) < 0.5
        assert abs(calibrated - standard_reference) > 10
    assert known.samples["b"].calibrated_rt(measured["b"]) == pytest.approx(
        standard_reference
    )
    assert calibrated + known.offset_min("a", calibrated) == pytest.approx(15.3)


def test_neighboring_identity_conflicts_do_not_reverse_the_supported_map():
    samples = ("a", "b", "c")
    detected = {
        f"F{i}": {
            s: float(rt) - 1 + (0.05 if i % 2 else -0.05) if s == "a" else float(rt)
            for s in samples
        }
        for i, rt in enumerate(range(8, 40))
    }
    measured = (
        ("first", "a", 15.3),
        ("first", "b", 16.36),
        ("first", "c", 16.36),
        ("second", "a", 15.4),
        ("second", "b", 16.34),
        ("second", "c", 16.34),
    )
    for label, sample, observed in measured:
        detected.setdefault(label, {})[sample] = observed
    kwargs = dict(
        sample_stems=samples, prior_injections={}, policy=RtCalibrationPolicy()
    )
    ordinary = build_batch_rt_calibration(detected, **kwargs)
    known = build_batch_rt_calibration(detected, measured_istds=measured, **kwargs)
    assert known.samples["a"].knots == ordinary.samples["a"].knots
    assert known.samples["a"].status == "calibrated"
    reversed_labels = build_batch_rt_calibration(
        detected, measured_istds=tuple(reversed(measured)), **kwargs
    )
    assert reversed_labels.samples == known.samples


def test_measured_front_shift_decides_and_timeline_explains() -> None:
    early = (-0.6, -0.6) + (0.0,) * (len(REFERENCE_RTS) - 2)
    late = (0.4, 0.4) + (0.0,) * (len(REFERENCE_RTS) - 2)
    detected = _batch({"S01": early, "S02": late, "S03": early, "S04": late})
    # S06 cannot be measured: it lacks the first knot.
    del detected["F0"]["S06"]
    normal = PriorInjection(False, False, False)
    priors = {s: normal for s in SAMPLES}
    # Short method, short idle: the pre-flagged early shift.
    priors["S01"] = PriorInjection(True, True, False)
    # Short method, long idle: not pre-flagged, gradient still unsettled.
    priors["S02"] = PriorInjection(False, True, False)
    # S03 follows a full-length run; S04 has no timeline row.
    del priors["S04"]
    priors["S05"] = PriorInjection(True, True, False)

    calibration = _calibrate(detected, priors)

    status = {s: c.perturbation_status for s, c in calibration.samples.items()}
    assert status["S01"] == "confirmed"
    assert status["S02"] == "confirmed"
    assert status["S03"] == "unexplained_outlier"
    assert status["S04"] == "outlier_without_timeline"
    assert status["S05"] == "flagged_unconfirmed"
    assert status["S06"] == "unmeasured"
    assert status["S07"] == "not_perturbed"
    assert calibration.perturbation_tolerance_min == pytest.approx(0.1)


def test_first_run_after_method_change_explains_outlier() -> None:
    early = (-0.8, -0.8) + (0.0,) * (len(REFERENCE_RTS) - 2)
    detected = _batch({"S01": early, "S02": early})
    normal = PriorInjection(False, False, False)
    priors = {s: normal for s in SAMPLES}
    priors["S01"] = PriorInjection(False, False, True)

    calibration = _calibrate(detected, priors)

    assert calibration.samples["S01"].perturbation_status == "confirmed"
    assert calibration.samples["S02"].perturbation_status == "unexplained_outlier"


def test_cli_writes_calibration_and_heldout_evidence(tmp_path: Path) -> None:
    detected = _batch({"S01": (-0.6,) * len(REFERENCE_RTS)})
    # Multiple views of one acquired peak must stay on one side of the split.
    detected.update({f"F1-view{i}": dict(detected["F1"]) for i in range(6)})
    # A partially observed feature and one conflicting coordinate; the
    # diagnostic independently withholds a feature from the validation fit.
    detected["heldout"] = {s: 11.0 for s in SAMPLES[:9]}
    detected["heldout"]["S01"] = 10.4
    detected["heldout"]["S09"] = 33.0  # outside S09's anchor span
    cells = tmp_path / "alignment_cells.tsv"
    lines = ["feature_family_id\tsample_stem\tstatus\tapex_rt"]
    for feature, rts in detected.items():
        status = "detected"
        lines += [f"{feature}\t{s}\t{status}\t{rt}" for s, rt in rts.items()]
    lines.append("F0\tS11\tbackfilled\t8.0")
    cells.write_text("\n".join(lines) + "\n", encoding="utf-8")
    timeline = tmp_path / "timeline.json"
    timeline.write_text(
        json.dumps(
            {
                "schema": "injection_timeline",
                "policy": {
                    "max_previous_run_min": 20.0,
                    "max_previous_run_fraction": 0.5,
                    "max_idle_min": 5.0,
                },
                "injections": [
                    {
                        "injection_order": n,
                        "raw_stem": s,
                        # S03 is the first run after another method.
                        "header": {"instrument_method": "PRM" if n < 2 else "DDA"},
                        "possibly_perturbed": s == "S01",
                        "previous_raw_stem": "SDOLEK" if s == "S01" else "prior",
                        # S02: within the length ratio, above the 20-min limit.
                        "previous_run_length_min": {"S01": 8.0, "S02": 30.0}.get(
                            s, 60.0
                        ),
                        "run_length_min": 70.0 if s == "S02" else 60.0,
                        "idle_min": 1.9,
                    }
                    for n, s in enumerate(SAMPLES)
                ]
                + [
                    # A blank has its own method; S05 still compares with S04.
                    {
                        "injection_order": 3.5,
                        "raw_stem": "Blank",
                        "header": {"instrument_method": "Blank"},
                        "possibly_perturbed": False,
                        "previous_raw_stem": "S04",
                        "previous_run_length_min": 60.0,
                        "run_length_min": 15.0,
                        "idle_min": 1.9,
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    out = tmp_path / "out"

    assert (
        main(
            [
                "--alignment-cells",
                str(cells),
                "--injection-timeline",
                str(timeline),
                "--output-dir",
                str(out),
                "--loess-fraction",
                "0.3",
                "--heldout-min-sample-fraction",
                "0.7",
            ]
        )
        == 0
    )

    payload = json.loads((out / "sample_rt_calibration.json").read_text("utf-8"))
    assert payload["evidence_status"] == "diagnostic_only"
    assert payload["sample_count"] == 11
    assert payload["timeline_unmatched_samples"] == ["S11"]
    assert len(payload["knots"]) == len(REFERENCE_RTS) + 1
    by_sample = {row["sample_stem"]: row for row in payload["samples"]}
    assert by_sample["S01"]["perturbation_status"] == "confirmed"
    assert by_sample["S01"]["after_short_run"] is True
    assert by_sample["S01"]["previous_raw_stem"] == "SDOLEK"
    assert by_sample["S02"]["after_short_run"] is False
    assert by_sample["S03"]["after_method_change"] is True
    assert by_sample["S03"]["previous_full_run_raw_stem"] == "S02"
    assert by_sample["S04"]["after_method_change"] is False
    assert by_sample["S05"]["after_method_change"] is False
    assert by_sample["S11"]["status"] == "insufficient_anchors"
    heldout = payload["heldout_validation"]
    assert heldout["paired_feature_count"] == 2
    assert heldout["calibrated_median_feature_mad_min"] < 0.01
    with (out / "sample_rt_calibration_heldout.tsv").open(encoding="utf-8") as f:
        rows = {
            (row["sample_stem"], row["rt_band"]): row
            for row in csv.DictReader(f, delimiter="\t")
        }
    early = rows["S01", "10-15"]
    assert float(early["raw_median_offset_min"]) == pytest.approx(-0.6, abs=0.01)
    assert abs(float(early["calibrated_median_offset_min"])) < 0.01
    assert ("S09", "10-15") not in rows  # Held-out RT 33 is outside support.
    assert ("S09", "20-30") in rows
