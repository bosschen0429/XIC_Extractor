from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from pathlib import Path

from xic_extractor.config import ExtractionConfig, Target
from xic_extractor.configuration.target_identity import (
    ResolvedTargetDefinition,
    mint_target_list_snapshot,
    resolve_target_definition,
    target_list_snapshot_payload,
)
from xic_extractor.discovery.models import DiscoveryCandidate, DiscoverySettings
from xic_extractor.discovery.targeted_candidate_set import (
    TargetedCandidateSetMember,
    TargetedCandidateSetRef,
    complete_targeted_candidate_discovery,
    mint_targeted_candidate_set_ref,
)
from xic_extractor.evidence_artifacts import EvidenceArtifactRef
from xic_extractor.extraction.cid_nl_evidence_projection import (
    TargetedCidNlProjectionRef,
    mint_targeted_cid_nl_projection_ref,
)
from xic_extractor.extraction.targeted_inputs import (
    ResolvedTargetedInputs,
    build_targeted_processing_request_identity,
)
from xic_extractor.neutral_loss import MS2ProductEvidence
from xic_extractor.peak_detection.cid_nl_chromatographic_support import (
    compose_cid_nl_chromatographic_support_ref,
)
from xic_extractor.peak_detection.cid_nl_evidence import mint_cid_nl_evidence_ref
from xic_extractor.peak_detection.cid_nl_peak_support import (
    DdaCidNlPeakSupportPolicy,
    resolve_dda_cid_nl_peak_support_ref,
)
from xic_extractor.peak_detection.hypotheses import IntegrationResult, PeakHypothesis
from xic_extractor.peak_detection.observations import mint_peak_observation_ref
from xic_extractor.peak_detection.peak_hypothesis_identity import (
    compose_cid_nl_peak_hypothesis,
)
from xic_extractor.peak_detection.selection_decision import (
    DiagnosticPeakHypothesisSelectionRef,
    mint_diagnostic_peak_hypothesis_selection_ref,
)
from xic_extractor.peak_detection.traces import Trace
from xic_extractor.processing_identity import (
    AcquisitionInputBinding,
    ProcessingAttemptIdentity,
    ProcessingOwnerReferences,
    ProcessingRequestIdentity,
    build_acquisition_source_identity,
    build_processing_request_identity,
    compose_processing_owner_references,
    issue_processing_attempt,
    resolve_request_acquisition_source,
)
from xic_extractor.sample_metadata import (
    ResolvedCanonicalSampleIdentity,
    SampleMetadata,
    SampleRole,
    mint_sample_metadata_snapshot,
    resolve_canonical_sample_identity,
)

PRECURSOR_MZ = 500.0
NEUTRAL_LOSS_DA = 116.0474
PRODUCT_MZ = PRECURSOR_MZ - NEUTRAL_LOSS_DA


@dataclass(frozen=True)
class Package2NoRawFixture:
    request: ProcessingRequestIdentity
    attempt: ProcessingAttemptIdentity
    samples: tuple[ResolvedCanonicalSampleIdentity, ...]
    target: ResolvedTargetDefinition
    other_target: ResolvedTargetDefinition
    owners: ProcessingOwnerReferences
    artifact_registry: dict[str, EvidenceArtifactRef] = field(default_factory=dict)
    projections_by_hypothesis_id: dict[str, TargetedCidNlProjectionRef] = field(
        default_factory=dict
    )

    def sample(self, raw_stem: str) -> ResolvedCanonicalSampleIdentity:
        matches = tuple(
            sample for sample in self.samples if sample.metadata.raw_stem == raw_stem
        )
        if len(matches) != 1:
            raise AssertionError(f"fixture sample is not unique: {raw_stem}")
        return matches[0]

    def lineage_for(
        self,
        members: tuple[TargetedCandidateSetMember, ...],
    ) -> tuple[EvidenceArtifactRef, ...]:
        reachable: set[str] = set()

        def visit(artifact_id: str) -> None:
            if artifact_id in reachable:
                return
            artifact = self.artifact_registry[artifact_id]
            reachable.add(artifact_id)
            for upstream_id in artifact.upstream_artifact_ids:
                visit(upstream_id)

        for member in members:
            hypothesis_artifact = member.hypothesis.artifact
            assert hypothesis_artifact is not None
            visit(hypothesis_artifact.evidence_artifact_id)
            visit(member.targeted_cid_nl_projection.artifact.evidence_artifact_id)
        return tuple(
            self.artifact_registry[artifact_id]
            for artifact_id in sorted(reachable)
        )

    def retry(self) -> Package2NoRawFixture:
        retry_attempt = issue_processing_attempt(self.request, retry_of=self.attempt)
        retry_owners = compose_processing_owner_references(
            request=self.request,
            attempt=retry_attempt,
            sample_snapshot=self.samples[0].snapshot,
            sample_resolutions=self.samples,
            target_snapshot=self.target.snapshot,
        )
        return Package2NoRawFixture(
            request=self.request,
            attempt=retry_attempt,
            samples=self.samples,
            target=self.target,
            other_target=self.other_target,
            owners=retry_owners,
        )


