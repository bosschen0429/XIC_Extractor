"""Target-owned row binding and cell-subject construction before cell decisions."""

from __future__ import annotations

from dataclasses import InitVar, dataclass, field
from typing import Literal, TypeAlias, cast

from xic_extractor.alignment.cross_sample_peak_groups import (
    CrossSampleGroupIdentity,
    CrossSampleGroupMemberSubject,
    group_member_subject_from_peak_hypothesis,
    validate_cross_sample_group_population,
)
from xic_extractor.alignment.cross_sample_reconciliation import (
    CrossSampleFinalReceipt,
)
from xic_extractor.alignment.result.scientific_alignment import (
    FinalizedScientificAlignment,
    ObservedSearchSubject,
    SearchedNoObservationSubject,
    TerminalSearchObservation,
)
from xic_extractor.configuration.target_identity import (
    ResolvedTargetDefinition,
    TargetDefinitionIdentity,
    TargetKey,
    TargetListSnapshot,
)
from xic_extractor.peak_detection.hypotheses import PeakHypothesis
from xic_extractor.peak_detection.search_scope import (
    CaptureCandidateBinding,
    CaptureMethodConstraints,
    EvidenceProviderBinding,
    SearchScopeContractError,
    TargetSearchScope,
    require_capture_binding,
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

_TARGET_PROJECTION_MINT_TOKEN = object()


@dataclass(frozen=True, slots=True)
class TargetRowBinding:
    """One fixed Target row with a closed zero/one/many group binding."""

    variant: TargetRowBindingVariant
    target: ResolvedTargetDefinition
    processing_request: ProcessingRequestIdentity
    eligible_groups: tuple[CrossSampleGroupIdentity, ...]
    _mint_token: InitVar[object] = field(kw_only=True, repr=False)

    def __post_init__(self, _mint_token: object) -> None:
        _require_projection_mint(_mint_token)
        _validate_row_binding(self)

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
    _mint_token: InitVar[object] = field(kw_only=True, repr=False)

    def __post_init__(self, _mint_token: object) -> None:
        _require_projection_mint(_mint_token)
        _validate_cell_subject(self)

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
    _mint_token: InitVar[object] = field(kw_only=True, repr=False)

    def __post_init__(self, _mint_token: object) -> None:
        _require_projection_mint(_mint_token)
        if type(self.finalized_alignment) is not FinalizedScientificAlignment:
            raise TargetProjectionContractError(
                "Target pre-cell projection requires an exact finalized alignment"
            )
        if type(self.scope) is not TargetSearchScope:
            raise TargetProjectionContractError(
                "Target pre-cell projection requires an exact TargetSearchScope"
            )
        if self.scope is not self.finalized_alignment.scope:
            raise TargetProjectionContractError(
                "Target pre-cell projection crossed its finalized alignment"
            )
        if type(self.row_binding) is not TargetRowBinding:
            raise TargetProjectionContractError(
                "Target pre-cell projection requires an exact TargetRowBinding"
            )
        if type(self.cell_subjects) is not tuple:
            raise TargetProjectionContractError(
                "Target cell subjects must be an immutable tuple"
            )
        if self.row_binding.target is not self.scope.target:
            raise TargetProjectionContractError(
                "Target row binding crossed the exact resolved Target"
            )
        if self.row_binding.processing_request is not self.scope.request:
            raise TargetProjectionContractError(
                "Target row binding crossed the exact processing request"
            )
        expected_samples = _canonical_samples(self.scope.samples)
        actual_samples = tuple(
            subject.canonical_sample for subject in self.cell_subjects
        )
        if actual_samples != expected_samples:
            raise TargetProjectionContractError(
                "Target cell subjects must cover the canonical sample universe "
                "in canonical identity order"
            )
        for subject in self.cell_subjects:
            if type(subject) is not TargetCellSubject:
                raise TargetProjectionContractError(
                    "Target cell population contains a non-owner subject"
                )
            if subject.row_binding is not self.row_binding:
                raise TargetProjectionContractError(
                    "Target cell subject does not retain the exact row binding"
                )
            if subject.target is not self.scope.target:
                raise TargetProjectionContractError(
                    "Target cell subject crossed the exact resolved Target"
                )
            if subject.processing_request is not self.scope.request:
                raise TargetProjectionContractError(
                    "Target cell subject crossed the exact processing request"
                )

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

    _validate_finalized_input(finalized_alignment)
    scope = cast(TargetSearchScope, finalized_alignment.scope)
    _revalidate_scope(scope)
    samples = _canonical_samples(scope.samples)
    sample_by_id = {
        sample.identity.sample_identity_id: sample for sample in samples
    }
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
            terminal_subject=terminals[sample.identity.sample_identity_id],
            _mint_token=_TARGET_PROJECTION_MINT_TOKEN,
        )
        for sample in samples
    )
    return TargetPreCellProjection(
        finalized_alignment=finalized_alignment,
        scope=scope,
        row_binding=row_binding,
        cell_subjects=subjects,
        _mint_token=_TARGET_PROJECTION_MINT_TOKEN,
    )


