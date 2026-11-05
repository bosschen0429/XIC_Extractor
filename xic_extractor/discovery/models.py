import math
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Literal

from xic_extractor.configuration.cid_nl_profile import DEFAULT_CID_NL_MASS_POLICY
from xic_extractor.configuration.cid_nl_profile import (
    NeutralLossProfile as NeutralLossProfile,
)
from xic_extractor.configuration.mass_recalibration import (
    MassRecalibration,
    resolve_mass_recalibration,
)
from xic_extractor.discovery.evidence_config import (
    DEFAULT_EVIDENCE_PROFILE,
    DiscoveryEvidenceProfile,
)
from xic_extractor.ms2_scan_models import Ms2PrecursorMetadata
from xic_extractor.precursor_evidence import (
    Ms1PrecursorObservation,
    PrecursorLossAssessment,
    assess_precursor_loss,
    supports_direct_nl,
)
from xic_extractor.precursor_evidence import PrecursorMzBasis as PrecursorMzBasis

if TYPE_CHECKING:
    from xic_extractor.discovery.physical_fact_record import (
        DiscoveryCandidatePhysicalBinding,
    )
    from xic_extractor.processing_identity import FileContentFingerprint

ReviewPriority = Literal["HIGH", "MEDIUM", "LOW"]
GroupPrecursorMzBasis = Literal[
    "scan_precursor", "scan_monoisotopic", "product_plus_neutral_loss", "mixed"
]
NeutralLossErrorBasis = Literal[
    "measured_scan_precursor_product",
    "recorded_monoisotopic_precursor_product",
    "configured_loss_inferred_precursor",
    "mixed",
]
DiscoveryCandidateState = Literal[
    "ms1_feature_nl_supported",
    "ms1_feature_nl_rescued",
    "review_only_orphan_nl",
    "review_only_ambiguous_coisolation",
    "review_only_unresolved_peak_attribution",
    "rejected_noise_or_outside_rt",
]
DISCOVERY_CANDIDATE_STATE_VALUES: tuple[DiscoveryCandidateState, ...] = (
    "ms1_feature_nl_supported",
    "ms1_feature_nl_rescued",
    "review_only_orphan_nl",
    "review_only_ambiguous_coisolation",
    "review_only_unresolved_peak_attribution",
    "rejected_noise_or_outside_rt",
)
MS1_FEATURE_BACKED_STATES: tuple[DiscoveryCandidateState, ...] = (
    "ms1_feature_nl_supported",
    "ms1_feature_nl_rescued",
)
# A measured MS1 peak alone does not authorize CID-NL Alignment work.
CID_NL_ALIGNMENT_STATES: tuple[DiscoveryCandidateState, ...] = (
    "ms1_feature_nl_supported",
)

DISCOVERY_CANDIDATE_REVIEW_COLUMNS = (
    "review_priority",
    "evidence_tier",
    "evidence_score",
    "ms2_support",
    "ms1_support",
    "rt_alignment",
    "candidate_id",
    "feature_id",
    "feature_family_size",
    "precursor_mz",
    "product_mz",
    "observed_neutral_loss_da",
    "best_seed_rt",
    "seed_event_count",
    "ms1_peak_found",
    "ms1_apex_rt",
    "ms1_area",
    "ms2_product_max_intensity",
    "reason",
)

# Compatibility alias. This is the review-first section of
# discovery_candidates.csv, not the brief discovery_review.csv schema.
DISCOVERY_REVIEW_COLUMNS = DISCOVERY_CANDIDATE_REVIEW_COLUMNS

DISCOVERY_PROVENANCE_COLUMNS = (
    "raw_file",
    "sample_stem",
    "best_ms2_scan_id",
    "seed_scan_ids",
    "neutral_loss_tag",
    "configured_neutral_loss_da",
    "neutral_loss_mass_error_ppm",
    "neutral_loss_error_basis",
    "precursor_mz_basis",
    "scan_precursor_mz",
    "scan_precursor_delta_da",
    "max_scan_precursor_abs_delta_da",
    "rt_seed_min",
    "rt_seed_max",
    "ms1_search_rt_min",
    "ms1_search_rt_max",
    "ms1_seed_delta_min",
    "ms1_peak_rt_start",
    "ms1_peak_rt_end",
    "ms1_height",
    "ms1_apex_mz",
    "ms1_trace_quality",
    "ms1_scan_support_score",
    "selected_tag_count",
    "matched_tag_count",
    "matched_tag_names",
    "primary_tag_name",
    "tag_combine_mode",
    "tag_intersection_status",
    "tag_evidence_json",
)

