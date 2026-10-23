from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from tests.test_discovery_csv import _candidate, _read_csv
from xic_extractor.discovery.csv_reader import read_discovery_candidates_csv
from xic_extractor.discovery.csv_writer import (
    build_discovery_review_note,
    write_discovery_candidates_csv,
    write_discovery_review_csv,
)
from xic_extractor.discovery.models import (
    DISCOVERY_CANDIDATE_STATE_VALUES,
    assign_discovery_candidate_state,
)


@pytest.mark.parametrize(
    "candidate_state",
    ["review_only_physical_unavailable", "review_only_physical_failed"],
)
def test_discovery_csv_rejects_non_physical_state_values(
    tmp_path: Path,
    candidate_state: str,
) -> None:
    output_path = tmp_path / "discovery_candidates.csv"
    candidate = replace(
        _candidate(
            candidate_id="Sample_1#6095@mz258.108512_p142.061112",
            discovery_candidate_state=candidate_state,
            ms1_feature_row_id="",
            ms1_peak_found=False,
            ms1_area=None,
            ms1_support="missing",
            rt_alignment="missing",
            reason=f"physical MS1 evaluation {candidate_state}",
        ),
        ms1_apex_rt=None,
        ms1_seed_delta_min=None,
        ms1_peak_rt_start=None,
        ms1_peak_rt_end=None,
        ms1_height=None,
        ms1_scan_support_score=None,
        matched_tag_names=("DNA_dR",),
        primary_tag_name="DNA_dR",
    )

    write_discovery_candidates_csv(output_path, (candidate,))
    with pytest.raises(ValueError, match="discovery_candidate_state must be one"):
        read_discovery_candidates_csv(output_path)


@pytest.mark.parametrize(
    (
        "case",
        "ms2_support",
        "ms1_peak_found",
        "precursor_mz_basis",
        "trace_quality",
        "reason",
        "ms1_support",
        "rt_alignment",
        "expected_state",
        "expected_note",
    ),
    [
        (
            "legacy_absence_weak",
            "weak",
            False,
            "scan_precursor",
            "missing",
            "strict MS2 NL seed; MS1 peak missing",
            "missing",
            "missing",
            "review_only_orphan_nl",
            "weak MS2; missing MS1; missing RT",
        ),
        (
            "legacy_absence_moderate",
            "moderate",
            False,
            "scan_precursor",
            "missing",
            "strict MS2 NL seed; MS1 peak missing",
            "missing",
            "missing",
            "review_only_orphan_nl",
            "moderate MS2; missing MS1; missing RT",
        ),
        (
            "legacy_absence_strong",
            "strong",
            False,
            "scan_precursor",
            "missing",
            "strict MS2 NL seed; MS1 peak missing",
            "missing",
            "missing",
            "review_only_orphan_nl",
            "strong MS2; missing MS1; missing RT",
        ),
        (
            "legacy_supported",
            "moderate",
            True,
            "scan_precursor",
            "GOOD",
            "single MS2 NL seed; MS1 peak found",
            "moderate",
            "aligned",
            "ms1_feature_nl_supported",
            "moderate MS2; moderate MS1; aligned RT",
        ),
        (
            "legacy_rescued",
            "strong",
            True,
            "product_plus_neutral_loss",
            "GOOD",
            "single MS2 NL seed; MS1 peak found",
            "strong",
            "near",
            "ms1_feature_nl_rescued",
            "strong MS2; strong MS1; near RT",
        ),
        (
            "available_supported",
            "moderate",
            True,
            "scan_precursor",
            "clean",
            "single MS2 NL seed; MS1 peak found",
            "moderate",
            "aligned",
            "ms1_feature_nl_supported",
            "moderate MS2; moderate MS1; aligned RT",
        ),
        (
            "available_rescued",
            "strong",
            True,
            "mixed",
            "clean",
            "single MS2 NL seed; MS1 peak found",
            "strong",
            "near",
            "ms1_feature_nl_rescued",
            "strong MS2; strong MS1; near RT",
        ),
        (
            "physical_unavailable",
            "weak",
            False,
            "scan_precursor",
            "physical_unavailable",
            "physical MS1 evaluation unavailable: insufficient_points_for_profile",
            "missing",
            "missing",
            "review_only_orphan_nl",
            "physical MS1 unavailable: insufficient_points_for_profile; missing RT",
        ),
        (
            "physical_failed",
            "moderate",
            False,
            "product_plus_neutral_loss",
            "physical_failed",
            "physical MS1 evaluation failed: non_monotonic_rt",
            "missing",
            "missing",
            "review_only_orphan_nl",
            "physical MS1 failed: non_monotonic_rt; missing RT",
        ),
    ],
)
def test_discovery_state_and_physical_truth_projection_table(
    tmp_path: Path,
    case: str,
    ms2_support: str,
    ms1_peak_found: bool,
    precursor_mz_basis: str,
    trace_quality: str,
    reason: str,
    ms1_support: str,
    rt_alignment: str,
    expected_state: str,
    expected_note: str,
) -> None:
    state = assign_discovery_candidate_state(
        ms1_peak_found=ms1_peak_found,
        has_nl_support=precursor_mz_basis == "scan_precursor",
    )
    candidate = replace(
        _candidate(
            candidate_id=f"Sample_1#6095@mz258.108512_p142.061112-{case}",
            ms2_support=ms2_support,
            ms1_support=ms1_support,
            rt_alignment=rt_alignment,
            reason=reason,
            ms1_peak_found=ms1_peak_found,
            ms1_area=88765.4 if ms1_peak_found else None,
            discovery_candidate_state=state,
            ms1_feature_row_id=(
                "Sample_1|DNA_dR|258.108512|7.841234" if ms1_peak_found else ""
            ),
        ),
        precursor_mz_basis=precursor_mz_basis,  # type: ignore[arg-type]
        ms1_trace_quality=trace_quality,
        ms1_apex_rt=7.841234 if ms1_peak_found else None,
        ms1_seed_delta_min=0.011234 if ms1_peak_found else None,
        ms1_peak_rt_start=7.701234 if ms1_peak_found else None,
        ms1_peak_rt_end=7.981234 if ms1_peak_found else None,
        ms1_height=4500.5 if ms1_peak_found else None,
        ms1_scan_support_score=0.8 if ms1_peak_found else None,
    )

    assert candidate.discovery_candidate_state == expected_state
    assert candidate.ms1_trace_quality == trace_quality
    assert candidate.reason == reason
    assert build_discovery_review_note(candidate) == expected_note
    output_path = tmp_path / f"{case}.review.csv"
    write_discovery_review_csv(output_path, (candidate,))
    assert _read_csv(output_path)[0]["review_note"] == expected_note
    if trace_quality in {"physical_unavailable", "physical_failed"}:
        assert candidate.ms1_apex_rt is None
        assert candidate.ms1_area is None
        assert candidate.ms1_height is None
        assert candidate.ms1_scan_support_score is None
        assert candidate.discovery_candidate_state == "review_only_orphan_nl"
        assert candidate.discovery_candidate_state not in {
            "ms1_feature_nl_supported",
            "ms1_feature_nl_rescued",
            "rejected_noise_or_outside_rt",
        }


def test_discovery_candidate_state_vocabulary_remains_exact() -> None:
    assert DISCOVERY_CANDIDATE_STATE_VALUES == (
        "ms1_feature_nl_supported",
        "ms1_feature_nl_rescued",
        "review_only_orphan_nl",
        "review_only_ambiguous_coisolation",
        "review_only_unresolved_peak_attribution",
        "rejected_noise_or_outside_rt",
    )
