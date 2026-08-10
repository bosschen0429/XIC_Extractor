"""Compose bounded Untargeted explanation entries from existing live owners.

The HTML surface consumes this owner. This module orchestrates typed owner
projections; it does not render HTML, read persisted evidence, or recompute
scientific decisions.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import final

from xic_extractor.alignment.artifact_write_receipts import (
    AlignmentArtifactFingerprintBatch,
)
from xic_extractor.alignment.backfill_evidence_contract import (
    BackfillEvidenceRecord,
)
from xic_extractor.alignment.cid_nl_evidence_spine import CidNlReferenceSpine
from xic_extractor.alignment.config import AlignmentConfig
from xic_extractor.alignment.conservative_backfill import (
    ConservativeBackfillCellDecision,
    ConservativeBackfillGroupFacts,
)
from xic_extractor.alignment.evidence_claims import EvidenceQueryResult
from xic_extractor.alignment.matrix import AlignedCell, AlignmentMatrix
from xic_extractor.alignment.matrix_output_contract import (
    MATRIX_ROW_BINDING_COLUMN,
    serialize_public_matrix_table,
)
from xic_extractor.alignment.product_matrix import (
    build_product_matrix_rows,
    formatted_identity_rows,
)
from xic_extractor.alignment.production_decisions import build_production_decisions
from xic_extractor.alignment.untargeted_explanation_contract import (
    _ENTRY_MINT_TOKEN,
    _INDEX_MINT_TOKEN,
    _SUBJECT_MINT_TOKEN,
    EXPLANATION_INDEX_SCHEMA_VERSION,
    EXPLANATION_INFORMATION_REQUIREMENTS,
    EXPLANATION_ROUTE_LIMIT,
    AlignmentExplanationPayload,
    BackfillExplanationPayload,
    DiscoveryExplanationPayload,
    ExplanationIndexEntry,
    ExplanationLocator,
    ExplanationLocatorKind,
    ExplanationLocatorRequest,
    ExplanationLookupRequest,
    ExplanationRunBinding,
    ExplanationSection,
    ExplanationSubject,
    ExplanationTopic,
    MatrixExplanationPayload,
    UntargetedExplanationIndex,
)
from xic_extractor.alignment.untargeted_explanation_run_binding import (
    bind_explanation_locators,
    build_explanation_run_binding,
)
from xic_extractor.alignment.untargeted_explanation_sections import (
    build_explanation_sections,
)
from xic_extractor.processing_identity import ProcessingAttemptIdentity
from xic_extractor.sample_metadata import ResolvedCanonicalSampleIdentity


@final
@dataclass(frozen=True, slots=True)
class BackfillExplanationInput:
    """One #230 owner-reader result bound to a requested explanation route."""

    route_key: tuple[str, str]
    query_result: EvidenceQueryResult[BackfillEvidenceRecord]

    def __init_subclass__(cls, **kwargs: object) -> None:
        raise TypeError("BackfillExplanationInput is final")

    def __post_init__(self) -> None:
        if (
            not isinstance(self.route_key, tuple)
            or len(self.route_key) != 2
            or any(not isinstance(value, str) or not value for value in self.route_key)
        ):
            raise ValueError("Backfill explanation route_key must contain two IDs")
        if not isinstance(self.query_result, EvidenceQueryResult):
            raise TypeError("query_result must be an EvidenceQueryResult")


