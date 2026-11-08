"""Discovery method settings, independent of candidates and peak evaluation."""

from dataclasses import asdict, dataclass
from typing import Literal

from xic_extractor.configuration.cid_nl_profile import (
    DEFAULT_CID_NL_MASS_POLICY,
    NeutralLossProfile,
)
from xic_extractor.configuration.mass_recalibration import (
    MassRecalibration,
    resolve_mass_recalibration,
)
from xic_extractor.discovery.evidence_config import (
    DEFAULT_EVIDENCE_PROFILE,
    DiscoveryEvidenceProfile,
)


@dataclass(frozen=True)
class DiscoverySettings:
    neutral_loss_profile: NeutralLossProfile | None = None
    neutral_loss_profiles: tuple[NeutralLossProfile, ...] = ()
    selected_tag_names: tuple[str, ...] = ()
    tag_combine_mode: Literal["single", "union", "intersection"] = "single"
    nl_tolerance_ppm: float = DEFAULT_CID_NL_MASS_POLICY.nl_tolerance_ppm
    precursor_mz_tolerance_ppm: float = (
        DEFAULT_CID_NL_MASS_POLICY.precursor_mz_tolerance_ppm
    )
    product_mz_tolerance_ppm: float = (
        DEFAULT_CID_NL_MASS_POLICY.product_mz_tolerance_ppm
    )
    product_search_ppm: float = DEFAULT_CID_NL_MASS_POLICY.product_search_ppm
    ms2_precursor_tol_da: float = DEFAULT_CID_NL_MASS_POLICY.ms2_precursor_tol_da
    nl_min_intensity_ratio: float = DEFAULT_CID_NL_MASS_POLICY.nl_min_intensity_ratio
    seed_rt_gap_min: float = 0.20
    ms1_search_padding_min: float = 0.35
    rt_min: float | None = None
    rt_max: float | None = None
    resolver_mode: str = "local_minimum"
    evidence_profile: DiscoveryEvidenceProfile = DEFAULT_EVIDENCE_PROFILE
    enable_blank_qualification: bool = False
    blank_library_path: str | None = None
    blank_ppm_tol: float = 10.0
    blank_fold_change_threshold: float = 3.0
    mass_recalibration: MassRecalibration = MassRecalibration.EASY_IC_RESTORE

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "mass_recalibration",
            resolve_mass_recalibration(self.mass_recalibration),
        )
        profiles = self.neutral_loss_profiles
        # The singular field also aliases profiles[0] after normalization.
        # dataclasses.replace carries both fields; do not append that alias again.
        if self.neutral_loss_profile is not None and (
            not profiles or self.neutral_loss_profile != profiles[0]
        ):
            profiles = (self.neutral_loss_profile,) + profiles
        if not profiles:
            raise ValueError("at least one neutral-loss profile is required")
        object.__setattr__(self, "neutral_loss_profiles", profiles)
        object.__setattr__(self, "neutral_loss_profile", profiles[0])
        if not self.selected_tag_names:
            object.__setattr__(
                self,
                "selected_tag_names",
                tuple(profile.tag for profile in profiles),
            )


def discovery_settings_identity_fields(
    settings: DiscoverySettings,
) -> dict[str, object]:
    """Every Discovery setting; the vendor-mass policy is recorded by omission."""
    fields = asdict(settings)
    if settings.mass_recalibration is MassRecalibration.NONE:
        del fields["mass_recalibration"]
    else:
        fields["mass_recalibration"] = settings.mass_recalibration.value
    return fields
