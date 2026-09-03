"""WAVE 4 / Computer use: lifecycle, risk/onay, wrong-window, kurtarma,
cihaz dürüst unavailable."""
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from app.multimodal.computer import (  # noqa: E402
    APPROVED, DENIED, EXECUTED, FAILED, OBSERVED, PLANNED, RISK_CHECKED,
    VERIFIED, ComputerAction, ComputerDenied, ComputerUseRuntime,
    DeviceUnavailable, PyAutoGUIExecutor, WrongWindowError,
)


class ScriptExecutor:
    """DI seam — gerçek PyAutoGUIExecutor yerine kayıt tutan test kancası."""
    def __init__(self, results=None, fail_kinds=(), windows=None, available=True):
        self.calls = []
        self.results = results or {}
        self.fail_kinds = set(fail_kinds)
        self.windows = windows
        self.available = available
        self.error = None if available else "test cihazı yok"

    def status(self):
        return {"executor": "script", "available": self.available,
                "error": self.error}

    def execute(self, action):
        if not self.available:
            raise DeviceUnavailable("script executor unavailable")
        self.calls.append((action.kind, tuple(sorted(action.target))))
        if action.kind in self.fail_kinds:
            raise RuntimeError(f"eylem hatası: {action.kind}")
        return self.results.get(action.kind, {"ok": True})

    def active_window_title(self):
        return self.windows


def test_action_lifecycle_full_with_verify():
    obs_log = []
    def observe_fn(action, result):
        obs_log.append(1)
        return {"window_title": "Belge - Not Defteri"}
    def verify_fn(action, obs):
        ok = action.expected.get("window_title") == obs.get("window_title")
        return ok, "pencere başlığı eşleşti" if ok else "beklenen durum yok"
    rt = ComputerUseRuntime(ScriptExecutor(), observe_fn=observe_fn, verify_fn=verify_fn)
    a = ComputerAction("click", target={"x": 10, "y": 20}, expected={"window_title": "Belge - Not Defteri"})
    out = rt.run(a, approved=True)
    assert out["state"] == VERIFIED
    assert a.state == VERIFIED
    assert obs_log and len(rt.audit) >= 3


def test_lifecycle_order_enforced():
    rt = ComputerUseRuntime(ScriptExecutor())
    a = ComputerAction("click", target={"x": 1, "y": 1})
    a.state = PLANNED
    with pytest.raises(RuntimeError, match="lifecycle"):
        rt._advance(a, EXECUTED)


def test_click_and_type_reach_executor():
    ex = ScriptExecutor(results={"type_text": {"typed": 5}})
    rt = ComputerUseRuntime(ex)
    rt.run(ComputerAction("click", target={"x": 5, "y": 6}), approved=True)
    rt.run(ComputerAction("type_text", params={"text": "merhaba"}), approved=True)
    assert [c[0] for c in ex.calls] == ["click", "type_text"]


def test_critical_action_requires_human_approval():
    rt = ComputerUseRuntime(ScriptExecutor())
    a = ComputerAction("delete_file", target={"path": "/tmp/x.txt"})
    with pytest.raises(ComputerDenied, match="onay gerekli"):
        rt.run(a, approved=False)
    assert a.state == DENIED and rt.executor.calls == []


def test_critical_action_approved_executes():
    rt = ComputerUseRuntime(ScriptExecutor())
    a = ComputerAction("shutdown")
    out = rt.run(a, approved=True)
    assert out["state"] == EXECUTED and a.risk["critical"] is True


def test_secret_typing_escalates_to_critical():
    rt = ComputerUseRuntime(ScriptExecutor())
    a = ComputerAction("type_text", params={"text": "şifre123", "secret": True})
    with pytest.raises(ComputerDenied):
        rt.run(a, approved=False)


def test_unknown_action_kind_denied():
    rt = ComputerUseRuntime(ScriptExecutor())
    with pytest.raises(ComputerDenied, match="bilinmeyen"):
        rt.run(ComputerAction("format_c_drive"))


def test_approval_callback_path():
    asked = []
    def approve_fn(action):
        asked.append(action.kind)
        return True
    rt = ComputerUseRuntime(ScriptExecutor(), approve_fn=approve_fn)
    out = rt.run(ComputerAction("click", target={"x": 1, "y": 1}))
    assert out["state"] == EXECUTED and asked == ["click"]


def test_wrong_window_rejected_before_execute():
    ex = ScriptExecutor(windows="Oyun Penceresi")
    rt = ComputerUseRuntime(ex)
    a = ComputerAction("click", target={"x": 1, "y": 1}, window="Vergi Dairesi Formu")
    with pytest.raises(WrongWindowError, match="yanlış pencere"):
        rt.run(a, approved=True)
    assert ex.calls == []


def test_window_unverifiable_rejected():
    ex = ScriptExecutor(windows=None)
    rt = ComputerUseRuntime(ex)
    a = ComputerAction("click", target={"x": 1, "y": 1}, window="Hedef")
    with pytest.raises(WrongWindowError):
        rt.run(a, approved=True)


def test_right_window_executes():
    ex = ScriptExecutor(windows="Vergi Dairesi Formu — Chrome")
    rt = ComputerUseRuntime(ex)
    out = rt.run(ComputerAction("click", target={"x": 1, "y": 1}, window="Vergi Dairesi"), approved=True)
    assert out["state"] == EXECUTED


def test_action_failure_reported_not_hidden():
    ex = ScriptExecutor(fail_kinds={"click"})
    rt = ComputerUseRuntime(ex)
    out = rt.run(ComputerAction("click", target={"x": 1, "y": 1}), approved=True)
    assert out["state"] == FAILED and "eylem hatası" in out["error"]


def test_verify_failure_leads_to_failed_with_detail():
    rt = ComputerUseRuntime(ScriptExecutor(), observe_fn=lambda a, r: {"window_title": "başka"}, verify_fn=lambda a, o: (False, "buton durumu değişmedi"))
    a = ComputerAction("click", target={"x": 1, "y": 1}, expected={"window_title": "hedef"})
    out = rt.run(a, approved=True)
    assert out["state"] == FAILED and out["error"] == "buton durumu değişmedi"


def test_retry_limited_not_infinite():
    attempts = {"n": 0}
    class Flaky(ScriptExecutor):
        def execute(self, action):
            attempts["n"] += 1
            raise RuntimeError("arızalı")
    rt = ComputerUseRuntime(Flaky())
    for _ in range(3):
        try:
            rt.run(ComputerAction("click", target={"x": 1, "y": 1}), approved=True)
        except Exception:
            pass
    assert attempts["n"] == 3


def test_pyautogui_honest_unavailable_headless():
    ex = PyAutoGUIExecutor()
    st = ex.status()
    if st["available"]:
        # A real display is a valid capability state; do not mark it skipped.
        assert st["available"] is True
        return
    assert st["available"] is False and "pyautogui" in st["error"]
    rt = ComputerUseRuntime(ex)
    a = ComputerAction("click", target={"x": 1, "y": 1})
    with pytest.raises(DeviceUnavailable):
        rt.run(a, approved=True)


def test_runtime_status_honest():
    rt = ComputerUseRuntime(ScriptExecutor(available=False))
    assert rt.status()["available"] is False
