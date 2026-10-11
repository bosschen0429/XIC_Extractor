"""Minimal sample-by-group observation closure for diagnostic evidence flow."""

from __future__ import annotations

import hashlib
import re
from collections.abc import Sequence
from dataclasses import InitVar, dataclass, field
from typing import Literal, TypeAlias

from xic_extractor.alignment.backfill.evaluation_coverage import (
    BackfillAttemptTerminal,
    BackfillPlanningPosition,
    validate_backfill_conservative_no_request_position,
    validate_backfill_retained_candidate_position,
)
from xic_extractor.alignment.cell_group_context import (
    CidNlCellGroupContext,
    CidNlCellGroupSupport,
)
from xic_extractor.alignment.cell_observation_records import (
    CellObservationContent,
    CellObservationContractError,
    CellObservationRef,
    OutsideGroupCellRelation,
    _direct_member_integration,
    canonical_cell_observation_bytes,
    mint_cell_observation_ref,
)
from xic_extractor.alignment.cross_sample.association import (
    CrossSampleAssociationLineageHandoff,
    CrossSampleCoveragePositionAssociation,
    validate_cross_sample_association_lineage_handoff,
)
from xic_extractor.alignment.cross_sample_peak_groups import (
    CrossSampleGroupIdentity,
    CrossSampleGroupMemberSubject,
)
from xic_extractor.evidence_artifacts import (
    _mint_artifact_semantic_contract_ref,
)
from xic_extractor.peak_detection.cid_nl_method_assessment import (
    CidNlMethodAssessmentRef,
    is_ms1_only_cid_nl_backfill_assessment,
)
from xic_extractor.peak_detection.hypotheses import PeakHypothesis
from xic_extractor.peak_detection.integration_result import IntegrationResult
from xic_extractor.peak_detection.observations import PeakObservationRef
from xic_extractor.processing_identity import (
    mint_typed_identity,
)
from xic_extractor.sample_metadata import ResolvedCanonicalSampleIdentity

SCHEMA_VERSION = "cell_observation"
ARTIFACT_SEMANTIC_ROLE = "diagnostic_cell_observation"
VALIDATION_STATUS = "diagnostic_only"
MEDIA_TYPE = "application/json"
SERIALIZATION_FORMAT = "canonical-json-utf8-lf"

_ARTIFACT_CONTENT_ID_PREFIX = "xic.artifact-content:"
_EVIDENCE_ARTIFACT_ID_PREFIX = "xic.evidence-artifact:"
_PEAK_CONTENT_ID_PREFIX = "xic.peak-observation-content:"
_PEAK_HYPOTHESIS_ID_PREFIX = "xic.peak-hypothesis:"
_SAMPLE_ID_PREFIX = "xic.sample:"
_METHOD_SUBJECT_ID_PREFIX = "xic.cid-nl-method-subject:"
_TARGET_KEY_ID_PREFIX = "xic.target-key:"
_TARGET_DEFINITION_ID_PREFIX = "xic.target-definition:"
_FINAL_CELL_OBSERVATION_ID_PREFIX = "xic.final-cell-observation:"
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_FINAL_CELL_MINT_TOKEN = object()
_ARTIFACT_SEMANTIC_CONTRACT = _mint_artifact_semantic_contract_ref(
    owner_namespace="xic_extractor.alignment.cell_observations",
    semantic_role=ARTIFACT_SEMANTIC_ROLE,
    schema_version=SCHEMA_VERSION,
    validation_status=VALIDATION_STATUS,
    media_type=MEDIA_TYPE,
    serialization_format=SERIALIZATION_FORMAT,
)

FinalCellEvidenceState: TypeAlias = Literal[
    "direct_observation",
    "ms1_only_backfill_observation",
    "unresolved",
    "rejected_observation",
    "searched_no_observation",
    "unavailable",
    "not_searched",
]
TerminalObservationVariant: TypeAlias = Literal[
    "observed",
    "searched_no_observation",
]


# ---------------------------------------------------------------------------
# Exact CrossSample/Backfill handoff closure
# ---------------------------------------------------------------------------
#
# A final cell is a correlation boundary. Its inputs are the exact CrossSample
# handoff and owner-minted predecessor/completed Backfill cell evidence, not
# caller-assembled group, terminal, provider, or repair arguments.


FinalCellEvidenceAggregate: TypeAlias = FinalCellEvidenceState | Literal["mixed"]


