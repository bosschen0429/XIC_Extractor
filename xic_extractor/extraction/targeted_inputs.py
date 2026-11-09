from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, Mapping, Sequence
from dataclasses import asdict, dataclass
from pathlib import Path

from xic_extractor.analysis_set import AnalysisSet
from xic_extractor.configuration.models import ExtractionConfig
from xic_extractor.configuration.scientific_settings import scientific_settings_payload
from xic_extractor.configuration.signal_processing_profile import (
    SignalProcessingProfile,
)
from xic_extractor.configuration.target_identity import (
    TargetListSnapshot,
    target_list_snapshot_payload,
)
from xic_extractor.configuration.targeted_strategy import strategy_receipt
from xic_extractor.discovery.models import DiscoveryPeak
from xic_extractor.discovery.settings import (
    DiscoverySettings,
    discovery_settings_identity_fields,
)
from xic_extractor.peak_detection.search_scope import (
    PHYSICAL_CAPTURE_INPUT_KEY,
    physical_capture_input_payload,
)
from xic_extractor.processing_identity import (
    AcquisitionInputBinding,
    IdentityContractError,
    ProcessingRequestIdentity,
    build_local_acquisition_source_identity,
    build_processing_request_identity,
)
from xic_extractor.raw_sequence import read_analysis_set
from xic_extractor.raw_source_binding import (
    THERMO_RAW_ACQUISITION_FORMAT,
    THERMO_RAW_OBSERVATION_NAMESPACE,
)
from xic_extractor.sample_metadata import SampleMetadata

TARGETED_PROCESSING_INPUT_SCHEMA_VERSION = "targeted_extraction_inputs"
TARGETED_PROCESSING_ALGORITHM_VERSION = "targeted_extraction_algorithm"
TARGETED_PROCESSING_CONTRACT_VERSION = "targeted_processing_contract"
TARGETED_REAL_DATA_CANDIDATE_UNIVERSE_INPUT_KEY = (
    "targeted_real_data_candidate_universe"
)
TARGETED_REAL_DATA_CANDIDATE_UNIVERSE_SCHEMA_VERSION = (
    "targeted_real_data_candidate_universe"
)
TARGETED_REAL_DATA_DISCOVERY_SETTINGS_INPUT_KEY = (
    "targeted_real_data_discovery_settings"
)
TARGETED_REAL_DATA_DISCOVERY_SETTINGS_SCHEMA_VERSION = (
    "targeted_real_data_discovery_settings"
)


@dataclass(frozen=True)
class ResolvedTargetedInputs:
    injection_order: dict[str, int]
    sample_metadata: tuple[SampleMetadata, ...] = ()


def resolve_targeted_inputs(
    config: ExtractionConfig,
    *,
    raw_paths: list[Path],
    injection_order: dict[str, int] | None,
) -> ResolvedTargetedInputs:
    by_path = {entry.raw_path: entry for entry in _analysis_set(config).entries}
    selected = [by_path[path] for path in raw_paths]
    return ResolvedTargetedInputs(
        injection_order=(
            injection_order
            if injection_order is not None
            else {entry.raw_stem: entry.injection_order for entry in selected}
        ),
        sample_metadata=tuple(entry.metadata for entry in selected),
    )


def resolve_targeted_raw_paths(config: ExtractionConfig) -> list[Path]:
    return sorted(_analysis_set(config).raw_paths)


def _analysis_set(config: ExtractionConfig) -> AnalysisSet:
    # 上機資訊 annotates roles; the RAW headers own order and sample names.
    return read_analysis_set(
        config.data_dir,
        config.dll_dir,
        sequence_source=config.injection_order_source,
        include_context=False,
    )


