import math
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Literal

from xic_extractor.discovery.evidence_config import (
    DEFAULT_EVIDENCE_PROFILE,
    DiscoveryEvidenceProfile,
)
from xic_extractor.settings_schema import normalize_resolver_mode

if TYPE_CHECKING:
    from xic_extractor.discovery.physical_fact_record import (
        DiscoveryCandidatePhysicalBinding,
    )

ReviewPriority = Literal["HIGH", "MEDIUM", "LOW"]
PrecursorMzBasis = Literal["scan_precursor", "product_plus_neutral_loss"]
GroupPrecursorMzBasis = Literal["scan_precursor", "product_plus_neutral_loss", "mixed"]
NeutralLossErrorBasis = Literal[
    "measured_scan_precursor_product",
    "configured_loss_inferred_precursor",
    "mixed",
]
DiscoveryCandidateState = Literal[
    "ms1_feature_nl_supported",
    "ms1_feature_nl_rescued",
    "review_only_orphan_nl",
    "review_only_ambiguous_coisolation",
    "rejected_noise_or_outside_rt",
]
DISCOVERY_CANDIDATE_STATE_VALUES: tuple[DiscoveryCandidateState, ...] = (
    "ms1_feature_nl_supported",
    "ms1_feature_nl_rescued",
    "review_only_orphan_nl",
    "review_only_ambiguous_coisolation",
    "rejected_noise_or_outside_rt",
)
MS1_FEATURE_BACKED_STATES: tuple[DiscoveryCandidateState, ...] = (
    "ms1_feature_nl_supported",
    "ms1_feature_nl_rescued",
)

DISCOVERY_CANDIDATE_REVIEW_COLUMNS = (
    "review_priority",
    "evidence_tier",
    "evidence_score",
    "ms2_support",
    "ms1_support",
    "rt_alignment",
    "candidate_id",
    "feature_family_id",
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
class NeutralLossProfile:
    tag: str
    neutral_loss_da: float


@dataclass(frozen=True)
class DiscoverySettings:
    neutral_loss_profile: NeutralLossProfile | None = None
    neutral_loss_profiles: tuple[NeutralLossProfile, ...] = ()
    selected_tag_names: tuple[str, ...] = ()
    tag_combine_mode: Literal["single", "union", "intersection"] = "single"
    nl_tolerance_ppm: float = 20.0
    precursor_mz_tolerance_ppm: float = 20.0
    product_mz_tolerance_ppm: float = 20.0
    product_search_ppm: float = 50.0
    ms2_precursor_tol_da: float = 1.1
    nl_min_intensity_ratio: float = 0.01
    seed_rt_gap_min: float = 0.20
    ms1_search_padding_min: float = 0.35
    rt_min: float = 0.0
    rt_max: float = 999.0
    resolver_mode: str = "local_minimum"
    evidence_profile: DiscoveryEvidenceProfile = DEFAULT_EVIDENCE_PROFILE
    enable_blank_qualification: bool = False
    blank_library_path: str | None = None
    blank_ppm_tol: float = 10.0
    blank_fold_change_threshold: float = 3.0

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "resolver_mode", normalize_resolver_mode(self.resolver_mode)
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
    observed_loss_error_ppm: float
    matched_tag_names: tuple[str, ...] = ()
    tag_evidence_json: str = "{}"
    scan_precursor_mz: float | None = None
    precursor_mz_basis: PrecursorMzBasis = "scan_precursor"
    acquisition_source_id: str = ""


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
    neutral_loss_mass_error_ppm: float
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
    """One ordered Discovery physical question bound to its acquisition."""

    work_ordinal: int
    acquisition_source_id: str
    sample_stem: str
    best_ms2_scan_id: int
    precursor_mz: float
    product_mz: float
    query_rt_min: float
    context_rt_start_min: float
    context_rt_end_min: float
    trace_coordinate_id: str
    acquired_trace_content_id: str
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
            "trace_coordinate_id",
            "acquired_trace_content_id",
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
    neutral_loss_mass_error_ppm: float
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
    feature_family_id: str = ""
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
        neutral_loss_mass_error_ppm: float,
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
            precursor_mz_basis=precursor_mz_basis,
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
        candidate.feature_family_id,
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
    physical_facts_json: Path | None = None
    physical_bindings: tuple["DiscoveryCandidatePhysicalBinding", ...] = ()


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
    precursor_mz_basis: GroupPrecursorMzBasis,
) -> DiscoveryCandidateState:
    if not ms1_peak_found:
        return "review_only_orphan_nl"
    if precursor_mz_basis in {"product_plus_neutral_loss", "mixed"}:
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