def build_untargeted_explanation_index(
    *,
    processing_attempt: ProcessingAttemptIdentity,
    matrix: AlignmentMatrix,
    alignment_config: AlignmentConfig,
    sample_population: Sequence[ResolvedCanonicalSampleIdentity],
    requests: Sequence[ExplanationLookupRequest],
    cid_nl_spines: Sequence[CidNlReferenceSpine] = (),
    backfill_group_facts: Sequence[ConservativeBackfillGroupFacts] = (),
    backfill_decisions: Sequence[ConservativeBackfillCellDecision] = (),
    backfill_inputs: Sequence[BackfillExplanationInput] = (),
    artifact_fingerprints: AlignmentArtifactFingerprintBatch | None = None,
) -> UntargetedExplanationIndex:
    """Build a bounded index from live owners; no persisted evidence is parsed."""

    if not isinstance(processing_attempt, ProcessingAttemptIdentity):
        raise TypeError("processing_attempt must be a ProcessingAttemptIdentity")
    if not isinstance(matrix, AlignmentMatrix):
        raise TypeError("matrix must be an AlignmentMatrix")
    if not isinstance(alignment_config, AlignmentConfig):
        raise TypeError("alignment_config must be an AlignmentConfig")
    samples = _validate_samples(sample_population, matrix)
    normalized_requests = _validate_requests(requests, samples)
    production_decisions = build_production_decisions(matrix, alignment_config)
    request_keys = tuple(item.route_key for item in normalized_requests)
    backfill_by_key = _index_backfill_inputs(backfill_inputs, request_keys)
    spine_by_source_feature = _index_spines(
        cid_nl_spines,
        matrix=matrix,
        processing_attempt=processing_attempt,
    )
    group_facts_by_source_feature = _index_group_facts(
        backfill_group_facts,
        spine_by_source_feature=spine_by_source_feature,
        processing_attempt=processing_attempt,
    )
    backfill_decision_by_key = _index_backfill_decisions(
        backfill_decisions,
        group_facts_by_source_feature=group_facts_by_source_feature,
    )
    matrix_cells_by_key = _index_matrix_cells(matrix)

    product_rows = build_product_matrix_rows(
        matrix,
        production_decisions=production_decisions,
    )
    identity_rows = formatted_identity_rows(
        matrix,
        production_decisions=production_decisions,
    )
    public_table = serialize_public_matrix_table(
        ({"Mz": row.mz, "RT": row.rt, **row.sample_values} for row in product_rows),
        sample_columns=matrix.sample_order,
    )
    if len(product_rows) != len(identity_rows) or len(product_rows) != len(
        public_table.rows
    ):
        raise ValueError("Matrix owner projections disagree on row cardinality")
    persisted_sample_columns = dict(
        zip(matrix.sample_order, public_table.header[2:], strict=True)
    )
    matrix_sample_indices = {
        sample_stem: index
        for index, sample_stem in enumerate(matrix.sample_order, start=1)
    }
    row_by_peak_id = {
        row.peak_hypothesis_id: (row, identity_rows[index], public_table.rows[index])
        for index, row in enumerate(product_rows)
    }
    if len(row_by_peak_id) != len(product_rows):
        raise ValueError("Matrix owner produced duplicate peak_hypothesis_id values")

    entries: list[ExplanationIndexEntry] = []
    for request in normalized_requests:
        owner_row = row_by_peak_id.get(request.peak_hypothesis_id)
        if owner_row is None:
            raise ValueError(
                f"unknown Matrix peak_hypothesis_id {request.peak_hypothesis_id!r}"
            )
        product_row, identity_row, public_row = owner_row
        if identity_row["peak_hypothesis_id"] != product_row.peak_hypothesis_id:
            raise ValueError("Matrix identity row does not bind its owner row")
        source_ids = tuple(
            part
            for part in identity_row["source_feature_family_ids"].split(";")
            if part
        )
        if len(source_ids) != 1:
            raise ValueError(
                "Matrix explanation route requires exactly one source feature family"
            )
        subject = ExplanationSubject(
            processing_attempt=processing_attempt,
            matrix_row_index=product_row.matrix_row_index,
            matrix_sample_index=matrix_sample_indices[request.sample.lookup_token],
            matrix_row_binding_sha256=identity_row[MATRIX_ROW_BINDING_COLUMN],
            matrix_row_identity_basis=identity_row["row_identity_basis"],
            peak_hypothesis_id=product_row.peak_hypothesis_id,
            source_feature_family_id=source_ids[0],
            sample=request.sample,
            sample_stem=request.sample.lookup_token,
            _mint_token=_SUBJECT_MINT_TOKEN,
        )
        sample_column = persisted_sample_columns[subject.sample_stem]
        route_spine = spine_by_source_feature.get(subject.source_feature_family_id)
        production_decision = production_decisions.cells.get(
            (subject.source_feature_family_id, subject.sample_stem)
        )
        sections = build_explanation_sections(
            subject,
            spine=route_spine,
            matrix_cell=matrix_cells_by_key.get(
                (subject.source_feature_family_id, subject.sample_stem)
            ),
            matrix_value=public_row[sample_column],
            matrix_row_mz=public_row["Mz"],
            matrix_row_rt=public_row["RT"],
            query_result=backfill_by_key.get(subject.route_key),
            owner_group_facts=group_facts_by_source_feature.get(
                subject.source_feature_family_id
            ),
            owner_decision=backfill_decision_by_key.get(
                (
                    subject.source_feature_family_id,
                    subject.sample.identity.sample_identity_id,
                )
            ),
            production_decision=production_decision,
            locators_by_topic=bind_explanation_locators(subject, request.locators),
        )
        entries.append(
            ExplanationIndexEntry(
                subject=subject,
                sections=sections,
                _mint_token=_ENTRY_MINT_TOKEN,
            )
        )

    entries.sort(
        key=lambda item: (
            item.subject.matrix_row_index,
            item.subject.matrix_sample_index,
        )
    )
    return UntargetedExplanationIndex(
        schema_version=EXPLANATION_INDEX_SCHEMA_VERSION,
        run_binding=build_explanation_run_binding(
            processing_attempt,
            artifact_fingerprints,
        ),
        entries=tuple(entries),
        _mint_token=_INDEX_MINT_TOKEN,
    )


