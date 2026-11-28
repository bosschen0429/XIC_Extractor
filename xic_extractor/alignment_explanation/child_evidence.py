"""A finalized child's report facts before batch feature IDs are assigned."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .writer import ExplanationReportWriter


@dataclass(frozen=True, slots=True)
class LocatedPeak:
    sample_key: str
    key: str
    coordinates: tuple[float, float, float]
    signal_digest: str


@dataclass(frozen=True, slots=True)
class CandidateTerminal:
    candidate_id: str
    sample_key: str
    hypothesis_id: str
    physical_peak_key: str


@dataclass(frozen=True, slots=True)
class ChildGroup:
    group_id: str
    feature_offset: int | None
    disposition: str
    members: tuple[dict[str, Any], ...]
    center: tuple[float, float] | None


@dataclass(frozen=True, slots=True)
class ChildRow:
    group_id: str
    destination: str
    cells: tuple[dict[str, Any], ...]
    curves: tuple[tuple[str, bytes], ...]
    positions: tuple[tuple[str, float], ...]


@dataclass(frozen=True, slots=True)
class ChildExplanation:
    peaks: tuple[LocatedPeak, ...]
    terminals: tuple[CandidateTerminal, ...]
    groups: tuple[ChildGroup, ...]
    rows: tuple[ChildRow, ...]

    def write_to(
        self,
        writer: ExplanationReportWriter,
        *,
        locate: Callable[[LocatedPeak], None],
        label: Callable[[int], str],
        merge_into: Mapping[int, str],
    ) -> dict[int, str]:
        """Publish in first-appearance order, extending a paired group in place."""
        for peak in self.peaks:
            locate(peak)
        for terminal in self.terminals:
            writer.add_terminal(
                terminal.candidate_id,
                terminal.sample_key,
                hypothesis_id=terminal.hypothesis_id,
                physical_peak_key=terminal.physical_peak_key,
            )
        merged: dict[str, str] = {}
        final_groups: dict[int, str] = {}
        for group in self.groups:
            offset = group.feature_offset
            if offset is not None and offset in merge_into:
                merged[group.group_id] = merge_into[offset]
                writer.extend_final_group(merge_into[offset], members=group.members)
            else:
                writer.add_final_group(
                    group.group_id,
                    feature_id=None if offset is None else label(offset),
                    disposition=group.disposition,
                    members=group.members,
                    center_mz=group.center[0] if group.center else None,
                    ppm=group.center[1] if group.center else None,
                )
            if offset is not None:
                final_groups[offset] = merged.get(group.group_id, group.group_id)
        for row in self.rows:
            group_id = merged.get(row.group_id, row.group_id)
            if row.group_id not in merged:
                writer.set_row_destination(group_id, row.destination)
            writer.add_cells(group_id, row.cells)
            for sample, curve in row.curves:
                writer.set_cell_curve(group_id, sample, curve)
            for sample, rt in row.positions:
                writer.set_cell_position(group_id, sample, rt)
        return final_groups
