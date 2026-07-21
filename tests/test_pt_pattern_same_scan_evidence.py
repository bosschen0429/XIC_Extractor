from __future__ import annotations

import numpy as np
import pytest

from xic_extractor.discovery.pt_pattern_same_scan_evidence import (
    CandidateFeatureClaims,
    PtPatternSeed,
    SameScanSettings,
    build_conflict_components,
    evaluate_same_scan_envelope,
    resolve_conflict_components,
)
from xic_extractor.ms1_scan_index import MS1Scan


def _scan(
    scan_number: int,
    rt_min: float,
    *,
    base: float,
    plus1: float,
    plus2: float,
    plus4: float | None,
) -> MS1Scan:
    masses = [500.0, 501.002111, 502.002271]
    intensities = [base, plus1, plus2]
    if plus4 is not None:
        masses.append(504.005213)
        intensities.append(plus4)
    return MS1Scan(
        scan_number=scan_number,
        rt=rt_min,
        masses=np.asarray(masses, dtype=np.float64),
        intensities=np.asarray(intensities, dtype=np.float64),
    )


def test_same_scan_evidence_separates_strict_full_from_censored_plus4() -> None:
    index = (
        _scan(100, 10.00, base=90_000, plus1=120_000, plus2=80_000, plus4=12_000),
        _scan(101, 10.02, base=110_000, plus1=150_000, plus2=90_000, plus4=20_000),
        _scan(102, 10.04, base=80_000, plus1=100_000, plus2=70_000, plus4=10_000),
    )
    seed = PtPatternSeed(seed_id="known-cp", source_set="known", base_mz=500.0)

    result = evaluate_same_scan_envelope(
        index,
        seed,
        SameScanSettings(noise_level=25_000.0, minimum_joint_scans=2),
    )

    assert result.core_above_noise_scan_count == 3
    assert result.full_above_noise_scan_count == 0
    assert result.longest_consecutive_core_scans == 3
    assert result.longest_consecutive_full_scans == 0
    assert result.strict_full_supported is False
    assert result.core_plus4_conditional_supported is True
    assert result.plus4_support_status == "censored_below_noise"
    assert result.shared_apex_scan_number == 101
    assert result.shared_apex_base_over_plus1 == 110_000 / 150_000
    assert result.shared_apex_plus2_over_plus1 == 90_000 / 150_000
    assert result.shared_apex_plus4_over_plus1 == 20_000 / 150_000


def test_same_scan_evidence_requires_consecutive_full_scans_for_strict_support() -> (
    None
):
    index = (
        _scan(100, 10.00, base=90_000, plus1=120_000, plus2=80_000, plus4=40_000),
        _scan(101, 10.02, base=90_000, plus1=120_000, plus2=80_000, plus4=None),
        _scan(102, 10.04, base=90_000, plus1=120_000, plus2=80_000, plus4=40_000),
    )

    result = evaluate_same_scan_envelope(
        index,
        PtPatternSeed(seed_id="candidate", source_set="candidate", base_mz=500.0),
        SameScanSettings(noise_level=25_000.0, minimum_joint_scans=2),
    )

    assert result.full_above_noise_scan_count == 2
    assert result.longest_consecutive_full_scans == 1
    assert result.strict_full_supported is False
    assert result.core_plus4_conditional_supported is True
    assert result.plus4_support_status == "partial_above_noise"


def test_same_scan_evidence_separates_missing_from_below_noise_plus4() -> None:
    index = (
        _scan(100, 10.00, base=90_000, plus1=120_000, plus2=80_000, plus4=10_000),
        _scan(101, 10.02, base=90_000, plus1=120_000, plus2=80_000, plus4=None),
    )

    result = evaluate_same_scan_envelope(
        index,
        PtPatternSeed(seed_id="mixed", source_set="candidate", base_mz=500.0),
        SameScanSettings(noise_level=25_000.0, minimum_joint_scans=2),
    )

    assert result.plus4_support_status == "mixed_missing_and_below_noise"


@pytest.mark.parametrize(
    "settings",
    (
        SameScanSettings(mass_tolerance_ppm=float("nan")),
        SameScanSettings(noise_level=float("inf")),
        SameScanSettings(mass_tolerance_ppm=True),
    ),
)
def test_same_scan_evidence_rejects_invalid_thresholds(
    settings: SameScanSettings,
) -> None:
    with pytest.raises(ValueError, match="finite|real number"):
        evaluate_same_scan_envelope(
            (),
            PtPatternSeed(seed_id="invalid", source_set="candidate", base_mz=500.0),
            settings,
        )


