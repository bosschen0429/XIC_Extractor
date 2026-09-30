import json
from pathlib import Path

import pytest

from xic_extractor.diagnostics.shared_peak_identity_explanation import (
    overlay_trace_data,
)


def test_overlay_trace_data_loads_typed_bundle(tmp_path: Path) -> None:
    path = tmp_path / "overlay_trace_data.json"
    path.write_text(
        json.dumps(
            {
                "family_id": "FAM001",
                "rt_min": "7.5",
                "rt_max": 8.5,
                "family_center_rt": "8.0",
                "evidence_summary": {"family_verdict": "supportive"},
                "traces": [
                    {
                        "sample_stem": "S1",
                        "status": "detected",
                        "rt": ["7.9", 8.0],
                        "intensity": [10, "20"],
                    },
                    "ignored",
                ],
            }
        ),
        encoding="utf-8",
    )

    bundle = overlay_trace_data.load_overlay_trace_data(path)

    assert bundle.family_id == "FAM001"
    assert bundle.rt_min == pytest.approx(7.5)
    assert bundle.rt_max == pytest.approx(8.5)
    assert bundle.family_center_rt == pytest.approx(8.0)
    assert bundle.evidence_summary["family_verdict"] == "supportive"
    assert len(bundle.traces) == 1
    assert bundle.traces[0].sample_stem == "S1"
    assert bundle.traces[0].optional_float_sequence("rt") == (7.9, 8.0)
    assert bundle.traces[0].optional_float_sequence("intensity") == (10.0, 20.0)


def test_overlay_trace_data_accepts_legacy_samples_alias(tmp_path: Path) -> None:
    path = tmp_path / "overlay_trace_data.json"
    path.write_text(
        json.dumps(
            {
                "feature_family_id": "FAM002",
                "samples": [
                    {
                        "sample_stem": "S2",
                        "group": "QC",
                    },
                ],
            }
        ),
        encoding="utf-8",
    )

    bundle = overlay_trace_data.load_overlay_trace_data(path)

    assert bundle.family_id == "FAM002"
    assert len(bundle.traces) == 1
    assert bundle.traces[0].sample_stem == "S2"
    assert bundle.traces[0].group == "QC"


def test_overlay_trace_data_requires_traces_array(tmp_path: Path) -> None:
    path = tmp_path / "overlay_trace_data.json"
    path.write_text(json.dumps({"family_id": "FAM001"}), encoding="utf-8")

    with pytest.raises(ValueError, match="missing traces/samples array"):
        overlay_trace_data.load_overlay_trace_data(path)
