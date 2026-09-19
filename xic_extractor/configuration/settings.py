import logging
from dataclasses import dataclass
from pathlib import Path

from xic_extractor.configuration.models import ConfigError, ExtractionConfig
from xic_extractor.configuration.parsing import (
    _config_error,
    _parse_bool,
    _parse_existing_dir,
    _parse_float,
    _parse_int,
    _parse_optional_path,
    _require_range,
)
from xic_extractor.peak_detection.signal_processing_profile import (
    DEFAULT_SIGNAL_PROCESSING_PROFILE,
    SignalProcessingProfile,
    resolve_signal_processing_profile,
)
from xic_extractor.settings_schema import (
    ARBITRATED_RESOLVER_RETIRED_MESSAGE,
    CANONICAL_SETTINGS_DEFAULTS,
    RESOLVER_MODES,
)
from xic_extractor.targeted_ms1_shape_identity_policy import (
    TARGETED_MS1_SHAPE_IDENTITY_ACTIVATION_POLICIES,
)

LOGGER = logging.getLogger(__name__)

_MIGRATION_DEFAULT_KEYS = tuple(
    key
    for key in CANONICAL_SETTINGS_DEFAULTS
    if key
    not in {
        "data_dir",
        "dll_dir",
        "ms1_morphology_smoothing_window_points",
    }
)

_CANONICAL_SIGNAL_WINDOW_KEY = "signal_processing_window_points"
_LEGACY_SIGNAL_WINDOW_KEY = "ms1_morphology_smoothing_window_points"


@dataclass(frozen=True)
class _ParsedSettings:
    data_dir: Path
    dll_dir: Path
    smooth_window: int
    smooth_polyorder: int
    signal_processing_method: str
    signal_processing_window_points: int
    peak_rel_height: float
    peak_min_prominence_ratio: float
    resolver_mode: str
    resolver_chrom_threshold: float
    resolver_min_search_range_min: float
    resolver_min_relative_height: float
    resolver_min_absolute_height: float
    resolver_min_ratio_top_edge: float
    resolver_peak_duration_min: float
    resolver_peak_duration_max: float
    resolver_min_scans: int
    ms2_precursor_tol_da: float
    nl_min_intensity_ratio: float
    count_no_ms2_as_detected: bool
    nl_rt_anchor_search_margin_min: float
    nl_rt_anchor_half_window_min: float
    nl_fallback_half_window_min: float
    injection_order_source: Path | None
    rolling_window_size: int
    dirty_matrix_mode: bool
    rt_prior_library_path: Path | None
    target_pair_rt_calibration_path: Path | None
    model_selection_expected_diff_approval_registry: Path | None
    targeted_ms1_shape_identity_support_tsv: Path | None
    targeted_ms1_shape_identity_activation_policy: str
    emit_score_breakdown: bool
    emit_review_report: bool
    emit_peak_candidates: bool
    keep_intermediate_csv: bool
    baseline_audit_method: str
    baseline_integration_method: str
    parallel_mode: str
    parallel_workers: int


