from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from pathlib import Path
from typing import TYPE_CHECKING

from xic_extractor.extraction.result_assembly import reproject_extraction_result
from xic_extractor.output.targeted_product_model import (
    OWN_MAX_SAME_PEAK_SUPPORT_REASON,
)
from xic_extractor.tabular_io import (
    bool_value,
    optional_float,
    read_tsv_required,
    text_value,
)
from xic_extractor.targeted_ms1_shape_identity_policy import (
    EXPLICIT_SUPPORT_TSV_POLICY,
    require_supported_activation_policy,
    target_allowed_by_activation_policy,
)

if TYPE_CHECKING:
    from xic_extractor.configuration.models import Target
    from xic_extractor.extraction.result_models import ExtractionResult, RunOutput
    from xic_extractor.peak_detection.selection_decision import (
        PeakHypothesisSelectionDecision,
    )

SCHEMA_VERSION = "targeted_ms1_shape_identity"
VALIDATION_LABEL = "diagnostic_only"
DECISION_AUTHORITY = "diagnostic_only_no_product_write"

TARGETED_MS1_SHAPE_IDENTITY_COLUMNS = (
    "schema_version",
    "validation_label",
    "decision_authority",
    "sample_name",
    "target_name",
    "target_role",
    "paired_istd",
    "source_row_id",
    "candidate_state",
    "reference_source",
    "candidate_rt_min",
    "reference_rt_min",
    "candidate_anchor_rt_delta_min",
    "paired_istd_rt_min",
    "candidate_pair_rt_delta_min",
    "target_window_status",
    "own_max_same_peak_status",
    "own_max_same_peak_supported",
    "own_max_same_peak_support_reason",
    "own_max_same_peak_similarity",
    "own_max_compared_point_count",
    "strongest_peak_rt_min",
    "strongest_peak_own_max_ratio",
    "strongest_competing_peak_rt_min",
    "strongest_competing_peak_own_max_ratio",
    "competing_peak_status",
    "reason",
)
REQUIRED_COLUMNS = TARGETED_MS1_SHAPE_IDENTITY_COLUMNS
EVIDENCE_SOURCE = "targeted_ms1_shape_identity"
OWN_MAX_EVIDENCE_SOURCE = "own_max_same_peak"


@dataclass(frozen=True)
class TargetedMs1ShapeIdentitySupport:
    sample_name: str
    target_name: str
    support_reason: str = OWN_MAX_SAME_PEAK_SUPPORT_REASON
    source_schema_version: str = SCHEMA_VERSION


def load_targeted_ms1_shape_identity_supports(
    path: Path,
    *,
    activation_policy: str = EXPLICIT_SUPPORT_TSV_POLICY,
) -> tuple[TargetedMs1ShapeIdentitySupport, ...]:
    rows = read_tsv_required(path, REQUIRED_COLUMNS)
    return targeted_ms1_shape_identity_supports_from_rows(
        rows,
        activation_policy=activation_policy,
    )


def targeted_ms1_shape_identity_supports_from_rows(
    rows: Sequence[Mapping[str, str]],
    *,
    activation_policy: str = EXPLICIT_SUPPORT_TSV_POLICY,
) -> tuple[TargetedMs1ShapeIdentitySupport, ...]:
    require_supported_activation_policy(activation_policy)
    supports: list[TargetedMs1ShapeIdentitySupport] = []
    seen: set[tuple[str, str]] = set()
    for row in rows:
        support = _support_from_row(row, activation_policy=activation_policy)
        if support is None:
            continue
        key = (support.sample_name, support.target_name)
        if key in seen:
            raise ValueError(
                "duplicate targeted MS1 shape identity support row: "
                f"{support.sample_name}|{support.target_name}",
            )
        seen.add(key)
        supports.append(support)
    return tuple(supports)


