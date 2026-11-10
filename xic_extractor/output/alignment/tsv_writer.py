"""Untargeted TSV outputs rendered from each finalized group and its cells.

A batch is rendered in one pass, one group at a time, so no output keeps the
whole Matrix in memory. Row-level values that exist only in the written
outputs (the reported m/z and RT, the ordinal ``feature_id``, the cell's
source candidate) are derived here when written.
"""

from __future__ import annotations

import csv
import math
from collections import Counter
from collections.abc import Callable, Iterable, Mapping
from contextlib import ExitStack
from pathlib import Path
from typing import TYPE_CHECKING, Literal, cast

from xic_extractor.alignment.backfill.evidence_contract import (
    ALIGNMENT_BACKFILL_CELL_EVIDENCE_COLUMNS,
    ALIGNMENT_BACKFILL_CELL_EVIDENCE_SCHEMA_VERSION,
)
from xic_extractor.alignment.cell_observation.observations import (
    FinalGroupCellObservation,
)
from xic_extractor.alignment.membership_selection import AcceptedMembershipRisk
from xic_extractor.alignment.owner_group_delivery import feature_id_label
from xic_extractor.alignment.primary_matrix_area import (
    primary_matrix_area_from_integration,
)
from xic_extractor.alignment.result.scientific_alignment import (
    FinalizedScientificAlignment,
    ObservedSearchSubject,
)
from xic_extractor.output.alignment.matrix_output_contract import (
    MATRIX_REVIEW_COLUMNS,
    bind_matrix_review_row,
    canonical_mz_text,
    serialize_public_matrix_table,
)
from xic_extractor.output.alignment.output_rows import (
    format_float,
    format_value,
    safe_rate,
)
from xic_extractor.peak_detection.observations import PeakObservationContent
from xic_extractor.tabular_io import escape_excel_formula

if TYPE_CHECKING:
    from xic_extractor.alignment.cell_decision.owner import ProductionCellDecision
    from xic_extractor.alignment.cross_sample.peak_groups import (
        CrossSamplePeakGroupHypothesis,
    )
    from xic_extractor.alignment.result.application_result import (
        FinalizedUntargetApplicationResult,
        FinalizedUntargetBatchApplicationResult,
    )

PRESENTATION_FORMULA_VERSION = "cross_sample_owner_arithmetic_mean"
# Mean of Discovery's apex MS1 readings of the row's direct members.
APEX_READING_CENTER_MZ_BASIS = "discovery_ms1_apex_mean"

CellOrigin = Literal["detected", "backfilled"]

ALIGNMENT_REVIEW_COLUMNS = (
    "feature_id",
    *(
        column
        for column in MATRIX_REVIEW_COLUMNS
        if column not in {"feature_id", "accepted_cell_count"}
    ),
    "neutral_loss_tag",
    "family_center_mz",
    "family_center_rt",
    "family_product_mz",
    "family_observed_neutral_loss_da",
    "physical_peak_keys",
    "detected_count",
    "not_searched_count",
    "unresolved_count",
    "present_rate",
    "identity_reason",
    "accepted_cell_count",
    "accepted_backfill_count",
    "include_in_primary_matrix",
    "row_flags",
    "membership_risk_flags",
    "warning",
    "reason",
)

ALIGNMENT_CELLS_COLUMNS = (
    "feature_id",
    "sample_stem",
    "status",
    "area",
    "primary_matrix_area",
    "primary_matrix_area_source",
    "primary_matrix_area_reason",
    "apex_rt",
    "height",
    "peak_start_rt",
    "peak_end_rt",
    "noise_sigma",
    "signal_to_noise",
    "low_signal_to_noise",
    "source_candidate_id",
    "neutral_loss_tag",
    "family_center_mz",
    "family_center_rt",
    "reason",
)

STATUS_MATRIX_GROUP_COLUMNS = (
    "feature_id",
    "neutral_loss_tag",
    "family_center_mz",
    "family_center_rt",
)


