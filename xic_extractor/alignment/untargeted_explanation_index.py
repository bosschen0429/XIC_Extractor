"""Compose bounded Untargeted explanation entries from existing owners.

The future HTML surface is a consumer of this owner.  This module does not
render HTML, read persisted evidence, or recompute scientific decisions.
"""

from __future__ import annotations

from collections.abc import Sequence

from xic_extractor.alignment.artifact_write_receipts import (
    AlignmentArtifactFingerprintBatch,
)
from xic_extractor.alignment.backfill_evidence_contract import (
    BackfillEvidenceRecord,
    EvidenceClaim,
    EvidenceClaimState,
    EvidenceQueryResult,
)
from xic_extractor.alignment.matrix import AlignmentMatrix
from xic_extractor.alignment.matrix_output_contract import (
    MATRIX_ROW_BINDING_COLUMN,
    serialize_public_matrix_table,
)
from xic_extractor.alignment.product_matrix import (
    build_product_matrix_rows,
    formatted_identity_rows,
)
from xic_extractor.alignment.production_decisions import (
    ProductionDecisionSet,
)
from xic_extractor.alignment.untargeted_explanation_contract import (
    _ENTRY_MINT_TOKEN,
    _INDEX_MINT_TOKEN,
    _RUN_BINDING_MINT_TOKEN,
    _SECTION_MINT_TOKEN,
    _SUBJECT_MINT_TOKEN,
    EXPLANATION_INDEX_SCHEMA_VERSION,
    EXPLANATION_INFORMATION_REQUIREMENTS,
    BackfillExplanationInput,
    ExplanationIndexEntry,
    ExplanationLocator,
    ExplanationLocatorKind,
    ExplanationLookupRequest,
    ExplanationRunBinding,
    ExplanationSection,
    ExplanationSubject,
    ExplanationTopic,
    MatrixExplanationPayload,
    UntargetedExplanationIndex,
)
from xic_extractor.processing_identity import (
    FileContentFingerprint,
    ProcessingAttemptIdentity,
)
from xic_extractor.sample_metadata import ResolvedCanonicalSampleIdentity


def build_untargeted_explanation_index(
    *,
    processing_attempt: ProcessingAttemptIdentity,
    matrix: AlignmentMatrix,
    sample_population: Sequence[ResolvedCanonicalSampleIdentity],
    requests: Sequence[ExplanationLookupRequest],
    production_decisions: ProductionDecisionSet,
    backfill_inputs: Sequence[BackfillExplanationInput] = (),
    artifact_fingerprints: AlignmentArtifactFingerprintBatch | None = None,
) -> UntargetedExplanationIndex:
    """Build a bounded index from live owners; no persisted evidence is parsed."""

    if not isinstance(processing_attempt, ProcessingAttemptIdentity):
        raise TypeError("processing_attempt must be a ProcessingAttemptIdentity")
    if not isinstance(matrix, AlignmentMatrix):
        raise TypeError("matrix must be an AlignmentMatrix")
    if not isinstance(production_decisions, ProductionDecisionSet):
        raise TypeError("production_decisions must be a ProductionDecisionSet")
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

    normalized_requests = tuple(requests)
    if any(
        not isinstance(item, ExplanationLookupRequest) for item in normalized_requests
    ):
        raise TypeError("requests must contain ExplanationLookupRequest values")
    request_keys = tuple(item.route_key for item in normalized_requests)
    if len(request_keys) != len(set(request_keys)):
        raise ValueError("explanation lookup requests must be unique")
    sample_by_id = {sample.identity.sample_identity_id: sample for sample in samples}
    for request in normalized_requests:
        if (
            sample_by_id.get(request.sample.identity.sample_identity_id)
            != request.sample
        ):
            raise ValueError(
                "request sample is not bound to the Matrix sample snapshot"
            )

    normalized_backfill = tuple(backfill_inputs)
    if any(
        not isinstance(item, BackfillExplanationInput) for item in normalized_backfill
    ):
        raise TypeError("backfill_inputs must contain BackfillExplanationInput values")
    backfill_by_key = {
        item.route_key: item.query_result for item in normalized_backfill
    }
    if len(backfill_by_key) != len(normalized_backfill):
        raise ValueError("Backfill explanation inputs must use unique routes")
    if not set(backfill_by_key).issubset(set(request_keys)):
        raise ValueError("Backfill explanation input has no requested Matrix route")

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
            peak_hypothesis_id=product_row.peak_hypothesis_id,
            source_feature_family_id=source_ids[0],
            sample=request.sample,
            sample_stem=request.sample.lookup_token,
            _mint_token=_SUBJECT_MINT_TOKEN,
        )
        sample_column = persisted_sample_columns[subject.sample_stem]
        matrix_value = public_row[sample_column]
        sections = (
            _unavailable_section(
                ExplanationTopic.DISCOVERY,
                source_owner="Discovery owner",
                reason=(
                    "no owner-produced Discovery selection explanation was supplied"
                ),
            ),
            _unavailable_section(
                ExplanationTopic.ALIGNMENT,
                source_owner="Alignment grouping owner",
                reason=(
                    "no owner-produced Alignment grouping explanation was supplied"
                ),
            ),
            _backfill_section(
                subject,
                backfill_by_key.get(subject.route_key),
            ),
            _matrix_section(matrix_value, locators=request.locators),
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
        run_binding=_run_binding(processing_attempt, artifact_fingerprints),
        entries=tuple(entries),
        _mint_token=_INDEX_MINT_TOKEN,
    )


