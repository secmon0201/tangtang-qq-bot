from pathlib import Path
from PyInstaller.utils.hooks import collect_submodules


root = Path(SPECPATH)

a = Analysis(
    [str(root / "manager.py")],
    pathex=[str(root)],
    binaries=[],
    datas=[
        (str(root / "bot"), "bot"),
        (str(root / "data" / "zhijiang_hourly_copy.json"), "data"),
    ],
    hiddenimports=[
        *collect_submodules("bot"),
        *collect_submodules("nonebot.drivers"),
        "bot.__main__",
        "bot.plugins.commands",
        "bot.plugins.stats",
        "bot.services.gateway",
        "bot.services.runtime",
        "nonebot.adapters.onebot.v11",
        "nonebot.drivers.fastapi",
        "nonebot.drivers",
        "uvicorn.logging",
        "uvicorn.loops.auto",
        "uvicorn.protocols.http.auto",
        "uvicorn.protocols.websockets.auto",
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="QQBotManager",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
)
