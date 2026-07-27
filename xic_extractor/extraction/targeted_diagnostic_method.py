"""Bounded no-RAW Targeted CID-NL method and hard-exact legacy projection."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, Mapping
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Literal

from xic_extractor.alignment.config import AlignmentConfig
from xic_extractor.alignment.cross_sample_peak_groups import (
    CrossSamplePeakGroupHypothesis,
)
from xic_extractor.alignment.owner_backfill import OwnerBackfillSource
from xic_extractor.alignment.targeted_hypothesis_alignment import (
    TargetedHypothesisAlignmentInput,
    align_selected_targeted_hypotheses,
)
from xic_extractor.alignment.targeted_hypothesis_backfill import (
    TargetedHypothesisCell,
    build_targeted_hypothesis_cells,
)
from xic_extractor.config import ExtractionConfig, Target
from xic_extractor.execution_runtime import AttemptTerminalOutcome
from xic_extractor.execution_runtime_finalization_models import (
    SupervisorFinalizationRecord,
)
from xic_extractor.extractor import RunOutput
from xic_extractor.output.csv_writers import write_wide_csv
from xic_extractor.processing_identity import ProcessingAttemptIdentity


class TargetedDiagnosticMethodError(ValueError):
    """Raised when the bounded method cannot preserve its frozen contracts."""


class TargetedDiagnosticMethodParityError(TargetedDiagnosticMethodError):
    """Raised on any semantic or exact-byte difference from legacy."""


@dataclass(frozen=True, slots=True)
class TargetedDiagnosticProjection:
    sample_stem: str
    status: str
    rt: float | None
    area: float | None
    intensity: float | None
    peak_start: float | None
    peak_end: float | None
    peak_width: float | None
    confidence: str
    reason: str
    counted_detection: bool
    nl_token: str | None


@dataclass(frozen=True, slots=True)
class TargetedDiagnosticMethodRequest:
    alignment_inputs: tuple[TargetedHypothesisAlignmentInput, ...]
    backfill_sources: Mapping[str, OwnerBackfillSource]
    alignment_config: AlignmentConfig
    peak_config: ExtractionConfig
    targets: tuple[Target, ...]
    processing_attempt: ProcessingAttemptIdentity
    authoritative_legacy_output: RunOutput
    prior_current_delivery_id: str


@dataclass(frozen=True, slots=True)
class TargetedDiagnosticMethodResult:
    processing_request_id: str
    processing_attempt_id: str
    groups: tuple[CrossSamplePeakGroupHypothesis, ...]
    cells: tuple[TargetedHypothesisCell, ...]
    projections: tuple[TargetedDiagnosticProjection, ...]
    authoritative_legacy_output: RunOutput


TargetedDiagnosticDeliveryStatus = Literal[
    "sealed_diagnostic",
    "quarantined",
    "rolled_back",
]


@dataclass(frozen=True, slots=True)
class TargetedDiagnosticDelivery:
    status: TargetedDiagnosticDeliveryStatus
    processing_attempt_id: str
    diagnostic_csv_path: Path | None
    diagnostic_csv_sha256: str | None
    prior_current_delivery_id: str
    active_output: RunOutput
    quarantine_reason: str = ""


def targeted_attempt_namespace_component(processing_attempt_id: str) -> str:
    """Return a deterministic filesystem-safe component for one exact attempt ID."""

    if (
        not isinstance(processing_attempt_id, str)
        or not processing_attempt_id.startswith("xic.processing-attempt.v1:")
    ):
        raise TargetedDiagnosticMethodError(
            "processing_attempt_id must be an owner-issued typed identity"
        )
    digest = hashlib.sha256(processing_attempt_id.encode("utf-8")).hexdigest()
    return f"attempt-{digest}"


def run_targeted_diagnostic_method(
    request: TargetedDiagnosticMethodRequest,
    *,
    should_stop: Callable[[], bool] | None = None,
) -> TargetedDiagnosticMethodResult:
    """Run selection → Alignment → Backfill and verify legacy projection."""

    if not isinstance(request, TargetedDiagnosticMethodRequest):
        raise TargetedDiagnosticMethodError(
            "request must be TargetedDiagnosticMethodRequest"
        )
    if should_stop is not None and not callable(should_stop):
        raise TargetedDiagnosticMethodError("should_stop must be callable or None")
    if len(request.targets) != 1:
        raise TargetedDiagnosticMethodError(
            "bounded Package 2 method requires exactly one Targeted definition"
        )
    if not request.alignment_inputs:
        raise TargetedDiagnosticMethodError(
            "bounded Package 2 method requires multi-sample inputs"
        )
    target = request.alignment_inputs[0].target
    if request.targets[0] != target.definition.target:
        raise TargetedDiagnosticMethodError(
            "legacy writer target does not match the exact owner-resolved definition"
        )
    attempts = {
        item.selection.artifact.producer_processing_attempt_id
        for item in request.alignment_inputs
    }
    requests = {
        item.selection.artifact.producer_processing_request_id
        for item in request.alignment_inputs
    }
    if len(attempts) != 1 or len(requests) != 1:
        raise TargetedDiagnosticMethodError(
            "all method inputs must bind one processing request and attempt"
        )
    _validate_request_attempt(request, requests=requests, attempts=attempts)
    groups = align_selected_targeted_hypotheses(
        request.alignment_inputs,
        target=target,
        config=request.alignment_config,
    )
    return complete_targeted_diagnostic_method(request, groups)


def complete_targeted_diagnostic_method(
    request: TargetedDiagnosticMethodRequest,
    groups: tuple[CrossSamplePeakGroupHypothesis, ...],
) -> TargetedDiagnosticMethodResult:
    """Consume Alignment's exact groups without recomputing correspondence."""

    if not isinstance(request, TargetedDiagnosticMethodRequest):
        raise TargetedDiagnosticMethodError(
            "request must be TargetedDiagnosticMethodRequest"
        )
    if not isinstance(groups, tuple) or any(
        not isinstance(group, CrossSamplePeakGroupHypothesis) for group in groups
    ):
        raise TargetedDiagnosticMethodError(
            "groups must be an immutable Alignment-owned tuple"
        )
    attempts = {
        item.selection.artifact.producer_processing_attempt_id
        for item in request.alignment_inputs
    }
    requests = {
        item.selection.artifact.producer_processing_request_id
        for item in request.alignment_inputs
    }
    if len(attempts) != 1 or len(requests) != 1:
        raise TargetedDiagnosticMethodError(
            "all method inputs must bind one processing request and attempt"
        )
    _validate_request_attempt(request, requests=requests, attempts=attempts)
    cells = build_targeted_hypothesis_cells(
        request.alignment_inputs,
        groups,
        backfill_sources=request.backfill_sources,
        alignment_config=request.alignment_config,
        peak_config=request.peak_config,
    )
    projections = _validate_and_project_legacy(
        cells,
        request.authoritative_legacy_output,
        target_label=request.targets[0].label,
    )
    return TargetedDiagnosticMethodResult(
        processing_request_id=next(iter(requests)),
        processing_attempt_id=next(iter(attempts)),
        groups=groups,
        cells=cells,
        projections=projections,
        authoritative_legacy_output=request.authoritative_legacy_output,
    )


