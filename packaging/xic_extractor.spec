# -*- mode: python ; coding: utf-8 -*-

import os
import sys

from PyInstaller.utils.hooks import collect_data_files

if sys.platform == "win32":
    system32 = os.path.join(os.environ["SystemRoot"], "System32")
    os.environ["PATH"] = system32 + os.pathsep + os.environ.get("PATH", "")

block_cipher = None

SPEC_DIR = globals().get("SPECPATH") or os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.abspath(SPEC_DIR))

preset_datas = collect_data_files(
    "xic_extractor.presets",
    includes=["data/*.toml"],
)
inspector_datas = collect_data_files("xic_inspector.rendering", includes=["assets/*"])
workbench_datas = collect_data_files(
    "xic_workbench",
    includes=["assets/*.html", "assets/*.js", "assets/*.css"],
)

a = Analysis(
    [os.path.join(ROOT, "xic_workbench", "__main__.py")],
    pathex=[ROOT],
    binaries=[],
    datas=[
        (
            os.path.join(ROOT, "xic_extractor", "configuration", "data"),
            "xic_extractor/configuration/data",
        ),
    ]
    + preset_datas
    + inspector_datas
    + workbench_datas,
    hiddenimports=[
        "pythonnet",
        "clr",
        "openpyxl",
        "openpyxl.styles",
        "openpyxl.utils",
        "xic_inspector.api",
        "xic_inspector.sources.cid_nl",
        "xic_inspector.rendering.html_builder",
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["PyQt6", "PySide6"],
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
    icon=os.path.join(SPEC_DIR, "app_icon.png"),
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