def completed_untarget_batch_sample_order(
    completed: FinalizedUntargetBatchApplicationResult,
) -> tuple[str, ...]:
    """Return the canonical output columns, including for an empty batch."""

    canonical_samples = tuple(completed.canonical_samples)
    injection_orders = tuple(sample.injection_order for sample in canonical_samples)
    if any(
        isinstance(order, bool) or not isinstance(order, int) or order < 1
        for order in injection_orders
    ) or len(set(injection_orders)) != len(injection_orders):
        raise ValueError(
            "writer projection requires unique positive sample injection order"
        )
    ordered_samples = tuple(
        sorted(
            canonical_samples,
            key=lambda sample: cast(int, sample.injection_order),
        )
    )
    sample_stems = tuple(sample.raw_stem for sample in ordered_samples)
    if not sample_stems or len(sample_stems) != len(set(sample_stems)):
        raise ValueError("writer projection requires unique canonical sample stems")
    return sample_stems


def write_alignment_tsvs(
    paths: Mapping[str, Path],
    completed: FinalizedUntargetBatchApplicationResult,
    *,
    consume_child: Callable[[FinalizedUntargetApplicationResult], None] | None = None,
    ms1_apex_mz_by_candidate: Mapping[str, float] | None = None,
    membership_risks_by_owner: Mapping[str, AcceptedMembershipRisk] | None = None,
) -> int:
    """Render the requested TSVs (``matrix``, ``review``, ``backfill``,
    ``cells``, ``status``) in one bounded pass and return the group count.

    ``ms1_apex_mz_by_candidate`` holds Discovery's apex MS1 readings; they set
    only the reported m/z (see ``_reported_center``).
    """

    sample_order = completed_untarget_batch_sample_order(completed)
    headers = {
        "matrix": serialize_public_matrix_table((), sample_columns=sample_order).header,
        "review": ALIGNMENT_REVIEW_COLUMNS,
        "backfill": ALIGNMENT_BACKFILL_CELL_EVIDENCE_COLUMNS,
        "cells": ALIGNMENT_CELLS_COLUMNS,
        "status": (*STATUS_MATRIX_GROUP_COLUMNS, *sample_order),
    }
    risks_by_owner = membership_risks_by_owner or {}
    source_candidate_owners: dict[str, str] = {}
    seen_physical_peaks: set[PeakObservationContent] = set()
    group_count = 0
    matrix_row_count = 0
    with ExitStack() as stack:
        writers = {}
        for name, path in paths.items():
            path.parent.mkdir(parents=True, exist_ok=True)
            handle = stack.enter_context(
                path.open("w", newline="", encoding="utf-8", buffering=1024 * 1024)
            )
            writer = csv.writer(handle, delimiter="\t", lineterminator="\n")
            writer.writerow([escape_excel_formula(column) for column in headers[name]])
            writers[name] = writer

        def emit(name: str, rows: Iterable[Mapping[str, object]]) -> None:
            if name in writers:
                for row in rows:
                    writers[name].writerow(
                        [format_value(row.get(column, "")) for column in headers[name]]
                    )

        for child in completed.children:
            core = child.scientific_core
            direct_candidate_ids = _direct_candidate_ids(core)
            candidate_by_physical_id = (
                _direct_candidate_ids_by_physical_id(core)
                if ms1_apex_mz_by_candidate
                else {}
            )
            for group, canonical_cells in child.group_cells:
                group_count += 1
                feature_id = feature_id_label(group_count)
                cells = _cells_in_sample_order(canonical_cells, sample_order)
                evidence = tuple(
                    cast(FinalGroupCellObservation, cell.evidence) for cell in cells
                )
                center_mz, center_rt, center_mz_basis, peaks = _reported_center(
                    evidence, candidate_by_physical_id, ms1_apex_mz_by_candidate
                )
                duplicate_peaks = seen_physical_peaks.intersection(peaks)
                if duplicate_peaks:
                    raise ValueError(
                        "writer projection reused physical observations across rows: "
                        + ", ".join(
                            sorted(peak.physical_peak_key for peak in duplicate_peaks)
                        )
                    )
                seen_physical_peaks.update(peaks)
                tag, product_mz, loss_da, has_anchor = _cell_context_facts(evidence)
                candidate_ids = {
                    sample_stem: candidate_id
                    for sample_stem, cell, observation in zip(
                        sample_order, cells, evidence, strict=True
                    )
                    if (
                        candidate_id := _selected_cell_source(
                            cell, observation, direct_candidate_ids=direct_candidate_ids
                        )
                    )
                    is not None
                }
                risk_flags = _membership_risk_flags(
                    group,
                    risks_by_owner,
                    core.grouping_result.backfill_membership_risks,
                )
                group_columns = {
                    "feature_id": feature_id,
                    "neutral_loss_tag": tag,
                    "family_center_mz": format_value(center_mz),
                    "family_center_rt": format_value(center_rt),
                }
                matrix_review: Mapping[str, object] = {}
                if group.destination == "matrix":
                    for candidate_id in candidate_ids.values():
                        previous = source_candidate_owners.setdefault(
                            candidate_id, feature_id
                        )
                        if previous != feature_id:
                            raise ValueError(
                                "source peak cannot contribute to multiple product "
                                f"hypothesis rows: {candidate_id}"
                            )
                    matrix_row_count += 1
                    emit(
                        "matrix",
                        serialize_public_matrix_table(
                            (
                                {
                                    "Mz": center_mz,
                                    "RT": center_rt,
                                    **{
                                        sample_stem: ""
                                        if cell.value is None
                                        else format_float(cell.value)
                                        for sample_stem, cell in zip(
                                            sample_order, cells, strict=True
                                        )
                                    },
                                },
                            ),
                            sample_columns=sample_order,
                        ).rows,
                    )
                    matrix_review = bind_matrix_review_row(
                        source_review={
                            "feature_id": feature_id,
                            "matrix_mz": center_mz,
                            "matrix_rt": center_rt,
                            "center_mz_basis": center_mz_basis,
                            "accepted_cell_count": sum(
                                cell.value is not None for cell in cells
                            ),
                        },
                        index=matrix_row_count,
                    )
                emit(
                    "review",
                    (
                        _review_row(
                            group,
                            cells,
                            group_columns=group_columns,
                            matrix_review=matrix_review,
                            center_mz=center_mz,
                            center_rt=center_rt,
                            product_mz=product_mz,
                            loss_da=loss_da,
                            has_anchor=has_anchor,
                            physical_peak_keys=tuple(
                                peak.physical_peak_key for peak in peaks
                            ),
                            risk_flags=risk_flags,
                            sample_count=len(sample_order),
                        ),
                    ),
                )
                if any(cell_origin(cell) == "backfilled" for cell in cells):
                    emit(
                        "backfill",
                        (
                            {
                                **_cell_fields(
                                    group_columns, sample_stem, cell, candidate_ids
                                ),
                                "schema_version": (
                                    ALIGNMENT_BACKFILL_CELL_EVIDENCE_SCHEMA_VERSION
                                ),
                                "include_in_primary_matrix": (
                                    group.destination == "matrix"
                                ),
                                "identity_decision": (
                                    "finalized_matrix"
                                    if group.destination == "matrix"
                                    else "finalized_archive"
                                ),
                                "row_flags": _row_flags(group),
                                **dict(
                                    zip(
                                        (
                                            "backfill_query_rt_min",
                                            "backfill_query_rt_max",
                                        ),
                                        map(format_value, backfill_query_bounds(cell)),
                                        strict=True,
                                    )
                                ),
                            }
                            for sample_stem, cell in zip(
                                sample_order, cells, strict=True
                            )
                            if cell_origin(cell) is not None
                        ),
                    )
                emit(
                    "cells",
                    (
                        _cell_fields(group_columns, sample_stem, cell, candidate_ids)
                        for sample_stem, cell in zip(sample_order, cells, strict=True)
                    ),
                )
                emit(
                    "status",
                    (
                        {
                            **group_columns,
                            **{
                                sample_stem: cell_status(cell)
                                for sample_stem, cell in zip(
                                    sample_order, cells, strict=True
                                )
                            },
                        },
                    ),
                )
            if consume_child is not None:
                consume_child(child)
    return group_count


