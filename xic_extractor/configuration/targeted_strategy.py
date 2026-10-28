"""Target intent over shared read-only chemistry and mass policy."""

import tomllib
from dataclasses import asdict, dataclass
from functools import lru_cache
from importlib.resources import files

from xic_extractor.configuration.cid_nl_profile import (
    CidNlMassPolicy,
    NeutralLossProfile,
    PrecursorReferenceRequirement,
)
from xic_extractor.configuration.models import ConfigError
from xic_extractor.configuration.signal_processing_profile import (
    DEFAULT_SIGNAL_PROCESSING_PROFILE,
)
from xic_extractor.settings_schema import TARGETED_STRATEGY

TAG_PRESETS = {"dR": "dna_dr", "R": "rna_ribose", "MeR": "rna_mer"}
TARGETED_PRECURSOR_REFERENCE: PrecursorReferenceRequirement = "recorded_precursor"
TARGETED_REFERENCE_HALF_WINDOW_MIN = 2.0
# MS1 half-window around the injection's ISTD-measured mass offset. On a
# 54-sample urine batch, 86/92 NL-supported co-eluting analytes fell within
# 6 ppm of it, while neighbouring interferences sat 9-25 ppm away.
TARGETED_MS1_MASS_TOLERANCE_PPM = 6.0


@dataclass(frozen=True)
class TargetedTagProfile(CidNlMassPolicy):
    neutral_loss_profiles: tuple[NeutralLossProfile, ...] = ()


@lru_cache(maxsize=3)
def targeted_tag_profile(tag: str) -> TargetedTagProfile:
    if tag not in TAG_PRESETS:
        raise ConfigError(
            f"cid_nl_tag must be one of {', '.join(TAG_PRESETS)}; got {tag!r}"
        )
    # Read the same packaged chemistry data without importing orchestration facades.
    path = files("xic_extractor").joinpath(
        "presets", "data", TAG_PRESETS[tag] + ".toml"
    )
    payload = tomllib.loads(path.read_text(encoding="utf-8"))
    profile = payload["tag"][0]
    return TargetedTagProfile(
        neutral_loss_profiles=(NeutralLossProfile(profile["name"], profile["value"]),)
    )


def strategy_receipt() -> dict:
    return {
        "strategy": TARGETED_STRATEGY,
        "candidate_coverage": "reference_localized_observations_before_quality",
        "acquisition_coverage": "complete_acquisition_exact_mass",
        "expected_rt_usage": "bounded_candidate_scope_not_exact_apex",
        "reference_half_window_min": TARGETED_REFERENCE_HALF_WINDOW_MIN,
        "quantitation_policy": "acquired_core_and_flanking_background",
        "cid_nl_identity_precursor_reference": TARGETED_PRECURSOR_REFERENCE,
        "cid_nl_evidence_scope": "target_mass_and_physical_peak",
        "cid_nl_ranking_priority": "positive_support_only",
        "hmdc_fragment_identity": (
            "rna_only_peak_veto_mixed_peak_unquantified_missing_fragments_allow_backfill"
        ),
        "paired_istd_coelution": "istd_apex_plus_offset_within_istd_half_width",
        "ms1_mass_window": "median_istd_injection_offset_when_three_measured",
        "ms1_mass_tolerance_ppm": TARGETED_MS1_MASS_TOLERANCE_PPM,
        "paired_istd_selection": "local_method_evidence_before_quantitation",
        "uncalibrated_paired_istd_position": (
            "qualified_identity_and_coelution_rejection"
        ),
        "signal_processing": asdict(DEFAULT_SIGNAL_PROCESSING_PROFILE),
        "cid_nl_recipes": {
            tag: asdict(targeted_tag_profile(tag)) for tag in TAG_PRESETS
        },
    }
