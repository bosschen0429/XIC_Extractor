from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from statistics import median

import numpy as np

from xic_extractor.configuration.models import Target
from xic_extractor.diagnostics.targeted_ms1_shape_identity import (
    TargetedMs1ShapeCandidate,
)
from xic_extractor.peak_detection.ms1_shape_identity import (
    DEFAULT_OWN_MAX_SMOOTH_POINTS,
    gaussian_smooth_values,
)
from xic_extractor.tabular_io import (
    bool_value,
    optional_float,
    text_value,
)
from xic_extractor.xic_models import XICTrace

LONG_CSV_REQUIRED_COLUMNS = (
    "SampleName",
    "Target",
    "Role",
    "ISTD Pair",
    "RT",
    "NL",
    "Product State",
    "Counted Detection",
    "Reason",
)
DEFAULT_CANDIDATE_SEARCH_HALF_WINDOW_MIN = 0.20
DEFAULT_MIN_REFERENCE_POINTS = 3


@dataclass(frozen=True)
class TargetedMs1ShapeIdentityCandidateRow:
    sample_name: str
    target_name: str
    paired_istd: str
    candidate_state: str
    paired_istd_rt_min: float | None
    expected_candidate_rt_min: float | None
    source_row_id: str


@dataclass(frozen=True)
class TargetedMs1ShapeIdentityReferenceRow:
    sample_name: str
    target_name: str
    paired_istd: str
    rt_min: float
    paired_istd_rt_min: float | None
    source_quality: str


@dataclass(frozen=True)
class TargetedMs1ShapeTraceRequest:
    sample_name: str
    target_name: str
    mz: float
    rt_min: float
    rt_max: float
    ppm_tol: float

    @property
    def key(self) -> tuple[str, str]:
        return (self.sample_name, self.target_name)


@dataclass(frozen=True)
class TargetedMs1ShapeReferenceModel:
    target_name: str
    paired_istd: str
    representative: TargetedMs1ShapeIdentityReferenceRow
    reference_rows: tuple[TargetedMs1ShapeIdentityReferenceRow, ...]
    reference_rt_median_min: float
    paired_delta_median_min: float | None

    @property
    def source_label(self) -> str:
        return (
            "representative_counted_reference:"
            f"{self.representative.sample_name}|{self.representative.source_quality}"
        )


@dataclass(frozen=True)
class TargetedMs1ShapeIdentitySupportPlan:
    candidates: tuple[TargetedMs1ShapeIdentityCandidateRow, ...]
    reference_models: Mapping[str, TargetedMs1ShapeReferenceModel]
    trace_requests: tuple[TargetedMs1ShapeTraceRequest, ...]


def build_targeted_ms1_shape_identity_support_plan(
    rows: Sequence[Mapping[str, str]],
    *,
    targets: Sequence[Target],
    target_names: Sequence[str] = (),
    min_reference_points: int = DEFAULT_MIN_REFERENCE_POINTS,
) -> TargetedMs1ShapeIdentitySupportPlan:
    targets_by_label = {target.label: target for target in targets}
    requested_targets = set(target_names)
    row_index = _row_index(rows)
    reference_models = _reference_models(
        rows,
        targets_by_label=targets_by_label,
        row_index=row_index,
        requested_targets=requested_targets,
        min_reference_points=min_reference_points,
    )
    candidates = _candidate_rows(
        rows,
        targets_by_label=targets_by_label,
        row_index=row_index,
        reference_models=reference_models,
        requested_targets=requested_targets,
    )
    trace_requests = _trace_requests(
        candidates,
        reference_models=reference_models,
        targets_by_label=targets_by_label,
    )
    return TargetedMs1ShapeIdentitySupportPlan(
        candidates=tuple(candidates),
        reference_models=reference_models,
        trace_requests=trace_requests,
    )


