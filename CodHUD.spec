# PyInstaller build for the desktop app.
#
#   pip install -r requirements-windows.txt pyinstaller
#   pyinstaller --noconfirm CodHUD.spec
#
# Produces dist/CodHUD/CodHUD.exe (plus its _internal folder). The GitHub
# Actions workflow in .github/workflows/build-windows.yml runs this.
import os
import sys

root = SPECPATH
icon = os.path.join(root, "packaging", "icon.ico")
from PyInstaller.utils.hooks import collect_all

hidden, datas, binaries = [], [(os.path.join(root, "web"), "web")], []
# Custom design (HUD Builder, built in): its pages, the PSD reader and Figma files' zstandard.
from PyInstaller.utils.hooks import collect_all
datas.append((os.path.join(root, "hudbuilder", "web"), os.path.join("hudbuilder", "web")))
for package in ("psd_tools", "zstandard"):
    d, b, h = collect_all(package)
    datas, binaries, hidden = datas + d, binaries + b, hidden + h
if sys.platform == "win32":
    # pywebview's Windows window (Edge WebView2) is loaded at runtime.
    hidden += ["webview.platforms.winforms", "webview.platforms.edgechromium", "clr"]
    # Windows text recognition (reading the players' names).
    d, b, h = collect_all("winrt")
    datas, binaries, hidden = datas + d, binaries + b, hidden + h

a = Analysis(
    [os.path.join(root, "CodHUD.py")],
    pathex=[root],
    datas=datas,
    binaries=binaries,
    hiddenimports=hidden,
    excludes=["tkinter"],
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="CodHUD",
    console=False,
    icon=icon if sys.platform == "win32" else None,
)
coll = COLLECT(exe, a.binaries, a.datas, name="CodHUD")
