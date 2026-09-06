from __future__ import annotations

import json
import math
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Literal, TypeAlias

from xic_extractor.configuration.models import ExtractionConfig
from xic_extractor.configuration.target_identity import (
    ResolvedTargetDefinition,
    TargetListSnapshot,
    target_list_snapshot_payload,
)
from xic_extractor.peak_detection.cid_nl_method_subject import (
    BATCH_SCHEMA_VERSION,
    CID_NL_PROVIDER_ID,
    CidNlMethodSubjectContractError,
    cid_nl_method_subject_batch_from_request,
    cid_nl_method_subject_from_request,
    cid_nl_method_subject_from_target,
)
from xic_extractor.peak_detection.cid_nl_method_subject import (
    REQUEST_INPUT_KEY as CID_NL_REQUEST_INPUT_KEY,
)
from xic_extractor.peak_detection.evidence_facts import (
    PhysicalPeakEvaluation,
    validate_bounded_physical_peak_evaluation,
)
from xic_extractor.peak_detection.integration_result import IntegrationResult
from xic_extractor.peak_detection.signal_processing_profile import (
    SignalProcessingProfile,
    canonical_signal_processing_profile_bytes,
)
from xic_extractor.processing_identity import (
    ProcessingOwnerReferences,
    ProcessingRequestIdentity,
)
from xic_extractor.sample_metadata import ResolvedCanonicalSampleIdentity

SEARCH_SCOPE_SCHEMA_VERSION = "search_scope_v1"
PHYSICAL_CAPTURE_INPUT_KEY = "physical_capture_inputs"
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

    def __post_init__(self) -> None:
        if self.boundary_policy not in {
            "resolver_interval",
            "targeted_gaussian15",
        }:
            raise SearchScopeContractError("unsupported capture boundary policy")
        if not isinstance(self.peak_config, ExtractionConfig):
            raise SearchScopeContractError(
                "capture method constraints require owner-resolved ExtractionConfig"
            )
        if self.signal_processing_profile is not None and (
            not isinstance(self.signal_processing_profile, SignalProcessingProfile)
            or self.boundary_policy != "resolver_interval"
        ):
            raise SearchScopeContractError(
                "physical capture requires an exact signal profile "
                "and no legacy promotion"
            )


@dataclass(frozen=True, slots=True)
class CaptureCandidateBinding:
    """Bind one authorized candidate to its exact sample-local capture query."""

    candidate_id: str
    method_subject_id: str
    sample_identity_id: str
    sample_name: str
    acquisition_source_id: str
    query_mz: float
    query_rt_min: float
    query_rt_max: float
    query_ppm_tol: float
    preferred_rt: float
    retained_selected_integration: IntegrationResult | None = None
    selection_policy: CaptureSelectionPolicy = "capture_default"
    physical_evaluation: PhysicalPeakEvaluation | None = None

    def __post_init__(self) -> None:
        _normalized_text(self.candidate_id, field="candidate_id")
        _normalized_text(self.method_subject_id, field="method_subject_id")
        _normalized_text(self.sample_identity_id, field="sample_identity_id")
        _normalized_text(self.sample_name, field="sample_name")
        _normalized_text(self.acquisition_source_id, field="acquisition_source_id")
        _positive_finite_number(self.query_mz, field="query_mz")
        _positive_finite_number(self.query_ppm_tol, field="query_ppm_tol")
        rt_min = _nonnegative_finite_number(self.query_rt_min, field="query_rt_min")
        rt_max = _nonnegative_finite_number(self.query_rt_max, field="query_rt_max")
        preferred_rt = _nonnegative_finite_number(
            self.preferred_rt,
            field="preferred_rt",
        )
        if self.selection_policy not in {"capture_default", "retained_only"}:
            raise SearchScopeContractError(
                "capture selection_policy must be capture_default or retained_only"
            )
        if rt_min > rt_max:
            raise SearchScopeContractError(
                "capture candidate query RT interval must be ordered"
            )
        if not rt_min <= preferred_rt <= rt_max:
            raise SearchScopeContractError(
                "capture candidate preferred_rt is outside its query RT interval"
            )
        if (
            self.retained_selected_integration is not None
            and type(self.retained_selected_integration) is not IntegrationResult
        ):
            raise SearchScopeContractError(
                "retained capture selection must use one exact IntegrationResult"
            )
        if self.physical_evaluation is not None:
            validate_bounded_physical_peak_evaluation(self.physical_evaluation)
            question = self.physical_evaluation.question
            if (
                self.selection_policy != "capture_default"
                or self.retained_selected_integration is not None
                or question.query_rt_min != self.preferred_rt
            ):
                raise SearchScopeContractError(
                    "physical capture binding crosses its exact owner question"
                )


