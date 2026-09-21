"""Minimal sample-by-group observation closure for diagnostic evidence flow."""

from __future__ import annotations

import hashlib
import re
from collections.abc import Mapping, Sequence
from dataclasses import InitVar, dataclass, field
from typing import Literal, TypeAlias

from xic_extractor.alignment.backfill.evaluation_coverage import (
    OwnerBackfillAttemptTerminal,
    OwnerBackfillPlanningPosition,
    validate_owner_backfill_conservative_no_request_position,
    validate_owner_backfill_retained_candidate_position,
)
from xic_extractor.alignment.backfill.observation_repair import (
    ObservationRepairReceipt,
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
    _repaired_member_integration,
    canonical_cell_observation_bytes,
    mint_cell_observation_ref,
)
from xic_extractor.alignment.config import AlignmentConfig
from xic_extractor.alignment.cross_sample.association import (
    CrossSampleAssociationLineageHandoff,
    CrossSampleCoveragePositionAssociation,
    validate_cross_sample_association_lineage_handoff,
)
from xic_extractor.alignment.cross_sample_peak_groups import (
    CrossSampleGroupIdentity,
    CrossSampleGroupMemberSubject,
    CrossSamplePeakGroupHypothesis,
)
from xic_extractor.alignment.edge_scoring import DriftLookupProtocol
from xic_extractor.alignment.hypothesis_owner_adapter import (
    compose_peak_hypotheses_from_assessment_universe,
)
from xic_extractor.alignment.owner_backfill import (
    OwnerBackfillPeakFactObservation,
    validate_owner_backfill_peak_fact_observation,
)
from xic_extractor.evidence_artifacts import (
    _mint_artifact_semantic_contract_ref,
)
from xic_extractor.peak_detection.cid_nl_method_assessment import (
    CidNlMethodAssessmentRef,
)
from xic_extractor.peak_detection.hypotheses import PeakHypothesis
from xic_extractor.peak_detection.integration_result import IntegrationResult
from xic_extractor.peak_detection.observations import PeakObservationRef
from xic_extractor.processing_identity import (
    ProcessingOwnerReferences,
    canonical_identity_basis_bytes,
    typed_identity_digest,
)
from xic_extractor.sample_metadata import ResolvedCanonicalSampleIdentity

SCHEMA_VERSION = "cell_observation_v2"
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
    "repaired_observation",
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
# The first implementation of this module exposed a final-cell mint function
# whose caller supplied group, terminal, provider, and evidence arguments.  A
# final cell is a correlation boundary, so those arguments are not an owner
# contract: the only admissible inputs are the CrossSample handoff and the
# owner-minted predecessor cell population.  The definitions below intentionally
# supersede the legacy names above while keeping the low-level
# ``mint_cell_observation_ref`` owner API intact.


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
    coverage_terminals: tuple[OwnerBackfillAttemptTerminal, ...]
    repair_receipts: tuple[ObservationRepairReceipt, ...]
    repaired_observations: tuple[OwnerBackfillPeakFactObservation, ...]
    evidence_states: tuple[FinalCellEvidenceState, ...]
    basis_bytes: bytes
    recovery_cell: CellObservationRef | None = None
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
            (self.recovery_cell,)
            if self.recovery_cell is not None
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
        repaired = tuple(
            observation.facts.integration
            for observation in self.repaired_observations
            if observation.facts is not None
        )
        return direct + repaired

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
                *((self.recovery_cell,) if self.recovery_cell is not None else ()),
            )
        )

    @property
    def repair_provenance(
        self,
    ) -> tuple[
        tuple[ObservationRepairReceipt, ...],
        tuple[OwnerBackfillPeakFactObservation, ...],
    ]:
        return self.repair_receipts, self.repaired_observations


