"""Targeted settings policy shared by runtime and presentation adapters.

settings.csv is the active configuration. Method snapshots only transfer the
method-scoped scalar parameters; local resources and run controls stay local.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

from xic_extractor.configuration.models import ConfigError
from xic_extractor.configuration.settings import (
    _ParsedSettings,
    migrate_settings_dict,
    validate_settings_values,
)
from xic_extractor.settings_schema import (
    CANONICAL_SETTINGS_DEFAULTS,
)
from xic_extractor.targeted_ms1_shape_identity_policy import (
    TARGETED_MS1_SHAPE_IDENTITY_ACTIVATION_POLICIES,
)

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
    "targeted_ms1_shape_identity_activation_policy",
)
RESOURCE_KEYS = (
    "rt_prior_library_path",
    "target_pair_rt_calibration_path",
    "model_selection_expected_diff_approval_registry",
    "targeted_ms1_shape_identity_support_tsv",
)
LEGACY_WINDOW_KEY = "ms1_morphology_smoothing_window_points"
SNAPSHOT_VERSION = 1


def _scope(key: str) -> str:
    if key in METHOD_KEYS:
        return "method"
    if key in RESOURCE_KEYS:
        return "resource"
    if key in {"data_dir", "injection_order_source"}:
        return "batch"
    if key == "dll_dir":
        return "environment"
    if key.startswith("parallel_"):
        return "runtime"
    return "output"


_VISIBLE_SETTINGS: tuple[dict[str, Any], ...] = (
    {
        "key": "data_dir",
        "label": "資料目錄",
        "kind": "folder",
        "group": "RAW 資料",
    },
    {
        "key": "dll_dir",
        "label": "DLL 目錄",
        "kind": "folder",
        "group": "本機環境",
    },
    {
        "key": "signal_processing_window_points",
        "label": "Gaussian 平滑視窗（點）",
        "kind": "number",
        "group": "峰形平滑",
        "min": 3,
        "step": 2,
        "help": "奇數 3–999。",
    },
    {
        "key": "ms2_precursor_tol_da",
        "label": "MS2 precursor 容差（Da）",
        "kind": "number",
        "group": "MS2 與 neutral loss 確認",
        "min": 0.01,
        "step": 0.1,
        "help": "掃描 precursor 與推算 precursor 的最大差距。",
    },
    {
        "key": "nl_min_intensity_ratio",
        "label": "NL product 最低強度比",
        "kind": "number",
        "group": "MS2 與 neutral loss 確認",
        "min": 0.01,
        "step": 0.01,
        "help": "相對於該 scan 的 base peak。",
    },
    {
        "key": "nl_rt_anchor_search_margin_min",
        "label": "NL 錨點搜尋半徑（min）",
        "kind": "number",
        "group": "MS2 與 neutral loss 確認",
        "min": 0.0,
        "help": "在目標 RT 中心 ± 此值內尋找 NL 確認的 MS2。",
    },
    {
        "key": "nl_rt_anchor_half_window_min",
        "label": "錨定後 XIC 半寬（min）",
        "kind": "number",
        "group": "MS2 與 neutral loss 確認",
        "min": 0.0,
        "help": "找到錨點時，XIC 視窗為錨點 RT ± 此值。",
    },
    {
        "key": "nl_fallback_half_window_min",
        "label": "未錨定時 XIC 半寬（min）",
        "kind": "number",
        "group": "MS2 與 neutral loss 確認",
        "min": 0.0,
        "help": "找不到錨點時，XIC 視窗為 RT 中心 ± 此值。",
    },
    {
        "key": "count_no_ms2_as_detected",
        "label": "將 NO_MS2 視為偵測到",
        "kind": "checkbox",
        "group": "MS2 與 neutral loss 確認",
        "help": "補救 DDA 未觸發 MS2 造成的假陰性。",
    },
    {
        "key": "rolling_window_size",
        "label": "ISTD RT prior 滾動視窗（± 注射數）",
        "kind": "number",
        "group": "RT 與注射順序",
        "min": 1,
        "step": 1,
    },
    {
        "key": "injection_order_source",
        "label": "注射順序來源",
        "kind": "file",
        "group": "RT 與注射順序",
        "help": (
            "含 Sample_Name 與 Injection_Order 欄的 CSV／XLSX；"
            "留空時使用 RAW 修改時間。"
        ),
    },
    {
        "key": "target_pair_rt_calibration_path",
        "label": "Target pair RT 校正",
        "kind": "file",
        "group": "RT 與注射順序",
        "help": "RT prior 使用的 TSV；留空停用。",
    },
    {
        "key": "rt_prior_library_path",
        "label": "RT prior 參考資料庫",
        "kind": "file",
        "group": "RT 與注射順序",
        "help": "一般使用請留空。",
    },
    {
        "key": "dirty_matrix_mode",
        "label": "複雜基質模式",
        "kind": "checkbox",
        "group": "基質與輸出",
        "help": "放寬 S/N、收緊峰形；用於尿液等複雜基質。",
    },
    {
        "key": "emit_review_report",
        "label": "輸出 Review Report HTML",
        "kind": "checkbox",
        "group": "基質與輸出",
    },
    {
        "key": "emit_score_breakdown",
        "label": "輸出 Score Breakdown 工作表",
        "kind": "checkbox",
        "group": "基質與輸出",
    },
    {
        "key": "keep_intermediate_csv",
        "label": "保留中間 CSV 檔",
        "kind": "checkbox",
        "group": "基質與輸出",
        "help": "除錯用。",
    },
    {
        "key": "parallel_mode",
        "label": "執行方式",
        "kind": "select",
        "group": "執行效能",
        "options": ["serial", "process"],
        "help": "process 平行處理；serial 逐一處理，供除錯使用。",
    },
    {
        "key": "parallel_workers",
        "label": "平行 worker 數",
        "kind": "number",
        "group": "執行效能",
        "min": 1,
        "step": 1,
        "help": "process 模式使用的行程數。",
    },
)


def targeted_setting_fields() -> list[dict[str, Any]]:
    """Describe each current setting once, with owner-defined scope/default."""
    presentations = {field["key"]: field for field in _VISIBLE_SETTINGS}
    presentations.update(
        {
            "model_selection_expected_diff_approval_registry": {
                "key": "model_selection_expected_diff_approval_registry",
                "label": "預期差異核准紀錄",
                "kind": "file",
                "group": "核准與參考資源",
                "help": "已核准的 expected-diff registry TSV；沒有對應核准紀錄時留空。",
            },
            "targeted_ms1_shape_identity_support_tsv": {
                "key": "targeted_ms1_shape_identity_support_tsv",
                "label": "MS1 峰形身分支持資料",
                "kind": "file",
                "group": "核准與參考資源",
                "help": (
                    "已審核的 support TSV；留空時由目前啟用政策處理，不隨方法參數匯出。"
                ),
            },
            "signal_processing_method": {
                "key": "signal_processing_method",
                "label": "平滑方法",
                "kind": "readonly",
                "group": "峰形平滑",
                "options": ["gaussian"],
            },
            "targeted_ms1_shape_identity_activation_policy": {
                "key": "targeted_ms1_shape_identity_activation_policy",
                "label": "MS1 shape identity 啟用政策",
                "kind": "select",
                "group": "方法政策",
                "options": list(TARGETED_MS1_SHAPE_IDENTITY_ACTIVATION_POLICIES),
            },
        }
    )
    fields = []
    for key, default in CANONICAL_SETTINGS_DEFAULTS.items():
        if key == LEGACY_WINDOW_KEY:
            continue
        presentation = presentations[key]
        field = {
            name: list(value) if isinstance(value, list) else value
            for name, value in presentation.items()
        }
        field.update(scope=_scope(key), default=default)
        if key == "count_no_ms2_as_detected":
            field["kind"] = "readonly"
            field["help"] = (
                "保留舊設定供相容性使用；正式 Targeted product projection 不會"
                "將 NO_MS2 算為偵測到。"
            )
            field["compatibility_only"] = True
        fields.append(field)
    return fields


@dataclass(frozen=True)
class ResolvedTargetedSettings:
    values: dict[str, str]
    provenance: dict[str, str]
    warnings: tuple[str, ...]
    receipts: tuple[str, ...]
    parsed: _ParsedSettings
    source: str = "settings.csv"


def resolve_targeted_settings(
    raw: dict[str, str] | ResolvedTargetedSettings,
    overrides: dict[str, str] | None = None,
    *,
    source: str | None = None,
    override_source: str = "override",
) -> ResolvedTargetedSettings:
    """Resolve CSV, the one legacy alias, defaults, then explicit overrides.

    Scalar validation is pure; local directories are admitted only by load_config.
    The receipts explain effective values and never replace byte-bound run hashes.
    """
    previous = raw if isinstance(raw, ResolvedTargetedSettings) else None
    raw_values = raw.values if isinstance(raw, ResolvedTargetedSettings) else raw
    source = source or (previous.source if previous is not None else "settings.csv")
    values, migration_warnings = migrate_settings_dict(raw_values)
    if overrides:
        # Resolve the persisted alias at the input boundary before a canonical
        # method snapshot overrides it. Conflicting aliases in either input fail.
        if overrides.get(LEGACY_WINDOW_KEY, "").strip():
            values.pop("signal_processing_window_points", None)
        values, override_warnings = migrate_settings_dict({**values, **overrides})
        migration_warnings.extend(override_warnings)
    parsed = validate_settings_values(values, Path(source))
    known_keys = set(CANONICAL_SETTINGS_DEFAULTS) - {LEGACY_WINDOW_KEY}
    provenance = {}
    for key in values:
        if key not in known_keys:
            provenance[key] = "preserved"
        elif overrides and (
            key in overrides
            or (
                key == "signal_processing_window_points"
                and LEGACY_WINDOW_KEY in overrides
            )
        ):
            provenance[key] = override_source
        elif previous is not None and key in previous.provenance:
            provenance[key] = previous.provenance[key]
        elif key == "signal_processing_window_points" and (
            not raw_values.get(key, "").strip()
            and raw_values.get(LEGACY_WINDOW_KEY, "").strip()
        ):
            provenance[key] = "legacy_alias"
        elif key in raw_values and raw_values[key] != "":
            provenance[key] = "csv"
        else:
            provenance[key] = "default"
    warnings = list(previous.warnings) if previous is not None else []
    warnings.extend(migration_warnings)
    warnings.extend(
        f"settings key={key} status=unknown_or_retired_preserved; "
        "not consumed by Targeted"
        for key in values
        if key not in known_keys
    )
    if source.endswith("settings.example.csv"):
        warnings.append(
            "settings.csv is absent; using settings.example.csv defaults "
            "(not active until saved)"
        )
    receipts = (
        previous.receipts
        if previous is not None
        else (
            f"source={source}",
            "priority=defaults < settings.csv/legacy_alias < explicit overrides",
        )
    )
    if overrides:
        receipts = (
            *receipts,
            f"override_source={override_source} keys={','.join(overrides)}",
        )
    return ResolvedTargetedSettings(
        values, provenance, tuple(dict.fromkeys(warnings)), receipts, parsed, source
    )


def _setting_values_equal(actual: str, expected: str) -> bool:
    actual_text, expected_text = str(actual).strip(), str(expected).strip()
    if expected_text.lower() in {"true", "false"}:
        return actual_text.lower() == expected_text.lower()
    try:
        return Decimal(actual_text) == Decimal(expected_text)
    except InvalidOperation:
        return actual_text == expected_text


def targeted_method_summary(
    settings: dict[str, str] | ResolvedTargetedSettings,
) -> dict[str, Any]:
    resolved = (
        settings
        if isinstance(settings, ResolvedTargetedSettings)
        else resolve_targeted_settings(settings)
    )
    custom_keys = [
        key
        for key in METHOD_KEYS
        if not _setting_values_equal(
            resolved.values[key], CANONICAL_SETTINGS_DEFAULTS[key]
        )
    ]
    resource_keys = [
        key for key in RESOURCE_KEYS if resolved.values.get(key, "").strip()
    ]
    return {
        "label": f"自訂參數 · {len(custom_keys)} 項調整" if custom_keys else "標準參數",
        "custom_keys": custom_keys,
        "resource_keys": resource_keys,
        "differences": [
            {
                "key": key,
                "default": CANONICAL_SETTINGS_DEFAULTS[key],
                "value": resolved.values[key],
            }
            for key in custom_keys
        ],
        "provenance": resolved.provenance,
        "warnings": list(resolved.warnings),
        "receipts": list(resolved.receipts),
        "source": resolved.source,
        "compatibility": {
            "count_no_ms2_as_detected": {
                "configured": resolved.values["count_no_ms2_as_detected"],
                "projection_effect": (
                    "NO_MS2 不會在正式 Targeted product projection 算為偵測到"
                ),
                "note": (
                    "相容評分流程仍讀取配置值；正式 product projection 不計入 NO_MS2。"
                ),
            },
        },
    }


def reset_targeted_method(settings: dict[str, str]) -> dict[str, str]:
    """Reset method scalars; preserve resources, local controls and retired rows."""
    values, _warnings = migrate_settings_dict(settings)
    values.update({key: CANONICAL_SETTINGS_DEFAULTS[key] for key in METHOD_KEYS})
    return values


def export_targeted_method_snapshot(
    settings: dict[str, str],
    *,
    name: str = "",
    description: str = "",
) -> dict[str, Any]:
    values = resolve_targeted_settings(settings).values
    snapshot = {
        "version": SNAPSHOT_VERSION,
        "name": name,
        "description": description,
        "settings": {key: values[key] for key in METHOD_KEYS},
    }
    snapshot["settings"] = _validate_snapshot(snapshot)
    return snapshot


def _validate_snapshot(snapshot: Any) -> dict[str, str]:
    if not isinstance(snapshot, dict) or set(snapshot) != {
        "version",
        "name",
        "description",
        "settings",
    }:
        raise ConfigError(
            "method snapshot must contain exactly version, name, description, settings"
        )
    if type(snapshot["version"]) is not int or snapshot["version"] != SNAPSHOT_VERSION:
        raise ConfigError(
            f"unsupported method snapshot version {snapshot['version']!r}"
        )
    if not isinstance(snapshot["name"], str) or not isinstance(
        snapshot["description"], str
    ):
        raise ConfigError("method snapshot name and description must be strings")
    settings = snapshot["settings"]
    if not isinstance(settings, dict) or set(settings) != set(METHOD_KEYS):
        raise ConfigError(
            "method snapshot settings must contain all method keys and no other keys"
        )
    values = {}
    for key, value in settings.items():
        if type(value) not in {str, int, float, bool}:
            raise ConfigError(f"method snapshot {key} must be a scalar")
        values[key] = str(value).lower() if isinstance(value, bool) else str(value)
        if not values[key].strip():
            raise ConfigError(f"method snapshot {key} must not be empty")
    # Reuse current parser/ranges without defaulting invalid/missing snapshot values.
    validate_settings_values({**CANONICAL_SETTINGS_DEFAULTS, **values})
    return values


def apply_targeted_method_snapshot(
    settings: dict[str, str],
    snapshot: dict[str, Any],
) -> dict[str, str]:
    imported = _validate_snapshot(snapshot)
    values, _warnings = migrate_settings_dict(settings)
    return {**values, **imported}


def _unique_json_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result = {}
    for key, value in pairs:
        if key in result:
            raise ConfigError(f"method snapshot contains duplicate key {key!r}")
        result[key] = value
    return result


def load_targeted_method_snapshot(path: Path) -> dict[str, Any]:
    try:
        snapshot = json.loads(
            Path(path).read_text(encoding="utf-8-sig"),
            object_pairs_hook=_unique_json_object,
        )
    except (ValueError, OSError) as exc:
        raise ConfigError(f"{path}: invalid method snapshot: {exc}") from exc
    snapshot["settings"] = _validate_snapshot(snapshot)
    return snapshot


def save_targeted_method_snapshot(
    settings: dict[str, str],
    path: Path,
    *,
    name: str = "",
    description: str = "",
) -> None:
    if Path(path).suffix.lower() != ".json":
        raise ConfigError(
            "method parameter snapshot must be saved as a .json file, "
            "not active CSV inputs"
        )
    snapshot = export_targeted_method_snapshot(
        settings, name=name, description=description
    )
    Path(path).write_text(
        json.dumps(snapshot, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