def _validate_finalized_input(
    finalized_alignment: object,
) -> None:
    if type(finalized_alignment) is not FinalizedScientificAlignment:
        raise TargetProjectionContractError(
            "Target pre-cell projection requires an exact finalized alignment"
        )
    scope = finalized_alignment.scope
    if type(scope) is not TargetSearchScope:
        raise TargetProjectionContractError(
            "Target pre-cell projection requires an exact TargetSearchScope"
        )
    if type(scope.request) is not ProcessingRequestIdentity:
        raise TargetProjectionContractError(
            "TargetSearchScope requires an exact processing request"
        )
    if type(scope.target_snapshot) is not TargetListSnapshot:
        raise TargetProjectionContractError(
            "TargetSearchScope requires an exact Target snapshot"
        )
    if type(scope.target) is not ResolvedTargetDefinition:
        raise TargetProjectionContractError(
            "TargetSearchScope requires an exact resolved Target"
        )
    if finalized_alignment.processing_request is not scope.request:
        raise TargetProjectionContractError(
            "finalized Target alignment crossed the exact processing request"
        )
    expected_samples = _canonical_samples(scope.samples)
    if finalized_alignment.canonical_samples != expected_samples:
        raise TargetProjectionContractError(
            "finalized Target alignment crossed the canonical sample universe"
        )
    if type(finalized_alignment.terminal_subjects) is not tuple:
        raise TargetProjectionContractError(
            "finalized terminal subjects must be an immutable tuple"
        )
    if (
        type(finalized_alignment.final_cross_sample_receipt)
        is not CrossSampleFinalReceipt
    ):
        raise TargetProjectionContractError(
            "finalized Target alignment requires an owner final receipt"
        )
    if (
        finalized_alignment.final_cross_sample_receipt.processing_request_id
        != finalized_alignment.processing_request_id
    ):
        raise TargetProjectionContractError(
            "finalized Target alignment receipt crossed the processing request"
        )