def compose_final_group_cell_observation_population(
    *,
    handoff: CrossSampleAssociationLineageHandoff,
    predecessor_cells: Sequence[CellObservationRef],
    repair_receipts: Sequence[ObservationRepairReceipt] = (),
    repaired_observations: Sequence[OwnerBackfillPeakFactObservation] = (),
    recovery_cells: Sequence[CellObservationRef] = (),
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
    if isinstance(repair_receipts, (str, bytes)) or isinstance(
        repaired_observations, (str, bytes)
    ):
        raise CellObservationContractError(
            "repair evidence must be finite owner-sealed sequences"
        )
    sources = tuple(predecessor_cells)
    receipts = tuple(repair_receipts)
    observations = tuple(repaired_observations)
    return _compose_final_group_cell_observation_population(
        handoff=handoff,
        predecessor_cells=sources,
        repair_receipts=receipts,
        repaired_observations=observations,
        recovery_cells=tuple(recovery_cells),
    )


def _compose_final_group_cell_observation_population(
    *,
    handoff: CrossSampleAssociationLineageHandoff,
    predecessor_cells: tuple[CellObservationRef, ...],
    repair_receipts: tuple[ObservationRepairReceipt, ...],
    repaired_observations: tuple[OwnerBackfillPeakFactObservation, ...],
    recovery_cells: tuple[CellObservationRef, ...] = (),
) -> tuple[FinalGroupCellObservation, ...]:
    request_id = _validate_final_handoff(handoff)
    recovered_by_key: dict[tuple[str, str], CellObservationRef] = {}
    for recovery_ref in recovery_cells:
        _validate_source_cell(recovery_ref, processing_request_id=request_id)
        key = (
            recovery_ref.content.group_hypothesis_id,
            recovery_ref.content.canonical_sample_identity_id,
        )
        if key in recovered_by_key:
            raise CellObservationContractError("duplicate completed recovery cell")
        recovered_by_key[key] = recovery_ref
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
        proof = validate_owner_backfill_conservative_no_request_position(
            position,
            source_cell=source_by_position.get(position.position_id),
        )
        if proof is not None and id(proof.decision.group_facts) not in checked_facts:
            validate_owner_backfill_conservative_no_request_position(
                position,
                source_population=tuple(
                    sources_by_group[position.group_identity.group_hypothesis_id]
                ),
            )
            checked_facts.add(id(proof.decision.group_facts))
    receipt_by_position, observation_by_receipt = _index_repair_inputs(
        repair_receipts,
        repaired_observations,
        positions=positions,
        final_population=handoff.final_population,
        processing_request_id=request_id,
    )
    _validate_handoff_repair_closure(
        handoff=handoff,
        positions=positions,
        repair_receipts=repair_receipts,
        repaired_observations=repaired_observations,
        receipt_by_position=receipt_by_position,
        observation_by_receipt=observation_by_receipt,
    )
    terminal_lists: dict[str, list[OwnerBackfillAttemptTerminal]] = {}
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
            receipts = tuple(
                sorted(
                    (
                        receipt
                        for association in associations
                        for receipt in receipt_by_position.get(
                            association.position.position_id,
                            (),
                        )
                    ),
                    key=lambda item: item.receipt_id,
                )
            )
            repaired = tuple(
                sorted(
                    (
                        observation
                        for receipt in receipts
                        for observation in observation_by_receipt.get(
                            receipt.receipt_id,
                            (),
                        )
                    ),
                    key=lambda item: item.work_unit_key,
                )
            )
            states: tuple[FinalCellEvidenceState, ...]
            recovered = recovered_by_key.pop(
                (group.group_hypothesis_id, sample.identity.sample_identity_id), None
            )
            if recovered is not None:
                if (
                    len(associations) != 1
                    or recovered.group_context.group_identity != group
                ):
                    raise CellObservationContractError(
                        "recovery must extend one exact predecessor"
                    )
                predecessor = associations[0].position.group_identity
                if not set(predecessor.member_subjects).issubset(group.member_subjects):
                    raise CellObservationContractError(
                        "recovery changed original group membership"
                    )
                if (
                    _member_for_sample(predecessor, sample.identity.sample_identity_id)
                    is not None
                ):
                    raise CellObservationContractError(
                        "recovery cannot replace an existing sample member"
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
                        repair_receipts=receipt_by_position.get(
                            association.position.position_id,
                            (),
                        ),
                        repaired_observations=tuple(
                            observation
                            for receipt in receipt_by_position.get(
                                association.position.position_id,
                                (),
                            )
                            for observation in observation_by_receipt.get(
                                receipt.receipt_id,
                                (),
                            )
                        ),
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
                    repair_receipts=receipts,
                    repaired_observations=repaired,
                    evidence_states=states,
                    recovery_cell=recovered,
                )
            )
    if recovered_by_key:
        raise CellObservationContractError(
            "recovery cells are outside the final population"
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
    receipts = tuple(
        receipt for item in observations for receipt in item.repair_receipts
    )
    repaired = tuple(
        observation
        for item in observations
        for observation in item.repaired_observations
    )
    expected = compose_final_group_cell_observation_population(
        handoff=handoff,
        predecessor_cells=source_cells,
        repair_receipts=receipts,
        repaired_observations=repaired,
        recovery_cells=tuple(
            item.recovery_cell
            for item in observations
            if item.recovery_cell is not None
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
    positions: tuple[OwnerBackfillPlanningPosition, ...],
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
    positions_by_key: dict[tuple[str, str], list[OwnerBackfillPlanningPosition]] = {}
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


def _index_repair_inputs(
    repair_receipts: Sequence[ObservationRepairReceipt],
    repaired_observations: Sequence[OwnerBackfillPeakFactObservation],
    *,
    positions: tuple[OwnerBackfillPlanningPosition, ...],
    final_population: tuple[CrossSampleGroupIdentity, ...],
    processing_request_id: str,
) -> tuple[
    dict[str, tuple[ObservationRepairReceipt, ...]],
    dict[str, tuple[OwnerBackfillPeakFactObservation, ...]],
]:
    if isinstance(repair_receipts, (str, bytes)) or isinstance(
        repaired_observations,
        (str, bytes),
    ):
        raise CellObservationContractError(
            "repair evidence must be finite owner-sealed sequences"
        )
    receipts = tuple(repair_receipts)
    observations = tuple(repaired_observations)
    if any(type(receipt) is not ObservationRepairReceipt for receipt in receipts):
        raise CellObservationContractError(
            "repair population contains a non-owner receipt"
        )
    if len({receipt.receipt_id for receipt in receipts}) != len(receipts):
        raise CellObservationContractError("repair population duplicates a receipt")
    if any(
        type(observation) is not OwnerBackfillPeakFactObservation
        for observation in observations
    ):
        raise CellObservationContractError(
            "repair population contains a non-owner Backfill observation"
        )
    for observation in observations:
        try:
            validate_owner_backfill_peak_fact_observation(observation)
        except (AttributeError, TypeError, ValueError) as exc:
            raise CellObservationContractError(
                f"repaired observation is not revalidatable: {exc}"
            ) from exc
    if len({observation.work_unit_key for observation in observations}) != len(
        observations
    ):
        raise CellObservationContractError(
            "repair population duplicates a Backfill work unit"
        )
    position_by_id = {position.position_id: position for position in positions}
    receipts_by_position: dict[str, list[ObservationRepairReceipt]] = {}
    for receipt in receipts:
        subject = receipt.subject
        if subject.predecessor_group.processing_request_id != processing_request_id:
            raise CellObservationContractError(
                "repair receipt crossed its processing request"
            )
        matching_positions = tuple(
            position
            for position in positions
            if position.sample.identity.sample_identity_id
            == subject.sample_identity.identity.sample_identity_id
            and subject.predecessor_group == position.group_identity
        )
        if len(matching_positions) != 1:
            raise CellObservationContractError(
                "repair receipt does not bind exactly one coverage position"
            )
        receipts_by_position.setdefault(
            matching_positions[0].position_id,
            [],
        ).append(receipt)
    observation_by_receipt: dict[str, list[OwnerBackfillPeakFactObservation]] = {}
    for observation in observations:
        matching_receipts = tuple(
            receipt
            for receipt in receipts
            if observation.logical_request == receipt.subject.logical_request
            and observation.request_identity == receipt.subject.request_identity
            and observation.cell.group_hypothesis_id
            == receipt.subject.predecessor_group.group_hypothesis_id
            and observation.cell.sample_stem == receipt.sample_stem
        )
        if len(matching_receipts) != 1:
            raise CellObservationContractError(
                "repaired observation does not bind exactly one repair receipt"
            )
        receipt = matching_receipts[0]
        if receipt.outcome != "repaired":
            raise CellObservationContractError(
                "a no-repair receipt cannot carry repaired observation facts"
            )
        observation_by_receipt.setdefault(receipt.receipt_id, []).append(observation)
    for receipt in receipts:
        matched_observations = observation_by_receipt.get(receipt.receipt_id, ())
        if receipt.outcome == "repaired" and len(matched_observations) != 1:
            raise CellObservationContractError(
                "repaired receipt requires exactly one live Backfill observation"
            )
        if receipt.outcome == "no_repair" and matched_observations:
            raise CellObservationContractError(
                "no-repair receipt carries contradictory repaired observation facts"
            )
    if any(position_id not in position_by_id for position_id in receipts_by_position):
        raise CellObservationContractError(
            "repair receipt references an extra position"
        )
    # ``final_population`` is consumed here to make the owner boundary explicit:
    # a repair is only accepted as evidence for a CrossSample successor that is
    # actually present in the handoff; no successor is minted or inferred here.
    if not final_population:
        raise CellObservationContractError("handoff final population is empty")
    return (
        {
            key: tuple(sorted(value, key=lambda item: item.receipt_id))
            for key, value in receipts_by_position.items()
        },
        {
            key: tuple(sorted(value, key=lambda item: item.work_unit_key))
            for key, value in observation_by_receipt.items()
        },
    )


def _validate_handoff_repair_closure(
    *,
    handoff: CrossSampleAssociationLineageHandoff,
    positions: tuple[OwnerBackfillPlanningPosition, ...],
    repair_receipts: Sequence[ObservationRepairReceipt],
    repaired_observations: Sequence[OwnerBackfillPeakFactObservation],
    receipt_by_position: dict[str, tuple[ObservationRepairReceipt, ...]],
    observation_by_receipt: dict[str, tuple[OwnerBackfillPeakFactObservation, ...]],
) -> None:
    """Reject the retired grouping-time repair injection path."""
    if repair_receipts or repaired_observations:
        raise CellObservationContractError(
            "repair observations must be closed by bounded Backfill before finalization"
        )


def _canonical_terminal_subset(
    associations: tuple[CrossSampleCoveragePositionAssociation, ...],
    terminals_by_position: dict[str, tuple[OwnerBackfillAttemptTerminal, ...]],
) -> tuple[OwnerBackfillAttemptTerminal, ...]:
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
    coverage_terminals: tuple[OwnerBackfillAttemptTerminal, ...],
    repair_receipts: tuple[ObservationRepairReceipt, ...],
    repaired_observations: tuple[OwnerBackfillPeakFactObservation, ...],
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
            or repair_receipts
            or repaired_observations
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
    if len(repair_receipts) > 1:
        raise CellObservationContractError(
            "one exact predecessor position has multiple repair receipts"
        )
    if len(repaired_observations) > 1:
        raise CellObservationContractError(
            "one exact repair receipt has multiple live observations"
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
            or repair_receipts
            or repaired_observations
            or coverage_terminals
            or association.position.planning_state != "not_planned"
            or association.position.planning_reason != "local_owner_not_supersedable"
        ):
            raise CellObservationContractError(
                "outside-group lane cannot supply local observation or repair evidence"
            )
        # A merged sibling may supply a direct lane; this lane adds no value.
        return "not_searched"
    no_request_proof = validate_owner_backfill_conservative_no_request_position(
        association.position, source_cell=source_cell
    )
    if no_request_proof is not None:
        if (
            coverage_terminals
            or repair_receipts
            or repaired_observations
            or final_member is not None
        ):
            raise CellObservationContractError(
                "conservative no-request lane cannot carry physical or repair evidence"
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
    if validate_owner_backfill_retained_candidate_position(
        association.position, source_cell=source_cell
    ):
        if coverage_terminals or repair_receipts or repaired_observations:
            raise CellObservationContractError(
                "retained unresolved candidates cannot claim acquisition or repair"
            )
        return "unresolved"
    if repair_receipts:
        receipt = repair_receipts[0]
        if receipt.outcome == "repaired":
            if not repaired_observations:
                raise CellObservationContractError(
                    "repaired receipt lacks its exact live observation"
                )
            if final_member is None:
                raise CellObservationContractError(
                    "repair evidence cannot create CrossSample membership"
                )
            _repaired_member_integration(
                member=final_member,
                group=final_group,
                canonical_sample=canonical_sample,
                repair_receipt=receipt,
                repaired_observation=repaired_observations[0],
            )
            if source_cell.peak_observation is not None or source_cell.peak_hypothesis:
                raise CellObservationContractError(
                    "one evidence lane cannot claim direct and repaired observation"
                )
            if method_result == "REJECT":
                raise CellObservationContractError(
                    "repaired evidence cannot override a rejected method lane"
                )
            return "repaired_observation"
        if repaired_observations:
            raise CellObservationContractError(
                "no-repair lane contains contradictory live observation facts"
            )
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
    coverage_terminals: tuple[OwnerBackfillAttemptTerminal, ...],
    repair_receipts: tuple[ObservationRepairReceipt, ...],
    repaired_observations: tuple[OwnerBackfillPeakFactObservation, ...],
    evidence_states: tuple[FinalCellEvidenceState, ...],
    recovery_cell: CellObservationRef | None = None,
) -> FinalGroupCellObservation:
    basis = _final_closure_basis(
        processing_request_id=processing_request_id,
        handoff=handoff,
        group=group,
        canonical_sample=canonical_sample,
        position_associations=position_associations,
        predecessor_cells=predecessor_cells,
        coverage_terminals=coverage_terminals,
        repair_receipts=repair_receipts,
        repaired_observations=repaired_observations,
        evidence_states=evidence_states,
        recovery_cell=recovery_cell,
    )
    return FinalGroupCellObservation(
        cell_observation_id=typed_identity_digest(
            _FINAL_CELL_OBSERVATION_ID_PREFIX,
            basis,
        ),
        processing_request_id=processing_request_id,
        group=group,
        canonical_sample=canonical_sample,
        position_associations=position_associations,
        predecessor_cells=predecessor_cells,
        coverage_terminals=coverage_terminals,
        repair_receipts=repair_receipts,
        repaired_observations=repaired_observations,
        evidence_states=evidence_states,
        basis_bytes=basis,
        recovery_cell=recovery_cell,
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
    coverage_terminals: tuple[OwnerBackfillAttemptTerminal, ...],
    repair_receipts: tuple[ObservationRepairReceipt, ...],
    repaired_observations: tuple[OwnerBackfillPeakFactObservation, ...],
    evidence_states: tuple[FinalCellEvidenceState, ...],
    recovery_cell: CellObservationRef | None = None,
) -> bytes:
    resolved_handoff_id = handoff_id or (
        handoff.handoff_id if handoff is not None else None
    )
    if resolved_handoff_id is None:
        raise CellObservationContractError("final cell basis requires exact handoff")
    payload = {
        "schema_version": "final_group_cell_observation_v3",
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
        "repair_receipts": [item.receipt_id for item in repair_receipts],
        "repaired_observations": [
            {
                "work_unit_key": item.work_unit_key,
                "fact_content_id": (
                    None
                    if item.physical_evaluation is None
                    or item.physical_evaluation.facts is None
                    else item.physical_evaluation.facts.fact_content_id
                ),
            }
            for item in repaired_observations
        ],
        "evidence_states": list(evidence_states),
    }
    if recovery_cell is not None:
        payload["recovery_cell_artifact_id"] = (
            recovery_cell.artifact.evidence_artifact_id
        )
    return canonical_identity_basis_bytes(payload)


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
    if actual.recovery_cell is not expected.recovery_cell:
        raise CellObservationContractError(
            "final cell substituted its completed recovery reference"
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
    if any(
        actual_ref is not expected_ref
        for actual_ref, expected_ref in zip(
            actual.repair_receipts,
            expected.repair_receipts,
            strict=True,
        )
    ):
        raise CellObservationContractError(
            "final cell did not retain exact repair receipt references"
        )
    if any(
        actual_ref is not expected_ref
        for actual_ref, expected_ref in zip(
            actual.repaired_observations,
            expected.repaired_observations,
            strict=True,
        )
    ):
        raise CellObservationContractError(
            "final cell did not retain exact repaired observation references"
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
    "compose_cid_nl_predecessor_cells",
    "mint_cell_observation_ref",
    "validate_final_group_cell_observation_population",
]


def compose_cid_nl_predecessor_cells(
    *,
    processing_owners: ProcessingOwnerReferences,
    groups: tuple[CrossSamplePeakGroupHypothesis, ...],
    sample_assessments: tuple[
        tuple[ResolvedCanonicalSampleIdentity, CidNlMethodAssessmentRef], ...
    ],
    alignment_config: AlignmentConfig,
    sample_universe: frozenset[str] | None = None,
    drift_lookup: DriftLookupProtocol | None = None,
    injection_order: Mapping[str, int] | None = None,
    recovery_assessments_by_group: Mapping[
        tuple[str, str, str],
        tuple[ResolvedCanonicalSampleIdentity, CidNlMethodAssessmentRef],
    ]
    | None = None,
    selected_cells: frozenset[tuple[str, str]] | None = None,
    retained_hypotheses_by_assessment: Mapping[
        tuple[str, str], tuple[PeakHypothesis, ...]
    ]
    | None = None,
) -> tuple[CellObservationRef, ...]:
    """Attach initial evidence and only each group's own recovery assessment."""
    initial_assessment_objects = {
        id(assessment) for _, assessment in sample_assessments
    }
    recoveries = recovery_assessments_by_group or {}
    for (_, sample_id, subject_id), (sample, assessment) in recoveries.items():
        if (
            sample.identity.sample_identity_id != sample_id
            or assessment.content.canonical_sample_identity_id != sample_id
            or assessment.content.method_subject.method_subject_id != subject_id
        ):
            raise CellObservationContractError(
                "group recovery crossed its sample or method subject"
            )
    # Assessment artifacts already own their identity. Hashing the frozen
    # assessment itself recursively visits its entire evidence/curve graph.
    unique_assessments: dict[
        tuple[str, str],
        tuple[ResolvedCanonicalSampleIdentity, CidNlMethodAssessmentRef],
    ] = {}
    for sample, assessment in (*sample_assessments, *recoveries.values()):
        key = (
            sample.identity.sample_identity_id,
            assessment.artifact.evidence_artifact_id,
        )
        unique_assessments.setdefault(key, (sample, assessment))
    sample_assessments = tuple(
        sorted(
            unique_assessments.values(),
            key=lambda pair: pair[0].identity.sample_identity_id,
        )
    )
    retained_hypothesis_ids = frozenset(
        owner_id for group in groups for owner_id in group.owner_ids
    )
    retained_by_assessment = dict(retained_hypotheses_by_assessment or {})
    consumed_retained_keys: set[tuple[str, str]] = set()
    supports_list: list[CidNlCellGroupSupport] = []
    for sample, assessment in sample_assessments:
        retained_key = (
            sample.identity.sample_identity_id,
            assessment.artifact.content_identity.artifact_content_id,
        )
        retained_hypotheses = retained_by_assessment.get(retained_key, ())
        if retained_key in retained_by_assessment:
            consumed_retained_keys.add(retained_key)
        supports_list.extend(
            CidNlCellGroupSupport(
                sample=sample,
                assessment=assessment,
                hypothesis=hypothesis,
            )
            for hypothesis in retained_hypotheses
            if hypothesis.hypothesis_id in retained_hypothesis_ids
        )
    if consumed_retained_keys != set(retained_by_assessment):
        raise CellObservationContractError(
            "retained hypotheses reference an assessment outside the cell input"
        )
    retained_support_ids = {
        support.hypothesis.hypothesis_id for support in supports_list
    }
    missing_owner_ids = retained_hypothesis_ids - retained_support_ids
    if missing_owner_ids:
        missing_member_observations_by_sample: dict[str, set[str]] = {}
        for group in groups:
            for member in group.member_subjects:
                if member.subject_id in missing_owner_ids:
                    missing_member_observations_by_sample.setdefault(
                        member.canonical_sample_identity_id,
                        set(),
                    ).update(member.physical_observation_ids)
        for sample, assessment in sample_assessments:
            sample_id = sample.identity.sample_identity_id
            missing_observation_ids = missing_member_observations_by_sample.get(
                sample_id,
                set(),
            )
            if not missing_observation_ids.intersection(
                candidate.content.peak_observation_content_id
                for candidate in assessment.candidate_peaks
            ):
                continue
            supports_list.extend(
                CidNlCellGroupSupport(
                    sample=sample,
                    assessment=assessment,
                    hypothesis=hypothesis,
                )
                for hypothesis in compose_peak_hypotheses_from_assessment_universe(
                    processing_owners=processing_owners,
                    sample=sample,
                    assessment=assessment,
                )
                if hypothesis.hypothesis_id in missing_owner_ids
            )
    supports = tuple(supports_list)
    supports_by_hypothesis_id = {
        support.hypothesis.hypothesis_id: support for support in supports
    }
    if len(supports_by_hypothesis_id) != len(supports):
        raise CellObservationContractError(
            "group SUPPORT closures contain duplicate hypotheses"
        )
    contexts = tuple(
        CidNlCellGroupContext(
            group=group,
            supports=tuple(
                sorted(
                    (
                        supports_by_hypothesis_id[owner_id]
                        for owner_id in group.owner_ids
                        if owner_id in supports_by_hypothesis_id
                    ),
                    key=lambda support: support.sample.identity.sample_identity_id,
                )
            ),
            config=alignment_config,
            processing_attempt=processing_owners.attempt,
            sample_universe=sample_universe,
            drift_lookup=drift_lookup,
            injection_order=(
                None
                if injection_order is None
                else tuple(sorted(injection_order.items()))
            ),
        )
        for group in groups
    )
    assessments_by_sample_subject: dict[
        tuple[str, str], list[CidNlMethodAssessmentRef]
    ] = {}
    samples = {}
    for sample, assessment in sample_assessments:
        sample_id = sample.identity.sample_identity_id
        samples[sample_id] = sample
        if id(assessment) in initial_assessment_objects:
            assessments_by_sample_subject.setdefault(
                (sample_id, assessment.content.method_subject.method_subject_id), []
            ).append(assessment)
    support_contexts: dict[tuple[str, int], list[CidNlCellGroupContext]] = {}
    for context in contexts:
        for support in context.supports:
            support_contexts.setdefault(
                (
                    support.sample.identity.sample_identity_id,
                    id(support.assessment),
                ),
                [],
            ).append(context)
    cells = []
    for context in contexts:
        method_subject_id = context.method_subject_id
        context_support_by_sample_id = {
            support.sample.identity.sample_identity_id: support
            for support in context.supports
        }
        for sample_id in samples:
            if (
                selected_cells is not None
                and (context.group.group_hypothesis_id, sample_id) not in selected_cells
            ):
                continue
            sample = samples[sample_id]
            local_support = context_support_by_sample_id.get(sample_id)
            local_refs = () if local_support is None else (local_support.assessment,)
            canonical_refs = assessments_by_sample_subject.get(
                (sample_id, method_subject_id), ()
            )
            recovery = recoveries.get(
                (
                    context.group.group_hypothesis_id,
                    sample_id,
                    method_subject_id,
                )
            )
            recovered = recovery[1] if recovery is not None else None
            if local_refs:
                assessment = local_refs[0]
            elif recovered is not None:
                assessment = recovered
            elif canonical_refs:
                assessment = canonical_refs[0]
            else:
                continue
            local = (
                ()
                if local_support is None
                or local_support.sample is not sample
                or local_support.assessment is not assessment
                else (local_support,)
            )
            outside_context = None
            observation = None
            hypothesis = None
            if local:
                (support,) = local
                hypothesis = support.hypothesis
                (observation,) = hypothesis.peak_observation_refs
            elif assessment.content.method_result == "SUPPORT":
                siblings = tuple(
                    sibling
                    for sibling in support_contexts.get((sample_id, id(assessment)), ())
                    if sibling is not context
                )
                if not siblings and recovered is not assessment:
                    raise ValueError(
                        "outside-group cell requires one exact sibling SUPPORT context"
                    )
                outside_context = (
                    min(siblings, key=lambda sibling: sibling.group.group_hypothesis_id)
                    if siblings
                    else None
                )
            elif (
                assessment.content.method_result == "HOLD"
                and len(assessment.candidate_peaks) == 1
            ):
                observation = assessment.candidate_peaks[0]
            cells.append(
                mint_cell_observation_ref(
                    processing_owners=processing_owners,
                    sample=sample,
                    group_context=context,
                    method_assessment=assessment,
                    peak_observation=observation,
                    peak_hypothesis=hypothesis,
                    outside_group_context=outside_context,
                    unassigned_support=(
                        assessment.content.method_result == "SUPPORT"
                        and not local
                        and outside_context is None
                    ),
                )
            )
    return tuple(cells)
