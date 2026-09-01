"""Public Matrix projection from a completed Untarget batch owner result."""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import TYPE_CHECKING

from xic_extractor.alignment.cell_observations import FinalGroupCellObservation
from xic_extractor.alignment.workflow import CompletedUntargetBatchWorkflowResult

if TYPE_CHECKING:
    from xic_extractor.alignment.cell_decision.owner import ProductionCellDecision


@dataclass(frozen=True, slots=True)
class FinalizedUntargetMatrixCell:
    sample_stem: str
    decision: ProductionCellDecision

    @property
    def value(self) -> float | None:
        return self.decision.value

    @property
    def variant(self) -> str:
        return self.decision.variant


@dataclass(frozen=True, slots=True)
class FinalizedUntargetMatrixRow:
    row_id: str
    destination: str
    mz: float
    rt_min: float
    cells: tuple[FinalizedUntargetMatrixCell, ...]
    physical_observation_ids: tuple[str, ...]
    lineage_edge_ids: tuple[str, ...]
    presentation_formula_version: str


@dataclass(frozen=True, slots=True)
class FinalizedUntargetMatrix:
    sample_stems: tuple[str, ...]
    rows: tuple[FinalizedUntargetMatrixRow, ...]


def project_completed_untarget_batch_matrix(
    completed: CompletedUntargetBatchWorkflowResult,
) -> FinalizedUntargetMatrix:
    """Project values and explicit no-write decisions without rerunning science."""

    if type(completed) is not CompletedUntargetBatchWorkflowResult:
        raise TypeError("Matrix projection requires a completed Untarget batch")
    application = completed.application_result
    sample_stems = tuple(
        sample.metadata.raw_stem for sample in application.canonical_samples
    )
    rows: list[FinalizedUntargetMatrixRow] = []
    for child in application.children:
        observations = {
            cell.group_hypothesis_id: cell
            for cell in child.scientific_core.cell_observations
        }
        for projected in child.completed_projection.rows:
            observation = observations[projected.row_key.group_hypothesis_id]
            mz_values, rt_values, physical_ids = _presentation_facts(observation)
            if not mz_values or not rt_values:
                raise ValueError("Matrix row lacks owner-backed Mz/RT observations")
            cells = tuple(
                FinalizedUntargetMatrixCell(
                    sample_stem=decision.subject.canonical_sample.metadata.raw_stem,
                    decision=decision,
                )
                for decision in projected.decisions
            )
            if tuple(cell.sample_stem for cell in cells) != sample_stems:
                raise ValueError("Matrix row crossed canonical sample order")
            rows.append(
                FinalizedUntargetMatrixRow(
                    row_id=projected.row_key.untarget_row_key_id,
                    destination=projected.route.destination,
                    mz=sum(mz_values) / len(mz_values),
                    rt_min=sum(rt_values) / len(rt_values),
                    cells=cells,
                    physical_observation_ids=physical_ids,
                    lineage_edge_ids=tuple(
                        sorted(
                            edge.lineage_edge_id
                            for edge in child.scientific_core.lineage
                            if edge.successor_group_hypothesis_id
                            == projected.row_key.group_hypothesis_id
                        )
                    ),
                    presentation_formula_version=(
                        "cross_sample_owner_arithmetic_mean_v1"
                    ),
                )
            )
    return FinalizedUntargetMatrix(sample_stems=sample_stems, rows=tuple(rows))


def _presentation_facts(observation: FinalGroupCellObservation) -> tuple[
    tuple[float, ...], tuple[float, ...], tuple[str, ...]
]:
    direct = observation.direct_observations
    mz_values = [item.content.mz for item in direct]
    rt_values = [item.content.integration.rt_apex_min for item in direct]
    physical_ids = [item.content.peak_observation_content_id for item in direct]
    for repaired in observation.repaired_observations:
        if not repaired.selected_for_output:
            continue
        mz = repaired.cell.backfill_seed_mz
        rt = repaired.cell.apex_rt
        if mz is not None and rt is not None:
            mz_values.append(mz)
            rt_values.append(rt)
            physical_ids.append(repaired.prepared_signal_id or repaired.work_unit_key)
    if (
        not mz_values
        or len(mz_values) != len(rt_values)
        or any(not math.isfinite(value) for value in (*mz_values, *rt_values))
    ):
        raise ValueError("Matrix row lacks finite owner-backed Mz/RT observations")
    return tuple(mz_values), tuple(rt_values), tuple(sorted(physical_ids))


__all__ = [
    "FinalizedUntargetMatrix",
    "FinalizedUntargetMatrixCell",
    "FinalizedUntargetMatrixRow",
    "project_completed_untarget_batch_matrix",
]
