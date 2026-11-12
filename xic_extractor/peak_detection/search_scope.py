from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, TypeAlias

from xic_extractor.configuration.models import ExtractionConfig
from xic_extractor.configuration.signal_processing_profile import (
    SignalProcessingProfile,
)
from xic_extractor.configuration.target_identity import (
    ResolvedTargetDefinition,
    TargetListSnapshot,
)
from xic_extractor.peak_detection.cid_nl_method_subject import (
    CidNlMethodSubject,
    UntargetedCidNlMethodSubjectBatch,
)
from xic_extractor.peak_detection.integration_result import IntegrationResult
from xic_extractor.peak_detection.observations import (
    PeakObservationGenerationProvenance,
)
from xic_extractor.peak_detection.physical_peak_models import (
    PhysicalPeakEvaluation,
)
from xic_extractor.sample_metadata import SampleMetadata

SEARCH_SCOPE_SCHEMA_VERSION = "search_scope"
SearchScopeVariant = Literal["targeted", "untargeted"]
CaptureBoundaryPolicy = Literal["resolver_interval", "targeted_gaussian15"]
CaptureSelectionPolicy = Literal["capture_default", "retained_only"]


class SearchScopeContractError(ValueError):
    """Raised when application-owned search content cannot close the boundary."""


class SearchScopeAuthorizationError(SearchScopeContractError):
    """Raised when a candidate crosses its declared or capture authorization."""


@dataclass(frozen=True, slots=True)
class CaptureMethodConstraints:
    """Closed sample-local scientific constraints selected by an application."""

    boundary_policy: CaptureBoundaryPolicy
    peak_config: ExtractionConfig
    signal_processing_profile: SignalProcessingProfile | None = None


@dataclass(frozen=True, slots=True)
class CaptureCandidateBinding:
    """Bind one authorized candidate to its exact sample-local capture query."""

    candidate_id: str
    method_subject_id: str
    sample_key: str
    acquisition_source_id: str
    query_mz: float
    query_rt_min: float
    query_rt_max: float
    query_ppm_tol: float
    preferred_rt: float
    retained_selected_integration: IntegrationResult | None = None
    selection_policy: CaptureSelectionPolicy = "capture_default"
    physical_evaluation: PhysicalPeakEvaluation | None = None
    acquired_trace_provenance: PeakObservationGenerationProvenance | None = None


@dataclass(frozen=True, slots=True)
class EvidenceProviderBinding:
    """Name one evidence provider without granting it capture authority."""

    provider_id: str
    method_subject_id: str


@dataclass(frozen=True, slots=True)
class TargetSearchScope:
    schema_version: str
    samples: tuple[SampleMetadata, ...]
    method_constraints: CaptureMethodConstraints
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
    method_constraints: CaptureMethodConstraints
    evidence_providers: tuple[EvidenceProviderBinding, ...]
    declared_candidate_ids: tuple[str, ...]
    capture_bindings: tuple[CaptureCandidateBinding, ...]

    @property
    def variant(self) -> Literal["untargeted"]:
        return "untargeted"


SearchScope: TypeAlias = TargetSearchScope | UntargetedSearchScope


def require_capture_binding(
    scope: SearchScope,
    candidate_id: str,
) -> CaptureCandidateBinding:
    candidate = _normalized_text(candidate_id, field="candidate_id")
    if candidate not in scope.declared_candidate_ids:
        raise SearchScopeAuthorizationError(
            f"candidate {candidate!r} is not declared by this SearchScope"
        )
    binding = next(
        (item for item in scope.capture_bindings if item.candidate_id == candidate),
        None,
    )
    if binding is None:
        if isinstance(scope, TargetSearchScope):
            raise SearchScopeAuthorizationError(
                "Target capture cannot authorize non-configured candidate "
                f"{candidate!r}"
            )
        raise SearchScopeAuthorizationError(
            f"Untargeted capture cannot authorize candidate {candidate!r}"
        )
    return binding


def target_ms1_subject_id(target: ResolvedTargetDefinition) -> str:
    """Return the exact Target-definition identity used by MS1-only capture."""

    if not isinstance(target, ResolvedTargetDefinition):
        raise SearchScopeContractError("MS1 Target subject must be owner-resolved")
    if target.definition.target.neutral_loss_da is not None:
        raise SearchScopeContractError("MS1 Target subject requires neutral loss off")
    return f"target-ms1:{target.lookup_label}"


def _normalized_text(value: object, *, field: str) -> str:
    if not isinstance(value, str) or not value or value != value.strip():
        raise SearchScopeContractError(f"{field} must be non-empty normalized text")
    return value


__all__ = (
    "SEARCH_SCOPE_SCHEMA_VERSION",
    "CaptureBoundaryPolicy",
    "CaptureCandidateBinding",
    "CaptureMethodConstraints",
    "CaptureSelectionPolicy",
    "EvidenceProviderBinding",
    "SearchScope",
    "SearchScopeAuthorizationError",
    "SearchScopeContractError",
    "SearchScopeVariant",
    "TargetSearchScope",
    "UntargetedSearchScope",
    "require_capture_binding",
    "target_ms1_subject_id",
)
