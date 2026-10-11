import logging
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from pathlib import Path

from xic_extractor.configuration.models import ExtractionConfig
from xic_extractor.configuration.parsing import (
    _config_error,
    _parse_bool,
    _parse_existing_dir,
    _parse_float,
    _parse_int,
    _parse_optional_path,
)
from xic_extractor.configuration.signal_processing_profile import (
    DEFAULT_SIGNAL_PROCESSING_PROFILE,
    SignalProcessingProfile,
    resolve_signal_processing_profile,
)
from xic_extractor.settings_schema import (
    CANONICAL_SETTINGS_DEFAULTS,
    METHOD_KEYS,
)
from xic_extractor.targeted_ms1_shape_identity_policy import (
    TARGETED_MS1_SHAPE_IDENTITY_ACTIVATION_POLICIES,
)

LOGGER = logging.getLogger(__name__)

_CANONICAL_SIGNAL_WINDOW_KEY = "signal_processing_window_points"
_DEFAULTED_KEYS = tuple(
    key for key in CANONICAL_SETTINGS_DEFAULTS if key not in {"data_dir", "dll_dir"}
)


@dataclass(frozen=True)
class _ParsedSettings:
    data_dir: Path
    dll_dir: Path
    signal_processing_method: str
    signal_processing_window_points: int
    ms2_precursor_tol_da: float
    nl_min_intensity_ratio: float
    count_no_ms2_as_detected: bool
    nl_rt_anchor_search_margin_min: float
    nl_rt_anchor_half_window_min: float
    nl_fallback_half_window_min: float
    injection_order_source: Path | None
    rolling_window_size: int
    dirty_matrix_mode: bool
    model_selection_expected_diff_approval_registry: Path | None
    targeted_ms1_shape_identity_support_tsv: Path | None
    targeted_ms1_shape_identity_activation_policy: str
    parallel_mode: str
    parallel_workers: int


def fill_setting_defaults(raw: dict[str, str]) -> tuple[dict[str, str], list[str]]:
    """Complete omitted values from the canonical defaults; never rename keys."""
    values = dict(raw)
    warnings: list[str] = []
    for key in _DEFAULTED_KEYS:
        default = CANONICAL_SETTINGS_DEFAULTS[key]
        if key not in values or (values[key] == "" and default != ""):
            values[key] = default
            warnings.append(f"Filled missing {key} with default {default}.")
    return values, warnings


def build_extraction_config(
    parsed: _ParsedSettings,
    settings: dict[str, str],
    settings_path: Path,
    output_dir: Path,
    config_hash: str,
) -> ExtractionConfig:
    # Filesystem admission belongs to starting a run, not editing a method.
    for key in ("data_dir", "dll_dir"):
        _parse_existing_dir(
            settings_path, key, _setting_value(settings, settings_path, key)
        )
    return _build_config(parsed, output_dir, config_hash)


def validate_settings_values(
    settings: dict[str, str], settings_path: Path = Path("settings.csv")
) -> _ParsedSettings:
    """Validate scalar settings without admitting local inputs or reading files."""
    parsed = _parse_settings_values(settings, settings_path)
    _validate_settings_ranges(settings, settings_path, parsed)
    return parsed


def resolve_settings(
    raw: dict[str, str],
    overrides: dict[str, str] | None = None,
    *,
    settings_path: Path = Path("settings.csv"),
) -> tuple[dict[str, str], _ParsedSettings, list[str]]:
    """Apply overrides and defaults, then parse effective values once."""
    values, warnings = fill_setting_defaults({**raw, **(overrides or {})})
    warnings.extend(
        f"Unknown setting {key!r} is preserved, not consumed by Targeted."
        for key in values
        if key not in CANONICAL_SETTINGS_DEFAULTS
    )
    return values, validate_settings_values(values, settings_path), warnings


def reset_method_settings(settings: dict[str, str]) -> dict[str, str]:
    """Reset method parameters, preserving batch, resource and execution values."""
    values, _warnings = fill_setting_defaults(settings)
    values.update({key: CANONICAL_SETTINGS_DEFAULTS[key] for key in METHOD_KEYS})
    return values


def method_settings_differences(settings: dict[str, str]) -> list[dict[str, str]]:
    """Compare normalized method values without revalidating or reading inputs."""
    differences = []
    for key in METHOD_KEYS:
        value, default = settings[key], CANONICAL_SETTINGS_DEFAULTS[key]
        try:
            equal = Decimal(value) == Decimal(default)
        except InvalidOperation:
            equal = value.strip().lower() == default.strip().lower()
        if not equal:
            differences.append({"key": key, "default": default, "value": value})
    return differences


