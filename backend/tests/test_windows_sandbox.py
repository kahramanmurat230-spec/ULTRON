"""Windows filesystem-sandbox logic regression tests.

Pure-logic tests using PureWindowsPath parts — pathlib yields the same
('C:\\', 'Windows', ...) parts tuples on real Windows, so this locks the
Windows behavior of FilesystemSandbox on any platform.

Locked semantics:
- a resolved write inside ANY drive's Windows system tree (Windows,
  Program Files, Program Files (x86), ProgramData, $Recycle.Bin, System
  Volume Information, Perflogs) -> SandboxViolation, even when the write
  root would otherwise contain it (e.g. workspace = drive root)
- ordinary user trees (Users\\<u>\\..., Temp, projects) are NOT system dirs
- UNC paths are rejected before resolution
"""

import sys
from pathlib import Path, PureWindowsPath

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.security.sandbox import FilesystemSandbox, SandboxViolation  # noqa: E402


def test_windows_system_dir_parts_detected():
    f = FilesystemSandbox._is_system_dir_path
    cases = [
        (PureWindowsPath("C:/Windows/System32/x").parts, True),
        (PureWindowsPath("c:\\windows").parts, True),
        (PureWindowsPath("D:/Program Files/App").parts, True),
        (PureWindowsPath("E:/Program Files (x86)/Y").parts, True),
        (PureWindowsPath("C:/ProgramData/z").parts, True),
        (PureWindowsPath("C:/$Recycle.Bin").parts, True),
        (PureWindowsPath("F:/System Volume Information/x").parts, True),
        (PureWindowsPath("C:/PerfLogs").parts, True),
    ]
    for parts, expected in cases:
        assert f(parts) is expected, (parts, expected)


def test_windows_non_system_parts_allowed():
    f = FilesystemSandbox._is_system_dir_path
    cases = [
        (PureWindowsPath("C:/Users/Boss/ULTRON/backend").parts, False),
        (PureWindowsPath("C:/Users/Boss/Documents/x").parts, False),
        (PureWindowsPath("C:/Temp/normal").parts, False),
        (PureWindowsPath("C:/myproject/windows_backup").parts, False),  # deep, not drive-root
        (PureWindowsPath("C:/Users").parts, False),  # user tree — approval domain, not sandbox
    ]
    for parts, expected in cases:
        assert f(parts) is expected, (parts, expected)


def test_posix_defensive_windows_form():
    # posix-style /windows/... (e.g. MSYS-mapped) is still caught
    assert FilesystemSandbox._is_system_dir_path(("/", "Windows", "System32")) is True
    assert FilesystemSandbox._is_system_dir_path(("/", "home", "user", "x")) is False


def test_check_not_system_called_on_write(tmp_path):
    # on this platform a literal /Windows dir can be simulated inside tmp
    ws = tmp_path / "ws"
    ws.mkdir()
    sb = FilesystemSandbox(workspace_root=str(ws))
    # ordinary write inside workspace succeeds
    rp = sb.check(str(ws / "dosya.txt"), write=True)
    assert str(rp).startswith(str(ws))


def test_unc_rejected_for_read_and_write(tmp_path):
    sb = FilesystemSandbox(workspace_root=str(tmp_path))
    for raw in ("\\\\server\\share\\f", "//server/share/f"):
        for write in (False, True):
            try:
                sb.check(raw, write=write)
                raise AssertionError(f"UNC not rejected: {raw!r} write={write}")
            except SandboxViolation:
                pass