def build_targeted_processing_request_identity(
    config: ExtractionConfig,
    *,
    target_snapshot: TargetListSnapshot,
    raw_paths: Sequence[Path],
    resolved_inputs: ResolvedTargetedInputs,
    current_discovery_settings: DiscoverySettings | None = None,
    current_discovery_candidates_by_sample: (
        Mapping[str, Sequence[DiscoveryPeak]] | None
    ) = None,
    should_cancel: Callable[[], bool] | None = None,
) -> ProcessingRequestIdentity:
    """Bind resolved Targeted inputs to local acquisition file revisions."""

    sources_by_stem = {}
    for raw_path in raw_paths:
        stem = raw_path.stem
        if stem in sources_by_stem:
            raise IdentityContractError(f"ambiguous acquisition source stem: {stem!r}")
        sources_by_stem[stem] = build_local_acquisition_source_identity(
            raw_path,
            acquisition_format=THERMO_RAW_ACQUISITION_FORMAT,
            observation_namespace=THERMO_RAW_OBSERVATION_NAMESPACE,
            should_cancel=should_cancel,
        )

    identity_injection_order = resolved_inputs.injection_order
    expected_stems = set(sources_by_stem)
    actual_stems = set(identity_injection_order)
    if actual_stems != expected_stems:
        missing = sorted(expected_stems - actual_stems)
        unexpected = sorted(actual_stems - expected_stems)
        raise IdentityContractError(
            "injection order must bind every acquisition source exactly once; "
            f"missing={missing!r}, unexpected={unexpected!r}"
        )

    bindings = tuple(
        AcquisitionInputBinding(
            acquisition_source_id=source.acquisition_source_id,
            adapter_input_key=stem,
            injection_order=identity_injection_order[stem],
        )
        for stem, source in sources_by_stem.items()
    )
    scientific_inputs = {
        "sample_metadata": [asdict(row) for row in resolved_inputs.sample_metadata],
        "strategy": strategy_receipt(),
        "settings": scientific_settings_payload(config),
        "target_list_snapshot": target_list_snapshot_payload(target_snapshot),
        TARGETED_REAL_DATA_CANDIDATE_UNIVERSE_INPUT_KEY: (
            targeted_real_data_candidate_universe_payload(
                current_discovery_candidates_by_sample,
                expected_sample_stems=tuple(sources_by_stem),
            )
        ),
    }
    if current_discovery_settings is not None:
        scientific_inputs[TARGETED_REAL_DATA_DISCOVERY_SETTINGS_INPUT_KEY] = (
            targeted_real_data_discovery_settings_payload(current_discovery_settings)
        )
    return build_processing_request_identity(
        acquisition_sources=tuple(sources_by_stem.values()),
        acquisition_bindings=bindings,
        scientific_input_schema_version=TARGETED_PROCESSING_INPUT_SCHEMA_VERSION,
        processing_algorithm_version=TARGETED_PROCESSING_ALGORITHM_VERSION,
        processing_contract_version=TARGETED_PROCESSING_CONTRACT_VERSION,
        scientific_inputs=scientific_inputs,
    )


def bind_targeted_physical_capture_request(
    request: ProcessingRequestIdentity,
    *,
    profile: SignalProcessingProfile,
) -> ProcessingRequestIdentity:
    """Mint the attempt-owning request after acquisition physical facts exist."""

    if not isinstance(request, ProcessingRequestIdentity):
        raise IdentityContractError("physical capture requires a Targeted request")
    scientific_inputs = json.loads(request.normalized_scientific_inputs_json)
    if PHYSICAL_CAPTURE_INPUT_KEY in scientific_inputs:
        raise IdentityContractError(
            "Targeted physical capture is already request-bound"
        )
    scientific_inputs[PHYSICAL_CAPTURE_INPUT_KEY] = physical_capture_input_payload(
        profile,
    )
    return build_processing_request_identity(
        acquisition_sources=request.acquisition_sources,
        acquisition_bindings=request.acquisition_bindings,
        scientific_input_schema_version=request.scientific_input_schema_version,
        processing_algorithm_version=request.processing_algorithm_version,
        processing_contract_version=request.processing_contract_version,
        scientific_inputs=scientific_inputs,
    )