def _parse_settings_values(
    settings: dict[str, str], settings_path: Path
) -> _ParsedSettings:
    signal_profile = _parse_signal_processing_profile(settings, settings_path)
    return _ParsedSettings(
        data_dir=Path(settings.get("data_dir", "")).expanduser(),
        dll_dir=Path(settings.get("dll_dir", "")).expanduser(),
        signal_processing_method=signal_profile.method,
        signal_processing_window_points=signal_profile.window_points,
        ms2_precursor_tol_da=_parse_float(
            settings_path,
            None,
            "ms2_precursor_tol_da",
            _setting_value(settings, settings_path, "ms2_precursor_tol_da"),
        ),
        nl_min_intensity_ratio=_parse_float(
            settings_path,
            None,
            "nl_min_intensity_ratio",
            _setting_value(settings, settings_path, "nl_min_intensity_ratio"),
        ),
        count_no_ms2_as_detected=_parse_bool(
            settings_path,
            None,
            "count_no_ms2_as_detected",
            _setting_value(settings, settings_path, "count_no_ms2_as_detected"),
        ),
        nl_rt_anchor_search_margin_min=_parse_float(
            settings_path,
            None,
            "nl_rt_anchor_search_margin_min",
            _setting_value(settings, settings_path, "nl_rt_anchor_search_margin_min"),
        ),
        nl_rt_anchor_half_window_min=_parse_float(
            settings_path,
            None,
            "nl_rt_anchor_half_window_min",
            _setting_value(settings, settings_path, "nl_rt_anchor_half_window_min"),
        ),
        nl_fallback_half_window_min=_parse_float(
            settings_path,
            None,
            "nl_fallback_half_window_min",
            _setting_value(settings, settings_path, "nl_fallback_half_window_min"),
        ),
        injection_order_source=_parse_optional_path(
            settings.get("injection_order_source", "")
        ),
        rolling_window_size=_parse_int(
            settings_path,
            None,
            "rolling_window_size",
            _setting_value(settings, settings_path, "rolling_window_size"),
        ),
        dirty_matrix_mode=_parse_bool(
            settings_path,
            None,
            "dirty_matrix_mode",
            _setting_value(settings, settings_path, "dirty_matrix_mode"),
        ),
        model_selection_expected_diff_approval_registry=_parse_optional_path(
            settings.get("model_selection_expected_diff_approval_registry", "")
        ),
        targeted_ms1_shape_identity_support_tsv=_parse_optional_path(
            settings.get("targeted_ms1_shape_identity_support_tsv", "")
        ),
        targeted_ms1_shape_identity_activation_policy=_setting_value(
            settings,
            settings_path,
            "targeted_ms1_shape_identity_activation_policy",
        ),
        parallel_mode=_setting_value(settings, settings_path, "parallel_mode"),
        parallel_workers=_parse_int(
            settings_path,
            None,
            "parallel_workers",
            _setting_value(settings, settings_path, "parallel_workers"),
        ),
    )


def _validate_settings_ranges(
    settings: dict[str, str], settings_path: Path, parsed: _ParsedSettings
) -> None:
    if parsed.ms2_precursor_tol_da <= 0:
        raise _config_error(
            settings_path,
            None,
            "ms2_precursor_tol_da",
            settings["ms2_precursor_tol_da"],
            "must be > 0",
        )
    if not 0 < parsed.nl_min_intensity_ratio <= 1:
        raise _config_error(
            settings_path,
            None,
            "nl_min_intensity_ratio",
            settings["nl_min_intensity_ratio"],
            "must be > 0 and <= 1",
        )
    for column, parsed_value in (
        (
            "nl_rt_anchor_search_margin_min",
            parsed.nl_rt_anchor_search_margin_min,
        ),
        (
            "nl_rt_anchor_half_window_min",
            parsed.nl_rt_anchor_half_window_min,
        ),
        (
            "nl_fallback_half_window_min",
            parsed.nl_fallback_half_window_min,
        ),
    ):
        if parsed_value <= 0:
            raise _config_error(
                settings_path,
                None,
                column,
                settings[column],
                "must be > 0",
            )
    if parsed.rolling_window_size < 1:
        raise _config_error(
            settings_path,
            None,
            "rolling_window_size",
            settings["rolling_window_size"],
            "must be >= 1",
        )
    if parsed.parallel_mode not in {"serial", "process"}:
        raise _config_error(
            settings_path,
            None,
            "parallel_mode",
            settings["parallel_mode"],
            "must be serial or process",
        )
    if parsed.parallel_workers < 1:
        raise _config_error(
            settings_path,
            None,
            "parallel_workers",
            settings["parallel_workers"],
            "must be >= 1",
        )
    if (
        parsed.targeted_ms1_shape_identity_activation_policy
        not in TARGETED_MS1_SHAPE_IDENTITY_ACTIVATION_POLICIES
    ):
        allowed = ", ".join(TARGETED_MS1_SHAPE_IDENTITY_ACTIVATION_POLICIES)
        raise _config_error(
            settings_path,
            None,
            "targeted_ms1_shape_identity_activation_policy",
            settings["targeted_ms1_shape_identity_activation_policy"],
            f"must be {allowed}",
        )


