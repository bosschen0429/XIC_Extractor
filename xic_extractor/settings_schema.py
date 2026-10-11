from xic_extractor.configuration.execution_profile import default_parallel_workers
from xic_extractor.configuration.signal_processing_profile import (
    DEFAULT_SIGNAL_PROCESSING_PROFILE,
)

RESOLVER_MODES: tuple[str, ...] = (
    "savgol",
    "local_minimum",
)
CANONICAL_RESOLVER_MODE = "local_minimum"


def normalize_resolver_mode(value: str) -> str:
    """Accept the saved-settings alias; new effective settings use savgol."""
    return "savgol" if value == "legacy_savgol" else value


ARBITRATED_RESOLVER_RETIRED_MESSAGE = (
    "arbitrated resolver mode is retired; legacy diagnostics use local_minimum. "
    "Targeted uses the canonical signal-processing profile."
)


CANONICAL_SETTINGS_DEFAULTS: dict[str, str] = {
    "data_dir": "C:/your/data/folder",
    "dll_dir": "C:\\Xcalibur\\system\\programs",
    "signal_processing_method": DEFAULT_SIGNAL_PROCESSING_PROFILE.method,
    "signal_processing_window_points": str(
        DEFAULT_SIGNAL_PROCESSING_PROFILE.window_points
    ),
    "ms2_precursor_tol_da": "0.8",
    "nl_min_intensity_ratio": "0.01",
    "count_no_ms2_as_detected": "false",
    "injection_order_source": "",
    "rolling_window_size": "5",
    "dirty_matrix_mode": "false",
    "nl_rt_anchor_search_margin_min": "2.0",
    "nl_rt_anchor_half_window_min": "1.0",
    "nl_fallback_half_window_min": "2.0",
    "parallel_mode": "process",
    "parallel_workers": str(default_parallel_workers()),
}

CANONICAL_SETTINGS_DESCRIPTIONS: dict[str, str] = {
    "data_dir": "資料來源資料夾（換批次只改這裡）",
    "dll_dir": "Xcalibur DLL 路徑（通常不需更改）",
    "signal_processing_method": (
        "Canonical signal smoothing method (gaussian only). AsLS is the fixed "
        "baseline estimator, not a smoothing method."
    ),
    "signal_processing_window_points": (
        "Canonical Gaussian smoothing window points (odd integer 3-999; default 15)"
    ),
    "ms2_precursor_tol_da": (
        "MS2 precursor m/z 半徑（Da，|scan precursor − 推算 precursor| 上限；"
        "方法隔離全寬 1.6 Da 對應名義半寬 0.8 Da）"
    ),
    "nl_min_intensity_ratio": (
        "NL product 強度至少為該 scan base peak 的比例（1% 排除 noise）"
    ),
    "count_no_ms2_as_detected": (
        "相容性 evidence 設定；正式 Targeted product projection "
        "不將 NO_MS2 算為偵測到，並非全域偵測開關"
    ),
    "injection_order_source": (
        "Sample 注射順序來源檔（CSV/XLSX 有 Sample_Name 與 Injection_Order 欄；"
        "留空則 fallback to RAW mtime）"
    ),
    "rolling_window_size": "ISTD RT prior 的滾動視窗半徑（±N 個注射）",
    "dirty_matrix_mode": "髒基質模式（放寬 S/N、收緊峰形；尿液等複雜基質用）",
    "nl_rt_anchor_search_margin_min": (
        "NL 錨定搜尋半徑（min）：以 rt_center ±此值搜尋 NL 確認的 MS2 作為 RT anchor"
    ),
    "nl_rt_anchor_half_window_min": (
        "NL 錨定後的 XIC 半寬（min）：找到 anchor 時，XIC 窗口 = [anchor_rt ± 此值]"
    ),
    "nl_fallback_half_window_min": (
        "NL 錨定失敗時的 fallback XIC 半寬（min）：窗口 = [rt_center ± 此值]"
    ),
    "parallel_mode": "執行後端（serial 或 process；預設 process）",
    "parallel_workers": "Process mode worker 數量（>= 1；預設 CPU 核心數 - 1）",
}


METHOD_KEYS = (
    "signal_processing_method",
    "signal_processing_window_points",
    "ms2_precursor_tol_da",
    "nl_min_intensity_ratio",
    "count_no_ms2_as_detected",
    "nl_rt_anchor_search_margin_min",
    "nl_rt_anchor_half_window_min",
    "nl_fallback_half_window_min",
    "rolling_window_size",
    "dirty_matrix_mode",
)