def _membership_risk_flags(
    group: CrossSamplePeakGroupHypothesis,
    risks_by_owner: Mapping[str, AcceptedMembershipRisk],
    backfill_risks: tuple[tuple[str, str, AcceptedMembershipRisk], ...],
) -> list[str]:
    """Flags of the accepted risks behind the group's members and Backfills."""

    risks = [
        risk
        for member in group.member_subjects
        if (risk := risks_by_owner.get(member.subject_id)) is not None
    ]
    risks.extend(
        risk
        for risk_group, _sample, risk in backfill_risks
        if risk_group == group.group_hypothesis_id
    )
    return sorted({flag for risk in risks for flag in risk.risk_flags})


def _cells_in_sample_order(
    canonical_cells: tuple[ProductionCellDecision, ...],
    sample_order: tuple[str, ...],
) -> tuple[ProductionCellDecision, ...]:
    by_stem = {cell.subject.canonical_sample.raw_stem: cell for cell in canonical_cells}
    if len(by_stem) != len(canonical_cells) or set(by_stem) != set(sample_order):
        raise ValueError("writer projection crossed canonical sample order")
    return tuple(by_stem[sample_stem] for sample_stem in sample_order)


def _reported_center(
    observations: tuple[FinalGroupCellObservation, ...],
    candidate_by_physical_id: Mapping[PeakObservationContent, str],
    ms1_apex_mz_by_candidate: Mapping[str, float] | None,
) -> tuple[float, float, str, tuple[PeakObservationContent, ...]]:
    """The written m/z and RT of one group, its m/z basis and its member peaks.

    RT is the mean apex of the group's direct member peaks. The m/z is their
    mean measured m/z unless any of them has a Discovery MS1 apex reading; then
    it is the mean of those readings, because the precursor m/z inferred from
    product + loss has only MS2 accuracy (``docs/product/quant-matrix.md``).
    This is the reported centre only: the group's own centre, which its
    Backfill searches used, stays on the group.
    """

    facts: dict[PeakObservationContent, tuple[float, float]] = {}
    for observation in observations:
        member_peaks = {
            peak
            for member in observation.group.member_subjects
            if member.sample_key == observation.sample_key
            for peak in member.physical_peaks
        }
        for direct in observation.direct_observations:
            peak = direct.content.physical_identity
            if peak not in member_peaks:
                continue
            if peak in facts:
                raise ValueError(
                    "Matrix presentation facts contain duplicate physical identity"
                )
            mz, rt = direct.content.mz, direct.content.integration.rt_apex_min
            if not math.isfinite(mz) or not math.isfinite(rt):
                raise ValueError(
                    "Matrix row lacks finite owner-backed Mz/RT observations"
                )
            facts[peak] = (float(mz), float(rt))
    if not facts:
        raise ValueError("Matrix row lacks owner-backed Mz/RT observations")
    ordered = tuple(sorted(facts.items(), key=lambda item: item[0].measured_order_key))
    peaks = tuple(peak for peak, _values in ordered)
    mz_values = tuple(values[0] for _peak, values in ordered)
    rt_values = tuple(values[1] for _peak, values in ordered)
    center_mz = sum(mz_values) / len(mz_values)
    center_mz_basis = PRESENTATION_FORMULA_VERSION
    readings = []
    if ms1_apex_mz_by_candidate:
        for peak in peaks:
            candidate_id = candidate_by_physical_id.get(peak)
            reading = (
                None
                if candidate_id is None
                else ms1_apex_mz_by_candidate.get(candidate_id)
            )
            if reading is not None and math.isfinite(reading):
                readings.append(float(reading))
    if readings:
        center_mz = sum(readings) / len(readings)
        center_mz_basis = APEX_READING_CENTER_MZ_BASIS
    center_rt = sum(rt_values) / len(rt_values)
    if not math.isfinite(center_mz) or not math.isfinite(center_rt):
        raise ValueError("writer projection produced non-finite coordinates")
    return center_mz, center_rt, center_mz_basis, peaks


