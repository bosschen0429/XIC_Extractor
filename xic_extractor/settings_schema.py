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


TARGETED_STRATEGY_VERSION = "targeted-cid-nl-v2"

CANONICAL_SETTINGS_DEFAULTS: dict[str, str] = {
    "data_dir": "C:/your/data/folder",
    "dll_dir": "C:/Xcalibur/system/programs",
    "injection_order_source": "",
}
CANONICAL_SETTINGS_DESCRIPTIONS: dict[str, str] = {
    "data_dir": "RAW 批次資料夾",
    "dll_dir": "Xcalibur DLL 資料夾",
    "injection_order_source": "CSV／XLSX 注射順序；留空使用 RAW 修改時間",
}
METHOD_KEYS = ("strategy_version",)
