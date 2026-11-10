"""Targeted extraction result models shared by extraction, application and writers."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from xic_extractor.evidence.targeted_identification import MethodCandidateEvidence
    from xic_extractor.extraction.targeted_peak_selection import (
        TargetedCandidateAssessment,
    )

from xic_extractor.evidence.neutral_loss import (
    CandidateMS2Evidence,
    MS2ProductEvidence,
    NLResult,
)
from xic_extractor.extraction.scoring_factory import selected_candidate
from xic_extractor.output.messages import (
    DiagnosticRecord,
)
from xic_extractor.output.targeted_product_model import (
    TargetedProductProjection,
)
from xic_extractor.peak_detection.cid_nl_method_assessment import (
    CidNlEvaluationReceipt,
    CidNlMethodRelations,
)
from xic_extractor.peak_detection.hypotheses import (
    IntegrationResult,
    PeakHypothesis,
)
from xic_extractor.peak_detection.models import PeakDetectionResult, PeakResult
from xic_extractor.peak_detection.observations import (
    PeakObservationGenerationProvenance,
)
from xic_extractor.peak_detection.peak_hypothesis_identity import (
    MeasuredPeakHypothesis,
)
from xic_extractor.peak_detection.physical_peak_models import PhysicalPeakEvaluation
from xic_extractor.peak_detection.selection_decision import (
    PeakHypothesisSelectionDecision,
)
from xic_extractor.peak_detection.traces import Trace
from xic_extractor.sample_metadata import SampleMetadata


@dataclass(frozen=True)
class ExtractionResult:
    peak_result: PeakDetectionResult
    nl: NLResult | None
    candidate_ms2_evidence: CandidateMS2Evidence | None = None
    target_label: str = ""
    role: str = ""
    istd_pair: str = ""
    reason: str = ""
    severities: tuple[tuple[int, str], ...] = ()
    prior_rt: float | None = None
    prior_source: str = ""
    quality_penalty: int = 0
    quality_flags: tuple[str, ...] = ()
    score_breakdown: tuple[tuple[str, str], ...] = ()
    selected_hypothesis: PeakHypothesis | None = None
    selection_decision: PeakHypothesisSelectionDecision | None = None
    # The captured peak a finalized result reports; its integration is the
    # one written as RT, area, height and bounds.
    measured_hypothesis: MeasuredPeakHypothesis | None = None
    acquisition_trace: Trace | None = None
    acquisition_observation_provenance: PeakObservationGenerationProvenance | None = (
        None
    )
    physical_evaluation: PhysicalPeakEvaluation | None = None
    candidate_physical_evaluations: tuple[PhysicalPeakEvaluation, ...] = ()
    cid_nl_products: tuple[MS2ProductEvidence, ...] = ()
    cid_nl_evaluation_receipt: CidNlEvaluationReceipt | None = None
    cid_nl_relations: CidNlMethodRelations | None = None
    cid_nl_candidate_evidence: tuple[CandidateMS2Evidence, ...] = ()
    selected_cid_nl_candidate_index: int | None = None
    targeted_product_projection: TargetedProductProjection | None = None
    # Capture retains alternatives until the complete batch is available. No
    # RAW handle, callback or scoring closure crosses the worker boundary.
    targeted_candidate_assessment: TargetedCandidateAssessment | None = None
    # Selection owner's scope for later method assessment, not a new ranking.
    # None: no context decision; (): no candidate admitted. Full evidence remains.
    context_admissible_candidate_indexes: tuple[int, ...] | None = None
    method_candidate_evidence: tuple[MethodCandidateEvidence, ...] = ()
    selected_method_candidate_index: int | None = None

    @property
    def selected_method_supported(self) -> bool:
        return any(
            item.candidate_index == self.selected_method_candidate_index
            and item.accepted
            for item in self.method_candidate_evidence
        )

    @property
    def peak(self) -> PeakResult | None:
        return self.peak_result.peak

    @property
    def selected_cid_nl_supported(self) -> bool:
        return (
            self.cid_nl_relations is not None
            and self.selected_cid_nl_candidate_index
            in self.cid_nl_relations.supported_candidate_indexes
        )

    @property
    def nl_result(self) -> NLResult | None:
        return self.nl

    @property
    def nl_token(self) -> str | None:
        if self.candidate_ms2_evidence is not None:
            return self.candidate_ms2_evidence.to_token()
        if self.cid_nl_evaluation_receipt is not None:
            # No selected peak means no peak-owned positive claim. A different
            # scan elsewhere in the acquired run is not an output fallback.
            return None
        if self.nl is not None:
            return self.nl.to_token()
        return None

    @property
    def _selected_integration(self) -> IntegrationResult | None:
        if self.measured_hypothesis is not None:
            return self.measured_hypothesis.peak.integration
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
    sample_metadata: SampleMetadata | None = None


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
