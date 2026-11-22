"""Untargeted TSV outputs rendered from each finalized group and its cells.

Each finalized child is formatted into rows when it is composed; the batch
then writes those rows in one pass in its global child order, adding only the
batch-scoped values (the ordinal ``feature_id`` and Matrix row index) and the
cross-row checks. Row-level values that exist only in the written outputs
(the reported RT, the cell's source candidate) are derived when formatting;
the reported m/z is the group's own centre.
"""

from __future__ import annotations

import csv
import hashlib
import math
from collections import Counter
from collections.abc import Callable, Iterable, Mapping, Sequence
from contextlib import ExitStack
from dataclasses import dataclass
from functools import partial
from pathlib import Path
from typing import TYPE_CHECKING, Literal, cast

from xic_extractor.alignment.backfill.evidence_contract import (
    ALIGNMENT_BACKFILL_CELL_EVIDENCE_COLUMNS,
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
from xic_extractor.alignment.rt_drift import DriftCoefficients
from xic_extractor.output.alignment.matrix_output_contract import (
    canonical_mz_text,
    serialize_public_matrix_table,
)
from xic_extractor.output.alignment.output_rows import (
    format_float,
    format_value,
)
from xic_extractor.peak_detection.observations import PeakObservation
from xic_extractor.sample_metadata import SampleMetadata
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

CellOrigin = Literal["detected", "backfilled", "ms1_only"]

ALIGNMENT_REVIEW_COLUMNS = (
    "feature_id",
    "matrix_row_index",
    "neutral_loss_tag",
    "group_center_mz",
    "group_center_rt",
    "group_product_mz",
    "group_observed_neutral_loss_da",
    "detected_count",
    "not_searched_count",
    "unresolved_count",
    "identity_reason",
    "accepted_cell_count",
    "accepted_backfill_count",
    "membership_risk_flags",
    "rt_time_coefficient",
    "rt_sample_shift_coefficient",
    "warning",
)

ALIGNMENT_CELLS_COLUMNS = (
    "feature_id",
    "sample_stem",
    "status",
    "area",
    "primary_matrix_area_source",
    "apex_rt",
    "height",
    "peak_start_rt",
    "peak_end_rt",
    "noise_sigma",
    "signal_to_noise",
    "low_signal_to_noise",
    "review_reasons",
    "source_candidate_id",
)

STATUS_MATRIX_GROUP_COLUMNS = (
    "feature_id",
    "neutral_loss_tag",
    "group_center_mz",
    "group_center_rt",
)


def untarget_sample_order(
    canonical_samples: Iterable[SampleMetadata],
) -> tuple[str, ...]:
    """Return the canonical output columns, including for an empty batch."""

    canonical_samples = tuple(canonical_samples)
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


@dataclass(frozen=True, slots=True)
class FinalMatrixValues:
    """One finalized group's unformatted values, in batch sample order."""

    mz: float
    rt: float
    areas: tuple[float | None, ...]

    def row(self, order: tuple[str, ...]) -> tuple[str, ...]:
        table = serialize_public_matrix_table(
            (
                {
                    "Mz": self.mz,
                    "RT": self.rt,
                    **{
                        stem: "" if area is None else format_float(area)
                        for stem, area in zip(order, self.areas, strict=True)
                    },
                },
            ),
            sample_columns=order,
        )
        return tuple(format_value(table.rows[0][column]) for column in table.header)


@dataclass(frozen=True, slots=True)
class UntargetGroupRows:
    """One written group's TSV rows before the batch numbers its rows.

    Matrix values stay numeric until serialization; other rows are formatted.
    Their first cell is the ``feature_id`` slot, and a review row's second cell is
    its ``matrix_row_index`` slot. Physical peaks are named by their key and a
    digest of the measured signal that makes two peaks equal.
    """

    destination: str
    physical_peaks: tuple[tuple[str, str], ...]
    source_candidate_ids: tuple[str, ...]
    rows: tuple[tuple[str, tuple[tuple[str, ...], ...]], ...]
    matrix: FinalMatrixValues | None

    def formatted_rows(
        self, sample_order: tuple[str, ...]
    ) -> dict[str, tuple[tuple[str, ...], ...]]:
        rows = dict(self.rows)
        if self.matrix is not None:
            rows["matrix"] = (self.matrix.row(sample_order),)
        return rows


@dataclass(frozen=True, slots=True)
class UntargetChildOutputs:
    """One finalized child's written rows and its review-database projection."""

    groups: tuple[UntargetGroupRows, ...]
    explanation: object | None = None


def _output_headers(sample_order: tuple[str, ...]) -> dict[str, tuple[str, ...]]:
    return {
        "matrix": serialize_public_matrix_table((), sample_columns=sample_order).header,
        "review": ALIGNMENT_REVIEW_COLUMNS,
        "backfill": ALIGNMENT_BACKFILL_CELL_EVIDENCE_COLUMNS,
        "cells": ALIGNMENT_CELLS_COLUMNS,
        "status": (*STATUS_MATRIX_GROUP_COLUMNS, *sample_order),
    }


def peak_signal_digest(peak: PeakObservation) -> str:
    """Digest of the fields that make two physical peaks equal."""

    return hashlib.blake2b(
        repr(
            (peak.sample_key, peak.mz, peak.rt, peak.intensity, peak.integration)
        ).encode("utf-8"),
        digest_size=16,
    ).hexdigest()


def project_untarget_child_rows(
    child: FinalizedUntargetApplicationResult,
    *,
    outputs: Iterable[str],
    sample_order: tuple[str, ...],
    membership_risks_by_owner: Mapping[str, AcceptedMembershipRisk] | None = None,
) -> tuple[UntargetGroupRows, ...]:
    """Format one child's groups for the requested TSVs (``matrix``,
    ``review``, ``backfill``, ``cells``, ``status``) without batch numbering."""

    requested = frozenset(outputs)
    headers = _output_headers(sample_order)
    risks_by_owner = membership_risks_by_owner or {}
    core = child.scientific_core
    direct_candidate_ids = _direct_candidate_ids(core)
    projected = []
    for group, canonical_cells in child.group_cells:
        rows: dict[str, list[tuple[str, ...]]] = {}

        def emit(
            name: str,
            values: Iterable[Mapping[str, object]],
            rows: dict[str, list[tuple[str, ...]]] = rows,
        ) -> None:
            if name in requested:
                rows.setdefault(name, []).extend(
                    tuple(format_value(row.get(column, "")) for column in headers[name])
                    for row in values
                )

        cells = _cells_in_sample_order(canonical_cells, sample_order)
        evidence = tuple(
            cast(FinalGroupCellObservation, cell.evidence) for cell in cells
        )
        peaks = _member_direct_peaks(evidence)
        center_mz = float(group.group_center_mz)
        center_rt = _member_mean_rt(peaks)
        if not math.isfinite(center_mz):
            raise ValueError("group centre m/z is not finite")
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
            "feature_id": "",
            "neutral_loss_tag": tag,
            "group_center_mz": format_value(center_mz),
            "group_center_rt": format_value(center_rt),
        }
        matrix_values = None
        if group.destination == "matrix" and "matrix" in requested:
            matrix_values = FinalMatrixValues(
                center_mz, center_rt, tuple(cell.value for cell in cells)
            )
        emit(
            "review",
            (
                _review_row(
                    group,
                    cells,
                    group_columns=group_columns,
                    center_mz=center_mz,
                    center_rt=center_rt,
                    product_mz=product_mz,
                    loss_da=loss_da,
                    has_anchor=has_anchor,
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
                        **_cell_fields(sample_stem, cell, candidate_ids),
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
                    for sample_stem, cell in zip(sample_order, cells, strict=True)
                    if cell_origin(cell) is not None
                ),
            )
        emit(
            "cells",
            (
                _cell_fields(sample_stem, cell, candidate_ids)
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
                        for sample_stem, cell in zip(sample_order, cells, strict=True)
                    },
                },
            ),
        )
        projected.append(
            UntargetGroupRows(
                destination=group.destination,
                physical_peaks=tuple(
                    (peak.physical_peak_key, peak_signal_digest(peak)) for peak in peaks
                ),
                source_candidate_ids=tuple(candidate_ids.values()),
                matrix=matrix_values,
                rows=tuple((name, tuple(values)) for name, values in rows.items()),
            )
        )
    return tuple(projected)