def build_targeted_ms1_shape_identity_candidates_from_traces(
    plan: TargetedMs1ShapeIdentitySupportPlan,
    *,
    targets: Sequence[Target],
    traces: Mapping[tuple[str, str], XICTrace],
    candidate_search_half_window_min: float = (
        DEFAULT_CANDIDATE_SEARCH_HALF_WINDOW_MIN
    ),
    smooth_points: int = DEFAULT_OWN_MAX_SMOOTH_POINTS,
) -> tuple[TargetedMs1ShapeCandidate, ...]:
    targets_by_label = {target.label: target for target in targets}
    candidates: list[TargetedMs1ShapeCandidate] = []
    for row in plan.candidates:
        target = targets_by_label[row.target_name]
        reference_model = plan.reference_models[row.target_name]
        reference = reference_model.representative
        candidate_trace = traces.get((row.sample_name, row.target_name))
        reference_trace = traces.get((reference.sample_name, reference.target_name))
        candidate_rt_min = (
            None
            if candidate_trace is None
            else select_smoothed_local_apex_rt(
                candidate_trace,
                center_rt_min=row.expected_candidate_rt_min,
                target_window_start_min=target.rt_min,
                target_window_end_min=target.rt_max,
                half_window_min=candidate_search_half_window_min,
                smooth_points=smooth_points,
            )
        )
        reference_rt_min = reference.rt_min if reference_trace is not None else None
        candidate_trace = candidate_trace or XICTrace.empty()
        reference_trace = reference_trace or XICTrace.empty()
        candidates.append(
            TargetedMs1ShapeCandidate(
                sample_name=row.sample_name,
                target_name=row.target_name,
                target_role="analyte",
                paired_istd=row.paired_istd,
                source_row_id=row.source_row_id,
                candidate_state=row.candidate_state,
                reference_source=reference_model.source_label,
                candidate_rt_min=candidate_rt_min,
                candidate_rt=tuple(float(value) for value in candidate_trace.rt),
                candidate_intensity=tuple(
                    float(value) for value in candidate_trace.intensity
                ),
                reference_rt_min=reference_rt_min,
                reference_rt=tuple(float(value) for value in reference_trace.rt),
                reference_intensity=tuple(
                    float(value) for value in reference_trace.intensity
                ),
                paired_istd_rt_min=row.paired_istd_rt_min,
                target_window_start_min=target.rt_min,
                target_window_end_min=target.rt_max,
            )
        )
    return tuple(candidates)


def select_smoothed_local_apex_rt(
    trace: XICTrace,
    *,
    center_rt_min: float | None,
    target_window_start_min: float,
    target_window_end_min: float,
    half_window_min: float = DEFAULT_CANDIDATE_SEARCH_HALF_WINDOW_MIN,
    smooth_points: int = DEFAULT_OWN_MAX_SMOOTH_POINTS,
) -> float | None:
    rt = np.asarray(trace.rt, dtype=float)
    intensity = np.asarray(trace.intensity, dtype=float)
    if rt.size != intensity.size or rt.size == 0:
        return None
    if center_rt_min is None or not math.isfinite(center_rt_min):
        lower = target_window_start_min
        upper = target_window_end_min
    else:
        lower = max(target_window_start_min, center_rt_min - half_window_min)
        upper = min(target_window_end_min, center_rt_min + half_window_min)
    mask = (
        np.isfinite(rt)
        & np.isfinite(intensity)
        & (rt >= lower)
        & (rt <= upper)
    )
    if int(np.sum(mask)) == 0:
        return None
    window_rt = rt[mask]
    window_intensity = intensity[mask]
    smoothed = np.asarray(
        gaussian_smooth_values(window_intensity, points=smooth_points),
        dtype=float,
    )
    if smoothed.size == 0 or float(np.max(smoothed)) <= 0:
        return None
    return float(window_rt[int(np.argmax(smoothed))])


