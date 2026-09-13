"""Build a self-contained Windows backend executable with PyInstaller.
Run from backend after installing requirements + pyinstaller:

    python build_windows.py

Importing this module must be side-effect free (it is scanned by import
graph audits); the build only runs when executed as a script.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DIST = ROOT / "dist"


def main() -> int:
    cmd = [
        sys.executable, "-m", "PyInstaller",
        "--noconfirm", "--clean", "--onedir", "--name", "ultron-backend",
        # PyInstaller --add-data separator: ';' on Windows, ':' on POSIX.
        "--add-data", f"{ROOT / 'config'}{os.pathsep}config",
        "server.py",
    ]
    print("Building:", " ".join(map(str, cmd)))
    subprocess.run(cmd, cwd=ROOT, check=True)
    print(f"Backend bundle ready: {DIST / 'ultron-backend'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