@dataclass(frozen=True, slots=True)
class EvidenceProviderBinding:
    """Name one evidence provider without granting it capture authority."""

    provider_id: str
    method_subject_id: str

    def __post_init__(self) -> None:
        _normalized_text(self.provider_id, field="provider_id")
        _normalized_text(self.method_subject_id, field="method_subject_id")


@dataclass(frozen=True, slots=True)
class TargetSearchScope:
    schema_version: str
    request: ProcessingRequestIdentity
    samples: tuple[ResolvedCanonicalSampleIdentity, ...]
    method_constraints: CaptureMethodConstraints
    evidence_providers: tuple[EvidenceProviderBinding, ...]
    declared_candidate_ids: tuple[str, ...]
    capture_bindings: tuple[CaptureCandidateBinding, ...]
    target_snapshot: TargetListSnapshot
    target: ResolvedTargetDefinition

    def __post_init__(self) -> None:
        if self.method_constraints.signal_processing_profile is not None or any(
            binding.physical_evaluation is not None for binding in self.capture_bindings
        ):
            raise SearchScopeContractError(
                "Target scope does not consume physical replay"
            )
        _validate_common_scope(
            schema_version=self.schema_version,
            request=self.request,
            samples=self.samples,
            method_constraints=self.method_constraints,
            evidence_providers=self.evidence_providers,
            declared_candidate_ids=self.declared_candidate_ids,
            capture_bindings=self.capture_bindings,
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
        if CID_NL_REQUEST_INPUT_KEY in _request_inputs(self.request):
            raise SearchScopeContractError(
                "Target SearchScope rejects Untargeted provider fields"
            )
        _validate_targeted_evidence_provider(
            self.evidence_providers,
            target=self.target,
        )
        _validate_targeted_capture_bindings(self.capture_bindings, target=self.target)

    @property
    def variant(self) -> Literal["targeted"]:
        return "targeted"


@dataclass(frozen=True, slots=True)
class UntargetedSearchScope:
    schema_version: str
    request: ProcessingRequestIdentity
    samples: tuple[ResolvedCanonicalSampleIdentity, ...]
    method_constraints: CaptureMethodConstraints
    evidence_providers: tuple[EvidenceProviderBinding, ...]
    declared_candidate_ids: tuple[str, ...]
    capture_bindings: tuple[CaptureCandidateBinding, ...]

    def __post_init__(self) -> None:
        _validate_common_scope(
            schema_version=self.schema_version,
            request=self.request,
            samples=self.samples,
            method_constraints=self.method_constraints,
            evidence_providers=self.evidence_providers,
            declared_candidate_ids=self.declared_candidate_ids,
            capture_bindings=self.capture_bindings,
            allow_sample_subset=True,
        )
        if "target_list_snapshot" in _request_inputs(self.request):
            raise SearchScopeContractError(
                "Untargeted SearchScope rejects Targeted request fields"
            )
        _validate_untargeted_evidence_provider(
            self.evidence_providers,
            request=self.request,
        )
        _validate_untargeted_capture_bindings(
            self.capture_bindings,
            request=self.request,
            samples=self.samples,
            declared_candidate_ids=self.declared_candidate_ids,
            evidence_providers=self.evidence_providers,
        )
        _validate_physical_capture_scope(self)

    @property
    def variant(self) -> Literal["untargeted"]:
        return "untargeted"

    @property
    def method_context_id(self) -> str:
        return self.evidence_providers[0].method_subject_id


SearchScope: TypeAlias = TargetSearchScope | UntargetedSearchScope


def physical_capture_input_payload(
    profile: SignalProcessingProfile,
    evaluations: Mapping[str, PhysicalPeakEvaluation],
) -> dict[str, object]:
    """Pin the profile and owner-issued physical answers in request identity."""
    return {
        "schema_version": "physical_capture_inputs_v1",
        "profile": json.loads(canonical_signal_processing_profile_bytes(profile)),
        "candidates": {
            candidate_id: _physical_evaluation_identity(evaluation)
            for candidate_id, evaluation in sorted(evaluations.items())
        },
    }


def _physical_evaluation_identity(evaluation: PhysicalPeakEvaluation) -> dict[str, str]:
    validate_bounded_physical_peak_evaluation(evaluation)
    return {
        "prepared_signal_id": evaluation.prepared.prepared_signal_id,
        "physical_question_id": evaluation.question.physical_question_id,
        "physical_answer_id": evaluation.answer.physical_answer_id,
        "physical_fact_id": evaluation.facts.fact_content_id
        if evaluation.facts
        else "",
    }


def _validate_physical_capture_scope(scope: UntargetedSearchScope) -> None:
    profile = scope.method_constraints.signal_processing_profile
    payload = _request_inputs(scope.request).get(PHYSICAL_CAPTURE_INPUT_KEY)
    if profile is None:
        if payload is not None or any(
            binding.physical_evaluation is not None
            for binding in scope.capture_bindings
        ):
            raise SearchScopeContractError(
                "physical replay lost its request-bound profile"
            )
        return
    if (
        not isinstance(payload, dict)
        or payload.get("schema_version") != "physical_capture_inputs_v1"
        or payload.get("profile")
        != json.loads(canonical_signal_processing_profile_bytes(profile))
        or not isinstance(payload.get("candidates"), dict)
    ):
        raise SearchScopeContractError("physical capture profile is not request-bound")
    candidates = payload["candidates"]
    for binding in scope.capture_bindings:
        evaluation = binding.physical_evaluation
        if (
            evaluation is None
            or evaluation.prepared.profile_id != profile.profile_id
            or candidates.get(binding.candidate_id)
            != _physical_evaluation_identity(evaluation)
        ):
            raise SearchScopeContractError(
                "physical capture answer is not bound to its request candidate"
            )


def scope_allows_evidence_inspection(scope: SearchScope, candidate_id: str) -> bool:
    _validate_scope_type(scope)
    candidate = _normalized_text(candidate_id, field="candidate_id")
    return candidate in scope.declared_candidate_ids


def require_capture_binding(
    scope: SearchScope,
    candidate_id: str,
) -> CaptureCandidateBinding:
    _validate_scope_type(scope)
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


def validate_scope_trace_coordinates(
    scope: SearchScope,
    *,
    binding: CaptureCandidateBinding,
    sample_name: object,
    acquisition_source_id: object,
    mz: object,
    rt_min: object,
    rt_max: object,
    ppm_tol: object,
) -> None:
    """Reject a trace that conflicts with its exact candidate capture binding."""

    _validate_scope_type(scope)
    if binding not in scope.capture_bindings:
        raise SearchScopeContractError("capture binding is outside this SearchScope")
    trace_sample = _normalized_text(sample_name, field="trace sample_name")
    trace_source = _normalized_text(
        acquisition_source_id,
        field="trace acquisition_source_id",
    )
    trace_mz = _positive_finite_number(mz, field="trace mz")
    trace_ppm_tol = _positive_finite_number(ppm_tol, field="trace ppm_tol")
    trace_rt_min = _nonnegative_finite_number(rt_min, field="trace rt_min")
    trace_rt_max = _nonnegative_finite_number(rt_max, field="trace rt_max")
    if (
        trace_sample != binding.sample_name
        or trace_source != binding.acquisition_source_id
        or trace_mz != binding.query_mz
        or trace_rt_min != binding.query_rt_min
        or trace_rt_max != binding.query_rt_max
        or trace_ppm_tol != binding.query_ppm_tol
    ):
        raise SearchScopeContractError(
            "capture trace coordinates mismatch candidate capture binding"
        )


def _validate_common_scope(
    *,
    schema_version: object,
    request: object,
    samples: object,
    method_constraints: object,
    evidence_providers: object,
    declared_candidate_ids: object,
    capture_bindings: object,
    allow_sample_subset: bool = False,
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
    samples_are_valid = (
        bool(actual_tokens)
        and len(actual_tokens) == len(set(actual_tokens))
        and set(actual_tokens).issubset(expected_tokens)
        if allow_sample_subset
        else actual_tokens == expected_tokens
    )
    if not samples_are_valid:
        raise SearchScopeContractError(
            "SearchScope sample universe is not authorized by the processing request"
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
    if not isinstance(method_constraints, CaptureMethodConstraints):
        raise SearchScopeContractError(
            "method_constraints must be a closed CaptureMethodConstraints value"
        )
    if not isinstance(evidence_providers, tuple) or any(
        not isinstance(provider, EvidenceProviderBinding)
        for provider in evidence_providers
    ):
        raise SearchScopeContractError("evidence_providers must be a typed tuple")
    provider_keys = tuple(
        (provider.provider_id, provider.method_subject_id)
        for provider in evidence_providers
    )
    if len(provider_keys) != len(set(provider_keys)):
        raise SearchScopeContractError("SearchScope has duplicate evidence providers")
    declared = _candidate_ids(
        declared_candidate_ids,
        field="declared_candidate_ids",
    )
    if not isinstance(capture_bindings, tuple) or any(
        not isinstance(binding, CaptureCandidateBinding) for binding in capture_bindings
    ):
        raise SearchScopeContractError("capture_bindings must be a typed tuple")
    capture_ids = tuple(binding.candidate_id for binding in capture_bindings)
    if len(capture_ids) != len(set(capture_ids)):
        raise SearchScopeContractError(
            "capture_bindings contains duplicate candidate IDs"
        )
    if not set(capture_ids).issubset(declared):
        raise SearchScopeContractError(
            "capture candidate universe must be a subset of declared evidence"
        )
    samples_by_identity = {
        sample.identity.sample_identity_id: sample for sample in samples
    }
    request_sources = {
        item.adapter_input_key: item.acquisition_source_id
        for item in request.acquisition_bindings
    }
    for binding in capture_bindings:
        sample = samples_by_identity.get(binding.sample_identity_id)
        if sample is None or sample.metadata.sample_name != binding.sample_name:
            raise SearchScopeContractError(
                "capture binding references a different canonical sample"
            )
        if request_sources.get(sample.lookup_token) != binding.acquisition_source_id:
            raise SearchScopeContractError(
                "capture binding acquisition source is not request-bound"
            )


def _validate_targeted_capture_bindings(
    bindings: tuple[CaptureCandidateBinding, ...],
    *,
    target: ResolvedTargetDefinition,
) -> None:
    value = target.definition.target
    expected_subject_id = _target_capture_subject_id(target)
    for binding in bindings:
        if (
            binding.method_subject_id != expected_subject_id
            or binding.query_mz != value.mz
            or binding.query_ppm_tol != value.ppm_tol
        ):
            raise SearchScopeContractError(
                "Target capture binding mismatches exact Target subject/m/z/ppm"
            )
        expected_selection_policy = (
            "retained_only" if value.neutral_loss_da is None else "capture_default"
        )
        if binding.selection_policy != expected_selection_policy:
            raise SearchScopeContractError(
                "Target capture selection policy mismatches its evidence method"
            )
        if not value.rt_min <= binding.preferred_rt <= value.rt_max:
            raise SearchScopeContractError(
                "Target capture preferred_rt is outside configured Target RT interval"
            )


def _validate_untargeted_capture_bindings(
    bindings: tuple[CaptureCandidateBinding, ...],
    *,
    request: ProcessingRequestIdentity,
    samples: tuple[ResolvedCanonicalSampleIdentity, ...],
    declared_candidate_ids: tuple[str, ...],
    evidence_providers: tuple[EvidenceProviderBinding, ...],
) -> None:
    request_inputs = _request_inputs(request)
    payload = request_inputs.get(CID_NL_REQUEST_INPUT_KEY)
    if (
        isinstance(payload, dict)
        and payload.get("schema_version") == BATCH_SCHEMA_VERSION
    ):
        try:
            batch = cid_nl_method_subject_batch_from_request(request)
        except CidNlMethodSubjectContractError as exc:
            raise SearchScopeContractError(str(exc)) from exc
        provider_subject_ids = tuple(
            provider.method_subject_id for provider in evidence_providers
        )
        all_subject_ids = tuple(subject.method_subject_id for subject in batch.subjects)
        if provider_subject_ids == all_subject_ids:
            expected_memberships = batch.candidate_memberships
        elif len(provider_subject_ids) == 1:
            expected_memberships = tuple(
                membership
                for membership in batch.candidate_memberships
                if membership[1] == provider_subject_ids[0]
            )
        else:
            raise SearchScopeContractError(
                "Untargeted batch SearchScope must be the complete universe or "
                "one exact subject view"
            )
        sample_candidate_ids = _request_sample_candidate_ids(
            request_inputs,
            samples=samples,
        )
        if sample_candidate_ids is not None:
            expected_memberships = tuple(
                membership
                for membership in expected_memberships
                if membership[0] in sample_candidate_ids
            )
        expected_candidate_ids = tuple(
            candidate_id for candidate_id, _ in expected_memberships
        )
        expected_positions = {
            candidate_id: index
            for index, candidate_id in enumerate(expected_candidate_ids)
        }
        if not set(declared_candidate_ids).issubset(expected_positions) or tuple(
            expected_positions[candidate_id] for candidate_id in declared_candidate_ids
        ) != tuple(
            sorted(
                expected_positions[candidate_id]
                for candidate_id in declared_candidate_ids
            )
        ):
            raise SearchScopeContractError(
                "Untargeted SearchScope candidates do not match request subject "
                "memberships"
            )
        binding_ids = tuple(binding.candidate_id for binding in bindings)
        declared_positions = {
            candidate_id: index
            for index, candidate_id in enumerate(declared_candidate_ids)
        }
        binding_positions = tuple(
            declared_positions[candidate_id] for candidate_id in binding_ids
        )
        if binding_positions != tuple(sorted(binding_positions)):
            raise SearchScopeContractError(
                "Untargeted capture bindings do not preserve declared candidate order"
            )
        subjects_by_id = {
            subject.method_subject_id: subject for subject in batch.subjects
        }
        memberships_by_candidate = dict(expected_memberships)
        for binding in bindings:
            subject_id = memberships_by_candidate[binding.candidate_id]
            subject = subjects_by_id.get(subject_id)
            if subject is None or binding.method_subject_id != subject_id:
                raise SearchScopeContractError(
                    "Untargeted capture binding does not match its candidate "
                    "subject membership"
                )
            mz_error_ppm = (
                abs(binding.query_mz - subject.precursor_mz)
                / subject.precursor_mz
                * 1_000_000.0
            )
            if (
                mz_error_ppm > subject.precursor_tolerance_ppm
                or binding.query_ppm_tol != subject.precursor_tolerance_ppm
            ):
                raise SearchScopeContractError(
                    "Untargeted capture binding query does not match its bound subject"
                )
        return

    try:
        subject = cid_nl_method_subject_from_request(request)
    except CidNlMethodSubjectContractError as exc:
        raise SearchScopeContractError(str(exc)) from exc
    for binding in bindings:
        mz_error_ppm = (
            abs(binding.query_mz - subject.precursor_mz)
            / subject.precursor_mz
            * 1_000_000.0
        )
        if (
            binding.method_subject_id != subject.method_subject_id
            or mz_error_ppm > subject.precursor_tolerance_ppm
            or binding.query_ppm_tol != subject.precursor_tolerance_ppm
        ):
            raise SearchScopeContractError(
                "Untargeted capture binding mismatches request-bound method subject"
            )


def _request_sample_candidate_ids(
    request_inputs: dict[str, object],
    *,
    samples: tuple[ResolvedCanonicalSampleIdentity, ...],
) -> frozenset[str] | None:
    universe = request_inputs.get("current_discovery_candidate_universe")
    if universe is None:
        return None
    if not isinstance(universe, dict) or not isinstance(universe.get("samples"), list):
        raise SearchScopeContractError(
            "Untargeted batch request candidate universe is malformed"
        )
    expected_stems = {sample.metadata.raw_stem for sample in samples}
    matched_stems: set[str] = set()
    candidate_ids: set[str] = set()
    for entry in universe["samples"]:
        if (
            not isinstance(entry, dict)
            or entry.get("sample_stem") not in expected_stems
        ):
            continue
        stem = entry["sample_stem"]
        ids = entry.get("candidate_ids")
        if (
            not isinstance(stem, str)
            or not isinstance(ids, list)
            or any(not isinstance(candidate_id, str) for candidate_id in ids)
        ):
            raise SearchScopeContractError(
                "Untargeted batch candidate universe is malformed"
            )
        if stem in matched_stems:
            raise SearchScopeContractError(
                "Untargeted batch candidate universe duplicated a sample"
            )
        matched_stems.add(stem)
        candidate_ids.update(ids)
    if matched_stems != expected_stems:
        raise SearchScopeContractError(
            "Untargeted batch candidate universe omitted a scope sample"
        )
    return frozenset(candidate_ids)


def _request_inputs(request: ProcessingRequestIdentity) -> dict[str, object]:
    values = json.loads(request.normalized_scientific_inputs_json)
    if not isinstance(values, dict):
        raise SearchScopeContractError(
            "processing request scientific inputs must be a mapping"
        )
    return values


def _validate_targeted_evidence_provider(
    providers: tuple[EvidenceProviderBinding, ...],
    *,
    target: ResolvedTargetDefinition,
) -> None:
    if target.definition.target.neutral_loss_da is None:
        if providers:
            raise SearchScopeContractError(
                "MS1-only Target SearchScope does not accept a CID-NL provider"
            )
        return
    try:
        expected_subject = cid_nl_method_subject_from_target(target)
    except CidNlMethodSubjectContractError as exc:
        raise SearchScopeContractError(str(exc)) from exc
    _validate_exact_cid_nl_provider(
        providers,
        expected_method_subject_id=expected_subject.method_subject_id,
        variant="Target",
    )


def target_ms1_subject_id(target: ResolvedTargetDefinition) -> str:
    """Return the exact Target-definition identity used by MS1-only capture."""

    if not isinstance(target, ResolvedTargetDefinition):
        raise SearchScopeContractError("MS1 Target subject must be owner-resolved")
    if target.definition.target.neutral_loss_da is not None:
        raise SearchScopeContractError("MS1 Target subject requires neutral loss off")
    return f"target-ms1:{target.definition.target_definition_id}"


def _target_capture_subject_id(target: ResolvedTargetDefinition) -> str:
    if target.definition.target.neutral_loss_da is None:
        return target_ms1_subject_id(target)
    try:
        return cid_nl_method_subject_from_target(target).method_subject_id
    except CidNlMethodSubjectContractError as exc:
        raise SearchScopeContractError(str(exc)) from exc


def _validate_untargeted_evidence_provider(
    providers: tuple[EvidenceProviderBinding, ...],
    *,
    request: ProcessingRequestIdentity,
) -> None:
    request_inputs = _request_inputs(request)
    payload = request_inputs.get(CID_NL_REQUEST_INPUT_KEY)
    if (
        isinstance(payload, dict)
        and payload.get("schema_version") == BATCH_SCHEMA_VERSION
    ):
        try:
            batch = cid_nl_method_subject_batch_from_request(request)
        except CidNlMethodSubjectContractError as exc:
            raise SearchScopeContractError(
                f"Untargeted SearchScope evidence provider is not request-bound: {exc}"
            ) from exc
        expected = tuple(
            EvidenceProviderBinding(
                provider_id=CID_NL_PROVIDER_ID,
                method_subject_id=subject.method_subject_id,
            )
            for subject in batch.subjects
        )
        if providers != expected and not (
            len(providers) == 1 and providers[0] in expected
        ):
            raise SearchScopeContractError(
                "Untargeted SearchScope evidence providers must match the request "
                "subject batch or one exact subject view"
            )
        return
    try:
        expected_subject = cid_nl_method_subject_from_request(request)
    except CidNlMethodSubjectContractError as exc:
        raise SearchScopeContractError(
            f"Untargeted SearchScope evidence provider is not request-bound: {exc}"
        ) from exc
    _validate_exact_cid_nl_provider(
        providers,
        expected_method_subject_id=expected_subject.method_subject_id,
        variant="Untargeted",
    )


def _validate_exact_cid_nl_provider(
    providers: tuple[EvidenceProviderBinding, ...],
    *,
    expected_method_subject_id: str,
    variant: str,
) -> None:
    if len(providers) != 1:
        raise SearchScopeContractError(
            f"{variant} SearchScope requires exactly one current evidence provider"
        )
    binding = providers[0]
    if (
        binding.provider_id != CID_NL_PROVIDER_ID
        or binding.method_subject_id != expected_method_subject_id
    ):
        raise SearchScopeContractError(
            f"{variant} SearchScope evidence provider mismatches its owner definition"
        )


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


def _positive_finite_number(value: object, *, field: str) -> float:
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not math.isfinite(float(value))
        or float(value) <= 0.0
    ):
        raise SearchScopeContractError(
            f"capture trace coordinates require positive finite {field}"
        )
    return float(value)


def _nonnegative_finite_number(value: object, *, field: str) -> float:
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not math.isfinite(float(value))
        or float(value) < 0.0
    ):
        raise SearchScopeContractError(
            f"capture binding requires finite non-negative {field}"
        )
    return float(value)


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
    "scope_allows_evidence_inspection",
    "target_ms1_subject_id",
    "validate_scope_owner_binding",
    "validate_scope_trace_coordinates",
)
