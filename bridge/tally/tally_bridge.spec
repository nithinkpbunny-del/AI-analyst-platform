# PyInstaller build of the Tally bridge for Windows (run on a Windows PC with Python 3.12):
#
#   cd bridge\tally
#   uv sync --group build
#   uv run pyinstaller tally_bridge.spec
#
# Output: dist\tally-bridge.exe (one file, console). The development stand-in and fake helper are left out.
# Sign the .exe with the company's code-signing certificate before giving it to customers (docs/SETUP_TALLY.md).
# -*- mode: python ; coding: utf-8 -*-

a = Analysis(
    ["tally_bridge/__main__.py"],
    pathex=["."],
    binaries=[],
    datas=[],
    hiddenimports=[],
    excludes=["tally_bridge.dev", "tkinter", "unittest", "pydoc"],
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="tally-bridge",
    console=True,
    upx=False,
    version=None,
)
