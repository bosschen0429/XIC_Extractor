"""Admit batch/machine inputs; scientific policy is not a settings surface."""

from dataclasses import dataclass, replace
from pathlib import Path

from xic_extractor.configuration.execution_profile import (
    ExecutionProfile,
    load_execution_profile,
)
from xic_extractor.configuration.extraction_defaults import default_extraction_config
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
    execution: ExecutionProfile


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
    settings: dict[str, str], settings_path: Path = Path("workspace.json")
) -> _ParsedSettings:
    values, _ = fill_setting_defaults(settings)
    for key in ("data_dir", "dll_dir"):
        if not values[key].strip():
            raise ConfigError(f"{settings_path}: {key} is required")
    execution = load_execution_profile()
    return _ParsedSettings(
        Path(values["data_dir"]).expanduser(),
        Path(values["dll_dir"]).expanduser(),
        _parse_optional_path(values["injection_order_source"]),
        execution,
    )


def resolve_settings(
    raw: dict[str, str],
    overrides: dict[str, str] | None = None,
    *,
    settings_path: Path = Path("workspace.json"),
) -> tuple[dict[str, str], _ParsedSettings, list[str]]:
    fill_setting_defaults(raw)
    fill_setting_defaults(overrides or {})
    values, warnings = fill_setting_defaults({**raw, **(overrides or {})})
    return values, validate_settings_values(values, settings_path), warnings


def build_extraction_config(
    parsed: _ParsedSettings,
    settings: dict[str, str],
    settings_path: Path,
    output_dir: Path,
) -> ExtractionConfig:
    for key in ("data_dir", "dll_dir"):
        _parse_existing_dir(settings_path, key, settings[key])
    return replace(
        default_extraction_config(
            data_dir=parsed.data_dir,
            dll_dir=parsed.dll_dir,
            output_dir=output_dir,
            execution=parsed.execution,
        ),
        injection_order_source=parsed.injection_order_source,
    )