@dataclass(frozen=True, slots=True)
class FinalGroupCellObservation:
    """One immutable final ``CrossSampleGroupIdentity × sample`` closure.

    ``position_associations`` retain every coverage lane. ``predecessor_cells``
    retain actual method evidence; a declared no-input position has no synthetic
    cell. A merge keeps every lane, while a split remainder has no associations.
    This object carries evidence only; it has no value, Matrix, writer, or
    quantifiability authority.
    """

    cell_observation_id: str
    processing_request_id: str
    group: CrossSampleGroupIdentity
    canonical_sample: ResolvedCanonicalSampleIdentity
    position_associations: tuple[CrossSampleCoveragePositionAssociation, ...]
    predecessor_cells: tuple[CellObservationRef, ...]
    coverage_terminals: tuple[BackfillAttemptTerminal, ...]
    evidence_states: tuple[FinalCellEvidenceState, ...]
    basis_bytes: bytes
    backfill_cell: CellObservationRef | None = None
    _mint_token: InitVar[object] = field(kw_only=True, repr=False)

    def __post_init__(self, _mint_token: object) -> None:
        if _mint_token is not _FINAL_CELL_MINT_TOKEN:
            raise CellObservationContractError(
                "final cell observation must be minted by CellObservation"
            )
        # The composer validates the input joins and derives these fields once.

    @property
    def group_hypothesis_id(self) -> str:
        return self.group.group_hypothesis_id

    @property
    def canonical_sample_identity_id(self) -> str:
        return self.canonical_sample.identity.sample_identity_id

    @property
    def evidence_state(self) -> FinalCellEvidenceAggregate:
        """Return a scalar only when every retained lane has the same state."""

        if not self.evidence_states:
            return "not_searched"
        states = set(self.evidence_states)
        if len(states) == 1:
            return self.evidence_states[0]
        return "mixed"

    @property
    def method_assessments(self) -> tuple[CidNlMethodAssessmentRef, ...]:
        return tuple(cell.method_assessment for cell in self.effective_cells)

    @property
    def effective_cells(self) -> tuple[CellObservationRef, ...]:
        return (
            (self.backfill_cell,)
            if self.backfill_cell is not None
            else self.predecessor_cells
        )

    @property
    def direct_observations(self) -> tuple[PeakObservationRef, ...]:
        return tuple(
            cell.peak_observation
            for cell in self.effective_cells
            if cell.peak_observation is not None
        )

    @property
    def direct_hypotheses(self) -> tuple[PeakHypothesis, ...]:
        return tuple(
            cell.peak_hypothesis
            for cell in self.effective_cells
            if cell.peak_hypothesis is not None
        )

    @property
    def integrations(self) -> tuple[IntegrationResult, ...]:
        direct = tuple(
            hypothesis.integration
            for hypothesis in self.direct_hypotheses
            if hypothesis.integration is not None
        )
        ms1_only = tuple(
            observation.content.integration
            for observation in self.ms1_only_backfill_observations
        )
        return direct + ms1_only

    @property
    def ms1_only_backfill_observations(self) -> tuple[PeakObservationRef, ...]:
        """The single MS1 peak a group-bounded Backfill found without CID-NL."""

        if self.evidence_state != "ms1_only_backfill_observation":
            return ()
        return self.direct_observations

    @property
    def provider_ids(self) -> tuple[str, ...]:
        return tuple(
            cell.method_assessment.content.provider_id for cell in self.effective_cells
        )

    @property
    def method_subject_ids(self) -> tuple[str, ...]:
        return tuple(
            cell.group_context.method_subject_id for cell in self.effective_cells
        )

    @property
    def position_ids(self) -> tuple[str, ...]:
        return tuple(
            association.position.position_id
            for association in self.position_associations
        )

    @property
    def source_cell_artifact_ids(self) -> tuple[str, ...]:
        return tuple(
            cell.artifact.evidence_artifact_id
            for cell in (
                *self.predecessor_cells,
                *((self.backfill_cell,) if self.backfill_cell is not None else ()),
            )
        )


def compose_final_group_cell_observation_population(
    *,
    handoff: CrossSampleAssociationLineageHandoff,
    predecessor_cells: Sequence[CellObservationRef],
    backfill_cells: Sequence[CellObservationRef] = (),
) -> tuple[FinalGroupCellObservation, ...]:
    """Compose one exact final closure for every final Group × sample key.

    The complete Backfill coverage and CrossSample handoff are the only source
    of correlation. ``predecessor_cells`` cover every searched handoff position
    exactly once. Declared no-input positions remain explicit coverage without
    materializing method evidence.
    """

    if isinstance(predecessor_cells, (str, bytes)):
        raise CellObservationContractError(
            "predecessor cells must be a finite owner-sealed sequence"
        )
    sources = tuple(predecessor_cells)
    return _compose_final_group_cell_observation_population(
        handoff=handoff,
        predecessor_cells=sources,
        backfill_cells=tuple(backfill_cells),
    )


