"""Targeted form fields and method descriptions without analysis execution."""

from typing import Any

from xic_extractor.settings_schema import CANONICAL_SETTINGS_DEFAULTS

# Labels and controls are presentation only.
_SETTING_FIELDS: tuple[tuple[str, str, str, dict[str, Any]], ...] = (
    ("data_dir", "RAW 資料夾", "folder", {}),
    (
        "injection_order_source",
        "上機資訊",
        "file",
        {
            # The same 上機資訊 files as Untargeted.
            "browse": "metadata",
            "help": "Word、Excel、CSV 或 TSV。補充樣本角色、組別與排除標記；"
            "注射順序與樣本名稱以 RAW 為準。",
        },
    ),
    ("dll_dir", "RawFileReader DLL 資料夾", "folder", {}),
)


def setting_fields() -> list[dict[str, Any]]:
    fields = []
    for key, label, kind, options in _SETTING_FIELDS:
        scope = (
            "batch"
            if key in {"data_dir", "injection_order_source"}
            else "environment"
            if key == "dll_dir"
            else "runtime"
            if key.startswith("parallel_")
            else "output"
        )
        fields.append(
            dict(
                key=key,
                label=label,
                kind=kind,
                scope=scope,
                default=CANONICAL_SETTINGS_DEFAULTS[key],
                **options,
            )
        )
    return fields
