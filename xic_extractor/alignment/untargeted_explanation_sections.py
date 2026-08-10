"""Project live scientific owners into bounded explanation sections."""

from __future__ import annotations

from collections.abc import Mapping

from xic_extractor.alignment.backfill_evidence_contract import (
    BackfillEvidenceRecord,
)
from xic_extractor.alignment.cell_observations import CellObservationRef
from xic_extractor.alignment.cid_nl_evidence_spine import CidNlReferenceSpine
from xic_extractor.alignment.conservative_backfill import (
    ConservativeBackfillCellDecision,
    ConservativeBackfillGroupFacts,
)
from xic_extractor.alignment.cross_sample_peak_groups import (
    CrossSamplePeakGroupHypothesis,
)
from xic_extractor.alignment.evidence_claims import (
    EvidenceClaim,
    EvidenceClaimState,
    EvidenceQueryResult,
)
from xic_extractor.alignment.matrix import AlignedCell
from xic_extractor.alignment.production_decisions import ProductionCellDecision
from xic_extractor.alignment.untargeted_explanation_contract import (
    _SECTION_MINT_TOKEN,
    EXPLANATION_INFORMATION_REQUIREMENTS,
    AlignmentExplanationPayload,
    BackfillExplanationPayload,
    DiscoveryExplanationPayload,
    ExplanationLocator,
    ExplanationSection,
    ExplanationSubject,
    ExplanationTopic,
    MatrixExplanationPayload,
)


def build_explanation_sections(
    subject: ExplanationSubject,
    *,
    spine: CidNlReferenceSpine | None,
    matrix_cell: AlignedCell | None,
    matrix_value: str,
    matrix_row_mz: str,
    matrix_row_rt: str,
    query_result: EvidenceQueryResult[BackfillEvidenceRecord] | None,
    owner_group_facts: ConservativeBackfillGroupFacts | None,
    owner_decision: ConservativeBackfillCellDecision | None,
    production_decision: ProductionCellDecision | None,
    locators_by_topic: Mapping[ExplanationTopic, tuple[ExplanationLocator, ...]],
) -> tuple[ExplanationSection, ...]:
    """Build the canonical four-topic projection for one exact Matrix route."""

    spine_cell = _spine_cell_for_subject(spine, subject)
    return (
        _discovery_section(
            subject,
            spine_cell=spine_cell,
            matrix_cell=matrix_cell,
            locators=locators_by_topic[ExplanationTopic.DISCOVERY],
        ),
        _alignment_section(
            subject,
            spine=spine,
            spine_cell=spine_cell,
            locators=locators_by_topic[ExplanationTopic.ALIGNMENT],
        ),
        _backfill_section(
            subject,
            matrix_cell=matrix_cell,
            query_result=query_result,
            spine=spine,
            owner_group_facts=owner_group_facts,
            owner_decision=owner_decision,
            production_decision=production_decision,
            locators=locators_by_topic[ExplanationTopic.BACKFILL],
        ),
        _matrix_section(
            subject,
            matrix_value=matrix_value,
            matrix_row_mz=matrix_row_mz,
            matrix_row_rt=matrix_row_rt,
            locators=locators_by_topic[ExplanationTopic.MATRIX],
        ),
    )


def _unavailable_section(
    topic: ExplanationTopic,
    *,
    source_owner: str,
    reason: str,
    supporting_claims: tuple[EvidenceClaim, ...] = (),
    locators: tuple[ExplanationLocator, ...] = (),
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
        locators=locators,
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
    locators: tuple[ExplanationLocator, ...],
) -> ExplanationSection:
    if spine_cell is None:
        return _unavailable_section(
            ExplanationTopic.DISCOVERY,
            source_owner="xic_extractor.peak_detection.cid_nl_method_assessment",
            reason="no owner-produced CID-NL reference spine was supplied",
            locators=locators,
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
            candidate_universe_artifact_id=assessment.artifact.evidence_artifact_id,
            candidate_peak_content_ids=assessment.content.candidate_peak_content_ids,
            selected_peak_hypothesis_id=selected_hypothesis_id,
            selection_outcome=outcome,
            selection_reasons=assessment.content.reason_codes,
        ),
        missing_requirements=(),
        locators=locators,
        _mint_token=_SECTION_MINT_TOKEN,
    )


