# -*- mode: python ; coding: utf-8 -*-

import os
import sys

from PyInstaller.utils.hooks import collect_data_files, collect_dynamic_libs, collect_submodules


PROJECT_ROOT = os.path.abspath(SPECPATH)
HIDDEN_IMPORTS = collect_submodules("libxrk") + collect_submodules("tkinterdnd2") + [
    "ldparser.ldparser",
    "tkinter",
    "tkinter.ttk",
    "tkinter.filedialog",
    "tkinter.messagebox",
]
DATAS = collect_data_files("libxrk") + collect_data_files("tkinterdnd2")
BINARIES = collect_dynamic_libs("libxrk") + collect_dynamic_libs("tkinterdnd2")


a = Analysis(
    ["motec_log_generator.py"],
    pathex=[PROJECT_ROOT],
    binaries=BINARIES,
    datas=DATAS,
    hiddenimports=HIDDEN_IMPORTS,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
)
pyz = PYZ(a.pure)

if sys.platform == "darwin":
    exe = EXE(
        pyz,
        a.scripts,
        [],
        exclude_binaries=True,
        name="MotecLogGenerator",
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
        name="MotecLogGenerator",
    )

    app = BUNDLE(
        coll,
        name="MotecLogGenerator.app",
        icon=None,
        bundle_identifier="com.acloran.motecloggenerator",
        info_plist={
            "CFBundleName": "MotecLogGenerator",
            "CFBundleDisplayName": "MotecLogGenerator",
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
        name="MotecLogGenerator",
        debug=False,
        bootloader_ignore_signals=False,
        strip=False,
        upx=True,
        upx_exclude=[],
        runtime_tmpdir=None,
        console=False,
        disable_windowed_traceback=False,
        argv_emulation=False,
        target_arch=None,
        codesign_identity=None,
        entitlements_file=None,
    )
