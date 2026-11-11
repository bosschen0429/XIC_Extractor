from collections.abc import Iterable
from dataclasses import dataclass
from dataclasses import field as dataclass_field
from pathlib import Path
from typing import Literal

from xic_extractor.ms2_scan_models import Ms2PrecursorMetadata
from xic_extractor.precursor_evidence import (
    Ms1PrecursorObservation,
    PrecursorLossAssessment,
    assess_precursor_loss,
    supports_direct_nl,
)
from xic_extractor.precursor_evidence import PrecursorMzBasis as PrecursorMzBasis

GroupPrecursorMzBasis = Literal[
    "scan_precursor", "scan_monoisotopic", "product_plus_neutral_loss", "mixed"
]
PeakStatus = Literal[
    "admitted",
    "blank",
    "doubly_charged",
    "overlap",
    "no_nl_event",
]
"""Whether a peak enters Alignment, and if not, the first reason that holds it."""
PEAK_STATUSES: tuple[PeakStatus, ...] = (
    "admitted",
    "blank",
    "doubly_charged",
    "overlap",
    "no_nl_event",
)

DISCOVERY_PEAK_COLUMNS = (
    "status",
    "evidence_score",
    "attached_event_count",
    "peak_id",
    "sample_stem",
    "peak_mz",
    "precursor_mz_basis",
    "ms1_apex_mz",
    "ms1_apex_rt",
    "ms1_search_rt_min",
    "ms1_search_rt_max",
    "query_rt_min",
    "best_ms2_scan_id",
    "neutral_loss_tag",
    "matched_tag_names",
)

DISCOVERY_EVENT_COLUMNS = (
    "ms2_scan_number",
    "ms2_rt",
    "neutral_loss_tag",
    "product_mz",
    "product_intensity",
    "precursor_mz",
    "precursor_mz_basis",
    "scan_precursor_mz",
    "precursor_monoisotopic_mz",
    "precursor_charge",
    "isolation_width_da",
    "isolation_offset_da",
    "master_scan_number",
    "parent_ms1_scan_number",
    "parent_ms1_rt",
    "parent_ms1_centroid_mzs",
    "observed_neutral_loss_da",
    "neutral_loss_mass_error_ppm",
    "precursor_verified",
    "xic_mz",
    "peak_id",
    "unattached_reason",
)


@dataclass(frozen=True)
class DiscoveryEvent:
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
class DiscoveryIon:
    """One sample's MS1 ion: every strict NL event whose measured ion it is.

    ``mz`` is the XIC center. The ion is fixed before acquisition and carries no
    tag, product or RT identity; physical peak boundaries separate its peaks in RT.
    """

    raw_file: Path
    sample_stem: str
    mz: float
    events: tuple[DiscoveryEvent, ...]


@dataclass(frozen=True)
class DiscoveryPeak:
    """One physical MS1 peak on one ion's XIC, with the NL events locating it.

    ``peak_mz`` is the peak's ion mass: the apex reading of an inferred
    precursor, else the XIC center. Every consumer reads it.
    ``ms1_apex_rt`` is the apex Discovery judged the peak at. The XIC window
    ``ms1_search_rt_min``..``ms1_search_rt_max`` at ``peak_mz`` and
    ``query_rt_min`` are the capture conditions of that measurement; Alignment
    measures the peak again from RAW with them and publishes its own
    measurement. ``best_ms2_scan_id`` names the
    representative event, whose values live in the event table;
    ``attached_event_count`` counts the located events that attach and admit
    the peak. The representative event is attached when any event attaches.
    """

    peak_id: str
    sample_stem: str
    peak_mz: float
    neutral_loss_tag: str
    best_ms2_scan_id: int
    attached_event_count: int
    ms1_apex_rt: float
    ms1_search_rt_min: float
    ms1_search_rt_max: float
    query_rt_min: float
    status: PeakStatus
    # Qualified MS1 ion reading at the apex of an inferred precursor.
    ms1_apex_mz: float | None = None
    evidence_score: int = 0
    precursor_mz_basis: GroupPrecursorMzBasis = "scan_precursor"
    matched_tag_names: tuple[str, ...] = ()
    # Every strict NL event locating this peak, attached or not. The event
    # table persists them; readers restore them by ``peak_id``.
    located_events: tuple[DiscoveryEvent, ...] = dataclass_field(
        default=(), compare=False
    )

    @property
    def is_clean_peak(self) -> bool:
        """No peak-level gate holds this peak, whatever its NL attachment."""
        return self.status in {"admitted", "no_nl_event"}

    @property
    def admitted(self) -> bool:
        """Whether this peak enters CID-NL Alignment."""
        return self.status == "admitted"


