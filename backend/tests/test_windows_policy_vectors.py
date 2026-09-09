"""Windows security-policy vector regression tests.

Pure text-analysis tests: Windows path/command STRINGS evaluated by
ShellPolicy. No Windows OS required — this locks in the Windows behavior
of the policy (cmd.exe / PowerShell / Windows paths / drive roots / UNC /
chaining) on any platform, so a policy regression is caught before it can
reach a real Windows deployment.

Semantics locked here:
- destructive Windows commands (format/diskpart/rd /s/del /s/reg/netsh/
  net user/sc/vssadmin/wevtutil/shutdown/Remove-Item -Recurse) -> HARD BLOCK
- PowerShell -EncodedCommand (any prefix: -enc/-encodedcommand, powershell/
  pwsh) -> HARD BLOCK; plain readable -command stays allowed
- Windows system trees on ANY drive letter, case-insensitive, both
  separators (\ and /), incl. redirection INTO them -> HARD BLOCK
- Windows critical files (registry hives, hosts, cmd.exe, pagefile...) for
  rm/del/erase and for redirection, flags or not -> HARD BLOCK
- C:\\Users and C:\\Users\\<one profile> (the /home/<user> analog) -> HARD
  BLOCK; deeper user paths stay approval-gated (allowed=True from policy)
- drive roots, drive-root globs, UNC paths (rm and redirection) -> HARD BLOCK
- workspace destruction via Windows paths (backslashes, any casing, any
  ancestor) -> HARD BLOCK; workspace SUBDIRS stay approval-gated
- single `&` (cmd.exe chaining) is analyzed like `&&` — the second command
  cannot smuggle a blocked payload; benign URL `&` stays allowed
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.security.shell_policy import ShellPolicy  # noqa: E402

WS = "C:/Users/Boss/ULTRON/backend"  # Windows-style workspace


def _blocked(policy, cmd, workspace=WS):
    d = policy.evaluate(cmd, workspace=workspace)
    assert d.allowed is False, f"expected BLOCK for {cmd!r}, got {d!r}"
    return d


def _allowed(policy, cmd, workspace=WS):
    d = policy.evaluate(cmd, workspace=workspace)
    assert d.allowed is True, f"expected ALLOW for {cmd!r}, got {d!r}"
    return d


def test_cmdexe_destructive_family_blocked():
    p = ShellPolicy()
    for cmd in [
        "format C: /fs:ntfs",
        "format D:",
        "diskpart /s script.txt",
        "rd /s /q C:\\Users",
        "rd /s /q C:\\Temp",
        "del /f /s /q C:\\Users\\Boss\\dosya.txt",
        "reg delete HKLM\\SOFTWARE\\Ultrn /f",
        "reg add HKLM\\SOFTWARE\\X /v k /d v",
        "netsh advfirewall set allprofiles state off",
        "net user Administrator Pass123!",
        "sc delete wuauserv",
        "sc stop wuauserv",
        "vssadmin delete shadows /all /quiet",
        "wevtutil cl System",
        "shutdown /s /t 0",
        "Stop-Computer -Force",
        "Restart-Computer -Force",
        "powershell -c \"rm -rf C:\\\"",
        "cmd /c del /q C:\\Windows\\win.ini",
    ]:
        _blocked(p, cmd)


def test_powershell_encoded_command_blocked():
    p = ShellPolicy()
    for cmd in [
        "powershell -EncodedCommand AG1yAG0AIAAtAHIAZgAgAC8A",
        "powershell -encodedcommand AG1yAG0A",
        "powershell -enc AG1yAG0A",
        "pwsh -EncodedCommand xyz",
        "pwsh -enc xyz",
    ]:
        _blocked(p, cmd)
    # readable, non-encoded usage stays available (approval path intact)
    _allowed(p, "powershell -command Get-Process")


def test_windows_system_trees_any_drive_case_separator():
    p = ShellPolicy()
    for cmd in [
        "rm -rf C:\\Windows",
        "rm -rf c:\\windows\\system32",
        "rm -rf C:/Windows/Temp/kalinti",
        "rm -rf D:\\Program Files\\App",
        'rm -rf "D:\\Program Files\\App"',
        "rm -rf E:\\ProgramData\\ultron",
        "rm -rf D:\\Program Files (x86)\\X",
        "chmod -R 777 C:\\Windows",
        "echo x > C:\\Windows\\System32\\config\\SAM",
        "echo x > C:\\Windows\\System32\\drivers\\etc\\hosts",
        "echo x >> c:\\windows\\system32\\config\\system",
        "echo x > C:\\Windows\\deneme.txt",
    ]:
        _blocked(p, cmd)


def test_windows_critical_files_rm_del_erase_redirect():
    p = ShellPolicy()
    for cmd in [
        "rm C:\\Windows\\System32\\config\\SAM",
        "del C:\\Windows\\System32\\cmd.exe",
        "erase C:\\Windows\\System32\\config\\SAM",
        "del /f C:\\Windows\\System32\\drivers\\etc\\hosts",
        "rm c:/windows/system32/drivers/etc/hosts",
        "echo x > C:\\pagefile.sys",
        "echo x > C:\\hiberfil.sys",
        "echo x > c:\\boot\\bcd",
        "rm C:\\Windows\\explorer.exe",
    ]:
        _blocked(p, cmd)
    # ordinary single-file deletion stays allowed (consistent with Unix rm)
    _allowed(p, "del C:\\Temp\\normal.txt")
    _allowed(p, "del D:\\Temp\\normal.txt")


def test_program_files_space_split_backstop():
    # unquoted paths with spaces split into two tokens during tokenization;
    # the joined-target backstop must still recognize the system tree
    p = ShellPolicy()
    _blocked(p, "rm -rf D:\\Program Files\\App")
    _blocked(p, "rm -rf C:\\Program Files (x86)\\X")
    # ...but an ordinary path that merely CONTAINS a space is not blocked
    _allowed(p, "rm -rf D:\\Projeler\\Benim Klasor")


def test_windows_user_profile_rules():
    p = ShellPolicy()
    _blocked(p, "rm -rf C:\\Users")            # every profile
    _blocked(p, "rm -rf C:\\Users\\Boss")      # one whole profile (/home/<u> analog)
    _blocked(p, "rm -rf c:\\users\\boss")      # case-insensitive
    # deeper user paths stay approval-gated by design (like ~/Documents/x)
    _allowed(p, "rm -rf C:\\Users\\Boss\\Documents\\eski")
    _allowed(p, "rm -rf C:\\Users\\Boss\\Downloads\\tmp")


def test_drive_roots_and_unc_blocked():
    p = ShellPolicy()
    for cmd in [
        "rm -rf C:\\",
        "rm -rf C:\\*",
        "rm -rf c:/",
        "rm -rf D:\\*",
        "rm -rf \\\\server\\share",
        "rm -rf \\\\server\\share\\klasor",
        "echo x > \\\\server\\share\\f",
    ]:
        _blocked(p, cmd)
    # non-system drive paths stay approval-gated
    _allowed(p, "rm -rf D:\\\\Temp\\\\normal")
    _allowed(p, "echo cikti > D:\\Projeler\\out.txt")


def test_workspace_destruction_windows_paths():
    p = ShellPolicy()
    _blocked(p, "rm -rf C:\\Users\\Boss\\ULTRON")             # ancestor, backslashes
    _blocked(p, "rm -rf c:\\users\\boss\\ultron")             # case-insensitive
    _blocked(p, "rm -rf C:\\Users\\Boss\\ULTRON\\backend")    # workspace itself
    _blocked(p, "rm -rf C:/Users/Boss/ULTRON/backend")        # forward slashes
    # workspace SUBDIRS stay approval-gated (by-design escape hatch)
    _allowed(p, "rm -rf .\\data\\gecici")
    _allowed(p, "rm -rf C:/Users/Boss/ULTRON/backend/data/gecici")
    # Unix workspace protection unchanged
    _blocked(p, "rm -rf /home/user/ULTRON", workspace="/home/user/ULTRON/backend")
    _blocked(p, "rm -rf /home/user/ULTRON/backend", workspace="/home/user/ULTRON/backend")


def test_single_ampersand_chaining_analyzed():
    p = ShellPolicy()
    for cmd in [
        "echo x & rm -rf C:\\Windows",
        "dir & del C:\\Windows\\System32\\cmd.exe",
        "echo a & rm -rf C:\\Users\\Boss\\ULTRON",
        "dir & format E:",
        "echo x & rm -rf /etc",
    ]:
        _blocked(p, cmd)
    # benign uses of & (URLs, plain text) stay allowed
    _allowed(p, "curl http://x/?a=1&b=2 -o out.txt")
    _allowed(p, "echo a & b")
    _allowed(p, 'grep "x & y" dosya.txt')


def test_remove_item_recurse_blanket_block():
    # documented strictness: ANY Remove-Item -Recurse is blocked outright
    p = ShellPolicy()
    _blocked(p, "Remove-Item -Recurse -Force C:\\Windows\\System32")
    _blocked(p, "Remove-Item -Recurse .\\temp_klasor")


def test_unix_vectors_unchanged():
    # the Windows hardening must not disturb the Unix profile
    p = ShellPolicy()
    _blocked(p, "rm -rf /")
    _blocked(p, "rm -rf /etc /usr")
    _blocked(p, ":(){ :|:& };:")
    _blocked(p, "echo x > /etc/passwd")
    _allowed(p, "rm /tmp/normal.txt")
    _allowed(p, "rm -rf /tmp/ultron_test_dir")


def test_api_health_endpoint_exists():
    # Phase-2 spec lists /api/health; the app is built inside main(), so
    # verify handler + route registration textually (same wiring pytest
    # cannot reach without booting the server).
    import server
    import inspect
    assert callable(getattr(server, "api_health", None))
    body = inspect.getsource(server.main)
    assert 'add_get("/api/health", api_health)' in body
