"""Targeted extraction result models shared by extraction, application and writers."""

from __future__ import annotations

from dataclasses import dataclass, field

from xic_extractor.evidence.neutral_loss import CandidateMS2Evidence, NLResult
from xic_extractor.extraction.scoring_factory import selected_candidate
from xic_extractor.output.messages import (
    DiagnosticRecord,
)
from xic_extractor.output.targeted_product_model import (
    TargetedProductProjection,
)
from xic_extractor.peak_detection.hypotheses import (
    IntegrationResult,
    PeakHypothesis,
    validate_peak_hypothesis_selection_authority,
)
from xic_extractor.peak_detection.model_selection import (
    PeakModelSelectionResult,
)
from xic_extractor.peak_detection.models import PeakDetectionResult, PeakResult
from xic_extractor.peak_detection.physical_peak_models import PhysicalPeakEvaluation
from xic_extractor.peak_detection.selection_decision import (
    PeakHypothesisSelectionDecision,
)
from xic_extractor.peak_detection.traces import Trace


@dataclass(frozen=True)
class ExtractionResult:
    peak_result: PeakDetectionResult
    nl: NLResult | None
    candidate_ms2_evidence: CandidateMS2Evidence | None = None
    target_label: str = ""
    role: str = ""
    istd_pair: str = ""
    confidence: str = ""
    reason: str = ""
    severities: tuple[tuple[int, str], ...] = ()
    prior_rt: float | None = None
    prior_source: str = ""
    quality_penalty: int = 0
    quality_flags: tuple[str, ...] = ()
    score_breakdown: tuple[tuple[str, str], ...] = ()
    selected_hypothesis: PeakHypothesis | None = None
    selection_decision: PeakHypothesisSelectionDecision | None = None
    model_selection_result: PeakModelSelectionResult | None = None
    acquisition_trace: Trace | None = None
    physical_evaluation: PhysicalPeakEvaluation | None = None
    targeted_product_projection: TargetedProductProjection | None = None

    def __post_init__(self) -> None:
        if self.selected_hypothesis is not None:
            validate_peak_hypothesis_selection_authority(
                self.selected_hypothesis,
                consumer="ExtractionResult",
            )

    @property
    def peak(self) -> PeakResult | None:
        return self.peak_result.peak

    @property
    def nl_result(self) -> NLResult | None:
        return self.nl

    @property
    def nl_token(self) -> str | None:
        if self.candidate_ms2_evidence is not None:
            return self.candidate_ms2_evidence.to_token()
        if self.nl is not None:
            return self.nl.to_token()
        return None

    @property
    def total_severity(self) -> int:
        return sum(severity for severity, _ in self.severities) + self.quality_penalty

    @property
    def _selected_integration(self) -> IntegrationResult | None:
        if self.selected_hypothesis is None:
            return None
        return self.selected_hypothesis.integration

    @property
    def reported_rt(self) -> float | None:
        """User-facing RT uses selected integration apex when available."""
        integration = self._selected_integration
        if integration is not None:
            return integration.rt_apex_min
        candidate = selected_candidate(self.peak_result)
        if candidate is not None:
            return candidate.selection_apex_rt
        peak = self.peak
        if peak is None:
            return None
        return peak.rt

    @property
    def reported_peak_area(self) -> float | None:
        integration = self._selected_integration
        if integration is not None:
            if integration.area_ms1_morphology is not None:
                return integration.area_ms1_morphology
            return integration.area_raw_counts_seconds
        peak = self.peak
        return None if peak is None else peak.area

    @property
    def reported_peak_intensity(self) -> float | None:
        integration = self._selected_integration
        if integration is not None:
            return integration.height_raw
        peak = self.peak
        return None if peak is None else peak.intensity

    @property
    def reported_smoothed_peak_intensity(self) -> float | None:
        """Reuse the selected peak owner's smoothed height without recomputation."""
        integration = self._selected_integration
        if integration is not None:
            return integration.height_smoothed
        peak = self.peak
        return None if peak is None else peak.intensity_smoothed

    @property
    def reported_peak_start(self) -> float | None:
        integration = self._selected_integration
        if integration is not None:
            return integration.rt_left_min
        peak = self.peak
        return None if peak is None else peak.peak_start

    @property
    def reported_peak_end(self) -> float | None:
        integration = self._selected_integration
        if integration is not None:
            return integration.rt_right_min
        peak = self.peak
        return None if peak is None else peak.peak_end

    @property
    def reported_peak_width(self) -> float | None:
        integration = self._selected_integration
        if integration is not None:
            return abs(integration.rt_width_min)
        start = self.reported_peak_start
        end = self.reported_peak_end
        if start is None or end is None:
            return None
        return abs(end - start)


@dataclass
class FileResult:
    sample_name: str
    results: dict[str, ExtractionResult]
    group: str | None = None
    error: str | None = None
    peak_candidate_rows: list[dict[str, str]] = field(default_factory=list)
    peak_candidate_boundary_rows: list[dict[str, str]] = field(default_factory=list)
    selected_envelope_diagnostic_rows: list[dict[str, str]] = field(
        default_factory=list
    )

    @property
    def extraction_results(self) -> list[ExtractionResult]:
        return list(self.results.values())


@dataclass
class RunOutput:
    file_results: list[FileResult]
    diagnostics: list[DiagnosticRecord]


@dataclass
class RawFileExtractionResult:
    raw_index: int
    sample_name: str
    file_result: FileResult
    diagnostics: list[DiagnosticRecord]