def targeted_real_data_candidate_universe_payload(
    candidates_by_sample: Mapping[str, Sequence[DiscoveryPeak]] | None,
    *,
    expected_sample_stems: Sequence[str],
) -> dict[str, object]:
    """Bind one complete, order-insensitive Discovery universe per sample."""

    sample_stems = _canonical_candidate_universe_sample_stems(expected_sample_stems)
    if candidates_by_sample is not None and not isinstance(
        candidates_by_sample, Mapping
    ):
        raise IdentityContractError("candidate universe must be a sample-keyed mapping")
    declared = (
        {sample_stem: () for sample_stem in sample_stems}
        if candidates_by_sample is None
        else dict(candidates_by_sample)
    )
    if any(not isinstance(key, str) for key in declared):
        raise IdentityContractError("candidate universe sample keys must be text")
    if set(declared) != set(sample_stems):
        missing = sorted(set(sample_stems).difference(declared))
        unexpected = sorted(set(declared).difference(sample_stems))
        raise IdentityContractError(
            "candidate universe must bind every acquisition sample exactly once; "
            f"missing={missing!r}, unexpected={unexpected!r}"
        )
    payload: dict[str, object] = {
        "schema_version": TARGETED_REAL_DATA_CANDIDATE_UNIVERSE_SCHEMA_VERSION,
        "samples": [
            targeted_real_data_candidate_universe_entry_payload(
                sample_stem,
                declared[sample_stem],
            )
            for sample_stem in sample_stems
        ],
    }
    validate_targeted_real_data_candidate_universe_payload(
        payload,
        expected_sample_stems=sample_stems,
    )
    return payload


def validate_targeted_real_data_candidate_universe_payload(
    payload: object,
    *,
    expected_sample_stems: Sequence[str],
) -> tuple[dict[str, object], ...]:
    """Validate the complete persisted universe before runtime replay."""

    sample_stems = _canonical_candidate_universe_sample_stems(expected_sample_stems)
    if not isinstance(payload, dict) or set(payload) != {
        "schema_version",
        "samples",
    }:
        raise IdentityContractError(
            "candidate universe payload must use the exact envelope"
        )
    if (
        payload.get("schema_version")
        != TARGETED_REAL_DATA_CANDIDATE_UNIVERSE_SCHEMA_VERSION
    ):
        raise IdentityContractError(
            "candidate universe payload uses an unsupported schema version"
        )
    samples = payload.get("samples")
    if not isinstance(samples, list):
        raise IdentityContractError("candidate universe samples must be a list")
    typed_samples: list[dict[str, object]] = []
    for entry in samples:
        if not isinstance(entry, dict) or set(entry) != {
            "sample_stem",
            "candidate_count",
            "candidate_ids",
            "candidate_universe_sha256",
        }:
            raise IdentityContractError(
                "candidate universe sample entry must use the exact shape"
            )
        sample_stem = entry.get("sample_stem")
        candidate_count = entry.get("candidate_count")
        candidate_ids = entry.get("candidate_ids")
        digest = entry.get("candidate_universe_sha256")
        if (
            not isinstance(sample_stem, str)
            or not sample_stem.strip()
            or sample_stem != sample_stem.strip()
        ):
            raise IdentityContractError(
                "candidate universe sample entry requires a canonical sample stem"
            )
        if (
            isinstance(candidate_count, bool)
            or not isinstance(candidate_count, int)
            or candidate_count < 0
        ):
            raise IdentityContractError(
                "candidate universe candidate_count must be non-negative"
            )
        if not isinstance(candidate_ids, list) or any(
            not isinstance(item, str) or not item.strip() for item in candidate_ids
        ):
            raise IdentityContractError(
                "candidate universe candidate_ids must be non-empty text"
            )
        if candidate_ids != sorted(set(candidate_ids)) or len(candidate_ids) != (
            candidate_count
        ):
            raise IdentityContractError(
                "candidate universe IDs must be complete, unique, and canonical-sorted"
            )
        if (
            not isinstance(digest, str)
            or len(digest) != 64
            or any(character not in "0123456789abcdef" for character in digest)
        ):
            raise IdentityContractError(
                "candidate universe digest must be a lower-case SHA-256 value"
            )
        typed_samples.append(entry)
    actual_stems = tuple(entry["sample_stem"] for entry in typed_samples)
    if actual_stems != sample_stems:
        raise IdentityContractError(
            "candidate universe payload must contain one canonical entry per "
            "acquisition sample"
        )
    return tuple(typed_samples)


