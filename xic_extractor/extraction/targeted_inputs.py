from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass
from pathlib import Path

from xic_extractor.config import ConfigError, ExtractionConfig
from xic_extractor.configuration.target_identity import (
    TargetListSnapshot,
    target_list_snapshot_payload,
)
from xic_extractor.injection_rolling import read_injection_order
from xic_extractor.peak_detection.model_selection import (
    ExpectedDiffApprovalRecord,
)
from xic_extractor.peak_detection.model_selection_approval_registry import (
    load_expected_diff_approval_registry,
)
from xic_extractor.processing_identity import (
    AcquisitionInputBinding,
    IdentityContractError,
    ProcessingRequestIdentity,
    build_acquisition_source_identity,
    build_processing_request_identity,
    fingerprint_file_content,
)
from xic_extractor.rt_prior_library import LibraryEntry, load_library
from xic_extractor.sample_metadata import (
    is_sample_metadata_source,
    load_sample_metadata,
    sample_metadata_to_raw_stem_injection_order,
)
from xic_extractor.target_pair_rt_calibration import (
    load_target_pair_rt_calibration,
    rt_prior_library_from_target_pair_calibration,
)

TARGETED_PROCESSING_INPUT_SCHEMA_VERSION = "targeted_extraction_inputs_v1"
TARGETED_PROCESSING_ALGORITHM_VERSION = "targeted_extraction_algorithm_v1"
TARGETED_PROCESSING_CONTRACT_VERSION = "targeted_processing_contract_v1"
THERMO_RAW_ACQUISITION_FORMAT = "thermo_raw"
THERMO_RAW_OBSERVATION_NAMESPACE = "thermo_raw_file_reader:Device.MS:1"
TARGETED_SCIENTIFIC_SETTING_FIELDS = (
    "smooth_window",
    "smooth_polyorder",
    "peak_rel_height",
    "peak_min_prominence_ratio",
    "ms2_precursor_tol_da",
    "nl_min_intensity_ratio",
    "ms1_morphology_smoothing_window_points",
    "resolver_mode",
    "resolver_chrom_threshold",
    "resolver_min_search_range_min",
    "resolver_min_relative_height",
    "resolver_min_absolute_height",
    "resolver_min_ratio_top_edge",
    "resolver_peak_duration_min",
    "resolver_peak_duration_max",
    "resolver_min_scans",
    "count_no_ms2_as_detected",
    "nl_rt_anchor_search_margin_min",
    "nl_rt_anchor_half_window_min",
    "nl_fallback_half_window_min",
    "rolling_window_size",
    "dirty_matrix_mode",
    "baseline_audit_method",
    "baseline_integration_method",
)


@dataclass(frozen=True)
class ResolvedTargetedInputs:
    injection_order: dict[str, int]
    rt_prior_library: dict[tuple[str, str], LibraryEntry]
    model_selection_expected_diff_approvals: (
        Mapping[str, ExpectedDiffApprovalRecord] | None
    )


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
    return ResolvedTargetedInputs(
        injection_order=resolve_injection_order(
            config,
            raw_paths,
            injection_order,
        ),
        rt_prior_library=resolve_rt_prior_library(config, rt_prior_library),
        model_selection_expected_diff_approvals=(
            model_selection_expected_diff_approvals
        ),
    )


def resolve_targeted_raw_paths(config: ExtractionConfig) -> list[Path]:
    return sorted(config.data_dir.glob("*.raw"))


def build_targeted_processing_request_identity(
    config: ExtractionConfig,
    *,
    target_snapshot: TargetListSnapshot,
    raw_paths: Sequence[Path],
    resolved_inputs: ResolvedTargetedInputs,
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
        )

    expected_stems = set(sources_by_stem)
    actual_stems = set(resolved_inputs.injection_order)
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
            injection_order=resolved_inputs.injection_order[stem],
        )
        for stem, source in sources_by_stem.items()
    )
    scientific_inputs = {
        "settings": {
            field: getattr(config, field)
            for field in TARGETED_SCIENTIFIC_SETTING_FIELDS
        },
        "target_list_snapshot": target_list_snapshot_payload(target_snapshot),
        "rt_prior_library": _rt_prior_snapshot(resolved_inputs.rt_prior_library),
        "model_selection_expected_diff_approvals": _approval_snapshot(
            resolved_inputs.model_selection_expected_diff_approvals
        ),
        "targeted_ms1_shape_identity_support": (
            _targeted_ms1_shape_identity_support_snapshot(config)
        ),
    }
    return build_processing_request_identity(
        acquisition_sources=tuple(sources_by_stem.values()),
        acquisition_bindings=bindings,
        scientific_input_schema_version=TARGETED_PROCESSING_INPUT_SCHEMA_VERSION,
        processing_algorithm_version=TARGETED_PROCESSING_ALGORITHM_VERSION,
        processing_contract_version=TARGETED_PROCESSING_CONTRACT_VERSION,
        scientific_inputs=scientific_inputs,
    )


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
        "schema_version": "targeted_ms1_shape_identity_support_input_v1",
        "activation_policy": config.targeted_ms1_shape_identity_activation_policy,
        "content_sha256": fingerprint.content_sha256,
        "byte_size": fingerprint.byte_size,
    }


def resolve_injection_order(
    config: ExtractionConfig,
    raw_paths: list[Path],
    injection_order: dict[str, int] | None,
) -> dict[str, int]:
    if injection_order is not None:
        return injection_order
    if config.injection_order_source is not None:
        return _read_injection_order_source(config.injection_order_source)
    return fallback_injection_order_from_mtime(raw_paths)


def _read_injection_order_source(path: Path) -> dict[str, int]:
    if is_sample_metadata_source(path):
        return sample_metadata_to_raw_stem_injection_order(load_sample_metadata(path))
    return read_injection_order(path)


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
