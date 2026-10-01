from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, Mapping, Sequence
from dataclasses import asdict, dataclass
from pathlib import Path

from xic_extractor.configuration.models import ConfigError, ExtractionConfig
from xic_extractor.configuration.scientific_settings import SCIENTIFIC_SETTING_FIELDS
from xic_extractor.configuration.signal_processing_profile import (
    SignalProcessingProfile,
)
from xic_extractor.configuration.target_identity import (
    TargetListSnapshot,
    target_list_snapshot_payload,
)
from xic_extractor.discovery.models import DiscoveryCandidate, DiscoverySettings
from xic_extractor.extraction.rt_prior_library import (
    LibraryEntry,
    load_library,
    rt_prior_library_from_target_pair_calibration,
)
from xic_extractor.injection_rolling import read_injection_order
from xic_extractor.peak_detection.model_selection import (
    ExpectedDiffApprovalRecord,
)
from xic_extractor.peak_detection.model_selection_approval_registry import (
    load_expected_diff_approval_registry,
)
from xic_extractor.peak_detection.physical_peak_models import PhysicalPeakEvaluation
from xic_extractor.peak_detection.search_scope import (
    PHYSICAL_CAPTURE_INPUT_KEY,
    physical_capture_input_payload,
)
from xic_extractor.processing_identity import (
    AcquisitionInputBinding,
    IdentityContractError,
    ProcessingRequestIdentity,
    build_acquisition_source_identity,
    build_processing_request_identity,
    fingerprint_file_content,
)
from xic_extractor.raw_reader import (
    THERMO_RAW_ACQUISITION_FORMAT,
    THERMO_RAW_OBSERVATION_NAMESPACE,
    read_analysis_set,
)
from xic_extractor.sample_metadata import (
    is_sample_metadata_source,
    load_sample_metadata,
    sample_metadata_to_injection_order,
    sample_metadata_to_raw_stem_injection_order,
)
from xic_extractor.target_pair_rt_calibration import load_target_pair_rt_calibration

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
    rt_prior_library: dict[tuple[str, str], LibraryEntry]
    model_selection_expected_diff_approvals: (
        Mapping[str, ExpectedDiffApprovalRecord] | None
    )
    acquisition_injection_order: dict[str, int] | None = None


def resolve_targeted_inputs(
    config: ExtractionConfig,
    *,
    raw_paths: list[Path],
    injection_order: dict[str, int] | None,
    rt_prior_library: dict[tuple[str, str], LibraryEntry] | None,
    model_selection_expected_diff_approvals: (
        Mapping[str, ExpectedDiffApprovalRecord] | None
    ),
) -> ResolvedTargetedInputs:
    resolved_injection_order, acquisition_injection_order = _resolve_injection_orders(
        config,
        raw_paths,
        injection_order,
    )
    return ResolvedTargetedInputs(
        injection_order=resolved_injection_order,
        rt_prior_library=resolve_rt_prior_library(config, rt_prior_library),
        model_selection_expected_diff_approvals=(
            model_selection_expected_diff_approvals
        ),
        acquisition_injection_order=acquisition_injection_order,
    )


def resolve_targeted_raw_paths(config: ExtractionConfig) -> list[Path]:
    source = config.injection_order_source
    metadata = (
        load_sample_metadata(source)
        if source is not None and is_sample_metadata_source(source)
        else None
    )
    return sorted(
        read_analysis_set(config.data_dir, config.dll_dir, metadata).raw_paths
    )


