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
    SkippedEvidenceOwnerBindings,
    SkippedEvidenceRecord,
)
from xic_extractor.diagnostics.backfill_evidence.serialization import (
    backfill_evidence_identity_candidates,
)


def rehydrate_backfill_evidence_bindings(
    record: BackfillEvidenceRecord,
    bindings: BackfillEvidenceOwnerBindings,
) -> BackfillEvidenceOwnerResult:
    group_hypothesis_id = _resolve_owner_identity(
        record.group_hypothesis_id,
        known_values=bindings.known_group_ids,
        field="group_hypothesis_id",
    )
    if group_hypothesis_id and group_hypothesis_id not in bindings.known_group_ids:
        raise ValueError(f"unknown group_hypothesis_id {group_hypothesis_id!r}")

    expected_feature_family_id = bindings.group_feature_family_ids.get(
        group_hypothesis_id
    )
    feature_family_id = _resolve_owner_identity(
        record.feature_family_id,
        known_values=(
            frozenset({expected_feature_family_id})
            if expected_feature_family_id is not None
            else frozenset()
        ),
        field="feature_family_id",
    )
    if (
        expected_feature_family_id is not None
        and feature_family_id != expected_feature_family_id
    ):
        raise ValueError(
            f"group_hypothesis_id {group_hypothesis_id!r} belongs to "
            f"feature_family_id {expected_feature_family_id!r}, not "
            f"{feature_family_id!r}"
        )

    expected_public_family_id = bindings.group_public_family_ids.get(
        group_hypothesis_id
    )
    public_family_id = _resolve_owner_identity(
        record.public_family_id,
        known_values=(
            frozenset({expected_public_family_id})
            if expected_public_family_id
            else frozenset()
        ),
        field="public_family_id",
    )
    if (
        expected_public_family_id is not None
        and public_family_id
        and public_family_id != expected_public_family_id
    ):
        raise ValueError(
            f"group_hypothesis_id {group_hypothesis_id!r} has public_family_id "
            f"{expected_public_family_id!r}, not {public_family_id!r}"
        )

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
        feature_family_id=feature_family_id,
        group_hypothesis_id=group_hypothesis_id,
        public_family_id=public_family_id,
        sample_stem=sample_stem,
        source_candidate_id=source_candidate_id,
    )
    group_assignment_binding = (
        EvidenceClaimState.OWNER_REHYDRATED
        if group_hypothesis_id and expected_feature_family_id is not None
        else EvidenceClaimState.UNAVAILABLE
    )
    public_family_binding = (
        EvidenceClaimState.OWNER_REHYDRATED
        if (
            group_assignment_binding is EvidenceClaimState.OWNER_REHYDRATED
            and public_family_id
            and expected_public_family_id is not None
        )
        else EvidenceClaimState.UNAVAILABLE
    )
    candidate_assignment = (
        BackfillEvidenceCandidateAssignment(
            sample_stem=sample_stem,
            source_candidate_id=source_candidate_id,
            group_hypothesis_id=group_hypothesis_id,
        )
        if (
            source_candidate_id
            and group_assignment_binding is EvidenceClaimState.OWNER_REHYDRATED
        )
        else None
    )
    return BackfillEvidenceOwnerResult(
        record=canonical_record,
        group_assignment_binding=group_assignment_binding,
        public_family_binding=public_family_binding,
        candidate_assignment=candidate_assignment,
    )


def validate_skipped_evidence_bindings(
    record: SkippedEvidenceRecord,
    bindings: SkippedEvidenceOwnerBindings,
) -> None:
    if record.feature_family_id not in bindings.known_feature_family_ids:
        raise ValueError(f"unknown feature_family_id {record.feature_family_id!r}")
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
    owner_results: tuple[BackfillEvidenceOwnerResult, ...] | None,
) -> tuple[EvidenceClaim, ...]:
    group_assignment_bound = _all_backfill_owner_results(
        owner_results,
        field="group_assignment_binding",
    )
    public_family_bound = _all_backfill_owner_results(
        owner_results,
        field="public_family_binding",
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
                "feature, canonical group, sample, and optional candidate "
                "relations passed Alignment owner validation"
                if found and group_assignment_bound
                else "the canonical group assignment was omitted, incomplete, "
                "or not owner-validated"
            ),
        ),
        EvidenceClaim(
            subject="public_family_binding",
            state=(
                EvidenceClaimState.OWNER_REHYDRATED
                if found and public_family_bound
                else EvidenceClaimState.UNAVAILABLE
            ),
            reason=(
                "the compatibility label matches the owner-supplied canonical "
                "group relation; it is not canonical identity proof"
                if found and public_family_bound
                else "the public compatibility label relation was omitted or "
                "not owner-validated"
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
                "canonical group/sample/candidate relations and the public "
                "compatibility-label relation passed owner validation"
                if found and cross_artifact_bound
                else "one or more canonical or compatibility-label relations "
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
    "rehydrate_backfill_evidence_bindings",
    "skipped_evidence_query_claims",
    "validate_skipped_evidence_bindings",
)