def _unavailable_section(
    topic: ExplanationTopic,
    *,
    source_owner: str,
    reason: str,
    supporting_claims: tuple[EvidenceClaim, ...] = (),
) -> ExplanationSection:
    return ExplanationSection(
        topic=topic,
        source_owner=source_owner,
        claim=EvidenceClaim(
            subject=f"{topic.value}_explanation",
            state=EvidenceClaimState.UNAVAILABLE,
            reason=reason,
        ),
        payload=None,
        missing_requirements=EXPLANATION_INFORMATION_REQUIREMENTS[topic],
        supporting_claims=supporting_claims,
        _mint_token=_SECTION_MINT_TOKEN,
    )


def _matrix_section(
    matrix_value: str,
    *,
    locators: tuple[ExplanationLocator, ...],
) -> ExplanationSection:
    return ExplanationSection(
        topic=ExplanationTopic.MATRIX,
        source_owner="xic_extractor.alignment.product_matrix",
        claim=EvidenceClaim(
            subject="matrix_explanation",
            state=EvidenceClaimState.OWNER_REHYDRATED,
            reason=(
                "the live Product Matrix owner produced the exact public cell token "
                "and its bound row identity"
            ),
        ),
        payload=MatrixExplanationPayload(
            written=bool(matrix_value),
            matrix_value=matrix_value or None,
        ),
        missing_requirements=(),
        locators=locators,
        _mint_token=_SECTION_MINT_TOKEN,
    )


def _backfill_section(
    subject: ExplanationSubject,
    query_result: EvidenceQueryResult[BackfillEvidenceRecord] | None,
) -> ExplanationSection:
    if query_result is None:
        return _unavailable_section(
            ExplanationTopic.BACKFILL,
            source_owner="xic_extractor.alignment.backfill_evidence_owner",
            reason="no owner-reader Backfill query result was supplied",
        )
    expected_claim_subjects = (
        "record_content",
        "group_assignment_binding",
        "public_family_binding",
        "cross_artifact_binding",
        "production_decision",
        "full_aligned_cell",
        "missing_key_absence",
    )
    claim_subjects = tuple(claim.subject for claim in query_result.claims)
    if claim_subjects != expected_claim_subjects:
        raise ValueError("Backfill query result has a noncanonical claim census")
    if len(query_result.records) > 1:
        raise ValueError("one explanation route cannot consume multiple Backfill rows")
    if not query_result.records:
        return _unavailable_section(
            ExplanationTopic.BACKFILL,
            source_owner="xic_extractor.alignment.backfill_evidence_owner",
            reason=query_result.claim("missing_key_absence").reason,
            supporting_claims=query_result.claims,
        )
    record = query_result.records[0]
    if record.feature_family_id != subject.source_feature_family_id:
        raise ValueError("Backfill record crosses the Matrix source feature family")
    if record.sample_stem != subject.sample_stem:
        raise ValueError("Backfill record crosses the canonical Matrix sample")
    if query_result.claim("group_assignment_binding").state is not (
        EvidenceClaimState.OWNER_REHYDRATED
    ):
        return _unavailable_section(
            ExplanationTopic.BACKFILL,
            source_owner="xic_extractor.alignment.backfill_evidence_owner",
            reason=(
                "the Backfill group assignment is not owner-rehydrated for this route"
            ),
            supporting_claims=query_result.claims,
        )
    production_claim = query_result.claim("production_decision")
    if production_claim.state is not EvidenceClaimState.UNAVAILABLE:
        raise ValueError(
            "the current #230 contract cannot promote production_decision; "
            "a dedicated owner contract is required"
        )
    reason = production_claim.reason
    if record.group_hypothesis_id != subject.peak_hypothesis_id:
        reason = (
            f"{reason}; the owner result does not bind group_hypothesis_id "
            "to the final Matrix peak_hypothesis_id"
        )
    return ExplanationSection(
        topic=ExplanationTopic.BACKFILL,
        source_owner="xic_extractor.alignment.backfill_evidence_owner",
        claim=EvidenceClaim(
            subject="backfill_explanation",
            state=EvidenceClaimState.UNAVAILABLE,
            reason=reason,
        ),
        payload=None,
        missing_requirements=EXPLANATION_INFORMATION_REQUIREMENTS[
            ExplanationTopic.BACKFILL
        ],
        supporting_claims=query_result.claims,
        _mint_token=_SECTION_MINT_TOKEN,
    )


