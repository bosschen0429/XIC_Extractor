from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import TYPE_CHECKING, Any

import numpy as np

from xic_extractor.ms2_trace_evidence import MS2TraceStrength
from xic_extractor.peak_detection.peak_scoring_evidence import EvidenceScore
from xic_extractor.peak_detection.signal_quality import SignalQuality

if TYPE_CHECKING:
    from xic_extractor.peak_detection.evidence_facts import CandidateEvidenceFacts


class Confidence(Enum):
    HIGH = "HIGH"
    MEDIUM = "MEDIUM"
    LOW = "LOW"
    VERY_LOW = "VERY_LOW"


CONFIDENCE_RANK = {
    Confidence.HIGH: 0,
    Confidence.MEDIUM: 1,
    Confidence.LOW: 2,
    Confidence.VERY_LOW: 3,
}


@dataclass(frozen=True)
class ScoredCandidate:
    candidate: Any
    severities: tuple[tuple[int, str], ...]
    confidence: Confidence
    reason: str
    prior_rt: float | None
    quality_penalty: int = 0
    selection_quality_penalty: float | None = None

    evidence_score: EvidenceScore | None = None
    evidence_facts: CandidateEvidenceFacts | None = None


@dataclass(frozen=True)
class ScoringContext:
    rt_array: np.ndarray
    intensity_array: np.ndarray
    apex_index: int
    half_width_ratio: float
    fwhm_ratio: float | None
    ms2_present: bool
    nl_match: bool
    rt_prior: float | None
    rt_prior_sigma: float | None
    rt_min: float
    rt_max: float
    neutral_loss_required: bool = True
    count_no_ms2_as_detected: bool = False
    ms2_trace_strength: MS2TraceStrength | None = None
    ms2_alignment_source: str | None = None
    trigger_scan_count: int | None = None
    strict_nl_scan_count: int | None = None
    ms1_peak_group_source: str = ""
    ms1_peak_group_rt_min: float | None = None
    ms1_peak_group_rt_max: float | None = None
    ms1_peak_group_trigger_scan_count: int | None = None
    ms1_peak_group_strict_nl_scan_count: int | None = None
    ms1_peak_group_strict_nl_event_count: int | None = None
    outside_ms1_peak_group_trigger_scan_count: int | None = None
    outside_ms1_peak_group_strict_nl_scan_count: int | None = None
    signal_quality: SignalQuality | None = None
    baseline_method: str = ""

    active_trace_source: str = ""
    morphology_trace_method: str = ""
    morphology_trace_window_points: int | None = None


def confidence_from_value(value: str) -> Confidence:
    return Confidence(value)