def _build_config(
    parsed: _ParsedSettings, output_dir: Path, config_hash: str
) -> ExtractionConfig:
    return ExtractionConfig(
        data_dir=parsed.data_dir,
        dll_dir=parsed.dll_dir,
        output_csv=output_dir / "xic_results.csv",
        diagnostics_csv=output_dir / "xic_diagnostics.csv",
        # Direct-finder-only fields stay fixed for the shared ExtractionConfig
        # API; formal Targeted settings no longer expose or consume them.
        smooth_window=15,
        smooth_polyorder=3,
        signal_processing_method=parsed.signal_processing_method,
        signal_processing_window_points=parsed.signal_processing_window_points,
        ms1_morphology_smoothing_window_points=(parsed.signal_processing_window_points),
        peak_rel_height=0.95,
        peak_min_prominence_ratio=0.10,
        ms2_precursor_tol_da=parsed.ms2_precursor_tol_da,
        nl_min_intensity_ratio=parsed.nl_min_intensity_ratio,
        count_no_ms2_as_detected=parsed.count_no_ms2_as_detected,
        nl_rt_anchor_search_margin_min=parsed.nl_rt_anchor_search_margin_min,
        nl_rt_anchor_half_window_min=parsed.nl_rt_anchor_half_window_min,
        nl_fallback_half_window_min=parsed.nl_fallback_half_window_min,
        injection_order_source=parsed.injection_order_source,
        rolling_window_size=parsed.rolling_window_size,
        dirty_matrix_mode=parsed.dirty_matrix_mode,
        model_selection_expected_diff_approval_registry=(
            parsed.model_selection_expected_diff_approval_registry
        ),
        targeted_ms1_shape_identity_support_tsv=(
            parsed.targeted_ms1_shape_identity_support_tsv
        ),
        targeted_ms1_shape_identity_activation_policy=(
            parsed.targeted_ms1_shape_identity_activation_policy
        ),
        baseline_integration_method="asls",
        parallel_mode=parsed.parallel_mode,
        parallel_workers=parsed.parallel_workers,
        config_hash=config_hash,
    )


def _setting_value(settings: dict[str, str], path: Path, column: str) -> str:
    value = settings.get(column, "")
    if value == "":
        raise _config_error(path, None, column, value, "is required")
    return value


def _parse_signal_processing_profile(
    settings: dict[str, str], settings_path: Path
) -> SignalProcessingProfile:
    method = _setting_value(
        settings,
        settings_path,
        "signal_processing_method",
    )
    window_value = _setting_value(
        settings,
        settings_path,
        _CANONICAL_SIGNAL_WINDOW_KEY,
    )
    window_points = _parse_int(
        settings_path,
        None,
        _CANONICAL_SIGNAL_WINDOW_KEY,
        window_value,
    )
    try:
        return resolve_signal_processing_profile(
            signal_processing_method=method,
            signal_processing_window_points=window_points,
        )
    except (TypeError, ValueError) as exc:
        column = (
            "signal_processing_method"
            if method != DEFAULT_SIGNAL_PROCESSING_PROFILE.method
            else _CANONICAL_SIGNAL_WINDOW_KEY
        )
        value = method if column == "signal_processing_method" else window_value
        raise _config_error(
            settings_path,
            None,
            column,
            value,
            str(exc),
        ) from exc