def build_targeted_processing_request_identity(
    config: ExtractionConfig,
    *,
    target_snapshot: TargetListSnapshot,
    raw_paths: Sequence[Path],
    resolved_inputs: ResolvedTargetedInputs,
    current_discovery_settings: DiscoverySettings | None = None,
    current_discovery_candidates_by_sample: (
        Mapping[str, Sequence[DiscoveryCandidate]] | None
    ) = None,
    should_cancel: Callable[[], bool] | None = None,
) -> ProcessingRequestIdentity:
    """Bind resolved Targeted extraction inputs to exact acquisition content."""

    sources_by_stem = {}
    for raw_path in raw_paths:
        stem = raw_path.stem
        if stem in sources_by_stem:
            raise IdentityContractError(f"ambiguous acquisition source stem: {stem!r}")
        sources_by_stem[stem] = build_acquisition_source_identity(
            raw_path,
            acquisition_format=THERMO_RAW_ACQUISITION_FORMAT,
            observation_namespace=THERMO_RAW_OBSERVATION_NAMESPACE,
            should_cancel=should_cancel,
        )

    identity_injection_order = (
        resolved_inputs.acquisition_injection_order
        if resolved_inputs.acquisition_injection_order is not None
        else resolved_inputs.injection_order
    )
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
        "settings": {
            field: getattr(config, field)
            for field in SCIENTIFIC_SETTING_FIELDS
        },
        "target_list_snapshot": target_list_snapshot_payload(target_snapshot),
        "rt_prior_library": _rt_prior_snapshot(resolved_inputs.rt_prior_library),
        "model_selection_expected_diff_approvals": _approval_snapshot(
            resolved_inputs.model_selection_expected_diff_approvals
        ),
        "targeted_ms1_shape_identity_support": (
            _targeted_ms1_shape_identity_support_snapshot(config)
        ),
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
    evaluations: Mapping[str, PhysicalPeakEvaluation],
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
        evaluations,
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
    candidates_by_sample: Mapping[str, Sequence[DiscoveryCandidate]] | None,
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
    candidates: Sequence[DiscoveryCandidate],
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
            "candidate universe must contain DiscoveryCandidate values"
        )
    try:
        candidate_tuple = tuple(candidates)
    except TypeError as exc:
        raise IdentityContractError(
            "candidate universe must contain DiscoveryCandidate values"
        ) from exc
    if any(not isinstance(item, DiscoveryCandidate) for item in candidate_tuple):
        raise IdentityContractError(
            "candidate universe must contain DiscoveryCandidate values"
        )
    if any(
        not isinstance(item.candidate_id, str) or not item.candidate_id.strip()
        for item in candidate_tuple
    ):
        raise IdentityContractError(
            "candidate universe candidate IDs must be non-empty text"
        )
    ordered = tuple(sorted(candidate_tuple, key=lambda item: item.candidate_id))
    candidate_ids = tuple(item.candidate_id for item in ordered)
    if len(candidate_ids) != len(set(candidate_ids)):
        raise IdentityContractError(
            "candidate universe candidate IDs must be unique within a sample"
        )
    for candidate in ordered:
        if candidate.sample_stem != sample_stem:
            raise IdentityContractError(
                f"{candidate.candidate_id}: candidate belongs to a different sample"
            )
        if not isinstance(candidate.raw_file, Path) or (
            candidate.raw_file.stem != sample_stem
        ):
            raise IdentityContractError(
                f"{candidate.candidate_id}: candidate RAW stem mismatches its sample"
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


def _candidate_identity_payload(candidate: DiscoveryCandidate) -> dict[str, object]:
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
        "settings": asdict(settings),
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


def _rt_prior_snapshot(
    library: Mapping[tuple[str, str], LibraryEntry],
) -> list[dict[str, object]]:
    snapshot: list[dict[str, object]] = []
    for (target_label, role), entry in sorted(library.items()):
        if (entry.target_label, entry.role) != (target_label, role):
            raise IdentityContractError(
                "RT-prior mapping key must match the bound LibraryEntry"
            )
        snapshot.append(
            {
                "target_label": target_label,
                "role": role,
                "istd_pair": entry.istd_pair,
                "median_delta_rt": entry.median_delta_rt,
                "sigma_delta_rt": entry.sigma_delta_rt,
                "median_abs_rt": entry.median_abs_rt,
                "sigma_abs_rt": entry.sigma_abs_rt,
            }
        )
    return snapshot


def _approval_snapshot(
    approvals: Mapping[str, ExpectedDiffApprovalRecord] | None,
) -> list[dict[str, object]]:
    snapshot: list[dict[str, object]] = []
    for stable_row_id, approval in sorted((approvals or {}).items()):
        if approval.stable_row_id != stable_row_id:
            raise IdentityContractError(
                "approval mapping key must match the bound approval record"
            )
        snapshot.append(asdict(approval))
    return snapshot


def _targeted_ms1_shape_identity_support_snapshot(
    config: ExtractionConfig,
) -> dict[str, object] | None:
    path = config.targeted_ms1_shape_identity_support_tsv
    if path is None:
        return None
    fingerprint = fingerprint_file_content(path)
    return {
        "schema_version": "targeted_ms1_shape_identity_support_input",
        "activation_policy": config.targeted_ms1_shape_identity_activation_policy,
        "content_sha256": fingerprint.content_sha256,
        "byte_size": fingerprint.byte_size,
    }


def resolve_injection_order(
    config: ExtractionConfig,
    raw_paths: list[Path],
    injection_order: dict[str, int] | None,
) -> dict[str, int]:
    resolved_injection_order, _ = _resolve_injection_orders(
        config,
        raw_paths,
        injection_order,
    )
    return resolved_injection_order


def _resolve_injection_orders(
    config: ExtractionConfig,
    raw_paths: list[Path],
    injection_order: dict[str, int] | None,
) -> tuple[dict[str, int], dict[str, int]]:
    if injection_order is not None:
        return injection_order, injection_order
    if config.injection_order_source is not None:
        return _read_injection_order_source(config.injection_order_source)
    fallback = fallback_injection_order_from_mtime(raw_paths)
    return fallback, fallback


def _read_injection_order_source(
    path: Path,
) -> tuple[dict[str, int], dict[str, int]]:
    if is_sample_metadata_source(path):
        rows = load_sample_metadata(path)
        return (
            sample_metadata_to_injection_order(rows),
            sample_metadata_to_raw_stem_injection_order(rows),
        )
    injection_order = read_injection_order(path)
    return injection_order, injection_order


def resolve_rt_prior_library(
    config: ExtractionConfig,
    rt_prior_library: dict[tuple[str, str], LibraryEntry] | None,
) -> dict[tuple[str, str], LibraryEntry]:
    if config.target_pair_rt_calibration_path is not None:
        calibration_rows = load_target_pair_rt_calibration(
            config.target_pair_rt_calibration_path,
            expected_target_config_hash=config.target_config_hash or None,
        )
        return rt_prior_library_from_target_pair_calibration(calibration_rows)
    if rt_prior_library is not None:
        return rt_prior_library
    if config.rt_prior_library_path is None:
        return {}
    return load_library(config.rt_prior_library_path, config.config_hash)


def resolve_model_selection_expected_diff_approvals(
    config: ExtractionConfig,
    approvals: Mapping[str, ExpectedDiffApprovalRecord] | None,
) -> Mapping[str, ExpectedDiffApprovalRecord] | None:
    if approvals is not None:
        return approvals
    registry_path = config.model_selection_expected_diff_approval_registry
    if registry_path is None:
        return None
    try:
        return load_expected_diff_approval_registry(registry_path)
    except ValueError as exc:
        raise ConfigError(str(exc)) from exc


def fallback_injection_order_from_mtime(raw_paths: list[Path]) -> dict[str, int]:
    ordered_paths = sorted(
        raw_paths,
        key=lambda path: (path.stat().st_mtime, path.name),
    )
    return {path.stem: index for index, path in enumerate(ordered_paths, start=1)}
