from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

if TYPE_CHECKING:
    from xic_extractor.peak_detection.evidence_facts import CandidateEvidenceFacts
    from xic_extractor.peak_detection.physical_peak_models import PhysicalSegment

PeakStatus = Literal["OK", "NO_SIGNAL", "WINDOW_TOO_SHORT", "PEAK_NOT_FOUND"]
LocalMinimumQualityFlag = Literal[
    "edge_clipped",
    "too_broad",
    "too_short",
    "low_scan_count",
    "low_top_edge_ratio",
    "low_scan_support",
    "low_trace_continuity",
    "poor_edge_recovery",
]


@dataclass(frozen=True)
class PeakResult:
    rt: float
    intensity: float
    intensity_smoothed: float
    area: float
    peak_start: float
    peak_end: float


@dataclass(frozen=True)
class PeakCandidate:
    """Detected peak candidate."""

    peak: PeakResult
    selection_apex_rt: float
    selection_apex_intensity: float
    selection_apex_index: int
    raw_apex_rt: float
    raw_apex_intensity: float
    raw_apex_index: int
    prominence: float
    quality_flags: tuple[LocalMinimumQualityFlag, ...] = ()
    region_scan_count: int | None = None
    region_duration_min: float | None = None
    region_edge_ratio: float | None = None
    region_trace_continuity: float | None = None
    proposal_sources: tuple[str, ...] = ()
    source_apex_rank: int | None = None
    merge_note: str = ""
    ms2_evidence_peak_start: float | None = None
    ms2_evidence_peak_end: float | None = None
    # The physical owner supplies this binding; proposal_sources is audit text.
    physical_segment: PhysicalSegment | None = None


@dataclass(frozen=True)
class LocalMinimumRegionQuality:
    flags: tuple[LocalMinimumQualityFlag, ...]
    scan_count: int
    duration_min: float
    edge_ratio: float | None
    trace_continuity: float | None


@dataclass(frozen=True)
class PeakCandidatesResult:
    status: PeakStatus
    candidates: tuple[PeakCandidate, ...]
    n_points: int
    max_smoothed: float | None
    n_prominent_peaks: int


@dataclass(frozen=True)
class PeakCandidateScore:
    candidate: PeakCandidate
    reason: str
    raw_score: int | None = None
    support_labels: tuple[str, ...] = ()
    concern_labels: tuple[str, ...] = ()
    cap_labels: tuple[str, ...] = ()
    prior_rt: float | None = None
    quality_penalty: int = 0
    selection_quality_penalty: float | None = None
    severities: tuple[tuple[int, str], ...] = ()
    evidence_facts: CandidateEvidenceFacts | None = None


@dataclass(frozen=True)
class PeakDetectionResult:
    status: PeakStatus
    peak: PeakResult | None
    n_points: int
    max_smoothed: float | None
    n_prominent_peaks: int
    candidates: tuple[PeakCandidate, ...] = ()
    reason: str | None = None
    severities: tuple[tuple[int, str], ...] = ()
    score_breakdown: tuple[tuple[str, str], ...] = ()
    candidate_scores: tuple[PeakCandidateScore, ...] = ()
    selection_reference_rt: float | None = None
    paired_istd_anchor_rt: float | None = None

    def __deepcopy__(self, memo: dict[int, object]) -> PeakDetectionResult:
        # Peak geometry, scores and evidence facts are frozen scalar/tuple graphs.
        return self
