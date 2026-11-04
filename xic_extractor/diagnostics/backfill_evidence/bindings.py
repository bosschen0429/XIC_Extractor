"""Owner binding and claim derivation shared by evidence representations."""

from __future__ import annotations

from dataclasses import replace

from xic_extractor.alignment.backfill.evidence_contract import (
    BackfillEvidenceCandidateAssignment,
    BackfillEvidenceOwnerBindings,
    BackfillEvidenceOwnerResult,
    BackfillEvidenceRecord,
    EvidenceClaim,
    EvidenceClaimState,
)
from xic_extractor.diagnostics.backfill_evidence.serialization import (
    backfill_evidence_identity_candidates,
)


def rehydrate_backfill_evidence_bindings(
    record: BackfillEvidenceRecord,
    bindings: BackfillEvidenceOwnerBindings,
) -> BackfillEvidenceOwnerResult:
    feature_id = _resolve_owner_identity(
        record.feature_id, known_values=bindings.known_feature_ids, field="feature_id"
    )
    if feature_id not in bindings.known_feature_ids:
        raise ValueError(f"unknown feature_id {feature_id!r}")

    sample_stem = _resolve_owner_identity(
        record.sample_stem,
        known_values=bindings.known_sample_stems,
        field="sample_stem",
    )
    if sample_stem not in bindings.known_sample_stems:
        raise ValueError(f"unknown sample_stem {sample_stem!r}")

    source_candidate_id = _resolve_owner_identity(
        record.source_candidate_id,
        known_values=frozenset(bindings.candidate_samples),
        field="source_candidate_id",
    )
    if source_candidate_id:
        candidate_sample = bindings.candidate_samples.get(source_candidate_id)
        if candidate_sample is None:
            raise ValueError(f"unknown source_candidate_id {source_candidate_id!r}")
        if candidate_sample != sample_stem:
            raise ValueError(
                f"source_candidate_id {source_candidate_id!r} belongs to "
                f"sample {candidate_sample!r}, not {sample_stem!r}"
            )

    canonical_record = replace(
        record,
        feature_id=feature_id,
        sample_stem=sample_stem,
        source_candidate_id=source_candidate_id,
    )
    group_assignment_binding = EvidenceClaimState.OWNER_REHYDRATED
    candidate_assignment = (
        BackfillEvidenceCandidateAssignment(
            sample_stem=sample_stem,
            source_candidate_id=source_candidate_id,
            feature_id=feature_id,
        )
        if source_candidate_id
        else None
    )
    return BackfillEvidenceOwnerResult(
        record=canonical_record,
        group_assignment_binding=group_assignment_binding,
        candidate_assignment=candidate_assignment,
    )


def backfill_evidence_query_claims(
    *,
    found: bool,
    owner_results: tuple[BackfillEvidenceOwnerResult, ...] | None,
) -> tuple[EvidenceClaim, ...]:
    group_assignment_bound = _all_backfill_owner_results(
        owner_results,
        field="group_assignment_binding",
    )
    cross_artifact_bound = False
    if owner_results:
        cross_artifact_bound = all(
            result.cross_artifact_binding is EvidenceClaimState.OWNER_REHYDRATED
            for result in owner_results
        )
    return (
        EvidenceClaim(
            subject="record_content",
            state=(
                EvidenceClaimState.STRUCTURE_BOUND
                if found
                else EvidenceClaimState.UNAVAILABLE
            ),
            reason=(
                "the reduced BackfillEvidenceRecord passed the exact schema and "
                "intrinsic projection invariants"
                if found
                else "no matching reduced record was retained"
            ),
        ),
        EvidenceClaim(
            subject="group_assignment_binding",
            state=(
                EvidenceClaimState.OWNER_REHYDRATED
                if found and group_assignment_bound
                else EvidenceClaimState.UNAVAILABLE
            ),
            reason=(
                "feature, sample, and optional candidate "
                "relations passed Alignment owner validation"
                if found and group_assignment_bound
                else "the feature assignment was omitted, incomplete, "
                "or not owner-validated"
            ),
        ),
        EvidenceClaim(
            subject="cross_artifact_binding",
            state=(
                EvidenceClaimState.OWNER_REHYDRATED
                if found and cross_artifact_bound
                else EvidenceClaimState.UNAVAILABLE
            ),
            reason=(
                "feature/sample/candidate relations passed owner validation"
                if found and cross_artifact_bound
                else "one or more feature/sample/candidate relations "
                "were omitted, unavailable, or no record matched"
            ),
        ),
        EvidenceClaim(
            subject="production_decision",
            state=EvidenceClaimState.UNAVAILABLE,
            reason=(
                "the compact schema omits complete ProductionDecisionSet owner inputs"
            ),
        ),
        EvidenceClaim(
            subject="full_aligned_cell",
            state=EvidenceClaimState.UNAVAILABLE,
            reason="the compact schema omits complete AlignedCell owner content",
        ),
        EvidenceClaim(
            subject="missing_key_absence",
            state=EvidenceClaimState.UNAVAILABLE,
            reason="zero retained rows does not prove scientific absence",
        ),
    )


def _resolve_owner_identity(
    value: str,
    *,
    known_values: frozenset[str],
    field: str,
) -> str:
    if not value:
        return value
    matches = tuple(
        candidate
        for candidate in backfill_evidence_identity_candidates(value)
        if candidate in known_values
    )
    if len(matches) > 1:
        raise ValueError(
            f"ambiguous formula-escaped machine identity in {field!r}: {value!r}"
        )
    return matches[0] if matches else value


def _all_backfill_owner_results(
    owner_results: tuple[BackfillEvidenceOwnerResult, ...] | None,
    *,
    field: str,
) -> bool:
    if not owner_results:
        return False
    return all(
        getattr(result, field) is EvidenceClaimState.OWNER_REHYDRATED
        for result in owner_results
    )


__all__ = (
    "backfill_evidence_query_claims",
    "rehydrate_backfill_evidence_bindings",
)
