from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from xic_extractor.config import ConfigError, ExtractionConfig
from xic_extractor.injection_rolling import read_injection_order
from xic_extractor.peak_detection.model_selection import (
    ExpectedDiffApprovalRecord,
)
from xic_extractor.peak_detection.model_selection_approval_registry import (
    load_expected_diff_approval_registry,
)
from xic_extractor.rt_prior_library import LibraryEntry, load_library
from xic_extractor.sample_metadata import (
    is_sample_metadata_source,
    load_sample_metadata,
    sample_metadata_to_injection_order,
)
from xic_extractor.target_pair_rt_calibration import (
    load_target_pair_rt_calibration,
    rt_prior_library_from_target_pair_calibration,
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
        return sample_metadata_to_injection_order(load_sample_metadata(path))
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
