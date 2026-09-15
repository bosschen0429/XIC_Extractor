# xic_extractor.spec
# -*- mode: python ; coding: utf-8 -*-

import os
import sys

from PyInstaller.utils.hooks import collect_data_files

if sys.platform == "win32":
    # Resolve Windows DLLs before unrelated tools on PATH (notably ICU).
    # Package-specific directories are still supplied by PyInstaller's hooks.
    system32 = os.path.join(os.environ["SystemRoot"], "System32")
    os.environ["PATH"] = system32 + os.pathsep + os.environ.get("PATH", "")

block_cipher = None

preset_datas = collect_data_files(
    "xic_extractor.presets",
    includes=["data/*.toml"],
)
authority_datas = collect_data_files(
    "xic_extractor.alignment",
    includes=["data/quantitative_projection_authority_v1.json"],
)

a = Analysis(
    ["gui/main.py"],
    pathex=[],
    binaries=[],
    datas=[
        ("assets", "assets"),
        ("config/settings.example.csv", "config"),
        ("config/targets.example.csv", "config"),
        ("scripts", "scripts"),
    ]
    + preset_datas
    + authority_datas,
    hiddenimports=[
        "PyQt6.QtCore",
        "PyQt6.QtWidgets",
        "PyQt6.QtGui",
        "PyQt6.sip",
        "pythonnet",
        "clr",
        "openpyxl",
        "openpyxl.styles",
        "openpyxl.utils",
        "scripts.csv_to_excel",
        "scripts.xlsx_to_targets",
        "xic_inspector.api",
        "xic_inspector.sources.cid_nl",
        "xic_inspector.rendering.html_builder",
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="XIC_Extractor",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon="assets/app_icon.png",
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="XIC_Extractor",
)