def _compose_final_group_cell_observation_population(
    *,
    handoff: CrossSampleAssociationLineageHandoff,
    predecessor_cells: tuple[CellObservationRef, ...],
    backfill_cells: tuple[CellObservationRef, ...] = (),
) -> tuple[FinalGroupCellObservation, ...]:
    request_id = _validate_final_handoff(handoff)
    backfilled_by_key: dict[tuple[str, str], CellObservationRef] = {}
    for backfill_ref in backfill_cells:
        _validate_source_cell(backfill_ref, processing_request_id=request_id)
        key = (
            backfill_ref.content.group_hypothesis_id,
            backfill_ref.content.canonical_sample_identity_id,
        )
        if key in backfilled_by_key:
            raise CellObservationContractError("duplicate completed backfill cell")
        backfilled_by_key[key] = backfill_ref
    positions = tuple(handoff.coverage.epoch.positions)
    source_by_position = _index_predecessor_cells(
        predecessor_cells,
        positions=positions,
        processing_request_id=request_id,
    )
    sources_by_group: dict[str, list[CellObservationRef]] = {}
    for cell in predecessor_cells:
        sources_by_group.setdefault(cell.content.group_hypothesis_id, []).append(cell)
    checked_facts: set[int] = set()
    for position in positions:
        proof = validate_backfill_conservative_no_request_position(
            position,
            source_cell=source_by_position.get(position.position_id),
        )
        if proof is not None and id(proof.decision.group_facts) not in checked_facts:
            validate_backfill_conservative_no_request_position(
                position,
                source_population=tuple(
                    sources_by_group[position.group_identity.group_hypothesis_id]
                ),
            )
            checked_facts.add(id(proof.decision.group_facts))
    terminal_lists: dict[str, list[BackfillAttemptTerminal]] = {}
    for terminal in handoff.coverage.terminals:
        terminal_lists.setdefault(terminal.position_id, []).append(terminal)
    terminals_by_position = {
        position.position_id: tuple(terminal_lists.get(position.position_id, ()))
        for position in positions
    }
    associations_by_key: dict[
        tuple[int, int], list[CrossSampleCoveragePositionAssociation]
    ] = {}
    for association in handoff.position_associations:
        associations_by_key.setdefault(
            (id(association.final_successor), id(association.position.sample)), []
        ).append(association)
    cells: list[FinalGroupCellObservation] = []
    for group in handoff.final_population:
        for sample in handoff.coverage.epoch.samples:
            associations = tuple(
                sorted(
                    associations_by_key.get((id(group), id(sample)), ()),
                    key=lambda item: item.position.position_id,
                )
            )
            source_lanes = tuple(
                source_by_position[item.position.position_id]
                for item in associations
                if item.position.position_id in source_by_position
            )
            coverage_lanes = _canonical_terminal_subset(
                associations,
                terminals_by_position,
            )
            states: tuple[FinalCellEvidenceState, ...]
            recovered = backfilled_by_key.pop(
                (group.group_hypothesis_id, sample.identity.sample_identity_id), None
            )
            if recovered is not None:
                if (
                    len(associations) != 1
                    or recovered.group_context.group_identity != group
                ):
                    raise CellObservationContractError(
                        "backfill must extend one exact predecessor"
                    )
                predecessor = associations[0].position.group_identity
                if not set(predecessor.member_subjects).issubset(group.member_subjects):
                    raise CellObservationContractError(
                        "backfill changed original group membership"
                    )
                if (
                    _member_for_sample(predecessor, sample.identity.sample_identity_id)
                    is not None
                ):
                    raise CellObservationContractError(
                        "backfill cannot replace an existing sample member"
                    )
                if (
                    recovered.content.unassigned_support
                    or recovered.content.outside_group is not None
                ):
                    states = ("unresolved",)
                elif recovered.peak_hypothesis is not None:
                    states = ("direct_observation",)
                elif recovered.content.method_result == "HOLD":
                    states = (
                        "unresolved"
                        if recovered.method_assessment.candidate_peaks
                        else "searched_no_observation",
                    )
                elif recovered.content.method_result == "REJECT":
                    states = ("rejected_observation",)
                else:
                    states = ("unavailable",)
            elif not associations:
                states = ("not_searched",)
            else:
                states = tuple(
                    _derive_lane_evidence_state(
                        association=association,
                        source_cell=source_by_position.get(
                            association.position.position_id
                        ),
                        coverage_terminals=terminals_by_position[
                            association.position.position_id
                        ],
                        final_group=group,
                        canonical_sample=sample,
                        processing_request_id=request_id,
                    )
                    for association in associations
                )
            cells.append(
                _mint_final_group_cell_observation(
                    processing_request_id=request_id,
                    handoff=handoff,
                    group=group,
                    canonical_sample=sample,
                    position_associations=associations,
                    predecessor_cells=source_lanes,
                    coverage_terminals=coverage_lanes,
                    evidence_states=states,
                    backfill_cell=recovered,
                )
            )
    if backfilled_by_key:
        raise CellObservationContractError(
            "backfill cells are outside the final population"
        )
    return tuple(sorted(cells, key=_final_cell_sort_key))