def _canonical_candidate_universe_sample_stems(
    expected_sample_stems: Sequence[str],
) -> tuple[str, ...]:
    if isinstance(expected_sample_stems, (str, bytes)) or any(
        not isinstance(item, str) or not item.strip() for item in expected_sample_stems
    ):
        raise IdentityContractError(
            "candidate universe requires non-empty acquisition sample stems"
        )
    sample_stems = tuple(sorted(expected_sample_stems))
    if not sample_stems or len(sample_stems) != len(set(sample_stems)):
        raise IdentityContractError(
            "candidate universe requires unique acquisition sample stems"
        )
    return sample_stems


def targeted_real_data_candidate_universe_entry_payload(
    sample_stem: str,
    candidates: Sequence[DiscoveryPeak],
) -> dict[str, object]:
    """Return the request-owned digest for one exact sample candidate set."""

    if not isinstance(sample_stem, str) or not sample_stem.strip():
        raise IdentityContractError("candidate universe sample_stem must be non-empty")
    if sample_stem != sample_stem.strip():
        raise IdentityContractError(
            "candidate universe sample_stem cannot contain surrounding whitespace"
        )
    if isinstance(candidates, (str, bytes)):
        raise IdentityContractError(
            "candidate universe must contain DiscoveryPeak values"
        )
    try:
        candidate_tuple = tuple(candidates)
    except TypeError as exc:
        raise IdentityContractError(
            "candidate universe must contain DiscoveryPeak values"
        ) from exc
    if any(not isinstance(item, DiscoveryPeak) for item in candidate_tuple):
        raise IdentityContractError(
            "candidate universe must contain DiscoveryPeak values"
        )
    if any(
        not isinstance(item.peak_id, str) or not item.peak_id.strip()
        for item in candidate_tuple
    ):
        raise IdentityContractError(
            "candidate universe candidate IDs must be non-empty text"
        )
    ordered = tuple(sorted(candidate_tuple, key=lambda item: item.peak_id))
    candidate_ids = tuple(item.peak_id for item in ordered)
    if len(candidate_ids) != len(set(candidate_ids)):
        raise IdentityContractError(
            "candidate universe candidate IDs must be unique within a sample"
        )
    for candidate in ordered:
        if candidate.sample_stem != sample_stem:
            raise IdentityContractError(
                f"{candidate.peak_id}: candidate belongs to a different sample"
            )
        if not isinstance(candidate.raw_file, Path) or (
            candidate.raw_file.stem != sample_stem
        ):
            raise IdentityContractError(
                f"{candidate.peak_id}: candidate RAW stem mismatches its sample"
            )
    canonical_candidates = [_candidate_identity_payload(item) for item in ordered]
    try:
        candidate_bytes = json.dumps(
            canonical_candidates,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise IdentityContractError(
            "candidate universe must have canonical JSON content"
        ) from exc
    return {
        "sample_stem": sample_stem,
        "candidate_count": len(ordered),
        "candidate_ids": list(candidate_ids),
        "candidate_universe_sha256": hashlib.sha256(candidate_bytes).hexdigest(),
    }


def _candidate_identity_payload(candidate: DiscoveryPeak) -> dict[str, object]:
    payload = asdict(candidate)
    payload.pop("raw_file")
    return payload


def targeted_real_data_discovery_settings_payload(
    settings: DiscoverySettings,
) -> dict[str, object]:
    """Snapshot all Discovery settings so future consumers cannot escape lineage."""

    if not isinstance(settings, DiscoverySettings):
        raise IdentityContractError(
            "current_discovery_settings must be DiscoverySettings"
        )
    raw_payload = {
        "schema_version": TARGETED_REAL_DATA_DISCOVERY_SETTINGS_SCHEMA_VERSION,
        "settings": discovery_settings_identity_fields(settings),
    }
    try:
        normalized = json.loads(
            json.dumps(
                raw_payload,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
                allow_nan=False,
            )
        )
    except (TypeError, ValueError, json.JSONDecodeError) as exc:
        raise IdentityContractError(
            "current_discovery_settings must have canonical JSON content"
        ) from exc
    if not isinstance(normalized, dict):
        raise IdentityContractError(
            "current_discovery_settings must have canonical JSON content"
        )
    return normalized
