from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, replace
from pathlib import Path

from xic_extractor.configuration.models import ExtractionConfig, Target
from xic_extractor.configuration.target_identity import (
    ResolvedTargetDefinition,
    mint_target_list_snapshot,
    resolve_target_definition,
    target_list_snapshot_payload,
)
from xic_extractor.discovery.models import DiscoveryPeak
from xic_extractor.discovery.settings import DiscoverySettings
from xic_extractor.extraction.targeted_inputs import (
    ResolvedTargetedInputs,
    build_targeted_processing_request_identity,
)
from xic_extractor.processing_identity import (
    AcquisitionInputBinding,
    ProcessingAttemptIdentity,
    ProcessingOwnerReferences,
    ProcessingRequestIdentity,
    build_acquisition_source_identity,
    build_processing_request_identity,
    compose_processing_owner_references,
    issue_processing_attempt,
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
class ProcessingIdentityNoRawFixture:
    request: ProcessingRequestIdentity
    attempt: ProcessingAttemptIdentity
    samples: tuple[ResolvedCanonicalSampleIdentity, ...]
    target: ResolvedTargetDefinition
    other_target: ResolvedTargetDefinition
    owners: ProcessingOwnerReferences

    def sample(self, raw_stem: str) -> ResolvedCanonicalSampleIdentity:
        matches = tuple(
            sample for sample in self.samples if sample.metadata.raw_stem == raw_stem
        )
        if len(matches) != 1:
            raise AssertionError(f"fixture sample is not unique: {raw_stem}")
        return matches[0]


def build_processing_identity_no_raw_fixture(
    tmp_path: Path,
    *,
    sample_stems: tuple[str, ...],
    sample_roles: Mapping[str, SampleRole] | None = None,
    excluded_samples: frozenset[str] = frozenset(),
) -> ProcessingIdentityNoRawFixture:
    declared_roles = dict(sample_roles or {})
    unknown_role_samples = set(declared_roles).difference(sample_stems)
    if unknown_role_samples:
        raise ValueError(
            "sample_roles contains samples outside sample_stems: "
            + ", ".join(sorted(unknown_role_samples))
        )
    unknown_excluded_samples = excluded_samples.difference(sample_stems)
    if unknown_excluded_samples:
        raise ValueError(
            "excluded_samples contains samples outside sample_stems: "
            + ", ".join(sorted(unknown_excluded_samples))
        )
    sources = []
    bindings = []
    metadata = []
    for injection_order, sample_stem in enumerate(sample_stems, start=1):
        source_path = tmp_path / f"{sample_stem}.synthetic"
        source_path.write_bytes(
            f"processing-identity-no-raw:{sample_stem}".encode("utf-8")
        )
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
                excluded=sample_stem in excluded_samples,
                exclusion_reason=(
                    "synthetic fixture exclusion"
                    if sample_stem in excluded_samples
                    else ""
                ),
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
        identity_namespace="target-list:processing-identity-no-raw",
    )
    request = build_processing_request_identity(
        acquisition_sources=tuple(sources),
        acquisition_bindings=tuple(bindings),
        scientific_input_schema_version="processing-identity-targeted-input",
        processing_algorithm_version="processing-identity-targeted-algorithm",
        processing_contract_version="processing-identity-targeted-contract",
        scientific_inputs={
            "fixture": "processing-identity-no-raw-multi-sample",
            "target_list_snapshot": target_list_snapshot_payload(target_snapshot),
        },
    )
    sample_snapshot = mint_sample_metadata_snapshot(
        tuple(metadata),
        identity_namespace="study:processing-identity-no-raw",
    )
    samples = tuple(
        resolve_canonical_sample_identity(sample_snapshot, stem)
        for stem in sample_stems
    )
    target = resolve_target_definition(target_snapshot, "Analyte")
    other_target = resolve_target_definition(target_snapshot, "Other Analyte")
    attempt = issue_processing_attempt()
    owners = compose_processing_owner_references(
        request=request,
        attempt=attempt,
        sample_snapshot=sample_snapshot,
        sample_resolutions=samples,
        target_snapshot=target_snapshot,
    )
    return ProcessingIdentityNoRawFixture(
        request=request,
        attempt=attempt,
        samples=samples,
        target=target,
        other_target=other_target,
        owners=owners,
    )


def build_targeted_processing_identity_raw_fixture(
    tmp_path: Path,
    *,
    sample_stems: tuple[str, ...],
    peak_config: ExtractionConfig,
    current_discovery_settings: DiscoverySettings | None = None,
    current_discovery_candidates_by_sample: (
        Mapping[str, tuple[DiscoveryPeak, ...]] | None
    ) = None,
) -> ProcessingIdentityNoRawFixture:
    """Rebind the processing-identity fixture to the real Targeted RAW request owner."""

    fixture = build_processing_identity_no_raw_fixture(
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
        ),
        current_discovery_settings=current_discovery_settings,
        current_discovery_candidates_by_sample=(current_discovery_candidates_by_sample),
    )
    attempt = issue_processing_attempt()
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