def build_package2_no_raw_fixture(
    tmp_path: Path,
    *,
    sample_stems: tuple[str, ...],
    sample_roles: Mapping[str, SampleRole] | None = None,
) -> Package2NoRawFixture:
    declared_roles = dict(sample_roles or {})
    unknown_role_samples = set(declared_roles).difference(sample_stems)
    if unknown_role_samples:
        raise ValueError(
            "sample_roles contains samples outside sample_stems: "
            + ", ".join(sorted(unknown_role_samples))
        )
    sources = []
    bindings = []
    metadata = []
    for injection_order, sample_stem in enumerate(sample_stems, start=1):
        source_path = tmp_path / f"{sample_stem}.synthetic"
        source_path.write_bytes(f"package2-no-raw:{sample_stem}".encode("utf-8"))
        source = build_acquisition_source_identity(
            source_path,
            acquisition_format="synthetic_no_raw",
            observation_namespace=f"synthetic:{sample_stem}",
        )
        sources.append(source)
        bindings.append(
            AcquisitionInputBinding(
                acquisition_source_id=source.acquisition_source_id,
                adapter_input_key=sample_stem,
                injection_order=injection_order,
            )
        )
        metadata.append(
            SampleMetadata(
                sample_name=sample_stem,
                raw_stem=sample_stem,
                sample_role=declared_roles.get(sample_stem, "unknown"),
            )
        )

    target_value = Target(
        label="Analyte",
        mz=PRECURSOR_MZ,
        rt_min=7.5,
        rt_max=8.6,
        ppm_tol=20.0,
        neutral_loss_da=NEUTRAL_LOSS_DA,
        nl_ppm_warn=20.0,
        nl_ppm_max=40.0,
        is_istd=False,
        istd_pair="",
    )
    other_target_value = Target(
        label="Other Analyte",
        mz=PRECURSOR_MZ,
        rt_min=7.5,
        rt_max=8.6,
        ppm_tol=20.0,
        neutral_loss_da=NEUTRAL_LOSS_DA,
        nl_ppm_warn=20.0,
        nl_ppm_max=40.0,
        is_istd=False,
        istd_pair="",
    )
    target_snapshot = mint_target_list_snapshot(
        (target_value, other_target_value),
        identity_namespace="target-list:package2-no-raw",
    )
    request = build_processing_request_identity(
        acquisition_sources=tuple(sources),
        acquisition_bindings=tuple(bindings),
        scientific_input_schema_version="package2-targeted-input-v1",
        processing_algorithm_version="package2-targeted-algorithm-v1",
        processing_contract_version="package2-targeted-contract-v1",
        scientific_inputs={
            "fixture": "package2-no-raw-multi-sample",
            "target_list_snapshot": target_list_snapshot_payload(target_snapshot),
        },
    )
    sample_snapshot = mint_sample_metadata_snapshot(
        tuple(metadata),
        identity_namespace="study:package2-no-raw",
    )
    samples = tuple(
        resolve_canonical_sample_identity(sample_snapshot, stem)
        for stem in sample_stems
    )
    target = resolve_target_definition(target_snapshot, "Analyte")
    other_target = resolve_target_definition(target_snapshot, "Other Analyte")
    attempt = issue_processing_attempt(request)
    owners = compose_processing_owner_references(
        request=request,
        attempt=attempt,
        sample_snapshot=sample_snapshot,
        sample_resolutions=samples,
        target_snapshot=target_snapshot,
    )
    return Package2NoRawFixture(
        request=request,
        attempt=attempt,
        samples=samples,
        target=target,
        other_target=other_target,
        owners=owners,
    )