def _alignment_section(
    subject: ExplanationSubject,
    *,
    spine: CidNlReferenceSpine | None,
    spine_cell: CellObservationRef | None,
    locators: tuple[ExplanationLocator, ...],
) -> ExplanationSection:
    if spine is None or spine.group is None or spine_cell is None:
        return _unavailable_section(
            ExplanationTopic.ALIGNMENT,
            source_owner="xic_extractor.alignment.cross_sample_peak_groups",
            reason="no owner-produced CID-NL reference spine was supplied",
            locators=locators,
        )
    group = spine.group
    if group.feature_family_id != subject.source_feature_family_id:
        raise ValueError("Alignment group crosses the Matrix source feature")
    if group.group_hypothesis_id != subject.peak_hypothesis_id:
        raise ValueError("Alignment group crosses the final Matrix hypothesis")
    disposition = _alignment_disposition(group)
    representative = (
        group.consolidation_winner_group_hypothesis_id or group.group_hypothesis_id
    )
    owner_reasons = tuple(
        owner.assignment_reason
        for owner in group.owners
        if owner.sample_stem == subject.sample_stem
    )
    reasons = tuple(
        sorted({value for value in (group.evidence, *owner_reasons) if value})
    ) or ("owner_group_membership_closed",)
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
            sample_member_peak_hypothesis_id=spine_cell.content.peak_hypothesis_id,
            grouping_disposition=disposition,
            representative_group_hypothesis_id=representative,
            decision_reasons=reasons,
        ),
        missing_requirements=(),
        locators=locators,
        _mint_token=_SECTION_MINT_TOKEN,
    )


def _alignment_disposition(group: CrossSamplePeakGroupHypothesis) -> str:
    if group.review_only:
        return "review_only"
    if group.consolidation_state and group.consolidation_state != "not_consolidated":
        return group.consolidation_state
    return "grouped"


def _matrix_section(
    subject: ExplanationSubject,
    *,
    matrix_value: str,
    matrix_row_mz: str,
    matrix_row_rt: str,
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
            matrix_row_mz=matrix_row_mz,
            matrix_row_rt=matrix_row_rt,
            sample_stem=subject.sample_stem,
        ),
        missing_requirements=(),
        locators=locators,
        _mint_token=_SECTION_MINT_TOKEN,
    )


