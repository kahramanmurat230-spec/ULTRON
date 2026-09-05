import subprocess
import sys

def run_diagnostic(command):
    """Run only read-only diagnostics. Never use for file deletion or system changes."""
    if sys.platform != "win32":
        raise RuntimeError("Windows tanılama aracı Windows'ta çalışır.")
    allowed = {
        "whoami",
        "hostname",
        "ver",
        "ipconfig",
        "tasklist",
    }
    cmd = command.strip().lower()
    if cmd not in allowed:
        raise PermissionError(f"Bu tanılama komutuna izin verilmiyor: {command}")
    p = subprocess.run(
        ["cmd", "/c", cmd],
        capture_output=True,
        text=True,
        timeout=20,
        creationflags=subprocess.CREATE_NO_WINDOW,
    )
    return (p.stdout or p.stderr).strip()[:12000]
