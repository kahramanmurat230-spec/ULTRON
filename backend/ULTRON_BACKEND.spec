from pathlib import Path
from PyInstaller.utils.hooks import collect_submodules

ROOT = Path(__file__).resolve().parent

hiddenimports = collect_submodules('app')
hiddenimports += [
    'aiohttp',
    'aiohttp.web',
    'psutil',
]

datas = []
config_dir = ROOT / 'config'
if config_dir.exists():
    datas.append((str(config_dir), 'config'))

# Keep runtime data directories available when present, while user-generated
# data remains beside the packaged backend executable.
for name in ('assets', 'models'):
    src = ROOT / name
    if src.exists():
        datas.append((str(src), name))

analysis = Analysis(
    ['server.py'],
    pathex=[str(ROOT)],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
)

pyz = PYZ(analysis.pure)
exe = EXE(
    pyz,
    analysis.scripts,
    analysis.binaries,
    analysis.datas,
    [],
    name='ULTRON_BACKEND',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
)