def _cell_context_facts(
    observations: tuple[FinalGroupCellObservation, ...],
) -> tuple[str, float | None, float | None, bool]:
    """Tag, fragment, loss and anchor reported for a row.

    They are read from the groups whose evidence formed the row's cells: the
    group itself and, when Backfill extended it, the group it extended. A value
    present on several of them is reported as their mean.
    """

    groups = {
        id(cell.group_context.group): cell.group_context.group
        for observation in observations
        for cell in observation.effective_cells
    }.values()
    tags = sorted(
        {group.neutral_loss_tag for group in groups if group.neutral_loss_tag}
    )
    products = [
        group.group_product_mz for group in groups if group.group_product_mz is not None
    ]
    losses = [
        group.group_observed_neutral_loss_da
        for group in groups
        if group.group_observed_neutral_loss_da is not None
    ]
    return (
        ";".join(tags),
        sum(products) / len(products) if products else None,
        sum(losses) / len(losses) if losses else None,
        any(group.has_anchor for group in groups),
    )


def _selected_cell_source(
    decision: ProductionCellDecision,
    evidence: FinalGroupCellObservation,
    *,
    direct_candidate_ids: Mapping[tuple[str, str], str],
) -> str | None:
    """The Discovery candidate of a numeric cell read from a direct observation."""

    if decision.value is None or evidence.ms1_only_backfill_observations:
        return None
    direct = tuple(evidence.direct_observations)
    if len(direct) != 1:
        raise ValueError("numeric writer cell requires one physical source")
    hypothesis_ids = tuple(
        hypothesis.hypothesis_id for hypothesis in evidence.direct_hypotheses
    )
    if len(hypothesis_ids) != 1:
        raise ValueError(
            "direct writer cell requires one source PeakHypothesis identity"
        )
    # A source outside Discovery has no candidate ID; the physical source remains
    # retained on the completed observation.
    return direct_candidate_ids.get(
        (decision.subject.canonical_sample.sample_name, hypothesis_ids[0])
    )


