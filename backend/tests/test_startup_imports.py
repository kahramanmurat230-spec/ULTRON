"""Regression tests for real backend startup import ordering.

These guard against two production-breaking defects found during the
production/runtime acceptance audit:

1. A circular import (bridge -> test_runner -> runtime_degradation -> bridge)
   that crashed ``import server`` (and therefore ``python server.py``)
   whenever ``bridge`` was the first of the two modules to be imported.
   ``server.py`` imports ``bridge`` before ``test_runner``, so this crashed
   on every real startup, not just in artificial import orders.
2. A Windows-only ``UnicodeEncodeError`` crash during ``on_startup`` when the
   doctor summary (which may contain Turkish/unicode text) was printed to a
   legacy-codepage (e.g. cp1252) console.

Both must keep working via a real subprocess import (not just a mocked
in-process import) because import side effects and module-level execution
order are exactly what broke in production.
"""
import subprocess
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parent.parent


def _run_python(code: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-c", code],
        cwd=str(BACKEND),
        capture_output=True,
        text=True,
        timeout=60,
    )


def test_bridge_first_import_does_not_crash():
    """Importing bridge.py directly (before test_runner) must not raise.

    This is the exact real-world ordering used by server.py, which does
    ``from bridge import UltronBridge`` before ``import test_runner``.
    """
    result = _run_python("import bridge\nprint('OK')")
    assert result.returncode == 0, result.stderr
    assert "OK" in result.stdout
    assert "circular import" not in result.stderr


def test_server_module_imports_cleanly():
    """The real backend entrypoint must import without raising at all."""
    result = _run_python("import server\nprint('OK')")
    assert result.returncode == 0, result.stderr
    assert "OK" in result.stdout


def test_test_runner_does_not_install_guard_at_import_time():
    """install_bridge_guard must be deferred to run_all(), not import time.

    Calling it eagerly at module import time is what caused the circular
    import: it reaches back into ``bridge`` before that module has finished
    defining ``UltronBridge``.
    """
    source = (BACKEND / "test_runner.py").read_text(encoding="utf-8")
    # Only the deferred call inside run_all() should remain; there must not
    # be a bare top-level `install_bridge_guard()` call.
    lines = [ln for ln in source.splitlines() if ln.strip() == "install_bridge_guard()"]
    assert len(lines) == 1, "expected exactly one deferred install_bridge_guard() call"


def test_stdout_stderr_reconfigured_for_unicode_safety():
    """server.py must make stdio unicode-safe to survive legacy consoles."""
    source = (BACKEND / "server.py").read_text(encoding="utf-8")
    assert "reconfigure(encoding=\"utf-8\"" in source
