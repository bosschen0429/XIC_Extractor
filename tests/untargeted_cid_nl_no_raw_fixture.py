from __future__ import annotations

import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from xic_extractor.peak_detection.cid_nl_method_subject import (
    CidNlMethodSubject,
    mint_untargeted_cid_nl_method_subject,
)
from xic_extractor.processing_identity import (
    AcquisitionSourceIdentity,
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
PRECURSOR_TOLERANCE_PPM = 20.0
NEUTRAL_LOSS_DA = 116.0474
PRODUCT_MZ = PRECURSOR_MZ - NEUTRAL_LOSS_DA


@dataclass(frozen=True, slots=True)
class UntargetedCidNlNoRawFixture:
    processing_attempt_id: str
    samples: tuple[SampleMetadata, ...]
    acquisition_sources: Mapping[str, AcquisitionSourceIdentity]
    method_subject: CidNlMethodSubject
    source_paths: Mapping[str, Path]

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
    sources: dict[str, AcquisitionSourceIdentity] = {}
    metadata = []
    source_paths: dict[str, Path] = {}
    for injection_order, (sample_stem, sample_role) in enumerate(
        sample_roles.items(),
        start=1,
    ):
        source_path = tmp_path / f"{sample_stem}.synthetic"
        source_path.write_bytes(f"untargeted-cid-nl-no-raw:{sample_stem}".encode())
        sources[sample_stem] = build_local_acquisition_source_identity(
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
        metadata.append(
            SampleMetadata(
                sample_name=sample_stem,
                raw_stem=sample_stem,
                injection_order=injection_order,
                sample_role=sample_role,
            )
        )
        source_paths[sample_stem] = source_path
    return UntargetedCidNlNoRawFixture(
        processing_attempt_id=str(uuid.uuid4()),
        samples=tuple(metadata),
        acquisition_sources=sources,
        method_subject=method_subject,
        source_paths=source_paths,
    )