def _direct_candidate_ids(
    scientific_core: FinalizedScientificAlignment,
) -> dict[tuple[str, str], str]:
    candidate_ids: dict[tuple[str, str], str] = {}
    observed_subjects: dict[tuple[str, str], ObservedSearchSubject] = {}
    for subject in scientific_core.terminal_subjects:
        if type(subject) is not ObservedSearchSubject:
            continue
        key = (subject.sample_key, subject.hypothesis.hypothesis_id)
        previous = observed_subjects.get(key)
        if previous is not None and previous.hypothesis != subject.hypothesis:
            raise ValueError(
                "finalized scientific core conflicts on a direct physical identity"
            )
        observed_subjects[key] = subject
        candidate_ids[key] = min(
            candidate_ids.get(key, subject.candidate_id), subject.candidate_id
        )
    return candidate_ids


def _direct_candidate_ids_by_physical_id(
    scientific_core: FinalizedScientificAlignment,
) -> dict[PeakObservationContent, str]:
    candidate_ids: dict[PeakObservationContent, str] = {}
    for subject in scientific_core.terminal_subjects:
        if type(subject) is not ObservedSearchSubject:
            continue
        for ref in subject.hypothesis.peak_observation_refs:
            physical_id = ref.content.physical_identity
            candidate_ids[physical_id] = min(
                candidate_ids.get(physical_id, subject.candidate_id),
                subject.candidate_id,
            )
    return candidate_ids


def cell_origin(decision: ProductionCellDecision) -> CellOrigin | None:
    """Where the cell's peak came from, or ``None`` when it reports no peak.

    A numeric value read from an MS1-only Backfill peak is ``backfilled``; one
    read from the group's own direct observation is ``detected``. A no-write
    cell that still retains a direct observation reports ``detected``.
    """

    evidence = cast(FinalGroupCellObservation, decision.evidence)
    if decision.value is not None:
        if evidence.direct_observations and not (
            evidence.ms1_only_backfill_observations
        ):
            return "detected"
        return "backfilled"
    if evidence.direct_observations and decision.variant not in {
        "not_searched",
        "unresolved",
    }:
        return "detected"
    return None


def cell_status(decision: ProductionCellDecision) -> str:
    """The cell's origin, or its no-write state when it reports no peak."""

    return cell_origin(decision) or decision.variant


def backfill_query_bounds(
    decision: ProductionCellDecision,
) -> tuple[float | None, float | None]:
    """The RT window of the cell's single Backfill search, when it has one."""

    evidence = cast(FinalGroupCellObservation, decision.evidence)
    bounds = {
        (
            assessment.content.evaluation_receipt.query_rt_min,
            assessment.content.evaluation_receipt.query_rt_max,
        )
        for assessment in evidence.method_assessments
        if assessment.content.detector_invocation_id == "untargeted-cid-nl-backfill"
    }
    return bounds.pop() if len(bounds) == 1 else (None, None)


