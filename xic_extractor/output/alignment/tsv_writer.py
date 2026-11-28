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
from collections.abc import Callable, Iterable, Mapping, Sequence
from contextlib import ExitStack
from dataclasses import dataclass
from functools import partial
from pathlib import Path
from typing import TYPE_CHECKING, cast

from xic_extractor.alignment.backfill.evidence_contract import (
    ALIGNMENT_BACKFILL_CELL_EVIDENCE_COLUMNS,
)
from xic_extractor.alignment.cell_observation.observations import (
    FinalGroupCellObservation,
)
from xic_extractor.alignment.owner_group_delivery import feature_id_label
from xic_extractor.alignment.primary_matrix_area import (
    primary_matrix_area_from_integration,
)
from xic_extractor.alignment.result.scientific_alignment import (
    FinalizedScientificAlignment,
    ObservedSearchSubject,
)
from xic_extractor.injection_timeline import InjectionSequence
from xic_extractor.output.alignment.matrix_output_contract import (
    MatrixRowIdentity,
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
    "source_candidate_id",
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
    sample_stems = InjectionSequence.from_samples(canonical_samples).stems
    if not sample_stems or len(sample_stems) != len(canonical_samples):
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
    Their first cell is the ``feature_id`` slot. Physical peaks are named by their
    key and a digest of the measured signal.
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
        "backfill": ALIGNMENT_BACKFILL_CELL_EVIDENCE_COLUMNS,
        "cells": ALIGNMENT_CELLS_COLUMNS,
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
) -> tuple[UntargetGroupRows, ...]:
    """Format one child's groups for the requested TSVs (``matrix``,
    ``backfill``, ``cells``) without batch numbering."""

    requested = frozenset(outputs)
    headers = _output_headers(sample_order)
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
        matrix_values = None
        if group.destination == "matrix" and "matrix" in requested:
            matrix_values = FinalMatrixValues(
                center_mz, center_rt, tuple(cell.value for cell in cells)
            )
        emit(
            "backfill",
            (
                {
                    **_cell_fields(sample_stem, cell, candidate_ids),
                    "backfill_query_rt_min": format_value(query_min),
                    "backfill_query_rt_max": format_value(query_max),
                }
                for sample_stem, cell in zip(sample_order, cells, strict=True)
                for query_min, query_max in (cell.backfill_query_bounds,)
                if cell.origin is not None and query_min is not None
            ),
        )
        emit(
            "cells",
            (
                _cell_fields(sample_stem, cell, candidate_ids)
                for sample_stem, cell in zip(sample_order, cells, strict=True)
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


class _AlignmentRowWriter:
    """Number and publish the finalized rows of either batch layout."""

    def __init__(
        self,
        stack: ExitStack,
        paths: Mapping[str, Path],
        headers: Mapping[str, tuple[str, ...]],
        matrix_identities: list[MatrixRowIdentity] | None,
    ) -> None:
        self.group_count = 0
        self._matrix_identities = matrix_identities
        self._source_candidate_owners: dict[str, str] = {}
        self._seen_physical_peaks: set[tuple[str, str]] = set()
        self._write_rows: dict[str, Callable[[Iterable[str]], object]] = {}
        for name, path in paths.items():
            path.parent.mkdir(parents=True, exist_ok=True)
            handle = stack.enter_context(
                path.open("w", newline="", encoding="utf-8", buffering=1024 * 1024)
            )
            writer = csv.writer(handle, delimiter="\t", lineterminator="\n")
            writer.writerow([escape_excel_formula(column) for column in headers[name]])
            self._write_rows[name] = writer.writerow

    def write(
        self,
        groups: Sequence[UntargetGroupRows],
        tables: Iterable[tuple[str, tuple[tuple[str, ...], ...]]],
    ) -> str:
        self.group_count += 1
        feature_id = format_value(feature_id_label(self.group_count))
        peaks = {peak for group in groups for peak in group.physical_peaks}
        duplicates = self._seen_physical_peaks.intersection(peaks)
        if duplicates:
            raise ValueError(
                "writer projection reused physical observations across rows: "
                + ", ".join(sorted(key for key, _digest in duplicates))
            )
        self._seen_physical_peaks.update(peaks)
        if groups[0].destination == "matrix":
            for group in groups:
                for candidate_id in group.source_candidate_ids:
                    previous = self._source_candidate_owners.setdefault(
                        candidate_id, feature_id
                    )
                    if previous != feature_id:
                        raise ValueError(
                            "source peak cannot contribute to multiple product "
                            f"hypothesis rows: {candidate_id}"
                        )

        for name, rows in tables:
            write_row = self._write_rows.get(name)
            if write_row is None:
                continue
            for row in rows:
                if name == "matrix":
                    write_row(row)
                    if self._matrix_identities is not None:
                        assert groups[0].matrix is not None
                        self._matrix_identities.append(
                            (feature_id, groups[0].matrix.mz)
                        )
                else:
                    write_row((feature_id, *row[1:]))
        return feature_id


def write_alignment_tsvs(
    paths: Mapping[str, Path],
    completed: FinalizedUntargetBatchApplicationResult,
    *,
    consume_child: Callable[[UntargetChildOutputs, int], None] | None = None,
    matrix_identities: list[MatrixRowIdentity] | None = None,
) -> int:
    return write_projected_alignment_tsvs(
        paths,
        completed.canonical_samples,
        completed.child_outputs,
        consume_child=consume_child,
        matrix_identities=matrix_identities,
    )


def write_projected_alignment_tsvs(
    paths: Mapping[str, Path],
    canonical_samples: Sequence[SampleMetadata],
    child_outputs: Sequence[object],
    *,
    consume_child: Callable[[UntargetChildOutputs, int], None] | None = None,
    matrix_identities: list[MatrixRowIdentity] | None = None,
) -> int:
    """Number and write the requested TSVs from the batch's projected rows in
    one bounded pass, and return the group count.

    ``consume_child`` receives each child's outputs with the count of groups
    written before it, so its review records name the same ``feature_id``.
    """

    sample_order = untarget_sample_order(canonical_samples)
    with ExitStack() as stack:
        writer = _AlignmentRowWriter(
            stack, paths, _output_headers(sample_order), matrix_identities
        )
        for child in child_outputs:
            child = cast(UntargetChildOutputs, child)
            first_group = writer.group_count
            for group in child.groups:
                writer.write((group,), group.formatted_rows(sample_order).items())
            if consume_child is not None:
                consume_child(child, first_group)
    return writer.group_count


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
        if not math.isfinite(peak.rt_apex_min):
            raise ValueError("Matrix row lacks finite owner-backed RT observations")
    return sum(float(peak.rt_apex_min) for peak in peaks) / len(peaks)


def _selected_cell_source(
    decision: ProductionCellDecision,
    evidence: FinalGroupCellObservation,
    *,
    direct_candidate_ids: Mapping[tuple[str, str], str],
) -> str | None:
    """The Discovery candidate of a numeric cell read from a direct observation."""

    if decision.value is None or not evidence.own_cid_nl_support:
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


def cell_status(decision: ProductionCellDecision) -> str:
    """The cell's origin, or its no-write state when it reports no peak."""

    return decision.origin or decision.variant


def _cell_fields(
    sample_stem: str,
    cell: ProductionCellDecision,
    candidate_ids: Mapping[str, str],
) -> dict[str, object]:
    integration = cell.selected_integration
    quality = integration.signal_quality if integration is not None else None
    missing_area_reason = (
        primary_matrix_area_from_integration(integration).reason
        if cell.origin is not None
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
        "source_candidate_id": candidate_ids.get(sample_stem, ""),
    }


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
    to_reference_rt: Callable[[str, float], float | None],
    consume_row: Callable[[str, Sequence[tuple[str, int, int]]], None] | None = None,
    matrix_identities: list[MatrixRowIdentity] | None = None,
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
    headers = _output_headers(global_order)
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

    written_partners: set[tuple[str, str]] = set()
    ordered_sources = sorted(
        sources, key=lambda item: (item.batch_id != reference_batch, item.batch_id)
    )
    with ExitStack() as stack:
        writer = _AlignmentRowWriter(stack, paths, headers, matrix_identities)
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
                    mapped_batch = (
                        source.batch_id if source.batch_id != reference_batch else None
                    )
                    batch_rows = [
                        (orders[batch_id], part.formatted_rows(orders[batch_id]))
                        for batch_id, part in parts
                    ]
                    reference_rt = partial(
                        _reference_rt_text, to_reference_rt, mapped_batch
                    )
                    feature_id = writer.write(
                        [part for _, part in parts],
                        (
                            (
                                name,
                                _merge_batch_rows(
                                    name,
                                    [
                                        (order, rows.get(name, ()))
                                        for order, rows in batch_rows
                                    ],
                                    global_order,
                                    headers[name],
                                    reference_rt,
                                ),
                            )
                            for name in paths
                        ),
                    )
                    if consume_row is not None:
                        consume_row(feature_id, places)
    if written_partners != paired_elsewhere:
        raise ValueError(
            "paired features without a written reference row: "
            + ", ".join(sorted(key for _, key in paired_elsewhere - written_partners))
        )
    return writer.group_count


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
    if name == "matrix":
        values: dict[str, str] = {}
        for order, rows in present:
            values.update(zip(order, rows[0][2:], strict=True))
        mz, rt = present[0][1][0][:2]
        return (
            (mz, reference_rt(rt), *(values.get(stem, "") for stem in global_order)),
        )
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
