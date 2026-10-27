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
# Selection and final identity use the same method-relative paired-ISTD window.
TARGETED_PAIRED_RT_TOLERANCE_MIN = 1.0


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
        "candidate_coverage": "complete_acquisition_exact_mass",
        "expected_rt_usage": "identity_reference_not_acquisition_window",
        "cid_nl_identity_precursor_reference": TARGETED_PRECURSOR_REFERENCE,
        "cid_nl_evidence_scope": "target_mass_and_physical_peak",
        "cid_nl_ranking_priority": "positive_support_only",
        "hmdc_fragment_identity": "competing_rna_veto_missing_fragments_allow_backfill",
        "paired_istd_rt_tolerance_min": TARGETED_PAIRED_RT_TOLERANCE_MIN,
        "paired_istd_selection": "qualified_local_candidates_before_chemical_ranking",
        "uncalibrated_paired_istd_position": (
            "qualified_identity_and_coelution_rejection"
        ),
        "signal_processing": asdict(DEFAULT_SIGNAL_PROCESSING_PROFILE),
        "cid_nl_recipes": {
            tag: asdict(targeted_tag_profile(tag)) for tag in TAG_PRESETS
        },
    }
