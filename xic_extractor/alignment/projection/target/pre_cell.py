"""Target-owned row binding and cell-subject construction before cell decisions."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, TypeAlias, cast

from xic_extractor.alignment.cross_sample.peak_groups import (
    CrossSampleGroupIdentity,
    CrossSampleGroupMemberSubject,
    group_member_subject_from_peak_hypothesis,
)
from xic_extractor.alignment.result.scientific_alignment import (
    FinalizedScientificAlignment,
    ObservedSearchSubject,
    TerminalSearchObservation,
)
from xic_extractor.configuration.target_identity import (
    ResolvedTargetDefinition,
)
from xic_extractor.peak_detection.search_scope import (
    TargetSearchScope,
)
from xic_extractor.processing_identity import ProcessingRequestIdentity
from xic_extractor.sample_metadata import ResolvedCanonicalSampleIdentity


class TargetProjectionContractError(ValueError):
    """Raised when the Target pre-cell projection cannot close exactly."""


TargetRowBindingVariant: TypeAlias = Literal[
    "unbound",
    "bound",
    "unresolved_many",
]


@dataclass(frozen=True, slots=True)
class TargetRowBinding:
    """One fixed Target row with a closed zero/one/many group binding."""

    variant: TargetRowBindingVariant
    target: ResolvedTargetDefinition
    processing_request: ProcessingRequestIdentity
    eligible_groups: tuple[CrossSampleGroupIdentity, ...]

    @property
    def group(self) -> CrossSampleGroupIdentity | None:
        if self.variant == "bound":
            return self.eligible_groups[0]
        return None


@dataclass(frozen=True, slots=True)
class TargetCellSubject:
    """One Target × canonical-sample subject for the cell-decision owner."""

    target: ResolvedTargetDefinition
    processing_request: ProcessingRequestIdentity
    canonical_sample: ResolvedCanonicalSampleIdentity
    row_binding: TargetRowBinding
    terminal_subject: TerminalSearchObservation

    @property
    def sample(self) -> ResolvedCanonicalSampleIdentity:
        return self.canonical_sample


@dataclass(frozen=True, slots=True)
class TargetPreCellProjection:
    """Closed Target pre-cell handoff consumed by the cell-decision owner."""

    finalized_alignment: FinalizedScientificAlignment
    scope: TargetSearchScope
    row_binding: TargetRowBinding
    cell_subjects: tuple[TargetCellSubject, ...]

    @property
    def binding(self) -> TargetRowBinding:
        return self.row_binding

    @property
    def subjects(self) -> tuple[TargetCellSubject, ...]:
        return self.cell_subjects


def project_target_pre_cell(
    finalized_alignment: FinalizedScientificAlignment,
) -> TargetPreCellProjection:
    """Project one finalized Target alignment into a row and cell subjects."""

    scope = cast(TargetSearchScope, finalized_alignment.scope)
    samples = _canonical_samples(scope.samples)
    sample_by_id = {sample.identity.sample_key: sample for sample in samples}
    terminals = _terminals_by_sample(
        finalized_alignment,
        scope=scope,
        samples=sample_by_id,
    )
    eligible_groups = _eligible_groups(
        finalized_alignment,
        sample_by_id=sample_by_id,
        terminals=terminals,
    )
    row_binding = _mint_row_binding(
        target=scope.target,
        processing_request=scope.request,
        eligible_groups=eligible_groups,
    )
    subjects = tuple(
        TargetCellSubject(
            target=scope.target,
            processing_request=scope.request,
            canonical_sample=sample,
            row_binding=row_binding,
            terminal_subject=terminals[sample.identity.sample_key],
        )
        for sample in samples
    )
    return TargetPreCellProjection(
        finalized_alignment=finalized_alignment,
        scope=scope,
        row_binding=row_binding,
        cell_subjects=subjects,
    )


def _canonical_samples(
    samples: tuple[ResolvedCanonicalSampleIdentity, ...],
) -> tuple[ResolvedCanonicalSampleIdentity, ...]:
    return tuple(sorted(samples, key=lambda sample: sample.identity.sample_key))


def _terminals_by_sample(
    finalized_alignment: FinalizedScientificAlignment,
    *,
    scope: TargetSearchScope,
    samples: dict[str, ResolvedCanonicalSampleIdentity],
) -> dict[str, TerminalSearchObservation]:
    terminals = {}
    for terminal in finalized_alignment.terminal_subjects:
        sample_id = terminal.sample_key
        if sample_id in terminals:
            raise TargetProjectionContractError(
                "finalized terminal population contains duplicate canonical samples"
            )
        terminals[sample_id] = terminal
    if set(terminals) != set(samples):
        raise TargetProjectionContractError(
            "finalized terminal population is missing or adds a canonical sample"
        )
    return terminals


def _eligible_groups(
    finalized_alignment: FinalizedScientificAlignment,
    *,
    sample_by_id: dict[str, ResolvedCanonicalSampleIdentity],
    terminals: dict[str, TerminalSearchObservation],
) -> tuple[CrossSampleGroupIdentity, ...]:
    groups = finalized_alignment.final_population
    request_id = finalized_alignment.processing_request_id
    members_by_sample = {
        sample_id: _member_from_terminal(
            terminal=terminals[sample_id], sample=sample, request_id=request_id
        )
        for sample_id, sample in sample_by_id.items()
        if type(terminals[sample_id]) is ObservedSearchSubject
    }
    eligible: list[CrossSampleGroupIdentity] = []
    for group in groups:
        if group.disposition not in {"established", "provisional"}:
            continue
        if any(
            (
                any(
                    (
                        candidate == members_by_sample[sample_id]
                        for candidate in group.member_subjects
                    )
                )
                for sample_id in members_by_sample
            )
        ):
            eligible.append(group)
    return tuple(eligible)


def _member_from_terminal(
    *,
    terminal: TerminalSearchObservation,
    sample: ResolvedCanonicalSampleIdentity,
    request_id: str,
) -> CrossSampleGroupMemberSubject:
    if type(terminal) is not ObservedSearchSubject:
        raise TargetProjectionContractError(
            "only observed terminals can mint owner group members"
        )
    try:
        return group_member_subject_from_peak_hypothesis(
            sample=sample,
            hypothesis=terminal.hypothesis,
            processing_request_id=request_id,
        )
    except Exception as exc:
        raise TargetProjectionContractError(
            "finalized observed terminal cannot mint its owner group member"
        ) from exc


def _mint_row_binding(
    *,
    target: ResolvedTargetDefinition,
    processing_request: ProcessingRequestIdentity,
    eligible_groups: tuple[CrossSampleGroupIdentity, ...],
) -> TargetRowBinding:
    count = len(eligible_groups)
    sample_ids = tuple(
        member.sample_key
        for group in eligible_groups
        for member in group.member_subjects
    )
    variant: TargetRowBindingVariant
    if count == 0:
        variant = "unbound"
    elif len(sample_ids) == len(set(sample_ids)):
        variant = "bound"
    else:
        variant = "unresolved_many"
    return TargetRowBinding(
        variant=variant,
        target=target,
        processing_request=processing_request,
        eligible_groups=eligible_groups,
    )


__all__ = (
    "TargetCellSubject",
    "TargetPreCellProjection",
    "TargetProjectionContractError",
    "TargetRowBinding",
    "TargetRowBindingVariant",
    "project_target_pre_cell",
)
