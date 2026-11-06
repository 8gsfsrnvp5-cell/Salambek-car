# -*- mode: python ; coding: utf-8 -*-
# Сборка: python -m PyInstaller installer/AutoServiceAI.spec --noconfirm --clean
import re
from pathlib import Path

from PyInstaller.utils.hooks import collect_submodules
from PyInstaller.utils.win32.versioninfo import (
    FixedFileInfo, StringFileInfo, StringStruct, StringTable, VarFileInfo, VarStruct, VSVersionInfo,
)

ROOT = Path(SPECPATH).resolve().parent
PKG = ROOT / "src" / "autoservice"
VERSION = re.search(r'__version__ = "([^"]+)"', (PKG / "__init__.py").read_text(encoding="utf-8")).group(1)
nums = tuple(int(x) for x in (VERSION.split(".") + ["0", "0", "0"])[:4])

version_info = VSVersionInfo(
    ffi=FixedFileInfo(filevers=nums, prodvers=nums),
    kids=[
        StringFileInfo([StringTable("041904B0", [
            StringStruct("CompanyName", "AutoService AI"),
            StringStruct("FileDescription", "AutoService AI — управление автосервисом"),
            StringStruct("FileVersion", VERSION),
            StringStruct("InternalName", "AutoServiceAI"),
            StringStruct("OriginalFilename", "AutoServiceAI.exe"),
            StringStruct("ProductName", "AutoService AI"),
            StringStruct("ProductVersion", VERSION),
        ])]),
        VarFileInfo([VarStruct("Translation", [0x0419, 1200])]),
    ],
)

datas = [
    (str(PKG / "data"), "autoservice/data"),
    (str(PKG / "assets"), "autoservice/assets"),
    # Alembic читает миграции как файлы .py — кладём их рядом с пакетом.
    (str(PKG / "persistence" / "migrations"), "autoservice/persistence/migrations"),
]

hiddenimports = (
    collect_submodules("autoservice")
    + collect_submodules("alembic", filter=lambda name: not name.startswith("alembic.testing"))
    + ["sqlalchemy.dialects.sqlite", "mako.template", "mako.runtime"]
)

excludes = [
    "tkinter", "matplotlib", "numpy", "pytest", "IPython",
    "PySide6.QtWebEngineCore", "PySide6.QtWebEngineWidgets", "PySide6.QtWebEngineQuick", "PySide6.QtQuick", "PySide6.QtQml",
    "PySide6.QtMultimedia", "PySide6.Qt3DCore", "PySide6.QtCharts", "PySide6.QtDataVisualization", "PySide6.QtBluetooth",
    "PySide6.QtPositioning", "PySide6.QtLocation", "PySide6.QtSensors", "PySide6.QtSerialPort", "PySide6.QtNetworkAuth",
    "PySide6.QtWebSockets", "PySide6.QtDesigner", "PySide6.QtHelp", "PySide6.QtSql", "PySide6.QtTest",
]

a = Analysis(
    [str(ROOT / "installer" / "launcher.py")],
    pathex=[str(ROOT / "src")],
    datas=datas,
    hiddenimports=hiddenimports,
    excludes=excludes,
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="AutoServiceAI",
    icon=str(PKG / "assets" / "autoservice.ico"),
    version=version_info,
    console=False,
    disable_windowed_traceback=False,
    upx=False,
)
coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name="AutoServiceAI")
