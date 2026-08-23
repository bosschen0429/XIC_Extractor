from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Literal, TypeAlias

from xic_extractor.configuration.target_identity import (
    ResolvedTargetDefinition,
    TargetListSnapshot,
    target_list_snapshot_payload,
)
from xic_extractor.processing_identity import (
    ProcessingOwnerReferences,
    ProcessingRequestIdentity,
)
from xic_extractor.sample_metadata import ResolvedCanonicalSampleIdentity

SEARCH_SCOPE_SCHEMA_VERSION = "search_scope_v1"
SearchScopeVariant = Literal["targeted", "untargeted"]


class SearchScopeContractError(ValueError):
    """Raised when application-owned search content cannot close the boundary."""


class SearchScopeAuthorizationError(SearchScopeContractError):
    """Raised when a candidate crosses its declared or capture authorization."""


@dataclass(frozen=True, slots=True)
class EvidenceProviderBinding:
    """Name one evidence provider without granting it capture authority."""

    provider_id: str

    def __post_init__(self) -> None:
        _normalized_text(self.provider_id, field="provider_id")


@dataclass(frozen=True, slots=True)
class TargetSearchScope:
    schema_version: str
    request: ProcessingRequestIdentity
    samples: tuple[ResolvedCanonicalSampleIdentity, ...]
    evidence_providers: tuple[EvidenceProviderBinding, ...]
    declared_candidate_ids: tuple[str, ...]
    capture_candidate_ids: tuple[str, ...]
    target_snapshot: TargetListSnapshot
    target: ResolvedTargetDefinition

    def __post_init__(self) -> None:
        _validate_common_scope(
            schema_version=self.schema_version,
            request=self.request,
            samples=self.samples,
            evidence_providers=self.evidence_providers,
            declared_candidate_ids=self.declared_candidate_ids,
            capture_candidate_ids=self.capture_candidate_ids,
        )
        if not isinstance(self.target_snapshot, TargetListSnapshot):
            raise SearchScopeContractError("target_snapshot must be owner-minted")
        if not isinstance(self.target, ResolvedTargetDefinition):
            raise SearchScopeContractError("target must be owner-resolved")
        if self.target.snapshot != self.target_snapshot:
            raise SearchScopeContractError(
                "Target scope resolved target must come from its exact snapshot"
            )
        if _request_inputs(self.request).get(
            "target_list_snapshot"
        ) != target_list_snapshot_payload(self.target_snapshot):
            raise SearchScopeContractError(
                "Target scope snapshot is not pinned by the processing request"
            )

    @property
    def variant(self) -> Literal["targeted"]:
        return "targeted"


@dataclass(frozen=True, slots=True)
class UntargetedSearchScope:
    schema_version: str
    request: ProcessingRequestIdentity
    samples: tuple[ResolvedCanonicalSampleIdentity, ...]
    evidence_providers: tuple[EvidenceProviderBinding, ...]
    declared_candidate_ids: tuple[str, ...]
    capture_candidate_ids: tuple[str, ...]
    method_context_id: str

    def __post_init__(self) -> None:
        _validate_common_scope(
            schema_version=self.schema_version,
            request=self.request,
            samples=self.samples,
            evidence_providers=self.evidence_providers,
            declared_candidate_ids=self.declared_candidate_ids,
            capture_candidate_ids=self.capture_candidate_ids,
        )
        _normalized_text(self.method_context_id, field="method_context_id")

    @property
    def variant(self) -> Literal["untargeted"]:
        return "untargeted"


SearchScope: TypeAlias = TargetSearchScope | UntargetedSearchScope


def scope_allows_evidence_inspection(scope: SearchScope, candidate_id: str) -> bool:
    _validate_scope_type(scope)
    candidate = _normalized_text(candidate_id, field="candidate_id")
    return candidate in scope.declared_candidate_ids


def require_capture_candidate(scope: SearchScope, candidate_id: str) -> str:
    _validate_scope_type(scope)
    candidate = _normalized_text(candidate_id, field="candidate_id")
    if candidate not in scope.declared_candidate_ids:
        raise SearchScopeAuthorizationError(
            f"candidate {candidate!r} is not declared by this SearchScope"
        )
    if candidate not in scope.capture_candidate_ids:
        if isinstance(scope, TargetSearchScope):
            raise SearchScopeAuthorizationError(
                "Target capture cannot authorize non-configured candidate "
                f"{candidate!r}"
            )
        raise SearchScopeAuthorizationError(
            f"Untargeted capture cannot authorize candidate {candidate!r}"
        )
    return candidate


