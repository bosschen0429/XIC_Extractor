"""Owner binding and claim derivation shared by evidence representations."""

from __future__ import annotations

from xic_extractor.alignment.backfill_evidence_contract import (
    BackfillEvidenceOwnerBindings,
    BackfillEvidenceRecord,
    EvidenceClaim,
    EvidenceClaimState,
    SkippedEvidenceOwnerBindings,
    SkippedEvidenceRecord,
)


def validate_backfill_evidence_bindings(
    record: BackfillEvidenceRecord,
    bindings: BackfillEvidenceOwnerBindings,
) -> None:
    if (
        record.group_hypothesis_id
        and record.group_hypothesis_id not in bindings.known_group_ids
    ):
        raise ValueError(
            f"unknown group_hypothesis_id {record.group_hypothesis_id!r}"
        )
    if record.sample_stem not in bindings.known_sample_stems:
        raise ValueError(f"unknown sample_stem {record.sample_stem!r}")
    if record.source_candidate_id:
        candidate_sample = bindings.candidate_samples.get(record.source_candidate_id)
        if candidate_sample is None:
            raise ValueError(
                f"unknown source_candidate_id {record.source_candidate_id!r}"
            )
        if candidate_sample != record.sample_stem:
            raise ValueError(
                f"source_candidate_id {record.source_candidate_id!r} belongs to "
                f"sample {candidate_sample!r}, not {record.sample_stem!r}"
            )


def validate_skipped_evidence_bindings(
    record: SkippedEvidenceRecord,
    bindings: SkippedEvidenceOwnerBindings,
) -> None:
    if record.feature_family_id not in bindings.known_feature_family_ids:
        raise ValueError(
            f"unknown feature_family_id {record.feature_family_id!r}"
        )
    if record.sample_stem not in bindings.known_sample_stems:
        raise ValueError(f"unknown sample_stem {record.sample_stem!r}")
    if record.backfill_scope != bindings.expected_backfill_scope:
        raise ValueError(
            f"backfill_scope {record.backfill_scope!r} does not match expected "
            f"{bindings.expected_backfill_scope!r}"
        )
    if record.predicate_version != bindings.expected_predicate_version:
        raise ValueError(
            f"predicate_version {record.predicate_version!r} does not match expected "
            f"{bindings.expected_predicate_version!r}"
        )
    if record.source_artifact != bindings.expected_source_artifact:
        raise ValueError(
            f"source_artifact {record.source_artifact!r} does not match expected "
            f"{bindings.expected_source_artifact!r}"
        )


def backfill_evidence_query_claims(
    *,
    found: bool,
    owner_bound: bool,
) -> tuple[EvidenceClaim, ...]:
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
            subject="cross_artifact_binding",
            state=(
                EvidenceClaimState.OWNER_REHYDRATED
                if found and owner_bound
                else EvidenceClaimState.UNAVAILABLE
            ),
            reason=(
                "required group/public IDs are present and group, sample, and "
                "candidate bindings passed owner validation"
                if found and owner_bound
                else "complete owner IDs and bindings were not supplied or no "
                "record matched"
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


def skipped_evidence_query_claims(
    *,
    found: bool,
    owner_bound: bool,
) -> tuple[EvidenceClaim, ...]:
    return (
        EvidenceClaim(
            subject="record_content",
            state=(
                EvidenceClaimState.OWNER_REHYDRATED
                if found
                else EvidenceClaimState.UNAVAILABLE
            ),
            reason=(
                "complete SkippedEvidenceRecord reconstructed by the Alignment "
                "Backfill scope owner parser"
                if found
                else "no matching skipped record was retained"
            ),
        ),
        EvidenceClaim(
            subject="cross_artifact_binding",
            state=(
                EvidenceClaimState.OWNER_REHYDRATED
                if found and owner_bound
                else EvidenceClaimState.UNAVAILABLE
            ),
            reason=(
                "family, sample, scope, predicate, and source bindings passed"
                if found and owner_bound
                else "complete owner bindings were not supplied or no record matched"
            ),
        ),
        EvidenceClaim(
            subject="full_audit_execution",
            state=(
                EvidenceClaimState.STRUCTURE_BOUND
                if found
                else EvidenceClaimState.UNAVAILABLE
            ),
            reason=(
                "the persisted availability literal passed schema invariants but is "
                "not a runtime execution receipt"
                if found
                else "no matching skipped record was retained"
            ),
        ),
        EvidenceClaim(
            subject="missing_key_absence",
            state=EvidenceClaimState.UNAVAILABLE,
            reason=(
                "zero retained rows does not prove a Backfill request was unnecessary"
            ),
        ),
    )


__all__ = (
    "backfill_evidence_query_claims",
    "skipped_evidence_query_claims",
    "validate_backfill_evidence_bindings",
    "validate_skipped_evidence_bindings",
)
