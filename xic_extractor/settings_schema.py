from xic_extractor.thermo_reader_install import default_thermo_reader_dir

TARGETED_STRATEGY = "cid-nl"

CANONICAL_SETTINGS_DEFAULTS: dict[str, str] = {
    "data_dir": "C:/your/data/folder",
    "dll_dir": default_thermo_reader_dir().as_posix(),
    "injection_order_source": "",
}
CANONICAL_SETTINGS_DESCRIPTIONS: dict[str, str] = {
    "data_dir": "RAW 批次資料夾",
    "dll_dir": "RawFileReader 8 DLL 資料夾",
    "injection_order_source": "上機資訊（Word／Excel／CSV／TSV），補充樣本角色",
}
