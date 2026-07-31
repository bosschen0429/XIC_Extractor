"""Evidence-gated Backfill for the diagnostic Targeted hypothesis path."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Literal

from xic_extractor.alignment.config import AlignmentConfig
from xic_extractor.alignment.cross_sample_peak_groups import (
    CrossSamplePeakGroupHypothesis,
)
from xic_extractor.alignment.matrix import AlignedCell
from xic_extractor.alignment.owner_backfill import (
    OwnerBackfillSource,
    build_owner_backfill_result,
)
from xic_extractor.alignment.targeted_hypothesis_alignment import (
    TargetedHypothesisAlignmentInput,
)
from xic_extractor.config import ExtractionConfig
from xic_extractor.peak_detection.hypotheses import IntegrationResult
from xic_extractor.peak_detection.selection_decision import (
    resolve_diagnostic_peak_hypothesis_selection,
)

TargetedHypothesisCellStatus = Literal[
    "observed",
    "backfilled",
    "true_missing",
    "unchecked",
    "unassessable",
    "ambiguous",
    "failed",
]


class TargetedHypothesisBackfillError(ValueError):
    """Raised when Backfill eligibility or Alignment lineage is violated."""


@dataclass(frozen=True, slots=True)
class TargetedHypothesisCell:
    canonical_sample_identity_id: str
    sample_stem: str
    target_definition_id: str
    selection_evidence_artifact_id: str
    status: TargetedHypothesisCellStatus
    group_hypothesis_id: str | None
    alignment_owner_ids: tuple[str, ...]
    source_hypothesis_id: str | None
    selected_integration: IntegrationResult | None
    backfill_cell: AlignedCell | None
    reason: str


def build_targeted_hypothesis_cells(
    inputs: tuple[TargetedHypothesisAlignmentInput, ...],
    groups: tuple[CrossSamplePeakGroupHypothesis, ...],
    *,
    backfill_sources: Mapping[str, OwnerBackfillSource],
    alignment_config: AlignmentConfig,
    peak_config: ExtractionConfig,
) -> tuple[TargetedHypothesisCell, ...]:
    """Project observed cells and query only eligible Alignment-backed gaps."""

    if not isinstance(inputs, tuple) or not isinstance(groups, tuple):
        raise TargetedHypothesisBackfillError(
            "inputs and groups must be immutable tuples"
        )
    by_stem = {item.sample.metadata.raw_stem: item for item in inputs}
    if len(by_stem) != len(inputs):
        raise TargetedHypothesisBackfillError(
            "Targeted Backfill inputs contain duplicate sample stems"
        )
    unknown_sources = set(backfill_sources).difference(by_stem)
    if unknown_sources:
        raise TargetedHypothesisBackfillError(
            "Backfill source does not belong to the Targeted input set"
        )
    for sample_stem in backfill_sources:
        if by_stem[sample_stem].selection.status != "no_candidate":
            raise TargetedHypothesisBackfillError(
                "Backfill source is allowed only for a no-candidate Discovery cell"
            )

    group_by_owner: dict[str, CrossSamplePeakGroupHypothesis] = {}
    for alignment_group in groups:
        for owner_id in alignment_group.owner_ids:
            if owner_id in group_by_owner:
                raise TargetedHypothesisBackfillError(
                    "selected hypothesis appears in multiple Alignment groups"
                )
            group_by_owner[owner_id] = alignment_group

    cells: list[TargetedHypothesisCell] = []
    for item in sorted(inputs, key=lambda value: value.sample.metadata.raw_stem):
        selected = resolve_diagnostic_peak_hypothesis_selection(
            item.selection,
            item.candidate_set,
        )
        if selected is not None:
            group = group_by_owner.get(selected.hypothesis_id)
            if group is None:
                raise TargetedHypothesisBackfillError(
                    "Alignment lost the selected hypothesis before Backfill"
                )
            integration = selected.peak_observation_refs[0].content.integration
            cells.append(
                _cell(
                    item,
                    status="observed",
                    group=group,
                    source_hypothesis_id=selected.hypothesis_id,
                    selected_integration=integration,
                    reason="unique Discovery hypothesis preserved by Alignment",
                )
            )
            continue
        if item.selection.status == "ambiguous":
            cells.append(
                _cell(
                    item,
                    status="ambiguous",
                    reason="multiple Discovery hypotheses cannot authorize Backfill",
                )
            )
            continue
        if item.selection.status == "failed":
            cells.append(
                _cell(
                    item,
                    status="failed",
                    reason=item.selection.reasons[0],
                )
            )
            continue
        cells.append(
            _backfill_no_candidate(
                item,
                groups=groups,
                source=backfill_sources.get(item.sample.metadata.raw_stem),
                alignment_config=alignment_config,
                peak_config=peak_config,
            )
        )
    return tuple(cells)


def _backfill_no_candidate(
    item: TargetedHypothesisAlignmentInput,
    *,
    groups: tuple[CrossSamplePeakGroupHypothesis, ...],
    source: OwnerBackfillSource | None,
    alignment_config: AlignmentConfig,
    peak_config: ExtractionConfig,
) -> TargetedHypothesisCell:
    if len(groups) != 1:
        return _cell(
            item,
            status="ambiguous",
            reason="Alignment correspondence is not unique for Backfill",
        )
    group = groups[0]
    if not group.owner_ids:
        return _cell(
            item,
            status="unchecked",
            reason="Alignment has no discovered hypothesis to authorize Backfill",
        )
    if source is None:
        return _cell(
            item,
            status="unchecked",
            group=group,
            reason="no local evidence source was supplied for the missing cell",
        )
    result = build_owner_backfill_result(
        (group,),
        sample_order=(item.sample.metadata.raw_stem,),
        raw_sources={item.sample.metadata.raw_stem: source},
        alignment_config=alignment_config,
        peak_config=peak_config,
    )
    if len(result.cells) != 1:
        raise TargetedHypothesisBackfillError(
            "existing Backfill owner returned a non-canonical cell count"
        )
    backfill_cell = result.cells[0]
    if backfill_cell.status == "unchecked":
        if backfill_cell.trace_quality == "owner_backfill_not_detected":
            return _cell(
                item,
                status="true_missing",
                group=group,
                backfill_cell=backfill_cell,
                reason=backfill_cell.reason,
            )
        if backfill_cell.trace_quality == "owner_backfill_unassessable":
            return _cell(
                item,
                status="unassessable",
                group=group,
                backfill_cell=backfill_cell,
                reason=backfill_cell.reason,
            )
        return _cell(
            item,
            status="unchecked",
            group=group,
            backfill_cell=backfill_cell,
            reason=backfill_cell.reason,
        )
    if backfill_cell.status in {"ambiguous_ms1_owner", "duplicate_assigned"}:
        return _cell(
            item,
            status="ambiguous",
            group=group,
            backfill_cell=backfill_cell,
            reason=backfill_cell.reason,
        )
    if backfill_cell.status != "rescued":
        raise TargetedHypothesisBackfillError(
            "existing Backfill owner returned an unsupported terminal status: "
            f"{backfill_cell.status}"
        )
    if backfill_cell.selected_integration is None:
        raise TargetedHypothesisBackfillError(
            "rescued Backfill cell is missing owner-selected integration"
        )
    return _cell(
        item,
        status="backfilled",
        group=group,
        selected_integration=backfill_cell.selected_integration,
        backfill_cell=backfill_cell,
        reason="Alignment correspondence and local evidence authorized Backfill",
    )


def _cell(
    item: TargetedHypothesisAlignmentInput,
    *,
    status: TargetedHypothesisCellStatus,
    reason: str,
    group: CrossSamplePeakGroupHypothesis | None = None,
    source_hypothesis_id: str | None = None,
    selected_integration: IntegrationResult | None = None,
    backfill_cell: AlignedCell | None = None,
) -> TargetedHypothesisCell:
    return TargetedHypothesisCell(
        canonical_sample_identity_id=item.sample.identity.sample_identity_id,
        sample_stem=item.sample.metadata.raw_stem,
        target_definition_id=item.target.definition.target_definition_id,
        selection_evidence_artifact_id=item.selection.artifact.evidence_artifact_id,
        status=status,
        group_hypothesis_id=None if group is None else group.group_hypothesis_id,
        alignment_owner_ids=() if group is None else group.owner_ids,
        source_hypothesis_id=source_hypothesis_id,
        selected_integration=selected_integration,
        backfill_cell=backfill_cell,
        reason=reason,
    )