def validate_final_group_cell_observation_population(
    *,
    handoff: CrossSampleAssociationLineageHandoff,
    cell_observations: Sequence[FinalGroupCellObservation],
) -> tuple[FinalGroupCellObservation, ...]:
    """Revalidate exact handoff, source population, lanes, and final keys."""

    _validate_final_handoff(handoff)
    if isinstance(cell_observations, (str, bytes)):
        raise CellObservationContractError(
            "cell population must be a finite owner-sealed sequence"
        )
    observations = tuple(cell_observations)
    if any(type(item) is not FinalGroupCellObservation for item in observations):
        raise CellObservationContractError(
            "cell population contains a non-owner observation"
        )
    expected_keys = tuple(
        (group.group_hypothesis_id, sample.identity.sample_identity_id)
        for group in handoff.final_population
        for sample in handoff.coverage.epoch.samples
    )
    actual_keys = tuple(
        (item.group_hypothesis_id, item.canonical_sample_identity_id)
        for item in observations
    )
    if len(actual_keys) != len(set(actual_keys)):
        raise CellObservationContractError(
            "final cell population contains a duplicate Group x sample position"
        )
    if set(actual_keys) != set(expected_keys):
        raise CellObservationContractError(
            "final cell population is missing or contains an extra Group x sample"
        )
    groups_by_id = {
        group.group_hypothesis_id: group for group in handoff.final_population
    }
    samples_by_id = {
        sample.identity.sample_identity_id: sample
        for sample in handoff.coverage.epoch.samples
    }
    source_cells = tuple(
        cell for item in observations for cell in item.predecessor_cells
    )
    expected = compose_final_group_cell_observation_population(
        handoff=handoff,
        predecessor_cells=source_cells,
        backfill_cells=tuple(
            item.backfill_cell
            for item in observations
            if item.backfill_cell is not None
        ),
    )
    expected_by_key = {
        (item.group_hypothesis_id, item.canonical_sample_identity_id): item
        for item in expected
    }
    ordered = tuple(sorted(observations, key=_final_cell_sort_key))
    for item in ordered:
        key = (item.group_hypothesis_id, item.canonical_sample_identity_id)
        expected_item = expected_by_key[key]
        _validate_final_cell_handoff_refs(item, handoff=handoff)
        if item != expected_item:
            raise CellObservationContractError(
                "final cell does not retain the exact owner-composed content"
            )
        _require_same_refs(item, expected_item)
        if item.group is not groups_by_id[item.group_hypothesis_id]:
            raise CellObservationContractError(
                "final cell substituted an equivalent final group"
            )
        if (
            item.canonical_sample
            is not samples_by_id[item.canonical_sample_identity_id]
        ):
            raise CellObservationContractError(
                "final cell substituted an equivalent canonical sample"
            )
    return ordered


def _validate_final_handoff(
    handoff: CrossSampleAssociationLineageHandoff,
) -> str:
    if type(handoff) is not CrossSampleAssociationLineageHandoff:
        raise CellObservationContractError(
            "final closure requires an exact CrossSample association handoff"
        )
    try:
        validate_cross_sample_association_lineage_handoff(handoff)
    except (TypeError, ValueError) as exc:
        raise CellObservationContractError(str(exc)) from exc
    if handoff.final_population is not handoff.final_receipt.final_population:
        raise CellObservationContractError(
            "handoff final population is not the exact retained CrossSample receipt"
        )
    if not handoff.final_population:
        raise CellObservationContractError("handoff final population is empty")
    final_groups = {id(group) for group in handoff.final_population}
    epoch_groups = {id(group) for group in handoff.coverage.epoch.group_identities}
    epoch_samples = {id(sample) for sample in handoff.coverage.epoch.samples}
    for position_association in handoff.position_associations:
        if id(position_association.final_successor) not in final_groups:
            raise CellObservationContractError(
                "handoff position association substituted its final group"
            )
        if id(position_association.position.group_identity) not in epoch_groups:
            raise CellObservationContractError(
                "handoff position association substituted its predecessor group"
            )
        if id(position_association.position.sample) not in epoch_samples:
            raise CellObservationContractError(
                "handoff position association substituted its canonical sample"
            )
    request_id = handoff.coverage.epoch.processing_owners.request.processing_request_id
    if handoff.final_receipt.processing_request_id != request_id:
        raise CellObservationContractError(
            "handoff crossed its owner processing request"
        )
    return request_id


