# -*- mode: python ; coding: utf-8 -*-

import os
import sys

from PyInstaller.utils.hooks import collect_data_files, collect_dynamic_libs, collect_submodules


PROJECT_ROOT = os.path.abspath(SPECPATH)
ICON_PNG = os.path.join(PROJECT_ROOT, "assets", "app_icon_256.png")
ICON_ICNS = os.path.join(PROJECT_ROOT, "assets", "MotecLogGeneratorQt.icns")
ICON_ICO = os.path.join(PROJECT_ROOT, "assets", "MotecLogGeneratorQt.ico")
HIDDEN_IMPORTS = collect_submodules("libxrk") + [
    "ldparser.ldparser",
]
DATAS = collect_data_files("libxrk") + [
    (ICON_PNG, "assets"),
]
BINARIES = collect_dynamic_libs("libxrk")


a = Analysis(
    ["motec_log_generator_qt.py"],
    pathex=[PROJECT_ROOT],
    binaries=BINARIES,
    datas=DATAS,
    hiddenimports=HIDDEN_IMPORTS,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["tkinter", "tkinterdnd2", "matplotlib", "pandas", "pyarrow", "jinja2"],
    noarchive=False,
)
pyz = PYZ(a.pure)

if sys.platform == "darwin":
    exe = EXE(
        pyz,
        a.scripts,
        [],
        exclude_binaries=True,
        name="MotecLogGeneratorQt",
        debug=False,
        bootloader_ignore_signals=False,
        strip=False,
        upx=True,
        upx_exclude=[],
        runtime_tmpdir=None,
        console=False,
        disable_windowed_traceback=False,
        argv_emulation=True,
        target_arch=None,
        codesign_identity=os.environ.get("APPLE_CODESIGN_IDENTITY") or None,
        entitlements_file=None,
    )

    coll = COLLECT(
        exe,
        a.binaries,
        a.zipfiles,
        a.datas,
        strip=False,
        upx=True,
        upx_exclude=[],
        name="MotecLogGeneratorQt",
    )

    app = BUNDLE(
        coll,
        name="MotecLogGeneratorQt.app",
        icon=ICON_ICNS,
        bundle_identifier="com.acloran.motecloggenerator.qt",
        info_plist={
            "CFBundleName": "MotecLogGeneratorQt",
            "CFBundleDisplayName": "MotecLogGeneratorQt",
            "CFBundleShortVersionString": "1.0.0",
            "CFBundleVersion": "1.0.0",
        },
    )
else:
    exe = EXE(
        pyz,
        a.scripts,
        a.binaries,
        a.zipfiles,
        a.datas,
        [],
        name="MotecLogGeneratorQt",
        debug=False,
        bootloader_ignore_signals=False,
        strip=False,
        upx=True,
        upx_exclude=[],
        runtime_tmpdir=None,
        console=False,
        disable_windowed_traceback=False,
        argv_emulation=False,
        icon=ICON_ICO,
        target_arch=None,
        codesign_identity=None,
        entitlements_file=None,
    )