def _run_binding(
    processing_attempt: ProcessingAttemptIdentity,
    artifact_fingerprints: AlignmentArtifactFingerprintBatch | None,
) -> ExplanationRunBinding:
    matrix_fingerprint: FileContentFingerprint | None = None
    identity_fingerprint: FileContentFingerprint | None = None
    artifact_claim = EvidenceClaim(
        subject="matrix_artifact_bytes",
        state=EvidenceClaimState.UNAVAILABLE,
        reason=(
            "alignment_matrix.tsv and alignment_matrix_identity.tsv writer receipts "
            "were not both supplied"
        ),
    )
    if artifact_fingerprints is not None:
        if not isinstance(artifact_fingerprints, AlignmentArtifactFingerprintBatch):
            raise TypeError(
                "artifact_fingerprints must be an AlignmentArtifactFingerprintBatch"
            )
        receipts_by_name = {
            receipt.artifact_name: receipt for receipt in artifact_fingerprints.receipts
        }
        matrix_receipt = receipts_by_name.get("alignment_matrix.tsv")
        identity_receipt = receipts_by_name.get("alignment_matrix_identity.tsv")
        if matrix_receipt is not None and identity_receipt is not None:
            if (
                matrix_receipt.artifact_path.parent
                != identity_receipt.artifact_path.parent
            ):
                raise ValueError(
                    "Matrix and identity receipts must share one output root"
                )
            matrix_fingerprint = matrix_receipt.fingerprint
            identity_fingerprint = identity_receipt.fingerprint
            artifact_claim = EvidenceClaim(
                subject="matrix_artifact_bytes",
                state=EvidenceClaimState.BYTE_BOUND,
                reason=(
                    "Alignment writer receipts bind the exact Matrix and identity "
                    "sidecar bytes; semantic claims remain with their live owners"
                ),
            )
    return ExplanationRunBinding(
        processing_attempt=processing_attempt,
        attempt_claim=EvidenceClaim(
            subject="processing_attempt_identity",
            state=EvidenceClaimState.OWNER_REHYDRATED,
            reason="the processing identity owner validates the immutable attempt",
        ),
        matrix_artifact_claim=artifact_claim,
        matrix_run_binding_claim=EvidenceClaim(
            subject="matrix_run_binding",
            state=EvidenceClaimState.UNAVAILABLE,
            reason=(
                "writer byte receipts and processing attempt identity do not prove "
                "that the retained Matrix belongs to this attempt or live owner result"
            ),
        ),
        runtime_finalization_claim=EvidenceClaim(
            subject="runtime_finalization",
            state=EvidenceClaimState.UNAVAILABLE,
            reason=(
                "this bounded contract does not accept or reconstruct a runtime "
                "finalization receipt"
            ),
        ),
        matrix_fingerprint=matrix_fingerprint,
        matrix_identity_fingerprint=identity_fingerprint,
        _mint_token=_RUN_BINDING_MINT_TOKEN,
    )


__all__ = (
    "EXPLANATION_INDEX_SCHEMA_VERSION",
    "EXPLANATION_INFORMATION_REQUIREMENTS",
    "BackfillExplanationInput",
    "ExplanationIndexEntry",
    "ExplanationLocator",
    "ExplanationLocatorKind",
    "ExplanationLookupRequest",
    "ExplanationRunBinding",
    "ExplanationSection",
    "ExplanationSubject",
    "ExplanationTopic",
    "MatrixExplanationPayload",
    "UntargetedExplanationIndex",
    "build_untargeted_explanation_index",
)