def _cell_fields(
    group_columns: Mapping[str, object],
    sample_stem: str,
    cell: ProductionCellDecision,
    candidate_ids: Mapping[str, str],
) -> dict[str, object]:
    integration = cell.selected_integration
    quality = integration.signal_quality if integration is not None else None
    missing_area_reason = (
        primary_matrix_area_from_integration(integration).reason
        if cell_origin(cell) is not None
        else ""
    )
    return {
        "feature_id": group_columns["feature_id"],
        "sample_stem": sample_stem,
        "status": cell_status(cell),
        "area": format_value(cell.value),
        "primary_matrix_area": format_value(cell.value),
        "primary_matrix_area_source": cell.source or missing_area_reason,
        "primary_matrix_area_reason": missing_area_reason,
        "apex_rt": format_value(
            integration.rt_apex_min
            if cell.value is not None and integration is not None
            else None
        ),
        "height": format_value(
            integration.height_raw if integration is not None else None
        ),
        "peak_start_rt": format_value(
            integration.rt_left_min if integration is not None else None
        ),
        "peak_end_rt": format_value(
            integration.rt_right_min if integration is not None else None
        ),
        "noise_sigma": format_value(quality.noise_sigma) if quality is not None else "",
        "signal_to_noise": format_value(quality.signal_to_noise)
        if quality is not None
        else "",
        "low_signal_to_noise": quality.low_signal_to_noise
        if quality is not None and quality.low_signal_to_noise is not None
        else "",
        "source_candidate_id": candidate_ids.get(sample_stem, ""),
        "neutral_loss_tag": group_columns["neutral_loss_tag"],
        "family_center_mz": group_columns["family_center_mz"],
        "family_center_rt": group_columns["family_center_rt"],
        "reason": cell.source or cell.variant,
    }


def _review_row(
    group: CrossSamplePeakGroupHypothesis,
    cells: tuple[ProductionCellDecision, ...],
    *,
    group_columns: Mapping[str, object],
    matrix_review: Mapping[str, object],
    center_mz: float,
    center_rt: float,
    product_mz: float | None,
    loss_da: float | None,
    has_anchor: bool,
    physical_peak_keys: tuple[str, ...],
    risk_flags: list[str],
    sample_count: int,
) -> dict[str, object]:
    status_counts = Counter(cell_status(cell) for cell in cells)
    detected_count = status_counts["detected"]
    backfilled_count = status_counts["backfilled"]
    not_searched_count = status_counts["not_searched"]
    unresolved_count = status_counts["unresolved"]
    present_count = detected_count + backfilled_count
    return {
        "feature_id": group_columns["feature_id"],
        **matrix_review,
        "neutral_loss_tag": group_columns["neutral_loss_tag"],
        "family_center_mz": canonical_mz_text(center_mz),
        "family_center_rt": center_rt,
        "family_product_mz": product_mz,
        "family_observed_neutral_loss_da": loss_da,
        "physical_peak_keys": ";".join(physical_peak_keys),
        "detected_count": detected_count,
        "not_searched_count": not_searched_count,
        "unresolved_count": unresolved_count,
        "present_rate": safe_rate(present_count, sample_count),
        "identity_reason": group.disposition,
        "accepted_cell_count": sum(cell.value is not None for cell in cells),
        "accepted_backfill_count": backfilled_count,
        "include_in_primary_matrix": group.destination == "matrix",
        "row_flags": _row_flags(group),
        "membership_risk_flags": ";".join(risk_flags),
        "warning": _warning(
            has_anchor=has_anchor,
            sample_count=sample_count,
            detected_count=detected_count,
            backfilled_count=backfilled_count,
            not_searched_count=not_searched_count,
        ),
        "reason": _reason(
            has_anchor=has_anchor,
            present_count=present_count,
            sample_count=sample_count,
            backfilled_count=backfilled_count,
            unresolved_count=unresolved_count,
        ),
    }


def _row_flags(group: CrossSamplePeakGroupHypothesis) -> str:
    return f"finalized_route:{group.destination};{PRESENTATION_FORMULA_VERSION}"


def _warning(
    *,
    has_anchor: bool,
    sample_count: int,
    detected_count: int,
    backfilled_count: int,
    not_searched_count: int,
) -> str:
    if not has_anchor:
        return "no_anchor"
    if sample_count > 0 and not_searched_count / sample_count > 0.5:
        return "high_not_searched"
    if backfilled_count > 0 and detected_count == 0:
        return "high_backfill_dependency"
    return ""


def _reason(
    *,
    has_anchor: bool,
    present_count: int,
    sample_count: int,
    backfilled_count: int,
    unresolved_count: int,
) -> str:
    prefix = "anchor family" if has_anchor else "no anchor"
    parts = [
        prefix,
        f"{present_count}/{sample_count} present",
        f"{backfilled_count} MS1 backfilled",
    ]
    if unresolved_count:
        parts.append(f"{unresolved_count} unresolved")
    return "; ".join(parts)
