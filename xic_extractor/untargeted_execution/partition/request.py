"""Bind an Untargeted partition cohort to its processing request and sources."""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path

from xic_extractor.alignment.rt_frame import SampleRtFrame
from xic_extractor.configuration.models import ExtractionConfig
from xic_extractor.configuration.scientific_settings import (
    scientific_settings_payload,
)
from xic_extractor.configuration.signal_processing_profile import (
    SignalProcessingProfile,
)
from xic_extractor.discovery.batch_contract import (
    ALIGNMENT_FEATURE_INPUT_KEY,
    PEAK_UNIVERSE_INPUT_KEY,
    PEAK_UNIVERSE_SCHEMA_VERSION,
    DiscoveryPeakUniverseReceipt,
    canonical_sample_stems,
    validate_discovery_peak_universe,
    validate_peak_universe_payload,
)
from xic_extractor.discovery.models import DiscoveryPeak
from xic_extractor.discovery.physical_fact_record import (
    DiscoveryPhysicalFactRecord,
    validate_discovery_physical_fact_record,
)
from xic_extractor.discovery.settings import (
    DiscoverySettings,
    discovery_settings_identity_fields,
)
from xic_extractor.peak_detection.cid_nl_method_subject import (
    REQUEST_INPUT_KEY,
    CidNlMethodSubject,
    UntargetedCidNlMethodSubjectBatch,
)
from xic_extractor.peak_detection.search_scope import (
    PHYSICAL_CAPTURE_INPUT_KEY,
    physical_capture_input_payload,
)
from xic_extractor.processing_identity import (
    AcquisitionInputBinding,
    AcquisitionSourceExpectation,
    AcquisitionSourceIdentity,
    IdentityContractError,
    ProcessingOwnerReferences,
    build_local_acquisition_source_identity,
    build_processing_request_identity,
    compose_processing_owner_references,
    issue_processing_attempt,
)
from xic_extractor.raw_source_binding import (
    THERMO_RAW_ACQUISITION_FORMAT,
    THERMO_RAW_OBSERVATION_NAMESPACE,
    stat_identity,
)
from xic_extractor.sample_metadata import (
    ResolvedCanonicalSampleIdentity,
    SampleMetadata,
    mint_sample_metadata_snapshot,
    resolve_canonical_sample_identity,
)

UNTARGETED_CID_NL_PROCESSING_INPUT_SCHEMA_VERSION = "untargeted_cid_nl_real_data_inputs"
UNTARGETED_CID_NL_PROCESSING_ALGORITHM_VERSION = "untargeted_cid_nl_real_data_algorithm"
UNTARGETED_CID_NL_PROCESSING_CONTRACT_VERSION = "untargeted_cid_nl_real_data_contract"
DISCOVERY_SETTINGS_INPUT_KEY = "current_discovery_settings"
DISCOVERY_SETTINGS_SCHEMA_VERSION = "current_discovery_settings"
PEAK_SETTINGS_INPUT_KEY = "peak_detection_settings"


@dataclass(frozen=True, slots=True)
class UntargetedPartitionContext:
    """One attempt's exact cohort, subject, peak receipts, and RAW paths."""

    processing_owners: ProcessingOwnerReferences
    samples: tuple[ResolvedCanonicalSampleIdentity, ...]
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
            SampleRtFrame.from_samples(self.processing_owners.sample_resolutions),
        )

    def sample(self, raw_stem: str) -> ResolvedCanonicalSampleIdentity:
        matches = tuple(
            sample for sample in self.samples if sample.metadata.raw_stem == raw_stem
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
    sample_identity_namespace: str,
    signal_processing_profile: SignalProcessingProfile,
    physical_records_by_peak: Mapping[str, DiscoveryPhysicalFactRecord] | None = None,
    acquisition_source_expectations: tuple[AcquisitionSourceExpectation, ...]
    | None = None,
    feature_candidate_ids: tuple[str, ...] | None = None,
) -> UntargetedPartitionContext:
    """Mint one replayable request before any real-data runtime RAW handle is opened."""

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
            or work.precursor_mz != peak.best_event_precursor_mz
            or work.product_mz != peak.best_event_product_mz
            or work.xic_rt_min != peak.ms1_search_rt_min
            or work.xic_rt_max != peak.ms1_search_rt_max
            or record.evaluation.prepared.profile != signal_processing_profile
        ):
            raise IdentityContractError(
                "physical replay crossed peak, acquisition, or profile"
            )
    candidate_universe = candidate_universe_payload(
        peak_map,
        discovery_receipts_by_sample=receipt_map,
        expected_sample_stems=tuple(paths_by_stem),
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
        sources_by_stem=sources_by_stem,
        source_expectations=source_expectations,
        peak_map=peak_map,
        receipt_map=receipt_map,
        physical_records=physical_records,
        sample_identity_namespace=sample_identity_namespace,
        signal_processing_profile=signal_processing_profile,
        candidate_universe=candidate_universe,
        feature_candidate_ids=feature_candidate_ids,
        physical_capture=(
            physical_capture_input_payload(
                signal_processing_profile,
            )
        ),
    )