DISCOVERY_SUCCESSOR_COLUMNS = (
    "discovery_candidate_state",
    "ms1_feature_row_id",
)

DISCOVERY_CANDIDATE_COLUMNS = (
    DISCOVERY_CANDIDATE_REVIEW_COLUMNS
    + DISCOVERY_SUCCESSOR_COLUMNS
    + DISCOVERY_PROVENANCE_COLUMNS
    + ("is_provisional",)
)

DISCOVERY_BRIEF_COLUMNS = (
    "review_priority",
    "evidence_tier",
    "evidence_score",
    "ms2_support",
    "ms1_support",
    "rt_alignment",
    "candidate_id",
    "precursor_mz",
    "best_seed_rt",
    "ms1_area",
    "seed_event_count",
    "neutral_loss_tag",
    "matched_tag_names",
    "matched_tag_count",
    "tag_intersection_status",
    "review_note",
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


@dataclass(frozen=True)
class DiscoverySeed:
    raw_file: Path
    sample_stem: str
    scan_number: int
    rt: float
    precursor_mz: float
    product_mz: float
    product_intensity: float
    neutral_loss_tag: str
    configured_neutral_loss_da: float
    observed_neutral_loss_da: float
    observed_loss_error_ppm: float | None
    matched_tag_names: tuple[str, ...] = ()
    tag_evidence_json: str = "{}"
    scan_precursor_mz: float | None = None
    precursor_mz_basis: PrecursorMzBasis = "scan_precursor"
    precursor_metadata: Ms2PrecursorMetadata | None = None
    acquisition_source_id: str = ""
    ms1_precursor_observation: Ms1PrecursorObservation | None = None

    def assess_precursor_loss(self, tolerance_ppm: float) -> PrecursorLossAssessment:
        return assess_precursor_loss(
            basis=self.precursor_mz_basis,
            precursor_mz=self.precursor_mz,
            product_mz=self.product_mz,
            neutral_loss_da=self.configured_neutral_loss_da,
            observed_loss_da=self.observed_neutral_loss_da,
            observed_loss_error_ppm=self.observed_loss_error_ppm,
            tolerance_ppm=tolerance_ppm,
            selected_mz=self.scan_precursor_mz,
            metadata=self.precursor_metadata,
            ms1_observation=self.ms1_precursor_observation,
        )

    @property
    def supports_direct_nl(self) -> bool:
        return supports_direct_nl(
            self.precursor_mz_basis,
            self.precursor_mz,
            self.scan_precursor_mz,
            self.precursor_metadata,
        )


@dataclass(frozen=True)
class DiscoverySeedGroup:
    raw_file: Path
    sample_stem: str
    seeds: tuple[DiscoverySeed, ...]
    neutral_loss_tag: str
    configured_neutral_loss_da: float
    precursor_mz: float
    product_mz: float
    observed_neutral_loss_da: float
    neutral_loss_mass_error_ppm: float | None
    rt_seed_min: float
    rt_seed_max: float
    matched_tag_names: tuple[str, ...] = ()
    tag_evidence_json: str = "{}"
    precursor_mz_basis: GroupPrecursorMzBasis = "scan_precursor"
    neutral_loss_error_basis: NeutralLossErrorBasis = "measured_scan_precursor_product"
    scan_precursor_mz: float | None = None
    scan_precursor_delta_da: float | None = None
    max_scan_precursor_abs_delta_da: float | None = None


@dataclass(frozen=True, slots=True)
class DiscoveryPhysicalWorkItem:
    """CID-NL sidecar projection of completed physical work and method identity.

    This persisted schema serves Discovery replay and Alignment. Shared physical
    capture takes method-independent search coordinates, not this projection.
    """

    work_ordinal: int
    acquisition_source_id: str
    sample_stem: str
    best_ms2_scan_id: int
    precursor_mz: float
    product_mz: float
    query_rt_min: float
    context_rt_start_min: float
    context_rt_end_min: float
    xic_mz: float
    xic_rt_min: float
    xic_rt_max: float
    xic_ppm_tol: float

    def __post_init__(self) -> None:
        if (
            isinstance(self.work_ordinal, bool)
            or not isinstance(self.work_ordinal, int)
            or self.work_ordinal < 0
        ):
            raise ValueError("work_ordinal must be a non-negative integer")
        for field in (
            "acquisition_source_id",
            "sample_stem",
        ):
            if not getattr(self, field):
                raise ValueError(f"{field} must be non-empty text")
        if (
            isinstance(self.best_ms2_scan_id, bool)
            or not isinstance(self.best_ms2_scan_id, int)
            or self.best_ms2_scan_id < 0
        ):
            raise ValueError("best_ms2_scan_id must be a non-negative integer")
        for field in (
            "precursor_mz",
            "product_mz",
            "query_rt_min",
            "context_rt_start_min",
            "context_rt_end_min",
            "xic_mz",
            "xic_rt_min",
            "xic_rt_max",
            "xic_ppm_tol",
        ):
            value = getattr(self, field)
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise TypeError(f"{field} must be numeric")
            if not math.isfinite(float(value)):
                raise ValueError(f"{field} must be finite")
        if (
            self.precursor_mz <= 0.0
            or self.product_mz <= 0.0
            or self.xic_mz <= 0.0
            or self.xic_ppm_tol <= 0.0
        ):
            raise ValueError("physical work m/z values must be positive")
        if self.context_rt_start_min < 0.0:
            raise ValueError("context_rt_start_min must be non-negative")
        if self.context_rt_start_min > self.context_rt_end_min:
            raise ValueError("physical work context has an invalid RT window")
        if self.xic_rt_min < 0.0 or self.xic_rt_min > self.xic_rt_max:
            raise ValueError("physical work XIC request has an invalid RT window")
        if not self.xic_rt_min <= self.query_rt_min <= self.xic_rt_max:
            raise ValueError("physical work query RT must be inside its XIC request")

    @property
    def candidate_id(self) -> str:
        return build_discovery_candidate_id(
            sample_stem=self.sample_stem,
            best_ms2_scan_id=self.best_ms2_scan_id,
            precursor_mz=self.precursor_mz,
            product_mz=self.product_mz,
        )

    @property
    def selection_subject_id(self) -> str:
        return build_discovery_physical_selection_subject_id(
            sample_stem=self.sample_stem,
            best_ms2_scan_id=self.best_ms2_scan_id,
            precursor_mz=self.precursor_mz,
            product_mz=self.product_mz,
        )


@dataclass(frozen=True)
class DiscoveryCandidate:
    review_priority: ReviewPriority
    evidence_score: int
    evidence_tier: str
    ms2_support: str
    ms1_support: str
    rt_alignment: str
    candidate_id: str
    precursor_mz: float
    product_mz: float
    observed_neutral_loss_da: float
    best_seed_rt: float
    seed_event_count: int
    ms1_peak_found: bool
    ms1_apex_rt: float | None
    ms1_area: float | None
    ms2_product_max_intensity: float
    reason: str
    raw_file: Path
    sample_stem: str
    best_ms2_scan_id: int
    seed_scan_ids: tuple[int, ...]
    neutral_loss_tag: str
    configured_neutral_loss_da: float
    neutral_loss_mass_error_ppm: float | None
    rt_seed_min: float
    rt_seed_max: float
    ms1_search_rt_min: float
    ms1_search_rt_max: float
    ms1_seed_delta_min: float | None
    ms1_peak_rt_start: float | None
    ms1_peak_rt_end: float | None
    ms1_height: float | None
    ms1_trace_quality: str
    ms1_scan_support_score: float | None = None
    neutral_loss_error_basis: NeutralLossErrorBasis = "measured_scan_precursor_product"
    precursor_mz_basis: GroupPrecursorMzBasis = "scan_precursor"
    scan_precursor_mz: float | None = None
    scan_precursor_delta_da: float | None = None
    max_scan_precursor_abs_delta_da: float | None = None
    feature_id: str = ""
    feature_family_size: int = 1
    selected_tag_count: int = 1
    matched_tag_count: int = 1
    matched_tag_names: tuple[str, ...] = ()
    primary_tag_name: str = ""
    tag_combine_mode: Literal["single", "union", "intersection"] = "single"
    tag_intersection_status: Literal[
        "not_required",
        "complete",
        "incomplete",
    ] = "not_required"
    tag_evidence_json: str = "{}"
    discovery_candidate_state: DiscoveryCandidateState = "review_only_orphan_nl"
    ms1_feature_row_id: str = ""
    is_provisional: bool = False
    # Qualified MS1 ion reading; the original precursor remains the MS2 seed key.
    ms1_apex_mz: float | None = None

    @property
    def physical_mz(self) -> float:
        """Mass for physical capture and grouping, independent of MS2 receipts."""
        return self.precursor_mz if self.ms1_apex_mz is None else self.ms1_apex_mz

    @classmethod
    def from_values(
        cls,
        *,
        raw_file: Path,
        sample_stem: str,
        precursor_mz: float,
        product_mz: float,
        observed_neutral_loss_da: float,
        best_seed: DiscoverySeed,
        seed_scan_ids: tuple[int, ...],
        neutral_loss_tag: str,
        configured_neutral_loss_da: float,
        neutral_loss_mass_error_ppm: float | None,
        rt_seed_min: float,
        rt_seed_max: float,
        ms1_search_rt_min: float,
        ms1_search_rt_max: float,
        ms1_seed_delta_min: float | None,
        ms1_peak_rt_start: float | None,
        ms1_peak_rt_end: float | None,
        ms1_height: float | None,
        ms1_trace_quality: str,
        ms1_scan_support_score: float | None = None,
        neutral_loss_error_basis: NeutralLossErrorBasis = (
            "measured_scan_precursor_product"
        ),
        precursor_mz_basis: GroupPrecursorMzBasis = "scan_precursor",
        scan_precursor_mz: float | None = None,
        scan_precursor_delta_da: float | None = None,
        max_scan_precursor_abs_delta_da: float | None = None,
        seed_event_count: int,
        ms1_peak_found: bool,
        ms1_apex_rt: float | None,
        ms1_area: float | None,
        ms2_product_max_intensity: float,
        review_priority: ReviewPriority,
        reason: str,
        is_provisional: bool = False,
    ) -> "DiscoveryCandidate":
        discovery_candidate_state = assign_discovery_candidate_state(
            ms1_peak_found=ms1_peak_found,
            has_nl_support=(
                best_seed.supports_direct_nl
                and precursor_mz_basis in {"scan_precursor", "scan_monoisotopic"}
            ),
        )
        return cls(
            review_priority=review_priority,
            evidence_score=0,
            evidence_tier="E",
            ms2_support="weak",
            ms1_support="missing",
            rt_alignment="missing",
            candidate_id=build_discovery_candidate_id(
                sample_stem=sample_stem,
                best_ms2_scan_id=best_seed.scan_number,
                precursor_mz=precursor_mz,
                product_mz=product_mz,
            ),
            precursor_mz=precursor_mz,
            product_mz=product_mz,
            observed_neutral_loss_da=observed_neutral_loss_da,
            best_seed_rt=best_seed.rt,
            seed_event_count=seed_event_count,
            ms1_peak_found=ms1_peak_found,
            ms1_apex_rt=ms1_apex_rt,
            ms1_area=ms1_area,
            ms2_product_max_intensity=ms2_product_max_intensity,
            reason=reason,
            raw_file=raw_file,
            sample_stem=sample_stem,
            best_ms2_scan_id=best_seed.scan_number,
            seed_scan_ids=seed_scan_ids,
            neutral_loss_tag=neutral_loss_tag,
            configured_neutral_loss_da=configured_neutral_loss_da,
            neutral_loss_mass_error_ppm=neutral_loss_mass_error_ppm,
            rt_seed_min=rt_seed_min,
            rt_seed_max=rt_seed_max,
            ms1_search_rt_min=ms1_search_rt_min,
            ms1_search_rt_max=ms1_search_rt_max,
            ms1_seed_delta_min=ms1_seed_delta_min,
            ms1_peak_rt_start=ms1_peak_rt_start,
            ms1_peak_rt_end=ms1_peak_rt_end,
            ms1_height=ms1_height,
            ms1_trace_quality=ms1_trace_quality,
            ms1_scan_support_score=ms1_scan_support_score,
            neutral_loss_error_basis=neutral_loss_error_basis,
            precursor_mz_basis=precursor_mz_basis,
            scan_precursor_mz=scan_precursor_mz,
            scan_precursor_delta_da=scan_precursor_delta_da,
            max_scan_precursor_abs_delta_da=max_scan_precursor_abs_delta_da,
            selected_tag_count=1,
            matched_tag_count=1,
            matched_tag_names=(neutral_loss_tag,),
            primary_tag_name=neutral_loss_tag,
            tag_combine_mode="single",
            tag_intersection_status="not_required",
            tag_evidence_json=best_seed.tag_evidence_json,
            discovery_candidate_state=discovery_candidate_state,
            ms1_feature_row_id=build_ms1_feature_row_id(
                sample_stem=sample_stem,
                neutral_loss_tag=neutral_loss_tag,
                precursor_mz=precursor_mz,
                best_seed_rt=best_seed.rt,
                ms1_peak_found=ms1_peak_found,
                ms1_apex_rt=ms1_apex_rt,
                ms1_peak_rt_start=ms1_peak_rt_start,
                ms1_peak_rt_end=ms1_peak_rt_end,
            ),
            is_provisional=is_provisional,
        )


_DISCOVERY_PRIORITY_RANK = {"HIGH": 0, "MEDIUM": 1, "LOW": 2}


def discovery_candidate_output_sort_key(
    candidate: DiscoveryCandidate,
) -> tuple[object, ...]:
    """Canonical Discovery candidate order shared by outputs and handoffs."""

    area_is_missing = candidate.ms1_area is None
    area_desc = 0.0 if candidate.ms1_area is None else -candidate.ms1_area
    return (
        _DISCOVERY_PRIORITY_RANK.get(
            candidate.review_priority,
            len(_DISCOVERY_PRIORITY_RANK),
        ),
        -candidate.evidence_score,
        -candidate.feature_family_size,
        candidate.feature_id,
        -candidate.seed_event_count,
        -candidate.ms2_product_max_intensity,
        area_is_missing,
        area_desc,
        candidate.best_seed_rt,
    )


@dataclass(frozen=True)
class DiscoveryRunOutputs:
    candidates_csv: Path
    review_csv: Path
    physical_facts_json: Path
    batch_index_csv: Path | None = None
    physical_bindings: tuple["DiscoveryCandidatePhysicalBinding", ...] = ()
    physical_facts_fingerprint: "FileContentFingerprint | None" = None


@dataclass(frozen=True)
class DiscoveryBatchOutputs:
    batch_index_csv: Path
    per_sample: tuple[DiscoveryRunOutputs, ...]


def build_discovery_candidate_id(
    *,
    sample_stem: str,
    best_ms2_scan_id: int,
    precursor_mz: float,
    product_mz: float,
) -> str:
    return (
        f"{sample_stem}#{best_ms2_scan_id}"
        f"@mz{_format_id_mz(precursor_mz)}_p{_format_id_mz(product_mz)}"
    )


def build_discovery_physical_selection_subject_id(
    *,
    sample_stem: str,
    best_ms2_scan_id: int,
    precursor_mz: float,
    product_mz: float,
) -> str:
    return (
        "discovery-physical-work:"
        f"{sample_stem}:{best_ms2_scan_id}:"
        f"{float(precursor_mz).hex()}:{float(product_mz).hex()}"
    )


def assign_discovery_candidate_state(
    *,
    ms1_peak_found: bool,
    has_nl_support: bool,
) -> DiscoveryCandidateState:
    if not ms1_peak_found:
        return "review_only_orphan_nl"
    if not has_nl_support:
        return "ms1_feature_nl_rescued"
    return "ms1_feature_nl_supported"


def build_ms1_feature_row_id(
    *,
    sample_stem: str,
    neutral_loss_tag: str,
    precursor_mz: float,
    best_seed_rt: float,
    ms1_peak_found: bool,
    ms1_apex_rt: float | None,
    ms1_peak_rt_start: float | None,
    ms1_peak_rt_end: float | None,
) -> str:
    if not ms1_peak_found:
        return ""
    return "|".join(
        (
            sample_stem,
            neutral_loss_tag,
            _format_id_mz(precursor_mz),
            _format_id_mz(
                _ms1_feature_rt_identity(
                    best_seed_rt=best_seed_rt,
                    ms1_apex_rt=ms1_apex_rt,
                    ms1_peak_rt_start=ms1_peak_rt_start,
                    ms1_peak_rt_end=ms1_peak_rt_end,
                )
            ),
        )
    )


def _ms1_feature_rt_identity(
    *,
    best_seed_rt: float,
    ms1_apex_rt: float | None,
    ms1_peak_rt_start: float | None,
    ms1_peak_rt_end: float | None,
) -> float:
    if ms1_apex_rt is not None:
        return ms1_apex_rt
    if ms1_peak_rt_start is not None and ms1_peak_rt_end is not None:
        return (ms1_peak_rt_start + ms1_peak_rt_end) / 2.0
    return best_seed_rt


def _format_id_mz(value: float) -> str:
    return f"{value:.6f}".rstrip("0").rstrip(".")
