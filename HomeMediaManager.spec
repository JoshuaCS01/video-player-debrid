from pathlib import Path

root = Path(SPECPATH)
optional_ffmpeg = root / "bin" / "ffmpeg.exe"
binaries = [(str(optional_ffmpeg), "bin")] if optional_ffmpeg.exists() else []

# Private/runtime files intentionally stay beside the executable and are not bundled:
# .env, fullNew.pls, playlist.json, caches, thumbnails, and import/source lists.

a = Analysis(
    ["desktop.py"],
    pathex=[str(root)],
    binaries=binaries,
    datas=[(str(root / "templates"), "templates"), (str(root / "static"), "static")],
    hiddenimports=[],
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="HomeMediaManager",
    debug=False,
    strip=False,
    upx=True,
    console=False,
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    name="HomeMediaManager",
)
