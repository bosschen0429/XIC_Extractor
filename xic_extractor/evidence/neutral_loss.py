from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

from xic_extractor.ms2_trace_evidence import (
    MS2TraceEvidence,
    empty_ms2_trace_evidence,
)
from xic_extractor.precursor_evidence import (
    CidNlScan,
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
    scan: CidNlScan
    product_base_ratio: float
    target_product_ppm: float
    acquisition_source_id: str = ""
    precursor_inference_tolerance_da: float | None = None

    def __deepcopy__(self, memo: dict[int, object]) -> MS2ProductEvidence:
        return self


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
    best_product_evidence: MS2ProductEvidence | None = None

    def __deepcopy__(self, memo: dict[int, object]) -> CandidateMS2Evidence:
        return self

    def to_token(self) -> str:
        if self.nl_status == "WARN" and self.best_loss_ppm is not None:
            return f"WARN_{self.best_loss_ppm:.1f}ppm"
        return self.nl_status