def _revalidate_scope(scope: TargetSearchScope) -> None:
    try:
        request = scope.request
        ProcessingRequestIdentity(
            processing_request_id=request.processing_request_id,
            acquisition_sources=request.acquisition_sources,
            acquisition_bindings=request.acquisition_bindings,
            scientific_input_schema_version=request.scientific_input_schema_version,
            processing_algorithm_version=request.processing_algorithm_version,
            processing_contract_version=request.processing_contract_version,
            normalized_scientific_inputs_json=(
                request.normalized_scientific_inputs_json
            ),
        )
        target = scope.target
        target_key = target.definition.target_key
        TargetKey(
            target_key_id=target_key.target_key_id,
            identity_namespace=target_key.identity_namespace,
            target_label=target_key.target_label,
        )
        TargetDefinitionIdentity(
            target_definition_id=target.definition.target_definition_id,
            target_key=target.definition.target_key,
            target=target.definition.target,
        )
        snapshot = scope.target_snapshot
        TargetListSnapshot(
            snapshot_id=snapshot.snapshot_id,
            content_sha256=snapshot.content_sha256,
            identity_namespace=snapshot.identity_namespace,
            definitions=snapshot.definitions,
        )
        ResolvedTargetDefinition(
            definition=target.definition,
            snapshot=target.snapshot,
            lookup_label=target.lookup_label,
        )
        CaptureMethodConstraints(
            boundary_policy=scope.method_constraints.boundary_policy,
            peak_config=scope.method_constraints.peak_config,
        )
        for provider in scope.evidence_providers:
            EvidenceProviderBinding(
                provider_id=provider.provider_id,
                method_subject_id=provider.method_subject_id,
            )
        for binding in scope.capture_bindings:
            CaptureCandidateBinding(
                candidate_id=binding.candidate_id,
                sample_identity_id=binding.sample_identity_id,
                sample_name=binding.sample_name,
                acquisition_source_id=binding.acquisition_source_id,
                query_mz=binding.query_mz,
                query_rt_min=binding.query_rt_min,
                query_rt_max=binding.query_rt_max,
                query_ppm_tol=binding.query_ppm_tol,
                preferred_rt=binding.preferred_rt,
            )
        TargetSearchScope(
            schema_version=scope.schema_version,
            request=scope.request,
            samples=scope.samples,
            method_constraints=scope.method_constraints,
            evidence_providers=scope.evidence_providers,
            declared_candidate_ids=scope.declared_candidate_ids,
            capture_bindings=scope.capture_bindings,
            target_snapshot=scope.target_snapshot,
            target=scope.target,
        )
    except (TypeError, ValueError) as exc:
        raise TargetProjectionContractError(
            "Target scope violates its owner closure"
        ) from exc


def _canonical_samples(
    samples: tuple[ResolvedCanonicalSampleIdentity, ...],
) -> tuple[ResolvedCanonicalSampleIdentity, ...]:
    if type(samples) is not tuple:
        raise TargetProjectionContractError(
            "canonical samples must be an immutable tuple"
        )
    if any(type(sample) is not ResolvedCanonicalSampleIdentity for sample in samples):
        raise TargetProjectionContractError(
            "canonical samples must be exact owner-resolved identities"
        )
    try:
        for sample in samples:
            ResolvedCanonicalSampleIdentity(
                identity=sample.identity,
                snapshot=sample.snapshot,
                metadata=sample.metadata,
                lookup_token=sample.lookup_token,
            )
    except (TypeError, ValueError) as exc:
        raise TargetProjectionContractError(
            "canonical sample violates its owner resolution"
        ) from exc
    ordered = tuple(
        sorted(samples, key=lambda sample: sample.identity.sample_identity_id)
    )
    ids = tuple(sample.identity.sample_identity_id for sample in samples)
    if len(ids) != len(set(ids)):
        raise TargetProjectionContractError(
            "canonical sample population contains duplicate identities"
        )
    return ordered


def _terminals_by_sample(
    finalized_alignment: FinalizedScientificAlignment,
    *,
    scope: TargetSearchScope,
    samples: dict[str, ResolvedCanonicalSampleIdentity],
) -> dict[str, TerminalSearchObservation]:
    terminals: dict[str, TerminalSearchObservation] = {}
    for terminal in finalized_alignment.terminal_subjects:
        if type(terminal) not in {
            ObservedSearchSubject,
            SearchedNoObservationSubject,
        }:
            raise TargetProjectionContractError(
                "finalized terminal population contains an unsupported subject"
            )
        _validate_terminal_owner_fields(terminal)
        sample_id = terminal.canonical_sample_identity_id
        if sample_id not in samples:
            raise TargetProjectionContractError(
                "finalized terminal population contains an extra canonical sample"
            )
        try:
            binding = require_capture_binding(scope, terminal.candidate_id)
        except SearchScopeContractError as exc:
            raise TargetProjectionContractError(
                "finalized terminal candidate lacks its exact scope capture binding"
            ) from exc
        if binding.sample_identity_id != sample_id:
            raise TargetProjectionContractError(
                "finalized terminal candidate crossed its capture-binding sample"
            )
        if sample_id in terminals:
            raise TargetProjectionContractError(
                "finalized terminal population contains duplicate canonical samples"
            )
        terminals[sample_id] = terminal
    if set(terminals) != set(samples):
        raise TargetProjectionContractError(
            "finalized terminal population is missing a canonical sample"
        )
    return terminals