def compose_partition_context(
    *,
    method_subject: CidNlMethodSubject | UntargetedCidNlMethodSubjectBatch,
    settings: DiscoverySettings,
    peak_config: ExtractionConfig,
    paths_by_stem: Mapping[str, Path],
    metadata_by_stem: Mapping[str, SampleMetadata],
    sources_by_stem: Mapping[str, AcquisitionSourceIdentity],
    source_expectations: dict[str, AcquisitionSourceExpectation],
    peak_map: Mapping[str, tuple[DiscoveryPeak, ...]],
    receipt_map: Mapping[str, DiscoveryPeakUniverseReceipt],
    physical_records: dict[str, DiscoveryPhysicalFactRecord],
    sample_identity_namespace: str,
    signal_processing_profile: SignalProcessingProfile,
    candidate_universe: dict[str, object],
    physical_capture: dict[str, object],
    feature_candidate_ids: tuple[str, ...] | None = None,
) -> UntargetedPartitionContext:
    """Compose request identity from input-owner verified compact facts."""
    ordered_stems = tuple(
        stem
        for stem, _metadata in sorted(
            metadata_by_stem.items(),
            key=lambda item: (item[1].injection_order, item[0]),
        )
    )
    bindings = tuple(
        AcquisitionInputBinding(
            acquisition_source_id=sources_by_stem[stem].acquisition_source_id,
            adapter_input_key=stem,
            injection_order=_required_injection_order(metadata_by_stem[stem]),
        )
        for stem in ordered_stems
    )
    request = build_processing_request_identity(
        acquisition_sources=tuple(sources_by_stem[stem] for stem in ordered_stems),
        acquisition_bindings=bindings,
        scientific_input_schema_version=(
            UNTARGETED_CID_NL_PROCESSING_INPUT_SCHEMA_VERSION
        ),
        processing_algorithm_version=(UNTARGETED_CID_NL_PROCESSING_ALGORITHM_VERSION),
        processing_contract_version=(UNTARGETED_CID_NL_PROCESSING_CONTRACT_VERSION),
        scientific_inputs={
            REQUEST_INPUT_KEY: method_subject.request_payload(),
            DISCOVERY_SETTINGS_INPUT_KEY: discovery_settings_payload(settings),
            PEAK_UNIVERSE_INPUT_KEY: candidate_universe,
            PEAK_SETTINGS_INPUT_KEY: scientific_settings_payload(peak_config),
            PHYSICAL_CAPTURE_INPUT_KEY: physical_capture,
            **(
                {ALIGNMENT_FEATURE_INPUT_KEY: list(feature_candidate_ids)}
                if feature_candidate_ids is not None
                else {}
            ),
        },
    )
    snapshot = mint_sample_metadata_snapshot(
        tuple(metadata_by_stem[stem] for stem in ordered_stems),
        identity_namespace=sample_identity_namespace,
    )
    samples = tuple(
        resolve_canonical_sample_identity(snapshot, stem) for stem in ordered_stems
    )
    owners = compose_processing_owner_references(
        request=request,
        attempt=issue_processing_attempt(),
        sample_snapshot=snapshot,
        sample_resolutions=samples,
    )
    return UntargetedPartitionContext(
        processing_owners=owners,
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


def discovery_settings_payload(settings: DiscoverySettings) -> dict[str, object]:
    if not isinstance(settings, DiscoverySettings):
        raise IdentityContractError("settings must be DiscoverySettings")
    return _canonical_json_mapping(
        {
            "schema_version": DISCOVERY_SETTINGS_SCHEMA_VERSION,
            "settings": discovery_settings_identity_fields(settings),
        },
        subject="Discovery settings",
    )


def candidate_universe_payload(
    peaks_by_sample: Mapping[str, Sequence[DiscoveryPeak]],
    *,
    discovery_receipts_by_sample: Mapping[
        str,
        DiscoveryPeakUniverseReceipt,
    ],
    expected_sample_stems: Sequence[str],
) -> dict[str, object]:
    declared = _peak_map(
        peaks_by_sample,
        expected_sample_stems=expected_sample_stems,
    )
    if set(discovery_receipts_by_sample) != set(declared):
        raise IdentityContractError(
            "Discovery receipts must bind every peak sample exactly once"
        )
    payload: dict[str, object] = {
        "schema_version": PEAK_UNIVERSE_SCHEMA_VERSION,
        "samples": [
            _peak_universe_entry(
                stem,
                declared[stem],
                discovery_receipts_by_sample[stem],
            )
            for stem in sorted(declared)
        ],
    }
    validate_peak_universe_payload(
        payload,
        expected_sample_stems=expected_sample_stems,
    )
    return payload


def _required_injection_order(row: SampleMetadata) -> int:
    if row.injection_order is None:
        raise IdentityContractError("sample metadata requires injection_order")
    return row.injection_order


def _peak_universe_entry(
    sample_stem: str,
    peaks: tuple[DiscoveryPeak, ...],
    receipt: DiscoveryPeakUniverseReceipt,
) -> dict[str, object]:
    ordered = tuple(sorted(peaks, key=lambda item: item.peak_id))
    candidate_ids = tuple(item.peak_id for item in ordered)
    if receipt.sample_stem != sample_stem:
        raise IdentityContractError(
            "Discovery receipt sample does not match the peak universe"
        )
    validate_discovery_peak_universe(
        receipt,
        acquisition_source=receipt.acquisition_source,
        peaks=ordered,
    )
    return {
        "sample_stem": sample_stem,
        "peak_count": len(ordered),
        "candidate_ids": list(candidate_ids),
        "peak_csv_schema_version": (receipt.peak_csv_schema_version),
        "peak_csv_sha256": receipt.peak_csv_sha256,
        "peak_csv_byte_size": receipt.peak_csv_byte_size,
        "acquisition_source_id": receipt.acquisition_source.acquisition_source_id,
    }


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
        if any(
            item.sample_stem != stem or item.raw_file.stem != stem for item in ordered
        ):
            raise IdentityContractError(
                f"peak universe for {stem!r} contains a different sample"
            )
        result[stem] = ordered
    return result


def _canonical_json_mapping(
    value: Mapping[str, object],
    *,
    subject: str,
) -> dict[str, object]:
    try:
        normalized = json.loads(
            json.dumps(
                value,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
                allow_nan=False,
            )
        )
    except (TypeError, ValueError, json.JSONDecodeError) as exc:
        raise IdentityContractError(
            f"{subject} must have canonical JSON content"
        ) from exc
    if not isinstance(normalized, dict):
        raise IdentityContractError(f"{subject} must be a mapping")
    return normalized


__all__ = (
    "DISCOVERY_SETTINGS_INPUT_KEY",
    "PEAK_SETTINGS_INPUT_KEY",
    "UNTARGETED_CID_NL_PROCESSING_ALGORITHM_VERSION",
    "UNTARGETED_CID_NL_PROCESSING_CONTRACT_VERSION",
    "UNTARGETED_CID_NL_PROCESSING_INPUT_SCHEMA_VERSION",
    "UntargetedPartitionContext",
    "build_partition_context",
    "candidate_universe_payload",
    "discovery_settings_payload",
)
