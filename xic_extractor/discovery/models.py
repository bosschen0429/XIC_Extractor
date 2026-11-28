from collections.abc import Iterable
from dataclasses import dataclass
from dataclasses import field as dataclass_field
from pathlib import Path
from typing import Literal

from xic_extractor.precursor_evidence import (
    CidNlScan,
)
from xic_extractor.precursor_evidence import PrecursorMzBasis as PrecursorMzBasis

GroupPrecursorMzBasis = Literal[
    "scan_precursor", "scan_monoisotopic", "product_plus_neutral_loss", "mixed"
]
PeakStatus = Literal[
    "admitted",
    "blank",
    "doubly_charged",
    "no_nl_event",
]
"""Whether a peak enters Alignment, and if not, the first reason that holds it."""
PEAK_STATUSES: tuple[PeakStatus, ...] = (
    "admitted",
    "blank",
    "doubly_charged",
    "no_nl_event",
)


@dataclass(frozen=True)
class DiscoveryEvent:
    raw_file: Path
    sample_stem: str
    neutral_loss_tag: str
    scan: CidNlScan
    acquisition_source_id: str = ""


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
    ``ms1_apex_rt`` is the apex Discovery judged the peak at; Alignment measures
    the peak again from RAW and publishes its own measurement.
    ``best_ms2_scan_id`` names the representative event, whose values live in
    the event table; ``attached_event_count`` counts the located events that
    attach and admit the peak. The representative event is attached when any
    event attaches.
    """

    peak_id: str
    sample_stem: str
    peak_mz: float
    neutral_loss_tag: str
    best_ms2_scan_id: int
    attached_event_count: int
    ms1_apex_rt: float
    status: PeakStatus
    ms1_apex_mz: float | None = None
    matched_tag_names: tuple[str, ...] = ()
    located_events: tuple[DiscoveryEvent, ...] = dataclass_field(
        default=(), compare=False
    )

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
    peak_id: str = ""
    unattached_reason: UnattachedReason = ""

    @property
    def attached(self) -> bool:
        return not self.unattached_reason

    @property
    def sample_stem(self) -> str:
        return self.event.sample_stem


def representative_event_rt(peak: DiscoveryPeak) -> float:
    """RT of the MS2 scan ``best_ms2_scan_id`` among the peak's located events."""

    for event in peak.located_events:
        if event.scan.scan_number == peak.best_ms2_scan_id:
            return event.scan.rt_min
    raise ValueError(f"{peak.peak_id}: located events lack the representative scan")


def located_event_order(
    events: Iterable[DiscoveryEvent],
) -> tuple[DiscoveryEvent, ...]:
    """Canonical order of the events locating one peak."""

    return tuple(
        sorted(
            events,
            key=lambda event: (
                event.scan.scan_number,
                event.neutral_loss_tag,
                event.scan.precursor_mz,
                event.scan.product_mz,
                event.scan.precursor_mz_basis,
            ),
        )
    )


@dataclass(frozen=True)
class DiscoveryBatchOutputs:
    """The published Discovery file and the samples it holds, in run order."""

    store: Path
    sample_stems: tuple[str, ...]


def build_discovery_peak_id(
    *,
    sample_stem: str,
    xic_mz: float,
    peak_rt_min: float,
) -> str:
    return f"{sample_stem}@mz{_format_id_mz(xic_mz)}_rt{peak_rt_min:.4f}"


def _format_id_mz(value: float) -> str:
    return f"{value:.6f}".rstrip("0").rstrip(".")
