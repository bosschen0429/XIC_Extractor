"""Published review retains the final calibration, not bootstrap coordinates."""

from dataclasses import replace

from tests.alignment_explanation_fixtures import (
    explanation_discovery_facts,
    run_alignment_fixture,
)
from xic_extractor.alignment.rt_frame import SampleRtFrame
from xic_extractor.alignment.sample_rt_calibration import (
    BatchRtCalibration,
    RtCalibrationPolicy,
    RtEstimateSupport,
    SampleRtCalibration,
)
from xic_extractor.alignment_explanation.reader import read_alignment_explanation
from xic_extractor.diagnostics.alignment_explanation_projection import (
    write_alignment_explanation,
)
from xic_extractor.rt_calibration_codec import rt_calibration_from_payload


def test_explanation_freezes_final_rt_owner_and_preserves_reference_provenance(
    tmp_path, monkeypatch
):
    completed, candidates, matrix, config = run_alignment_fixture(
        tmp_path, monkeypatch, "positive"
    )
    calibration = BatchRtCalibration(
        RtCalibrationPolicy(),
        (),
        {
            stem: SampleRtCalibration(
                stem,
                "insufficient_anchors",
                (),
                None,
                None,
                "unmeasured",
                estimate_support=RtEstimateSupport((7.0, 9.0), shift, 0.01, None),
            )
            for stem, shift in (("S1", 0.2), ("S2", -0.3))
        },
        None,
        None,
        None,
    )
    completed = replace(
        completed, sample_rt_frame=SampleRtFrame().with_sample_calibration(calibration)
    )
    provenance = {
        "target_snapshot": "saved target input",
        "coordinates": "measured input",
        "calibration": {"role": "bootstrap_identity_reference"},
    }
    path = write_alignment_explanation(
        tmp_path / "final.sqlite",
        completed=completed,
        **explanation_discovery_facts(completed),
        peaks_by_sample=candidates,
        matrix_path=matrix,
        alignment_config=config,
        known_istd_reference_provenance=provenance,
    )
    summary = read_alignment_explanation(path)
    replay = rt_calibration_from_payload(summary["sample_rt_calibration"])
    assert replay.samples["S1"].estimate_rt(8).rt_min == 8.2
    assert replay.samples["S2"].estimate_rt(8).rt_min == 7.7
    assert replay.samples["S1"].estimate_support.positions_min == (7, 9)
    assert summary["known_istd_reference"] == {
        "target_snapshot": "saved target input",
        "coordinates": "measured input",
    }
    assert "calibration" in provenance