def build_package2_targeted_raw_fixture(
    tmp_path: Path,
    *,
    sample_stems: tuple[str, ...],
    peak_config: ExtractionConfig,
    current_discovery_settings: DiscoverySettings | None = None,
    current_discovery_candidates_by_sample: (
        Mapping[str, tuple[DiscoveryCandidate, ...]] | None
    ) = None,
) -> Package2NoRawFixture:
    """Rebind the Package 2 fixture to the real Targeted RAW request owner."""

    fixture = build_package2_no_raw_fixture(
        tmp_path,
        sample_stems=sample_stems,
    )
    request = build_targeted_processing_request_identity(
        peak_config,
        target_snapshot=fixture.target.snapshot,
        raw_paths=[tmp_path / f"{stem}.synthetic" for stem in sample_stems],
        resolved_inputs=ResolvedTargetedInputs(
            injection_order={
                stem: order for order, stem in enumerate(sample_stems, start=1)
            },
            rt_prior_library={},
            model_selection_expected_diff_approvals=None,
        ),
        current_discovery_settings=current_discovery_settings,
        current_discovery_candidates_by_sample=(
            current_discovery_candidates_by_sample
        ),
    )
    attempt = issue_processing_attempt(request)
    owners = compose_processing_owner_references(
        request=request,
        attempt=attempt,
        sample_snapshot=fixture.samples[0].snapshot,
        sample_resolutions=fixture.samples,
        target_snapshot=fixture.target.snapshot,
    )
    return replace(
        fixture,
        request=request,
        attempt=attempt,
        owners=owners,
    )


def package2_hypothesis(
    fixture: Package2NoRawFixture,
    sample_stem: str,
    *,
    apex_rt: float,
    scan_number: int,
    height: float = 500.0,
) -> PeakHypothesis:
    sample = fixture.sample(sample_stem)
    source = resolve_request_acquisition_source(
        processing_owners=fixture.owners,
        sample=sample,
    )
    rt = (apex_rt - 0.3, apex_rt - 0.1, apex_rt, apex_rt + 0.1, apex_rt + 0.3)
    trace = Trace.from_arrays(
        sample_name=sample_stem,
        mz=PRECURSOR_MZ,
        rt=rt,
        intensity=(0.0, height / 10.0, height, height / 10.0, 0.0),
        rt_min=rt[0],
        rt_max=rt[-1],
        ppm_tol=10.0,
        source="synthetic_characterization",
        acquisition_source_id=source.acquisition_source_id,
    )
    integration = IntegrationResult(
        rt_left_min=apex_rt - 0.1,
        rt_apex_min=apex_rt,
        rt_right_min=apex_rt + 0.1,
        raw_apex_rt_min=apex_rt,
        rt_width_min=0.2,
        height_raw=height,
        height_smoothed=height,
        area_raw_counts_seconds=height * 6.6,
        integration_method="raw_trapezoid",
        boundary_sources=("candidate_interval",),
        raw_scan_indices=(1, 2, 3),
    )
    peak = mint_peak_observation_ref(
        processing_owners=fixture.owners,
        sample=sample,
        trace=trace,
        integration=integration,
        resolver_mode="local_minimum",
        proposal_sources=("local_minimum",),
    )
    _remember_artifact(fixture, peak.artifact)
    supports = []
    projected_evidence = None
    for scan_offset, rt_offset in ((0, -0.01), (1, 0.01)):
        evidence = mint_cid_nl_evidence_ref(
            processing_owners=fixture.owners,
            sample=sample,
            scan_number=scan_number + scan_offset,
            precursor_mz=PRECURSOR_MZ,
            product_mz=PRODUCT_MZ,
            configured_neutral_loss_da=NEUTRAL_LOSS_DA,
            observed_neutral_loss_da=NEUTRAL_LOSS_DA,
            observed_loss_error_ppm=0.0,
            scan_rt_min=apex_rt + rt_offset,
            product_intensity=height * 0.8,
        )
        _remember_artifact(fixture, evidence.artifact)
        support = resolve_dda_cid_nl_peak_support_ref(
            processing_owners=fixture.owners,
            sample=sample,
            canonical_evidence=evidence,
            candidate_peaks=(peak,),
            policy=DdaCidNlPeakSupportPolicy(
                precursor_tolerance_da=0.01,
                neutral_loss_tolerance_ppm=20.0,
            ),
        )
        supports.append(support)
        _remember_artifact(fixture, support.artifact)
        if scan_offset == 0:
            projected_evidence = evidence
    event = compose_cid_nl_chromatographic_support_ref(
        processing_owners=fixture.owners,
        sample=sample,
        scan_support_refs=tuple(supports),
    )
    _remember_artifact(fixture, event.artifact)
    hypothesis = compose_cid_nl_peak_hypothesis(
        processing_owners=fixture.owners,
        sample=sample,
        peak_observation=peak,
        chromatographic_support=event,
    )
    _remember_artifact(fixture, hypothesis.artifact)
    assert projected_evidence is not None
    projection = mint_targeted_cid_nl_projection_ref(
        processing_owners=fixture.owners,
        sample=sample,
        target=fixture.target,
        canonical_evidence=projected_evidence,
        targeted_evidence=MS2ProductEvidence(
            scan_rt=apex_rt - 0.01,
            precursor_mz=PRECURSOR_MZ,
            product_mz=PRODUCT_MZ,
            product_intensity=height * 0.8,
            product_base_ratio=0.8,
            target_product_ppm=0.0,
            observed_loss_da=NEUTRAL_LOSS_DA,
            observed_loss_error_ppm=0.0,
            scan_number=scan_number,
            acquisition_source_id=source.acquisition_source_id,
        ),
    )
    _remember_artifact(fixture, projection.artifact)
    fixture.projections_by_hypothesis_id[hypothesis.hypothesis_id] = projection
    return hypothesis


