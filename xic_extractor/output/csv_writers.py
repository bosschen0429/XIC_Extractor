import csv
from collections.abc import Mapping, Sequence
from typing import Protocol

from xic_extractor.config import ExtractionConfig, Target
from xic_extractor.neutral_loss import CandidateMS2Evidence, NLResult
from xic_extractor.output.detection import is_accepted_result_detection
from xic_extractor.output.reason_display import (
    display_measurement_reason,
    display_nl_evidence,
)
from xic_extractor.output.schema import (
    DIAGNOSTIC_HEADERS,
    LONG_HEADERS,
    MS1_SUFFIXES,
    SCORE_BREAKDOWN_HEADERS,
    TARGETED_PRODUCT_PROJECTION_HEADERS,
)
from xic_extractor.peak_detection.targeted_product_projection import (
    TargetedProductProjection,
)
from xic_extractor.sample_groups import classify_sample_group
from xic_extractor.signal_processing import PeakDetectionResult


class DiagnosticRecordLike(Protocol):
    @property
    def sample_name(self) -> str: ...

    @property
    def target_label(self) -> str: ...

    @property
    def issue(self) -> str: ...

    @property
    def reason(self) -> str: ...


class ExtractionResultLike(Protocol):
    @property
    def peak_result(self) -> PeakDetectionResult: ...

    @property
    def nl(self) -> NLResult | None: ...

    @property
    def candidate_ms2_evidence(self) -> CandidateMS2Evidence | None: ...

    @property
    def nl_token(self) -> str | None: ...

    @property
    def target_label(self) -> str: ...

    @property
    def confidence(self) -> str: ...

    @property
    def reason(self) -> str: ...

    @property
    def severities(self) -> tuple[tuple[int, str], ...]: ...

    @property
    def prior_rt(self) -> float | None: ...

    @property
    def prior_source(self) -> str: ...

    @property
    def quality_penalty(self) -> int: ...

    @property
    def quality_flags(self) -> tuple[str, ...]: ...

    @property
    def score_breakdown(self) -> tuple[tuple[str, str], ...]: ...

    @property
    def reported_rt(self) -> float | None: ...

    @property
    def reported_peak_area(self) -> float | None: ...

    @property
    def reported_peak_intensity(self) -> float | None: ...

    @property
    def reported_smoothed_peak_intensity(self) -> float | None: ...

    @property
    def reported_peak_start(self) -> float | None: ...

    @property
    def reported_peak_end(self) -> float | None: ...

    @property
    def reported_peak_width(self) -> float | None: ...

    @property
    def total_severity(self) -> int: ...

    @property
    def targeted_product_projection(self) -> TargetedProductProjection | None: ...


class FileResultLike(Protocol):
    @property
    def sample_name(self) -> str: ...

    @property
    def results(self) -> Mapping[str, ExtractionResultLike]: ...

    @property
    def error(self) -> str | None: ...

    @property
    def extraction_results(self) -> Sequence[ExtractionResultLike]: ...


def write_all(
    config: ExtractionConfig,
    targets: Sequence[Target],
    file_results: Sequence[FileResultLike],
    diagnostics: Sequence[DiagnosticRecordLike],
    *,
    emit_score_breakdown: bool,
) -> None:
    write_wide_csv(config, targets, file_results)
    write_long_csv(config, targets, file_results)
    write_diagnostics_csv(config, diagnostics)
    if emit_score_breakdown:
        write_score_breakdown_csv(config, file_results)


def write_wide_csv(
    config: ExtractionConfig,
    targets: Sequence[Target],
    file_results: Sequence[FileResultLike],
) -> None:
    config.output_csv.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = _output_fieldnames(targets)
    with config.output_csv.open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for file_result in file_results:
            writer.writerow(_output_row(file_result, targets))


