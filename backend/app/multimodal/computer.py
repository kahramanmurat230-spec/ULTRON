"""WAVE 4 — Computer Use runtime (§12, §27).

Her action yaşam döngüsü: PLAN → RISK → PERMISSION → EXECUTE → OBSERVE →
VERIFY. Risk değerlendirme MEVCUT security core'dan (app/security/risk.py
RiskEngine — DEĞİŞTİRİLMEDEN) alınır; §27'deki yüksek riskli işlem
sınıfı (delete/format/shutdown/credential/security setting/system dir/
external upload/payment/message send) CRITICAL kabul edilir ve insan
onayı OLMADAN yürütülemez (bypass YASAK).

Yanlış pencere koruması: action bir pencere hedefliyorsa execute ÖNCESİ
aktif pencere doğrulanır; eşleşmezse WrongWindowError ile RED.

Gerçek cihaz katmanı pyautogui'dir; kurulu değilse dürüst unavailable —
sahte tıklama YOK.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field

from app.security.risk import RiskEngine  # security core — korunur

# ---------------------------------------------------------------- lifecycle
PLANNED = "PLANNED"
RISK_CHECKED = "RISK_CHECKED"
APPROVED = "APPROVED"
DENIED = "DENIED"
EXECUTED = "EXECUTED"
OBSERVED = "OBSERVED"
VERIFIED = "VERIFIED"
FAILED = "FAILED"

COMPUTER_LIFECYCLE = {
    PLANNED: (RISK_CHECKED, DENIED),
    RISK_CHECKED: (APPROVED, DENIED),
    APPROVED: (EXECUTED, DENIED, FAILED),
    EXECUTED: (OBSERVED, FAILED),
    OBSERVED: (VERIFIED, FAILED),
    VERIFIED: (),
    FAILED: (),
    DENIED: (),
}

# §27 — insan onayı zorunlu yüksek riskli işlem sınıfı
CRITICAL_ACTIONS = {
    "delete_file", "delete_dir", "format", "shutdown", "reboot",
    "credential_change", "security_setting", "system_dir_write",
    "external_upload", "payment", "purchase", "message_send",
    "account_deletion", "credential_submit",
}
MEDIUM_ACTIONS = {
    "click", "double_click", "right_click", "type_text", "press",
    "hotkey", "scroll", "focus_window", "clipboard_write",
    "launch_app", "move",
}
SAFE_ACTIONS = {"observe", "screenshot", "clipboard_read", "read_window",
                "find_window", "list_windows"}


class WrongWindowError(RuntimeError):
    """Action yanlış pencerede uygulanmak üzere — RED."""


class ComputerDenied(PermissionError):
    """Risk/onay kapısı action'ı reddetti."""


class DeviceUnavailable(RuntimeError):
    """Gerçek giriş cihazı katmanı yok (headless) — sahte eylem YOK."""


@dataclass
class ComputerAction:
    kind: str                          # click/type_text/delete_file/...
    target: dict = field(default_factory=dict)   # {element|x,y|path|window}
    params: dict = field(default_factory=dict)   # {text, keys, ...}
    expected: dict = field(default_factory=dict) # verify için beklenen durum
    window: str | None = None          # bağlı pencere (wrong-window guard)
    state: str = PLANNED
    result: dict = field(default_factory=dict)
    observation: dict = field(default_factory=dict)
    error: str | None = None
    risk: dict = field(default_factory=dict)
    started_at: float | None = None
    finished_at: float | None = None

    # ---------------------------------------------------------------- risk
    def risk_level(self) -> str:
        if self.kind in CRITICAL_ACTIONS:
            return "CRITICAL"
        if self.kind in MEDIUM_ACTIONS:
            return "MEDIUM"
        if self.kind in SAFE_ACTIONS:
            return "SAFE"
        return "UNKNOWN"               # bilinmeyen → yürütülmez


class PyAutoGUIExecutor:
    """Gerçek cihaz katmanı (pyautogui + pygetwindow) — headless'ta dürüst."""

    NAME = "pyautogui"

    def __init__(self):
        self.error = None
        try:
            import pyautogui  # noqa: F401
        except (Exception, SystemExit) as exc:  # noqa: BLE001
            # Some optional pyautogui deps (e.g. mouseinfo) call sys.exit()
            # instead of raising when a required system lib (tkinter) is
            # missing, even though a display is present. Treat that as an
            # honest "unavailable", never as a process-crashing failure.
            self.error = f"pyautogui yok ({exc})"
            self.available = False
        else:
            self.available = True

    def status(self) -> dict:
        return {"executor": self.NAME, "available": self.available,
                "error": self.error}

    def _gui(self):
        if not self.available:
            raise DeviceUnavailable(self.error)
        import pyautogui
        return pyautogui

    # -- gerçek eylemler (execute() sözleşmesine uyar) --
    def execute(self, action: ComputerAction) -> dict:
        g = self._gui()
        k, t, p = action.kind, action.target, action.params
        if k == "click":
            el = t.get("element") or {}
            x, y = (t.get("x", el.get("center", (0, 0))[0]),
                    t.get("y", el.get("center", (0, 0))[1]))
            g.click(int(x), int(y))
            return {"clicked": [int(x), int(y)]}
        if k == "type_text":
            g.write(str(p.get("text", "")))
            return {"typed": len(str(p.get("text", "")))}
        if k == "press":
            g.press(str(p.get("key")))
            return {"pressed": p.get("key")}
        if k == "hotkey":
            g.hotkey(*[str(kk) for kk in p.get("keys", [])])
            return {"hotkey": p.get("keys")}
        if k == "scroll":
            g.scroll(int(p.get("amount", 0)))
            return {"scrolled": p.get("amount")}
        if k == "focus_window":
            return self.focus_window(str(t.get("window", "")))
        raise DeviceUnavailable(f"gerçek eylem yok: {k}")

    def active_window_title(self) -> str | None:
        if not self.available:
            return None
        try:
            import pygetwindow as gw
            w = gw.getActiveWindow()
            return w.title if w else None
        except Exception:  # noqa: BLE001
            return None

    def focus_window(self, title: str) -> dict:
        if not self.available:
            raise DeviceUnavailable(self.error)
        import pygetwindow as gw
        ws = gw.getWindowsWithTitle(title)
        if not ws:
            return {"focused": False}
        w = ws[0]
        if getattr(w, "isMinimized", False):
            w.restore()
        w.activate()
        return {"focused": True, "title": w.title}


