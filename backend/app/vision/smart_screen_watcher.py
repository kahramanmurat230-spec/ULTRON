"""Smart Screen Watcher — adaptive, privacy-first visual awareness.

* Adaptive loop: 5 s default, 15 s when the workspace is IDLE.
* Zero-cost diff: downsampled grayscale mean-abs diff; <3% change => NO OCR/LLaVA.
* Focused-app filtering: deep analysis only for target windows.
* Blacklist: windows titled Password/Banka/Login/Private are NEVER captured or
  retained (architectural block, frame discarded, counter only).
* Frames live in memory only — never written to disk or sent out.
"""
import time

DEFAULT_TARGETS = ("code", "vscode", "terminal", "pycharm", "chrome", "powershell", "cmd")
DEFAULT_BLACKLIST = ("password", "banka", "bank", "login", "private", "şifre", "cüzdan", "wallet")


def get_frame_pil(size=(64, 36)):
    """64x36 grayscale bytes via PIL; None when headless/no display."""
    try:
        from PIL import ImageGrab
        img = ImageGrab.grab()
        return img.convert("L").resize(size).tobytes()
    except Exception:
        return None


def diff_ratio(a: bytes, b: bytes) -> float:
    if not a or not b or len(a) != len(b):
        return 1.0
    n = len(a)
    acc = 0
    for i in range(n):
        acc += abs(a[i] - b[i])
    return (acc / n) / 255.0


class SmartScreenWatcher:
    def __init__(self, capture_fn=get_frame_pil, title_fn=None, enabled=False,
                 base_interval=5.0, idle_interval=15.0, threshold=0.03,
                 targets=DEFAULT_TARGETS, blacklist=DEFAULT_BLACKLIST,
                 on_change=None, now=None):
        self.capture_fn = capture_fn
        self.title_fn = title_fn
        self.enabled = bool(enabled)
        self.base_interval = base_interval
        self.idle_interval = idle_interval
        self.threshold = threshold
        self.targets = tuple(t.lower() for t in targets)
        self.blacklist = tuple(b.lower() for b in blacklist)
        self.on_change = on_change
        self.now = now or time.time
        self.last_frame = None
        self.last_diff = 0.0
        self.last_title = None
        self.privacy_blocks = 0
        self.analyses = 0
        self._stop = False

    def blacklisted(self, title: str) -> bool:
        t = (title or "").lower()
        return any(b in t for b in self.blacklist)

    def is_target(self, title: str) -> bool:
        t = (title or "").lower()
        return any(k in t for k in self.targets)

    def interval(self, idle: bool) -> float:
        return self.idle_interval if idle else self.base_interval

    def tick(self, frame=None, title=None, idle=False) -> dict:
        """One observation step (thread loop or tests call this directly)."""
        if not self.enabled:
            return {"status": "paused", "analyzed": False}
        title = title if title is not None else (self.title_fn() if self.title_fn else None)
        self.last_title = title
        if title and self.blacklisted(title):
            self.privacy_blocks += 1
            self.last_frame = None  # architectural: never retain blacklisted pixels
            return {"status": "privacy_blocked", "analyzed": False}
        if frame is None and self.capture_fn:
            frame = self.capture_fn()
        if frame is None:
            return {"status": "no_display", "analyzed": False}  # headless: graceful wait
        out = {"status": "ok", "analyzed": False, "diff": self.last_diff}
        if self.last_frame is not None:
            d = diff_ratio(self.last_frame, frame)
            self.last_diff = round(d, 4)
            out["diff"] = self.last_diff
            if d >= self.threshold and (title is None or self.is_target(title)):
                self.analyses += 1
                out.update({"status": "change", "analyzed": True})
                if self.on_change:
                    self.on_change(frame, title, d)
        self.last_frame = frame
        return out

    def loop(self, idle_fn=None) -> None:
        while not self._stop:
            idle = bool(idle_fn()) if idle_fn else False
            self.tick(idle=idle)
            time.sleep(self.interval(idle))

    def stop(self) -> None:
        self._stop = True
