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
from xic_extractor.alignment.cell_observations import CellObservationRef
from xic_extractor.alignment.cid_nl_evidence_spine import CidNlReferenceSpine
from xic_extractor.alignment.conservative_backfill import (
    ConservativeBackfillCellDecision,
)
from xic_extractor.alignment.cross_sample_peak_groups import (
    CrossSamplePeakGroupHypothesis,
)
from xic_extractor.alignment.matrix import AlignedCell, AlignmentMatrix
from xic_extractor.alignment.matrix_output_contract import (
    MATRIX_ROW_BINDING_COLUMN,
    serialize_public_matrix_table,
)
from xic_extractor.alignment.product_matrix import (
    build_product_matrix_rows,
    formatted_identity_rows,
)
from xic_extractor.alignment.production_decisions import (
    ProductionCellDecision,
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
    AlignmentExplanationPayload,
    BackfillExplanationInput,
    BackfillExplanationPayload,
    DiscoveryExplanationPayload,
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
    cid_nl_spines: Sequence[CidNlReferenceSpine] = (),
    backfill_decisions: Sequence[ConservativeBackfillCellDecision] = (),
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

    normalized_spines = tuple(cid_nl_spines)
    if any(not isinstance(item, CidNlReferenceSpine) for item in normalized_spines):
        raise TypeError("cid_nl_spines must contain CidNlReferenceSpine values")
    spine_by_source_feature: dict[str, CidNlReferenceSpine] = {}
    for candidate_spine in normalized_spines:
        if candidate_spine.group is None:
            raise ValueError("an explanation spine must retain its Alignment group")
        if not any(
            candidate_spine.group is cluster for cluster in matrix.clusters
        ):
            raise ValueError(
                "CID-NL explanation spine does not reuse an exact live Matrix group"
            )
        previous_spine = spine_by_source_feature.setdefault(
            candidate_spine.group.feature_family_id,
            candidate_spine,
        )
        if previous_spine is not candidate_spine:
            raise ValueError("CID-NL explanation spines must use unique groups")

    normalized_decisions = tuple(backfill_decisions)
    if any(
        not isinstance(item, ConservativeBackfillCellDecision)
        for item in normalized_decisions
    ):
        raise TypeError(
            "backfill_decisions must contain ConservativeBackfillCellDecision values"
        )
    backfill_decision_by_key: dict[
        tuple[str, str], ConservativeBackfillCellDecision
    ] = {}
    for decision in normalized_decisions:
        group = decision.group_facts.group_context.group
        bound_spine = spine_by_source_feature.get(group.feature_family_id)
        if bound_spine is None or bound_spine.group is not group:
            raise ValueError(
                "Backfill explanation decision has no exact supplied CID-NL spine"
            )
        key = (
            group.feature_family_id,
            decision.target_cell.content.canonical_sample_identity_id,
        )
        previous_decision = backfill_decision_by_key.setdefault(key, decision)
        if previous_decision is not decision:
            raise ValueError("Backfill explanation decisions must use unique routes")

    matrix_cells_by_key: dict[tuple[str, str], AlignedCell] = {}
    for cell in matrix.cells:
        key = (cell.cluster_id, cell.sample_stem)
        if key in matrix_cells_by_key:
            raise ValueError("live Matrix contains duplicate feature/sample cells")
        matrix_cells_by_key[key] = cell

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
        matrix_value = public_row[sample_column]
        route_spine = spine_by_source_feature.get(subject.source_feature_family_id)
        spine_cell = _spine_cell_for_subject(route_spine, subject)
        matrix_cell = matrix_cells_by_key.get(
            (subject.source_feature_family_id, subject.sample_stem)
        )
        production_decision = production_decisions.cells.get(
            (subject.source_feature_family_id, subject.sample_stem)
        )
        sections = (
            _discovery_section(
                subject,
                spine_cell=spine_cell,
                matrix_cell=matrix_cell,
            ),
            _alignment_section(
                subject,
                spine=route_spine,
                spine_cell=spine_cell,
            ),
            _backfill_section(
                subject,
                query_result=backfill_by_key.get(subject.route_key),
                spine=route_spine,
                owner_decision=backfill_decision_by_key.get(
                    (
                        subject.source_feature_family_id,
                        subject.sample.identity.sample_identity_id,
                    )
                ),
                production_decision=production_decision,
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


def _spine_cell_for_subject(
    spine: CidNlReferenceSpine | None,
    subject: ExplanationSubject,
) -> CellObservationRef | None:
    if spine is None:
        return None
    group = spine.group
    if group is None or group.feature_family_id != subject.source_feature_family_id:
        raise ValueError("CID-NL spine crosses the Matrix source feature identity")
    if subject.matrix_row_identity_basis != "no_split_peak_hypothesis":
        return None
    if group.group_hypothesis_id != subject.peak_hypothesis_id:
        raise ValueError("CID-NL spine crosses the final Matrix hypothesis identity")
    selected = tuple(
        cell
        for cell in spine.cells
        if cell.content.canonical_sample_identity_id
        == subject.sample.identity.sample_identity_id
    )
    if len(selected) != 1:
        raise ValueError(
            "CID-NL spine must contain exactly one cell for the requested sample"
        )
    cell = selected[0]
    if cell.group_context.processing_attempt != subject.processing_attempt:
        raise ValueError("CID-NL spine belongs to a different processing attempt")
    return cell


def _discovery_section(
    subject: ExplanationSubject,
    *,
    spine_cell: CellObservationRef | None,
    matrix_cell: AlignedCell | None,
) -> ExplanationSection:
    if spine_cell is None:
        return _unavailable_section(
            ExplanationTopic.DISCOVERY,
            source_owner="xic_extractor.peak_detection.cid_nl_method_assessment",
            reason="no owner-produced CID-NL reference spine was supplied",
        )
    assessment = spine_cell.method_assessment
    if assessment.artifact.producer_processing_attempt_id != (
        subject.processing_attempt.processing_attempt_id
    ):
        raise ValueError("Discovery assessment belongs to a different attempt")
    if assessment.content.canonical_sample_identity_id != (
        subject.sample.identity.sample_identity_id
    ):
        raise ValueError("Discovery assessment crosses the canonical Matrix sample")
    selected_hypothesis_id = spine_cell.content.peak_hypothesis_id
    if selected_hypothesis_id is not None:
        if matrix_cell is None:
            raise ValueError("Discovery-selected hypothesis has no live Matrix cell")
        if matrix_cell.source_candidate_id != selected_hypothesis_id:
            raise ValueError(
                "Discovery-selected hypothesis crosses the live Matrix source cell"
            )
    outcome = assessment.content.method_result or assessment.content.technical_state
    return ExplanationSection(
        topic=ExplanationTopic.DISCOVERY,
        source_owner="xic_extractor.peak_detection.cid_nl_method_assessment",
        claim=EvidenceClaim(
            subject="discovery_explanation",
            state=EvidenceClaimState.OWNER_REHYDRATED,
            reason=(
                "the CID-NL assessment owner retains the complete candidate peak "
                "universe and the exact sample-local hypothesis, when selected"
            ),
        ),
        payload=DiscoveryExplanationPayload(
            candidate_universe_artifact_id=(
                assessment.artifact.evidence_artifact_id
            ),
            candidate_peak_content_ids=(
                assessment.content.candidate_peak_content_ids
            ),
            selected_peak_hypothesis_id=selected_hypothesis_id,
            selection_outcome=outcome,
            selection_reasons=assessment.content.reason_codes,
        ),
        missing_requirements=(),
        _mint_token=_SECTION_MINT_TOKEN,
    )


def _alignment_section(
    subject: ExplanationSubject,
    *,
    spine: CidNlReferenceSpine | None,
    spine_cell: CellObservationRef | None,
) -> ExplanationSection:
    if spine is None or spine.group is None or spine_cell is None:
        return _unavailable_section(
            ExplanationTopic.ALIGNMENT,
            source_owner="xic_extractor.alignment.cross_sample_peak_groups",
            reason="no owner-produced CID-NL reference spine was supplied",
        )
    group = spine.group
    if group.feature_family_id != subject.source_feature_family_id:
        raise ValueError("Alignment group crosses the Matrix source feature")
    if group.group_hypothesis_id != subject.peak_hypothesis_id:
        raise ValueError("Alignment group crosses the final Matrix hypothesis")
    disposition = _alignment_disposition(group)
    representative = (
        group.consolidation_winner_group_hypothesis_id
        or group.group_hypothesis_id
    )
    owner_reasons = tuple(
        owner.assignment_reason
        for owner in group.owners
        if owner.sample_stem == subject.sample_stem
    )
    reasons = tuple(
        sorted(
            {
                value
                for value in (group.evidence, *owner_reasons)
                if value
            }
        )
    )
    if not reasons:
        reasons = ("owner_group_membership_closed",)
    return ExplanationSection(
        topic=ExplanationTopic.ALIGNMENT,
        source_owner="xic_extractor.alignment.cross_sample_peak_groups",
        claim=EvidenceClaim(
            subject="alignment_explanation",
            state=EvidenceClaimState.OWNER_REHYDRATED,
            reason=(
                "the exact live Matrix group is the group retained by the "
                "owner-validated CID-NL reference spine"
            ),
        ),
        payload=AlignmentExplanationPayload(
            group_hypothesis_id=group.group_hypothesis_id,
            member_peak_hypothesis_ids=tuple(sorted(group.owner_ids)),
            sample_member_peak_hypothesis_id=(
                spine_cell.content.peak_hypothesis_id
            ),
            grouping_disposition=disposition,
            representative_group_hypothesis_id=representative,
            decision_reasons=reasons,
        ),
        missing_requirements=(),
        _mint_token=_SECTION_MINT_TOKEN,
    )


def _alignment_disposition(group: CrossSamplePeakGroupHypothesis) -> str:
    if group.review_only:
        return "review_only"
    if group.consolidation_state and group.consolidation_state != "not_consolidated":
        return group.consolidation_state
    return "grouped"


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
    *,
    query_result: EvidenceQueryResult[BackfillEvidenceRecord] | None,
    spine: CidNlReferenceSpine | None,
    owner_decision: ConservativeBackfillCellDecision | None,
    production_decision: ProductionCellDecision | None,
) -> ExplanationSection:
    if query_result is None:
        return _unavailable_section(
            ExplanationTopic.BACKFILL,
            source_owner="xic_extractor.alignment.backfill_evidence_owner",
            reason="no owner-reader Backfill query result was supplied",
        )
    _validate_backfill_query_result(query_result)
    if query_result.records:
        record = query_result.records[0]
        expected_group_hypothesis_id = (
            spine.group.group_hypothesis_id
            if spine is not None and spine.group is not None
            else None
        )
        _validate_backfill_record_route(
            record,
            subject,
            expected_group_hypothesis_id=expected_group_hypothesis_id,
        )
        if not record.group_hypothesis_id:
            return _unavailable_section(
                ExplanationTopic.BACKFILL,
                source_owner="xic_extractor.alignment.backfill_evidence_owner",
                reason="the #230 record omits group_hypothesis_id",
                supporting_claims=query_result.claims,
            )
        if query_result.claim("group_assignment_binding").state is not (
            EvidenceClaimState.OWNER_REHYDRATED
        ):
            return _unavailable_section(
                ExplanationTopic.BACKFILL,
                source_owner="xic_extractor.alignment.backfill_evidence_owner",
                reason=(
                    "the Backfill group assignment is not owner-rehydrated for "
                    "this route"
                ),
                supporting_claims=query_result.claims,
            )
    else:
        return _unavailable_section(
            ExplanationTopic.BACKFILL,
            source_owner="xic_extractor.alignment.backfill_evidence_owner",
            reason=query_result.claim("missing_key_absence").reason,
            supporting_claims=query_result.claims,
        )
    if subject.matrix_row_identity_basis != "no_split_peak_hypothesis":
        return _unavailable_section(
            ExplanationTopic.BACKFILL,
            source_owner="xic_extractor.alignment.conservative_backfill",
            reason=(
                "the supplied owners do not bind this split final Matrix "
                "hypothesis to one Backfill cell"
            ),
            supporting_claims=query_result.claims,
        )
    if owner_decision is None:
        return _unavailable_section(
            ExplanationTopic.BACKFILL,
            source_owner="xic_extractor.alignment.conservative_backfill",
            reason=(
                "no complete live Backfill owner decision was supplied; the #230 "
                "query cannot replace the complete ProductionDecisionSet/current "
                "owner result"
            ),
            supporting_claims=query_result.claims,
        )
    if production_decision is None:
        return _unavailable_section(
            ExplanationTopic.BACKFILL,
            source_owner="xic_extractor.alignment.production_decisions",
            reason="the live Matrix route has no ProductionCellDecision",
            supporting_claims=query_result.claims,
        )
    record = query_result.records[0]
    production_claim = query_result.claim("production_decision")
    if production_claim.state is not EvidenceClaimState.UNAVAILABLE:
        raise ValueError(
            "the current #230 contract cannot promote production_decision; "
            "a dedicated owner contract is required"
        )

    target = owner_decision.target_cell
    group = owner_decision.group_facts.group_context.group
    if group.feature_family_id != subject.source_feature_family_id:
        raise ValueError("Backfill owner decision crosses the Matrix source feature")
    if group.group_hypothesis_id != subject.peak_hypothesis_id:
        raise ValueError("Backfill owner decision crosses the final Matrix hypothesis")
    if target.content.canonical_sample_identity_id != (
        subject.sample.identity.sample_identity_id
    ):
        raise ValueError("Backfill owner decision crosses the canonical sample")
    if target.group_context.processing_attempt != subject.processing_attempt:
        raise ValueError("Backfill owner decision belongs to a different attempt")
    if (
        production_decision.feature_family_id != subject.source_feature_family_id
        or production_decision.sample_stem != subject.sample_stem
    ):
        raise ValueError("ProductionCellDecision crosses the Matrix route")

    matched_content_id = None
    if owner_decision.matched_candidate is not None:
        matched_content_id = (
            owner_decision.matched_candidate.peak_observation.content
        ).peak_observation_content_id
    reasons = tuple(
        sorted(
            {
                *owner_decision.reason_codes,
                *(
                    (production_decision.blank_reason,)
                    if production_decision.blank_reason
                    else ()
                ),
            }
        )
    )
    return ExplanationSection(
        topic=ExplanationTopic.BACKFILL,
        source_owner="xic_extractor.alignment.conservative_backfill",
        claim=EvidenceClaim(
            subject="backfill_explanation",
            state=EvidenceClaimState.OWNER_REHYDRATED,
            reason=(
                "the live Backfill owner and ProductionDecisionSet close the exact "
                "cell; #230 claims retain their reduced persisted-record ceiling"
            ),
        ),
        payload=BackfillExplanationPayload(
            evaluated=True,
            disposition=_backfill_disposition(
                owner_decision,
                production_decision,
            ),
            production_status=production_decision.production_status,
            write_matrix_value=production_decision.write_matrix_value,
            policy_version=owner_decision.policy_version,
            matched_candidate_peak_content_id=matched_content_id,
            decision_reasons=reasons,
        ),
        missing_requirements=(),
        supporting_claims=query_result.claims,
        _mint_token=_SECTION_MINT_TOKEN,
    )


def _validate_backfill_query_result(
    query_result: EvidenceQueryResult[BackfillEvidenceRecord],
) -> None:
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
    if query_result.claim("production_decision").state is not (
        EvidenceClaimState.UNAVAILABLE
    ):
        raise ValueError(
            "the current #230 contract cannot promote production_decision; "
            "a dedicated owner contract is required"
        )


def _validate_backfill_record_route(
    record: BackfillEvidenceRecord,
    subject: ExplanationSubject,
    *,
    expected_group_hypothesis_id: str | None,
) -> None:
    if record.feature_family_id != subject.source_feature_family_id:
        raise ValueError("Backfill record crosses the Matrix source feature family")
    if record.sample_stem != subject.sample_stem:
        raise ValueError("Backfill record crosses the canonical Matrix sample")
    if (
        record.group_hypothesis_id
        and expected_group_hypothesis_id is not None
        and record.group_hypothesis_id != expected_group_hypothesis_id
    ):
        raise ValueError("Backfill record crosses the Matrix source group")


def _backfill_disposition(
    owner_decision: ConservativeBackfillCellDecision,
    production_decision: ProductionCellDecision,
) -> str:
    if owner_decision.disposition == "candidate_mapped":
        return "written" if production_decision.write_matrix_value else "unresolved"
    if owner_decision.disposition == "hold":
        return "withheld"
    return "not_applicable"


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
    "AlignmentExplanationPayload",
    "BackfillExplanationPayload",
    "BackfillExplanationInput",
    "DiscoveryExplanationPayload",
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