def _index_predecessor_cells(
    predecessor_cells: Sequence[CellObservationRef],
    *,
    positions: tuple[BackfillPlanningPosition, ...],
    processing_request_id: str,
) -> dict[str, CellObservationRef]:
    if isinstance(predecessor_cells, (str, bytes)):
        raise CellObservationContractError(
            "predecessor cells must be a finite owner-sealed sequence"
        )
    cells = tuple(predecessor_cells)
    if len({id(cell) for cell in cells}) != len(cells):
        raise CellObservationContractError(
            "predecessor cell population duplicates an exact source object"
        )
    result: dict[str, CellObservationRef] = {}
    positions_by_key: dict[tuple[str, str], list[BackfillPlanningPosition]] = {}
    for position in positions:
        key = (
            position.sample.identity.sample_identity_id,
            position.group_identity.group_hypothesis_id,
        )
        positions_by_key.setdefault(key, []).append(position)
    for cell in cells:
        _validate_source_cell(cell, processing_request_id=processing_request_id)
        source_identity = cell.group_context.group_identity
        matches = tuple(
            position
            for position in positions_by_key.get(
                (
                    cell.content.canonical_sample_identity_id,
                    source_identity.group_hypothesis_id,
                ),
                (),
            )
            if source_identity == position.group_identity
        )
        if len(matches) != 1:
            raise CellObservationContractError(
                "predecessor CellObservationRef does not bind exactly one "
                "Backfill coverage position by full group identity and sample"
            )
        position = matches[0]
        if position.position_id in result:
            raise CellObservationContractError(
                "predecessor CellObservationRef population duplicates a coverage "
                "position"
            )
        result[position.position_id] = cell
    expected_ids = {
        position.position_id
        for position in positions
        if position.planning_reason != "no_discovery_input"
    }
    if set(result) != expected_ids:
        raise CellObservationContractError(
            "predecessor CellObservationRef population is incomplete or extra"
        )
    return result


def _validate_source_cell(
    cell: CellObservationRef,
    *,
    processing_request_id: str,
) -> None:
    if type(cell) is not CellObservationRef:
        raise CellObservationContractError(
            "predecessor population contains a non-owner CellObservationRef"
        )
    # CellObservationRef validated its immutable content at construction. This
    # consumer checks the new request/group/sample join, not that owner again.
    if cell.content.group_hypothesis_id != cell.group_context.group.group_hypothesis_id:
        raise CellObservationContractError(
            "predecessor CellObservationRef crossed its group identity"
        )
    if cell.group_context.group.processing_request_id != processing_request_id:
        raise CellObservationContractError(
            "predecessor CellObservationRef crossed its processing request"
        )


def _canonical_terminal_subset(
    associations: tuple[CrossSampleCoveragePositionAssociation, ...],
    terminals_by_position: dict[str, tuple[BackfillAttemptTerminal, ...]],
) -> tuple[BackfillAttemptTerminal, ...]:
    position_rank = {
        association.position.position_id: index
        for index, association in enumerate(associations)
    }
    terminals = tuple(
        terminal
        for association in associations
        for terminal in terminals_by_position.get(association.position.position_id, ())
    )
    return tuple(
        sorted(
            terminals,
            key=lambda item: (
                position_rank.get(item.position_id, len(position_rank)),
                item.candidate_phase,
                item.candidate_index,
                item.logical_request.logical_request_id,
                item.request_identity.request_identity_id,
                item.terminal_id,
            ),
        )
    )


