from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from xic_extractor.sequence_document import ActivationMethod as ActivationMethod

TrendConfidence = Literal["clean", "warning", "low"]
InstrumentQCStatus = Literal["detected", "not_detected", "error"]
HCDAuditStatus = Literal[
    "hcd_supported",
    "hcd_partial",
    "no_ms2_trigger",
    "no_product_match",
    "hcd_group_unmapped",
    "ms2_parse_error",
]


@dataclass(frozen=True)
class InstrumentQCDiagnostic:
    sample_name: str
    raw_path: Path
    issue: str
    detail: str


@dataclass(frozen=True)
class SDOLEKTrendRow:
    sample_name: str
    raw_path: Path
    injection_order: int | None
    compound: str
    precursor_mz: float
    identity_evidence: str
    reference_rt_min: float | None
    rt_delta_to_reference_min: float | None
    apex_rt_min: float | None
    area: float | None
    base_width_min: float | None
    reference_base_width_min: float | None
    base_width_ratio_to_reference: float | None
    peak_start_rt_min: float | None
    peak_end_rt_min: float | None
    trend_confidence: TrendConfidence
    trend_flags: tuple[str, ...]
    status: InstrumentQCStatus
    reason: str


@dataclass(frozen=True)
class HCDProductIon:
    compound_or_group: str
    precursor_mz: float | None
    activation: ActivationMethod
    product_label: str
    product_mz: float
    product_role: str


@dataclass(frozen=True)
class HCDAuditRow:
    sample_name: str
    raw_path: Path
    injection_order: int | None
    compound: str
    precursor_mz: float
    ms1_apex_rt_min: float | None
    ms1_status: InstrumentQCStatus
    instrument_method: str
    activation_method: ActivationMethod
    hcd_mapping_source: str
    hcd_product_group: str
    hcd_status: HCDAuditStatus
    best_ms2_scan_rt_min: float | None
    apex_ms2_delta_min: float | None
    trigger_scan_count: int
    expected_product_count: int
    matched_product_count: int
    best_product_ppm: float | None
    best_product_base_ratio: float | None
    matched_products: tuple[str, ...]
    review_flags: tuple[str, ...]
    review_reason: str


@dataclass(frozen=True)
class InstrumentQCRunOutput:
    trend_rows: tuple[SDOLEKTrendRow, ...]
    diagnostics: tuple[InstrumentQCDiagnostic, ...]
    trend_tsv: Path
    trend_json: Path
    diagnostics_tsv: Path
    workbook: Path
    mixstds_rows: tuple[SDOLEKTrendRow, ...] = ()
    mixstds_trend_tsv: Path | None = None
    mixstds_trend_json: Path | None = None
    mixstds_diagnostics_tsv: Path | None = None
    hcd_audit_rows: tuple[HCDAuditRow, ...] = ()
    hcd_audit_tsv: Path | None = None
    hcd_audit_json: Path | None = None