def _validate_samples(
    sample_population: Sequence[ResolvedCanonicalSampleIdentity],
    matrix: AlignmentMatrix,
) -> tuple[ResolvedCanonicalSampleIdentity, ...]:
    samples = tuple(sample_population)
    if any(not isinstance(item, ResolvedCanonicalSampleIdentity) for item in samples):
        raise TypeError("sample_population must contain resolved sample identities")
    if tuple(sample.lookup_token for sample in samples) != matrix.sample_order:
        raise ValueError(
            "sample_population must exactly follow the live Matrix sample order"
        )
    sample_ids = tuple(sample.identity.sample_identity_id for sample in samples)
    if len(sample_ids) != len(set(sample_ids)):
        raise ValueError("sample_population canonical identities must be unique")
    return samples


def _validate_requests(
    requests: Sequence[ExplanationLookupRequest],
    samples: tuple[ResolvedCanonicalSampleIdentity, ...],
) -> tuple[ExplanationLookupRequest, ...]:
    normalized = tuple(requests)
    if any(not isinstance(item, ExplanationLookupRequest) for item in normalized):
        raise TypeError("requests must contain ExplanationLookupRequest values")
    keys = tuple(item.route_key for item in normalized)
    if len(keys) != len(set(keys)):
        raise ValueError("explanation lookup requests must be unique")
    if len(normalized) > EXPLANATION_ROUTE_LIMIT:
        raise ValueError(
            f"explanation index accepts at most {EXPLANATION_ROUTE_LIMIT} "
            "requested routes"
        )
    sample_by_id = {sample.identity.sample_identity_id: sample for sample in samples}
    for request in normalized:
        if (
            sample_by_id.get(request.sample.identity.sample_identity_id)
            != request.sample
        ):
            raise ValueError(
                "request sample is not bound to the Matrix sample snapshot"
            )
    return normalized


def _index_backfill_inputs(
    inputs: Sequence[BackfillExplanationInput],
    request_keys: tuple[tuple[str, str], ...],
) -> dict[tuple[str, str], EvidenceQueryResult[BackfillEvidenceRecord]]:
    normalized = tuple(inputs)
    if any(not isinstance(item, BackfillExplanationInput) for item in normalized):
        raise TypeError("backfill_inputs must contain BackfillExplanationInput values")
    by_key = {item.route_key: item.query_result for item in normalized}
    if len(by_key) != len(normalized):
        raise ValueError("Backfill explanation inputs must use unique routes")
    if not set(by_key).issubset(set(request_keys)):
        raise ValueError("Backfill explanation input has no requested Matrix route")
    return by_key