def _derive_lane_evidence_state(
    *,
    association: CrossSampleCoveragePositionAssociation,
    source_cell: CellObservationRef | None,
    coverage_terminals: tuple[BackfillAttemptTerminal, ...],
    final_group: CrossSampleGroupIdentity,
    canonical_sample: ResolvedCanonicalSampleIdentity,
    processing_request_id: str,
) -> FinalCellEvidenceState:
    if source_cell is None:
        if (
            association.position.planning_reason != "no_discovery_input"
            or association.position.planning_state != "not_planned"
            or association.position.sample is not canonical_sample
            or association.final_successor is not final_group
            or coverage_terminals
        ):
            raise CellObservationContractError(
                "missing source cell requires exact no-input coverage"
            )
        return "not_searched"
    _validate_source_cell(source_cell, processing_request_id=processing_request_id)
    source_identity = source_cell.group_context.group_identity
    if source_identity != association.position.group_identity:
        raise CellObservationContractError(
            "source CellObservationRef crossed the exact predecessor group identity"
        )
    if (
        source_cell.content.canonical_sample_identity_id
        != association.position.sample.identity.sample_identity_id
        or association.position.sample is not canonical_sample
        or association.final_successor is not final_group
    ):
        raise CellObservationContractError(
            "source CellObservationRef crossed the exact canonical sample"
        )
    source_member = _member_for_sample(
        source_identity,
        association.position.sample.identity.sample_identity_id,
    )
    final_member = _member_for_sample(
        final_group,
        canonical_sample.identity.sample_identity_id,
    )
    if source_member is not None and source_member not in final_group.member_subjects:
        raise CellObservationContractError(
            "CrossSample successor dropped the exact source member evidence"
        )
    assessment = source_cell.method_assessment
    technical_state = assessment.content.technical_state
    method_result = assessment.content.method_result
    if source_cell.content.unassigned_support:
        if final_member is not None:
            raise CellObservationContractError(
                "unassigned support cannot supply a final member"
            )
        return "unresolved"
    if source_cell.content.outside_group is not None:
        if (
            source_member is not None
            or source_cell.peak_observation is not None
            or source_cell.peak_hypothesis is not None
            or coverage_terminals
            or association.position.planning_state != "not_planned"
            or association.position.planning_reason != "local_owner_not_supersedable"
        ):
            raise CellObservationContractError(
                "outside-group lane cannot supply local observation evidence"
            )
        # A merged sibling may supply a direct lane; this lane adds no value.
        return "not_searched"
    no_request_proof = validate_backfill_conservative_no_request_position(
        association.position, source_cell=source_cell
    )
    if no_request_proof is not None:
        if coverage_terminals or final_member is not None:
            raise CellObservationContractError(
                "conservative no-request lane cannot carry physical evidence"
            )
        return (
            "not_searched"
            if no_request_proof.variant == "threshold_not_met"
            else "unavailable"
        )
    if final_member is not None and method_result in {"HOLD", "REJECT"}:
        raise CellObservationContractError(
            "final CrossSample member cannot carry a HOLD or REJECT method lane"
        )
    if validate_backfill_retained_candidate_position(
        association.position, source_cell=source_cell
    ):
        if coverage_terminals:
            raise CellObservationContractError(
                "retained unresolved candidates cannot claim acquisition"
            )
        return "unresolved"
    retained_local_owner = (
        association.position.planning_state == "not_planned"
        and association.position.planning_reason == "retained_local_owner"
    )
    if (
        association.position.planning_state == "not_planned"
        and not retained_local_owner
    ):
        if source_cell.peak_observation is not None or source_cell.peak_hypothesis:
            raise CellObservationContractError(
                "not-planned lane cannot carry direct observation evidence"
            )
        if technical_state in {"unsupported", "failed"}:
            return "unavailable"
        if technical_state != "not_evaluated" or method_result is not None:
            raise CellObservationContractError(
                "not-planned coverage position crossed an evaluated source cell"
            )
        return "not_searched"
    if not retained_local_owner and not coverage_terminals:
        raise CellObservationContractError(
            "planned coverage position has no exact retained terminal"
        )
    coverage_unavailable = any(
        terminal.candidate_phase
        in {"primary_fallback", "validation_unavailable", "validation_fallback"}
        or (
            terminal.physical_evaluation is not None
            and terminal.physical_evaluation.state in {"unavailable", "failed"}
        )
        for terminal in coverage_terminals
    )
    if technical_state in {"unsupported", "failed"}:
        if source_cell.peak_observation is not None or source_cell.peak_hypothesis:
            raise CellObservationContractError(
                "unavailable lane cannot carry direct observation evidence"
            )
        return "unavailable"
    if technical_state == "not_evaluated":
        if not coverage_unavailable:
            raise CellObservationContractError(
                "evaluated Backfill coverage contradicts a not-evaluated source cell"
            )
        return "unavailable"
    if method_result == "SUPPORT":
        if final_member is None:
            raise CellObservationContractError(
                "SUPPORT evidence has no exact final CrossSample member"
            )
        if source_cell.peak_observation is None or source_cell.peak_hypothesis is None:
            raise CellObservationContractError(
                "SUPPORT source cell lacks its exact observation and hypothesis"
            )
        _direct_member_integration(
            member=final_member,
            processing_request_id=processing_request_id,
            canonical_sample=canonical_sample,
            terminal_variant="observed",
            terminal_hypothesis=source_cell.peak_hypothesis,
            method_assessment=assessment,
            peak_observation=source_cell.peak_observation,
            peak_hypothesis=source_cell.peak_hypothesis,
        )
        return "direct_observation"
    if method_result == "REJECT":
        if source_cell.peak_hypothesis is not None:
            raise CellObservationContractError(
                "REJECT lane cannot carry a SUPPORT hypothesis"
            )
        return "rejected_observation"
    if method_result != "HOLD":
        raise CellObservationContractError(
            "evaluated source cell requires SUPPORT, HOLD, or REJECT"
        )
    if source_cell.peak_hypothesis is not None:
        raise CellObservationContractError(
            "HOLD lane cannot carry a SUPPORT hypothesis"
        )
    if not assessment.candidate_peaks and source_cell.peak_observation is None:
        return "searched_no_observation"
    if (
        source_cell.peak_observation is not None
        and source_cell.peak_observation not in assessment.candidate_peaks
    ):
        raise CellObservationContractError(
            "unresolved source evidence is outside the exact candidate universe"
        )
    if (
        source_cell.peak_observation is not None
        and is_ms1_only_cid_nl_backfill_assessment(assessment)
    ):
        return "ms1_only_backfill_observation"
    return "unresolved"


