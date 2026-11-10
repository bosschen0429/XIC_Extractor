"""Lay completed Feature x Sample answers out by group and sample."""

from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, TypeAlias

from xic_extractor.alignment.cross_sample.peak_groups import (
    CrossSamplePeakGroupHypothesis,
)
from xic_extractor.alignment.projection.untarget.identity import (
    GroupCellSubject,
    UntargetProjectionContractError,
)
from xic_extractor.alignment.result.scientific_alignment import (
    FinalizedScientificAlignment,
)
from xic_extractor.peak_detection.integration_result import IntegrationResult

if TYPE_CHECKING:
    from xic_extractor.alignment.cell_decision.owner import (
        CellDecisionPayload,
        ProductionCellDecision,
    )

GroupCells: TypeAlias = tuple[
    CrossSamplePeakGroupHypothesis, tuple["ProductionCellDecision", ...]
]


def project_untarget_matrix(
    finalized_alignment: FinalizedScientificAlignment,
    decisions: Mapping[
        tuple[CrossSamplePeakGroupHypothesis, str],
        tuple[CellDecisionPayload, IntegrationResult | None],
    ],
) -> tuple[GroupCells, ...]:
    """Each final group, ordered by key, with one cell per canonical sample."""

    from xic_extractor.alignment.cell_decision.owner import ProductionCellDecision

    observations = finalized_alignment.cell_observations
    if set(decisions) != {item.key for item in observations}:
        raise UntargetProjectionContractError(
            "Group decision population is missing, extra, or crossed"
        )
    by_position = {
        (item.group_hypothesis_id, item.sample_key): item for item in observations
    }
    groups = sorted(
        finalized_alignment.final_population,
        key=lambda group: group.group_hypothesis_id,
    )
    rows = []
    for group in groups:
        members = {member.sample_key: member for member in group.member_subjects}
        projected = []
        for sample in finalized_alignment.canonical_samples:
            sample_id = sample.sample_name
            observation = by_position[group.group_hypothesis_id, sample_id]
            payload, integration = decisions[observation.key]
            projected.append(
                ProductionCellDecision(
                    subject=GroupCellSubject(
                        group,
                        sample,
                        members.get(sample_id),
                    ),
                    evidence=observation,
                    payload=payload,
                    selected_integration=integration,
                )
            )
        rows.append((group, tuple(projected)))
    return tuple(rows)
