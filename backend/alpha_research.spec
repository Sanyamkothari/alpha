# -*- mode: python ; coding: utf-8 -*-

from pathlib import Path
import sys

block_cipher = None

# Root directory of backend (where pyproject.toml / app/ lives)
SPEC_DIR = Path(SPECPATH).resolve()
REPO_ROOT = SPEC_DIR.parent

# Collect required static asset and template directories
datas = [
    (str(SPEC_DIR / "app" / "static" / "index.html"), "app/static"),
    (str(SPEC_DIR / "migrations"), "migrations"),
    (str(REPO_ROOT / "operators"), "operators"),
    (str(REPO_ROOT / "fields"), "fields"),
]
if (REPO_ROOT / "templates").exists():
    datas.append((str(REPO_ROOT / "templates"), "templates"))

hiddenimports = [
    "uvicorn.logging",
    "uvicorn.loops",
    "uvicorn.loops.auto",
    "uvicorn.protocols",
    "uvicorn.protocols.http",
    "uvicorn.protocols.http.auto",
    "uvicorn.protocols.http.h11_impl",
    "uvicorn.protocols.websockets",
    "uvicorn.protocols.websockets.auto",
    "uvicorn.lifespan",
    "uvicorn.lifespan.on",
    "uvicorn.lifespan.off",
    "alembic",
    "alembic.config",
    "alembic.command",
    "alembic.runtime.environment",
    "alembic.script",
    "sqlalchemy.ext.baked",
    "sqlalchemy.engine.interfaces",
    "sqlalchemy.dialects.sqlite",
    "pydantic",
    "pydantic_settings",
    "app.models",
    "app.routers",
    "app.seeds",
    "app.seeds.load_fields",
    "app.seeds.load_lookups",
    "app.seeds.load_operators",
]

a = Analysis(
    ['app/desktop.py'],
    pathex=[str(SPEC_DIR)],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name='alpha-research-desktop',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=True,
    disable_windowed_traceback=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
