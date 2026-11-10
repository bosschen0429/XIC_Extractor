from __future__ import annotations

import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from xic_extractor.configuration.models import Target
from xic_extractor.configuration.target_identity import (
    ResolvedTargetDefinition,
    mint_target_list_snapshot,
    resolve_target_definition,
)
from xic_extractor.processing_identity import (
    AcquisitionSourceIdentity,
    build_acquisition_source_identity,
    build_local_acquisition_source_identity,
)
from xic_extractor.raw_source_binding import (
    THERMO_RAW_ACQUISITION_FORMAT,
    THERMO_RAW_OBSERVATION_NAMESPACE,
)
from xic_extractor.sample_metadata import (
    SampleMetadata,
    SampleRole,
)

PRECURSOR_MZ = 500.0
NEUTRAL_LOSS_DA = 116.0474
PRODUCT_MZ = PRECURSOR_MZ - NEUTRAL_LOSS_DA


@dataclass(frozen=True)
class ProcessingIdentityNoRawFixture:
    processing_attempt_id: str
    samples: tuple[SampleMetadata, ...]
    acquisition_sources: Mapping[str, AcquisitionSourceIdentity]
    target: ResolvedTargetDefinition
    other_target: ResolvedTargetDefinition

    def sample(self, raw_stem: str) -> SampleMetadata:
        matches = tuple(
            sample for sample in self.samples if sample.raw_stem == raw_stem
        )
        if len(matches) != 1:
            raise AssertionError(f"fixture sample is not unique: {raw_stem}")
        return matches[0]

    def source(self, sample: SampleMetadata | str) -> AcquisitionSourceIdentity:
        stem = sample if isinstance(sample, str) else sample.raw_stem
        return self.acquisition_sources[stem]


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
    sources: dict[str, AcquisitionSourceIdentity] = {}
    metadata = []
    for sample_stem in sample_stems:
        source_path = tmp_path / f"{sample_stem}.synthetic"
        source_path.write_bytes(
            f"processing-identity-no-raw:{sample_stem}".encode("utf-8")
        )
        sources[sample_stem] = build_acquisition_source_identity(
            source_path,
            acquisition_format="synthetic_no_raw",
            observation_namespace=f"synthetic:{sample_stem}",
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
    return ProcessingIdentityNoRawFixture(
        processing_attempt_id=str(uuid.uuid4()),
        samples=tuple(metadata),
        acquisition_sources=sources,
        target=resolve_target_definition(target_snapshot, "Analyte"),
        other_target=resolve_target_definition(target_snapshot, "Other Analyte"),
    )


def build_targeted_processing_identity_raw_fixture(
    tmp_path: Path,
    *,
    sample_stems: tuple[str, ...],
) -> ProcessingIdentityNoRawFixture:
    """Bind the synthetic sources as Thermo local file revisions."""

    fixture = build_processing_identity_no_raw_fixture(
        tmp_path, sample_stems=sample_stems
    )
    sources = {
        stem: build_local_acquisition_source_identity(
            tmp_path / f"{stem}.synthetic",
            acquisition_format=THERMO_RAW_ACQUISITION_FORMAT,
            observation_namespace=THERMO_RAW_OBSERVATION_NAMESPACE,
        )
        for stem in sample_stems
    }
    return ProcessingIdentityNoRawFixture(
        processing_attempt_id=fixture.processing_attempt_id,
        samples=fixture.samples,
        acquisition_sources=sources,
        target=fixture.target,
        other_target=fixture.other_target,
    )