def _member_for_sample(
    group: CrossSampleGroupIdentity,
    sample_identity_id: str,
) -> CrossSampleGroupMemberSubject | None:
    matches = tuple(
        member
        for member in group.member_subjects
        if member.canonical_sample_identity_id == sample_identity_id
    )
    if len(matches) > 1:
        raise CellObservationContractError(
            "CrossSample group has duplicate members for one canonical sample"
        )
    return matches[0] if matches else None


def _mint_final_group_cell_observation(
    *,
    processing_request_id: str,
    handoff: CrossSampleAssociationLineageHandoff,
    group: CrossSampleGroupIdentity,
    canonical_sample: ResolvedCanonicalSampleIdentity,
    position_associations: tuple[CrossSampleCoveragePositionAssociation, ...],
    predecessor_cells: tuple[CellObservationRef, ...],
    coverage_terminals: tuple[BackfillAttemptTerminal, ...],
    evidence_states: tuple[FinalCellEvidenceState, ...],
    backfill_cell: CellObservationRef | None = None,
) -> FinalGroupCellObservation:
    payload = _final_closure_basis(
        processing_request_id=processing_request_id,
        handoff=handoff,
        group=group,
        canonical_sample=canonical_sample,
        position_associations=position_associations,
        predecessor_cells=predecessor_cells,
        coverage_terminals=coverage_terminals,
        evidence_states=evidence_states,
        backfill_cell=backfill_cell,
    )
    identity, basis = mint_typed_identity(_FINAL_CELL_OBSERVATION_ID_PREFIX, payload)
    return FinalGroupCellObservation(
        cell_observation_id=identity,
        processing_request_id=processing_request_id,
        group=group,
        canonical_sample=canonical_sample,
        position_associations=position_associations,
        predecessor_cells=predecessor_cells,
        coverage_terminals=coverage_terminals,
        evidence_states=evidence_states,
        basis_bytes=basis,
        backfill_cell=backfill_cell,
        _mint_token=_FINAL_CELL_MINT_TOKEN,
    )


def _final_closure_basis(
    *,
    processing_request_id: str,
    handoff: CrossSampleAssociationLineageHandoff | None = None,
    handoff_id: str | None = None,
    group: CrossSampleGroupIdentity,
    canonical_sample: ResolvedCanonicalSampleIdentity,
    position_associations: tuple[CrossSampleCoveragePositionAssociation, ...],
    predecessor_cells: tuple[CellObservationRef, ...],
    coverage_terminals: tuple[BackfillAttemptTerminal, ...],
    evidence_states: tuple[FinalCellEvidenceState, ...],
    backfill_cell: CellObservationRef | None = None,
) -> dict[str, object]:
    resolved_handoff_id = handoff_id or (
        handoff.handoff_id if handoff is not None else None
    )
    if resolved_handoff_id is None:
        raise CellObservationContractError("final cell basis requires exact handoff")
    payload: dict[str, object] = {
        "schema_version": "final_group_cell_observation",
        "processing_request_id": processing_request_id,
        "handoff_id": resolved_handoff_id,
        "group": {
            "group_hypothesis_id": group.group_hypothesis_id,
            "processing_request_id": group.processing_request_id,
            "member_subject_ids": [
                member.member_subject_id for member in group.member_subjects
            ],
            "basis_sha256": hashlib.sha256(group.basis_bytes).hexdigest(),
            "disposition": group.disposition,
        },
        "canonical_sample_identity_id": canonical_sample.identity.sample_identity_id,
        "position_associations": [
            {
                "position_id": item.position.position_id,
                "predecessor_group_hypothesis_id": (
                    item.position.group_identity.group_hypothesis_id
                ),
                "predecessor_group_basis_sha256": hashlib.sha256(
                    item.position.group_identity.basis_bytes
                ).hexdigest(),
                "sample_identity_id": item.position.sample.identity.sample_identity_id,
                "final_successor_group_hypothesis_id": (
                    item.final_successor.group_hypothesis_id
                ),
                "resolution": item.resolution,
            }
            for item in position_associations
        ],
        "predecessor_cells": [
            {
                "artifact_id": cell.artifact.evidence_artifact_id,
                "content_sha256": cell.artifact.content_identity.content_sha256,
                "group_hypothesis_id": cell.group_context.group.group_hypothesis_id,
                "sample_identity_id": cell.content.canonical_sample_identity_id,
                "method_assessment_artifact_content_id": (
                    cell.method_assessment.artifact.content_identity.artifact_content_id
                ),
                "peak_observation_content_id": (
                    None
                    if cell.peak_observation is None
                    else cell.peak_observation.content.peak_observation_content_id
                ),
                "peak_hypothesis_id": (
                    None
                    if cell.peak_hypothesis is None
                    else cell.peak_hypothesis.hypothesis_id
                ),
            }
            for cell in predecessor_cells
        ],
        "coverage_terminals": [item.terminal_id for item in coverage_terminals],
        # Keep the canonical identity encoding stable for existing artifacts.
        "repair_receipts": [],
        "repaired_observations": [],
        "evidence_states": list(evidence_states),
    }
    if backfill_cell is not None:
        payload["backfill_cell_artifact_id"] = (
            backfill_cell.artifact.evidence_artifact_id
        )
    return payload


