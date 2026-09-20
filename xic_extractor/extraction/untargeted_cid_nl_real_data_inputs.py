"""Request owner for replayable Untargeted CID-NL real-data work."""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass, field
from pathlib import Path

from xic_extractor.config import ExtractionConfig
from xic_extractor.discovery.batch_contract import (
    DISCOVERY_CANDIDATE_ARTIFACT_SCHEMA_VERSION,
    DiscoveryCandidateUniverseReceipt,
    validate_discovery_candidate_universe,
)
from xic_extractor.discovery.models import DiscoveryCandidate, DiscoverySettings
from xic_extractor.discovery.physical_fact_record import (
    DiscoveryPhysicalFactRecord,
    validate_discovery_physical_fact_record,
)
from xic_extractor.extraction.targeted_inputs import (
    TARGETED_SCIENTIFIC_SETTING_FIELDS,
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
from xic_extractor.peak_detection.signal_processing_profile import (
    SignalProcessingProfile,
)
from xic_extractor.processing_identity import (
    AcquisitionInputBinding,
    AcquisitionSourceExpectation,
    AcquisitionSourceIdentity,
    IdentityContractError,
    ProcessingOwnerReferences,
    build_acquisition_source_identity,
    build_processing_request_identity,
    compose_processing_owner_references,
    issue_processing_attempt,
)
from xic_extractor.raw_reader import (
    THERMO_RAW_ACQUISITION_FORMAT,
    THERMO_RAW_OBSERVATION_NAMESPACE,
    _stat_identity,
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
CANDIDATE_UNIVERSE_INPUT_KEY = "current_discovery_candidate_universe"
CANDIDATE_UNIVERSE_SCHEMA_VERSION = "current_discovery_candidate_universe"
PEAK_SETTINGS_INPUT_KEY = "peak_detection_settings"


@dataclass(frozen=True, slots=True)
class UntargetedCidNlRealDataContext:
    """One attempt's exact cohort, subject, candidate receipts, and RAW paths."""

    processing_owners: ProcessingOwnerReferences
    samples: tuple[ResolvedCanonicalSampleIdentity, ...]
    method_subject: CidNlMethodSubject | UntargetedCidNlMethodSubjectBatch
    settings: DiscoverySettings
    peak_config: ExtractionConfig
    raw_paths_by_sample: Mapping[str, Path]
    candidates_by_sample: Mapping[str, tuple[DiscoveryCandidate, ...]]
    discovery_receipts_by_sample: Mapping[
        str,
        DiscoveryCandidateUniverseReceipt,
    ]
    signal_processing_profile: SignalProcessingProfile | None = None
    physical_records_by_candidate: Mapping[str, DiscoveryPhysicalFactRecord] = field(
        default_factory=dict
    )
    acquisition_sources_by_sample: Mapping[str, AcquisitionSourceExpectation] = field(
        default_factory=dict, repr=False, compare=False
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


def build_untargeted_cid_nl_real_data_context(
    *,
    raw_paths: Sequence[Path],
    sample_metadata: Sequence[SampleMetadata],
    method_subject: CidNlMethodSubject | UntargetedCidNlMethodSubjectBatch,
    settings: DiscoverySettings,
    peak_config: ExtractionConfig,
    candidates_by_sample: Mapping[str, Sequence[DiscoveryCandidate]],
    discovery_receipts_by_sample: Mapping[
        str,
        DiscoveryCandidateUniverseReceipt,
    ],
    sample_identity_namespace: str,
    signal_processing_profile: SignalProcessingProfile | None = None,
    physical_records_by_candidate: Mapping[str, DiscoveryPhysicalFactRecord]
    | None = None,
    acquisition_source_expectations: tuple[AcquisitionSourceExpectation, ...]
    | None = None,
) -> UntargetedCidNlRealDataContext:
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
    candidate_map = _candidate_map(
        candidates_by_sample,
        expected_sample_stems=tuple(paths_by_stem),
    )
    if isinstance(method_subject, UntargetedCidNlMethodSubjectBatch):
        candidate_ids = tuple(
            sorted(
                candidate.candidate_id
                for candidates in candidate_map.values()
                for candidate in candidates
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
                "CID-NL subject batch must cover the exact Discovery candidate universe"
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
            if _stat_identity(path.stat()) != expectation.path_identity:
                raise IdentityContractError(
                    "CID-NL RAW source changed after run-spec verification"
                )
            sources_by_stem[stem] = source
            source_expectations[stem] = expectation
    else:
        for stem, path in paths_by_stem.items():
            before = _stat_identity(path.stat())
            source = build_acquisition_source_identity(
                path,
                acquisition_format=THERMO_RAW_ACQUISITION_FORMAT,
                observation_namespace=THERMO_RAW_OBSERVATION_NAMESPACE,
            )
            if _stat_identity(path.stat()) != before:
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
        candidates_by_stem=candidate_map,
    )
    physical_records = dict(physical_records_by_candidate or {})
    candidate_by_id = {
        candidate.candidate_id: candidate
        for candidates in candidate_map.values()
        for candidate in candidates
    }
    if signal_processing_profile is None:
        if physical_records:
            raise IdentityContractError(
                "physical replay records require a signal profile"
            )
    else:
        if not isinstance(signal_processing_profile, SignalProcessingProfile):
            raise IdentityContractError(
                "physical replay profile must be owner-resolved"
            )
        if set(physical_records) != set(candidate_by_id):
            raise IdentityContractError(
                "physical replay must cover the exact candidate universe"
            )
        for candidate_id, record in physical_records.items():
            validate_discovery_physical_fact_record(record)
            candidate = candidate_by_id[candidate_id]
            work = record.work_item
            if (
                record.candidate_id != candidate_id
                or work.sample_stem != candidate.sample_stem
                or work.acquisition_source_id
                != sources_by_stem[candidate.sample_stem].acquisition_source_id
                or work.best_ms2_scan_id != candidate.best_ms2_scan_id
                or work.precursor_mz != candidate.precursor_mz
                or work.product_mz != candidate.product_mz
                or work.query_rt_min != candidate.best_seed_rt
                or work.xic_rt_min != candidate.ms1_search_rt_min
                or work.xic_rt_max != candidate.ms1_search_rt_max
                or record.evaluation.prepared.profile_id
                != signal_processing_profile.profile_id
            ):
                raise IdentityContractError(
                    "physical replay crossed candidate, acquisition, or profile"
                )
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
            CANDIDATE_UNIVERSE_INPUT_KEY: candidate_universe_payload(
                candidate_map,
                discovery_receipts_by_sample=receipt_map,
                expected_sample_stems=ordered_stems,
            ),
            PEAK_SETTINGS_INPUT_KEY: {
                field: getattr(peak_config, field)
                for field in TARGETED_SCIENTIFIC_SETTING_FIELDS
            },
            **(
                {
                    PHYSICAL_CAPTURE_INPUT_KEY: physical_capture_input_payload(
                        signal_processing_profile,
                        {
                            candidate_id: record.evaluation
                            for candidate_id, record in physical_records.items()
                        },
                    )
                }
                if signal_processing_profile is not None
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
        attempt=issue_processing_attempt(request),
        sample_snapshot=snapshot,
        sample_resolutions=samples,
    )
    return UntargetedCidNlRealDataContext(
        processing_owners=owners,
        samples=samples,
        method_subject=method_subject,
        settings=settings,
        peak_config=peak_config,
        raw_paths_by_sample=dict(paths_by_stem),
        candidates_by_sample=dict(candidate_map),
        discovery_receipts_by_sample=dict(receipt_map),
        signal_processing_profile=signal_processing_profile,
        physical_records_by_candidate=physical_records,
        acquisition_sources_by_sample=source_expectations,
    )


def discovery_settings_payload(settings: DiscoverySettings) -> dict[str, object]:
    if not isinstance(settings, DiscoverySettings):
        raise IdentityContractError("settings must be DiscoverySettings")
    return _canonical_json_mapping(
        {
            "schema_version": DISCOVERY_SETTINGS_SCHEMA_VERSION,
            "settings": asdict(settings),
        },
        subject="Discovery settings",
    )


def candidate_universe_payload(
    candidates_by_sample: Mapping[str, Sequence[DiscoveryCandidate]],
    *,
    discovery_receipts_by_sample: Mapping[
        str,
        DiscoveryCandidateUniverseReceipt,
    ],
    expected_sample_stems: Sequence[str],
) -> dict[str, object]:
    declared = _candidate_map(
        candidates_by_sample,
        expected_sample_stems=expected_sample_stems,
    )
    if set(discovery_receipts_by_sample) != set(declared):
        raise IdentityContractError(
            "Discovery receipts must bind every candidate sample exactly once"
        )
    payload: dict[str, object] = {
        "schema_version": CANDIDATE_UNIVERSE_SCHEMA_VERSION,
        "samples": [
            _candidate_universe_entry(
                stem,
                declared[stem],
                discovery_receipts_by_sample[stem],
            )
            for stem in sorted(declared)
        ],
    }
    validate_candidate_universe_payload(
        payload,
        expected_sample_stems=expected_sample_stems,
    )
    return payload


def _required_injection_order(row: SampleMetadata) -> int:
    if row.injection_order is None:
        raise IdentityContractError("sample metadata requires injection_order")
    return row.injection_order


def validate_candidate_universe_payload(
    payload: object,
    *,
    expected_sample_stems: Sequence[str],
) -> tuple[dict[str, object], ...]:
    expected = tuple(sorted(_canonical_sample_stems(expected_sample_stems)))
    if not isinstance(payload, dict) or set(payload) != {
        "schema_version",
        "samples",
    }:
        raise IdentityContractError("candidate universe must use the exact envelope")
    if payload.get("schema_version") != CANDIDATE_UNIVERSE_SCHEMA_VERSION:
        raise IdentityContractError("candidate universe schema is unsupported")
    samples = payload.get("samples")
    if not isinstance(samples, list):
        raise IdentityContractError("candidate universe samples must be a list")
    typed: list[dict[str, object]] = []
    for entry in samples:
        if not isinstance(entry, dict) or set(entry) != {
            "sample_stem",
            "candidate_count",
            "candidate_ids",
            "candidate_payload_sha256",
            "discovery_receipt_id",
            "candidate_artifact_schema_version",
            "candidate_artifact_sha256",
            "candidate_artifact_byte_size",
            "acquisition_source_id",
        }:
            raise IdentityContractError(
                "candidate universe entry must use the exact current shape"
            )
        stem = entry.get("sample_stem")
        count = entry.get("candidate_count")
        ids = entry.get("candidate_ids")
        digest = entry.get("candidate_payload_sha256")
        if not isinstance(stem, str) or not stem.strip() or stem != stem.strip():
            raise IdentityContractError("candidate universe sample stem is invalid")
        if (
            isinstance(count, bool)
            or not isinstance(count, int)
            or count < 0
            or not isinstance(ids, list)
            or ids != sorted(set(ids))
            or count != len(ids)
            or any(not isinstance(item, str) or not item.strip() for item in ids)
        ):
            raise IdentityContractError("candidate universe IDs are invalid")
        if (
            not isinstance(digest, str)
            or len(digest) != 64
            or any(character not in "0123456789abcdef" for character in digest)
        ):
            raise IdentityContractError("candidate universe digest is invalid")
        if (
            not isinstance(entry.get("discovery_receipt_id"), str)
            or not entry["discovery_receipt_id"].startswith(
                "xic.discovery-candidate-universe:"
            )
            or not isinstance(entry.get("candidate_artifact_schema_version"), str)
            or entry["candidate_artifact_schema_version"]
            != DISCOVERY_CANDIDATE_ARTIFACT_SCHEMA_VERSION
            or not _is_digest(entry.get("candidate_artifact_sha256"))
            or isinstance(entry.get("candidate_artifact_byte_size"), bool)
            or not isinstance(entry.get("candidate_artifact_byte_size"), int)
            or entry["candidate_artifact_byte_size"] < 0
            or not isinstance(entry.get("acquisition_source_id"), str)
            or not entry["acquisition_source_id"].startswith("xic.acquisition-source:")
        ):
            raise IdentityContractError(
                "candidate universe owner receipt facts are invalid"
            )
        typed.append(entry)
    if tuple(entry["sample_stem"] for entry in typed) != expected:
        raise IdentityContractError(
            "candidate universe must bind every acquisition sample exactly once"
        )
    return tuple(typed)


def _candidate_universe_entry(
    sample_stem: str,
    candidates: tuple[DiscoveryCandidate, ...],
    receipt: DiscoveryCandidateUniverseReceipt,
) -> dict[str, object]:
    ordered = tuple(sorted(candidates, key=lambda item: item.candidate_id))
    candidate_ids = tuple(item.candidate_id for item in ordered)
    if receipt.sample_stem != sample_stem:
        raise IdentityContractError(
            "Discovery receipt sample does not match the candidate universe"
        )
    validate_discovery_candidate_universe(
        receipt,
        acquisition_source=receipt.acquisition_source,
        candidates=ordered,
    )
    return {
        "sample_stem": sample_stem,
        "candidate_count": len(ordered),
        "candidate_ids": list(candidate_ids),
        "candidate_payload_sha256": receipt.candidate_payload_sha256,
        "discovery_receipt_id": receipt.discovery_receipt_id,
        "candidate_artifact_schema_version": (
            receipt.candidate_artifact_schema_version
        ),
        "candidate_artifact_sha256": receipt.candidate_artifact_sha256,
        "candidate_artifact_byte_size": receipt.candidate_artifact_byte_size,
        "acquisition_source_id": receipt.acquisition_source.acquisition_source_id,
    }


def _discovery_receipt_map(
    receipts: Mapping[str, DiscoveryCandidateUniverseReceipt],
    *,
    expected_sample_stems: Sequence[str],
    sources_by_stem: Mapping[str, object],
    candidates_by_stem: Mapping[str, tuple[DiscoveryCandidate, ...]],
) -> dict[str, DiscoveryCandidateUniverseReceipt]:
    expected = set(_canonical_sample_stems(expected_sample_stems))
    if not isinstance(receipts, Mapping) or set(receipts) != expected:
        raise IdentityContractError(
            "Discovery receipts must bind every acquisition sample exactly once"
        )
    result: dict[str, DiscoveryCandidateUniverseReceipt] = {}
    for stem in sorted(expected):
        receipt = receipts[stem]
        if not isinstance(receipt, DiscoveryCandidateUniverseReceipt):
            raise IdentityContractError(
                "Discovery receipts must contain owner receipt values"
            )
        source = sources_by_stem[stem]
        if not isinstance(source, AcquisitionSourceIdentity):
            raise IdentityContractError(
                "acquisition source owner returned invalid data"
            )
        validate_discovery_candidate_universe(
            receipt,
            acquisition_source=source,
            candidates=candidates_by_stem[stem],
        )
        result[stem] = receipt
    return result


def _is_digest(value: object) -> bool:
    return (
        isinstance(value, str)
        and len(value) == 64
        and all(character in "0123456789abcdef" for character in value)
    )


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


def _candidate_map(
    candidates_by_sample: Mapping[str, Sequence[DiscoveryCandidate]],
    *,
    expected_sample_stems: Sequence[str],
) -> dict[str, tuple[DiscoveryCandidate, ...]]:
    if not isinstance(candidates_by_sample, Mapping):
        raise IdentityContractError("candidate universe must be sample-keyed")
    expected = set(_canonical_sample_stems(expected_sample_stems))
    if set(candidates_by_sample) != expected:
        raise IdentityContractError(
            "candidate universe must bind every acquisition sample exactly once"
        )
    result: dict[str, tuple[DiscoveryCandidate, ...]] = {}
    for stem in sorted(expected):
        candidates = tuple(candidates_by_sample[stem])
        if any(not isinstance(item, DiscoveryCandidate) for item in candidates):
            raise IdentityContractError(
                "candidate universe must contain DiscoveryCandidate values"
            )
        ordered = tuple(sorted(candidates, key=lambda item: item.candidate_id))
        ids = tuple(item.candidate_id for item in ordered)
        if len(ids) != len(set(ids)):
            raise IdentityContractError("candidate universe IDs must be unique")
        if any(
            item.sample_stem != stem or item.raw_file.stem != stem for item in ordered
        ):
            raise IdentityContractError(
                f"candidate universe for {stem!r} contains a different sample"
            )
        result[stem] = ordered
    return result


def _canonical_sample_stems(values: Sequence[str]) -> tuple[str, ...]:
    if isinstance(values, (str, bytes)):
        raise IdentityContractError("sample stems must be a sequence")
    stems = tuple(values)
    if (
        not stems
        or len(stems) != len(set(stems))
        or any(not isinstance(item, str) or not item.strip() for item in stems)
    ):
        raise IdentityContractError("sample stems must be unique non-empty text")
    return stems


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
    "CANDIDATE_UNIVERSE_INPUT_KEY",
    "DISCOVERY_SETTINGS_INPUT_KEY",
    "PEAK_SETTINGS_INPUT_KEY",
    "UNTARGETED_CID_NL_PROCESSING_ALGORITHM_VERSION",
    "UNTARGETED_CID_NL_PROCESSING_CONTRACT_VERSION",
    "UNTARGETED_CID_NL_PROCESSING_INPUT_SCHEMA_VERSION",
    "UntargetedCidNlRealDataContext",
    "build_untargeted_cid_nl_real_data_context",
    "candidate_universe_payload",
    "discovery_settings_payload",
    "validate_candidate_universe_payload",
)
