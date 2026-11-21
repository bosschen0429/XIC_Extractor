"""Bind an Untargeted run's cohort to its RAW sources and Discovery inputs."""

from __future__ import annotations

import uuid
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path

from xic_extractor.alignment.rt_frame import SampleRtFrame
from xic_extractor.configuration.signal_processing_profile import (
    SignalProcessingProfile,
)
from xic_extractor.discovery.batch_contract import (
    DiscoveryPeakUniverseReceipt,
)
from xic_extractor.discovery.models import DiscoveryPeak
from xic_extractor.discovery.settings import (
    DiscoverySettings,
)
from xic_extractor.peak_detection.cid_nl_method_subject import (
    CidNlMethodSubject,
    UntargetedCidNlMethodSubjectBatch,
)
from xic_extractor.processing_identity import (
    AcquisitionSourceExpectation,
    IdentityContractError,
)
from xic_extractor.sample_metadata import (
    SampleMetadata,
)


@dataclass(frozen=True, slots=True)
class UntargetedPartitionContext:
    """One run's cohort, subjects, peak receipts, and RAW paths."""

    processing_attempt_id: str
    samples: tuple[SampleMetadata, ...]
    method_subject: CidNlMethodSubject | UntargetedCidNlMethodSubjectBatch
    settings: DiscoverySettings
    raw_paths_by_sample: Mapping[str, Path]
    peaks_by_sample: Mapping[str, tuple[DiscoveryPeak, ...]]
    discovery_receipts_by_sample: Mapping[
        str,
        DiscoveryPeakUniverseReceipt,
    ]
    signal_processing_profile: SignalProcessingProfile
    acquisition_sources_by_sample: Mapping[str, AcquisitionSourceExpectation] = field(
        default_factory=dict, repr=False, compare=False
    )

    rt_frame: SampleRtFrame = field(init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "rt_frame",
            SampleRtFrame.from_samples(self.samples),
        )

    def sample(self, raw_stem: str) -> SampleMetadata:
        matches = tuple(
            sample for sample in self.samples if sample.raw_stem == raw_stem
        )
        if len(matches) != 1:
            raise IdentityContractError(
                f"sample {raw_stem!r} is not unique in the real-data context"
            )
        return matches[0]


def compose_partition_context(
    *,
    method_subject: CidNlMethodSubject | UntargetedCidNlMethodSubjectBatch,
    settings: DiscoverySettings,
    paths_by_stem: Mapping[str, Path],
    metadata_by_stem: Mapping[str, SampleMetadata],
    source_expectations: dict[str, AcquisitionSourceExpectation],
    peak_map: Mapping[str, tuple[DiscoveryPeak, ...]],
    receipt_map: Mapping[str, DiscoveryPeakUniverseReceipt],
    signal_processing_profile: SignalProcessingProfile,
) -> UntargetedPartitionContext:
    """Compose one run from input-owner verified facts, samples in injection order."""
    samples = tuple(
        metadata
        for _stem, metadata in sorted(
            metadata_by_stem.items(),
            key=lambda item: (item[1].injection_order, item[0]),
        )
    )
    orders = [sample.injection_order for sample in samples]
    if None in orders:
        raise IdentityContractError("sample metadata requires injection_order")
    if len(set(orders)) != len(orders):
        raise IdentityContractError("injection_order values must be unique")
    return UntargetedPartitionContext(
        processing_attempt_id=str(uuid.uuid4()),
        samples=samples,
        method_subject=method_subject,
        settings=settings,
        raw_paths_by_sample=dict(paths_by_stem),
        peaks_by_sample=dict(peak_map),
        discovery_receipts_by_sample=dict(receipt_map),
        signal_processing_profile=signal_processing_profile,
        acquisition_sources_by_sample=source_expectations,
    )


__all__ = (
    "UntargetedPartitionContext",
    "compose_partition_context",
)
