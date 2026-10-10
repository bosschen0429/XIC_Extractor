import logging
from pathlib import Path

from xic_extractor.configuration.csv_io import _config_input_path, _read_settings
from xic_extractor.configuration.hashing import (
    _compute_config_hash,
    compute_target_config_hash,
)
from xic_extractor.configuration.models import ConfigError, ExtractionConfig, Target
from xic_extractor.configuration.settings import build_extraction_config
from xic_extractor.configuration.target_identity import (
    TargetListSnapshot,
    mint_target_list_snapshot,
)
from xic_extractor.configuration.targeted_settings import resolve_targeted_settings
from xic_extractor.configuration.targets import _read_targets
from xic_extractor.target_pair_rt_calibration import load_target_pair_rt_calibration

LOGGER = logging.getLogger(__name__)


def load_target_list_snapshot(
    config_dir: Path,
    *,
    identity_namespace: str,
) -> TargetListSnapshot:
    """Load and owner-mint the exact target-list snapshot for one run."""

    targets_path = _config_input_path(config_dir, "targets")
    return mint_target_list_snapshot(
        tuple(_read_targets(targets_path)),
        identity_namespace=identity_namespace,
    )


def load_config(
    config_dir: Path,
    *,
    settings_overrides: dict[str, str] | None = None,
    output_dir: Path | None = None,
) -> tuple[ExtractionConfig, list[Target]]:
    """Load active settings; output_dir only controls run artifact placement."""
    settings_path = _config_input_path(config_dir, "settings")
    targets_path = _config_input_path(config_dir, "targets")

    raw_settings = _read_settings(settings_path)
    try:
        resolved = resolve_targeted_settings(
            raw_settings, settings_overrides, source=str(settings_path)
        )
    except ConfigError as exc:
        raise ConfigError(f"{settings_path}: {exc}") from exc
    for warning in resolved.warnings:
        LOGGER.warning(warning)

    output_dir = (
        Path(output_dir) if output_dir is not None else config_dir.parent / "output"
    )
    output_dir.mkdir(parents=True, exist_ok=True)

    config_hash = _compute_config_hash(
        targets_path,
        settings_path,
        settings_overrides=settings_overrides,
    )
    config = build_extraction_config(
        resolved.parsed, resolved.values, settings_path, output_dir, config_hash
    )
    targets = _read_targets(targets_path)
    target_config_hash = compute_target_config_hash(targets_path)
    config = ExtractionConfig(
        **{
            **config.__dict__,
            "target_config_hash": target_config_hash,
        }
    )
    if config.target_pair_rt_calibration_path is not None:
        load_target_pair_rt_calibration(
            config.target_pair_rt_calibration_path,
            expected_target_config_hash=target_config_hash,
        )
    return config, targets
