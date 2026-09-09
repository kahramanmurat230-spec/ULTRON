"""Build a self-contained Windows backend executable with PyInstaller.
Run from backend after installing requirements + pyinstaller.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DIST = ROOT / "dist"

cmd = [
    sys.executable, "-m", "PyInstaller",
    "--noconfirm", "--clean", "--onedir", "--name", "ultron-backend",
    "--add-data", f"{ROOT / 'config'};config",
    "server.py",
]
print("Building:", " ".join(map(str, cmd)))
subprocess.run(cmd, cwd=ROOT, check=True)
print(f"Backend bundle ready: {DIST / 'ultron-backend'}")
