from xic_extractor.execution_settings import default_parallel_workers
from xic_extractor.peak_detection.signal_processing_profile import (
    DEFAULT_SIGNAL_PROCESSING_PROFILE,
)
from xic_extractor.targeted_ms1_shape_identity_policy import (
    EXPLICIT_SUPPORT_TSV_POLICY,
    LIMITED_HMDC_MEDC_POLICY,
)

RESOLVER_MODES: tuple[str, ...] = (
    "legacy_savgol",
    "local_minimum",
)
CANONICAL_RESOLVER_MODE = "local_minimum"

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
    # Direct lookup compatibility for excluded callers that have not migrated.
    # The settings loader maps this spelling one-way into the canonical key.
    "ms1_morphology_smoothing_window_points": str(
        DEFAULT_SIGNAL_PROCESSING_PROFILE.window_points
    ),
    "ms2_precursor_tol_da": "1.1",
    "nl_min_intensity_ratio": "0.01",
    "count_no_ms2_as_detected": "false",
    "injection_order_source": "",
    "rolling_window_size": "5",
    "dirty_matrix_mode": "false",
    "rt_prior_library_path": "",
    "target_pair_rt_calibration_path": "",
    "model_selection_expected_diff_approval_registry": "",
    "targeted_ms1_shape_identity_support_tsv": "",
    "targeted_ms1_shape_identity_activation_policy": LIMITED_HMDC_MEDC_POLICY,
    "emit_score_breakdown": "false",
    "emit_review_report": "false",
    "emit_peak_candidates": "false",
    "keep_intermediate_csv": "false",
    "baseline_audit_method": "",
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
    "ms1_morphology_smoothing_window_points": (
        "Legacy Gaussian15 alias for signal_processing_window_points; "
        "do not use in new settings"
    ),
    "ms2_precursor_tol_da": (
        "MS2 precursor m/z 半徑（Da，|scan precursor − 推算 precursor| 上限；"
        "儀器隔離寬度 1.6 Da（±0.8）加實測邊緣透過量 → 1.1 Da，見 #466）"
    ),
    "nl_min_intensity_ratio": (
        "NL product 強度至少為該 scan base peak 的比例（1% 排除 noise）"
    ),
    "count_no_ms2_as_detected": (
        "是否將無 MS2 觸發的樣品算為偵測到（DDA 隨機性假陰性補救用）"
    ),
    "injection_order_source": (
        "Sample 注射順序來源檔（CSV/XLSX 有 Sample_Name 與 Injection_Order 欄；"
        "留空則 fallback to RAW mtime）"
    ),
    "rolling_window_size": "ISTD RT prior 的滾動視窗半徑（±N 個注射）",
    "dirty_matrix_mode": "髒基質模式（放寬 S/N、收緊峰形；尿液等複雜基質用）",
    "rt_prior_library_path": (
        "developer/debug RT prior library CSV path; leave empty for normal use"
    ),
    "target_pair_rt_calibration_path": (
        "target pair RT calibration TSV path for shadow auto-reselection "
        "diagnostics; leave empty to disable"
    ),
    "model_selection_expected_diff_approval_registry": (
        "Durable expected-diff approval registry TSV for selected-hypothesis "
        "model selection; leave empty unless approved real-data evidence exists"
    ),
    "targeted_ms1_shape_identity_support_tsv": (
        "Reviewed targeted_ms1_shape_identity support TSV for explicit "
        "own-max same-peak product projection support. Leave empty to let the "
        "limited default auto workflow decide eligible support rows."
    ),
    "targeted_ms1_shape_identity_activation_policy": (
        "Activation policy for targeted MS1 shape identity support. "
        f"Default {LIMITED_HMDC_MEDC_POLICY} runs the headless bounded auto "
        "workflow when no support TSV is configured, restricting accepted "
        f"support rows to 5-hmdC and 5-medC only. Use {EXPLICIT_SUPPORT_TSV_POLICY} "
        "to preserve explicit support TSV behavior."
    ),
    "emit_score_breakdown": "是否輸出 Score Breakdown sheet（預設關閉）",
    "emit_review_report": "是否輸出 Review Report HTML（預設關閉）",
    "emit_peak_candidates": "是否輸出 Peak Candidate TSV（除錯/審計用，預設關閉）",
    "keep_intermediate_csv": "是否保留中間 CSV 檔（除錯用，預設關閉）",
    "baseline_audit_method": (
        "Integration audit baseline shadow method; leave empty for default TSV "
        "schema or set to asls to emit AsLS comparison columns"
    ),
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