def write_diagnostics_csv(
    config: ExtractionConfig, diagnostics: Sequence[DiagnosticRecordLike]
) -> None:
    config.diagnostics_csv.parent.mkdir(parents=True, exist_ok=True)
    with config.diagnostics_csv.open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=DIAGNOSTIC_HEADERS)
        writer.writeheader()
        for record in diagnostics:
            writer.writerow(
                {
                    "SampleName": record.sample_name,
                    "Target": record.target_label,
                    "Issue": record.issue,
                    "Reason": record.reason,
                }
            )


def write_long_csv(
    config: ExtractionConfig,
    targets: Sequence[Target],
    file_results: Sequence[FileResultLike],
) -> None:
    path = config.output_csv.with_name("xic_results_long.csv")
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=LONG_HEADERS)
        writer.writeheader()
        for file_result in file_results:
            writer.writerows(_long_output_rows(file_result, targets))


def _long_output_rows(
    file_result: FileResultLike, targets: Sequence[Target]
) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for target in targets:
        row = {
            "SampleName": file_result.sample_name,
            "Group": _sample_group(file_result.sample_name),
            "Target": target.label,
            "Role": "ISTD" if target.is_istd else "Analyte",
            "ISTD Pair": target.istd_pair,
            "RT": "",
            "Area": "",
            "NL": "",
            "NL Evidence": "",
            "Int": "",
            "IntSmoothed": "",
            "PeakStart": "",
            "PeakEnd": "",
            "PeakWidth": "",
            "Confidence": "",
            "Reason": "",
            **{header: "" for header in TARGETED_PRODUCT_PROJECTION_HEADERS},
        }
        if file_result.error is not None:
            _set_long_ms1_values(row, "ERROR")
            row["NL"] = "ERROR" if target.neutral_loss_da is not None else ""
            row["NL Evidence"] = display_nl_evidence(
                "ERROR", configured=target.neutral_loss_da is not None
            )
            row["Reason"] = "RAW file could not be processed."
            row.update(_error_projection_fields())
        else:
            result = file_result.results[target.label]
            _set_long_peak_values(row, result)
            row["NL"] = (
                result.nl_token or "" if target.neutral_loss_da is not None else ""
            )
            row["NL Evidence"] = display_nl_evidence(
                result.nl_token, configured=target.neutral_loss_da is not None
            )
            row["Confidence"] = _display_confidence(result)
            row["Reason"] = _display_reason(result)
            row.update(_projection_fields(result.targeted_product_projection))
        rows.append(row)
    return rows


def write_score_breakdown_csv(
    config: ExtractionConfig, file_results: Sequence[FileResultLike]
) -> None:
    path = config.output_csv.with_name("xic_score_breakdown.csv")
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=SCORE_BREAKDOWN_HEADERS)
        writer.writeheader()
        for file_result in file_results:
            writer.writerows(
                _score_breakdown_rows(
                    file_result,
                    count_no_ms2_as_detected=config.count_no_ms2_as_detected,
                )
            )


def _score_breakdown_rows(
    file_result: FileResultLike,
    *,
    count_no_ms2_as_detected: bool,
) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for result in file_result.extraction_results:
        severities = {label: severity for severity, label in result.severities}
        weighted_fields = dict(result.score_breakdown)
        rows.append(
            {
                "SampleName": file_result.sample_name,
                "Target": result.target_label,
                "Final Confidence": weighted_fields.get(
                    "Final Confidence", result.confidence
                ),
                "Detection Counted": (
                    "TRUE"
                    if is_accepted_result_detection(
                        result,
                        count_no_ms2_as_detected,
                        require_projection=True,
                    )
                    else "FALSE"
                ),
                "Product State": _projection_text(
                    result.targeted_product_projection,
                    "product_state",
                ),
                "Review State": _projection_text(
                    result.targeted_product_projection,
                    "review_state",
                ),
                "Projection Reason": _projection_text(
                    result.targeted_product_projection,
                    "projection_reason",
                ),
                "Legacy Authority Status": _projection_text(
                    result.targeted_product_projection,
                    "legacy_authority_status",
                ),
                "Caps": weighted_fields.get("Caps", ""),
                "Raw Score": weighted_fields.get("Raw Score", ""),
                "Support": weighted_fields.get("Support", ""),
                "Concerns": weighted_fields.get("Concerns", ""),
                "Base Score": weighted_fields.get("Base Score", ""),
                "Positive Points": weighted_fields.get("Positive Points", ""),
                "Negative Points": weighted_fields.get("Negative Points", ""),
                "symmetry": _format_optional_severity(severities.get("symmetry")),
                "local_sn": _format_optional_severity(severities.get("local_sn")),
                "nl_support": _format_optional_severity(severities.get("nl_support")),
                "rt_prior": _format_optional_severity(severities.get("rt_prior")),
                "rt_centrality": _format_optional_severity(
                    severities.get("rt_centrality")
                ),
                "noise_shape": _format_optional_severity(severities.get("noise_shape")),
                "peak_width": _format_optional_severity(severities.get("peak_width")),
                "Quality Penalty": str(result.quality_penalty),
                "Quality Flags": ",".join(result.quality_flags),
                "Total Severity": str(result.total_severity),
                "Confidence": result.confidence,
                "Prior RT": _format_optional_number(result.prior_rt),
                "Prior Source": result.prior_source,
            }
        )
    return rows


