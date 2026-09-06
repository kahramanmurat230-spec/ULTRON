#!/usr/bin/env python3
"""Stage a native, self-contained Windows backend for Electron Builder.

PyInstaller intentionally runs only on Windows: it cannot safely cross-compile
Python extension modules. The Windows GitHub Actions job and release machines
therefore create the same backend that ships beside ULTRON.exe.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
BACKEND = ROOT / "backend"
DESKTOP = ROOT / "desktop"
STAGE = DESKTOP / ".stage"
DIST = STAGE / "pyinstaller-dist"
WORK = STAGE / "pyinstaller-work"
SPEC = STAGE / "pyinstaller-spec"
OUTPUT = STAGE / "backend"


def main() -> int:
    if os.name != "nt":
        print("Windows backend staging requires a native Windows Python runtime.", file=sys.stderr)
        return 2
    for directory in (DIST, WORK, SPEC, OUTPUT):
        shutil.rmtree(directory, ignore_errors=True)
    STAGE.mkdir(parents=True, exist_ok=True)

    separator = os.pathsep
    command = [
        sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean", "--onedir",
        "--name", "ULTRON-Backend",
        "--distpath", str(DIST), "--workpath", str(WORK), "--specpath", str(SPEC),
        "--paths", str(BACKEND),
        "--add-data", f"{BACKEND / 'config'}{separator}config",
        "--collect-submodules", "app",
        "--collect-data", "app",
        "--hidden-import", "server",
        str(BACKEND / "server.py"),
    ]
    print("+", subprocess.list2cmdline(command))
    subprocess.run(command, cwd=ROOT, check=True)

    built = DIST / "ULTRON-Backend"
    executable = built / "ULTRON-Backend.exe"
    if not executable.is_file():
        raise RuntimeError(f"PyInstaller output missing: {executable}")
    shutil.copytree(built, OUTPUT)
    print(f"Staged native backend: {OUTPUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
