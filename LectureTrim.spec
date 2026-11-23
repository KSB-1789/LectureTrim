# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec: one executable, with FFmpeg and the Silero model bundled."""

import shutil
from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files, collect_dynamic_libs, copy_metadata

PROJECT = Path(SPECPATH)

datas = []
binaries = []
hiddenimports = [
    "lecturetrim",
    "lecturetrim.media",
    "lecturetrim.vad",
    "lecturetrim.fastvision",
    "lecturetrim.timeline",
]

# Package data only. collect_all() imports every submodule of torch, which
# takes minutes and is redundant with PyInstaller's own torch hooks.
for package in (
    "silero_vad",
    "webview",
    "webview.dom",
    "pywebview",
    "numpy",
    "torch",
    "torchaudio",
):
    try:
        datas.extend(collect_data_files(package))
        binaries.extend(collect_dynamic_libs(package))
        # Several libraries read their own version through importlib.metadata
        # at import time. Without dist-info the frozen app dies before opening.
        datas.extend(copy_metadata(package))
    except Exception as exc:  # a missing optional package must not kill the build
        print(f"[LectureTrim] data collection for {package} skipped: {exc}")

# De-duplicate collected entries (torch hooks and collect_all overlap heavily).
def dedupe(entries):
    seen = set()
    result = []
    for entry in entries:
        key = tuple(entry) if isinstance(entry, (tuple, list)) else entry
        if key not in seen:
            seen.add(key)
            result.append(entry)
    return result


datas = dedupe(datas)
binaries = dedupe(binaries)
hiddenimports = dedupe(hiddenimports)

# The app resolves ffmpeg/ffprobe from <bundle>/bin before looking on PATH.
for name in ("ffmpeg", "ffprobe"):
    found = shutil.which(name)
    if found:
        binaries.append((found, "bin"))
    else:
        print(f"[LectureTrim] WARNING: {name} not found on PATH; bundle will need it")

a = Analysis(
    [str(PROJECT / "native_main.py")],
    pathex=[str(PROJECT)],
    binaries=binaries,
    datas=datas + [
        (str(PROJECT / "ui"), "ui"),
        (str(PROJECT / "tokens.css"), "."),
        (str(PROJECT / "base.css"), "."),
    ],
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        "cv2",
        "PIL",
        "matplotlib",
        "scipy",
        "pandas",
        "streamlit",
        "tkinter",
        "onnxruntime",
    ],
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    name="LectureTrim",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