def _validate_terminal_owner_fields(terminal: TerminalSearchObservation) -> None:
    values = (
        terminal.candidate_id,
        terminal.canonical_sample_identity_id,
        terminal.trace_group_id,
    )
    if any(
        not isinstance(value, str) or not value or value != value.strip()
        for value in values
    ):
        raise TargetProjectionContractError(
            "finalized terminal contains incomplete owner fields"
        )
    if type(terminal) is ObservedSearchSubject:
        if type(terminal.hypothesis) is not PeakHypothesis:
            raise TargetProjectionContractError(
                "finalized observed terminal requires an exact PeakHypothesis"
            )
        if terminal.trace_group_id != terminal.hypothesis.trace_group_id:
            raise TargetProjectionContractError(
                "finalized observed terminal crossed its trace-group hypothesis"
            )
    elif (
        type(terminal) is SearchedNoObservationSubject
        and terminal.detection_status != "not_detected"
    ):
        raise TargetProjectionContractError(
            "finalized negative-search terminal is not closed"
        )


def _eligible_groups(
    finalized_alignment: FinalizedScientificAlignment,
    *,
    sample_by_id: dict[str, ResolvedCanonicalSampleIdentity],
    terminals: dict[str, TerminalSearchObservation],
) -> tuple[CrossSampleGroupIdentity, ...]:
    groups = finalized_alignment.final_population
    if type(groups) is not tuple:
        raise TargetProjectionContractError(
            "final CrossSample population must be an immutable tuple"
        )
    for group in groups:
        if type(group) is not CrossSampleGroupIdentity:
            raise TargetProjectionContractError(
                "final CrossSample population contains a non-owner group"
            )
    ordered_groups = tuple(
        sorted(groups, key=lambda group: group.group_hypothesis_id)
    )
    if ordered_groups != groups:
        raise TargetProjectionContractError(
            "final CrossSample population is not canonical identity ordered"
        )
    request_id = finalized_alignment.processing_request_id
    for group in groups:
        if group.processing_request_id != request_id:
            raise TargetProjectionContractError(
                "final CrossSample population crossed the processing request"
            )
        for member in group.member_subjects:
            if type(member) is not CrossSampleGroupMemberSubject:
                raise TargetProjectionContractError(
                    "final CrossSample group contains a non-owner member"
                )
            if member.processing_request_id != request_id:
                raise TargetProjectionContractError(
                    "final CrossSample member crossed the processing request"
                )
            if member.canonical_sample_identity_id not in sample_by_id:
                raise TargetProjectionContractError(
                    "final CrossSample member crossed the canonical sample universe"
                )
        _revalidate_owner_group(group)
    try:
        validate_cross_sample_group_population(groups)
    except Exception as exc:
        raise TargetProjectionContractError(
            f"final CrossSample population is not closed: {exc}"
        ) from exc
    members_by_sample = {
        sample_id: _member_from_terminal(
            terminal=terminals[sample_id],
            sample=sample,
            request_id=request_id,
        )
        for sample_id, sample in sample_by_id.items()
        if type(terminals[sample_id]) is ObservedSearchSubject
    }
    eligible: list[CrossSampleGroupIdentity] = []
    for group in groups:
        if group.disposition not in {"established", "provisional"}:
            continue
        if any(
            any(
                candidate == members_by_sample[sample_id]
                for candidate in group.member_subjects
            )
            for sample_id in members_by_sample
        ):
            eligible.append(group)
    return tuple(eligible)


def _revalidate_owner_group(group: CrossSampleGroupIdentity) -> None:
    try:
        members = tuple(
            CrossSampleGroupMemberSubject(
                member_subject_id=member.member_subject_id,
                processing_request_id=member.processing_request_id,
                canonical_sample_identity_id=member.canonical_sample_identity_id,
                subject_kind=member.subject_kind,
                subject_id=member.subject_id,
                physical_observation_ids=member.physical_observation_ids,
                basis_bytes=member.basis_bytes,
            )
            for member in group.member_subjects
        )
        CrossSampleGroupIdentity(
            group_hypothesis_id=group.group_hypothesis_id,
            processing_request_id=group.processing_request_id,
            member_subjects=members,
            basis_bytes=group.basis_bytes,
            disposition=group.disposition,
        )
    except Exception as exc:
        raise TargetProjectionContractError(
            "final CrossSample group violates its owner identity invariants"
        ) from exc


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
    variant: TargetRowBindingVariant
    if count == 0:
        variant = "unbound"
    elif count == 1:
        variant = "bound"
    else:
        variant = "unresolved_many"
    return TargetRowBinding(
        variant=variant,
        target=target,
        processing_request=processing_request,
        eligible_groups=eligible_groups,
        _mint_token=_TARGET_PROJECTION_MINT_TOKEN,
    )