def write_alignment_tsvs(
    paths: Mapping[str, Path],
    completed: FinalizedUntargetBatchApplicationResult,
    *,
    consume_child: Callable[[UntargetChildOutputs, int], None] | None = None,
) -> int:
    return write_projected_alignment_tsvs(
        paths,
        completed.canonical_samples,
        completed.child_outputs,
        consume_child=consume_child,
    )


def write_projected_alignment_tsvs(
    paths: Mapping[str, Path],
    canonical_samples: Sequence[SampleMetadata],
    child_outputs: Sequence[object],
    *,
    consume_child: Callable[[UntargetChildOutputs, int], None] | None = None,
) -> int:
    """Number and write the requested TSVs from the batch's projected rows in
    one bounded pass, and return the group count.

    ``consume_child`` receives each child's outputs with the count of groups
    written before it, so its review records name the same ``feature_id``.
    """

    sample_order = untarget_sample_order(canonical_samples)
    headers = _output_headers(sample_order)
    source_candidate_owners: dict[str, str] = {}
    seen_physical_peaks: set[tuple[str, str]] = set()
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

        for child in child_outputs:
            child = cast(UntargetChildOutputs, child)
            first_group = group_count
            for group in child.groups:
                group_count += 1
                feature_id = format_value(feature_id_label(group_count))
                duplicate_peaks = seen_physical_peaks.intersection(group.physical_peaks)
                if duplicate_peaks:
                    raise ValueError(
                        "writer projection reused physical observations across rows: "
                        + ", ".join(sorted(key for key, _digest in duplicate_peaks))
                    )
                seen_physical_peaks.update(group.physical_peaks)
                matrix_row_index = ""
                if group.destination == "matrix":
                    for candidate_id in group.source_candidate_ids:
                        previous = source_candidate_owners.setdefault(
                            candidate_id, feature_id
                        )
                        if previous != feature_id:
                            raise ValueError(
                                "source peak cannot contribute to multiple product "
                                f"hypothesis rows: {candidate_id}"
                            )
                    matrix_row_count += 1
                    matrix_row_index = format_value(matrix_row_count)
                for name, rows in group.formatted_rows(sample_order).items():
                    row_writer = writers.get(name)
                    if row_writer is None:
                        continue
                    for row in rows:
                        if name == "matrix":
                            row_writer.writerow(row)
                        elif name == "review":
                            row_writer.writerow(
                                (feature_id, matrix_row_index, *row[2:])
                            )
                        else:
                            row_writer.writerow((feature_id, *row[1:]))
            if consume_child is not None:
                consume_child(child, first_group)
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