def canonical_targeted_diagnostic_method_bytes(
    result: TargetedDiagnosticMethodResult,
) -> bytes:
    payload = {
        "schema_version": "targeted_diagnostic_method_v1",
        "processing_request_id": result.processing_request_id,
        "processing_attempt_id": result.processing_attempt_id,
        "groups": [
            {
                "group_hypothesis_id": group.group_hypothesis_id,
                "owner_ids": group.owner_ids,
            }
            for group in result.groups
        ],
        "cells": [
            {
                "sample_stem": cell.sample_stem,
                "selection_evidence_artifact_id": (cell.selection_evidence_artifact_id),
                "status": cell.status,
                "group_hypothesis_id": cell.group_hypothesis_id,
                "alignment_owner_ids": cell.alignment_owner_ids,
                "source_hypothesis_id": cell.source_hypothesis_id,
                "reason": cell.reason,
            }
            for cell in result.cells
        ],
        "projections": [
            {
                "sample_stem": projection.sample_stem,
                "status": projection.status,
                "rt": projection.rt,
                "area": projection.area,
                "intensity": projection.intensity,
                "peak_start": projection.peak_start,
                "peak_end": projection.peak_end,
                "peak_width": projection.peak_width,
                "confidence": projection.confidence,
                "reason": projection.reason,
                "counted_detection": projection.counted_detection,
                "nl_token": projection.nl_token,
            }
            for projection in result.projections
        ],
    }
    return (
        json.dumps(
            payload,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
            allow_nan=False,
        )
        + "\n"
    ).encode("utf-8")


