from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, TypeAlias

from xic_extractor.configuration.target_identity import (
    ResolvedTargetDefinition,
    TargetListSnapshot,
)
from xic_extractor.peak_detection.cid_nl_method_subject import (
    CidNlMethodSubject,
    UntargetedCidNlMethodSubjectBatch,
)
from xic_extractor.sample_metadata import SampleMetadata

SEARCH_SCOPE_SCHEMA_VERSION = "search_scope"
SearchScopeVariant = Literal["targeted", "untargeted"]


class SearchScopeContractError(ValueError):
    """Raised when application-owned search content cannot close the boundary."""


@dataclass(frozen=True, slots=True)
class CaptureCandidateBinding:
    """Bind one searched candidate to its method subject and sample."""

    candidate_id: str
    method_subject_id: str
    sample_key: str


@dataclass(frozen=True, slots=True)
class EvidenceProviderBinding:
    """Name one evidence provider without granting it capture authority."""

    provider_id: str
    method_subject_id: str


@dataclass(frozen=True, slots=True)
class TargetSearchScope:
    schema_version: str
    samples: tuple[SampleMetadata, ...]
    evidence_providers: tuple[EvidenceProviderBinding, ...]
    declared_candidate_ids: tuple[str, ...]
    capture_bindings: tuple[CaptureCandidateBinding, ...]
    target_snapshot: TargetListSnapshot
    target: ResolvedTargetDefinition

    @property
    def variant(self) -> Literal["targeted"]:
        return "targeted"


@dataclass(frozen=True, slots=True)
class UntargetedSearchScope:
    schema_version: str
    method_subjects: CidNlMethodSubject | UntargetedCidNlMethodSubjectBatch
    samples: tuple[SampleMetadata, ...]
    evidence_providers: tuple[EvidenceProviderBinding, ...]
    declared_candidate_ids: tuple[str, ...]
    capture_bindings: tuple[CaptureCandidateBinding, ...]

    @property
    def variant(self) -> Literal["untargeted"]:
        return "untargeted"


SearchScope: TypeAlias = TargetSearchScope | UntargetedSearchScope


def target_ms1_subject_id(target: ResolvedTargetDefinition) -> str:
    """Return the exact Target-definition identity used by MS1-only capture."""

    if not isinstance(target, ResolvedTargetDefinition):
        raise SearchScopeContractError("MS1 Target subject must be owner-resolved")
    if target.definition.target.neutral_loss_da is not None:
        raise SearchScopeContractError("MS1 Target subject requires neutral loss off")
    return f"target-ms1:{target.lookup_label}"


__all__ = (
    "SEARCH_SCOPE_SCHEMA_VERSION",
    "CaptureCandidateBinding",
    "EvidenceProviderBinding",
    "SearchScope",
    "SearchScopeContractError",
    "SearchScopeVariant",
    "TargetSearchScope",
    "UntargetedSearchScope",
    "target_ms1_subject_id",
)
