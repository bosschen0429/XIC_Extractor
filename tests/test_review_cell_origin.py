"""A cell's origin follows the peak that supplied its value, not MS2 support."""

from types import SimpleNamespace as NS

import pytest

from xic_extractor.alignment_explanation.review import review_cell


@pytest.mark.parametrize(
    ("stage", "method", "value", "expected"),
    [
        ("initial_capture", "SUPPORT", 100.0, "discovery_support"),
        ("backfill_observation", "HOLD", 100.0, "backfill_support"),
        ("backfill_observation", "SUPPORT", 100.0, "backfill_support"),
        ("method_backfill", "SUPPORT", 100.0, "backfill_support"),
        ("initial_capture", "SUPPORT", None, "unavailable"),
    ],
)
@pytest.mark.parametrize("with_companion", [False, True])
def test_origin_names_only_the_peak_providing_the_matrix_value(
    stage, method, value, expected, with_companion
):
    def observation(key, capture_stage, method_result):
        return NS(
            peak_observation=NS(
                capture_stage=capture_stage,
                physical_peak_key=key,
                integration=NS(rt_apex_min=1.0, signal_quality=None),
            ),
            physical_peak_key=key,
            method_result=method_result,
            reason_codes=(),
        )

    selected = observation("selected", stage, method)
    other = observation("other", "backfill_observation", "SUPPORT")
    decision = NS(
        subject=NS(canonical_sample=NS(sample_name="S1")),
        evidence=NS(
            effective_cells=(selected, other) if with_companion else (selected,),
            evidence_states=(),
        ),
        selected_integration=selected.peak_observation.integration,
        value=value,
        variant="numeric" if value is not None else "not_quantifiable",
        source="trapezoid_counts_seconds_on_canonical_signal",
    )
    assert review_cell(decision)["support_origin"] == expected