def _validate_row_binding(binding: TargetRowBinding) -> None:
    if type(binding.target) is not ResolvedTargetDefinition:
        raise TargetProjectionContractError(
            "Target row binding requires an owner-resolved Target"
        )
    if type(binding.processing_request) is not ProcessingRequestIdentity:
        raise TargetProjectionContractError(
            "Target row binding requires an owner processing request"
        )
    if binding.variant not in {"unbound", "bound", "unresolved_many"}:
        raise TargetProjectionContractError(
            "Target row binding uses an unsupported closed variant"
        )
    if type(binding.eligible_groups) is not tuple:
        raise TargetProjectionContractError(
            "Target row binding groups must be an immutable tuple"
        )
    if any(
        type(group) is not CrossSampleGroupIdentity
        for group in binding.eligible_groups
    ):
        raise TargetProjectionContractError(
            "Target row binding groups must be exact owner identities"
        )
    ordered = tuple(
        sorted(binding.eligible_groups, key=lambda group: group.group_hypothesis_id)
    )
    if ordered != binding.eligible_groups:
        raise TargetProjectionContractError(
            "Target row binding groups are not canonical identity ordered"
        )
    if any(
        group.processing_request_id != binding.processing_request.processing_request_id
        or group.disposition not in {"established", "provisional"}
        for group in binding.eligible_groups
    ):
        raise TargetProjectionContractError(
            "Target row binding contains an ineligible or crossed group"
        )
    count = len(binding.eligible_groups)
    expected_variant = (
        "unbound" if count == 0 else "bound" if count == 1 else "unresolved_many"
    )
    if binding.variant != expected_variant:
        raise TargetProjectionContractError(
            "Target row binding variant does not match its eligible group cardinality"
        )


def _validate_cell_subject(subject: TargetCellSubject) -> None:
    if type(subject.target) is not ResolvedTargetDefinition:
        raise TargetProjectionContractError(
            "Target cell subject requires an owner-resolved Target"
        )
    if type(subject.processing_request) is not ProcessingRequestIdentity:
        raise TargetProjectionContractError(
            "Target cell subject requires an owner processing request"
        )
    if type(subject.canonical_sample) is not ResolvedCanonicalSampleIdentity:
        raise TargetProjectionContractError(
            "Target cell subject requires an owner-resolved canonical sample"
        )
    if type(subject.row_binding) is not TargetRowBinding:
        raise TargetProjectionContractError(
            "Target cell subject requires an exact TargetRowBinding"
        )
    if type(subject.terminal_subject) not in {
        ObservedSearchSubject,
        SearchedNoObservationSubject,
    }:
        raise TargetProjectionContractError(
            "Target cell subject requires an exact terminal search subject"
        )
    if subject.processing_request is not subject.row_binding.processing_request:
        raise TargetProjectionContractError(
            "Target cell subject crossed the exact processing request"
        )
    if subject.target is not subject.row_binding.target:
        raise TargetProjectionContractError(
            "Target cell subject crossed the exact resolved Target"
        )
    if (
        subject.terminal_subject.canonical_sample_identity_id
        != subject.canonical_sample.identity.sample_identity_id
    ):
        raise TargetProjectionContractError(
            "Target cell subject crossed its canonical sample terminal"
        )
def _require_projection_mint(value: object) -> None:
    if value is not _TARGET_PROJECTION_MINT_TOKEN:
        raise TargetProjectionContractError(
            "Target pre-cell values must be minted by the Target projection owner"
        )


__all__ = (
    "TargetCellSubject",
    "TargetPreCellProjection",
    "TargetProjectionContractError",
    "TargetRowBinding",
    "TargetRowBindingVariant",
    "project_target_pre_cell",
)