def migrate_settings_dict(raw: dict[str, str]) -> tuple[dict[str, str], list[str]]:
    migrated = dict(raw)
    warnings: list[str] = []

    if "smooth_points" in migrated:
        if "smooth_window" not in migrated:
            migrated["smooth_window"] = migrated["smooth_points"]
            warnings.append("Renamed smooth_points to smooth_window.")
        else:
            warnings.append(
                "Ignored legacy smooth_points because smooth_window exists."
            )
        migrated.pop("smooth_points", None)

    if "smooth_sigma" in migrated:
        migrated.pop("smooth_sigma", None)
        warnings.append(
            "Dropped smooth_sigma because Savitzky-Golay smoothing has no sigma."
        )

    canonical_window = migrated.get(_CANONICAL_SIGNAL_WINDOW_KEY, "").strip()
    legacy_window = migrated.get(_LEGACY_SIGNAL_WINDOW_KEY, "").strip()
    if legacy_window:
        if canonical_window:
            try:
                values_match = int(canonical_window) == int(legacy_window)
            except ValueError:
                values_match = canonical_window == legacy_window
            if not values_match:
                raise ConfigError(
                    "conflicting settings keys "
                    f"{_CANONICAL_SIGNAL_WINDOW_KEY}={canonical_window!r} and "
                    f"{_LEGACY_SIGNAL_WINDOW_KEY}={legacy_window!r}"
                )
            warnings.append(
                "settings_migration status=deprecated_alias_equal "
                f"legacy_key={_LEGACY_SIGNAL_WINDOW_KEY} "
                f"canonical_key={_CANONICAL_SIGNAL_WINDOW_KEY} "
                f"value={canonical_window}"
            )
        else:
            try:
                legacy_points = int(legacy_window)
                resolve_signal_processing_profile(
                    signal_processing_window_points=legacy_points,
                )
            except (TypeError, ValueError) as exc:
                raise ConfigError(
                    f"legacy setting {_LEGACY_SIGNAL_WINDOW_KEY}="
                    f"{legacy_window!r} cannot migrate to "
                    f"{_CANONICAL_SIGNAL_WINDOW_KEY}: {exc}"
                ) from exc
            migrated[_CANONICAL_SIGNAL_WINDOW_KEY] = legacy_window
            warnings.append(
                "settings_migration status=deprecated_alias_migrated "
                f"legacy_key={_LEGACY_SIGNAL_WINDOW_KEY} "
                f"canonical_key={_CANONICAL_SIGNAL_WINDOW_KEY} "
                f"value={legacy_window}"
            )
        migrated.pop(_LEGACY_SIGNAL_WINDOW_KEY, None)
    elif _LEGACY_SIGNAL_WINDOW_KEY in migrated:
        migrated.pop(_LEGACY_SIGNAL_WINDOW_KEY)
        warnings.append(
            "settings_migration status=deprecated_alias_empty "
            f"legacy_key={_LEGACY_SIGNAL_WINDOW_KEY} "
            f"canonical_key={_CANONICAL_SIGNAL_WINDOW_KEY}"
        )

    for key in _MIGRATION_DEFAULT_KEYS:
        default = CANONICAL_SETTINGS_DEFAULTS[key]
        if key not in migrated or migrated[key] == "":
            migrated[key] = default
            warnings.append(f"Filled missing {key} with default {default}.")

    return migrated, warnings


def _validate_settings(
    settings: dict[str, str],
    settings_path: Path,
    output_dir: Path,
    config_hash: str,
) -> ExtractionConfig:
    parsed = _parse_settings_values(settings, settings_path)
    _validate_settings_ranges(settings, settings_path, parsed)
    return _build_config(parsed, output_dir, config_hash)