UnattachedReason = str
"""Why an event does not attach to the peak it locates, or locates none.

``no_peak_at_rt`` or another physical evaluation reason when the ion has no
measured peak at the event RT; a precursor assessment status (for example
``parent_ms1_ion_missing`` or ``outside_nominal_isolation``) when the event's
precursor is not verified; ``mass_outside_peak`` when the verified
precursor is not the measured ion; ``rt_outside_peak`` when the MS2 scan or
its parent MS1 scan falls outside the peak bounds plus the attachment margin.
"""


@dataclass(frozen=True, slots=True)
class EventAttachment:
    """One strict NL event, the peak it locates, and whether it attaches there.

    ``peak_id`` is the located peak; it is empty only when the ion has no
    measured peak at the event RT. ``unattached_reason`` is empty exactly when
    the event attaches to that peak.
    """

    event: DiscoveryEvent
    xic_mz: float
    precursor_verified: bool
    peak_id: str = ""
    unattached_reason: UnattachedReason = ""

    def __post_init__(self) -> None:
        if not self.peak_id and not self.unattached_reason:
            raise ValueError("a Discovery event without a located peak needs a reason")

    @property
    def attached(self) -> bool:
        return not self.unattached_reason

    @property
    def sample_stem(self) -> str:
        return self.event.sample_stem

    @property
    def ms2_scan_number(self) -> int:
        return self.event.scan_number

    @property
    def ms2_rt(self) -> float:
        return self.event.rt

    @property
    def neutral_loss_tag(self) -> str:
        return self.event.neutral_loss_tag

    @property
    def product_mz(self) -> float:
        return self.event.product_mz

    @property
    def product_intensity(self) -> float:
        return self.event.product_intensity

    @property
    def precursor_mz(self) -> float:
        return self.event.precursor_mz

    @property
    def precursor_mz_basis(self) -> str:
        return self.event.precursor_mz_basis

    @property
    def scan_precursor_mz(self) -> float | None:
        return self.event.scan_precursor_mz

    @property
    def precursor_monoisotopic_mz(self) -> float | None:
        metadata = self.event.precursor_metadata
        return None if metadata is None else metadata.monoisotopic_mz

    @property
    def precursor_charge(self) -> int | None:
        metadata = self.event.precursor_metadata
        return None if metadata is None else metadata.charge

    @property
    def isolation_width_da(self) -> float | None:
        metadata = self.event.precursor_metadata
        return None if metadata is None else metadata.isolation_width_da

    @property
    def isolation_offset_da(self) -> float | None:
        metadata = self.event.precursor_metadata
        return None if metadata is None else metadata.isolation_offset_da

    @property
    def master_scan_number(self) -> int | None:
        metadata = self.event.precursor_metadata
        return None if metadata is None else metadata.master_scan_number

    @property
    def parent_ms1_scan_number(self) -> int | None:
        observation = self.event.ms1_precursor_observation
        return None if observation is None else observation.scan_number

    @property
    def parent_ms1_rt(self) -> float | None:
        observation = self.event.ms1_precursor_observation
        return None if observation is None else observation.rt_min

    @property
    def parent_ms1_centroid_mzs(self) -> tuple[float, ...] | None:
        observation = self.event.ms1_precursor_observation
        return None if observation is None else observation.centroid_mzs

    @property
    def observed_neutral_loss_da(self) -> float:
        return self.event.observed_neutral_loss_da

    @property
    def neutral_loss_mass_error_ppm(self) -> float | None:
        return self.event.observed_loss_error_ppm


def representative_event_rt(peak: DiscoveryPeak) -> float:
    """RT of the MS2 scan ``best_ms2_scan_id`` among the peak's located events."""

    for event in peak.located_events:
        if event.scan_number == peak.best_ms2_scan_id:
            return event.rt
    raise ValueError(f"{peak.peak_id}: located events lack the representative scan")


def discovery_event_output_sort_key(event: EventAttachment) -> tuple[object, ...]:
    return (
        event.ms2_rt,
        event.ms2_scan_number,
        event.neutral_loss_tag,
        event.product_mz,
    )


def located_event_order(
    events: Iterable[DiscoveryEvent],
) -> tuple[DiscoveryEvent, ...]:
    """Canonical order of the events locating one peak."""

    return tuple(
        sorted(
            events,
            key=lambda event: (
                event.scan_number,
                event.neutral_loss_tag,
                event.precursor_mz,
                event.product_mz,
                event.precursor_mz_basis,
            ),
        )
    )


@dataclass(frozen=True)
class DiscoveryRunOutputs:
    peaks_csv: Path
    events_csv: Path
    batch_index_csv: Path | None = None


@dataclass(frozen=True)
class DiscoveryBatchOutputs:
    batch_index_csv: Path
    per_sample: tuple[DiscoveryRunOutputs, ...]


def build_discovery_peak_id(
    *,
    sample_stem: str,
    xic_mz: float,
    peak_rt_min: float,
) -> str:
    return f"{sample_stem}@mz{_format_id_mz(xic_mz)}_rt{peak_rt_min:.4f}"


def _format_id_mz(value: float) -> str:
    return f"{value:.6f}".rstrip("0").rstrip(".")
