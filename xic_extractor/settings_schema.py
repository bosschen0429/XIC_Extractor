from xic_extractor.thermo_reader_install import default_thermo_reader_dir

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


TARGETED_STRATEGY = "cid-nl"

CANONICAL_SETTINGS_DEFAULTS: dict[str, str] = {
    "data_dir": "C:/your/data/folder",
    "dll_dir": default_thermo_reader_dir().as_posix(),
    "injection_order_source": "",
    "parallel_workers": "",
}
CANONICAL_SETTINGS_DESCRIPTIONS: dict[str, str] = {
    "data_dir": "RAW 批次資料夾",
    "dll_dir": "RawFileReader 8 DLL 資料夾",
    "injection_order_source": "上機資訊（Word／Excel／CSV／TSV），補充樣本角色",
    "parallel_workers": "RAW worker 數；留空使用 CPU 數減一（至少 1）",
}
METHOD_KEYS = ("strategy",)