def _projection_fields(
    projection: TargetedProductProjection | None,
) -> dict[str, str]:
    if projection is None:
        return {header: "" for header in TARGETED_PRODUCT_PROJECTION_HEADERS}
    return {
        "Product State": projection.product_state,
        "Counted Detection": "TRUE" if projection.counted_detection else "FALSE",
        "Review State": projection.review_state,
        "Projection Reason": projection.projection_reason,
        "Projection Support Reasons": _join_reasons(projection.support_reasons),
        "Projection Review Reasons": _join_reasons(projection.review_reasons),
        "Projection Conflict Reasons": _join_reasons(projection.conflict_reasons),
        "Projection Not Counted Reasons": _join_reasons(projection.not_counted_reasons),
        "Projection Exclusion Reasons": _join_reasons(projection.exclusion_reasons),
        "Legacy Authority Status": projection.legacy_authority_status,
        "Benchmark Eligibility State": projection.benchmark_eligibility_state,
    }


def _display_reason(result: ExtractionResultLike) -> str:
    projection = result.targeted_product_projection
    if projection is not None:
        return display_measurement_reason(
            has_quantifiable_peak=_has_reported_peak(result),
            review_reasons=projection.review_reasons,
            conflict_reasons=projection.conflict_reasons,
            not_counted_reasons=projection.not_counted_reasons,
            exclusion_reasons=projection.exclusion_reasons,
            concerns=dict(result.score_breakdown).get("Concerns", ""),
        )
    return result.reason


def _display_confidence(result: ExtractionResultLike) -> str:
    projection = result.targeted_product_projection
    if projection is not None and projection.product_state in {
        "ambiguous",
        "excluded",
        "not_counted",
    }:
        return "VERY_LOW"
    return result.confidence


def _error_projection_fields() -> dict[str, str]:
    return {
        "Product State": "excluded",
        "Counted Detection": "FALSE",
        "Review State": "review_required",
        "Projection Reason": "decision: excluded; excluded: file_error",
        "Projection Support Reasons": "",
        "Projection Review Reasons": "",
        "Projection Conflict Reasons": "",
        "Projection Not Counted Reasons": "",
        "Projection Exclusion Reasons": "file_error",
        "Legacy Authority Status": "retired",
        "Benchmark Eligibility State": "",
    }


def _projection_text(
    projection: TargetedProductProjection | None,
    field_name: str,
) -> str:
    if projection is None:
        return ""
    value = getattr(projection, field_name)
    return str(value)


def _join_reasons(reasons: tuple[str, ...]) -> str:
    return "; ".join(reasons)


def _format_optional_severity(value: int | None) -> str:
    if value is None:
        return ""
    return str(value)


def _format_optional_number(value: float | None) -> str:
    if value is None:
        return "NA"
    return f"{value:g}"