def settle_targeted_diagnostic_delivery(
    result: TargetedDiagnosticMethodResult,
    *,
    targeted_finalization: SupervisorFinalizationRecord,
    alignment_finalization: SupervisorFinalizationRecord,
    config: ExtractionConfig,
    targets: tuple[Target, ...],
    authoritative_legacy_csv_bytes: bytes,
    prior_current_delivery_id: str,
) -> TargetedDiagnosticDelivery:
    """Write only after both real runtime consumers close the exact attempt."""

    finalizations = (targeted_finalization, alignment_finalization)
    for finalization in finalizations:
        if finalization.attempt.processing_attempt_id != result.processing_attempt_id:
            raise TargetedDiagnosticMethodError(
                "runtime finalization references a different method attempt"
            )
    failed = tuple(
        finalization.terminal_outcome.value
        for finalization in finalizations
        if finalization.terminal_outcome is not AttemptTerminalOutcome.SUCCEEDED
    )
    if failed:
        return TargetedDiagnosticDelivery(
            status="quarantined",
            processing_attempt_id=result.processing_attempt_id,
            diagnostic_csv_path=None,
            diagnostic_csv_sha256=None,
            prior_current_delivery_id=prior_current_delivery_id,
            active_output=result.authoritative_legacy_output,
            quarantine_reason="runtime attempt did not close successfully: "
            + ",".join(failed),
        )
    output_path = (
        config.output_csv.parent
        / "successor_attempts"
        / targeted_attempt_namespace_component(result.processing_attempt_id)
        / "xic_results.csv"
    )
    attempt_config = replace(config, output_csv=output_path)
    write_wide_csv(
        attempt_config,
        targets,
        result.authoritative_legacy_output.file_results,
    )
    observed = output_path.read_bytes()
    if observed != authoritative_legacy_csv_bytes:
        raise TargetedDiagnosticMethodParityError(
            "successor attempt xic_results.csv bytes differ from authoritative legacy"
        )
    return TargetedDiagnosticDelivery(
        status="sealed_diagnostic",
        processing_attempt_id=result.processing_attempt_id,
        diagnostic_csv_path=output_path,
        diagnostic_csv_sha256=hashlib.sha256(observed).hexdigest(),
        prior_current_delivery_id=prior_current_delivery_id,
        active_output=result.authoritative_legacy_output,
    )


def rollback_targeted_diagnostic_delivery(
    delivery: TargetedDiagnosticDelivery,
    result: TargetedDiagnosticMethodResult,
) -> TargetedDiagnosticDelivery:
    """Roll back the whole attempt without deleting its diagnostic evidence."""

    if delivery.processing_attempt_id != result.processing_attempt_id:
        raise TargetedDiagnosticMethodError(
            "delivery and method result reference different attempts"
        )
    return replace(
        delivery,
        status="rolled_back",
        active_output=result.authoritative_legacy_output,
    )


def _validate_and_project_legacy(
    cells: tuple[TargetedHypothesisCell, ...],
    legacy: RunOutput,
    *,
    target_label: str,
) -> tuple[TargetedDiagnosticProjection, ...]:
    file_results = {item.sample_name: item for item in legacy.file_results}
    if len(file_results) != len(legacy.file_results):
        raise TargetedDiagnosticMethodParityError(
            "legacy fixture contains duplicate sample identities"
        )
    if set(file_results) != {cell.sample_stem for cell in cells}:
        raise TargetedDiagnosticMethodParityError(
            "successor and legacy sample universes differ"
        )
    projections = []
    for cell in cells:
        file_result = file_results[cell.sample_stem]
        if cell.status == "failed":
            if not file_result.error:
                raise TargetedDiagnosticMethodParityError(
                    "failed successor cell does not match legacy file failure"
                )
            successor_confidence, successor_reason = _successor_output_semantics(cell)
            if (
                successor_confidence != "ERROR"
                or successor_reason != file_result.error
            ):
                raise TargetedDiagnosticMethodParityError(
                    "successor confidence/reason differs from legacy"
                )
            projections.append(
                TargetedDiagnosticProjection(
                    sample_stem=cell.sample_stem,
                    status=cell.status,
                    rt=None,
                    area=None,
                    intensity=None,
                    peak_start=None,
                    peak_end=None,
                    peak_width=None,
                    confidence=successor_confidence,
                    reason=successor_reason,
                    counted_detection=False,
                    nl_token=None,
                )
            )
            continue
        if file_result.error is not None or target_label not in file_result.results:
            raise TargetedDiagnosticMethodParityError(
                "legacy result is missing the successor sample-target cell"
            )
        legacy_result = file_result.results[target_label]
        product_projection = legacy_result.targeted_product_projection
        if product_projection is None:
            raise TargetedDiagnosticMethodParityError(
                "legacy result is missing counted-state projection"
            )
        successor_confidence, successor_reason = _successor_output_semantics(cell)
        successor_nl_token = _successor_output_nl_token(cell)
        if (
            legacy_result.confidence != successor_confidence
            or legacy_result.reason != successor_reason
            or legacy_result.nl_token != successor_nl_token
        ):
            raise TargetedDiagnosticMethodParityError(
                "successor confidence/reason/NL token differs from legacy"
            )
        values = (
            legacy_result.reported_rt,
            legacy_result.reported_peak_area,
            legacy_result.reported_peak_intensity,
            legacy_result.reported_peak_start,
            legacy_result.reported_peak_end,
            legacy_result.reported_peak_width,
        )
        if cell.status in {"observed", "backfilled"}:
            integration = cell.selected_integration
            if integration is None:
                raise TargetedDiagnosticMethodParityError(
                    "selected successor cell is missing integration"
                )
            expected = (
                integration.rt_apex_min,
                (
                    integration.area_ms1_morphology
                    if integration.area_ms1_morphology is not None
                    else integration.area_raw_counts_seconds
                ),
                integration.height_raw,
                integration.rt_left_min,
                integration.rt_right_min,
                abs(integration.rt_width_min),
            )
            if values != expected or not product_projection.counted_detection:
                raise TargetedDiagnosticMethodParityError(
                    "selected peak/boundary/area/count differs from legacy"
                )
        elif any(value is not None for value in values) or (
            product_projection.counted_detection
        ):
            raise TargetedDiagnosticMethodParityError(
                "non-selected successor cell differs from legacy counted state"
            )
        projections.append(
            TargetedDiagnosticProjection(
                sample_stem=cell.sample_stem,
                status=cell.status,
                rt=values[0],
                area=values[1],
                intensity=values[2],
                peak_start=values[3],
                peak_end=values[4],
                peak_width=values[5],
                confidence=successor_confidence,
                reason=successor_reason,
                counted_detection=product_projection.counted_detection,
                nl_token=successor_nl_token,
            )
        )
    return tuple(projections)


