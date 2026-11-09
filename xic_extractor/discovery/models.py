import math
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Literal

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
        DiscoveryPeakPhysicalBinding,
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
AdmissionReason = Literal[
    "admitted",
    "system_background",
    "doubly_charged_isotope",
    "requires_overlap_review",
    "no_attached_event",
]

DISCOVERY_PEAK_COLUMNS = (
    "review_priority",
    "admitted",
    "admission_reason",
    "evidence_tier",
    "evidence_score",
    "ms2_support",
    "ms1_support",
    "rt_alignment",
    "peak_id",
    "channel_mz",
    "precursor_mz",
    "product_mz",
    "observed_neutral_loss_da",
    "ms1_apex_rt",
    "ms1_area",
    "attached_event_count",
    "best_seed_rt",
    "ms2_product_max_intensity",
    "reason",
    "requires_overlap_review",
    "doubly_charged_isotope",
    "blank_background",
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
    "is_provisional",
)

DISCOVERY_EVENT_COLUMNS = (
    "sample_stem",
    "ms2_scan_number",
    "ms2_rt",
    "neutral_loss_tag",
    "product_mz",
    "product_intensity",
    "precursor_mz",
    "precursor_mz_basis",
    "scan_precursor_mz",
    "parent_ms1_rt",
    "parent_ms1_mz",
    "observed_neutral_loss_da",
    "neutral_loss_mass_error_ppm",
    "supports_nl",
    "channel_mz",
    "peak_id",
    "unattached_reason",
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


@dataclass(frozen=True, slots=True)
class DiscoveryChannel:
    """One sample's MS1 ion: every strict NL event whose measured ion it is.

    The channel is fixed before acquisition and carries no tag, product or RT
    identity; physical peak boundaries separate its peaks in RT.
    """

    raw_file: Path
    sample_stem: str
    mz: float
    seeds: tuple[DiscoverySeed, ...]


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
    def peak_id(self) -> str:
        return build_discovery_peak_id(
            sample_stem=self.sample_stem,
            channel_mz=self.xic_mz,
            peak_rt_min=self.query_rt_min,
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
class DiscoveryPeak:
    """One physical MS1 peak on one ion channel, with the NL events locating it.

    Peak facts come from the physical measurement. Evidence fields (scan IDs,
    tag evidence, RT range, product intensity) cover every event locating the
    peak; ``attached_event_count`` counts those that attach and admit it. The
    representative event is attached when any event attaches.
    """

    peak_id: str
    raw_file: Path
    sample_stem: str
    channel_mz: float
    precursor_mz: float
    product_mz: float
    observed_neutral_loss_da: float
    neutral_loss_tag: str
    configured_neutral_loss_da: float
    neutral_loss_mass_error_ppm: float | None
    best_ms2_scan_id: int
    best_seed_rt: float
    seed_scan_ids: tuple[int, ...]
    attached_event_count: int
    rt_seed_min: float
    rt_seed_max: float
    ms2_product_max_intensity: float
    ms1_search_rt_min: float
    ms1_search_rt_max: float
    ms1_apex_rt: float
    ms1_area: float
    ms1_height: float
    ms1_peak_rt_start: float
    ms1_peak_rt_end: float
    ms1_seed_delta_min: float
    ms1_trace_quality: str
    review_priority: ReviewPriority
    reason: str
    ms1_scan_support_score: float | None = None
    # Qualified MS1 ion reading at the apex of an inferred precursor.
    ms1_apex_mz: float | None = None
    requires_overlap_review: bool = False
    doubly_charged_isotope: bool = False
    blank_background: bool = False
    evidence_score: int = 0
    evidence_tier: str = "E"
    ms2_support: str = "weak"
    ms1_support: str = "missing"
    rt_alignment: str = "missing"
    neutral_loss_error_basis: NeutralLossErrorBasis = "measured_scan_precursor_product"
    precursor_mz_basis: GroupPrecursorMzBasis = "scan_precursor"
    scan_precursor_mz: float | None = None
    scan_precursor_delta_da: float | None = None
    max_scan_precursor_abs_delta_da: float | None = None
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
    is_provisional: bool = False

    @property
    def admission_reason(self) -> AdmissionReason:
        """Peak-level gates first, then NL attachment."""
        if self.blank_background:
            return "system_background"
        if self.doubly_charged_isotope:
            return "doubly_charged_isotope"
        if self.requires_overlap_review:
            return "requires_overlap_review"
        if self.attached_event_count < 1:
            return "no_attached_event"
        return "admitted"

    @property
    def passes_peak_gates(self) -> bool:
        """No peak-level gate holds this peak, whatever its NL attachment."""
        return self.admission_reason in {"admitted", "no_attached_event"}

    @property
    def admitted(self) -> bool:
        """Whether this peak enters CID-NL Alignment."""
        return self.admission_reason == "admitted"

    @property
    def physical_mz(self) -> float:
        """Mass of the measured ion, independent of MS2 receipts."""
        return self.channel_mz if self.ms1_apex_mz is None else self.ms1_apex_mz


UnattachedReason = str
"""Why an event attaches to no peak.

``no_peak_at_rt`` or another physical evaluation reason when the channel has no
measured peak at the event RT; a precursor assessment status (for example
``parent_ms1_ion_missing`` or ``outside_nominal_isolation``) when the event's
precursor is not verified; ``precursor_outside_peak_mass`` when the verified
precursor is not the measured ion; ``outside_peak_bounds`` when the MS2 scan or
its parent MS1 scan falls outside the peak bounds plus the attachment margin.
"""


@dataclass(frozen=True, slots=True)
class DiscoveryEvent:
    """One strict NL event and the peak it attaches to, or why it attaches to none."""

    seed: DiscoverySeed
    channel_mz: float
    supports_nl: bool
    peak_id: str = ""
    unattached_reason: UnattachedReason = ""

    def __post_init__(self) -> None:
        if bool(self.peak_id) == bool(self.unattached_reason):
            raise ValueError(
                "a Discovery event attaches to one peak or records one reason"
            )

    @property
    def sample_stem(self) -> str:
        return self.seed.sample_stem

    @property
    def ms2_scan_number(self) -> int:
        return self.seed.scan_number

    @property
    def ms2_rt(self) -> float:
        return self.seed.rt

    @property
    def neutral_loss_tag(self) -> str:
        return self.seed.neutral_loss_tag

    @property
    def product_mz(self) -> float:
        return self.seed.product_mz

    @property
    def product_intensity(self) -> float:
        return self.seed.product_intensity

    @property
    def precursor_mz(self) -> float:
        return self.seed.precursor_mz

    @property
    def precursor_mz_basis(self) -> str:
        return self.seed.precursor_mz_basis

    @property
    def scan_precursor_mz(self) -> float | None:
        return self.seed.scan_precursor_mz

    @property
    def parent_ms1_rt(self) -> float | None:
        observation = self.seed.ms1_precursor_observation
        return None if observation is None else observation.rt_min

    @property
    def parent_ms1_mz(self) -> float | None:
        observation = self.seed.ms1_precursor_observation
        if observation is None or len(observation.centroid_mzs) != 1:
            return None
        return observation.centroid_mzs[0]

    @property
    def observed_neutral_loss_da(self) -> float:
        return self.seed.observed_neutral_loss_da

    @property
    def neutral_loss_mass_error_ppm(self) -> float | None:
        return self.seed.observed_loss_error_ppm


_DISCOVERY_PRIORITY_RANK = {"HIGH": 0, "MEDIUM": 1, "LOW": 2}


def discovery_peak_output_sort_key(peak: DiscoveryPeak) -> tuple[object, ...]:
    """Canonical Discovery peak order shared by outputs and handoffs."""

    return (
        _DISCOVERY_PRIORITY_RANK.get(
            peak.review_priority,
            len(_DISCOVERY_PRIORITY_RANK),
        ),
        -peak.evidence_score,
        -peak.attached_event_count,
        -peak.ms2_product_max_intensity,
        -peak.ms1_area,
        peak.best_seed_rt,
        peak.peak_id,
    )


def discovery_event_output_sort_key(event: DiscoveryEvent) -> tuple[object, ...]:
    return (
        event.ms2_rt,
        event.ms2_scan_number,
        event.neutral_loss_tag,
        event.product_mz,
    )


@dataclass(frozen=True)
class DiscoveryRunOutputs:
    peaks_csv: Path
    events_csv: Path
    physical_facts_json: Path
    batch_index_csv: Path | None = None
    physical_bindings: tuple["DiscoveryPeakPhysicalBinding", ...] = ()
    physical_facts_fingerprint: "FileContentFingerprint | None" = None


@dataclass(frozen=True)
class DiscoveryBatchOutputs:
    batch_index_csv: Path
    per_sample: tuple[DiscoveryRunOutputs, ...]


def build_discovery_peak_id(
    *,
    sample_stem: str,
    channel_mz: float,
    peak_rt_min: float,
) -> str:
    return f"{sample_stem}@mz{_format_id_mz(channel_mz)}_rt{peak_rt_min:.4f}"


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


def _format_id_mz(value: float) -> str:
    return f"{value:.6f}".rstrip("0").rstrip(".")