def test_same_scan_evidence_rejects_empty_index() -> None:
    with pytest.raises(ValueError, match="at least one MS1 scan"):
        evaluate_same_scan_envelope(
            (),
            PtPatternSeed(seed_id="empty", source_set="candidate", base_mz=500.0),
            SameScanSettings(),
        )


def test_same_scan_evidence_respects_candidate_rt_window() -> None:
    index = (
        _scan(100, 9.00, base=90_000, plus1=120_000, plus2=80_000, plus4=40_000),
        _scan(101, 10.00, base=90_000, plus1=120_000, plus2=80_000, plus4=40_000),
        _scan(102, 10.02, base=90_000, plus1=120_000, plus2=80_000, plus4=40_000),
    )
    seed = PtPatternSeed(
        seed_id="bounded",
        source_set="candidate",
        base_mz=500.0,
        rt_center_min=10.0,
        rt_half_window_sec=3.0,
    )

    result = evaluate_same_scan_envelope(
        index,
        seed,
        SameScanSettings(noise_level=25_000.0, minimum_joint_scans=2),
    )

    assert result.selected_scan_count == 2
    assert result.longest_consecutive_full_scans == 2
    assert result.strict_full_supported is True


def test_conflict_components_keep_shared_feature_hypotheses_dependent() -> None:
    claims = (
        CandidateFeatureClaims("candidate-a", ("f1", "f2", "f3", "f4")),
        CandidateFeatureClaims("candidate-b", ("f4", "f5", "f6", "f7")),
        CandidateFeatureClaims("candidate-c", ("f8", "f9", "f10", "f11")),
    )

    components = build_conflict_components(claims)

    assert len(components) == 1
    assert components[0].candidate_ids == ("candidate-a", "candidate-b")
    assert components[0].shared_feature_ids == ("f4",)

    resolved = resolve_conflict_components(
        components,
        strict_support_by_candidate={"candidate-a": True, "candidate-b": False},
        core_support_by_candidate={"candidate-a": True, "candidate-b": True},
    )

    assert resolved[0].resolution_status == "unique_strict_support"
    assert resolved[0].preferred_candidate_id == "candidate-a"


@pytest.mark.parametrize(
    "claims",
    (
        (
            CandidateFeatureClaims("candidate-a", ("f1", "f2")),
            CandidateFeatureClaims("candidate-a", ("f1", "f2")),
        ),
        (
            CandidateFeatureClaims("candidate-a", ("f1", "f2")),
            CandidateFeatureClaims("candidate-a", ("f3", "f4")),
        ),
        (
            CandidateFeatureClaims("candidate-a", ("f3", "f4")),
            CandidateFeatureClaims("candidate-a", ("f1", "f2")),
        ),
    ),
)
def test_conflict_components_reject_duplicate_candidate_identity(
    claims: tuple[CandidateFeatureClaims, ...],
) -> None:
    with pytest.raises(ValueError, match="duplicate candidate_id: candidate-a"):
        build_conflict_components(claims)


def test_conflict_components_reject_blank_candidate_identity() -> None:
    with pytest.raises(ValueError, match="candidate_id"):
        build_conflict_components((CandidateFeatureClaims("", ("f1", "f2")),))


def test_conflict_components_keep_feature_claims_set_like() -> None:
    assert (
        build_conflict_components(
            (CandidateFeatureClaims("candidate-a", ("f1", "f1")),)
        )
        == ()
    )


def test_conflict_resolution_keeps_multiple_supported_hypotheses_ambiguous() -> None:
    components = build_conflict_components(
        (
            CandidateFeatureClaims("candidate-a", ("f1", "f2")),
            CandidateFeatureClaims("candidate-b", ("f2", "f3")),
        )
    )

    resolved = resolve_conflict_components(
        components,
        strict_support_by_candidate={"candidate-a": True, "candidate-b": True},
        core_support_by_candidate={"candidate-a": True, "candidate-b": True},
    )

    assert resolved[0].resolution_status == "ambiguous_multiple_strict_support"
    assert resolved[0].preferred_candidate_id is None


def test_core_only_support_never_nominates_a_discovery_preferred_hypothesis() -> None:
    components = build_conflict_components(
        (
            CandidateFeatureClaims("candidate-a", ("f1", "f2")),
            CandidateFeatureClaims("candidate-b", ("f2", "f3")),
        )
    )

    resolved = resolve_conflict_components(
        components,
        strict_support_by_candidate={"candidate-a": False, "candidate-b": False},
        core_support_by_candidate={"candidate-a": True, "candidate-b": False},
    )

    assert resolved[0].resolution_status == "unique_core_support"
    assert resolved[0].preferred_candidate_id is None