def _index_spines(
    spines: Sequence[CidNlReferenceSpine],
    *,
    matrix: AlignmentMatrix,
    processing_attempt: ProcessingAttemptIdentity,
) -> dict[str, CidNlReferenceSpine]:
    normalized = tuple(spines)
    if any(not isinstance(item, CidNlReferenceSpine) for item in normalized):
        raise TypeError("cid_nl_spines must contain CidNlReferenceSpine values")
    by_source_feature: dict[str, CidNlReferenceSpine] = {}
    for spine in normalized:
        if spine.group is None:
            raise ValueError("an explanation spine must retain its Alignment group")
        if not any(spine.group is cluster for cluster in matrix.clusters):
            raise ValueError(
                "CID-NL explanation spine does not reuse an exact live Matrix group"
            )
        if spine.group.feature_family_id in by_source_feature:
            raise ValueError("CID-NL explanation spines must use unique groups")
        if any(
            cell.group_context.processing_attempt != processing_attempt
            for cell in spine.cells
        ):
            raise ValueError("CID-NL explanation spine belongs to a different attempt")
        by_source_feature[spine.group.feature_family_id] = spine
    return by_source_feature


def _index_group_facts(
    facts: Sequence[ConservativeBackfillGroupFacts],
    *,
    spine_by_source_feature: dict[str, CidNlReferenceSpine],
    processing_attempt: ProcessingAttemptIdentity,
) -> dict[str, ConservativeBackfillGroupFacts]:
    normalized = tuple(facts)
    if any(not isinstance(item, ConservativeBackfillGroupFacts) for item in normalized):
        raise TypeError(
            "backfill_group_facts must contain ConservativeBackfillGroupFacts values"
        )
    by_source_feature: dict[str, ConservativeBackfillGroupFacts] = {}
    for item in normalized:
        group = item.group_context.group
        spine = spine_by_source_feature.get(group.feature_family_id)
        if spine is None or spine.group is not group:
            raise ValueError(
                "Backfill group facts have no exact supplied CID-NL/Matrix group"
            )
        if item.group_context.processing_attempt != processing_attempt:
            raise ValueError("Backfill group facts belong to a different attempt")
        if group.feature_family_id in by_source_feature:
            raise ValueError("Backfill group facts must use unique groups")
        by_source_feature[group.feature_family_id] = item
    return by_source_feature


def _index_backfill_decisions(
    decisions: Sequence[ConservativeBackfillCellDecision],
    *,
    group_facts_by_source_feature: dict[str, ConservativeBackfillGroupFacts],
) -> dict[tuple[str, str], ConservativeBackfillCellDecision]:
    normalized = tuple(decisions)
    if any(
        not isinstance(item, ConservativeBackfillCellDecision) for item in normalized
    ):
        raise TypeError(
            "backfill_decisions must contain ConservativeBackfillCellDecision values"
        )
    by_key: dict[tuple[str, str], ConservativeBackfillCellDecision] = {}
    for decision in normalized:
        group = decision.group_facts.group_context.group
        if group_facts_by_source_feature.get(group.feature_family_id) is not (
            decision.group_facts
        ):
            raise ValueError(
                "Backfill decision must reuse exact supplied owner group facts"
            )
        key = (
            group.feature_family_id,
            decision.target_cell.content.canonical_sample_identity_id,
        )
        if key in by_key:
            raise ValueError("Backfill explanation decisions must use unique routes")
        by_key[key] = decision
    return by_key


def _index_matrix_cells(matrix: AlignmentMatrix) -> dict[tuple[str, str], AlignedCell]:
    by_key: dict[tuple[str, str], AlignedCell] = {}
    for cell in matrix.cells:
        key = (cell.cluster_id, cell.sample_stem)
        if key in by_key:
            raise ValueError("live Matrix contains duplicate feature/sample cells")
        by_key[key] = cell
    return by_key


__all__ = (
    "EXPLANATION_INDEX_SCHEMA_VERSION",
    "EXPLANATION_INFORMATION_REQUIREMENTS",
    "EXPLANATION_ROUTE_LIMIT",
    "AlignmentExplanationPayload",
    "BackfillExplanationPayload",
    "BackfillExplanationInput",
    "DiscoveryExplanationPayload",
    "ExplanationIndexEntry",
    "ExplanationLocator",
    "ExplanationLocatorKind",
    "ExplanationLocatorRequest",
    "ExplanationLookupRequest",
    "ExplanationRunBinding",
    "ExplanationSection",
    "ExplanationSubject",
    "ExplanationTopic",
    "MatrixExplanationPayload",
    "UntargetedExplanationIndex",
    "build_untargeted_explanation_index",
)