def _validate_request_attempt(
    request: TargetedDiagnosticMethodRequest,
    *,
    requests: set[str],
    attempts: set[str],
) -> None:
    attempt = request.processing_attempt
    if not isinstance(attempt, ProcessingAttemptIdentity):
        raise TargetedDiagnosticMethodError(
            "processing_attempt must be an owner-issued identity"
        )
    if (
        requests != {attempt.processing_request_id}
        or attempts != {attempt.processing_attempt_id}
    ):
        raise TargetedDiagnosticMethodError(
            "processing_attempt does not match the method input lineage"
        )


def _successor_output_semantics(
    cell: TargetedHypothesisCell,
) -> tuple[str, str]:
    """Project bounded successor semantics before consulting legacy output.

    The reason preconditions keep this comparator sensitive to the actual
    Selection/Alignment/Backfill terminal explanation.  The returned wording is
    the frozen legacy writer contract for this migration fixture; it is not a
    new product decision owner.
    """

    if cell.status == "failed":
        if not cell.reason:
            raise TargetedDiagnosticMethodParityError(
                "failed successor cell is missing its owner reason"
            )
        return "ERROR", cell.reason
    if cell.status == "observed":
        _require_successor_reason(
            cell,
            "unique Discovery hypothesis preserved by Alignment",
        )
        return "HIGH", "legacy authoritative selected result"
    if cell.status == "backfilled":
        _require_successor_reason(
            cell,
            "Alignment correspondence and local evidence authorized Backfill",
        )
        return "BACKFILLED", "legacy authoritative selected result"
    if cell.status == "ambiguous":
        if cell.reason not in {
            "multiple Discovery hypotheses cannot authorize Backfill",
            "Alignment correspondence is not unique for Backfill",
        }:
            raise TargetedDiagnosticMethodParityError(
                "ambiguous successor cell has a non-canonical owner reason"
            )
        return "AMBIGUOUS", "legacy authoritative non-selected result"
    if cell.status == "true_missing":
        _require_successor_reason(
            cell,
            "owner-centered MS1 backfill query found no accepted peak",
        )
        return "NOT_DETECTED", "legacy authoritative non-selected result"
    if cell.status == "unassessable":
        if not cell.reason:
            raise TargetedDiagnosticMethodParityError(
                "unassessable successor cell is missing its owner reason"
            )
        return "NOT_DETECTED", "legacy authoritative non-selected result"
    if cell.status == "unchecked":
        if not cell.reason:
            raise TargetedDiagnosticMethodParityError(
                "unchecked successor cell is missing its owner reason"
            )
        return "NOT_DETECTED", "legacy authoritative non-selected result"
    raise TargetedDiagnosticMethodParityError(
        f"unsupported successor semantic status: {cell.status}"
    )


def _successor_output_nl_token(cell: TargetedHypothesisCell) -> str | None:
    """Project the frozen bounded writer token without consulting legacy DTOs."""

    if cell.status not in {
        "observed",
        "backfilled",
        "ambiguous",
        "true_missing",
        "unassessable",
        "unchecked",
    }:
        raise TargetedDiagnosticMethodParityError(
            f"unsupported successor NL-token status: {cell.status}"
        )
    return None


def _require_successor_reason(
    cell: TargetedHypothesisCell,
    expected: str,
) -> None:
    if cell.reason != expected:
        raise TargetedDiagnosticMethodParityError(
            f"{cell.status} successor cell has a non-canonical owner reason"
        )