def validate_scope_owner_binding(
    scope: SearchScope,
    *,
    processing_owners: ProcessingOwnerReferences,
    sample: ResolvedCanonicalSampleIdentity,
) -> None:
    _validate_scope_type(scope)
    if not isinstance(processing_owners, ProcessingOwnerReferences):
        raise SearchScopeContractError(
            "capture requires validated ProcessingOwnerReferences"
        )
    if processing_owners.request != scope.request:
        raise SearchScopeContractError(
            "SearchScope references a different processing request"
        )
    if (
        sample not in scope.samples
        or sample not in processing_owners.sample_resolutions
    ):
        raise SearchScopeContractError("sample is outside the SearchScope universe")
    if isinstance(scope, TargetSearchScope):
        if processing_owners.target_snapshot != scope.target_snapshot:
            raise SearchScopeContractError(
                "Target SearchScope references a different owner snapshot"
            )


def _validate_common_scope(
    *,
    schema_version: object,
    request: object,
    samples: object,
    evidence_providers: object,
    declared_candidate_ids: object,
    capture_candidate_ids: object,
) -> None:
    if schema_version != SEARCH_SCOPE_SCHEMA_VERSION:
        raise SearchScopeContractError(
            f"SearchScope schema must be {SEARCH_SCOPE_SCHEMA_VERSION!r}"
        )
    if not isinstance(request, ProcessingRequestIdentity):
        raise SearchScopeContractError("request must be owner-minted")
    if not isinstance(samples, tuple) or any(
        not isinstance(sample, ResolvedCanonicalSampleIdentity) for sample in samples
    ):
        raise SearchScopeContractError(
            "samples must contain owner-resolved canonical identities"
        )
    expected_tokens = tuple(
        sorted(binding.adapter_input_key for binding in request.acquisition_bindings)
    )
    actual_tokens = tuple(sorted(sample.lookup_token for sample in samples))
    if actual_tokens != expected_tokens:
        raise SearchScopeContractError(
            "SearchScope sample universe must cover the processing request exactly"
        )
    sample_ids = tuple(sample.identity.sample_identity_id for sample in samples)
    if len(sample_ids) != len(set(sample_ids)):
        raise SearchScopeContractError(
            "SearchScope sample universe contains duplicate canonical identities"
        )
    if samples and any(sample.snapshot != samples[0].snapshot for sample in samples):
        raise SearchScopeContractError(
            "SearchScope samples must come from one owner snapshot"
        )
    if (
        not isinstance(evidence_providers, tuple)
        or not evidence_providers
        or any(
            not isinstance(provider, EvidenceProviderBinding)
            for provider in evidence_providers
        )
    ):
        raise SearchScopeContractError(
            "evidence_providers must be a non-empty typed tuple"
        )
    provider_ids = tuple(provider.provider_id for provider in evidence_providers)
    if len(provider_ids) != len(set(provider_ids)):
        raise SearchScopeContractError("SearchScope has duplicate evidence providers")
    declared = _candidate_ids(
        declared_candidate_ids,
        field="declared_candidate_ids",
    )
    capture = _candidate_ids(
        capture_candidate_ids,
        field="capture_candidate_ids",
    )
    if not set(capture).issubset(declared):
        raise SearchScopeContractError(
            "capture candidate universe must be a subset of declared evidence"
        )


def _request_inputs(request: ProcessingRequestIdentity) -> dict[str, object]:
    values = json.loads(request.normalized_scientific_inputs_json)
    if not isinstance(values, dict):
        raise SearchScopeContractError(
            "processing request scientific inputs must be a mapping"
        )
    return values


def _candidate_ids(value: object, *, field: str) -> tuple[str, ...]:
    if not isinstance(value, tuple):
        raise SearchScopeContractError(f"{field} must be a tuple")
    normalized = tuple(_normalized_text(item, field=field) for item in value)
    if len(normalized) != len(set(normalized)):
        raise SearchScopeContractError(f"{field} contains duplicate candidate IDs")
    return normalized


def _validate_scope_type(scope: object) -> None:
    if not isinstance(scope, (TargetSearchScope, UntargetedSearchScope)):
        raise SearchScopeContractError(
            "SearchScope must be TargetSearchScope or UntargetedSearchScope"
        )


def _normalized_text(value: object, *, field: str) -> str:
    if not isinstance(value, str) or not value or value != value.strip():
        raise SearchScopeContractError(f"{field} must be non-empty normalized text")
    return value


__all__ = (
    "SEARCH_SCOPE_SCHEMA_VERSION",
    "EvidenceProviderBinding",
    "SearchScope",
    "SearchScopeAuthorizationError",
    "SearchScopeContractError",
    "SearchScopeVariant",
    "TargetSearchScope",
    "UntargetedSearchScope",
    "require_capture_candidate",
    "scope_allows_evidence_inspection",
    "validate_scope_owner_binding",
)