def _reference_models(
    rows: Sequence[Mapping[str, str]],
    *,
    targets_by_label: Mapping[str, Target],
    row_index: Mapping[tuple[str, str], Mapping[str, str]],
    requested_targets: set[str],
    min_reference_points: int,
) -> dict[str, TargetedMs1ShapeReferenceModel]:
    models: dict[str, TargetedMs1ShapeReferenceModel] = {}
    for target in targets_by_label.values():
        if target.is_istd or not target.istd_pair:
            continue
        if requested_targets and target.label not in requested_targets:
            continue
        reference_rows = _reference_rows_for_target(
            rows,
            target=target,
            row_index=row_index,
            primary_clean_only=True,
        )
        if len(reference_rows) < min_reference_points:
            reference_rows = _reference_rows_for_target(
                rows,
                target=target,
                row_index=row_index,
                primary_clean_only=False,
            )
        if len(reference_rows) < min_reference_points:
            continue
        reference_rt_median = float(median(row.rt_min for row in reference_rows))
        paired_deltas = [
            row.rt_min - row.paired_istd_rt_min
            for row in reference_rows
            if row.paired_istd_rt_min is not None
        ]
        paired_delta_median = (
            float(median(paired_deltas)) if paired_deltas else None
        )
        representative = min(
            reference_rows,
            key=lambda row: abs(row.rt_min - reference_rt_median),
        )
        models[target.label] = TargetedMs1ShapeReferenceModel(
            target_name=target.label,
            paired_istd=target.istd_pair,
            representative=representative,
            reference_rows=tuple(reference_rows),
            reference_rt_median_min=reference_rt_median,
            paired_delta_median_min=paired_delta_median,
        )
    return models


def _reference_rows_for_target(
    rows: Sequence[Mapping[str, str]],
    *,
    target: Target,
    row_index: Mapping[tuple[str, str], Mapping[str, str]],
    primary_clean_only: bool,
) -> tuple[TargetedMs1ShapeIdentityReferenceRow, ...]:
    references: list[TargetedMs1ShapeIdentityReferenceRow] = []
    for row in rows:
        if text_value(row.get("Target")) != target.label:
            continue
        if text_value(row.get("Role")).lower() != "analyte":
            continue
        if bool_value(row.get("Counted Detection")) is not True:
            continue
        if text_value(row.get("NL")) != "OK":
            continue
        product_state = text_value(row.get("Product State"))
        if primary_clean_only and product_state != "detected_clean":
            continue
        rt_min = optional_float(row.get("RT"))
        sample_name = text_value(row.get("SampleName"))
        if rt_min is None or not sample_name:
            continue
        istd_rt = _paired_istd_rt(
            sample_name=sample_name,
            paired_istd=target.istd_pair,
            row_index=row_index,
        )
        references.append(
            TargetedMs1ShapeIdentityReferenceRow(
                sample_name=sample_name,
                target_name=target.label,
                paired_istd=target.istd_pair,
                rt_min=rt_min,
                paired_istd_rt_min=istd_rt,
                source_quality=product_state or "counted_detection",
            )
        )
    return tuple(references)


def _candidate_rows(
    rows: Sequence[Mapping[str, str]],
    *,
    targets_by_label: Mapping[str, Target],
    row_index: Mapping[tuple[str, str], Mapping[str, str]],
    reference_models: Mapping[str, TargetedMs1ShapeReferenceModel],
    requested_targets: set[str],
) -> list[TargetedMs1ShapeIdentityCandidateRow]:
    candidates: list[TargetedMs1ShapeIdentityCandidateRow] = []
    seen: set[tuple[str, str]] = set()
    for row in rows:
        target_name = text_value(row.get("Target"))
        target = targets_by_label.get(target_name)
        if target is None or target.is_istd or not target.istd_pair:
            continue
        if requested_targets and target_name not in requested_targets:
            continue
        reference_model = reference_models.get(target_name)
        if reference_model is None:
            continue
        if not _is_shape_identity_candidate_row(row):
            continue
        sample_name = text_value(row.get("SampleName"))
        key = (sample_name, target_name)
        if not sample_name or key in seen:
            continue
        seen.add(key)
        paired_istd_rt = _paired_istd_rt(
            sample_name=sample_name,
            paired_istd=target.istd_pair,
            row_index=row_index,
        )
        if paired_istd_rt is None:
            continue
        expected_rt = _expected_candidate_rt(
            paired_istd_rt=paired_istd_rt,
            reference_model=reference_model,
        )
        candidates.append(
            TargetedMs1ShapeIdentityCandidateRow(
                sample_name=sample_name,
                target_name=target_name,
                paired_istd=target.istd_pair,
                candidate_state=text_value(row.get("NL")),
                paired_istd_rt_min=paired_istd_rt,
                expected_candidate_rt_min=expected_rt,
                source_row_id=f"{sample_name}|{target_name}",
            )
        )
    return candidates