def _member_direct_peaks(
    observations: tuple[FinalGroupCellObservation, ...],
) -> tuple[PeakObservation, ...]:
    """The group's member peaks read as direct observations, in measured order."""

    peaks: set[PeakObservation] = set()
    for observation in observations:
        member_peaks = {
            peak
            for member in observation.group.member_subjects
            if member.sample_key == observation.sample_key
            for peak in member.physical_peaks
        }
        for direct in observation.direct_observations:
            peak = direct.physical_identity
            if peak not in member_peaks:
                continue
            if peak in peaks:
                raise ValueError(
                    "Matrix presentation facts contain duplicate physical identity"
                )
            peaks.add(peak)
    if not peaks:
        group = observations[0].group
        if group.destination == "matrix":
            raise ValueError("Matrix row lacks owner-backed Mz/RT observations")
        peaks.update(
            peak for member in group.member_subjects for peak in member.physical_peaks
        )
    return tuple(sorted(peaks, key=lambda peak: peak.measured_order_key))


def _member_mean_rt(peaks: tuple[PeakObservation, ...]) -> float:
    """The written RT of a group: the mean apex of its member peaks."""

    for peak in peaks:
        if not math.isfinite(peak.integration.rt_apex_min):
            raise ValueError("Matrix row lacks finite owner-backed RT observations")
    return sum(float(peak.integration.rt_apex_min) for peak in peaks) / len(peaks)


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

    if (
        decision.value is None
        or evidence.ms1_only_backfill_observations
        or evidence.ms1_only_group_observations
    ):
        return None
    direct = tuple(evidence.direct_observations)
    if len(direct) != 1:
        raise ValueError("numeric writer cell requires one physical source")
    hypothesis_ids = tuple(
        hypothesis.hypothesis_id for hypothesis in evidence.direct_hypotheses
    )
    if len(hypothesis_ids) != 1:
        raise ValueError(
            "direct writer cell requires one source MeasuredPeakHypothesis identity"
        )
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