def _backfill_section(
    subject: ExplanationSubject,
    *,
    matrix_cell: AlignedCell | None,
    query_result: EvidenceQueryResult[BackfillEvidenceRecord] | None,
    spine: CidNlReferenceSpine | None,
    owner_group_facts: ConservativeBackfillGroupFacts | None,
    owner_decision: ConservativeBackfillCellDecision | None,
    production_decision: ProductionCellDecision | None,
    locators: tuple[ExplanationLocator, ...],
) -> ExplanationSection:
    if query_result is None:
        return _unavailable_section(
            ExplanationTopic.BACKFILL,
            source_owner="xic_extractor.alignment.backfill_evidence_owner",
            reason="no owner-reader Backfill query result was supplied",
            locators=locators,
        )
    _validate_backfill_query_result(query_result)
    if not query_result.records:
        return _unavailable_section(
            ExplanationTopic.BACKFILL,
            source_owner="xic_extractor.alignment.backfill_evidence_owner",
            reason=query_result.claim("missing_key_absence").reason,
            supporting_claims=query_result.claims,
            locators=locators,
        )
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
            locators=locators,
        )
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
            locators=locators,
        )
    if subject.matrix_row_identity_basis != "no_split_peak_hypothesis":
        return _unavailable_section(
            ExplanationTopic.BACKFILL,
            source_owner="xic_extractor.alignment.conservative_backfill",
            reason=(
                "the supplied owners do not bind this split final Matrix hypothesis "
                "to one Backfill cell"
            ),
            supporting_claims=query_result.claims,
            locators=locators,
        )
    if owner_group_facts is None:
        return _unavailable_section(
            ExplanationTopic.BACKFILL,
            source_owner="xic_extractor.alignment.conservative_backfill",
            reason="no complete live Backfill owner group facts were supplied",
            supporting_claims=query_result.claims,
            locators=locators,
        )
    if production_decision is None:
        return _unavailable_section(
            ExplanationTopic.BACKFILL,
            source_owner="xic_extractor.alignment.production_decisions",
            reason="the live Matrix route has no ProductionCellDecision",
            supporting_claims=query_result.claims,
            locators=locators,
        )
    _validate_group_facts_route(owner_group_facts, subject)
    _validate_production_route(production_decision, subject)
    if (
        subject.sample.identity.sample_identity_id
        in owner_group_facts.direct_support_sample_ids
    ):
        reasons = tuple(
            sorted(
                {
                    *owner_group_facts.reason_codes,
                    "direct_support_no_recovery_required",
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
                    "the live Backfill group facts identify direct SUPPORT for the "
                    "exact cell, so recovery was not applicable; #230 claims retain "
                    "their reduced persisted-record ceiling"
                ),
            ),
            payload=BackfillExplanationPayload(
                evaluated=False,
                disposition="not_applicable",
                production_status=production_decision.production_status,
                write_matrix_value=production_decision.write_matrix_value,
                policy_version=owner_group_facts.policy_version,
                matched_candidate_peak_content_id=None,
                decision_reasons=reasons,
            ),
            missing_requirements=(),
            supporting_claims=query_result.claims,
            locators=locators,
            _mint_token=_SECTION_MINT_TOKEN,
        )
    if owner_decision is not None:
        matched_candidate = owner_decision.matched_candidate
        if production_decision.write_matrix_value and matched_candidate is not None:
            matched_content_id = (
                matched_candidate.peak_observation.content.peak_observation_content_id
            )
            if (
                matrix_cell is None
                or matrix_cell.source_candidate_id != matched_content_id
            ):
                return _unavailable_section(
                    ExplanationTopic.BACKFILL,
                    source_owner="xic_extractor.alignment.conservative_backfill",
                    reason=(
                        "the live Matrix cell does not bind the Backfill matched "
                        "candidate"
                    ),
                    supporting_claims=query_result.claims,
                    locators=locators,
                )
        return _evaluated_backfill_section(
            owner_decision,
            production_decision,
            query_result=query_result,
            locators=locators,
        )
    return _unavailable_section(
        ExplanationTopic.BACKFILL,
        source_owner="xic_extractor.alignment.conservative_backfill",
        reason=(
            "no complete live Backfill owner decision was supplied for this "
            "non-direct-support cell"
        ),
        supporting_claims=query_result.claims,
        locators=locators,
    )


def _evaluated_backfill_section(
    owner_decision: ConservativeBackfillCellDecision,
    production_decision: ProductionCellDecision,
    *,
    query_result: EvidenceQueryResult[BackfillEvidenceRecord],
    locators: tuple[ExplanationLocator, ...],
) -> ExplanationSection:
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
            disposition=_backfill_disposition(owner_decision, production_decision),
            production_status=production_decision.production_status,
            write_matrix_value=production_decision.write_matrix_value,
            policy_version=owner_decision.policy_version,
            matched_candidate_peak_content_id=matched_content_id,
            decision_reasons=reasons,
        ),
        missing_requirements=(),
        supporting_claims=query_result.claims,
        locators=locators,
        _mint_token=_SECTION_MINT_TOKEN,
    )


def _validate_group_facts_route(
    facts: ConservativeBackfillGroupFacts,
    subject: ExplanationSubject,
) -> None:
    group = facts.group_context.group
    if group.feature_family_id != subject.source_feature_family_id:
        raise ValueError("Backfill group facts cross the Matrix source feature")
    if group.group_hypothesis_id != subject.peak_hypothesis_id:
        raise ValueError("Backfill group facts cross the final Matrix hypothesis")
    if facts.group_context.processing_attempt != subject.processing_attempt:
        raise ValueError("Backfill group facts belong to a different attempt")


def _validate_production_route(
    decision: ProductionCellDecision,
    subject: ExplanationSubject,
) -> None:
    if (
        decision.feature_family_id != subject.source_feature_family_id
        or decision.sample_stem != subject.sample_stem
    ):
        raise ValueError("ProductionCellDecision crosses the Matrix route")


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
    if tuple(claim.subject for claim in query_result.claims) != expected_claim_subjects:
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


__all__ = ("build_explanation_sections",)