def package2_candidate_member(
    fixture: Package2NoRawFixture,
    hypothesis: PeakHypothesis,
) -> TargetedCandidateSetMember:
    return TargetedCandidateSetMember(
        hypothesis=hypothesis,
        targeted_cid_nl_projection=(
            fixture.projections_by_hypothesis_id[hypothesis.hypothesis_id]
        ),
    )


def package2_candidate_set(
    fixture: Package2NoRawFixture,
    sample_stem: str,
    hypotheses: tuple[PeakHypothesis, ...],
    *,
    failure_reason: str | None = None,
) -> TargetedCandidateSetRef:
    members = tuple(package2_candidate_member(fixture, item) for item in hypotheses)
    kwargs = {}
    if failure_reason is not None:
        kwargs["failure_reason"] = failure_reason
    completion = complete_targeted_candidate_discovery(
        processing_owners=fixture.owners,
        sample=fixture.sample(sample_stem),
        target=fixture.target,
        hypotheses=hypotheses,
        targeted_cid_nl_projections=tuple(
            member.targeted_cid_nl_projection for member in members
        ),
        lineage_artifacts=fixture.lineage_for(members),
        **kwargs,
    )
    return mint_targeted_candidate_set_ref(
        processing_owners=fixture.owners,
        sample=fixture.sample(sample_stem),
        target=fixture.target,
        completion=completion,
    )


def package2_selection(
    fixture: Package2NoRawFixture,
    sample_stem: str,
    hypotheses: tuple[PeakHypothesis, ...],
    *,
    candidate_set: TargetedCandidateSetRef | None = None,
    failure_reason: str | None = None,
) -> DiagnosticPeakHypothesisSelectionRef:
    return mint_diagnostic_peak_hypothesis_selection_ref(
        candidate_set=(
            package2_candidate_set(
                fixture,
                sample_stem,
                hypotheses,
                failure_reason=failure_reason,
            )
            if candidate_set is None
            else candidate_set
        )
    )


def _remember_artifact(
    fixture: Package2NoRawFixture,
    artifact: EvidenceArtifactRef | None,
) -> None:
    assert artifact is not None
    fixture.artifact_registry[artifact.evidence_artifact_id] = artifact
