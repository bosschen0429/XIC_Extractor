"""Admit batch/machine inputs; scientific policy is not a settings surface."""

from dataclasses import dataclass
from pathlib import Path

from xic_extractor.configuration.execution_profile import default_parallel_workers
from xic_extractor.configuration.models import ConfigError, ExtractionConfig
from xic_extractor.configuration.parsing import (
    _parse_existing_dir,
    _parse_optional_path,
)
from xic_extractor.settings_schema import CANONICAL_SETTINGS_DEFAULTS


@dataclass(frozen=True)
class _ParsedSettings:
    data_dir: Path
    dll_dir: Path
    injection_order_source: Path | None


def fill_setting_defaults(raw: dict[str, str]) -> tuple[dict[str, str], list[str]]:
    unknown = sorted(set(raw) - set(CANONICAL_SETTINGS_DEFAULTS))
    if unknown:
        raise ConfigError(
            "Retired or unknown Targeted settings: "
            + ", ".join(unknown)
            + ". Use only data_dir, dll_dir and injection_order_source; "
            "scientific overrides are not accepted."
        )
    return {**CANONICAL_SETTINGS_DEFAULTS, **raw}, []


def validate_settings_values(
    settings: dict[str, str], settings_path: Path = Path("settings.csv")
) -> _ParsedSettings:
    values, _ = fill_setting_defaults(settings)
    for key in ("data_dir", "dll_dir"):
        if not values[key].strip():
            raise ConfigError(f"{settings_path}: {key} is required")
    return _ParsedSettings(
        Path(values["data_dir"]).expanduser(),
        Path(values["dll_dir"]).expanduser(),
        _parse_optional_path(values["injection_order_source"]),
    )


def resolve_settings(
    raw: dict[str, str],
    overrides: dict[str, str] | None = None,
    *,
    settings_path: Path = Path("settings.csv"),
) -> tuple[dict[str, str], _ParsedSettings, list[str]]:
    # Validate both inputs before merge so an override cannot hide a retired key.
    fill_setting_defaults(raw)
    fill_setting_defaults(overrides or {})
    values, warnings = fill_setting_defaults({**raw, **(overrides or {})})
    return values, validate_settings_values(values, settings_path), warnings


def build_extraction_config(
    parsed: _ParsedSettings,
    settings: dict[str, str],
    settings_path: Path,
    output_dir: Path,
    config_hash: str,
) -> ExtractionConfig:
    from xic_extractor.configuration.targeted_strategy import targeted_tag_profile

    policy = targeted_tag_profile("dR")
    for key in ("data_dir", "dll_dir"):
        _parse_existing_dir(settings_path, key, settings[key])
    return ExtractionConfig(
        data_dir=parsed.data_dir,
        dll_dir=parsed.dll_dir,
        output_csv=output_dir / "xic_results.csv",
        diagnostics_csv=output_dir / "xic_diagnostics.csv",
        smooth_window=15,
        smooth_polyorder=3,
        peak_rel_height=0.95,
        peak_min_prominence_ratio=0.10,
        ms2_precursor_tol_da=policy.ms2_precursor_tol_da,
        nl_min_intensity_ratio=policy.nl_min_intensity_ratio,
        injection_order_source=parsed.injection_order_source,
        parallel_mode="process",
        parallel_workers=default_parallel_workers(),
        config_hash=config_hash,
    )
