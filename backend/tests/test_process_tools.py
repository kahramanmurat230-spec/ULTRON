"""PHASE 8: computer use — process inspection (real psutil) + kill safety."""
import os
import subprocess
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from app.tools.system_tools import (  # noqa: E402
    process_info, process_kill, process_list, system_settings_view,
)


def test_process_list_real():
    res = process_list(limit=10)
    assert res["count"] >= 5 and len(res["processes"]) <= 10
    names = [p["name"] for p in res["processes"]]
    assert any(names)
    # kendi sürecimizi bulalım (name filtresi gerçek çalışıyor)
    me = process_list(name="python", limit=100)
    assert me["count"] >= 1


def test_process_info_real():
    info = process_info(os.getpid())
    assert info["pid"] == os.getpid() and info["name"]
    assert isinstance(info["cmdline"], list)


def test_process_kill_refuses_self():
    import pytest
    with pytest.raises(PermissionError):
        process_kill(os.getpid())  # asla intihar etmez


def test_process_kill_real_and_confirmed():
    # gerçek süreci başlat → öldür → doğrula (REQUEST→RISK→APPROVAL→EXECUTE→VERIFY)
    p = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
    time.sleep(0.5)
    assert p.poll() is None
    res = process_kill(p.pid)
    assert res["killed"] == p.pid and res["name"]
    time.sleep(0.2)
    assert p.poll() is not None


def test_system_settings_view_honest():
    out = system_settings_view()
    assert "os" in out and "startup_items" in out  # Windows değilse dürüst not