def _parse_settings_values(
    settings: dict[str, str], settings_path: Path
) -> _ParsedSettings:
    signal_profile = _parse_signal_processing_profile(settings, settings_path)
    return _ParsedSettings(
        data_dir=_parse_existing_dir(
            settings_path,
            "data_dir",
            _setting_value(settings, settings_path, "data_dir"),
        ),
        dll_dir=_parse_existing_dir(
            settings_path, "dll_dir", _setting_value(settings, settings_path, "dll_dir")
        ),
        smooth_window=_parse_int(
            settings_path,
            None,
            "smooth_window",
            _setting_value(settings, settings_path, "smooth_window"),
        ),
        smooth_polyorder=_parse_int(
            settings_path,
            None,
            "smooth_polyorder",
            _setting_value(settings, settings_path, "smooth_polyorder"),
        ),
        signal_processing_method=signal_profile.method,
        signal_processing_window_points=signal_profile.window_points,
        peak_rel_height=_parse_float(
            settings_path,
            None,
            "peak_rel_height",
            _setting_value(settings, settings_path, "peak_rel_height"),
        ),
        peak_min_prominence_ratio=_parse_float(
            settings_path,
            None,
            "peak_min_prominence_ratio",
            _setting_value(settings, settings_path, "peak_min_prominence_ratio"),
        ),
        resolver_mode=_setting_value(settings, settings_path, "resolver_mode"),
        resolver_chrom_threshold=_parse_float(
            settings_path,
            None,
            "resolver_chrom_threshold",
            _setting_value(settings, settings_path, "resolver_chrom_threshold"),
        ),
        resolver_min_search_range_min=_parse_float(
            settings_path,
            None,
            "resolver_min_search_range_min",
            _setting_value(settings, settings_path, "resolver_min_search_range_min"),
        ),
        resolver_min_relative_height=_parse_float(
            settings_path,
            None,
            "resolver_min_relative_height",
            _setting_value(settings, settings_path, "resolver_min_relative_height"),
        ),
        resolver_min_absolute_height=_parse_float(
            settings_path,
            None,
            "resolver_min_absolute_height",
            _setting_value(settings, settings_path, "resolver_min_absolute_height"),
        ),
        resolver_min_ratio_top_edge=_parse_float(
            settings_path,
            None,
            "resolver_min_ratio_top_edge",
            _setting_value(settings, settings_path, "resolver_min_ratio_top_edge"),
        ),
        resolver_peak_duration_min=_parse_float(
            settings_path,
            None,
            "resolver_peak_duration_min",
            _setting_value(settings, settings_path, "resolver_peak_duration_min"),
        ),
        resolver_peak_duration_max=_parse_float(
            settings_path,
            None,
            "resolver_peak_duration_max",
            _setting_value(settings, settings_path, "resolver_peak_duration_max"),
        ),
        resolver_min_scans=_parse_int(
            settings_path,
            None,
            "resolver_min_scans",
            _setting_value(settings, settings_path, "resolver_min_scans"),
        ),
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
        rt_prior_library_path=_parse_optional_path(
            settings.get("rt_prior_library_path", "")
        ),
        target_pair_rt_calibration_path=_parse_optional_path(
            settings.get("target_pair_rt_calibration_path", "")
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
        emit_score_breakdown=_parse_bool(
            settings_path,
            None,
            "emit_score_breakdown",
            _setting_value(settings, settings_path, "emit_score_breakdown"),
        ),
        emit_review_report=_parse_bool(
            settings_path,
            None,
            "emit_review_report",
            _setting_value(settings, settings_path, "emit_review_report"),
        ),
        emit_peak_candidates=_parse_bool(
            settings_path,
            None,
            "emit_peak_candidates",
            _setting_value(settings, settings_path, "emit_peak_candidates"),
        ),
        keep_intermediate_csv=_parse_bool(
            settings_path,
            None,
            "keep_intermediate_csv",
            _setting_value(settings, settings_path, "keep_intermediate_csv"),
        ),
        baseline_audit_method=(
            settings.get("baseline_audit_method", "").strip().lower()
        ),
        baseline_integration_method=(
            settings.get("baseline_integration_method", "asls").strip().lower()
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
    if parsed.smooth_window < 3 or parsed.smooth_window % 2 == 0:
        raise _config_error(
            settings_path,
            None,
            "smooth_window",
            settings["smooth_window"],
            "must be odd and >= 3",
        )
    if parsed.smooth_polyorder < 1 or parsed.smooth_polyorder >= parsed.smooth_window:
        raise _config_error(
            settings_path,
            None,
            "smooth_polyorder",
            settings["smooth_polyorder"],
            "must be >= 1 and < smooth_window",
        )
    _require_range(
        settings_path,
        "peak_rel_height",
        settings["peak_rel_height"],
        parsed.peak_rel_height,
        0.5,
        0.99,
    )
    _require_range(
        settings_path,
        "peak_min_prominence_ratio",
        settings["peak_min_prominence_ratio"],
        parsed.peak_min_prominence_ratio,
        0.01,
        0.50,
    )
    if parsed.resolver_mode == "arbitrated":
        raise _config_error(
            settings_path,
            None,
            "resolver_mode",
            settings["resolver_mode"],
            ARBITRATED_RESOLVER_RETIRED_MESSAGE,
        )
    if parsed.resolver_mode not in RESOLVER_MODES:
        raise _config_error(
            settings_path,
            None,
            "resolver_mode",
            settings["resolver_mode"],
            f"must be {', '.join(RESOLVER_MODES[:-1])}, or {RESOLVER_MODES[-1]}",
        )
    if not 0 <= parsed.resolver_chrom_threshold <= 1:
        raise _config_error(
            settings_path,
            None,
            "resolver_chrom_threshold",
            settings["resolver_chrom_threshold"],
            "must be between 0 and 1",
        )
    if parsed.resolver_min_search_range_min <= 0:
        raise _config_error(
            settings_path,
            None,
            "resolver_min_search_range_min",
            settings["resolver_min_search_range_min"],
            "must be > 0",
        )
    if not 0 <= parsed.resolver_min_relative_height <= 1:
        raise _config_error(
            settings_path,
            None,
            "resolver_min_relative_height",
            settings["resolver_min_relative_height"],
            "must be >= 0 and <= 1",
        )
    if parsed.resolver_min_absolute_height < 0:
        raise _config_error(
            settings_path,
            None,
            "resolver_min_absolute_height",
            settings["resolver_min_absolute_height"],
            "must be >= 0",
        )
    if parsed.resolver_min_ratio_top_edge <= 1:
        raise _config_error(
            settings_path,
            None,
            "resolver_min_ratio_top_edge",
            settings["resolver_min_ratio_top_edge"],
            "must be > 1",
        )
    if parsed.resolver_peak_duration_min < 0:
        raise _config_error(
            settings_path,
            None,
            "resolver_peak_duration_min",
            settings["resolver_peak_duration_min"],
            "must be >= 0",
        )
    if parsed.resolver_peak_duration_max <= 0:
        raise _config_error(
            settings_path,
            None,
            "resolver_peak_duration_max",
            settings["resolver_peak_duration_max"],
            "must be > 0",
        )
    if parsed.resolver_peak_duration_min > parsed.resolver_peak_duration_max:
        raise _config_error(
            settings_path,
            None,
            "resolver_peak_duration_min",
            settings["resolver_peak_duration_min"],
            "must be <= resolver_peak_duration_max",
        )
    if parsed.resolver_min_scans < 1:
        raise _config_error(
            settings_path,
            None,
            "resolver_min_scans",
            settings["resolver_min_scans"],
            "must be >= 1",
        )
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
    if parsed.baseline_audit_method not in {"", "asls"}:
        raise _config_error(
            settings_path,
            None,
            "baseline_audit_method",
            settings.get("baseline_audit_method", ""),
            "must be empty or asls",
        )
    if parsed.baseline_integration_method == "linear_edge":
        raise _config_error(
            settings_path,
            None,
            "baseline_integration_method",
            settings.get("baseline_integration_method", ""),
            "linear_edge is retired; use asls",
        )
    if parsed.baseline_integration_method != "asls":
        raise _config_error(
            settings_path,
            None,
            "baseline_integration_method",
            settings.get("baseline_integration_method", ""),
            "must be asls",
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
        smooth_window=parsed.smooth_window,
        smooth_polyorder=parsed.smooth_polyorder,
        signal_processing_method=parsed.signal_processing_method,
        signal_processing_window_points=parsed.signal_processing_window_points,
        ms1_morphology_smoothing_window_points=(parsed.signal_processing_window_points),
        peak_rel_height=parsed.peak_rel_height,
        peak_min_prominence_ratio=parsed.peak_min_prominence_ratio,
        resolver_mode=parsed.resolver_mode,
        resolver_chrom_threshold=parsed.resolver_chrom_threshold,
        resolver_min_search_range_min=parsed.resolver_min_search_range_min,
        resolver_min_relative_height=parsed.resolver_min_relative_height,
        resolver_min_absolute_height=parsed.resolver_min_absolute_height,
        resolver_min_ratio_top_edge=parsed.resolver_min_ratio_top_edge,
        resolver_peak_duration_min=parsed.resolver_peak_duration_min,
        resolver_peak_duration_max=parsed.resolver_peak_duration_max,
        resolver_min_scans=parsed.resolver_min_scans,
        ms2_precursor_tol_da=parsed.ms2_precursor_tol_da,
        nl_min_intensity_ratio=parsed.nl_min_intensity_ratio,
        count_no_ms2_as_detected=parsed.count_no_ms2_as_detected,
        nl_rt_anchor_search_margin_min=parsed.nl_rt_anchor_search_margin_min,
        nl_rt_anchor_half_window_min=parsed.nl_rt_anchor_half_window_min,
        nl_fallback_half_window_min=parsed.nl_fallback_half_window_min,
        injection_order_source=parsed.injection_order_source,
        rolling_window_size=parsed.rolling_window_size,
        dirty_matrix_mode=parsed.dirty_matrix_mode,
        rt_prior_library_path=parsed.rt_prior_library_path,
        target_pair_rt_calibration_path=parsed.target_pair_rt_calibration_path,
        model_selection_expected_diff_approval_registry=(
            parsed.model_selection_expected_diff_approval_registry
        ),
        targeted_ms1_shape_identity_support_tsv=(
            parsed.targeted_ms1_shape_identity_support_tsv
        ),
        targeted_ms1_shape_identity_activation_policy=(
            parsed.targeted_ms1_shape_identity_activation_policy
        ),
        emit_score_breakdown=parsed.emit_score_breakdown,
        emit_review_report=parsed.emit_review_report,
        emit_peak_candidates=parsed.emit_peak_candidates,
        keep_intermediate_csv=parsed.keep_intermediate_csv,
        baseline_audit_method=parsed.baseline_audit_method,
        baseline_integration_method=parsed.baseline_integration_method,
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
