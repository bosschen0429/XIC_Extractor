from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

from xic_extractor.ms2_scan_models import Ms2PrecursorMetadata
from xic_extractor.ms2_trace_evidence import (
    MS2TraceEvidence,
    empty_ms2_trace_evidence,
)
from xic_extractor.precursor_evidence import (
    Ms1PrecursorObservation,
    PrecursorLossAssessment,
    PrecursorMzBasis,
    assess_precursor_loss,
)

CandidateMS2AlignmentSource = Literal[
    "region", "apex_fallback", "boundary_rescue", "none"
]
NLStatus = Literal["OK", "WARN", "NL_FAIL", "NO_MS2"]


@dataclass(frozen=True)
class NLResult:
    status: NLStatus
    best_ppm: float | None
    best_scan_rt: float | None
    valid_ms2_scan_count: int
    parse_error_count: int
    matched_scan_count: int
    product_evidence: tuple[MS2ProductEvidence, ...] = ()

    def to_token(self) -> str:
        if self.status == "WARN" and self.best_ppm is not None:
            return f"WARN_{self.best_ppm:.1f}ppm"
        return self.status


@dataclass(frozen=True)
class MS2ProductEvidence:
    scan_rt: float
    precursor_mz: float
    product_mz: float
    product_intensity: float
    product_base_ratio: float
    target_product_ppm: float
    observed_loss_da: float
    observed_loss_error_ppm: float | None
    scan_number: int | None = None
    acquisition_source_id: str = ""
    precursor_mz_basis: PrecursorMzBasis = "scan_precursor"
    scan_precursor_mz: float | None = None
    precursor_metadata: Ms2PrecursorMetadata | None = None
    precursor_inference_tolerance_da: float | None = None
    ms1_precursor_observation: Ms1PrecursorObservation | None = None

    def __deepcopy__(self, memo: dict[int, object]) -> MS2ProductEvidence:
        # Product facts and precursor metadata contain only frozen scalar/tuple data.
        return self

    def assess_precursor_loss(
        self, neutral_loss_da: float, tolerance_ppm: float
    ) -> PrecursorLossAssessment:
        return assess_precursor_loss(
            basis=self.precursor_mz_basis,
            precursor_mz=self.precursor_mz,
            product_mz=self.product_mz,
            neutral_loss_da=neutral_loss_da,
            observed_loss_da=self.observed_loss_da,
            observed_loss_error_ppm=self.observed_loss_error_ppm,
            tolerance_ppm=tolerance_ppm,
            selected_mz=(
                self.precursor_mz
                if self.scan_precursor_mz is None
                else self.scan_precursor_mz
            ),
            metadata=self.precursor_metadata,
            ms1_observation=self.ms1_precursor_observation,
        )


@dataclass(frozen=True)
class CandidateMS2Evidence:
    ms2_present: bool
    nl_match: bool
    nl_status: NLStatus
    trigger_scan_count: int
    strict_nl_scan_count: int
    best_loss_ppm: float | None
    best_scan_rt: float | None
    best_product_base_ratio: float | None
    alignment_source: CandidateMS2AlignmentSource
    diagnostic_product_absence_reason: str = ""
    nearest_product_loss_ppm: float | None = None
    nearest_product_base_ratio: float | None = None
    nearest_product_mz: float | None = None
    trace: MS2TraceEvidence = field(default_factory=empty_ms2_trace_evidence)
    ms1_peak_group_source: str = ""
    ms1_peak_group_rt_min: float | None = None
    ms1_peak_group_rt_max: float | None = None
    ms1_peak_group_trigger_scan_count: int | None = None
    ms1_peak_group_strict_nl_scan_count: int | None = None
    ms1_peak_group_strict_nl_event_count: int | None = None
    outside_ms1_peak_group_trigger_scan_count: int | None = None
    outside_ms1_peak_group_strict_nl_scan_count: int | None = None
    best_product_evidence: MS2ProductEvidence | None = None
    # Candidate-local recorded-precursor RNA transition; not a structural ID.
    competing_rna_scan_numbers: tuple[int, ...] = ()

    def __deepcopy__(self, memo: dict[int, object]) -> CandidateMS2Evidence:
        # Trace summaries and product facts have no mutable signal arrays.
        return self

    def to_token(self) -> str:
        if self.nl_status == "WARN" and self.best_loss_ppm is not None:
            return f"WARN_{self.best_loss_ppm:.1f}ppm"
        return self.nl_status
