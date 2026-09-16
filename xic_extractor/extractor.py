from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Literal

from xic_extractor import neutral_loss, raw_reader, signal_processing
from xic_extractor.config import ExtractionConfig, Target
from xic_extractor.extraction.scoring_factory import selected_candidate
from xic_extractor.output.messages import (
    DiagnosticIssue,
    DiagnosticRecord,
)
from xic_extractor.peak_detection.hypotheses import (
    IntegrationResult,
    PeakHypothesis,
    validate_peak_hypothesis_selection_authority,
)
from xic_extractor.peak_detection.model_selection import (
    ExpectedDiffApprovalRecords,
    PeakModelSelectionResult,
)
from xic_extractor.peak_detection.selection_decision import (
    PeakHypothesisSelectionDecision,
)
from xic_extractor.peak_detection.targeted_product_projection import (
    TargetedProductProjection,
)
from xic_extractor.peak_detection.traces import Trace
from xic_extractor.rt_prior_library import LibraryEntry

open_raw = raw_reader.open_raw
CandidateMS2Evidence = neutral_loss.CandidateMS2Evidence
NLResult = neutral_loss.NLResult
check_nl = neutral_loss.check_nl
collect_candidate_ms2_evidence = neutral_loss.collect_candidate_ms2_evidence
find_nl_anchor_rt = neutral_loss.find_nl_anchor_rt
PeakDetectionResult = signal_processing.PeakDetectionResult
PeakResult = signal_processing.PeakResult
find_peak_and_area = signal_processing.find_peak_and_area

__all__ = [
    "DiagnosticIssue",
    "DiagnosticRecord",
    "ExtractionResult",
    "FileResult",
    "RunOutput",
    "acquire",
    "run",
]


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


def run(
    config: ExtractionConfig,
    targets: list[Target],
    progress_callback: Callable[[int, int, str], None] | None = None,
    should_stop: Callable[[], bool] | None = None,
    injection_order: dict[str, int] | None = None,
    rt_prior_library: dict[tuple[str, str], LibraryEntry] | None = None,
    model_selection_expected_diff_approvals: ExpectedDiffApprovalRecords | None = None,
) -> RunOutput:
    from xic_extractor.extraction import targeted_diagnostic_application as application
    from xic_extractor.extraction import targeted_service

    return application.build_canonical_targeted_application_service(acquire).execute(
        targeted_service.build_targeted_application_request(
            config,
            targets,
            progress_callback=progress_callback,
            should_stop=should_stop,
            injection_order=injection_order,
            rt_prior_library=rt_prior_library,
            model_selection_expected_diff_approvals=(
                model_selection_expected_diff_approvals
            ),
        )
    )


def acquire(
    config: ExtractionConfig,
    targets: list[Target],
    progress_callback: Callable[[int, int, str], None] | None = None,
    should_stop: Callable[[], bool] | None = None,
    injection_order: dict[str, int] | None = None,
    rt_prior_library: dict[tuple[str, str], LibraryEntry] | None = None,
    model_selection_expected_diff_approvals: ExpectedDiffApprovalRecords | None = None,
    acquisition_sources_by_sample: Any = None,
    *,
    publish_outputs: Literal[False] = False,
) -> RunOutput:
    from xic_extractor.extraction.pipeline import run_pipeline

    return run_pipeline(
        config,
        targets,
        progress_callback=progress_callback,
        should_stop=should_stop,
        injection_order=injection_order,
        rt_prior_library=rt_prior_library,
        model_selection_expected_diff_approvals=model_selection_expected_diff_approvals,  # noqa: E501
        acquisition_sources_by_sample=acquisition_sources_by_sample,
        publish_outputs=publish_outputs,
    )