def cell_origin(decision: ProductionCellDecision) -> CellOrigin | None:
    """Where the cell's peak came from, or ``None`` when it reports no peak.

    A numeric value read from an MS1-only Backfill peak is ``backfilled``; one
    read from the group's own direct observation is ``detected``. A no-write
    cell that still retains a direct observation reports ``detected``.
    """

    evidence = cast(FinalGroupCellObservation, decision.evidence)
    if evidence.ms1_only_group_observations and decision.variant != "unresolved":
        return "ms1_only"
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
            assessment.evaluation_receipt.query_rt_min,
            assessment.evaluation_receipt.query_rt_max,
        )
        for assessment in evidence.method_assessments
        if assessment.detector_invocation_id == "untargeted-cid-nl-backfill"
    }
    return bounds.pop() if len(bounds) == 1 else (None, None)


def _cell_fields(
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
        "feature_id": "",
        "sample_stem": sample_stem,
        "status": cell_status(cell),
        "area": format_value(cell.value),
        "primary_matrix_area_source": cell.source or missing_area_reason,
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
        "review_reasons": ";".join(cell.review_reasons),
        "source_candidate_id": candidate_ids.get(sample_stem, ""),
    }


def _review_row(
    group: CrossSamplePeakGroupHypothesis,
    cells: tuple[ProductionCellDecision, ...],
    *,
    group_columns: Mapping[str, object],
    center_mz: float,
    center_rt: float,
    product_mz: float | None,
    loss_da: float | None,
    has_anchor: bool,
    risk_flags: list[str],
    sample_count: int,
) -> dict[str, object]:
    status_counts = Counter(cell_status(cell) for cell in cells)
    detected_count = status_counts["detected"]
    backfilled_count = status_counts["backfilled"]
    not_searched_count = status_counts["not_searched"]
    unresolved_count = status_counts["unresolved"]
    drift = (
        group.inference_basis.rt_drift
        if group.inference_basis is not None
        else DriftCoefficients()
    )
    return {
        "feature_id": group_columns["feature_id"],
        "matrix_row_index": "",
        "neutral_loss_tag": group_columns["neutral_loss_tag"],
        "group_center_mz": canonical_mz_text(center_mz),
        "group_center_rt": center_rt,
        "group_product_mz": product_mz,
        "group_observed_neutral_loss_da": loss_da,
        "detected_count": detected_count,
        "not_searched_count": not_searched_count,
        "unresolved_count": unresolved_count,
        "identity_reason": group.disposition,
        "accepted_cell_count": sum(cell.value is not None for cell in cells),
        "accepted_backfill_count": backfilled_count,
        "membership_risk_flags": ";".join(risk_flags),
        "rt_time_coefficient": drift.time,
        "rt_sample_shift_coefficient": drift.sample_shift,
        "warning": _warning(
            has_anchor=has_anchor,
            sample_count=sample_count,
            detected_count=detected_count,
            backfilled_count=backfilled_count,
            not_searched_count=not_searched_count,
        ),
    }


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


BATCH_REVIEW_COLUMNS = (*ALIGNMENT_REVIEW_COLUMNS, "batch_ids", "batch_pairing")


@dataclass(frozen=True, slots=True)
class BatchRowSource:
    """One independently aligned batch's projected rows.

    ``feature_key`` names the correspondence feature a row carries, or None
    for a row the correspondence did not consider (an archived group).
    """

    batch_id: str
    canonical_samples: Sequence[SampleMetadata]
    child_outputs: Sequence[object]
    feature_key: Callable[[UntargetGroupRows], str | None]


