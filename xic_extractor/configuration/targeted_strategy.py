"""Target intent over shared read-only chemistry and mass policy."""

from dataclasses import asdict, dataclass
from functools import lru_cache

from xic_extractor.configuration.cid_nl_profile import (
    DEFAULT_CID_NL_MASS_POLICY,
    CidNlMassPolicy,
    NeutralLossProfile,
    PrecursorReferenceRequirement,
)
from xic_extractor.configuration.signal_processing_profile import (
    DEFAULT_SIGNAL_PROCESSING_PROFILE,
    LOCALIZED_VALLEY_MIN_DROP_FRACTION,
)
from xic_extractor.settings_schema import TARGETED_STRATEGY

TARGETED_PRECURSOR_REFERENCE: PrecursorReferenceRequirement = "recorded_precursor"


@dataclass(frozen=True)
class TargetedTagProfile(CidNlMassPolicy):
    neutral_loss_profiles: tuple[NeutralLossProfile, ...] = ()


@lru_cache(maxsize=64)
def targeted_loss_profile(neutral_loss_da: float) -> TargetedTagProfile:
    """The shared CID-NL mass policy applied to one target's neutral loss."""
    return TargetedTagProfile(
        neutral_loss_profiles=(
            NeutralLossProfile(f"NL:{neutral_loss_da:.4f}", neutral_loss_da),
        )
    )


def strategy_receipt() -> dict:
    return {
        "strategy": TARGETED_STRATEGY,
        "candidate_coverage": "complete_acquisition_exact_mass",
        "expected_rt_usage": "identity_reference_not_acquisition_window",
        "ms1_only": {
            "selection": "contextual_provisional_mapping",
            "integration": "valley_bounded_envelope_with_tenth_height_outer_return",
            "min_valley_drop_fraction": LOCALIZED_VALLEY_MIN_DROP_FRACTION,
            "min_valley_duration_min": (
                DEFAULT_SIGNAL_PROCESSING_PROFILE.boundary_min_sigma_rt
            ),
            "same_mass_localization": (
                "valley_and_reference_order_with_shared_region_review"
            ),
            "shape_quality": "review_not_numeric_veto",
        },
        "cid_nl_identity_precursor_reference": TARGETED_PRECURSOR_REFERENCE,
        "cid_nl_evidence_scope": "target_mass_and_physical_peak",
        "cid_nl_ranking_priority": "positive_support_only",
        "uncalibrated_paired_istd_position": (
            "qualified_identity_and_coelution_rejection"
        ),
        "signal_processing": asdict(DEFAULT_SIGNAL_PROCESSING_PROFILE),
        "cid_nl_mass_policy": asdict(DEFAULT_CID_NL_MASS_POLICY),
    }