class ComputerUseRuntime:
    """Lifecycle + risk + onay + gözlem + doğrulama orkestrasyonu."""

    def __init__(self, executor=None, *, risk_engine: RiskEngine | None = None,
                 approve_fn=None, observe_fn=None, verify_fn=None,
                 now=None):
        self.executor = executor or PyAutoGUIExecutor()
        self.risk = risk_engine or RiskEngine({
            k: True for k in MEDIUM_ACTIONS | CRITICAL_ACTIONS})
        self.approve = approve_fn          # fn(action) -> bool (insan onayı)
        self.observe = observe_fn          # fn(action, result) -> dict
        self.verify = verify_fn            # fn(action, obs) -> (bool, str)
        self._now = now or time.monotonic
        self.audit: list[dict] = []

    # ------------------------------------------------------------ status
    def status(self) -> dict:
        return {"executor": self.executor.status(),
                "available": getattr(self.executor, "available", False)}

    def _audit(self, ev: str, action: ComputerAction):
        self.audit.append({"ts": time.time(), "event": ev,
                           "kind": action.kind, "state": action.state,
                           "risk": action.risk.get("level"),
                           "target_keys": sorted(action.target)[:4],
                           "window": action.window})

    def _advance(self, action: ComputerAction, new_state: str):
        allowed = COMPUTER_LIFECYCLE[action.state]
        if new_state not in allowed:
            raise RuntimeError(
                f"lifecycle violation: {action.state} -> {new_state}")
        action.state = new_state

    # ------------------------------------------------------------ akış
    def run(self, action: ComputerAction, *, approved: bool = False) -> dict:
        """Tam lifecycle: PLAN→RISK→PERMISSION→EXECUTE→OBSERVE→VERIFY."""
        # 1) RISK — mevcut security core'dan
        level = action.risk_level()
        action.risk = {"level": level,
                       "requires_approval": level in ("MEDIUM", "HIGH",
                                                      "CRITICAL", "UNKNOWN"),
                       "critical": level == "CRITICAL"}
        self._advance(action, RISK_CHECKED)
        self._audit("risk_checked", action)
        if level == "UNKNOWN":
            action.error = f"bilinmeyen eylem türü: {action.kind}"
            self._advance(action, DENIED)
            raise ComputerDenied(action.error)
        # CRITICAL arg-escalation: credential metni/sistem yolu
        if action.kind == "type_text" and action.params.get("secret"):
            action.risk = {"level": "CRITICAL", "requires_approval": True,
                           "critical": True}
        # 2) PERMISSION — insan onayı (bypass YASAK)
        needs = action.risk["requires_approval"]
        human_ok = approved or (self.approve is not None
                                and self.approve(action))
        if needs and not human_ok:
            action.error = (f"onay gerekli: {action.kind} "
                            f"(risk={action.risk['level']})")
            self._advance(action, DENIED)
            self._audit("denied", action)
            raise ComputerDenied(action.error)
        self._advance(action, APPROVED)
        # 3) EXECUTE — wrong-window guard + gerçek eylem
        self._check_window(action)
        action.started_at = self._now()
        try:
            action.result = self.executor.execute(action)
        except DeviceUnavailable:
            action.error = "cihaz katmanı unavailable"
            self._advance(action, FAILED)
            self._audit("failed", action)
            raise
        except Exception as exc:  # noqa: BLE001
            action.error = str(exc)[:200]
            self._advance(action, FAILED)
            self._audit("failed", action)
            return {"state": FAILED, "error": action.error}
        action.finished_at = self._now()
        self._advance(action, EXECUTED)
        self._audit("executed", action)
        # 4) OBSERVE
        if self.observe is not None:
            action.observation = self.observe(action, action.result)
            self._advance(action, OBSERVED)
        # 5) VERIFY
        if self.verify is not None and action.state == OBSERVED:
            ok, detail = self.verify(action, action.observation)
            if ok:
                self._advance(action, VERIFIED)
                self._audit("verified", action)
            else:
                action.error = detail
                self._advance(action, FAILED)
                self._audit("verify_failed", action)
                return {"state": FAILED, "error": detail,
                        "observation": action.observation}
        return {"state": action.state, "result": action.result,
                "observation": action.observation, "risk": action.risk}

    # ------------------------------------------------------------ guards
    def _check_window(self, action: ComputerAction) -> None:
        """Yanlış pencere koruması (§24)."""
        if not action.window:
            return
        title = self.executor.active_window_title()
        if title is None:
            # cihaz katmanı başlık veremiyorsa kör devam EDİLMEZ
            raise WrongWindowError(
                "aktif pencere doğrulanamıyor (cihaz katmanı yok)")
        if action.window.lower() not in title.lower():
            raise WrongWindowError(
                f"yanlış pencere: beklenen '{action.window}', aktif "
                f"'{title[:60]}' — action RED")