def apply_targeted_ms1_shape_identity_projection(
    output: RunOutput,
    *,
    targets: Sequence[Target],
    supports: Sequence[TargetedMs1ShapeIdentitySupport],
) -> RunOutput:
    targets_by_label = {target.label: target for target in targets}
    support_index = {
        (support.sample_name, support.target_name): support for support in supports
    }
    for file_result in output.file_results:
        updated_results: dict[str, ExtractionResult] = {}
        for label, result in file_result.results.items():
            target = targets_by_label.get(label)
            support = support_index.get((file_result.sample_name, label))
            if target is None or support is None:
                updated_results[label] = result
                continue
            updated_results[label] = result_with_targeted_ms1_shape_identity_support(
                result,
                target=target,
                sample_name=file_result.sample_name,
                support=support,
            )
        file_result.results = updated_results
    return output


def result_with_targeted_ms1_shape_identity_support(
    result: ExtractionResult,
    *,
    target: Target,
    sample_name: str,
    support: TargetedMs1ShapeIdentitySupport,
) -> ExtractionResult:
    if (
        target.is_istd
        or support.support_reason != OWN_MAX_SAME_PEAK_SUPPORT_REASON
        or support.source_schema_version != SCHEMA_VERSION
        or support.sample_name != sample_name
        or support.target_name != target.label
    ):
        return result
    decision = _selection_decision_with_own_max_same_peak_support(result)
    if decision is None:
        return result
    return reproject_extraction_result(
        result,
        target=target,
        sample_name=sample_name,
        selection_decision=decision,
    )


def _support_from_row(
    row: Mapping[str, str],
    *,
    activation_policy: str,
) -> TargetedMs1ShapeIdentitySupport | None:
    if (
        text_value(row.get("schema_version")) != SCHEMA_VERSION
        or text_value(row.get("validation_label")) != VALIDATION_LABEL
        or text_value(row.get("decision_authority")) != DECISION_AUTHORITY
        or text_value(row.get("target_role")).lower() != "analyte"
        or text_value(row.get("target_window_status"))
        != "candidate_inside_target_window"
        or text_value(row.get("own_max_same_peak_status"))
        != "own_max_same_peak_supported"
        or bool_value(row.get("own_max_same_peak_supported")) is not True
        or text_value(row.get("own_max_same_peak_support_reason"))
        != OWN_MAX_SAME_PEAK_SUPPORT_REASON
        or text_value(row.get("competing_peak_status"))
        not in {
            "no_competing_peak_observed",
            "competing_peak_observed_below_blocker_threshold",
        }
    ):
        return None
    sample_name = text_value(row.get("sample_name"))
    target_name = text_value(row.get("target_name"))
    if (
        not sample_name
        or not target_name
        or not text_value(row.get("paired_istd"))
        or not text_value(row.get("source_row_id"))
        or not text_value(row.get("reason"))
        or optional_float(row.get("candidate_rt_min")) is None
        or optional_float(row.get("reference_rt_min")) is None
        or optional_float(row.get("own_max_same_peak_similarity")) is None
    ):
        return None
    if not target_allowed_by_activation_policy(activation_policy, target_name):
        raise ValueError(
            "targeted MS1 shape identity support row outside "
            f"{activation_policy} scope: {sample_name}|{target_name}",
        )
    return TargetedMs1ShapeIdentitySupport(
        sample_name=sample_name,
        target_name=target_name,
    )


def _selection_decision_with_own_max_same_peak_support(
    result: ExtractionResult,
) -> PeakHypothesisSelectionDecision | None:
    decision = result.selection_decision
    if decision is None:
        return None
    support_reasons = tuple(
        dict.fromkeys(
            (
                *decision.support_reasons,
                OWN_MAX_SAME_PEAK_SUPPORT_REASON,
            )
        )
    )
    evidence_sources = tuple(
        dict.fromkeys(
            (
                *decision.evidence_sources,
                EVIDENCE_SOURCE,
                OWN_MAX_EVIDENCE_SOURCE,
            )
        )
    )
    return replace(
        decision,
        support_reasons=support_reasons,
        evidence_sources=evidence_sources,
        legacy_projection_status="successor_owned",
        compatibility_oracle="successor_evidence_decision_semantics",
    )