def write_batch_alignment_tsvs(
    paths: Mapping[str, Path],
    sources: Sequence[BatchRowSource],
    *,
    reference_batch: str,
    partners: Mapping[tuple[str, str], tuple[tuple[str, str], ...]],
    states: Mapping[tuple[str, str], str],
    to_reference_rt: Callable[[str, float], float | None],
    consume_row: Callable[[str, Sequence[tuple[str, int, int]]], None] | None = None,
) -> int:
    """Write independently aligned batches as one set of TSVs.

    A reference row carries its paired rows' cells; every other row keeps its
    own batch's cells, and another batch's samples are ``not_searched``. Rows
    are rendered from each batch's own projection; no cell is recomputed.
    ``consume_row`` receives each written row's feature ID and the place
    (batch, child index, offset in the child) of each of its parts.
    """
    global_order = untarget_sample_order(
        sample for source in sources for sample in source.canonical_samples
    )
    headers = {**_output_headers(global_order), "review": BATCH_REVIEW_COLUMNS}
    orders = {
        source.batch_id: untarget_sample_order(source.canonical_samples)
        for source in sources
    }
    paired_elsewhere = {
        member
        for key, members in partners.items()
        if key[0] == reference_batch
        for member in members
    }
    located: dict[tuple[str, str], tuple[UntargetGroupRows, tuple[str, int, int]]] = {}
    for source in sources:
        if source.batch_id == reference_batch:
            continue
        for child_index, child in enumerate(source.child_outputs):
            groups = cast(UntargetChildOutputs, child).groups
            for offset, group in enumerate(groups):
                key = source.feature_key(group)
                if key is not None and (source.batch_id, key) in paired_elsewhere:
                    if (source.batch_id, key) in located:
                        raise ValueError(f"two written rows carry one feature: {key}")
                    located[(source.batch_id, key)] = (
                        group,
                        (source.batch_id, child_index, offset),
                    )

    source_candidate_owners: dict[str, str] = {}
    seen_physical_peaks: set[tuple[str, str]] = set()
    written_partners: set[tuple[str, str]] = set()
    group_count = 0
    matrix_row_count = 0
    ordered_sources = sorted(
        sources, key=lambda item: (item.batch_id != reference_batch, item.batch_id)
    )
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
        for source in ordered_sources:
            for child_index, child in enumerate(source.child_outputs):
                groups = cast(UntargetChildOutputs, child).groups
                for offset, group in enumerate(groups):
                    key = source.feature_key(group)
                    member = (source.batch_id, key or "")
                    if key is not None and member in paired_elsewhere:
                        continue
                    parts = [(source.batch_id, group)]
                    places = [(source.batch_id, child_index, offset)]
                    if key is not None and source.batch_id == reference_batch:
                        for partner in partners.get(member, ()):
                            partner_group, partner_place = located[partner]
                            parts.append((partner[0], partner_group))
                            places.append(partner_place)
                            written_partners.add(partner)
                    group_count += 1
                    feature_id = format_value(feature_id_label(group_count))
                    peaks = {peak for _, part in parts for peak in part.physical_peaks}
                    duplicate_peaks = seen_physical_peaks.intersection(peaks)
                    if duplicate_peaks:
                        raise ValueError(
                            "writer projection reused physical observations across "
                            "rows: "
                            + ", ".join(sorted(key for key, _ in duplicate_peaks))
                        )
                    seen_physical_peaks.update(peaks)
                    matrix_row_index = ""
                    if group.destination == "matrix":
                        for _, part in parts:
                            for candidate_id in part.source_candidate_ids:
                                previous = source_candidate_owners.setdefault(
                                    candidate_id, feature_id
                                )
                                if previous != feature_id:
                                    raise ValueError(
                                        "source peak cannot contribute to multiple "
                                        f"product hypothesis rows: {candidate_id}"
                                    )
                        matrix_row_count += 1
                        matrix_row_index = format_value(matrix_row_count)
                    mapped_batch = (
                        source.batch_id if source.batch_id != reference_batch else None
                    )
                    batch_rows = [
                        (orders[batch_id], part.formatted_rows(orders[batch_id]))
                        for batch_id, part in parts
                    ]
                    for name, row_writer in writers.items():
                        rows = _merge_batch_rows(
                            name,
                            [(order, rows.get(name, ())) for order, rows in batch_rows],
                            global_order,
                            headers[name],
                            partial(_reference_rt_text, to_reference_rt, mapped_batch),
                        )
                        for row in rows:
                            if name == "matrix":
                                row_writer.writerow(row)
                            elif name == "review":
                                row_writer.writerow(
                                    (
                                        feature_id,
                                        matrix_row_index,
                                        *row[2:],
                                        ";".join(batch_id for batch_id, _ in parts),
                                        states.get(member, "") if key else "",
                                    )
                                )
                            else:
                                row_writer.writerow((feature_id, *row[1:]))
                    if consume_row is not None:
                        consume_row(feature_id, places)
    if written_partners != paired_elsewhere:
        raise ValueError(
            "paired features without a written reference row: "
            + ", ".join(sorted(key for _, key in paired_elsewhere - written_partners))
        )
    return group_count