def _is_shape_identity_candidate_row(row: Mapping[str, str]) -> bool:
    reason = text_value(row.get("Reason"))
    support_reasons = _support_reasons(reason)
    not_counted_reasons = _not_counted_reasons(reason)
    if text_value(row.get("Role")).lower() != "analyte":
        return False
    if text_value(row.get("NL")) not in {"NL_FAIL", "NO_MS2"}:
        return False
    if bool_value(row.get("Counted Detection")) is not False:
        return False
    if text_value(row.get("Product State")) not in {"not_counted"}:
        return False
    if not_counted_reasons != {"analyte_nl_fail_requires_policy"}:
        return False
    if "paired_area_ratio_support" not in support_reasons:
        return False
    if not (
        support_reasons
        & {
            "paired_istd_rt_within_1min_support",
            "paired_istd_anchor_support",
            "role_aware_rt_support",
        }
    ):
        return False
    if "selected_envelope_boundary_" in reason:
        return False
    return True


def _support_reasons(reason: str) -> set[str]:
    for section in reason.split(";"):
        label, separator, values = section.partition(":")
        if separator and label.strip() == "support":
            return {value.strip() for value in values.split(",") if value.strip()}
    return set()


def _not_counted_reasons(reason: str) -> set[str]:
    for section in reason.split(";"):
        label, separator, values = section.partition(":")
        if separator and label.strip() == "not_counted":
            return {value.strip() for value in values.split(",") if value.strip()}
    return set()


def _expected_candidate_rt(
    *,
    paired_istd_rt: float | None,
    reference_model: TargetedMs1ShapeReferenceModel,
) -> float | None:
    if (
        paired_istd_rt is not None
        and reference_model.paired_delta_median_min is not None
    ):
        return paired_istd_rt + reference_model.paired_delta_median_min
    return reference_model.reference_rt_median_min


def _trace_requests(
    candidates: Sequence[TargetedMs1ShapeIdentityCandidateRow],
    *,
    reference_models: Mapping[str, TargetedMs1ShapeReferenceModel],
    targets_by_label: Mapping[str, Target],
) -> tuple[TargetedMs1ShapeTraceRequest, ...]:
    requests: list[TargetedMs1ShapeTraceRequest] = []
    seen: set[tuple[str, str]] = set()

    def add(sample_name: str, target_name: str) -> None:
        key = (sample_name, target_name)
        if key in seen:
            return
        target = targets_by_label[target_name]
        seen.add(key)
        requests.append(
            TargetedMs1ShapeTraceRequest(
                sample_name=sample_name,
                target_name=target_name,
                mz=target.mz,
                rt_min=target.rt_min,
                rt_max=target.rt_max,
                ppm_tol=target.ppm_tol,
            )
        )

    for model in reference_models.values():
        add(model.representative.sample_name, model.representative.target_name)
    for candidate in candidates:
        add(candidate.sample_name, candidate.target_name)
    return tuple(requests)


def _paired_istd_rt(
    *,
    sample_name: str,
    paired_istd: str,
    row_index: Mapping[tuple[str, str], Mapping[str, str]],
) -> float | None:
    row = row_index.get((sample_name, paired_istd))
    if row is None:
        return None
    return optional_float(row.get("RT"))


def _row_index(
    rows: Sequence[Mapping[str, str]],
) -> dict[tuple[str, str], Mapping[str, str]]:
    index: dict[tuple[str, str], Mapping[str, str]] = {}
    for row in rows:
        sample_name = text_value(row.get("SampleName"))
        target_name = text_value(row.get("Target"))
        if sample_name and target_name:
            index[(sample_name, target_name)] = row
    return index