def _final_cell_sort_key(
    cell: FinalGroupCellObservation,
) -> tuple[str, str]:
    return cell.group_hypothesis_id, cell.canonical_sample_identity_id


def _validate_final_cell_handoff_refs(
    cell: FinalGroupCellObservation,
    *,
    handoff: CrossSampleAssociationLineageHandoff,
) -> None:
    groups = {group.group_hypothesis_id: group for group in handoff.final_population}
    samples = {
        sample.identity.sample_identity_id: sample
        for sample in handoff.coverage.epoch.samples
    }
    group = groups.get(cell.group_hypothesis_id)
    sample = samples.get(cell.canonical_sample_identity_id)
    if group is None or sample is None:
        raise CellObservationContractError(
            "final cell key is outside the exact handoff population"
        )
    if cell.group is not group or cell.canonical_sample is not sample:
        raise CellObservationContractError(
            "final cell substituted an equivalent handoff group or sample"
        )
    handoff_associations = handoff.position_associations
    handoff_terminals = handoff.coverage.terminals
    for association in cell.position_associations:
        if not any(association is item for item in handoff_associations):
            raise CellObservationContractError(
                "final cell position association is not the exact handoff object"
            )
    for terminal in cell.coverage_terminals:
        if not any(terminal is item for item in handoff_terminals):
            raise CellObservationContractError(
                "final cell terminal is not the exact coverage object"
            )


def _require_same_refs(
    actual: FinalGroupCellObservation,
    expected: FinalGroupCellObservation,
) -> None:
    if actual.backfill_cell is not expected.backfill_cell:
        raise CellObservationContractError(
            "final cell substituted its completed backfill reference"
        )
    if (
        actual.group is not expected.group
        or actual.canonical_sample is not expected.canonical_sample
    ):
        raise CellObservationContractError(
            "final cell did not retain exact group/sample owner references"
        )
    for actual_association, expected_association in zip(
        actual.position_associations,
        expected.position_associations,
        strict=True,
    ):
        if actual_association is not expected_association:
            raise CellObservationContractError(
                "final cell did not retain exact position association references"
            )
    for actual_cell, expected_cell in zip(
        actual.predecessor_cells,
        expected.predecessor_cells,
        strict=True,
    ):
        if actual_cell is not expected_cell:
            raise CellObservationContractError(
                "final cell did not retain exact predecessor cell references"
            )
    if any(
        actual_ref is not expected_ref
        for actual_ref, expected_ref in zip(
            actual.coverage_terminals,
            expected.coverage_terminals,
            strict=True,
        )
    ):
        raise CellObservationContractError(
            "final cell did not retain exact coverage terminal references"
        )


__all__ = [
    "ARTIFACT_SEMANTIC_ROLE",
    "CidNlCellGroupContext",
    "CidNlCellGroupSupport",
    "CellObservationContent",
    "CellObservationContractError",
    "CellObservationRef",
    "FinalCellEvidenceAggregate",
    "FinalCellEvidenceState",
    "FinalGroupCellObservation",
    "OutsideGroupCellRelation",
    "SCHEMA_VERSION",
    "TerminalObservationVariant",
    "canonical_cell_observation_bytes",
    "compose_final_group_cell_observation_population",
    "mint_cell_observation_ref",
    "validate_final_group_cell_observation_population",
]
