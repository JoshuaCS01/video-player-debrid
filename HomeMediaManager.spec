from pathlib import Path

root = Path(SPECPATH)
optional_ffmpeg = root / "bin" / "ffmpeg.exe"
binaries = [(str(optional_ffmpeg), "bin")] if optional_ffmpeg.exists() else []

a = Analysis(
    ["desktop.py"],
    pathex=[str(root)],
    binaries=binaries,
    datas=[(str(root / "templates"), "templates"), (str(root / "static"), "static")],
    hiddenimports=[],
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz, a.scripts, a.binaries, a.datas,
    name="HomeMediaManager",
    debug=False,
    strip=False,
    upx=True,
    console=False,
)
