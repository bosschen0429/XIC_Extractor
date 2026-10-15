from collections.abc import Mapping, Sequence
from typing import Protocol

from xic_extractor.configuration.models import Target
from xic_extractor.evidence.neutral_loss import CandidateMS2Evidence, NLResult
from xic_extractor.output.reason_display import (
    display_measurement_reason,
    display_nl_evidence,
)
from xic_extractor.output.schema import (
    TARGETED_PRODUCT_PROJECTION_HEADERS,
)
from xic_extractor.output.targeted_product_model import (
    TargetedProductProjection,
)
from xic_extractor.peak_detection.models import PeakDetectionResult
from xic_extractor.sample_groups import classify_sample_group


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
            row["Reason"] = "RAW 檔無法處理。"
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
            row.update(_projection_fields(result.targeted_product_projection))
            row["Reason"] = _display_reason(result, row)
        rows.append(row)
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


def _display_reason(result: ExtractionResultLike, row: dict[str, str]) -> str:
    """Explain the finished projection already rendered into `row`."""
    if result.targeted_product_projection is not None:
        return display_measurement_reason(
            row,
            has_quantifiable_peak=_has_reported_peak(result),
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


def _join_reasons(reasons: tuple[str, ...]) -> str:
    return "; ".join(reasons)


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
