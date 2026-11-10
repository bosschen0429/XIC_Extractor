"""Bind an Untargeted run's cohort to its RAW sources and Discovery inputs."""

from __future__ import annotations

import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path

from xic_extractor.alignment.rt_frame import SampleRtFrame
from xic_extractor.configuration.models import ExtractionConfig
from xic_extractor.configuration.signal_processing_profile import (
    SignalProcessingProfile,
)
from xic_extractor.discovery.batch_contract import (
    DiscoveryPeakUniverseReceipt,
    canonical_sample_stems,
    validate_discovery_peak_universe,
)
from xic_extractor.discovery.models import DiscoveryPeak
from xic_extractor.discovery.physical_fact_record import (
    DiscoveryPhysicalFactRecord,
    validate_discovery_physical_fact_record,
)
from xic_extractor.discovery.settings import (
    DiscoverySettings,
)
from xic_extractor.peak_detection.cid_nl_method_subject import (
    CidNlMethodSubject,
    UntargetedCidNlMethodSubjectBatch,
)
from xic_extractor.processing_identity import (
    AcquisitionSourceExpectation,
    AcquisitionSourceIdentity,
    IdentityContractError,
    build_local_acquisition_source_identity,
)
from xic_extractor.raw_source_binding import (
    THERMO_RAW_ACQUISITION_FORMAT,
    THERMO_RAW_OBSERVATION_NAMESPACE,
    stat_identity,
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
    peak_config: ExtractionConfig
    raw_paths_by_sample: Mapping[str, Path]
    peaks_by_sample: Mapping[str, tuple[DiscoveryPeak, ...]]
    discovery_receipts_by_sample: Mapping[
        str,
        DiscoveryPeakUniverseReceipt,
    ]
    signal_processing_profile: SignalProcessingProfile
    physical_records_by_peak: Mapping[str, DiscoveryPhysicalFactRecord] = field(
        default_factory=dict
    )
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


def build_partition_context(
    *,
    raw_paths: Sequence[Path],
    sample_metadata: Sequence[SampleMetadata],
    method_subject: CidNlMethodSubject | UntargetedCidNlMethodSubjectBatch,
    settings: DiscoverySettings,
    peak_config: ExtractionConfig,
    peaks_by_sample: Mapping[str, Sequence[DiscoveryPeak]],
    discovery_receipts_by_sample: Mapping[
        str,
        DiscoveryPeakUniverseReceipt,
    ],
    signal_processing_profile: SignalProcessingProfile,
    physical_records_by_peak: Mapping[str, DiscoveryPhysicalFactRecord] | None = None,
    acquisition_source_expectations: tuple[AcquisitionSourceExpectation, ...]
    | None = None,
    feature_candidate_ids: tuple[str, ...] | None = None,
) -> UntargetedPartitionContext:
    """Bind one run's inputs before any real-data runtime RAW handle is opened."""

    if not isinstance(
        method_subject,
        (CidNlMethodSubject, UntargetedCidNlMethodSubjectBatch),
    ):
        raise IdentityContractError("method_subject must be owner-normalized")
    subjects = (
        method_subject.subjects
        if isinstance(method_subject, UntargetedCidNlMethodSubjectBatch)
        else (method_subject,)
    )
    if any(subject.subject_kind != "untargeted_precursor" for subject in subjects):
        raise IdentityContractError(
            "The real-data runtime requires an Untargeted CID-NL subject"
        )
    if not isinstance(settings, DiscoverySettings):
        raise IdentityContractError("settings must be DiscoverySettings")
    if not isinstance(peak_config, ExtractionConfig):
        raise IdentityContractError("peak_config must be ExtractionConfig")
    paths_by_stem = _raw_paths_by_stem(raw_paths)
    metadata_by_stem = _metadata_by_stem(sample_metadata)
    if set(metadata_by_stem) != set(paths_by_stem):
        raise IdentityContractError(
            "sample metadata must bind every RAW source exactly once"
        )
    peak_map = _peak_map(
        peaks_by_sample,
        expected_sample_stems=tuple(paths_by_stem),
    )
    admitted = (
        None if feature_candidate_ids is None else frozenset(feature_candidate_ids)
    )
    if isinstance(method_subject, UntargetedCidNlMethodSubjectBatch):
        candidate_ids = tuple(
            sorted(
                peak.peak_id
                for peaks in peak_map.values()
                for peak in peaks
                if admitted is None or peak.peak_id in admitted
            )
        )
        membership_ids = tuple(
            candidate_id
            for candidate_id, _method_subject_id in (
                method_subject.candidate_memberships
            )
        )
        if membership_ids != candidate_ids:
            raise IdentityContractError(
                "CID-NL subject batch must cover the exact Discovery peak universe"
            )
    sources_by_stem: dict[str, AcquisitionSourceIdentity] = {}
    source_expectations: dict[str, AcquisitionSourceExpectation] = {}
    if acquisition_source_expectations is not None:
        if not isinstance(acquisition_source_expectations, tuple) or len(
            acquisition_source_expectations
        ) != len(paths_by_stem):
            raise IdentityContractError(
                "source expectations must cover every RAW source exactly once"
            )
        expectations_by_stem = {
            path.stem: expectation
            for path, expectation in zip(
                raw_paths, acquisition_source_expectations, strict=True
            )
        }
        for stem, path in paths_by_stem.items():
            expectation = expectations_by_stem[stem]
            if not isinstance(expectation, AcquisitionSourceExpectation):
                raise IdentityContractError(
                    "source expectations must be owner-verified bindings"
                )
            source = expectation.source
            if (
                source.acquisition_format != THERMO_RAW_ACQUISITION_FORMAT
                or source.observation_namespace != THERMO_RAW_OBSERVATION_NAMESPACE
            ):
                raise IdentityContractError(
                    "source expectation uses the wrong acquisition contract"
                )
            if stat_identity(path.stat()) != expectation.path_identity:
                raise IdentityContractError(
                    "CID-NL RAW source changed after run-spec verification"
                )
            sources_by_stem[stem] = source
            source_expectations[stem] = expectation
    else:
        for stem, path in paths_by_stem.items():
            before = stat_identity(path.stat())
            source = build_local_acquisition_source_identity(
                path,
                acquisition_format=THERMO_RAW_ACQUISITION_FORMAT,
                observation_namespace=THERMO_RAW_OBSERVATION_NAMESPACE,
            )
            if stat_identity(path.stat()) != before:
                raise IdentityContractError(
                    "CID-NL RAW source changed during fingerprinting"
                )
            sources_by_stem[stem] = source
            source_expectations[stem] = AcquisitionSourceExpectation(
                source=source, path_identity=before
            )
    receipt_map = _discovery_receipt_map(
        discovery_receipts_by_sample,
        expected_sample_stems=tuple(paths_by_stem),
        sources_by_stem=sources_by_stem,
        peaks_by_stem=peak_map,
    )
    physical_records = dict(physical_records_by_peak or {})
    peak_by_id = {peak.peak_id: peak for peaks in peak_map.values() for peak in peaks}
    if not isinstance(signal_processing_profile, SignalProcessingProfile):
        raise IdentityContractError("physical replay profile must be owner-resolved")
    if physical_records.keys() != peak_by_id.keys():
        raise IdentityContractError(
            "physical replay must cover every measured Discovery peak"
        )
    for candidate_id, record in physical_records.items():
        validate_discovery_physical_fact_record(record)
        peak = peak_by_id[candidate_id]
        work = record.work_item
        if (
            record.peak_id != candidate_id
            or work.sample_stem != peak.sample_stem
            or work.acquisition_source_id
            != sources_by_stem[peak.sample_stem].acquisition_source_id
            or work.best_ms2_scan_id != peak.best_ms2_scan_id
            or record.evaluation.prepared.profile != signal_processing_profile
        ):
            raise IdentityContractError(
                "physical replay crossed peak, acquisition, or profile"
            )
    if admitted is not None:
        peak_map = {
            stem: tuple(c for c in values if c.peak_id in admitted)
            for stem, values in peak_map.items()
        }
        physical_records = {
            cid: record for cid, record in physical_records.items() if cid in admitted
        }
    return compose_partition_context(
        method_subject=method_subject,
        settings=settings,
        peak_config=peak_config,
        paths_by_stem=paths_by_stem,
        metadata_by_stem=metadata_by_stem,
        source_expectations=source_expectations,
        peak_map=peak_map,
        receipt_map=receipt_map,
        physical_records=physical_records,
        signal_processing_profile=signal_processing_profile,
    )


def compose_partition_context(
    *,
    method_subject: CidNlMethodSubject | UntargetedCidNlMethodSubjectBatch,
    settings: DiscoverySettings,
    peak_config: ExtractionConfig,
    paths_by_stem: Mapping[str, Path],
    metadata_by_stem: Mapping[str, SampleMetadata],
    source_expectations: dict[str, AcquisitionSourceExpectation],
    peak_map: Mapping[str, tuple[DiscoveryPeak, ...]],
    receipt_map: Mapping[str, DiscoveryPeakUniverseReceipt],
    physical_records: dict[str, DiscoveryPhysicalFactRecord],
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
        peak_config=peak_config,
        raw_paths_by_sample=dict(paths_by_stem),
        peaks_by_sample=dict(peak_map),
        discovery_receipts_by_sample=dict(receipt_map),
        signal_processing_profile=signal_processing_profile,
        physical_records_by_peak=physical_records,
        acquisition_sources_by_sample=source_expectations,
    )


def _discovery_receipt_map(
    receipts: Mapping[str, DiscoveryPeakUniverseReceipt],
    *,
    expected_sample_stems: Sequence[str],
    sources_by_stem: Mapping[str, object],
    peaks_by_stem: Mapping[str, tuple[DiscoveryPeak, ...]],
) -> dict[str, DiscoveryPeakUniverseReceipt]:
    expected = set(canonical_sample_stems(expected_sample_stems))
    if not isinstance(receipts, Mapping) or set(receipts) != expected:
        raise IdentityContractError(
            "Discovery receipts must bind every acquisition sample exactly once"
        )
    result: dict[str, DiscoveryPeakUniverseReceipt] = {}
    for stem in sorted(expected):
        receipt = receipts[stem]
        if not isinstance(receipt, DiscoveryPeakUniverseReceipt):
            raise IdentityContractError(
                "Discovery receipts must contain owner receipt values"
            )
        source = sources_by_stem[stem]
        if not isinstance(source, AcquisitionSourceIdentity):
            raise IdentityContractError(
                "acquisition source owner returned invalid data"
            )
        validate_discovery_peak_universe(
            receipt,
            acquisition_source=source,
            peaks=peaks_by_stem[stem],
        )
        result[stem] = receipt
    return result


def _raw_paths_by_stem(raw_paths: Sequence[Path]) -> dict[str, Path]:
    if isinstance(raw_paths, (str, bytes)):
        raise IdentityContractError("raw_paths must contain Path values")
    paths: dict[str, Path] = {}
    for raw_path in raw_paths:
        if not isinstance(raw_path, Path) or not raw_path.is_file():
            raise IdentityContractError("every RAW path must be an existing file")
        if raw_path.stem in paths:
            raise IdentityContractError(
                f"ambiguous acquisition source stem: {raw_path.stem!r}"
            )
        paths[raw_path.stem] = raw_path
    if not paths:
        raise IdentityContractError("at least one RAW path is required")
    return dict(sorted(paths.items()))


def _metadata_by_stem(
    rows: Sequence[SampleMetadata],
) -> dict[str, SampleMetadata]:
    if isinstance(rows, (str, bytes)):
        raise IdentityContractError("sample_metadata must contain owner rows")
    result: dict[str, SampleMetadata] = {}
    injection_orders: set[tuple[str, int]] = set()
    for row in rows:
        if not isinstance(row, SampleMetadata):
            raise IdentityContractError("sample_metadata contains a non-owner row")
        if not row.raw_stem or row.raw_stem in result:
            raise IdentityContractError("sample metadata RAW stems must be unique")
        if row.injection_order is None:
            raise IdentityContractError(
                "sample metadata injection orders must be complete and unique"
            )
        order_key = (row.batch_id, row.injection_order)
        if order_key in injection_orders:
            raise IdentityContractError(
                "sample metadata injection orders must be complete and unique"
            )
        result[row.raw_stem] = row
        injection_orders.add(order_key)
    return result


def _peak_map(
    peaks_by_sample: Mapping[str, Sequence[DiscoveryPeak]],
    *,
    expected_sample_stems: Sequence[str],
) -> dict[str, tuple[DiscoveryPeak, ...]]:
    if not isinstance(peaks_by_sample, Mapping):
        raise IdentityContractError("peak universe must be sample-keyed")
    expected = set(canonical_sample_stems(expected_sample_stems))
    if set(peaks_by_sample) != expected:
        raise IdentityContractError(
            "peak universe must bind every acquisition sample exactly once"
        )
    result: dict[str, tuple[DiscoveryPeak, ...]] = {}
    for stem in sorted(expected):
        peaks = tuple(peaks_by_sample[stem])
        if any(not isinstance(item, DiscoveryPeak) for item in peaks):
            raise IdentityContractError(
                "peak universe must contain DiscoveryPeak values"
            )
        ordered = tuple(sorted(peaks, key=lambda item: item.peak_id))
        ids = tuple(item.peak_id for item in ordered)
        if len(ids) != len(set(ids)):
            raise IdentityContractError("peak universe IDs must be unique")
        if any(item.sample_stem != stem for item in ordered):
            raise IdentityContractError(
                f"peak universe for {stem!r} contains a different sample"
            )
        result[stem] = ordered
    return result


__all__ = (
    "UntargetedPartitionContext",
    "build_partition_context",
    "compose_partition_context",
)
