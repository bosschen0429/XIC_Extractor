from PyQt6.QtWidgets import QDoubleSpinBox, QSpinBox

from xic_extractor.settings_schema import CANONICAL_SETTINGS_DEFAULTS, RESOLVER_MODES


def _int_value(settings: dict[str, str], key: str) -> int:
    try:
        return int(settings.get(key, CANONICAL_SETTINGS_DEFAULTS[key]))
    except ValueError:
        return int(CANONICAL_SETTINGS_DEFAULTS[key])


def _float_value(settings: dict[str, str], key: str) -> float:
    try:
        return float(settings.get(key, CANONICAL_SETTINGS_DEFAULTS[key]))
    except ValueError:
        return float(CANONICAL_SETTINGS_DEFAULTS[key])


def _bool_value(settings: dict[str, str], key: str) -> bool:
    return settings.get(key, CANONICAL_SETTINGS_DEFAULTS[key]).lower() == "true"


def _invalid_parallel_mode(value: str) -> str | None:
    return None if value in {"serial", "process"} else value


def _invalid_parallel_workers(value: str) -> str | None:
    try:
        return None if int(value) >= 1 else value
    except ValueError:
        return value


def _int_setting_text(
    settings: dict[str, str],
    key: str,
    spin: QSpinBox,
) -> str:
    original = settings.get(key, CANONICAL_SETTINGS_DEFAULTS[key])
    try:
        if spin.value() == int(original):
            return original
    except ValueError:
        pass
    return str(spin.value())


def _float_setting_text(
    settings: dict[str, str],
    key: str,
    spin: QDoubleSpinBox,
) -> str:
    original = settings.get(key, CANONICAL_SETTINGS_DEFAULTS[key])
    try:
        tolerance = 10 ** -spin.decimals()
        if abs(spin.value() - float(original)) <= tolerance:
            return original
    except ValueError:
        pass
    return f"{spin.value():g}"


def _settings_values_are_valid(values: dict[str, str]) -> bool:
    smooth_window = int(values["smooth_window"])
    smooth_polyorder = int(values["smooth_polyorder"])
    signal_processing_window_points = int(values["signal_processing_window_points"])
    peak_rel_height = float(values["peak_rel_height"])
    peak_min_prominence_ratio = float(values["peak_min_prominence_ratio"])
    ms2_precursor_tol_da = float(values["ms2_precursor_tol_da"])
    nl_min_intensity_ratio = float(values["nl_min_intensity_ratio"])
    resolver_min_search_range_min = float(values["resolver_min_search_range_min"])
    resolver_min_relative_height = float(values["resolver_min_relative_height"])
    resolver_min_absolute_height = float(values["resolver_min_absolute_height"])
    resolver_min_ratio_top_edge = float(values["resolver_min_ratio_top_edge"])
    resolver_peak_duration_min = float(values["resolver_peak_duration_min"])
    resolver_peak_duration_max = float(values["resolver_peak_duration_max"])
    resolver_min_scans = int(values["resolver_min_scans"])
    try:
        parallel_workers = int(values["parallel_workers"])
    except ValueError:
        parallel_workers = 0
    return (
        bool(values["data_dir"])
        and bool(values["dll_dir"])
        and smooth_window >= 3
        and smooth_window % 2 == 1
        and 1 <= smooth_polyorder < smooth_window
        and signal_processing_window_points >= 3
        and signal_processing_window_points % 2 == 1
        and 0.50 <= peak_rel_height <= 0.99
        and 0.01 <= peak_min_prominence_ratio <= 0.50
        and ms2_precursor_tol_da > 0
        and 0 < nl_min_intensity_ratio <= 1
        and values["resolver_mode"] in RESOLVER_MODES
        and resolver_min_search_range_min > 0
        and 0 <= resolver_min_relative_height <= 1
        and resolver_min_absolute_height >= 0
        and resolver_min_ratio_top_edge > 1
        and resolver_peak_duration_min >= 0
        and resolver_peak_duration_max > 0
        and resolver_peak_duration_min <= resolver_peak_duration_max
        and resolver_min_scans >= 1
        and values["parallel_mode"] in {"serial", "process"}
        and parallel_workers >= 1
    )