def _reference_rt_text(
    to_reference_rt: Callable[[str, float], float | None],
    batch_id: str | None,
    text: str,
) -> str:
    """A non-reference row's written RT in the reference batch's coordinate."""
    if batch_id is None or not text:
        return text
    mapped = to_reference_rt(batch_id, float(text))
    return text if mapped is None else format_value(mapped)


def _merge_batch_rows(
    name: str,
    parts: Sequence[tuple[tuple[str, ...], tuple[tuple[str, ...], ...]]],
    global_order: tuple[str, ...],
    header: tuple[str, ...],
    reference_rt: Callable[[str], str],
) -> tuple[tuple[str, ...], ...]:
    """One output's rows for a written row assembled from its batches' rows."""
    present = [(order, rows) for order, rows in parts if rows]
    if not present:
        return ()
    if name in {"cells", "backfill"}:
        by_sample = {row[1]: row for _, rows in present for row in rows}
        if name == "backfill":
            return tuple(by_sample[stem] for stem in global_order if stem in by_sample)
        empty = ("",) * (len(header) - 3)
        return tuple(
            by_sample.get(stem) or ("", stem, "not_searched", *empty)
            for stem in global_order
        )
    if name in {"matrix", "status"}:
        fixed = 2 if name == "matrix" else 4
        values: dict[str, str] = {}
        for order, rows in present:
            values.update(zip(order, rows[0][fixed:], strict=True))
        first = list(present[0][1][0][:fixed])
        first[fixed - 1] = reference_rt(first[fixed - 1])
        missing = "" if name == "matrix" else "not_searched"
        return ((*first, *(values.get(stem, missing) for stem in global_order)),)
    if name == "review":
        columns = ALIGNMENT_REVIEW_COLUMNS
        records = [dict(zip(columns, rows[0], strict=True)) for _, rows in present]
        merged = dict(records[0])
        merged["group_center_rt"] = reference_rt(merged["group_center_rt"])
        for count in (
            "detected_count",
            "not_searched_count",
            "unresolved_count",
            "accepted_cell_count",
            "accepted_backfill_count",
        ):
            merged[count] = str(sum(int(record[count]) for record in records))
        merged["not_searched_count"] = str(
            int(merged["not_searched_count"])
            + len(global_order)
            - sum(len(order) for order, _ in present)
        )
        for joined in (
            "identity_reason",
            "membership_risk_flags",
            "rt_time_coefficient",
            "rt_sample_shift_coefficient",
            "warning",
        ):
            merged[joined] = ";".join(
                dict.fromkeys(
                    value
                    for record in records
                    for value in record[joined].split(";")
                    if value
                )
            )
        return (tuple(merged[column] for column in columns),)
    raise ValueError(f"unknown Untargeted output: {name}")


def feature_key_by_peaks(
    initial_groups: Iterable[CrossSamplePeakGroupHypothesis],
) -> Callable[[UntargetGroupRows], str | None]:
    """A written row's correspondence feature: the Matrix group it grew from."""
    key_by_peak = {
        owner.peak.physical_peak_key: group.group_hypothesis_id
        for group in initial_groups
        if group.destination == "matrix"
        for owner in group.owners
    }

    def feature_key(rows: UntargetGroupRows) -> str | None:
        keys = {
            key_by_peak[peak]
            for peak, _digest in rows.physical_peaks
            if peak in key_by_peak
        }
        if len(keys) > 1:
            raise ValueError("one written row grew from several Matrix groups")
        return next(iter(keys), None) if rows.destination == "matrix" else None

    return feature_key
