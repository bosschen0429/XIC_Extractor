import json
from pathlib import Path

import pytest

from tools.diagnostics.build_sample_rt_calibration import main
from xic_extractor.alignment.sample_rt_calibration import (
    RtCalibrationPolicy,
    build_batch_rt_calibration,
)

SAMPLES = tuple(f"S{i:02d}" for i in range(1, 11))
REFERENCE_RTS = (8.0, 12.0, 16.0, 24.0, 32.0)


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


def _calibrate(detected, flags=None, **policy):
    return build_batch_rt_calibration(
        detected,
        sample_stems=SAMPLES,
        possibly_perturbed=flags or {},
        policy=RtCalibrationPolicy(**policy),
    )


def test_coeluting_anchor_features_collapse_into_one_knot() -> None:
    detected = _batch()
    # An isotope/adduct family of the 12-min compound, inverted in one sample.
    detected["F1_adduct"] = {
        s: rt + (0.02 if s == "S03" else -0.01) for s, rt in detected["F1"].items()
    }

    calibration = _calibrate(detected)

    assert [k.reference_rt_min for k in calibration.knots] == pytest.approx(
        REFERENCE_RTS, abs=0.01
    )
    assert set(calibration.knots[1].feature_ids) == {"F1", "F1_adduct"}
    assert {s.status for s in calibration.samples.values()} == {"calibrated"}


def test_calibrated_rt_corrects_local_shift_inside_span_only() -> None:
    shifted = {"S01": (-0.5, -0.2, 0.0, 0.0, 0.0)}
    calibration = _calibrate(_batch(shifted))
    sample = calibration.samples["S01"]

    # Halfway between the 8- and 12-min knots the sample runs 0.35 min early.
    assert sample.calibrated_rt(10.0 - 0.35) == pytest.approx(10.0, abs=0.01)
    assert sample.calibrated_rt(20.0) == pytest.approx(20.0, abs=0.01)
    assert sample.calibrated_rt(6.0) is None
    assert sample.calibrated_rt(33.0) is None


def test_rare_features_are_not_anchors() -> None:
    detected = _batch()
    detected["rare"] = {s: 20.0 for s in SAMPLES[:5]}

    calibration = _calibrate(detected)

    assert all("rare" not in k.feature_ids for k in calibration.knots)


def test_too_few_knots_leave_sample_uncalibrated() -> None:
    detected = {k: v for k, v in _batch().items() if k == "F0"}

    calibration = _calibrate(detected)

    sample = calibration.samples["S01"]
    assert sample.status == "insufficient_anchors"
    assert sample.calibrated_rt(8.0) is None


def test_crossing_anchor_order_leaves_sample_uncalibrated() -> None:
    # S01 elutes the 12-min anchor after the 16-min anchor.
    calibration = _calibrate(_batch({"S01": (0.0, 4.5, 0.0, 0.0, 0.0)}))

    sample = calibration.samples["S01"]
    assert sample.status == "non_monotonic_anchors"
    assert sample.calibrated_rt(10.0) is None
    assert calibration.samples["S02"].status == "calibrated"


def test_only_flagged_injection_with_measured_front_shift_is_confirmed() -> None:
    front = (-0.6, -0.6, 0.0, 0.0, 0.0)
    detected = _batch({"S01": front, "S02": front})
    # S04 is flagged but lacks the first knot, so its front cannot be measured.
    del detected["F0"]["S04"]

    calibration = _calibrate(
        detected,
        flags={"S01": True, "S03": True, "S04": True},
        anchor_min_sample_fraction=0.9,
    )

    status = {s: c.perturbation_status for s, c in calibration.samples.items()}
    assert status["S01"] == "confirmed"
    assert status["S02"] == "not_flagged"
    assert status["S03"] == "flagged_unconfirmed"
    assert status["S04"] == "flagged_unmeasured"
    assert calibration.perturbation_tolerance_min == pytest.approx(0.1)


def test_cli_writes_calibration_and_heldout_evidence(tmp_path: Path) -> None:
    detected = _batch({"S01": (-0.6, -0.6, 0.0, 0.0, 0.0)})
    # Seen in 9 samples: below the anchor threshold, above the held-out one.
    detected["heldout"] = {s: 11.0 for s in SAMPLES[:9]}
    detected["heldout"]["S01"] = 10.4
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
                "injections": [
                    {"raw_stem": s, "possibly_perturbed": s == "S01"} for s in SAMPLES
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
                "--anchor-min-sample-fraction",
                "0.9",
                "--heldout-min-sample-fraction",
                "0.8",
            ]
        )
        == 0
    )

    payload = json.loads((out / "sample_rt_calibration.json").read_text("utf-8"))
    assert payload["evidence_status"] == "diagnostic_only"
    assert payload["sample_count"] == 11
    assert payload["timeline_unmatched_samples"] == ["S11"]
    assert len(payload["knots"]) == len(REFERENCE_RTS)
    by_sample = {row["sample_stem"]: row for row in payload["samples"]}
    assert by_sample["S01"]["perturbation_status"] == "confirmed"
    assert by_sample["S11"]["status"] == "insufficient_anchors"
    heldout = payload["heldout_validation"]
    assert heldout["paired_feature_count"] == 1
    assert heldout["calibrated_median_feature_mad_min"] < 0.01
    rows = (out / "sample_rt_calibration_heldout.tsv").read_text("utf-8")
    assert "S01\t10-15\t1\t-0.6\t1\t" in rows
