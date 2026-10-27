from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from xic_extractor.peak_detection.cid_nl_method_subject import (
    REQUEST_INPUT_KEY,
    CidNlMethodSubject,
    mint_untargeted_cid_nl_method_subject,
)
from xic_extractor.processing_identity import (
    AcquisitionInputBinding,
    ProcessingOwnerReferences,
    build_acquisition_source_identity,
    build_processing_request_identity,
    compose_processing_owner_references,
    issue_processing_attempt,
)
from xic_extractor.raw_reader import (
    THERMO_RAW_ACQUISITION_FORMAT,
    THERMO_RAW_OBSERVATION_NAMESPACE,
)
from xic_extractor.sample_metadata import (
    ResolvedCanonicalSampleIdentity,
    SampleMetadata,
    SampleRole,
    mint_sample_metadata_snapshot,
    resolve_canonical_sample_identity,
)

PRECURSOR_MZ = 500.0
PRECURSOR_TOLERANCE_PPM = 20.0
NEUTRAL_LOSS_DA = 116.0474
PRODUCT_MZ = PRECURSOR_MZ - NEUTRAL_LOSS_DA


@dataclass(frozen=True, slots=True)
class UntargetedCidNlNoRawFixture:
    owners: ProcessingOwnerReferences
    samples: tuple[ResolvedCanonicalSampleIdentity, ...]
    method_subject: CidNlMethodSubject
    source_paths: Mapping[str, Path]

    def sample(self, raw_stem: str) -> ResolvedCanonicalSampleIdentity:
        matches = tuple(
            sample for sample in self.samples if sample.metadata.raw_stem == raw_stem
        )
        if len(matches) != 1:
            raise AssertionError(f"fixture sample is not unique: {raw_stem}")
        return matches[0]


def build_untargeted_cid_nl_no_raw_fixture(
    tmp_path: Path,
    *,
    sample_roles: Mapping[str, SampleRole],
    thermo_raw_sources: bool = False,
) -> UntargetedCidNlNoRawFixture:
    method_subject = mint_untargeted_cid_nl_method_subject(
        precursor_mz=PRECURSOR_MZ,
        precursor_tolerance_ppm=PRECURSOR_TOLERANCE_PPM,
        neutral_loss_da=NEUTRAL_LOSS_DA,
    )
    sources = []
    bindings = []
    metadata = []
    source_paths: dict[str, Path] = {}
    for injection_order, (sample_stem, sample_role) in enumerate(
        sample_roles.items(),
        start=1,
    ):
        source_path = tmp_path / f"{sample_stem}.synthetic"
        source_path.write_bytes(f"untargeted-cid-nl-no-raw:{sample_stem}".encode())
        source = build_acquisition_source_identity(
            source_path,
            acquisition_format=(
                THERMO_RAW_ACQUISITION_FORMAT
                if thermo_raw_sources
                else "synthetic_no_raw"
            ),
            observation_namespace=(
                THERMO_RAW_OBSERVATION_NAMESPACE
                if thermo_raw_sources
                else f"untargeted-cid-nl:{sample_stem}"
            ),
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
                injection_order=injection_order,
                sample_role=sample_role,
            )
        )
        source_paths[sample_stem] = source_path

    request = build_processing_request_identity(
        acquisition_sources=tuple(sources),
        acquisition_bindings=tuple(bindings),
        scientific_input_schema_version="untargeted-cid-nl-fixture-input",
        processing_algorithm_version="untargeted-cid-nl-fixture",
        processing_contract_version="untargeted-cid-nl-fixture",
        scientific_inputs={
            "fixture": "untargeted-cid-nl-no-raw",
            REQUEST_INPUT_KEY: method_subject.request_payload(),
        },
    )
    sample_snapshot = mint_sample_metadata_snapshot(
        tuple(metadata),
        identity_namespace="study:untargeted-cid-nl-no-raw",
    )
    samples = tuple(
        resolve_canonical_sample_identity(sample_snapshot, sample_stem)
        for sample_stem in sample_roles
    )
    attempt = issue_processing_attempt()
    owners = compose_processing_owner_references(
        request=request,
        attempt=attempt,
        sample_snapshot=sample_snapshot,
        sample_resolutions=samples,
    )
    return UntargetedCidNlNoRawFixture(
        owners=owners,
        samples=samples,
        method_subject=method_subject,
        source_paths=source_paths,
    )