def _set_long_ms1_values(row: dict[str, str], value: str) -> None:
    row["RT"] = value
    row["Area"] = value
    row["Int"] = value
    row["IntSmoothed"] = value
    row["PeakStart"] = value
    row["PeakEnd"] = value
    row["PeakWidth"] = value


def _set_long_peak_values(row: dict[str, str], result: ExtractionResultLike) -> None:
    if not _has_reported_peak(result):
        _set_long_ms1_values(row, "ND")
        return
    row["RT"] = _format_peak_decimal(result.reported_rt, digits=4)
    row["Area"] = _format_peak_decimal(result.reported_peak_area, digits=2)
    row["Int"] = _format_peak_decimal(result.reported_peak_intensity, digits=0)
    row["IntSmoothed"] = _format_peak_decimal(
        result.reported_smoothed_peak_intensity, digits=0
    )
    row["PeakStart"] = _format_peak_decimal(result.reported_peak_start, digits=4)
    row["PeakEnd"] = _format_peak_decimal(result.reported_peak_end, digits=4)
    row["PeakWidth"] = _format_peak_decimal(result.reported_peak_width, digits=4)


def _output_fieldnames(targets: Sequence[Target]) -> list[str]:
    fieldnames = ["SampleName"]
    for target in targets:
        fieldnames.extend(_target_fieldnames(target))
    return fieldnames


def _target_fieldnames(target: Target) -> list[str]:
    fieldnames = [f"{target.label}_{suffix}" for suffix in MS1_SUFFIXES]
    if target.neutral_loss_da is not None:
        fieldnames.append(f"{target.label}_NL")
    return fieldnames


def _output_row(
    file_result: FileResultLike, targets: Sequence[Target]
) -> dict[str, str]:
    row = {"SampleName": file_result.sample_name}
    for target in targets:
        if file_result.error is not None:
            _set_target_values(row, target, "ERROR")
            continue

        result = file_result.results[target.label]
        _set_peak_values(row, target, result)
        if target.neutral_loss_da is not None:
            row[f"{target.label}_NL"] = result.nl_token or "ND"
    return row


def _set_target_values(row: dict[str, str], target: Target, value: str) -> None:
    for suffix in MS1_SUFFIXES:
        row[f"{target.label}_{suffix}"] = value
    if target.neutral_loss_da is not None:
        row[f"{target.label}_NL"] = value


def _set_peak_values(
    row: dict[str, str],
    target: Target,
    result: ExtractionResultLike,
) -> None:
    if not _has_reported_peak(result):
        for suffix in MS1_SUFFIXES:
            row[f"{target.label}_{suffix}"] = "ND"
        return

    row[f"{target.label}_RT"] = _format_peak_decimal(result.reported_rt, digits=4)
    row[f"{target.label}_Int"] = _format_peak_decimal(
        result.reported_peak_intensity,
        digits=0,
    )
    row[f"{target.label}_IntSmoothed"] = _format_peak_decimal(
        result.reported_smoothed_peak_intensity,
        digits=0,
    )
    row[f"{target.label}_Area"] = _format_peak_decimal(
        result.reported_peak_area,
        digits=2,
    )
    row[f"{target.label}_PeakStart"] = _format_peak_decimal(
        result.reported_peak_start,
        digits=4,
    )
    row[f"{target.label}_PeakEnd"] = _format_peak_decimal(
        result.reported_peak_end,
        digits=4,
    )
    row[f"{target.label}_PeakWidth"] = _format_peak_decimal(
        result.reported_peak_width,
        digits=4,
    )


def _has_reported_peak(result: ExtractionResultLike) -> bool:
    return all(
        value is not None
        for value in (
            result.reported_peak_area,
            result.reported_peak_intensity,
            result.reported_peak_start,
            result.reported_peak_end,
            result.reported_peak_width,
        )
    )


def _format_peak_decimal(value: float | None, *, digits: int) -> str:
    if value is None:
        return "ND"
    return f"{value:.{digits}f}"


def _sample_group(name: str) -> str:
    return classify_sample_group(name)
